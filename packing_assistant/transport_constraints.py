"""Explicit transport requirements. No inferred frame sizes or load capacities.

The packaged-ledger lane can check a declared envelope, kept at its stated
orientation and on the floor. Automatic material packaging cannot establish that
an upright or specially supported item stays that way inside a generated box.
"""
from __future__ import annotations

import math
import re

UNKNOWN = "UNSPECIFIED"
CHOICES = {"orientation": {UNKNOWN, "fixed", "upright", "free"},
           "stacking": {UNKNOWN, "no_stack", "allowed"}}
FRAME = re.compile(r"(?<![a-z])a[\s_-]?frames?(?![a-z])|stillages?|运输架|周转架|A\s*(?:型)?架", re.I)
# Scan mixed remarks only for explicit handling instructions. A general note
# such as "tip sheet enclosed" is not itself a transport constraint.
RAW_HANDLING = re.compile(
    r"upright|no[ _-]?stack|do not stack|this side up|"
    r"\b(?:do\s+not|don['’]t|never)\s+(?:tip|tilt|turn\s+over|invert|rotate)\b|"
    r"\bkeep\s+(?:this|the)\s+(?:face|side)\s+up(?:wards?)?\b|\bno\s+rotation\b|"
    r"竖放|直立|禁止堆叠|不得堆码|禁止叠放|(?:禁止|不得|不可|请勿)(?:倾斜|倒置|旋转)|此面向上",
    re.I,
)


def _key(value):
    return re.sub(r"[\s_-]+", " ", str(value).strip().lower())


def normalize(field, value, header=""):
    """Read only explicit values; header polarity matters for boolean columns."""
    text, heading = _key(value), _key(header)
    if text in {"", "unspecified", "unknown", "待确认", "未知"}:
        return UNKNOWN, ""
    yes, no = {"true", "yes", "1", "是"}, {"false", "no", "0", "否"}
    if field == "orientation":
        if heading in {"upright", "this side up", "保持直立", "此面向上", "竖放"}:
            if text in yes:
                return "upright", ""
            if text in no:
                return "free", ""
        values = {"fixed": "fixed", "as stated": "fixed", "no rotation": "fixed", "禁止旋转": "fixed", "固定": "fixed",
                  "upright": "upright", "vertical": "upright", "this side up": "upright", "keep upright": "upright",
                  "立放": "upright", "竖放": "upright", "直立": "upright", "此面向上": "upright",
                  "free": "free", "any": "free", "any orientation": "free", "可旋转": "free", "不限": "free"}
    else:
        if heading in {"no stack", "no stacking", "禁止堆叠", "禁止叠放", "不可堆叠"}:
            if text in yes:
                return "no_stack", ""
            if text in no:
                return "allowed", ""
        if heading in {"stackable", "可堆叠", "允许堆叠"}:
            if text in yes:
                return "allowed", ""
            if text in no:
                return "no_stack", ""
        values = {"no stack": "no_stack", "no stacking": "no_stack", "do not stack": "no_stack", "不可堆叠": "no_stack",
                  "禁止堆叠": "no_stack", "禁止叠放": "no_stack", "不得堆码": "no_stack", "allowed": "allowed",
                  "stackable": "allowed", "可堆叠": "allowed", "允许堆叠": "allowed"}
    if text in values:
        return values[text], ""
    return UNKNOWN, "运输要求取值无法明确识别，请核对原文并选择明确的运输姿态或堆叠要求。"


def text_requirements(raw):
    """A closed grammar, never a keyword hit that discards the rest of a sentence."""
    if raw in (None, "", UNKNOWN) or _key(raw) in {"none", "no special handling", "无特殊要求"}:
        return {}, []
    requirements, unsupported = {}, []
    for part in re.split(r"[;；,，\n]+|\s+and\s+", str(raw).strip(), flags=re.I):
        if not part.strip():
            continue
        if FRAME.fullmatch(part.strip()):
            requirements["frame"] = True
            continue
        matched = False
        for field in CHOICES:
            value, reason = normalize(field, part)
            if not reason and value != UNKNOWN:
                if field in requirements and requirements[field] != value:
                    unsupported.append(part)
                else:
                    requirements[field] = value
                matched = True
                break
        if not matched:
            unsupported.append(part)
    return requirements, unsupported


