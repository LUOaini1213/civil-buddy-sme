"""Literal BIM records with file/project ownership; never inspect a model."""
from __future__ import annotations

import html
import re

from packing_assistant.post_drafts.bim import _Input, _FIELDS, _COORD_CONTEXT, UNKNOWN
from packing_assistant.post_drafts.planning import _inputs
from packing_assistant.post_drafts.procurement import _literal_source, _source_name
from packing_assistant.word_export import _table_cells

EXTRA = {
    "owner": ("责任方",), "owner_person": ("责任人", "负责人", "跟进人"),
    "deadline": ("期限", "完成时限", "整改时限"), "recipient": ("交付对象", "接收人"),
    "split": ("拆分",), "source": ("资料来源",), "zone": ("对象辖区",),
    "project_zone": ("总体辖区", "项目辖区"), "basis_version": ("依据版本",),
    "project_stage": ("项目阶段",),
    "CN_basis": ("CN依据",), "SG_basis": ("SG依据",), "EU_basis": ("EU依据",),
    "pending": ("待核项", "待补资料"),
}
ALIASES = {a.casefold(): k for k, names in _FIELDS.items() for a in names}
ALIASES.update({a.casefold(): k for k, names in EXTRA.items() for a in names})
LABELS = "|".join(map(re.escape, sorted(ALIASES, key=len, reverse=True)))
LABEL = re.compile(r"(?<![A-Za-z0-9_])("+LABELS+r")\s*[:：=]\s*", re.I)
BARE = re.compile(r"^("+LABELS+r")\s+(.+)$", re.I)
NUMBER = r"[+-]?\d+(?:,\d{3})*(?:\.\d+)?"
VERSION = r"V\d+(?:\.\d+)*"
DATE = r"(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}月\d{1,2}[号日]|月底|周[一二三四五六日])"
EMPTY = re.compile(r"^(?:\[A\d+\]\s*)?(?:待填|待核|未提供|未知|未说明|UNSPECIFIED|TBD|N/A)$", re.I)
MISSING = re.compile(r"忘了|回头补|没拿到|没报|没人报|没人给|还没|未给|未提供|待补|未测|未知|没定|未定|预计|计划提供|计划生成|将生成")
UNSAFE = re.compile(r"例如|比如|假设|示例|如果|自行计算|自己算|不采用|不是|是否|[？?]")
SOURCES = {"source", "evidence", "closure", "exchange_record", "clash_record", "quantity_record", "acceptance", "open_record", "attribute_record"}
SCOPED = {"project", "project_zone", "basis", "basis_version", "CN_basis", "SG_basis", "EU_basis"}


def _clean(raw):
    value = raw.strip(" \t\r\n，,；。")
    if not re.search(r"&(?:#\d+|#x[0-9a-f]+|[a-z][a-z0-9]+);$", value, re.I):
        value = value.rstrip(";")
    return value or UNKNOWN


def _typed(key, raw):
    value = _clean(raw)
    if EMPTY.fullmatch(value):
        return UNKNOWN
    if key in {"zone", "project_zone"}:
        return {"CN": "CN", "SG": "SG", "EU": "EU", "DUAL": "DUAL", "中国": "CN", "新加坡": "SG"}.get(value.upper(), UNKNOWN)
    if key in {"version", "date", "lod", "quantity", "deadline", "delivery_date", "tolerance"} | SOURCES and (MISSING.search(value) or UNSAFE.search(value)):
        return UNKNOWN
    return value


