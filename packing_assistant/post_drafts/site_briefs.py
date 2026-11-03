"""Source-owned site discussion templates, not instructions or signed records."""
from __future__ import annotations

import html
import re

from packing_assistant.post_drafts.planning import _inputs
from packing_assistant.post_drafts.procurement import _literal_source, _source_name
from packing_assistant.word_export import _table_cells

UNKNOWN = "UNSPECIFIED"
TOOLS = {"construction": "construction__scheme_draft", "emergency": "emergency__plan", "worker-brief": "worker-brief__talk"}
DISCLAIMER = ("AI 草稿 · 内部讨论 · 不得作为法定专项方案 / 交底签认件。本文件仅转录用户资料，"
              "未核验现场、参数、人员、资源、演练或规则原文。不构成设计文件、法定专项施工方案、"
              "交底签认件、监理指令、专家论证材料或开工/竣工验收依据。须持证人员复核签认。")
COMMON = {
    "项目名称": ("项目名称", "工程名称", "项目", "工程"), "资料阶段": ("资料阶段", "项目阶段"),
    "总体辖区": ("总体辖区", "项目辖区"), "辖区": ("辖区", "对象辖区"),
    "版本": ("版本", "文件版本", "预案版本"), "适用依据": ("适用依据", "编制依据", "依据"),
    "依据版本": ("依据版本",), "依据来源": ("依据来源",), "资料来源": ("资料来源", "来源文件", "来源"),
    "CN依据": ("CN依据",), "SG依据": ("SG依据",), "EU依据": ("EU依据",),
    "待补资料": ("待补资料", "缺项", "待核项"), "用户记录": ("用户记录", "用户实施记录", "实施记录"),
}
FIELDS = {
    "construction": {
        "作业部位": ("作业部位", "单位工程", "专项范围", "部位", "工作面"),
        "作业编号": ("作业编号", "工作面编号"), "工序": ("工序", "作业内容", "专项名称"),
        "支模高度": ("支模高度", "支撑高度", "架体高度", "架高", "高度"),
        "板厚": ("板厚", "厚度"), "梁截面": ("最大梁截面", "梁截面", "截面"),
        "跨度": ("最大跨度", "跨度"), "架体": ("架体", "支架", "支撑体系", "支撑形式", "材料"),
        "开挖深度": ("开挖深度", "基坑深度", "深度"), "支护形式": ("支护形式", "围护形式"),
        "分层开挖厚度": ("分层开挖厚度", "每层开挖厚度", "分层厚度"),
        "图纸": ("图纸", "图号", "尺寸来源"), "勘察报告": ("勘察报告编号", "勘察报告", "地勘报告"),
        "计划开始": ("计划开始", "搭设日期", "开始日期"), "施工员": ("施工员", "现场施工员", "负责人", "责任人", "责任接口"),
        "机械": ("机械", "设备", "挖机", "机具"), "劳动力": ("劳动力", "人数"),
        "荷载": ("荷载", "施工总荷载", "用户荷载"), "现场条件": ("现场条件", "周边环境"),
        "工艺步骤": ("工艺步骤", "用户工艺", "施工工艺"), "质量要求": ("质量要求", "质量检查"),
        "安全措施": ("安全措施", "防护措施"), "应急联系人": ("应急联系人",),
        "应急措施": ("应急措施",), "环保措施": ("环保措施",),
        "监测要求": ("监测要求",), "验收资料": ("验收资料", "验收记录"),
    },
    "emergency": {
        "场景": ("场景", "风险场景", "作业", "场所"), "场景编号": ("场景编号",),
        "部位": ("部位", "位置", "现场处置部位"), "风险": ("主要风险", "风险", "风险描述", "事故风险"),
        "响应分级": ("响应分级", "响应层级"), "总指挥": ("总指挥", "组长", "应急领导小组组长"),
        "现场处置负责人": ("现场处置负责人", "现场处置组组长", "现场处置", "牵头人", "负责人"),
        "抢险队伍": ("抢险队伍", "救援队伍", "队伍"), "应急物资": ("应急物资", "应急资源", "资源", "装备", "设备"),
        "拟应急物资": ("拟应急物资", "计划应急物资"), "拟抢险队伍": ("拟抢险队伍", "计划救援队伍"),
        "联系人": ("联系人",), "电话": ("电话", "联系电话"), "联络来源": ("联络来源", "联系方式来源"),
        "医院": ("医院", "医院名称"), "医院路线": ("医院路线",),
        "洗消点": ("洗消点",), "避险点": ("避险点", "集合点", "疏散集合点"),
        "撤离路线": ("撤离路线",), "报告顺序": ("报告顺序", "内部升级顺序"),
        "预案名称": ("预案名称",), "处置原则": ("处置原则", "处置措施"),
        "演练科目": ("演练科目", "演练目标", "科目"), "演练时间": ("演练时间", "计划演练日期", "演练日期"),
        "参演单位": ("参演单位",), "评估人": ("评估人",), "实际演练日期": ("实际演练日期",),
        "演练记录": ("演练记录", "实际演练记录"), "发现问题": ("发现问题",), "修订意见": ("修订意见",),
        "公布日": ("公布日",), "拟备案机关": ("拟备案机关",), "评估年": ("评估年",),
    },
    "worker-brief": {
        "班组": ("班组", "工种", "听众"), "部位": ("部位", "作业面", "位置"),
        "今天作业": ("今天活", "今天作业", "工序", "作业内容"), "作业日期": ("作业日期", "班前日期", "日期"),
        "人数": ("人数",), "危险点": ("危险", "危险点", "风险", "隐患"),
        "交叉作业": ("交叉作业",), "防护": ("防护", "防护措施", "动作", "措施", "做法"),
        "喊停": ("喊停", "停工条件", "喊停条件"), "带班": ("带班", "带班人", "班组长"),
        "安全员": ("安全员",), "报告对象": ("报告对象", "找谁"), "电话": ("电话", "联系电话"),
        "监护人": ("监护人", "监护"),
        "联系人来源": ("联系人来源",), "撤离点": ("撤离点", "集合点", "撤离方向"),
        "用户尺寸": ("用户尺寸", "尺寸"), "参数来源": ("参数来源", "尺寸来源"), "天气": ("天气",),
    },
}
IDENTITY = {"construction": ("作业部位", "作业编号"), "emergency": ("场景", "场景编号"), "worker-brief": ("班组", "部位")}
CONTEXT = {"项目名称", "资料阶段", "总体辖区"}
NUMBER = r"[+-]?\d+(?:\.\d+)?"
DATE = r"(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}月\d{1,2}[号日]|今天|明天|周[一二三四五六日])"
LOCATION = r"(?:\d+#楼(?:[东西南北]侧)?(?:外架[一二三四五六七八九十\d]+层|地下室(?:顶板|后浇带)?|[一二三四五六七八九十\d]+层|屋面)?|裙楼中庭|[A-Z]\d*区基坑)"
MISSING = re.compile(r"未提供|未给|未定|未量|未查|待填|待核|待补|未知|没定|没查到|没排|没人|还没|没有|不在场|没派|没统计|忘了|UNSPECIFIED|TBD|N/A", re.I)
UNSAFE = re.compile(r"例如|比如|示例|假设|如果|自行计算|自己算|不是|不采用|是否|[？?]")
FUTURE_RESOURCE = re.compile(r"预计|计划|拟|准备(?!了|好)|将")
SOURCES = {"资料来源", "依据来源", "图纸", "勘察报告", "参数来源", "联系人来源", "联络来源", "验收资料", "演练记录"}
FACTS = {"支模高度", "板厚", "梁截面", "跨度", "开挖深度", "分层开挖厚度", "荷载",
         "施工员", "现场处置负责人", "总指挥", "评估人", "带班", "安全员", "电话", "医院", "用户尺寸",
         "应急物资", "抢险队伍", "机械", "劳动力", "人数", "实际演练日期", "监护人",
         "演练时间", "计划开始", "作业日期"}


