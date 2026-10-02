"""Agent5 装载执行：自 N0 递增柜数至 can_fit；skjolber → python-laff-3d → 1D。"""

from __future__ import annotations

from typing import Any, Dict, List

from packing_assistant.adapters import boxes_to_internal
from packing_assistant.skjolber_client import (
    health_check as skjolber_health,
    is_skjolber_configured,
    pack_via_skjolber,
)
from packing_assistant.state import PackingState
from packing_assistant.tools.bin3d import pack_boxes_api
from packing_assistant.tools.consolidation import run_consolidation


def agent_loader(state: PackingState) -> Dict[str, Any]:
    boxes = list(state.get("boxes") or [])
    plan = state.get("plan") or {}
    ctype = plan.get("container_type") or state.get("container_type") or "40HQ"
    from packing_assistant.tools.cargo_conservation import physical_split_issues

    physical_issues = physical_split_issues(state.get("materials") or [], boxes)
    if physical_issues:
        return {
            "container_plan": {"container_type": ctype, "can_fit": False, "containers_used": 0,
                               "layout": [], "unpacked_box_ids": [box.get("box_id") for box in boxes],
                               "engine": "blocked_physical_split", "message": physical_issues[0]["ask"]},
            "needs_human": physical_issues, "materials_incomplete": True, "ship_ok": False,
            "errors": ["loader_blocked: physical_split_not_authorized"],
            "agent_meta": {"node": "loader", "tools_used": ["loader.block_physical_split"],
                           "artifacts": {"can_fit": False, "layout": 0, "needs_human": len(physical_issues)}},
        }
    priority = plan.get("priority_order") or []
    booking = plan.get("booking") or state.get("booking") or {}
    packing_opts = dict(state.get("packing_options") or {})
    # 大票 / 策略要求：统一 40HQ（除非用户硬锁 20/40GP/45）
    if packing_opts.get("prefer_40hq_multi") or packing_opts.get("force_40hq"):
        if str(ctype).upper() in ("40GP", "40HQ", ""):
            ctype = "40HQ"
    soft_b = int(
        packing_opts.get("container_budget_soft")
        or packing_opts.get("soft_budget")
        or plan.get("container_budget_soft")
        or 0
    )
    if soft_b > 0:
        packing_opts["container_budget_soft"] = soft_b
        packing_opts.setdefault("soft_budget_mid50", 0.60)
    # P0/P1 3D 堆码默认：可叠优先叠高 + 绑扎间隙 + 支撑比
    packing_opts.setdefault("prefer_stack", True)
    packing_opts.setdefault(
        "clearance_mm",
        packing_opts.get("lashing_gap_mm", packing_opts.get("gap_mm", 30)),
    )
    packing_opts.setdefault("support_ratio_min", 0.55)
    packing_opts.setdefault("max_stack_layers", 3)
    packing_opts.setdefault("prefer_bottom_weight_kg", 2000)
    packing_opts.setdefault("multi_start", True)
    packing_opts.setdefault("cog_aware", True)
    # CTU 60/50：默认强制中段质量再平衡（消 mid50 block）
    packing_opts.setdefault("cog_rebalance", True)
    packing_opts.setdefault("r4_repair", True)
    packing_opts.setdefault("r4_target_mid50", 0.60)
    packing_opts.setdefault("r0_r1", True)
    packing_opts.setdefault("r2_slab", True)
    packing_opts.setdefault("lns_worst", True)
    packing_opts.setdefault("lateral_repair", True)
    packing_opts.setdefault("corner_support", True)
    packing_opts.setdefault("export_strict", False)  # 出运时 state 可设 True
    # 一箱一柜：5 箱 → 5 集装箱（不拼柜优化）
    if _want_one_box_per_container(state, packing_opts, boxes):
        container_plan = _plan_one_box_per_container(boxes, ctype, booking)
        n0 = max(1, len(boxes))
        container_plan = _enrich_plan_metrics(
            container_plan,
            boxes=boxes,
            booking=booking,
            container_type=str(ctype),
            n0=n0,
        )
        used = int(container_plan.get("containers_used") or n0)
        tools_used = ["loader.one_box_per_container", f"engine:{container_plan.get('engine')}"]
        return {
            "container_plan": container_plan,
            "booking": container_plan.get("booking") or booking,
            "agent_meta": {
                "node": "loader",
                "capability": ["使用工具", "采取行动"],
                "tools_used": tools_used,
                "artifacts": {
                    "can_fit": True,
                    "containers_used": used,
                    "n0": n0,
                    "mode": "one_box_per_container",
                    "booking_volume_utilization": container_plan.get(
                        "booking_volume_utilization"
                    ),
                    "outer_space_utilization": container_plan.get(
                        "outer_space_utilization"
                    ),
                },
            },
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        f"【行动·装载】模式=一箱一柜 boxes={len(boxes)} → 集装箱={used} "
                        f"can_fit=True engine={container_plan.get('engine')} "
                        f"｜tools={','.join(tools_used)}"
                    ),
                }
            ],
        }

    n0 = int(
        plan.get("n0")
        or booking.get("n0")
        or booking.get("containers_needed")
        or 1
    )
    n0 = max(1, n0)
    # 搜索上限：plan 已带 headroom；state.max_containers 仅作用户封顶（非目标柜数）
    # 硬锁柜（lock/budget/meeting_cap）：严格 cap，可 can_fit=False，禁止为 N0 擅自加柜
    plan_cap = int(plan.get("max_containers") or 0)
    user_cap = int(state.get("max_containers") or 0)
    hard_lock = bool(
        packing_opts.get("lock_max_containers")
        or packing_opts.get("meeting_cap")
        or packing_opts.get("fixed_container_budget")
        or packing_opts.get("container_budget")
    )
    budget_opt = int(packing_opts.get("container_budget") or 0)
    n_max = plan_cap if plan_cap > 0 else min(40, n0 + 8)
    if hard_lock and (user_cap > 0 or budget_opt > 0 or plan_cap > 0):
        cap = user_cap or budget_opt or plan_cap
        n_max = max(1, min(int(cap), 40))
        n0 = min(n0, n_max)  # 搜索从 min(N0,cap) 起，不突破 cap
    elif user_cap > 0:
        # 软封顶：不得低于 N0 时仍至少试到 N0；cap>=N0 则 cap 为上界
        n_max = max(n0, min(user_cap, 40)) if user_cap >= n0 else max(n0, n_max)
        n_max = max(n0, min(n_max, 40))
    else:
        n_max = max(n0, min(n_max, 40))
    # 若 cap 意外等于 n0 且无显式「只要一柜」意图，仍留 headroom（replan 前的安全垫）
    if n_max == n0 and user_cap <= 0 and not hard_lock:
        n_max = min(40, n0 + 8)

    notes: List[str] = []
    # 大票多柜：Planner 的「超长优先」序常伤 mid50 / 抬柜数；默认丢弃，交给 bin3d 重货+CoG
    drop_prio = packing_opts.get("drop_load_priority")
    if drop_prio is None:
        drop_prio = (
            len(boxes) >= 40
            or n0 >= 8
            or bool(packing_opts.get("prefer_40hq_multi") or packing_opts.get("force_40hq"))
        )
    if drop_prio and priority:
        notes.append(
            f"big_ticket drop_load_priority n_boxes={len(boxes)} n0={n0} "
            f"(was {len(priority)} ids) → CoG/multi_start 默认序"
        )
        priority = []
        packing_opts["drop_load_priority"] = True

    if priority:
        order = {bid: i for i, bid in enumerate(priority)}
        boxes = sorted(boxes, key=lambda b: order.get(b.get("box_id"), 999))

    container_plan: Dict[str, Any] | None = None
    rid = str(state.get("run_id") or state.get("packing_plan_id") or "")

    # 0) skjolber 优先（需 SKJOLBER_URL + 服务健康；无管理员用户目录 JDK 可起）
    skjolber_ok = False
    if is_skjolber_configured():
        try:
            skjolber_ok = bool(skjolber_health(timeout=0.5).get("ok"))
        except Exception:
            skjolber_ok = False
    if skjolber_ok:
        try:
            for mc in range(n0, n_max + 1):
                trial = pack_via_skjolber(
                    boxes,
                    {
                        **plan,
                        "container_type": ctype,
                        "max_containers": mc,
                        "priority_order": priority,
                    },
                    request_id=rid,
                )
                container_plan = trial
                notes.append(
                    f"skjolber try_N={mc} can_fit={trial.get('can_fit')} "
                    f"engine={trial.get('engine')}"
                )
                if trial.get("can_fit"):
                    break
            if container_plan is not None:
                notes.append(container_plan.get("engine") or "skjolber")
        except Exception as e:
            notes.append(f"skjolber失败回退: {e}")
            container_plan = None

    # 1) 自主定柜：N0 起递增 Python 3D（主路径 / skjolber 失败回退）
    if container_plan is None:
        try:
            from packing_assistant.tools.booking import pack_with_auto_containers

            container_plan = pack_with_auto_containers(
                boxes,
                container_type=str(ctype),
                n0=n0,
                n_max=n_max,
                priority_order=priority or None,
                fill_ratio=0.82,
                packing_options=packing_opts or None,
            )
            # 与 booking 对齐 N0*（pack 可能刷新 geom 组件）
            if container_plan.get("n0") is not None:
                n0 = int(container_plan.get("n0") or n0)
            notes.append(
                f"auto_N0*={n0}->used={container_plan.get('containers_used')} "
                f"gap={container_plan.get('n0_gap')} "
                f"merge={container_plan.get('merged_ok')} "
                f"booking_vol_util={container_plan.get('booking_volume_utilization')}"
            )
            notes.append(container_plan.get("engine") or "python-laff-3d")
            # Loader 二次 soft 压柜：priority 序下 pack_with_auto 可能漏压；在 light..light+3 再扫
            container_plan = _soft_budget_post_densify(
                container_plan,
                boxes=boxes,
                container_type=str(ctype),
                priority_order=priority or None,
                packing_opts=packing_opts,
                notes=notes,
            )
        except Exception as e:
            notes.append(f"auto_booking失败: {e}")
            container_plan = None

    # 2) 本机 3D 兜底：与 auto 相同自 N0 递增至 can_fit（禁止只试 N0）
    if container_plan is None:
        try:
            last_fb: Dict[str, Any] | None = None
            for mc in range(n0, n_max + 1):
                trial = pack_boxes_api(
                    boxes,
                    container_type=ctype,
                    max_containers=mc,
                    priority_order=priority or None,
                    packing_options=packing_opts or None,
                )
                last_fb = trial
                notes.append(
                    f"fallback3d try_N={mc} can_fit={trial.get('can_fit')} "
                    f"engine={trial.get('engine')}"
                )
                if trial.get("can_fit"):
                    break
            container_plan = last_fb
            if container_plan is None:
                raise RuntimeError("pack_boxes_api returned empty")
            notes.append(container_plan.get("engine") or "python-laff-3d")
        except Exception as e:
            notes.append(f"python3d失败: {e}")
            container_plan = _local_1d(boxes, ctype)
            notes.append("local-1d-fallback")

    # 全引擎统一补齐订柜/外廓指标（禁止评估侧用 outer 顶替 booking）
    container_plan = _enrich_plan_metrics(
        container_plan or {},
        boxes=boxes,
        booking=booking,
        container_type=str(ctype),
        n0=n0,
    )
    booking_out = container_plan.get("booking") or booking
    # 保证 multi 解释字段进 plan
    if not container_plan.get("multi_container_explain"):
        n0x = int(container_plan.get("n0") or n0)
        ux = int(container_plan.get("containers_used") or 0)
        container_plan["multi_container_explain"] = (
            f"N0*={n0x} → 3D实装={ux}（gap={ux - n0x}）"
        )
        container_plan.setdefault("n0_gap", ux - n0x)

    # 指标拆分文案
    outer_u = float(
        container_plan.get("outer_space_utilization")
        or container_plan.get("space_utilization")
        or 0
    )
    book_u = float(container_plan.get("booking_volume_utilization") or 0)
    n0_used = container_plan.get("n0") or n0
    eng = str(container_plan.get("engine") or "python-laff-3d")
    used = int(container_plan.get("containers_used") or n0_used or 0)
    # 显式重试轨迹：N0 失败则 N+1…直至 can_fit（写进 tools 与 message）
    retry_steps: List[str] = []
    if used > n0:
        for k in range(n0, used + 1):
            if k < used:
                retry_steps.append(f"try_N={k}:can_fit=False→N+1")
            else:
                retry_steps.append(f"try_N={k}:can_fit={container_plan.get('can_fit')}")
    elif not container_plan.get("can_fit"):
        retry_steps.append(f"try_N={n0}..{n_max}:仍 can_fit=False（达搜索上限）")
    else:
        retry_steps.append(f"try_N={n0}:can_fit=True（一次通过）")

    tools_used = [
        "booking.pack_with_auto_containers",
        "bin3d.pack_boxes_api",
        f"engine:{eng}",
    ]
    if used > n0:
        tools_used.append(f"retry:N0={n0}->used={used}")

    # 指标别名：floor_utilization ← floor_utilization_avg（前端/脚本统一读）
    if container_plan.get("floor_utilization") is None and container_plan.get(
        "floor_utilization_avg"
    ) is not None:
        container_plan["floor_utilization"] = container_plan["floor_utilization_avg"]
    floor_u = float(
        container_plan.get("floor_utilization")
        or container_plan.get("floor_utilization_avg")
        or 0
    )

    return {
        "container_plan": container_plan,
        "booking": booking_out,
        "agent_meta": {
            "node": "loader",
            "capability": ["使用工具", "采取行动", "追求目标"],
            "tools_used": tools_used,
            "artifacts": {
                "can_fit": container_plan.get("can_fit"),
                "containers_used": container_plan.get("containers_used"),
                "n0": n0_used,
                "booking_volume_utilization": book_u,
                "outer_space_utilization": outer_u,
                "floor_utilization": floor_u,
                "weight_utilization": container_plan.get("weight_utilization"),
                "retry_steps": retry_steps,
            },
        },
        "messages": [
            {
                "role": "assistant",
                "content": (
                    f"【行动·装载】engine={eng} "
                    f"can_fit={container_plan.get('can_fit')} "
                    f"用柜={used}(自N0*={n0_used}递增) "
                    f"{container_plan.get('multi_container_explain') or ''} "
                    + (
                        f" 策略={((container_plan.get('strategy_decision') or {}).get('chosen'))}"
                        f" mid50={container_plan.get('worst_mid50')} "
                        if container_plan.get("strategy_decision")
                        or container_plan.get("worst_mid50") is not None
                        else " "
                    )
                    + f"重试轨迹: {' → '.join(retry_steps)} "
                    f"外廓摆柜率{outer_u:.0%} "
                    f"订柜有效体积率{book_u:.0%} "
                    f"货外廓{float(container_plan.get('cargo_solid_volume_m3') or 0):.2f}m³/"
                    f"柜{float(container_plan.get('container_inner_volume_m3') or 0):.1f}m³ "
                    f"底面积{float(container_plan.get('floor_utilization_avg') or 0):.0%} "
                    f"重量{float(container_plan.get('weight_utilization') or 0):.0%} "
                    f"[{'; '.join(notes)}]"
                    f"｜tools={','.join(tools_used)}（已写入 layout，属系统内行动）"
                ),
            }
        ],
    }


