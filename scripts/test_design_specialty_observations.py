#!/usr/bin/env python3
"""Specialty literal values, independent scopes and unverified interfaces."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packing_assistant.office_job import tables_from_md
from packing_assistant.post_drafts.design_specialties import TOOLS, UNKNOWN, build_draft
from packing_assistant.post_drafts.design_specialty_observations import parse


def draft(post, text):
    return build_draft(post, TOOLS[post], text)


def table(md, title):
    return dict(tables_from_md(md))[title]


def values(md, title):
    rows = table(md, title)
    return [dict(zip(rows[0], row)) for row in rows[1:]]


class SpecialtyObservationTests(unittest.TestCase):
    def test_crown_without_colon_is_not_part_of_plant_name(self):
        md = draft("landscape", "儿童活动区，甲方苗木表给了红叶石楠球冠幅137，共39株。")
        r = values(md, "5 软景与苗木登记")[0]
        self.assertEqual("红叶石楠球", r["苗木名称"])
        self.assertEqual("137", r["冠幅"])
        self.assertEqual("39株", r["用户数量"])
        self.assertEqual("儿童活动区", r["所属分区"])

    def test_multiple_zones_keep_plants_soil_and_shared_source_separate(self):
        md = draft("landscape", "南入口广场种银杏，胸径19，共25株；宅间绿地种桂花，冠幅251，覆土1.3米。苗木表是甲方苗表L-28。")
        plants = values(md, "苗木与分区来源核对")
        self.assertEqual(["南入口广场", "宅间绿地"], [r["分区"] for r in plants])
        self.assertEqual(["19", UNKNOWN], [r["胸径"] for r in plants])
        self.assertEqual([UNKNOWN, "251"], [r["冠幅"] for r in plants])
        self.assertTrue(all(r["来源"] == "甲方苗表L-28" for r in plants))
        soil = values(md, "6 土壤、顶板覆土与灌排")
        self.assertEqual([UNKNOWN, "1.3米"], [r["用户覆土厚度"] for r in soil])

    def test_each_room_keeps_its_own_node_source_joint_owner_and_waterproof(self):
        md = draft("interior", "房间：甲卫；地面：砖A；防水材料：涂料A；防水上翻：1800mm；节点图号：N-A；节点来源：节点包A；收口责任：张工\n房间：乙卫；地面：砖B；节点图号：N-B；收口责任：李工")
        a, b = values(md, "房间做法与收口责任")
        self.assertEqual(["甲卫", "乙卫"], [a["房间"], b["房间"]])
        self.assertEqual("节点包A", a["节点来源"])
        self.assertNotIn("节点包A", str(b))
        self.assertEqual(["张工", "李工"], [a["收口责任"], b["收口责任"]])
        self.assertEqual(UNKNOWN, b["防水"])
        self.assertEqual(UNKNOWN, b["上翻"])
        self.assertEqual("", b["签认"])

    def test_common_room_narrative_keeps_literal_height_and_upturn_units(self):
        md = draft("interior", "3层公卫，吊顶用铝扣板净高2.37米，防水刷聚氨酯1.7厚，上翻1.9米。茶水间，地面用锁扣地板，上翻300mm。")
        rooms = values(md, "3 防水、防潮与隔声节点")
        self.assertEqual(["1.9米", "300mm"], [r["用户上翻高度"] for r in rooms])
        self.assertEqual("聚氨酯", rooms[0]["防水材料 / 做法"])
        self.assertEqual("1.7", rooms[0]["用户防水厚度"])
        self.assertEqual(UNKNOWN, rooms[1]["防水材料 / 做法"])
        self.assertNotIn("1900mm", md)

    def test_ups_and_mobile_station_requirements_are_not_selections_or_completed_work(self):
        weak = draft("intel-weak", "系统：门禁\n供电：UPS集中供电，后备47分钟\n消防联动：给干接点，尚未联调")
        r = values(weak, "5 供电、UPS、接地与空间提资")[0]
        self.assertEqual("UPS集中供电", r["电源"])
        self.assertEqual("后备47分钟", r["用户 UPS 要求"])
        self.assertEqual("", values(weak, "系统点表与联调边界")[0]["联调结果"])
        self.assertNotIn("kVA", weak)
        defense = draft("civil-defense", "防护单元：F1\n柴油电站：不设固定电站，预留移动电站接口")
        r = values(defense, "6 给排水、柴油电站与转换")[0]
        self.assertIn("不设固定电站", r["柴油电站设置"])
        self.assertIn("预留移动电站接口", r["柴油电站设置"])

    def test_system_reference_restores_only_its_own_route_context(self):
        md = draft("intel-weak", "系统：视频监控覆盖A楼；点数：213；品牌：甲品牌\n系统：门禁；覆盖：B楼；点数：87\n监控走的桥架是201×101；门禁那边桥架规格是151×76")
        routes = values(md, "3 机房、弱电井与桥架路由")
        self.assertEqual(["视频监控", "门禁"], [r["关联子系统"] for r in routes])
        self.assertEqual(["201×101", "151×76"], [r["用户桥架规格"] for r in routes])
        systems = values(md, "2 子系统与用户功能需求")
        self.assertEqual(["213", "87"], [r["用户点数"] for r in systems])
        self.assertEqual(UNKNOWN, systems[1]["用户品牌"])

    def test_duplicate_short_reference_is_unassigned_not_last_system(self):
        data = parse("系统：视频监控；覆盖：A楼\n系统：视频监控；覆盖：B楼\n监控走的桥架是200×100", "intel-weak")
        r = data.records["routes"][0]
        self.assertNotIn("_parent", r)
        self.assertNotIn("system_ref", r)
        self.assertEqual("200×100", r["tray_spec"])

    def test_same_short_reference_cannot_cross_project_scope(self):
        data = parse("项目：甲项目；系统：视频监控；覆盖：A楼\n项目：乙项目；系统：门禁\n监控走的桥架是200×100", "intel-weak")
        r = data.records["routes"][0]
        self.assertNotIn("_parent", r)
        self.assertNotIn("system_ref", r)

    def test_file_boundaries_even_repeated_names_reset_parent_and_source(self):
        data = parse("【用户上传：图纸.csv】\n项目：甲项目；防护单元：FH-01；人防等级：核6级\n【用户上传：图纸.csv】\n口部：西口；图号：K2", "civil-defense")
        r = data.records["mouths"][0]
        self.assertNotIn("unit_ref", r)
        self.assertNotIn("_parent", r)
        self.assertNotEqual(r["_file"], data.records["units"][0]["_file"])
        self.assertEqual(UNKNOWN, data.contexts[r["_context"]]["jurisdiction"])

    def test_stage_change_does_not_join_later_room_or_door_to_earlier_stage(self):
        data = parse("项目：甲；设计阶段：初步设计\n房间：大厅；地面：石材\n设计阶段：施工图设计\n门窗：东门", "interior")
        r = data.records["doors"][0]
        self.assertNotIn("_parent", r)
        self.assertNotIn("object_ref", r)
        self.assertNotEqual(r["_context"], data.records["rooms"][0]["_context"])

    def test_late_project_does_not_relabel_preceding_unassigned_object(self):
        md = draft("interior", "房间：大厅；地面：石材\n项目：后给项目\n房间：办公室；地面：地毯")
        r = values(md, "对象参数、来源与待核记录")
        first = [v for v in r if v["专业对象"] == "大厅"]
        self.assertTrue(first)
        self.assertTrue(all(v["项目 / 分组"] == UNKNOWN for v in first))
        self.assertTrue(all(v["项目 / 分组"] == "后给项目" for v in r if v["专业对象"] == "办公室"))

    def test_each_file_and_project_keeps_its_own_basis(self):
        md = draft("interior", "【用户上传：甲.md】\n项目：甲；CN依据：CN-A\n房间：甲卫\n【用户上传：乙.md】\n项目：乙；SG依据：SG-B\n房间：乙卫")
        a, b = values(md, "各项目与文件依据归属")
        self.assertEqual(["甲", "乙"], [a["项目"], b["项目"]])
        self.assertEqual(["CN-A", UNKNOWN], [a["CN 用户资料"], b["CN 用户资料"]])
        self.assertEqual([UNKNOWN, "SG-B"], [a["SG 用户资料"], b["SG 用户资料"]])
        self.assertEqual(["甲.md", "乙.md"], [a["输入文件"], b["输入文件"]])

    def test_missing_basis_does_not_imply_jurisdiction(self):
        for post in TOOLS:
            with self.subTest(post=post):
                md = draft(post, "CN依据：UNSPECIFIED；SG依据：待填")
                self.assertIn("- 辖区：UNSPECIFIED", md)

    def test_reordered_tables_keep_shared_quantity_and_source_with_declared_object(self):
        cases = (("landscape", "苗木", "plants", "plant_name"), ("interior", "房间", "rooms", "room"),
                 ("intel-weak", "点位", "points", "point"), ("civil-defense", "防护设备", "equipment", "equipment"),
                 ("hydraulic", "勘探孔", "geology", "borehole"))
        for post, label, kind, anchor in cases:
            with self.subTest(post=post):
                data = parse(f"|数量|来源|{label}|\n|---|---|---|\n|9|资料A|甲|\n||资料B|乙|", post)
                self.assertEqual(2, len(data.records[kind]))
                a, b = data.records[kind]
                self.assertEqual(["甲", "乙"], [a[anchor], b[anchor]])
                self.assertEqual("9", a["quantity"])
                self.assertEqual(UNKNOWN, b["quantity"])
                self.assertEqual(["资料A", "资料B"], [a["source"], b["source"]])
                self.assertTrue(all(r.get("_parent") is None for r in data.records[kind]))

    def test_explicitly_different_owner_detaches_child_from_last_parent(self):
        for post, text, kind, ref in (
            ("landscape", "分区：东园\n苗木：桂花；所属分区：西园", "plants", "object_ref"),
            ("interior", "房间：东厅\n门窗：西门；所属房间：西厅", "doors", "object_ref"),
            ("intel-weak", "系统：监控\n桥架：西路；服务系统：门禁", "routes", "system_ref"),
            ("civil-defense", "防护单元：F1\n口部：西口；所属单元：F2", "mouths", "unit_ref"),
            ("hydraulic", "水闸：甲闸\n水文断面：H2；关联对象：乙闸", "hydrology", "object_ref"),
        ):
            with self.subTest(post=post):
                r = parse(text, post).records[kind][0]
                self.assertNotIn("_parent", r)
                self.assertNotEqual(UNKNOWN, r[ref])

    def test_explicitly_unknown_owner_also_cancels_inferred_relation(self):
        r = parse("防护单元：F1\n口部：西口；所属单元：待填", "civil-defense").records["mouths"][0]
        self.assertEqual(UNKNOWN, r["unit_ref"])
        self.assertNotIn("_parent", r)

    def test_detached_hydrology_cannot_supply_previous_structures_water_level(self):
        md = draft("hydraulic", "水闸：甲闸\n水文断面：H2；关联对象：乙闸；水位：8.62m；流量：127.6m3/s；水文报告：报告乙")
        r = values(md, "水工参数来源与待计算项")[0]
        self.assertEqual(UNKNOWN, r["用户水位"])
        self.assertEqual(UNKNOWN, r["用户流量"])
        self.assertNotIn("报告乙", r["来源"])
        self.assertEqual("8.62m", values(md, "3 水文成果与水位流量登记")[0]["用户设计水位"])

    def test_unattached_hydrology_never_defaults_to_only_structure(self):
        md = draft("hydraulic", "水闸：甲闸\n【用户上传：水文.md】\n水文断面：H1；水位：7.8m")
        self.assertEqual(UNKNOWN, values(md, "水工参数来源与待计算项")[0]["用户水位"])

    def test_two_hydraulic_structures_keep_their_own_water_geology_and_pending_calculations(self):
        md = draft("hydraulic", "水闸：甲闸\n水文断面：H1；水位：8.6m；流量：127.5m3/s；水文报告：水文甲\n勘探孔：Z1；地层：黏土\n水闸：乙闸\n水文断面：H2；水位：7.9m；水文报告：水文乙\n勘探孔：Z2；地层：淤泥")
        a, b = values(md, "水工参数来源与待计算项")
        self.assertEqual(["8.6m", "7.9m"], [a["用户水位"], b["用户水位"]])
        self.assertEqual(["127.5m3/s", UNKNOWN], [a["用户流量"], b["用户流量"]])
        self.assertIn("Z1", a["地质资料"])
        self.assertNotIn("Z1", b["地质资料"])
        self.assertEqual(["水文甲", "水文乙"], [a["来源"], b["来源"]])
        self.assertTrue(all("均未连接" in r["待计算项"] and r["签认"] == "" for r in (a, b)))

    def test_unconfirmed_grade_remains_user_requirement_not_approved_result(self):
        md = draft("civil-defense", "防护单元：甲；人防等级：甲方口述核6级\n防护单元：乙；平时功能：停车")
        a, b = values(md, "防护单元专业接口核对")
        self.assertEqual(UNKNOWN, a["批准资料来源"])
        self.assertEqual(UNKNOWN, b["用户等级要求（未核）"])
        self.assertTrue(all(r["签认"] == "" for r in (a, b)))
        self.assertIn("审查结论：UNSPECIFIED", md)

    def test_unknown_numeric_update_does_not_keep_previous_number_as_current_input(self):
        md = draft("interior", "房间：甲；净高：3.1米\n净高：还没定，不按原值")
        r = values(md, "4 建筑、结构、机电与外窗界面")[0]
        self.assertEqual(UNKNOWN, r["用户净高"])
        self.assertIn("原用户值：3.1米", md)

    def test_natural_unknown_numeric_update_marks_the_current_field_missing(self):
        md = draft("interior", "房间：甲；净高：3.1米\n净高还没定")
        self.assertEqual(UNKNOWN, values(md, "4 建筑、结构、机电与外窗界面")[0]["用户净高"])

    def test_examples_and_conditional_inputs_do_not_become_actual_records(self):
        for post, label, parameter in (("landscape", "苗木", "冠幅"), ("interior", "房间", "净高"),
                                      ("intel-weak", "系统", "点数"), ("civil-defense", "防护单元", "出入口数量"),
                                      ("hydraulic", "水闸", "孔数")):
            with self.subTest(post=post):
                md = draft(post, f"比如{label}：样例对象；{parameter}：999\n{label}：仍在举例；{parameter}：888\n实际情况：{label}：真实对象；{parameter}：7")
                self.assertNotIn("999", md)
                self.assertNotIn("888", md)
                self.assertNotIn("样例对象", md)
                self.assertIn("真实对象", md)

    def test_empty_values_are_listed_as_missing_not_already_registered(self):
        md = draft("interior", "房间：甲；地面：待填；墙面：未给；天花：TBD")
        gap = values(md, "缺项、复核与交付目录")[0]
        self.assertIn("地面做法", gap["缺项 / 核对内容"])
        self.assertIn("墙面做法", gap["缺项 / 核对内容"])
        self.assertNotIn("已登记用户值", gap["缺项 / 核对内容"])

    def test_private_credentials_and_label_shaped_secret_tails_do_not_enter_weak_draft(self):
        secrets = ("password=ALPHA ;品牌：SECRET-TAIL", "ｔｏｋｅｎ＝SECRET-A", "api_key: sk-syntheticABC12345",
                   "Authorization: Bearer syntheticSECRET", "口令：SECRET-B", "用户名：PRIVATE-PERSON")
        for secret in secrets:
            with self.subTest(secret=secret):
                md = draft("intel-weak", "系统：门禁\n"+secret+"\n点数：18\n品牌：安全品牌")
                self.assertNotRegex(md, r"ALPHA|SECRET-|syntheticSECRET|PRIVATE-PERSON|sk-synthetic")
                self.assertEqual("18", values(md, "2 子系统与用户功能需求")[0]["用户点数"])
                self.assertEqual("安全品牌", values(md, "2 子系统与用户功能需求")[0]["用户品牌"])

    def test_multiline_private_key_and_quoted_credential_cannot_create_brand_fields(self):
        for secret in ('password="FIRST\n品牌：SECRET-TAIL\nEND"',
                       "-----BEGIN " "PRIVATE KEY-----\n品牌：SECRET-KEY\n-----END PRIVATE KEY-----"):
            with self.subTest(secret=secret):
                md = draft("intel-weak", "系统：门禁\n"+secret+"\n点数：18")
                self.assertNotIn("SECRET-", md)
                self.assertEqual(UNKNOWN, values(md, "2 子系统与用户功能需求")[0]["用户品牌"])

    def test_true_addresses_in_values_and_source_names_are_redacted(self):
        addresses = ("https://private.example.invalid/api", "10.24.35.46", "[2001:db8::8]", "tester@example.invalid")
        for address in addresses:
            with self.subTest(address=address):
                md = draft("intel-weak", f"【用户上传：{address}.md】\n系统：门禁；网络安全：隔离{address}\n品牌：安全品牌")
                self.assertNotIn(address, md)
                self.assertEqual("安全品牌", values(md, "2 子系统与用户功能需求")[0]["用户品牌"])

    def test_safe_uploaded_entities_decode_once_and_keep_literal_pipe(self):
        md = draft("intel-weak", "|系统|品牌|网络安全|\n|---|---|---|\n|=2+3|literal &amp;amp;|x&lt;10 &amp;&amp; A&#124;B|")
        r = values(md, "2 子系统与用户功能需求")[0]
        self.assertEqual("=2+3", r["子系统"])
        self.assertEqual("literal &amp;", r["用户品牌"])
        self.assertEqual("x<10 && A|B", values(md, "6 消防联动、网络与公共安全接口")[0]["用户网络安全要求"])
        self.assertNotIn("x<10", md)

    def test_table_project_metadata_is_owned_even_when_project_is_last_column(self):
        data = parse("|房间|地面|项目|设计阶段|\n|---|---|---|---|\n|大厅|石材A|项目甲|初步设计|\n|办公室|地毯B|项目乙|施工图设计|", "interior")
        a, b = data.records["rooms"]
        self.assertEqual(["项目甲", "项目乙"], [data.contexts[r["_context"]]["project"] for r in (a, b)])
        self.assertEqual(["初步设计", "施工图设计"], [data.contexts[r["_context"]]["stage"] for r in (a, b)])
        self.assertEqual(["石材A", "地毯B"], [a["floor"], b["floor"]])

    def test_object_basis_table_does_not_promote_rows_to_global_project_basis(self):
        md = draft("civil-defense", "项目：甲\n|辖区|依据|防护单元|\n|---|---|---|\n|CN|CN-甲|单元甲|\n|SG|SG-乙|单元乙|")
        a, b = values(md, "各项目与文件依据归属")
        self.assertEqual(["单元甲", "单元乙"], [a["专业对象"], b["专业对象"]])
        self.assertEqual(["CN-甲", UNKNOWN], [a["CN 用户资料"], b["CN 用户资料"]])
        self.assertEqual([UNKNOWN, "SG-乙"], [a["SG 用户资料"], b["SG 用户资料"]])
        self.assertIn("| 用户指定文件 | UNSPECIFIED | UNSPECIFIED | UNSPECIFIED | unverified |", md)

    def test_explicit_water_and_borehole_ids_link_only_unique_same_file_scope_rows(self):
        md = draft("hydraulic", "水闸：甲闸；关联水文断面：H1；关联勘探孔：Z1\n|水文断面|水位|水文报告|\n|---|---|---|\n|H1|8.63m|水文甲|\n|H2|7.94m|水文乙|\n\n|勘探孔|地勘报告|\n|---|---|\n|Z1|地勘甲|\n|Z2|地勘乙|")
        r = values(md, "水工参数来源与待计算项")[0]
        self.assertEqual("8.63m", r["用户水位"])
        self.assertEqual("水文甲", r["来源"])
        self.assertEqual("Z1 / 地勘甲", r["地质资料"])

    def test_duplicate_or_cross_file_water_id_never_supplies_linked_parameter(self):
        for body in ("|水文断面|水位|\n|---|---|\n|H1|8.63m|\n|H1|7.94m|",
                     "【用户上传：独立水文.md】\n水文断面：H1；水位：8.63m"):
            with self.subTest(body=body):
                md = draft("hydraulic", "水闸：甲闸；关联水文断面：H1\n"+body)
                self.assertEqual(UNKNOWN, values(md, "水工参数来源与待计算项")[0]["用户水位"])

    def test_source_marker_scenarios_reset_but_shared_sources_do_not_cross_files(self):
        md = draft("landscape", "【用户上传：甲.md】\n分区：甲园；苗木：桂花；数量：8；苗木来源：苗表甲\n【用户上传：乙.md】\n分区：乙园；苗木：银杏；数量：9\n苗木表是苗表乙")
        a, b = values(md, "苗木与分区来源核对")
        self.assertEqual(["苗表甲", "苗表乙"], [a["来源"], b["来源"]])
        self.assertEqual(["8", "9"], [a["数量"], b["数量"]])

    def test_reference_before_colon_field_is_not_lost(self):
        md = draft("intel-weak", "系统：视频监控；点数：20\n系统：门禁；点数：30\n监控走的桥架规格：200×100\n门禁那边桥架规格：150×75")
        r = values(md, "3 机房、弱电井与桥架路由")
        self.assertEqual(["视频监控", "门禁"], [v["关联子系统"] for v in r])
        self.assertEqual(["200×100", "150×75"], [v["用户桥架规格"] for v in r])

    def test_obfuscated_multiline_secret_does_not_leave_later_field_shaped_lines(self):
        for secret in ('ｐａｓｓｗｏｒｄ＝"TOP\n品牌：SECRET-TAIL\nEND"',
                       "-----ＢＥＧＩＮ PRIVATE KEY-----\n品牌：SECRET-KEY\n-----END PRIVATE KEY-----"):
            with self.subTest(secret=secret):
                md = draft("intel-weak", "系统：门禁\n"+secret+"\n点数：20")
                self.assertNotIn("SECRET-", md)
                self.assertEqual("20", values(md, "2 子系统与用户功能需求")[0]["用户点数"])

    def test_negative_numeric_cell_is_unknown_and_not_a_calculated_default(self):
        md = draft("hydraulic", "|水文断面|水位|流量|\n|---|---|---|\n|H1|未给，别按8.8m|例如128m3/s|")
        r = values(md, "3 水文成果与水位流量登记")[0]
        self.assertEqual(UNKNOWN, r["用户设计水位"])
        self.assertEqual(UNKNOWN, r["用户流量"])
        self.assertIn("submit_blocked=true", md)

    def test_missing_field_instruction_after_attachment_does_not_create_phantom_object(self):
        for post, label, kind in (("interior", "房间", "rooms"), ("intel-weak", "系统", "systems"), ("hydraulic", "水闸", "structures")):
            with self.subTest(post=post):
                data = parse(f"【用户上传：甲.csv】\n|{label}|\n|---|\n|甲|\n|乙|\n【本轮用户要求】\n按附件生成内部草稿，缺失字段保持待填。", post)
                self.assertEqual(2, len(data.records[kind]))

    def test_family_specific_sources_restore_their_own_record_after_switching_family(self):
        data = parse("分区：屋顶花园；苗木：桂花；数量：8；覆土：1.3米；苗木来源：苗表甲", "landscape")
        self.assertEqual("苗表甲", data.records["plants"][0]["plant_source"])
        self.assertNotIn("plant_source", data.records["zones"][0])
        data = parse("防护单元：F1；口部：东口；等级批准资料：用户批准包甲", "civil-defense")
        self.assertEqual("用户批准包甲", data.records["units"][0]["grade_source"])
        self.assertNotIn("grade_source", data.records["mouths"][0])

    def test_water_parameter_source_and_geology_source_stay_in_their_own_family(self):
        md = draft("hydraulic", "水闸：甲闸；水文断面：H1；水位：8.3m；水文报告：报告甲；勘探孔：Z1；地质资料来源：孔表甲；水位来源：水位表甲")
        data = parse("水闸：甲闸；水文断面：H1；水位：8.3m；勘探孔：Z1；地质资料来源：孔表甲；水位来源：水位表甲", "hydraulic")
        self.assertEqual("孔表甲", data.records["geology"][0]["geo_source"])
        self.assertNotIn("hydro_source", data.records["geology"][0])
        self.assertEqual("水位表甲", values(md, "水工参数来源与待计算项")[0]["来源"])

    def test_rejected_or_questioned_numbers_are_not_current_design_parameters(self):
        for value in ("不按8.8m", "不要按8.8m", "不是8.8m", "是否8.8m", "8.8m？"):
            with self.subTest(value=value):
                md = draft("hydraulic", "|水文断面|水位|\n|---|---|\n|H1|"+value+"|")
                self.assertEqual(UNKNOWN, values(md, "3 水文成果与水位流量登记")[0]["用户设计水位"])


if __name__ == "__main__":
    unittest.main()
