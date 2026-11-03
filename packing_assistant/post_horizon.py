"""Per-post capability records and boundaries vs 易标 or pack-agent."""

from __future__ import annotations

from typing import Any, Dict, List

from packing_assistant.expert_roster import list_experts

YIBIAO_STEPS = ("parse", "outline", "qa", "kb", "write")
PACK_POSTS = frozenset({"pack-ship"})
FORBIDDEN_GOALS = ("可以投标", "可以开工", "中标率")


def lane_id(category: str) -> str:
    return f"lane-{category}"


def _step_status(exp, step: str) -> str:
    # yibiao tags live on yibiao-map; roster exclusive + category imply kb/write
    if step == "kb":
        return "已有 · 分层 KB + search_kb/read_kb（demo/kb）"
    if step == "write":
        tools = ", ".join(exp.exclusive) or "write_deliverable"
        return f"已有 · 独有 {tools}；chat 不写盘"
    if step == "qa":
        return f"已有 · {exp.category}__scan_forbidden" + (
            " + 高风险确认句" if exp.risk == "high" else ""
        )
    if step == "parse":
        if exp.id in {"env", "supervision", "survey"}:
            return "已有 · 按文件/项目/阶段及活动、来文条目或测点登记口语、标签与附件表格；字面数量与基准不换算、不借相邻记录，资料未核验"
        if exp.id in {"cost", "variation", "finance-book", "finance-tax"}:
            return "已有 · 按文件/项目/阶段及清单项、签证事件、单据或税种登记口语、标签与附件表格；只转录用户值，未计量或查验凭据"
        if exp.id in {"hr-recruit", "hr-train", "hr-labor"}:
            return "已有 · 按文件/项目/岗位、批次和当事人登记口语、标签及附件表格；只转录用户资料，未核验人员、合同或培训"
        if exp.id in {"method-hazard", "bim-coord", "bim-qto", "bim-deliver", "construction", "emergency", "worker-brief"}:
            return "已有 · 口语、标签和附件表格按文件/项目/阶段/对象登记；只转录用户值，未读取模型或核验判定资料"
        if exp.category == "design":
            return "已有 · 用户字段与附件表格按对象登记；扫描图像无 OCR 不读取，原始资料未核验"
        if exp.id == "bid-parse":
            return "已有 · bid-parse__extract / run_tender_pipeline（exact_text）"
        if any(x in " ".join(exp.exclusive) for x in ("extract", "takeoff", "parse", "record", "recon")):
            return f"部分 · 独有 {', '.join(exp.exclusive)} 可抄用户原文，无扫描 PDF"
        return "缺口 · 本岗不解析招标；用户原文进草稿，扫描 PDF 仍拒绝"
    if step == "outline":
        if exp.id in {"env", "supervision", "survey"}:
            return "已有 · 本岗章节及来源、缺项、核验和签认空栏；Markdown + Word + Excel，未判排放、闭合或测量合格，高风险完整确认后仅出内部草稿"
        if exp.id in {"cost", "variation", "finance-book", "finance-tax"}:
            return "已有 · 用户数量、报价、金额与批准/入账/申报核验分栏；Markdown + Word + Excel，缺项及签字留空，未执行价格计算、支付或申报"
        if exp.id in {"hr-recruit", "hr-train", "hr-labor"}:
            return "已有 · 本岗章节及来源、缺项、核验空栏；Markdown + Word + Excel，未录用、签认或作合同与培训结论"
        if exp.id in {"method-hazard", "bim-coord", "bim-qto", "bim-deliver", "construction", "emergency", "worker-brief"}:
            return "已有 · 专业对象、责任、版本/依据及缺项分表；Markdown + Word + Excel，未判定、计算、检测或验收"
        if exp.category == "design":
            return "已有 · 本专业章节、提资与缺项表；Markdown + Excel，未连接设计计算引擎"
        if any(x in " ".join(exp.exclusive) for x in ("outline", "expand", "draft", "brief", "memo", "plan", "network", "week")):
            return f"已有 · {', '.join(exp.exclusive)} 提纲/说明"
        return "部分 · run 出内部提纲骨架，未对照易标目录扩写器"
    return "UNSPECIFIED"


