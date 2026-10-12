#!/usr/bin/env python3
"""Administration drafts: structured facts, approval boundaries and real Office output."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packing_assistant.expert_roster import exclusive_tools
from packing_assistant.office_job import tables_from_md
from packing_assistant.post_drafts.admin import build_draft


def draft(post: str, text: str) -> str:
    value = build_draft(post, exclusive_tools(post)[0], text)
    assert isinstance(value, str)
    return value


def rows_for(markdown: str, title: str) -> list[list[str]]:
    return next(rows for name, rows in tables_from_md(markdown) if name.startswith(title))


class AdminDraftTests(unittest.TestCase):
    def test_roster_tools_and_mismatches(self) -> None:
        for post in ("admin-doc", "admin-office"):
            for tool in exclusive_tools(post):
                with self.subTest(post=post, tool=tool):
                    self.assertIsInstance(build_draft(post, tool, "写一份草稿"), str)
        self.assertIsNone(build_draft("admin-doc", "admin-office__list", "写请示"))
        self.assertIsNone(build_draft("admin-office", "unknown", "写会务清单"))
        self.assertIsNone(build_draft("hr-train", "hr-train__plan", "写培训计划"))

    def test_request_populates_facts_and_preserves_document_number(self) -> None:
        md = draft("admin-doc", "写一份请示\n文种：请示；标题：关于补配会议设备的请示；"
                   "发文机关：项目综合部；文号：综〔2026〕8号；主送：公司办公室；"
                   "请示事项：补配投影设备；背景：现有投影无法开机；依据：用户提供的设备台账；"
                   "请求事项：请批准设备检修；附件：故障记录、报价材料；成文日期：2026-09-12")
        self.assertIn(["事实与背景", "现有投影无法开机"], rows_for(md, "请示正文"))
        self.assertIn(["请求事项", "请批准设备检修"], rows_for(md, "请示正文"))
        self.assertIn(["主送", "公司办公室"], rows_for(md, "文件控制"))
        self.assertIn(["发文字号（仅录入原值，不生成序号）", "综〔2026〕8号"], rows_for(md, "文件控制"))
        self.assertIn(["批示意见", ""], rows_for(md, "请示正文"))
        self.assertIn("妥否，请批示", md)
        self.assertNotIn("## 用户原文", md)

    def test_minutes_do_not_promote_discussion_or_proposals(self) -> None:
        md = draft("admin-doc", "写会议纪要；会议名称：材料协调会；讨论：是否调整供货计划；"
                   "决议：建议下周再讨论；待定事项：供货日期；责任人：张工；期限：下周")
        decisions = rows_for(md, "已议定事项")
        self.assertTrue(decisions[1][0].startswith("UNSPECIFIED"))
        self.assertTrue(decisions[1][1].startswith("UNSPECIFIED"))
        self.assertIn("建议下周再讨论", rows_for(md, "待定事项")[1][0])
        self.assertEqual("", rows_for(md, "待定事项")[1][-1])
        self.assertIn(["讨论摘要", "是否调整供货计划"], rows_for(md, "会议记录要素"))

    def test_minutes_transfer_only_explicit_decision_with_owner_and_deadline(self) -> None:
        md = draft("admin-doc", "文种：会议纪要；会议名称：办公室周会；时间：2026-09-12 09:00；"
                   "地点：302室；主持人：陈主任；与会人员：综合部、设备部；记录人：小刘；"
                   "决议：补交设备故障记录；责任人：张工；截止时间：2026-09-15")
        self.assertEqual(["补交设备故障记录", "张工", "2026-09-15", ""], rows_for(md, "已议定事项")[1])
        self.assertIn(["与会人员", "综合部、设备部"], rows_for(md, "会议记录要素"))
        self.assertIn(["记录人", "小刘"], rows_for(md, "会议记录要素"))

    def test_labeled_proposal_is_still_pending(self) -> None:
        for proposal in ("拟调整会议安排", "预算待批准", "讨论更换会议场地"):
            with self.subTest(proposal=proposal):
                md = draft("admin-doc", f"写会议纪要；决议：{proposal}")
                self.assertTrue(rows_for(md, "已议定事项")[1][0].startswith("UNSPECIFIED"))
                self.assertEqual(proposal, rows_for(md, "待定事项")[1][0])

    def test_seal_form_does_not_sign_or_execute(self) -> None:
        md = draft("admin-doc", "写用印申请；文件名称：场地租赁说明；份数：2份；印章种类：公司公章；"
                   "审批人：王主任；申请人：陈工；用印事由：提交场地使用材料；"
                   "送达对象：场地方；是否骑缝：待核；附件：无")
        rows = rows_for(md, "用印申请与核验")
        for row in (["文件名称", "场地租赁说明"], ["份数", "2份"], ["印章种类", "公司公章"],
                    ["拟审批人（用户提供，权限待核）", "王主任"], ["审批人签认", ""],
                    ["实际用印日期及登记编号", ""]):
            self.assertIn(row, rows)
        self.assertIn("禁止代用印、预盖空白文件", md)
        self.assertIn(["附件目录（按用户顺序）", "无"], rows_for(md, "附件与归档交接"))

    def test_missing_or_ambiguous_document_kind_does_not_invent_a_request(self) -> None:
        for text in ("写一份公文", "写请示报告", "文种：通知；主题：设备盘点"):
            with self.subTest(text=text):
                md = draft("admin-doc", text)
                self.assertIn("文种待确认", md)
                self.assertNotIn("## 请示正文", md)
                self.assertIn("UNSPECIFIED", rows_for(md, "附件与归档交接")[1][1])

    def test_office_four_tables_populate_inputs_and_keep_decisions_blank(self) -> None:
        md = draft("admin-office", "写会务清单；会议名称：周协调会；目的：梳理资料缺项；"
                   "时间：周五 14:00；场地：项目部二楼；议程：资料盘点、责任分工；"
                   "主持人：李工；与会人员：工程部、综合部；资料目录：上周纪要、图纸目录；"
                   "设备：投影仪；决议：同意所有安排")
        self.assertEqual("项目部二楼", rows_for(md, "场地")[1][1])
        self.assertEqual("资料盘点、责任分工", rows_for(md, "议程")[1][1])
        self.assertEqual("工程部、综合部", rows_for(md, "与会")[1][1])
        self.assertEqual("上周纪要、图纸目录", rows_for(md, "资料目录")[1][0])
        for title in ("场地", "议程", "与会", "资料目录"):
            rows = rows_for(md, title)
            decision = rows[0].index("决定")
            self.assertTrue(all(row[decision] == "" for row in rows[1:]), title)
        self.assertNotIn("同意所有安排", md)

    def test_common_narrative_meeting_facts_are_extracted(self) -> None:
        md = draft("admin-office", "请写会务清单，计划于9月15日14:00在项目部二楼召开周例会，"
                   "由李工主持，由小张记录，参会人员为工程部和综合部。")
        self.assertIn(["时间", "9月15日14:00"], rows_for(md, "任务信息"))
        self.assertIn(["会议/活动名称", "周例会"], rows_for(md, "任务信息"))
        self.assertEqual("项目部二楼", rows_for(md, "场地")[1][1])
        self.assertEqual("李工", rows_for(md, "议程")[1][2])
        self.assertEqual("小张", rows_for(md, "与会")[3][1])

    def test_requested_reception_travel_and_supplies_have_specific_fields(self) -> None:
        md = draft("admin-office", "编制后勤清单；接待对象：合作单位代表；人数：3人；住宿：用户待确认；"
                   "出差人员：李工；路线：上海至苏州；交通方式：铁路；"
                   "申请部门：综合部；办公物资：文件盒；规格：A4；数量：10个；库存：2个；领用人：小张；用途：资料归档")
        self.assertIn(["人数", "3人"], rows_for(md, "接待安排"))
        self.assertIn(["路线", "上海至苏州"], rows_for(md, "差旅及用车"))
        self.assertEqual(["综合部", "文件盒", "A4", "10个", "2个", "小张", "资料归档", ""], rows_for(md, "办公物资申领")[1])
        self.assertIn(["费用标准及审批", ""], rows_for(md, "接待安排"))

    def test_jurisdiction_is_shared_and_missing_values_stay_unknown(self) -> None:
        for post in ("admin-doc", "admin-office"):
            for prefix, expected in (("", "UNSPECIFIED"), ("CN ", "CN"), ("新加坡 ", "SG"), ("EU CN ", "DUAL")):
                with self.subTest(post=post, prefix=prefix):
                    md = draft(post, prefix + "写一份请示" if post == "admin-doc" else prefix + "写会务清单")
                    self.assertIn(f"- 辖区：{expected}\n", md)
                    self.assertIn("草稿声明", md)
                    self.assertIn("不代用印", md)
        self.assertIn(["签发人签认", ""], rows_for(draft("admin-doc", "写请示"), "文件控制"))

    def test_field_boundaries_and_markdown_table_cells_are_safe(self) -> None:
        md = draft("admin-office", "场地：A|B<script>；议程：资料盘点, 确认记录模板\n"
                   "额外说明不属于议程字段\n主持人：李工")
        self.assertEqual("A|B<script>", rows_for(md, "场地")[1][1])
        self.assertEqual(4, len(rows_for(md, "场地")[1]))
        self.assertEqual("资料盘点, 确认记录模板", rows_for(md, "议程")[1][1])
        self.assertNotIn("<script>", md)

    def test_key_value_markdown_table_populates_office(self) -> None:
        md = draft("admin-office", "| 栏位 | 值 |\n| --- | --- |\n| 场地 | 二楼会议室 |\n| 议程 | 资料盘点 |\n| 附件 | 图纸目录 |")
        self.assertEqual("二楼会议室", rows_for(md, "场地")[1][1])
        self.assertEqual("资料盘点", rows_for(md, "议程")[1][1])
        self.assertEqual("图纸目录", rows_for(md, "资料目录")[1][0])

    def test_column_table_keeps_meeting_rows_and_missing_values_separate(self) -> None:
        md = draft("admin-office", "| 时间 | 场地 | 议程 | 主持人 | 资料目录 |\n| --- | --- | --- | --- | --- |\n"
                   "| 上午 | 一楼 | 盘点资料 | 李工 | 图纸目录 |\n| 下午 | 二楼 | 核对设备 | | 设备台账 |")
        agenda = rows_for(md, "议程")
        self.assertEqual(["上午", "盘点资料", "李工", ""], agenda[1])
        self.assertEqual("下午", agenda[2][0])
        self.assertTrue(agenda[2][2].startswith("UNSPECIFIED"))
        self.assertEqual(["一楼", "二楼"], [row[1] for row in rows_for(md, "场地")[1:3]])
        self.assertEqual(["图纸目录", "设备台账"], [row[0] for row in rows_for(md, "资料目录")[1:3]])

    def test_separate_decisions_never_share_another_records_deadline(self) -> None:
        for text in (
            "写会议纪要\n决议：整理目录；责任人：甲；期限：周五\n责任人：乙；决议：核对台账",
            "写会议纪要\n决议：整理目录\n责任人：甲\n期限：周五\n决议：核对台账\n责任人：乙",
        ):
            with self.subTest(text=text):
                rows = rows_for(draft("admin-doc", text), "已议定事项")
                self.assertEqual(["整理目录", "甲", "周五", ""], rows[1])
                self.assertEqual("核对台账", rows[2][0])
                self.assertEqual("乙", rows[2][1])
                self.assertTrue(rows[2][2].startswith("UNSPECIFIED"))

    def test_decision_column_table_keeps_each_owner_and_deadline(self) -> None:
        md = draft("admin-doc", "写会议纪要\n| 决议 | 责任人 | 期限 |\n| --- | --- | --- |\n"
                   "| 整理目录 | 甲 | 周五 |\n| 核对台账 | 乙 | |\n| 拟调整预算 | 丙 | 周一 |")
        rows = rows_for(md, "已议定事项")
        self.assertEqual(3, len(rows))
        self.assertEqual(["整理目录", "甲", "周五", ""], rows[1])
        self.assertEqual("乙", rows[2][1])
        self.assertTrue(rows[2][2].startswith("UNSPECIFIED"))
        self.assertIn("拟调整预算", rows_for(md, "待定事项")[1][0])

    def test_seal_table_preserves_file_to_copies_association(self) -> None:
        md = draft("admin-doc", "写用印申请\n| 文件名称 | 份数 | 印章种类 |\n| --- | --- | --- |\n"
                   "| 交接单甲 | 2份 | 资料章 |\n| 交接单乙 | | |")
        rows = rows_for(md, "逐份用印文件核验")
        self.assertEqual(["交接单甲", "2份", "资料章"], rows[1][:3])
        self.assertEqual("交接单乙", rows[2][0])
        self.assertTrue(rows[2][1].startswith("UNSPECIFIED"))
        self.assertTrue(rows[2][2].startswith("UNSPECIFIED"))
        self.assertIn(["份数", "见逐份用印文件核验（不合并份数或权限）"], rows_for(md, "用印申请与核验"))

    def test_reported_minutes_extract_literal_roles_decision_and_pending_work(self) -> None:
        md = draft("admin-doc", "整理会议纪要，10月8日下午在项目部西楼会议室开的材料协调会，"
                   "项目经理吴海平主持，记录人是资料员林宁，监理代表张健列席，"
                   "会上定了由材料负责人钱文在10月15日前完成二层材料清点，"
                   "东门卸货时间没定下来，留到下次例会再议。")
        info = dict(rows_for(md, "会议记录要素")[1:])
        self.assertEqual("材料协调会", info["会议名称"])
        self.assertEqual("10月8日下午", info["时间"])
        self.assertEqual("项目部西楼会议室", info["地点"])
        self.assertEqual("项目经理吴海平", info["主持人"])
        self.assertEqual("资料员林宁", info["记录人"])
        self.assertEqual("监理代表张健", info["列席人员"])
        decided = rows_for(md, "已议定事项")[1]
        self.assertEqual(["二层材料清点", "材料负责人钱文", "10月15日前", ""], decided)
        self.assertIn("东门卸货时间没定下来", rows_for(md, "待定事项")[1][0])

    def test_narrative_proposals_and_example_decisions_do_not_become_resolutions(self) -> None:
        for text in ("会上建议由李工在10月15日前完成目录核对", "会上讨论由李工完成目录核对",
                     "比如写成会上定了由李工在10月15日前完成目录核对",
                     "如果会上决定由李工完成目录核对，请先别当成已定事项"):
            with self.subTest(text=text):
                md = draft("admin-doc", "写会议纪要，" + text)
                self.assertTrue(rows_for(md, "已议定事项")[1][0].startswith("UNSPECIFIED"))

    def test_narrative_decision_owners_and_dates_stay_with_their_own_item(self) -> None:
        md = draft("admin-doc", "写会议纪要，上会决定由张工在10月16日前完成图纸目录，"
                   "同时决定由李工完成设备台账，设备费用还没定。")
        decided = rows_for(md, "已议定事项")
        self.assertEqual(["图纸目录", "张工", "10月16日前", ""], decided[1])
        self.assertEqual("设备台账", decided[2][0])
        self.assertEqual("李工", decided[2][1])
        self.assertTrue(decided[2][2].startswith("UNSPECIFIED"))

    def test_space_delimited_approver_is_a_named_pending_person_not_approval(self) -> None:
        md = draft("admin-doc", "起草请示\n事由：补配资料柜\n审批人 刘文平")
        self.assertIn(["拟审批人（用户提供，权限待核）", "刘文平"], rows_for(md, "用印申请与核验"))
        self.assertIn(["审批人签认", ""], rows_for(md, "用印申请与核验"))
        for unknown in ("还没定", "待确认", "未知", "写谁还不清楚"):
            md = draft("admin-doc", "写用印申请\n审批人 " + unknown)
            person = dict(rows_for(md, "用印申请与核验")[1:])["拟审批人（用户提供，权限待核）"]
            self.assertTrue(person.startswith("UNSPECIFIED"), person)

    def test_narrative_seal_files_do_not_share_copies_seal_or_approver(self) -> None:
        md = draft("admin-doc", "写用印审批表，一个是钢筋班组劳动合同，27份，盖合同专用章，"
                   "审批人写商务经理林安；另一个是施工安全管理协议，11份，盖项目部印章，"
                   "这个谁批还没定先空着。经办都是我，劳资员周宁，下周二前要用。")
        rows = rows_for(md, "逐份用印文件核验")
        self.assertEqual(["钢筋班组劳动合同", "27份", "合同专用章", "商务经理林安", ""], rows[1])
        self.assertEqual(["施工安全管理协议", "11份", "项目部印章"], rows[2][:3])
        self.assertTrue(rows[2][3].startswith("UNSPECIFIED"))
        self.assertEqual("", rows[2][-1])
        self.assertIn(["申请人", "劳资员周宁"], rows_for(md, "用印申请与核验"))

    def test_narrative_second_seal_file_cannot_inherit_missing_quantity_or_stamp(self) -> None:
        md = draft("admin-doc", "写用印申请，一个是资料交接单，6份，盖资料章，审批人是王主任；"
                   "另一个是场地说明，份数和印章还没确定。")
        rows = rows_for(md, "逐份用印文件核验")
        self.assertEqual(["资料交接单", "6份", "资料章", "王主任", ""], rows[1])
        self.assertEqual("场地说明", rows[2][0])
        self.assertTrue(all(value.startswith("UNSPECIFIED") for value in rows[2][1:4]))

    def test_narrative_meeting_roles_and_equipment_demand_reach_separate_fields(self) -> None:
        md = draft("admin-office", "出会务清单，定于2026-10-20下午14:10在西楼大会议室召开8#楼图纸会审会，"
                   "由总工赵建平主持，资料员何宁记录，参会的有设计院和设备部大概23人，"
                   "会场要备好投影仪和三支无线话筒，茶水席卡也一起准备。")
        self.assertIn(["会议/活动名称", "8#楼图纸会审会"], rows_for(md, "任务信息"))
        self.assertEqual("资料员何宁", rows_for(md, "与会")[3][1])
        equipment = next(row for row in rows_for(md, "场地") if row[0].startswith("设备/"))
        self.assertEqual("投影仪和三支无线话筒", equipment[1])
        self.assertEqual("", equipment[-1])
        self.assertIn("茶水席卡", next(row for row in rows_for(md, "场地") if row[0].startswith("签到"))[1])

    def test_natural_reception_metadata_is_not_lost_or_treated_as_approval(self) -> None:
        md = draft("admin-office", "列接待清单\n来访单位：市住建局观摩团\n事由：工地观摩交流\n"
                   "带队 许宁副局长，一行21人\n到达：2026-10-22 08:50\n离开：当天15:30\n"
                   "场所 项目部展厅和样板层\n陪同：项目经理刘文\n用餐：中午工作餐，不住宿")
        info = dict(rows_for(md, "接待安排")[1:])
        self.assertEqual("工地观摩交流", info["接待事由"])
        self.assertEqual("许宁副局长", info["带队人"])
        self.assertEqual("21人", info["人数"])
        self.assertEqual("2026-10-22 08:50", info["到达时间"])
        self.assertEqual("当天15:30", info["离开时间"])
        self.assertEqual("项目部展厅和样板层", info["接待地点"])
        self.assertEqual("", info["费用标准及审批"])

    def test_narrative_vehicle_requests_keep_second_driver_plate_and_time_unknown(self) -> None:
        md = draft("admin-office", "周五有两拨用车列后勤清单，第一拨商务车苏E8J613，司机赵文，"
                   "早上8:10从项目部送甲方徐总他们3个人去苏州高铁站；"
                   "第二拨下午去诚达钢构厂看构件，安全部王宁带队6个人，"
                   "来回大概140公里，司机还没定车也没批，几点走等通知。")
        rows = rows_for(md, "逐项用车需求")
        first, second = dict(zip(rows[0], rows[1])), dict(zip(rows[0], rows[2]))
        self.assertEqual("苏E8J613", first["拟用车/车牌（待核）"])
        self.assertEqual("赵文", first["拟司机（待核）"])
        self.assertEqual("早上8:10", first["出发时间"])
        self.assertEqual("项目部", first["出发地点"])
        self.assertEqual("苏州高铁站", first["目的地"])
        self.assertEqual("甲方徐总", first["乘车人员"])
        self.assertEqual("3个人", first["人数"])
        self.assertEqual("诚达钢构厂", second["目的地"])
        self.assertEqual("安全部王宁", second["带队人"])
        self.assertEqual("6个人", second["人数"])
        self.assertEqual("大概140公里", second["用户提供里程（未计算）"])
        for key in ("拟用车/车牌（待核）", "拟司机（待核）", "出发时间"):
            self.assertTrue(second[key].startswith("UNSPECIFIED"), second)
        self.assertEqual("", first["派车审批"])
        self.assertEqual("", second["派车审批"])

    def test_example_and_negated_driver_or_equipment_do_not_become_actual_fields(self) -> None:
        md = draft("admin-office", "写用车清单，第一拨去北站，司机不是王工，车牌还没定；"
                   "第二拨去南站，比如车牌写苏E12345，司机写李工只是示例。")
        if "逐项用车需求" in md:
            for row in rows_for(md, "逐项用车需求")[1:]:
                self.assertTrue(row[2].startswith("UNSPECIFIED"))
                self.assertTrue(row[3].startswith("UNSPECIFIED"))
        md = draft("admin-office", "写会务清单，计划于周二在资料室召开周例会，"
                   "会场不用投影仪，设备需求还没定。")
        equipment = next(row for row in rows_for(md, "场地") if row[0].startswith("设备/"))
        self.assertTrue(equipment[1].startswith("UNSPECIFIED"))

    def test_two_natural_meetings_keep_missing_chair_and_equipment_separate(self) -> None:
        md = draft("admin-office", "写会务清单，计划于周一9:00在东楼召开材料协调会，由李工主持，"
                   "会场要备好投影仪；计划于周二14:00在西楼召开设备协调会，主持人还没定。")
        tables = tables_from_md(md)
        tasks = [dict(rows[1:]) for name, rows in tables if name.startswith("任务信息")]
        self.assertEqual(["材料协调会", "设备协调会"], [item["会议/活动名称"] for item in tasks])
        agendas = [rows for name, rows in tables if name.startswith("议程")]
        self.assertEqual("李工", agendas[0][1][2])
        self.assertTrue(agendas[1][1][2].startswith("UNSPECIFIED"))
        venues = [rows for name, rows in tables if name.startswith("场地")]
        self.assertEqual("投影仪", venues[0][2][1])
        self.assertTrue(venues[1][2][1].startswith("UNSPECIFIED"))

    def test_decimal_and_thousands_literals_are_never_truncated_to_integer_suffixes(self) -> None:
        md = draft("admin-doc", "写用印申请，一个是劳动合同，2.5份，盖合同专用章；"
                   "另一个是安全协议，1,200份，盖项目部印章。")
        rows = rows_for(md, "逐份用印文件核验")
        self.assertTrue(rows[1][1].startswith("UNSPECIFIED"))
        self.assertEqual("1,200份", rows[2][1])
        md = draft("admin-office", "写用车清单，第一拨去北站，来回大概140.5公里；"
                   "第二拨去南站，来回2,100公里。")
        rows = rows_for(md, "逐项用车需求")
        mileage = rows[0].index("用户提供里程（未计算）")
        self.assertEqual(["大概140.5公里", "2,100公里"], [row[mileage] for row in rows[1:]])
        md = draft("admin-office", "写会务清单，计划于周二在东楼召开周例会，参会的有项目部大概3.5人。")
        self.assertTrue(dict(rows_for(md, "任务信息")[1:])["参会人数（用户提供，待核）"].startswith("UNSPECIFIED"))

    def test_hypothetical_scope_survives_commas_and_separate_lines(self) -> None:
        md = draft("admin-doc", "写会议纪要，以下只是示例，会上决定由李工在10月16日前完成目录核对。")
        self.assertTrue(rows_for(md, "已议定事项")[1][0].startswith("UNSPECIFIED"))
        md = draft("admin-office", "写用车清单。比如，第一拨商务车苏E8J613，司机赵文，去北站；\n"
                   "第二拨去南站，来回100公里。")
        self.assertNotIn("逐项用车需求", md)

    def test_hypothetical_labels_are_not_imported_as_application_facts(self) -> None:
        md = draft("admin-doc", "写用印申请，以下只是示例\n文件名称：示意合同\n份数：20份\n审批人：王工")
        rows = dict(rows_for(md, "用印申请与核验")[1:])
        for key in ("文件名称", "份数", "拟审批人（用户提供，权限待核）"):
            self.assertTrue(rows[key].startswith("UNSPECIFIED"), rows)
        md = draft("admin-doc", "写用印申请，以下只是示例，份数：20份。"
                   "实际情况是：文件名称：资料交接单；份数：3份。")
        rows = dict(rows_for(md, "用印申请与核验")[1:])
        self.assertEqual("3份", rows["份数"])
        self.assertEqual("资料交接单", rows["文件名称"])

    def test_mixed_narrative_and_colon_fields_keep_local_seal_associations(self) -> None:
        md = draft("admin-doc", "写用印审批表，一个是劳动合同，份数：9份，印章：合同专用章，审批人：张工；"
                   "另一个是安全协议，份数：4份，印章：项目部印章，审批人：还没定。")
        rows = rows_for(md, "逐份用印文件核验")
        self.assertEqual(["劳动合同", "9份", "合同专用章", "张工", ""], rows[1])
        self.assertEqual(["安全协议", "4份", "项目部印章"], rows[2][:3])
        self.assertTrue(rows[2][3].startswith("UNSPECIFIED"))

    def test_vehicle_column_table_does_not_promote_unknown_people_or_inherit_them(self) -> None:
        md = draft("admin-office", "写用车需求\n| 车牌 | 司机 | 目的地 | 出发时间 |\n| --- | --- | --- | --- |\n"
                   "| 苏E8J613 | 赵文 | 北站 | 8:10 |\n| | | 南站 | |")
        rows = rows_for(md, "逐项用车需求")
        self.assertEqual("苏E8J613", rows[1][1])
        self.assertEqual("赵文", rows[1][2])
        self.assertTrue(rows[2][1].startswith("UNSPECIFIED"))
        self.assertTrue(rows[2][2].startswith("UNSPECIFIED"))
        self.assertTrue(rows[2][3].startswith("UNSPECIFIED"))

    def test_two_minutes_keep_resolutions_and_pending_work_under_their_own_meeting(self) -> None:
        md = draft("admin-doc", "写会议纪要，10月7日在东楼开的材料协调会，上会决定由张工在10月15日前完成材料目录；"
                   "10月8日在西楼开的设备协调会，上会决定由李工完成设备台账，设备费用还没定。")
        tables = tables_from_md(md)
        meetings = [dict(rows[1:])["会议名称"] for name, rows in tables if name.startswith("会议记录要素")]
        self.assertEqual(["材料协调会", "设备协调会"], meetings)
        decisions = [rows for name, rows in tables if name.startswith("已议定事项")]
        self.assertEqual(["材料目录", "张工", "10月15日前", ""], decisions[0][1])
        self.assertEqual(["设备台账", "李工"], decisions[1][1][:2])
        self.assertTrue(decisions[1][1][2].startswith("UNSPECIFIED"))
        pending = [rows for name, rows in tables if name.startswith("待定事项")]
        self.assertTrue(pending[0][1][0].startswith("UNSPECIFIED"))
        self.assertEqual("设备费用还没定", pending[1][1][0])

    def test_long_meeting_list_does_not_rescan_every_preceding_event(self) -> None:
        import time
        from packing_assistant.post_drafts.admin_narrative import parse_narrative
        started = time.perf_counter()
        parsed = parse_narrative("计划于周一9:00在东楼召开材料协调会，由李工主持；" * 1000)
        self.assertEqual(1000, len(parsed.meetings))
        self.assertTrue(all(record["chair"] == "李工" for record in parsed.meetings))
        self.assertLess(time.perf_counter() - started, 3.0)


class AdminRuntimeTests(unittest.TestCase):
    def test_real_runtime_markdown_and_excel_contain_structured_facts(self) -> None:
        from packing_assistant import expert_turn
        from packing_assistant.runtime import memory
        import openpyxl

        with tempfile.TemporaryDirectory(prefix="civil-admin-") as tmp:
            base = Path(tmp)
            with patch.dict(os.environ, {"CIVIL_JOB_ROOT": str(base / "job"), "CIVIL_SANDBOX_ROOTS": str(base), "PYTHON_DOTENV_DISABLED": "1"}), \
                 patch.object(expert_turn, "_OUT", base / "sessions"), patch.object(memory, "_OUT", base / "sessions"):
                cases = (
                    ("admin-doc", "写请示；请示事项：会议设备检修；背景：设备无法开机；请求事项：批准检修", "请示正文", "设备无法开机"),
                    ("admin-doc", "写会议纪要；会议名称：测试周会；决议：补交资料目录；责任人：李工；期限：周五", "已议定事项", "补交资料目录"),
                    ("admin-doc", "写用印申请；文件名称：测试资料交接单；份数：3份；印章种类：资料章", "用印申请与核验", "测试资料交接单"),
                    ("admin-office", "写会务清单；场地：测试会议室；议程：盘点资料；资料目录：图纸清单", "场地", "测试会议室"),
                    ("admin-office", "写会务清单；场地：面积 < 20 且分区 A|B", "场地", "面积 < 20 且分区 A|B"),
                )
                for index, (post, text, table, expected) in enumerate(cases):
                    with self.subTest(post=post):
                        result = expert_turn.run_expert_turn(text, post, force_intent="run", session_id=f"admin-draft-{index}")
                        self.assertTrue(result["ok"], result)
                        self.assertTrue(result["wrote"], result)
                        self.assertEqual("done", result["state"])
                        files = [Path(item["path"]) for item in result["files"]]
                        md = next(path for path in files if path.suffix == ".md")
                        self.assertTrue(md.is_relative_to(base))
                        self.assertIn(expected, str(rows_for(md.read_text(encoding="utf-8"), table)))
                        book = next(path for path in files if path.suffix == ".xlsx")
                        self.assertTrue(book.is_relative_to(base))
                        workbook = openpyxl.load_workbook(book)
                        try:
                            sheet = next(workbook[name] for name in workbook.sheetnames if name.startswith(table))
                            self.assertTrue(any(expected in row for row in sheet.iter_rows(values_only=True)))
                            if post == "admin-office":
                                self.assertTrue(all(row[3] in (None, "") for row in sheet.iter_rows(min_row=2, values_only=True)))
                        finally:
                            workbook.close()

    def test_question_does_not_create_draft(self) -> None:
        from packing_assistant.expert_turn import run_expert_turn

        result = run_expert_turn("用印清单需要哪些信息？", "admin-doc", force_intent="chat", session_id="admin-question-test")
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["wrote"])
        self.assertEqual([], result["files"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
