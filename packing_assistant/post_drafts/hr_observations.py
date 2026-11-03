"""Source-owned HR materials, not hiring, attendance or legal determinations."""
from __future__ import annotations

import html
import re

from packing_assistant.post_drafts import hr
from packing_assistant.post_drafts.design_observations import _breaks
from packing_assistant.post_drafts.procurement import _source_name
from packing_assistant.word_export import _table_cells

UNKNOWN = "UNSPECIFIED"
TOOLS = {"hr-recruit": "hr-recruit__brief", "hr-train": "hr-train__plan", "hr-labor": "hr-labor__check"}
ALIASES = dict(hr._ALIASES)
ALIASES.update({
    "资料阶段": "stage", "项目阶段": "stage", "总体辖区": "project_zone", "项目辖区": "project_zone",
    "对象辖区": "jurisdiction", "招聘岗位": "role", "岗位名称": "role", "职位": "role",
    "需求部门": "demand_department", "用人部门": "demand_department", "需求项目": "demand_department",
    "用工类型": "employment_type", "编制性质": "employment_type", "用工性质": "employment_type",
    "招聘人数": "headcount", "需求人数": "headcount", "人数": "headcount",
    "计划到岗": "arrival", "计划到岗日期": "arrival", "到岗日期": "arrival", "到岗时间": "arrival",
    "主要职责": "duties", "岗位职责": "duties", "任职": "qualification", "任职条件": "qualification",
    "任职要求": "qualification", "经验年限": "experience", "学历专业": "education",
    "证书要求": "certificate", "证件要求": "certificate", "薪资": "salary", "薪酬": "salary",
    "面试重点": "interview", "面试提纲": "interview", "面试问法": "interview",
    "录用前核验": "verification", "证件核验": "verification", "核验证据": "verification_source",
    "轮休": "rotation", "派驻": "stationing", "报告对象": "report_to", "工作条件": "conditions",
    "课题": "special_topic", "培训目标": "goal", "培训层级": "level", "层级": "level",
    "公司级日期": "company_date", "项目级日期": "project_date", "班组级日期": "team_date",
    "公司级地点": "company_place", "项目级地点": "project_place", "班组级地点": "team_place",
    "计划学时": "hours_plan", "学时来源": "training_basis", "学时": "hours_plan",
    "实际培训日期": "actual_date", "实际学时": "actual_hours", "签到记录": "attendance_source",
    "用户考核记录": "assessment_source", "考核记录": "assessment_source", "培训完成记录": "completion_source",
    "资料来源": "source", "来源文件": "source", "适用依据": "basis", "依据": "basis",
    "依据版本": "basis_version", "依据来源": "basis_source", "CN依据": "CN_basis",
    "SG依据": "SG_basis", "EU依据": "EU_basis", "文件版本": "version", "版本": "version",
    "待补资料": "pending", "缺项": "pending", "待核项": "pending", "咨询入口": "consultation",
})
ALIASES = {key.casefold(): value for key, value in ALIASES.items()}
META = {"project", "company", "stage", "project_zone"}
BASES = {"basis", "basis_version", "basis_source", "training_basis", "CN_basis", "SG_basis", "EU_basis"}
IDENTITY = {"hr-recruit": ("role", "recruit_id"), "hr-train": ("audience", "batch"),
            "hr-labor": ("worker", "contract_id", "client", "provider")}
ALIASES.update({"招聘编号": "recruit_id", "招聘需求编号": "recruit_id"})
ALIASES.update({"project": "project", "company": "company", "jurisdiction": "jurisdiction", "role": "role",
                "employee": "worker", "employer": "employer", "contract id": "contract_id", "contract type": "contract_type",
                "salary": "salary", "wage": "wage", "audience": "audience", "headcount": "headcount",
                "training date": "training_date", "teacher": "teacher", "source": "source"})
