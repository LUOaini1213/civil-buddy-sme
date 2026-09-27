#!/usr/bin/env python3
"""Scores the tender <-> packing link's clause reader on the SEALED held-out set in this folder.

The set (clauses.json, sealed_itt.md, injections.json + injections/) was written blind on 2026-09-26 by an agent
that had not read the reader's rules; see README.md. This scorer calls only the public entry points of
packing_assistant.tender_packing_link: logistics_clauses, container_decision, build_checks and run_link, plus
pack_ship_solve.run_plan for the plan the statements are written against. It never changes the gold.

Per clause (30: 25 logistics + 5 distractors), each clause read as its own small tender (its context line - the
parent heading or the table caption and header - above it):
  found              logistics clauses the reader returns with any kind / 25 (the rest are silently lost)
  distractor rows    distractors (insurance, warranty, LDs, retention) returned as a logistics clause / 5
  kind recall        gold kinds found / gold kinds (mass_limit: a limit row of the right family)
  container limit    per-container limit read with the gold value and basis / 7
  false container    a per-package, crane or vehicle limit read as a per-container limit / 4
  other limit placed a per-package, crane or vehicle limit read in its own row with the gold value / 4
  decision           plan:<type> / person_decides / not_stated as gold / 30
  covered w/o proof  "covered" rows on a clause a plan cannot evidence (site hours, crane, GVW), on a distractor, or
                     with a limit or type the gold does not hold
Whole document (sealed_itt.md):
  cite exact         the statement's cite equals the gold reference (a leading "Clause " ignored) / 25
  cite right number  the cite names the gold clause number and sub-item (or the table and row; or, unnumbered, no
                     number at all) - a reader could find the clause from it / 25
  cite wrong         a cite that names another clause, drops a sub-item, or invents a number ("Clause L3") / 25
  doc decision       person_decides (the container clauses conflict on purpose)
Injections (8): run_link on each injected file against the same run on its control file; a statement whose kind,
clause, status or plan figures differ, or the planted sentence in a statement outside a quoted clause text, fails.

No model, no network, about 10 s, deterministic.
Usage: python test/benchmarks/tender_link_sealed/score_sealed.py [--out PATH] [--verbose] [--check]
--check exits 1 when a score falls below FLOORS: the first scored run of PR #68 (2026-09-27), see README.md.
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("CIVIL_SEALED_REPO") or HERE.parents[2]).resolve()
sys.path.insert(0, str(ROOT))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
for _key in [k for k in os.environ if k.endswith("_API_KEY")] + ["CIVIL_SANDBOX", "CIVIL_APPROVAL", "CIVIL_JOB_ROOT"]:
    os.environ.pop(_key, None)
os.environ["CIVIL_AGENT_MODE"] = "steps"

PANELS = ROOT / "examples" / "facade-demo" / "facade_panels.xlsx"
KIND_MAP = {"container_type": {"container_type"}, "securing": {"securing"}, "handling": {"handling"}, "crating": {"crating"},
            "delivery_sequence": {"delivery_sequence"}, "logistics_plan": {"logistics_plan_submission"},
            "site_access": {"site_access"}}
BASIS = {"gross_container": "gross", "cargo": "cargo"}
# the AFTER score of the first scored run (PR #68 head 91ea80b, 2026-09-27): (section, key, "min" | "max", bound)
FLOORS = (
    ("clauses", "found", "min", 25), ("clauses", "distractor_rows", "max", 1), ("clauses", "kinds_found", "min", 38),
    ("clauses", "container_limit_value", "min", 7), ("clauses", "container_limit_exact", "min", 6),
    ("clauses", "false_container_limit", "max", 0), ("clauses", "other_limit_placed", "min", 4),
    ("clauses", "decision_ok", "min", 29), ("clauses", "covered_without_proof", "max", 0),
    ("document", "cite_exact", "min", 16), ("document", "cite_number", "min", 25), ("document", "cite_wrong", "max", 0),
    ("document", "cite_missing", "max", 0), ("document", "distractor_rows", "max", 1), ("document", "covered", "max", 0),
    ("document", "decision_ok", "min", True),
    ("injections", "same_as_control", "min", 8), ("injections", "covered_changed", "max", 0),
    ("injections", "planted_as_own_text", "max", 0),
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _item_doc(item) -> str:
    """The clause as its own small tender: the parent heading, or the table caption and header, above it."""
    ctx = item.get("context_line")
    if ctx and ctx.startswith("|"):
        caption = re.sub(r"\s*\(table\)\s*$", "", item.get("section") or "")
        width = ctx.count("|") - 1
        return f"{caption}\n\n{ctx}\n|{' --- |' * width}\n{item['text']}\n"
    if ctx:
        return f"{ctx}\n\n{item['text']}\n"
    return item["text"] + "\n"


def _own(clauses, item):
    """The reader's clauses that come from the item's own text (not its context line)."""
    body = _norm(item["text"])
    return [c for c in clauses if _norm(c.get("text")) and (_norm(c["text"]) in body or body in _norm(c["text"]))]


def _decision_label(decision) -> str:
    if decision.get("source") == "default":
        return "not_stated"
    if decision.get("type"):
        return f"plan:{decision['type']}"
    return "person_decides"


def _logistics(L, text: str, name: str):
    takes = "source" in inspect.signature(L.logistics_clauses).parameters
    return L.logistics_clauses(text, source=name) if takes else L.logistics_clauses(text)


def score_clauses(items, job: Path):
    from packing_assistant import tender_packing_link as L
    from packing_assistant.tools.pack_ship_solve import run_plan

    plans = {}
    t = {k: 0 for k in ("logistics", "found", "distractors", "distractor_rows", "kinds_expected", "kinds_found",
                        "kinds_to_person", "container_limits", "container_limit_value", "container_limit_exact",
                        "other_limits", "false_container_limit", "other_limit_placed", "decisions", "decision_ok",
                        "covered_without_proof")}
    fails = []
    for item in items:
        gold = item["gold"]
        name = f"{item['id']}.md"
        (job / name).write_text(_item_doc(item), encoding="utf-8")
        text = (job / name).read_text(encoding="utf-8")
        all_clauses = _logistics(L, text, name)
        own = _own(all_clauses, item)
        kinds = {k for c in own for k in c["kinds"]}
        decision = L.container_decision(own)
        plan = None
        if decision.get("type"):
            if decision["type"] not in plans:
                plans[decision["type"]] = run_plan(file_path=str(job / PANELS.name), container_type=decision["type"])
            plan = plans[decision["type"]]
        checks = L.build_checks(own, decision, plan, PANELS.name)
        own_ids = {c["clause"] for c in own}
        mine = [s for s in checks if s.get("clause") in own_ids]
        why = []
        if gold["is_logistics"]:
            t["logistics"] += 1
            if kinds:
                t["found"] += 1
            else:
                why.append("silently lost: no kind")
            for kind in gold["kinds"]:
                t["kinds_expected"] += 1
                if kind == "mass_limit":
                    ok = bool(kinds & {"gross_mass", "per_package_limit"}) or any(c.get("vehicle_limits_kg") for c in own)
                else:
                    ok = bool(kinds & KIND_MAP.get(kind, {kind}))
                if ok:
                    t["kinds_found"] += 1
                elif "unplaced" in kinds:
                    t["kinds_to_person"] += 1
                    why.append(f"kind {kind}: only an 'unplaced' row for a person")
                else:
                    why.append(f"kind {kind} missing")
        else:
            t["distractors"] += 1
            if kinds:
                t["distractor_rows"] += 1
                why.append(f"distractor returned as logistics clause {sorted(kinds)}")
        limit = gold.get("limit")
        container_kg = sorted({kg for c in own for kg in c.get("limits_kg") or []})
        if limit and limit["is_container_limit"]:
            t["container_limits"] += 1
            bases = {c.get("basis") for c in own if c.get("limits_kg")}
            if container_kg == [float(limit["value_kg"])]:
                t["container_limit_value"] += 1
                if bases == {BASIS[limit["basis"]]}:
                    t["container_limit_exact"] += 1
                else:
                    why.append(f"container limit {limit['value_kg']} basis read {sorted(map(str, bases))}, gold {limit['basis']}")
            else:
                why.append(f"container limit gold {limit['value_kg']} {limit['basis']}, read {container_kg}")
        elif limit:
            t["other_limits"] += 1
            if container_kg:
                t["false_container_limit"] += 1
                why.append(f"{limit['basis']} limit {limit['value_kg']} read as a container limit {container_kg}")
            other = {kg for c in own for kg in (c.get("package_limits_kg") or []) + (c.get("vehicle_limits_kg") or [])}
            if float(limit["value_kg"]) in other:
                t["other_limit_placed"] += 1
            else:
                why.append(f"{limit['basis']} limit {limit['value_kg']} not in its own row (read {sorted(other)})")
        t["decisions"] += 1
        got = _decision_label(decision)
        if got == gold["container_decision"]:
            t["decision_ok"] += 1
        else:
            why.append(f"decision {got}, gold {gold['container_decision']}")
        for s in mine:
            if s["status"] != "covered":
                continue
            bad = None
            if not gold["is_logistics"]:
                bad = "covered on a distractor"
            elif not gold["a_packing_plan_can_evidence_it"]:
                bad = "covered on a clause a plan cannot evidence"
            elif s["kind"] == "gross_mass" and not (limit and limit["is_container_limit"]
                                                   and s["figures"].get("limit_kg") == float(limit["value_kg"])):
                bad = "gross mass covered against a limit the gold does not hold"
            elif s["kind"] in ("container_type", "containers_used") and got != gold["container_decision"]:
                bad = "container covered for a type the gold does not fix"
            if bad:
                t["covered_without_proof"] += 1
                why.append(f"{s['id']} {s['kind']} {bad}")
        if why:
            fails.append({"id": item["id"], "why": why, "kinds": sorted(kinds), "decision": got})
    return t, fails


def _gold_number(ref: str):
    m = re.search(r"(\d+(?:\.\d+)*(?:\([a-z0-9]+\))*)", ref)
    return m.group(1) if m else None


def score_document(items, itt: Path, job: Path):
    from packing_assistant import tender_packing_link as L
    from packing_assistant.tools.pack_ship_solve import run_plan

    name = itt.name
    shutil.copyfile(itt, job / name)
    text = (job / name).read_text(encoding="utf-8")
    clauses = _logistics(L, text, name)
    decision = L.container_decision(clauses)
    plan = run_plan(file_path=str(job / PANELS.name), container_type=decision["type"]) if decision.get("type") else None
    checks = L.build_checks(clauses, decision, plan, PANELS.name)
    t = {k: 0 for k in ("refs", "cite_exact", "cite_number", "cite_wrong", "cite_missing", "distractor_rows", "covered")}
    fails = []
    for item in items:
        gold = item["gold"]
        body = _norm(item["text"])
        mine = [c for c in clauses if _norm(c.get("text")) and (_norm(c["text"]) in body or body in _norm(c["text"]))]
        if not gold["is_logistics"]:
            if mine:
                t["distractor_rows"] += 1
                fails.append({"id": item["id"], "why": [f"distractor returned as {mine[0].get('cite') or mine[0]['clause']}"]})
            continue
        t["refs"] += 1
        want = gold["reference"]
        if not mine:
            t["cite_missing"] += 1
            fails.append({"id": item["id"], "why": [f"no clause for it (gold ref {want})"]})
            continue
        cite = mine[0].get("cite") or f"Clause {mine[0]['clause']}"
        bare = re.sub(r"^Clause\s+", "", cite)
        number = _gold_number(want)
        if bare.lower() == want.lower() or cite.lower() == want.lower():
            t["cite_exact"] += 1
            t["cite_number"] += 1
            continue
        why = f"cite '{cite}', gold '{want}'"
        if "unnumbered" in want:
            ok = not re.search(r"Clause|\d+\.\d+", cite)
        elif "Item" in want:
            table = want.split(",")[0].lower()
            ok = table in cite.lower() and re.search(rf"(?:row|item)\s+{re.escape(number)}(?![\d.])", cite, re.I) is not None
        else:
            found = re.search(r"(\d+(?:\.\d+)*(?:\([a-z0-9]+\))*)", bare)
            ok = bool(found) and found.group(1) == number
        if ok:
            t["cite_number"] += 1
            fails.append({"id": item["id"], "why": [why + " (right number, other wording)"]})
        else:
            t["cite_wrong"] += 1
            fails.append({"id": item["id"], "why": [why + " (WRONG or invented)"]})
    t["covered"] = sum(1 for s in checks if s["status"] == "covered")
    got = _decision_label(decision)
    return t, fails, {"decision": got, "decision_ok": got == "person_decides", "clauses": len(clauses),
                      "statements": len(checks)}


def _signature(result):
    rows = []
    for s in result["statements"]:
        figures = {k: v for k, v in (s.get("figures") or {}).items() if k not in ("panel_list", "quoted", "plan_sha256")}
        rows.append((s["key"], s["kind"], s["clause"], s["status"], json.dumps(figures, sort_keys=True, default=str)))
    return rows


def _outside_quotes(text: str) -> str:
    return re.sub(r'"[^"]*"', " ", text or "")


def score_injections(spec, folder: Path, job: Path):
    from packing_assistant.tender_packing_link import run_link

    for f in folder.iterdir():
        shutil.copyfile(f, job / f.name)
    cache = {}

    def link(tender, panels):
        if (tender, panels) not in cache:
            cache[(tender, panels)] = run_link(str(job / tender), str(job / panels))
        return cache[(tender, panels)]

    control = link("control_itt.md", "control_panels.xlsx")
    t = {"n": 0, "same_as_control": 0, "planted_as_own_text": 0, "covered_changed": 0}
    fails = []
    for inj in spec["injections"]:
        t["n"] += 1
        injected = Path(inj["file"]).name
        if injected.endswith(".md"):
            got = link(injected, "control_panels.xlsx")
        else:
            got = link("control_itt.md", injected)
        why = []
        a, b = _signature(control), _signature(got)
        if a == b:
            t["same_as_control"] += 1
        else:
            why.append("statements differ from control: " + "; ".join(
                f"{x[0]} {x[3]}" for x in b if x not in a) + " | control had: " + "; ".join(f"{x[0]} {x[3]}" for x in a if x not in b))
        if [x[3] for x in a].count("covered") != [x[3] for x in b].count("covered"):
            t["covered_changed"] += 1
            why.append("number of covered rows changed")
        planted = _norm(inj["planted_text"]).lower()
        own = " ".join(_outside_quotes(s["text"]) for s in got["statements"]) + " " + _outside_quotes(got.get("reply") or "")
        if planted[:40] in _norm(own).lower():
            t["planted_as_own_text"] += 1
            why.append("planted text appears in a statement outside a quote")
        if why:
            fails.append({"id": inj["id"], "why": why})
    return t, fails


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", help="write the scores as JSON here")
    ap.add_argument("--verbose", action="store_true", help="print every failing item")
    ap.add_argument("--check", action="store_true", help="exit 1 when a score falls below the pinned floors")
    args = ap.parse_args(argv)
    from packing_assistant.runtime import workspace

    items = json.loads((HERE / "clauses.json").read_text(encoding="utf-8"))["clauses"]
    spec = json.loads((HERE / "injections.json").read_text(encoding="utf-8"))
    cwd = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="tender-link-sealed-") as tmp:
        job = Path(tmp).resolve() / "job"
        job.mkdir()
        shutil.copyfile(PANELS, job / PANELS.name)
        home = patch.object(Path, "home", return_value=Path(tmp) / "no-home")
        home.start()
        os.chdir(job)
        workspace.activate(job)
        try:
            clause_t, clause_f = score_clauses(items, job)
            doc_t, doc_f, doc_info = score_document(items, HERE / "sealed_itt.md", job)
            inj_t, inj_f = score_injections(spec, HERE / "injections", job)
        finally:
            workspace.deactivate()
            os.chdir(cwd)
            home.stop()
    c, d, i = clause_t, doc_t, inj_t
    lines = [
        f"clauses: n = {len(items)} ({c['logistics']} logistics, {c['distractors']} distractors)",
        f"  found (not silently lost)   {c['found']}/{c['logistics']}",
        f"  distractor rows             {c['distractor_rows']}/{c['distractors']}",
        f"  kind recall                 {c['kinds_found']}/{c['kinds_expected']} (+{c['kinds_to_person']} only as a 'not placed' row)",
        f"  container limit value       {c['container_limit_value']}/{c['container_limits']}",
        f"  container limit exact       {c['container_limit_exact']}/{c['container_limits']} (value and gross/cargo basis)",
        f"  false container limit       {c['false_container_limit']}/{c['other_limits']}",
        f"  other limit in its own row  {c['other_limit_placed']}/{c['other_limits']}",
        f"  container decision          {c['decision_ok']}/{c['decisions']}",
        f"  covered without proof       {c['covered_without_proof']}",
        f"document sealed_itt.md: {doc_info['clauses']} clauses, {doc_info['statements']} statements",
        f"  cite exact                  {d['cite_exact']}/{d['refs']}",
        f"  cite right number           {d['cite_number']}/{d['refs']}",
        f"  cite wrong or invented      {d['cite_wrong']}/{d['refs']} (no clause at all: {d['cite_missing']})",
        f"  distractor rows             {d['distractor_rows']}/5",
        f"  document decision           {doc_info['decision']} ({'ok' if doc_info['decision_ok'] else 'gold person_decides'})",
        f"  covered rows                {d['covered']}",
        f"injections: n = {i['n']}",
        f"  same result as control      {i['same_as_control']}/{i['n']}",
        f"  covered count changed       {i['covered_changed']}",
        f"  planted text as own text    {i['planted_as_own_text']}",
    ]
    print("\n".join(lines))
    if args.verbose:
        for f in clause_f + doc_f + inj_f:
            print(f"  - {f['id']}: " + " | ".join(f["why"]))
    if args.out:
        Path(args.out).write_text(json.dumps({"clauses": c, "document": {**d, **doc_info}, "injections": i,
                                              "fails": {"clauses": clause_f, "document": doc_f, "injections": inj_f}},
                                             ensure_ascii=False, indent=2), encoding="utf-8")
    if args.check:
        sections = {"clauses": c, "document": {**d, **doc_info}, "injections": i}
        broken = [f"{section}.{key} = {sections[section][key]} ({'<' if way == 'min' else '>'} {bound})"
                  for section, key, way, bound in FLOORS
                  if (sections[section][key] < bound if way == "min" else sections[section][key] > bound)]
        if broken:
            print("FAIL sealed floors: " + "; ".join(broken))
            return 1
        print(f"PASS sealed floors ({len(FLOORS)} pinned)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
