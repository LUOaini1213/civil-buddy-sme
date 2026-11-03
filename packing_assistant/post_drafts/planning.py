"""Literal planning records, not a scheduler or resource/CPM calculator.

Dates, durations and quantities remain user statements. File, building,
activity and resource identities own their fields independently; an unknown
identity is an unassigned row, never a licence to use the preceding row.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import html
import re

from packing_assistant.post_drafts.design_observations import _breaks
from packing_assistant.post_drafts.procurement import _literal_source, _source_name
from packing_assistant.post_facts import table_cell
from packing_assistant.word_export import _table_cells

MISSING = "[A001] / UNSPECIFIED"
TOOLS = {"plan-master": "plan-master__network", "plan-lookahead": "plan-lookahead__week", "plan-resource": "plan-resource__peak"}
DISCLAIMER = ("AI 草稿 · 内部讨论 · 不得作为法定专项方案 / 交底签认件。本文件仅登记用户资料，"
              "未进行网络、关键线路、时差、资源峰值或均衡计算；不是批准计划、工期签证、采购订单或开复工许可。"
              "正式资料和实施条件须由具备相应资格的人员复核签认。")
_DATE = r"(?:\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?|\d{1,2}月\d{1,2}[日号]|\d{1,2}月底|\d{1,2}号|下周|本周|下月|本月)"
_NUMBER = r"(?:[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|[一二三四五六七八九十百]+)"
_QUANTITY = re.compile(r"(?<![A-Za-z0-9_.#/-])"+_NUMBER+r"\s*(?:个人|人|个|名|台班|台|套|工日|吨|方|根|块|张|卷|m[³3](?![A-Za-z0-9_.-])|kg(?![A-Za-z0-9_.-]))", re.I)
_UNIT = re.compile(r"\d+[#＃]楼|[A-Z]座|(?:南|北|东|西)区管廊[A-Z]段")
_ACTIVITY = re.compile(r"(?:\d+[#＃]楼|[A-Z]座)(?:[一二三四五六七八九十\d]+层(?:到[一二三四五六七八九十\d]+层)?)?(?:屋面防水|二次结构|顶板模板支设|模板支设|砌筑|砌体|基础|主体结构|主体|装饰|回填)|(?:南|北|东|西)区管廊[A-Z]段回填|地下室(?:车库)?(?:消防管道|顶板模板支设|结构|外墙|顶板)|核心筒爬模|主体和装饰|主体结构|主体|装饰|砌筑|\d+层砌筑")
_MILESTONE = re.compile(r"主体结构封顶|主体封顶|结构封顶|外立面移交|地下室结构完成|基础(?:完成)?|封顶|形象节点")
_RESOURCE = re.compile(r"混凝土工|钢筋工|架子工|木工|瓦工|泥工|土方工|塔吊|汽车泵|汽车吊|泵车|挖机|挖掘机|吊车|钢筋(?!工)|木方|加气块|水泥|混凝土(?!工)|周转模板|模板|砖")
_UNKNOWN = re.compile(r"未提供|未给|未定|未知|待填|待核|待补|没给|没定|没报|还没|没有|多少|UNSPECIFIED|TBD|N/A", re.I)
_UNSAFE = re.compile(r"例如|比如|假设|如果|示例|样例|自行计算|自己算|不采用|不按|别按|不要|不是|是否|[？?]")
_BLOCKED = re.compile(r"未清|未到|未交|未发|未签|未验|未核|未完成|未收到|未落实|尚未|不具备|未满足|未批准|未具备|没到|没下|没签|还没|无图|过期|制约未|条件未|等.*验收")
_CLEARED = re.compile(r"制约已清|条件已具备|前置条件已满足")
_PENDING_CLEAR = re.compile(r"预计|计划|拟|将|明天|下周|尚未|未达到|未解除|没说|据说|听说|说会|声称|不算|还未|并未|未经|未核")
_FIELDS = {
    "project": ("项目名称", "工程名称", "项目", "Project"), "jurisdiction": ("辖区", "Jurisdiction"),
    "unit": ("单位工程", "楼栋", "楼号", "栋号", "Building"),
    "activity": ("活动名称", "工作名称", "区段工序", "工作面", "活动", "作业", "区段", "工作", "Activity"),
    "activity_id": ("活动ID", "活动编号", "WBS编码", "编码", "Activity ID"),
    "contract": ("合同工期", "总工期", "Contract duration"), "duration": ("用户工期", "持续时间", "工期"),
    "start": ("计划开工日期", "计划开工", "开工日期"), "finish": ("计划竣工日期", "计划竣工"),
    "period": ("计划周期", "计划时段", "计划期", "时段", "起止", "Period"), "baseline": ("基线版本", "总控版本", "基准计划", "Baseline"),
    "owner": ("责任单位", "责任班组", "责任方", "班组", "工长", "Owner"),
    "qty_source": ("工程量来源", "数量来源"), "source": ("来源文件", "资料来源", "来源", "Source"),
    "dependency": ("前置依赖", "前置条件", "紧前", "逻辑关系", "Predecessor"),
    "constraint": ("制约因素", "约束", "制约", "Constraint"),
    "clear_owner": ("制约责任人", "清除责任人", "清除责任"), "clear_date": ("计划清除日", "清除日"),
    "week": ("周次", "Week"), "node": ("里程碑", "用户节点", "节点", "Milestone"), "node_date": ("节点日期", "控制日期"),
    "cross": ("交叉作业", "交叉窗口"), "stop": ("停工条件", "暂停条件"),
    "actual": ("实际完成", "完成事实", "实际形象"), "todo": ("待办", "补齐行动"),
    "labor": ("工种名称", "劳动力", "工种", "人工"), "plant": ("机械名称", "机具名称", "机械", "机具", "设备"),
    "kind": ("资源类别", "资源类型"), "resource": ("资源名称",),
    "material": ("材料名称", "物资名称", "材料", "物资"), "model": ("规格型号", "型号", "规格"),
    "demand": ("用户需求量", "需用人数", "需用量", "需求量", "需用", "需求", "数量"),
    "available": ("实际可用量", "可用人数", "供应能力", "可用量", "可用"),
    "arrival": ("计划进场日期", "计划进场", "进场日期", "进场日", "到场日期"), "departure": ("退场日", "退场日期"),
    "window": ("需用窗口",), "yard": ("堆场位置", "堆场"), "supply": ("甲指或自采", "供料方式"),
    "conflict": ("供应约束", "冲突", "错峰约束"), "certificate": ("证件状态", "证件"),
}
ALIASES = {a.casefold(): k for k, aliases in _FIELDS.items() for a in aliases}
_LABEL = re.compile(r"^("+"|".join(re.escape(a) for a in sorted(ALIASES, key=len, reverse=True))+r")\s*[:：=]\s*(.*)$", re.I)


@dataclass
class Data:
    contexts: list[dict[str, str]] = field(default_factory=list)
    activities: list[dict[str, str]] = field(default_factory=list)
    nodes: list[dict[str, str]] = field(default_factory=list)
    resources: list[dict[str, str]] = field(default_factory=list)
    interfaces: list[dict[str, str]] = field(default_factory=list)
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
    if key in {"owner", "clear_owner", "model"} and (_UNKNOWN.search(value) or _UNSAFE.search(value) or "谁" in value):
        return MISSING
    if key in {"demand", "available", "contract", "duration", "start", "finish", "arrival", "departure", "node_date", "clear_date"}:
        if _UNKNOWN.search(value) or _UNSAFE.search(value):
            return MISSING
        if key in {"demand", "available"} and not (_QUANTITY.search(value) or re.fullmatch(_NUMBER, value)):
            return MISSING
        if key in {"contract", "duration"} and not (re.search(_NUMBER+r"\s*(?:日历天|天|工日|工作日|周|月)", value) or re.fullmatch(_NUMBER, value)):
            return MISSING
        if key in {"start", "finish", "arrival", "departure", "node_date", "clear_date"} and not re.search(_DATE, value):
            return MISSING
    return value


def _put(row, key, value):
    value = _typed(key, value)
    if row.get(key) in {None, MISSING, "UNSPECIFIED", "TBD"}:
        row[key] = value
    elif value != MISSING and value not in row[key].split("；"):
        row[key] += "；"+value


def _inputs(raw):
    """Yield clause or labelled row, retaining physical line/source boundaries."""
    filtered = _literal_source(raw)
    header = None
    offset = 0
    in_table = False
    for line in filtered.splitlines(keepends=True):
        stripped = line.strip()
        if _source_name(stripped) is not None or stripped.startswith(("【本轮用户要求", "【此前用户资料")):
            if in_table:
                yield None, True, offset, ""
            header = None
            in_table = False
            yield stripped, False, offset, stripped
        elif stripped.startswith("|"):
            cells = _table_cells(stripped)
            if any(cells) and all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                pass
            elif header is None:
                header = [html.unescape(c).strip() for c in cells]
                in_table = True
            else:
                yield [(key, html.unescape(cells[i]).strip() if i < len(cells) else "") for i, key in enumerate(header)], True, offset, stripped
        else:
            if in_table:
                yield None, True, offset, ""
                header = None
                in_table = False
            cursor = 0
            for boundary in [*_breaks(line), None]:
                end = boundary.start() if boundary else len(line)
                clause = line[cursor:end].strip()
                if clause:
                    yield clause, False, offset+cursor, clause
                cursor = boundary.end() if boundary else len(line)
        offset += len(line)


def _label(key):
    key = key.strip()
    match = re.fullmatch(r"第([一二三四1234])周(?:区段|工序|作业)?", key)
    if match:
        return "activity", "第"+match.group(1)+"周"
    if re.fullmatch(r"里程碑[一二三四1234]", key):
        return "node", None
    return ALIASES.get(key.casefold()), None


def _pairs(content):
    if isinstance(content, list):
        return [(key, value, week) for label, value in content for key, week in [_label(label)] if key]
    dynamic = re.match(r"^(第[一二三四1234]周(?:区段|工序|作业)?|里程碑[一二三四1234])\s*[:：]\s*(.*)$", content)
    if dynamic:
        key, week = _label(dynamic.group(1))
        return [(key, dynamic.group(2), week)]
    match = _LABEL.match(content)
    if match:
        return [(ALIASES[match.group(1).casefold()], match.group(2), None)]
    bare = re.match(r"^(计划清除日|需用人数|需用量|实际可用量|可用人数|可用量|需求量|可用)\s*(.+)$", content)
    return [(ALIASES[bare.group(1)], bare.group(2), None)] if bare else []


def _dates(text):
    return [m.group() for m in re.finditer(_DATE, text)]


def _node(value):
    dates = _dates(value)
    name = re.sub(_DATE, "", value).strip(" :：；;，,前到在是的")
    return _value(name), dates[0] if dates else MISSING


def _resource_parts(key, value):
    """A named resource can carry stated model/quantity, never a suffix of an ID."""
    facts = [(key, value, None)]
    if value.lstrip().startswith(("=", "+", "@", "-")):
        return facts
    match = _RESOURCE.search(value)
    if match:
        facts = [(key, match.group(), None)]
        tail = value[match.end():]
        if key == "plant":
            model = re.search(r"(?<![A-Za-z0-9_.-])([A-Za-z]+[A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*)(?![A-Za-z0-9_.-])", tail)
            if model and not (_UNKNOWN.search(tail) or _UNSAFE.search(tail)):
                facts.append(("model", model.group(1), None))
        if not (_UNKNOWN.search(tail) or _UNSAFE.search(tail)):
            previous = 0
            for qty in _QUANTITY.finditer(tail):
                before = tail[previous:qty.start()]
                qualifier = re.search(r"(?:大概|估算|粗算|约)\s*$", before)
                literal = (qualifier.group() if qualifier else "")+qty.group()
                facts.append(("available" if re.search(r"可用|已到场|实际到场", before) else "demand", literal, None))
                previous = qty.end()
        window = re.search(r"需用窗口\s*(.+)", tail)
        if window:
            facts.append(("window", window.group(1), None))
    return facts


def parse(post, raw):
    if post not in TOOLS:
        raise ValueError(post)
    data = Data()
    origin = ""
    scope = {"jurisdiction": "UNSPECIFIED"}
    data.contexts.append(scope)
    current = resource = None
    unit = ""

    def new(collection, values=None):
        row = {"_origin": origin, "_scope": len(data.contexts)-1, "_baseline": scope.get("baseline", MISSING), "jurisdiction": scope.get("jurisdiction", "UNSPECIFIED"),
               "project": scope.get("project", MISSING), "unit": unit or MISSING, **(values or {})}
        getattr(data, collection).append(row)
        return row

    def activity(name, *, fresh=False):
        nonlocal current, resource
        found_unit = _UNIT.search(name)
        named_unit = found_unit.group() if found_unit else unit
        candidates = [r for r in data.activities if r.get("activity") == name and r.get("unit") == (named_unit or MISSING)
                      and r.get("_scope") == len(data.contexts)-1 and r.get("_baseline") == scope.get("baseline", MISSING)]
        current = candidates[0] if len(candidates) == 1 and not fresh else new("activities", {"activity": _value(name), "unit": named_unit or MISSING})
        resource = None
        return current

    def record(key, value, target):
        _put(target, key, value)
        if _typed(key, value) != MISSING:
            data.evidence.append({"object": target.get("activity") or target.get("labor") or target.get("plant") or target.get("material") or target.get("unit") or MISSING,
                                  "field": _FIELDS[key][0], "value": _typed(key, value), "location": location})

    for content, is_table, offset, source in _inputs(raw):
        location = (origin+" · " if origin else "")+f"本轮输入第{raw[:offset].count(chr(10))+1}行（非原件页码）"
        if content is None:
            current = resource = None
            unit = ""
            continue
        marker = _source_name(source)
        if marker is not None or source.startswith(("【本轮用户要求", "【此前用户资料")):
            origin = marker if marker is not None else source.strip("【】")
            scope = {"_origin": origin, "jurisdiction": "UNSPECIFIED"}
            data.contexts.append(scope)
            current = resource = None
            unit = ""
            continue
        pairs = _pairs(content)
        text = source if isinstance(content, str) else ""
        if not is_table:
            text = re.sub(r"^(?:写|出|生成|起草|编制)一份(?:总控计划|总进度计划|周计划|月计划|资源负荷|资源计划)\s+", "", text)
        if not pairs and text:
            # Context is explicit, not the whole command copied as an activity.
            contract = re.search(r"([^\s：:]{1,30}?)合同工期\s*("+_NUMBER+r"\s*日历天)", text)
            if contract:
                pairs += [("project", contract.group(1), None), ("contract", contract.group(2), None)]
            start = re.search(r"计划\s*("+_DATE+r")\s*开工", text)
            if start:
                pairs.append(("start", start.group(1), None))
            period = re.search(r"("+_DATE+r")\s*起四周", text)
            if period:
                pairs.append(("period", period.group(1)+"起四周", None))
            elif re.match(r"下周的活", text):
                pairs.append(("period", "下周", None))
            if post == "plan-resource":
                match = _ACTIVITY.search(text)
                if match and not re.search(r"未|没|不|是否|[？?]", text[:match.start()]):
                    pairs.append(("activity", match.group(), None))
                    dates = _dates(text[:match.start()])
                    if dates:
                        pairs.append(("period", "到".join(dates), None))
                    if "下周" in text[match.end():]:
                        pairs.append(("period", "下周", None))
                for match in _RESOURCE.finditer(text):
                    name = match.group()
                    # Resource words inside the activity are not resources.
                    if any(k == "activity" and name in v for k, v, _ in pairs):
                        continue
                    if name in {"砖", "钢筋", "混凝土", "模板"} and re.search(r"吊|运|搬", text[max(0, match.start()-3):match.start()]):
                        continue
                    kind = "labor" if name.endswith("工") else "plant" if name in {"塔吊", "汽车泵", "汽车吊", "泵车", "挖机", "挖掘机", "吊车"} else "material"
                    tail_end = next((m.start() for m in _RESOURCE.finditer(text, match.end())), len(text))
                    pairs += _resource_parts(kind, text[match.start():tail_end])
                    source_match = re.search(r"(?:按|由)(劳务公司[^\d一二三四五六七八九十]{0,12})", text[match.end():tail_end])
                    if source_match:
                        pairs.append(("source", source_match.group(1).rstrip("的"), None))
                dates = _dates(text)
                if dates and "进场" in text:
                    pairs.append(("arrival", dates[-1], None))
                supply = re.search(r"甲指|甲供|自采", text)
                if supply:
                    pairs.append(("supply", supply.group(), None))
                yard = re.search(r"(?:堆在|堆到|堆场在)\s*(.+)", text)
                if yard:
                    pairs.append(("yard", yard.group(1), None))
            else:
                milestone = _MILESTONE.search(text)
                dates = _dates(text)
                is_node = bool(milestone and (dates or re.search(r"节点|日子|卡在|卡的是", text) or text == milestone.group()))
                if is_node:
                    pairs += [("node", milestone.group(), None), ("node_date", dates[-1] if dates else MISSING, None)]
                elif not pairs and not re.search(r"交叉|同一层|同一立面|停工|停止|图纸|检测归|制约|条件", text):
                    match = _ACTIVITY.search(text)
                    if match:
                        pairs.append(("activity", match.group(), None))
                        if re.search(r"已完成|已经完成|昨天完成|昨日完成", text[match.end():]):
                            pairs.append(("actual", text[match.end():], None))
                owner = re.search(r"(?:由|归)\s*([^，,；;。]+?)(?:干|负责|$)", text)
                if owner:
                    pairs.append(("owner", owner.group(1), None))
                elif post == "plan-lookahead":
                    owner = re.search(r"(?:工长)?(?:[\u4e00-\u9fff]{1,6}队长(?:的泥工班)?|泥工班|土方队|机电分包|木工二队|工长[\u4e00-\u9fff]{2,4})", text)
                    if owner:
                        pairs.append(("owner", owner.group(), None))
                    if "进场" in text and dates:
                        pairs.append(("arrival", dates[-1], None))
                quantity_source = re.search(r"工程量(?:来源(?:是|为)?|按)\s*([^，,；;。]+?)(?:算的|$)", text)
                if quantity_source:
                    pairs.append(("qty_source", quantity_source.group(1), None))
                duration = re.search(r"工期\s*((?:粗算|估算|约)?"+_NUMBER+r"\s*(?:日历天|天|工作日))", text)
                if duration and not contract:
                    pairs.append(("duration", duration.group(1), None))
                dependency = re.search(r"(?:要等|等)(.+?)(?:完才能|才能|才)(?:.*)$", text)
                if dependency:
                    pairs.append(("dependency", dependency.group(1), None))
                if re.search(r"图纸.*还没|检测归.*没|塔吊未到|制约未|条件未", text):
                    pairs.append(("constraint", text, None))
                if _CLEARED.search(text):
                    pairs.append(("constraint", text, None))
                if "交叉" in text or "同一立面" in text:
                    pairs.append(("cross", text, None))
                if re.search(r"停工|停止.+作业", text):
                    pairs.append(("stop", text, None))
                clear_date = re.search(r"计划清除日\s*[:：]?\s*("+_DATE+r")", text)
                if clear_date:
                    pairs.append(("clear_date", clear_date.group(1), None))
        if not pairs:
            continue
        if post == "plan-resource":
            stated_kind = next((v for k, v, _ in pairs if k == "kind"), "")
            mapped_kind = {"人工": "labor", "劳动力": "labor", "机械": "plant", "机具": "plant", "材料": "material", "labor": "labor", "plant": "plant", "material": "material"}.get(stated_kind.casefold())
            if mapped_kind:
                pairs = [(mapped_kind, next((v for k, v, _ in pairs if k == "resource"), MISSING), None)]+[(k, v, w) for k, v, w in pairs if k not in {"resource", "kind"}]
            else:
                pairs = [(k, v, w) for k, v, w in pairs if k != "kind"]
            if is_table:
                # Allocate every resource identity before consuming its
                # attributes, independent of the user's column order.
                context_keys = {"project", "jurisdiction", "period", "baseline"}
                identity_keys = {"labor", "plant", "material"}
                pairs = ([p for p in pairs if p[0] in context_keys]
                         + [p for p in pairs if p[0] in identity_keys]
                         + [p for p in pairs if p[0] not in context_keys | identity_keys])
        if is_table:
            # Identity cells are authoritative even when empty and reordered.
            current = resource = None
            unit = next((_value(v) for k, v, _ in pairs if k == "unit"), "")
            names = [v for k, v, _ in pairs if k == "activity"]
            if names:
                found_unit = _UNIT.search(names[0])
                if not unit and found_unit:
                    unit = found_unit.group()
                activity(names[0], fresh=True)
            else:
                current = new("activities")
        elif any(k == "project" for k, _, _ in pairs) and scope.get("project"):
            scope = {"_origin": origin, "jurisdiction": "UNSPECIFIED"}
            data.contexts.append(scope)
            current = resource = None
            unit = ""
        baseline = next((v for k, v, _ in pairs if k == "baseline"), None)
        if baseline is not None and scope.get("baseline") not in {None, baseline}:
            scope = {"_origin": origin, "project": scope.get("project", MISSING), "jurisdiction": scope.get("jurisdiction", "UNSPECIFIED")}
            data.contexts.append(scope)
            current = resource = None
            unit = ""
        # A unit change revokes the previous activity/resource owner.
        explicit_unit = next((v for k, v, _ in pairs if k == "unit"), None)
        activity_name = next((v for k, v, _ in pairs if k == "activity"), None)
        found = _UNIT.search(activity_name or text)
        new_unit = explicit_unit if explicit_unit is not None else found.group() if found else None
        if new_unit is not None and new_unit != unit:
            unit = _value(new_unit)
            current = resource = None
        if activity_name is not None and not is_table:
            activity(activity_name)
        if post == "plan-lookahead" and not is_table:
            # A new interface or independently named subcontractor is not
            # another responsibility of the preceding roof/masonry activity.
            if any(k in {"cross", "stop"} for k, _, _ in pairs) or text.startswith("总控"):
                current = resource = None
                unit = ""
            elif activity_name is None and re.match(r"(?:机电|消防|幕墙|土建)分包", text):
                current = new("activities", {"unit": MISSING})
                unit = ""
            elif text.strip() in {"制约已清", "条件已具备", "前置条件已满足"}:
                candidates = [r for r in data.activities if r["_scope"] == len(data.contexts)-1 and r.get("activity") not in {None, MISSING}]
                if len(candidates) != 1:
                    current = new("activities", {"unit": MISSING})
        node_value = next((v for k, v, _ in pairs if k == "node"), None)
        node_date = next((v for k, v, _ in pairs if k == "node_date"), None)
        if node_value is not None or node_date is not None:
            name, date = _node(node_value) if node_value is not None else (MISSING, MISSING)
            date = node_date if node_date is not None else date
            rows = []
            collective = post == "plan-lookahead" and bool(re.search(r"这两摊|这两项|两个都|两项都", text))
            owned = [r for r in data.activities if r["_scope"] == len(data.contexts)-1 and r.get("activity") not in {None, MISSING}]
            if collective and len(owned) == 2:
                rows = [new("nodes", {"unit": r["unit"], "activity": r["activity"]}) for r in owned]
            else:
                # Lookahead control nodes without an explicit activity are
                # global/unassigned, not inherited from the last row.
                values = {"unit": MISSING, "activity": MISSING} if post == "plan-lookahead" and activity_name is None and explicit_unit is None else {}
                if collective:
                    values = {"unit": MISSING, "activity": MISSING}
                rows = [new("nodes", values)]
            for row in rows:
                if is_table:
                    for context_key in ("project", "jurisdiction", "baseline"):
                        literal = next((v for k, v, _ in pairs if k == context_key), None)
                        if literal is not None:
                            row["_baseline" if context_key == "baseline" else context_key] = _value(literal)
                record("node", name, row)
                record("node_date", date, row)
        if post == "plan-master" and current is None and unit and any(k not in {"project", "start", "contract", "finish", "baseline", "period", "jurisdiction", "node", "node_date"} for k, _, _ in pairs):
            current = new("activities")
        for key, value, week in pairs:
            if key in {"project", "contract", "start", "finish", "baseline", "jurisdiction"}:
                target = resource if key == "jurisdiction" and resource is not None else current if is_table or key == "jurisdiction" and current is not None else scope
                record(key, value, target)
                if key == "baseline" and target is scope and _value(value) != MISSING:
                    # A source may state its file-wide baseline after its
                    # rows. Fill only still-missing provenance in this scope;
                    # a later revision has already opened a new scope above.
                    for row in data.activities+data.nodes+data.resources:
                        if row["_scope"] == len(data.contexts)-1 and row.get("_baseline") in {None, MISSING}:
                            row["_baseline"] = _value(value)
                continue
            if key == "period":
                record(key, value, current if current is not None and post == "plan-resource" else scope)
                continue
            if key == "unit":
                if current is not None:
                    record(key, value, current)
                continue
            if key == "activity":
                if current is not None:
                    record(key, value, current)
                    if week:
                        record("week", week, current)
                continue
            if key in {"labor", "plant", "material"}:
                pieces = _resource_parts(key, value)
                resource = new("resources", {"activity": current.get("activity", MISSING) if current else MISSING,
                                              "period": current.get("period", scope.get("period", MISSING)) if current else scope.get("period", MISSING), "kind": key,
                                              "jurisdiction": current.get("jurisdiction", "UNSPECIFIED") if current else scope.get("jurisdiction", "UNSPECIFIED"),
                                              "project": current.get("project", MISSING) if current else scope.get("project", MISSING)})
                for rkey, rval, _ in pieces:
                    record(rkey, rval, resource)
                continue
            if key in {"node", "node_date"}:
                continue
            if key in {"cross", "stop"}:
                record(key, value, new("interfaces"))
                continue
            if post == "plan-resource":
                if is_table and key in {"demand", "available"} and sum(k in {"labor", "plant", "material"} for k, _, _ in pairs) > 1 and (resource is None or resource.get("kind") != "unassigned"):
                    resource = new("resources", {"kind": "unassigned", "activity": current.get("activity", MISSING) if current else MISSING})
                if key == "source" and re.search(r"劳务|班组编制", value) and resource and resource.get("kind") != "labor":
                    resource = new("resources", {"kind": "unassigned", "activity": current.get("activity", MISSING) if current else MISSING})
                if resource is None:
                    resource = new("resources", {"kind": "unassigned", "activity": current.get("activity", MISSING) if current else MISSING})
                record(key, value, resource)
            else:
                if current is None:
                    current = new("activities")
                if key == "qty_source" and not unit and len([r for r in data.activities if r["_scope"] == len(data.contexts)-1 and r.get("activity")]) > 1:
                    current = new("activities", {"unit": MISSING})
                target = current
                if key == "owner" and post == "plan-master" and "这栋" in text and unit:
                    for row in data.activities:
                        if row["unit"] == unit and row["_scope"] == len(data.contexts)-1:
                            record(key, value, row)
                else:
                    record(key, value, target)
    data.contexts = [r for r in data.contexts if len(r) > 1] or data.contexts[:1]
    return data


def _table(rows, specs, *, extra=(), defaults=None):
    defaults = defaults or {}
    headers = [label for label, _ in specs]+[label for label, _ in extra]
    result = ["| "+" | ".join(headers)+" |", "| "+" | ".join("---" for _ in headers)+" |"]
    for row in rows or [{}]:
        values = [row.get(key) or defaults.get(key, MISSING) for _, key in specs]+[value for _, value in extra]
        result.append("| "+" | ".join(table_cell(v) for v in values)+" |")
    return "\n".join(result)


def _header(title, data):
    return [f"# {title}（AI 草稿 · 内部讨论）", "", DISCLAIMER, "", "- submit_blocked=true", "",
            _table(data.contexts, (("工程 / 项目名称", "project"), ("用户合同工期", "contract"), ("计划开工", "start"),
                                  ("计划竣工", "finish"), ("计划期 / 周期", "period"), ("总控 / 基线版本", "baseline"), ("辖区", "jurisdiction"), ("输入资料名", "_origin"))), "",
            "缺项保持待填，未核原件或现行条款；没有网络参数、工作日历和计算结果，不判断进度是否可实现。", ""]


def _evidence(data):
    return _table(data.evidence, (("用户对象", "object"), ("记录项", "field"), ("用户证据原文", "value"), ("输入位置", "location")))


def _nodes(data):
    return _table(data.nodes, (("单位工程 / 楼栋", "unit"), ("区段工序", "activity"), ("用户里程碑 / 节点", "node"), ("节点日期原文", "node_date"), ("项目名称", "project"), ("对象辖区", "jurisdiction"), ("基线版本", "_baseline"), ("输入资料名", "_origin")))


def _master(data):
    lines = _header("施工总进度计划", data)
    lines += ["## 1 范围声明与文件控制", "", "WBS 编码、编制、复核和签认由责任人填；签认栏：（空）。", "",
              "## 2 总控目标", "", _nodes(data), "", "节点为用户约定或陈述，未自动计算计划竣工日期。", "",
              "## 3 工作分解 / 活动分解", "", _table(data.activities, (("单位工程 / 楼栋", "unit"), ("WBS活动ID", "activity_id"), ("工作名称", "activity"),
                  ("责任单位", "owner"), ("工程量来源", "qty_source"), ("用户工期 / 持续时间口径", "duration"), ("前置依赖 / 紧前", "dependency"), ("用户里程碑", "node"), ("项目名称", "project"), ("对象辖区", "jurisdiction"), ("基线版本", "_baseline"), ("输入资料名", "_origin"))), "",
              "用户粗算与合同工期分别登记；未给持续时间，不从工程量、日期或经验倒算。", "",
              "## 4 一级网络与里程碑", "", _nodes(data), "",
              "## 5 逻辑关系", "", _table(data.activities, (("单位工程", "unit"), ("工作名称", "activity"), ("活动ID", "activity_id"), ("前置依赖 / 紧前原文", "dependency"), ("来源", "source"))), "",
              "FS / SS / FF / SF 仅按用户原文登记，不推导紧后、搭接时长或网络图。", "",
              "## 6 关键线路待核", "", "关键线路=待计算；总时差=待计算。本轮未执行 CPM 或排程优化。", "",
              "## 7 资源接口与来源", "", _evidence(data), "",
              "## 8 风险与更新", "", "来源、基线、日历、责任单位和外部约束缺项交责任人核对；不默写规范版本或条款，不签发实施指令。", ""]
    return "\n".join(lines)


def _lookahead(data):
    lines = _header("四周滚动计划 / 月度计划", data)
    jobs = [r for r in data.activities if not r.get("actual")]
    candidates = [r for r in jobs if _CLEARED.search(r.get("constraint", "")) and not _UNSAFE.search(r.get("constraint", "")) and not _PENDING_CLEAR.search(r.get("constraint", "")) and not _BLOCKED.search(r.get("constraint", "")+r.get("dependency", ""))
                  and r.get("activity") not in {None, MISSING} and r.get("week") in {None, "第1周", "第一周"}]
    lines += ["## 1 封面", "", "编制与签认：（空）。周期和总控版本见文件控制表；未给辖区不默认国家。", "",
              "## 2 从总控抽取窗口", "", _nodes(data), "",
              "## 3 近细远粗 / 四周工序", "", _table(jobs, (("周次（未分配则待填）", "week"), ("单位工程 / 楼栋", "unit"), ("区段工序 / 作业", "activity"),
                   ("前置条件原文", "dependency"), ("责任方 / 班组 / 工长", "owner"), ("制约 / 约束原文", "constraint"), ("用户计划进场日", "arrival"), ("待办", "todo"), ("项目名称", "project"), ("对象辖区", "jurisdiction"), ("基线版本", "_baseline"), ("输入资料名", "_origin"))), "",
              "| 周次窗口 | 分配状态 |", "| --- | --- |", "| 第1周 | 只登记明确周次的作业，不自动铺排。 |", "| 第2周 | 未指定活动保持待填。 |", "| 第3周 | 未指定活动保持待填。 |", "| 第4周 | 未指定活动保持待填。 |", "",
              "## 4 制约因素 / 前置条件", "", _table(jobs, (("区段工序", "activity"), ("前置条件", "dependency"), ("制约 / 阻塞项", "constraint"), ("清除责任人", "clear_owner"), ("计划清除日", "clear_date"), ("待办", "todo"))), "",
              "## 5 周承诺", "", "仅登记该项用户已声明条件具备的候选，不代表核实或开工许可。" if candidates else "制约未清或证据未给，不得写入本周承诺。", "",
              _table(candidates, (("作业", "activity"), ("认领人（待核）", "owner"), ("用户条件状态", "constraint")), extra=(("认领 / 签认", "（空）"), ("周末兑现", "待对照"))), "",
              "## 6 交叉作业", "", _table([r for r in data.interfaces if "cross" in r], (("单位工程", "unit"), ("交叉作业原文", "cross"), ("来源资料", "_origin"))), "",
              "## 7 停工条件", "", _table([r for r in data.interfaces if "stop" in r], (("单位工程", "unit"), ("用户停工触发条件原文（未核）", "stop"), ("来源资料", "_origin"))), "",
              "用户预警或暂停条件只记录，适用作业和当地现行要求须另核；不从模板给风速限值或签发复工许可。", "",
              "## 8 与总控的回写", "", _evidence(data), "",
              "## 9 月度形象对照 / 实际完成", "", _table([r for r in data.activities if "actual" in r], (("区段工序", "activity"), ("用户实际完成原文（未核）", "actual"))), "",
              "实际完成与未来拟安排分开；本轮未计算完成百分比、PPC 或偏差天数。", "",
              "## 10 待填与禁令", "", "未满足前置条件的活动保留阻塞；不承诺工期、资源齐备或交叉作业安全。", ""]
    return "\n".join(lines)


def _resource(data):
    lines = _header("资源负荷表", data)
    common = (("工作 / 活动 / 作业面", "activity"), ("单位工程 / 楼栋", "unit"), ("计划时段", "period"))
    provenance = (("来源（用户给定，未核）", "source"), ("冲突待核", "conflict"), ("项目名称", "project"), ("对象辖区", "jurisdiction"), ("基线版本", "_baseline"), ("输入资料名", "_origin"))

    def group(kind):
        return [r for r in data.resources if r.get("kind") == kind]
    lines += ["## 1 封面与声明", "", "签认栏：（空）。需求和供应分别登记，不编定额、台班或材料耗用。", "",
              "## 2 输入清单 / 活动时段", "", _table(data.activities, common+(("资源来源", "source"),)), "",
              "## 3 劳动力负荷表头", "", _table(group("labor"), (("工种 / 劳动力", "labor"),)+common+(("用户需用人数原文", "demand"), ("实际可用量原文", "available"))+provenance, defaults={"demand": "TBD / "+MISSING}), "",
              "## 4 施工机具负荷表头", "", _table(group("plant"), (("机械名称", "plant"), ("规格 / 型号", "model"))+common+(("台数 / 台班需求原文", "demand"), ("实际可用量原文", "available"), ("计划进场日（非进场事实）", "arrival"), ("退场日", "departure"), ("用户证件状态（未核）", "certificate"))+provenance), "",
              "## 5 主要材料与周转料表头", "", _table(group("material"), (("材料名称", "material"),)+common+(("需用窗口", "window"), ("用户需求量原文", "demand"), ("实际可用量原文", "available"), ("计划进场日", "arrival"), ("堆场", "yard"), ("甲指或自采", "supply"))+provenance), "",
              "## 6 峰值与错峰", "", "峰值=待计算；时差=待计算。未做均衡，不相加不同单位或不同活动的需求，不压缩持续时间。", "",
              "## 7 冲突提示栏 / 归属待核", "", _table(group("unassigned"), (("工作 / 活动", "activity"), ("资源名称原文", "resource"), ("用户需求量原文", "demand"), ("实际可用量原文", "available"), ("来源", "source"), ("冲突待核", "conflict"))), "",
              "## 8 与周月、采购、资金的接口", "", _evidence(data), "",
              "## 9 优化记录", "", "未做均衡，仅登记用户资料与表头；本轮未计算人机料峰值或供应缺口。", "",
              "## 10 禁令", "", "数量及单位按用户原文保留，不将计划进场当作已到场，不代核证件、采购或实施签认。", ""]
    return "\n".join(lines)


def build_draft(post, tool_name, text):
    if TOOLS.get(post) != tool_name:
        return None
    data = parse(post, text)
    return {"plan-master": _master, "plan-lookahead": _lookahead, "plan-resource": _resource}[post](data)
