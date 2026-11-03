"""Architecture, structure, geotechnical and facade drafts from explicit facts.

Pure renderers: no design calculations, report parsing claims, approval or I/O.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
import html
import re

from packing_assistant.jurisdiction import infer_jurisdiction
from packing_assistant.post_drafts.design_basic_observations import aliases_for, parameter_value, statements
from packing_assistant.post_drafts.design_observations import MISSING as OBSERVATION_MISSING

DISCLAIMER = (
    "本文件由 Civil Buddy 根据用户输入生成，仅供内部讨论与起草。"
    "不构成设计文件、法定专项施工方案、交底签认件、监理指令、专家论证材料或开工/竣工验收依据。"
)
MISSING = "[A001] 待填"
NOT_FOUND = "未在原文检出"
_TOOLS = {"architecture": "architecture__memo", "structure": "structure__calc_outline", "geotech": "geotech__brief", "facade": "facade__brief"}
_FIELDS = {
    "project": ("项目名称", "工程名称", "项目"), "jurisdiction": ("辖区",),
    "brief": ("设计任务书", "任务书"), "report": ("勘察报告", "地勘报告", "SI报告", "SI 报告"),
    "architectural_drawings": ("建筑图文件",), "load_files": ("荷载表文件",), "drawings": ("图号清单", "图纸清单"),
    "basis": ("设计依据", "规范依据", "依据"),
    "cn_basis": ("CN依据", "CN标准"), "sg_basis": ("SG依据", "SG标准"), "eu_basis": ("EU依据", "EU标准"),
    "review": ("审查接口",), "cn_review": ("CN审查接口",), "sg_review": ("SG审查接口",), "eu_review": ("EU审查接口",),
    "object": ("单体名称", "建筑名称", "单体"), "object_ref": ("关联单体",), "location": ("部位", "位置"),
    "stage": ("勘察阶段", "设计阶段", "阶段"), "source": ("来源文件", "参数来源", "来源"),
    "area": ("建筑面积", "面积"), "site_area": ("用地面积",), "floors": ("地上层数", "层数"),
    "basements": ("地下室层数",), "height": ("建筑高度", "高度"), "function": ("功能业态", "建筑用途", "建筑功能", "功能", "用途"),
    "fire_rating": ("耐火等级",), "entrance": ("出入口",), "fire_lane": ("消防车道",),
    "fire_site": ("登高操作场地",), "road_link": ("市政衔接",),
    "space": ("分区名称", "空间名称"), "circulation": ("流线",), "toilets": ("卫生间",),
    "plant": ("设备用房", "机房"), "shaft": ("竖井",), "fire_class": ("建筑分类",),
    "fire_compartment": ("防火分区",), "fire_area": ("防火分区面积",),
    "exit_count": ("安全出口数量",), "egress_width": ("疏散宽度",),
    "level_difference": ("场地高差",), "ramp": ("无障碍坡道", "坡道"), "accessible_lift": ("无障碍电梯",),
    "accessible_toilet": ("无障碍卫生间",), "signage": ("无障碍标识",),
    "storey_height": ("层高",), "clear_height": ("室内净高", "净高"), "stairs": ("楼梯",),
    "lifts": ("电梯",), "roof": ("屋面",), "basement_access": ("地下室出入口",),
    "envelope": ("围护要求",), "thermal": ("传热系数",), "shading": ("遮阳系数",),
    "energy_report": ("节能计算书", "节能报告"), "green": ("绿色建筑要求",),
    "drawing_id": ("图号",), "drawing_name": ("图名",), "revision": ("图纸版本", "版本"),
    "legend": ("图例要求",), "title_block": ("图签要求",),
    "system": ("结构体系",), "seismic_target": ("设防目标",), "seismic_grade": ("抗震等级",),
    "seismic_parameter": ("地震参数",), "weakness": ("薄弱部位",),
    "component": ("构件编号", "构件名称", "构件"), "span": ("跨度",), "section": ("截面",),
    "dead_load": ("恒荷载", "恒载"), "live_load": ("活荷载", "活载"), "wind_load": ("风荷载",),
    "load_level": ("荷载水平",),
    "snow_load": ("雪荷载",), "seismic_load": ("地震作用",), "combination": ("荷载组合",),
    "concrete": ("混凝土强度", "混凝土等级"), "rebar": ("钢筋强度", "钢筋等级"), "steel": ("钢材强度", "钢材等级"),
    "bearing_layer": ("持力层",), "foundation": ("基础型式", "基础形式"), "depth": ("基础埋深",),
    "bearing": ("承载力", "承载力特征值"), "calculation": ("计算书",),
    "topography": ("地形地貌",), "adverse": ("不良地质",), "water": ("地下水位", "水位"),
    "hole": ("钻孔编号", "孔号"), "hole_depth": ("孔深",), "spacing": ("孔距",),
    "in_situ": ("原位测试",), "laboratory": ("室内试验",), "layer": ("分层编号", "层号"),
    "soil": ("岩土名称", "地层名称", "土层"), "layer_depth": ("层深", "层底深度"),
    "cohesion": ("黏聚力", "粘聚力", "c"), "friction": ("内摩擦角", "φ", "phi"),
    "modulus": ("压缩模量",), "pile_friction": ("桩侧摩阻",),
    "uniformity": ("均匀性资料",), "liquefaction": ("液化资料",), "corrosion": ("腐蚀性资料",),
    "slope": ("边坡资料",), "pit": ("基坑资料",), "recommendation": ("用户方案结论", "报告建议"),
    "settlement_record": ("沉降记录",), "water_record": ("水位记录",), "displacement_record": ("支护位移记录",),
    "facade": ("幕墙编号", "幕墙名称", "幕墙系统名称"), "facade_type": ("幕墙体系",),
    "panel_grid": ("分格",), "wind_pressure": ("风压", "设计风压"), "panel_thickness": ("面板厚度",),
    "mullion": ("龙骨规格",), "embed": ("预埋件",), "structure_interface": ("主体接口",),
    "anchor": ("后置锚栓",), "pullout": ("拉拔检测计划",), "deflection": ("挠度限值",),
    "air": ("气密要求", "气密性能"), "watertight": ("水密要求", "水密性能"),
    "movement": ("层间变位",), "fire_stop": ("防火封堵",), "fire_glass": ("防火玻璃范围",),
    "lightning": ("防雷接口",), "condensate": ("冷凝水措施",),
    "opening": ("开启扇",), "limiter": ("限制器",), "cleaning": ("清洗维护",), "track": ("擦窗机轨道",),
    "shop_drawings": ("加工图",), "material_report": ("材料检测", "材料质保资料"),
    "performance_report": ("性能检测", "三性检测"), "acceptance": ("验收资料", "验收记录"),
    "buffer": ("下方防护", "缓冲防护"), "light_transmission": ("可见光指标",),
    "interface_id": ("接口编号",), "information": ("提资项", "提资内容"),
    "provider": ("提资方",), "receiver": ("接收专业",), "deadline": ("截止日期",),
    "status": ("用户状态", "状态"),
}
_ALIASES = {alias.casefold(): key for key, aliases in _FIELDS.items() for alias in aliases}
_LABEL = "|".join(re.escape(alias) for alias in sorted(_ALIASES, key=len, reverse=True))
_FIELD_RE = re.compile(r"(?<![\w])(" + _LABEL + r")\s*[:：=]\s*", re.I)
_EMPTY = re.compile(r"(?:\[A\d+\]\s*)?(?:待填|待核|未知|未提供|UNSPECIFIED|TBD|N/A)", re.I)
_GLOBAL = {"project", "brief", "report", "architectural_drawings", "load_files", "drawings", "basis", "review", "cn_basis", "sg_basis", "eu_basis", "cn_review", "sg_review", "eu_review"}
_PRIORITY = ("layers", "components", "facades", "spaces", "interfaces", "drawings", "holes", "objects")
_FIELD_DELIMITER = re.compile(r"&(?:#[0-9]+|#x[0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);|[;；]")


def _cell(value: object) -> str:
    return html.escape(str(value).replace("\r", " ").replace("\n", "；").strip(), quote=False).replace("|", "&#124;")


def _table(headers: Iterable[str], rows: Iterable[Iterable[object]]) -> str:
    columns = tuple(headers)
    return "\n".join(["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |",
        *("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows)])


def _value(row: dict[str, str], key: str, missing: str = MISSING) -> str:
    return row.get(key) or missing


def _field_value(raw: str) -> str:
    for match in _FIELD_DELIMITER.finditer(raw):
        if match.group() in {";", "；"}:
            raw = raw[:match.start()]
            break
    return raw.strip(" ,，")


@dataclass
class _Input:
    context: dict[str, str] = field(default_factory=dict)
    records: dict[str, list[dict[str, str]]] = field(default_factory=dict)

    def rows(self, kind: str) -> list[dict[str, str]]:
        return self.records.get(kind) or [self.context]


def _kind(key: str | None, post: str) -> str | None:
    if key in {"object", "drawing_id", "interface_id", "information"}:
        return {"object": "objects", "drawing_id": "drawings", "interface_id": "interfaces", "information": "interfaces"}[key]
    if post == "architecture" and key == "space":
        return "spaces"
    if post == "structure" and key == "component":
        return "components"
    if post == "geotech":
        return "holes" if key == "hole" else "layers" if key in {"layer", "soil"} else None
    if post == "facade" and key == "facade":
        return "facades"
    return None


def _record_pairs(pairs: list[tuple[str, str]], post: str) -> list[tuple[str, str]]:
    """Move suffix identifiers before their own adjacent fields, never a sibling's."""
    anchors = [index for index, (key, _) in enumerate(pairs) if _kind(key, post)]
    if not anchors or anchors[0] == 0:
        return pairs
    first = anchors[0]
    kinds = {_kind(pairs[index][0], post) for index in anchors}
    if len(anchors) == 1 or len(kinds) > 1:
        return [pairs[first], *pairs[:first], *pairs[first + 1:]]
    ordered = []
    start = 0
    for end in anchors:
        ordered.extend([pairs[end], *pairs[start:end]])
        start = end + 1
    return ordered + pairs[start:]


