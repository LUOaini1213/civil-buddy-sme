"""Literal commercial/finance records. No pricing, accounting or tax engine."""
from __future__ import annotations

import html
import re

from packing_assistant.post_drafts.design_observations import _breaks
from packing_assistant.post_drafts.hr_observations import _literal_hr_lines
from packing_assistant.post_drafts.procurement import _source_name
from packing_assistant.word_export import _table_cells

UNKNOWN = "UNSPECIFIED"
TOOLS = {"cost": "cost__takeoff", "variation": "variation__form", "finance-book": "finance-book__check", "finance-tax": "finance-tax__calendar"}
DISCLAIMER = ("AI 草稿 · 内部讨论。本文件仅登记用户资料，未核验数量、报价、指令、票据、审批或规则原文。"
              "不构成结算审定价、已签认签证、记账凭证、审计结论、纳税申报或税务意见。"
              "未执行计量、组价、入账、付款或申报，原件及适用性须由具备相应资格的人员复核签认。")
COMMON = {
    "project": ("项目名称", "工程名称", "项目", "工程", "Project"), "stage": ("资料阶段", "项目阶段"),
    "project_zone": ("总体辖区", "项目辖区", "全局辖区"), "zone": ("辖区", "对象辖区", "地区", "Jurisdiction"),
    "version": ("文件版本", "版本"), "basis": ("适用依据", "依据", "制度依据"),
    "basis_version": ("依据版本",), "basis_source": ("依据来源",), "source": ("资料来源", "来源文件", "来源", "Source"),
    "CN_basis": ("CN依据",), "SG_basis": ("SG依据",), "EU_basis": ("EU依据",),
    "pending": ("待补资料", "待核项", "缺项"), "author": ("编制人", "制表人", "填表人"),
    "draft_date": ("编制日期",), "reviewer": ("复核人",),
}
FIELDS = {
    "cost": {
        "item": ("清单项", "分部分项", "项目名称特征", "名称特征", "项目特征", "名称", "分项", "Item"),
        "code": ("项目编码", "清单编码", "编码", "项目编号", "Code"), "location": ("部位", "楼栋", "单位工程"),
        "quantity": ("用户数量", "数量", "工程量", "Quantity"), "unit": ("单位", "Unit"),
        "quantity_source": ("数量来源", "计量依据", "图纸", "图号"), "measurement": ("计量规则", "计量口径"),
        "contract_form": ("合同形式", "合同类型", "计价方式"), "inquiry": ("询价", "询价对象", "供应商"),
        "quoted_price": ("用户报价", "单价", "询价单价", "报价"), "price_source": ("单价来源", "价格来源", "报价来源"),
        "currency": ("币种", "Currency"), "cost_elements": ("组价资料", "人材机资料"), "fees": ("取费资料",),
        "tax_input": ("税费资料",), "model_quantity": ("模型量",), "drawing_quantity": ("图示量",), "site_quantity": ("现场实测量",),
    },
    "variation": {
        "event": ("事项", "签证事项", "变更事项", "事件", "Event"), "id": ("变更编号", "签证编号", "事项编号", "编号", "Event ID"),
        "kind": ("文件类型", "文种"), "location": ("部位", "位置"), "date": ("发生时间", "发生日期", "时间", "日期"),
        "facts": ("事实原文", "事实经过", "事由", "原因"), "instruction": ("指令", "用户指令"),
        "instruction_source": ("指令来源", "指令出处"), "issuer": ("提出人", "指令人", "通知人"),
        "instruction_date": ("下发日期", "指令日期"), "measurer": ("测量员", "测量人", "记录人"),
        "quantity": ("工程量", "用户工程量", "数量"), "unit": ("单位",), "material": ("材料", "材料名称"),
        "spec": ("规格", "材质", "强度等级"), "quantity_kind": ("数量类型",), "direction": ("增减", "变动方向"),
        "quantity_source": ("数量来源", "原始记录", "计量来源"), "measurement": ("计量口径", "计量规则"),
        "machine": ("机械", "设备", "机具"), "machine_days": ("台班", "机械台班"), "labor_days": ("工日", "窝工工日"),
        "evidence": ("附件", "附件证据", "证据目录", "照片资料"), "owner": ("责任单位", "责任人"),
        "contract": ("合同依据", "合同编号"), "claim_amount": ("用户金额", "申报金额", "金额"),
        "implementation": ("实施记录", "实施状态"), "application": ("申请记录", "申请状态"), "approval_record": ("用户批准记录", "批准记录"),
    },
    "finance-book": {
        "business": ("业务事项", "事项", "业务", "事由", "摘要", "Business"), "document_id": ("报销单号", "单据编号", "凭证编号"),
        "period": ("核算期间", "报告期", "核算期", "期间", "Period"), "entity": ("账套主体", "企业名称", "主体"),
        "contract": ("核算对象", "合同", "合同编号"), "counterparty": ("往来单位", "供应商", "供方", "销方"),
        "invoice": ("发票号码", "发票号", "票号", "Invoice"), "invoice_type": ("发票种类", "票种"),
        "amount": ("用户金额", "金额", "价税合计", "报销金额", "Amount"), "currency": ("币种", "Currency"),
        "subject": ("成本项目", "科目", "账套科目"), "subject_source": ("科目来源", "账套来源"),
        "handler": ("经办", "经办人", "报销人", "申请人"), "evidence": ("附件", "合同发票资料"),
        "approval": ("审批", "审批原文", "审批记录"), "approval_source": ("审批证据", "审批来源"),
        "reimbursement": ("用户报销记录", "报销记录"), "payment": ("用户付款记录", "付款记录"),
        "payment_source": ("付款来源", "付款证据"), "claimed_special": ("用户主张专项费用", "安措费主张"),
        "reconciliation": ("对账资料", "对账缺口"), "archive": ("归档资料",),
    },
    "finance-tax": {
        "entity": ("主体", "纳税主体", "主体类型", "企业名称", "纳税人", "Entity"), "tax": ("税种", "税目", "Tax"),
        "tax_id": ("税号", "纳税人识别号"), "period": ("申报期间", "申报期", "所属期", "期间", "Period"),
        "deadline": ("申报截止日期", "申报截止", "截止日期", "申报节点", "Deadline"),
        "rate_input": ("用户税率", "税率", "Rate"), "amount": ("用户税额", "税额", "Amount"),
        "source_date": ("资料日期", "来源日期", "核验日期"), "owner": ("责任人", "办税人", "办税员"),
        "business": ("申报事项", "业务事项", "扣缴事项"), "place": ("发生地", "办理地点", "预缴地"),
        "institution_place": ("机构所在地", "申报地"), "node_kind": ("节点名称", "申报类型"),
        "method": ("计税方法",), "invoice": ("发票资料", "税票资料"),
        "filing_record": ("用户申报记录", "申报回执", "申报记录"), "payment_record": ("用户缴税记录", "缴税记录"),
    },
}
IDENTITY = {"cost": ("item", "code"), "variation": ("event", "id"),
            "finance-book": ("business", "document_id", "invoice"), "finance-tax": ("entity", "tax")}
