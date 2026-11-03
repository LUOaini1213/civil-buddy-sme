"""Literal environmental, notice and survey records; no approval or solver."""
from __future__ import annotations

import html
import re

from packing_assistant.post_drafts.design_observations import _breaks
from packing_assistant.post_drafts.hr_observations import _literal_hr_lines
from packing_assistant.post_drafts.procurement import _source_name
from packing_assistant.post_drafts.business_observations import cell, table, val, support
from packing_assistant.word_export import _table_cells

UNKNOWN = "UNSPECIFIED"
TOOLS = {"env": "env__list", "supervision": "supervision__reply", "survey": "survey__record"}
DISCLAIMER = ("AI 草稿 · 内部讨论。本文件只整理用户资料，未核验现场、来文、许可、仪器或原始观测。"
              "不构成监理指令、验收依据、排污许可、测量成果签认件或竣工测量报告。"
              "未进行排放判定、整改复查、测量平差或精度检核，须由具备相应资格的人员复核签认。")
COMMON = {
    "project": ("项目名称", "工程名称", "项目", "工程", "Project"), "stage": ("资料阶段", "阶段"),
    "task": ("任务编号", "任务", "Task"), "project_zone": ("总体辖区", "项目辖区", "全局辖区"),
    "zone": ("辖区", "国家地区", "Jurisdiction"), "location": ("部位", "位置", "工点", "Location"),
    "version": ("版本", "文件版本"), "source": ("资料来源", "来源", "来源文件", "Source"),
    "basis": ("依据", "适用依据", "要求来源"), "basis_version": ("依据版本",), "basis_source": ("依据来源",),
    "CN_basis": ("CN依据",), "SG_basis": ("SG依据",), "EU_basis": ("EU依据",),
    "pending": ("待补资料", "缺项", "待核项"), "date": ("日期", "记录日期", "Date"),
}
FIELDS = {
    "env": {
        "activity": ("部位活动", "施工活动", "活动", "Activity"), "id": ("活动编号", "清单编号"),
        "district": ("属地", "区县", "行政区"), "pollutant": ("污染源", "分类", "废物类别", "Pollutant"),
        "quantity": ("用户数量", "数量", "产生量", "外运量"), "unit": ("单位", "Unit"),
        "estimated_quantity": ("用户估计量", "估计量", "计划数量"), "estimated_unit": ("估计量单位",),
        "quantity_source": ("数量来源", "运输台账"), "dust": ("扬尘", "降尘措施"),
        "enclosure": ("围挡高度", "围挡"), "washing": ("洗车台", "冲洗措施"),
        "wastewater": ("污水", "泥浆措施", "污水措施", "处理措施"), "outlet": ("排放口", "排水去向"),
        "carrier": ("运输单位", "渣土运输", "承运单位"), "carrier_source": ("运输资质来源",),
        "destination": ("消纳去向", "消纳场", "接收单位", "去向"), "destination_source": ("去向证明",),
        "manifest": ("联单号", "联单编号", "联单", "台账编号"), "storage": ("暂存点", "分类堆放"),
        "night": ("夜间作业", "夜间施工", "运输时段", "出场时段"), "night_date": ("夜间日期",),
        "noise": ("噪声措施",), "monitor_value": ("用户监测值", "监测值"), "monitor_source": ("监测来源",),
        "limit_input": ("用户限值", "限值"), "limit_source": ("限值来源",),
        "permit_record": ("用户许可材料", "许可记录", "报批记录"), "permit_source": ("许可来源",),
        "inspector": ("检查人", "环保员", "责任方", "责任人"), "traffic": ("场容交通", "市容", "交通措施"),
        "evidence": ("证据", "附件", "影像资料"), "action": ("拟办", "整改措施"),
        "review_record": ("用户复查资料", "复查资料"), "escalation": ("停工升级", "升级接口"),
        "commercial": ("商务接口", "措施费资料"),
    },
    "supervision": {
        "id": ("来文编号", "来文号", "通知单编号", "单号", "编号", "Notice ID"),
        "issue": ("来文条目", "来文问题", "问题", "来文要点", "事项", "条目", "目录条目", "Issue"),
        "notice_date": ("来文日期", "发文日期", "收文日期"), "notice_source": ("通知原文来源", "来文来源"),
        "kind": ("来文类型", "文种"), "recipient": ("致", "监理机构", "收文机构"), "cc": ("抄送",),
        "facts": ("用户事实", "事实原文", "整改事实"), "cause": ("原因", "原因分析"),
        "action": ("拟办", "拟办措施", "整改措施"), "implementation": ("用户实施记录", "实施记录"),
        "owner": ("责任人", "责任方", "负责人"), "deadline": ("完成时限", "整改期限", "期限", "Deadline"),
        "deadline_source": ("期限来源",), "evidence": ("附件", "证据", "证据目录"),
        "review_record": ("用户复查记录", "复查记录"), "reply_record": ("用户回复记录", "回复记录"),
        "reply_date": ("用户实际回复日", "实际回复日"), "reply_id": ("回复编号",),
        "thickness": ("用户厚度", "实测厚度"), "measurement_source": ("检测来源", "实测来源"),
        "prevention": ("预防拟办",), "category": ("目录类别", "资料类别", "分部"),
        "archive_status": ("用户目录状态", "资料状态"), "archive_owner": ("资料责任方",),
        "handover": ("用户移交资料", "移交要求"),
    },
    "survey": {
        "point": ("点号", "点名", "控制点", "水准点", "Point"), "record_id": ("记录编号",),
        "station": ("测站", "设站", "Station"), "backsight": ("后视", "后视点", "Backsight"),
        "foresight": ("前视", "前视点"), "x": ("X", "北坐标", "北向坐标", "北向", "纵坐标"),
        "y": ("Y", "东坐标", "东向坐标", "东向", "横坐标"), "h": ("H", "高程", "标高"),
        "original_h": ("原高程", "交桩高程", "原始高程"), "observed_h": ("复测高程", "实测高程", "本次高程"),
        "coordinate_system": ("坐标系", "平面基准", "坐标基准"), "height_datum": ("高程系", "高程基准", "水准基面"),
        "unit": ("单位", "坐标单位", "Unit"), "height_unit": ("高程单位",),
        "coordinate_source": ("坐标来源", "控制点来源"), "original_source": ("原高程来源", "交桩来源"),
        "observation_source": ("观测来源", "复测来源"), "drawing": ("图号", "图纸", "放样图号"),
        "instrument": ("仪器", "设备", "仪器型号", "Instrument"), "instrument_id": ("仪器编号", "机身号", "编号"),
        "calibration_until": ("检定有效期", "校准有效期"), "calibration_source": ("检定来源", "检定证"),
        "observer": ("观测", "观测人", "测量员", "测量人", "司镜", "Observer"),
        "reviewer": ("复核", "复核人", "复测人", "Reviewer"), "recorder": ("记录人",),
        "network": ("控制网", "加密方法"), "setting_out": ("放样内容", "放样任务"), "vertical": ("竖向传递",),
        "observation": ("原始观测", "观测原文"), "user_difference": ("用户差值", "用户闭合差"),
        "tolerance_input": ("用户限差", "限差"), "tolerance_source": ("限差来源",),
        "monitoring": ("监测资料",), "anomaly": ("异常", "停测原因", "备注"), "handover": ("交接资料",),
        "task_instrument": ("任务共用仪器",), "task_observer": ("任务共用观测人",),
        "task_coordinate_system": ("任务坐标系",), "task_height_datum": ("任务高程基准",),
        "task_unit": ("任务坐标单位",), "task_height_unit": ("任务高程单位",),
    },
}
IDENTITY = {"env": ("activity", "location", "id"), "supervision": ("issue", "id"), "survey": ("point", "station", "record_id")}
META = {"project", "stage", "task", "project_zone"}
BASES = {"basis", "basis_version", "basis_source", "CN_basis", "SG_basis", "EU_basis"}
PARENT = {"id", "notice_date", "notice_source", "recipient", "kind"}
SURVEY_SHARED = {"task_instrument": "instrument", "task_observer": "observer", "task_coordinate_system": "coordinate_system",
                 "task_height_datum": "height_datum", "task_unit": "unit", "task_height_unit": "height_unit"}