def _parse(text: str, post: str) -> _Input:
    data = _Input()
    aliases = aliases_for(post, _ALIASES)
    labels = "|".join(re.escape(alias) for alias in sorted(aliases, key=len, reverse=True))
    field_re = re.compile(r"(?<![\w])(" + labels + r")\s*[:：=]\s*", re.I)
    active, active_kind = data.context, None
    object_name, hole_name = "", ""
    object_scope, hole_scope = "UNSPECIFIED", "UNSPECIFIED"
    fresh_object_kinds: set[str] = set()
    fresh_hole = False
    unassigned_objects: dict[int, dict[str, str]] = {}
    preamble: list[str] = []

    def add(key: str, value: str, *, revisit: bool = False, narrative: bool = False, unscoped: bool = False) -> None:
        nonlocal active, active_kind, object_name, hole_name, object_scope, hole_scope, fresh_object_kinds, fresh_hole
        kind = _kind(key, post)
        primary = key not in {"soil", "information"}
        if kind and (primary or kind != active_kind or key in active):
            child = kind not in {"objects", "holes"}
            # A freshly named parent introduces its own children, including
            # codes also used in another building/hole. Later bare references
            # may revisit a globally unique child, never just the last parent.
            scoped = child and not unscoped and (fresh_hole if kind == "layers" else kind in fresh_object_kinds)
            candidates = [row for row in data.records.get(kind, []) if row.get(key) == value
                          and (not scoped or (row.get("hole_ref", "") == hole_name if kind == "layers"
                                              else row.get("object_ref", "") == object_name))] if revisit and value else []
            ambiguous = child and (unscoped or len(candidates) > 1)
            active_kind = kind
            if len(candidates) == 1 and not unscoped:
                active = candidates[0]
                if child:
                    object_name = active.get("object_ref", "")
                    parents = [row for row in data.records.get("objects", []) if row.get("object") == object_name]
                    object_scope = (parents[0].get("jurisdiction") or parents[0].get("_jurisdiction", "UNSPECIFIED")) if len(parents) == 1 else active.get("jurisdiction") or active.get("_jurisdiction", "UNSPECIFIED")
                    if not scoped:
                        fresh_object_kinds.clear()
                    if kind == "layers":
                        hole_name = active.get("hole_ref", "")
                        holes = [row for row in data.records.get("holes", []) if row.get("hole") == hole_name]
                        hole_scope = (holes[0].get("jurisdiction") or holes[0].get("_jurisdiction", "UNSPECIFIED")) if len(holes) == 1 else active.get("jurisdiction") or active.get("_jurisdiction", "UNSPECIFIED")
            else:
                active = {}
                data.records.setdefault(kind, []).append(active)
                if ambiguous:
                    object_name, hole_name = "", ""
                    object_scope, hole_scope = "UNSPECIFIED", "UNSPECIFIED"
                    fresh_object_kinds.clear()
            scope = data.context.get("jurisdiction", "UNSPECIFIED")
            if kind != "objects" and object_name:
                scope = object_scope
            if kind == "layers" and hole_name:
                scope = hole_scope
            active.setdefault("_jurisdiction", "UNSPECIFIED" if ambiguous else scope)
            if kind not in {"objects", "holes"} and object_name:
                active.setdefault("object_ref", object_name)
            if kind == "layers" and hole_name:
                active["hole_ref"] = hole_name
            if child:
                fresh_object_kinds.discard(kind)
                if kind == "layers":
                    fresh_hole = False
        value = parameter_value(key, value).strip()
        empty = not value or bool(_EMPTY.fullmatch(value))
        if key == "object":
            object_name = "" if empty else value
            hole_name = ""
            object_scope = active.get("jurisdiction") or active["_jurisdiction"]
            hole_scope = "UNSPECIFIED"
            fresh_object_kinds = {"components", "facades", "spaces", "interfaces", "drawings", "layers"} if object_name else set()
            fresh_hole = False
        elif key == "hole":
            hole_name = "" if empty else value
            hole_scope = active.get("jurisdiction") or active["_jurisdiction"]
            fresh_hole = bool(hole_name)
        elif key == "jurisdiction":
            # A local explicit unknown also overrides a global jurisdiction.
            scope = "UNSPECIFIED" if empty else value
            active["jurisdiction"] = scope
            if active_kind == "objects":
                object_scope = scope
            elif active_kind == "holes":
                hole_scope = scope
            return
        if empty:
            return
        target = data.context if key in _GLOBAL else active
        object_fields = {"foundation", "bearing_layer", "bearing", "depth", "system", "seismic_target", "seismic_parameter", "seismic_grade", "floors", "height"} if post == "structure" else {
            "area", "height", "floors", "function", "fire_rating", "entrance", "fire_lane", "fire_site", "road_link", "storey_height", "clear_height",
            "accessible_lift", "accessible_toilet", "ramp", "signage", "roof", "basement_access", "envelope", "thermal", "shading", "energy_report", "green",
        } if post == "architecture" else set()
        if post == "geotech":
            object_fields = {"recommendation", "foundation", "stage", "load_level", "basements"}
        if key in object_fields and active_kind not in {None, "objects", "spaces"}:
            parents = [row for row in data.records.get("objects", []) if row.get("object", "") == object_name]
            if len(parents) == 1:
                target = parents[0]
            elif not data.records.get("objects"):
                target = data.context
            else:
                # Preserve an explicit unit-level fact even when no parent
                # can be identified, without putting it on a member or a
                # different building. Its blank identity is visible in body.
                if id(active) not in unassigned_objects:
                    unassigned_objects[id(active)] = {"_jurisdiction": "UNSPECIFIED"}
                    data.records["objects"].append(unassigned_objects[id(active)])
                target = unassigned_objects[id(active)]
        if narrative and post == "geotech" and key == "water" and active_kind == "layers":
            parents = [row for row in data.records.get("holes", []) if row.get("hole", "") == hole_name]
            if len(parents) == 1:
                target = parents[0]
        if key in target and target[key] != value:
            target[key] += "；" + value
        else:
            target[key] = value

    lines, index = text.splitlines(), 0
    narrative_checked_until = 0
    while index < len(lines):
        if not lines[index].lstrip().startswith("|") and index >= narrative_checked_until:
            end = index + 1
            while end < len(lines) and not lines[end].lstrip().startswith("|"):
                end += 1
            narrative_checked_until = end
            observations = statements(post, "\n".join(lines[index:end]), aliases)
            if observations is not None:
                for source, events in observations:
                    if not data.records:
                        first_anchor = next((event.start for event in events if _kind(event.key, post)), None)
                        # Only a pre-object statement may establish a default.
                        if first_anchor is None:
                            zone = infer_jurisdiction(source)
                            if "jurisdiction" not in data.context and zone != "UNSPECIFIED":
                                data.context["jurisdiction"] = zone
                    if post == "facade" and not any(event.key == "facade" for event in events):
                        mentioned = re.findall(r"单元式|构件式|全玻", source)
                        owners = [row for row in data.records.get("facades", []) if any(value in row.get("facade_type", "") for value in mentioned)]
                        if len(owners) == 1:
                            active, active_kind = owners[0], "facades"
                            object_name = active.get("object_ref", "")
                            object_scope = active.get("jurisdiction") or active.get("_jurisdiction", "UNSPECIFIED")
                            fresh_object_kinds.clear()
                        elif len(owners) > 1:
                            add("facade", "", narrative=True, unscoped=True)
                    pairs = [(event.key, "" if event.value == OBSERVATION_MISSING else event.value) for event in events]
                    for key, value in _record_pairs(pairs, post):
                        labelled = any(event.key == key and event.value == value and event.labelled for event in events)
                        unscoped = bool(re.search(r"没说是哪栋|没说是哪个|未说明(?:关联)?单体|单体(?:未定|未知)", source))
                        add(key, value, revisit=not labelled, narrative=True, unscoped=unscoped)
                index = end
                continue
        if lines[index].lstrip().startswith("|"):
            block = []
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                interior = lines[index].strip()[1:]
                if interior.endswith("|"):
                    interior = interior[:-1]
                # Decode adapter entities after splitting, exactly once.
                block.append([html.unescape(cell.strip()) for cell in interior.split("|")])
                index += 1
            headers = [aliases.get(cell.casefold()) for cell in block[0]]
            is_table = len(block) > 1 and all(re.fullmatch(r":?-{3,}:?", cell) for cell in block[1])
            if is_table and any(headers):
                kinds = {_kind(key, post) for key in headers}
                kind = next((kind for kind in _PRIORITY if kind in kinds), {"architecture": "objects", "structure": "components", "geotech": "layers", "facade": "facades"}[post])
                for values in block[2:]:
                    row = {key: parameter_value(key, value) for key, value in zip(headers, values) if key and parameter_value(key, value) and not _EMPTY.fullmatch(value)}
                    row["_jurisdiction"] = data.context.get("jurisdiction", "UNSPECIFIED")
                    if "jurisdiction" in headers:
                        local_scope = values[headers.index("jurisdiction")] if headers.index("jurisdiction") < len(values) else ""
                        row["jurisdiction"] = "UNSPECIFIED" if not local_scope or _EMPTY.fullmatch(local_scope) else local_scope
                    if kind != "objects" and "object" in row:
                        row["object_ref"] = row.pop("object")
                    if kind == "layers" and "hole" in row:
                        row["hole_ref"] = row.pop("hole")
                    data.records.setdefault(kind, []).append(row)
                active, active_kind = data.context, None
                object_name, hole_name = "", ""
                object_scope, hole_scope = "UNSPECIFIED", "UNSPECIFIED"
                fresh_object_kinds.clear()
                fresh_hole = False
            else:
                for cells in block:
                    if len(cells) > 1 and (key := aliases.get(cells[0].casefold())):
                        add(key, cells[1])
            continue
        matches = list(field_re.finditer(lines[index]))
        if not data.records:
            first_anchor = next((match.start() for match in matches if _kind(aliases[match.group(1).casefold()], post)), len(lines[index]))
            preamble.append(lines[index][:first_anchor])
            zone = infer_jurisdiction("\n".join(preamble))
            if "jurisdiction" not in data.context and zone != "UNSPECIFIED":
                data.context["jurisdiction"] = zone
        pairs = []
        for position, match in enumerate(matches):
            end = matches[position + 1].start() if position + 1 < len(matches) else len(lines[index])
            value = _field_value(lines[index][match.end():end])
            pairs.append((aliases[match.group(1).casefold()], value))
        for key, value in _record_pairs(pairs, post):
            add(key, value)
        index += 1
    return data


