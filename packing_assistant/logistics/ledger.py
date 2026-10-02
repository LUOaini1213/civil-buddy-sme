"""Bounded pure ledger validation and reconciliation. Unknown facts remain unknown."""
from __future__ import annotations

from copy import deepcopy
import json
import math
import re

from packing_assistant.runtime.cancel import check
from packing_assistant.transport_constraints import CHOICES, ledger_issues

UNSPECIFIED = "UNSPECIFIED"
SCHEMA = "civil.logistics.v1"
MAX_ROWS = 5000
MAX_JSON_BYTES = 12 * 1024 * 1024
NUMERIC_FIELDS = ("package_count", "quantity", "units_per_package", "length_mm", "width_mm", "height_mm", "net_kg", "gross_kg", "tare_kg", "capacity_kg")
COUNT_FIELDS = ("package_count", "quantity", "units_per_package")
TEXT_FIELDS = ("package_id", "container_id", "package_type", "material_id", "name", "spec", "unit", "dimension_scope", "weight_scope", "orientation", "stacking", "handling_requirements")
FIELDS = TEXT_FIELDS + NUMERIC_FIELDS
FIELD_LABELS = {"package_count": "包装数", "quantity": "货物数量", "units_per_package": "每箱件数",
                "length_mm": "长度", "width_mm": "宽度", "height_mm": "高度", "net_kg": "净重", "gross_kg": "毛重",
                "name": "品名", "container_id": "集装箱号", "package_type": "包装类型",
                "dimension_scope": "尺寸口径", "weight_scope": "重量口径", "orientation": "运输姿态", "stacking": "堆叠要求",
                "handling_requirements": "运输要求原文", "tare_kg": "每包装皮重", "capacity_kg": "每包装声明载荷上限"}


def _json(value, depth=0):
    if depth > 16:
        raise ValueError("单据结构层级过深")
    if isinstance(value, dict):
        if len(value) > 5000 or any(not isinstance(k, str) or len(k) > 256 for k in value):
            raise ValueError("单据字典键无效")
        for v in value.values():
            _json(v, depth + 1)
    elif isinstance(value, list):
        if len(value) > 20000:
            raise ValueError("单据数组过长")
        for v in value:
            _json(v, depth + 1)
    elif isinstance(value, str):
        if len(value) > 20000:
            raise ValueError("单据文字字段过长")
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("单据不接受非有限数")
    elif value is not None and not isinstance(value, (int, bool)):
        raise ValueError("单据必须是 JSON 数据")


def _location(location):
    if not isinstance(location, dict):
        raise ValueError("证据来源必须为对象")
    for key in ("row", "column", "page"):
        if key in location and (type(location[key]) is not int or not 1 <= location[key] <= 100000):
            raise ValueError("证据页码、行列须为正整数")
    if "bbox" in location:
        b = location["bbox"]
        if not isinstance(b, list) or len(b) != 4 or any(type(n) not in (int, float) or not math.isfinite(n) for n in b) or b[2] < b[0] or b[3] < b[1]:
            raise ValueError("证据坐标框无效")


