"""Hazard input/authority comparison cards; no statutory classification engine."""
from __future__ import annotations

import html
import re

from packing_assistant.post_drafts.planning import _inputs
from packing_assistant.post_drafts.procurement import _literal_source, _source_name
from packing_assistant.word_export import _table_cells

UNKNOWN = "UNSPECIFIED"
TOOL = "method-hazard__judge_hazard"
DISCLAIMER = ("AI 草稿 · 内部讨论 · 不得作为法定专项方案 / 交底签认件。"
              "已填内容仅为用户资料转录，未核验；本轮未作法定危大认定、超规模认定、许可签发或开工放行。"
              "须由持证人员依据项目辖区、正式规则原文及现场资料复核并签认。")
FIELDS = {
    "作业名称": ("作业", "作业名称", "分部分项", "对象名称"),
    "作业编号": ("作业编号",),
    "项目名称": ("项目", "项目名称", "工程名称"), "设计阶段": ("设计阶段", "资料阶段"),
    "部位": ("部位", "位置", "区域", "作业部位"),
    "辖区": ("辖区",), "总体辖区": ("总体辖区", "项目辖区"),
    "适用依据": ("适用依据", "规则原文", "拟用规则", "规范名称", "依据"),
    "依据版本": ("依据版本", "规则版本"), "依据来源": ("依据来源", "规则来源"),
    "CN依据": ("CN依据",), "SG依据": ("SG依据",), "EU依据": ("EU依据",),
    "支模高度": ("支模高度", "支撑高度"), "架体高度": ("架体高度", "搭设高度", "脚手架高度", "架高"),
    "高度": ("高度",), "开挖深度": ("开挖深度", "基坑深度", "挖深", "深度"),
    "跨度": ("最大跨度", "跨度"), "施工总荷载": ("施工总荷载", "总荷载", "荷载"),
    "起挑楼层": ("起挑楼层", "起挑层"), "单件重量": ("单件重量", "单件重", "构件重", "起吊重量", "吊重"),
    "起重设备": ("起重设备", "吊装设备", "汽车吊", "起重机", "吊车"),
    "额定起重量": ("额定起重量", "吊车吨位"),
    "吊装高度": ("吊装高度", "起吊高度", "起升高度"), "吊装跨度": ("吊装跨度",),
    "尺寸来源": ("尺寸来源", "图号", "依据图", "图纸", "数值来源", "数据来源"),
    "资料来源": ("资料来源", "来源文件", "来源"),
    "支护形式": ("支护形式", "围护形式", "施工工艺"),
    "周边环境": ("周边环境", "周边", "环境", "毗邻条件"),
    "地勘报告": ("地勘报告", "勘察报告", "地质报告"),
    "计划作业日期": ("计划开挖", "计划日期", "计划作业日期", "作业日期"),
    "已有人工判定": ("已有判定", "已有人工判定", "人工判定", "初判"),
    "人工判定人": ("人工判定人", "判定人", "复核人"),
    "人工判定证据": ("人工判定证据", "判定证据", "书面判定"),
    "专项资料": ("专项资料", "专项方案", "方案资料"),
    "待补资料": ("待补资料", "缺项", "信息缺口", "待核项"),
}
ALIASES = {a.casefold(): k for k, names in FIELDS.items() for a in names}
LABELS = "|".join(map(re.escape, sorted(ALIASES, key=len, reverse=True)))
LABEL = re.compile(r"(?<![A-Za-z0-9_])("+LABELS+r")\s*[:：=]\s*", re.I)
BARE = re.compile(r"^("+LABELS+r")\s+(.+)$", re.I)
NUMBER = r"[+-]?\d+(?:\.\d+)?"
LENGTH = NUMBER+r"\s*(?:毫米|厘米|米|mm|cm|m)?"
CODE = r"[A-Za-z一-鿿]+(?:[-_][A-Za-z0-9]+)+"
EMPTY = re.compile(r"^(?:待填|待核|未提供|未知|未说明|UNSPECIFIED|TBD|N/A)$", re.I)
MISSING = re.compile(r"没量|没定|没给|未测|未量|未提供|还没|待补|未知|没有书面|未签|待签|计划提供|预计|口头")
UNSAFE = re.compile(r"例如|比如|假设|示例|如果|自行计算|自己算|不是|不采用|不要|是否|[？?]")
DIMENSIONS = {"支模高度", "架体高度", "高度", "开挖深度", "跨度", "施工总荷载", "单件重量", "额定起重量", "吊装高度", "吊装跨度"}
SOURCE_KEYS = {"尺寸来源", "地勘报告", "人工判定证据", "依据来源", "专项资料"}
CONTEXT = {"项目名称", "设计阶段", "总体辖区"}
BASIS = {"适用依据", "依据版本", "依据来源", "CN依据", "SG依据", "EU依据"}


