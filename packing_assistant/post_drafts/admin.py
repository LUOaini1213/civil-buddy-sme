"""Fact-based internal administration drafts; no approval or seal execution."""

from __future__ import annotations

import html
import re
from collections.abc import Sequence

from packing_assistant.jurisdiction import infer_jurisdiction
from packing_assistant.office_job import tables_from_md
from packing_assistant.post_drafts.admin_narrative import literal_source, parse_narrative

_MISSING = "UNSPECIFIED（待补）"
_TOOLS = {"admin-doc": {"admin-doc__draft"}, "admin-office": {"admin-office__list"}}
_FIELDS = {
    "kind": ("文种", "公文类型"),
    "title": ("文件名称", "文件名", "公文标题", "标题"),
    "organization": ("发文机关", "发文单位"),
    "number": ("发文字号", "文号"),
    "recipient": ("主送机关", "主送"),
    "copy": ("抄送机关", "抄送"),
    "subject": ("请示事项", "申请事项", "事由", "主题"),
    "facts": ("事实情况", "事实", "背景", "现状"),
    "basis": ("制度依据", "依据"),
    "request": ("请求事项", "请求", "拟请批准", "申请内容"),
    "attachments": ("附件目录", "附件名称", "附件"),
    "date": ("成文日期", "落款日期"),
    "meeting_date": ("会议日期", "日期"),
    "seal_needed": ("是否用印", "是否需要用印"),
    "copies": ("用印份数", "份数"),
    "seal": ("印章种类", "印章类型", "印章"),
    "approver": ("拟审批人", "审批人"),
    "applicant": ("申请部门及申请人", "申请人", "经办人"),
    "seal_date": ("申请日期", "拟用印日期"),
    "seal_recipient": ("送达对象", "收件单位", "接收方"),
    "seal_reason": ("用印事由", "用印用途"),
    "cross_seal": ("是否骑缝", "骑缝要求"),
    "meeting": ("会议名称", "会议主题", "活动名称"),
    "purpose": ("会议目的", "活动目的", "目的"),
    "time": ("会议时间", "活动时间", "开会时间", "时间"),
    "venue": ("会议地点", "活动地点", "会场", "场地", "地点", "场所"),
    "chair": ("主持人", "主持"),
    "attendees": ("与会人员", "参会人员", "参加人员", "参会单位", "与会", "参会"),
    "observers": ("列席人员", "列席"),
    "absent": ("缺席人员", "缺席"),
    "recorder": ("记录人", "记录员"),
    "agenda": ("会议议程", "议程", "议题"),
    "discussion": ("讨论摘要", "讨论内容", "讨论"),
    "decisions": ("已议定事项", "会议决议", "决议", "已决定事项", "决定"),
    "pending": ("待定事项", "未决事项", "待定", "待讨论"),
    "owner": ("责任人", "负责人"),
    "deadline": ("完成期限", "截止时间", "截止日期", "期限"),
    "materials": ("会前资料", "资料目录", "会议资料", "所需资料", "资料"),
    "equipment": ("设备需求", "会场设备", "设备"),
    "hospitality": ("签到及茶水需求", "茶水席卡"),
    "contact": ("会务联系人", "联系人"),
    "handoff": ("纪要交接时间", "记录交接时间"),
    "guests": ("接待对象", "来访单位", "来宾"),
    "count": ("来访人数", "人数"),
    "arrival": ("到达时间", "抵达时间", "到达"),
    "departure": ("离开时间", "返程时间", "离开"),
    "leader": ("带队人", "带队人员", "带队"),
    "accompany": ("陪同人员", "陪同"),
    "lodging": ("住宿安排", "住宿"),
    "meals": ("用餐安排", "用餐"),
    "trip_purpose": ("出差事由", "出差目的"),
    "travelers": ("出差人员", "出行人员"),
    "days": ("出差天数", "天数"),
    "route": ("出行路线", "路线", "行程"),
    "transport": ("交通方式", "用车需求", "交通"),
    "plate": ("拟用车", "车牌号", "车牌"),
    "driver": ("拟司机", "司机"),
    "departure_time": ("出发时间",),
    "origin": ("出发地点",),
    "destination": ("目的地",),
    "passengers": ("乘车人员",),
    "mileage": ("预计里程", "里程"),
    "supplies": ("办公物资", "物资名称", "物品名称"),
    "department": ("申请部门", "需求部门"),
    "specification": ("规格型号", "规格"),
    "quantity": ("申请数量", "数量"),
    "inventory": ("现有库存", "库存"),
    "receiver": ("领用人", "领取人"),
    "usage": ("使用用途", "用途"),
}
_ALIASES = {alias: key for key, aliases in _FIELDS.items() for alias in aliases}
_LABELS = re.compile(
    r"(?:^|[\s，,；;。])(" + "|".join(sorted(map(re.escape, _ALIASES), key=len, reverse=True))
    + r")\s*[:：=]\s*", re.MULTILINE
)