def validate_document(document: dict) -> dict:
    check()
    if not isinstance(document, dict):
        raise ValueError("单据必须为对象")
    _json(document)
    if len(json.dumps(document, ensure_ascii=False).encode("utf-8")) > MAX_JSON_BYTES:
        raise ValueError("单据结构超过 12 MB")
    doc = deepcopy(document)
    if doc.get("schema") != SCHEMA:
        raise ValueError("不支持的物流单据版本")
    source = doc.get("source")
    if not isinstance(source, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(source.get("sha256", ""))):
        raise ValueError("缺少有效原文件 SHA256")
    if not isinstance(source.get("filename"), str) or not source["filename"] or len(source["filename"]) > 255:
        raise ValueError("原文件名称无效")
    if type(source.get("bytes")) is not int or not 0 < source["bytes"] <= 20 * 1024 * 1024:
        raise ValueError("原文件字节数无效")
    if not isinstance(doc.get("extraction"), dict):
        raise ValueError("缺少解析来源")
    rows = doc.get("rows")
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise ValueError("物料行数超过限制")
    ids = set()
    for row in rows:
        check()
        if not isinstance(row, dict) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(row.get("id", ""))):
            raise ValueError("物料行 ID 无效")
        if row["id"] in ids:
            raise ValueError("物料行 ID 重复")
        ids.add(row["id"])
        for field in FIELDS:
            value = row.setdefault(field, UNSPECIFIED)
            if value == UNSPECIFIED:
                continue
            if field in NUMERIC_FIELDS:
                if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1e12:
                    raise ValueError(f"{row['id']}.{field} 必须为非负有限数或 UNSPECIFIED")
                if field in COUNT_FIELDS and value != int(value):
                    raise ValueError(f"{row['id']}.{field} 必须为整数")
            elif not isinstance(value, str) or len(value) > 2000:
                raise ValueError(f"{row['id']}.{field} 文字无效")
        if row["dimension_scope"] not in (UNSPECIFIED, "package", "item") or row["weight_scope"] not in (UNSPECIFIED, "package", "item", "row"):
            raise ValueError("尺寸或重量范围无效")
        for field, values in CHOICES.items():
            if row[field] not in values:
                raise ValueError(f"{field} 运输约束取值无效")
        ev = row.setdefault("evidence", {})
        if not isinstance(ev, dict):
            raise ValueError("字段证据必须为对象")
        for field, evidence in ev.items():
            if not isinstance(evidence, dict) or not isinstance(evidence.get("raw", ""), str):
                raise ValueError("字段证据原文无效")
            _location(evidence.get("source", {}))
    by_id = {r["id"]: r for r in rows}
    shared = {}
    for row in rows:
        for field, evidence in row["evidence"].items():
            group = evidence.get("group")
            if group is None:
                continue
            if (field not in FIELDS or not isinstance(group, dict)
                    or set(group) != {"id", "row_ids", "anchor_row_id", "source"}
                    or not isinstance(group["id"], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", group["id"])
                    or not isinstance(group["row_ids"], list) or not 2 <= len(group["row_ids"]) <= MAX_ROWS
                    or any(not isinstance(i, str) or i not in ids for i in group["row_ids"])
                    or len(set(group["row_ids"])) != len(group["row_ids"])
                    or group["anchor_row_id"] != group["row_ids"][0] or row["id"] not in group["row_ids"]):
                raise ValueError("共享单元格的范围或锚点无效")
            _location(group["source"])
            if "bbox" not in group["source"] or "page" not in group["source"]:
                raise ValueError("共享单元格须有原件页码与合并区域")
            key = (field, group["id"])
            if key in shared and shared[key] != group:
                raise ValueError("共享单元格的成员范围不一致")
            shared[key] = group
    for (field, _), group in shared.items():
        for ident in group["row_ids"]:
            member = by_id[ident]
            if member["evidence"].get(field, {}).get("group") != group:
                raise ValueError("共享单元格缺少成员证据")
            if ident != group["anchor_row_id"] and member[field] != UNSPECIFIED:
                raise ValueError("共享单元格不能复制数值到每个成员行")
            if field in ("net_kg", "gross_kg") and member["weight_scope"] not in ("row", UNSPECIFIED):
                raise ValueError("共享合并重量不能当成每件或每箱重量")
    totals = doc.setdefault("totals", [])
    if not isinstance(totals, list) or len(totals) > MAX_ROWS:
        raise ValueError("合计记录无效")
    for total in totals:
        if not isinstance(total, dict) or not isinstance(total.get("values"), dict):
            raise ValueError("合计记录无效")
        _location(total.get("source", {}))
        if any(k not in NUMERIC_FIELDS or type(v) not in (int, float) or not math.isfinite(v) or v < 0 for k, v in total["values"].items()):
            raise ValueError("合计数值无效")
        if (not isinstance(total.get("row_ids", []), list) or any(not isinstance(i, str) or i not in ids for i in total.get("row_ids", []))
                or len(set(total.get("row_ids", []))) != len(total.get("row_ids", []))):
            raise ValueError("合计引用了不存在的物料行")
    return doc


def known(value):
    return type(value) in (int, float) and math.isfinite(value)


def row_total(row, field):
    value = row.get(field, UNSPECIFIED)
    if not known(value):
        return None
    if field in ("net_kg", "gross_kg"):
        scope = row.get("weight_scope")
        if scope == "package":
            if row.get("evidence", {}).get("package_count", {}).get("group"):
                return None  # Shared package counts cannot be allocated to one material row.
            multiplier = row.get("package_count")
        elif scope == "item":
            if row.get("evidence", {}).get("quantity", {}).get("group"):
                return None
            multiplier = row.get("quantity")
        elif scope == "row":
            multiplier = 1
        else:
            return None
        return value * multiplier if known(multiplier) else None
    return value


def package_identities(rows):
    """Resolve stated package identity, including an explicit shared container cell.

    A box label is local to its container. When the same label has both known
    and unknown containers we cannot decide whether those rows are one box.
    Shared evidence is read here without filling values into member rows.
    """
    by_id = {row["id"]: row for row in rows}

    def stated(row, field):
        group = row.get("evidence", {}).get(field, {}).get("group")
        if group:
            anchor = by_id.get(group["anchor_row_id"])
            if not anchor or anchor.get("evidence", {}).get(field, {}).get("group") != group:
                return UNSPECIFIED
            row = anchor
        value = row.get(field, UNSPECIFIED)
        return value.strip() if isinstance(value, str) and value.strip() else UNSPECIFIED

    identities, containers = {}, {}
    for row in rows:
        pid, cid = stated(row, "package_id"), stated(row, "container_id")
        identities[row["id"]] = None if pid == UNSPECIFIED else (cid, pid)
        if pid != UNSPECIFIED:
            containers.setdefault(pid, set()).add(cid)
    ambiguous = {pid for pid, values in containers.items() if UNSPECIFIED in values and len(values) > 1}
    return identities, ambiguous


def aggregate_rows(rows):
    """One package may contain several material rows; package facts count once.

    Quantity has a unit, so mixed units never collapse into a single number.
    This function is shared by screen summaries and source-total reconciliation.
    """
    issues, groups, quantities = [], {}, {}
    identities, ambiguous = package_identities(rows)
    for row in rows:
        check()
        identity = identities[row["id"]]
        key = (("ambiguous-package", identity[1]) if identity and identity[1] in ambiguous
               else ("package", *identity) if identity else ("row", row["id"]))
        groups.setdefault(key, []).append(row)
        unit = str(row["unit"]).strip().lower()
        if unit in ("pc", "pcs", "ea", "件", "个"):
            unit = "pcs"
        value = row["quantity"]
        key = unit if unit and unit != UNSPECIFIED.lower() else UNSPECIFIED
        quantities.setdefault(key, []).append(value if known(value) else None)
    quantities_by_unit = {u: sum(v) if all(n is not None for n in v) else UNSPECIFIED for u, v in quantities.items()}
    sums = {field: [] for field in ("package_count", "net_kg", "gross_kg")}
    by_id = {r["id"]: r for r in rows}
    seen_shared = set()
    for group in groups.values():
        first = group[0]
        repeated = len(group) > 1
        identity = identities[first["id"]]
        ambiguous_container = identity is not None and identity[1] in ambiguous
        conflict = repeated and (
            ambiguous_container or any(r["dimension_scope"] != "package" or r["weight_scope"] != "package" for r in group)
            or any(any(r[field] != first[field] for r in group[1:]) for field in ("package_count", "length_mm", "width_mm", "height_mm", "net_kg", "gross_kg"))
            or not known(first["package_count"])
        )
        if conflict:
            issues.append({"severity": "error", "code": "package_group_conflict", "row_id": ",".join(r["id"] for r in group), "field": "package_id",
                           "message": "同一箱号同时存在明确和未知柜号，无法确定是否同一包装；请先核对柜号。" if ambiguous_container else
                           "同一柜号下同一箱号的包装数量、尺寸或重量不一致，或每箱范围未明确；不合并计算包数与重量。"})
        for field in sums:
            if conflict:
                sums[field].append(None)
            else:
                shared = first.get("evidence", {}).get(field, {}).get("group")
                if shared:
                    key = (field, shared["id"])
                    if key in seen_shared:
                        continue
                    seen_shared.add(key)
                    # A subset of a merged cell has no independently stated amount.
                    complete = set(shared["row_ids"]).issubset(by_id) and not repeated
                    anchor = by_id.get(shared["anchor_row_id"])
                    sums[field].append(row_total(anchor, field) if complete and anchor else None)
                else:
                    sums[field].append(row_total(first, field))
    totals = {f: round(sum(values), 6) if values and all(v is not None for v in values) else UNSPECIFIED for f, values in sums.items()}
    totals["quantity"] = next(iter(quantities_by_unit.values())) if len(quantities_by_unit) == 1 and UNSPECIFIED not in quantities_by_unit else UNSPECIFIED
    return {"totals": totals, "quantities_by_unit": quantities_by_unit, "issues": issues}


def audit_document(document: dict) -> dict:
    doc = validate_document(document)
    issues = []

    def issue(code, message, row_id="", field="", severity="error"):
        issues.append(dict(severity=severity, code=code, row_id=row_id, field=field, message=message))

    for item in doc["extraction"].get("issues", []):
        if isinstance(item, dict):
            issue(str(item.get("code", "extraction")), str(item.get("message", "解析需要核对")), severity=str(item.get("severity", "warning")))
    if not doc["rows"]:
        issue("no_material_rows", "没有识别出具有明确表头的物料行；请提供表格或核对版式。")
    aggregate = aggregate_rows(doc["rows"])
    issues.extend(aggregate["issues"])
    seen_shared = set()
    for row in doc["rows"]:
        check()
        rid = row["id"]
        issues.extend(ledger_issues(row))
        for field in ("name", "quantity", "package_count"):
            if row[field] == UNSPECIFIED and not row["evidence"].get(field, {}).get("group"):
                issue("missing_" + field, f"{FIELD_LABELS[field]}未确定，未填入默认值。", rid, field, "warning")
        for field, ev in row["evidence"].items():
            group = ev.get("group")
            if group and (field, group["id"]) not in seen_shared:
                seen_shared.add((field, group["id"]))
                issue("shared_cell", f"{FIELD_LABELS.get(field, field)}原表跨 {len(group['row_ids'])} 行合并，覆盖 {', '.join(group['row_ids'])}；合并值只汇总一次，不分配到每行或每箱。", group["anchor_row_id"], field, "warning")
            if ev.get("reason") and row.get(field) == UNSPECIFIED:
                issue("unresolved_field", str(ev["reason"]), rid, field)
            if ev.get("ocr") and not ev.get("confirmed") and not ev.get("correction") and not ev.get("corrections"):
                issue("ocr_requires_confirmation", "OCR 字段需对照原图确认。", rid, field, "warning")
        for field in NUMERIC_FIELDS:
            if known(row[field]) and row[field] == 0:
                issue("zero_value", f"{FIELD_LABELS[field]}明确为 0，请确认该行是否参与本次物流。", rid, field, "warning")
        q, p, u = (row[f] for f in ("quantity", "package_count", "units_per_package"))
        if all(known(n) for n in (q, p, u)) and q != p * u:
            issue("quantity_mismatch", "货物件数不等于箱数 × 每箱件数。", rid, "quantity")
        same_range = (row["evidence"].get("net_kg", {}).get("group", {}).get("row_ids", [rid])
                      == row["evidence"].get("gross_kg", {}).get("group", {}).get("row_ids", [rid]))
        if same_range and all(known(row[f]) for f in ("net_kg", "gross_kg")) and row["gross_kg"] < row["net_kg"]:
            issue("gross_below_net", "毛重小于同范围净重。", rid, "gross_kg")
        for group, scope in ((("length_mm", "width_mm", "height_mm"), "dimension_scope"), (("net_kg", "gross_kg"), "weight_scope")):
            if any(known(row[f]) for f in group) and row[scope] == UNSPECIFIED:
                issue("unknown_scope", f"{FIELD_LABELS[scope]}未明确为每箱、单件或整行；未用于汇总。", rid, scope)
    if len(aggregate["quantities_by_unit"]) > 1:
        issue("mixed_quantity_units", "数量含不同单位，按单位分别汇总，不给混合总件数。", field="unit", severity="warning")
    if UNSPECIFIED in aggregate["quantities_by_unit"]:
        issue("unknown_quantity_unit", "部分数量单位未明确，不参与单一数量总计。", field="unit", severity="warning")
    by_id = {r["id"]: r for r in doc["rows"]}
    checked_ranges = set()
    for row in doc["rows"]:
        for field in ("net_kg", "gross_kg"):
            group = row["evidence"].get(field, {}).get("group")
            if not group:
                continue
            members = tuple(group["row_ids"])
            if members in checked_ranges:
                continue
            checked_ranges.add(members)
            weight = aggregate_rows([by_id[i] for i in members])["totals"]
            if all(known(weight[f]) for f in ("net_kg", "gross_kg")) and weight["gross_kg"] < weight["net_kg"]:
                issue("group_gross_below_net", f"共享范围 {', '.join(members)} 的毛重 {weight['gross_kg']:g} kg 小于同范围净重 {weight['net_kg']:g} kg。", members[0], "gross_kg")
    for total in doc["totals"]:
        selected = [by_id[i] for i in total.get("row_ids", [])]
        selected_aggregate = aggregate_rows(selected)
        for field, expected in total["values"].items():
            actual = selected_aggregate["totals"].get(field, UNSPECIFIED)
            if field == "quantity" and len(selected_aggregate["quantities_by_unit"]) > 1:
                separated = "、".join(f"{v} {u}" for u, v in selected_aggregate["quantities_by_unit"].items())
                issue("mixed_unit_source_total", f"{total.get('label','合计')}原数量 {expected:g} 涉及不同单位；分开为 {separated}，不把原值当作总件数。", field=field, severity="warning")
            elif not known(actual):
                issue("total_unverifiable", f"{total.get('label','合计')}的{FIELD_LABELS[field]}缺逐行明确值、单位或范围，不能校核。", field=field, severity="warning")
            elif not math.isclose(actual, expected, rel_tol=1e-8, abs_tol=0.01 if field.endswith("_kg") else 0):
                issue("total_mismatch", f"{total.get('label','合计')}的{FIELD_LABELS[field]}原值 {expected} 与核对汇总 {actual:g} 不一致。", field=field)
    errors = sum(i["severity"] == "error" for i in issues)
    return {"ok": errors == 0 and bool(doc["rows"]), "issues": issues,
            "counts": {"rows": len(doc["rows"]), "totals": len(doc["totals"]), "errors": errors,
                       "warnings": sum(i["severity"] == "warning" for i in issues),
                       "info": sum(i["severity"] == "info" for i in issues)}}


def summarize(document: dict) -> dict:
    doc = validate_document(document)
    aggregate = aggregate_rows(doc["rows"])
    audit = audit_document(doc)
    ready = bool(doc["rows"]) and audit["ok"] and all(
        r["dimension_scope"] == "package" and r["weight_scope"] == "package"
        and all(known(r[f]) and r[f] > 0 for f in ("package_count", "gross_kg", "length_mm", "width_mm", "height_mm"))
        for r in doc["rows"])
    requires_review = any(i["code"] == "ocr_requires_confirmation" for i in audit["issues"])
    return {"source": deepcopy(doc["source"]), "rows": len(doc["rows"]), "totals": aggregate["totals"],
            "quantities_by_unit": aggregate["quantities_by_unit"], "audit": audit,
            "input_complete": ready, "requires_review": requires_review,
            "ready_for_packing": ready, "confirmation_required": True}