def _clean(raw):
    value = raw.strip(" \t\r\n，,；。")
    if not re.search(r"&(?:#\d+|#x[0-9a-f]+|[a-z][a-z0-9]+);$", value, re.I):
        value = value.rstrip(";")
    return value or UNKNOWN


def _typed(key, raw):
    value = _clean(raw)
    if EMPTY.fullmatch(value):
        return UNKNOWN
    if key in {"辖区", "总体辖区"}:
        return {"CN": "CN", "SG": "SG", "EU": "EU", "DUAL": "DUAL", "中国": "CN", "新加坡": "SG"}.get(value.upper(), UNKNOWN)
    if key in DIMENSIONS | SOURCE_KEYS and (MISSING.search(value) or UNSAFE.search(value)):
        return UNKNOWN
    return value


def _natural(text):
    text = re.sub(r"^.*?[:：](?=(?:一个是|另一个是))", "", text)
    if UNSAFE.search(text):
        return []
    pairs = []

    def emit(key, pattern, group=1):
        match = re.search(pattern, text)
        if match:
            pairs.append((key, match.group(group)))

    if re.fullmatch(r"SG|CN|EU|DUAL|中国|新加坡", text, re.I):
        return [("辖区", text)]
    if re.match(r"^(?:一个是|另一个是)", text):
        emit("作业名称", r"^(?:一个是|另一个是)(.+?(?:高支模|脚手架|整体吊装))")
    else:
        emit("作业名称", r"^(?:我们)?(.+?(?:高支模|悬挑脚手架|整体吊装))(?=下周|$)")
    emit("部位", r"(\d+#楼(?:地下室顶板)?|B\d+区)")
    for key, labels, unit in (
        ("支模高度", "支模高度", r"(?:米|m)?"), ("架体高度", "架体高度", r"(?:米|m)?"),
        ("开挖深度", "(?:开挖深度|基坑深度)", r"(?:米|m)?"),
        ("跨度", "最大跨度", r"(?:米|m)?"),
        ("施工总荷载", "施工总荷载(?:算下来)?", r"(?:kN/m[²2]|kN/㎡)?"),
        ("单件重量", "单件重(?:量)?", r"(?:吨|t|kg)?"),
    ):
        emit(key, labels+r"\s*(?:是|为|约)?\s*("+NUMBER+r"\s*"+unit+r")")
    emit("起挑楼层", r"从(\d+层)开始挑")
    emit("起重设备", r"用("+NUMBER+r"\s*(?:吨|t)汽车吊)")
    emit("尺寸来源", r"(?:尺寸(?:都是从|看的)|按)([^，；]*?"+CODE+r"(?:图)?)")
    emit("适用依据", r"((?:住建部令第\s*)?37\s*号令)")
    if "口头" in text or re.search(r"初步看过|初判", text):
        reference = re.search(r"(脚手架|高支模|吊装)(?:那个|的)", text)
        if reference:
            pairs.insert(0, ("_reference", reference.group(1)))
        pairs.append(("已有人工判定", text))
        emit("人工判定人", r"(?:技术部|安全总监|总工)([一-鿿]{2,4})(?=初步|说|口头)")
    if MISSING.search(text):
        pairs.append(("待补资料", text))
    return pairs


