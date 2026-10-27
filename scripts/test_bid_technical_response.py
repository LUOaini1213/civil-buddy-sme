"""A technical response must contain supplied prose in its business section, not an empty frame."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from packing_assistant.tools.technical_response import collect
from packing_assistant.tools.tender_parse import run_tender_pipeline, build_tech_outline_from_handoff

TENDER = "招标正文：\n项目名称：合成幕墙技术响应项目。\n工期180日历天。施工组织设计35分。\n\n"
RESPONSE = ("投标响应：\n部署工艺：先核对接口图，再按已提供的样板记录整理分区安装顺序。\n"
            "工期资源：每周更新到货与安装计划，机械数量未提供。\n"
            "质量措施：保存材料检验记录，样板确认记录另附。\n"
            "安全环保：高风险专项另行编制，本稿只列接口，不作实施依据。\n"
            "组织机构：李测试负责节点图协调，陈测试负责材料资料。\n")


class TechnicalResponseTests(unittest.TestCase):
    def test_supplied_bidder_prose_reaches_corresponding_body_and_keeps_source(self):
        result = run_tender_pipeline(TENDER + RESPONSE)
        outline = result["tech_outline"]
        self.assertEqual(outline["technical_response_count"], 5)
        self.assertEqual(outline["body_status"], "source_organized")
        md = outline["markdown"]
        for section, following, value in (
            ("3 部署工艺", "4 工期资源", "先核对接口图"),
            ("4 工期资源", "5 质量", "每周更新到货与安装计划"),
            ("5 质量", "6 安全环保", "保存材料检验记录"),
            ("6 安全环保", "7 点名专项", "本稿只列接口"),
            ("8 组织机构", "9 缺项与自检", "李测试负责节点图协调"),
        ):
            body = md.split("## " + section, 1)[1].split("## " + following, 1)[0]
            self.assertIn(value, body)
            self.assertIn("来源：本轮用户输入 L", body)
        self.assertTrue(result["submit_blocked"])

    def test_tender_measures_are_not_rewritten_as_our_commitment(self):
        result = run_tender_pipeline(TENDER + "招标要求：部署工艺：按指定顺序进行安装。")
        self.assertEqual(result["tech_outline"]["technical_response_count"], 0)
        self.assertIn("未提供明确的我方响应措施", result["tech_outline_markdown"])

    def test_role_declared_by_caller_beats_role_markers_inside_a_document(self):
        self.assertEqual(collect(sources=[{"role": "tender", "text": RESPONSE}]), [])
        self.assertEqual(collect(sources=[{"role": "reference", "text": RESPONSE}]), [])
        records = collect(sources=[{"role": "response", "title": "响应草稿.txt", "text": "部署工艺：先整理样板记录。"}])
        self.assertEqual(records[0]["source"], "响应草稿.txt")
        self.assertEqual(records[0]["value"], "先整理样板记录。")

    def test_unowned_section_label_is_not_a_bidder_fact(self):
        self.assertEqual(collect("部署工艺：先安装20块。"), [])
        own = collect("我方部署工艺：先整理接口图。\n请输出Word，并附审批结论。")
        self.assertEqual(len(own), 1)
        self.assertEqual(own[0]["value"], "先整理接口图。")

    def test_lots_and_values_are_not_borrowed_across_sections(self):
        records = collect("一标段我方部署工艺：安装20块。\n二标段我方工期资源：工人8人。")
        self.assertEqual([(r["lot"], r["section"], r["value"]) for r in records],
                         [("一标段", "deployment", "安装20块。"), ("二标段", "schedule", "工人8人。")])
        md = build_tech_outline_from_handoff({"technical_response": records})["markdown"]
        deployment = md.split("## 3 部署工艺", 1)[1].split("## 4 工期资源", 1)[0]
        self.assertIn("一标段", deployment)
        self.assertNotIn("8人", deployment)

    def test_approval_claims_are_held_for_review_not_published_as_technical_prose(self):
        records = collect("我方安全环保：本方案已通过专家论证，可以开工。")
        self.assertEqual(records[0]["status"], "needs_review")
        md = build_tech_outline_from_handoff({"technical_response": records})["markdown"]
        self.assertNotIn("可以开工", md)
        self.assertIn("技术响应待复核", md)

    def test_large_source_reports_its_boundary_instead_of_silently_truncating(self):
        records = collect("我方部署工艺：" + "资料" * 100_001)
        self.assertEqual(records[0]["status"], "too_large")
        result = build_tech_outline_from_handoff({"technical_response": records})
        self.assertEqual(result["body_status"], "outline_only")
        self.assertIn("超过读取上限", result["markdown"])

    def test_paragraph_limit_includes_the_last_line_and_all_response_files(self):
        sixty = "\n".join(f"我方部署工艺：第{i}段来源草稿。" for i in range(60))
        self.assertEqual(len(collect(sixty)), 60)
        overflow = collect(sixty + "\n我方部署工艺：第61段来源草稿。")
        self.assertEqual(len(overflow), 1)
        self.assertEqual(overflow[0]["status"], "too_large")
        split = collect(sources=[
            {"role": "response", "title": "第一份响应.txt", "text": sixty},
            {"role": "response", "title": "第二份响应.txt", "text": "部署工艺：第61段来源草稿。"}])
        self.assertEqual(split[0]["status"], "too_large")

    def test_short_prose_follow_up_keeps_the_previous_tenders_scores(self):
        from packing_assistant import expert_turn
        from packing_assistant.runtime import session_handoff
        old = run_tender_pipeline(TENDER + RESPONSE)["handoff"]
        with tempfile.TemporaryDirectory(prefix="technical-followup-", dir=ROOT / "output") as directory:
            with patch.object(expert_turn, "_OUT", Path(directory)), \
                 patch.object(session_handoff, "load_handoff", return_value=old), \
                 patch.object(session_handoff, "save_handoff"), \
                 patch.dict(os.environ, {"CIVIL_JOB_ROOT": "", "CIVIL_SANDBOX_ROOTS": directory}):
                result = expert_turn.run_named_exclusive("bid-tech__expand", {
                    "text": "我方部署工艺：先核对图纸。", "session_id": "followup-tech"})
            self.assertTrue(result["wrote"])
            md = Path(result["files"][0]["path"]).read_text(encoding="utf-8")
            self.assertIn("35分", md)
            self.assertIn("先核对图纸", md.split("## 3 部署工艺")[1].split("## 4 工期资源")[0])
            self.assertNotIn("先核对接口图", md.split("## 3 部署工艺")[1].split("## 4 工期资源")[0])
            self.assertIn("每周更新到货与安装计划", md.split("## 4 工期资源")[1].split("## 5 质量")[0])
            self.assertEqual(result["tech_outline"]["technical_response_count"], 5)

    def test_prose_markup_stays_source_text_not_a_table_or_html_injection(self):
        records = collect("我方部署工艺：接口A | 接口B <script>alert(1)</script>。")
        md = build_tech_outline_from_handoff({"technical_response": records})["markdown"]
        self.assertIn("&#124;", md)
        self.assertNotIn("<script>", md)
        self.assertIn("&lt;script&gt;", md)

    def test_workflow_uses_response_source_and_preserves_immutable_handoff(self):
        from packing_assistant.runtime import tender_workflow
        from packing_assistant.runtime.civil_config import CivilConfig
        with tempfile.TemporaryDirectory(prefix="technical-response-", dir=ROOT / "output") as directory:
            with patch.dict(os.environ, {"CIVIL_JOB_ROOT": "", "CIVIL_SANDBOX_ROOTS": directory}), \
                 patch("packing_assistant.runtime.civil_config.load_config", return_value=CivilConfig()):
                result = tender_workflow.run_tender_workflow("核对招标并起草技术响应", session_id="tech-response-test",
                    output_root=Path(directory), sources=[
                        {"source_id": "t", "role": "tender", "text": TENDER, "title": "招标.txt"},
                        {"source_id": "r", "role": "response", "text": RESPONSE, "title": "响应.txt"}])
            self.assertTrue(result["ok"], result.get("error"))
            path = next(Path(f["path"]) for f in result["files"] if f["name"] == "bid-tech.md")
            md = path.read_text(encoding="utf-8")
            self.assertIn("先核对接口图", md.split("## 3 部署工艺")[1].split("## 4 工期资源")[0])
            self.assertIn("来源：响应.txt", md)
            self.assertTrue(result["submit_blocked"])
            self.assertTrue(result["review"]["handoff_unchanged"])


if __name__ == "__main__":
    unittest.main()
