#!/usr/bin/env python3
"""Scores the panel-list reader on the SEALED held-out panel lists in this folder.

The set (panel_lists/*.xlsx + gold.json) was written blind on 2026-09-26 by an agent that had not read the
table_mapper / pack_ship rules; see README.md. This scorer calls only public entry points:
packing_assistant.tools.pack_ship_solve.run_plan (the pack route) and packing_assistant.tender_packing_link.run_link
(the linked run, with the SYNTHETIC control ITT of tender_link_sealed, which names 40HQ). It never changes the gold.

Per list (6: 3 that must plan, 3 that must stop for a person):
  exact      plan lists: a solver plan whose conservation record holds exactly the gold pieces and kg (+-1 kg).
             stop lists: no finished plan, and the rows sent to a person are exactly the gold stop rows (by mark).
  safe       plan lists: a correct plan, or no plan with a stated reason (never a plan with wrong pieces or kg).
             stop lists: no finished plan.
  row named  stop lists: the question sent to a person names the gold sheet row (sheet_row) - AFTER #69 only.
  link       run_link on the same list: no statement reads "covered" unless the pack-route result is a correct plan,
             and for a stop list the reply names the gold mark.

No model, no network, a few seconds, deterministic.
Usage: python test/benchmarks/panel_lists_sealed/score_sealed.py [--out PATH] [--verbose] [--check]
--check exits 1 when a score falls below FLOORS: the first scored run of PR #69 (2026-09-27), see README.md.
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
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

LISTS = HERE / "panel_lists"
ITT = Path(os.environ.get("CIVIL_SEALED_ITT") or ROOT / "test" / "benchmarks" / "tender_link_sealed" / "injections" / "control_itt.md")
# the AFTER score of the first scored run (PR #69 head 77d6122, 2026-09-27): (key, "min" | "max", bound)
FLOORS = (
    ("exact", "min", 4),
    ("safe", "min", 5),
    ("row_named", "min", 2),
    ("link_ok", "min", 5),
    ("wrong_plan", "max", 1),
    ("covered_without_plan", "max", 0),
)


def _run_plan(path: Path):
    from packing_assistant.tools.pack_ship_solve import run_plan

    kwargs = {"file_path": str(path), "container_type": "40HQ"}
    if "lang" in inspect.signature(run_plan).parameters:
        kwargs["lang"] = "en"
    return run_plan(**kwargs)


def _run_link(path: Path, job: Path):
    from packing_assistant.tender_packing_link import run_link

    return run_link(str(job / ITT.name), str(path), now="2026-09-27T00:00:00+00:00")


def _matches(entry: dict, mark: str) -> bool:
    text = f"{entry.get('id') or ''} {entry.get('name') or ''}".lower()
    return mark.lower() in text


def score_one(item: dict, job: Path) -> dict:
    path = job / item["file"]
    plan = _run_plan(path)
    source = plan.get("source")
    cons = plan.get("conservation") or {}
    pieces, kg = cons.get("pieces_in"), cons.get("kg_in")
    solved = bool(plan.get("ok") and source == "solver")
    asks = [r for r in plan.get("needs_human") or [] if isinstance(r, dict)]
    res = {"file": item["file"], "expected": item["expected"], "source": source, "error": plan.get("error"),
           "pieces": pieces, "kg": kg, "containers": plan.get("containers_used") if solved else None,
           "can_fit": plan.get("can_fit") if solved else None,
           "asks": [{k: a.get(k) for k in ("id", "name", "reason", "sheet_row")} for a in asks]}
    fails = []
    if item["expected"] == "plan":
        right = solved and pieces == item["pieces"] and kg is not None and abs(float(kg) - item["total_kg"]) <= 1
        res["exact"] = right
        res["wrong_plan"] = solved and not right
        res["safe"] = right or (not solved and bool(asks or plan.get("detail") or plan.get("error")))
        if not right:
            fails.append(f"want plan {item['pieces']} pcs / {item['total_kg']} kg; got {source} {pieces} pcs / {kg} kg"
                         + (f", asks {[a.get('id') or a.get('name') for a in asks]}" if asks else ""))
    else:
        marks = [s["mark"] for s in item["stop_rows"]]
        hit = [a for a in asks if any(_matches(a, m) for m in marks)]
        extra = [a for a in asks if not any(_matches(a, m) for m in marks)]
        res["exact"] = not solved and bool(hit) and not extra
        res["wrong_plan"] = solved
        res["safe"] = not solved
        res["row_named"] = any(a.get("sheet_row") == s["row"] for a in hit for s in item["stop_rows"])
        if solved:
            fails.append(f"want stop at {marks}; got a finished plan of {pieces} pcs / {kg} kg")
        elif not hit:
            fails.append(f"want stop at {marks}; got {source} ({plan.get('error')}) asking about "
                         f"{[a.get('id') or a.get('name') for a in asks]}")
        elif extra:
            fails.append(f"stop at {marks} plus rows the gold does not stop: {[a.get('id') or a.get('name') for a in extra]}")
    link = _run_link(path, job)
    rec = link.get("record") or {}
    covered = [s for s in rec.get("statements") or [] if s.get("status") == "covered"]
    res["covered"] = len(covered)
    res["covered_without_plan"] = 0 if res["exact"] and item["expected"] == "plan" else len(covered)
    reply = str(link.get("reply") or "")
    if item["expected"] == "plan":
        res["link_ok"] = res["covered_without_plan"] == 0 and (not res["exact"] or rec.get("plan") is not None)
    else:
        res["link_ok"] = not covered and any(s["mark"].lower() in reply.lower() for s in item["stop_rows"])
        if not res["link_ok"]:
            fails.append("link reply does not name " + ", ".join(s["mark"] for s in item["stop_rows"])
                         + (f" ({len(covered)} covered)" if covered else ""))
    res["reply"] = reply
    res["failures"] = fails
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    gold = json.loads((LISTS / "gold.json").read_text(encoding="utf-8"))
    from packing_assistant.runtime import workspace

    cwd = Path.cwd()
    # the linked run reads only inside the job folder (the #61 read sandbox), so the lists and the ITT are copied into
    # a fresh one, the way a person's job folder holds them
    with tempfile.TemporaryDirectory(prefix="panel-lists-sealed-") as tmp:
        job = Path(tmp).resolve() / "job"
        job.mkdir()
        for it in gold["files"]:
            shutil.copyfile(LISTS / it["file"], job / it["file"])
        shutil.copyfile(ITT, job / ITT.name)
        home = patch.object(Path, "home", return_value=Path(tmp) / "no-home")
        home.start()
        os.chdir(job)
        workspace.activate(job)
        try:
            items = [score_one(it, job) for it in gold["files"]]
        finally:
            workspace.deactivate()
            os.chdir(cwd)
            home.stop()
    n = len(items)
    n_stop = sum(1 for it in items if it["expected"] == "stop")
    summary = {
        "n": n,
        "exact": sum(1 for it in items if it["exact"]),
        "safe": sum(1 for it in items if it["safe"]),
        "row_named": sum(1 for it in items if it.get("row_named")),
        "n_stop": n_stop,
        "link_ok": sum(1 for it in items if it["link_ok"]),
        "wrong_plan": sum(1 for it in items if it["wrong_plan"]),
        "covered_without_plan": sum(it["covered_without_plan"] for it in items),
    }
    out = {"repo": str(ROOT), "summary": summary,
           "items": [{k: v for k, v in it.items() if args.verbose or k != "reply"} for it in items]}
    text = json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))
    for it in items:
        mark = "ok  " if it["exact"] else "FAIL"
        print(f"{mark} {it['file']}: " + ("; ".join(it["failures"]) or f"{it['source']} {it['pieces']} pcs / {it['kg']} kg"))
    if args.check:
        bad = [f"{k} {summary[k]} {'<' if op == 'min' else '>'} {b}" for k, op, b in FLOORS
               if (summary[k] < b if op == "min" else summary[k] > b)]
        if bad:
            print("below floor: " + "; ".join(bad))
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
