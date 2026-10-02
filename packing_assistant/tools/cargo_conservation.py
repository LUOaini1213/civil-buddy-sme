"""装进箱里的货 == 装箱单上的货。每次求解之后核对一遍，不看成箱器和拼柜器怎么说。

成箱引擎会拆行、合箱、换箱型、钳外廓；其中任何一步丢了件或丢了重量，后面的柜数、
N0、载重利用率和 VGM 都会建立在少掉的那部分货上，而且看起来完全正常——
can_fit=True、柜数合理、没有告警。实测过的三种丢法：

- 单件超过箱型净重上限的行按质量切成「虚拟半件」时只切了第一件，行上的数量被丢掉
  （2 根 850 kg 的梁出来 1 根；4 行装箱单 6040 kg 只剩 3330 kg）；
- 当量直通把数量为 N 的行出成 1 个箱，另外 N-1 个箱的体积消失；
- 13.5 m 的梁被钳进 12.032 m 的箱，箱进得了柜，货其实进不了箱。

这里只用两样东西：进引擎之前的物料行，和引擎交出来的箱。三条独立的账：

1. 件数：Σ 行数量 == Σ 箱内条目数量 / split_of（质量拆分的一份算 1/split_of 件）；
2. 净重：Σ 行总重 == Σ 箱净重（容差只覆盖逐箱 0.1 kg 的取整）；
3. 几何：箱内任何一件的三边从大到小逐一不超过箱外廓的三边——无论怎么摆，比箱
   还大的货进不了箱。最长边超出记 content_exceeds_box，另外两边（截面）超出记
   content_section_exceeds_box，两种都判失败。截面那一种原先只记 warnings：标准箱
   外宽一律 1100 mm，引擎把 1200 mm 见方的电缆盘照样放进去、只标「尺寸紧张」
   （59 个夹具里 6 个、共 69 条）。成箱器现在对这种件按货定制外廓，剩下还会报的
   只有定制之后仍装不下的——货比柜还大。只比外廓、不算壁厚和间隙：这是必要条件，
   不会误报；货进得了外廓却进不了内腔的那一类不在这本账里。

件数和净重互相独立：引擎把 split_of 标错，净重账会不平；把重量算错，件数账仍然成立。
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from fractions import Fraction
from typing import Any, Dict, List, Sequence

NOT_CONSERVED = "cargo_not_conserved"
PHYSICAL_SPLIT_NOT_AUTHORIZED = "physical_split_not_authorized"

#: 每个箱的净重取整到 0.1 kg，逐箱最多差 0.05；再留一点给拆行时的 0.001 kg 取整。
_KG_PER_BOX = 0.06
_KG_BASE = 0.5
_MM_TOL = 1.0


def physical_split_issues(materials, boxes, *, lang="zh") -> List[Dict[str, Any]]:
    """A conserved fractional piece is not an authorized physical cargo item.

    Keep candidate boxes available for audit, but require a human-reviewed
    material list or packaging design before any layout or shipping draft.
    Check actual content markers so a caller cannot bypass this by omitting the
    conservation summary or by invoking the loader directly.
    """
    sources = {str(row.get("id") or ""): row for row in materials or []}
    splits = {}
    for box in boxes or []:
        for item in box.get("contents") or box.get("content") or []:
            parts = int(item.get("split_of") or 1)
            if parts <= 1:
                continue
            source_id = str(item.get("source_material_id") or item.get("material_id") or "")
            record = splits.setdefault(source_id, {"id": source_id, "parts_per_unit": parts})
            record["parts_per_unit"] = max(record["parts_per_unit"], parts)
    ask = ("成箱器将一个原始实物按质量拆成了虚拟份额，但原件未授权切割或拆解。请保留原物料，"
           "由人工确认适用的包装设计；若实物已经拆分，请提供人工核对后的实际分件尺寸、重量和数量。")
    if lang == "en":
        ask = ("Automatic boxing would split an individual source piece into virtual mass parts. "
               "This source does not authorize cutting or dismantling. Preserve the original piece and obtain "
               "a suitable packaging design, or provide a human-reviewed source with the actual separate pieces "
               "and their dimensions, weights and quantities.")
    return [{**record, "name": str(sources.get(source_id, {}).get("name") or ""),
             "reason": PHYSICAL_SPLIT_NOT_AUTHORIZED,
             "source_material": deepcopy(sources.get(source_id, {})), "ask": ask}
            for source_id, record in sorted(splits.items())]


def _row_quantity(m: Dict[str, Any]) -> int:
    from packing_assistant.adapters import material_quantity

    return max(material_quantity(m), 1)


def _row_kg(m: Dict[str, Any], qty: int) -> float:
    """与 adapters.material_api_to_internal 同口径：总重为正用总重，否则 单重 × 数量。"""
    total = float(m.get("total_weight_kg") or m.get("总重_kg") or 0)
    if total > 0:
        return total
    return float(m.get("weight_kg") or m.get("单重_kg") or 0) * qty


def _sorted_dims(d: Dict[str, Any]) -> List[float]:
    return sorted(
        (float(d.get(k) or d.get(cn) or 0) for k, cn in (("length", "长"), ("width", "宽"), ("height", "高"))),
        reverse=True,
    )


def check_conservation(
    materials: Sequence[Dict[str, Any]],
    boxes: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """物料行 vs 成箱结果。返回 ok 与逐条违例；不抛异常，读不了的行本身就是一条违例。"""
    violations: List[Dict[str, Any]] = []

    pieces_in = 0
    kg_in = 0.0
    row_pieces_in: Dict[str, int] = defaultdict(int)
    row_kg_in: Dict[str, float] = defaultdict(float)
    row_names: Dict[str, str] = {}
    every_row_has_id = bool(materials)
    for m in materials or []:
        try:
            qty = _row_quantity(m)
            kg = _row_kg(m, qty)
        except (TypeError, ValueError, OverflowError) as exc:
            violations.append({"kind": "unreadable_row", "id": str(m.get("id") or ""),
                               "name": str(m.get("name") or ""), "detail": f"{type(exc).__name__}: {exc}"})
            every_row_has_id = False
            continue
        pieces_in += qty
        kg_in += kg
        rid = str(m.get("id") or "")
        if not rid:
            every_row_has_id = False
            continue
        row_pieces_in[rid] += qty
        row_kg_in[rid] += kg
        row_names.setdefault(rid, str(m.get("name") or ""))

    pieces_out = Fraction(0)
    kg_out = 0.0
    row_pieces_out: Dict[str, Fraction] = defaultdict(Fraction)
    row_kg_out: Dict[str, float] = defaultdict(float)
    every_line_attributed = True
    every_line_weighed = True
    split_units: Dict[str, Dict[str, Any]] = {}
    for b in boxes or []:
        kg_out += float(b.get("net_weight_kg") or 0)
        outer = _sorted_dims(b.get("outer_size_mm") or {})
        for c in b.get("contents") or b.get("content") or []:
            qty = int(c.get("quantity") or 1)
            split_of = max(int(c.get("split_of") or 1), 1)
            share = Fraction(qty, split_of)
            pieces_out += share
            src = str(c.get("source_material_id") or "")
            if src and src in row_pieces_in:
                row_pieces_out[src] += share
                if c.get("weight_kg") is None:
                    every_line_weighed = False
                else:
                    row_kg_out[src] += float(c.get("weight_kg") or 0)
                if split_of > 1:
                    rec = split_units.setdefault(src, {"id": src, "name": row_names.get(src, ""),
                                                       "parts_per_unit": split_of, "parts": 0})
                    rec["parts"] += qty
            else:
                every_line_attributed = False
            # 当量直通的条目尺寸是估出来的占位（恒小于外廓），照样核对，不特判
            dims = _sorted_dims(c.get("outer_size_mm") or {})
            if any(dims) and any(outer) and any(cd > od + _MM_TOL for cd, od in zip(dims, outer)):
                too_long = dims[0] > outer[0] + _MM_TOL
                violations.append({
                    "kind": "content_exceeds_box" if too_long else "content_section_exceeds_box",
                    "box_id": str(b.get("box_id") or ""),
                    "id": src or str(c.get("material_id") or ""),
                    "name": str(c.get("name") or ""),
                    "content_mm": dims,
                    "box_outer_mm": outer,
                })

    n_boxes = len(boxes or [])
    kg_tol = _KG_BASE + _KG_PER_BOX * n_boxes
    if pieces_out != pieces_in:
        violations.append({"kind": "pieces_lost" if pieces_out < pieces_in else "pieces_gained",
                           "pieces_in": pieces_in, "pieces_out": float(pieces_out)})
    if abs(kg_out - kg_in) > kg_tol:
        violations.append({"kind": "kg_lost" if kg_out < kg_in else "kg_gained",
                           "kg_in": round(kg_in, 3), "kg_out": round(kg_out, 3), "tolerance_kg": round(kg_tol, 3)})

    per_row = every_row_has_id and every_line_attributed
    if per_row:
        for rid, qty in row_pieces_in.items():
            got = row_pieces_out.get(rid, Fraction(0))
            if got != qty:
                violations.append({"kind": "row_pieces", "id": rid, "name": row_names.get(rid, ""),
                                   "pieces_in": qty, "pieces_out": float(got)})
            elif every_line_weighed and abs(row_kg_out.get(rid, 0.0) - row_kg_in[rid]) > _KG_BASE:
                violations.append({"kind": "row_kg", "id": rid, "name": row_names.get(rid, ""),
                                   "kg_in": round(row_kg_in[rid], 3), "kg_out": round(row_kg_out.get(rid, 0.0), 3)})

    for rec in split_units.values():
        rec["units"] = rec["parts"] // rec["parts_per_unit"]
    return {
        "ok": not violations,
        "pieces_in": pieces_in,
        "pieces_out": int(pieces_out) if pieces_out.denominator == 1 else round(float(pieces_out), 4),
        "kg_in": round(kg_in, 3),
        "kg_out": round(kg_out, 3),
        "kg_tolerance": round(kg_tol, 3),
        "n_boxes": n_boxes,
        "per_row_checked": per_row,
        "mass_split_rows": sorted(split_units.values(), key=lambda r: r["id"]),
        "violations": violations,
    }


def violation_sentences(result: Dict[str, Any], limit: int = 8) -> List[str]:
    """违例写成人能核对的句子：数字只抄，不下结论。"""
    out: List[str] = []
    for v in (result.get("violations") or [])[:limit]:
        kind = v.get("kind")
        label = v.get("name") or v.get("id") or "（未命名行）"
        if kind in ("pieces_lost", "pieces_gained"):
            out.append(f"件数对不上：装箱单 {v['pieces_in']} 件，成箱结果 {v['pieces_out']:g} 件。")
        elif kind in ("kg_lost", "kg_gained"):
            out.append(f"净重对不上：装箱单 {v['kg_in']:g} kg，成箱结果 {v['kg_out']:g} kg（容差 {v['tolerance_kg']:g} kg）。")
        elif kind == "row_pieces":
            out.append(f"{label}：装箱单 {v['pieces_in']} 件，箱里 {v['pieces_out']:g} 件。")
        elif kind == "row_kg":
            out.append(f"{label}：装箱单 {v['kg_in']:g} kg，箱里 {v['kg_out']:g} kg。")
        elif kind in ("content_exceeds_box", "content_section_exceeds_box"):
            c = "×".join(f"{x:g}" for x in v["content_mm"])
            o = "×".join(f"{x:g}" for x in v["box_outer_mm"])
            where = "货" if kind == "content_exceeds_box" else "货的截面"
            out.append(f"{label}：{where} {c} mm 大于所在箱 {v.get('box_id') or ''} 的外廓 {o} mm，箱进得了柜、货进不了箱。")
        elif kind == "unreadable_row":
            out.append(f"{label}：数量或重量读不出来（{v.get('detail')}）。")
    more = len(result.get("violations") or []) - len(out)
    if more > 0:
        out.append(f"另有 {more} 条。")
    return out
