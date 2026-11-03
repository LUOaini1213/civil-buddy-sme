#!/usr/bin/env python3
"""Literal hazard facts and authority boundaries, independent of the benchmark."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packing_assistant.office_job import tables_from_md
from packing_assistant.post_drafts.hazard import TOOL, UNKNOWN, build_draft, parse


def draft(text):
    return build_draft("method-hazard", TOOL, text)


def rows(text, title):
    table = dict(tables_from_md(draft(text)))[title]
    return [dict(zip(table[0], row)) for row in table[1:]]


class HazardDraftTests(unittest.TestCase):
    def test_exact_tool_and_always_pending_authority(self):
        self.assertIsNone(build_draft("method-hazard", "bim-coord__clash", ""))
        self.assertIsNone(build_draft("structure", TOOL, ""))
        for text in ("", "作业：高支模；支模高度：11m；适用依据：用户规则",
                     "作业：基坑；已有判定：已认定危大"):
            with self.subTest(text=text):
                md = draft(text)
                self.assertIn("submit_blocked=true", md)
                conclusions = rows(text, "7 结论待判与方案交接")
                for row in conclusions:
                    self.assertEqual(["信息不足"]*3, [row[k] for k in ("是否危大", "是否超规模", "要否论证 / PTW")])
                    self.assertEqual("", row["签认"])

    def test_lifting_weight_is_not_crane_capacity(self):
        text = "作业：钢梁整体吊装；单件重量：9.7吨；起重设备：70吨汽车吊；额定起重量：70吨；吊装高度：23.4米"
        row = rows(text, "3 尺寸与触发事实")[0]
        self.assertEqual("9.7吨", row["单件重量"])
        self.assertEqual("70吨汽车吊", row["起重设备"])
        self.assertEqual("70吨", row["额定起重量"])
        self.assertEqual("23.4米", row["吊装高度"])
        self.assertEqual(UNKNOWN, row["支模高度"])

    def test_natural_height_span_load_and_source_are_separate(self):
        text = "一个是3#楼地下室顶板高支模，支模高度10.7米，最大跨度19.3米，施工总荷载算下来是14.2kN/m²，尺寸都是从结施-H07图看的"
        row = rows(text, "3 尺寸与触发事实")[0]
        self.assertEqual("10.7米", row["支模高度"])
        self.assertEqual("19.3米", row["跨度"])
        self.assertEqual("14.2kN/m²", row["施工总荷载"])
        self.assertIn("结施-H07", row["尺寸来源"])

    def test_pit_source_does_not_become_depth(self):
        text = "作业：基坑甲；开挖深度：6.8m 按基坑支护图JK-13"
        row = rows(text, "3 尺寸与触发事实")[0]
        self.assertEqual("6.8m", row["开挖深度"])
        self.assertEqual("基坑支护图JK-13", row["尺寸来源"])

    def test_units_zero_and_expression_are_literal_not_computed(self):
        text = "作业：尺寸甲；高度：0mm；跨度：3×21米；施工总荷载：12.5kN/㎡"
        row = rows(text, "3 尺寸与触发事实")[0]
        self.assertEqual("0mm", row["高度"])
        self.assertEqual("3×21米", row["跨度"])
        self.assertEqual("12.5kN/㎡", row["施工总荷载"])
        self.assertNotIn("63米", draft(text))

    def test_repeated_work_names_do_not_lend_dimensions(self):
        a, b = parse("作业：坑甲；开挖深度：7m；尺寸来源：JK-A\n作业：坑甲；支护形式：排桩")
        self.assertEqual("7m", a["开挖深度"])
        self.assertNotIn("开挖深度", b)
        self.assertNotIn("尺寸来源", b)

    def test_late_and_repeated_work_ids_are_not_work_names(self):
        a, b = parse("作业：坑甲；开挖深度：7m；作业编号：HA-9\n作业：坑乙；作业编号：HA-9")
        self.assertEqual(("坑甲", "HA-9"), (a["作业名称"], a["作业编号"]))
        self.assertEqual(("坑乙", "HA-9"), (b["作业名称"], b["作业编号"]))
        self.assertNotIn("开挖深度", b)

    def test_same_named_files_are_separate_epochs(self):
        text = ("【用户上传：同名.md】\n项目：甲；辖区：CN；适用依据：CN用户规则\n作业：坑甲；开挖深度：7m；尺寸来源：JK-A\n"
                "【用户上传：同名.md】\n项目：乙\n作业：坑乙")
        a, b = parse(text)
        self.assertNotEqual(a["_file"], b["_file"])
        self.assertEqual("甲", a["项目名称"])
        self.assertEqual("乙", b["项目名称"])
        self.assertNotIn("适用依据", b)
        self.assertNotIn("7m", str(b))
        self.assertEqual(UNKNOWN, b["_project_zone"])
        self.assertIn("同名.md · 输入第3行", a["_sources"]["尺寸来源"])

    def test_project_and_stage_change_reset_active_work(self):
        text = "项目：甲；设计阶段：初步\n作业：坑甲；开挖深度：7m\n设计阶段：施工图\n作业：坑甲\n项目：乙\n作业：坑甲"
        a, b, c = parse(text)
        self.assertEqual(["初步", "施工图"], [r["设计阶段"] for r in (a, b)])
        self.assertNotIn("开挖深度", b)
        self.assertEqual("乙", c["项目名称"])
        self.assertNotIn("设计阶段", c)

    def test_late_project_does_not_relabel_previous_record(self):
        a, b = parse("作业：坑甲\n项目：后来项目\n作业：坑乙")
        self.assertNotIn("项目名称", a)
        self.assertEqual("后来项目", b["项目名称"])

    def test_local_jurisdiction_override_drops_unscoped_other_basis(self):
        a, b = parse("项目：甲；辖区：CN；适用依据：CN用户规则\n作业：甲；辖区：SG\n作业：乙")
        self.assertNotIn("适用依据", a)
        self.assertEqual("CN用户规则", b["适用依据"])
        self.assertNotIn("辖区", b)

    def test_dual_is_explicit_and_not_an_automatic_rule_selection(self):
        text = "总体辖区：DUAL；CN依据：用户CN规则；EU依据：用户EU规则\n作业：甲；辖区：EU"
        row = rows(text, "2 辖区与依据来源")[0]
        self.assertEqual("EU", row["辖区"])
        self.assertEqual("用户CN规则", row["CN依据"])
        self.assertEqual("用户EU规则", row["EU依据"])
        self.assertEqual(UNKNOWN, row["SG依据"])
        self.assertEqual(UNKNOWN, row["适用依据"])

    def test_country_in_work_or_source_does_not_set_zone(self):
        md = draft("作业：新加坡吊装；尺寸来源：中国图纸.pdf")
        self.assertIn("- 辖区：UNSPECIFIED", md)
        self.assertNotIn("37 号令", md)

    def test_natural_human_reference_targets_only_unique_local_work(self):
        text = "作业：北侧脚手架；架体高度：26m\n作业：南侧高支模；支模高度：9m\n脚手架那个技术部王明初步看过说要复核"
        a, b = parse(text)
        self.assertIn("初步看过", a["已有人工判定"])
        self.assertEqual("王明", a["人工判定人"])
        self.assertNotIn("已有人工判定", b)

    def test_ambiguous_or_cross_file_human_reference_never_selects_one(self):
        for prefix in ("作业：北脚手架\n作业：南脚手架\n",
                       "【用户上传：甲.md】\n作业：北脚手架\n【用户上传：乙.md】\n"):
            with self.subTest(prefix=prefix):
                result = parse(prefix+"脚手架那个技术部王明初步看过说要复核")
                self.assertFalse(any(r.get("作业名称") and "已有人工判定" in r for r in result))
                pending = result[-1]
                self.assertNotIn("作业名称", pending)
                self.assertIn("已有人工判定", pending)

    def test_oral_proof_and_later_unknown_clear_previous_values(self):
        text = "作业：坑甲；开挖深度：7m；尺寸来源：JK-A；人工判定证据：口头说没问题\n开挖深度：未量；尺寸来源：待补"
        row = parse(text)[0]
        for key in ("开挖深度", "尺寸来源", "人工判定证据"):
            self.assertEqual(UNKNOWN, row[key])
            self.assertNotIn(key, row["_sources"])
        self.assertIn("7m", row["待补资料"])

    def test_scenario_is_not_fact_until_actual_reset(self):
        text = "例如作业：例坑；开挖深度：99m\n作业：仍是假设\n真实记录：作业：真实坑；开挖深度：6m"
        result = parse(text)
        self.assertEqual(1, len(result))
        self.assertEqual("真实坑", result[0]["作业名称"])
        self.assertNotIn("99m", draft(text))

    def test_reordered_matrix_preserves_blank_name_and_scope(self):
        for header, a, b in (
            ("支模高度|作业|项目|设计阶段|总体辖区", "9m|甲|项目甲|初步|CN", "||项目乙|施工图|"),
            ("项目|设计阶段|总体辖区|作业|支模高度", "项目甲|初步|CN|甲|9m", "项目乙|施工图|||"),
        ):
            with self.subTest(header=header):
                result = parse("|"+header+"|\n|"+"|".join("---" for _ in header.split("|"))+"|\n|"+a+"|\n|"+b+"|")
                self.assertEqual(2, len(result))
                self.assertEqual("9m", result[0]["支模高度"])
                self.assertEqual(UNKNOWN, result[1]["作业名称"])
                self.assertEqual(UNKNOWN, result[1]["支模高度"])
                self.assertEqual("项目乙", result[1]["项目名称"])
                self.assertEqual("施工图", result[1]["设计阶段"])
                self.assertEqual(UNKNOWN, result[1]["_project_zone"])

    def test_entities_decode_only_once_and_do_not_add_columns(self):
        text = "|作业|高度|\n|---|---|\n|=A&#124;B &lt; 9|字面&amp;lt;|"
        row = rows(text, "3 尺寸与触发事实")[0]
        self.assertEqual("=A|B < 9", row["作业名称"])
        self.assertEqual("字面&lt;", row["高度"])

    def test_metadata_only_uses_latest_file(self):
        row = parse("【用户上传：甲.md】\n项目：甲\n【用户上传：乙.md】\n项目：乙；辖区：EU")[0]
        self.assertEqual("乙", row["项目名称"])
        self.assertEqual("EU", row["_project_zone"])
        self.assertEqual("乙.md", row["_origin"])

    def test_same_project_matrix_zones_do_not_relabel_previous_work(self):
        a, b = parse("|项目|作业|总体辖区|\n|---|---|---|\n|甲|作业甲|CN|\n|甲|作业乙|SG|")
        self.assertEqual(["CN", "SG"], [r["_project_zone"] for r in (a, b)])

    def test_negative_dimension_does_not_create_positive_source(self):
        row = parse("作业：坑甲；开挖深度：不是9m按JK-15")[0]
        self.assertEqual(UNKNOWN, row["开挖深度"])
        self.assertNotIn("尺寸来源", row)

    def test_late_global_zone_does_not_relabel_previous_work(self):
        a, b = parse("作业：坑甲\n总体辖区：SG\n作业：坑乙")
        self.assertEqual(UNKNOWN, a["_project_zone"])
        self.assertEqual("SG", b["_project_zone"])


if __name__ == "__main__":
    unittest.main()
