#!/usr/bin/env python3
"""Dispatch observations, plan/actual separation, identity and real exports."""
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
from packing_assistant.post_drafts.dispatch import MISSING, TOOL, _owned, build_draft, parse
from packing_assistant.runtime.scheduler import Scheduler
from packing_assistant.runtime.tool_engine import default_engine


def draft(text):
    return build_draft("dispatch", TOOL, text)


def table(md, title):
    return next(rows for name, rows in tables_from_md(md) if name.startswith(title))


class Dispatch(unittest.TestCase):
    def test_legacy_user_fact_label_is_an_unverified_actual_record_not_a_plan_or_approval(self):
        data = parse("作业面：A区；用户事实：钢筋绑扎已完成；计划：明日进行模板安装；责任人：王工。")
        self.assertEqual("钢筋绑扎已完成", data.faces[0]["actual"])
        self.assertEqual("明日进行模板安装", data.faces[0]["plan"])
        self.assertNotIn("quantity", data.faces[0])
        self.assertEqual("未执行 / 待核", table(draft("作业面：A区；用户事实：钢筋绑扎已完成"), "4 当日")[1][-1])
        future = parse("作业面：A区；用户事实：明日计划安装20块")
        self.assertNotIn("actual", future.faces[0])
        self.assertIn("明日计划安装20块", future.faces[0]["plan"])

    def test_exclusive_dispatch_and_no_country_rate_or_operating_order_default(self):
        md = draft("")
        self.assertEqual(md, dispatch("dispatch", TOOL, ""))
        self.assertIsNone(build_draft("pm-daily", TOOL, ""))
        self.assertIsNone(build_draft("dispatch", "pm-daily__report", ""))
        self.assertEqual(11, len([s for s in md.splitlines() if s.startswith("## ")]))
        for text in ("AI 草稿", "submit_blocked=true", "未下达", "method-hazard", "签认：（空）"):
            self.assertIn(text, md)
        for text in ("CN：", "SG：", "BCA construction", "## 用户原文", "已浇筑完成", "可以开工", "抢回工期", "隐患已消除", "同意复工"):
            self.assertNotIn(text, md)

    def test_every_development_fact_reaches_semantically_correct_business_field(self):
        from scripts.eval_post_content import classify, load, spots, split_echo
        for case in load("cases"):
            if case["post"] != "dispatch":
                continue
            with self.subTest(case=case["id"]):
                md = draft(case["request"])
                body, echo = split_echo(md)
                for fact in case["facts"]:
                    self.assertEqual("placed", classify(fact, body, echo, spots(body)), fact)
                self.assertTrue(all(t not in md for t in case.get("forbidden", [])))

    def test_partial_pour_approx_quantity_does_not_become_finished_face(self):
        data = parse("今天9月18日白班，3#楼十二层顶板C35混凝土7:30开泵，到收工浇了约246方还没收面，泥工班上了34人、汽车泵一台。")
        self.assertEqual(1, len(data.faces))
        row = data.faces[0]
        self.assertEqual(("9月18日", "白班", "3#楼十二层顶板", "7:30", "约246方"), tuple(row[k] for k in ("date", "shift", "face", "start", "quantity")))
        self.assertIn("还没收面", row["actual"])
        self.assertNotIn("finish", row)
        self.assertEqual("34人", data.labor[0]["people"])
        self.assertEqual("一台", data.plant[0]["machine_count"])
        self.assertEqual("未执行 / 待核", table(draft("作业面：3#楼\n今日完成：浇了约246方还没收面"), "4 当日")[1][-1])

    def test_directive_separates_issuer_receiver_deadline_and_unissued_status(self):
        text = "作业面：3#楼\n指令：生产经理周建国要求钢筋班长刘海涛9月19日中午前把十三层墙柱插筋补齐"
        data = parse(text)
        action = data.actions[0]
        self.assertEqual(("生产经理周建国", "钢筋班长刘海涛", "9月19日中午前"), tuple(action[k] for k in ("issuer", "receiver", "deadline")))
        self.assertNotIn("owner", action)
        rows = table(draft(text), "6 指令")
        self.assertEqual(["草稿 · 待责任人核准 · 未下达", "（空）"], rows[1][-2:])

    def test_issuer_is_not_inferred_as_missing_receiver_or_action_owner(self):
        action = parse("作业面：5#楼\n杨工发话周五前必须拼完").actions[0]
        self.assertEqual("杨工", action["issuer"])
        self.assertEqual("周五前", action["deadline"])
        self.assertNotIn("receiver", action)
        self.assertNotIn("owner", action)

    def test_requested_restoration_is_not_recorded_as_restored(self):
        data = parse("作业面：地下室B2层4区底板\n停工：22:40停电，原因配电房跳闸，底板钢筋绑扎中断\n指令：项目副经理陈立新口头要求机电分包赵鹏次日8点前恢复供电")
        self.assertEqual(1, len(data.stops))
        stop = data.stops[0]
        self.assertEqual(("22:40停电", "配电房跳闸", "底板钢筋绑扎中断"), tuple(stop[k] for k in ("stop", "stop_reason", "stop_impact")))
        self.assertNotIn("restoration", stop)
        self.assertEqual("次日8点前", data.actions[0]["deadline"])
        self.assertFalse(data.faces[0].get("quantity"))

    def test_multiple_faces_never_share_labor_equipment_materials_or_orders(self):
        data = parse("5#楼八层铝模拼装，木工上了41个人，塔吊TC6013上午趴窝俩小时吊不了料，杨工发话周五前必须拼完；2#楼外架才搭到十五层，架子工只来了17个，钢管扣件还差一车没到，这边谁盯着还没定")
        labor = _owned(data, data.labor)
        self.assertEqual([("5#楼八层", "41个人"), ("2#楼外架", "17个")], [(r["face"], r["people"]) for r in labor])
        self.assertEqual("5#楼八层", _owned(data, data.plant)[0]["face"])
        self.assertEqual("TC6013", data.plant[0]["model"])
        self.assertEqual("2#楼外架", _owned(data, data.materials)[0]["face"])
        self.assertEqual("5#楼八层", _owned(data, data.actions)[0]["face"])
        self.assertTrue(all("quantity" not in f for f in data.faces))

    def test_planned_work_and_following_resource_clauses_stay_planned(self):
        data = parse("作业面：3#楼\n明日安排：计划浇筑35方\n木工上了34人\n机械：塔吊TC6013\n材料：钢筋\n今日完成：浇了约20方\n泥工上了12人")
        self.assertEqual("约20方", data.faces[0]["quantity"])
        self.assertIn("35方", data.faces[0]["next"])
        self.assertEqual([True, False], [r["_planned"] for r in data.labor])
        self.assertTrue(data.plant[0]["_planned"])
        self.assertTrue(data.materials[0]["_planned"])

    def test_future_actual_label_value_becomes_plan_not_actual_quantity(self):
        row = parse("作业面：3#楼\n今日完成：预计明天浇筑35方").faces[0]
        self.assertNotIn("actual", row)
        self.assertNotIn("quantity", row)
        self.assertIn("35方", row["plan"])
        self.assertEqual("35方", row["plan_quantity"])

    def test_explicit_actual_table_column_is_not_overridden_by_earlier_next_day_heading(self):
        data = parse("明日安排：计划浇筑35方\n| 作业面 | 今日完成 | 工种 | 人数 |\n| --- | --- | --- | --- |\n"
                     "| 3#楼 | 浇了约20方 | 泥工 | 12人 |\n| 5#楼 | 预计明天浇筑40方 | 木工 | 34人 |")
        self.assertEqual("约20方", data.faces[1]["quantity"])
        self.assertEqual([False, True], [r["_planned"] for r in data.labor])
        self.assertNotIn("quantity", data.faces[2])
        self.assertEqual("40方", data.faces[2]["plan_quantity"])

    def test_needed_people_are_proposed_not_reported_as_present(self):
        data = parse("作业面：3#楼\n需要木工34人")
        self.assertTrue(data.labor[0]["_planned"])
        self.assertEqual("34人", data.labor[0]["people"])
        self.assertIn("非实到", _owned(data, data.labor)[0]["_nature"])

    def test_capacity_absence_and_missing_labor_are_records_not_attendance(self):
        for text in ("木工班定员34人", "木工请假34人", "木工缺勤34人", "木工最多34人", "木工未到34人", "木工缺了34人"):
            with self.subTest(text=text):
                row = parse("作业面：3#楼\n"+text).labor[0]
                self.assertNotIn("people", row)
                self.assertEqual(text, row["labor_record"])

    def test_forecast_weather_does_not_replace_observation_or_plan_actual_labor(self):
        data = parse("天气：小雨\n天气预报：预计明天晴\n作业面：3#楼\n木工上了34人")
        self.assertEqual("小雨", data.faces[0]["weather"])
        self.assertEqual("预计明天晴", data.faces[0]["forecast_weather"])
        self.assertFalse(data.labor[0]["_planned"])
        rows = table(draft("天气：预计小雨"), "1 报头")
        self.assertEqual(MISSING, rows[1][rows[0].index("用户天气记录")])
        self.assertEqual("预计小雨", rows[1][rows[0].index("用户天气预报（非实况）")])

    def test_weather_never_comes_from_names_rainwater_pipes_or_covering_materials(self):
        for text in ("今天经理刘晴要求周五前记录", "雨水管进场", "雨棚材料已到", "工人雨衣30件"):
            with self.subTest(text=text):
                data = parse(text)
                self.assertTrue(all(r.get("weather", MISSING) == MISSING for r in data.faces+data.contexts))

    def test_next_day_clause_does_not_rewrite_dispatch_header_date(self):
        data = parse("日期：2026-09-18\n作业面：3#楼\n明天木工上了34人")
        self.assertEqual("2026-09-18", data.faces[0]["date"])
        self.assertTrue(data.labor[0]["_planned"])

    def test_plan_and_actual_restoration_records_are_separate_without_permit(self):
        text = "作业面：3#楼\n停工：断电\n用户恢复记录：预计明天恢复供电"
        stop = parse(text).stops[0]
        self.assertNotIn("restoration", stop)
        self.assertEqual("预计明天恢复供电", stop["restart_plan"])
        rows = table(draft(text), "8 停复工")
        self.assertEqual("未执行 · 未签发许可", rows[1][-1])
        stop = parse("作业面：3#楼\n用户恢复记录：用户说今日供电恢复").stops[0]
        self.assertIn("今日供电恢复", stop["restoration"])

    def test_future_action_completion_is_not_actual_implementation(self):
        action = parse("作业面：3#楼\n指令：补齐插筋\n用户落实记录：预计明天补齐").actions[0]
        self.assertNotIn("action_record", action)
        self.assertEqual("预计明天补齐", action["action_plan"])

    def test_new_face_does_not_borrow_child_records_or_personal_responsibility(self):
        data = parse("作业面：3#楼\n工种：木工\n人数：34人\n指令：補齐\n责任人：甲班\n作业面：5#楼\n人数：17人\n期限：周五前")
        labor = _owned(data, data.labor)
        self.assertEqual("17人", labor[1]["people"])
        self.assertNotIn("labor", data.labor[1])
        self.assertEqual("5#楼", _owned(data, data.actions)[1]["face"])
        self.assertNotIn("owner", data.actions[1])

    def test_file_and_repeated_filename_boundary_reset_scope_and_children(self):
        data = parse("### A.txt\n辖区：CN\n日期：9月18日\n作业面：3#楼\n机械：塔吊TC6013\n台数：一台\n### A.txt\n作业面：5#楼\n台数：两台")
        records = _owned(data, data.plant)
        self.assertEqual(["CN", "UNSPECIFIED"], [r["jurisdiction"] for r in records])
        self.assertEqual(MISSING, records[1]["date"])
        self.assertNotIn("machine", data.plant[1])
        self.assertNotIn("model", data.plant[1])

    def test_context_changes_do_not_reuse_the_previous_face_or_children(self):
        for label, first, second, key in (("日期", "9月18日", "9月19日", "date"), ("班次", "白班", "夜班", "shift"),
                                         ("辖区", "CN", "SG", "jurisdiction"), ("项目", "甲", "乙", "project")):
            with self.subTest(label=label):
                data = parse(label+"："+first+"\n作业面：3#楼\n人数：34人\n"+label+"："+second+"\n人数：17人")
                self.assertEqual(2, len(data.faces))
                self.assertEqual([first, second], [r[key] for r in data.faces])
                self.assertNotIn("face", data.faces[1])

    def test_unknown_blank_table_scope_does_not_mask_conflicting_header(self):
        data = parse("日期：9月18日\n| 作业面 | 日期 | 人数 |\n| --- | --- | --- |\n| 3#楼 | | 34人 |\n\n日期：9月19日\n人数：17人")
        self.assertEqual(2, len(data.faces))
        self.assertNotIn("face", data.faces[1])
        self.assertEqual("9月19日", data.faces[1]["date"])

    def test_new_machine_model_does_not_borrow_count_fault_or_source(self):
        data = parse("作业面：3#楼\n机械：塔吊TC6013\n台数：一台\n机械状态：故障\n机械来源：A.csv\n型号：TC7015")
        self.assertEqual(2, len(data.plant))
        self.assertEqual(["TC6013", "TC7015"], [r["model"] for r in data.plant])
        self.assertEqual("塔吊", data.plant[1]["machine"])
        for key in ("machine_count", "machine_record", "machine_source"):
            self.assertNotIn(key, data.plant[1])

    def test_repeated_counts_keep_their_independent_source_records_without_sum(self):
        data = parse("作业面：3#楼\n工种：木工\n人数：34人\n出勤来源：A.csv\n人数：17人\n出勤来源：B.csv")
        self.assertEqual([("34人", "A.csv"), ("17人", "B.csv")], [(r["people"], r["labor_source"]) for r in data.labor])
        self.assertTrue(all(r["labor"] == "木工" for r in data.labor))
        self.assertNotIn("51人", draft("作业面：3#楼\n工种：木工\n人数：34人\n人数：17人"))

    def test_late_labor_machine_and_material_names_fill_only_their_existing_unknown_identity(self):
        data = parse("作业面：3#楼\n人数：34人\n出勤来源：L.csv\n工种：木工\n台数：一台\n机械来源：P.csv\n机械：塔吊TC6013\n材料数量：35吨\n材料来源：M.csv\n材料：螺纹钢")
        self.assertEqual(1, len(data.labor))
        self.assertEqual(("木工", "34人", "L.csv"), tuple(data.labor[0][k] for k in ("labor", "people", "labor_source")))
        self.assertEqual(1, len(data.plant))
        self.assertEqual(("塔吊", "TC6013", "一台", "P.csv"), tuple(data.plant[0][k] for k in ("machine", "model", "machine_count", "machine_source")))
        self.assertEqual(1, len(data.materials))
        self.assertEqual(("螺纹钢", "35吨", "M.csv"), tuple(data.materials[0][k] for k in ("material", "material_quantity", "material_source")))

    def test_late_instruction_content_does_not_discard_explicit_issuer_receiver_and_source(self):
        data = parse("作业面：3#楼\n下达人：甲经理\n接收人：乙班\n指令凭证：I.pdf\n指令：补齐插筋")
        self.assertEqual(1, len(data.actions))
        self.assertEqual(("甲经理", "乙班", "I.pdf", "补齐插筋"), tuple(data.actions[0][k] for k in ("issuer", "receiver", "order_source", "instruction")))

    def test_unique_face_id_reference_returns_only_to_its_owned_face(self):
        data = parse("作业面：3#楼\n记录编号：A\n人数：34人\n作业面：5#楼\n记录编号：B\n人数：17人\n记录编号：A\n机械：塔吊TC6013")
        self.assertEqual(2, len(data.faces))
        self.assertEqual("3#楼", _owned(data, data.plant)[0]["face"])

    def test_duplicate_face_id_reference_without_identity_is_unassigned(self):
        data = parse("作业面：3#楼\n记录编号：DUP\n作业面：5#楼\n记录编号：DUP\n记录编号：DUP\n人数：17人")
        self.assertEqual(3, len(data.faces))
        self.assertNotIn("face", data.faces[-1])
        self.assertEqual(MISSING, _owned(data, data.labor)[0].get("face", MISSING))

    def test_blank_markdown_row_and_tail_notes_never_use_previous_face(self):
        data = parse("| 作业面 | 人数 |\n| --- | --- |\n| 3#楼 | 34人 |\n| | |\n\n机械：塔吊TC6013")
        self.assertEqual(3, len(data.faces))
        self.assertEqual(MISSING, data.faces[1]["face"])
        self.assertNotIn("face", data.faces[-1])
        self.assertEqual(MISSING, _owned(data, data.plant)[0].get("face", MISSING))

    def test_reordered_table_fields_keep_each_role_source_in_its_own_face(self):
        data = parse("| 指令凭证 | 机械来源 | 出勤来源 | 作业面 | 指令 | 机械 | 工种 | 人数 |\n| --- | --- | --- | --- | --- | --- | --- | --- |\n"
                     "| I.pdf | P.csv | L.csv | 3#楼 | 补齐插筋 | 塔吊TC6013 | 木工 | 34人 |\n"
                     "| | | | 5#楼 | 复核缺项 | | | 17人 |")
        self.assertEqual("I.pdf", data.actions[0]["order_source"])
        self.assertEqual("P.csv", data.plant[0]["machine_source"])
        self.assertEqual("L.csv", data.labor[0]["labor_source"])
        for records, key in ((data.actions, "order_source"), (data.plant, "machine_source"), (data.labor, "labor_source")):
            self.assertEqual(MISSING, records[1][key])

    def test_general_source_is_not_assigned_to_labor_machine_order_or_actual(self):
        data = parse("作业面：3#楼\n来源：A.pdf\n人数：34人\n机械：塔吊TC6013\n指令：补齐")
        self.assertEqual("A.pdf", data.faces[0]["source"])
        self.assertNotIn("labor_source", data.labor[0])
        self.assertNotIn("machine_source", data.plant[0])
        self.assertNotIn("order_source", data.actions[0])

    def test_models_codes_and_dates_do_not_become_quantities_people_or_machines(self):
        for label, key, kind in (("完成量", "quantity", "faces"), ("人数", "people", "labor"), ("台数", "machine_count", "plant")):
            for value in ("TC6013", "HRB400", "C35", "EQ-12", "2026-09-18", "V2.0", "34人？", "不是34", "例如34人"):
                with self.subTest(label=label, value=value):
                    data = parse("作业面：3#楼\n"+label+"："+value)
                    self.assertTrue(all(r.get(key, MISSING) == MISSING for r in getattr(data, kind)))
        data = parse("机械：塔吊TC6013")
        self.assertEqual("TC6013", data.plant[0]["model"])
        self.assertNotIn("machine_count", data.plant[0])

    def test_negated_unknown_or_question_machine_cannot_leak_model(self):
        for value in ("不是塔吊TC6013", "塔吊TC6013？", "型号未提供TC6013"):
            with self.subTest(value=value):
                data = parse("作业面：3#楼\n机械："+value)
                self.assertTrue(all(r.get("model", MISSING) == MISSING for r in data.plant))

    def test_relative_dates_and_deadlines_are_not_resolved_or_invented(self):
        data = parse("日期：今天\n作业面：3#楼\n指令：补齐\n期限：周五前")
        self.assertEqual("今天", data.faces[0]["date"])
        self.assertEqual("周五前", data.actions[0]["deadline"])
        for value in ("ORDER-20260919", "V2.0", "不是9月19日", "9月19日？"):
            self.assertEqual(MISSING, parse("作业面：3#楼\n期限："+value).actions[0]["deadline"])

    def test_explicit_actual_labels_and_english_roles_keep_sources_and_missing_values(self):
        data = parse("Project: Synthetic A\nJurisdiction: SG\nDate: 2026-09-18\nWorkface: Zone A\nActual record: installed 20 panels\nWorkers: 8\nCrew: Team A\nInstruction: inspect joint\nIssuer: Manager A\nReceiver: Team B\nInstruction source: I.pdf")
        self.assertEqual("Zone A", data.faces[0]["face"])
        self.assertEqual("installed 20 panels", data.faces[0]["actual"])
        self.assertEqual("8", data.labor[0]["people"])
        self.assertEqual("Team A", data.labor[0]["labor"])
        self.assertEqual(("Manager A", "Team B", "I.pdf"), tuple(data.actions[0][k] for k in ("issuer", "receiver", "order_source")))
        self.assertNotIn("quantity", data.faces[0])

    def test_sensitive_names_and_handover_are_not_permits_or_completion(self):
        md = draft("写一份调度日报 临边防护 白班\n工作面交接：用户说已交接\n敏感作业时段：夜班\n验收资料：AC-A.pdf")
        self.assertIn("临边防护", md)
        self.assertIn("method-hazard", md)
        self.assertEqual("未执行", table(md, "7 交叉")[1][-1])
        self.assertEqual("未执行", table(md, "9 危大")[1][-1])
        self.assertEqual("未执行", table(md, "11 附件")[1][-1])

    def test_examples_are_masked_and_explicit_actual_reset_preserved(self):
        data = parse("例如3#楼浇了35方，木工34人\n实际情况：作业面：5#楼\n今日完成：铺贴约730平米")
        self.assertEqual(1, len(data.faces))
        self.assertEqual("5#楼", data.faces[0]["face"])
        self.assertEqual("约730平米", data.faces[0]["quantity"])
        self.assertNotIn("34人", repr(data))

    def test_evidence_keeps_site_face_separate_from_physical_input_line(self):
        data = parse("### A.txt\n作业面：3#楼\n人数：34人")
        evidence = _owned(data, data.evidence)[-1]
        self.assertEqual("3#楼", evidence["face"])
        self.assertEqual("A.txt · 本轮输入第3行（非原件页码）", evidence["input_position"])
        self.assertEqual("A.txt", evidence["_origin"])


