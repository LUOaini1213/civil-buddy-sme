"""Admission and saved-result verification for the host's optional Excel broker.

This module never opens Excel, evaluates a formula, loads/saves a workbook with
openpyxl, or establishes an OS sandbox. The host must attest its own broker and
receipt. A calculation receipt establishes execution and persisted results, not
the correctness of an engineering design. Unknown workbook features fail closed.
"""
from __future__ import annotations

import hashlib
import json
import math
import posixpath
import re
from decimal import Decimal, InvalidOperation

from .common import fail
from .ooxml import Package, S, R

POLICY_VERSION = "civil-xlsx-engine-v1"
LIMITS = {"package_parts": 128, "expanded_bytes": 60_000_000,
          "sheets": 32, "cells": 100_000, "formulas": 5_000,
          "formula_characters": 2_048, "total_formula_characters": 1_000_000,
          "formula_depth": 64, "range_cells": 100_000, "text_characters": 32_767}
P = "http://schemas.openxmlformats.org/package/2006/relationships"
C = "http://schemas.openxmlformats.org/package/2006/content-types"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
CORE = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
APP = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
VT = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
DC = "http://purl.org/dc/elements/1.1/"
DCT = "http://purl.org/dc/terms/"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
XML = "http://www.w3.org/XML/1998/namespace"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
X14AC = "http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac"
X15AC = "http://schemas.microsoft.com/office/spreadsheetml/2010/11/ac"
XR = "http://schemas.microsoft.com/office/spreadsheetml/2014/revision"
XR2 = "http://schemas.microsoft.com/office/spreadsheetml/2015/revision2"
XR6 = "http://schemas.microsoft.com/office/spreadsheetml/2016/revision6"
XR10 = "http://schemas.microsoft.com/office/spreadsheetml/2016/revision10"
CALCF = "http://schemas.microsoft.com/office/spreadsheetml/2018/calcfeatures"
REL = R + "/"
FUNCTIONS = {
    "SUM", "MIN", "MAX", "AVERAGE", "ABS", "ROUND", "ROUNDUP", "ROUNDDOWN", "SQRT",
    "IF", "IFERROR", "AND", "OR", "NOT", "COUNT", "COUNTA", "COUNTBLANK", "COUNTIF",
    "SUMIF", "SUMIFS", "COUNTIFS", "INDEX", "MATCH", "VLOOKUP", "HLOOKUP",
}
STATIC_NAMES = {"_xlnm.Print_Area", "_xlnm.Print_Titles", "_xlnm._FilterDatabase"}

WORKBOOK_NODES = set("workbook fileVersion workbookPr workbookProtection bookViews workbookView sheets sheet definedNames definedName calcPr".split())
SHEET_NODES = set("worksheet sheetPr outlinePr pageSetUpPr tabColor dimension sheetViews sheetView pane selection sheetFormatPr cols col sheetData row c f v is t r rPr phoneticPr mergeCells mergeCell sheetProtection autoFilter filterColumn filters filter customFilters customFilter sortState sortCondition printOptions pageMargins pageSetup headerFooter oddHeader oddFooter evenHeader evenFooter firstHeader firstFooter rowBreaks colBreaks brk".split())
STRING_NODES = set("sst si t r rPr rFont charset family b i strike outline shadow condense extend color sz u vertAlign scheme phoneticPr".split())
STYLE_NODES = set("styleSheet numFmts numFmt fonts font name charset family b i strike outline shadow condense extend color sz u vertAlign scheme fills fill patternFill fgColor bgColor gradientFill stop borders border left right top bottom diagonal vertical horizontal start end cellStyleXfs cellXfs xf alignment protection cellStyles cellStyle dxfs dxf tableStyles tableStyle tableStyleElement colors indexedColors rgbColor mruColors".split())
THEME_NODES = set("theme themeElements clrScheme dk1 lt1 dk2 lt2 accent1 accent2 accent3 accent4 accent5 accent6 hlink folHlink sysClr srgbClr schemeClr prstClr tint shade satMod lumMod lumOff alpha alphaModFix fontScheme majorFont minorFont latin ea cs font fmtScheme fillStyleLst solidFill gradFill gsLst gs lin path fillToRect lnStyleLst ln prstDash miter headEnd tailEnd effectStyleLst effectStyle effectLst outerShdw scene3d camera lightRig rot sp3d bevelT bevelB bgFillStyleLst objectDefaults extraClrSchemeLst".split())
CORE_NODES = {"coreProperties", "title", "subject", "creator", "keywords", "description", "lastModifiedBy", "revision", "created", "modified", "lastPrinted", "category", "contentStatus", "identifier", "language", "version"}
APP_NODES = set("Properties Application AppVersion DocSecurity ScaleCrop HeadingPairs TitlesOfParts Company LinksUpToDate SharedDoc HyperlinksChanged TotalTime Pages Words Characters CharactersWithSpaces Lines Paragraphs Template Manager".split())
VT_NODES = {"vector", "variant", "lpstr", "lpwstr", "i4"}


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _namespace(tag):
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else ""


def _unsupported(message):
    fail("unsupported_excel_engine", message)


def _integer(value, label, minimum=0, maximum=1_048_576):
    try:
        result = int(value)
    except (ValueError, TypeError):
        _unsupported(f"Invalid {label}")
    if str(result) != str(value) or not minimum <= result <= maximum:
        _unsupported(f"Invalid {label}")
    return result


def _decimal(value):
    if not isinstance(value, str) or len(value) > 256:
        _unsupported("Numeric serialization exceeds the supported length")
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError, TypeError):
        _unsupported("Invalid numeric cell or formula literal")
    if not number.is_finite() or number.copy_abs() > Decimal("1e100"):
        _unsupported("Numeric value is nonfinite or outside the supported magnitude")
    if number and number.copy_abs() < Decimal("1e-100"):
        _unsupported("Numeric value is below the supported magnitude")
    if not number:
        return "0"
    sign, digits, exponent = number.as_tuple()
    digits = list(digits)
    while digits[-1] == 0:
        digits.pop()
        exponent += 1
    # Context-free canonicalization preserves every input digit; Decimal's
    # default-context normalize() can silently round long numeric inputs.
    return ("-" if sign else "") + "".join(map(str, digits)) + "E" + str(exponent)


