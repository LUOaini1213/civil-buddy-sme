"""Five design specialties: organize supplied inputs without sizing or approval claims."""
from __future__ import annotations

from dataclasses import dataclass, field
import html

UNKNOWN = "UNSPECIFIED"
DISCLAIMER = (
    "本文件由 Civil Buddy 根据用户输入生成，仅供内部讨论与起草。"
    "不构成设计文件、法定专项施工方案、交底签认件、监理指令、专家论证材料或开工/竣工验收依据。"
)
TOOLS = {
    "landscape": "landscape__memo", "interior": "interior__schedule",
    "intel-weak": "intel-weak__memo", "civil-defense": "civil-defense__brief",
    "hydraulic": "hydraulic__outline",
}
_FIELDS = {
    "project": ("项目名称", "工程名称", "项目"), "jurisdiction": ("项目辖区", "总体辖区", "辖区"),
    "scope": ("设计范围", "本次范围", "范围"), "stage": ("设计阶段", "阶段"),
    "drawings": ("用户图号清单", "图号清单", "图纸清单"), "location": ("作业部位", "位置", "部位"),
    "source": ("资料来源", "选材来源", "数据来源", "来源"), "drawing": ("节点图号", "用户图号", "图号"),
    "notes": ("用户备注", "备注"), "record_zone": ("适用辖区",),
    "basis": ("用户依据", "设计依据", "依据"),
    "basis_cn": ("CN依据", "CN 依据", "中国依据"), "basis_sg": ("SG依据", "SG 依据", "新加坡依据"),
    "basis_eu": ("EU依据", "EU 依据", "欧盟依据"),
    "interface_id": ("接口编号",), "interface": ("接口名称", "接口项", "提资项"),
    "provider": ("提资方", "提供专业"), "receiver": ("接收专业", "接收方"),
    "information": ("接口内容", "提资内容"), "deadline": ("提资节点", "提资日期"),
    "interface_cn": ("CN接口", "CN 接口"), "interface_sg": ("SG接口", "SG 接口"),
    "interface_eu": ("EU接口", "EU 接口"), "owner": ("责任专业", "责任人"),
    "object_ref": ("关联对象", "关联房间", "所属房间", "关联分区", "所属分区", "关联建筑物"),
    "quantity": ("用户数量", "数量"), "measure_unit": ("计量单位", "单位"),
    "brand": ("用户品牌", "品牌"), "model": ("用户型号", "型号"), "spec": ("用户规格", "规格"),
    "zone_id": ("分区编号",), "zone_name": ("功能分区", "景观分区", "分区名称", "分区"),
    "zone_type": ("软硬景类型", "分区类型"), "function": ("使用功能", "功能", "用途"),
    "redline": ("用地红线", "景观红线", "红线"), "area": ("用户面积", "面积"),
    "elevation": ("设计标高", "室外标高", "标高"), "indoor_elevation": ("室内标高",),
    "datum": ("标高基准", "高程基准"), "slope": ("排水坡度", "坡度"),
    "drainage": ("排水坡向", "排水去向", "排水"), "accessible": ("无障碍衔接", "无障碍"),
    "soil_depth": ("覆土厚度", "种植土厚度"), "soil": ("土壤条件", "种植土"),
    "roof_load": ("顶板荷载", "顶板承载资料"), "irrigation": ("灌溉方式", "灌溉"),
    "fire_access": ("消防车道", "消防登高面", "消防通道"), "sight": ("视距要求", "视距"),
    "paving_id": ("铺装编号",), "paving_name": ("铺装名称", "铺装部位", "铺装"),
    "paving_material": ("铺装材料", "铺装材质"), "paving_thickness": ("铺装厚度",),
    "pattern": ("铺装纹样", "纹样"), "kerb": ("缘石",), "step": ("台阶构造", "台阶"),
    "plant_id": ("苗木编号",), "plant_name": ("苗木名称", "苗木种类", "植物名称", "苗木"),
    "layer": ("种植层次", "层次"), "dbh": ("胸径",), "crown": ("冠幅",),
    "retained_tree": ("现状树处理", "保留现状树"), "facility": ("设施名称", "室外设施"),
    "facility_type": ("设施类型",), "power": ("供电要求", "电源", "供电"), "water": ("给水要求", "给水"),
    "room_id": ("房间编号",), "room": ("房间名称", "房间"),
    "floor_base": ("地面基层",), "floor": ("地面面层", "地面做法", "地面"),
    "wall_base": ("墙面基层",), "wall": ("墙面饰面", "墙面做法", "墙面"),
    "ceiling_base": ("天花基层", "吊顶基层"), "ceiling": ("天花做法", "吊顶做法", "天花", "吊顶"),
    "partition": ("隔墙做法", "隔墙"), "fire_rating": ("材料燃烧性能", "防火性能"),
    "waterproof_area": ("防水部位",), "waterproof": ("防水材料", "防水做法", "防水"),
    "waterproof_thickness": ("防水厚度",), "upturn": ("防水上翻高度", "防水上翻", "上翻高度"),
    "moisture": ("防潮做法", "防潮"), "acoustic": ("隔声要求", "隔声"),
    "clear_height": ("吊顶净高", "净高"), "escape_width": ("疏散宽度",),
    "load": ("新增荷载", "设备荷载", "荷载"), "opening": ("楼板开洞", "开洞"),
    "ceiling_coord": ("综合天花", "天花点位接口"), "window_joint": ("窗台收口", "外窗收口"),
    "door_id": ("门编号",), "door": ("门名称", "门窗名称", "门窗"),
    "hardware": ("五金要求", "五金"), "protection": ("成品保护",),
    "system_id": ("系统编号",), "system": ("子系统名称", "系统名称", "子系统", "系统"),
    "coverage": ("覆盖范围", "服务范围", "覆盖"), "points": ("用户点数", "点位数量", "点数"),
    "pixels": ("摄像头像素", "像素"), "retention": ("存储天数", "存储周期"),
    "room_equipment": ("机房位置", "机房"), "shaft": ("弱电井",), "grounding": ("接地端子", "接地"),
    "ups": ("UPS要求", "UPS"), "fire_link": ("消防联动接口", "消防联动"),
    "network": ("网络安全要求", "网络安全"), "platform": ("公共安全平台接口", "平台接口"),
    "route_id": ("桥架编号", "路由编号"), "route": ("桥架名称", "桥架路由", "桥架"),
    "system_ref": ("关联子系统", "关联弱电系统", "服务系统"),
    "tray_spec": ("桥架规格",), "separation": ("与强电桥架间距", "与强电间距", "强弱电间距"),
    "route_length": ("路由长度", "桥架长度"), "penetration": ("穿越做法", "穿墙措施"),
    "point_id": ("点位编号",), "point": ("点位名称", "点位"), "point_type": ("点位类型",),
    "unit_id": ("防护单元编号", "单元编号"), "unit_name": ("防护单元名称", "防护单元", "单元名称"),
    "unit_ref": ("所属单元", "关联单元"), "grade": ("人防等级", "防护等级"),
    "peacetime": ("平时功能",), "wartime": ("战时功能",), "airtight": ("密闭分区",),
    "entrances": ("出入口数量",), "conversion": ("平战转换", "转换措施"),
    "ventilation": ("防化通风", "通风方式"), "filtering": ("滤毒要求", "滤毒"),
    "overpressure": ("超压要求", "超压"), "diesel": ("柴油电站",),
    "mouth_id": ("口部编号",), "mouth": ("口部名称", "口部"), "mouth_type": ("口部类型",),
    "diffusion": ("扩散室",), "canopy": ("防倒塌棚架",), "mouth_ref": ("所属口部",),
    "equipment_id": ("防护设备编号", "设备编号"), "equipment": ("防护设备名称", "防护设备"),
    "equipment_type": ("设备类型",), "door_size": ("门樘尺寸", "门尺寸"), "wall_thickness": ("防护墙厚", "墙厚"),
    "structure_id": ("建筑物编号", "水工建筑物编号"),
    "structure": ("水工建筑物", "建筑物名称", "工程对象", "堤段", "护岸段", "水闸", "泵站"),
    "structure_type": ("建筑物类型", "工程类型"), "task": ("工程任务",),
    "hydro_ref": ("关联水文断面", "采用水文断面"), "geo_ref": ("关联勘探孔", "采用勘探孔"),
    "hydro_id": ("水文断面编号",), "hydro": ("水文断面", "水文站"),
    "hydro_report": ("水文报告", "水文成果"), "flood": ("设计洪水", "设计洪峰"),
    "water_level": ("设计洪水位", "设计水位", "水位"), "frequency": ("洪水频率", "设计频率"),
    "flow": ("设计流量", "流量"), "tide": ("潮位",),
    "borehole": ("地勘孔号", "勘探孔号", "勘探孔"), "geology_report": ("地勘报告", "地质报告"),
    "soil_layer": ("地层描述", "地层"), "groundwater": ("地下水位",),
    "permeability": ("渗透系数",), "fill": ("筑堤材料",),
    "crest": ("堤顶高程",), "crest_width": ("堤顶宽度",), "bank_slope": ("边坡系数", "边坡坡比", "坡比"),
    "seepage": ("渗流条件", "防渗要求"), "scour": ("冲刷资料", "冲刷条件"), "ecology": ("生态岸线要求", "生态岸线"),
    "gate_count": ("闸孔数", "孔数"), "head": ("泵站扬程", "扬程"),
    "diversion": ("施工导流条件", "导流条件"), "flood_season": ("度汛条件",), "monitoring": ("观测要求",),
    "plant_source": ("苗木表来源", "苗木来源"), "quantity_source": ("数量来源", "点表来源", "点表"),
    "node_source": ("节点来源", "节点依据"), "joint_owner": ("收口责任", "收口责任人", "收口单位"),
    "grade_source": ("等级批准资料", "等级来源", "抗力批准资料"), "region": ("所属区域", "区域"),
    "terminal": ("终端名称", "终端"), "pending": ("待核记录", "待核项"),
    "hydro_source": ("水文来源", "水位来源", "流量来源"), "geo_source": ("地质资料来源", "地勘来源"),
}
_ALIASES = {label.casefold(): key for key, labels in _FIELDS.items() for label in labels}
_GLOBAL = {"project", "jurisdiction", "scope", "stage", "drawings", "basis", "basis_cn", "basis_sg", "basis_eu"}
_SHARED = {"location", "source", "drawing", "notes", "record_zone", "quantity", "measure_unit", "brand", "model", "spec", "object_ref", "region", "pending"}
_INTERFACE = ({"interface_id", "interface"}, {"provider", "receiver", "information", "deadline", "owner", "interface_cn", "interface_sg", "interface_eu"})
_GROUPS = {
    "landscape": {
        "zones": ({"zone_id", "zone_name"}, {"zone_type", "function", "redline", "area", "elevation", "indoor_elevation", "datum", "slope", "drainage", "accessible", "soil_depth", "soil", "roof_load", "irrigation", "retained_tree", "fire_access", "sight"}),
        "paving": ({"paving_id", "paving_name"}, {"paving_material", "paving_thickness", "pattern", "kerb", "step", "protection"}),
        "plants": ({"plant_id", "plant_name"}, {"layer", "dbh", "crown", "retained_tree", "plant_source"}),
        "facilities": ({"facility"}, {"facility_type", "power", "water"}),
    },
    "interior": {
        "rooms": ({"room_id", "room"}, {"function", "area", "floor_base", "floor", "wall_base", "wall", "ceiling_base", "ceiling", "partition", "fire_rating", "waterproof_area", "waterproof", "waterproof_thickness", "upturn", "moisture", "acoustic", "clear_height", "escape_width", "load", "opening", "ceiling_coord", "window_joint", "protection", "joint_owner", "node_source"}),
        "doors": ({"door_id", "door"}, {"hardware", "door_size", "protection", "joint_owner", "node_source"}),
    },
    "intel-weak": {
        "systems": ({"system_id", "system"}, {"function", "coverage", "points", "pixels", "retention", "room_equipment", "shaft", "power", "grounding", "load", "ups", "fire_link", "network", "platform", "terminal", "quantity_source"}),
        "routes": ({"route_id", "route"}, {"system_ref", "tray_spec", "separation", "route_length", "penetration"}),
        "points": ({"point_id", "point"}, {"system_ref", "point_type", "power", "terminal", "quantity_source"}),
    },
    "civil-defense": {
        "units": ({"unit_id", "unit_name"}, {"grade", "peacetime", "wartime", "airtight", "entrances", "area", "conversion", "ventilation", "filtering", "overpressure", "water", "drainage", "diesel", "wall_thickness", "grade_source"}),
        "mouths": ({"mouth_id", "mouth"}, {"unit_ref", "mouth_type", "diffusion", "canopy", "penetration"}),
        "equipment": ({"equipment_id", "equipment"}, {"unit_ref", "mouth_ref", "equipment_type", "door_size", "protection"}),
    },
    "hydraulic": {
        "structures": ({"structure_id", "structure"}, {"structure_type", "task", "hydro_ref", "geo_ref", "crest", "crest_width", "bank_slope", "seepage", "scour", "ecology", "gate_count", "head", "flow", "diversion", "flood_season", "monitoring"}),
        "hydrology": ({"hydro_id", "hydro"}, {"hydro_report", "flood", "water_level", "frequency", "flow", "tide", "datum", "hydro_source"}),
        "geology": ({"borehole"}, {"geology_report", "soil_layer", "groundwater", "permeability", "fill", "geo_source"}),
    },
}


