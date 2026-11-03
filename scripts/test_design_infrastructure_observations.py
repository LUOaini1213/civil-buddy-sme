#!/usr/bin/env python3
"""Infrastructure literal ownership, sources and independent child records."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packing_assistant.office_job import tables_from_md
from packing_assistant.post_drafts.design_infrastructure import PROFILES, UNKNOWN, build_draft
from packing_assistant.post_drafts.design_infrastructure_observations import parse


def draft(post, text):
    return build_draft(post, PROFILES[post].tool, text)


def rows(markdown, title):
    table = dict(tables_from_md(markdown))[title]
    return [dict(zip(table[0], row)) for row in table[1:]]


class InfrastructureObservationTests(unittest.TestCase):
    def test_natural_municipal_speed_does_not_replace_width_or_elevation(self):
        text = "东环路按城市次干路做，桩号K0+010到K1+720，红线宽24米，设计速度60km/h，现状地面标高21.45米。"
        row = rows(draft("municipal", text), "道路区段专业核对")[0]
        self.assertEqual("东环路", row["道路名称"])
        self.assertEqual("24米", row["红线宽"])
        self.assertEqual("60km/h", row["设计速度"])
        self.assertEqual("21.45米", row["现状地面标高"])
        self.assertEqual(UNKNOWN, row["设计高程"])

    def test_natural_bridge_span_is_literal_not_arithmetic_or_width(self):
        text = "东河大桥跨径3×27米，桥宽23.6米，下部双柱墩配钻孔灌注桩。"
        row = rows(draft("bridge", text), "桥梁比选与输入来源")[0]
        self.assertEqual("3×27米", row["跨径组合"])
        self.assertEqual("23.6米", row["桥宽"])
        self.assertEqual("钻孔灌注桩", row["基础型式"])
        self.assertNotIn("81米", draft("bridge", text))

    def test_each_post_has_local_file_project_and_source_boundaries(self):
        fields = {"municipal": ("红线宽", "29m"), "bridge": ("跨径", "31m"),
                  "port": ("高水位", "4.23m"), "tunnel": ("埋深", "38m"),
                  "traffic": ("交通量", "891pcu/h"), "design-coord": ("期限", "周五")}
        for post, profile in PROFILES.items():
            with self.subTest(post=post):
                identity = profile.identity.split("|")[0]
                key, literal = fields[post]
                text = (f"【用户上传：同名.csv】\n项目：甲；辖区：CN；适用依据：文件甲\n"
                        f"{identity}：对象甲；{key}：{literal}；资料来源：来源甲\n"
                        f"【用户上传：同名.csv】\n项目：乙\n{identity}：对象乙")
                a, b = parse(post, text)
                self.assertEqual("甲", a["项目名称"])
                self.assertEqual("乙", b["项目名称"])
                self.assertNotEqual(a["_file"], b["_file"])
                self.assertEqual(UNKNOWN, b["_project_zone"])
                self.assertNotIn("适用依据", b)
                self.assertNotIn("资料来源", b)
                self.assertNotIn(literal, str(b))
                self.assertIn("同名.csv", a["_sources"]["资料来源"])

    def test_late_project_does_not_relabel_previous_object(self):
        for post, profile in PROFILES.items():
            with self.subTest(post=post):
                identity = profile.identity.split("|")[0]
                a, b = parse(post, f"{identity}：对象甲\n项目：后来项目\n{identity}：对象乙")
                self.assertNotIn("项目名称", a)
                self.assertEqual("后来项目", b["项目名称"])
                self.assertNotEqual(a["_context"], b["_context"])

    def test_stage_change_is_a_real_scope_boundary(self):
        a, b = parse("bridge", "项目：甲；设计阶段：初步设计\n桥名：一桥；跨径：31m\n设计阶段：施工图设计\n桥名：一桥")
        self.assertEqual("初步设计", a["设计阶段"])
        self.assertEqual("施工图设计", b["设计阶段"])
        self.assertNotIn("跨径组合", b)
        self.assertNotEqual(a["_context"], b["_context"])

    def test_preamble_sources_have_positions_without_becoming_object_jurisdiction(self):
        md = draft("bridge", "【用户上传：依据.md】\n项目：甲；辖区：CN；适用依据：CN资料\n桥名：一桥")
        fields = rows(md, "对象参数与来源逐项登记")
        basis = next(r for r in fields if r["参数栏目"] == "适用依据")
        self.assertIn("依据.md", basis["输入位置（非原件页码）"])
        self.assertIn("输入第2行（非原件页码）", basis["输入位置（非原件页码）"])
        self.assertIn("- 对象辖区：UNSPECIFIED", md)

    def test_country_names_in_object_or_file_do_not_select_law(self):
        md = draft("bridge", "桥名：新加坡大桥；地质资料：中国报告.pdf")
        self.assertIn("- 辖区：UNSPECIFIED", md)
        self.assertNotIn("| SG |", md)
        self.assertNotIn("| CN |", md)

    def test_metadata_only_uses_latest_file_not_initial_empty_scope(self):
        row = parse("port", "【用户上传：资料.md】\n项目：仅资料项目；辖区：EU")[0]
        self.assertEqual("仅资料项目", row["项目名称"])
        self.assertEqual("EU", row["_project_zone"])
        self.assertEqual("资料.md", row["_origin"])

    def test_unique_late_berth_reference_does_not_attach_to_last_berth(self):
        a, b = parse("port", "1#泊位，高水位4.23m；2#泊位，高水位4.57m。地勘只有1#做了，报告号DK-2031-811。2#还在钻。")
        self.assertEqual("DK-2031-811", a["地质资料"])
        self.assertNotIn("地质资料", b)
        self.assertNotIn("DK-2031-811", str(b))
        self.assertIn("还在钻", b["待核记录"])

    def test_ambiguous_late_berth_reference_stays_unassigned(self):
        records = parse("port", "码头：1#泊位；高水位：4.2m\n码头：1#泊位；高水位：4.7m\n地勘只有1#做了，报告号DK-2031-812")
        self.assertEqual(3, len(records))
        self.assertTrue(all("地质资料" not in r for r in records[:2]))
        self.assertNotIn("码头名称", records[-1])
        self.assertEqual("DK-2031-812", records[-1]["地质资料"])

    def test_late_berth_reference_cannot_cross_file(self):
        records = parse("port", "【用户上传：甲.md】\n1#泊位，高水位4.2m\n【用户上传：乙.md】\n地勘只有1#做了，报告号DK-2031-813")
        self.assertNotIn("地质资料", records[0])
        self.assertNotIn("码头名称", records[-1])

    def test_segment_parent_name_is_not_a_source_for_sibling_parameters(self):
        text = "山岭隧道的设计提纲，进口段K3+010到K3+210，五级围岩，最大埋深12.9m，准备上CRD法，超前用Φ108钢管。洞身段K3+210到K4+980，三级围岩，最大埋深147m，全断面开挖。"
        a, b = parse("tunnel", text)
        self.assertEqual("山岭隧道", a["隧道总体名称"])
        self.assertEqual("山岭隧道", b["隧道总体名称"])
        self.assertEqual("12.9m", a["埋深"])
        self.assertEqual("147m", b["埋深"])
        self.assertNotIn("超前支护资料", b)
        self.assertNotIn("围岩来源", a)

    def test_groundwater_depth_is_not_tunnel_overburden(self):
        row = parse("tunnel", "隧道：甲；埋深：19m，地下水：潜水，水位埋深3.7m")[0]
        self.assertEqual("19m", row["埋深"])
        self.assertIn("3.7m", row["地下水"])
        self.assertNotIn("3.7m", row["埋深"])

    def test_groundwater_colon_and_unsplit_narrative_do_not_change_cover(self):
        for water in ("水位埋深：3.7m", "地下水位埋深 3.7m", "地下水是潜水水位埋深3.7m"):
            with self.subTest(water=water):
                row = parse("tunnel", "隧道：甲；埋深：19m，"+water)[0]
                self.assertEqual("19m", row["埋深"])
                self.assertIn("3.7m", row["地下水"])

    def test_traffic_movements_have_separate_flow_sources_and_signals(self):
        text = ("节点：节点甲；调查时段：07:20-08:20；东进口左转301pcu/h；流量文件：调查甲.csv；信号周期：109秒；"
                "西进口右转172辆；流量文件：调查乙.csv")
        data = rows(draft("traffic", text), "交通调查与实验待核")
        self.assertEqual(2, len(data))
        self.assertEqual(["东进口左转", "西进口右转"], [r["方向车型"] for r in data])
        self.assertEqual(["301pcu/h", "172辆"], [r["交通量"] for r in data])
        self.assertEqual(["调查甲.csv", "调查乙.csv"], [r["流量文件"] for r in data])
        self.assertEqual(["109秒", UNKNOWN], [r["现状信号周期"] for r in data])
        self.assertTrue(all(r["调查时段"] == "07:20-08:20" for r in data))

    def test_traffic_period_change_does_not_borrow_direction_vehicle_or_file(self):
        text = ("节点：节点甲；调查时段：07:20-08:20；方向：东进口直行；车型：小客车；流量：301pcu/h；流量文件：调查甲.csv\n"
                "调查时段：17:20-18:20；交通量：209辆")
        a, b = rows(draft("traffic", text), "交通调查与实验待核")
        self.assertEqual("17:20-18:20", b["调查时段"])
        self.assertEqual(UNKNOWN, b["方向车型"])
        self.assertEqual(UNKNOWN, b["用户车型"])
        self.assertEqual(UNKNOWN, b["流量文件"])
        self.assertEqual("209辆", b["交通量"])
        self.assertNotIn("209辆", str(a))

    def test_same_clause_with_two_movements_does_not_drop_second(self):
        data = rows(draft("traffic", "节点：节点甲；东进口左转301pcu/h和西进口右转172辆"), "交通调查与实验待核")
        self.assertEqual(["301pcu/h", "172辆"], [r["交通量"] for r in data])

    def test_traffic_reordered_matrix_is_one_child_per_physical_row(self):
        text = "|交通量|流量文件|节点|方向|调查时段|车型|\n|---|---|---|---|---|---|\n|301pcu/h|a.csv|节点甲|东进口直行|07:20-08:20|小客车|\n|209辆||节点甲||17:20-18:20||"
        records = parse("traffic", text)
        self.assertEqual(2, len(records))
        self.assertTrue(all(len(r["_traffic_rows"]) == 1 for r in records))
        self.assertEqual("301pcu/h", records[0]["_traffic_rows"][0]["交通量"])
        self.assertEqual(UNKNOWN, records[1]["_traffic_rows"][0]["流量文件"])

    def test_each_pipe_keeps_its_own_owner_and_source(self):
        text = ("道路名称：东路；起止桩号：K0+010到K0+200；现状管线：DN300供水；权属单位是单位甲；管线来源：图甲\n"
                "现状管线：DN200燃气；权属单位是单位乙")
        data = rows(draft("municipal", text), "管线接口逐项归属")
        self.assertEqual(["DN300供水", "DN200燃气"], [r["现状管线"] for r in data])
        self.assertEqual(["单位甲", "单位乙"], [r["管线权属单位"] for r in data])
        self.assertEqual(["图甲", UNKNOWN], [r["管线来源"] for r in data])
        self.assertTrue(all(r["签认"] == "" for r in data))

    def test_pipe_name_and_existing_description_are_complementary_not_new_pipes(self):
        data = rows(draft("municipal", "道路名称：东路；管线名称：供水甲；现状管线：DN300供水；管线权属单位：单位甲"), "管线接口逐项归属")
        self.assertEqual(1, len(data))
        self.assertEqual("供水甲", data[0]["管线名称"])
        self.assertEqual("DN300供水", data[0]["现状管线"])

    def test_unknown_numeric_update_removes_old_value_and_source(self):
        row = parse("bridge", "桥名：一桥；跨径：31m\n跨径：还没定")[0]
        self.assertEqual(UNKNOWN, row["跨径组合"])
        self.assertNotIn("跨径组合", row["_sources"])
        self.assertIn("31m", row["待核记录"])

    def test_unknown_flow_update_clears_same_movement_not_adds_another(self):
        row = parse("traffic", "节点：节点甲；方向：东进口左转；流量：301pcu/h\n交通量：还没定")[0]
        self.assertEqual(1, len(row["_traffic_rows"]))
        child = row["_traffic_rows"][0]
        self.assertEqual(UNKNOWN, child["交通量"])
        self.assertNotIn("交通量", child["_sources"])
        self.assertIn("301pcu/h", child["待核记录"])

    def test_pending_documents_are_not_available_sources(self):
        tests = (("bridge", "地质资料", "地勘报告尚未提供"),
                 ("port", "地质来源", "计划提供报告A"),
                 ("traffic", "模型文件", "预计生成模型A.inpx"),
                 ("design-coord", "状态证据", "答应月底提供记录"))
        for post, key, value in tests:
            with self.subTest(post=post):
                row = parse(post, PROFILES[post].identity.split("|")[0]+f"：对象甲；{key}：{value}")[0]
                self.assertEqual(UNKNOWN, row[key])
                self.assertIn(value, row["待核记录"])

    def test_decision_requires_its_own_evidence_and_never_means_approval(self):
        for value in ("修改梁位", "口头答应修改梁位", "计划修改梁位", "讨论修改梁位"):
            with self.subTest(value=value):
                md = draft("design-coord", "问题编号：Q-A；决议："+value)
                self.assertIn("| 已议定事项 | UNSPECIFIED |", md)
                self.assertIn("| 待定事项 | "+value+" |", md)
        md = draft("design-coord", "问题编号：Q-A；决议：补测净高；决议证据：会议原件A\n问题编号：Q-B；决议：修改梁位")
        first, second = md.split("# 独立材料记录 2", 1)
        self.assertIn("| 已议定事项 | 补测净高 |", first)
        self.assertIn("| 已议定事项 | UNSPECIFIED |", second)
        self.assertNotIn("会议原件A", second)
        self.assertIn("| 问题闭环核验 | UNSPECIFIED |", first)

    def test_examples_are_not_real_objects_and_actual_reset_restores_input(self):
        data = parse("bridge", "假设桥名：示例桥；跨径：999m\n桥名：示例桥二；跨径：888m\n本次实际情况：桥名：实际桥；跨径：31m")
        self.assertEqual(1, len(data))
        self.assertEqual("实际桥", data[0]["桥梁名称"])
        self.assertEqual("31m", data[0]["跨径组合"])
        self.assertNotIn("999m", str(data))
        self.assertNotIn("888m", str(data))

    def test_literal_units_and_cell_entities_are_not_normalized_or_double_decoded(self):
        md = draft("bridge", "|桥名|桥宽|跨径|\n|---|---|---|\n|=对象A&#124;B &lt; 9|23600mm|字面&amp;lt;|")
        row = rows(md, "桥梁比选与输入来源")[0]
        self.assertEqual("=对象A|B < 9", row["桥梁名称"])
        self.assertEqual("23600mm", row["桥宽"])
        self.assertEqual("字面&lt;", row["跨径组合"])
        self.assertNotIn("23.6m", md)


if __name__ == "__main__":
    unittest.main()
