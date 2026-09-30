"""Real file fixtures exercise hash-bound delivery checks without Office or a model."""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
import sys
import tempfile
import unittest
from uuid import uuid4
from xml.etree import ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packing_assistant.documents import handle
from packing_assistant.documents.ooxml import S
from packing_assistant.word_export import markdown_docx_bytes


def repack(data, edit):
    with ZipFile(BytesIO(data)) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    edit(members)
    out = BytesIO()
    with ZipFile(out, "w") as archive:
        for name, value in members.items():
            archive.writestr(name, value)
    return out.getvalue()


def workbook(*, cached="5", formula="A1*2", error=False):
    from openpyxl import Workbook
    book = Workbook()
    sheet = book.active
    sheet.title = "数量 Summary"
    sheet["A1"] = 3
    sheet["B1"] = "=" + formula  # Deliberately stale cache 5, current value would be 6.
    if error:
        sheet["C1"] = "#DIV/0!"
    book.calculation.calcMode = "manual"
    output = BytesIO()
    book.save(output)

    def edit(parts):
        root = ET.fromstring(parts["xl/worksheets/sheet1.xml"])
        cell = next(c for c in root.iter(f"{{{S}}}c") if c.get("r") == "B1")
        value = cell.find(f"{{{S}}}v")
        if cached is None:
            if value is not None:
                cell.remove(value)
        else:
            if value is None:
                value = ET.SubElement(cell, f"{{{S}}}v")
            value.text = cached
        parts["xl/worksheets/sheet1.xml"] = ET.tostring(root)
    return repack(output.getvalue(), edit)


