#!/usr/bin/env python3
"""BIM input ownership and deliberately unverified engineering outputs."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packing_assistant.office_job import tables_from_md
from packing_assistant.post_drafts.bim import UNKNOWN, _quantity, build_draft, _TOOLS
from packing_assistant.post_drafts.bim_observations import parse


def draft(post, text):
    return build_draft(post, _TOOLS[post], text)


def rows(post, text, title):
    table = dict(tables_from_md(draft(post, text)))[title]
    return [dict(zip(table[0], row)) for row in table[1:]]


class BIMObservationTests(unittest.TestCase):
    def test_all_three_same_named_files_are_independent(self):
        for post in _TOOLS:
            with self.subTest(post=post):
                data = parse("【用户上传：同名.csv】\n项目：甲；辖区：CN；依据：规则甲\n模型：A；版本：V9；来源：原件甲\n"
                             "【用户上传：同名.csv】\n项目：乙\n模型：B", post)
                a, b = data.records["models"]
                self.assertNotEqual(a["_file"], b["_file"])
                self.assertEqual("甲", a["_context_data"]["project"])
                self.assertEqual("乙", b["_context_data"]["project"])
                self.assertNotIn("basis", b["_context_data"])
                self.assertNotIn("version", b)
                self.assertNotIn("source", b)
                self.assertEqual(UNKNOWN, b["_context_data"]["project_zone"])
                self.assertIn("同名.csv · 输入第3行", a["_sources"]["version"])

    def test_late_project_does_not_relabel_model(self):
        a, b = parse("模型：A\n项目：后来项目\n模型：B", "bim-deliver").records["models"]
        self.assertNotIn("project", a["_context_data"])
        self.assertEqual("后来项目", b["_context_data"]["project"])

    def test_project_stage_change_resets_model_and_children(self):
        data = parse("项目：甲；项目阶段：初步\n模型：A；版本：V1\n构件类别：墙；数量：7\n"
                     "项目阶段：深化\n模型：A\n构件类别：墙", "bim-qto")
        a, b = data.records["models"]
        q1, q2 = data.records["quantities"]
        self.assertEqual(["初步", "深化"], [r["_context_data"]["stage"] for r in (a, b)])
        self.assertNotIn("version", b)
        self.assertNotIn("quantity", q2)
        self.assertIs(q1["_model"], a)
        self.assertIs(q2["_model"], b)

    def test_scope_changes_remove_old_check_configuration(self):
        text = "项目：甲；检查范围：A；容差：7mm\n模型：甲模\n项目：乙\n模型：乙模"
        data = parse(text, "bim-coord")
        self.assertNotIn("tolerance", data.records["models"][1]["_context_data"])
        self.assertNotIn("check_scope", data.records["models"][1]["_context_data"])

    def test_every_scope_configuration_is_visible_with_own_project(self):
        text = "【用户上传：甲.md】\n项目：甲；检查范围：A；容差：7mm\n模型：甲模\n"
        text += "【用户上传：乙.md】\n项目：乙；检查范围：B；容差：13mm\n模型：乙模"
        items = rows("bim-coord", text, "项目约定与资料位置")
        second = next(r for r in items if r["项目"] == "乙" and r["参数栏目"] == "检查范围")
        self.assertEqual("B", second["用户值（未核验）"])
        self.assertIn("乙.md", second["输入位置（非原件页码）"])
        self.assertFalse(any(r["项目"] == "乙" and r["用户值（未核验）"] == "7mm" for r in items))

    def test_matrix_stage_is_scoped_before_model_regardless_of_column_order(self):
        for header, a, b in (
            ("模型|项目阶段|项目|版本|总体辖区", "A|初步|甲|V1|CN", "B|深化|甲||"),
            ("项目|总体辖区|版本|模型|项目阶段", "甲|CN|V1|A|初步", "甲|||B|深化"),
        ):
            with self.subTest(header=header):
                data = parse("|"+header+"|\n|"+"|".join("---" for _ in header.split("|"))+"|\n|"+a+"|\n|"+b+"|", "bim-deliver")
                models = data.records["models"]
                self.assertEqual(2, len(models))
                self.assertEqual(["初步", "深化"], [r["_context_data"]["stage"] for r in models])
                self.assertEqual(["A", "B"], [r["model"] for r in models])
                self.assertEqual(UNKNOWN, models[1]["version"])

    def test_local_model_stage_is_not_project_stage(self):
        a, b = parse("项目阶段：初步\n模型：A；模型阶段：深化\n模型：B", "bim-deliver").records["models"]
        self.assertEqual("深化", a["stage"])
        self.assertEqual("初步", a["_context_data"]["stage"])
        self.assertNotIn("stage", b)

    def test_matrix_child_rows_with_blank_ids_do_not_disappear(self):
        for post, header, kind, key in (
            ("bim-coord", "责任人|问题编号|问题描述|模型", "issues", "issue_id"),
            ("bim-qto", "数量|构件类别|模型|单位", "quantities", "category"),
            ("bim-deliver", "交付日期|交付物|模型", "deliverables", "deliverable"),
        ):
            with self.subTest(post=post):
                row = "张工||净高不足|A" if post == "bim-coord" else "0||A|件" if post == "bim-qto" else "周五||A"
                data = parse("|"+header+"|\n|"+"|".join("---" for _ in header.split("|"))+"|\n|"+row+"|", post)
                self.assertEqual(1, len(data.records[kind]))
                self.assertEqual(UNKNOWN, data.records[kind][0][key])
                self.assertEqual("A", data.records[kind][0]["model_ref"])
                self.assertNotIn("models", data.records)

    def test_model_version_before_name_in_matrix_is_not_preamble(self):
        data = parse("|版本|模型|来源|\n|---|---|---|\n|V1|A|a.csv|\n||B||", "bim-qto")
        a, b = data.records["models"]
        self.assertEqual("V1", a["version"])
        self.assertEqual("a.csv", a["source"])
        self.assertEqual(UNKNOWN, b["version"])
        self.assertNotIn("version", data.context)

    def test_multiple_children_do_not_borrow_quantity_or_rule(self):
        data = parse("模型：A；版本：V1\n构件类别：墙；数量：0；单位：m3；扣洞：用户扣洞\n构件类别：梁；单位：件", "bim-qto")
        a, b = data.records["quantities"]
        self.assertEqual("0", _quantity(a))
        self.assertEqual(UNKNOWN, _quantity(b))
        self.assertNotIn("openings", b)
        self.assertEqual("A", b["model_ref"])
        self.assertNotIn("version", b)

    def test_explicit_other_model_reference_does_not_keep_previous_parent(self):
        data = parse("模型：A；版本：V1\n构件类别：墙；关联模型：B；数量：7", "bim-qto")
        item = data.records["quantities"][0]
        self.assertEqual("B", item["model_ref"])
        self.assertNotIn("_model", item)

    def test_model_reference_matches_only_unique_in_same_project_and_file(self):
        text = "模型：A；版本：V1\n模型：B；版本：V2\n构件类别：墙；关联模型：A"
        data = parse(text, "bim-qto")
        item = data.records["quantities"][0]
        self.assertEqual("A", item["model_ref"])
        self.assertIs(item["_model"], data.records["models"][0])
        for prefix in ("模型：A；版本：V1\n模型：A；版本：V2\n",
                       "【用户上传：甲.md】\n模型：A\n【用户上传：乙.md】\n"):
            with self.subTest(prefix=prefix):
                item = parse(prefix+"构件类别：墙；关联模型：A", "bim-qto").records["quantities"][0]
                self.assertNotIn("_model", item)

    def test_late_issue_identifier_enriches_unnamed_description(self):
        data = parse("问题描述：净高不足；位置：B1\n问题编号：Q-A", "bim-coord")
        self.assertEqual(1, len(data.records["issues"]))
        self.assertEqual("Q-A", data.records["issues"][0]["issue_id"])

    def test_repeated_issue_ids_never_merge_distinct_rows(self):
        result = parse("问题编号：Q-A；问题描述：问题甲；责任人：张工\n问题编号：Q-A；问题描述：问题乙", "bim-coord")
        a, b = result.records["issues"]
        self.assertEqual("张工", a["owner_person"])
        self.assertNotIn("owner_person", b)
        self.assertNotIn("问题甲", str(b))

    def test_owner_person_is_not_responsible_profession(self):
        item = parse("问题编号：Q-A；责任专业：暖通；责任人：张工；期限：周五", "bim-coord").records["issues"][0]
        self.assertEqual("暖通", item["owner"])
        self.assertEqual("张工", item["owner_person"])
        self.assertEqual("周五", item["deadline"])

    def test_later_unknown_clears_version_lod_and_proof(self):
        a = parse("模型：A；版本：V7；LOD：LOD400；碰撞检查记录：报告甲\n版本：忘了；LOD：待补；碰撞检查记录：还没拿到", "bim-deliver").records["models"][0]
        for key in ("version", "lod", "clash_record"):
            self.assertEqual(UNKNOWN, a[key])
            self.assertNotIn(key, a["_sources"])
        self.assertIn("忘了", a["pending"])

    def test_quantity_is_literal_zero_and_words_not_arithmetic(self):
        for literal in ("0", "712.30立方", "85平米", "1,234.50m3"):
            with self.subTest(literal=literal):
                item = parse("构件类别：墙；数量："+literal, "bim-qto").records["quantities"][0]
                self.assertEqual(literal, _quantity(item))
        for literal in ("10*20", "5+6", "=SUM(A1:A3)", "未提供"):
            with self.subTest(literal=literal):
                item = parse("构件类别：墙；数量："+literal, "bim-qto").records["quantities"][0]
                self.assertEqual(UNKNOWN, _quantity(item))

    def test_natural_deadline_instruction_is_not_a_model_name(self):
        data = parse("结构模型V3，问题编号 Q-19，要求周五前改完模型", "bim-coord")
        self.assertEqual(["结构模型"], [r["model"] for r in data.records["models"]])
        self.assertEqual("周五", data.records["issues"][0]["deadline"])

    def test_purpose_lod_recipient_are_local_not_lent_to_second_model(self):
        text = "模型：A；目的：用于施工深化；LOD：LOD350；交付对象：业主；交付日期：周五\n模型：B；目的：资产管理"
        a, b = rows("bim-deliver", text, "模型交付目的与期限")
        self.assertEqual("LOD350", a["用户LOD"])
        self.assertEqual("业主", a["交付对象"])
        self.assertEqual("资产管理", b["交付目的"])
        for key in ("用户LOD", "交付对象", "交付日期"):
            self.assertEqual(UNKNOWN, b[key])
        self.assertEqual("", b["签认"])

    def test_unknown_file_cannot_be_invented_from_model_name(self):
        md = draft("bim-deliver", "模型：机电；格式：IFC4；LOD：LOD350")
        item = rows("bim-deliver", "模型：机电；格式：IFC4；LOD：LOD350", "模型交付目的与期限")[0]
        self.assertEqual(UNKNOWN, item["文件名"])
        self.assertNotIn("机电.ifc", md)

    def test_country_names_in_model_do_not_choose_jurisdiction(self):
        for post in _TOOLS:
            with self.subTest(post=post):
                md = draft(post, "模型：新加坡楼；来源：中国报告.pdf")
                self.assertIn("- 辖区：UNSPECIFIED", md)
                self.assertNotIn("| SG |", md)
                self.assertNotIn("| CN |", md)

    def test_dual_preserves_explicit_basis_without_inventing_partner_country(self):
        text = "总体辖区：DUAL；CN依据：用户CN文本；EU依据：用户EU文本\n模型：A；对象辖区：EU"
        item = rows("bim-deliver", text, "项目与文件依据归属")[0]
        self.assertEqual("DUAL", item["总体辖区（不代填对象）"])
        self.assertEqual("用户CN文本", item["CN用户依据"])
        self.assertEqual("用户EU文本", item["EU用户依据"])
        self.assertEqual(UNKNOWN, item["SG用户依据"])

    def test_scenario_and_future_source_are_not_verified_fact(self):
        data = parse("例如模型：假模型；版本：V99\n模型：仍是假设\n真实记录：模型：真实模型；来源：计划提供明细", "bim-qto")
        self.assertEqual(1, len(data.records["models"]))
        self.assertEqual("真实模型", data.records["models"][0]["model"])
        self.assertEqual(UNKNOWN, data.records["models"][0]["source"])

    def test_entities_decode_once_keep_physical_cells(self):
        item = rows("bim-deliver", "|模型|目的|\n|---|---|\n|=A&#124;B &lt; 9|字面&amp;lt;|", "模型交付目的与期限")[0]
        self.assertEqual("=A|B < 9", item["模型"])
        self.assertEqual("字面&lt;", item["交付目的"])

    def test_all_outputs_keep_no_scan_no_calculation_no_acceptance_boundaries(self):
        for post in _TOOLS:
            with self.subTest(post=post):
                md = draft(post, "模型：A；状态：Published；验收记录：用户称已验收")
                self.assertIn("submit_blocked=true", md)
                self.assertNotIn("本轮验收通过", md)
                self.assertNotIn("零碰撞证明已生成", md)
                self.assertNotIn("算量结果为", md)

    def test_same_project_matrix_zones_do_not_relabel_previous_model(self):
        data = parse("|项目|模型|总体辖区|\n|---|---|---|\n|甲|A|CN|\n|甲|B|SG|", "bim-deliver")
        self.assertEqual(["CN", "SG"], [r["_context_data"]["project_zone"] for r in data.records["models"]])

    def test_late_global_zone_does_not_relabel_previous_model(self):
        data = parse("模型：A\n总体辖区：SG\n模型：B", "bim-deliver")
        self.assertEqual([UNKNOWN, "SG"], [r["_context_data"]["project_zone"] for r in data.records["models"]])

    def test_unique_link_is_visible_without_copying_unrelated_version(self):
        text = "模型：A；版本：V1；来源：a.csv\n模型：B；版本：V2\n构件类别：墙；关联模型：A"
        item = rows("bim-qto", text, "对象与模型版本关联（仅明确归属）")[0]
        self.assertEqual("A", item["关联模型"])
        self.assertEqual("V1", item["关联模型用户版本"])
        self.assertEqual("a.csv", item["关联模型用户来源"])
        bad = text.replace("关联模型：A", "关联模型：未提供")
        item = rows("bim-qto", bad, "对象与模型版本关联（仅明确归属）")[0]
        self.assertEqual(UNKNOWN, item["关联模型用户版本"])


if __name__ == "__main__":
    unittest.main()
