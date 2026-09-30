"""Edit selected OOXML nodes, preserving every unmodified ZIP member verbatim.

No Office application, arbitrary Python, macros, or external links are executed.
The byte content of unaffected package parts is retained; ZIP container metadata
and serialization of the edited XML parts may differ.
"""
from __future__ import annotations

from io import BytesIO
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile, BadZipFile

from .common import fail, fields, integer, number, text, validation

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
XML = "http://www.w3.org/XML/1998/namespace"
ET.register_namespace("w", W)
ET.register_namespace("r", R)


class Package:
    def __init__(self, data):
        try:
            with ZipFile(BytesIO(data)) as archive:
                self.infos = archive.infolist()
                if len(self.infos) > 20_000 or sum(i.file_size for i in self.infos) > 100_000_000:
                    fail("too_large", "OOXML package expands beyond the supported limit")
                names = [i.filename for i in self.infos]
                if len(names) != len(set(names)) or any(n.startswith("/") or ".." in n.split("/") for n in names):
                    fail("invalid_document", "Ambiguous or unsafe package member")
                self.parts = {i.filename: archive.read(i) for i in self.infos}
        except (BadZipFile, KeyError, RuntimeError) as exc:
            fail("invalid_document", "Cannot read the OOXML package")
        self.changed = set()
        self.namespaces = {}

    def xml(self, name):
        raw = self.parts.get(name)
        normalized = raw.replace(b"\0", b"").upper() if raw else b""
        if raw is None or b"<!DOCTYPE" in normalized or b"<!ENTITY" in normalized:
            fail("invalid_document", "Required XML part is absent or contains a DTD")
        try:
            namespaces = {}
            for _, (prefix, uri) in ET.iterparse(BytesIO(raw), events=("start-ns",)):
                if prefix in namespaces and namespaces[prefix] != uri:
                    fail("unsupported", "XML with shadowed namespace prefixes requires a different editor")
                namespaces[prefix] = uri
                if not re.fullmatch(r"ns\d+", prefix):
                    ET.register_namespace(prefix, uri)
            self.namespaces[name] = namespaces
            return ET.fromstring(raw)
        except ET.ParseError:
            fail("invalid_document", "Malformed document XML")

    def put(self, name, root):
        # Word's mc:Ignorable and other QName-valued attributes may refer to
        # prefixes that ElementTree would otherwise discard as unused.
        initial = ET.tostring(root, encoding="unicode")
        head = initial.split(">", 1)[0]
        for prefix, uri in self.namespaces.get(name, {}).items():
            attr = "xmlns:" + prefix if prefix else "xmlns"
            if not re.search(r"\s" + re.escape(attr) + r"=", head):
                root.set(attr, uri)
        self.parts[name] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        self.changed.add(name)

    def edit_guard(self):
        if any(n.startswith("_xmlsignatures/") for n in self.parts):
            fail("unsupported", "Digitally signed Office packages cannot be edited by this worker")

    def save(self):
        output = BytesIO()
        with ZipFile(output, "w") as archive:
            for info in self.infos:
                archive.writestr(info, self.parts[info.filename])
        return output.getvalue()


def _pt(p):
    return "".join(n.text or "" for n in p.iter(f"{{{W}}}t"))