def _pack_steps(exp) -> Dict[str, str]:
    if exp.id == "pack-ship":
        return {
            "list": "已有 · pack-ship__list",
            "plan": "已有 · pack-ship__plan 投影 solver",
            "export": "已有 · pack-ship__export",
            "can_fit": "已有 · 只抄 solver；断线字面 UNSPECIFIED",
            "mid50": "已有 · 只抄 solver；断线 UNSPECIFIED",
            "utilization": "已有 · 只抄 solver；断线 UNSPECIFIED",
            "xyz": "禁止编造 · 未接通不写坐标",
        }
    return {
        "list": "缺口 · 非装柜岗不暴露 pack-ship 工具表",
        "plan": "不适用",
        "export": "不适用",
        "can_fit": "不适用 · 不得手写 can_fit",
        "mid50": "不适用",
        "utilization": "不适用",
        "xyz": "禁止编造",
    }


# Distinctive next-knives from lane-bid / lane-construction / lane-plant /
# lane-design / remaining-12 plan children. One string per seed id.
_NEXT = {
    "bid-parse": "expert_turn 把 run_tender_pipeline 的 handoff 另存 tender.handoff.json，供后岗读；本岗 submit_blocked 仍 true。",
    "bid-compliance": "expert_turn 专用 gaps：读 handoff 或重跑 pipeline，落盘三列已响应/未响应/招标未提供正文，不代判废标。",
    "bid-tech": "expert_turn 读 scoring_points 调 build_tech_outline_from_handoff；无评分点不套上个项目目录。",
    "architecture": "已实现 T044 architecture：单体总平面、分区面积与功能、消防分区及疏散用户值、无障碍、竖向、节能和专业接口；缺面积、宽度不推算。依据仅抄用户资料、未核验；辖区与接口分栏。",
    "structure": "已实现 T044 structure：单体体系、构件荷载与组合、材料、基础输入、抗震资料及复核清单；承载力、配筋和截面计算结果保持 UNSPECIFIED。依据仅抄用户资料、未核验；辖区与接口分栏。",
    "geotech": "已实现 T044 geotech：孔号与分层、c/φ/水位和来源、勘探试验、地基比选、监测及提资；孔层缺项不借值，不替正式勘察报告。依据仅抄用户资料、未核验；辖区与接口分栏。",
    "plumbing": "已实现 T045 plumbing：系统水源/水压、市政接驳、室内出户及井标高、给排水分区、雨水回用、消防水资料；管径和选泵计算待核。依据仅抄用户资料、未核验；辖区与接口分栏。",
    "hvac": "已实现 T045 hvac：室内外参数、逐时冷热负荷、风水系统、防排烟联锁、机房竖井与消声保温；主机、风管及排烟量不代算。依据仅抄用户资料、未核验；辖区与接口分栏。",
    "electrical": "已实现 T045 electrical：市政电源、容量和负荷系数、变配电用户方案、照明、防雷接地、线路及消防/弱电接口；不选变压器或电缆。依据仅抄用户资料、未核验；辖区与接口分栏。",
    "fire-protect": "已实现 T045 fire-protect：救援条件、防火分区、疏散避难、消防水、防排烟、报警联动、电气及报审目录；不作审图通过或放行结论。依据仅抄用户资料、未核验；辖区与接口分栏。",
    "steel": "已实现 T045 steel：构件体系与跨度、荷载、材料规格、螺栓焊缝、稳定支撑、防腐防火及加工安装接口；截面和连接计算未执行。依据仅抄用户资料、未核验；辖区与接口分栏。",
    "landscape": "landscape__memo 按文件、项目、分区和苗木登记字面胸径/冠幅/数量与来源；调整表格列顺序不串值，顶板荷载与消防接口待核，不选苗、不换单位、未签认。",
    "interior": "interior__schedule 按文件、项目、阶段和房间登记地墙顶、防水上翻、节点来源及收口责任；缺项不借相邻房间，门窗归属独立，不作结构或消防批准结论。",
    "facade": "已实现 T044 facade：幕墙体系、风压与分格、预埋后锚固、气密水密变位、防火防雷、加工检测和维护接口；不选厚度或签验收结论。依据仅抄用户资料、未核验；辖区与接口分栏。",
    "intel-weak": "intel-weak__memo 按系统及区域保留终端数量、点表来源、路由、UPS 及接口要求；引用须同文件分组且唯一，真实地址凭据不入稿，不布点、不选型、联调结果空白。",
    "civil-defense": "civil-defense__brief 保留单元用途、用户等级及批准资料来源、口部设备与移动电站接口；CN/SG 对象依据分开，高风险完整确认后仅出未签认提纲，不代审图。",
    "hydraulic": "hydraulic__outline 按建筑物、水文断面、地勘孔和来源登记用户参数；明确引用只联同文件同分组唯一记录，水力/渗流/稳定计算未连接；完整确认后出未签认提纲。",
    "port": "port__outline 按文件、项目及泊位登记用户船型、水位波浪与地质来源；后置引用只关联同文件同阶段唯一泊位，缺基准不补造，不计算桩长靠船力；完整确认后出未签认提纲。",
    "municipal": "municipal__memo 分开登记路段桩号、红线宽、设计速度和地面标高；管线逐条保留权属与来源，缺项不借相邻管线；不拆算车道宽或计算路面厚度，未探测或核验。",
    "bridge": "bridge__outline 按文件、项目、阶段及逐桥记录用户跨径表达式、桥宽、桥型荷载与基础资料；保留单位，不求和或选型，依据未核验；完整确认后出未签认提纲。",
    "tunnel": "tunnel__outline 逐区间保留围岩来源、覆盖埋深、用户工法及支护监测接口；地下水埋深不填覆盖埋深，相邻区间不借支护参数；支护计算和阈值未连接，完整确认后仅出未签认提纲。",
    "traffic": "traffic__skeleton 按节点时段转向分行登记用户流量、车型、文件及现状信号；保留原单位，时段与相邻方向不串文件或配时；模型资料缺项待填，未运行仿真或优化信号。",
    "design-coord": "design-coord__minutes 逐问题登记图号版本、原文、责任期限及状态证据；口头承诺、计划与无本条证据的决议只列待决，闭环核验保持未执行，不签发变更或代审图。",
    "bim-coord": "bim-coord__clash 按文件、项目和阶段分开模型、问题及接口；责任专业、人员、期限独立；显式关联只连同分组唯一模型，不借版本，不扫描 IFC、不认定闭环。",
    "bim-qto": "bim-qto__rules 逐模型和构件登记过滤、计量单位、用户数量及来源扣减；零值保留、表达式不计算、未给量 UNSPECIFIED，单价合价 TBD；不从 IFC 真抽量。",
    "bim-deliver": "bim-deliver__lod 逐模型分开用途、LOD、版本、格式、接收方和期限；文件名不拼造，多文件和换列不串值；未读取、检查或验收，签认空白。",
    "plan-master": "plan-master__network 固定 WBS|紧前|里程碑待填|关键线路=待计算。",
    "plan-lookahead": "已做 T032 plan-lookahead。plan-lookahead__week 出四周表；制约未清不得写入本周承诺。",
    "plan-resource": "已做 T032 plan-resource。plan-resource__peak 拆劳动力|机具|材料三表，数量待填。",
    "construction": "construction__scheme_draft 按文件、项目、阶段及工作面填十一章讨论模板；尺寸、工艺、资源和图号独立，缺单位不补，未计算或审批。construction__fill_scheme_docx 以同一结构内容生成可编辑 Word，不凭未给辖区选择国家法定表。",
    "method-hazard": "method-hazard__judge_hazard 分作业登记高度/深度/跨度、图号来源及已有人工初判；单件重量不填吊车吨位，编号不当名称；辖区无默认，DUAL 按用户依据分栏，危大/超规模/PTW 均信息不足，完整确认后只出未签认资料卡。",
    "survey": "survey__record 分测点登记北向 X、东向 Y、原高程及复测值；文件、任务、坐标系、高程基准和单位分表，共用仪器仅限明确分组。差值、精度和检定有效性 UNSPECIFIED，不平差、不放样，完整确认后签认留空。",
    "dispatch": "dispatch__daily 按 outline 十一章落表头；敏感作业只列名，判定交 method-hazard。",
    "safety-brief": "已做 T035 safety-brief。safety-brief__talk 按 outline 写全 11 栏；毫米/电话 [A001]；确认句后才写盘。",
    "quality": "已做 T035 quality。quality__lot 出主控|一般|隐蔽三表，结果=未检；写盘后 hse__scan_forbidden。",
    "env": "env__list 按活动分别登记污染源、字面实际量/估计量、运输消纳联单及夜间报批摘录；区县不当国家辖区，缺来源不借相邻活动，限值与许可/达标核验不自动补，未盘点或签认。",
    "emergency": "emergency__plan 逐场景登记风险、处置责任、联络来源及用户资源；拟演练日期与用户实际记录分开，未盘点、未评估或备案；未给电话医院不预填、不编响应时长。",
    "cost": "cost__takeoff 逐清单项登记编码、名称特征、字面数量单位和图号来源；用户模型/图示/现场量及询价分开，综合单价 TBD、合价 UNSPECIFIED，未计量或组价。",
    "variation": "variation__form 按事件与编号分开用户事实、指令、申请、批准摘录及工程量子项；材料、数量来源不借相邻子项，金额 TBD、签认空白，未批准或代签。",
    "claim": "claim__notice 分事件登记日期、同期资源、函件及用户签收、补证资料；合同条款原文未核，工期金额 TBD，不认定送达或索赔成立。",
    "subcontract": "subcontract__sheet 按合同分包、期间、分项与来源分别登记核量、拟扣款、支付与工资资料；本轮合价、应付 TBD，不下结算或支付结论。",
    "interim": "interim__measure 出开累/本期/监理审/业主核空表；无确认不编应付合价。",
    "proc-plan": "已做 T037 proc-plan。proc-plan__schedule 先分甲供/甲指/自采再列表；无供方周期则提前期 UNSPECIFIED。",
    "proc-compare": "已做 T037 proc-compare。proc-compare__table 一行一家多列；定商标待制度定；写盘后 scan_forbidden。",
    "proc-vendor": "已做 T037 proc-vendor。proc-vendor__eval 出准入|考察|短名单；分数/结论待核；禁止成交结论。",
    "equip": "已做 T036 equip。equip__ledger 写出与 Rust 同表头台账，只抄用户设备名与已给证件。",
    "warehouse": "已做 T036 warehouse。warehouse__log 按行 parse 收发原文；有数只抄、无数 TBD；无盘点不编盈亏。",
    "pack-ship": "sidecar/packing_summary 快照抄进 pack-ship__plan/export；先 health；无则四字段字面 UNSPECIFIED；禁止重算 xyz。",
    "material-site": "已做 T036 material-site。material-site__recon 按行 parse 应耗/领料/盘点；算不出节超则 TBD。",
    "lab-mix": "已做 T033 lab-mix。lab-mix__report 四层目录；无试验数据则施工配比整节待填。",
    "lab-sample": "已做 T033 lab-sample。lab-sample__list 出类别|部位|见证人空|升级路径；组数 [A001]。",
    "lab-record": "已做 T033 lab-record。lab-record__ledger 加报告编号待核|仪器检定|结论待填。",
    "finance-book": "finance-book__check 按单据与业务分开用户金额、发票、合同和审批付款材料；无账套不编科目或分录，入账金额 UNSPECIFIED、复核空白，未付款或认定专项列支。",
    "finance-fund": "已做 T038 finance-fund。finance-fund__plan 出收入/支出窗口，金额 TBD，不当付款指令。",
    "finance-tax": "finance-tax__calendar 按主体/税种登记字面期间、节点、用户税率和来源；辖区只按明确输入，DUAL/冲突税率不应用，申报缴税材料未核，不推算节点、税额或抄历史 KB 当现行规则。",
    "supervision": "supervision__reply 逐来文条目分开事实、拟办、实施主张、期限和证据；已知前置来文头仅供本通知条目，迟补编号不借未来条目。闭合核验与复查签发留空，暂停/复工仅目录，不签指令或许可。",
    "hr-recruit": "按文件、项目、岗位登记需求、职责、任职、面试与用户薪资；不借相邻岗位金额，不作录用或证件结论；Word/Excel 可下载。",
    "hr-labor": "按文件、当事人和合同登记原文、工资与期限，劳动/劳务/派遣/非全日制分表；补偿仍 [A001]，不认定关系、效力或争议结果。",
    "hr-train": "按文件、批次与公司/项目/班组层级登记用户课题、日期、讲师和学时来源；计划与用户实际材料分开，签到、核验结果和签字不预填。",
    "admin-doc": "已做 T041 admin-doc。admin-doc__draft 按文种套请示/纪要/用印三套栏，禁止代用印。",
    "admin-office": "已做 T041 admin-office。admin-office__list 出场地|议程|与会|资料目录，决定栏留空。",
    "it-ops": "it-ops__runbook 按系统岗位分开权限申请、审批回收与故障升级；留痕来源归属，实际执行与签认待填，凭据不入稿。",
    "it-data": "it-data__backup 按系统数据对象保留业务 RPO/RTO 与策略来源；演练步骤、证据与实测时长独立，计划不作恢复成功，凭据不入稿。",
    "it-app": "it-app__srs 按系统模块保留角色功能、规则、验收来源及接口边界；未实现与未验收分开，期望不作交付承诺，真实地址不入稿。",
    "worker-brief": "worker-brief__talk 分班组与作业面整理危险、防护、喊停条件和报告角色；人员不当停工条件，不在场者不当联系人，无来源尺寸只登记不进口播；不是交底签认件或开工指令。",
    "pm-daily": "已做 T039 pm-daily。pm-daily__log 出天气待填|部位|形象（不编百分比）|出勤待填。",
}

