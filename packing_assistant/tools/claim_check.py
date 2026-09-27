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
    named       "S4 is covered", "S2 and S3 are covered", "S1 to S7 are covered"
                                                                   struck when a named statement is not covered
A claim that is negated, asked, conditional, quoted or reported is let through, by the same checks the verdict guard
uses. Deterministic, no model. Only a turn that produced a link record is checked; without one there is nothing to
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
_ZH_NEG = re.compile(r"不|未|没有?|无|非|是否|能否|尚")


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
            stated = (verdict_guard._english_stated(reply, m, negation=True, conditions=True, questions=True, reported=True,
                                                    quotes=True) if lang == "en" else _zh_stated(reply, m))
            if not stated:
                continue
            kind = next(k for k in ("all", "all2", "all3", "all4", "count", "frac", "named") if m.groupdict().get(k))
            wrong, claimed = False, None
            if kind.startswith("all"):
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


def record_sentence(record: Dict[str, Any], lang: str = "en") -> str:
    t = tally(record)
    if lang == "zh":
        return (f"据联动记录，{t['total']} 条应答中 {t['covered']} 条由装柜方案覆盖（{t['partial']} 条部分覆盖，"
                f"{t['gap']} 条缺口，{t['human_required']} 条待人判断）")
    return (f"per the link record, {t['covered']} of {t['total']} statements are covered by the plan "
            f"({t['partial']} partial, {t['gap']} gap, {t['human_required']} for a person)")


def correct(reply: str, found: List[Dict[str, Any]], record: Dict[str, Any]) -> str:
    """Each unsupported claim replaced, right to left, by what the record says."""
    out = reply
    for item in sorted(found, key=lambda f: f["start"], reverse=True):
        out = out[: item["start"]] + "[" + record_sentence(record, item["lang"]) + "]" + out[item["end"]:]
    return out


def notice(found: List[Dict[str, Any]], record: Dict[str, Any]) -> str:
    if not found:
        return ""
    t = tally(record)
    said = "; ".join(f'"{f["text"]}"' for f in found)
    return (f"⚠ Corrected from the link record ({LINK_FILE}): the reply said {said}; the record has {t['covered']} of "
            f"{t['total']} statements covered by the plan, and nothing is confirmed by a person.")