class Word:
    def __init__(self, data):
        self.package = Package(data)
        self.root = self.package.xml("word/document.xml")
        self.paragraphs = list(self.root.iter(f"{{{W}}}p"))
        self.tables = list(self.root.iter(f"{{{W}}}tbl"))
        if len(self.paragraphs) > 50_000:
            fail("too_large", "DOCX contains too many paragraphs")

    def inspect(self):
        return {"format": "docx", "paragraph_count": len(self.paragraphs), "table_count": len(self.tables),
                "nodes": [{"id": f"p:{i}", "text": _pt(p)[:500], "chars": len(_pt(p))}
                          for i, p in enumerate(self.paragraphs[:1000])],
                "nodes_truncated": len(self.paragraphs) > 1000,
                "table_shapes": [{"table": i, "rows": len(t.findall(f"{{{W}}}tr")),
                                  "columns_by_row": [len(r.findall(f"{{{W}}}tc")) for r in t.findall(f"{{{W}}}tr")][:200]}
                                 for i, t in enumerate(self.tables[:100])],
                "locator_scope": "main_document_xml; paragraph IDs require the source hash",
                "validation": validation()}

    def _paragraph(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r"p:\d+", identifier):
            fail("invalid_patch", "paragraph_id must be p:<zero-based index>")
        index = int(identifier[2:])
        if index >= len(self.paragraphs):
            fail("not_found", "Paragraph does not exist")
        paragraph = self.paragraphs[index]
        if paragraph not in self.root.iter(f"{{{W}}}p"):
            fail("conflict", "An earlier patch removed this paragraph")
        return paragraph

    def read(self, args):
        ids = args.get("block_ids", [f"p:{i}" for i in range(min(100, len(self.paragraphs)))])
        if not isinstance(ids, list) or len(ids) > 500:
            fail("invalid_request", "Read at most 500 paragraph IDs")
        blocks = [{"id": ident, "text": _pt(self._paragraph(ident))} for ident in ids]
        if sum(len(b["text"]) for b in blocks) > 500_000:
            fail("too_large", "Read fewer document blocks")
        return {"blocks": blocks, "render": "not_checked"}

    @staticmethod
    def _replace(p, new):
        # Fields, hyperlinks, drawings, tracked changes and mixed-content runs
        # cannot be flattened without violating their semantics.
        if any(n.tag not in (f"{{{W}}}pPr", f"{{{W}}}r") for n in p):
            fail("unsupported", "Target paragraph contains fields, links or revision markup")
        runs = p.findall(f"{{{W}}}r")
        if any(n.tag not in (f"{{{W}}}rPr", f"{{{W}}}t") for r in runs for n in r):
            fail("unsupported", "Target paragraph contains non-text run content")
        if not runs:
            runs = [ET.SubElement(p, f"{{{W}}}r")]
        for r in runs:
            for n in list(r):
                if n.tag == f"{{{W}}}t":
                    r.remove(n)
        value = ET.SubElement(runs[0], f"{{{W}}}t", {f"{{{XML}}}space": "preserve"})
        value.text = new

    def patch(self, patches):
        self.package.edit_guard()
        if "word/settings.xml" in self.package.parts:
            settings = self.package.xml("word/settings.xml")
            if settings.find(f"{{{W}}}documentProtection") is not None:
                fail("unsupported", "Protected DOCX editing is unavailable")
        changes = []
        for patch in patches:
            op = patch.get("op")
            if op == "replace_paragraph":
                fields(patch, ("op", "paragraph_id", "expected_text", "text"), ("evidence", "trust"))
                p = self._paragraph(patch["paragraph_id"])
                old, new = _pt(p), text(patch["text"])
                if old != text(patch["expected_text"]):
                    fail("conflict", "Paragraph no longer matches expected_text")
                self._replace(p, new)
                locator = patch["paragraph_id"]
            elif op == "replace_cell":
                fields(patch, ("op", "table", "row", "column", "expected_text", "text"), ("evidence", "trust"))
                ti, ri, ci = (integer(patch[k]) for k in ("table", "row", "column"))
                try:
                    cell = self.tables[ti].findall(f"{{{W}}}tr")[ri].findall(f"{{{W}}}tc")[ci]
                except IndexError:
                    fail("not_found", "Table cell does not exist")
                if any(n.tag in (f"{{{W}}}vMerge", f"{{{W}}}gridSpan", f"{{{W}}}tbl") for n in cell.iter()):
                    fail("unsupported", "Editing merged or nested table cells is unavailable")
                paragraphs = cell.findall(f"{{{W}}}p")
                if not paragraphs:
                    fail("unsupported", "Cell has no editable paragraph")
                old, new = "\n".join(_pt(p) for p in paragraphs), text(patch["text"])
                if old != text(patch["expected_text"]):
                    fail("conflict", "Cell no longer matches expected_text")
                for p in paragraphs:
                    self._replace(p, "")
                self._replace(paragraphs[0], new)
                for p in paragraphs[1:]:
                    cell.remove(p)
                locator = f"t:{ti}/r:{ri}/c:{ci}"
            else:
                fail("unsupported", "Unsupported DOCX operation")
            changes.append({"op": op, "locator": locator, "before": old, "after": new,
                            "trust": "model_proposed", "evidence": patch.get("evidence", [])})
        self.package.put("word/document.xml", self.root)
        return self.package.save(), changes, validation(layout="not_checked", native_track_changes=False,
                                                       text_style="replacement uses the first text run style")


