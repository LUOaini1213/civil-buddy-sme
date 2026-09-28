"""What a model reply claims about a link record or a plan, checked against the record.

The number guard (number_provenance) asks whether a figure appears somewhere in the turn's evidence. That misses
three things a live 3B model did on 2026-09-26: it reported the 40HQ rated payload (28,610 kg, present in the tool
result) as the heaviest container's mass; it described clause 4.10 as "materials of the pre-embedded parts" when the
clause is about cargo securing (the clause number was in the user's words, so nothing flagged); and a scripted
compromised reply said "All seven clauses are covered". Each of those has a figure or a word that exists; what is
wrong is what it is attached to. This module attaches them:

  status      a sentence that names statements (S1, S2-S3, S4 to S7) and a status must give each named statement
              the status the record gives it; "all ... covered" must match a record where all are covered
  type        a container type named as the plan's must be the record's (a sentence about the tender, a clause or
              a negation may name another)
  mass        a kg figure in a sentence about the heaviest / each loaded container must be the record's figure for
              the quantity the nearest label names: gross -> max_gross_kg, cargo -> max_cargo_kg, tare -> the
              tare, limit -> the clause limit; no label -> gross or cargo. A figure named as a rating or payload,
              a total or a per-crate mass is another quantity and is left to the number guard
  clause      "Clause X asks for ..." must share at least one content word with clause X's text in the record
              (English only, sentences with three or more content words: a heuristic, not a parser)
              ("met", "satisfied", "complies" count as covered)
  approval    "approved for submission", "ready to submit / book", "can be submitted", "is signed off", "is compliant
              with all ...", "you can book the containers", "the submission can go ahead" (English;
              the Chinese forms are verdict_guard's): no draft is, since every one is submit_blocked = true and
              confirmed_by_person = false; a hedged sentence (not, until, once, must, if, a question) is left alone.
              This one needs no record, so it runs on every checked reply.

Facts come only from tool results (read_link_record, pack_plan, the deterministic link). A sentence that fails is
struck from the reply and listed; it never changes a status, a figure or a type, which stay the record's.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

_SPLIT = re.compile(r"(?<=[。！？!?；;\n])|(?<=\.)(?=\s|$)")
_IDS = re.compile(r"\bS\s?([1-9]\d?)\b(?:\s*(?:-|–|—|to|through|至|到|~)\s*S?\s?([1-9]\d?)\b)?")
_ALL = re.compile(r"(?i)\b(?:all|every|each)\b[^.;。；]{0,40}?\b(?:statements?|clauses?|items?|requirements?)\b[^.;。；]{0,25}?"
                  r"\b(?:are|is|were|was|been|now)\s+(?:all\s+|fully\s+|now\s+)?(?:covered|met|satisfied|fulfilled|complied\s+with)\b|"
                  r"全部(?:已)?覆盖|均已覆盖|都已覆盖|全部满足|均已满足|都已满足")
_NOT_COVERED = re.compile(r"(?i)\b(?:not|never|no longer|isn't|aren't|un)\s*(?:yet\s+|fully\s+)?(?:covered|met|satisfied|fulfilled)\b|"
                          r"未覆盖|没有覆盖|不能覆盖|无法覆盖|未满足")
_STATUS = {
    # "met", "satisfied", "complies" say covered in other words (reviewer's probe of PR #71, 2026-09-27)
    "covered": re.compile(r"(?i)\bcovered\b|\bmet\b|\bsatisfie[sd]\b|\bfulfill?ed\b|\bcompl(?:ies|ied|iant)\b|已覆盖|覆盖|已满足"),
    "partial": re.compile(r"(?i)\bpartial(?:ly)?\b|\bpartly\b|部分"),
    "gap": re.compile(r"(?i)\bgaps?\b|缺口|不满足"),
    "human_required": re.compile(r"(?i)human[_\s]required|\bfor a person\b|\b(?:a|the) person\b|\bpeople\b|\bhuman\b|人工|由人|待人|需人"),
}
_TYPE = re.compile(r"(?<![A-Za-z0-9])(20|40|45)\s?(GP|HQ|HC|OT|FR|RF|DV)(?![A-Za-z0-9])", re.I)
_ABOUT_TENDER = re.compile(r"(?i)\bclause\b|\btender\b|\bitt\b|\brequire[sd]?\b|\basks?\b|条款|招标|第\s*\d")
_NEGATION = re.compile(r"(?i)\bnot\b|\bno\b|\bnever\b|\binstead of\b|\brather than\b|不是|而非|不用|不能")
_MASS_TOPIC = re.compile(r"(?i)heaviest|\bmax(?:imum)?\.?\s+(?:gross|cargo|mass|weight)|\b(?:per|each|every)\s+(?:loaded\s+)?container|"
                         r"最重|单柜|每柜|每个柜")
_KG = re.compile(r"(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s*(kg|公斤|千克|t\b|tonnes?|吨)", re.I)
_LABELS = (
    ("gross", re.compile(r"(?i)\bgross\b|毛重|总重")),
    ("tare", re.compile(r"(?i)\btare\b|柜重|自重|皮重")),
    ("payload", re.compile(r"(?i)payload|\brat(?:ed|ing)\b|\bmax(?:imum)?\.?\s+load|额定|载重量")),
    ("limit", re.compile(r"(?i)\blimit|\bnot\s+exceed|\bexceed|\bunder\b|\bmargin\b|上限|不超过|不得超过|余量")),
    ("cargo", re.compile(r"(?i)\bcargo\b|\bnet\b|\bpanels?\b|\bcrates?\b|\bcarries\b|货重|净重|货物")),
    ("other", re.compile(r"(?i)\btotal\b|\bsum\b|合计|总计|\beach crate\b|\bper crate\b|单箱|单件")),
)
_LIMIT_WORD = re.compile(r"(?i)\blimit|\bnot\s+exceed|\bexceed|\ballowed\b|上限|不超过|不得超过|限")
_APPROVAL = re.compile(r"(?i)\bapproved\s+for\s+(?:submission|booking|issue|tender)\b|\bready\s+(?:to|for)\s+(?:be\s+)?(?:submit|submission|"
                       r"submitted|book|booking|booked)\b|\b(?:can|may)\s+(?:now\s+)?(?:be\s+)?(?:submitted|booked)\b|"
                       r"\b(?:is|are|was|were|has\s+been|have\s+been|now)\s+(?:fully\s+)?(?:approved|signed[\s-]off|certified)\b|"
                       r"\b(?:is|are)\s+(?:fully\s+)?compliant\s+with\s+(?:all|every|the\s+(?:tender|itt))\b|"
                       r"\b(?:can|may)\s+(?:now\s+)?(?:book|submit|issue)\s+(?:the\s+|these\s+|your\s+)?(?:bid|tender|response|"
                       r"submission|containers?|booking|shipment)\b|"
                       r"\b(?:submission|booking|bid|shipment)\s+(?:can|may)\s+(?:now\s+)?go\s+ahead\b|"
                       r"\b(?:draft|bid|response)\s+(?:is\s+final\s+and\s+)?(?:may|can)\s+(?:now\s+)?be\s+issued\b")
_HEDGE = re.compile(r"(?i)\bnot\b|\bno\b|\bnever\b|n't\b|\bcannot\b|\buntil\b|\bbefore\b|\bonce\b|\bafter\b|\bwhen\b|\bwhether\b|"
                    r"\bif\b|\bmust\b|\bneeds?\b|\bonly\b|\?")
_NAMES = {"gross": "heaviest container gross mass", "cargo": "heaviest container cargo mass", "tare": "container tare",
          "limit": "clause limit", "mass": "heaviest container mass"}
_CLAUSE_REF = re.compile(r"(?i)\bclause\s+(\d+(?:\.\d+)*)|第\s*(\d+(?:\.\d+)*)\s*条")
_WORD = re.compile(r"[A-Za-z][A-Za-z-]{3,}")
_STOP = set("""clause clauses asks asked ask requires required require says said states stated tender itt shall
should must this that these those what which with from into about their there where when will would could each every
other only also than then them they your yours have been being does done make made makes more most such very
""".split())


def sentences(text: str) -> List[Dict[str, Any]]:
    out, pos = [], 0
    for part in _SPLIT.split(text or ""):
        if part.strip():
            out.append({"text": part, "start": pos, "end": pos + len(part)})
        pos += len(part)
    return out


def facts_from(name: str, result: Dict[str, Any], facts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Fold one tool result into the turn's facts. Only results that carry record or plan figures add anything."""
    facts = facts if facts is not None else {}
    if not isinstance(result, dict) or result.get("ok") is False:
        return facts
    if name == "read_link_record" or result.get("schema") == "tender.link_record.view.v1":
        facts["statuses"] = {str(s.get("id")): str(s.get("status")) for s in result.get("statements") or [] if s.get("id")}
        facts["clauses"] = {str(c.get("clause")): str(c.get("text") or "") for c in result.get("clauses") or [] if c.get("clause")}
        if result.get("container_type"):
            facts["container_type"] = str(result["container_type"]).upper()
        _masses(facts, result.get("heaviest_container") or {})
    elif name == "pack_plan":
        if result.get("container_type"):
            facts["container_type"] = str(result["container_type"]).upper()
        _masses(facts, result.get("heaviest_container") or {})
    return facts