def _cell(address):
    match = re.fullmatch(r"(\$?)([A-Za-z]{1,3})(\$?)([1-9][0-9]{0,6})", address)
    if not match:
        _unsupported(f"Unsupported cell reference: {address[:80]}")
    column = 0
    for ch in match[2].upper():
        column = column * 26 + ord(ch) - 64
    row = int(match[4])
    if column > 16_384 or row > 1_048_576:
        _unsupported("Cell reference exceeds XLSX worksheet bounds")
    return (match[2].upper() + str(row), column, row, bool(match[1]), bool(match[3]))


def _sheet_prefix(text, current, names):
    if "!" not in text:
        return current, text
    # Exclamation marks inside quoted sheet names are valid; split at the final
    # delimiter. Quotes are decoded rather than discarded by string patterns.
    prefix, address = text.rsplit("!", 1)
    if prefix.startswith("'") and prefix.endswith("'"):
        inner = prefix[1:-1]
        if re.search(r"(?<!')'(?!')", inner):
            _unsupported("Malformed quoted worksheet reference")
        prefix = inner.replace("''", "'")
    elif not re.fullmatch(r"[^\s'!\[\]:/\\?*]+", prefix):
        _unsupported("Unsupported worksheet reference")
    resolved = names.get(prefix.casefold())
    if resolved is None:
        _unsupported("Formula refers to a worksheet outside this workbook")
    return resolved, address


def _reference(text, current, names, *, static_name=False):
    sheet, address = _sheet_prefix(text, current, names)
    segments = address.split(":")
    if len(segments) > 2:
        _unsupported("Unsupported reference operator")
    if static_name and len(segments) == 2 and all(re.fullmatch(r"\$[1-9][0-9]{0,6}", p) for p in segments):
        ends = [int(p[1:]) for p in segments]
        if not 1 <= ends[0] <= ends[1] <= 1_048_576:
            _unsupported("Invalid static print row range")
        return ("rows", sheet, *ends)
    if static_name and len(segments) == 2 and all(re.fullmatch(r"\$[A-Za-z]{1,3}", p) for p in segments):
        ends = [_cell(p + "$1") for p in segments]
        if ends[0][1] > ends[1][1]:
            _unsupported("Invalid static print column range")
        return ("columns", sheet, ends[0][1], ends[1][1])
    ends = [_cell(part) for part in segments]
    if len(ends) == 2:
        if ends[0][1] > ends[1][1] or ends[0][2] > ends[1][2]:
            _unsupported("Reversed rectangular ranges are unsupported")
        count = (ends[1][1] - ends[0][1] + 1) * (ends[1][2] - ends[0][2] + 1)
        if not static_name and count > LIMITS["range_cells"]:
            _unsupported("Formula range exceeds the supported cell limit")
    if static_name and not all(end[3] and end[4] for end in ends):
        _unsupported("Static built-in names require absolute references")
    return ("reference", sheet, tuple((e[0], e[3], e[4]) for e in ends))


class _Formula:
    """Pure lexer plus complete bounded grammar; never evaluates formulas."""
    def __init__(self, value, sheet, names):
        from openpyxl.formula.tokenizer import Tokenizer, TokenizerError
        if not isinstance(value, str) or not value or len(value) > LIMITS["formula_characters"]:
            _unsupported("Formula is empty or exceeds the supported length")
        try:
            tokens = Tokenizer("=" + value).items
        except (TokenizerError, IndexError, ValueError):
            _unsupported("Malformed or unsupported formula")
        # Whitespace between operands is Excel's intersection operator. Keeping
        # it until adjacency validation prevents silently turning A1 B1 into A1B1.
        self.tokens = [t for t in tokens if t.type != "WHITE-SPACE"]
        self.index, self.sheet, self.names = 0, sheet, names
        self.tree = self.expression(0, 0)
        if self.index != len(self.tokens):
            _unsupported("Unsupported formula syntax or reference operator")

    def expression(self, minimum, depth):
        if depth > LIMITS["formula_depth"] or self.index >= len(self.tokens):
            _unsupported("Formula nesting or syntax exceeds the supported profile")
        token = self.tokens[self.index]
        self.index += 1
        if token.type == "OPERATOR-PREFIX" and token.value in {"+", "-"}:
            left = ("unary", token.value, self.expression(60, depth + 1))
        elif token.type == "PAREN" and token.subtype == "OPEN":
            left = self.expression(0, depth + 1)
            self.close("PAREN")
        elif token.type == "FUNC" and token.subtype == "OPEN":
            function = token.value[:-1].upper()
            if function not in FUNCTIONS:
                _unsupported(f"Unsupported Excel function: {function[:80]}")
            arguments = []
            after_separator = False
            while True:
                if self.index < len(self.tokens) and self.tokens[self.index].type == "FUNC" and self.tokens[self.index].subtype == "CLOSE":
                    if not arguments or after_separator:
                        _unsupported("Empty function arguments are unsupported")
                    break
                arguments.append(self.expression(0, depth + 1))
                after_separator = False
                if self.index < len(self.tokens) and self.tokens[self.index].type == "SEP" and self.tokens[self.index].subtype == "ARG":
                    self.index += 1
                    after_separator = True
                else:
                    break
            self.close("FUNC")
            left = ("function", function, tuple(arguments))
        elif token.type == "OPERAND":
            if token.subtype == "NUMBER":
                left = ("number", _decimal(token.value))
            elif token.subtype == "LOGICAL" and token.value.upper() in {"TRUE", "FALSE"}:
                left = ("boolean", token.value.upper() == "TRUE")
            elif token.subtype == "TEXT" and token.value.startswith('"') and token.value.endswith('"'):
                left = ("text", token.value[1:-1].replace('""', '"'))
            elif token.subtype == "RANGE":
                left = _reference(token.value, self.sheet, self.names)
            else:
                _unsupported("Error literals, arrays or unresolved operands are unsupported")
        else:
            _unsupported("Unsupported formula operand")
        precedence = {"=": 10, "<>": 10, "<": 10, ">": 10, "<=": 10, ">=": 10,
                      "&": 20, "+": 30, "-": 30, "*": 40, "/": 40, "^": 50}
        while self.index < len(self.tokens):
            token = self.tokens[self.index]
            if token.type == "OPERATOR-POSTFIX" and token.value == "%":
                if 55 < minimum:
                    break
                self.index += 1
                left = ("percent", left)
            elif token.type == "OPERATOR-INFIX" and token.value in precedence and precedence[token.value] >= minimum:
                self.index += 1
                # Excel uses left association for equal-precedence operators.
                right = self.expression(precedence[token.value] + 1, depth + 1)
                left = ("operator", token.value, left, right)
            else:
                break
        return left

    def close(self, kind):
        if self.index >= len(self.tokens) or self.tokens[self.index].type != kind or self.tokens[self.index].subtype != "CLOSE":
            _unsupported("Unbalanced formula parentheses or unsupported arguments")
        self.index += 1


