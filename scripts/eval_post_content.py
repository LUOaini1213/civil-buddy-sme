#!/usr/bin/env python3
"""Does what the user said end up in the right place of a post's deliverable?

The post scorecard (eval_post_scorecard.py) checks that a draft has its required bars and keeps its
placeholders. A draft can pass all of that and still be hollow: the request pasted whole into one
table cell, the other cells TBD. This measures the other half. Each case is a request a site person
would type, plus the facts in it and the field each belongs to; a fact counts only when it lands

  placed     in a short table cell or labelled line whose column header / row key / label matches
  misplaced  in a short structured spot, under the wrong header
  dumped     only inside a long cell or line (the sentence was pasted, not parsed)
  echo_only  only in the "用户原文" echo
  missing    nowhere

Offline, no model, no key: the writers are called through run_named_exclusive, the ToolEngine entry.

  python scripts/eval_post_content.py                      # dev set, table per post
  python scripts/eval_post_content.py --set heldout
  python scripts/eval_post_content.py --post warehouse -v  # per-fact detail
  python scripts/eval_post_content.py --show warehouse-01  # print the deliverable
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "test" / "benchmarks" / "post_content"
sys.path.insert(0, str(ROOT))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

SHORT = 40  # a cell or value longer than this is prose, not a field
ECHO_HEADING = re.compile(r"^#{1,6}\s*用户原文\s*$")
HEADING = re.compile(r"^#{1,6}\s")
LABELLED = re.compile(r"^\s*(?:[-*+]\s*)?(?:\*\*)?([^：:|*]{1,24}?)(?:\*\*)?\s*[：:]\s*(.+?)\s*$")
CLASSES = ("placed", "misplaced", "dumped", "echo_only", "missing")
_FIELD_ALIASES = {
    "Project": "项目名称", "Employer": "招标人", "Tender reference": "招标编号",
    "Jurisdiction": "辖区", "Contract duration": "工期", "Tender validity": "投标有效期",
    "Tender security": "投标保证金", "Submission deadline": "投标截止",
    "Registration / workhead": "注册资格/工作类别", "Warranty / defects liability": "缺陷责任期/质保期",
    "Evaluation method": "评标办法", "Qualifications": "资质", "Relevant experience": "类似业绩",
    "Project manager": "项目经理", "Price cap": "最高限价", "Quality standard": "质量标准",
}


def split_echo(md: str) -> tuple[str, str]:
    """(everything but the echo section, the echo section)."""
    body, echo, inside = [], [], False
    for line in md.splitlines():
        if ECHO_HEADING.match(line.strip()):
            inside = True
            continue
        if inside and HEADING.match(line):
            inside = False
        (echo if inside else body).append(line)
    return "\n".join(body), "\n".join(echo)


def _cells(line: str) -> list[str]:
    # post_facts.table_cell() escapes a user's "&" and "|" for Markdown; the Excel export and every Markdown
    # viewer undo that, and so does the reading here - the fact is "Housing & Development Board"
    import html

    return [html.unescape(c.strip()) for c in line.strip().strip("|").split("|")]


def spots(body: str) -> list[tuple[str, str]]:
    """Every structured spot as (what it is filed under, its text).

    A table cell is filed under its column header and, for key/value tables, under the row's first
    cell as well. A labelled line is filed under its label.
    """
    found: list[tuple[str, str]] = []
    header: list[str] | None = None
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("|") and line.endswith("|"):
            cells = _cells(line)
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c) and cells:
                continue
            if header is None:
                header = cells
                continue
            for index, cell in enumerate(cells):
                column = header[index] if index < len(header) else ""
                key = cells[0] if index else ""
                # Compare semantic field identity across presentations, not the UI language.
                # Never translate values or relax the short-cell / wrong-field checks.
                alias = _FIELD_ALIASES.get(key, "")
                found.append((f"{column} {key} {alias}".strip(), cell))
            continue
        header = None
        match = LABELLED.match(line)
        if match and not line.startswith("#"):
            found.append((match.group(1).strip(), match.group(2).strip()))
    return found


def classify(fact: dict, body: str, echo: str, structured: list[tuple[str, str]]) -> str:
    value, field = str(fact["value"]), re.compile(fact["field"])
    short = [(under, text) for under, text in structured if value in text and len(text) <= SHORT]
    if any(field.search(under) for under, _ in short):
        return "placed"
    if short:
        return "misplaced"
    if value in body:
        return "dumped"
    return "echo_only" if value in echo else "missing"


def pasted_cells(request: str, structured: list[tuple[str, str]]) -> int:
    """Structured spots that hold a run of the request instead of a field value."""
    probe = re.sub(r"\s+", "", request)
    count = 0
    for _, text in structured:
        flat = re.sub(r"\s+", "", text)
        if len(flat) > SHORT and any(flat[i:i + 16] in probe for i in range(0, max(1, len(flat) - 15), 4)):
            count += 1
    return count


def run_case(case: dict, out_root: Path) -> dict:
    from packing_assistant import expert_turn
    from packing_assistant.runtime import agent_loop

    with patch.object(expert_turn, "_OUT", out_root), patch.object(agent_loop, "_OUT", out_root):
        result = expert_turn.run_named_exclusive(
            case["tool"], {"text": case["request"], "confirm_ok": True, "session_id": "post-content-" + case["id"]})
    path = next((f.get("path") for f in result.get("files") or [] if str(f.get("name") or "").endswith(".md")), None)
    md = Path(path).read_text(encoding="utf-8") if path else ""
    body, echo = split_echo(md)
    structured = spots(body)
    verdicts = [{"value": f["value"], "field": f["field"], "class": classify(f, body, echo, structured)}
                for f in case["facts"]]
    forbidden = [w for w in case.get("forbidden", []) if w in body]
    return {"id": case["id"], "post": case["post"], "wrote": bool(md), "facts": verdicts,
            "pasted_cells": pasted_cells(case["request"], structured), "forbidden": forbidden, "md": md}


def load(name: str) -> list[dict]:
    data = json.loads((BENCH / f"{name}.json").read_text(encoding="utf-8"))
    return data["cases"] if isinstance(data, dict) else data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--set", default="cases", help="cases | heldout | <file stem in test/benchmarks/post_content>")
    parser.add_argument("--post", action="append", help="only these posts")
    parser.add_argument("--show", help="print one case's deliverable and stop")
    parser.add_argument("--json", help="write the full result here")
    parser.add_argument("--min-placed", type=float, default=0.0, help="fail below this overall placed rate")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    cases = [c for c in load(args.set) if not args.post or c["post"] in args.post]
    if args.show:
        cases = [c for c in cases if c["id"] == args.show]
    if not cases:
        print("no cases")
        return 2
    (ROOT / "output").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="post-content-", dir=ROOT / "output") as tmp:
        results = [run_case(case, Path(tmp)) for case in cases]
    if args.show:
        print(results[0]["md"])
        return 0

    posts: dict[str, dict] = {}
    for r in results:
        row = posts.setdefault(r["post"], {k: 0 for k in CLASSES} | {"facts": 0, "pasted_cells": 0, "forbidden": 0, "cases": 0})
        row["cases"] += 1
        row["pasted_cells"] += r["pasted_cells"]
        row["forbidden"] += len(r["forbidden"])
        for fact in r["facts"]:
            row["facts"] += 1
            row[fact["class"]] += 1
    print(f"{'post':18s} cases facts placed  rate  misplaced dumped echo missing pasted forbidden")
    for post, row in sorted(posts.items(), key=lambda kv: (kv[1]["placed"] / max(1, kv[1]["facts"]), kv[0])):
        print(f"{post:18s} {row['cases']:5d} {row['facts']:5d} {row['placed']:6d} {row['placed'] / max(1, row['facts']):5.2f} "
              f"{row['misplaced']:9d} {row['dumped']:6d} {row['echo_only']:4d} {row['missing']:7d} {row['pasted_cells']:6d} {row['forbidden']:9d}")
    total = {k: sum(row[k] for row in posts.values()) for k in (*CLASSES, "facts", "pasted_cells", "forbidden")}
    rate = total["placed"] / max(1, total["facts"])
    macro = sum(row["placed"] / max(1, row["facts"]) for row in posts.values()) / len(posts)
    print(f"\nposts {len(posts)}  cases {len(results)}  facts {total['facts']}  placed {total['placed']} ({rate:.3f} micro, {macro:.3f} macro)  "
          f"misplaced {total['misplaced']}  dumped {total['dumped']}  echo_only {total['echo_only']}  missing {total['missing']}  "
          f"pasted_cells {total['pasted_cells']}  forbidden {total['forbidden']}")
    if args.verbose:
        for r in results:
            for fact in r["facts"]:
                if fact["class"] != "placed":
                    print(f"  {r['id']}: {fact['class']:9s} {fact['value']!r} -> /{fact['field']}/")
            for word in r["forbidden"]:
                print(f"  {r['id']}: forbidden {word!r}")
    if args.json:
        Path(args.json).write_text(json.dumps({"total": total, "micro": rate, "macro": macro, "posts": posts,
                                               "results": [{k: v for k, v in r.items() if k != "md"} for r in results]},
                                              ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if rate >= args.min_placed and not total["forbidden"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
