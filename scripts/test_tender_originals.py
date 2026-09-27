#!/usr/bin/env python3
"""Offline original-upload reading and unclassified front-table preservation."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
from test_tender_document import TENDER  # noqa: E402
from packing_assistant.tools.tender_parse import workbench_bid_extract  # noqa: E402

SCRIPT = ROOT / "workbench/scripts/run_tender_extract.py"
spec = importlib.util.spec_from_file_location("tender_original_reader", SCRIPT)
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


class Originals(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="civil-tender-originals-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.uploads = self.root / "uploads"
        self.uploads.mkdir()

    def payload(self, path, name="招标文件.txt"):
        return {"upload_dir": str(self.uploads), "files": [{"path": str(path), "name": name}]}

    def run_sidecar(self, payload):
        env = {**os.environ, "PACKING_AGENT_ROOT": str(ROOT), "PYTHONUTF8": "1"}
        result = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(payload, ensure_ascii=False).encode(),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=40, env=env)
        return result.returncode, json.loads(result.stdout)

    def test_original_text_beyond_prompt_prefix_is_read_and_unchanged(self):
        text = TENDER.replace("第三章 评标办法", ("施工资料仅供记录。\n" * 3500) + "第三章 评标办法")
        path = self.uploads / "a1b2c3.bin"
        path.write_bytes(text.encode("utf-8"))
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        code, result = self.run_sidecar(self.payload(path))
        self.assertEqual(code, 0, result)
        self.assertTrue(result["ok"])
        self.assertEqual(result["files_read"], ["招标文件.txt"])
        self.assertIn("工艺调试方案", result["extract_table_markdown"])
        self.assertIn("300日历天", result["extract_table_markdown"])
        self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_unknown_front_rows_keep_whole_text_and_source(self):
        long_value = "样板资料应按现场档案架顺序放置。" * 25 + "末尾标识保留"
        text = TENDER.replace("| 10 | 需要补充的其他内容", f"| 9.9 | 档案架索引 | {long_value} |\n| 9.8 | 空白项目 | / |\n| 10 | 需要补充的其他内容")
        result = workbench_bid_extract(text, project_name="synthetic")
        table = result["extract_table_markdown"]
        self.assertIn("10B 前附表未归类各行", table)
        self.assertIn("末尾标识保留", table)
        self.assertIn("第二章 前附表 9.9", table)
        self.assertNotIn("前附表 空白项目", table)

    def test_submission_envelope_keeps_original_wording_and_locator(self):
        from packing_assistant.tools.tender_parse import parse_tender_text
        wording = "Two Envelope: technical and price separately"
        result = workbench_bid_extract(f"INVITATION TO TENDER\nQuality 40%\nPrice 60%\n{wording}", project_name="synthetic")
        self.assertIn(wording, result["extract_table_markdown"])
        self.assertEqual(result["handoff"]["envelope_sources"], [{"text": wording, "locator": "L4"}])
        # A bidder's proposed submission method is not the tender's requirement.
        mixed = parse_tender_text("招标要求工期60日历天；我们拟用双信封递交投标文件。")
        self.assertNotIn("envelope_sources", mixed["handoff"])
        no_scheme = workbench_bid_extract("招标要求工期60日历天。", project_name="synthetic")
        self.assertNotIn("Two Envelope", no_scheme["extract_table_markdown"])

    def test_outside_path_corrupt_and_empty_originals_fail_without_table(self):
        path = self.root / "outside.bin"
        path.write_text(TENDER, encoding="utf-8")
        code, result = self.run_sidecar(self.payload(path))
        self.assertNotEqual(code, 0)
        self.assertIn("超出", result["error"])
        self.assertNotIn("extract_table_markdown", result)
        inside = self.uploads / "aabb.bin"
        for name, data in (("tender.docx", b"not a zip"), ("tender.pdf", b"not pdf"), ("tender.txt", b"")):
            inside.write_bytes(data)
            code, result = self.run_sidecar(self.payload(inside, name))
            self.assertNotEqual(code, 0, name)
            self.assertFalse(result["ok"])
            self.assertNotIn("extract_table_markdown", result)

    def test_original_docx_is_read_and_unchanged(self):
        from html import escape
        from zipfile import ZipFile
        path = self.uploads / "doc.bin"
        paragraphs = "".join(f"<w:p><w:r><w:t>{escape(line)}</w:t></w:r></w:p>" for line in TENDER.splitlines())
        with ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                             f"<w:body>{paragraphs}</w:body></w:document>")
        before = path.read_bytes()
        bodies, names, previews = reader.read_originals(self.payload(path, "招标文件.docx"))
        self.assertEqual(names, ["招标文件.docx"])
        self.assertEqual(previews, [])
        self.assertIn("300日历天", bodies[0])
        self.assertEqual(before, path.read_bytes())

    def test_mixed_original_and_spreadsheet_keeps_visible_bounded_preview(self):
        original = self.uploads / "tender.bin"
        original.write_text(TENDER, encoding="utf-8")
        sheet = self.uploads / "sheet.bin"
        sheet.write_bytes(b"synthetic uploaded workbook bytes")
        cached = self.uploads / "sheet.txt"
        cached.write_text("BCA workhead CW02\n" + "original spreadsheet cell\n" * 1000, encoding="utf-8")
        payload = self.payload(original)
        payload["files"].append({"path": str(sheet), "name": "requirements.xlsx"})
        code, result = self.run_sidecar(payload)
        self.assertEqual(code, 0, result)
        self.assertEqual(result["files_read"], ["招标文件.txt"])
        self.assertEqual(result["files_previewed"], ["requirements.xlsx"])
        self.assertIn("CW02", result["extract_table_markdown"])
        self.assertIn("并非全文读取：requirements.xlsx", result["extract_table_markdown"])
        self.assertEqual(sheet.read_bytes(), b"synthetic uploaded workbook bytes")

    @unittest.skipIf(os.name == "nt", "Windows symlink privilege is not assumed")
    def test_linked_spreadsheet_preview_is_refused(self):
        original = self.uploads / "sheet.bin"
        original.write_bytes(b"synthetic uploaded workbook")
        outside = self.root / "private.txt"
        outside.write_text(TENDER, encoding="utf-8")
        original.with_suffix(".txt").symlink_to(outside)
        with self.assertRaises(ValueError):
            reader.read_originals(self.payload(original, "requirements.xlsx"))

    @unittest.skipIf(os.name == "nt", "Windows symlink privilege is not assumed")
    def test_symlink_original_is_refused(self):
        outside = self.root / "outside.bin"
        outside.write_text(TENDER, encoding="utf-8")
        link = self.uploads / "link.bin"
        link.symlink_to(outside)
        with self.assertRaises(ValueError):
            reader.read_originals(self.payload(link))


if __name__ == "__main__":
    unittest.main(verbosity=2)
