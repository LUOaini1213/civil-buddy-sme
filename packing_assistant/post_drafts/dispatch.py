"""Literal dispatch observations and proposed actions, never operating orders.

Faces, dates/shifts and sources own their progress, people, machines, materials,
stops and actions. Planned work remains planned across following clauses until
an explicit actual cue. No completion rate, schedule recovery or permit is made.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

from packing_assistant.post_drafts.planning import _inputs, _table
from packing_assistant.post_drafts.procurement import _source_name

MISSING = "[A001] / UNSPECIFIED"
TOOL = "dispatch__daily"
DISCLAIMER = ("AI 草稿 · 内部讨论 · 不得作为法定专项方案 / 交底签认件。本稿仅整理用户调度资料，"
              "未核验现场实绩、指令权限、资源台账或停复工条件，不构成调度令、停复工令、监理指令或工期承诺。"
              "不计算产值、完成率或进度偏差，不替领导发令、签认或执行现场作业。")
_NUM = r"(?:[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|[一二三四五六七八九十百两俩]+)"
_UNIT = r"(?:立方米|平方米|立方|平米|吨|方|袋|根|块|卷|米|t|kg|m[³3]|m[²2]|m)"
_QTY = re.compile(r"(?<![A-Za-z0-9_.#/-])(?:约\s*)?"+_NUM+r"\s*"+_UNIT+r"(?![A-Za-z0-9_.#/-])", re.I)
_DATE = r"(?:\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?|\d{1,2}月\d{1,2}[日号]|今天|今日|昨天|昨日|明天|明日|周[一二三四五六日天])"
_CLOCK = r"(?:\d{1,2}[:：]\d{2}|\d{1,2}点(?:半|\d{1,2}分)?)"
_DUE = r"(?:"+_DATE+r"|次日)(?:"+_CLOCK+r")?(?:早上|上午|下午|中午|晚上|收工)?(?:前|之前)?"
_FACE = re.compile(r"\d+[#＃]楼(?:[一二三四五六七八九十\d]+层)?(?:墙柱|顶板|底板|外架)?|地下室(?:B\d+层)?(?:\d+区)?(?:底板|外墙)?")
_TRADE = re.compile(r"木工|钢筋工|泥工|架子工|混凝土工|瓦工|电工|普工")
_MACHINE = re.compile(r"汽车泵|汽车吊|塔吊|塔式起重机|泵车|挖掘机|挖机|升降机|压路机|吊车|(?<![A-Za-z0-9_/-])(?:tower crane|mobile crane|excavator|hoist)(?![A-Za-z0-9_/-])", re.I)
_MODEL = re.compile(r"(?<![A-Za-z0-9_.-])[A-Z]{1,12}\d+[A-Za-z0-9]*(?:[-_/][A-Za-z0-9]+)*(?![A-Za-z0-9_.-])", re.I)
_MATERIAL = re.compile(r"钢管扣件|防水卷材|螺纹钢|加气块|钢筋|水泥|混凝土|模板|铝模|钢管|扣件|材料")
_SENSITIVE = re.compile(r"临边防护|临边|高处作业|高处|外架|脚手架|模板支撑|起重吊装|起重|有限空间|深基坑|交通导改")
_WEATHER = r"(?:晴天|晴|多云|阴天|阴|雷阵雨|暴雨|大雨|中雨|小雨|阵雨|雨|大雪|小雪|雪|大雾|雾|台风)"
_UNKNOWN = re.compile(r"还没|未提供|未给|未定|未报|未知|待填|待核|待补|待确认|没给|没定|没报|没带|没确认|忘了|没有|没人|不知道|不清楚|多少|UNSPECIFIED|TBD|N/A|unknown|not provided", re.I)
_UNSAFE = re.compile(r"例如|比如|假设|如果|示例|样例|自行计算|自己算|不采用|别按|不是|是否|[？?]|\b(?:for example|assuming|hypothetical|whether)\b", re.I)
_FUTURE = re.compile(r"明天|明日|次日|后天|预报|预计|计划|准备|拟安排|打算|将要|安排.*(?:进场|上人|复工)|(?:需(?:要|用)?|拟|将|待)[^，,；;。\n]{0,12}(?:进场|到场|出勤|上人|人)|\b(?:planned|forecast|tomorrow|next day|will)\b", re.I)
_CURRENT = re.compile(r"^(?:今天|今日|当日|实际情况|真实记录|实际|实到)|^(?:actual|today)\b", re.I)
_ACTUAL_CUE = re.compile(r"浇了|铺贴|才搭到|正在|已安装|安装了|已浇筑|拼装|已完成|完成了")
_NOT_ACTUAL = re.compile(r"未完成|没完成|尚未完成|未浇|没浇|未安装|没安装|未铺贴|没铺贴|听说|据说|\b(?:not|reportedly)\b", re.I)
_LABOR_NONACTUAL = re.compile(r"缺勤|未到|请假|缺席|定员|最多|上限|容量|至少|不少于|缺了|少了|缺人")
_FIELDS = {
    "project": ("项目名称", "工程名称", "项目", "Project"), "jurisdiction": ("辖区", "Jurisdiction"),
    "date": ("调度日期", "日期", "Date"), "shift": ("班次", "班别", "Shift"), "weather": ("天气", "Weather"),
    "forecast_weather": ("天气预报", "预报天气", "Forecast weather"), "version": ("日报版本", "版本", "Version"),
    "face": ("作业面", "工作面", "施工部位", "部位", "Workface"), "face_id": ("工作面编号", "记录编号", "Face ID"),
    "process": ("作业内容", "工序", "Process"), "actual": ("今日完成", "当日实际", "实际完成", "用户施工状态", "用户事实", "Actual record"),
    "quantity": ("用户实际数量", "当日数量", "完成量", "Quantity"), "actual_source": ("实绩来源", "现场记录来源", "Actual source"),
    "start": ("实际开始", "开始时间", "Start time"), "finish": ("实际结束", "结束时间", "End time"),
    "plan": ("计划节点", "计划要求", "计划", "Plan"), "plan_source": ("计划来源", "Plan source"), "target": ("计划节点日期", "节点日期", "Target date"),
    "plan_quantity": ("计划数量", "Planned quantity"), "next": ("明日安排", "明日计划", "下步安排", "Next plan"),
    "labor": ("工种", "班组", "劳动力", "Labor", "Crew"), "people": ("实到人数", "出勤人数", "人数", "People", "Workers"), "labor_source": ("人员来源", "出勤来源", "Labor source"), "labor_record": ("人员动态", "人员记录", "Labor record"),
    "machine": ("机械名称", "机械", "设备", "Machine"), "model": ("机械型号", "设备型号", "型号", "Model"),
    "machine_count": ("机械数量", "台数", "Machine quantity"), "machine_record": ("机械开停记录", "机械状态", "故障记录", "Machine record"),
    "machine_source": ("机械来源", "Machine source"), "material": ("材料名称", "材料", "物资", "Material"),
    "material_record": ("材料动态", "到货记录", "待料记录", "Material record"), "material_quantity": ("材料数量", "Material quantity"), "material_source": ("材料来源", "Material source"),
    "instruction": ("指令内容", "指令", "调度待办", "Instruction"), "issuer": ("下达人", "指令来源", "指令人", "Issuer"),
    "receiver": ("接收人", "接收班组", "接收方", "Receiver"), "owner": ("责任人", "责任方", "负责人", "执行人", "Owner"),
    "deadline": ("要求期限", "期限", "截止时间", "Deadline"), "order_time": ("指令时间", "Order time"),
    "order_source": ("指令文件来源", "指令凭证", "Instruction source"), "precondition": ("指令前置条件", "前置条件", "Precondition"),
    "action_record": ("用户落实记录", "指令状态原文", "Action record"), "action_plan": ("用户计划落实", "计划落实", "Action plan"),
    "stop": ("停工记录", "停工", "停电记录", "Stop record"), "stop_reason": ("停工原因", "原因", "Stop reason"),
    "stop_impact": ("影响节点", "停工影响", "Stop impact"), "stop_source": ("停工来源", "Stop source"),
    "restart_plan": ("拟复工条件", "复工计划", "拟恢复条件", "Restart plan"), "restoration": ("用户恢复记录", "实际恢复记录", "Restoration record"),
    "outside": ("外部制约", "外部事项", "External constraint"), "handover": ("工作面交接", "交叉作业", "交接记录", "Handover"),
    "sensitive": ("敏感作业名称", "敏感作业", "Sensitive work"), "sensitive_window": ("敏感作业时段", "Sensitive window"),
    "attachments": ("附件目录", "附件", "Attachments"), "witness": ("旁站资料", "Witness evidence"),
    "briefing": ("交底资料", "Briefing evidence"), "acceptance": ("验收资料", "Acceptance evidence"),
    "source": ("来源文件", "资料来源", "来源", "Source"), "record": ("待核记录", "记录", "Record"),
}
ALIASES = {a.casefold(): k for k, labels in _FIELDS.items() for a in labels}
_LABEL = re.compile(r"^("+"|".join(re.escape(a) for a in sorted(ALIASES, key=len, reverse=True))+r")\s*[:：=]\s*(.*)$", re.I)
_META = {"project", "jurisdiction", "date", "shift", "weather", "forecast_weather", "version"}
_LABOR = {"labor", "people", "labor_source", "labor_record"}
_PLANT = {"machine", "model", "machine_count", "machine_record", "machine_source"}
_MATERIALS = {"material", "material_record", "material_quantity", "material_source"}
_ACTIONS = {"instruction", "issuer", "receiver", "owner", "deadline", "order_time", "order_source", "precondition", "action_record", "action_plan"}
_STOPS = {"stop", "stop_reason", "stop_impact", "stop_source", "restart_plan", "restoration", "outside"}


@dataclass
class Data:
    contexts: list[dict[str, str]] = field(default_factory=list)
    faces: list[dict[str, str]] = field(default_factory=list)
    labor: list[dict[str, str]] = field(default_factory=list)
    plant: list[dict[str, str]] = field(default_factory=list)
    materials: list[dict[str, str]] = field(default_factory=list)
    actions: list[dict[str, str]] = field(default_factory=list)
    stops: list[dict[str, str]] = field(default_factory=list)
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
    if key in {"face", "face_id", "model", "issuer", "receiver", "owner", "labor", "machine", "material", "quantity", "people", "machine_count", "material_quantity", "date", "start", "finish", "deadline", "target"} and (_UNSAFE.search(value) or _UNKNOWN.search(value)):
        return MISSING
    if key in {"quantity", "material_quantity", "plan_quantity"} and not re.fullmatch(r"(?:约\s*)?"+_NUM+r"(?:\s*"+_UNIT+r")?", value, re.I):
        if key != "plan_quantity" or not re.fullmatch(r"(?:预计|计划)\s*"+_NUM+r"(?:\s*"+_UNIT+r")?", value, re.I):
            return MISSING
    if key == "people" and not re.fullmatch(r"(?:约\s*)?"+_NUM+r"(?:\s*(?:个人|人|个|名))?", value):
        return MISSING
    if key == "machine_count" and not re.fullmatch(_NUM+r"(?:\s*(?:台|辆|套))?", value):
        return MISSING
    if key == "date" and not re.fullmatch(_DATE, value):
        return MISSING
    if key in {"start", "finish", "order_time"} and not re.fullmatch(r"(?:"+_DATE+r")?(?:"+_CLOCK+r")?|收工|上午|下午", value):
        return MISSING
    if key in {"deadline", "target"} and not re.fullmatch(_DUE, value):
        return MISSING
    return value


def _directive(value):
    pairs = [("instruction", value)]
    match = re.match(r"^(.*?)(?:口头要求|要求|发话|交办|指示|通知)(.+)$", value)
    if match:
        pairs.append(("issuer", match.group(1)))
        due = re.search(_DUE, match.group(2))
        if due:
            receiver = match.group(2)[:due.start()].strip()
            if receiver:
                pairs.append(("receiver", receiver))
            pairs.append(("deadline", due.group()))
    return pairs


def _details(key, value):
    if key == "instruction":
        return _directive(value)
    if key == "weather" and _FUTURE.search(value):
        return [("forecast_weather", value)]
    if key == "restoration" and (_FUTURE.search(value) or _NOT_ACTUAL.search(value)):
        return [("restart_plan", value)]
    if key == "action_record" and (_FUTURE.search(value) or _NOT_ACTUAL.search(value)):
        return [("action_plan", value)]
    if key in {"actual", "quantity"} and _FUTURE.search(value):
        pairs = [("plan", value)]
        quantity = _QTY.search(value)
        if quantity:
            pairs.append(("plan_quantity", quantity.group()))
        return pairs
    pairs = [(key, value)]
    if key == "people" and _typed(key, value) == MISSING and _value(value) != MISSING:
        pairs.append(("labor_record", value))
    if key == "actual" and not (_NOT_ACTUAL.search(value) or _UNSAFE.search(value)):
        quantity = _QTY.search(value)
        if quantity:
            pairs.append(("quantity", quantity.group()))
    if key == "machine" and _typed(key, value) != MISSING:
        model = list(_MODEL.finditer(value))
        if len(model) == 1:
            pairs = [(key, value[:model[0].start()]+value[model[0].end():]), ("model", model[0].group())]
    return pairs


def _scan(text):
    text = re.sub(r"^.{0,24}?(?:出个|出|整理成|写一份|生成|起草)(?:一份)?调度日报(?:草稿)?\s*[:：]?\s*", "", text)
    label = _LABEL.match(text)
    if label:
        return _details(ALIASES[label.group(1).casefold()], label.group(2))
    if _UNSAFE.search(text):
        return []
    pairs = []
    future = bool(_FUTURE.search(text))
    if not future and (text.startswith(("今天", "今日")) or re.match(r"^"+_DATE, text)):
        date = re.search(_DATE, text)
        concrete = re.search(r"\d{1,2}月\d{1,2}[日号]", text)
        pairs.append(("date", concrete.group() if concrete else date.group()))
    if not future:
        shift = re.search(r"白班|夜班|早班|晚班", text)
        if shift:
            pairs.append(("shift", shift.group()))
    weather = re.search(r"(?:天气\s*[:：]?|上午|下午|晚上|今天|今日|明天)(?:下了?)?\s*("+_WEATHER+r")(?!水|管|棚|衣|靴)", text)
    if not weather:
        weather = re.fullmatch(r"("+_WEATHER+r")", text)
    if weather:
        pairs.append(("forecast_weather" if future else "weather", text if future else weather.group(1)))
    face = _FACE.search(text)
    if face:
        pairs.append(("face", face.group()))
        remaining = text[face.end():].strip()
        if remaining:
            pairs.append(("process", remaining))
    if _ACTUAL_CUE.search(text):
        pairs += _details("actual", text)
    start = re.search(r"("+_CLOCK+r")\s*(?:开泵|开工|开始)", text)
    if start and not future:
        pairs.append(("start", start.group(1)))
    trade = _TRADE.search(text)
    people = re.search(r"(?:上了|到了|来了|实到|到岗|只来了)?\s*("+_NUM+r"\s*(?:个人|人|个|名))", text)
    if trade and people:
        pairs += [("labor", trade.group()), ("labor_record", text)]
        if not (_NOT_ACTUAL.search(text) or _LABOR_NONACTUAL.search(text)):
            pairs.append(("people", people.group(1)))
    machine = _MACHINE.search(text)
    if machine:
        pairs.append(("machine", machine.group()))
        model = _MODEL.search(text[machine.end():])
        if model and not _UNKNOWN.search(text):
            pairs.append(("model", model.group()))
        count = re.search(r"("+_NUM+r"\s*(?:台|辆|套))(?!班)", text[machine.end():])
        if count:
            pairs.append(("machine_count", count.group(1)))
        if re.search(r"趴窝|故障|坏了|停机|吊不了|运行|开机|维修|修复", text):
            pairs.append(("machine_record", text))
    material = _MATERIAL.search(text)
    if material and re.search(r"待料|没到|未到|还差|到货|进场|运来|入库", text):
        pairs += [("material", material.group()), ("material_record", text)]
        quantity = _QTY.search(text[material.end():])
        if quantity and not re.search(r"待料|没到|未到|还差", text):
            pairs.append(("material_quantity", quantity.group()))
    if re.match(r"^.*?(?:口头要求|要求|发话|交办|指示|通知)", text) and not re.match(r"^(?:请|帮我|麻烦)", text):
        pairs += _directive(text)
    if re.search(r"停工|停电|跳闸", text) and not text.startswith("原因"):
        pairs.append(("stop", text))
    elif "中断" in text:
        pairs.append(("stop_impact", text))
    reason = re.match(r"^原因\s*(.+)$", text)
    if reason:
        pairs.append(("stop_reason", reason.group(1)))
    if re.search(r"还没收面|剩余待料|谁盯着.*没定|待决策", text):
        pairs.append(("record", text))
    if future and not any(k in _ACTIONS | _LABOR | _PLANT | _MATERIALS for k, _ in pairs):
        pairs.append(("next", text))
    sensitive = _SENSITIVE.search(text)
    if sensitive:
        pairs.append(("sensitive", sensitive.group()))
    return pairs


def parse(raw):
    data = Data()
    scope = {"jurisdiction": "UNSPECIFIED"}
    data.contexts.append(scope)
    current, origin, future, children = None, "", False, {}

    def ref():
        return next((i for i, row in enumerate(data.faces) if row is current), None)

    def new_face():
        row = {"_origin": origin, "_scope": len(data.contexts)-1,
               **{k: scope.get(k, "UNSPECIFIED" if k == "jurisdiction" else MISSING) for k in _META}}
        data.faces.append(row)
        return row

    def put(target, key, value):
        value = _typed(key, value)
        if target.get(key) in {None, MISSING, "UNSPECIFIED"}:
            target[key] = value
        elif value != MISSING and value not in target[key].split("；"):
            target[key] += "；"+value
        if value not in {MISSING, "UNSPECIFIED"}:
            data.evidence.append({"_record": ref(), "field": _FIELDS[key][0], "value": value, "input_position": position, "_origin": origin})

    def child(kind, fresh=False):
        if fresh or kind not in children:
            row = {"_record": ref(), "_planned": future, "input_position": position}
            getattr(data, kind).append(row)
            children[kind] = row
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
            current, future, children = None, False, {}
            continue
        pairs = ([(key, val) for label, value in content if label.casefold() in ALIASES for key, val in _details(ALIASES[label.casefold()], value)]
                 if is_table else _scan(source))
        if not pairs:
            continue
        stated = dict(pairs)
        enclosing_future = future
        if is_table and any(key == "actual" for key, _ in pairs):
            # A column explicitly reporting actual state owns this row; an
            # earlier next-day narrative is not authority to relabel it.
            future = False
        label = _LABEL.match(source) if not is_table else None
        explicit_actual = label is not None and ALIASES[label.group(1).casefold()] == "actual"
        if not is_table and (_CURRENT.match(source) or explicit_actual) and not _FUTURE.search(source):
            future, children = False, {}
        if not is_table and (any(k in {"plan", "next"} for k, _ in pairs) or _FUTURE.search(source) and any(k not in _META | {"restart_plan"} for k, _ in pairs)) and not any(k in _ACTIONS for k, _ in pairs):
            future, children = True, {}
        def old_value(key):
            value = current.get(key) if current is not None else None
            return scope.get(key) if value in {None, MISSING, "UNSPECIFIED"} else value
        if not is_table and any(k in stated and old_value(k) not in {None, MISSING, "UNSPECIFIED", _typed(k, stated[k])} for k in {"project", "jurisdiction", "date", "shift", "version"}):
            scope = {"_origin": origin, "jurisdiction": "UNSPECIFIED"}
            data.contexts.append(scope)
            current, children = None, {}
        if is_table or "face" in stated and (current is None or current.get("face") not in {None, MISSING}):
            current, children = new_face(), {}
        elif "face_id" in stated and _typed("face_id", stated["face_id"]) != MISSING:
            identifier = _typed("face_id", stated["face_id"])
            found = [r for r in data.faces if r["_scope"] == len(data.contexts)-1 and r.get("face_id") == identifier]
            if current is None or current.get("face_id") not in {None, MISSING, identifier} or len(found) > 1:
                current, children = (found[0] if len(found) == 1 else new_face()), {}
        for kind, keys, identity in (("labor", _LABOR, "labor"), ("plant", _PLANT, "machine"), ("materials", _MATERIALS, "material"), ("actions", _ACTIONS, "instruction"), ("stops", _STOPS, "stop")):
            if any(k in stated for k in keys):
                if current is None:
                    current = new_face()
                previous = children.get(kind, {})
                count_key = {"labor": "people", "plant": "machine_count", "materials": "material_quantity"}.get(kind)
                model_change = kind == "plant" and "model" in stated and previous.get("model") not in {None, MISSING, _typed("model", stated["model"])}
                repeated_count = count_key in stated and previous.get(count_key) not in {None, MISSING} if count_key else False
                declared_identity = identity in stated and previous.get(identity) not in {None, MISSING}
                new_record = child(kind, fresh=declared_identity or model_change or repeated_count)
                if identity not in stated and (model_change or repeated_count):
                    for retained in ({identity} if model_change else {identity, "model"} if kind == "plant" else {identity}):
                        if retained in previous:
                            new_record[retained] = previous[retained]
        for key, value in pairs:
            if key in _META and current is None:
                put(scope, key, value)
                continue
            if current is None:
                current = new_face()
            target = current
            for kind, keys in (("labor", _LABOR), ("plant", _PLANT), ("materials", _MATERIALS), ("actions", _ACTIONS), ("stops", _STOPS)):
                if key in keys:
                    target = child(kind)
                    break
            if future and key in {"actual", "quantity", "start", "finish"}:
                key = {"actual": "plan", "quantity": "plan_quantity", "start": "record", "finish": "record"}[key]
            put(target, key, value)
        if is_table:
            future = enclosing_future
    data.contexts = [r for r in data.contexts if len(r) > 1] or data.contexts[:1]
    return data


def _owned(data, records):
    return [{**(data.faces[r["_record"]] if isinstance(r.get("_record"), int) else {}), **r,
             "_nature": "用户计划（非实到 / 非已执行）" if r.get("_planned") else "用户现场记录（未核）"} for r in records]


def build_draft(post, tool_name, text):
    if post != "dispatch" or tool_name != TOOL:
        return None
    data = parse(text)
    owner = (("作业面 / 工作面", "face"), ("工作面编号", "face_id"), ("工序 / 作业内容", "process"),
             ("调度日期", "date"), ("班次", "shift"), ("项目名称", "project"), ("辖区", "jurisdiction"), ("输入资料名", "_origin"))
    return "\n".join([
        "# 调度日报草稿（AI 草稿 · 内部讨论）", "", DISCLAIMER, "", "- submit_blocked=true", "",
        "## 1 报头", "", _table(data.contexts, (("项目名称", "project"), ("调度日期", "date"), ("班次", "shift"), ("用户天气记录", "weather"), ("用户天气预报（非实况）", "forecast_weather"), ("辖区", "jurisdiction"), ("日报版本", "version"), ("输入资料名", "_origin"))), "",
        "填报 / 复核 / 签认：（空）。相对日期、时段只保留原文，不换算日历。", "",
        "## 2 草稿声明", "", "未下达任何指令，未核验完成或复工条件。只整理用户观察与待办，不代表现场已经执行或获得许可。", "",
        "## 3 计划接口", "", _table(data.faces, owner+(("用户计划节点 / 要求", "plan"), ("计划来源", "plan_source"), ("用户节点日期", "target"), ("用户计划数量（非实绩）", "plan_quantity")), extra=(("本轮计划回写 / 偏差计算", "未执行"),)), "",
        "没有计划原件则保留待核，不编节点日期、工期保证、产值或完成率。", "",
        "## 4 当日实际", "", _table(data.faces, owner+(("用户施工状态 / 当日实绩记录（未核）", "actual"), ("用户当日实际数量原文（未核）", "quantity"), ("用户开始时间", "start"), ("用户结束时间", "finish"), ("实绩来源", "actual_source")), extra=(("本轮完成核验", "未执行 / 待核"),)), "",
        "数量、约数及未完事项照原文登记，不由浇筑量推断整面完成，不把计划、目标或据说的实绩当完成事实。", "",
        "## 5 人机料动态", "",
        "### 用户人员记录", "", _table(_owned(data, data.labor), owner+(("用户工种 / 班组", "labor"), ("用户人数原文", "people"), ("用户人员动态原文", "labor_record"), ("出勤来源", "labor_source"), ("用户记录性质（非本轮核定）", "_nature"))), "",
        "### 用户机械记录", "", _table(_owned(data, data.plant), owner+(("用户机械名称", "machine"), ("用户机械型号", "model"), ("用户机械数量", "machine_count"), ("用户机械开停 / 故障记录", "machine_record"), ("机械来源", "machine_source"), ("用户记录性质（非本轮核定）", "_nature"))), "",
        "### 用户材料记录", "", _table(_owned(data, data.materials), owner+(("用户材料名称", "material"), ("用户材料到货 / 待料记录", "material_record"), ("用户材料数量", "material_quantity"), ("材料来源", "material_source"), ("用户记录性质（非本轮核定）", "_nature"))), "",
        "人、机、料分对象登记；型号数字不是台数，未到货不是已到货。不计算工日、台班或跨作业面汇总，不据故障记录推断恢复使用。", "",
        "## 6 指令栏", "", _table(_owned(data, data.actions), owner+(("用户指令内容 / 待办原文（未核）", "instruction"), ("用户下达人 / 指令来源", "issuer"), ("用户接收人 / 接收方", "receiver"), ("用户责任人", "owner"),
            ("用户指令时间", "order_time"), ("用户要求期限", "deadline"), ("指令凭证来源", "order_source"), ("用户前置条件", "precondition"), ("用户落实记录（未核）", "action_record"), ("用户计划落实（非已落实）", "action_plan")), extra=(("本轮批准 / 下达状态", "草稿 · 待责任人核准 · 未下达"), ("签认", "（空）"))), "",
        "转述口头要求不等于系统发令；指令来源、接收人、责任方与期限不互相代填。要求恢复不等于已经恢复。", "",
        "## 7 交叉作业与工作面交接", "", _table(data.faces, owner+(("用户交叉作业 / 交接原文（未核）", "handover"),), extra=(("本轮交接核验", "未执行"),)), "",
        "不补上下游交接、防护完成或验收状态。", "",
        "## 8 停复工与异常", "", _table(_owned(data, data.stops), owner+(("用户停工 / 中断记录", "stop"), ("用户停工原因", "stop_reason"), ("用户影响节点", "stop_impact"), ("停工来源", "stop_source"), ("用户外部制约", "outside"), ("用户拟复工 / 恢复条件", "restart_plan"), ("用户实际恢复记录（未核）", "restoration"), ("用户记录性质（非本轮核定）", "_nature")), extra=(("本轮复工 / 恢复核验", "未执行 · 未签发许可"),)), "",
        "停工事项、拟恢复条件与实际恢复记录分开，故障维修和整改不自动构成复工许可。", "",
        "## 9 危大/高处/临边等敏感作业清单", "", _table(data.faces, owner+(("用户敏感作业名称原文", "sensitive"), ("用户敏感作业时段", "sensitive_window")), extra=(("本轮判定 / 许可", "未执行"),)), "",
        "只列用户点名的名称与时段；判定、专项方案与相关许可交 method-hazard，本岗不判危大、不签发。", "",
        "## 10 明日条件与待决策", "", _table(data.faces, owner+(("用户下步 / 明日安排", "next"), ("用户待核记录 / 未完事项", "record"), ("未分派资料来源", "source")), extra=(("本轮现场执行 / 计划修改", "未执行"),)), "",
        "缺责任方、采购、物机或测量资料时保留待核，不补口头指令授权。", "",
        "## 11 附件表头", "", _table(data.faces, owner+(("用户附件目录", "attachments"), ("用户旁站资料标识", "witness"), ("用户交底资料标识", "briefing"), ("用户验收资料标识", "acceptance")), extra=(("本轮资料 / 签认核验", "未执行"),)), "",
        _table(_owned(data, data.evidence), owner+(("记录项", "field"), ("用户证据原文", "value"), ("输入位置（非原件页码）", "input_position"))), "",
        "不默写国家、法规版本、参数或图号；现行依据及来源原件未核则标 UNSPECIFIED。不作完成、合格或获准作业结论。", "",
    ])