META = {"project", "stage", "project_zone"}
BASES = {"basis", "basis_version", "basis_source", "CN_basis", "SG_basis", "EU_basis"}
MISSING = re.compile(r"未提供|未给|未定|未查|未核|未批|未签|待定|待填|待核|待补|未知|没定|没查|还没|没有|未说明|没人|拿不准|UNSPECIFIED|TBD|N/A", re.I)
UNSAFE = re.compile(r"比如|例如|假设|如果|自行计算|自己算|不采用|不是|是否|[？?]")
NUM = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
UNIT = r"(?:立方(?:米)?|平方米|方|m[³3²2]|吨|t|kg|米|m|件|个|台班|工日)"
DATE = r"(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}月\d{1,2}[号日]|上周[一二三四五六日]|今天|明天)"
PERIOD = r"(?:\d{4}年\d{1,2}月|\d{4}-\d{1,2}(?!-\d)|\d{4}年(?:第?[一二三四1-4]季度)|[一二三四1-4]季度)"
QUANTITY_KEYS = {"quantity", "model_quantity", "drawing_quantity", "site_quantity", "machine_days", "labor_days"}
AMOUNTS = {"amount", "claim_amount", "quoted_price", "rate_input"}
FACTS = QUANTITY_KEYS | AMOUNTS | {"code", "id", "document_id", "date", "draft_date", "instruction_date", "deadline", "invoice", "tax_id", "source_date", "issuer", "measurer", "handler", "owner", "reviewer"}
SOURCES = {"source", "basis_source", "price_source", "quantity_source", "subject_source", "approval_source", "payment_source", "filing_record", "payment_record"}


def _clean(raw):
    value = raw.strip(" \t\r\n，,；。")
    return value if re.search(r"&(?:#\d+|#x[\da-f]+|[a-z][\da-z]+);$", value, re.I) else value.rstrip(";")


