"""Bounded literal fields for building/member and borehole/layer hierarchies.

Assignments remain in design_basic. This scanner supplies source spans and
literal field values; it never calculates, converts units or chooses a design.
"""

from __future__ import annotations

import re

from packing_assistant.post_drafts.design_observations import (
    MISSING, Observation, _breaks, _CODE, _NUMBER, _UNIT, _UNKNOWN, _UNSAFE,
    literal_parameter, scan_literal_fields,
)

IDENTITIES = {"object", "component", "hole", "layer", "soil", "facade", "space", "drawing_id", "interface_id", "information"}
NUMERIC = {
    "area": r"(?:平米|平方米|㎡|m[²2]|平)?", "site_area": r"(?:平米|平方米|㎡|m[²2]|平)?",
    "height": r"(?:m|米)?", "storey_height": r"(?:m|米)?", "clear_height": r"(?:m|米)?",
    "floors": r"(?:层)?", "basements": r"(?:层)?", "span": r"(?:m|米)?", "depth": r"(?:m|米)?",
    "dead_load": r"(?:kN/m[²2]|kPa|kN)?", "live_load": r"(?:kN/m[²2]|kPa|kN)?",
    "wind_load": r"(?:kPa)?", "snow_load": r"(?:kPa)?", "wind_pressure": r"(?:kPa|Pa)?",
    "hole_depth": r"(?:m|米)?", "layer_depth": r"(?:m|米)?", "water": r"(?:m|米)?",
    "cohesion": r"(?:kPa)?", "friction": r"(?:度|°)?", "modulus": r"(?:MPa)?",
    "bearing": r"(?:kPa)?", "pile_friction": r"(?:kPa)?", "panel_grid": r"(?:mm|m|米)?",
    "panel_thickness": r"(?:mm)?", "thermal": r"(?:W/\(?m[²2][·.]?K\)?)?", "shading": "",
}
_EXTRA = {
    "architecture": {"总建筑面积": "area", "首层层高": "storey_height", "高": "height", "提资": "information"},
    "structure": {"混凝土": "concrete", "钢筋": "rebar", "钢材": "steel", "设防烈度": "seismic_parameter"},
    "geotech": {"稳定水位埋深": "water", "地下水埋深": "water", "c值": "cohesion", "φ值": "friction"},
    "facade": {"体系": "facade_type", "风压来源": "source", "预埋": "embed", "分格宽": "panel_grid",
               "水密": "watertight", "气密": "air"},
}


def aliases_for(post: str, aliases: dict[str, str]) -> dict[str, str]:
    result = {**aliases, **_EXTRA[post]}
    if post != "geotech":
        for alias in ("c", "φ", "phi"):
            result.pop(alias, None)
    return result


def parameter_value(key: str, value: str) -> str:
    result = literal_parameter(key, value, NUMERIC)
    return "" if result == MISSING else result


