#!/usr/bin/env python3
"""Offline planning ownership, conservative conditions and real Office exports."""
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
from packing_assistant.post_drafts.planning import MISSING, TOOLS, build_draft, parse
from packing_assistant.runtime.scheduler import Scheduler
from packing_assistant.runtime.tool_engine import default_engine


def draft(post, text):
    return build_draft(post, TOOLS[post], text)


def table(md, title):
    return next(rows for name, rows in tables_from_md(md) if name.startswith(title))


class PlanningDrafts(unittest.TestCase):
    def test_all_posts_dispatch_only_their_tool_and_keep_internal_boundaries(self):
        for post, tool in TOOLS.items():
            md = draft(post, "")
            self.assertEqual(md, dispatch(post, tool, ""))
            self.assertIsNone(build_draft(post, "warehouse__stock", ""))
            self.assertIn("AI 草稿", md)
            self.assertIn("submit_blocked=true", md)
            self.assertIn("签认", md)
            self.assertNotIn("## 用户原文", md)
            self.assertNotIn("GB/T", md)
            self.assertNotIn("PSSCOC", md)
            self.assertIn("UNSPECIFIED", md)

    def test_all_development_facts_have_semantically_correct_fields(self):
        from scripts.eval_post_content import classify, load, spots, split_echo
        for case in load("cases"):
            if case["post"] not in TOOLS:
                continue
            with self.subTest(case=case["id"]):
                md = draft(case["post"], case["request"])
                body, echo = split_echo(md)
                for fact in case["facts"]:
                    self.assertEqual("placed", classify(fact, body, echo, spots(body)), fact)
                self.assertFalse(any(word in body for word in case["forbidden"]))

    def test_master_keeps_building_dates_durations_sources_and_crews_independent(self):
        data = parse("plan-master", "5#楼基础9月底完，主体封顶甲方卡的是2027-06-30，这栋归三公司干；"
                     "6#楼工程量按建施-11算的，工期粗算560天，封顶日子还没给准信，干的队伍也没定。")
        self.assertEqual(["5#楼", "5#楼", "6#楼"], [r["unit"] for r in data.nodes])
        self.assertEqual(["9月底", "2027-06-30", MISSING], [r["node_date"] for r in data.nodes])
        first, second = data.activities
        self.assertEqual("三公司", first["owner"])
        self.assertEqual(("建施-11", "粗算560天"), (second["qty_source"], second["duration"]))
        self.assertNotIn("owner", second)
        self.assertNotIn("duration", first)
        self.assertNotIn("qty_source", first)
        self.assertTrue(all("contract" not in r for r in data.contexts))

    def test_contract_duration_does_not_become_an_activity_duration(self):
        data = parse("plan-master", "锦澜府三期合同工期720日历天，计划2026-10-15开工，地下室结构由二分公司干。")
        self.assertEqual("720日历天", data.contexts[0]["contract"])
        self.assertEqual("2026-10-15", data.contexts[0]["start"])
        self.assertTrue(all("duration" not in r for r in data.activities))
        self.assertTrue(all("finish" not in r for r in data.contexts))

    def test_master_keeps_literal_logic_without_solving_or_guessed_edges(self):
        data = parse("plan-master", "活动：地下室顶板\n活动ID：W-A\n紧前：外墙 FS+2天\n持续时间：18工作日\n来源：网络A")
        row = data.activities[0]
        self.assertEqual(("W-A", "外墙 FS+2天", "18工作日", "网络A"),
                         tuple(row[k] for k in ("activity_id", "dependency", "duration", "source")))
        md = draft("plan-master", "活动：地下室顶板\n紧前：外墙 FS+2天")
        self.assertIn("关键线路=待计算", md)
        self.assertIn("总时差=待计算", md)
        self.assertNotIn("总时差为零", md)

    def test_master_multi_activity_responsibility_does_not_drop_decoration(self):
        data = parse("plan-master", "地下室结构由二分公司干，主体和装饰归一分公司，工程量来源是结施-03清单。")
        self.assertEqual(["地下室结构", "主体和装饰"], [r["activity"] for r in data.activities if "activity" in r])
        self.assertTrue(all("qty_source" not in r for r in data.activities if "activity" in r))
        self.assertEqual("结施-03清单", data.activities[-1]["qty_source"])
        self.assertNotIn("activity", data.activities[-1])

    def test_explicit_week_is_not_repeated_in_other_weeks(self):
        md = draft("plan-lookahead", "第2周区段：4#楼七层頂板\n责任班组：木工二队")
        plan = table(md, "3 近细")
        self.assertEqual(2, len(plan))
        self.assertEqual("第2周", plan[1][0])
        self.assertEqual(1, sum("4#楼七层頂板" in line for line in md.splitlines() if line.startswith("| 第")))
        promises = table(md, "5 周承诺")
        self.assertNotIn("4#楼七层頂板", repr(promises))

    def test_clearance_for_one_activity_cannot_clear_another(self):
        md = draft("plan-lookahead", "活动：3层砌筑\n制约：制约已清\n活动：4层砌筑\n制约：塔吊未到")
        promise = table(md, "5 周承诺")
        self.assertEqual("3层砌筑", promise[1][0])
        self.assertNotIn("4层砌筑", repr(promise))
        constraints = table(md, "4 制约")
        self.assertIn("塔吊未到", repr(constraints))

    def test_ambiguous_bare_clearance_after_two_activities_is_unassigned(self):
        md = draft("plan-lookahead", "活动：作业A\n活动：作业B\n制约已清")
        promises = table(md, "5 周承诺")
        self.assertEqual(MISSING, promises[1][0])
        self.assertNotIn("作业A", repr(promises))
        self.assertNotIn("作业B", repr(promises))

    def test_negated_question_and_future_clearance_do_not_create_promises(self):
        for condition in ("不是制约已清", "制约已清？", "如果制约已清", "制约已清但塔吊未到", "制约未清", "尚未达到条件已具备", "预计明天制约已清", "听说制约已清"):
            with self.subTest(condition=condition):
                md = draft("plan-lookahead", "活动：作业A\n制约："+condition)
                self.assertNotIn("作业A", repr(table(md, "5 周承诺")))

    def test_waiting_for_acceptance_blocks_even_a_claimed_clearance(self):
        md = draft("plan-lookahead", "活动：屋面防水\n前置条件：等保温层验收\n制约：制约已清")
        self.assertNotIn("屋面防水", repr(table(md, "5 周承诺")))

    def test_future_week_clearance_is_not_a_this_week_promise(self):
        md = draft("plan-lookahead", "第3周区段：屋面防水\n制约：制约已清")
        self.assertNotIn("屋面防水", repr(table(md, "5 周承诺")))

    def test_cross_work_and_new_subcontractor_do_not_belong_to_last_roof(self):
        data = parse("plan-lookahead", "6#楼屋面防水要等保温层验收完才能插，地下室车库消防管道和桥架在同一层交叉，机电分包10月9日进场，总控里9月30日主体封顶这个节点不能动")
        roof, subcontractor = data.activities
        self.assertNotIn("owner", roof)
        self.assertNotIn("arrival", roof)
        self.assertEqual((MISSING, "机电分包", "10月9日"), tuple(subcontractor[k] for k in ("unit", "owner", "arrival")))
        self.assertEqual(MISSING, data.interfaces[0]["unit"])
        self.assertEqual((MISSING, MISSING), (data.nodes[0]["unit"], data.nodes[0]["activity"]))

    def test_unknown_detection_responsibility_is_not_a_person_or_crew(self):
        data = parse("plan-lookahead", "南区管廊C段回填，土方队说25号能进场，压实度检测归谁做还没说定")
        row = data.activities[0]
        self.assertEqual("土方队", row["owner"])
        self.assertEqual("25号", row["arrival"])
        self.assertIn("压实度检测", row["constraint"])
        self.assertNotIn("9月25", repr(data))

    def test_collective_node_applies_to_exactly_two_identified_jobs_only(self):
        data = parse("plan-lookahead", "活动：作业A\n活动：作业B\n这两摊都得在9月28日前给业主一个形象节点")
        self.assertEqual(["作业A", "作业B"], [r["activity"] for r in data.nodes])
        self.assertEqual(["9月28日", "9月28日"], [r["node_date"] for r in data.nodes])
        data = parse("plan-lookahead", "活动：作业A\n活动：作业B\n活动：作业C\n这两摊都得在9月28日前给业主一个形象节点")
        self.assertEqual(1, len(data.nodes))
        self.assertEqual(MISSING, data.nodes[0]["activity"])

    def test_actual_completion_is_not_relisted_as_future_work(self):
        md = draft("plan-lookahead", "活动：砌体A\n实际完成：昨日报告完成6层（未核）\n活动：砌体B\n制约：图纸未发")
        self.assertNotIn("砌体A", repr(table(md, "3 近细")))
        self.assertIn("砌体B", repr(table(md, "3 近细")))
        actual = table(md, "9 月度")
        self.assertEqual("砌体A", actual[1][0])
        self.assertEqual("昨日报告完成6层（未核）", actual[1][1])
        self.assertNotIn("100%", md)

    def test_resource_narrative_keeps_labor_plant_material_and_dates_separate(self):
        data = parse("plan-resource", "10月12日到10月25日干核心筒爬模，木工按劳务公司报的86人，钢筋工45人，塔吊上一台STT293，钢筋10月8日进场，这批是甲指材料，堆在北侧堆场。")
        wood, steel_labor, crane, steel = data.resources
        self.assertEqual(["labor", "labor", "plant", "material"], [r["kind"] for r in data.resources])
        self.assertEqual(("86人", "45人", "一台"), (wood["demand"], steel_labor["demand"], crane["demand"]))
        self.assertEqual("STT293", crane["model"])
        self.assertNotIn("arrival", crane)
        self.assertEqual(("10月8日", "甲指", "北侧堆场"), tuple(steel[k] for k in ("arrival", "supply", "yard")))
        self.assertTrue(all(r["activity"] == "核心筒爬模" for r in data.resources))
        self.assertTrue(all("available" not in r for r in data.resources))

    def test_crane_uses_brick_is_not_an_independent_material_requirement(self):
        data = parse("plan-resource", "3#楼砌筑下周上瓦工38个，塔吊TC5613吊砖，进场日子定的11月20日；2#楼二次结构上多少人还没定，只知道要用汽车吊，型号没报，材料是加气块，堆到东侧空地。")
        self.assertEqual(4, len(data.resources))
        labor, crane, second_crane, blocks = data.resources
        self.assertEqual("38个", labor["demand"])
        self.assertEqual(("TC5613", "11月20日", "3#楼"), tuple(crane[k] for k in ("model", "arrival", "unit")))
        self.assertEqual("2#楼", second_crane["unit"])
        self.assertNotIn("arrival", second_crane)
        self.assertNotIn("model", second_crane)
        self.assertEqual("加气块", blocks["material"])
        self.assertNotIn("demand", blocks)

    def test_demand_and_availability_remain_distinct_literals(self):
        data = parse("plan-resource", "活动：作业A\n工种：木工需用20人实际可用8人\n工种：钢筋工\n需用人数：12人\n可用量：6人")
        self.assertEqual(["20人", "12人"], [r["demand"] for r in data.resources])
        self.assertEqual(["8人", "6人"], [r["available"] for r in data.resources])
        md = draft("plan-resource", "活动：作业A\n工种：木工\n需用人数：20人\n可用量：8人")
        self.assertNotIn("12人缺口", md)
        self.assertIn("峰值=待计算", md)

    def test_model_dates_drawings_versions_and_negations_are_not_quantities(self):
        for value in ("STT293", "TC5613", "HB56", "结施-20", "2026-11-20", "11月20日", "V2.0", "不是20人", "如果20人", "20人？", "还没定20人", "A-20人"):
            with self.subTest(value=value):
                data = parse("plan-resource", "工种：木工\n需求量："+value)
                self.assertEqual(MISSING, data.resources[0].get("demand", MISSING))
        for value in ("木工不是20人", "木工还没定20人", "木工需用20人？", "木工比如20人"):
            with self.subTest(value=value):
                data = parse("plan-resource", "工种："+value)
                self.assertTrue(all("demand" not in r for r in data.resources))

    def test_resource_claimed_source_does_not_turn_labor_source_into_material_source(self):
        data = parse("plan-resource", "活动：顶板\n工种：架子工52人\n材料：木方\n来源：劳务公司已批的班组编制")
        self.assertNotIn("source", data.resources[0])
        self.assertNotIn("source", data.resources[1])
        self.assertEqual("unassigned", data.resources[2]["kind"])
        self.assertIn("已批", data.resources[2]["source"])

    def test_source_boundaries_do_not_reuse_building_activity_resource_or_region(self):
        for post, record in (("plan-master", "活动：3#楼砌筑\n持续时间：18天"),
                             ("plan-lookahead", "活动：3#楼砌筑\n制约：制约已清"),
                             ("plan-resource", "活动：3#楼砌筑\n工种：木工20人")):
            data = parse(post, "### A.txt\n辖区：CN\n"+record+"\n### B.txt\n责任单位：乙队")
            last = data.resources[-1] if post == "plan-resource" else data.activities[-1]
            self.assertEqual("UNSPECIFIED", last["jurisdiction"])
            self.assertEqual(MISSING, last["unit"])
            self.assertEqual(MISSING, last.get("activity", MISSING))

    def test_repeated_filename_is_still_an_independent_source_scope(self):
        data = parse("plan-master", "### A.txt\n活动：3#楼砌筑\n持续时间：18天\n### A.txt\n活动：3#楼砌筑\n责任单位：乙队")
        self.assertEqual(2, len(data.activities))
        self.assertNotIn("duration", data.activities[1])
        self.assertNotIn("owner", data.activities[0])

    def test_baseline_change_does_not_reuse_previous_activity_fields(self):
        data = parse("plan-master", "基线版本：V1\n活动：3#楼砌筑\n持续时间：18天\n基线版本：V2\n活动：3#楼砌筑\n责任单位：乙队")
        self.assertEqual(2, len(data.activities))
        self.assertNotIn("duration", data.activities[1])
        self.assertEqual(["V1", "V2"], [r["baseline"] for r in data.contexts])

    def test_examples_do_not_become_facts_and_actual_reset_remains_visible(self):
        data = parse("plan-resource", "例如木工20人，塔吊一台。\n实际情况：活动：作业A\n工种：木工12人")
        self.assertEqual(1, len(data.resources))
        self.assertEqual("12人", data.resources[0]["demand"])
        self.assertNotIn("20人", repr(data))

    def test_baseline_stated_after_rows_only_fills_missing_same_source_provenance(self):
        data = parse("plan-master", "### A.txt\n活动：作业A\n里程碑：封顶 10月8日\n基线版本：V1.0\n"
                     "### B.txt\n活动：作业B")
        self.assertEqual(["V1.0", MISSING], [r["_baseline"] for r in data.activities])
        self.assertEqual("V1.0", data.nodes[0]["_baseline"])

    def test_resource_tables_keep_reordered_and_empty_identities(self):
        data = parse("plan-resource", "| 可用量 | 需用人数 | 工种 | 活动 |\n| --- | --- | --- | --- |\n"
                     "| 8人 | 20人 | 木工 | 3#楼砌筑 |\n| | 12人 | 钢筋工 | |\n\n来源：尾注A")
        first, second, tail = data.resources
        self.assertEqual(("3#楼砌筑", "20人", "8人"), tuple(first[k] for k in ("activity", "demand", "available")))
        self.assertEqual(MISSING, second["activity"])
        self.assertEqual(MISSING, second["available"])
        self.assertEqual("12人", second["demand"])
        self.assertEqual(MISSING, tail.get("activity", MISSING))
        self.assertEqual("unassigned", tail["kind"])

    def test_generic_resource_table_keeps_explicit_category(self):
        data = parse("plan-resource", "| 来源 | 资源名称 | 资源类别 | 活动 | 用户需求量 | 可用量 |\n| --- | --- | --- | --- | --- | --- |\n| 台账A | 吊机A | 机械 | 作业A | 一台 | |")
        row = data.resources[0]
        self.assertEqual(("plant", "吊机A", "一台", MISSING, "台账A"), tuple(row[k] for k in ("kind", "plant", "demand", "available", "source")))

    def test_fully_empty_table_row_is_a_real_unassigned_record_not_a_separator(self):
        data = parse("plan-resource", "| 活动 | 工种 | 需用人数 |\n| --- | --- | --- |\n"
                     "| 作业A | 木工 | 20人 |\n| | | |")
        self.assertEqual(2, len(data.resources))
        self.assertEqual(MISSING, data.resources[1]["activity"])
        self.assertEqual(MISSING, data.resources[1]["labor"])
        self.assertEqual(MISSING, data.resources[1]["demand"])

    def test_reordered_table_project_and_region_own_each_activity_and_node(self):
        text = ("| 楼栋 | 活动 | 项目名称 | 辖区 | 节点日期 | 里程碑 |\n| --- | --- | --- | --- | --- | --- |\n"
                "| 3#楼 | 封顶 | 项目A | CN | 10月8日 | 封顶 |\n| 2#楼 | 封顶 | 项目B | SG | | 封顶 |")
        data = parse("plan-master", text)
        self.assertEqual(["项目A", "项目B"], [r["project"] for r in data.activities])
        self.assertEqual(["CN", "SG"], [r["jurisdiction"] for r in data.activities])
        self.assertEqual(["项目A", "项目B"], [r["project"] for r in data.nodes])
        self.assertEqual(["CN", "SG"], [r["jurisdiction"] for r in data.nodes])
        md = draft("plan-master", text)
        actual = table(md, "4 一级")
        self.assertEqual(["CN", "SG"], [r[actual[0].index("对象辖区")] for r in actual[1:]])

    def test_table_resource_inherits_only_its_own_stated_project_and_region(self):
        data = parse("plan-resource", "| 工种 | 需用人数 | 项目名称 | 辖区 | 活动 |\n| --- | --- | --- | --- | --- |\n"
                     "| 木工 | 20人 | 项目A | CN | 3#楼砌筑 |\n| 钢筋工 | | 项目B | SG | 2#楼二次结构 |")
        self.assertEqual(["项目A", "项目B"], [r["project"] for r in data.resources])
        self.assertEqual(["CN", "SG"], [r["jurisdiction"] for r in data.resources])
        self.assertEqual(["20人", MISSING], [r["demand"] for r in data.resources])

    def test_a_quantity_for_multiple_resource_identities_stays_unassigned(self):
        data = parse("plan-resource", "| 活动 | 工种 | 机械 | 用户需求量 | 可用量 |\n| --- | --- | --- | --- | --- |\n"
                     "| 作业A | 木工 | 塔吊 | 20 | 8 |")
        labor, plant, unassigned = data.resources
        self.assertNotIn("demand", labor)
        self.assertNotIn("available", plant)
        self.assertEqual(("unassigned", "20", "8"), tuple(unassigned[k] for k in ("kind", "demand", "available")))

    def test_master_table_has_no_cross_row_dates_durations_or_owners(self):
        data = parse("plan-master", "| 楼栋 | 活动 | 用户工期 | 责任单位 | 节点日期 | 里程碑 |\n| --- | --- | --- | --- | --- | --- |\n"
                     "| 3#楼 | 封顶 | 18天 | 甲队 | 10月8日 | 封顶 |\n| 2#楼 | | | | | 封顶 |")
        self.assertEqual("18天", data.activities[0]["duration"])
        self.assertEqual(MISSING, data.activities[1]["duration"])
        self.assertEqual(["10月8日", MISSING], [r["node_date"] for r in data.nodes])
        self.assertEqual(["3#楼", "2#楼"], [r["unit"] for r in data.nodes])

    def test_evidence_reports_filename_and_physical_input_line_not_invented_page(self):
        data = parse("plan-master", "### A.txt\n活动：作业A\n持续时间：18天\n### B.txt\n责任单位：乙队")
        self.assertEqual(["A.txt · 本轮输入第2行（非原件页码）", "A.txt · 本轮输入第3行（非原件页码）", "B.txt · 本轮输入第5行（非原件页码）"],
                         [r["location"] for r in data.evidence])


