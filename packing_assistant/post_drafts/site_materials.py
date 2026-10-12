"""Literal material reconciliation records, not a stocktake or cost solver.

Each source, location, period, material and specification owns its quantities.
Tickets, inventory, adjustments and user-reported consumption stay separate;
none is subtracted, multiplied, converted or silently certified by this draft.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

from packing_assistant.post_drafts.planning import _inputs, _table
from packing_assistant.post_drafts.procurement import _source_name

MISSING = "[A001] / UNSPECIFIED"
TOOL = "material-site__recon"
DISCLAIMER = ("AI 草稿 · 内部讨论 · 不得作为法定专项方案 / 交底签认件。本稿仅整理用户材料核算资料，"
              "未核验工程量、指标、领退料或盘点，不计算应耗、实耗、节超率、盈亏或结算价款，"
              "不作材料质量结论，不替代真实盘点、成本核算或签认。资料与口径须由责任人复核。")
_NUMBER = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_UNIT = r"(?:吨|方|袋|根|块|卷|平米|平方米|平|个|套|件|米|t|kg|m[³3]|m[²2]|m)"
_QUANTITY = re.compile(r"(?<![A-Za-z0-9_.#/-])("+_NUMBER+r"\s*"+_UNIT+r")(?![A-Za-z0-9_.#/-])", re.I)
_QUANTITY_VALUE = re.compile(_NUMBER+r"(?:\s*"+_UNIT+r")?", re.I)
_LOCATION = re.compile(r"\d+[#＃]楼(?:[一二三四五六七八九十\d]+(?:至[一二三四五六七八九十\d]+)?层)?(?:二次结构|墙柱|顶板|底板|砌体|外墙)?|地下室(?:B\d+层)?(?:底板|外墙)?")
_MATERIAL = re.compile(r"商品混凝土|防水卷材|蒸压加气块|加气块|螺纹钢|钢筋|水泥|砌块|混凝土|模板|木方|钢管|扣件|安全网|(?<![A-Za-z0-9_/-])(?:rebar|concrete|blocks|membrane)(?![A-Za-z0-9_/-])", re.I)
_MODEL = re.compile(r"(?<![A-Za-z0-9_.-])(?:HRB\d+E?|HPB\d+|C\d+(?:P\d+)?|SBS)(?![A-Za-z0-9_.-])", re.I)
_PERIOD = re.compile(r"\d{4}[-/.年]\d{1,2}(?:月)?|[一二三四五六七八九十\d]+月|这个月|本月|下月")
_DATE = r"(?:\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?|\d{1,2}月\d{1,2}[日号]|月底|本周|下周|昨天|今天)"
_UNKNOWN = re.compile(r"还没|未提供|未给|未定|未报|未知|待填|待核|待补|没给|没定|没报|没做|没有|多少|UNSPECIFIED|TBD|N/A|unknown|not provided", re.I)
_UNSAFE = re.compile(r"例如|比如|假设|如果|示例|样例|自行计算|自己算|不采用|不按|别按|不要|不是|是否|[？?]|\b(?:for example|assuming|hypothetical|whether)\b", re.I)
_FORECAST = re.compile(r"预计|计划|拟|估计|感觉|大概|约|应该|未盘点|没盘点|未确认|待确认|还没|没做|尚未|据说|听说|明天|下周|将要|\b(?:planned|forecast|estimated|unconfirmed|reportedly|tomorrow|will)\b", re.I)
_NOT_OCCURRED = re.compile(r"(?:未|没|还未|尚未|暂未)\s*(?:领|退|收方|计量|盘点|核定|发生|实耗)|\bnot\b", re.I)
_FIELDS = {
    "project": ("项目名称", "工程名称", "项目", "Project"), "jurisdiction": ("辖区", "Jurisdiction"),
    "period": ("核算期间", "期间", "所属期", "Period"), "version": ("核算版本", "版本", "Version"),
    "location": ("分部或部位", "核算部位", "部位", "Location"), "material": ("材料名称", "材料", "Material"),
    "model": ("材料规格", "规格型号", "规格", "Model"), "batch": ("材料批次", "批号", "Batch"), "record_id": ("核算编号", "记录编号", "Record ID"),
    "unit": ("材料单位", "单位", "Unit"), "kind": ("材料类别", "类别", "Kind"), "supply": ("甲指或自采", "供料方式", "Supply"),
    "work": ("已完工程量", "完成工程量", "工程量", "Work quantity"), "work_source": ("工程量来源", "计量依据", "Work source"),
    "work_unit": ("工程量单位", "Work unit"), "indicator": ("消耗指标", "用户指标", "指标", "Consumption index"),
    "indicator_source": ("指标来源", "Indicator source"), "indicator_note": ("指标口径", "Indicator note"), "loss": ("用户损耗率", "损耗率", "Loss rate"),
    "expected": ("用户应耗", "应耗", "计划用量", "Expected quantity"), "plan_source": ("计划来源", "应耗来源", "Plan source"),
    "issued": ("本期领料", "领料", "领用", "Issued"), "issue_source": ("领料来源", "领料单", "Issue source"),
    "returned": ("本期退料", "退料", "退库", "Returned"), "return_source": ("退料来源", "退料单", "Return source"),
    "count_record": ("用户盘点记录", "盘点记录", "盘点", "Count record"), "counted": ("实盘数量", "盘点实存", "实存", "实盘", "Counted"),
    "count_source": ("盘点来源", "盘点单", "Count source"), "count_date": ("盘点日期", "Count date"),
    "adjustment": ("用户盘点调整", "盘点调整", "Adjustment"), "adjustment_source": ("调整依据", "调整来源", "Adjustment source"),
    "actual": ("用户实耗", "实耗", "Actual consumption"), "actual_source": ("实耗来源", "Actual source"),
    "variance": ("用户节超", "节超", "Variance"), "variance_source": ("节超来源", "Variance source"),
    "sign_convention": ("节超符号约定", "正负约定", "Sign convention"),
    "cause": ("原因类型", "原因", "Cause"), "owner": ("责任方", "责任班组", "责任人", "Owner"),
    "change": ("变更资料", "变更", "Change"), "isolation": ("隔离记录", "隔离", "Isolation"),
    "arrival": ("周转料进场", "进场数量", "Arrival"), "in_use": ("在用数量", "在用", "In use"),
    "repair": ("维修数量", "维修", "Repair"), "scrapped": ("报废数量", "报废", "Scrapped"), "return_rental": ("退租数量", "退租", "Rental return"),
    "amortization": ("用户摊销方法", "摊销方法", "Amortization"), "price": ("用户单价原文", "单价", "Price"),
    "price_source": ("单价来源", "价格依据", "Price source"),
    "source": ("来源文件", "资料来源", "来源", "Source"), "record": ("待核记录", "记录", "Record"),
}
ALIASES = {a.casefold(): k for k, labels in _FIELDS.items() for a in labels}
_LABEL = re.compile(r"^("+"|".join(re.escape(a) for a in sorted(ALIASES, key=len, reverse=True))+r")\s*[:：=]\s*(.*)$", re.I)
_NUMERIC = {"work", "expected", "issued", "returned", "counted", "adjustment", "actual", "variance", "arrival", "in_use", "repair", "scrapped", "return_rental"}
_SOURCES = {"work": "work_source", "indicator": "indicator_source", "expected": "plan_source", "issued": "issue_source", "returned": "return_source", "counted": "count_source", "count_record": "count_source", "adjustment": "adjustment_source", "actual": "actual_source", "variance": "variance_source"}


@dataclass
class Data:
    contexts: list[dict[str, str]] = field(default_factory=list)
    rows: list[dict[str, str]] = field(default_factory=list)
    events: list[dict[str, str]] = field(default_factory=list)
    evidence: list[dict[str, str]] = field(default_factory=list)


def _value(value):
    value = value.strip()
    if not re.search(r"&(?:#\d+|#x[\da-f]+|[a-z][\da-z]+);$", value, re.I):
        value = value.rstrip("，,；;。")
    return MISSING if not value or _UNKNOWN.fullmatch(value) else value


def _typed(key, value):
    value = _value(value)
    if key == "jurisdiction" and value == MISSING:
        return "UNSPECIFIED"
    if key in _NUMERIC | {"indicator", "loss", "model", "owner", "record_id", "batch", "material", "location", "count_date"} and (_UNSAFE.search(value) or _UNKNOWN.search(value)):
        return MISSING
    if key in _NUMERIC and not _QUANTITY_VALUE.fullmatch(value):
        return MISSING
    if key in {"indicator", "loss"} and not re.fullmatch(_NUMBER+r"\s*%?", value):
        return MISSING
    if key == "count_date" and not re.fullmatch(_DATE, value):
        return MISSING
    return value


def _details(key, value):
    if key == "count_record":
        facts = [(key, value)]
        if _QUANTITY_VALUE.fullmatch(value.strip()):
            facts.append(("counted", value))
        elif not (_UNKNOWN.search(value) or _UNSAFE.search(value) or _FORECAST.search(value) or re.search(r"差|差异|调整", value)):
            counted = re.search(r"(?:实存|实盘|还剩)\s*("+_NUMBER+r"\s*"+_UNIT+r")", value, re.I)
            if counted:
                facts.append(("counted", counted.group(1)))
        return facts
    if key == "material" and _typed(key, value) != MISSING:
        models = list(_MODEL.finditer(value))
        return [(key, value[:models[0].start()]+value[models[0].end():]), ("model", models[0].group())] if len(models) == 1 else [(key, value)]
    if key in _NUMERIC and (_FORECAST.search(value) or _UNKNOWN.search(value) or _NOT_OCCURRED.search(value)):
        return [(key, value), ("record", _FIELDS[key][0]+"："+value)]
    return [(key, value)]


def _scan(text):
    text = re.sub(r"^(?:本次实际情况|实际情况|真实记录)\s*[:：]\s*", "", text)
    match = _LABEL.match(text)
    if match:
        return _details(ALIASES[match.group(1).casefold()], match.group(2))
    command = re.fullmatch(r"(?:写|出|生成|起草|编制)一份材料核算(?:表头)?\s+(.+)", text)
    if command:
        text = command.group(1)
        if not _MATERIAL.search(text):
            return [("material", text)]
    if _UNSAFE.search(text):
        return []
    if re.match(r"^来源\s*", text):
        return [("implicit_source", re.sub(r"^来源\s*", "", text))]
    facts = []
    location, material = _LOCATION.search(text), _MATERIAL.search(text)
    if location:
        facts.append(("location", location.group()))
    if material and not re.search(r"(?:领|退|盘点|实耗|指标|来源|变更|整改).*"+re.escape(material.group()), text[:material.end()]):
        facts.append(("material", material.group()))
    if "材料核算" in text or text in {"这个月", "本月", "下月"}:
        period = _PERIOD.search(text)
        if period:
            facts.append(("period", period.group()))
    if material and any(k == "material" for k, _ in facts):
        model = _MODEL.search(text)
        if model:
            facts.append(("model", model.group()))
    patterns = {
        "work": r"(?:已完工程量|收方单上工程量|完成工程量|工程量)",
        "expected": r"(?:应耗|计划用量)", "issued": r"(?:本期领料|领料|领了|领用)",
        "returned": r"(?:退料|退了|退库)", "actual": r"(?:实耗)", "variance": r"(?:节超)",
    }
    for key, label in patterns.items():
        start = re.search(label, text)
        if start:
            value = text[start.end():]
            quantity = _QUANTITY.search(value)
            pending = (_UNKNOWN.search(text) or _NOT_OCCURRED.search(text)
                       or key != "expected" and _FORECAST.search(text))
            if pending:
                facts.append(("record", text))
            elif quantity and not _UNSAFE.search(value):
                facts.append((key, quantity.group(1)))
                if _FORECAST.search(text):
                    facts.append(("record", text))
            if key == "work" and "对上计量" in value:
                facts.append(("work_source", "对上计量"))
            if key == "work" and "收方单" in text:
                facts.append(("work_source", "收方单"))
    indicator = re.search(r"消耗指标\s*("+_NUMBER+r"\s*%?)", text)
    if indicator:
        facts.append(("indicator", indicator.group(1)))
        source = re.search(r"(?:来自|来源)\s*(.+)$", text[indicator.end():])
        if source:
            facts.append(("indicator_source", source.group(1)))
    elif text in {"公司企业指标", "公司指标", "企业指标"}:
        facts.append(("indicator_source", text))
    if "盘点" in text:
        facts += _details("count_record", text)
    return facts


def parse(raw):
    data = Data()
    scope = {"jurisdiction": "UNSPECIFIED"}
    data.contexts.append(scope)
    origin = ""
    current = None
    active = ""
    events = {}

    def current_ref():
        return next((i for i, row in enumerate(data.rows) if row is current), None)

    def new(values=None):
        row = {"_origin": origin, "_scope": len(data.contexts)-1,
               "project": scope.get("project", MISSING), "jurisdiction": scope.get("jurisdiction", "UNSPECIFIED"),
               "period": scope.get("period", MISSING), "version": scope.get("version", MISSING), **(values or {})}
        data.rows.append(row)
        return row

    def scoped_value(key):
        value = current.get(key) if current is not None else None
        return scope.get(key) if value in {None, MISSING, "UNSPECIFIED"} else value

    def put(target, key, value):
        value = _typed(key, value)
        if target.get(key) in {None, MISSING, "UNSPECIFIED"}:
            target[key] = value
        elif value != MISSING and value not in target[key].split("；"):
            target[key] += "；"+value
        if target is current and key in _NUMERIC | {"indicator"}:
            event = {"_record": current_ref(), "field": _FIELDS[key][0], "quantity": value, "input_position": location}
            data.events.append(event)
            events[key] = event
        elif target is current and key in _SOURCES.values():
            quantity_key = next(k for k, source_key in _SOURCES.items() if source_key == key)
            event = events.get(quantity_key)
            if event is None:
                event = {"_record": current_ref(), "field": _FIELDS[quantity_key][0], "quantity": MISSING, "input_position": location}
                data.events.append(event)
                events[quantity_key] = event
            if event.get("source") in {None, MISSING}:
                event["source"] = value
            elif value != MISSING and value not in event["source"].split("；"):
                event["source"] += "；"+value
        if value not in {MISSING, "UNSPECIFIED"}:
            data.evidence.append({"_record": current_ref(), "field": _FIELDS[key][0], "value": value, "input_position": location, "_origin": origin})

    for content, is_table, offset, source in _inputs(raw):
        location = (origin+" · " if origin else "")+f"本轮输入第{raw[:offset].count(chr(10))+1}行（非原件页码）"
        if content is None:
            current, active, events = None, "", {}
            continue
        marker = _source_name(source)
        if marker is not None or source.startswith(("【本轮用户要求", "【此前用户资料")):
            origin = marker if marker is not None else source.strip("【】")
            scope = {"_origin": origin, "jurisdiction": "UNSPECIFIED"}
            data.contexts.append(scope)
            current, active, events = None, "", {}
            continue
        pairs = ([(key, val) for label, value in content if label.casefold() in ALIASES
                  for key, val in _details(ALIASES[label.casefold()], value)] if is_table else _scan(source))
        if not pairs:
            continue
        stated = dict(pairs)
        meta = {"project", "jurisdiction", "period", "version"}
        if not is_table and any(k in stated and scoped_value(k) not in {None, MISSING, "UNSPECIFIED", _typed(k, stated[k])}
                                for k in {"project", "jurisdiction", "version"}):
            scope = {"_origin": origin, "jurisdiction": "UNSPECIFIED"}
            data.contexts.append(scope)
            current, active, events = None, "", {}
        identity = {k: _typed(k, stated[k]) for k in meta if k in stated}
        declared_material = "material" in stated and (current is None or current.get("material") not in {None, MISSING})
        declared_location = "location" in stated and (current is None or current.get("location") not in {None, MISSING, _typed("location", stated["location"])})
        if is_table or declared_material or declared_location:
            # A new location may retain only the generic material name from
            # the explicit ongoing item, never its grade, quantities or units.
            if declared_location and not declared_material and current is not None:
                identity["material"] = current.get("material", MISSING)
            current, active, events = new(identity), "", {}
        elif "record_id" in stated and _typed("record_id", stated["record_id"]) != MISSING:
            identifier = _typed("record_id", stated["record_id"])
            found = [row for row in data.rows if row["_scope"] == len(data.contexts)-1 and row.get("record_id") == identifier]
            if current is None or current.get("record_id") not in {None, MISSING, identifier} or len(found) > 1:
                current, active, events = (found[0] if len(found) == 1 else new(identity)), "", {}
        if current is not None and any(k in stated and _typed(k, stated[k]) != MISSING and current.get(k) not in {None, MISSING, _typed(k, stated[k])}
                                      for k in {"model", "batch", "period"}):
            identity.update({k: current[k] for k in {"material", "location", "period"} if k in current and k not in stated})
            identity.update({k: _typed(k, stated[k]) for k in {"model", "batch", "period", "record_id"} if k in stated})
            current, active, events = new(identity), "", {}
        if is_table:
            # Sources appearing before their amount column still belong to
            # their explicitly named role, not the preceding table row.
            pairs = [p for p in pairs if p[0] not in _SOURCES.values()]+[p for p in pairs if p[0] in _SOURCES.values()]
        for key, value in pairs:
            if key == "implicit_source":
                key = _SOURCES.get(active, "source")
            if key in meta and current is None:
                put(scope, key, value)
                continue
            if current is None:
                current = new()
            put(current, key, value)
            if key in _NUMERIC | {"indicator", "count_record"}:
                active = key
    data.contexts = [r for r in data.contexts if len(r) > 1] or data.contexts[:1]
    return data


def _with_units(rows):
    """Expose only units literally present, without merging different units."""
    result = []
    for original in rows:
        row = dict(original)
        for field_name, keys in (("unit", {"expected", "issued", "returned", "counted", "adjustment", "actual"}), ("work_unit", {"work"})):
            units = set()
            for key in keys:
                for value in row.get(key, "").split("；"):
                    match = re.fullmatch(_NUMBER+r"\s*("+_UNIT+r")", value, re.I)
                    if match:
                        units.add(match.group(1))
            if row.get(field_name) in {None, MISSING} and len(units) == 1:
                row[field_name] = next(iter(units))
            if len(units) > 1 or row.get(field_name) not in {None, MISSING} and any(u != row[field_name] for u in units):
                row[field_name+"_note"] = "单位原文不一致，待核："+" / ".join(sorted(units))
        result.append(row)
    return result


def _owned(data, rows):
    result = []
    owners = _with_units(data.rows)
    for row in rows:
        ref = row.get("_record")
        owner = owners[ref] if isinstance(ref, int) else {}
        result.append({**owner, **row})
    return result


def build_draft(post, tool_name, text):
    if post != "material-site" or tool_name != TOOL:
        return None
    data = parse(text)
    rows = _with_units(data.rows)
    owner = (("分部或部位", "location"), ("材料名称", "material"), ("规格", "model"), ("材料批次", "batch"),
             ("核算期间", "period"), ("材料单位（用户原文）", "unit"), ("材料单位待核", "unit_note"), ("记录编号", "record_id"),
             ("项目名称", "project"), ("辖区", "jurisdiction"), ("输入资料名", "_origin"))
    full = owner+(("用户已完工程量（未核）", "work"), ("工程量来源", "work_source"), ("工程量单位（用户原文）", "work_unit"), ("工程量单位待核", "work_unit_note"),
                 ("用户消耗指标（未核）", "indicator"), ("指标来源", "indicator_source"), ("用户指标口径原文", "indicator_note"), ("用户损耗率原文", "loss"),
                 ("用户应耗 / 计划量（未核）", "expected"), ("计划来源", "plan_source"),
                 ("用户本期领料原文", "issued"), ("领料来源", "issue_source"), ("用户退料原文", "returned"), ("退料来源", "return_source"),
                 ("用户盘点记录（未核）", "count_record"), ("用户盘点实存原文（未核）", "counted"), ("盘点来源", "count_source"),
                 ("用户盘点调整原文（未核）", "adjustment"), ("调整依据", "adjustment_source"), ("用户实耗原文（未核）", "actual"), ("实耗来源", "actual_source"),
                 ("用户节超原文（未核）", "variance"), ("节超来源", "variance_source"), ("用户正负符号约定", "sign_convention"), ("原因类型原文", "cause"),
                 ("用户单价原文（未核）", "price"), ("单价来源", "price_source"))
    return "\n".join([
        "# 材料核算表头（AI 草稿 · 内部讨论）", "", DISCLAIMER, "", "- submit_blocked=true", "",
        _table(data.contexts, (("项目名称", "project"), ("核算期间", "period"), ("辖区", "jurisdiction"), ("核算版本", "version"), ("输入资料名", "_origin"))), "",
        "## 1 草稿声明", "", "编制、复核与签认：（空）。无盘点不编盈亏；本稿不是仓库收发、工程求解或造价组价结果。", "",
        "## 2 核算对象", "", "### 用户明示主要 / 实体材料", "", _table([r for r in rows if r.get("kind") in {"主要材料", "实体", "实体材料"}], owner+(("用户材料类别", "kind"), ("甲指 / 自采等用户供料方式", "supply"))), "",
        "### 用户明示周转材料", "", _table([r for r in rows if r.get("kind") in {"周转", "周转材料"}], owner+(("用户材料类别", "kind"), ("甲指 / 自采等用户供料方式", "supply"))), "",
        "### 材料类别待核", "", _table([r for r in rows if r.get("kind") not in {"主要材料", "实体", "实体材料", "周转", "周转材料"}], owner+(("用户材料类别", "kind"), ("甲指 / 自采等用户供料方式", "supply"))), "",
        "不同部位、期间、规格与批次分别登记。工程量单位、材料单位及原数量单位不互相替换。", "",
        "## 3 应耗量口径 / 计划用量", "", _table(rows, owner+(("用户已完工程量", "work"), ("工程量来源", "work_source"), ("工程量单位", "work_unit"), ("工程量单位待核", "work_unit_note"),
            ("用户消耗指标", "indicator"), ("指标来源", "indicator_source"), ("用户指标口径原文", "indicator_note"), ("用户损耗率原文", "loss"),
            ("用户应耗 / 计划量", "expected"), ("计划来源", "plan_source")), extra=(("本轮应耗计算", "TBD"),)), "",
        "工程量与指标须有对应来源及可比口径；系数不擅自改为百分比，损耗率不擅自改为乘算系数。缺基准不补经验消耗。", "",
        "## 4 实耗量口径 / 领退记录", "", _table(rows, owner+(("用户本期领料", "issued"), ("领料来源", "issue_source"), ("用户退料", "returned"), ("退料来源", "return_source"),
            ("用户实耗（未核）", "actual"), ("实耗来源", "actual_source")), extra=(("本轮实耗计算", "TBD"),)), "",
        _table(_owned(data, data.events), owner+(("数量记录项", "field"), ("用户单笔数量 / 指标原文", "quantity"), ("本笔来源", "source"), ("输入位置（非原件页码）", "input_position")),
            extra=(("本轮核验 / 汇总", "未执行"),)), "",
        "多笔领退与来源逐笔保留，不合并为伪汇总；领料减退料不自动成为实耗，库存和待确认调整也不代入。", "",
        "## 5 节超口径 / 盘点", "", _table(rows, owner+(("用户盘点记录（未核）", "count_record"), ("用户盘点实存原文（未核）", "counted"),
            ("用户盘点日期", "count_date"), ("盘点来源", "count_source"), ("用户盘点调整原文（未核）", "adjustment"), ("调整依据", "adjustment_source"),
            ("用户正负符号约定", "sign_convention")), extra=(("盘点 / 调整核验", "本轮未执行"),)), "",
        "盘点实存、小票差异、调整与盈亏不是同一字段。差异只保留记录，不视为库存或已确认调整。用户符号约定未给时保持待核，不擅自选择正负方向。", "",
        "## 6 核算表头 / 节超待核", "", _table(rows, full, extra=(("本轮应耗", "TBD"), ("本轮实耗", "TBD"), ("本轮节超 / 节超率", "TBD"), ("本轮单价", "TBD"), ("本轮合价", "TBD"))), "",
        "无盘点或计划基准不宣布盈亏、节超或可结算金额。用户提供的数量、指标、实耗与节超仅为原文登记，未核原件不成为本轮核定值。", "",
        "## 7 原因类型 / 责任", "", _table(rows, owner+(("用户原因类型", "cause"), ("用户责任方", "owner"), ("变更资料", "change"), ("隔离记录", "isolation"),
            ("用户待核记录", "record"), ("未分派资料来源", "source"))), "",
        "原因与责任只抄用户。不将未盘点、未计量、隔离或雨损陈述改写为损失、材料质量结论或已成立的索赔。", "",
        "## 8 周转材料", "", _table(rows, owner+(("用户进场原文", "arrival"), ("用户在用原文", "in_use"), ("用户维修原文", "repair"),
            ("用户报废原文", "scrapped"), ("用户退租原文", "return_rental"), ("用户摊销方法", "amortization")), extra=(("本轮摊销 / 库存计算", "TBD"),)), "",
        "各状态数量不互相代填。不计算摊销率、会计分录、退租结算或合格实耗。", "",
        "## 9 节奏与会签 / 闭环", "", "工程量、指标、领退、盘点、变更及价格各自追溯原件，责任人核对部位、期间、规格、单位与签认。", "",
        "| 施工 / 商务 | 仓管 | 材料员 | 复核 |\n| --- | --- | --- | --- |\n| （空） | （空） | （空） | （空） |", "",
        "## 10 禁令与证据位置", "", _table(_owned(data, data.evidence), owner+(("记录项", "field"), ("用户证据原文", "value"), ("输入位置（非原件页码）", "input_position"))), "",
        "不摘录定额全文，不默写规范编号、损耗百分比、币种或法规版本；现行依据与条款待核 / UNSPECIFIED。", "",
    ])
