"""What someone typed about a tender, taken apart: which lot, whose number, which field.

The three bid posts used to read a request with the document parser only. That parser looks for
whole lines ("★…", "…评分 25 分") and takes the first "N 日历天" in the text as *the* duration. A person
at a desk does not type lines, they type

    招标文件要求工期365日历天，我们投标函草稿工期写成了380日历天，保函还没开

and the draft came back with 380 days lost, or - when the response was mentioned first - with the
bidder's own number printed as the tender's requirement.

Three decisions, each from a failure that was reproduced:

* **Whose number it is, is decided by where it stands.** A value is the tender's unless a cue for our
  own side (我们 / 拟派 / 已转 / 照着写 …) stands before it in the same clause. So
  "工期要求300天投标函照着写的" is a requirement of 300 天 *and* a statement about our 投标函, and
  "我们先写了999天，招标要求60天" never turns 999 into a requirement. The cues are words a tender
  document does not use about itself: "财务报表" and "投标函须盖章" stay requirements.
* **Which lot it belongs to, is carried.** "一标段 … ；二标段 …" - a lot holds until another lot is
  named or the text says it speaks for all of them (两个标段 / 各标段).
* **Nothing is computed, converted or inferred.** Every value is a literal stretch of the text, as in
  packing_assistant.post_facts. A clause that carries a number or a name and fits no field is
  returned in ``unplaced`` so a writer can list it; it is never dropped.

Pure functions: no model, no disk, no verdicts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

from packing_assistant import post_facts

# ---------------------------------------------------------------------------
# values
# ---------------------------------------------------------------------------

#: "3,268.50" - and "3，268.50", which is what a scan's reading makes of it. Groups of exactly three digits; and a
#: number never starts after "digit + separator": matching only the "268.50" of "3，268.50万元" is a wrong number shown
#: as if it were read, the one thing worse than showing none.
_NUM = r"(?<!\d,)(?<!\d，)\d+(?:[,，]\d{3})*(?:\.\d+)?"
_TIME = re.compile(r"(?<![\dA-Za-z#.])" + _NUM + r"\s*(?:个?日历天|日历日|个?工作日|calendar\s*(?:days?|months?)|working\s*days?|days?|"
                   r"months?|weeks?|years?|个月|天|日|周|月|年)", re.I)
_MONEY = re.compile(r"(?<![\dA-Za-z#.])" + _NUM + r"\s*(?:万元|亿元|万|亿|元)(?!/)"
                    r"|(?<![\dA-Za-z#.])" + _NUM + r"\s*[（(]\s*(?:万元|亿元|元)\s*[)）]"       # "预算金额：86(万元)" - a platform's form
                    r"|(?:S\$|US\$|HK\$|SGD|USD|RMB|CNY|PHP|PhP|Php|₱|¥|￥|\$)\s*" + _NUM + r"(?:\s*(?:万元|万|million|mil|k|K))?")
_AREA = re.compile(r"(?<![\dA-Za-z#.])" + _NUM + r"\s*(?:万?平方米|万?平米|万?平方|万?平|㎡|m²|m2)(?![一-鿿])", re.I)
_SCORE = re.compile(r"(?<![\dA-Za-z#.])" + _NUM + r"\s*分(?![钟公包部项期批别类析布配])")
_MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
_DATE = re.compile(r"\d{4}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2}\s*[日号]?|\d{1,2}\s*月\s*\d{1,2}\s*[日号]"
                   r"|\d{1,2}(?:st|nd|rd|th)?\s+" + _MONTH + r"\.?,?\s+\d{4}|" + _MONTH + r"\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}"
                   r"|\d{1,2}/\d{1,2}/\d{4}", re.I)
_CLOCK = re.compile(r"\d{1,2}[.:]\d{2}\s*(?:a\.?m\.?|p\.?m\.?)|\d{1,2}\s*(?:am|pm)\b|\d{4}\s*hrs?\b"
                    r"|(?:上午|下午|晚上|中午)?\s*\d{1,2}\s*时(?:\s*\d{1,2}\s*分|整)?"
                    r"|(?:上午|下午|晚上|中午)?\s*(?:[01]?\d|2[0-3])\s*[:：]\s*[0-5]\d(?:\s*[:：]\s*[0-5]\d)?|(?:上午|下午|晚上|中午)\s*\d{1,2}\s*点(?:\s*半|\s*\d{1,2}\s*分)?"
                    r"|\d{1,2}\s*点(?:\s*半|\s*\d{1,2}\s*分)", re.I)
_WORKHEAD = re.compile(r"(?<![A-Za-z0-9])(?:CW|CR|ME|SY|TR|MW|RW)\d{2}(?![A-Za-z0-9])"
                       r"(?:\s*(?:grade\s*)?(?:[ABC]\d|L[1-6]|single\s+grade)(?![A-Za-z0-9]))?", re.I)
_DOC_CODE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{2,10}(?:[-_/][A-Za-z0-9]{1,8}){1,4}(?![A-Za-z0-9])")
_GRADE = re.compile(r"(?:特|[一二三四五]|[甲乙丙])级")
_EVAL_METHOD = re.compile(r"综合评估法|综合评分法|最低评审价法|经评审的最低投标价法|最低评标价法|合理低价法?|最低价法|性价比法|Price Quality Method|PQM|QFM|Quality Fee Method", re.I)
_STRUCTURE = re.compile(r"(?:框架[-—－]?核心筒|框架[-—－]?剪力墙|核心筒|框剪|剪力墙|钢框架|框架|框筒|筒中筒|框支|砖混|钢[-—－]?混凝土组合|钢筋混凝土"
                        r"|型钢混凝土|钢|装配式[一-鿿]{0,6}?|木)结构")
_FLOORS = re.compile(r"地[上下][一二三四五六七八九十百两\d]{1,4}层")

#: An addendum changes what it names and nothing else, so a value keeps where it came from.
_ADDENDUM = re.compile(r"补遗(?:文件|通知)?\s*(?:第?\s*[一二三四五六七八九十\d]+\s*号?)?|澄清(?:文件|通知|公告)|答疑纪要|修改通知|补充(?:文件|通知|公告)|变更公告")
#: a full stop ends an English sentence - not the one in "Tender No. 5", "Pte. Ltd." or "4.00 pm"
_STOP = r"(?<!\bNo)(?<!\bRef)(?<!\bLtd)(?<!\bPte)(?<!\bCo)(?<!\bSt)\.(?=\s+[A-Z一-鿿]|\s*$)"
_SENTENCE = re.compile(r"[\n。；;！!？?]|" + _STOP)
#: a comma splits clauses - not a thousands comma, not the one before a time of day ("30 October 2026, 4.00 pm")
#: a comma ends a clause - not the one inside a grouped number: "3,268.50万元", and "200，000元" as a scan or a Chinese
#: keyboard writes it (a digit before, exactly three digits after)
_CLAUSE = re.compile(r"(?<!\d)，|，(?!\d{3}(?!\d))|,(?!\d{3}(?!\d))(?!\s*\d{1,2}[.:]\d{2}\s*[aApP])")
_PIECE = re.compile(r"(?<=[。；;！!？?])|(?<=[，,])(?<!\d[，,])|(?<=\d[，,])(?!\d{3}(?!\d))|(?<=\.)(?=\s+[A-Z])")
_EDGE = " \t，,;；。、:：-—（）()"
_CONNECT = re.compile(r"^(?:[\s：:＝=\-—]|为|是|约|共计|共|计|达|有|了|的|要求|要|须|应|具备|具有|持有|不少于|不超过|不低于|不高于|至少|最高|最多|大概|大约|到|至|人民币"
                      r"|(?i:\b(?:is|are|shall\s+be|will\s+be|of|the)\b))*")
SHORT = 36  # a text value longer than this is a sentence, not a field

# ---------------------------------------------------------------------------
# lots
# ---------------------------------------------------------------------------

_CN_NUM = "一二三四五六七八九十"
_LOT = re.compile(r"(?<![A-Za-z0-9个统唯])(?:第)?([" + _CN_NUM + r"]{1,2}|\d{1,2}|[A-Z])\s*标段?(?![书准高志识题价杆注])")
_ALL_LOTS = re.compile(r"(?:两个?|[二三四五六几]个|\d个|各个?|每个?|所有|全部)标段?|标段(?:都|均)")

# ---------------------------------------------------------------------------
# whose side
# ---------------------------------------------------------------------------

#: After one of these a value is ours, not the tender's. The first group is how a person speaks of
#: their own side and nothing else. The second group is how they report progress ("已开", "还没盖",
#: "定了") - a tender document uses those words too ("已经取得许可证的，须提供复印件"), so they only
#: count in text that is somebody talking, never in a pasted excerpt (see _is_document).
_OURS_STRONG = (
    r"我们|我方|我司|我公司|本公司|咱们|自述|草稿|(?i:\b(?:we|our|ours)\b)"
    r"|投标函(?:里|上|中)?(?:写|填|报|照着写|照抄)|施组(?:里|中|上)?|技术标里|商务标里|照着写|照抄"
)
_OURS_WEAK = (
    r"(?<![须应需得])承诺|拟派|拟任|拟报|拟投入|打算报|准备报|打算派|准备派|报的|排的|写成|写的|填的|报了"
    r"|写了|填了|排了|编了"
    r"|(?<![确决指规约])定了|(?<![确决指规约])定的"
    r"|财务(?=[^，,。；;]{0,4}(?:转|付|交|缴|说))|实缴|只转|转了|交了|缴了"
    r"|已经|已开|已转|已交|已缴|已盖|已办|已附|已提交|已编|开好|盖好|办好|都在"
    r"|尚未|还没|还差|没拿到|没定|没盖|没开|没交|没办|没编|未盖|未开|未交|未办|待补|后补|明天补|谁跟"
    r"|^\s*(?:他|她|此人|该同志)"  # "我们拟派周建国，他是二级的": still about our man
)
_OURS = re.compile(_OURS_STRONG + "|" + _OURS_WEAK)
_OURS_IN_DOCUMENT = re.compile(_OURS_STRONG)
#: "还没公布 / 没提 / 没给" say the *tender side* gave nothing - not that we have not acted.
_NOT_GIVEN = re.compile(r"(?:还没|尚未|没有|没|未)(?:公布|发布|提|给|写|明确|说|载明|约定|提供|出)|待定|未知|不详")
#: A clause that obliges or threatens is the tender speaking, whatever else it holds:
#: "拟派项目经理须具备一级建造师", "未盖章的按否决投标处理".
_TENDER_SPEAKS = re.compile(r"否决|废标|无效投标|无效标|不予受理|拒收|视为|按[^，,。]{0,8}处理|不得|必须|须|应当|应具备|应具有|应提供|应满足"
                            r"|(?i:\b(?:shall|must)\b|\b(?:is|are)\s+required\s+to\b)")
_NUMBERED_LINE = re.compile(r"^\s*(?:[一二三四五六七八九十]+\s*[、.．]|\d+(?:\.\d+)*\s*[.、)）]|\d+(?:\.\d+)+\s|[（(]\s*\d+\s*[)）]|第[一二三四五六七八九十\d]+[条章节款]|[★☆＊])")


#: "帮我对下有没有废标点" / "废标检查，出个响应缺口清单": the person talking about the job. It holds the
#: word 废标, and the parser's reject rule used to file it as a P0 requirement of the tender.
_TASK_TALK = re.compile(
    r"^\s*(?:请|麻烦|烦请)?\s*(?:帮我|帮忙|给我|替我|我要|我想|老板让|经理让|领导让|出个|出一份|出一版|做个|做一份|整理成)"
    r"|^\s*(?:请|麻烦|烦请)\s*(?:解析|整理|核对|对照|检查|看|出|做)"
    r"|废标(?:检查|点|清单|对照|风险)|有没有废标|会不会废标|响应缺口|解析表|技术标目录|资料如下|如下[：:，,。]?\s*$")


#: "有没有废标点" holds 废标 and obliges nobody; these do
_OBLIGES = re.compile(r"必须|须|应当|应具备|应具有|应提供|应满足|不得|否决|无效投标|不予受理|按[^，,。]{0,8}处理")


#: office_job writes this under the heading of a job file it could not read. It is neither side speaking.
UNREAD_MARK = "（读失败）"
CUT_MARK = "（未读完）"  # office_job writes it under a file that was longer than what was read
OCR_MARK = "〔OCR〕"  # office_job writes it above a scan's text: what follows is a reading, not the text


def is_task_talk(piece: str) -> bool:
    """A clause that only says what the user wants done: no number, no ★, no obligation in it."""
    return bool(_TASK_TALK.search(piece)) and not re.search(r"\d|[★☆＊]", piece) and not _OBLIGES.search(piece)


#: A person who writes "招标要求：… / 我方情况：…" has said whose words follow. With text after the colon
#: the block is the rest of that line (up to the next marker); alone on its line it is a heading and
#: holds until the next one.
_BLOCK = re.compile(r"(?:^|(?<=[。；;！!？?\s]))\s*(?P<theirs>招标正文|招标要求|招标文件要求|招标方要求|业主要求|甲方要求)\s*[：:]\s*"
                    r"|(?:^|(?<=[。；;！!？?\s]))\s*(?P<ours>投标响应|我方响应|我方情况|我们的情况|我司情况|响应情况|投标文件响应)\s*[：:]\s*")


def _table_row(line: str) -> Optional[str]:
    """"| 计划工期 | 450日历天 |" as "计划工期：450日历天"; "" for the ruler row; None when the line is no table row.
    The cells are literal, only the bars between them are replaced."""
    body = line.strip()
    if not (body.startswith("|") and body.endswith("|") and body.count("|") >= 3):
        return None
    cells = [c.strip() for c in body.strip("|").split("|")]
    if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
        return ""
    cells = [c for c in cells if c]
    return "：".join(cells[:2]) + ("，" + "，".join(cells[2:]) if len(cells) > 2 else "") if cells else ""


def _segments(text: str, sides: str, *, tables: bool = False) -> List[Tuple[int, str, str]]:
    """(line number, stretch of that line, whose block it stands in: "" | "theirs" | "ours")."""
    out: List[Tuple[int, str, str]] = []
    line_no = 0  # counts non-empty lines, like the parser's L# references
    heading = ""
    for raw_line in (text or "").splitlines() or [""]:
        if not raw_line.strip():
            heading = ""  # a blank line ends a block that a heading opened
            continue
        line_no += 1
        row = _table_row(raw_line) if tables else None  # the parser keeps the line as it stands: it quotes literally
        if row is not None:
            if row:
                out.append((line_no, row, heading))
            continue
        marks = [] if sides == "none" else list(_BLOCK.finditer(raw_line))
        if not marks:
            out.append((line_no, raw_line, heading))
            continue
        if raw_line[:marks[0].start()].strip():
            out.append((line_no, raw_line[:marks[0].start()], heading))
        for index, mark in enumerate(marks):
            side = "theirs" if mark.group("theirs") else "ours"
            stop = marks[index + 1].start() if index + 1 < len(marks) else len(raw_line)
            body = raw_line[mark.end():stop]
            if body.strip():
                out.append((line_no, body, side))
                heading = ""
            else:
                out.append((line_no, "", side))  # a heading alone: no words, but the line still counts
                heading = side
    return out


def _sentences(line: str) -> List[str]:
    """The sentences of one line. The full stop after a clause number - "1. 投标人须…", "3.2 The Tenderer…" -
    numbers the clause; it does not end a sentence, so the number stays with what it numbers."""
    lead = _NUMBERED_LINE.match(line)
    if not lead:
        return _SENTENCE.split(line)
    parts = _SENTENCE.split(line[lead.end():])
    return [line[:lead.end()] + parts[0]] + parts[1:] if parts else [line]


def _is_document(text: str) -> bool:
    """Numbered or starred clauses on several lines: an excerpt somebody pasted, not somebody talking."""
    return sum(1 for line in (text or "").splitlines() if _NUMBERED_LINE.match(line)) >= 2


#: who is speaking when the clause names the tender's side as its subject
_TENDER_SUBJECT = re.compile(r"招标文件|招标公告|招标方|招标人|招标要求|前附表|须知|业主|甲方|建设单位|他们|对方")
_STRONG_RE = re.compile(_OURS_STRONG)


def _our_cue(clause: str, *, document: bool = False) -> "Optional[re.Match[str]]":
    """Where in this clause our own side starts speaking, if it does.

    A progress word after a tender subject is the tender's verb: in "招标文件写的是工期365日历天"
    nobody of our side wrote anything. Only a first-person cue ("…，我们排了450天") takes over from it.
    """
    cues = _OURS_IN_DOCUMENT if document else _OURS
    absent = _NOT_GIVEN.search(clause)
    subject = _TENDER_SUBJECT.search(clause)
    obliges = _TENDER_SPEAKS.search(clause)
    cue = cues.search(clause)
    while cue:
        first_person = bool(_STRONG_RE.match(clause, cue.start()))
        inside_absent = bool(absent and absent.start() <= cue.start() < absent.end())
        governed = bool(subject and subject.start() < cue.start() and not first_person)
        # a clause that obliges is the tender speaking - unless somebody says "we" before the obligation:
        # "我们必须周五前开出保函", "We shall complete the Works in 26 months"
        overruled = bool(obliges and not (first_person and cue.start() < obliges.start()))
        if not (inside_absent or governed or overruled):
            return cue
        cue = cues.search(clause, cue.end())
    return None

# ---------------------------------------------------------------------------
# topics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Topic:
    key: str
    label: str  # how a draft names the row
    aliases: Tuple[str, ...]
    kind: str  # time | money | date | area | code | text | person | method
    section: str  # which part of a tender summary it belongs to


TOPICS: Tuple[Topic, ...] = (
    Topic("project", "项目名称", ("项目名称", "工程名称", "项目名", "工程名", "Project Title", "Project Name", "Name of Project", "Tender for"), "text", "project"),
    Topic("owner", "招标人", ("招标人", "采购人", "建设单位", "发包人", "业主单位", "业主", "甲方", "Procuring Entity", "Employer", "Developer"), "text", "project"),
    Topic("tender_no", "招标编号", ("招标项目编号", "招标文件编号", "招标编号", "项目编号", "标书编号", "招标文件",
                                  "Tender Reference", "Tender Ref", "Tender No", "Quotation No", "Contract No", "ITT No", "ITQ No"), "code", "project"),
    Topic("scope", "招标范围", ("招标范围", "承包范围", "施工范围", "工程范围", "发包范围", "Scope of Works", "Scope of Work"), "text", "project"),
    Topic("area", "建筑面积", ("总建筑面积", "建筑面积", "面积", "Gross Floor Area", "GFA"), "area", "project"),
    Topic("structure", "结构形式", ("结构形式", "结构类型", "结构体系", "结构"), "text", "project"),
    Topic("deadline_bid", "投标截止", ("投标文件递交的截止时间", "投标文件递交截止", "递交的截止时间", "投标截止时间", "投标截止", "递交截止", "截标时间", "截标",
                                     "Tender closing date", "Tender closes", "Closing date", "Submission deadline"), "date", "timeline"),
    Topic("deadline_open", "开标", ("开标时间", "开标日期", "开标"), "date", "timeline"),
    Topic("deadline_query", "答疑/澄清截止", ("答疑截止", "澄清截止", "提问截止", "质疑截止", "异议截止"), "date", "timeline"),
    Topic("deadline_visit", "踏勘", ("现场踏勘", "踏勘现场", "踏勘", "Site show-round", "Site briefing", "Site visit"), "date", "timeline"),
    Topic("registration", "注册资格/工作类别", ("workhead", "BCA"), "workhead", "qualification"),
    Topic("qualification", "资质", ("资质要求", "资质条件", "企业资质", "资质等级", "资质"), "text", "qualification"),
    Topic("track_record", "类似业绩", ("类似工程业绩", "类似项目业绩", "类似业绩", "业绩要求", "同类业绩", "业绩", "contracts similar to the Project",
                                   "similar contracts", "Single Largest Completed Contract"), "text", "qualification"),
    Topic("pm", "项目经理", ("项目经理", "项目负责人", "Project Manager", "Project Director"), "person", "qualification"),
    Topic("tech_lead", "技术负责人", ("技术负责人", "项目总工", "总工"), "person", "qualification"),
    Topic("duration", "工期", ("计划工期", "招标工期", "要求工期", "总工期", "工期要求", "工期承诺", "承诺工期", "工期", "服务期限", "服务期", "Contract Period", "Contract Duration",
                                "Time for Completion", "Completion Period", "Construction Period", "completed within", "complete the Works in",
                                "complete the Works within", "completion within", "completion in"), "time", "substantive"),
    Topic("delivery", "交货期", ("交货期", "交货时间", "供货期"), "time", "substantive"),
    Topic("quality", "质量标准", ("质量标准", "质量要求", "质量目标", "质量等级", "质量承诺"), "text", "substantive"),
    Topic("validity", "投标有效期", ("投标有效期", "报价有效期", "有效期", "Tender validity period", "Tender validity", "Bid validity", "Validity period",
                                  "remains valid for", "remain valid for", "valid for"), "time", "substantive"),
    Topic("warranty", "缺陷责任期/质保期", ("缺陷责任期", "质量保证期", "质保期", "保修期", "Defects Liability Period", "Warranty period", "DLP"), "time", "substantive"),
    Topic("eval_method", "评标办法", ("评标办法", "评标方法", "评审办法", "评分办法", "Evaluation method", "Evaluation criteria", "Evaluation"), "method", "scoring"),
    Topic("price_cap", "最高限价", ("最高投标限价", "最高限价", "招标控制价", "控制价", "拦标价", "限价", "Estimated Procurement Value", "Budget ceiling", "Ceiling price"), "money", "price"),
    Topic("our_price", "我方报价", ("我方报价", "投标总报价", "投标报价", "投标总价", "总报价", "报价", "Tender sum", "Tender price", "Bid price"), "money", "price"),
    Topic("bond_validity", "保函有效期", ("保函有效期", "保证金有效期"), "date", "bond"),
    Topic("bond", "投标保证金", ("投标保证金", "投标担保", "保证金", "保函", "Tender Deposit", "Tender Bond", "Tender security", "Bid Bond", "Bid Security"), "money", "bond"),
    Topic("poa", "授权委托书", ("法定代表人授权委托书", "授权委托书", "法人授权书", "授权书"), "text", "form"),
    Topic("seal", "签章", ("签字盖章", "电子签章", "盖章", "公章", "签章"), "text", "form"),
    Topic("evidence", "已有证据", ("已有证据", "现有证据", "企业证据", "证明材料", "支撑材料"), "text", "evidence"),
    Topic("owner_person", "责任人", ("缺口责任人", "责任人", "跟进人", "对接人", "Gap owner", "Action owner", "Person in charge"), "person", "owner"),
    # anybody else named with a post beside the name; the row is called by the post as it was written
    Topic("staff", "其他人员", ("专职安全员", "安全负责人", "安全总监", "安全主管", "安全经理", "安全主任", "安全工程师", "安全员", "质量负责人", "质量经理",
                              "质量主管", "质量工程师", "质量员", "质检员", "资料员", "施工员", "技术员", "材料员", "造价员", "施工负责人", "生产负责人", "商务负责人",
                              "预算员", "测量员", "试验员", "商务经理", "生产经理", "项目副经理", "执行经理", "法定代表人", "法人代表", "授权代表",
                              "委托代理人", "授权委托人", "联系人", "经办人", "BIM负责人", "机电负责人", "总监理工程师"), "person", "qualification"),
)
#: Fields only a document lays down, row by row in its front table. They are not looked for in typed text:
#: nobody types "投标文件副本份数", and a keyword that is never typed can only steal a clause from another field.
DOCUMENT_TOPICS: Tuple[Topic, ...] = (
    Topic("consortium", "联合体投标", ("是否接受联合体投标", "联合体投标", "联合体", "Joint Ventures", "Joint Venture", "Consortium", "Consortia"), "text", "qualification"),
    Topic("copies", "投标文件份数", ("投标文件副本份数", "投标文件份数", "副本份数", "正本份数", "Number of copies", "Copies of tender", "Copies"), "text", "form"),
    Topic("binding", "装订要求", ("装订要求", "装订"), "text", "form"),
    Topic("signing", "签字盖章要求", ("签字或盖章要求", "签字盖章要求", "签章要求"), "text", "form"),
    Topic("performance_bond", "履约担保", ("履约担保", "履约保证金", "履约保函", "Performance Bond", "Performance Security", "Security Deposit"), "text", "bond"),
    Topic("alternative", "备选投标方案", ("是否允许递交备选投标方案", "备选投标方案", "备选方案", "Alternative Tenders", "Alternative Tender", "Alternative Bids", "Alternative Offers"), "text", "substantive"),
    Topic("subcontract", "分包", ("分包", "Subcontracting", "Sub-contracting", "Subcontracts"), "text", "substantive"),
    Topic("deviation", "偏离", ("偏离",), "text", "substantive"),
    Topic("open_place", "开标地点", ("开标地点", "开标时间和地点", "bid opening shall take place at", "tender opening shall take place at",
                                "place of bid opening", "place of tender opening"), "text", "timeline"),
    Topic("submit_place", "递交地点", ("递交投标文件地点", "投标文件递交地点", "递交地点"), "text", "timeline"),
    Topic("candidates", "中标候选人", ("是否授权评标委员会确定中标人", "中标候选人"), "text", "scoring"),
    Topic("budget", "采购预算", ("预算金额", "采购预算", "项目预算", "Approved Budget for the Contract"), "money", "price"),
    Topic("this_lot", "本文件所属标段", (), "text", "project"),     # read off the cover (tender_document.document_lot), never by a keyword
)
_DOCUMENT_ALIASES: List[Tuple[str, str]] = sorted(
    ((alias, topic.key) for topic in DOCUMENT_TOPICS for alias in topic.aliases), key=lambda pair: -len(pair[0]))
_DEADLINE_QUERY_NAMES = ("提出问题的截止时间", "澄清招标文件的截止时间", "要求澄清招标文件的截止时间", "答疑截止时间", "提问截止时间")
#: what the rows of a front table are called when the purchase is not a works tender (政府采购: 磋商 / 谈判 / 询价):
#: 供应商 for 投标人, 响应文件 for 投标文件, 服务期限 for 工期
_DOCUMENT_ROW_NAMES: Tuple[Tuple[str, str], ...] = (
    (r"(?i:deadline for (?:bid |tender )?submission|(?:bid|tender) submission deadline|closing date(?: and time)?)", "deadline_bid"),
    (r"(?i:name of the (?:bidding process|contract|works|project)|contract title|title of (?:the )?(?:contract|works))", "project"),
    (r"(?i:(?:invitation for bids?|IFB|ITT|tender|bid|contract|RFP|RFQ) (?:no\.?|number|ref(?:erence)?\.?)(?: no\.?)?)", "tender_no"),
    # before "X人的资格要求" below: the person in charge is not the bidder
    (r"(?:拟派|拟任|拟投入)?(?:项目负责人|项目经理)(?:的)?(?:任职|执业)?(?:资格)?(?:要求|条件|资格)?", "pm"),
    (r"(?:拟派|拟任|拟投入)?(?:技术负责人|项目总工)(?:的)?(?:任职|执业)?(?:资格)?(?:要求|条件|资格)?", "tech_lead"),
    (r"(?:响应文件)?开启时间|开标时间", "deadline_open"),
    (r"评[分审标]方法(?:及标准)?|评审办法|定标方式", "eval_method"),
    (r"质量保修期|保修期限?|质保期限?", "warranty"),
    (r"[一-鿿]{2,8}文件(?:提交|递交)地点|(?:提交|递交)[一-鿿]{2,8}文件地点", "submit_place"),
    (r"(?:项目)?预算金额(?:和|及)最高限价", "price_cap"),
    (r"(?:提交|递交)?备选[一-鿿/／]{0,8}方案", "alternative"),            # 是否允许提交备选投标/响应方案
    (r"第[一二三两]个?信封[一-鿿]{0,4}开标时间", "deadline_open"),
    (r"第[一二三两]个?信封[一-鿿]{0,4}开标地点", "open_place"),
    # by the SHAPE of the name, not by one procurement method's words: 投标 / 响应 / 磋商 / 比选申请 / 报价 … 文件, 有效期, 人
    (r"[一-鿿]{0,4}服务期限?|合同履行期限|履约期限|履行期限|供货期限?|交付期限?", "duration"),
    (r"(?!保函|保证金|担保|证书|许可)[一-鿿]{2,6}有效期", "validity"),
    (r"[一-鿿]{2,8}文件(?:的)?(?:正、?副本)?(?:份数|数量)(?:[及和与、][一-鿿]{2,10})?", "copies"),     # 响应文件份数及装订要求
    (r"(?:供应商|成交供应商|[一-鿿]{2,4}人)(?:的)?分包", "subcontract"),                  # 是否允许投标人分包
    (r"可选择(?:或调整)?的(?:投标|响应|报价)(?:和报价)?|选择性(?:投标|报价)(?:方案)?", "alternative"),
    (r"评[审标分]标准(?:和|及|与)(?:方法|办法)|评[审标分](?:方法|办法)(?:和|及|与)标准", "eval_method"),
    (r"(?:提交|递交|送达|投标)地点", "submit_place"),
    (r"(?:评标委员会)?推荐(?:的)?中标候选人(?:的)?(?:人数|数量|名额)?", "candidates"),
    (r"偏差|偏离", "deviation"),
    (r"(?:供应商|[一-鿿]{2,6}人)(?:的)?(?:资格|资质)(?:要求|条件)", "qualification"),
    (r"(?:首次)?[一-鿿]{2,8}文件(?:的)?(?:递交|提交)(?:的)?截止时间|(?:提交|递交)[一-鿿]{2,8}文件(?:的)?截止时间", "deadline_bid"),
    (r"[一-鿿]{0,4}特定资格要求", "qualification"),
    (r"合同履约期限", "duration"),
    (r"比选人|询价人|谈判人|磋商人|发包人名称", "owner"),
    (r"总监理工程师(?:的)?(?:要求|资格|条件)?|总监(?:的)?(?:要求|资格)|项目总监(?:的)?(?:要求|资格)?", "pm"),
    (r"[一-鿿]{0,6}最高限价|[一-鿿]{0,6}控制价|[一-鿿]{0,6}拦标价", "price_cap"),
    (r"质疑(?:与|和|及)?(?:澄清|答疑)?|澄清(?:与|和|及)质疑", "deadline_query"),
    (r"(?:[一-鿿]{0,6})(?:质量)?保修期|质保期限?", "warranty"),
    (r"(?!履约|质量|预付款)[一-鿿]{2,4}保证金", "bond"),
    (r"评审(?:方法|办法)", "eval_method"),
    (r"(?:响应|报价)文件(?:的)?(?:签署|签章|签字盖章)", "signing"),
)
_TOPIC = {t.key: t for t in TOPICS + DOCUMENT_TOPICS}


def document_topic(name: str) -> str:
    """The field a front-table row (or a label inside its content) is about, by its name. The keyword has
    to be what the name is about: 招标人书面澄清的时间 is not the 招标人."""
    name = re.sub(r"[（(][^）)]*[)）]", "", (name or "").strip())           # 第一个信封（商务及技术文件）开标时间
    name = re.sub(r"^(?:本项目|本工程|本次招标|招标工程|招标项目)?是否(?:接受|允许|组织|召开|需要)?", "", name)   # "是否接受联合体" is about 联合体
    if re.search(r"分包人|分包商|分包单位|第三人", name):
        return ""   # 对分包人的资格要求 is about whoever the work is sublet to, not about the bidder
    if re.search(r"预付款|质量保证金|质保金|工资保证金|农民工|廉洁保证金|保修金|低价风险", name):
        return ""   # the guarantees of the contract are not the bid bond
    for alias in _DEADLINE_QUERY_NAMES:
        if alias in name:
            return "deadline_query"
    if re.fullmatch(r"答疑|澄清|质疑|提问|答疑澄清|澄清答疑|疑问提出", name):
        return "deadline_query"
    for pattern, key in _DOCUMENT_ROW_NAMES:
        if re.fullmatch(pattern, name):
            return key
    if "资质条件" in name:
        return "qualification"
    for alias, key in _DOCUMENT_ALIASES:
        if (alias in name or (alias.isascii() and alias.lower() in name.lower())) and len(alias) * 2 >= len(name):
            return key
    for start, end, key in _topic_hits(name):
        rest = name[:start] + name[end:]
        if (end - start) * 2 >= len(name) or re.fullmatch(
                r"(?:投标人|供应商|拟派|拟任|拟投入|本项目|的)*(?:任职|执业)?(?:资格)?(?:要求|条件|时间|金额|标准|期限|资格|(?:和|及)地点)?", rest):
            return key
    return ""
def loose_document_topic(name: str) -> str:
    """The field a loosely worded name is about - "转包与分包", "磋商响应文件有效期为 90 天": any field word in it. Only
    where a name HAS to be read this way (the one-cell rows of a two-column front table)."""
    name = re.sub(r"^\s*(?:\d+(?:\.\d+)*\s*[.．、]?|[（(]\s*\d+\s*[)）])\s*", "", name or "")
    found = document_topic(name)
    if found:
        return found
    for pattern, key in _DOCUMENT_ROW_NAMES:
        if re.search(pattern, name):
            return key      # the shape of a row name anywhere in the phrase: "包2合同履行期限为90日历天"
    low = name.lower()
    for alias, key in _DOCUMENT_ALIASES:
        if alias in name or (alias.isascii() and alias.lower() in low):
            return key
    hits = _topic_hits(name)
    return hits[0][2] if hits else ""


_ALWAYS_OURS = frozenset({"our_price", "evidence", "staff"})  # ours by nature (a named person is ours)
_NO_SIDE = frozenset({"owner_person"})  # neither the tender's nor a response
_STATEMENT_ONLY = frozenset({"poa", "seal"})  # what matters is what is said about them, not a value
_KIND_RE = {"time": _TIME, "money": _MONEY, "area": _AREA, "date": _DATE, "workhead": _WORKHEAD}
_LINE_LABELS = {"工程": "project", "项目": "project", "Project": "project", "project": "project", "PROJECT": "project",
                "点名专项": "special", "专项": "special", "危大工程": "special",
                "专项方案": "special", "必须编制的专项": "special"}

_ALIAS_TABLE: List[Tuple[str, str]] = sorted(
    ((alias, topic.key) for topic in TOPICS for alias in topic.aliases), key=lambda pair: -len(pair[0]))

_COMPOUND_SURNAMES = ("欧阳|司马|上官|诸葛|皇甫|令狐|司徒|东方|慕容|尉迟|长孙|夏侯|公孙|端木|轩辕|宇文|独孤|南宫|呼延|闻人|澹台|万俟|"
                      "濮阳|淳于|单于|太叔|申屠|公羊|赫连|钟离|宗政|司空|司寇|子车|颛孙|第五")
_SURNAME = "(?:" + _COMPOUND_SURNAMES + "|[" + post_facts._SURNAMES + "])"
_AFTER_NAME = r"(?=$|[\s，,;；。、（(）)]|但|的|跟|负责|担任|任|为|是|和|及|与|已|还|尚|等|牵头|对接|盯|管|来|去|在|做|同志|持|具)"
_PERSON_BOUNDED = re.compile(_SURNAME + r"[一-鿿]{1,2}?" + _AFTER_NAME)
_PERSON_LOOSE = re.compile(_SURNAME + r"[一-鿿]{1,2}")
_DEPARTMENT = re.compile(r"[一-鿿]{2,5}(?:部|科|处|室|中心)(?=" + _SURNAME + ")")
_FOLLOW = re.compile(r"(?:让|由|交给|交|归|找|请|先?挂在?)\s*(?:[一-鿿]{2,5}(?:部|科|处|室|中心))?\s*(" + _SURNAME + r"[一-鿿]{1,2}?)"
                     r"(?=跟进|跟|负责|盯|对接|牵头|落实|管|$|[\s，,;；。、])")


_FOLLOW_AFTER = re.compile(r"(?:缺口|责任|补证|跟进|对接|这块|事项)[^，,。；;]{0,6}?(" + _SURNAME + r"[一-鿿]{1,2}?)(?=负责|跟进|跟|盯|牵头|对接|落实|管)")


#: "Rachel Lim", "Tan Wei Ming": two to four capitalised words, none of them a word of the trade
_EN_NAME = re.compile(r"(?<![A-Za-z])[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}(?![A-Za-z])")
_EN_NOT_A_NAME = frozenset("project manager director engineer professional registered form tender works contract period our we the gap "
                           "owner action person charge safety officer site senior resident technical quality construction authority "
                           "board corporation pte ltd method statement price management health campus phase".split())


def _person(text: str) -> str:
    """A name, literal. A department before it ("商务部王丽娟") is not the name."""
    body = _DEPARTMENT.sub(lambda m: " " * len(m.group(0)), text)
    match = _PERSON_BOUNDED.search(body) or _PERSON_LOOSE.search(body)
    if match:
        return match.group(0)
    if not re.search(r"[一-鿿]", body):
        for found in _EN_NAME.finditer(body):
            if not any(word.lower() in _EN_NOT_A_NAME for word in found.group(0).split()):
                return found.group(0)
    return ""


@dataclass(frozen=True)
class Mention:
    """One thing the text says about one topic."""

    topic: str
    side: str  # "tender" | "ours" | "none"
    lot: str  # "" = every lot / not said
    value: str  # literal; "" when the clause only makes a statement ("保函还没开")
    note: str  # the literal clause the value or statement came from
    line: int  # 1-based line of the text
    not_given: bool = False  # "限价还没公布": the text says the tender has not given this
    origin: str = ""  # "补遗1号" when the sentence speaks of an addendum, as written
    role: str = ""  # topic "staff" only: the post as written - "专职安全员"
    ref: str = ""  # a document's own locator - "第二章 前附表 3.4.1"; "" for typed text, where the line is all there is

    @property
    def label(self) -> str:
        return _TOPIC[self.topic].label

    @property
    def section(self) -> str:
        return _TOPIC[self.topic].section


@dataclass(frozen=True)
class ScorePoint:
    name: str  # literal; "" when the text gave a score with nothing to call it
    score: str  # "35分", literal
    lot: str
    note: str
    line: int
    ref: str = ""  # where it stands in a document: "第三章 前附表 2.2.4（1）（第142页）"


@dataclass(frozen=True)
class Special:
    name: str  # "深基坑专项方案", literal
    detail: str  # "挖深11.8米", literal; "" when none
    lot: str
    note: str
    line: int
    not_given: bool = False  # "专项没提"
    ref: str = ""


@dataclass
class TenderFacts:
    mentions: List[Mention] = field(default_factory=list)
    scores: List[ScorePoint] = field(default_factory=list)
    specials: List[Special] = field(default_factory=list)
    lots: List[str] = field(default_factory=list)  # as written, in order of first mention
    lot_scopes: Dict[str, str] = field(default_factory=dict)  # "一标段" -> "顶管加检查井"
    unplaced: List[str] = field(default_factory=list)
    jurisdiction: str = "UNSPECIFIED"  # CN | SG | EU | DUAL, only when the text says so; never a default country
    addenda_unread: List[Dict[str, str]] = field(default_factory=list)  # changes an addendum makes where the reader cannot follow

    def of(self, topic: str, *, side: Optional[str] = None, lot: Optional[str] = None) -> List[Mention]:
        return [m for m in self.mentions if m.topic == topic and (side is None or m.side == side)
                and (lot is None or m.lot == lot)]

    def first(self, topic: str, *, side: Optional[str] = None, lot: Optional[str] = None) -> Optional[Mention]:
        found = [m for m in self.of(topic, side=side, lot=lot) if m.value]
        return found[0] if found else None

    def to_dict(self) -> Dict[str, object]:
        return {
            "schema": "tender.facts.v1",
            "jurisdiction": self.jurisdiction,
            "lots": list(self.lots),
            "lot_scopes": dict(self.lot_scopes),
            "mentions": [{"topic": m.topic, "label": m.label, "side": m.side, "lot": m.lot, "value": m.value,
                          "note": m.note, "line": m.line, "not_given": m.not_given, "origin": m.origin, "role": m.role,
                          **({"ref": m.ref} if m.ref else {})}
                         for m in self.mentions],
            "scores": [vars(s) for s in self.scores],
            "specials": [vars(s) for s in self.specials],
            "unplaced": list(self.unplaced),
            **({"addenda_unread": list(self.addenda_unread)} if self.addenda_unread else {}),
        }


# ---------------------------------------------------------------------------
# scoring points and named specials
# ---------------------------------------------------------------------------

_SCORE_LEAD = re.compile(
    r"^(?:.*?(?:评标办法|评分办法|评分表|评分标准|评审表|技术标评分|商务标评分|技术标|商务标|评分点|评分项|评分)(?:(?:里面?|中|内|上)[：:，,\s]*|[：:，,]\s*)"
    r"|其中|另外|还有|以及|和|及|与|、|\s)+")
# "计" and "共" are not connectors here: they end 施工组织设计 and 公共
_SCORE_TAIL = re.compile(r"(?:那块|这块|那部分|这部分|部分|这项|那项|一项|方面)?(?:最重|最高|最多|较重)?的?(?:评分|分值|权重|满分|得分|分数)?(?:为|是|占|合计|最高)?\s*$")
_HAZARD = r"(?:深基坑|基坑|高支模|高大模板|模板支撑|支模|脚手架|起重吊装|吊装|爆破|拆除|顶管|盾构|暗挖|降水|边坡|幕墙|钢结构安装|大体积混凝土|沟槽支护|支护)"
_SPECIAL = re.compile(r"[一-鿿A-Za-z0-9#]{0,12}?" + _HAZARD + r"[一-鿿]{0,6}?(?:专项施工方案|专项方案|专项)")
_SPECIAL_LEAD = re.compile(r"^.*?(?:点名|要求|必须|须|还要|需要|要|需)\s*(?:要|须)?\s*(?:编制|编写|编|做|出|提交|报)\s*(?:一份|一个)?")
_SPECIAL_DETAIL = re.compile(
    r"(?:挖深|开挖深度|基坑深度|坑深|支模高度|搭设高度|搭设跨度|跨度|板厚|梁高|净高|高度|埋深|覆土|顶进长度|单件重量?|起重量|吊重)\s*(?:约|为|是|达|最大|最深|最高){0,3}\s*"
    + _NUM + r"\s*(?:mm|cm|km|m|毫米|厘米|米|吨|t|kN)?", re.I)


def _clean_name(text: str) -> str:
    return text.strip(_EDGE + "、和及与")


def _score_points(clause: str, previous: str, lot: str, line: int) -> List[ScorePoint]:
    """"施工组织设计占35分" / "施工组织设计 25 分、项目管理机构 10 分" / "施工方案那块最重，占18.5分"."""
    points: List[ScorePoint] = []
    cursor = 0
    for match in _SCORE.finditer(clause):
        head = clause[cursor:match.start()]
        cursor = match.end()
        name = _clean_name(_SCORE_TAIL.sub("", _SCORE_LEAD.sub("", head)))
        if not name and not points and previous:
            # "施工方案那块最重，占18.5分": the name is what the clause before was about
            name = _clean_name(_SCORE_TAIL.sub("", _SCORE_LEAD.sub("", previous)))
        if _width(name) > 24 or re.search(r"\d", name) or _OURS.search(name) or _LOT.search(name):
            name = ""
        points.append(ScorePoint(name, re.sub(r"\s+", "", match.group(0)), lot, clause.strip(_EDGE), line))
    return points


def _special_name(text: str) -> str:
    return _clean_name(_SPECIAL_LEAD.sub("", text))


# ---------------------------------------------------------------------------
# the walk
# ---------------------------------------------------------------------------


# Aliases are immutable vocabulary. Compile once rather than escape and look up
# hundreds of expressions again for every sentence of a full tender document.
_ALIAS_PATTERNS = tuple((alias, key, alias.isascii(),
                         re.compile(r"(?<![A-Za-z])" + re.escape(alias) + r"(?![A-Za-z])", re.I)
                         if alias.isascii() else re.compile(re.escape(alias)))
                        for alias, key in _ALIAS_TABLE)


def _topic_hits(clause: str) -> List[Tuple[int, int, str]]:
    taken = [False] * len(clause)
    hits: List[Tuple[int, int, str]] = []
    folded = clause.casefold()
    for alias, key, ascii_alias, pattern in _ALIAS_PATTERNS:
        # a quick "its letters are not in this sentence" skip; casefold, not lower, so re.I's Unicode matches
        # (the long s, the Kelvin sign) are not skipped
        if (alias.casefold() not in folded) if ascii_alias else (alias not in clause):
            continue
        # Keep re.I's Unicode case behavior for English aliases, and keep the
        # original string for exact evidence offsets and longest-alias priority.
        for match in pattern.finditer(clause):
            start, end = match.span()
            if any(taken[start:end]):
                continue
            after = clause[end:end + 3]
            if alias == "结构" and not re.match(r"\s*(?:[：:]|为|是|\s)", after):
                continue  # "剪力墙结构" / "优质结构" is a value, not a label
            if alias == "招标文件" and not _DOC_CODE.match(clause, end):
                continue  # only "招标文件HD-2026-SG-018": the number standing right after it
            if alias == "报价" and re.match(r"\s*分", after):
                continue  # 报价分 is a scoring point
            if alias == "有效期" and (not re.match(r"\s*(?:为|是|要|[：:])?\s*\d", clause[end:end + 6])
                                   or re.search(r"(?:证|证书|执照|许可|保函|保证金|担保|资质|注册|合同|业绩)的?$", clause[:start])):
                continue  # bare 有效期 is the bid's only with a number right after it and no certificate or guarantee before it
            if alias == "面积" and re.search(r"(?:使用|占地|用地|绿化)$", clause[:start]):
                continue
            if alias in ("业绩", "资质") and re.search(r"(?:评分|分值)$", clause[:start]):
                continue
            for index in range(start, end):
                taken[index] = True
            hits.append((start, end, key))
    return sorted(hits)


def _value(kind: str, region: str) -> str:
    """The literal value of a topic's kind inside the stretch of text that follows its keyword."""
    if kind in _KIND_RE:
        match = _KIND_RE[kind].search(region)
        if not match:
            return ""
        text = match.group(0).strip()
        if kind == "date":
            clock = _CLOCK.search(region, match.end())
            if clock and clock.start() - match.end() <= 3:
                text = region[match.start():clock.end()].strip()
        return text
    if kind == "code":
        match = _DOC_CODE.search(region)
        return match.group(0) if match else ""
    if kind == "person":
        return _person(region)
    if kind == "method":
        match = _EVAL_METHOD.search(region)
        return match.group(0) if match else ""
    text = _CONNECT.sub("", region).strip(_EDGE + "、.")
    for opened, closed in (("（", "）"), ("(", ")")):
        if text.count(opened) > text.count(closed) and closed in region[region.find(text) + len(text):][:1]:
            text += closed
    return text if _width(text) <= SHORT else ""


