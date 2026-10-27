#!/usr/bin/env python3
"""T044 first four design posts: body facts, record isolation and write gates."""

from __future__ import annotations

import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packing_assistant import expert_turn
from packing_assistant.office_job import tables_from_md
from packing_assistant.post_drafts.design_basic import DISCLAIMER, build_draft
from packing_assistant.runtime.scheduler import Scheduler
from packing_assistant.runtime.tool_engine import default_engine

TOOLS = {"architecture": "architecture__memo", "structure": "structure__calc_outline", "geotech": "geotech__brief", "facade": "facade__brief"}


def draft(post: str, text: str = "") -> str:
    return build_draft(post, TOOLS[post], text)


def chapter(markdown: str, title: str) -> str:
    return markdown.split(f"## {title}\n", 1)[1].split("\n## ", 1)[0]


def table_rows(markdown: str) -> list[list[str]]:
    return [[cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in markdown.splitlines() if line.startswith("| ") and not re.fullmatch(r"[| \-:]+", line)]


def typed_rows(markdown: str, title: str, header: str) -> list[dict[str, str]]:
    rows = next(rows for _, rows in tables_from_md(chapter(markdown, title)) if header in rows[0])
    return [dict(zip(rows[0], row)) for row in rows[1:]]


class DesignBuilderTests(unittest.TestCase):
    def test_exact_tool_contract_and_default_draft_boundaries(self):
        self.assertIsNone(build_draft("architecture", "structure__calc_outline", ""))
        self.assertIsNone(build_draft("steel", "steel__brief", ""))
        for post in TOOLS:
            with self.subTest(post=post):
                markdown = draft(post)
                self.assertIn(DISCLAIMER, markdown)
                self.assertIn("- 辖区：UNSPECIFIED", markdown)
                self.assertIn("submit_blocked=true", markdown)
                self.assertNotIn("## 用户原文", markdown)
                self.assertNotRegex(markdown, r"第[一二三四五六七八九十\d]+条|经验算满足|地基已满足")
                self.assertNotIn("见图 ", markdown)

    def test_architecture_has_ten_full_chapters_and_missing_area_and_egress(self):
        markdown = draft("architecture", "写一份建筑专业说明，面积暂缺")
        self.assertEqual(len(re.findall(r"^## \d+ ", markdown, re.M)), 10)
        overview = table_rows(chapter(markdown, "1 工程概况与设计范围"))[-1]
        self.assertEqual(overview[1:7], ["[A001] 待填"] * 6)
        fire = table_rows(chapter(markdown, "5 防火"))[1]
        self.assertEqual(fire[4:7], ["[A001] 待填"] * 3)
        self.assertNotRegex(markdown, r"\b\d+\s*(?:m2|m²|㎡)")

    def test_architecture_supplied_parameters_fill_corresponding_sections(self):
        markdown = draft("architecture", "项目名称：示例建筑；图号清单：A-101、A-102\n单体：办公楼；建筑面积：4560m2；层数：6；建筑高度：24m；功能：办公；耐火等级：用户二级；消防车道：东侧环通；防火分区面积：用户1500m2；疏散宽度：用户2.4m；无障碍坡道：南入口坡道；层高：4m；室内净高：3.2m；传热系数：用户0.45；节能计算书：节能资料A\n图号：A-101；图名：首层平面；版本：R2")
        overview = chapter(markdown, "1 工程概况与设计范围")
        self.assertIn("| 办公楼 | [A001] 待填 | 4560m2 | 6 | 24m | 办公 |", overview)
        self.assertIn("东侧环通", chapter(markdown, "3 总平面"))
        self.assertIn("| 用户1500m2 | [A001] 待填 | 用户2.4m |", chapter(markdown, "5 防火"))
        self.assertIn("南入口坡道", chapter(markdown, "6 无障碍"))
        self.assertIn("| 办公楼 | 4m | 3.2m |", chapter(markdown, "7 竖向"))
        self.assertIn("用户0.45", chapter(markdown, "8 节能与绿色"))
        drawings = chapter(markdown, "9 图则口径")
        self.assertIn("| A-101 | 首层平面 | R2 |", drawings)
        self.assertIn("A-101、A-102", drawings)
        self.assertNotIn("见图", drawings)

    def test_architecture_multiple_buildings_and_spaces_do_not_share_areas(self):
        markdown = draft("architecture", "单体：楼A；建筑面积：1200m2；层数：3\n分区名称：接待厅；面积：100m2；功能：接待\n分区名称：设备区；功能：设备\n单体：楼B；层数：2\n分区名称：休息区；面积：80m2")
        overview = table_rows(chapter(markdown, "1 工程概况与设计范围"))
        self.assertEqual(next(row for row in overview if row[0] == "楼A")[2], "1200m2")
        self.assertEqual(next(row for row in overview if row[0] == "楼B")[2], "[A001] 待填")
        spaces = table_rows(chapter(markdown, "4 平面功能"))[1:]
        self.assertEqual([row[:3] for row in spaces], [["楼A", "接待厅", "100m2"], ["楼A", "设备区", "[A001] 待填"], ["楼B", "休息区", "80m2"]])

    def test_architecture_markdown_table_preserves_rows_instead_of_merging(self):
        markdown = draft("architecture", "| 单体 | 建筑面积 | 功能 |\n| --- | --- | --- |\n| 楼A | 1600m2 | 办公 |\n| 楼B | | 仓储 |")
        overview = table_rows(chapter(markdown, "1 工程概况与设计范围"))
        self.assertEqual(next(row for row in overview if row[0] == "楼B")[2], "[A001] 待填")
        functions = table_rows(chapter(markdown, "4 平面功能"))
        self.assertEqual(functions[1][:4], ["楼A", "[A001] 待填", "1600m2", "办公"])
        self.assertEqual(functions[2][:4], ["楼B", "[A001] 待填", "[A001] 待填", "仓储"])

    def test_structure_has_ten_chapters_and_uncomputed_results(self):
        markdown = draft("structure", "写结构提纲，没有地勘；请按办公室估算活载和承载力")
        self.assertEqual(len(re.findall(r"^## \d+ ", markdown, re.M)), 10)
        loads = table_rows(chapter(markdown, "4 荷载与组合"))[1]
        self.assertEqual(loads[3:9], ["[A001] 待填"] * 6)
        foundation = table_rows(chapter(markdown, "6 地基基础"))[1]
        self.assertEqual(foundation[1:6], ["[A001] 待填"] * 5)
        results = table_rows(chapter(markdown, "7 计算步骤"))[1:]
        self.assertTrue(all(row[-1] == "UNSPECIFIED" for row in results))
        self.assertIn("QA 自检", markdown)

    def test_structure_member_loads_material_and_spans_reach_tables(self):
        markdown = draft("structure", "项目名称：结构测试；勘察报告：地勘文件G；荷载表文件：荷载L\n单体：楼A；结构体系：用户框架；基础型式：用户桩基；持力层：报告第五层；承载力：用户报告300kPa\n构件编号：梁A；跨度：6m；截面：用户300×600；功能：办公；恒载：5kPa；活载：2kPa；风荷载：用户风表；荷载组合：用户组合LC1；混凝土等级：用户C30；钢筋等级：用户HRB400；来源：用户荷载L；计算书：计算附件待核\n构件编号：板B；活载：3kPa")
        loads = table_rows(chapter(markdown, "4 荷载与组合"))[1:]
        self.assertEqual(loads[0][:6], ["楼A", "梁A", "办公", "5kPa", "2kPa", "用户风表"])
        self.assertEqual(loads[1][3:5], ["[A001] 待填", "3kPa"])
        materials = table_rows(chapter(markdown, "5 材料"))
        self.assertEqual(materials[1][1:3], ["用户C30", "用户HRB400"])
        self.assertEqual(materials[2][1:3], ["[A001] 待填"] * 2)
        members = table_rows(chapter(markdown, "9 构件复核清单"))
        self.assertEqual(members[1][2:5], ["6m", "用户300×600", "计算附件待核"])
        self.assertIn("用户报告300kPa", chapter(markdown, "6 地基基础"))
        self.assertNotIn("7kPa", markdown)

    def test_structure_unit_level_inputs_work_without_inventing_member_names(self):
        markdown = draft("structure", "单体：单体A；活载：4kPa；功能：仓储\n单体：单体B；恒载：5kPa")
        rows = table_rows(chapter(markdown, "4 荷载与组合"))[1:]
        self.assertEqual(rows[0][:5], ["单体A", "[A001] 待填", "仓储", "[A001] 待填", "4kPa"])
        self.assertEqual(rows[1][:5], ["单体B", "[A001] 待填", "[A001] 待填", "5kPa", "[A001] 待填"])

    def test_structure_general_loads_remain_separate_when_member_loads_are_given(self):
        markdown = draft("structure", "单体：楼A；活载：用户总体3kPa\n构件编号：梁A；活载：用户局部4kPa\n构件编号：梁B；跨度：5m")
        rows = table_rows(chapter(markdown, "4 荷载与组合"))[1:]
        self.assertEqual([row[1] for row in rows], ["[A001] 待填", "梁A", "梁B"])
        self.assertEqual([row[4] for row in rows], ["用户总体3kPa", "用户局部4kPa", "[A001] 待填"])

    def test_structure_markdown_rows_and_missing_member_names_are_isolated(self):
        markdown = draft("structure", "| 构件编号 | 活载 | 跨度 | 来源 |\n| --- | --- | --- | --- |\n| 梁A | 2kPa | 6m | 表A |\n| | 3kPa | | 表B |")
        loads = table_rows(chapter(markdown, "4 荷载与组合"))[1:]
        self.assertEqual(loads[0][4], "2kPa")
        self.assertEqual(loads[1][1:5], ["[A001] 待填", "[A001] 待填", "[A001] 待填", "3kPa"])
        members = table_rows(chapter(markdown, "9 构件复核清单"))
        self.assertEqual(members[2][2], "[A001] 待填")

    def test_geotech_empty_input_does_not_invent_holes_or_parameters(self):
        markdown = draft("geotech", "写岩土勘察纲要，SI 暂无")
        layers = table_rows(chapter(markdown, "6 地层与参数摘录"))[1]
        self.assertEqual(layers[4:10], ["未在原文检出"] * 6)
        water = table_rows(chapter(markdown, "4 场地与地下水"))
        self.assertTrue(any(row[-3] == "未在原文检出" for row in water[1:]))
        choices = table_rows(chapter(markdown, "7 地基方案比选"))[1:4]
        self.assertTrue(all(row[-1] == "UNSPECIFIED" for row in choices))
        self.assertNotRegex(markdown, r"BH-?\d+|\d+\s*(?:kPa|MPa)")

    def test_geotech_multiple_holes_and_layers_keep_c_phi_and_water_local(self):
        markdown = draft("geotech", "勘察报告：SI-示例摘录\n孔号：BH-1；孔深：20m；地下水位：1.5m；来源：SI第A页\n层号：①；土层：粉质黏土；层深：3m；c：12kPa；φ：18°\n层号：②；土层：砂土；φ：30°\n孔号：BH-2；孔深：25m\n层号：①；土层：黏土；压缩模量：用户8MPa")
        holes = table_rows(chapter(markdown, "5 勘探工作量与试验"))[1:]
        self.assertEqual([row[:2] for row in holes], [["BH-1", "20m"], ["BH-2", "25m"]])
        water_rows = table_rows(chapter(markdown, "4 场地与地下水"))
        self.assertEqual(next(row for row in water_rows if row[0] == "BH-1")[2], "1.5m")
        self.assertEqual(next(row for row in water_rows if row[0] == "BH-2")[2], "未在原文检出")
        layers = table_rows(chapter(markdown, "6 地层与参数摘录"))[1:]
        self.assertEqual(layers[0][:6], ["BH-1", "①", "粉质黏土", "3m", "12kPa", "18°"])
        self.assertEqual(layers[1][:6], ["BH-1", "②", "砂土", "未在原文检出", "未在原文检出", "30°"])
        self.assertEqual(layers[2][:6], ["BH-2", "①", "黏土", "未在原文检出", "未在原文检出", "未在原文检出"])
        self.assertEqual(layers[2][7], "用户8MPa")
        self.assertTrue(all(row[9] == "未在原文检出" for row in layers))

    def test_geotech_markdown_layers_keep_hole_ids_with_missing_values(self):
        markdown = draft("geotech", "| 孔号 | 层号 | 土层 | c | φ | 地下水位 |\n| --- | --- | --- | --- | --- | --- |\n| BH-A | 1 | 黏土 | 15kPa | 20° | 2m |\n| BH-B | 2 | 砂层 | | 31° | |")
        layers = table_rows(chapter(markdown, "6 地层与参数摘录"))[1:]
        self.assertEqual(layers[0][0], "BH-A")
        self.assertEqual(layers[0][4:7], ["15kPa", "20°", "2m"])
        self.assertEqual(layers[1][0], "BH-B")
        self.assertEqual(layers[1][4:7], ["未在原文检出", "31°", "未在原文检出"])

    def test_geotech_parameter_records_without_layer_id_are_preserved(self):
        markdown = draft("geotech", "孔号：BH-A；c：用户10kPa；地下水位：用户2m\n孔号：待填；φ：用户28°")
        layers = table_rows(chapter(markdown, "6 地层与参数摘录"))[1:]
        self.assertEqual(layers[0][0], "BH-A")
        self.assertEqual(layers[0][4], "用户10kPa")
        self.assertEqual(layers[1][0], "未在原文检出")
        self.assertEqual(layers[1][4:6], ["未在原文检出", "用户28°"])

    def test_facade_empty_input_has_no_wind_pressure_or_dimensions(self):
        markdown = draft("facade", "写幕墙说明，未提供风压，请经验选面板厚度和龙骨")
        rows = table_rows(chapter(markdown, "4 抗风、气密、水密与层间变位"))
        self.assertEqual(rows[1][1:8], ["[A001] 待填"] * 7)
        self.assertIn("计算书", chapter(markdown, "15 资料目录与关键缺项"))
        self.assertNotRegex(markdown, r"\d+\s*(?:mm|kPa|MPa)")

    def test_facade_multiple_systems_keep_wind_and_material_values_separate(self):
        markdown = draft("facade", "单体：主楼\n幕墙编号：CW-A；位置：东立面；幕墙体系：用户单元式；风压：用户2.5kPa；层高：4m；分格：用户1.5m；面板厚度：用户8mm；龙骨规格：用户截面A；预埋件：用户节点P1；气密要求：用户气密目标；水密要求：用户水密目标；开启扇：用户开启范围；加工图：CW-D1；材料检测：材料资料M1\n幕墙编号：CW-B；位置：西立面；幕墙体系：用户构件式；风压：用户3kPa")
        rows = table_rows(chapter(markdown, "4 抗风、气密、水密与层间变位"))[1:]
        self.assertEqual(rows[0][1], "用户2.5kPa")
        self.assertEqual(rows[0][6:8], ["用户8mm", "用户截面A"])
        self.assertEqual(rows[1][1], "用户3kPa")
        self.assertEqual(rows[1][6:8], ["[A001] 待填"] * 2)
        self.assertIn("用户节点P1", chapter(markdown, "3 预埋件与主体结构接口"))
        self.assertIn("用户开启范围", chapter(markdown, "6 开启扇与清洗维护"))
        self.assertIn("| CW-A | CW-D1 | 材料资料M1 |", chapter(markdown, "7 加工图与材料检测"))

    def test_facade_records_do_not_certify_performance_or_acceptance(self):
        markdown = draft("facade", "幕墙编号：CW-A；防火封堵：用户节点F1；防雷接口：电气接点表；拉拔检测计划：用户检测计划；性能检测：用户报告P；验收记录：用户签收记录；状态：用户称已验收\n幕墙编号：CW-B；防火玻璃范围：用户范围B")
        acceptance = table_rows(chapter(markdown, "8 验收资料"))[1:]
        self.assertEqual(acceptance[0], ["CW-A", "用户签收记录", "用户称已验收", "本轮未执行验收", "UNSPECIFIED"])
        self.assertEqual(acceptance[1][1:3], ["[A001] 待填"] * 2)
        self.assertIn("用户检测计划", chapter(markdown, "10 后置锚固与拉拔检测接口"))
        self.assertIn("用户范围B", chapter(markdown, "11 防火专业接口"))
        self.assertIn("电气接点表", chapter(markdown, "12 防雷专业接口"))

    def test_facade_markdown_rows_and_unknown_ids_do_not_share_thickness(self):
        markdown = draft("facade", "| 幕墙编号 | 风压 | 面板厚度 | 层高 |\n| --- | --- | --- | --- |\n| CW-A | 用户2kPa | 用户6mm | 4m |\n| | 用户3kPa | | 5m |")
        rows = table_rows(chapter(markdown, "4 抗风、气密、水密与层间变位"))[1:]
        self.assertEqual(rows[0][6], "用户6mm")
        self.assertEqual(rows[1][0:2], ["[A001] 待填", "用户3kPa"])
        self.assertEqual(rows[1][6], "[A001] 待填")

    def test_dual_basis_and_review_interfaces_have_separate_columns_for_all_posts(self):
        for post in TOOLS:
            with self.subTest(post=post):
                markdown = draft(post, "辖区：DUAL；CN依据：用户CN资料；SG依据：用户SG资料；EU依据：用户EU资料；CN审查接口：国内接口甲；SG审查接口：新加坡接口乙；EU审查接口：成员国接口丙；依据：未分配文件")
                rows = table_rows(markdown)
                basis = next(row for row in rows if row[0] == "用户指定依据")
                self.assertEqual(basis[1:], ["用户CN资料", "用户SG资料", "用户EU资料"])
                review = next(row for row in rows if row[0] == "用户审查接口")
                self.assertEqual(review[1:], ["国内接口甲", "新加坡接口乙", "成员国接口丙"])
                versions = next(row for row in rows if row[0] == "已核版本 / 条款")
                self.assertEqual(versions[1:], ["UNSPECIFIED"] * 3)
                self.assertIn("| 通用依据（不自动分配辖区） | 未分配文件 |", markdown)

    def test_explicit_sg_drafts_do_not_gain_chinese_rule_families(self):
        for post in TOOLS:
            with self.subTest(post=post):
                markdown = draft(post, "辖区：SG；依据：用户项目文件")
                self.assertIn("- 辖区：SG", markdown)
                self.assertNotRegex(markdown, r"JGJ|GB/T|37\s*号|38\s*号")

    def test_interfaces_and_html_values_are_preserved_safely(self):
        markdown = draft("structure", "单体：楼A\n接口编号：I-1；提资方：建筑；接收专业：结构；提资内容：开洞A|B且x<10；截止日期：2026-10-10\n接口编号：I-2；提资方：幕墙；接收专业：结构；提资内容：预埋需求")
        rows = table_rows(chapter(markdown, "10 专业接口与 QA 自检"))
        one = next(row for row in rows if row[0] == "I-1")
        two = next(row for row in rows if row[0] == "I-2")
        self.assertEqual(one[1:6], ["楼A", "建筑", "结构", "开洞A&#124;B且x&lt;10", "2026-10-10"])
        self.assertEqual(two[5], "[A001] 待填")
        self.assertNotIn("&amp;lt;", markdown)

    def test_compact_empty_identity_cells_do_not_shift_professional_parameters(self):
        cases = (
            ("architecture", "|单体|建筑面积|\n|---|---|\n||400m2|", "1 工程概况与设计范围", 2, "400m2"),
            ("structure", "|构件编号|跨度|\n|---|---|\n||6m|", "9 构件复核清单", 2, "6m"),
            ("geotech", "|孔号|层号|c|\n|---|---|---|\n||①|12kPa|", "6 地层与参数摘录", 4, "12kPa"),
            ("facade", "|幕墙编号|风压|\n|---|---|\n||用户3kPa|", "4 抗风、气密、水密与层间变位", 1, "用户3kPa"),
        )
        for post, text, title, index, expected in cases:
            with self.subTest(post=post):
                rows = table_rows(chapter(draft(post, text), title))
                match = next(row for row in rows if len(row) > index and row[index] == expected)
                self.assertEqual(match[0], "未在原文检出" if post == "geotech" else "[A001] 待填")

    def test_sibling_objects_keep_local_or_unknown_jurisdictions(self):
        cases = (
            ("architecture", "单体", "建筑面积", "1 工程概况与设计范围"),
            ("structure", "构件编号", "活载", "4 荷载与组合"),
            ("facade", "幕墙编号", "风压", "4 抗风、气密、水密与层间变位"),
        )
        for post, name, parameter, title in cases:
            with self.subTest(post=post):
                text = f"{name}：对象A；辖区：CN；{parameter}：数值A\n{name}：对象B；{parameter}：数值B\n{name}：对象C；辖区：SG；{parameter}：数值C"
                rows = table_rows(chapter(draft(post, text), title))
                self.assertEqual([next(row for row in rows if obj in row)[-1] for obj in ("对象A", "对象B", "对象C")], ["CN", "UNSPECIFIED", "SG"])
        geology = draft("geotech", "孔号：孔A；辖区：CN；层号：①；c：数值A\n孔号：孔B；层号：②；c：数值B\n孔号：孔C；辖区：SG；层号：③；c：数值C")
        rows = table_rows(chapter(geology, "6 地层与参数摘录"))[1:]
        self.assertEqual([row[-1] for row in rows], ["CN", "UNSPECIFIED", "SG"])

    def test_explicit_global_scope_and_parent_scope_do_not_leak_to_siblings(self):
        global_text = draft("architecture", "辖区：CN\n单体：对象A\n单体：对象B；辖区：UNSPECIFIED\n单体：对象C；辖区：SG")
        rows = table_rows(chapter(global_text, "1 工程概况与设计范围"))
        self.assertEqual([next(row for row in rows if row[0] == obj)[-1] for obj in ("对象A", "对象B", "对象C")], ["CN", "UNSPECIFIED", "SG"])
        hierarchy = draft("structure", "单体：楼A；辖区：CN\n构件编号：梁A；活载：数值A\n单体：楼B\n构件编号：梁B；活载：数值B")
        rows = table_rows(chapter(hierarchy, "4 荷载与组合"))[1:]
        self.assertEqual([row[-1] for row in rows], ["CN", "UNSPECIFIED"])
        bare = draft("facade", "SG\n幕墙编号：幕墙A\n幕墙编号：幕墙B")
        self.assertEqual([row[-1] for row in table_rows(chapter(bare, "4 抗风、气密、水密与层间变位"))[1:]], ["SG", "SG"])

    def test_matrix_scope_cells_do_not_borrow_another_rows_region(self):
        text = "|幕墙编号|风压|辖区|\n|---|---|---|\n|幕墙A|输入A|CN|\n|幕墙B|输入B||\n|幕墙C|输入C|SG|"
        rows = table_rows(chapter(draft("facade", text), "4 抗风、气密、水密与层间变位"))[1:]
        self.assertEqual([row[-1] for row in rows], ["CN", "UNSPECIFIED", "SG"])

    def test_adapter_table_entities_decode_once_and_plain_text_does_not_decode(self):
        table = draft("structure", "|构件编号|截面|\n|---|---|\n|=A&#124;B &lt; 10|字面&amp;lt;|")
        rows = table_rows(chapter(table, "9 构件复核清单"))
        self.assertEqual(rows[1][0], "=A&#124;B &lt; 10")
        self.assertEqual(rows[1][3], "字面&amp;lt;")
        plain = draft("structure", "构件编号：字面&lt;；截面：x<10且A|B")
        self.assertIn("| 字面&amp;lt; |", chapter(plain, "9 构件复核清单"))

    def test_suffix_identifiers_keep_each_objects_preceding_values(self):
        cases = (
            ("architecture", "建筑面积：400m2；单体：对象A；建筑面积：500m2；单体：对象B", "1 工程概况与设计范围", 2, "400m2", "500m2"),
            ("structure", "跨度：6m；构件编号：对象A；跨度：7m；构件编号：对象B", "9 构件复核清单", 2, "6m", "7m"),
            ("geotech", "c：12kPa；孔号：对象A；c：15kPa；孔号：对象B", "6 地层与参数摘录", 4, "12kPa", "15kPa"),
            ("facade", "风压：用户2kPa；幕墙编号：对象A；风压：用户3kPa；幕墙编号：对象B", "4 抗风、气密、水密与层间变位", 1, "用户2kPa", "用户3kPa"),
        )
        for post, text, title, index, first, second in cases:
            with self.subTest(post=post):
                rows = table_rows(chapter(draft(post, text), title))
                self.assertEqual(next(row for row in rows if row[0] == "对象A")[index], first)
                self.assertEqual(next(row for row in rows if row[0] == "对象B")[index], second)

    def test_narrative_architecture_registers_literal_area_height_and_task_brief(self):
        md = draft("architecture", "起草建筑说明，3#楼是人才公寓，地上18层，总建筑面积21917.3平米，"
                   "建筑高度53.15米，首层层高4.9米，设计任务书用甲方2026-08-19下发的那版，图号目前只有建施-06。")
        row = typed_rows(md, "1 工程概况与设计范围", "单体")[0]
        self.assertEqual(("3#楼", "人才公寓", "18层", "21917.3平米", "53.15米"),
                         tuple(row[key] for key in ("单体", "功能 / 业态", "层数", "建筑面积", "建筑高度")))
        self.assertEqual("4.9米", typed_rows(md, "7 竖向", "层高")[0]["层高"])
        self.assertIn("2026-08-19", chapter(md, "1 工程概况与设计范围"))
        drawing = typed_rows(md, "9 图则口径", "用户图号")[0]
        self.assertEqual("建施-06", drawing["用户图号"])
        self.assertEqual("3#楼", drawing["关联单体"])

    def test_two_narrative_buildings_keep_each_drawing_and_unknown_storeys_separate(self):
        md = draft("architecture", "两栋一起出建筑说明：1#楼商业带办公，面积32718.2平，高80.6米，"
                   "消防车道走北侧规划路，图号建施-102；另外那个幼儿园是6#楼，才2891.7平，三层，"
                   "高度甲方还没定，主入口开在南边小区路，图号建施-602是B版。")
        rows = typed_rows(md, "1 工程概况与设计范围", "单体")
        self.assertEqual(["1#楼", "6#楼"], [row["单体"] for row in rows])
        self.assertEqual(["商业带办公", "幼儿园"], [row["功能 / 业态"] for row in rows])
        self.assertEqual(["32718.2平", "2891.7平"], [row["建筑面积"] for row in rows])
        self.assertEqual(["[A001] 待填", "三层"], [row["层数"] for row in rows])
        self.assertEqual("[A001] 待填", rows[1]["建筑高度"])
        drawing = typed_rows(md, "9 图则口径", "用户图号")
        self.assertEqual(["1#楼", "6#楼"], [row["关联单体"] for row in drawing])
        self.assertEqual(["[A001] 待填", "B版"], [row["版本"] for row in drawing])
        general = typed_rows(md, "3 总平面", "消防车道")
        self.assertEqual("北侧规划路", general[0]["消防车道"])
        self.assertEqual("[A001] 待填", general[1]["消防车道"])

    def test_space_labelled_architecture_keeps_elevator_location_and_drawing_version(self):
        md = draft("architecture", "单体：综合教学楼A座\n地上6层，建筑高度24.95m\n"
                   "无障碍电梯 1部，设在东侧门厅\n图号：建施-J-03，版本V2.2\n"
                   "提资：2026-10-18前给结构提楼板开洞条件")
        row = typed_rows(md, "6 无障碍", "无障碍电梯")[0]
        self.assertIn("1部", row["无障碍电梯"])
        self.assertIn("东侧门厅", row["无障碍电梯"])
        self.assertEqual("V2.2", typed_rows(md, "9 图则口径", "用户图号")[0]["版本"])
        interface = typed_rows(md, "10 专业界面、依据与自检", "截止日期")[0]
        self.assertEqual("2026-10-18前", interface["截止日期"])
        self.assertEqual("结构", interface["接收专业"])

    def test_narrative_structure_preserves_two_buildings_and_member_load_units(self):
        md = draft("structure", "起草结构提纲，1#办公楼是框架，柱子KZ-6用C45，活载按荷载表取2.7；"
                   "2#仓库门式刚架，钢柱GZ-3用Q355D，活载业主说按7.8kN考虑。仓库基础型式还没定。")
        objects = typed_rows(md, "1 工程概况", "单体")
        self.assertEqual(["1#办公楼", "2#仓库"], [row["单体"] for row in objects])
        self.assertEqual(["框架", "门式刚架"], [row["结构体系"] for row in objects])
        loads = typed_rows(md, "4 荷载与组合", "活载")
        self.assertEqual(["1#办公楼", "2#仓库"], [row["关联单体"] for row in loads])
        self.assertEqual(["KZ-6", "GZ-3"], [row["构件 / 部位"] for row in loads])
        self.assertEqual(["2.7", "7.8kN"], [row["活载"] for row in loads])
        material = typed_rows(md, "5 材料", "用户混凝土等级")
        self.assertEqual("C45", material[0]["用户混凝土等级"])
        self.assertEqual("Q355D", material[1]["用户钢材等级"])
        self.assertEqual("[A001] 待填", material[1]["用户混凝土等级"])
        self.assertTrue(all(row[-1] == "UNSPECIFIED" for row in table_rows(chapter(md, "7 计算步骤"))[1:]))

    def test_narrative_structure_member_dimensions_do_not_swallow_material_ids(self):
        md = draft("structure", "起草结构提纲，3#楼，框架剪力墙结构，地上29层，建筑高度81.8米，"
                   "设防烈度7度0.16g，二层转换梁KZL-4跨度8.7米，截面720×1530，混凝土C40，钢筋HRB400。")
        row = typed_rows(md, "9 构件复核清单", "用户跨度")[0]
        self.assertEqual(("KZL-4", "8.7米", "720×1530"), tuple(row[key] for key in ("构件", "用户跨度", "用户截面")))
        quake = typed_rows(md, "8 抗震概念与薄弱部位", "用户地震参数")[0]
        self.assertEqual("7度0.16g", quake["用户地震参数"])
        self.assertEqual("C40", typed_rows(md, "5 材料", "用户混凝土等级")[0]["用户混凝土等级"])

    def test_late_foundation_label_belongs_to_building_not_current_beam(self):
        md = draft("structure", "单体：5#楼\n构件：LL-13连梁\n混凝土等级：C35\n基础型式 筏板基础")
        row = typed_rows(md, "6 地基基础", "用户基础型式")[0]
        self.assertEqual("5#楼", row["单体"])
        self.assertEqual("筏板基础", row["用户基础型式"])

    def test_narrative_geotech_holes_and_layers_keep_unknown_water_and_phi_local(self):
        md = draft("geotech", "地勘摘录帮我理成表：ZK4那个孔打了38.7米，水位2.45，"
                   "第3层是粉砂，c给的6.7、φ29.8；ZK12孔深42.6，第3层变成淤泥质粉质黏土了，"
                   "c是13.9，φ没写，水位也没量。")
        holes = typed_rows(md, "5 勘探工作量与试验", "用户孔号")
        self.assertEqual(["ZK4", "ZK12"], [row["用户孔号"] for row in holes])
        self.assertEqual(["38.7米", "42.6"], [row["孔深"] for row in holes])
        layers = typed_rows(md, "6 地层与参数摘录", "用户层号")
        self.assertEqual(["ZK4", "ZK12"], [row["用户孔号"] for row in layers])
        self.assertEqual(["6.7", "13.9"], [row["黏聚力 c"] for row in layers])
        self.assertEqual(["29.8", "未在原文检出"], [row["φ"] for row in layers])
        self.assertTrue(all(row["用户承载力记录"] == "未在原文检出" for row in layers))
        water = typed_rows(md, "4 场地与地下水", "用户地下水位")
        self.assertEqual(["2.45", "未在原文检出"], [row["用户地下水位"] for row in water])

    def test_geotech_space_hole_id_preserves_negative_depth_and_report_reference(self):
        md = draft("geotech", "地勘报告：YT-2026-059（中间成果）\n孔号 BH13\n孔深：33.4m\n"
                   "地下水位：埋深1.85m\n层号：④\n岩土名称：淤泥质黏土\n层底深度 19.7m\nc：11.3kPa\nφ：8.7°")
        holes = typed_rows(md, "5 勘探工作量与试验", "用户孔号")
        self.assertEqual("BH13", holes[0]["用户孔号"])
        row = typed_rows(md, "6 地层与参数摘录", "用户层号")[0]
        self.assertEqual(("BH13", "④", "19.7m", "11.3kPa", "8.7°"),
                         tuple(row[key] for key in ("用户孔号", "用户层号", "层深", "黏聚力 c", "φ")))
        self.assertIn("YT-2026-059", chapter(md, "1 任务与阶段"))

    def test_narrative_facade_relative_type_reference_updates_only_its_system(self):
        md = draft("facade", "A座有两种幕墙，生成说明。大堂东立面那块是全玻幕墙，编号QB-02，层高9.7，"
                   "风压设计院说按2.47算；塔楼标准层是单元式的UC-04，风压3.36，层高4.6，水密要1100Pa。"
                   "全玻那块气密水密都还没给，预埋单元式用槽式埋件，全玻的还不知道。")
        rows = typed_rows(md, "4 抗风、气密、水密与层间变位", "用户风压")
        self.assertEqual(["QB-02", "UC-04"], [row["幕墙"] for row in rows])
        self.assertEqual(["2.47", "3.36"], [row["用户风压"] for row in rows])
        self.assertEqual(["[A001] 待填", "1100Pa"], [row["水密要求"] for row in rows])
        embed = typed_rows(md, "3 预埋件与主体结构接口", "预埋件")
        self.assertEqual(["[A001] 待填", "槽式埋件"], [row["预埋件"] for row in embed])
        self.assertNotIn("2.47kPa", md)

    def test_facade_space_fields_keep_literal_fraction_and_user_source(self):
        md = draft("facade", "幕墙编号：MQ-03\n体系：构件式明框玻璃幕墙\n设计风压：3.13kPa\n"
                   "风压来源：结构提资单JG-TZ-018\n层高 4.0m\n层间变位 1/450\n后置锚栓 M14化学锚栓")
        row = typed_rows(md, "1 体系选型", "用户体系")[0]
        self.assertEqual("构件式明框玻璃幕墙", row["用户体系"])
        self.assertEqual("4.0m", row["层高"])
        row = typed_rows(md, "4 抗风、气密、水密与层间变位", "层间变位")[0]
        self.assertEqual("1/450", row["层间变位"])
        self.assertEqual("结构提资单JG-TZ-018", typed_rows(md, "3 预埋件与主体结构接口", "用户图纸来源")[0]["用户图纸来源"])

    def test_a_unique_member_revisit_restores_its_own_building_and_region(self):
        md = draft("structure", "1#办公楼，辖区：CN，柱子KZ-6用C45；2#仓库，钢柱GZ-3用Q355D。"
                   "柱子KZ-6跨度6.7米。")
        rows = typed_rows(md, "9 构件复核清单", "用户跨度")
        self.assertEqual(["KZ-6", "GZ-3"], [row["构件"] for row in rows])
        self.assertEqual(["6.7米", "[A001] 待填"], [row["用户跨度"] for row in rows])
        self.assertEqual(["CN", "UNSPECIFIED"], [row["对象辖区"] for row in rows])

    def test_same_member_code_without_a_named_building_is_not_assigned_to_last_building(self):
        md = draft("structure", "1#办公楼，柱子KZ-6用C45；2#仓库，柱子KZ-6用C35。"
                   "柱子KZ-6跨度6.7米，没说是哪栋。")
        rows = typed_rows(md, "4 荷载与组合", "构件 / 部位")
        self.assertEqual(["1#办公楼", "2#仓库", "[A001] 待填"], [row["关联单体"] for row in rows])
        members = typed_rows(md, "9 构件复核清单", "用户跨度")
        self.assertEqual(["[A001] 待填", "[A001] 待填", "6.7米"], [row["用户跨度"] for row in members])

    def test_pure_colon_foundation_input_is_also_unit_level_not_member_level(self):
        md = draft("structure", "单体：5#楼\n构件：梁A；跨度：6m\n基础型式：筏板基础")
        row = typed_rows(md, "6 地基基础", "用户基础型式")[0]
        self.assertEqual("筏板基础", row["用户基础型式"])

    def test_named_building_revisit_can_disambiguate_its_duplicate_member(self):
        md = draft("structure", "1#办公楼，辖区：CN，柱子KZ-6用C45；"
                   "2#仓库，辖区：SG，柱子KZ-6用C35。1#办公楼，柱子KZ-6跨度6.7米。")
        rows = typed_rows(md, "9 构件复核清单", "用户跨度")
        self.assertEqual(["6.7米", "[A001] 待填"], [row["用户跨度"] for row in rows])
        self.assertEqual(["CN", "SG"], [row["对象辖区"] for row in rows])

    def test_kindergarten_is_a_separate_building_not_more_area_on_the_school(self):
        md = draft("architecture", "生成建筑设计说明。1#教学楼，地上5层，总建筑面积5140平米，高23.6米；"
                   "2#幼儿园，三层，总建筑面积2380平米，高度还没给。")
        rows = typed_rows(md, "1 工程概况与设计范围", "建筑高度")
        self.assertEqual(["1#教学楼", "2#幼儿园"], [row["单体"] for row in rows])
        self.assertEqual(["5140平米", "2380平米"], [row["建筑面积"] for row in rows])
        self.assertEqual(["5层", "三层"], [row["层数"] for row in rows])
        self.assertEqual(["23.6米", "[A001] 待填"], [row["建筑高度"] for row in rows])

    def test_unrecognised_numbered_building_never_contaminates_the_prior_building(self):
        md = draft("architecture", "1#教学楼，总建筑面积5140平米，高23.6米；"
                   "西侧2#研发中心，总建筑面积2380平米，高29.7米。")
        rows = typed_rows(md, "1 工程概况与设计范围", "建筑高度")
        self.assertEqual(["1#教学楼", "[A001] 待填"], [row["单体"] for row in rows])
        self.assertEqual(["5140平米", "2380平米"], [row["建筑面积"] for row in rows])
        self.assertEqual(["23.6米", "29.7米"], [row["建筑高度"] for row in rows])

    def test_unique_member_revisit_updates_its_foundation_not_the_last_building(self):
        md = draft("structure", "1#办公楼，柱子KZ-6用C45；2#仓库，钢柱GZ-3用Q355D。"
                   "柱子KZ-6跨度6.7米，基础型式 筏板基础。")
        rows = typed_rows(md, "6 地基基础", "用户基础型式")
        self.assertEqual(["筏板基础", "[A001] 待填"], [row["用户基础型式"] for row in rows])

    def test_named_hole_revisit_restores_local_region_and_only_its_layer(self):
        md = draft("geotech", "孔号 BH13\n辖区：CN\n层号：④\nc：11.3kPa\n"
                   "孔号 BH14\n辖区：SG\n层号：④\nc：12.3kPa\n孔号 BH13\n层号 ④\nφ 12.4度")
        rows = typed_rows(md, "6 地层与参数摘录", "用户层号")
        self.assertEqual(["BH13", "BH14"], [row["用户孔号"] for row in rows])
        self.assertEqual(["12.4度", "未在原文检出"], [row["φ"] for row in rows])
        self.assertEqual(["CN", "SG"], [row["对象辖区"] for row in rows])

    def test_ambiguous_layer_revisit_cannot_choose_the_last_hole(self):
        md = draft("geotech", "孔号 BH13\n层号：④\nc：11.3kPa\n"
                   "孔号 BH14\n层号：④\nc：12.3kPa\n层号 ④\nφ 12.4度")
        rows = typed_rows(md, "6 地层与参数摘录", "用户层号")
        self.assertEqual(["BH13", "BH14", "未在原文检出"], [row["用户孔号"] for row in rows])
        self.assertEqual(["未在原文检出", "未在原文检出", "12.4度"], [row["φ"] for row in rows])

    def test_member_table_revisit_preserves_its_original_body_and_region(self):
        md = draft("structure", "|单体|构件|混凝土等级|辖区|\n|---|---|---|---|\n"
                   "|1#办公楼|KZ-6|C45|CN|\n|2#仓库|GZ-3|C35|SG|\n柱子KZ-6跨度6.7米。")
        rows = typed_rows(md, "9 构件复核清单", "用户跨度")
        self.assertEqual(["6.7米", "[A001] 待填"], [row["用户跨度"] for row in rows])
        self.assertEqual(["CN", "SG"], [row["对象辖区"] for row in rows])
        self.assertEqual(["1#办公楼", "2#仓库"], [row["关联单体"] for row in typed_rows(md, "4 荷载与组合", "构件 / 部位")])

    def test_local_region_and_later_layer_values_do_not_cross_boreholes(self):
        md = draft("geotech", "孔号 BH13\n辖区：CN\n层号：④\nc：11.3kPa\n"
                   "孔号 BH14\n层号：④\nφ 12.4度")
        rows = typed_rows(md, "6 地层与参数摘录", "用户层号")
        self.assertEqual(["BH13", "BH14"], [row["用户孔号"] for row in rows])
        self.assertEqual(["11.3kPa", "未在原文检出"], [row["黏聚力 c"] for row in rows])
        self.assertEqual(["CN", "UNSPECIFIED"], [row["对象辖区"] for row in rows])

    def test_basic_design_examples_and_report_codes_do_not_become_parameters(self):
        cases = (
            ("architecture", "单体：教学楼\n建筑高度按图号A-2026-031取，未提供高度", "1 工程概况与设计范围", "建筑高度"),
            ("structure", "构件：梁A\n跨度比如6m只是示例", "9 构件复核清单", "用户跨度"),
            ("geotech", "孔号 BH13\n层号：④\nc只给报告YT-2026-031\nφ没写", "6 地层与参数摘录", "黏聚力 c"),
            ("facade", "幕墙编号：MQ-03\n层间变位：比如1/450只是示例", "4 抗风、气密、水密与层间变位", "层间变位"),
        )
        for post, text, title, label in cases:
            with self.subTest(post=post):
                rows = typed_rows(draft(post, text), title, label)
                self.assertTrue(all(row[label] in {"[A001] 待填", "未在原文检出"} for row in rows))

    def test_facade_ambiguous_type_reference_stays_unassigned_and_does_not_claim_acceptance(self):
        md = draft("facade", "A座有两种幕墙。单元式的UC-04，风压3.36；单元式的UC-05，风压3.46。"
                   "预埋单元式用槽式埋件，没说是哪个系统。")
        rows = typed_rows(md, "3 预埋件与主体结构接口", "预埋件")
        self.assertEqual(["[A001] 待填", "[A001] 待填", "槽式埋件"], [row["预埋件"] for row in rows])
        self.assertEqual("[A001] 待填", rows[2]["幕墙"])
        self.assertTrue(all(row["本轮状态"] == "本轮未执行验收" for row in typed_rows(md, "8 验收资料", "用户验收资料")))

    def test_ambiguous_facade_type_across_buildings_has_no_last_building_owner(self):
        md = draft("facade", "A座，单元式的UC-04，风压3.36；B座，单元式的UC-05，风压3.46。"
                   "预埋单元式用槽式埋件，没说是哪个系统。")
        rows = typed_rows(md, "1 体系选型", "关联单体")
        self.assertEqual(["A座", "B座", "[A001] 待填"], [row["关联单体"] for row in rows])
        embed = typed_rows(md, "3 预埋件与主体结构接口", "预埋件")
        self.assertEqual(["[A001] 待填", "[A001] 待填", "槽式埋件"], [row["预埋件"] for row in embed])


class DesignRuntimeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="civil-design-basic-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for context in (
            patch.object(expert_turn, "_OUT", self.root),
            patch("packing_assistant.runtime.memory._OUT", self.root),
            patch("packing_assistant.runtime.scheduler.get_scheduler", return_value=Scheduler()),
            patch.dict(os.environ, {"CIVIL_SANDBOX_ROOTS": str(self.root), "CIVIL_JOB_ROOT": "", "CIVIL_SANDBOX": "workspace-write", "CIVIL_APPROVAL": "on-request", "PYTHON_DOTENV_DISABLED": "1"}),
        ):
            context.start()
            self.addCleanup(context.stop)

    def _artifacts(self, result: dict, expected: list[str]) -> str:
        self.assertTrue(result.get("wrote"), result)
        self.assertTrue(result["submit_blocked"])
        md = next(Path(item["path"]) for item in result["files"] if item["path"].endswith(".md"))
        text = md.read_text(encoding="utf-8")
        xlsx = next(Path(item["path"]) for item in result["files"] if item["path"].endswith(".xlsx"))
        import openpyxl
        book = openpyxl.load_workbook(xlsx, data_only=False)
        try:
            values = [str(cell.value) for sheet in book for row in sheet for cell in row if cell.value is not None]
            for value in expected:
                self.assertIn(value, values)
            self.assertFalse(any(cell.data_type == "f" for sheet in book for row in sheet for cell in row))
        finally:
            book.close()
        return text

    def test_all_four_expert_turns_export_actual_specialized_markdown_and_excel(self):
        fixtures = {
            "architecture": ("单体：导出建筑；建筑面积：4560m2\n单体：导出建筑B；功能：仓储", ["导出建筑", "4560m2", "导出建筑B", "仓储"]),
            "structure": ("构件编号：导出构件；活载：用户2kPa\n构件编号：导出构件B；恒载：用户5kPa", ["导出构件", "用户2kPa", "导出构件B", "用户5kPa"]),
            "geotech": ("孔号：导出孔A；层号：导出层1；土层：用户黏土；c：用户12kPa\n孔号：导出孔B；层号：导出层2；土层：用户砂土；φ：用户30度", ["导出孔A", "导出层1", "用户12kPa", "导出孔B", "导出层2", "用户30度"]),
            "facade": ("幕墙编号：导出幕墙；风压：用户3kPa\n幕墙编号：导出幕墙B；风压：用户4kPa", ["导出幕墙", "用户3kPa", "导出幕墙B", "用户4kPa"]),
        }
        for post, (fields, expected) in fixtures.items():
            with self.subTest(post=post):
                result = expert_turn.run_expert_turn("写一份内部专业草稿\n" + fields, post, session_id=f"export-{post}", confirm_ok=True, force_intent="run")
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["state"], "done")
                self.assertIn(TOOLS[post], result["tools_run"])
                self._artifacts(result, expected)

    def test_all_four_named_tools_generate_complete_empty_drafts(self):
        for post, tool in TOOLS.items():
            with self.subTest(post=post):
                result = expert_turn.run_named_exclusive(tool, {"text": "", "confirm_ok": True, "session_id": f"empty-{post}"})
                text = self._artifacts(result, ["[A001] 待填", "UNSPECIFIED"])
                self.assertIn("- 辖区：UNSPECIFIED", text)

    def test_high_risk_posts_reject_missing_and_wrong_typed_confirmation(self):
        for post in ("structure", "geotech", "facade"):
            for value in (False, "true", "false", 1, [True]):
                with self.subTest(post=post, value=value):
                    result = expert_turn.run_named_exclusive(TOOLS[post], {"text": "写内部草稿", "confirm_ok": value, "p0_confirmed": value, "session_id": f"gate-{post}"})
                    self.assertFalse(result["wrote"])
                    self.assertTrue(result["hitl_pending"])
                    self.assertEqual(result["files"], [])
                    self.assertIn("我明白，将由持证人员签认", result["reply"])
        self.assertFalse(list(self.root.rglob("*.md")))

    def test_low_risk_architecture_follows_its_roster_without_extra_gate(self):
        result = expert_turn.run_named_exclusive("architecture__memo", {"text": "单体：低风险建筑", "session_id": "architecture-low"})
        self.assertFalse(result.get("hitl_pending"))
        self._artifacts(result, ["低风险建筑"])

    def test_high_risk_expert_turn_unconfirmed_stops_before_builder(self):
        for post in ("structure", "geotech", "facade"):
            with self.subTest(post=post), patch("packing_assistant.post_drafts.design_basic.build_draft", wraps=build_draft) as builder:
                result = expert_turn.run_expert_turn("写一份专业草稿", post, session_id=f"turn-gate-{post}", force_intent="run")
                self.assertFalse(result["wrote"])
                self.assertEqual(result["state"], "waiting_hitl")
                builder.assert_not_called()

    def test_all_four_chat_paths_do_not_create_artifacts(self):
        engine = default_engine()
        for post, tool in TOOLS.items():
            with self.subTest(post=post):
                result = expert_turn.run_expert_turn("怎么理解这个岗位的输入资料？", post, session_id=f"chat-{post}")
                self.assertEqual(result["intent"], "chat")
                self.assertFalse(result["wrote"])
                self.assertEqual(result["files"], [])
                denied = engine.execute(tool, {"text": "写一份草稿", "confirm_ok": True}, expert_id=post, intent="chat")
                self.assertFalse(denied["ok"])
                self.assertEqual(denied["error_code"], "permission_denied")
        self.assertFalse(list(self.root.rglob("*.md")))
        self.assertFalse(list(self.root.rglob("*.xlsx")))

    def test_read_only_rejects_all_named_writes_even_with_confirmation(self):
        with patch.dict(os.environ, {"CIVIL_SANDBOX": "read-only"}):
            for post, tool in TOOLS.items():
                with self.subTest(post=post):
                    result = expert_turn.run_named_exclusive(tool, {"text": "写内部草稿", "confirm_ok": True, "session_id": f"ro-{post}"})
                    self.assertFalse(result["wrote"])
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["files"], [])
        self.assertFalse(list(self.root.rglob("*.md")))

    def test_tool_engine_keeps_specialized_values_and_excel_symbols_literal(self):
        result = default_engine().execute("structure__calc_outline", {"text": "构件编号：=2+3；截面：x<10且A|B", "confirm_ok": True, "session_id": "symbols"}, expert_id="structure", intent="run")
        self.assertTrue(result["ok"], result)
        text = self._artifacts(result, ["=2+3", "x<10且A|B"])
        self.assertIn("x&lt;10且A&#124;B", chapter(text, "9 构件复核清单"))

    def test_compact_adapter_table_exports_original_values_and_local_scopes(self):
        text = "|构件编号|截面|辖区|\n|---|---|---|\n|=A&#124;B &lt; 10|字面&amp;lt;|CN|\n||用户第二截面||"
        result = expert_turn.run_named_exclusive("structure__calc_outline", {"text": text, "confirm_ok": True, "session_id": "table-adapter"})
        markdown = self._artifacts(result, ["=A|B < 10", "字面&lt;", "用户第二截面", "CN", "UNSPECIFIED"])
        rows = table_rows(chapter(markdown, "9 构件复核清单"))[1:3]
        self.assertEqual(rows[0][-1], "CN")
        self.assertEqual(rows[1][0], "[A001] 待填")
        self.assertEqual(rows[1][-1], "UNSPECIFIED")

    def test_single_suffix_identity_exports_the_given_area_in_its_body(self):
        result = expert_turn.run_named_exclusive("architecture__memo", {"text": "建筑面积：400m2；单体：后置单体", "session_id": "suffix-building"})
        markdown = self._artifacts(result, ["后置单体", "400m2"])
        rows = table_rows(chapter(markdown, "1 工程概况与设计范围"))
        self.assertEqual(next(row for row in rows if row[0] == "后置单体")[2], "400m2")


if __name__ == "__main__":
    unittest.main()
