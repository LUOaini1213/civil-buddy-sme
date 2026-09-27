"""Reading a tender DOCUMENT - eight chapters and a hundred pages - not a typed request.

tools/tender_facts.py reads what somebody types: whose number, which lot, which field. Pointed at a real
招标文件 it goes wrong in a way of its own - every stretch of running text that stands next to the word
招标人 becomes a 招标人 ("将予以拒收", "在监理人收到后56天内将进度应付款支付给承包人"), and the sixty pages of
contract conditions are full of numbers of days that are nobody's 工期.

A tender document says where things are. The values are laid down in two tables - 投标人须知前附表 and
评标办法前附表 - row by row, each under its clause number; the notice in chapter one repeats some of them
as labelled lines. The clauses that get a bid rejected are sentences with a small vocabulary (否决, 不予受理,
拒收, 无效投标) scattered through chapters two, three and seven. So this module reads structure first:

    read(text)            chapters, numbered clauses, table rows - every piece knows where it stands
    is_document(text)     at least two chapters, or a front table
    front_rows(doc)       the rows of the two front tables: number, name, content
    field_mentions(doc)   fields from the front table first, the notice's labelled lines for what it lacks;
                          where the two disagree both are kept and the disagreement is said
    rejections(doc)       every sentence that gets a bid rejected or refused, the clause it cites beside it
    obligations(doc)      front-table rows that lay down a 须 / 不得 on form, sealing, delivery
    scores / specials / forms

Every value is a literal stretch of the document; every piece carries ``ref`` - "第二章 前附表 3.4.1",
"第三章 3.1.5" - which is what a person needs to find it again. Nothing is judged.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

_CN = "一二三四五六七八九十百"
_CHAPTER = re.compile(r"^#*\s*(第[" + _CN + r"\d]+(?:章|部分|篇|卷|册)|(?:SECTION|PART|CHAPTER|VOLUME|Section|Part|Chapter|Volume)\s+(?:\d{1,2}|[IVX]{1,4}|[A-H])(?![A-Za-z]))\s*[.:：–—-]?\s*(.*)$")
_TABLE_ROW = re.compile(r"^\|.*\|$")
_RULER = re.compile(r":?-{2,}:?")
#: "3.4 投标保证金" / "1. 总则" / "2.4 计划工期：540日历天。" at the start of a paragraph
_UNIT = "米天日年月万元个份名人次分项级倍吨时号％%页条款章"
_LEAD_NUMBER = re.compile(r"^#*\s*(\d+(?:\.\d+){0,3})[.．、]?\s+(?=\S)|^#*\s*(\d+)[.．、]\s*(?=[^\d\s])"
                          r"|^#*\s*(\d+(?:\.\d+){1,3})(?=[一-鿿])(?![" + _UNIT + r"])")   # "1.1.1根据…": a scan's reading drops the space
#: "… 。3.4.2 投标人不按 …" inside a paragraph: a clause number at the start of a sentence
_INLINE_NUMBER = re.compile(r"(?:(?<=[。；;])|(?<=[。；;]\s))(\d+(?:\.\d+){1,3})(?:\s+(?=\S)|(?=[一-鿿])(?![" + _UNIT + r"]))")
# "…as non-responsive. Any bid not accompanied by…", "…specified in the BDS. Any bid received after…" - not "No. 5", "Art. 3"
_SENTENCE_END = re.compile(r"(?<=。)|(?<![\s(][A-Z][a-z])(?<![\s(][A-Z][a-z]{2})(?<=[A-Za-z0-9)\]][.;])\s+(?=[A-Z][a-z ])")
_HEADING_MARK = re.compile(r"^#+\s*")
_FRONT_HEADER = ("条款号", "序号", "编号", "项号", "条款", "Item", "ITEM", "No.", "No", "S/N", "Clause", "Ref", "Ref.", "ITB Reference", "ITB Clause",
                 "ITB", "Clause Reference", "Reference")   # local templates number the front table 1, 2, 3 …
_INSTRUCTIONS = re.compile(r"须知|INSTRUCTIONS? TO (?:TENDERERS?|BIDDERS?)|Instructions? to (?:Tenderers?|Bidders?)|TENDER DATA|BID DATA", re.I)
_FORMS_CHAPTER = re.compile(r"格式|FORMS? OF TENDER|TENDER FORMS?|BID FORMS?|FORMS AND SCHEDULES", re.I)
_CONTRACT = re.compile(r"合同条款|合同条件|合同格式|Conditions of Contract", re.I)

_REJECT = re.compile(r"否决其?投标|否决投标|作否决|被否决|予以否决|不予受理|不予接[收受]|予以拒收|拒收|拒绝接收|拒绝受理|无效投标|投标无效|按无效|"
                     r"作无效|无效标|视为无效|无效响应|响应无效|无效报价|报价无效|废标|取消其?(?:投标|中标|成交|磋商|中选|比选|入围)资格|不予通过|"
                     r"将被拒绝|予以拒绝|恕不接受|不予接受|不被接受|视为(?:自动)?放弃|判定[^，,。；;]{0,6}不合格|按不响应处理|不进入下一(?:阶段|环节)|"
                     r"拒绝其[^，,。；;]{0,4}参[与加]|可以?拒绝其|(?:投标|响应|报价)失效|不得存在下列(?:情形|情况|行为)|(?:资格审查|资格评审|初步评审)[^，,。；;]{0,4}不合格|"
                     r"拒绝接受|拒绝参[与加]|不接受联合体(?:投标|响应|应答|参与)|导致[^，,。；;]{0,10}被拒绝|(?:投标|磋商|响应|报价|谈判)(?:文件)?被拒绝|"
                     r"判定?为无效|有权拒绝|将拒绝|原封退回|"
                     r"不得(?:同时)?参[加与](?:本项目|本次|同一)?[^，,。；;]{0,8}(?:投标|磋商|谈判|报价|采购活动)|不得进入[^，,。；;]{0,6}(?:环节|阶段|评审)|"
                     r"shall be rejected|will be rejected|be disqualified|non-responsive|render(?:s|ed)? the (?:tender|bid) invalid|"
                     r"(?:tender|bid) (?:shall|will) be (?:invalid|void)|(?:shall|will) not be considered|liable to (?:be )?reject|(?:may|shall|will) be rejected|"
                     r"\brejected\b|disqualif|(?:shall|will) not be accepted|rejection of (?:his|her|its|the|their|such) (?:bid|tender)", re.I)
#: "投标文件有下列情形之一的，按无效投标处理：" - what follows, one item to a paragraph, is the list it announces
_LIST_LEAD = re.compile(r"(?:下列|以下|如下)(?:情形|情况|行为|条件)?.{0,12}[：:]\s*$|[：:]\s*$")
_LIST_ITEM = re.compile(r"^\s*(?:[（(]\s*[\d" + _CN + r"]+\s*[)）]|[①-⑳]|\d+\s*[)）.、](?!\d)|[" + _CN + r"]+\s*、)\s*")    # "4.3 …" is the next clause, not item "4."
_NOT_A_REJECTION = re.compile(r"(?i:shall not be (?:a )?ground for (?:disqualification|rejection))|否决所有投标|否决全部投标"
                              r"|(?:采购项目|招标项目|本项目|采购活动)[^，,。；;]{0,8}废标|废标后"          # the procurement is cancelled: no bid is thrown out
                              r"|(?:投诉|异议|质疑)[^。；;]{0,24}(?:不予受理|不予接受|不予处理)|(?:不予受理|不予接受)[^，,。；;]{0,8}(?:投诉|异议|质疑)")   # a COMPLAINT is turned away
#: chapters about the WORKS, not about the bid: "监理人可拒收此类材料" rejects a delivery, not a tender
_WORKS_CHAPTER = re.compile(r"技术标准|技术规范|技术要求|工程量清单|图纸|计量规则|计量与支付|SPECIFICATIONS?|DRAWINGS|BILLS? OF QUANTITIES|SCHEDULE OF RATES", re.I)
_ABOUT_BID = re.compile(r"投标|响应文件|供应商|报价|磋商|比选|应答|竞标|(?i:\bbids?\b|\bbidders?\b|\btenders?\b|\btenderers?\b)")
_EVALUATION = re.compile(r"评标办法|评审办法|评标方法|评审方法|评分办法|评审标准|评分标准|资格审查|符合性审查|评审程序|EVALUATION|Evaluation"
                         r"|符合性评审|符合性检查|资格性检查|资格性审查|资格评审|形式评审|响应性评审|初步评审|评分细则|评审细则|评议标准")
#: the buyer's own documents and the contract's - "磋商文件的组成" lists what the BUYER issued, not what the bid holds
_THEIR_FILE = re.compile(r"(?:招标|磋商|谈判|询价|采购|比选|合同|预审|竞争性磋商|竞争性谈判)文件")
_OUR_FILE = re.compile(r"(?:投标|响应|报价|应答|申请|竞价|参选)文件")


def _ours(text: str) -> bool:
    """Whether the 文件 whose composition the text announces is the bidder's."""
    return bool(_OUR_FILE.search(text)) or not _THEIR_FILE.search(text)
_CITES = re.compile(r"第?\s*(\d+(?:\.\d+){1,3})\s*[项款条]")
_OBLIGES = re.compile(r"须|必须|不得|应当|严禁|不允许|不接受")
_STAR = re.compile(r"[★☆＊]")
_FORM_LIST = re.compile(r"[一-鿿]{2,8}文件应包括(?:下列|以下)内容|[一-鿿]{2,8}文件(?:由|应由)(?:下列|以下)(?:部分|内容)(?:组成|构成)"
                        r"|(?i:(?:tender|bid|proposal)s? (?:shall|must|should) (?:comprise|consist of|include|contain) the following)")
_FORM_ITEM = re.compile(r"[（(]\s*(?:\d+|[a-z])\s*[)）]\s*([^；;。（(]+)")
_FORM_DIR = re.compile(r"(?:^|[；;：:])\s*[" + _CN + r"]+、\s*([^；;。]+)")
_POINTS = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*分\s*$")
_POINT_PART = re.compile(r"([^：:；;，,。\d][^：:；;，,。]{1,23}?)\s*[：:]\s*(\d+(?:\.\d+)?\s*分)")


@dataclass(frozen=True)
class Piece:
    """One sentence, heading or table row of the document, and where it stands."""

    chapter: str            # "第二章 投标人须知"
    number: str             # "3.4.2"; "" when it stands under no number
    heading: str            # the nearest heading above it
    table: str              # the heading a table stands under when this piece is one of its rows
    cells: Tuple[str, ...]  # the row's cells; () for running text
    text: str               # literal
    line: int               # n-th non-empty line of the source, from 1
    kind: str               # "heading" | "text" | "row" | "header"
    header: Tuple[str, ...] = ()  # a row's column names
    page: int = 0           # the PDF page it stands on; 0 when the source has no pages (Word, text)
    file: str = ""          # the job file it comes from when several were given ("补遗书第1号.docx"); "" for a single text

    @property
    def addendum(self) -> bool:
        return bool(self.file and ADDENDUM_NAME.search(self.file))

    @property
    def chapter_no(self) -> str:
        found = _CHAPTER.match(self.chapter)
        return found.group(1) if found else ""

    @property
    def ref(self) -> str:
        where = "前附表 " if (self.kind == "row" and "前附表" in self.table) else ""
        parts = [self.chapter_no, (where + self.number).strip() or ""]
        shown = " ".join(p for p in parts if p)
        where_page = f"第{self.page}页" if self.page else ""
        found = (f"{shown}（{where_page}）" if (shown and where_page) else shown or where_page) or f"L{self.line}"
        return f"{Path(self.file).stem} {found}" if self.addendum else found


@dataclass
class Document:
    pieces: List[Piece] = field(default_factory=list)
    chars: int = 0
    lines: int = 0
    ocr: bool = False   # the text is an OCR reading of a scan (office_job marks it): every number needs the original

    def chapter(self, *names: str) -> List[Piece]:
        return [p for p in self.pieces if any(n in p.chapter for n in names)]

    def numbered(self, number: str, chapter_no: str = "") -> List[Piece]:
        return [p for p in self.pieces if p.number == number and p.kind != "heading" and (not chapter_no or p.chapter_no == chapter_no)]