def _typed(key, raw):
    value = _clean(raw)
    if key in {"zone", "project_zone"}:
        return {"CN": "CN", "SG": "SG", "EU": "EU", "DUAL": "DUAL", "中国": "CN", "新加坡": "SG", "CN/SG": "DUAL", "SG/CN": "DUAL"}.get(value.upper(), UNKNOWN)
    if not value or MISSING.fullmatch(value):
        return UNKNOWN
    if key in FACTS | SOURCES and (MISSING.search(value) or UNSAFE.search(value)):
        return UNKNOWN
    if key in AMOUNTS | QUANTITY_KEYS and re.search(r"估计|估算|行情|市场价|大概|编个|编一个|随便", value):
        return UNKNOWN
    if key in SOURCES and re.search(r"拟提供|计划提供|将提供|预计提供", value):
        return UNKNOWN
    return value


def _natural(clause, post):
    if UNSAFE.search(clause):
        return []
    pairs = []
    def emit(key, pattern, group=1):
        match = re.search(pattern, clause, re.I)
        if match:
            literal = match.group(group) or next((g for g in match.groups() if g is not None), UNKNOWN)
            pairs.append((key, literal))
    if re.fullmatch(r"CN|SG|EU|DUAL|中国|新加坡|CN/SG|SG/CN", clause, re.I):
        return [("zone", clause)]
    if post == "cost":
        emit("item", r"^(?:.*?[:：])?(?:一个是|另一个(?:是)?)(.+)$")
        emit("item", r"^(\d+#楼.+?(?:混凝土|砼|钢筋|砌体墙).*)$")
        emit("quantity_source", r"(?:按)?((?:结施|建施|图纸|图号)-[A-Za-z\d_-]+)(?=图示|上|算|量|\s|$)")
        emit("quantity", r"(?:算出来是|量出来|算的是|数量为|数量是)("+NUM+r"\s*"+UNIT+r")")
        emit("measurement", r"(图示净量)")
        emit("code", r"清单编码\s*([A-Za-z\d._/-]+)")
        emit("contract_form", r"(单价合同|固定总价|总价合同|成本加酬金)")
        emit("author", r"编制人(?:写|是|为)?\s*(.+)$")
        emit("inquiry", r"(?:准备|计划)?找(.+?)询价")
        if re.search(r"编码|供应商|供.*没定", clause) and MISSING.search(clause):
            if "编码" in clause:
                pairs.append(("code", UNKNOWN))
        if "单价" in clause and re.search(r"空|没回|别编|未给|未定", clause):
            pairs.append(("quoted_price", UNKNOWN))
    elif post == "variation":
        emit("event", r"^(?:.*?[:：])?(?:一个是|另一个是)(.+)$")
        emit("id", r"(?:变更编号|编号先用)\s*([A-Za-z\d._/-]+)")
        if not re.search(r"(?:下发|发出|指令日期|通知日期)", clause):
            emit("date", "("+DATE+r")")
        emit("location", r"(\d+#楼(?:地下室[东西南北]侧|基坑[东西南北]侧|[一二三四五六七八九十\d]+层梁板)?)")
        emit("facts", r"(?:碰到|挖出来)(.+)$")
        emit("issuer", r"((?:监理|甲方代表)[一-鿿]{2,4})(?=现场|在现场)")
        emit("instruction", r"(?:现场口头|在现场)(让.+)$")
        emit("measurer", r"测量员([一-鿿]{2,4})(?=实测|量的)")
        emit("quantity", r"(?:实测破除量|量的是)("+NUM+r"\s*"+UNIT+r")")
        emit("machine", r"((?:PC|QTZ|TC|CAT)[A-Za-z\d-]+)")
        emit("machine_days", r"干了("+NUM+r"个?台班)")
        emit("labor_days", r"窝工("+NUM+r"个?工日)")
        emit("material", r"换的(.+)$")
        emit("evidence", r"(照片和实测草图都有|拍了照)")
        emit("quantity_kind", r"^(混凝土|钢筋)(?=增加|减少|取消)")
        emit("direction", r"^(?:混凝土|钢筋)(增加|减少|取消)")
        emit("quantity", r"^(?:混凝土|钢筋)(?:增加|减少|取消)("+NUM+r"\s*"+UNIT+r")")
        emit("spec", r"^(C\d+|HRB\d+)$")
        if "通知" in clause and MISSING.search(clause):
            pairs.append(("issuer", UNKNOWN))
        if "编号" in clause and MISSING.search(clause):
            pairs.append(("id", UNKNOWN))
        emit("instruction_date", "("+DATE+r")下发")
    elif post == "finance-book":
        emit("period", "("+PERIOD+")")
        emit("business", r"^(?:一笔是)(.+)$")
        emit("business", r"^另一笔(.+?)垫钱买的(.+)$", 2)
        emit("business", PERIOD+r"(.+?)报销")
        emit("counterparty", r"供应商(.+)$")
        emit("invoice", r"(?:发票号码|票号)([A-Za-z\d_-]+)")
        emit("invoice_type", r"(增值税专用发票|电子普票|收款收据)")
        emit("amount", r"(?:价税合计|一共|钱是)("+NUM+r"(?:元|块)?)")
        emit("subject", r"走(.+?)科目")
        emit("handler", r"经办是(.+)$")
        emit("evidence", r"附了(.+)$")
        emit("claimed_special", r"(?:他说走|声称走)(.+)$")
        emit("evidence", r"(收款收据)")
        emit("pending", r"(发票弄丢了.*)$")
        emit("pending", r"(收发存未盘点)")
        emit("approval", r"(项目经理.*(?:还没签|还没批|待签))")
        emit("approval", r"^((?:部门负责人|项目经理).*(?:已签|待签|未签|没签|没批))$")
        if "科目" in clause and MISSING.search(clause):
            pairs.append(("subject", UNKNOWN))
    else:
        emit("entity", r"主体是(.+)$")
        emit("entity", r"^(?:一个是)(.+?)这边的")
        emit("entity", r"^另一个是(.+)$")
        if not re.search(r"申报期.*(?:跟着|随|按).*税", clause):
            emit("tax", r"(城市维护建设税|企业所得税|个人所得税|增值税|印花税|GST)(?!走)")
        emit("tax", r"^(企业所得税)走")
        emit("place", r"在(.+?)跨县提供建筑服务")
        emit("institution_place", r"回机构所在地(.+?)申报")
        emit("period", "("+PERIOD+")")
        emit("deadline", "("+DATE+r")前(?:申报|报)")
        emit("owner", r"办税的是(.+)$")
        emit("business", r"^(工资薪金.+)$")
        emit("node_kind", r"(预缴|扣缴|申报|汇算)")
        emit("rate_input", r"\bGST\s*(?:税率|rate)?\s*[:：=]?\s*("+NUM+r"\s*[%％])")
        if "税号" in clause and MISSING.search(clause):
            pairs.append(("tax_id", UNKNOWN))
        if re.search(r"哪天报|申报截止|申报节点", clause) and MISSING.search(clause):
            pairs.append(("deadline", UNKNOWN))
        emit("pending", r"(申报期他们说.*)$")
    return pairs