def _coord(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z]{1,3}[1-9]\d{0,6}", value):
        fail("invalid_patch", "A1-style cell locator required")
    letters, row = re.match(r"([A-Z]+)(\d+)", value).groups()
    column = 0
    for char in letters:
        column = column * 26 + ord(char) - 64
    if column > 16384 or int(row) > 1048576:
        fail("invalid_patch", "Cell is outside XLSX limits")
    return int(row), column


def _a1(row, col):
    letters = ""
    while col:
        col, rem = divmod(col - 1, 26)
        letters = chr(65 + rem) + letters
    return letters + str(row)


def _range(value):
    if not isinstance(value, str):
        fail("invalid_request", "Cell range required")
    corners = value.split(":")
    if len(corners) not in (1, 2):
        fail("invalid_request", "Only rectangular ranges are supported")
    start, end = _coord(corners[0]), _coord(corners[-1])
    if start[0] > end[0] or start[1] > end[1] or (end[0]-start[0]+1)*(end[1]-start[1]+1) > 2000:
        fail("too_large", "Read/write at most 2000 cells per range")
    return [[_a1(r, c) for c in range(start[1], end[1]+1)] for r in range(start[0], end[0]+1)]


def _typed(value):
    fields(value, ("type", "value"))
    kind, item = value["type"], value["value"]
    if kind == "blank" and item is None:
        return value
    if kind == "text":
        text(item, maximum=32767)
    elif kind == "number":
        number(item)
    elif kind == "boolean" and type(item) is bool:
        pass
    elif kind == "formula":
        text(item, maximum=8192)
        if not item.startswith("=") or any(s in item for s in ("[", "]", "|", "://", "\\")):
            fail("unsupported", "Only explicit formulas without external references are supported")
        allowed = {"SUM", "AVERAGE", "MIN", "MAX", "COUNT", "COUNTA", "IF", "AND", "OR", "NOT", "ROUND", "ROUNDUP", "ROUNDDOWN", "ABS", "SQRT", "POWER", "SUMIF", "SUMIFS", "COUNTIF", "COUNTIFS", "IFERROR", "LEFT", "RIGHT", "LEN", "TRIM", "TEXT", "VALUE", "CONCAT", "CONCATENATE", "DATE", "YEAR", "MONTH", "DAY"}
        functions = re.findall(r"([A-Za-z_][A-Za-z0-9_.]*)\s*\(", item)
        if any(f.upper() not in allowed for f in functions):
            fail("unsupported", "Formula function is outside the supported allowlist")
    else:
        fail("invalid_patch", "Unsupported typed cell value")
    return value