_DESIGN_TESTS = {
    "env": "scripts/test_site_records.py",
    "supervision": "scripts/test_site_records.py",
    "survey": "scripts/test_site_records.py",
    "cost": "scripts/test_business_observations.py",
    "variation": "scripts/test_business_observations.py",
    "finance-book": "scripts/test_business_observations.py",
    "finance-tax": "scripts/test_business_observations.py",
    "architecture": "scripts/test_design_basic_drafts.py",
    "structure": "scripts/test_design_basic_drafts.py",
    "geotech": "scripts/test_design_basic_drafts.py",
    "facade": "scripts/test_design_basic_drafts.py",
    "plumbing": "scripts/test_design_services_drafts.py",
    "hvac": "scripts/test_design_services_drafts.py",
    "electrical": "scripts/test_design_services_drafts.py",
    "fire-protect": "scripts/test_design_services_drafts.py",
    "steel": "scripts/test_design_services_drafts.py",
    "landscape": "scripts/test_design_specialties_drafts.py",
    "interior": "scripts/test_design_specialties_drafts.py",
    "intel-weak": "scripts/test_design_specialties_drafts.py",
    "civil-defense": "scripts/test_design_specialties_drafts.py",
    "hydraulic": "scripts/test_design_specialties_drafts.py",
    "port": "scripts/test_design_infrastructure_drafts.py",
    "municipal": "scripts/test_design_infrastructure_drafts.py",
    "bridge": "scripts/test_design_infrastructure_drafts.py",
    "tunnel": "scripts/test_design_infrastructure_drafts.py",
    "traffic": "scripts/test_design_infrastructure_drafts.py",
    "design-coord": "scripts/test_design_infrastructure_drafts.py",
    "method-hazard": "scripts/test_hazard_drafts.py",
    "bim-coord": "scripts/test_bim_observations.py",
    "bim-qto": "scripts/test_bim_observations.py",
    "bim-deliver": "scripts/test_bim_observations.py",
    "construction": "scripts/test_site_briefs.py",
    "emergency": "scripts/test_site_briefs.py",
    "worker-brief": "scripts/test_site_briefs.py",
    "hr-recruit": "scripts/test_hr_observations.py",
    "hr-train": "scripts/test_hr_observations.py",
    "hr-labor": "scripts/test_hr_observations.py",
}