def _cells(line: str) -> List[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


#: 《政府采购公告和公示信息格式规范》: every notice on ccgp.gov.cn is laid out under these headings
_NOTICE_HEADINGS = ("项目基本情况", "申请人的资格要求", "获取招标文件", "获取采购文件", "提交投标文件截止时间", "响应文件提交", "公告期限", "对本次招标提出询问",
                    "对本次采购提出询问")


def is_document(text: str) -> bool:
    """A document, not a request: two chapters, or a front table. A pasted page or two is neither - it is
    read the way it always was."""
    body = text or ""
    if len(body) < 1500 and sum(1 for heading in _NOTICE_HEADINGS if heading in body) < 2:
        return False
    chapters = {m.group(1) for line in body.splitlines() for m in [_CHAPTER.match(line.strip())] if m}
    if len(chapters) >= 2 or len(body) >= 8000:
        return True   # nobody types eight thousand characters: a long text is a file, whatever its headings look like
    if sum(1 for heading in _NOTICE_HEADINGS if heading in body) >= 2:
        return True   # the national format of a procurement notice: fixed headings over labelled lines
    return any(_TABLE_ROW.match(line.strip()) and "条款号" in line and ("条款名称" in line or "编列内容" in line)
               for line in body.splitlines())


def read(text: str) -> Document:
    doc = Document(chars=len(text or ""))
    chapter = heading = number = table_heading = ""
    header: Optional[List[str]] = None
    line_no = 0
    page = 0
    first = len(doc.pieces)
    listed, contents_lines = _contents((text or "").splitlines())
    at_chapter = 0
    raw_no = 0
    first_titles: Dict[str, str] = {}
    counted: Dict[str, int] = {}     # how far each unit has counted: 第二部分 over 第一章 … 第四章 is two sequences, not one
    body_started = False      # the chapter's numbered text has begun: a table after that is not its front table
    current_file = ""
    opening_header: Optional[List[str]] = None   # the header of the table a 须知 chapter opens with
    stamp_from = 0
    for raw in (text or "").splitlines():
        raw_no += 1
        line = raw.strip()
        if raw_no in contents_lines:
            continue    # a line of a contents page that has no leaders: it names a chapter and lays nothing down
        if current_file and stamp_from < len(doc.pieces):
            for index in range(stamp_from, len(doc.pieces)):      # what the last line gave belongs to the file it stood in
                doc.pieces[index] = replace(doc.pieces[index], file=current_file)
        stamp_from = len(doc.pieces)
        if not line:
            header = None
            continue
        if _TOC_LINE.search(line) and not _TABLE_ROW.match(line):
            continue    # the contents page names the chapters; it lays nothing down
        if line.startswith("〔OCR〕"):
            doc.ocr = True
            continue
        opened = _FILE_MARK.match(line)
        if opened:
            # the next job file: a document of its own - its chapters are not the last file's, its pages start again
            for index in range(first, len(doc.pieces)):
                if not doc.pieces[index].page and page:
                    doc.pieces[index] = _on_page(doc.pieces[index], page)
            current_file = opened.group(1).strip()
            chapter = heading = number = table_heading = ""
            header, page, first = None, 0, len(doc.pieces)
            at_chapter, body_started, opening_header = 0, False, None
            counted, first_titles = {}, {}
            if ADDENDUM_NAME.search(current_file):
                chapter = f"补遗 {current_file}"
            continue
        marker = _PAGE_MARK.match(line)
        if marker:
            # a PDF read by office_job.pdf_document_text: what follows stands on this page
            for index in range(first, len(doc.pieces)):
                if not doc.pieces[index].page and page:
                    doc.pieces[index] = _on_page(doc.pieces[index], page)
            first, page = len(doc.pieces), int(marker.group(1))
            continue
        line_no += 1
        found = _CHAPTER.match(line)
        if found:
            # a chapter of THIS document: a short title, not a sentence that begins "第五章“工程量清单”中的…", and not
            # the 第一章 总则 of a regulation bound into the contract chapter
            title, order = found.group(2).strip(), _ordinal(found.group(1))
            english = found.group(1).isascii()
            unit = _unit(found.group(1))
            at_chapter = counted.get(unit, 0)
            if english and (not title or not title[:1].isupper() or re.search(r"[.;]\s|\b(?:of this|shall|hereof|above|below)\b", title) or len(title) > 80):
                found = None      # "Section 3 of this document shall …" is a sentence
            elif not english and (len(title) > 40 or re.search(r"[，。；：:]", title) or _NOT_A_TITLE.match(title)):
                found = None
            elif listed:
                known = listed.get((unit, order))
                # the contents page sets a title in capitals ("SECTION III. BID DATA SHEET"), the body does not
                said, listed_as = _flat(title).lower(), (known or "").lower()
                if known is None or (title and known and not (said.startswith(listed_as[:6]) or listed_as.startswith(said[:6]))):
                    found = None
            elif order < at_chapter and not (order == 1 and first_titles.get(unit) and _flat(title) == first_titles[unit]):
                found = None
            if found:
                if not at_chapter or order == 1:
                    first_titles.setdefault(unit, _flat(title))
                counted[unit] = order
        if found:
            chapter = f"{found.group(1)} {found.group(2).strip()}".strip()
            heading, number, header = chapter, "", None
            body_started, opening_header = False, None
            doc.pieces.append(Piece(chapter, "", heading, "", (), _HEADING_MARK.sub("", line), line_no, "heading"))
            continue
        if _TABLE_ROW.match(line):
            cells = _cells(line)
            if all(_RULER.fullmatch(c) for c in cells if c):
                continue
            if header is None:
                # what a table is follows from its own header row - a Word reader gives no "#", and the line
                # "投标人须知前附表" above the table is just one more paragraph
                joined = "".join(cells)
                front = cells[0].strip() in _FRONT_HEADER and not _FORMS_CHAPTER.search(chapter) and (
                    cells[0].strip() == "条款号" or "前附表" in heading or re.search(r"条款名称|编列内容|内容及要求|说明[与和及]要求", joined))
                # ... or by where it stands: the table the 须知 chapter opens with, before its first clause, whatever it is
                # called (供应商须知资料表, or nothing at all) and however its columns are headed (序号 | 项目 | 内容)
                opening = (cells[0].strip() in _FRONT_HEADER and _INSTRUCTIONS.search(chapter) and not body_started and len(cells) >= 2
                           and (opening_header is None or cells == opening_header)      # that ONE table (and its later pages) - not the
                           and not re.search(r"评审因素|评审标准|评分因素|评分标准|分值|检查因素|审查", joined))   # appendix tables behind it
                if opening:
                    opening_header = list(cells)
                elif opening_header is None and cells[0].strip() in _FRONT_HEADER and _INSTRUCTIONS.search(chapter):
                    opening_header = []       # the chapter's first table is headed otherwise (条款号 | 条款名称 | 编列内容): the window is shut
                table_heading = ("投标人须知前附表" if ((front and ("条款名称" in joined or "编列内容" in joined)) or opening)
                                 else "评标办法前附表" if (front and re.search(r"评审因素|评审标准|评分因素|评分标准|分值", joined)) else heading)
                header = cells
                doc.pieces.append(Piece(chapter, "", heading, table_heading, tuple(cells), line, line_no, "header"))
                continue
            row_number = cells[0] if (header and header[0] in _FRONT_HEADER and re.fullmatch(r"[\d.()（）]+", cells[0] or "")) else ""
            doc.pieces.append(Piece(chapter, row_number, heading, table_heading, tuple(cells), line, line_no, "row", tuple(header)))
            continue
        header = None
        is_heading = line.startswith("#")
        body = _HEADING_MARK.sub("", line)
        lead = _LEAD_NUMBER.match(body)
        if re.match(r"(?:[一1][、.．\s]\s*)?(?:总\s*则|说\s*明|定\s*义)\s*$", body) or (lead and len(body) > 40):
            body_started = True
        short_title = len(body) <= 24 and not re.search(r"[。；;：:，,]", body)   # "投标人须知前附表", "一、投标函"
        if short_title and re.match(r"[（(]\s*(?:\d+|[a-z])\s*[)）]\s*\S", body) and body.rstrip().endswith((".", ";")):
            short_title = False    # "(d) Technical Proposal." - the last item of a list, not a heading
        if is_heading or short_title or (lead and len(body) <= 30 and not re.search(r"[。；;：:]", body)):
            heading = body
            number = (lead.group(1) or lead.group(2) or lead.group(3)) if lead else ""
            doc.pieces.append(Piece(chapter, number, heading, "", (), body, line_no, "heading"))
            continue
        if lead:
            number = lead.group(1) or lead.group(2) or lead.group(3)
        # a paragraph may hold several numbered clauses: "3.4.1 …。3.4.2 …。"
        current = number
        cursor = 0
        stretches: List[Tuple[str, str]] = []
        for inner in _INLINE_NUMBER.finditer(body):
            if inner.start() > cursor:
                stretches.append((current, body[cursor:inner.start()]))
            current, cursor = inner.group(1), inner.start()
        stretches.append((current, body[cursor:]))
        for clause_number, stretch in stretches:
            for sentence in _SENTENCE_END.split(stretch):
                if sentence.strip():
                    doc.pieces.append(Piece(chapter, clause_number, heading, "", (), sentence.strip(), line_no, "text"))
        number = current
    if page:
        for index in range(first, len(doc.pieces)):
            doc.pieces[index] = _on_page(doc.pieces[index], page)
    if current_file:
        for index in range(stamp_from, len(doc.pieces)):
            doc.pieces[index] = replace(doc.pieces[index], file=current_file)
    doc.lines = line_no
    return doc


_PAGE_MARK = re.compile(r"^〔第(\d+)页〕$")
_FILE_MARK = re.compile(r"^###\s+(.+\.(?:pdf|docx?|xlsx?|txt|md|csv))\s*$", re.I)     # office_job.named_files_blob: one job file begins
ADDENDUM_NAME = re.compile(r"补遗|澄清|答疑|修改通知|变更通知|更正|补充通知|addend|clarif|corrigend", re.I)
_TOC_LINE = re.compile(r"[.．·]{6,}|…{3,}")            # "第二章 投标人须知........................ 9": a line of the contents page
_NOT_A_TITLE = re.compile(r"^[“”\"「」『』‘’、）)，,]|^(?:的|中|所|内|规定|约定|第|和|及|与|或)")


def _unit(chapter: str) -> str:
    """What the document counts in: 章, 部分, 篇 … or "section", "part"."""
    found = re.search(r"(章|部分|篇|卷|册)$|^([A-Za-z]+)", chapter.strip())
    return (found.group(1) or found.group(2).lower()) if found else ""


def _ordinal(chapter: str) -> int:
    """第十二章 -> 12, 第3章 -> 3, 第七部分 -> 7, SECTION 4 -> 4, PART B -> 2, Chapter IV -> 4."""
    body = re.sub(r"部分|[第章篇卷册\s]|(?i:section|part|chapter|volume)", "", chapter)
    if body.isdigit():
        return int(body)
    if re.fullmatch(r"[IVX]{1,4}", body):
        return {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}.get(body, 0)
    if re.fullmatch(r"[A-H]", body):
        return ord(body) - 64
    total, current = 0, 0
    for ch in body:
        if ch == "百":
            total += (current or 1) * 100
            current = 0
        elif ch == "十":
            total += (current or 1) * 10
            current = 0
        else:
            current = _CN.find(ch) + 1 if ch in _CN[:9] else current
    return total + current


def _contents(lines: Sequence[str]) -> Tuple[Dict[Tuple[str, int], str], set]:
    """The chapters a contents page lists - (unit, ordinal) -> title - and the lines (from 1) that LIST chapters without
    leaders: three chapters or more counting up on lines next to each other - a contents page, or the clause that says
    what the tender consists of ("招标文件包括：第一章 … 第六章 …"). In the body a chapter's heading stands pages away from
    the next one's; a listing names chapters that are headed somewhere else as well (three empty chapters at the end of
    a tender - 图纸（另册）… - are headed nowhere else, and stay)."""
    listed: Dict[Tuple[str, int], str] = {}
    for line in lines:
        found = _CHAPTER.match(line.strip())
        if found and _TOC_LINE.search(line):
            listed.setdefault((_unit(found.group(1)), _ordinal(found.group(1))), _flat(_TOC_LINE.split(found.group(2))[0]))
    run: List[Tuple[int, str, int, str]] = []       # (line, unit, ordinal, title)
    runs: List[List[Tuple[int, str, int, str]]] = []
    since = 0                                       # non-empty lines since the run's last chapter
    for number, line in enumerate(lines, 1):
        body = line.strip()
        if not body or _PAGE_MARK.match(body):
            continue
        found = _CHAPTER.match(body)
        title = found.group(2).strip() if found else ""
        plain = found and not _TOC_LINE.search(body) and len(title) <= 40 and not re.search(r"[，。；：:]", title)
        if plain and run and _unit(found.group(1)) == run[-1][1] and _ordinal(found.group(1)) == run[-1][2] + 1 and since <= 2:
            run.append((number, _unit(found.group(1)), _ordinal(found.group(1)), _flat(title)))
            since = 0
        elif plain:
            if len(run) >= 3:
                runs.append(run)
            run, since = [(number, _unit(found.group(1)), _ordinal(found.group(1)), _flat(title))], 0
        else:
            since += 1
            if since > 2:
                if len(run) >= 3:
                    runs.append(run)
                run = []
    if len(run) >= 3:
        runs.append(run)
    skipped: set = set()
    headed = [(_unit(m.group(1)), _ordinal(m.group(1)), n) for n, line in enumerate(lines, 1)
              for m in [_CHAPTER.match(line.strip())] if m and not _TOC_LINE.search(line)]
    for members in runs:
        inside = {number for number, _, _, _ in members}
        # ... headed elsewhere: not in this run, and not in a listing already found (a contents page and a body of very
        # short chapters are not each other's proof)
        elsewhere = sum(1 for _, unit, order, _ in members
                        if any(u == unit and o == order and n not in inside and n not in skipped for u, o, n in headed))
        if elsewhere < max(2, (len(members) + 1) // 2):
            continue
        for number, unit, order, title in members:
            listed.setdefault((unit, order), title)
            skipped.add(number)
    return (listed if len(listed) >= 2 else {}), (skipped if len(listed) >= 2 else set())


def _on_page(piece: Piece, page: int) -> Piece:
    return replace(piece, page=page)


# ---------------------------------------------------------------------------
# the two front tables
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FrontRow:
    number: str
    name: str
    content: str
    piece: Piece


def front_rows(doc: Document) -> List[FrontRow]:
    rows: List[FrontRow] = []
    for p in doc.pieces:
        if p.addendum:
            continue
        if p.kind == "row" and "前附表" in p.table and len(p.cells) >= 3:
            rows.append(FrontRow(p.cells[0], p.cells[1], "；".join(c for c in p.cells[2:] if c), p))
        elif p.kind == "row" and "前附表" in p.table and len(p.cells) == 2 and p.cells[1]:
            # 序号 | 内容及要求: the row's name is what stands before its first colon ("磋商响应文件有效期为 90 天" has none)
            named = re.match(r"\s*([^：:；;，,。]{2,20}?|[A-Za-z][^：:]{3,90}?)\s*[：:]\s*(.+)$", p.cells[1], re.S)
            rows.append(FrontRow(p.cells[0], named.group(1) if named else "", named.group(2) if named else p.cells[1], p))
    return rows


def front_remainder(doc: Document) -> List[FrontRow]:
    """Keep meaningful front-table rows that no recognized field or requirement represents."""
    used = {m.line for m in field_mentions(doc)}
    used |= {p.line for p in obligations(doc)}
    used |= {r.piece.line for r in rejections(doc)}
    used |= {piece.line for _, piece in forms(doc)}
    rows: List[FrontRow] = []
    seen: set = set()
    for row in front_rows(doc):
        text = row.content.strip()
        if "评标办法" in row.piece.table or row.piece.line in used or not text or re.fullmatch(r"[/／\-—–\s]+", text):
            continue
        key = (_flat(row.name), _flat(text))
        if key not in seen:
            seen.add(key)
            rows.append(row)
    return rows


_SUBLABEL = re.compile(r"[：:]\s*(?=(?:\d+(?:\.\d+)+\s*)?([^：:；;，,。\s]{2,12})[：:])")
_SUBLABEL_END = re.compile(r"(?:要求|条件|资格|形式|金额|时间|日期|地点|方式|比例|期限|年份|名称|地址|联系人|电话)$")


def _split_parts(content: str) -> List[str]:
    """The parts of a front-table cell: at every "；" - and at a "：" that is followed by another label
    ("…施工业绩：项目经理资格：建筑…"). A scan read by OCR gives "；" back as "：" more often than not; a label is
    known by what it names (a field) or by how it ends (…要求 / …资格 / …金额)."""
    from packing_assistant.tools import tender_facts as tf

    parts: List[str] = []
    # at every "；" - and at a "。" that a labelled part follows ("…显示为准。第二个信封（报价文件）开标时间：…")
    for chunk in re.split(r"[；;]|。(?=[^：:；;，,。]{2,20}[：:])", content):
        cursor = 0
        for found in _SUBLABEL.finditer(chunk):
            label = found.group(1)
            # only after a part that already has its own label and value: "形式：银行保函：履约担保的金额：…"
            if re.search(r"[：:]", chunk[cursor:found.start()]) and (tf.document_topic(label) or _SUBLABEL_END.search(label)):
                parts.append(chunk[cursor:found.start()])
                cursor = found.end()
        parts.append(chunk[cursor:])
    return parts


def _parts(content: str) -> List[Tuple[str, str, str]]:
    """"金额：80万元；形式：银行转账…；10.3 缺陷责任期：24个月" as [(label, body, number)]; "" where a part has none."""
    out: List[Tuple[str, str, str]] = []
    for part in _split_parts(content):
        part = part.strip()
        if not part:
            continue
        lead = re.match(r"(\d+(?:\.\d+)+)\s*", part)
        number = lead.group(1) if lead else ""
        rest = part[lead.end():] if lead else part
        # "1.开标时间：…；2.现场开标地点：…": the parts of the cell are numbered - the number is no part of the label
        rest = re.sub(r"^\s*(?:\d{1,2}\s*[.．、)）](?!\d)|[（(]\s*\d{1,2}\s*[)）]|[①-⑳])\s*(?=[^\d\s：:]{2,18}\s*[：:])", "", rest)
        found = re.match(r"(?!\d)((?:(?!\d{2})[^：:；;，,。\s]){2,18})\s*[：:]\s*(.+)$", rest)   # "应于2026-10-2709：00" holds a time, not a label
        out.append((found.group(1), found.group(2).strip(), number) if found else ("", rest, number))
    return out


#: labels that say WHICH ASPECT of the row's field a part gives, not another field
_GENERIC_LABEL = re.compile(r"^(?:金额|数额|额度|形式|名称|内容|要求|标准|期限|时间|方式|规定|说明|全称)$")


_LOT = re.compile(r"第?\s*([一二三四五六七八九十\d]{1,3})\s*(标段|标包|合同包|包)|(?<![A-Za-z])([A-Z])\s*(标段|包)|(包|标段)\s*(\d{1,2})(?!\d)")
_LOT_HEAD = re.compile(r"^(?:标段|标段名称|标段号|标段编号|包号|包|标包|合同包|采购包)$")
_CHANGED = re.compile(r"(?:修改|调整|变更|更正|改|延期|顺延|推迟|延长|提前|延)(?:为|至|到)\s*[：:]?\s*")


def lot_of(text: str) -> str:
    """The lot a stretch of text names, as it is written ("二标段", "第2包"); "" when it names none or several."""
    found = {m.group(0).replace(" ", "") for m in _LOT.finditer(text or "")}
    return found.pop() if len(found) == 1 else ""


def _lot_key(lot: str) -> str:
    """一标段 / 第1标段 / 1标段 / 标段1 are one lot."""
    found = _LOT.search(lot or "")
    if not found:
        return lot
    number = found.group(1) or found.group(3) or found.group(6) or ""
    kind = found.group(2) or found.group(4) or found.group(5) or ""
    digits = str(_ordinal("第" + number + "章")) if number and not number.isdigit() and not number.isalpha() else number
    return f"{digits}{'包' if '包' in kind else '标段'}"


def lot_mentions(doc: Document):
    """A table that has one row per lot: 标段 | 建设内容 | 最高投标限价 | 计划工期 | 投标保证金 - every column that is a
    field gives that lot's value."""
    from packing_assistant.tools import tender_facts as tf

    out = []
    for p in doc.pieces:
        if p.kind != "row" or p.addendum or not p.header or not _LOT_HEAD.match(p.header[0].strip()) or _CONTRACT.search(p.chapter):
            continue
        lot = p.cells[0].strip()
        if not _LOT.search(lot) and not re.fullmatch(r"[\d一二三四五六七八九十A-Z]{1,3}", lot):
            continue
        lot = lot if _LOT.search(lot) else f"{lot}{'包' if '包' in p.header[0] else '标段'}"
        for name, cell in zip(p.header[1:], p.cells[1:]):
            topic = tf.document_topic(name.strip())
            if not topic or topic in tf._ALWAYS_OURS | tf._NO_SIDE | tf._STATEMENT_ONLY or not cell.strip():
                continue
            value = _document_value(topic, cell.strip(), same_as=False)
            if value:
                out.append(tf.Mention(topic, "tender", lot, value, f"{lot} {name}：{cell}"[:160], p.line, ref=p.ref))
    return out


def addenda_mentions(doc: Document):
    """What the addenda change. The field comes from the front-table clause the sentence names ("前附表第3.4.1项") or from
    its own words; the lot from the sentence (or, for an answer, from the question); the value is what stands after
    修改为 / 调整为 / 延至 - or the answer itself where a question about one field is answered plainly."""
    from packing_assistant.tools import tender_facts as tf

    by_number = {row.number: tf.document_topic(row.name) for row in front_rows(doc) if not row.piece.addendum}
    out = []
    question = ""
    for p in doc.pieces:
        if p.addendum and p.kind == "row":
            out += _compared(p, by_number)
        if not p.addendum or p.kind != "text":
            continue
        text = p.text.strip()
        if re.match(r"(?:原(?:文|条款|内容|表述|规定)?|(?:修改|更正|变更|调整)前(?:的)?(?:内容)?)\s*(?:[：:]|为\s*[：:])", text):
            continue    # "原文：4.2.1 … 5月6日" is what the tender USED to say; the new value stands in the line after it
        if re.match(r"问\s*\d*\s*[：:]", text):
            question = text
            continue
        answer = re.match(r"答\s*\d*\s*[：:]\s*", text)
        said = text[answer.end():] if answer else text
        around = (question + " " + said) if answer else said
        change = _CHANGED.search(said)
        cited = re.search(r"第?\s*(\d+(?:\.\d+)+)\s*[项款条]", around)
        before = said[:change.start()] if change else around
        topic = (by_number.get(cited.group(1)) if cited else "") or tf.loose_document_topic(re.sub(r"[“”\"][^“”\"]*[“”\"]", "", before)[-40:])
        if not topic and answer:
            topic = tf.loose_document_topic(question)
        if not topic and change:
            # "现修改为：3.3.1 投标有效期 120 日历天" - the field is named after the verb: by its clause, or by how the tail begins
            tail_text = said[change.end():].lstrip("“\" ")
            numbered = re.match(r"(\d+(?:\.\d+)+)\s*", tail_text)
            topic = by_number.get(numbered.group(1), "") if numbered else ""
            if not topic:
                opening = tail_text[numbered.end():] if numbered else tail_text
                hits = tf._topic_hits(opening[:24])
                topic = hits[0][2] if hits and hits[0][0] <= 2 else ""
        if not topic or topic in tf._ALWAYS_OURS | tf._NO_SIDE | tf._STATEMENT_ONLY:
            if answer:
                question = ""
            continue
        tail = said[change.end():] if change else said
        value = _document_value(topic, tail.strip("“”\" "), same_as=False)
        if not value and answer and tf._TOPIC[topic].kind not in tf._KIND_RE:
            value = said.rstrip("。 ")[:60]
        if value:
            lot = lot_of(before) or lot_of(question if answer else "")
            origin = re.sub(r"^招标文件?", "", Path(p.file).stem)
            out.append(tf.Mention(topic, "tender", lot, value, text[:160], p.line, origin=origin, ref=p.ref))
        if answer:
            question = ""
    return out


_WAS = re.compile(r"^(?:原(?:招标|采购|磋商|谈判)?(?:文件)?(?:条款|内容|表述|规定|文)?(?:内容)?|(?:修改|更正|变更|调整|澄清)前(?:内容|条款)?)$")
_NOW = re.compile(r"^(?:(?:修改|更正|变更|调整|澄清)后(?:的)?(?:内容|条款)?|现?(?:修改|更正|变更|调整)为|(?:修改|更正|变更)内容|现内容)$")


def _compared(p: Piece, by_number: Dict[str, str]):
    """A row of a correction notice's comparison table - 序号 | 原招标文件内容 | 修改后内容: the field is the clause either cell
    names, or what the revised cell is labelled with; the value is what the revised cell lays down."""
    from packing_assistant.tools import tender_facts as tf

    header = [h.replace(" ", "") for h in p.header]
    was = next((i for i, h in enumerate(header) if _WAS.match(h)), None)
    now = next((i for i, h in enumerate(header) if _NOW.match(h)), None)
    if was is None or now is None or max(was, now) >= len(p.cells) or not p.cells[now].strip():
        return []
    revised, original = p.cells[now].strip(), p.cells[was].strip()
    cited = re.search(r"(\d+(?:\.\d+)+)", " ".join(c for i, c in enumerate(p.cells) if i not in (was, now)) + " " + original + " " + revised)
    row_topic = by_number.get(cited.group(1), "") if cited else ""
    out = []
    for label, body, _ in _parts(re.sub(r"^\s*\d+(?:\.\d+)+\s*", "", revised)):
        topic = (tf.document_topic(label) if label else "") or row_topic
        if not topic and not label:
            hits = tf._topic_hits(body[:24])
            topic = hits[0][2] if hits and hits[0][0] <= 2 else ""
            body = body[hits[0][1]:] if topic else body
        if not topic or topic in tf._ALWAYS_OURS | tf._NO_SIDE | tf._STATEMENT_ONLY:
            continue
        value = _document_value(topic, _chosen(body) or "", same_as=False)
        if value and not any(m.topic == topic and _same(m.value, value) for m in out):
            origin = re.sub(r"^招标文件?", "", Path(p.file).stem)
            out.append(tf.Mention(topic, "tender", lot_of(original + revised), value, f"{original} → {revised}"[:160], p.line, origin=origin, ref=p.ref))
    return out


_NOT_READ = re.compile(r"(?:详见|见|以)[^，,。；;]{0,12}附件|另行(?:发布|通知|公布)|(?:整体|全文|整章|全部)替换|重新(?:发布|上传|下载)|以(?:更新|修改|更正|调整)后的?[^，,。；;]{0,12}为准")


def addenda_unread(doc: Document) -> List[Piece]:
    """Sentences of an addendum that change something the reader cannot take: the change is in an attachment, a chapter
    is replaced whole, a new file is put out. Said, so that nobody reads the merged table as ALL the addendum changed."""
    out: List[Piece] = []
    seen: set = set()
    for p in doc.pieces:
        if p.addendum and p.kind == "text" and _NOT_READ.search(p.text) and _flat(p.text) not in seen:
            seen.add(_flat(p.text))
            out.append(p)
    return out[:12]


_SEVERAL_LOTS = re.compile(r"[" + _CN + r"\d]\s*[、,，及和与至~～\-—]\s*第?\s*[" + _CN + r"\d]{1,3}\s*(?:标段|标包|合同包|包)")


def document_lot(doc: Document) -> Optional[Tuple[str, Piece]]:
    """The lot THIS document is issued for, as its cover or the title of its notice names it: "…日常养护作业第2标段（专业
    名称…）", "（项目名称）施工二标段施工招标公告". A project of several lots puts out one document per lot, and the notice inside
    each still lists them all. A line that names several ("一、二、三标段"), or says how the project is split, names none."""
    words = [p for p in doc.pieces if not p.addendum and p.kind in ("text", "heading")]
    # the cover - everything before the first chapter, a template's usage notes included - and the head of the notice;
    # in a document without chapters, its first lines
    cover = [p for p in words if not p.chapter]
    head = [p for p in words if p.chapter and _ordinal(p.chapter_no) == 1][:8]
    for p in (cover + head if head else words[:60]):
        if len(p.text) <= 60 and not _SEVERAL_LOTS.search(p.text) and not re.search(
                r"划分|分为|共\s*[" + _CN + r"两\d]+\s*(?:个|包|标段|标包)|每个|各|[：:]\s*$", p.text):      # "本次招标共 1 包：" says how many there are
            lot = lot_of(p.text)
            if lot:
                return lot, p
    return None


def field_mentions(doc: Document):
    """Mentions for the facts layer: the front table first, the notice for what the table lacks; then the values the
    lot table gives per lot, and last what the addenda changed."""
    found = _main_mentions(doc)
    per_lot = lot_mentions(doc)
    lots = {_lot_key(m.lot): m.lot for m in per_lot + [m for m in found if m.lot]}
    merged = []
    for m in found + per_lot:
        m = replace(m, lot=lots.get(_lot_key(m.lot), m.lot)) if m.lot else m
        if not any(x.topic == m.topic and _lot_key(x.lot) == _lot_key(m.lot) and _same(x.value, m.value) for x in merged):
            merged.append(m)
    # a value given for every lot makes the lot-less one of the same field a leftover of the same cell, not a second value
    for m in addenda_mentions(doc):
        merged.append(replace(m, lot=lots.get(_lot_key(m.lot), m.lot)) if m.lot else m)
    this = document_lot(doc)
    if this:
        from packing_assistant.tools import tender_facts as tf

        lot, piece = this
        merged.append(tf.Mention("this_lot", "tender", "", lots.get(_lot_key(lot), lot), piece.text[:160], piece.line, ref=piece.ref))
    return merged


def _main_mentions(doc: Document):
    """The front table first, the notice for what the table lacks."""
    from packing_assistant.tools import tender_facts as tf

    table: List[tf.Mention] = []
    for row in front_rows(doc):
        if "评标办法" in row.piece.table:
            continue  # its rows are review standards and scoring, read by scores() and rejections()
        cleaned = re.sub(r"^\s*(?:\[[^\]]{4,300}\]\s*)+", "", row.content)     # "[Insert … if allowed.]": the drafter's instruction, left in
        if cleaned != row.content:
            row = replace(row, content=cleaned)
        if re.match(r"[(（]?\s*(?:Please\s+)?(?:refer to|see)\b", row.content, re.I) and len(row.content) <= 160:
            continue        # "(Please refer to the Scope of Works / Specifications …)" lays nothing down here
        row_topic = tf.document_topic(row.name)
        if not row_topic and len(row.piece.cells) == 2:
            # 序号 | 内容及要求: a row is named by how its one cell begins - "转包与分包：否", "磋商响应文件有效期为 90 天";
            # "在中标通知书发出前，招标人将…" begins with no field and is about none
            content = row.content
            ascii_row = not re.search(r"[一-鿿]", content)
            opening = row.name or (content[:200] if ascii_row else content[:16])
            loose = tf.loose_document_topic(opening)
            if loose and not row.name and ascii_row:
                hits = tf._topic_hits(opening)
                if hits and min(h[0] for h in hits) > 40:
                    loose = ""      # "…as may be required by existing laws and/or the Procuring Entity": a field word far in names nothing
            if loose and not row.name and not ascii_row:
                hits = tf._topic_hits(opening)
                clause = re.search(r"[，,。；;]", opening)
                if hits and clause and min(h[0] for h in hits) > clause.start() and not tf.document_topic(opening):
                    loose = ""      # the field's word comes after the first clause: the cell is ABOUT something else
            row_topic = loose if loose not in tf._ALWAYS_OURS | tf._NO_SIDE | tf._STATEMENT_ONLY else ""
        taken: set = set()
        if (row_topic == "bond" and not re.search(r"[一-鿿]", row.content) and len(tf._MONEY.findall(row.content)) >= 2
                and re.search(r"(?i)\bif\b[^.;]{0,60}\b(?:cash|surety|bond|guarantee|check|letter of credit)", row.content)):
            # "… not less than Php… if in cash …; … Php… if in Surety Bond": one amount per form - the first alone would mislead
            flat_row = re.sub(r"\s+", " ", row.content)
            each = [flat_row[found.start():found.start() + 56].split(";")[0].strip() for found in tf._MONEY.finditer(flat_row)]
            table.append(tf.Mention("bond", "tender", "", "；".join(each)[:118], f"{row.name}：{row.content}"[:160], row.piece.line, ref=row.piece.ref))
            continue
        if row_topic == "copies" and row.content.strip():
            # how many copies is the whole cell: "（1）投标文件正本 1 份；（2）投标文件副本 6 份；（3）开标一览表正本 1 份…"
            value = _chosen(row.content) or ""
            if value and not _PLACEHOLDER.match(value.strip()):
                table.append(tf.Mention("copies", "tender", "", value.strip()[:160], f"{row.name}：{row.content}"[:160], row.piece.line, ref=row.piece.ref))
            continue
        parts = _parts(row.content)
        # a 资格要求 row opens with what the law asks of every bidder; the requirement of THIS tender stands further
        # down. Where parts of the row name a qualification (资质 / 许可证 / 等级), those are the row's value.
        strong = [body for label, body, _ in parts if not label and re.search(r"资质|许可证|等级", _chosen(body) or "")]
        for label, body, inner in parts:
            part_lot = ""
            if label and _LOT.fullmatch(label.replace(" ", "")):
                part_lot, label = label.replace(" ", ""), ""      # "一标段：30万元": the row's field, for that lot
            picked = _chosen(body if not _BOXES.search(label) else label + "：" + body)
            if picked is None or _PLACEHOLDER.match(picked.strip()):
                continue   # an option left unticked; "（填写采购人名称）" on the envelope row
            body = picked
            label = _BOXES.sub("", label).strip()
            own = (tf.document_topic(label) or _label_under(label, row.name)) if label else ""
            if label and not own and row_topic and (
                    (len(row.name) >= 3 and row.name in label) or any(len(a) >= 3 and a in label for a in tf._TOPIC[row_topic].aliases)
                    or (row_topic == "quality" and "质量" in label)):
                # "磋商保证金金额：25000元" under 磋商保证金, "专项验收的质量评定：…" under 质量要求: the row's own field, and
                # what the label adds to its name says which aspect of it
                own = row_topic
            if label and not own and not _GENERIC_LABEL.match(label):
                continue   # "采购预算：860万元" under 采购预算及最高限价 is the budget, not the cap: a part named for something else
            topic = own or row_topic
            if topic == "qualification" and not own and len(row.content) > 200 and not re.search(
                    r"资质|资格|许可证|证书|等级|注册|建造师|业绩|信誉|财务|认证", body):
                continue   # one paragraph of a long 资格要求 row that names no qualification (a policy note, an option)
            if not topic and tf._EVAL_METHOD.search(body) and re.search(r"本项目|本次|本标段|采用", body) and len(body) <= 60:
                topic = "eval_method"   # "10.1 本项目采用综合评估法评标" - not a rule that merely mentions a method
            if not topic or topic in tf._ALWAYS_OURS or topic in tf._NO_SIDE or topic in tf._STATEMENT_ONLY:
                continue
            if topic == "qualification" and not own and strong:
                if body not in strong[:3]:
                    continue
            elif not own and topic in taken and not part_lot:
                continue  # "地址：…" under 招标人 is not a second 招标人
            related = not row_topic or tf._TOPIC[topic].section == tf._TOPIC[row_topic].section
            value = _document_value(topic, body, same_as=related)
            if re.match(r"(?:同|见|详见|按)\s*[《“\"]?[^，。；;]{0,12}(?:公告|邀请)|(?:详?见|按)\s*(?:本章)?\s*附[录件表]\s*[\d" + _CN + r"]*\s*$", value or ""):
                continue   # "资质要求：同招标公告", "项目负责人资格：见附录4" lay nothing down here; what they point at is read there
            if not value or any(m.topic == topic and m.lot == part_lot and _flat(m.value) == _flat(value) for m in table):
                continue  # "招标人名称：…" on the envelope row says nothing the 招标人 row did not
            taken.add(topic)
            ref = row.piece.ref if not inner else f"{row.piece.chapter_no} 前附表 {inner}".strip()
            # two parts of one row about the same field ("履约担保的形式：…；履约担保的金额：…") are told apart by their label
            part_name = re.sub(r"^" + re.escape(tf._TOPIC[topic].label) + r"的?", "", label) if (own and own == row_topic) else ""
            if own and own != row_topic:
                # "第一个信封（商务及技术文件）开标时间" / "第二个信封（报价文件）开标时间": two values of one field, told
                # apart by what the label says beyond the field's own name
                alias = max((a for a in tf._TOPIC[own].aliases if a in label), key=len, default="")
                extra = label.replace(alias, "", 1).strip("的 ") if alias else ""
                part_name = extra if len(extra) >= 4 else part_name
            table.append(tf.Mention(topic, "tender", part_lot, value, f"{row.name}：{body}"[:160], row.piece.line, role=part_name, ref=ref))
    # how bids are judged is what the chapter on it is called ("第三章 评标办法（综合评分法）") - or what it opens with
    # ("评审方法：最低评标价法"); a method some row of the front table mentions in passing does not outrank that
    named_method = None
    for p in doc.pieces:
        if _EVALUATION.search(p.chapter) and p.kind in ("heading", "text") and len(p.text) <= 40:
            found = tf._EVAL_METHOD.search(p.text)
            if found and (p.kind == "heading" or re.match(r"(?:评[审标分](?:方法|办法)|本项目)", p.text)):
                named_method = tf.Mention("eval_method", "tender", "", found.group(0), p.text[:160], p.line, ref=p.chapter_no or p.ref)
                break
    stated = [m for m in table if m.topic == "eval_method"]
    if named_method is None and not stated:
        # no heading names it: what the document says it USES - "本次评标采用综合评分法", "由磋商小组采用综合评分法对…" - in
        # the chapter on how bids are judged first, anywhere but the contract otherwise
        said = [p for p in doc.pieces if p.kind in ("heading", "text") and not p.addendum and not _CONTRACT.search(p.chapter)
                and re.search(r"采用\s*[“\"]?\s*(?:" + tf._EVAL_METHOD.pattern + r")", p.text, re.I)]
        said.sort(key=lambda p: (not _EVALUATION.search(p.chapter + p.heading), p.line))
        if said:
            found = tf._EVAL_METHOD.search(said[0].text)
            named_method = tf.Mention("eval_method", "tender", "", found.group(0), said[0].text[:160], said[0].line, ref=said[0].ref)
    if named_method is not None and not stated:
        table.append(named_method)
    elif named_method is not None and not any(_same(m.value, named_method.value) for m in stated):
        # the front table says one method and the chapter is headed with another: both stand, and the reader is told
        table.append(replace(named_method, origin="评标办法一章的标题，与前附表不一致"))
    if not any(m.topic == "price_cap" for m in table):
        for row in front_rows(doc):
            found = re.search(r"(?:投标)?最高(?:投标)?限价\s*(?:为|是)?\s*(?:人民币)?\s*[¥￥]?\s*(" + tf._NUM + r"\s*(?:万元|亿元|元))", row.content)
            if found and "评标办法" not in row.piece.table:
                table.append(tf.Mention("price_cap", "tender", "", found.group(1), f"{row.name}：{row.content}"[:160], row.piece.line, ref=row.piece.ref))
                break
    if not any(m.topic == "copies" for m in table):
        # "响应文件组成和封装 | 1）正本1 份；副本2份。…": the copies statement, whatever its row is called
        for row in front_rows(doc):
            found = re.search(r"正本\s*[" + _CN + r"壹贰叁肆伍陆柒捌玖拾两\d]{1,3}\s*份[^。]{0,12}?副本\s*[" + _CN + r"壹贰叁肆伍陆柒捌玖拾两\d]{1,3}\s*份", row.content)
            if found and "评标办法" not in row.piece.table:
                table.append(tf.Mention("copies", "tender", "", found.group(0), f"{row.name}：{row.content}"[:160], row.piece.line, ref=row.piece.ref))
                break
    # "附录4 资格审查条件（项目负责人最低要求）": 人员 | 数量 | 资格要求 - the rows the front table points at with "见附录4"
    for p in doc.pieces:
        if (p.kind == "row" and not p.addendum and len(p.cells) >= 2 and _INSTRUCTIONS.search(p.chapter) and "前附表" not in p.table
                and re.search(r"资格要求|任职要求|最低要求|资格条件", "".join(p.header)) and re.fullmatch(r"人员|岗位|职务|人员类别|人员名称", p.header[0].strip())):
            who = tf.document_topic(p.cells[0].strip())
            asked = next((c.strip() for i, c in enumerate(p.cells) if i and re.search(r"资格|要求|条件", p.header[i] if i < len(p.header) else "") and c.strip()), "")
            if who in ("pm", "tech_lead") and asked and not any(m.topic == who for m in table):
                table.append(tf.Mention(who, "tender", "", _balanced(tf._requirement_text(asked), asked) or asked[:120], f"{p.cells[0]}：{asked}"[:160], p.line, ref=p.ref))
    if not any(m.topic == "price_cap" for m in table):
        # a tender on RATES caps the rate: "■有，最高投标限价：100%"
        for row in front_rows(doc):
            if tf.document_topic(row.name) == "price_cap" and "评标办法" not in row.piece.table:
                rate = re.search(r"(?:最高(?:投标)?限价|上限)\s*[：:为]?\s*(\d+(?:\.\d+)?\s*[%％])", _chosen(row.content) or "")
                if rate:
                    table.append(tf.Mention("price_cap", "tender", "", rate.group(1), f"{row.name}：{row.content}"[:160], row.piece.line, ref=row.piece.ref))
                    break
    have = {m.topic for m in table}
    notice: List[tf.Mention] = []
    # the notice; in a document with no chapters at all (an English ITT, a bare specification) every labelled line
    # ... and the cover before it ("项目编号：…", "采 购 人：…")
    chaptered = any(p.chapter for p in doc.pieces)
    noticed = ([p for p in doc.pieces if p.chapter and not p.addendum and _ordinal(p.chapter_no) == 1]
               + [replace(p, table="封面") for p in doc.pieces if not p.chapter and p.kind == "text" and not p.addendum]
               if chaptered else doc.pieces)
    under = ""   # the heading a labelled line stands under: "名称：…" is the 采购人's only under 采购人信息
    under_lot = ""
    qualifying = False
    lines_of_notice: List[Piece] = []
    for p in noticed:
        if p.kind != "text":
            lines_of_notice.append(p)
            continue
        # "预算金额：250000.00 元，最高限价：250000 元" / "名    称：某中心 地    址：某路" - a labelled part each
        cuts = [m.start() for m in re.finditer(r"(?<=[，,、；;\s])(?=(?:项目)?(?:最高限价|预算金额|地\s*址|联系人|联系方式|电\s*话)\s*(?:[（(][^）)]*[)）])?\s*[：:])", p.text)]
        if cuts:
            edges = [0] + cuts + [len(p.text)]
            lines_of_notice += [replace(p, text=p.text[a:b].strip(" ，,、；;")) for a, b in zip(edges, edges[1:]) if p.text[a:b].strip(" ，,、；;")]
        else:
            lines_of_notice.append(p)
    for p in lines_of_notice:
        opens = p.kind == "text" and len(p.text) <= 24 and re.search(r"资格要求\s*[：:]?\s*$|资格条件\s*[：:]?\s*$", p.text)
        if p.kind in ("heading", "text") and len(p.text) <= 24 and lot_of(p.text) and not re.search(r"[：:。，]", p.text):
            under_lot = lot_of(p.text)      # "3.1.2 二标段": what follows is that lot's
        elif p.kind == "heading" and not lot_of(p.text):
            under_lot = ""
        if p.kind == "heading" or opens or (p.kind == "text" and len(p.text) <= 16 and not re.search(r"[：:。]", p.text)):
            under = p.text
            qualifying = bool(re.search(r"资格要求|资格条件", p.text)) or (qualifying and not re.match(r"\s*[一二三四五六七八九十]+\s*、", p.text))
        if p.kind != "text":
            continue
        body = re.sub(r"^\s*(?:\d+(?:\.\d+)*\s*[.．、]?\s*)?(?:[（(]\s*\d+\s*[)）]\s*)?", "", _LEAD_NUMBER.sub("", p.text))
        if qualifying and not re.match(r"[^：:]{2,14}[：:]", body):
            # under 申请人的资格要求: "供应商具有…建筑工程施工总承包叁级及以上资质…" / "拟派项目经理具有…贰级…注册建造师…"
            for topic, wanted in (("qualification", r"[^。；;]*?(?:具有|具备|持有|须有)[^。；;]*?(?:资质|许可证|资格证书)[^。；;]*"),
                                  ("pm", r"[^。；;，,]*?(?:项目经理|项目负责人)[^。；;]*?(?:建造师|职称|证书)[^。；;]*")):
                said = re.search(wanted, body)
                if said and not any(n.topic == topic and n.lot == under_lot for n in notice):
                    notice.append(tf.Mention(topic, "tender", under_lot, said.group(0).strip()[:120], p.text[:160], p.line, ref=p.ref))
        joint = re.search(r"[（(]\s*(是|否|不|不允许|不接受|允许|接受)\s*[)）]\s*接受联合体", body)   # "本项目（ 否 ）接受联合体投标。"
        if joint:
            notice.append(tf.Mention("consortium", "tender", "", joint.group(1), p.text[:160], p.line, ref=p.ref))
            continue
        visit = re.search(r"(?:招标人|采购人|本项目|本次招标)?\s*(不(?:统一|集中)?组织[^，,。；;]{0,6}踏勘[^，,。；;]{0,4}|不组织现场踏勘)", body) if len(body) <= 60 else None
        if visit and not any(n.topic == "deadline_visit" for n in notice):     # "1、招标人不组织踏勘现场。招标人不组织投标预备会。"
            notice.append(tf.Mention("deadline_visit", "tender", "", visit.group(1).strip(), p.text[:160], p.line, ref=p.ref))
        joint = re.search(r"(?:本项目|本次[一-鿿]{0,6}|本标段|本包)\s*((?:不接受|不允许|接受|允许)联合体(?:投标|报名|参加|响应|磋商|谈判|形式)?)", body) if len(body) <= 40 else None
        if joint and not any(n.topic == "consortium" for n in notice):     # "本项目不接受联合体报名。"
            notice.append(tf.Mention("consortium", "tender", "", joint.group(1), p.text[:160], p.line, ref=p.ref))
            continue
        found = re.match(r"([A-Za-z][A-Za-z .'/&-]{2,40}?|(?:[^：:；;，,。\s]\s{0,6}){2,20}?)\s*[：:]\s*(.+)$", body)
        if found:
            label = re.sub(r"[（(][^）)]*[)）]|\s+", "", found.group(1)) if re.search(r"[一-鿿]", found.group(1)) else found.group(1)
            label = re.sub(r"^本项目(?:的)?", "", label)
            if label == "名称" and re.search(r"采购人|招标人|比选人", under) and "代理" not in under:
                notice.append(tf.Mention("owner", "tender", "", found.group(2).strip()[:60], p.text[:160], p.line, ref=p.ref))
                continue
            topic = tf.document_topic(label)
            # the national notice format: "截止时间：…" under 四、响应文件提交, "时间：…" / "地点：…" under 五、开启
            if not topic and under:
                topic = _label_under(label, under)
            if topic and topic not in tf._ALWAYS_OURS | tf._NO_SIDE | tf._STATEMENT_ONLY:
                value = _document_value(topic, _chosen(found.group(2)) or "")
                if topic in ("pm", "tech_lead") and re.fullmatch(r"[一-鿿·]{2,4}", value or ""):
                    value = ""   # "项目负责人：贾某" among the notice's contacts is somebody at the agency - a tender requires a grade, not a person
                if value and not _PLACEHOLDER.match(value):
                    notice.append(tf.Mention(topic, "tender", "", value, p.text[:160], p.line, ref=p.ref))
                continue
        for topic, value in _strict_in_sentence(body):
            notice.append(tf.Mention(topic, "tender", "", value, p.text[:160], p.line, ref=p.ref))
        if not re.search(r"[一-鿿]", body) and sum(1 for ch in body if ch.isalpha() and ch.isascii()) >= 12:     # "–", "“ ”" are no Chinese
            # an English notice is sentences, not labelled lines: "X (the "Employer") invites tenders for Y.", "The closing
            # date for submission of tenders is 14 August 2029 at 4.00 pm." - one field a sentence, the field named early in it
            several = _english_notice(body)
            if several:
                for topic, value in several:
                    if not any(n.topic == topic for n in notice):
                        notice.append(tf.Mention(topic, "tender", "", value, p.text[:160], p.line, ref=p.ref))
                continue
            invites = re.match(r"(?:\d+(?:\.\d+)*\s+)?(.{4,80}?)\s*\((?:the\s+)?[\"“]?(?:Employer|Authority|Owner|Client)[\"”]?\)\s+invites\s+(?:tenders?|bids?|quotations?)\s+for\s+(?:the\s+)?(.{6,160}?)[.]?$", body)
            if invites:
                notice.append(tf.Mention("owner", "tender", "", invites.group(1).strip(), p.text[:160], p.line, ref=p.ref))
                notice.append(tf.Mention("project", "tender", "", invites.group(2).strip(), p.text[:160], p.line, ref=p.ref))
                continue
            hits = [h for h in tf._topic_hits(body) if h[2] not in tf._ALWAYS_OURS | tf._NO_SIDE | tf._STATEMENT_ONLY and tf._TOPIC[h[2]].kind in tf._KIND_RE]
            if len({h[2] for h in hits}) == 1 and len(body) <= 220 and (hits[0][0] <= 40 or tf._TOPIC[hits[0][2]].kind == "workhead"):
                value = _document_value(hits[0][2], body[hits[0][1]:] if tf._TOPIC[hits[0][2]].kind != "workhead" else body, same_as=False)
                if value and not any(n.topic == hits[0][2] for n in notice):
                    notice.append(tf.Mention(hits[0][2], "tender", "", value, p.text[:160], p.line, ref=p.ref))
                continue
        # a sentence that is nothing but the statement of one field: "工期60日历天。", "★投标保证金人民币20万元。"
        # Running text NEXT to a field word is not a field; a sentence that IS the field is.
        plain = body.lstrip("★☆＊ ").rstrip("。；; ")
        hits = tf._topic_hits(plain) if len(plain) <= 30 else []
        if len(hits) == 1 and hits[0][0] <= 2 and hits[0][2] not in tf._ALWAYS_OURS | tf._NO_SIDE | tf._STATEMENT_ONLY:
            kind = tf._TOPIC[hits[0][2]].kind
            value = _document_value(hits[0][2], plain[hits[0][1]:], same_as=False) if kind in tf._KIND_RE else ""
            if value and not any(n.topic == hits[0][2] and n.line == p.line for n in notice):
                notice.append(tf.Mention(hits[0][2], "tender", "", value, p.text[:160], p.line, ref=p.ref))
    if not any(m.topic == "track_record" for m in table + notice):
        for p in doc.pieces:
            if p.kind == "text" and p.text.isascii() and not p.addendum and not _CONTRACT.search(p.chapter):
                said = re.search(r"(?:Bidder|Tenderer)s? (?:shall|must) have (?:successfully )?(?:completed|executed|carried out)\s+([^.]{10,220})", p.text)
                if said:
                    notice.append(tf.Mention("track_record", "tender", "", said.group(1).strip()[:200], p.text[:160], p.line, ref=p.ref))
                    break
    if not any(m.topic == "validity" for m in table + notice):
        # "14.2 The Bid and bid security shall be valid until 12 December 2031." stands in the instructions, not in the notice
        for p in doc.pieces:
            if p.kind == "text" and not p.addendum and _INSTRUCTIONS.search(p.chapter) and not re.search(r"[一-鿿]", p.text):
                until = [value for topic, value in _english_notice(p.text) if topic == "validity"]
                if until:
                    notice.append(tf.Mention("validity", "tender", "", until[0], p.text[:160], p.line, ref=p.ref))
                    break
    if not any(m.topic == "pm" for m in table + notice):
        # an English ITT lays the requirement on the Project Manager down in a sentence of its instructions
        for p in doc.pieces:
            if p.kind == "text" and p.text.isascii() and _INSTRUCTIONS.search(p.chapter) and not p.addendum:
                said = re.match(r"(?:\d+(?:\.\d+)*\s+)?(The (?:proposed\s+)?Project (?:Manager|Director)\b[^.]{0,40}\bshall\b[^.]{8,200})", p.text)
                if said:
                    notice.append(tf.Mention("pm", "tender", "", said.group(1).strip()[:200], p.text[:160], p.line, ref=p.ref))
                    break
    mentions = list(table)
    # the cover fills only what is still missing: its lines are cut where the page was ("…项目-库" / "区改造维修")
    cover_lines = {p.line for p in noticed if p.table == "封面"}
    body_topics = {m.topic for m in table} | {m.topic for m in notice if m.line not in cover_lines}
    for m in notice:
        if m.line in cover_lines and m.topic in body_topics:
            continue
        if m.topic not in have:
            if not any(x.topic == m.topic and _same(x.value, m.value) for x in mentions):
                mentions.append(m)
            continue
        same = [x for x in table if x.topic == m.topic]
        if any(x.value.startswith("同") for x in same):
            continue   # "开标时间：同投标截止时间" refers to the very date the notice gives: no contradiction
        if same and not any(_same(x.value, m.value) or _flat(m.value) in _flat(x.note) or _flat(x.value) in _flat(m.note) for x in same):
            # the notice and the front table disagree: both stand, and whoever reads the draft is told
            mentions.append(tf.Mention(m.topic, "tender", "", m.value, m.note, m.line, origin="招标公告，与前附表不一致", ref=m.ref))
    return mentions


def _flat(text: str) -> str:
    return re.sub(r"[\s,，]+", "", text or "")


_NO = re.compile(r"^(?:否|不|无|不接受|不允许|不得|不组织|不召开|不要求|不需要|不适用)$")
_YES = re.compile(r"^(?:是|有|接受|允许|组织|召开|要求|需要|适用)$")


def _same(a: str, b: str) -> bool:
    x, y = _flat(a), _flat(b)
    if (_NO.match(x) or _YES.match(x)) and (_NO.match(y) or _YES.match(y)):
        return bool(_NO.match(x)) == bool(_NO.match(y))     # 否 in the notice, 不接受 in the front table: one answer
    return x == y or x in y or y in x


_TICKED = "■☑✓✔√☒▣"
_UNTICKED = "□☐◻○"
_BOXES = re.compile("[" + _TICKED + _UNTICKED + "]")
_PLACEHOLDER = re.compile(r"^[（(]\s*(?:请)?(?:填写|填入|此处|项目名称|招标人名称|采购人名称)[^）)]*[)）]$|^[_＿/／\s-]*$")


def _chosen(body: str) -> Optional[str]:
    """A row of tick boxes lays down what is TICKED: "□不允许；☑允许，允许分包的专项工程…" says 允许. The text after each
    ticked box, up to the next box; None when the part is an option left unticked; the text itself when it has no box."""
    if not _BOXES.search(body):
        return body
    chosen = [found.group(1).strip(" ；;，,/／") for found in re.finditer("[" + _TICKED + "]([^" + _TICKED + _UNTICKED + "]*)", body)]
    chosen = [c for c in chosen if c]
    return "；".join(chosen) if chosen else None


def _balanced(value: str, source: str) -> str:
    """A value keeps the bracket it opened: "…合格证书（B证）"."""
    for opened, closed in (("（", "）"), ("(", ")")):
        if value.count(opened) > value.count(closed):
            at = source.find(value)
            if at >= 0 and source[at + len(value):at + len(value) + 1] == closed:
                value += closed
    return value


def _document_value(topic: str, body: str, *, same_as: bool = True) -> str:
    """The literal value of a field inside the content laid down for it. A document's text value may be a
    clause long; it is kept whole up to the sentence end and cut only by the table cell."""
    from packing_assistant.tools import tender_facts as tf

    kind = tf._TOPIC[topic].kind
    text = body.strip().rstrip("。；; ")
    if kind in tf._KIND_RE:
        found = tf._KIND_RE[kind].search(text)
        if not found and len(text) <= 24 and re.search(r"不适用|不要求|不收取|不需要|无需|免收|免交|免缴|不设|不组织|不集中|不召开|不统一|不安排|自行", text):
            return text     # "磋商保证金：本项目不适用" - no amount, and that is what is laid down
        if not found:
            # "开标时间：同投标截止时间" - as written. Not under another field's row: the 递交截止时间 of the
            # 投标保证金 row is the bond's, not the bid's.
            said = re.match(r"同[^，,；;。]{2,12}(?:时间|日期)", text) if (kind == "date" and same_as) else None
            return said.group(0) if said else ""
        value = found.group(0).strip()
        if kind == "time" and (re.search(r"(?:当年|次年|每年|年)\s*$", text[:found.start()]) or re.match(r"\s*(?:之前|以前|前|底|末|份)", text[found.end():])):
            return text[:80]    # "于当年12 月之前完成调查工作": a date to finish by, as written - not twelve months
        if kind == "time" and len(tf._KIND_RE[kind].findall(text)) >= 2 and re.search(r"[，,、；;]", text) and len(text) <= 60:
            return text         # "防水工程 5 年，其他工程 2 年": one period per kind of work - the first alone would mislead
        if kind == "money" and len(tf._KIND_RE[kind].findall(text)) >= 2 and re.search(r"(?i)\bif\b[^.;]{0,40}\b(?:cash|surety|bond|guarantee|check|letter of credit)", text):
            return text[:240]   # "not less than Php… if in cash …; Php… if in Surety Bond": one amount per form - the first alone would mislead
        lots = re.search(r"第\s*[\d一二三四五六七八九十]+\s*(?:包|标段|标包)|[包标]\s*\d+\s*[:：-]", text)
        if lots and len(tf._KIND_RE[kind].findall(text)) >= 2:
            # "第1包390.66万元,第2包398.76万元,第3包424.72万元": one value per lot - the first alone would read as the total
            return text[:120]
        if kind == "date":
            clock = tf._CLOCK.match(text[found.end():].lstrip("，, "))
            if clock:
                value = text[found.start():found.end() + (len(text[found.end():]) - len(text[found.end():].lstrip("，, "))) + clock.end()].strip()
        return value
    if kind == "method":
        found = tf._EVAL_METHOD.search(text)
        if not found and re.fullmatch(r"[一-鿿]{2,10}法", text):
            return text      # "评标办法 | 评审价法。": a method this reader has no word for, named by the row that is about it
        return found.group(0) if found else ""
    if kind == "code":
        found = (tf._DOC_CODE.search(text) or re.match(r"[A-Za-z0-9][A-Za-z0-9\-_/]{5,60}", text)   # "310115…-15372573": all digits
                 or re.match(r"[一-鿿]{1,6}[-－—][A-Za-z0-9][A-Za-z0-9\-_/]{1,30}", text))                # "青中-2711"
        return found.group(0) if found else ""
    if kind == "person":
        return _balanced(tf._requirement_text(text), text) or text[:80]
    if topic == "track_record" and not re.search(r"[一-鿿]", text):
        listed = re.search(r"(?:which shall be|as follows|the following)\s*[:：]\s*(.+)$", text)
        if listed:
            return listed.group(1).strip()[:118]    # "… refer to contracts which have the same major categories of work, which shall be: a. …"
    if topic in ("binding", "signing"):
        return text[:160]    # how a bid is bound or signed is the whole cell: "…应分别装订成册。左侧胶装…不得采用活页装订"
    first = re.split(r"[。]", text)[0].strip()
    return first[:120]


_HANDING_IN = re.compile(r"(?:投标|响应|报价|应答|竞价|申请)文件[^，,。；;]{0,4}(?:提交|递交|送达|接收)|(?:提交|递交|送达|接收)[^，,。；;]{0,4}(?:投标|响应|报价|应答|竞价|申请)文件")


def _label_under(label: str, under: str) -> str:
    """The field a bare label names under the heading (or the row name) it stands under: "截止时间：…" under 四、响应文件提交
    is the bid's deadline, "地点：…" under 五、开启 where bids are opened. What is handed in has to be THE BID - "地点：…"
    under 三、递交投标登记文件 is where to register."""
    label, under = re.sub(r"\s+", "", label or ""), re.sub(r"\s+", "", under or "")
    if not under or re.search(r"保证金|保函|担保|质疑|澄清|异议|答疑|领取|获取|发售|登记|报名|踏勘", under):
        return ""
    handing, opening = _HANDING_IN.search(under), re.search(r"开启|开标", under)
    if re.fullmatch(r"(?:提交|递交)?截止(?:时间|日期)", label) and handing:
        return "deadline_bid"
    if re.fullmatch(r"(?:开启|开标)?时间", label) and opening:
        return "deadline_open"
    if re.fullmatch(r"(?:开启|开标)?地点", label) and opening:
        return "open_place"
    if re.fullmatch(r"(?:提交|递交)?地点", label) and handing:
        return "submit_place"
    return ""


_EN_DATE =(r"\d{1,2}(?:st|nd|rd|th)?\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s*,?\s*\d{4}"
            r"|(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?\s*,?\s*\d{4}")
_EN_CLOCK = r"(?:\s*(?:at|,)?\s*\d(?:\s?\d)?\s*[:.]\s*\d\s*\d\s*(?:[AaPp]\.?\s*[Mm]\.?)?)?"       # "1 0:00 A.M.": the text layer broke the hour


def _english_notice(sentence: str) -> List[Tuple[str, str]]:
    """The fields an English invitation lays down in its sentences - several a sentence, each by the words that cannot
    be mistaken: "…the sum of … (USD 1,250,000.00) … being the Approved Budget for the Contract (ABC) to payments under
    the contract for the “…” under Project Identification (ID) Number HW-2031-07", "… within Ninety (90) calendar days",
    "Bids must be duly received … on or before 14 August 2031 at 9:00 A.M.", "Bid Opening shall be on …",
    "The Bid and bid security shall be valid until 12 December 2031"."""
    out: List[Tuple[str, str]] = []
    text = re.sub(r"\s+", " ", sentence)

    def put(topic: str, value: str) -> None:
        value = value.strip(" ,;.")
        if value and not any(t == topic for t, _ in out):
            out.append((topic, value))

    budget = re.search(r"\(\s*((?:PHP|PhP|Php|₱|USD|US\$|S\$|SGD|\$)\s*[\d,]+(?:\.\d+)?)\s*\)[^.]{0,80}?(?:being|is|as) the (?:Approved Budget for the Contract|approved budget)", text)
    if budget:
        put("budget", budget.group(1))
        owner = re.match(r"(?:\d+\.\s*)?The (.{4,90}?)(?:,)? (?:through [^,]{3,60}? )?intends to apply", text)
        if owner:
            put("owner", owner.group(1))
    named = re.search(r"(?:under|of) the contract for (?:the )?[“\"]\s*([^”\"]{6,200}?)\s*[”\"]", text)
    if named:
        put("project", named.group(1))
    number = re.search(r"(?:Project )?Identification (?:\(ID\) )?(?:Number|No\.?)\s*[:：]?\s*([A-Za-z0-9][\w./-]{2,30})", text)
    if number:
        put("tender_no", number.group(1))
    # "within Ninety (90) calendar days", "within One Hundred Twenty (120) calendar days": the number spelt out, then in figures
    spelt = r"(?:(?i:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|and)[ -]+){1,6}"
    period = re.search(r"\bwithin ((?:" + spelt + r")?\(\s*\d+\s*\) (?:calendar |working )?(?:days|months)|\d+ (?:calendar |working )?(?:days|months))", text)
    if period and re.search(r"(?i)deliver|complet|contract duration|notice to proceed", text):
        put("duration", period.group(1))
    received = re.search(r"(?i:bids? (?:must|shall|should) be (?:duly )?(?:received|submitted)|deadline for (?:the )?submission).{0,400}?on or before\s+((?:" + _EN_DATE + r")" + _EN_CLOCK + r")", text)
    if received:
        put("deadline_bid", received.group(1))
        # up to the LAST "copy" of the clause - "hardcopy" inside a bracket is not where it ends
        copies = re.search(r"((?:one|two|three|four|five|six|\d+)\s*\(\s*\d+\s*\)\s*original cop(?:y|ies)\b(?:[^.;]{0,100}\bcop(?:y|ies)\b)?)", text)
        if copies:
            put("copies", copies.group(1))
    opening = re.search(r"(?i:bid opening|opening of bids) (?:shall|will) be (?:held )?on\s+((?:" + _EN_DATE + r")" + _EN_CLOCK + r")", text)
    if opening:
        put("deadline_open", opening.group(1))
    valid = re.search(r"(?i:\bbids?(?: and bid securit(?:y|ies))? shall (?:be|remain) valid)\s+(until\s+(?:" + _EN_DATE + r")|for\s+[^.;,]{3,60})", text)
    if valid:
        put("validity", valid.group(1))
    return out


_STRICT = (("tender_no", re.compile(r"(?:招标|项目|采购)编号\s*[：:]\s*([A-Za-z0-9][A-Za-z0-9\-_/〔〕\[\]（）()]{3,40})")),)


def _strict_in_sentence(sentence: str) -> List[Tuple[str, str]]:
    """Inside the notice's running text only what cannot be mistaken: "招标编号：LJZB-2026-SG-0418",
    "投标截止时间…为2026年11月3日9时30分"."""
    from packing_assistant.tools import tender_facts as tf

    out: List[Tuple[str, str]] = []
    for topic, pattern in _STRICT:
        found = pattern.search(sentence)
        if found:
            out.append((topic, found.group(1).rstrip("。，,；;")))
    for start, end, topic in tf._topic_hits(sentence):
        if tf._TOPIC[topic].kind == "date":
            value = _document_value(topic, sentence[end:])
            if value and sentence[end:].find(value) <= 24:
                out.append((topic, value))
    return out


# ---------------------------------------------------------------------------
# what gets a bid rejected
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Rejection:
    piece: Piece
    cited: Tuple[Piece, ...]  # the clauses it points at ("第1.4.3项规定的任何一种情形")
    star: bool


def _rejecting_parts(sentence: str) -> List[str]:
    """A sentence, or - when it is a run of "；"-separated items of which one says 不得参加投标 - that item.
    A sentence that announces a list ("有下列情形之一的，否决其投标：（1）…；（2）…") stays whole: its items are
    what it is about."""
    parts = [part.strip() for part in re.split(r"[；;]", sentence) if part.strip()]
    if len(parts) < 3 or len(sentence) <= 160:
        return [sentence]
    lead = next((i for i, part in enumerate(parts) if _REJECT.search(part) or _STAR.search(part)), None)
    if lead is None or re.search(r"[：:]", parts[lead]) and lead + 1 < len(parts) and _LIST_ITEM.match(parts[lead + 1]):
        return [sentence]
    return [part for part in parts if _REJECT.search(part) or _STAR.search(part)]


_CELL_ITEM = re.compile(r"^\s*(?:[（(]\s*\d{1,2}\s*[)）]|[①-⑳]|\d{1,2}\s*[)）])")
_CELL_CUT = re.compile(r"[；;。]|(?<=[：:，,])(?=\s*(?:[（(]\s*\d{1,2}\s*[)）]|[①-⑳]))|(?<=[：:])(?=\s*\d{1,2}\s*[)）])|(?<=[^\d.（(第除])(?=[①-⑳](?![条项款种情]))")


def _cell_items(text: str, named: bool = False) -> List[Tuple[str, bool]]:
    """The sentences of a front-table cell and, for each, whether it is an ITEM of a list that a rejecting sentence
    announced. One cell may hold the whole rejection list of a tender: "1.投标文件存在以下情形之一的…其投标文件将被否决：
    （1）资格审查内容：①投标人不满足…的；②…（2）初步评审内容：①…③…视为串通投标行为…：1）…；2）…". The items carry no
    rejecting word of their own - the sentence over them does."""
    out: List[Tuple[str, bool]] = []
    listing = named      # the row's own name may announce the list: "电子投标文件的拒收情形 | 1.投标截止时间后送达…；2.…"
    for segment in _CELL_CUT.split(text):
        segment = segment.strip()
        if not segment:
            continue
        item = bool(_CELL_ITEM.match(segment))
        body = _CELL_ITEM.sub("", segment).strip()
        if _REJECT.search(segment) and not _NOT_A_REJECTION.search(segment):
            out.append((segment, False))
            if re.search(r"[：:]\s*$|下列|以下|如下", segment):
                listing = True
        elif listing and (re.match(r"注\s*[：:]", segment) or (not named and re.match(r"\d{1,2}\s*[.．、](?!\d)", segment))):
            listing = False      # "2.在…": the next numbered sentence of the cell, and it rejects nothing
        elif listing and item and len(body) <= 14 and body.endswith(("：", ":")):
            continue             # "（1）资格审查内容：" heads a group of items
        elif listing and not item and segment.endswith(("：", ":")):
            listing = False      # "…所列1.4.3条“骗取中标”是指：" announces another list - of definitions, and it rejects nothing
        elif listing and len(body) >= 4:
            out.append((segment, True))     # an item - or the second half of one ("…盖章的；投标文件未经法定代表人…盖章的")
    return out


def rejections(doc: Document) -> List[Rejection]:
    out: List[Rejection] = []
    seen: set = set()
    for p in doc.pieces:
        if p.kind not in ("text", "row") or _CONTRACT.search(p.chapter):
            continue
        if _WORKS_CHAPTER.search(p.chapter) and not _ABOUT_BID.search(p.text):
            continue
        texts = [p.text] if p.kind == "text" else [c for c in p.cells[2:] or p.cells]
        if p.kind == "row" and "前附表" not in p.table and not any(_REJECT.search(c) for c in p.cells):
            marks = [c for c in p.cells if _STAR.search(c)]
            if marks and all(not _STAR.sub("", c).strip() for c in marks) and (
                    re.search(r"隐患|风险|危险源", "".join(p.header)) or not re.search(r"要求|参数|指标|规格|条款|响应|标准", "".join(p.header))):
                continue    # a ★ alone in a 备注 cell of a hazard list marks a MAJOR HAZARD, not a term of the bid
        if p.kind == "row" and "前附表" not in p.table and any(_STAR.search(c) or _REJECT.search(c) for c in p.cells):
            # a row of a requirements table: "沥青混凝土面层：★上面层采用…；不满足的为无效投标" - the cells, not the bars
            cells = [c for c in p.cells if c and c != "—" and not re.fullmatch(r"[\d.]+", c)]
            texts = ["：".join(cells[:2]) + ("；" + "；".join(cells[2:]) if len(cells) > 2 else "")]
        for text in texts:
            whole_row = p.kind == "row" and "前附表" not in p.table
            if whole_row and len(text) > 160:
                # a cell as long as a page ("响应报价得分=…【异常报价】…将被作为无效响应处理"): the sentence that rejects
                found = [s for s in re.split(r"(?<=[。；;])", text) if _REJECT.search(s) or _STAR.search(s)]
                rows_text = found or [text]
            else:
                rows_text = [text]
            if p.kind == "text":
                candidates = [(s, False) for s in _rejecting_parts(text)]
            elif whole_row:
                candidates = [(s, False) for s in rows_text]
            else:
                name_cell = p.cells[1].strip() if len(p.cells) >= 3 else ""
                candidates = _cell_items(text, named=bool(name_cell) and len(name_cell) <= 20 and bool(_REJECT.search(name_cell)))
                known = {c[0] for c in candidates}
                candidates += [(s, False) for s in re.split(r"[；;。]", text) if _STAR.search(s) and s.strip() not in known]
            for sentence, announced in candidates:
                sentence = sentence.strip()
                # "注：备注栏中加“★”标记的为重大隐患" explains the mark; it is not an item that bears it
                star = bool(_STAR.search(re.sub(r"[“\"‘'「]\s*[★☆＊]\s*[”\"’'」]", "", sentence)))
                if not sentence or not (star or announced or (_REJECT.search(sentence) and not _NOT_A_REJECTION.search(sentence))):
                    continue
                if star and p.kind == "text" and len(_STAR.sub("", sentence).strip(" ：:")) <= 10 and sentence.rstrip().endswith(("：", ":")):
                    # "★附件 1：" - a starred label with nothing behind it: the form's title stands on the next line
                    at = doc.pieces.index(p)
                    title = next((q for q in doc.pieces[at + 1:at + 3] if q.kind in ("text", "heading")), None)
                    if title is not None and len(title.text) <= 40 and not _STAR.search(title.text):
                        sentence += title.text
                key = _flat(sentence)
                if key in seen:
                    continue
                seen.add(key)
                cited: List[Piece] = []
                for number in _CITES.findall(sentence):
                    if number != p.number:
                        cited += [c for c in doc.pieces if c.number == number and c.kind == "text" and c is not p][:6]
                piece = p if (p.kind == "text" and sentence == p.text) else replace(p, text=sentence)
                out.append(Rejection(piece, tuple(cited), star))
                if p.kind == "text" and not star and _LIST_LEAD.search(sentence):
                    at = doc.pieces.index(p)
                    for item in doc.pieces[at + 1:at + 40]:
                        if item.kind != "text" or not _LIST_ITEM.match(item.text):
                            break
                        mark = _LIST_ITEM.match(item.text).group(0).strip()
                        if _flat(item.text) in seen:
                            continue
                        seen.add(_flat(item.text))
                        number = f"{p.number}{mark}" if p.number else mark
                        out.append(Rejection(replace(item, number=number), (), False))
    return out


_WEAK = re.compile(r"无效|拒绝|拒收|否决|不予|取消[^，。；]{0,8}资格|失去[^，。；]{0,8}资格|不得|视为(?:自动)?放弃|不合格|不通过|未通过|不接受|不被接受|不响应|"
                   r"作废|没收|淘汰|出局|排除在外|终止[^，。；]{0,6}(?:资格|评审)|shall not|will not be (?:considered|accepted)|rejected|disqualif", re.I)
_BIDDER_SIDE = re.compile(r"投标|响应|报价|供应商|磋商|谈判|比选|竞标|应答|申请人|参选|资格|保证金|tender|bid|proposal|supplier", re.I)


def rejection_candidates(doc: Document, limit: int = 80) -> List[Piece]:
    """Sentences that MAY get a bid thrown out and are not on the rejection list: a weak sign of a fatal
    consequence (无效 / 拒绝 / 不予 / 不得 / 取消…资格 …) in a sentence about the bidder's side, outside the
    contract conditions. The rejection list knows a vocabulary; a template that uses another gets a third to
    a half of its clauses onto it. This net is wide on purpose and is shown apart: it costs a person a minute
    per page of it, and a missed clause costs the bid."""
    listed = {_flat(r.piece.text) for r in rejections(doc)} | {_flat(p.text) for p in obligations(doc)}
    out: List[Piece] = []
    seen: set = set()
    pieces = doc.pieces
    for index, p in enumerate(pieces):
        if p.kind not in ("text", "row") or _CONTRACT.search(p.chapter) or "格式" in p.chapter:
            continue
        if _WORKS_CHAPTER.search(p.chapter) and not _ABOUT_BID.search(p.text):
            continue
        texts = [p.text] if p.kind == "text" else [c for c in p.cells[1:] if c]
        for text in texts:
            for sentence in ([text] if p.kind == "text" else re.split(r"[；;。]", text)):
                sentence = sentence.strip()
                key = _flat(sentence)
                # a front-table row is about the bid whatever words it uses; running text has to say whose business it is
                about_bid = _BIDDER_SIDE.search(sentence) or (p.kind == "row" and "前附表" in p.table)
                if (not sentence or key in seen or key in listed or any(key in done or done in key for done in listed if len(done) >= 8)
                        or not (_WEAK.search(sentence) and about_bid)):
                    continue
                seen.add(key)
                out.append(p if sentence == p.text else replace(p, text=sentence))
                if p.kind == "text" and _LIST_LEAD.search(sentence):
                    for item in pieces[index + 1:index + 40]:
                        if item.kind != "text" or not _LIST_ITEM.match(item.text):
                            break
                        if _flat(item.text) not in seen | listed:
                            seen.add(_flat(item.text))
                            out.append(item)
                if len(out) >= limit:
                    return out
    return out


_REVIEW_GROUP = re.compile(r"(?:形式|资格|响应性|符合性|实质性)[^，。；]{0,8}评审")
_ITEM_MARK = re.compile(r"[（(]\s*(\d{1,2})\s*[)）]")


def review_standards(doc: Document) -> List[Tuple[str, str, str, Piece]]:
    """The standards every bid is checked against before it is scored - (group, factor, standard, where). One that
    is not met and the bid goes no further, whatever words the tender uses for that.

        | 形式评审标准 | 供应商名称 | 与营业执照、资质证书、安全生产许可证一致 |          a row each
        | 2.1.2 | 资格评审标准 | （1）投标人具备有效的营业执照…（2）投标人的资质等级…  |          one cell, a numbered list
    """
    out: List[Tuple[str, str, str, Piece]] = []
    seen: set = set()
    for p in doc.pieces:
        if p.kind != "row" or _CONTRACT.search(p.chapter) or _WORKS_CHAPTER.search(p.chapter):
            continue
        at = next((i for i, cell in enumerate(p.cells) if _REVIEW_GROUP.search(cell) and len(cell) <= 30), None)
        if at is None:
            # the group stands over the table ("2.形式评审", "符合性审查要求"), the columns say what they hold
            factor_at = next((i for i, h in enumerate(p.header) if re.fullmatch(r"(?:评审|检查|审查)(?:因素|项目|内容项)|项目内容|审查项|评审项", h.strip())), None)
            standard_at = next((i for i, h in enumerate(p.header) if re.fullmatch(r"(?:评审|检查|审查)(?:标准|内容|要求)|合格条件|合格标准|通过条件|通过标准", h.strip())), None)
            if (factor_at is None and standard_at is not None and len(p.header) >= 2 and p.header[0].strip() in _FRONT_HEADER
                    and all(re.fullmatch(r"是否[一-鿿]{2,8}|结论|评审结论|评审结果|备注|说明", h.strip()) for i, h in enumerate(p.header) if i not in (0, standard_at))):
                factor_at = 0       # 序号 | 评审内容 | 是否满足要求: the content IS the standard, the rest is for the committee's ticks
            if (factor_at is not None and standard_at is not None and max(factor_at, standard_at) < len(p.cells)
                    and re.search(r"评审|审查|检查", p.heading) and _EVALUATION.search(p.chapter + p.heading)
                    and not re.fullmatch(r"结论|合计|总计|小计", p.cells[0].strip())):
                key = (_flat(p.heading), _flat(p.cells[factor_at]), _flat(p.cells[standard_at]))
                if p.cells[standard_at].strip() and key not in seen:
                    seen.add(key)
                    factor = p.cells[factor_at].strip() if factor_at else ""
                    out.append((re.sub(r"^[\d.．、\s（）()]+", "", p.heading), factor, p.cells[standard_at].strip(), p))
            continue
        group = p.cells[at].strip()
        rest = [cell.strip() for cell in p.cells[at + 1:] if cell.strip()]
        if not rest:
            continue
        if len(rest) >= 2:
            entries = [(rest[0], "；".join(rest[1:]))]
        else:
            marks = [m for m in _ITEM_MARK.finditer(rest[0])]
            # "（1）…（2）…": the items of a list - numbered 1, 2, 3 in turn, not a bracketed figure inside a sentence
            starts: List[Tuple[int, int]] = []
            for mark in marks:
                n = int(mark.group(1))
                if (not starts and (n == 1 or mark.start() <= 2)) or (starts and n == starts[-1][1] + 1):
                    starts.append((mark.start(), n))
            if len(starts) >= 2:
                cuts = [s for s, _ in starts] + [len(rest[0])]
                lead = rest[0][:cuts[0]].strip(" ：:；")
                entries = [(lead, rest[0][a:b].strip(" ；;")) for a, b in zip(cuts, cuts[1:])]
            else:
                entries = [("", rest[0])]
        for factor, standard in entries:
            key = (_flat(group), _flat(factor), _flat(standard))
            if standard and key not in seen:
                seen.add(key)
                out.append((group, factor, standard, p))
    return out


def obligations(doc: Document) -> List[Piece]:
    """Front-table content that lays down a 须 / 不得 - binding, sealing, delivery, the account a transfer
    comes from. No rejection word stands in them; the formal review rejects for them all the same."""
    out: List[Piece] = []
    rejected = {_flat(r.piece.text) for r in rejections(doc)}
    for row in front_rows(doc):
        for part in re.split(r"[；;]", row.content):
            part = part.strip()
            if _BOXES.search(part):
                # one option of a row of tick boxes: what is ticked is the row's field, already shown as one
                part = _chosen(part) or ""
                if len(part) <= 8:
                    continue
            if part and _OBLIGES.search(part) and _flat(part) not in rejected:
                out.append(replace(row.piece, number=row.number, heading=row.name, text=part))
    # the notice lays down who may bid at all: "本次招标不接受联合体投标", "拟派项目经理须具备…"
    said = {_flat(p.text) for p in out}
    for p in doc.chapter("第一章"):
        about_bidders = "资格" in p.heading or re.search(r"投标人|联合体|项目经理|项目负责人", p.text)
        if p.kind == "text" and about_bidders and _OBLIGES.search(p.text) and _flat(p.text) not in rejected | said:
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# scoring rows, named 危大 items, the documents a bid must contain
# ---------------------------------------------------------------------------
#: a cell that says HOW points are won ("有 1 项类似业绩得 2 分，满分 10 分"), not what is scored ("报价得分")
_SCORING_RULE = re.compile(r"\d\s*分|.{8,}(?:得分|满分|加分|扣分)|(?:得分|满分|加分|扣分).{8,}")


#: "报价（60分）", "内容完整性（0-1 分）", "商务（投标报价）；（35 分）" - the points stand inside the name cell
_NAMED_POINTS = re.compile(r"\s*(.*?)\s*[；;]?\s*[（(]\s*(?:满分|共|计)?\s*(\d+(?:\.\d+)?(?:\s*[-~～—－]\s*\d+(?:\.\d+)?)?)\s*分\s*[)）]\s*")
_SCORE_HEADER = re.compile(r"评分(?:标准|因素|内容|项目|细则|办法|要点|项|指标)|评审(?:因素|项目)|评价(?:内容|标准|因素)|分值项|评议(?:内容|项目)|打分")


def _named_points(cell: str) -> List[Tuple[str, str]]:
    """The (name, points) a cell holds when it is NOTHING BUT names with their points: one ("报价（60分）") or several
    run together ("进货渠道（3 分）售后网点（4 分）"). A sentence that mentions points ("…的得5分") holds none."""
    text = cell.strip()
    if not text or len(text) > 90:
        return []
    found: List[Tuple[str, str]] = []
    cursor = 0
    while cursor < len(text):
        unit = _NAMED_POINTS.match(text, cursor)
        if not unit or unit.end() == cursor:
            return []
        name = unit.group(1).strip(" ；;：:")
        if len(name) > 40 or re.search(r"[。！？]|得\s*\d|扣\s*\d|加\s*\d", name):
            return []
        found.append((name, re.sub(r"\s+", "", unit.group(2)) + "分"))
        cursor = unit.end()
    return found


def scores(doc: Document) -> List[Tuple[str, str, Piece]]:
    out: List[Tuple[str, str, Piece]] = []
    seen: set = set()
    judged = any(_EVALUATION.search(p.chapter) for p in doc.pieces)     # the document has a chapter on how bids are judged
    for p in doc.pieces:
        # "2.2.6计算商务报价得分（满分100分）" - where price is all that is scored there is no table, only this heading
        if p.kind in ("heading", "text") and len(p.text) <= 40 and (_EVALUATION.search(p.chapter) or "评标" in p.chapter):
            full = re.search(r"(?:计算)?([一-鿿]{2,10}?)得分\s*[（(]\s*满分\s*(\d+(?:\.\d+)?)\s*分\s*[)）]", p.text)
            if full and (_flat(full.group(1)), full.group(2)) not in seen:
                seen.add((_flat(full.group(1)), full.group(2)))
                out.append((full.group(1), full.group(2) + "分", p))
    cut: Dict[int, str] = {}       # column -> a name the page cut before its points ("主要施工方案与技术措")
    for p in doc.pieces:
        if p.kind != "row":
            cut = {} if p.kind == "header" else cut
            continue
        by_header = bool(_SCORE_HEADER.search("".join(p.header)))
        if not by_header and "评标" not in p.chapter and "评审" not in p.chapter and "评分" not in p.table and not _EVALUATION.search(p.chapter):
            continue
        if _CONTRACT.search(p.chapter) or _WORKS_CHAPTER.search(p.chapter) or (judged and not _EVALUATION.search(p.chapter)):
            continue    # the contract scores the contractor's PERFORMANCE (考核评分表); that is not how the bid is scored
        named = [_named_points(c) for c in p.cells]
        for i, units in enumerate(named):
            for name, value in units:
                if len(name) < 3 and cut.get(i):
                    name = cut[i] + name        # the page cut the name: what the last row left and what this one begins with
                name = re.sub(r"(?<=[一-鿿]{2})评分标准$", "", name)      # "施工组织设计评分标准（35分）" is the factor 施工组织设计
                key = (_flat(name), _flat(value))
                if (len(name) >= 2 and key not in seen and not re.fullmatch(r"[\d.()（）；\s]+", name)
                        and not re.fullmatch(r"优秀?|良好?|中等?|较[好差优]|一般|合格|不合格|基本[一-鿿]{2}", name)):      # a grade's band, not an item
                    seen.add(key)
                    out.append((name, value, p))
        # a name that holds no points: the page may have cut it off before them ("主要施工方案与技术措" / "施；（0-4 分）")
        cut = {i: c.strip() for i, c in enumerate(p.cells) if not named[i] and 4 <= len(c.strip()) <= 30 and not re.search(r"[，。；：:]|\d\s*分", c)}

        column = next((i for i, h in enumerate(p.header) if re.fullmatch(
            r"分值|分数|满分|权重|标准分|分值分配|分值区间|分值范围|分值[（(]分[)）]|(?i:weightage|weighting|weight(?:\s*\(%\))?|points?|marks?|max(?:imum)? (?:score|points|marks))", h.strip())), None)
        if column is not None and (column >= len(p.cells) or column < 1 or named[column - 1]):
            column = None       # "价格（30分） | 30": the name cell said it already
        spots = [i for i, c in enumerate(p.cells) if i and _POINTS.match(c) and not named[i - 1]]
        if any(named) and column is None and not spots:
            continue    # every score of this row stood inside a name cell; with a points cell of its own beside a named
            #             one ("施工组织设计评分标准（35分） | 内容完整性 | 5分") the named cell is the FACTOR, and the item follows
        if (column is not None and column < len(p.cells) and column >= 1 and re.fullmatch(r"\d+(?:\.\d+)?\s*(?:%|points?|marks?)", p.cells[column].strip(), re.I)):
            name, value = p.cells[column - 1].strip(), p.cells[column].strip()      # "Price | 60%": a weight is the score as it is written
            if name and (_flat(name), _flat(value)) not in seen:
                seen.add((_flat(name), _flat(value)))
                out.append((name, value, p))
            continue
        if (column is not None and column < len(p.cells) and column >= 1
                and re.fullmatch(r"\d+(?:\.\d+)?(?:\s*[~～\-—]\s*\d+(?:\.\d+)?)?(?:\s*分)?", p.cells[column].strip())):
            name = p.cells[column - 1].strip()
            if column >= 2 and _SCORING_RULE.search(name) and p.cells[column - 2].strip() and not re.fullmatch(
                    r"[\d.()（）；\s]+", p.cells[column - 2].strip()):
                name = p.cells[column - 2].strip()     # "工程业绩 | 有 1 项类似业绩得 2 分，满分 10 分 | 10": the cell before the rule
            value = p.cells[column].strip()
            key = (_flat(name), _flat(value))
            if name and len(name) <= 30 and key not in seen:
                seen.add(key)
                out.append((name, value if value.endswith("分") else value + "分", p))
            continue
        for at in spots[1:]:
            # a second points cell is the ITEM's under its factor: "施工组织设计 | 28.0分 | 总体施工布置及规划 | 4.0分"
            name = p.cells[at - 1].strip()
            key = (_flat(name), _flat(p.cells[at]))
            if 2 <= len(name) <= 30 and not _POINTS.match(name) and not _SCORING_RULE.search(name) and key not in seen:
                seen.add(key)
                out.append((name, p.cells[at].strip(), p))
        at = spots[0] if spots else None
        points = p.cells[at] if at is not None else ""
        if points and len(p.cells) >= 3:
            # the name stands to the LEFT of the points, wherever in the row the points are
            # ("2.2.4（2）| 主要人员 | 5分 | 满足…得3分 | 3-5分")
            item = p.cells[at - 1]
            if (at >= 2 and _SCORING_RULE.search(item) and p.cells[at - 2].strip()
                    and not re.fullmatch(r"[\d.()（）；\s]+", p.cells[at - 2].strip())):
                item = p.cells[at - 2]      # "工程业绩 | 有 1 项类似工程业绩得 3 分，满分 12 分 | 12": the name is the cell before the rule
            # a long name: the factor in the cell before it - or, where that is the clause number (a scan's reading runs
            # factor and item into one cell), the name itself without its "评分标准（35分）"
            before = p.cells[at - 2].strip() if at >= 2 else ""
            wide = before if (before and not re.fullmatch(r"[\d.()（）；\s]+", before)) else item
            name = item if len(item) <= 24 else re.sub(r"评分标准|[（(][^）)]*[)）]", "", wide).strip(" ；;")
            key = (_flat(name), _flat(points))
            if name and not re.fullmatch(r"[\d.\-~～—\s]+分?|[\d.()（）；\s]+", name) and key not in seen:
                seen.add(key)
                out.append((name, points.strip(), p))
            continue
        for text in p.cells[2:]:
            if "分值" in "".join(p.cells[:2]) or "分值构成" in text:
                for name, value in _POINT_PART.findall(text):
                    key = (_flat(name), _flat(value))
                    if key not in seen:
                        seen.add(key)
                        out.append((name.strip(), re.sub(r"\s+", "", value), p))
    return out


def specials(doc: Document):
    from packing_assistant.tools import tender_facts as tf

    out = []
    seen: set = set()
    for p in doc.pieces:
        if p.kind != "text" or _CONTRACT.search(p.chapter):
            continue
        for clause in re.split(r"[；;]", p.text):
            for found in tf._SPECIAL.finditer(clause):
                hazard = re.search(tf._HAZARD, found.group(0))
                name = found.group(0)[hazard.start():] if hazard else found.group(0)  # "深基坑专项施工方案", without 须编制
                # the figure may stand in the clause before: "基坑开挖深度6.8米，…，须编制深基坑专项施工方案"
                # ... or anywhere in a sentence that names this one item only; another item's figure is not this one's
                alone = len(tf._SPECIAL.findall(p.text)) == 1
                detail = tf._SPECIAL_DETAIL.search(clause) or (tf._SPECIAL_DETAIL.search(p.text) if alone else None)
                key = hazard.group(0) if hazard else name
                if key in seen:
                    continue
                seen.add(key)
                out.append((name, detail.group(0).strip() if detail else "", p))
    return out


def forms(doc: Document) -> List[Tuple[str, Piece]]:
    out: List[Tuple[str, Piece]] = []
    seen: List[str] = []

    def add(name: str, piece: Piece) -> None:
        name = re.sub(r"^[\s、.．]+|[\s。；;.]+$", "", name)
        latin = not re.search(r"[一-鿿]", name)      # "bidder’s" holds a curly apostrophe and is English all the same
        if not (2 <= len(name) <= (90 if latin else 30)) or re.search(r"规定的其他材料|其他材料$", name) and any("其他材料" in s for s in seen):
            return
        flat_name = _flat(name)
        # the same form named twice ("资格审查资料" / "资格审查资料（适用于…）") - but 投标函 and 投标函附录 are two forms: a name
        # of three characters inside a longer one proves nothing
        if any(flat_name == s or (min(len(flat_name), len(s)) >= 5 and (flat_name in s or s in flat_name)) for s in seen):
            return
        seen.append(flat_name)
        out.append((name, piece))

    composition = re.compile(r"[一-鿿]{2,8}文件的?(?:组成|构成)|[一-鿿]{2,8}文件由(?:下列|以下)|[一-鿿]{2,8}文件由[^。；：:]{2,40}(?:组成|构成)"
                             r"|(?i:(?:tender|bid|proposal)s? (?:shall|must|should) (?:comprise|consist of|include|contain) the following)")
    for row in front_rows(doc):
        # "3.1 | 投标文件的组成 | 商务标（主要包括下列内容）：（1）投标函；（2）投标函附录；（3）已标价工程量清单…"
        named = composition.search(row.name)
        if named and _ours(named.group(0)) and "其他" not in row.name and "评标办法" not in row.piece.table:
            for name in _FORM_ITEM.findall(row.content):
                add(name, row.piece)
    # "Section IX. Checklist of Technical and Financial Documents": (a) … (m), an item a paragraph
    for p in doc.pieces:
        # (the chapter's title may be cut by the line: "Checklist of Technical and Financial" / "Documents")
        if p.kind == "text" and re.search(r"(?i)\bchecklist of\b", p.chapter) and not re.search(r"[一-鿿]", p.text):
            item = re.match(r"\(\s*([a-z]{1,2})\s*\)\s+(.{6,900})$", p.text)
            if item:
                # as written, up to where the name ends and its conditions begin; a name longer than a line is cut at a
                # word, never reworded
                name = re.split(r"\s*(?:\bin accordance with\b|\bpursuant to\b|\bwhich shall\b|, if any|;|\band if applicable\b|, including\b|\bsimilar to\b)",
                                item.group(2))[0].strip()
                if len(name) > 90:
                    name = name[:90].rsplit(" ", 1)[0]
                add(name.rstrip(" ,;."), p)
    announced = False   # "投标文件由资格证明文件、商务技术文件、报价文件三部分组成：" - the lists follow, paragraph by paragraph
    announced_at = ""
    for p in doc.pieces:
        if _CONTRACT.search(p.chapter):
            continue    # 合同文件的组成 is the contract's
        if p.kind == "heading":
            found = composition.search(p.text)
            announced, announced_at = bool(found and _ours(found.group(0))), p.number
        elif p.kind == "text" and composition.search(p.text) and not _FORM_ITEM.search(p.text):
            announced, announced_at = _ours(composition.search(p.text).group(0)), p.number
            continue
        if announced and p.kind == "text" and re.search(r"[：:]\s*$", p.text) and not composition.search(p.text) and not _FORM_LIST.search(p.text):
            announced = False   # another list begins: "3.4.4 有下列情形之一的，保证金不予退还：" - what follows are no documents
        if announced and p.kind == "text" and _FORM_ITEM.search(p.text) and not _FORM_LIST.search(p.text):
            for name in _FORM_ITEM.findall(p.text):
                add(name, p)
            continue
        if p.kind == "heading" and "格式" in p.chapter and re.match(r"[" + _CN + r"]+、", p.text):
            add(re.sub(r"^[" + _CN + r"]+、\s*", "", p.text), p)   # "一、投标函": a form of its own
            continue
        if p.kind != "text":
            continue
        if (composition.search(p.heading) and _ours(composition.search(p.heading).group(0)) and not _FORM_LIST.search(p.text)
                and _FORM_ITEM.search(p.text)):
            for name in _FORM_ITEM.findall(p.text):
                add(name, p)
            continue
        if _FORM_LIST.search(p.text) and _ours(_FORM_LIST.search(p.text).group(0)):
            for name in _FORM_ITEM.findall(p.text):
                for single in re.split(r"及|和(?=投标函附录)", name) if "投标函及投标函附录" in name else [name]:
                    add(single, p)
        elif "投标文件格式" in p.chapter and p.text.startswith("目录"):
            for name in _FORM_DIR.findall(p.text):
                for single in re.split(r"及(?=投标函附录)", name):
                    add(single, p)
    return out


#: fields whose value in OUR documents can be set against the tender's and against one another
_OUR_TOPICS = ("duration", "validity", "warranty", "bond", "our_price", "quality", "pm", "tech_lead", "project", "tender_no")
_OUR_PRICE = re.compile(r"(?:投标总报价|投标报价|投标总价|总报价|报价)[^，,；;。]{0,24}?(?:[¥￥]\s*)?(\d[\d,，]*(?:\.\d+)?\s*(?:万元|亿元|元))")
_OUR_PRICE_BEFORE = re.compile(r"[¥￥]\s*(\d[\d,，]*(?:\.\d+)?\s*(?:万元|亿元|元)?)[）)]?\s*的?(?:投标总报价|投标报价|投标总价|总报价)")
_STAGE = re.compile(r"阶段|节点|其中|里程碑|分部|单体|楼栋|每层|标准层")
_QUALITY_WORD = re.compile(r"(?:工程)?质量(?:标准|目标|等级|要求)?\s*(?:达到|为|：|:|承诺)?\s*(合格|优良|优质工程|优质)")
_CONTINUES = re.compile(r"^(?:金额|总额|数额|额度|期限|时间)?\s*(?:为|即|共计?|计|：|:)")


def response_values(text: str, title: str):
    """What one of our own documents says about the fields a tender lays down: [Mention(side="ours", origin=title)].

    A bid document is ours from its first word to its last, so no cue is looked for. It is also long and full
    of numbers, so a value counts only when it stands in the same clause as its field word - "其中基础阶段120日历天"
    is a stage, not the 工期 - and nothing is carried from one clause to the next."""
    from packing_assistant.tools import tender_facts as tf

    found = []
    seen: set = set()
    line_no = 0
    header: List[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            header = []
            continue
        line_no += 1
        if _TABLE_ROW.match(line):
            cells = _cells(line)
            if all(_RULER.fullmatch(c) for c in cells if c):
                continue
            if not header:
                header = cells   # 报价一览表: the field words are the column names, the values stand under them
                continue
            pairs = [(cells[i], cells[i + 1]) for i in range(len(cells) - 1)]          # 项目经理 | 周建华
            pairs += [(header[i], cells[i]) for i in range(min(len(header), len(cells)))]  # 工期（日历天） over 200
            for label, body in pairs:
                topic = tf.document_topic(re.sub(r"[（(][^）)]*[)）]", "", label)) if label and body and "____" not in body else ""
                if topic not in _OUR_TOPICS:
                    continue
                if topic in ("pm", "tech_lead"):
                    value = tf._person(body) if len(body) <= 12 else ""
                elif topic == "our_price":
                    money = tf._MONEY.search(body)
                    bare = re.fullmatch(r"[¥￥]?\s*\d[\d,]*(?:\.\d+)?", body)
                    unit = re.search(r"[（(]\s*(万元|亿元|元)\s*[)）]", label)
                    # "投标总报价（元）" over "33,000,000.00": the unit stands in the column name
                    value = money.group(0).strip() if money else f"{body}{unit.group(1)}" if (bare and unit) else body if bare else ""
                elif topic == "quality":
                    value = body if len(body) <= 12 else ""
                else:
                    kind = tf._TOPIC[topic].kind
                    match = tf._KIND_RE[kind].search(body) if kind in tf._KIND_RE else None
                    unit = re.search(r"[（(]([^）)]+)[)）]", label)
                    value = (match.group(0).strip() if match else
                             f"{body}{unit.group(1)}" if (unit and re.fullmatch(r"\d+(?:\.\d+)?", body)) else "")
                if value:
                    _keep(found, seen, tf.Mention(topic, "ours", "", value, line[:160], line_no, origin=title))
            continue
        header = []
        for sentence in re.split(r"[。；;]", line):
            price = _OUR_PRICE_BEFORE.search(sentence) or _OUR_PRICE.search(sentence)
            if price and "____" not in sentence:
                _keep(found, seen, tf.Mention("our_price", "ours", "", price.group(1).replace("，", ",").strip(), sentence.strip()[:160], line_no, origin=title))
            quality = _QUALITY_WORD.search(sentence)
            if quality and "____" not in sentence:
                _keep(found, seen, tf.Mention("quality", "ours", "", quality.group(1), sentence.strip()[:160], line_no, origin=title))
            clauses = [c.strip() for c in re.split(r"，|,(?!\d)", sentence)]   # "¥200,000.00": that comma ends no clause
            for index, clause in enumerate(clauses):
                if not clause or "____" in clause:
                    continue
                following = clauses[index + 1] if index + 1 < len(clauses) else ""
                for start, end, topic in tf._topic_hits(clause):
                    if topic not in _OUR_TOPICS or topic in ("our_price", "quality"):
                        continue
                    rest = clause[end:]
                    if topic in ("pm", "tech_lead"):
                        value = tf._person_adjacent(rest)
                    elif topic == "duration" and _STAGE.search(clause):
                        continue
                    else:
                        kind = tf._TOPIC[topic].kind
                        match = tf._KIND_RE[kind].search(rest) if kind in tf._KIND_RE else None
                        value = match.group(0).strip() if (match and match.start() <= 12) else ""
                        if not value and kind in tf._KIND_RE and _CONTINUES.match(following):
                            # "提交投标保证金一份，金额为人民币80万元": the clause right after says how much
                            after = tf._KIND_RE[kind].search(following)
                            value = after.group(0).strip() if (after and after.start() <= 12) else ""
                        if not value and kind == "text" and topic == "quality":
                            grade = re.search(r"合格|优良|优质", rest[:16])
                            value = grade.group(0) if grade else ""
                        if not value and kind == "code":
                            code = tf._DOC_CODE.search(rest[:40])
                            value = code.group(0) if code else ""
                    if value:
                        _keep(found, seen, tf.Mention(topic, "ours", "", value, sentence.strip()[:160], line_no, origin=title))
    return found


def _keep(found: list, seen: set, mention) -> None:
    key = (mention.topic, _flat(mention.value), mention.origin)
    if key not in seen:
        seen.add(key)
        found.append(mention)


def consistency(mentions: Sequence) -> List[Dict[str, object]]:
    """Fields our own files do not agree on: [{topic, label, values: [(value, file)], same}]. Only fields that
    at least two files speak of, or one file speaks of twice with different values."""
    from packing_assistant.tools import tender_facts as tf

    by_topic: Dict[str, List] = {}
    for m in mentions:
        by_topic.setdefault(m.topic, []).append(m)
    rows: List[Dict[str, object]] = []
    for topic, items in by_topic.items():
        values = list(dict.fromkeys((_flat(m.value), m.value, m.origin) for m in items))
        distinct = {_amount(v[1]) or v[0] for v in values}   # 3,300.00万元 and 33,000,000.00元 are one amount
        if len(values) < 2:
            continue
        rows.append({"topic": topic, "label": tf._TOPIC[topic].label, "values": [(v[1], v[2]) for v in values], "same": len(distinct) == 1})
    return rows


_AMOUNT = re.compile(r"[¥￥]?\s*(\d[\d,]*(?:\.\d+)?)\s*(亿元|万元|元|日历天|天|日|个月|月|年)?")
_SCALE = {"亿元": ("元", 100_000_000), "万元": ("元", 10_000), "元": ("元", 1), "日历天": ("天", 1), "天": ("天", 1), "日": ("天", 1),
          "个月": ("月", 1), "月": ("月", 1), "年": ("年", 1)}


def _amount(value: str) -> str:
    """"3,300.00万元" -> "元:33000000"; "" when the value is not a number with a unit. For telling whether two
    writings are the same quantity - never for showing."""
    found = _AMOUNT.fullmatch((value or "").strip())
    if not found or not found.group(2):
        return ""
    unit, scale = _SCALE[found.group(2)]
    number = float(found.group(1).replace(",", "")) * scale
    return f"{unit}:{number:.4f}".rstrip("0").rstrip(".")


def found_in(texts: Sequence[Tuple[str, str]], words: Sequence[str]) -> List[str]:
    """The titles of the files in which any of ``words`` occurs, letter for letter after folding whitespace."""
    return [title for title, text in texts if any(_flat(w) and _flat(w) in _flat(text) for w in words)]


def summary(doc: Document) -> Dict[str, int]:
    return {"chars": doc.chars, "lines": doc.lines, "ocr": doc.ocr, "pages": max((p.page for p in doc.pieces), default=0),
            "rejection_candidates": len(rejection_candidates(doc)),
            "chapters": len({p.chapter for p in doc.pieces if p.chapter}),
            "front_rows": len(front_rows(doc)), "rejections": len(rejections(doc)), "obligations": len(obligations(doc)),
            "scores": len(scores(doc)), "specials": len(specials(doc)), "forms": len(forms(doc))}