def _soft_budget_post_densify(
    plan: Dict[str, Any],
    *,
    boxes: List[Dict[str, Any]],
    container_type: str,
    priority_order: List[str] | None,
    packing_opts: Dict[str, Any],
    notes: List[str],
) -> Dict[str, Any]:
    """
    Loader 二次压柜：优先无 multi_start 关 + 无 priority，在 25 附近找 mid≥0.55 最少柜。
    """
    if not plan or not boxes or packing_opts.get("disable_soft_budget_densify"):
        return plan
    try:
        from packing_assistant.tools.booking import _plan_worst_mid50
    except Exception as ex:
        notes.append(f"soft_densify_import_fail:{ex}")
        return plan

    used = int(plan.get("containers_used") or 0)
    ref = int(plan.get("reference_light_used") or 0)
    mid0 = _plan_worst_mid50(plan)
    plan["soft_densify_attempted"] = True
    if used < 6:
        return plan
    if ref <= 0:
        ref = max(1, used - 4)
    # 目标带：light .. light+3（至少覆盖 used）
    lo = max(1, min(ref, used))
    hi = min(max(used, ref + 3), lo + 5, 40)
    mid_tgt = float(packing_opts.get("soft_budget_mid50") or 0.60)
    notes.append(f"soft_densify_scan {lo}..{hi} ref={ref} used={used} mid0={mid0}")

    # Tool 对齐 opts（少扰动）优先；再完整 loader opts
    base = dict(packing_opts or {})
    tool_like = {
        "prefer_stack": True,
        "multi_start": True,
        "cog_aware": True,
        "cog_rebalance": True,
        "r4_repair": True,
        "r4_target_mid50": float(base.get("r4_target_mid50") or mid_tgt),
        "r0_r1": True,
        "r2_slab": True,
        "lateral_repair": True,
        "clearance_mm": int(base.get("clearance_mm") or 30),
        "support_ratio_min": float(base.get("support_ratio_min") or 0.55),
        "max_stack_layers": int(base.get("max_stack_layers") or 3),
        "prefer_bottom_weight_kg": float(base.get("prefer_bottom_weight_kg") or 2000),
        "lns_worst": False,
        "r3_repack": False,
    }
    densify_opt_list = [tool_like, base]

    best = plan
    best_key = (
        0 if (mid0 is not None and mid0 + 1e-9 >= 0.55) else 1,
        used,
        -(mid0 or 0),
    )
    # 大票压柜：默认不带 priority（与 Tool 捷径同构）
    prio_list: List[Any] = [None]
    if priority_order and not packing_opts.get("drop_load_priority", True):
        prio_list.append(priority_order)
    for densify_opts in densify_opt_list:
        for prio in prio_list:
            for n in range(lo, hi + 1):
                try:
                    trial = pack_boxes_api(
                        boxes,
                        container_type=container_type,
                        max_containers=n,
                        priority_order=prio,
                        packing_options=densify_opts,
                    )
                except Exception:
                    continue
                if not trial.get("can_fit"):
                    continue
                tu = int(trial.get("containers_used") or 0)
                if tu <= 0 or tu > n:
                    continue
                tm = _plan_worst_mid50(trial)
                ge55 = tm is not None and tm + 1e-9 >= 0.55
                improve_mid = (
                    tm is not None
                    and mid0 is not None
                    and tm > mid0 + 0.03
                    and tu <= used
                )
                if not ge55 and not improve_mid:
                    continue
                key = (0 if ge55 else 1, tu, -(tm or 0))
                if key < best_key:
                    best_key = key
                    best = dict(trial)
                    best["density_mode"] = (
                        "soft_budget_cog"
                        if tm is not None and tm + 1e-9 >= mid_tgt
                        else "soft_budget_cog_soft"
                    )
                    best["worst_mid50"] = tm
                    notes.append(
                        f"soft_densify_cand n={n} used={tu} mid={tm} "
                        f"prio={prio is not None} tool_like={densify_opts is tool_like}"
                    )
                    if (
                        ge55
                        and tm is not None
                        and tm + 1e-9 >= mid_tgt
                        and tu <= max(ref + 1, lo + 1)
                    ):
                        break
            if best_key[0] == 0 and best_key[1] <= max(ref + 1, lo + 1):
                break
        if best_key[0] == 0 and best_key[1] <= max(ref + 1, lo + 1):
            break

    if best is plan or int(best.get("containers_used") or 0) <= 0:
        notes.append("soft_densify_no_improve")
        return plan

    for k in (
        "booking",
        "n0",
        "n0_star",
        "n0_search",
        "n0_components",
        "n0_note",
        "reference_light_used",
        "reference_light_plan",
        "strategy_decision",
        "strategy_candidates",
        "n_tried",
    ):
        if plan.get(k) is not None and best.get(k) is None:
            best[k] = plan.get(k)
    best["reference_light_used"] = plan.get("reference_light_used") or ref
    best["soft_budget_applied"] = True
    best["loader_post_densify"] = True
    best["soft_densify_attempted"] = True
    best["worst_mid50"] = _plan_worst_mid50(best)
    # 刷新策略决策卡
    try:
        from packing_assistant.tools.booking import (
            _candidate_row,
            select_packing_strategy,
        )

        cands = []
        refp = best.get("reference_light_plan")
        if isinstance(refp, dict):
            cands.append(
                {
                    "strategy_id": "min_bins_light",
                    "used": int(refp.get("used") or ref),
                    "weight_utilization": refp.get("weight_utilization"),
                    "mid50": refp.get("mid50"),
                    "can_fit": True,
                    "reference_only": True,
                    "ship_ok_hint": False,
                    "note": "下界参考",
                }
            )
        sid = str(best.get("density_mode") or "soft_budget_cog")
        cands.append(_candidate_row(sid, best, note="loader soft densify"))
        best["strategy_decision"] = select_packing_strategy(cands)
        best["strategy_candidates"] = cands
    except Exception:
        pass
    notes.append(
        f"loader_soft_densify {used}->{best.get('containers_used')} "
        f"mid50={best.get('worst_mid50')}"
    )
    return best


