#!/usr/bin/env python3
"""tools/pdf_grid.py: the tables of a PDF rebuilt from what its pages draw.

Every page here is made up by hand - pieces of text with a place, segments of drawing - the way pypdf's visitors
hand them over. What they copy are the layouts two real tender documents (a 165-page consultation file and a
751-page road-maintenance tender, both exported from Word) were drawn in; none of their text is in this file.

    python scripts/test_pdf_grid.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packing_assistant.tools import pdf_grid as pg  # noqa: E402
from packing_assistant.tools import pdf_layout  # noqa: E402

XS = (50.0, 110.0, 200.0, 520.0)


def grid_segments(xs: Sequence[float], ys: Sequence[float]) -> List[pg.Segment]:
    """A fully ruled grid: every column edge top to bottom, every row edge left to right."""
    return [(x, ys[-1], x, ys[0]) for x in xs] + [(xs[0], y, xs[-1], y) for y in ys]


def piece(x: float, y: float, text: str, size: float = 12.0) -> pg.Piece:
    return pg.Piece(x, y, size, text)


def page(pieces: Sequence[pg.Piece], segments: Sequence[pg.Segment], height: float = 842.0) -> pg.PageParts:
    return pg.PageParts(list(pieces), list(segments), True, height)


def rows_of(layout: pg.Layout) -> List[List[str]]:
    return [[cell.text for cell in row] for item in layout if isinstance(item, pg.Table) for row in item.rows]


class Cells(unittest.TestCase):
    def test_text_leading_uses_drawn_distance_not_font_size_or_library_matrix(self) -> None:
        class FakePage:
            mediabox = type("Box", (), {"height": 842.0, "width": 595.0})()

            def extract_text(self, visitor_text=None, visitor_operand_before=None):
                identity = [1, 0, 0, 1, 0, 0]
                stale = [1, 0, 0, 1, 50, 730]
                before = lambda op, args=(): visitor_operand_before(op, args, identity, stale)
                before(b"BT")
                before(b"Tm", stale)
                before(b"Tf", ["/F1", 10])
                before(b"TL", [13])
                before(b"Tj")
                visitor_text("First line", identity, stale, None, 10)
                before(b"T*")
                stale[5] = 600  # the library erroneously multiplied 13 by 10
                before(b"Tj")
                visitor_text("Second line", identity, stale, None, 10)
                before(b"q")
                before(b"TL", [21])
                before(b"Q")
                before(b"Tf", ["/F2", 9])  # font changes do not change TL
                before(b"T*")
                before(b"Tj")
                visitor_text("Third line", identity, stale, None, 9)
                before(b"TD", [2, -7])
                before(b"T*")
                before(b"Tj")
                visitor_text("Fourth line", identity, stale, None, 9)

        parts = pg.read_page(FakePage())
        self.assertEqual([(50, 730), (50, 717), (50, 704), (52, 690)], [(p.x, p.y) for p in parts.pieces])
        self.assertEqual(["First line", "Second line", "Third line", "Fourth line"], [p.text for p in parts.pieces])

    def test_cells_centred_in_their_row_go_to_that_row(self) -> None:
        """Word centres the number and the name; the content runs over several lines above and below them. Read
        straight across, that is "1.10.2 答疑提出的" / "截止时间 /" - the grid says which cell each piece is in."""
        ys = (760.0, 700.0, 640.0)
        pieces = [piece(55, 728, "1.10.2"), piece(115, 737, "投标人提出问题"), piece(115, 719, "的截止时间"), piece(205, 728, "/"),
                  piece(55, 668, "3.3.1"), piece(115, 668, "投标有效期"), piece(205, 668, "90 日历天")]
        rows = rows_of(pg.page_layout(page(pieces, grid_segments(XS, ys))))
        self.assertEqual(rows, [["1.10.2", "投标人提出问题的截止时间", "/"], ["3.3.1", "投标有效期", "90 日历天"]])

    def test_a_line_that_fills_the_cell_runs_on_and_a_short_one_ends_a_paragraph(self) -> None:
        ys = (760.0, 640.0)
        full = "地址：临江市滨河新区学府路东侧一百二十八号综合"          # reaches the right edge of a 320 pt cell at 12 pt
        pieces = [piece(55, 700, "1.1.3"), piece(115, 700, "招标代理机构"),
                  piece(205, 740, "名称：中衡咨询有限公司"), piece(205, 722, full), piece(241, 704, "办公楼九层"),
                  piece(205, 686, "联系人：周某")]
        rows = rows_of(pg.page_layout(page(pieces, grid_segments(XS, ys))))
        self.assertEqual(rows[0][2], "名称：中衡咨询有限公司；" + full + "办公楼九层；联系人：周某",
                         "a hanging indent under 地址： is the same paragraph; a label starts a new one")

    def test_first_line_indents_tell_paragraphs_apart_where_the_measure_cannot(self) -> None:
        """A cell with a wide right indent: no line comes near the drawn edge, so the measure says every line is short.
        The cell indents the FIRST line of each paragraph - a line at the margin therefore runs on."""
        xs = (50.0, 110.0, 520.0)
        ys = (760.0, 600.0)
        pieces = [piece(55, 680, "5.1"),
                  piece(137, 740, "增加一项："), piece(137, 722, "招标人在规定的时间和地点对投标文件进行开标，并邀请"),
                  piece(115, 704, "所有投标人的法定代表人或其委托代理人准时参加。")]
        rows = rows_of(pg.page_layout(page([pg.Piece(p.x, p.y, 10.5, p.text) for p in pieces], grid_segments(xs, ys))))
        self.assertIn("并邀请所有投标人", rows[0][1])
        self.assertIn("增加一项：招标人", rows[0][1], "a colon ends the line before; nothing is put between")

    def test_a_cell_spanning_rows_is_repeated_so_every_row_reads_whole(self) -> None:
        xs = (50.0, 110.0, 200.0, 440.0, 520.0)
        ys = (760.0, 720.0, 680.0)
        segments = [(x, 680.0, x, 760.0) for x in xs] + [(50.0, 760.0, 520.0, 760.0), (50.0, 680.0, 520.0, 680.0),
                                                        (200.0, 720.0, 520.0, 720.0)]      # the middle rule stops short of the first two columns
        pieces = [piece(55, 716, "2.2.4（1）"), piece(115, 716, "施工方案"), piece(205, 736, "内容完整"), piece(445, 736, "5分"),
                  piece(205, 696, "措施可行"), piece(445, 696, "8分")]
        rows = rows_of(pg.page_layout(page(pieces, segments)))
        self.assertEqual(rows, [["2.2.4（1）", "施工方案", "内容完整", "5分"], ["2.2.4（1）", "施工方案", "措施可行", "8分"]])

    def test_a_number_cut_over_two_lines_is_one_number(self) -> None:
        ys = (760.0, 700.0)
        pieces = [piece(55, 737, "2.2.4"), piece(55, 719, "（4）"), piece(115, 728, "财务能力"), piece(205, 728, "4分")]
        self.assertEqual(rows_of(pg.page_layout(page(pieces, grid_segments(XS, ys))))[0][0], "2.2.4（4）")


class WhatIsARule(unittest.TestCase):
    def test_a_line_under_words_is_no_border(self) -> None:
        """An underlined place name inside a cell: taken for a border it cut one real row into four."""
        ys = (760.0, 640.0)
        segments = grid_segments(XS, ys) + [(205.0, 698.6, 515.0, 698.6)]        # 1.4 pt under the baseline at 700
        pieces = [piece(55, 700, "5.1"), piece(115, 700, "开标地点"), piece(205, 700, "临江市政务服务中心十一层开标室"),
                  piece(205, 682, "具体开标室以屏幕显示为准")]
        rows = rows_of(pg.page_layout(page(pieces, segments)))
        self.assertEqual(len(rows), 1)
        self.assertIn("开标室", rows[0][2])

    def test_a_filled_area_has_no_edges(self) -> None:
        """White laid behind every line of a notice, header shading: outlines nobody drew. Counted as rules they made a
        "table" of a page of running text."""
        class FakePage:
            mediabox = type("Box", (), {"height": 842.0})()

            def extract_text(self, visitor_text=None, visitor_operand_before=None):
                identity = [1, 0, 0, 1, 0, 0]
                for y in (700.0, 680.0, 660.0):
                    visitor_operand_before(b"re", [50.0, y, 470.0, 18.0], identity, identity)
                    visitor_operand_before(b"f*", [], identity, identity)
                    visitor_text("本标段要求投标人具有独立法人资格\n", identity, [1, 0, 0, 1, 50.0, y + 4], None, 12.0)
                visitor_operand_before(b"re", [50.0, 600.0, 470.0, 0.5], identity, identity)      # a border drawn as a filled sliver
                visitor_operand_before(b"f", [], identity, identity)
                return ""

        parts = pg.read_page(FakePage())
        across, down = pg.rules_of(parts.segments)
        self.assertEqual((len(across), len(down)), (1, 0), "the sliver is a rule; the three shaded areas are not")
        self.assertEqual([item for item in pg.page_layout(parts) if isinstance(item, pg.Table)], [])

    def test_a_clipping_path_is_not_painted(self) -> None:
        class FakePage:
            mediabox = type("Box", (), {"height": 842.0})()

            def extract_text(self, visitor_text=None, visitor_operand_before=None):
                identity = [1, 0, 0, 1, 0, 0]
                visitor_operand_before(b"re", [50.0, 600.0, 470.0, 100.0], identity, identity)
                visitor_operand_before(b"W*", [], identity, identity)
                visitor_operand_before(b"n", [], identity, identity)
                return ""

        self.assertEqual(pg.read_page(FakePage()).segments, [])

    def test_a_frame_of_one_column_is_no_table(self) -> None:
        segments = grid_segments((50.0, 520.0), (760.0, 640.0))
        layout = pg.page_layout(page([piece(60, 700, "注意事项：投标人应当仔细阅读。")], segments))
        self.assertEqual(layout, ["注意事项：投标人应当仔细阅读。"])

    def test_what_is_painted_in_the_papers_colour_rules_nothing(self) -> None:
        """White slivers laid behind the characters of "名  称：" (Word's distributed alignment): taken for rules they cut
        one cell into three - "名地联电 | 系 | 称：某某中心"."""
        class FakePage:
            mediabox = type("Box", (), {"height": 842.0, "width": 595.0})()

            def extract_text(self, visitor_text=None, visitor_operand_before=None):
                identity = [1, 0, 0, 1, 0, 0]
                visitor_operand_before(b"q", [], identity, identity)
                visitor_operand_before(b"rg", [1, 1, 1], identity, identity)
                for x in (272.0, 277.0, 282.0):
                    visitor_operand_before(b"re", [x, 600.0, 2.0, 80.0], identity, identity)
                    visitor_operand_before(b"f*", [], identity, identity)
                visitor_operand_before(b"RG", [1, 1, 1], identity, identity)
                visitor_operand_before(b"m", [300.0, 600.0], identity, identity)
                visitor_operand_before(b"l", [300.0, 680.0], identity, identity)
                visitor_operand_before(b"S", [], identity, identity)
                visitor_operand_before(b"Q", [], identity, identity)
                visitor_operand_before(b"m", [50.0, 590.0], identity, identity)      # black again after Q: a real rule
                visitor_operand_before(b"l", [520.0, 590.0], identity, identity)
                visitor_operand_before(b"S", [], identity, identity)
                return ""

        across, down = pg.rules_of(pg.read_page(FakePage()).segments)
        self.assertEqual((len(across), len(down)), (1, 0))


class Nested(unittest.TestCase):
    def test_a_table_inside_a_cell_reads_row_by_row_with_the_outer_rows_number_and_name(self) -> None:
        """表中表: the content cell of row 3.4.1 holds a grid of its own (one line per lot). Every inner row comes out as a
        row that still says which clause it belongs to; no two cells' words are run together."""
        outer = [(50.0, 600.0, 50.0, 760.0), (110.0, 600.0, 110.0, 760.0), (200.0, 600.0, 200.0, 760.0), (520.0, 600.0, 520.0, 760.0),
                 (50.0, 760.0, 520.0, 760.0), (50.0, 720.0, 520.0, 720.0), (50.0, 600.0, 520.0, 600.0)]
        inner = [(360.0, 600.0, 360.0, 720.0), (200.0, 680.0, 520.0, 680.0), (200.0, 640.0, 520.0, 640.0)]
        pieces = [piece(55, 736, "条款号"), piece(115, 736, "条款名称"), piece(205, 736, "编列内容"),
                  piece(55, 656, "3.4.1"), piece(115, 656, "投标保证金"),
                  piece(205, 696, "一标段"), piece(365, 696, "20万元"), piece(205, 656, "二标段"), piece(365, 656, "15万元"),
                  piece(205, 616, "三标段"), piece(365, 616, "18万元")]
        rows = rows_of(pg.page_layout(page(pieces, outer + inner)))
        self.assertEqual(rows[1:], [["3.4.1", "投标保证金", "一标段", "20万元"], ["3.4.1", "投标保证金", "二标段", "15万元"],
                                    ["3.4.1", "投标保证金", "三标段", "18万元"]])


class TwoColumns(unittest.TestCase):
    LEFT = ["投标人应当按照招标文件的要求编制投标", "文件，并对招标文件提出的实质性要求和", "条件作出响应。投标文件应当包括下列内"]
    RIGHT = ["评标委员会应当按照招标文件确定的评标", "标准和方法，对投标文件进行评审和比较", "；设有标底的，应当参考标底。评标委员"]

    def prose(self) -> List[pg.Piece]:
        pieces = []
        for n in range(30):
            y = 790.0 - 18 * n
            pieces += [piece(60, y, self.LEFT[n % 3]), piece(310, y, self.RIGHT[n % 3])]      # written row by row: left, right, left, …
        return pieces

    def test_prose_in_two_columns_is_read_column_by_column(self) -> None:
        text = "".join(pg.page_layout(page(self.prose(), [])))
        lines = text.splitlines()
        self.assertEqual(lines[:3], self.LEFT)
        self.assertEqual(lines[30:33], self.RIGHT, "the right column follows the WHOLE left one")
        self.assertFalse(any(left in line and right in line for line in lines for left in self.LEFT for right in self.RIGHT))

    def test_labels_with_their_values_beside_them_are_not_two_columns(self) -> None:
        pieces = []
        for n, (label, value) in enumerate([("项目名称", "某某河道整治工程"), ("建设地点", "某某市某某区"), ("计划工期", "180日历天")] * 6):
            pieces += [piece(60, 760.0 - 18 * n, label), piece(310, 760.0 - 18 * n, value)]
        parts = page(pieces, [])
        self.assertIsNone(pg.two_columns(parts.pieces, parts.width), "read column-wise, every label would lose its value")


class Furniture(unittest.TestCase):
    def pages(self) -> List[pg.PageParts]:
        out = []
        for number in range(1, 13):
            out.append(page([piece(60, 700, f"第{number}条 " + "甲乙丙丁戊己庚辛壬癸子丑"[number - 1] * 8 + "，这一页的正文。\n"),
                             piece(60, 640 - 7 * number, "投标人：（盖单位章）法定代表人：（签字）\n"),   # under every form, never at one place
                             piece(128, 400, "样本文件仅供浏览不得用于编制投标文件\n", 16.0),              # a watermark across the body
                             piece(300 - (3 if number > 9 else 0), 32, str(number)),                    # the page number, centred
                             piece(205, 70, "15"), piece(230, 70, "分钟内到达")], []))                   # a figure in a sentence near the foot
        return out

    def test_watermark_and_page_number_go_a_figure_in_a_sentence_stays(self) -> None:
        pages = self.pages()
        drops = pg.furniture(pages)
        for parts, drop in zip(pages, drops):
            kept = "".join(p.text for index, p in enumerate(parts.pieces) if index not in drop)
            self.assertNotIn("仅供浏览", kept)
            self.assertIn("（盖单位章）", kept, "words that recur but stand elsewhere each time are the document's")
            self.assertIn("15分钟内到达", kept, "a figure that is not alone on its line is no page number")
            self.assertRegex(kept, r"^第\d+条")
            self.assertNotRegex(kept, r"\n\d+$|\n\d+15", "the page number is gone")

    def test_a_lone_number_that_does_not_count_up_stays(self) -> None:
        pages = [page([piece(60, 700, "正文\n"), piece(300, 32, "86")], []) for _ in range(6)]
        self.assertTrue(all(not drop for drop in pg.furniture(pages)), "86 on every page is not a page number")

    def test_a_running_head_that_repeats_a_value_goes_where_it_stands_and_nowhere_else(self) -> None:
        name = "栖霞学院实训楼配电改造工程施工项目"
        pages = []
        for number in range(1, 9):
            pages.append(page([piece(70, 790, "项目名称："), piece(116, 790, name),                    # the head: a label and the name
                               piece(70, 700, f"第{number}条 这一页的正文。\n"),
                               piece(120, 640, name) if number == 3 else piece(120, 640, "正文\n"),    # the front table's 项目名称 row
                               piece(70, 52, "青桐招标有限公司编制投标文件格式"),                           # the foot: who made it ...
                               piece(494, 52, "-"), piece(497, 52, str(number)), piece(502, 52, "/"), piece(504, 52, "39"), piece(513, 52, "-")], []))
        drops = pg.furniture(pages)
        for number, (parts, drop) in enumerate(zip(pages, drops), 1):
            kept = "".join(p.text for index, p in enumerate(parts.pieces) if index not in drop)
            self.assertNotIn("项目名称：", kept, "what shares the head's line is the head")
            self.assertNotIn("青桐", kept)
            self.assertNotRegex(kept, r"[-/]|39", "a page number drawn in five pieces, on the foot's line")
            self.assertEqual(name in kept, number == 3, "the same words in the body are the document's")

    def test_latin_lines_and_pieces_are_joined_with_a_space_chinese_and_numbers_are_not(self) -> None:
        self.assertEqual(pg.between("intends to apply the sum", "of Five Million"), " ")
        self.assertEqual(pg.between("投标人须具备市政公用工程", "施工总承包资质"), "")
        self.assertEqual(pg.between("账号：9410070100320400", "0207189"), "", "a long number broken by the line is one number")
        self.assertEqual(pg.between("an inter-", "national bidder"), "", "a word divided at the line end is one word")
        self.assertEqual(pg.between("采用 BIM", "技术"), "")
        # two text objects side by side in one cell line: no space character stands between them on the page
        lines = pg.cell_lines([piece(60, 700, "contracts similar to the Project"), piece(232, 700, "refer to contracts")])
        self.assertEqual(lines[0][0], "contracts similar to the Project refer to contracts")
        lines = pg.cell_lines([piece(60, 700, "招标编号：LX-"), piece(140, 700, "2031-07")])
        self.assertEqual(lines[0][0], "招标编号：LX-2031-07", "a number is set solid, whatever objects it is drawn in")

    def test_an_english_paragraph_cut_at_every_line_end_is_whole_again(self) -> None:
        from packing_assistant.tools import pdf_layout

        text = ("1. The Harbour Works Authority through the Capital Fund intends to apply the sum of One Million\n"
                "Dollars (USD 1,000,000.00), being the Approved Budget for the Contract (ABC) to payments under the\n"
                "contract for the Rehabilitation of Pier 4.\n"
                "(a) Valid Registration Certificate;\n"
                "(b) Statement of all ongoing contracts.\n")
        rebuilt = [line for line in pdf_layout.rebuild(text).splitlines() if line.strip()]
        self.assertEqual(len(rebuilt), 3, rebuilt)
        self.assertIn("the sum of One Million Dollars (USD 1,000,000.00), being", rebuilt[0])
        self.assertTrue(rebuilt[0].endswith("Rehabilitation of Pier 4."))
        self.assertEqual(rebuilt[1:], ["(a) Valid Registration Certificate;", "(b) Statement of all ongoing contracts."], "a list item begins a paragraph")

    def test_where_a_piece_stands_does_not_depend_on_the_librarys_memo(self) -> None:
        """Up to pypdf 6.18 a piece that begins with an inserted space reports a STALE position to the text visitor (the
        second text object of a line came back at 0, 0) - a cell of a real front table was read as two lines, and a
        165-page tender fell from 22/22 fields to 9/22 on nothing but the library's minor version. read_page takes the
        position from the text-showing operator itself."""
        import io

        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

        writer = PdfWriter()
        made = writer.add_blank_page(width=595, height=842)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        made[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 1 0 0 1 72 700 Tm (Alpha) Tj ET BT /F1 12 Tf 1 0 0 1 300 700 Tm (2031) Tj ET "
                        b"BT /F1 12 Tf 1 0 0 1 72 650 Tm (Omega) Tj ET")
        made[NameObject("/Contents")] = writer._add_object(stream)
        buffer = io.BytesIO()
        writer.write(buffer)
        buffer.seek(0)
        found = {p.text.strip(): (round(p.x), round(p.y)) for p in pg.read_page(PdfReader(buffer).pages[0]).pieces if p.text.strip()}
        self.assertEqual(found, {"Alpha": (72, 700), "2031": (300, 700), "Omega": (72, 650)})

    def test_a_page_number_drawn_piece_by_piece_counts_up(self) -> None:
        pages = [page([piece(60, 700, "正文\n"), piece(280, 40, "-"), piece(286, 40, str(number)), piece(292, 40, "/"), piece(297, 40, "12"), piece(309, 40, "-")], [])
                 for number in range(1, 7)]
        for parts, drop in zip(pages, pg.furniture(pages)):
            self.assertEqual("".join(p.text for index, p in enumerate(parts.pieces) if index not in drop), "正文\n")


class OverThePage(unittest.TestCase):
    def table(self, rows: Sequence[Sequence[Tuple[float, float, str]]]) -> pg.Table:
        return pg.Table([[pg.Cell(x0, x1, text) for x0, x1, text in row] for row in rows])

    def test_header_repeated_and_row_cut_in_two(self) -> None:
        head = [(50, 110, "条款号"), (110, 200, "条款名称"), (200, 520, "编列内容")]
        first = self.table([head, [(50, 110, "2.2.2"), (110, 200, "递交截止时间"), (200, 520, "供应商代表须同时提交身份证明，听")]])
        second = self.table([head, [(50, 110, ""), (110, 200, ""), (200, 520, "从工作人员引导。")],
                             [(50, 110, "3.3.1"), (110, 200, "有效期"), (200, 520, "90 天")]])
        layouts = [["第二章\n", first], [second, "后面的正文\n"]]
        pg.join_pages(layouts)
        self.assertEqual([c.text for c in first.rows[-1]], ["2.2.2", "递交截止时间", "供应商代表须同时提交身份证明，听从工作人员引导。"])
        self.assertEqual([[c.text for c in row] for row in second.rows], [["3.3.1", "有效期", "90 天"]])

    def test_cells_are_joined_by_where_they_stand_not_by_their_count(self) -> None:
        """The first page's row has six cells, the rest of it on the next page only the three that still hold text."""
        head = [(50, 110, "条款号"), (110, 520, "评分因素与评分标准")]
        first = self.table([head, [(50, 110, "2.2.4（4）"), (110, 200, "养护目标"), (200, 250, "40分"),
                                   (250, 440, "指标满足要求得2.4分。（除"), (440, 520, "2.4-4分")]])
        second = self.table([[(50, 110, ""), (110, 250, ""), (250, 440, "去当年大修路段计算）"), (440, 520, "")]])
        layouts = [[first], [second]]
        pg.join_pages(layouts)
        self.assertEqual(first.rows[-1][3].text, "指标满足要求得2.4分。（除去当年大修路段计算）")
        self.assertEqual(first.rows[-1][1].text, "养护目标", "nothing lands in the name cell")
        self.assertEqual(second.rows, [])

    def test_a_row_of_its_own_under_the_clause_above_reads_with_its_number(self) -> None:
        head = [(50, 110, "条款号"), (110, 200, "评分因素"), (200, 440, "评分标准"), (440, 520, "分值")]
        first = self.table([head, [(50, 110, "2.2.4（1）"), (110, 200, "施工方案"), (200, 440, "方案全面得3分。"), (440, 520, "3-5分")]])
        second = self.table([[(50, 110, ""), (110, 200, ""), (200, 440, "保证体系健全得2分。"), (440, 520, "1.2-2分")]])
        pg.join_pages([[first], [second]])
        self.assertEqual([c.text for c in second.rows[0]], ["2.2.4（1）", "施工方案", "保证体系健全得2分。", "1.2-2分"],
                         "two finished cells, each going on: not the rest of one cell - a row under the same clause")


class Rebuild(unittest.TestCase):
    def test_rows_pass_through_and_the_page_marker_stands_between_them(self) -> None:
        text = "\n".join(["〔第1页〕", "第二章 投标人须知", "| 条款号 | 条款名称 | 编列内容 |", "| 1.3.2 | 计划工期 | 300日历天 |", "〔第2页〕",
                          "| 条款号 | 条款名称 | 编列内容 |", "| 3.3.1 | 投标有效期 | 90日历天 |", "1. 总则"])
        lines = pdf_layout.rebuild(text).splitlines()
        at = lines.index("| 1.3.2 | 计划工期 | 300日历天 |")
        self.assertEqual(lines[at:at + 3], ["| 1.3.2 | 计划工期 | 300日历天 |", "〔第2页〕", "| 3.3.1 | 投标有效期 | 90日历天 |"],
                         "no blank line (it would end the table for the reader), no second header")

    def test_the_measure_of_a_full_line_is_the_pages_own(self) -> None:
        """A contents page of dot leaders is twice as wide as any line of running text. One measure for the whole file
        (the 90th percentile) then calls no line full, and no paragraph is joined again."""
        body = ["投标人应当按照招标文件的要求编制投标文件并对招标文件提出的实质性要求作出响应采购人", "不予受理。"] * 30
        contents = [f"第{n}章 某一章的标题" + "." * 90 + str(n) for n in range(1, 30)]
        lines = pdf_layout.rebuild("\n".join(["〔第1页〕"] + contents + ["〔第2页〕"] + body)).splitlines()
        self.assertIn("投标人应当按照招标文件的要求编制投标文件并对招标文件提出的实质性要求作出响应采购人不予受理。", lines)

    def test_a_scan_reading_runs_the_pages_own_number_into_a_cell(self) -> None:
        text = "\n".join(["〔第6页〕", "条款号", "评分因素", "评分项", "分值", "2.2.4(2)", "项目管理机构评分标准（10分）", "其他主要人员第6页", "2分"])
        self.assertIn("| 2.2.4(2) | 项目管理机构评分标准（10分） | 其他主要人员 | 2分 |", pdf_layout.rebuild(text))

    def test_a_bracket_wrapped_inside_its_cell_goes_back_together(self) -> None:
        text = "\n".join(["〔第6页〕", "条款号", "评分因素", "评分项", "分值", "2.2.4(1)", "施工组织设计评分标准（35", "内容完整性", "5分", "分）"])
        self.assertIn("| 2.2.4(1) | 施工组织设计评分标准（35分） | 内容完整性 | 5分 |", pdf_layout.rebuild(text))


if __name__ == "__main__":
    unittest.main(verbosity=1)
