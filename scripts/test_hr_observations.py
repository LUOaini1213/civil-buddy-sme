#!/usr/bin/env python3
"""Independent HR ownership/plan/result regressions; no model or law lookup."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packing_assistant.post_drafts.hr_observations import TOOLS, UNKNOWN, build_draft, parse_records
from packing_assistant.office_job import tables_from_md


class HROwnershipTests(unittest.TestCase):
    def rows(self, text, post="hr-labor"):
        return parse_records(text, post)

    def draft(self, text, post="hr-labor"):
        return build_draft(post, TOOLS[post], text)

    def test_exact_tools_only_and_all_templates(self):
        for post, tool in TOOLS.items():
            with self.subTest(post=post):
                md = build_draft(post, tool, "")
                self.assertIn("submit_blocked=true", md)
                self.assertIn("AI 草稿", md)
                self.assertNotIn("## 用户原文", md)
                for wrong in set(TOOLS.values())-{tool}:
                    self.assertIsNone(build_draft(post, wrong, ""))

    def test_file_epochs_even_with_same_name_never_share_worker_or_pay(self):
        rows = self.rows("### 同名.csv\n项目：甲；劳动者：王甲；工资：7000；依据：甲制度\n### 同名.csv\n劳动者：王乙")
        self.assertEqual(2, len(rows))
        self.assertEqual("7000", rows[0]["wage"])
        self.assertNotIn("wage", rows[1])
        self.assertNotIn("project", rows[1])
        self.assertNotIn("basis", rows[1])
        self.assertNotEqual(rows[0]["_file"], rows[1]["_file"])

    def test_late_contract_identifier_enriches_not_duplicates(self):
        rows = self.rows("劳动者：王甲；工资：0；合同编号：L-1；用人单位：单位甲")
        self.assertEqual(1, len(rows))
        self.assertEqual("L-1", rows[0]["contract_id"])
        self.assertEqual("0", rows[0]["wage"])

    def test_repeated_contracts_and_workers_independent(self):
        rows = self.rows("合同编号：L-1；劳动者：甲；工资：7100\n合同编号：L-2；劳动者：乙；合同期限：两年")
        self.assertEqual(2, len(rows))
        self.assertNotIn("wage", rows[1])
        self.assertNotIn("term", rows[0])

    def test_project_and_stage_changes_freeze_older_records(self):
        rows = self.rows("项目：甲；资料阶段：初稿；劳动者：王甲；工资：7100\n资料阶段：复核；劳动者：王乙\n项目：乙；劳动者：王丙")
        self.assertEqual(["甲", "甲", "乙"], [r["project"] for r in rows])
        self.assertEqual(["初稿", "复核"], [r["stage"] for r in rows[:2]])
        self.assertNotIn("stage", rows[2])

    def test_contract_type_before_project_does_not_create_orphan(self):
        rows = self.rows("合同类型：劳动合同；项目：甲；劳动者：王甲；工资：7100")
        self.assertEqual(1, len(rows))
        self.assertEqual("劳动合同", rows[0]["contract_type"])

    def test_reordered_matrix_and_fully_blank_short_rows_are_objects(self):
        rows = self.rows("| 工资 | 劳动者 | 合同编号 |\n| --- | --- | --- |\n| 7100 | 甲 | L1 |\n| 9200 | | |\n|||\n|")
        self.assertEqual(4, len(rows))
        self.assertEqual(UNKNOWN, rows[1]["worker"])
        self.assertEqual("9200", rows[1]["wage"])
        self.assertEqual(UNKNOWN, rows[2]["wage"])
        self.assertEqual(UNKNOWN, rows[3]["contract_id"])

    def test_matrix_metadata_keeps_one_record_and_order_independent(self):
        rows = self.rows("| 工资 | 项目 | 劳动者 |\n|---|---|---|\n|7100|甲|王甲|\n|9200|乙|王乙|")
        self.assertEqual(2, len(rows))
        self.assertEqual(["甲", "乙"], [r["project"] for r in rows])

    def test_label_table_with_empty_first_cell_is_not_shifted(self):
        md = self.draft("||工资|7100|")
        self.assertNotIn("7100", md)

    def test_basis_scope_and_local_country_override(self):
        rows = self.rows("项目：甲；总体辖区：DUAL；依据：总体制度；CN依据：中国版；SG依据：新加坡版\n劳动者：王甲；辖区：CN\n劳动者：王乙；辖区：SG")
        self.assertEqual(["CN", "SG"], [r["jurisdiction"] for r in rows])
        self.assertTrue(all("basis" not in r for r in rows))
        self.assertTrue(all(r["CN_basis"] == "中国版" and r["SG_basis"] == "新加坡版" for r in rows))

    def test_late_bare_country_is_only_current_object(self):
        rows = self.rows("劳动者：王甲\nSG\n劳动者：王乙")
        self.assertEqual("SG", rows[0]["jurisdiction"])
        self.assertNotIn("jurisdiction", rows[1])
        self.assertEqual(UNKNOWN, rows[1]["_project_zone"])

    def test_country_not_inferred_from_company_ministry_or_filename(self):
        for text in ("项目：新加坡楼；用人单位：中国建筑；劳动者：王甲", "### SG劳动合同.pdf\n劳动者：王甲", "写招聘简报 施工员 住建部"):
            self.assertIn("辖区：UNSPECIFIED", self.draft(text))

    def test_unknown_pay_clears_old_value_and_provenance(self):
        row = self.rows("劳动者：王甲；工资：7100；工资：还没定")[0]
        self.assertEqual(UNKNOWN, row["wage"])
        self.assertNotIn("wage", row["_sources"])
        self.assertIn("7100", row["pending"])
        md = self.draft("劳动者：王甲；工资：7100；工资：还没定")
        section = md.split("## 3 劳动合同", 1)[1].split("## 4", 1)[0]
        self.assertNotIn("7100", section)

    def test_natural_multi_worker_parsing_keeps_unknown_second(self):
        rows = self.rows("一个是焊工赵明，月工资9100，2026-11-02进的场；另一个是电工钱慧，走的非全日制，一天干五小时，按小时给钱具体多少还没定。")
        self.assertEqual(["赵明", "钱慧"], [r["worker"] for r in rows])
        self.assertEqual("9100", rows[0]["wage"])
        self.assertEqual(UNKNOWN, rows[1]["wage"])
        self.assertEqual("五小时", rows[1]["hours"])
        self.assertNotIn("start_date", rows[1])

    def test_literal_years_and_money_never_calculated(self):
        md = self.draft("劳动者：王甲；工作年限：4.5年；月工资基数：8100元；请计算补偿")
        self.assertNotIn("36450", md)
        self.assertIn("| 经济补偿 | [A001] |", md)

    def test_contract_service_money_and_labor_pay_not_assigned_across_types(self):
        md = self.draft("劳动者：王甲；工资：7100；劳务接受方：甲方；劳务提供方：乙方；服务费：13000")
        labor = md.split("## 3", 1)[1].split("## 4", 1)[0]
        service = md.split("## 4", 1)[1].split("## 5", 1)[0]
        self.assertNotIn("13000", labor)
        self.assertNotIn("7100", service)

    def test_recruit_two_roles_share_only_explicit_verification(self):
        rows = self.rows("施工员1个，生产部要的，月薪谈到8500；资料员归资料室管，要2个人，到岗时间和工资都还没批。两个岗入职前都要查身份证和毕业证原件。", "hr-recruit")
        self.assertEqual(2, len(rows))
        self.assertEqual("8500", rows[0]["salary"])
        self.assertEqual(UNKNOWN, rows[1]["salary"])
        self.assertTrue(all("毕业证" in r["verification"] for r in rows))

    def test_recruit_shared_conditions_do_not_cross_files(self):
        rows = self.rows("### 甲.txt\n岗位：施工员\n### 乙.txt\n岗位：资料员。两个岗入职前都要查毕业证。", "hr-recruit")
        self.assertNotIn("verification", rows[0])
        self.assertEqual("毕业证", rows[1]["verification"])

    def test_recruit_labeled_wage_and_missing_salary(self):
        row = self.rows("岗位：资料员；月薪：8100；薪资：未批", "hr-recruit")[0]
        self.assertEqual(UNKNOWN, row["salary"])
        md = self.draft("岗位：资料员", "hr-recruit")
        self.assertIn("待填 / UNSPECIFIED", md)
        self.assertNotRegex(md, r"\d+元|\d+岁")

    def test_recruit_discriminatory_or_hanging_certificate_demands_not_requirements(self):
        for text in ("男性优先", "35岁以下", "只招本地", "挂证"):
            md = self.draft("岗位：资料员；任职："+text, "hr-recruit")
            self.assertNotIn(text, md)
            self.assertIn("未转为招聘要求", md)

    def test_recruit_literal_requirements_and_rotation_are_fields(self):
        row = self.rows("岗位：测量员；任职：3年经验，持岗位证明；工作地点：A项目，双周轮休", "hr-recruit")[0]
        self.assertEqual("双周轮休", row["rotation"])
        self.assertIn("3年", row["qualification"])
        self.assertEqual("岗位证明", row["certificate"])

    def test_level_date_teacher_place_owned_independently(self):
        rows = self.rows("这批新进场的是木工班9个人，公司级10月21号在培训室由王明讲，项目级10月22号讲洞口防护，班组级10月23号由班组长讲木工机具安全。", "hr-train")
        row = rows[0]
        self.assertEqual("10月21号", row["company_date"])
        self.assertEqual("王明", row["company_teacher"])
        self.assertEqual("培训室", row["company_place"])
        self.assertNotIn("project_teacher", row)
        self.assertEqual("洞口防护", row["project_topic"])
        self.assertEqual("木工机具安全", row["team_topic"])

    def test_multi_training_groups_do_not_share_specialist_teacher_or_date(self):
        rows = self.rows("塔吊司机和信号工那拨13个人，要的是特种作业证复审前再教育，9月29号下午讲，讲师请了市里的赵明；另一拨是刚进场的抹灰班21个人，班组级班组长自己讲抹灰机具安全操作。", "hr-train")
        self.assertEqual(2, len(rows))
        self.assertEqual("赵明", rows[0]["teacher"])
        self.assertNotIn("teacher", rows[1])
        self.assertNotIn("training_date", rows[1])
        self.assertNotIn("special_topic", rows[1])

    def test_training_matrix_blank_audience_does_not_receive_previous_teacher(self):
        rows = self.rows("| 培训对象 | 讲师 | 培训日期 |\n|---|---|---|\n|班甲|王甲|2026-11-01|\n||王乙||", "hr-train")
        self.assertEqual(2, len(rows))
        self.assertEqual(UNKNOWN, rows[1]["audience"])
        self.assertEqual("王乙", rows[1]["teacher"])
        self.assertEqual(UNKNOWN, rows[1]["training_date"])

    def test_plan_dates_and_hours_are_not_actual_training_or_signatures(self):
        md = self.draft("培训对象：班甲；培训日期：2026-11-01；公司级学时：6小时；参训人员：王甲、王乙；讲师：李甲", "hr-train")
        sign = md.split("## 6", 1)[1].split("## 7", 1)[0]
        self.assertEqual(18, sign.count("（空栏）"))
        for value in ("王甲", "王乙", "李甲", "2026-11-01", "6小时"):
            self.assertNotIn(value, sign)
        actual = dict(tables_from_md(md))["用户实际培训材料（摘录待核，不作完成结论）"]
        self.assertEqual([UNKNOWN]*6, actual[1])

    def test_actual_user_record_remains_unverified_without_result(self):
        md = self.draft("培训对象：班甲；实际培训日期：2026-11-02；实际学时：5小时；签到记录：现场签到扫描件", "hr-train")
        actual = dict(tables_from_md(md))["用户实际培训材料（摘录待核，不作完成结论）"]
        self.assertEqual("2026-11-02", actual[1][0])
        self.assertEqual(UNKNOWN, actual[1][-1])
        self.assertNotIn("考核已合格", md)

    def test_future_records_are_not_actual(self):
        row = self.rows("培训对象：班甲；实际培训日期：计划11月2日；签到记录：拟提供扫描件", "hr-train")[0]
        self.assertEqual(UNKNOWN, row["actual_date"])
        self.assertEqual(UNKNOWN, row["attendance_source"])

    def test_unknown_teacher_update_clears_source(self):
        row = self.rows("培训对象：班甲；讲师：王明；讲师：未定", "hr-train")[0]
        self.assertEqual(UNKNOWN, row["teacher"])
        self.assertNotIn("teacher", row["_sources"])

    def test_names_containing_example_word_are_literal_names(self):
        row = self.rows("雇主：示例雇主；劳动者：张示例；工资：7100")[0]
        self.assertEqual("示例雇主", row["employer"])
        self.assertEqual("张示例", row["worker"])
        row = self.rows("参训人员：张示例、李示例；讲师：王示例", "hr-train")[0]
        self.assertEqual("王示例", row["teacher"])

    def test_scenario_persists_until_actual_reset_and_not_next_file(self):
        rows = self.rows("假设有劳动者：假人，工资：99999。劳动者：仍是假人\n真实记录：劳动者：王甲；工资：7100\n### 乙.txt\n劳动者：王乙")
        self.assertEqual(["王甲", "王乙"], [r["worker"] for r in rows])
        self.assertNotIn("99999", self.draft("假设有劳动者：假人，工资：99999。真实记录：劳动者：王甲"))

    def test_entities_decode_once_after_cell_split_and_formula_literal(self):
        rows = self.rows("| 劳动者 | 工资 |\n|---|---|\n|甲 &amp;amp; 乙&#124;丙|=1+2|\n|丁|0|")
        self.assertEqual("甲 &amp; 乙|丙", rows[0]["worker"])
        self.assertEqual("=1+2", rows[0]["wage"])
        self.assertEqual("0", rows[1]["wage"])
        self.assertNotIn("<script>", self.draft("劳动者：<script>王甲</script>"))

    def test_english_matrix_keeps_its_own_people_and_pay(self):
        rows = self.rows("| Wage | Employee | Employer |\n|---|---|---|\n|0|Ann|A Co|\n|8200|Bob||")
        self.assertEqual(["Ann", "Bob"], [r["worker"] for r in rows])
        self.assertEqual(UNKNOWN, rows[1]["employer"])

    def test_only_explicit_salary_not_a_market_estimate(self):
        for value in ("估计8100", "市场行情8100", "大概8100"):
            self.assertEqual(UNKNOWN, self.rows("岗位：资料员；薪资："+value, "hr-recruit")[0]["salary"])

    def test_declared_training_level_owns_generic_date_hours_and_teacher(self):
        row = self.rows("培训对象：班甲；层级：公司级；培训日期：2026-11-02；讲师：王明；课题：企业制度；计划学时：6学时", "hr-train")[0]
        self.assertEqual("王明", row["company_teacher"])
        self.assertEqual("6学时", row["company_hours"])
        self.assertNotIn("teacher", row)
        self.assertNotIn("project_teacher", row)

    def test_reordered_level_matrix_does_not_lend_company_teacher(self):
        rows = self.rows("|讲师|层级|培训对象|培训日期|\n|---|---|---|---|\n|王明|公司级|班甲|2026-11-02|\n||项目级|班乙||", "hr-train")
        self.assertEqual("王明", rows[0]["company_teacher"])
        self.assertEqual(UNKNOWN, rows[1]["project_teacher"])
        self.assertNotIn("company_teacher", rows[1])

    def test_negated_type_does_not_assign_ambiguous_remuneration_to_labor_wage(self):
        md = self.draft("劳动者：王甲；合同类型：不是劳动合同；报酬：8100")
        labor = md.split("## 3", 1)[1].split("## 4", 1)[0]
        self.assertNotIn("8100", labor)

    def test_labor_employment_type_label_is_user_contract_type_not_recruitment(self):
        row = self.rows("劳动者：王甲；用工类型：非全日制；工时：五小时")[0]
        self.assertEqual("非全日制", row["contract_type"])
        self.assertNotIn("employment_type", row)


if __name__ == "__main__":
    unittest.main()