def _width(text: str) -> float:
    """How much room a value takes: a Latin letter is half a Chinese character."""
    return sum(1.0 if ord(ch) > 0x2E7F else 0.5 for ch in text)


def _person_adjacent(region: str) -> str:
    """The name standing right after a post: "专职安全员张伟", "质量负责人：李娜", "资料员由王芳担任". Not a name
    somewhere later in the clause - "安全员今天请假" names nobody."""
    body = re.sub(r"^(?:[\s：:＝=｜|]|我方|我们|由|是|为|拟派|拟任|暂定为?|定的是|定了|报的是|报的|用的是)*", "", region)
    body = _DEPARTMENT.sub("", body, count=1) if _DEPARTMENT.match(body) else body
    match = _PERSON_BOUNDED.match(body) or _PERSON_LOOSE.fullmatch(body.strip(_EDGE))
    return match.group(0) if match else ""


def _before(kind: str, clause: str, start: int, floor: int) -> str:
    """"75万银行保函": a number right before its keyword, when nothing follows the keyword."""
    if kind not in _KIND_RE:
        return ""
    found = [m for m in _KIND_RE[kind].finditer(clause[floor:start])]
    return found[-1].group(0).strip() if found and start - (floor + found[-1].end()) <= 6 else ""


_REQUIRES = re.compile(r"^\s*(?:[一二三四五六七八九十\d]+\s*[、.．)）]\s*)?(?:[★☆＊*]\s*)?(?:投标人|供应商|申请人|承包人|投标单位)?\s*"
                       r"(?:须|应当|应|必须|需|要求|得是|得有)\s*(?:同时)?\s*(?:具备|具有|持有|取得|满足|提供)?\s*(?:有效的)?")


