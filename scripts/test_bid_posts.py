#!/usr/bin/env python3
"""bid-parse / bid-tech / bid-compliance end to end: what lands in which cell of the deliverable.

Every assertion reads a table cell by its row and its column. A draft that pastes the request under
"用户原文" and keeps the frame - which passed the older acceptance scripts - has no such cell and
fails here (see FakeDraft). The cases are the ones that were reproduced failing before the fact layer:
the bidder's number read as the tender's when it came first, the second clause on a line dropped, a
补遗 ignored, one lot's value in another's row, a stale handoff answering a new tender, the user's
own instruction filed as a P0 requirement.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
for _key in [k for k in os.environ if k.endswith("_API_KEY")]:
    os.environ.pop(_key)

from packing_assistant import expert_turn  # noqa: E402
from packing_assistant.office_job import tables_from_md  # noqa: E402
from packing_assistant.runtime import agent_loop  # noqa: E402

FORBIDDEN = ("可以投标", "不会废标", "资格审查通过", "符合投标资格", "建议投标", "确保中标", "可得满分", "可以开工", "废标成立")
TOOLS = {"bid-parse": "bid-parse__extract", "bid-tech": "bid-tech__expand", "bid-compliance": "bid-compliance__gaps"}


def tables(md: str) -> List[List[Dict[str, str]]]:
    """Every Markdown table of the draft as a list of {column: cell} rows."""
    found: List[List[Dict[str, str]]] = []
    header: Optional[List[str]] = None
    for raw in md.splitlines():
        line = raw.strip()
        if not (line.startswith("|") and line.endswith("|")):
            header = None
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
            continue
        if header is None:
            header = cells
            found.append([])
            continue
        found[-1].append(dict(zip(header, cells)))
    return found


def rows(md: str, first: str) -> List[Dict[str, str]]:
    """Rows whose first cell is exactly ``first``."""
    return [row for table in tables(md) for row in table if next(iter(row.values()), "") == first]


def cell(md: str, first: str, column: str) -> Optional[str]:
    hit = rows(md, first)
    return hit[0].get(column) if hit else None


class Posts(unittest.TestCase):
    def setUp(self) -> None:
        (ROOT / "output").mkdir(exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="bid-posts-", dir=ROOT / "output"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.drafts: List[str] = []
        self.addCleanup(self._no_verdicts)

    def _no_verdicts(self) -> None:
        for draft in self.drafts:
            for word in FORBIDDEN:
                self.assertNotIn(word, draft)

    def run_post(self, post: str, text: str, session: str = "s1") -> str:
        with patch.object(expert_turn, "_OUT", self.tmp), patch.object(agent_loop, "_OUT", self.tmp):
            result = expert_turn.run_named_exclusive(TOOLS[post], {"text": text, "confirm_ok": True, "session_id": session})
        self.assertTrue(result.get("wrote"), result)
        self.assertIs(result.get("submit_blocked"), True)
        path = next(f["path"] for f in result["files"] if f["name"].endswith(".md"))
        draft = Path(path).read_text(encoding="utf-8")
        self.drafts.append(draft)
        return draft


class BidParse(Posts):
    def test_the_bidders_own_number_is_never_the_requirement(self) -> None:
        md = self.run_post("bid-parse", "帮我理一下，我们投标函草稿里工期先写了999日历天，经营部说不对，"
                                         "招标文件BJSZ-2026-117要求的工期是60日历天、投标有效期90天。")
        self.assertEqual(cell(md, "工期", "要求原文"), "60日历天")
        self.assertEqual(cell(md, "投标有效期", "要求原文"), "90天")
        self.assertEqual(cell(md, "招标编号", "要求原文"), "BJSZ-2026-117")
        required = [row.get("要求原文", "") for table in tables(md) for row in table]
        self.assertFalse([c for c in required if "999" in c], "999 is ours; it may be listed, never as a requirement")
        self.assertIn("999日历天", md, "and it is not dropped either")

    def test_each_lot_keeps_its_own_values(self) -> None:
        md = self.run_post("bid-parse", "城南片区污水管网完善工程分两个标段别串了。一标段顶管加检查井，工期180日历天，限价2860万，保证金45万，"
                                         "招标点名要编深基坑专项方案；二标段是泵站土建，工期270天，限价还没公布，保证金55万，专项没提。"
                                         "两个标段投标截止都是2026-10-22。")
        self.assertEqual(cell(md, "工期（一标段）", "要求原文"), "180日历天")
        self.assertEqual(cell(md, "工期（二标段）", "要求原文"), "270天")
        self.assertEqual(cell(md, "投标保证金（一标段）", "要求原文"), "45万")
        self.assertEqual(cell(md, "投标保证金（二标段）", "要求原文"), "55万")
        self.assertEqual(cell(md, "最高限价（二标段）", "要求原文"), "招标未写", "限价还没公布 is the tender not having said")
        self.assertEqual(cell(md, "点名专项（二标段）", "要求原文"), "招标未写")
        self.assertEqual(cell(md, "投标截止", "要求原文"), "2026-10-22", "what holds for both lots is not tied to one")

    def test_an_addendum_changes_what_it_names_and_nothing_else(self) -> None:
        md = self.run_post("bid-parse", "石桥镇中心小学教学综合楼新建工程，招标正文写计划工期420日历天、投标保证金50万元；"
                                         "昨天发的补遗1号只把计划工期改成了450日历天，保证金和资质没提。")
        duration = rows(md, "工期")
        self.assertEqual([r["要求原文"] for r in duration], ["420日历天", "450日历天"])
        self.assertIn("补遗1号", duration[1]["来源页段"])
        self.assertEqual([r["要求原文"] for r in rows(md, "投标保证金")], ["50万元"], "the addendum did not touch it")
        self.assertIn("450 日历天", md, "the tender's duration is the amended one")

    def test_two_scoring_clauses_on_one_line_are_two_rows(self) -> None:
        md = self.run_post("bid-parse", "评标办法摘录如下，请解析：\n施工组织设计评分35分。进度计划与保障措施评分12.5分。\n工期：365日历天。")
        scored = [row["要求原文"] for table in tables(md) for row in table if str(next(iter(row.values()))).startswith("评分点")]
        self.assertEqual(scored, ["施工组织设计 35分", "进度计划与保障措施 12.5分"])

    def test_a_field_asked_for_and_absent_says_so(self) -> None:
        md = self.run_post("bid-parse", "帮我解析下招标文件，目前只知道工期240日历天，其他还没看到，先出个表我好去查。")
        self.assertEqual(cell(md, "工期", "要求原文"), "240日历天")
        self.assertEqual(cell(md, "最高限价", "要求原文"), "未在原文检出")
        self.assertEqual(cell(md, "最高限价", "是否检出"), "未检出")
        self.assertTrue(cell(md, "最高限价", "澄清建议"))

    def test_the_users_instruction_is_not_a_clause_of_the_tender(self) -> None:
        md = self.run_post("bid-parse", "帮我看看这个标会不会废标，招标文件要求投标保证金80万元、工期365日历天，其他的我还没来得及看。")
        self.assertNotIn("会不会废标", "".join(row.get("要求原文", "") for table in tables(md) for row in table))

    def test_a_pasted_document_is_parsed_clause_by_clause(self) -> None:
        md = self.run_post("bid-parse", "一、投标人须具备建筑工程施工总承包二级及以上资质。\n二、★投标人须提供近三年经审计的财务报表。\n"
                                         "三、投标人已经取得安全生产许可证的，须提供复印件并加盖公章。\n四、工期：365日历天。\n"
                                         "五、投标保证金人民币80万元，未按时到账的按否决投标处理。")
        self.assertEqual(cell(md, "资质", "要求原文"), "建筑工程施工总承包二级及以上资质")
        quoted = "".join(row.get("要求原文", "") for table in tables(md) for row in table)
        self.assertIn("财务报表", quoted)
        self.assertIn("已经取得安全生产许可证", quoted, "已经 in a document is not our side speaking")


class BidTech(Posts):
    def test_no_track_record_given_means_a_placeholder_not_a_project(self) -> None:
        md = self.run_post("bid-tech", "帮我按评分点出城东安置房二期的技术标目录，招标工期540日历天，评分表里类似工程业绩占10分、"
                                        "施工组织设计占35分，拟派项目经理汪建国，公司近三年的业绩合同经营部还没给我，业绩那块先留着待填。")
        self.assertIn("[A001] 待填", cell(md, "已有证据", "内容") or "")
        self.assertEqual(cell(md, "项目经理·拟派", "姓名或要求"), "汪建国")
        self.assertEqual(cell(md, "招标工期", "内容"), "540日历天")

    def test_every_scoring_point_has_a_chapter_in_both_lots(self) -> None:
        md = self.run_post("bid-tech", "两个标段一起投，技术标目录先各出一版。一标段是1#2#住宅楼，面积大概27300平，工期要求420天，项目经理定了邓永强；"
                                        "二标段是地下车库加幼儿园，工期465天，招标点名要编车库顶板高支模专项，板厚350，面积和项目经理都还没给我。"
                                        "评分表两个标段一样，施工方案占18.5分，进度计划占12.5分。")
        mapping = [row for table in tables(md) for row in table if "拟写章节" in row]
        self.assertEqual([r["评分点原文"] for r in mapping], ["施工方案 18.5分", "进度计划 12.5分"])
        self.assertTrue(all(re.match(r"第\d+章", r["拟写章节"]) for r in mapping))
        self.assertEqual(cell(md, "建筑面积（一标段）", "内容"), "27300平")
        self.assertIn("待填", cell(md, "建筑面积（二标段）", "内容") or "")
        self.assertEqual(cell(md, "招标工期（二标段）", "内容"), "465天")
        self.assertEqual(cell(md, "项目经理·拟派（一标段）", "姓名或要求"), "邓永强")
        self.assertIn("待填", cell(md, "项目经理·拟派（二标段）", "姓名或要求") or "")
        self.assertEqual(cell(md, "车库顶板高支模专项（二标段）", "参数原文"), "板厚350")

    def test_a_new_tender_in_the_same_session_is_not_answered_from_the_old_one(self) -> None:
        self.run_post("bid-parse", "滨江路雨污分流改造二标段招标文件：施工组织设计评分35分，工期240日历天，招标点名要编沟槽支护专项方案。", session="stale")
        md = self.run_post("bid-tech", "换了一份招标文件：城北中学迁建工程，评分表里质量管理体系占20分、绿色施工占8分，工期300日历天，出技术标目录。", session="stale")
        mapping = [row["评分点原文"] for table in tables(md) for row in table if "拟写章节" in row]
        self.assertEqual(mapping, ["质量管理体系 20分", "绿色施工 8分"])
        self.assertNotIn("沟槽支护", md)
        self.assertNotIn("240", md)

    def test_a_request_without_content_still_uses_the_sessions_parse(self) -> None:
        self.run_post("bid-parse", "招标文件摘录：\n技术标评分：施工组织设计 25 分、项目管理机构 10 分。\n★深基坑专项方案须编制。\n工期：365日历天。", session="carry")
        md = self.run_post("bid-tech", "出一份技术标目录", session="carry")
        mapping = [row["评分点原文"] for table in tables(md) for row in table if "拟写章节" in row]
        self.assertEqual(mapping, ["施工组织设计 25分", "项目管理机构 10分"])

    def test_no_scoring_points_no_borrowed_outline(self) -> None:
        md = self.run_post("bid-tech", "出一份技术标目录，项目是南山路小学，工期300日历天，评分表我还没拿到。", session="empty")
        self.assertIn("禁止套上个中标项目目录", md)
        self.assertEqual([row for table in tables(md) for row in table if "拟写章节" in row], [])


class BidCompliance(Posts):
    LOTS = ("安置房项目我们一标段三标段都投，帮我对下有没有废标点。一标保证金要45万，财务只转了38.5万说明天补，工期要求300天投标函照着写的。"
            "三标保证金68万保函已经开好了，但工期要求270天，我们施组里排的是285天。项目经理一标报的刘志强，三标还没定人。"
            "三标的缺口让{a}跟，一标的缺口让{b}跟。")

    def test_a_number_on_both_sides_is_compared_and_worded_as_a_question(self) -> None:
        md = self.run_post("bid-compliance", "帮我查下响应缺口，招标文件要求工期60日历天、投标有效期90天、投标保证金80万，"
                                              "我们投标函草稿工期写成了999日历天，投标有效期写了90天，保函还没开。")
        row = rows(md, "工期")[0]
        self.assertEqual(row["招标要求"], "60日历天")
        self.assertIn("999日历天", row["响应原文或证据"])
        self.assertEqual(row["三态"], "未响应·数值不符")
        self.assertIn("待人工核验", row["缺口"])
        self.assertEqual(rows(md, "投标有效期")[0]["三态"], "已响应·待核验", "90 against 90 is no conflict - and still not verified")
        bond = rows(md, "投标保证金")[0]
        self.assertEqual((bond["招标要求"], bond["三态"]), ("80万", "未响应"))
        self.assertIn("保函还没开", bond["响应原文或证据"])

    def test_saying_it_is_attached_is_not_evidence(self) -> None:
        md = self.run_post("bid-compliance", "招标正文：★投标人须提供营业执照复印件并加盖公章。工期60日历天。\n投标响应：已附营业执照复印件。")
        licence = [row for table in tables(md) for row in table
                   if "营业执照" in row.get("招标要求", "") and str(next(iter(row.values()))).startswith("★")]
        self.assertEqual(len(licence), 1, "the ★ clause is one row (its 加盖公章 half is a row of 形式签章 too)")
        self.assertEqual(licence[0]["三态"], "已响应·待核验")
        self.assertIn("不代表已实质响应", licence[0]["缺口"])
        self.assertIn("submit_blocked=true", md)

    def test_lots_owners_and_swapping_the_owners(self) -> None:
        md = self.run_post("bid-compliance", self.LOTS.format(a="赵倩", b="孙伟"), session="own1")
        self.assertEqual(rows(md, "工期（三标段）")[0]["责任人"], "赵倩")
        self.assertEqual(rows(md, "工期（一标段）")[0]["责任人"], "孙伟")
        self.assertEqual(rows(md, "投标保证金（一标段）")[0]["响应原文或证据"].split("（")[0], "38.5万")
        self.assertEqual(rows(md, "工期（三标段）")[0]["三态"], "未响应·数值不符")
        self.assertEqual(rows(md, "工期（一标段）")[0]["三态"], "已响应·待核验")
        swapped = self.run_post("bid-compliance", self.LOTS.format(a="孙伟", b="赵倩"), session="own2")
        self.assertEqual(rows(swapped, "工期（三标段）")[0]["责任人"], "孙伟", "the owner follows the lot, not the position")
        self.assertEqual(rows(swapped, "工期（一标段）")[0]["责任人"], "赵倩")

    def test_a_turn_that_only_adds_our_side_is_compared_with_the_sessions_tender(self) -> None:
        self.run_post("bid-parse", "招标文件要求：工期365日历天，投标保证金85万元，投标有效期120天。", session="two")
        md = self.run_post("bid-compliance", "补充一下我们这边的情况：我们投标函草稿工期写成了380日历天，保函还没开，缺口责任人先挂经营部李敏。", session="two")
        row = rows(md, "工期")[0]
        self.assertEqual((row["招标要求"], row["三态"], row["责任人"]), ("365日历天", "未响应·数值不符", "李敏"))
        self.assertIn("380日历天", row["响应原文或证据"])
        self.assertEqual(rows(md, "投标有效期")[0]["三态"], "未响应", "asked for, and we said nothing about it")

    def test_only_our_side_and_no_tender_text(self) -> None:
        md = self.run_post("bid-compliance", "我们这边拟派项目经理周建国，保函已经开好了，工期我们排了380日历天，招标文件我还没拿到，先出个对照。", session="ours")
        self.assertEqual(rows(md, "工期")[0]["三态"], "招标未提供正文")
        self.assertEqual(rows(md, "项目经理")[0]["响应原文或证据"].split("（")[0], "周建国")

    def test_the_users_instruction_is_not_an_unresolved_p0(self) -> None:
        md = self.run_post("bid-compliance", self.LOTS.format(a="赵倩", b="孙伟"), session="p0")
        self.assertNotIn("帮我对下有没有废标点", "".join(c for table in tables(md) for row in table for c in row.values()))
        self.assertNotIn("- 帮我对下", md)


class EnglishAndStaff(Posts):
    def test_an_english_tender_lands_in_the_same_rows(self) -> None:
        md = self.run_post("bid-parse", "Tender for Jurong East MRT Station Upgrading. Employer: Land Transport Authority. Tender No. LTA/2026/C123.\n"
                                         "Contract Period: 24 months. The tender validity period shall be 120 days. Tender Deposit: S$500,000.\n"
                                         "Tender closing date: 30 October 2026, 4.00 pm. Tenderer must be registered with BCA under workhead CW01 grade A1.")
        # English requests now get English presentation, with identical source values.
        table_rows = [row for _, table in tables_from_md(md) for row in table[1:]]
        values = {row[0]: row[1] for row in table_rows if len(row) == 5}
        self.assertEqual(values["Project"], "Jurong East MRT Station Upgrading")
        self.assertEqual(values["Employer"], "Land Transport Authority")
        self.assertEqual(values["Tender reference"], "LTA/2026/C123")
        self.assertEqual(values["Jurisdiction"], "SG")
        self.assertEqual(values["Contract duration"], "24 months")
        self.assertEqual(values["Tender validity"], "120 days")
        self.assertEqual(values["Tender security"], "S$500,000")
        self.assertEqual(values["Submission deadline"], "30 October 2026, 4.00 pm")
        self.assertEqual(values["Registration / workhead"], "CW01 grade A1")
        self.assertIn("- workhead: CW01 · envelope:", md)

    def test_our_english_offer_against_their_english_requirement(self) -> None:
        md = self.run_post("bid-compliance", "The Works shall be completed within 24 months. Tender validity period: 120 days. "
                                              "We shall complete the Works in 26 months and our tender remains valid for 90 days.")
        duration, validity = rows(md, "工期")[0], rows(md, "投标有效期")[0]
        self.assertEqual((duration["招标要求"], duration["三态"]), ("24 months", "未响应·数值不符"))
        self.assertIn("26 months", duration["响应原文或证据"])
        self.assertEqual((validity["招标要求"], validity["三态"]), ("120 days", "未响应·数值不符"))
        self.assertIn("待人工核验", validity["缺口"])

    def test_everybody_named_with_a_post_has_a_row(self) -> None:
        request = ("技术标目录排一下，评分表里施工组织设计占35分，项目经理拟派周建国，专职安全员张伟，质量负责人：李娜，资料员由王芳担任。")
        md = self.run_post("bid-tech", request)
        self.assertEqual(cell(md, "项目经理·拟派", "姓名或要求"), "周建国")
        self.assertEqual(cell(md, "专职安全员·拟派", "姓名或要求"), "张伟")
        self.assertEqual(cell(md, "质量负责人·拟派", "姓名或要求"), "李娜")
        self.assertEqual(cell(md, "资料员·拟派", "姓名或要求"), "王芳")
        gaps = self.run_post("bid-compliance", "招标文件要求工期365日历天，我们拟派项目经理周建国，专职安全员张伟，法定代表人是陈立新。", session="staff")
        self.assertEqual(rows(gaps, "专职安全员")[0]["响应原文或证据"].split("（")[0], "张伟")
        self.assertEqual(rows(gaps, "法定代表人")[0]["三态"], "招标未提供正文")


class ResponseDocuments(unittest.TestCase):
    """The workflow's case: tender and response came as documents with a role, nobody typed our side."""

    def test_a_conflict_stays_with_the_field_it_is_about(self) -> None:
        from packing_assistant.tools.tender_parse import build_response_matrix, parse_tender_text
        from packing_assistant.tools.tender_response_match import compare_responses
        from packing_assistant.tools.tender_tables import compliance_gaps

        tender = "工期60日历天，投标有效期90天。\n项目经理须具备一级注册建造师资格。"
        response = "供应商自述工期999日历天。\n投标有效期90天。\n拟派项目经理李明，二级注册建造师。"
        parsed = parse_tender_text(tender, sides="none")
        comparison = compare_responses(parsed["requirements"], [{"source_id": "r1", "role": "response", "text": response, "start": 0}])
        md = compliance_gaps(parsed["handoff"], build_response_matrix(parsed["requirements"]), comparison=comparison)
        duration, validity, manager = rows(md, "工期")[0], rows(md, "投标有效期")[0], rows(md, "项目经理")[0]
        self.assertEqual(duration["三态"], "未响应·数值不符")
        self.assertIn("999日历天", duration["响应原文或证据"])
        self.assertEqual(validity["三态"], "已响应·待核验", "the 999 against 60 found in the same sentence is not about 投标有效期")
        self.assertNotIn("999", validity["响应原文或证据"] + validity["缺口"])
        self.assertIn("等级字样不同（招标：一级；响应：二级），待人工核验", manager["缺口"])
        self.assertEqual(manager["三态"], "已响应·待核验", "said as a difference of wording, not as a verdict")
        self.assertNotIn("未提供投标响应资料", md)

    def test_no_response_documents_at_all(self) -> None:
        from packing_assistant.tools.tender_parse import build_response_matrix, parse_tender_text
        from packing_assistant.tools.tender_tables import compliance_gaps

        parsed = parse_tender_text("工期60日历天。\n投标保证金人民币80万元。", sides="none")
        md = compliance_gaps(parsed["handoff"], build_response_matrix(parsed["requirements"]), comparison=[])
        self.assertIn("用户未提供投标响应资料，不能认定已响应", md)
        self.assertEqual({rows(md, "工期")[0]["三态"], rows(md, "投标保证金")[0]["三态"]}, {"未响应"})