def facts_from_record(record: Dict[str, Any]) -> Dict[str, Any]:
    from packing_assistant.tender_packing_link import link_record_view

    return facts_from("read_link_record", link_record_view(record))


def _masses(facts: Dict[str, Any], heaviest: Dict[str, Any]) -> None:
    for key in ("max_gross_kg", "max_cargo_kg", "container_tare_kg"):
        if heaviest.get(key) is not None:
            facts.setdefault(key, [])
            if float(heaviest[key]) not in facts[key]:
                facts[key].append(float(heaviest[key]))
    limit = heaviest.get("limit_kg")
    for value in (limit if isinstance(limit, list) else [limit]):
        if isinstance(value, (int, float)) and float(value) not in facts.setdefault("limit_kg", []):
            facts["limit_kg"].append(float(value))


def _ids(sentence: str) -> List[str]:
    found: List[str] = []
    for m in _IDS.finditer(sentence):
        first = int(m.group(1))
        last = int(m.group(2)) if m.group(2) else first
        found += [f"S{n}" for n in range(first, max(first, last) + 1)][:12]
    return found


def _kg_values(sentence: str) -> List[Dict[str, Any]]:
    values = []
    for m in _KG.finditer(sentence):
        number = float(m.group(1).replace(",", "") + ("." + m.group(2) if m.group(2) else ""))
        if m.group(3).lower() in {"t", "tonne", "tonnes", "吨"}:
            number *= 1000.0
        values.append({"value": number, "text": m.group(0), "start": m.start()})
    return values


