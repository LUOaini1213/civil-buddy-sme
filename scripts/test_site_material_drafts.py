#!/usr/bin/env python3
"""Site-material sources, quantity identities, stocktake boundaries and exports."""
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
from packing_assistant.post_drafts.site_materials import MISSING, TOOL, _owned, _with_units, build_draft, parse
from packing_assistant.runtime.scheduler import Scheduler
from packing_assistant.runtime.tool_engine import default_engine


def draft(text):
    return build_draft("material-site", TOOL, text)


def table(md, title):
    return next(rows for name, rows in tables_from_md(md) if name.startswith(title))


class SiteMaterials(unittest.TestCase):
    def test_dispatch_exclusive_and_no_country_price_balance_or_approval_default(self):
        md = draft("")
        self.assertEqual(md, dispatch("material-site", TOOL, ""))
        self.assertIsNone(build_draft("material-site", "quality__lot", ""))
        self.assertIsNone(build_draft("quality", TOOL, ""))
        self.assertEqual(10, len([s for s in md.splitlines() if s.startswith("## ") and not s.startswith("###")]))
        for marker in ("AI 草稿", "submit_blocked=true", "无盘点不编盈亏", "TBD", "签认：（空）"):
            self.assertIn(marker, md)
        for word in ("CN：", "SG：", "Factory Notification", "## 用户原文", "检验合格", "节超已核定", "可以结算", "盈亏已核实"):
            self.assertNotIn(word, md)

    def test_all_dev_facts_have_semantically_correct_fields(self):
        from scripts.eval_post_content import classify, load, spots, split_echo
        for case in load("cases"):
            if case["post"] != "material-site":
                continue
            with self.subTest(case=case["id"]):
                md = draft(case["request"])
                body, echo = split_echo(md)
                for fact in case["facts"]:
                    self.assertEqual("placed", classify(fact, body, echo, spots(body)), fact)
                self.assertTrue(all(w not in body for w in case.get("forbidden", [])))

    def test_narrative_steel_quantities_and_sources_do_not_become_calculated_consumption(self):
        data = parse("九月的材料核算表头做一下：3#楼三至五层墙柱钢筋 HRB400，已完工程量按对上计量是326.8吨，消耗指标1.02来自公司指标，本期领料341.5吨，退料12.6吨，月底堆场盘点实存27.3吨。")
        self.assertEqual(1, len(data.rows))
        row = data.rows[0]
        self.assertEqual(("3#楼三至五层墙柱", "钢筋", "HRB400", "326.8吨", "对上计量", "1.02", "公司指标"), tuple(row[k] for k in ("location", "material", "model", "work", "work_source", "indicator", "indicator_source")))
        self.assertEqual(("341.5吨", "12.6吨", "27.3吨"), tuple(row[k] for k in ("issued", "returned", "counted")))
        self.assertNotIn("actual", row)
        self.assertNotIn("expected", row)
        self.assertNotIn("variance", row)

    def test_material_before_location_in_sentence_remains_one_object(self):
        data = parse("加气块是2#楼二次结构用的，领了2860块，盘点堆场还剩412块，应耗商务还没给；防水卷材是地下室外墙用的，领料76卷，退了14卷，收方单上工程量1893平，盘点这周没做。")
        first, second = data.rows
        self.assertEqual(("加气块", "2#楼二次结构", "2860块", "412块"), tuple(first[k] for k in ("material", "location", "issued", "counted")))
        self.assertEqual(("防水卷材", "地下室外墙", "76卷", "14卷", "1893平", "收方单"), tuple(second[k] for k in ("material", "location", "issued", "returned", "work", "work_source")))
        self.assertNotIn("counted", second)
        self.assertNotIn("expected", first)
        self.assertNotIn("actual", first)
        self.assertNotIn("actual", second)

    def test_ticket_discrepancy_is_record_not_inventory_or_adjustment(self):
        data = parse("部位：地下室B2层底板\n材料：商品混凝土 C35P8\n盘点：现场收方与小票差 47.8 方")
        self.assertEqual(1, len(data.rows))
        self.assertEqual("现场收方与小票差 47.8 方", data.rows[0]["count_record"])
        self.assertNotIn("counted", data.rows[0])
        self.assertNotIn("adjustment", data.rows[0])
        self.assertNotIn("actual", data.rows[0])
        md = draft("材料：混凝土\n盘点：现场收方与小票差 47.8 方")
        stock = table(md, "5 节超")
        self.assertEqual(MISSING, stock[1][stock[0].index("用户盘点实存原文（未核）")])

    def test_pending_hypothetical_or_estimated_count_is_not_physical_inventory(self):
        for text in ("盘点这周没做", "预计实存412块", "感觉实存412块", "约412块", "据说还剩412块", "未确认调整412块"):
            with self.subTest(text=text):
                row = parse("材料：加气块\n盘点："+text).rows[0]
                self.assertNotIn("counted", row)

    def test_forecast_hearsay_and_not_occurred_are_records_not_actual_quantities(self):
        for text, key in (("预计实耗12吨", "actual"), ("计划退料3吨", "returned"), ("还没领料35吨", "issued"),
                          ("未退料3吨", "returned"), ("听说实耗12吨", "actual"), ("明天领料35吨", "issued"),
                          ("预计已完工程量40吨", "work"), ("节超估计3吨", "variance"), ("实耗：预计12吨", "actual")):
            with self.subTest(text=text):
                row = parse("材料：钢筋\n"+text).rows[0]
                self.assertEqual(MISSING, row.get(key, MISSING))
                self.assertIn(text.replace("实耗：", ""), row["record"])

    def test_user_forecast_plan_quantity_does_not_become_actual_consumption(self):
        row = parse("材料：钢筋\n预计应耗12吨").rows[0]
        self.assertEqual("12吨", row["expected"])
        self.assertEqual("预计应耗12吨", row["record"])
        self.assertNotIn("actual", row)
        self.assertNotIn("issued", row)

    def test_engineering_work_units_never_replace_material_units(self):
        rows = _with_units(parse("材料：防水卷材\n工程量：1893平\n领料：76卷\n退料：14卷").rows)
        self.assertEqual("平", rows[0]["work_unit"])
        self.assertEqual("卷", rows[0]["unit"])
        self.assertNotIn("actual", rows[0])

    def test_mixed_material_quantity_units_are_flagged_not_converted_or_added(self):
        row = _with_units(parse("材料：钢筋\n领料：35吨\n退料：120kg").rows)[0]
        self.assertEqual(MISSING, row.get("unit", MISSING))
        self.assertIn("待核", row["unit_note"])
        self.assertEqual("35吨", row["issued"])
        self.assertEqual("120kg", row["returned"])

    def test_explicit_unit_is_not_overwritten_by_quantity_unit(self):
        row = _with_units(parse("材料：钢筋\n单位：kg\n领料：35吨").rows)[0]
        self.assertEqual("kg", row["unit"])
        self.assertIn("待核", row["unit_note"])

    def test_models_do_not_create_quantities_strength_or_default_material(self):
        data = parse("材料：商品混凝土 C35P8")
        self.assertEqual(("商品混凝土", "C35P8"), tuple(data.rows[0][k] for k in ("material", "model")))
        for key in ("work", "expected", "issued", "counted", "actual", "variance"):
            self.assertNotIn(key, data.rows[0])
        self.assertEqual(MISSING, parse("材料：C35P8").rows[0]["material"])

    def test_negated_unknown_or_question_material_does_not_leak_embedded_model(self):
        for value in ("不是钢筋 HRB400", "钢筋 HRB400？", "未定 HRB400"):
            with self.subTest(value=value):
                data = parse("部位：3#楼\n材料："+value)
                self.assertTrue(all(r.get("model", MISSING) == MISSING for r in data.rows))
                self.assertTrue(all(r.get("material", MISSING) == MISSING for r in data.rows))

    def test_numeric_values_do_not_come_from_models_versions_dates_or_examples(self):
        for label, key in (("领料", "issued"), ("退料", "returned"), ("实存", "counted"), ("消耗指标", "indicator")):
            for value in ("HRB400", "C35P8", "V2.0", "2026-10-20", "EQ-12", "不是12吨", "12吨？", "例如12吨", "如果12吨", "未给"):
                with self.subTest(label=label, value=value):
                    row = parse("材料：钢筋\n"+label+"："+value).rows[0]
                    self.assertEqual(MISSING, row.get(key, MISSING))

    def test_indicator_and_loss_rate_do_not_convert_into_each_other(self):
        row = parse("材料：钢筋\n消耗指标：1.02\n损耗率：2%").rows[0]
        self.assertEqual("1.02", row["indicator"])
        self.assertEqual("2%", row["loss"])
        self.assertNotIn("expected", row)

    def test_new_model_does_not_borrow_quantities_sources_inventory_or_units(self):
        data = parse("部位：3#楼\n材料：钢筋 HRB400\n单位：吨\n领料：35吨\n领料来源：A.csv\n盘点：实存12吨\n规格：HRB500")
        first, second = data.rows
        self.assertEqual("HRB400", first["model"])
        self.assertEqual("HRB500", second["model"])
        self.assertEqual("钢筋", second["material"])
        self.assertEqual("3#楼", second["location"])
        for key in ("issued", "issue_source", "counted", "unit"):
            self.assertNotIn(key, second)

    def test_new_location_does_not_borrow_model_or_previous_numbers(self):
        data = parse("部位：3#楼\n材料：钢筋 HRB400\n领料：35吨\n部位：4#楼\n退料：12吨")
        self.assertEqual(2, len(data.rows))
        self.assertEqual("4#楼", data.rows[1]["location"])
        self.assertEqual("钢筋", data.rows[1]["material"])
        self.assertNotIn("model", data.rows[1])
        self.assertNotIn("issued", data.rows[1])
        self.assertNotIn("returned", data.rows[0])

    def test_period_and_batch_changes_do_not_borrow_actual_consumption_or_count(self):
        for label, first, second, key in (("期间", "9月", "10月", "period"), ("批号", "B1", "B2", "batch")):
            with self.subTest(label=label):
                data = parse("材料：钢筋\n"+label+"："+first+"\n实耗：35吨\n盘点：12吨\n"+label+"："+second+"\n领料：20吨")
                self.assertEqual([first, second], [r[key] for r in data.rows])
                self.assertNotIn("actual", data.rows[1])
                self.assertNotIn("counted", data.rows[1])
                self.assertNotIn("issued", data.rows[0])

    def test_unique_record_id_reference_returns_only_to_its_own_material(self):
        data = parse("材料：钢筋\n记录编号：A\n领料：35吨\n材料：卷材\n记录编号：B\n领料：20卷\n记录编号：A\n退料：12吨")
        self.assertEqual(2, len(data.rows))
        self.assertEqual("12吨", data.rows[0]["returned"])
        self.assertNotIn("returned", data.rows[1])

    def test_record_id_reference_with_changed_model_keeps_new_grade_separate(self):
        data = parse("| 材料 | 记录编号 | 规格 | 领料 |\n| --- | --- | --- | --- |\n| 钢筋 | A | HRB400 | 35吨 |\n\n"
                     "记录编号：A\n规格：HRB500\n退料：12吨")
        self.assertEqual(2, len(data.rows))
        self.assertEqual(["HRB400", "HRB500"], [row["model"] for row in data.rows])
        self.assertEqual("钢筋", data.rows[1]["material"])
        self.assertNotIn("issued", data.rows[1])
        self.assertNotIn("returned", data.rows[0])

    def test_blank_table_metadata_does_not_hide_conflicting_file_scope(self):
        data = parse("版本：V1\n| 材料 | 版本 | 领料 |\n| --- | --- | --- |\n| 钢筋 | | 35吨 |\n\n"
                     "版本：V2\n退料：12吨")
        self.assertEqual(2, len(data.rows))
        self.assertEqual("V2", data.rows[1]["version"])
        self.assertNotIn("material", data.rows[1])
        self.assertNotIn("issued", data.rows[1])

    def test_duplicate_record_id_without_identity_stays_unassigned_not_last_material(self):
        data = parse("材料：钢筋\n记录编号：DUP\n领料：35吨\n材料：卷材\n记录编号：DUP\n记录编号：DUP\n退料：12吨")
        self.assertEqual(3, len(data.rows))
        self.assertNotIn("material", data.rows[2])
        self.assertEqual("12吨", data.rows[2]["returned"])
        self.assertNotIn("returned", data.rows[1])

    def test_multi_issue_events_keep_each_quantity_and_source_without_sum(self):
        data = parse("材料：钢筋\n领料：10吨\n领料来源：A.csv\n领料：20吨\n领料来源：B.csv\n退料：3吨\n退料来源：C.csv")
        self.assertEqual([("10吨", "A.csv"), ("20吨", "B.csv"), ("3吨", "C.csv")], [(e["quantity"], e["source"]) for e in data.events])
        self.assertEqual("10吨；20吨", data.rows[0]["issued"])
        self.assertNotIn("actual", data.rows[0])
        self.assertNotIn("27吨", draft("材料：钢筋\n领料：10吨\n领料：20吨\n退料：3吨"))

    def test_reordered_table_sources_attach_to_their_role_not_last_quantity(self):
        data = parse("| 退料来源 | 领料来源 | 工程量来源 | 材料 | 部位 | 领料 | 退料 | 工程量 |\n| --- | --- | --- | --- | --- | --- | --- | --- |\n"
                     "| R.csv | I.csv | W.pdf | 钢筋 | 3#楼 | 35吨 | 12吨 | 40吨 |")
        sources = {e["field"]: (e["quantity"], e.get("source")) for e in data.events}
        self.assertEqual(("35吨", "I.csv"), sources["本期领料"])
        self.assertEqual(("12吨", "R.csv"), sources["本期退料"])
        self.assertEqual(("40吨", "W.pdf"), sources["已完工程量"])

    def test_empty_table_identity_and_tail_notes_do_not_borrow_previous_material(self):
        data = parse("| 材料 | 部位 | 领料 |\n| --- | --- | --- |\n| 钢筋 | 3#楼 | 35吨 |\n| | | |\n\n退料：12吨")
        self.assertEqual(3, len(data.rows))
        self.assertEqual(MISSING, data.rows[1]["material"])
        self.assertNotIn("material", data.rows[2])
        self.assertNotIn("returned", data.rows[0])
        self.assertEqual("12吨", data.rows[2]["returned"])

    def test_generic_source_does_not_become_work_count_or_consumption_source(self):
        row = parse("材料：钢筋\n来源：A.pdf\n领料：35吨").rows[0]
        self.assertEqual("A.pdf", row["source"])
        for key in ("work_source", "count_source", "actual_source", "issue_source"):
            self.assertNotIn(key, row)

    def test_work_source_bare_continuation_is_not_borrowed_into_other_file(self):
        data = parse("### A.txt\n材料：混凝土\n已完工程量：1284.6方\n来源结施-05\n### B.txt\n来源结施-06")
        self.assertEqual("结施-05", data.rows[0]["work_source"])
        self.assertEqual("结施-06", data.rows[1]["source"])
        self.assertNotIn("work", data.rows[1])

    def test_sources_with_no_quantity_do_not_steal_previous_roles_quantity(self):
        data = parse("材料：钢筋\n领料：35吨\n退料来源：R.csv")
        self.assertEqual(MISSING, data.events[1]["quantity"])
        self.assertEqual("R.csv", data.events[1]["source"])
        self.assertNotIn("source", data.events[0])

    def test_files_repeated_names_and_regions_have_distinct_material_owners(self):
        data = parse("### A.txt\n辖区：CN\n材料：钢筋\n规格：HRB400\n领料：35吨\n### A.txt\n材料：钢筋\n退料：12吨")
        self.assertEqual(2, len(data.rows))
        self.assertEqual(["CN", "UNSPECIFIED"], [r["jurisdiction"] for r in data.rows])
        self.assertNotIn("model", data.rows[1])
        self.assertNotIn("issued", data.rows[1])

    def test_project_version_and_region_change_do_not_reuse_previous_material(self):
        for label, first, second, key in (("项目", "甲", "乙", "project"), ("版本", "V1", "V2", "version"), ("辖区", "CN", "SG", "jurisdiction")):
            with self.subTest(label=label):
                data = parse("材料：钢筋\n"+label+"："+first+"\n领料：35吨\n"+label+"："+second+"\n退料：12吨")
                self.assertEqual(2, len(data.rows))
                self.assertEqual([first, second], [r[key] for r in data.rows])
                self.assertNotIn("material", data.rows[1])
                self.assertNotIn("issued", data.rows[1])

    def test_turnover_states_are_separate_not_net_inventory_or_amortization(self):
        row = parse("材料：钢管\n材料类别：周转材料\n进场数量：300根\n在用：200根\n维修：20根\n报废：3根\n退租：40根\n摊销方法：分次").rows[0]
        self.assertEqual(["300根", "200根", "20根", "3根", "40根"], [row[k] for k in ("arrival", "in_use", "repair", "scrapped", "return_rental")])
        self.assertEqual("分次", row["amortization"])
        self.assertNotIn("actual", row)

    def test_material_category_is_not_inferred_from_name(self):
        data = parse("材料：钢管\n领料：35根")
        self.assertNotIn("kind", data.rows[0])
        md = draft("材料：钢管\n材料类别：周转材料\n材料：钢筋\n材料类别：实体材料\n材料：卷材")
        self.assertEqual("钢筋", table(md, "用户明示主要")[1][1])
        self.assertEqual("钢管", table(md, "用户明示周转")[1][1])
        self.assertEqual("卷材", table(md, "材料类别待核")[1][1])

    def test_source_events_keep_physical_location_separate_from_input_position(self):
        data = parse("### A.txt\n部位：3#楼\n材料：钢筋\n领料：35吨")
        event = _owned(data, data.events)[0]
        self.assertEqual("3#楼", event["location"])
        self.assertEqual("A.txt · 本轮输入第4行（非原件页码）", event["input_position"])
        self.assertEqual("A.txt", event["_origin"])

    def test_english_labels_preserve_units_sources_and_missing_fields(self):
        row = parse("Project: Synthetic A\nJurisdiction: SG\nMaterial: rebar\nModel: HRB400\nLocation: Zone A\nPeriod: 2026-10\nIssued: 1,200kg\nIssue source: I.csv\nReturned: 50kg").rows[0]
        self.assertEqual(("rebar", "HRB400", "Zone A", "1,200kg", "I.csv", "50kg"), tuple(row[k] for k in ("material", "model", "location", "issued", "issue_source", "returned")))
        self.assertNotIn("counted", row)
        self.assertNotIn("actual", row)

    def test_examples_not_facts_and_explicit_actual_reset_is_kept(self):
        data = parse("例如钢筋HRB400领料35吨，退料12吨\n实际情况：材料：卷材\n领料：76卷")
        self.assertEqual(1, len(data.rows))
        self.assertEqual("卷材", data.rows[0]["material"])
        self.assertEqual("76卷", data.rows[0]["issued"])
        self.assertNotIn("35吨", repr(data))

    def test_dates_do_not_come_from_codes_or_assumed_calendar(self):
        for value in ("COUNT-20261020", "V2.0", "不是2026-10-20", "2026-10-20？"):
            self.assertEqual(MISSING, parse("材料：钢筋\n盘点日期："+value).rows[0]["count_date"])
        self.assertEqual("月底", parse("材料：钢筋\n盘点日期：月底").rows[0]["count_date"])

    def test_user_actual_variance_price_and_sign_do_not_create_calculated_values(self):
        md = draft("材料：钢筋\n实耗：35吨\n节超：-2吨\n单价：SGD 800/t\n单价来源：P-A.csv\n节超符号约定：超耗为正\n隔离：批次B暂隔离")
        rows = table(md, "6 核算")
        self.assertEqual(["TBD"]*5, rows[1][-5:])
        self.assertEqual("35吨", rows[1][rows[0].index("用户实耗原文（未核）")])
        self.assertEqual("-2吨", rows[1][rows[0].index("用户节超原文（未核）")])
        self.assertEqual("SGD 800/t", rows[1][rows[0].index("用户单价原文（未核）")])
        self.assertEqual("P-A.csv", rows[1][rows[0].index("单价来源")])
        self.assertEqual("超耗为正", rows[1][rows[0].index("用户正负符号约定")])
        self.assertIn("超耗为正", md)
        self.assertIn("批次B暂隔离", md)
        self.assertIn("SGD 800/t", md)

    def test_mixed_work_units_reach_primary_table_without_converting(self):
        rows = table(draft("材料：钢筋\n工程量：30吨\n工程量：40kg\n领料：35吨"), "6 核算")
        self.assertEqual("30吨；40kg", rows[1][rows[0].index("用户已完工程量（未核）")])
        self.assertEqual(MISSING, rows[1][rows[0].index("工程量单位（用户原文）")])
        self.assertIn("待核", rows[1][rows[0].index("工程量单位待核")])
        self.assertEqual("吨", rows[1][rows[0].index("材料单位（用户原文）")])