def _clean(value: str) -> str:
    value = re.split(r"[；;。]\s*(?=(?:未提供|缺失|未知|其余)[^。\n]{0,60}(?:保持|待填|待补))", value, maxsplit=1)[0]
    value = value.strip(" \t\r\n，,；。")
    while value.endswith(";") and not re.search(r"&(?:#\d+|#x[\da-fA-F]+|[A-Za-z]+);$", value):
        value = value[:-1].rstrip(" \t，,；。")
    return value.lstrip(";")


class _Facts:
    """Explicit fields first; separate literal narrative objects never share defaults."""

    def __init__(self, text: str):
        self.text = text or ""
        self.values: dict[str, str] = {}
        self.source_records: list[dict[str, str]] = []
        self.table_records: list[dict[str, str]] = []
        prose: list[str] = []
        lines = literal_source(self.text).splitlines()
        index = 0
        while index < len(lines):
            line = lines[index]
            if line.lstrip().startswith("|"):
                end = index + 1
                while end < len(lines) and lines[end].lstrip().startswith("|"):
                    end += 1
                self._table_entries("\n".join(lines[index:end]))
                index = end
                continue
            index += 1
            matches = list(_LABELS.finditer(line))
            prose.append(line)
            record: dict[str, str] = {}
            for field_index, match in enumerate(matches):
                end = matches[field_index + 1].start() if field_index + 1 < len(matches) else len(line)
                key, value = _ALIASES[match.group(1)], _clean(line[match.end():end])
                if key == "decisions" and "decisions" in record:
                    self._add_record(record)
                    record = {}
                if value:
                    record[key] = "；".join(filter(None, (record.get(key), value)))
            self._add_record(record)
        narrative = parse_narrative("\n".join(prose))
        self.meeting_records = narrative.meetings
        self.meeting_sources = narrative.meeting_sources
        self.seal_records = narrative.seals
        self.vehicle_records = narrative.vehicles
        self.narrative_decisions = narrative.decisions
        for key, value in narrative.values.items():
            self.values.setdefault(key, value)
        if len(narrative.seals) == 1:
            for key, value in narrative.seals[0].items():
                self.values.setdefault(key, value)

    def get(self, key: str, default: str = _MISSING) -> str:
        return self.values.get(key, default)

    def _add_record(self, record: dict[str, str]) -> None:
        self.source_records.append(record)
        for key, value in record.items():
            self.values[key] = "；".join(filter(None, (self.values.get(key), value)))

    def _table_entries(self, markdown: str) -> None:
        for _, rows in tables_from_md(markdown):
            headers = [_ALIASES.get(cell) for cell in rows[0]]
            if any(headers):
                for row in rows[1:]:
                    if len(row) == len(headers):
                        record = {key: _clean(value) for key, value in zip(headers, row) if key and _clean(value)}
                        self.table_records.append(record)
                        self._add_record(record)
                        # A missing value in one table row cannot inherit from another row.
                        self.source_records.append({})
            else:
                for row in rows[1:]:
                    key = _ALIASES.get(row[0]) if row else None
                    if key and len(row) >= 2 and not any(row[2:]) and _clean(row[1]):
                        self._add_record({key: _clean(row[1])})

    def decision_records(self) -> list[dict[str, str]]:
        records: list[dict[str, str]] = []
        current: dict[str, str] = {}
        for record in self.source_records:
            if "decisions" in record or not record:
                if current:
                    records.append(current)
                current = {}
            if "decisions" in record:
                current = {key: value for key, value in record.items() if key in {"decisions", "owner", "deadline"}}
            elif current:
                for key in ("owner", "deadline"):
                    if key in record:
                        current[key] = "；".join(filter(None, (current.get(key), record[key])))
        if current:
            records.append(current)
        for record in records:
            # Keep a literal person/date binding local to this decision.
            stated = re.match(r"([一-鿿·]{2,8})在(\d{4}-\d{2}-\d{2}前)(?=\S)", record["decisions"])
            if stated:
                record.setdefault("owner", stated.group(1))
                record.setdefault("deadline", stated.group(2))
        return records + self.narrative_decisions

    def rows(self, keys: Sequence[str]) -> list[dict[str, str]]:
        records = [record for record in self.table_records if any(key in record for key in keys)]
        return records or [{key: self.values[key] for key in keys if key in self.values}]