def parse_records(text, post):
    fields = {**COMMON, **FIELDS[post]}
    aliases = {alias.casefold(): key for key, names in fields.items() for alias in names}
    label = re.compile(r"(?<![\w])("+"|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True))+r")\s*[:：=]\s*", re.I)
    scope = {"project_zone": UNKNOWN, "_sources": {}}
    contexts, records = [scope], []
    active, origin, file_id, header, detail = None, "", 0, None, None
    def cid():
        return next(i for i, context in enumerate(contexts) if context is scope)
    def new():
        nonlocal active, detail
        active = {"_context": cid(), "_origin": origin, "_file": file_id, "_sources": {}, "_quantities": []}
        records.append(active)
        detail = None
    def put(row, key, raw, position):
        value = _typed(key, raw)
        previous = row.get(key)
        if value == UNKNOWN:
            if previous not in {None, UNKNOWN}:
                row["pending"] = "；".join(filter(None, (row.get("pending"), key+"原值待复核："+previous)))
            row[key] = UNKNOWN
            row["_sources"].pop(key, None)
            if _clean(raw) not in {"", UNKNOWN}:
                row["pending"] = "；".join(filter(None, (row.get("pending"), key+"："+_clean(raw))))
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
            active, header, detail = None, None, None
            continue
        position = (origin+" · " if origin else "")+f"输入第{line_number}行（非原件页码）"
        pairs, matrix = [], False
        if stripped.startswith("|"):
            cells = [html.unescape(c).strip() for c in _table_cells(stripped)]
            if any(cells) and all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            if cells and cells[0].casefold() in aliases and sum(c.casefold() in aliases for c in cells) >= 2 and all(not c or c.casefold() in aliases for c in cells):
                header = cells
                active, detail = None, None
                continue
            if header is not None:
                matrix = True
                pairs = [(aliases[h.casefold()], cells[i] if i < len(cells) else "") for i, h in enumerate(header) if h.casefold() in aliases]
                active, detail = None, None
                scope = {**scope, "_sources": dict(scope["_sources"])}
                contexts.append(scope)
                pairs.sort(key=lambda p: {"project": 0, "stage": 1, "project_zone": 2}.get(p[0], 3))
            elif len(cells) >= 2 and cells[0].casefold() in aliases and cells[1].casefold() not in aliases and not any(cells[2:]):
                pairs = [(aliases[cells[0].casefold()], cells[1])]
            elif cells and cells[0].casefold() in aliases and sum(c.casefold() in aliases for c in cells) >= 2:
                header = cells
                continue
            else:
                continue
        else:
            if header is not None:
                active, header, detail = None, None, None
            cursor = 0
            for boundary in [*_breaks(line), None]:
                end = boundary.start() if boundary else len(line)
                clause = line[cursor:end].strip()
                cursor = boundary.end() if boundary else len(line)
                if not clause:
                    continue
                matches = list(label.finditer(clause))
                if matches:
                    found = [(aliases[m.group(1).casefold()], clause[m.end():matches[i+1].start() if i+1 < len(matches) else len(clause)]) for i, m in enumerate(matches)]
                    zone = re.match(r"^(CN/SG|SG/CN|CN|SG|EU|DUAL)\s+", clause, re.I)
                    if zone:
                        found.insert(0, ("zone", zone.group(1)))
                else:
                    found = _natural(clause, post)
                for key, raw in found:
                    pairs.append((key, raw))
                    if post == "finance-book" and key == "approval":
                        person = re.search(r"经办(?:是)?([一-鿿]{2,4})(?=，|,|$)", raw)
                        if person:
                            pairs.append(("handler", person.group(1)))
                    if post == "variation" and key == "instruction":
                        issued = re.search("("+DATE+r")下发", raw)
                        if issued:
                            pairs.append(("instruction_date", issued.group(1)))
        if matrix:
            new()
        for key, raw in pairs:
            scoped = key in META or key in BASES and active is None and not any(k in IDENTITY[post] for k, _ in pairs)
            if scoped:
                if matrix and active is not None and not any(not k.startswith("_") for k in active):
                    records.remove(active)
                    active = None
                populated = active is not None and any(k in IDENTITY[post] for k in active)
                if scope.get(key) not in {None, UNKNOWN, _typed(key, raw)} or populated and key in META and scope.get(key) in {None, UNKNOWN}:
                    scope = {"project_zone": UNKNOWN, "_sources": {}} if key == "project" else {**scope, "_sources": dict(scope["_sources"])}
                    scope.pop(key, None)
                    scope["_sources"].pop(key, None)
                    contexts.append(scope)
                    active, detail = None, None
                put(scope, key, raw, position)
                continue
            if not matrix and key in IDENTITY[post] and _typed(key, raw) != UNKNOWN and active is not None and active.get(key) not in {None, UNKNOWN}:
                active, detail = None, None
            if not matrix and key == "zone" and _typed(key, raw) != UNKNOWN and active is not None and active.get("zone") not in {None, UNKNOWN} and any(k in active for k in IDENTITY[post]):
                active, detail = None, None
            if active is None:
                new()
            if post == "variation" and key in {"quantity", "quantity_kind", "direction", "spec", "unit", "quantity_source", "material"}:
                if key in {"quantity", "quantity_kind"} and _typed(key, raw) != UNKNOWN and detail is not None and detail.get(key) not in {None, UNKNOWN}:
                    detail = None
                if detail is None:
                    detail = {"_sources": {}}
                    active["_quantities"].append(detail)
                put(detail, key, raw, position)
                target = detail
            else:
                put(active, key, raw, position)
                target = active
            if key == "quantity":
                unit = re.fullmatch("("+NUM+r")\s*("+UNIT+")", val(target, key), re.I)
                if unit and target.get("unit") in {None, UNKNOWN}:
                    put(target, "unit", unit.group(2), position)
                    target["_unit_from_quantity"] = True
                elif val(target, key) == UNKNOWN and target.get("_unit_from_quantity"):
                    target["unit"] = UNKNOWN
                    target["_sources"].pop("unit", None)
            elif key == "unit":
                target["_unit_from_quantity"] = False
    result = []
    for row in records or [{"_context": len(contexts)-1, "_origin": origin, "_file": file_id, "_sources": {}, "_quantities": []}]:
        context = contexts[row["_context"]]
        inherited = {key: value for key, value in context.items() if not key.startswith("_") and key != "project_zone"}
        if "zone" in row and row["zone"] != context.get("project_zone", UNKNOWN):
            for key in ("basis", "basis_version", "basis_source"):
                inherited.pop(key, None)
        sources = {k: v for k, v in context["_sources"].items() if k in inherited and k not in row}
        sources.update(row["_sources"])
        result.append({**inherited, **row, "_sources": sources, "_project_zone": context.get("project_zone", UNKNOWN)})
    return result