def _header(title: str, text: str) -> list[str]:
    return [f"# {title}（AI 草稿 · 内部讨论）", "", DISCLAIMER, "",
        f"- 辖区：{infer_jurisdiction(text)}", "- submit_blocked=true", "",
        "本轮只整理用户明确输入；未执行图纸解析、勘察报告原件核验、工程计算、检测或审查。参数、方案和记录均待专业人员核对。", ""]


def _rows_table(rows: list[dict[str, str]], specs: tuple[tuple[str, str], ...], *, missing: str = MISSING, status: str = "用户输入待核") -> str:
    return _table((*[label for label, _ in specs], "本轮状态", "对象辖区"), [
        (*(_value(row, key, missing) for _, key in specs), status,
         infer_jurisdiction(row.get("jurisdiction") or row.get("_jurisdiction", ""))) for row in rows])


def _register(data: _Input) -> str:
    return _table(("资料项", "用户提供 / 缺口"), [
        (label, _value(data.context, key)) for label, key in (
            ("工程名称", "project"), ("设计任务书", "brief"), ("勘察报告", "report"),
            ("建筑图文件", "architectural_drawings"), ("荷载表文件", "load_files"),
        )])


def _basis(data: _Input, text: str) -> str:
    zone, values = infer_jurisdiction(text), data.context
    if zone == "DUAL":
        return _table(("核对项", "CN 独立栏", "SG 独立栏", "EU / 其他辖区待定栏"), [
            ("用户指定依据", _value(values, "cn_basis", "UNSPECIFIED"), _value(values, "sg_basis", "UNSPECIFIED"), _value(values, "eu_basis", "UNSPECIFIED")),
            ("用户审查接口", _value(values, "cn_review", "UNSPECIFIED"), _value(values, "sg_review", "UNSPECIFIED"), _value(values, "eu_review", "UNSPECIFIED")),
            ("已核版本 / 条款", "UNSPECIFIED", "UNSPECIFIED", "UNSPECIFIED"),
            ("适用性 / 审查状态", "unverified", "unverified", "unverified"),
        ]) + "\n\n" + _table(("未归属资料", "用户输入"), [
            ("通用依据（不自动分配辖区）", _value(values, "basis")),
            ("通用审查接口（待明确辖区）", _value(values, "review")),
        ])
    prefix = zone.lower()
    return _table(("辖区", "用户指定依据", "已核版本", "已核条款", "用户审查接口", "核验"), [
        (zone, values.get(f"{prefix}_basis") or _value(values, "basis", "UNSPECIFIED"), "UNSPECIFIED", "UNSPECIFIED",
         values.get(f"{prefix}_review") or _value(values, "review", "UNSPECIFIED"), "unverified")])


