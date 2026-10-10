"""Offline synthetic intake evidence. These fixtures are not real shipment acceptance."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
import sys
from threading import Event
import unittest
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from packing_assistant.logistics import parse_document, validate_document, audit_document, summarize
from packing_assistant.logistics import intake, ocr
from packing_assistant.logistics.ledger import UNSPECIFIED
from packing_assistant.runtime.cancel import RunCancelled, scope


HEADERS = ["package_id", "material_id", "name", "package_count", "quantity", "units_per_package", "unit", "length_mm", "width_mm", "height_mm", "dimension_scope", "net_kg", "gross_kg", "weight_scope"]
VALUES = ["C001", "P001", "Synthetic panel", 2, 10, 5, "pcs", 1200, 800, 100, "package", 100, 110, "package"]


def csv_bytes(headers=HEADERS, rows=None):
    import csv
    from io import StringIO
    out = StringIO()
    writer = csv.writer(out)
    writer.writerow(headers)
    writer.writerows(rows or [VALUES])
    return out.getvalue().encode("utf-8-sig")


def pdf_bytes(pages=2, blank=False):
    from pypdf import PdfWriter
    from pypdf.generic import NameObject, DictionaryObject, DecodedStreamObject
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(700, 800)
        if blank:
            continue
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        commands = []
        for x in (30, 210, 360, 650):
            commands.append(f"{x} 650 m {x} 730 l S")
        for y in (650, 690, 730):
            commands.append(f"30 {y} m 650 {y} l S")
        for x, y, text in ((40, 705, "Name"), (220, 705, "Quantity"), (370, 705, "Total net weight (kg)"), (40, 665, "Synthetic panel"), (220, 665, "10"), (370, 665, "100")):
            text = text.replace("(", r"\(").replace(")", r"\)")
            commands.append(f"BT /F1 10 Tf {x} {y} Td ({text}) Tj ET")
        stream = DecodedStreamObject()
        stream.set_data("\n".join(commands).encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def business_pdf_bytes(titles=("PACKING LIST",), merged=True, cargo_name="Synthetic material A"):
    """Generate geometry and changed values; never embed a customer document."""
    from pypdf import PdfWriter
    from pypdf.generic import NameObject, DictionaryObject, DecodedStreamObject
    writer = PdfWriter()
    for title in titles:
        page = writer.add_blank_page(700, 800)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        commands = []
        xs = (30, 140, 300, 390, 470, 570, 670)
        for x in xs:
            commands.append(f"{x} 590 m {x} 710 l S")
        for y in (590, 620, 680, 710):
            commands.append(f"30 {y} m 670 {y} l S")
        if merged:
            for start, end in ((140, 300), (390, 470), (570, 670)):
                commands.append(f"{start} 650 m {end} 650 l S")
        else:
            commands.append("30 650 m 670 650 l S")
        def write(x, y, text, size=8):
            for offset, line in enumerate(text.splitlines()):
                escaped = line.replace("\\", "\\\\").replace("(", r"\(").replace(")", r"\)")
                commands.append(f"BT /F1 {size} Tf {x} {y-offset*10} Td ({escaped}) Tj ET")
        write(220, 760, title, 15)
        headers = ["Container No", "Description", "Cases", "Quantity", "Total gross\nweight (kg)", "Total net\nweight (kg)"]
        for x, header in zip(xs, headers):
            write(x + 3, 698, header)
        values = [
            ["SYNTH-01", cargo_name, "3 crates", "4 pcs", "90", "30"],
            ["" if merged else "SYNTH-02", "Synthetic material B", "" if merged else "2 crates", "5 pcs", "" if merged else "80", "40"],
            ["Total", "", "3" if merged else "5", "9", "90" if merged else "170", "70"],
        ]
        for y, row in zip((667, 637, 607), values):
            for x, value in zip(xs, row):
                write(x + 3, y, value)
        stream = DecodedStreamObject(); stream.set_data("\n".join(commands).encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    out = BytesIO(); writer.write(out); return out.getvalue()


class IntakeTests(unittest.TestCase):
    def test_digital_pdf_capability_missing_dependencies_and_native_loader_failure(self):
        source = b"%PDF-1.4\n"
        for error, code in [
            (ModuleNotFoundError("dependency missing at C:/private/synthetic.py"), "missing_dependency"),
            (ImportError("DLL failure at C:/private/synthetic.pyd"), "dependency_load_failed"),
            (OSError("DLL failure at C:/private/synthetic.dll"), "dependency_load_failed"),
        ]:
            with self.subTest(error=type(error).__name__), patch.object(intake.importlib, "import_module", side_effect=error):
                capability = intake.digital_pdf_capability()
                with self.assertRaises(intake.DigitalPdfDependencyError) as refused:
                    parse_document(source, "synthetic.pdf", "none")
            self.assertFalse(capability["available"])
            self.assertEqual(capability["status"], "unavailable")
            self.assertEqual(capability["reason_code"], code)
            self.assertEqual(refused.exception.reason_code, code)
            self.assertEqual(str(refused.exception), capability["reason"])
            self.assertTrue(refused.exception.__suppress_context__)
            self.assertNotIn("private", json.dumps(capability))
            self.assertNotIn("DLL", str(refused.exception))

    def test_digital_pdf_module_without_opener_fails_closed_in_capability_and_intake(self):
        from types import SimpleNamespace
        with patch.object(intake.importlib, "import_module", return_value=SimpleNamespace(open=None)):
            capability = intake.digital_pdf_capability()
            with self.assertRaises(intake.DigitalPdfDependencyError) as refused:
                parse_document(b"%PDF-1.4\n", "synthetic.pdf", "none")
        self.assertFalse(capability["available"])
        self.assertEqual(capability["reason_code"], "dependency_load_failed")
        self.assertEqual(str(refused.exception), capability["reason"])

    def test_digital_pdf_dependency_readiness_is_not_field_accuracy_or_ocr(self):
        from types import SimpleNamespace
        with patch.object(intake.importlib, "import_module", return_value=SimpleNamespace(open=lambda: None)):
            available = intake.digital_pdf_capability()
        self.assertTrue(available["available"])
        self.assertEqual(available["status"], "available")
        self.assertEqual(available["verification_scope"], "dependency_import_only")
        self.assertEqual(available["field_accuracy"], "not_verified")
        self.assertNotIn("ocr", available)

    def test_native_csv_keeps_separate_quantities_and_net_gross(self):
        doc = parse_document(csv_bytes(), "synthetic.csv", "off")
        row = doc["rows"][0]
        self.assertEqual([row[f] for f in HEADERS], VALUES)
        self.assertEqual(row["evidence"]["quantity"]["source"], {"row": 2, "column": 5})
        self.assertEqual(summarize(doc)["totals"], {"package_count": 2, "quantity": 10, "net_kg": 200, "gross_kg": 220})
        self.assertTrue(summarize(doc)["ready_for_packing"])

    def test_changing_column_order_cannot_change_interpretation(self):
        original = dict(zip(HEADERS, VALUES))
        headers = list(reversed(HEADERS))
        doc = parse_document(csv_bytes(headers, [[original[h] for h in headers]]), "reordered.csv")
        self.assertEqual({h: doc["rows"][0][h] for h in HEADERS}, original)

    def test_unknown_quantity_and_units_are_not_invented(self):
        doc = parse_document(csv_bytes(["name", "length", "width", "height", "net weight"], [["Synthetic", 1, 2, 3, 100]]), "unknown.csv")
        row = doc["rows"][0]
        self.assertEqual(row["quantity"], UNSPECIFIED)
        self.assertEqual(row["length_mm"], UNSPECIFIED)
        self.assertEqual(row["net_kg"], UNSPECIFIED)
        self.assertFalse(doc["report"]["ok"])
        self.assertFalse(summarize(doc)["ready_for_packing"])

    def test_explicit_units_and_scope_are_evidenced(self):
        doc = parse_document(csv_bytes(["name", "箱长(cm)", "箱宽(cm)", "箱高(cm)", "Net weight per package (kg)", "Gross weight per package (kg)", "箱数"], [["Synthetic", 120, 80, 10, 100, 110, 2]]), "units.csv")
        row = doc["rows"][0]
        self.assertEqual(row["length_mm"], 1200)
        self.assertEqual(row["dimension_scope"], "package")
        self.assertEqual(row["weight_scope"], "package")
        self.assertEqual(row["evidence"]["length_mm"]["raw"], "120")

    def test_scope_is_not_inferred_from_magnitude(self):
        values = list(VALUES); values[10] = ""; values[13] = ""
        doc = parse_document(csv_bytes(rows=[values]), "scope.csv")
        self.assertEqual(summarize(doc)["totals"]["gross_kg"], UNSPECIFIED)
        self.assertIn("unknown_scope", [i["code"] for i in doc["report"]["issues"]])

    def test_combined_and_individual_dimensions_cannot_choose_by_column_order(self):
        headers = ["name", "package_count", "package dimensions (mm)", "length_mm", "weight_scope", "gross_kg"]
        values = ["Synthetic", 1, "1000x800x600", 2000, "package", 100]
        for order in (list(range(len(headers))), list(reversed(range(len(headers))))):
            with self.subTest(order=order):
                doc = parse_document(csv_bytes([headers[i] for i in order], [[values[i] for i in order]]), "dimension-conflict.csv")
                row = doc["rows"][0]
                self.assertEqual(row["length_mm"], UNSPECIFIED)
                self.assertEqual((row["width_mm"], row["height_mm"]), (800, 600))
                self.assertIn("多个列", row["evidence"]["length_mm"]["reason"])
                self.assertFalse(doc["report"]["ok"])
                self.assertFalse(summarize(doc)["ready_for_packing"])

    def test_duplicate_combined_dimension_columns_reject_all_three_targets(self):
        doc = parse_document(csv_bytes(["name", "package dimensions (mm)", "dimensions (mm)"],
                                      [["Synthetic", "1000x800x600", "2000x900x700"]]), "duplicate-dimensions.csv")
        row = doc["rows"][0]
        for field in ("length_mm", "width_mm", "height_mm"):
            self.assertEqual(row[field], UNSPECIFIED)
            self.assertIn("多个列", row["evidence"][field]["reason"])
        self.assertFalse(doc["report"]["ok"])

    def test_rejected_pdf_dimensions_keep_the_extractor_reason(self):
        headers = ["name", "package_count", "package dimensions (mm)", "weight_scope", "gross_kg"]
        values = ["Synthetic", 1, "1000x800x600", "package", 100]
        table = {"rows": [[intake._cell(v, page=1, row=ri, column=ci)
                           for ci, v in enumerate(row, 1)] for ri, row in enumerate((headers, values), 1)]}
        reason = "单元格横跨多个字段列，未猜测该值属于哪一列。"
        table["rows"][1][2]["reason"] = reason
        with patch.object(intake, "_pdf", return_value=([table], [], "pdfplumber")):
            doc = parse_document(b"synthetic extractor boundary", "synthetic.pdf", "off")
        for field in ("length_mm", "width_mm", "height_mm"):
            self.assertEqual(doc["rows"][0][field], UNSPECIFIED)
            self.assertEqual(doc["rows"][0]["evidence"][field]["reason"], reason)
        self.assertFalse(summarize(doc)["ready_for_packing"])
        self.assertEqual(sum(i["code"] == "unresolved_field" for i in doc["report"]["issues"]), 3)

    def test_totals_and_quantity_reconcile_independently(self):
        values = list(VALUES); values[4] = 11
        total = ["", "", "Grand total", 2, 10, "", "", "", "", "", "", 201, 220, "row"]
        doc = parse_document(csv_bytes(rows=[values, total]), "mismatch.csv")
        self.assertEqual(len(doc["rows"]), 1)
        self.assertEqual(len(doc["totals"]), 1)
        codes = [i["code"] for i in doc["report"]["issues"]]
        self.assertIn("quantity_mismatch", codes)
        self.assertIn("total_mismatch", codes)

    def test_duplicate_headers_and_fractional_counts_remain_unresolved(self):
        doc = parse_document(csv_bytes(["name", "quantity", "件数", "package_count"], [["Synthetic", 10, 12, "2.5"]]), "conflict.csv")
        row = doc["rows"][0]
        self.assertEqual(row["quantity"], UNSPECIFIED)
        self.assertEqual(row["package_count"], UNSPECIFIED)
        self.assertFalse(doc["report"]["ok"])

    def test_repeated_headers_never_drop_repeated_cargo(self):
        doc = parse_document(csv_bytes(rows=[VALUES, HEADERS, VALUES]), "repeat.csv")
        self.assertEqual(len(doc["rows"]), 2)
        self.assertEqual([r["id"] for r in doc["rows"]], ["R00001", "R00002"])
        self.assertEqual(doc["rows"][1]["evidence"]["name"]["source"]["row"], 4)

    def test_xlsx_sheets_formula_and_formatted_ids(self):
        from openpyxl import Workbook
        wb = Workbook()
        wb.active.title = "Sheet A"
        for sheet in (wb.active, wb.create_sheet("Sheet B")):
            sheet.append(HEADERS); sheet.append(VALUES)
        wb.active["A2"] = 7; wb.active["A2"].number_format = "0000"
        wb.active["E2"] = "=2*5"
        wb.active["B2"] = None  # read_only EmptyCell has no row/column attributes
        data = BytesIO(); wb.save(data); wb.close()
        doc = parse_document(data.getvalue(), "synthetic.xlsx")
        self.assertEqual(len(doc["rows"]), 2)
        self.assertEqual(doc["rows"][0]["package_id"], "0007")
        self.assertEqual(doc["rows"][0]["quantity"], UNSPECIFIED)
        self.assertEqual(doc["rows"][0]["evidence"]["quantity"]["raw"], "=2*5")
        self.assertEqual(doc["rows"][1]["evidence"]["name"]["source"]["sheet"], "Sheet B")

    def test_real_pdf_table_geometry_and_page_duplicates(self):
        doc = parse_document(pdf_bytes(), "synthetic-two-pages.pdf", "off")
        self.assertEqual(len(doc["rows"]), 2)
        self.assertEqual([r["quantity"] for r in doc["rows"]], [10, 10])
        self.assertEqual([r["evidence"]["name"]["source"]["page"] for r in doc["rows"]], [1, 2])
        self.assertEqual(doc["rows"][0]["evidence"]["name"]["source"]["coordinate_system"], "pdf_points")
        self.assertEqual(len(doc["rows"][0]["evidence"]["name"]["source"]["bbox"]), 4)

    def test_blank_pdf_requires_ocr_without_empty_success(self):
        doc = parse_document(pdf_bytes(1, True), "synthetic-scan.pdf", "off")
        self.assertFalse(doc["report"]["ok"])
        self.assertIn("ocr_required", [i["code"] for i in doc["report"]["issues"]])

    def test_injected_local_ocr_preserves_raw_boxes_and_requires_confirmation(self):
        html = "<table><tr>" + "".join(f"<td>{h}</td>" for h in HEADERS) + "</tr><tr>" + "".join(f"<td>{v}</td>" for v in VALUES) + "</tr></table>"
        converted = ocr.tables_from_result({"table_res_list": [{"pred_html": html, "cell_box_list": [[i, 0, i + 1, 10] for i in range(28)]}]}, page=1)
        with patch.object(ocr, "extract_tables", return_value=converted):
            doc = parse_document(b"synthetic injected OCR bytes", "synthetic.png")
        self.assertEqual(doc["rows"][0]["quantity"], 10)
        self.assertEqual(doc["rows"][0]["evidence"]["quantity"]["source"]["bbox"], [18, 0, 19, 10])
        self.assertTrue(summarize(doc)["input_complete"])
        self.assertTrue(summarize(doc)["requires_review"])
        self.assertTrue(summarize(doc)["confirmation_required"])
        self.assertIn("ocr_requires_confirmation", [i["code"] for i in doc["report"]["issues"]])

    def test_same_package_material_rows_do_not_duplicate_package_totals(self):
        second = list(VALUES); second[1] = "P002"; second[2] = "Synthetic bolts"
        total = ["", "", "Grand total", 2, 20, "", "", "", "", "", "", 200, 220, "row"]
        doc = parse_document(csv_bytes(rows=[VALUES, second, total]), "same-package.csv")
        summary = summarize(doc)
        self.assertEqual(summary["totals"], {"package_count": 2, "quantity": 20, "net_kg": 200, "gross_kg": 220})
        self.assertTrue(summary["audit"]["ok"])

    def test_same_package_conflicting_weights_never_add_up(self):
        second = list(VALUES); second[1] = "P002"; second[12] = 115
        doc = parse_document(csv_bytes(rows=[VALUES, second]), "conflicting-package.csv")
        summary = summarize(doc)
        self.assertEqual(summary["totals"]["gross_kg"], UNSPECIFIED)
        self.assertEqual(summary["totals"]["package_count"], UNSPECIFIED)
        self.assertFalse(summary["ready_for_packing"])
        self.assertIn("package_group_conflict", [i["code"] for i in summary["audit"]["issues"]])

    def test_mixed_quantity_units_are_grouped_not_summed(self):
        second = list(VALUES); second[0] = "C002"; second[6] = "m"
        total = ["", "", "Grand total", 4, 20, "", "", "", "", "", "", 400, 440, "row"]
        doc = parse_document(csv_bytes(rows=[VALUES, second, total]), "mixed-units.csv")
        summary = summarize(doc)
        self.assertEqual(summary["totals"]["quantity"], UNSPECIFIED)
        self.assertEqual(summary["quantities_by_unit"], {"pcs": 10, "m": 10})
        self.assertIn("mixed_unit_source_total", [i["code"] for i in summary["audit"]["issues"]])

    def test_ocr_environment_is_not_reported_ready_without_manifest(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); exe = root / "python.exe"; exe.write_bytes(b"synthetic executable placeholder")
            manifest = root / "ready.json"
            with patch.dict(os.environ, {"CIVIL_LOGISTICS_OCR_PYTHON": str(exe), "CIVIL_LOGISTICS_OCR_READY_MANIFEST": str(manifest)}):
                cap = ocr.capability()
                self.assertTrue(cap["environment_found"]); self.assertFalse(cap["available"])
                model = root / "model.bin"; model.write_bytes(b"synthetic")
                manifest.write_text(json.dumps({"schema": "civil.logistics.ocr-ready.v1", "python": str(exe), "paddleocr": "test", "paddlepaddle": "test", "models": [{"path": str(model), "bytes": model.stat().st_size}]}), encoding="utf-8")
                self.assertTrue(ocr.capability()["available"])
                model.write_bytes(b"changed")
                self.assertFalse(ocr.capability()["available"])

    def test_complex_ocr_merged_table_is_explicitly_unsupported(self):
        result = ocr.tables_from_result({"table_res_list": [{"pred_html": '<table><tr><td rowspan="2">Name</td><td>Qty</td></tr><tr><td>10</td></tr></table>'}]}, page=1)
        self.assertEqual(result["tables"], [])
        self.assertEqual(result["issues"][0]["code"], "ocr_merged_cells")

    def test_corrupt_files_zip_and_limits(self):
        for data, name in ((b"bad", "x.xlsx"), (b"bad", "x.xls"), (b"a", "../x.csv")):
            with self.assertRaises((ValueError, OSError)):
                parse_document(data, name)
        data = BytesIO()
        with ZipFile(data, "w") as archive:
            for i in range(4097):
                archive.writestr(str(i), b"")
        with self.assertRaisesRegex(ValueError, "解压"):
            parse_document(data.getvalue(), "synthetic-bomb.xlsx")
        with patch.object(intake, "MAX_ROWS", 1), self.assertRaisesRegex(ValueError, "物料行"):
            parse_document(csv_bytes(rows=[VALUES, VALUES]), "limit.csv")

    def test_image_pixel_limit_checked_before_ocr_runtime(self):
        from PIL import Image
        image = BytesIO(); Image.new("RGB", (20, 20)).save(image, format="PNG")
        with patch.object(ocr, "MAX_PIXELS", 100), self.assertRaisesRegex(ValueError, "像素"):
            ocr.extract_tables(image.getvalue(), ".png")

    def test_validation_rejects_nonfinite_duplicate_ids_and_invalid_location(self):
        doc = parse_document(csv_bytes(), "synthetic.csv")
        bad = deepcopy(doc); bad["rows"][0]["net_kg"] = float("nan")
        with self.assertRaises(ValueError): validate_document(bad)
        bad = deepcopy(doc); bad["rows"].append(deepcopy(bad["rows"][0]))
        with self.assertRaises(ValueError): validate_document(bad)
        bad = deepcopy(doc); bad["rows"][0]["evidence"]["quantity"]["source"]["page"] = -1
        with self.assertRaises(ValueError): validate_document(bad)
        clone = validate_document(doc); clone["rows"][0]["quantity"] = 7
        self.assertEqual(doc["rows"][0]["quantity"], 10)

    def test_parallel_imports_do_not_share_stats_or_sources(self):
        def parse(index):
            values = list(VALUES); values[0] = f"C{index}"
            return parse_document(csv_bytes(rows=[values]), f"synthetic-{index}.csv")
        with ThreadPoolExecutor(max_workers=4) as pool:
            docs = list(pool.map(parse, range(8)))
        self.assertEqual([d["source"]["filename"] for d in docs], [f"synthetic-{i}.csv" for i in range(8)])
        self.assertEqual([d["rows"][0]["package_id"] for d in docs], [f"C{i}" for i in range(8)])

    def test_cancellation_prevents_publication(self):
        event = Event(); event.set()
        with scope(event=event), self.assertRaises(RunCancelled):
            parse_document(csv_bytes(), "synthetic.csv")

    def test_json_roundtrip_rebinds_hash_and_retains_original_evidence(self):
        original = parse_document(csv_bytes(), "synthetic.csv")
        raw = json.dumps(original).encode("utf-8")
        imported = parse_document(raw, "synthetic-ledger.json")
        self.assertEqual(imported["rows"], original["rows"])
        self.assertNotEqual(imported["source"]["sha256"], original["source"]["sha256"])
        self.assertEqual(imported["extraction"]["original_source"], original["source"])
        with self.assertRaises(ValueError):
            parse_document(b'{"rows":[]}', "not-ledger.json")
        with self.assertRaises(ValueError):
            parse_document(b'{"schema":1,"schema":2}', "duplicate.json")

    def test_total_word_in_spec_does_not_remove_material(self):
        doc = parse_document(csv_bytes(["name", "spec", "quantity", "unit"], [["Synthetic panel", "Total", 10, "pcs"]]), "spec.csv")
        self.assertEqual(len(doc["rows"]), 1)
        self.assertEqual(doc["rows"][0]["quantity"], 10)
        self.assertEqual(doc["totals"], [])

    def test_local_ocr_child_does_not_inherit_host_secrets(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-secret", "CIVIL_TOKEN": "synthetic-token", "AWS_SECRET_ACCESS_KEY": "synthetic-secret", "HTTPS_PROXY": "synthetic-proxy", "CIVIL_LOGISTICS_OCR_CONFIG": "local-models.json"}):
            env = ocr._worker_environment([1])
        self.assertFalse(any(k in env for k in ("OPENAI_API_KEY", "CIVIL_TOKEN", "AWS_SECRET_ACCESS_KEY", "HTTPS_PROXY")))
        self.assertEqual(env["CIVIL_LOGISTICS_OCR_CONFIG"], "local-models.json")
        self.assertEqual(env["HF_HUB_OFFLINE"], "1")

    def test_chinese_headers_can_touch_explicit_units_without_spaces(self):
        doc = parse_document(csv_bytes(["品名", "材料编号", "净重kg", "毛重kg", "重量口径"], [["Synthetic", "MAT001", 100, 110, "每箱"]]), "compact-headers.csv")
        self.assertEqual(doc["rows"][0]["material_id"], "MAT001")
        self.assertEqual(doc["rows"][0]["net_kg"], 100)
        self.assertEqual(doc["rows"][0]["gross_kg"], 110)

    def test_bilingual_wrapped_headers_keep_container_and_box_ids_separate(self):
        headers = ["集装箱号\nCtn.No", "货物名称及规格\nDescription", "包装总箱\n数Cases", "总数量\nQuantity", "总毛重\nGW.(KGS)", "总净重\nNW.(KGS)"]
        doc = parse_document(csv_bytes(headers, [["SYNTH-123", "Synthetic steel", "7 铁框", "123 件", "950", "900"]]), "bilingual.csv")
        row = doc["rows"][0]
        self.assertEqual(row["container_id"], "SYNTH-123")
        self.assertEqual(row["package_id"], UNSPECIFIED)
        self.assertEqual((row["package_count"], row["package_type"]), (7, "铁框"))
        self.assertEqual((row["quantity"], row["unit"]), (123, "件"))
        self.assertEqual((row["gross_kg"], row["net_kg"], row["weight_scope"]), (950, 900, "row"))
        self.assertEqual(row["evidence"]["package_type"]["raw"], "7 铁框")

    def test_conflicting_header_translations_are_rejected(self):
        doc = parse_document(csv_bytes(["品名\nDescription", "净重\nGross weight (kg)", "数量\nQuantity"], [["Synthetic", 90, "10 件"]]), "header-conflict.csv")
        self.assertEqual(doc["rows"][0]["net_kg"], UNSPECIFIED)
        self.assertEqual(doc["rows"][0]["gross_kg"], UNSPECIFIED)
        self.assertIn("conflicting_header", [i["code"] for i in doc["report"]["issues"]])

    def test_quantity_units_and_package_types_come_from_written_suffixes(self):
        doc = parse_document(csv_bytes(["品名", "数量", "包装数"], [["Synthetic A", "15 卷", "2 木箱"], ["Synthetic B", "80 米", "5 扎"]]), "suffix.csv")
        self.assertEqual([(r["quantity"], r["unit"], r["package_count"], r["package_type"]) for r in doc["rows"]], [(15, "卷", 2, "木箱"), (80, "米", 5, "扎")])
        self.assertEqual(summarize(doc)["quantities_by_unit"], {"卷": 15, "米": 80})

    def test_explicit_package_count_pieces_and_racks_are_not_renamed_boxes(self):
        doc = parse_document(csv_bytes(["品名", "包装数"], [["Synthetic A", "17 铁架"], ["Synthetic B", "6 件"]]), "package-units.csv")
        self.assertEqual([(r["package_count"], r["package_type"]) for r in doc["rows"]], [(17, "铁架"), (6, "件")])

    def test_suffix_and_unit_column_conflict_is_not_silently_resolved(self):
        doc = parse_document(csv_bytes(["name", "quantity", "unit"], [["Synthetic", "18 米", "pcs"]]), "unit-conflict.csv")
        self.assertEqual(doc["rows"][0]["quantity"], UNSPECIFIED)
        self.assertEqual(doc["rows"][0]["unit"], UNSPECIFIED)
        self.assertIn("冲突", doc["rows"][0]["evidence"]["quantity"]["reason"])

    def test_bilingual_totals_are_scoped_to_each_table(self):
        def table(qty):
            return {"rows": [[intake._cell(v) for v in row] for row in [["货物名称\nDescription", "数量\nQuantity"], ["Synthetic", f"{qty} 件"], ["总计\nTotal", qty]]]}
        rows, totals = intake._table_rows([table(7), table(11)], [])
        self.assertEqual([t["row_ids"] for t in totals], [["R00001"], ["R00002"]])
        self.assertEqual([r["quantity"] for r in rows], [7, 11])

    def test_grand_total_after_subtotals_keeps_the_full_table_scope(self):
        for label in ("Grand total", "总计", "总计\nTotal"):
            with self.subTest(label=label):
                doc = parse_document(csv_bytes(["name", "package_count", "quantity", "unit"], [
                    ["Synthetic A", 1, 2, "pcs"], ["Subtotal", 1, 2, ""],
                    ["Synthetic B", 3, 4, "pcs"], ["Subtotal", 3, 4, ""], [label, 4, 6, ""]]), "subtotals.csv")
                self.assertEqual([t["row_ids"] for t in doc["totals"]],
                                 [["R00001"], ["R00002"], ["R00001", "R00002"]])
                self.assertTrue(doc["report"]["ok"])

    def test_incorrect_grand_total_after_subtotal_still_fails_reconciliation(self):
        doc = parse_document(csv_bytes(["name", "package_count", "quantity", "unit"], [
            ["Synthetic A", 1, 2, "pcs"], ["Subtotal", 1, 2, ""],
            ["Synthetic B", 3, 4, "pcs"], ["Grand total", 3, 4, ""]]), "wrong-total.csv")
        self.assertEqual(doc["totals"][-1]["row_ids"], ["R00001", "R00002"])
        self.assertEqual(sum(i["code"] == "total_mismatch" for i in doc["report"]["issues"]), 2)

    def test_header_after_closed_grand_total_starts_an_independent_table(self):
        headers = ["name", "package_count", "quantity", "unit"]
        doc = parse_document(csv_bytes(headers, [
            ["Synthetic A", 1, 2, "pcs"], ["Grand total", 1, 2, ""], [], headers,
            ["Synthetic B", 3, 4, "pcs"], ["Grand total", 3, 4, ""]]), "separate-tables.csv")
        self.assertEqual([t["row_ids"] for t in doc["totals"]], [["R00001"], ["R00002"]])
        self.assertTrue(doc["report"]["ok"])

    def test_repeated_header_before_grand_total_keeps_accumulating(self):
        headers = ["name", "package_count", "quantity", "unit"]
        doc = parse_document(csv_bytes(headers, [
            ["Synthetic A", 1, 2, "pcs"], ["Subtotal", 1, 2, ""], [], headers,
            ["Synthetic B", 3, 4, "pcs"], ["Grand total", 4, 6, ""]]), "continued-table.csv")
        self.assertEqual([t["row_ids"] for t in doc["totals"]], [["R00001"], ["R00001", "R00002"]])
        self.assertTrue(doc["report"]["ok"])

    def test_explicit_invoice_and_contract_pages_are_excluded(self):
        doc = parse_document(business_pdf_bytes(("INVOICE", "PACKING LIST", "CONTRACT")), "mixed-synthetic.pdf", "off")
        self.assertEqual(len(doc["rows"]), 2)
        self.assertEqual({r["evidence"]["name"]["source"]["page"] for r in doc["rows"]}, {2})
        excluded = [i for i in doc["extraction"]["issues"] if i["code"] == "excluded_non_packing_page"]
        self.assertEqual([i["page"] for i in excluded], [1, 3])

    def test_invoice_word_inside_material_name_is_not_a_page_title(self):
        doc = parse_document(business_pdf_bytes(cargo_name="INVOICE"), "cargo-word.pdf", "off")
        self.assertEqual(len(doc["rows"]), 2)
        self.assertEqual(doc["rows"][0]["name"], "INVOICE")

    def test_unknown_page_in_mixed_pack_is_not_assumed_a_continuation(self):
        doc = parse_document(business_pdf_bytes(("PACKING LIST", "APPENDIX")), "unknown-page.pdf", "off")
        self.assertEqual(len(doc["rows"]), 2)
        self.assertIn("ambiguous_document_page", [i["code"] for i in doc["extraction"]["issues"]])

    def test_pdf_rowspan_has_one_value_and_shared_geometry_evidence(self):
        doc = parse_document(business_pdf_bytes(), "merged-synthetic.pdf", "off")
        first, second = doc["rows"]
        self.assertEqual(first["package_count"], 3)
        self.assertEqual(second["package_count"], UNSPECIFIED)
        group = first["evidence"]["package_count"]["group"]
        self.assertEqual(group["row_ids"], [first["id"], second["id"]])
        self.assertEqual(group, second["evidence"]["package_count"]["group"])
        self.assertEqual(group["anchor_row_id"], first["id"])
        self.assertEqual(group["source"]["bbox"][1:4:2], [120, 180])
        self.assertEqual(summarize(doc)["totals"]["gross_kg"], 90)
        self.assertEqual(summarize(doc)["totals"]["package_count"], 3)

    def test_empty_unmerged_cells_are_never_forward_filled(self):
        tables, issues, _ = intake._pdf(business_pdf_bytes(merged=False), "none")
        tables[0]["rows"][2][2]["raw"] = ""
        rows, _ = intake._table_rows(tables, issues)
        self.assertEqual(rows[1]["package_count"], UNSPECIFIED)
        self.assertNotIn("group", rows[1]["evidence"]["package_count"])

    def test_two_packing_pages_keep_separate_totals_and_source_groups(self):
        doc = parse_document(business_pdf_bytes(("PACKING LIST", "PACKING LIST")), "two-packing-lists.pdf", "off")
        self.assertEqual(len(doc["rows"]), 4)
        self.assertEqual([t["row_ids"] for t in doc["totals"]], [["R00001", "R00002"], ["R00003", "R00004"]])
        self.assertNotEqual(doc["rows"][0]["evidence"]["gross_kg"]["group"]["id"], doc["rows"][2]["evidence"]["gross_kg"]["group"]["id"])
        self.assertNotIn("total_mismatch", [i["code"] for i in doc["report"]["issues"]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
