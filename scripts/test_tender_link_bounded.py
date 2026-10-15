#!/usr/bin/env python3
"""The tender clause reader is bounded (packing_assistant/tender_packing_link.py, gateway/web_link.py), on SYNTHETIC text.

  dense      a sentence with more than 12 mass figures, or longer than 2,000 characters, is not read figure by figure:
             its clause becomes one row for a person ("too many figures in one sentence for a reliable reading"),
             never quoted, never dropped and never covered; 12 figures are still read one by one
  covered    a clause that also holds such a sentence never has its gross mass stated as met
  linear     5,000 mass figures in one sentence (205 kB) read in seconds, not minutes (before: 515.7 s)
  cancel     a cancelled or timed-out run stops at the reader's next checkpoint (one per clause and per sentence), so
             a timed-out tender.packing_link worker exits soon after its deadline and gives its slot back
  web        POST /api/tender/link refuses tender text over CIVIL_LINK_MAX_TENDER_TEXT_KB (400 kB) with 413: a .md
             before a slot or a job folder is taken, a .docx / .pdf once its text is read; the ~123 kB probe tender
             (3,000 figures in one sentence) now answers 200 with the row for a person
No model and no network.
"""
from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
for _key in [k for k in os.environ if k.endswith("_API_KEY")] + ["CIVIL_SANDBOX", "CIVIL_APPROVAL", "CIVIL_JOB_ROOT"]:
    os.environ.pop(_key, None)
os.environ["CIVIL_AGENT_MODE"] = "steps"

from packing_assistant import tender_packing_link as tpl  # noqa: E402

FIXTURES = ROOT / "examples" / "facade-demo"
ITT = (FIXTURES / "facade_itt_doc.md").read_text(encoding="utf-8")
PANELS = (FIXTURES / "facade_panels.xlsx").read_bytes()


def synthetic_geometry_panels() -> bytes:
    """A separate unrestricted geometry control, never the facade source file.

    A fitted plan is necessary to test whether a dense *tender clause* prevents
    its mass being claimed as covered. The real facade list independently stops
    earlier on its explicit upright/A-frame/no-stack requirements.
    """
    from openpyxl import Workbook
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "SYNTHETIC geometry only"
    sheet.append(["id", "name", "quantity", "length_mm", "width_mm", "height_mm", "weight_kg"])
    sheet.append(["CONTROL-1", "Synthetic rectangular block", 2, 1000, 500, 200, 10])
    data = io.BytesIO()
    workbook.save(data)
    workbook.close()
    return data.getvalue()
MASS_CLAUSE = ("4.9 Container gross mass: the gross mass of each loaded container, including the container tare, shall not exceed "
               "20,000 kg to suit the site hoisting and road haulage arrangements.")
# the retry/timeout probe's clause: two mass figures per repeat, all in one run-on sentence
UNIT = "the gross mass of each loaded container shall not exceed 2 t, and each crate 1.5 t, "
SENTENCE = "The gross mass of each loaded container shall not exceed 2 t and each crate 1.5 t. "
DENSE_ROW = "too many figures in one sentence for a reliable reading"


def dense_tender(repeats: int) -> str:
    return "# ITT\n\n12.3 Transport. " + UNIT * repeats + "end.\n"


def many_clauses(n: int) -> str:
    return "# ITT\n\n" + "".join(f"12.{i} Transport. {SENTENCE}\n" for i in range(1, n + 1))


def docx_bytes(text: str) -> bytes:
    from xml.sax.saxutils import escape

    paras = "".join(f"<w:p><w:r><w:t xml:space=\"preserve\">{escape(line)}</w:t></w:r></w:p>" for line in text.splitlines())
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body>{paras}</w:body></w:document>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Override '
                   'PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.'
                   'wordprocessingml.document.main+xml"/></Types>')
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


