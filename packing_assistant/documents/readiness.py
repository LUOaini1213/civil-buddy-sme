"""Bounded, read-only delivery checks; neither a renderer nor a calculation engine.

The host supplies a registered artifact's expected hash. These checks describe
the exact bytes inspected and never establish engineering or human acceptance.
Office XML is parsed with Package.xml's DTD/entity rejection. PDF streams are
not executed or decoded by the active-content scan.
"""
from __future__ import annotations

import math
import re

from .common import fail, validation
from .ooxml import S, W

MAX_CELL_DETAILS = 100
MAX_PDF_OBJECTS = 50_000


def _check(identifier, status, detail):
    return {"id": identifier, "status": status, "detail": detail}


def _office_parts(document):
    package = document.package
    roots = {}
    # Inspect all XML, including relationships and untouched package parts. An
    # entity in an otherwise unused header must not escape the XML guard.
    for name in package.parts:
        if name.lower().endswith((".xml", ".rels")):
            roots[name] = package.xml(name)
    lower_names = [name.lower() for name in package.parts]
    external = 0
    macros = any("vbaproject" in name or "/macrosheets/" in name for name in lower_names)
    embedded = sum("/embeddings/" in name or "/activex/" in name for name in lower_names)
    for name, root in roots.items():
        if name.lower().endswith(".rels"):
            external += sum(node.get("TargetMode", "").lower() == "external" for node in root)
        if name == "[Content_Types].xml":
            macros |= any("macroenabled" in node.get("ContentType", "").lower()
                          or "vba" in node.get("ContentType", "").lower() for node in root)
    details = {"macro_content": bool(macros), "embedded_object_parts": embedded,
               "external_relationship_count": external,
               "digital_signature_parts": sum(name.startswith("_xmlsignatures/") for name in lower_names),
               "xml_parts_checked": len(roots)}
    checks = [
        _check("active_content", "blocked" if macros or embedded else "pass",
               "Macros or embedded objects require a separate review workflow." if macros or embedded
               else "No macro or embedded-object parts were found; no content was executed."),
        _check("external_links", "review_required" if external else "pass",
               "External relationships are present and were not opened." if external
               else "No external relationships were found in the package."),
    ]
    return roots, details, checks


def _docx(document):
    roots, details, checks = _office_parts(document)
    if document.root.tag != f"{{{W}}}document":
        fail("invalid_document", "DOCX main document has an unsupported root")
    details.update(paragraph_count=len(document.paragraphs), table_count=len(document.tables),
                   tracked_change_count=sum(node.tag in {f"{{{W}}}ins", f"{{{W}}}del"}
                                            for root in roots.values() for node in root.iter()),
                   field_count=sum(node.tag in {f"{{{W}}}fldSimple", f"{{{W}}}fldChar"}
                                   for root in roots.values() for node in root.iter()),
                   renderer="not_run", pagination="not_verified")
    checks.append(_check("layout", "review_required",
                         "Open the exact DOCX in an Office renderer and review every page; XML checks do not verify pagination."))
    if details["tracked_change_count"] or details["field_count"]:
        checks.append(_check("document_fields", "review_required",
                             "Tracked changes or fields are present; their final appearance and values need review."))
    return details, checks, {"kind": "unavailable", "eligible": False, "rendered": False}


