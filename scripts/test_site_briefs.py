#!/usr/bin/env python3
"""Source isolation and truthful safety boundaries beyond the scored cases."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packing_assistant.office_job import tables_from_md
from packing_assistant.post_drafts.site_briefs import TOOLS, UNKNOWN, SCHEME, EMERGENCY, WORKER, parse, build_draft

IDENTITIES = {"construction": ("部位", "作业部位"), "emergency": ("场景", "场景"), "worker-brief": ("班组", "班组")}
PARAMETERS = {"construction": ("支模高度", "9.2m"), "emergency": ("应急物资", "气体检测仪3台"), "worker-brief": ("防护", "用户挡火措施")}


def draft(post, text):
    return build_draft(post, TOOLS[post], text)


def rows(post, text, title):
    table = dict(tables_from_md(draft(post, text)))[title]
    return [dict(zip(table[0], row)) for row in table[1:]]


class SiteBriefTests(unittest.TestCase):
    def test_exact_tools_and_full_templates(self):
        for post, chapters in (("construction", SCHEME), ("emergency", EMERGENCY), ("worker-brief", WORKER)):
            with self.subTest(post=post):
                md = draft(post, "")
                self.assertIn("submit_blocked=true", md)
                self.assertIn("辖区：UNSPECIFIED", md)
                self.assertNotIn("## 用户原文", md)
                for index, title in enumerate(chapters, 1):
                    self.assertIn(f"## {index} {title}", md)
                self.assertIsNone(build_draft(post, "hr-labor__check", ""))

    def test_same_named_files_never_lend_parameters_or_basis(self):
        for post, (alias, key) in IDENTITIES.items():
            with self.subTest(post=post):
                parameter, value = PARAMETERS[post]
                a, b = parse(f"【用户上传：同名.csv】\n项目：甲；辖区：CN；依据：规则甲\n{alias}：对象甲；{parameter}：{value}；资料来源：甲原件\n"
                             f"【用户上传：同名.csv】\n项目：乙\n{alias}：对象乙", post)
                self.assertNotEqual(a["_file"], b["_file"])
                self.assertEqual("甲", a["项目名称"])
                self.assertEqual("乙", b["项目名称"])
                self.assertNotIn(parameter, b)
                self.assertNotIn("资料来源", b)
                self.assertNotIn("适用依据", b)
                self.assertEqual(UNKNOWN, b["_project_zone"])
                self.assertIn("同名.csv · 输入第3行", a["_sources"][parameter])

    def test_repeated_identity_is_independent(self):
        for post, (alias, key) in IDENTITIES.items():
            with self.subTest(post=post):
                parameter, value = PARAMETERS[post]
                a, b = parse(f"{alias}：甲；{parameter}：{value}\n{alias}：甲", post)
                self.assertEqual(value, a[parameter])
                self.assertNotIn(parameter, b)

    def test_late_project_and_stage_do_not_relabel_previous_object(self):
        for post, (alias, key) in IDENTITIES.items():
            with self.subTest(post=post):
                a, b, c = parse(f"{alias}：甲\n项目：后来项目；资料阶段：初步\n{alias}：乙\n资料阶段：深化\n{alias}：乙", post)
                self.assertNotIn("项目名称", a)
                self.assertEqual("后来项目", b["项目名称"])
                self.assertEqual("初步", b["资料阶段"])
                self.assertEqual("深化", c["资料阶段"])

    def test_country_in_identity_or_file_does_not_set_zone(self):
        for post, (alias, key) in IDENTITIES.items():
            with self.subTest(post=post):
                md = draft(post, f"{alias}：新加坡对象；资料来源：中国资料.pdf")
                self.assertIn("辖区：UNSPECIFIED", md)
                self.assertNotIn("第37号", md)
                self.assertNotIn("SCDF Emergency", md)

    def test_dual_retains_explicit_country_bases_and_local_zone(self):
        for post, (alias, key) in IDENTITIES.items():
            with self.subTest(post=post):
                a, b = parse(f"项目：甲；总体辖区：DUAL；CN依据：CN资料；EU依据：EU资料\n{alias}：甲；辖区：EU\n{alias}：乙", post)
                self.assertEqual("EU", a["辖区"])
                self.assertEqual("EU资料", a["EU依据"])
                self.assertNotIn("SG依据", a)
                self.assertNotIn("辖区", b)

    def test_local_zone_cannot_borrow_unspecified_other_country_rule(self):
        row = parse("辖区：CN；依据：CN资料\n部位：A；辖区：SG", "construction")[0]
        self.assertNotIn("适用依据", row)

    def test_matrix_column_permutations_preserve_blank_identity_and_scope(self):
        for post, (alias, key) in IDENTITIES.items():
            parameter, value = PARAMETERS[post]
            for headers, first, second in (
                ([parameter, alias, "项目", "资料阶段", "总体辖区"], [value, "甲", "项目甲", "初步", "CN"], ["", "", "项目乙", "深化", ""]),
                (["项目", "资料阶段", "总体辖区", alias, parameter], ["项目甲", "初步", "CN", "甲", value], ["项目乙", "深化", "", "", ""]),
            ):
                with self.subTest(post=post, headers=headers):
                    text = "|"+"|".join(headers)+"|\n|"+"|".join("---" for _ in headers)+"|\n|"+"|".join(first)+"|\n|"+"|".join(second)+"|"
                    result = parse(text, post)
                    self.assertEqual(2, len(result))
                    self.assertEqual(value, result[0][parameter])
                    self.assertEqual(UNKNOWN, result[1][key])
                    self.assertEqual(UNKNOWN, result[1][parameter])
                    self.assertEqual("项目乙", result[1]["项目名称"])
                    self.assertEqual("深化", result[1]["资料阶段"])
                    self.assertEqual(UNKNOWN, result[1]["_project_zone"])

    def test_same_project_matrix_zones_do_not_relabel_earlier_row(self):
        for post, (alias, key) in IDENTITIES.items():
            with self.subTest(post=post):
                a, b = parse(f"|项目|{alias}|总体辖区|\n|---|---|---|\n|甲|对象甲|CN|\n|甲|对象乙|SG|", post)
                self.assertEqual(["CN", "SG"], [r["_project_zone"] for r in (a, b)])

    def test_unknown_update_clears_parameter_and_source_position(self):
        for post, (alias, key) in IDENTITIES.items():
            parameter = "支模高度" if post == "construction" else "应急物资" if post == "emergency" else "安全员"
            row = parse(f"{alias}：甲；{parameter}：原值\n{parameter}：还没定", post)[0]
            self.assertEqual(UNKNOWN, row[parameter])
            self.assertNotIn(parameter, row["_sources"])
            self.assertIn("原值", row["待补资料"])

    def test_scenario_does_not_promote_fact_before_actual_reset(self):
        for post, (alias, key) in IDENTITIES.items():
            with self.subTest(post=post):
                result = parse(f"例如{alias}：假对象\n{alias}：仍是假设\n真实记录：{alias}：真实对象", post)
                self.assertEqual(1, len(result))
                self.assertEqual("真实对象", result[0][key])

    def test_entities_decode_once_and_keep_physical_columns(self):
        for post, (alias, key) in IDENTITIES.items():
            with self.subTest(post=post):
                text = f"|{alias}|资料来源|\n|---|---|\n|=A&#124;B &lt; 9|字面&amp;lt;|"
                row = parse(text, post)[0]
                self.assertEqual("=A|B < 9", row[key])
                self.assertEqual("字面&lt;", row["资料来源"])
                self.assertNotIn("<script>", draft(post, f"{alias}：<script>"))

    def test_construction_dimensions_sources_and_no_computation(self):
        text = "部位：A；支模高度：9.2m按结施-21；板厚：0mm；梁截面：500×1200；跨度：3×21m"
        row = rows("construction", text, "施工参数与现场条件")[0]
        self.assertEqual("9.2m", row["支模高度"])
        self.assertEqual("0mm", row["板厚"])
        self.assertEqual("500×1200", row["梁截面"])
        self.assertEqual("3×21m", row["跨度"])
        self.assertNotIn("63m", draft("construction", text))
        self.assertEqual("结施-21", parse(text, "construction")[0]["图纸"])

    def test_unique_late_construction_reference_does_not_change_other_work(self):
        text = "部位：2#楼地下室；支模高度：5m\n部位：裙楼中庭；支模高度：11m\n中庭归赵明盯\n地下室这边谁管还没定"
        a, b = parse(text, "construction")
        self.assertEqual(UNKNOWN, a["施工员"])
        self.assertEqual("赵明", b["施工员"])
        self.assertNotIn("5m", str(b))

    def test_ambiguous_and_cross_file_reference_never_selects_one(self):
        for prefix in ("部位：北中庭\n部位：南中庭\n",
                       "【用户上传：甲.md】\n部位：中庭\n【用户上传：乙.md】\n"):
            with self.subTest(prefix=prefix):
                result = parse(prefix+"中庭归赵明盯", "construction")
                self.assertFalse(any("作业部位" in r and r.get("施工员") == "赵明" for r in result))
                self.assertEqual("赵明", result[-1]["施工员"])

    def test_construction_plan_date_is_not_implemented_or_approved(self):
        md = draft("construction", "部位：A；计划开始：2026-12-01；验收记录：用户称已验收")
        self.assertIn("2026-12-01", md)
        row = rows("construction", "部位：A；验收记录：用户称已验收", "验收与资料待核")[0]
        self.assertEqual(UNKNOWN, row["验收结论"])
        self.assertEqual("", row["签认"])

    def test_emergency_planned_drill_is_separate_from_actual_record(self):
        text = "场景：坑甲；演练时间：2026-12-01；演练科目：撤离；应急物资：担架2副"
        plan = rows("emergency", text, "拟演练计划")[0]
        actual = rows("emergency", text, "用户实际演练记录待核")[0]
        self.assertEqual("2026-12-01", plan["演练时间"])
        self.assertEqual(UNKNOWN, actual["实际演练日期"])
        self.assertEqual(UNKNOWN, actual["演练记录"])
        self.assertEqual(UNKNOWN, actual["本轮评估结论"])
        self.assertEqual("", actual["签认"])

    def test_emergency_resources_and_people_are_not_borrowed_between_scenarios(self):
        a, b = parse("场景：坑甲；应急物资：担架2副；现场处置负责人：张工；演练时间：11月8号\n场景：起重", "emergency")
        for key in ("应急物资", "现场处置负责人", "演练时间"):
            self.assertIn(key, a)
            self.assertNotIn(key, b)

    def test_emergency_future_or_unknown_phone_hospital_and_proof_stay_pending(self):
        row = parse("场景：坑甲；电话：还没统计；医院：没有统计；演练记录：计划提供记录", "emergency")[0]
        for key in ("电话", "医院", "演练记录"):
            self.assertEqual(UNKNOWN, row[key])
        self.assertNotRegex(draft("emergency", "场景：坑甲"), r"\b(?:995|999|120|119)\b")

    def test_emergency_user_actual_record_does_not_prove_success(self):
        text = "场景：坑甲；实际演练日期：2026-09-08；演练记录：记录甲；发现问题：联络延误"
        row = rows("emergency", text, "用户实际演练记录待核")[0]
        self.assertEqual("2026-09-08", row["实际演练日期"])
        self.assertEqual("记录甲", row["演练记录"])
        self.assertEqual(UNKNOWN, row["本轮评估结论"])

    def test_worker_people_list_is_not_a_stop_condition(self):
        text = "班组：架子工班；喊停：班组长周明；安全员李慧；出事先撤到南门集合点"
        row = rows("worker-brief", text, "喊停与报告对象")[0]
        self.assertEqual("周明", row["带班"])
        self.assertEqual("李慧", row["安全员"])
        self.assertEqual(UNKNOWN, row["喊停"])
        self.assertEqual("南门集合点", row["撤离点"])
        self.assertNotIn("遇到班组长周明", draft("worker-brief", text))

    def test_worker_unavailable_safety_officer_is_not_named_as_present(self):
        text = "班组：焊工班；安全员：老周\n安全员老周说他明天不在场"
        row = parse(text, "worker-brief")[0]
        self.assertEqual(UNKNOWN, row["安全员"])
        spoken = draft("worker-brief", text).split("## 班前口播", 1)[1].split("## 对象依据", 1)[0]
        self.assertNotIn("老周", spoken)

    def test_worker_number_without_source_is_not_an_operating_threshold(self):
        text = "班组：架子工班；用户尺寸：1200mm"
        md = draft("worker-brief", text)
        self.assertIn("1200mm", md)
        spoken = md.split("## 班前口播", 1)[1].split("## 对象依据", 1)[0]
        self.assertNotIn("1200mm", spoken)
        self.assertNotIn("毫米", spoken)

    def test_worker_never_adds_unnamed_hazards_or_human_names(self):
        text = "班组：泥工班；部位：北区；今天作业：砌墙"
        spoken = draft("worker-brief", text).split("## 班前口播", 1)[1].split("## 对象依据", 1)[0]
        self.assertNotIn("动火", spoken)
        self.assertNotIn("吊装", spoken)
        self.assertNotIn("有限空间", spoken)
        self.assertIn("姓名待填", spoken)

    def test_metadata_only_uses_latest_file(self):
        for post in TOOLS:
            with self.subTest(post=post):
                row = parse("【用户上传：甲.md】\n项目：甲\n【用户上传：乙.md】\n项目：乙；辖区：EU", post)[0]
                self.assertEqual("乙", row["项目名称"])
                self.assertEqual("EU", row["_project_zone"])
                self.assertEqual("乙.md", row["_origin"])

    def test_construction_intro_is_not_a_project_name(self):
        for text in ("一块是2#楼地下室顶板，架子高5.7", "另一块3#楼屋面，支模高度9.3m"):
            with self.subTest(text=text):
                row = parse(text, "construction")[0]
                self.assertNotIn("项目名称", row)

    def test_natural_uncertain_height_does_not_promote_numeric_fragment(self):
        for text in ("部位：A；支模高度：9.3m\n高度还没量", "部位：A\n支模高度9.3m还没量"):
            with self.subTest(text=text):
                row = parse(text, "construction")[0]
                self.assertEqual(UNKNOWN, row["支模高度"])

    def test_late_unknown_planned_drill_date_clears_previous_date(self):
        row = parse("场景：A；演练时间：11月8号\n演练时间：还没排", "emergency")[0]
        self.assertEqual(UNKNOWN, row["演练时间"])

    def test_planned_resources_are_not_listed_as_on_site_inventory(self):
        text = "场景：A；应急物资：计划采购担架2副；抢险队伍：拟组建8人队伍"
        row = rows("emergency", text, "风险场景与现场资源")[0]
        self.assertEqual(UNKNOWN, row["应急物资"])
        self.assertEqual(UNKNOWN, row["抢险队伍"])
        self.assertEqual("计划采购担架2副", row["拟应急物资"])
        self.assertEqual("拟组建8人队伍", row["拟抢险队伍"])

    def test_natural_future_resources_are_not_on_site_inventory(self):
        row = parse("场景：A\n预计现场备了担架2副", "emergency")[0]
        self.assertNotIn("应急物资", row)
        self.assertEqual("担架2副", row["拟应急物资"])

    def test_user_says_resources_prepared_remains_unverified_not_future_plan(self):
        row = parse("场景：A；应急物资：准备了担架2副", "emergency")[0]
        self.assertEqual("准备了担架2副", row["应急物资"])
        self.assertNotIn("拟应急物资", row)

    def test_other_named_crew_and_its_literal_task_are_not_an_empty_template(self):
        row = parse("幕墙班今天上午在8#楼十九层安装立柱，紧挨着洞口，得铺防护毯", "worker-brief")[0]
        self.assertEqual("幕墙班", row["班组"])
        self.assertEqual("8#楼十九层", row["部位"])
        self.assertEqual("安装立柱", row["今天作业"])
        self.assertEqual("洞口", row["危险点"])
        self.assertEqual("铺防护毯", row["防护"])

    def test_other_explicit_frame_type_is_not_replaced_by_pankou(self):
        row = parse("部位：A\n架体采用扣件式", "construction")[0]
        self.assertEqual("扣件式", row["架体"])
        self.assertNotIn("盘扣", draft("construction", "部位：A\n架体采用扣件式"))

    def test_natural_support_form_is_only_user_transcription(self):
        row = parse("部位：坑甲\n支护形式为排桩加内支撑", "construction")[0]
        self.assertEqual("排桩加内支撑", row["支护形式"])

    def test_natural_planned_purchase_stays_a_plan(self):
        row = parse("场景：坑甲\n计划采购检测仪3台", "emergency")[0]
        self.assertEqual("检测仪3台", row["拟应急物资"])
        self.assertNotIn("应急物资", row)

    def test_short_worker_task_preserves_named_hazard_and_literal_size(self):
        for text in ("写一份班前白话 临边 1200mm", "班前白话：今天三层临边防护，不报未给的尺寸。"):
            with self.subTest(text=text):
                md = draft("worker-brief", text)
                self.assertIn("临边", md)
                if "1200mm" in text:
                    self.assertIn("1200mm", md)
                    spoken = md.split("## 班前口播", 1)[1].split("## 对象依据", 1)[0]
                    self.assertNotIn("1200mm", spoken)

    def test_negated_worker_hazard_does_not_become_a_positive_keyword(self):
        row = parse("班组：泥工班\n没有临边作业", "worker-brief")[0]
        self.assertNotIn("危险点", row)


if __name__ == "__main__":
    unittest.main()