def _cell(value: object) -> str:
    return html.escape(str(value).replace("\r", " ").replace("\n", "；").strip(), quote=False).replace("|", "&#124;")


def _table(headers: tuple[str, ...], rows: list[tuple[object, ...]]) -> str:
    return "\n".join(["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |",
                       *("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows)])


def _v(row: dict[str, str], key: str) -> str:
    return row.get(key) or UNKNOWN


@dataclass
class _Input:
    context: dict[str, str] = field(default_factory=dict)
    records: dict[str, list[dict]] = field(default_factory=dict)
    contexts: list[dict] = field(default_factory=list)

    def rows(self, kind: str) -> list[dict[str, str]]:
        return self.records.get(kind) or [{}]


def _parse(text: str, post: str) -> _Input:
    from packing_assistant.post_drafts.design_specialty_observations import parse
    return parse(text, post)


def _section(title: str, data: _Input, kind: str, columns: tuple[tuple[str, str], ...], note: str = "") -> list[str]:
    rows = [tuple(_v(row, key) for _, key in columns) for row in data.rows(kind)]
    return [f"## {title}", "", _table(tuple(label for label, _ in columns), rows), "", note, ""]


def _identity(row: dict, kind: str, post: str) -> str:
    keys = _INTERFACE[0] if kind == "interfaces" else _GROUPS[post][kind][0]
    return " / ".join(row[key] for key in _FIELDS if key in keys and row.get(key) not in {None, UNKNOWN}) or UNKNOWN


def _jurisdiction(data: _Input) -> str:
    zones = {c.get("jurisdiction", UNKNOWN) for c in data.contexts}
    zones.update(r.get("record_zone", UNKNOWN) for records in data.records.values() for r in records)
    zones.update(zone.upper() for c in [*data.contexts, *(r for rows in data.records.values() for r in rows)]
                 for zone in ("cn", "sg", "eu") if c.get("basis_"+zone) not in {None, "", UNKNOWN})
    zones.discard(UNKNOWN)
    return next(iter(zones)) if len(zones) == 1 else "DUAL" if zones else UNKNOWN


def _origin(row, key, explicit="source"):
    return row.get(explicit) or row.get("source") or row.get("_sources", {}).get(key) or UNKNOWN


def _pending(row, keys):
    missing = [_FIELDS[key][0] for key in keys if row.get(key) in {None, UNKNOWN}]
    return "；".join(filter(None, (row.get("pending"), "待核："+"、".join(missing) if missing else "用户资料未核验；待责任专业签认")))


def _hydraulic_children(data, parent, kind, reference, anchors):
    ref = parent.get(reference)
    if ref in {None, UNKNOWN}:
        return [c for c in data.records.get(kind, []) if c.get("_parent") is parent]
    identities = {parent.get(k) for k in _GROUPS["hydraulic"]["structures"][0]} - {None, UNKNOWN}
    matches = [c for c in data.records.get(kind, [])
               if c.get("_file") == parent.get("_file") and c.get("_context") == parent.get("_context")
               and any(c.get(k) == ref for k in anchors)
               and ("object_ref" not in c or c["object_ref"] in identities)]
    # An explicit ID is enough only when it resolves to one supplied record in
    # this file/project/stage. Duplicates and cross-file matches stay unlinked.
    return matches if len(matches) == 1 else []


def _professional(data: _Input, post: str) -> list[str]:
    lines = []
    if post == "landscape":
        lines += ["## 苗木与分区来源核对", "", _table(("分区", "苗木种类", "用户规格", "胸径", "冠幅", "数量", "来源", "待核项", "签认"), [
            tuple(_v(r, k) for k in ("object_ref", "plant_name", "spec", "dbh", "crown", "quantity"))
            + (_origin(r, "plant_name", "plant_source"), _pending(r, ("plant_source", "spec", "quantity")), "") for r in data.rows("plants")]), ""]
    elif post == "interior":
        lines += ["## 房间做法与收口责任", "", _table(("房间", "地面", "墙面", "天花", "防水", "上翻", "节点图号", "节点来源", "收口责任", "待核项", "签认"), [
            tuple(_v(r, k) for k in ("room", "floor", "wall", "ceiling", "waterproof", "upturn", "drawing"))
            + (_origin(r, "drawing", "node_source"), _v(r, "joint_owner"), _pending(r, ("node_source", "joint_owner", "fire_rating")), "") for r in data.rows("rooms")]), ""]
    elif post == "intel-weak":
        lines += ["## 系统点表与联调边界", "", _table(("系统", "区域", "终端或功能", "用户数量", "数量来源", "网络供电", "接口", "缺项 / 待测", "联调结果"), [
            (_v(r, "system"), _v(r, "coverage"), r.get("terminal", r.get("function", UNKNOWN)), r.get("points", r.get("quantity", UNKNOWN)),
             _origin(r, "points", "quantity_source"), "；".join(r[k] for k in ("network", "power", "ups") if r.get(k)) or UNKNOWN,
             _v(r, "fire_link"), _pending(r, ("quantity_source", "platform"))+"；接口及协议待测，本轮未联调", "") for r in data.rows("systems")]), ""]
    elif post == "civil-defense":
        lines += ["## 防护单元专业接口核对", "", _table(("单元", "辖区", "平时用途", "战时用途", "用户等级要求（未核）", "批准资料来源", "口部资料", "通风给排水", "用户图号", "待核项", "签认"), [
            (_identity(r, "units", post),)+tuple(_v(r, k) for k in ("record_zone", "peacetime", "wartime", "grade"))
            + (_v(r, "grade_source"), "；".join(_identity(c, "mouths", post)+" / "+_v(c, "drawing") for c in data.records.get("mouths", []) if c.get("_parent") is r) or UNKNOWN,
               "；".join(r[k] for k in ("ventilation", "water", "drainage", "diesel") if r.get(k)) or UNKNOWN, _v(r, "drawing"),
               _pending(r, ("grade_source", "grade", "airtight")), "") for r in data.rows("units")]), ""]
    elif post == "hydraulic":
        parameters = []
        for r in data.rows("structures"):
            hydrology = _hydraulic_children(data, r, "hydrology", "hydro_ref", ("hydro_id", "hydro"))
            geology = _hydraulic_children(data, r, "geology", "geo_ref", ("borehole",))
            for h in hydrology or [{}]:
                parameters.append((_identity(r, "structures", post), _v(h, "water_level"), h.get("flow", r.get("flow", UNKNOWN)),
                                   r.get("hydro_ref", h.get("hydro", UNKNOWN)),
                                   "；".join(_v(c, "borehole")+" / "+_v(c, "geology_report") for c in geology) or _v(r, "geo_ref"),
                                   h.get("hydro_source") or _origin(h, "water_level", "hydro_report"), "水力、渗流、防渗、抗滑及稳定计算均未连接 / UNSPECIFIED", ""))
        lines += ["## 水工参数来源与待计算项", "", _table(("建筑物", "用户水位", "用户流量", "关联水文断面", "地质资料", "来源", "待计算项", "签认"), parameters), ""]
    scope_rows = [( _v(c, "project"), _v(c, "jurisdiction"), _v(c, "scope"), _v(c, "stage"), _v(c, "drawings"), c.get("_origin") or UNKNOWN)
                  for c in data.contexts if any(not k.startswith("_") and (k != "jurisdiction" or v != UNKNOWN) for k, v in c.items())]
    lines += ["## 项目与文件资料分组", "", _table(("项目", "总体辖区（不代替对象）", "范围", "设计阶段", "用户图号清单", "输入资料名"), scope_rows or [(UNKNOWN,)*6]), ""]
    basis_rows = [(_v(c, "project"), UNKNOWN)+tuple(_v(c, k) for k in ("stage", "basis", "basis_cn", "basis_sg", "basis_eu"))
                  +(c.get("_origin") or UNKNOWN, "未核验；适用性待责任专业确认") for c in data.contexts
                  if any(c.get(k) not in {None, "", UNKNOWN} for k in ("basis", "basis_cn", "basis_sg", "basis_eu"))]
    for kind, records in data.records.items():
        for r in records:
            if any(r.get(k) not in {None, "", UNKNOWN} for k in ("basis", "basis_cn", "basis_sg", "basis_eu")):
                context = data.contexts[r.get("_context", 0)]
                basis_rows.append((_v(context, "project"), _identity(r, kind, post), _v(context, "stage"))
                                  +tuple(_v(r, k) for k in ("basis", "basis_cn", "basis_sg", "basis_eu"))
                                  +(r.get("_origin") or UNKNOWN, "未核验；适用性待责任专业确认"))
    lines += ["## 各项目与文件依据归属", "", _table(("项目", "专业对象", "阶段", "未指定辖区的依据", "CN 用户资料", "SG 用户资料", "EU 用户资料", "输入文件", "核对状态"), basis_rows or [(UNKNOWN,)*8+("未核验",)]), ""]
    facts = []
    for kind, records in data.records.items():
        for r in records:
            context = data.contexts[r.get("_context", 0)]
            for key, value in r.items():
                if key.startswith("_") or value == UNKNOWN:
                    continue
                explicit = "plant_source" if kind == "plants" else "quantity_source" if key in {"points", "quantity"} else "node_source" if key == "drawing" else "source"
                facts.append((_v(context, "project"), _identity(r, kind, post), _FIELDS[key][0], value,
                              _origin(r, key, explicit), r.get("_sources", {}).get(key, UNKNOWN), _v(r, "drawing"), "未核验；待责任专业复核签认"))
    lines += ["## 对象参数、来源与待核记录", "", _table(("项目 / 分组", "专业对象", "参数栏目", "用户给定值（未核）", "用户资料来源", "输入位置（非原件页码）", "用户图号", "核对状态"), facts or [(UNKNOWN,)*7+("未核验",)]), ""]
    return lines


def _header(title: str, data: _Input, text: str, boundary: str) -> list[str]:
    return [f"# {title}（AI 草稿 · 内部讨论）", "", DISCLAIMER, "", boundary, "",
            f"- 辖区：{_jurisdiction(data)}", "- submit_blocked=true；未签认", "",
            "所有表内参数均为用户明确给定的待核输入；UNSPECIFIED 表示缺项。本轮未进行设计计算、选型、布点或审查。", "",
            "页头辖区仅描述本次资料的总体范围，不代表各对象适用辖区。对象没有明确辖区时保持 UNSPECIFIED。", "",
            "## 1 项目与资料登记", "",
            _table(("登记项", "用户提供 / 缺项"), [(label, _v(data.context, key)) for label, key in (
                ("项目", "project"), ("本次范围", "scope"), ("作业部位", "location"), ("设计阶段", "stage"), ("用户图号清单", "drawings"))]), ""]


def _basis(data: _Input, text: str) -> list[str]:
    zone = _jurisdiction(data)
    columns = ("依据项", "CN 输入", "SG 输入", "EU 输入", "核实状态")
    rows = [("用户指定文件", _v(data.context, "basis_cn"), _v(data.context, "basis_sg"), _v(data.context, "basis_eu"), "unverified")]
    object_rows = []
    for kind, records in data.records.items():
        anchor_keys = _INTERFACE[0] if kind == "interfaces" else next(groups[kind][0] for groups in _GROUPS.values() if kind in groups)
        for row in records:
            identifier = "；".join(f"{_FIELDS[key][0]}：{value}" for key, value in row.items() if key in anchor_keys) or UNKNOWN
            object_rows.append((identifier, _v(row, "record_zone")))
    return ["## 依据与辖区核对", "", _table(("识别辖区", "未分配辖区的用户依据", "条款 / 适用性"), [(zone, _v(data.context, "basis"), UNKNOWN)]), "",
            _table(columns, rows), "", "DUAL 时分别核对各辖区原文、年份和适用范围；空列不表示该辖区适用。不将一辖区依据复制给另一辖区。", "",
            "### 对象辖区登记", "", _table(("对象编号 / 名称", "用户指定对象辖区"), object_rows or [(UNKNOWN, UNKNOWN)]), "",
            "此表只登记该对象行明确给定的辖区；总体范围、相邻记录和专业依据不会自动填入空栏。", ""]


def _interfaces(data: _Input, requests: tuple[tuple[str, str], ...]) -> list[str]:
    columns = (("接口编号", "interface_id"), ("接口项", "interface"), ("关联对象", "object_ref"),
               ("提资方", "provider"), ("接收专业", "receiver"), ("用户提资内容", "information"),
               ("CN 接口", "interface_cn"), ("SG 接口", "interface_sg"), ("EU 接口", "interface_eu"),
               ("提资节点", "deadline"), ("责任人 / 专业", "owner"))
    return [*_section("专业提资与辖区接口", data, "interfaces", columns,
                      "接口按用户指定对象逐行登记。CN / SG / EU 栏互不补值，责任与日期缺失时继续待核。"),
            "### 待发起的专业核对目录", "", _table(("对接专业", "需确认资料", "资料状态"), [(p, t, UNKNOWN) for p, t in requests]), ""]


def _gaps(data: _Input, specs: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...]) -> list[str]:
    rows = []
    for kind, name_key, fields in specs:
        for row in data.rows(kind):
            missing = "、".join(label for label, key in fields if row.get(key) in {None, "", UNKNOWN})
            rows.append((_v(row, name_key), missing or "已登记用户值；仍需核验来源与适用性", "待责任专业复核"))
    return ["## 缺项、复核与交付目录", "", _table(("对象", "缺项 / 核对内容", "处理状态"), rows), "",
            "交付目录仅为待补清单：说明、相关平面、节点详图、材料或设备表、依据原文、提资回复及专业签认记录。图号只使用用户清单；本稿未生成施工图或签认文件。", ""]


def _landscape(text: str) -> str:
    data = _parse(text, "landscape")
    lines = _header("园林景观设计说明", data, text, "软硬景与接口原则稿；不编苗木规格、铺装厚度或单价，不替代种植施工图签章。")
    lines += _section("2 范围红线与软硬景分区", data, "zones", (("分区编号", "zone_id"), ("分区", "zone_name"), ("软硬景类型", "zone_type"), ("功能", "function"), ("位置", "location"), ("红线", "redline"), ("用户面积", "area")))
    lines += ["入口、广场、宅间、活动区、水景、顶板绿化仅是可选核对类别，不代表本项目均有设置。", ""]
    lines += _section("3 竖向、无障碍与排水", data, "zones", (("分区", "zone_name"), ("标高基准", "datum"), ("室内标高", "indoor_elevation"), ("室外标高", "elevation"), ("用户坡度", "slope"), ("排水坡向 / 去向", "drainage"), ("无障碍衔接", "accessible")), "未给标高和衔接条件时，不推算坡度或台阶尺寸。")
    lines += _section("4 园路与硬景铺装", data, "paving", (("铺装编号", "paving_id"), ("铺装部位", "paving_name"), ("关联分区", "object_ref"), ("材质", "paving_material"), ("用户厚度", "paving_thickness"), ("纹样", "pattern"), ("缘石", "kerb"), ("台阶", "step"), ("选材来源", "source")))
    lines += _section("5 软景与苗木登记", data, "plants", (("苗木编号", "plant_id"), ("苗木名称", "plant_name"), ("所属分区", "object_ref"), ("乔灌草层次", "layer"), ("用户规格", "spec"), ("胸径", "dbh"), ("冠幅", "crown"), ("用户数量", "quantity"), ("单位", "measure_unit"), ("现状树处理", "retained_tree"), ("苗木表来源", "source")), "无甲方苗木表不选胸径、冠幅和数量；不据此承诺成活率。")
    lines += _section("6 土壤、顶板覆土与灌排", data, "zones", (("分区", "zone_name"), ("种植土", "soil"), ("用户覆土厚度", "soil_depth"), ("顶板承载资料", "roof_load"), ("灌溉", "irrigation"), ("排水", "drainage")), "地下室顶板上的覆土、构筑物与积水荷载提资结构专业；本轮未校核顶板。")
    lines += _section("7 水景、照明与室外设施", data, "facilities", (("设施", "facility"), ("类型", "facility_type"), ("位置", "location"), ("用户供电要求", "power"), ("用户给水要求", "water"), ("用户规格", "spec"), ("资料来源", "source")))
    lines += _section("8 消防交通与周边衔接", data, "zones", (("分区", "zone_name"), ("消防通道条件", "fire_access"), ("用户视距要求", "sight"), ("边界 / 红线", "redline")), "核对消防通道、登高面、人行通行及视线边界；景观变更不得自行修改建筑防火或市政横断面条件。")
    lines += _interfaces(data, (("建筑 / 总图", "红线、出入口、散水台阶、室内外标高和消防场地"), ("结构", "顶板覆土、水景及设施荷载与防水保护"), ("市政 / 给排水 / 电气", "道路铺装接缝、雨水去向、灌溉及照明接口")))
    lines += _gaps(data, (("zones", "zone_name", (("红线", "redline"), ("标高", "elevation"), ("排水", "drainage"))), ("plants", "plant_name", (("苗木表来源", "source"), ("规格", "spec"), ("数量", "quantity"))), ("paving", "paving_name", (("材质", "paving_material"), ("构造依据", "source")))))
    return "\n".join(lines + _professional(data, "landscape") + _basis(data, text))


def _interior(text: str) -> str:
    data = _parse(text, "interior")
    lines = _header("室内装修设计说明与界面表", data, text, "仅整理饰面、隔墙、吊顶、防水与收口资料；不设计主体配筋或承载力，不替代消防专篇。")
    lines += _section("2 房间功能与饰面做法", data, "rooms", (("房间编号", "room_id"), ("房间", "room"), ("功能", "function"), ("地面基层", "floor_base"), ("地面面层", "floor"), ("墙面基层", "wall_base"), ("墙面饰面", "wall"), ("天花基层", "ceiling_base"), ("天花做法", "ceiling"), ("隔墙", "partition"), ("选材来源", "source")))
    lines += _section("3 防水、防潮与隔声节点", data, "rooms", (("房间", "room"), ("防水部位", "waterproof_area"), ("防水材料 / 做法", "waterproof"), ("用户防水厚度", "waterproof_thickness"), ("用户上翻高度", "upturn"), ("防潮", "moisture"), ("隔声", "acoustic"), ("用户节点图号", "drawing")), "卫生间、阳台等湿区应先核对用户范围、基层与管根收口；未给构造不补厚度或上翻数值。")
    lines += _section("4 建筑、结构、机电与外窗界面", data, "rooms", (("房间", "room"), ("隔墙性质 / 做法", "partition"), ("用户净高", "clear_height"), ("新增荷载", "load"), ("楼板开洞", "opening"), ("综合天花接口", "ceiling_coord"), ("外窗收口", "window_joint")), "对照主体设计确认隔墙属性、楼板开洞和吊杆锚固；综合天花协调灯位、风口、喷淋与检修口。承重构件不得按普通隔墙处理。")
    lines += _section("5 门窗五金与成品保护", data, "doors", (("门编号", "door_id"), ("门窗", "door"), ("所属房间", "object_ref"), ("用户尺寸", "door_size"), ("五金", "hardware"), ("用户品牌", "brand"), ("成品保护", "protection")))
    lines += _section("6 消防疏散与材料核对", data, "rooms", (("房间", "room"), ("用户疏散宽度", "escape_width"), ("用户材料燃烧性能", "fire_rating"), ("用户选材来源", "source")), "核对装修与主体疏散、消防设施的衔接；宽度、燃烧性能和耐火参数未经资料核验，不在本稿形成合格结论。")
    lines += _interfaces(data, (("建筑", "主体平面、隔墙性质、门洞、疏散与净高条件"), ("结构", "石材及设备新增荷载、洞口与吊杆锚固资料"), ("机电 / 幕墙", "综合天花、防水管根、箱门开启、窗台与窗帘盒收口")))
    lines += _gaps(data, (("rooms", "room", (("地面做法", "floor"), ("墙面做法", "wall"), ("天花做法", "ceiling"), ("选材来源", "source"))), ("doors", "door", (("关联房间", "object_ref"), ("五金要求", "hardware")))))
    lines += ["验收资料目录待补：材料样板及来源、隐蔽节点记录、饰面做法表、节点索引、观感检查和专业提资回复；本稿不预填验收结果。", ""]
    return "\n".join(lines + _professional(data, "interior") + _basis(data, text))


def _weak(text: str) -> str:
    data = _parse(text, "intel-weak")
    lines = _header("弱电 / 智能化设计说明", data, text, "无建筑平面不布点，无用户要求不选品牌、型号、摄像头像素或存储周期。火灾自动报警主体由消防 / 电气负责。")
    lines += _section("2 子系统与用户功能需求", data, "systems", (("系统编号", "system_id"), ("子系统", "system"), ("功能", "function"), ("覆盖范围", "coverage"), ("用户点数", "points"), ("用户品牌", "brand"), ("用户型号", "model"), ("摄像头像素", "pixels"), ("用户存储天数", "retention"), ("来源", "source")))
    lines += ["未点名的子系统仅作为待勾选目录：综合布线、无线、信息网络、视频安防、出入口、楼宇自控、停车、背景音乐、信息导引和运营商进线；不默认全部建设。", ""]
    lines += _section("3 机房、弱电井与桥架路由", data, "routes", (("桥架编号", "route_id"), ("桥架 / 路由", "route"), ("关联子系统", "system_ref"), ("位置", "location"), ("用户桥架规格", "tray_spec"), ("用户路由长度", "route_length"), ("用户强弱电间距", "separation"), ("穿越措施", "penetration")), "桥架规格与间距缺失时保持待定；路由须结合风管、喷淋、检修空间及防护密闭接口协调。")
    lines += _section("4 点位与系统关联登记", data, "points", (("点位编号", "point_id"), ("点位", "point"), ("类型", "point_type"), ("关联子系统", "system_ref"), ("位置", "location"), ("用户数量", "quantity"), ("用户图号", "drawing")), "点位表只抄用户记录，不据位置描述生成坐标或覆盖证明。")
    lines += _section("5 供电、UPS、接地与空间提资", data, "systems", (("子系统", "system"), ("机房", "room_equipment"), ("弱电井", "shaft"), ("电源", "power"), ("用户负荷", "load"), ("用户 UPS 要求", "ups"), ("接地端子", "grounding")), "电源和接地由电气提供资料；本岗不计算 UPS 容量或替代供配电设计。")
    lines += _section("6 消防联动、网络与公共安全接口", data, "systems", (("子系统", "system"), ("用户消防联动接口", "fire_link"), ("用户网络安全要求", "network"), ("用户公共安全平台接口", "platform")), "门禁、道闸、电梯等联动动作交消防 / 电气确认；未给网络或平台要求不编参数。")
    lines += _interfaces(data, (("建筑 / 装修", "机房、弱电井、点位平面、面板与检修空间"), ("电气 / 消防", "电源、接地、UPS 输入及联动边界"), ("总包 / 幕墙 / 人防", "桥架预埋、出线和穿越防护密闭接口")))
    lines += _gaps(data, (("systems", "system", (("系统范围", "coverage"), ("点位来源", "source"), ("用户点数", "points"))), ("routes", "route", (("关联系统", "system_ref"), ("桥架规格", "tray_spec"), ("间距依据", "separation"))), ("points", "point", (("关联系统", "system_ref"), ("图号", "drawing")))))
    lines += ["调试与资料目录待补：系统图、平面图、点位表、设备表、联动接口表、调试记录及验收资料；本稿未执行调试或技防审查。", ""]
    return "\n".join(lines + _professional(data, "intel-weak") + _basis(data, text))


def _defense(text: str) -> str:
    data = _parse(text, "civil-defense")
    lines = _header("人防工程设计专篇提纲", data, text, "仅登记防护单元、口部和专业接口；不替代人防主管部门审图。等级、结构尺寸和防护设备均须专项人员复核。")
    lines += _section("2 防护单元、等级与平战功能", data, "units", (("单元编号", "unit_id"), ("防护单元", "unit_name"), ("适用辖区", "record_zone"), ("用户防护等级", "grade"), ("平时功能", "peacetime"), ("战时功能", "wartime"), ("密闭分区", "airtight"), ("用户面积", "area"), ("用户出入口数量", "entrances"), ("转换措施", "conversion")), "未知等级、功能和分区继续待填；CN 人防等级与 SG HS/SS 等防护空间概念不直接互换。")
    lines += _section("3 口部、出入口与扩散空间", data, "mouths", (("口部编号", "mouth_id"), ("口部", "mouth"), ("所属单元", "unit_ref"), ("口部类型", "mouth_type"), ("位置", "location"), ("扩散室", "diffusion"), ("防倒塌棚架", "canopy"), ("穿越措施", "penetration"), ("用户图号", "drawing")), "口部配置和出入口数量按用户等级与正式设计核对，不在此选择个数或尺寸。")
    lines += _section("4 防护设备与孔口登记", data, "equipment", (("设备编号", "equipment_id"), ("防护设备", "equipment"), ("所属单元", "unit_ref"), ("所属口部", "mouth_ref"), ("设备类型", "equipment_type"), ("用户型号", "model"), ("用户门樘尺寸", "door_size"), ("用户数量", "quantity"), ("来源", "source")), "防护密闭门、防爆破活门等仅为设备分类提示，不能用普通消防门信息补齐防护设备选型。")
    lines += _section("5 防化通风、滤毒与超压", data, "units", (("防护单元", "unit_name"), ("用户通风方式", "ventilation"), ("滤毒要求", "filtering"), ("用户超压要求", "overpressure"), ("用户防护墙厚", "wall_thickness")), "本轮未计算通风量、超压或防护结构；穿越围护结构的管线需逐项复核防护密闭做法。")
    lines += _section("6 给排水、柴油电站与转换", data, "units", (("防护单元", "unit_name"), ("用户给水要求", "water"), ("用户排水要求", "drainage"), ("柴油电站设置", "diesel"), ("用户转换措施", "conversion")), "柴油电站是否设置由用户与专项设计资料确定，不能默认新增；平时疏散接口与防护转换接口分开核对。")
    lines += _interfaces(data, (("建筑 / 结构", "上部建筑、单元边界、口部、围护结构和用户防护等级资料"), ("暖通 / 给排水 / 电气", "防化通风、滤毒、密闭穿越、给排水和柴油电站接口"), ("消防 / 装修", "平时疏散、转换、孔口设备及防护设施保护")))
    lines += _gaps(data, (("units", "unit_name", (("等级", "grade"), ("平时功能", "peacetime"), ("战时功能", "wartime"), ("密闭分区", "airtight"))), ("mouths", "mouth", (("所属单元", "unit_ref"), ("正式图号", "drawing"))), ("equipment", "equipment", (("设备来源", "source"), ("所属口部", "mouth_ref"), ("用户型号", "model")))))
    lines += ["审查资料目录待补：平时与战时平面、单元划分、口部详图、防护设备表、专业计算和专项签认记录。审查结论：UNSPECIFIED。", ""]
    return "\n".join(lines + _professional(data, "civil-defense") + _basis(data, text))


def _hydraulic(text: str) -> str:
    data = _parse(text, "hydraulic")
    lines = _header("水利工程设计提纲", data, text, "覆盖堤防、护岸、水闸、泵站等用户指定对象。无水文地质成果不定量，本轮未进行水力、渗流、冲刷或边坡稳定计算。")
    lines += _section("2 工程任务与水工对象", data, "structures", (("建筑物编号", "structure_id"), ("水工建筑物", "structure"), ("类型", "structure_type"), ("工程任务", "task"), ("位置", "location"), ("关联水文断面", "hydro_ref"), ("关联勘探孔", "geo_ref")), "防洪、排涝、灌溉、供水仅为任务候选；未由用户指定时不代选。码头桩台、泊位与系缆交港航专业。")
    lines += _section("3 水文成果与水位流量登记", data, "hydrology", (("断面编号", "hydro_id"), ("水文断面 / 站", "hydro"), ("水文成果来源", "hydro_report"), ("设计洪水", "flood"), ("用户设计水位", "water_level"), ("用户流量", "flow"), ("用户频率", "frequency"), ("用户潮位", "tide"), ("高程基准", "datum")), "数据逐断面保留，不将一个断面的水位或基准用于另一个断面；未给水文成果时不推定设计洪水位。")
    lines += _section("4 地质、地下水与筑堤材料", data, "geology", (("勘探孔号", "borehole"), ("地勘报告", "geology_report"), ("地层", "soil_layer"), ("用户地下水位", "groundwater"), ("用户渗透系数", "permeability"), ("筑堤材料", "fill"), ("来源", "source")), "参数均为待核输入；缺地勘、材料试验或地下水条件时，稳定与防渗校核保持未执行。")
    lines += _section("5 堤防、护岸与断面原则", data, "structures", (("水工建筑物", "structure"), ("用户堤顶高程", "crest"), ("用户堤顶宽度", "crest_width"), ("用户边坡坡比", "bank_slope"), ("渗流 / 防渗条件", "seepage"), ("冲刷资料", "scour"), ("生态岸线要求", "ecology")), "只登记用户断面条件，不从水位自行叠加安全超高，不补边坡系数；生态与亲水接口不能反向改变防洪条件。")
    lines += _section("6 水闸、泵站与穿堤接口", data, "structures", (("水工建筑物", "structure"), ("类型", "structure_type"), ("用户闸孔数", "gate_count"), ("用户扬程", "head"), ("用户流量", "flow"), ("关联水文断面", "hydro_ref"), ("用户图号", "drawing")), "闸孔数与扬程只抄明确给值，不选泵、不算过流能力；穿堤建筑物的防渗与堤身接口需专项复核。")
    lines += _section("7 施工导流、度汛与观测提资", data, "structures", (("水工建筑物", "structure"), ("用户导流条件", "diversion"), ("用户度汛条件", "flood_season"), ("用户观测要求", "monitoring")), "设计条件交施工方案专业深化导流与度汛措施；本稿不补施工期流量、设备能力或联系人。")
    lines += _interfaces(data, (("水文 / 岩土", "水文成果、基准、地勘及筑堤材料试验"), ("港航 / 桥梁", "通航与码头连接段、穿堤构筑物、桥梁上部的分工界面"), ("市政 / 景观 / 施工", "城市雨洪出路、生态岸线、亲水平台及导流度汛条件")))
    lines += _gaps(data, (("structures", "structure", (("工程任务", "task"), ("水文关联", "hydro_ref"), ("地勘关联", "geo_ref"))), ("hydrology", "hydro", (("水文成果", "hydro_report"), ("水位", "water_level"), ("高程基准", "datum"))), ("geology", "borehole", (("地勘报告", "geology_report"), ("地层", "soil_layer")))))
    lines += ["观测与验收目录待补：水文地质成果、断面与结构设计资料、施工条件提资、观测方案、质量记录及专业签认。校核与验收结果：UNSPECIFIED。", ""]
    return "\n".join(lines + _professional(data, "hydraulic") + _basis(data, text))


def build_draft(expert_id: str, tool_name: str, text: str) -> str | None:
    """Pure renderer; the host owns confirmation, sandboxed writes and Excel export."""
    if TOOLS.get(expert_id) != tool_name:
        return None
    return {"landscape": _landscape, "interior": _interior, "intel-weak": _weak,
            "civil-defense": _defense, "hydraulic": _hydraulic}[expert_id](text or "")