def _interfaces(data: _Input, defaults: tuple[tuple[str, str], ...]) -> str:
    explicit = _rows_table(data.rows("interfaces"), (("接口编号", "interface_id"), ("关联单体", "object_ref"), ("提资方", "provider"), ("接收专业", "receiver"), ("提资内容", "information"), ("截止日期", "deadline")))
    return explicit + "\n\n" + _table(("专业接口待核项", "需取得资料", "确认状态"), [(label, item, "UNSPECIFIED") for label, item in defaults])


def _qa(items: tuple[tuple[str, str], ...]) -> str:
    return _table(("关键缺项 / 复核项", "所需资料或动作", "本轮结论"), [(name, action, "未核验") for name, action in items])


def _detail_rows(data: _Input, kind: str) -> list[dict[str, str]]:
    """Single-building facts still reach their section without inventing a member."""
    if data.records.get(kind):
        relevant = {
            "components": {"dead_load", "live_load", "wind_load", "snow_load", "seismic_load", "combination", "concrete", "rebar", "steel", "span", "section"},
            "facades": {"facade_type", "wind_pressure", "panel_thickness", "mullion", "embed", "air", "watertight", "movement"},
        }.get(kind, set())
        general = [{**row, "object_ref": row.get("object", "")} for row in data.records.get("objects", []) if relevant.intersection(row)]
        return general + data.records[kind]
    if data.records.get("objects"):
        return [{**row, "object_ref": row.get("object", "")} for row in data.records["objects"]]
    return [data.context]