NUM = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
UNIT = r"(?:m[³3²2]|米|方|立方米|吨|t|kg|车|台|mm|毫米|m)"
SURVEY_VALUE = NUM + r"(?:\s*(?:mm|毫米|m|米)(?![A-Za-z]))?"
DATE = r"(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}月\d{1,2}[号日]|昨天下午|昨天|今天)"
MISSING = re.compile(r"未提供|未给|未定|未核|待定|待填|待核|待补|未知|还没|没定|没写|没带|没有|没人|拿不准|UNSPECIFIED|TBD|N/A", re.I)
UNSAFE = re.compile(r"比如|例如|假设|如果|自行计算|自己算|不采用|是否|[？?]")
FACTS = {"quantity", "estimated_quantity", "enclosure", "monitor_value", "limit_input", "id", "date", "night_date",
         "inspector", "notice_date", "deadline", "owner", "thickness", "reply_date", "point", "station", "backsight", "foresight",
         "x", "y", "h", "original_h", "observed_h", "instrument_id", "calibration_until", "observer", "reviewer", "recorder", "user_difference", "tolerance_input"}
SOURCES = {"source", "basis_source", "quantity_source", "carrier_source", "destination_source", "monitor_source", "limit_source", "permit_source",
           "notice_source", "deadline_source", "measurement_source", "coordinate_source", "original_source", "observation_source", "calibration_source", "tolerance_source"}


