#!/usr/bin/env python3
"""Literal laboratory ownership and real offline deliverable acceptance."""
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
from packing_assistant.post_drafts.lab import MISSING, TOOLS, build_draft, parse
from packing_assistant.runtime.scheduler import Scheduler
from packing_assistant.runtime.tool_engine import default_engine


def draft(post, text):
    return build_draft(post, TOOLS[post], text)


def table(md, title):
    return next(rows for name, rows in tables_from_md(md) if name.startswith(title))


def rows(md, title):
    values = table(md, title)
    return [dict(zip(values[0], row)) for row in values[1:]]


class LabDrafts(unittest.TestCase):
    def test_only_own_tool_dispatches_and_all_three_have_internal_boundaries(self):
        for post, tool in TOOLS.items():
            self.assertEqual(draft(post, ""), dispatch(post, tool, ""))
            self.assertIsNone(build_draft(post, "proc-compare__table", ""))
            md = draft(post, "")
            self.assertIn("AI 草稿", md)
            self.assertIn("submit_blocked=true", md)
            self.assertEqual(9, len([line for line in md.splitlines() if line.startswith("## ")]))
            self.assertNotIn("## 用户原文", md)

    def test_narrative_sample_has_separate_materials_batches_and_quantities(self):
        data = parse("lab-sample", "第一车HRB500E盘螺Φ8，磅单28.6吨，批号PL0916，用在1#楼三层楼板；"
                     "另一车中砂35方，批号没给，见证人监理刘卫东，取样让王师傅去。")
        self.assertEqual(2, len(data.records))
        first, second = data.records
        self.assertEqual(("盘螺", "HRB500E；Φ8", "28.6吨", "PL0916", "1#楼三层楼板"),
                         tuple(first[k] for k in ("sample", "spec", "quantity", "batch", "site")))
        self.assertEqual(("中砂", "35方", "监理刘卫东", "王师傅"),
                         tuple(second[k] for k in ("sample", "quantity", "witness", "sampler")))
        self.assertEqual(MISSING, second.get("batch", MISSING))
        self.assertNotIn("spec", second)
        self.assertNotIn("site", second)

    def test_multiline_sample_places_manufacturer_people_and_ids_in_fields(self):
        md = draft("lab-sample", "材料：P·O42.5水泥\n厂家：海螺水泥\n批号：CM20260915\n"
                   "进场数量：180吨\n使用部位：地下室B1层底板\n取样员：陈立强\n"
                   "见证人：监理张德福\n送检单位：市建工质检中心\n委托单号：SJ2026-0932")
        actual = rows(md, "1 封面")[0]
        self.assertEqual(("P·O42.5水泥", "海螺水泥", "CM20260915", "180吨", "SJ2026-0932"),
                         tuple(actual[k] for k in ("材料 / 类别", "厂家 / 品牌", "材料批号", "用户进场数量", "委托单 / 送检编号")))
        signatures = rows(md, "2 角色")[0]
        self.assertEqual(("（空）", "（空）"), (signatures["取样签字"], signatures["见证签字"]))

    def test_material_words_inside_institution_or_manufacturer_are_not_new_samples(self):
        for label, name, field in (("检测机构", "水泥质检中心", "lab"), ("厂家", "海螺水泥", "manufacturer")):
            for delimiter in ("", " ", "："):
                with self.subTest(label=label, delimiter=delimiter):
                    data = parse("lab-sample", "材料：钢筋\n"+label+delimiter+name)
                    self.assertEqual(1, len(data.records))
                    self.assertEqual("钢筋", data.records[0]["sample"])
                    self.assertEqual(name, data.records[0][field])

    def test_english_alias_inside_an_identifier_cannot_truncate_the_identifier(self):
        data = parse("lab-record", "样品：试件A\n校准证书 JGJ-TEST\n报告编号 SAMPLE-TEST-001")
        self.assertEqual("JGJ-TEST", data.instruments[0]["calibration_id"])
        self.assertEqual("SAMPLE-TEST-001", data.records[0]["report_id"])
        self.assertNotIn("test", data.records[0])

    def test_delivery_material_is_not_a_testing_institution(self):
        data = parse("lab-record", "2026-08-26送检的钢筋原材，HRB400 Φ25，检测机构省建科院。")
        self.assertEqual(1, len(data.records))
        self.assertEqual("省建科院", data.records[0]["lab"])
        self.assertEqual("2026-08-26", data.records[0]["send_date"])
        self.assertNotIn("sample_date", data.records[0])

    def test_requested_deliverable_title_is_not_a_person_or_lab(self):
        for post, title in (("lab-sample", "取样送检清单"), ("lab-record", "试验台账"), ("lab-mix", "配比报告提纲")):
            with self.subTest(post=post):
                data = parse(post, "根据所选附件生成"+title+"，缺失字段保持待核。")
                self.assertEqual([], data.records)
                self.assertEqual([], data.instruments)
                self.assertEqual([], data.evidence)

    def test_each_sample_date_category_stays_literal(self):
        data = parse("lab-record", "样品：试样A\n进场日期：2026-09-01\n取样日期：2026-09-02\n"
                     "成型日期：2026-09-03\n送检日期：2026-09-04\n委托日期：2026-09-05")
        record = data.records[0]
        self.assertEqual(["2026-09-0"+str(i) for i in range(1, 6)],
                         [record[k] for k in ("arrival_date", "sample_date", "formed_date", "send_date", "entrusted_date")])

    def test_missing_sample_count_and_frequency_are_not_derived_from_batch(self):
        md = draft("lab-sample", "材料：钢筋\n数量：180吨\n辖区：SG")
        actual = rows(md, "3 取样依据")[0]
        self.assertEqual(MISSING, actual["用户取样组数原文"])
        self.assertEqual(MISSING, actual["用户取样频次原文"])
        self.assertEqual("UNSPECIFIED", actual["本轮计算组数"])
        self.assertNotIn("30%", md)
        self.assertNotIn("24 小时", md)
        self.assertNotIn("JGJ", md)

    def test_user_sample_count_remains_a_literal_not_a_generated_plan(self):
        md = draft("lab-sample", "材料：钢筋\n取样组数：3组\n取样频次：每车一组\n取样制度：项目规定Q-A")
        actual = rows(md, "3 取样依据")[0]
        self.assertEqual(("3组", "每车一组", "项目规定Q-A", "UNSPECIFIED"),
                         tuple(actual[k] for k in ("用户取样组数原文", "用户取样频次原文", "用户取样要求来源", "本轮计算组数")))

    def test_standalone_instrument_does_not_get_assigned_to_last_sample(self):
        data = parse("lab-record", "样品：砂浆试块\n报告编号：SJ-2026-0921\n"
                     "样品：SBS防水卷材\n委托单号：WT-2026-0917\n压力机YL-017上月刚检定完")
        self.assertEqual(2, len(data.records))
        self.assertEqual(1, len(data.instruments))
        self.assertTrue(all("instrument" not in r for r in data.records))
        self.assertEqual("YL-017", data.instruments[0]["instrument"])
        self.assertEqual("上月刚检定完", data.instruments[0]["verification_record"])
        self.assertNotIn("expiry", data.instruments[0])

    def test_explicit_instrument_reference_and_certificates_have_separate_columns(self):
        md = draft("lab-record", "样品：试件A\n仪器编号：YL-017\n检定证书：ZJ-2026-0455\n"
                   "校准证书：JZ-2026-0111\n检定日期：2026-03-19\n有效期至：2027-03-19")
        report = rows(md, "3 试验记录")[0]
        instrument = rows(md, "4 仪器")[0]
        self.assertEqual("YL-017", report["用户仪器编号引用"])
        self.assertEqual(("ZJ-2026-0455", "JZ-2026-0111", "2027-03-19", "本轮未核验"),
                         tuple(instrument[k] for k in ("用户检定证书", "用户校准证书", "用户检定有效期原文", "有效性 / 使用状态")))

    def test_two_instruments_never_share_certificate_or_expiry(self):
        data = parse("lab-record", "仪器编号：YL-017\n检定证书：ZJ-A\n有效期至：2027-03-19\n"
                     "仪器编号：YL-018\n校准证书：JZ-B")
        first, second = data.instruments
        self.assertEqual("ZJ-A", first["verification_id"])
        self.assertEqual("JZ-B", second["calibration_id"])
        self.assertNotIn("verification_id", second)
        self.assertNotIn("expiry", second)

    def test_negative_verification_record_does_not_drop_its_negation(self):
        for value in ("尚未检定完", "还没检定完", "未检定完成", "上月还没检定完"):
            with self.subTest(value=value):
                md = draft("lab-record", "压力机YL-017"+value)
                actual = rows(md, "4 仪器")[0]
                self.assertEqual(value, actual["用户仪器检定记录"])
                self.assertEqual("本轮未核验", actual["有效性 / 使用状态"])
                self.assertEqual(MISSING, actual["用户检定有效期原文"])

    def test_instrument_certificate_before_its_number_is_not_lost(self):
        data = parse("lab-record", "仪器名称：压力机\n检定证书：ZJ-A\n仪器编号：YL-017")
        self.assertEqual(1, len(data.instruments))
        self.assertEqual("ZJ-A", data.instruments[0]["verification_id"])
        self.assertEqual("YL-017", data.instruments[0]["instrument"])

    def test_same_kind_new_batch_is_separate_and_does_not_inherit_people(self):
        data = parse("lab-sample", "钢筋批号A01，取样员李测试；另一车钢筋批号A02。")
        self.assertEqual(2, len(data.records))
        self.assertEqual(["A01", "A02"], [r["batch"] for r in data.records])
        self.assertNotIn("sampler", data.records[1])

    def test_another_load_of_same_material_with_missing_batch_is_not_the_first_load(self):
        data = parse("lab-sample", "第一车钢筋批号A01，数量62吨，取样员李测试；另一车钢筋，批号没给。")
        self.assertEqual(2, len(data.records))
        self.assertEqual(MISSING, data.records[1].get("batch", MISSING))
        self.assertNotIn("sampler", data.records[1])
        self.assertNotIn("quantity", data.records[1])

    def test_explicit_same_instrument_revision_does_not_reuse_the_previous_certificate(self):
        data = parse("lab-record", "仪器编号：YL-017\n检定证书：ZJ-A\n有效期至：2027-03-19\n"
                     "仪器编号：YL-017\n校准证书：JZ-B")
        self.assertEqual(2, len(data.instruments))
        self.assertEqual("JZ-B", data.instruments[1]["calibration_id"])
        self.assertNotIn("verification_id", data.instruments[1])
        self.assertNotIn("expiry", data.instruments[1])

    def test_report_not_returned_is_pending_not_a_result(self):
        md = draft("lab-record", "样品：钢筋原材\n报告状态：还没收到")
        actual = rows(md, "3 试验记录")[0]
        self.assertEqual("还没收到", actual["用户报告状态"])
        self.assertEqual("待核", actual["报告是否收到 / 原件核验"])
        self.assertEqual("本轮未检测", actual["结论"])

    def test_concrete_and_mortar_targets_remain_separate_without_inferred_units(self):
        data = parse("lab-mix", "地下室外墙C30，抗渗P8，坍落度140±20，水泥用海螺；"
                     "砌体M7.5水泥砂浆，稠度没给；砂都是赣江砂场拉的，两边都还没试拌。")
        self.assertEqual(2, len(data.records))
        first, second = data.records
        self.assertEqual(("地下室外墙", "C30", "P8", "140±20", "海螺"),
                         tuple(first[k] for k in ("site", "strength", "permeability", "slump", "cement")))
        self.assertEqual(("砌体", "M7.5"), (second["site"], second["strength"]))
        self.assertNotIn("cement", second)
        self.assertNotIn("permeability", second)
        self.assertNotIn("slump", second)
        self.assertEqual(["赣江砂场", "赣江砂场"], [r["sand"] for r in data.records])
        self.assertEqual(["还没试拌"]*2, [r["trial_status"] for r in data.records])

    def test_trial_id_does_not_claim_trial_data_or_approval(self):
        md = draft("lab-mix", "工程部位：池壁\n强度等级：C35\n试配记录编号：PB-2026-0812")
        actual = rows(md, "5 试配记录")[0]
        self.assertEqual("PB-2026-0812", actual["试配记录编号"])
        self.assertEqual(MISSING, actual["用户试配状态（未核）"])
        layer = next(line for line in md.splitlines() if line.startswith("| 施工配合比 |"))
        self.assertIn("不给施工配合比", layer)

    def test_negative_trial_statements_cannot_become_positive(self):
        for value in ("没有试验数据", "无试验数据", "还没试拌", "不是已有试验数据", "是否已有试验数据？"):
            with self.subTest(value=value):
                md = draft("lab-mix", "工程部位：池壁\n"+value)
                layer = next(line for line in md.splitlines() if line.startswith("| 施工配合比 |"))
                self.assertIn("不给施工配合比", layer)

    def test_user_positive_data_claim_still_does_not_compute_or_approve_a_mix(self):
        md = draft("lab-mix", "写一份配比报告提纲 C40 已有试验数据")
        self.assertIn("施工配比数字仍须试验室签认", md)
        actual = rows(md, "6 批准资料")[0]
        self.assertEqual("UNSPECIFIED", actual["本轮计算施工用量"])
        self.assertEqual("（空）", actual["批准 / 签认"])

    def test_hearsay_or_unchecked_trial_status_preserves_its_qualification(self):
        for value in ("据说已有试验数据但未核对", "听说已有试验数据，未见原件", "已有试验数据但原件未见"):
            with self.subTest(value=value):
                md = draft("lab-mix", "部位：池壁\n"+value)
                layer = next(line for line in md.splitlines() if line.startswith("| 施工配合比 |"))
                self.assertIn("不给施工配合比", layer)
                actual = rows(md, "5 试配记录")[0]
                self.assertNotEqual("已有试验数据", actual["用户试配状态（未核）"])
                self.assertIn("已有试验数据", actual["用户试配状态（未核）"])

    def test_original_user_ratios_are_registered_but_not_used_for_production(self):
        md = draft("lab-mix", "部位：池壁\n用户试验室配比：1:2.3:3.7\n"
                   "实测含水率：4.2%\n用户水胶比：0.46\n用户施工配比原文：水171kg/m3")
        actual = rows(md, "5 试配记录")[0]
        self.assertEqual(("1:2.3:3.7", "4.2%", "0.46"),
                         tuple(actual[k] for k in ("用户试验室配比原文", "用户含水率原文", "用户水胶比原文")))
        approved = rows(md, "6 批准资料")[0]
        self.assertEqual("水171kg/m3", approved["用户施工配比原文（未核）"])
        self.assertEqual("UNSPECIFIED", approved["本轮计算施工用量"])

    def test_ambiguous_two_of_three_mix_targets_do_not_choose_the_last_target(self):
        data = parse("lab-mix", "部位：池壁A\n强度等级：C35\n部位：池壁B\n强度等级：C30\n"
                     "部位：池壁C\n强度等级：C25\n两边都还没试拌")
        self.assertEqual(4, len(data.records))
        self.assertTrue(all("trial_status" not in r for r in data.records[:3]))
        self.assertEqual("还没试拌", data.records[-1]["trial_status"])
        self.assertNotIn("site", data.records[-1])
        self.assertIn("范围未明确", data.records[-1]["_unassigned"])

    def test_numeric_codes_dates_versions_do_not_become_quantities_or_workability(self):
        for key in ("数量", "坍落度", "取样组数"):
            for value in ("2026/10/18", "第3版", "BJ-2026-018", "2026A", "3#楼"):
                with self.subTest(key=key, value=value):
                    data = parse("lab-mix" if key == "坍落度" else "lab-sample", "样品：试样A\n"+key+"："+value)
                    field = {"数量": "quantity", "坍落度": "slump", "取样组数": "group_count"}[key]
                    self.assertEqual(MISSING, data.records[0].get(field, MISSING))

    def test_report_ids_or_dates_do_not_become_strength_or_permeability_grades(self):
        for label, field in (("强度等级", "strength"), ("抗渗等级", "permeability")):
            for value in ("PB-2026-018", "2026/10/18", "第3版", "PB-C35", "WT-P8"):
                with self.subTest(label=label, value=value):
                    data = parse("lab-mix", "部位：池壁\n"+label+"："+value)
                    self.assertEqual(MISSING, data.records[0][field])

    def test_literal_grade_families_are_not_converted_or_certified(self):
        for value in ("C35", "M7.5", "C30/37", "Grade 40"):
            with self.subTest(value=value):
                data = parse("lab-mix", "部位：池壁\n强度等级："+value)
                self.assertEqual(value, data.records[0]["strength"])

    def test_example_scope_ends_only_at_actual_record_reset(self):
        data = parse("lab-mix", "例如池壁C60，试配记录PB-FAKE，已有试验数据；"
                     "实际情况：地下室外墙C30，试配记录PB-REAL，还没试拌。")
        self.assertEqual(1, len(data.records))
        self.assertEqual("C30", data.records[0]["strength"])
        self.assertEqual("PB-REAL", data.records[0]["trial_id"])
        self.assertEqual("还没试拌", data.records[0]["trial_status"])
        self.assertNotIn("PB-FAKE", draft("lab-mix", "例如池壁C60，试配记录PB-FAKE，已有试验数据"))

    def test_explicit_metadata_named_example_is_not_a_hypothetical_scenario(self):
        data = parse("lab-sample", "材料名称：示例钢筋\n数量：62吨")
        self.assertEqual("示例钢筋", data.records[0]["sample"])
        self.assertEqual("62吨", data.records[0]["quantity"])

    def test_matrix_empty_cells_do_not_reuse_a_previous_sample(self):
        data = parse("lab-sample", "|材料|批号|数量|见证人|\n|---|---|---|---|\n"
                     "|钢筋|A01|62吨|刘测试|\n|中砂||35方||\n||C03|10吨||")
        self.assertEqual(3, len(data.records))
        self.assertEqual(MISSING, data.records[1]["batch"])
        self.assertEqual(MISSING, data.records[1]["witness"])
        self.assertEqual(MISSING, data.records[2]["sample"])
        self.assertEqual("C03", data.records[2]["batch"])

    def test_matrix_symbols_escape_once_and_keep_column_ownership(self):
        md = draft("lab-record", "|样品|报告编号|检测机构|\n|---|---|---|\n|=2+3 A\\|B &lt;10|R-A|机构A|")
        actual = rows(md, "3 试验记录")[0]
        self.assertEqual("=2+3 A|B <10", actual["样品 / 试样"])
        self.assertEqual("R-A", actual["用户报告编号"])
        self.assertIn("=2+3 A&#124;B &lt;10", md)

    def test_separate_source_files_do_not_share_identity_or_certificate(self):
        data = parse("lab-record", "### 检测A.txt\n样品：试件A\n报告编号：R-A\n"
                     "仪器编号：YL-017\n检定证书：ZJ-A\n### 检测B.txt\n报告编号：R-B\n有效期至：2027-03-19")
        self.assertEqual(2, len(data.records))
        self.assertNotIn("sample", data.records[1])
        self.assertEqual("R-B", data.records[1]["report_id"])
        self.assertEqual(2, len(data.instruments))
        self.assertNotIn("instrument", data.instruments[1])
        self.assertNotIn("verification_id", data.instruments[1])
        self.assertEqual("检测B.txt", data.instruments[1]["_origin"])

    def test_region_is_owned_by_the_record_not_the_last_named_country(self):
        data = parse("lab-mix", "部位：池壁A\n辖区：CN\n强度等级：C35\n"
                     "部位：池壁B\n辖区：SG\n强度等级：C30")
        self.assertEqual(["CN", "SG"], [r["jurisdiction"] for r in data.records])

    def test_file_local_region_does_not_default_the_next_file(self):
        data = parse("lab-sample", "### A.txt\n辖区：CN\n材料：钢筋\n### B.txt\n材料：中砂")
        self.assertEqual(["CN", "UNSPECIFIED"], [r["jurisdiction"] for r in data.records])

    def test_evidence_position_reports_real_input_line_and_filename(self):
        md = draft("lab-record", "### A.txt\n样品：试件A\n报告编号：R-A\n### B.txt\n报告编号：R-B")
        proof = rows(md, "6 用户证据")
        self.assertEqual(["A.txt · 本轮输入第2行", "A.txt · 本轮输入第3行", "B.txt · 本轮输入第5行"],
                         [r["输入位置（非原件页码）"] for r in proof])
        self.assertEqual(["试件A", "试件A", MISSING], [r["用户对象"] for r in proof])