def _next_knife(exp) -> str:
    if exp.id in _NEXT:
        return _NEXT[exp.id]
    return f"在 chat/run 上把 {exp.exclusive[0] if exp.exclusive else 'write'} 的用户栏位写全，缺数 [A001]/UNSPECIFIED。"


def build_post_plans() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for exp in list_experts():
        bench = "pack-agent" if exp.id in PACK_POSTS else "yibiao"
        rec: Dict[str, Any] = {
            "id": exp.id,
            "name": exp.name,
            "category": exp.category,
            "lane": lane_id(exp.category),
            "benchmark": bench,
            "risk": exp.risk,
            "exclusive": list(exp.exclusive),
            "next_knife": _next_knife(exp),
        }
        if exp.id in _DESIGN_TESTS:
            rec["evidence"] = _DESIGN_TESTS[exp.id]
        if bench == "yibiao":
            rec["steps"] = {s: _step_status(exp, s) for s in YIBIAO_STEPS}
        else:
            rec["steps"] = _pack_steps(exp)
        rows.append(rec)
    return rows


def coverage_pairs() -> List[tuple[str, str]]:
    return [(p["id"], p["lane"]) for p in build_post_plans()]


def horizon_order() -> List[str]:
    return [
        "1. 保持 66 岗同一套 chat/run，不回退成一召唤就写盘。",
        "2. bid-parse / bid-compliance / bid-tech 与经营岗矩阵、再审共用同一 handoff。",
        "3. pack-ship 把真实 packing_summary 抄进 list/plan/export，断线 UNSPECIFIED。",
        "4. construction / method-hazard 高风险确认句后出讨论提纲，不写法定专项。",
        "5. T044–T047 的 20 岗已有逐岗专业文书；四组岗位回归见 product-plan §7.2，整体交付已通过本地解压验收。",
        "6. 本地发布验收已完成，后续按用户试用反馈迭代；未发布 GitHub Release。分页订阅等延期项不自动成为新主链。",
    ]