def _want_one_box_per_container(
    state: PackingState,
    opts: Dict[str, Any],
    boxes: List[Dict[str, Any]],
) -> bool:
    if not boxes:
        return False
    if opts.get("one_box_per_container") or opts.get("one_crate_per_container"):
        return True
    if opts.get("force_containers") is not None:
        try:
            return int(opts.get("force_containers")) == len(boxes)
        except Exception:
            pass
    # 用户目标：尽量少拼柜 / 分柜出运
    goal = str(state.get("goal") or "")
    if goal in ("one_box_per_container", "split_all_boxes"):
        return True
    text = " ".join(
        [
            str(state.get("user_input") or ""),
            str(state.get("adjust_note") or ""),
            str((state.get("nl_revision") or {}).get("instruction") or ""),
        ]
    )
    keys = (
        "一箱一柜",
        "一箱一集装箱",
        "每箱一柜",
        "分柜出运",
        "不拼柜",
        "one_box_per_container",
        "1 box 1 container",
    )
    return any(k in text for k in keys)


def _plan_one_box_per_container(
    boxes: List[Dict[str, Any]],
    container_type: str,
    booking: Dict[str, Any],
) -> Dict[str, Any]:
    """每箱独占一个集装箱：layout.container_no = 1..N。"""
    layout: List[Dict[str, Any]] = []
    unpacked: List[str] = []
    for i, b in enumerate(boxes):
        bid = str(b.get("box_id") or f"B{i+1}")
        outer = b.get("outer_size_mm") or {}
        try:
            dx = int(round(float(outer.get("length") or 1)))
            dy = int(round(float(outer.get("width") or 1)))
            dz = int(round(float(outer.get("height") or 1)))
        except Exception:
            unpacked.append(bid)
            continue
        layout.append(
            {
                "box_id": bid,
                "container_no": i + 1,
                "position": {"x": 0, "y": 0, "z": 0},
                "size": {"dx": dx, "dy": dy, "dz": dz},
                "layer": 1,
                "gross_weight_kg": b.get("gross_weight_kg"),
            }
        )
    n = len(layout)
    return {
        "container_type": container_type,
        "containers_used": n,
        "n0": n,
        "can_fit": n > 0 and not unpacked,
        "layout": layout,
        "unpacked_box_ids": unpacked,
        "engine": "one_box_per_container",
        "message": f"一箱一柜：{n} 箱 → {n} 集装箱",
        "mode": "one_box_per_container",
        "booking": dict(booking or {}),
        "volume_basis": "solid_outer_aabb",
    }