MISSING = re.compile(r"未提供|未给|未定|未批|待定|待填|待核|待补|未知|没定|没登记|还没|没有|未说明|UNSPECIFIED|TBD|N/A", re.I)
UNSAFE = re.compile(r"比如|例如|假设|如果|自行计算|自己算|不采用|不是|是否|[？?]")
DISCRIMINATION = re.compile(r"(?:男|女)性?优先|只(?:招|要)(?:男|女|本地)|\d+\s*岁以下|婚育|民族限制|户籍限制|挂证")
DATE = r"(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}月\d{1,2}[号日]|今天|明天|下周)"
FACTS = {"salary", "wage", "compensation_base", "service_years", "headcount", "arrival", "probation", "term",
         "start_date", "signed_date", "training_date", "teacher", "company_teacher", "project_teacher", "team_teacher",
         "company_date", "project_date", "team_date", "company_hours", "project_hours", "team_hours", "hours_plan",
         "actual_date", "actual_hours", "expiry", "certificate_id"}
SOURCES = {"source", "basis_source", "verification_source", "contract_source", "attendance_source", "assessment_source", "completion_source"}


def _clean(raw):
    value = raw.strip(" \t\r\n，,；。")
    return value if re.search(r"&(?:#\d+|#x[\da-f]+|[a-z][\da-z]+);$", value, re.I) else value.rstrip(";")


def _typed(key, raw):
    value = _clean(raw)
    if key in {"jurisdiction", "project_zone"}:
        return {"CN": "CN", "SG": "SG", "EU": "EU", "DUAL": "DUAL", "中国": "CN", "新加坡": "SG"}.get(value.upper(), UNKNOWN)
    if not value or MISSING.fullmatch(value):
        return UNKNOWN
    if key in FACTS | SOURCES and (MISSING.search(value) or UNSAFE.search(value)):
        return UNKNOWN
    if key in {"salary", "wage", "compensation_base"} and re.search(r"估计|估算|行情|市场|大概|随便|编个|编一个", value):
        return UNKNOWN
    if key == "contract_type" and UNSAFE.search(value):
        return UNKNOWN
    if key in {"qualification", "interview", "certificate", "employment_type"} and DISCRIMINATION.search(value):
        return UNKNOWN
    if key in {"actual_date", "actual_hours"} | SOURCES and re.search(r"计划|预计|拟|将提供", value):
        return UNKNOWN
    return value


