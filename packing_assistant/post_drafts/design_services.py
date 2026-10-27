"""Services and steel design draft renderers; record facts, never size or approve."""

from __future__ import annotations

import html
import re
from collections.abc import Sequence

from packing_assistant.jurisdiction import infer_jurisdiction
from packing_assistant.office_job import tables_from_md
from packing_assistant.post_drafts.design_observations import literal_parameter, parse_narrative

MISSING = "[A001] / UNSPECIFIED"
_TOOLS = {"plumbing": "plumbing__memo", "hvac": "hvac__memo", "electrical": "electrical__memo",
          "fire-protect": "fire-protect__brief", "steel": "steel__memo"}
_TITLES = {"plumbing": "给排水专业设计说明", "hvac": "暖通专业设计说明", "electrical": "电气专业设计说明",
           "fire-protect": "消防设计专篇提纲", "steel": "钢结构设计说明"}
_COMMON = {
    "project": ("项目名称", "工程名称", "项目"), "unit": ("单体名称", "单体", "作业部位", "部位"),
    "jurisdiction": ("辖区", "适用辖区"), "system": ("系统名称", "系统"),
    "component": ("构件编号", "构件名称", "构件"), "scope": ("系统范围", "设计范围", "服务空间", "范围"),
    "source": ("资料来源", "提资人", "来源"), "basis": ("设计依据", "依据文件", "依据"),
    "drawings": ("图号清单", "图纸清单", "图号", "用户图号"), "calculations": ("计算书名称", "计算书", "计算资料"),
    "interface": ("专业接口", "接口要求", "接口"), "function": ("建筑功能", "使用功能", "功能"),
    "height": ("建筑高度", "高度"), "floors": ("建筑层数", "层数"), "zone": ("系统分区", "分区"),
    "room": ("机房位置", "机房"), "metering": ("计量方式", "计量"),
    "water_flow": ("设计流量", "水流量", "流量"), "pipe": ("用户管径", "管径"),
    "fire_system": ("灭火系统", "消防系统", "消防给水系统"), "fire_flow": ("消防用水量", "消防水量"),
    "fire_pressure": ("消防水压",), "smoke_zone": ("防烟分区",), "smoke_flow": ("排烟量",),
    "interlock": ("联动要求", "联锁要求", "联动", "联锁"), "fire_power": ("消防电源",),
    "emergency_lighting": ("应急照明", "疏散指示"), "fire_rating": ("耐火极限",),
    "material": ("材料牌号", "钢材牌号", "材料"), "specification": ("用户规格", "构件规格", "规格"),
    "load": ("用户负荷", "负荷", "荷载"), "efficiency": ("效率资料", "效率", "能效"),
}
for _zone in ("CN", "SG", "EU"):
    _COMMON[f"{_zone.lower()}_basis"] = (f"{_zone}依据",)
    _COMMON[f"{_zone.lower()}_interface"] = (f"{_zone}接口",)

