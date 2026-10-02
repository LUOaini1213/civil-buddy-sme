"""Agent1 材料解析智能体。"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from packing_assistant.adapters import classify_material, material_internal_to_api
from packing_assistant.state import PackingState
from packing_assistant.tools.pack_ship_solve import (
    NEEDS_HUMAN_MISSING_DIMENSIONS,
    needs_human_sentences,
    rows_blocking_plan,
    rows_invalid_quantity,
)


def agent_material_parser(state: PackingState) -> Dict[str, Any]:
    raw = (state.get("user_input") or state.get("raw_input") or "").strip()  # type: ignore[arg-type]
    existing = state.get("materials") or []

    # 调整指令且已有材料：保留
    note = state.get("adjust_note") or ""
    if existing and note and _is_adjust_only(note) and _has_metrics_api(existing):
        summary = _summary(existing)
        perception = _build_perception(existing, summary, source="retain", note=note)
        return {
            "materials": existing,
            "materials_summary": {**summary, "categories": perception.get("categories")},
            "perception": perception,
            "agent_meta": {
                "node": "material_parser",
                "capability": ["感知环境"],
                "tools_used": ["material_parser.retain"],
                "artifacts": {"total_pieces": summary.get("total_pieces")},
            },
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"【感知】保留材料 {len(existing)} 条，应用调整指令。"
                        f" {perception.get('summary_text')}"
                    ),
                }
            ],
        }

    llm_note = ""
    incomplete_dims = False
    existing_norm = _normalize_llm_materials(list(existing)) if existing else []
    existing_complete = bool(existing_norm) and _has_metrics_api(existing_norm) and not _has_incomplete_dims(
        existing_norm
    )
    # 显式注入且三维完整：优先保留。禁止把「只准 1 个柜」这类句子当成清单，
    # 更不能 fallback 时强行 materials_incomplete=True。
    if existing_complete:
        mats = existing_norm
        source = "inject"
        incomplete_dims = False
    elif existing and not _looks_like_list(raw):
        mats = existing_norm or list(existing)
        source = "inject" if _has_metrics_api(mats) else "inject_partial"
        incomplete_dims = _has_incomplete_dims(mats)
    else:
        mats = _rule_parse(raw)
        source = "rule"
        # DeepSeek 等 LLM 增强结构化（仅当像材料清单时）
        if raw and _looks_like_list(raw):
            from packing_assistant.llm import chat_json_array, llm_available

            if llm_available():
                llm_mats = chat_json_array(
                    system=(
                        "你是钢结构装箱材料解析助手。从用户输入提取材料清单，"
                        "只输出 JSON 数组，每项字段："
                        "id,name,spec,length_mm,width_mm,height_mm,weight_kg,quantity,total_weight_kg,category。"
                        "category 只能是：超长件|重件|普通件。"
                        "length_mm>=4000 为超长件；单重>=200 为重件。"
                        "数字无法确定填 0。不要输出其它文字。"
                    ),
                    user=raw,
                )
                if llm_mats:
                    mats = _normalize_llm_materials(llm_mats)
                    source = "llm"
                    llm_note = f" LLM解析{len(mats)}条"
        # 仅「无注入且解析为空」时用 demo；有注入残缺则保留残缺
        if not mats or not _has_metrics_api(mats):
            if existing:
                mats = existing_norm or list(existing)
                source = "inject_partial"
                incomplete_dims = _has_incomplete_dims(mats)
            else:
                mats = _demo_materials()
                source = "demo"
        incomplete_dims = incomplete_dims or _has_incomplete_dims(mats)

    # 应用简单「去掉 xxx」
    if note and ("去掉" in note or "删除" in note):
        mats = _filter_remove(mats, note)

    # 通用表注入：仅当 profile 未指定或为 balanced 时自动套 generic_table
    packing_options = dict(state.get("packing_options") or {})
    profile_applied = ""

    def _profile_is_default(opts: Dict[str, Any]) -> bool:
        pid = str(opts.get("profile_id") or "").strip()
        return pid in ("", "balanced")

    table_src = existing if existing else mats
    looks_table = bool(
        mats
        and (
            _looks_like_generic_table(table_src if table_src else mats)
            or (existing and _looks_like_generic_table(existing))
        )
    )
    if looks_table and _profile_is_default(packing_options):
        try:
            from packing_assistant.packing_profiles import apply_profile

            packing_options = apply_profile(packing_options, "generic_table")
            profile_applied = "generic_table"
        except Exception:
            packing_options.setdefault("crate_passthrough", True)
            packing_options.setdefault("profile_id", "generic_table")
            profile_applied = "generic_table_fallback"

    # 缺重量 / 缺尺寸 / 读不出件数：与 pack-ship 工具面同一套规则，一次问完。此前这条路径上没有
    # 任何一条：上传回执 ok=True，读不出件数的行按 1 件成箱拼柜，ship_ok=True。
    blocking = rows_blocking_plan(mats)
    beyond_dims = [r for r in blocking if r["reason"] != NEEDS_HUMAN_MISSING_DIMENSIONS]

    summary = _summary(mats)
    perception = _build_perception(mats, summary, source=source, note=note)
    summary = {**summary, **{k: perception[k] for k in (
        "categories", "filter_rules", "container_assumption", "longest_mm", "heaviest_unit_kg"
    ) if k in perception}}
    tools_used = ["material_parser.rule_parse" if source == "rule" else f"material_parser.{source}"]
    if source == "llm":
        tools_used.append("llm.chat_json_array")
    if profile_applied:
        tools_used.append("profile.generic_table")
    warn_bits = []
    if incomplete_dims:
        warn_bits.append("缺尺寸(L/W/H=0)不可默成出运")
    if source == "inject_partial":
        warn_bits.append("注入材料字段不完整")
    if beyond_dims:
        warn_bits.append(f"{len(beyond_dims)} 行缺重量或数量读不出件数，需人工处理")
    if profile_applied:
        warn_bits.append(f"已套 profile={profile_applied}")
    msg = (
        f"【感知】材料摘要({source}{llm_note})："
        f"{summary.get('total_pieces')} 件 / {summary.get('total_weight_kg')} kg / "
        f"{summary.get('material_line_count')} 行；"
        f"分类 {perception.get('categories')}；"
        f"过滤={perception.get('filter_rules')}；"
        f"柜型假设={perception.get('container_assumption')}；"
        f"最长={perception.get('longest_mm')}mm 最重单件={perception.get('heaviest_unit_kg')}kg"
        f"{('；警告=' + ';'.join(warn_bits)) if warn_bits else ''}"
        f"｜tools={','.join(tools_used)}"
    )
    out: Dict[str, Any] = {
        "materials": mats,
        "materials_summary": summary,
        "perception": perception,
        "phase": "team_a_running",
        "materials_incomplete": bool(incomplete_dims or blocking),
        "needs_human": blocking,
        "agent_meta": {
            "node": "material_parser",
            "capability": ["感知环境"],
            "tools_used": tools_used,
            "artifacts": {
                "total_pieces": summary.get("total_pieces"),
                "total_weight_kg": summary.get("total_weight_kg"),
                "source": source,
                "incomplete_dims": bool(incomplete_dims),
                "profile_applied": profile_applied or None,
            },
        },
        "messages": [{"role": "assistant", "content": msg}],
    }
    if packing_options:
        out["packing_options"] = packing_options
    if incomplete_dims:
        errs = list(state.get("errors") or [])  # type: ignore[arg-type]
        errs.append("materials_missing_dims: 存在 L/W/H 为 0 的物料，禁止当完整方案出运")
        out["errors"] = errs
        out["warnings"] = list(state.get("warnings") or []) + [  # type: ignore[arg-type]
            "缺尺寸物料：需补尺寸或剔除后再成箱"
        ]
        # 硬信号：不可 ship
        out["ship_ok"] = False
    if beyond_dims:
        # errors 是累加字段，只回新增的那一条
        out["errors"] = list(out.get("errors") or []) + [
            "materials_need_human: " + "；".join(needs_human_sentences(beyond_dims, 6))
        ]
        out["warnings"] = list(out.get("warnings") or state.get("warnings") or []) + [  # type: ignore[arg-type]
            "有行缺重量或数量读不出件数：需人工补齐或剔除后再成箱"
        ]
        out["ship_ok"] = False
    return out


def _normalize_llm_materials(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for i, m in enumerate(items, 1):
        if not isinstance(m, dict):
            continue
        # 读不出件数的格不在这里改写成 1 件：此前 int(float(x or 1)) 把 0 和已标记的行读成 1，
        # 2.7 读成 2，-3 经 max(…, 1) 读成 1，"abc" / NaN 直接抛异常。
        # 判定只有一处（pack_ship_solve.rows_invalid_quantity）；读得出的格读法不变。
        # `qty` 键仍然不读（只写 qty: 5 的行照旧按 1 件）：引擎适配器自 #49 起认它，这里一旦也认，
        # fan-out 喂的就是真实件数——CI 抽样实测 2+7 箱 / 10 s → 24+37 箱 / 96 s，整份 fan-out
        # 重跑的口径随之改变。那是另一个需要单独拍板的改动，不夹带在这里。
        qty_unreadable = bool(rows_invalid_quantity([m]))
        qty = 0 if qty_unreadable else int(float(m.get("quantity") or m.get("数量") or 1))
        unit = float(m.get("weight_kg") or m.get("单重_kg") or 0)
        total = float(m.get("total_weight_kg") or unit * qty)
        L = float(m.get("length_mm") or (m.get("外尺寸_mm") or {}).get("长") or 0)
        W = float(m.get("width_mm") or (m.get("外尺寸_mm") or {}).get("宽") or 0)
        H = float(m.get("height_mm") or (m.get("外尺寸_mm") or {}).get("高") or 0)
        name = str(m.get("name") or m.get("名称") or f"材料-{i}")
        text_blob = " ".join(
            str(x)
            for x in (name, m.get("spec"), m.get("规格"), m.get("note"), m.get("备注"))
            if x
        )
        cat = str(m.get("category") or classify_material(L, unit, total, height_mm=H, width_mm=W, text=text_blob))
        try:
            from packing_assistant.knowledge import MATERIAL_CATEGORIES

            allowed = set(MATERIAL_CATEGORIES)
        except Exception:
            allowed = {"超长件", "重件", "薄板", "异形件", "精密件", "工厂架", "普通件"}
        if cat not in allowed:
            cat = classify_material(L, unit, total, height_mm=H, width_mm=W, text=text_blob)
        row = {
            "id": str(m.get("id") or f"M{i:03d}"),
            "name": name,
            "spec": str(m.get("spec") or m.get("规格") or name),
            "length_mm": L,
            "width_mm": W,
            "height_mm": H,
            "weight_kg": unit,
            "quantity": qty,
            "total_weight_kg": round(total, 3),
            "category": cat,
        }
        # 可选透传字段（非标检验 / HITL / 通用表 meta）
        for k in (
            "note",
            "备注",
            "dims_source",
            "orientation",
            "lift_points",
            "stackable",
            "fragile",
            "this_side_up",
            "no_stack",
            "stacking",
            "upright",
            "handling_requirements",
            "package_type",
            "a_frame",
            "envelope_mm",
            "ns_tags",
            "hazard_class",
            "enrich_source",
            "meta",
            "profile_hint",
            "part_no",
        ):
            if m.get(k) is not None:
                row[k] = m.get(k)
        if qty_unreadable:
            # 与 table_mapper 同一个标记：quantity 留 0，原始格留给人看
            meta = dict(row["meta"]) if isinstance(row.get("meta"), dict) else {}
            written = next((m.get(k) for k in ("quantity", "数量", "qty") if m.get(k) not in (None, "")), None)
            meta.setdefault("quantity_raw", str(written))
            meta["quantity_invalid"] = True
            row["meta"] = meta
        out.append(row)
    return out


def _is_adjust_only(text: str) -> bool:
    return any(k in text for k in ("去掉", "删除", "不要", "单独", "合箱", "改"))


def _looks_like_list(text: str) -> bool:
    t = (text or "").strip()
    if t.startswith("["):
        return True
    if re.search(r"\d+\s*(件|个|根|套|kg)", t, re.I):
        return True
    if re.search(r"\d+[x×]\d+", t):
        return True
    return False


def _looks_like_generic_table(materials: List[Dict[str, Any]]) -> bool:
    """材料带 table_mapper meta 或 profile_hint=generic_table。"""
    n_hint = 0
    for m in materials:
        if not isinstance(m, dict):
            continue
        meta = m.get("meta") if isinstance(m.get("meta"), dict) else {}
        hint = str(meta.get("profile_hint") or m.get("profile_hint") or "")
        src = str(meta.get("source") or "")
        if hint == "generic_table" or src in ("csv", "xlsx", "json", "upload", "api_json_rows", "api_rows"):
            n_hint += 1
        if meta.get("column_map"):
            n_hint += 1
    return n_hint >= max(1, len(materials) // 3)


def _has_metrics_api(materials: List[Dict[str, Any]]) -> bool:
    for m in materials:
        if float(m.get("weight_kg") or m.get("单重_kg") or 0) > 0:
            return True
        if float(m.get("length_mm") or (m.get("外尺寸_mm") or {}).get("长") or 0) > 0:
            return True
    return False


def _has_incomplete_dims(materials: List[Dict[str, Any]]) -> bool:
    """任一行缺有效三维 → 不可当完整装箱输入。"""
    if not materials:
        return False
    for m in materials:
        L = float(m.get("length_mm") or (m.get("外尺寸_mm") or {}).get("长") or 0)
        W = float(m.get("width_mm") or (m.get("外尺寸_mm") or {}).get("宽") or 0)
        H = float(m.get("height_mm") or (m.get("外尺寸_mm") or {}).get("高") or 0)
        if L <= 1e-6 or W <= 1e-6 or H <= 1e-6:
            return True
    return False


def _summary(materials: List[Dict[str, Any]]) -> Dict[str, Any]:
    pieces = 0
    weight = 0.0
    for m in materials:
        unknown = isinstance(m.get("meta"), dict) and m["meta"].get("quantity_invalid")
        q = 0 if unknown else int(m.get("quantity") or m.get("数量") or 1)  # 读不出件数的行不计 1 件
        pieces += q
        weight += float(m.get("total_weight_kg") or m.get("总重_kg") or float(m.get("weight_kg") or 0) * q)
    return {
        "total_pieces": pieces,
        "total_weight_kg": round(weight, 2),
        "material_line_count": len(materials),
    }


def _build_perception(
    materials: List[Dict[str, Any]],
    summary: Dict[str, Any],
    *,
    source: str,
    note: str = "",
) -> Dict[str, Any]:
    """跑前状态摘要：件数、总重、分类、过滤规则、柜型假设。"""
    cats: Dict[str, int] = {}
    longest = 0.0
    heaviest = 0.0
    for m in materials:
        cat = str(m.get("category") or "普通件")
        q = int(m.get("quantity") or m.get("数量") or 1)
        cats[cat] = cats.get(cat, 0) + q
        L = float(m.get("length_mm") or (m.get("外尺寸_mm") or {}).get("长") or 0)
        unit = float(m.get("weight_kg") or m.get("单重_kg") or 0)
        longest = max(longest, L)
        heaviest = max(heaviest, unit)
    filter_rules = [
        "length_mm>=4000 → 超长件",
        "单重>=200kg → 重件",
        "其余 → 普通件",
    ]
    if note and ("去掉" in note or "删除" in note):
        filter_rules.append(f"调整指令过滤: {note[:80]}")
    # 柜型假设：超长倾向 40HQ/45；重货注意 PAYLOAD
    if longest >= 12000:
        ctn_assume = "45HQ 或开顶/框架柜（超长>12m 需复核）"
    elif longest >= 5800 or heaviest >= 200:
        ctn_assume = "40HQ（默认；超长/重件需底层与绑扎）"
    else:
        ctn_assume = "40HQ 或 40GP（主控将按重量/体积再推荐）"
    return {
        "total_pieces": summary.get("total_pieces"),
        "total_weight_kg": summary.get("total_weight_kg"),
        "material_line_count": summary.get("material_line_count"),
        "categories": cats,
        "filter_rules": filter_rules,
        "container_assumption": ctn_assume,
        "longest_mm": round(longest, 1),
        "heaviest_unit_kg": round(heaviest, 2),
        "source": source,
        "summary_text": (
            f"{summary.get('total_pieces')}件 / {summary.get('total_weight_kg')}kg / "
            f"{summary.get('material_line_count')}行 | 分类{cats} | 柜型假设={ctn_assume}"
        ),
    }


def _demo_materials() -> List[Dict[str, Any]]:
    """默认演示：高利用率密实模块（避免「空柜感」）。

    钢件轻量叙事请用 preset=steel_light 或文案含「钢件轻量」。
    """
    try:
        from packing_assistant.demo_presets import materials_high_util

        return materials_high_util()
    except Exception:
        from packing_assistant.adapters import material_internal_to_api

        raw = [
            {
                "名称": "H型钢柱",
                "规格": "H400×200",
                "数量": 4,
                "单重_kg": 85,
                "外尺寸_mm": {"长": 3800, "宽": 400, "高": 200},
            },
            {
                "名称": "钢梁",
                "规格": "H350×175",
                "数量": 6,
                "单重_kg": 55,
                "外尺寸_mm": {"长": 4200, "宽": 350, "高": 175},
            },
        ]
        return [material_internal_to_api(m, i) for i, m in enumerate(raw, 1)]


def _rule_parse(raw: str) -> List[Dict[str, Any]]:
    if not raw:
        return []
    raw = raw.strip()
    if raw.startswith("["):
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                out = []
                for i, item in enumerate(data, 1):
                    if "name" in item or "length_mm" in item:
                        item = dict(item)
                        item.setdefault("id", f"M{i:03d}")
                        item.setdefault(
                            "category",
                            classify_material(
                                float(item.get("length_mm") or 0),
                                float(item.get("weight_kg") or 0),
                                float(item.get("total_weight_kg") or 0),
                            ),
                        )
                        out.append(item)
                    else:
                        out.append(material_internal_to_api(item, i))
                return out
        except json.JSONDecodeError:
            pass

    materials: List[Dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(
            r"(.+?)\s+(\d+)\s*(?:件|个|根|套)?\s*([\d.]+)?\s*kg?"
            r"(?:\s+([\d.]+)[x×]([\d.]+)[x×]([\d.]+))?",
            line,
            re.I,
        )
        if m:
            name = m.group(1).strip()
            qty = int(m.group(2))
            weight = float(m.group(3) or 0)
            L, W, H = m.group(4), m.group(5), m.group(6)
            total = qty * weight
            materials.append(
                {
                    "id": f"M{len(materials)+1:03d}",
                    "name": name,
                    "spec": name,
                    "length_mm": float(L or 0),
                    "width_mm": float(W or 0),
                    "height_mm": float(H or 0),
                    "weight_kg": weight,
                    "quantity": qty,
                    "total_weight_kg": total,
                    "category": classify_material(float(L or 0), weight, total),
                }
            )
        else:
            materials.append(
                {
                    "id": f"M{len(materials)+1:03d}",
                    "name": line,
                    "spec": line,
                    "length_mm": 0,
                    "width_mm": 0,
                    "height_mm": 0,
                    "weight_kg": 0,
                    "quantity": 1,
                    "total_weight_kg": 0,
                    "category": "普通件",
                }
            )
    return materials


def _filter_remove(materials: List[Dict[str, Any]], note: str) -> List[Dict[str, Any]]:
    m = re.search(r"(?:去掉|删除)\s*([^\s,，。；;]+)", note)
    if not m:
        return materials
    key = m.group(1)
    filtered = [
        x
        for x in materials
        if key not in str(x.get("name", ""))
        and key not in str(x.get("id", ""))
        and key not in str(x.get("spec", ""))
    ]
    return filtered if filtered else materials
