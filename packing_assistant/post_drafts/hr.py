"""T040 human-resources drafts. Pure builders; host owns approval and writes."""

from __future__ import annotations

import html
import re
from typing import Iterable

from packing_assistant.jurisdiction import infer_jurisdiction

DISCLAIMER = (
    "本文件由 Civil Buddy 根据用户输入生成，仅供内部讨论与起草。"
    "不构成设计文件、法定专项施工方案、交底签认件、监理指令、专家论证材料或开工/竣工验收依据。"
)
MISSING = "[A001] 待填"
_TOOLS = {"hr-labor": "hr-labor__check", "hr-train": "hr-train__plan"}
_ALIASES = {
    "项目名称": "project", "工程名称": "project", "项目": "project",
    "企业名称": "company", "公司名称": "company", "公司": "company", "组织部门": "department",
    "合同类型": "contract_type", "文本类型": "contract_type", "合同编号": "contract_id",
    "用人单位": "employer", "雇主": "employer", "单位名称": "employer",
    "单位住所": "employer_address", "单位地址": "employer_address", "负责人": "representative",
    "劳动者": "worker", "雇员": "worker", "员工姓名": "worker",
    "劳动者住址": "worker_address", "证件号码": "identity", "身份证号": "identity",
    "劳务接受方": "client", "委托方": "client", "客户": "client",
    "劳务提供方": "provider", "服务提供方": "provider", "承揽方": "provider",
    "派遣单位": "agency", "用工单位": "host", "派遣协议": "dispatch_agreement",
    "派遣许可证": "dispatch_license", "派遣岗位": "dispatch_role",
    "岗位": "role", "工种": "trade", "班组": "team", "工作地点": "workplace",
    "工作内容": "duties", "职责": "duties", "服务内容": "service", "成果要求": "deliverable",
    "合同期限": "term", "期限": "term", "期限类型": "term_type",
    "签订日期": "signed_date", "用工日期": "start_date", "入职日期": "start_date", "开工日": "start_date",
    "试用期": "probation", "工作时间": "hours", "工时": "hours", "休息休假": "leave",
    "工资": "wage", "月薪": "wage", "基本薪": "wage", "劳动报酬": "wage",
    "报酬": "remuneration", "劳务报酬": "service_fee", "服务费": "service_fee",
    "支付方式": "payment_method", "支付周期": "pay_period", "薪期": "pay_period",
    "支付时间": "pay_date", "结算方式": "settlement", "验收方式": "acceptance",
    "社会保险": "insurance", "社保": "insurance", "劳动保护": "protection",
    "职业危害防护": "hazard_protection", "保密条款": "confidentiality", "培训服务期": "service_term",
    "管理方式": "management", "考勤制度": "attendance_system", "报酬形式": "pay_basis",
    "工具提供方": "tools_owner", "盈亏风险": "business_risk",
    "考勤记录": "attendance_record", "工资记录": "wage_record", "社保记录": "insurance_record",
    "工作证": "work_card", "合同原文": "contract_text", "合同文件": "contract_source",
    "变更事项": "change", "解除事由": "termination_reason", "解除日期": "termination_date",
    "工作年限": "service_years", "月工资基数": "compensation_base", "证据": "evidence",
    "固定津贴": "allowances", "扣款": "deductions", "加班约定": "overtime",
    "医疗福利": "medical", "通知期": "notice", "工资支付台账": "payroll",
    "实名登记": "real_name", "专用账户": "payroll_account", "代发记录": "payroll_agent",
    "计划年度": "year", "进场批次": "batch", "培训对象": "audience",
    "参训人员": "participants", "人员名单": "participants", "参训名单": "participants",
    "参加人数": "headcount", "培训人数": "headcount", "培训时间": "training_date", "培训日期": "training_date",
    "培训地点": "training_place", "讲师": "teacher", "授课人": "teacher",
    "公司级课题": "company_topic", "公司课题": "company_topic",
    "项目级课题": "project_topic", "项目课题": "project_topic",
    "班组级课题": "team_topic", "班组课题": "team_topic",
    "公司级学时": "company_hours", "公司学时": "company_hours",
    "项目级学时": "project_hours", "项目学时": "project_hours",
    "班组级学时": "team_hours", "班组学时": "team_hours",
    "公司级讲师": "company_teacher", "项目级讲师": "project_teacher", "班组级讲师": "team_teacher",
    "学时依据": "training_basis", "制度依据": "training_basis", "适用制度": "training_basis",
    "培训课题": "special_topic", "专项课题": "special_topic", "四新内容": "new_technology",
    "考核方式": "assessment", "证件名称": "certificate", "证号": "certificate_id",
    "到期日": "expiry", "复审计划": "renewal", "档案目录": "archive",
    "保存期限": "retention", "费用安排": "training_cost", "工资安排": "training_pay",
    "辖区": "jurisdiction", "备注": "notes",
}
_LABEL = "|".join(re.escape(label) for label in sorted(_ALIASES, key=len, reverse=True))
_FIELD_RE = re.compile(r"(?<![\w])(" + _LABEL + r")\s*[:：=]\s*")
_EMPTY_RE = re.compile(r"(?:\[A\d+\]\s*)?(?:待填|未知|未提供|待核|未说明|UNSPECIFIED|TBD|N/A)", re.I)


