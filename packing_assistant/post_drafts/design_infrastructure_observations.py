"""Literal infrastructure records; no design calculations or approval effects.

File, project and physical-record boundaries own values. Named updates resolve
only within the same file/project/stage, never by proximity or a guessed ID.
"""
from __future__ import annotations

import html
import re

from packing_assistant.post_drafts.design_infrastructure import PROFILES, _COMMON, _EXTRA, UNKNOWN
from packing_assistant.post_drafts.planning import _inputs
from packing_assistant.post_drafts.procurement import _literal_source, _source_name
from packing_assistant.word_export import _table_cells

_NUMBER = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_LENGTH = _NUMBER+r"\s*(?:米|mm|cm|m)?"
_CODE = r"[A-Za-z]+[A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)+"
_STATION = r"[A-Z]?K\d+\+\d+(?:\.\d+)?"
_RANGE = _STATION+r"\s*(?:到|~|～|-|至)\s*"+_STATION
_TIME = r"\d{1,2}:\d{2}\s*[-~～至]\s*\d{1,2}:\d{2}"
_DATE = r"(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}月\d{1,2}[日号]?|月底|周[一二三四五六日])"
_UNKNOWN = re.compile(r"还没|没定|没给|没人给|没拿到|没发|未给|未定|未提供|未说明|待定|待补|待填|未知|未回|还在钻|下个月才出|UNSPECIFIED|TBD|N/A", re.I)
_UNSAFE = re.compile(r"比如|例如|示例|样例|假设|如果|自行计算|自己算|不采用|不按|别按|不是|不要|是否|[？?]")
_META = {"项目名称", "图纸清单", "总体辖区", "设计阶段"}
_BASIS = {name.split("|", 1)[0] for name in _COMMON if name != "辖区"}
_NUMERIC = {"红线宽", "设计速度", "设计高程", "现状地面标高", "纵坡", "坡长", "管径", "流量", "接驳标高", "路面厚度", "设计年限",
            "桥长", "桥宽", "跨径组合", "梁高", "桩长", "承台尺寸", "通航净空", "设计高水位", "设计低水位", "吞吐量", "波浪要素", "前沿高程", "泊位长度", "泊位宽度",
            "隧道长度", "埋深", "交通量", "现状信号周期", "配时", "版面尺寸", "建筑面积"}
FLOW_FIELDS = {"调查日期", "调查时段", "方向车型", "用户车型", "交通量", "流量文件", "调查来源", "现状信号周期", "配时"}
PIPE_FIELDS = {"管线名称", "现状管线", "拟建管线", "管线权属单位", "管线来源", "交叉位置", "管径"}
_MATERIAL_FIELDS = {
    "地质资料", "水文资料", "地质来源", "水文来源", "资料来源", "荷载来源",
    "围岩来源", "支护来源", "监测来源", "监测阈值来源", "管线来源",
    "流量文件", "调查来源", "信号配时文件", "路网文件", "模型文件",
    "状态证据", "决议证据", "闭环证据", "变更文件", "会审纪要文件",
}
_FUTURE = re.compile(r"预计|拟出|拟提供|计划提供|计划生成|将提供|将生成|稍后补|答应|承诺|口头|未签|待签|待复核|待审批|尚未")


def _aliases(post):
    profile = PROFILES[post]
    fields = (*_COMMON, "总体辖区|项目辖区", "设计阶段", "资料来源|来源", "待核记录|待核项", "项目名称|工程名称",
              *(f for s in profile.sections for f in s.fields), *_EXTRA[post])
    return {a.casefold(): name.split("|", 1)[0] for name in fields for a in name.split("|")}


def _clean(value):
    value = value.strip(" \t\r\n,，；。")
    if not re.search(r"&(?:#\d+|#x[0-9a-f]+|[a-z][a-z0-9]+);$", value, re.I):
        value = value.rstrip(";")
    return value or UNKNOWN


