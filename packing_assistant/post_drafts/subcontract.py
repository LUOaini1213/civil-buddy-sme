"""Literal subcontract records; no settlement, offset or payment approval.

Contract/party, period, item and file identities own independent quantities,
rates, proposed deductions, payment records, variations and wage materials.
Applications, foreman records and upstream confirmations never substitute for
each other. Living advances are not a statement that wages were paid in full.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

from packing_assistant.post_drafts.planning import _inputs, _table
from packing_assistant.post_drafts.procurement import _source_name

MISSING = "[A001] / UNSPECIFIED"
TOOL = "subcontract__sheet"
DISCLAIMER = ("AI 草稿 · 内部讨论 · 不得作为法定专项方案 / 交底签认件。本稿只整理用户对下结算资料，"
              "未核实合同关系、实物工程量、计量依据、扣款凭证、支付记录或工资材料。"
              "不是生效结算协议、工资发放清单、付款证书或支付批准，不计算合价、应付、净额、税额或抵扣。")
_NUMBER = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_UNIT = r"(?:平方米|立方米|平米|工日|吨|方|平|米|个|套|件|t|kg|m[²2]|m[³3]|m)"
_QTY = re.compile(r"(?:约\s*)?"+_NUMBER+r"(?:\s*"+_UNIT+r")?", re.I)
_QTY_START = re.compile(r"^(?:是|为|量是|量为|数量是|数量为)?\s*((?:约\s*)?"+_NUMBER+r"(?:\s*"+_UNIT+r")?)(?![A-Za-z0-9_.#/-])", re.I)
_CURRENCY = r"(?:SGD|CNY|RMB|USD|EUR|GBP|HKD|AUD|JPY|CHF|CAD)"
_MONEY_VALUE = r"(?:"+_CURRENCY+r"|S\$|[$€£¥])?\s*"+_NUMBER+r"\s*(?:万元|元|"+_CURRENCY+r")?"
_RATE_VALUE = _MONEY_VALUE+r"(?:(?:\s*/\s*|\s*一|\s*每)"+_UNIT+r")?"
_MONEY_END = r"(?![A-Za-z0-9_.#/-]|吨|公斤|千克|平方米|立方米|平米|平方|工日|平|方|米|台|人)"
_DATE = r"(?:\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?|\d{1,2}月\d{1,2}[日号]|今天|明天|昨日|今日|本月|这个月|上月|下月)"
_LOCATION = re.compile(r"\d+[#＃]楼(?:[一二三四五六七八九十\d]+层|标准层)?(?:地下室)?(?:主体|顶板|底板|外架|屋面|墙柱)?|地下室(?:B\d+层)?(?:顶板|底板|外墙)?")
_UNKNOWN = re.compile(r"未提供|未给|未定|未知|待填|待核|待补|待确认|没给|没定|没报|还没|没有|忘了|没带|回头再找|多少|UNSPECIFIED|TBD|N/A|unknown|not provided", re.I)
_UNSAFE = re.compile(r"例如|比如|假设|如果|示例|样例|自己算|自行计算|不采用|别按|不是|是否|[？?]|\b(?:for example|assuming|hypothetical|whether)\b", re.I)
_PENDING = re.compile(r"预计|计划|拟|估计|感觉|大概|应该|听说|据说|明天|下周|将|尚未|还没|未审|没审|未核|未确认|没确认|没看|待确认|(?:未|没)\s*(?:付|支付|转账|签|确认|审|核)|\b(?:planned|forecast|estimated|reportedly|unconfirmed|not|will|tomorrow)\b", re.I)
_FIELDS = {
    "project": ("项目名称", "工程名称", "项目", "Project"), "jurisdiction": ("辖区", "Jurisdiction"),
    "period": ("结算期间", "结算期", "结算周期", "期间", "期次", "Period"),
    "begin": ("结算起始日期", "开始日期", "Start date"), "end": ("结算结束日期", "截止日期", "End date"),
    "version": ("结算版本", "版本", "Version"), "currency": ("币种", "Currency"),
    "company": ("合同分包", "分包单位", "分包商", "分包方", "劳务单位", "班组", "Subcontractor"),
    "contract": ("分包合同编号", "合同编号", "合同号", "分包合同", "Contract"),
    "kind": ("分包类型", "分包性质", "Contract type"), "pricing": ("计价方式", "计价口径", "Pricing basis"),
    "upstream_period": ("对上验工期次", "对上期次", "Upstream period"), "link": ("对上周期关系", "周期挂钩", "Period link"),
    "name": ("分项名称", "清单名称", "任务名称", "分项", "Name"), "code": ("清单编号", "清单编码", "子目编号", "BOQ code"),
    "location": ("计量部位", "施工部位", "部位", "Location"), "unit": ("计量单位", "单位", "Unit"),
    "rule": ("计量规则", "计量口径", "Measurement rule"), "task": ("任务单编号", "任务单", "派工单", "Task sheet"),
    "task_source": ("任务单来源", "计量依据", "Task source"), "declared": ("申请工程量", "申报工程量", "申请量", "申报量", "Declared quantity"),
    "declared_source": ("申请量来源", "申报量来源", "Declared source"),
    "completed": ("本期完成量", "完成工程量", "完成量", "Completed quantity"), "completed_source": ("完成量来源", "Completed source"),
    "confirmed": ("用户验工量", "确认工程量", "验工量", "确认量", "Confirmed quantity"), "confirmed_source": ("确认量来源", "验工量来源", "Confirmed source"),
    "foreman": ("工长", "核量人", "Foreman"), "foreman_qty": ("工长核量", "现场核量", "Foreman quantity"), "foreman_source": ("工长核量来源", "Foreman source"),
    "upstream_qty": ("对上确认量", "总包确认量", "业主确认量", "Upstream quantity"), "upstream_source": ("对上确认来源", "总包确认来源", "Upstream source"),
    "upstream_record": ("对上确认记录", "Upstream record"), "unassigned": ("未分派数量", "数量", "Unassigned quantity"),
    "price": ("合同单价", "结算单价", "合同价", "单价", "Contract rate"), "price_source": ("单价来源", "Rate source"),
    "claimed_amount": ("用户申请价款", "申请金额", "申报价款", "Claimed amount"), "claimed_source": ("申请金额来源", "Claimed source"),
    "user_payable": ("用户应付分包工程款", "用户应付金额", "User payable"), "payable_source": ("用户应付依据", "Payable source"),
    "tax_basis": ("价税口径", "Tax basis"), "deduction_kind": ("扣款项", "扣款类别", "Deduction type"),
    "deduction_amount": ("用户拟扣金额", "扣款金额", "扣款", "Deduction amount"), "deduction_record": ("扣款记录", "Deduction record"),
    "deduction_source": ("扣款依据", "扣款来源", "Deduction source"), "notice": ("通知单", "书面通知", "Notice"),
    "quality_notice": ("质量罚款通知单", "Quality penalty notice"),
    "utilities": ("水电费",), "quality_penalty": ("质量罚款",), "safety_penalty": ("安全罚款",), "damage": ("周转料具损坏", "扣件损坏扣款"),
    "supplied": ("甲供材扣款", "代供料扣款"), "advance_deduction": ("预付款抵扣", "预支抵扣"),
    "deduction_quantity": ("代供材料数量", "扣款材料数量", "Deduction quantity"),
    "paid": ("本期已付", "已付金额", "Paid amount"), "cumulative_paid": ("累计已付", "前期累计已付", "Cumulative paid"),
    "advance_paid": ("预付款已付", "用户已预支", "Advance paid"), "living_paid": ("生活费预支", "生活费已付", "Living advance"),
    "wage_paid": ("工资代发已付", "用户已付工资", "Wages paid"), "planned_payment": ("用户计划付款", "计划付款", "Planned payment"),
    "payment_record": ("支付记录", "付款记录", "Payment record"), "payment_source": ("支付凭证", "付款来源", "Payment source"),
    "payment_date": ("支付日期", "付款日期", "Payment date"), "payee": ("收款人", "收款方", "Payee"),
    "variation": ("变更项目", "合同外项目", "签证项目", "Variation"), "variation_qty": ("变更申请量", "合同外申请量", "Variation quantity"),
    "variation_amount": ("用户变更金额", "变更金额", "Variation amount"), "variation_source": ("变更来源", "签证来源", "Variation source"),
    "variation_record": ("变更签认记录", "签证状态", "Variation record"),
    "retention_rate": ("用户质保比例", "质保比例", "Retention rate"), "retention_amount": ("用户质保金", "质保金", "Retention amount"),
    "retention_source": ("质保依据", "质保金来源", "Retention source"), "defect_period": ("缺陷责任期", "Defects period"),
    "warranty": ("保修期", "Warranty period"), "retention_return": ("质保金返还记录", "Retention return"),
    "labor_payable": ("用户应付人工费", "应付人工费", "Labor payable"), "wage_sheet": ("工资表", "Wage sheet"),
    "attendance": ("考勤记录", "考勤", "Attendance"), "wage_signature": ("工人签字记录", "Wage signature"),
    "wage_source": ("工资资料来源", "Wage source"), "labor_account": ("人工费账户", "Labor account"), "wage_record": ("用户工资记录", "Wage record"),
    "sign_record": ("用户会签记录", "会签记录", "Countersign record"), "attachments": ("附件目录", "附件", "Attachments"),
    "source": ("来源文件", "资料来源", "来源", "Source"), "record": ("待核记录", "记录", "Record"),
}
ALIASES = {a.casefold(): k for k, labels in _FIELDS.items() for a in labels}
_LABEL = re.compile(r"^("+"|".join(re.escape(a) for a in sorted(ALIASES, key=len, reverse=True))+r")(?:\s*[:：=]\s*|\s+)(.*)$", re.I)
_META = {"project", "jurisdiction", "period", "begin", "end", "version", "currency"}
_PARTY = {"company", "contract", "kind", "pricing", "upstream_period", "link"}
_QUANTITIES = {"declared", "completed", "confirmed", "foreman_qty", "upstream_qty", "unassigned", "variation_qty", "deduction_quantity"}
_PAYMENTS = {"paid", "cumulative_paid", "advance_paid", "living_paid", "wage_paid", "planned_payment", "payment_record", "payment_source", "payment_date", "payee"}
_DEDUCTIONS = {"deduction_kind", "deduction_amount", "deduction_record", "deduction_source", "notice", "_qualified_notice", "deduction_quantity"}
_VARIATIONS = {"variation", "variation_qty", "variation_amount", "variation_source", "variation_record"}
_QUALITY = {"retention_rate", "retention_amount", "retention_source", "defect_period", "warranty", "retention_return"}
_WAGES = {"labor_payable", "wage_sheet", "attendance", "wage_signature", "wage_source", "labor_account", "wage_record"}
_MONEY = {"claimed_amount", "user_payable", "deduction_amount", "variation_amount", "retention_amount", "labor_payable"} | (_PAYMENTS - {"payment_record", "payment_source", "payment_date", "payee"})
_PAID = {"paid", "cumulative_paid", "advance_paid", "living_paid", "wage_paid"}
_DEDUCTION_KINDS = {"utilities": "水电费", "quality_penalty": "质量罚款", "safety_penalty": "安全罚款", "damage": "周转料具损坏", "supplied": "甲供材 / 代供料扣款", "advance_deduction": "预付款抵扣"}


@dataclass
class Data:
    contexts: list[dict[str, str]] = field(default_factory=list)
    rows: list[dict[str, str]] = field(default_factory=list)
    deductions: list[dict[str, str]] = field(default_factory=list)
    payments: list[dict[str, str]] = field(default_factory=list)
    variations: list[dict[str, str]] = field(default_factory=list)
    quality: list[dict[str, str]] = field(default_factory=list)
    wages: list[dict[str, str]] = field(default_factory=list)
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
    if key in _QUANTITIES | _MONEY | {"price", "company", "contract", "name", "code", "location", "foreman", "payee", "retention_rate", "begin", "end", "payment_date"} and (_UNKNOWN.search(value) or _UNSAFE.search(value) or _PENDING.search(value)):
        return MISSING
    if key in _QUANTITIES and not _QTY.fullmatch(value):
        return MISSING
    if key in _MONEY and not re.fullmatch(_MONEY_VALUE, value, re.I):
        return MISSING
    if key == "price" and not re.fullmatch(_RATE_VALUE, value, re.I):
        return MISSING
    if key == "retention_rate" and not re.fullmatch(_NUMBER+r"\s*[%％]", value):
        return MISSING
    if key in {"begin", "end", "payment_date"} and not re.fullmatch(_DATE, value):
        return MISSING
    return value


def _details(key, value):
    if key == "quality_notice":
        return [("deduction_kind", "质量罚款"), ("_qualified_notice", value)]
    if key in _DEDUCTION_KINDS:
        return [("deduction_kind", _DEDUCTION_KINDS[key])]+_details("deduction_amount", value)
    if key in _PAID and (_PENDING.search(value) or _UNKNOWN.search(value)):
        planned = re.fullmatch(r"(?:预计|计划|拟|据说|听说)(?:明天|明日|下周)?(?:已付|支付|付|转账)?\s*("+_MONEY_VALUE+r")", value, re.I)
        return [("planned_payment", planned.group(1).strip() if planned else MISSING), ("payment_record", _FIELDS[key][0]+"："+value)]
    pairs = [(key, value)]
    if key in _QUANTITIES | _MONEY | {"price", "retention_rate"} and _typed(key, value) == MISSING and _value(value) != MISSING:
        pairs.append(("record", _FIELDS[key][0]+"："+value))
    if key == "period":
        dates = re.findall(_DATE, value)
        if len(dates) == 2 and re.search(r"至|到|~|～", value):
            pairs += [("begin", dates[0]), ("end", dates[1])]
    return pairs


def _scan(text):
    text = re.sub(r"^(?:本次实际情况|实际情况|真实记录)\s*[:：]\s*", "", text)
    text = re.sub(r"^.{0,24}?(?:写一份|写份|生成|起草|出)(?:一份)?分包(?:劳务)?结算(?:表头|草稿)?\s*[:：]?\s*", "", text)
    label = _LABEL.match(text)
    if label:
        return _details(ALIASES[label.group(1).casefold()], label.group(2))
    if _UNSAFE.search(text):
        return []
    parts = re.split(r"(?:但是|不过|但|而|只知道)(?=(?:单价|合同价|合同单价|工长|确认量|申请量|累计|预计|计划))", text)
    if len(parts) > 1:
        return [pair for part in parts for pair in _scan(part)]
    pairs = []
    group = re.match(r"(.+?(?:木工班|劳务队|劳务班))和(.+?(?:架子队|木工班|劳务队|劳务班))(本月|这个月|上月|下月).*结", text)
    if group:
        return [("period", group.group(3)), ("_party", group.group(1)), ("_party", group.group(2))]
    period = re.search(r"\d{4}[-年]\d{1,2}(?:月|期)|本月|这个月|上月|下月", text)
    if period and re.search(r"结算|结$", text):
        pairs += _details("period", period.group().rstrip("期月"))
    if "专业分包" in text:
        pairs.append(("kind", "专业分包"))
    elif "劳务结算" in text or "劳务分包" in text:
        pairs.append(("kind", "劳务分包"))
    party = re.match(r"(.+?(?:劳务|工程有限公司|架子队|木工班))(?:干的|做的|搭的)(.+)$", text)
    reference = re.match(r"(?:另外)?(木工班|木工|架子队)(?=干的是|搭的|预支过|已付|本期|计划|预计)(.*)$", text)
    if party:
        pairs.extend((("company", party.group(1)), ("name", party.group(2))))
        location = _LOCATION.search(party.group(2))
        if location:
            pairs.append(("location", location.group()))
    elif reference:
        pairs.append(("_reference_company", reference.group(1)))
        body = re.sub(r"^(?:干的是|搭的)", "", reference.group(2))
        location = _LOCATION.search(body)
        if location:
            pairs.extend((("location", location.group()), ("name", body)))
    for key, pattern in (("contract", r"合同编号\s*([A-Za-z0-9][A-Za-z0-9_.-]*)"), ("task", r"任务单\s*([A-Za-z0-9][A-Za-z0-9_.-]*)"), ("notice", r"通知单\s*([A-Za-z0-9][A-Za-z0-9_.-]*)")):
        match = re.search(pattern, text)
        if match:
            pairs.append((key, match.group(1)))
    quantity = re.search(r"工长(?:([\u4e00-\u9fff]{2,4}))?核完(?:量)?(?:是)?\s*(.+)$", text)
    if quantity:
        if quantity.group(1):
            pairs.append(("foreman", quantity.group(1)))
        number = _QTY_START.match(quantity.group(2))
        if number and not (_PENDING.search(text) or _UNKNOWN.search(text)):
            pairs.append(("foreman_qty", number.group(1)))
        else:
            pairs.append(("record", text))
    rate = re.search(r"(?:合同单价|合同价|单价)\s*("+_RATE_VALUE+r")(?![A-Za-z0-9_.#/-])", text, re.I)
    if rate:
        if _UNKNOWN.search(text[rate.start():]) or _PENDING.search(text[rate.start():]):
            pairs += [("price", MISSING), ("record", text[rate.start():])]
        else:
            pairs.append(("price", rate.group(1).strip()))
    for pattern, kind in ((r"(?:本期要扣|要扣|扣)水电费\s*("+_MONEY_VALUE+r")"+_MONEY_END, "水电费"),
                          (r"质量罚款\s*("+_MONEY_VALUE+r")"+_MONEY_END, "质量罚款"),
                          (r"摔坏扣件.*?要扣\s*("+_MONEY_VALUE+r")"+_MONEY_END, "周转料具损坏")):
        amount = re.search(pattern, text, re.I)
        if amount:
            pairs += [("deduction_kind", kind)]+_details("deduction_amount", amount.group(1).strip())
            pairs.append(("deduction_record", text))
    for key, pattern in (("cumulative_paid", r"(?:之前|前期)?累计(?:已经|已)?(?:付了|已付|付|支付了)\s*("+_MONEY_VALUE+r")"+_MONEY_END),
                         ("living_paid", r"(?:预支过|预支|已付)?生活费\s*("+_MONEY_VALUE+r")"+_MONEY_END)):
        amount = re.search(pattern, text, re.I)
        if amount:
            value = amount.group(1).strip()
            if _PENDING.search(text) or _UNKNOWN.search(text):
                pairs += [("planned_payment", value), ("payment_record", text)]
            elif key == "living_paid" and not re.search(r"预支|已付", text):
                pairs.append(("payment_record", text))
            else:
                pairs += [(key, value), ("payment_record", text)]
    if re.search(r"量还没核|合同号.*回头|量未核|单价没定", text):
        pairs.append(("record", text))
    loose = re.fullmatch(r"(.+?)\s+("+_NUMBER+r"\s*"+_UNIT+r")", text, re.I)
    if loose and not pairs:
        pairs.extend((("name", loose.group(1)), ("unassigned", loose.group(2))))
    return pairs


def parse(raw):
    data = Data()
    scope = {"jurisdiction": "UNSPECIFIED"}
    data.contexts.append(scope)
    current, origin, epoch, children = None, "", 0, {}

    def ref():
        return next((i for i, row in enumerate(data.rows) if row is current), None)

    def new_row(retain=None):
        row = {"_origin": origin, "_file": epoch, "_scope": len(data.contexts)-1, **scope}
        if retain:
            row.update({k: retain[k] for k in _PARTY | _META if k in retain})
        data.rows.append(row)
        return row

    def put(target, key, value):
        value = _typed(key, value)
        if target.get(key) in {None, MISSING, "UNSPECIFIED"}:
            target[key] = value
        elif value != MISSING and value not in target[key].split("；"):
            target[key] += "；"+value
        if value not in {MISSING, "UNSPECIFIED"}:
            data.evidence.append({"_record": target.get("_record", ref()), "field": _FIELDS[key][0], "value": value, "input_position": position})

    def child(kind, fresh=False):
        if fresh or kind not in children:
            children[kind] = {"_record": ref()}
            getattr(data, kind).append(children[kind])
        return children[kind]

    for content, is_table, offset, source in _inputs(raw):
        position = (origin+" · " if origin else "")+f"本轮输入第{raw[:offset].count(chr(10))+1}行（非原件页码）"
        if content is None:
            current, children = None, {}
            continue
        marker = _source_name(source)
        if marker is not None or source.startswith(("【本轮用户要求", "【此前用户资料")):
            origin = marker if marker is not None else source.strip("【】")
            epoch += 1
            scope = {"_origin": origin, "jurisdiction": "UNSPECIFIED"}
            data.contexts.append(scope)
            current, children = None, {}
            continue
        pairs = ([(k, v) for label, value in content if label.casefold() in ALIASES for k, v in _details(ALIASES[label.casefold()], value)] if is_table else _scan(source))
        if not pairs:
            continue
        stated = dict(pairs)
        if not is_table and any(k in stated and scope.get(k) not in {None, MISSING, "UNSPECIFIED", _typed(k, stated[k])} for k in _META):
            critical_change = any(k in stated and scope.get(k) not in {None, MISSING, "UNSPECIFIED", _typed(k, stated[k])} for k in {"project", "jurisdiction"})
            prior = None if critical_change else current
            scope = {"_origin": origin, "jurisdiction": "UNSPECIFIED"} if critical_change else {**scope}
            for key in _META:
                if key in stated:
                    scope.pop(key, None)
            if "period" in stated:
                scope.pop("begin", None)
                scope.pop("end", None)
            data.contexts.append(scope)
            current, children = new_row(prior) if prior else None, {}
            if current is not None:
                for k in _META:
                    if k in stated or "period" in stated and k in {"begin", "end"}:
                        current.pop(k, None)
        if "_reference_company" in stated:
            token = stated["_reference_company"]
            found = [r for r in data.rows if r["_file"] == epoch and token in r.get("company", "") and (scope.get("period") in {None, MISSING} or r.get("period") == scope["period"])]
            identities = {(r.get("company"), r.get("contract"), r.get("period")) for r in found}
            current, children = (found[0] if len(found) == 1 else new_row(found[0]) if len(identities) == 1 and found else new_row()), {}
        if is_table:
            current, children = new_row(), {}
            if "period" in stated:
                current.pop("begin", None)
                current.pop("end", None)
        elif "company" in stated and current is not None and current.get("company") not in {None, MISSING}:
            current, children = new_row(), {}
        elif "contract" in stated and current is not None and current.get("contract") not in {None, MISSING, _typed("contract", stated["contract"])}:
            previous = current
            current, children = new_row(), {}
            if previous.get("company"):
                current["company"] = previous["company"]
        elif any(k in stated and current is not None and current.get(k) not in {None, MISSING, _typed(k, stated[k])} for k in {"name", "location"}):
            current, children = new_row(current), {}
        elif not is_table and current is not None and any(k in stated and current.get(k) not in {None, MISSING} for k in (_QUANTITIES - {"variation_qty", "deduction_quantity"}) | {"price", "claimed_amount", "user_payable"}):
            prior = current
            current, children = new_row(prior), {}
            current.update({k: prior[k] for k in {"name", "code", "location"} if k in prior})
        if "code" in stated and not is_table and "name" not in stated and "location" not in stated:
            code = _typed("code", stated["code"])
            found = [r for r in data.rows if r["_file"] == epoch and r.get("code") == code and r.get("_scope") == len(data.contexts)-1]
            if current is None or current.get("code") not in {None, MISSING, code} or len(found) > 1:
                current, children = (found[0] if len(found) == 1 else new_row()), {}
        for key, value in pairs:
            if key in _META and not is_table:
                put(scope, key, value)
                if current is not None:
                    put(current, key, value)
        parties = [value for key, value in pairs if key == "_party"]
        if parties:
            for company in parties:
                current = new_row()
                put(current, "company", company)
            current, children = None, {}
            continue
        if current is None and any(k not in _META for k, _ in pairs):
            current = new_row()
        for key, value in pairs:
            if key.startswith("_"):
                continue
            if key in _META and not is_table:
                continue
            if key not in _DEDUCTIONS | _PAYMENTS | _VARIATIONS | _QUALITY | _WAGES:
                if is_table and key in _META:
                    current[key] = _typed(key, value)
                put(current, key, value)
        for kind, keys, identity in (("deductions", _DEDUCTIONS, "deduction_kind"), ("payments", _PAYMENTS, None), ("variations", _VARIATIONS, "variation"), ("quality", _QUALITY, None), ("wages", _WAGES, None)):
            supplied = [(k, v) for k, v in pairs if k in keys]
            if not supplied:
                continue
            money_keys = _PAID | {"planned_payment"}
            shared_keys = {"notice", "deduction_source"} if kind == "deductions" else {"payment_source", "payment_date", "payee", "payment_record"}
            multiple = (sum(k == "deduction_amount" and _typed(k, v) != MISSING for k, v in supplied) > 1 if kind == "deductions" else sum(k in money_keys and _typed(k, v) != MISSING for k, v in supplied) > 1 if kind == "payments" else False)
            if multiple:
                shared = [(k, v) for k, v in supplied if k in shared_keys]
                supplied = [(k, v) for k, v in supplied if k not in shared_keys]
                if shared:
                    target = child(kind, fresh=True)
                    for key, value in shared:
                        put(target, key, value)
                    children.pop(kind)
            groups = [[]]
            for key, value in supplied:
                boundary = (kind == "deductions" and key == "deduction_kind" and any(k == key for k, _ in groups[-1]) or kind == "payments" and key in money_keys and _typed(key, value) != MISSING and any(k in money_keys and _typed(k, v) != MISSING for k, v in groups[-1]))
                if boundary:
                    groups.append([])
                groups[-1].append((key, value))
            for group in groups:
                provided = dict(group)
                previous = children.get(kind, {})
                qualified = "_qualified_notice" in provided
                found = [r for r in data.deductions if r.get("_record") == ref() and r.get("deduction_kind") == provided.get("deduction_kind") and r.get("deduction_amount") not in {None, MISSING}] if qualified else []
                declared_identity = identity in provided and previous.get(identity) not in {None, MISSING} if identity else False
                if kind == "deductions" and previous.get("deduction_kind") == provided.get("deduction_kind") and previous.get("deduction_amount") in {None, MISSING}:
                    declared_identity = False
                repeat = any(k in provided and previous.get(k) not in {None, MISSING} for k in keys - {identity})
                changed_payment = kind == "payments" and any(k in provided for k in money_keys) and any(k in previous for k in money_keys)
                target = found[0] if len(found) == 1 else child(kind, fresh=declared_identity or repeat or changed_payment or qualified and len(found) > 1)
                if len(found) == 1:
                    children[kind] = target
                if repeat and not declared_identity and identity and identity not in provided and identity in previous:
                    target[identity] = previous[identity]
                for key, value in group:
                    put(target, "notice" if key == "_qualified_notice" else key, value)
    return data


def _owned(data, records):
    return [{**(data.rows[r["_record"]] if isinstance(r.get("_record"), int) else {}), **r} for r in records]


def build_draft(post, tool_name, text):
    if post != "subcontract" or tool_name != TOOL:
        return None
    data = parse(text)
    owner = (("合同分包 / 分包单位 / 班组", "company"), ("分包合同编号", "contract"), ("结算期间原文", "period"), ("分项 / 用户工作内容", "name"), ("计量部位", "location"), ("清单编号", "code"), ("项目名称", "project"), ("辖区", "jurisdiction"), ("币种（未换算）", "currency"), ("输入资料名", "_origin"))
    return "\n".join([
        "# 分包（劳务）结算表头（AI 草稿 · 内部讨论）", "", DISCLAIMER, "", "- submit_blocked=true", "",
        "## 1 封面与草稿声明", "", _table(data.contexts, (("项目名称", "project"), ("结算期间原文", "period"), ("起始日期", "begin"), ("截止日期", "end"), ("辖区", "jurisdiction"), ("结算版本", "version"), ("币种", "currency"), ("输入资料名", "_origin"))), "",
        "缺项为 [A001] / UNSPECIFIED；本轮结算金额和批准应付款为 TBD。相对期间只登记原文，不换算日历或默设币种。", "",
        "## 2 合同关系", "", _table(data.rows, owner+(("用户分包类型（未核）", "kind"), ("用户计价方式", "pricing"), ("用户对上验工期次", "upstream_period"), ("用户周期挂钩记录", "link")), extra=(("本轮合同 / 合规性核验", "未执行 / 待核"),)), "",
        "不由单位名称判合同合法性、分包范围或计价方式，不借用其他合同资料。", "",
        "## 3 本期完成", "", _table(data.rows, owner+(("用户计量单位", "unit"), ("用户计量规则", "rule"), ("用户任务单编号", "task"), ("任务单 / 计量依据来源", "task_source"),
            ("用户申请工程量（非确认量）", "declared"), ("申请量来源", "declared_source"), ("用户本期完成量（未核）", "completed"), ("完成量来源", "completed_source"),
            ("用户验工 / 确认工程量原文（未核）", "confirmed"), ("用户确认量来源", "confirmed_source"), ("用户工长 / 核量人", "foreman"), ("用户工长核量原文（非本轮核定）", "foreman_qty"), ("工长核量来源", "foreman_source"),
            ("用户对上确认量（未核）", "upstream_qty"), ("对上确认来源", "upstream_source"), ("用户对上确认记录", "upstream_record"), ("用户未分派数量（口径待核）", "unassigned")), extra=(("本轮数量 / 质量核验", "未执行 / 待核"),)), "",
        "申请量、完成量、工长核量、用户确认量与对上确认分列；任务单或签字资料名称不补缺量，不由百分比、楼层、型号、日期或合同编号计算实物量。", "",
        "## 4 合同内价款栏", "", _table(data.rows, owner+(("用户合同单价原文（未核）", "price"), ("单价来源", "price_source"), ("用户申请价款原文（非核定）", "claimed_amount"), ("申请价款来源", "claimed_source"), ("用户应付分包工程款记录（未核）", "user_payable"), ("用户应付依据", "payable_source"), ("用户价税口径", "tax_basis")), extra=(("本轮数量乘单价 / 结算合价", "TBD · 未计算"), ("本轮批准应付款", "TBD · 未核定"))), "",
        "不计算乘积、累计、扣款合计、净额、税额或汇率。单价有数字不等于核定价款或付款批准。", "",
        "## 5 合同外", "", _table(_owned(data, data.variations), owner+(("用户变更 / 合同外项目", "variation"), ("用户合同外申请量", "variation_qty"), ("用户变更金额原文（未核）", "variation_amount"), ("变更 / 签证来源", "variation_source"), ("用户签认记录（未核）", "variation_record")), extra=(("本轮签认核验 / 纳入结算", "未执行 / 待核 · 未纳入"),)), "",
        "## 6 扣款表头", "", _table(_owned(data, data.deductions), owner+(("用户拟扣款项", "deduction_kind"), ("用户拟扣款金额原文（未核）", "deduction_amount"), ("用户扣款记录", "deduction_record"), ("用户扣款依据 / 凭证", "deduction_source"), ("用户通知单标识（未核）", "notice"), ("用户代供 / 扣款材料数量", "deduction_quantity")), extra=(("本轮扣款核验 / 抵扣", "未执行 / 未抵扣"),)), "",
        "拟扣、凭证和批准分开；未提供凭证则待核，不造扣款依据，不把金额当工程量。", "",
        "## 7 质量与质保", "", _table(_owned(data, data.quality), owner+(("用户质保比例原文", "retention_rate"), ("用户质保金原文（未核）", "retention_amount"), ("用户质保依据", "retention_source"), ("用户缺陷责任期", "defect_period"), ("用户保修期", "warranty"), ("用户质保金返还记录（未核）", "retention_return")), extra=(("本轮质保 / 返还核验", "未执行 / 待核"),)), "",
        "不默认质保比例、期限或法律结论；返还记录不表示保修责任结束。", "",
        "## 8 农民工工资专节", "", _table(_owned(data, data.wages), owner+(("用户应付人工费原文（未核）", "labor_payable"), ("用户工资表标识", "wage_sheet"), ("用户考勤记录", "attendance"), ("用户工人签字记录（未核）", "wage_signature"), ("用户人工费账户", "labor_account"), ("工资资料来源", "wage_source"), ("用户工资记录", "wage_record")), extra=(("本轮应付人工费", "TBD · 未核定"), ("本轮工资结清核验", "未执行 / 待核"))), "",
        "### 用户支付记录（非支付批准）", "", _table(_owned(data, data.payments), owner+(("用户本期已付原文（未核）", "paid"), ("用户前期累计已付原文（未核）", "cumulative_paid"), ("用户预付款 / 预支记录（未核）", "advance_paid"), ("用户生活费预支原文（非工资结清）", "living_paid"), ("用户工资代发已付原文（未核）", "wage_paid"), ("用户计划 / 据说付款金额（非实付）", "planned_payment"), ("用户付款记录原文", "payment_record"), ("支付凭证 / 来源", "payment_source"), ("用户收款方", "payee"), ("用户支付日期", "payment_date")), extra=(("本轮实付 / 工资核验", "未执行 / 待核"),)), "",
        "累计已付、本期付款、预支、生活费和工资代发不互相代填，不将计划或据说的支付当实付，不说工资已结清。", "",
        "## 9 会签栏", "", _table(data.rows, owner+(("用户会签记录（非本轮核验）", "sign_record"),), extra=(("本轮会签状态", "未会签"), ("工长 / 工程 / 安质 / 物资 / 商务 / 项目经理签认", "（空）"))), "",
        "## 10 与对上验工、财务接口", "", _table(data.rows, owner+(("用户差异 / 待核记录", "record"), ("未分派资料来源", "source"), ("用户附件目录", "attachments")), extra=(("本轮对账 / 付款指令", "未执行 / 未签发"),)), "",
        "付款申请交 finance-fund，税务资料交 finance-tax；不推断对下累计与对上份额关系或发票税目。", "",
        "## 11 自检", "", _table(_owned(data, data.evidence), owner+(("记录项", "field"), ("用户证据原文", "value"), ("输入位置（非原件页码）", "input_position"))), "",
        "原件、现行条款、数量、扣款与签认未核。无来源不补单位、数字或国家，不下结算、支付、质量或合法性结论。", "",
    ])