def _natural(text, post):
    text = re.sub(r"^.*?[:：](?=(?:一条是|另一条是))", "", text)
    if UNSAFE.search(text):
        return []
    pairs = []

    def emit(key, pattern, group=1):
        match = re.search(pattern, text, re.I)
        if match and match.group(group) is not None:
            pairs.append((key, match.group(group)))

    if re.fullmatch(r"SG|CN|EU|DUAL|中国|新加坡", text, re.I):
        return [("zone", text)]
    emit("project", r"^([一-鿿]{2,20})(?=\d+#(?:楼|厂房))")
    model = re.search(r"([\w#一-鿿]+模型)(?=LOD|要到LOD|V\d|$|昨天|，|。)", text, re.I)
    if model and not re.search(r"要求|改完|修改|回头|忘了|提供|生成", model.group(1)):
        pairs.append(("model", model.group(1)))
    elif post == "bim-qto" and re.match(r"^机电那个", text):
        pairs.append(("model", "机电"))
    emit("building", r"(\d+#(?:楼|厂房))")
    emit("version", r"("+VERSION+r")")
    emit("date", r"("+DATE+r")更新")
    emit("format", r"(rvt|ifc4|ifc2x3|nwd)(?:格式|$)")
    if post == "bim-coord":
        if re.match(r"^(?:一条是|另一条是)", text):
            pairs.append(("_issue", ""))
            text = re.sub(r"^(?:一条是|另一条是)", "", text)
        if re.search(r"硬碰|撞了|没留洞|间距不够|检修空间不足", text):
            if not any(k == "_issue" for k, _ in pairs):
                pairs.append(("_issue_if_needed", ""))
            emit("location", r"^((?:\d+#楼)?(?:B\d+|F\d+|屋面)层(?:\d+-\d+轴交[A-Z]轴|车库入口)?)")
            pairs.append(("description", text))
            emit("component_a", r"(排烟风管|送风管|冷却水管DN\d+)")
            emit("component_b", r"((?:KL-?\d+)(?:梁)?|剪力墙)")
            # User wording is retained, not a performed collision classification.
            emit("issue_type", r"(硬碰|没留洞|间距不够|检修空间不足)")
        emit("issue_id", r"(?:问题)?编号\s*([A-Za-z]+-\d+)")
        emit("owner", r"责任专业\s*(.+)$")
        emit("owner_person", r"责任人\s*(.+)$")
        emit("owner", r"^(暖通|结构)(?=[一-鿿]{2,4}(?:负责|跟进)|这边)")
        emit("owner_person", r"^(?:暖通|结构(?:这边)?)([一-鿿]{2,4})(?:负责|跟进)")
        emit("deadline", r"(?:要求)?("+DATE+r")前")
    elif post == "bim-qto":
        category = re.search(r"(剪力墙|砌体墙|框架柱|镀锌风管)(?:的混凝土体积|按面积算|$)", text)
        if category:
            pairs.append(("category", category.group(1)))
        emit("level", r"(地下室)(?=剪力墙)")
        emit("material", r"材质只要(C\d+)的")
        if "镀锌风管" in text:
            pairs.append(("material", "镀锌"))
        emit("rule", r"(按面积算|按展开面积)")
        emit("rule", r"(混凝土体积)")
        if "门窗洞" in text and re.search(r"要扣|扣掉", text):
            emit("openings", r"(门窗洞.+)$")
        emit("deduction", r"^(弯头三通不单独计)$")
        quantity = re.search(r"(?:明细表(?:导出来是|拉出来)|导出来是)\s*("+NUMBER+r"\s*(?:立方米|立方|平米|平方米|m3|m2)?)", text)
        if quantity:
            pairs.append(("quantity", quantity.group(1)))
            if "明细表" in text:
                pairs.append(("source", "明细表"))
            unit = re.search(r"(立方米|立方|平米|平方米|m3|m2)$", quantity.group(1))
            if unit:
                pairs.append(("unit", unit.group(1)))
        emit("unit", r"单位(?:也)?是(.+)$")
    else:
        emit("stage", r"(施工深化)(?:阶段)?")
        emit("lod", r"(LOD\s*\d+)")
        emit("recipient", r"(?:要交给|交给|发给)(.+)$")
        emit("recipient", r"接收人是(.+)$")
        emit("purpose", r"(?:是给|拿去给)(.+?)用(?:的)?")
        emit("format", r"(?:格式交|格式他们要)(.+)$")
        emit("delivery_date", r"("+DATE+r")前(?:交完|发给)")
        if "碰撞复查记录还没" in text:
            pairs.append(("clash_record", UNKNOWN))
    if MISSING.search(text):
        pairs.append(("pending", text))
    return pairs


