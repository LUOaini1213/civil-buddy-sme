"""Offline source retrieval, isolation, revision and exact-citation tests."""
from __future__ import annotations

from copy import deepcopy
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packing_assistant.retrieval import handle
from scripts.test_document_worker import word_fixture, pdf_fixture


class SourceRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="civil-retrieval-")
        self.root = Path(self.temp.name).resolve()
        (self.root / "requirements.txt").write_text("工程项目：示例。\n设计变更要求：钢梁数量为 12 根，必须复核连接节点。\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def request(self, operation="search", sources=None, **extra):
        req = {"version": 1, "call_id": uuid4().hex, "operation": operation, "workspace": str(self.root),
               "sources": sources or ["requirements.txt"]}
        if operation == "search":
            req["query"] = "设计变更"
        req.update(extra)
        return req

    def good(self, request):
        response = handle(request)
        self.assertTrue(response["ok"], response)
        return response["result"]

    def error(self, request, code):
        response = handle(request)
        self.assertFalse(response["ok"], response)
        self.assertEqual(response["error"]["code"], code, response)

    def test_chinese_bigrams_bm25_exact_quote_and_no_network_provider(self):
        response = self.good(self.request())
        self.assertEqual(len(response["hits"]), 1)
        hit = response["hits"][0]
        self.assertIn("钢梁数量为 12 根", hit["quote"])
        self.assertEqual(hit["source_sha256"], sha256((self.root / "requirements.txt").read_bytes()).hexdigest())
        self.assertEqual(hit["locator"]["line_start"], 1)
        verified = self.good(self.request("verify", references=[hit]))
        self.assertTrue(verified["valid"])
        self.assertEqual(verified["engineering_truth"], "not_verified")
        self.assertFalse(response["embeddings"])
        self.assertTrue((self.root / ".civil-buddy/out/retrieval/sources.sqlite3").exists())

    def test_selected_files_filter_before_candidate_limit(self):
        (self.root / "private.txt").write_text("设计变更 秘密投标价格", encoding="utf-8")
        self.good(self.request("index", ["requirements.txt", "private.txt"]))
        public = self.good(self.request(sources=["requirements.txt"]))
        self.assertTrue(all(h["source"] == "requirements.txt" for h in public["hits"]))
        private = self.good(self.request(sources=["private.txt"], query="秘密投标"))["hits"][0]
        denied = self.good(self.request("verify", references=[private]))
        self.assertFalse(denied["valid"])
        self.assertEqual(denied["references"][0]["reason"], "source_not_allowed")

    def test_version_change_rebuilds_and_old_citations_are_invalid(self):
        old = self.good(self.request())["hits"][0]
        path = self.root / "requirements.txt"
        path.write_text("材料报审：新供应商文件。", encoding="utf-8")
        changed = self.good(self.request(query="材料报审"))
        self.assertEqual(changed["index"]["updated"], 1)
        self.assertIn("新供应商", changed["hits"][0]["quote"])
        self.assertEqual(self.good(self.request(query="设计变更"))["hits"], [])
        checked = self.good(self.request("verify", references=[old]))
        self.assertEqual(checked["references"][0]["reason"], "version_mismatch")
        self.error(self.request(expected_versions={"requirements.txt": old["source_sha256"]}), "conflict")

    def test_unchanged_sources_do_not_reindex(self):
        self.good(self.request("index"))
        again = self.good(self.request("index"))
        self.assertEqual((again["index"]["updated"], again["index"]["unchanged"]), (0, 1))

    def test_quote_locator_and_chunk_id_cannot_be_fabricated(self):
        hit = self.good(self.request())["hits"][0]
        cases = []
        wrong = deepcopy(hit)
        wrong["quote"] = "钢梁数量为 99 根"
        cases.append(wrong)
        wrong = deepcopy(hit)
        wrong["locator"]["start"] = 5
        cases.append(wrong)
        wrong = deepcopy(hit)
        wrong["chunk_id"] = "0"*64
        cases.append(wrong)
        result = self.good(self.request("verify", references=cases))
        self.assertFalse(result["valid"])
        self.assertEqual([r["reason"] for r in result["references"]], ["quote_mismatch", "locator_mismatch", "chunk_mismatch"])

    def test_verification_rereads_source_not_mutable_cache(self):
        hit = self.good(self.request())["hits"][0]
        db = self.root / ".civil-buddy/out/retrieval/sources.sqlite3"
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute("UPDATE chunks SET quote=?", ("伪造原文 99 根",))
        fabricated = {**hit, "quote": "伪造原文 99 根"}
        result = self.good(self.request("verify", references=[fabricated, hit]))
        self.assertEqual([r["status"] for r in result["references"]], ["invalid", "valid"])

    def test_docx_paragraph_and_cell_locators(self):
        (self.root / "report.docx").write_bytes(word_fixture())
        paragraph = self.good(self.request(sources=["report.docx"], query="Original paragraph"))["hits"]
        self.assertTrue(any(h["locator"]["kind"] == "docx_paragraph" for h in paragraph))
        cells = self.good(self.request(sources=["report.docx"], query="Beam", limit=30))["hits"]
        cell = next(h for h in cells if h["locator"]["kind"] == "docx_cell")
        self.assertEqual((cell["locator"]["table"], cell["locator"]["row"], cell["locator"]["column"]), (0, 1, 0))
        self.assertTrue(self.good(self.request("verify", ["report.docx"], references=[cell]))["valid"])

    def test_xlsx_row_cell_and_formula_references(self):
        from openpyxl import Workbook
        book = Workbook()
        book.active.title = "数量"
        book.active.append(["构件", "数量"])
        book.active.append(["钢梁", 12])
        book.active["C2"] = "=B2*2"
        book.save(self.root / "quantities.xlsx")
        result = self.good(self.request(sources=["quantities.xlsx"], query="钢梁", limit=30))
        kinds = {h["locator"]["kind"] for h in result["hits"]}
        self.assertEqual(kinds, {"xlsx_row", "xlsx_cell"})
        row = next(h for h in result["hits"] if h["locator"]["kind"] == "xlsx_row")
        self.assertIn("B2: 12", row["quote"])
        self.assertIn("C2: =B2*2", row["quote"])
        formula = self.good(self.request(sources=["quantities.xlsx"], query="B2", limit=30))["hits"]
        cell = next(h for h in formula if h["locator"]["kind"] == "xlsx_cell" and h["locator"]["cell"] == "C2")
        self.assertEqual(cell["locator"]["value_type"], "formula")
        self.assertTrue(self.good(self.request("verify", ["quantities.xlsx"], references=[row, cell]))["valid"])

    def test_pdf_page_location_and_empty_text_source_status(self):
        (self.root / "requirements.pdf").write_bytes(pdf_fixture())
        result = self.good(self.request(sources=["requirements.pdf"], query="Original page two"))
        hit = result["hits"][0]
        self.assertEqual(hit["locator"]["page"], 2)
        self.assertTrue(self.good(self.request("verify", ["requirements.pdf"], references=[hit]))["valid"])
        from pypdf import PdfWriter
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        with (self.root / "scan.pdf").open("wb") as stream:
            writer.write(stream)
        empty = self.good(self.request(sources=["scan.pdf"]))
        self.assertEqual(empty["hits"], [])
        self.assertEqual(empty["index"]["empty_text_sources"], ["scan.pdf"])
        self.assertEqual(empty["index"]["ocr"], "not_performed")

    def test_workspace_escape_unselected_versions_and_deleted_file(self):
        self.error(self.request(sources=["../outside.txt"]), "path_denied")
        self.error(self.request(expected_versions={"unselected.txt": "0"*64}), "path_denied")
        self.good(self.request())
        (self.root / "requirements.txt").unlink()
        self.error(self.request(), "not_found")

    def test_sql_like_query_does_not_change_index(self):
        self.good(self.request())
        self.good(self.request(query="' OR 1=1; DROP TABLE sources; --"))
        self.assertTrue(self.good(self.request())["hits"])

    def test_workspace_indices_are_independent(self):
        hit = self.good(self.request())["hits"][0]
        with tempfile.TemporaryDirectory() as other:
            second = Path(other)
            (second / "requirements.txt").write_text("其他项目", encoding="utf-8")
            result = self.good(self.request("verify", workspace=str(second.resolve()), references=[hit]))
            self.assertEqual(result["references"][0]["reason"], "version_mismatch")

    def test_fixed_worker_json_protocol(self):
        req = self.request()
        run = subprocess.run([sys.executable, "-m", "packing_assistant.retrieval.worker"], input=(json.dumps(req)+"\n").encode(),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr.decode(errors="replace"))
        self.assertEqual(len(run.stdout.splitlines()), 1)
        response = json.loads(run.stdout)
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["call_id"], req["call_id"])


if __name__ == "__main__":
    unittest.main()
