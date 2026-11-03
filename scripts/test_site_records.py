#!/usr/bin/env python3
"""Independent notice/environment/point ownership and non-verdict regressions."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packing_assistant.post_drafts.site_records import TOOLS, UNKNOWN, build_draft, parse_records
from packing_assistant.office_job import tables_from_md


class SiteRecordTests(unittest.TestCase):
    def rows(self, text, post="survey"):
        return parse_records(text, post)

    def tables(self, text, post="survey"):
        return dict(tables_from_md(build_draft(post, TOOLS[post], text)))

    def test_exact_tools_templates_and_registry(self):
        from packing_assistant.post_drafts import build_draft as registered, dedicated_posts
        for post, tool in TOOLS.items():
            md = build_draft(post, tool, "")
            self.assertIn("AI 草稿", md)
            self.assertIn("submit_blocked=true", md)
            self.assertIn("（空栏）", md)
            self.assertIn("[A001]", md)
            self.assertNotIn("## 用户原文", md)
            self.assertIn(post, dedicated_posts())
            self.assertEqual(md, registered(post, tool, ""))
            for wrong in set(TOOLS.values())-{tool}:
                self.assertIsNone(build_draft(post, wrong, ""))

    def test_same_named_file_is_a_new_epoch_and_missing_never_borrowed(self):
        rows = self.rows("### 同名.csv\n项目：甲；依据：甲口径；点号：BM01；原高程：12.5；观测：王甲\n### 同名.csv\n点号：BM02")
        self.assertEqual(2, len(rows))
        self.assertNotEqual(rows[0]["_file"], rows[1]["_file"])
        self.assertTrue(all(k not in rows[1] for k in ("project", "basis", "observer", "original_h")))

    def test_project_stage_task_changes_freeze_older_records(self):
        rows = self.rows("项目：甲；阶段：初稿；任务：T01；点号：BM01；X：71\n阶段：复核；点号：BM02\n任务：T02；点号：BM03\n项目：乙；点号：BM04")
        self.assertEqual(["甲", "甲", "甲", "乙"], [r["project"] for r in rows])
        self.assertEqual(["T01", "T01", "T02"], [r["task"] for r in rows[:3]])
        self.assertNotIn("stage", rows[3])
        self.assertNotIn("x", rows[1])

    def test_reordered_metadata_matrix_and_empty_short_rows_independent(self):
        for post, header, identity, field in (
            ("survey", "X|项目|点号", "point", "x"),
            ("env", "数量|项目|部位活动", "activity", "quantity"),
            ("supervision", "完成时限|项目|来文条目", "issue", "deadline"),
        ):
            rows = self.rows("|"+header+"|\n|---|---|---|\n|71|甲|对象甲|\n|92|乙||\n|||\n|", post)
            self.assertEqual(4, len(rows), (post, rows))
            self.assertEqual(["甲", "乙"], [r["project"] for r in rows[:2]])
            self.assertEqual(UNKNOWN, rows[1][identity])
            self.assertEqual("92", rows[1][field])
            self.assertEqual(UNKNOWN, rows[3][field])

    def test_metadata_only_matrix_rows_are_not_discarded(self):
        rows = self.rows("|项目|阶段|\n|---|---|\n|甲|初稿|\n|乙|复核|")
        self.assertEqual(2, len(rows))
        self.assertEqual(["甲", "乙"], [r["project"] for r in rows])

    def test_header_reordering_and_blank_leading_label_cell(self):
        rows = self.rows("|点号|X|\n|---|---|\n|BM01|71|\n|X|点号|\n|---|---|\n|92|BM02|")
        self.assertEqual(["BM01", "BM02"], [r["point"] for r in rows])
        self.assertEqual(["71", "92"], [r["x"] for r in rows])
        self.assertNotIn("719", build_draft("survey", TOOLS["survey"], "||X|719|"))

    def test_physical_cells_split_before_single_entity_decode(self):
        row = self.rows("|点号|X|来源|\n|---|---|---|\n|甲&#124;乙&lt;丙|=1+2|字面&amp;lt;|")[0]
        self.assertEqual("甲|乙<丙", row["point"])
        self.assertEqual("=1+2", row["x"])
        self.assertEqual("字面&lt;", row["source"])

    def test_jurisdiction_not_inferred_from_names_filename_or_datum(self):
        for post, text in (("survey", "### SG测量.pdf\n项目：中国楼；点号：BM01；坐标系：SVY21"),
                           ("env", "部位：新加坡厂房；属地：江宁区"),
                           ("supervision", "项目：中国楼；监理机构：新加坡监理；问题：漏缝")):
            row = self.rows(text, post)[0]
            self.assertEqual(UNKNOWN, row["_project_zone"])
            self.assertNotIn("zone", row)

    def test_local_country_does_not_become_default_for_next_point(self):
        rows = self.rows("点号：BM01\nSG\n点号：BM02")
        self.assertEqual("SG", rows[0]["zone"])
        self.assertNotIn("zone", rows[1])

    def test_explicit_dual_basis_is_separate_and_local_override_drops_shared_basis(self):
        rows = self.rows("总体辖区：DUAL；依据：总体口径；CN依据：中国文件；SG依据：新加坡文件\n点号：BM01；辖区：CN\n点号：BM02；辖区：SG")
        self.assertEqual(["CN", "SG"], [r["zone"] for r in rows])
        self.assertTrue(all("basis" not in r for r in rows))
        self.assertTrue(all(r["CN_basis"] == "中国文件" and r["SG_basis"] == "新加坡文件" for r in rows))

    def test_scenario_excluded_but_named_example_point_kept(self):
        rows = self.rows("示例：点号：假点；X：999\n真实记录：点号：示例公司点；X：71")
        self.assertEqual(1, len(rows))
        self.assertEqual("示例公司点", rows[0]["point"])
        self.assertEqual("71", rows[0]["x"])
        self.assertNotIn("999", build_draft("survey", TOOLS["survey"], "假设：点号：假点；X：999"))

    def test_source_positions_do_not_invent_original_page_numbers(self):
        row = self.rows("### 合成点表.csv\n点号：BM01；X：71")[0]
        self.assertIn("合成点表.csv", row["_sources"]["x"])
        self.assertIn("非原件页码", row["_sources"]["x"])

    def test_two_pollution_activities_no_orphans_or_number_destination_borrowing(self):
        rows = self.rows("两个事：一个是4#地块桩基泥浆，一天出82方，现在排沉淀池沉了再拉走，拉到哪个厂还没定；另一个是老厂房混凝土块，估计214吨，找的合成渣运，说是夜间出场。", "env")
        self.assertEqual(2, len(rows))
        self.assertEqual("82方", rows[0]["quantity"])
        self.assertNotIn("quantity", rows[1])
        self.assertEqual("估计214吨", rows[1]["estimated_quantity"])
        self.assertNotIn("carrier", rows[0])
        self.assertEqual(UNKNOWN, rows[0]["destination"])
        self.assertNotIn("wastewater", rows[1])

    def test_labeled_activity_extracts_location_without_extra_record(self):
        rows = self.rows("活动：4#地块桩基泥浆；数量：82方", "env")
        self.assertEqual(1, len(rows))
        self.assertEqual("4#地块", rows[0]["location"])

    def test_estimated_quantity_not_a_measured_disposal_quantity(self):
        tables = self.tables("活动：泥浆；估计量：大概82方；数量：71方", "env")
        quantities = tables["5 弃土与建筑垃圾"]
        self.assertEqual("71方", quantities[1][quantities[0].index("用户产生/外运数量")])
        self.assertEqual("大概82方", quantities[1][quantities[0].index("用户估计运输/产生量（未核）")])
        row = self.rows("活动：泥浆；数量：大概82方", "env")[0]
        self.assertEqual(UNKNOWN, row["quantity"])
        self.assertEqual("大概82方", row["estimated_quantity"])

    def test_estimate_redirect_keeps_own_unit_and_withdraws_measured_unit(self):
        row = self.rows("活动：泥浆；数量：71方；数量：预计82吨", "env")[0]
        self.assertEqual(UNKNOWN, row["quantity"])
        self.assertEqual(UNKNOWN, row["unit"])
        self.assertEqual("预计82吨", row["estimated_quantity"])
        self.assertEqual("吨", row["estimated_unit"])
        self.assertNotIn("quantity", row["_sources"])
        self.assertNotIn("unit", row["_sources"])

    def test_unknown_estimate_withdraws_derived_estimate_unit(self):
        row = self.rows("活动：泥浆；估计量：82吨；估计量：没定", "env")[0]
        self.assertEqual(UNKNOWN, row["estimated_quantity"])
        self.assertEqual(UNKNOWN, row["estimated_unit"])
        self.assertNotIn("estimated_unit", row["_sources"])

    def test_unknown_quantity_clears_derived_unit_and_provenance(self):
        row = self.rows("活动：泥浆；数量：71方；数量：没定", "env")[0]
        self.assertEqual(UNKNOWN, row["quantity"])
        self.assertEqual(UNKNOWN, row["unit"])
        self.assertNotIn("quantity", row["_sources"])
        self.assertNotIn("unit", row["_sources"])

    def test_district_is_not_a_country_and_reported_submission_not_a_permit(self):
        row = self.rows("部位：4#楼；辖区：江宁区；夜间作业：10月26日连续浇筑，已报街道", "env")[0]
        self.assertEqual("江宁区", row["district"])
        self.assertNotIn("zone", row)
        table = self.tables("部位：4#楼；夜间作业：10月26日连续浇筑，已报街道", "env")["4 噪声与夜间"]
        self.assertEqual("已报街道", table[1][table[0].index("用户报批材料摘录")])
        self.assertEqual(UNKNOWN, table[1][-1])

    def test_monitor_value_limit_and_source_do_not_produce_compliance_verdict(self):
        table = self.tables("活动：切割；监测值：71dBA；监测来源：M01.pdf；限值：72dBA；限值来源：L01.pdf", "env")["7 检查与整改"]
        self.assertEqual("71dBA", table[1][table[0].index("用户监测值摘录")])
        self.assertEqual(UNKNOWN, table[1][-1])

    def test_env_manifest_belongs_to_own_transport_and_future_source_unknown(self):
        rows = self.rows("活动：泥浆；运输单位：甲运；联单号：L01；去向：甲厂；去向证明：将提供甲厂.pdf\n活动：碎砼；运输单位：乙运", "env")
        self.assertEqual("L01", rows[0]["manifest"])
        self.assertEqual(UNKNOWN, rows[0]["destination_source"])
        self.assertNotIn("manifest", rows[1])
        self.assertNotIn("destination", rows[1])

    def test_two_notifications_missing_first_id_and_second_deadline_independent(self):
        rows = self.rows("一张说6#楼三层框架柱保护层偏薄，具体厚度还没量，我们打算重新垫塞，周甲负责，10月26日前交；另一张单号JL-2026-0312，东侧外脚手架连墙件缺了两处，那张期限监理没写。", "supervision")
        self.assertEqual(2, len(rows))
        self.assertNotIn("id", rows[0])
        self.assertEqual("JL-2026-0312", rows[1]["id"])
        self.assertEqual(UNKNOWN, rows[1]["deadline"])
        self.assertNotIn("owner", rows[1])
        self.assertEqual(UNKNOWN, rows[0]["thickness"])

    def test_notice_parent_header_shared_only_with_own_issue_children(self):
        rows = self.rows("来文编号：N01；来文日期：2026-11-02；监理机构：甲监理；来文条目：漏缝；拟办：封堵；完成时限：11月6日\n来文条目：积水；拟办：清理\n来文编号：N02；来文条目：钢筋保护层", "supervision")
        self.assertEqual(3, len(rows))
        self.assertEqual(["N01", "N01", "N02"], [r["id"] for r in rows])
        self.assertEqual("2026-11-02", rows[1]["notice_date"])
        self.assertNotIn("notice_date", rows[2])
        self.assertNotIn("deadline", rows[1])
        self.assertNotIn("recipient", rows[2])

    def test_late_notice_id_enriches_not_adds_or_borrows_to_future_notice(self):
        rows = self.rows("来文条目：漏缝；来文编号：N01；拟办：封堵\n来文条目：积水", "supervision")
        self.assertEqual(2, len(rows))
        self.assertEqual("N01", rows[0]["id"])
        self.assertNotIn("id", rows[1])

    def test_unknown_notice_id_or_deadline_clears_current_source(self):
        rows = self.rows("来文编号：N01；问题：漏缝；期限：11月6日；期限：没写；来文编号：未核\n问题：积水", "supervision")
        self.assertEqual(UNKNOWN, rows[0]["deadline"])
        self.assertEqual(UNKNOWN, rows[0]["id"])
        self.assertNotIn("deadline", rows[0]["_sources"])
        self.assertNotIn("id", rows[0]["_sources"])
        self.assertNotIn("id", rows[1])

    def test_action_continuation_and_completion_claim_are_not_recheck(self):
        rows = self.rows("来文问题：漏缝；拟办：割开重新满焊，再做闭水试验；实施记录：用户称已完成；复查记录：用户复查扫描件", "supervision")
        self.assertIn("再做闭水试验", rows[0]["action"])
        table = self.tables("问题：漏缝；实施记录：用户称已完成；复查记录：用户复查扫描件", "supervision")["6 期限与闭合台账"]
        self.assertEqual([UNKNOWN, "（空栏）"], table[1][-2:])

    def test_stop_and_restart_documents_only_directory_and_empty_signatures(self):
        tables = self.tables("来文类型：暂停令；问题：漏缝；拟办：补充资料", "supervision")
        signatures = tables["8 签发与复查空栏"]
        self.assertEqual(["（空栏）"]*5, signatures[1][1:])
        md = build_draft("supervision", TOOLS["supervision"], "生成复工报审资料目录")
        self.assertIn("只出目录", md)
        self.assertNotIn("可以复工", md)

    def test_archive_request_preserves_user_items_and_not_ready_for_acceptance(self):
        md = build_draft("supervision", TOOLS["supervision"], "生成验收资料目录；目录条目：试验报告；资料类别：主体；资料状态：待附；用户移交资料：合同A01")
        for title in ("组卷总目录", "施工过程子目录", "独立过程夹", "闭合检查表", "移交提示"):
            self.assertIn(title, md)
        self.assertIn("试验报告", md)
        self.assertNotIn("已具备验收条件", md)

    def test_coordinate_axes_zero_precision_and_negative_literals_not_swapped(self):
        row = self.rows("点号：GPS09；X：-3456218.3470；Y：512076.5920；高程：0")[0]
        self.assertEqual("-3456218.3470", row["x"])
        self.assertEqual("512076.5920", row["y"])
        self.assertEqual("0", row["h"])
        self.assertNotIn("unit", row)

    def test_natural_axis_units_are_kept_literal_not_assumed_or_converted(self):
        row = self.rows("BM01点 北71.00mm，东92.50毫米，高程12.50mm")[0]
        self.assertEqual("71.00mm", row["x"])
        self.assertEqual("92.50毫米", row["y"])
        self.assertEqual("12.50mm", row["h"])
        self.assertNotIn("unit", row)
        self.assertNotIn("height_unit", row)

    def test_natural_original_height_not_a_generic_or_remeasured_height(self):
        row = self.rows("BM01点 原高程12.50mm，这次测的12.40mm")[0]
        self.assertEqual("12.50mm", row["original_h"])
        self.assertEqual("12.40mm", row["observed_h"])
        self.assertNotIn("h", row)

    def test_known_and_remeasured_heights_separate_and_difference_unknown(self):
        table = self.tables("点号：BM09；原高程：12.486；复测高程：12.483；用户差值：-0.003；高程单位：m")["原高程与复测高程分组 1"]
        self.assertEqual("12.486", table[1][table[0].index("用户原高程")])
        self.assertEqual("12.483", table[1][table[0].index("用户复测高程")])
        self.assertEqual([UNKNOWN, UNKNOWN], table[1][-2:])
        self.assertNotIn("-0.003", table[1])

    def test_point_datum_and_unit_differences_are_separate_tables(self):
        tables = self.tables("点号：BM01；坐标系：CGCS2000；高程基准：1985；单位：m；高程单位：m；X：71\n点号：BM02；坐标系：SVY21；高程基准：SHD；单位：mm；高程单位：mm；X：92")
        groups = [t for title, t in tables.items() if title.startswith("控制点基准分组")]
        self.assertEqual(2, len(groups))
        self.assertTrue(all(len(t) == 2 for t in groups))

    def test_next_point_does_not_borrow_local_datum_observer_or_instrument(self):
        rows = self.rows("点号：BM01；坐标系：CGCS2000；高程基准：1985；仪器：DSZ3；观测：王甲\n点号：BM02")
        self.assertEqual(2, len(rows))
        self.assertTrue(all(k not in rows[1] for k in ("coordinate_system", "height_datum", "instrument", "observer")))

    def test_explicit_task_common_fields_apply_only_to_own_task(self):
        rows = self.rows("任务：T01；任务共用仪器：DSZ3；任务共用观测人：王甲；任务高程基准：1985\n点号：BM01；原高程：12.4\n点号：BM02；观测：李乙\n任务：T02；点号：BM03")
        self.assertEqual(["DSZ3", "DSZ3"], [r["instrument"] for r in rows[:2]])
        self.assertEqual(["王甲", "李乙"], [r["observer"] for r in rows[:2]])
        self.assertNotIn("instrument", rows[2])
        self.assertNotIn("height_datum", rows[2])

    def test_implicit_task_datum_before_points_not_inherited_by_next_task(self):
        rows = self.rows("任务：T01；坐标系：CGCS2000；高程基准：1985；单位：m；点号：BM01\n任务：T02；点号：BM02")
        self.assertEqual("CGCS2000", rows[0]["coordinate_system"])
        self.assertTrue(all(k not in rows[1] for k in ("coordinate_system", "height_datum", "unit")))

    def test_later_explicit_point_group_cannot_lend_instrument_to_earlier_point(self):
        rows = self.rows("点号：BM0；原高程：8.1。两个水准点又测了一遍，BM1交桩单上高程12.5；BM2单子上是13.8，用的合成DSZ3水准仪，观测王甲。")
        self.assertEqual(3, len(rows))
        self.assertNotIn("instrument", rows[0])
        self.assertNotIn("observer", rows[0])
        self.assertTrue(all(r["instrument"] == "合成DSZ3水准仪" for r in rows[1:]))

    def test_explicit_two_point_observation_group_has_only_its_common_instrument(self):
        rows = self.rows("两个水准点又测了一遍，BM1交桩单上高程12.5，我们测出来12.4；BM2单子上是13.8，这次测的13.9，用的合成DSZ3水准仪，观测王甲。\n### 乙.csv\n点号：BM3")
        self.assertEqual(3, len(rows))
        self.assertTrue(all(r["instrument"] == "合成DSZ3水准仪" and r["observer"] == "王甲" for r in rows[:2]))
        self.assertNotIn("observer", rows[2])
        self.assertNotIn("instrument", rows[2])

    def test_station_backsight_and_point_identifier_are_not_interchangeable(self):
        row = self.rows("测站：JM2；后视：JM4；点号：P-09；图号：结施-A9")[0]
        self.assertEqual("JM2", row["station"])
        self.assertEqual("JM4", row["backsight"])
        self.assertEqual("P-09", row["point"])

    def test_unknown_coordinate_or_instrument_certificate_withdraws_value(self):
        row = self.rows("点号：BM01；X：71；X：还没测；检定来源：C01.pdf；检定来源：没带")[0]
        self.assertEqual(UNKNOWN, row["x"])
        self.assertEqual(UNKNOWN, row["calibration_source"])
        self.assertNotIn("x", row["_sources"])
        self.assertNotIn("calibration_source", row["_sources"])

    def test_instrument_expiry_is_literal_not_certified_validity(self):
        table = self.tables("点号：BM01；仪器：DSZ3；仪器编号：301234；检定有效期：2027-03-15；观测：王甲；复核：李乙")["8 仪器检定与人员"]
        self.assertEqual("2027-03-15", table[1][table[0].index("用户检定有效期")])
        self.assertEqual(UNKNOWN, table[1][-1])

    def test_survey_signatures_are_empty_even_when_user_names_given(self):
        sign = self.tables("点号：BM01；观测：王甲；复核：李乙")["11 附录与签认空栏"]
        self.assertEqual(["（空栏）"]*4, sign[1][1:])


if __name__ == "__main__":
    unittest.main()