def parse(text, post):
    result = _Input()
    scope = {"project_zone": UNKNOWN, "_sources": {}, "_origin": ""}
    contexts = [scope]
    active, kind, model_parent = None, None, None
    file_id, origin, header = 0, "", None

    def context_id():
        return next(i for i, context in enumerate(contexts) if context is scope)

    def new(row_kind):
        nonlocal active, kind
        active = {"_context": context_id(), "_file": file_id, "_origin": origin, "_sources": {}}
        kind = row_kind
        if row_kind in {"issues", "quantities"} and model_parent:
            active["model_ref"] = model_parent.get("model", UNKNOWN)
            active["_model"] = model_parent
        result.records.setdefault(row_kind, []).append(active)

    def put(row, key, raw, position):
        value = _typed(key, raw)
        if value == UNKNOWN and _clean(raw) != UNKNOWN and key in {"version", "date", "lod", "quantity", "deadline", "delivery_date", "tolerance"} | SOURCES:
            row["pending"] = "；".join(filter(None, (row.get("pending"), key+"："+_clean(raw))))
        if value == UNKNOWN:
            row[key] = UNKNOWN
            row["_sources"].pop(key, None)
        else:
            row[key] = value if row.get(key) in {None, UNKNOWN} else "；".join(dict.fromkeys((row[key], value)))
            row["_sources"][key] = "；".join(dict.fromkeys(filter(None, (row["_sources"].get(key), position))))

    def scan(clause):
        clause = re.sub(r"^(?:本次实际情况|实际情况|真实记录)\s*[:：]\s*", "", clause)
        clause = re.sub(r"^(?:两个|两份)模型\s*[:：]\s*", "", clause)
        matches = list(LABEL.finditer(clause))
        if matches:
            return [(ALIASES[m.group(1).casefold()], clause[m.end():matches[i+1].start() if i+1 < len(matches) else len(clause)]) for i, m in enumerate(matches)]
        bare = BARE.match(clause)
        if bare:
            key, raw = ALIASES[bare.group(1).casefold()], bare.group(2)
            suffix = re.fullmatch(r"(.+?)\s+版本\s*("+VERSION+r")", raw, re.I) if key == "model" else None
            return [(key, suffix.group(1)), ("version", suffix.group(2))] if suffix else [(key, raw)]
        return _natural(clause, post)

    for line_number, line in enumerate(_literal_source(text).splitlines(), 1):
        stripped = line.strip()
        if _source_name(stripped) is not None or stripped.startswith(("【本轮用户要求", "【此前用户资料")):
            origin = _source_name(stripped) or stripped.strip("【】")
            file_id += 1
            scope = {"project_zone": UNKNOWN, "_sources": {}, "_origin": origin}
            contexts.append(scope)
            active, kind, model_parent, header = None, None, None, None
            continue
        position = (origin+" · " if origin else "")+f"输入第{line_number}行（非原件页码）"
        matrix_kind = None
        if stripped.startswith("|"):
            cells = [html.unescape(c).strip() for c in _table_cells(stripped)]
            if any(cells) and all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            if header is not None:
                pairs = [(ALIASES[h.casefold()], cells[i] if i < len(cells) else "") for i, h in enumerate(header) if h.casefold() in ALIASES]
                keys = {k for k, _ in pairs}
                matrix_kind = ("issues" if post == "bim-coord" and keys & {"issue_id", "description"} else
                               "interfaces" if post == "bim-coord" and keys & {"interface_id", "information"} else
                               "quantities" if post == "bim-qto" and keys & {"category", "quantity", "rule"} else
                               "deliverables" if post == "bim-deliver" and "deliverable" in keys else "models")
                if matrix_kind != "models":
                    pairs = [("model_ref" if k == "model" else k, v) for k, v in pairs]
                active, kind, model_parent = None, None, None
            elif len(cells) >= 2 and cells[0].casefold() in ALIASES and cells[1].casefold() not in ALIASES and not any(cells[2:]):
                pairs = [(ALIASES[cells[0].casefold()], cells[1])]
            elif any(c.casefold() in ALIASES for c in cells):
                header = cells
                continue
            else:
                continue
        else:
            if header is not None:
                active, kind, model_parent, header = None, None, None, None
            pairs = [p for clause, _, _, _ in _inputs(line) if clause is not None for p in scan(clause)]
        if not pairs:
            continue
        if matrix_kind:
            scope = {**scope, "_sources": dict(scope.get("_sources", {}))}
            contexts.append(scope)
            pairs.sort(key=lambda p: {"project": 0, "project_stage": 1, "project_zone": 2}.get(p[0], 3))
        for key, raw in pairs:
            scoped = key in {"project", "project_zone"} or key in SCOPED and active is None
            scoped = scoped or post == "bim-coord" and key in _COORD_CONTEXT | {"meeting_date", "attendees", "decision", "next_merge"}
            if key == "project_stage":
                key, scoped = "stage", True
            if key == "zone" and active is None and not any(k in {"model", "issue_id", "category", "deliverable"} for k, _ in pairs):
                key, scoped = "project_zone", True
            if scoped:
                if key in {"project", "stage", "project_zone"} and (scope.get(key) not in {None, UNKNOWN, _typed(key, raw)} or active is not None and scope.get(key) in {None, UNKNOWN}):
                    scope = {"project_zone": UNKNOWN, "_sources": {}, "_origin": origin} if key == "project" else {**scope, "_sources": dict(scope.get("_sources", {}))}
                    scope.pop(key, None)
                    scope["_sources"].pop(key, None)
                    contexts.append(scope)
                    active, kind, model_parent = None, None, None
                put(scope, key, raw, position)
                continue
            if key == "_issue":
                new("issues")
                continue
            if key == "_issue_if_needed":
                if kind != "issues" or active and active.get("description") not in {None, UNKNOWN}:
                    new("issues")
                continue
            row_kind = ("models" if key == "model" else
                        "issues" if post == "bim-coord" and key in {"issue_id", "description"} else
                        "interfaces" if post == "bim-coord" and key in {"interface_id", "information"} else
                        "quantities" if post == "bim-qto" and key == "category" else
                        "deliverables" if post == "bim-deliver" and key == "deliverable" else None)
            if matrix_kind and active is None:
                new(matrix_kind)
            elif row_kind and (kind != row_kind or active is None or active.get(key) not in {None, UNKNOWN}):
                new(row_kind)
            if active is None:
                if key in {"pending"}:
                    continue
                # A plain preamble is stored separately, never copied into
                # every object just because it precedes them.
                put(scope, "model_unit" if key == "unit" and post != "bim-qto" else key, raw, position)
                continue
            if key == "unit" and kind == "models":
                key = "model_unit"
            if key == "model_ref" and key not in active["_sources"]:
                # An explicit reference supersedes the preceding-model hint.
                # It may not keep that model's version or other private link.
                active.pop("model_ref", None)
                active.pop("_model", None)
            put(active, key, raw, position)
            if kind == "models":
                model_parent = active
    result.contexts = contexts
    result.context = next((c for c in contexts if any(not k.startswith("_") and (k != "project_zone" or v != UNKNOWN) for k, v in c.items())), contexts[-1])
    for row_kind, records in result.records.items():
        for row in records:
            row["_context_data"] = contexts[row["_context"]]
            if row_kind != "models" and "model_ref" in row.get("_sources", {}):
                candidates = [model for model in result.records.get("models", [])
                              if model["_file"] == row["_file"] and model["_context"] == row["_context"]
                              and model.get("model") == row["model_ref"] and row["model_ref"] != UNKNOWN]
                row.pop("_model", None)
                if len(candidates) == 1:
                    row["_model"] = candidates[0]
    return result