def _enrich_plan_metrics(
    plan: Dict[str, Any],
    *,
    boxes: List[Dict[str, Any]],
    booking: Dict[str, Any],
    container_type: str,
    n0: int,
) -> Dict[str, Any]:
    """
    为任意装载引擎结果补齐双指标：
    - outer_space_utilization = 摆柜几何（space_utilization）
    - booking_volume_utilization = V_eff / (用柜 × 可用容积)
    禁止把 outer 当成订柜体积分子。
    """
    out = dict(plan or {})
    book = dict(out.get("booking") or booking or {})
    if not book and boxes:
        try:
            from packing_assistant.tools.booking import compute_booking

            book = compute_booking(
                boxes=boxes, container_type=container_type, fill_ratio=0.82
            )
        except Exception:
            book = {}
    if book:
        out["booking"] = book
    if out.get("n0") is None:
        out["n0"] = int(book.get("n0") or n0 or 1)

    outer_u = float(out.get("outer_space_utilization") or out.get("space_utilization") or 0)
    out["outer_space_utilization"] = outer_u
    # 兼容旧字段：space_utilization 仅表示外廓摆柜率
    if out.get("space_utilization") is None:
        out["space_utilization"] = outer_u

    book_u = out.get("booking_volume_utilization")
    if book_u is None or (isinstance(book_u, (int, float)) and float(book_u) <= 0):
        used = int(out.get("containers_used") or 0) or int(out.get("n0") or n0 or 1)
        usable_one = float(book.get("usable_m3_per_container") or 0)
        if usable_one <= 0:
            # 40HQ 默认 76.4×0.82
            usable_one = 76.4 * 0.82
        v_eff = float(book.get("volume_m3") or 0)
        denom = usable_one * max(used, 1)
        book_u = round(min(v_eff / denom, 9.99), 4) if denom > 0 and v_eff > 0 else 0.0
        out["booking_volume_utilization"] = book_u
        out["booking_volume_basis"] = "recomputed_from_booking_V_eff"
    else:
        out["booking_volume_utilization"] = float(book_u)
        out.setdefault("booking_volume_basis", "engine_or_auto")
    return out


