"""Bounded literal administrative observations, without inferred approvals.

Separate files, meetings and vehicle requests stay separate records.  Dates,
headcounts, plates and roles are copied from source; no normalization or totals.
Unsupported or hypothetical wording remains unknown rather than being guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

_BREAK = r"[，；;。\n]|(?<!\d),|,(?!\d)"
_INTEGER = r"(?:\d{1,3}(?:,\d{3})+|\d+)"
_SCENARIO = re.compile(r"比如|例如|示例|样例|假设|如果|写成")
_NONLITERAL = re.compile(r"比如|例如|示例|样例|假设|如果|写成|不要|别把|不是|不由|不用|不能|不得")
_UNKNOWN = re.compile(r"还没|没定|未定|未确定|未知|待定|待确认|待核|不清楚|谁|等通知|等他通知")
_PERSON = r"[一-鿿·]{2,24}"
_DATE = r"(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|(?:\d{4}年)?\d{1,2}月\d{1,2}日|本周[一二三四五六日天]|周[一二三四五六日天]|明天|后天)"
_MEETING = re.compile(r"(?:召开|举行|开的|开)\s*([^，,；;。\n]{1,60}(?:会议|会))(?=$|[，,；;。\n])")
_DECISION = re.compile(r"(?:会上定了|会上决定|会议决定|上会决定|同时决定|已议定)\s*")


@dataclass
class NarrativeFacts:
    values: dict[str, str] = field(default_factory=dict)
    meetings: list[dict[str, str]] = field(default_factory=list)
    meeting_sources: list[str] = field(default_factory=list)
    decisions: list[dict[str, str]] = field(default_factory=list)
    seals: list[dict[str, str]] = field(default_factory=list)
    vehicles: list[dict[str, str]] = field(default_factory=list)


def _clauses(text: str) -> list[str]:
    return [value.strip() for value in re.split(_BREAK, text) if value.strip()]


def literal_source(text: str) -> str:
    """Mask hypothetical scopes, preserving literal field delimiters and values."""
    scenario = False
    pieces = []
    cursor = 0
    for separator in re.finditer(_BREAK + r"|$", text):
        clause = text[cursor:separator.start()]
        reset = re.match(r"\s*(?:本次实际情况|实际情况|真实记录)(?:是\s*[:：]?|[:：])\s*", clause)
        if reset:
            scenario = False
            clause = clause[reset.end():]
        if _SCENARIO.search(clause):
            scenario = True
        pieces.append("" if scenario else clause)
        pieces.append(separator.group())
        cursor = separator.end()
    return "".join(pieces)


def _person(value: str) -> str:
    value = value.strip()
    return value if re.fullmatch(_PERSON, value) and not (_UNKNOWN.search(value) or _NONLITERAL.search(value)) else ""


def _put(record: dict[str, str], key: str, value: str) -> None:
    value = value.strip()
    if value:
        record.setdefault(key, value)


def _meeting_fields(text: str) -> dict[str, str]:
    record: dict[str, str] = {}
    for clause in _clauses(text):
        if _NONLITERAL.search(clause):
            continue
        opened = _MEETING.search(clause)
        if opened:
            _put(record, "meeting", opened.group(1))
            before = clause[:opened.start()]
            venue = re.search(r"在\s*([^，,；;。\n]+)$", before)
            if venue:
                _put(record, "venue", venue.group(1))
                before = before[:venue.start()]
            timing = re.search(r"(?:定于|计划于|将于)\s*(.+)$", before)
            if not timing:
                timing = re.search(r"(" + _DATE + r"[^，,；;。\n]*)$", before)
            if timing:
                _put(record, "time", timing.group(1))
        for key, suffix in (("chair", "主持"), ("recorder", "(?:记录|担任记录员)"), ("observers", "列席")):
            matched = re.fullmatch(r"(?:由\s*)?(" + _PERSON + r")\s*" + suffix, clause)
            if matched:
                _put(record, key, _person(matched.group(1)))
        recorder = re.fullmatch(r"记录[人员](?:是|为|[ \t]+)\s*(" + _PERSON + r")", clause)
        if recorder:
            _put(record, "recorder", _person(recorder.group(1)))
        attendees = re.fullmatch(r"(?:参会人员为|与会人员为|参会的有)\s*(.+)", clause)
        if attendees:
            _put(record, "attendees", attendees.group(1))
            count = re.search(r"(?<![\d.,])(?:大概|约)?" + _INTEGER + r"\s*(?:个人|人)", attendees.group(1))
            if count:
                _put(record, "count", count.group())
        equipment = re.fullmatch(r"(?:会场)?(?:要|需|需要)(?:备好|准备|配备|配置)\s*(.+)", clause)
        if equipment and not _UNKNOWN.search(equipment.group(1)):
            _put(record, "equipment", equipment.group(1))
        hospitality = re.fullmatch(r"(.{1,30}(?:茶水|席卡|桌牌|签到表).{0,10})也一起准备", clause)
        if hospitality:
            _put(record, "hospitality", hospitality.group(1))
    return record


def _meeting_records(text: str) -> tuple[list[dict[str, str]], list[str]]:
    starts: list[int] = []
    cursor = 0
    for separator in re.finditer(_BREAK + r"|$", text):
        clause = text[cursor:separator.start()]
        if _MEETING.search(clause) and not _NONLITERAL.search(clause):
            starts.append(cursor)
        cursor = separator.end()
    records, sources = [], []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        record = _meeting_fields(text[start:end])
        if "meeting" in record:
            records.append(record)
            sources.append(text[start:end])
    return records, sources


def _decisions(text: str) -> tuple[list[dict[str, str]], str]:
    records, pending = [], []
    for clause in _clauses(text):
        if _NONLITERAL.search(clause):
            continue
        if re.match(r"^(?:已议定事项|会议决议|决议|已决定事项|决定)\s*[:：=]", clause):
            continue
        decided = _DECISION.search(clause)
        if decided:
            body = clause[decided.end():].strip()
            if not body:
                continue
            if re.search(r"建议|讨论|是否|拟|尚未|待批准|待审批", body) or _UNKNOWN.search(body):
                pending.append(body)
                continue
            record = {"decisions": body}
            owned = re.fullmatch(r"由\s*(" + _PERSON + r"?)(?:在(" + _DATE + r"前))?\s*完成\s*(.+)", body)
            if owned and _person(owned.group(1)):
                record = {"decisions": owned.group(3), "owner": owned.group(1)}
                if owned.group(2):
                    record["deadline"] = owned.group(2)
            records.append(record)
        elif re.search(r"没定(?:下来)?|未确定|待定", clause):
            pending.append(clause)
    return records, "；".join(pending)


def _blocks(text: str, marker: str) -> list[str]:
    matches = list(re.finditer(marker, text, re.MULTILINE))
    if not matches:
        return [text]
    return [text[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(text)]
            for index, match in enumerate(matches)]


def _seal_records(text: str) -> list[dict[str, str]]:
    if not re.search(r"用印|盖章|印章", text):
        return []
    records = []
    marker = r"(?:另一个|一个|第[一二三四五六七八九十\d]+个)(?:是|[:：])\s*|(?=^(?:文件名称|文件名)\s*[:：=])"
    aliases = {"文件名称": "title", "文件名": "title", "份数": "copies", "用印份数": "copies",
               "印章": "seal", "印章种类": "seal", "印章类型": "seal", "拟审批人": "approver", "审批人": "approver"}
    for block in _blocks(text, marker):
        record: dict[str, str] = {}
        for clause in _clauses(block):
            if _NONLITERAL.search(clause):
                continue
            labelled = re.fullmatch(r"(" + "|".join(aliases) + r")\s*[:：=]\s*(.+)", clause)
            if labelled:
                key, value = aliases[labelled.group(1)], labelled.group(2)
                if key == "approver":
                    value = _person(value)
                elif key == "copies" and not re.fullmatch(_INTEGER + r"\s*份|[一二三四五六七八九十百]+\s*份", value):
                    value = ""
                elif _UNKNOWN.search(value):
                    value = ""
                _put(record, key, value)
                continue
            title = re.fullmatch(r"([^：:=]{1,50}(?:合同|协议|交接单|说明|函|证明|清单|报告))", clause)
            if title and not re.search(r"帮|写|起草|生成|审批|还没|待定|未知", title.group(1)):
                _put(record, "title", title.group(1))
            copies = re.fullmatch(r"(?:共|一共)?\s*((?:" + _INTEGER + r"|[一二三四五六七八九十百]+)\s*份)", clause)
            if copies:
                _put(record, "copies", copies.group(1))
            seal = re.fullmatch(r"(?:盖|使用)(?:的是|用)?\s*([^，,；;。\n]{1,20}章)", clause)
            if seal and not _UNKNOWN.search(seal.group(1)):
                _put(record, "seal", seal.group(1))
            approver = re.fullmatch(r"审批人(?:写|是|为|[:：=]|[ \t]+)\s*(" + _PERSON + r")", clause)
            if approver:
                _put(record, "approver", _person(approver.group(1)))
        if "title" in record:
            records.append(record)
    return records


def _vehicle_records(text: str) -> list[dict[str, str]]:
    if not re.search(r"用车|派车|商务车|司机|车牌", text):
        return []
    records = []
    for block in _blocks(text, r"(?:第[一二三四五六七八九十\d]+拨|另一拨)\s*(?:是|[:：])?\s*"):
        record: dict[str, str] = {}
        for clause in _clauses(block):
            if _NONLITERAL.search(clause):
                continue
            plate = re.fullmatch(r"(?:商务车|车牌(?:号)?|拟用车)\s*[:：=]?\s*([一-鿿][A-Z][A-Z0-9]{5,6})", clause)
            if plate:
                _put(record, "plate", plate.group(1))
            driver = re.fullmatch(r"司机(?:是|为|[:：=]|[ \t]+)?\s*(" + _PERSON + r")", clause)
            if driver:
                _put(record, "driver", _person(driver.group(1)))
            route = re.fullmatch(r"(?:(早上|上午|下午|晚上)?\s*(\d{1,2}:\d{2}))?从(.+?)送(" + _PERSON + r"?)(?:他们|一行)?(" + _INTEGER + r"\s*(?:个人|人))去(.+)", clause)
            if route:
                if route.group(2):
                    _put(record, "departure_time", (route.group(1) or "") + route.group(2))
                _put(record, "origin", route.group(3))
                _put(record, "passengers", route.group(4).removesuffix("他们").removesuffix("一行"))
                _put(record, "count", route.group(5))
                _put(record, "destination", route.group(6))
            else:
                destination = re.fullmatch(r"(?:上午|早上|下午|晚上)?去(.+?)(?:看构件|办事|开会|参观)?", clause)
                if destination:
                    _put(record, "destination", destination.group(1))
            leader = re.fullmatch(r"(" + _PERSON + r")带队(" + _INTEGER + r"\s*(?:个人|人))", clause)
            if leader:
                _put(record, "leader", _person(leader.group(1)))
                _put(record, "count", leader.group(2))
            mileage = re.fullmatch(r"(?:来回|全程|路程)?((?:大概|约)?" + _INTEGER + r"(?:\.\d+)?\s*公里)", clause)
            if mileage:
                _put(record, "mileage", mileage.group(1))
        if record:
            records.append(record)
    return records


def parse_narrative(text: str) -> NarrativeFacts:
    """Parse supplied non-table prose; the caller's explicit fields take priority."""
    text = literal_source(text)
    result = NarrativeFacts()
    result.meetings, result.meeting_sources = _meeting_records(text)
    # Never use the first meeting's people or equipment as global defaults for others.
    if len(result.meetings) == 1:
        result.values.update(result.meetings[0])
    elif not result.meetings:
        result.values.update(_meeting_fields(text))
    result.decisions, pending = _decisions(text)
    if pending:
        result.values["pending"] = pending
    for clause in _clauses(text):
        if _NONLITERAL.search(clause):
            continue
        for key, label in (("approver", "审批人"), ("leader", "带队(?:人)?")):
            stated = re.fullmatch(label + r"[ \t]+(" + _PERSON + r")", clause)
            if stated:
                _put(result.values, key, _person(stated.group(1)))
        venue = re.fullmatch(r"场所[ \t]+(.+)", clause)
        if venue:
            _put(result.values, "venue", venue.group(1))
        count = re.fullmatch(r"一行(" + _INTEGER + r"\s*(?:个人|人))", clause)
        if count:
            _put(result.values, "count", count.group(1))
    # A named shared applicant is accepted only after an explicit shared-role statement.
    applicant = re.search(r"经办(?:人)?都是我[，,\n]\s*((?:劳资员|资料员|经办人)" + _PERSON + r")(?=$|[，,；;。\n])", text)
    if applicant and not _NONLITERAL.search(text[:applicant.end()]):
        _put(result.values, "applicant", _person(applicant.group(1)))
    result.seals = _seal_records(text)
    result.vehicles = _vehicle_records(text)
    return result