def cell(value):
    return html.escape(str(value), quote=False).replace("|", "&#124;").replace("\r", " ").replace("\n", "；")


def table(headers, rows):
    return "\n".join(["| "+" | ".join(headers)+" |", "| "+" | ".join("---" for _ in headers)+" |",
                      *("| "+" | ".join(cell(value) for value in row)+" |" for row in rows)])


def val(row, key):
    return row.get(key) or UNKNOWN


def zone(row):
    return row.get("zone", row.get("_project_zone", UNKNOWN))


def scoped_table(title, columns, records, tail=()):
    headers = ("项目", "阶段", "辖区")+tuple(label for label, _ in columns)+tuple(label for label, _ in tail)
    rows = [(val(row, "project"), val(row, "stage"), zone(row))+tuple(val(row, key) for _, key in columns)
            +tuple(value for _, value in tail) for row in records]
    return "## "+title+"\n\n"+table(headers, rows)+"\n"


def support(records):
    basis = []
    provenance = []
    for index, row in enumerate(records, 1):
        basis.append((str(index), zone(row), val(row, "basis"), val(row, "basis_version"), UNKNOWN, val(row, "basis_source"), "unverified"))
        basis.extend((str(index), z, row[z+"_basis"], UNKNOWN, UNKNOWN, UNKNOWN, "unverified") for z in ("CN", "SG", "EU") if row.get(z+"_basis") not in {None, UNKNOWN})
        for key, value in row.items():
            if not key.startswith("_") and value != UNKNOWN:
                provenance.append((str(index), key, value, row["_sources"].get(key, UNKNOWN)))
        for detail in row["_quantities"]:
            provenance.extend((str(index), key, value, detail["_sources"].get(key, UNKNOWN)) for key, value in detail.items() if not key.startswith("_") and value != UNKNOWN)
    return "\n\n".join(("### 对象依据（未核官方原文）", table(("材料序号", "辖区", "用户依据", "版本", "条款", "依据来源", "状态"), basis),
        "### 缺口与复核空栏", table(("材料序号", "待补资料", "核验结果", "复核人签字", "签认日期"),
                                  [(str(i), val(r, "pending"), UNKNOWN, "（空栏）", "（空栏）") for i, r in enumerate(records, 1)]),
        "### 字段来源定位", table(("材料序号", "字段", "用户摘录", "输入位置（非原件页码）"), provenance or [(UNKNOWN, UNKNOWN, UNKNOWN, UNKNOWN)])))


