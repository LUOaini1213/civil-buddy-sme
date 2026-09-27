#!/usr/bin/env python3
"""Gate: a Singapore façade subcontract ITT through the shipped tender parse, matrix and English bid-book.

    python scripts/test_facade_tender.py

The ITT below is SYNTHETIC - written for this test; the project, the Main Contractor and every figure are
invented, and it is no contractor's document. It is the research fixture of 2026-09-26, run once on
0f9c13c, which found:
  - theme words matched inside other words: "struCTUral" made the PMU-test and PE-endorsement clauses
    重心/绑扎/系固 (CTU), and "NOT" / "not" made "shall not be stacked" 超长/异形运输 (OT);
  - with a packing run that fits, every packaging/transport row read "covered · No Deviation" - the A-frame,
    upright, no-stacking clause included, which the packing engine does not model; so did a 20GP-only clause
    beside a 40HQ run (§6 of the same bid-book prints 40HQ), and a lashing / CTU clause on mid50 alone;
  - handoff.workheads knew only CW01/CW02, so §9 said "workhead: 未在原文检出" beside "CR16 已检出".
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ITT = """SYNTHETIC TENDER DOCUMENT - WRITTEN FOR TESTING ONLY - NOT A REAL PROJECT OR EMPLOYER
Invitation to Tender for Facade Works Subcontract - Synthetic Office Tower (Ref SYN-ITT-2026-07)
1. Scope of Works: design, supply, fabrication, delivery and installation of unitised curtain wall, aluminium cladding and a glass canopy.
2. Tender closing date: 16 November 2026, 12:00 noon.
3. Tender validity: the tender shall remain valid for 90 days from the tender closing date.
4. Registration: the Tenderer shall be registered with BCA under workhead CR16 (Curtain Walls) at grade L4 or higher.
5. Safety: the Tenderer shall hold bizSAFE Level 3 or above.
6. Contract period: 420 calendar days from the date of site possession.
7. Performance bond: 10% of the Subcontract Sum in the form of a banker's guarantee, valid until 6 months after completion.
8. Liquidated damages: S$5,000 per day of delay.
9. Performance mock-up (PMU): one two-storey PMU shall be tested at an accredited laboratory for air infiltration, static and dynamic water penetration, structural performance under the design wind load of 2.4 kPa and inter-storey movement, before fabrication starts.
10. Visual mock-up (VMU): a VMU shall be erected on site for the Architect's approval within 8 weeks of award.
11. Heat soak test: all fully tempered glass shall be heat soak tested to EN 14179-1 and the heat soak records submitted for each batch.
12. Site water test: hose testing shall be carried out on 5% of the installed joints.
13. Structural calculations shall be endorsed by a Professional Engineer (Civil) registered in Singapore.
14. Warranty: 10 years for water tightness, and 20 years for the PVDF coating of aluminium.
15. Defects liability period: 12 months from completion.
16. Packing and delivery: unitised panels shall be transported upright on steel A-frame stillages with the glass faces protected; glazed panels shall not be stacked.
17. Shop drawings shall be submitted within 6 weeks of award.
18. Evaluation: Price Quality Method with Quality 40% and Price 60%.
19. Insurance: the Tenderer shall maintain Contractor's All Risks and Work Injury Compensation insurance.
20. Payment terms: monthly progress payments with 5% retention.
21. A tender without a signed Form of Tender shall be rejected.
22. A method statement for installation and working at height shall be submitted with the tender.
23. Track record: the Tenderer shall list similar curtain wall projects completed in the last 5 years.
"""

# the same ITT as a document (SECTION chapters, an appendix table): the path `civil exec "解析招标 …"` takes
ITT_DOC = """# INVITATION TO TENDER

# SYNTHETIC EXAMPLE - Facade Works Subcontract for Synthetic Office Tower

Tender Reference: SYN-ITT-2026-07

This document was written for software testing only. The project, employer and all figures are invented.

SECTION 1 INVITATION TO TENDER

1.1 Synthetic Main Contractor Pte Ltd (the "Main Contractor") invites tenders for the design, supply, fabrication, delivery and installation of unitised curtain wall, aluminium cladding and a glass canopy for the Synthetic Office Tower.

