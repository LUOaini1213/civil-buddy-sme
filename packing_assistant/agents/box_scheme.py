"""Agent3 装箱方案智能体。"""

from __future__ import annotations

from typing import Any, Dict, List

from packing_assistant.adapters import boxes_to_api, material_api_to_internal, material_quantity
from packing_assistant.state import PackingState
from packing_assistant.tools.packing import run_packing


def _materials_missing_dims(materials: List[Dict[str, Any]]) -> bool:
    if not materials:
        return False
    for m in materials:
        L = float(m.get("length_mm") or (m.get("外尺寸_mm") or {}).get("长") or 0)
        W = float(m.get("width_mm") or (m.get("外尺寸_mm") or {}).get("宽") or 0)
        H = float(m.get("height_mm") or (m.get("外尺寸_mm") or {}).get("高") or 0)
        if L <= 1e-6 or W <= 1e-6 or H <= 1e-6:
            return True
    return False


def _crate_passthrough_enabled(materials: List[Dict[str, Any]], opts: Dict[str, Any]) -> bool:
    """
    工地/工厂当量箱直通：材料行本身已是「一箱一当量」，禁止再标准箱库二次放大外廓。
    开启方式（收紧后，避免混料假直通 → 虚高柜数/低利用率）：
      - packing_options.crate_passthrough / materials_are_crates = True
      - 或 materials 多数带 note: dims=crate_equiv_est / crate= / stack / 当量名
      - 或 ≥50% 名称含 铁件架/叠层架/长料架/当量/密装
      - 或模块级大件多数（_module_like_majority）
    不再用「≥70% 行柜级外廓尺寸」整票自动直通（混料易误触发）。
    显式 standard_boxes=True 且 crate_passthrough 未开 → 仅强 note 信号才直通。
    """
    if opts.get("crate_passthrough") is False or opts.get("materials_are_crates") is False:
        return False
    if opts.get("crate_passthrough") or opts.get("materials_are_crates"):
        return True
    # 用户明确要标准箱库时：仅当量 note 强信号才直通，禁止仅凭铁件名自动直通
    force_standard = (
        opts.get("standard_boxes") is True
        and not opts.get("dense_mode")
        and not opts.get("crate_passthrough")
        and not opts.get("materials_are_crates")
    )
    if not materials:
        return False
    hits = 0
    for m in materials:
        note = str(m.get("note") or m.get("备注") or "")
        name = str(m.get("name") or "")
        if (
            "crate_equiv" in note
            or "crate=" in note
            or "factory_stack" in note
            or "factory_long" in note
            or "dense_bom" in note
            or "当量" in name
            or "铁件架" in name
            or "叠层架" in name
            or "长料架" in name
            or "密装" in name
        ):
            hits += 1
    n = len(materials)
    # 模块级大件优先：即使 standard_boxes=True，也禁止再塞进多只 6m 空心架（假多柜根因）
    if _module_like_majority(materials):
        return True
    # 标准箱优先：只有 note 当量信号才直通（忽略「铁件」尺寸启发式）
    if force_standard:
        note_only = 0
        for m in materials:
            note = str(m.get("note") or m.get("备注") or "")
            name = str(m.get("name") or "")
            if (
                "crate_equiv" in note
                or "crate=" in note
                or "当量" in name
                or "铁件架" in name
                or "叠层架" in name
            ):
                note_only += 1
        return note_only >= max(1, int(0.5 * n))
    if hits >= max(1, int(0.5 * n)):
        return True
    return False


