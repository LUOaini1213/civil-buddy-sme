"""Literal procurement records, not prices, procurement orders or approvals.

Quoted prices retain their original units/currency. Requirements and supplier
responses are separate; no conversion, totals, ranking or lead-time arithmetic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import html
import re

from packing_assistant.jurisdiction import infer_jurisdiction
from packing_assistant.post_drafts.design_observations import _breaks
from packing_assistant.post_facts import table_cell
from packing_assistant.word_export import _table_cells as _markdown_cells

MISSING = "[A001] / UNSPECIFIED"
TOOLS = {"proc-plan": "proc-plan__schedule", "proc-compare": "proc-compare__table", "proc-vendor": "proc-vendor__eval"}
DISCLAIMER = ("本文件为 AI 草稿，仅供内部讨论与起草；不构成采购合同、付款指令、正式设计或进场许可。"
              "用户报价、证照、考察和履约记录均未核验，不代替责任人审查签认。")
_NUMBER = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_DATE = r"(?:\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?|\d{1,2}月\d{1,2}[日号])"
_UNKNOWN = re.compile(r"未提供|没提供|没给|未给|没交|未交|没定|未定|待填|待核|未知|无报价|未报价|没报价|UNSPECIFIED|TBD|N/A|还没|没有", re.I)
_UNSAFE = re.compile(r"比如|例如|假设|如果|只是示例|只是样例|不采用|不按|别按|不要|不是|[？?]")
_MATERIAL = re.compile(r"螺纹钢|钢筋|砌筑砂浆|砂浆|商品混凝土|混凝土|岩棉板|岩棉|聚氨酯板|地泵管|外墙真石漆|真石漆|机制砂|碎石|散装水泥|水泥|球墨铸铁管(?:及管件)?|卷材|电梯|钢管|铝板|玻璃|电缆|rebar", re.I)
_SPEC = re.compile(r"(?:HRB|HPB|CRB|C|M|P[·.]?O|P|DN|Φ|φ)\s?\d+(?:\.\d+)?[A-Za-z]?(?:[×x/-]\d+(?:\.\d+)?)*|\d+(?:\.\d+)?(?:mm厚|厚)")
_QTY = re.compile(r"(?:数量\s*[:：]?\s*|一共|合计|共|要|需要)\s*(" + _NUMBER + r"\s*(?:吨|方|平|㎡|m[²2³3]|米|根|件|块|张|台|套|卷|t)?)(?![A-Za-z0-9.#/-])", re.I)
_NUM_START = r"(?<![A-Za-z0-9_.#/-])"
_PRICE_LITERAL = re.compile(_NUM_START + _NUMBER + r"\s*(?:元|人民币|美元|新币|SGD\b|USD\b|CNY\b|EUR\b|/[A-Za-z一-鿿㎡]|每|一(?:吨|平|方|米|根)|含税|不含税|到场|自提)", re.I)
_QTY_LITERAL = re.compile(_NUM_START + _NUMBER + r"\s*(?:吨|公斤|千克|立方米|立方|平方米|平方|方|平|㎡|米|根|件|块|张|台|套|卷|m[²2³3]?\b|kg\b|t\b)", re.I)
_ADJACENT_QTY = re.compile(r"\s*(" + _QTY_LITERAL.pattern + r")(?=$|[，,；;。\n])", re.I)
_FIELDS = {
    "project": ("项目名称", "工程名称", "项目"), "jurisdiction": ("辖区",),
    "material": ("物资名称", "材料名称", "物资", "材料", "标的", "品名"),
    "spec": ("规格型号", "规格", "型号", "技术要求", "规格响应"), "quantity": ("用户数量", "询价数量", "供方报价数量", "报价数量", "数量", "需用量"),
    "location": ("交货地点", "需用部位", "使用部位", "部位", "到货地点"),
    "delivery": ("需要日期", "需用日期", "到货日期", "到货节点", "到货期", "交货期", "交期"),
    "supplier": ("供方名称", "供应商名称", "报价单位", "供应商", "供方", "指定供应商"),
    "kind": ("供应方式", "采购划分", "甲指/自采"), "lead": ("采购提前期", "提前期", "生产周期"),
    "source": ("数量来源", "需用单编号", "需用单", "资料来源", "报价来源", "报价单编号", "来源文件", "来源"),
    "owner": ("责任人", "责任方", "提需人", "提需部门", "需求部门", "经办"),
    "inventory": ("库存", "库存条件"), "design": ("设计确认", "设计审批", "审批条件"),
    "price": ("原报价", "报价原文", "单价", "报价", "价格"), "currency": ("币种",),
    "price_unit": ("计价单位", "报价单位口径", "币种单位"), "tax": ("税费口径", "税口径", "含税口径"),
    "freight": ("运费装卸", "运费口径", "运费", "运输条件"), "payment": ("付款方式", "付款", "结算方式"),
    "validity": ("报价有效期",), "warranty": ("质保期", "质保"), "deviation": ("偏差", "偏离说明"),
    "scope": ("供货范围", "供货品类", "经营范围"), "licence": ("营业执照", "执照"),
    "permit": ("生产许可", "生产许可证", "经营许可", "许可"), "expiry": ("证照有效期", "有效期"),
    "performance": ("类似业绩", "工程业绩", "业绩"), "factory": ("厂址", "厂区地址", "厂址与库容"),
    "visit_date": ("考察日期",), "visitor": ("考察人", "考察人员"), "visit": ("考察记录", "现场考察"),
    "quality": ("质量资料", "检测报告", "质量能力"), "service": ("售后服务", "售后", "服务网点"),
    "criterion": ("准入标准", "评价标准", "企业制度"), "status": ("用户状态", "用户名单状态", "名单状态"),
    "batch": ("供货批次", "批次",),
}
ALIASES = {alias.casefold(): key for key, aliases in _FIELDS.items() for alias in aliases}
_LABEL = re.compile("|".join(map(re.escape, sorted(ALIASES, key=len, reverse=True))), re.I)


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
    items: list[dict[str, str]] = field(default_factory=list)
    quotes: list[dict[str, str]] = field(default_factory=list)
    vendors: list[dict[str, str]] = field(default_factory=list)
    evidence: list[dict[str, str]] = field(default_factory=list)


def _value(value: str) -> str:
    value = value.strip()
    if not re.search(r"&(?:#\d+|#x[0-9a-f]+|[a-z][a-z0-9]+);$", value, re.I):
        value = value.rstrip("，,；;。")
    return MISSING if not value or _UNKNOWN.fullmatch(value) else value


def _source_name(line: str) -> str | None:
    marker = (re.match(r"^【用户上传：([^】]+)】", line)
              or re.match(r"^【所选附件：(.+?) · 字符 [\d–-]+】", line)
              or re.fullmatch(r"\s*###\s+(.+\.(?:txt|md|csv|xlsx|docx|pdf|json|log))\s*", line, re.I))
    return marker.group(1) if marker else None


def _literal_source(raw: str) -> str:
    """Narrative scenarios persist until an explicit actual-record reset.

    Examples inside table cells are field-local and handled by _typed. They
    must not hide the next independently supplied row or rename a supplier.
    """
    scenario = False
    result = []
    for line in raw.splitlines(keepends=True):
        if _source_name(line) is not None or line.strip() == "【本轮用户要求】" or line.startswith("【此前用户资料"):
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
            reset = re.match(r"\s*(?:本次实际情况|实际情况|真实记录)(?:是\s*[:：]?|[:：])", clause)
            named = re.match(r"\s*(?:供应商(?:名称)?|供方(?:名称)?|物资(?:名称)?|材料(?:名称)?|标的|项目(?:名称)?|来源(?:文件)?|报价来源)\s*[:：=]", clause)
            if reset:
                scenario = False
            elif re.search(r"比如|例如|示例|样例|假设|如果", clause) and not (named and not re.search(r"比如|例如|假设|如果|只是示例|只是样例", clause)):
                scenario = True
            result.append(" "*len(clause) if scenario else clause)
            if boundary:
                result.append(boundary.group())
                cursor = boundary.end()
    return "".join(result)


def _typed(key: str, value: str) -> str:
    value = _value(value)
    if key in {"price", "quantity", "lead"}:
        if _UNSAFE.search(value) or _UNKNOWN.search(value) or re.search(r"示例|样例", value):
            return MISSING
        if key == "price" and re.search(r"市场价|估算|编个|编一个|自行计算", value):
            return MISSING
        if key == "quantity" and not (re.fullmatch(_NUMBER, value) or _QTY_LITERAL.search(value)):
            return MISSING
        if key == "price" and not (re.fullmatch(r"(?:口头(?:报|报价)|到场价|单价|报价?)?\s*"+_NUMBER, value)
                or re.search(r"(?<![A-Za-z0-9])(?:SGD|USD|CNY|EUR|S\$|\$|€)\s*" + _NUMBER+r"(?![A-Za-z0-9_.#-])", value, re.I)
                or _PRICE_LITERAL.search(value)):
            return MISSING  # a version, date or document number is not a price
    return value


def _put(row: dict[str, str], key: str, value: str) -> None:
    value = _typed(key, value)
    previous = row.get(key)
    if previous is None or previous == MISSING:
        row[key] = value
    elif value != MISSING and value not in previous.split("；"):
        row[key] = previous + "；" + value


def _scan(raw: str, post: str, offset: int) -> list[Fact]:
    """One bounded clause; explicit source spans protect names and text values."""
    facts: list[Fact] = []
    matches = list(_LABEL.finditer(raw))
    labelled = [m for m in matches if re.match(r"\s*[:：=]", raw[m.end():])]
    for index, match in enumerate(labelled):
        key = ALIASES[match.group().casefold()]
        delimiter = re.match(r"\s*[:：=]\s*", raw[match.end():])
        start = match.end() + delimiter.end()
        end = labelled[index+1].start() if index+1 < len(labelled) else len(raw)
        value = raw[start:end].strip()
        if key in {"price", "quantity", "lead"} and (_UNSAFE.search(value) or _UNKNOWN.search(value) or key == "price" and re.search(r"市场价|估算|编个|编一个|自行计算", value)):
            value = MISSING
        facts.append(Fact(key, _value(value), offset+match.start(), offset+end, True))

    def emit(key: str, match: re.Match[str] | None, group=1, *, negative=False):
        if match is None or any(f.explicit and f.start <= offset+match.start() < f.end
                               and not (key == "expiry" and f.key == "permit"
                                        or f.key == "price" and key in {"currency", "price_unit", "tax", "freight"}) for f in facts):
            return
        if _UNSAFE.search(raw):
            return
        value = match.group(group).strip()
        if not negative and _UNKNOWN.search(value):
            value = MISSING
        facts.append(Fact(key, _value(value), offset+match.start(group), offset+match.end(group)))

    # Multi-line forms and ordinary whitespace labels share the same field
    # identities. Do not interpret an embedded professional word as a label.
    for index, match in enumerate(matches):
        key = ALIASES[match.group().casefold()]
        if any(f.explicit and f.start <= offset+match.start() < f.end for f in facts):
            continue
        tail = raw[match.end():matches[index+1].start() if index+1 < len(matches) else len(raw)]
        connector = re.match(r"\s+|(?:是|为|给的|给了|已交|未给|没给|还没|没定)", tail)
        if connector and tail.strip():
            value = re.sub(r"^\s*(?:是|为|给的)\s*", "", tail).strip()
            if _UNKNOWN.search(value):
                value = MISSING
            if not _UNSAFE.search(raw):
                facts.append(Fact(key, _value(value), offset+match.start(), offset+match.end()+len(tail)))

    # Quote forms such as "报价一：长青建材 468 含税…" are not a
    # vendor whose name is the entire quotation or a single untyped price.
    quote = re.search(r"报价[一二三四五六七八九十\d]+\s*[:：]\s*([^，,；;。\n]+?)\s+(" + _NUMBER + r")(?=\s|元|含|不含|$)(.*)", raw)
    if quote:
        facts = [f for f in facts if f.key != "price"]
        facts += [Fact("supplier", quote.group(1).strip(), offset+quote.start(1), offset+quote.end(1), True),
                  Fact("price", quote.group(2), offset+quote.start(2), offset+quote.end(2), True)]
    else:
        emit("supplier", re.search(r"(?:^|[，,：:])(?:一个是|另一个是|一家是|另一家是)\s*([^，,；;。\n]+)", raw))
        emit("supplier", re.search(r"(?:是甲指的|甲指的)\s*([^，,；;。\n]+?)(?=供货|供应|$)", raw))
        emit("supplier", re.search(r"(?:^|[，,])\s*([一-鿿A-Za-z][一-鿿A-Za-z\s]{1,24}?)(?=报(?:价)?\s*" + _NUMBER + r"|只回了)", raw))
        emit("supplier", re.search(r"([一-鿿A-Za-z][一-鿿A-Za-z\s]{1,30}?(?:有限公司|有限责任公司))", raw))
    supplier_spans = [(f.start-offset, f.end-offset) for f in facts if f.key == "supplier"]
    if post != "proc-vendor":
        materials = []
        for match in _MATERIAL.finditer(raw):
            if not any(start <= match.start() < end for start, end in supplier_spans):
                materials.append(match)
                emit("material", match, 0)
        # Literal grade/size tokens are copied, not selected from a catalogue.
        for match in _SPEC.finditer(raw):
            emit("spec", match, 0)
        emit("quantity", _QTY.search(raw))
        # A single plan item may state its quantity directly ("钢筋 20 吨").
        # Require an adjacent complete literal and an unambiguous item; never
        # reuse this shortcut for prices, supplier responses or source codes.
        if post == "proc-plan" and len(materials) == 1 and not any(f.key == "quantity" for f in facts):
            emit("quantity", _ADJACENT_QTY.match(raw, materials[0].end()))
        emit("location", re.search(r"((?:\d+[#＃]楼|地下室|塔吊基础)[^，,；;。\n]{0,12}?)(?=的|那个|(?:HRB|C|M)\d|外墙真石漆|岩棉板|混凝土)", raw))
        emit("delivery", re.search(r"(" + _DATE + r"(?:前)?)(?=[^，,；;。\n]{0,8}(?:运到|进场|到场|到货|就得来|必须))", raw))
        emit("location", re.search(r"运到([^，,；;。\n]+)", raw))
        for match in re.finditer(r"(甲供|甲指|甲限|甲控|自采|乙供)", raw):
            if not _UNKNOWN.search(raw):
                emit("kind", match)
        emit("source", re.search(r"(?:需用单(?:编号)?|数量来源)\s*[:：]?\s*([A-Za-z][A-Za-z0-9_-]+)", raw))
        emit("source", re.search(r"数量是([^，,；;。\n]+)", raw))
        emit("owner", re.search(r"([^，,；;。\n]{1,20}?)提的", raw))
        emit("price", re.search(r"(?:报价?|给|只回了(?:岩棉|聚氨酯板)?|岩棉)\s*(" + _NUMBER + r")(?![A-Za-z0-9_.-])", raw))
        emit("price_unit", re.search(r"(?:报价?|给)\s*" + _NUMBER + r"\s*((?:元|SGD|USD|CNY|S\$|\$)?(?:/[^，,；;。\s]+|一(?:吨|平|方|米|根)|每(?:吨|平|方|米|根)))", raw, re.I))
        emit("currency", re.search(r"(SGD|USD|CNY|EUR|人民币|美元|新币|新加坡元|元|S\$|\$|€)(?=\d|/|每|一|\s|$)", raw, re.I))
        emit("price_unit", re.search(_NUMBER + r"\s*((?:/)(?:吨|方|平|米|根|t|m[²2³3]))", raw, re.I))
        emit("tax", re.search(r"(不含税|含税(?:含泵送)?)", raw))
        emit("freight", re.search(r"(到场|自提|运费另计|泵送费另计|含运费|包运费|不含运费|含运输|含卸车)", raw))
        emit("payment", re.search(r"(月结\s*\d+天|收货后\d+天承兑|货到付款|预付[^，,；;。\n]+)", raw))
    else:
        emit("scope", re.search(r"(?:^|[，,])供([^，,；;。\n]+)", raw))
        emit("scope", re.search(r"([^，,；;。\n]+)那家", raw))
        emit("factory", re.search(r"(?:厂子|厂区)(?:在|设在)([^，,；;。\n]+)", raw))
        emit("factory", re.search(r"去他们([^，,；;。\n]+?厂)(?=看|考察)", raw))
        both = re.search(r"(营业执照和生产许可[^，,；;。\n]+)", raw)
        emit("licence", both)
        emit("permit", both)
        emit("licence", re.search(r"(执照(?:给了|已交)[^，,；;。\n]*)", raw))
        emit("performance", re.search(r"业绩(?:报的是|说做过|是|为)([^，,；;。\n]+)", raw))
        emit("expiry", re.search(r"有效期到\s*(" + _DATE + r")", raw))
        if re.search(r"去.*(?:看过|看了|考察)", raw) and not re.search(r"没去|未去|没看|未看", raw):
            emit("visit_date", re.search("(" + _DATE + r"|上个月|昨天|昨日)", raw))
            emit("visitor", re.search(r"(?:" + _DATE + r"|上个月|昨天|昨日)\s*([^，,；;。\n]{1,15}?)(?=去)", raw))
            emit("visit", re.search(r"((?:去|看过|看了)[^，,；;。\n]+)", raw))
        emit("service", re.search(r"(售后电话[^，,；;。\n]*)", raw), negative=True)
    # Prefer explicit fields, but keep more specific natural observations.
    return sorted(facts, key=lambda f: (f.start, f.key not in {"material", "supplier"}))


def _groups(text: str):
    """Yield atomic input rows or clauses; tables never inherit a previous row."""
    lines = text.splitlines(keepends=True)
    offset = index = 0
    while index < len(lines):
        line = lines[index]
        if _source_name(line) is not None or line.strip() == "【本轮用户要求】" or line.startswith("【此前用户资料"):
            yield line.strip(), False, offset, line.strip()
            offset += len(line)
            index += 1
            continue
        if line.lstrip().startswith("|"):
            block, block_offsets, start = [], [], offset
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                original = lines[index]
                block.append([html.unescape(cell) for cell in _markdown_cells(original)])
                block_offsets.append(offset)
                offset += len(original)
                index += 1
            if len(block) > 1 and all(re.fullmatch(r":?-{3,}:?", cell) for cell in block[1]):
                keys = [ALIASES.get(cell.casefold()) for cell in block[0]]
                for row_no, cells in enumerate(block[2:], 2):
                    yield [(key, _value(value)) for key, value in zip(keys, cells) if key], True, block_offsets[row_no], ""
            elif block:
                for row_no, cells in enumerate(block):
                    if len(cells) >= 2 and cells[0].casefold() in ALIASES:
                        yield [(ALIASES[cells[0].casefold()], _value(cells[1]))], False, block_offsets[row_no], ""
            yield None, False, offset, ""
            continue
        # Explicit form lines can include a literal comma in a specification.
        labels = [m for m in _LABEL.finditer(line) if re.match(r"\s*[:：=]", line[m.end():])]
        boundaries = _breaks(line)
        if labels and line[:labels[0].start()].strip() in {"", "-", "*"} and not any(b.group() in {"；", ";"} for b in boundaries):
            yield line, False, offset, line
        else:
            cursor = 0
            for boundary in [*boundaries, None]:
                end = boundary.start() if boundary else len(line)
                clause = line[cursor:end].strip()
                if clause:
                    leading = len(line[cursor:end]) - len(line[cursor:end].lstrip())
                    yield clause, False, offset+cursor+leading, clause
                cursor = boundary.end() if boundary else len(line)
        offset += len(line)
        index += 1


def _parse(post: str, raw: str) -> Data:
    text = _literal_source(raw)
    data = Data()
    item = quote = vendor = None
    last_supplier = ""
    named_list = False
    fresh_item = False
    quote_response = False
    input_source = ""

    def new(kind: str, values: dict[str, str] | None = None):
        row = {"jurisdiction": data.context.get("jurisdiction", "UNSPECIFIED"), "_origin": input_source, **(values or {})}
        getattr(data, kind).append(row)
        return row

    def item_reference(name: str):
        candidates = [row for row in data.items if row.get("_origin", "") == input_source
                      and (name == row.get("material") or name in row.get("material", ""))]
        return candidates[0] if len(candidates) == 1 else None

    for content, table, offset, source in _groups(text):
        marker = _source_name(source)
        if marker is not None or source.strip() == "【本轮用户要求】" or source.startswith("【此前用户资料"):
            input_source = marker if marker is not None else "本轮用户要求" if source.strip() == "【本轮用户要求】" else "此前用户资料（未核原件）"
            item = quote = vendor = None
            last_supplier = ""
            fresh_item = quote_response = False
            continue
        if content is None:
            item = quote = vendor = None
            last_supplier = ""
            fresh_item = False
            quote_response = False
            continue
        simple = re.match(r"\s*(?:写|生成|起草)一份(?:采购计划|比价表|供方评价)\s+([^，,；;。\n]{1,80})$", source)
        if isinstance(content, str):
            facts = _scan(simple.group(1) if simple else content, post, offset+simple.start(1) if simple else offset)
            pairs = [(f.key, f.value) for f in facts]
        else:
            pairs = content
            if post == "proc-compare":
                # A cell such as "SGD 38.25/m2" contains stated currency
                # and units, even when there are no separate columns.
                stated = {key for key, _ in pairs}
                for key, value in list(pairs):
                    if key == "price" and _typed(key, value) != MISSING:
                        pairs += [(fact.key, fact.value) for fact in _scan("原报价："+value, post, offset)
                                  if fact.key in {"currency", "price_unit", "tax", "freight"} and fact.key not in stated]
        if simple and post == "proc-compare" and len(simple.group(1).split()) > 1:
            parts = simple.group(1).split()
            if len(parts) > 2 and re.match(r"(?:原报价|报价|单价|价格)", parts[2]):
                pairs = [("material", parts[0]), ("supplier", parts[1]),
                         *[(key, value) for key, value in pairs if key not in {"material", "supplier"}]]
            else:
                # A list of named companies means separate rows, never one
                # vendor cell containing all names (or an unlabelled number).
                item = item_reference(parts[0]) or new("items", {"material": parts[0]})
                for name in parts[1:]:
                    if re.match(r"(?:原报价|报价|单价|价格|数量|付款|含税|运费|SGD|USD|CNY|EUR|\d)", name, re.I):
                        break
                    new("quotes", {"material": parts[0], "supplier": name, "jurisdiction": item["jurisdiction"]})
                    last_supplier = name
                quote = None
                fresh_item = quote_response = False
                named_list = True
                continue
            named_list = True
        elif not pairs:
            # A short command-tail is an explicit named subject, not all prose.
            if simple:
                tail = simple.group(1).strip()
                if post == "proc-vendor":
                    pairs = [("supplier", tail)]
                    named_list = True
                elif post == "proc-plan":
                    pairs = [("material", tail)]
                else:
                    parts = tail.split()
                    pairs = [("material", parts[0]), *[("supplier", value) for value in parts[1:]]]
                    named_list = True
            elif named_list and post in {"proc-compare", "proc-vendor"} and source.strip() in {"甲", "乙", "丙", "丁"}:
                pairs = [("supplier", source.strip())]
            elif source.strip() in {"SG", "CN", "EU", "DUAL"} and not data.context.get("jurisdiction"):
                data.context["jurisdiction"] = source.strip()
            else:
                continue
        keys = {key for key, _ in pairs}
        # Identity columns own every field in their table row regardless of
        # column order. An empty identity stays a real unassigned row.
        material = next((value for key, value in pairs if key == "material"), None)
        supplier = next((value for key, value in pairs if key == "supplier"), None)
        zone = next((value for key, value in pairs if key == "jurisdiction"), None)
        if table:
            if post == "proc-vendor":
                vendor = new("vendors")
            elif post == "proc-plan":
                item = new("items")
            else:
                if material is not None:
                    item = new("items", {"material": material})
                quote = new("quotes", {"material": material or MISSING})
        elif post == "proc-vendor" and supplier is not None:
            candidates = [row for row in data.vendors if row.get("supplier") == supplier and row.get("_origin", "") == input_source]
            vendor = candidates[0] if len(candidates) == 1 else new("vendors")
        elif post != "proc-vendor" and material is not None:
            revisited = item_reference(material)
            # A quote naming an earlier material ("只回了岩棉35") restores
            # that item. New material declarations never copy a prior quote.
            item = revisited or new("items")
            quote = None
            fresh_item = True
            quote_response = False
        if post == "proc-compare" and supplier is not None:
            quote_response = not bool(re.search(r"报价[一二三四五六七八九十\d]+\s*[:：]", source))
            previous = [row for row in data.quotes if row.get("supplier") == supplier and row.get("_origin", "") == input_source]
            if not fresh_item and not table and previous:
                if len(previous) == 1:
                    quote = previous[0]
                    item = item_reference(quote.get("material", MISSING))
                else:
                    item = None
            quote_material = item.get("material", material or MISSING) if item else material or MISSING
            matching = [row for row in data.quotes if row.get("supplier") == supplier and row.get("material") == quote_material and row.get("_origin", "") == input_source]
            quote = matching[0] if len(matching) == 1 and not table else quote if table else new("quotes", {
                "material": quote_material, "jurisdiction": item.get("jurisdiction", "UNSPECIFIED") if item else "UNSPECIFIED"})
            _put(quote, "supplier", supplier)
            last_supplier = supplier
            fresh_item = False
        elif post == "proc-compare" and "price" in keys and re.search(r"还是这家", source):
            quote = new("quotes", {"supplier": last_supplier or MISSING, "material": item.get("material", MISSING) if item else MISSING,
                "jurisdiction": item.get("jurisdiction", "UNSPECIFIED") if item else "UNSPECIFIED"})
            fresh_item = False
            quote_response = True
        elif post == "proc-compare" and "price" in keys and quote is None:
            quote = new("quotes", {"supplier": MISSING, "material": item.get("material", material or MISSING) if item else material or MISSING,
                "jurisdiction": item.get("jurisdiction", "UNSPECIFIED") if item else "UNSPECIFIED"})
        if zone is not None and not data.items and not data.quotes and not data.vendors:
            data.context["jurisdiction"] = "UNSPECIFIED" if zone == MISSING else zone
        elif not data.items and not data.quotes and not data.vendors:
            inferred = infer_jurisdiction(source)
            if inferred != "UNSPECIFIED":
                data.context.setdefault("jurisdiction", inferred)
        collective_quote = None
        for key, value in pairs:
            if key == "project" or key == "criterion" or key == "jurisdiction" and not data.items and not data.quotes and not data.vendors:
                _put(data.context, key, value)
                continue
            if post == "proc-vendor":
                if key in {"price", "currency", "price_unit", "tax", "freight", "payment", "quantity"}:
                    continue  # this post evaluates evidence, not quoted amounts
                if vendor is None:
                    vendor = new("vendors")
                target = vendor
            elif post == "proc-plan":
                if item is None:
                    item = new("items")
                target = item
            else:
                if key in {"payment", "freight", "tax"} and re.search(r"两家.*都|两家都|两份.*都", source):
                    scoped = [row for row in data.quotes if item is not None and row.get("material") == item.get("material")
                              and row.get("_origin", "") == input_source]
                    names = {row.get("supplier", MISSING) for row in scoped}
                    if len(scoped) == 2 and len(names) == 2 and MISSING not in names:
                        for row in scoped:
                            _put(row, key, value)
                    else:
                        if collective_quote is None:
                            collective_quote = new("quotes", {"supplier": MISSING,
                                "material": item.get("material", MISSING) if item else MISSING,
                                "jurisdiction": item.get("jurisdiction", "UNSPECIFIED") if item else "UNSPECIFIED",
                                "_scope_note": "集体条件的供方范围未明确；待核"})
                        _put(collective_quote, key, value)
                    continue
                request_field = ((key == "delivery" and re.search(r"需要日期|需用日期|到货节点", source))
                                 or (key == "quantity" and re.search(r"用户数量|询价数量|需用量", source))
                                 or (key == "source" and re.search(r"数量来源|需用单", source)))
                response_field = (quote is not None and (table or quote_response or supplier is not None))
                if key == "material" or request_field or key in {"quantity", "location", "delivery", "spec"} and not response_field:
                    if item is None:
                        item = new("items")
                    target = item
                elif quote is not None and (response_field or key in {"price", "tax", "freight", "currency", "price_unit", "validity", "warranty", "deviation", "source", "jurisdiction", "batch"}):
                    # A revised price or a different original quotation is a
                    # new response, not alternatives glued into the old cell.
                    if key in {"price", "source", "batch"} and quote.get(key) not in {None, MISSING, _typed(key, value)} and _typed(key, value) != MISSING:
                        quote = new("quotes", {"supplier": quote.get("supplier", MISSING),
                            "material": quote.get("material", MISSING),
                            "jurisdiction": quote.get("jurisdiction", "UNSPECIFIED")})
                    target = quote
                else:
                    if item is None:
                        item = new("items")
                    target = item
            if key == "jurisdiction":
                value = "UNSPECIFIED" if value == MISSING else value
                if value == "UNSPECIFIED" or target.get("jurisdiction") == "UNSPECIFIED":
                    target["jurisdiction"] = value
                    continue
            if key == "kind" and value != MISSING:
                methods = re.findall(r"甲供|甲指|甲限|甲控|自采|乙供", value)
                value = methods[0] if len(set(methods)) == 1 and not _UNKNOWN.search(value) else MISSING
            if key == "material" and target.get("material") and value in target["material"]:
                continue  # a short revisit is an alias, not a renamed material
            _put(target, key, value)
            if post == "proc-vendor" and key not in {"supplier", "jurisdiction"} and value != MISSING:
                location = f"本轮输入第{raw[:offset].count(chr(10))+1}行"
                if input_source:
                    location = input_source + " · " + location
                evidence = {"supplier": vendor.get("supplier", MISSING), "field": _FIELDS[key][0], "value": value, "location": location}
                if evidence not in data.evidence:
                    data.evidence.append(evidence)
    return data


def _table(rows: list[dict[str, str]], specs: tuple[tuple[str, str], ...], *, defaults=None, extra=()) -> str:
    values = defaults or {}
    headers = [label for label, _ in specs] + [label for label, _ in extra]
    result = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows or [{}]:
        cells = [row.get(key) or values.get(key, MISSING) for _, key in specs] + [value for _, value in extra]
        result.append("| " + " | ".join(table_cell(value) for value in cells) + " |")
    return "\n".join(result)


def _header(title: str, data: Data) -> list[str]:
    return [f"# {title}（AI 草稿 · 内部讨论）", "", DISCLAIMER, "", "- submit_blocked=true", "",
            _table([data.context], (("项目名称", "project"), ("明确全局辖区", "jurisdiction"))), "",
            "仅整理本轮已给文字；未核原件、未进行资格审查、价格转换、选商、倒排或审批。", ""]


def _plan(data: Data) -> str:
    lines = _header("采购计划表", data)
    columns = (("物资", "material"), ("规格", "spec"), ("供应方式", "kind"), ("用户数量", "quantity"),
               ("需用部位", "location"), ("需要日期 / 到货节点", "delivery"), ("用户提前期来源", "lead"),
               ("数量来源 / 需用单", "source"), ("指定供方", "supplier"), ("责任方 / 提需人", "owner"), ("输入资料名 / 分组", "_origin"), ("对象辖区", "jurisdiction"))
    lines += ["## 1 封面与文件控制", "", "编制人、审核人、批准人及签认日期：（空栏）。", "",
              "## 2 草稿声明", "", "甲供、甲指、甲限 / 甲控与自采分别登记；未划分项保持待划。", "",
              "## 3 合同供应方式分列", ""]
    for kind in ("甲供", "甲指", "自采", "甲限", "甲控", "乙供", "待划"):
        rows = [row for row in data.items if (row.get("kind") if row.get("kind") not in {None, MISSING} else "待划") == kind]
        lines += [f"### {kind}", "", _table(rows, columns, defaults={"kind": "待划", "lead": "UNSPECIFIED"}, extra=(("本轮状态", "用户输入待核"),)), ""]
    lines += ["## 4 需用计划来源", "", _table(data.items, (("物资", "material"), ("用户数量", "quantity"), ("数量来源 / 需用单", "source"), ("责任方", "owner"))), "",
              "## 5 物资分类", "", "分类、购置或租赁属性按用户资料复核，不从名称自动决定。", "",
              "## 6 提前期倒排", "", _table(data.items, (("物资", "material"), ("需要日期", "delivery"), ("用户提前期原文", "lead")), defaults={"lead": "UNSPECIFIED"}, extra=(("最迟提需日", "UNSPECIFIED"), ("最迟下单日", "UNSPECIFIED"))), "",
              "未给供方周期不倒推采购日期；已给周期也须按审批、生产、运输和验收条件另行核对，本轮未计算。", "",
              "## 7 到货节点", "", _table(data.items, (("物资", "material"), ("需用部位", "location"), ("到货节点", "delivery")), extra=(("到货与验收确认", "未核验"),)), "",
              "## 8 采购方式初判", "", "采购方式待企业制度，未核金额或法规适用性，不裁定程序。", "",
              "## 9 接口栏", "", _table(data.items, (("物资", "material"), ("库存条件", "inventory"), ("设计审批条件", "design")), extra=(("仓储卸货 / 试验 / 资金接口", MISSING), ("审批签认", "（空栏）"))), "",
              "## 10 禁令", "", "不编预算或单价，不把本稿作为下单或付款指令。SG 外部 CRS / GeBIZ 资料本轮未核验；其他辖区依据亦为 unverified / UNSPECIFIED。", ""]
    return "\n".join(lines)


def _compare(data: Data) -> str:
    lines = _header("询价比价表", data)
    lines += ["## 1 范围与方式定性", "", "本包采购制度、资格和程序待实际核对，不自动归类为政府采购或依法必须招标。", "",
              "## 2 草稿声明", "", "无报价不编价；不换币、不统一税口径、不乘合价、不排序。", "",
              "## 3 询价文件必备栏", "", _table(data.items, (("物资 / 标的", "material"), ("规格", "spec"), ("询价数量", "quantity"), ("需要日期", "delivery"), ("交货地点", "location"), ("询价付款要求（非供方响应）", "payment"), ("数量来源", "source"))), "",
              "## 4 邀请口径", "", "邀请来源、企业制度及有效报价不足三家原因待核；不得虚构供方或有效书面报价。", "",
              "## 5 比价表", "", _table(data.quotes, (("供方名称", "supplier"), ("物资 / 标的", "material"), ("规格响应", "spec"), ("供方报价数量", "quantity"), ("单价 / 原报价", "price"), ("币种", "currency"), ("计价单位", "price_unit"), ("税费口径", "tax"), ("运费装卸", "freight"), ("到货期", "delivery"), ("付款", "payment"), ("质保", "warranty"), ("报价有效期", "validity"), ("偏离说明", "deviation"), ("报价来源", "source"), ("供货批次", "batch"), ("输入资料名 / 分组", "_origin"), ("对象辖区", "jurisdiction"), ("归属提示", "_scope_note")), defaults={"price": "TBD", "source": "原始报价出处未提供 / 待核", "_origin": "本轮用户文字", "_scope_note": "用户输入待核"}, extra=(("合价", "TBD"), ("资料是否齐全", "待核"))), "",
              "## 6 评审口径", "", "价格、技术、税费、运输、付款、交期及有效期分别复询；可比性未确认前不作最低总价或排名结论。", "",
              "## 7 串标与形式审查", "", "仅列可核对的原始材料，未经核验不作异常事实或法律认定。", "",
              "## 8 定商建议栏", "", "待制度定。责任人 / 评审签认：（空栏）。", "",
              "## 9 接口栏", "", "与采购计划、供应商资料和付款条件逐项核对，不能把询价要求当作供方响应。", "",
              "## 10 禁令", "", "不补市场价、不代选商。GeBIZ 等外部资料本轮未核验，依据条款为 unverified / UNSPECIFIED。", ""]
    return "\n".join(lines)


def _vendor(data: Data) -> str:
    lines = _header("供应商准入 / 考察 / 评价表", data)
    base = (("供方", "supplier"), ("供货范围", "scope"))
    lines += ["## 1 封面与目的", "", "本次准入、复评、短名单或退出目的待用户 / 企业制度明确，编制与签认：（空栏）。", "",
              "## 2 草稿声明", "", "复印件、用户自报及考察叙述仅为待核资料，不代表证照真实有效或已批准准入。", "",
              "## 3 准入", "", _table(data.vendors, (*base, ("营业执照", "licence"), ("许可", "permit"), ("有效期原文", "expiry"), ("用户类似业绩", "performance"), ("对象辖区", "jurisdiction")), extra=(("初审", "待核"),)), "",
              "## 4 初审", "", "按原件、许可适用范围、用户提供名单和制度复核；本轮未联网查证，不生成证书编号。", "",
              "## 5 考察", "", _table(data.vendors, (("供方", "supplier"), ("厂址与库容", "factory"), ("考察日期", "visit_date"), ("考察人", "visitor"), ("用户考察记录", "visit")), extra=(("结论", "待核"),)), "",
              "## 6 短名单", "", _table(data.vendors, (("供方", "supplier"), ("用户名单状态（未核）", "status")), extra=(("拟纳入 / 观察待决", "待核"), ("审批签认", "（空栏）"))), "",
              "## 7 评价表头", "", _table(data.vendors, (("供方", "supplier"), ("供货批次", "batch"), ("质量资料", "quality"), ("售后 / 服务证据", "service")), extra=(("规格符合", "待核"), ("到货及时", "待核"), ("资料齐全", "待核"), ("取样复试", "待核"), ("安全文明", "待核"), ("书面报价有无", "未核验"), ("分数", "待核"), ("结论", "待核"))), "",
              "### 用户证据位置", "", _table(data.evidence, (("供方", "supplier"), ("准入 / 评价项", "field"), ("用户证据原文", "value"), ("输入位置（非原件页码）", "location"))), "",
              "## 8 动态与退出", "", "新增异常须有用户事实和证据位置；本轮不编信用等级、退出结论或制度。", "",
              "## 9 接口栏", "", _table([data.context], (("企业准入 / 评价标准", "criterion"),)), "", "采购、试验和财务按原始资料交接，不将待核名单冒充已批准名录。", "",
              "## 10 禁令", "", "不编证照、业绩、权重或分数，不代准入审批。GeBIZ / CRS 等外部资料本轮未核验，依据条款为 unverified / UNSPECIFIED。", ""]
    return "\n".join(lines)


def build_draft(expert_id: str, tool_name: str, text: str) -> str | None:
    if TOOLS.get(expert_id) != tool_name:
        return None
    data = _parse(expert_id, text or "")
    return {"proc-plan": _plan, "proc-compare": _compare, "proc-vendor": _vendor}[expert_id](data)
