"""A reply's coverage claims, checked against the link record the same turn wrote.

The linked run (tender_packing_link.py) writes tender-packing-link.json: one statement per logistics clause check,
each with a status (covered / partial / gap / human_required). A model that summarises the run can still say "all
seven clauses are covered" while the record has one covered row, for example after reading a planted instruction.
The verdict guard strikes universal verdicts, but it does not know the record. This check does:

    overclaims(reply, record) -> each coverage claim the record does not support
    correct(reply, found, record) -> the reply with each such claim replaced by what the record says

A claim is one of
    universal   all / every clause (statement, requirement ...) is covered, everything is covered, covers all clauses,
                全部覆盖 / 所有条款均已覆盖                           struck when any statement is not covered
    count       "6 of 7 statements are covered", "five clauses are covered", "7/7 covered", 7 条已覆盖
                                                                   struck when the number is above the record's
    nearly all  "nearly / almost all clauses are covered" is a count, not universal: struck when fewer than
                total - max(1, total // 5) are covered (6 of 7); "most / the majority of" when half or fewer are
    named       "S4 is covered", "S2 and S3 are covered", "S1 to S7 are covered"
                                                                   struck when a named statement is not covered
A claim that is negated, asked, conditional, quoted or reported is let through, by the same checks the verdict guard
uses. correct() replaces the whole sentence that carries a claim with the record's own sentence; splicing the record
into the claim's span left "Nearly [per the link record ...]" and "[...] by the plan" (review of #72, 2026-09-27).
Deterministic, no model. Only a turn that produced a link record is checked; without one there is nothing to
compare, and the verdict guard still strikes the universal forms.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from packing_assistant.tools import verdict_guard

LINK_FILE = "tender-packing-link.json"

_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                 "ten": 10, "eleven": 11, "twelve": 12}
_NUM = r"(?:\d{1,3}|" + "|".join(_NUMBER_WORDS) + r")"
_NOUN = r"(?:logistics\s+|tender\s+|ITT\s+|bid\s+)?(?:clauses?|statements?|requirements?|rows?|checks?|items?)"
_BE = r"(?:are|is|were|was|have\s+been|has\s+been)"
_COVERED = r"(?:(?:now|fully|all|already)\s+){0,2}covered"
_SID = r"S\d{1,2}"

_EN = re.compile("|".join((
    r"(?P<near>\b(?:nearly|almost|virtually|practically)\s+(?:all|every)\s+(?:of\s+the\s+|the\s+)?(?:" + _NUM + r"\s+)?" + _NOUN
    + r"\s+" + _BE + r"\s+" + _COVERED + r"\b)",
    r"(?P<most>\b(?:most|the\s+(?:vast\s+)?majority)\s+(?:of\s+(?:the\s+)?)?(?:" + _NUM + r"\s+)?" + _NOUN + r"\s+" + _BE
    + r"\s+" + _COVERED + r"\b)",
    r"(?P<all>\b(?:all|every|each)\s+(?:of\s+the\s+|the\s+)?(?:" + _NUM + r"\s+)?" + _NOUN + r"\s+" + _BE + r"\s+" + _COVERED + r"\b)",
    r"(?P<all2>\b(?:everything|all)\s+" + _BE + r"\s+" + _COVERED + r"\b)",
    r"(?P<all3>\bcovers?\s+(?:all|every|each)\s+(?:of\s+the\s+|the\s+)?(?:" + _NUM + r"\s+)?" + _NOUN + r"\b)",
    r"(?P<all4>\b(?:the\s+)?(?:whole\s+|entire\s+)?(?:tender|ITT|response|submission|bid)\s+is\s+(?:now\s+)?fully\s+covered\b)",
    r"(?P<count>\b(?P<n>" + _NUM + r")(?:\s*(?:of|out\s+of|/)\s*(?:the\s+)?" + _NUM + r")?\s+(?:the\s+)?" + _NOUN + r"\s+" + _BE
    + r"\s+" + _COVERED + r"\b)",
    r"(?P<frac>\b(?P<fn>\d{1,3})\s*/\s*(?P<fd>\d{1,3})\s+(?:" + _NOUN + r"\s+)?(?:" + _BE + r"\s+)?covered\b)",
    r"(?P<named>\b(?P<ids>" + _SID + r"(?:\s*(?:,|and|&|to|-|–|through)\s*" + _SID + r")*)\s+" + _BE + r"\s+" + _COVERED + r"\b)",
)), re.I)
_ZH = re.compile("|".join((
    r"(?P<all>(?:全部|所有|各项?|每一?条)(?:条款|应答|要求|物流条款)?(?:均|都|也)?(?:已经?|已)?(?:全部)?(?:被)?覆盖)",
    r"(?P<count>(?P<n>\d{1,3})\s*条(?:应答|条款|物流条款)?(?:均|都|全部)?(?:已经?|已)?(?:被)?覆盖)",
    r"(?P<named>(?P<ids>" + _SID + r"(?:\s*(?:、|,|，|和|与|至|到|-|–)\s*" + _SID + r")*)\s*(?:均|都)?(?:已经?|已)?(?:被)?覆盖)",
)))
# 不仅 / 不但 / 不光 / 不只 ("not only") affirm what follows (review of #74)
_ZH_NEG = re.compile(r"不(?![仅但光只])|未|没有?|无|非|是否|能否|尚")


def load_record(files: Iterable[Any]) -> Optional[Dict[str, Any]]:
    """The last link record among a turn's files ({"path": ...} rows or paths), or None."""
    for row in reversed(list(files or [])):
        path = str(row.get("path") or "") if isinstance(row, dict) else str(row)
        if Path(path).name == LINK_FILE:
            try:
                data = json.loads(Path(path).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            return data if isinstance(data, dict) and isinstance(data.get("statements"), list) else None
    return None


def tally(record: Dict[str, Any]) -> Dict[str, Any]:
    rows = [s for s in record.get("statements") or [] if isinstance(s, dict)]
    status = {str(s.get("id")): str(s.get("status")) for s in rows}
    counts = {k: sum(1 for v in status.values() if v == k) for k in ("covered", "partial", "gap", "human_required")}
    clauses_covered = len({str(s.get("clause")) for s in rows if s.get("status") == "covered"})
    return {"total": len(rows), "status": status, "clauses_covered": clauses_covered, **counts}


def _number(token: str) -> int:
    token = token.lower()
    return int(token) if token.isdigit() else _NUMBER_WORDS.get(token, 0)


def _ids(text: str) -> List[str]:
    found: List[str] = []
    parts = re.split(r"\s*(?:,|and|&|、|，|和|与)\s*", text)
    for part in parts:
        span = re.match(r"S(\d{1,2})\s*(?:to|-|–|through|至|到)\s*S(\d{1,2})$", part.strip(), re.I)
        if span:
            lo, hi = sorted((int(span.group(1)), int(span.group(2))))
            found += [f"S{i}" for i in range(lo, hi + 1)]
        else:
            found += [f"S{m}" for m in re.findall(r"S(\d{1,2})", part, re.I)]
    return found


def _zh_stated(blob: str, match: re.Match) -> bool:
    breaks = [m.end() for m in verdict_guard._CLAUSE_BREAK.finditer(blob, 0, match.start())]
    before = blob[breaks[-1] if breaks else 0: match.start()]
    inside = match.group(0)
    after = blob[match.end(): match.end() + 12]
    if _ZH_NEG.search(before) or re.search(r"未|没|不", inside):
        return False
    if verdict_guard._CONDITION_BEFORE.search(before) or verdict_guard._CONDITION_AFTER.search(after):
        return False
    return not verdict_guard._QUESTION_TAIL.search(after)


# A claim "reported" from the product's own sources is still the product's claim: "According to the link record, all
# clauses are covered" is exactly what this check compares with the record. Reported speech is let through only when
# the sentence does not name one of these as its source.
_OWN_SOURCE = re.compile(r"\b(?:(?:link\s+)?record|plan|link|matrix|analysis|check|run|response|we|i|our|my)\b", re.I)


def _en_stated(reply: str, match: re.Match) -> bool:
    flags = dict(negation=True, conditions=True, questions=True, quotes=True)
    if verdict_guard._english_stated(reply, match, reported=True, **flags):
        return True
    if not verdict_guard._english_stated(reply, match, reported=False, **flags):
        return False
    sentence = reply[verdict_guard._last_break(verdict_guard._EN_SENTENCE_BREAK, reply, match.start()): match.start()]
    return _OWN_SOURCE.search(sentence) is not None


def overclaims(reply: str, record: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Coverage claims in ``reply`` that the link record does not support."""
    if not reply or not record or not record.get("statements"):
        return []
    t = tally(record)
    found: List[Dict[str, Any]] = []
    for pattern, lang in ((_EN, "en"), (_ZH, "zh")):
        for m in pattern.finditer(reply):
            if any(f["start"] < m.end() and m.start() < f["end"] for f in found):
                continue
            stated = (_en_stated(reply, m) if lang == "en" else _zh_stated(reply, m))
            if not stated:
                continue
            kind = next(k for k in ("near", "most", "all", "all2", "all3", "all4", "count", "frac", "named")
                        if m.groupdict().get(k))
            wrong, claimed = False, None
            if kind == "near":
                claimed = max(1, t["total"] - max(1, t["total"] // 5))
                wrong = t["covered"] < claimed
            elif kind == "most":
                claimed = t["total"] // 2 + 1
                wrong = t["covered"] < claimed
            elif kind.startswith("all"):
                claimed, wrong = t["total"], t["covered"] < t["total"]
            elif kind == "count":
                claimed = _number(m.group("n"))
                noun_is_clause = re.search(r"clause|条款", m.group(0), re.I) is not None
                wrong = claimed > (t["clauses_covered"] if noun_is_clause else t["covered"])
            elif kind == "frac":
                claimed = int(m.group("fn"))
                wrong = claimed > t["covered"]
            elif kind == "named":
                named = _ids(m.group("ids"))
                bad = [i for i in named if t["status"].get(i.upper()) != "covered"]
                claimed, wrong = named, bool(bad)
            if wrong:
                found.append({"kind": kind, "text": m.group(0), "start": m.start(), "end": m.end(), "lang": lang,
                              "claimed": claimed})
    found.sort(key=lambda f: f["start"])
    return found


def record_sentence(record: Dict[str, Any], lang: str = "en", *, stop: bool = True) -> str:
    """What the record says, as one whole sentence ("1 of 7 statements is covered", "2 of 7 ... are covered");
    ``stop=False`` leaves off the full stop."""
    t = tally(record)
    if lang == "zh":
        return (f"据联动记录，{t['total']} 条应答中 {t['covered']} 条由装柜方案覆盖（{t['partial']} 条部分覆盖，"
                f"{t['gap']} 条缺口，{t['human_required']} 条待人判断）" + ("。" if stop else ""))
    verb = "is" if t["covered"] == 1 else "are"
    gaps = "gap" if t["gap"] == 1 else "gaps"
    return (f"Per the link record, {t['covered']} of {t['total']} statements {verb} covered by the plan "
            f"({t['partial']} partial, {t['gap']} {gaps}, {t['human_required']} for a person)" + ("." if stop else ""))


# A sentence starts after . ! ? followed by a space, or after ; a line break or a CJK stop, and ends at the next one.
_SENTENCE_START = re.compile(r"[.!?](?=\s)|[;\n。！？；]")
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)|[;\n。！？；]")
_BULLET = re.compile(r"(?:[-*•>]|\d{1,3}[.)])[ \t]+|#{1,6}[ \t]+")


def _sentence_span(text: str, start: int, end: int) -> tuple:
    """The whole sentence around text[start:end]: from its first non-space character through its closing mark (a line
    break is kept, not replaced)."""
    begin = verdict_guard._last_break(_SENTENCE_START, text, start)
    while begin < start and text[begin].isspace():
        begin += 1
    if begin == 0 or text[begin - 1] == "\n":
        # a list marker stays: "- All clauses are covered." becomes "- [Per the link record ...]." (review of #74)
        bullet = _BULLET.match(text, begin, start)
        if bullet:
            begin = bullet.end()
    stop = _SENTENCE_END.search(text, end)
    if stop is None:
        finish = len(text)
    else:
        finish = stop.start() if stop.group(0) == "\n" else stop.end()
    while finish > end and text[finish - 1] in " \t\r":
        finish -= 1
    return begin, finish


def correct(reply: str, found: List[Dict[str, Any]], record: Dict[str, Any]) -> str:
    """Each sentence that carries an unsupported claim replaced, right to left, by the record's own sentence. Two claims
    in one sentence replace it once."""
    spans: Dict[tuple, str] = {}
    for item in found:
        spans.setdefault(_sentence_span(reply, item["start"], item["end"]), item["lang"])
    out = reply
    for (begin, finish), lang in sorted(spans.items(), reverse=True):
        # the stop goes after the bracket, so the next sentence still starts where every sentence splitter expects
        mark = "。" if lang == "zh" else "."
        out = out[:begin] + "[" + record_sentence(record, lang, stop=False) + "]" + mark + out[finish:]
    return out


def notice(found: List[Dict[str, Any]], record: Dict[str, Any]) -> str:
    if not found:
        return ""
    t = tally(record)
    said = "; ".join(dict.fromkeys(f'"{f["text"]}"' for f in found))      # a claim repeated in the rewrite is named once
    return (f"⚠ Corrected from the link record ({LINK_FILE}): the reply said {said}; the record has {t['covered']} of "
            f"{t['total']} statements covered by the plan, and nothing is confirmed by a person.")
