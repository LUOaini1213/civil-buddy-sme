#!/usr/bin/env python3
"""scripts/demo_facade.py on the SYNTHETIC façade pack: the flows run offline and say what they found.

  linked     the ITT's logistics clauses remain linked to unchanged handling requirements; no plan is invented;
             rev B changes the source hash while missing transport data continues to require a person
  tender     CR16, 420 calendar days, 90-day validity, the 10% bond and the 12-month DLP land in their rows;
             the two bid posts write from the same session's hand-off
  packing    upright / A-frame / no-stack requirements stop automatic boxing; a source-hashed supplement checklist is written
  site docs  a daily report is written; the work-at-height briefing (high risk) writes nothing until the
             person's sentence is given, and is written once it is
  scope      nothing is written outside the job folder the demo creates; a wrong --sign or a used folder
             is refused before any turn runs
No model and no network: steps mode only.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
for _key in [k for k in os.environ if k.endswith("_API_KEY")] + ["CIVIL_AGENT_MODE", "CIVIL_SANDBOX", "CIVIL_APPROVAL"]:
    os.environ.pop(_key, None)

spec = importlib.util.spec_from_file_location("demo_facade", ROOT / "scripts" / "demo_facade.py")
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)

from packing_assistant.civil import CONFIRM  # noqa: E402
from packing_assistant.runtime import workspace  # noqa: E402

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
_WATCH = {"on": False, "writes": []}


def _audit(event, args):
    if not _WATCH["on"]:
        return
    path = None
    if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
        mode, flags = (args[1] if len(args) > 1 else None), (args[2] if len(args) > 2 else 0)
        if (isinstance(mode, str) and any(c in mode for c in "wax+")) or (isinstance(flags, int) and flags & _WRITE_FLAGS):
            path = args[0]
    elif event in {"os.mkdir", "os.rename", "os.replace", "os.remove", "os.rmdir", "shutil.copyfile", "shutil.rmtree"} and args:
        path = args[1] if event in {"os.rename", "os.replace", "shutil.copyfile"} and len(args) > 1 else args[0]
    elif event == "sqlite3.connect" and args:
        path = args[0]
    if path is not None and not isinstance(path, int):
        _WATCH["writes"].append(os.fsdecode(path))


sys.addaudithook(_audit)


class FacadeDemo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="facade-demo-test-")
        cls.base = Path(cls.tmp.name).resolve()
        cls.job = cls.base / "job"
        cls.cwd = Path.cwd()
        cls.out = io.StringIO()
        home = patch.object(Path, "home", return_value=cls.base / "no-home")     # the user's ~/.civil-buddy stays out
        home.start()
        cls.addClassCleanup(home.stop)
        mpl = os.environ.get("MPLCONFIGDIR")
        _WATCH.update(on=True, writes=[])
        try:
            with contextlib.redirect_stdout(cls.out):
                cls.result = demo.run_demo(cls.job)
                cls.signed = demo.Demo(cls.job, sign=CONFIRM)     # the person types the sentence
                cls.signed.briefing()
        finally:
            _WATCH["on"] = False
            workspace.deactivate()
            os.chdir(cls.cwd)
            tempfile.tempdir = None
            if mpl is None:
                os.environ.pop("MPLCONFIGDIR", None)
            else:
                os.environ["MPLCONFIGDIR"] = mpl
        sys.stdout.write(cls.out.getvalue()[-4000:])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_no_flow_errored(self):
        self.assertEqual(self.result["errors"], [])
        self.assertEqual(self.signed.errors, [])

    def test_tender_rows_and_bid_posts(self):
        rows = self.result["tender"]["rows"]
        self.assertIn("CR16", rows["注册资格/工作类别"])
        self.assertIn("420 calendar days", rows["工期"])
        self.assertIn("90 days", rows["投标有效期"])
        self.assertIn("10%", rows["履约担保"])
        self.assertIn("12 months", rows["缺陷责任期/质保期"])
        self.assertEqual(len(self.result["tender"]["scoring"]), 4)
        self.assertTrue(self.result["tender"]["submit_blocked"])
        self.assertTrue(self.result["bid_tech"]["written"])
        self.assertTrue(self.result["bid_compliance"]["written"])
        self.assertTrue((self.job / ".civil-buddy" / "out" / "civil-cli" / "bid-parse" / "tender.parse.md").is_file())

    def test_linked_run_ties_statements_to_clause_and_plan(self):
        linked = self.result["linked"]
        first = {s["kind"]: s for s in linked["first"]["statements"]}
        self.assertEqual(linked["first"]["container"]["clause"], "4.8")
        self.assertIsNone(linked["first"]["plan"])
        self.assertIsNone(linked["rev_b"]["plan"])
        self.assertEqual(first["containers_used"]["clause"], "4.8")
        self.assertEqual(first["gross_mass"]["clause"], "4.9")
        for version in ("first", "rev_b"):
            self.assertEqual(linked[version]["plan_refusal"]["error"], "unsupported_transport_requirements")
            self.assertIsNone(linked[version]["inputs"]["plan"]["sha256"])
            self.assertTrue(all(s["status"] != "covered" for s in linked[version]["statements"]))
            self.assertTrue(linked[version]["plan_refusal"]["needs_human"])
        self.assertTrue(any(item["input"] == "panel_list" for item in linked["changes"]["inputs_changed"]))
        self.assertTrue(linked["submit_blocked"])
        self.assertTrue(Path(linked["record"]).is_file())
        self.assertIn("since the previous run: panel list (facade_panels.xlsx -> facade_panels_rev_b.xlsx) changed", self.out.getvalue())

    def test_original_handling_requirements_produce_a_human_checklist_not_a_plan(self):
        from hashlib import sha256

        packing = self.result["packing"]
        self.assertEqual(packing["status"], "needs_human")
        self.assertIsNone(packing["plan"])
        self.assertTrue(packing["submit_blocked"])
        self.assertEqual(set(packing["sources"]), set(demo.PANELS))
        for name, review in packing["sources"].items():
            with self.subTest(name=name):
                self.assertEqual(review["reason"], "unsupported_transport_requirements")
                self.assertTrue(review["needs_human"])
                self.assertTrue(all(item["requirements"] for item in review["needs_human"]))
                original = (demo.FIXTURES / name).read_bytes()
                self.assertEqual((self.job / "inputs" / name).read_bytes(), original)
                self.assertEqual(review["source_sha256"], sha256(original).hexdigest())
                self.assertIsNone(review["containers_used"])
                self.assertIsNone(review["can_fit"])
                record = json.loads(Path(review["record"]).read_text(encoding="utf-8"))
                self.assertEqual(record["needs_human"], review["needs_human"])
                text = Path(review["checklist"]).read_text(encoding="utf-8")
                self.assertIn("SYNTHETIC", text)
                self.assertIn("未生成装柜方案、柜数或可装结论", text)
                self.assertIn("每包装毛重与包装数", text)
                self.assertIn("每架净重、皮重和声明载荷上限", text)
                self.assertIn(review["source_sha256"], text)
        self.assertFalse((self.job / "inputs" / "panels_no_notes.xlsx").exists())
        for name in demo.INPUTS:
            self.assertEqual((self.job / "inputs" / name).read_bytes(), (demo.FIXTURES / name).read_bytes(), name)
        self.assertFalse(list(self.job.rglob("pack-plan.json")))
        self.assertIn("No loading plan or container count", self.out.getvalue())
        self.assertIn("运输要求包含自动成箱尚不能执行的约束", self.out.getvalue())
        self.assertNotIn("行缺重量或尺寸", self.out.getvalue())

    def test_daily_report_is_written(self):
        daily = self.result["daily"]
        self.assertTrue(daily["written"])
        self.assertEqual(daily["rows"]["日期"], "2026-09-24")
        self.assertEqual(daily["rows"]["部位"], "东立面五层")

    def test_briefing_waits_for_the_person(self):
        self.assertTrue(self.result["briefing"]["refused_without_sentence"])
        self.assertFalse(self.result["briefing"]["written"])
        self.assertIn("This demo does not supply the sentence", self.out.getvalue())
        self.assertIn(CONFIRM, self.out.getvalue())
        signed = self.signed.result["briefing"]
        self.assertTrue(signed["refused_without_sentence"])      # the signed run still asks first
        self.assertTrue(signed["written"])
        self.assertTrue((self.job / signed["file"]).is_file())

    def test_nothing_is_written_outside_the_job_folder(self):
        outside = sorted({p for p in _WATCH["writes"] if not Path(p).resolve().is_relative_to(self.job)})
        self.assertEqual(outside, [], "\n".join(outside))
        self.assertGreater(len(_WATCH["writes"]), 10)          # the hook did see the drafts being written

    def test_fixtures_say_they_are_synthetic(self):
        import openpyxl

        for name in ("facade_itt_doc.md", "daily_report_input.txt", "wah_briefing_input.txt", "README.md"):
            head = (demo.FIXTURES / name).read_text(encoding="utf-8")[:400]
            self.assertRegex(head, r"SYNTHETIC|合成示例", name)
        for name in ("facade_panels.xlsx", "facade_panels_zh.xlsx", "facade_panels_rev_b.xlsx"):
            wb = openpyxl.load_workbook(demo.FIXTURES / name, read_only=True)
            try:
                self.assertEqual(wb.sheetnames, ["materials", "README"])
                self.assertIn("SYNTHETIC", str(next(wb["README"].iter_rows(values_only=True))[0]))
                names = [row[1] for row in wb["materials"].iter_rows(min_row=2, values_only=True)]
                self.assertTrue(names and all("SYNTHETIC" in n or "合成示例" in n for n in names), names)
            finally:
                wb.close()

    def test_bad_arguments_are_refused_before_any_turn(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(demo.main(["--sign", "yes"]), 2)
            self.assertEqual(demo.main(["--job", str(self.job)]), 2)         # used folder: never written into
        self.assertIsNone(workspace.active())


if __name__ == "__main__":
    unittest.main(verbosity=1)
