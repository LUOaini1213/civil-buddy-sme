#!/usr/bin/env python3
"""Safety briefing literal ownership, unsigned outputs and confirmation gates."""
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
from packing_assistant.post_drafts import build_draft as dedicated
from packing_assistant.post_drafts.safety_brief import MISSING, TOOL, _owned, build_draft, parse
from packing_assistant.runtime.scheduler import Scheduler


def draft(text):
    return build_draft("safety-brief", TOOL, text)


def table(md, prefix):
    return next(rows for title, rows in tables_from_md(md) if title.startswith(prefix))


class SafetyBrief(unittest.TestCase):
    def test_exclusive_eleven_sections_no_default_hazards_measures_laws_or_phone(self):
        md = draft("")
        self.assertEqual(md, dedicated("safety-brief", TOOL, ""))
        self.assertIsNone(build_draft("worker-brief", TOOL, ""))
        self.assertIsNone(build_draft("safety-brief", "method-hazard__identify", ""))
        self.assertEqual(11, len([line for line in md.splitlines() if line.startswith("## ")]))
        for text in ("AI 草稿", "内部讨论", "submit_blocked=true", "电话", "未执行", "（空）"):
            self.assertIn(text, md)
        for text in ("临边坠落", "触电先断电", "WSH", "BCA", "CN：", "SG：", "## 用户原文", "120", "995", "已交底完毕", "签认完毕", "可以施工"):
            self.assertNotIn(text, md)

    def test_every_development_fact_reaches_its_own_semantic_field(self):
        from scripts.eval_post_content import classify, load, spots, split_echo
        for case in load("cases"):
            if case["post"] != "safety-brief":
                continue
            with self.subTest(case=case["id"]):
                md = draft(case["request"])
                body, echo = split_echo(md)
                for fact in case["facts"]:
                    self.assertEqual("placed", classify(fact, body, echo, spots(body)), fact)
                self.assertTrue(all(t not in md for t in case.get("forbidden", [])))

    def test_sentence_separates_workface_parameters_user_hazard_and_contact(self):
        data = parse("明天给架子班交底，4#楼东立面搭悬挑脚手架，挑梁用16号工字钢，挑梁间距1.5米，班组自己提的危险点是临边坠落，现场安全员周斌全程盯着，六级以上大风就停")
        self.assertEqual(1, len(data.works))
        self.assertEqual(("明天", "4#楼东立面", "悬挑脚手架", "架子班"), tuple(data.works[0][k] for k in ("date", "location", "process", "crew")))
        self.assertEqual(("16号工字钢", "1.5米"), tuple(data.parameters[0][k] for k in ("spec", "spacing")))
        self.assertEqual(("临边坠落", "六级以上大风"), tuple(data.hazards[0][k] for k in ("hazard", "stop")))
        self.assertEqual(("安全员", "周斌"), tuple(data.contacts[0][k] for k in ("contact_role", "contact")))
        self.assertNotIn("phone", data.contacts[0])

    def test_ventilation_time_is_a_user_measure_not_detection_result_or_permit(self):
        text = "工序：B2层西南角集水坑有限空间清淤\n班组：土建班，监护人赵国庆\n危险源：中毒缺氧\n措施：作业前通风30分钟，进前测氧气和硫化氢\n喊停条件：检测超标或通风中断立即撤出"
        data = parse(text)
        self.assertEqual(("B2层西南角集水坑", "有限空间清淤"), tuple(data.works[0][k] for k in ("location", "process")))
        self.assertEqual("赵国庆", data.contacts[0]["contact"])
        self.assertTrue(any("通风30分钟" in r.get("measures", "") for r in data.hazards))
        self.assertTrue(any("进前测氧气和硫化氢" in r.get("measures", "") for r in data.hazards))
        self.assertTrue(all("user_record" not in r for r in data.hazards))
        for row in table(draft(text), "6 防护")[1:]:
            self.assertIn("未执行", row[-1])

    def test_two_faces_keep_crew_height_and_ppe_apart_shared_hazard_unassigned(self):
        data = parse("两个面一块交底：6#楼电梯井口要拆盖板吊料，洞口下面空着3.2米，木工班李海涛带人干；屋面女儿墙外侧打胶得挂安全带，吊篮那个还没定班组。危险点我就想到洞口坠落和落物")
        self.assertEqual(2, len(data.works))
        first, second = data.works
        self.assertEqual("木工班", first["crew"])
        self.assertNotIn("crew", second)
        self.assertNotIn("ppe", first)
        self.assertEqual("安全带", second["ppe"])
        self.assertEqual("6#楼电梯井口", _owned(data, data.parameters)[0]["location"])
        self.assertEqual("3.2米", data.parameters[0]["height"])
        self.assertEqual("6#楼电梯井口", _owned(data, data.contacts)[0]["location"])
        unassigned = next(r for r in _owned(data, data.hazards) if "洞口坠落" in r.get("hazard", ""))
        self.assertNotIn("location", unassigned)
        evidence = next(r for r in _owned(data, data.evidence) if "洞口坠落" in r.get("value", ""))
        self.assertNotIn("location", evidence)

    def test_new_face_never_borrows_hazard_contact_measure_or_parameter_source(self):
        data = parse("作业部位：3#楼\n工序：拆盖板\n班组：甲班\n危险点：坠落\n措施：用户措施A\n交底措施来源：H-A.pdf\n监护人：张三\n电话：13800000000\n参数来源：P-A.pdf\n高度：3.2米\n作业部位：5#楼\n工序：吊料\n危险点：碰撞\n高度：4.1米")
        hazards = _owned(data, data.hazards)
        self.assertEqual(["3#楼", "5#楼"], [r["location"] for r in hazards])
        for key in ("measures", "hazard_source"):
            self.assertNotIn(key, hazards[1])
        self.assertNotIn("parameter_source", data.parameters[1])
        self.assertEqual(1, len(data.contacts))

    def test_new_step_does_not_borrow_previous_step_hazard_measures_or_crew(self):
        data = parse("作业部位：3#楼\n作业步骤：拆盖板\n班组：甲班\n危险点：坠落\n措施：用户措施A\n步骤：吊料\n危险点：落物")
        hazards = _owned(data, data.hazards)
        self.assertEqual(["拆盖板", "吊料"], [r["_step"] for r in hazards])
        self.assertNotIn("measures", hazards[1])
        self.assertNotIn("crew", data.works[1])
        self.assertEqual("3#楼", data.works[1]["location"])

    def test_late_step_and_workface_own_their_initial_hazard_or_contact_only(self):
        data = parse("危险点：落物\n联系人：张三\n电话：13800000000\n作业步骤：吊料\n作业部位：3#楼")
        self.assertEqual(1, len(data.works))
        hazard = _owned(data, data.hazards)[0]
        self.assertEqual(("3#楼", "吊料"), (hazard["location"], hazard["_step"]))
        self.assertEqual("3#楼", _owned(data, data.contacts)[0]["location"])

    def test_changed_contact_role_drops_previous_name_phone_and_source(self):
        data = parse("作业部位：3#楼\n监护人：张三\n电话：13800000000\n联系人来源：C-A.csv\n安全员：李四")
        self.assertEqual(2, len(data.contacts))
        first, second = data.contacts
        self.assertEqual(("监护人", "张三"), (first["contact_role"], first["contact"]))
        self.assertEqual(("安全员", "李四"), (second["contact_role"], second["contact"]))
        self.assertNotIn("phone", second)
        self.assertNotIn("contact_source", second)

    def test_many_contacts_in_one_reordered_table_row_are_independent_roles(self):
        data = parse("| 电话 | 作业部位 | 监护人 | 安全员 | 班组长 |\n| --- | --- | --- | --- | --- |\n| 13800000000 | 3#楼 | 张三 | 李四 | 王五 |")
        self.assertEqual([(r["contact_role"], r["contact"]) for r in data.contacts[1:]], [("监护人", "张三"), ("安全员", "李四"), ("带班 / 班组长", "王五")])
        self.assertEqual("13800000000", data.contacts[0]["phone"])
        self.assertNotIn("contact", data.contacts[0])
        self.assertTrue(all("phone" not in r for r in data.contacts[1:]))

    def test_qualified_phones_attach_only_to_unique_same_face_role_despite_column_order(self):
        data = parse("| 作业部位 | 监护人 | 安全员 | 安全员电话 | 监护人电话 |\n| --- | --- | --- | --- | --- |\n| 3#楼 | 张三 | 李四 | 13900000000 | 13800000000 |")
        self.assertEqual(2, len(data.contacts))
        self.assertEqual({r["contact"]: r["phone"] for r in data.contacts}, {"张三": "13800000000", "李四": "13900000000"})

    def test_qualified_phone_cannot_choose_one_of_duplicate_same_face_roles(self):
        data = parse("作业部位：3#楼\n监护人：张三\n监护人：李四\n监护人电话：13800000000")
        self.assertEqual(3, len(data.contacts))
        self.assertTrue(all("phone" not in r for r in data.contacts[:2]))
        self.assertNotIn("contact", data.contacts[2])

    def test_repeated_qualified_phone_preserves_separate_values_without_borrowing_source(self):
        data = parse("作业部位：3#楼\n监护人：张三\n监护人电话：13800000000\n联系人来源：C-A.csv\n监护人电话：13900000000")
        self.assertEqual(2, len(data.contacts))
        self.assertEqual(["13800000000", "13900000000"], [r["phone"] for r in data.contacts])
        self.assertEqual("张三", data.contacts[1]["contact"])
        self.assertNotIn("contact_source", data.contacts[1])

    def test_qualified_phone_does_not_borrow_a_name_from_another_face(self):
        data = parse("作业部位：3#楼\n监护人：张三\n作业部位：5#楼\n监护人电话：13800000000")
        self.assertEqual(2, len(data.contacts))
        self.assertNotIn("phone", data.contacts[0])
        self.assertNotIn("contact", data.contacts[1])
        self.assertEqual("5#楼", _owned(data, data.contacts)[1]["location"])

    def test_reordered_table_children_use_the_explicit_step_not_column_position(self):
        data = parse("| 措施 | 危险点 | 监护人 | 高度 | 参数来源 | 步骤 | 部位 |\n| --- | --- | --- | --- | --- | --- | --- |\n| A | 落物 | 张三 | 3.2米 | P-A.pdf | 吊料 | 3#楼 |\n| B | 碰撞 | 李四 | 4.1米 | | 拆盖板 | 5#楼 |")
        self.assertEqual(["吊料", "拆盖板"], [r["_step"] for r in data.hazards])
        self.assertEqual(["3#楼", "5#楼"], [r["location"] for r in _owned(data, data.contacts)])
        self.assertEqual(MISSING, data.parameters[1]["parameter_source"])

    def test_repeated_same_named_face_starts_fresh_children_not_a_continuation(self):
        data = parse("作业部位：3#楼\n工序：吊料\n危险点：落物\n措施：A\n联系人：张三\n电话：13800000000\n作业部位：3#楼\n危险点：碰撞\n联系人：李四")
        self.assertEqual(2, len(data.works))
        self.assertNotIn("measures", data.hazards[1])
        self.assertNotIn("phone", data.contacts[1])

    def test_late_contact_name_or_material_identity_retains_only_its_initial_fields(self):
        data = parse("电话：13800000000\n联系人来源：C-A.csv\n监护人：张三\n间距：1.5米\n参数来源：P-A.pdf\n材料：挑梁\n规格：16号工字钢")
        self.assertEqual(1, len(data.contacts))
        self.assertEqual(("13800000000", "C-A.csv", "张三"), tuple(data.contacts[0][k] for k in ("phone", "contact_source", "contact")))
        self.assertEqual(1, len(data.parameters))
        self.assertEqual(("1.5米", "P-A.pdf", "挑梁", "16号工字钢"), tuple(data.parameters[0][k] for k in ("spacing", "parameter_source", "material", "spec")))

    def test_new_material_never_borrows_spacing_load_or_parameter_source(self):
        data = parse("材料：挑梁\n规格：16号工字钢\n间距：1.5米\n参数来源：P-A.pdf\n材料：安全带\n规格：用户型号B")
        self.assertEqual(2, len(data.parameters))
        self.assertNotIn("spacing", data.parameters[1])
        self.assertNotIn("parameter_source", data.parameters[1])

    def test_new_hazard_does_not_borrow_measure_stop_or_basis(self):
        data = parse("工序：吊料\n危险点：落物\n措施：A\n措施依据：A.pdf\n喊停条件：用户条件A\n危险点：碰撞")
        self.assertEqual(2, len(data.hazards))
        self.assertEqual("吊料", data.hazards[1]["_step"])
        self.assertTrue(all(k not in data.hazards[1] for k in ("measures", "measure_basis", "stop")))

    def test_generic_source_not_assigned_to_parameters_contacts_scheme_or_hazard(self):
        data = parse("来源：generic.pdf\n工序：吊料\n材料：挑梁\n间距：1.5米\n危险点：落物\n联系人：张三")
        self.assertEqual("generic.pdf", data.works[0]["source"])
        self.assertNotIn("parameter_source", data.parameters[0])
        self.assertNotIn("hazard_source", data.hazards[0])
        self.assertNotIn("contact_source", data.contacts[0])
        self.assertNotIn("scheme_source", data.works[0])

    def test_parameters_keep_explicit_units_and_never_default_or_convert(self):
        data = parse("间距：1500mm\n高度：3.2\n荷载：2.0kN/m2")
        self.assertEqual(("1500mm", "3.2", "2.0kN/m2"), tuple(data.parameters[0][k] for k in ("spacing", "height", "load")))
        self.assertNotIn("3.2米", draft("高度：3.2"))

    def test_models_dates_questions_and_negations_are_not_dimension_or_phone(self):
        for key in ("间距", "高度", "荷载"):
            for value in ("HRB400", "2026-09-22", "不是3.2米", "约多少米？", "待核", "例如1.5米"):
                with self.subTest(key=key, value=value):
                    data = parse(key+"："+value)
                    field = {"间距": "spacing", "高度": "height", "荷载": "load"}[key]
                    self.assertTrue(all(row.get(field) == MISSING for row in data.parameters))
        for value in ("TC6013", "号码未定", "是否13800000000？", "2026-09-22"):
            self.assertEqual(MISSING, parse("电话："+value).contacts[0]["phone"])

    def test_relative_briefing_date_is_not_resolved_to_today_or_signed(self):
        data = parse("交底日期：明天\n交底人：张三\n接受人：李四\n作业部位：3#楼")
        self.assertEqual("明天", data.works[0]["date"])
        md = draft("交底日期：明天\n交底人：张三\n接受人：李四")
        signature = table(md, "11 签字")
        self.assertEqual(["（空）"]*4+["未执行 · 未签发"], signature[1])
        self.assertNotIn("张三", "".join(signature[1]))

    def test_future_or_hearsay_implementation_goes_to_plan_not_actual_record(self):
        data = parse("工序：通风\n危险点：缺氧\n用户实施记录：预计明天完成通风检测\n用户实施记录：据说昨天测过\n用户实施记录：用户称今天已通风20分钟")
        self.assertTrue(all("user_record" not in r for r in data.hazards[:2]))
        self.assertTrue(all("planned_record" in r for r in data.hazards[:2]))
        self.assertEqual("用户称今天已通风20分钟", data.hazards[-1]["user_record"])
        self.assertIn("未执行", table(draft("用户实施记录：用户称今天已通风20分钟"), "6 防护")[1][-1])

    def test_named_scheme_standard_and_clause_remain_unverified_not_authority(self):
        md = draft("专项方案：方案A\n方案来源：scheme-A.pdf\n规范：用户规范甲2026\n条款号：4.2\n规范来源：standard-A.pdf\n用户图号：D-A01")
        values = table(md, "10 依据")[1]
        self.assertIn("用户规范甲2026", values)
        self.assertIn("4.2", values)
        self.assertEqual("unverified / UNSPECIFIED", values[-1])
        self.assertIn("D-A01", table(md, "4 作业")[1])

    def test_repeated_source_filename_starts_an_independent_file_epoch(self):
        for second in ("b.md", "a.md"):
            with self.subTest(second=second):
                data = parse(f"### a.md\n作业部位：3#楼\n危险点：落物\n措施：用户A\n联系人：张三\n电话：13800000000\n### {second}\n危险点：碰撞\n联系人：李四")
                self.assertEqual(2, len(data.works))
                self.assertNotIn("location", _owned(data, data.hazards)[1])
                self.assertNotIn("measures", data.hazards[1])
                self.assertNotIn("phone", data.contacts[1])

    def test_changed_project_date_jurisdiction_or_version_resets_work_and_children(self):
        for key, first, second in (("日期", "2026-09-22", "2026-09-23"), ("项目", "甲", "乙"), ("辖区", "SG", "CN"), ("版本", "V1", "V2")):
            with self.subTest(key=key):
                data = parse(f"{key}：{first}\n作业部位：3#楼\n危险点：落物\n措施：A\n联系人：张三\n电话：13800000000\n{key}：{second}\n危险点：碰撞\n联系人：李四")
                self.assertNotIn("location", data.works[1])
                self.assertNotIn("measures", data.hazards[1])
                self.assertNotIn("phone", data.contacts[1])

    def test_blank_table_identities_and_table_tail_do_not_borrow_previous_location(self):
        data = parse("| 作业部位 | 工序 | 危险点 | 措施 | 监护人 | 电话 |\n| --- | --- | --- | --- | --- | --- |\n| 3#楼 | 吊料 | 落物 | A | 张三 | 13800000000 |\n| | | 碰撞 | | 李四 | |\n| 5#楼 | 通风 | 缺氧 | B | 王五 | |\n\n危险点：火灾")
        owned = _owned(data, data.hazards)
        self.assertEqual([r.get("location") for r in owned], ["3#楼", MISSING, "5#楼", None])
        self.assertNotIn("measures", owned[-1])
        self.assertEqual(MISSING, data.contacts[1]["phone"])
        self.assertEqual(MISSING, data.contacts[2]["phone"])

    def test_unique_work_id_reference_owns_only_that_workface(self):
        data = parse("工作面编号：A\n作业部位：3#楼\n工序：吊料\n危险点：落物\n工作面编号：B\n作业部位：5#楼\n工序：通风\n危险点：缺氧\n工作面编号：A\n联系人：张三")
        self.assertEqual("3#楼", _owned(data, data.contacts)[0]["location"])

    def test_duplicate_work_id_reference_is_unassigned_not_last_matching_work(self):
        data = parse("| 工作面编号 | 作业部位 | 工序 |\n| --- | --- | --- |\n| A | 3#楼 | 吊料 |\n| A | 5#楼 | 通风 |\n\n工作面编号：A\n联系人：张三")
        self.assertNotIn("location", _owned(data, data.contacts)[0])

    def test_english_explicit_fields_keep_own_roles_and_sources(self):
        data = parse("Project: Synthetic A\nJurisdiction: SG\nLocation: Roof A\nProcess: panel handling\nCrew: Crew A\nHazard: user-named collision\nMeasures: user-proposed cordon\nMeasure basis: notes-A.pdf\nMonitor: Alex\nPhone: +65 6000 0000")
        self.assertEqual("Roof A", data.works[0]["location"])
        self.assertEqual("notes-A.pdf", data.hazards[0]["measure_basis"])
        self.assertEqual(("监护人", "Alex", "+65 6000 0000"), tuple(data.contacts[0][k] for k in ("contact_role", "contact", "phone")))

    def test_example_scope_does_not_leak_and_explicit_actual_reset_restores_parsing(self):
        data = parse("例如3#楼高度3.2米，危险点：坠落\n实际情况：\n作业部位：5#楼\n高度：4.1米\n危险点：用户点名碰撞")
        self.assertEqual(1, len(data.works))
        self.assertEqual("5#楼", data.works[0]["location"])
        self.assertEqual("4.1米", data.parameters[0]["height"])
        self.assertEqual("用户点名碰撞", data.hazards[0]["hazard"])

    def test_physical_workface_and_input_line_not_claimed_as_original_page(self):
        data = parse("### a.pdf\n作业部位：3#楼\n高度：3.2米")
        record = next(r for r in _owned(data, data.evidence) if r["field"] == "洞口落差")
        self.assertEqual("3#楼", record["location"])
        self.assertEqual("a.pdf · 本轮输入第3行（非原件页码）", record["input_position"])

    def test_short_legacy_command_keeps_named_process_without_inventing_hazard(self):
        data = parse("写一份安全交底 临边")
        self.assertEqual("临边", data.works[0]["process"])
        self.assertEqual([], data.hazards)


