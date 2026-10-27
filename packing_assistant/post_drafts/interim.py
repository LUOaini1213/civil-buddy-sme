"""Literal interim measurement records, not a payment certificate or solver.

Declared, completed, previous/cumulative, supervisor and owner quantities have
distinct roles and sources. A signed document never supplies a missing quantity,
unit, rate, approval or payable amount. Sources and periods own separate rows.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

from packing_assistant.post_drafts.planning import _inputs, _table
from packing_assistant.post_drafts.procurement import _source_name

MISSING = "[A001] / UNSPECIFIED"
TOOL = "interim__measure"
DISCLAIMER = ("AI 草稿 · 内部讨论 · 不得作为法定专项方案 / 交底签认件。本稿仅登记用户验工资料，"
              "未审核实物工程量、质量、计量口径、清单单价或确认凭证，不计算核准量、计价值、税额或应付款。"
              "不是已核准验工报表、付款证书、结算或支付指令，不替代合同核准人的真实审核与签认。")
_NUMBER = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_UNIT = r"(?:立方米|平方米|立方|平米|吨|方|袋|根|块|卷|个|套|件|米|t|kg|m[³3]|m[²2]|m)"
_QTY_VALUE = re.compile(_NUMBER+r"(?:\s*"+_UNIT+r")?", re.I)
_QTY_START = re.compile(r"^(?:是|为|了|数量为|数量是|量为)?\s*("+_NUMBER+r"(?:\s*"+_UNIT+r")?)(?![A-Za-z0-9_./-])", re.I)
_DATE = r"(?:\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?|\d{1,2}月\d{1,2}[日号])"
_MODEL = re.compile(r"(?<![A-Za-z0-9_.-])(?:HRB\d+E?|HPB\d+|C\d+(?:P\d+)?|AC-\d+[A-Z]?|SBS)(?![A-Za-z0-9_.-])", re.I)
_LOCATION = re.compile(r"\d+[#＃]楼(?:[一二三四五六七八九十\d]+层)?(?:主体|二次结构|墙柱|顶板|底板|砌体)?|地下室(?:B\d+层)?(?:底板|外墙)?")
_MATERIAL = re.compile(r"承台混凝土|沥青混凝土面层|加气块(?:砌体)?|钢筋|混凝土|砌体|防水卷材|模板|螺纹钢")
_UNKNOWN = re.compile(r"未提供|未给|未定|未知|待填|待核|待补|没给|没定|没报|还没|没有|忘了|没带|先留空|留空|多少|UNSPECIFIED|TBD|N/A|unknown|not provided", re.I)
_UNSAFE = re.compile(r"例如|比如|假设|如果|示例|样例|自己算|自行计算|不采用|别按|不要|不是|是否|[？?]|\b(?:for example|assuming|hypothetical|whether)\b", re.I)
_PENDING = re.compile(r"预计|计划|拟|估计|感觉|大概|大约|约(?=\s*\d)|应该|听说|据说|明天|下周|尚未|还没|未审|没审|未核|未确认|没确认|没看|待确认|(?:未|没)\s*(?:报|完成|签|确认|审|核)|\b(?:planned|forecast|estimated|reportedly|unconfirmed|not|will|tomorrow)\b", re.I)
_FIELDS = {
    "project": ("项目名称", "工程名称", "项目", "Project"), "jurisdiction": ("辖区", "Jurisdiction"),
    "contract": ("合同名称", "合同编号", "合同", "Contract"), "lot": ("合同标段", "标段", "Lot"),
    "period": ("计量期间", "验工期间", "期次", "期间", "Period"), "begin": ("起始日期", "开始日期", "起算日期", "Start date"),
    "end": ("截止日期", "截止", "结束日期", "End date"), "version": ("计量版本", "版本", "Version"),
    "code": ("清单编码", "清单编号", "清单号", "子目编号", "子目号", "编码", "BOQ code"),
    "name": ("清单名称", "清单项", "子目名称", "名称", "Name"), "model": ("项目特征", "规格型号", "规格", "Model"),
    "location": ("计量部位", "部位", "Location"), "unit": ("计量单位", "单位", "Unit"),
    "contract_qty": ("合同工程量", "合同数量", "合同量", "Contract quantity"), "contract_source": ("合同量来源", "Contract source"),
    "previous": ("上期末开累", "上期累计", "上期末累计", "Previous cumulative"), "previous_source": ("上期累计来源", "上期开累来源", "Previous source"),
    "completed": ("本期完成量", "本期完成", "Completed quantity"), "completed_source": ("本期完成来源", "Completed source"),
    "declared": ("本期申报量", "本期申报", "本期用户量", "申报量", "Declared quantity"), "declared_source": ("申报量来源", "申报来源", "Declared source"),
    "cumulative": ("本期末累计", "本期末开累", "累计申报量", "Cumulative quantity"), "cumulative_source": ("本期累计来源", "Cumulative source"),
    "unassigned": ("用户未分派数量", "未分派数量", "数量", "Unassigned quantity"),
    "supervisor": ("监理审核量", "监理确认量", "监理核量", "总监审核量", "监理审", "监理量", "Supervisor quantity"),
    "supervisor_source": ("监理确认来源", "监理审核来源", "监理来源", "Supervisor source"),
    "supervisor_record": ("监理审核记录", "监理记录", "Supervisor record"),
    "owner_qty": ("业主确认量", "业主核准量", "业主审核量", "业主审", "业主核", "建设单位确认量", "建设单位核", "甲方确认量", "甲方核", "发包人确认量", "发包人核准量", "核准量", "Owner quantity"),
    "owner_source": ("业主确认来源", "业主来源", "确认来源", "Owner source"),
    "owner_record": ("业主确认记录", "业主记录", "Owner record"), "owner": ("业主代表", "合同核准人", "Owner representative"),
    "rule": ("计量口径", "计算规则", "Measurement rule"), "measurement_source": ("计量附件", "计量依据", "Measurement source"),
    "quality": ("质量证明资料", "检验资料", "Quality evidence"), "drawing": ("图纸依据", "Drawing"),
    "price": ("合同单价", "用户单价", "单价", "Contract rate"), "price_source": ("单价来源", "价格依据", "Rate source"),
    "claimed": ("用户申报金额", "申报金额", "Claimed amount"), "claimed_source": ("申报金额来源", "Claimed source"),
    "owner_amount": ("用户业主核准金额", "业主核准金额", "Owner amount"), "amount_source": ("核准金额来源", "Amount source"),
    "tax_basis": ("用户税口径", "价税口径", "Tax basis"), "currency": ("币种", "Currency"),
    "change": ("变更项目", "变更资料", "变更", "Variation"), "change_doc": ("变更文号", "变更来源", "Variation source"),
    "change_status": ("变更状态", "用户变更批准记录", "Variation status"), "change_amount": ("用户变更金额", "变更金额", "Variation amount"),
    "adjustment": ("调价依据", "调价资料", "Adjustment"), "claim": ("索赔资料", "索赔记录", "Claim"),
    "certificate": ("付款证书编号", "付款证书", "支付证书", "Payment certificate"),
    "certificate_source": ("付款证书来源", "Certificate source"), "payment": ("支付资料", "付款资料", "Payment evidence"),
    "attachments": ("附件目录", "附件", "Attachments"), "wage": ("用户工资款", "农民工工资款", "Wage amount"),
    "advance": ("用户预付款抵扣", "预付款抵扣", "Advance deduction"), "supplied": ("用户甲供材扣款", "甲供材扣款", "Supplied deduction"),
    "retention": ("用户质保金", "质保金", "Retention"), "penalty": ("用户违约金", "违约金", "Penalty"),
    "deduction_source": ("扣减依据", "扣减来源", "Deduction source"), "warning": ("待核事项", "警示记录", "Pending"),
    "wage_source": ("工资款来源", "Wage source"), "advance_source": ("预付款抵扣来源", "Advance source"),
    "supplied_source": ("甲供材扣款来源", "Supplied source"), "retention_source": ("质保金来源", "Retention source"),
    "penalty_source": ("违约金来源", "Penalty source"), "change_amount_source": ("变更金额来源", "Variation amount source"),
    "source": ("来源文件", "资料来源", "来源", "Source"), "record": ("用户补充记录", "记录", "Record"),
}
ALIASES = {a.casefold(): k for k, labels in _FIELDS.items() for a in labels}
_LABEL = re.compile(r"^("+"|".join(re.escape(a) for a in sorted(ALIASES, key=len, reverse=True))+r")(?:\s*[:：=]\s*|\s+)(.*)$", re.I)
_QUANTITIES = {"contract_qty", "previous", "completed", "declared", "cumulative", "supervisor", "owner_qty", "unassigned"}
_MONEY = {"claimed", "owner_amount", "change_amount", "wage", "advance", "supplied", "retention", "penalty"}
_SOURCES = {"contract_qty": "contract_source", "previous": "previous_source", "completed": "completed_source", "declared": "declared_source", "cumulative": "cumulative_source",
            "supervisor": "supervisor_source", "owner_qty": "owner_source", "price": "price_source", "claimed": "claimed_source", "owner_amount": "amount_source",
            "wage": "wage_source", "advance": "advance_source", "supplied": "supplied_source", "retention": "retention_source", "penalty": "penalty_source", "change_amount": "change_amount_source"}
_META = {"project", "jurisdiction", "contract", "lot", "period", "begin", "end", "version", "currency"}


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
    if key in _QUANTITIES | _MONEY | {"price", "code", "name", "model", "owner", "location", "begin", "end"} and (_UNKNOWN.search(value) or _UNSAFE.search(value) or _PENDING.search(value)):
        return MISSING
    if key in _QUANTITIES and not _QTY_VALUE.fullmatch(value):
        return MISSING
    if key in {"begin", "end"} and not re.fullmatch(_DATE, value):
        return MISSING
    # Price/amount are literal monetary statements, never quantity products.
    currency = r"(?:SGD|CNY|RMB|USD|EUR|GBP|HKD|AUD|JPY|CHF|CAD)"
    if key in _MONEY | {"price"} and not re.fullmatch(r"(?:"+currency+r"|S\$|[$€£¥])?\s*"+_NUMBER+r"\s*(?:万元|元|"+currency+r")?(?:\s*/\s*"+_UNIT+r")?", value, re.I):
        return MISSING
    return value


def _details(key, value):
    pairs = [(key, value)]
    if key in _QUANTITIES | _MONEY | {"price"} and _typed(key, value) == MISSING and _value(value) != MISSING:
        pairs.append(("record", _FIELDS[key][0]+"："+value))
    if key == "name" and _typed(key, value) != MISSING:
        models = list(_MODEL.finditer(value))
        if len(models) == 1:
            pairs = [(key, value[:models[0].start()]+value[models[0].end():]), ("model", models[0].group())]
    if key == "period":
        dates = re.findall(_DATE, value)
        if len(dates) == 2 and re.search(r"到|至|~|～", value):
            pairs += [("begin", dates[0]), ("end", dates[1])]
    return pairs


def _scan(text):
    text = re.sub(r"^(?:本次实际情况|实际情况|真实记录)\s*[:：]\s*", "", text)
    short_command = re.fullmatch(r"(?:写|出|生成|起草|编制)(?:一份)?(?:对上)?验工计价(?:草稿|表)?\s+(.+)", text)
    if short_command:
        text = short_command.group(1)
    # A qualifier in another role must not erase an explicitly stated value:
    # "owner confirmed 30, but rate unknown" still has a user owner quantity.
    parts = re.split(r"(?:但是|不过|但|而)(?=(?:监理|业主|建设单位|发包人|甲方|单价|合同量|本期|上期|累计|预计|计划|听说|据说))", text)
    if len(parts) > 1:
        return [pair for part in parts for pair in _scan(part)]
    match = _LABEL.match(text)
    if match:
        return _details(ALIASES[match.group(1).casefold()], match.group(2))
    if _UNSAFE.search(text):
        return []
    unassigned = re.fullmatch(r"(.+?)\s+("+_NUMBER+r"\s*"+_UNIT+r")", text, re.I)
    role_label = r"(?:监理|总监|业主|建设单位|发包人|甲方)(?:已经|已)?(?:审|核|确认)|合同(?:工程量|数量|量)|本期|上期|清单项"
    if unassigned and not re.search(role_label, unassigned.group(1)):
        return _details("name", unassigned.group(1))+[("unassigned", unassigned.group(2))]
    pairs = []
    match = re.match(r"^合同编号\s*(\S+)$", text)
    if match:
        return [("contract", match.group(1))]
    match = re.match(r"^(?:计量期间|验工期间)\s*(.+)$", text)
    if match:
        return _details("period", match.group(1))
    match = re.match(r"^(?:截止日期|截止)\s*("+_DATE+r")$", text)
    if match:
        return [("end", match.group(1))]
    if "验工计价" in text:
        period = re.search(r"\d{4}年第\d+季度|[一二三四五六七八九十\d]+月", text)
        if period:
            pairs.append(("period", period.group()))
    # An explicit later reference is not a newly declared item. Resolve only
    # a unique item in this source epoch; ambiguous references stay unassigned.
    reference = re.search(r"上期末开累.*?(钢筋|加气块|砌体|混凝土)\s*是\s*(.+)$", text)
    if reference:
        quantity = _QTY_START.match(reference.group(2))
        return [("reference_name", reference.group(1)), ("previous", quantity.group(1))] if quantity else [("record", text)]
    item = re.search(r"清单项\s*(.+?)(?=本期|这期|申报|$)", text)
    location = _LOCATION.search(text)
    material = _MATERIAL.search(text)
    if item:
        pairs += _details("name", item.group(1))
    elif material and (location or re.match(r"^"+re.escape(material.group()), text)):
        if location:
            pairs.append(("location", location.group()))
        pairs += _details("name", material.group())
        model = _MODEL.search(text)
        if model:
            pairs.append(("model", model.group()))
    patterns = {
        "contract_qty": r"合同(?:工程量|数量|量)", "previous": r"上期末开累|上期末累计|上期累计",
        "declared": r"本期申报|这期报|本期报|报了", "completed": r"本期完成量?|完成工程量",
        "cumulative": r"本期末累计|本期末开累|累计申报量",
        "supervisor": r"(?:监理|总监)(?:已经|已)?(?:审了|审核了|核下来|审核量|确认量|核量|审核|审|核)",
        "owner_qty": r"(?:业主|建设单位|发包人|甲方)(?:已经|已)?(?:确认量|核准量|审核量|核了|确认了|核准了|审了|审核了|核准|审核|确认|核|审)",
    }
    for key, label in patterns.items():
        start = re.search(label, text)
        if start:
            quantity = _QTY_START.match(text[start.end():].strip())
            if _UNKNOWN.search(text) or _PENDING.search(text):
                pairs.append(("record", text))
            elif quantity:
                pairs.append((key, quantity.group(1)))
    document = re.search(r"业主(?:计量单|确认单)\s*([A-Za-z0-9][A-Za-z0-9_.-]*)", text)
    if document:
        pairs += [("owner_source", document.group(1)), ("owner_record", text)]
    elif re.search(r"业主|监理", text) and not any(k in _QUANTITIES for k, _ in pairs):
        pairs.append(("owner_record" if "业主" in text else "supervisor_record", text))
        if _PENDING.search(text) or _UNKNOWN.search(text):
            pairs.append(("record", text))
    if re.search(r"单价|付款|支付|变更|索赔", text) and (_PENDING.search(text) or _UNKNOWN.search(text)):
        pairs.append(("record", text))
    return pairs


def parse(raw):
    data = Data()
    scope = {"jurisdiction": "UNSPECIFIED"}
    data.contexts.append(scope)
    origin, current, events = "", None, {}
    file_epoch = 0

    def ref():
        return next((i for i, row in enumerate(data.rows) if row is current), None)

    def new(values=None):
        row = {"_origin": origin, "_scope": len(data.contexts)-1, "_file": file_epoch,
               **{k: scope.get(k, "UNSPECIFIED" if k == "jurisdiction" else MISSING) for k in _META}, **(values or {})}
        data.rows.append(row)
        return row

    def scoped_value(key):
        value = current.get(key) if current is not None else None
        return scope.get(key) if value in {None, MISSING, "UNSPECIFIED"} else value

    def matches_scope(row):
        return row["_file"] == file_epoch and all(
            scope.get(k) in {None, MISSING, "UNSPECIFIED"} or row.get(k) in {None, MISSING, "UNSPECIFIED", scope[k]}
            for k in _META)

    def resolve(found):
        row = found[0] if len(found) == 1 else new()
        if len(found) == 1:
            for key in _META:
                if row.get(key) in {None, MISSING, "UNSPECIFIED"} and scope.get(key) not in {None, MISSING, "UNSPECIFIED"}:
                    row[key] = scope[key]
        return row

    def put(target, key, value):
        value = _typed(key, value)
        if target.get(key) in {None, MISSING, "UNSPECIFIED"}:
            target[key] = value
        elif value != MISSING and value not in target[key].split("；"):
            target[key] += "；"+value
        if target is current and key in _QUANTITIES | _MONEY | {"price"}:
            event = {"_record": ref(), "field": _FIELDS[key][0], "quantity": value, "input_position": position}
            data.events.append(event)
            events[key] = event
        elif target is current and key in _SOURCES.values():
            role = next(k for k, source_key in _SOURCES.items() if source_key == key)
            if role not in events:
                events[role] = {"_record": ref(), "field": _FIELDS[role][0], "quantity": MISSING, "input_position": position}
                data.events.append(events[role])
            event = events[role]
            if event.get("source") in {None, MISSING}:
                event["source"] = value
            elif value != MISSING and value not in event["source"].split("；"):
                event["source"] += "；"+value
        if value not in {MISSING, "UNSPECIFIED"}:
            data.evidence.append({"_record": ref(), "field": _FIELDS[key][0], "value": value, "input_position": position, "_origin": origin})

    for content, is_table, offset, source in _inputs(raw):
        position = (origin+" · " if origin else "")+f"本轮输入第{raw[:offset].count(chr(10))+1}行（非原件页码）"
        if content is None:
            current, events = None, {}
            continue
        marker = _source_name(source)
        if marker is not None or source.startswith(("【本轮用户要求", "【此前用户资料")):
            origin = marker if marker is not None else source.strip("【】")
            file_epoch += 1
            scope = {"_origin": origin, "jurisdiction": "UNSPECIFIED"}
            data.contexts.append(scope)
            current, events = None, {}
            continue
        pairs = ([(k, v) for label, value in content if label.casefold() in ALIASES for k, v in _details(ALIASES[label.casefold()], value)]
                 if is_table else _scan(source))
        if not pairs:
            continue
        stated = dict(pairs)
        if not is_table and any(k in stated and scoped_value(k) not in {None, MISSING, "UNSPECIFIED", _typed(k, stated[k])} for k in _META):
            scope = {"_origin": origin, "jurisdiction": "UNSPECIFIED"}
            data.contexts.append(scope)
            current, events = None, {}
        if is_table:
            current, events = new(), {}
        elif "reference_name" in stated:
            name = _typed("name", stated["reference_name"])
            found = [r for r in data.rows if matches_scope(r) and name != MISSING and name in r.get("name", MISSING)]
            current, events = resolve(found), {}
            if len(found) != 1:
                pairs.append(("record", source))
            pairs = [p for p in pairs if p[0] != "reference_name"]
        elif "name" in stated and (current is None or current.get("name") not in {None, MISSING}):
            current, events = new(), {}
        elif "code" in stated and _typed("code", stated["code"]) != MISSING:
            code = _typed("code", stated["code"])
            found = [r for r in data.rows if matches_scope(r) and r.get("code") == code]
            if current is None or current.get("code") not in {None, MISSING, code} or len(found) > 1:
                current, events = resolve(found), {}
        if current is not None and any(k in stated and _typed(k, stated[k]) != MISSING and current.get(k) not in {None, MISSING, _typed(k, stated[k])} for k in {"model", "location"}):
            values = {"name": current.get("name", MISSING)}
            values.update({k: _typed(k, stated[k]) for k in {"model", "location", "code"} if k in stated})
            current, events = new(values), {}
        if is_table:
            pairs = [p for p in pairs if p[0] not in _SOURCES.values()]+[p for p in pairs if p[0] in _SOURCES.values()]
        for key, value in pairs:
            if key in _META and current is None:
                put(scope, key, value)
                continue
            if current is None:
                current = new()
            put(current, key, value)
    data.contexts = [r for r in data.contexts if len(r) > 1] or data.contexts[:1]
    return data


def _with_units(rows):
    result = []
    for original in rows:
        row, units = dict(original), set()
        for key in _QUANTITIES:
            for value in row.get(key, "").split("；"):
                match = re.fullmatch(_NUMBER+r"\s*("+_UNIT+r")", value, re.I)
                if match:
                    units.add(match.group(1))
        if row.get("unit") in {None, MISSING} and len(units) == 1:
            row["unit"] = next(iter(units))
        if len(units) > 1 or row.get("unit") not in {None, MISSING} and any(u != row["unit"] for u in units):
            row["unit_note"] = "单位原文不一致，待核："+" / ".join(sorted(units))
        result.append(row)
    return result


def _owned(data, records):
    owners = _with_units(data.rows)
    return [{**(owners[r["_record"]] if isinstance(r.get("_record"), int) else {}), **r} for r in records]


def build_draft(post, tool_name, text):
    if post != "interim" or tool_name != TOOL:
        return None
    data = parse(text)
    rows = _with_units(data.rows)
    owner = (("清单编码", "code"), ("清单名称", "name"), ("项目特征 / 规格", "model"), ("计量部位", "location"),
             ("计量单位（用户原文）", "unit"), ("单位待核", "unit_note"), ("计量期间 / 期次", "period"), ("起始日期", "begin"), ("截止日期", "end"),
             ("合同名称 / 编号", "contract"), ("标段", "lot"), ("项目名称", "project"), ("辖区", "jurisdiction"), ("币种原文", "currency"), ("输入资料名", "_origin"))
    quantities = (("用户合同量（未核）", "contract_qty"), ("合同量来源", "contract_source"), ("用户上期末开累（未核）", "previous"), ("上期累计来源", "previous_source"),
                  ("用户本期完成量（未核）", "completed"), ("本期完成来源", "completed_source"), ("用户本期申报量（未核）", "declared"), ("申报量来源", "declared_source"),
                  ("用户本期末累计（未核）", "cumulative"), ("本期累计来源", "cumulative_source"), ("用户监理审核量（未核）", "supervisor"), ("监理确认来源", "supervisor_source"),
                  ("用户业主确认量（未核）", "owner_qty"), ("业主确认来源（未核）", "owner_source"), ("用户未分派数量原文（口径待核）", "unassigned"))
    full = owner+quantities+(("用户合同单价原文（未核）", "price"), ("单价来源", "price_source"), ("用户申报金额（未核）", "claimed"), ("申报金额来源", "claimed_source"),
                            ("用户业主核准金额（未核）", "owner_amount"), ("核准金额来源", "amount_source"), ("用户价税口径", "tax_basis"))
    return "\n".join([
        "# 对上验工计价草稿（AI 草稿 · 内部讨论）", "", DISCLAIMER, "", "- submit_blocked=true", "",
        _table(data.contexts, tuple((h, k) for h, k in owner if k in _META | {"_origin"})), "",
        "## 1 封面与草稿声明", "", "无业主或合同约定核准人的真实确认，不编本期应付金额。编制 / 审核 / 核准签字：（空）。", "",
        "## 2 原则", "", "本期、上期末与累计记录分列。用户完成量、申报量、监理量、业主量不互相代填。照片不换算工程量，对下结算不当作对上已完。", "",
        "## 3 本期范围", "", _table(rows, owner), "", "不同合同、标段、期间、清单项、规格和资料版本单独登记；不补默认计量周期或日历日期。", "",
        "## 4 计量依据", "", _table(rows, owner+(("用户计量口径", "rule"), ("计量依据 / 附件", "measurement_source"), ("用户图纸依据", "drawing"), ("用户质量证明资料", "quality"))), "",
        "资料名称仅为用户记录，未抽取原件、未核验质量或计算规则，不据此宣布计量成立。", "",
        "## 5 计量草表", "", _table(rows, full, extra=(("本轮核准量", "TBD"), ("本轮累计计算", "TBD"), ("本轮单价核定", "TBD"), ("本轮价款 / 税额", "TBD"), ("本轮应付金额", "TBD"))), "",
        _table(_owned(data, data.events), owner+(("数量 / 价款记录项", "field"), ("用户单笔原文", "quantity"), ("本笔来源", "source"), ("输入位置（非原件页码）", "input_position")), extra=(("本轮汇总 / 核验", "未执行"),)), "",
        "多笔申报与来源逐笔登记，不求和、不补累计、不减出审减量、不用监理量代业主量。单位不转换，价税不混算。", "",
        "### 用户确认记录", "", _table(rows, owner+(("用户监理审核记录（未核）", "supervisor_record"), ("用户业主确认记录（未核）", "owner_record"), ("用户业主代表 / 核准人", "owner")), extra=(("本轮确认凭证核验", "未执行"),)), "",
        "签过计量单不等于本轮已核准，也不自动给出确认数量、合同单价或应付金额。", "",
        "## 6 变更、物价、索赔", "", _table(rows, owner+(("用户变更项目", "change"), ("变更文号 / 来源", "change_doc"), ("用户变更状态（未核）", "change_status"), ("用户变更金额原文（未核）", "change_amount"), ("变更金额来源", "change_amount_source"), ("用户调价依据", "adjustment"), ("用户索赔资料", "claim")), extra=(("本轮纳入计价金额", "TBD"),)), "",
        "变更、调价、索赔分别留接口；未核原件与批准权限，不据用户状态计算纳入金额或形成支付结论。", "",
        "## 7 过程结算与进度款", "", _table(rows, owner+(("用户付款证书编号 / 原文（未核）", "certificate"), ("付款证书来源", "certificate_source"), ("用户支付资料", "payment"), ("用户申报金额原文（未核）", "claimed"), ("用户业主核准金额原文（未核）", "owner_amount"), ("附件目录", "attachments")), extra=(("本轮签发 / 支付", "未执行"),)), "",
        "请款、计量、核准与实付不是同一状态。未代签证书，不补适用法律、支付比例或付款时限。", "",
        "## 8 农民工工资列示", "", _table(rows, owner+(("用户工资款原文（未核）", "wage"), ("工资款来源", "wage_source")), extra=(("本轮工资款核定", "TBD"),)), "",
        "工资款单列，不写工资已结清，不执行转款。", "",
        "## 9 扣减与预留", "", _table(rows, owner+(("用户预付款抵扣（未核）", "advance"), ("预付款抵扣来源", "advance_source"), ("用户甲供材扣款（未核）", "supplied"), ("甲供材扣款来源", "supplied_source"),
            ("用户质保金原文（未核）", "retention"), ("质保金来源", "retention_source"), ("用户违约金原文（未核）", "penalty"), ("违约金来源", "penalty_source"), ("未分派扣减依据", "deduction_source")), extra=(("本轮扣减 / 预留计算", "TBD"),)), "",
        "扣减只保留用户记录，不补比例或把申报额减成应付款。", "",
        "## 10 不予计价警示", "", _table(rows, owner+(("用户待核事项", "warning"), ("用户补充 / 未确认记录", "record"), ("未分派来源", "source"))), "",
        "核对重复、跨期、未完成、质量及变更接口，仅提示核对，不作虚假验工指控、质量或计价成立结论。", "",
        "## 11 报审签认", "", "编制、监理审核、合同核准人签字：（空）。本轮只整理内部草稿，未提交、未审核、未签发。", "",
        "## 12 自检", "", _table(_owned(data, data.evidence), owner+(("记录项", "field"), ("用户证据原文", "value"), ("输入位置（非原件页码）", "input_position"))), "",
        "金额、税额、比例和现行依据未核则保持 TBD / UNSPECIFIED；不编条款号、默认国家或法规版本，不作应付或审核结论。", "",
    ])