def _local_1d(boxes: List[Dict[str, Any]], ctype: str) -> Dict[str, Any]:
    internal = boxes_to_internal(boxes)
    raw = run_consolidation(internal, container_type=ctype)
    layout_api: List[Dict[str, Any]] = []
    detail = raw.get("详情") or {}
    overflow = set(detail.get("溢出箱号") or [])
    unpacked: List[str] = []

    for item in raw.get("布局") or []:
        bid = item.get("箱号") or ""
        box = next((b for b in boxes if b.get("box_id") == bid), {})
        outer = box.get("outer_size_mm") or {}
        start_m = float(item.get("起始位置_m") or 0)
        length_m = float(item.get("长度_m") or 0)
        if bid in overflow:
            unpacked.append(bid)
        layout_api.append(
            {
                "box_id": bid,
                "container_no": 1,
                "position": {"x": int(round(start_m * 1000)), "y": 0, "z": 0},
                "size": {
                    "dx": int(outer.get("length") or length_m * 1000),
                    "dy": int(outer.get("width") or 0),
                    "dz": int(outer.get("height") or 0),
                },
                "rotation": "LWH",
                "layer": int(item.get("层级") or 1),
            }
        )

    def _pct(s: str) -> float:
        try:
            return float(str(s).replace("%", "")) / 100.0
        except ValueError:
            return 0.0

    return {
        "container_type": raw.get("柜型") or ctype,
        "containers_used": 1 if layout_api else 0,
        "space_utilization": round(_pct(raw.get("空间利用率") or "0%"), 4),
        "weight_utilization": round(_pct(raw.get("重量利用率") or "0%"), 4),
        "can_fit": len(unpacked) == 0 and len(layout_api) > 0,
        "layout": layout_api,
        "unpacked_box_ids": unpacked,
        "message": raw.get("结论") or "",
        "engine": "local-1d",
    }
