"""Literal design observations, scoped to named buildings/systems/components.

This is not a design calculator. Units, source IDs and capacities are copied,
never converted or inferred. Labelled/table input remains the primary path;
this opt-in parser handles bounded conversational observations only.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

MISSING = "[A001] / UNSPECIFIED"
_BREAK = re.compile(r"[，；;。\n]|(?<!\d),|,(?!\d)")
_NUMBER = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_CODE = r"(?:[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)+|[一-鿿]{1,8}-[A-Za-z0-9-]+)"
_UNIT = re.compile(r"\d+[#＃](?:[一-鿿]{0,10}?(?:办公楼|住宅楼|教学楼|实验楼|科研楼|商业楼|宿舍楼|厂房|宿舍|仓库|冷库|幼儿园|车间|食堂|泵房|门卫房)|楼)|[A-Z]座")
_SCENARIO = re.compile(r"比如|例如|示例|样例|假设|如果")
_UNSAFE = re.compile(r"比如|例如|示例|样例|假设|如果|请自行|自己算|自行计算|不是|不要|不能|别按|不采用|不按|不设|要多少|[？?]")
_UNKNOWN = re.compile(r"还没|没定|没给|没人给|未给|未定|未提供|未说明|未知|待定|待补|待填|UNSPECIFIED|TBD|没有|啥的没有", re.I)
_INTRO = re.compile(r"^\s*(?:[:：=]\s*|是|为|按|先按|设在|在|放|走|取自|大概|约|才|用的是|合计)\s*")
_IDENTITIES = {"unit", "system", "component"}
_META = {"project", "unit", "jurisdiction", "source", "basis", "drawings", "calculations", "interface",
         "cn_basis", "sg_basis", "eu_basis", "cn_interface", "sg_interface", "eu_interface"}
_NUMERIC = {
    "height": r"(?:m|米)", "floors": r"(?:层)", "span": r"(?:m|米)",
    "cooling_load": r"(?:kW|千瓦)", "heating_load": r"(?:kW|千瓦)",
    "capacity": r"(?:kW|千瓦)", "active": r"(?:kW|千瓦)", "reactive": r"(?:kvar)",
    "apparent": r"(?:kVA)", "transformer": r"(?:kVA)", "factor": "",
    "pressure": r"(?:MPa|兆帕|kPa)", "fire_pressure": r"(?:MPa|兆帕|kPa)",
    "water_flow": r"(?:L/s|m[³3]/h)", "fresh_air": r"(?:m[³3]/h)", "air_flow": r"(?:m[³3]/h)",
    "smoke_flow": r"(?:m[³3]/h)", "fire_flow": r"(?:立方米|立方|m[³3]|L/s)",
    "compartment_area": r"(?:平米|平方米|m[²2])", "wind": r"(?:kPa|千帕)",
    "roof_load": r"(?:kN/m[²2])", "fixtures": "",
    "floor_level": r"(?:m|米)?", "outlet_level": r"(?:m|米)?", "municipal_level": r"(?:m|米)?",
}


@dataclass(frozen=True)
class Observation:
    start: int
    end: int
    key: str
    value: str
    labelled: bool = False


@dataclass
class Parsed:
    records: list[dict[str, str]]
    context: dict[str, str]
    top_level: list[str]


def _breaks(text: str, start: int = 0, end: int | None = None) -> list[re.Match[str]]:
    return [match for match in _BREAK.finditer(text, start, len(text) if end is None else end)
            if not (match.group() == ";" and re.search(r"&(?:#\d+|#x[0-9a-f]+|[a-z][a-z0-9]+)$", text[:match.start()], re.I))]


def _literal_source(text: str) -> str:
    scenario = False
    pieces = []
    cursor = 0
    for boundary in [*_breaks(text), None]:
        end = boundary.start() if boundary else len(text)
        clause = text[cursor:end]
        reset = re.match(r"\s*(?:本次实际情况|实际情况|真实记录)(?:是\s*[:：]?|[:：])\s*", clause)
        named_metadata = re.match(r"\s*(?:项目名称|工程名称|项目|单体名称|单体|系统名称|系统|构件编号|构件名称|构件|资料来源|来源|计算书名称|计算书|用户图号|图号|勘察报告|地勘报告|设计任务书)\s*[:：=]", clause)
        if reset:
            scenario = False
        elif _SCENARIO.search(clause) and not (named_metadata and not re.search(r"比如|例如|假设|如果|只是示例|只是样例", clause)):
            scenario = True
        pieces.append(" " * len(clause) if scenario else clause)
        if boundary:
            pieces.append(boundary.group())
            cursor = boundary.end()
    return "".join(pieces)


def _value(text: str) -> str:
    text = text.strip()
    if not re.search(r"&(?:#\d+|#x[0-9a-f]+|[a-z][a-z0-9]+);$", text, re.I):
        text = text.rstrip("，,；;。")
    return MISSING if not text or _UNKNOWN.fullmatch(text) else text


def literal_parameter(key: str, text: str, numeric_fields: dict[str, str] | None = None) -> str:
    """Keep explicit literals intact, but do not promote examples/instructions."""
    if key in (_NUMERIC if numeric_fields is None else numeric_fields):
        if _UNSAFE.search(text) or _UNKNOWN.search(text):
            return MISSING
        for boundary in _breaks(text):
            if boundary.group() in {",", "，"} and re.match(r"\s*(?:出自|取自|来源)", text[boundary.end():]):
                return _value(text[:boundary.start()])
    return _value(text)


def _number(key: str, text: str, numeric_fields: dict[str, str] | None = None) -> str | None:
    if _UNSAFE.search(text) or _UNKNOWN.search(text):
        return MISSING
    unit = (_NUMERIC if numeric_fields is None else numeric_fields)[key]
    pattern = r"(?<![A-Za-z0-9_.])(" + _NUMBER + r"\s*" + unit + r")(?![A-Za-z0-9_.-])"
    match = re.search(pattern, text, re.I)
    if not match:
        return None
    # Never take a suffix from a reference/date/building identifier. Source
    # descriptions before a number are fine; an intervening source ID is not.
    prefix = text[:match.start()]
    if re.search(r"[A-Za-z0-9]|图号|编号|日期|图纸", prefix):
        return None
    return match.group(1).strip()


def _scan(post: str, text: str, aliases: dict[str, str], *, numeric_fields: dict[str, str] | None = None,
          identities: set[str] | None = None, unit_key: str = "unit", reference_keys: set[str] | None = None) -> tuple[list[Observation], bool]:
    numeric_fields = _NUMERIC if numeric_fields is None else numeric_fields
    identities = _IDENTITIES if identities is None else identities
    reference_keys = {"calculations", "drawings", "node", "municipal_well"} if reference_keys is None else reference_keys
    events: list[Observation] = []
    enhanced = False
    labels = re.compile("|".join(sorted(map(re.escape, aliases), key=len, reverse=True)), re.I)
    candidates = [match for match in labels.finditer(text) if not (
        re.fullmatch(r"[a-zφ]+", match.group(), re.I) and (
            match.start() and re.match(r"[A-Za-z0-9_]", text[match.start()-1])
            or match.end() < len(text) and re.match(r"[A-Za-z_]", text[match.end()])
        )
    )]
    # "先做两个单体：1#厂房设备容量…" is a list introduction,
    # not a singular form field whose whole value is one building name.
    candidates = [match for match in candidates if not (
        aliases[match.group().casefold()] in {"unit", "object"}
        and re.search(r"(?:\d+|[一二三四五六七八九十两]+)(?:个|栋|座)$", text[:match.start()])
        and re.match(r"\s*[:：]", text[match.end():])
    )]
    explicit = [match for match in candidates if re.match(r"\s*[:：=]", text[match.end():])]
    protected = []
    for index, match in enumerate(explicit):
        boundaries = _breaks(text, match.end())
        end = boundaries[0].start() if boundaries else len(text)
        if index + 1 < len(explicit):
            end = min(end, explicit[index + 1].start())
        protected.append((match.end(), end))
    candidates = [match for match in candidates if match in explicit or not any(start <= match.start() < end for start, end in protected)]
    for index, match in enumerate(candidates):
        key = aliases[match.group().casefold()]
        tail_start = match.end()
        delimiter = re.match(r"\s*[:：=]\s*", text[tail_start:])
        labelled = bool(delimiter)
        if delimiter:
            tail_start += delimiter.end()
        boundaries = _breaks(text, tail_start)
        if labelled and key != "jurisdiction":
            boundaries = [boundary for boundary in boundaries if boundary.group() not in {"，", ","}]
        end = boundaries[0].start() if boundaries else len(text)
        if index + 1 < len(candidates):
            next_key = aliases[candidates[index + 1].group().casefold()]
            if not (key == "factor" and next_key in {"load", "calculations"}):
                end = min(end, candidates[index + 1].start())
        tail = text[tail_start:end].strip()
        if labelled:
            # Literal explicit text values (including "用户原表…") are not
            # normalized. Unsafe numerical examples are still not parameters.
            value = literal_parameter(key, tail, numeric_fields)
        elif key in numeric_fields:
            value = _number(key, tail, numeric_fields)
        elif key in reference_keys:
            reference = re.sub(r"^(?:目前只有|目前仅有|只有|仅有|是|为|在|编号)\s*", "", tail)
            code = re.search(r"(?<![A-Za-z0-9_-])(?:W\d+|BH\d+|ZK\d+|" + _CODE + r")(?![A-Za-z0-9_-])", reference)
            value = code.group() if code and not _UNSAFE.search(tail) else None
            if not code and _UNKNOWN.search(tail):
                value = MISSING
        else:
            # Text observations require a separator/connector, rather than
            # treating every professional word inside prose as a field label.
            connector = re.match(r"[ \t]+|(?:是|为|按|先按|设在|在|放|走|取自)", text[match.end():])
            value = _INTRO.sub("", tail, count=1) if connector and tail else None
            if _UNKNOWN.search(tail):
                value = MISSING
            elif value and _UNSAFE.search(tail):
                value = MISSING
        clause_start = max((boundary.end() for boundary in _breaks(text, 0, match.start())), default=0)
        if not labelled and _UNSAFE.search(text[clause_start:end]):
            value = MISSING if key not in identities else None
        if value is not None:
            events.append(Observation(match.start(), end, key, _value(value), labelled))
            enhanced = enhanced or not labelled

    def emit(key: str, match: re.Match[str] | None, group: str | int = 1) -> None:
        nonlocal enhanced
        if match is None:
            return
        if any(event.labelled and event.start <= match.start() < event.end for event in events):
            return
        # Only the local clause is rejected. A missing heat load must not
        # suppress a separately supplied cold load or another object's facts.
        start = max((boundary.end() for boundary in _breaks(text, 0, match.start())), default=0)
        stops = _breaks(text, match.end())
        clause = text[start:stops[0].start() if stops else len(text)]
        if _UNSAFE.search(clause):
            return
        events.append(Observation(match.start(group), match.end(group), key, match.group(group).strip()))
        enhanced = True

    for match in _UNIT.finditer(text):
        emit(unit_key, match, 0)
        # A project immediately preceding a numbered building is a literal
        # name, not a guessed project inferred from a place or source author.
        prefix = text[max(0, match.start() - 45):match.start()]
        project = re.search(r"([一-鿿]{2,30}(?:中心|园|广场|基地|工程|园区))$", prefix)
        if project and not any(event.key == "project" for event in events):
            name = re.sub(r"^.*(?:起草|帮我出个|帮我出一份|说明草稿|说明|提纲)[，,\s]*", "", project.group(1))
            events.append(Observation(match.start() - len(name), match.start(), "project", name))

    if post == "hvac":
        for match in re.finditer(r"(?:的)?(集中空调|净化空调|中央空调)系统", text):
            emit("system", match)
        for match in re.finditer(r"(?:^|[，,；;。\n])([^，,；;。\n]{1,20}?)(?:那套)?是(风机盘管带新风|风机盘管加新风|全空气)", text):
            emit("system", match, 2)
            emit("scope", match, 1)
        for key, expression in (
            ("scope", r"服务(?:空间\s*[:：]?\s*)?([^，,；;。\n]+)"),
            ("outdoor", r"室外干球(?:按|为|是)?\s*(" + _NUMBER + r"\s*℃)"),
            ("room", r"(?:制冷)?机房(?:位置)?(?:设在|在|\s+)([^，,；;。\n]+)"),
            ("tower", r"冷却塔(?:位置)?(?:放|设在|在|\s+)([^，,；;。\n]+)"),
            ("cooling_load", r"(?<![一-鿿A-Za-z])冷\s*(" + _NUMBER + r"\s*kW)"),
            ("heating_load", r"(?<![一-鿿A-Za-z])热\s*(" + _NUMBER + r"\s*kW)"),
        ):
            for match in re.finditer(expression, text, re.I):
                emit(key, match)
    elif post == "electrical":
        for key, expression in (
            ("circuits", r"(双回路|单回路)(?:供电)?"),
            ("voltage", r"(?:市政电源|供电)[^，,；;。\n]{0,16}?(" + _NUMBER + r"\s*kV)"),
            ("metering", r"(高供高计|高供低计)"),
            ("lightning", r"防雷(?:类别)?(?:按|是|为)?\s*([一二三]类)"),
            ("lightning", r"([一二三]类)防雷"),
            ("transformer_count", r"变压器(?:甲方已定|已定|\s*[:：]?\s*)(\d+\s*台)"),
            ("transformer", r"单台\s*(" + _NUMBER + r"\s*kVA)"),
            ("backup", r"(自备[^，,；;。\n]*柴油发电机)"),
        ):
            for match in re.finditer(expression, text, re.I):
                emit(key, match)
        # Elliptical capacity after a newly named building: a kW literal is
        # accepted only with the stated capacity connector, not any nearby kW.
        for match in re.finditer(r"(?:" + _UNIT.pattern + r")(?:设备容量)?(?:才|为|是|大概)\s*(" + _NUMBER + r"\s*kW)", text, re.I):
            emit("capacity", match)
    elif post == "fire-protect":
        for key, expression in (
            ("function", r"(?:[A-Z]座)是([^，,；;。\n]+)"),
            ("classification", r"([一二]类高层公共建筑|多层公共建筑)"),
            ("fire_grade", r"耐火等级\s*([一二三四]级)"),
            ("smoke_method", r"(?:走|是)(机械排烟|自然排烟窗)"),
            ("fire_lane", r"(环形消防车道[^，,；;。\n]+)"),
            ("fire_system", r"(室内消火栓(?:加|\+|和)自动喷水灭火系统)"),
        ):
            for match in re.finditer(expression, text):
                emit(key, match)
    elif post == "plumbing":
        for key, expression in (
            ("connection", r"(?:从|接自)([^，,；;。\n]*?路)(?=DN\d+|接|市政)"),
            ("mains_pipe", r"(DN\d+)(?=市政)"),
            ("municipal_well", r"(?:市政(?:污水)?井)\s*(W\d+)"),
            ("source", r"资料是([^，,；;。\n]+)"),
        ):
            for match in re.finditer(expression, text):
                emit(key, match)
    elif post == "steel":
        for expression in (r"钢[梁柱]\s*(" + _CODE + r")", r"(" + _CODE + r")刚架"):
            for match in re.finditer(expression, text):
                emit("component", match)
        for key, expression in (
            ("material", r"(?:钢材|材料|用的是)\s*(Q\d{3}[A-Z]+)"),
            ("structure", r"是(门式刚架)"),
            ("crane", r"(\d+(?:\.\d+)?t[^，,；;。\n]{0,10}吊车)"),
            ("bolt", r"(\d+(?:\.\d+)?级M\d+高强螺栓)"),
            ("embedment", r"(预埋件\s*" + _CODE + r"(?:焊接)?)"),
            ("anchor", r"地脚螺栓\s*(\d+根M\d+)"),
            ("fireproof", r"刷([^，,；;。\n]{1,20}防火涂料)"),
        ):
            for match in re.finditer(expression, text, re.I):
                emit(key, match)

    # Code references may have a short natural source/ownership clause before
    # their ID (including a comma). The ID itself remains a separate field.
    for match in re.finditer(r"(?:逐时负荷|负荷)?计算书(?:编号)?([^。；;\n]{0,45}?)(?<![A-Za-z0-9_-])(" + _CODE + r")(?![A-Za-z0-9_-])", text):
        if not _UNSAFE.search(match.group(1)):
            emit(aliases.get("计算书", "calculations"), match, 2)
    return sorted(events, key=lambda event: (event.start, event.key not in identities)), enhanced


def scan_literal_fields(post: str, text: str, aliases: dict[str, str], **options) -> tuple[str, list[Observation], bool]:
    """Shared scanner; the caller owns domain-specific hierarchy and assignment."""
    literal = _literal_source(text)
    events, enhanced = _scan(post, literal, aliases, **options)
    return literal, events, enhanced or literal != text


def parse_narrative(post: str, text: str, aliases: dict[str, str], context: dict[str, str],
                    existing: list[dict[str, str]] | None = None) -> Parsed | None:
    original = text
    text = _literal_source(text)
    events, enhanced = _scan(post, text, aliases)
    if not enhanced and text == original:
        return None
    rows: list[dict[str, str]] = list(existing or [])
    initial_count = len(rows)
    shared = dict(context)
    current: dict[str, str] | None = None
    descriptors: dict[int, str] = {}
    first_identity = next((event.start for event in events if event.key in _IDENTITIES), len(text))
    prefix = text[:first_identity]
    top_level: list[str] = [prefix.rsplit("\n", 1)[0]] if "\n" in prefix else []

    def put(row: dict[str, str], key: str, value: str) -> None:
        prior = row.get(key)
        if prior is None or prior == MISSING:
            row[key] = value
        elif value != MISSING and value != prior:
            # Conflicting literal inputs are retained as alternatives, not
            # silently selected or combined into a computed design figure.
            alternatives = prior.split("；")
            if value not in alternatives:
                row[key] = prior + "；" + value

    def new(identities: dict[str, str]) -> dict[str, str]:
        inherited = {key: value for key, value in shared.items() if key in {"project", "unit", "jurisdiction"}}
        if "unit" in identities and identities["unit"] != shared.get("unit"):
            inherited.pop("unit", None)
        inherited.update(identities)
        rows.append(inherited)
        return inherited

    # Use statement boundaries, not every field match, as the assignment
    # unit. Thus a value appearing before its named system stays with it.
    groups = []
    line_start = 0
    for line in text.splitlines(keepends=True):
        line_end = line_start + len(line)
        identities = [event for event in events if line_start <= event.start < line_end and event.key in _IDENTITIES]
        labelled_line = identities and all(event.labelled for event in identities) and len({event.key for event in identities}) == len(identities)
        if labelled_line:
            groups.append((line_start, line_end))
        else:
            boundaries = [line_start, *[match.end() for match in _breaks(text, line_start, line_end)], line_end]
            groups.extend(zip(boundaries, boundaries[1:]))
        line_start = line_end
    for start, end in groups:
        local = [event for event in events if start <= event.start < end]
        if not local:
            continue
        clause = text[start:end].strip("，,；;。\n ")
        identities = {event.key: event.value for event in local if event.key in _IDENTITIES}
        if identities:
            strongest = "component" if "component" in identities else "system" if "system" in identities else "unit"
            candidates = [row for row in rows if row.get(strongest) == identities[strongest]
                          and ("unit" not in identities or row.get("unit") == identities["unit"])]
            if len(candidates) == 1:
                current = candidates[0]
            elif len(candidates) > 1 and "unit" not in identities:
                current = new({**identities, "unit": MISSING})
            elif current is not None and not set(current) - _META and not any(
                key in current and current[key] != value for key, value in identities.items()
            ):
                # A single labelled building header belongs to its first
                # system. Its local jurisdiction is not a sibling default.
                current.update(identities)
            else:
                current = new(identities)
            for key, value in identities.items():
                put(current, key, value)
            if "unit" in identities:
                shared["unit"] = identities["unit"]
            if "component" in identities:
                descriptors[id(current)] = clause
        if current is None:
            current = new({})
        target = current
        sentence_start = max(text.rfind("。", 0, start), text.rfind("\n", 0, start), text.rfind("；", 0, start)) + 1
        sentence_end = min([position for char in "。\n；" if (position := text.find(char, end - 1)) >= 0] or [len(text)])
        sentence = text[sentence_start:sentence_end]
        ownership = re.search(r"(?:只有|仅有|只提供了)([一-鿿]{1,12}?)的", sentence)
        if ownership and any(event.key == "calculations" for event in local):
            owners = [row for row in rows if ownership.group(1) in row.get("unit", "")]
            target = owners[0] if len(owners) == 1 else new({"unit": MISSING})
        if post == "steel" and any(event.key == "fireproof" for event in local):
            # Literal place/type references are usable only when they uniquely
            # match a previously named component's declaration.
            references = re.findall(r"([一-鿿]{1,15})([梁柱])(?:刷|的)", clause)
            owners = []
            for row in rows:
                descriptor = descriptors.get(id(row), "")
                if any("钢" + kind in descriptor and any(place[-length:] in descriptor for length in range(2, len(place) + 1))
                       for place, kind in references):
                    owners.append(row)
            target = owners[0] if len(owners) == 1 else new({"component": MISSING, "unit": MISSING})
        for event in local:
            if event.key in _IDENTITIES:
                continue
            if event.key == "jurisdiction" and not any(set(row) & _IDENTITIES for row in rows):
                shared["jurisdiction"] = event.value
                top_level.append("辖区：" + event.value)
            if event.key == "project":
                shared["project"] = event.value
            if event.key == "calculations" and re.search(r"两套都|两栋都|两座都", sentence):
                objects = [row for row in rows if any(key in row for key in _IDENTITIES)]
                if len(objects) == 2:
                    for row in objects:
                        put(row, event.key, event.value)
                    continue
            if event.key == "source" and not event.labelled and len([row for row in rows if set(row) - _META]) > 1 and not identities:
                rows.append({"source": event.value})
                continue
            put(target, event.key, event.value)
    return Parsed(rows[initial_count:], shared, top_level)