def _clean(raw):
    value = raw.strip(" \t\r\n，,；。")
    return value.rstrip(";") if not re.search(r"&(?:#\d+|#x[0-9a-f]+|[a-z][a-z0-9]+);$", value, re.I) else value


def _typed(key, raw):
    value = _clean(raw) or UNKNOWN
    if key in {"辖区", "总体辖区"}:
        return {"CN": "CN", "SG": "SG", "EU": "EU", "DUAL": "DUAL", "中国": "CN", "新加坡": "SG"}.get(value.upper(), UNKNOWN)
    if value == UNKNOWN or MISSING.fullmatch(value):
        return UNKNOWN
    if key in FACTS | SOURCES and (MISSING.search(value) or UNSAFE.search(value)):
        return UNKNOWN
    if key in SOURCES | {"实际演练日期"} and re.search(r"计划提供|预计|拟提供|将提供|计划演练|拟演练", value):
        return UNKNOWN
    return value


def _natural(text, post):
    text = re.sub(r"^.*?[:：](?=(?:一块是|另一块|一个是|另一个是|一摊是|另一摊))", "", text)
    if UNSAFE.search(text):
        return []
    pairs = []
    def emit(key, pattern, group=1):
        match = re.search(pattern, text, re.I)
        if match:
            value = UNKNOWN if key in FACTS | SOURCES and MISSING.search(text) else match.group(group)
            if post == "emergency" and key in {"应急物资", "抢险队伍"} and FUTURE_RESOURCE.search(text):
                key = "拟"+key
            pairs.append((key, value))
    if re.fullmatch(r"SG|CN|EU|DUAL|中国|新加坡", text, re.I):
        return [("总体辖区", text)]
    if post == "construction":
        project = re.search(r"^(.+?)(?=(?:二期|一期)?\d+#楼)", text)
        if project and not re.match(r"^(?:一块是|另一块|方案|帮我|生成|计划|今天|明天)", project.group(1)):
            pairs.append(("项目名称", project.group(1)))
        emit("作业部位", "("+LOCATION+")")
        for key, label, unit in (
            ("支模高度", "支模高度|架子高|高度", r"(?:米|m)?"), ("板厚", "板厚", r"(?:mm|米|m)?"),
            ("梁截面", "最大梁截面|梁截面", r"(?:mm)?"), ("跨度", "跨度", r"(?:米|m)?"),
        ):
            number = NUMBER+r"(?:[×x*]"+NUMBER+r")?" if key == "梁截面" else NUMBER
            emit(key, r"(?:"+label+r")\s*(?:是|为)?\s*("+number+r"\s*"+unit+r")")
        emit("架体", r"(盘扣(?:式)?)(?:的)?")
        emit("架体", r"架体(?:用|采用|为)(.+)$")
        emit("支护形式", r"支护(?:形式)?(?:用|采用|为|是)(.+)$")
        emit("图纸", r"图纸(?:是|为)\s*(.+)$")
        emit("计划开始", r"(?:计划)?("+DATE+r")开始")
        emit("施工员", r"(?:现场)?施工员\s*([一-鿿]{2,4})$")
        reference = re.match(r"^(.+?)(?:归|这边谁管)", text)
        if reference:
            pairs.insert(0, ("_reference", reference.group(1)))
            emit("施工员", r"归([一-鿿]{2,4})盯")
            if MISSING.search(text):
                pairs.append(("施工员", UNKNOWN))
        if re.search(r"架子.*还没定", text):
            pairs.append(("架体", UNKNOWN))
        if "板厚" in text and MISSING.search(text):
            pairs.append(("板厚", UNKNOWN))
        if re.search(r"支模高度|架子高|高度", text) and MISSING.search(text):
            pairs.append(("支模高度", UNKNOWN))
    elif post == "emergency":
        emit("项目名称", r"^(.+?)(?=基坑开挖阶段)")
        emit("资料阶段", r"(基坑开挖阶段)")
        emit("场景", r"^(?:一个是|另一个是)(.+)$")
        emit("风险", r"(?:主要风险是|主要怕)(.+)$")
        if re.match(r"^(?:一个是|另一个是)", text) and "起重伤害" in text:
            pairs.append(("风险", "起重伤害"))
        emit("总指挥", r"(?:应急领导小组组长是|总指挥是)(.+)$")
        emit("现场处置负责人", r"现场处置由(.+?)牵头")
        emit("现场处置负责人", r"处置这块归(.+?)管")
        emit("抢险队伍", r"抢险队伍是(.+)$")
        emit("应急物资", r"(?:现场备了|配的是)(.+)$")
        emit("拟应急物资", r"(?:计划|拟|准备)(?:采购|配备|备好)(.+)$")
        emit("演练时间", r"("+DATE+r")(?:打算组织|$)")
        emit("演练时间", r"演练定在("+DATE+r")")
        emit("避险点", r"(?:临时)?避险点设在(.+)$")
        if "抢险谁牵头" in text and MISSING.search(text):
            pairs.append(("现场处置负责人", UNKNOWN))
        if "没排演练" in text:
            pairs.append(("演练时间", UNKNOWN))
        emit("风险", r"\b(fire|火灾)\b")
    else:
        crew = re.search(r"(钢筋班|架子工班|混凝土班|木工班|泥工班|电焊工|焊工班|电工班)(?=今天|明天|在|$)", text)
        if not crew:
            crew = re.search(r"^(?:一摊是|另一摊|今天|明天)?([一-鿿]{2,8}班)(?=今天|明天|在|$)", text)
        if crew:
            pairs.append(("班组", crew.group(1)))
        emit("部位", "("+LOCATION+")")
        emit("今天作业", r"(?:今天上午|今天下午|明天|在)?"+LOCATION+r"(.+)$")
        emit("作业日期", r"(今天上午|今天下午|明天|今天)")
        emit("人数", r"(?:今天|今日)(\d+人)")
        emit("危险点", r"(?:紧挨着|底下就是)(.+)$")
        if re.search(r"防护盖板被人挪开|泵送车管子", text):
            pairs.append(("危险点", text))
        emit("交叉作业", r"(木工在下面拆模.*)$")
        emit("防护", r"(?:得|要)(铺挡火毯)")
        emit("防护", r"^(?:得|要)((?:铺|系|挂|盖|围|设置|使用).+)$")
        emit("喊停", r"(防护没了先停)")
        emit("带班", r"(?:找带班|班组长)([一-鿿]{2,4})$")
        emit("安全员", r"(?:再找安全员|安全员)([一-鿿]{2,4})$")
        emit("撤离点", r"(?:出事)?先撤到(.+)$")
        emit("用户尺寸", "("+NUMBER+r"\s*mm)")
        if re.search(r"^动火$|^临边$|^洞口$|\bedge\b", text, re.I):
            pairs.append(("危险点", re.sub(r"^.*?(?:白话|口播)\s*", "", text)))
        if not re.search(r"(?:没有|不涉及|不安排|取消|远离|无)(?:.{0,8})(?:临边|洞口|动火|触电|吊装|交叉作业)", text):
            for match in re.finditer(r"临边|洞口|动火|触电|吊装|交叉作业", text):
                pairs.append(("危险点", match.group()))
        if "安全员" in text and "不在场" in text:
            pairs.append(("安全员", UNKNOWN))
    if MISSING.search(text):
        pairs.append(("待补资料", text))
    return pairs