def _natural(clause, post, level):
    if UNSAFE.search(clause):
        return [], level
    pairs = []
    def emit(key, pattern, group=1):
        match = re.search(pattern, clause)
        if match:
            literal = match.group(group) or next((g for g in match.groups() if g is not None), UNKNOWN)
            pairs.append((key, UNKNOWN if key in FACTS and MISSING.search(clause) else literal))
    if re.fullmatch(r"CN|SG|EU|DUAL|中国|新加坡", clause, re.I):
        return [("jurisdiction", clause)], level
    if post == "hr-recruit":
        roles = r"专职安全员|施工员|资料员|劳务员|质量员|材料员|机械员|测量员|项目经理|安全员"
        emit("role", r"(?:招\d+名|招聘简报\s*|^|另一个是)("+roles+r")")
        emit("headcount", r"(?:招|要)(\d+名|\d+个人|\d+人)")
        emit("headcount", r"(?:"+roles+r")(\d+个)")
        emit("demand_department", r"^(.+?(?:部|室))(?:要招|要的)$")
        emit("demand_department", r"^(.+?(?:部|室))要招")
        emit("demand_department", r"归(.+?(?:部|室))管")
        emit("employment_type", r"(自有职工|劳务派遣|分包自有工人)")
        emit("arrival", "("+DATE+r"(?:之前)?)(?:到岗)")
        emit("workplace", r"^驻(.+)$")
        emit("rotation", r"^(.+轮休)$")
        emit("qualification", r"^要求(.+)$")
        emit("qualification", r"^(会.+)$")
        emit("experience", r"((?:\d+|[一二三四五六七八九十]+)年(?:以上)?)(?=.*经验)|经验((?:\d+|[一二三四五六七八九十]+)年)")
        emit("certificate", r"^持(.+)$")
        emit("salary", r"月薪(?:谈到)?\s*([\d,.]+(?:元)?)(?:已经批了|$)")
        emit("interview", r"面试重点(?:问)?(.+)$")
        emit("duties", r"^负责(.+)$")
        emit("verification", r"入职前.*?(?:查|核验)(.+)$")
        if re.search(r"两个岗|两岗|各岗位", clause) and pairs:
            pairs.insert(0, ("_shared", "verification"))
        if re.search(r"到岗.*(?:还没|未定|未批)", clause):
            pairs.append(("arrival", UNKNOWN))
        if re.search(r"工资|薪资|月薪", clause) and MISSING.search(clause):
            pairs.append(("salary", UNKNOWN))
    elif post == "hr-labor":
        person = re.search(r"(?:一个是|另一个是|核一下)(钢筋工|食堂帮厨|模板工|木工|木工班组带班|电工|焊工|测量员)([一-鿿]{2,4})(?:的劳动合同|$)", clause)
        if person:
            pairs.extend((("worker", person.group(2)), ("role", person.group(1))))
        emit("employer", r"用人单位(?:写的|是|为)?(.+)$")
        emit("employer", r"跟(.+?)签的劳动合同")
        emit("contract_type", r"(劳动合同|劳务协议|劳务派遣|非全日制|contract of service|contract for service)")
        emit("start_date", "("+DATE+r")(?:用工|进的场|入职)")
        emit("term", r"合同期(?:限)?(?:为|是)?(.+)$")
        emit("role", r"岗位(?:是|为)(.+)$")
        emit("workplace", r"工作地点\s*(.+)$")
        emit("wage", r"月工资\s*([\d,.]+(?:元)?)")
        emit("probation", r"试用期(?:写了|是|为)?(.+)$")
        emit("contract_id", r"合同编号\s*([A-Za-z0-9._/-]+)")
        emit("insurance", r"(社保交了)")
        emit("hours", r"一天干(.+)$")
        if "进场" in clause and MISSING.search(clause):
            pairs.append(("start_date", UNKNOWN))
        if re.search(r"工资|钱|报酬", clause) and MISSING.search(clause):
            pairs.append(("wage", UNKNOWN))
    else:
        group = re.search(r"(?:这批新进场的是|另一拨是刚进场的|^)(.+?(?:班|工))(?:那拨)?(\d+)(?:个人|人)", clause)
        if group:
            pairs.extend((("audience", group.group(1)), ("headcount", group.group(2))))
            level = None
        lev = re.search(r"^(公司级|项目级|班组级)(?!课题|学时|讲师|日期|地点)", clause)
        if lev:
            level = {"公司级": "company", "项目级": "project", "班组级": "team"}[lev.group(1)]
        if level:
            emit(level+"_date", "("+DATE+r"(?:下午|上午)?)(?:讲|在|由|$)")
            emit(level+"_teacher", r"由(.+?)讲")
            emit(level+"_topic", r"讲(.+)$")
            emit(level+"_place", r"在(.+?)由")
            if "讲师" in clause and MISSING.search(clause):
                pairs.append((level+"_teacher", UNKNOWN))
            if "日期" in clause and MISSING.search(clause):
                pairs.append((level+"_date", UNKNOWN))
        else:
            emit("training_date", "("+DATE+r"(?:下午|上午)?)(?:讲|培训|$)")
            emit("teacher", r"讲师请了(?:市里的)?(.+)$")
        emit("team_topic", r"班组长自己讲(.+)$")
        if "班组长自己讲" in clause:
            pairs.append(("team_teacher", "班组长"))
        emit("special_topic", r"要的是(.+)$")
        emit("training_basis", r"学时按(.+?)(?:算|$)")
        emit("assessment", r"考核用(.+)$")
        if "学时" in clause and MISSING.search(clause):
            pairs.append(("hours_plan", UNKNOWN))
        if "考核方式" in clause and MISSING.search(clause):
            pairs.append(("assessment", UNKNOWN))
    return pairs, level