def _before_text(kind: str, clause: str, floor: int, end: int) -> str:
    """"投标人须具备建筑工程施工总承包二级及以上资质": a document puts the keyword last.

    Only for a requirement sentence (须 / 应 / 必须 …); the value is the literal run up to and
    including the keyword, without the "投标人须具备" that introduces it.
    """
    if kind != "text":
        return ""
    lead = _REQUIRES.match(clause[floor:])
    if not lead or not lead.group(0).strip() or not re.search(r"须|应|必须|需|要求|得是|得有", lead.group(0)):
        return ""
    text = clause[floor + lead.end():end].strip(_EDGE)
    return text if 4 <= len(text) <= SHORT else ""


def _requirement_text(region: str) -> str:
    """For 项目经理 / 技术负责人 on the tender's side the value is a qualification, not a name."""
    text = re.sub(r"(?:而|但|可|却|且|并|不过)$", "", _CONNECT.sub("", region).strip(_EDGE + "、"))
    ok = _GRADE.search(text) or re.search(r"建造师|注册|职称|工程师|证书|[ABC]证|资格", text)
    return text if ok and len(text) <= SHORT else ""


_NAME_LEAD = re.compile(
    r"^(?:.*?(?:帮我|帮忙|麻烦|请|让我|要我|叫我)\s*(?:把|将|对|给|查下|查一下|看下|看一下|对下|对一下|理一下|出一份|出个|出|做|写|整理|解析)?"
    r"|(?:刚|才|已经?)?(?:拿到|收到|接到|看了|翻了|看过)|(?:查|看|对|理|过)一?下|把|将|对|给|关于|针对|就)\s*")