def parse(text, post):
    aliases = {a.casefold(): k for k, names in {**COMMON, **FIELDS[post]}.items() for a in names}
    labels = "|".join(map(re.escape, sorted(aliases, key=len, reverse=True)))
    label = re.compile(r"(?<![A-Za-z0-9_])("+labels+r")\s*[:：=]\s*", re.I)
    bare = re.compile(r"^("+labels+r")\s+(.+)$", re.I)
    contexts, records = [], []
    scope = {"总体辖区": UNKNOWN, "_sources": {}}
    contexts.append(scope)
    active, file_id, origin, header = None, 0, "", None
    def context_id():
        return next(i for i, context in enumerate(contexts) if context is scope)
    def new():
        nonlocal active
        active = {"_context": context_id(), "_file": file_id, "_origin": origin, "_sources": {}}
        records.append(active)
    def put(row, key, raw, position):
        value = _typed(key, raw)
        if value == UNKNOWN:
            if row.get(key) not in {None, UNKNOWN}:
                row["待补资料"] = "；".join(filter(None, (row.get("待补资料"), key+"原值待复核："+row[key])))
            row[key] = UNKNOWN
            row["_sources"].pop(key, None)
        else:
            row[key] = value if row.get(key) in {None, UNKNOWN} else "；".join(dict.fromkeys((row[key], value)))
            row["_sources"][key] = "；".join(dict.fromkeys(filter(None, (row["_sources"].get(key), position))))
        if value == UNKNOWN and _clean(raw) not in {"", UNKNOWN}:
            row["待补资料"] = "；".join(filter(None, (row.get("待补资料"), key+"："+_clean(raw))))
    def scan(clause):
        clause = re.sub(r"^(?:本次实际情况|实际情况|真实记录)\s*[:：]\s*", "", clause)
        matches = list(label.finditer(clause))
        if matches:
            found = [(aliases[m.group(1).casefold()], clause[m.end():matches[i+1].start() if i+1 < len(matches) else len(clause)]) for i, m in enumerate(matches)]
            expanded = []
            for key, raw in found:
                # A list of people under "喊停" is not a stop condition.
                leader = re.fullmatch(r"(?:班组长|带班)([一-鿿]{2,4})", _clean(raw)) if post == "worker-brief" and key == "喊停" else None
                source = re.fullmatch(r"(.+?)\s*(?:按|取自|摘自)\s*(.+)", raw) if key in {"支模高度", "板厚", "梁截面", "跨度", "开挖深度", "分层开挖厚度", "用户尺寸"} else None
                if leader:
                    expanded.append(("带班", leader.group(1)))
                elif post == "emergency" and key in {"应急物资", "抢险队伍"} and FUTURE_RESOURCE.search(raw):
                    expanded.append(("拟"+key, raw))
                elif source and not (MISSING.search(raw) or UNSAFE.search(raw)) and re.search(r"[A-Za-z一-鿿]+[-_][A-Za-z0-9]+", source.group(2)):
                    expanded.extend(((key, source.group(1)), ("参数来源" if post == "worker-brief" else "图纸", source.group(2))))
                else:
                    expanded.append((key, raw))
            return expanded
        match = bare.match(clause)
        return [(aliases[match.group(1).casefold()], match.group(2))] if match else _natural(clause, post)
    for line_number, line in enumerate(_literal_source(text).splitlines(), 1):
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
                pairs = [(aliases[h.casefold()], cells[i] if i < len(cells) else "") for i, h in enumerate(header) if h.casefold() in aliases]
                active, matrix = None, True
                scope = {**scope, "_sources": dict(scope["_sources"])}
                contexts.append(scope)
                pairs.sort(key=lambda p: {"项目名称": 0, "资料阶段": 1, "总体辖区": 2}.get(p[0], 3))
            elif len(cells) >= 2 and cells[0].casefold() in aliases and cells[1].casefold() not in aliases and not any(cells[2:]):
                pairs = [(aliases[cells[0].casefold()], cells[1])]
            elif any(c.casefold() in aliases for c in cells):
                header = cells
                continue
            else:
                continue
        else:
            if header is not None:
                active, header = None, None
            pairs = [p for clause, _, _, _ in _inputs(line) if clause is not None for p in scan(clause)]
        if matrix:
            new()
        for key, raw in pairs:
            scoped = key in CONTEXT or key in {"适用依据", "依据版本", "依据来源", "CN依据", "SG依据", "EU依据"} and active is None and not any(k in IDENTITY[post] for k, _ in pairs)
            if key == "辖区" and active is None and not any(k in IDENTITY[post] for k, _ in pairs):
                key, scoped = "总体辖区", True
            if scoped:
                if matrix and active is not None and not any(not k.startswith("_") for k in active):
                    records.remove(active)
                    active = None
                if scope.get(key) not in {None, UNKNOWN, _typed(key, raw)} or active is not None and key in CONTEXT and scope.get(key) in {None, UNKNOWN}:
                    scope = {"总体辖区": UNKNOWN, "_sources": {}} if key == "项目名称" else {**scope, "_sources": dict(scope["_sources"])}
                    scope.pop(key, None)
                    scope["_sources"].pop(key, None)
                    contexts.append(scope)
                    active = None
                put(scope, key, raw, position)
                continue
            if key == "_reference":
                candidates = [r for r in records if r["_file"] == file_id and r["_context"] == context_id()
                              and raw in r.get(IDENTITY[post][0], "")]
                active = candidates[0] if len(candidates) == 1 else None
                continue
            if not matrix and key in IDENTITY[post] and active is not None and active.get(key) not in {None, UNKNOWN}:
                active = None
            if active is None:
                if key == "待补资料":
                    put(scope, key, raw, position)
                    continue
                new()
            put(active, key, raw, position)
    result = []
    for row in records or [{"_context": len(contexts)-1, "_file": file_id, "_origin": origin, "_sources": {}}]:
        context = contexts[row["_context"]]
        inherited = {key: value for key, value in context.items() if not key.startswith("_") and key != "总体辖区"}
        if "辖区" in row and row["辖区"] != context.get("总体辖区", UNKNOWN):
            for key in ("适用依据", "依据版本", "依据来源"):
                inherited.pop(key, None)
        sources = {key: value for key, value in context["_sources"].items() if key in inherited and key not in row}
        sources.update(row["_sources"])
        result.append({**inherited, **row, "_sources": sources, "_project_zone": context.get("总体辖区", UNKNOWN)})
    return result