def _label_before(before: str) -> str:
    """The quantity a kg figure is said to be: the label word nearest before it in its sentence (mass when none)."""
    best, where = "mass", -1
    window = before[-80:]
    for label, rx in _LABELS:
        for m in rx.finditer(window):
            if m.start() > where:
                best, where = label, m.start()
    return best


_NEARLY = re.compile(r"(?i)\b(?:nearly|almost|virtually|practically)\s+$")


_NEARLY_OR_MOST = re.compile(r"(?i)\b(?:nearly|almost|virtually|practically|most|majority)\b")


# A condition before the claim, in its clause or leading its sentence: "If / Until / Unless all clauses are covered".
_ALL_CONDITION = re.compile(r"(?i)\b(?:if|once|unless|until|when|whenever|after|before|provided|providing|assuming|"
                            r"as\s+soon\s+as|in\s+case|subject\s+to|pending)\b")


def _all_stated(text: str, match: re.Match) -> bool:
    """An "all covered" claim the sentence makes, not one it negates, asks or sets as a condition: "Not all clauses are
    covered", "并非所有条款均已覆盖", "If all clauses are covered, a person still signs off", "Until all clauses are
    covered, the bid stays blocked", "Are all clauses covered?" say the opposite or nothing (review of #71, 2026-09-27).

    Only those three let a sentence through. Reported, quoted, hedged ("would appear", "as expected") or trailing
    conditions ("... covered following the rev B update", "... after the latest run") do not: the coverage is the
    record's fact, and a sentence that says every statement is covered contradicts a record where one is not, whoever
    it is attributed to (review of #74: the claim check's full exemption list let those seven through, main struck them)."""
    from packing_assistant.tools import claim_check, verdict_guard

    if _NEARLY.search(text, 0, match.start()):
        return False            # "nearly all ... covered" is a count; claim_check holds it to the record
    if not re.search(r"[A-Za-z]", match.group(0)):
        return claim_check._zh_stated(text, match)
    if not verdict_guard._english_stated(text, match, negation=True, conditions=False, questions=True, reported=False,
                                         quotes=False):
        return False            # negated in its clause (a leading "with no ..." phrase does not count), or asked
    clause = text[verdict_guard._last_break(verdict_guard._EN_CLAUSE_BREAK_BEFORE, text, match.start()): match.start()]
    sentence = text[verdict_guard._last_break(verdict_guard._EN_SENTENCE_BREAK, text, match.start()): match.start()]
    for m in _ALL_CONDITION.finditer(clause):
        if m.group(0).lower() not in ("after", "when") or verdict_guard._EN_SUBORDINATE.match(clause[m.end():] + match.group(0)):
            return False
    return not verdict_guard._en_condition_lead(sentence)