def pdf(*, javascript=False, attachment=False):
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    if javascript:
        writer.add_js("app.alert('Do not execute');")
    if attachment:
        writer.add_attachment("note.txt", b"embedded content")
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="civil-readiness-")
        self.workspace = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def request(self, name, data, **extra):
        target = self.workspace / name
        target.write_bytes(data)
        return {"version": 1, "call_id": uuid4().hex, "operation": "inspect_readiness",
                "workspace": str(self.workspace), "source": name,
                "expected_sha256": sha256(data).hexdigest(), **extra}

    def good(self, request):
        before = (self.workspace / request["source"]).read_bytes()
        response = handle(request)
        self.assertTrue(response["ok"], response)
        result = response["result"]
        self.assertEqual(result["source_sha256"], sha256(before).hexdigest())
        self.assertEqual((self.workspace / request["source"]).read_bytes(), before)
        self.assertFalse(result["writes"])
        self.assertFalse((self.workspace / ".civil-buddy").exists())
        self.assertFalse(result["readiness"]["automatic_acceptance"])
        self.assertEqual(result["validation"]["render"], "not_checked")
        return result["readiness"]

    def test_docx_exact_copy_checks_do_not_claim_render_or_acceptance(self):
        raw = markdown_docx_bytes("# Engineering draft\n\nReview sources and figures.\n\n| Item | Quantity |\n| --- | --- |\n| Beam | 3 |")
        report = self.good(self.request("draft.docx", raw))
        self.assertEqual(report["status"], "review_required")
        self.assertFalse(report["preview"]["eligible"])
        self.assertEqual(report["format_details"]["renderer"], "not_run")
        self.assertEqual(report["format_details"]["table_count"], 1)

    def test_expected_hash_is_mandatory_and_changed_bytes_are_rejected(self):
        request = self.request("draft.docx", markdown_docx_bytes("Original"))
        without_hash = dict(request)
        del without_hash["expected_sha256"]
        self.assertEqual(handle(without_hash)["error"]["code"], "invalid_request")
        (self.workspace / "draft.docx").write_bytes(markdown_docx_bytes("Changed"))
        self.assertEqual(handle(request)["error"]["code"], "conflict")

    def test_entities_in_unedited_office_part_are_rejected(self):
        raw = markdown_docx_bytes("Original")
        for content in (b'<!DOCTYPE x [<!ENTITY local SYSTEM "file:///secret">]><x>&local;</x>',
                        '<!DOCTYPE x [<!ENTITY x "expanded">]><x>&x;</x>'.encode("utf-16")):
            with self.subTest(content=content[:20]):
                edited = repack(raw, lambda parts: parts.update({"word/header1.xml": content}))
                result = handle(self.request("draft.docx", edited))
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], "invalid_document")

    def test_macro_embedding_and_external_relationship_flags(self):
        def edit(parts):
            parts["word/vbaProject.bin"] = b"not executed"
            parts["word/embeddings/object.bin"] = b"not executed"
            parts["word/_rels/header1.xml.rels"] = b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Type="link" TargetMode="External" Target="https://example.invalid/private"/></Relationships>'
        report = self.good(self.request("draft.docx", repack(markdown_docx_bytes("Draft"), edit)))
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(report["format_details"]["macro_content"])
        self.assertEqual(report["format_details"]["embedded_object_parts"], 1)
        self.assertEqual(report["format_details"]["external_relationship_count"], 1)

    def test_stale_formula_cache_is_never_reported_as_recalculated(self):
        report = self.good(self.request("quantities.xlsx", workbook()))
        detail = report["format_details"]
        self.assertEqual(detail["formula_cached_count"], 1)
        self.assertEqual(detail["formula_missing_cache_count"], 0)
        self.assertEqual(detail["cached_values_status"], "stored_unverified")
        self.assertEqual(detail["recalculation"], "not_performed")
        self.assertEqual(detail["calculation_properties"]["calcMode"], "manual")
        self.assertEqual(report["status"], "review_required")

    def test_missing_invalid_and_error_caches_block_delivery(self):
        for cached, error, expected in ((None, False, "formula_missing_cache_count"),
                                       ("NaN", False, "formula_invalid_cache_count"),
                                       ("5", True, "error_cell_count")):
            with self.subTest(cached=cached, error=error):
                report = self.good(self.request("quantities.xlsx", workbook(cached=cached, error=error)))
                self.assertEqual(report["status"], "blocked")
                self.assertEqual(report["format_details"][expected], 1)
                self.assertFalse(report["format_details"]["issue_cells_truncated"])

    def test_external_formula_detected_without_refreshing(self):
        report = self.good(self.request("quantities.xlsx", workbook(formula="'[Other.xlsx]Sheet1'!A1")))
        self.assertEqual(report["format_details"]["external_formula_count"], 1)
        self.assertTrue(any(c["id"] == "external_data" for c in report["checks"]))

    def test_no_formula_workbook_does_not_require_recalculation(self):
        from openpyxl import Workbook
        book = Workbook()
        book.active["A1"] = "Material"
        book.active["B1"] = 12
        output = BytesIO()
        book.save(output)
        report = self.good(self.request("values.xlsx", output.getvalue()))
        self.assertEqual(report["format_details"]["formula_count"], 0)
        self.assertEqual(report["format_details"]["cached_values_status"], "not_applicable")
        self.assertEqual(next(c["status"] for c in report["checks"] if c["id"] == "recalculation"), "not_applicable")
        self.assertEqual(report["status"], "review_required")

    def test_empty_string_formula_cache_is_valid_but_unverified(self):
        def edit(parts):
            root = ET.fromstring(parts["xl/worksheets/sheet1.xml"])
            cell = next(c for c in root.iter(f"{{{S}}}c") if c.get("r") == "B1")
            cell.set("t", "str")
            parts["xl/worksheets/sheet1.xml"] = ET.tostring(root)
        raw = repack(workbook(cached="", formula='IF(A1=3,"","x")'), edit)
        report = self.good(self.request("quantities.xlsx", raw))
        self.assertEqual(report["format_details"]["formula_missing_cache_count"], 0)
        self.assertEqual(report["format_details"]["formula_cached_count"], 1)
        self.assertEqual(report["format_details"]["cached_values_status"], "stored_unverified")

    def test_generated_copy_requests_auto_calculation_but_keeps_stale_cache_warning(self):
        raw = workbook()
        request = self.request("quantities.xlsx", raw, operation="apply", arguments={"patches": [
            {"op": "set_cell", "sheet": "数量 Summary", "cell": "A1", "expected": {"type": "number", "value": 3},
             "value": {"type": "number", "value": 4}}]})
        response = handle(request)
        self.assertTrue(response["ok"], response)
        result = response["result"]
        self.assertEqual((self.workspace / "quantities.xlsx").read_bytes(), raw)
        self.assertEqual(result["validation"]["recalculation"], "not_recalculated")
        inspection = handle({"version": 1, "call_id": uuid4().hex, "operation": "inspect_readiness",
                             "workspace": str(self.workspace), "source": result["output_path"],
                             "expected_sha256": result["output_sha256"]})
        self.assertTrue(inspection["ok"], inspection)
        details = inspection["result"]["readiness"]["format_details"]
        self.assertEqual(details["calculation_properties"]["calcMode"], "auto")
        self.assertEqual(details["calculation_properties"]["fullCalcOnLoad"], "1")
        self.assertEqual(details["calculation_properties"]["forceFullCalc"], "1")
        self.assertEqual(details["cached_values_status"], "stored_unverified")

    def test_static_pdf_is_eligible_for_viewer_but_not_already_rendered(self):
        report = self.good(self.request("draft.pdf", pdf()))
        self.assertEqual(report["preview"], {"kind": "native_pdf", "eligible": True, "rendered": False})
        self.assertEqual(report["format_details"]["page_count"], 1)
        self.assertEqual(report["status"], "review_required")

    def test_pdf_javascript_and_embedded_files_disable_inline_preview(self):
        for kwargs in ({"javascript": True}, {"attachment": True}):
            with self.subTest(kwargs=kwargs):
                report = self.good(self.request("draft.pdf", pdf(**kwargs)))
                self.assertEqual(report["status"], "blocked")
                self.assertFalse(report["preview"]["eligible"])
                self.assertTrue(report["format_details"]["active_content_markers"])

    def test_pdf_encryption_is_rejected(self):
        from pypdf import PdfReader, PdfWriter
        writer = PdfWriter(clone_from=PdfReader(BytesIO(pdf())))
        writer.encrypt("private")
        output = BytesIO()
        writer.write(output)
        response = handle(self.request("draft.pdf", output.getvalue()))
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "unsupported")


if __name__ == "__main__":
    unittest.main()
