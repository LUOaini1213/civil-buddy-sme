#!/usr/bin/env python3
"""T040: inspect HR body tables and real exports, not echoed user prompts."""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packing_assistant import expert_turn
from packing_assistant.post_drafts.hr import DISCLAIMER, build_draft
from packing_assistant.runtime.scheduler import Scheduler
from packing_assistant.runtime.tool_engine import default_engine


def chapter(markdown: str, heading: str) -> str:
    return markdown.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]


class HRBuilderTests(unittest.TestCase):
    def test_multiple_workers_do_not_share_pay_or_terms(self):
        for text in (
            "劳动者：甲；工资：7000元；合同期限：一年\n劳动者：乙；岗位：测量员",
            "| 劳动者 | 工资 | 合同期限 |\n| --- | --- | --- |\n| 甲 | 7000元 | 一年 |\n| 乙 | | |",
        ):
            with self.subTest(text=text):
                markdown = self.labor(text)
                # Compare individual records, not the new explicitly keyed overview.
                first, second = markdown.split("# 材料记录 1", 1)[1].split("# 材料记录 2", 1)
                self.assertIn("| 劳动者姓名 | 甲 |", first)
                self.assertIn("| 劳动报酬 | 7000元 |", first)
                self.assertIn("| 劳动者姓名 | 乙 |", second)
                self.assertIn("| 劳动报酬 | [A001] 待填 |", second)
                self.assertNotIn("7000元", second)
                self.assertNotIn("一年", second)

    def test_contract_matrix_keeps_row_boundaries_with_missing_or_last_identity(self):
        for worker in ("乙", ""):
            with self.subTest(worker=worker):
                markdown = self.labor("| 工资 | 劳动者 |\n| --- | --- |\n| 7000元 | 甲 |\n| 9000元 | " + worker + " |")
                first, second = markdown.split("# 材料记录 1", 1)[1].split("# 材料记录 2", 1)
                self.assertIn("| 劳动报酬 | 7000元 |", first)
                self.assertNotIn("9000元", first)
                self.assertIn("| 劳动报酬 | 9000元 |", second)
                self.assertNotIn("7000元", second)
                self.assertIn("| 劳动者姓名 | " + (worker or "[A001] 待填") + " |", second)

    def test_compact_rows_keep_empty_cells_in_their_column(self):
        # A row's own pipes are its column borders; an adjacent pipe is an empty cell.
        head = "|劳动者|工资|合同期限|\n|---|---|---|\n|甲|7000元|一年|\n"
        spaced = "| 劳动者 | 工资 | 合同期限 |\n| --- | --- | --- |\n| 甲 | 7000元 | 一年 |\n|  | 9000元 | 两年 |"
        self.assertEqual(self.labor(head + "||9000元|两年|"), self.labor(spaced))
        for row, (worker, wage, term) in (
            ("||9000元|两年|", ("[A001] 待填", "9000元", "两年")),
            ("|乙|9000元||", ("乙", "9000元", "[A001] 待填")),
            ("|||", ("[A001] 待填", "[A001] 待填", "[A001] 待填")),
            ("|乙|", ("乙", "[A001] 待填", "[A001] 待填")),
            ("|", ("[A001] 待填", "[A001] 待填", "[A001] 待填")),
        ):
            with self.subTest(row=row):
                second = self.labor(head + row).split("# 材料记录 2", 1)[1]
                self.assertIn(f"| 劳动者姓名 | {worker} |", second)
                self.assertIn(f"| 劳动报酬 | {wage} |", second)
                self.assertIn(f"| 合同期限 | {term} |", second)

    def test_compact_label_rows_keep_the_empty_first_cell(self):
        # hr-train__plan returns before _labor_records, so the field table is the only splitter there.
        for draft, compact, spaced, value in (
            (self.train, "||工种|电工|", "| | 工种 | 电工 |", "电工"),
            (self.train, "||企业名称|某公司|", "| | 企业名称 | 某公司 |", "某公司"),
            (self.labor, "||工资|7000元|", "| | 工资 | 7000元 |", "7000元"),
        ):
            with self.subTest(compact=compact):
                markdown = draft(compact)
                self.assertEqual(markdown, draft(spaced))
                self.assertNotIn(value, markdown)

    def labor(self, text: str = "写一份劳动合同检查表") -> str:
        return build_draft("hr-labor", "hr-labor__check", text)

    def train(self, text: str = "写一份培训计划") -> str:
        return build_draft("hr-train", "hr-train__plan", text)

    def test_registry_selects_only_exact_post_tool_pairs(self):
        for post, tool in (("hr-recruit", "hr-recruit__brief"), ("hr-labor", "hr-train__plan"), ("hr-train", "hr-train__draft")):
            self.assertIsNone(build_draft(post, tool, "写一份草稿"))
        for markdown in (self.labor(), self.train()):
            self.assertIn(DISCLAIMER, markdown)
            self.assertIn("submit_blocked=true", markdown)
            self.assertNotIn("## 用户原文", markdown)

    def test_missing_labor_material_stays_missing_and_unknown(self):
        markdown = self.labor()
        self.assertIn("- 辖区：UNSPECIFIED", markdown)
        essential = chapter(markdown, "3 劳动合同必备条款对照")
        self.assertIn("| 用人单位名称 | [A001] 待填 |", essential)
        self.assertIn("| 劳动报酬 | [A001] 待填 |", essential)
        self.assertIn("| 社会保险 | [A001] 待填 |", essential)
        self.assertIn("| 经济补偿 | [A001] |", chapter(markdown, "9 变更、解除与补偿待核表"))
        self.assertIn("| UNSPECIFIED | 项目辖区与适用文件待指定 | UNSPECIFIED | UNSPECIFIED |", chapter(markdown, "12 依据与自检"))
        self.assertNotIn("## 10 SG 关键雇佣条款", markdown)
        self.assertNotRegex(markdown, r"第[零一二三四五六七八九十百\d]+条|\b\d+%|\b\d+\s*元")

    def test_labor_material_populates_essential_timing_and_evidence_tables(self):
        markdown = self.labor("写检查表；合同类型：劳动合同；项目名称：河岸改造；合同编号：HT-7；用人单位：甲建筑；单位住所：示例路；负责人：负责人甲；劳动者：工人甲；工作地点：东区工点；岗位：钢筋工；工作内容：钢筋绑扎；月薪：7800元；社会保险：缴费记录待补；签订日期：2026-08-01；入职日期：2026-08-03；合同期限：一年；考勤记录：附件考勤表；管理方式：项目排班；合同文件：劳动合同摘录.pdf")
        essential = chapter(markdown, "3 劳动合同必备条款对照")
        for label, value in (("用人单位名称", "甲建筑"), ("劳动者姓名", "工人甲"), ("岗位", "钢筋工"), ("工作地点", "东区工点"), ("劳动报酬", "7800元"), ("社会保险", "缴费记录待补")):
            self.assertIn(f"| {label} | {value} |", essential)
        register = chapter(markdown, "1 文件与材料登记")
        self.assertIn("| 项目名称 | 河岸改造 |", register)
        self.assertIn("| 合同文件 | 劳动合同摘录.pdf |", register)
        timing = chapter(markdown, "7 订立时点、期限与可约定事项")
        self.assertIn("| 签订日期 | 2026-08-01 |", timing)
        self.assertIn("| 用工或入职日期 | 2026-08-03 |", timing)
        facts = chapter(markdown, "2 关系识别事实与证据")
        self.assertIn("| 现场管理与工作安排 | 项目排班 |", facts)
        self.assertIn("| 考勤记录 | 附件考勤表 |", facts)

    def test_service_parties_and_remuneration_do_not_use_labor_fields(self):
        markdown = self.labor("合同类型：劳动合同与劳务协议；用人单位：劳动雇主；劳动者：劳动雇员；工资：7300元；劳务接受方：服务客户；劳务提供方：服务团队；服务内容：整理竣工图目录；成果要求：提交目录初稿；服务费：12000元；结算方式：用户约定两批结算；验收方式：目录清单核对")
        labor = chapter(markdown, "3 劳动合同必备条款对照")
        service = chapter(markdown, "4 劳务协议独立检查表")
        self.assertIn("| 劳动报酬 | 7300元 |", labor)
        self.assertNotIn("12000元", labor)
        for label, value in (("劳务接受方", "服务客户"), ("劳务提供方", "服务团队"), ("服务内容", "整理竣工图目录"), ("劳务报酬", "12000元")):
            self.assertIn(f"| {label} | {value} |", service)
        self.assertNotIn("劳动雇主", service)
        self.assertNotIn("7300元", service)
        no_service = chapter(self.labor("合同类型：劳动合同；用人单位：甲单位；工资：7400元"), "4 劳务协议独立检查表")
        self.assertIn("| 劳务报酬 | [A001] 待填 |", no_service)
        self.assertIn("| 劳务接受方 | [A001] 待填 |", no_service)

    def test_ambiguous_remuneration_is_preserved_without_assigning_a_type(self):
        markdown = self.labor("合同类型：承揽材料待定；报酬：9000元")
        register = chapter(markdown, "1 文件与材料登记")
        self.assertIn("| 用户标示的文本类型 | 承揽材料待定 |", register)
        self.assertIn("| 用户报酬记录（类型待核） | 9000元 |", register)
        self.assertNotIn("9000元", chapter(markdown, "3 劳动合同必备条款对照"))
        self.assertNotIn("9000元", chapter(markdown, "4 劳务协议独立检查表"))
        service = self.labor("合同类型：劳务协议；报酬：9000元")
        self.assertIn("| 劳务报酬 | 9000元 |", chapter(service, "4 劳务协议独立检查表"))

    def test_dispatch_and_part_time_keep_their_own_check_tables(self):
        markdown = self.labor("合同类型：劳务派遣；派遣单位：派遣企业；用工单位：工地企业；劳动者：测试人员；派遣岗位：仓管；派遣协议：协议附件；派遣许可证：许可证复印件")
        dispatch = chapter(markdown, "5 劳务派遣三方材料检查表")
        for label, value in (("派遣单位", "派遣企业"), ("用工单位", "工地企业"), ("劳动者", "测试人员"), ("派遣岗位", "仓管")):
            self.assertIn(f"| {label} | {value} |", dispatch)
        part_time = chapter(self.labor("合同类型：非全日制；工作时间：每周二上午；工资：按用户时薪记录；支付周期：按合同待核"), "6 非全日制独立检查表")
        self.assertIn("| 工作时间安排 | 每周二上午 |", part_time)
        self.assertIn("| 报酬记录 | 按用户时薪记录 |", part_time)
        self.assertIn("支付限制：UNSPECIFIED", part_time)

    def test_given_years_and_base_do_not_generate_compensation_or_a_verdict(self):
        markdown = self.labor("合同类型：劳动合同；工作年限：5年；月工资基数：9000元；解除事由：用户拟调整岗位；证据：岗位沟通邮件；请计算经济补偿并判定合法")
        compensation = chapter(markdown, "9 变更、解除与补偿待核表")
        self.assertIn("| 工作年限 | 5年 |", compensation)
        self.assertIn("| 月工资基数 | 9000元 |", compensation)
        self.assertIn("| 经济补偿 | [A001] |", compensation)
        self.assertIn("| 欠薪 / 赔偿 / 二倍工资 | [A001] |", compensation)
        self.assertNotIn("45000", markdown)
        self.assertNotIn("解除合法", markdown)
        self.assertNotIn("应当补偿", markdown)

    def test_jurisdictions_share_inference_and_dual_sources_stay_separate(self):
        for zone in ("CN", "SG", "EU", "DUAL"):
            with self.subTest(zone=zone):
                markdown = self.labor(f"辖区：{zone}；雇主：示例雇主；基本薪：用户基本薪数；固定津贴：用户津贴数")
                self.assertIn(f"- 辖区：{zone}", markdown)
                if zone in {"SG", "DUAL"}:
                    kets = chapter(markdown, "10 SG 关键雇佣条款 KETs 待核表")
                    self.assertIn("| 基本薪 | 用户基本薪数 |", kets)
                    self.assertIn("| 固定津贴 | 用户津贴数 |", kets)
                if zone == "DUAL":
                    sources = chapter(markdown, "12 依据与自检")
                    self.assertIn("| CN |", sources)
                    self.assertIn("| SG |", sources)

    def test_training_missing_data_has_three_suggested_levels_without_hours(self):
        markdown = self.train()
        self.assertIn("- 辖区：UNSPECIFIED", markdown)
        plan = chapter(markdown, "3 公司 / 项目 / 班组三层课题计划")
        for level in ("公司级", "项目级", "班组级"):
            self.assertIn(f"| {level} | 建议课题：", plan)
            row = next(row for row in plan.splitlines() if row.startswith(f"| {level} |"))
            cells = [cell.strip() for cell in row.strip().removeprefix("|").removesuffix("|").split("|")]
            self.assertEqual(cells[2:8], ["[A001] 待填"] * 6)
        self.assertNotRegex(plan, r"\d+\s*(?:学时|小时)")
        self.assertIn("| 培训完成状态 | 计划待实施 | 不代填合格或有效 |", chapter(markdown, "7 考核与档案"))

    def test_training_fields_reach_course_and_certificate_tables(self):
        markdown = self.train("写培训计划；项目名称：滨河项目；公司名称：示例建设；进场批次：第三批；培训对象：新进场木工；参训人员：张示例、李示例；培训人数：2人；培训日期：2026-09-15；培训地点：培训教室；讲师：讲师甲；公司级课题：企业制度解读；项目级课题：现场风险识别；班组级课题：木工操作练习；公司级学时：6学时；项目级学时：5学时；班组级学时：4学时；班组级讲师：班组讲师乙；学时依据：企业教育制度试行稿；考核方式：问答加实操；证件名称：用户操作证；证号：TEST-01；到期日：2027-09-01；复审计划：由管理员核对窗口")
        register = chapter(markdown, "1 计划登记")
        self.assertIn("| 工程名称 | 滨河项目 |", register)
        self.assertIn("| 进场批次 | 第三批 |", register)
        plan = chapter(markdown, "3 公司 / 项目 / 班组三层课题计划")
        self.assertIn("| 公司级 | 企业制度解读 | 新进场木工 | 6学时 | 讲师甲 | 2026-09-15 | 培训教室 | 问答加实操 |", plan)
        self.assertIn("| 项目级 | 现场风险识别 | 新进场木工 | 5学时 |", plan)
        self.assertIn("| 班组级 | 木工操作练习 | 新进场木工 | 4学时 | 班组讲师乙 |", plan)
        self.assertNotIn("建议课题", plan)
        basis = chapter(markdown, "4 学时与制度核对")
        self.assertIn("| 用户指定制度 | 企业教育制度试行稿 |", basis)
        certificates = chapter(markdown, "8 证件与复审计划")
        self.assertIn("| 用户操作证 | TEST-01 | 2027-09-01 | 由管理员核对窗口 | 未核验 |", certificates)

    def test_roster_is_not_attendance_or_signature_even_when_supplied(self):
        markdown = self.train("参训人员：张示例、李示例；讲师：王示例；班组：木工一组；培训人数：2人；请直接填全部签到并认定合格")
        roster = chapter(markdown, "2 对象分层与名册接口")
        self.assertIn("张示例、李示例", roster)
        self.assertIn("仅供组织，不代表到课或签字", roster)
        sign_in = chapter(markdown, "6 培训签到空表")
        for name in ("张示例", "李示例", "王示例", "木工一组"):
            self.assertNotIn(name, sign_in)
        self.assertEqual(sign_in.count("（空栏）"), 18)
        self.assertNotIn("全部合格", markdown)
        self.assertNotIn("资格有效。", chapter(markdown, "8 证件与复审计划").split("人员证件真伪", 1)[0])

    def test_markdown_source_fields_and_pipes_are_safe_table_values(self):
        markdown = self.train("| 项目名称 | 表格项目 |\n| 公司级课题 | 现场制度 |\n项目级课题：风险 A|B；班组级课题：操作练习")
        self.assertIn("| 工程名称 | 表格项目 |", chapter(markdown, "1 计划登记"))
        plan = chapter(markdown, "3 公司 / 项目 / 班组三层课题计划")
        self.assertIn("| 公司级 | 现场制度 |", plan)
        self.assertIn("| 项目级 | 风险 A&#124;B |", plan)
        self.assertEqual(len(next(row for row in plan.splitlines() if row.startswith("| 项目级")).split("|")), 11)


class HRRuntimeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="civil-hr-")
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

    def _assert_export(self, result, expected_text):
        self.assertTrue(result.get("wrote"), result)
        self.assertTrue(result["submit_blocked"])
        markdown = next(Path(row["path"]) for row in result["files"] if row["path"].endswith(".md"))
        self.assertIn(expected_text, markdown.read_text(encoding="utf-8"))
        xlsx = next(Path(row["path"]) for row in result["files"] if row["path"].endswith(".xlsx"))
        import openpyxl

        workbook = openpyxl.load_workbook(xlsx, data_only=False)
        try:
            values = [str(cell.value) for sheet in workbook for row in sheet for cell in row if cell.value is not None]
            self.assertIn(expected_text, values)
            self.assertFalse(any(cell.data_type == "f" for sheet in workbook for row in sheet for cell in row))
        finally:
            workbook.close()
        return markdown.read_text(encoding="utf-8")

    def test_named_exclusive_produces_labor_tables_in_markdown_and_excel(self):
        result = expert_turn.run_named_exclusive("hr-labor__check", {"text": "合同类型：劳动合同；用人单位：导出测试公司；工资：7800元", "session_id": "named-labor"})
        markdown = self._assert_export(result, "导出测试公司")
        self.assertIn("| 劳动报酬 | 7800元 |", chapter(markdown, "3 劳动合同必备条款对照"))

    def test_expert_turn_produces_training_tables_in_markdown_and_excel(self):
        result = expert_turn.run_expert_turn("写一份培训计划；公司级课题：导出课程甲；项目级课题：导出课程乙；班组级课题：导出课程丙；参训人员：名单甲", "hr-train", session_id="turn-train")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["state"], "done")
        markdown = self._assert_export(result, "导出课程甲")
        self.assertIn("| 班组级 | 导出课程丙 |", chapter(markdown, "3 公司 / 项目 / 班组三层课题计划"))
        self.assertNotIn("名单甲", chapter(markdown, "6 培训签到空表"))

    def test_tool_engine_generates_both_posts_with_their_own_tools(self):
        engine = default_engine()
        for post, tool, text, expected in (
            ("hr-labor", "hr-labor__check", "合同类型：劳务协议；劳务接受方：引擎客户", "引擎客户"),
            ("hr-train", "hr-train__plan", "公司级课题：引擎课程", "引擎课程"),
        ):
            with self.subTest(post=post):
                result = engine.execute(tool, {"text": text, "session_id": post}, expert_id=post, intent="run")
                self.assertTrue(result["ok"], result)
                self.assertIn(tool, result["tools_run"])
                self._assert_export(result, expected)

    def test_chat_and_wrong_expert_never_write_artifacts(self):
        engine = default_engine()
        for post, tool in (("hr-labor", "hr-labor__check"), ("hr-train", "hr-train__plan")):
            with self.subTest(post=post):
                result = expert_turn.run_expert_turn("怎么理解这个岗位的材料要求？", post, session_id=f"chat-{post}")
                self.assertEqual(result["intent"], "chat")
                self.assertFalse(result["wrote"])
                self.assertEqual(result["files"], [])
                denied = engine.execute(tool, {"text": "写一份草稿", "session_id": f"engine-{post}"}, expert_id=post, intent="chat")
                self.assertFalse(denied["ok"])
                self.assertEqual(denied["error_code"], "permission_denied")
                sibling = engine.execute(tool, {"text": "写一份草稿"}, expert_id="finance-tax", intent="run")
                self.assertFalse(sibling["ok"])
        self.assertFalse(list(self.root.rglob("*.md")))
        self.assertFalse(list(self.root.rglob("*.xlsx")))

    def test_high_risk_host_gate_precedes_dedicated_builder(self):
        # Verify the shared guard also surrounds dedicated posts if their risk rises.
        original = expert_turn.get_expert("hr-labor")
        with patch.object(expert_turn, "get_expert", return_value=replace(original, risk="high")):
            with patch("packing_assistant.post_drafts.hr.build_draft", wraps=build_draft) as builder:
                result = expert_turn.run_expert_turn("写一份劳动检查表", "hr-labor", session_id="gate-hr", force_intent="run")
                builder.assert_not_called()
            self.assertFalse(result["wrote"])
            self.assertTrue(result["hitl_pending"])
            self.assertIn("我明白，将由持证人员签认", result["reply"])
            self.assertFalse(list(self.root.rglob("*.md")))
            confirmed = expert_turn.run_expert_turn("写一份劳动检查表；用人单位：确认测试单位", "hr-labor", session_id="gate-hr", force_intent="run", confirm_ok=True)
        self.assertTrue(confirmed["ok"], confirmed)
        self._assert_export(confirmed, "确认测试单位")

    def test_forbidden_user_claim_fails_without_publishing_any_artifact(self):
        result = default_engine().execute("hr-train__plan", {"text": "公司级课题：可以开工", "session_id": "rejected-hr"}, expert_id="hr-train", intent="run")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "forbidden_content")
        self.assertFalse(result["wrote"])
        self.assertEqual(result["files"], [])
        self.assertFalse(list(self.root.rglob("*.md")))
        self.assertFalse(list(self.root.rglob("*.xlsx")))


if __name__ == "__main__":
    unittest.main()