def _typed(key, raw):
    value = raw.strip(" \t\r\n，,；。")
    if not re.search(r"&(?:#\d+|#x[\da-f]+|[a-z][\da-z]+);$", value, re.I):
        value = value.rstrip(";")
    if key in {"zone", "project_zone"}:
        return {"CN": "CN", "SG": "SG", "EU": "EU", "DUAL": "DUAL", "中国": "CN", "新加坡": "SG", "CN/SG": "DUAL", "SG/CN": "DUAL"}.get(value.upper(), UNKNOWN)
    if not value or MISSING.fullmatch(value):
        return UNKNOWN
    if key in FACTS | SOURCES and (MISSING.search(value) or UNSAFE.search(value)):
        return UNKNOWN
    if key in SOURCES and re.search(r"拟提供|计划提供|将提供|预计提供", value):
        return UNKNOWN
    if key in {"x", "y", "h", "original_h", "observed_h", "quantity", "thickness", "enclosure"} and re.search(r"大概|估计|预计|估算|猜|模拟|编个", value):
        return UNKNOWN
    return value


def _natural(clause, post):
    if UNSAFE.search(clause):
        return []
    pairs = []
    def emit(key, pattern, group=1):
        match = re.search(pattern, clause, re.I)
        if match:
            pairs.append((key, match.group(group)))
    if re.fullmatch(r"CN|SG|EU|DUAL|中国|新加坡|CN/SG|SG/CN", clause, re.I):
        return [("zone", clause)]
    if post == "env":
        emit("activity", r"^(?:.*?[:：])?(?:一个是|另一个是)(.+)$")
        emit("district", r"^(.+?[区县])这边$")
        emit("location", r"(\d+#(?:楼(?:基坑|地下室)?|地块))")
        emit("pollutant", r"(桩基泥浆|混凝土块|建筑垃圾|渣土|土方)")
        emit("date", "("+DATE+r")起外运")
        emit("quantity", r"一天出("+NUM+r"\s*"+UNIT+")")
        emit("estimated_quantity", r"((?:一天)?(?:大概|估计|预计)\s*"+NUM+r"\s*"+UNIT+r")")
        emit("destination", r"拉到(.+?)(?:$|去了)")
        emit("washing", r"(洗车台)")
        emit("wastewater", r"((?:现在排)?沉淀池.*)$")
        emit("enclosure", r"围挡高("+NUM+r"\s*(?:米|m))")
        emit("inspector", r"环保员([一-鿿]{2,4})(?=让|$)")
        emit("carrier", r"找的(.+)$")
        emit("manifest", r"联单(?:号|编号)\s*([A-Za-z\d/_-]+)")
        emit("night", r"(夜间出场)")
        emit("permit_record", r"(已报.+)$")
        if re.search(r"哪个厂|去向|接收", clause) and MISSING.search(clause):
            pairs.append(("destination", UNKNOWN))
    elif post == "supervision":
        if re.search(r"^(?:一张说|另一张|另一份)", clause):
            pairs.append(("_new_notice", "1"))
        emit("id", r"((?:JL|ZJJL|NCR|TZ)[A-Za-z\d]*(?:[-_][A-Za-z\d]+)+)")
        emit("notice_date", "("+DATE+r")(?:发的|下发)")
        emit("location", r"(\d+#楼[一二三四五六七八九十\d]+层(?:剪力墙|框架柱|梁板)|[东西南北]侧外脚手架)")
        emit("issue", r"(模板拼缝跑浆|保护层偏薄|连墙件缺了[一二三四五六七八九十\d]+处)")
        emit("implementation", r"我们已经(.+)$")
        emit("action", r"我们(?:已经|打算)(.+)$")
        emit("implementation", r"^(钢管.+(?:已经|已).+)$")
        emit("owner", r"让([一-鿿]{2,4})工长盯着")
        emit("owner", r"^([一-鿿]{2,4})负责$")
        emit("deadline", "("+DATE+r")前(?:整改完|交|完成)")
        emit("evidence", r"^附(.+)$")
        emit("cc", r"抄送(.+)$")
        emit("kind", r"(暂停令|复工报审|停工令)")
        if re.search(r"厚度|期限", clause) and MISSING.search(clause):
            pairs.append(("thickness" if "厚度" in clause else "deadline", UNKNOWN))
    else:
        if re.search(r"两个(?:水准点|控制点|测点)", clause):
            pairs.append(("_point_group", "1"))
        emit("location", r"^(\d+#楼.+?)(?:之前|要先|放线)")
        emit("point", r"(?<![A-Za-z\d])((?:GPS|BM|CP|PT|TP|GC|SP|JM)[-_]?\d+[A-Za-z]?)(?:点|交桩|单子|\s|$)")
        emit("x", r"(?:北(?:向)?|X)\s*[=:：]?\s*("+SURVEY_VALUE+")")
        emit("y", r"(?:东(?:向)?|Y)\s*[=:：]?\s*("+SURVEY_VALUE+")")
        emit("h", r"(?<![原上])(?<!原始)高程\s*("+SURVEY_VALUE+")")
        emit("original_h", r"(?:交桩单上高程|单子上是|原(?:始)?高程)\s*("+SURVEY_VALUE+")")
        emit("observed_h", r"(?:我们测出来|这次测的)\s*("+SURVEY_VALUE+")")
        emit("coordinate_system", r"坐标系(?:是|用的)?([A-Za-z][A-Za-z\d/_-]+)")
        emit("height_datum", r"高程用的(.+)$")
        emit("instrument", r"用的(.+?(?:水准仪|全站仪|GNSS))")
        emit("instrument_id", r"^编号\s*([A-Za-z\d/_-]+)$")
        emit("observer", r"(?:测量员|观测)([一-鿿]{2,4})(?=$|负责)")
        emit("anomaly", r"((?:怀疑|差得|没啥变化).+)$")
        if re.search(r"复核|检定证", clause) and MISSING.search(clause):
            pairs.append(("reviewer" if "复核" in clause else "calibration_source", UNKNOWN))
    return pairs