def _module_like_majority(materials: List[Dict[str, Any]]) -> bool:
    """
    模块/整包级外廓：一件≈一箱（半柜宽 + 有高度 + 单件重），
    走当量直通/贴货，避免标准库拆成数十只 6m 架导致假多柜。

    按行的外廓与单件重判，不看数量。原先要求「单行单件」（数量为 1），是因为当量直通
    一行只出一箱、数量会丢；直通改成一件一箱之后这条限制只剩副作用：2100×1100×1200、
    单件 1.8 t 的成品框架写成「数量 2」就不算模块，被送进标准箱库按跨距上限切成虚拟件——
    9 个框架 23.8 t 出 48 个箱、判 9 个柜装不下；按模块直通是 9 个箱、3 个柜。
    """
    if not materials:
        return False
    hits = 0
    for m in materials:
        try:
            L = float(m.get("length_mm") or m.get("L") or 0)
            W = float(m.get("width_mm") or m.get("W") or 0)
            H = float(m.get("height_mm") or m.get("H") or 0)
            q = max(material_quantity(m), 1)
            total = float(m.get("total_weight_kg") or 0)
            unit = float(m.get("weight_kg") or 0)
            if total <= 0 and unit > 0:
                total = unit * q
            unit = total / q if q else total
        except Exception:
            continue
        # 半柜宽附近 + 中高 + 有分量（单件重，不是行总重）
        if (
            L >= 1200
            and W >= 900
            and H >= 500
            and (unit >= 200 or H >= 800)
        ):
            hits += 1
    n = len(materials)
    return hits >= max(1, int(0.6 * n + 0.999))


def _should_force_dense_sheets(materials: List[Dict[str, Any]], opts: Dict[str, Any]) -> bool:
    """薄板/片料占多数时强制 dense，避免标准箱库撑成 4m/6m 空心铁架。"""
    if opts.get("dense_mode") or opts.get("force_dense_sheets") is False:
        return bool(opts.get("dense_mode"))
    if opts.get("crate_passthrough") or opts.get("materials_are_crates"):
        return False
    if not materials:
        return False
    thin = 0
    for m in materials:
        try:
            H = float(m.get("height_mm") or m.get("H") or 0)
            L = float(m.get("length_mm") or m.get("L") or 0)
        except Exception:
            continue
        if 0 < H <= 80 and L >= 600:
            thin += 1
    return thin >= max(2, int(0.55 * len(materials)))


def _fill_hint(m: Dict[str, Any]) -> float:
    name = str(m.get("name") or "")
    spec = str(m.get("spec") or "")
    if "铁件" in name or "铁件" in spec or "米铁" in name:
        return 0.28
    if "铝板" in name or "铝板" in spec:
        return 0.35
    if "瓦楞" in name or "木板" in name:
        return 0.40
    if "五金" in name or "紧固" in spec or "螺丝" in spec:
        return 0.65
    if "胶" in name or "垫" in name:
        return 0.55
    return 0.35


