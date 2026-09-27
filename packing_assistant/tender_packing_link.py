"""Tender <-> packing, in one run and kept linked: the tender's logistics clauses, a loading plan made from the
real panel list under those clauses, and the English statements that answer them.

The partner's problem, in its words: the tender response and the outbound packing "run as two disconnected
exercises and need to stay linked". Bid statements on packing were not tied to a loading plan, and container
counts were not tied to the clauses they should satisfy. This module is the tie:

  tender file ──> logistics clauses (clause no., kind, limit)      container type taken FROM the clause
  panel list  ──> pack_ship_solve.run_plan (the demo's own path: conservation check, needs-human gates)
  both        ──> one matrix row per clause check, with status and the plan figure behind it
              ──> the English bid-book logistics section: every statement cites its clause and figure; what the
                  plan cannot support is a marked placeholder for a named person
              ──> tender-packing-link.json: statement -> clause -> plan figures -> sha256 of tender, list, plan

On a re-run the new record is compared with the previous one: every earlier statement is reported as changed,
unchanged or withdrawn, and the inputs that moved are named. No statement is carried forward silently.

Nothing the reader finds goes silent: a clause with a mass or container term in a transport or packing context
that it cannot place becomes a row for a person quoting it ("limit/requirement not placed"), and a tender with no
gross-mass or no securing clause gets a row saying so. A limit per stillage, crate, piece or crane lift is its own
row (per_package_limit) and a truck's GVW a site-access row: neither is ever compared with a container's gross mass.
Statements cite clauses the way the tender does ("Clause 4.9(a)", "Part C Clause 12", "Table 4-1, row 4.9",
"line 3 of itt.md"), never an invented number.

Not modelled, and so never "covered": securing / lashing to the CTU Code, A-frame stillages, upright transport,
no-stacking rules, delivery sequencing, site access and delivery hours, per-package / per-lift limits. A-frame
stillage sizes and masses are not guessed. A plan that does not fit (can_fit is not true: crates left out of the
containers) evidences no type, count or mass. A person confirms the plan before any booking; submit_blocked stays true.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCHEMA = "tender.packing_link.v1"
LINK_FILE = "tender-packing-link.json"
REPORT_FILE = "tender-packing-link.md"
BIDBOOK_FILE = "bidbook.en.md"
PLAN_FILE = "pack-plan.json"
DEFAULT_CONTAINER = "40HQ"

# kinds of logistics clause, in the order their statements are numbered (S1, S2, ...; "S" for statement, so they never read as the line references L7, L15 of the tender matrix)
KINDS = ("container_type", "containers_used", "gross_mass", "per_package_limit", "securing", "handling", "crate_structure",
         "delivery_sequence", "site_access", "logistics_plan_submission", "unplaced")
KIND_TITLE = {
    "container_type": "Container type",
    "containers_used": "Containers used",
    "gross_mass": "Gross mass per loaded container",
    "per_package_limit": "Mass limit per stillage / crate / lift (not per container)",
    "securing": "Cargo securing / lashing",
    "handling": "Handling: stillages, upright, stacking, protection",
    "crate_structure": "Crate / frame structure",
    "delivery_sequence": "Delivery sequence",
    "site_access": "Site access, vehicles and delivery hours",
    "logistics_plan_submission": "Packing / logistics plan submission",
    "unplaced": "Logistics limit or requirement not placed",
}
KIND_OWNER = {"container_type": "logistics", "containers_used": "logistics", "gross_mass": "logistics",
              "per_package_limit": "packing designer", "securing": "competent person (lashing)", "handling": "logistics",
              "crate_structure": "packing designer", "delivery_sequence": "project manager", "site_access": "project manager",
              "logistics_plan_submission": "logistics", "unplaced": "logistics"}

_NO_L = r"(?<![A-Za-z])"
_NO_R = r"(?![A-Za-z])"
_CONTEXT_RE = re.compile(r"transport|deliver|ship|pack|container|cargo|load|stillage|haul|lorr(?:y|ies)|truck|vehicle|"
                         r"crat(?:e|es|ing)|crane|hoist|lift|logistic|transit|freight|travel|call(?:ed)?[\s-]off|"
                         r"运输|包装|装柜|装箱|交货|发货|到货|集装箱|货物|货|柜", re.I)
# Mass figures include comma or space-grouped thousands; lower-case "mt" is metres.
_MASS_RE = re.compile(r"(?<![\d.,])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{1,3}(?:[ \u00a0\u2009\u202f]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
                      r"(kg|kgs|kilograms?|kilos?|tonnes?|tons?|metric\s+ton(?:ne)?s?|(?-i:MT)|t|公斤|千克|吨)(?![A-Za-z])", re.I)
_TONNES = ("t", "tonne", "tonnes", "ton", "tons", "mt", "吨")
# a load that is no transport load: "the design wind load of 2.4 kPa", "the dead load of each panel"
_STRUCTURAL_LOAD_RE = re.compile(r"(?:dead|live|wind|imposed|design|seismic|snow|floor|roof|point|distributed)\s+loads?", re.I)
_TRANSPORT_RE = re.compile(r"transport|deliver|ship|pack|cargo|(?<![A-Za-z])(?:load(?:s|ed|ing)?|laden)(?![A-Za-z])|stillage|haul|"
                           r"lorr(?:y|ies)|truck|vehicle|crat(?:e|es|ing)|crane|hoist|lift|freight|consign|logistic|transit|travel|arriv|"
                           r"运输|包装|装柜|装箱|交货|发货|到货|货物|吊装|塔吊|车辆进场", re.I)
_CONTAINER_TERM_RE = re.compile(r"(?<![A-Za-z])(?:containers?|CTUs?|VGM|MGW)(?![A-Za-z])|集装箱|货柜", re.I)
_MASS_WORD_RE = re.compile(r"(?<![A-Za-z])(?:weigh(?:s|t|ts|ed|ing)?|mass(?:es)?|payload|heav(?:y|ier|iest)|VGM|MGW|GVW|tonnage|"
                           r"loads?|loaded|laden|capacity|SWL)(?![A-Za-z])|重量|毛重|总重|限重|载重|重", re.I)
_LIMIT_RE = re.compile(r"exceed|(?:not|no)\s+(?:be\s+)?(?:more|heavier|greater)\s+than|more\s+than|heavier\s+than|in\s+excess\s+of|"
                       r"(?<![A-Za-z])max(?:imum|\.)?(?![A-Za-z])|limit|up\s+to|at\s+most|capacity|(?<![A-Za-z])(?:SWL|WLL|rated)(?![A-Za-z])|"
                       r"safe\s+working\s+load|"
                       r"≤|<=|不超过|不得超过|不大于|不应超过|不能超过|以内|最大|限重|超过", re.I)
# the subject of a mass limit: a container (its gross mass is checked against the plan), a package or a lift (a
# stillage, a crate, a crane's safe working load - never a container limit) or a vehicle (a truck's GVW includes the truck)
_SUBJECT_WORDS = {
    "container": r"containers?|CTUs?|(?:20|40|45)\s*(?:ft|foot|feet|['’])?\s*-?\s*(?:HQ|HC|GP|DV|DC)|集装箱|货柜|柜",
    "package": r"stillages?|crates?|(?:timber|wooden|packing)\s+cases?|packages?|pallets?|bundles?|racks?|skids?|panels?|pieces?|"
               r"items?|units?|lifts?|lifting|cranes?|hoists?|SWL|forklifts?|木箱|托盘|单件|构件|板块|塔吊|起重|吊",
    "vehicle": r"trucks?|lorr(?:y|ies)|vehicles?|trailers?|prime\s+movers?|GVW|axles?|车辆|货车|卡车",
}
_SUBJECT_RE = re.compile("|".join(f"(?P<{name}>(?<![A-Za-z])(?:{words})(?![A-Za-z]))" for name, words in _SUBJECT_WORDS.items()), re.I)
_ANY_SUBJECT = "|".join(f"(?:{words})" for words in _SUBJECT_WORDS.values())
# how a limit names its subject, most specific first: "the gross mass of each loaded container", "per stillage",
# "each delivery truck", "container weight"
_SUBJECT_FRAMES = (
    re.compile(r"(?:mass|weight|payload|VGM|load|limit|tonnage)\s+(?:of|per|for|in)\s+(?:(?:each|every|any|a|an|the|one|single|"
               r"individual|loaded|laden|packed|such)\s+){0,3}(?<![A-Za-z])(?:" + _ANY_SUBJECT + r")(?![A-Za-z])", re.I),
    re.compile(r"(?<![A-Za-z])(?:per|each|every|any|single|individual|no)\s+(?:[\w'’-]+\s+){0,2}?(?:" + _ANY_SUBJECT
               + r")(?![A-Za-z])|每(?:个|只|件)?(?:" + _ANY_SUBJECT + r")", re.I),
    re.compile(r"(?<![A-Za-z])(?:" + _ANY_SUBJECT + r")\s+(?:gross\s+|total\s+|loaded\s+)?(?:mass|weight|payload|load)", re.I),
)
# "20 t per container", "2,000 kg each crate", "1.5 t/stillage": the subject named right after the figure
_AFTER_FIGURE_RE = re.compile(r"\s*(?:gross\s+|net\s+|max(?:imum|\.)?\s+)?(?:(?:per|each|a|an)\s+|/\s*)(?:loaded\s+|single\s+|laden\s+)?"
                             r"(?:" + _ANY_SUBJECT + r")(?![A-Za-z])", re.I)
_EXCL_TARE_RE = re.compile(r"(?:excluding|excl\.?|exclusive\s+of|not\s+including|net\s+of|without)\s+(?:the\s+)?(?:container(?:['’]s)?\s+)?tare|"
                           r"不含(?:箱体|柜)?(?:自重|皮重)|不含柜", re.I)
_TARE_RE = re.compile(r"tare|including\s+the\s+container|incl\.?\s+(?:the\s+)?container|all[\s-]in|box\s+included|含柜|柜重|箱体自重|含箱|皮重",
                      re.I)
_CARGO_BASIS_RE = re.compile(r"payload|cargo\s+(?:mass|weight)|net\s+(?:mass|weight)|货重|货载|净重", re.I)
_GROSS_WORD_RE = re.compile(r"gross|(?<![A-Za-z])(?:VGM|MGW)(?![A-Za-z])|毛重", re.I)
_LIMIT_NOT_RE = re.compile(r"(?<![A-Za-z])(?:not|no)\s+(?:to\s+)?(?:(?:be|weigh|carry)\s+)?(?:exceed(?:ing)?|more\s+than|heavier\s+than|greater\s+than)"
                           r"|不(?:得|应|能)?(?:超过|大于)", re.I)
_LOWER_BOUND_RE = re.compile(r"\b(?:min(?:imum)?|at\s+least|not\s+less\s+than|no\s+less\s+than)\b|>=|≥|至少|最低|不小于", re.I)
_POSITIVE_COMPARISON_RE = re.compile(r"\b(?:exceed(?:s|ed|ing)?|(?:more|heavier|greater)\s+than|in\s+excess\s+of)\b|超过|大于", re.I)
_REJECT_OVER_RE = re.compile(r"\b(?:not|never)\s+(?:be\s+)?(?:accepted|allowed|permitted)\b|不得(?:进场|入场)|拒收", re.I)
_NO_SUBJECT_UPPER_RE = re.compile(
    r"\bno\s+(?:[\w'-]+\s+){0,3}(?:" + _ANY_SUBJECT + r")(?![A-Za-z])"
    r"(?:(?!\b(?:and|while|but)\b)[^.;,:\d]){0,100}?"
    r"(?:exceed(?:s|ed|ing)?|(?:more|heavier|greater)\s+than)\b", re.I)
_SENTENCE_RE = re.compile(r"(?<=[;。；])\s*|(?<![Mm]ax)(?<!No)(?<!Nos)(?<!Cl)(?<!incl)(?<!excl)(?<!approx)(?<!Art)"
                          r"(?<=[a-z0-9)\]%])\.\s+(?=[A-Z(])")
_SEQUENCE_RE = re.compile(r"sequenc|installation\s+programme|installation\s+program|delivery\s+schedule|just[\s-]in[\s-]time|"
                          r"call(?:ed)?[\s-]off|order\s+of\s+(?:erection|installation)|level\s+by\s+level|floor\s+by\s+floor|"
                          r"按.{0,8}(?:顺序|进度)|分批|交货计划|到货计划", re.I)
# words the CTU Code's securing uses beyond lash / secure (tender_parse._LASHING_RE)
_SECURING_EXTRA_RE = re.compile(r"(?<![A-Za-z])(?:restrain(?:ed|t|ts|ing)?|chock(?:s|ed|ing)?|tie[\s-]?downs?|strap(?:s|ped|ping)?)(?![A-Za-z])",
                                re.I)
# a container type spelt out: "40ft general purpose", "20' dry", "40DV" are the planner's GP
_GP_RE = re.compile(r"(?<![A-Za-z0-9])(20|40|45)\s*-?\s*(?:ft|foot|feet|['’])?\.?[\s-]*(?:general[\s-]purpose|dry(?:[\s-](?:van|cargo))?|DV|DC|"
                    r"standard|STD)(?![A-Za-z])", re.I)
# the tender asks for the plan itself: "submit a packing and logistics plan ... 14 days prior to the first delivery"
_PLAN_RE = re.compile(r"(?:packing|packaging|logistics?|delivery|loading|shipping|transport(?:ation)?|containeri[sz]ation)"
                      r"(?:\s*(?:,|and|&)\s*(?:packing|packaging|logistics?|delivery|loading|shipping|transport(?:ation)?))*\s+plans?"
                      r"(?![A-Za-z])|装箱方案|包装方案|物流方案|运输方案|装柜方案", re.I)
_SUBMIT_RE = re.compile(r"submi(?:t|ts|tted|ssion)|provide|furnish|prepare|提交|报送|报审", re.I)
_SITE_ACCESS_RE = re.compile(
    r"site\s+access|access\s+(?:road|route)|(?:delivery|working|site)\s+hours|delivery\s+(?:times?|windows?)|"
    r"between\s+\d{1,2}(?:[.:]\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.|hrs|hours)(?![A-Za-z])|no\s+deliver(?:y|ies)\s+(?:between|on|before|after|during)|"
    r"loading\s+bay|height\s+clearance|headroom|(?:vehicles?|lorr(?:y|ies)|trucks?|trailers?)\s+(?:(?:shall|must|may)\s+)?not\s+exceed|"
    r"not\s+exceeding\s+[\d.]+\s*m(?![A-Za-z])|(?<![A-Za-z])GVW(?![A-Za-z])|gross\s+vehicle\s+weight|axle\s+loads?|road\s+closure|"
    r"traffic\s+management|车辆进场|进场道路|限行|限高|卸货区|送货时间", re.I)
_CRATE_RE = re.compile(_NO_L + r"(?:crat(?:e|es|ed|ing)|timber\s+cases?|wooden\s+cases?|steel\s+frames?|packing\s+cases?)"
                       + _NO_R + r"|木箱|铁架|钢架", re.I)
# what a handling clause asks for, in words a bid statement can use (tender_parse._PACK_UNMODELLED_RE finds them)
_HANDLING_LABELS = ((r"a[\s-]?frame|stillage", "A-frame stillages"), (r"upright|vertical|竖放|立放|直立|竖立", "upright transport"),
                    (r"stack|堆叠|叠放|堆码", "no stacking"), (r"protect|防护", "face protection"), (r"fragile|易碎", "fragile handling"),
                    (r"防潮|防雨", "weather protection"), (r"熏蒸", "fumigation"))
# handling asked for in other words: "standing on edge", "returnable steel racks", "nothing placed on top"
_HANDLING_EXTRA = ((re.compile(r"(?<![A-Za-z])on\s+(?:its\s+|their\s+)?edge(?![A-Za-z])", re.I), "upright transport"),
                   (re.compile(r"(?<![A-Za-z])racks?(?![A-Za-z])", re.I), "steel racks"),
                   (re.compile(r"(?:placed|stacked|loaded)\s+on\s+top", re.I), "no stacking"))
# how a clause is referred to, the way the tender writes it: "4.9", "4.8.1", "Clause 12", "Part C Clause 12.3(b)", "12.3(b)"
_REF_RE = re.compile(r"^\s*(?:(?P<part>(?:Part|Section|Appendix|Annex|Schedule|Volume)\s+(?-i:[A-Z]|\d{1,2}|[IVX]{1,5}))\s*[,:\-–—]?\s*)?"
                     r"(?:(?P<word>clause|cl\.|item|para(?:graph)?|article|art\.)\s*)?"
                     r"(?P<num>\d+(?:\.\d+){0,3})(?P<sub>(?:\s*\((?:[a-z]{1,2}|[ivx]{1,5}|\d{1,2})\))*)(?=[\s.:)\-–—,]|$)", re.I)
# a lettered sub-item: "(a) ...", "b) ...", "(iv) ..."
_LETTER_RE = re.compile(r"^\s*\(?((?:[a-z]{1,2}|[ivx]{1,5}|\d{1,2}))\)\s+(?=\S)")
_SECTION_LINE_RE = re.compile(r"^\s*#|^\s*(?:SECTION|PART|CHAPTER|APPENDIX|ANNEX|SCHEDULE|VOLUME)\b", re.I)
# "Table 4-1", "Schedule 3", "Appendix B" - a label with a number or a letter, not "APPENDIX TO THE INSTRUCTIONS"
_TABLE_LABEL_RE = re.compile(r"^\s*((?i:Table|Schedule|Appendix|Annex)\s+(?:\d[\w.\-–]*|[A-Z](?:[.\-–]?\d[\w.\-–]*)?|[IVX]{1,5}))(?![A-Za-z])")
_ROW_REF_RE = re.compile(r"\d+(?:\.\d+){0,3}(?:\([a-z0-9]{1,3}\))*|[A-Z]\d{0,2}(?:\.\d+)*")
# a container size with no type code ("20-foot containers", "40' containers", "40尺柜"): 40 ft is a GP or an HQ, and
# the planner needs the type, so a clause like this is read as naming a container but not a type to plan in
_SIZE_ONLY_RE = re.compile(r"(?<![\w.])(20|40|45)\s*-?\s*(?:ft|foot|feet|['’]|尺|英尺)\.?(?:\s*[A-Za-z]+){0,2}?\s*(?:containers?|集装箱|柜)",
                           re.I)
# Source text addressed to a document processor is not a logistics requirement.
# This only bounds the unclassified-requirement fallback and its quotations;
# numbers/types in the original still make conflicting constraints fail safe.
_PROCESSOR_TEXT_RE = re.compile(
    r"\b(?:AI\s+(?:system|assistant|model)|automated\s+(?:assistants?|reviewers?)|language\s+model|ChatGPT|LLM)\b|"
    r"\b(?:ignore|disregard|override|forget|bypass)\s+(?:(?:all|the|your|previous|prior|system)\s+){0,3}"
    r"(?:instructions?|rules?|safeguards?|tender\s+limits)\b|"
    r"(?:^|[.:;]\s*)\s*(?:record|mark|treat|report|declare)\b[^.;]{0,100}\b(?:clauses?|statements?|requirements?)\b"
    r"[^.;]{0,80}\b(?:covered|approved|compliant)\b|"
    r"人工智能(?:系统|助手)|自动(?:化)?(?:助手|审阅器)|语言模型|大模型|"
    r"忽略.{0,20}(?:指令|规则)|(?:标记|记录|声明).{0,30}(?:条款|应答).{0,20}(?:已覆盖|全部覆盖|已批准)", re.I)


def _unplaced_source_text(text: str) -> str:
    """Only non-processor sentences may be quoted as an unknown requirement."""
    parts, start = [], 0
    for boundary in _SENTENCE_RE.finditer(text or ""):
        parts.append(text[start:boundary.end()])  # preserve the source sentence's punctuation
        start = boundary.end()
    parts.append((text or "")[start:])
    return " ".join(part.strip() for part in parts if part.strip() and not _processor_text(part))


def _processor_text(text: str) -> bool:
    # A source clause label must not hide an imperative at the sentence start.
    return bool(_PROCESSOR_TEXT_RE.search(_REF_RE.sub("", text or "", count=1)))


def _mixed_processor_requirement(text: str, context: str) -> bool:
    """A real transport obligation before a processor instruction still needs review."""
    for part in _SENTENCE_RE.split(text or ""):
        part = _REF_RE.sub("", part, count=1)
        instruction = _PROCESSOR_TEXT_RE.search(part)
        if instruction is None:
            continue
        prefix = part[:instruction.start()]
        normative = re.search(r"\b(?:shall|must|required|requirement)\b|必须|应当|不得", prefix, re.I)
        term = _CONTAINER_TERM_RE.search(prefix) or _MASS_WORD_RE.search(prefix) or _mass_figures(prefix)
        transport = _TRANSPORT_RE.search(_CONTAINER_TERM_RE.sub(" ", prefix)) or _TRANSPORT_RE.search(context)
        if normative and term and transport:
            return True
    return False


# ---------------------------------------------------------------------------------------------------------
# tender -> logistics clauses

def _explicit_ref(line: str) -> Optional[Tuple[str, str, bool]]:
    """(id, cite, worded) of a clause reference at the start of a line: "4.9" -> ("4.9", "Clause 4.9"),
    "Part C Clause 12" -> ("Part C Clause 12", "Part C Clause 12"), "12.3(b)" -> ("12.3(b)", "Clause 12.3(b)").
    A bare number needs a dot ("4.9"), so a list number or a figure at the start of a line is no clause."""
    found = _REF_RE.match(line)
    if not found:
        return None
    part, word, num = found.group("part"), found.group("word"), found.group("num")
    sub = re.sub(r"\s+", "", found.group("sub") or "")
    if not word and "." not in num:
        return None
    word = {"cl.": "Clause", "art.": "Article"}.get(word.lower(), word.capitalize()) if word else ""
    written = " ".join(p for p in (part, word or ("" if part else "Clause"), num + sub) if p)
    ident = written[len("Clause "):] if written.startswith("Clause ") else written
    return ident, written, bool(part or word or sub)


def _table_ref(label: str, caption: str, row: str, table_line: Optional[int], where: str) -> Tuple[str, str]:
    """A table row, cited by its table and row: "Table 4-1, row 4.9"."""
    named = _TABLE_LABEL_RE.match(label or caption or "")
    if named:
        table = named.group(1).rstrip(".:")
        return f"{table} row {row}", f"{table}, row {row}"
    if caption:
        short = caption if len(caption) <= 60 else caption[:57] + "..."
        return f"table '{short}' row {row}", f"the table under '{short}', row {row}"
    return f"table at line {table_line} row {row}", f"the table at line {table_line} of {where}, row {row}"


class _Lines:
    """Physical line numbers of the source, so "line N" is the line a person opens the file at."""

    def __init__(self, text: str) -> None:
        self.lines = [line.strip() for line in (text or "").splitlines()]
        self.at = 0

    def find(self, snippet: str) -> Optional[int]:
        key = re.sub(r"\s+", " ", snippet.strip())[:40]
        if not key:
            return None
        for start in (self.at, 0):
            for index in range(start, len(self.lines)):
                if key in re.sub(r"\s+", " ", self.lines[index]):
                    self.at = index
                    return index + 1
        return None


def _clause_units(text: str, source: Optional[str] = None) -> List[Dict[str, Any]]:
    """The tender as clauses: (id, cite, locator, text).

    ``clause`` is the short id ("4.9", "4.9(a)", "Table 4-1 row 4.9", "line 3"); ``cite`` is how a statement names
    it, the way the tender does: "Clause 4.9", "Clause 4.9(a)" for a lettered item under 4.9, "Part C Clause 12" as
    written, "Table 4-1, row 4.9" for a table row, and "line 3 of <file>" when the tender gives no number - never an
    invented "Clause L3". A structured document gives its own clause numbers (tools/tender_document.py)."""
    from packing_assistant.tools import tender_document

    where = source or "the tender"
    units: List[Dict[str, Any]] = []
    if tender_document.is_document(text or ""):
        doc = tender_document.read(text or "")
        lines = _Lines(text)
        grouped: Dict[Tuple[str, int], Dict[str, Any]] = {}
        for piece in doc.pieces:
            if piece.kind in ("heading", "header"):
                continue
            key = (piece.ref, piece.line)
            unit = grouped.get(key)
            if unit is None:
                body = piece.text.strip()
                number = str(piece.number or "").strip()
                context = ""
                if piece.kind == "row":
                    cells = [c for c in piece.cells]
                    row = number or (cells[0] if cells and _ROW_REF_RE.fullmatch(cells[0] or "") else "")
                    label = piece.table or piece.heading
                    ident, cite = _table_ref(label, label, row or f"at line {lines.find(body) or piece.line}",
                                             lines.find(body), where)
                    context = " ".join([label or "", *piece.header])
                else:
                    explicit = _explicit_ref(body)
                    letter = _LETTER_RE.match(body)
                    if explicit and (explicit[2] or not number):
                        ident, cite = explicit[0], explicit[1]          # "Part C Clause 12", "12.3(b)": as written
                    elif number:
                        if letter and not number.endswith(")"):
                            number = f"{number}({letter.group(1)})"      # "(a)" under 4.9 is 4.9(a)
                        ident, cite = number, f"Clause {number}"
                    else:
                        line = lines.find(body)
                        if piece.page:
                            ident, cite = f"page {piece.page}" + (f" line {line}" if line else ""), f"page {piece.page} of {where}"
                        else:
                            line = line or piece.line
                            item = f"item ({letter.group(1)}), " if letter else ""
                            ident, cite = (f"({letter.group(1)}) " if letter else "") + f"line {line}", f"{item}line {line} of {where}"
                grouped[key] = unit = {"clause": ident, "cite": cite, "locator": piece.ref, "line": piece.line,
                                       "context": context, "parts": []}
                units.append(unit)
            unit["parts"].append(piece.text.strip())
        for unit in units:
            unit["text"] = " ".join(p for p in unit.pop("parts") if p)
    else:
        parent: Optional[Tuple[str, str]] = None        # the last numbered clause: (id, cite)
        caption = ""
        header: Optional[List[str]] = None
        table_caption, table_line, row_no = "", 0, 0
        for n, raw in enumerate((text or "").splitlines(), 1):
            line = raw.strip()
            if not line:
                header = None
                continue
            if line.startswith("|") and line.endswith("|") and len(line) > 1:
                cells = [c.strip() for c in line.strip("|").split("|")]
                if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                    continue
                if header is None:
                    header, table_caption, table_line, row_no = cells, caption, n, 0
                    continue
                row_no += 1
                row = cells[0] if cells and _ROW_REF_RE.fullmatch(cells[0] or "") else str(row_no)
                ident, cite = _table_ref(table_caption, table_caption, row, table_line, where)
                units.append({"clause": ident, "cite": cite, "locator": f"L{n}", "line": n, "text": line,
                              "context": " ".join([table_caption, *header])})
                continue
            header = None
            explicit = _explicit_ref(line)
            letter = _LETTER_RE.match(line)
            if explicit:
                ident, cite = explicit[0], explicit[1]
                parent = (ident, cite)
            elif letter and parent:
                ident, cite = f"{parent[0]}({letter.group(1)})", f"{parent[1]}({letter.group(1)})"
            elif letter:
                ident, cite = f"({letter.group(1)}) line {n}", f"item ({letter.group(1)}), line {n} of {where}"
            else:
                ident, cite = f"line {n}", f"line {n} of {where}"
                if _SECTION_LINE_RE.match(line):
                    parent = None                            # a new section: "(a)" below it is not under the last clause
            caption = line if len(line) <= 80 else ""
            units.append({"clause": ident, "cite": cite, "locator": f"L{n}", "line": n, "text": line, "context": ""})
    return [u for u in units if u.get("text")]


def _mass_figures(text: str) -> List[Tuple[float, int, int]]:
    """Every mass figure in ``text`` as (kg, start, end)."""
    out = []
    for m in _MASS_RE.finditer(text):
        number = float(re.sub(r"[, \u00a0\u2009\u202f]", "", m.group(1)))
        unit = re.sub(r"\s+", " ", m.group(2).lower())
        tonnes = unit in _TONNES or unit.startswith("metric")
        out.append((number * 1000.0 if tonnes else number, m.start(), m.end()))
    return out


def _mass_values(text: str) -> List[float]:
    return [kg for kg, _, _ in _mass_figures(text)]


def _subject(sentence: str, at: int, end: Optional[int] = None) -> Tuple[Optional[str], str]:
    """What a mass figure at ``at`` limits - "container", "package" or "vehicle" (None: the sentence does not say) -
    and how that was read. A subject named right after the figure ("20 t per container, 2 t per crate") comes first,
    then a frame that names it before the figure ("the gross mass of each loaded container", "per stillage", "each
    delivery truck", "container weight"); failing both, the nearest subject noun before the figure, then after."""
    if end is not None:
        after = _AFTER_FIGURE_RE.match(sentence, end)
        if after:
            found = _SUBJECT_RE.search(after.group(0))
            if found:
                return found.lastgroup, "after"
    before = sentence[:at]
    # A new subject after the previous figure starts another relation: the earlier
    # "mass of each container" must not capture "..., and no crate ... 2 t" or
    # "..., while the site crane capacity is 2 t". Retain the earlier subject only
    # when the following figure supplies none (then multiple limits remain for review).
    previous_figures = _mass_figures(before)
    if previous_figures:
        local = before[previous_figures[-1][2]:]
        if _SUBJECT_RE.search(local):
            before = local
    hits = [(index, hit) for index, frame in enumerate(_SUBJECT_FRAMES) for hit in frame.finditer(before)]
    if hits:
        index, nearest = max(hits, key=lambda item: (item[1].end(), -item[0], item[1].start()))
        subjects = list(_SUBJECT_RE.finditer(nearest.group(0)))
        if subjects:
            if index == 1 and subjects[-1].lastgroup == "container" and any(
                    item.lastgroup != "container" for item in _SUBJECT_RE.finditer(before, nearest.end())):
                # "each container ... crane rated 50 t" does not set the box's
                # mass limit. An explicit "gross mass of ..." frame is stronger.
                return None, "ambiguous"
            return subjects[-1].lastgroup, "frame"
    previous = [m for m in _SUBJECT_RE.finditer(before)]
    if previous:
        return previous[-1].lastgroup, "near"
    following = _SUBJECT_RE.search(sentence, at)
    return (following.lastgroup, "near") if following else (None, "none")


def _basis(sentence: str) -> str:
    if _EXCL_TARE_RE.search(sentence):
        return "cargo"
    if _TARE_RE.search(sentence):
        return "gross"
    if _CARGO_BASIS_RE.search(sentence):
        return "cargo"
    if _GROSS_WORD_RE.search(sentence):
        return "gross"
    return "unstated"


def _upper_bound(sentence: str) -> bool:
    """Only recognise upper bounds; lower or mixed bounds need a person's reading.

    A naked "exceed" / "more than" is not an upper bound. Explicit negation,
    "no single container ... more than", or rejection of overweight deliveries
    provides its direction. This is deliberately conservative for mixed wording.
    """
    if _LOWER_BOUND_RE.search(sentence):
        return False
    # A noun-only inclusion aside does not separate "no single lift" from its
    # upper bound. Do not remove asides containing another numeric relation.
    directional = re.sub(r",\s*(?:including|excluding)\b[^,;:\d]{1,120},", " ", sentence, flags=re.I)
    remaining = _NO_SUBJECT_UPPER_RE.sub(" ", _LIMIT_NOT_RE.sub(" ", directional))
    if not _POSITIVE_COMPARISON_RE.search(remaining):
        return True
    # Do not borrow a "no" or refusal from another numeric relation in the sentence.
    if len(_mass_figures(sentence)) != 1:
        return False
    if _REJECT_OVER_RE.search(sentence):
        return True
    return False


def _mass_limits(body: str, context: str = "") -> Dict[str, Any]:
    """The mass limits a clause sets, sorted by what they limit. A limit on a stillage, a crate, a piece or a crane
    lift is a per-package limit and a truck's is a vehicle limit: neither is ever compared with a container's gross
    mass. A figure in a sentence that reads like a transport limit but names none of these is returned as not placed,
    for a person - it never disappears."""
    out: Dict[str, Any] = {"container": [], "basis": [], "package": [], "package_subjects": [], "vehicle": [], "unplaced": []}
    clause_transport = bool(_TRANSPORT_RE.search(_STRUCTURAL_LOAD_RE.sub(" ", body)) or _CONTAINER_TERM_RE.search(body))
    carried = (False, False, True)
    for sentence in (s for s in _SENTENCE_RE.split(body) if s and s.strip()):
        plain = _STRUCTURAL_LOAD_RE.sub(" ", sentence)
        figures = _mass_figures(plain)
        own = (bool(_LIMIT_RE.search(plain)), bool(_MASS_WORD_RE.search(plain)), _upper_bound(plain))
        limit, massy, upper = own if any(own[:2]) else carried
        # Preserve the direction too: a lower or mixed bound before a semicolon
        # cannot silently become an upper bound on the following bare figure.
        carried = (limit, massy, upper) if sentence.rstrip().endswith((";", "；")) else (False, False, True)
        if not figures:
            continue
        transport = bool(_TRANSPORT_RE.search(plain) or _CONTAINER_TERM_RE.search(plain) or _TRANSPORT_RE.search(context)
                         or clause_transport)
        if limit and transport and not (upper and _upper_bound(plain)):
            out["unplaced"].append(sentence.strip())
            continue
        placed = False
        for kg, start, _end in figures:
            subject, how = _subject(plain, start, _end) if limit and (massy or transport) else (None, "none")
            if subject == "container" and how == "near" and any(m.lastgroup == "vehicle" for m in _SUBJECT_RE.finditer(plain)):
                # "the prime mover and trailer with a loaded 40HQ shall not exceed 40 t": whose limit is it? A vehicle's
                # would pass any container, so the tool does not guess - a person reads it
                subject = None
            if subject == "container":
                out["container"].append(kg)
                out["basis"].append(_basis(plain))
                placed = True
            elif subject == "package":
                out["package"].append(kg)
                words = [m.group(0).lower() for m in _SUBJECT_RE.finditer(plain[:start]) if m.lastgroup == "package"]
                out["package_subjects"].append(words[-1] if words else "package")
                placed = True
            elif subject == "vehicle":
                out["vehicle"].append(kg)
                placed = True
        if not placed and transport and (limit or massy):
            out["unplaced"].append(sentence.strip())
    return out


def logistics_clauses(text: str, source: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every clause of the tender that sets a logistics requirement, with its kinds and what it states.

    ``source``: the tender's file name, so a clause without a number is cited as "line N of <file>".
    Nothing with a mass or container term in a transport or packing context disappears: a clause (or a sentence of
    one) that the reader cannot place in a kind becomes an "unplaced" clause, which build_checks turns into a row
    for a person that quotes it."""
    from packing_assistant.tools.tender_parse import _CONTAINER_RE, _LASHING_RE, _PACK_UNMODELLED_RE, _REFUSE_RE, _container_codes

    def codes_in(part: str) -> set:
        return _container_codes(part) | {f"{m.group(1)}GP" for m in _GP_RE.finditer(part)}

    found: List[Dict[str, Any]] = []
    for unit in _clause_units(text, source):
        body = unit["text"]
        around = unit.get("context") or ""
        context = bool(_CONTEXT_RE.search(body) or _CONTEXT_RE.search(around))
        kinds: List[str] = []
        detail: Dict[str, Any] = {}
        codes = codes_in(body)
        sizes = sorted({f"{m.group(1)} ft" for m in _SIZE_ONLY_RE.finditer(_GP_RE.sub(" ", _CONTAINER_RE.sub(" ", body)))})
        if codes or sizes:
            refused = set()
            # a code in a sentence that also says not / no / excluded: the sentence may refuse it ("20GP containers are
            # not accepted") or not ("40HQ containers, which shall not be stacked"); the tool does not decide which
            for sentence in re.split(r"(?<=[.;。；])\s*", body):
                # the "not" of a mass limit ("the payload of each 40HQ shall not exceed 22 t") refuses no type
                if _REFUSE_RE.search(_LIMIT_NOT_RE.sub(" ", sentence)):
                    refused |= codes_in(sentence)
            kinds.append("container_type")
            detail.update(named=sorted(codes), refused=sorted(refused), allowed=sorted(codes - refused), sizes=sizes)
        limits = _mass_limits(body, around)
        if limits["container"]:
            kinds.append("gross_mass")
            bases = list(dict.fromkeys(limits["basis"]))
            detail.update(limits_kg=sorted(set(limits["container"])), basis=bases[0] if len(bases) == 1 else "unstated")
        if limits["package"]:
            kinds.append("per_package_limit")
            detail.update(package_limits_kg=sorted(set(limits["package"])),
                          package_subjects=list(dict.fromkeys(limits["package_subjects"])))
        securing = sorted({m.group(0) for m in _LASHING_RE.finditer(body)} | {m.group(0) for m in _SECURING_EXTRA_RE.finditer(body)},
                          key=str.lower)
        if securing and context:
            kinds.append("securing")
            detail["securing_terms"] = securing
        words = {m.group(0).lower() for m in _PACK_UNMODELLED_RE.finditer(body)}
        unmodelled = [label for pattern, label in _HANDLING_LABELS if any(re.search(pattern, w, re.I) for w in words)]
        unmodelled += [label for pattern, label in _HANDLING_EXTRA if pattern.search(body) and label not in unmodelled]
        if unmodelled and context:
            kinds.append("handling")
            detail["unmodelled"] = unmodelled
        if _CRATE_RE.search(body) and context:
            kinds.append("crating")
        if _SEQUENCE_RE.search(body) and context:
            kinds.append("delivery_sequence")
        access = sorted({m.group(0) for m in _SITE_ACCESS_RE.finditer(body)}, key=str.lower)
        if access or limits["vehicle"]:
            kinds.append("site_access")
            detail.update(access_terms=access, vehicle_limits_kg=sorted(set(limits["vehicle"])))
        if _PLAN_RE.search(body) and _SUBMIT_RE.search(body):
            kinds.append("logistics_plan_submission")
            detail["plan_asked"] = _PLAN_RE.search(body).group(0)
        # the safety net: a mass or container term in a transport / packing context that no kind took
        requirement = _unplaced_source_text(body)
        plain = _STRUCTURAL_LOAD_RE.sub(" ", requirement)
        term = bool(_CONTAINER_TERM_RE.search(plain) or _mass_figures(plain) or _MASS_WORD_RE.search(plain))
        transport = bool(_TRANSPORT_RE.search(_CONTAINER_TERM_RE.sub(" ", plain)) or _TRANSPORT_RE.search(around))
        mixed_requirement = _mixed_processor_requirement(body, around)
        if limits["unplaced"] or mixed_requirement:
            kinds.append("unplaced")
            if mixed_requirement or any(_processor_text(part) for part in limits["unplaced"]):
                # Keep the unresolved numeric constraint as a stop, without
                # promoting an instruction from the source into our statement.
                detail.update(unplaced_text="", unplaced_instruction=True)
            else:
                detail["unplaced_text"] = " ... ".join(limits["unplaced"])
        elif not kinds and term and transport:
            kinds.append("unplaced")
            detail["unplaced_text"] = requirement
        if kinds:
            found.append({**unit, "kinds": kinds, **detail, "sha256": _sha(body.encode("utf-8"))})
    return found


_CHOOSE = "a person chooses the type and names it in the request (e.g. 'in 40HQ')"


def _ref(clause: Dict[str, Any]) -> str:
    """How a statement names the clause: the tender's own reference ("Clause 4.9", "Clause 4.9(a)", "Part C Clause 12",
    "Table 4-1, row 4.9", "line 3 of itt.md")."""
    return clause.get("cite") or f"Clause {clause['clause']}"


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _refs(clauses: Sequence[Dict[str, Any]]) -> str:
    cites = [_ref(c) for c in clauses]
    if cites and all(c.startswith("Clause ") for c in cites):
        return "Clause " + ", ".join(c[len("Clause "):] for c in cites)
    return "; ".join(cites)


def _ref_of(clauses: Sequence[Dict[str, Any]], ident: Optional[str]) -> str:
    return next((_ref(c) for c in clauses if c["clause"] == ident), f"Clause {ident}")


_NOUN = {"crane": "lift", "cranes": "lift", "lifting": "lift", "lifts": "lift", "hoist": "lift", "hoists": "lift", "swl": "lift",
         "塔吊": "lift", "起重": "lift", "吊": "lift", "木箱": "crate", "托盘": "pallet", "单件": "piece", "构件": "piece", "板块": "panel"}


def _noun(word: str) -> str:
    word = word.lower()
    if word in _NOUN:
        return _NOUN[word]
    return word[:-1] if word.endswith("s") and not word.endswith("ss") else word


def _quote(text: str, limit: int = 300) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip().replace('"', "'")
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."


def container_decision(clauses: Sequence[Dict[str, Any]], known: Optional[Sequence[str]] = None,
                       requested: Optional[str] = None) -> Dict[str, Any]:
    """Which container type the plan is made for, and why: the type the request names, else the tender's clause,
    else 40HQ by default - said so.

    A type the planner cannot model, several types, a size with no type ("40 ft containers") or a code in a sentence
    that also says not / no: no plan, a person decides - and says the type in the request. The planner's own list of
    types is the only list (pack_ship_solve.known_container_types)."""
    if known is None:
        from packing_assistant.tools.pack_ship_solve import known_container_types

        known = known_container_types()
    if requested:
        code = str(requested).strip().upper()
        tender = container_decision(clauses, known)
        if code not in known:
            return {"type": None, "source": "request", "clause": None, "named": code, "tender": tender,
                    "reason": f"The request names {code}; the planner can only model {', '.join(known)}. No plan was made."}
        same = tender.get("type") == code
        return {"type": code, "source": "request", "clause": tender.get("clause") if same else None, "tender": tender,
                "reason": f"Container type {code} named in the request"
                          + (f"; {_ref_of(clauses, tender['clause'])} names the same type." if same and tender.get("clause")
                             else f" (the ITT: {tender['reason']})")}
    naming = [c for c in clauses if "container_type" in c["kinds"]]
    if not naming:
        return {"type": DEFAULT_CONTAINER, "source": "default", "clause": None,
                "reason": f"No container type (20GP / 40GP / 40HQ / 45HQ / OT / FR) was found in the ITT; the plan uses "
                          f"{DEFAULT_CONTAINER}, the planner's default."}
    allowed = sorted({code for c in naming for code in c.get("allowed") or []})
    sizes = sorted({size for c in naming for size in c.get("sizes") or []})
    refs = ", ".join(c["clause"] for c in naming)
    said = _cap(_refs(naming))
    if len(allowed) == 1 and not sizes:
        code = allowed[0]
        chosen = next(c for c in naming if code in (c.get("allowed") or []))
        clause = chosen["clause"]
        if code not in known:
            return {"type": None, "source": "tender_clause", "clause": clause, "named": code,
                    "reason": f"{_cap(_ref(chosen))} names {code}; the planner can only model {', '.join(known)}. No plan was made: "
                              "a person plans this container type."}
        return {"type": code, "source": "tender_clause", "clause": clause,
                "reason": f"Container type {code} taken from {_ref(chosen)}."}
    if not allowed and sizes:
        refused = sorted({code for c in naming for code in c.get("refused") or []})
        return {"type": None, "source": "tender_clause", "clause": refs, "named": ", ".join(sizes + refused),
                "reason": f"{said} names {' / '.join(sizes)} containers without the type (GP or HQ) the planner needs. "
                          f"No plan was made: {_CHOOSE}."}
    if not allowed:
        refused = sorted({code for c in naming for code in c.get("refused") or []})
        return {"type": None, "source": "tender_clause", "clause": refs, "named": ", ".join(refused),
                "reason": f"{said} names {', '.join(refused)} in a sentence that also says not / no / excluded; the tool "
                          f"does not decide whether that allows or excludes it. No plan was made: {_CHOOSE}."}
    options = allowed + [s for s in sizes]
    return {"type": None, "source": "tender_clause", "clause": refs, "named": ", ".join(options),
            "reason": f"{said} names {', '.join(options)}; which one to plan for is a person's choice. No plan was made: "
                      f"{_CHOOSE}."}


# ---------------------------------------------------------------------------------------------------------
# plan figures

def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def plan_sha256(plan: Optional[Dict[str, Any]]) -> Optional[str]:
    if not plan:
        return None
    stable = {k: v for k, v in plan.items() if k not in ("elapsed_s",)}
    if isinstance(stable.get("parse"), dict):
        # how the sheet was read is a function of the panel list's bytes (hashed in inputs.panel_list) and the reader;
        # kept out so the plan hash names the plan, and a plan made before the reading record keeps its hash
        stable["parse"] = {k: v for k, v in stable["parse"].items() if k != "reading"}
    return _sha(json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"))


def container_tare_kg(container_type: str) -> Optional[float]:
    """The container's tare from the in-repo knowledge base (approximate; the CSC plate on the box governs)."""
    from packing_assistant.knowledge import load_kb

    spec = (load_kb().get("containers") or {}).get(str(container_type or "").upper()) or {}
    if spec.get("tare_kg") is not None:
        return float(spec["tare_kg"])
    if spec.get("tare_ton_approx") is not None:
        return round(float(spec["tare_ton_approx"]) * 1000.0, 1)
    return None


def heaviest_container(per_container: Sequence[Dict[str, Any]], container_type: str) -> Dict[str, Any]:
    """The heaviest loaded container of a plan: its number, the cargo in it (panels and crates, from the engine's
    per-container load), the container's tare and their sum, the gross mass. One computation for the link's mass
    statement, the model's pack_plan result and the reply check, so the three cannot disagree. The rated payload
    of the container type is a different figure and is not here."""
    heaviest = max(per_container, key=lambda item: float(item.get("cargo_kg") or 0))
    cargo = float(heaviest.get("cargo_kg") or 0)
    tare = container_tare_kg(container_type)
    return {"heaviest_container_no": heaviest.get("container_no"), "max_cargo_kg": cargo, "container_tare_kg": tare,
            "max_gross_kg": round(cargo + tare, 1) if tare is not None else None, "containers": len(per_container)}


def _kg(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{number:,.1f}".rstrip("0").rstrip(".") if number != int(number) else f"{int(number):,}"


def _rows_per_container(plan: Dict[str, Any], limit: int = 12) -> str:
    parts = []
    per = plan.get("per_container") or []
    for item in per[:limit]:
        rows = ", ".join(f"{row} x {n}" for row, n in (item.get("rows") or {}).items())
        parts.append(f"{item.get('container_no')}: {rows or '(no rows)'}")
    more = f"; (+{len(per) - limit} more in {PLAN_FILE})" if len(per) > limit else ""
    return "; ".join(parts) + more


# ---------------------------------------------------------------------------------------------------------
# the checks: one matrix row and one statement per (clause, kind)

def _check(kind: str, clause: Optional[Dict[str, Any]], status: str, text: str, figures: Dict[str, Any],
           note: str, placeholder: bool) -> Dict[str, Any]:
    ref = clause["clause"] if clause else None
    return {"key": f"{kind}@{ref or 'none'}", "kind": kind, "clause": ref, "cite": _ref(clause) if clause else None,
            "locator": clause["locator"] if clause else None,
            "clause_text": clause["text"] if clause else None, "clause_sha256": clause["sha256"] if clause else None,
            "status": status, "text": text, "figures": figures, "note": note, "placeholder": placeholder,
            "owner": KIND_OWNER[kind]}


def _placeholder(owner: str, body: str) -> str:
    return f"[TO CONFIRM by {owner}: {body}]"


def build_checks(clauses: Sequence[Dict[str, Any]], decision: Dict[str, Any], plan: Optional[Dict[str, Any]],
                 panel_list: str) -> List[Dict[str, Any]]:
    """The rows of the logistics response. Never "covered" without a plan figure that shows it."""
    checks: List[Dict[str, Any]] = []
    solved = bool(plan and plan.get("ok") and plan.get("source") == "solver")
    # a plan that does not fit (can_fit is not true) leaves crates unplaced: it evidences no type, count or mass
    fits = solved and plan.get("can_fit") is True
    by_kind = {kind: [c for c in clauses if kind in c["kinds"]] for kind in ("container_type", "gross_mass", "per_package_limit",
                                                                              "securing", "handling", "crating", "delivery_sequence",
                                                                              "site_access", "logistics_plan_submission", "unplaced")}
    handling = by_kind["handling"]
    stillage_words = list(dict.fromkeys(w for c in handling for w in c.get("unmodelled") or []))
    unmodelled_packing = bool(handling)
    if decision.get("type") is None:
        no_plan_why = decision.get("reason")
    else:
        from packing_assistant.tools.pack_ship_solve import cell_text

        rows = [f"{cell_text(r.get('id') or r.get('name'))} ({r.get('reason')})" + (f" in row {r['sheet_row']}" if r.get("sheet_row") else "")
                for r in (plan or {}).get("needs_human") or [] if isinstance(r, dict)]
        no_plan_why = (f"no plan ({(plan or {}).get('error') or 'not run'}): {(plan or {}).get('detail') or ''}".strip(": ")
                       + (f"; panel-list rows a person must fix first: {', '.join(rows[:6])}" + (" ..." if len(rows) > 6 else "")
                          if rows else ""))
    ctype = (plan or {}).get("container_type") if solved else decision.get("type")
    cons = (plan or {}).get("conservation") or {}
    per = (plan or {}).get("per_container") or []
    placed = sum(int(item.get("boxes") or 0) for item in per)
    no_fit_why = (f"the loading plan does not fit: {len(per)} x {ctype} containers hold {placed} of the {plan.get('n_boxes')} "
                  f"crates (the planner's lower bound is {plan.get('n0')}, binding constraint {plan.get('binding_constraint')})"
                  if solved and not fits else "")
    if decision.get("source") == "request" and decision.get("type"):
        type_source = "the request"
    elif decision.get("source") == "tender_clause" and decision.get("type"):
        type_source = _ref_of(clauses, decision["clause"])
    else:
        type_source = None

    # container type: one row per clause that names one; a default is its own row
    deciding = next((c for c in by_kind["container_type"] if c["clause"] == decision.get("clause")), None)
    for clause in by_kind["container_type"] or [None]:
        named = ", ".join((clause.get("named") or []) + (clause.get("sizes") or [])) if clause else ""
        if clause is None:
            figures = {"container_type": ctype, "source": decision.get("source")}
            if solved:
                why = (f"The ITT names no container type. The loading plan uses {ctype}, "
                       + ("the type named in the request. " if decision.get("source") == "request" else "the planner's default. "))
                checks.append(_check("container_type", None, "human_required",
                                     why + _placeholder("logistics", "container type to be offered"
                                                        + (f"; {no_fit_why}" if no_fit_why else "")),
                                     figures, decision["reason"], True))
            else:
                checks.append(_check("container_type", None, "human_required",
                                     _placeholder("logistics", f"container type; {no_plan_why}"), figures, no_plan_why, True))
            continue
        figures = {"clause_names": named, "plan_container_type": ctype if solved else None, "type_source": type_source,
                   "can_fit": plan.get("can_fit") if solved else None}
        if solved and not fits:
            checks.append(_check("container_type", clause, "gap",
                                 _placeholder("logistics", f"container type ({_ref(clause)} names {named}): {no_fit_why}; "
                                                           "re-plan before the type is offered"),
                                 figures, no_fit_why, True))
        elif solved and ctype in (clause.get("allowed") or []) and decision.get("type") == ctype:
            checks.append(_check("container_type", clause, "covered",
                                 f"{_cap(_ref(clause))}: the panels will be shipped in {ctype} containers. The loading plan "
                                 f"was computed for {ctype}, the type taken from {type_source}"
                                 + ("." if type_source == _ref(clause) else f", which {_ref(clause)} also names."),
                                 figures, "plan container_type equals the type the clause names", False))
        elif solved:
            why = (f"{_cap(_ref(clause))} names {named}; the plan was made in {ctype}"
                   + (" (the engine changed the requested type)" if decision.get("type") and ctype != decision.get("type") else ""))
            checks.append(_check("container_type", clause, "human_required", _placeholder("logistics", why + "."), figures, why, True))
        else:
            checks.append(_check("container_type", clause, "human_required",
                                 _placeholder("logistics", f"container type ({_ref(clause)} names {named}). {no_plan_why}"),
                                 figures, no_plan_why, True))

    # containers used: the count, tied to the clause the type came from
    count_clause = deciding
    if solved:
        n, n0 = plan.get("containers_used"), plan.get("n0")
        types = sorted({t for item in plan.get("per_container") or [] for t in (item.get("box_types") or {})})
        figures = {"containers_used": n, "container_type": ctype, "n0": n0, "pieces": cons.get("pieces_in"),
                   "cargo_net_kg": cons.get("kg_in"), "panel_list": panel_list, "crates": plan.get("n_boxes"),
                   "can_fit": plan.get("can_fit")}
        where = f"{_cap(_ref(count_clause))}: t" if count_clause else "T"
        if not fits:
            # crates are left out of the containers: no count is stated, only why
            text = _placeholder("logistics", f"number of containers for the {cons.get('pieces_in')} items "
                                             f"({_kg(cons.get('kg_in'))} kg net) of panel list {panel_list}: {no_fit_why}; "
                                             "re-plan before any count is stated")
            checks.append(_check("containers_used", count_clause, "gap", text, figures, "the plan does not fit", True))
        else:
            text = (f"{where}he loading plan places the {cons.get('pieces_in')} items ({_kg(cons.get('kg_in'))} kg net) of panel list "
                    f"{panel_list} in {n} x {ctype} containers (the planner's lower bound is {n0}); every piece and kilogram on the list is "
                    f"in a crate (conservation check {cons.get('pieces_in')} -> {cons.get('pieces_out')} pieces).")
            status, note, placeholder = "covered", "count from the loading plan", False
            if decision.get("type") and ctype != decision["type"]:
                status, note = "human_required", f"plan made in {ctype}, not the {decision['type']} asked for"
                text += " " + _placeholder("logistics", note)
                placeholder = True
            else:
                if unmodelled_packing:
                    refs = _refs(handling)
                    text += (f" The count rests on the planner's own crate model ({plan.get('n_boxes')} crates, {len(types)} crate "
                             f"type{'s' if len(types) != 1 else ''}, listed in {PLAN_FILE}); "
                             + _placeholder("logistics", f"re-confirm the count once the packaging of {refs} "
                                                         f"({', '.join(stillage_words)}) is sized - it is not modelled"))
                    status, note = "partial", f"crate model, not the packaging {refs} asks for"
                if not count_clause:
                    # the type came from the request or the default, not from a clause the count could satisfy
                    status, note = "human_required", "no clause names the planned container type; the count answers no clause"
                    text += " " + _placeholder("logistics", f"the ITT does not name {ctype}; confirm the container type before "
                                                            "this count is stated")
                    placeholder = True
            checks.append(_check("containers_used", count_clause, status, text, figures, note, placeholder))
    else:
        checks.append(_check("containers_used", count_clause, "human_required",
                             _placeholder("logistics", f"number of containers. {no_plan_why}"), {"containers_used": None}, no_plan_why, True))

    # gross mass per loaded container, against each limit
    for clause in by_kind["gross_mass"]:
        limits = clause.get("limits_kg") or []
        basis = clause.get("basis")
        if not fits or not per:
            why = no_plan_why if not solved else no_fit_why or "the plan has no per-container figures"
            checks.append(_check("gross_mass", clause, "human_required",
                                 _placeholder("logistics", f"gross mass per container ({_ref(clause)}). {why}"),
                                 {"limit_kg": limits[0] if len(limits) == 1 else limits}, why, True))
            continue
        mass = heaviest_container(per, ctype)
        heaviest = {"container_no": mass["heaviest_container_no"]}
        cargo, tare, gross = mass["max_cargo_kg"], mass["container_tare_kg"], mass["max_gross_kg"]
        figures = {"limit_kg": limits[0] if len(limits) == 1 else limits, "limit_basis": basis, **mass}
        if len(limits) != 1 or tare is None:
            why = ("several mass figures in one clause: " + ", ".join(_kg(x) for x in limits)) if len(limits) != 1 else f"no tare for {ctype}"
            checks.append(_check("gross_mass", clause, "human_required",
                                 _placeholder("logistics", f"gross mass per container ({_ref(clause)}): {why}"), figures, why, True))
            continue
        limit = limits[0]
        compared = cargo if basis == "cargo" else gross
        label = "cargo mass (panels and crates)" if basis == "cargo" else "gross mass"
        head = (f"{_cap(_ref(clause))} limits the {'cargo' if basis == 'cargo' else 'gross'} mass of each loaded container to "
                f"{_kg(limit)} kg. The heaviest planned container (no. {heaviest.get('container_no')} of {len(per)}) carries "
                f"{_kg(cargo)} kg of panels and crates; with the {ctype} tare of {_kg(tare)} kg (knowledge base, approximate - the "
                f"container's CSC plate governs) its gross mass is {_kg(gross)} kg.")
        if compared > limit:
            if basis == "unstated" and cargo <= limit:
                why = "the clause does not say whether the limit includes the tare: cargo is within it, gross is not"
                checks.append(_check("gross_mass", clause, "human_required", head + " " + _placeholder("logistics", why), figures, why, True))
            else:
                over = round(compared - limit, 1)
                checks.append(_check("gross_mass", clause, "gap",
                                     head + f" The {label} exceeds the limit by {_kg(over)} kg: the plan does not meet "
                                     f"{_ref(clause)}. " + _placeholder("logistics", "re-plan before this statement can be made"),
                                     figures, f"over the limit by {_kg(over)} kg", True))
            continue
        margin = round(limit - compared, 1)
        figures["margin_kg"] = margin
        text = head + f" That is {_kg(margin)} kg under the limit" + (" (compared as gross, the stricter reading, because the clause does not say)" if basis == "unstated" else "") + "."
        if unmodelled_packing:
            refs = _refs(handling)
            text += " " + _placeholder("logistics", f"the mass of the packaging of {refs} and of dunnage is not included; confirm it fits "
                                                    f"within the {_kg(margin)} kg margin before the VGM is signed")
            checks.append(_check("gross_mass", clause, "partial", text, figures,
                                 f"within the limit by the plan's figures; packaging mass of {refs} not modelled", False))
        else:
            text += " Dunnage and lashing material are not included; the shipper's signed VGM governs."
            checks.append(_check("gross_mass", clause, "covered", text, figures, "per-container figure within the limit", False))

    if not by_kind["gross_mass"]:
        # no container mass limit read: said so, never silence (a limit worded otherwise is also in an unplaced row)
        figures: Dict[str, Any] = {"limit_kg": None}
        known_gross = ""
        if fits and per:
            heaviest = max(per, key=lambda item: float(item.get("cargo_kg") or 0))
            tare = container_tare_kg(ctype)
            if tare is not None:
                figures.update(max_cargo_kg=float(heaviest.get("cargo_kg") or 0),
                               max_gross_kg=round(float(heaviest.get("cargo_kg") or 0) + tare, 1))
                known_gross = f" (the plan's heaviest container: {_kg(figures['max_gross_kg'])} kg gross, tare approximate)"
        others = [c for c in by_kind["per_package_limit"] + by_kind["unplaced"]]
        checks.append(_check("gross_mass", None, "human_required",
                             _placeholder("logistics", "no gross-mass limit per loaded container was recognised in the ITT" + known_gross
                                          + ". A person checks the tender for a container mass limit (VGM, payload, maximum gross "
                                            "weight, a crane or road limit) before any container mass is stated"
                                          + (f"; see also {_refs(others)}" if others else "")),
                             figures, "no gross-mass clause recognised", True))

    # a limit per stillage / crate / piece / lift: never a container limit, never compared with a container's gross mass
    for clause in by_kind["per_package_limit"]:
        limits = clause.get("package_limits_kg") or []
        subject = " / ".join(dict.fromkeys(_noun(w) for w in clause.get("package_subjects") or [])) or "package"
        figures = {"limit_kg": limits[0] if len(limits) == 1 else limits, "limit_per": subject,
                   "crates": plan.get("n_boxes") if solved else None}
        checks.append(_check("per_package_limit", clause, "human_required",
                             _placeholder("the packing designer",
                                          f"{_ref(clause)} limits each {subject} to {' / '.join(_kg(x) + ' kg' for x in limits)}. "
                                          f"That is a limit per {subject}, not per container: it is not compared with any "
                                          "container's gross mass. The planner reports no mass per crate, stillage or lift, so a "
                                          "person checks each one against it"),
                             figures, "per-package limit; not modelled", True))

    # not modelled: securing, handling, delivery sequence
    for clause in by_kind["securing"]:
        terms = ", ".join(clause.get("securing_terms") or [])
        figures = {"mid50": (plan or {}).get("mid50") if solved else None}
        checks.append(_check("securing", clause, "human_required",
                             _placeholder("a competent person", f"cargo securing / lashing to {_ref(clause)}"
                                                                 + (f" (the clause's terms: {terms})" if terms else "") + ". "
                                                                 "Not modelled by the planner: the lashing plan is designed and signed "
                                                                 "separately (the plan gives the centre of gravity only)"),
                             figures, "not modelled", True))
    if not by_kind["securing"]:
        checks.append(_check("securing", None, "human_required",
                             _placeholder("a competent person", "no cargo securing / lashing clause was recognised in the ITT. Every "
                                                                 "container still needs a lashing plan (CTU Code), designed and signed "
                                                                 "separately; the planner does not model securing"),
                             {"mid50": (plan or {}).get("mid50") if solved else None}, "no securing clause recognised", True))
    for clause in handling:
        words = ", ".join(clause.get("unmodelled") or [])
        stillage = "A-frame stillages" in (clause.get("unmodelled") or [])
        checks.append(_check("handling", clause, "human_required",
                             _placeholder("logistics", f"{words} ({_ref(clause)}). Not modelled: "
                                          + ("the planner has no A-frame stillage size, tare or capacity, and " if stillage else "the planner ")
                                          + "does not model upright, no-stacking or protection rules"),
                             {"asks_for": words}, "not modelled", True))
    for clause in by_kind["delivery_sequence"]:
        figures = {"rows_per_container": _rows_per_container(plan) if solved else None}
        checks.append(_check("delivery_sequence", clause, "human_required",
                             _placeholder("the project manager", f"delivery sequence ({_ref(clause)}). Not modelled: the "
                                                                   "plan loads by packing, not by the installation programme"
                                                                   + (f" (plan: {figures['rows_per_container']})" if solved else "")),
                             figures, "not modelled", True))

    for clause in by_kind["site_access"]:
        terms = ", ".join(clause.get("access_terms") or [])
        vehicle = clause.get("vehicle_limits_kg") or []
        said = "; ".join(p for p in (terms, ("vehicle mass " + " / ".join(_kg(x) + " kg" for x in vehicle)) if vehicle else "") if p)
        checks.append(_check("site_access", clause, "human_required",
                             _placeholder("the project manager", f"site access and delivery constraints of {_ref(clause)} ({said}). "
                                          "Not modelled: the plan does not check vehicle size or weight, delivery hours or "
                                          "loading-bay clearance"
                                          + ("; a vehicle's gross weight includes the vehicle, so it is never compared with a "
                                             "container's gross mass" if vehicle else "")),
                             {"access_terms": terms or None, "vehicle_limit_kg": (vehicle[0] if len(vehicle) == 1 else vehicle) or None},
                             "not modelled", True))
    for clause in by_kind["logistics_plan_submission"]:
        asked = clause.get("plan_asked") or "packing / logistics plan"
        if fits:
            sha = plan_sha256(plan) or ""
            figures = {"plan_file": PLAN_FILE, "plan_sha256": sha, "containers_used": plan.get("containers_used"),
                       "container_type": ctype, "panel_list": panel_list}
            checks.append(_check("logistics_plan_submission", clause, "partial",
                                 f"{_cap(_ref(clause))} asks for a {asked}. A draft loading plan made from panel list {panel_list} "
                                 f"is attached as {PLAN_FILE} (sha256 {sha[:12]}): {plan.get('containers_used')} x {ctype}. "
                                 + _placeholder("logistics", "the draft is internal and not confirmed; a person confirms the plan, "
                                                             "its format and the submission date the clause sets before it is sent"),
                                 figures, "draft plan attached; a person confirms it and the date", False))
        else:
            why = no_plan_why if not solved else no_fit_why
            checks.append(_check("logistics_plan_submission", clause, "human_required",
                                 _placeholder("logistics", f"the {asked} {_ref(clause)} asks for: there is no loading plan to "
                                                           f"attach. {why}"),
                                 {"plan_file": None}, why, True))
    # the safety net: what the reader found but could not place goes to a person, quoted - it never disappears
    for clause in by_kind["unplaced"]:
        if clause.get("unplaced_instruction"):
            checks.append(_check("unplaced", clause, "human_required",
                                 _placeholder("logistics", f"unresolved mass/transport requirement at {_ref(clause)}. "
                                              "The same source also contains processor-directed wording; it is not a "
                                              "logistics instruction and is not reproduced as a bid statement. A person "
                                              "must read the original and identify the applicable constraint"),
                                 {"quoted": None}, "unresolved constraint; processor-directed source wording withheld", True))
            continue
        quote = _quote(clause.get("unplaced_text") or clause["text"])
        checks.append(_check("unplaced", clause, "human_required",
                             _placeholder("logistics", f"limit/requirement not placed \u2014 {_ref(clause)}: \"{quote}\". The "
                                                       "tender states a mass or container term in a transport or packing context "
                                                       "here that the tool could not place; a person reads it and says whether it "
                                                       "limits the plan"),
                             {"quoted": quote}, "not placed by the reader", True))

    # crate structure: the plan's own verdict per crate; a pending design is a person's job, never a pass
    # (always one row, so a re-run without a plan names this statement as changed, not withdrawn)
    crate_clause = (by_kind["crating"] or handling or [None])[0]
    if not solved:
        checks.append(_check("crate_structure", crate_clause, "human_required",
                             _placeholder("the packing designer", f"crate structure. {no_plan_why}"),
                             {"n_boxes": None}, no_plan_why, True))
    else:
        st = plan.get("structure") or {}
        figures = {k: st.get(k) for k in ("n_boxes", "pass", "needs_reinforcement", "fail", "pending_design")}
        if st.get("n_boxes") and st.get("pass") == st.get("n_boxes") and unmodelled_packing:
            # the check is on the planner's own crates; the tender asks for packaging the planner does not model
            refs = _refs(handling)
            checks.append(_check("crate_structure", crate_clause, "partial",
                                 f"All {st.get('n_boxes')} crates of the planner's own crate model pass its structural check. "
                                 + _placeholder("the packing designer", f"the packaging {refs} asks for "
                                                                        f"({', '.join(stillage_words)}) is not modelled or checked"),
                                 figures, f"planner's crates pass; packaging of {refs} not modelled", False))
        elif st.get("n_boxes") and st.get("pass") == st.get("n_boxes"):
            checks.append(_check("crate_structure", crate_clause, "covered",
                                 f"All {st.get('n_boxes')} crates pass the planner's structural check.", figures, "structure check", False))
        else:
            checks.append(_check("crate_structure", crate_clause, "human_required",
                                 _placeholder("the packing designer", f"crate structure - of {st.get('n_boxes')} crates, "
                                              f"{st.get('pending_design')} are pending detailed design, "
                                              f"{st.get('needs_reinforcement')} need reinforcement, {st.get('fail')} fail"),
                                 figures, "detailed design", True))
    order = {kind: i for i, kind in enumerate(KINDS)}
    checks.sort(key=lambda c: order[c["kind"]])
    for i, check in enumerate(checks, 1):
        check["id"] = f"S{i}"
        check["sha256"] = _sha(json.dumps([check["text"], check["figures"], check["clause_sha256"]], ensure_ascii=False,
                                          sort_keys=True, default=str).encode("utf-8"))
    return checks


# ---------------------------------------------------------------------------------------------------------
# stay linked: compare with the previous record

def compare(previous: Optional[Dict[str, Any]], current: Dict[str, Any],
            earlier_exports: Sequence[str] = ()) -> Optional[Dict[str, Any]]:
    """What moved since the previous record, statement by statement. None when there is no previous record.

    ``earlier_exports``: Word copies an earlier run left in the folder. A Word export never overwrites (a person may
    have edited it), so the old statements live on in them: they are named, not deleted."""
    if previous is None:
        return None
    if not isinstance(previous, dict) or previous.get("schema") != SCHEMA:
        ids = [s["id"] for s in current.get("statements") or []]
        return {"previous_generated_at": None, "inputs_changed": [{"input": "record", "label": "previous link record unreadable"}],
                "changed": [], "unchanged": [], "withdrawn": [], "new": [{"id": i, "key": s["key"]} for i, s in
                                                                       zip(ids, current.get("statements") or [])],
                "needs_reconfirmation": ids,
                "summary": "the previous link record could not be read: every statement (" + ", ".join(ids) + ") needs confirmation"}
    inputs = []
    for key, label in (("tender", "tender"), ("panel_list", "panel list"), ("plan", "plan")):
        old, new = (previous.get("inputs") or {}).get(key) or {}, (current.get("inputs") or {}).get(key) or {}
        if old.get("sha256") != new.get("sha256"):
            names = f" ({old.get('name')} -> {new.get('name')})" if old.get("name") != new.get("name") else ""
            inputs.append({"input": key, "label": label + names, "old_sha256": old.get("sha256"), "new_sha256": new.get("sha256")})
    old_by = {s["key"]: s for s in previous.get("statements") or []}
    new_by = {s["key"]: s for s in current.get("statements") or []}
    changed, unchanged, withdrawn, added = [], [], [], []
    moved: Dict[str, Tuple[Any, Any]] = {}
    for key, new in new_by.items():
        old = old_by.get(key)
        if old is None:
            added.append({"id": new["id"], "key": key})
            continue
        if old.get("sha256") == new.get("sha256"):
            unchanged.append({"id": new["id"], "key": key, "previous_id": old.get("id")})
            continue
        diffs = {k: [old.get("figures", {}).get(k), v] for k, v in (new.get("figures") or {}).items()
                 if old.get("figures", {}).get(k) != v}
        for k, pair in diffs.items():
            moved.setdefault(k, tuple(pair))
        changed.append({"id": new["id"], "key": key, "previous_id": old.get("id"), "figures": diffs,
                        "clause_changed": old.get("clause_sha256") != new.get("clause_sha256"),
                        "status": [old.get("status"), new.get("status")]})
    for key, old in old_by.items():
        if key not in new_by:
            withdrawn.append({"id": old.get("id"), "key": key, "text": old.get("text"),
                              "label": f"earlier {old.get('id')} ({KIND_TITLE.get(old.get('kind'), old.get('kind'))}, "
                                       + (f"{old.get('cite') or 'Clause ' + str(old.get('clause'))})" if old.get("clause")
                                          else "no clause)")})
    headline_keys = ("containers_used", "max_cargo_kg", "max_gross_kg", "pieces", "cargo_net_kg", "container_type", "limit_kg")
    figure_line = "; ".join(f"{k.replace('_', ' ')} {_kg(a)} -> {_kg(b)}" for k, (a, b) in moved.items() if k in headline_keys)
    recheck = [c["id"] for c in changed] + [a["id"] for a in added]
    # statement ids are renumbered per run: where one moved, say which earlier statement it answers
    named = [c["id"] + (f" (was {c['previous_id']})" if c.get("previous_id") and c["previous_id"] != c["id"] else "")
             for c in changed] + [f"{a['id']} (new)" for a in added]
    if not inputs:
        summary = f"no input changed since the previous run; {len(unchanged)} statements re-derived with the same figures"
    else:
        summary = ", ".join(i["label"] + " changed" for i in inputs)
        if figure_line:
            summary += f": {figure_line}"
        summary += ("; statements " + ", ".join(named) + " need re-confirmation") if named else "; no statement changed"
        if withdrawn:
            summary += "; withdrawn (remove from the bid): " + ", ".join(w["label"] for w in withdrawn)
        if earlier_exports:
            summary += "; earlier Word copies " + ", ".join(earlier_exports) + " still hold the previous statements - do not send them"
    return {"previous_generated_at": previous.get("generated_at"), "inputs_changed": inputs, "changed": changed,
            "unchanged": unchanged, "withdrawn": withdrawn, "new": added, "needs_reconfirmation": recheck,
            "stale_exports": list(earlier_exports) if inputs else [], "summary": summary}


# ---------------------------------------------------------------------------------------------------------
# matrix rows for the bid-book, and the English section

_STATUS_DEV = {"covered": "No Deviation", "partial": "Partial Deviation", "gap": "Negative Deviation",
               "human_required": "Pending SME", "pending": "To confirm"}


def _requirement_ref(check: Dict[str, Any]) -> str:
    if not check["clause"]:
        return "(no clause)"
    cite = check.get("cite")
    return f"ITT {check['clause']}" if not cite or cite == f"Clause {check['clause']}" else f"ITT {cite}"


def matrix_rows(checks: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for c in checks:
        rows.append({"req_id": c["key"], "title": f"{KIND_TITLE[c['kind']]} ({c['id']})", "category": "transport",
                     "item_kind": "logistics_clause", "requirement_ref": _requirement_ref(c),
                     "owner": c["owner"], "risk": "high", "requirement_type": "mandatory", "status": c["status"],
                     "proposal_location": f"§6 Logistics {c['id']}", "compliance_label": _STATUS_DEV.get(c["status"], "To confirm"),
                     "evidence": {"type": "packing_link", "figures": c["figures"], "note": c["note"]},
                     "snippets": [c["clause_text"]] if c["clause_text"] else [], "exact_text": c["clause_text"]})
    return rows


def _cell(value: Any) -> str:
    return str(value if value is not None else "—").replace("|", "/").replace("\n", " ")


# How a plan figure reads in the bid-book and the report: words, not the record's keys (the keys stay in the JSON).
_FIGURE_LABEL = {
    "clause_names": "clause names {}", "plan_container_type": "plan made in {}", "type_source": "type from {}",
    "source": "type source: {}", "containers_used": "{} containers used", "container_type": "type {}",
    "n0": "lower bound {}", "pieces": "{} pieces", "cargo_net_kg": "{} kg net", "panel_list": "panel list {}",
    "crates": "{} crates", "limit_kg": "limit {} kg", "limit_basis": "limit on {} mass",
    "heaviest_container_no": "heaviest is container {}", "max_cargo_kg": "{} kg cargo", "container_tare_kg": "{} kg tare",
    "max_gross_kg": "{} kg gross", "containers": "of {} containers", "margin_kg": "{} kg margin",
    "mid50": "CTU mid-length mass share {}", "asks_for": "the clause asks for {}", "n_boxes": "{} crates checked",
    "pass": "{} pass", "needs_reinforcement": "{} need reinforcement", "fail": "{} fail",
    "pending_design": "{} pending detailed design", "rows_per_container": "rows by container: {}",
}


def _figure_value(key: str, value: Any) -> str:
    if key == "can_fit":
        return "all crates placed" if value is True else "NOT all crates placed"
    if key == "mid50" and isinstance(value, (int, float)) and not isinstance(value, bool) and value <= 1:
        return f"{value * 100:.0f}%"
    return str(_kg(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else value)


def _figure_text(figures: Dict[str, Any]) -> str:
    parts = []
    for k, v in figures.items():
        if v in (None, "", [], {}):
            continue
        text = _figure_value(k, v)
        parts.append(text if k == "can_fit" else _FIGURE_LABEL.get(k, k.replace("_", " ") + " {}").format(text))
    return "; ".join(parts) or "—"


def logistics_section(record: Dict[str, Any]) -> str:
    """Chapter 6 of the English bid-book, written from the plan. Each statement cites its clause and figure."""
    from packing_assistant.bidbook.templates_en import STATUS_EN

    inputs = record["inputs"]
    decision = record["container"]
    lines = ["## 6. Logistics & Packing (linked to the loading plan)", "",
             "> DRAFT. Every statement below cites the ITT clause it answers and the loading-plan figure behind it "
             f"(`{LINK_FILE}`). Text in `[TO CONFIRM ...]` is not supported by the plan and waits for the named person. "
             "The plan is an internal draft: a person confirms it before any booking. Qualifications and price are not "
             "written here.", "",
             f"- Tender: `{inputs['tender']['name']}` (sha256 {inputs['tender']['sha256'][:12]})",
             f"- Panel list: `{inputs['panel_list']['name']}` (sha256 {inputs['panel_list']['sha256'][:12]})",
             f"- Loading plan: " + (f"`{PLAN_FILE}` (sha256 {inputs['plan']['sha256'][:12]})" if inputs["plan"]["sha256"] else "none - no plan was made"),
             f"- Container type: {decision['reason']}", ""]
    changes = record.get("changes_since_previous")
    if changes and changes.get("inputs_changed"):
        lines += [f"> **Changed since the previous run ({changes.get('previous_generated_at')}):** {changes['summary']}.", ""]
    lines += ["| Stmt | ITT clause | Check | Status | Plan figure |", "|---|---|---|---|---|"]
    for s in record["statements"]:
        status = STATUS_EN.get(s["status"], s["status"])
        lines.append(f"| {s['id']} | {_cell(s['clause'])} | {KIND_TITLE[s['kind']]} | {status} | {_cell(_figure_text(s['figures']))} |")
    lines.append("")
    for s in record["statements"]:
        lines.append(f"**{s['id']} ({s.get('cite') or ('Clause ' + s['clause'] if s['clause'] else 'no clause')}).** {s['text']}")
        lines.append("")
    return "\n".join(lines)


def _annex_a(record: Dict[str, Any]) -> str:
    plan = record.get("plan") or {}
    lines = ["## Annex A — Loading plan summary", ""]
    if not plan:
        return "\n".join(lines + ["No loading plan was made; see §6.", ""])
    for key, label in (("container_type", "Container type"), ("containers_used", "Containers used"),
                       ("n0", "Lower bound (planner)"), ("can_fit", "All crates placed"), ("n_boxes", "Crates"),
                       ("utilization", "Volume utilisation"), ("weight_utilization", "Weight utilisation"),
                       ("mid50", "CTU mid-length mass share")):
        value = plan.get(key)
        lines.append(f"- {label}: " + ({True: "yes", False: "no"}[value] if isinstance(value, bool) else
                                       _figure_value(key, value) if key == "mid50" and value is not None else str(value)))
    for item in plan.get("per_container") or []:
        lines.append(f"- container {item.get('container_no')}: {item.get('boxes')} crates, {_kg(item.get('cargo_kg'))} kg cargo "
                     f"(panels + crates), rows {', '.join(f'{r} x {n}' for r, n in (item.get('rows') or {}).items())}")
    return "\n".join(lines) + "\n"


def report_markdown(record: Dict[str, Any]) -> str:
    """The one-page link report: clauses, matrix, statements, what changed. For the bid and logistics leads."""
    lines = ["# Tender <-> packing link (internal draft)", "",
             "Not for submission (submit_blocked = true). A person confirms the loading plan before any booking.", "",
             "## Inputs", ""]
    for key in ("tender", "panel_list", "plan"):
        item = record["inputs"][key]
        lines.append(f"- {key}: {item.get('name') or '—'} · sha256 {item.get('sha256') or '—'}")
    read = record.get("panel_list_reading") or {}
    if read.get("summary"):
        lines.append(f"- panel list read: {read['summary']}")
    if read.get("unmapped_columns"):
        from packing_assistant.tools.pack_ship_solve import cell_text

        lines.append("- panel list columns not read: " + ", ".join(cell_text(c) for c in read["unmapped_columns"]))
    lines += ["", f"Container type: {record['container']['reason']}", "", "## Logistics clauses found in the tender", ""]
    if not record["clauses"]:
        lines.append("- none: the tender states no logistics requirement the tool recognises; a person checks it.")
    for c in record["clauses"]:
        # the tender's words, marked as a quotation: text planted in a tender reads as quoted input, never as ours
        lines.append(f"- {c.get('cite') or 'Clause ' + c['clause']} ({', '.join(c['kinds'])}), quoted from the tender: “{c['text']}”")
    lines += ["", "## Response matrix (logistics)", "", "| Stmt | Clause | Check | Status | Owner | Plan figure | Note |",
              "|---|---|---|---|---|---|---|"]
    for s in record["statements"]:
        lines.append(f"| {s['id']} | {_cell(s['clause'])} | {KIND_TITLE[s['kind']]} | {s['status']} | {s['owner']} | "
                     f"{_cell(_figure_text(s['figures']))} | {_cell(s['note'])} |")
    lines += ["", "## Statements", ""]
    lines += [f"- {s['id']}: {s['text']}" for s in record["statements"]]
    changes = record.get("changes_since_previous")
    lines += ["", "## Changes since the previous run", ""]
    if not changes:
        lines.append("- first run: no previous link record in this folder.")
    else:
        lines.append(f"- {changes['summary']}")
        for c in changes["changed"]:
            diffs = "; ".join(f"{k} {_kg(a)} -> {_kg(b)}" for k, (a, b) in c["figures"].items()) or "wording"
            was = f" (was {c['previous_id']})" if c.get("previous_id") and c["previous_id"] != c["id"] else ""
            lines.append(f"- {c['id']}{was} changed ({diffs}){' - its clause text changed' if c['clause_changed'] else ''}: re-confirm")
        for c in changes["new"]:
            lines.append(f"- {c['id']} is new: confirm")
        for c in changes["withdrawn"]:
            lines.append(f"- {c.get('label') or c['id']} withdrawn: remove it from the bid ({str(c.get('text') or '')[:120]})")
        if changes["unchanged"]:
            lines.append("- re-derived with the same figures: " + ", ".join(c["id"] for c in changes["unchanged"]))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------------------
# the run

def _read_tender(path: Path) -> str:
    from packing_assistant.office_job import DOCUMENT_FILE_CHARS, read_material_checked

    body, why = read_material_checked(path, DOCUMENT_FILE_CHARS)
    if why:
        raise ValueError(f"the tender {path.name} could not be read: {why}")
    return body


def earlier_exports(folder: str) -> List[str]:
    """Word copies of the link report and bid-book already in ``folder`` (from earlier runs)."""
    root = Path(folder) if folder else None
    if not root or not root.is_dir():
        return []
    stems = (Path(REPORT_FILE).stem, Path(BIDBOOK_FILE).stem)
    return sorted(p.name for p in root.glob("*.docx") if any(p.stem == s or p.stem.startswith(s + "-") for s in stems))


def run_link(tender_path: str, packing_list: str, *, previous: Optional[Dict[str, Any]] = None,
             project_name: str = "", now: Optional[str] = None, exports: Sequence[str] = (),
             container_type: Optional[str] = None) -> Dict[str, Any]:
    """ITT + panel list -> clauses, plan, matrix rows, statements, bid-book, link record (and what changed).

    ``container_type``: the type the request names, when a person chose it (the ITT names none, several, or a size
    only). It is planned as asked; a clause that names another type then reads human_required, never covered."""
    from packing_assistant.bidbook.sg_facade import build_sg_facade_bidbook
    from packing_assistant.tools.pack_ship_solve import plan_record_json, reading_sentence, run_plan, unread_columns_sentence
    from packing_assistant.tools.tender_parse import (_count_by, _readiness_score, build_response_matrix, open_actions,
                                                      parse_tender_text)

    from packing_assistant.office_job import _resolve_job_file

    # both inputs through the job folder's read guard (inside the job root, no secrets), as every job-file read
    tender, table = _resolve_job_file(Path(tender_path)), _resolve_job_file(Path(packing_list))
    text = _read_tender(tender)
    clauses = logistics_clauses(text, source=tender.name)
    decision = container_decision(clauses, requested=container_type)
    plan: Optional[Dict[str, Any]] = None
    if decision["type"]:
        plan = run_plan(file_path=str(table), container_type=decision["type"], lang="en")
    solved = bool(plan and plan.get("ok") and plan.get("source") == "solver")
    checks = build_checks(clauses, decision, plan, table.name)
    record: Dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": now or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "inputs": {"tender": {"name": tender.name, "sha256": _sha(tender.read_bytes())},
                   "panel_list": {"name": table.name, "sha256": _sha(table.read_bytes())},
                   "plan": {"name": PLAN_FILE if solved else None, "sha256": plan_sha256(plan) if solved else None}},
        "container": decision,
        "materials_source": "panel_list",
        "clauses": [{k: c.get(k) for k in ("clause", "cite", "locator", "kinds", "text", "sha256")} for c in clauses],
        "plan": ({k: plan.get(k) for k in ("container_type", "containers_used", "n0", "can_fit", "n_boxes", "utilization",
                                          "weight_utilization", "mid50", "per_container", "conservation", "structure")}
                 if solved else None),
        "plan_refusal": None if solved else {k: (plan or {}).get(k) for k in ("source", "error", "detail", "needs_human")} if plan else
                        {"source": "not_run", "error": "container_type_undecided", "detail": decision["reason"]},
        "statements": [{k: c[k] for k in ("id", "key", "kind", "clause", "cite", "locator", "clause_sha256", "status", "text",
                                          "figures", "note", "placeholder", "owner", "sha256")} for c in checks],
        "confirmed_by_person": False,
        "submit_blocked": True,
    }
    reading = ((plan or {}).get("parse") or {}).get("reading") or {}
    read_note, unread = reading_sentence(reading), unread_columns_sentence(reading)
    if read_note or unread:
        # how the panel list was read, when it was more than "row 1 is the header, every column read, mm and kg"
        record["panel_list_reading"] = {
            "summary": read_note, "header_rows": reading.get("header_rows"), "name_from": reading.get("name_from"),
            "unit_conversions": [u for u in reading.get("units") or [] if u.get("to_mm", u.get("to_kg")) not in (1, 1.0)],
            "skipped_summary_rows": reading.get("skipped_summary_rows") or [],
            "unmapped_columns": reading.get("unmapped_columns") or []}
    record["changes_since_previous"] = compare(previous, record, exports)

    parsed = parse_tender_text(text, source="tender-packing-link")
    reqs = list(parsed.get("requirements") or [])
    base = build_response_matrix(reqs, packing_summary=None)
    rows = [r for r in base["rows"] if r.get("category") not in ("transport", "packaging")] + matrix_rows(checks)
    summary = {"n": len(rows), **{s: sum(1 for r in rows if r["status"] == s)
                                  for s in ("covered", "partial", "pending", "human_required", "gap", "review")},
               "by_owner": _count_by(rows, "owner"), "by_risk": _count_by(rows, "risk")}
    summary["readiness_score"] = _readiness_score(summary)
    matrix = {"schema": "tender.response_matrix.v1", "tool": "tender.packing_link", "rows": rows, "summary": summary}
    bidbook = build_sg_facade_bidbook(tender_text=text, parsed=parsed, matrix=matrix, open_actions=open_actions(matrix),
                                      project_title=project_name or None, logistics_section=logistics_section(record),
                                      annex_a=_annex_a(record))
    deliverables = [{"name": REPORT_FILE, "text": report_markdown(record)},
                    {"name": BIDBOOK_FILE, "text": bidbook["markdown"]},
                    {"name": LINK_FILE, "text": json.dumps(record, ensure_ascii=False, indent=2) + "\n"}]
    if solved:
        deliverables.append({"name": PLAN_FILE, "text": plan_record_json(plan, table.name)})
    counts = {s: sum(1 for c in checks if c["status"] == s) for s in ("covered", "partial", "gap", "human_required")}
    reply = (f"Linked {tender.name} and {table.name}: {len(clauses)} logistics clauses, {len(checks)} statements "
             f"({counts['covered']} covered by the plan, {counts['partial']} partial, {counts['gap']} gap, "
             f"{counts['human_required']} for a person). "
             + (f"Plan {plan.get('containers_used')} x {plan.get('container_type')} ({decision['reason']}) "
                + ("" if plan.get("can_fit") is True else "DOES NOT FIT (can_fit is not true): no count or mass is stated. ")
                if solved
                else f"No plan: {decision['reason'] if not decision['type'] else (plan or {}).get('error')}. "
                + _refusal_rows(plan))
             + (f"Panel list read: {read_note}. " if read_note else "")
             + (f"{unread} " if unread and not solved else "")
             + (f"Since the previous run: {record['changes_since_previous']['summary']}. " if record["changes_since_previous"] else "")
             + "Internal draft, submit_blocked=true; a person confirms the plan before booking.")
    return {"ok": True, "schema": SCHEMA, "record": record, "statements": record["statements"], "matrix": matrix,
            "handoff": parsed.get("handoff"), "bidbook_markdown": bidbook["markdown"], "deliverables": deliverables,
            "plan": plan, "reply": reply, "submit_blocked": True}


def _refusal_rows(plan: Optional[Dict[str, Any]], limit: int = 4) -> str:
    """The panel-list rows a person must fix, as the English questions name them (sheet row and mark)."""
    rows = [r for r in (plan or {}).get("needs_human") or [] if isinstance(r, dict)]
    if not rows:
        return ""
    asks = " ".join(str(r.get("ask") or r.get("reason")) for r in rows[:limit])
    more = f" ({len(rows) - limit} more in tender-packing-link.json)" if len(rows) > limit else ""
    return f"{len(rows)} panel-list row{'s' if len(rows) > 1 else ''} to fix first: {asks}{more} "


def load_previous(path: str) -> Optional[Dict[str, Any]]:
    """The previous link record in the output folder, if any. An unreadable one is reported, not trusted."""
    target = Path(path) if path else None
    if not target or not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": "unreadable"}
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------------------------------------
# the record, read back: what a model may explain and a question may be answered from

RECORD_VIEW = "tender.link_record.view.v1"
_VIEW_TEXT = 320


def link_record_view(record: Dict[str, Any], *, where: str = "") -> Dict[str, Any]:
    """The link record as a read-only tool result: every status, figure and clause text a reply may use, labelled so a
    small model cannot misread one figure for another (the heaviest container's gross mass is not the container's
    rated payload). Nothing here is computed afresh: it is the record as it was written."""
    statements = [s for s in record.get("statements") or [] if isinstance(s, dict)]
    plan = record.get("plan") or None
    decision = record.get("container") or {}
    heaviest = next((dict(s.get("figures") or {}) for s in statements if s.get("kind") == "gross_mass"
                     and (s.get("figures") or {}).get("max_gross_kg") is not None), None)
    if heaviest is None and plan and plan.get("can_fit") is True and plan.get("per_container"):
        heaviest = heaviest_container(plan["per_container"], plan.get("container_type") or decision.get("type"))
    if heaviest is not None:
        heaviest = {k: heaviest.get(k) for k in ("heaviest_container_no", "max_cargo_kg", "container_tare_kg", "max_gross_kg",
                                                 "containers", "limit_kg") if k in heaviest}
    counts = {s: sum(1 for c in statements if c.get("status") == s) for s in ("covered", "partial", "gap", "human_required")}
    return {
        "ok": True, "schema": RECORD_VIEW, "record": where or LINK_FILE, "generated_at": record.get("generated_at"),
        "inputs": {role: (record.get("inputs") or {}).get(role, {}).get("name") for role in ("tender", "panel_list")},
        "container_type": decision.get("type"), "container_source": decision.get("source"),
        "plan": ({"container_type": plan.get("container_type"), "containers_used": plan.get("containers_used"),
                  "can_fit": plan.get("can_fit")} if plan else None),
        "plan_refusal": record.get("plan_refusal"),
        "heaviest_container": heaviest,
        "labels": {"max_cargo_kg": "panels and crates in the heaviest loaded container, no tare",
                   "max_gross_kg": "max_cargo_kg + container tare: the gross mass of the heaviest loaded container",
                   "limit_kg": "the clause's limit, not a plan figure"},
        "counts": counts,
        "statements": [{"id": s.get("id"), "clause": s.get("clause"), "kind": s.get("kind"), "status": s.get("status"),
                        "text": str(s.get("text") or "")[:_VIEW_TEXT]} for s in statements],
        "clauses": [{"clause": c.get("clause"), "text": str(c.get("text") or "")[:_VIEW_TEXT]}
                    for c in record.get("clauses") or [] if isinstance(c, dict)],
        "confirmed_by_person": record.get("confirmed_by_person") is True,
        "submit_blocked": True,
        "rule": ("Statuses, figures and the container type are the record's. Explain them; do not change them. A person "
                 "confirms every statement before booking or submission."),
    }


def latest_link_record(roots: Sequence[Path], session_dir: Optional[Path] = None) -> Optional[Path]:
    """The link record a question means: this session's own, else the newest one under the given output roots."""
    if session_dir is not None:
        own = [p for p in session_dir.rglob(LINK_FILE) if p.is_file()]
        if own:
            return max(own, key=lambda p: p.stat().st_mtime)
    found = [p for root in roots if root and root.is_dir() for p in root.rglob(LINK_FILE) if p.is_file()]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None