_NAME = r"[一-鿿A-Za-z0-9#（）()·\-]"
#: what a project's name ends in, when nobody wrote "项目名称："
_NAME_END = (r"(?:工程|项目|大厦|广场|厂房|车间|仓库|住宅|公寓|花园|家园|小区|地块|[" + _CN_NUM + r"\d]+期"
             r"|管廊|跨河桥|大桥|立交|桥|隧道|泵站|水厂|道路|公路|大道|基地|园区|场馆|体育馆|车站|航站楼)")
_NAME_TAIL = r"(?:第?[" + _CN_NUM + r"\d]{1,2}标段)?(?:(?:施工|监理|设计|勘察|采购|总承包)?的?招标.*|的.*)?$"
_NAMED = re.compile(r"(?:项目|工程)(?:名称)?(?:是|为|叫|名为)\s*(" + _NAME + r"{4,36})$")
_NAME_SHAPE = re.compile(r"^(" + _NAME + r"{2,34}?" + _NAME_END + r")" + _NAME_TAIL)
_NAME_BEFORE_LOT = re.compile(r"^(" + _NAME + r"{6,36}?)第?[" + _CN_NUM + r"\d]{1,2}标段")
_NOT_A_NAME = re.compile(r"专项|技术标|解析|目录|缺口|我们|咱们|招标|投标|标段|这次|这个|本次|那个|分了|分为|一下|帮我|[是有在要把被给]"
                         r"|^(?:另一个|另外|还有|以及|关于|工作地点|地点|地址|驻|赴|去|到)"
                         r"|^(?:单位|分部|分项|子分部|单项|本|该|此|全部|所有|其他|成本|试验|检测|检验|培训|科研|管理|在建|类似|同类|相关|重点|新开)?(?:工程|项目)$")


