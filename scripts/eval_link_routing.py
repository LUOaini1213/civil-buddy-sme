#!/usr/bin/env python3
"""Score which requests start the tender <-> packing link (task_router.wants_link + route_task).

    python scripts/eval_link_routing.py            # route-level score on test/benchmarks/link_routing/dev.json
    python scripts/eval_link_routing.py --show     # and every miss
    python scripts/eval_link_routing.py --e2e      # also run each request that names only the SYNTHETIC demo files
                                                   # through civil.run_task (steps mode) in a fresh demo job
    python scripts/eval_link_routing.py --check    # CI floor on the DEV set

dev.json is a DEV set: written before the router change and used while building it, not held-out. Per request:
link = wants_link true and the route is bid-parse / run; chat = the route's intent is chat; other = wants_link false
(whatever post or intent the request then gets).
The --e2e column says whether the turn ran tender.packing_link and, for an English request, whether the reply has
any Chinese character in it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")

from packing_assistant.runtime.task_router import route_task, wants_link  # noqa: E402

DEV = ROOT / "test" / "benchmarks" / "link_routing" / "dev.json"
FLOOR = 0.95          # the DEV set was used to build the rule: this floor guards against a regression, it is not a score
_CJK = re.compile(r"[㐀-鿿]")
_DEMO_FILES = {"facade_itt_doc.md", "facade_panels.xlsx"}


def classify(text: str) -> str:
    route = route_task(text)
    if route["intent"] == "chat":
        return "chat"
    if wants_link(text):
        return "link" if (route["expert_ids"], route["intent"]) == (["bid-parse"], "run") else "link?"
    return "other"


def score(cases):
    rows = []
    for c in cases:
        got = classify(c["text"])
        # other = the request does not start the link, whichever post or intent it then gets
        ok = (got != "link" and not wants_link(c["text"])) if c["want"] == "other" else got == c["want"]
        rows.append({**c, "got": got, "ok": ok})

    def acc(sub):
        return (sum(r["ok"] for r in sub) / len(sub)) if sub else float("nan")
    out = {"n": len(rows), "accuracy": acc(rows), "rows": rows}
    for lang in ("en", "zh"):
        out[lang] = acc([r for r in rows if r["lang"] == lang])
    for want in ("link", "other", "chat"):
        out[want] = acc([r for r in rows if r["want"] == want])
    out["false_link"] = sum(r["got"] == "link" and r["want"] != "link" for r in rows)
    return out


def e2e(cases):
    os.environ["CIVIL_AGENT_MODE"] = "steps"
    import demo_facade
    from packing_assistant.civil import enter_workspace, run_task

    job = demo_facade.make_job(Path(tempfile.mkdtemp(prefix="link-routing-")) / "job")
    enter_workspace(str(job))
    rows = []
    for i, case in enumerate(cases):
        named = set(re.findall(r"[\w.-]+\.(?:xlsx|md|docx|pdf|csv)", case["text"]))
        if not named or not named <= _DEMO_FILES:
            continue
        out = run_task(case["text"], session_id=f"link-routing-{i}")
        reply = str(out.get("reply") or "")
        rows.append({**case, "ran_link": "tender.packing_link" in (out.get("tools_run") or []),
                     "skill": out.get("skill"), "ok": out.get("ok"), "cjk_reply": bool(_CJK.search(reply)),
                     "first_line": reply.strip().splitlines()[0][:140] if reply.strip() else ""})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--e2e", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    cases = json.loads(DEV.read_text(encoding="utf-8"))["cases"]
    s = score(cases)
    print(f"link_routing dev n={s['n']}  acc {s['accuracy']:.3f}  en {s['en']:.3f}  zh {s['zh']:.3f}  "
          f"link {s['link']:.3f}  other {s['other']:.3f}  chat {s['chat']:.3f}  false_link {s['false_link']}")
    if args.show or args.check:
        for r in s["rows"]:
            if not r["ok"]:
                print(f"    want {r['want']:<5} got {r['got']:<5} {r['text']}")
    if args.e2e:
        rows = e2e(cases)
        want_link = [r for r in rows if r["want"] == "link"]
        en = [r for r in rows if r["lang"] == "en"]
        print(f"e2e on the demo job: link ran for {sum(r['ran_link'] for r in want_link)} of {len(want_link)} link requests; "
              f"{sum(r['ran_link'] for r in rows if r['want'] != 'link')} of {len(rows) - len(want_link)} others ran it; "
              f"{sum(r['cjk_reply'] for r in en)} of {len(en)} English requests got Chinese in the reply")
        for r in rows:
            print(f"    [{r['want']:<5}] link={int(r['ran_link'])} cjk={int(r['cjk_reply'])} skill={r['skill']} :: {r['text'][:70]}"
                  f"\n              {r['first_line']}")
    if args.check:
        ok = s["accuracy"] >= FLOOR and s["false_link"] == 0
        print(("PASS" if ok else "FAIL") + f" link_routing dev floor (accuracy >= {FLOOR}, false_link 0)")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