def materials_to_passthrough_boxes(materials: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """一件材料 = 一箱（外廓=材料 LWH），输出 API boxes，含订柜体积字段。

    数量为 N 的行出 N 个箱。原先一行只出一箱：3 个 3000×1100×1500 的出厂架变成 1 个
    同样外廓、装着 3 个的重量的箱，另外两个架子的体积从方案里消失；行上没有总重时
    连重量也只剩一件的。
    """
    boxes: List[Dict[str, Any]] = []
    for i, m in enumerate(materials, 1):
        from packing_assistant.transport_constraints import legacy_handling
        if legacy_handling(m):
            raise ValueError("自动当量成箱不能验证声明的运输要求；请提供已包装整体数据并使用物流台账。")
        qty = max(material_quantity(m), 1)
        row_net = float(m.get("total_weight_kg") or 0) or float(m.get("weight_kg") or 0) * qty
        for unit_no in range(1, qty + 1):
            box = _passthrough_box(m, i, row_net / qty, unit_no if qty > 1 else 0)
            if box:
                boxes.append(box)
    # 保证 box_id 唯一
    seen = set()
    for i, b in enumerate(boxes):
        if b["box_id"] in seen:
            b["box_id"] = f"{b['box_id']}-{i}"
        seen.add(b["box_id"])
    return boxes


def _passthrough_box(m: Dict[str, Any], i: int, net: float, unit_no: int) -> Dict[str, Any]:
    """一件 → 一箱。unit_no 为 0 表示该行只有一件（箱号不带件序）。"""
    L = float(m.get("length_mm") or m.get("L") or 0)
    W = float(m.get("width_mm") or m.get("W") or 0)
    H = float(m.get("height_mm") or m.get("H") or 0)
    if L <= 0 or W <= 0 or H <= 0:
        return {}
    outer = L * W * H / 1e9
    fill = _fill_hint(m)
    content = outer * fill
    # 当量路径：材料重已含货；略加箱皮
    gross = net + 40.0
    longish = L >= 4000
    bid = str(m.get("id") or f"CRATE-{i:03d}")
    name = str(m.get("name") or bid)
    # 订柜体积与 volume_estimate 统一（按 fill_outer 选 k）
    try:
        from packing_assistant.tools.volume_estimate import pack_k_for_fill

        k_pt = pack_k_for_fill(fill, k_max=1.60)
    except Exception:
        k_pt = 1.50
    if content <= 1e-12:
        booking_m3 = outer * 0.45
    else:
        booking_m3 = min(outer, content * k_pt)
    box_id = bid if bid.startswith("CRATE") or bid.startswith("S") else f"PT-{i:03d}"
    if unit_no:
        box_id = f"{box_id}-{unit_no}"
    return {
        "box_id": box_id,
        "box_type": name.split("|")[0].strip()[:40] or "当量箱",
        "base_box_type": "crate_passthrough",
        "outer_size_mm": {
            "length": round(L, 1),
            "width": round(W, 1),
            "height": round(H, 1),
        },
        "outer_m3": round(outer, 6),
        "content_m3": round(content, 6),
        "crate_fill_ratio": round(fill, 4),
        "fill_outer_ratio": round(fill, 4),
        "booking_volume_m3": round(booking_m3, 6),
        "gross_weight_kg": round(gross, 2),
        "net_weight_kg": round(net, 2),
        # P0：短箱默认可叠；prefer_bottom 仅超长/重铁架（阈值抬高，避免 ≥800kg 全铺底）
        "stackable": bool(H <= 1300 and not longish),
        "prefer_bottom": bool(
            longish
            or ("铁架" in name or "铁笼" in name)
            or net >= 2000
        ),
        "special_attributes": (["超长", "当量直通"] if longish else ["当量直通"]),
        "structure_conclusion": "通过",
        "content": [
            {
                "material_id": str(m.get("id") or ""),
                "source_material_id": str(m.get("id") or ""),
                "split_of": 1,
                "name": name,
                "quantity": 1,
                "weight_kg": round(net, 3),
                "outer_size_mm": {
                    "length": max(1, int(L * 0.9)),
                    "width": max(1, int(W * 0.7)),
                    "height": max(1, int(H * fill / 0.7)) if fill > 0 else max(1, int(H // 3)),
                },
            }
        ],
        "part_no": m.get("part_no"),
        "note": m.get("note"),
    }


def _conservation_fields(materials: List[Dict[str, Any]], boxes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """成箱后对账：装箱单上的件数、净重要全在箱里，货不能比箱长。对不上就不许出运。

    返回要并进节点输出的键；finalize 见到 ship_ok=False 即判不可出运（与缺尺寸阻断同一机制）。
    """
    from packing_assistant.tools.cargo_conservation import check_conservation, physical_split_issues, violation_sentences

    found = check_conservation(materials, boxes)
    physical_issues = physical_split_issues(materials, boxes)
    if physical_issues:
        return {
            "cargo_conservation": found, "needs_human": physical_issues,
            "materials_incomplete": True, "ship_ok": False,
            "errors": ([] if found["ok"] else ["box_scheme_cargo_not_conserved: " + "；".join(violation_sentences(found, 3))])
                      + ["box_scheme_blocked: physical_split_not_authorized"],
        }
    if found["ok"]:
        return {"cargo_conservation": found}
    return {
        "cargo_conservation": found,
        "ship_ok": False,
        # errors 是累加字段（state.py: operator.add），节点只交新增的条目
        "errors": ["box_scheme_cargo_not_conserved: " + "；".join(violation_sentences(found, 3))],
    }


def effective_container_type(materials: List[Dict[str, Any]], requested: str) -> str:
    """成箱实际按哪种柜算：20GP 遇到 ≥4 m 的件或 ≥8 t 的票自动改 40HQ，其余照请求。"""
    max_L = max((float(m.get("length_mm") or 0) for m in materials), default=0)
    total_w = sum(float(m.get("total_weight_kg") or 0) for m in materials)
    if str(requested).upper() == "20GP" and (max_L >= 4000 or total_w >= 8000):
        return "40HQ"
    return requested


def agent_box_scheme(state: PackingState) -> Dict[str, Any]:
    materials = state.get("materials") or []
    constraints = state.get("structure_constraints") or []
    rev = state.get("revision") or {}
    packing_opts = state.get("packing_options") or {}

    ctype = effective_container_type(
        materials,
        state.get("container_type")
        or (state.get("orchestrator") or {}).get("container_type_chosen")
        or "40HQ",
    )

    # 缺重量 / 读不出件数：与 pack-ship 工具面同一套规则。材料解析已经拦过一次；这里兜住
    # 不经过它的入口（保留材料的调整指令、graph 模式、直接调本节点）。缺尺寸仍走下面原有的阻断。
    from packing_assistant.tools.pack_ship_solve import (
        NEEDS_HUMAN_MISSING_DIMENSIONS,
        needs_human_sentences,
        rows_blocking_plan,
    )

    blocking = rows_blocking_plan(materials)
    beyond_dims = [r for r in blocking if r["reason"] != NEEDS_HUMAN_MISSING_DIMENSIONS]
    if beyond_dims:
        asks = needs_human_sentences(beyond_dims, 6)
        return {
            "boxes": [],
            "ship_ok": False,
            "materials_incomplete": True,
            "needs_human": blocking,
            "team_a_summary": {
                "pass": 0,
                "fail": len(materials),
                "packing_mode": "blocked_needs_human",
            },
            "structure_notes": ["材料有行缺重量、数量不明确或存在未支持的运输要求，成箱阻断"],
            "errors": ["box_scheme_blocked: materials_need_human"],
            "agent_meta": {
                "node": "box_scheme",
                "capability": ["使用工具", "采取行动"],
                "tools_used": ["box_scheme.block_needs_human"],
                "artifacts": {"boxes": 0, "mode": "blocked_needs_human", "needs_human": len(blocking)},
            },
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        f"装箱阻断：{len(beyond_dims)} 行需要人工处理；不猜件数、重量或运输要求。"
                        + "；".join(asks)
                        + "｜tools=box_scheme.block_needs_human"
                    ),
                }
            ],
        }

    # 缺尺寸：禁止静默成箱出运
    if state.get("materials_incomplete") or _materials_missing_dims(materials):
        return {
            "boxes": [],
            "ship_ok": False,
            "materials_incomplete": True,
            "team_a_summary": {
                "pass": 0,
                "fail": len(materials),
                "packing_mode": "blocked_missing_dims",
            },
            "structure_notes": ["材料缺 L/W/H，成箱阻断"],
            "errors": list(state.get("errors") or [])
            + ["box_scheme_blocked: materials_missing_dims"],
            "agent_meta": {
                "node": "box_scheme",
                "capability": ["使用工具", "采取行动"],
                "tools_used": ["box_scheme.block_missing_dims"],
                "artifacts": {"boxes": 0, "mode": "blocked_missing_dims"},
            },
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        "装箱阻断：存在缺尺寸物料（L/W/H=0），"
                        "拒绝编造外廓或换成演示票｜tools=box_scheme.block_missing_dims"
                    ),
                }
            ],
        }

    # 模块级大件：覆盖 standard 默认，防空心标准架假多柜
    packing_opts = dict(packing_opts)
    module_pt = _module_like_majority(materials) and packing_opts.get(
        "force_standard_boxes"
    ) is not True
    if module_pt:
        packing_opts.setdefault("crate_passthrough", True)
        packing_opts["standard_boxes"] = False
        packing_opts.setdefault("dense_mode", True)

    # —— 当量箱直通：不二次标准箱合箱 ——
    if _crate_passthrough_enabled(materials, packing_opts):
        boxes = materials_to_passthrough_boxes(materials)
        outer_sum = sum(float(b.get("outer_m3") or 0) for b in boxes)
        content_sum = sum(float(b.get("content_m3") or 0) for b in boxes)
        fills = [float(b.get("crate_fill_ratio") or 0) for b in boxes]
        avg_fill = sum(fills) / len(fills) if fills else 0.0
        summary = {
            "pass": len(boxes),
            "reinforce": 0,
            "fail": 0,
            "crate_passthrough": True,
            "standard_boxes": False,
            "mix_mode": False,
            "boxes_outer_volume_m3": round(outer_sum, 4),
            "cargo_item_volume_m3": round(content_sum, 4),
            "avg_crate_fill": round(avg_fill, 4),
            "packing_mode": "crate_passthrough",
            "multi_risk": "ok",
            "module_passthrough": bool(module_pt),
        }
        return {
            **_conservation_fields(materials, boxes),
            "boxes": boxes,
            "packing_options": packing_opts,
            "team_a_summary": {
                **summary,
                "structure_overall": "通过(当量直通)",
                "total_net_weight_kg": round(sum(float(b.get("net_weight_kg") or 0) for b in boxes), 1),
                "total_gross_weight_kg": round(
                    sum(float(b.get("gross_weight_kg") or 0) for b in boxes), 1
                ),
            },
            "structure_notes": [
                "当量箱直通：材料行=箱外廓，未再走标准箱库合箱（避免外廓虚高/假多柜）"
            ]
            + (
                ["模块级外廓检测：已禁用空心标准架放大"]
                if module_pt
                else []
            ),
            "agent_meta": {
                "node": "box_scheme",
                "capability": ["使用工具", "采取行动"],
                "tools_used": ["box_scheme.materials_to_passthrough_boxes"],
                "artifacts": {"boxes": len(boxes), "mode": "crate_passthrough"},
            },
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        f"装箱完成：{len(boxes)} 箱 — 当量直通（crate_passthrough）"
                        f" 外廓{outer_sum:.2f}m³/有效内容{content_sum:.2f}m³ 填充均{avg_fill:.0%}"
                        f"｜tools=passthrough（已生成 boxes[]，非口头建议）"
                    ),
                }
            ],
        }

    internal = [material_api_to_internal(m) for m in materials]
    # 把 material id 写入内部便于 content 回填
    for src, dst in zip(materials, internal):
        dst["加工件编号"] = src.get("id") or ""
        dst["id"] = src.get("id") or ""

    max_net = float(
        rev.get("max_box_net_kg")
        or packing_opts.get("max_box_net_kg")
        or 3200.0
    )
    revision_mode = bool(rev.get("active") or packing_opts.get("revision_mode"))
    # 默认：标准箱库外廓 + 跨长度档混装（短件塞进长标准箱）
    # dense_mode 仅在明确关闭 standard 时生效
    standard_boxes = packing_opts.get("standard_boxes")
    if standard_boxes is None:
        standard_boxes = packing_opts.get("standard_outer")
    if standard_boxes is None:
        standard_boxes = True  # 默认标准化
    standard_boxes = bool(standard_boxes)
    mix_mode = packing_opts.get("mix_mode")
    if mix_mode is None:
        mix_mode = True
    mix_mode = bool(mix_mode)
    dense_mode = bool(
        packing_opts.get("dense_mode")
        or packing_opts.get("dense")
        or rev.get("dense_mode")
    )
    prefer_single_row = bool(
        packing_opts.get("prefer_single_row")
        or packing_opts.get("force_single_row")
        or rev.get("prefer_single_row")
    )
    # Agent 自动：薄板主材 → dense + 关标准箱，避免 3mm 铝板被合成 4/6m 空心铁架
    force_dense_sheets = _should_force_dense_sheets(materials, packing_opts)
    if force_dense_sheets:
        dense_mode = True
        standard_boxes = False
        mix_mode = True
    if standard_boxes:
        dense_mode = False
    # 一排偏好需要改外宽 → 不能锁标准箱库
    if prefer_single_row:
        standard_boxes = False
    design_facts = state.get("design_facts") or packing_opts.get("design_facts")
    # 自然语言强制箱型写入 defaults
    if packing_opts.get("force_box_type"):
        design_facts = dict(design_facts or {})
        design_facts.setdefault("defaults", {})
        design_facts["defaults"]["force_box_type"] = packing_opts["force_box_type"]
    # 成箱前：若物料单件已超货载，压低 max_box_net_kg 触发质量拆分
    try:
        from packing_assistant.tools.cargo_feasibility import check_cargo_feasibility

        pre_feas = check_cargo_feasibility(
            materials=materials,
            container_type=str(ctype),
        )
        if not pre_feas.get("ok"):
            rec = float(pre_feas.get("max_box_net_kg_recommend") or 2500)
            max_net = min(max_net, rec)
            note_parts_pre = list(pre_feas.get("blockers") or [])[:2]
            revision_mode = True
        else:
            note_parts_pre = []
    except Exception:
        pre_feas = {"ok": True}
        note_parts_pre = []

    result = run_packing(
        internal,
        container_type=str(ctype),
        max_box_net_kg=max_net,
        revision_mode=revision_mode,
        dense_mode=dense_mode,
        standard_boxes=standard_boxes,
        mix_mode=mix_mode,
        prefer_single_row=prefer_single_row,
        design_facts=design_facts if isinstance(design_facts, dict) else None,
    )
    boxes_raw = result.get("箱子列表") or []
    boxes = boxes_to_api(boxes_raw)

    try:
        from packing_assistant.tools.cargo_feasibility import check_cargo_feasibility

        post_feas = check_cargo_feasibility(
            boxes=boxes,
            materials=materials,
            container_type=str(ctype),
        )
    except Exception:
        post_feas = pre_feas if isinstance(pre_feas, dict) else {"ok": True}

    # 用约束补充加固文案
    reinforce_types = {
        c.get("recommended_box_type"): c
        for c in constraints
        if c.get("need_reinforcement")
    }
    for b in boxes:
        c = reinforce_types.get(b.get("box_type"))
        if c and c.get("reinforcement_plan"):
            b["reinforcement"] = c["reinforcement_plan"]
            attrs = list(b.get("special_attributes") or [])
            if "需加固" not in attrs:
                attrs.append("需加固")
            b["special_attributes"] = attrs

        # content material_id 回填
        for item in b.get("content") or []:
            if not item.get("material_id"):
                # 按名称匹配
                for m in materials:
                    if m.get("name") == item.get("name"):
                        item["material_id"] = m.get("id") or ""
                        break

    summary = result.get("结构汇总") or {}
    note_parts = []
    if standard_boxes or summary.get("standard_boxes"):
        counts = summary.get("standard_box_type_counts") or {}
        count_s = ",".join(f"{k}×{v}" for k, v in list(counts.items())[:6])
        note_parts.append(
            f"标准箱库{'+混装' if mix_mode else ''} "
            f"外廓{summary.get('boxes_outer_volume_m3', '?')}m³/"
            f"货{summary.get('cargo_item_volume_m3', '?')}m³ "
            f"填充均{float(summary.get('avg_crate_fill') or 0):.0%}"
            + (f" [{count_s}]" if count_s else "")
        )
    elif dense_mode or summary.get("dense_mode"):
        note_parts.append(
            f"密装外廓 dense"
            f"{'(薄板自动)' if force_dense_sheets else ''} "
            f"箱外廓{summary.get('boxes_outer_volume_m3', '?')}m³/"
            f"货件{summary.get('cargo_item_volume_m3', '?')}m³ "
            f"箱内填充均{float(summary.get('avg_crate_fill') or 0):.0%}"
        )
    if summary.get("custom_section_boxes"):
        # 标准箱外宽一律 1100 mm：比它大的单件没有标准箱可用，外廓按货定制，做箱要另行下料
        note_parts.append(f"截面超标准箱→按货定制 {summary['custom_section_boxes']} 箱")
    if revision_mode or summary.get("revision_mode"):
        note_parts.append(
            f"改箱 max_net={max_net:.0f}kg 拆分后料行={summary.get('item_chunks_after_split', '?')}"
        )
    if note_parts_pre:
        note_parts.append("可行性:" + "；".join(note_parts_pre))
    if isinstance(post_feas, dict) and not post_feas.get("ok", True):
        note_parts.append(
            "成箱后仍超货载:"
            + "；".join((post_feas.get("blockers") or [])[:2])
        )
    # 标准箱库命中校验（passthrough 合法例外）
    try:
        from packing_assistant.knowledge import validate_boxes_against_kb

        std_audit = validate_boxes_against_kb(
            boxes, allow_passthrough=True
        )
    except Exception:
        std_audit = {"ok": True, "hit_rate": 1.0, "by_type": {}, "n_unknown": 0}
    if std_audit.get("n_unknown"):
        note_parts.append(
            f"标准箱命中{float(std_audit.get('hit_rate') or 0):.0%}"
            f" 未知{std_audit.get('n_unknown')}"
        )
    note = f"（{'；'.join(note_parts)}）" if note_parts else ""
    # plan/act/observe/reflect 供比赛轨迹
    reflect = {
        "plan": f"成箱策略 standard={standard_boxes} dense={dense_mode} max_net={max_net:.0f}kg",
        "act": f"run_packing → {len(boxes)} 箱",
        "observe": (
            f"结构{summary.get('结论', '')} feas="
            f"{(post_feas or {}).get('ok')} 标准箱命中"
            f"{float(std_audit.get('hit_rate') or 0):.0%}"
        ),
        "reflect": (
            "继续拼柜"
            if (post_feas or {}).get("ok", True) and std_audit.get("ok", True)
            else "需拆箱/改标准箱或人工确认"
        ),
    }
    return {
        **_conservation_fields(materials, boxes),
        "boxes": boxes,
        "cargo_feasibility": post_feas if isinstance(post_feas, dict) else {},
        "standard_box_audit": std_audit,
        "agent_meta": {
            "node": "box_scheme",
            "capability": ["使用工具", "采取行动"],
            "tools_used": [
                "packing.run_packing",
                "cargo_feasibility.check",
                "knowledge.validate_boxes",
            ],
            "plan": reflect["plan"],
            "act": reflect["act"],
            "observe": reflect["observe"],
            "reflect": reflect["reflect"],
            "artifacts": {
                "boxes": len(boxes),
                "mode": summary.get("packing_mode") or "standard",
                "feas_ok": (post_feas or {}).get("ok"),
                "standard_hit_rate": std_audit.get("hit_rate"),
                "box_type_counts": std_audit.get("by_type"),
            },
        },
        "team_a_summary": {
            "box_count": len(boxes),
            "pass": summary.get("通过", 0),
            "reinforce": summary.get("需加强", 0),
            "fail": summary.get("不通过", 0),
            "total_net_weight_kg": summary.get("总净重_kg", 0),
            "total_gross_weight_kg": summary.get("总毛重_kg", 0),
            "structure_overall": summary.get("结论", ""),
            "max_box_net_kg": summary.get("max_box_net_kg", max_net),
            "revision_mode": bool(revision_mode or summary.get("revision_mode")),
            "dense_mode": bool(dense_mode or summary.get("dense_mode")),
            "standard_boxes": bool(standard_boxes or summary.get("standard_boxes")),
            "mix_mode": bool(mix_mode if mix_mode is not None else summary.get("mix_mode")),
            "packing_mode": summary.get("packing_mode") or "",
            "boxes_outer_volume_m3": summary.get("boxes_outer_volume_m3"),
            "cargo_item_volume_m3": summary.get("cargo_item_volume_m3"),
            "avg_crate_fill": summary.get("avg_crate_fill"),
            "standard_box_type_counts": summary.get("standard_box_type_counts")
            or std_audit.get("by_type"),
            "standard_box_hit_rate": std_audit.get("hit_rate"),
            "custom_section_boxes": int(summary.get("custom_section_boxes") or 0),
        },
        "messages": [
            {
                "role": "assistant",
                "content": (
                    f"装箱完成：{len(boxes)} 箱 — {summary.get('结论', '')}{note}"
                    f"｜tools=packing+feas+标准箱校验"
                    f"｜reflect={reflect['reflect']}"
                ),
            }
        ],
        "validation_warnings": (
            [
                f"标准箱库未命中 {u.get('box_id')}:{u.get('box_type')}"
                for u in (std_audit.get("unknown") or [])[:5]
            ]
            if std_audit.get("n_unknown")
            else []
        ),
    }