def parse_records(text, post):
    aliases = {a.casefold(): k for k, values in {**COMMON, **FIELDS[post]}.items() for a in values}
    label = re.compile(r"(?<![\w])("+"|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True))+r")\s*[:：=]\s*", re.I)
    scope = {"project_zone": UNKNOWN, "_sources": {}}
    contexts, groups, records = [scope], {}, []
    active, header, origin, file_id, notice = None, None, "", 0, {}
    def cid():
        return next(i for i, c in enumerate(contexts) if c is scope)
    def new():
        nonlocal active
        active = {"_context": cid(), "_file": file_id, "_origin": origin, "_sources": {}, "_quantities": [],
                  "_notice": dict(notice), "_notice_sources": dict(notice.get("_sources", {}))}
        records.append(active)
    def put(row, key, raw, position):
        value = _typed(key, raw)
        previous = row.get(key)
        if value == UNKNOWN:
            if previous not in {None, UNKNOWN}:
                row["pending"] = "；".join(filter(None, (row.get("pending"), key+"原值待核："+previous)))
            row[key] = UNKNOWN
            row["_sources"].pop(key, None)
            if raw.strip() not in {"", UNKNOWN}:
                row["pending"] = "；".join(filter(None, (row.get("pending"), key+"："+raw.strip())))
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
            active, header, notice = None, None, {}
            continue
        position = (origin+" · " if origin else "")+f"输入第{line_number}行（非原件页码）"
        chunks = []
        if stripped.startswith("|"):
            cells = [html.unescape(c).strip() for c in _table_cells(stripped)]
            if any(cells) and all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            if cells and cells[0].casefold() in aliases and sum(c.casefold() in aliases for c in cells) >= 2 and all(not c or c.casefold() in aliases for c in cells):
                header, active, notice = cells, None, {}
                continue
            if header is not None:
                active, notice = None, {}
                scope = {**scope, "_sources": dict(scope["_sources"])}
                contexts.append(scope)
                pairs = [(aliases[h.casefold()], cells[i] if i < len(cells) else "") for i, h in enumerate(header) if h.casefold() in aliases]
                pairs.sort(key=lambda p: {"project": 0, "stage": 1, "task": 2, "project_zone": 3}.get(p[0], 4))
                chunks = [(pairs, True, False)]
            elif len(cells) >= 2 and cells[0].casefold() in aliases and cells[1].casefold() not in aliases and not any(cells[2:]):
                chunks = [([(aliases[cells[0].casefold()], cells[1])], False, False)]
        else:
            if header is not None:
                active, header, notice = None, None, {}
            cursor = 0
            continuation = None
            for boundary in [*_breaks(line), None]:
                end = boundary.start() if boundary else len(line)
                clause = line[cursor:end].strip()
                cursor = boundary.end() if boundary else len(line)
                if not clause:
                    continue
                matches = list(label.finditer(clause))
                if matches:
                    pairs = _natural(clause[:matches[0].start()], post)
                    pairs += [(aliases[m.group(1).casefold()], clause[m.end():matches[i+1].start() if i+1 < len(matches) else len(clause)]) for i, m in enumerate(matches)]
                    continuation = pairs[-1][0]
                else:
                    pairs = _natural(clause, post)
                    if not pairs and continuation in {"action", "facts", "evidence", "dust", "night", "wastewater", "anomaly", "observation"} and re.match(r"^(?:再|并|同时|以及|还要|然后|另附)", clause):
                        pairs = [(continuation, clause)]
                chunks.append((pairs, False, not matches))
        for pairs, matrix, natural in chunks:
            if matrix and not pairs:
                new()
            for key, raw in pairs:
                if key == "_new_notice":
                    active, notice = None, {}
                    continue
                if key == "_point_group":
                    scope = {k: v for k, v in scope.items() if k in META | BASES or k == "_sources"}
                    scope["_sources"] = {k: v for k, v in scope["_sources"].items() if k in META | BASES}
                    contexts.append(scope)
                    active = None
                    groups[cid()] = {"_sources": {}}
                    continue
                if post == "env" and key == "zone" and _typed(key, raw) == UNKNOWN and not MISSING.search(raw):
                    key = "district"
                scoped = key in META or key in BASES and active is None and not any(k in IDENTITY[post] for k, _ in pairs)
                if post == "survey" and key in {"location", "coordinate_system", "height_datum", "unit", "height_unit"} and active is None and not any(k in IDENTITY[post] for k, _ in pairs):
                    scoped = True
                if key in SURVEY_SHARED and post == "survey":
                    shared = groups.setdefault(cid(), {"_sources": {}})
                    put(shared, SURVEY_SHARED[key], raw, position)
                    continue
                if post == "survey" and natural and key in {"instrument", "observer"} and cid() in groups:
                    put(groups[cid()], key, raw, position)
                    continue
                if scoped:
                    populated = active is not None and any(k in IDENTITY[post] for k in active)
                    if scope.get(key) not in {None, UNKNOWN, _typed(key, raw)} or populated and key in META and scope.get(key) in {None, UNKNOWN}:
                        scope = {"project_zone": UNKNOWN, "_sources": {}} if key == "project" else {**scope, "_sources": dict(scope["_sources"])}
                        if key in {"task", "stage"} and post == "survey":
                            scope = {k: v for k, v in scope.items() if k in META | BASES or k == "_sources"}
                            scope["_sources"] = {k: v for k, v in scope["_sources"].items() if k in META | BASES}
                        scope.pop(key, None)
                        scope["_sources"].pop(key, None)
                        contexts.append(scope)
                        active, notice = None, {}
                    put(scope, key, raw, position)
                    continue
                if post == "supervision" and not matrix and key == "id" and _typed(key, raw) != UNKNOWN:
                    if active is not None and active.get("id", active["_notice"].get("id", UNKNOWN)) != UNKNOWN:
                        active, notice = None, {}
                    if active is None or not any(k in active for k in ("issue", "location")):
                        notice.setdefault("_sources", {})
                        put(notice, key, raw, position)
                if post == "supervision" and key in PARENT and _typed(key, raw) == UNKNOWN and active is not None:
                    notice.pop(key, None)
                    notice.get("_sources", {}).pop(key, None)
                if not matrix and key in IDENTITY[post] and _typed(key, raw) != UNKNOWN and active is not None and active.get(key) not in {None, UNKNOWN}:
                    active = None
                if active is None:
                    new()
                if post == "supervision" and key in PARENT and "issue" not in active and "location" not in active and not matrix:
                    notice.setdefault("_sources", {})
                    put(notice, key, raw, position)
                    active["_notice"] = dict(notice)
                    active["_notice_sources"] = dict(notice["_sources"])
                put(active, key, raw, position)
                if post == "env" and key == "activity":
                    for subkey, value in _natural(raw, post):
                        if subkey != "activity" and subkey not in active and not any(k == subkey for k, _ in pairs):
                            put(active, subkey, value, position)
                if post == "env" and key == "night":
                    date = re.search(DATE, raw)
                    if date:
                        put(active, "night_date", date.group(), position)
                if post == "env" and key in {"quantity", "estimated_quantity"}:
                    if key == "quantity" and re.search(r"大概|估计|预计|估算", raw):
                        put(active, "estimated_quantity", raw, position)
                        if active.get("_derived_unit"):
                            active["unit"] = UNKNOWN
                            active["_sources"].pop("unit", None)
                        key = "estimated_quantity"
                    unit = re.search("("+UNIT+r")\s*$", raw)
                    unit_key = "estimated_unit" if key == "estimated_quantity" else "unit"
                    if val(active, key) != UNKNOWN and unit and active.get(unit_key) in {None, UNKNOWN}:
                        put(active, unit_key, unit.group(1), position)
                        active["_derived_"+unit_key] = True
                    elif val(active, key) == UNKNOWN and active.get("_derived_"+unit_key):
                        active[unit_key] = UNKNOWN
                        active["_sources"].pop(unit_key, None)
                if post == "env" and key in {"unit", "estimated_unit"}:
                    active["_derived_"+key] = False
                if post == "survey" and key == "instrument":
                    number = re.search(r"编号\s*([A-Za-z\d/_-]+)", raw)
                    if number:
                        put(active, "instrument_id", number.group(1), position)
            if matrix and active is None:
                new()
    result = []
    for row in records or [{"_context": cid(), "_file": file_id, "_origin": origin, "_sources": {}, "_quantities": [], "_notice": {}, "_notice_sources": {}}]:
        context = contexts[row["_context"]]
        inherited = {k: v for k, v in context.items() if not k.startswith("_") and k != "project_zone"}
        inherited.update({k: v for k, v in row["_notice"].items() if not k.startswith("_")})
        shared = groups.get(row["_context"], {})
        inherited.update({k: v for k, v in shared.items() if not k.startswith("_")})
        if "zone" in row and row["zone"] != context.get("project_zone", UNKNOWN):
            for key in ("basis", "basis_version", "basis_source"):
                inherited.pop(key, None)
        sources = {**context["_sources"], **row["_notice_sources"], **shared.get("_sources", {}), **row["_sources"]}
        sources = {k: v for k, v in sources.items() if row.get(k, inherited.get(k, UNKNOWN)) != UNKNOWN}
        result.append({**inherited, **row, "_sources": sources, "_project_zone": context.get("project_zone", UNKNOWN)})
    return result