def _literal_hr_lines(text, label):
    """Scenarios stay excluded; explicitly named people are not scenarios."""
    scenario = False
    for line in text.splitlines():
        stripped = line.strip()
        if _source_name(stripped) is not None or stripped.startswith(("【本轮用户要求", "【此前用户资料")):
            scenario = False
            yield line
            continue
        if stripped.startswith("|"):
            yield "" if scenario else line
            continue
        pieces, cursor = [], 0
        for boundary in [*_breaks(line), None]:
            end = boundary.start() if boundary else len(line)
            clause = line[cursor:end]
            if re.match(r"\s*(?:本次实际情况|实际情况|真实记录)\s*[:：]", clause):
                scenario = False
                clause = re.sub(r"^\s*(?:本次实际情况|实际情况|真实记录)\s*[:：]\s*", "", clause)
            elif re.search(r"比如|例如|假设|如果|^\s*(?:示例|样例)\s*[:：]", clause) and not label.match(clause.strip()):
                scenario = True
            pieces.append(" "*len(clause) if scenario else clause)
            if boundary:
                pieces.append(boundary.group())
                cursor = boundary.end()
        yield "".join(pieces)


def parse_records(text, post):
    label = re.compile(r"(?<![\w])("+"|".join(re.escape(a) for a in sorted(ALIASES, key=len, reverse=True))+r")\s*[:：=]\s*", re.I)
    scope = {"project_zone": UNKNOWN, "_sources": {}}
    contexts, records = [scope], []
    active, origin, file_id, header, level = None, "", 0, None, None
    def context_id():
        return next(i for i, context in enumerate(contexts) if context is scope)
    def new():
        nonlocal active
        active = {"_context": context_id(), "_origin": origin, "_file": file_id, "_sources": {}}
        records.append(active)
    def put(row, key, raw, position):
        value = _typed(key, raw)
        previous = row.get(key)
        if value == UNKNOWN:
            if previous not in {None, UNKNOWN}:
                row["pending"] = "；".join(filter(None, (row.get("pending"), key+"原值待复核："+previous)))
            row[key] = UNKNOWN
            row["_sources"].pop(key, None)
            if _clean(raw) not in {"", UNKNOWN}:
                note = "招聘条件含与岗位无关限制，未转为招聘要求" if key in {"qualification", "interview", "certificate", "employment_type"} and DISCRIMINATION.search(raw) else key+"："+_clean(raw)
                row["pending"] = "；".join(filter(None, (row.get("pending"), note)))
        else:
            row[key] = value if previous in {None, UNKNOWN} else "；".join(dict.fromkeys((previous, value)))
            row["_sources"][key] = "；".join(dict.fromkeys(filter(None, (row["_sources"].get(key), position))))
    for line_number, line in enumerate(_literal_hr_lines(text, label), 1):
        stripped = line.strip()
        if _source_name(stripped) is not None or stripped.startswith(("【本轮用户要求", "【此前用户资料")):
            origin = _source_name(stripped) or stripped.strip("【】")
            file_id += 1
            scope = {"project_zone": UNKNOWN, "_sources": {}}
            contexts.append(scope)
            active, header, level = None, None, None
            continue
        position = (origin+" · " if origin else "")+f"输入第{line_number}行（非原件页码）"
        pairs, matrix, shared = [], False, False
        if stripped.startswith("|"):
            cells = [html.unescape(c).strip() for c in _table_cells(stripped)]
            if any(cells) and all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            if header is not None:
                matrix = True
                pairs = [(ALIASES[h.casefold()], cells[i] if i < len(cells) else "") for i, h in enumerate(header) if h.casefold() in ALIASES]
                active, level = None, None
                scope = {**scope, "_sources": dict(scope["_sources"])}
                contexts.append(scope)
                pairs.sort(key=lambda p: {"project": 0, "company": 1, "stage": 2, "project_zone": 3}.get(p[0], 4))
                if post == "hr-train":
                    declared = next((raw for key, raw in pairs if key == "level"), "")
                    level = {"公司级": "company", "项目级": "project", "班组级": "team"}.get(declared)
                    if level:
                        mapping = {"special_topic": "topic", "training_date": "date", "teacher": "teacher", "training_place": "place", "hours_plan": "hours"}
                        pairs = [(level+"_"+mapping[key] if key in mapping else key, raw) for key, raw in pairs]
            elif len(cells) >= 2 and cells[0].casefold() in ALIASES and cells[1].casefold() not in ALIASES and not any(cells[2:]):
                pairs = [(ALIASES[cells[0].casefold()], cells[1])]
            elif cells and cells[0].casefold() in ALIASES and sum(c.casefold() in ALIASES for c in cells) >= 2:
                header = cells
                continue
            else:
                continue
        else:
            if header is not None:
                active, header, level = None, None, None
            cursor = 0
            for boundary in [*_breaks(line), None]:
                end = boundary.start() if boundary else len(line)
                clause = line[cursor:end].strip()
                cursor = boundary.end() if boundary else len(line)
                if not clause:
                    continue
                matches = list(label.finditer(clause))
                if matches:
                    found = [(ALIASES[m.group(1).casefold()], clause[m.end():matches[i+1].start() if i+1 < len(matches) else len(clause)]) for i, m in enumerate(matches)]
                    if post == "hr-train":
                        mapped = []
                        for key, raw in found:
                            if key == "level":
                                level = {"公司级": "company", "项目级": "project", "班组级": "team"}.get(_clean(raw))
                            elif key in IDENTITY[post]:
                                level = None
                            if level and key in {"special_topic", "training_date", "teacher", "training_place", "hours_plan"}:
                                key = level+"_"+{"special_topic": "topic", "training_date": "date", "teacher": "teacher", "training_place": "place", "hours_plan": "hours"}[key]
                            mapped.append((key, raw))
                        found = mapped
                else:
                    found, level = _natural(clause, post, level)
                for key, raw in found:
                    if key == "_shared":
                        shared = True
                    else:
                        pairs.append((key, raw))
        if matrix:
            new()
        for key, raw in pairs:
            if post == "hr-recruit" and key == "wage":
                key = "salary"
            if post == "hr-labor" and key == "employment_type":
                key = "contract_type"
            scoped = key in META or key in BASES | {"verification"} and (shared or active is None and not any(k in IDENTITY[post] for k, _ in pairs))
            if key == "jurisdiction" and active is None and not any(k in IDENTITY[post] for k, _ in pairs):
                key, scoped = "project_zone", True
            if scoped:
                if matrix and active is not None and not any(not k.startswith("_") for k in active):
                    records.remove(active)
                    active = None
                populated = active is not None and any(not k.startswith("_") and k != "contract_type" for k in active)
                if scope.get(key) not in {None, UNKNOWN, _typed(key, raw)} or populated and key in META and scope.get(key) in {None, UNKNOWN}:
                    scope = {"project_zone": UNKNOWN, "_sources": {}} if key == "project" else {**scope, "_sources": dict(scope["_sources"])}
                    scope.pop(key, None)
                    scope["_sources"].pop(key, None)
                    contexts.append(scope)
                    active, level = None, None
                put(scope, key, raw, position)
                continue
            if not matrix and key in IDENTITY[post] and active is not None and active.get(key) not in {None, UNKNOWN}:
                active, level = None, None
            if active is None:
                new()
            put(active, key, raw, position)
            # A number embedded in an explicitly provided audience or requirement
            # is kept in its own quantity/experience slot; no unit conversion.
            if key == "audience":
                number = re.search(r"(\d+)\s*(?:个人|人|名)", raw)
                if number and not MISSING.search(raw):
                    put(active, "headcount", number.group(1), position)
            if key == "qualification":
                certificate = re.search(r"持(.+)$", raw)
                if certificate:
                    put(active, "certificate", certificate.group(1), position)
        if shared:
            for record in records:
                if record["_file"] == file_id and record["_context"] == context_id():
                    for key, raw in pairs:
                        if key == "verification":
                            put(record, key, raw, position)
    result = []
    for row in records or [{"_context": len(contexts)-1, "_file": file_id, "_origin": origin, "_sources": {}}]:
        context = contexts[row["_context"]]
        inherited = {k: v for k, v in context.items() if not k.startswith("_") and k != "project_zone"}
        if "jurisdiction" in row and row["jurisdiction"] != context.get("project_zone", UNKNOWN):
            for key in ("basis", "basis_version", "basis_source", "training_basis"):
                inherited.pop(key, None)
        sources = {key: val for key, val in context["_sources"].items() if key in inherited and key not in row}
        sources.update(row["_sources"])
        result.append({**inherited, **row, "_sources": sources, "_project_zone": context.get("project_zone", UNKNOWN)})
    return result