class PlanningRuntime(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="planning-regression-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for context in (patch.object(expert_turn, "_OUT", self.root),
                        patch("packing_assistant.runtime.memory._OUT", self.root),
                        patch("packing_assistant.runtime.scheduler.get_scheduler", return_value=Scheduler()),
                        patch.dict(os.environ, {"CIVIL_JOB_ROOT": "", "CIVIL_SANDBOX_ROOTS": str(self.root), "CIVIL_SANDBOX": "workspace-write", "CIVIL_APPROVAL": "on-request"})):
            context.start()
            self.addCleanup(context.stop)

    def test_all_three_tools_export_matching_real_markdown_word_and_excel(self):
        from xml.etree import ElementTree as ET
        from zipfile import ZipFile
        import openpyxl
        for post, text, target in (("plan-master", "活动：=作业|A\n责任单位：乙 & 丙\n持续时间：18天", "3 工作"),
                                   ("plan-lookahead", "活动：=作业|A\n责任班组：乙 & 丙\n制约：图纸未发", "3 近细"),
                                   ("plan-resource", "活动：=作业|A\n工种：木工\n需用人数：20人\n可用量：8人", "3 劳动")):
            with self.subTest(post=post):
                result = default_engine().execute(TOOLS[post], {"text": text, "session_id": "export-"+post}, expert_id=post, intent="run")
                self.assertTrue(result["ok"] and result["wrote"], result)
                self.assertTrue(result["submit_blocked"])
                paths = {Path(f["path"]).suffix: Path(f["path"]) for f in result["files"]}
                self.assertTrue({".md", ".docx", ".xlsx"}.issubset(paths), paths)
                expected = table(paths[".md"].read_text(encoding="utf-8"), target)
                book = openpyxl.load_workbook(paths[".xlsx"])
                try:
                    sheet = next(book[name] for name in book.sheetnames if name.startswith(target))
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

    def test_readonly_and_question_modes_do_not_write(self):
        for post, tool in TOOLS.items():
            result = expert_turn.run_expert_turn("这个岗位怎么用？", post, session_id="chat-"+post)
            self.assertFalse(result["wrote"])
        with patch.dict(os.environ, {"CIVIL_SANDBOX": "read-only"}):
            for post, tool in TOOLS.items():
                result = expert_turn.run_named_exclusive(tool, {"text": "生成内部表", "session_id": "ro-"+post})
                self.assertFalse(result["wrote"])
        self.assertFalse(list(self.root.rglob("*.md")))

    def test_all_planning_posts_survive_hostile_inputs_with_intact_tables(self):
        from scripts.test_post_robustness import INPUTS, VERDICTS, table_rows
        import re
        for post, tool in TOOLS.items():
            for name, source in INPUTS.items():
                with self.subTest(post=post, input=name):
                    result = expert_turn.run_named_exclusive(tool, {"text": source, "session_id": "hostile-"+post})
                    self.assertTrue(result["wrote"] and result["submit_blocked"], result)
                    md = next(Path(f["path"]) for f in result["files"] if f["path"].endswith(".md")).read_text(encoding="utf-8")
                    parsed = table_rows(md)
                    for index in range(1, len(parsed)):
                        number, cells = parsed[index]
                        previous_number, previous = parsed[index-1]
                        if number != previous_number+1 or all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                            continue
                        self.assertEqual(len(previous), len(cells))
                    self.assertTrue(all(pattern.search(md) is None for pattern in VERDICTS))


if __name__ == "__main__":
    unittest.main()