def _zone(row):
    return row.get("zone", row.get("_project_zone", UNKNOWN))


def scoped(title, columns, rows, tail=()):
    headers = ("项目", "阶段", "辖区")+tuple(k for k, _ in columns)+tuple(k for k, _ in tail)
    values = [(val(r, "project"), val(r, "stage"), _zone(r))+tuple(val(r, key) for _, key in columns)+tuple(value for _, value in tail) for r in rows]
    return "## "+title+"\n\n"+table(headers, values)+"\n"


def _env(rows):
    lines = ["# 环保文明清单（AI 草稿 · 内部讨论）", "", DISCLAIMER, "", "submit_blocked=true", "",
        "## 1 封面与声明", "", "未盘点或检查现场；已有设施、监测值、估计量与报批主张均只是用户摘录，不作排放或许可结论。", "",
        scoped("活动与环保检查范围", (("部位活动", "activity"), ("产生部位", "location"), ("属地区县", "district"), ("污染源分类", "pollutant"), ("检查或外运日期", "date"), ("检查人/环保员（用户资料）", "inspector")), rows),
        scoped("2 扬尘与围挡", (("部位", "location"), ("用户扬尘措施", "dust"), ("用户围挡高度", "enclosure"), ("冲洗设施摘录", "washing")), rows),
        scoped("3 污水与泥浆", (("部位", "location"), ("用户污染源", "pollutant"), ("用户污水处理措施", "wastewater"), ("排放口及排水去向", "outlet")), rows),
        scoped("4 噪声与夜间", (("部位活动", "activity"), ("夜间作业/运输时段", "night"), ("用户夜间日期", "night_date"), ("用户噪声措施", "noise"), ("用户报批材料摘录", "permit_record"), ("许可来源", "permit_source")), rows, (("许可核验", UNKNOWN),)),
        scoped("5 弃土与建筑垃圾", (("产生部位", "location"), ("污染源分类", "pollutant"), ("用户产生/外运数量", "quantity"), ("数量单位", "unit"), ("用户估计运输/产生量（未核）", "estimated_quantity"), ("估计量单位", "estimated_unit"), ("数量台账来源", "quantity_source")), rows),
        scoped("运输与消纳证据", (("部位活动", "activity"), ("运输单位", "carrier"), ("资质来源", "carrier_source"), ("接收消纳去向", "destination"), ("去向证明", "destination_source"), ("联单号", "manifest"), ("暂存分类", "storage")), rows),
        scoped("6 场容交通与市容", (("部位", "location"), ("用户场容交通措施", "traffic"), ("商务接口资料", "commercial")), rows),
        scoped("7 检查与整改", (("部位活动", "activity"), ("用户监测值摘录", "monitor_value"), ("监测来源", "monitor_source"), ("用户限值摘录（未核）", "limit_input"), ("限值来源", "limit_source"), ("用户拟办", "action"), ("证据目录", "evidence")), rows, (("达标核验", UNKNOWN),)),
        scoped("停工升级接口", (("部位活动", "activity"), ("用户升级接口", "escalation"), ("用户复查材料", "review_record")), rows),
        "### 签认空栏", "", table(("活动序号", "环保员签字", "生产经理签字", "资料员签字", "日期"), [(str(i), "（空栏）", "（空栏）", "（空栏）", "（空栏）") for i, _ in enumerate(rows, 1)]), "",
        "[A001] 未给运输资质、去向证明、联单、监测原件或许可的格子待填。未计算处置量、限值、距离或费用；费用 TBD，属地指令与许可由人核对。", "", support(rows)]
    return "\n".join(lines)