def _validate_tree(root, namespaces, permitted, label):
    for node in root.iter():
        if _namespace(node.tag) not in namespaces or _local(node.tag) not in permitted:
            _unsupported(f"Unsupported {label} element: {_local(node.tag)}")
        for key in node.attrib:
            if _namespace(key) not in {"", XML, R, XSI}:
                _unsupported(f"Unsupported {label} extension attribute")
            if _local(key) == "base":
                _unsupported("XML base URI overrides are unsupported")


def _metadata(root, kind):
    """Validate exact known Office metadata shapes in the private parsed tree.

    Microsoft MS-XLSX sections 2.2.4.9, 2.2.4.11, 2.4.72 and 2.4.82 describe
    these records. They contain no workbook expressions. Removing them from this
    in-memory semantic view never changes the supplied or saved package bytes.
    Unknown extensions, alternate branches, attributes and children stay denied.
    """
    if kind not in {"workbook", "worksheet", "styles"}:
        return
    guid = r"\{[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\}"
    for node in root.iter():
        for key, value in list(node.attrib.items()):
            namespace, local = _namespace(key), _local(key)
            allowed = False
            if key == f"{{{MC}}}Ignorable" and node is root:
                allowed = bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?: [A-Za-z][A-Za-z0-9_]*)*", value))
            elif namespace == X14AC and local == "dyDescent" and node.tag in {f"{{{S}}}row", f"{{{S}}}sheetFormatPr"}:
                try:
                    descent = float(value)
                    allowed = math.isfinite(descent) and 0 <= descent <= 255
                except ValueError:
                    pass
            elif namespace == X14AC and local == "knownFonts" and node.tag in {f"{{{S}}}fonts", f"{{{S}}}font"}:
                allowed = value in {"0", "1", "true", "false"}
            elif ((namespace == XR and node.tag == f"{{{S}}}worksheet")
                  or (namespace == XR2 and node.tag == f"{{{S}}}workbookView")) and local == "uid":
                allowed = bool(re.fullmatch(guid, value))
            if allowed:
                del node.attrib[key]
    for child in list(root):
        if kind == "workbook" and child.tag == f"{{{MC}}}AlternateContent":
            if child.attrib or len(child) != 1 or child[0].tag != f"{{{MC}}}Choice" or set(child[0].attrib) != {"Requires"} or len(child[0]) != 1:
                _unsupported("Unsupported alternate workbook content")
            absolute = child[0][0]
            if (absolute.tag != f"{{{X15AC}}}absPath" or set(absolute.attrib) != {"url"} or len(absolute)
                    or len(absolute.get("url", "")) > 4096 or not re.match(r"^(?:[A-Za-z]:[\\/]|\\\\)", absolute.get("url", ""))):
                _unsupported("Unsupported alternate workbook path metadata")
            root.remove(child)
        elif kind == "workbook" and child.tag == f"{{{XR}}}revisionPtr":
            allowed = {"revIDLastSave", "documentId", f"{{{XR6}}}coauthVersionLast", f"{{{XR6}}}coauthVersionMax", f"{{{XR10}}}uidLastSave"}
            if len(child) or not {"revIDLastSave", "documentId"} <= child.attrib.keys() or set(child.attrib) - allowed:
                _unsupported("Unsupported workbook revision metadata")
            _integer(child.get("revIDLastSave"), "revision metadata", maximum=2**64 - 1)
            if len(child.get("documentId")) > 256:
                _unsupported("Oversized workbook revision metadata")
            for key in (f"{{{XR6}}}coauthVersionLast", f"{{{XR6}}}coauthVersionMax"):
                if key in child.attrib:
                    _integer(child.get(key), "coauthor metadata", maximum=2**32 - 1)
            if f"{{{XR10}}}uidLastSave" in child.attrib and not re.fullmatch(guid, child.get(f"{{{XR10}}}uidLastSave")):
                _unsupported("Invalid workbook revision identity")
            root.remove(child)
        elif child.tag == f"{{{S}}}extLst":
            if child.attrib or not len(child):
                _unsupported("Unsupported empty or attributed extension list")
            seen = set()
            for extension in child:
                identifier = extension.get("uri", "").upper()
                if extension.tag != f"{{{S}}}ext" or set(extension.attrib) != {"uri"} or len(extension) != 1 or identifier in seen:
                    _unsupported("Unsupported Office extension record")
                seen.add(identifier)
                value = extension[0]
                if kind == "workbook" and identifier == "{B58B0392-4F1F-4190-BB64-5DF3571DCE5F}":
                    if value.tag != f"{{{CALCF}}}calcFeatures" or value.attrib or not 1 <= len(value) <= 16:
                        _unsupported("Unsupported calculation-engine feature metadata")
                    names = {"microsoft.com:rd", "microsoft.com:single", "microsoft.com:fv", "microsoft.com:cnmtm", "microsoft.com:let_wf", "microsoft.com:lambda_wf", "microsoft.com:arraytext_wf"}
                    for feature in value:
                        if feature.tag != f"{{{CALCF}}}feature" or set(feature.attrib) != {"name"} or len(feature) or feature.get("name", "").casefold() not in names:
                            _unsupported("Unknown calculation-engine feature metadata")
                elif kind == "styles" and identifier in {"{EB79DEF2-80B8-43E5-95BD-54CBDDF9020C}", "{9260A510-F301-46A8-8635-F512D64BE5F5}"}:
                    slicer = identifier == "{EB79DEF2-80B8-43E5-95BD-54CBDDF9020C}"
                    expected = "{http://schemas.microsoft.com/office/spreadsheetml/2009/9/main}slicerStyles" if slicer else "{http://schemas.microsoft.com/office/spreadsheetml/2010/11/main}timelineStyles"
                    attribute = "defaultSlicerStyle" if slicer else "defaultTimelineStyle"
                    if value.tag != expected or set(value.attrib) != {attribute} or len(value) or not re.fullmatch(r"(?:Time)?SlicerStyle(?:Light|Dark)[1-9][0-9]?", value.get(attribute, "")):
                        _unsupported("Unsupported default slicer/timeline style metadata")
                else:
                    _unsupported("Unknown workbook or stylesheet extension")
            root.remove(child)