_POST_FIELDS = {
    "plumbing": {
        "water_source": ("水源",), "connection": ("市政接驳位置", "接管位置", "接驳位置"),
        "mains_pipe": ("市政管径", "接管管径"), "pressure": ("市政水压", "市政压力", "供水压力", "水压", "压力"),
        "water_meter": ("水表井",), "fixtures": ("卫生器具当量", "器具当量"),
        "tank": ("水箱方案", "水箱"), "pump": ("既有泵型", "用户泵型", "泵型"),
        "floor_level": ("室内地面标高", "室内标高"), "outlet_level": ("出户标高", "排水出户标高", "出户"),
        "municipal_level": ("市政井标高", "井底"), "municipal_well": ("市政井编号", "市政井"),
        "riser": ("立管编号", "立管"),
        "drain_route": ("排水路径", "排水布置"), "catchment": ("汇水范围", "汇水面积"),
        "rainfall": ("降雨资料", "暴雨参数"), "overflow": ("溢流措施", "溢流"),
        "reuse": ("雨水回用", "中水方案", "回用方案"), "pump_room": ("消防泵房", "泵房资料"),
        "pit": ("集水坑", "集水井"), "lift_drain": ("消防电梯井底排水",),
        "grease": ("隔油设施", "隔油"), "septic": ("化粪设施", "化粪池"),
        "pressure_control": ("超压控制",), "zone": ("供水分区", "系统分区", "分区"),
    },
    "hvac": {
        "indoor": ("室内计算参数", "室内参数"), "outdoor": ("室外计算参数", "室外参数"),
        "parameter_source": ("参数来源",), "heat_source": ("冷热源方案", "冷热源"),
        "cooling_load": ("冷负荷",), "heating_load": ("热负荷",), "hourly_load": ("逐时负荷", "逐项逐时负荷"),
        "fresh_air": ("新风量",), "air_flow": ("送风量", "风量"),
        "duct": ("用户风管断面", "风管断面", "风道尺寸"),
        "window": ("自然排烟窗", "排烟窗"), "pressurization": ("加压送风", "加压送风量"),
        "makeup_air": ("补风路径", "补风"), "tower": ("冷却塔位置", "冷却塔"),
        "shaft": ("竖井位置", "竖井"), "outdoor_unit": ("室外机位",),
        "noise": ("消声措施", "消声"), "vibration": ("隔振措施", "隔振"),
        "condensate": ("冷凝水",), "insulation": ("保温方案", "保温"),
        "model": ("既有主机型号", "用户主机型号"),
    },
    "electrical": {
        "circuits": ("市政回路数", "回路数"), "voltage": ("供电电压", "电压"),
        "backup": ("自备电源", "备用电源"), "capacity": ("设备容量", "装机容量"),
        "factor": ("需要系数", "利用系数", "系数"), "active": ("计算有功", "有功功率"),
        "reactive": ("计算无功", "无功功率"), "apparent": ("计算视在", "视在功率"),
        "transformer": ("用户变压器容量", "变压器容量"), "transformer_count": ("变压器台数",),
        "distribution": ("低压配电", "配电原则"), "lighting": ("一般照明", "照明"),
        "lightning": ("防雷类别",), "grounding": ("接地电阻目标", "接地目标"),
        "equipotential": ("等电位",), "cable_route": ("线路敷设", "敷设路径"),
        "tray": ("桥架",), "spacing": ("用户间距", "与弱电间距"),
        "fire_cable": ("消防线路", "防火电缆"), "transfer": ("切换时间要求", "切换时间"),
        "weak_power": ("弱电机房电源",), "weak_ground": ("弱电接地端子",),
    },
    "fire-protect": {
        "classification": ("建筑分类",), "fire_grade": ("耐火等级",),
        "fire_lane": ("消防车道",), "staging": ("登高操作场地",), "fire_face": ("消防扑救面", "扑救面"),
        "outdoor_hydrant": ("室外消火栓",), "compartment": ("防火分区",),
        "compartment_area": ("防火分区面积", "分区面积"), "fire_wall": ("防火墙与楼板", "防火墙"),
        "fire_door": ("防火门窗", "防火门"), "curtain_stop": ("幕墙层间封堵", "层间封堵"),
        "exits": ("安全出口", "安全出口数量"), "egress_width": ("疏散宽度",),
        "egress_distance": ("疏散距离",), "refuge": ("避难层间", "避难层", "避难间"),
        "smoke_method": ("防排烟方式",), "alarm": ("报警保护对象", "报警系统"),
        "control": ("消防控制接口",), "fire_cable": ("消防配电线路",),
        "renovation": ("装修范围", "改造范围"), "existing_boundary": ("既有分区边界",),
        "documents": ("报审资料目录", "资料目录"),
    },
    "steel": {
        "structure": ("结构体系", "体系"), "span": ("跨度",),
        "roof_load": ("屋面活荷载", "屋面恒载", "屋面荷载"), "crane": ("吊车资料", "吊车荷载"),
        "wind": ("风荷载", "风压"), "seismic": ("地震资料", "抗震参数"), "temperature": ("温度作用", "温度荷载"),
        "material_source": ("材料来源", "钢材来源"), "connection": ("连接方式", "连接"),
        "bolt": ("用户螺栓规格", "螺栓规格"), "weld": ("用户焊缝尺寸", "焊缝尺寸"),
        "node": ("节点图号", "节点详图"), "atlas": ("用户指定图集", "图集"),
        "bracing": ("柱间支撑", "支撑"), "tie": ("系杆",), "knee": ("隅撑",),
        "derust": ("除锈等级",), "coating": ("防腐体系", "防腐涂装", "防腐"),
        "coating_thickness": ("防腐涂层厚度", "涂层厚度"), "fireproof": ("防火保护", "防火做法"),
        "fireproof_thickness": ("防火涂料厚度",), "fire_source": ("耐火极限来源", "防火依据"),
        "fabrication": ("加工资料",), "transport": ("运输资料",), "erection": ("安装资料",),
        "inspection": ("焊缝检测资料", "检测资料"), "embedment": ("预埋件接口",),
        "deck": ("组合楼板接口",), "purlin": ("幕墙檩条接口",), "anchor": ("地脚螺栓接口",),
    },
}
_UNKNOWN = re.compile(r"(?:\[A\d+\]\s*)?(?:待填|待补|未知|未提供|未说明|UNSPECIFIED|TBD)?", re.I)