def _status_problem(text: str, statuses: Dict[str, str]) -> str:
    negated = bool(_NOT_COVERED.search(text))
    claimed = {k for k, rx in _STATUS.items() if rx.search(text)}
    if negated:
        claimed.discard("covered")
    if (not negated and any(v != "covered" for v in statuses.values())
            and any(_all_stated(text, m) for m in _ALL.finditer(text))):
        return "the record does not have every statement covered"
    if _NEARLY_OR_MOST.search(text):
        # "nearly all / most ... covered" is a count: held to the statuses the same way claim_check holds it to a
        # link record file (a read_link_record turn has the statuses but may have no file)
        from packing_assistant.tools import claim_check

        view = {"statements": [{"id": sid, "clause": sid, "status": status} for sid, status in statuses.items()]}
        if any(item["kind"] in ("near", "most") for item in claim_check.overclaims(text, view)):
            return "the record has fewer statements covered than the sentence says"
    for sid in _ids(text):
        if claimed and sid in statuses and statuses[sid] not in claimed:
            return f"the record gives {sid} the status {statuses[sid]}"
    return ""


def _type_problem(text: str, ctype: str) -> str:
    named = {(m.group(1) + m.group(2)).upper().replace("HC", "HQ") for m in _TYPE.finditer(text)}
    other = sorted(named - {ctype})
    if other and not _ABOUT_TENDER.search(text) and not _NEGATION.search(text):
        return f"the plan in the record is {ctype}, not {', '.join(other)}"
    return ""


def _mass_problem(text: str, facts: Dict[str, Any]) -> str:
    if not _MASS_TOPIC.search(text) or not (facts.get("max_gross_kg") or facts.get("max_cargo_kg")):
        return ""
    for item in _kg_values(text):
        label = _label_before(text[: item["start"]])
        if label in {"payload", "other"}:
            continue                     # a correctly named rating, a total or a crate mass is another quantity
        allowed = {"gross": facts.get("max_gross_kg") or [], "cargo": facts.get("max_cargo_kg") or [],
                   "tare": facts.get("container_tare_kg") or [], "limit": facts.get("limit_kg") or [],
                   "mass": (facts.get("max_gross_kg") or []) + (facts.get("max_cargo_kg") or [])}[label]
        if label in {"limit", "tare"} and not allowed:
            continue
        if _LIMIT_WORD.search(text):        # "Clause 4.9 limits the gross mass ... to 20,000 kg": the limit is named
            allowed = allowed + (facts.get("limit_kg") or [])
        if not any(abs(item["value"] - v) <= 0.6 for v in allowed):
            shown = " or ".join(f"{v:,.1f}".rstrip("0").rstrip(".") for v in allowed) or "none"
            return f"{item['text']} is not the record's {_NAMES[label]} ({shown} kg)"
    return ""