def _part_kind(name):
    if name == "[Content_Types].xml":
        return "types"
    if name in {"_rels/.rels", "xl/_rels/workbook.xml.rels"}:
        return "relationships"
    if name == "xl/workbook.xml":
        return "workbook"
    if re.fullmatch(r"xl/worksheets/sheet[1-9][0-9]*\.xml", name):
        return "worksheet"
    return {"xl/styles.xml": "styles", "xl/sharedStrings.xml": "strings",
            "xl/theme/theme1.xml": "theme", "xl/calcChain.xml": "chain",
            "docProps/core.xml": "core", "docProps/app.xml": "app"}.get(name)


CONTENT_TYPES = {
    "workbook": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    "worksheet": "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml",
    "styles": "application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml",
    "strings": "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml",
    "theme": "application/vnd.openxmlformats-officedocument.theme+xml",
    "chain": "application/vnd.openxmlformats-officedocument.spreadsheetml.calcChain+xml",
    "core": "application/vnd.openxmlformats-package.core-properties+xml",
    "app": "application/vnd.openxmlformats-officedocument.extended-properties+xml",
}


def _package(parts):
    if len(parts) > LIMITS["package_parts"] or sum(map(len, parts.values())) > LIMITS["expanded_bytes"]:
        _unsupported("XLSX package exceeds the Excel engine profile limits")
    if len({name.casefold() for name in parts}) != len(parts):
        _unsupported("Case-ambiguous package members are unsupported")
    for name in parts:
        if any(c in name for c in "\\%:#?\0") or _part_kind(name) is None:
            _unsupported(f"Unsupported package part: {name[:120]}")
    package = object.__new__(Package)
    package.parts, package.namespaces = parts, {}
    roots = {name: package.xml(name) for name in parts}
    for name, root in roots.items():
        kind = _part_kind(name)
        _metadata(root, kind)
        policy = {
            "types": ({C}, {"Types", "Default", "Override"}),
            "relationships": ({P}, {"Relationships", "Relationship"}),
            "workbook": ({S}, WORKBOOK_NODES), "worksheet": ({S}, SHEET_NODES),
            "styles": ({S}, STYLE_NODES), "strings": ({S}, STRING_NODES),
            "theme": ({A}, THEME_NODES), "chain": ({S}, {"calcChain", "c"}),
            "core": ({CORE, DC, DCT}, CORE_NODES), "app": ({APP, VT}, APP_NODES | VT_NODES),
        }[kind]
        _validate_tree(root, *policy, kind)
        if kind in {"workbook", "worksheet", "styles", "strings", "types", "relationships"}:
            repeatable = {"sheet", "definedName", "c", "row", "col", "si", "Default", "Override", "Relationship"}
            seen = set()
            for child in root:
                if child.tag in seen and _local(child.tag) not in repeatable:
                    _unsupported(f"Duplicate {kind} structural element")
                seen.add(child.tag)
    required = {"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl/styles.xml"}
    if not required <= roots.keys():
        _unsupported("The required ordinary XLSX parts are absent")
    types = roots["[Content_Types].xml"]
    if types.tag != f"{{{C}}}Types":
        _unsupported("Invalid content-types root")
    defaults, overrides = {}, {}
    for node in types:
        key = node.get("Extension") if _local(node.tag) == "Default" else node.get("PartName")
        destination = defaults if _local(node.tag) == "Default" else overrides
        if not key or key in destination:
            _unsupported("Duplicate or invalid content type declarations")
        destination[key] = node.get("ContentType")
    if any(key not in {"xml", "rels"} for key in defaults):
        _unsupported("Unsupported default package content type")
    if defaults.get("xml") not in {None, "application/xml"} or defaults.get("rels") != "application/vnd.openxmlformats-package.relationships+xml":
        _unsupported("Unexpected package default content type")
    if any(not key.startswith("/") or key[1:] not in parts for key in overrides):
        _unsupported("Content type refers to an unknown package part")
    for name in parts:
        kind = _part_kind(name)
        if kind in CONTENT_TYPES and overrides.get("/" + name) != CONTENT_TYPES[kind]:
            _unsupported(f"Unexpected or missing content type for {name}")
    relationships = {}
    expected = {
        "_rels/.rels": {REL + "officeDocument": "xl/workbook.xml",
                         "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties": "docProps/core.xml",
                         REL + "extended-properties": "docProps/app.xml"},
        "xl/_rels/workbook.xml.rels": {REL + "worksheet": None, REL + "styles": "xl/styles.xml",
                                        REL + "sharedStrings": "xl/sharedStrings.xml", REL + "theme": "xl/theme/theme1.xml",
                                        REL + "calcChain": "xl/calcChain.xml"},
    }
    reached = {"[Content_Types].xml", "_rels/.rels", "xl/_rels/workbook.xml.rels"}
    for name, allowed in expected.items():
        root = roots[name]
        if root.tag != f"{{{P}}}Relationships":
            _unsupported("Invalid relationships root")
        ids, singleton = {}, set()
        for node in root:
            identifier, target, reltype = node.get("Id"), node.get("Target", ""), node.get("Type")
            if not identifier or identifier in ids or node.get("TargetMode", "Internal") != "Internal":
                _unsupported("Duplicate, external or invalid relationship")
            if reltype not in allowed or not target or any(c in target for c in "\\%:#?\0"):
                _unsupported("Unsupported relationship type or target")
            resolved = target.lstrip("/") if target.startswith("/") else posixpath.normpath(("xl/" if name.startswith("xl/") else "") + target)
            if resolved not in parts or ".." in resolved.split("/"):
                _unsupported("Relationship target is absent or outside the package")
            fixed = allowed[reltype]
            if fixed is not None and resolved != fixed:
                _unsupported("Relationship target does not match its declared type")
            if fixed is None and _part_kind(resolved) != "worksheet":
                _unsupported("Non-worksheet sheet relationships are unsupported")
            if fixed is not None and reltype in singleton:
                _unsupported("Duplicate singleton package relationship")
            singleton.add(reltype)
            ids[identifier] = resolved
            reached.add(resolved)
        relationships[name] = ids
    if set(parts) != reached:
        _unsupported("Unreferenced or hidden package parts are unsupported")
    return roots, relationships


