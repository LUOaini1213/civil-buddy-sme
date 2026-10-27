"""The tables of a PDF, put back together from what its pages DRAW: ruling lines and positioned text.

A tender exported from Word draws its front table as a grid of ruling lines with the text of every cell placed
inside it - the number and the name centred in their cells, the content on as many lines as it takes. pypdf's
plain text of such a page is the rows read straight across:

    1.10.2 供应商提出问题
    的截止时间 /
    2.2.2 提交首次响应文
    件截止时间
    2028 年 6 月 12 日 9 时 30 分

which no rule about lines can take apart again: where the name ends and the content begins is not in the text.
It is on the page. The same pass of pypdf that yields the text yields every drawing operator, so

    document_texts(reader)   every page's text, each ruled table in it as "| 2.2.2 | 提交首次响应文件截止时间 | 2028 年 … |" rows
    page_text(page)          one page on its own (no knowledge of what repeats from page to page)

Running text stays exactly what pypdf gives (the pieces outside a table, in pypdf's own order and spacing).
Inside a table a cell's lines are joined the way a paragraph's are: a line that fills the cell runs on, a line
that stops short ends a paragraph of the cell ("；", the separator a Word cell's paragraphs get in
document_text.py). A cell spanning several rows is repeated in each of them, so every row reads whole.

Page furniture - a watermark, a running header, the page number - is text that stands at the same place on page
after page. It belongs to no cell and no sentence, and it is left out (a watermark drawn across the middle of
the page otherwise lands inside whatever cell lies under it).

Nothing is corrected or guessed: a page without a ruled grid of two columns or more comes back as plain text.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

Segment = Tuple[float, float, float, float]          # x0, y0, x1, y1
Rule = Tuple[float, float, float]                    # position, from, to
Key = Tuple[str, int, int]
_SHOW = {b"Tj", b"TJ"}       # not ' and ": they move to the next line first, and the matrices seen before them are the old line's
_STROKE = {b"S", b"s", b"B", b"B*", b"b", b"b*"}
_FILL = {b"f", b"F", b"f*"}
_TOUCH = 3.0          # rules drawn cell by cell meet within this much
_SAME = 2.0           # two rules this close are one rule
_COVER = 0.6          # a rule separates two cells when it runs along this much of their common edge
_CLOSERS = "，。；：、）」』”’！？》〉】%％"
_ENDED = "。；;：:，,、！？"


class Piece:
    """One stretch of text as pypdf flushed it, with where it starts on the page."""

    __slots__ = ("x", "y", "size", "text")

    def __init__(self, x: float, y: float, size: float, text: str) -> None:
        self.x, self.y, self.size, self.text = x, y, size, text

    @property
    def key(self) -> Key:
        return re.sub(r"\d+", "#", self.text.strip()), round(self.x / 3), round(self.y / 3)

    @property
    def edge_key(self) -> Key:
        """At the head or the foot of a page the place across does not count: "9" and "100" are centred apart."""
        return re.sub(r"\d+", "#", self.text.strip()), -1, round(self.y / 3)


class PageParts:
    __slots__ = ("pieces", "segments", "upright", "height", "width")

    def __init__(self, pieces: List[Piece], segments: List[Segment], upright: bool, height: float, width: float = 595.0) -> None:
        self.pieces, self.segments, self.upright, self.height, self.width = pieces, segments, upright, height, width


def _apply(cm: Sequence[float], x: float, y: float) -> Tuple[float, float]:
    return x * cm[0] + y * cm[2] + cm[4], x * cm[1] + y * cm[3] + cm[5]


def read_page(page: Any) -> PageParts:
    """The page's text pieces in pypdf's order and every LINE its drawing paints: what is stroked, and what is
    filled as thin as a line (Word draws a border as a filled sliver). An area that is filled - the shading of a
    header row, the white laid behind a line of text - has an outline, but nobody drew it: it is no rule."""
    pieces: List[Piece] = []
    painted: List[Segment] = []
    paths: List[List[Tuple[float, float]]] = []
    stands = [0, 0]
    ink = {"fill": False, "stroke": False}     # whether the colour in force is white: white laid behind a run of
    saved: List[Dict[str, bool]] = []          # characters, or a border "drawn" in the paper's colour, rules nothing
    leading = 0.0
    leading_stack: List[float] = []
    text_line: Optional[List[float]] = None

    def white(args: Sequence[Any], cmyk: bool = False) -> bool:
        try:
            values = [float(a) for a in args if isinstance(a, (int, float)) or str(a).replace(".", "", 1).replace("-", "", 1).isdigit()]
        except (TypeError, ValueError):
            return False
        if not values:
            return False
        if cmyk or len(values) == 4:
            return all(v <= 0.03 for v in values)
        return all(v >= 0.97 for v in values)

    # Where a piece STANDS is where its first text-showing operator stood. The matrices the library hands to the text
    # visitor are its own memo of that - in some versions the memo is stale for a piece that begins with an inserted
    # space: " 2025" reported the line above it, and one cell of a real front table came back as two lines.
    shown: List[Tuple[List[float], List[float]]] = []
    current = [b""]

    def on_text(text: str, cm: Sequence[float], tm: Sequence[float], font: Any, size: float) -> None:
        if not text:
            return
        if shown:
            start = shown[0]
            # a piece handed over WHILE a text-showing operator is at work ended before it: that operator opens the next
            shown[:] = shown[-1:] if (current[0] in _SHOW and len(shown) > 1) else []
            if text.strip():
                cm, tm = start      # (a run of blanks uses up its operators all the same, and stands nowhere that matters)
        x, y = _apply(cm, tm[4], tm[5])
        scale = math.hypot(tm[0], tm[1]) * math.hypot(cm[0], cm[1])
        if text.strip():
            stands[0 if abs(tm[0] * cm[1] + tm[1] * cm[3]) < 1e-3 else 1] += 1
        pieces.append(Piece(x, y, float(size or 0) * (scale or 1.0), text))

    def on_operator(op: Any, args: Sequence[Any], cm: Sequence[float], tm: Sequence[float]) -> None:
        nonlocal leading, text_line
        current[0] = op
        if op in _SHOW:
            shown.append(([float(v) for v in cm], list(text_line) if text_line is not None else [float(v) for v in tm]))
        try:
            if op == b"q":
                saved.append(dict(ink))
                leading_stack.append(leading)
            elif op == b"Q" and saved:
                ink.update(saved.pop())
                leading = leading_stack.pop()
            elif op in (b"g", b"rg", b"k", b"sc", b"scn"):
                ink["fill"] = white(args, cmyk=op == b"k")
            elif op in (b"G", b"RG", b"K", b"SC", b"SCN"):
                ink["stroke"] = white(args, cmyk=op == b"K")
            # Text leading (TL) is an absolute distance in text space, not a
            # font-size multiplier. Installed pypdf versions can multiply it
            # by Tf and move wrapped text into unrelated cells or off-page.
            # Track the drawn line origin directly; decoded text still comes
            # from pypdf and is never corrected or guessed.
            if op == b"BT":
                text_line = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
            elif op == b"Tm":
                text_line = [float(value) for value in args[:6]]
                if len(text_line) != 6 or not all(math.isfinite(value) for value in text_line):
                    raise ValueError("Invalid text matrix")
            elif op == b"TL":
                leading = float(args[0])
                if not math.isfinite(leading):
                    raise ValueError("Invalid text leading")
            elif op in (b"Td", b"TD") and text_line is not None:
                tx, ty = float(args[0]), float(args[1])
                if op == b"TD":
                    leading = -ty
                text_line[4] += tx * text_line[0] + ty * text_line[2]
                text_line[5] += tx * text_line[1] + ty * text_line[3]
            elif op in (b"T*", b"'", b'"') and text_line is not None:
                text_line[4] -= leading * text_line[2]
                text_line[5] -= leading * text_line[3]
            if op == b"m":
                paths.append([_apply(cm, float(args[0]), float(args[1]))])
            elif op == b"l" and paths:
                paths[-1].append(_apply(cm, float(args[0]), float(args[1])))
            elif op == b"h" and paths and len(paths[-1]) > 1:
                paths[-1].append(paths[-1][0])
            elif op == b"re":
                x, y, w, h = (float(a) for a in args[:4])
                corners = [_apply(cm, x, y), _apply(cm, x + w, y), _apply(cm, x + w, y + h), _apply(cm, x, y + h)]
                paths.append(corners + [corners[0]])
            elif op in _STROKE:
                for path in paths if not ink["stroke"] else ():
                    painted.extend((*a, *b) for a, b in zip(path, path[1:]))
                paths.clear()
            elif op in _FILL:
                for path in paths if not ink["fill"] else ():
                    xs, ys = [pt[0] for pt in path], [pt[1] for pt in path]
                    wide, high = max(xs) - min(xs), max(ys) - min(ys)
                    if high <= 2.5 and wide >= 5:
                        painted.append((min(xs), (min(ys) + max(ys)) / 2, max(xs), (min(ys) + max(ys)) / 2))
                    elif wide <= 2.5 and high >= 5:
                        painted.append(((min(xs) + max(xs)) / 2, min(ys), (min(xs) + max(xs)) / 2, max(ys)))
                paths.clear()
            elif op == b"n":            # a clipping path: outlined, never painted
                paths.clear()
        except (TypeError, ValueError, IndexError):
            paths.clear()
            if op in (b"Tm", b"TL", b"Td", b"TD", b"T*", b"'", b'"'):
                text_line = None

    page.extract_text(visitor_text=on_text, visitor_operand_before=on_operator)
    try:
        height, width = float(page.mediabox.height), float(page.mediabox.width)
    except Exception:   # noqa: BLE001
        height, width = 842.0, 595.0
    return PageParts(pieces, painted, stands[0] >= stands[1], height, width)


_PAGE_NUMBER = re.compile(r"^(?:第\s*)?[-—–]?\s*(\d{1,4})\s*[-—–]?(?:\s*页)?(?:\s*[/，,]?\s*共?\s*\d{1,4}\s*页?)?\s*[-—–]?$"
                          r"|^(?i:page)\s*(\d{1,4})(?:\s*(?i:of|/)\s*\d{1,4})?$")


def at_edge(piece: Piece, height: float, share: float = 0.1) -> bool:
    return piece.y <= height * share or piece.y >= height * (1 - share)


def furniture(pages: Sequence[PageParts]) -> List[Set[int]]:
    """For every page, which of its pieces are page furniture - text that belongs to the paper, not to the document:

    a watermark        long words at the SAME PLACE in the body of a quarter of the pages - and then wherever else they
                       stand (on a landscape page the same stamp lands elsewhere). Words that merely recur - "投标人：
                       （盖单位章）" under every form - stand somewhere else each time and stay.
    a running head     any words at the same height in the outermost 6 % of a quarter of the pages (a table's header
                       row, repeated page after page, stands further in and stays); long words at the same place in the
                       head or foot, and what shares their line ("项目名称：" before the name). A head repeats a value OF
                       the document - the project's name - so it is dropped where it stands and nowhere else: the front
                       table's row that says the same words is no furniture.
    the page number    a number alone on its line in the head or foot - drawn in one piece or in five ("-", "4", "/",
                       "39", "-"), or what a head's line still holds without the head - one more than the page before's
                       (a figure in a sentence or in a table's last row is neither alone nor in step)
    """
    drop: List[Set[int]] = [set() for _ in pages]
    if len(pages) < 4:
        return drop
    stamped: Dict[Key, int] = {}
    placed: Dict[Key, int] = {}
    edge_stamp: Dict[Key, bool] = {}
    for parts in pages:
        stamps, keys = set(), set()
        for piece in parts.pieces:
            text = piece.text.strip()
            if not text:
                continue
            if len(text) >= 12:
                stamps.add(piece.key)
                edge_stamp[piece.key] = at_edge(piece, parts.height)
            if at_edge(piece, parts.height, 0.06):
                keys.add(piece.edge_key)
        for key in stamps:
            stamped[key] = stamped.get(key, 0) + 1
        for key in keys:
            placed[key] = placed.get(key, 0) + 1
    needed = max(4, math.ceil(len(pages) * 0.25))
    repeated = {key for key, count in stamped.items() if count >= needed}
    watermarks = {key[0] for key in repeated if not edge_stamp.get(key)}
    heads = {key for key in repeated if edge_stamp.get(key)}
    numbers: List[Dict[Tuple[int, ...], Tuple[int, int]]] = []       # per page: the pieces of a line -> (value, height bucket)
    for at, parts in enumerate(pages):
        head_lines = {round(piece.y / 2) for piece in parts.pieces if len(piece.text.strip()) >= 12 and piece.key in heads}
        lines: Dict[int, List[int]] = {}
        for index, piece in enumerate(parts.pieces):
            text = piece.text.strip()
            if not text:
                continue
            if len(text) >= 12 and piece.key[0] in watermarks:
                drop[at].add(index)
            elif round(piece.y / 2) in head_lines:
                drop[at].add(index)      # the head's own line, whatever else it holds: "项目名称：", the chapter's name, "-4/39-"
            else:
                if at_edge(piece, parts.height, 0.06) and placed.get(piece.edge_key, 0) >= needed and not _PAGE_NUMBER.match(text):
                    drop[at].add(index)
                if at_edge(piece, parts.height):
                    lines.setdefault(round(piece.y / 2), []).append(index)      # with its dashes, which stand at one place: "-", "4", "/", "39", "-"
        lone: Dict[Tuple[int, ...], Tuple[int, int]] = {}
        for members in lines.values():
            members = sorted(members, key=lambda i: parts.pieces[i].x)
            found = _PAGE_NUMBER.match("".join(parts.pieces[i].text.strip() for i in members))
            if found:
                lone[tuple(members)] = (int(found.group(1) or found.group(2)), round(parts.pieces[members[0]].y / 3))
        numbers.append(lone)
    for at in range(len(pages)):
        for members, (value, bucket) in numbers[at].items():
            for other, delta in ((at - 1, -1), (at + 1, 1)):
                if 0 <= other < len(pages) and any(v == value + delta and abs(b - bucket) <= 1 for v, b in numbers[other].values()):
                    drop[at].update(members)
    return drop


def _merge(rules: List[Rule]) -> List[Rule]:
    """Rules on one line that touch become one."""
    out: List[List[float]] = []
    for at, lo, hi in sorted(rules):
        for known in out:
            if abs(known[0] - at) <= _SAME and lo <= known[2] + _TOUCH and hi >= known[1] - _TOUCH:
                known[1], known[2] = min(known[1], lo), max(known[2], hi)
                break
        else:
            out.append([at, lo, hi])
    merged = [(a, b, c) for a, b, c in out]
    return merged if len(merged) == len(rules) else _merge(merged)


def rules_of(segments: Sequence[Segment]) -> Tuple[List[Rule], List[Rule]]:
    """(horizontal rules as (y, x0, x1), vertical rules as (x, y0, y1)) - the rest of what a page draws is left out."""
    across, down = [], []
    for x0, y0, x1, y1 in segments:
        if abs(y0 - y1) <= 0.8 and abs(x1 - x0) >= 12:
            across.append(((y0 + y1) / 2, min(x0, x1), max(x0, x1)))
        elif abs(x0 - x1) <= 0.8 and abs(y1 - y0) >= 5:
            down.append(((x0 + x1) / 2, min(y0, y1), max(y0, y1)))
    return _merge(across), _merge(down)


def _cluster(values: Iterable[float]) -> List[float]:
    out: List[List[float]] = []
    for value in sorted(values):
        if out and value - out[-1][-1] <= _SAME:
            out[-1].append(value)
        else:
            out.append([value])
    return [sum(group) / len(group) for group in out]


def _covered(rules: Sequence[Rule], at: float, lo: float, hi: float) -> float:
    """How much of [lo, hi] the rules standing at ``at`` run along (0..1)."""
    if hi <= lo:
        return 0.0
    spans = sorted((max(lo, a), min(hi, b)) for position, a, b in rules if abs(position - at) <= _SAME and b > lo and a < hi)
    total, reach = 0.0, lo
    for a, b in spans:
        if b > reach:
            total += b - max(a, reach)
            reach = b
    return total / (hi - lo)


class Grid:
    """One ruled table: column edges left to right, row edges top to bottom, and the rules between its cells.

    Every ruled edge is kept, also one that stands in a single row (the six columns of one scoring row under a
    three-column table; the frame around one value). Where a row has no rule at an edge its cells are one cell
    (spans), and the text of a merged cell stands in its first column - so a row may come out with empty cells
    between its values, never with two cells' words run together."""

    def __init__(self, xs: List[float], ys: List[float], across: List[Rule], down: List[Rule]) -> None:
        self.xs, self.ys, self.across, self.down = xs, ys, across, down

    @property
    def box(self) -> Tuple[float, float, float, float]:
        return self.xs[0], self.ys[-1], self.xs[-1], self.ys[0]

    def holds(self, x: float, y: float) -> bool:
        x0, y0, x1, y1 = self.box
        return x0 - 1 <= x <= x1 + 1 and y0 - 1 <= y <= y1 + 1

    def cell(self, x: float, y: float) -> Tuple[int, int]:
        col = max(0, min(len(self.xs) - 2, sum(1 for edge in self.xs[1:-1] if x >= edge - 0.5)))
        row = max(0, min(len(self.ys) - 2, sum(1 for edge in self.ys[1:-1] if y <= edge)))
        return row, col

    def spans(self) -> Dict[Tuple[int, int], Tuple[int, int]]:
        """Every cell -> the top-left cell of the merged cell it belongs to."""
        rows, cols = len(self.ys) - 1, len(self.xs) - 1
        parent = {(r, c): (r, c) for r in range(rows) for c in range(cols)}

        def find(node: Tuple[int, int]) -> Tuple[int, int]:
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        def join(a: Tuple[int, int], b: Tuple[int, int]) -> None:
            a, b = find(a), find(b)
            if a != b:
                parent[max(a, b)] = min(a, b)

        for r in range(rows):
            for c in range(cols):
                if c and _covered(self.down, self.xs[c], self.ys[r + 1], self.ys[r]) < _COVER:
                    join((r, c - 1), (r, c))
                if r and _covered(self.across, self.ys[r], self.xs[c], self.xs[c + 1]) < _COVER:
                    join((r - 1, c), (r, c))
        return {node: find(node) for node in parent}


def grids_of(across: List[Rule], down: List[Rule]) -> List[Grid]:
    """Rules that touch one another make one table; a table has at least two columns."""
    nodes = [("h", rule) for rule in across] + [("v", rule) for rule in down]
    parent = list(range(len(nodes)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, (kind_i, (y, x0, x1)) in enumerate(nodes):
        if kind_i != "h":
            continue
        for j, (kind_j, (x, y0, y1)) in enumerate(nodes):
            if kind_j == "v" and x0 - _TOUCH <= x <= x1 + _TOUCH and y0 - _TOUCH <= y <= y1 + _TOUCH:
                parent[find(i)] = find(j)
    groups: Dict[int, List[int]] = {}
    for i in range(len(nodes)):
        groups.setdefault(find(i), []).append(i)
    grids: List[Grid] = []
    for members in groups.values():
        hs = [nodes[i][1] for i in members if nodes[i][0] == "h"]
        vs = [nodes[i][1] for i in members if nodes[i][0] == "v"]
        xs, ys = _cluster(v[0] for v in vs), sorted(_cluster(h[0] for h in hs), reverse=True)
        if len(xs) >= 3 and len(ys) >= 2 and xs[-1] - xs[0] >= 120:
            grid = Grid(xs, ys, hs, vs)
            if len(grid.xs) >= 3:
                grids.append(grid)
    return sorted(grids, key=lambda g: -g.ys[0])


def _width(text: str, size: float) -> float:
    """How wide the text is set, near enough: a full-width character one em, the rest half of one - and the
    quarter em Word puts wherever Chinese text meets a figure or a Latin letter."""
    total = sum(size if ord(ch) > 0x2E7F else size * 0.5 for ch in text)
    meets = len(re.findall(r"(?<=[一-鿿])(?=[A-Za-z0-9])|(?<=[A-Za-z0-9])(?=[一-鿿])", text))
    return total + meets * size * 0.25


def _first_unit(text: str) -> str:
    """What could not have been left behind on the line before: a whole figure or word, or one character -
    with the closing marks that may not start a line."""
    found = re.match(r"[A-Za-z0-9.]+|.", text)
    unit = found.group(0) if found else ""
    rest = text[len(unit):]
    return unit + rest[:len(rest) - len(rest.lstrip(_CLOSERS))]


Line = Tuple[str, float, float, float]      # text, where it starts, where it ends (estimated), type size

_STARTS_PARAGRAPH = re.compile(r"[（(]\s*[\d一二三四五六七八九十]+\s*[)）]|\d+\s*[.．、)）]|[■□☑☐√○●◆※★]|[一二三四五六七八九十]+\s*、"
                               r"|第[一二三两]个?信封[^：:，。；]{0,20}[：:]"        # "第一个信封（商务及技术文件）开标地点："
                               r"|(?![^：:]{0,8}(?:应|须|按|以下|如下|下列|为|的))[一-鿿]{2,8}\s*[：:]")   # a label: "联系人：", not "文件应按以下要求装订："


def cell_lines(pieces: Sequence[Piece]) -> List[Line]:
    """The pieces of one cell as lines, top to bottom."""
    grouped: List[List[Piece]] = []
    for piece in sorted(pieces, key=lambda p: (-p.y, p.x)):
        if grouped and abs(grouped[-1][0].y - piece.y) <= max(2.0, 0.3 * (piece.size or 10)):
            grouped[-1].append(piece)
        else:
            grouped.append([piece])
    lines: List[Line] = []
    for line in grouped:
        ordered = sorted(enumerate(line), key=lambda item: (round(item[1].x, 1), item[0]))
        joined = ""
        last_x: Optional[float] = None
        for _, p in ordered:
            # two text objects side by side: Chinese is set solid, Latin words are set apart by position alone - the page
            # holds no space character between "the Project" and "refer to" when the font changes there
            if (joined and last_x is not None and abs(p.x - last_x) > 0.5
                    and re.search(r"[A-Za-z][,.;:)]?$", joined) and re.match(r"[(“\"]?[A-Za-z]", p.text)):
                joined += " "
            joined += p.text
            last_x = p.x
        text = re.sub(r"\s+", " ", joined).strip()
        if not text:
            continue
        size = max(p.size for p in line) or 10.0
        start = min(p.x for p in line)
        if max(p.x for p in line) - start < 0.5:     # one text object: every piece reports the line's start
            end = start + _width(text, size)
        else:
            end = max(p.x + _width(p.text.strip(), p.size or size) for p in line)
        lines.append((text, start, end, size))
    return lines


_LATIN_END = re.compile(r"[A-Za-z0-9,.;:!?)\]%’”\"']$")
_LATIN_START = re.compile(r"^[A-Za-z0-9(\[‘“\"'$₱]")


def between(before: str, after: str) -> str:
    """What stands between a line and the line it runs on into: nothing in Chinese, a space between two Latin ends
    ("the sum" / "of Five Million"). A word divided at the line end ("inter-" / "national") is one word."""
    if not before or not after:
        return ""
    if re.search(r"[A-Za-z]-$", before) and re.match(r"[a-z]", after):
        return ""
    if before[-1].isdigit() and after[0].isdigit():
        return ""       # a long number broken by the line ("…0320400" / "0207189") is one number
    return " " if (_LATIN_END.search(before) and _LATIN_START.match(after)) else ""


def join_lines(lines: Sequence[Line], left: float, right: float, reach: float = 0.0) -> str:
    """The text of one cell. A line runs on into the next - one paragraph, joined as it stands - or ends a paragraph
    of the cell ("；"). In that order:

      the next line opens with a list mark or a label ("（2）", "联系人：")     a new paragraph
      by measure: the next line's first word would not have fitted behind it   it runs on (``reach`` is how far the
                                                                              column's full lines really go; 0: the
                                                                              drawn edge less the usual padding)
      the cell sets its paragraphs with a FIRST-LINE indent, the next line     it runs on all the same - the measure is
      stands at the margin and this one is not plainly short                   an estimate, the indent is drawn
    """
    if not lines:
        return ""
    margin = min(start for _, start, _, _ in lines)

    def limit(size: float) -> float:
        return min(reach - size, right - 1.35 * size) if reach else (right - 1.35 * size)

    def full(index: int) -> bool:
        text, _, end, size = lines[index]
        return end + _width(_first_unit(lines[index + 1][0]), size) > limit(size)

    def plainly_short(index: int) -> bool:
        _, _, end, size = lines[index]
        return end < limit(size) - 4 * size

    # a paragraph's FIRST line is indented (not its later ones, as under "地址：" with a hanging indent): an indented
    # line opens the cell or follows a line that is plainly short
    first_line_style = any(start >= margin + 0.8 * size and (index == 0 or plainly_short(index - 1))
                           for index, (_, start, _, size) in enumerate(lines))
    out = ""
    for index, (text, start, end, size) in enumerate(lines):
        out += text
        if index + 1 == len(lines):
            break
        following, next_start = lines[index + 1][0], lines[index + 1][1]
        if _STARTS_PARAGRAPH.match(following):
            runs_on = False
        elif full(index):
            runs_on = True
        else:
            runs_on = first_line_style and next_start < margin + 0.8 * size and not plainly_short(index)
        if not runs_on and text[-1] not in _ENDED:
            out += "；"
        elif runs_on:
            out += between(text, following)
    if re.fullmatch(r"[\d.．；\s（）()]+", out):
        out = re.sub(r"[；\s]+(?=[（(）)])", "", out)   # "2.2.4" over "（4）" in a narrow number cell is one number
    return out.replace("|", "／")


def cell_text(pieces: Sequence[Piece], left: float, right: float) -> str:
    return join_lines(cell_lines(pieces), left, right)


class Cell:
    __slots__ = ("x0", "x1", "text")

    def __init__(self, x0: float, x1: float, text: str) -> None:
        self.x0, self.x1, self.text = x0, x1, text


class Table:
    """The rows of one ruled table on one page; every row the cells it really has (a merged cell is one cell)."""

    __slots__ = ("rows", "follows")

    def __init__(self, rows: List[List[Cell]]) -> None:
        self.rows = rows
        self.follows = False        # goes on from the table that ended the page before

    def render(self) -> str:
        return "\n".join("| " + " | ".join(cell.text for cell in row) + " |" for row in self.rows)


def table_rows(grid: Grid, pieces: Sequence[Piece]) -> List[List[Cell]]:
    spans = grid.spans()
    held: Dict[Tuple[int, int], List[Piece]] = {}
    for piece in pieces:
        if piece.text.strip():
            held.setdefault(spans[grid.cell(piece.x, piece.y)], []).append(piece)
    rows_n, cols_n = len(grid.ys) - 1, len(grid.xs) - 1
    edges: Dict[Tuple[int, int], Tuple[float, float]] = {}
    for (r, c), root in spans.items():
        lo, hi = edges.get(root, (grid.xs[c], grid.xs[c + 1]))
        edges[root] = (min(lo, grid.xs[c]), max(hi, grid.xs[c + 1]))
    lines = {root: cell_lines(found) for root, found in held.items()}
    # how far the full lines of a column really reach: the longest line among the cells that share its edges
    reach: Dict[Tuple[int, int], float] = {}
    for root, found in lines.items():
        key = (round(edges[root][0]), round(edges[root][1]))
        reach[key] = max([reach.get(key, 0.0)] + [line_end for _, _, line_end, _ in found])
    texts = {}
    for root, found in lines.items():
        x0, x1 = edges[root]
        far = reach[(round(x0), round(x1))]
        texts[root] = join_lines(found, x0, x1, min(far, x1 - 2) if far >= x0 + 0.6 * (x1 - x0) else 0.0)
    rows: List[List[Cell]] = []
    for r in range(rows_n):
        row: List[Cell] = []
        own = False
        for c in range(cols_n):
            root = spans[(r, c)]
            if c and spans[(r, c - 1)] == root:
                continue                # the same merged cell, one column further
            # a cell spanning rows is repeated in each of them, so every row reads whole
            row.append(Cell(edges[root][0], edges[root][1], texts.get(root, "")))
            own = own or (root[0] == r and bool(texts.get(root)))
        if own:
            rows.append(row)
    # the sliver between a frame drawn around a value and the cell's own edge is no cell of the table
    return [[cell for cell in row if cell.text or cell.x1 - cell.x0 >= 12] for row in rows]


Layout = List[Any]      # the page in reading order: str (running text as pypdf gives it) and Table


def _underlines(rule: Rule, pieces: Sequence[Piece]) -> bool:
    """A line drawn UNDER words is no edge of a cell: it stands a point or two below their baseline, where the
    border of a cell keeps the descenders' depth and the cell's padding away (3 pt and more). Taken for a border it
    cuts one row into several at every underlined line."""
    y, x0, x1 = rule
    for piece in pieces:
        if piece.text.strip() and piece.x <= x1 and 0.2 <= piece.y - y <= max(1.8, 0.2 * (piece.size or 10)):
            return True
    return False


def two_columns(pieces: Sequence[Piece], width: float) -> Optional[float]:
    """Where the gutter of a page of prose in two columns runs - None for every other page.

    Two columns of PROSE: text starts at two left edges, a good dozen lines at each; the lines of the left column
    mostly run up to the gutter (they are full lines of a paragraph), and none runs through it. Labels with their
    values beside them are short on the left and are left alone: read column-wise, every label would lose its value."""
    lines: Dict[int, List[Piece]] = {}
    for piece in pieces:
        if piece.text.strip():
            lines.setdefault(round(piece.y / 2), []).append(piece)
    starts = sorted(min(p.x for p in line) for line in lines.values())
    if len(starts) < 24:
        return None
    left_edge = starts[len(starts) // 10]
    right_starts = [x for line in lines.values() for x in [min((p.x for p in line if p.x > width * 0.42), default=None)] if x is not None]
    if len(right_starts) < 12:
        return None
    gutter_right = sorted(right_starts)[len(right_starts) // 2]        # where the right column's lines begin
    if gutter_right < left_edge + width * 0.3 or sum(1 for x in right_starts if abs(x - gutter_right) <= 3) < len(right_starts) * 0.7:
        return None
    full = crossing = left_lines = 0
    for line in lines.values():
        left = [p for p in line if p.x < gutter_right - 2]
        if not left:
            continue
        left_lines += 1
        size = max(p.size for p in left) or 10.0
        end = max(p.x + _width(p.text.strip(), p.size or size) for p in left)
        if len({round(p.x, 1) for p in left}) == 1:
            end = left[0].x + _width("".join(p.text for p in left).strip(), size)
        crossing += end > gutter_right + 2 * size
        full += gutter_right - 5 * size <= end <= gutter_right + 2 * size
    if left_lines < 12 or crossing > left_lines * 0.1 or full < left_lines * 0.6:
        return None
    return gutter_right - 1.0


def _column_text(pieces: Sequence[Piece]) -> str:
    out: List[str] = []
    for text, _, _, _ in cell_lines(pieces):
        out.append(text)
    return "\n".join(out) + ("\n" if out else "")


def page_layout(parts: PageParts, drop: Optional[Set[int]] = None) -> Layout:
    pieces = [p for index, p in enumerate(parts.pieces) if not (drop and index in drop)]
    plain = "".join(p.text for p in pieces)
    if not parts.upright or len(parts.segments) > 20000:
        return [plain]
    across, down = rules_of(parts.segments)
    grids = grids_of([rule for rule in across if not _underlines(rule, pieces)], down) if parts.segments else []
    if not grids:
        gutter = two_columns(pieces, parts.width)
        if gutter is not None:
            return [_column_text([p for p in pieces if p.x < gutter]) + _column_text([p for p in pieces if p.x >= gutter])]
        return [plain]
    inside: Dict[int, List[Piece]] = {}
    order: List[Any] = []
    in_table = False
    for piece in pieces:
        if not piece.text.strip():
            if not in_table:
                order.append(piece.text)
            continue
        index = next((i for i, grid in enumerate(grids) if grid.holds(piece.x, piece.y)), None)
        if index is None:
            in_table = False
            order.append(piece.text)
            continue
        in_table = True
        if index not in inside:
            order.append(index)
        inside.setdefault(index, []).append(piece)
    layout: Layout = []
    for item in order:
        if isinstance(item, str):
            if layout and isinstance(layout[-1], str):
                layout[-1] += item
            else:
                layout.append(item)
        else:
            rows = table_rows(grids[item], inside[item])
            if rows:
                layout.append(Table(rows))
    return layout


def _said(row: Sequence[Cell]) -> List[str]:
    return [re.sub(r"\s+", "", cell.text) for cell in row if cell.text.strip()]


_NUMBER_CELL = re.compile(r"^\d+(?:[.．]\d+)*\s*(?:[（(]\s*\d+\s*[)）]?)?(?:\s*[；;、,，]\s*\d+(?:[.．]\d+)*)*$")   # "2.1.1；2.1.3"
_NUMBERED_HEAD = ("条款号", "序号", "编号", "项号", "条款")


def _numbered(head: Optional[Sequence[Cell]]) -> bool:
    said = _said(head or [])
    return bool(said) and said[0] in _NUMBERED_HEAD


def join_pages(layouts: Sequence[Layout]) -> None:
    """One table over several pages is one table: the header Word repeats at the top of a page goes, a row the page
    break cut in two is put together again (cell to cell by where they stand, not by how many there are), and a row
    whose number and name stood on the page before reads with them."""
    chain_head: Optional[List[Cell]] = None
    last: Optional[Table] = None
    for layout in layouts:
        solid = [item for item in layout if isinstance(item, Table) or item.strip()]
        first = solid[0] if solid else None
        if last is not None and isinstance(first, Table) and chain_head is not None:
            first.follows = True
            if first.rows and _said(first.rows[0]) == _said(chain_head):
                first.rows.pop(0)
            numbered = _numbered(chain_head) or bool(last.rows and _NUMBER_CELL.match(last.rows[-1][0].text.strip()))
            if first.rows and last.rows and numbered:
                row, above = first.rows[0], last.rows[-1]
                lead = row[0].text.strip()
                loose = not lead or (len(lead) <= 3 and not re.search(r"[\w一-鿿]", lead))     # "" or the "）" of a number cut in two
                if loose and any(cell.text for cell in row):
                    pairs = [(_over(above, cell), cell) for cell in row if cell.text]
                    # the rest of ONE cell (nothing else in the row), or a sentence broken off in mid-air: the same row.
                    # Several cells, each going on from a finished one: a row of its own under the same clause.
                    cut = bool(lead) or len(pairs) == 1 or any(
                        old is not None and len(old.text) >= 12 and old.text[-1] not in "。；;！？" and re.search(r"[一-鿿]", new.text)
                        for old, new in pairs)     # "3-5分" is a finished cell though it ends in no full stop
                    if cut:
                        for old, new in pairs:
                            if old is not None:
                                old.text += new.text
                        first.rows.pop(0)
        for item in layout:
            if not isinstance(item, Table):
                continue
            head = chain_head if (item.follows and chain_head is not None) else (item.rows[0] if item.rows else None)
            above_row: Optional[List[Cell]] = last.rows[-1] if (item.follows and last is not None and last.rows) else None
            for row in item.rows:
                if (_numbered(head) and above_row is not None and not row[0].text.strip() and any(cell.text for cell in row)
                        and _NUMBER_CELL.match(above_row[0].text.strip())):
                    for at in range(min(2, len(row), len(above_row))):
                        if row[at].text.strip():
                            break
                        row[at].text = above_row[at].text
                above_row = row
            if not item.follows:
                chain_head = item.rows[0] if item.rows else None
        tail = solid[-1] if solid else None
        if isinstance(tail, Table):
            if tail.rows or last is None:
                last = tail
        else:
            last, chain_head = None, None


def _over(row: Sequence[Cell], cell: Cell) -> Optional[Cell]:
    """The cell of ``row`` standing above/below ``cell``: the one sharing most of its width."""
    best, shared = None, 0.0
    for other in row:
        common = min(other.x1, cell.x1) - max(other.x0, cell.x0)
        if common > shared:
            best, shared = other, common
    return best


def render(layout: Layout) -> str:
    out = ""
    for item in layout:
        if isinstance(item, Table):
            if item.rows:
                out += ("\n" if out and not out.endswith("\n") else "") + item.render() + "\n"
        else:
            out += item
    return out


def page_text(page: Any) -> str:
    """One page on its own: its text with its ruled tables as Markdown rows; plain pypdf text when it draws none."""
    try:
        parts = page if isinstance(page, PageParts) else read_page(page)
    except Exception:   # noqa: BLE001 - a visitor pypdf cannot serve: the plain text is still there
        return page.extract_text() or ""
    return render(page_layout(parts))


def document_texts(reader: Any, max_pages: Optional[int] = None) -> List[str]:
    """The text of every page, tables rebuilt, furniture left out. A page pypdf cannot walk gives its plain text."""
    pages: List[Any] = []
    for number, page in enumerate(reader.pages):
        if max_pages is not None and number >= max_pages:
            break
        try:
            pages.append(read_page(page))
        except Exception:   # noqa: BLE001
            try:
                pages.append(page.extract_text() or "")
            except Exception:   # noqa: BLE001
                pages.append("")
    walked = [p for p in pages if isinstance(p, PageParts)]
    drops = iter(furniture(walked))
    layouts: List[Layout] = [[p] if isinstance(p, str) else page_layout(p, next(drops)) for p in pages]
    join_pages(layouts)
    return [render(layout) for layout in layouts]
