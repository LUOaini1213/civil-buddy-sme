"""Restricted logistics tools: user's explicit corrections become reviewable proposals."""
from __future__ import annotations

from copy import deepcopy
import math
import re

from packing_assistant.runtime.cancel import check
from .records import digest

NUMERIC = {"package_count", "quantity", "units_per_package", "length_mm", "width_mm", "height_mm", "net_kg", "gross_kg"}
TEXT = {"package_id", "container_id", "package_type", "material_id", "name", "spec", "unit", "dimension_scope", "weight_scope"}
ALIASES = {"箱数": "package_count", "包装数": "package_count", "件数": "quantity", "数量": "quantity", "每箱件数": "units_per_package",
           "长": "length_mm", "长度": "length_mm", "宽": "width_mm", "宽度": "width_mm", "高": "height_mm", "高度": "height_mm",
           "净重": "net_kg", "毛重": "gross_kg", "箱号": "package_id", "材料编号": "material_id", "名称": "name", "规格": "spec",
           "单位": "unit", "集装箱号": "container_id", "柜号": "container_id", "包装类型": "package_type",
           "尺寸口径": "dimension_scope", "重量口径": "weight_scope"}
ALIASES.update({
    "package count": "package_count", "box count": "package_count", "quantity": "quantity",
    "items per package": "units_per_package", "units per package": "units_per_package",
    "length": "length_mm", "width": "width_mm", "height": "height_mm",
    "net mass": "net_kg", "net weight": "net_kg", "gross mass": "gross_kg", "gross weight": "gross_kg",
    "package id": "package_id", "box id": "package_id", "container id": "container_id",
    "material id": "material_id", "name": "name", "specification": "spec", "unit": "unit",
    "package type": "package_type", "dimension basis": "dimension_scope", "mass basis": "weight_scope",
})


def _english(context, message):
    locale = (context or {}).get("locale")
    return locale == "en" or locale is None and bool(re.search(r"[A-Za-z]", message)) and not re.search(r"[\u4e00-\u9fff]", message)


def _undo_requested(message):
    return bool(re.search(r"撤销", message) or re.fullmatch(
        r"\s*(?:please\s+)?(?:undo(?:\s+(?:the\s+)?(?:last\s+)?change)?|revert\s+(?:the\s+)?last\s+change)\s*[.!]?\s*",
        message, re.I))


def propose_changes(document, changes, reason):
    from .ledger import validate_document
    doc = validate_document(document)
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 4000:
        raise ValueError("请记录修订的原话或原因。")
    if not isinstance(changes, list) or not 1 <= len(changes) <= 100:
        raise ValueError("每次须提出 1–100 项修订。")
    rows = {r["id"]: r for r in doc["rows"]}
    seen, normalized = set(), []
    for item in changes:
        check()
        if not isinstance(item, dict) or set(item) != {"row_id", "field", "value"}:
            raise ValueError("修订只能包含 row_id、field、value。")
        ident, field, value = item["row_id"], item["field"], item["value"]
        if not isinstance(ident, str) or ident not in rows or not isinstance(field, str) or field not in NUMERIC | TEXT:
            raise ValueError("只能修改已有台账行的受限字段。")
        if (ident, field) in seen:
            raise ValueError("同一字段不能重复修订。")
        if rows[ident].get("evidence", {}).get(field, {}).get("group"):
            raise ValueError("该字段属于原图跨行合并单元格，不能作为单行修改；请核对共享范围并提供拆分后的原件。")
        seen.add((ident, field))
        if value != "UNSPECIFIED":
            if field in NUMERIC and (type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 1e12):
                raise ValueError("数值须为明确的正有限数，未知请用 UNSPECIFIED。")
            if field in {"package_count", "quantity", "units_per_package"} and (type(value) not in (int, float) or int(value) != value):
                raise ValueError("箱数与件数必须是正整数。")
            if field in TEXT and (not isinstance(value, str) or not value.strip() or len(value) > 500):
                raise ValueError("文本修订须为 1–500 字。")
            if field == "dimension_scope" and value not in {"package", "item"}:
                raise ValueError("尺寸口径仅支持 package / item。")
            if field == "weight_scope" and value not in {"package", "item", "row"}:
                raise ValueError("重量口径仅支持 package / item / row。")
        before = rows[ident].get(field, "UNSPECIFIED")
        if before == value:
            continue
        normalized.append({"row_id": ident, "field": field, "before": before, "after": value})
        rows[ident][field] = value
        evidence = rows[ident].setdefault("evidence", {}).setdefault(field, {"raw": "UNSPECIFIED", "source": {}})
        evidence.setdefault("corrections", []).append({"before": before, "after": value, "reason": reason})
    if not normalized:
        raise ValueError("所填值与台账一致，没有待应用的修订。")
    doc = validate_document(doc)
    return {"schema": "civil.logistics.proposal.v1", "base_digest": digest(document), "source_text": reason,
            "changes": normalized, "document": doc}