def _canon(node):
    content = node.text or ""
    if len(node) and not content.strip():
        content = ""
    return (_local(node.tag), tuple(sorted((_local(k), v) for k, v in node.attrib.items())),
            content, tuple(_canon(child) for child in node))


def _true(value):
    return value in {"1", "true", "True"}


def _attrs(node, defaults=None, omit=()):
    attributes = {_local(k): v for k, v in node.attrib.items() if _local(k) not in omit}
    boolean = {"diagonalUp", "diagonalDown", "outline", "wrapText", "shrinkToFit",
               "justifyLastLine", "locked", "hidden", "quotePrefix", "pivotButton",
               "customHeight", "customFormat", "collapsed", "thickTop", "thickBot",
               "bestFit", "customWidth", "zeroHeight", "summaryBelow", "summaryRight"}
    for key, default in (defaults or {}).items():
        value = attributes.get(key, default)
        if key in boolean:
            if value not in {"0", "1", "true", "false"}:
                _unsupported(f"Invalid boolean worksheet attribute: {key}")
            value = "1" if _true(value) else "0"
        if value == default:
            attributes.pop(key, None)
        else:
            attributes[key] = value
    return tuple(sorted(attributes.items()))


def _font(node, *, themed=False):
    children = []
    scheme = node.find(f"{{{S}}}scheme")
    theme_font = themed and scheme is not None and scheme.get("val") in {"major", "minor"}
    for child in node:
        name = _local(child.tag)
        # ISO/IEC29500 x:scheme selects the theme's major/minor definition. The
        # cached localized name can change on save; the complete theme itself
        # is compared separately, so a changed effective theme remains denied.
        if theme_font and name == "name":
            continue
        if name in {"b", "i", "strike", "outline", "shadow", "condense", "extend"}:
            if _true(child.get("val", "1")):
                children.append((name, True))
        elif name == "u" and child.get("val", "single") == "none":
            continue
        else:
            children.append(_canon(child))
    return tuple(sorted(children, key=lambda value: value[0]))


def _fill(node):
    pattern = node.find(f"{{{S}}}patternFill")
    if pattern is not None and pattern.get("patternType", "none") in {"none", "gray125"}:
        return ("pattern", pattern.get("patternType", "none"))
    return _canon(node)


def _border(node):
    children = []
    for child in node:
        if child.get("style") is not None:
            children.append(_canon(child))
    return (_attrs(node, {"diagonalUp": "0", "diagonalDown": "0", "outline": "1"}),
            tuple(sorted(children, key=lambda value: value[0])))


def _style_map(root, *, themed=False):
    from openpyxl.styles.numbers import BUILTIN_FORMATS
    def children(name):
        found = root.find(f"{{{S}}}{name}")
        return list(found) if found is not None else []
    fonts, fills, borders = map(children, ("fonts", "fills", "borders"))
    bases, formats = children("cellStyleXfs"), children("cellXfs")
    if not fonts or not fills or not borders or not formats:
        _unsupported("Incomplete worksheet styles")
    numfmts = dict(BUILTIN_FORMATS)
    custom_ids = set()
    for node in children("numFmts"):
        identifier = _integer(node.get("numFmtId"), "number format ID", maximum=65_535)
        if identifier in custom_ids or identifier < 164:
            _unsupported("Ambiguous custom number format")
        custom_ids.add(identifier)
        numfmts[identifier] = node.get("formatCode", "")
    def components(node):
        indexes = [_integer(node.get(key, "0"), key, maximum=65_535) for key in ("fontId", "fillId", "borderId", "numFmtId")]
        if indexes[0] >= len(fonts) or indexes[1] >= len(fills) or indexes[2] >= len(borders) or indexes[3] not in numfmts:
            _unsupported("Worksheet style refers to a missing component")
        alignment = node.find(f"{{{S}}}alignment")
        protection = node.find(f"{{{S}}}protection")
        return (_font(fonts[indexes[0]], themed=themed), _fill(fills[indexes[1]]), _border(borders[indexes[2]]), numfmts[indexes[3]],
                () if alignment is None else _attrs(alignment, {"horizontal": "general", "vertical": "bottom", "textRotation": "0", "wrapText": "0", "shrinkToFit": "0", "indent": "0", "relativeIndent": "0", "justifyLastLine": "0", "readingOrder": "0"}),
                () if protection is None else _attrs(protection, {"locked": "1", "hidden": "0"}))
    def style(node):
        values = dict(node.attrib)
        baseid = _integer(values.get("xfId", "0"), "base style ID", maximum=65_535)
        if bases:
            if baseid >= len(bases):
                _unsupported("Invalid base worksheet style")
            effective = dict(bases[baseid].attrib)
            effective.update(values)
        else:
            effective = values
        indexes = [_integer(effective.get(key, "0"), key, maximum=65_535) for key in ("fontId", "fillId", "borderId", "numFmtId")]
        if indexes[0] >= len(fonts) or indexes[1] >= len(fills) or indexes[2] >= len(borders) or indexes[3] not in numfmts:
            _unsupported("Worksheet style refers to a missing component")
        alignment = node.find(f"{{{S}}}alignment")
        protection = node.find(f"{{{S}}}protection")
        local = (_font(fonts[indexes[0]], themed=themed), _fill(fills[indexes[1]]), _border(borders[indexes[2]]), numfmts[indexes[3]],
                () if alignment is None else _attrs(alignment, {"horizontal": "general", "vertical": "bottom", "textRotation": "0", "wrapText": "0", "shrinkToFit": "0", "indent": "0", "relativeIndent": "0", "justifyLastLine": "0", "readingOrder": "0"}),
                () if protection is None else _attrs(protection, {"locked": "1", "hidden": "0"}))
        base = components(bases[baseid]) if bases else local
        # Keep the referenced base formatting and any explicit suppression of a
        # different local component. Missing vs true apply flags are Excel save
        # serialization changes; false may change effective style inheritance.
        suppressed = []
        for key, index in (("applyFont", 0), ("applyFill", 1), ("applyBorder", 2), ("applyNumberFormat", 3), ("applyAlignment", 4), ("applyProtection", 5)):
            flag = node.get(key)
            if flag not in {None, "0", "1", "true", "false"}:
                _unsupported("Invalid worksheet format application flag")
            if flag in {"0", "false"} and local[index] != base[index]:
                suppressed.append(key)
        return (*local, _attrs(node, {"quotePrefix": "0", "pivotButton": "0"}, omit={"fontId", "fillId", "borderId", "numFmtId", "xfId", "applyAlignment", "applyBorder", "applyFill", "applyFont", "applyNumberFormat", "applyProtection"}), base, tuple(suppressed))
    return [style(node) for node in formats]