def _supervision(rows, text):
    archive = bool(re.search(r"验收资料目录|组卷范围|组卷目录|生成.*资料目录", text))
    lines = ["# 监理通知回复草稿 / 资料目录（AI 草稿 · 内部讨论）", "", DISCLAIMER, "", "submit_blocked=true", "",
        "## 1 范围声明与封面", "", "资料目录或逐条回复仅供内部起草，不替监理发指令。来文未给不造通知，缺地方格式不套表号。", "",
        scoped("2 来文与文件控制", (("来文编号", "id"), ("来文日期", "notice_date"), ("来文类型", "kind"), ("来文原文来源", "notice_source"), ("致监理机构", "recipient"), ("抄送", "cc"), ("用户回复编号", "reply_id")), rows),
        scoped("3 来文要点复述与事实问题", (("来文条目", "issue"), ("部位", "location"), ("用户事实原文", "facts"), ("用户实施记录（未核）", "implementation"), ("用户实测厚度", "thickness"), ("实测来源", "measurement_source")), rows),
        scoped("4 原因待核", (("来文条目", "issue"), ("用户原因分析", "cause")), rows),
        scoped("5 拟办措施", (("来文条目", "issue"), ("部位", "location"), ("用户拟办/整改措施", "action"), ("用户责任人", "owner"), ("用户预防拟办", "prevention")), rows),
        "暂停令、停工令、复工报审只出目录和拟办提纲；不签发、不授权施工，不用普通通知回复替代法定流程。", "",
        scoped("6 期限与闭合台账", (("来文编号", "id"), ("来文条目", "issue"), ("用户完成时限", "deadline"), ("期限来源", "deadline_source"), ("用户实际回复日", "reply_date"), ("用户回复记录", "reply_record")), rows, (("闭合核验", UNKNOWN), ("复查意见", "（空栏）"))),
        scoped("7 证据目录", (("来文条目", "issue"), ("用户附件目录", "evidence"), ("用户复查记录摘录", "review_record"), ("资料来源", "source")), rows),
        "## 8 签发与复查空栏", "", table(("条目序号", "项目技术负责人签字", "质量负责人签字", "项目经理签发", "监理复查意见", "日期"), [(str(i), "（空栏）", "（空栏）", "（空栏）", "（空栏）", "（空栏）") for i, _ in enumerate(rows, 1)]), "",
        "本回复为 AI 草稿，待项目经理等持证人员审核签发后报出；未自检、签发或认定闭合，不补造培训、检测报告或意见。", "",
        "[A001] 未给来文、日期、原件、期限来源或实际回复记录的格子待填。", "", support(rows)]
    if archive:
        lines += ["", "### 组卷总目录（讨论分类，非属地法定目录）", "", table(("资料块", "用户目录", "核验结果"), [(k, UNKNOWN, UNKNOWN) for k in ("工程准备阶段文件", "监理文件", "施工文件", "竣工图", "竣工验收文件")]), "",
            scoped("施工过程子目录与用户资料", (("目录条目", "issue"), ("部位", "location"), ("用户类别/分部", "category"), ("用户资料状态", "archive_status"), ("资料责任方", "archive_owner"), ("来源", "source")), rows),
            "### 独立过程夹与闭合检查表", "", table(("目录接口", "对应记录", "复核结果"), [(k, UNKNOWN, UNKNOWN) for k in ("施工组织", "专项讨论提纲", "交底", "检查表", "影像", "验收")]), "",
            scoped("移交提示（要求原件未核）", (("目录条目", "issue"), ("用户移交要求或资料", "handover")), rows)]
    return "\n".join(lines)