def _cost(rows):
    split = [("项目编码", "code"), ("分项名称特征", "item"), ("部位", "location"), ("单位", "unit"), ("用户数量", "quantity"), ("数量来源", "quantity_source")]
    lines = ["# 工程量拆分表与组价口径（AI 草稿 · 内部讨论）", "", DISCLAIMER, "", "- submit_blocked=true", "",
        "## 1 范围声明", "", "未连接算量或计价引擎。用户数量是原记录，不是本轮算量；用户报价只作询价摘录，不作综合单价或结算价。", "",
        scoped_table("2 清单来源与文件控制", (("清单来源", "source"), ("合同形式", "contract_form"), ("编制人（用户资料）", "author"), ("编制日期", "draft_date"), ("复核人（用户资料）", "reviewer")), rows),
        scoped_table("3 工程量拆分", split, rows, (("综合单价", "TBD"), ("合价", UNKNOWN))),
        scoped_table("4 计量口径", (("项目编码", "code"), ("名称特征", "item"), ("计量规则", "measurement"), ("数量依据", "quantity_source")), rows),
        scoped_table("5 用户数量对照（未计算）", (("名称特征", "item"), ("用户数量", "quantity"), ("用户模型量", "model_quantity"), ("用户图示量", "drawing_quantity"), ("用户现场实测量", "site_quantity")), rows),
        scoped_table("6 组价与询价资料", (("名称特征", "item"), ("询价对象与状态", "inquiry"), ("用户报价摘录", "quoted_price"), ("价格来源", "price_source"), ("币种", "currency"), ("用户人材机资料", "cost_elements")), rows),
        "## 7 单价与取费缺项", "", "单价 TBD；合价 UNSPECIFIED。清单、报价、定额、合同与数量来源由造价人员核对，不编综合单价、不换算单位、不计算总价。", "",
        scoped_table("取费及税费用户资料", (("名称特征", "item"), ("取费资料", "fees"), ("税费资料", "tax_input")), rows),
        "## 8 复核", "", "[A001] 未给编码、图纸、规则、数量单位、报价与审批逐项待填；不是结算依据。", "", support(rows)]
    return "\n".join(lines)