def _strings(root):
    if root is None:
        return []
    if any(_local(node.tag) in {"r", "rPr", "phoneticPr"} for node in root.iter()):
        _unsupported("Rich or phonetic shared strings require a separate formatting workflow")
    return ["".join(node.text or "" for node in item.iter(f"{{{S}}}t")) for item in root]


def _worksheet_phonetic(root, stylesheet, *, themed=False):
    """Preserve Excel's worksheet default; actual string phonetic runs are denied.

    ISO/IEC 29500 x:phoneticPr may be a worksheet child and its fontId resolves
    into stylesheet fonts. This is distinct from rich/phonetic string content.
    Only the observed noConversion default is admitted in this policy version.
    """
    found = root.find(f"{{{S}}}phoneticPr")
    if found is None:
        return None
    if (set(found.attrib) - {"fontId", "type", "alignment"} or len(found)
            or (found.text or "").strip() or found.get("type") != "noConversion"
            or found.get("alignment") not in {None, "noControl", "left", "center", "distributed"}):
        _unsupported("Unsupported worksheet phonetic defaults")
    fonts = stylesheet.find(f"{{{S}}}fonts")
    identifier = _integer(found.get("fontId"), "worksheet phonetic font ID", maximum=len(fonts) - 1)
    return (found.get("type"), found.get("alignment"), _font(fonts[identifier], themed=themed))


def _sheet_properties(root):
    found = root.find(f"{{{S}}}sheetPr")
    if found is None:
        return ()
    values = []
    seen = set()
    for child in found:
        tag = _local(child.tag)
        if tag in seen or tag not in {"outlinePr", "pageSetUpPr", "tabColor"} or len(child):
            _unsupported("Unsupported or ambiguous worksheet properties")
        seen.add(tag)
        if tag == "outlinePr":
            value = _attrs(child, {"summaryBelow": "1", "summaryRight": "1"})
        else:
            value = _attrs(child)
        if value:
            values.append((tag, value))
    attributes = _attrs(found)
    return (attributes, tuple(sorted(values))) if attributes or values else ()


def _palette(root):
    from openpyxl.styles.colors import COLOR_INDEX
    found = root.find(f"{{{S}}}colors/{{{S}}}indexedColors")
    return tuple(COLOR_INDEX) if found is None else tuple(node.get("rgb") for node in found)


def _typed(cell, strings, *, cache=False):
    kind, value = cell.get("t", "n"), cell.find(f"{{{S}}}v")
    if kind == "e":
        _unsupported("Spreadsheet error cells require manual review")
    if cache and value is None:
        _unsupported("An Excel formula has no persisted result cache")
    if kind == "inlineStr" and not cache:
        return ("text", "".join(node.text or "" for node in cell.iter(f"{{{S}}}t")))
    if value is None:
        return ("blank", None)
    raw = value.text or ""
    if kind in {"n", ""}:
        if not raw:
            if cache:
                _unsupported("Numeric formula result cache is empty")
            return ("blank", None)
        return ("number", _decimal(raw))
    if kind == "b" and raw in {"0", "1"}:
        return ("boolean", raw == "1")
    if kind == "str":
        return ("text", raw)
    if kind == "s" and not cache:
        index = _integer(raw, "shared string ID", maximum=100_000)
        if index < len(strings):
            return ("text", strings[index])
    _unsupported("Unsupported or invalid worksheet value type")


def _names(book, sheets, names):
    container = book.find(f"{{{S}}}definedNames")
    result, seen = [], set()
    if container is None:
        return result
    for node in container:
        name = node.get("name")
        if name not in STATIC_NAMES or set(node.attrib) - {"name", "localSheetId", "hidden"}:
            _unsupported("Only static built-in print/filter names are supported")
        index = _integer(node.get("localSheetId"), "static name sheet ID", maximum=len(sheets) - 1)
        if (name, index) in seen:
            _unsupported("Duplicate static built-in name")
        seen.add((name, index))
        value = node.text or ""
        if len(value) > 4096:
            _unsupported("Static print/filter name exceeds the supported length")
        # Commas inside quoted worksheet names are ordinary name characters.
        segments = re.split(r",(?=(?:[^']*'[^']*')*[^']*$)", value)
        if len(segments) > 32:
            _unsupported("Static print/filter name has too many ranges")
        refs = []
        for segment in segments:
            if "!" not in segment:
                _unsupported("Static names must explicitly select their worksheet")
            ref = _reference(segment, sheets[index][0], names, static_name=True)
            if ref[1] != sheets[index][0]:
                _unsupported("Static name sheet scope does not match its reference")
            refs.append(ref)
        result.append((name, index, tuple(refs), _true(node.get("hidden", "0"))))
    return sorted(result)