def _survey(rows):
    lines = ["# 测量方案/记录表（AI 草稿 · 内部讨论）", "", DISCLAIMER, "", "submit_blocked=true", "",
        "## 1 封面与文件控制", "", scoped("任务范围与部位", (("任务", "task"), ("部位", "location"), ("用户记录日期", "date"), ("版本", "version"), ("资料来源", "source")), rows),
        "## 2 草稿与责任声明", "", "只转录用户给定点号和坐标高程。不连接平差、转换、放样或监测引擎，未作成果检核或合格判断。", "",
        "## 3 基准资料与已知起算", "", "不同坐标系、高程基准或单位分表；无明确基准与单位不借相邻点，不作数值比较或坐标转换。", ""]
    groups = {}
    for row in rows:
        key = (row["_file"], val(row, "project"), val(row, "stage"), val(row, "task"), _zone(row),
               val(row, "coordinate_system"), val(row, "height_datum"), val(row, "unit"), val(row, "height_unit"))
        groups.setdefault(key, []).append(row)
    for index, group in enumerate(groups.values(), 1):
        columns = (("任务", "task"), ("部位", "location"), ("点号", "point"), ("用户北向 X 坐标", "x"), ("用户东向 Y 坐标", "y"),
                   ("用户高程 H", "h"), ("坐标系", "coordinate_system"), ("高程基准", "height_datum"), ("坐标单位", "unit"), ("高程单位", "height_unit"), ("坐标来源", "coordinate_source"))
        lines += [scoped(f"控制点基准分组 {index}", columns, group)]
    lines += [scoped("4 控制网与加密", (("任务", "task"), ("点号", "point"), ("用户控制网方法", "network")), rows),
        scoped("5 放样内容", (("任务", "task"), ("部位", "location"), ("用户放样内容", "setting_out"), ("用户图号", "drawing"), ("测站", "station"), ("后视", "backsight"), ("前视", "foresight")), rows),
        scoped("6 竖向传递", (("任务", "task"), ("点号", "point"), ("用户竖向传递方法", "vertical")), rows),
        "## 7 复测与检核（未计算）", ""]
    for index, group in enumerate(groups.values(), 1):
        lines += [scoped(f"原高程与复测高程分组 {index}", (("点号", "point"), ("用户原高程", "original_h"), ("原高程来源", "original_source"), ("用户复测高程", "observed_h"), ("复测观测来源", "observation_source"), ("高程基准", "height_datum"), ("高程单位", "height_unit"), ("用户观测人", "observer")), group, (("本轮差值/闭合差", UNKNOWN), ("精度核验", UNKNOWN)))]
    lines += [scoped("观测手簿用户摘录", (("点号", "point"), ("原始观测", "observation"), ("用户差值摘录（未核）", "user_difference"), ("用户限差摘录", "tolerance_input"), ("限差来源", "tolerance_source")), rows),
        scoped("8 仪器检定与人员", (("点号", "point"), ("用户仪器型号", "instrument"), ("用户仪器编号", "instrument_id"), ("用户检定有效期", "calibration_until"), ("检定来源", "calibration_source"), ("用户观测人", "observer"), ("用户复核人", "reviewer"), ("用户记录人", "recorder")), rows, (("有效性核验", UNKNOWN),)),
        scoped("9 监测与停测异常", (("点号", "point"), ("用户监测资料", "monitoring"), ("用户异常描述", "anomaly")), rows),
        scoped("10 资料交接", (("点号", "point"), ("用户交接资料", "handover")), rows),
        "## 11 附录与签认空栏", "", table(("记录序号", "观测人签字", "复核人签字", "交接签字", "日期"), [(str(i), "（空栏）", "（空栏）", "（空栏）", "（空栏）") for i, _ in enumerate(rows, 1)]), "",
        "[A001] 点号、坐标、基准、单位、图纸、检定原件或观测缺失处待填；不填示范数、不换算单位，不补仪器限差、证书编号或签字。", "", support(rows)]
    return "\n".join(lines)


def build_draft(expert_id, tool_name, text):
    if TOOLS.get(expert_id) != tool_name:
        return None
    rows = parse_records(text or "", expert_id)
    if expert_id == "supervision":
        return _supervision(rows, text or "")
    return {"env": _env, "survey": _survey}[expert_id](rows)