def _project_name(sentence: str) -> str:
    """A project named without a label. Four shapes, each one a person was seen to use; anything else is
    left out - a wrong name in the first row of a draft is worse than "未在原文检出".

      项目是云栖小学新建工程            said outright
      青龙湖大道提升改造工程施工招标文件   ends the way a project's name ends, then 招标 / a lot / nothing
      城东安置房二期总建筑面积38600平     the same, standing right before the first field of its clause
      滨江路市政道路改造二标段           whatever stands before its lot
    """
    for raw in re.split(r"[，,：:]", sentence):
        clause = raw.strip(_EDGE)
        said = _NAMED.search(clause)
        if said and not _NOT_A_NAME.search(said.group(1)):
            return said.group(1)
        body = _NAME_LEAD.sub("", post_facts.strip_command(clause))
        if not body or _LOT.match(body):
            continue  # "第1标段是主厂房" describes a lot, not the project
        hits = _topic_hits(body)
        head = body[:hits[0][0]] if hits and hits[0][0] >= 4 else body
        found = _NAME_SHAPE.match(head) or (_NAME_BEFORE_LOT.match(body) if not hits or hits[0][0] >= 6 else None)
        name = found.group(1) if found else ""
        if 4 <= len(name) <= SHORT and not _NOT_A_NAME.search(name) and not _topic_hits(name) and not _our_cue(name):
            return name
    return ""