def parse(text):
    records, contexts = [], []
    scope = {"总体辖区": UNKNOWN, "_sources": {}}
    contexts.append(scope)
    active, file_id, origin = None, 0, ""

    def context_id():
        return next(i for i, context in enumerate(contexts) if context is scope)

    def new():
        nonlocal active
        active = {"_context": context_id(), "_file": file_id, "_origin": origin, "_sources": {}}
        records.append(active)

    def put(row, key, raw, position):
        value = _typed(key, raw)
        if value == UNKNOWN and _clean(raw) != UNKNOWN and key in DIMENSIONS | SOURCE_KEYS:
            row["待补资料"] = "；".join(filter(None, (row.get("待补资料"), key+"："+_clean(raw))))
        if value == UNKNOWN:
            if row.get(key) not in {None, UNKNOWN}:
                row["待补资料"] = "；".join(filter(None, (row.get("待补资料"), key+"原用户值："+row[key]+"；当前未定")))
            row[key] = UNKNOWN
            row["_sources"].pop(key, None)
        else:
            previous = row.get(key)
            row[key] = value if previous in {None, UNKNOWN} else "；".join(dict.fromkeys((previous, value)))
            sources = row.setdefault("_sources", {})
            sources[key] = "；".join(dict.fromkeys(filter(None, (sources.get(key), position))))

    def scan(clause):
        clause = re.sub(r"^(?:本次实际情况|实际情况|真实记录)\s*[:：]\s*", "", clause)
        matches = list(LABEL.finditer(clause))
        if matches:
            return [(ALIASES[m.group(1).casefold()], clause[m.end():matches[i+1].start() if i+1 < len(matches) else len(clause)])
                    for i, m in enumerate(matches)]
        match = BARE.match(clause)
        if match:
            return [(ALIASES[match.group(1).casefold()], match.group(2))]
        return _natural(clause)

    header = None
    filtered = _literal_source(text)
    for line_number, line in enumerate(filtered.splitlines(), 1):
        stripped = line.strip()
        if _source_name(stripped) is not None or stripped.startswith(("【本轮用户要求", "【此前用户资料")):
            origin = _source_name(stripped) or stripped.strip("【】")
            file_id += 1
            scope = {"总体辖区": UNKNOWN, "_sources": {}}
            contexts.append(scope)
            active, header = None, None
            continue
        position = (origin+" · " if origin else "")+f"输入第{line_number}行（非原件页码）"
        matrix = False
        if stripped.startswith("|"):
            cells = [html.unescape(c).strip() for c in _table_cells(stripped)]
            if any(cells) and all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            if header is not None:
                pairs = [(ALIASES[h.casefold()], cells[i] if i < len(cells) else "") for i, h in enumerate(header) if h.casefold() in ALIASES]
                active, matrix = None, True
            elif len(cells) >= 2 and cells[0].casefold() in ALIASES and cells[1].casefold() not in ALIASES and not any(cells[2:]):
                pairs = [(ALIASES[cells[0].casefold()], cells[1])]
            elif any(c.casefold() in ALIASES for c in cells):
                header = cells
                continue
            else:
                continue
        else:
            if header is not None:
                active, header = None, None
            pairs = [p for clause, _, _, _ in _inputs(line) if clause is not None for p in scan(clause)]
        if not pairs:
            continue
        if matrix:
            # Each physical row gets its own metadata snapshot. A blank zone
            # in a later row must not erase the earlier row's explicit zone.
            scope = {**scope, "_sources": dict(scope.get("_sources", {}))}
            contexts.append(scope)
            pairs.sort(key=lambda p: {"项目名称": 0, "设计阶段": 1, "总体辖区": 2, "作业名称": 3}.get(p[0], 4))
        expanded = []
        for key, value in pairs:
            embedded = re.fullmatch(r"(.+?)\s*(?:按|取自|摘自)\s*(.+)", value) if key in DIMENSIONS else None
            if embedded and not (MISSING.search(value) or UNSAFE.search(value)) and re.search(CODE, embedded.group(2)):
                expanded.extend(((key, embedded.group(1).strip()), ("尺寸来源", embedded.group(2).strip())))
            else:
                expanded.append((key, value))
        pairs = expanded
        local = []
        for key, value in pairs:
            scoped = key in CONTEXT or key in BASIS and active is None and not any(k == "作业名称" for k, _ in pairs)
            if key == "辖区" and active is None and not any(k == "作业名称" for k, _ in pairs):
                key, scoped = "总体辖区", True
            if scoped:
                typed = _typed(key, value)
                if key in CONTEXT and (scope.get(key) not in {None, UNKNOWN, typed} or active is not None and scope.get(key) in {None, UNKNOWN}):
                    scope = {"总体辖区": UNKNOWN, "_sources": {}} if key == "项目名称" else {**scope, "_sources": dict(scope.get("_sources", {}))}
                    scope.pop(key, None)
                    scope["_sources"].pop(key, None)
                    contexts.append(scope)
                    active = None
                put(scope, key, value, position)
            else:
                local.append((key, value))
        if matrix:
            new()
        elif active is None and local and all(key == "待补资料" for key, _ in local):
            continue
        for key, value in local:
            if key == "_reference":
                candidates = [row for row in records if row["_file"] == file_id and row["_context"] == context_id() and value in row.get("作业名称", "")]
                active = candidates[0] if len(candidates) == 1 else None
                continue
            if key in {"作业名称", "作业编号"} and not matrix and active is not None and active.get(key) not in {None, UNKNOWN}:
                active = None
            if active is None:
                new()
            put(active, key, value, position)
    result = []
    for row in records or [{"_context": len(contexts)-1, "_origin": origin, "_sources": {}}]:
        context = contexts[row["_context"]]
        inherited = {key: value for key, value in context.items() if not key.startswith("_") and key != "总体辖区"}
        if "辖区" in row and row["辖区"] != context.get("总体辖区", UNKNOWN):
            for key in ("适用依据", "依据版本", "依据来源"):
                inherited.pop(key, None)
        sources = {key: value for key, value in context.get("_sources", {}).items() if key in inherited and key not in row}
        sources.update(row.get("_sources", {}))
        result.append({**inherited, **row, "_sources": sources, "_project_zone": context.get("总体辖区", UNKNOWN)})
    return result