def statements(post: str, raw: str, aliases: dict[str, str]) -> list[tuple[str, list[Observation]]] | None:
    text, events, enhanced = scan_literal_fields(post, raw, aliases, numeric_fields=NUMERIC,
        identities=IDENTITIES, unit_key="object", reference_keys={"drawing_id", "hole", "calculation"})

    def emit(key: str, match: re.Match[str], group: str | int = 1, *, unknown_ok: bool = False) -> None:
        nonlocal enhanced
        if any(event.labelled and event.start <= match.start() < event.end
               and not (event.key == "information" and key in {"deadline", "receiver"}) for event in events):
            return
        start = max((boundary.end() for boundary in _breaks(text, 0, match.start())), default=0)
        ends = _breaks(text, match.end())
        clause = text[start:ends[0].start() if ends else len(text)]
        if _UNSAFE.search(clause) or (not unknown_ok and _UNKNOWN.search(match.group(group))):
            return
        value = match.group(group).strip()
        # A more complete observation in the same field/statement replaces
        # only a literal substring, never a sibling's or conflicting value.
        for event in list(events):
            if event.key == key and not event.labelled and start <= event.start <= match.end() and event.value in value:
                events.remove(event)
        events.append(Observation(match.start(group), match.end(group), key, value))
        enhanced = True

    patterns: list[tuple[str, str]] = []
    if post in {"architecture", "structure"}:
        patterns.append(("floors", r"地上\s*(\d+层)"))
    if post == "architecture":
        unit = r"(?:" + _UNIT.pattern + r")"
        patterns += [
            ("function", unit + r"是([^，,；;。\n]+)"),
            ("function", unit + r"(商业带办公|办公带商业|人才公寓|商业|办公|幼儿园|住宅|宿舍|厂房)"),
            ("function", r"(?:另外那个|另一个|那个|还有)([^，,；;。\n]+?)是" + unit),
            ("area", unit + r"(?:才|是|为)\s*(" + _NUMBER + r"\s*(?:平米|平方米|㎡|平))"),
            ("area", r"(?:^|[，,；;。\n])才\s*(" + _NUMBER + r"\s*(?:平米|平方米|㎡|平))"),
            ("floors", r"(?:^|[，,；;。\n])([一二三四五六七八九十]+层)(?=$|[，,；;。\n])"),
            ("accessible_lift", r"无障碍电梯\s*(\d+部(?:[，,]设在[^；;。\n]+)?)"),
            ("entrance", r"主入口(?:开在|设在|在)([^，,；;。\n]+)"),
            ("revision", r"(?:版本\s*|是)(V\d+(?:\.\d+)*|[A-Z]版)"),
            ("brief", r"设计任务书(?:用|采用)([^，,；;。\n]+)"),
            ("deadline", r"提资\s*[:：]?\s*(\d{4}-\d{2}-\d{2}前)"),
            ("receiver", r"提资\s*[:：]?\s*[^，,；;。\n]{0,30}?给(结构|建筑|暖通|电气|给排水|幕墙)提"),
        ]
    elif post == "structure":
        patterns += [
            ("system", r"(框架剪力墙|剪力墙|门式刚架|框架)(?:结构)?"),
            ("component", r"(?:转换梁|连梁|钢柱|柱子|梁|柱)\s*(" + _CODE + r")"),
            ("concrete", r"(?:混凝土|用)\s*(C\d{2,3})(?![A-Za-z0-9])"),
            ("rebar", r"(?:钢筋|用)\s*(HRB\d{3}[A-Z]?)(?![A-Za-z0-9])"),
            ("steel", r"(?:钢材|用)\s*(Q\d{3}[A-Z]+)(?![A-Za-z0-9])"),
            ("section", r"截面\s*(\d+(?:\.\d+)?[×x]\d+(?:\.\d+)?)"),
            ("seismic_parameter", r"设防烈度\s*(\d+度\s*\d+(?:\.\d+)?g)"),
        ]
    elif post == "geotech":
        patterns += [
            ("hole", r"(?<![A-Za-z0-9-])((?:ZK|BH)[-]?\d+)(?=(?:那个孔|孔深|孔|\s))"),
            ("hole_depth", r"(?:那个孔|孔)(?:打了|深)(" + _NUMBER + r"\s*(?:米|m)?)"),
            ("layer", r"第([一二三四五六七八九十\d]+层)"),
            ("soil", r"第[一二三四五六七八九十\d]+层(?:是|变成)?([^，,；;。\n]+?)(?=c值|c给|c是|[，,；;。\n]|$)"),
            ("stage", r"(详勘|初勘|可研勘察)阶段"),
            ("recommendation", r"报告建议(?:基础)?用([^，,；;。\n]+)"),
        ]
    elif post == "facade":
        patterns += [
            ("facade", r"(?:编号|幕墙编号)\s*[:：]?\s*(" + _CODE + r")"),
            ("facade", r"(?:的|是)(" + _CODE + r")是(?:单元式|构件式|全玻)"),
            ("facade", r"(?:单元式|构件式|全玻)(?:的|幕墙的)\s*(" + _CODE + r")"),
            ("facade_type", r"(单元式玻璃幕墙|构件式明框玻璃幕墙|构件式玻璃幕墙|全玻幕墙|单元式|构件式)"),
            ("embed", r"预埋(?:用|单元式用|构件式用|全玻用)([^，,；;。\n]+)"),
            ("movement", r"层间变位\s*(\d+/\d+)"),
            ("watertight", r"水密(?:要求|要)?\s*((?:固定部分)?\d+Pa)"),
            ("shop_drawings", r"加工图是([^，,；;。\n]+)"),
        ]
    for key, expression in patterns:
        for match in re.finditer(expression, text, re.I):
            emit(key, match)
    if post in {"architecture", "structure", "facade"}:
        # An unfamiliar numbered building at a statement boundary must not
        # let its following parameters silently flow into the prior building.
        # Without a bounded name match its identity remains explicitly unknown.
        for match in re.finditer(r"(?:^|[，,；;。\n])\s*(?:东侧|西侧|南侧|北侧)?(\d+[#＃])", text):
            if any(event.key == "object" and event.start == match.start(1) for event in events):
                continue
            if any(event.labelled and event.start <= match.start(1) < event.end for event in events):
                continue
            ends = _breaks(text, match.end())
            clause = text[match.start():ends[0].start() if ends else len(text)]
            if _UNSAFE.search(clause):
                continue
            events.append(Observation(match.start(1), match.end(1), "object", MISSING))
            enhanced = True
    if not enhanced:
        return None
    events.sort(key=lambda event: (event.start, event.key not in IDENTITIES))
    result: list[tuple[str, list[Observation]]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        stop = offset + len(line)
        line_events = [event for event in events if offset <= event.start < stop]
        anchors = [event for event in line_events if event.key in IDENTITIES]
        if anchors and all(event.labelled for event in anchors):
            result.append((text[offset:stop], line_events))
        else:
            boundaries = [offset, *[boundary.end() for boundary in _breaks(text, offset, stop)], stop]
            for start, end in zip(boundaries, boundaries[1:]):
                local = [event for event in line_events if start <= event.start < end]
                result.append((text[start:end], local))
        offset = stop
    # A facade declaration can put its code in the immediately following
    # comma clause: "…是全玻幕墙，编号QB-01". Do not leave the type on a building.
    merged: list[tuple[str, list[Observation]]] = []
    for source, local in result:
        if post == "facade" and any(event.key == "facade" for event in local) and merged and (
            any(event.key == "facade_type" for event in merged[-1][1])
            and not any(event.key == "facade" for event in merged[-1][1])
        ):
            prior, previous = merged.pop()
            merged.append((prior + source, previous + local))
        elif local:
            merged.append((source, local))
    return merged