def _typed(key, raw):
    value = _clean(raw)
    if re.fullmatch(r"(?:\[A\d+\]\s*)?(?:待填|待核|未知|未提供|未给|未说明|没定|没给|UNSPECIFIED|TBD|N/A)", value, re.I):
        return UNKNOWN
    if key in {"辖区", "总体辖区"}:
        return {"CN": "CN", "SG": "SG", "EU": "EU", "DUAL": "DUAL", "中国": "CN", "新加坡": "SG"}.get(value.upper(), UNKNOWN)
    if key in _NUMERIC and (_UNKNOWN.search(value) or _UNSAFE.search(value)):
        return UNKNOWN
    if key in _MATERIAL_FIELDS and (_UNKNOWN.search(value) or _FUTURE.search(value) or _UNSAFE.search(value)):
        return UNKNOWN
    return value


def _natural(text, post):
    """Bounded literal observations; no numeric/unit transformation."""
    if _UNSAFE.search(text):
        return []
    if post != "design-coord":
        text = re.sub(r"^(?:另一个|帮我(?:出一份|出个|搭个)|出一份)\s*", "", text)
    p = []

    def emit(key, pattern, group=1):
        m = re.search(pattern, text, re.I)
        if m and m.group(group) is not None:
            p.append((key, m.group(group), "自然字段"))

    def number(key, label, unit=r"(?:米|mm|cm|m)?"):
        emit(key, label+r"\s*(?:是|为|按|取|采用|约|大概|才|设计院给的)?\s*("+_NUMBER+r"\s*"+unit+r")")

    if re.fullmatch(r"CN|SG|EU|DUAL|中国|新加坡", text, re.I):
        p.append(("辖区", text, "自然字段"))
    emit("项目名称", r"项目(?:是|为)\s*(.+)$")
    emit("设计阶段", r"(初步设计|施工图设计|方案深化|方案设计)(?:阶段)?")
    if post == "municipal":
        emit("道路名称", r"([一-鿿]{2,12}(?:大道|东路|西路|南路|北路|街|路))(?=按城市|那段|桩号)")
        emit("道路等级", r"按(城市(?:次干路|主干路|支路))做")
        emit("起止桩号", r"(?:起止桩号|那段是|桩号)\s*("+_RANGE+r")")
        number("红线宽", r"红线(?:宽度|宽)?")
        number("设计速度", "设计速度", r"(?:km/h|公里/小时)?")
        number("现状地面标高", "现状地面标高")
        emit("面层", r"路面甲方(?:意向用|想用)(.+)$")
        emit("现状管线", r"(?:现状|有一根现状|有根)(DN\d+.+)$")
        emit("管线权属单位", r"权属(?:单位)?(?:是|为)(.+)$")
        if "迁改" in text:
            emit("拟建管线", r"(DN\d+.+迁改)")
    elif post == "bridge":
        emit("桥梁名称", r"([一-鿿]{2,12}(?:大桥|中桥|桥))(?=跨径|是\d|跨航道)")
        emit("跨径组合", r"(?:跨径(?:布置)?|(?:桥是)|是)\s*("+_NUMBER+r"(?:\s*[×xX+]\s*"+_NUMBER+r")+(?:米|m)?)")
        number("跨径组合", "主跨")
        number("桥宽", "桥宽")
        emit("桥位", r"桥位\s*(.+)$")
        emit("荷载等级", r"荷载(?:等级)?\s*((?:城-[A-Z]|公路-[IV]+)级)")
        emit("截面型式", r"上部(.+)$")
        emit("截面型式", r"\d[×xX]\d+(?:米|m)?(.+板)$")
        emit("候选桥型", r"想做(.+)$")
        emit("墩台型式", r"下部(.+?)(?=配|$)")
        emit("基础型式", r"(钻孔灌注桩|扩大基础|沉井基础)")
        emit("地质资料", r"(?:地勘报告编号|地质报告编号)\s*("+_CODE+r")")
        emit("地质资料", r"^(地质报告有了)$")
        emit("拟议施工方法", r"施工方法\s*(.+)$")
        number("通航净空", r"通航(?:净高|净空)(?:要求)?")
    elif post == "port":
        berth = re.search(r"(\d+#泊位)|^(\d+#)(?:是|走)(.+)$", text)
        if berth:
            p.append(("码头名称", berth.group(1) or berth.group(2)+"泊位", "自然对象"))
            if berth.group(3):
                p.append(("港口功能", berth.group(3), "自然字段"))
        emit("设计船型", r"(?:靠)?("+_NUMBER+r"\s*万吨级(?:的船|[^，；]*)?)")
        emit("吞吐量", r"年吞吐量(?:按|为)?\s*("+_NUMBER+r"\s*万吨)")
        number("设计高水位", r"(?:设计)?高水位")
        number("设计低水位", r"(?:设计)?低水位")
        emit("波浪要素", r"(H\d+%\s*(?:波高|浪高)\s*"+_LENGTH+r")")
        emit("地质资料", r"(?:地勘报告编号|报告号)\s*("+_CODE+r")")
        emit("候选结构", r"结构先按(.+?)比选")
        number("泊位长度", "泊位长度")
        emit("陆域接口", r"^(水利院对接人.+)$")
        reference = re.search(r"^地勘只有(\d+#)做了|^(\d+#)还在钻", text)
        if reference:
            p.insert(0, ("_reference", reference.group(1) or reference.group(2), ""))
    elif post == "tunnel":
        emit("隧道名称", r"([一-鿿]{2,12}隧道)(?=的设计提纲|帮忙)")
        segment = re.search(r"((?:进口|洞身|出口)段)\s*("+_RANGE+r")", text)
        if segment:
            p += [("_segment", segment.group(1), ""), ("隧道名称", segment.group(), "自然对象")]
        emit("工程类型", r"(公路隧道单洞双车道)")
        number("隧道长度", r"(?:全长|区间长度)")
        number("埋深", r"(?<!水位)(?<!地下水)(?:最大)?埋深")
        emit("围岩分级", r"([IVX]+级|[一二三四五六]级)围岩")
        if "勘察报告" in text and any(k == "围岩分级" for k, _, _ in p):
            p.append(("围岩来源", "用户口述勘察报告（未附原文）", "自然字段"))
        emit("工法", r"用(台阶法)开挖")
        method = re.search(r"准备上(CRD法)|(全断面)开挖", text)
        if method:
            p.append(("工法", method.group(1) or method.group(2), "自然字段"))
        emit("防水等级", r"防水等级(?:按|是)?(.+?)考虑")
        emit("地下水", r"地下水是(.+)$")
        emit("超前支护资料", r"超前用(.+)$")
        emit("偏压资料", r"(偏压)")
        emit("洞口条件", r"^(雨季洞口有渗水)$")
        emit("周边建筑", r"^(洞顶上方没有建筑物)$")
    elif post == "traffic":
        emit("研究名称", r"([一-鿿]{2,12}(?:大道|路|街)-[一-鿿]{2,12}(?:大道|路|街)(?:交叉口|路口)?)")
        if re.search(r"施工期交通导改|导改仿真", text):
            p.append(("研究类型", "施工导改", "自然字段"))
        elif "交通影响评价" in text:
            p.append(("研究类型", "TIA", "自然字段"))
        emit("导改阶段", r"(第[一二三四五六\d]+阶段.+)$")
        emit("调查日期", r"流量是\s*(\d{4}-\d{1,2}-\d{1,2})")
        emit("调查时段", r"("+_TIME+r")")
        for movement in re.finditer(r"([东西南北]进口(?:直行|左转|右转))\s*[:：]?\s*(?:大概|约)?\s*("+_NUMBER+r"\s*(?:pcu/h|辆/小时|辆)?)", text, re.I):
            p += [("方向车型", movement.group(1), "自然字段"), ("交通量", movement.group(2), "自然字段")]
        emit("流量文件", r"(?:原始数据在|数据在)(.+\.(?:xlsx|csv|xls))")
        emit("现状信号周期", r"(?:现在|现状)?信号周期\s*("+_NUMBER+r"\s*秒)")
        emit("仿真软件", r"(?:用|使用)(VISSIM|SUMO)(?:做|$)")
        if "调查表还没" in text:
            p.append(("流量文件", UNKNOWN, "自然字段"))
        if "配时也没拿到" in text:
            p.append(("配时", UNKNOWN, "自然字段"))
    elif post == "design-coord":
        issue = re.search(r"^(?:一个是|另一个是)(.+)$", text)
        if issue:
            p += [("_new", "", ""), ("问题位置", issue.group(1), "自然字段")]
        emit("会议时间", r"("+_DATE+r"(?:下午|上午)?)在")
        emit("会议地点", r"在(.+?)开的")
        emit("参加人员", r"^(设计院.+?和监理.+?)都在$")
        emit("图号", r"(?:暖施|结施|建施|水施)("+_CODE+r")")
        emit("图纸版本", r"的(V\d+)版")
        emit("问题编号", r"问题编号(?:记|是|为)?\s*("+_CODE+r")")
        emit("责任专业", r"责任专业定(.+)$")
        emit("完成时限", r"(?:答应|说|老周)("+_DATE+r")前")
        emit("责任人", r"设计院(.+?工)说")
        emit("责任人", r"机电的(.+?)(?=\d{1,2}月)")
        emit("问题描述", r"(?:画的|写的是)(降\s*"+_NUMBER+r")")
        emit("问题描述", r"(净高只剩\s*"+_NUMBER+r")")
        if "排烟风管" in text and "碰了" in text:
            p.append(("问题描述", "排烟风管与框架梁碰撞", "自然字段"))
        if "月底前出变更单" in text:
            p += [("变更需求", "月底前出变更单", "自然字段"), ("完成时限", "月底", "自然字段")]
        emit("提资内容", r"拿个(.+?)出来")
    if _UNKNOWN.search(text):
        p.append(("待核记录", text, "自然字段"))
    return p