def propose_command(document, message):
    from .ledger import validate_document
    if not isinstance(message, str) or not 1 <= len(message) <= 4000:
        raise ValueError("修订指令长度无效。")
    rows = {row["id"]: row for row in validate_document(document)["rows"]}

    def quantity_unit(value):
        unit = str(value).strip().lower()
        for normalized, aliases in {
            "pcs": {"pc", "pcs", "ea", "piece", "pieces", "件", "个"},
            "m": {"m", "米"}, "cm": {"cm", "厘米"}, "mm": {"mm", "毫米"},
            "kg": {"kg", "千克", "公斤"}, "g": {"g", "克"}, "t": {"t", "吨"},
            "roll": {"roll", "rolls", "卷"}, "set": {"set", "sets", "套"},
        }.items():
            if unit in aliases:
                return normalized
        return unit

    commands = [s.strip() for s in re.split(r"[;；\n]", message) if s.strip()]
    changes, explicit_quantity_units = [], []
    fields = "|".join(re.escape(f) for f in sorted([*ALIASES, *NUMERIC, *TEXT], key=len, reverse=True))
    for command in commands:
        match = re.fullmatch(r"(?:把|将|(?:change|set|update)\s+)?\s*([A-Za-z][A-Za-z0-9_-]{0,63})\s*(?:的)?\s*(" + fields + r")\s*(?:改为|设为|设置为|to\b|=|：|:)\s*(.+?)\s*[。.]?", command, re.I)
        # Both English forms require an explicit existing row, field and value.
        reverse = None if match else re.fullmatch(r"(?:change|set|update)\s+(?:the\s+)?(" + fields + r")\s+(?:of|for|in)\s+(?:row\s+)?([A-Za-z][A-Za-z0-9_-]{0,63})\s+(?:to|=)\s*(.+?)\s*[.]?", command, re.I)
        if reverse:
            field, ident, raw = reverse.groups()
        elif match:
            ident, field, raw = match.groups()
        if not match:
            if not reverse:
                raise ValueError("请明确已有行号、字段和新值，例如：把 R00001 毛重改为 120 kg；不会猜测数据。")
        field = ALIASES.get(field.lower(), field.lower())
        value = raw.strip()
        if value.lower() in {"未知", "未指定", "unspecified", "unknown"}:
            value = "UNSPECIFIED"
        elif field in NUMERIC:
            number = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+|毫米|厘米|米|千克|公斤|克|吨|件|个|箱|卷|套)?", value, re.I)
            if not number:
                raise ValueError("数字或单位不明确，未提出修订。")
            value, unit = float(number[1]), (number[2] or "").lower()
            if field.endswith("_mm"):
                if unit not in {"", "mm", "cm", "m", "毫米", "厘米", "米"}:
                    raise ValueError("尺寸须使用 mm、cm 或 m。")
                value *= {"cm": 10, "厘米": 10, "m": 1000, "米": 1000}.get(unit, 1)
            elif field.endswith("_kg"):
                if unit not in {"", "kg", "g", "t", "千克", "公斤", "克", "吨"}:
                    raise ValueError("重量须使用 kg、g 或 t。")
                value *= {"g": .001, "克": .001, "t": 1000, "吨": 1000}.get(unit, 1)
            elif field == "package_count":
                if unit not in {"", "箱", "box", "boxes", "package", "packages"}:
                    raise ValueError("包装数使用箱；不混用箱与货物数量单位。")
            elif unit:
                current_unit = rows.get(ident, {}).get("unit", "UNSPECIFIED")
                if current_unit == "UNSPECIFIED" or not str(current_unit).strip():
                    raise ValueError("原数量单位未明确，请先核对并明确单位，再修改数量；不会自动填入单位。")
                if quantity_unit(unit) != quantity_unit(current_unit):
                    raise ValueError(f"指令数量单位 {unit} 与原单位 {current_unit} 不一致，请先核对并确认单位；未修改数量或换算单位。")
                explicit_quantity_units.append((ident, unit))
        elif field in {"dimension_scope", "weight_scope"}:
            value = {"每箱": "package", "包装": "package", "单件": "item", "每件": "item", "整行": "row",
                     "per package": "package", "per box": "package", "per item": "item", "per row": "row"}.get(value.lower(), value)
        changes.append({"row_id": ident, "field": field, "value": value})
    revised_units = {change["row_id"]: change["value"] for change in changes if change["field"] == "unit"}
    for ident, unit in explicit_quantity_units:
        if ident in revised_units and quantity_unit(revised_units[ident]) != quantity_unit(unit):
            raise ValueError("同一提案中的数量单位与单位修订不一致，请分别核对单位和数量；未应用修改。")
    proposal = propose_changes(document, changes, message)
    proposal["command"] = True
    return proposal