class SiteMaterialRuntime(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="site-material-regression-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for context in (patch.object(expert_turn, "_OUT", self.root),
                        patch("packing_assistant.runtime.memory._OUT", self.root),
                        patch("packing_assistant.runtime.scheduler.get_scheduler", return_value=Scheduler()),
                        patch.dict(os.environ, {"CIVIL_JOB_ROOT": "", "CIVIL_SANDBOX_ROOTS": str(self.root), "CIVIL_SANDBOX": "workspace-write", "CIVIL_APPROVAL": "on-request"})):
            context.start()
            self.addCleanup(context.stop)

    def test_actual_markdown_word_and_excel_tables_match_and_neutralize_formulas(self):
        from xml.etree import ElementTree as ET
        from zipfile import ZipFile
        import openpyxl
        result = default_engine().execute(TOOL, {"text": "材料：=rebar|A\n部位：A & B\n领料：35吨", "session_id": "export-site-material"}, expert_id="material-site", intent="run")
        self.assertTrue(result["ok"] and result["wrote"] and result["submit_blocked"], result)
        paths = {Path(f["path"]).suffix: Path(f["path"]) for f in result["files"]}
        self.assertTrue({".md", ".docx", ".xlsx"}.issubset(paths), paths)
        expected = table(paths[".md"].read_text(encoding="utf-8"), "6 核算")
        book = openpyxl.load_workbook(paths[".xlsx"])
        try:
            sheet = next(book[n] for n in book.sheetnames if n.startswith("6 核算"))
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

    def test_question_and_readonly_paths_cannot_write(self):
        result = expert_turn.run_expert_turn("节超怎么理解？", "material-site", session_id="chat-site-material")
        self.assertFalse(result["wrote"])
        with patch.dict(os.environ, {"CIVIL_SANDBOX": "read-only"}):
            denied = expert_turn.run_named_exclusive(TOOL, {"text": "写材料核算表头", "session_id": "ro-site-material"})
            self.assertFalse(denied["wrote"])
        self.assertFalse(list(self.root.rglob("*.md")))

    def test_hostile_inputs_leave_columns_intact_and_do_not_assert_verdicts(self):
        from scripts.test_post_robustness import INPUTS, VERDICTS, table_rows
        import re
        for name, source in INPUTS.items():
            with self.subTest(input=name):
                result = expert_turn.run_named_exclusive(TOOL, {"text": source, "session_id": "hostile-site-material"})
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