def _variation(rows):
    quantities = []
    for i, row in enumerate(rows, 1):
        for detail in row["_quantities"] or [{}]:
            quantities.append((str(i), val(row, "id"), val(row, "location"), val(detail, "quantity_kind"), val(detail, "material"),
                val(detail, "spec"), val(detail, "direction"), val(detail, "quantity"), val(detail, "unit"), val(detail, "quantity_source"), UNKNOWN))
    lines = ["# 工程签证 / 设计变更费用口径草稿（AI 草稿 · 内部讨论）", "", DISCLAIMER, "", "- submit_blocked=true", "",
        "## 1 事项声明与文件类型判定", "", "文种仅按用户材料登记；没有明确文种时待填，不把口头指令、联系单或实测量当批准签证。", "",
        scoped_table("事项登记", (("事项", "event"), ("编号", "id"), ("用户文件类型", "kind")), rows),
        "## 2 事实栏", "", scoped_table("事实经过（用户摘录未核）", (("事项", "event"), ("发生时间", "date"), ("部位", "location"), ("事实原文", "facts"), ("用户实施记录", "implementation"), ("责任单位", "owner")), rows),
        "## 3 依据栏", "", scoped_table("指令与合同依据", (("编号", "id"), ("指令", "instruction"), ("提出人或指令人", "issuer"), ("指令来源", "instruction_source"), ("下发日期", "instruction_date"), ("合同依据", "contract")), rows),
        "## 4 工程量", "", table(("事项序号", "编号", "部位", "数量类型", "材料", "规格", "用户增减", "用户工程量", "单位", "计量来源", "核验结果"), quantities), "",
        scoped_table("测量人与机具原始记录", (("编号", "id"), ("测量员", "measurer"), ("机械", "machine"), ("用户台班", "machine_days"), ("用户窝工工日", "labor_days"), ("计量口径", "measurement")), rows),
        "## 5 影响待核", "", scoped_table("用户金额与状态（未确认）", (("编号", "id"), ("用户申报金额", "claim_amount"), ("用户申请记录", "application"), ("用户批准记录摘录", "approval_record")), rows, (("签证金额", "TBD"), ("批准核验", UNKNOWN))),
        "金额 TBD，工期及责任影响 UNSPECIFIED。未推算金额、认定索赔或替业主确认；用户记录仍待核原件与授权。", "",
        "## 6 证据目录", "", scoped_table("用户附件与来源", (("编号", "id"), ("用户附件声明", "evidence"), ("资料来源", "source"), ("数量来源", "quantity_source")), rows),
        "## 7 签认栏（审批空栏）", "", table(("事项序号", "事实复核签字", "价款复核签字", "授权审批人签字", "签认日期"),
              [(str(i), "（空栏）", "（空栏）", "（空栏）", "（空栏）") for i, _ in enumerate(rows, 1)]), "",
        "### 自检与相邻接口", "", "事实、申请及批准资料分开；照片声明不等于读取了照片。未代签或补造批准；索赔损失交 claim，当期计量交 interim。", "", support(rows)]
    return "\n".join(lines)


def _book(rows):
    lines = ["# 项目部核算检查表（AI 草稿 · 内部讨论）", "", DISCLAIMER, "", "- submit_blocked=true", "",
        "## 1 范围声明", "", "仅登记凭据原文；不编会计分录、不改账套、不编盈亏。用户金额不等于获批报销或入账金额。", "",
        scoped_table("2 核算检查与业务单据", (("业务事项", "business"), ("期间", "period"), ("单据编号", "document_id"), ("经办", "handler"), ("用户金额", "amount"), ("币种", "currency")), rows),
        scoped_table("3 合同发票与附件", (("业务事项", "business"), ("核算对象或合同", "contract"), ("往来单位", "counterparty"), ("发票号码", "invoice"), ("发票种类", "invoice_type"), ("附件", "evidence")), rows),
        "## 4 科目对照", "", scoped_table("用户科目与账套口径（未记账）", (("业务事项", "business"), ("账套主体", "entity"), ("用户成本项目", "subject"), ("科目来源", "subject_source"), ("用户主张专项费用", "claimed_special")), rows, (("核定科目", UNKNOWN), ("入账金额", UNKNOWN))),
        "未给账套不编科目代码、借贷或分录。人工费、材料费、机械使用费、其他直接费、间接费用仅是对照名称，不自动归集。", "",
        scoped_table("5 用户审批与付款记录", (("业务事项", "business"), ("审批原文", "approval"), ("审批证据", "approval_source"), ("用户报销记录", "reimbursement"), ("用户付款记录", "payment"), ("付款证据", "payment_source")), rows, (("核验结果", UNKNOWN),)),
        "### 报销勾选（未审核）", "", table(("检查项", "核验结果", "复核签字"),
              [(label, UNKNOWN, "（空栏）") for label in ("发票查验", "票面与业务", "审批链", "附件", "重复报与项目归属", "专款范围")]), "",
        "## 6 对账缺口与差异待核", "", scoped_table("对账材料", (("业务事项", "business"), ("用户对账资料与缺口", "reconciliation"), ("待补资料", "pending")), rows),
        "无仓库盘点或两侧台账不编盈亏；用户声称安措费列支不等于认定专项范围。", "",
        "## 7 归档", "", scoped_table("用户归档材料", (("业务事项", "business"), ("资料来源", "source"), ("归档资料", "archive")), rows),
        "[A001] 金额、合同、票据原件、账套与审批记录按实际资料复核，不作入账或付款结论。", "", support(rows)]
    zones = {zone(row) for row in rows}
    if zones & {"SG", "DUAL"}:
        lines += ["", "SG 查找入口：IRAS / ACRA；规则原文、版本与适用性未核验。"]
    if zones & {"CN", "DUAL"}:
        lines += ["", "CN 查找入口：企业会计及税务适用文件；原文、版本与适用性未核验。"]
    return "\n".join(lines)