def _architecture(text: str) -> str:
    data = _parse(text, "architecture")
    objects, spaces = data.rows("objects"), _detail_rows(data, "spaces")
    lines = _header("建筑专业设计说明", text)
    lines += ["## 1 工程概况与设计范围", "", _register(data), "",
        _rows_table(objects, (("单体", "object"), ("用地面积", "site_area"), ("建筑面积", "area"), ("层数", "floors"), ("建筑高度", "height"), ("功能 / 业态", "function"), ("耐火等级", "fire_rating"))), "",
        "## 2 草稿声明", "", DISCLAIMER, "", "不替代施工图审查、消防审查或人防审查。面积和疏散参数仅登记用户值，缺项 [A001]；不推导设计限值。", "",
        "## 3 总平面", "", _rows_table(objects, (("单体", "object"), ("出入口", "entrance"), ("消防车道", "fire_lane"), ("登高操作场地", "fire_site"), ("场地高差", "level_difference"), ("市政衔接", "road_link"))), "",
        "## 4 平面功能", "", _rows_table(spaces, (("关联单体", "object_ref"), ("分区 / 空间", "space"), ("面积", "area"), ("功能", "function"), ("流线", "circulation"), ("卫生间", "toilets"), ("设备用房", "plant"), ("竖井", "shaft"))), "",
        "分区信息不跨单体继承，不从总建筑面积计算分区或设备用房面积。", "",
        "## 5 防火", "", _rows_table(objects, (("单体", "object"), ("建筑分类", "fire_class"), ("耐火等级", "fire_rating"), ("防火分区", "fire_compartment"), ("用户分区面积", "fire_area"), ("用户出口数量", "exit_count"), ("用户疏散宽度", "egress_width"))), "",
        "分区划分与疏散需求交建筑及消防专业复核，出口、宽度和限值不根据层数或用途自动选取。", "",
        "## 6 无障碍", "", _rows_table(objects, (("单体", "object"), ("场地高差", "level_difference"), ("坡道", "ramp"), ("无障碍电梯", "accessible_lift"), ("无障碍卫生间", "accessible_toilet"), ("标识", "signage"))), "",
        "## 7 竖向", "", _rows_table(objects, (("单体", "object"), ("层高", "storey_height"), ("室内净高", "clear_height"), ("楼梯", "stairs"), ("电梯", "lifts"), ("屋面", "roof"), ("地下室出入口", "basement_access"))), "",
        "## 8 节能与绿色", "", _rows_table(objects, (("单体", "object"), ("围护要求", "envelope"), ("用户传热系数", "thermal"), ("用户遮阳系数", "shading"), ("节能计算书", "energy_report"), ("绿色建筑要求", "green"))), "",
        "热工参数的适用性与节能结果待计算书和检测资料核对，本轮未计算。", "",
        "## 9 图则口径", "", _table(("登记项", "用户提供 / 缺口"), [("用户图号清单", _value(data.context, "drawings"))]), "",
        _rows_table(data.rows("drawings"), (("用户图号", "drawing_id"), ("图名", "drawing_name"), ("版本", "revision"), ("图签要求", "title_block"), ("图例要求", "legend"), ("关联单体", "object_ref"))), "",
        "图号仅抄用户提供的标识；本稿不生成索引图号，不声称已查看所列图纸。", "",
        "## 10 专业界面、依据与自检", "",
        _interfaces(data, (("结构", "开洞、荷载用途和建筑条件"), ("机电", "机房、竖井与设备需求"), ("装修", "承重和疏散界面"), ("幕墙", "预埋与立面接口"), ("人防 / 消防", "口部与专篇分工"))), "",
        _qa((("面积与功能", "核对任务书和图纸，不据用途估算面积"), ("疏散与无障碍", "取得适用依据和专业复核记录"), ("图纸与出图签认", "核对用户图号、版本与责任人"))), "", _basis(data, text), "",
        "已核官方网页标题：本轮无。未列未经核实的官方标题或条款。", ""]
    return "\n".join(lines)