def parse(post, text):
    profile = PROFILES[post]
    identity = profile.identity.split("|", 1)[0]
    aliases = _aliases(post)
    label = re.compile(r"(?<![A-Za-z0-9_])("+"|".join(map(re.escape, sorted(aliases, key=len, reverse=True)))+r")\s*[:：=]\s*", re.I)
    bare = re.compile(r"^("+"|".join(map(re.escape, sorted(aliases, key=len, reverse=True)))+r")\s+(.+)$", re.I)
    records, contexts = [], []
    scope = {"总体辖区": UNKNOWN}
    contexts.append(scope)
    current, file_id, origin = None, 0, ""
    tunnel_name = ""

    def scope_id():
        return next(i for i, row in enumerate(contexts) if row is scope)

    def new():
        nonlocal current
        current = {"_context": scope_id(), "_file": file_id, "_origin": origin, "_sources": {}}
        records.append(current)
        return current

    def put(row, key, raw, position):
        value = _typed(key, raw)
        if key in _NUMERIC | _MATERIAL_FIELDS and value == UNKNOWN and _clean(raw) != UNKNOWN:
            row["待核记录"] = "；".join(filter(None, (row.get("待核记录"), key+"："+_clean(raw))))
        if value == UNKNOWN:
            previous = row.get(key)
            if previous not in {None, UNKNOWN}:
                row["待核记录"] = "；".join(filter(None, (row.get("待核记录"), key+"原用户值："+previous+"；本次未定，待核")))
            row[key] = UNKNOWN
            row.setdefault("_sources", {}).pop(key, None)
        elif row.get(key) in {None, UNKNOWN}:
            row[key] = value
        elif value not in row[key].split("；"):
            row[key] += "；"+value
        if value != UNKNOWN:
            sources = row.setdefault("_sources", {})
            previous = sources.get(key)
            sources[key] = position if not previous else "；".join(dict.fromkeys((previous, position)))

    def observation(row, key, value, position):
        """Repeated movements/pipes are child rows, not parallel joined strings."""
        if post == "traffic" and key in FLOW_FIELDS:
            children = row.setdefault("_traffic_rows", [])
            child = children[-1] if children else None
            period_change = key in {"调查日期", "调查时段"} and child and any(k in child for k in ("方向车型", "交通量"))
            movement_change = key == "方向车型" and child and "交通量" in child
            repeated_flow = key == "交通量" and child and "交通量" in child and _typed(key, value) != UNKNOWN
            if child is None or period_change or movement_change or repeated_flow:
                # Only an explicitly shared survey period follows the next
                # movement. Files, signals and vehicle classes never do.
                shared = {} if period_change or child is None else {
                    k: child[k] for k in ("调查日期", "调查时段") if k in child}
                sources = {} if child is None else {
                    k: child.get("_sources", {}).get(k, UNKNOWN) for k in shared}
                child = {**shared, "_sources": sources}
                children.append(child)
            put(child, key, value, position)
        elif post == "municipal" and key in PIPE_FIELDS:
            children = row.setdefault("_pipe_rows", [])
            child = children[-1] if children else None
            repeated_pipe = child and key in {"管线名称", "现状管线", "拟建管线"} and key in child
            changed_kind = child and key in {"现状管线", "拟建管线"} and ("拟建管线" if key == "现状管线" else "现状管线") in child
            if child is None or repeated_pipe or changed_kind:
                child = {"_sources": {}}
                children.append(child)
            put(child, key, value, position)
        put(row, key, value, position)

    def scan(clause):
        clause = re.sub(r"^(?:本次实际情况|实际情况|真实记录)\s*[:：]\s*", "", clause)
        if post == "tunnel":
            water = re.fullmatch(r"(?:地下)?水位埋深\s*[:：=]?\s*(.+)", clause)
            if water:
                return [("地下水", "水位埋深"+water.group(1), "自然字段")]
        matches = list(label.finditer(clause))
        if matches:
            return [(aliases[m.group(1).casefold()], clause[m.end():matches[i+1].start() if i+1 < len(matches) else len(clause)], m.group(1)) for i, m in enumerate(matches)]
        match = bare.match(clause)
        if match:
            return [(aliases[match.group(1).casefold()], match.group(2), match.group(1))]
        return _natural(clause, post)

    def lines():
        header, offset = None, 0
        for line in _literal_source(text).splitlines(keepends=True):
            stripped = line.strip()
            if _source_name(stripped) is not None or stripped.startswith(("【本轮用户要求", "【此前用户资料")):
                header = None
                yield "marker", stripped, offset
            elif stripped.startswith("|"):
                cells = [html.unescape(c).strip() for c in _table_cells(stripped)]
                if any(cells) and all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                    pass
                elif header is not None:
                    yield "row", [(aliases[h.casefold()], cells[i] if i < len(cells) else "", h) for i, h in enumerate(header) if h.casefold() in aliases], offset
                elif len(cells) >= 2 and cells[0].casefold() in aliases and cells[1] and cells[1].casefold() not in aliases and not any(cells[2:]):
                    yield "line", [(aliases[cells[0].casefold()], cells[1], cells[0])], offset
                elif any(c.casefold() in aliases for c in cells):
                    header = cells
            else:
                if header is not None:
                    yield "end_table", [], offset
                    header = None
                pairs = [pair for clause, _, _, _ in _inputs(line) if clause is not None for pair in scan(clause)]
                yield "line", pairs, offset
            offset += len(line)

    for kind, pairs, offset in lines():
        position = (origin+" · " if origin else "")+f"输入第{text[:offset].count(chr(10))+1}行（非原件页码）"
        if kind == "marker":
            origin = _source_name(pairs) or pairs.strip("【】")
            file_id += 1
            scope = {"总体辖区": UNKNOWN, "_origin": origin}
            contexts.append(scope)
            current, tunnel_name = None, ""
            continue
        if kind == "end_table":
            current = None
            continue
        if not pairs:
            continue
        if current is None and all(k == "待核记录" for k, _, _ in pairs):
            continue
        if kind == "row":
            current = None
            order = {"项目名称": 0, "设计阶段": 1, "总体辖区": 2, identity: 3}
            if post == "traffic":
                order.update({key: i+4 for i, key in enumerate(("调查日期", "调查时段", "方向车型", "用户车型", "交通量", "流量文件", "调查来源", "现状信号周期", "配时"))})
            elif post == "municipal":
                order.update({key: i+4 for i, key in enumerate(("管线名称", "现状管线", "拟建管线", "管径", "交叉位置", "管线权属单位", "管线来源"))})
            pairs.sort(key=lambda p: order.get(p[0], 30))
        local = []
        for key, value, label_name in pairs:
            is_scope = key in _META or key in _BASIS and current is None and not any(k == identity for k, _, _ in pairs)
            if post == "traffic" and key == "研究类型" and current is None and not any(k == identity for k, _, _ in pairs):
                is_scope = True
            if key == "辖区" and current is None and not any(k == identity for k, _, _ in pairs):
                key, is_scope = "总体辖区", True
            if is_scope:
                if key in {"项目名称", "设计阶段"} and (scope.get(key) not in {None, UNKNOWN, _typed(key, value)} or current is not None and scope.get(key) in {None, UNKNOWN}):
                    scope = {"总体辖区": UNKNOWN, "_origin": origin} if key == "项目名称" else {**scope, "_sources": dict(scope.get("_sources", {}))}
                    scope.pop(key, None)
                    scope.get("_sources", {}).pop(key, None)
                    contexts.append(scope)
                    current = None
                put(scope, key, value, position)
            else:
                local.append((key, value, label_name))
        if not local:
            continue
        ids = [i for i, (k, _, _) in enumerate(local) if k == identity]
        if kind == "row":
            chunks = [local]
            new()
        elif len(ids) > 1:
            if ids[0] == 0 or local[ids[0]][2].startswith("自然"):
                chunks = [local[s:e] for s, e in zip([0]+ids[1:], ids[1:]+[len(local)])]
            else:
                starts = [0]+[i+1 for i in ids[:-1]]
                chunks = [local[s:e+1] for s, e in zip(starts, ids)]
                chunks[-1].extend(local[ids[-1]+1:])
        else:
            chunks = [local]
        for index, chunk in enumerate(chunks):
            if kind != "row" and (index or any(k == identity for k, _, _ in chunk) and current is not None and current.get(identity) not in {None, UNKNOWN}):
                current = None
            for key, value, _ in chunk:
                if key == "_new":
                    current = None
                    continue
                if key == "_reference":
                    candidates = [r for r in records if r.get("_file") == file_id and r.get("_context") == scope_id()
                                  and re.match(re.escape(value)+r"(?:泊位|\b)", r.get(identity, ""))]
                    current = candidates[0] if len(candidates) == 1 else None
                    continue
                if key == "_segment":
                    if current is not None and current.get(identity) and not any(k in current for k in ("埋深", "围岩分级", "工法", "隧道长度")):
                        tunnel_name = current[identity]
                        current["隧道总体名称"] = tunnel_name
                        current["_container"] = True
                        current.pop(identity)
                    else:
                        current = None
                    continue
                if key.startswith("_"):
                    continue
                if current is None:
                    new()
                    if tunnel_name:
                        current["隧道总体名称"] = tunnel_name
                observation(current, key, value, position)
    merged = []
    for row in records or [{"_context": len(contexts)-1, "_origin": origin, "_sources": {}}]:
        if row.get("_container"):
            continue
        context = contexts[row["_context"]]
        inherited = {k: v for k, v in context.items() if not k.startswith("_") and k != "总体辖区"}
        local_zone = row.get("辖区", UNKNOWN)
        if "辖区" in row and local_zone != context.get("总体辖区", UNKNOWN):
            inherited.pop("适用依据", None)
            inherited.pop("依据版本", None)
        sources = {k: v for k, v in context.get("_sources", {}).items() if k in inherited and k not in row}
        sources.update(row.get("_sources", {}))
        merged.append({**inherited, **row, "_sources": sources, "_project_zone": context.get("总体辖区", UNKNOWN), "_context_data": context})
    return merged