def _xlsx(document):
    _, details, checks = _office_parts(document)
    if document.book.tag != f"{{{S}}}workbook":
        fail("invalid_document", "XLSX workbook has an unsupported root")
    formulas = cached = missing = invalid = errors = external_formulas = issue_count = 0
    issue_cells = []
    sheets = []
    for sheet_name, (_, root) in document.sheets.items():
        if root.tag != f"{{{S}}}worksheet":
            fail("unsupported", "Only worksheet XML is supported for readiness inspection")
        sheet_formulas = 0
        for cell in root.iter(f"{{{S}}}c"):
            formula = cell.find(f"{{{S}}}f")
            value = cell.find(f"{{{S}}}v")
            kind = cell.get("t", "n")
            issue = None
            if kind == "e":
                errors += 1
                issue = "cell_error"
            if formula is not None:
                formulas += 1
                sheet_formulas += 1
                formula_text = formula.text or ""
                # Flag external-workbook/DDE and network-capable functions
                # without evaluating formulas or following their targets.
                if (re.search(r"\[[^\]]+\][^!]*!|(?:https?|file):|\|[^!]*!", formula_text, re.I)
                        or re.search(r"(?:^|[^A-Z0-9_])(?:_XLFN\.)?(?:WEBSERVICE|RTD|HYPERLINK|IMAGE|DDE)\s*\(", formula_text, re.I)):
                    external_formulas += 1
                # An empty string is a valid cached string formula result. An
                # absent <v>, or an empty numeric cache, is not a result.
                if value is None or (value.text is None and kind != "str"):
                    missing += 1
                    issue = issue or "formula_cache_missing"
                else:
                    cached += 1
                    raw = value.text or ""
                    usable = kind in {"str", "e"}
                    if kind == "b":
                        usable = raw in {"0", "1"}
                    elif kind in {"n", ""}:
                        try:
                            usable = math.isfinite(float(raw))
                        except ValueError:
                            usable = False
                    if not usable:
                        invalid += 1
                        issue = issue or "formula_cache_invalid"
            if issue:
                issue_count += 1
                if len(issue_cells) < MAX_CELL_DETAILS:
                    issue_cells.append({"sheet": sheet_name, "cell": cell.get("r", ""), "issue": issue})
        sheets.append({"name": sheet_name, "formula_count": sheet_formulas})
    calc = document.book.find(f"{{{S}}}calcPr")
    flags = {key: calc.get(key) if calc is not None else None
             for key in ("calcMode", "fullCalcOnLoad", "forceFullCalc", "calcId")}
    details.update(sheet_count=len(document.sheets), sheets=sheets, formula_count=formulas,
                   formula_cached_count=cached, formula_missing_cache_count=missing,
                   formula_invalid_cache_count=invalid, error_cell_count=errors,
                   issue_cells=issue_cells, issue_cells_truncated=issue_count > len(issue_cells),
                   cached_values_status="stored_unverified" if formulas else "not_applicable",
                   recalculation="not_performed", calculation_properties=flags,
                   external_formula_count=external_formulas,
                   external_formula_check="syntax_markers_only",
                   data_connection_parts=sum(name.lower() == "xl/connections.xml" or name.lower().startswith("xl/querytables/")
                                             for name in document.package.parts),
                   external_link_parts=sum(name.lower().startswith("xl/externallinks/")
                                           for name in document.package.parts))
    if external_formulas or details["data_connection_parts"] or details["external_link_parts"]:
        checks.append(_check("external_data", "review_required",
                             "External-data formulas or connection parts are present; targets were not opened or refreshed."))
    checks.extend([
        _check("formula_cache", "blocked" if missing or invalid else "review_required" if formulas else "not_applicable",
               "Formula caches are missing or invalid; recalculate and save a new copy in a spreadsheet engine."
               if missing or invalid else "Stored formula caches may be stale; their presence does not prove recalculation."
               if formulas else "The workbook contains no cell formulas."),
        _check("spreadsheet_errors", "blocked" if errors else "pass",
               "Error cells were found; review them before delivery." if errors else "No cells with an error type were found."),
        _check("recalculation", "review_required" if formulas else "not_applicable",
               "No spreadsheet engine ran; automatic/full-calculation flags are requests, not proof of recalculation."
               if formulas else "No cell formulas require recalculation."),
        _check("layout", "review_required", "Review sheet layout, print areas and page breaks in a spreadsheet application."),
    ])
    return details, checks, {"kind": "unavailable", "eligible": False, "rendered": False}