def _cell(value: object) -> str:
    return html.escape(str(value), quote=False).replace("|", "&#124;").replace("\r", " ").replace("\n", "；").strip()


def _table(headers: Iterable[str], rows: Iterable[Iterable[object]]) -> str:
    headings = tuple(headers)
    return "\n".join([
        "| " + " | ".join(headings) + " |",
        "| " + " | ".join("---" for _ in headings) + " |",
        *("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows),
    ])


def _fields(text: str) -> dict[str, str]:
    result: dict[str, str] = {}

    def add(label: str, value: str) -> None:
        value = value.strip(" ,，;；")
        if not value or _EMPTY_RE.fullmatch(value):
            return
        key = _ALIASES[label]
        value = value[:2000].strip()
        if key in result and result[key] != value:
            result[key] += "；" + value
        else:
            result[key] = value

    matches = list(_FIELD_RE.finditer(text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        value = re.split(r"[；;\r\n]", text[match.end():end], maxsplit=1)[0]
        add(match.group(1), value)
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [part.strip() for part in line.strip().removeprefix("|").removesuffix("|").split("|")]
        if len(cells) >= 2 and cells[0] in _ALIASES:
            add(cells[0], cells[1])
    return result


def _value(fields: dict[str, str], key: str) -> str:
    return fields.get(key) or MISSING


def _check_rows(fields: dict[str, str], specs: Iterable[tuple[str, str]]) -> list[tuple[str, str, str]]:
    return [(label, _value(fields, key), "有用户摘录，待核原文与适用性" if fields.get(key) else "材料缺口，待补后核对") for label, key in specs]


def _source_table(zone: str, fields: dict[str, str], *, training: bool = False) -> str:
    names = []
    if zone in {"CN", "DUAL"}:
        names += [("CN", "企业及项目属地安全培训适用文件" if training else "劳动合同法 / 保障农民工工资支付条例")]
    if zone in {"SG", "DUAL"}:
        names += [("SG", "MOM 培训与岗位资格资料" if training else "Employment Act / Key Employment Terms / TADM 资料")]
    if zone == "EU":
        names += [("EU", "成员国及属地适用文件待指定")]
    if not names:
        names = [("UNSPECIFIED", "项目辖区与适用文件待指定")]
    rows = [(scope, name, "UNSPECIFIED", "UNSPECIFIED", "unverified；待核官方原文") for scope, name in names]
    if fields.get("training_basis"):
        rows.append((zone, fields["training_basis"], "UNSPECIFIED", "UNSPECIFIED", "用户指定名称，原文与版本待核"))
    return "仅列查找和核对项，本轮未核实法规全文，不给出期限、金额、学时或适用结论。\n\n" + _table(
        ("辖区", "资料名称或范围", "年份/版本", "条款", "状态"), rows)


def _labor(text: str, *, fields=None, zone=None) -> str:
    fields = dict(_fields(text) if fields is None else fields)
    zone = infer_jurisdiction(text) if zone is None else zone
    type_text = fields.get("contract_type") or text
    low = type_text.lower()
    selected = []
    for label, markers in (
        ("劳动合同", ("劳动合同", "contract of service")),
        ("劳务协议", ("劳务协议", "劳务合同", "contract for service")),
        ("劳务派遣三方材料", ("劳务派遣", "派遣协议")),
        ("非全日制", ("非全日制", "part-time")),
    ):
        if any(marker in low for marker in markers):
            selected.append(label)
    requested = fields.get("contract_type") or (" / ".join(selected) if selected else MISSING)
    if fields.get("remuneration") and len(selected) == 1:
        target = "service_fee" if selected[0] == "劳务协议" else "wage"
        fields.setdefault(target, fields["remuneration"])

    lines = [
        "# 劳动合同 / 劳务协议检查表（AI 草稿 · 内部讨论）", "", DISCLAIMER, "",
        "普法不诉讼。本表不认定法律关系、合同效力或争议结果，不是解除决定、法律意见书或仲裁请求。", "",
        f"- 辖区：{zone}", "- submit_blocked=true", "",
        "## 1 文件与材料登记", "",
        _table(("登记项", "用户提供 / 缺口"), [
            ("项目名称", _value(fields, "project")), ("合同编号", _value(fields, "contract_id")),
            ("用户标示的文本类型", requested), ("合同文件", _value(fields, "contract_source")),
            ("合同原文", _value(fields, "contract_text")), ("用户报酬记录（类型待核）", _value(fields, "remuneration")),
        ]), "", "类型仅按用户用词分组；名称不决定关系，未标示类型的表仍作为待核候选，不能混用。", "",
        "## 2 关系识别事实与证据", "",
        _table(("观察项", "用户材料摘录", "检查状态"), _check_rows(fields, (
            ("现场管理与工作安排", "management"), ("考勤制度", "attendance_system"),
            ("报酬形式", "pay_basis"), ("工具与场地提供", "tools_owner"), ("盈亏风险安排", "business_risk"),
            ("考勤记录", "attendance_record"), ("工资记录", "wage_record"), ("社保记录", "insurance_record"), ("工作证", "work_card"),
        ))), "",
        "合同名称与管理、支付等记录如有不一致，登记为「外观冲突，待认定」。本表不把观察项改写成关系认定。", "",
        "## 3 劳动合同必备条款对照", "", "本表是候选事项核对表；具体必备范围、适用性及完整性待法务按辖区核对。", "",
        _table(("劳动合同事项", "用户材料摘录", "检查状态"), _check_rows(fields, (
            ("用人单位名称", "employer"), ("单位住所", "employer_address"), ("负责人", "representative"),
            ("劳动者姓名", "worker"), ("劳动者住址", "worker_address"), ("身份证件信息", "identity"),
            ("期限", "term"), ("岗位", "role"), ("工作内容", "duties"), ("工作地点", "workplace"),
            ("工作时间", "hours"), ("休息休假", "leave"), ("劳动报酬", "wage"),
            ("薪期", "pay_period"), ("支付方式", "payment_method"), ("社会保险", "insurance"),
            ("劳动保护", "protection"), ("职业危害防护", "hazard_protection"),
        ))), "", "签字或盖章、文本交付及其他适用事项：待核原文与记录，不预填签认。", "",
        "## 4 劳务协议独立检查表", "",
        _table(("劳务协议事项", "用户材料摘录", "检查状态"), _check_rows(fields, (
            ("劳务接受方", "client"), ("劳务提供方", "provider"), ("服务内容", "service"),
            ("成果要求", "deliverable"), ("劳务报酬", "service_fee"), ("结算方式", "settlement"), ("验收方式", "acceptance"),
        ))), "", "不把劳动合同中的雇主、工资自动复制为劳务交易主体或服务费。管理边界与争议约定另核。", "",
        "## 5 劳务派遣三方材料检查表", "",
        _table(("派遣事项", "用户材料摘录", "检查状态"), _check_rows(fields, (
            ("派遣单位", "agency"), ("用工单位", "host"), ("劳动者", "worker"),
            ("派遣岗位", "dispatch_role"), ("派遣协议", "dispatch_agreement"), ("许可材料", "dispatch_license"),
            ("岗位保护安排", "protection"),
        ))), "", "主体责任、岗位范围、比例、培训和保护接口均待核；不认定已符合派遣要求。", "",
        "## 6 非全日制独立检查表", "",
        _table(("非全日制事项", "用户材料摘录", "检查状态"), _check_rows(fields, (
            ("岗位与任务", "duties"), ("工作时间安排", "hours"), ("报酬记录", "wage"),
            ("结算周期", "pay_period"), ("试用约定", "probation"),
        ))), "", "适用范围、工时及支付限制：UNSPECIFIED，待核辖区原文，不套用全日制模板。", "",
        "## 7 订立时点、期限与可约定事项", "",
        _table(("核对项", "用户材料摘录", "检查状态"), _check_rows(fields, (
            ("用工或入职日期", "start_date"), ("签订日期", "signed_date"), ("期限类型", "term_type"),
            ("合同期限", "term"), ("试用期", "probation"), ("培训服务期", "service_term"), ("保密约定", "confidentiality"),
        ))), "", "时间间隔、试用期上限及工资比例均不推算。证件保管、财物收取及特殊保护事项另列待核。", "",
        "## 8 工程建设工资资料接口", "",
        _table(("资料项", "用户材料摘录", "检查状态"), _check_rows(fields, (
            ("实名登记", "real_name"), ("工资支付台账", "payroll"),
            ("专用账户材料", "payroll_account"), ("代发记录", "payroll_agent"), ("支付时间约定", "pay_date"),
        ))), "", "项目适用制度、本人核对记录、总分包接口、维权信息及保存期限均待核；不认定已足额支付。", "",
        "## 9 变更、解除与补偿待核表", "",
        _table(("事项", "用户提供 / 待填", "处理边界"), [
            ("变更事项", _value(fields, "change"), "只登记，不作变更决定"),
            ("解除事由", _value(fields, "termination_reason"), "证据和适用程序待核"),
            ("解除日期", _value(fields, "termination_date"), "通知、证明及移转记录待核"),
            ("工作年限", _value(fields, "service_years"), "用户记录，未计算或认定"),
            ("月工资基数", _value(fields, "compensation_base"), "用户记录，口径待核"),
            ("经济补偿", "[A001]", "未计算；适用情形、年限、工资基数待核"),
            ("欠薪 / 赔偿 / 二倍工资", "[A001]", "不代算、不预测请求结果"),
            ("证据目录", _value(fields, "evidence"), "真实性和完整性待核"),
        ]), "",
    ]
    if zone in {"SG", "DUAL"}:
        lines += ["## 10 SG 关键雇佣条款 KETs 待核表", "",
            "SG 栏独立核对 contract of service / contract for service 的用户材料，适用性未认定。", "",
            _table(("SG 核对项", "用户材料摘录", "检查状态"), _check_rows(fields, (
                ("雇主", "employer"), ("雇员", "worker"), ("职务与职责", "duties"), ("起始日", "start_date"),
                ("期限", "term"), ("工时与休息", "hours"), ("薪期", "pay_period"), ("基本薪", "wage"),
                ("固定津贴", "allowances"), ("扣款", "deductions"), ("加班约定", "overtime"),
                ("休假", "leave"), ("医疗福利", "medical"), ("试用期", "probation"), ("通知期", "notice"),
            ))), "", "准证、薪资申报、发薪记录及覆盖范围另核；本稿不填法定门槛或期限。", ""]
    else:
        lines += ["## 10 辖区补充核对", "", "SG KETs 不自动套用。EU 需指定成员国；辖区未知时保持 UNSPECIFIED。", ""]
    channels = [("适用辖区待定", "协商、调解及属地咨询入口待核", "不替当事人选择路径或预测结果")]
    if zone in {"CN", "DUAL"}:
        channels += [("CN", "劳动人事咨询、监察、调解、仲裁及法律援助入口待核", "联系方式、受理范围与时限 UNSPECIFIED")]
    if zone in {"SG", "DUAL"}:
        channels += [("SG", "雇主协商、工会、TADM 咨询资料待核", "联系方式、受理范围与时限 UNSPECIFIED")]
    lines += ["## 11 争议咨询与资料核对", "", _table(("辖区", "并列核对入口", "边界"), channels), "",
        "## 12 依据与自检", "", _source_table(zone, fields), "",
        "自检：合同类型分表；未认定关系或效力；补偿仍为 [A001]；空缺不补数字；签字空栏；普法不诉讼。", ""]
    return "\n".join(lines)


def _train(text: str, *, fields=None, zone=None) -> str:
    fields = dict(_fields(text) if fields is None else fields)
    zone = infer_jurisdiction(text) if zone is None else zone
    levels = (
        ("公司级", "company", "企业安全制度、异常报告与通用岗位要求"),
        ("项目级", "project", "工程特点、作业环境、风险识别与现场制度"),
        ("班组级", "team", "本工种操作规程、作业交接、事故案例与岗位讲评"),
    )
    course_rows = []
    for label, prefix, suggested in levels:
        course_rows.append((label, fields.get(f"{prefix}_topic") or f"建议课题：{suggested}（待确认）",
            _value(fields, "audience"), _value(fields, f"{prefix}_hours"),
            fields.get(f"{prefix}_teacher") or _value(fields, "teacher"),
            fields.get(f"{prefix}_date") or _value(fields, "training_date"),
            fields.get(f"{prefix}_place") or _value(fields, "training_place"),
            _value(fields, "assessment"), "未实施，结果待记录"))
    lines = [
        "# 培训计划草稿（AI 草稿 · 内部讨论）", "", DISCLAIMER, "",
        "本稿是计划与记录表头，不是考核合格证、上岗许可或安全技术交底签认件。人员资格、学时适用性与考核结果须另核。", "",
        f"- 辖区：{zone}", "- submit_blocked=true", "",
        "## 1 计划登记", "",
        _table(("登记项", "用户提供 / 缺口"), [
            ("工程名称", _value(fields, "project")), ("企业名称", _value(fields, "company")),
            ("计划年度", _value(fields, "year")), ("进场批次", _value(fields, "batch")),
            ("组织部门", _value(fields, "department")), ("工种", _value(fields, "trade")),
            ("班组", _value(fields, "team")), ("总体培训时间", _value(fields, "training_date")),
            ("地点", _value(fields, "training_place")), ("组织讲师", _value(fields, "teacher")),
        ]), "",
        "## 2 对象分层与名册接口", "",
        _table(("对象项", "用户提供 / 需补材料", "计划状态"), [
            ("本次培训对象", _value(fields, "audience"), "待分组确认"),
            ("用户提供的参训名单", _value(fields, "participants"), "仅供组织，不代表到课或签字"),
            ("用户提供的人数", _value(fields, "headcount"), "不据此编造个人名单"),
            ("新进场人员", MISSING, "按实际对象单独造册"),
            ("转岗、换岗与返回岗位人员", MISSING, "原岗位、离岗及新岗位资料待补"),
            ("企业 / 项目负责人及安全管理人员", MISSING, "岗位与证件资料待核"),
            ("特种作业人员", MISSING, "用户工种、操作证与适用范围待核"),
            ("其他管理技术人员、被派遣人员及实习学生", MISSING, "按实际对象另列名册"),
        ]), "",
        "## 3 公司 / 项目 / 班组三层课题计划", "",
        "按公司级、项目级、班组级分别组织。下面是计划安排，学时按用户指定的现行制度核对，不自动宣称满足要求。", "",
        _table(("层级", "课题", "对象", "计划学时", "讲师", "计划时间", "地点", "考核方式", "实施状态"), course_rows), "",
        "## 4 学时与制度核对", "",
        _table(("核对项", "用户提供 / 缺口", "待办"), [
            ("用户指定制度", _value(fields, "training_basis"), "获取原文、版本与适用范围"),
            ("公司级学时", _value(fields, "company_hours"), "核对所选制度，未认定达标"),
            ("项目级学时", _value(fields, "project_hours"), "核对所选制度，未认定达标"),
            ("班组级学时", _value(fields, "team_hours"), "核对所选制度，未认定达标"),
            ("属地及企业制度差异", "UNSPECIFIED", "由管理人员明确采用依据，不自行折中"),
            ("培训工资安排", _value(fields, "training_pay"), "财务与人事按制度核对"),
            ("培训费用安排", _value(fields, "training_cost"), "不编费用或会计科目"),
        ]), "",
        "## 5 四新、专项及派遣实习接口", "",
        _table(("专项", "用户提供 / 缺口", "安排与接口"), [
            ("新工艺、新技术、新材料、新设备", _value(fields, "new_technology"), "确认对象与课题后另列安排"),
            ("用户指定专项课题", _value(fields, "special_topic"), "课件、讲师与实操条件待核"),
            ("被派遣人员", MISSING, "用工方与派遣方培训资料接口待核"),
            ("实习学生", MISSING, "接收单位培训、带教与防护安排待核"),
            ("工序安全技术交底", MISSING, "交安全交底岗；培训记录不能替代签认件"),
        ]), "",
        "## 6 培训签到空表", "",
        "本表不预填姓名或代签；用户提供的人员名单保留在对象登记表，实际到课由现场如实登记。", "",
        _table(("层级", "实际日期", "姓名", "单位 / 班组", "本人签字", "实际学时", "讲师签字"), [
            (label, "（空栏）", "（空栏）", "（空栏）", "（空栏）", "（空栏）", "（空栏）") for label, _, _ in levels
        ]), "",
        "## 7 考核与档案", "",
        _table(("记录项", "用户提供 / 计划", "实际记录"), [
            ("考核方式", _value(fields, "assessment"), "考核结果待填"),
            ("课件、时间及讲师记录", _value(fields, "archive"), "待归档核对"),
            ("签到、实操与补训记录", MISSING, "待现场形成"),
            ("保存期限", _value(fields, "retention"), "制度原文与版本待核"),
            ("培训完成状态", "计划待实施", "不代填合格或有效"),
        ]), "",
        "## 8 证件与复审计划", "",
        _table(("证件名称", "用户提供证号", "到期日", "复审计划", "核验结果"), [
            (_value(fields, "certificate"), _value(fields, "certificate_id"), _value(fields, "expiry"), _value(fields, "renewal"), "未核验")
        ]), "", "人员证件真伪、有效期与岗位范围另核；不从培训计划推导资格有效。", "",
        "## 9 依据、自检与相邻岗位", "", _source_table(zone, fields, training=True), "",
        "自检：三层课题分别列出；课题建议与用户字段有区分；学时未擅自选定；名单不等于签到；结果不预填。", "",
        "工序交底交安全交底岗，班前口播交工友白话岗；劳动合同事项交劳动关系岗，报名差旅与费用交行政及财务。", "",
    ]
    return "\n".join(lines)


def build_draft(expert_id: str, tool_name: str, text: str) -> str | None:
    """Return a post-specific draft only for its exact exclusive tool."""
    if _TOOLS.get(expert_id) != tool_name:
        return None
    from packing_assistant.post_drafts.hr_observations import build_draft as owned_draft
    return owned_draft(expert_id, tool_name, text)


def _labor_records(text: str) -> list[str]:
    """Keep repeated people/contracts separate, including supplied table rows."""
    # Normalize explicitly labeled Markdown rows before identifying boundaries.
    # A matrix row describes one record; a two-column field table is one record.
    normalized = []
    headers: list[str] = []
    row_break = "\x1e"
    for line in text.splitlines():
        if line.lstrip().startswith("|"):
            cells = [part.strip() for part in line.strip().removeprefix("|").removesuffix("|").split("|")]
            if cells and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                continue
            if len(cells) >= 2 and sum(cell in _ALIASES for cell in cells) >= 2:
                headers = cells
                continue
            if headers:
                normalized.append(row_break + "；".join(f"{label}：{value}" for label, value in zip(headers, cells)
                                           if label in _ALIASES and value))
                continue
            if len(cells) >= 2 and cells[0] in _ALIASES:
                normalized.append(f"{cells[0]}：{cells[1]}")
                continue
        else:
            headers = []
        normalized.append(line)
    source = "\n".join(normalized)
    if row_break in source:
        prefix, *rows = source.split(row_break)
        # A table row is a record regardless of column order or empty identity.
        # Keep any preceding labeled record separate; prose context can prefix
        # the first row without assigning fields from another table row.
        if any(_ALIASES[m.group(1)] in {"worker", "contract_id", "client", "provider"}
               for m in _FIELD_RE.finditer(prefix)):
            rows.insert(0, prefix)
        elif rows:
            rows[0] = prefix + rows[0]
        return [row.strip() for row in rows]
    starts = [0]
    seen: set[str] = set()
    boundaries = {"worker", "contract_id", "client", "provider"}
    for match in _FIELD_RE.finditer(source):
        key = _ALIASES[match.group(1)]
        if key not in boundaries:
            continue
        if key in seen:
            starts.append(match.start())
            seen.clear()
        seen.add(key)
    return [source[start:end].strip() for start, end in zip(starts, starts[1:] + [len(source)])]