class SafetyRuntime(unittest.TestCase):
    def setUp(self):
        from packing_assistant.runtime import agent_loop
        (ROOT / "output").mkdir(exist_ok=True)
        directory = tempfile.TemporaryDirectory(prefix="safety-brief-test-", dir=ROOT / "output")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        from packing_assistant.runtime import memory
        for module in (expert_turn, agent_loop, memory):
            context = patch.object(module, "_OUT", self.root)
            context.start()
            self.addCleanup(context.stop)
        self.scheduler = Scheduler()
        context = patch("packing_assistant.runtime.scheduler.get_scheduler", return_value=self.scheduler)
        context.start()
        self.addCleanup(context.stop)
        env = patch.dict(os.environ, {"CIVIL_JOB_ROOT": "", "CIVIL_SANDBOX_ROOTS": str(self.root), "CIVIL_SANDBOX": "workspace-write", "CIVIL_APPROVAL": "on-request", "PYTHON_DOTENV_DISABLED": "1"})
        env.start()
        self.addCleanup(env.stop)

    def test_high_risk_gate_questions_and_readonly_paths_do_not_write(self):
        chat = expert_turn.run_expert_turn("安全交底和作业许可怎么区分？", "safety-brief", session_id="chat-safety")
        self.assertFalse(chat["wrote"])
        for value in (False, "true", 1):
            denied = expert_turn.run_named_exclusive(TOOL, {"text": "写安全交底", "confirm_ok": value, "session_id": "gate-safety"})
            self.assertFalse(denied["wrote"])
            self.assertTrue(denied["hitl_pending"])
        with patch.dict(os.environ, {"CIVIL_SANDBOX": "read-only"}):
            denied = expert_turn.run_named_exclusive(TOOL, {"text": "写安全交底", "confirm_ok": True, "session_id": "ro-safety"})
        self.assertFalse(denied["wrote"])
        self.assertFalse(list(self.root.rglob("*.md")))

    def test_real_markdown_word_excel_consistent_and_formula_values_inert(self):
        import openpyxl
        from xml.etree import ElementTree as ET
        from zipfile import ZipFile
        result = expert_turn.run_expert_turn("写一份安全交底\n工序：吊料\n危险点：碰撞\n措施：用户措施A | B & C\n措施依据：=1+1\n监护人：张三\n电话：13800000000", "safety-brief", confirm_ok=True, force_intent="run", session_id="formats-safety")
        self.assertTrue(result["wrote"] and result["submit_blocked"], result)
        paths = {Path(f["path"]).suffix: Path(f["path"]) for f in result["files"]}
        expected = table(paths[".md"].read_text(encoding="utf-8"), "6 防护")
        book = openpyxl.load_workbook(paths[".xlsx"])
        try:
            sheet = next(book[name] for name in book.sheetnames if name.startswith("6 防护"))
            self.assertEqual(expected, [["" if c.value is None else c.value for c in row] for row in sheet])
            self.assertTrue(all(c.data_type != "f" for sheet in book for row in sheet for c in row))
        finally:
            book.close()
        with ZipFile(paths[".docx"]) as archive:
            document = ET.fromstring(archive.read("word/document.xml"))
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        tables = [[["".join(t.text or "" for t in c.findall(".//w:t", ns)) for c in r.findall("w:tc", ns)] for r in tbl.findall("w:tr", ns)] for tbl in document.findall(".//w:tbl", ns)]
        self.assertIn(expected, tables)
        self.assertEqual("done", self.scheduler.get(result["run_id"]).state)

    def test_hostile_inputs_survive_without_ragged_tables_or_own_verdicts(self):
        from scripts.test_post_robustness import INPUTS, VERDICTS, table_rows
        import re
        for name, source in INPUTS.items():
            with self.subTest(input=name):
                result = expert_turn.run_named_exclusive(TOOL, {"text": source, "confirm_ok": True, "session_id": "hostile-safety"})
                self.assertTrue(result["wrote"] and result["submit_blocked"], result)
                path = next(Path(f["path"]) for f in result["files"] if f["path"].endswith(".md"))
                md = path.read_text(encoding="utf-8")
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