def _snapshot(parts, *, require_caches=False):
    roots, rels = _package(parts)
    book = roots["xl/workbook.xml"]
    if book.tag != f"{{{S}}}workbook":
        _unsupported("Unexpected workbook root")
    calc = book.find(f"{{{S}}}calcPr")
    if calc is not None and (_true(calc.get("iterate", "0")) or calc.get("fullPrecision", "1") in {"0", "false"} or calc.get("refMode", "A1") != "A1"):
        _unsupported("Iterative, precision-as-displayed or R1C1 calculation is unsupported")
    sheets, names, ids, targets = [], {}, set(), set()
    for node in book.findall(f"{{{S}}}sheets/{{{S}}}sheet"):
        name, identity = node.get("name", ""), node.get("sheetId")
        target = rels["xl/_rels/workbook.xml.rels"].get(node.get(f"{{{R}}}id"))
        if not name or len(name) > 31 or any(c in name for c in "[]:*?/\\") or any(ord(c) < 32 for c in name) or name.casefold() in names:
            _unsupported("Invalid or duplicate worksheet name")
        if identity is None or identity in ids or target in targets or _part_kind(target or "") != "worksheet":
            _unsupported("Ambiguous or invalid worksheet identity")
        ids.add(identity)
        targets.add(target)
        names[name.casefold()] = name
        sheets.append((name, target, node.get("state", "visible")))
    if len(list(book.iter(f"{{{S}}}sheet"))) != len(sheets):
        _unsupported("Sheet declarations outside the ordinary workbook structure are unsupported")
    if len(list(book.iter(f"{{{S}}}definedName"))) != len(book.findall(f"{{{S}}}definedNames/{{{S}}}definedName")):
        _unsupported("Defined names outside the ordinary workbook structure are unsupported")
    if not 1 <= len(sheets) <= LIMITS["sheets"] or targets != {name for name in roots if _part_kind(name) == "worksheet"}:
        _unsupported("Unlisted sheets or worksheet count exceed the supported profile")
    if any(state not in {"visible", "hidden", "veryHidden"} for _, _, state in sheets):
        _unsupported("Invalid worksheet visibility")
    styles = _style_map(roots["xl/styles.xml"], themed="xl/theme/theme1.xml" in roots)
    strings = _strings(roots.get("xl/sharedStrings.xml"))
    total_cells = total_chars = 0
    formulas, snapshots, caches = [], [], {}
    for name, target, state in sheets:
        root = roots[target]
        if root.tag != f"{{{S}}}worksheet":
            _unsupported("Only ordinary worksheet roots are supported")
        if (any(_local(node.tag) in {"r", "rPr"} for node in root.iter())
                or len(list(root.iter(f"{{{S}}}phoneticPr"))) != len(root.findall(f"{{{S}}}phoneticPr"))):
            _unsupported("Rich or phonetic inline strings require a separate formatting workflow")
        phonetic = _worksheet_phonetic(root, roots["xl/styles.xml"], themed="xl/theme/theme1.xml" in roots)
        cells, rows, columns = {}, {}, {}
        for row in root.findall(f"{{{S}}}sheetData/{{{S}}}row"):
            index = _integer(row.get("r"), "worksheet row", minimum=1)
            if index in rows:
                _unsupported("Duplicate worksheet rows are unsupported")
            rowattrs = _attrs(row, {"hidden": "0", "customHeight": "0", "customFormat": "0", "outlineLevel": "0", "collapsed": "0", "thickTop": "0", "thickBot": "0"}, omit={"r", "spans", "s"})
            rowstyle = _integer(row.get("s", "0"), "row style", maximum=len(styles) - 1)
            rows[index] = (rowattrs, styles[rowstyle]) if rowattrs or rowstyle else None
            for cell in row:
                if cell.tag != f"{{{S}}}c":
                    _unsupported("Unsupported sheet row content")
                address, _, cellrow, abscol, absrow = _cell(cell.get("r", ""))
                if cellrow != index or abscol or absrow or address in cells:
                    _unsupported("Duplicate or misplaced worksheet cell")
                styleid = _integer(cell.get("s", "0"), "cell style", maximum=len(styles) - 1)
                if set(cell.attrib) - {"r", "s", "t"}:
                    _unsupported("Unsupported cell metadata or extension")
                children = list(cell)
                if len({child.tag for child in children}) != len(children) or any(child.tag not in {f"{{{S}}}f", f"{{{S}}}v", f"{{{S}}}is"} for child in children):
                    _unsupported("Ambiguous worksheet cell content")
                formula = cell.find(f"{{{S}}}f")
                if formula is not None:
                    if formula.attrib:
                        _unsupported("Shared, array and data-table formulas are unsupported")
                    raw = formula.text or ""
                    tree = _Formula(raw, name, names).tree
                    value = ("formula", tree)
                    formulas.append({"sheet": name, "cell": address, "formula": "=" + raw})
                    total_chars += len(raw)
                    if require_caches:
                        caches[(name, address)] = _typed(cell, strings, cache=True)
                else:
                    value = _typed(cell, strings)
                if value[0] == "text" and len(value[1]) > LIMITS["text_characters"]:
                    _unsupported("Worksheet text exceeds the supported length")
                cells[address] = (value, styles[styleid])
                total_cells += 1
                if total_cells > LIMITS["cells"] or len(formulas) > LIMITS["formulas"] or total_chars > LIMITS["total_formula_characters"]:
                    _unsupported("Worksheet cells or formulas exceed the Excel engine profile limits")
        if len(list(root.iter(f"{{{S}}}c"))) != len(cells) or len(list(root.iter(f"{{{S}}}row"))) != len(rows):
            _unsupported("Cells or rows outside the ordinary worksheet structure are unsupported")
        for col in root.findall(f"{{{S}}}cols/{{{S}}}col"):
            first = _integer(col.get("min"), "column range", minimum=1, maximum=16_384)
            last = _integer(col.get("max"), "column range", minimum=first, maximum=16_384)
            styleid = _integer(col.get("style", "0"), "column style", maximum=len(styles) - 1)
            attributes = _attrs(col, {"hidden": "0", "bestFit": "0", "customWidth": "0", "outlineLevel": "0", "collapsed": "0"}, omit={"min", "max", "style"})
            for index in range(first, last + 1):
                if index in columns:
                    _unsupported("Overlapping column definitions are unsupported")
                columns[index] = (attributes, styles[styleid])
        merges = []
        for node in root.findall(f"{{{S}}}mergeCells/{{{S}}}mergeCell"):
            ref = _reference(node.get("ref", ""), name, names)
            if len(ref[2]) != 2 or ref in merges:
                _unsupported("Invalid or duplicate merged cell range")
            merges.append(ref)
        # Relationship-bearing cell content is already rejected by the schema;
        # every allowed formula node must be a direct worksheet-cell formula.
        if len(list(root.iter(f"{{{S}}}f"))) != sum(value[0][0] == "formula" for value in cells.values()):
            _unsupported("Formula occurs outside an ordinary worksheet cell")
        extras = {}
        for element in ("sheetProtection", "autoFilter", "printOptions", "pageMargins", "pageSetup", "headerFooter", "rowBreaks", "colBreaks"):
            found = root.find(f"{{{S}}}{element}")
            if found is not None:
                extras[element] = _canon(found)
        panes = [_canon(pane) for pane in root.findall(f"{{{S}}}sheetViews/{{{S}}}sheetView/{{{S}}}pane")]
        formatpr = root.find(f"{{{S}}}sheetFormatPr")
        formatattrs = () if formatpr is None else _attrs(formatpr, {"baseColWidth": "8", "zeroHeight": "0", "customHeight": "0", "outlineLevelRow": "0", "outlineLevelCol": "0"})
        snapshots.append({"name": name, "state": state, "cells": cells, "default_style": styles[0],
                          "rows": {k: v for k, v in rows.items() if v is not None}, "columns": columns,
                          "merges": sorted(merges), "panes": panes, "sheet_format": formatattrs, "layout": extras,
                          "phonetic_defaults": phonetic, "sheet_properties": _sheet_properties(root)})
    if total_cells > LIMITS["cells"] or len(formulas) > LIMITS["formulas"] or total_chars > LIMITS["total_formula_characters"]:
        _unsupported("Worksheet cells or formulas exceed the Excel engine profile limits")
    properties = book.find(f"{{{S}}}workbookPr")
    protection = book.find(f"{{{S}}}workbookProtection")
    snapshot = {"sheets": snapshots, "names": _names(book, sheets, names),
                "date1904": False if properties is None else _true(properties.get("date1904", "0")),
                "workbook_protection": None if protection is None or (not protection.attrib and not len(protection)) else _canon(protection),
                "theme": None if "xl/theme/theme1.xml" not in roots else _canon(roots["xl/theme/theme1.xml"]),
                "indexed_colors": _palette(roots["xl/styles.xml"])}
    return snapshot, formulas, caches