def _clause_problem(text: str, clauses: Dict[str, str]) -> str:
    for m in _CLAUSE_REF.finditer(text):
        number = m.group(1) or m.group(2)
        body = clauses.get(number)
        if body is None:
            continue
        words = {w.lower() for w in _WORD.findall(text[m.end():])} - _STOP
        source = {w.lower()[:5] for w in _WORD.findall(body)}
        if len(words) >= 3 and not any(w[:5] in source for w in words):
            return f"nothing in it matches the text of Clause {number} in the record"
    return ""


def _approval_problem(text: str) -> str:
    """Every draft this system makes is submit_blocked = true and no record is confirmed_by_person until a person
    confirms it, so a sentence saying the bid is approved, signed off or ready to submit or book contradicts it."""
    if _APPROVAL.search(text) and not _HEDGE.search(text):
        return "every draft is submit_blocked = true and confirmed_by_person = false until a person confirms it"
    return ""


def mismatches(reply: str, facts: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Sentences of ``reply`` that attach a status, a type or a mass to the wrong thing, per the record's facts, or that
    say a draft is approved or ready to submit (true of no draft this system makes)."""
    found: List[Dict[str, Any]] = []
    statuses: Dict[str, str] = facts.get("statuses") or {}
    ctype = str(facts.get("container_type") or "").upper()
    clauses: Dict[str, str] = facts.get("clauses") or {}
    from packing_assistant.tools.verdict_guard import INVISIBLE, visible_variants

    for s in sentences(reply):
        text = s["text"]
        # a zero-width space or soft hyphen hides no claim: the sentence is also read with them as spaces and dropped
        # (review of #74, dev_round3_review.json); the struck span stays the original sentence
        why = ""
        for variant in ([text] + [v for v, _ in visible_variants(text)] if INVISIBLE.search(text) else [text]):
            why = ((_status_problem(variant, statuses) if statuses else "")
                   or (_type_problem(variant, ctype) if ctype else "")
                   or _mass_problem(variant, facts)
                   or (_clause_problem(variant, clauses) if clauses else "")
                   or _approval_problem(variant))
            if why:
                break
        if why:
            found.append({"kind": "record", "text": text.strip(), "start": s["start"], "end": s["end"], "why": why})
    return found


def strike(reply: str, found: Iterable[Dict[str, Any]]) -> str:
    """The reply with each flagged sentence replaced, right to left so positions stay valid."""
    out = reply
    for item in sorted(found, key=lambda entry: entry["start"], reverse=True):
        part = out[item["start"]: item["end"]]
        lead = part[: len(part) - len(part.lstrip())]
        out = out[: item["start"]] + lead + "(struck: does not match the link record / 与联动记录不符，已删去)" + out[item["end"]:]
    return out


def sentences_with(reply: str, spans: Iterable[Dict[str, Any]], why: str) -> List[Dict[str, Any]]:
    """The sentences of ``reply`` that contain any of ``spans`` (items with start/end), as strike items."""
    items = list(spans)
    out = []
    for s in sentences(reply):
        hit = [i for i in items if s["start"] <= int(i.get("start", -1)) < s["end"]]
        if hit:
            out.append({"kind": "untraced", "text": s["text"].strip(), "start": s["start"], "end": s["end"],
                        "why": why + ": " + ", ".join(str(i.get("text")) for i in hit)})
    return out


def notice(found: List[Dict[str, Any]]) -> str:
    if not found:
        return ""
    items = "; ".join(dict.fromkeys(f"\"{item['text'][:120]}\" ({item['why']})" for item in found))
    return "⚠ Struck from the reply because the link record says otherwise / 以下说法与记录不符，已从回复中删去: " + items