def apply_proposal(document, proposal):
    if not isinstance(proposal, dict) or proposal.get("base_digest") != digest(document):
        raise ValueError("建议属于旧台账，请重新提出。")
    if proposal.get("command"):
        expected = propose_command(document, proposal["source_text"])
    else:
        expected = propose_changes(document, [{"row_id": c["row_id"], "field": c["field"], "value": c["after"]} for c in proposal["changes"]], proposal["source_text"])
    if expected != proposal:
        raise ValueError("建议内容被修改，未应用。")
    check()
    return deepcopy(expected["document"])


def compare_documents(document, other):
    """Only explicit unique material IDs join; no fuzzy name/spec matching."""
    from .ledger import validate_document
    left, right = validate_document(document), validate_document(other)
    def index(doc):
        values, duplicates, unmatched = {}, set(), []
        for row in doc["rows"]:
            key = row.get("material_id", "UNSPECIFIED")
            if key == "UNSPECIFIED":
                unmatched.append(row["id"])
            elif key in values:
                duplicates.add(key)
            else:
                values[key] = row
        return values, duplicates, unmatched
    a, ad, au = index(left)
    b, bd, bu = index(right)
    rows = []
    for key in sorted(set(a) | set(b)):
        x, y = a.get(key), b.get(key)
        if key in ad | bd:
            rows.append({"material_id": key, "status": "ambiguous_duplicate"})
        elif x is None or y is None:
            rows.append({"material_id": key, "status": "only_current" if y is None else "only_comparison"})
        else:
            fields = {f: {"current": x.get(f, "UNSPECIFIED"), "comparison": y.get(f, "UNSPECIFIED")} for f in ("name", "spec", "unit", "quantity", "package_count") if x.get(f) != y.get(f)}
            rows.append({"material_id": key, "current_row": x["id"], "comparison_row": y["id"], "status": "different" if fields else "same", "differences": fields})
    return {"rows": rows, "unmatched_current": au, "unmatched_comparison": bu,
            "note": "仅按明确且唯一的材料编号对照原值；不按名称猜匹配，不推断采购单价。"}


def operation(message, context=None):
    if _undo_requested(message):
        return "logistics_undo"
    if re.search(r"改为|设为|设置为|=|\b(?:change|set|update)\b.*\bto\b", message, re.I):
        return "logistics_propose"
    if re.search(r"缺|检查|核对|审计|\b(?:check|audit|review|missing|issues?|errors?|anomalies)\b", message, re.I):
        return "logistics_audit"
    if re.search(r"汇总|总计|多少|\b(?:summari[sz]e|summary|totals?|how many|how much)\b", message, re.I):
        return "logistics_summarize"
    return "logistics_inspect"


