"""BIM post drafts from supplied records, with no model inspection or calculation."""

from __future__ import annotations

from dataclasses import dataclass, field
import html
import re
from collections.abc import Iterable


DISCLAIMER = (
    "本文件由 Civil Buddy 根据用户输入生成，仅供内部讨论与起草。"
    "不构成设计文件、法定专项施工方案、交底签认件、监理指令、专家论证材料或开工/竣工验收依据。"
)
UNKNOWN = "UNSPECIFIED"
_TOOLS = {"bim-coord": "bim-coord__clash", "bim-qto": "bim-qto__rules", "bim-deliver": "bim-deliver__lod"}
_FIELDS = {
    "project": ("项目名称", "工程名称", "项目"), "zone": ("辖区",),
    "building": ("单体", "区域"), "purpose": ("提量目的", "交付目的", "使用目的", "目的"),
    "stage": ("项目阶段", "模型阶段", "阶段"), "recipient": ("接收方",),
    "basis": ("计算规范", "计算依据", "交付依据", "项目BEP", "BEP", "依据"),
    "scope": ("模型范围", "范围"), "check_scope": ("检查范围",),
    "model": ("模型名称", "模型文件", "模型"), "model_ref": ("关联模型", "来源模型"),
    "discipline": ("参与专业", "专业"), "version": ("模型版本", "文件版本", "版本"),
    "date": ("模型日期", "版本日期", "日期"), "format": ("模型格式", "文件格式", "格式"),
    "source": ("来源文件", "数量来源", "来源"), "file_name": ("实际文件名", "文件名"),
    "coordinates": ("坐标系",), "origin": ("共享原点", "原点"), "north": ("北向",),
    "model_unit": ("模型单位",), "elevation": ("高程系统",), "grid": ("轴网标高", "轴网"),
    "alignment": ("对齐记录", "坐标对齐状态"), "shared_coordinates": ("共享坐标发布记录",),
    "test_set": ("测试集", "检查集"), "pair": ("专业对",),
    "tolerance": ("检查容差", "容差"), "ignore_size": ("忽略尺寸",),
    "duplicate": ("重复处理", "重复实例", "重复规则"), "links": ("链接处理", "链接文件"),
    "placeholder": ("占位族处理", "占位族"),
    "issue_id": ("问题编号", "碰撞编号", "编号"), "description": ("问题描述", "碰撞问题", "问题"),
    "issue_type": ("碰撞类型", "问题类型", "类型"), "location": ("问题位置", "位置"),
    "component_a": ("构件A", "构件 A"), "component_b": ("构件B", "构件 B"),
    "owner": ("责任专业", "责任人", "合成责任人"), "status": ("用户状态", "状态"),
    "deadline": ("截止日期", "截止时间", "截止"), "evidence": ("截图链接", "BCF链接", "截图或BCF", "证据"),
    "closure": ("关闭依据",), "interface_id": ("接口编号",), "information": ("提资项", "提资内容"),
    "provider": ("提资方",), "receiver": ("接收专业",),
    "meeting_date": ("会议时间",), "attendees": ("出席记录",), "decision": ("会议决议",),
    "next_merge": ("下次合成节点",),
    "category": ("分部分项", "构件类别", "工程分类", "算量项", "条目名称"),
    "type_name": ("构件类型名", "类型名"), "material": ("材质",),
    "level": ("楼层", "区段"), "phase": ("施工段", "相位"),
    "include": ("包含项", "包含", "过滤条件"), "exclude": ("排除项", "排除"), "reason": ("过滤理由", "理由"),
    "rule": ("计量规则", "计量口径", "口径"), "unit": ("计量单位", "单位"),
    "quantity": ("用户工程量", "工程量", "数量"), "openings": ("扣洞规则", "孔洞扣减", "扣洞"),
    "overlap": ("重叠规则", "重叠处理"), "deduction": ("扣减说明", "扣减"),
    "attribute": ("属性路径", "数量集", "属性名"), "guid": ("构件GUID", "GUID"),
    "reconcile": ("校核来源", "对照记录"), "deviation": ("偏差记录",),
    "lod": ("LOD要求", "LOD", "细度要求"), "geometry": ("几何要求",),
    "attributes": ("属性要求",), "documents": ("文档要求", "图纸要求"),
    "split": ("拆分规则",), "max_size": ("单文件上限",), "naming": ("命名规则",),
    "deliverable": ("交付物名称", "交付物"), "required": ("是否必交", "必交要求"),
    "delivery_date": ("交付节点", "交付日期"), "container_state": ("容器状态", "发布状态"),
    "change": ("版本变更",), "previous": ("前版", "上一版本"),
    "ifc_version": ("IFC版本",), "export_view": ("导出视图",), "export_settings": ("导出设置",),
    "exchange_record": ("交换抽检记录",), "open_record": ("模型打开记录",),
    "attribute_record": ("属性检查记录",), "clash_record": ("碰撞检查记录", "碰撞状态"),
    "quantity_record": ("算量校核记录",), "acceptance": ("验收记录", "验收状态"),
    "missing": ("缺项", "未建模系统"), "excluded_delivery": ("不交清单",),
}
_COORD_CONTEXT = {"test_set", "pair", "check_scope", "tolerance", "ignore_size", "duplicate", "links", "placeholder"}
_QUANTITY_RE = re.compile(
    r"(?:约\s*)?[+-]?\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][+-]?\d+)?"
    r"(?:\s*(?:m[²³23]?|mm|㎡|立方米|立方|平方米|平米|米|个|件|台|套|樘|kg|t|吨|千克))?", re.I)


