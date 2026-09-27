"""Preserve Office and delimited-text tables at the draft input boundary."""
from __future__ import annotations

import csv
import html
from io import StringIO
from itertools import islice
from typing import Iterable
from xml.etree import ElementTree


def table_markdown(rows: Iterable[Iterable[object]], limit: int) -> str:
    """Keep column positions and literal content; the first nonempty row is the header and rows share the widest width that fits."""
    lines: list[str] = []
    counts: list[int] = []
    used = width = gaps = 0
    frozen = False
    for row in islice(rows, 20_000):
        values = ["" if value is None else str(value) for value in islice(row, 256)]
        if not any(value.strip() for value in values):
            continue
        cells = [html.escape(value.replace("\r", " ").replace("\n", "；"), quote=False).replace("|", "&#124;") for value in values]
        line = "| " + " | ".join(cells) + " |"
        count = len(cells)
        if count > width and not frozen:
            # Widening pads every kept row ("  |" per cell). Once that no longer fits, later wide rows stay as written.
            grown = gaps + (count - width) * len(lines)
            if used + len(line) + 1 + 3 * grown + 6 * count + 2 <= limit:
                width, gaps = count, grown
            frozen = width < count
        missing = max(0, width - count)
        if not width or used + len(line) + 1 + 3 * (gaps + missing) + 6 * width + 2 > limit:
            break
        lines.append(line)
        counts.append(count)
        used, gaps = used + len(line) + 1, gaps + missing
    lines = [line + "  |" * (width - count) for line, count in zip(lines, counts)]
    if lines:
        lines.insert(1, "| " + " | ".join(["---"] * width) + " |")
    return "\n".join(lines)


def csv_text(text: str, limit: int) -> str:
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return table_markdown(csv.reader(StringIO(text), dialect), limit)


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_CN_DIGITS = "零一二三四五六七八九"


def _chinese(number: int) -> str:
    if number <= 0 or number >= 100:
        return str(number)
    tens, ones = divmod(number, 10)
    return (_CN_DIGITS[ones] if not tens else ("十" if tens == 1 else _CN_DIGITS[tens] + "十") + (_CN_DIGITS[ones] if ones else ""))


