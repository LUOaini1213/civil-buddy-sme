"""Literal, clause-local observations for a daily-report draft, never a solver.

Only explicit reporting cues are recognized. Dates in historical prose are not
report dates, forecasts are not weather observations, and next-day context stays
in force across following comma clauses. No counts are summed or converted.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from packing_assistant.post_facts import clauses, items

LABELS = {"date": "日期", "weather": "天气", "site": "部位", "progress": "形象进度",
          "labor": "出勤", "resources": "机械材料", "hse": "安全质量记事", "tomorrow": "明日计划"}
_WEATHER = r"(?:晴天|晴|多云|阴天|阴|雷阵雨|暴雨|大雨|中雨|小雨|阵雨|雨|大雪|中雪|小雪|雪|大雾|雾|台风)(?:转(?:晴天|晴|多云|阴天|阴|雷阵雨|暴雨|大雨|中雨|小雨|阵雨|雨|雪|雾))?"
_DATE = re.compile(r"^(\d{4}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2}\s*日?|\d{1,2}\s*月\s*\d{1,2}\s*[日号])")
_WEATHER_CUE = re.compile(r"(?:天气\s*[:：]?\s*|上午|下午|晚上|夜间|今天|今日)(?:下了?|出现|有)?\s*(" + _WEATHER + r")(?!水|管|措施|棚|衣|靴)")
_WEATHER_ONLY = re.compile(r"^(" + _WEATHER + r")(?:$|\s)")
_FUTURE = re.compile(r"明天|明日|后天|次日|预报|预计|拟安排|计划|准备|打算|安排|例如|比如|示例|假设|不少于|至少|上限|容纳"
                     r"|(?:需(?:要)?|须|拟|待|将)[^，,；;。\n]{0,12}(?:进场|到场|到货|安装|浇筑|绑扎|出勤)")
_CURRENT = re.compile(r"^(?:今天|今日|当日|实际|实到|已到场|上午|下午|晚上|夜间)|实际完成|(?<!未)已完成|已到场")
_UNKNOWN = re.compile(r"待填|未填|未提供|未知|不确定|未确定|未说明|UNSPECIFIED|没报|未报|未记录", re.I)
_NEXT_PLAN = re.compile(r"^(?:明天|明日|次日)(?:准备|计划|拟安排|安排|打算)")
_COMMAND = re.compile(r"(?:帮我|请|麻烦|给我|帮忙).{0,16}(?:日报|工程日志)(?:草稿|模板)?\s*[:：]?$")
_PREFACE = re.compile(r"^.{0,30}?(?:日报|工程日志)\s*[:：]\s*")
_SHORT_COMMAND = re.compile(r"^(?:[@$][\w-]+\s*)?(?:(?:请|帮我|麻烦|帮忙)\s*)?(?:写|生成|起草|编制)(?:一份|个)?\s*(?:项目日报|日报|工程日志)(?:草稿)?\s+")
_COMPACT_WEATHER = re.compile(r"(?<!\S)(" + _WEATHER + r")(?!\S)")
_DIRECTIVE = re.compile(r"^(?:请|帮我|帮忙|麻烦|需要|不要|禁止|别)")
_EXPLICIT_LABEL = re.compile(r"^([^：:\n]{1,20})\s*[:：=]")
_STOREY = r"(?:[0-9一二三四五六七八九十]+层)?"
_SITE = re.compile(r"(?:[一-鿿A-Za-z]{1,18})?\d+[#＃]楼" + _STOREY + r"(?:剪力墙|地下室|屋面|顶板|承台)?"
                   r"|[0-9一二三四五六七八九十]+号楼" + _STOREY + r"(?:剪力墙|地下室|屋面|顶板|承台)?"
                   r"|[东西南北](?:立面|侧作业面)")
_PROGRESS = re.compile(r"(?<!未)(?:完成(?:到|了)?|浇了|已浇筑|已安装|还在挖|正在开挖|正在浇筑|正在安装|正在绑扎)")
_LABOR = re.compile(r"(?<![A-Za-z0-9.,])(?:(?:木工|钢筋工?|砼工|混凝土工|架子工|电焊工?|普工|电工|瓦工|泥工|工人|管理人员|劳务人员|出勤|到场|实到|到岗|人数)(?:班组?|组)?\s*(?:到了|来了|到場|到场|实到|到岗)?\s*[:：=]?\s*)?\d+(?:,\d{3})*\s*个?人")
_LABOR_ABSENT = re.compile(r"请假|缺勤|未到|缺席|缺人|少了|休假|不在场|来访|参观|参会|定员|容量")
_PLANT = re.compile(r"塔吊|泵车|挖机|挖掘机|吊机|汽车吊|吊车|装载机|压路机|搅拌机")
_MATERIAL = re.compile(r"商砼|混凝土|钢筋|水泥|铝板|玻璃|模板|砂石|砂浆|材料")
_RESOURCE_EVENT = re.compile(r"进场|到货|来了|运来|入库|领用|消耗|送到|已到")
_COUNT = re.compile(r"[0-9一二三四五六七八九十两]+(?:\.\d+)?\s*(?:台|辆|车|吨|方|张|块|袋|根|kg|t)\b|[0-9一二三四五六七八九十两]+\s*(?:台|辆|车|吨|方|张|块|袋|根)")
_PLANT_STATE = re.compile(r"停在|在场|运转|运行|故障|坏了|维修|修到|停机")
_HSE = re.compile(r"巡查|查出|发现|安全员|监理|业主|质检")
_HSE_EVENT = re.compile(r"防护|安全|质量|隐患|缺|脱落|整改|违章|裂缝")


@dataclass(frozen=True)
class Observation:
    field: str
    text: str
    location: str = ""


def future_scope(label: str, value: str, current: bool) -> bool:
    """A next-day heading owns subsequent fields until an explicit actual cue."""
    folded = label.casefold()
    if re.search(r"明日|明天|次日|计划|预报|预计|tomorrow|next day|forecast|planned", folded):
        return True
    actual_label = (folded.startswith(("今日", "今天", "当日", "actual ", "today's "))
                    or folded in {"work completed", "completed work", "实际完成"})
    if actual_label or _CURRENT.search(value):
        return bool(_FUTURE.search(value))
    return current


def extract(text: str) -> list[Observation]:
    result: list[Observation] = []
    future = False
    for item in items(text or ""):
        for raw in clauses(item):
            piece = _PREFACE.sub("", raw).strip()
            short_command = _SHORT_COMMAND.match(piece)
            if short_command:
                piece = piece[short_command.end():].strip()
            if not piece or _COMMAND.search(piece):
                continue
            if _DIRECTIVE.match(piece):
                future = True
                continue
            explicit = _EXPLICIT_LABEL.match(piece)
            if explicit:
                label = explicit.group(1).strip()
                future = future_scope(label, piece[explicit.end():], future)
                continue
            # A historical sentence containing a date is intentionally not
            # treated as a report header; require a leading date + weather.
            date = _DATE.match(piece)
            weather_tail = piece[date.end():].strip() if date else ""
            weather = _WEATHER_ONLY.match(weather_tail) if date else None
            if _CURRENT.match(piece):
                future = False
            if _NEXT_PLAN.match(piece) and not _UNKNOWN.search(piece):
                result.append(Observation("tomorrow", piece))
                future = True
                continue
            if _FUTURE.search(piece):
                future = True
                continue
            if future or _UNKNOWN.search(piece) or re.search(r"(?:不是|并非|是否|请核对|请检查)", piece):
                continue
            if date and weather:
                result.append(Observation("date", date.group(1)))
            weather = weather or _WEATHER_CUE.search(piece) or _WEATHER_ONLY.match(piece)
            if not weather and short_command and len(piece) <= 80:
                weather = _COMPACT_WEATHER.search(piece)
            if weather:
                result.append(Observation("weather", weather.group(1)))
            site = _SITE.search(piece)
            location = site.group() if site else ""
            if site and (_PROGRESS.search(piece) or piece == location):
                result.append(Observation("site", location, location))
            if site and _PROGRESS.search(piece):
                result.append(Observation("progress", piece, location))
            if not _LABOR_ABSENT.search(piece):
                for labor in _LABOR.finditer(piece):
                    result.append(Observation("labor", labor.group(), location))
            if ((_PLANT.search(piece) and (_COUNT.search(piece) or _PLANT_STATE.search(piece)))
                    or (_MATERIAL.search(piece) and _RESOURCE_EVENT.search(piece))):
                result.append(Observation("resources", piece, location))
            if _HSE.search(piece) and _HSE_EVENT.search(piece):
                result.append(Observation("hse", piece, location))
    return list(dict.fromkeys(result))


def fields(records: list[Observation]) -> dict[str, str]:
    """Report-wide literal summary; rows retain their own location separately."""
    result: dict[str, list[str]] = {}
    for record in records:
        values = result.setdefault(record.field, [])
        if record.text not in values:
            values.append(record.text)
    return {key: "；".join(values) for key, values in result.items()}
