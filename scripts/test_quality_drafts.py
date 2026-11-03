#!/usr/bin/env python3
"""Quality lot ownership, raw evidence, review boundaries and real exports."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from packing_assistant import expert_turn
from packing_assistant.office_job import tables_from_md
from packing_assistant.post_drafts import build_draft as dispatch
from packing_assistant.post_drafts.quality import MISSING, TOOL, _owned, build_draft, parse
from packing_assistant.runtime.scheduler import Scheduler
from packing_assistant.runtime.tool_engine import default_engine


def draft(text):
    return build_draft("quality", TOOL, text)


def table(md, title):
    return next(rows for name, rows in tables_from_md(md) if name.startswith(title))


class QualityDrafts(unittest.TestCase):
    def test_dispatch_is_exclusive_and_keeps_safety_and_unknown_jurisdiction(self):
        md = draft("")
        self.assertEqual(md, dispatch("quality", TOOL, ""))
        self.assertIsNone(build_draft("quality", "material-site__recon", ""))
        self.assertIsNone(build_draft("material-site", TOOL, ""))
        self.assertEqual(10, len([s for s in md.splitlines() if s.startswith("## ")]))
        for marker in ("AI 草稿", "submit_blocked=true", "未检 / 待核", "签认：（空）", "未执行 /（空）"):
            self.assertIn(marker, md)
        for word in ("CN：", "SG：", "CONQUAS", "## 用户原文", "检验批合格", "验收通过", "可以隐蔽"):
            self.assertNotIn(word, md)

    def test_all_dev_facts_have_semantically_correct_fields(self):
        from scripts.eval_post_content import classify, load, spots, split_echo
        for case in load("cases"):
            if case["post"] != "quality":
                continue
            with self.subTest(case=case["id"]):
                md = draft(case["request"])
                body, echo = split_echo(md)
                for fact in case["facts"]:
                    self.assertEqual("placed", classify(fact, body, echo, spots(body)), fact)
                self.assertTrue(all(w not in body for w in case.get("forbidden", [])))

    def test_two_lots_do_not_borrow_measurements_counts_reports_or_responsibility(self):
        data = parse("两个批一块弄：1#楼四层顶板有几条收缩裂缝，最宽的量了0.3毫米，试块报告还没出，谁整改还没定；"
                     "2#楼三层砌体拉结筋漏了12处，砌块用的海螺蒸压加气块，让瓦工班王大勇去补。")
        self.assertEqual(["1#楼四层顶板", "2#楼三层砌体"], [r["location"] for r in data.lots])
        first, second = data.checks
        self.assertEqual("0.3毫米", first["measurement"])
        self.assertNotIn("count", first)
        self.assertEqual("12处", second["count"])
        self.assertNotIn("measurement", second)
        self.assertEqual(0, data.reports[0]["_lot"])
        self.assertNotIn("report_id", data.reports[0])
        self.assertEqual(1, data.actions[0]["_lot"])
        self.assertEqual("瓦工班王大勇", data.actions[0]["owner"])
        self.assertNotIn("deadline", data.actions[0])

    def test_two_observations_in_same_lot_are_not_one_invented_inspection(self):
        data = parse("3#楼二层剪力墙钢筋这个检验批，昨天自检暗柱箍筋间距偏大，还有保护层垫块漏放")
        self.assertEqual(2, len(data.checks))
        self.assertEqual([0, 0], [r["_lot"] for r in data.checks])
        self.assertNotIn("kind", data.checks[0])
        self.assertNotIn("measurement", data.checks[0])
        self.assertIn("暗柱箍筋间距偏大", data.checks[0]["observation"])
        self.assertIn("保护层垫块漏放", data.checks[1]["observation"])

    def test_report_receipt_does_not_become_test_result_or_acceptance(self):
        data = parse("检验批：A\n材料：钢筋\n规格：HRB400\n复试报告GJ-2026-0312已到")
        self.assertEqual("GJ-2026-0312", data.reports[0]["report_id"])
        self.assertEqual("已到", data.reports[0]["report_state"])
        md = draft("检验批：A\n材料：钢筋\n规格：HRB400\n复试报告GJ-2026-0312已到")
        report = next(rows for _, rows in tables_from_md(md) if "用户报告编号" in rows[0])
        self.assertEqual("本轮未核验", report[1][-1])
        self.assertNotIn("检验批合格", md)

    def test_material_name_model_supplier_batch_are_independent_fields(self):
        data = parse("3#楼二层剪力墙钢筋这个检验批，钢筋是宏运钢厂的HRB400，复试报告GJ-A已到")
        self.assertEqual(("钢筋", "HRB400", "宏运钢厂"), tuple(data.materials[0][k] for k in ("material", "model", "supplier")))
        self.assertNotIn("batch", data.materials[0])
        self.assertEqual("HRB400", _owned(data, data.reports)[0]["material_model"])

    def test_new_material_does_not_borrow_report_or_model(self):
        data = parse("检验批：A\n材料：钢筋\n规格：HRB400\n复试报告：GJ-A\n材料：卷材\n材料批次：B2")
        self.assertEqual(2, len(data.materials))
        self.assertNotIn("model", data.materials[1])
        self.assertEqual(0, data.reports[0]["_material"])
        self.assertEqual("钢筋", _owned(data, data.reports)[0]["material_name"])

    def test_new_material_batch_does_not_borrow_model_supplier_or_old_report(self):
        data = parse("检验批：A\n材料：钢筋\n规格：HRB400\n材料批次：B1\n复试报告：GJ-A\n材料批次：B2\n复试报告：GJ-B")
        self.assertEqual(["B1", "B2"], [r["batch"] for r in data.materials])
        self.assertNotIn("model", data.materials[1])
        self.assertEqual([0, 1], [r["_material"] for r in data.reports])

    def test_repeated_report_kind_has_separate_ids_dates_and_sources(self):
        data = parse("检验批：A\n复试报告：GJ-A\n报告日期：2026-10-20\n报告来源：A.pdf\n复试报告：GJ-B")
        self.assertEqual(["GJ-A", "GJ-B"], [r["report_id"] for r in data.reports])
        self.assertNotIn("report_date", data.reports[1])
        self.assertNotIn("report_source", data.reports[1])

    def test_reordered_table_report_fields_and_blank_row_do_not_borrow(self):
        data = parse("| 报告来源 | 报告日期 | 复试报告 | 材料 | 规格 | 检验批 | 检验批编号 |\n| --- | --- | --- | --- | --- | --- | --- |\n"
                     "| A.pdf | 2026-10-20 | GJ-A | 钢筋 | HRB400 | A | LOT-A |\n| | | | | | | |\n\n复试报告：GJ-X")
        self.assertEqual([0, 1, None], [r["_lot"] for r in data.reports])
        self.assertEqual(["GJ-A", MISSING, "GJ-X"], [r["report_id"] for r in data.reports])
        self.assertEqual("A.pdf", data.reports[0]["report_source"])
        self.assertEqual("2026-10-20", data.reports[0]["report_date"])
        self.assertEqual("HRB400", _owned(data, data.reports)[0]["material_model"])
        self.assertEqual(MISSING, _owned(data, data.reports)[1]["material_model"])

    def test_generic_report_source_for_two_types_stays_unassigned(self):
        data = parse("| 检验批 | 进场报告 | 复试报告 | 报告来源 |\n| --- | --- | --- | --- |\n| A | JC-A | FS-A | A.pdf |")
        self.assertEqual(3, len(data.reports))
        self.assertNotIn("report_source", data.reports[0])
        self.assertNotIn("report_source", data.reports[1])
        self.assertNotIn("report_kind", data.reports[2])
        self.assertEqual("A.pdf", data.reports[2]["report_source"])

    def test_generic_source_and_evidence_in_multi_role_row_are_not_assigned_to_last_role(self):
        data = parse("| 来源 | 证据 | 检验批 | 检查项 | 材料 | 复试报告 |\n| --- | --- | --- | --- | --- | --- |\n| A.pdf | E-A | LOT-A | 外观 | 钢筋 | GJ-A |")
        self.assertEqual("A.pdf", data.lots[0]["source"])
        self.assertEqual("E-A", data.lots[0]["evidence"])
        self.assertNotIn("source", data.checks[0])
        self.assertNotIn("source", data.reports[0])
        self.assertNotIn("evidence", data.reports[0])

    def test_single_role_source_is_independent_of_table_column_order(self):
        data = parse("| 来源 | 证据 | 自检记录 | 检验批 |\n| --- | --- | --- | --- |\n| A.pdf | E-A | 缺陷A | LOT-A |")
        self.assertEqual("A.pdf", data.checks[0]["source"])
        self.assertEqual("E-A", data.checks[0]["evidence"])
        self.assertEqual("缺陷A", data.checks[0]["observation"])

    def test_role_named_sources_and_evidence_stay_with_explicit_objects(self):
        data = parse("| 检查来源 | 材料来源 | 整改来源 | 复查来源 | 报告来源 | 检查证据 | 检验批 | 检查项 | 材料 | 复试报告 | 整改责任 | 复查 |\n"
                     "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
                     "| CK.pdf | MAT.pdf | ACT.pdf | REV.pdf | REP.pdf | CK.jpg | LOT-A | 外观 | 钢筋 | GJ-A | 甲班 | 明天复查 |")
        for collection, source in ((data.checks, "CK.pdf"), (data.materials, "MAT.pdf"), (data.actions, "ACT.pdf"), (data.reviews, "REV.pdf")):
            self.assertEqual(source, collection[0]["source"])
        self.assertEqual("REP.pdf", data.reports[0]["report_source"])
        self.assertEqual("CK.jpg", data.checks[0]["evidence"])

    def test_concealment_notice_is_in_body_without_inventing_inspection_item(self):
        md = draft("检验批：A\n隐蔽通知：监理待确认\n影像记录：photoA.jpg")
        rows = table(md, "6 隐蔽")
        self.assertEqual("监理待确认", rows[1][rows[0].index("用户隐蔽通知 / 旁站记录")])
        self.assertEqual("photoA.jpg", rows[1][rows[0].index("检查证据")])
        self.assertEqual(MISSING, rows[1][rows[0].index("检查项")])
        self.assertEqual("未执行 /（空）", rows[1][-1])

    def test_future_hearsay_and_negated_review_cannot_fill_actual_review(self):
        for phrase in ("计划已复查", "预计明天已复查", "据说已复查", "并未已复查", "尚未复查", "还没复查"):
            with self.subTest(phrase=phrase):
                data = parse("检验批：A\n"+phrase)
                self.assertNotIn("review_actual", data.reviews[0])

    def test_explicit_review_plan_never_becomes_actual_even_with_completion_word(self):
        data = parse("检验批：A\n复查计划：已复查待再检查")
        self.assertEqual("已复查待再检查", data.reviews[0]["review"])
        self.assertNotIn("review_actual", data.reviews[0])

    def test_lot_id_given_after_report_is_visible_in_report_owner(self):
        data = parse("检验批：A\n复试报告：GJ-A\n检验批编号：LOT-A")
        self.assertEqual("LOT-A", _owned(data, data.reports)[0]["lot_id"])

    def test_unique_id_reference_returns_to_only_that_lot(self):
        data = parse("检验批：A\n检验批编号：LOT-A\n检验批：B\n检验批编号：LOT-B\n检验批编号：LOT-A\n整改责任：张测试")
        self.assertEqual(2, len(data.lots))
        self.assertEqual(0, data.actions[0]["_lot"])

    def test_duplicate_id_without_name_does_not_choose_last_lot(self):
        data = parse("检验批：A\n检验批编号：DUP\n检验批：B\n检验批编号：DUP\n检验批编号：DUP\n整改责任：张测试")
        self.assertEqual(3, len(data.lots))
        self.assertNotIn("lot", data.lots[2])
        self.assertEqual(2, data.actions[0]["_lot"])

    def test_same_lot_name_alone_does_not_merge_two_physical_lots(self):
        data = parse("检验批：底板\n检验批编号：LOT-A\n问题：裂缝\n检验批：底板\n检验批编号：LOT-B")
        self.assertEqual(2, len(data.lots))
        self.assertEqual(0, data.checks[0]["_lot"])

    def test_location_in_declared_lot_and_relative_self_check_date_are_retained(self):
        data = parse("检验批：5#楼地下室底板防水\n昨天自检西侧卷材起鼓")
        self.assertEqual("5#楼地下室底板防水", data.lots[0]["location"])
        self.assertEqual("昨天", data.checks[0]["checked_date"])
        self.assertNotIn("report_date", data.checks[0])

    def test_late_file_metadata_fills_only_missing_fields_in_its_own_source_epoch(self):
        data = parse("### A.txt\n检验批：A\n检查项：外观\n### B.txt\n检验批：B\n复试报告：GJ-B\n单位工程：乙\n辖区：SG")
        self.assertEqual(MISSING, data.lots[0]["unit"])
        self.assertEqual("UNSPECIFIED", data.lots[0]["jurisdiction"])
        self.assertEqual("乙", data.lots[1]["unit"])
        self.assertEqual("SG", data.lots[1]["jurisdiction"])
        self.assertEqual("乙", _owned(data, data.reports)[0]["owner_unit"])

    def test_missing_or_example_lot_identity_in_table_is_not_borrowed(self):
        data = parse("| 检验批 | 检查项 |\n| --- | --- |\n| 甲批 | 外观 |\n| 例如乙批 | 隐蔽 |")
        self.assertEqual("甲批", data.lots[0]["lot"])
        self.assertEqual(MISSING, data.lots[1]["lot"])
        self.assertEqual(MISSING, _owned(data, data.checks)[1]["lot_name"])

    def test_declared_check_classes_are_not_guessed_or_copied(self):
        data = parse("检验批：A\n主控检查项：搭接\n一般检查项：外观\n隐蔽检查项：预埋\n检查项：裂缝")
        self.assertEqual(["主控", "一般", "隐蔽", MISSING], [r.get("kind", MISSING) for r in data.checks])
        self.assertEqual(["搭接", "外观", "预埋", "裂缝"], [r["item"] for r in data.checks])

    def test_measurement_does_not_create_design_requirement_or_rate(self):
        data = parse("检验批：A\n检查项：裂缝\n实测：0.3mm\n问题数量：12处")
        self.assertEqual(("0.3mm", "12处"), tuple(data.checks[0][k] for k in ("measurement", "count")))
        self.assertNotIn("requirement", data.checks[0])
        self.assertNotIn("sampling", data.checks[0])
        self.assertNotIn("lot_quantity", data.lots[0])
        self.assertNotIn("100%", draft("检验批：A\n检查项：裂缝\n实测：0.3mm"))

    def test_numeric_fields_cannot_come_from_report_ids_dates_or_examples(self):
        for label, key in (("实测", "measurement"), ("问题数量", "count"), ("抽样数量", "sampling"), ("检验批数量", "lot_quantity")):
            for value in ("GJ-12", "HRB400", "2026-10-20", "0.3mm？", "不是0.3mm", "例如12", "如果12", "未定"):
                with self.subTest(label=label, value=value):
                    data = parse("检验批：A\n检查项：检查A\n"+label+"："+value)
                    rows = data.lots if key == "lot_quantity" else data.checks
                    self.assertEqual(MISSING, rows[0].get(key, MISSING))

    def test_dates_are_not_derived_from_codes_or_assumed_year(self):
        for value in ("GJ-20261020", "V2.0", "不是2026-10-20", "2026-10-20？", "未给"):
            data = parse("检验批：A\n整改期限："+value)
            self.assertEqual(MISSING, (data.actions or [{}])[0].get("deadline", MISSING))
        self.assertEqual("9月26日", parse("检验批：A\n整改期限：9月26日").actions[0]["deadline"])

    def test_correction_completion_does_not_create_review_confirmation(self):
        data = parse("检验批：A\n整改责任：钢筋班张测试\n整改期限：9月26日\n整改完成：未完成\n复查：9月27日质检员李测试复查")
        self.assertEqual("未完成", data.actions[0]["completed"])
        self.assertEqual("9月26日", data.actions[0]["deadline"])
        self.assertEqual(("9月27日", "李测试"), tuple(data.reviews[0][k] for k in ("review_date", "reviewer")))
        self.assertNotIn("review_actual", data.reviews[0])
        self.assertEqual("待核", table(draft("检验批：A\n整改完成：已完成\n复查：明天复查"), "9 资料")[1][-2])

    def test_planned_and_reported_actual_review_are_separate(self):
        data = parse("检验批：A\n复查计划：10月20日复查\n实际复查记录：10月22日已复查\n复查状态：用户说处理完成")
        self.assertEqual(1, len(data.reviews))
        self.assertEqual("10月20日复查", data.reviews[0]["review"])
        self.assertEqual("10月22日已复查", data.reviews[0]["review_actual"])
        self.assertEqual("用户说处理完成", data.reviews[0]["review_state"])

    def test_owner_changes_do_not_borrow_deadline_or_completion(self):
        data = parse("检验批：A\n整改责任：甲班\n整改期限：10月20日\n整改完成：已完成\n整改责任：乙班")
        self.assertEqual(2, len(data.actions))
        self.assertNotIn("deadline", data.actions[1])
        self.assertNotIn("completed", data.actions[1])

    def test_report_source_completion_evidence_and_review_evidence_stay_separate(self):
        data = parse("检验批：A\n复试报告：GJ-A\n来源：A.pdf\n整改完成：已完成\n整改完成证据：B.jpg\n来源：B.pdf\n实际复查记录：已复查\n复查证据：C.jpg\n来源：C.pdf")
        self.assertEqual("A.pdf", data.reports[0]["source"])
        self.assertEqual("B.pdf", data.actions[0]["source"])
        self.assertEqual("C.pdf", data.reviews[0]["source"])
        self.assertNotIn("review_evidence", data.actions[0])

    def test_unknown_person_does_not_fill_owner_or_review_signatures(self):
        data = parse("检验批：A\n整改责任：还没定\n复查人：未给\n复查：我再复查")
        self.assertEqual(MISSING, data.actions[0]["owner"])
        self.assertEqual(MISSING, data.reviews[0]["reviewer"])
        signatures = table(draft("检验批：A\n整改责任：张测试\n复查人：李测试"), "10 签字")
        self.assertEqual(["（空）"]*4, signatures[1])

    def test_file_and_unit_project_region_scopes_do_not_lend_prior_lot(self):
        data = parse("### A.txt\n辖区：CN\n单位工程：甲\n检验批：底板\n实测：0.3mm\n"
                     "### A.txt\n单位工程：乙\n检验批：底板\n整改责任：张测试")
        self.assertEqual(["CN", "UNSPECIFIED"], [r["jurisdiction"] for r in data.lots])
        self.assertEqual(["甲", "乙"], [r["unit"] for r in data.lots])
        self.assertEqual(1, data.actions[0]["_lot"])
        self.assertEqual(0, data.checks[0]["_lot"])

    def test_orphan_report_in_next_file_has_no_lot_or_material_from_prior_file(self):
        data = parse("### A.txt\n检验批：A\n材料：钢筋\n规格：HRB400\n### B.txt\n复试报告：GJ-X")
        row = _owned(data, data.reports)[0]
        self.assertEqual(MISSING, row["lot_name"])
        self.assertEqual(MISSING, row["material_name"])
        self.assertEqual(MISSING, row["material_model"])

    def test_examples_are_not_facts_and_actual_reset_is_kept(self):
        data = parse("例如1#楼四层顶板有收缩裂缝，最宽0.3毫米\n实际情况：检验批：A\n检查项：外观\n实测：0.1mm")
        self.assertEqual(1, len(data.lots))
        self.assertEqual("A", data.lots[0]["lot"])
        self.assertEqual("0.1mm", data.checks[0]["measurement"])
        self.assertNotIn("0.3", repr(data))

    def test_english_labels_are_raw_unverified_and_do_not_invent_missing_fields(self):
        data = parse("Project: Synthetic A\nJurisdiction: SG\nLot: L-A\nLot ID: LOT-A\nLocation: Panel A\nMain item: joint\nObservation: seal missing\nMaterial: sealant\nReport ID: R-A\nRectification owner: Team A\nActual review: user says checked")
        self.assertEqual("Synthetic A", data.lots[0]["project"])
        self.assertEqual("SG", data.lots[0]["jurisdiction"])
        self.assertEqual("seal missing", data.checks[0]["observation"])
        self.assertEqual("Team A", data.actions[0]["owner"])
        self.assertNotIn("deadline", data.actions[0])
        self.assertNotIn("measurement", data.checks[0])

    def test_evidence_uses_filename_and_physical_line_not_original_page(self):
        data = parse("### A.txt\n检验批：A\n实测：0.3mm\n### B.txt\n复试报告：GJ-X")
        self.assertEqual(["A.txt · 本轮输入第2行（非原件页码）", "A.txt · 本轮输入第3行（非原件页码）", "B.txt · 本轮输入第5行（非原件页码）"], [r["location"] for r in data.evidence])

    def test_orphan_evidence_keeps_its_own_source_and_late_region_not_previous_lot(self):
        data = parse("### A.txt\n辖区：CN\n检验批：A\n### B.txt\n复试报告：GJ-X\n辖区：SG")
        row = next(r for r in _owned(data, data.evidence) if r["value"] == "GJ-X")
        self.assertEqual("B.txt", row["_origin"])
        self.assertEqual("SG", row["owner_jurisdiction"])
        self.assertEqual(MISSING, row["lot_name"])


class QualityRuntime(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="quality-regression-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for context in (patch.object(expert_turn, "_OUT", self.root),
                        patch("packing_assistant.runtime.memory._OUT", self.root),
                        patch("packing_assistant.runtime.scheduler.get_scheduler", return_value=Scheduler()),
                        patch.dict(os.environ, {"CIVIL_JOB_ROOT": "", "CIVIL_SANDBOX_ROOTS": str(self.root), "CIVIL_SANDBOX": "workspace-write", "CIVIL_APPROVAL": "on-request"})):
            context.start()
            self.addCleanup(context.stop)

    def test_actual_markdown_word_and_excel_tables_match_and_formulas_are_neutralized(self):
        from xml.etree import ElementTree as ET
        from zipfile import ZipFile
        import openpyxl
        result = default_engine().execute(TOOL, {"text": "检验批：=Lot|A\n检查项：外观\n自检记录：A & B\n实测：0.3mm", "confirm_ok": True,
                                                "session_id": "export-quality"}, expert_id="quality", intent="run")
        self.assertTrue(result["ok"] and result["wrote"] and result["submit_blocked"], result)
        paths = {Path(f["path"]).suffix: Path(f["path"]) for f in result["files"]}
        self.assertTrue({".md", ".docx", ".xlsx"}.issubset(paths), paths)
        expected = table(paths[".md"].read_text(encoding="utf-8"), "7 通病")
        book = openpyxl.load_workbook(paths[".xlsx"])
        try:
            sheet = next(book[n] for n in book.sheetnames if n.startswith("7 通病"))
            self.assertEqual(expected, [["" if c.value is None else c.value for c in row] for row in sheet])
            self.assertTrue(all(c.data_type != "f" for sheet in book for row in sheet for c in row))
        finally:
            book.close()
        with ZipFile(paths[".docx"]) as archive:
            document = ET.fromstring(archive.read("word/document.xml"))
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        tables = [[["".join(t.text or "" for t in c.findall(".//w:t", ns)) for c in r.findall("w:tc", ns)]
                   for r in tbl.findall("w:tr", ns)] for tbl in document.findall(".//w:tbl", ns)]
        self.assertIn(expected, tables)

    def test_confirmation_question_and_readonly_paths_do_not_write(self):
        denied = expert_turn.run_named_exclusive(TOOL, {"text": "写质量检查表", "session_id": "gate-quality"})
        self.assertFalse(denied["wrote"])
        self.assertTrue(denied["hitl_pending"])
        result = expert_turn.run_expert_turn("检验批怎么检查？", "quality", session_id="chat-quality")
        self.assertFalse(result["wrote"])
        with patch.dict(os.environ, {"CIVIL_SANDBOX": "read-only"}):
            denied = expert_turn.run_named_exclusive(TOOL, {"text": "写质量检查表", "confirm_ok": True, "session_id": "ro-quality"})
            self.assertFalse(denied["wrote"])
        self.assertFalse(list(self.root.rglob("*.md")))

    def test_hostile_inputs_leave_columns_intact_and_do_not_assert_verdicts(self):
        from scripts.test_post_robustness import INPUTS, VERDICTS, table_rows
        import re
        for name, source in INPUTS.items():
            with self.subTest(input=name):
                result = expert_turn.run_named_exclusive(TOOL, {"text": source, "confirm_ok": True, "session_id": "hostile-quality"})
                self.assertTrue(result["wrote"] and result["submit_blocked"], result)
                md = next(Path(f["path"]) for f in result["files"] if f["path"].endswith(".md")).read_text(encoding="utf-8")
                parsed = table_rows(md)
                for i in range(1, len(parsed)):
                    number, cells = parsed[i]
                    previous_number, previous = parsed[i-1]
                    if number != previous_number+1 or all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                        continue
                    self.assertEqual(len(previous), len(cells))
                self.assertTrue(all(pattern.search(md) is None for pattern in VERDICTS))


if __name__ == "__main__":
    unittest.main()
