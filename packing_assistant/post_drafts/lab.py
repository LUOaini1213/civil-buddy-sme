"""Literal laboratory records and evidence, never testing or mix-design results.

Samples, instruments and mix targets have independent ownership. Numeric
requirements are copied with their stated units; no sample counts, ratios,
certificate validity, report results or permission to use materials is derived.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import html
import re

from packing_assistant.jurisdiction import infer_jurisdiction
from packing_assistant.post_drafts.design_observations import _breaks
from packing_assistant.post_drafts.procurement import _source_name
from packing_assistant.post_facts import table_cell
from packing_assistant.word_export import _table_cells

MISSING = "[A001] / UNSPECIFIED"
TOOLS = {"lab-sample": "lab-sample__list", "lab-record": "lab-record__ledger", "lab-mix": "lab-mix__report"}
DISCLAIMER = ("本文件为 AI 草稿 · 内部讨论，仅整理用户资料；不得作为法定专项方案 / 交底签认件、"
              "检测报告、验收资料、配合比通知单或开盘依据。未实施取样、检测、仪器检定或配合比计算，"
              "不代替具备相应资格的人员复核签认。")
_NUMBER = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_DATE = r"(?:\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?|\d{1,2}月\d{1,2}[日号])"
_CODE = r"[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*"
_UNKNOWN = re.compile(r"还没|没给|没回|没定|没影|未给|未回|未定|未提供|未收到|未试拌|未试配|未知|待填|待核|待补|UNSPECIFIED|TBD|N/A|没有|多少", re.I)
_UNSAFE = re.compile(r"例如|比如|假设|如果|只是示例|只是样例|不采用|不按|别按|不要|请自行|自行计算|不是|是否|[？?]")
_FIELDS = {
    "project": ("项目名称", "工程名称", "试验室名称", "项目", "Project"), "jurisdiction": ("辖区", "Jurisdiction"),
    "sample": ("样品批次", "样品名称", "材料名称", "试样名称", "样品", "试样", "材料", "类别", "Sample"),
    "spec": ("规格型号", "规格", "牌号", "型号", "Spec"), "batch": ("样品批号", "材料批号", "批号", "批次", "Batch"),
    "manufacturer": ("生产厂家", "生产企业", "厂家", "生产厂", "品牌",),
    "quantity": ("进场数量", "代表数量原文", "数量", "批量", "Quantity"), "quantity_source": ("数量来源", "磅单编号", "磅单",),
    "site": ("工程部位", "使用部位", "施工段", "部位", "用途", "Location"),
    "arrival_date": ("进场日期", "到货日期"), "sample_date": ("取样日期",), "send_date": ("送检日期",),
    "formed_date": ("成型日期",), "entrusted_date": ("委托日期",),
    "sampler": ("取样员", "取样人", "送样人", "Sampler"), "sampler_unit": ("取样单位",),
    "witness": ("见证人", "Witness"), "witness_unit": ("见证单位",), "lab": ("送检单位", "检测机构", "检测单位", "受托单位", "Laboratory"),
    "entrust_id": ("委托单编号", "委托单号", "委托编号", "委托单", "送检编号"),
    "sample_id": ("样品标识", "样品编号", "唯一标识"), "test": ("试验项目", "试验项", "检测项目", "检测项", "Test"),
    "report_id": ("报告编号", "报告号", "Report ID"), "report_status": ("报告状态", "报告追踪", "报告收集状态"),
    "requirements": ("检测要求", "取样要求来源", "取样要求", "取样制度", "取样依据", "制度"),
    "group_count": ("用户取样组数", "取样组数", "组数"), "frequency": ("取样频次", "频次"), "anomaly": ("不合格记录", "异常信息", "异常",),
    "source": ("来源文件", "资料来源", "记录来源", "来源",),
    "instrument": ("仪器编号", "设备编号", "器具编号", "仪器", "Instrument ID"), "instrument_name": ("仪器名称", "设备名称",),
    "verification_id": ("检定证书编号", "检定证书",), "calibration_id": ("校准证书编号", "校准证书",),
    "verification_date": ("检定日期",), "expiry": ("检定有效期至", "检定有效期", "有效期至", "有效期",),
    "verification_record": ("检定记录", "仪器检定",),
    "strength": ("强度等级", "砂浆等级", "设计强度",), "permeability": ("抗渗等级", "抗渗",),
    "slump": ("坍落度要求", "坍落度",), "consistency": ("稠度要求", "稠度",),
    "cement": ("水泥",), "sand": ("细骨料", "砂",), "stone": ("粗骨料", "石",),
    "flyash": ("粉煤灰", "矿物掺合料",), "admixture": ("外加剂",), "water": ("拌合水",),
    "trial_id": ("试配记录编号", "试拌记录编号", "试配编号", "试配记录", "试拌记录",),
    "raw_report_id": ("原材料复试报告编号", "原材料复试", "原材报告",), "client": ("委托人", "申请人",),
    "trial_status": ("试配状态", "试拌状态", "试验数据状态",), "prep": ("拌合方式",),
    "lab_ratio": ("用户试验室配比", "试验室配比原文", "用户配比",),
    "approved_source": ("批准资料", "批准的配合比编号",), "water_content": ("实测含水率", "含水率",),
    "water_binder": ("用户水胶比", "水胶比",), "production_ratio": ("用户施工配比原文", "施工配比原文",),
}
ALIASES = {alias.casefold(): key for key, aliases in _FIELDS.items() for alias in aliases}
_LABEL = re.compile("|".join(
    r"(?<![A-Za-z0-9_./-])"+re.escape(alias)+r"(?![A-Za-z0-9_./-])"
    if re.fullmatch(r"[a-z ]+", alias) else re.escape(alias)
    for alias in sorted(ALIASES, key=len, reverse=True)), re.I)
_SAMPLE = re.compile(r"钢筋原材|SBS\s*防水卷材|防水卷材|砌筑砂浆试块|砂浆试块|混凝土试块|同条件试件|螺纹钢|盘螺|钢筋|中砂|水泥", re.I)
_SITE = re.compile(r"\d+[#＃]楼(?:[一二三四五六七八九十\d]+层)?(?:剪力墙|梁板|楼板|顶板|池壁)?|地下室外墙|地下室(?:B\d层)?(?:底板)?|屋面|砌体")
_INSTRUMENT_FIELDS = {"instrument", "instrument_name", "verification_id", "calibration_id", "verification_date", "expiry", "verification_record"}
_MIX_FIELDS = {"strength", "permeability", "slump", "consistency", "cement", "sand", "stone", "flyash", "admixture", "water",
               "trial_id", "trial_status", "raw_report_id", "client", "prep", "lab_ratio", "approved_source", "water_content", "water_binder", "production_ratio"}
_NUMERIC = {"quantity", "group_count", "frequency", "slump", "consistency", "water_content", "water_binder"}


@dataclass(frozen=True)
class Fact:
    key: str
    value: str
    start: int
    end: int
    explicit: bool = False


@dataclass
class Data:
    context: dict[str, str] = field(default_factory=dict)
    records: list[dict[str, str]] = field(default_factory=list)
    instruments: list[dict[str, str]] = field(default_factory=list)
    evidence: list[dict[str, str]] = field(default_factory=list)


def _value(value: str) -> str:
    value = value.strip()
    if not re.search(r"&(?:#\d+|#x[\da-f]+|[a-z][\da-z]+);$", value, re.I):
        value = value.rstrip("，,；;。")
    return MISSING if not value or _UNKNOWN.fullmatch(value) else value


def _typed(key: str, value: str) -> str:
    value = _value(value)
    if key in _NUMERIC or key in {"strength", "permeability", "batch", "report_id", "entrust_id", "trial_id", "verification_id", "calibration_id"}:
        if _UNSAFE.search(value) or _UNKNOWN.search(value):
            return MISSING
    if key == "quantity" and not (re.fullmatch(_NUMBER, value) or re.search(r"(?<![A-Za-z0-9_.#/-])"+_NUMBER+r"\s*(?:吨|方|公斤|千克|立方米|kg\b|t\b|m[³3]\b|根|卷|件|块|组)", value, re.I)):
        return MISSING
    if key in {"slump", "consistency", "water_content", "water_binder", "group_count"} and not re.search(r"(?<![A-Za-z0-9_.#/-])"+_NUMBER+r"(?![A-Za-z0-9_.#-])", value):
        return MISSING
    if key in {"slump", "consistency", "water_content", "water_binder", "group_count"} and re.search(r"\d\s*[/年#]\s*\d|第\d+版|\d+[A-Za-z_-]\d", value):
        return MISSING
    if key in {"strength", "permeability"}:
        grade = r"(?:[CM]\s*\d+(?:\.\d+)?(?:/\d+)?|Grade\s*\d+(?:\.\d+)?)" if key == "strength" else r"P\s*\d+"
        if not re.search(r"(?<![A-Za-z0-9_.#/-])"+grade+r"(?![A-Za-z0-9_.#-])", value, re.I):
            return MISSING
    return value


def _put(row, key, value):
    value = _typed(key, value)
    if row.get(key) in {None, MISSING}:
        row[key] = value
    elif value != MISSING and value not in row[key].split("；"):
        row[key] += "；"+value


def _literal_source(raw):
    result, scenario = [], False
    for line in raw.splitlines(keepends=True):
        if _source_name(line) is not None or line.startswith("【本轮用户要求") or line.startswith("【此前用户资料"):
            scenario = False
            result.append(line)
            continue
        if line.lstrip().startswith("|"):
            result.append("".join("\n" if c == "\n" else " " for c in line) if scenario else line)
            continue
        cursor = 0
        for boundary in [*_breaks(line), None]:
            end = boundary.start() if boundary else len(line)
            clause = line[cursor:end]
            reset = re.match(r"\s*(?:实际情况|本次实际情况|真实记录)(?:是\s*[:：]?|[:：])", clause)
            named = re.match(r"\s*(?:样品(?:名称)?|材料(?:名称)?|来源(?:文件)?|资料来源|项目(?:名称)?|工程名称)\s*[:：=]", clause)
            if reset:
                scenario = False
            elif re.search(r"示例|样例|例如|比如|假设|如果", clause) and not (named and not _UNSAFE.search(clause)):
                scenario = True
            result.append(" "*len(clause) if scenario else clause)
            if boundary:
                result.append(boundary.group())
                cursor = boundary.end()
    return "".join(result)


def _scan(raw, post):
    events = []
    matches = list(_LABEL.finditer(raw))
    explicit = [m for m in matches if re.match(r"\s*[:：=]", raw[m.end():])]
    for index, match in enumerate(explicit):
        delimiter = re.match(r"\s*[:：=]\s*", raw[match.end():])
        end = explicit[index+1].start() if index+1 < len(explicit) else len(raw)
        events.append(Fact(ALIASES[match.group().casefold()], _value(raw[match.end()+delimiter.end():end]), match.start(), end, True))
    # A requested deliverable title is not a sampler, laboratory or test.
    # Keep literal titles supplied as the value of an explicit source field.
    for title in re.finditer(r"取样送检清单|试验台账(?:骨架)?|配比报告提纲", raw):
        if not any(e.start <= title.start() < e.end for e in events):
            raw = raw[:title.start()]+" "*(title.end()-title.start())+raw[title.end():]

    def emit(key, match, group=1, *, negative=False):
        if match is None or _UNSAFE.search(raw) or any(
            e.start <= match.start(group) < e.end and (e.explicit or e.key in {"lab", "manufacturer", "source", "instrument_name"})
            for e in events):
            return
        value = match.group(group).strip()
        if not negative and _UNKNOWN.search(value):
            value = MISSING
        events.append(Fact(key, _value(value), match.start(group), match.end(group)))

    for index, match in enumerate(matches):
        if any(e.explicit and e.start <= match.start() < e.end for e in events):
            continue
        key = ALIASES[match.group().casefold()]
        tail = raw[match.end():matches[index+1].start() if index+1 < len(matches) else len(raw)]
        if key in {"manufacturer", "lab", "source", "instrument_name"}:
            # Material words and English aliases inside a proper name are
            # values, not new fields. A colon-delimited following field is
            # still a reliable boundary within a compact form line.
            end = next((label.start() for label in explicit if label.start() > match.start()), len(raw))
            tail = raw[match.end():end]
        if re.match(r"\s+|(?:是|为|用|要求|要|编号|至|还没|没给|没回)", tail) and tail.strip() and not _UNSAFE.search(raw):
            value = re.sub(r"^\s*(?:是|为|用|要求|要|编号|至)\s*", "", tail).strip()
            if _UNKNOWN.search(value) and key != "trial_status" and key != "report_status":
                value = MISSING
            events.append(Fact(key, _value(value), match.start(), match.end()+len(tail)))
    if post != "lab-mix":
        # Reserve natural institution/manufacturer spans before looking for
        # material words inside their names (e.g. 水泥质检中心).
        lab = re.search(r"(?:检测机构|检测单位|送检单位)(?:是|为)?\s*([^，,；;。\n]+)", raw)
        if lab is None:
            lab = re.search(r"(?:送检|送的)\s*([^，,；;。\n]+)", raw)
            if lab and (_SAMPLE.search(lab.group(1)) or lab.group(1).startswith("的")):
                lab = None
        emit("lab", lab)
        emit("manufacturer", re.search(r"(?:生产厂家|生产企业|厂家|生产厂|品牌)(?:是|为)?\s*([^，,；;。\n]+)", raw))
        for match in _SAMPLE.finditer(raw):
            emit("sample", match, 0)
        for match in re.finditer(r"(?:HRB|HPB)\s*\d+[A-Za-z]?|[Φφ]\s*\d+(?:\.\d+)?|P[·.]O\s*\d+(?:\.\d+)?|M\d+(?:\.\d+)?", raw):
            emit("spec", match, 0)
        emit("quantity", re.search(r"(?<![A-Za-z0-9_.#/-])("+_NUMBER+r"\s*(?:吨|方|kg|t|m[³3]))(?![A-Za-z0-9_.#-])", raw, re.I))
        emit("quantity_source", re.search(r"(磅单[^，,；;。\n]*)", raw))
        if re.search(r"进场|到货", raw):
            emit("arrival_date", re.search("("+_DATE+")", raw))
        if re.search(r"取的|取样|取了", raw):
            emit("sample_date", re.search("("+_DATE+")", raw))
        if re.search(r"送检|送的", raw):
            emit("send_date", re.search("("+_DATE+")", raw))
        emit("sampler", re.search(r"(?:取样(?:员|人)?(?:是|让)?|送样人(?:是)?)\s*([^，,；;。\s]{1,15}?)(?=去|$)", raw))
        emit("witness", re.search(r"见证人(?:还是|是)?\s*([^，,；;。\n]+)", raw))
        for key, aliases in (("batch", "批号"), ("entrust_id", "委托单(?:编号|号)?"), ("report_id", "报告(?:编号|号)")):
            emit(key, re.search(aliases+r"\s*[:：]?\s*("+_CODE+")", raw))
        emit("report_status", re.search(r"报告(?:号)?\s*(?:"+_CODE+r"\s*)?((?:已经|已)?回来了|还没回|还没收到|未收到|也没影)", raw), negative=True)
        emit("instrument", re.search(r"(?:压力机|仪器|设备)\s*("+_CODE+")", raw))
        emit("instrument_name", re.search(r"(压力机)(?=\s*"+_CODE+r")", raw))
        emit("verification_record", re.search(r"((?:上月|上个月|昨天)?(?:尚未|还没|还未|没有|未|没)?刚?检定(?:完成|完))", raw), negative=True)
    else:
        for match in re.finditer(r"(?<![A-Za-z0-9_.])(?:C|M)\d+(?:\.\d+)?(?![A-Za-z0-9_.-])", raw):
            emit("strength", match, 0)
        emit("permeability", re.search(r"抗渗(?:等级)?\s*(P\d+)", raw))
        emit("slump", re.search(r"坍落度(?:要求|要)?\s*[:：]?\s*("+_NUMBER+r"(?:\s*[±+-]\s*\d+(?:\.\d+)?)?(?:\s*(?:mm|毫米))?)", raw))
        emit("consistency", re.search(r"稠度(?:要求|要)?\s*[:：]?\s*("+_NUMBER+r"(?:\s*±\s*\d+(?:\.\d+)?)?(?:\s*(?:mm|毫米))?)", raw))
        emit("cement", re.search(r"水泥(?!砂浆)(?:是|用)?\s*([^，,；;。\n]+?)(?:的)?$", raw))
        emit("sand", re.search(r"砂(?:用|都是|是)\s*([^，,；;。\n]+?)(?:拉的)?$", raw))
        emit("trial_id", re.search(r"试(?:配|拌)(?:记录)?(?:编号)?\s*[:：]?\s*("+_CODE+")", raw))
        emit("client", re.search(r"委托人\s*([^，,；;。\n]+)", raw))
        trial = re.search(r"(已有试验数据|没有试验数据|无试验数据|还没试拌|未试拌|还没试配|未试配)", raw)
        if trial and trial.group(1) == "已有试验数据" and re.search(r"据说|听说|未核|待核|未经核验|原件未见|未见原件", raw):
            trial = re.search(r"([^，,；;。\n]*已有试验数据[^，,；;。\n]*)", raw)
        emit("trial_status", trial, negative=True)
        emit("prep", re.search(r"(预拌|现场拌合)", raw))
    for match in _SITE.finditer(raw):
        emit("site", match, 0)
    return sorted(events, key=lambda event: (event.start, event.key not in {"sample", "site", "instrument"}))


def _groups(text):
    lines = text.splitlines(keepends=True)
    index = offset = 0
    while index < len(lines):
        line = lines[index]
        if _source_name(line) is not None or line.startswith("【本轮用户要求") or line.startswith("【此前用户资料"):
            yield line.strip(), False, offset
            offset += len(line)
            index += 1
            continue
        if line.lstrip().startswith("|"):
            block, positions = [], []
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                block.append([html.unescape(cell) for cell in _table_cells(lines[index])])
                positions.append(offset)
                offset += len(lines[index])
                index += 1
            if len(block) > 1 and all(re.fullmatch(r":?-{3,}:?", c) for c in block[1]):
                keys = [ALIASES.get(c.casefold()) for c in block[0]]
                for row_no, cells in enumerate(block[2:], 2):
                    yield [Fact(key, _value(value), 0, 0, True) for key, value in zip(keys, cells) if key], True, positions[row_no]
            else:
                for row_no, cells in enumerate(block):
                    if len(cells) > 1 and cells[0].casefold() in ALIASES:
                        yield [Fact(ALIASES[cells[0].casefold()], _value(cells[1]), 0, 0, True)], False, positions[row_no]
            yield None, False, offset
            continue
        labels = [m for m in _LABEL.finditer(line) if re.match(r"\s*[:：=]", line[m.end():])]
        boundaries = _breaks(line)
        if labels and not line[:labels[0].start()].strip(" -*") and not any(b.group() in {"；", ";"} for b in boundaries):
            yield line.strip(), False, offset
        else:
            cursor = 0
            for boundary in [*boundaries, None]:
                end = boundary.start() if boundary else len(line)
                clause = line[cursor:end].strip()
                if clause:
                    yield clause, False, offset+cursor
                cursor = boundary.end() if boundary else len(line)
        offset += len(line)
        index += 1


def parse(post: str, raw: str) -> Data:
    text = _literal_source(raw)
    data = Data()
    current = instrument = None
    origin = ""
    source_zone = None

    def new(instruments=False):
        row = {"jurisdiction": source_zone or data.context.get("jurisdiction", "UNSPECIFIED"), "_origin": origin}
        (data.instruments if instruments else data.records).append(row)
        return row

    for content, table, offset in _groups(text):
        source = content if isinstance(content, str) else ""
        marker = _source_name(source)
        if marker is not None or source.startswith("【本轮用户要求") or source.startswith("【此前用户资料"):
            origin = marker if marker is not None else "本轮用户要求" if source.startswith("【本轮用户要求") else "此前用户资料（未核原件）"
            current = instrument = None
            source_zone = None
            continue
        if content is None:
            current = instrument = None
            continue
        events = _scan(content, post) if isinstance(content, str) else content
        short = re.match(r"\s*写一份(?:试验台账|取样送检清单)\s+([^，,；;。\n]+)$", source)
        if short and not events and post != "lab-mix":
            events = [Fact("sample", short.group(1).strip(), 0, 0, True)]
        if current is None and instrument is None:
            inferred = infer_jurisdiction(source)
            if inferred != "UNSPECIFIED":
                if origin:
                    source_zone = source_zone or inferred
                elif not data.records and not data.instruments:
                    data.context.setdefault("jurisdiction", inferred)
        if not events:
            if source.strip() in {"CN", "SG", "EU", "DUAL"}:
                if current is not None:
                    current["jurisdiction"] = source.strip()
                elif instrument is not None:
                    instrument["jurisdiction"] = source.strip()
                elif origin:
                    source_zone = source.strip()
                else:
                    data.context["jurisdiction"] = source.strip()
            continue
        explicit_new = bool(re.search(r"^(?:另一条|另一车|另一个|第二条|第二车|第二个)", source))
        if explicit_new:
            current = instrument = None
        primary = next((e for e in events if e.key == ("site" if post == "lab-mix" else "sample")), None)
        batch = next((e.value for e in events if e.key == "batch" and _typed("batch", e.value) != MISSING), None)
        strength = next((e.value for e in events if e.key == "strength"), None)
        instrument_id = next((e for e in events if e.key == "instrument"), None)
        if table:
            current = new() if any(e.key not in _INSTRUMENT_FIELDS for e in events) else None
            instrument = None
        elif primary:
            candidates = [r for r in data.records if r.get("_origin", "") == origin and r.get("sample" if post != "lab-mix" else "site") == primary.value]
            if batch:
                candidates = [r for r in candidates if r.get("batch") in {None, MISSING, batch}]
            if current is None or current.get("sample" if post != "lab-mix" else "site"):
                current = candidates[0] if not primary.explicit and not explicit_new and len(candidates) == 1 else new()
            instrument = None
        elif batch and current is not None and current.get("batch") not in {None, MISSING, batch}:
            candidates = [r for r in data.records if r.get("_origin", "") == origin and r.get("batch") == batch]
            current = candidates[0] if len(candidates) == 1 else new()
            instrument = None
        elif post == "lab-mix" and strength and current is not None and current.get("strength") not in {None, MISSING, strength}:
            current = new()
        if instrument_id:
            matches = [r for r in data.instruments if r.get("_origin", "") == origin and r.get("instrument") == instrument_id.value]
            if instrument is None or instrument.get("instrument") not in {None, MISSING}:
                instrument = matches[0] if not table and not instrument_id.explicit and len(matches) == 1 else new(True)
            if instrument_id.explicit and current is not None:
                _put(current, "instrument", instrument_id.value)
        collective = post == "lab-mix" and bool(re.search(r"两边都|两道都|两个都|两种都|砂都是", source))
        collective_rows = [r for r in data.records if r.get("_origin", "") == origin]
        if collective and len(collective_rows) != 2:
            current = new()
            current["_unassigned"] = "所指对象范围未明确，须核对归属"
        if primary and current is not None:
            _put(current, primary.key, primary.value)
        if instrument_id and instrument is not None:
            _put(instrument, "instrument", instrument_id.value)
        for event in events:
            key, value = event.key, event.value
            if key == "project" or key == "jurisdiction" and current is None and instrument is None:
                if key == "jurisdiction" and origin:
                    source_zone = "UNSPECIFIED" if value == MISSING else value
                else:
                    _put(data.context, key, "UNSPECIFIED" if key == "jurisdiction" and value == MISSING else value)
                continue
            if key in _INSTRUMENT_FIELDS or key == "jurisdiction" and current is None and instrument is not None:
                if instrument is None:
                    instrument = new(True)
                target = instrument
            else:
                if current is None:
                    current = new()
                target = current
            if key == "jurisdiction":
                target[key] = "UNSPECIFIED" if value == MISSING else value
            else:
                _put(target, key, value)
            if collective and key in {"sand", "trial_status"}:
                if len(collective_rows) == 2:
                    for row in collective_rows:
                        _put(row, key, value)
            if value != MISSING and key not in {"project", "jurisdiction"}:
                location = f"本轮输入第{raw[:offset].count(chr(10))+1}行"
                if origin:
                    location = origin+" · "+location
                data.evidence.append({"sample": target.get("sample") or target.get("site") or target.get("instrument") or MISSING,
                                      "field": _FIELDS[key][0], "value": _typed(key, value), "location": location})
    return data


def _table(rows, specs, *, extra=(), defaults=None):
    defaults = defaults or {}
    headers = [label for label, _ in specs]+[label for label, _ in extra]
    result = ["| "+" | ".join(headers)+" |", "| "+" | ".join("---" for _ in headers)+" |"]
    for row in rows or [{}]:
        values = [row.get(key) or defaults.get(key, MISSING) for _, key in specs]+[value for _, value in extra]
        result.append("| "+" | ".join(table_cell(value) for value in values)+" |")
    return "\n".join(result)


def _header(title, data):
    zones = {r.get("jurisdiction", "UNSPECIFIED") for r in data.records+data.instruments}
    zone = next(iter(zones)) if len(zones) == 1 else "DUAL" if len(zones - {"UNSPECIFIED"}) > 1 else data.context.get("jurisdiction", "UNSPECIFIED")
    return [f"# {title}（AI 草稿 · 内部讨论）", "", DISCLAIMER, "", "- submit_blocked=true", f"- 辖区：{zone or 'UNSPECIFIED'}", "",
            _table([data.context], (("工程 / 项目名称", "project"),)), "",
            "缺失信息保持待填；仅使用本轮已给的文字和资料片段，未核原件或现行法规。", ""]


def _evidence(data):
    return _table(data.evidence, (("用户对象", "sample"), ("记录项", "field"), ("用户证据原文", "value"), ("输入位置（非原件页码）", "location")))


def _basis(data):
    zones = {r.get("jurisdiction") for r in data.records+data.instruments} | {data.context.get("jurisdiction")}
    lines = ["取样比例、组数、异常上报时限和条款均须核对实际项目制度与现行适用依据；本轮未联网核验，不从模板补值。"]
    if zones & {"CN", "DUAL"}:
        lines.append("CN：JGJ 混凝土 / 砂浆配比规程、建设工程质量检测管理办法等仅列名称，版本与条款 unverified / UNSPECIFIED。")
    if zones & {"SG", "DUAL"}:
        lines.append("SG：SAC 实验室资料及 BCA 项目资料仅列目录名，适用条件、版本和条款 unverified / UNSPECIFIED。")
    if zones & {"EU", "DUAL"}:
        lines.append("EU：项目依据待用户提供，版本与条款 unverified / UNSPECIFIED。")
    return "\n\n".join(lines)


def _sample(data):
    columns = (("材料 / 类别", "sample"), ("规格 / 牌号", "spec"), ("厂家 / 品牌", "manufacturer"), ("材料批号", "batch"), ("用户进场数量", "quantity"),
               ("数量来源", "quantity_source"), ("工程部位", "site"), ("进场日期", "arrival_date"), ("用户取样日期", "sample_date"),
               ("取样员（未核）", "sampler"), ("见证人（未核）", "witness"), ("检测机构", "lab"),
               ("委托单 / 送检编号", "entrust_id"), ("对象辖区", "jurisdiction"), ("输入资料名 / 分组", "_origin"))
    lines = _header("取样送检清单", data)
    lines += ["## 1 封面与取样范围", "", _table(data.records, columns), "",
              "## 2 角色与签认", "", _table(data.records, (("材料批次", "batch"), ("取样人", "sampler"), ("取样单位", "sampler_unit"), ("见证人", "witness"), ("见证单位", "witness_unit")), extra=(("取样签字", "（空）"), ("见证签字", "（空）"))), "",
              "人员姓名仅记录用户输入，不推断签字或角色适用性；取样与见证角色关系须由责任人核对。", "",
              "## 3 取样依据与数量待核", "", _table(data.records, (("材料 / 类别", "sample"), ("材料批号", "batch"), ("用户取样要求来源", "requirements"), ("用户取样组数原文", "group_count"), ("用户取样频次原文", "frequency")), extra=(("本轮计算组数", "UNSPECIFIED"),)), "", _basis(data), "",
              "## 4 样品标识与封志", "", _table(data.records, (("材料 / 类别", "sample"), ("样品标识", "sample_id"), ("工程部位", "site")), extra=(("标识 / 封志核验", "本轮未执行"),)), "",
              "## 5 送检与报告追踪", "", _table(data.records, (("材料 / 类别", "sample"), ("委托单号", "entrust_id"), ("检测机构", "lab"), ("送检日期", "send_date"), ("用户报告编号", "report_id"), ("用户报告状态", "report_status")), extra=(("检测结论", "本轮未检测"),)), "",
              "## 6 异常升级路径", "", _table(data.records, (("材料 / 类别", "sample"), ("用户异常记录", "anomaly")), extra=(("升级路径", "异常资料交责任人核对并按实际制度处理"), ("处置 / 签认", "（空）"))), "",
              "## 7 用户证据位置", "", _evidence(data), "",
              "## 8 岗位接口", "", "取样与送检需实际人员执行，报告由检测机构出具；相关资料分别交试验台账、配比、仓储和项目资料责任人核对。", "",
              "## 9 禁令", "", "不编取样组数、代表数量、检测结果或材料使用许可。不混同计划、现场取样事实和检测报告。", ""]
    return "\n".join(lines)


def _record(data):
    lines = _header("试验台账骨架", data)
    lines += ["## 1 范围与文件控制", "", "编号制度、编制、审核和归档签认保持待填 / 空栏。本稿不生成正式检测报告或新编号。", "",
              "## 2 样品与委托链", "", _table(data.records, (("样品 / 试样", "sample"), ("材料规格", "spec"), ("材料批号", "batch"), ("工程部位", "site"), ("取样日期", "sample_date"), ("送检日期", "send_date"), ("委托日期", "entrusted_date"), ("委托单号", "entrust_id"), ("取样人", "sampler"), ("见证人", "witness"), ("检测机构", "lab"), ("对象辖区", "jurisdiction"))), "",
              "## 3 试验记录与报告目录", "", _table(data.records, (("样品 / 试样", "sample"), ("试验项目", "test"), ("成型日期", "formed_date"), ("用户报告编号", "report_id"), ("用户报告状态", "report_status"), ("用户仪器编号引用", "instrument")), extra=(("报告是否收到 / 原件核验", "待核"), ("结论", "本轮未检测"))), "",
              "## 4 仪器检定 / 校准", "", _table(data.instruments, (("仪器编号", "instrument"), ("仪器名称", "instrument_name"), ("用户检定证书", "verification_id"), ("用户校准证书", "calibration_id"), ("用户检定日期", "verification_date"), ("用户检定有效期原文", "expiry"), ("用户仪器检定记录", "verification_record"), ("输入资料名 / 分组", "_origin")), extra=(("有效性 / 使用状态", "本轮未核验"),)), "",
              "校准、检定和期间核查分别留证据，不互相代填。未给有效期不从检定日期倒算，也不宣布仪器可用。", "",
              "## 5 异常与缺失报告", "", _table(data.records, (("样品 / 试样", "sample"), ("用户报告编号", "report_id"), ("报告收集状态", "report_status"), ("用户异常信息", "anomaly")), extra=(("待办", "核对委托、报告原件及追溯链；缺项待收集"),)), "",
              "## 6 用户证据位置", "", _evidence(data), "",
              "## 7 依据待核", "", _basis(data), "",
              "## 8 归档与岗位接口", "", "记录、报告、委托单及仪器资料逐项追溯，不把收到报告、归档待办或用户状态当作合格结论。", "",
              "## 9 禁令", "", "不编仪器、证书或报告编号，不生成检测数据，不代检定或法定签认。", ""]
    return "\n".join(lines)


def _mix(data):
    lines = _header("配比报告提纲", data)
    lines += ["## 1 用途与性能目标", "", _table(data.records, (("工程部位 / 用途", "site"), ("用户强度等级 / 砂浆等级", "strength"), ("用户抗渗等级", "permeability"), ("用户坍落度要求", "slump"), ("用户砂浆稠度要求", "consistency"), ("用户拌合方式", "prep"), ("委托人", "client"), ("对象辖区", "jurisdiction"))), "",
              "混凝土与砂浆按各自部位和目标分行，未给的工作性单位不补写，未给拌合方式不默认现场拌合。", "",
              "## 2 草稿声明", "", "本轮未试配、未换算、未复核强度或耐久性，不构成浇筑许可。", "",
              "## 3 配合比层次与边界", "", "| 层次 | 本稿 |", "| --- | --- |",
              "| 初步（理论）配合比 | 原材和计算资料待核，本轮未计算。 |",
              "| 基准配合比 | 试拌记录、工作性和调整证据待核。 |",
              "| 试验室配合比 | 已给原文单独登记，未核试验结果或批准原件。 |"]
    claimed = any(r.get("trial_status") == "已有试验数据" for r in data.records)
    lines += ["| 施工配合比 | "+("用户声明已有试验数据；施工配比数字仍须试验室签认，本轮未换算或代批。" if claimed else "无已核工程试验数据：不给施工配合比，计算与批准栏保持待填。")+" |", "",
              "## 4 原材料与复试来源", "", _table(data.records, (("工程部位 / 用途", "site"), ("用户水泥原文 / 厂家规格", "cement"), ("用户细骨料 / 产地", "sand"), ("用户粗骨料", "stone"), ("用户粉煤灰 / 掺合料", "flyash"), ("用户外加剂", "admixture"), ("用户拌合水", "water"), ("用户原材料批号", "batch"), ("原材料复试报告编号", "raw_report_id"))), "",
              "## 5 试配记录与验证", "", _table(data.records, (("工程部位 / 用途", "site"), ("试配记录编号", "trial_id"), ("用户试配状态（未核）", "trial_status"), ("用户试验室配比原文", "lab_ratio"), ("用户含水率原文", "water_content"), ("用户水胶比原文", "water_binder"), ("归属待核", "_unassigned")), extra=(("强度 / 耐久性验证", "本轮未执行"),)), "",
              "编号或已给目标不代表试拌已做、数据完整或已批准。用户输入的配比参数只登记原文，不用于计算施工用量。", "",
              "## 6 批准资料与施工调整", "", _table(data.records, (("工程部位 / 用途", "site"), ("用户批准资料出处", "approved_source"), ("用户施工配比原文（未核）", "production_ratio")), extra=(("本轮计算施工用量", "UNSPECIFIED"), ("批准 / 签认", "（空）"))), "",
              "## 7 用户证据位置", "", _evidence(data), "",
              "## 8 编制依据待核", "", _basis(data), "",
              "## 9 禁令与接口", "", "无工程、本批原材的试验和批准证据不给施工配比；不编水胶比、砂率、每立方米用量或开盘结论。资料交试验室、取样、台账和项目责任人核对。", ""]
    return "\n".join(lines)


def build_draft(post: str, tool_name: str, text: str) -> str | None:
    if TOOLS.get(post) != tool_name:
        return None
    data = parse(post, text or "")
    return {"lab-sample": _sample, "lab-record": _record, "lab-mix": _mix}[post](data)