def _pdf(document):
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject

    reader = document.reader
    # Walk the reachable object graph with bounded work, including annotations,
    # form fields and name trees. Never execute actions or decode stream data.
    stack = [(reader.trailer, 0)]
    visited_refs, visited_containers, active = set(), set(), set()
    processed = 0
    action_keys = {"/JS", "/JavaScript", "/OpenAction", "/AA", "/A", "/Launch",
                   "/EmbeddedFiles", "/EF", "/XFA", "/RichMedia", "/RichMediaContent"}
    action_types = {"/JavaScript", "/Launch", "/SubmitForm", "/ImportData", "/GoToR",
                    "/GoToE", "/URI", "/Rendition", "/Movie", "/Sound"}
    while stack:
        obj, depth = stack.pop()
        processed += 1
        if processed > MAX_PDF_OBJECTS or depth > 100:
            fail("too_large", "PDF object graph exceeds the readiness inspection limit")
        if isinstance(obj, IndirectObject):
            key = (obj.idnum, obj.generation)
            if key in visited_refs:
                continue
            visited_refs.add(key)
            stack.append((obj.get_object(), depth + 1))
        elif isinstance(obj, (DictionaryObject, ArrayObject)):
            if id(obj) in visited_containers:
                continue
            visited_containers.add(id(obj))
            if isinstance(obj, DictionaryObject):
                active.update(str(key) for key in obj if str(key) in action_keys)
                if str(obj.get("/S", "")) in action_types:
                    active.add(str(obj.get("/S")))
                if str(obj.get("/Subtype", "")) in {"/RichMedia", "/Movie", "/Sound", "/3D"}:
                    active.add(str(obj.get("/Subtype")))
                stack.extend((value, depth + 1) for value in obj.values())
            else:
                stack.extend((value, depth + 1) for value in obj)
        if len(stack) > MAX_PDF_OBJECTS:
            fail("too_large", "PDF object graph exceeds the readiness inspection limit")
    pages = []
    for i, page in enumerate(reader.pages):
        width, height = float(page.mediabox.width), float(page.mediabox.height)
        if not all(math.isfinite(n) and 0 < n <= 200_000 for n in (width, height)):
            fail("invalid_document", "PDF page dimensions are invalid or exceed the supported limit")
        pages.append({"page": i + 1, "width_points": width, "height_points": height})
    details = {"page_count": len(pages), "pages": pages, "active_content_markers": sorted(active),
               "object_graph_checked": True, "renderer": "not_run", "form_appearance": "not_verified"}
    checks = [
        _check("active_content", "blocked" if active else "pass",
               "Interactive actions or embedded content were found; inline preview is unavailable."
               if active else "No active-content markers were found in the inspected PDF object graph."),
        _check("layout", "review_required", "Review every page in the PDF viewer; structural inspection does not verify appearance."),
    ]
    return details, checks, {"kind": "native_pdf" if not active else "unavailable",
                             "eligible": not bool(active), "rendered": False}


def inspect_readiness(document, file_format):
    handlers = {"docx": _docx, "xlsx": _xlsx, "pdf": _pdf}
    details, checks, preview = handlers[file_format](document)
    checks = [_check("source_hash", "pass", "The inspected bytes match the required SHA-256."),
              _check("structure", "pass", "The document container and supported structure were reopened."),
              *checks,
              _check("engineering_review", "review_required",
                     "A responsible reviewer must verify source evidence, figures and conclusions; no acceptance is inferred.")]
    report = validation(source_hash="pass")
    if file_format == "xlsx":
        report.update(recalculation="not_recalculated", cached_values=details["cached_values_status"])
    return {"writes": False, "original_preserved": True, "validation": report,
            "readiness": {"schema_version": 1, "status": "blocked" if any(c["status"] == "blocked" for c in checks) else "review_required",
                          "automatic_acceptance": False, "checks": checks, "format_details": details, "preview": preview}}