def tax_records(text, previous=""):
    rows = parse_records(text or "", "finance-tax")
    result = []
    for row in rows:
        z = zone(row)
        if z == UNKNOWN and "zone" not in row and row.get("_project_zone") == UNKNOWN and previous in {"CN", "SG", "EU", "DUAL"}:
            z = previous
        raw = val(row, "rate_input")
        values = re.findall(r"(?<![\d.])\d+(?:\.\d+)?\s*[%％](?!\d)", raw)
        rate = values[0] if len(values) == 1 and z in {"CN", "SG", "EU"} and not (MISSING.search(raw) or UNSAFE.search(raw)) else UNKNOWN
        result.append({**row, "zone": z, "rate": rate, "rate_input": raw,
            "source": val(row, "source"), "review": "用户提供，适用性及现行版本未核验" if rate != UNKNOWN else "待补明确辖区、税种及税率依据"})
    return result


def draft_tax(text, disclaimer=DISCLAIMER):
    rows = tax_records(text)
    zones = {row["zone"] for row in rows}
    z = next(iter(zones)) if len(zones) == 1 else "DUAL"
    lines = ["# 税务日历/检查表（AI 草稿 · 内部讨论）", "", disclaimer, "", DISCLAIMER, "", "submit_blocked=true", "",
        f"辖区：{z}。用户税率与节点仅为待核输入；本轮未联网核验，不从历史 KB 或模型记忆补充现行税率。", "",
        "## 1 辖区、主体与申报日历", "", table(("辖区", "主体", "税种", "申报期", "申报节点", "用户税率（待核）", "用户税额（待核）"),
            [tuple(val(row, key) for key in ("zone", "entity", "tax", "period", "deadline", "rate", "amount")) for row in rows]), "",
        "## 2 依据与资料核对", "", table(("辖区", "税种", "用户税率原文", "资料来源", "资料日期", "核验状态", "责任人"),
            [tuple(val(row, key) for key in ("zone", "tax", "rate_input", "source", "source_date", "review", "owner")) for row in rows]), "",
        "## 3 发票与申报准备", "", table(("辖区", "主体", "税号", "税种", "申报事项", "发生地", "机构所在地", "用户节点名称", "用户计税方法", "税票资料"),
            [tuple(val(row, key) for key in ("zone", "entity", "tax_id", "tax", "business", "place", "institution_place", "node_kind", "method", "invoice")) for row in rows]), "",
        "### 用户申报与缴税材料（未核验）", "", table(("辖区", "主体", "税种", "用户申报回执", "用户缴税记录", "核验结果", "复核签字"),
            [tuple(val(row, key) for key in ("zone", "entity", "tax", "filing_record", "payment_record"))+(UNKNOWN, "（空栏）") for row in rows]), "",
        "## 4 缺项与复核", "", "[A001] 未知地区不默认 SG，DUAL 的依据各自分栏，不共用税率。缺项待填；缺资料不推算截止日或税额；未计算税负、申报或缴税，不作税务意见。", "", support(rows)]
    return "\n".join(lines)


def build_draft(expert_id, tool_name, text):
    if TOOLS.get(expert_id) != tool_name:
        return None
    if expert_id == "finance-tax":
        return draft_tax(text or "")
    rows = parse_records(text or "", expert_id)
    return {"cost": _cost, "variation": _variation, "finance-book": _book}[expert_id](rows)