def _cell(value: object) -> str:
    return html.escape(_input_value(value), quote=False).replace("|", "&#124;")


def _input_value(value: object) -> str:
    return str(value).replace("\r", " ").replace("\n", "；").strip()


def _table(headers: Iterable[str], rows: Iterable[Iterable[object]]) -> str:
    columns = tuple(headers)
    return "\n".join([
        "| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |",
        *("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows),
    ])


def _value(row: dict[str, str], key: str) -> str:
    return row.get(key) or UNKNOWN


@dataclass
class _Input:
    context: dict[str, str] = field(default_factory=dict)
    records: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    contexts: list[dict] = field(default_factory=list)

    def rows(self, kind: str) -> list[dict[str, str]]:
        return self.records.get(kind) or [self.context]


def _parse(text: str, post: str) -> _Input:
    from packing_assistant.post_drafts.bim_observations import parse
    return parse(text, post)


def _header(title: str, text: str, boundary: str, data: _Input) -> list[str]:
    zones = {context.get("project_zone", UNKNOWN) for context in data.contexts}
    zones.update(row["zone"] for records in data.records.values() for row in records if "zone" in row)
    known = zones-{UNKNOWN}
    zone = next(iter(known)) if len(known) == 1 else "逐对象登记，不合并辖区" if len(known) > 1 else UNKNOWN
    return [f"# {title}（AI 草稿 · 内部讨论）", "", DISCLAIMER, "", boundary, "",
        f"- 辖区：{zone}（仅汇总明确输入，不代填对象空栏）" if zone != UNKNOWN else f"- 辖区：{zone}", "- submit_blocked=true", ""]


def _register(data: _Input) -> str:
    return _table(("登记项", "用户提供 / 缺口"), [
        (label, _value(data.context, key)) for label, key in (
            ("工程名称", "project"), ("单体 / 区域", "building"), ("使用目的", "purpose"),
            ("项目阶段", "stage"), ("接收方", "recipient"),
        )])


def _models(data: _Input) -> str:
    return _table(("模型名称 / 文件", "专业", "版本", "日期", "范围", "格式", "阶段", "用户来源", "本轮读取状态"), [
        (*(_value(row, key) for key in ("model", "discipline", "version", "date", "scope", "format", "stage", "source")), "未读取模型")
        for row in data.rows("models")
    ])


def _coordinates(data: _Input) -> str:
    rows = data.rows("models")
    if data.records.get("models") and any(key in data.context for key in ("coordinates", "origin", "north", "model_unit", "elevation", "grid", "alignment")):
        rows = [{**data.context, "model": "全局约定（用户提供）"}, *rows]
    return _table(("模型 / 范围", "坐标系", "原点", "北向", "模型单位", "高程系统", "轴网 / 标高", "用户对齐记录", "核验状态"), [
        (*(_value(row, key) for key in ("model", "coordinates", "origin", "north", "model_unit", "elevation", "grid", "alignment")), UNKNOWN)
        for row in rows
    ])


def _sources(data: _Input, text: str) -> str:
    contexts = data.contexts or [data.context]
    return _table(("辖区", "用户指定依据", "年份 / 版本", "条款", "核实状态"), [
        (_value(context, "project_zone"), _value(context, "basis"), _value(context, "basis_version"), UNKNOWN, "unverified；原文与适用性待核") for context in contexts
    ]) + "\n\n本稿仅整理资料和检查项；未引用或认定规范条文，不把模型记录改写为签认结论。"


def _ownership(data: _Input, post: str) -> list[str]:
    from packing_assistant.post_drafts.bim_observations import EXTRA
    labels = {key: names[0] for key, names in {**_FIELDS, **EXTRA}.items()}
    out = ["## 项目与文件依据归属", "", _table(("项目", "项目阶段", "总体辖区（不代填对象）", "输入文件", "用户依据", "CN用户依据", "SG用户依据", "EU用户依据", "核验状态"), [
        tuple(_value(context, key) for key in ("project", "stage", "project_zone", "_origin", "basis", "CN_basis", "SG_basis", "EU_basis"))+("unverified",)
        for context in data.contexts if any(not key.startswith("_") and (key != "project_zone" or value != UNKNOWN) for key, value in context.items())] or [(UNKNOWN,)*8+("unverified",)]), ""]
    out += ["## 项目约定与资料位置", "", _table(("项目", "项目阶段", "参数栏目", "用户值（未核验）", "输入文件", "输入位置（非原件页码）", "状态"), [
        (context.get("project", UNKNOWN), context.get("stage", UNKNOWN), labels.get(key, key), value,
         context.get("_origin") or UNKNOWN, context.get("_sources", {}).get(key, UNKNOWN), "未核验")
        for context in data.contexts for key, value in context.items() if not key.startswith("_") and value != UNKNOWN
    ] or [(UNKNOWN,)*6+("未核验",)]), ""]
    out += ["## 模型单体与版本来源", "", _table(("模型", "单体", "模型版本", "模型日期", "用户来源", "输入文件", "对象辖区", "核验状态"), [
        tuple(_value(row, key) for key in ("model", "building", "version", "date", "source", "_origin", "zone"))+("未读取或核验",)
        for row in data.rows("models")]), ""]
    if post == "bim-coord":
        title, kind, keys = "协调问题责任与版本对照", "issues", ("issue_id", "model_ref", "location", "description", "owner", "owner_person", "deadline", "status", "evidence", "closure")
        columns = ("问题编号", "关联模型", "构件位置", "问题原文", "责任专业 / 责任方", "责任人", "完成时限", "用户状态", "用户证据", "用户关闭依据")
    elif post == "bim-qto":
        title, kind, keys = "算量模型与规则来源对照", "quantities", ("model_ref", "category", "material", "include", "exclude", "rule", "unit", "quantity", "source", "openings", "deduction")
        columns = ("模型", "构件类别", "材质", "过滤包含", "过滤排除", "计量规则", "计量单位", "用户数量原记录（未核算）", "用户来源", "孔洞扣减", "其他扣减")
    else:
        title, kind, keys = "模型交付目的与期限", "models", ("model", "building", "purpose", "lod", "version", "format", "recipient", "delivery_date", "file_name", "clash_record", "acceptance")
        columns = ("模型", "单体", "交付目的", "用户LOD", "用户版本", "交付格式", "交付对象", "交付日期", "文件名", "用户碰撞复查记录", "用户验收记录")
    out += ["## "+title, "", _table(columns+("核验状态", "签认"), [
        tuple(_value(row, key) for key in keys)+("未计算、检查或验收；仅用户输入", "") for row in data.rows(kind)]), ""]
    linked = []
    for row_kind, records in data.records.items():
        if row_kind == "models":
            continue
        for row in records:
            model = row.get("_model", {})
            context = row.get("_context_data", {})
            linked.append((context.get("project", UNKNOWN),
                           row.get("issue_id") or row.get("category") or row.get("interface_id") or row.get("deliverable") or UNKNOWN,
                           _value(row, "model_ref"), _value(model, "version"), _value(model, "source"),
                           _value(row, "_origin"), _value(model, "_origin"),
                           "仅用户记录关联；未读取模型" if model else "关联未确定；不得借用其他模型版本"))
    out += ["## 对象与模型版本关联（仅明确归属）", "", _table(
        ("项目", "专业对象", "关联模型", "关联模型用户版本", "关联模型用户来源", "对象输入文件", "模型输入文件", "关联核验状态"),
        linked or [(UNKNOWN,)*7+("关联未确定",)]), ""]
    facts = []
    for rows in data.records.values():
        for row in rows:
            context = row.get("_context_data", {})
            object_name = row.get("model") or row.get("issue_id") or row.get("category") or row.get("deliverable") or UNKNOWN
            for key, value in row.items():
                if not key.startswith("_") and value != UNKNOWN:
                    label = labels.get(key, key)
                    facts.append((context.get("project", UNKNOWN), object_name, label, value, row.get("_origin") or UNKNOWN, row.get("_sources", {}).get(key, UNKNOWN), "未核验"))
    out += ["## 对象参数与资料位置", "", _table(("项目", "专业对象", "参数栏目", "用户值（未核验）", "输入文件", "输入位置（非原件页码）", "状态"), facts or [(UNKNOWN,)*6+("未核验",)]), ""]
    return out


def _coord(text: str) -> str:
    data = _parse(text, "bim-coord")
    lines = _header("碰撞 / 协调纪要", text, "仅整理用户提供的问题记录。本轮未打开或扫描 IFC / 原位模型，未执行碰撞检查，不提供碰撞总数或零碰撞证明。", data)
    lines += ["## 1 项目与合成模型登记", "", _register(data), "", _models(data), "",
        "纪要编号：（空栏）。各模型版本分行保留，不以一个模型的版本补全其他模型。", "",
        "## 2 合成前提与坐标核对", "", _coordinates(data), "",
        "原点、单位、轴网或高程未确认时，检查集保持待执行，交模型交付与测量人员复核对齐资料。", "",
        "## 3 检查范围、容差与过滤", "",
        _table(("检查项", "用户提供 / 缺口", "执行状态"), [
            (label, _value(data.context, key), "未执行") for label, key in (
                ("测试集", "test_set"), ("专业对", "pair"), ("检查范围", "check_scope"),
                ("容差", "tolerance"), ("忽略尺寸", "ignore_size"), ("重复实例", "duplicate"),
                ("链接代理", "links"), ("占位族", "placeholder"),
            )]), "",
        "## 4 碰撞类型分类栏", "",
        _table(("分类", "待核对象", "本轮检查结果"), [
            ("硬碰撞", "用户报告的几何相交问题", UNKNOWN),
            ("间隙不足", "用户指定的检修、保温或通行空间", UNKNOWN),
            ("预留洞未做", "用户指定的洞口与墙梁板接口", UNKNOWN),
            ("4D / 工序冲突", "用户提供进度与工作面挂接记录", UNKNOWN),
        ]), "",
        "## 5 用户问题清单", "",
        _table(("编号", "关联模型", "位置 / 层轴网", "用户类型", "问题描述", "构件 A", "构件 B", "责任专业", "用户记录状态", "截止日期", "截图 / BCF", "用户关闭依据", "本轮核验"), [
            (*(_value(row, key) for key in ("issue_id", "model_ref", "location", "issue_type", "description", "component_a", "component_b", "owner", "status", "deadline", "evidence", "closure")), "未核验")
            for row in data.rows("issues")
        ]), "", "用户写「关闭」只保留为用户记录；关闭依据与改模版本未复核时不认定闭环。空表不表示没有问题。", "",
        "## 6 专业提资接口", "",
        _table(("接口编号", "提资方", "接收专业", "提资内容", "格式", "截止时间", "关联模型", "用户记录状态"), [
            tuple(_value(row, key) for key in ("interface_id", "provider", "receiver", "information", "format", "deadline", "model_ref", "status"))
            for row in data.rows("interfaces")
        ]), "",
        "## 7 会议与闭环记录", "",
        _table(("记录项", "用户提供 / 缺口", "本轮核验"), [
            (label, _value(data.context, key), "未核验") for label, key in (
                ("会议时间", "meeting_date"), ("出席记录", "attendees"),
                ("会议决议", "decision"), ("下次合成节点", "next_merge"),
            )]), "", "签认：（空栏）。每条修改须关联具体版本和专业确认记录，协调纪要不代替设计变更。", "",
        "## 8 依据、自检与相邻岗位", "", _sources(data, text), "",
        "自检：模型与问题逐行；无自动编号、容差或总数；关闭状态未经核验；专业提资独立。LOD / 命名 / 拆分交模型交付，过滤出量交模型算量，改图交设计统筹，现场危大交危大识别。", "",
    ]
    return "\n".join(lines+_ownership(data, "bim-coord"))


def _quantity(row: dict[str, str]) -> str:
    supplied = row.get("quantity", "")
    return supplied if _QUANTITY_RE.fullmatch(supplied) else UNKNOWN


def _qto(text: str) -> str:
    data = _parse(text, "bim-qto")
    rows = data.rows("quantities")
    lines = _header("模型算量口径说明", text, "本轮未读取模型、未提取 IFC 属性、未运行几何算量。数量只抄用户明确给值，未给值保持 UNSPECIFIED；模型量不构成结算书、招标控制价或验工计价依据。", data)
    lines += ["## 1 项目与模型来源", "", _register(data), "", _models(data), "",
        "## 2 工程分类与构件过滤", "",
        _table(("工程分类 / 构件类", "关联模型", "类型名", "材质", "楼层 / 区段", "相位 / 施工段", "包含", "排除", "理由"), [
            tuple(_value(row, key) for key in ("category", "model_ref", "type_name", "material", "level", "phase", "include", "exclude", "reason")) for row in rows
        ]), "", "链接、占位族、注释体量及重复模型的计入边界须明确；没有规则时不默认计入或排除。", "",
        "## 3 计量口径、重复与扣减", "",
        _table(("条目", "计量规则（用户指定）", "单位", "孔洞扣减", "重叠处理", "重复处理", "其他扣减", "执行结果"), [
            (*(_value(row, key) for key in ("category", "rule", "unit", "openings", "overlap", "duplicate", "deduction")), "未计算") for row in rows
        ]), "", "没有指定计算规则时，仅保留几何量口径待定义；不自动套用清单、定额或消耗量规则。", "",
        "## 4 用户数量登记表", "",
        _table(("工程分类 / 构件类", "关联模型", "过滤条件", "计量规则", "工程量", "单位", "数量来源", "用户来源说明", "单价", "合价", "用户数量原记录"), [
            (_value(row, "category"), _value(row, "model_ref"), _value(row, "include"), _value(row, "rule"), _quantity(row), _value(row, "unit"),
             "用户明确给值；未核算" if _quantity(row) != UNKNOWN else "未获得可抄录数量", _value(row, "source"), "TBD", "TBD", _value(row, "quantity")) for row in rows
        ]), "", "不从尺寸、表达式或多行数量计算结果，不合并不同模型和不同计量口径。单价与合价交造价岗。", "",
        "## 5 属性路径与来源校核", "",
        _table(("条目", "关联模型", "用户属性路径", "用户 GUID", "校核来源 / 记录", "偏差记录", "本轮校核"), [
            (*(_value(row, key) for key in ("category", "model_ref", "attribute", "guid", "reconcile", "deviation")), "未执行") for row in rows
        ]), "", "未给属性路径不造数量集或 GUID；字段缺失不等于已判定模型未赋值。单位、范围、重复实例及来源版本须另核。", "",
        "## 6 偏差与可提量性待核", "",
        _table(("待核项目", "需补资料", "本模型可提量性"), [
            ("洞口、墙梁重叠与柱墙交接", "构件范围、几何与扣减规则", UNKNOWN),
            ("变截面和异形构件", "几何或属性记录与计量规则", UNKNOWN),
            ("钢筋", "可统计实体、明细及规则", UNKNOWN),
            ("土方", "原始地形、设计地形与边界", UNKNOWN),
            ("模板、脚手架等措施项目", "适用计算规则与建模范围", UNKNOWN),
        ]), "",
        "## 7 一模多算与交接", "",
        "实物量、清单量及消耗量按各自规则分别留行，不能合为一个未注明口径的总量。现场完成量、合同结算量仍需收方和合同依据；本稿不作相等认定。", "",
        "## 8 依据与自检", "", _sources(data, text), "",
        "自检：过滤先于数字；数量只抄已给值；未知数 UNSPECIFIED；单价合价 TBD；无虚构属性路径或 GUID；无结算结论。组价交造价，签证事实交变更签证，对上验工交验工计价，模型冲突交模型协调。", "",
    ]
    return "\n".join(lines+_ownership(data, "bim-qto"))


def _deliver(text: str) -> str:
    data = _parse(text, "bim-deliver")
    models = data.rows("models")
    lines = _header("BIM 交付清单", text, "本稿整理交付要求与缺口。本轮未打开模型、未检查碰撞、未验收，不构成合同交付签认、审图报件或竣工备案模型。", data)
    lines += ["## 1 项目、阶段与模型登记", "", _register(data), "", _models(data), "",
        "## 2 坐标系与单位", "", _coordinates(data), "",
        "共享坐标发布与现场控制点对齐仍需发布记录及测量复核；不从坐标系名称推导坐标数值。", "",
        "## 3 拆分与命名规则", "",
        _table(("模型", "拆分规则", "单文件体量上限", "合成责任人", "命名规则", "用户实际文件名", "核验"), [
            (*(_value(row, key) for key in ("model", "split", "max_size", "owner", "naming", "file_name")), "未核验") for row in models
        ]), "", "命名待约定字段：项目标识、原点、单体 / 分区、专业、阶段、版本。不拼造具体文件名；拆分后可合成性待验证。", "",
        "## 4 LOD 与信息需求矩阵", "",
        _table(("模型 / 构件范围", "专业", "阶段", "用途", "用户 LOD / 细度要求", "几何要求", "属性要求", "文档要求", "满足情况"), [
            (*(_value(row, key) for key in ("model", "discipline", "stage", "purpose", "lod", "geometry", "attributes", "documents")), UNKNOWN) for row in models
        ]), "", "几何、属性、文档分别核对。不把不同细度体系或 LOD 数字自动等同，不从细度数字推导施工、下料或结算条件。", "",
        "## 5 交付物与格式清单", "",
        _table(("交付物", "关联模型", "格式", "版本", "是否必交（用户要求）", "交付节点", "接收方", "核验结果"), [
            (*(_value(row, key) for key in ("deliverable", "model_ref", "format", "version", "required", "delivery_date", "recipient")), UNKNOWN)
            for row in data.rows("deliverables")
        ]), "", "模型、图纸说明、协调纪要、算量口径、BEP 等只作为待确认的资料类别；是否必交以合同或业主要求为准，不默认已提供。", "",
        "## 6 版本与发布记录", "",
        _table(("模型", "当前用户版本", "前版", "版本变更", "用户容器状态", "用户共享坐标发布记录", "本轮发布 / 签认"), [
            (*(_value(row, key) for key in ("model", "version", "previous", "change", "container_state", "shared_coordinates")), "未执行") for row in models
        ]), "", "作业、共享、发布、归档状态由项目约定；用户状态不等于接收方签收。签认人：（空栏）。", "",
        "## 7 交换与接收抽检", "",
        _table(("模型", "用户 IFC 版本", "导出视图", "导出设置", "用户交换抽检记录", "本轮交换验证"), [
            (*(_value(row, key) for key in ("model", "ifc_version", "export_view", "export_settings", "exchange_record")), "未执行") for row in models
        ]), "", "原位与交换文件的几何、单位、属性、链接对应关系须抽检；本稿不认定等价，也不生成交换文件。", "",
        "## 8 交付检查与未交清单", "",
        _table(("模型", "用户打开记录", "用户属性检查", "用户碰撞记录", "用户算量校核", "用户验收记录", "本轮核验 / 验收"), [
            (*(_value(row, key) for key in ("model", "open_record", "attribute_record", "clash_record", "quantity_record", "acceptance")), UNKNOWN) for row in models
        ]), "",
        _table(("模型", "用户缺项", "用户不交清单", "缺项完整性"), [
            (_value(row, "model"), _value(row, "missing"), _value(row, "excluded_delivery"), "未核验") for row in models
        ]), "", "未知检查结果不改写为通过或齐全；未建模、示意构件、无依据参数及未对齐链接须由责任人逐项核对。", "",
        "## 9 依据、自检与相邻岗位", "", _sources(data, text), "",
        "自检：多模型版本不串填；用途与 LOD 分列；坐标和单位无默认值；文件名只抄用户值；碰撞与验收未知；无软件账号或授权信息。碰撞交模型协调，出量交模型算量，平台权限交 IT，设计变更交设计统筹，竣工资料交资料监理。", "",
    ]
    return "\n".join(lines+_ownership(data, "bim-deliver"))


def build_draft(expert_id: str, tool_name: str, text: str) -> str | None:
    """Render the requested BIM post; approval and persistence belong to the host."""
    if _TOOLS.get(expert_id) != tool_name:
        return None
    return {"bim-coord": _coord, "bim-qto": _qto, "bim-deliver": _deliver}[expert_id](text or "")
