"""Literal specialty observations with project, file and object ownership.

No sizing, selection, unit conversion, approval or operational side effects.
Object associations are explicit names or the current bounded parent context.
"""
from __future__ import annotations

import html
import ipaddress
import re
import unicodedata

from packing_assistant.post_drafts.design_specialties import (
    _ALIASES, _FIELDS, _GLOBAL, _GROUPS, _INTERFACE, _Input, _SHARED, UNKNOWN,
)
from packing_assistant.post_drafts.planning import _inputs
from packing_assistant.post_drafts.procurement import _literal_source, _source_name
from packing_assistant.word_export import _table_cells

_NUMBER = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_CODE = r"[A-Za-z]+[A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*"
_UNIT = r"(?:公分|厘米|毫米|平米|平方米|平|米|mm|cm|m[²³23]?|方每秒|[m米][³3]?/s|吨|株|棵|只|个|樘|天)?"
_UNKNOWN = re.compile(r"还没|没定|没给|没出|没拿到|没人|未给|未定|未提供|未说明|未批|未知|待定|待补|待填|暂无|UNSPECIFIED|TBD|N/A", re.I)
_SCENARIO = re.compile(r"比如|例如|示例|样例|假设|如果|自行计算|自己算|不采用|不按|别按|不要|不是|是否|[？?]")
_LABEL = re.compile(r"(?<![A-Za-z0-9_])("+"|".join(map(re.escape, sorted(_ALIASES, key=len, reverse=True)))+r")\s*[:：=]\s*", re.I)
_NUMERIC = {"area", "elevation", "indoor_elevation", "slope", "soil_depth", "paving_thickness", "dbh", "crown", "quantity", "waterproof_thickness", "upturn", "clear_height", "escape_width", "points", "pixels", "retention", "separation", "route_length", "entrances", "wall_thickness", "water_level", "flow", "frequency", "groundwater", "permeability", "crest", "crest_width", "gate_count", "head", "door_size", "tray_spec"}
_PRIMARY = {post: next(iter(groups)) for post, groups in _GROUPS.items()}
_IPV4 = re.compile(r"(?<![A-Za-z0-9_.])(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?(?![A-Za-z0-9_.])")


def _redact_ipv4(match):
    try:
        ipaddress.ip_address(match.group().split("/")[0])
    except ValueError:
        return match.group()
    return "[敏感内容已省略]"


def _sensitive(value):
    from packing_assistant.post_drafts import it
    normalized = unicodedata.normalize("NFKC", html.unescape(value))
    return (any(pattern.search(normalized) for pattern in (it._PRIVATE_KEY, it._QUOTED_SECRET, it._SECRET_START, it._URL, it._TOKEN, it._ADDRESS))
            or any(_redact_ipv4(match) != match.group() for match in _IPV4.finditer(normalized))
            or any(it._redact_ipv6(match) != match.group() for match in it._IPV6_CANDIDATE.finditer(normalized)))


def _redact_ipv4_text(value):
    from packing_assistant.post_drafts.it import _redact
    normalized = unicodedata.normalize("NFKC", html.unescape(value))
    return _redact(_IPV4.sub(_redact_ipv4, normalized))