1.2 Tenderers shall be registered with the Building and Construction Authority under workhead CR16 (Curtain Walls) with a financial grade of L4 and above.

1.3 The closing date for submission of tenders is 16 November 2026 at 12.00 pm. Tenders received after the closing date and time shall be rejected.

SECTION 2 INSTRUCTIONS TO TENDERERS

APPENDIX TO THE INSTRUCTIONS TO TENDERERS

| Item | Description | Particulars |
| --- | --- | --- |
| 1 | Main Contractor | Synthetic Main Contractor Pte Ltd |
| 2 | Contract Period | 420 calendar days from the date of site possession |
| 3 | Tender Validity Period | 90 days from the closing date |
| 4 | Alternative Tenders | Not permitted |

2.1 A tender that does not include the priced Schedule of Rates shall be rejected.

SECTION 4 PARTICULAR SPECIFICATION - FACADE

4.1 Performance mock-up: one two-storey performance mock-up (PMU) shall be tested at an accredited laboratory for structural performance under the design wind load of 2.4 kPa before fabrication starts.

4.5 Structural calculations and shop drawings shall be endorsed by a Professional Engineer (Civil) registered in Singapore and submitted within 6 weeks of award.

4.7 Packing and delivery: unitised panels shall be transported upright on steel A-frame stillages with the glass faces protected; glazed panels shall not be stacked.
"""

FITS = {"can_fit": True, "containers_used": 2, "n0": 2, "ship_ok": True, "mid50": 0.72,
        "container_type": "40HQ", "phase": "done"}
NOT_OURS = ("Professional Engineer", "mock-up", "PMU", "A-frame", "stacked")

failed: list = []


def check(name: str, ok: bool, detail: object = "") -> None:
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"))
    if not ok:
        failed.append(name)


def themes(text: str) -> set:
    from packing_assistant.tools.tender_parse import parse_tender_text

    return {r["id"] for r in parse_tender_text(text, source="facade-unit", sides="none")["requirements"]
            if r.get("item_kind") == "theme"}


def deviation_rows(bidbook_md: str) -> list:
    sec = bidbook_md.split("## 3. Compliance & Deviation Schedule", 1)[1].split("\n## ", 1)[0]
    return [ln for ln in sec.splitlines() if ln.startswith("| ") and not ln.startswith("| Ref")]


def main() -> int:
    from packing_assistant.tools.tender_parse import build_response_matrix, parse_tender_text, run_tender_pipeline

    # 1. short Latin theme codes are tokens, and still match beside Chinese
    for line, rid in (("Structural calculations shall be endorsed by a Professional Engineer.", "cog_lashing"),
                      ("Aluminium flashings shall be PVDF coated.", "cog_lashing"),
                      ("Alternative tenders are not permitted.", "overlength"),
                      ("NOT A REAL PROJECT; glazed panels shall not be stacked.", "overlength"),
                      ("Isolation pads shall separate aluminium from steel.", "qualification"),
                      ("Concrete modulus E = 20GPa.", "transport_container"),
                      ("投标人须办理CA证书并按时解密。", "personnel"),
                      ("须采用40HQ集装箱海运。", "pkg_standard")):
        check(f"no {rid} in {line!r}", rid not in themes(line), sorted(themes(line)))
    for line, rid in (("装柜须符合CTU规范。", "cog_lashing"),
                      ("Lashing shall follow the CTU Code.", "cog_lashing"),
                      ("大件可用OT柜发运。", "overlength"),
                      ("Long members may go in a 40OT.", "overlength"),
                      ("须通过ISO9001认证。", "qualification"),
                      ("The Tenderer shall hold ISO 9001.", "qualification"),
                      ("三类人员须持B证。", "personnel"),
                      ("须为在BCA注册的承包商。", "registration"),
                      ("采用20GP发运。", "transport_container"),
                      ("装箱单须随货。", "pkg_standard")):
        check(f"{rid} in {line!r}", rid in themes(line), sorted(themes(line)))

    # 2. the façade ITT with a packing run that fits
    for label, text in (("text", ITT), ("document", ITT_DOC)):
        pipe = run_tender_pipeline(text, packing_summary=FITS, source=f"facade-{label}")
        ids = {r["id"] for r in pipe["parse"]["requirements"] if r.get("item_kind") == "theme"}
        check(f"[{label}] no 重心/绑扎/系固 theme (struCTUral)", "cog_lashing" not in ids, sorted(ids))
        check(f"[{label}] no 超长/异形运输 theme (NOT / not)", "overlength" not in ids, sorted(ids))
        rows = pipe["matrix"]["rows"]
        wrong = [(r["req_id"], s[:60]) for r in rows if r.get("status") == "covered"
                 for s in (r.get("snippets") or []) if any(w in s for w in NOT_OURS)]
        check(f"[{label}] no PMU / PE / A-frame clause covered by the packing run", not wrong, wrong)
        pkg = next((r for r in rows if r["req_id"] == "pkg_standard"), None)
        check(f"[{label}] A-frame packing clause is Pending SME", pkg is not None and pkg.get("status") == "human_required",
              pkg and (pkg.get("status"), pkg.get("snippets")))
        note = str(((pkg or {}).get("evidence") or {}).get("note") or "")
        check(f"[{label}] its note names what the run does not model", "a-frame" in note and "upright" in note, note)
        check(f"[{label}] open actions say the packing run does not cover it",
              any(a.get("req_id") == "pkg_standard" and "装柜结果不覆盖本条" in str(a.get("action")) for a in pipe["open_actions"]),
              [a for a in pipe["open_actions"] if a.get("req_id") == "pkg_standard"])
        dev = deviation_rows(pipe["bidbook_markdown"])
        check(f"[{label}] bid-book §3 has no No Deviation row", not [d for d in dev if "No Deviation" in d],
              [d for d in dev if "No Deviation" in d])
        check(f"[{label}] packing row in bid-book reads Pending SME",
              any("Packaging / crating requirements" in d and "Pending SME" in d for d in dev), dev[:3])   # the English label of 包装/装箱要求
        ho = pipe["handoff"]
        check(f"[{label}] handoff.workheads has CR16", "CR16" in (ho.get("workheads") or []), ho.get("workheads"))
        s9 = next((ln for ln in pipe["extract_table_markdown"].splitlines() if ln.startswith("- workhead:")), "")
        check(f"[{label}] §9 names CR16", s9.startswith("- workhead: CR16"), s9)

    # 3. what the packing run does address stays covered; a failed run is still a gap
    addressed = "Panels shall be shipped in 40HQ containers.\n单柜不超过货载限制。"
    reqs = parse_tender_text(addressed, source="facade-control", sides="none")["requirements"]
    st = {r["req_id"]: r["status"] for r in build_response_matrix(reqs, packing_summary=FITS)["rows"]}
    check("container clause covered by a fitting run", st.get("transport_container") == "covered", st)
    check("payload clause covered by a fitting run", st.get("weight_limit") == "covered", st)
    mixed = "Glazed units shall be shipped in 40HQ containers, upright on A-frames, and shall not be stacked."
    reqs = parse_tender_text(mixed, source="facade-mixed", sides="none")["requirements"]
    row = next(r for r in build_response_matrix(reqs, packing_summary=FITS)["rows"] if r["req_id"] == "transport_container")
    check("container clause naming A-frame / upright / no stacking is Pending SME", row["status"] == "human_required", row["status"])
    # a container clause is covered only when the run used the one container it names (and it is not refused);
    # mid50 is the centre of gravity, not lashing / securing / the CTU Code
    no_ct = {k: v for k, v in FITS.items() if k != "container_type"}
    for text, pack, rid, want in (
            ("Panels shall be shipped in 20GP containers only; 40HQ containers are not accepted at the site gate.", FITS,
             "transport_container", "human_required"),
            ("须采用20GP集装箱运输，不接受40HQ。", FITS, "transport_container", "human_required"),
            ("40HQ containers are not accepted at the site gate.", FITS, "transport_container", "human_required"),
            ("Long members shall go in 40OT containers.", FITS, "transport_container", "human_required"),
            ("须采用开顶集装箱。", FITS, "transport_container", "human_required"),
            ("Delivery in 45HQ or 40HQ containers.", FITS, "transport_container", "human_required"),
            ("Delivery by 40 ft high cube containers.", FITS, "transport_container", "covered"),
            ("Within a framework agreement (Clause No. 5), delivery by 40HQ containers.", FITS, "transport_container", "covered"),
            ("Panels shall be shipped in 40HQ containers.", no_ct, "transport_container", "human_required"),
            ("货物须以集装箱海运。", no_ct, "transport_container", "covered"),
            ("Lashing and securing shall comply with the CTU Code; lashing certificates shall be submitted.", FITS,
             "cog_lashing", "partial"),
            ("装柜须符合CTU规范。", FITS, "cog_lashing", "partial"),
            ("重心与绑扎须符合 CTU 要求。", FITS, "cog_lashing", "partial"),
            ("货物重心须居中。", FITS, "cog_lashing", "covered"),
            ("货物重心须居中。", {**FITS, "mid50": 0.4}, "cog_lashing", "partial")):
        reqs = parse_tender_text(text, source="facade-evidence", sides="none")["requirements"]
        row = next((r for r in build_response_matrix(reqs, packing_summary=pack)["rows"] if r["req_id"] == rid), None)
        check(f"{rid} of {text!r} ({pack.get('container_type')}, mid50 {pack.get('mid50')}) is {want}",
              row is not None and row["status"] == want, row and (row["status"], (row.get("evidence") or {}).get("note")))
    reqs = parse_tender_text("Panels shall be shipped in 20GP containers only.", source="facade-note", sides="none")["requirements"]
    note = str(next(r for r in build_response_matrix(reqs, packing_summary=FITS)["rows"]
                    if r["req_id"] == "transport_container")["evidence"].get("note"))
    check("the container note names the clause's code and the run's", "20GP" in note and "40HQ" in note, note)
    reqs = parse_tender_text("Lashing shall follow the CTU Code.", source="facade-note", sides="none")["requirements"]
    note = str(next(r for r in build_response_matrix(reqs, packing_summary=FITS)["rows"]
                    if r["req_id"] == "cog_lashing")["evidence"].get("note"))
    check("the lashing note says mid50 is the centre of gravity only", "mid50" in note and "绑扎" in note, note)
    pipe = run_tender_pipeline("Panels shall be shipped in 20GP containers only; 40HQ containers are not accepted.\n"
                               "Lashing and securing shall comply with the CTU Code.", packing_summary=FITS, source="facade-bb")
    dev = deviation_rows(pipe["bidbook_markdown"])
    check("bid-book §3 does not say No Deviation to a 20GP-only clause beside §6 '40HQ'",
          "**container type:** 40HQ" in pipe["bidbook_markdown"]
          and not [d for d in dev if "集装箱/运输方式" in d and "No Deviation" in d], dev)
    check("bid-book §3 does not say No Deviation to a lashing clause", not [d for d in dev if "重心/绑扎/系固" in d
                                                                             and "No Deviation" in d], dev)
    reqs = parse_tender_text(ITT, source="facade-fail")["requirements"]
    fail = {r["req_id"]: r["status"] for r in build_response_matrix(
        reqs, packing_summary={"can_fit": False, "containers_used": 0, "n0": 2, "ship_ok": False})["rows"]}
    check("a run that does not fit leaves the packing clause a gap", fail.get("pkg_standard") == "gap", fail)

    # 4. workheads: CW kept, CR beside Chinese, nothing invented
    for text, want, not_want in (("BCA workhead CW01。", ["CW01"], "CW02"),
                                 ("须具备BCA CR16幕墙资质L4。", ["CR16"], "CW01"),
                                 ("Ref SCR160 and CR1 are not workheads.", [], "CR")):
        wh = parse_tender_text(text, source="wh", sides="none")["handoff"]["workheads"]
        check(f"workheads of {text!r} == {want}", wh == want and not_want not in " ".join(wh), wh)

    print(("PASS" if not failed else f"FAIL {len(failed)}") + " facade-tender")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