def _get(row, key):
    return row.get(key) or UNKNOWN


def _zone(row):
    return row.get("jurisdiction", row.get("_project_zone", UNKNOWN))


def _support(row):
    zone = _zone(row)
    rows = [(zone, _get(row, "basis"), _get(row, "basis_version"), UNKNOWN, _get(row, "basis_source"), "unverified")]
    rows.extend((z, _get(row, z+"_basis"), UNKNOWN, UNKNOWN, UNKNOWN, "unverified") for z in ("CN", "SG", "EU") if row.get(z+"_basis") not in {None, UNKNOWN})
    provenance = [(key, value, row["_sources"].get(key, UNKNOWN)) for key, value in row.items() if not key.startswith("_") and value != UNKNOWN]
    return "\n\n".join(("### 对象依据与缺口（未核官方原文）", hr._table(("辖区", "用户依据", "版本", "条款", "依据来源", "状态"), rows),
        hr._table(("待补资料", "核验状态", "签认"), [(_get(row, "pending"), "未核验", "（空栏）")]),
        "### 字段来源定位", hr._table(("字段", "用户摘录", "输入位置（非原件页码）"), provenance or [(UNKNOWN, UNKNOWN, UNKNOWN)])))


def _recruit(row):
    table = hr._table
    zone = _zone(row)
    lines = ["# 招聘简报（AI 草稿 · 内部讨论）", "", hr.DISCLAIMER, "",
        "本稿是岗位说明与面试讨论表，不是录用通知、薪酬批复或证件真伪结论。", "", f"- 辖区：{zone}", "- submit_blocked=true", "",
        "## 1 岗位声明", "", table(("岗位名称", "需求部门", "用工类型", "招聘人数", "计划到岗", "项目名称"),
            [tuple(_get(row, k) for k in ("role", "demand_department", "employment_type", "headcount", "arrival", "project"))]), "",
        "## 职责", "", table(("岗位", "主要职责", "职责界面", "报告对象"),
            [(_get(row, "role"), _get(row, "duties"), "用户职责待逐项核对，不替代其他岗位责任", _get(row, "report_to"))]), "",
        "## 任职", "", "仅登记用户门槛，未判断申请者是否满足；未给门槛保持待填。", "",
        table(("任职条件", "经验年限", "学历专业", "证书要求", "核验证据"),
            [tuple(_get(row, k) for k in ("qualification", "experience", "education", "certificate", "verification_source"))]), "",
        "## 4 工作地点", "", table(("工作地点", "派驻", "轮休", "工作条件"),
            [tuple(_get(row, k) for k in ("workplace", "stationing", "rotation", "conditions"))]), "",
        "## 面试问法", "", table(("职责", "任职要求", "面试问法", "核验证据", "待填项"),
            [(_get(row, "duties"), _get(row, "qualification"), _get(row, "interview"), _get(row, "verification_source"), _get(row, "pending"))]), "",
        "追问仅围绕已列职责、经验和专业材料，不编标准答案、分数或与岗位无关的个人问题。", "",
        "## 薪资", "", table(("用户薪资", "核对边界"), [(_get(row, "salary") if row.get("salary") not in {None, UNKNOWN} else "待填 / UNSPECIFIED", "不编市场带宽；用户金额未形成录用承诺")]), "",
        "[A001] 未给报酬由用人部门补充，不从岗位或辖区推算。", "",
        "## 5 告知事项", "", "工作内容、地点、条件、职业危害及报酬资料由用人部门核对，未代签告知或批准用工。", "",
        "## 7 证件核验", "", table(("录用前核验", "证书要求", "核验证据", "核验结果", "签字"),
            [(_get(row, "verification"), _get(row, "certificate"), _get(row, "verification_source"), UNKNOWN, "（空栏）")]), "",
        "## 8 相邻岗位与依据", "", "合同材料交劳动关系岗；进场培训交培训岗；作业交底和许可不在招聘简报内。", "", _support(row)]
    if zone in {"SG", "DUAL"}:
        lines += ["", "SG 查找项：Fair Consideration Framework / Key Employment Terms；版本与条款未核，不填广告或录用期限。"]
    if zone in {"CN", "DUAL"}:
        lines += ["", "CN 查找项：劳动合同法及属地招聘材料；版本与条款未核，不填法定期限。"]
    return "\n".join(lines)