class Spreadsheet:
    def __init__(self, data):
        import posixpath
        self.package = Package(data)
        self.book = self.package.xml("xl/workbook.xml")
        relationships = self.package.xml("xl/_rels/workbook.xml.rels")
        targets = {r.attrib["Id"]: r.attrib.get("Target", "") for r in relationships if r.attrib.get("TargetMode") != "External"}
        self.sheets = {}
        for sheet in self.book.findall(f"{{{S}}}sheets/{{{S}}}sheet"):
            target = targets.get(sheet.attrib.get(f"{{{R}}}id"), "")
            part = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
            if not part.startswith("xl/") or part not in self.package.parts:
                fail("invalid_document", "Worksheet relationship is invalid")
            root = self.package.xml(part)
            self.sheets[sheet.attrib["name"]] = (part, root)
        self.shared = []
        if "xl/sharedStrings.xml" in self.package.parts:
            self.shared = ["".join(n.text or "" for n in si.iter(f"{{{S}}}t")) for si in self.package.xml("xl/sharedStrings.xml")]

    def _sheet(self, name):
        if not isinstance(name, str) or name not in self.sheets:
            fail("not_found", "Worksheet does not exist")
        return self.sheets[name]

    def _value(self, cell):
        if cell is None:
            return {"type": "blank", "value": None}
        formula, value = cell.find(f"{{{S}}}f"), cell.findtext(f"{{{S}}}v")
        if formula is not None:
            return {"type": "formula", "value": "=" + (formula.text or "")}
        kind = cell.get("t", "n")
        if kind == "inlineStr":
            return {"type": "text", "value": "".join(n.text or "" for n in cell.iter(f"{{{S}}}t"))}
        if value is None:
            return {"type": "blank", "value": None}
        if kind == "s":
            try:
                return {"type": "text", "value": self.shared[int(value)]}
            except (ValueError, IndexError):
                fail("invalid_document", "Invalid shared string reference")
        if kind == "b":
            return {"type": "boolean", "value": value == "1"}
        if kind in ("str", "e", "d"):
            return {"type": {"str": "text", "e": "error", "d": "date"}[kind], "value": value}
        try:
            numeric = float(value)
            return {"type": "number", "value": int(numeric) if numeric.is_integer() else numeric}
        except ValueError:
            fail("invalid_document", "Invalid numeric cell")

    def inspect(self):
        return {"format": "xlsx", "sheets": [{"name": n, "used_range": r.find(f"{{{S}}}dimension").get("ref") if r.find(f"{{{S}}}dimension") is not None else None,
                    "formula_count": len(list(r.iter(f"{{{S}}}f"))), "merged_ranges": [m.get("ref") for m in r.findall(f"{{{S}}}mergeCells/{{{S}}}mergeCell")],
                    "protected": r.find(f"{{{S}}}sheetProtection") is not None} for n, (_, r) in self.sheets.items()],
                "validation": validation(recalculation="not_recalculated"), "advanced_parts_preserved": True}

    def read(self, args):
        _, root = self._sheet(args.get("sheet"))
        coords = _range(args.get("range", "A1:J20"))
        cells = {c.get("r"): c for c in root.iter(f"{{{S}}}c")}
        return {"sheet": args["sheet"], "range": args.get("range", "A1:J20"),
                "rows": [[{"cell": c, **self._value(cells.get(c)),
                           "cached_value": cells[c].findtext(f"{{{S}}}v") if c in cells and cells[c].find(f"{{{S}}}f") is not None else None} for c in row] for row in coords],
                "cached_values_status": "stored_unverified", "recalculation": "not_recalculated"}

    def _set(self, sheet, address, expected, value):
        part, root = self._sheet(sheet)
        row_index, col_index = _coord(address)
        if root.find(f"{{{S}}}sheetProtection") is not None:
            fail("unsupported", "Protected worksheet editing is unavailable")
        for merge in root.findall(f"{{{S}}}mergeCells/{{{S}}}mergeCell"):
            corners = merge.get("ref", "").split(":")
            first, last = _coord(corners[0]), _coord(corners[-1])
            if first[0] <= row_index <= last[0] and first[1] <= col_index <= last[1]:
                fail("unsupported", "Editing merged cells requires a separate merge-aware operation")
        data = root.find(f"{{{S}}}sheetData")
        if data is None:
            fail("invalid_document", "Worksheet has no sheetData")
        row = next((r for r in data if r.get("r") == str(row_index)), None)
        cell = next((c for c in row if c.get("r") == address), None) if row is not None else None
        old = self._value(cell)
        if old != expected:
            fail("conflict", f"Cell {sheet}!{address} no longer matches its expected value")
        # Shared/array formulas require group-aware editing, and are never erased.
        if cell is not None:
            formula = cell.find(f"{{{S}}}f")
            if formula is not None and formula.attrib:
                fail("unsupported", "Shared or array formulas are not editable")
            if any(c.tag not in (f"{{{S}}}f", f"{{{S}}}v", f"{{{S}}}is") for c in cell):
                fail("unsupported", "Target cell has unsupported extension content")
        value = _typed(value)
        if row is None:
            row = ET.Element(f"{{{S}}}row", {"r": str(row_index)})
            position = next((i for i, r in enumerate(data) if int(r.get("r", "0")) > row_index), len(data))
            data.insert(position, row)
        if cell is None:
            cell = ET.Element(f"{{{S}}}c", {"r": address})
            position = next((i for i, c in enumerate(row) if _coord(c.get("r"))[1] > col_index), len(row))
            row.insert(position, cell)
        for child in list(cell):
            if child.tag in (f"{{{S}}}v", f"{{{S}}}f", f"{{{S}}}is"):
                cell.remove(child)
        cell.attrib.pop("t", None)
        kind, item = value["type"], value["value"]
        if kind == "text":
            cell.set("t", "inlineStr")
            ET.SubElement(ET.SubElement(cell, f"{{{S}}}is"), f"{{{S}}}t", {f"{{{XML}}}space": "preserve"}).text = item
        elif kind == "formula":
            ET.SubElement(cell, f"{{{S}}}f").text = item[1:]
        elif kind != "blank":
            cell.set("t", "b" if kind == "boolean" else "n")
            ET.SubElement(cell, f"{{{S}}}v").text = str(int(item)) if kind == "boolean" else str(item)
        dimension = root.find(f"{{{S}}}dimension")
        if dimension is not None:
            positions = [_coord(c.get("r")) for c in root.iter(f"{{{S}}}c")]
            dimension.set("ref", _a1(min(x[0] for x in positions), min(x[1] for x in positions)) + ":" + _a1(max(x[0] for x in positions), max(x[1] for x in positions)))
        self.package.put(part, root)
        return {"op": "set_cell", "locator": f"{sheet}!{address}", "before": old, "after": value, "trust": "model_proposed"}

    def patch(self, patches):
        self.package.edit_guard()
        protection = self.book.find(f"{{{S}}}workbookProtection")
        if protection is not None and any(v not in ("0", "false", "off", "") for v in protection.attrib.values()):
            fail("unsupported", "Protected workbook editing is unavailable")
        changes = []
        for patch in patches:
            op = patch.get("op")
            if op == "set_cell":
                fields(patch, ("op", "sheet", "cell", "expected", "value"), ("evidence", "trust"))
                changes.append({**self._set(patch["sheet"], patch["cell"], patch["expected"], patch["value"]), "evidence": patch.get("evidence", [])})
            elif op == "set_range":
                fields(patch, ("op", "sheet", "range", "expected", "values"), ("evidence", "trust"))
                coords = _range(patch["range"])
                for matrix in (patch["expected"], patch["values"]):
                    if not isinstance(matrix, list) or len(matrix) != len(coords) or any(not isinstance(r, list) or len(r) != len(coords[i]) for i, r in enumerate(matrix)):
                        fail("invalid_patch", "Range matrix dimensions do not match")
                for ri, row in enumerate(coords):
                    for ci, cell in enumerate(row):
                        changes.append({**self._set(patch["sheet"], cell, patch["expected"][ri][ci], patch["values"][ri][ci]), "evidence": patch.get("evidence", [])})
            else:
                fail("unsupported", "Unsupported XLSX operation")
            if len(changes) > 2000:
                fail("too_large", "A patch may change at most 2000 cells")
        calc = self.book.find(f"{{{S}}}calcPr")
        if calc is None:
            calc = ET.SubElement(self.book, f"{{{S}}}calcPr")
        # Only the newly generated copy changes. These flags request a future
        # engine calculation; they do not establish refreshed formula caches.
        calc.set("calcMode", "auto")
        calc.set("fullCalcOnLoad", "1")
        calc.set("forceFullCalc", "1")
        self.package.put("xl/workbook.xml", self.book)
        return self.package.save(), changes, validation(recalculation="not_recalculated", cached_values="may_be_stale",
                                                       formula_guard="external_reference_and_function_allowlist", formula_syntax="not_checked")
