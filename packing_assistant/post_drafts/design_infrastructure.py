"""Infrastructure design notes from explicit records; no engineering solver."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass

UNKNOWN = "UNSPECIFIED"
DISCLAIMER = "内部讨论 AI 草稿。已填内容仅为用户提供的输入，未核验；本稿未做设计计算、审批或签认。缺数不补造，不作为施工、投标、审图或验收依据。"


@dataclass(frozen=True)
class Section:
    title: str
    fields: tuple[str, ...]
    note: str


@dataclass(frozen=True)
class Profile:
    tool: str
    title: str
    identity: str
    sections: tuple[Section, ...]
    outputs: tuple[str, ...]


def S(title: str, fields: str, note: str) -> Section:
    return Section(title, tuple(fields.split("/")) if fields else (), note)


PROFILES = {
    "port": Profile("port__outline", "港航工程设计提纲", "码头名称|泊位名称|对象名称", (
        S("码头等级、船型与吞吐量", "码头名称|泊位名称|对象名称/码头等级/设计船型|船型/吞吐量/港口功能|功能", "按用户资料登记泊位用途与船型；未给能力数据时不推定等级。"),
        S("草稿声明与原始资料", "项目名称|项目/图纸清单|图号清单/水文资料/地质资料", "水文、地质与船型文件应相互对应；缺失文件只列缺口，不写见图编号。"),
        S("水位、波浪与潮流", "设计高水位|高水位/设计低水位|低水位/波浪要素|波浪/潮流/水文来源|水文文件", "区分水位基准、观测期资料和设计采用值；本稿不外推水位及波浪。"),
        S("码头结构比选", "候选结构|码头结构/岸线条件/岸坡条件/施工约束", "高桩、重力及板桩仅作比较对象；按岸坡、地基、施工与维护条件整理待核项，不锁定最优方案。"),
        S("前沿高程与泊位尺度", "前沿高程/泊位长度/泊位宽度/靠船设施/系缆设施", "尺度与作用须有水文、船型和计算来源；不生成桩长、胸墙尺寸、靠船力。"),
        S("航道、回旋水域与防波堤", "航道条件/回旋水域/防波堤/通航条件/导助航设施", "航道与回旋水域结合船舶和水文核对；通航安全及导助航资料留给主管接口确认。"),
        S("装卸工艺与陆域堆场", "装卸工艺/装卸设备/陆域堆场|堆场/集疏运条件", "核对设备荷载、轨道及堆场接口；不由设备名称推算结构荷载或堆场容量。"),
        S("防腐、耐久与冲刷防护", "腐蚀环境/防腐要求/耐久要求/冲刷资料/防护要求", "区分浪溅区及水位变动区；防腐厚度与冲刷防护尺度待专用资料和计算。"),
        S("水利、结构与岩土接口", "接岸位置/防洪堤接口/岸坡稳定资料/结构提资/岩土提资", "后方防洪堤、护岸与水位调度交水利；岸坡、基础与结构计算分别提资，不在本岗替算。"),
        S("交付与责任核对", "交付清单/责任专业/待补资料/完成时限", "整理提纲、来源和待补资料，通航与结构相关审批均待实际责任方确认。"),
    ), ("桩长", "胸墙尺寸", "靠船力", "计算前沿高程")),
    "municipal": Profile("municipal__memo", "市政道路设计原则稿", "道路名称|路段名称|对象名称", (
        S("道路范围与等级", "道路名称|路段名称|对象名称/道路等级/起止桩号/红线宽|红线宽度/设计速度", "沿用户给定路段登记边界；不根据道路名称猜测等级或设计速度。"),
        S("草稿声明与基础资料", "项目名称|项目/图纸清单|图号清单/地形资料/交通量来源/地质资料", "无实测地面线、交通量与地质资料不形成道路定量设计。"),
        S("平面与交叉口", "中线条件/交叉口|路口/平面控制点/缘石条件", "核对与规划道路及交叉口的衔接，缘石半径须有设计依据，不按经验补值。"),
        S("横断与路幅分配", "规划横断面|横断面/机动车道/非机动车道/人行道/绿化带", "将各类通行空间与规划横断对应核对；不得从总宽自行拆算车道宽度。"),
        S("纵断与高程", "地面线来源/设计高程/纵坡/坡长/竖曲线", "高程、纵坡和竖曲线只抄用户值待核，不由起止桩号构造纵断。"),
        S("路面结构", "轴载资料/设计年限/面层/基层/垫层/路面厚度", "面层、基层、垫层作为结构组合核对项；无轴载和地基资料不选厚度。"),
        S("路基与排水出路", "土基条件/路基处理/排水出路/接驳标高/管径/流量", "先核对排水去向及接驳标高，再由对应专业计算管径和流量。"),
        S("管线综合", "管线名称/现状管线/拟建管线/交叉位置/管线权属单位", "给排水、电力、通信、燃气等分别核对平面和交叉接口；冲突转设计统筹或 BIM 登记。"),
        S("附属与无障碍", "公交站/照明接口/无障碍接口/标志标线接口", "核对缘石坡道、公交与照明的接口；交通设施定量交交通专业。"),
        S("施工导改与交付清单", "是否涉及导改|导改需求/导改阶段/交付清单/责任专业", "涉及占路时另列交通导改任务；道路原则稿不替代占路许可或专项施工方案。"),
    ), ("计算管径", "计算纵坡", "计算流量", "选定路面厚度")),
    "bridge": Profile("bridge__outline", "桥梁设计提纲", "桥梁名称|桥名|对象名称", (
        S("功能与等级", "桥梁名称|桥名|对象名称/工程类型|功能/荷载等级/设计速度/通航要求/行洪要求", "公路、市政及人行用途分别登记；未指定适用标准族时保持待定。"),
        S("草稿声明与资料", "项目名称|项目/图纸清单|图号清单/跨径来源/地质资料/水文资料", "无跨径、地质与水文资料不确定构件尺度或基础方案。"),
        S("桥位与桥长", "桥位/桥长/路线衔接/河道关系/交叉道路", "核对桥位与路线、河道及道路交叉关系，不自动确定桥孔布置。"),
        S("桥型比选", "候选桥型|桥型/景观约束/施工约束/维护约束", "梁桥、拱桥、斜拉桥及刚构按通航、施工、维护等条件列待核项，不锁定最优桥型。"),
        S("上部结构", "跨径组合|跨径/截面型式/梁高/预应力束|钢束/上部结构来源", "梁高及钢束只列用户资料；无专用计算不作满足性结论。"),
        S("下部结构", "墩台型式/基础型式/桩长/承台尺寸/岩土参数来源", "基础比选需岩土资料；不从桥长或荷载等级推断桩长和承台。"),
        S("支座、桥面与附属", "支座/伸缩缝/桥面铺装/桥面排水/栏杆|防撞设施", "支座、伸缩缝和桥面接口须与变形及荷载条件核对，不自动选型。"),
        S("水文、通航、抗震与耐久", "冲刷资料/通航净空/抗震资料/耐久要求", "冲刷深度、净空和抗震采用值须注明来源；涉河影响与水利接口分别核对。"),
        S("施工方法接口", "拟议施工方法|施工方法/支架条件/临时工程接口/施工提资", "支架、悬浇、顶推或转体仅列待比较接口，施工方案交施工岗位。"),
        S("监测、养护与交付", "监测需求/养护条件/交付清单/责任专业/待补资料", "健康监测是否设置由项目条件确认；不虚构已完成计算或专项审查。"),
    ), ("计算梁高", "钢束选型", "计算桩长", "承台设计尺寸", "冲刷深度")),
    "tunnel": Profile("tunnel__outline", "隧道设计提纲", "隧道名称|区间名称|对象名称", (
        S("功能、长度与净空", "隧道名称|区间名称|对象名称/工程类型|功能/隧道长度|长度/净空/防水等级", "公路、轨道交通和人行地下通道采用各自资料，不混用防灾标准。"),
        S("草稿声明与勘察资料", "项目名称|项目/图纸清单|图号清单/地质纵断面/地质资料/周边建筑|周边环境/管线资料", "无地质纵断面不确定开挖支护参数；周边建筑和管线需要调查来源。"),
        S("线位与纵坡", "线位/纵坡/路线衔接/横通道/停车带", "对照道路或轨道控制条件核对线位与联络通道，不推导纵坡。"),
        S("工法与适用条件", "工法|施工工法/埋深/地层条件/地下水/施工场地", "按用户指定工法单独列参数缺口；未指定时比较矿山法、盾构、明挖和顶管的条件，不确定方案。"),
        S("开挖与支护", "围岩分级|围岩等级/初期支护资料/二次衬砌资料/管片资料/开挖步序", "围岩分级只抄勘察资料；矿山法与盾构不共用支护参数或配筋。"),
        S("防水与接缝", "防水要求/施工缝/变形缝/密封要求/注浆资料", "按工法核对接缝、防水与密封接口；不生成注浆压力和材料配比。"),
        S("监控量测", "监测对象/拱顶下沉/净空收敛/地表沉降/测点布置/监测来源", "列观测项目、周边敏感点和反馈路径；测点间距及沉降控制值待专用方案，不编阈值。"),
        S("机电与防灾", "通风接口/照明接口/消防接口/疏散接口", "按用户给定隧道用途分别提资，不把公路、轨交和房建消防口径合并。"),
        S("洞口与浅埋段", "洞口条件/偏压资料/仰坡条件/超前支护资料", "洞口、偏压和浅埋敏感段单列地质与施工条件，不给支护定量。"),
        S("专业接口与交付", "市政接口/交通导改接口/岩土提资/交付清单/待补资料", "支护、监测和危大方案分别由对应责任专业核对，不替代施工监测方案。"),
    ), ("喷射混凝土厚度", "锚杆长度", "管片配筋", "注浆压力", "沉降控制值")),
    "traffic": Profile("traffic__skeleton", "交通组织与仿真报告提纲", "研究名称|路口名称|对象名称|情景名称", (
        S("研究问题与任务类型", "研究名称|路口名称|对象名称|情景名称/研究类型|任务类型/研究问题/研究范围", "建成后交通影响与施工导改分别成记录，不合并成同一效果结论。"),
        S("草稿声明与缺项", "项目名称|项目/图纸清单|图号清单/待补资料", "无调查数据不评价饱和度或服务水平，本文未运行仿真。"),
        S("调查数据与来源文件", "流量文件|流量来源/信号配时文件/路网文件/调查日期/调查时段/交通量|流量", "文件名只抄用户实际提供名称；缺失时不能用演示流量替代。"),
        S("现状与情景", "情景阶段|阶段/时段/路网范围/交通需求/导改阶段", "区分现状、建成或施工阶段及早晚高峰；不同情景不借用流量和配时。"),
        S("交通组织方案", "车道功能/禁左需求/单行需求/行人过街/公交组织/非机动车组织", "按通行对象分别核对组织接口，不自动生成封路或交通许可。"),
        S("标志标线与信号", "标志清单/标线清单/信号方案/版面尺寸/配时", "设施尺寸与信号配时需来源及审核，未提供则留空，不编秒数。"),
        S("仿真实验与指标", "仿真软件|软件/模型文件/标定资料/随机种子来源/指标口径", "延误、排队和饱和度仅列指标定义与取样口径；没有实跑报告时结果保持 UNSPECIFIED。"),
        S("图表清单", "流向图/渠化图/导改阶段图/调查表/结果报告", "图名和文件版本对应核对；不把拟出图表写成已有附件。"),
        S("道路、桥梁与出入口接口", "道路横断接口/桥梁净空接口/景观视距接口/建筑出入口接口", "核对道路横断、净空、视距和出入口；条件不足回提资，不替专业设计。"),
        S("实施与审批资料目录", "实施阶段/交通管理接口/责任专业/完成时限", "只列交通管理所需资料和待核责任，不作交警审批、实施或效果承诺。"),
    ), ("饱和度", "服务水平", "排队长度", "平均延误", "优化配时")),
    "design-coord": Profile("design-coord__minutes", "图纸会审纪要与提资清单", "问题编号|问题ID|对象名称", (
        S("项目与图纸范围", "项目名称|项目/图纸清单|图号清单/图号/图纸版本/问题编号|问题ID|对象名称", "只列用户图号及版本；无图纸清单不补图号或写见图引用。"),
        S("草稿声明与适用接口", "工程类型|功能/主管接口/适用指令/建筑面积|GFA/依据来源", "本岗整理协调记录，不代专业计算或出图章；适用审批路径和面积门槛待核官方文件，不内置旧阈值。"),
        S("会审、设计交底与技术交底", "会议类型/会议目的/会议时间/会议地点/参加人员", "会审记录图纸问题；设计交底说明设计意图；施工技术交底面向作业人员，三类记录分别归档。"),
        S("专业碰撞与问题清单", "问题描述|问题/问题位置|位置/涉及专业/相关构件/证据文件", "按建筑与结构、机电与净高、装修与疏散、幕墙预埋及人防密闭等接口登记，不声称已扫描模型。"),
        S("提资接口", "提资内容/提资方/接收方/文件名称/完成时限|期限", "谁向谁提供什么资料以及何时提供需逐条对应；下一条问题不沿用上一条期限。"),
        S("问题分级与转办", "用户分级|问题等级/影响范围/责任专业/转办对象", "涉及结构安全、消防或强制要求的事项列入待专业复核；本稿不自行判定修改可免审。"),
        S("设计变更记录", "变更需求/变更编号/变更文件/设计回复/原审查接口", "口头讨论不变成已签发变更，变更范围、设计文件与复核程序由项目责任方确认。"),
        S("会议纪要与待决事项", "讨论内容/已议定事项|决议/待定事项/责任人/截止日期", "用户提供决议只作转录待核；拟议、建议或待批准的内容不归为已决定。"),
        S("外部门提问分诊", "提出部门/提问内容/专业接收人/需补材料", "结构配筋、机电选型等交对应设计专业，施工与商务问题保留各自接口。"),
        S("归档与闭环核验", "归档目录/会审纪要文件/变更台账/闭环证据/用户状态|状态", "状态只作用户记录；没有证据不标已关闭，签发、签字及审查结论由责任人填写。"),
    ), ("专业计算结果", "审查结论", "变更签发状态", "问题闭环核验")),
}

_COMMON = ("辖区", "适用依据|规范名称", "依据版本|年份", "CN依据", "SG依据", "EU依据", "CN版本", "SG版本", "EU版本", "CN接口", "SG接口", "EU接口")
_EXTRA = {
    "port": ("码头名称|对象|泊位|码头", "地质资料|地勘|地勘报告|地质报告", "港口功能|货种|用途", "水位基准", "陆域接口", "疏浚资料", "地质来源"),
    "municipal": ("道路名称|路名|道路名|路段", "道路等级|等级", "起止桩号|桩号|起讫桩号", "规划横断面|横断", "排水出路|雨水出路", "现状地面标高|地面标高", "地质资料|地勘|地勘报告", "管线来源"),
    "bridge": ("桥梁名称|桥梁|桥涵", "桥宽|桥面宽|桥面全宽", "荷载等级|荷载|活载", "截面型式|上部|上部结构", "地质资料|地勘|地勘报告|地质报告", "荷载来源", "通航净空|通航净高"),
    "tunnel": ("隧道名称|隧道|区间", "隧道长度|区间长度|全长", "管片资料|管片", "监测对象|监测|监控量测", "围岩分级|围岩", "围岩来源", "支护来源", "监测阈值来源", "隧道总体名称"),
    "traffic": ("研究名称|评价节点|节点|交叉口", "方向车型|方向|转向|进口|流向", "现状信号周期|信号周期", "调查来源", "用户车型|车型", "实验目的"),
    "design-coord": ("问题编号|会议编号|会议或问题编号|纪要编号", "文件名称|文件名|提资文件", "对象图号|图纸编号", "状态证据", "变更来源", "决议证据"),
}
_EMPTY = re.compile(r"(?:\[A\d+\]\s*)?(?:待填|待核|未知|未提供|UNSPECIFIED|TBD|N/A)", re.I)


def _cell(value: str) -> str:
    return html.escape(value, quote=False).replace("|", "&#124;").replace("\n", " ")


def _table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    return "\n".join(["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |",
                      *("| " + " | ".join(_cell(v) for v in row) + " |" for row in rows)]) + "\n"


def _records(profile: Profile, text: str) -> list[dict]:
    from packing_assistant.post_drafts.design_infrastructure_observations import parse
    post = next(post for post, item in PROFILES.items() if item is profile)
    return parse(post, text)


def _scope_table(row: dict[str, str], text: str) -> str:
    zone = row.get("辖区", row.get("_project_zone", UNKNOWN))
    if zone == "DUAL":
        codes = [code for code in ("CN", "SG", "EU") if any(row.get(code+k) not in {None, UNKNOWN} for k in ("依据", "版本", "接口"))]
        codes = codes if len(codes) >= 2 else ["辖区A待指定", "辖区B待指定"]
    else:
        codes = [zone]
    rows = []
    for code in codes:
        basis = row.get(code + "依据") or (row.get("适用依据", UNKNOWN) if zone != "DUAL" else UNKNOWN)
        version = row.get(code + "版本") or (row.get("依据版本", UNKNOWN) if zone != "DUAL" else UNKNOWN)
        rows.append((code, basis, version, UNKNOWN, row.get(code + "接口", UNKNOWN), "unverified"))
    table = _table(("辖区", "用户指定依据", "版本待核", "条款", "审查接口待核", "状态"), rows)
    if zone == "DUAL" and row.get("适用依据"):
        table += "未分配辖区的用户依据：" + _cell(row["适用依据"]) + "；须明确归属后分别核对。\n"
    return f"- 辖区：{zone}\n- 对象辖区：{row.get('辖区', UNKNOWN)}；总体资料范围不代填对象空栏\n- submit_blocked=true；未签认\n\n" + table


def _source(row, key, explicit="资料来源"):
    return row.get(explicit) or row.get("资料来源") or row.get("_sources", {}).get(key) or UNKNOWN


def _professional(post, row):
    identity = PROFILES[post].identity.split("|", 1)[0]
    out = []
    if post == "municipal":
        title, columns = "道路区段专业核对", ("道路名称", "起止桩号", "红线宽", "现状地面标高", "设计高程", "设计速度", "面层", "现状管线", "管线权属单位", "待核记录")
    elif post == "bridge":
        title, columns = "桥梁比选与输入来源", ("桥梁名称", "跨径组合", "桥宽", "候选桥型", "截面型式", "荷载等级", "荷载来源", "基础型式", "地质资料", "待核记录")
    elif post == "port":
        title, columns = "港工对象与资料接口", ("码头名称", "港口功能", "设计船型", "设计高水位", "设计低水位", "波浪要素", "水位基准", "地质资料", "地质来源", "陆域接口", "待核记录")
    elif post == "tunnel":
        title, columns = "区间支护与来源待核", ("隧道名称", "隧道总体名称", "围岩分级", "围岩来源", "埋深", "工法", "初期支护资料", "超前支护资料", "监测对象", "监测来源", "监测阈值来源", "待核记录")
    elif post == "traffic":
        title, columns = "交通调查与实验待核", ("研究名称", "研究类型", "调查日期", "调查时段", "方向车型", "用户车型", "交通量", "流量文件", "现状信号周期", "模型文件", "待核记录")
    else:
        title, columns = "会审行动与证据归属", ("问题编号", "问题位置", "图号", "对象图号", "图纸版本", "问题描述", "责任专业", "责任人", "完成时限", "截止日期", "用户状态", "状态证据", "决议证据", "待核记录")
    children = row.get("_traffic_rows", []) if post == "traffic" else row.get("_pipe_rows", []) if post == "municipal" else []
    if children:
        from packing_assistant.post_drafts.design_infrastructure_observations import FLOW_FIELDS, PIPE_FIELDS
        child_keys = FLOW_FIELDS if post == "traffic" else PIPE_FIELDS
        professional_rows = [{**{k: v for k, v in row.items() if k not in child_keys}, **child} for child in children]
    else:
        child_keys, professional_rows = set(), [row]
    out += ["## "+title, "", _table(columns+("资料核对状态", "签认"), [
        tuple(record.get(key, UNKNOWN) for key in columns)+("用户输入未核验；接口待责任专业复核；本轮未计算或审批", "")
        for record in professional_rows]), ""]
    if post == "municipal" and children:
        pipe_columns = ("道路名称", "起止桩号", "管线名称", "现状管线", "拟建管线", "管径", "交叉位置", "管线权属单位", "管线来源")
        out += ["## 管线接口逐项归属", "", _table(pipe_columns+("探测及复核状态", "签认"), [
            tuple(record.get(key, UNKNOWN) for key in pipe_columns)+("未探测或核验；待权属单位确认", "")
            for record in professional_rows]), ""]
    # Additional typed input fields are not discarded merely because the old
    # outline did not have a matching column. Each keeps its own source.
    facts = [(row.get(identity, UNKNOWN), key, value, _source(row, key), row.get("_sources", {}).get(key, UNKNOWN), "未核验；待责任专业签认")
             for key, value in row.items() if not key.startswith("_") and key not in child_keys and value != UNKNOWN]
    for child in children:
        object_name = "；".join(row.get(k, UNKNOWN) for k in (identity,)) + "；" + "；".join(
            child.get(k, UNKNOWN) for k in (("调查时段", "方向车型") if post == "traffic" else ("管线名称", "现状管线", "拟建管线")))
        facts.extend((object_name, key, value, _source(child, key), child.get("_sources", {}).get(key, UNKNOWN), "未核验；待责任专业签认")
                     for key, value in child.items() if not key.startswith("_") and value != UNKNOWN)
    out += ["## 对象参数与来源逐项登记", "", _table(("专业对象", "参数栏目", "用户给定值（未核）", "资料来源", "输入位置（非原件页码）", "核对状态"), facts or [(UNKNOWN,)*5+("未核验",)]), ""]
    context = row.get("_context_data", {})
    out += ["## 项目与文件资料归属", "", _table(("项目", "总体辖区（不代填对象）", "设计阶段", "输入文件", "资料状态"), [
        (context.get("项目名称", UNKNOWN), context.get("总体辖区", UNKNOWN), context.get("设计阶段", UNKNOWN), row.get("_origin") or UNKNOWN, "未核验")]), ""]
    return out


def _render(profile: Profile, row: dict[str, str], text: str, post: str) -> str:
    rows = dict(row)
    if post == "design-coord":
        decision = rows.get("已议定事项", "")
        evidence = rows.get("决议证据", UNKNOWN)
        if decision not in {"", UNKNOWN} and (evidence == UNKNOWN or re.search(r"建议|拟|待定|待批|未决定|未通过|尚未|是否|口头|答应|承诺|计划|讨论|预计|待签|未签", decision)):
            rows["待定事项"] = "；".join(v for v in (rows.get("待定事项"), decision) if v)
            rows["已议定事项"] = UNKNOWN
    if post in {"traffic", "municipal"}:
        from packing_assistant.post_drafts.design_infrastructure_observations import FLOW_FIELDS, PIPE_FIELDS
        children = rows.get("_traffic_rows" if post == "traffic" else "_pipe_rows", [])
        keys = FLOW_FIELDS if post == "traffic" else PIPE_FIELDS
        if len(children) > 1:
            for key in keys:
                supplied = {child.get(key, UNKNOWN) for child in children}
                if len(supplied) > 1:
                    rows[key] = UNKNOWN
    out = [f"# {profile.title}（AI 草稿）", "", DISCLAIMER, "", _scope_table(rows, text)]
    if post == "traffic":
        kind = rows.get("研究类型", "")
        mode = {"TIA": "建成后交通影响", "建成后": "建成后交通影响", "建成后交通组织": "建成后交通影响", "施工导改": "施工期导改", "施工期": "施工期导改"}.get(kind, "UNSPECIFIED（请明确建成后交通影响或施工期导改）")
        out += ["- 本记录任务类型：" + mode, ""]
    for index, section in enumerate(profile.sections, start=1):
        out += [f"## {index} {section.title}", "", section.note, "",
                _table(("栏位", "用户提供的输入（未核验）", "核对状态"), [
                    (key.split("|", 1)[0], rows.get(key.split("|", 1)[0], UNKNOWN), "待补" if rows.get(key.split("|", 1)[0], UNKNOWN) == UNKNOWN else "待核来源及适用性")
                    for key in section.fields]), ""]
        if post == "tunnel" and index == 4:
            method = rows.get("工法", "")
            note = "盾构：管片、接缝密封与同步注浆资料单独核对，不套矿山法初支参数。" if method == "盾构" else (
                "矿山法：围岩分段、开挖步序、初期支护与二次衬砌分别核对。" if method in {"矿山法", "新奥法", "浅埋暗挖"} else "工法未唯一指定；各候选条件分别核对，不共用参数。")
            out += [note, ""]
    out += ["## 计算与审批结果待填", "", _table(("结果项", "工具输出", "状态"), [(label, UNKNOWN, "未计算或未核验") for label in profile.outputs]),
            "", _table(("编制核对", "专业复核", "签认"), [("", "", "")]), ""]
    return "\n".join(out+_professional(post, rows))


def build_draft(expert_id: str, tool_name: str, text: str) -> str | None:
    profile = PROFILES.get(expert_id)
    if profile is None or profile.tool != tool_name:
        return None
    records = _records(profile, text or "")
    return "\n\n".join(
        (f"# 独立材料记录 {i}\n\n" if len(records) > 1 else "") + _render(profile, row, text or "", expert_id)
        for i, row in enumerate(records, start=1)
    )