class LabRuntime(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="lab-regression-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for context in (
            patch.object(expert_turn, "_OUT", self.root),
            patch("packing_assistant.runtime.memory._OUT", self.root),
            patch("packing_assistant.runtime.scheduler.get_scheduler", return_value=Scheduler()),
            patch.dict(os.environ, {"CIVIL_JOB_ROOT": "", "CIVIL_SANDBOX_ROOTS": str(self.root), "CIVIL_SANDBOX": "workspace-write", "CIVIL_APPROVAL": "on-request"}),
        ):
            context.start()
            self.addCleanup(context.stop)

    def test_all_three_tools_export_matching_markdown_word_and_excel(self):
        from xml.etree import ElementTree as ET
        from zipfile import ZipFile
        import openpyxl
        for post, text, target in (
            ("lab-sample", "材料：=钢筋|A\n批号：A01\n数量：62吨\n取样员：李测试", "1 封面"),
            ("lab-record", "样品：=试件|A\n报告编号：R-A\n仪器编号：YL-017\n校准证书：JZ-A", "3 试验记录"),
            ("lab-mix", "部位：=池壁|A\n强度等级：C35\n坍落度：160±20\n试配记录：PB-A", "1 用途"),
        ):
            with self.subTest(post=post):
                result = default_engine().execute(TOOLS[post], {"text": text, "confirm_ok": True, "session_id": "export-"+post}, expert_id=post, intent="run")
                self.assertTrue(result["ok"] and result["wrote"], result)
                self.assertTrue(result["submit_blocked"])
                paths = {Path(f["path"]).suffix: Path(f["path"]) for f in result["files"]}
                self.assertTrue({".md", ".docx", ".xlsx"}.issubset(paths), paths)
                expected = table(paths[".md"].read_text(encoding="utf-8"), target)
                book = openpyxl.load_workbook(paths[".xlsx"])
                try:
                    sheet = next(book[name] for name in book.sheetnames if name.startswith(target))
                    self.assertEqual(expected, [["" if cell.value is None else cell.value for cell in row] for row in sheet])
                    self.assertTrue(all(cell.data_type != "f" for sheet in book for row in sheet for cell in row))
                finally:
                    book.close()
                with ZipFile(paths[".docx"]) as archive:
                    document = ET.fromstring(archive.read("word/document.xml"))
                ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                word_tables = [[["".join(t.text or "" for t in c.findall(".//w:t", ns)) for c in r.findall("w:tc", ns)]
                                for r in tbl.findall("w:tr", ns)] for tbl in document.findall(".//w:tbl", ns)]
                self.assertIn(expected, word_tables)

    def test_highrisk_confirmation_and_readonly_and_question_modes_stay_closed(self):
        for post, tool in TOOLS.items():
            if post != "lab-record":
                denied = expert_turn.run_named_exclusive(tool, {"text": "生成内部表", "session_id": "gate-"+post})
                self.assertFalse(denied["wrote"])
                self.assertTrue(denied["hitl_pending"])
            chat = expert_turn.run_expert_turn("这个岗位怎么用？", post, session_id="chat-"+post)
            self.assertFalse(chat["wrote"])
        with patch.dict(os.environ, {"CIVIL_SANDBOX": "read-only"}):
            for post, tool in TOOLS.items():
                denied = expert_turn.run_named_exclusive(tool, {"text": "生成内部表", "confirm_ok": True, "session_id": "ro-"+post})
                self.assertFalse(denied["wrote"])
        self.assertFalse(list(self.root.rglob("*.md")))

    def test_each_lab_post_survives_hostile_inputs_with_intact_tables(self):
        from scripts.test_post_robustness import INPUTS, VERDICTS, table_rows
        import re
        for post, tool in TOOLS.items():
            for name, source in INPUTS.items():
                with self.subTest(post=post, input=name):
                    result = expert_turn.run_named_exclusive(tool, {"text": source, "confirm_ok": True, "session_id": "hostile-"+post})
                    self.assertTrue(result["wrote"] and result["submit_blocked"], result)
                    md = next(Path(f["path"]) for f in result["files"] if f["path"].endswith(".md")).read_text(encoding="utf-8")
                    parsed = table_rows(md)
                    for index in range(1, len(parsed)):
                        number, cells = parsed[index]
                        last_number, previous = parsed[index-1]
                        if number != last_number+1 or all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                            continue
                        self.assertEqual(len(previous), len(cells))
                    self.assertTrue(all(pattern.search(md) is None for pattern in VERDICTS))


if __name__ == "__main__":
    unittest.main()
