#!/usr/bin/env python3
"""Equipment ownership, raw claims and offline export acceptance."""
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
from packing_assistant.post_drafts.equipment import MISSING, TOOL, _owned, build_draft, parse
from packing_assistant.runtime.scheduler import Scheduler
from packing_assistant.runtime.tool_engine import default_engine


def draft(text):
    return build_draft("equip", TOOL, text)


def table(md, title):
    return next(values for name, values in tables_from_md(md) if name.startswith(title))


class EquipmentDrafts(unittest.TestCase):
    def test_dispatch_is_exclusive_and_drafts_keep_all_internal_boundaries(self):
        md = draft("")
        self.assertEqual(md, dispatch("equip", TOOL, ""))
        self.assertIsNone(build_draft("equip", "plan-resource__peak", ""))
        self.assertIsNone(build_draft("plan-resource", TOOL, ""))
        self.assertIn("AI 草稿", md)
        self.assertIn("submit_blocked=true", md)
        self.assertIn("签认：（空）", md)
        self.assertIn("本轮未核验", md)
        self.assertNotIn("## 用户原文", md)
        self.assertNotIn("MOM", md)
        self.assertNotIn("CN：", md)
        self.assertNotIn("SG：", md)
        self.assertEqual(8, len([line for line in md.splitlines() if line.startswith("## ")]))

    def test_all_dev_facts_have_semantically_correct_fields(self):
        from scripts.eval_post_content import classify, load, spots, split_echo
        for case in load("cases"):
            if case["post"] != "equip":
                continue
            with self.subTest(case=case["id"]):
                md = draft(case["request"])
                body, echo = split_echo(md)
                for fact in case["facts"]:
                    self.assertEqual("placed", classify(fact, body, echo, spots(body)), fact)
                self.assertFalse(any(word in body for word in case["forbidden"]))

    def test_narrative_crane_has_distinct_ids_lease_driver_and_monthly_plan(self):
        data = parse("一台中联ZLT7532塔吊，从恒力机械租的，计划2026-10-20进场安装，厂编号TQ-20241188，"
                     "使用登记：DJ2026SH0417，司机周立军持证上岗，月保定在每月5号，租赁按包月计。")
        self.assertEqual(1, len(data.devices))
        device = data.devices[0]
        self.assertEqual(("塔吊", "ZLT7532", "中联", "一台", "恒力机械", "租赁", "2026-10-20", "TQ-20241188", "包月"),
                         tuple(device[k] for k in ("name", "model", "manufacturer", "quantity", "lessor", "ownership", "planned_arrival", "factory_id", "billing")))
        self.assertNotIn("arrival", device)
        self.assertNotIn("state", device)
        self.assertNotIn("capacity", device)
        self.assertEqual(("使用登记", "DJ2026SH0417"), tuple(data.certificates[0][k] for k in ("certificate", "cert_id")))
        self.assertEqual(("周立军", "持证上岗"), tuple(data.persons[0][k] for k in ("operator", "operator_state")))
        self.assertNotIn("operator_id", data.persons[0])
        self.assertEqual("每月5号", data.maintenance[0]["frequency"])

    def test_two_machines_do_not_borrow_capacity_lease_date_or_operator(self):
        data = parse("一台汽车吊250吨的，海通机械租来的，说下周三到场；还有台旋挖钻，型号ZR220，是咱自己的，机号XW-0316，进场时间还没定，司机也没排。")
        first, second = data.devices
        self.assertEqual(("汽车吊", "250吨", "海通机械", "下周三"), tuple(first[k] for k in ("name", "capacity", "lessor", "planned_arrival")))
        self.assertEqual(("旋挖钻", "ZR220", "XW-0316", "自有"), tuple(second[k] for k in ("name", "model", "device_id", "ownership")))
        self.assertNotIn("planned_arrival", second)
        self.assertNotIn("capacity", second)
        self.assertNotIn("lessor", second)
        self.assertTrue(all(r.get("operator", MISSING) == MISSING for r in data.persons))

    def test_factory_filing_and_device_numbers_remain_different_columns(self):
        data = parse("设备名称：塔吊\n设备编号：EQ-A\n厂编号：TQ-A\n备案号：DJ-A")
        self.assertEqual(("EQ-A", "TQ-A", "DJ-A"), tuple(data.devices[0][k] for k in ("device_id", "factory_id", "filing_id")))
        self.assertFalse(data.certificates)

    def test_models_do_not_create_capacity_or_quantity(self):
        for model in ("SC200/200", "STT293", "QY250-X5", "ZR220", "TC5613"):
            data = parse("设备名称：机械A\n型号："+model)
            self.assertNotIn("capacity", data.devices[0])
            self.assertNotIn("quantity", data.devices[0])

    def test_planned_and_actual_arrival_departure_stay_separate(self):
        data = parse("设备名称：机械A\n计划进场：2026-10-20\n实际进场：2026-10-22\n计划退场：2027-03-01\n实际退场：2027-03-05")
        row = data.devices[0]
        self.assertEqual(["2026-10-20", "2026-10-22", "2027-03-01", "2027-03-05"], [row[k] for k in ("planned_arrival", "arrival", "planned_departure", "departure")])
        self.assertNotIn("state", row)

    def test_expected_relative_arrival_is_not_converted_to_calendar_date(self):
        data = parse("设备名称：机械A\n说下周三到场")
        self.assertEqual("下周三", data.devices[0]["planned_arrival"])
        self.assertNotIn("arrival", data.devices[0])

    def test_date_fields_cannot_be_populated_with_ids_or_models(self):
        for value in ("STT293", "V2.0", "TQ-20241188", "备案号25号", "不是2026-10-20", "2026-10-20？"):
            with self.subTest(value=value):
                data = parse("设备名称：机械A\n计划进场："+value)
                self.assertEqual(MISSING, data.devices[0].get("planned_arrival", MISSING))

    def test_unknown_certificate_is_not_present_valid_or_received(self):
        md = draft("设备名称：机械A\n合格证：未提供\n检定日期：2026-10-01")
        certificates = table(md, "6 证件")
        self.assertEqual(MISSING, certificates[1][certificates[0].index("用户证件编号")])
        self.assertEqual(MISSING, certificates[1][certificates[0].index("用户检定期限 / 有效期原文")])
        self.assertEqual("本轮未核验", certificates[1][-1])
        self.assertNotIn("2027-10-01", md)

    def test_reported_approvals_remain_unverified_user_claims_not_system_permission(self):
        md = draft("设备名称：机械A\n设备状态：准予使用\n证书名称：验收报告\n"
                   "证件状态：验收合格\n司机：张测试\n上岗陈述：持证上岗\n维修记录：已恢复运行")
        for title, heading, claim in (("2 设备", "用户当前状态原文（未核）", "准予使用"),
                                      ("6 证件", "用户证件收集状态（未核）", "验收合格")):
            rows = table(md, title)
            self.assertEqual(claim, rows[1][rows[0].index(heading)])
        self.assertEqual("本轮未核验", table(md, "6 证件")[1][-1])
        self.assertEqual("未执行 /（空）", table(md, "3 进场")[1][-1])
        self.assertEqual("本稿不签发", table(md, "7 故障")[1][-1])
        self.assertIn("不能凭此草稿投入使用", md)
        self.assertIn("submit_blocked=true", md)

    def test_named_certificate_is_not_copied_to_second_machine(self):
        data = parse("设备名称：塔吊A\n机号：D-A\n合格证：A-001\n有效期至：2026-12-01\n"
                     "设备名称：塔吊B\n机号：D-B\n合格证：未提供")
        self.assertEqual([0, 1], [r["_device"] for r in data.certificates])
        self.assertEqual(["A-001", MISSING], [r["cert_id"] for r in data.certificates])
        self.assertNotIn("certificate_expiry", data.certificates[1])

    def test_distinct_certificate_types_have_distinct_ids_and_periods(self):
        data = parse("设备名称：机械A\n合格证：HG-A\n有效期至：2026-12-01\n使用登记：DJ-A\n有效期至：2027-01-01")
        self.assertEqual(["产品合格证", "使用登记"], [r["certificate"] for r in data.certificates])
        self.assertEqual(["HG-A", "DJ-A"], [r["cert_id"] for r in data.certificates])
        self.assertEqual(["2026-12-01", "2027-01-01"], [r["certificate_expiry"] for r in data.certificates])

    def test_repeated_named_certificate_type_opens_a_new_record(self):
        data = parse("设备名称：机械A\n证书名称：检测报告\n证件编号：JC-A\n有效期至：2026-12-01\n证书名称：检测报告\n证件编号：JC-B")
        self.assertEqual(2, len(data.certificates))
        self.assertEqual(["JC-A", "JC-B"], [r["cert_id"] for r in data.certificates])
        self.assertNotIn("certificate_expiry", data.certificates[1])

    def test_person_name_does_not_invent_licence_and_licence_does_not_invent_name(self):
        data = parse("设备名称：机械A\n司机：张测试\n司机：李测试\n作业人员证件：OP-B")
        self.assertEqual(2, len(data.persons))
        self.assertNotIn("operator_id", data.persons[0])
        self.assertEqual("OP-B", data.persons[1]["operator_id"])
        data = parse("设备名称：机械A\n作业人员证件：OP-A")
        self.assertNotIn("operator", data.persons[0])

    def test_person_expiry_never_becomes_the_device_certificate_expiry(self):
        data = parse("设备名称：机械A\n合格证：HG-A\n有效期至：2026-12-01\n司机：张测试\n作业证：OP-A\n有效期至：2027-03-01")
        self.assertEqual("2026-12-01", data.certificates[0]["certificate_expiry"])
        self.assertEqual("2027-03-01", data.persons[0]["operator_expiry"])

    def test_operator_card_validity_does_not_follow_another_device(self):
        data = parse("设备名称：机械A\n司机：张测试\n作业证：OP-A\n人员证件有效期：2027-03-01\n设备名称：机械B\n司机：李测试")
        self.assertEqual([0, 1], [r["_device"] for r in data.persons])
        self.assertNotIn("operator_id", data.persons[1])
        self.assertNotIn("operator_expiry", data.persons[1])

    def test_company_names_containing_machine_words_do_not_open_a_device(self):
        for label in ("出租单位", "安装单位", "厂家"):
            data = parse("设备名称：机械A\n"+label+"：宏远塔吊有限公司")
            self.assertEqual(1, len(data.devices))
            self.assertEqual("机械A", data.devices[0]["name"])

    def test_rental_terms_amount_and_currency_stay_raw_without_total(self):
        text = "设备名称：机械A\n产权：租赁\n出租单位：出租方A\n计费：包月\n租金：SGD 1,800/month\n租赁合同：HT-A"
        data = parse(text)
        self.assertEqual("SGD 1,800/month", data.devices[0]["rent"])
        self.assertEqual("HT-A", data.devices[0]["lease_id"])
        md = draft(text)
        lease = table(md, "4 租赁")
        self.assertEqual("TBD", lease[1][lease[0].index("本轮合价")])

    def test_quantities_capacity_and_prices_do_not_come_from_codes_or_examples(self):
        for field in ("吨位", "设备数量", "租金"):
            for value in ("SC200/200", "STT293", "V2.0", "2026-10-20", "11月20日", "EQ-20", "如果250吨", "不是250吨", "250吨？"):
                with self.subTest(field=field, value=value):
                    data = parse("设备名称：机械A\n"+field+"："+value)
                    key = {"吨位": "capacity", "设备数量": "quantity", "租金": "rent"}[field]
                    self.assertEqual(MISSING, data.devices[0].get(key, MISSING))

    def test_maintenance_plan_completion_fault_and_stoppage_do_not_overwrite_each_other(self):
        text = "设备名称：机械A\n维保计划：每15天一次定期保养\n维保完成记录：未完成\n故障：制动器漏油\n停用：已停用\n维修记录：等待配件"
        data = parse(text)
        self.assertEqual("每15天一次", data.maintenance[0]["frequency"])
        self.assertIn("未完成", repr(data.maintenance))
        self.assertIn("制动器漏油", repr(data.maintenance))
        self.assertIn("已停用", repr(data.maintenance))
        self.assertIn("等待配件", repr(data.maintenance))
        md = draft(text)
        self.assertNotIn("可以恢复", md)
        self.assertNotIn("2026", md)

    def test_maintenance_source_does_not_replace_earlier_certificate_source(self):
        data = parse("设备名称：机械A\n合格证：HG-A\n证件来源：证件A.pdf\n维保：每15天一次\n来源：保养A.pdf")
        self.assertEqual("证件A.pdf", data.certificates[0]["source"])
        self.assertEqual("保养A.pdf", data.maintenance[0]["source"])

    def test_new_model_does_not_borrow_id_rental_licence_or_maintenance(self):
        data = parse("设备名称：塔吊\n型号：STT293\n机号：D-A\n出租单位：甲商\n合格证：HG-A\n司机：张测试\n维保：每15天一次\n型号：TC5613")
        self.assertEqual(2, len(data.devices))
        self.assertEqual("TC5613", data.devices[1]["model"])
        self.assertNotIn("device_id", data.devices[1])
        self.assertNotIn("lessor", data.devices[1])
        self.assertTrue(all(r["_device"] == 0 for r in data.certificates+data.persons+data.maintenance))

    def test_explicit_unique_id_returns_to_only_its_device(self):
        data = parse("设备名称：机械A\n机号：D-A\n设备名称：机械B\n机号：D-B\n机号：D-A\n合格证：HG-A")
        self.assertEqual(2, len(data.devices))
        self.assertEqual(0, data.certificates[0]["_device"])

    def test_duplicate_id_without_name_is_unassigned_not_the_last_device(self):
        data = parse("设备名称：机械A\n机号：DUP\n设备名称：机械B\n机号：DUP\n机号：DUP\n合格证：HG-X")
        self.assertEqual(3, len(data.devices))
        self.assertEqual(MISSING, data.devices[2].get("name", MISSING))
        self.assertEqual(2, data.certificates[0]["_device"])

    def test_same_name_does_not_merge_two_physical_devices(self):
        data = parse("设备名称：塔吊\n机号：D-A\n合格证：HG-A\n设备名称：塔吊\n机号：D-B")
        self.assertEqual(2, len(data.devices))
        self.assertEqual(["D-A", "D-B"], [r["device_id"] for r in data.devices])
        self.assertEqual(0, data.certificates[0]["_device"])

    def test_device_identifier_stated_after_certificate_is_retained_in_owner_columns(self):
        data = parse("设备名称：机械A\n合格证：HG-A\n厂编号：TQ-A")
        self.assertEqual("TQ-A", _owned(data, data.certificates)[0]["factory_id"])

    def test_table_reordered_fields_empty_identity_and_tail_notes_do_not_merge(self):
        data = parse("| 证件来源 | 有效期至 | 合格证 | 型号 | 设备名称 | 机号 |\n| --- | --- | --- | --- | --- | --- |\n"
                     "| A.pdf | 2027-03-01 | HG-A | STT293 | 塔吊 | D-A |\n| | | | | | |\n\n合格证：HG-X")
        self.assertEqual([0, 1, None], [r["_device"] for r in data.certificates])
        self.assertEqual("A.pdf", data.certificates[0]["source"])
        self.assertEqual("2027-03-01", data.certificates[0]["certificate_expiry"])
        self.assertEqual(MISSING, data.devices[1]["name"])
        self.assertEqual(MISSING, data.devices[1]["model"])
        self.assertEqual(MISSING, data.certificates[1]["cert_id"])
        self.assertEqual("HG-X", data.certificates[2]["cert_id"])

    def test_generic_expiry_for_two_certificate_types_stays_unassigned(self):
        data = parse("| 设备名称 | 合格证 | 使用登记 | 有效期至 | 证件来源 |\n| --- | --- | --- | --- | --- |\n"
                     "| 塔吊 | HG-A | DJ-A | 2027-03-01 | A.pdf |")
        product, registration, unassigned = data.certificates
        self.assertNotIn("certificate_expiry", product)
        self.assertNotIn("source", registration)
        self.assertNotIn("certificate", unassigned)
        self.assertEqual(("2027-03-01", "A.pdf"), (unassigned["certificate_expiry"], unassigned["source"]))

    def test_files_repeated_names_and_regions_have_independent_owners(self):
        data = parse("### A.txt\n辖区：CN\n设备名称：塔吊\n机号：D-A\n合格证：HG-A\n"
                     "### A.txt\n设备名称：塔吊\n机号：D-A\n作业证：OP-B")
        self.assertEqual(2, len(data.devices))
        self.assertEqual(["CN", "UNSPECIFIED"], [r["jurisdiction"] for r in data.devices])
        self.assertEqual(0, data.certificates[0]["_device"])
        self.assertEqual(1, data.persons[0]["_device"])

    def test_orphan_certificate_in_second_file_has_no_previous_device(self):
        data = parse("### A.txt\n设备名称：塔吊\n机号：D-A\n### B.txt\n合格证：HG-X")
        self.assertIsNone(data.certificates[0]["_device"])
        owned = _owned(data, data.certificates)[0]
        self.assertEqual(MISSING, owned["device_name"])
        self.assertEqual(MISSING, owned["device_id"])

    def test_examples_are_not_facts_but_explicit_actual_reset_is_retained(self):
        data = parse("例如一台汽车吊250吨，海通机械租来的。\n实际情况：设备名称：塔吊\n型号：STT293")
        self.assertEqual(1, len(data.devices))
        self.assertEqual("塔吊", data.devices[0]["name"])
        self.assertNotIn("250吨", repr(data))

    def test_english_explicit_labels_keep_identifiers_units_and_dates_raw(self):
        data = parse("Equipment: tower crane\nModel: STT293\nDevice ID: EQ-A\nCapacity: 230kW\n"
                     "Planned arrival: 2026-10-20\nCertificate: registration\nCertificate ID: DJ-A\nExpiry: 2027-03-01")
        self.assertEqual(("tower crane", "STT293", "EQ-A", "230kW"), tuple(data.devices[0][k] for k in ("name", "model", "device_id", "capacity")))
        self.assertEqual("DJ-A", data.certificates[0]["cert_id"])

    def test_evidence_tracks_physical_input_line_filename_not_original_page(self):
        data = parse("### A.txt\n设备名称：塔吊\n型号：STT293\n### B.txt\n合格证：HG-X")
        self.assertEqual(["A.txt · 本轮输入第2行（非原件页码）", "A.txt · 本轮输入第3行（非原件页码）", "B.txt · 本轮输入第5行（非原件页码）"],
                         [r["location"] for r in data.evidence])