def _structure(text: str) -> str:
    data = _parse(text, "structure")
    objects, members = data.rows("objects"), _detail_rows(data, "components")
    lines = _header("结构计算书提纲 / 结构设计说明", text)
    lines += ["## 1 工程概况", "", _register(data), "", _rows_table(objects, (("单体", "object"), ("结构体系", "system"), ("层数", "floors"), ("高度", "height"), ("设防目标", "seismic_target"))), "",
        "## 2 草稿声明", "", DISCLAIMER, "", "本稿是计算步骤与核对清单，不是计算书。持证人员须按实际资料复核签认，本稿不提供承载力、配筋或截面计算结果。", "",
        "## 3 设计依据清单", "", _register(data), "", _basis(data, text), "",
        "文件名仅为用户登记，不代表已读取或核验其内容。", "",
        "## 4 荷载与组合", "", _rows_table(members, (("关联单体", "object_ref"), ("构件 / 部位", "component"), ("功能用途", "function"), ("恒载", "dead_load"), ("活载", "live_load"), ("风荷载", "wind_load"), ("雪荷载", "snow_load"), ("地震作用", "seismic_load"), ("用户组合", "combination"), ("参数来源", "source"))), "",
        "荷载与建筑功能的对应关系待核；不从用途选取荷载值，不组合或相加用户数字。", "",
        "## 5 材料", "", _rows_table(members, (("构件", "component"), ("用户混凝土等级", "concrete"), ("用户钢筋等级", "rebar"), ("用户钢材等级", "steel"), ("来源", "source"))), "",
        "## 6 地基基础", "", _rows_table(objects, (("单体", "object"), ("用户持力层", "bearing_layer"), ("用户基础型式", "foundation"), ("用户埋深", "depth"), ("用户承载力记录", "bearing"), ("记录来源", "source"))), "",
        "没有勘察原文时，承载力适用性和基础结论保持 UNSPECIFIED；用户摘录不代替岩土报告。", "",
        "## 7 计算步骤", "", _table(("计算模块", "需补输入", "计算结果"), [
            ("结构模型与边界条件", "体系、支承、荷载与材料", "UNSPECIFIED"), ("周期比与位移角", "模型、工况与适用标准", "UNSPECIFIED"),
            ("轴压比与构件配筋", "内力、材料与截面", "UNSPECIFIED"), ("基础与变形复核", "勘察、荷载与沉降要求", "UNSPECIFIED")]), "",
        "## 8 抗震概念与薄弱部位", "", _rows_table(objects, (("单体", "object"), ("设防目标", "seismic_target"), ("用户抗震等级", "seismic_grade"), ("用户地震参数", "seismic_parameter"), ("用户薄弱部位", "weakness"))), "",
        "规则性、传力路径及薄弱部位措施待专业复核，本稿不选取抗震参数。", "",
        "## 9 构件复核清单", "", _rows_table(members, (("构件", "component"), ("位置", "location"), ("用户跨度", "span"), ("用户截面", "section"), ("用户计算书", "calculation"))), "",
        _qa((("梁、板、柱、墙", "核对跨度、荷载、支承、材料及计算书"), ("楼梯、转换与悬挑", "核对局部传力及构造依据"), ("后锚固与开洞", "取得原结构资料和专项复核记录"))), "",
        "## 10 专业接口与 QA 自检", "", _interfaces(data, (("建筑", "功能、开洞与荷载用途"), ("幕墙 / 钢结构", "预埋、支座与作用传递"), ("岩土 / 基坑", "勘察参数、基础与支护边界"), ("人防", "专项荷载及接口资料"))), "",
        _qa((("资料完整性", "勘察、图纸、荷载表与实际单体一致"), ("计算记录", "所有模块结果待计算，不能以提纲代替"), ("签认", "责任人和签字空栏待实际复核"))), "", "签认：（空栏）。", ""]
    return "\n".join(lines)


