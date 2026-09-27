"""Offline file round trips and boundary tests for the document worker."""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from uuid import uuid4
from xml.etree import ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packing_assistant.documents import handle
from packing_assistant.word_export import markdown_docx_bytes


def parts(data):
    with ZipFile(BytesIO(data)) as z:
        return {n: z.read(n) for n in z.namelist()}


def word_fixture():
    raw = markdown_docx_bytes("# Report\n\nOriginal paragraph\n\nOutside text\n\n| Item | Value |\n| --- | --- |\n| Beam | Old |\n")
    entries = parts(raw)
    # Compatibility prefixes occur in real Word files even when no w14 element
    # is present. The editor must not silently drop their namespace binding.
    root = ET.fromstring(entries["word/document.xml"])
    root.set("xmlns:w14", "http://schemas.microsoft.com/office/word/2010/wordml")
    root.set("{http://schemas.openxmlformats.org/markup-compatibility/2006}Ignorable", "w14")
    entries["word/document.xml"] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    out = BytesIO()
    with ZipFile(out, "w") as z:
        for name, value in entries.items():
            z.writestr(name, value)
    return out.getvalue()


def pdf_fixture():
    from pypdf import PdfWriter
    from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, TextStringObject, DecodedStreamObject
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")}))
    for label in ("Original page one", "Original page two"):
        page = writer.add_blank_page(width=300, height=400)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/Helv"): font})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /Helv 12 Tf 20 300 Td ({label}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    field = DictionaryObject({NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"),
                              NameObject("/FT"): NameObject("/Tx"), NameObject("/T"): TextStringObject("project"),
                              NameObject("/V"): TextStringObject("Old project"), NameObject("/Ff"): NumberObject(0),
                              NameObject("/Rect"): ArrayObject([NumberObject(v) for v in (20, 100, 180, 120)]),
                              NameObject("/DA"): TextStringObject("/Helv 10 Tf 0 g"), NameObject("/P"): writer.pages[0].indirect_reference})
    field_ref = writer._add_object(field)
    writer.pages[0][NameObject("/Annots")] = ArrayObject([field_ref])
    writer.root_object[NameObject("/AcroForm")] = writer._add_object(DictionaryObject({NameObject("/Fields"): ArrayObject([field_ref]),
                NameObject("/DA"): TextStringObject("/Helv 10 Tf 0 g"),
                NameObject("/DR"): DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/Helv"): font})})}))
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


class DocumentWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="civil-documents-")
        self.workspace = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def request(self, operation, source="report.docx", arguments=None, **extra):
        req = {"version": 1, "call_id": uuid4().hex, "operation": operation, "workspace": str(self.workspace),
               "source": source, "arguments": arguments or {}}
        if operation in ("preview", "apply") and (self.workspace / source).exists():
            req["expected_sha256"] = sha256((self.workspace / source).read_bytes()).hexdigest()
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

    def word_patch(self):
        self.original = word_fixture()
        (self.workspace / "report.docx").write_bytes(self.original)
        detail = self.good(self.request("inspect"))
        identifier = next(n["id"] for n in detail["nodes"] if n["text"] == "Original paragraph")
        return [{"op": "replace_paragraph", "paragraph_id": identifier,
                 "expected_text": "Original paragraph", "text": "Model-proposed review text", "evidence": [{"source_id": "fixture", "quote": "original"}]},
                {"op": "replace_cell", "table": 0, "row": 1, "column": 1, "expected_text": "Old", "text": "Reviewed"}]

    def test_docx_preview_apply_preserves_source_and_unmodified_package_parts(self):
        patches = self.word_patch()
        preview = self.good(self.request("preview", arguments={"patches": patches}))
        self.assertEqual(len(preview["changes"]), 2)
        self.assertFalse(preview["writes"])
        self.assertFalse((self.workspace / ".civil-buddy").exists())
        output = self.good(self.request("apply", arguments={"patches": patches}))
        data = Path(output["output_path"]).read_bytes()
        self.assertEqual((self.workspace / "report.docx").read_bytes(), self.original)
        before, after = parts(self.original), parts(data)
        self.assertEqual(before.keys(), after.keys())
        self.assertEqual([k for k in before if before[k] != after[k]], ["word/document.xml"])
        self.assertIn(b"xmlns:w14=", after["word/document.xml"])
        self.assertIn(b"Model-proposed review text", after["word/document.xml"])
        self.assertIn(b"Outside text", after["word/document.xml"])
        self.assertEqual(output["validation"]["reopened"], "pass")
        self.assertEqual(output["validation"]["render"], "not_checked")

    def test_word_revision_and_expected_text_conflicts_publish_nothing(self):
        patches = self.word_patch()
        self.error(self.request("apply", arguments={"patches": patches}, expected_sha256="0"*64), "conflict")
        patches[0]["expected_text"] = "stale"
        self.error(self.request("apply", arguments={"patches": patches}), "conflict")
        self.assertFalse((self.workspace / ".civil-buddy").exists())

    def test_apply_is_idempotent_and_call_id_cannot_change_request(self):
        req = self.request("apply", arguments={"patches": self.word_patch()})
        req["expected_sha256"] = sha256(self.original).hexdigest()
        first = self.good(req)
        second = self.good(req)
        self.assertTrue(second["replayed"])
        self.assertEqual(first["output_path"], second["output_path"])
        req["arguments"]["patches"][0]["text"] = "Another proposal"
        self.error(req, "conflict")
        self.assertEqual(len(list(self.workspace.rglob("*-draft-*.docx"))), 1)

    def test_call_replay_checks_produced_artifact(self):
        patches = self.word_patch()
        req = self.request("apply", arguments={"patches": patches})
        first = self.good(req)
        Path(first["output_path"]).write_bytes(b"changed")
        self.error(req, "conflict")

    def test_busy_call_and_dead_worker_lock_recovery(self):
        patches = self.word_patch()
        req = self.request("apply", arguments={"patches": patches})
        output = self.workspace / ".civil-buddy" / "out" / "documents"
        output.mkdir(parents=True)
        lock = output / ("call-" + req["call_id"] + ".lock")
        lock.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
        self.error(req, "busy")
        lock.write_text(json.dumps({"pid": 2147483647}), encoding="utf-8")
        self.good(req)
        self.assertFalse(lock.exists())

    def test_workspace_escape_and_output_escape_are_rejected(self):
        patches = self.word_patch()
        self.error(self.request("inspect", source="../outside.docx"), "path_denied")
        self.error(self.request("apply", arguments={"patches": patches}, output_dir="../outside"), "path_denied")

    def test_source_symlink_escape_is_rejected_when_symlinks_available(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "secret.docx"
            target.write_bytes(word_fixture())
            try:
                (self.workspace / "link.docx").symlink_to(target)
            except OSError:
                self.skipTest("Creating symlinks is unavailable on this host")
            self.error(self.request("inspect", source="link.docx"), "path_denied")

    def workbook(self):
        from openpyxl import Workbook
        from openpyxl.styles import Font
        from openpyxl.chart import BarChart, Reference
        book = Workbook()
        sheet = book.active
        sheet.title = "Quantities"
        sheet["A1"], sheet["A2"], sheet["A3"] = "Quantity", 3, "Outside value"
        sheet["B2"] = "=SUM(A2,2)"
        sheet["B2"].font = Font(bold=True, color="FF0000")
        chart = BarChart()
        chart.add_data(Reference(sheet, min_col=1, min_row=1, max_row=2), titles_from_data=True)
        sheet.add_chart(chart, "F2")
        book.create_sheet("Other")["A1"] = "Untouched"
        source = self.workspace / "data.xlsx"
        book.save(source)
        return source

    def test_xlsx_range_types_formula_cache_and_original_objects_preserved(self):
        from openpyxl import load_workbook
        source = self.workbook()
        original = source.read_bytes()
        patches = [{"op": "set_cell", "sheet": "Quantities", "cell": "A2", "expected": {"type": "number", "value": 3}, "value": {"type": "number", "value": 7}},
                   {"op": "set_range", "sheet": "Quantities", "range": "C2:D2", "expected": [[{"type": "blank", "value": None}, {"type": "blank", "value": None}]],
                    "values": [[{"type": "text", "value": "=1+1"}, {"type": "formula", "value": "=SUM(A2,4)"}]]}]
        output = self.good(self.request("apply", "data.xlsx", {"patches": patches}))
        self.assertEqual(source.read_bytes(), original)
        book = load_workbook(output["output_path"], data_only=False)
        self.assertEqual(book["Quantities"]["A2"].value, 7)
        self.assertEqual(book["Quantities"]["C2"].value, "=1+1")
        self.assertEqual(book["Quantities"]["C2"].data_type, "s")
        self.assertEqual(book["Quantities"]["D2"].data_type, "f")
        self.assertEqual(book["Quantities"]["B2"].value, "=SUM(A2,2)")
        self.assertTrue(book["Quantities"]["B2"].font.bold)
        self.assertEqual(book["Other"]["A1"].value, "Untouched")
        self.assertEqual(len(book["Quantities"]._charts), 1)
        book.close()
        before, after = parts(original), parts(Path(output["output_path"]).read_bytes())
        for name in before:
            if name not in ("xl/worksheets/sheet1.xml", "xl/workbook.xml"):
                self.assertEqual(before[name], after[name], name)
        self.assertEqual(output["validation"]["recalculation"], "not_recalculated")
        read = self.good(self.request("read", output["output_path"], {"sheet": "Quantities", "range": "C2:D2"}))
        self.assertEqual(read["rows"][0][1]["type"], "formula")
        self.assertIsNone(read["rows"][0][1]["cached_value"])

    def test_xlsx_denies_external_formula_and_version_stale_value(self):
        self.workbook()
        p = {"op": "set_cell", "sheet": "Quantities", "cell": "A2", "expected": {"type": "number", "value": 3}, "value": {"type": "formula", "value": '=WEBSERVICE("https://example.com")'}}
        self.error(self.request("apply", "data.xlsx", {"patches": [p]}), "unsupported")
        p["value"] = {"type": "number", "value": 7}
        p["expected"]["value"] = 999
        self.error(self.request("apply", "data.xlsx", {"patches": [p]}), "conflict")

    def test_xlsx_merged_target_denied_other_cells_allowed(self):
        from openpyxl import load_workbook
        source = self.workbook()
        book = load_workbook(source)
        book.active.merge_cells("C4:D4")
        book.save(source)
        book.close()
        p = {"op": "set_cell", "sheet": "Quantities", "cell": "C4", "expected": {"type": "blank", "value": None}, "value": {"type": "text", "value": "new"}}
        self.error(self.request("apply", "data.xlsx", {"patches": [p]}), "unsupported")

    def test_pdf_read_annotation_form_fill_page_order_and_reopen(self):
        from pypdf import PdfReader
        source = self.workspace / "drawing.pdf"
        original = pdf_fixture()
        source.write_bytes(original)
        read = self.good(self.request("read", "drawing.pdf", {"pages": [1]}))
        self.assertIn("Original page one", read["pages"][0]["text"])
        patches = [{"op": "annotate", "page": 1, "rect": [20, 200, 60, 220], "text": "Review beam detail"},
                   {"op": "fill_fields", "fields": {"project": "New project"}},
                   {"op": "reorder_pages", "pages": [2, 1]}]
        preview = self.good(self.request("preview", "drawing.pdf", {"patches": patches}))
        self.assertEqual(len(preview["changes"]), 3)
        output = self.good(self.request("apply", "drawing.pdf", {"patches": patches}))
        self.assertEqual(source.read_bytes(), original)
        reader = PdfReader(output["output_path"])
        self.assertIn("Original page two", reader.pages[0].extract_text())
        self.assertIn("Original page one", reader.pages[1].extract_text())
        self.assertEqual(reader.get_fields()["project"]["/V"], "New project")
        contents = [str(a.get_object().get("/Contents", "")) for a in reader.pages[1]["/Annots"]]
        self.assertIn("Review beam detail", contents)
        self.assertEqual(output["validation"]["visual"], "not_checked")

    def test_pdf_unsupported_body_edit_and_bad_page_order(self):
        (self.workspace / "drawing.pdf").write_bytes(pdf_fixture())
        self.error(self.request("apply", "drawing.pdf", {"patches": [{"op": "replace_text", "text": "fake"}]}), "unsupported")
        self.error(self.request("apply", "drawing.pdf", {"patches": [{"op": "reorder_pages", "pages": [1, 1]}]}), "invalid_patch")

    def test_worker_protocol_real_subprocess_has_one_json_response(self):
        (self.workspace / "report.docx").write_bytes(word_fixture())
        request = self.request("inspect")
        run = subprocess.run([sys.executable, "-m", "packing_assistant.documents.worker"], input=(json.dumps(request)+"\n").encode(),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr.decode(errors="replace"))
        self.assertEqual(len(run.stdout.splitlines()), 1)
        response = json.loads(run.stdout)
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["call_id"], request["call_id"])

    def test_capabilities_are_honest_and_invalid_ops_fail_closed(self):
        result = self.good({"version": 1, "call_id": "capabilities", "operation": "capabilities"})
        self.assertFalse(result["capabilities"]["pdf"]["body_edit"])
        self.assertFalse(result["capabilities"]["xlsx"]["recalculate"])
        self.error({"version": 1, "call_id": "x", "operation": "shell"}, "unsupported")
        self.error({"version": 2, "call_id": "x", "operation": "inspect"}, "invalid_request")
        self.error({"version": 1, "call_id": "x", "operation": "capabilities", "extra": float("nan")}, "invalid_request")


if __name__ == "__main__":
    unittest.main()