def _lot_key(match: "re.Match[str]") -> str:
    return match.group(1)


def _extract_document(text: str) -> TenderFacts:
    """A tender document says where things are: tools/tender_document.py reads its structure, and the
    fields come from the rows of its front table - not from whatever running text stands next to a keyword."""
    from packing_assistant.jurisdiction import infer_jurisdiction
    from packing_assistant.tools import tender_document

    doc = tender_document.read(text)
    facts = TenderFacts()
    facts.mentions = list(tender_document.field_mentions(doc))
    facts.lots = list(dict.fromkeys(m.lot for m in facts.mentions if m.lot))
    facts.scores = [ScorePoint(name, score, "", piece.text[:160], piece.line, piece.ref) for name, score, piece in tender_document.scores(doc)]
    facts.specials = [Special(name, detail, "", piece.text[:160], piece.line, ref=piece.ref) for name, detail, piece in tender_document.specials(doc)]
    facts.addenda_unread = [{"text": piece.text[:200], "ref": piece.ref} for piece in tender_document.addenda_unread(doc)]
    facts.jurisdiction = infer_jurisdiction(text)
    return facts


def extract(text: str, *, sides: str = "auto") -> TenderFacts:
    """``sides="none"`` when the caller already knows every word is the tender's (a file given the
    tender role): then no cue is looked for at all. ``"auto"`` reads the text as somebody talking,
    unless it looks like a pasted excerpt, where only first-person cues count."""
    from packing_assistant.jurisdiction import infer_jurisdiction
    from packing_assistant.tools import tender_document

    if sides != "ours" and tender_document.is_document(text or ""):
        return _extract_document(text or "")

    facts = TenderFacts()
    facts.jurisdiction = infer_jurisdiction(text or "")
    document = _is_document(text)
    lot = ""
    lot_forms: Dict[str, str] = {}
    seen_clauses: List[str] = []
    for line_no, raw_line, block_side in _segments(text, sides, tables=True):
        if raw_line.lstrip().startswith((UNREAD_MARK, CUT_MARK, OCR_MARK)):
            continue
        # "标签：" at the start of a line governs the whole line: "已有证据：同类学校业绩一项，合同都在"
        line_topic: Optional[str] = None
        line_body = raw_line
        head = re.match(r"\s*(?:[-*+·•]\s*|\d+[.、)）]\s*|[（(]\d+[)）]\s*)?([^：:，,。；;\s]{1,14})\s*[：:]\s*", raw_line)
        if head:
            label = head.group(1)
            hits = _topic_hits(label)
            if label in _LINE_LABELS:
                line_topic, line_body = _LINE_LABELS[label], raw_line[head.end():]
            elif hits and hits[0][0] == 0 and hits[0][1] == len(label):
                line_topic, line_body = hits[0][2], raw_line[head.end():]
        if line_topic == "project":
            value = _value("text", line_body)
            if value and _width(value) <= SHORT:
                facts.mentions.append(Mention("project", "tender", "", value, raw_line.strip(_EDGE), line_no))
            continue
        if line_topic == "special":
            clauses = [c.strip(_EDGE) for c in _CLAUSE.split(line_body) if c.strip(_EDGE)]
            if clauses and not _NOT_GIVEN.search(clauses[0]):
                detail = _SPECIAL_DETAIL.search(line_body)
                rest = "，".join(clauses[1:])
                said = detail.group(0).strip() if detail else (rest if len(rest) <= SHORT else "")
                facts.specials.append(Special(clauses[0], said, lot, raw_line.strip(_EDGE), line_no))
                seen_clauses.extend(clauses)
            continue
        label_is_ours = bool(head and line_topic and _OURS.search(head.group(1)))
        first_of_line = line_topic is not None
        for sentence in _sentences(line_body):
            if not sentence.strip():
                continue
            carried: Optional[str] = line_topic
            previous_clause = ""
            origin, origin_from = "", 0
            for clause in _CLAUSE.split(sentence):
                if not clause or not clause.strip(_EDGE):
                    continue
                shown = raw_line.strip(_EDGE) if (first_of_line and len(raw_line.strip()) <= 60) else clause.strip(_EDGE)
                seen_clauses.append(shown)
                addendum = _ADDENDUM.search(clause)
                if addendum and not origin:
                    origin, origin_from = re.sub(r"\s+", "", addendum.group(0)), len(facts.mentions)
                # ---- which lot
                lot_hits = list(_LOT.finditer(clause))
                keys = list(dict.fromkeys(_lot_key(m) for m in lot_hits))
                for match in lot_hits:
                    if len(match.group(0).strip()) > len(lot_forms.get(_lot_key(match), "")):
                        lot_forms[_lot_key(match)] = match.group(0).strip()
                if _ALL_LOTS.search(clause) or len(keys) > 1:
                    lot = ""
                elif keys:
                    lot = keys[0]
                # ---- whose side, by position
                absent = _NOT_GIVEN.search(clause)
                cue = None if sides == "none" else _our_cue(clause, document=document)
                if block_side == "ours":
                    cue = re.match(r"", clause)  # under "投标响应：" every word is ours, a 须 included
                elif block_side == "theirs":
                    cue = None
                if label_is_ours and cue is None and sides != "none":
                    cue = re.match(r"", clause)  # the label said whose: everything on this line is ours
                hits = [] if line_topic in _ALWAYS_OURS | _STATEMENT_ONLY else _topic_hits(clause)
                if line_topic and hits and not all(key == line_topic for _s, _e, key in hits):
                    # inside a labelled line only a second "标签：" starts a new field
                    hits = [h for h in hits if re.match(r"\s*[：:]", clause[h[1]:h[1] + 2]) or h[2] == line_topic]
                placed = False
                claimed = False
                labelled_field = first_of_line and line_topic in _TOPIC and line_topic not in _STATEMENT_ONLY
                if labelled_field and all(key == line_topic for _s, _e, key in hits):
                    # "招标人：石桥镇人民政府" / "保证金：要求75万银行保函": the label names the field
                    topic = _TOPIC[line_topic]
                    ours = bool(cue and not clause[:cue.start()].strip(_EDGE)) or line_topic in _ALWAYS_OURS
                    side = "none" if line_topic in _NO_SIDE else "ours" if ours else "tender"
                    if line_topic in ("pm", "tech_lead"):
                        value = _person(clause) if side == "ours" else _requirement_text(clause)
                        if not value and side == "tender" and _person(clause):
                            side, value = "ours", _person(clause)
                    else:
                        value = _value(topic.kind, clause)
                    gone = bool(not value and absent and side == "tender")
                    if line_topic == "staff":
                        value = _person_adjacent(clause)
                        if value:
                            facts.mentions.append(Mention("staff", "ours", lot, value, shown, line_no, role=head.group(1)))
                            placed = True
                    elif value or gone or side == "ours":
                        facts.mentions.append(Mention(line_topic, side, lot, value, shown, line_no, not_given=gone))
                        placed = True
                    hits = []
                    carried = line_topic
                    first_of_line = False
                    previous_clause = clause
                    if placed:
                        continue
                first_of_line = False
                for index, (start, end, key) in enumerate(hits):
                    topic = _TOPIC[key]
                    stop = hits[index + 1][0] if index + 1 < len(hits) else len(clause)
                    if key == "staff":
                        name = _person_adjacent(clause[end:stop])
                        if name:
                            facts.mentions.append(Mention("staff", "ours", lot, name, shown, line_no, role=clause[start:end]))
                            placed = True
                        continue  # never carried on: what follows a post without a name belongs to nobody
                    if key in _STATEMENT_ONLY:
                        if cue or absent or re.search(r"须|应|必须|需", clause):
                            side = "ours" if cue else "tender"
                            facts.mentions.append(Mention(key, side, lot, "", shown, line_no))
                            placed = True
                        carried = key
                        break
                    ours_first = bool(cue and cue.start() <= start)
                    split_at = cue.start() if (cue and start < cue.start() < stop) else None
                    side = "none" if key in _NO_SIDE else "ours" if (ours_first or key in _ALWAYS_OURS) else "tender"
                    region = clause[end:split_at if split_at is not None else stop]
                    if key in ("pm", "tech_lead") and side != "ours":
                        value = _requirement_text(region)
                        if not value and _person(_CONNECT.sub("", region)[:4] or ""):
                            side, value = "ours", _person(region)  # "技术负责人 周海燕": a named person is ours
                    else:
                        value = _value(topic.kind, region)
                        if value and topic.kind == "text" and _NOT_GIVEN.search(value):
                            value = ""  # "资质没提" says nothing was given; "没提" is not a qualification
                        if not value and not claimed:
                            floor = hits[index - 1][1] if index else 0
                            value = _before(topic.kind, clause, start, floor) or _before_text(topic.kind, clause, floor, end)
                    claimed = bool(value)
                    gone = bool(not value and absent and side == "tender" and (split_at is None))
                    if value or gone:
                        facts.mentions.append(Mention(key, side, lot, value, shown, line_no, not_given=gone))
                        placed = True
                    elif side == "ours" and split_at is None:
                        facts.mentions.append(Mention(key, "ours", lot, "", shown, line_no))
                        placed = True
                    if split_at is not None:
                        rest = clause[split_at:stop]
                        kind = "person" if key in ("pm", "tech_lead") else topic.kind
                        rest_value = "" if kind in ("text", "method") else _value(kind, rest)
                        # "三标保证金68万保函已经开好了": our part is "保函已经开好了", not the requirement again
                        begin = start if end == split_at else split_at  # the keyword right before the cue is its subject
                        said = clause[begin:stop].strip(_EDGE)
                        later = any(k == key for _s, _e, k in hits[index + 1:])
                        if rest_value or not later:  # "保证金要80万我们保函开的是80万": 保函 speaks for our side
                            facts.mentions.append(Mention(key, "ours", lot, rest_value, said if len(said) >= 6 else shown, line_no))
                        placed = True
                    carried = key
                # ---- a clause with no keyword of its own continues the topic before it
                if not hits and carried and carried not in ("project", "special", "staff"):
                    topic = _TOPIC[carried]
                    stripped = _LOT.sub("", clause).strip(_EDGE)
                    lead = _OURS.search(stripped)
                    starts_with_cue = bool(cue and lead and not stripped[:lead.start()].strip(_EDGE + "但却就也都的"))
                    if carried in _ALWAYS_OURS | _STATEMENT_ONLY:
                        more = _value(topic.kind, clause) if carried in _ALWAYS_OURS else ""
                        if more:
                            facts.mentions.append(Mention(carried, "ours", lot, more, shown, line_no))
                            placed = True
                        elif carried in _STATEMENT_ONLY and (cue or absent):
                            facts.mentions.append(Mention(carried, "ours" if cue else "tender", lot, "", shown, line_no))
                            placed = True
                    else:
                        kind = "person" if (carried in ("pm", "tech_lead") and cue) else topic.kind
                        if carried in ("pm", "tech_lead") and not cue:
                            value = _requirement_text(clause)
                            whole = _PERSON_LOOSE.fullmatch(clause.strip(_EDGE))
                            if not value and whole:
                                # "项目经理用老贾，贾国庆": a clause that is nothing but a name, right after
                                # the post was named, is who holds it - a person, so ours
                                facts.mentions.append(Mention(carried, "ours", lot, whole.group(0), shown, line_no))
                                placed = True
                        else:
                            value = "" if kind in ("text", "method") else _value(kind, clause)
                        if value:
                            side = "none" if carried in _NO_SIDE else "ours" if cue else "tender"
                            facts.mentions.append(Mention(carried, side, lot, value, shown, line_no))
                            placed = True
                        elif cue and (starts_with_cue or line_topic):
                            facts.mentions.append(Mention(carried, "ours", lot, "", shown, line_no))
                            placed = True
                # ---- things said without a label
                structure = _STRUCTURE.search(clause)
                if structure and not any(key == "structure" for _s, _e, key in hits):
                    facts.mentions.append(Mention("structure", "tender", lot, structure.group(0), shown, line_no))
                    placed = True
                if re.search(r"谁(?:来)?(?:跟进|跟|负责|管|盯|牵头)|责任人[^，,。]{0,6}(?:没|未|待)", clause):
                    facts.mentions.append(Mention("owner_person", "none", lot, "", shown, line_no, not_given=True))
                    placed = True
                follow = _FOLLOW.search(clause) or _FOLLOW_AFTER.search(clause)
                if follow and not any(key == "owner_person" for _s, _e, key in hits):
                    facts.mentions.append(Mention("owner_person", "none", lot, follow.group(1), shown, line_no))
                    placed = True
                if _SCORE.search(clause):
                    points = _score_points(clause, previous_clause, lot, line_no)
                    facts.scores.extend(points)
                    placed = placed or bool(points)
                special = _SPECIAL.search(clause)
                if special:
                    name = _special_name(special.group(0))
                    if name and name not in ("专项方案", "专项施工方案", "专项") and not _NOT_GIVEN.search(clause[special.end():]):
                        if not any(s.name == name and s.lot == lot for s in facts.specials):
                            detail = _SPECIAL_DETAIL.search(sentence, sentence.find(clause) + special.end())
                            facts.specials.append(Special(name, detail.group(0).strip() if detail else "", lot,
                                                          shown, line_no))
                        placed = True
                elif re.search(r"专项", clause) and absent and not hits:
                    facts.specials.append(Special("", "", lot, shown, line_no, not_given=True))
                    placed = True
                if not placed and lot_hits and not hits:
                    # "二标段是泵站土建": what a lot consists of
                    rest = _CONNECT.sub("", clause[lot_hits[0].end():]).strip(_EDGE)
                    if rest and len(rest) <= SHORT and not re.search(r"都投|一起投|别串|分开", rest) and len(keys) == 1:
                        facts.lot_scopes.setdefault(keys[0], rest)
                        placed = True
                if not placed and re.search(r"\d", _LOT.sub(" ", _NUMBERED_LINE.sub("", clause, count=1))):
                    facts.unplaced.append(shown)  # a number nobody placed; "3. …" or "1标" alone is only a numbering
                previous_clause = clause
            if origin:
                # "补遗1号只把工期改成450天，保证金没提": what the rest of the sentence says is the addendum's
                facts.mentions[origin_from:] = [
                    replace(m, origin=origin)
                    for m in facts.mentions[origin_from:]]
    # ---- a project named without a label
    if not facts.first("project"):
        for sentence in (s for line in (text or "").splitlines() for s in _sentences(line)):
            name = _project_name(sentence)
            if name:
                facts.mentions.insert(0, Mention("project", "tender", "", name, name, 1))
                break
    # ---- lots as the text wrote them; one lot alone is the project's lot, not a split
    forms = {key: (form if form.endswith("段") else form + "段") for key, form in lot_forms.items()}
    facts.lots = list(forms.values())
    single = len(forms) == 1
    relot = (lambda key: "") if single else (lambda key: forms.get(key, key))
    facts.mentions = [replace(m, lot=relot(m.lot)) for m in facts.mentions]
    facts.scores = [ScorePoint(s.name, s.score, relot(s.lot), s.note, s.line) for s in facts.scores]
    facts.specials = [Special(s.name, s.detail, relot(s.lot), s.note, s.line, s.not_given) for s in facts.specials]
    facts.lot_scopes = {forms.get(key, key): scope for key, scope in facts.lot_scopes.items()}
    used = [s.detail for s in facts.specials if s.detail] + [m.value for m in facts.of("project") if m.value]
    facts.unplaced = [c for c in dict.fromkeys(facts.unplaced) if not any(u in c for u in used)]
    # The net under everything above. A clause may have one number filed and another not
    # ("招标文件HD-018写的是工期365日历天": 365 filed, the document number not). Every number and
    # every document code of the text is either inside something that was filed, or its clause
    # is listed - so a writer that prints the fields and ``unplaced`` has lost nothing.
    filed = [m.value for m in facts.mentions] + [s.score for s in facts.scores] + list(facts.lot_scopes.values())
    filed += [x for s in facts.specials for x in (s.name, s.detail)] + list(facts.unplaced)
    for clause in dict.fromkeys(seen_clauses):
        body = _LOT.sub(" ", _NUMBERED_LINE.sub("", clause, count=1))
        tokens = [m.group(0) for m in _DOC_CODE.finditer(body)]
        tokens += [m.group(0) for m in re.finditer(r"(?<![\dA-Za-z#.\-])" + _NUM + r"(?![\d#])", _DOC_CODE.sub(" ", body))]
        if any(not any(re.search(r"(?<![\d.])" + re.escape(tok) + r"(?![\d.])", kept) for kept in filed) for tok in tokens):
            facts.unplaced.append(clause)
    return facts