def _cell(value: str) -> str:
    # Office's Markdown parser splits literal pipes, including escaped pipes.
    return html.escape(str(value), quote=False).replace("|", "&#124;").replace("\n", " ").replace("\r", " ")


def _table(title: str, headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = [f"## {title}", "", "| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows)
    return "\n".join(lines) + "\n\n"


def _fields(title: str, rows: Sequence[Sequence[str]]) -> str:
    return _table(title, ("栏位", "本稿内容"), rows)


def _header(title: str, text: str) -> str:
    return (
        f"# {title}（AI 草稿）\n\n"
        "> 草稿声明：仅供内部讨论及人工核对，不是签发公文、盖章原件或外部承诺；"
        "不代审批、不代签字、不代用印。\n\n"
        f"- 辖区：{infer_jurisdiction(text)}\n"
        "- 来源：已填事实来自本次输入，未核验原件；缺项保留 UNSPECIFIED，签认栏留空。\n"
        "- 制度：按公司制度办理，未提供的制度名称及审批节点待填。\n\n"
    )


def _seal(facts: _Facts) -> str:
    files = [record for record in facts.table_records if "title" in record] + facts.seal_records
    per_file = "见逐份用印文件核验（不合并份数或权限）" if len(files) > 1 else ""
    detail = _table("逐份用印文件核验", ("文件名称", "份数", "印章种类", "拟审批人", "审批签认"), [
        tuple(record.get(key, _MISSING) for key in ("title", "copies", "seal", "approver")) + ("",) for record in files
    ]) if files else ""
    return detail + _fields("用印申请与核验", [
        ("是否需要用印", facts.get("seal_needed")),
        ("文件名称", per_file or facts.get("title")), ("份数", per_file or facts.get("copies")),
        ("印章种类", per_file or facts.get("seal")), ("拟审批人（用户提供，权限待核）", per_file or facts.get("approver")),
        ("申请人", facts.get("applicant")), ("拟用印/申请日期", facts.get("seal_date")),
        ("用印事由", facts.get("seal_reason", facts.get("subject"))),
        ("送达对象", facts.get("seal_recipient")), ("骑缝要求", facts.get("cross_seal")),
        ("审批人签认", ""), ("印章保管人核验", ""), ("实际用印日期及登记编号", ""),
    ]) + (
        "核验清单：核对定稿文件、用印份数、印章权限与审批链；签认完成情况由责任人核验。"
        "禁止代用印、预盖空白文件，项目章及技术章不得在本稿中视为合同授权。\n\n"
    )


def _document_kind(facts: _Facts) -> str:
    explicit = facts.get("kind", "")
    if explicit:
        return {"请示": "请示", "纪要": "纪要", "会议纪要": "纪要", "用印": "用印", "用印申请": "用印", "用印审批": "用印"}.get(explicit, "文种待确认")
    # Prefer the requested document over later mentions of an attachment or seal.
    leading = _LABELS.split(facts.text, maxsplit=1)[0]
    match = re.search(r"请示报告|会议纪要|纪要|请示|用印", leading)
    if match and match.group() != "请示报告":
        return "纪要" if match.group() == "会议纪要" else match.group()
    return "文种待确认"


def _doc(facts: _Facts) -> str:
    kind = _document_kind(facts)
    if kind == "纪要" and len(facts.meeting_sources) > 1:
        drafts = []
        for source in facts.meeting_sources:
            meeting = _Facts(source)
            meeting.values["kind"] = "会议纪要"
            meeting.text = facts.text
            drafts.append(_doc(meeting))
        return "\n\n".join(drafts)
    result = _header(kind + " · 公文印章", facts.text)
    result += _fields("文件控制", [
        ("文种", kind), ("文件名称", facts.get("title", facts.get("subject"))),
        ("发文机关", facts.get("organization")), ("发文字号（仅录入原值，不生成序号）", facts.get("number")),
        ("主送", facts.get("recipient")), ("抄送", facts.get("copy")),
        ("成文日期（拟）", facts.get("date")), ("签发人签认", ""),
    ])
    if kind == "请示":
        result += _fields("请示正文", [
            ("请示事项（一文一事）", facts.get("subject")), ("事实与背景", facts.get("facts")),
            ("依据（待核适用性）", facts.get("basis")), ("请求事项", facts.get("request")),
            ("结语", "妥否，请批示。"), ("批示意见", ""),
        ])
        result += "主送机关须由经办人核对行文关系；多项事项须拆分，不使用“请示报告”混合文种。\n\n"
    elif kind == "纪要":
        result += _fields("会议记录要素", [
            ("会议名称", facts.get("meeting")), ("时间", facts.get("time", facts.get("meeting_date"))),
            ("地点", facts.get("venue")), ("主持人", facts.get("chair")),
            ("与会人员", facts.get("attendees")), ("列席人员", facts.get("observers")),
            ("缺席人员", facts.get("absent")), ("记录人", facts.get("recorder")),
            ("议程", facts.get("agenda")), ("讨论摘要", facts.get("discussion")),
        ])
        pending = facts.get("pending", "")
        decision_rows = []
        for record in facts.decision_records():
            decision = record["decisions"]
            if re.search(r"是否|讨论|未确定|未决定|尚未|建议|拟|待定|待批准|待审批|待确认|未同意|未通过", decision):
                pending = "；".join(filter(None, (pending, decision)))
            else:
                decision_rows.append((decision, record.get("owner", _MISSING), record.get("deadline", _MISSING), ""))
        result += _table("已议定事项（仅转录用户明确提供的决议，待核原记录）", ("议定事项", "责任人", "期限", "核对签认"),
                         decision_rows or [(_MISSING, _MISSING, _MISSING, "")])
        result += _table("待定事项", ("待定或需上报事项", "下次讨论安排", "决定"), [(pending or _MISSING, _MISSING, "")])
        result += "讨论摘要不自动转为决议；没有明确决议时，议定事项保持待补。\n\n"
    elif kind == "用印":
        result += _fields("用印事由与文件范围", [
            ("事由", facts.get("seal_reason", facts.get("subject"))),
            ("文件定稿状态", _MISSING), ("送达对象与用途", facts.get("seal_recipient")),
            ("制度依据", facts.get("basis")), ("用印审批意见", ""),
        ])
    else:
        result += _fields("文种与行文关系待确认", [
            ("用户提供文种", facts.get("kind")), ("用途/事项", facts.get("subject")),
            ("待确认", "请明确请示、会议纪要或用印申请；请示报告须拆分为明确文种。"),
            ("事实", facts.get("facts")), ("请求事项", facts.get("request")),
        ])
    result += _seal(facts)
    result += _fields("附件与归档交接", [
        ("附件目录（按用户顺序）", facts.get("attachments")),
        ("定稿版本及归档位置", _MISSING), ("经办人核对签认", ""),
    ])
    return result


def _office(facts: _Facts) -> str:
    meetings = [record for record in facts.table_records if "meeting" in record] or facts.meeting_records
    if len(meetings) > 1:
        drafts = []
        for record in meetings:
            meeting = _Facts("")
            # Preserve jurisdiction context, without parsing another event's values.
            meeting.text = facts.text
            meeting._add_record(record)
            meeting.table_records = [record]
            drafts.append(_office(meeting))
        return "\n\n".join(drafts)
    result = _header("会务/后勤清单", facts.text)
    result += _fields("任务信息", [
        ("会议/活动名称", facts.get("meeting")), ("目的", facts.get("purpose")),
        ("时间", facts.get("time", facts.get("meeting_date"))), ("会务联系人", facts.get("contact")),
        ("参会人数（用户提供，待核）", facts.get("count") if facts.get("meeting", "") else _MISSING),
    ])
    result += _table("场地", ("项目", "用户提供或待补", "核对负责人", "决定"), [
        *(("地点", row.get("venue", _MISSING), row.get("owner", _MISSING), "") for row in facts.rows(("venue", "owner"))),
        ("设备/投影/麦克风/视频", facts.get("equipment"), _MISSING, ""),
        ("签到、桌牌及茶水", facts.get("hospitality"), _MISSING, ""),
    ])
    result += _table("议程", ("时间", "议题/安排", "主持人", "决定"), [
        *((row.get("time", _MISSING), row.get("agenda", _MISSING), row.get("chair", _MISSING), "") for row in facts.rows(("time", "agenda", "chair"))),
    ])
    result += _table("与会", ("角色", "人员/单位", "出席核对", "决定"), [
        *(("参会", row.get("attendees", _MISSING), "", "") for row in facts.rows(("attendees",))),
        ("列席", facts.get("observers"), "", ""),
        ("记录", facts.get("recorder"), "", ""),
    ])
    result += _table("资料目录", ("资料名称（按用户顺序）", "提供人", "版本/到位时间", "决定"), [
        *((row.get("materials", row.get("attachments", _MISSING)), _MISSING, _MISSING, "") for row in facts.rows(("materials", "attachments"))),
        ("签到表、记录模板", facts.get("recorder"), _MISSING, ""),
    ])
    if any(key in facts.values for key in ("guests", "arrival", "lodging", "meals")) or "接待" in facts.text:
        result += _fields("接待安排（待确认）", [
            ("接待事由", facts.get("purpose", facts.get("subject"))),
            ("来宾/单位", facts.get("guests")), ("人数", facts.get("count")),
            ("带队人", facts.get("leader")), ("接待地点", facts.get("venue")),
            ("到达时间", facts.get("arrival")), ("离开时间", facts.get("departure")),
            ("陪同人员", facts.get("accompany")), ("住宿", facts.get("lodging")),
            ("用餐", facts.get("meals")), ("费用标准及审批", ""),
        ])
    if any(key in facts.values for key in ("trip_purpose", "travelers", "route")) or "出差" in facts.text:
        result += _fields("差旅及用车（待确认）", [
            ("出差事由", facts.get("trip_purpose")), ("人员", facts.get("travelers")),
            ("天数", facts.get("days")), ("路线", facts.get("route")),
            ("交通/用车需求", facts.get("transport")), ("派车、报销标准及审批", ""),
        ])
    vehicle_keys = ("plate", "driver", "departure_time", "origin", "destination", "passengers", "count", "leader", "mileage")
    vehicles = [record for record in facts.table_records if any(key in record for key in vehicle_keys if key not in {"count", "leader"})]
    vehicles += facts.vehicle_records
    if not vehicles and any(key in facts.values for key in vehicle_keys if key not in {"count", "leader"}):
        vehicles = [{key: facts.values[key] for key in vehicle_keys if key in facts.values}]
    if vehicles:
        result += _table("逐项用车需求（待核，不执行派车）", (
            "事项", "拟用车/车牌（待核）", "拟司机（待核）", "出发时间", "出发地点", "目的地",
            "乘车人员", "人数", "带队人", "用户提供里程（未计算）", "派车审批",
        ), [
            (f"用车事项{index}",) + tuple(record.get(key, _MISSING) for key in vehicle_keys) + ("",)
            for index, record in enumerate(vehicles, 1)
        ])
    if "supplies" in facts.values or "办公物资" in facts.text:
        result += _table("办公物资申领", ("部门", "名称", "规格", "数量", "库存", "领用人", "用途", "审批"), [
            tuple(facts.get(key) for key in ("department", "supplies", "specification", "quantity", "inventory", "receiver", "usage")) + ("",),
        ])
    result += _fields("会后与后勤收尾", [
        ("记录交接人", facts.get("recorder")), ("交接时间", facts.get("handoff")),
        ("纪要起草接口", "将原始记录、与会名单与附件交 admin-doc 起草；不将会务安排当作会议决议。"),
        ("设备归还、场地复位及费用凭据", _MISSING), ("完成核验及签认", ""),
    ])
    result += "决定栏由实际责任人填写。本稿不确认场地已预订、车辆已派出、接待已获批；不生成技术方案、费用标准或餐饮许可结论。\n"
    return result


def build_draft(expert_id: str, tool_name: str, text: str) -> str | None:
    """Build one supported post/tool draft without I/O or model access."""
    if tool_name not in _TOOLS.get(expert_id, set()):
        return None
    facts = _Facts(text)
    return _doc(facts) if expert_id == "admin-doc" else _office(facts)
