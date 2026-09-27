"""数字溯源：成稿里的每个工程量，都得在本轮「看得见的东西」里找得到出处。

本项目的铁律是「数字只由工具算，模型只做路由与组织语言」。确定性路径靠代码结构
保证这一点；模型驱动的路径只能靠提示词要求，所以需要一道确定性的检查：

    untraced(draft, evidence) -> 成稿里没有出处的数量与条款号

``evidence`` 是本轮合法看到的全部文字：用户原文、CIVIL.md、工具结果（JSON 文本）、
知识库摘录、读过的工地文件。检查只回答「这个数在不在证据里」，不回答「这个数对不对」。

判定为有出处的情形（每一条都由基准里的用例支撑，见
test/benchmarks/number_provenance/cases.json 与 scripts/eval_number_provenance.py）：

    exact      证据里有同一个数值（千分位、末尾零不算差异）
    rounding   证据里的数按成稿的小数位四舍五入后相等（0.4043 → 40.4% / 约 40%）
    percent    百分数与小数互换（0.0781 ↔ 7.81%）
    scale      同一量纲内的单位换算：t↔kg、m↔cm↔mm、万元↔元；证据那一侧必须看得出
               是同一量纲（带单位，或 JSON 键名带 _kg / _mm 之类的后缀）

不算数量、因此不检查的：日期与时刻、行首序号与「第 N 步 / 表 3 / 附件 4」、
型号与标准号（C30、HRB400、40HQ、GB 50010-2010、合同号）、占位符 [A001] / UNSPECIFIED、
以及「3 个文件 / 2 行」这类对本轮自身的描述。

基准上的数字（37 例、35 个必报项、20 份干净稿；scripts/eval_number_provenance.py --variant all）：

    exact_only                    P 0.729  R 1.000
    +rounding                     P 0.761  R 1.000
    +percent                      P 0.897  R 1.000
    +scale（现行）                P 1.000  R 1.000
    去掉型号/标准号遮罩           P 1.000  R 0.971   「JGJ 130」替「130 cm」作证
    去掉计数分族                  P 1.000  R 0.914   "n_rows": 4 替「4 个柜」作证
    去掉条款号检查                P 1.000  R 0.914

拿去扫整篇文稿（civil review）时又量出两类误报，各有用例：Markdown 标题序号（「## 2 天气」读成 2 天，
修前 P 0.917）、条款号被当成裸小数再报一遍（「第7.2条」→ 7.2，修前 P 0.946）。

它查不出的一类错：数字有出处、标签贴错（实测小模型把柜体额定载重说成货物总重）。那一类靠
工具结果不给裸键、只给带标签的文字来防，见 runtime/model_loop.py 的 _pack_plan。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# 1. 量纲与单位
# ---------------------------------------------------------------------------

# (量纲, 单位写法, 换算到该量纲基准单位的系数；None 表示该量纲不做换算)
_UNIT_TABLE: Tuple[Tuple[str, str, Optional[float]], ...] = (
    ("length", r"mm|毫米", 1.0),
    ("length", r"cm|厘米", 10.0),
    ("length", r"km|公里|千米", 1_000_000.0),
    ("area", r"㎡|m²|m2|平方米|平米", None),
    ("volume", r"m³|m3|立方米|立方|方(?![案向式法面])", None),
    ("length", r"m(?![A-Za-z²³23])|米(?![以])", 1000.0),
    ("mass", r"kg|公斤|千克", 1.0),
    ("mass", r"吨|tonnes?|tons?|t(?![A-Za-z])", 1000.0),
    ("pressure", r"MPa|kPa|N/mm²|N/mm2", None),
    ("force", r"kN", None),
    ("temperature", r"℃|°C", None),
    ("time", r"日历天|calendar\s+days?|工作日|working\s+days?|天|days?|小时|hours?|分钟|个月|months?|周|weeks?|年|years?", None),
    ("money", r"亿元", 100_000_000.0),
    ("money", r"万元|万(?![一能分])", 10_000.0),
    ("money", r"元|RMB|CNY|SGD|USD|S\$", 1.0),
    ("ratio", r"%|％|‰", None),
    ("count", r"人|台|辆|柜|箱|件|根|块|片|套|层|项|次|workers?|persons?|containers?|boxes|pieces|crates?", None),
    ("count", r"个\s*(?:高柜|平柜|柜|集装箱|箱)", None),
)
_UNIT_RE = re.compile("|".join(f"(?P<u{i}>{pattern})" for i, (_c, pattern, _f) in enumerate(_UNIT_TABLE)), re.I)
_KEY_HINTS = (("_kg", "mass", 1.0), ("_t", "mass", 1000.0), ("_mm", "length", 1.0), ("_cm", "length", 10.0),
              ("_m", "length", 1000.0), ("weight", "mass", 1.0), ("length", "length", 1.0))
_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_CONTAINER = r"\d{2}\s?(?:HQ|GP|HC|OT|FR|RF)"

# ---------------------------------------------------------------------------
# 2. 不是数量的数字：先等长抹掉，位置仍对得上原文
# ---------------------------------------------------------------------------

_MASKS: Tuple[re.Pattern, ...] = tuple(re.compile(p, re.I | re.M) for p in (
    r"\[A\d+\]|UNSPECIFIED",
    # 日期、时刻
    r"\d{4}\s*年(?:\s*\d{1,2}\s*月)?(?:\s*\d{1,2}\s*[日号])?|\d{1,2}\s*月\s*\d{1,2}\s*[日号]|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}"
    r"|\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{4}|(?<![\d.:：])\d{1,2}[:：][0-5]\d(?![\d.])",
    # 标准号、型号、编号：字母贴着数字的整段
    r"(?:GB|JGJ|JTG|CJJ|DB\d*|ISO|BS|EN|ASTM|SS|CP|IEC)\s*/?\s*T?\s*\d+(?:[.\-–—]\d+)*",
    r"[A-Za-zΦφ]+[-‐]?\d[\w\-]*",
    # 序号与引用位置
    r"^\s*\d+\s*[.、)）](?!\d)|[（(]\s*\d+\s*[)）]",
    r"第\s*\d+(?:\.\d+)*\s*(?:步|章|节|页|项|款|次|轮|批|期|号|标段|部分)",
    r"(?:表|图|附件|附录|步骤|step|appendix|table|figure|section|§)\s*\d+(?:[.\-]\d+)*",
    r"\d+\s*(?:号|#)",
    r"\d+(?:\.\d+){2,}",
    # Markdown 标题的序号：「## 2 天气」「### 3.2 层间防护」里的 2、3.2 是章节号。整篇文稿里才会遇到，
    # 短回复的用例测不出来——这一条是拿护栏去扫整份日报草稿时发现的。
    r"^\s{0,3}#{1,6}\s*\d+(?:\.\d+)*",
))
_CLAUSE = re.compile(r"第\s*\d+(?:\.\d+)*\s*条|(?:clause|cl\.)\s*\d+(?:\.\d+)*", re.I)
_RATIO = re.compile(r"(?<![\d.:：])1\s*[:：]\s*\d+(?:\.\d+)?(?![\d:：])")
# 数字左右只排除 ASCII 字母数字：Python 3 的 \w 把汉字也算进去，「钢筋工12人」「期90天」
# 「币20万元」里紧贴汉字的数会整个读不出来。
_LEFT = r"(?<![A-Za-z0-9_.\-])"
_CONTAINER_COUNT = re.compile(_LEFT + r"(" + _NUMBER + r")\s*[个×xX*]\s*(?=" + _CONTAINER + r")")
_QUANTITY = re.compile(_LEFT + r"(?P<number>" + _NUMBER + r")(?P<gap>\s*)(?P<unit>" + _UNIT_RE.pattern + r")", re.I)
_BARE_DECIMAL = re.compile(_LEFT + r"(?P<number>\d+\.\d+)(?![A-Za-z0-9_.]|\s*(?:" + _UNIT_RE.pattern + r"))", re.I)
_JSON_NUMBER = re.compile(r'"(?P<key>[^"\\]{1,60})"\s*:\s*(?P<number>-?\d+(?:\.\d+)?)(?![\w.])')
_ANY_NUMBER = re.compile(_LEFT + _NUMBER + r"(?![A-Za-z])")   # 40HQ / 20GP 里的 40、20 是型号，不是数
# 工具结果是 JSON 文本，多行字符串里的换行写成反斜杠加 n：「\n12 人」里 12 的左邻是字母 n，
# 会被当成型号的一部分而读不出来。取证前把这几个转义还原成空白（等长，位置不变）。
_JSON_WHITESPACE = re.compile(r"\\[nrt]")

# 计数类的量不能只比数值：证据里的 "n_rows": 2 不能替「2 个柜」作证。计数单位按族对齐，
# JSON 证据按键名对齐；没有单位也没有对得上的键名的数，不给计数作证。
_COUNT_FAMILIES: Tuple[Tuple[str, str, str], ...] = (
    ("container", r"柜|集装箱|containers?", r"container|^n0$"),
    ("box", r"箱|boxes|crates?", r"box"),
    ("person", r"人|workers?|persons?", r"worker|person|attend|headcount"),
    ("piece", r"件|根|块|片|套|pieces", r"qty|quantity|pieces|^count$"),
    ("machine", r"台|辆", r"machine|vehicle|equipment"),
    ("storey", r"层", r"storey|floor"),
    ("item", r"项", r"item|projects?"),
    ("times", r"次", r"times|rounds?"),
)


def _count_family(unit_text: str = "", key: str = "") -> str:
    for family, units, keys in _COUNT_FAMILIES:
        if unit_text and re.search(units, unit_text, re.I):
            return family
        if key and re.search(keys, key, re.I):
            return family
    return ""


@dataclass(frozen=True)
class Quantity:
    text: str
    value: float
    decimals: int
    dimension: str      # length / mass / money / ratio / time / count / ... / "" for a bare decimal
    factor: Optional[float]
    start: int
    end: int
    kind: str = "quantity"
    family: str = ""    # for counts: container / box / person / ...


def _mask(text: str, *, identifiers: bool = True) -> str:
    out = text
    for index, pattern in enumerate(_MASKS):
        if not identifiers and index in (2, 3):
            continue
        out = pattern.sub(lambda m: " " * len(m.group(0)), out)
    return out


def _value(token: str) -> Tuple[float, int]:
    clean = token.replace(",", "")
    return float(clean), len(clean.split(".", 1)[1]) if "." in clean else 0


def _unit_of(match: re.Match) -> Tuple[str, Optional[float]]:
    for index, (dimension, _pattern, factor) in enumerate(_UNIT_TABLE):
        if match.groupdict().get(f"u{index}") is not None:
            return dimension, factor
    return "", None


def quantities(text: str, *, identifiers: bool = True) -> List[Quantity]:
    """Every engineering quantity stated in ``text``: a number with a unit, a bare decimal, a ratio."""
    raw = text or ""
    found: List[Quantity] = []
    for m in _CONTAINER_COUNT.finditer(raw):      # "7 个 40HQ" — before container types are masked away
        value, decimals = _value(m.group(1))
        found.append(Quantity(raw[m.start():m.end()].rstrip(" ×xX*个") + " 个柜", value, decimals, "count", None,
                              m.start(), m.end(), family="container"))
    masked = _mask(raw, identifiers=identifiers)
    # 条款号由条款检查单独报；这里不再把「第7.2条」里的 7.2 当成一个裸小数再报一遍。
    masked = _CLAUSE.sub(lambda m: " " * len(m.group(0)), masked)
    taken = [(q.start, q.end) for q in found]
    for m in _RATIO.finditer(masked):
        found.append(Quantity(re.sub(r"\s+", "", m.group(0)).replace("：", ":"), 0.0, 0, "slope", None, m.start(), m.end(), "ratio"))
        taken.append((m.start(), m.end()))
    for m in _QUANTITY.finditer(masked):
        if any(s <= m.start() < e for s, e in taken):
            continue
        value, decimals = _value(m.group("number"))
        dimension, factor = _unit_of(m)
        family = _count_family(m.group("unit")) if dimension == "count" else ""
        found.append(Quantity(raw[m.start():m.end()], value, decimals, dimension, factor, m.start(), m.end(), family=family))
        taken.append((m.start(), m.end()))
    for m in _BARE_DECIMAL.finditer(masked):
        if any(s <= m.start() < e for s, e in taken):
            continue
        value, decimals = _value(m.group("number"))
        found.append(Quantity(raw[m.start():m.end()], value, decimals, "", None, m.start(), m.end()))
    return sorted(found, key=lambda q: q.start)


@dataclass(frozen=True)
class _Source:
    value: float
    decimals: int
    dimension: str
    factor: Optional[float]
    family: str = ""


def _sources(evidence: Iterable[str], *, identifiers: bool = True) -> Tuple[List[_Source], str]:
    sources: List[_Source] = []
    blob_parts: List[str] = []
    for piece in evidence:
        text = _JSON_WHITESPACE.sub("  ", str(piece or ""))
        blob_parts.append(text)
        masked = _mask(text, identifiers=identifiers)
        with_units = {}
        for m in _QUANTITY.finditer(masked):
            value, decimals = _value(m.group("number"))
            dimension, factor = _unit_of(m)
            family = _count_family(m.group("unit")) if dimension == "count" else ""
            sources.append(_Source(value, decimals, dimension, factor, family))
            with_units[m.start("number")] = True
        for m in _CONTAINER_COUNT.finditer(text):
            value, decimals = _value(m.group(1))
            sources.append(_Source(value, decimals, "count", None, "container"))
        json_numbers = set()
        for m in _JSON_NUMBER.finditer(masked):
            value, decimals = _value(m.group("number").lstrip("-"))
            key = m.group("key").lower()
            hint = next(((dim, fac) for suffix, dim, fac in _KEY_HINTS if key.endswith(suffix) or suffix.strip("_") == key), ("", None))
            sources.append(_Source(value, decimals, hint[0], hint[1], _count_family(key=key)))
            json_numbers.add(m.start("number"))
        for m in _ANY_NUMBER.finditer(masked):
            if m.start() in with_units or m.start() in json_numbers:
                continue
            value, decimals = _value(m.group(0))
            sources.append(_Source(value, decimals, "", None))
    return sources, "\n".join(blob_parts)


def _same(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def _matches(value: float, decimals: int, source: _Source, *, rounding: bool) -> bool:
    if _same(value, source.value):
        return True
    return rounding and source.decimals > decimals and _same(round(source.value, decimals), value)


def _traced(q: Quantity, sources: Sequence[_Source], *, rounding: bool, percent: bool, scale: bool,
            families: bool = True) -> bool:
    for s in sources:
        if families and q.dimension == "count" and q.family and s.family != q.family:
            continue    # 计数只认同一族的出处
        if _matches(q.value, q.decimals, s, rounding=rounding):
            return True
        if percent and q.dimension == "ratio" and _matches(q.value / 100.0, q.decimals + 2, s, rounding=rounding):
            return True
        if percent and q.dimension == "" and s.dimension == "ratio" and _matches(q.value * 100.0, max(q.decimals - 2, 0), s, rounding=rounding):
            return True
        if scale and q.factor and s.factor and q.dimension == s.dimension and q.factor != s.factor:
            converted = q.value * q.factor / s.factor      # the draft's number expressed in the source's unit
            if _same(converted, s.value) or (rounding and _same(round(s.value * s.factor / q.factor, q.decimals), q.value)):
                return True
    return False


def untraced(draft: str, evidence: Iterable[str], *, rounding: bool = True, percent: bool = True,
             scale: bool = True, identifiers: bool = True, clauses: bool = True,
             families: bool = True) -> List[Dict[str, Any]]:
    """Quantities and clause references in ``draft`` that nothing in ``evidence`` supports."""
    sources, blob = _sources(evidence, identifiers=identifiers)
    compact_blob = re.sub(r"\s+", "", blob)
    flagged: List[Dict[str, Any]] = []
    for q in quantities(draft, identifiers=identifiers):
        if q.kind == "ratio":
            if q.text in compact_blob.replace("：", ":"):
                continue
        elif _traced(q, sources, rounding=rounding, percent=percent, scale=scale, families=families):
            continue
        flagged.append({"kind": q.kind, "text": q.text, "start": q.start, "end": q.end})
    if clauses:
        for m in _CLAUSE.finditer(draft or ""):
            reference = re.sub(r"\s+", "", m.group(0))
            if reference.lower() not in compact_blob.lower():
                flagged.append({"kind": "clause", "text": m.group(0), "start": m.start(), "end": m.end()})
    return sorted(flagged, key=lambda item: item["start"])


def notice(flagged: Sequence[Dict[str, Any]], *, english: bool = False) -> str:
    """The line appended to a reply whose numbers could not all be traced (in English for an English request)."""
    if not flagged:
        return ""
    names = dict.fromkeys(str(item["text"]).strip() for item in flagged)
    if english:
        return ("⚠ These numbers or clause references have no source in this turn's tool results, the user's words or "
                "the files read; do not use them unchecked: " + ", ".join(names))
    items = "、".join(names)
    return f"⚠ 以下数字或条款号在本轮的工具结果、用户原文和已读资料里找不到出处，未经核对不得使用：{items}"


#: 消融开关，scripts/eval_number_provenance.py --variant all 打印每一项的贡献。
_SHIPPED = dict(rounding=True, percent=True, scale=True, identifiers=True, clauses=True, families=True)
ABLATIONS: Dict[str, Dict[str, bool]] = {
    "exact_only": dict(_SHIPPED, rounding=False, percent=False, scale=False),
    "+rounding": dict(_SHIPPED, percent=False, scale=False),
    "+percent": dict(_SHIPPED, scale=False),
    "+scale (shipped)": dict(_SHIPPED),
    "ablate: no identifier masks": dict(_SHIPPED, identifiers=False),
    "ablate: no count families": dict(_SHIPPED, families=False),
    "ablate: no clause check": dict(_SHIPPED, clauses=False),
}
ABLATIONS["shipped"] = dict(_SHIPPED)
