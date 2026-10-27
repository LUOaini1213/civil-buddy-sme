"""Source-owned safety-briefing notes, not a site instruction or signed briefing.

Only user-named steps, hazards, parameters and measures are registered. A
different workface, step, file or date never inherits protection or contacts.
The expert runtime, not this pure renderer, owns confirmation and persistence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

from packing_assistant.post_drafts.planning import _inputs, _table
from packing_assistant.post_drafts.procurement import _source_name

MISSING = "[A001] / UNSPECIFIED"
TOOL = "safety-brief__talk"
DISCLAIMER = ("AI 草稿 · 内部讨论 · 不得作为法定专项方案 / 交底签认件。本稿只登记用户提供的工序、"
              "危险点、参数和措施，未核验专项方案、现场条件、参数适用性、联系人或现行规范。"
              "不是工人口播、现场指令、作业许可或签认件，须持证人员依据正式文本复核签字后方可实施。")
_NUM = r"(?:[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|[一二三四五六七八九十百两]+)"
_DATE = r"(?:\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?|\d{1,2}月\d{1,2}[日号]|今天|今日|明天|明日|周[一二三四五六日天])"
_LOCATION = re.compile(r"\d+[#＃]楼(?:[一二三四五六七八九十\d]+层)?(?:电梯井口|东立面|西立面|南立面|北立面|屋面|外架|顶板|墙柱)?|B\d+层(?:西南角|东南角|东北角|西北角)?(?:集水坑)?|屋面女儿墙(?:外侧|内侧)?|地下室(?:B\d+层)?(?:\d+区)?(?:集水坑)?")
_UNKNOWN = re.compile(r"未提供|未给|未定|未知|待填|待核|待补|待确认|没给|没定|没报|还没|没有|不知道|不清楚|多少|UNSPECIFIED|TBD|N/A|unknown|not provided", re.I)
_UNSAFE = re.compile(r"例如|比如|假设|如果|示例|样例|自行计算|自己算|不采用|别按|不是|是否|[？?]|\b(?:for example|assuming|hypothetical|whether)\b", re.I)
_PENDING = re.compile(r"计划|预计|拟|准备|明天|明日|将|待|听说|据说|未核|未测|没测|未经|未验|尚未|还未", re.I)
_FIELDS = {
    "project": ("工程名称", "项目名称", "项目", "Project"), "jurisdiction": ("辖区", "Jurisdiction"),
    "date": ("交底日期", "日期", "Briefing date", "Date"), "version": ("交底版本", "版本", "Version"),
    "location": ("作业部位", "工作面", "作业面", "部位", "位置", "Location", "Workface"),
    "work_id": ("工作面编号", "交底编号", "Workface ID"), "process": ("作业内容", "工序", "工作内容", "Process"),
    "step": ("作业步骤", "步骤", "Step"), "crew": ("交底对象", "接受班组", "班组", "Crew"),
    "briefing_person": ("用户交底人", "拟交底人", "交底人", "Briefing person"),
    "recipient": ("用户接受人", "接受人", "Recipient"), "preparation": ("作业准备", "准备条件", "Preparation"),
    "conditions": ("作业条件", "现场条件", "Conditions"), "scheme": ("专项方案", "方案名称", "方案", "Scheme"),
    "scheme_source": ("方案来源", "方案文件来源", "Scheme source"), "drawing": ("用户图号", "图号", "Drawing"),
    "material": ("材料名称", "构件名称", "材料", "构件", "Material"), "spec": ("规格型号", "规格", "型钢规格", "Specification"),
    "spacing": ("挑梁间距", "间距", "Spacing"), "height": ("洞口落差", "落差", "高度", "净空", "Height"),
    "load": ("用户荷载", "荷载", "Load"), "dimension": ("用户尺寸", "尺寸", "Dimension"),
    "parameter_source": ("参数来源", "参数依据", "Parameter source"),
    "hazard": ("用户危险点", "危险点", "危险源", "Hazard"), "measures": ("防护措施", "防护要点", "措施", "Measures"),
    "measure_basis": ("措施依据", "Measure basis"), "hazard_source": ("危险点来源", "交底措施来源", "Hazard source"),
    "stop": ("喊停条件", "停工条件", "停止作业条件", "Stop condition"), "interface": ("责任接口", "责任人", "Owner"),
    "ppe": ("个人防护", "防护用品", "PPE"), "ppe_spec": ("防护用品规格", "PPE specification"),
    "prohibited": ("禁止事项", "禁止作业", "Prohibited"), "direction": ("撤离方向", "撤离路线", "Escape route"),
    "emergency_action": ("用户应急动作", "应急动作", "Emergency action"),
    "contact_role": ("联系角色", "联系人角色", "Contact role"), "contact": ("联系人", "Contact"),
    "monitor": ("监护人", "监护", "Monitor"), "safety_officer": ("安全员", "Safety officer"),
    "leader": ("带班人", "班组长", "Leader"), "report": ("报告对象", "报告联系人", "Report contact"),
    "emergency": ("应急联系人", "急救联系人", "Emergency contact"), "phone": ("联系电话", "电话", "Phone"),
    "contact_source": ("联系人来源", "Contact source"), "standard": ("规范名称", "规范", "Standard"),
    "monitor_phone": ("监护电话", "监护人电话", "Monitor phone"),
    "safety_officer_phone": ("安全员电话", "Safety officer phone"),
    "leader_phone": ("带班人电话", "班组长电话", "Leader phone"),
    "report_phone": ("报告对象电话", "Report phone"), "emergency_phone": ("应急电话", "应急联系人电话", "Emergency phone"),
    "clause": ("用户条款号", "条款号", "Clause"), "standard_source": ("规范来源", "Standard source"),
    "user_record": ("用户实施记录", "用户检查记录", "实施记录", "检查记录", "User record"),
    "planned_record": ("用户计划记录", "计划记录", "Planned record"),
    "attachments": ("附件目录", "附件", "Attachments"), "source": ("资料来源", "来源文件", "来源", "Source"),
    "record": ("待核记录", "未完事项", "Record"),
}
ALIASES = {a.casefold(): k for k, labels in _FIELDS.items() for a in labels}
_LABEL = re.compile(r"^("+"|".join(re.escape(a) for a in sorted(ALIASES, key=len, reverse=True))+r")\s*[:：=]\s*(.*)$", re.I)
_META = {"project", "jurisdiction", "date", "version"}
_PARAMS = {"material", "spec", "spacing", "height", "load", "dimension", "parameter_source"}
_HAZARDS = {"hazard", "measures", "measure_basis", "stop", "interface", "hazard_source", "user_record", "planned_record"}
_CONTACTS = {"contact_role", "contact", "phone", "contact_source", "_role_phone"}
_ROLES = {"monitor": "监护人", "safety_officer": "安全员", "leader": "带班 / 班组长", "report": "报告对象", "emergency": "应急联系人"}


@dataclass
class Data:
    contexts: list[dict[str, str]] = field(default_factory=list)
    works: list[dict[str, str]] = field(default_factory=list)
    parameters: list[dict[str, str]] = field(default_factory=list)
    hazards: list[dict[str, str]] = field(default_factory=list)
    contacts: list[dict[str, str]] = field(default_factory=list)
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
    if key in {"location", "work_id", "process", "step", "crew", "contact", "briefing_person", "recipient", "material", "spec", "spacing", "height", "load", "dimension", "date", "phone", "hazard"} and (_UNSAFE.search(value) or _UNKNOWN.search(value)):
        return MISSING
    if key in {"spacing", "height", "dimension"} and not re.fullmatch(r"(?:约\s*)?"+_NUM+r"\s*(?:毫米|厘米|米|mm|cm|m)?", value, re.I):
        return MISSING
    if key == "load" and not re.fullmatch(r"(?:约\s*)?"+_NUM+r"\s*(?:kN(?:/m[²2])?|kPa|kg|吨|t)?", value, re.I):
        return MISSING
    if key == "phone" and (not re.fullmatch(r"\+?[\d() -]{3,30}", value) or re.fullmatch(_DATE, value)):
        return MISSING
    if key == "date" and not re.fullmatch(_DATE, value):
        return MISSING
    return value


def _process(value):
    pairs = [("process", value)]
    loc = _LOCATION.match(value)
    if loc:
        pairs.append(("location", loc.group()))
        remaining = value[loc.end():].lstrip(" ：:，,要搭做")
        if remaining:
            pairs[0] = ("process", remaining)
    return pairs


def _details(key, value):
    if key == "process":
        return _process(value)
    if key in _ROLES:
        return [("contact_role", _ROLES[key]), ("contact", value)]
    if key.endswith("_phone") and key[:-6] in _ROLES:
        return [("contact_role", _ROLES[key[:-6]]), ("_role_phone", value)]
    if key == "user_record" and _PENDING.search(value):
        return [("planned_record", value)]
    return [(key, value)]


def _scan(text):
    text = re.sub(r"^.{0,24}?(?:写一份|写份|生成|起草|出份|出个)(?:一份)?安全(?:技术)?交底(?:草稿)?\s*[:：]?\s*", "", text)
    label = _LABEL.match(text)
    if label:
        return _details(ALIASES[label.group(1).casefold()], label.group(2))
    if _UNSAFE.search(text):
        return []
    pairs = []
    loc = _LOCATION.search(text)
    if loc:
        pairs.append(("location", loc.group()))
        remaining = text[loc.end():].strip()
        process = re.match(r"(?:要|搭|做)?(.+?)(?=得挂|需要|用|$)", remaining)
        if process and process.group(1).strip():
            pairs.append(("process", (loc.group()+process.group(1)) if loc.group().startswith("屋面女儿墙") else process.group(1)))
    crew = re.search(r"(?:给|^|由)?(架子班|土建班|木工班|钢筋班|泥工班|机电班|吊篮班)(?:组)?", text)
    if crew:
        pairs.append(("crew", crew.group(1)))
    date = re.match(r"^("+_DATE+r").*交底", text)
    if date:
        pairs.append(("date", date.group(1)))
    for pattern, role in ((r"(?:现场)?安全员([\u4e00-\u9fff]{2,4}?)(?=全程|盯|负责|$)", "安全员"),
                          (r"监护人([\u4e00-\u9fff]{2,4}?)(?=负责|监护|$)", "监护人"),
                          (r"(?:木工班|架子班|土建班)([\u4e00-\u9fff]{2,4}?)(?=带人|带班|负责)", "带班 / 班组长")):
        contact = re.search(pattern, text)
        if contact:
            pairs.extend((("contact_role", role), ("contact", contact.group(1))))
    material = re.search(r"(?:挑梁|型钢)(?:用|采用)\s*(.+)", text)
    if material:
        pairs.extend((("material", "挑梁"), ("spec", material.group(1))))
    spacing = re.search(r"(?:挑梁)?间距\s*("+_NUM+r"\s*(?:毫米|厘米|米|mm|cm|m))", text, re.I)
    if spacing:
        pairs.append(("spacing", spacing.group(1)))
    height = re.search(r"(?:洞口下面空着|落差|净空|高度)\s*("+_NUM+r"\s*(?:毫米|厘米|米|mm|cm|m))", text, re.I)
    if height:
        pairs.append(("height", height.group(1)))
    hazard = re.search(r"危险(?:点|源)(?:我就想到|是|为|有|：|:)?\s*(.+)", text)
    if hazard:
        pairs.append(("hazard", hazard.group(1)))
        if re.search(r"两个面|共同|我就想到|不确定哪个", text):
            pairs.append(("_unassigned", "hazard"))
    stop = re.search(r"^(.+?)(?:就停|立即停工|停止作业)$", text)
    if stop:
        pairs.append(("stop", stop.group(1)))
    if re.search(r"(?:作业前通风|进前测|防护措施|设置.*防护|检测超标.*撤出)", text):
        pairs.append(("stop" if "撤出" in text else "measures", text))
    if re.search(r"挂安全带|戴安全帽|用防护镜|戴手套|呼吸防护", text):
        ppe = re.search(r"安全带|安全帽|防护镜|手套|呼吸防护", text)
        pairs.extend((("ppe", ppe.group()), ("measures", text if not loc else text[text.find("得挂")+1:])))
    if re.search(r"还没定班组|班组未定|谁监护.*没定|联系人.*未定", text):
        pairs.append(("record", text))
    if text in {"临边", "高处", "洞口", "脚手架", "有限空间"}:
        pairs.append(("process", text))
    return pairs


def parse(raw):
    data = Data()
    scope = {"jurisdiction": "UNSPECIFIED"}
    data.contexts.append(scope)
    current, origin, children = None, "", {}

    def ref():
        return next((i for i, row in enumerate(data.works) if row is current), None)

    def new_work():
        row = {"_origin": origin, "_scope": len(data.contexts)-1,
               **{k: scope.get(k, "UNSPECIFIED" if k == "jurisdiction" else MISSING) for k in _META}}
        data.works.append(row)
        return row

    def put(target, key, value):
        value = _typed(key, value)
        if target.get(key) in {None, MISSING, "UNSPECIFIED"}:
            target[key] = value
        elif value != MISSING and value not in target[key].split("；"):
            # Independent repetitions are rows, not a giant pasted cell.
            target[key] += "；"+value
        if value not in {MISSING, "UNSPECIFIED"}:
            data.evidence.append({"_record": target.get("_record", ref()), "field": _FIELDS[key][0], "value": value, "input_position": position})

    def child(kind, fresh=False, unassigned=False):
        if fresh or kind not in children:
            children[kind] = {"_record": None if unassigned else ref(), "_step": (current or {}).get("step") or (current or {}).get("process") or MISSING}
            if unassigned:
                children[kind]["_step"] = MISSING
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
            scope = {"_origin": origin, "jurisdiction": "UNSPECIFIED"}
            data.contexts.append(scope)
            current, children = None, {}
            continue
        pairs = ([(key, val) for label, value in content if label.casefold() in ALIASES for key, val in _details(ALIASES[label.casefold()], value)] if is_table else _scan(source))
        if not pairs:
            continue
        stated = dict(pairs)
        if not is_table and any(k in stated and scope.get(k) not in {None, MISSING, "UNSPECIFIED", _typed(k, stated[k])} for k in _META):
            scope = {"_origin": origin, "jurisdiction": "UNSPECIFIED"}
            data.contexts.append(scope)
            current, children = None, {}
        declared_location = "location" in stated and current is not None and current.get("location") not in {None, MISSING}
        if is_table or declared_location or any(k in stated and current is not None and current.get(k) not in {None, MISSING, _typed(k, stated[k])} for k in {"process", "step"}):
            prior_location = (current or {}).get("location") if "location" not in stated else None
            current, children = new_work(), {}
            if prior_location and not is_table:
                current["location"] = prior_location
        elif "work_id" in stated and _typed("work_id", stated["work_id"]) != MISSING:
            identifier = _typed("work_id", stated["work_id"])
            found = [r for r in data.works if r["_scope"] == len(data.contexts)-1 and r.get("work_id") == identifier]
            if current is None or current.get("work_id") not in {None, MISSING, identifier} or len(found) > 1:
                current, children = (found[0] if len(found) == 1 else new_work()), {}
            elif current.get("work_id") == identifier:
                children = {}
        for key, value in pairs:
            if key in _META:
                put(scope, key, value)
                if current is not None:
                    put(current, key, value)
        if any(k not in _META | {"_unassigned"} for k, _ in pairs) and current is None:
            current = new_work()
        # Apply work identity before creating children even for reordered tables.
        for key, value in pairs:
            if key not in _META | _PARAMS | _HAZARDS | _CONTACTS | {"_unassigned"}:
                put(current, key, value)
        for kind, keys, identity in (("parameters", _PARAMS, "material"), ("hazards", _HAZARDS, "hazard"), ("contacts", _CONTACTS, "contact")):
            supplied = [(k, v) for k, v in pairs if k in keys]
            if not supplied:
                continue
            orphan_contact = []
            if kind == "contacts" and sum(k == "contact" for k, _ in supplied) > 1:
                # An unqualified phone/source in a row naming several roles
                # has no authority to choose the first person in column order.
                orphan_contact = [(k, v) for k, v in supplied if k in {"phone", "contact_source"}]
                supplied = [(k, v) for k, v in supplied if k not in {"phone", "contact_source"}]
                if orphan_contact:
                    target = child(kind, fresh=True)
                    for key, value in orphan_contact:
                        put(target, key, value)
                    children.pop(kind)
            groups = [[]]
            for key, value in supplied:
                if kind == "contacts" and key in {"contact", "contact_role"} and any(k == key for k, _ in groups[-1]):
                    groups.append([])
                groups[-1].append((key, value))
            for group in groups:
                provided = dict(group)
                previous = children.get(kind, {})
                qualified = "_role_phone" in provided
                matched = [r for r in data.contacts if r.get("_record") == ref() and r.get("contact_role") == provided.get("contact_role") and r.get("contact") not in {None, MISSING}] if qualified and "contact" not in provided else []
                identity_change = identity in provided and previous.get(identity) not in {None, MISSING}
                role_change = kind == "contacts" and "contact_role" in provided and previous.get("contact_role") not in {None, MISSING, provided["contact_role"]}
                repeat = any(k in provided and previous.get(k) not in {None, MISSING} for k in keys - {identity, "contact_role"})
                nature_change = kind == "hazards" and (("user_record" in provided and previous.get("planned_record")) or ("planned_record" in provided and previous.get("user_record")))
                unassigned = kind == "hazards" and stated.get("_unassigned") == "hazard" and len(data.works) > 1
                ambiguous_role = qualified and len(matched) > 1
                changed_phone = len(matched) == 1 and matched[0].get("phone") not in {None, MISSING, _typed("phone", provided["_role_phone"])} if qualified else False
                target = matched[0] if len(matched) == 1 and not changed_phone else child(kind, fresh=identity_change or role_change or repeat or nature_change or unassigned or ambiguous_role or changed_phone, unassigned=unassigned)
                if changed_phone:
                    target.update({k: matched[0][k] for k in ("contact", "contact_role") if k in matched[0]})
                if len(matched) == 1:
                    children[kind] = target
                if (repeat or nature_change) and not identity_change and not role_change:
                    for k in {identity, "contact_role"} if kind == "contacts" else {identity}:
                        if k not in provided and k in previous:
                            target[k] = previous[k]
                for key, value in group:
                    put(target, "phone" if key == "_role_phone" else key, value)
    return data


def _owned(data, records):
    result = []
    for row in records:
        work = data.works[row["_record"]] if isinstance(row.get("_record"), int) else {}
        joined = {**work, **row}
        if joined.get("_step") in {None, MISSING}:
            joined["_step"] = work.get("step") or work.get("process") or MISSING
        result.append(joined)
    return result


def build_draft(post, tool_name, text):
    if post != "safety-brief" or tool_name != TOOL:
        return None
    data = parse(text)
    owner = (("作业部位 / 工作面", "location"), ("工作面编号", "work_id"), ("用户工序 / 作业内容", "process"), ("交底日期原文", "date"), ("项目名称", "project"), ("辖区", "jurisdiction"), ("输入资料名", "_origin"))
    hazards = _owned(data, data.hazards)
    return "\n".join([
        "# 安全技术交底草稿（AI 草稿 · 内部讨论）", "", DISCLAIMER, "", "- submit_blocked=true", "",
        "## 1 封面", "", _table(data.contexts, (("工程 / 项目名称", "project"), ("交底日期原文", "date"), ("辖区", "jurisdiction"), ("交底版本", "version"), ("输入资料名", "_origin"))), "",
        "给现场技术员的讨论用草稿。交底人、接受人、日期与所有签认栏：（空）。相对日期不换算，提供姓名或日期不代表已经签字。", "",
        "## 2 草稿声明", "", DISCLAIMER, "",
        "## 3 作业部位与范围", "", _table(data.works, owner+(("用户交底对象 / 接受班组", "crew"), ("用户拟交底人（非签字）", "briefing_person"), ("用户接受人记录（非签字）", "recipient"), ("用户待核 / 未完事项", "record"))), "",
        "## 4 作业内容和工序步骤", "", _table(data.works, owner+(("用户作业步骤", "step"), ("用户准备条件", "preparation"), ("用户作业条件（未核）", "conditions"), ("用户专项方案标识", "scheme"), ("方案来源", "scheme_source"), ("用户图号", "drawing")), extra=(("本轮方案 / 作业条件核验", "未执行 / 待核"),)), "",
        "### 用户材料及参数（非本轮设计值）", "", _table(_owned(data, data.parameters), owner+(("材料 / 构件名称", "material"), ("型钢 / 构件规格原文", "spec"), ("用户挑梁间距原文", "spacing"), ("用户洞口高度 / 落差 / 净空", "height"), ("用户荷载原文", "load"), ("用户尺寸原文", "dimension"), ("参数依据 / 来源", "parameter_source")), extra=(("本轮参数适用性核验", "未执行 / 待核"),)), "",
        "只登记用户给出的原值，不默认毫米、米或荷载，不换算、计算、补图号或推断方案满足条件。", "",
        "## 5 危险源", "", _table(hazards, owner+(("用户作业步骤 / 原文工序", "_step"), ("用户已识别危险点 / 危险源", "hazard"), ("危险点来源", "hazard_source")), extra=(("本轮危险源识别 / 关联核验", "未执行 / 待核"),)), "",
        "不按部位名称补危险源。范围未明确的共用记录单列待归属，不复制到每个工作面或步骤。", "",
        "## 6 防护要点", "", _table(hazards, owner+(("用户作业步骤", "_step"), ("用户危险点", "hazard"), ("用户措施原文（非已落实）", "measures"), ("用户措施依据", "measure_basis"), ("用户停工条件", "stop"), ("用户责任接口", "interface"), ("交底措施来源", "hazard_source"), ("用户实施记录（未核）", "user_record"), ("用户计划 / 据说记录（非实际核验）", "planned_record")), extra=(("待填项 / 本轮措施核验", "[A001] / 未执行 · 待持证人员核验"),)), "",
        "用户措施和已提供的时长仅作待核记录，不是本轮安全建议；通风时长不等于检测结果或作业许可，不因资料名称判断措施落实。", "",
        "## 7 个人防护", "", _table(data.works, owner+(("用户个人防护用品原文", "ppe"), ("用户用品规格", "ppe_spec")), extra=(("本轮防护配置 / 落实核验", "未执行 / 待核"),)), "",
        "## 8 禁止事项与喊停条件", "", _table(hazards, owner+(("用户作业步骤", "_step"), ("用户危险点", "hazard"), ("用户停工 / 喊停条件原文", "stop")), extra=(("本轮现场发令 / 停工执行", "未执行 · 未签发"),)), "",
        _table(data.works, owner+(("用户禁止事项原文", "prohibited"),)), "",
        "## 9 应急要点", "", _table(data.works, owner+(("用户撤离方向 / 路线", "direction"), ("用户应急动作原文", "emergency_action")), extra=(("本轮应急可用性核验", "未执行 / 待核"),)), "",
        "### 用户责任与联系接口", "", _table(_owned(data, data.contacts), owner+(("用户监护 / 安全员 / 带班 / 联系角色", "contact_role"), ("用户责任接口 / 联系人原文", "contact"), ("用户联系电话原文（未核）", "phone"), ("联系人来源", "contact_source")), extra=(("本轮身份 / 电话核验", "未执行 / 待核"),)), "",
        "联系人、监护人和电话不能借给其他工作面或角色，不默认热线、急救电话或撤离路线。", "",
        "## 10 依据", "", _table(data.works, owner+(("用户规范名称原文", "standard"), ("用户条款号原文（未核）", "clause"), ("规范来源", "standard_source"), ("未分派资料来源", "source"), ("用户附件目录", "attachments")), extra=(("本轮原文 / 现行版本核验", "unverified / UNSPECIFIED"),)), "",
        _table(_owned(data, data.evidence), owner+(("记录项", "field"), ("用户证据原文", "value"), ("输入位置（非原件页码）", "input_position"))), "",
        "不默认国家、规范标题或条款。字段来源只登记原文，不借通用来源代填方案、参数或联系人依据。", "",
        "## 11 签字栏", "", _table([{}], (), extra=(("交底人签字", "（空）"), ("接受班组签字", "（空）"), ("安全员签字", "（空）"), ("签认日期", "（空）"), ("本轮签认 / 许可", "未执行 · 未签发"))), "",
        "本稿不判危大或超规模，不代替专项方案、许可、现场交底或持证人员签认。", "",
    ])