def _clean(value: str, key: str = "") -> str:
    value = value.strip()
    return MISSING if _UNKNOWN.fullmatch(value) else literal_parameter(key, value)


def _input_table(lines: list[str]) -> str:
    normalized = []
    for line in lines:
        inner = line.strip()[1:]
        if inner.endswith("|"):
            inner = inner[:-1]
        cells = re.split(r"(?<!\\)\|", inner)
        normalized.append("| " + " | ".join(cell.strip().replace(r"\|", "&#124;") for cell in cells) + " |")
    return "\n".join(normalized)


class _Facts:
    """Preserve row boundaries before interpreting identity fields or column order."""

    def __init__(self, post: str, text: str):
        self.post, self.text = post, text or ""
        mapping = {**_COMMON, **_POST_FIELDS[post]}
        self.aliases = {alias.casefold(): key for key, names in mapping.items() for alias in names}
        labels = "|".join(sorted(map(re.escape, self.aliases), key=len, reverse=True))
        pattern = re.compile(r"(?<![\w])(" + labels + r")\s*[:：=]\s*", re.I)
        self.records: list[dict[str, str]] = []
        self.context: dict[str, str] = {}
        current: dict[str, str] = {}
        explicit: set[str] = set()
        object_keys = {"system", "component", "unit"}
        object_seen = False
        top_level_text: list[str] = []

        def flush() -> None:
            nonlocal current, explicit
            if current:
                self.records.append(current)
            current = {}
            explicit = set()

        def add(entries: list[tuple[str, str]], *, row: bool = False) -> None:
            nonlocal current, object_seen
            keys = {key for key, _ in entries}
            # A jurisdiction on an object is local to that object. It must not
            # become the next object's default, even when its column comes first.
            top_level = not object_seen and not keys.intersection(object_keys)
            object_seen = object_seen or bool(keys.intersection(object_keys))
            # Inspect the complete input line before choosing its record. A load
            # column before a new system name still belongs to the new system.
            identities = {"system", "component", "unit", "jurisdiction"}
            starts_object_after_header = bool(keys.intersection(object_keys)) and bool(current) and explicit <= {"project", "jurisdiction"}
            if row or starts_object_after_header or any(key in explicit for key, _ in entries if key in identities):
                flush()
            for key, value in entries:
                if key in explicit:
                    flush()
                if not current:
                    current = dict(self.context)
                current[key] = value
                explicit.add(key)
                if key in {"project", "unit"} or (key == "jurisdiction" and top_level):
                    self.context[key] = value
                if key == "jurisdiction" and top_level:
                    top_level_text.append("辖区：" + value)
            if row:
                flush()

        lines = self.text.splitlines()
        index = 0
        narrative_checked_until = 0
        after_table = False
        while index < len(lines):
            line = lines[index]
            if not line.lstrip().startswith("|") and index >= narrative_checked_until:
                end = index + 1
                while end < len(lines) and not lines[end].lstrip().startswith("|"):
                    end += 1
                narrative_checked_until = end
                context = {key: value for key, value in self.context.items() if not after_table or key != "unit"}
                parsed = parse_narrative(post, "\n".join(lines[index:end]), self.aliases, context,
                                         self.records if after_table else None)
                if parsed is not None:
                    flush()
                    self.records.extend(parsed.records)
                    self.context = parsed.context
                    top_level_text.extend(parsed.top_level)
                    object_seen = object_seen or any(set(record) & object_keys for record in parsed.records)
                    index = end
                    continue
            if line.lstrip().startswith("|"):
                end = index + 1
                while end < len(lines) and lines[end].lstrip().startswith("|"):
                    end += 1
                for _, rows in tables_from_md(_input_table(lines[index:end])):
                    headers = [self.aliases.get(cell.casefold()) for cell in rows[0]]
                    if sum(key is not None for key in headers) >= 2:
                        for cells in rows[1:]:
                            if len(cells) == len(headers):
                                add([(key, _clean(value, key)) for key, value in zip(headers, cells) if key], row=True)
                    else:
                        for cells in rows[1:]:
                            if len(cells) >= 2 and cells[1] and not any(cells[2:]) and cells[0].casefold() in self.aliases:
                                key = self.aliases[cells[0].casefold()]
                                add([(key, _clean(cells[1], key))])
                index = end
                after_table = True
                continue
            matches = list(pattern.finditer(line))
            entries = []
            for field_index, match in enumerate(matches):
                end = matches[field_index + 1].start() if field_index + 1 < len(matches) else len(line)
                value = line[match.end():end].strip()
                if field_index + 1 < len(matches) and not re.search(r"&(?:#\d+|#x[0-9a-f]+|[a-z][a-z0-9]+);$", value, re.I):
                    value = re.sub(r"[；;,，]\s*$", "", value)
                key = self.aliases[match.group(1).casefold()]
                entries.append((key, _clean(value, key)))
            if not object_seen and not any(key in object_keys for key, _ in entries):
                top_level_text.append(line)
            add(entries)
            index += 1
        flush()
        # Metadata-only lines preceding a table describe the document, not an
        # extra empty system. Keep metadata separately and never copy quantities.
        metadata = {"project", "unit", "jurisdiction", "basis", "drawings", "calculations", "source", "interface",
                    "cn_basis", "sg_basis", "eu_basis", "cn_interface", "sg_interface", "eu_interface"}
        self.all_records = list(self.records)
        concrete = [record for record in self.records if set(record) - metadata]
        self.records = concrete or self.records or [{}]
        self.zone = infer_jurisdiction(self.text)
        default = self.context.get("jurisdiction")
        self.default_zone = infer_jurisdiction("\n".join(top_level_text))
        if default is not None:
            self.default_zone = infer_jurisdiction(default) if default != MISSING else "UNSPECIFIED"

    def all(self, key: str) -> str:
        values = dict.fromkeys(record[key] for record in self.all_records if key in record and record[key] != MISSING)
        return "；".join(values) or MISSING

    def record_zone(self, record: dict[str, str]) -> str:
        if "jurisdiction" not in record:
            return self.default_zone
        value = record["jurisdiction"]
        return infer_jurisdiction(value) if value != MISSING else "UNSPECIFIED"

    def table(self, title: str, fields: Sequence[tuple[str, str]], *, pending: Sequence[str] = ()) -> str:
        identity = "构件" if self.post == "steel" else "系统"
        id_key = "component" if self.post == "steel" else "system"
        headers = ("辖区", "单体/部位", identity, *(label for label, _ in fields), *pending, "资料来源")
        rows = []
        for record in self.records:
            rows.append((self.record_zone(record), record.get("unit", MISSING), record.get(id_key, MISSING),
                         *(record.get(key, MISSING) for _, key in fields), *(MISSING for _ in pending), record.get("source", MISSING)))
        return _table(title, headers, rows)