def render_markdown(plans: List[Dict[str, Any]] | None = None) -> str:
    plans = plans or build_post_plans()
    lines = [
        "# 66 岗对照易标 / pack-agent 的长程规划（2026-08-17）",
        "",
        "> 2026-09-12 更新：已做/未做以 [product-plan.md](product-plan.md) §7 / §15 为准；K4 内容闸已覆盖 66/66。",
        "> 本页保留 2026-08-17 的对照结构；设计 20 岗更新为实际专业栏位与测试证据，其余历史下一刀不得单独当成当前队列。",
        "",
        "每岗一条。车道 = `lane-<大类>`。设计岗新文书只整理用户明确给定的数字、规范名称和版本，全部未核验；未知辖区 UNSPECIFIED，DUAL 的依据与接口分别登记。历史 KB 的官方标题、版本或门槛不是新起草器自动填充的规则，不据此宣称法规范已复核。",
        "易标完成度 = parse → outline → qa → kb → write。pack-agent = 数字只抄 solver + list/plan/export + 断线 UNSPECIFIED。",
        "内部讨论草稿。不以可以投标、可以开工、中标率 +N% 为完成目标。L2 专业文书不等于设计求解、IFC 检查或法定签认；L3 仍仅 pack-ship。",
        "",
        "## 长程总序",
        "",
    ]
    lines.extend(f"- {x}" for x in horizon_order())
    lines += ["", "## 覆盖", "", f"- 岗位数：{len(plans)}", ""]
    cur = ""
    for p in plans:
        if p["category"] != cur:
            cur = p["category"]
            lines += ["", f"## 大类 `{cur}` · 车道 `{p['lane']}`", ""]
        lines += [
            f"### {p['id']}",
            "",
            f"- 名称：{p['name']}",
            f"- 子代理/车道：`{p['lane']}`",
            f"- 对照：{p['benchmark']}",
            f"- 独有：{', '.join(p['exclusive'])}",
        ]
        for k, v in (p.get("steps") or {}).items():
            lines.append(f"- {k}：{v}")
        label = "专业实现与边界" if p.get("evidence") else "历史下一刀 / 保持边界"
        lines += [f"- {label}：{p['next_knife']}"]
        if p.get("evidence"):
            lines.append(f"- 岗位回归：`{p['evidence']}`（给定/缺失输入、真实 Markdown/Excel、chat 与高风险门）")
        lines.append("")
    return "\n".join(lines)