class Reader(unittest.TestCase):
    def test_a_sentence_with_too_many_figures_goes_to_a_person_whole(self) -> None:
        took = {}
        for figures in (14, 400, 5000):
            t0 = time.perf_counter()
            clauses = tpl.logistics_clauses(dense_tender(figures // 2), source="itt.md")
            took[figures] = time.perf_counter() - t0
            (clause,) = clauses
            self.assertIn("unplaced", clause["kinds"], figures)
            # nothing of that sentence is read as a limit: no gross mass, no per-package limit
            self.assertNotIn("gross_mass", clause["kinds"], figures)
            self.assertNotIn("per_package_limit", clause["kinds"], figures)
            self.assertEqual({"sentences": 1, "mass_figures": figures}, {k: clause["dense"][k] for k in ("sentences", "mass_figures")})
        # before the cap the same 5,000 figures took 515.7 s in _mass_limits alone (quadratic); now it is linear
        self.assertLess(took[5000], 20.0, took)

    def test_twelve_figures_are_still_read_one_by_one(self) -> None:
        limits = tpl._mass_limits("12.3 Transport. " + UNIT * 6 + "end.")
        self.assertEqual(([2000.0] * 6, [1500.0] * 6, []), (limits["container"], limits["package"], limits["dense"]))
        limits = tpl._mass_limits("12.3 Transport. " + UNIT * 7 + "end.")
        self.assertEqual(([], [], [(14, len("12.3 Transport. " + UNIT * 7 + "end."))]),
                         (limits["container"], limits["package"], limits["dense"]))

    def test_a_sentence_over_2000_characters_goes_to_a_person(self) -> None:
        long = ("4.9 Container gross mass: the gross mass of each loaded container shall not exceed 20,000 kg, "
                + "having regard to the site hoisting and haulage arrangements agreed with the main contractor, " * 22 + "and so on.")
        self.assertGreater(len(long), tpl.MAX_SENTENCE_CHARS)
        (clause,) = tpl.logistics_clauses(long + "\n", source="itt.md")
        self.assertEqual(["unplaced"], clause["kinds"])
        self.assertEqual(1, clause["dense"]["mass_figures"])
        # just under the cap it is read as before: one container limit
        short = long[:tpl.MAX_SENTENCE_CHARS - 20].rsplit(",", 1)[0] + "."
        (clause,) = tpl.logistics_clauses(short + "\n", source="itt.md")
        self.assertEqual((["gross_mass"], [20000.0]), (clause["kinds"], clause["limits_kg"]))

    def test_the_reader_stops_at_its_next_checkpoint(self) -> None:
        from packing_assistant.runtime import cancel

        stop, calls = threading.Event(), []
        real = tpl._checkpoint

        def counting() -> None:
            calls.append(1)
            if len(calls) == 50:
                stop.set()                      # what ToolEngine does when the deadline passes, or a person cancels
            real()

        body = "# ITT\n\n12.3 Transport. " + SENTENCE * 2000 + "\n"       # one clause, 2,000 sentences
        with patch.object(tpl, "_checkpoint", counting), cancel.scope(event=stop):
            with self.assertRaises(cancel.RunCancelled):
                tpl.logistics_clauses(body, source="itt.md")
        self.assertEqual(50, len(calls), "the reader went on past the checkpoint after the stop")


class Linked(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from packing_assistant.runtime import workspace

        cls.tmp = tempfile.TemporaryDirectory(prefix="tender-link-bounded-")
        cls.job = Path(cls.tmp.name).resolve() / "job"
        (cls.job / "inputs").mkdir(parents=True)
        (cls.job / "synthetic_geometry_panels.xlsx").write_bytes(synthetic_geometry_panels())
        dense = "The gross mass of each loaded container shall not exceed " + ", ".join(f"{t} t" for t in range(2, 16)) + "."
        assert MASS_CLAUSE in ITT
        (cls.job / "itt_dense_49.md").write_text(ITT.replace(MASS_CLAUSE, MASS_CLAUSE + " " + dense), encoding="utf-8")
        (cls.job / "itt_probe.md").write_text(dense_tender(1500), encoding="utf-8")
        (cls.job / "itt_big.md").write_text(many_clauses(8000), encoding="utf-8")
        cls.cwd = Path.cwd()
        home = patch.object(Path, "home", return_value=Path(cls.tmp.name) / "no-home")
        home.start()
        cls.addClassCleanup(home.stop)
        os.chdir(cls.job)
        workspace.activate(cls.job)
        # the modules a run imports on its first call: imported here, so the deadline below meets the reader, not an import
        tpl.run_link(str(cls.job / "itt_dense_49.md"), str(cls.job / "synthetic_geometry_panels.xlsx"))

    @classmethod
    def tearDownClass(cls) -> None:
        from packing_assistant.runtime import workspace

        workspace.deactivate()
        os.chdir(cls.cwd)
        cls.tmp.cleanup()

    def test_a_dense_sentence_is_one_row_for_a_person_and_the_clause_is_never_covered(self) -> None:
        out = tpl.run_link(str(self.job / "itt_dense_49.md"), str(self.job / "synthetic_geometry_panels.xlsx"))
        by_key = {s["key"]: s for s in out["record"]["statements"]}
        row = by_key["unplaced@4.9"]
        self.assertEqual("human_required", row["status"])
        self.assertIn(DENSE_ROW, row["text"])
        self.assertIn("a person reads Clause 4.9", row["text"])
        self.assertIsNone(row["figures"]["quoted"])
        self.assertEqual(14, row["figures"]["mass_figures"])
        self.assertNotIn("15 t", row["text"], "the dense sentence was quoted")
        # the 20,000 kg limit of the same clause is read, but not stated as met while the dense sentence is unread
        self.assertEqual("human_required", by_key["gross_mass@4.9"]["status"])
        self.assertIn("too many figures", by_key["gross_mass@4.9"]["text"])
        # the rest of the demo reads as before
        self.assertEqual("covered", by_key["container_type@4.8"]["status"])
        self.assertEqual("partial", by_key["containers_used@4.8"]["status"])

    def test_the_probe_tender_is_read_in_seconds(self) -> None:
        t0 = time.perf_counter()
        out = tpl.run_link(str(self.job / "itt_probe.md"), str(self.job / "synthetic_geometry_panels.xlsx"))
        took = time.perf_counter() - t0
        counts = {s: sum(1 for x in out["statements"] if x["status"] == s) for s in ("covered", "partial", "gap")}
        self.assertEqual({"covered": 0, "partial": 0, "gap": 0}, counts)
        self.assertEqual(1, sum(DENSE_ROW in s["text"] for s in out["statements"]))
        self.assertLess(took, 45.0)         # before: the reader alone ran past the tool's 60 s deadline for minutes

    def test_a_timed_out_link_gives_its_worker_back_soon_after_the_deadline(self) -> None:
        from packing_assistant.runtime.tool_engine import ToolEngine, _tender_packing_link

        engine = ToolEngine()
        engine.register("tender.packing_link", _tender_packing_link, expert_id="bid-parse", timeout_s=0.5)
        args = {"tender_path": str(self.job / "itt_big.md"), "packing_list": str(self.job / "synthetic_geometry_panels.xlsx")}
        result = engine.execute("tender.packing_link", args, expert_id="bid-parse", intent="run", run_id="bounded")
        deadline = time.perf_counter()
        self.assertEqual("timeout", result["error_code"], result)
        idle = threading.Event()
        engine.when_idle("bounded", idle.set)
        # 8,000 clauses (670 kB) take well over a minute to read and parse in full; the worker stops at a checkpoint
        self.assertTrue(idle.wait(30), "the timed-out worker did not stop at a checkpoint")
        self.assertLess(time.perf_counter() - deadline, 30.0)
        self.assertEqual({}, engine._workers)


class Web(unittest.TestCase):
    def setUp(self) -> None:
        from fastapi.testclient import TestClient

        from gateway import app as gateway
        from packing_assistant import storage

        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ))
        for key in ("CIVIL_ALLOW_OPEN_LAN", "CIVIL_JOB_ROOT", "UVICORN_HOST", "CIVIL_LINK_MAX_TENDER_MB",
                    "CIVIL_LINK_MAX_PANEL_MB", "CIVIL_LINK_MAX_TENDER_TEXT_KB"):
            os.environ.pop(key, None)
        self.tmp = Path(tempfile.mkdtemp(prefix="test-web-link-bounded-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        os.environ["PACKING_OUTPUT_DIR"] = str(self.tmp / "output")
        os.environ["CIVIL_TOKEN"] = "t2-bounded-token"
        self.stack.enter_context(patch.object(storage, "storage_mode", return_value="json"))
        self.client = TestClient(gateway.app)
        self.root = self.tmp / "output" / "web-link"

    def upload(self, tender, session: str = "bounded"):
        return self.client.post("/api/tender/link", headers={"Authorization": "Bearer t2-bounded-token"},
                                files={"tender": tender, "panel_list": ("facade_panels.xlsx", PANELS)},
                                data={"session_id": session})

    def jobs(self) -> list:
        return sorted(p for p in self.root.glob("*/*") if p.is_dir()) if self.root.is_dir() else []

    def test_tender_text_over_the_limit_is_refused_with_413(self) -> None:
        from gateway import web_link

        self.assertEqual(400 * 1024, web_link.max_tender_text_bytes())
        big = many_clauses(4500).encode("utf-8")                # 400+ kB of text, under the 10 MB file limit
        self.assertGreater(len(big), 400 * 1024)
        r = self.upload(("itt.md", big))
        self.assertEqual(413, r.status_code, r.text)
        self.assertEqual("too_large", r.json()["error_code"])
        self.assertIn("at most 400 kB", r.json()["detail"])
        self.assertEqual([], self.jobs(), "a refused .md took a job folder")
        # a Word file's text is known only once it is read: refused by the tool's own check, still 413
        os.environ["CIVIL_LINK_MAX_TENDER_TEXT_KB"] = "2"       # the demo ITT has 4 kB of text
        docx = docx_bytes(ITT)
        r = self.upload(("itt.docx", docx), session="bounded-docx")
        self.assertEqual(413, r.status_code, r.text)
        self.assertIn("kB of text", r.json()["detail"])
        del os.environ["CIVIL_LINK_MAX_TENDER_TEXT_KB"]
        r = self.upload(("itt.docx", docx), session="bounded-docx")
        self.assertEqual(200, r.status_code, r.text)
        # Text-size handling still succeeds, but the unchanged facade source
        # contains handling constraints that automatic boxing cannot establish.
        self.assertEqual({"covered": 0, "partial": 0, "gap": 0, "human_required": 7}, r.json()["counts"])
        self.assertIsNone(r.json()["plan"])

    def test_the_probe_upload_answers_with_a_row_for_a_person(self) -> None:
        r = self.upload(("itt.md", dense_tender(1500).encode("utf-8")))
        self.assertEqual(200, r.status_code, r.text)
        body = r.json()
        self.assertEqual(0, body["counts"]["covered"])
        rows = [s for s in body["statements"] if DENSE_ROW in s["text"]]
        self.assertEqual(1, len(rows))
        self.assertEqual("human_required", rows[0]["status"])
        self.assertIn("3,000 mass figures in one sentence", rows[0]["figure"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