def _overview(records, post):
    specs = {
        "hr-recruit": ("逐岗招聘需求（用户资料未核）", (
            ("岗位", "role"), ("需求部门", "demand_department"), ("用工类型", "employment_type"),
            ("人数", "headcount"), ("计划到岗", "arrival"), ("工作地点", "workplace"), ("用户薪资", "salary"))),
        "hr-train": ("逐批培训计划（不是完成记录）", (
            ("培训对象", "audience"), ("进场批次", "batch"), ("人数", "headcount"), ("计划日期", "training_date"),
            ("计划讲师", "teacher"), ("课题", "special_topic"), ("学时来源", "training_basis"))),
        "hr-labor": ("逐人合同登记（关系与条款待核）", (
            ("劳动者", "worker"), ("用人单位", "employer"), ("合同编号", "contract_id"), ("合同类型", "contract_type"),
            ("岗位", "role"), ("用户工资", "wage"), ("合同期限", "term"), ("用工日期", "start_date"))),
    }
    title, columns = specs[post]
    headers = ("项目", "阶段", "对象辖区")+tuple(label for label, _ in columns)+("用户资料来源", "核验结果", "签字")
    rows = [(_get(row, "project"), _get(row, "stage"), _zone(row))+tuple(_get(row, key) for _, key in columns)
            +(_get(row, "source") if row.get("source") else row["_origin"] or UNKNOWN, UNKNOWN, "（空栏）") for row in records]
    return "# 人力资料整理（AI 草稿 · 内部讨论）\n\n- submit_blocked=true\n\n## "+title+"\n\n"+hr._table(headers, rows)+"\n\n"


def build_draft(expert_id, tool_name, text):
    if TOOLS.get(expert_id) != tool_name:
        return None
    records = parse_records(text or "", expert_id)
    rendered = []
    for index, row in enumerate(records, 1):
        if expert_id == "hr-recruit":
            body = _recruit(row)
        else:
            fields = {key: value for key, value in row.items() if not key.startswith("_") and value != UNKNOWN}
            renderer = hr._labor if expert_id == "hr-labor" else hr._train
            body = renderer("", fields=fields, zone=_zone(row))+"\n\n"+_support(row)
            if expert_id == "hr-train":
                body += "\n\n### 用户实际培训材料（摘录待核，不作完成结论）\n\n"+hr._table(
                    ("用户实际日期", "用户实际学时", "签到来源", "用户考核记录", "用户完成记录", "核验结果"),
                    [tuple(_get(row, k) for k in ("actual_date", "actual_hours", "attendance_source", "assessment_source", "completion_source"))+(UNKNOWN,)])
        if len(records) > 1:
            body = f"# 材料记录 {index}（字段不跨记录借用）\n\n"+body
        rendered.append(body)
    return _overview(records, expert_id)+"\n\n".join(rendered)