class Exports(Posts):
    """The Word and Excel files a user actually opens say what the Markdown says, cell for cell."""

    REQUEST = ("招标文件要求工期365日历天、投标保证金85万，评分表里施工组织设计占35分，我们投标函草稿工期写成了380日历天，"
               "保函还没开，项目经理拟派周建国，缺口责任人李敏。")

    def deliver(self, post: str) -> Dict[str, Path]:
        with patch.object(expert_turn, "_OUT", self.tmp), patch.object(agent_loop, "_OUT", self.tmp):
            result = expert_turn.run_named_exclusive(TOOLS[post], {"text": self.REQUEST, "confirm_ok": True, "session_id": "exp-" + post})
        return {Path(f["name"]).suffix: Path(f["path"]) for f in result["files"]}

    def test_every_table_row_is_in_the_workbook_cell_for_cell(self) -> None:
        import openpyxl

        for post in TOOLS:
            with self.subTest(post=post):
                files = self.deliver(post)
                self.assertIn(".xlsx", files, "a draft with tables comes with a workbook")
                md = files[".md"].read_text(encoding="utf-8")
                self.drafts.append(md)
                sheets = [[[("" if c is None else str(c)) for c in row] for row in ws.iter_rows(values_only=True)]
                          for ws in openpyxl.load_workbook(files[".xlsx"]).worksheets]
                exported = {tuple(c.strip() for c in row) for sheet in sheets for row in sheet}
                wanted = [tuple(row.values()) for table in tables(md) for row in table]
                self.assertTrue(wanted)
                for row in wanted:
                    self.assertIn(tuple(c.replace("&#124;", "|") for c in row), exported)

    def test_the_word_file_opens_and_holds_the_same_cells(self) -> None:
        import zipfile
        from xml.etree import ElementTree

        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        for post in TOOLS:
            with self.subTest(post=post):
                files = self.deliver(post)
                with zipfile.ZipFile(files[".docx"]) as archive:
                    root = ElementTree.fromstring(archive.read("word/document.xml"))
                cells = {"".join(t.text or "" for t in tc.iterfind(".//w:t", ns)).strip() for tc in root.iterfind(".//w:tc", ns)}
                md = files[".md"].read_text(encoding="utf-8")
                for table in tables(md):
                    for row in table:
                        for value in row.values():
                            self.assertIn(value.replace("&#124;", "|"), cells)