def _cell(value):
    return html.escape(str(value), quote=False).replace("|", "&#124;").replace("\n", "；")


def _table(keys, rows):
    return "\n".join(["| "+" | ".join(keys)+" |", "| "+" | ".join("---" for _ in keys)+" |",
                      *("| "+" | ".join(_cell(value) for value in row)+" |" for row in rows)])


SCHEME = ("封面与文件控制", "草稿与责任声明", "工程概况", "编制依据", "施工部署与工艺", "质量", "安全与应急", "环保与文明施工", "资源计划", "验收与资料", "附录")
EMERGENCY = ("封面与声明", "编制说明", "综合预案目录", "专项预案目录", "现场处置方案", "应急处置卡", "信息报告", "演练计划与记录表头", "附件", "备案与评估节点", "禁令")
WORKER = ("今天干什么", "哪儿会掉、会砸、会淹", "三步怎么干", "谁喊停、找谁", "结束")


def build_draft(post, tool, text):
    if TOOLS.get(post) != tool and not (post == "construction" and tool == "construction__fill_scheme_docx"):
        return None
    records = parse(text or "", post)
    title = {"construction": "专项施工方案讨论提纲（AI 草稿）", "emergency": "生产安全事故应急预案提纲（AI 草稿 · 内部讨论）", "worker-brief": "班前白话稿（AI 草稿 · 内部讨论）"}[post]
    zones = {r.get("辖区", r["_project_zone"]) for r in records}
    out = ["# "+title, "", DISCLAIMER, "", "- submit_blocked=true",
           "- 辖区："+(next(iter(zones)) if len(zones) == 1 else "逐对象登记，不合并辖区"),
           "[A001] / UNSPECIFIED 为待填；所有用户资料未核验，签认不代填。", ""]
    def tab(title, keys, extras=()):
        out.extend(["### "+title, "", _table(keys+tuple(k for k, _ in extras),
                   [tuple(r.get(k, UNKNOWN) for k in keys)+tuple(v for _, v in extras) for r in records]), ""])
    chapters = SCHEME if post == "construction" else EMERGENCY if post == "emergency" else WORKER
    for i, heading in enumerate(chapters, 1):
        out += [f"## {i} {heading}", ""]
        if post == "construction":
            if i == 1:
                tab("项目与专项范围", ("项目名称", "资料阶段", "作业部位", "作业编号", "工序", "版本", "辖区"), (("签认", ""),))
            elif i == 2:
                out += [DISCLAIMER, "", "未执行结构、支架荷载或稳定计算。不是法定专项、专家论证资料或作业许可。", ""]
            elif i == 3:
                tab("施工参数与现场条件", ("作业部位", "支模高度", "板厚", "梁截面", "跨度", "架体", "开挖深度", "支护形式", "分层开挖厚度", "荷载", "现场条件"), (("核验状态", "未核验"),))
            elif i == 4:
                tab("用户依据与图纸", ("作业部位", "图纸", "勘察报告", "适用依据", "依据版本", "依据来源", "CN依据", "SG依据", "EU依据"), (("已核实依据", UNKNOWN), ("条款", UNKNOWN)))
            elif i == 5:
                tab("施工部署与用户工艺", ("作业部位", "工序", "计划开始", "施工员", "工艺步骤"), (("实施状态", "未核验"),))
            elif i == 6:
                tab("质量检查待核", ("作业部位", "质量要求", "用户记录"), (("本轮检查", "未执行"), ("签认", "")))
            elif i == 7:
                tab("安全应急与监测接口", ("作业部位", "安全措施", "监测要求", "应急措施", "应急联系人"), (("审批与许可", UNKNOWN),))
            elif i == 8:
                tab("用户环保措施", ("作业部位", "环保措施"), (("实施核验", "未执行"),))
            elif i == 9:
                tab("用户资源计划", ("作业部位", "机械", "劳动力", "架体"), (("资源核验", "未盘点"),))
            elif i == 10:
                tab("验收与资料待核", ("作业部位", "验收资料", "待补资料"), (("验收结论", UNKNOWN), ("签认", "")))
            else:
                tab("图号和资料目录", ("作业部位", "图纸", "勘察报告", "资料来源"))
        elif post == "emergency":
            if i == 1:
                tab("项目与预案范围", ("项目名称", "资料阶段", "场景", "预案名称", "版本", "辖区"), (("签署", ""),))
            elif i == 2:
                tab("风险场景与现场资源", ("场景", "部位", "风险", "抢险队伍", "应急物资", "拟抢险队伍", "拟应急物资"), (("盘点核验", "未执行"),))
            elif i == 3:
                tab("组织职责与响应接口", ("场景", "响应分级", "总指挥", "现场处置负责人", "报告顺序"), (("指令签发", "未执行"),))
            elif i == 4:
                tab("本轮点名专项", ("场景", "风险"), (("本稿", "本轮点名仅限已登记用户资料，不展开假场景"),))
                out += ["常见目录名称（不是本项目风险认定）：高处坠落、物体打击、坍塌、触电、起重机械、火灾爆炸、中毒窒息/有限空间、车辆伤害。", ""]
            elif i in {5, 6}:
                tab("处置与撤离接口" if i == 5 else "应急资源联络", ("场景", "部位", "现场处置负责人", "处置原则", "避险点", "撤离路线", "洗消点", "联系人", "电话", "联络来源"))
                out += ["有限空间不得盲目进入施救；本稿不发救援指令。", ""]
            elif i == 7:
                tab("用户报告安排", ("场景", "报告顺序", "总指挥", "电话"), (("报告完成情况", UNKNOWN),))
            elif i == 8:
                tab("拟演练计划", ("场景", "演练时间", "演练科目", "参演单位", "评估人"), (("计划核验", "未确认、不是完成记录"),))
                tab("用户实际演练记录待核", ("场景", "实际演练日期", "演练记录", "发现问题", "修订意见"), (("本轮评估结论", UNKNOWN), ("签认", "")))
            elif i == 9:
                tab("通讯与资源附件", ("场景", "联系人", "电话", "医院", "医院路线", "应急物资", "联络来源", "资料来源"), (("核验状态", "未核验"),))
            elif i == 10:
                tab("备案与评估待核", ("场景", "公布日", "拟备案机关", "评估年", "适用依据"), (("备案条件", UNKNOWN),))
            else:
                out += ["不编电话、医院、救援能力或响应时长；用户输入不等于已盘点、已完成演练或已公布备案。", ""]
        else:
            if i == 1:
                tab("今天作业与听众", ("班组", "部位", "今天作业", "作业日期", "人数"))
            elif i == 2:
                tab("危险点与交叉作业", ("班组", "部位", "危险点", "交叉作业", "天气"))
            elif i == 3:
                tab("防护动作与来源", ("班组", "部位", "防护", "用户尺寸", "参数来源"), (("现场核验", "未执行"),))
                out += ["尺寸按书面交底，不把无来源尺寸写成操作标准。", ""]
            elif i == 4:
                tab("喊停与报告对象", ("班组", "部位", "喊停", "带班", "安全员", "监护人", "报告对象", "电话", "联系人来源", "撤离点"))
            else:
                out += ["没听懂再问，不丢人。口播不是交底签认件，不能代替书面安全技术交底和班组签字。", ""]
    if post == "worker-brief":
        out += ["## 班前口播（逐作业讨论词，不是开工指令）", ""]
        for index, row in enumerate(records, 1):
            def say(key, fallback="待现场核对"):
                value = row.get(key, UNKNOWN)
                return fallback if value == UNKNOWN else html.escape(value, quote=False)
            out += [f"### 第{index}摊活", "",
                    f"{say('班组', '班组待填')}的工友，这摊活在{say('部位', '[A001]部位待填')}，任务是{say('今天作业', '[A001]工序待填')}。",
                    f"今天要注意：{say('危险点', '[A001]危险点待核')}。"+(f"交叉作业：{say('交叉作业')}。" if row.get("交叉作业") else ""),
                    f"用户交代的防护做法是：{say('防护', '[A001]防护做法待核')}。具体按经复核的书面交底，不猜尺寸或挂点。",
                    f"遇到{say('喊停', '防护不齐、看不懂或身体不适')}，先停、离开危险处。"
                    f"先找{say('带班', '带班（姓名待填）')}，再找{say('安全员', '安全员（姓名待填）')}。"
                    "没听懂再问，口播代替不了书面交底和签字。", ""]
        out += ["朗读范围仅上面的口播；若资料过多，由现场复核后分场次讲。本轮未进行班前会或书面交底。toolbox / 班前会不是书面交底。", ""]
    keys = ("项目名称", "资料阶段", "总体辖区", "适用依据", "依据版本", "依据来源", "CN依据", "SG依据", "EU依据")
    out += ["## 对象依据与缺项归属", "", _table(keys+("对象", "待补资料", "核验状态"), [
        tuple(r.get(k, r["_project_zone"] if k == "总体辖区" else UNKNOWN) for k in keys)+
        (r.get(IDENTITY[post][0], UNKNOWN), r.get("待补资料", UNKNOWN), "unverified") for r in records]), "",
        "## 参数与资料位置", "", _table(("对象", "参数栏目", "用户值（未核验）", "输入文件", "输入位置（非原件页码）", "状态"), [
            (r.get(IDENTITY[post][0], UNKNOWN), key, value, r["_origin"] or UNKNOWN, r["_sources"].get(key, UNKNOWN), "未核验")
            for r in records for key, value in r.items() if not key.startswith("_") and value != UNKNOWN]), ""]
    return "\n".join(out)