def _roman(number: int) -> str:
    out = ""
    for value, mark in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"),
                        (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while number >= value:
            out, number = out + mark, number - value
    return out


def _numbered(number: int, fmt: str) -> str:
    if fmt in ("chineseCounting", "chineseCountingThousand", "chineseLegalSimplified", "japaneseCounting", "ideographDigital"):
        return _chinese(number)
    if fmt == "upperLetter":
        return chr(ord("A") + (number - 1) % 26)
    if fmt == "lowerLetter":
        return chr(ord("a") + (number - 1) % 26)
    if fmt == "upperRoman":
        return _roman(number)
    if fmt == "lowerRoman":
        return _roman(number).lower()
    if fmt == "decimalZero":
        return f"{number:02d}"
    return str(number)


def docx_numbering(numbering: ElementTree.Element | None) -> dict:
    """``word/numbering.xml`` as {numId: {level: (format, text pattern, start)}}. A clause number that Word
    generates - "3.4.2", "（一）", "第二条" - is a list number, not text: without this the extracted paragraph
    has lost the very thing a person needs to find it again."""
    if numbering is None:
        return {}

    def level_of(level: ElementTree.Element) -> tuple:
        fmt = level.find(_W + "numFmt")
        text = level.find(_W + "lvlText")
        start = level.find(_W + "start")
        try:
            first = int(start.get(_W + "val")) if start is not None else 1
        except (TypeError, ValueError):
            first = 1
        return ((fmt.get(_W + "val") if fmt is not None else "decimal") or "decimal",
                (text.get(_W + "val") if text is not None else "") or "", first)

    def depth_of(element: ElementTree.Element) -> int | None:
        try:
            return int(element.get(_W + "ilvl") or 0)
        except (TypeError, ValueError):
            return None

    abstract: dict = {}
    for item in numbering.findall(_W + "abstractNum"):
        levels = {}
        for level in item.findall(_W + "lvl"):
            depth = depth_of(level)
            if depth is not None:
                levels[depth] = level_of(level)
        abstract[item.get(_W + "abstractNumId")] = levels
    result: dict = {}
    for item in numbering.findall(_W + "num"):
        ref = item.find(_W + "abstractNumId")
        if ref is None or ref.get(_W + "val") not in abstract:
            continue
        levels = dict(abstract[ref.get(_W + "val")])
        # what Word writes for "Set numbering value" / "Restart at": this list instance overrides a level of the
        # abstract definition - a whole <w:lvl>, or only its start (<w:startOverride w:val="4"/> makes 1.7 read 4.7)
        for override in item.findall(_W + "lvlOverride"):
            depth = depth_of(override)
            if depth is None:
                continue
            replaced = override.find(_W + "lvl")
            if replaced is not None:
                levels[depth] = level_of(replaced)
            start = override.find(_W + "startOverride")
            if start is not None:
                try:
                    value = int(start.get(_W + "val"))
                except (TypeError, ValueError):
                    continue
                fmt, pattern, _first = levels.get(depth, ("decimal", "", 1))
                levels[depth] = (fmt, pattern, value)
        result[item.get(_W + "numId")] = levels
    return result


class _Counters:
    """The running numbers of every list of a document, level by level."""

    def __init__(self, definitions: dict) -> None:
        self.definitions, self.counts = definitions, {}

    def label(self, paragraph: ElementTree.Element) -> str:
        props = paragraph.find(_W + "pPr")
        num = props.find(_W + "numPr") if props is not None else None
        if num is None:
            return ""
        ident, level = num.find(_W + "numId"), num.find(_W + "ilvl")
        key = ident.get(_W + "val") if ident is not None else None
        levels = self.definitions.get(key)
        if not levels:
            return ""
        try:
            depth = int(level.get(_W + "val")) if level is not None else 0
        except (TypeError, ValueError):
            depth = 0
        if depth not in levels:
            return ""
        counts = self.counts.setdefault(key, {})
        counts[depth] = counts.get(depth, levels[depth][2] - 1) + 1
        for deeper in [d for d in counts if d > depth]:
            del counts[deeper]
        fmt, pattern, _start = levels[depth]
        if fmt in ("bullet", "none") or not pattern:
            return ""
        out = pattern
        for index in range(depth + 1):
            value = counts.get(index, levels.get(index, ("decimal", "", 1))[2])
            out = out.replace(f"%{index + 1}", _numbered(value, levels.get(index, (fmt, "", 1))[0] if index != depth else fmt))
        return out.strip()


def docx_document_text(document: ElementTree.Element, limit: int, numbering: ElementTree.Element | None = None) -> str:
    """Read paragraphs and tables in document order without flattening table cells. With ``numbering``
    (the root of word/numbering.xml) a paragraph that Word numbers gets its number back, as text."""
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    tag = "{" + ns["w"] + "}"
    body = document.find("w:body", ns)
    if body is None:
        return ""
    blocks: list[str] = []
    used = 0
    counters = _Counters(docx_numbering(numbering))

    def paragraph_text(element: ElementTree.Element) -> str:
        text = "".join(node.text or "" for node in element.findall(".//w:t", ns))
        label = counters.label(element) if text.strip() else ""
        return f"{label} {text}" if label else text

    for element in body:
        if used >= limit:
            break
        if element.tag == tag + "tbl":
            rows = (["；".join(paragraph_text(p) for p in cell.findall("w:p", ns))
                     for cell in row.findall("w:tc", ns)] for row in element.findall("w:tr", ns))
            block = table_markdown(rows, limit - used)
        else:
            block = paragraph_text(element)[:limit - used]
        if block:
            blocks.append(block)
            used += len(block) + 2
    return "\n\n".join(blocks)[:limit]
