"""Literal inspection-lot, defect, report and follow-up records.

No inspection, acceptance, sample-size calculation or concealment permission
is issued. Correction and review are separate user records, never approvals.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

from packing_assistant.post_drafts.planning import _inputs, _table
from packing_assistant.post_drafts.procurement import _source_name

MISSING = "[A001] / UNSPECIFIED"
TOOL = "quality__lot"
DISCLAIMER = ("AI 草稿 · 内部讨论 · 不得作为法定专项方案 / 交底签认件。本稿仅整理用户检查资料，"
              "未实施检查、检测、验收或复查，不作质量结论、不代签检验批，不签发隐蔽或下道工序许可。"
              "正式依据、原始记录与处置须由具备相应资格的人员复核签认。")
_DATE = r"(?:\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?|\d{1,2}月\d{1,2}[日号]|本周|下周|明天|昨天|昨日|今天)"
_LOCATION = re.compile(r"\d+[#＃]楼(?:地下室|[一二三四五六七八九十\d]+层)?(?:底板防水|剪力墙钢筋|顶板|砌体|底板|外墙|防水)?|地下室(?:B\d+层)?(?:底板防水|底板|外墙|防水)?")
_NUMBER = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_MODEL = re.compile(r"(?<![A-Za-z0-9_.-])(?:HRB\d+(?:E)?|HPB\d+|C\d+(?:P\d+)?|SBS)(?![A-Za-z0-9_.-])", re.I)
_CODE = r"[A-Za-z0-9][A-Za-z0-9./_-]*"
_UNKNOWN = re.compile(r"还没|未提供|未给|未定|未报|未知|待填|待核|待补|没给|没定|没报|没有|多少|UNSPECIFIED|TBD|N/A|unknown|not provided", re.I)
_UNSAFE = re.compile(r"例如|比如|假设|如果|示例|样例|自行计算|自己算|不采用|不按|别按|不要|不是|是否|[？?]|\b(?:for example|assuming|hypothetical|whether)\b", re.I)
_FIELDS = {
    "project": ("项目名称", "工程名称", "项目", "Project"), "unit": ("单位工程", "Unit"), "jurisdiction": ("辖区", "Jurisdiction"),
    "lot": ("检验批名称", "检验批", "Lot"), "lot_id": ("检验批编号", "批号", "Lot ID"),
    "location": ("检验批部位", "检查部位", "楼栋部位", "部位", "位置", "Location"), "subdivision": ("分项名称", "分项", "工序", "Subdivision"),
    "form_id": ("检查表编号", "Form ID"), "lot_quantity": ("检验批数量", "批量", "Lot quantity"),
    "item": ("检查内容", "检查项", "Item"), "main_item": ("主控检查项", "主控项目", "Main item"),
    "general_item": ("一般检查项", "一般项目", "General item"), "hidden_item": ("隐蔽检查项", "隐蔽工序", "Hidden item"),
    "kind": ("检查类别", "类别", "Kind"), "issue_id": ("问题编号", "Issue ID"),
    "requirement": ("设计要求", "用户标准要求", "标准要求", "Requirement"),
    "observation": ("自检记录", "用户记录", "观察记录", "问题", "记录", "Observation"), "measurement": ("实测值", "实测", "宽度", "Measurement"),
    "count": ("问题数量", "缺陷处数", "处数", "Defect count"), "sampling": ("用户抽样数量", "抽样数量", "Sampling quantity"),
    "checker": ("自检人", "检查人", "Checker"), "checked_date": ("自检日期", "检查日期", "Check date"),
    "evidence": ("证据", "Evidence"), "source": ("来源文件", "资料来源", "来源", "Source"),
    "check_evidence": ("影像记录", "检查证据", "Check evidence"), "check_source": ("检查来源", "检查记录来源", "Check source"),
    "material_source": ("材料来源", "Material source"), "action_source": ("整改来源", "整改记录来源", "Correction source"), "review_source": ("复查来源", "Review source"),
    "drawing": ("图纸图号", "图号", "Drawing"), "basis": ("检查依据", "依据", "Basis"),
    "material": ("材料名称", "材料", "Material"), "model": ("规格型号", "材料规格", "规格", "Model"),
    "supplier": ("生产厂家", "厂家", "供方", "Supplier"), "batch": ("材料批次", "材料批号", "Material batch"),
    "report_kind": ("报告类型", "Report type"), "report_id": ("报告编号", "检测报告编号", "复试报告编号", "Report ID"),
    "report_state": ("报告状态", "Report state"), "report_date": ("报告日期", "Report date"), "report_source": ("报告来源", "Report source"),
    "arrival_report": ("进场报告",), "retest_report": ("复试报告",), "test_report": ("检测报告",), "block_report": ("试块报告",),
    "owner": ("整改责任人", "整改责任", "责任班组", "责任人", "Rectification owner"),
    "action_id": ("整改单编号", "整改编号", "Action ID"), "action": ("整改要求", "整改事项", "Correction"),
    "deadline": ("整改期限", "计划完成日", "Deadline"), "completed": ("用户整改完成记录", "整改完成记录", "整改完成", "Correction completed"),
    "completion_source": ("整改完成证据", "Completion evidence"),
    "review": ("复查计划", "复查", "Review"), "review_date": ("计划复查日", "复查日期", "Review date"),
    "reviewer": ("复查人", "Reviewer"), "review_actual": ("用户实际复查记录", "实际复查记录", "Actual review"),
    "review_state": ("用户复查状态", "复查状态", "Review state"), "review_evidence": ("复查证据", "Review evidence"),
    "notice": ("隐蔽通知", "旁站记录", "Concealment notice"),
}
ALIASES = {a.casefold(): k for k, aliases in _FIELDS.items() for a in aliases}
_LABEL = re.compile(r"^("+"|".join(re.escape(a) for a in sorted(ALIASES, key=len, reverse=True))+r")\s*[:：=]\s*(.*)$", re.I)
_REPORTS = {"arrival_report": "进场报告", "retest_report": "复试报告", "test_report": "检测报告", "block_report": "试块报告"}
_CHECK = {"item", "main_item", "general_item", "hidden_item", "kind", "issue_id", "requirement", "observation", "measurement", "count", "sampling", "checker", "checked_date", "notice", "check_evidence", "check_source"}
_MATERIAL = {"material", "model", "supplier", "batch", "material_source"}
_REPORT = {"report_kind", "report_id", "report_state", "report_date", "report_source"}
_ACTION = {"owner", "action_id", "action", "deadline", "completed", "completion_source", "action_source"}
_REVIEW = {"review", "review_date", "reviewer", "review_actual", "review_state", "review_evidence", "review_source"}


@dataclass
class Data:
    contexts: list[dict[str, str]] = field(default_factory=list)
    lots: list[dict[str, str]] = field(default_factory=list)
    checks: list[dict[str, str]] = field(default_factory=list)
    materials: list[dict[str, str]] = field(default_factory=list)
    reports: list[dict[str, str]] = field(default_factory=list)
    actions: list[dict[str, str]] = field(default_factory=list)
    reviews: list[dict[str, str]] = field(default_factory=list)
    evidence: list[dict[str, str]] = field(default_factory=list)


def _value(value):
    value = value.strip()
    if not re.search(r"&(?:#\d+|#x[\da-f]+|[a-z][\da-z]+);$", value, re.I):
        value = value.rstrip("，,；;。")
    return MISSING if not value or _UNKNOWN.fullmatch(value) else value


def _typed(key, value):
    value = _value(value)
    if key == "jurisdiction" and value == MISSING:
        return "UNSPECIFIED"
    if key in {"owner", "checker", "reviewer", "model", "lot", "location", "form_id", "report_id", "lot_id", "batch", "supplier", "drawing", "measurement", "count", "sampling", "lot_quantity", "checked_date", "report_date", "deadline", "review_date"}:
        if _UNSAFE.search(value) or _UNKNOWN.search(value):
            return MISSING
    if key in {"measurement", "count", "sampling", "lot_quantity"} and not re.fullmatch(_NUMBER+r"\s*(?:毫米|mm|cm|m|米|处|件|个|组|点|块)?", value, re.I):
        return MISSING
    if key in {"checked_date", "report_date", "deadline", "review_date"} and not re.fullmatch(_DATE+r"(?:前)?", value):
        return MISSING
    return value


def _scan(text):
    text = re.sub(r"^(?:本次实际情况|实际情况|真实记录)\s*[:：]\s*", "", text)
    match = _LABEL.match(text)
    if match:
        key, value = ALIASES[match.group(1).casefold()], match.group(2)
        if key == "review":
            return [("review" if "计划" in match.group(1) else _review_field(value), value), *_review_details(value)]
        if key == "lot" and _typed(key, value) != MISSING:
            location = _LOCATION.search(value)
            if location:
                return [(key, value), ("location", location.group())]
        return [(key, value)]
    command = re.fullmatch(r"(?:写|出|生成|起草|编制)一份质量检查表\s+(.+)", text)
    if command:
        text = command.group(1)
        if not _LOCATION.search(text):
            return [("lot", text)]
    if _UNSAFE.search(text):
        return []
    facts = []
    location = _LOCATION.search(text)
    if location:
        facts += [("location", location.group()), ("lot", location.group())]
    if re.search(r"箍筋间距偏大|垫块漏放|收缩裂缝|拉结筋.*漏|起鼓|空鼓|渗漏|裂缝|缺陷", text):
        observation = text[location.end():] if location else text
        observation = re.sub(r"^(?:有几条|这个检验批|昨天自检|还有)", "", observation).strip(" ，,：:")
        facts.append(("observation", observation))
        item = re.search(r"暗柱箍筋|保护层垫块|拉结筋|卷材|裂缝", observation)
        if item:
            facts.append(("item", item.group()))
    measurement = re.search(r"(?<![A-Za-z0-9_.#/-])("+_NUMBER+r"\s*(?:毫米|mm|cm))(?![A-Za-z0-9_.-])", text, re.I)
    if measurement:
        facts.append(("measurement", measurement.group(1)))
    count = re.search(r"漏(?:了)?\s*("+_NUMBER+r"\s*处)", text)
    if count:
        facts.append(("count", count.group(1)))
    self_check = re.search(r"(昨天|今天|昨日)自检", text)
    if self_check:
        facts.append(("checked_date", self_check.group(1)))
    material = re.search(r"(钢筋|砌块|卷材|混凝土)(?:是|用的|用的是)\s*(.+)", text)
    if material:
        value = material.group(2).rstrip("。")
        facts.append(("material", value if material.group(1) != "钢筋" else "钢筋"))
        supplier = re.search(r"(.+?(?:钢厂|有限公司))的", value)
        if supplier:
            facts.append(("supplier", supplier.group(1)))
        model = _MODEL.search(value)
        if model:
            facts.append(("model", model.group()))
    report = re.search(r"(进场报告|复试报告|检测报告|试块报告)\s*("+_CODE+r")", text)
    if report:
        facts.append((ALIASES[report.group(1)], report.group(2)))
        if text[report.end():].strip():
            facts.append(("report_state", text[report.end():].strip()))
    elif re.search(r"(?:进场|复试|检测|试块)报告.*(?:还没|未出|没出|未给)", text):
        facts.append(("report_state", text))
    owner = re.search(r"(?:整改让|让)\s*(.+?)(?="+_DATE+r"|去补|去改|改完|$)", text)
    if owner:
        facts.append(("owner", owner.group(1).strip()))
    if re.search(r"整改|改完", text):
        date = re.search(_DATE, text)
        if date:
            facts.append(("deadline", date.group()))
    if "复查" in text:
        facts += [(_review_field(text), text), *_review_details(text)]
    return facts


def _review_field(text):
    if re.search(r"计划|预计|拟|将|明天|下周|再复查", text):
        return "review"
    if re.search(r"(?<![未没不])已复查", text) and not re.search(r"据说|听说|并未|尚未|还未|没有|未经", text):
        return "review_actual"
    return "review_state" if re.search(r"未|没|不|据说|听说", text) else "review"


def _review_details(value):
    facts = []
    date = re.search(_DATE, value)
    if date and not _UNSAFE.search(value):
        facts.append(("review_date", date.group()))
    person = re.search(r"(?:质检员|复查人)\s*([\u4e00-\u9fff]{2,4}?)(?=复查|核查|\s|$)", value)
    if person:
        facts.append(("reviewer", person.group(1)))
    return facts


def parse(raw):
    data = Data()
    scope = {"jurisdiction": "UNSPECIFIED"}
    data.contexts.append(scope)
    origin = ""
    lot = check = material = report = action = review = None
    mode = "lot"

    def ref(rows, target):
        return next((i for i, row in enumerate(rows) if row is target), None)

    def new(collection, values=None):
        row = {"_origin": origin, "_scope": len(data.contexts)-1,
               "_lot": ref(data.lots, lot) if collection != "lots" else None,
               "_material": ref(data.materials, material) if collection == "reports" else None,
               "project": scope.get("project", MISSING), "unit": scope.get("unit", MISSING),
               "jurisdiction": scope.get("jurisdiction", "UNSPECIFIED"), **(values or {})}
        getattr(data, collection).append(row)
        return row

    def put(target, key, value):
        value = _typed(key, value)
        if target.get(key) in {None, MISSING, "UNSPECIFIED"}:
            target[key] = value
        elif value != MISSING and value not in target[key].split("；"):
            target[key] += "；"+value
        if value not in {MISSING, "UNSPECIFIED"}:
            data.evidence.append({"_lot": ref(data.lots, lot), "_material": ref(data.materials, material),
                                  "_origin": origin, "_scope": len(data.contexts)-1, "project": scope.get("project", MISSING),
                                  "unit": scope.get("unit", MISSING), "jurisdiction": scope.get("jurisdiction", "UNSPECIFIED"),
                                  "field": _FIELDS[key][0], "value": value, "location": location})

    for content, is_table, offset, source in _inputs(raw):
        location = (origin+" · " if origin else "")+f"本轮输入第{raw[:offset].count(chr(10))+1}行（非原件页码）"
        if content is None:
            lot = check = material = report = action = review = None
            mode = "lot"
            continue
        marker = _source_name(source)
        if marker is not None or source.startswith(("【本轮用户要求", "【此前用户资料")):
            origin = marker if marker is not None else source.strip("【】")
            scope = {"_origin": origin, "jurisdiction": "UNSPECIFIED"}
            data.contexts.append(scope)
            lot = check = material = report = action = review = None
            mode = "lot"
            continue
        pairs = ([(ALIASES[label.casefold()], value) for label, value in content if label.casefold() in ALIASES]
                 if is_table else _scan(source))
        if not pairs:
            continue
        normalized = []
        for key, value in pairs:
            if key in {"main_item", "general_item", "hidden_item"}:
                normalized += [("kind", {"main_item": "主控", "general_item": "一般", "hidden_item": "隐蔽"}[key]), ("item", value)]
            else:
                normalized.append((key, value))
        pairs = normalized
        stated = dict(pairs)
        families = {name for name, keys in (("check", _CHECK), ("material", _MATERIAL), ("report", _REPORT | set(_REPORTS)), ("action", _ACTION), ("review", _REVIEW)) if any(k in stated for k in keys)}
        if not is_table and any(k in stated and scope.get(k) not in {None, MISSING, "UNSPECIFIED", _typed(k, stated[k])}
                                for k in {"project", "unit", "jurisdiction"}):
            scope = {"_origin": origin, "jurisdiction": "UNSPECIFIED"}
            data.contexts.append(scope)
            lot = check = material = report = action = review = None
            mode = "lot"
        if is_table or "lot" in stated or "location" in stated and (lot is None or lot.get("location") not in {None, MISSING, _value(stated["location"])}):
            lot = new("lots", {k: _typed(k, stated[k]) for k in {"project", "unit", "jurisdiction"} if k in stated})
            check = material = report = action = review = None
            mode = "lot"
        elif "lot_id" in stated and _typed("lot_id", stated["lot_id"]) != MISSING:
            identifier = _typed("lot_id", stated["lot_id"])
            found = [row for row in data.lots if row["_scope"] == len(data.contexts)-1 and row.get("lot_id") == identifier]
            if lot is None or lot.get("lot_id") not in {None, MISSING, identifier} or len(found) > 1:
                lot = found[0] if len(found) == 1 else new("lots")
                check = material = report = action = review = None
                mode = "lot"
        if "material" in stated:
            material = new("materials")
            report = None
        elif "batch" in stated and material is not None and _typed("batch", stated["batch"]) != MISSING and material.get("batch") not in {None, MISSING, _typed("batch", stated["batch"])}:
            material = new("materials", {"material": material.get("material", MISSING)})
            report = None
        if "item" in stated or "issue_id" in stated and (check is None or check.get("issue_id") not in {None, MISSING, _value(stated["issue_id"])}):
            check = new("checks")
        elif "observation" in stated and check is not None and check.get("observation") not in {None, MISSING, _value(stated["observation"])}:
            check = new("checks")
        if "action_id" in stated and action is not None and action.get("action_id") not in {None, MISSING, _value(stated["action_id"])} or "owner" in stated and action is not None and _typed("owner", stated["owner"]) != MISSING and action.get("owner") not in {None, MISSING, _typed("owner", stated["owner"])}:
            action = None
        if "review" in stated or "review_actual" in stated and review is not None and review.get("review_actual"):
            review = None
        named_reports = [k for k, _ in pairs if k in _REPORTS]
        if "report_kind" in stated or "report_id" in stated and (report is None or report.get("report_id") not in {None, MISSING, _typed("report_id", stated["report_id"])}):
            report = None
        if is_table:
            identities = set(_REPORTS) | {"report_kind", "report_id"}
            pairs = [p for p in pairs if p[0] in identities]+[p for p in pairs if p[0] not in identities]
        for key, value in pairs:
            if key in {"project", "unit", "jurisdiction"}:
                put(lot if is_table else scope, key, value)
                if not is_table and _typed(key, value) not in {MISSING, "UNSPECIFIED"}:
                    # Late file-scope metadata fills only missing fields in
                    # this source epoch, never previous files or explicit rows.
                    for rows in (data.lots, data.checks, data.materials, data.reports, data.actions, data.reviews, data.evidence):
                        for row in rows:
                            if row["_scope"] == len(data.contexts)-1 and row.get(key) in {None, MISSING, "UNSPECIFIED"}:
                                row[key] = _typed(key, value)
                continue
            if key in _REPORTS:
                report = new("reports", {"report_kind": _REPORTS[key]})
                mode = "report"
                put(report, "report_id", value)
                continue
            if key in _REPORT:
                if report is None or is_table and len(named_reports) > 1 and key in {"report_state", "report_date", "report_source"} and report.get("report_kind"):
                    report = new("reports")
                mode = "report"
                put(report, key, value)
                continue
            if key in _MATERIAL:
                if material is None:
                    material = new("materials")
                mode = "material"
                put(material, "source" if key == "material_source" else key, value)
                continue
            if key in _CHECK:
                if check is None:
                    check = new("checks")
                mode = "check"
                put(check, "source" if key == "check_source" else "evidence" if key == "check_evidence" else key, value)
                continue
            if key in _ACTION:
                if action is None:
                    action = new("actions")
                mode = "action"
                put(action, "source" if key == "action_source" else key, value)
                continue
            if key in _REVIEW:
                if review is None:
                    review = new("reviews")
                mode = "review"
                put(review, "source" if key == "review_source" else key, value)
                continue
            if key in {"source", "evidence"} and is_table:
                if len(families) != 1:
                    put(lot, key, value)  # role is ambiguous, not the last column
                    continue
                family = next(iter(families))
                if family == "check" and check is None:
                    check = new("checks")
                elif family == "material" and material is None:
                    material = new("materials")
                elif family == "report" and (report is None or len(named_reports) > 1 and report.get("report_kind")):
                    report = new("reports")
                elif family == "action" and action is None:
                    action = new("actions")
                elif family == "review" and review is None:
                    review = new("reviews")
                put({"check": check, "material": material, "report": report, "action": action, "review": review}[family], key, value)
                continue
            if key in {"source", "evidence"} and mode != "lot":
                target = {"check": check, "material": material, "report": report, "action": action, "review": review}[mode]
                if is_table and mode == "report" and len(named_reports) > 1 and report.get("report_kind"):
                    report = new("reports")
                    target = report
                put(target, key, value)
                continue
            if lot is None:
                lot = new("lots")
            mode = "lot"
            put(lot, key, value)
    data.contexts = [r for r in data.contexts if len(r) > 1] or data.contexts[:1]
    return data


def _owned(data, rows):
    result = []
    for row in rows:
        lot_ref, material_ref = row.get("_lot"), row.get("_material")
        lot = data.lots[lot_ref] if isinstance(lot_ref, int) else {}
        material = data.materials[material_ref] if isinstance(material_ref, int) else {}
        result.append({"lot_name": lot.get("lot", MISSING), "lot_id": lot.get("lot_id", MISSING), "lot_location": lot.get("location", MISSING),
                       "owner_unit": lot.get("unit", row.get("unit", MISSING)), "owner_project": lot.get("project", row.get("project", MISSING)),
                       "owner_jurisdiction": lot.get("jurisdiction", row.get("jurisdiction", "UNSPECIFIED")),
                       "material_name": material.get("material", MISSING), "material_model": material.get("model", MISSING),
                       "material_batch": material.get("batch", MISSING), **row})
    return result


def build_draft(post, tool_name, text):
    if post != "quality" or tool_name != TOOL:
        return None
    data = parse(text)
    owner = (("检验批名称", "lot_name"), ("检验批编号", "lot_id"), ("检验批部位", "lot_location"), ("单位工程", "owner_unit"),
             ("项目名称", "owner_project"), ("辖区", "owner_jurisdiction"), ("输入资料名", "_origin"))
    check_cols = owner+(("检查项", "item"), ("用户检查类别", "kind"), ("问题编号", "issue_id"), ("用户设计 / 标准要求", "requirement"),
                       ("用户自检 / 观察记录（未核）", "observation"), ("用户实测值（未核）", "measurement"), ("用户问题数量", "count"),
                       ("用户抽样数量", "sampling"), ("用户检查人", "checker"), ("用户检查日期", "checked_date"), ("检查证据", "evidence"), ("记录来源", "source"), ("用户隐蔽通知 / 旁站记录", "notice"))
    checks = _owned(data, data.checks)
    return "\n".join([
        "# 质量检查表（AI 草稿 · 内部讨论）", "", DISCLAIMER, "", "- submit_blocked=true", "",
        _table(data.contexts, (("工程 / 项目名称", "project"), ("单位工程", "unit"), ("辖区", "jurisdiction"), ("输入资料名", "_origin"))), "",
        "## 1 封面与声明", "", "编制、复核与签认：（空）。检查、检测、验收、隐蔽与处置权限不由本稿产生。", "",
        "## 2 划分说明 / 检验批", "", _table(data.lots, (("检验批名称", "lot"), ("检验批部位", "location"), ("检验批编号", "lot_id"), ("分项 / 工序", "subdivision"),
            ("用户检验批数量", "lot_quantity"), ("检查表编号", "form_id"), ("单位工程", "unit"), ("项目名称", "project"), ("辖区", "jurisdiction"), ("输入资料名", "_origin"))), "",
        "批量和抽样各自登记，不从缺陷处数倒推批量、最小抽样或合格点率。", "",
        "## 3 进场与依据 / 材料检测资料", "", _table(data.lots, (("检验批名称", "lot"), ("检验批部位", "location"), ("图号（用户原文）", "drawing"), ("用户检查依据", "basis"), ("来源（归属待核）", "source"), ("未分派证据", "evidence"))), "",
        _table(_owned(data, data.materials), owner+(("材料名称", "material"), ("材料规格", "model"), ("生产厂家 / 供方", "supplier"), ("材料批次", "batch"), ("材料来源", "source")), extra=(("材料核验", "本轮未核验"),)), "",
        _table(_owned(data, data.reports), owner+(("对应材料", "material_name"), ("材料规格", "material_model"), ("材料批次", "material_batch"),
            ("报告类型", "report_kind"), ("用户报告编号", "report_id"), ("用户报告收集状态（未核）", "report_state"), ("用户报告日期", "report_date"),
            ("报告来源", "report_source"), ("记录来源", "source"), ("报告证据", "evidence")), extra=(("原件与结果核验", "本轮未核验"),)), "",
        "报告编号、收集状态和检验结果不互相替代；不编强度、抗渗等级、图号、报告日期或现行依据。", "",
        "## 4 主控项目检查栏", "", _table([r for r in checks if r.get("kind") == "主控"], check_cols, extra=(("本轮检查结果", "未检 / 待核"),)), "",
        "## 5 一般项目检查栏", "", _table([r for r in checks if r.get("kind") == "一般"], check_cols, extra=(("本轮检查结果", "未检 / 待核"),)), "",
        "检查类别只取用户明示，不将所有问题默认归为主控。", "",
        "## 6 隐蔽专项", "", _table([r for r in checks if r.get("kind") == "隐蔽" or r.get("notice") not in {None, MISSING}], check_cols, extra=(("本轮隐蔽签认", "未执行 /（空）"),)), "",
        "隐蔽通知、影像、检查和正式签认分别核对；本稿不允许覆盖未核工序。", "",
        "## 7 通病防治核对 / 问题记录", "", _table(checks, check_cols, extra=(("本轮检查结果", "未检 / 待核"),)), "",
        "只登记用户实际问题或观察原文，不推定允许偏差，不计算缺陷率或自定整改方法。", "",
        "## 8 不符合时的处理路径栏目 / 整改", "", _table(_owned(data, data.actions), owner+(("整改单编号", "action_id"), ("用户整改要求", "action"),
            ("整改责任 / 班组", "owner"), ("计划整改期限", "deadline"), ("用户整改完成记录（未核）", "completed"), ("整改完成证据", "completion_source"), ("整改记录来源", "source")),
            extra=(("本轮整改 / 签认", "未执行 /（空）"),)), "",
        "整改责任和期限是任务记录，不是完成事实；用户完成记录也不等于复查确认。处置方案与技术结论另行核对。", "",
        "## 9 资料闭合 / 复查与归档", "", _table(_owned(data, data.reviews), owner+(("用户复查计划（非实施事实）", "review"), ("用户复查日期原文", "review_date"),
            ("用户复查人", "reviewer"), ("用户实际复查记录（未核）", "review_actual"), ("用户复查陈述（未核）", "review_state"), ("复查证据", "review_evidence"), ("记录来源", "source")),
            extra=(("本轮复查状态", "待核"), ("本轮验收 / 隐蔽签认", "未执行 /（空）"))), "",
        "检测、检查、整改完成和复查资料各自对应检验批，不从一批的报告或完成记录替另一批闭合。", "",
        "## 10 签字栏与证据位置", "", "| 质检员 | 工长 | 技术负责人 | 监理 |\n| --- | --- | --- | --- |\n| （空） | （空） | （空） | （空） |", "",
        _table(_owned(data, data.evidence), owner+(("记录项", "field"), ("用户证据原文", "value"), ("输入位置（非原件页码）", "location"))), "",
        "签名栏不由输入姓名自动填入。依据与条款须核对正式原文；未提供时 unverified / UNSPECIFIED，不默认国家或法规版本。", "",
    ])