def _cell(value):
    return html.escape(str(value), quote=False).replace("|", "&#124;").replace("\n", " ")


def _table(columns, records):
    return "\n".join(["| "+" | ".join(columns)+" |", "| "+" | ".join("---" for _ in columns)+" |",
                      *("| "+" | ".join(_cell(value) for value in row)+" |" for row in records)])


def build_draft(expert_id, tool_name, text):
    if expert_id != "method-hazard" or tool_name != TOOL:
        return None
    records = parse(text or "")
    out = ["# 危大判定书（AI 草稿 · 只整理判定资料，不签发）", "", DISCLAIMER, "",
           "- submit_blocked=true", "- 是否危大：信息不足", "- 是否可能超规模需论证：信息不足", ""]
    zones = {row.get("辖区", row.get("_project_zone", UNKNOWN)) for row in records}
    out += ["- 辖区："+(next(iter(zones)) if len(zones) == 1 else "逐作业登记，不合并辖区"), ""]
    for number, (title, keys) in enumerate((
        ("作业登记", ("项目名称", "设计阶段", "作业名称", "作业编号", "部位", "辖区", "计划作业日期")),
        ("辖区与依据来源", ("作业名称", "辖区", "适用依据", "依据版本", "依据来源", "CN依据", "SG依据", "EU依据")),
        ("尺寸与触发事实", ("作业名称", "支模高度", "架体高度", "高度", "开挖深度", "跨度", "施工总荷载", "起挑楼层", "单件重量", "起重设备", "额定起重量", "吊装高度", "吊装跨度", "尺寸来源")),
        ("环境与专项资料", ("作业名称", "支护形式", "周边环境", "地勘报告", "专项资料", "资料来源")),
        ("已有人工判定与证据", ("作业名称", "已有人工判定", "人工判定人", "人工判定证据")),
        ("缺项与待核对照", ("作业名称", "待补资料", "尺寸来源", "适用依据")),
    ), 1):
        out += [f"## {number} {title}", "", _table(keys+("资料核验状态", "签认"), [
            tuple(row.get(key, UNKNOWN) for key in keys)+("用户输入未核验；不是法定认定", "") for row in records]), ""]
    out += ["## 7 结论待判与方案交接", "", _table(("作业", "触发词（仅用户提供）", "是否危大", "是否超规模", "要否论证 / PTW", "待核接口", "签认"), [
        (row.get("作业名称", UNKNOWN),
         "；".join(word for word in ("临边", "开挖", "起重", "吊装", "脚手架", "高支模", "有限空间") if word in "；".join(str(v) for k, v in row.items() if not k.startswith("_"))) or UNKNOWN,
         "信息不足", "信息不足", "信息不足", "补齐正式规则原文、尺寸及现场资料，由持证人员复核；方案交施工方案岗", "") for row in records]), "",
        "SG 的 WSH / PTW 仅为待确认接口，不能套用 CN 规则。未指定辖区或未核验原文时，不补规范名称、门槛或条款；本岗不签发。", "",
        "## 参数与资料来源登记", "", _table(("作业", "参数栏目", "用户值（未核验）", "输入文件", "输入位置（非原件页码）", "核验状态"), [
            (row.get("作业名称", UNKNOWN), key, value, row.get("_origin") or UNKNOWN, row.get("_sources", {}).get(key, UNKNOWN), "未核验")
            for row in records for key, value in row.items() if not key.startswith("_") and value != UNKNOWN]), ""]
    # No named standard is selected merely from the task type or a place name.
    return "\n".join(out)