def _cell(value: str) -> str:
    return html.escape(value, quote=False).replace("|", "&#124;").replace("\r", " ").replace("\n", " ")


def _table(title: str, headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = [f"## {title}", "", "| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows)
    return "\n".join(lines) + "\n\n"


def _header(facts: _Facts) -> str:
    return (
        f"# {_TITLES[facts.post]}（AI 草稿）\n\n"
        "> 草稿声明：仅供内部讨论，不替代正式设计、计算书或审查验收；未进行数值计算、设备或构件选型。"
        "用户参数仅抄录待核，不能作为本稿确认的设计结果。\n\n"
        f"- 辖区：{facts.zone}\n- submit_blocked=true\n"
        "- 缺失数值保持 [A001] / UNSPECIFIED；图号和依据仅登记用户提供的名称，未核原文与适用性。\n\n"
        + _table("工程与资料登记", ("项目", "单体/部位", "用户图号清单", "用户计算书"),
                 [(facts.all("project"), facts.all("unit"), facts.all("drawings"), facts.all("calculations"))])
        + _table("用户资料来源与归属", ("单体/部位", "系统/构件", "资料来源", "归属状态"), [
            (record.get("unit", MISSING), record.get("component" if facts.post == "steel" else "system", MISSING),
             record["source"], "仅登记原文；未明确归属的资料待用户分配")
            for record in facts.all_records if record.get("source", MISSING) != MISSING
        ] or [(MISSING, MISSING, MISSING, "待用户提供")])
    )


def _references(facts: _Facts, interfaces: str) -> str:
    if facts.zone == "DUAL":
        zones = [zone for zone in ("CN", "SG", "EU") if
                 re.search(r"(?<![A-Za-z0-9_])" + zone + r"(?![A-Za-z0-9_])", facts.text, re.I)
                 or any(infer_jurisdiction(record.get("jurisdiction", "")) == zone for record in facts.all_records)]
        if len(zones) < 2:
            zones = ["辖区 A（待指明）", "辖区 B（待指明）"]
    else:
        zones = [facts.zone]
    result = ""
    for zone in zones:
        records = [record for record in facts.all_records if facts.record_zone(record) == zone]
        scoped_basis = facts.all(zone.lower() + "_basis")
        scoped_interface = facts.all(zone.lower() + "_interface")
        rows = [(zone, record.get("basis", scoped_basis), record.get("drawings", MISSING),
                 record.get("calculations", MISSING), record.get("interface", scoped_interface), "UNSPECIFIED / unverified") for record in records]
        result += _table("依据与接口 · " + zone, ("辖区", "用户依据文件", "用户图号", "用户计算书", "用户接口要求", "条款与核验状态"),
                         rows or [(zone, scoped_basis, MISSING, MISSING, scoped_interface, "UNSPECIFIED / unverified")])
    if facts.zone != "UNSPECIFIED":
        unassigned = [record for record in facts.all_records if facts.record_zone(record) not in {"CN", "SG", "EU"}
                      and any(key in record for key in ("basis", "interface", "drawings", "calculations"))]
        if unassigned:
            result += _table("跨辖区资料待分配", ("依据文件", "图号", "计算书", "接口要求", "状态"), [
                tuple(record.get(key, MISSING) for key in ("basis", "drawings", "calculations", "interface")) + ("待用户指明所属辖区，不跨栏套用",) for record in unassigned
            ])
    result += "专业交接清单（待协调）：" + interfaces + "。核对职责、图纸版本及计算资料后由责任人签认；本稿不填写审查结论。\n"
    return result


def _plumbing(f: _Facts) -> str:
    out = _header(f)
    out += f.table("系统范围", (("用户范围", "scope"), ("建筑功能", "function")))
    out += "生活给水、直饮水、热水、污废水、雨水、中水及消防给水是否纳入，以用户范围为准；未列系统不自动设置。\n\n"
    out += f.table("水源与市政接驳", (("水源", "water_source"), ("接管位置", "connection"), ("用户市政管径", "mains_pipe"), ("用户水压", "pressure"), ("水表井", "water_meter")))
    out += f.table("生活给水与分区", (("分区", "zone"), ("器具当量记录", "fixtures"), ("水箱方案", "tank"), ("用户既有泵型", "pump"), ("计量", "metering")), pending=("本稿选泵结果",))
    out += f.table("排水与标高衔接", (("室内地面标高", "floor_level"), ("出户标高", "outlet_level"), ("市政井标高", "municipal_level"), ("市政井编号", "municipal_well"), ("用户流量", "water_flow"), ("用户管径", "pipe"), ("立管编号", "riser"), ("排水路径", "drain_route")), pending=("本稿管径计算结果",))
    out += "待核室内地面、出户与市政井标高的衔接，立管编号与系统/平面资料一致；缺标高、流量或水压不定管径、泵型。排水布置另核卧室及起居、用餐空间的避让。\n\n"
    out += f.table("雨水、溢流与回用", (("汇水资料", "catchment"), ("降雨资料", "rainfall"), ("溢流措施", "overflow"), ("回用方案", "reuse")))
    out += f.table("消防水量与泵房资料", (("用户消防系统", "fire_system"), ("用户消防水量", "fire_flow"), ("用户消防水压", "fire_pressure"), ("泵房资料", "pump_room"), ("计算书", "calculations")), pending=("本稿消防水量计算结果",))
    out += f.table("特殊部位", (("消防电梯井底排水", "lift_drain"), ("集水坑", "pit"), ("隔油设施", "grease"), ("化粪设施", "septic")))
    out += f.table("节水与计量", (("计量方式", "metering"), ("超压控制", "pressure_control"), ("中水/雨水回用", "reuse")))
    return out + _references(f, "建筑降板与卫生间、结构留洞、电气消防电源、暖通冷却补水、人防密闭套管；消防设置条件与消防岗协调，水量以给排水计算资料核对")


def _hvac(f: _Facts) -> str:
    out = _header(f)
    out += f.table("系统范围与空气计算参数", (("用户范围", "scope"), ("室内参数", "indoor"), ("室外参数", "outdoor"), ("参数来源", "parameter_source")))
    out += f.table("冷热源方案与负荷资料", (("用户冷热源方案", "heat_source"), ("用户冷负荷", "cooling_load"), ("用户热负荷", "heating_load"), ("其他负荷记录", "load"), ("逐项逐时负荷", "hourly_load"), ("计算书", "calculations"), ("用户既有主机型号", "model")), pending=("本稿主机选型",))
    out += "冷热源方案仅登记待比选；无完整室内外参数及逐项逐时负荷不选主机，单一总负荷不能代替完整选型依据。\n\n"
    out += f.table("风系统与水系统", (("分区", "zone"), ("用户新风量", "fresh_air"), ("用户风量", "air_flow"), ("用户水流量", "water_flow"), ("用户风管断面", "duct"), ("用户水管径", "pipe")), pending=("本稿风管断面", "本稿水力计算结果"))
    out += f.table("防烟、排烟与联锁", (("防烟分区", "smoke_zone"), ("自然排烟窗", "window"), ("用户排烟量", "smoke_flow"), ("加压送风记录", "pressurization"), ("补风路径", "makeup_air"), ("联锁要求", "interlock")), pending=("本稿排烟量计算结果",))
    out += "防烟分区、自然排烟开口与建筑协调，风口及联锁资料待核；人防通风另交人防岗位，不与平时空调合并作结论。\n\n"
    out += f.table("机房、冷却塔与竖向空间", (("机房", "room"), ("冷却塔", "tower"), ("竖井", "shaft"), ("室外机位", "outdoor_unit")))
    out += f.table("消声、隔振、冷凝水与保温", (("消声", "noise"), ("隔振", "vibration"), ("冷凝水", "condensate"), ("保温", "insulation")))
    out += f.table("节能资料", (("用户效率资料", "efficiency"), ("计算书", "calculations")), pending=("本稿节能核验结果",))
    return out + _references(f, "建筑排烟开口与机位、结构设备荷载及洞口、电气设备电源、给排水冷却补水和冷凝水；人防通风另行交接")


def _electrical(f: _Facts) -> str:
    out = _header(f)
    out += f.table("系统范围与市政电源", (("用户范围", "scope"), ("用户回路数", "circuits"), ("用户供电电压", "voltage"), ("计量", "metering"), ("用户自备电源安排", "backup")))
    out += f.table("负荷计算书数据登记", (("设备容量", "capacity"), ("需要/利用系数", "factor"), ("有功记录", "active"), ("无功记录", "reactive"), ("视在记录", "apparent"), ("其他负荷记录", "load"), ("计算书", "calculations")), pending=("本稿计算负荷",))
    out += f.table("变配电方案核对", (("用户变压器台数", "transformer_count"), ("用户变压器容量", "transformer"), ("低压配电安排", "distribution")), pending=("本稿变压器选型", "本稿柴油机/母线槽选型"))
    out += "负荷表须与设备资料核对；用户容量只是来源记录，本稿不据总数选变压器、柴油机或母线槽，也不生成容量或台数。\n\n"
    out += f.table("照明与疏散指示", (("一般照明", "lighting"), ("应急照明/疏散指示", "emergency_lighting"), ("图号", "drawings")))
    out += f.table("防雷、接地与等电位", (("用户防雷类别", "lightning"), ("用户接地电阻目标", "grounding"), ("等电位安排", "equipotential"), ("依据文件", "basis")), pending=("实测与核验结论",))
    out += f.table("线路敷设", (("敷设路径", "cable_route"), ("桥架", "tray"), ("用户间距要求", "spacing")), pending=("本稿电缆/桥架尺寸",))
    out += f.table("电气消防接口", (("消防电源", "fire_power"), ("消防线路", "fire_cable"), ("用户切换时间要求", "transfer"), ("应急照明", "emergency_lighting")))
    out += f.table("弱电电源与接地交接", (("弱电机房电源", "weak_power"), ("接地端子", "weak_ground"), ("用户接口要求", "interface")))
    out += "弱电系统清单、报警深化及点位另交智能化弱电岗；本岗仅登记电源、接地和协调条件。\n\n"
    out += f.table("电气节能资料", (("用户效率资料", "efficiency"), ("计量", "metering")))
    return out + _references(f, "结构预留洞、给排水消防泵控制、暖通设备电源、智能化弱电电源接地和人防电源；消防负荷及联动由对应专业核对")


def _fire(f: _Facts) -> str:
    out = _header(f)
    out += "高风险岗位：本专篇不替代消防设计审查验收，不作审图通过或工程放行结论。\n\n"
    out += f.table("工程概况", (("建筑分类", "classification"), ("用户建筑高度", "height"), ("用户层数", "floors"), ("建筑功能", "function"), ("用户耐火等级", "fire_grade")))
    out += f.table("总图与消防救援条件", (("消防车道", "fire_lane"), ("登高操作场地", "staging"), ("消防扑救面", "fire_face"), ("室外消火栓", "outdoor_hydrant")))
    out += f.table("建筑防火与分区", (("防火分区", "compartment"), ("用户分区面积", "compartment_area"), ("防火墙/楼板", "fire_wall"), ("防火门窗", "fire_door"), ("幕墙层间封堵", "curtain_stop")), pending=("分区合规核验结果",))
    out += f.table("安全疏散与避难", (("用户安全出口记录", "exits"), ("用户疏散宽度", "egress_width"), ("用户疏散距离", "egress_distance"), ("避难层/间安排", "refuge"), ("应急照明", "emergency_lighting")), pending=("疏散核验结果",))
    out += f.table("消防给水与灭火", (("用户拟设系统", "fire_system"), ("用户消防水量", "fire_flow"), ("用户消防水压", "fire_pressure"), ("给排水计算资料", "calculations")), pending=("本稿水量/喷淋参数计算",))
    out += f.table("防烟排烟", (("防烟分区", "smoke_zone"), ("用户防排烟方式", "smoke_method"), ("用户排烟量", "smoke_flow"), ("联动要求", "interlock")))
    out += f.table("火灾报警与联动", (("报警保护对象", "alarm"), ("控制接口", "control"), ("联动要求", "interlock")))
    out += f.table("消防电气", (("消防电源", "fire_power"), ("配电线路", "fire_cable"), ("应急照明", "emergency_lighting")))
    out += f.table("装修与既有使用边界", (("装修/改造范围", "renovation"), ("既有分区边界", "existing_boundary"), ("建筑功能", "function")))
    out += "装修变化须核对出口、疏散空间和既有分区，借用疏散条件不能仅凭本篇认定；本篇不代替装修消防资料。\n\n"
    out += f.table("报审资料待办目录", (("用户资料目录", "documents"), ("用户图号清单", "drawings"), ("用户计算书", "calculations")), pending=("资料核对签认",))
    return out + _references(f, "建筑防火分区与疏散、幕墙层间封堵、给排水消防水量、暖通防排烟、电气消防供电、智能化弱电报警联动；装修与消防双岗交接")


def _steel(f: _Facts) -> str:
    out = _header(f)
    out += "高风险岗位：无跨度、荷载和完整计算资料不定量；本稿不选择梁柱截面、螺栓或焊缝尺寸，不替代主体结构计算书。\n\n"
    out += f.table("结构体系与构件范围", (("用户体系", "structure"), ("用户跨度", "span"), ("用户高度", "height"), ("范围", "scope")), pending=("本稿体系选定",))
    out += f.table("荷载与计算资料", (("屋面荷载", "roof_load"), ("吊车资料", "crane"), ("风荷载/风压", "wind"), ("地震资料", "seismic"), ("温度作用", "temperature"), ("其他荷载", "load"), ("计算书", "calculations")), pending=("本稿荷载组合计算",))
    out += f.table("材料与构件规格记录", (("用户钢材牌号", "material"), ("材料来源", "material_source"), ("用户既有/提出规格", "specification")), pending=("本稿构件截面选型",))
    out += f.table("连接与节点资料", (("用户连接方式", "connection"), ("用户螺栓规格", "bolt"), ("用户焊缝尺寸", "weld"), ("节点图号", "node"), ("用户指定图集", "atlas")), pending=("本稿螺栓/焊缝选型",))
    out += "节点须核对明确详图或用户指定图集，连接方式和尺寸抄录不等于核验；不得用未说明的常规做法补齐节点。\n\n"
    out += f.table("稳定与支撑", (("柱间支撑", "bracing"), ("系杆", "tie"), ("隅撑", "knee")), pending=("稳定计算核验结果",))
    out += f.table("防腐涂装", (("用户除锈等级", "derust"), ("防腐体系", "coating"), ("用户涂层厚度", "coating_thickness")), pending=("本稿涂层厚度选定",))
    out += f.table("构件防火保护", (("用户防火做法", "fireproof"), ("用户耐火极限", "fire_rating"), ("用户防火涂料厚度", "fireproof_thickness"), ("建筑/消防来源", "fire_source")), pending=("本稿防火厚度计算",))
    out += f.table("加工、运输、安装与检测资料", (("加工", "fabrication"), ("运输", "transport"), ("安装", "erection"), ("焊缝检测", "inspection")))
    out += f.table("实体连接界面", (("混凝土预埋件", "embedment"), ("组合楼板", "deck"), ("幕墙/檩条", "purlin"), ("地脚螺栓", "anchor")))
    return out + _references(f, "建筑围护和耐火要求、主体结构预埋件及组合楼板、幕墙檩条、结构/岩土地脚螺栓与基础；加工安装及检测原始资料待核")


def build_draft(expert_id: str, tool_name: str, text: str) -> str | None:
    if _TOOLS.get(expert_id) != tool_name:
        return None
    facts = _Facts(expert_id, text)
    return {"plumbing": _plumbing, "hvac": _hvac, "electrical": _electrical,
            "fire-protect": _fire, "steel": _steel}[expert_id](facts)
