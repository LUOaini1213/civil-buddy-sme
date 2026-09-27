#!/usr/bin/env python3
"""Score where 30 English requests are routed: the tender <-> packing link, packing only, tender only, or chat.

    python test/benchmarks/english_requests/score.py            # score and every miss
    python test/benchmarks/english_requests/score.py --check    # CI floor (the first-run AFTER score, see README)
    python test/benchmarks/english_requests/score.py --data F   # score another file of the same shape
    python test/benchmarks/english_requests/score.py --root R   # score the code in another checkout R

The set (sealed.json next to this file) is HELD-OUT: written blind on 2026-09-26 21:02 SGT by an agent that had
not read the router or its tests, before PR #67 changed the router, and never used to tune it. See README.md.

Only public entry points are called: task_router.route_task and task_router.wants_link. What the product does with
a request, per the steps-mode agent (agent_loop._plan_calls runs tender.packing_link for bid-parse + wants_link):
    chat    route_task's intent is chat (nothing runs)
    link    wants_link, and the route is bid-parse with intent run (the linked run starts)
    pack    otherwise, pack-ship is among the routed posts
    tender  otherwise, a bid post (bid-*) or the tender-review workflow is routed
    other   anything else (printed with the posts it went to)
Gold is the file's own `gold.route`; nothing here changes it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "sealed.json"
# Pinned to the first scored run of PR #67's head, 986a3df (see README): 19 of 30, no request wrongly linked, no
# question run. A floor against regression, not a target: the set must not be used to tune the router.
FLOOR_CORRECT = 19
FLOOR_FALSE_LINK = 0
FLOOR_FALSE_RUN = 0


def classify(text: str, route_task, wants_link) -> tuple[str, dict]:
    route = route_task(text)
    ids = list(route.get("expert_ids") or [])
    if route.get("intent") == "chat":
        return "chat", route
    if wants_link(text) and ids == ["bid-parse"] and route.get("intent") == "run":
        return "link", route
    if "pack-ship" in ids:
        return "pack", route
    if route.get("workflow") == "tender-review" or any(eid.startswith("bid-") for eid in ids):
        return "tender", route
    return "other", route


def score(requests: list[dict], route_task, wants_link) -> dict:
    rows = []
    for item in requests:
        got, route = classify(item["text"], route_task, wants_link)
        want = item["gold"]["route"]
        rows.append({"id": item["id"], "text": item["text"], "want": want, "got": got, "ok": got == want,
                     "posts": route.get("expert_ids"), "intent": route.get("intent")})
    by = {}
    for want in ("link", "pack", "tender", "chat"):
        sub = [r for r in rows if r["want"] == want]
        by[want] = (sum(r["ok"] for r in sub), len(sub))
    return {"n": len(rows), "correct": sum(r["ok"] for r in rows), "rows": rows, "by": by,
            # a request that is not a link request but starts the linked run (writes a link record)
            "false_link": sum(r["got"] == "link" and r["want"] != "link" for r in rows),
            # a chat request (question / hypothetical) that runs anything
            "false_run": sum(r["want"] == "chat" and r["got"] != "chat" for r in rows)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(DATA))
    ap.add_argument("--root", default=str(HERE.parents[2]), help="the checkout whose router is scored")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    sys.path[:0] = [str(root)]
    os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")
    from packing_assistant.runtime.task_router import route_task, wants_link

    data = json.loads(Path(args.data).read_text(encoding="utf-8"))
    s = score(data["requests"], route_task, wants_link)
    if args.json:
        print(json.dumps(s, ensure_ascii=False, indent=1))
        return 0
    acc = s["correct"] / s["n"]
    parts = "  ".join(f"{k} {c}/{n}" for k, (c, n) in s["by"].items())
    print(f"english_requests n={s['n']}  accuracy {acc:.3f} ({s['correct']}/{s['n']})  {parts}  "
          f"false_link {s['false_link']}  false_run {s['false_run']}")
    for r in s["rows"]:
        if not r["ok"]:
            print(f"    {r['id']} want {r['want']:<6} got {r['got']:<6} posts={r['posts']} intent={r['intent']} :: {r['text']}")
    if args.check:
        ok = s["correct"] >= FLOOR_CORRECT and s["false_link"] <= FLOOR_FALSE_LINK and s["false_run"] <= FLOOR_FALSE_RUN
        print(("PASS" if ok else "FAIL") + f" english_requests floor (correct >= {FLOOR_CORRECT}/{s['n']}, "
              f"false_link <= {FLOOR_FALSE_LINK}, false_run <= {FLOOR_FALSE_RUN})")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
