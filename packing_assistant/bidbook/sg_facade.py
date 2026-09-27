"""Assemble an English Singapore façade bid-book draft from parse + matrix + packing."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from packing_assistant.bidbook import templates_en as T

DEMO_BIDDER: Dict[str, str] = {
    "legal_name": "Harbourline Facade Pte. Ltd.",
    "tag": "DEMO",
    "address": "[TO FILL] · Singapore",
    "uen": "[TO FILL]",
    "workhead": "[TO FILL]",
}

REQUIRED_HEADINGS: List[str] = [
    "1. Cover & Form of Tender",
    "2. Executive Summary",
    "3. Compliance & Deviation Schedule",
    "4. Technical Approach",
    "5. Method Statement & Programme",
    "6. Logistics & Packing Evidence",
    "7. WSH, Quality & Environment",
    "8. Resources, Track Record & Commercial",
    "Annex A",
    "Annex B",
]

_DEV = {
    "covered": "No Deviation",
    "partial": "Partial Deviation",
    "gap": "Negative Deviation",
    "human_required": "Pending SME",
    "review": "Pending review",
    "pending": "To confirm",
}


# A document-type heading names what the paper is, not the project: "INVITATION TO TENDER" is not a title.
_GENERIC_HEADING = re.compile(
    r"(?i)^(?:(?:invitation|instructions?)\s+to\s+tender(?:ers?)?|tender\s+documents?|conditions\s+of\s+(?:tender|contract)"
    r"|form\s+of\s+tender|(?:table\s+of\s+)?contents|(?:section|part|appendix|annex|schedule|volume)\b)")
_SYNTHETIC = re.compile(r"\bSYNTHETIC\b")


def infer_project_title(text: str) -> str:
    """The first short Latin line that names the job (a document-type heading such as "INVITATION TO TENDER" or
    "SECTION 1" is skipped); otherwise the demo default."""
    for raw in (text or "").splitlines():
        s = raw.strip().strip("#").strip()
        if not s:
            continue
        if re.match(r"^[一二三四五六七八九十\d]+[、.\s]", s):
            continue
        if len(s) < 8 or len(s) > 140:
            continue
        if _GENERIC_HEADING.match(s):
            continue
        if re.search(r"[A-Za-z]", s):
            return s[:140]
    return "Sample Singapore Façade Tender"


def is_synthetic(text: str) -> bool:
    """The tender marks itself SYNTHETIC (the demo fixtures do, in capitals)."""
    return bool(_SYNTHETIC.search(text or ""))


def _fmt_mid50(mid: Any) -> str:
    if mid is None or mid == "":
        return "—"
    try:
        n = float(mid)
    except (TypeError, ValueError):
        return str(mid)
    return f"{n * 100:.1f}%" if n <= 1.0 else f"{n:.1f}%"


def _schedule_block(requirements: List[Dict[str, Any]]) -> str:
    for r in requirements or []:
        if r.get("id") == "delivery_time" or r.get("category") == "schedule":
            snips = r.get("snippets") or []
            quoted = "; ".join(str(s) for s in snips[:3]) if snips else "(schedule clause captured)"
            return (
                "**Programme note from the invitation (unverified):**\n\n"
                f"> {quoted}\n\n"
                "Contractual completion dates remain `[TO FILL]` after planner review."
            )
    return (
        "No explicit delivery-period clause was extracted. "
        "Milestone dates remain `[TO FILL]`."
    )


def _deviation_table(matrix: Dict[str, Any]) -> str:
    rows = list((matrix or {}).get("rows") or [])
    lines = [
        "## 3. Compliance & Deviation Schedule",
        "",
        "Deviation codes: **No Deviation** (covered by tools or stated compliance) · "
        "**Partial Deviation** · **Negative Deviation** · **Pending SME**.",
        "",
        "| Ref | Requirement | Type | Status | Deviation | Owner | Proposal location |",
        "|-----|-------------|------|--------|-----------|-------|-------------------|",
    ]
    if not rows:
        lines.append("| — | (no clauses extracted) | — | — | To confirm | — | — |")
        return "\n".join(lines) + "\n"
    for r in rows:
        title = T.en_label(r.get("title") or r.get("req_id") or "", T.REQUIREMENT_EN).replace("|", "/")
        ref = str(r.get("requirement_ref") or r.get("req_id") or "—").replace("|", "/")
        rtype = str(r.get("requirement_type") or "—")
        st = str(r.get("status") or "pending")
        dev = _DEV.get(st, "To confirm")
        loc = T.en_label(r.get("proposal_location") or "—", T.LOCATION_EN).replace("|", "/")
        owner = str(r.get("owner") or "—")
        lines.append(f"| {ref} | {title} | {rtype} | {T.STATUS_EN.get(st, st)} | {dev} | {owner} | {loc} |")
    return "\n".join(lines) + "\n"


def _logistics_chapter(packing_summary: Optional[Dict[str, Any]]) -> str:
    pack = packing_summary or {}
    lines = [
        "## 6. Logistics & Packing Evidence",
        "",
        "Numbers below come from the packing **tools** (Team A/B), not from a language model.",
        "",
    ]
    if not pack:
        lines += [
            "Delivery packing was **not run** for this draft. Chapter 6 has no loading-plan figures.",
            "",
        ]
        return "\n".join(lines)
    if pack.get("materials_source") == "sample":
        lines += [
            "> **SAMPLE MATERIALS.** No panel list was given: the figures below were computed on the tool's built-in "
            "sample materials (`materials_source = sample`), not on this project's packing list. They support no bid statement.",
            "",
        ]
    lines += [
        f"- **materials source:** {pack.get('materials_source') or '—'}",
        f"- **all crates placed:** {_yes_no(pack.get('can_fit'))}",
        f"- **container type:** {pack.get('container_type') or '—'}",
        f"- **containers used:** {pack.get('containers_used')}",
        f"- **lower bound (planner):** {pack.get('n0')}",
        f"- **shipping check passed:** {_yes_no(pack.get('ship_ok'))}",
        f"- **CTU mid-length mass share:** {_fmt_mid50(pack.get('mid50'))}",
        f"- **planner phase:** {pack.get('phase') or '—'}",
        "",
        "Lashing design, VGM declaration and bill of lading remain `[TO FILL]`.",
        "",
    ]
    return "\n".join(lines)


_ANNEX_A_LABELS = (("can_fit", "All crates placed"), ("container_type", "Container type"), ("containers_used", "Containers used"),
                   ("n0", "Lower bound (planner)"), ("ship_ok", "Shipping check passed"),
                   ("mid50", "CTU mid-length mass share"), ("phase", "Planner phase"))


def _yes_no(value: Any) -> str:
    return {True: "yes", False: "no"}.get(value, "—") if isinstance(value, bool) or value is None else str(value)


def _annex_a(packing_summary: Optional[Dict[str, Any]]) -> str:
    pack = packing_summary or {}
    lines = ["## Annex A — Packing summary", ""]
    if not pack:
        lines.append("No packing_summary attached.")
        return "\n".join(lines) + "\n"
    for k, label in _ANNEX_A_LABELS:
        v = pack.get(k)
        if k == "mid50":
            v = _fmt_mid50(v)
        elif k in ("can_fit", "ship_ok"):
            v = _yes_no(v)
        lines.append(f"- {label}: {v}")
    return "\n".join(lines) + "\n"


def _annex_b(open_actions: List[Dict[str, Any]]) -> str:
    lines = [
        "## Annex B — Human / SME open actions",
        "",
        "These rows are **not** auto-closed. Directors must complete them before any submission.",
        "",
    ]
    if not open_actions:
        lines.append("- None listed (commercial / legal sign-off still required).")
        return "\n".join(lines) + "\n"
    for a in open_actions:
        status = str(a.get("status") or "")
        lines.append(
            f"- **[{a.get('risk')}]** {T.en_label(a.get('title'), T.REQUIREMENT_EN)} · {a.get('owner')} · "
            f"{T.STATUS_EN.get(status, status)} — {T.en_label(a.get('action'), T.ACTION_EN)}"
        )
    return "\n".join(lines) + "\n"


def _scoring_map(handoff: Optional[Dict[str, Any]]) -> str:
    """List extracted scoring points only. Do not invent PQM weights."""
    ho = handoff or {}
    points = list(ho.get("scoring_points") or [])
    env = ho.get("envelope")
    lines = [
        "## 4b. Scoring-point map (extracted)",
        "",
        "Each row is copied from the invitation. Weights are **not** taken from the "
        "BCA PQM Framework published bands (portal title *Price Quality Method (PQM) Framework*, "
        "page last updated 26 January 2026; public CW01/CW02 construction). "
        "This ITT's scores stay `[UNSPECIFIED]` unless quoted below.",
        "",
    ]
    if env:
        lines.append(f"- **Envelope scheme in ITT:** `{env}` (verbatim detect; not invented).")
        lines.append("")
    if not points:
        lines += [
            "No scoring-point lines were extracted. Technical chapters stay a generic "
            "skeleton and must be aligned to the ITT evaluation table by a person.",
            "",
        ]
        return "\n".join(lines)
    lines += [
        "| Ref | Extracted scoring line | Response chapter | Status |",
        "|-----|------------------------|------------------|--------|",
    ]
    for p in points:
        ref = str(p.get("requirement_ref") or "—").replace("|", "/")
        text = str(p.get("text") or "").replace("|", "/")[:160]
        lines.append(f"| {ref} | {text} | §4 / tech outline | [UNSPECIFIED] |")
    lines.append("")
    return "\n".join(lines)


def build_sg_facade_bidbook(
    *,
    tender_text: str = "",
    parsed: Optional[Dict[str, Any]] = None,
    matrix: Optional[Dict[str, Any]] = None,
    packing_summary: Optional[Dict[str, Any]] = None,
    open_actions: Optional[List[Dict[str, Any]]] = None,
    project_title: Optional[str] = None,
    p0_confirmed: bool = False,
    logistics_section: Optional[str] = None,
    annex_a: Optional[str] = None,
) -> Dict[str, Any]:
    """Deterministic English bid-book. Returns markdown + meta.

    ``logistics_section`` / ``annex_a``: chapter 6 and Annex A written elsewhere from a loading plan, clause by clause
    (tender_packing_link.py). Without them the chapter prints the packing summary as before."""
    parsed = parsed or {}
    matrix = matrix or {}
    bidder = DEMO_BIDDER
    reqs = list((parsed.get("requirements") or []))
    sm = (matrix.get("summary") or {})
    title = (project_title or "").strip() or infer_project_title(tender_text)
    actions = list(open_actions or [])
    ho = parsed.get("handoff") if isinstance(parsed, dict) else None
    ctx = {
        "legal_name": bidder["legal_name"],
        "tag": bidder["tag"],
        "address": bidder["address"],
        "uen": bidder["uen"],
        "workhead": bidder["workhead"],
        "project_title": title,
        "n_req": sm.get("n", len(reqs)),
        "n_covered": sm.get("covered", 0),
        "readiness": sm.get("readiness_score", "—"),
        "n_open": len(actions),
        "schedule_block": _schedule_block(reqs),
    }
    parts = [
        f"# Contractor's Proposal (Draft) — {title}",
        "",
        "> " + T.WATERMARK.format(**ctx),
        "",
        *([T.SYNTHETIC_BANNER, ""] if is_synthetic(tender_text) else []),
        (
            "> P0 noted by operator — still a draft, not for GeBIZ."
            if p0_confirmed
            else "> P0 qualification / reject / star items are **unconfirmed**. Not a bid decision."
        ),
        "",
        T.FORM_OF_TENDER.format(**ctx).rstrip(),
        "",
        T.EXEC_SUMMARY.format(**ctx).rstrip(),
        "",
        _deviation_table(matrix).rstrip(),
        "",
        T.TECHNICAL.format(**ctx).rstrip(),
        "",
        _scoring_map(ho).rstrip(),
        "",
        T.METHOD.format(**ctx).rstrip(),
        "",
        (logistics_section or _logistics_chapter(packing_summary)).rstrip(),
        "",
        T.WSH.format(**ctx).rstrip(),
        "",
        T.RESOURCES.format(**ctx).rstrip(),
        "",
        (annex_a or _annex_a(packing_summary)).rstrip(),
        "",
        _annex_b(actions).rstrip(),
        "",
    ]
    markdown = "\n".join(parts)
    return {
        "schema": "tender.bidbook.sg_facade.v1",
        "jurisdiction": "SG",
        "sector": "facade_curtain_wall",
        "language": "en",
        "bidder": dict(bidder),
        "project_title": title,
        "synthetic": is_synthetic(tender_text),
        "watermark": T.WATERMARK.format(**ctx),
        "headings": list(REQUIRED_HEADINGS),
        "markdown": markdown,
        "n_chars": len(markdown),
    }