def _weak_input(text):
    from packing_assistant.post_drafts import it
    def mask(match):
        return "".join("\n" if c == "\n" else " " for c in match.group())
    text = it._PRIVATE_KEY.sub(mask, text)
    text = it._QUOTED_SECRET.sub(mask, text)
    lines = []
    private_block, quoted = False, None
    for line in text.splitlines(keepends=True):
        normalized = unicodedata.normalize("NFKC", html.unescape(line))
        if private_block or re.search(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", normalized, re.I):
            private_block = not bool(re.search(r"-----END [A-Z0-9 ]*PRIVATE KEY-----", normalized, re.I))
            lines.append("".join("\n" if c == "\n" else " " for c in line))
            continue
        if quoted:
            if re.search(r"(?<!\\)"+re.escape(quoted), normalized):
                quoted = None
            lines.append("".join("\n" if c == "\n" else " " for c in line))
            continue
        # A credential assignment discards its physical line before labels are
        # scanned; otherwise a label inside its value could leak a secret tail.
        secret = it._SECRET_START.search(normalized)
        if secret:
            tail = normalized[secret.end():].lstrip()
            if tail.startswith(('"', "'")) and not re.search(r"(?<!\\)"+re.escape(tail[0]), tail[1:]):
                quoted = tail[0]
            if _source_name(line.strip()) is not None:
                lines.append("【用户上传：敏感文件名已省略】\n")
            else:
                lines.append("".join("\n" if c == "\n" else " " for c in line))
        else:
            lines.append(line)
    return "".join(lines)


def _clean(value):
    value = value.strip(" \t\r\n,，；。")
    if not re.search(r"&(?:#\d+|#x[0-9a-f]+|[a-z][a-z0-9]+);$", value, re.I):
        value = value.rstrip(";")
    return value or UNKNOWN


def _typed(key, value):
    value = _clean(value)
    if re.fullmatch(r"(?:\[A\d+\]\s*)?(?:待填|待核|未知|未提供|未给|未说明|UNSPECIFIED|TBD|N/A)", value, re.I):
        return UNKNOWN
    if key in _NUMERIC and (_UNKNOWN.search(value) or _SCENARIO.search(value)):
        return UNKNOWN
    if key in {"jurisdiction", "record_zone"}:
        return {"CN": "CN", "SG": "SG", "EU": "EU", "DUAL": "DUAL", "中国": "CN", "新加坡": "SG"}.get(value.upper(), UNKNOWN)
    return value


def _details(key, value, post):
    pairs = [(key, value)]
    if key == "system":
        system = re.fullmatch(r"(视频监控|视频安防|公共广播(?:系统)?|出入口控制|门禁)覆盖(.+)", value)
        if system:
            pairs = [("system", system.group(1)), ("coverage", system.group(2))]
    if key == "unit_name" and re.search(r"FH-\d+", value) and re.search(r"是|做", value):
        observed = _scan(value, post)
        if any(k == "unit_id" for k, _, _ in observed):
            pairs = [(k, v) for k, v, _ in observed]
    if _typed(key, value) == UNKNOWN and _clean(value) != UNKNOWN and _UNKNOWN.search(value):
        pairs.append(("pending", _FIELDS[key][0]+"："+value))
    if key == "equipment":
        model = re.match(r"("+_CODE+r")(?=[一-鿿])", value)
        if model:
            pairs.append(("model", model.group(1)))
    return pairs


def _scan(text, post):
    text = re.sub(r"^(?:本次实际情况|实际情况|真实记录)\s*[:：]\s*", "", text)
    labels = list(_LABEL.finditer(text))
    if labels:
        reference = (re.match(r"(监控|门禁)(?:走的|那边)", text) if post == "intel-weak"
                     else re.match(r"([A-Z]区)(?:战时|的)", text) if post == "civil-defense" else None)
        return ([("_reference", reference.group(1), "")] if reference else [])+[
                (k, v, match.group(1)) for i, match in enumerate(labels)
                for k, v in _details(_ALIASES[match.group(1).casefold()], text[match.end():labels[i+1].start() if i+1 < len(labels) else len(text)], post)]
    if _SCENARIO.search(text):
        return []
    pairs = []

    def emit(key, pattern, group=1):
        match = re.search(pattern, text, re.I)
        if match:
            pairs.append((key, match.group(group), ""))

    def numeric(key, label, unit=_UNIT):
        emit(key, label+r"\s*(?:是|为|按|用|留|刷|做|埋深)?\s*("+_NUMBER+r"\s*"+unit+r")")

    emit("project", r"项目(?:是|为)\s*(.+)$")
    emit("stage", r"(初步设计|方案深化|施工图设计|方案设计)(?:阶段)?")
    if re.fullmatch(r"CN|SG|EU|DUAL|中国|新加坡", text, re.I):
        pairs.append(("jurisdiction", text, ""))
    if post == "landscape":
        zone = re.search(r"((?:[东西南北]侧|[东西南北])?(?:入口广场|儿童活动区|宅间绿地|屋顶花园|[一-鿿]{1,8}广场))", text)
        if zone:
            pairs.append(("zone_name", zone.group(1), ""))
        plant = re.search(r"(?:种|种植)([一-鿿]+?)(?=$|[，；\s])|苗木表给了([一-鿿]+?)(?=胸径|冠幅|数量)", text)
        if plant:
            pairs.append(("plant_name", plant.group(1) or plant.group(2), ""))
        numeric("dbh", "胸径")
        numeric("crown", "冠幅")
        numeric("quantity", r"(?:数量|一共|共)")
        numeric("area", r"面积(?:大概|约)?")
        numeric("elevation", "室外标高")
        numeric("soil_depth", r"覆土(?:厚度)?(?:结构只答应|按|只答应)?")
        emit("irrigation", r"灌溉(?:用|是|采用)?(.+)$")
        emit("paving_material", r"(?:铺装用|地面用|园路用)(.+)$")
        if any(k == "paving_material" for k, _, _ in pairs):
            pairs.insert(next(i for i, p in enumerate(pairs) if p[0] == "paving_material"), ("paving_name", "园路" if "园路用" in text else "铺装", ""))
        if re.search(r"苗木表(?:是|来源)", text):
            value = re.split(r"苗木表(?:是|来源)", text, maxsplit=1)[1].strip("：: ")
            pairs += [("_all", "plants", ""), ("plant_source", value, "")]
        if "甲方苗木表给了" in text:
            pairs.append(("plant_source", "甲方苗木表", ""))
    elif post == "interior":
        room = re.search(r"((?:\d层)?(?:主卫|公卫|卫生间|茶水间|大会议室|办公室|客厅|厨房))", text)
        if room:
            pairs.append(("room", room.group(1), ""))
        for key, label in (("floor", "地面"), ("wall", "墙面"), ("ceiling", "吊顶")):
            match = re.search(label+r"\s*(?:是|贴|用|改)?(.+)$", text)
            if match and not _UNKNOWN.search(match.group(1)):
                value = re.split(r"净高", match.group(1), maxsplit=1)[0].removesuffix("了").strip()
                if not (key == "floor" and re.search(r"防水只做", text)):
                    pairs.append((key, value, ""))
        numeric("clear_height", "净高")
        numeric("upturn", "上翻")
        wp = re.search(r"防水(?:做|刷)(.+?)("+_NUMBER+r")厚", text)
        if wp:
            pairs += [("waterproof", wp.group(1), ""), ("waterproof_thickness", wp.group(2), "")]
        if "防水只做地面" in text:
            pairs.append(("waterproof_area", "地面", ""))
        emit("drawing", r"节点(?:图号|看)(?:是|为)?\s*("+_CODE+r")")
        emit("joint_owner", r"收口责任(?:归|由|是|为)(.+)$")
        if re.search(r"收口都找", text):
            pairs += [("_all", "rooms", ""), ("joint_owner", text.split("收口都找", 1)[1], "")]
    elif post == "intel-weak":
        emit("system", r"(?:只写|系统[:：]?)(公共广播(?:系统)?)")
        emit("system", r"(视频监控)(?=覆盖)")
        emit("system", r"^(门禁)(?=只做)")
        emit("coverage", r"(?:覆盖|^门禁只做)(.+)$")
        terminal = re.search(r"(扬声器|摄像机|读卡器)\s*(?:按.+?共|一共|共)?\s*("+_NUMBER+r"\s*(?:只|个))", text)
        if terminal:
            pairs += [("terminal", terminal.group(1), ""), ("points", terminal.group(2), "")]
        emit("brand", r"(?:品牌甲方指定|甲方指定品牌)(.+)$")
        emit("retention", r"录像存\s*("+_NUMBER+r"\s*天)")
        emit("tray_spec", r"桥架(?:规格)?(?:是|为)?\s*("+_NUMBER+r"\s*[×xX]\s*"+_NUMBER+r")")
        emit("separation", r"跟强电桥架间距按\s*("+_NUMBER+r")")
        emit("fire_link", r"^(火灾时.+)$")
        emit("quantity_source", r"按点表\s*("+_CODE+r")")
        emit("ups", r"^(后备\s*"+_NUMBER+r"\s*分钟)$")
        if "数量都是按" in text:
            pairs += [("_all", "systems", ""), ("quantity_source", text.split("数量都是按", 1)[1], "")]
        reference = re.match(r"(监控|门禁)(?:走的|那边)", text)
        if reference:
            pairs.insert(0, ("_reference", reference.group(1), ""))
    elif post == "civil-defense":
        unit = re.search(r"(?:([A-Z]区))?(FH-\d+)(?:防护单元)?", text)
        if unit:
            pairs.append(("unit_id", unit.group(2), ""))
            if unit.group(1):
                pairs.append(("region", unit.group(1), ""))
        emit("project", r"^(.+?)(?=地块地下|地下[一二三四]层人防)")
        emit("peacetime", r"平时(?:做|是)?(.+)$")
        emit("wartime", r"(?:战时(?:是|做)?|FH-\d+(?:是|做))(.*?(?:掩蔽所|物资库))")
        emit("grade", r"(?:抗力级别是|甲方批(?:复的|的)?)(核\w+级常\w+级)")
        numeric("area", r"(?:建筑面积|^)" )
        filter_model = re.search(r"过滤吸收器甲方指定("+_CODE+r")型", text)
        if filter_model:
            pairs += [("equipment", "过滤吸收器", ""), ("model", filter_model.group(1), ""), ("source", "甲方指定（未附批准资料）", "")]
        emit("drawing", r"图号先挂\s*([^\s，；]+)")
        emit("diesel", r"^(预留移动电站接口)$")
        mouth = re.search(r"(进风口部)(扩散室)?看图\s*("+_CODE+r")", text)
        if mouth:
            pairs += [("mouth", mouth.group(1), ""), ("diffusion", mouth.group(2) or UNKNOWN, ""), ("drawing", mouth.group(3), "")]
        numeric("quantity", "^", r"\s*樘")
        reference = re.search(r"([A-Z]区)(?:战时|的)", text)
        if reference:
            pairs.insert(0, ("_reference", reference.group(1), ""))
        water = re.search(r"战时饮用水箱.+?报的("+_NUMBER+r"\s*吨)", text)
        if water:
            pairs.append(("water", "战时饮用水箱："+water.group(1)+"（给排水用户提资，未核验）", ""))
    elif post == "hydraulic":
        structure = re.search(r"一个是(.+?闸)(?=拆除|重建|$)|另一个是(?:闸下游的)?(.+?护岸)(?=$|[，；])", text)
        if structure:
            name = structure.group(1) or structure.group(2)
            pairs += [("structure", name, "水闸" if structure.group(1) else "护岸段"), ("structure_type", "水闸" if structure.group(1) else "护岸", "")]
        reach = re.search(r"堤段桩号(.+)$", text)
        if reach:
            pairs += [("structure", reach.group(1), "堤段"), ("location", reach.group(1), "")]
        numeric("head", "扬程")
        numeric("water_level", r"(?:外江|闸上)?(?:设计洪水位|设计水位|水位)(?:按)?")
        numeric("flow", r"(?:设计流量|过闸流量)", r"(?:m[³3]/s|方每秒)?")
        emit("datum", r"(\d{4}国家高程基准|吴淞高程)")
        emit("hydro_report", r"水文(?:用的是|数是从)(.+)$")
        emit("geology_report", r"地勘报告编号\s*([A-Za-z0-9-]+)")
        emit("borehole", r"(?:地勘孔|勘探孔|一个孔)\s*(ZK\d+)")
        emit("soil_layer", r"(?:揭示堤基有|说是)(.+?)(?:层)?$")
        numeric("groundwater", r"地下水位(?:埋深)?")
        emit("fill", r"筑堤土料(?:打算用|用)(.+)$")
    if _UNKNOWN.search(text):
        for label in sorted(_ALIASES, key=len, reverse=True):
            key = _ALIASES[label]
            if key in _NUMERIC and text.startswith(label):
                remainder = text[len(label):].lstrip()
                if _UNKNOWN.match(remainder):
                    pairs.append((key, remainder, label))
                    break
        pairs.append(("pending", text, ""))
    return pairs


def parse(text, post):
    if post == "intel-weak":
        text = _weak_input(text)
    groups = {**_GROUPS[post], "interfaces": _INTERFACE}
    anchors = {key: kind for kind, (keys, _) in groups.items() for key in keys}
    data = _Input()
    scope = {"jurisdiction": UNKNOWN}
    data.contexts.append(scope)
    active, active_kind, seen = None, None, set()
    parents, children = {}, {}
    file_id, origin = 0, ""
    last_anchor_line = None

    def context_id():
        return next(i for i, row in enumerate(data.contexts) if row is scope)

    def new(kind):
        nonlocal active, active_kind, seen
        active = {"_context": context_id(), "_file": file_id, "_origin": origin}
        active_kind, seen = kind, set()
        data.records.setdefault(kind, []).append(active)
        primary = _PRIMARY[post]
        if kind == primary:
            parents.clear()
            children.clear()
            parents[primary] = active
        elif primary in parents:
            parent = parents[primary]
            identity = next((parent[k] for k in _FIELDS if k in groups[primary][0] and parent.get(k) not in {None, UNKNOWN}), UNKNOWN)
            ref = {"landscape": "object_ref", "interior": "object_ref", "intel-weak": "system_ref", "civil-defense": "unit_ref", "hydraulic": "object_ref"}[post]
            if identity != UNKNOWN and kind != "interfaces":
                active[ref] = identity
                active["_inferred_ref"] = ref
            active["_parent"] = parent
        if kind != primary:
            children[kind] = active
        return active

    def put(target, key, raw, position):
        if post == "intel-weak" and _sensitive(raw):
            raw = _redact_ipv4_text(raw)
            if raw == "[敏感内容已省略]" or not raw.strip():
                raw = UNKNOWN
        value = _typed(key, raw)
        parent = target.get("_parent")
        if key in {"object_ref", "system_ref", "unit_ref"} and parent is not None:
            if value not in {parent.get(k) for k in groups[_PRIMARY[post]][0]}:
                # An explicitly different (or missing) owner cancels the local
                # parent association; it must not appear under that parent.
                target.pop("_parent", None)
        if target.get("_inferred_ref") == key:
            target.pop(key, None)
            target.pop("_inferred_ref", None)
        if value == UNKNOWN:
            previous = target.get(key)
            if previous not in {None, UNKNOWN}:
                target["pending"] = "；".join(filter(None, (target.get("pending"), _FIELDS[key][0]+"原用户值："+previous+"；本次未定，待核")))
            target[key] = UNKNOWN
        elif target.get(key) in {None, UNKNOWN}:
            target[key] = value
        elif value != UNKNOWN and value not in target[key].split("；"):
            target[key] += "；"+value
        if value != UNKNOWN:
            target.setdefault("_sources", {})[key] = position

    def source_inputs():
        header, offset = None, 0
        for line in _literal_source(text).splitlines(keepends=True):
            stripped = line.strip()
            if _source_name(stripped) is not None or stripped.startswith(("【本轮用户要求", "【此前用户资料")):
                header = None
                yield "marker", stripped, offset
            elif stripped.startswith("|"):
                cells = [html.unescape(c).strip() for c in _table_cells(stripped)]
                if any(cells) and all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                    pass
                elif header is None:
                    header = cells
                elif any(c.casefold() in _ALIASES for c in header):
                    yield "row", [(k, cells[i] if i < len(cells) else "") for i, k in enumerate(header)], offset
                elif cells and cells[0].casefold() in _ALIASES and len(cells) >= 2 and cells[1] and not any(cells[2:]):
                    yield "pairs", [(_ALIASES[cells[0].casefold()], cells[1], cells[0])], offset
            else:
                if header is not None:
                    yield "end_table", [], offset
                    header = None
                for clause, _, relative, _ in _inputs(line):
                    if clause is not None:
                        yield "pairs", _scan(clause, post), offset+relative
            offset += len(line)

    for kind, content, offset in source_inputs():
        position = (origin+" · " if origin else "")+f"输入第{text[:offset].count(chr(10))+1}行（非原件页码）"
        if kind == "marker":
            origin = _source_name(content) or content.strip("【】")
            if post == "intel-weak" and _sensitive(origin):
                origin = _redact_ipv4_text(origin) or "敏感文件名已省略"
            file_id += 1
            scope = {"jurisdiction": UNKNOWN, "_origin": origin}
            data.contexts.append(scope)
            active, active_kind, seen, parents, children = None, None, set(), {}, {}
            continue
        if kind == "end_table":
            active, active_kind, seen, parents, children = None, None, set(), {}, {}
            continue
        pairs = ([(k, v, label) for label, value in content if label.casefold() in _ALIASES
                  for k, v in _details(_ALIASES[label.casefold()], value, post)] if kind == "row" else content)
        if kind == "row":
            # Metadata cells apply to this row regardless of column order.
            order = {"project": 0, "stage": 1, "scope": 2}
            pairs.sort(key=lambda p: order.get(p[0], 3))
        if not pairs:
            continue
        if active is None and all(key == "pending" for key, _, _ in pairs):
            # A general "missing fields stay blank" instruction after an
            # attachment is not an additional room, system or water structure.
            continue
        stated = {k: v for k, v, _ in pairs}
        if "_all" in stated:
            target_kind = stated["_all"]
            for row in data.records.get(target_kind, []):
                if row.get("_file") == file_id and row.get("_context") == context_id():
                    for key, value, _ in pairs:
                        if not key.startswith("_"):
                            put(row, key, value, position)
            continue
        if "_reference" in stated:
            primary = _PRIMARY[post]
            query = stated["_reference"]
            candidates = [row for row in data.records.get(primary, []) if row.get("_file") == file_id and row.get("_context") == context_id()
                          and any(query in row.get(k, "") for k in {*groups[primary][0], "region"})]
            if len(candidates) == 1:
                active, active_kind, seen = candidates[0], primary, set(groups[primary][0] & candidates[0].keys())
                scope = data.contexts[active["_context"]]
                parents, children = {primary: active}, {}
            else:
                active, active_kind, seen, parents, children = None, None, set(), {}, {}
        if kind == "row":
            active, active_kind, seen, parents, children = None, None, set(), {}, {}
        physical_line = text[:offset].count(chr(10))
        first_anchor = physical_line != last_anchor_line
        table_rows = {}
        only_kind = None
        if kind == "row":
            kinds = {anchors[k] for k, _, _ in pairs if k in anchors}
            if len(kinds) == 1:
                only_kind = next(iter(kinds))
        for key, value, label in pairs:
            if key.startswith("_"):
                continue
            if key == "jurisdiction" and (active is not None or only_kind is not None) and label not in {"项目辖区", "总体辖区"}:
                key = "record_zone"
            if key == "basis" and kind == "row" and stated.get("jurisdiction", stated.get("record_zone")) in {"CN", "SG", "EU"}:
                key = "basis_"+stated.get("jurisdiction", stated.get("record_zone")).lower()
            object_basis = kind == "row" and only_kind is not None and key in {"basis", "basis_cn", "basis_sg", "basis_eu"}
            if key in _GLOBAL and not object_basis:
                if key in {"project", "stage", "scope"} and (scope.get(key) not in {None, UNKNOWN, _typed(key, value)}
                        or scope.get(key) in {None, UNKNOWN} and active is not None):
                    scope = ({"jurisdiction": UNKNOWN, "_origin": origin} if key == "project" else {**scope, "_sources": dict(scope.get("_sources", {}))})
                    data.contexts.append(scope)
                    active, active_kind, seen, parents, children = None, None, set(), {}, {}
                put(scope, key, value, position)
                continue
            if only_kind is not None and not table_rows:
                # Quantity/source columns before the anchor still belong to
                # the declared object, including when its name cell is empty.
                table_rows[only_kind] = new(only_kind)
            target_kind = anchors.get(key)
            if target_kind:
                if kind == "row":
                    if target_kind not in table_rows:
                        table_rows[target_kind] = new(target_kind)
                    active, active_kind = table_rows[target_kind], target_kind
                elif active_kind != target_kind or key in seen or first_anchor and seen:
                    new(target_kind)
                elif active is None:
                    new(target_kind)
                seen.add(key)
                first_anchor = False
                last_anchor_line = physical_line
            elif key in _SHARED or object_basis:
                if active is None:
                    new(_PRIMARY[post])
            else:
                target_kind = active_kind if active_kind and key in groups[active_kind][1] else next((name for name, (_, keys) in groups.items() if key in keys), None)
                if target_kind is None:
                    continue
                if active_kind != target_kind:
                    if target_kind in children:
                        active, active_kind = children[target_kind], target_kind
                    elif target_kind in parents:
                        active, active_kind = parents[target_kind], target_kind
                    else:
                        new(target_kind)
                    seen = set(groups[target_kind][0] & active.keys())
            if key == "structure" and label in {"堤段", "护岸段", "水闸", "泵站"}:
                active.setdefault("structure_type", label)
            put(active, key, value, position)
    meaningful = [c for c in data.contexts if any(not k.startswith("_") and (k != "jurisdiction" or v != UNKNOWN) for k, v in c.items())]
    data.context = meaningful[0] if meaningful else {}
    return data
