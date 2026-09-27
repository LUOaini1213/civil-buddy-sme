#!/usr/bin/env python3
"""Scores the tender <-> packing link's clause reader on a labelled set (default: test/benchmarks/tender_link/dev.json).

Each case's text is written to <id>.md and read as a tender. The reader's clauses, its container decision and the
statements it would write under the SYNTHETIC demo panel list (examples/facade-demo/facade_panels.xlsx, planned once
per container type) are compared with the labels. Printed and, with --out, written as JSON:

  kind recall          expected kinds found / expected kinds
  extra kinds          kinds found that the label neither expects nor accepts ("unplaced" rows are counted apart)
  silently lost        expected kinds with no row at all - neither the kind nor a "not placed" row for a person
  container limit      per-container mass limit read exactly / labelled
  false container lim  a per-container limit where the label has none (a stillage, crate, truck ...) or a wrong value
  package limit        per-package limit (stillage / crate / lift / piece) read / labelled
  basis                gross / cargo read as labelled / labelled with a definite basis
  container decision   type (or "a person decides") as labelled / labelled
  cites                statements citing the clause the way the label writes it / labelled
  invalid cites        statements that cite "Clause L<n>" - a clause that does not exist
  false covered        "covered" statements for a kind the plan cannot evidence, or a mass limit the label does not hold
  unplaced rows        "not placed" rows for a person (on cases labelled with no kind, a false alarm)

No model and no network. Usage: python scripts/bench_tender_link.py [--set PATH] [--out PATH]
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
for _key in [k for k in os.environ if k.endswith("_API_KEY")] + ["CIVIL_SANDBOX", "CIVIL_APPROVAL", "CIVIL_JOB_ROOT"]:
    os.environ.pop(_key, None)
os.environ["CIVIL_AGENT_MODE"] = "steps"

DEFAULT_SET = ROOT / "test" / "benchmarks" / "tender_link" / "dev.json"
PANELS = ROOT / "examples" / "facade-demo" / "facade_panels.xlsx"
EVIDENCED = {"container_type", "containers_used", "gross_mass", "crate_structure", "logistics_plan_submission"}
INVALID_CITE = re.compile(r"Clause L\d+\b")


def _cite(clause) -> str:
    return clause.get("cite") or f"Clause {clause['clause']}"


def score(cases, job: Path):
    from packing_assistant import tender_packing_link as L
    from packing_assistant.tools.pack_ship_solve import run_plan

    takes_source = "source" in inspect.signature(L.logistics_clauses).parameters
    plans = {}
    rows = []
    for case in cases:
        name = f"{case['id']}.md"
        (job / name).write_text(case["text"] + "\n", encoding="utf-8")
        text = (job / name).read_text(encoding="utf-8")
        clauses = L.logistics_clauses(text, source=name) if takes_source else L.logistics_clauses(text)
        decision = L.container_decision(clauses)
        plan = None
        if decision.get("type"):
            if decision["type"] not in plans:
                plans[decision["type"]] = run_plan(file_path=str(job / PANELS.name), container_type=decision["type"])
            plan = plans[decision["type"]]
        checks = L.build_checks(clauses, decision, plan, PANELS.name)
        rows.append({"case": case, "clauses": clauses, "decision": decision, "checks": checks})
    return rows


def judge(rows):
    t = {k: 0 for k in ("kinds_expected", "kinds_found", "extra_kinds", "silently_lost", "limit_labelled", "limit_ok",
                        "false_container_limit", "package_labelled", "package_ok", "basis_labelled", "basis_ok",
                        "decision_labelled", "decision_ok", "cite_labelled", "cite_ok", "invalid_cites", "false_covered",
                        "unplaced_rows", "unplaced_on_no_kind_cases")}
    per_case = []
    for row in rows:
        case, clauses, decision, checks = row["case"], row["clauses"], row["decision"], row["checks"]
        found = [k for c in clauses for k in c["kinds"]]
        unplaced = [c for c in clauses if "unplaced" in c["kinds"]]
        expected = case.get("kinds") or []
        ok_extra = set(expected) | set(case.get("optional_kinds") or []) | {"unplaced"}
        miss, notes = [], []
        for kind in expected:
            t["kinds_expected"] += 1
            if kind in found:
                t["kinds_found"] += 1
            else:
                miss.append(kind)
                if kind == "unplaced" or not unplaced:
                    t["silently_lost"] += 1
                    notes.append(f"LOST {kind}")
        extra = sorted({k for k in found if k not in ok_extra})
        t["extra_kinds"] += len(extra)
        t["unplaced_rows"] += len(unplaced)
        if not expected and unplaced:
            t["unplaced_on_no_kind_cases"] += 1
        limits = sorted({float(x) for c in clauses if "gross_mass" in c["kinds"] for x in (c.get("limits_kg") or [])})
        bases = sorted({c.get("basis") for c in clauses if "gross_mass" in c["kinds"]})
        if "container_limit_kg" in case:
            want = case["container_limit_kg"]
            if want is None:
                if limits:
                    t["false_container_limit"] += 1
                    notes.append(f"FALSE container limit {limits}")
            else:
                t["limit_labelled"] += 1
                if limits == [float(want)]:
                    t["limit_ok"] += 1
                else:
                    notes.append(f"limit {limits} != {want}")
                    if limits:
                        t["false_container_limit"] += 1
        if case.get("basis") and case.get("container_limit_kg"):
            t["basis_labelled"] += 1
            if bases == [case["basis"]]:
                t["basis_ok"] += 1
            else:
                notes.append(f"basis {bases} != {case['basis']}")
        if case.get("package_limit_kg") is not None:
            t["package_labelled"] += 1
            got = sorted({float(x) for c in clauses if "per_package_limit" in c["kinds"] for x in (c.get("package_limits_kg") or [])})
            if got == [float(case["package_limit_kg"])]:
                t["package_ok"] += 1
            else:
                notes.append(f"package limit {got} != {case['package_limit_kg']}")
        if "type" in case:
            t["decision_labelled"] += 1
            want = case["type"]
            if want == "person":
                ok = decision.get("type") is None and decision.get("source") == "tender_clause"
                if ok and case.get("named"):
                    ok = all(code in str(decision.get("named") or "") for code in case["named"])
            elif want == "default":
                ok = decision.get("source") == "default"
            else:
                ok = decision.get("type") == want and decision.get("source") == "tender_clause"
            t["decision_ok"] += ok
            if not ok:
                notes.append(f"decision {decision.get('type')}/{decision.get('source')} != {want}")
        for kind, want in (case.get("cite") or {}).items():
            t["cite_labelled"] += 1
            carrying = [c for c in clauses if kind in c["kinds"]]
            if carrying and _cite(carrying[0]) == want:
                t["cite_ok"] += 1
            else:
                notes.append(f"cite {kind}: {[_cite(c) for c in carrying]} != {want!r}")
        for s in checks:
            if INVALID_CITE.search(s["text"] or ""):
                t["invalid_cites"] += 1
                notes.append(f"INVALID cite in {s['id']}")
            if s["status"] == "covered":
                bad = s["kind"] not in EVIDENCED
                if s["kind"] == "gross_mass":
                    lim = (s.get("figures") or {}).get("limit_kg")
                    bad = bad or case.get("container_limit_kg") in (None,) or lim != case.get("container_limit_kg")
                if bad:
                    t["false_covered"] += 1
                    notes.append(f"FALSE covered {s['id']} {s['kind']}")
        per_case.append({"id": case["id"], "origin": case["origin"], "found": found, "missing": miss, "extra": extra,
                         "limits": limits, "bases": bases, "decision": [decision.get("type"), decision.get("source")],
                         "cites": {k: [_cite(c) for c in clauses if k in c["kinds"]] for k in sorted(set(found))},
                         "statements": [[s["id"], s["kind"], s["clause"], s["status"]] for s in checks], "notes": notes})
    return t, per_case


def summary_lines(t):
    def frac(a, b):
        return f"{t[a]}/{t[b]}"
    return [f"kind recall          {frac('kinds_found', 'kinds_expected')}",
            f"extra kinds          {t['extra_kinds']}",
            f"silently lost        {t['silently_lost']}",
            f"container limit      {frac('limit_ok', 'limit_labelled')}",
            f"false container lim  {t['false_container_limit']}",
            f"package limit        {frac('package_ok', 'package_labelled')}",
            f"basis                {frac('basis_ok', 'basis_labelled')}",
            f"container decision   {frac('decision_ok', 'decision_labelled')}",
            f"cites                {frac('cite_ok', 'cite_labelled')}",
            f"invalid cites        {t['invalid_cites']}",
            f"false covered        {t['false_covered']}",
            f"unplaced rows        {t['unplaced_rows']} (on cases labelled with no kind: {t['unplaced_on_no_kind_cases']})"]


def run(set_path: Path = DEFAULT_SET):
    from packing_assistant.runtime import workspace

    data = json.loads(Path(set_path).read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="tender-link-bench-") as tmp:
        job = Path(tmp).resolve() / "job"
        job.mkdir()
        shutil.copyfile(PANELS, job / PANELS.name)
        cwd = Path.cwd()
        home = patch.object(Path, "home", return_value=Path(tmp) / "no-home")
        home.start()
        os.chdir(job)
        workspace.activate(job)
        try:
            rows = score(data["cases"], job)
        finally:
            workspace.deactivate()
            os.chdir(cwd)
            home.stop()
    totals, per_case = judge(rows)
    return {"set": str(Path(set_path).name), "n_cases": len(data["cases"]), "totals": totals, "cases": per_case}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--set", default=str(DEFAULT_SET))
    parser.add_argument("--out", default="")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    result = run(Path(args.set))
    if args.verbose:
        for c in result["cases"]:
            if c["notes"]:
                print(c["id"], "|", "; ".join(c["notes"]))
    print(f"{result['set']}: {result['n_cases']} cases")
    for line in summary_lines(result["totals"]):
        print("  " + line)
    if args.out:
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