# ---------------------------------------------------------------------------
# splitting a typed request into the tender's words and ours
# ---------------------------------------------------------------------------


def _line_sides(text: str, sides: str) -> List[Tuple[List[str], List[str]]]:
    """Per non-empty line: (the literal pieces that are the tender speaking, the pieces that are ours).

    One rule for the parser and the fact walk: block markers first ("招标要求：" / "我方情况：", as a
    heading or in front of a line), cues inside what no block claims. A line nobody of our side
    speaks in, and that holds no task talk, comes back whole as the one piece it is - a pasted
    document parses exactly as before.
    """
    document = _is_document(text)
    grouped: Dict[int, List[Tuple[str, str]]] = {}
    for line_no, stretch, side in _segments(text, sides):
        grouped.setdefault(line_no, []).append((stretch, side))
    out: List[Tuple[List[str], List[str]]] = []
    for line_no in sorted(grouped):
        theirs: List[str] = []
        ours: List[str] = []
        for stretch, side in grouped[line_no]:
            line = stretch.strip()
            if not line or line.startswith((UNREAD_MARK, CUT_MARK, OCR_MARK)):
                continue
            if sides == "none" or side == "theirs":
                theirs.append(line)
                continue
            if side == "ours":
                ours.append(line)
                continue
            kept: List[str] = []
            touched = False
            for piece in _PIECE.split(line):
                if not piece.strip():
                    continue
                if not document and is_task_talk(piece):
                    touched = True
                    continue
                cue = _our_cue(piece, document=document)
                if cue is None:
                    kept.append(piece)
                    continue
                touched = True
                if piece[:cue.start()].strip(_EDGE):
                    kept.append(piece[:cue.start()])
                ours.append(piece[cue.start():].strip())
            theirs += [line] if not touched else [x.strip().strip("，,") for x in kept if x.strip().strip("，,")]
        out.append((theirs, ours))
    return out


def split_sides(text: str, *, sides: str = "auto") -> Tuple[str, str]:
    """(what the tender asks, what we say about ourselves), line by line.

    For the document parser: it must never read "我们投标函写了999日历天" as a tender requirement.
    """
    pairs = _line_sides(text, sides)
    return ("\n".join("，".join(theirs) for theirs, _ours in pairs if theirs).strip(),
            "\n".join("".join(ours) for _theirs, ours in pairs if ours).strip())


def tender_pieces(text: str, *, sides: str = "auto") -> List[List[str]]:
    """Per non-empty line of the text: the literal pieces of it that are the tender speaking.

    Each piece is a literal stretch of its line, so a requirement quoted from one can be found again
    in the source. A line that is all ours is an empty list - it still counts, so L# keeps meaning
    "the n-th non-empty line of what was typed".
    """
    return [theirs for theirs, _ours in _line_sides(text, sides)]


def has_our_side(text: str) -> bool:
    return bool(_OURS.search(text or ""))