def _geotech(text: str) -> str:
    data = _parse(text, "geotech")
    holes = data.rows("holes")
    layers = list(data.records.get("layers", []))
    parameter_keys = {"soil", "cohesion", "friction", "modulus", "pile_friction", "bearing"}
    layers += [{**row, "hole_ref": row.get("hole", "")} for row in data.records.get("holes", []) if parameter_keys.intersection(row)]
    if not layers:
        layers = [data.context]
    lines = _header("岩土工程勘察纲要 / 地基方案比选", text)
    lines += ["## 1 任务与阶段", "", _register(data), "", _rows_table(data.rows("objects"), (("拟建单体", "object"), ("勘察阶段", "stage"), ("拟建层数", "floors"), ("地下室层数", "basements"), ("用户荷载水平", "load_level"))), "",
        "## 2 草稿声明", "", DISCLAIMER, "", "本稿不替代正式勘察报告，不构成地基处理或基坑支护施工图。孔号和分层均来自用户明确字段，本轮未解析 SI 原件。", "",
        "## 3 勘察与设计资料衔接", "", _basis(data, text), "", _qa((("报告与场地对应", "核对场地、任务阶段、报告版本和适用单体"), ("后续基础设计", "勘察结论与参数取得后交结构专业计算"))), "",
        "## 4 场地与地下水", "", _rows_table(data.rows("objects"), (("单体 / 场地", "object"), ("地形地貌", "topography"), ("不良地质", "adverse"), ("用户水位记录", "water")), missing=NOT_FOUND), "",
        _rows_table(holes, (("用户孔号", "hole"), ("位置", "location"), ("用户地下水位", "water"), ("记录来源", "source")), missing=NOT_FOUND), "",
        "未给水位不推断干燥场地；单位、测量日期及季节变化待核。", "",
        "## 5 勘探工作量与试验", "", _rows_table(holes, (("用户孔号", "hole"), ("孔深", "hole_depth"), ("孔距", "spacing"), ("原位测试", "in_situ"), ("室内试验", "laboratory"))), "",
        "不生成孔号、孔数、坐标或布孔间距；工作量由勘察单位结合任务确定。", "",
        "## 6 地层与参数摘录", "", _rows_table(layers, (("用户孔号", "hole_ref"), ("用户层号", "layer"), ("岩土名称", "soil"), ("层深", "layer_depth"), ("黏聚力 c", "cohesion"), ("φ", "friction"), ("用户水位", "water"), ("压缩模量", "modulus"), ("桩侧摩阻", "pile_friction"), ("用户承载力记录", "bearing"), ("来源", "source")), missing=NOT_FOUND), "",
        "仅保留孔号与所属分层的显式关联；后一孔或后一层不继承前一行参数。特征值、设计值及适用性均待原报告核对。", "",
        "## 7 地基方案比选", "", _table(("候选方案", "需核条件与资料", "本轮选择"), [("天然地基", "持力层、变形与荷载条件", "UNSPECIFIED"), ("复合地基", "处理目标、场地与试验资料", "UNSPECIFIED"), ("桩基", "土层、桩端桩侧资料及试验", "UNSPECIFIED")]), "",
        _rows_table(data.rows("objects"), (("单体", "object"), ("用户报告建议", "recommendation"), ("用户基础型式", "foundation"), ("来源", "source"))), "",
        "用户已有结论仅作为待核摘录，本轮不推荐最优方案或计算承载力。", "",
        "## 8 场地专项评价资料", "", _rows_table(data.rows("objects"), (("单体", "object"), ("均匀性", "uniformity"), ("液化", "liquefaction"), ("腐蚀性", "corrosion"), ("边坡", "slope"), ("基坑", "pit"))), "",
        "缺资料不改写为无风险。相关参数、评价和支护设计须由相应专业核对。", "",
        "## 9 基础建议与专业提资", "", _interfaces(data, (("结构", "荷载、基础形式、埋深与变形要求"), ("基坑 / 边坡", "场地、水位、周边条件和勘察参数"), ("测量", "孔位及高程原始记录"))), "",
        "## 10 监测、验收与关键缺项", "", _rows_table(data.rows("objects"), (("单体", "object"), ("沉降记录", "settlement_record"), ("水位记录", "water_record"), ("支护位移记录", "displacement_record"), ("用户验收资料", "acceptance"))), "",
        _qa((("缺失参数", "c、φ、水位、模量和承载力逐项核对原文"), ("来源与单位", "孔号、层号、试验方法、单位及参数类型核对"), ("结论与签认", "正式勘察、支护及结构结论由相应专业形成"))), "", "签认：（空栏）。", ""]
    return "\n".join(lines)