class DispatchRuntime(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="dispatch-regression-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for context in (patch.object(expert_turn, "_OUT", self.root), patch("packing_assistant.runtime.memory._OUT", self.root),
                        patch("packing_assistant.runtime.scheduler.get_scheduler", return_value=Scheduler()),
                        patch.dict(os.environ, {"CIVIL_JOB_ROOT": "", "CIVIL_SANDBOX_ROOTS": str(self.root), "CIVIL_SANDBOX": "workspace-write", "CIVIL_APPROVAL": "on-request"})):
            context.start()
            self.addCleanup(context.stop)

    def test_actual_word_excel_and_markdown_tables_match_and_neutralize_formula_values(self):
        from xml.etree import ElementTree as ET
        from zipfile import ZipFile
        import openpyxl
        result = default_engine().execute(TOOL, {"text": "作业面：=Zone|A\n今日完成：浇了35方\n工种：A & B\n人数：34人", "session_id": "export-dispatch"}, expert_id="dispatch", intent="run")
        self.assertTrue(result["ok"] and result["wrote"] and result["submit_blocked"], result)
        paths = {Path(f["path"]).suffix: Path(f["path"]) for f in result["files"]}
        self.assertTrue({".md", ".docx", ".xlsx"}.issubset(paths), paths)
        expected = table(paths[".md"].read_text(encoding="utf-8"), "4 当日")
        book = openpyxl.load_workbook(paths[".xlsx"])
        try:
            sheet = next(book[n] for n in book.sheetnames if n.startswith("4 当日"))
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

    def test_questions_and_readonly_paths_cannot_write_or_issue(self):
        chat = expert_turn.run_expert_turn("调度指令和日报怎么区分？", "dispatch", session_id="chat-dispatch")
        self.assertFalse(chat["wrote"])
        with patch.dict(os.environ, {"CIVIL_SANDBOX": "read-only"}):
            denied = expert_turn.run_named_exclusive(TOOL, {"text": "写调度日报", "session_id": "ro-dispatch"})
        self.assertFalse(denied["wrote"])
        self.assertFalse(list(self.root.rglob("*.md")))

    def test_hostile_inputs_survive_without_ragged_tables_or_operating_verdicts(self):
        from scripts.test_post_robustness import INPUTS, VERDICTS, table_rows
        import re
        for name, source in INPUTS.items():
            with self.subTest(input=name):
                result = expert_turn.run_named_exclusive(TOOL, {"text": source, "session_id": "hostile-dispatch"})
                self.assertTrue(result["wrote"] and result["submit_blocked"], result)
                md = next(Path(f["path"]) for f in result["files"] if f["path"].endswith(".md")).read_text(encoding="utf-8")
                parsed = table_rows(md)
                for i in range(1, len(parsed)):
                    line, cells = parsed[i]
                    previous_line, previous = parsed[i-1]
                    if line != previous_line+1 or all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                        continue
                    self.assertEqual(len(previous), len(cells))
                self.assertTrue(all(p.search(md) is None for p in VERDICTS))


if __name__ == "__main__":
    unittest.main()