class EquipmentRuntime(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="equipment-regression-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for context in (patch.object(expert_turn, "_OUT", self.root),
                        patch("packing_assistant.runtime.memory._OUT", self.root),
                        patch("packing_assistant.runtime.scheduler.get_scheduler", return_value=Scheduler()),
                        patch.dict(os.environ, {"CIVIL_JOB_ROOT": "", "CIVIL_SANDBOX_ROOTS": str(self.root), "CIVIL_SANDBOX": "workspace-write", "CIVIL_APPROVAL": "on-request"})):
            context.start()
            self.addCleanup(context.stop)

    def test_actual_exports_have_matching_tables_and_neutralize_spreadsheet_formulas(self):
        from xml.etree import ElementTree as ET
        from zipfile import ZipFile
        import openpyxl
        result = default_engine().execute(TOOL, {"text": "设备名称：=塔吊|A\n型号：STT293\n出租单位：乙 & 丙\n合格证：HG-A",
                                               "confirm_ok": True, "session_id": "export-equip"}, expert_id="equip", intent="run")
        self.assertTrue(result["ok"] and result["wrote"] and result["submit_blocked"], result)
        paths = {Path(f["path"]).suffix: Path(f["path"]) for f in result["files"]}
        self.assertTrue({".md", ".docx", ".xlsx"}.issubset(paths), paths)
        expected = table(paths[".md"].read_text(encoding="utf-8"), "2 设备")
        book = openpyxl.load_workbook(paths[".xlsx"])
        try:
            sheet = next(book[name] for name in book.sheetnames if name.startswith("2 设备"))
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

    def test_highrisk_readonly_and_question_paths_do_not_write(self):
        denied = expert_turn.run_named_exclusive(TOOL, {"text": "写设备台账", "session_id": "gate-equip"})
        self.assertFalse(denied["wrote"])
        self.assertTrue(denied["hitl_pending"])
        chat = expert_turn.run_expert_turn("设备证件怎么理解？", "equip", session_id="chat-equip")
        self.assertFalse(chat["wrote"])
        with patch.dict(os.environ, {"CIVIL_SANDBOX": "read-only"}):
            denied = expert_turn.run_named_exclusive(TOOL, {"text": "写设备台账", "confirm_ok": True, "session_id": "ro-equip"})
            self.assertFalse(denied["wrote"])
        self.assertFalse(list(self.root.rglob("*.md")))

    def test_forbidden_start_permission_is_refused_before_any_file_is_written(self):
        result = expert_turn.run_named_exclusive(TOOL, {"text": "设备名称：机械A\n设备状态：可以开工",
                                                       "confirm_ok": True, "session_id": "verdict-equip"})
        self.assertFalse(result["wrote"], result)
        self.assertEqual("forbidden_content", result["error_code"])
        self.assertFalse(list(self.root.rglob("*.*")))

    def test_hostile_inputs_keep_table_columns_and_no_affirmative_verdict(self):
        from scripts.test_post_robustness import INPUTS, VERDICTS, table_rows
        import re
        for name, source in INPUTS.items():
            with self.subTest(input=name):
                result = expert_turn.run_named_exclusive(TOOL, {"text": source, "confirm_ok": True, "session_id": "hostile-equip"})
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