def _facade(text: str) -> str:
    data = _parse(text, "facade")
    facades = _detail_rows(data, "facades")
    lines = _header("幕墙工程设计说明", text)
    lines += ["## 1 体系选型", "", _register(data), "", _rows_table(facades, (("幕墙编号 / 名称", "facade"), ("关联单体", "object_ref"), ("位置", "location"), ("用户体系", "facade_type"), ("用户风压", "wind_pressure"), ("层高", "storey_height"), ("分格", "panel_grid"))), "",
        "风压、层高和分格未确认时，只保留体系比选待定；不选定面板厚度或龙骨规格。", "",
        "## 2 草稿声明与依据", "", DISCLAIMER, "", _basis(data, text), "",
        "本稿不代替幕墙或主体结构计算书；记录中的尺寸仅为用户提供值，不是本轮设计选型。", "",
        "## 3 预埋件与主体结构接口", "", _rows_table(facades, (("幕墙", "facade"), ("预埋件", "embed"), ("主体接口", "structure_interface"), ("用户图纸来源", "source"))), "",
        "预埋节点、作用传递和主体承载条件交结构复核，不计算预埋件承载力。", "",
        "## 4 抗风、气密、水密与层间变位", "", _rows_table(facades, (("幕墙", "facade"), ("用户风压", "wind_pressure"), ("用户挠度限值", "deflection"), ("气密要求", "air"), ("水密要求", "watertight"), ("层间变位", "movement"), ("用户面板厚度", "panel_thickness"), ("用户龙骨规格", "mullion"))), "",
        "无计算书则强度、变形和性能满足情况 UNSPECIFIED，不从高度推导风压，也不从风压选取厚度。", "",
        "## 5 防火、防雷与冷凝水", "", _rows_table(facades, (("幕墙", "facade"), ("防火封堵", "fire_stop"), ("防雷接口", "lightning"), ("冷凝水措施", "condensate"))), "",
        "## 6 开启扇与清洗维护", "", _rows_table(facades, (("幕墙", "facade"), ("开启扇", "opening"), ("限制器", "limiter"), ("清洗维护", "cleaning"), ("擦窗机轨道", "track"))), "",
        "## 7 加工图与材料检测", "", _rows_table(facades, (("幕墙", "facade"), ("用户加工图", "shop_drawings"), ("材料检测 / 质保", "material_report"), ("参数来源", "source"))), "",
        "## 8 验收资料", "", _rows_table(facades, (("幕墙", "facade"), ("用户验收资料", "acceptance"), ("用户状态", "status")), status="本轮未执行验收"), "",
        "用户记录不改写为本轮验收结论。", "",
        "## 9 性能与热工核对", "", _rows_table(facades, (("幕墙", "facade"), ("用户风压", "wind_pressure"), ("气密", "air"), ("水密", "watertight"), ("变位", "movement"), ("传热系数", "thermal"), ("遮阳系数", "shading"), ("可见光指标", "light_transmission"), ("性能检测资料", "performance_report"))), "",
        "不自动生成性能等级；热工参数需与建筑节能资料核对。", "",
        "## 10 后置锚固与拉拔检测接口", "", _rows_table(facades, (("幕墙", "facade"), ("用户后置锚栓", "anchor"), ("用户拉拔计划", "pullout"), ("用户计算书", "calculation"))), "",
        "检测方案和主体复核结果待专业确认，不从锚栓名称推导设计承载力。", "",
        "## 11 防火专业接口", "", _rows_table(facades, (("幕墙", "facade"), ("用户封堵记录", "fire_stop"), ("防火玻璃范围", "fire_glass"))), "",
        "层间、窗槛墙和实体墙接口交建筑及消防确认，缺失范围不默认补齐。", "",
        "## 12 防雷专业接口", "", _rows_table(facades, (("幕墙", "facade"), ("用户防雷接口", "lightning"))), "",
        "均压环与接地点须由电气专业提资，本稿不编位置或规格。", "",
        "## 13 总图与下方安全界面", "", _rows_table(facades, (("幕墙", "facade"), ("位置", "location"), ("用户缓冲 / 下方防护", "buffer"))), "",
        "适用防护范围、总图关系和人员通行条件交建筑与相关专业核对。", "",
        "## 14 维护与装修接口", "", _interfaces(data, (("结构", "预埋、后锚固、支座和作用传递"), ("建筑 / 消防", "防火范围、总图和维护界面"), ("电气", "防雷连接条件"), ("装修 / 维护", "开启扇、限制器、擦窗机及检修可达性"))), "",
        "## 15 资料目录与关键缺项", "", _qa((("设计输入", "风压、层高、分格、主体条件与来源"), ("计算与检测", "计算书、性能检测和材料资料"), ("加工图一致性", "节点、预埋、结构图与实际版本核对"), ("签认与验收", "责任人、检测结果和签认记录待实际完成"))), "",
        "签认：（空栏）。装箱运输数值交装箱拼柜岗，本稿不计算物流数量。", ""]
    return "\n".join(lines)


def build_draft(expert_id: str, tool_name: str, text: str) -> str | None:
    if _TOOLS.get(expert_id) != tool_name:
        return None
    return {"architecture": _architecture, "structure": _structure, "geotech": _geotech, "facade": _facade}[expert_id](text or "")