class FakeDraft(unittest.TestCase):
    """The draft the older acceptance scripts let through: the frame, the disclaimer, the request pasted."""

    REQUEST = "招标文件要求工期60日历天、投标保证金80万，我们投标函工期写成了999日历天，缺口责任人李敏。"

    def test_a_frame_with_the_request_pasted_has_no_cell_to_read(self) -> None:
        fake = "\n".join(["# 废标检查岗 · 响应缺口对照", "", expert_turn.DISCLAIMER, "", "submit_blocked=true", "",
                          "## 已响应", "- （空）", "## 未响应", "- 交货期/工期", "## 招标未提供", "- （空）", "",
                          "## 用户原文", "", self.REQUEST])
        self.assertIsNone(cell(fake, "工期", "招标要求"))
        self.assertEqual(rows(fake, "投标保证金"), [])
        for needle in ("60日历天", "999日历天", "李敏"):
            self.assertIn(needle, fake, "the substring checks of the old acceptance would all have passed")


class Numbers(Posts):
    def test_no_number_in_a_requirement_or_response_cell_that_the_user_did_not_write(self) -> None:
        request = ("帮我查下响应缺口，招标编号BJSZ-2026-117，招标文件要求投标保证金85万、工期365日历天、投标有效期120天，"
                   "我们投标函草稿工期写成了380日历天，保函还没开，项目经理拟派周建国，缺口责任人先挂经营部李敏。")
        written = set(re.findall(r"\d+(?:\.\d+)?", request))
        for post in TOOLS:
            md = self.run_post(post, request, session="num-" + post)
            for table in tables(md):
                for row in table:
                    for column in ("要求原文", "招标要求", "响应原文或证据", "内容", "参数原文", "评分点原文", "姓名或要求"):
                        for number in re.findall(r"\d+(?:\.\d+)?", re.sub(r"\[A\d+\]|L\d+", "", row.get(column, ""))):
                            self.assertIn(number, written, f"{post}: {number} in {row}")


if __name__ == "__main__":
    unittest.main(verbosity=1)