def requirements(row):
    req, unsupported = text_requirements(row.get("handling_requirements"))
    for alias in ("handling", "handling_instructions", "transport_requirements", "shipping_instructions"):
        if row.get(alias) not in (None, "", UNKNOWN):
            extra, unknown = text_requirements(row[alias])
            unsupported.extend(unknown)
            for field, value in extra.items():
                if field in req and req[field] != value:
                    unsupported.append(alias + "=" + str(row[alias]))
                else:
                    req[field] = value
    for field in CHOICES:
        value = row.get(field, UNKNOWN)
        if value != UNKNOWN:
            if value not in CHOICES[field] or field in req and req[field] != value:
                unsupported.append(field + "=" + str(value))
            else:
                req[field] = value
    # Old JSON ledgers may contain these extra fields. They remain source data,
    # but may not bypass the same constraints as explicitly mapped table cells.
    for alias, field in (("upright", "orientation"), ("this_side_up", "orientation"), ("no_stack", "stacking"), ("stackable", "stacking")):
        if row.get(alias) not in (None, "", UNKNOWN):
            value, reason = normalize(field, row[alias], alias)
            if reason or field in req and req[field] != value:
                unsupported.append(alias + "=" + str(row[alias]))
            elif value != UNKNOWN:
                req[field] = value
    req["frame"] = bool(req.get("frame") or FRAME.search(str(row.get("package_type") or "")))
    if row.get("a_frame") not in (None, "", UNKNOWN):
        if _key(row["a_frame"]) in {"true", "yes", "1", "是"}:
            req["frame"] = True
        elif _key(row["a_frame"]) not in {"false", "no", "0", "否"}:
            unsupported.append("a_frame=" + str(row["a_frame"]))
    if row.get("fragile") not in (None, "", UNKNOWN) and _key(row["fragile"]) not in {"false", "no", "0", "否"}:
        unsupported.append("fragile=" + str(row["fragile"]))
    return req, unsupported


def ledger_issues(row, mode=None):
    """Return actionable, row-scoped blockers, independently of UI or model."""
    req, unsupported = requirements(row)
    result = []
    def add(code, field, message):
        result.append({"code": code, "severity": "error", "row_id": row["id"], "field": field, "message": message})
    if unsupported:
        add("unsupported_handling", "handling_requirements", "运输要求存在未支持或冲突内容：" + "；".join(unsupported)
            + "。请保留原文，由物流人员明确已包装外廓与可计算约束；不能直接按普通货物计算。")
    if mode == "materials" and (req.get("frame") or req.get("orientation") in {"fixed", "upright"} or req.get("stacking") == "no_stack"):
        add("material_handling_not_modelled", "handling_requirements", "自动成箱尚不能保证构件直立、禁止堆叠或 A 架要求。请提供已包装整体外尺寸、毛重和包装数，改用已包装箱件模式。")
    if req.get("frame"):
        missing = [f for f in ("net_kg", "tare_kg", "gross_kg", "capacity_kg")
                   if type(row.get(f)) not in (int, float) or not math.isfinite(row[f]) or row[f] <= 0
                   or row.get("evidence", {}).get(f, {}).get("group")]
        if row.get("dimension_scope") != "package" or row.get("weight_scope") != "package":
            missing += ["dimension_scope=package", "weight_scope=package"]
        if missing:
            add("frame_data_missing", "package_type", "A 架/运输架需提供每架含货整体外尺寸、货物净重、架体皮重、整体毛重和声明载荷上限；缺少：" + "、".join(missing) + "。不推算架体参数。")
        else:
            if row["net_kg"] > row["capacity_kg"]:
                add("frame_capacity_exceeded", "capacity_kg", "每架货物净重超过声明载荷上限，请核对架体资料或重新分配货物。")
            if not math.isclose(row["net_kg"] + row["tare_kg"], row["gross_kg"], rel_tol=1e-8, abs_tol=0.01):
                add("frame_mass_mismatch", "gross_kg", "每架净重加架体皮重与整体毛重不一致，请按同一包装范围核对重量。")
    return result


def legacy_handling(row):
    """Find restrictive/unknown explicit fields before legacy automatic boxing.

    Keep original values in the returned map for the human-facing refusal. These
    fields must never disappear into the legacy dimensional stacking heuristic.
    """
    found = {}
    # Inspect every mapped original cell too: two synonymous columns may have
    # conflicting values and a later cell must not erase an earlier restriction.
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    for cell in meta.get("handling_source", []) if isinstance(meta.get("handling_source", []), list) else []:
        if not isinstance(cell, dict):
            continue
        field = cell.get("field")
        if field and cell.get("raw") not in (None, "", UNKNOWN):
            original = legacy_handling({field: cell["raw"]})
            if original:
                found["source:" + str(cell.get("header", field))] = cell["raw"]
    for field in ("orientation", "stacking", "no_stack", "stackable", "this_side_up", "upright", "fragile", "a_frame", "handling_requirements"):
        value = row.get(field)
        if value in (None, "", UNKNOWN):
            continue
        if field in {"orientation", "stacking"}:
            canonical, reason = normalize(field, value)
            if reason or canonical not in {UNKNOWN, "free", "allowed"}:
                found[field] = value
        elif field in {"no_stack", "stackable", "this_side_up", "upright"}:
            target = "stacking" if field in {"no_stack", "stackable"} else "orientation"
            canonical, reason = normalize(target, value, field)
            if reason or canonical not in {UNKNOWN, "free", "allowed"}:
                found[field] = value
        elif field in {"fragile", "a_frame"}:
            if _key(value) not in {"false", "no", "0", "否"}:
                found[field] = value
        else:
            found[field] = value
    for field in ("note", "备注", "package_type", "spec"):
        value = str(row.get(field) or "")
        if FRAME.search(value) or RAW_HANDLING.search(value):
            found[field] = row[field]
    return found