def inspect_engine_input(document):
    snapshot, formulas, _ = _snapshot(document.package.parts)
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"eligible": True, "policy_version": POLICY_VERSION, "sheet_count": len(snapshot["sheets"]),
            "formula_count": len(formulas), "formula_cells": formulas,
            "semantic_snapshot_sha256": hashlib.sha256(encoded).hexdigest(), "limits": dict(LIMITS),
            "required_review": "professional_review", "engine_started": False}


def _compare(before, after):
    if len(before["sheets"]) != len(after["sheets"]):
        fail("excel_semantic_conflict", "Excel changed the worksheet count")
    for original, saved in zip(before["sheets"], after["sheets"]):
        for key in set(original) - {"cells"}:
            if original[key] != saved[key]:
                fail("excel_semantic_conflict", f"Excel changed worksheet {original['name']} {key}")
        for address in original["cells"].keys() | saved["cells"].keys():
            default = (("blank", None), original["default_style"])
            if original["cells"].get(address, default) != saved["cells"].get(address, default):
                fail("excel_semantic_conflict", f"Excel changed input, formula or style at {original['name']}!{address}")
    for key in set(before) - {"sheets"}:
        if before[key] != after[key]:
            fail("excel_semantic_conflict", f"Excel changed workbook {key}")


def verify_engine_output(beforebytes, afterbytes, engine_receipt):
    if not isinstance(beforebytes, bytes) or not isinstance(afterbytes, bytes) or not isinstance(engine_receipt, dict):
        fail("invalid_request", "Excel verification requires exact bytes and a host-attested receipt")
    receipt = engine_receipt
    source_hash, output_hash = hashlib.sha256(beforebytes).hexdigest(), hashlib.sha256(afterbytes).hexdigest()
    if (type(receipt.get("schema_version")) is not int or receipt.get("schema_version") != 1 or receipt.get("engine") != "Microsoft Excel"
            or not isinstance(receipt.get("engine_version"), str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", receipt["engine_version"])
            or receipt.get("calculation_method") != "CalculateFullRebuild" or receipt.get("calculation_state") != "done"
            or receipt.get("owned_instance") is not True or type(receipt.get("workbook_count")) is not int or receipt.get("workbook_count") != 1
            or receipt.get("source_sha256") != source_hash or receipt.get("output_sha256") != output_hash):
        fail("invalid_engine_receipt", "Excel receipt identity, completion or exact-byte binding is invalid")
    before, formulas, _ = _snapshot(Package(beforebytes).parts)
    after, after_formulas, caches = _snapshot(Package(afterbytes).parts, require_caches=True)
    _compare(before, after)
    values = receipt.get("formula_results")
    if not isinstance(values, list) or len(values) != len(formulas) or len(after_formulas) != len(formulas):
        fail("invalid_engine_receipt", "Excel must capture every formula result exactly once")
    seen = set()
    for row in values:
        if not isinstance(row, dict) or set(row) != {"sheet", "cell", "type", "value"}:
            fail("invalid_engine_receipt", "Invalid captured formula value shape")
        if not isinstance(row["sheet"], str) or not isinstance(row["cell"], str):
            fail("invalid_engine_receipt", "Captured formula identities must be strings")
        key = (row["sheet"], row["cell"])
        if key in seen or key not in caches:
            fail("invalid_engine_receipt", "Captured formula values have duplicates or unknown cells")
        seen.add(key)
        kind, saved = caches[key]
        value = row["value"]
        if row["type"] != kind:
            fail("excel_cache_conflict", "Saved formula result type differs from captured Excel Value2")
        if kind == "number":
            if type(value) not in {int, float} or abs(value) > 1e100 or not math.isfinite(value) or not math.isclose(float(saved), value, rel_tol=1e-14, abs_tol=0):
                fail("excel_cache_conflict", "Saved formula numeric result differs from captured Excel Value2")
        elif (kind == "boolean" and type(value) is not bool) or (kind == "text" and type(value) is not str) or value != saved:
            fail("excel_cache_conflict", "Saved formula result differs from captured Excel Value2")
    return {"source_sha256": source_hash, "output_sha256": output_hash,
            "policy_version": POLICY_VERSION, "verification": {"status": "pass", "semantic_preserved": True,
                "persisted_formula_caches_verified": True, "formula_count": len(formulas),
                "engine_execution": "host_attested", "engineering_facts": "not_checked",
                "render": "not_checked", "required_review": "professional_review"}}
