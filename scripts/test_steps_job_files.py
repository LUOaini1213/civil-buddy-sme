#!/usr/bin/env python3
"""The default (steps) path reads what the task points at in the job folder.

Before: `civil exec "packing.csv 要几个柜"` answered from an empty solver snapshot — every field
UNSPECIFIED — while the packing list sat in the folder, and "解析招标 tender.txt" parsed the
sentence instead of the file. The model-driven path already used the engine; the default did not.

  a named packing list     the packing engine computes; reply and pack-plan.md carry its numbers
  a list the engine gates  rows missing weight or size are listed; no container count is given
  can_fit=False            is a failure, said as one
  two named tables / none  nothing is guessed; the old projection answers, with a pointer
  a named tender document  is what gets parsed; with a named response it is compared, roles by file name
  civil's own Excel copy   in the folder's top level is a deliverable, never read back as material,
                           and a same-named workbook the user made is never overwritten
No model is involved anywhere in this file.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
for name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY", "CIVIL_API_KEY", "CIVIL_AGENT_MODE"):
    os.environ.pop(name, None)

from packing_assistant import office_job  # noqa: E402
from packing_assistant.runtime import agent_loop, workspace  # noqa: E402
from packing_assistant.runtime.agent_loop import run_agent  # noqa: E402
from packing_assistant.tools.pack_ship_solve import plan_reply, plan_report_md  # noqa: E402

HEADER = "S/N,Description of Goods,Q'ty,L (mm),W (mm),H (mm),G.W. (kg)\n"
GOOD = HEADER + "1,Steel bracket,4,1200,400,300,12.5\n2,Base plate,2,800,800,50,40\n"
NO_WEIGHT = HEADER + "1,Steel bracket,4,1200,400,300,12.5\n2,Rail B,2,800,800,50,\n"
TENDER = "第一章 投标人须知\n★工期60日历天。\n★投标保证金人民币20万元。\n"
RESPONSE = "投标响应\n我方承诺工期999日历天。\n投标保证金人民币20万元已备妥。\n"


class JobFolderCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="civil-steps-")
        self.addCleanup(temporary.cleanup)
        self.addCleanup(os.chdir, Path.cwd())
        self.addCleanup(workspace.deactivate)
        self.job = Path(temporary.name).resolve()
        (self.job / "CIVIL.md").write_text("- 项目：东桥改造工程（二标段）\n", encoding="utf-8")
        (self.job / "资料").mkdir()
        (self.job / "资料" / "packing.csv").write_text(GOOD, encoding="utf-8", newline="")
        os.chdir(self.job)
        with patch.object(Path, "home", return_value=self.job / "no-home"):
            workspace.activate(self.job)

    def state(self, *parts):
        return self.job.joinpath(".civil-buddy", "out", *parts)


class NamedFileTests(JobFolderCase):
    def test_files_are_named_by_path_name_or_a_stem_that_stands_alone(self):
        (self.job / "现场记录.txt").write_text("x", encoding="utf-8")
        target = self.job / "资料" / "packing.csv"
        for text in ("算 资料/packing.csv", "算 资料\\packing.csv", "算 PACKING.CSV", "packing 要几个柜"):
            self.assertEqual(office_job.files_named_in(text, (".csv",)), [target], text)
        self.assertEqual(office_job.files_named_in("用 packing-agent 算一下"), [])          # a tool's name, not the file
        self.assertEqual(office_job.files_named_in("看 现场记录"), [self.job / "现场记录.txt"])
        self.assertEqual(office_job.files_named_in("看 现场记录", (".csv",)), [])
        self.assertEqual(office_job.files_named_in(""), [])

    def test_the_tree_lists_one_level_down_and_skips_state_and_the_project_file(self):
        (self.job / ".civil-buddy" / "out").mkdir(parents=True, exist_ok=True)
        (self.job / ".civil-buddy" / "out" / "draft.md").write_text("x", encoding="utf-8")
        (self.job / "~$lock.xlsx").write_text("x", encoding="utf-8")
        (self.job / "资料" / "更深").mkdir()
        (self.job / "资料" / "更深" / "too-deep.csv").write_text("x", encoding="utf-8")
        self.assertEqual([row["name"] for row in office_job.job_tree_files()], ["资料/packing.csv"])


class PackingListTests(JobFolderCase):
    def test_a_named_packing_list_is_computed_by_the_engine(self):
        out = run_agent("帮我算一下 packing.csv 要几个柜", session_id="civil-cli")
        self.assertTrue(out["ok"], out.get("reply"))
        self.assertEqual((out["skill"], out["intent"], out["agent_mode"]), ("pack-ship", "run", "steps"))
        plan = out["pack_ship"]["plan"]
        self.assertEqual(plan["source"], "solver")
        self.assertGreaterEqual(plan["containers_used"], 1)
        self.assertIn(f"{plan['containers_used']} 个 {plan['container_type']}", out["reply"])
        self.assertNotIn("UNSPECIFIED", out["reply"])
        report = self.state("civil-cli", "pack-ship", "pack-plan.md").read_text(encoding="utf-8")
        self.assertEqual(report, plan_report_md(plan, "packing.csv"))
        self.assertIn("柜体额定载重（不是货重）", report)
        self.assertTrue(any(f["name"] == "pack-plan.md" for f in out["files"]), out["files"])

    def test_rows_the_engine_cannot_use_are_listed_and_no_count_is_given(self):
        (self.job / "缺重量.csv").write_text(NO_WEIGHT, encoding="utf-8", newline="")
        out = run_agent("装箱拼柜 缺重量.csv", session_id="civil-cli")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error_code"], "missing_weight")
        self.assertIn("Rail B", out["reply"])
        self.assertNotRegex(out["reply"], r"\d+\s*个\s*40HQ")
        self.assertFalse(self.state("civil-cli", "pack-ship", "pack-plan.md").exists())
        self.assertEqual([row["name"] for row in out["pack_ship"]["needs_human"]], ["Rail B"])

    def test_cannot_fit_is_said_as_a_failure(self):
        plan = {"ok": True, "source": "solver", "can_fit": False, "containers_used": 9, "container_type": "40HQ",
                "binding_constraint": "weight"}
        self.assertIn("装不下", plan_reply(plan, "x.csv"))
        self.assertIn("不是可用方案", plan_reply(plan, "x.csv"))
        with patch("packing_assistant.tools.pack_ship_solve.run_plan", return_value=plan):
            out = run_agent("装箱拼柜 packing.csv", session_id="civil-cli")
        self.assertEqual((out["ok"], out["error_code"], out["state"]), (False, "cannot_fit", "failed"))

    def test_nothing_is_guessed_between_two_tables_or_without_one(self):
        (self.job / "second.csv").write_text(GOOD, encoding="utf-8", newline="")
        both = run_agent("装箱拼柜 packing.csv second.csv", session_id="civil-cli")
        self.assertNotIn("plan", [key for key, value in both["pack_ship"].items() if isinstance(value, dict) and value.get("source") == "solver"])
        self.assertIn("一次点名一份", both["reply"])
        none = run_agent("装箱拼柜 出个方案", session_id="other-one")
        self.assertIn("UNSPECIFIED", none["reply"])
        self.assertIn("资料/packing.csv", none["reply"])
        self.assertIn("点名", none["reply"])

    def test_outside_a_job_folder_no_file_is_ever_picked_up(self):
        self.assertTrue(agent_loop._named_packing_list("装箱拼柜 packing.csv").endswith("packing.csv"))
        workspace.deactivate()
        self.assertEqual(office_job.job_tree_files(), [])
        self.assertEqual(agent_loop._named_packing_list("装箱拼柜 packing.csv"), "")     # the old projection path runs
        self.assertEqual(agent_loop._with_named_documents("解析招标 招标文件.txt"), "解析招标 招标文件.txt")
        self.assertIsNone(agent_loop._tender_sources("全面检查 招标文件.txt 投标响应.txt"))


class OwnExportTests(JobFolderCase):
    DAILY = "整理日报，日期：2031年5月6日，部位：东桥3号墩，天气：晴，出勤：钢筋工12人"

    def test_an_exported_workbook_is_not_read_back_as_material(self):
        # seen 2026-09-19: the daily report's attendance table turned up inside the next draft
        self.assertTrue(run_agent(self.DAILY, session_id="civil-cli")["ok"])
        self.assertTrue((self.job / "pm-daily__log.xlsx").is_file())
        self.assertEqual(office_job.own_exports(), {"pm-daily__log.xlsx"})
        self.assertNotIn("pm-daily__log.xlsx", [row["name"] for row in office_job.list_job_files()])
        self.assertNotIn("pm-daily__log.xlsx", [row["name"] for row in office_job.job_tree_files()])
        second = run_agent("出一份采购计划：钢筋 20 吨，5月20日前到场", session_id="civil-cli")
        self.assertTrue(second["ok"], second.get("reply"))
        draft = "\n".join(Path(f["path"]).read_text(encoding="utf-8") for f in second["files"] if f["path"].endswith(".md"))
        self.assertIn("20 吨", draft)
        self.assertNotIn("钢筋工12人", draft)
        self.assertNotIn("pm-daily__log", draft)

    def test_a_second_export_preserves_the_earlier_copy(self):
        run_agent(self.DAILY, session_id="civil-cli")
        before = (self.job / "pm-daily__log.xlsx").read_bytes()
        run_agent(self.DAILY.replace("12人", "14人"), session_id="civil-cli")
        self.assertEqual((self.job / "pm-daily__log.xlsx").read_bytes(), before)
        self.assertTrue((self.job / "pm-daily__log-2.xlsx").is_file())
        self.assertNotEqual((self.job / "pm-daily__log-2.xlsx").read_bytes(), before)
        self.assertEqual(office_job.own_exports(), {"pm-daily__log.xlsx", "pm-daily__log-2.xlsx"})

    def test_a_workbook_the_user_made_is_never_overwritten(self):
        mine = self.job / "pm-daily__log.xlsx"
        mine.write_bytes(b"the user's own workbook")
        out = run_agent(self.DAILY, session_id="civil-cli")
        self.assertTrue(out["ok"], out.get("reply"))
        self.assertEqual(mine.read_bytes(), b"the user's own workbook")
        self.assertEqual(office_job.own_exports(), set())
        self.assertTrue(self.state("civil-cli", "pm-daily", "pm-daily__log.xlsx").is_file())   # the copy beside the draft


class TenderDocumentTests(JobFolderCase):
    def setUp(self):
        super().setUp()
        (self.job / "招标文件.txt").write_text(TENDER, encoding="utf-8")
        (self.job / "投标响应.txt").write_text(RESPONSE, encoding="utf-8")

    def test_the_named_tender_document_is_what_gets_parsed(self):
        out = run_agent("解析招标 招标文件.txt", session_id="civil-cli")
        self.assertTrue(out["ok"], out.get("reply"))
        self.assertEqual(out["skill"], "bid-parse")
        rows = str(out.get("matrix"))
        self.assertIn("60", rows)
        self.assertIn("20万元", rows)

    def test_roles_come_from_the_file_names_and_are_never_guessed(self):
        sources = agent_loop._tender_sources("全面检查投标响应：招标文件.txt 投标响应.txt")
        self.assertEqual(sorted((s["role"], s["title"]) for s in sources), [("response", "投标响应.txt"), ("tender", "招标文件.txt")])
        self.assertEqual(len({s["source_id"] for s in sources}), 2)
        self.assertIsNone(agent_loop._tender_sources("全面检查 投标响应.txt"))           # no tender named
        (self.job / "招标补遗.txt").write_text("★工期90日历天。", encoding="utf-8")
        self.assertIsNone(agent_loop._tender_sources("检查 招标文件.txt 招标补遗.txt 投标响应.txt"))   # two tenders

    def test_a_comprehensive_check_compares_the_two_named_documents(self):
        out = run_agent("全面检查投标响应：招标文件.txt 投标响应.txt", session_id="civil-cli")
        self.assertTrue(out["ok"], out.get("reply"))
        notes = [c["note"] for c in out["review"]["conflicts"]]
        self.assertTrue(any("999" in note and "60" in note for note in notes), notes)
        self.assertTrue(out["review"]["response_evidence_supplied"])
        self.assertTrue(out["submit_blocked"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