def execute(context, name, args, user_text):
    from .ledger import validate_document, audit_document, summarize, FIELD_LABELS
    english = _english(context, user_text)
    choose = lambda zh, en: en if english else zh
    label = lambda field: str(field or "check").replace("_", " ") if english else FIELD_LABELS.get(field, "检查")
    if args != {} or name not in {"logistics_inspect", "logistics_audit", "logistics_summarize", "logistics_propose", "logistics_undo"}:
        return {"ok": False, "reply": choose("物流工具不接受路径、材料数组或模型生成的字段值。", "Logistics tools do not accept file paths, material arrays or model-generated field values.")}
    try:
        check()
        doc = validate_document(context["document"])
        if name == "logistics_propose":
            proposal = propose_command(doc, user_text)
            return {"ok": True, "logistics_proposal": proposal, "reply": choose(f"已提出 {len(proposal['changes'])} 项修订建议；台账未修改，请核对原值与新值后确认应用。", f"Proposed {len(proposal['changes'])} changes. The ledger is unchanged. Review the old and new values before approving.")}
        if name == "logistics_undo":
            if not _undo_requested(user_text) or not context.get("project", {}).get("can_undo"):
                raise ValueError("当前没有用户明确要求或没有可撤销的历史；未执行撤销。")
            return {"ok": True, "logistics_action": "undo", "reply": choose("已准备撤销上次台账修改的建议，尚未执行；请在工作台确认。", "An undo proposal is ready but has not been applied. Confirm it in the workbench.")}
        audit, summary = audit_document(doc), summarize(doc)
        errors = [i for i in audit.get("issues", []) if i.get("severity") == "error"]
        lines = [choose(f"当前台账有 {len(doc['rows'])} 行；检查发现 {len(errors)} 项错误、{len(audit.get('issues', [])) - len(errors)} 项提示。", f"The ledger contains {len(doc['rows'])} rows. Checks found {len(errors)} errors and {len(audit.get('issues', [])) - len(errors)} warnings.")]
        if name in {"logistics_inspect", "logistics_audit"}:
            lines.extend(f"{i.get('row_id') or choose('整表', 'Whole ledger')} / {label(i.get('field'))}: " + (i.get('code', 'review required').replace('_', ' ') if english else i['message'][:300]) for i in audit.get("issues", [])[:12])
        if name == "logistics_summarize":
            def value(v):
                if v == "UNSPECIFIED" or v is None:
                    return choose("未确定", "unspecified")
                if type(v) in (int, float) and v == int(v):
                    return str(int(v))
                return str(v)
            totals = summary.get("totals", {})
            lines.append(choose(f"包装数：{value(totals.get('package_count'))}；净重：{value(totals.get('net_kg'))} kg；毛重：{value(totals.get('gross_kg'))} kg。", f"Packages: {value(totals.get('package_count'))}; net mass: {value(totals.get('net_kg'))} kg; gross mass: {value(totals.get('gross_kg'))} kg."))
            quantities = summary.get("quantities_by_unit", {})
            lines.append(choose("货物数量按单位分别汇总：", "Item quantities by unit: ") + ("; ".join(f"{value(count)} {unit[:100] if unit != 'UNSPECIFIED' else choose('（单位未明确）', '(unit unspecified)')}" for unit, count in list(quantities.items())[:20]) or choose("未确定", "unspecified")) + ".")
            unknown = {f: sum(r.get(f, "UNSPECIFIED") == "UNSPECIFIED" and not r.get("evidence", {}).get(f, {}).get("group") for r in doc["rows"]) for f in NUMERIC | {"dimension_scope", "weight_scope", "unit"}}
            lines.append(choose("待补字段：", "Missing fields: ") + ("; ".join(f"{label(f)}: {count} " + choose("行", "rows") for f, count in sorted(unknown.items()) if count) or choose("本次汇总字段均有明确值", "all summary fields have explicit values")) + ".")
        lines.append(choose("本次仅检查台账，没有修改、装箱或导出。", "This operation only inspected the ledger. It did not change, pack or export anything."))
        selected = doc["rows"][:50]
        selected += [r for r in doc["rows"][50:] if r["id"] in user_text][:10]
        catalog = [{"id": r["id"], "package_id": r["package_id"][:100], "material_id": r["material_id"][:100], "name": r["name"][:100],
                    "known_values": {f: r[f][:100] if isinstance(r[f], str) else r[f] for f in sorted(NUMERIC | {"dimension_scope", "weight_scope", "unit"}) if r[f] != "UNSPECIFIED"},
                    "unknown_fields": [f for f in sorted(NUMERIC | {"dimension_scope", "weight_scope", "unit"}) if r[f] == "UNSPECIFIED"]} for r in selected]
        bounded_audit = {**audit, "issues": [{k: v[:500] if isinstance(v, str) else v for k, v in issue.items()} for issue in audit.get("issues", [])[:50]], "issues_truncated": len(audit.get("issues", [])) > 50}
        bounded_summary = {k: v for k, v in summary.items() if k != "audit"}
        bounded_summary["quantities_by_unit"] = dict(list(summary.get("quantities_by_unit", {}).items())[:50])
        bounded_summary["quantities_truncated"] = len(summary.get("quantities_by_unit", {})) > 50
        check()
        return {"ok": True, "audit": bounded_audit, "summary": bounded_summary, "row_catalog": catalog,
                "row_catalog_truncated": len(doc["rows"]) > len(catalog), "reply": "\n".join(lines)}
    except (ValueError, KeyError, TypeError) as exc:
        errors_en = {
            "请明确已有行号、字段和新值，例如：把 R00001 毛重改为 120 kg；不会猜测数据。": "Specify an existing row, field and value, for example: change R00001 gross mass to 120 kg. Values are never guessed.",
            "当前没有用户明确要求或没有可撤销的历史；未执行撤销。": "No explicit undo request or no history to undo. Nothing was changed.",
            "数字或单位不明确，未提出修订。": "The number or unit is unclear. No change was proposed.",
            "只能修改已有台账行的受限字段。": "Only allowed fields on existing ledger rows can be changed.",
            "尺寸须使用 mm、cm 或 m。": "Use mm, cm or m for dimensions.",
            "重量须使用 kg、g 或 t。": "Use kg, g or t for mass.",
            "包装数使用箱；不混用箱与货物数量单位。": "Use packages or boxes for package counts. Do not mix package and item units.",
            "原数量单位未明确，请先核对并明确单位，再修改数量；不会自动填入单位。": "Confirm the original unit before changing quantity. Units are never filled automatically.",
            "数值须为明确的正有限数，未知请用 UNSPECIFIED。": "Values must be positive finite numbers. Use UNSPECIFIED for unknown values.",
            "箱数与件数必须是正整数。": "Package counts and item quantities must be positive integers.",
            "所填值与台账一致，没有待应用的修订。": "The values already match the ledger. There is no change to apply.",
            "同一提案中的数量单位与单位修订不一致，请分别核对单位和数量；未应用修改。": "The quantity unit conflicts with the unit change in this proposal. Review both; no change was applied.",
            "同一字段不能重复修订。": "A proposal cannot change the same field more than once.",
            "该字段属于原图跨行合并单元格，不能作为单行修改；请核对共享范围并提供拆分后的原件。": "This field comes from a merged source cell spanning rows. Review its scope and provide a separated source before editing it.",
            "尺寸口径仅支持 package / item。": "Dimension basis must be package or item.",
            "重量口径仅支持 package / item / row。": "Mass basis must be package, item or row.",
        }
        if english:
            return {"ok": False, "reply": errors_en.get(str(exc), "The requested change could not be validated. Check the row, field, value and unit; the ledger is unchanged."), "detail": str(exc)}
        return {"ok": False, "reply": str(exc)}


def reply_for(results, context=None):
    if isinstance(results, dict):
        results = [results]
    return "\n\n".join(str(r.get("reply", "")) for r in results if isinstance(r, dict) and r.get("reply")) or "没有工具成功返回结果；台账未修改。"
