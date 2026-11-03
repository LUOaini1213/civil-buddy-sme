"""Literal claim-event records, not entitlement, service or loss calculation.

Events, workfaces, resources and individual correspondence own independent
records. A named letter is not proof of service or an accepted claim. Contract
wording stays user-supplied, unverified text; no template clock is applied.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

from packing_assistant.post_drafts.planning import _inputs as _shared_inputs, _table
from packing_assistant.post_drafts.procurement import _source_name

MISSING = "[A001] / UNSPECIFIED"
TOOL = "claim__notice"
DISCLAIMER = ("AI 草稿 · 内部讨论 · 不得作为法定专项方案 / 交底签认件。本稿仅整理用户索赔意向及调概资料，"
              "不是已发送、送达或获认可的索赔报告，不构成付款证书、工期批准或概算批复。"
              "未核原件、合同适用性、责任、因果关系、送达或时限，不计算损失、索赔金额或工期天数。")
_NUMBER = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_DATE = r"(?:\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?|\d{1,2}月\d{1,2}[日号]|上月底|本月底|上月|本月|下月|昨日|今日|今天|明天|下周)"
_CODE = r"[A-Za-z][A-Za-z0-9]*(?:[-_/.][A-Za-z0-9]+)+"
_MONEY = r"(?:(?:SGD|CNY|RMB|USD|EUR|GBP|HKD)|S\$|[$€£¥])?\s*"+_NUMBER+r"\s*(?:万元|元|SGD|CNY|RMB|USD|EUR|GBP|HKD)?"
_UNKNOWN = re.compile(r"未提供|未给|未定|未知|待填|待核|待补|没给|没定|没报|还没|没有|回头|稍后|得查|要查|UNSPECIFIED|TBD|N/A|unknown|not provided", re.I)
_UNSAFE = re.compile(r"例如|比如|假设|如果|示例|样例|自行计算|自己算|不采用|别按|不是|是否|[？?]|\b(?:for example|hypothetical|whether)\b", re.I)
_PENDING = re.compile(r"预计|计划|拟|估计|听说|据说|说会|尚未|还没|没发|未发|未收到|未送达|未签|没签|未曾|不曾|没打算|没准备|否认|拒绝|将|\b(?:planned|forecast|reportedly|not|will|tomorrow)\b", re.I)
_UNSENT_DOC = re.compile(r"草稿|仅拟稿|待发送|待寄|未寄|\b(?:draft|unsent)\b", re.I)
_LOCATION = re.compile(r"K\d+\+\d+(?:[~～至-]K\d+\+\d+)?[^，,；;。\n]*?(?:沟槽|管线|路段)|\d+[#＃]楼(?:地下室|转换层|[一二三四五六七八九十\d]+层)?(?:底板|顶板|结构图|钢筋绑扎|模板|外架|结构)?(?:钢筋绑扎)?|地下室(?:防水|底板|顶板)?")
_FIELDS = {
    "project": ("项目名称", "工程名称", "项目", "Project"), "jurisdiction": ("辖区", "Jurisdiction"),
    "contract": ("合同编号", "合同号", "Contract ID"), "contract_name": ("合同名称", "合同文本", "Contract name"),
    "contract_original": ("合同原文", "合同要求原文", "索赔条款原文", "合同索赔条款原文", "Contract wording"),
    "notice_original": ("通知要求原文", "通知时限原文", "Notice wording"),
    "eot_original": ("工期通知条款原文", "工期条款原文", "EOT wording"), "contract_source": ("合同原文来源", "条款来源", "Contract source"),
    "event": ("索赔事件", "事件名称", "事件", "事由", "Event"), "code": ("事件编号", "Event ID"),
    "location": ("影响工作面", "工作面", "部位", "位置", "Location"), "event_source": ("事件来源", "Event source"),
    "agreed_date": ("合同约定到场", "约定到场日期", "约定到场", "Agreed arrival"), "arrival": ("实际到场日期", "实际进场", "实际到场", "Actual arrival"),
    "draw_date": ("实际发图日期", "发图日期", "Actual drawing date"), "start": ("停工开始", "事件开始", "开始日期", "Start date"),
    "end": ("停工结束", "恢复日期", "事件结束", "结束日期", "End date"), "occurred": ("发生日期", "事件日期", "日期", "Event date"),
    "date_source": ("日期来源", "时间来源", "Date source"), "timeline": ("用户时间线", "时间线", "Timeline"),
    "cause": ("用户原因记录", "原因记录", "Cause record"), "responsibility": ("用户责任记录", "责任归属记录", "Responsibility record"),
    "claimant": ("主张方", "索赔方", "Claimant"), "respondent": ("被主张方", "相对方", "Respondent"),
    "network": ("网络图", "关键线路资料", "Network source"), "critical_record": ("用户关键线路记录", "Critical path record"),
    "mitigation": ("用户减损措施", "减损措施", "Mitigation"), "mitigation_source": ("减损来源", "Mitigation source"),
    "user_amount": ("用户主张金额", "申报索赔金额", "Claimed amount"), "amount_source": ("金额来源", "Amount source"),
    "user_duration": ("用户主张工期", "申请延长工期", "Claimed duration"), "duration_source": ("工期来源", "Duration source"),
    "request": ("用户意向请求", "意向请求", "Request"), "loss_record": ("用户损失记录", "损失记录", "Loss record"),
    "labor": ("窝工班组", "人工班组", "班组", "Labor crew"), "labor_qty": ("窝工人数", "人员数量", "人数", "Labor count"),
    "equipment": ("机械名称", "设备名称", "机械", "设备", "Equipment"), "model": ("机械型号", "设备型号", "型号", "Model"),
    "equipment_qty": ("机械数量", "设备数量", "Equipment count"), "resource_source": ("同期记录来源", "资源来源", "Resource source"),
    "_resource_kind": ("资源类别", "Resource type"),
    "resource_record": ("用户窝工记录", "窝工记录", "Resource record"), "idle_days": ("用户停置天数", "停置天数", "Idle duration"),
    "document_kind": ("资料类别", "通知类别", "Document type"), "document_id": ("函号", "文号", "通知编号", "Document ID"),
    "stop_order": ("停工令编号", "停工令", "Stop order"), "issued_letter": ("已发函", "已发通知", "Issued letter"),
    "planned_letter": ("拟发函", "拟发通知", "Planned letter"), "document_record": ("用户通知记录", "通知记录", "Document record"),
    "issued_record": ("用户发出记录", "发出记录", "Issued record"), "issue_date": ("发函日期", "通知发送日期", "Issue date"),
    "issue_source": ("发出凭证", "发送来源", "Issue source"), "document_source": ("函件来源", "通知来源", "Document source"),
    "receiver": ("用户签收人", "签收人", "接收人", "Receiver"), "receiver_role": ("签收人角色", "接收角色", "Receiver role"),
    "receipt_date": ("签收日期", "接收日期", "Receipt date"), "receipt_source": ("送达回证", "签收凭证", "Receipt source"),
    "receipt_record": ("用户签收记录", "签收记录", "Receipt record"), "intended_recipient": ("拟收件人", "拟收件单位", "Intended recipient"),
    "evidence": ("证据名称", "影响资料", "证据", "Evidence"), "evidence_position": ("证据位置", "原件位置", "Evidence position"),
    "evidence_source": ("证据来源", "Evidence source"), "owner": ("补证负责人", "补证责任人", "补证责任", "Evidence owner"),
    "evidence_owner": ("该证据补证责任", "该证据负责人", "Evidence responsible"),
    "action": ("补证行动", "补证计划", "补证事项", "Evidence action"), "deadline": ("补证期限", "补证日期", "Evidence deadline"),
    "adjustment": ("调概事项", "Adjustment item"), "budget": ("用户批准概算记录", "批准概算记录", "Budget record"),
    "adjustment_basis": ("用户调概依据", "调概依据", "Adjustment basis"), "adjustment_source": ("调概资料来源", "Adjustment source"),
    "source": ("来源文件", "资料来源", "来源", "Source"), "record": ("待核记录", "记录", "Record"),
    "idle": ("窝工",),
}
ALIASES = {a.casefold(): k for k, labels in _FIELDS.items() for a in labels}
_LABEL = re.compile(r"^("+"|".join(re.escape(a) for a in sorted(ALIASES, key=len, reverse=True))+r")(?:\s*[:：=]\s*|\s+)(.*)$", re.I)
_INNER_LABEL = re.compile(r"\s+("+"|".join(re.escape(a) for a in sorted(ALIASES, key=len, reverse=True))+r")\s*[:：=]\s*(.*)$", re.I)
_META = {"project", "jurisdiction", "contract", "contract_name", "contract_original", "notice_original", "eot_original", "contract_source"}
_ORIGINALS = {"contract_original", "notice_original", "eot_original"}
_RESOURCES = {"labor", "labor_qty", "equipment", "model", "equipment_qty", "resource_source", "resource_record", "idle_days", "_resource_kind"}
_DOCUMENTS = {"document_kind", "document_id", "document_record", "issued_record", "issue_date", "issue_source", "document_source", "receiver", "receiver_role", "receipt_date", "receipt_source", "receipt_record", "intended_recipient"}
_EVIDENCE = {"evidence", "evidence_position", "evidence_source", "evidence_owner"}
_ACTIONS = {"owner", "action", "deadline"}
_ADJUSTMENTS = {"adjustment", "budget", "adjustment_basis", "adjustment_source"}
_DATES = {"agreed_date", "arrival", "draw_date", "start", "end", "occurred", "issue_date", "receipt_date", "deadline"}


@dataclass
class Data:
    contexts: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    resources: list[dict] = field(default_factory=list)
    documents: list[dict] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    adjustments: list[dict] = field(default_factory=list)
    provenance: list[dict] = field(default_factory=list)


def _value(value):
    value = value.strip()
    if not re.search(r"&(?:#\d+|#x[\da-f]+|[a-z][\da-z]+);$", value, re.I):
        value = value.rstrip("，,；;。")
    return MISSING if not value or _UNKNOWN.fullmatch(value) else value


def _typed(key, value):
    if key in _ORIGINALS:
        value = value.strip()
        return MISSING if not value or _UNKNOWN.fullmatch(value) or re.fullmatch(r".*(?:稍后贴|待贴|未提供|回头找).*", value) else value
    value = _value(value)
    if key == "jurisdiction" and value == MISSING:
        return "UNSPECIFIED"
    if key == "event":
        return MISSING if _UNKNOWN.fullmatch(value) or _UNSAFE.search(value) else value
    if key in _DATES | {"labor_qty", "equipment_qty", "user_amount", "user_duration", "idle_days", "receiver", "owner", "evidence_owner", "model", "claimant", "respondent", "code", "location", "document_id"} and (_UNKNOWN.search(value) or _UNSAFE.search(value) or _PENDING.search(value)):
        return MISSING
    if key in {"arrival", "draw_date", "start", "end", "occurred", "issue_date", "receipt_date"} and value in {"明天", "下周", "下月"}:
        return MISSING
    if key in _DATES and not re.fullmatch(_DATE, value):
        return MISSING
    if key == "labor_qty" and not re.fullmatch(_NUMBER+r"\s*(?:个人|人|名)?", value):
        return MISSING
    if key == "equipment_qty" and not re.fullmatch(_NUMBER+r"\s*(?:台|套)?", value):
        return MISSING
    if key in {"user_duration", "idle_days"} and not re.fullmatch(_NUMBER+r"\s*(?:日历天|工作日|工日|天|日)?", value):
        return MISSING
    if key == "user_amount" and not re.fullmatch(_MONEY, value, re.I):
        return MISSING
    if key == "document_id" and (re.fullmatch(_DATE, re.sub(r"\s+", "", value)) or re.fullmatch(_NUMBER+r"\s*(?:人|个人|台|吨|天|日)?", value)):
        return MISSING
    return value


def _receipt(text):
    match = re.search(r"(?:由)?(业主代表|监理|业主|项目经理|收件人)?\s*([一-鿿]{2,4}|[A-Za-z]+(?:\s+[A-Za-z]+)*)\s*(?:已)?(?:签收了?|签的字|签字了?)", text)
    if not match:
        return []
    if _PENDING.search(text) or _UNKNOWN.search(text) or _UNSAFE.search(text):
        return [("receipt_record", text)]
    return [("receiver", match.group(2)), ("receiver_role", match.group(1) or MISSING), ("receipt_record", text)]


def _resources(text, explicit=None):
    pairs = []
    if _UNSAFE.search(text):
        return pairs
    labor = re.search(r"([一-鿿]{1,16}?(?:班组|工班|工队|工人|队))\s*("+_NUMBER+r"\s*(?:个人|人|名))", text)
    if labor:
        pairs += [("_resource_kind", "人工"), ("labor", labor.group(1)), ("labor_qty", MISSING if _PENDING.search(text) or _UNKNOWN.search(text) else labor.group(2)), ("resource_record", text)]
    machine = re.search(r"(?:还有|有)?(?:一?台)?\s*([A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*)\s*(塔吊|挖掘机|挖机|地泵|汽车泵|吊车|泵车)\s*("+_NUMBER+r"\s*(?:台|套))?", text)
    if machine:
        pairs += [("_resource_kind", "机械"), ("model", machine.group(1)), ("equipment", machine.group(2)), ("resource_record", text)]
        if machine.group(3):
            pairs.append(("equipment_qty", MISSING if _PENDING.search(text) or _UNKNOWN.search(text) else machine.group(3)))
    if not pairs and explicit:
        pairs = ([("_resource_kind", "机械"), ("equipment", text)] if explicit == "equipment" else [("resource_record", text)])
    return pairs


def _details(key, value):
    if key in {"idle", "equipment"}:
        return _resources(value, explicit=key)
    if key in {"labor", "labor_qty", "model", "equipment_qty"}:
        return [("_resource_kind", "人工" if key in {"labor", "labor_qty"} else "机械"), (key, value)]
    if key in {"stop_order", "issued_letter", "planned_letter"}:
        codes = re.findall(_CODE, value)
        pairs = []
        kind = "停工令" if key == "stop_order" else "往来函 / 通知（类别待核）"
        for code in codes or [value]:
            pairs += [("document_kind", kind), ("document_id", code)]
            if _typed("document_id", code) == MISSING and _value(code) != MISSING:
                pairs.append(("document_record", _FIELDS[key][0]+"："+value))
        if key == "issued_letter":
            pairs.append(("issued_record" if not (_PENDING.search(value) or _UNKNOWN.search(value) or _UNSENT_DOC.search(value)) else "document_record", _FIELDS[key][0]+"："+value))
        elif key == "planned_letter":
            pairs.append(("document_record", "拟发（非已发送）："+value))
        pairs += _receipt(value)
        return pairs
    pairs = [(key, value)]
    if key not in _ORIGINALS and _typed(key, value) == MISSING and _value(value) != MISSING:
        pairs.append(("record", _FIELDS[key][0]+"："+value))
    return pairs


def _inputs(raw):
    # Explicit contract extracts are one record, even with commas, semicolons
    # or conditional legal wording. Do not lose that wording to the scenario
    # filter or mistake words inside it for event/notification facts.
    protected, pieces, offset = {}, [], 0
    for line in raw.splitlines(keepends=True):
        match = _LABEL.match(line.strip())
        if match and ALIASES[match.group(1).casefold()] in _ORIGINALS:
            protected[offset] = line.strip()
            start = len(line)-len(line.lstrip())+match.start(2)
            pieces.append(line[:start]+"".join(c if c in "\r\n" else "X" for c in line[start:]))
        else:
            pieces.append(line)
        offset += len(line)
    for content, is_table, offset, source in _shared_inputs("".join(pieces)):
        line_start = raw.rfind("\n", 0, offset)+1
        if not is_table and line_start in protected:
            source = content = protected[line_start]
        yield content, is_table, offset, source


def _scan(text):
    text = re.sub(r"^(?:本次实际情况|实际情况|真实记录)\s*[:：]\s*", "", text)
    text = re.sub(r"^.{0,24}?(?:生成|起草|出|写)(?:一份)?索赔意向(?:草稿)?\s*[:：]?\s*", "", text)
    match = _LABEL.match(text)
    if match and ALIASES[match.group(1).casefold()] in _ORIGINALS:
        return _details(ALIASES[match.group(1).casefold()], match.group(2))
    embedded = _INNER_LABEL.search(text)
    if embedded:
        return _scan(text[:embedded.start()])+_details(ALIASES[embedded.group(1).casefold()], embedded.group(2))
    if match:
        return _details(ALIASES[match.group(1).casefold()], match.group(2))
    if _UNSAFE.search(text):
        return []
    pairs = []
    prefix = re.match(r"^(?:另一个|另一件|一个|第一件|第二件)(?:是|事是)\s*(.+)", text)
    if prefix:
        text = prefix.group(1)
        pairs += [("_new_event", "1"), ("event", text)]
    if re.search(r"甲供|图.*业主.*(?:拖|晚)|图纸迟到|图纸晚发|片区停电|管线迁改", text):
        pairs.append(("event", text))
    date = re.search(_DATE, text)
    if date:
        date_value = MISSING if _PENDING.search(text) else date.group()
        if re.search(r"合同约定|约定.*到场|应到", text):
            pairs.append(("agreed_date", date_value))
        elif re.search(r"实际.*(?:进场|到场)|才进场", text):
            pairs.append(("arrival", date_value))
        elif re.search(r"(?:结构图|图纸).*发|才发", text):
            pairs.append(("draw_date", date_value))
        elif re.search(r"停电|停工", text):
            pairs.append(("occurred", date_value))
        if date_value == MISSING:
            pairs.append(("record", text))
    location = _LOCATION.search(text)
    if location:
        pairs.append(("location", location.group()))
    if re.search(r"停着|停了|干等|窝工|窝着|闲置|趴窝", text):
        pairs.append(("loss_record", text))
    pairs += _resources(text)
    letter = re.search(r"(催货函|停工令|(?:发过|已发)(?:索赔意向)?函|函)("+_CODE+r")", text)
    if letter:
        pairs += [("document_kind", "催货函" if letter.group(1) == "催货函" else "停工令" if letter.group(1) == "停工令" else "往来函（类别待核）"), ("document_id", letter.group(2))]
        if re.search(r"发过|已发", text):
            pairs.append(("document_record" if _PENDING.search(text) or _UNKNOWN.search(text) or _UNSENT_DOC.search(text) else "issued_record", text))
    pairs += _receipt(text)
    if re.search(r"函.*(?:没发|未发)|(?:没发|未发).*函", text):
        pairs.append(("document_record", text))
    owner = re.search(r"(?:证据先让|先让|由)(?:资料员|商务部|商务员)?([一-鿿]{2,4})(?:去补|补证|负责补)", text)
    if owner:
        pairs += [("owner", owner.group(1)), ("action", text)]
    if _UNKNOWN.search(text) and re.search(r"合同|条款|日期|哪天|恢复|发函|通知|工期|金额|人数|证据|机械|班组", text):
        pairs.append(("record", text))
    return pairs


def parse(raw):
    data = Data()
    scope = {"jurisdiction": "UNSPECIFIED"}
    data.contexts.append(scope)
    current, children, origin, epoch = None, {}, "", 0

    def ref():
        return next((i for i, row in enumerate(data.events) if row is current), None)

    def new_event(retain=None):
        row = {**scope, "_origin": origin, "_file": epoch, "_scope": len(data.contexts)-1}
        if retain:
            row.update({k: retain[k] for k in {"event", "code"} if k in retain})
        data.events.append(row)
        return row

    def put(target, key, value):
        value = _typed(key, value)
        if target.get(key) in {None, MISSING, "UNSPECIFIED"}:
            target[key] = value
        elif value != MISSING and value not in target[key].split("；"):
            target[key] += "；"+value
        if value not in {MISSING, "UNSPECIFIED"}:
            data.provenance.append({"_record": target.get("_record", ref()), "_context": len(data.contexts)-1, "field": _FIELDS.get(key, ("资源类别",))[0], "value": value, "input_position": position})

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
            scope = {"jurisdiction": "UNSPECIFIED", "_origin": origin}
            data.contexts.append(scope)
            current, children = None, {}
            continue
        pairs = ([(k, v) for label, value in content if label.casefold() in ALIASES for k, v in _details(ALIASES[label.casefold()], value)] if is_table else _scan(source))
        if not pairs:
            continue
        stated = dict(pairs)
        late_meta = current is not None and any(k in stated and current.get(k) in {None, MISSING, "UNSPECIFIED"} for k in {"project", "jurisdiction", "contract", "contract_name"})
        if not is_table and late_meta:
            current, children = None, {}
        if not is_table and any(k in stated and scope.get(k) not in {None, MISSING, "UNSPECIFIED", _typed(k, stated[k])} for k in _META):
            critical = any(k in stated and scope.get(k) not in {None, MISSING, "UNSPECIFIED", _typed(k, stated[k])} for k in {"project", "jurisdiction", "contract", "contract_name"})
            if critical:
                scope = ({"jurisdiction": "UNSPECIFIED", "_origin": origin} if any(k in stated for k in {"project", "jurisdiction"})
                         else {k: v for k, v in scope.items() if k in {"project", "jurisdiction", "_origin"}})
            else:
                scope = {**scope}
            if any(k in stated for k in _ORIGINALS | {"contract_source"}):
                for key in _ORIGINALS | {"contract_source"}:
                    scope.pop(key, None)
            for key in _META:
                if key in stated:
                    scope.pop(key, None)
            data.contexts.append(scope)
            current, children = None, {}
        for key, value in pairs:
            if key in _META and not is_table:
                put(scope, key, value)
        late_event = current is not None and current.get("event") in {None, MISSING} and any(r.get("_record") == ref() for records in (data.resources, data.documents, data.evidence, data.actions) for r in records)
        if is_table or "_new_event" in stated or "event" in stated and current is not None and (current.get("event") not in {None, MISSING} or late_event):
            current, children = new_event(), {}
        elif current is not None and any(k in stated and current.get(k) not in {None, MISSING, _typed(k, stated[k])} for k in {"location", "code"}):
            current, children = new_event(current), {}
        elif current is not None and any(k in stated and current.get(k) not in {None, MISSING} for k in _DATES - {"issue_date", "receipt_date", "deadline"} | {"user_amount", "user_duration"}):
            prior = current
            current, children = new_event(prior), {}
            if prior.get("location"):
                current["location"] = prior["location"]
        if current is None and any(k not in _META for k, _ in pairs):
            current = new_event()
        if current is not None:
            for key, value in pairs:
                if key not in _RESOURCES | _DOCUMENTS | _EVIDENCE | _ACTIONS | _ADJUSTMENTS and not key.startswith("_"):
                    if is_table and key in _META:
                        current[key] = _typed(key, value)
                    put(current, key, value)
        for kind, keys, identities in (("resources", _RESOURCES, {"labor", "equipment", "model", "_resource_kind"}), ("documents", _DOCUMENTS, {"document_id", "document_kind"}), ("evidence", _EVIDENCE, {"evidence"}), ("actions", _ACTIONS, {"owner", "action"}), ("adjustments", _ADJUSTMENTS, {"adjustment"})):
            supplied = [(k, v) for k, v in pairs if k in keys]
            if not supplied:
                continue
            labor_fields, machine_fields = {"labor", "labor_qty"}, {"equipment", "model", "equipment_qty"}
            has_labor = any(k in labor_fields and _typed(k, v) != MISSING for k, v in supplied)
            has_machine = any(k in machine_fields and _typed(k, v) != MISSING for k, v in supplied)
            if kind == "resources" and has_labor != has_machine:
                other = machine_fields if has_labor else labor_fields
                supplied = [("_resource_kind", "人工" if has_labor else "机械")]+[(k, v) for k, v in supplied if k not in other | {"_resource_kind"}]
            if kind == "resources" and has_labor and has_machine:
                shared = [(k, v) for k, v in supplied if k in {"resource_source", "resource_record", "idle_days"}]
                if shared:
                    target = child(kind, True)
                    for key, value in shared:
                        put(target, key, value)
                    children.pop(kind)
                supplied = [("_resource_kind", "人工")]+[(k, v) for k, v in supplied if k in labor_fields]+[("_resource_kind", "机械")]+[(k, v) for k, v in supplied if k in machine_fields]
            if kind == "documents" and sum(k == "document_id" and _typed(k, v) != MISSING for k, v in supplied) > 1:
                shared = [(k, v) for k, v in supplied if k not in {"document_id", "document_kind"}]
                supplied = [(k, v) for k, v in supplied if k in {"document_id", "document_kind"}]
                if shared:
                    target = child(kind, True)
                    for key, value in shared:
                        put(target, key, value)
                    children.pop(kind)
            groups = [[]]
            for key, value in supplied:
                if key in {"_resource_kind", "document_kind"} and any(k == key and (kind != "resources" or v != value) for k, v in groups[-1]):
                    groups.append([])
                groups[-1].append((key, value))
            for group in groups:
                provided = dict(group)
                previous = children.get(kind, {})
                changed = any(k in provided and previous.get(k) not in {None, MISSING, _typed(k, provided[k])} for k in identities)
                repeated = any(k in provided and previous.get(k) not in {None, MISSING} for k in keys - identities) or kind == "documents" and "document_id" in provided and previous.get("document_id") not in {None, MISSING}
                repeated = repeated or kind == "resources" and any(k in provided and previous.get(k) not in {None, MISSING} for k in {"labor", "equipment", "model"})
                # A new resource identity cannot inherit a preceding resource's
                # quantity/evidence; a repeated observation has its own source.
                target = child(kind, fresh=changed or repeated)
                if repeated and not changed:
                    target.update({k: previous[k] for k in identities if k not in provided and k in previous})
                for key, value in group:
                    put(target, key, value)
            if kind == "documents" and sum(k == "document_id" for k, _ in supplied) > 1:
                children.pop(kind, None)
    return data


def _owned(data, records):
    return [{**(data.events[r["_record"]] if isinstance(r.get("_record"), int) else data.contexts[r["_context"]] if "_context" in r else {}), **r} for r in records]


def build_draft(post, tool_name, text):
    if post != "claim" or tool_name != TOOL:
        return None
    data = parse(text)
    owner = (("用户索赔事件（未核）", "event"), ("事件编号", "code"), ("影响工作面 / 部位", "location"), ("项目名称", "project"), ("辖区", "jurisdiction"), ("合同编号", "contract"), ("输入资料名", "_origin"))
    contract_gap = ("未提供合同条款原文：条款原文待贴；不从模板或记忆补天数。" if not any(c.get("contract_original") not in {None, MISSING} for c in data.contexts)
                    else "用户合同条款原文已登记（未核），不是已核实的适用依据。")
    return "\n".join([
        "# 索赔意向 / 调概事项草稿（AI 草稿 · 内部讨论）", "", DISCLAIMER, "", "- submit_blocked=true", "",
        "## 1 封面与草稿声明", "", _table(data.contexts, (("项目名称", "project"), ("辖区", "jurisdiction"), ("合同编号", "contract"), ("用户合同名称（适用性未核）", "contract_name"), ("输入资料名", "_origin"))), "",
        "缺项为 [A001] / UNSPECIFIED。本轮索赔金额和工期天数为 TBD，未发函、未送达、未签认。", "",
        "## 2 事件识别", "", _table(data.events, owner+(("用户事件来源", "event_source"), ("合同约定到场日期原文", "agreed_date"), ("用户实际进场 / 到场日期", "arrival"), ("用户实际发图日期", "draw_date"), ("用户停工 / 事件开始日期", "start"), ("用户事件发生日期", "occurred"), ("用户恢复 / 事件结束日期", "end"), ("用户日期来源", "date_source"), ("用户时间线原文", "timeline")), extra=(("本轮事件 / 日期核验", "未执行 / 待核"),)), "",
        "不同事件、部位、合同和资料文件不互相代填。日期不作相减，不由通知编号推日期。", "",
        "## 3 合同时钟", "", _table(data.contexts, (("合同编号", "contract"), ("用户合同名称（适用性未核）", "contract_name"), ("用户合同要求 / 索赔条款原文（未核）", "contract_original"), ("用户通知时限要求原文（未核）", "notice_original"), ("用户工期通知条款原文（未核）", "eot_original"), ("用户合同原文来源", "contract_source"), ("辖区", "jurisdiction")), extra=(("本轮通知时限 / 最后日期", "UNSPECIFIED · 未计算"), ("本轮条款适用性 / 程序核验", "未执行 / 待核"),)), "",
        "时限只登记本合同用户原文，不套示范文本天数、倒推通知日或断言失权。费用与工期程序分开核对。", "",
        contract_gap, "",
        "## 4 意向通知必备", "", _table(_owned(data, data.documents), owner+(("用户资料 / 通知类别（未核）", "document_kind"), ("用户函 / 通知 / 停工令编号", "document_id"), ("用户发出记录（非本轮发送）", "issued_record"), ("用户发函日期", "issue_date"), ("用户发出凭证", "issue_source"), ("用户拟收件人（非签收人）", "intended_recipient"), ("用户签收人（未核）", "receiver"), ("用户签收角色", "receiver_role"), ("用户签收日期", "receipt_date"), ("用户送达回证 / 签收凭证", "receipt_source"), ("用户签收记录（未核）", "receipt_record"), ("用户函件来源", "document_source"), ("用户通知 / 拟发记录", "document_record")), extra=(("本轮发送 / 送达 / 接受核验", "未发送 / 未送达 / 未核验"),)), "",
        "函号、催货函、停工令、计划发函或用户签收陈述不等于正式索赔意向已有效送达，不代填接收人或发送事实。", "",
        "### 用户意向请求（非获批）", "", _table(data.events, owner+(("用户主张方", "claimant"), ("用户相对方", "respondent"), ("用户意向请求", "request"), ("用户主张金额原文（未核）", "user_amount"), ("用户金额来源", "amount_source"), ("用户主张工期原文（未核）", "user_duration"), ("用户工期来源", "duration_source")), extra=(("本轮费用 / 工期请求核定", "金额 TBD / 工期天数 TBD · 未核定"),)), "",
        "## 5 证据清单", "", _table(_owned(data, data.evidence), owner+(("用户事件日期来源", "date_source"), ("用户合同要求原文（未核）", "contract_original"), ("用户影响资料 / 证据名称", "evidence"), ("用户证据位置原文（未核）", "evidence_position"), ("用户证据来源", "evidence_source"), ("该证据补证责任（明确归属才登记）", "evidence_owner")), extra=(("本轮证据核验", "未执行 / 待核"),)), "",
        "事件级补证计划另列；未说明负责哪份证据时，不把一般补证人或其他证据的责任人填进本行。", "",
        "### 用户同期资源与影响记录", "", _table(_owned(data, data.resources), owner+(("用户资源类别", "_resource_kind"), ("用户窝工 / 人工班组", "labor"), ("用户窝工人数原文（未核）", "labor_qty"), ("用户机械 / 设备名称", "equipment"), ("用户机械型号", "model"), ("用户机械数量原文（未核）", "equipment_qty"), ("用户停置天数原文（未核）", "idle_days"), ("用户同期记录来源", "resource_source"), ("用户窝工 / 闲置记录", "resource_record")), extra=(("本轮人数 / 台班 / 损失核定", "未执行 / 未计算"),)), "",
        "人员、机械型号、台数和停置天数分别登记，不把型号、楼号、文号、日期当数量，不推台班或损失。", "",
        "## 6 因果与责任栏", "", _table(data.events, owner+(("用户原因记录（非责任认定）", "cause"), ("用户责任归属记录（未核）", "responsibility"), ("用户损失记录（未核）", "loss_record"), ("用户网络图 / 关键线路资料", "network"), ("用户关键线路记录（非本轮计算）", "critical_record"), ("用户减损措施原文（未核）", "mitigation"), ("用户减损来源", "mitigation_source")), extra=(("本轮责任 / 因果 / 关键线路核验", "未执行 / 待核"),)), "",
        "不将施工错误或返工自行定为对方责任，也不因用户声称非己方过错认定索赔成立。", "",
        "## 7 费用组成口径", "", _table(data.events, owner+(("用户损失记录（未核）", "loss_record"), ("用户金额来源", "amount_source")), extra=(("本轮单价 / 合价 / 索赔金额", "TBD · 未计算 / 未核定"),)), "",
        "只整理已给损失资料，不默认利润、总部管理费或人工机械计价；不以日期、人数或设备数量算损失。", "",
        "## 8 调概专节", "", _table(_owned(data, data.adjustments), owner+(("用户调概事项", "adjustment"), ("用户批准概算记录（未核）", "budget"), ("用户调概依据原文（未核）", "adjustment_basis"), ("用户调概资料来源", "adjustment_source")), extra=(("本轮概算核验 / 调整批准", "未执行 / 未批准"),)), "",
        "调概与合同索赔不是同一程序，不将合同资料或意向草稿作为概算批复。", "",
        "## 9 与签证、验工接口", "", "变更指令内事实和价格路径交 variation；后续计量交 interim。索赔资料未核、不签发付款或工期批准。", "",
        "### 用户补证计划", "", _table(_owned(data, data.actions), owner+(("用户补证责任人", "owner"), ("用户补证行动", "action"), ("用户补证期限原文", "deadline")), extra=(("本轮补证 / 送达完成核验", "未执行 / 待核"),)), "",
        "## 10 自检", "", _table(_owned(data, data.provenance), owner+(("记录项", "field"), ("用户证据原文", "value"), ("输入位置（非原件页码）", "input_position"))), "",
        "### 用户待核资料", "", _table(data.events, owner+(("用户待核记录", "record"), ("未分派资料来源", "source")), extra=(("本轮签认状态", "未签认"), ("复核人 / 签字 / 日期", "（空）"))), "",
        "本稿不提供索赔胜率或法定权利结论，未核合同版本及条款，无批准、送达或结算结论。", "",
    ])
