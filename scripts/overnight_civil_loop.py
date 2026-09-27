#!/usr/bin/env python3
"""Civil Buddy overnight eval → optional one knife → report until AUTONOMY_END_TS.

A fetched title is source evidence, not a verified current tax rate. Does not change 部分合格.
Default: evaluate only (OVERNIGHT_APPLY=0). Grok scheduler applies knives.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
PY = sys.executable
OUT = ROOT / "output" / "overnight-civil"
OUT.mkdir(parents=True, exist_ok=True)
LOG = OUT / "loop.log"
STATUS = OUT / "STATUS.md"
QUEUE = OUT / "queue.json"
HEART = OUT / "heartbeat.log"
LIVE = OUT / "live_web.json"
FINAL = OUT / "FINAL_REPORT.md"
LOCK = OUT / "loop.lock"
PID = OUT / "loop.pid"
APPLY_LOCK = OUT / "apply.lock"

DEFAULT_END = "2026-08-20T08:30:00+08:00"
TZ8 = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (compatible; CivilBuddyOvernightEval/1.0; +https://github.com/LUOaini1213/civil-buddy-sme)"

PAGES = (
    {
        "id": "iras-gst",
        "url": "https://www.iras.gov.sg/taxes/goods-services-tax-(gst)/basics-of-gst/current-gst-rates",
        "needles": ("Current GST rates", "GST rate"),
        "gst_source": True,
    },
    {
        "id": "scdf-fire",
        "url": "https://www.scdf.gov.sg/fire-safety-services-listing/fire-code-2023",
        "needles": ("Fire Code 2023",),
        "gst_source": False,
    },
    {
        "id": "imo-ctu",
        "url": "https://www.imo.org/en/ourwork/safety/pages/ctu-code.aspx",
        "needles": ("CTU Code", "2014"),
        "gst_source": False,
    },
    {
        "id": "gebiz",
        "url": "https://www.gebiz.gov.sg/",
        "needles": ("GeBIZ",),
        "gst_source": False,
    },
    {
        "id": "mof-proc",
        "url": "https://www.mof.gov.sg/policies/government-procurement/procurement-processes/",
        "needles": ("Procurement", "GeBIZ"),
        "gst_source": False,
    },
)

GATES_FAST = (
    "scripts/test_understand.py",
    "scripts/test_sandbox.py",
    "scripts/test_runtime_p0.py",
    "scripts/test_agent_loop.py",
    "scripts/test_tender_handoff.py",
    "scripts/test_tender_review.py",
    "scripts/test_mcp_surface.py",
    "scripts/test_industry_agent_eval.py",
)
GATES_SLOW = (
    "scripts/test_expert_turn.py",
    "scripts/test_tender_ingest.py",
)

DEFAULT_QUEUE = [
    {"id": "P1-1", "title": "tender.handoff.json + compliance 三列 + tech 评分点", "status": "pending"},
    {"id": "P1-4", "title": "GET /api/runs/{id} messages/steps/tools", "status": "pending"},
    {"id": "P1-3", "title": "session slot 辖区/项目/P0", "status": "pending"},
    {"id": "P1-5", "title": "method-hazard 未确认 0 稿 + 禁语", "status": "pending"},
    {"id": "eval-needles", "title": "eval/live CORENET X / APPBCA 针", "status": "pending"},
    {"id": "agent-ui", "title": "Agent 循环 HITL/max_steps 文案", "status": "pending"},
]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_s() -> str:
    return now_utc().isoformat()


def log(msg: str) -> None:
    line = f"[{now_s()}] {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def parse_end() -> datetime:
    raw = (os.environ.get("AUTONOMY_END_TS") or DEFAULT_END).strip()
    s = raw.replace("Z", "+00:00")
    t = datetime.fromisoformat(s)
    if t.tzinfo is None:
        t = t.replace(tzinfo=TZ8)
    return t


def remaining_s(end: datetime) -> float:
    return (end - datetime.now(tz=end.tzinfo)).total_seconds()


def run(args: List[str], timeout: int = 240) -> Tuple[int, str]:
    try:
        p = subprocess.run(
            args,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONUNBUFFERED": "1"},
        )
        return p.returncode, ((p.stdout or "") + ("\n" + (p.stderr or "") if p.stderr else ""))[-8000:]
    except subprocess.TimeoutExpired:
        return 124, f"TIMEOUT {args}"
    except Exception as e:
        return 1, str(e)


def git(*a: str) -> Tuple[int, str]:
    return run(["git", *a], timeout=60)


def head() -> str:
    c, o = git("rev-parse", "HEAD")
    return o.strip().splitlines()[-1] if c == 0 else "unknown"


def load_queue() -> List[Dict[str, Any]]:
    if QUEUE.is_file():
        try:
            data = json.loads(QUEUE.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                return data
        except json.JSONDecodeError:
            pass
    QUEUE.write_text(json.dumps(DEFAULT_QUEUE, ensure_ascii=False, indent=2), encoding="utf-8")
    return list(DEFAULT_QUEUE)


def save_queue(q: List[Dict[str, Any]]) -> None:
    QUEUE.write_text(json.dumps(q, ensure_ascii=False, indent=2), encoding="utf-8")


def mark_queue(qid: str, status: str, note: str = "") -> None:
    q = load_queue()
    for row in q:
        if row.get("id") == qid:
            row["status"] = status
            if note:
                row["note"] = note
            row["ts"] = now_s()
    save_queue(q)


def next_pending() -> Optional[Dict[str, Any]]:
    for row in load_queue():
        if row.get("status") == "pending":
            return row
    return None


def fetch_page(url: str) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read(600_000)
            text = raw.decode("utf-8", "replace")
            return {"ok": True, "status": getattr(resp, "status", 200), "n": len(text), "text": text, "url": url}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        return {"ok": False, "error": str(e)[:300], "url": url, "text": "", "n": 0}


def live_web() -> Dict[str, Any]:
    if (os.environ.get("OVERNIGHT_LIVE_WEB") or "1").strip() not in {"1", "true", "yes"}:
        return {"skipped": True, "ok": True, "pages": []}
    pages = []
    gst_source_found: Optional[bool] = None
    for spec in PAGES:
        got = fetch_page(spec["url"])
        blob = got.get("text") or ""
        hits = [n for n in spec["needles"] if n in blob]
        rec = {
            "id": spec["id"],
            "url": spec["url"],
            "fetch_ok": bool(got.get("ok")),
            "http": got.get("status"),
            "n_chars": got.get("n") or 0,
            "hits": hits,
            "error": got.get("error"),
        }
        if spec.get("gst_source"):
            if not got.get("ok"):
                rec["gst_source_found"] = None
                rec["note"] = "fetch_failed; current rate remains unverified"
                gst_source_found = None if gst_source_found is None else gst_source_found
            else:
                has_title = any(needle in blob for needle in spec["needles"])
                rec["gst_source_found"] = has_title
                rec["note"] = "source title found; rate not validated" if has_title else "source title not found; do not infer tax rate"
                gst_source_found = has_title if has_title else False
        pages.append(rec)
    kb = ROOT / "demo" / "kb" / "finance" / "finance-tax" / "web-knowledge.md"
    kb_text = kb.read_text(encoding="utf-8") if kb.is_file() else ""
    out = {
        "ok": True,
        "ts": now_s(),
        "gst_source_found": gst_source_found,
        "kb_has_gst_source": "Current GST rates" in kb_text and "IRAS" in kb_text,
        "failed_fetch_does_not_validate_rate": True,
        "pages": pages,
    }
    LIVE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def run_gates(slow: bool) -> Dict[str, Any]:
    rows = []
    ok = True
    scripts = list(GATES_FAST) + (list(GATES_SLOW) if slow else [])
    for rel in scripts:
        timeout = 300 if "expert_turn" in rel else 180
        code, out = run([PY, rel], timeout=timeout)
        passed = code == 0 and "PASS" in out
        rows.append({"script": rel, "code": code, "pass": passed, "tail": out.strip().splitlines()[-1] if out.strip() else ""})
        if not passed:
            ok = False
            log(f"GATE FAIL {rel} code={code} {rows[-1]['tail']}")
        else:
            log(f"GATE PASS {rel}")
    return {"ok": ok, "rows": rows}


def eval_live_local() -> Dict[str, Any]:
    from packing_assistant.runtime.eval_live import live_eval

    return live_eval()


def detect_p11_done() -> bool:
    code, _ = run([PY, "scripts/test_tender_handoff.py"], timeout=120)
    return code == 0


def write_status(cycle: int, payload: Dict[str, Any], end: datetime) -> None:
    rem_h = max(0.0, remaining_s(end) / 3600)
    nxt = next_pending()
    md = "\n".join(
        [
            "# Overnight Civil Buddy STATUS",
            "",
            f"- ts: {now_s()}",
            f"- deadline: {end.isoformat()}",
            f"- remaining_h: {rem_h:.2f}",
            f"- cycle: {cycle}",
            f"- HEAD: `{payload.get('head')}`",
            f"- gates_ok: {payload.get('gates_ok')}",
            f"- eval_live_ok: {payload.get('eval_live_ok')}",
            f"- live_web gst_source_found: {payload.get('gst_source_found')}",
            f"- kb_has_gst_source: {payload.get('kb_has_gst_source')}",
            f"- next_knife: {(nxt or {}).get('id') or 'none'}",
            f"- rolled_back: {payload.get('rolled_back')}",
            f"- verdict_locked: 部分合格",
            "",
            "抓取失败不能推断官方内容；标题命中不代表税率已核验，缺依据保留 UNSPECIFIED。",
            "",
        ]
    )
    STATUS.write_text(md, encoding="utf-8")
    with HEART.open("a", encoding="utf-8") as f:
        f.write(
            f"CYCLE n={cycle} ok={payload.get('gates_ok')} remaining_h={rem_h:.2f} "
            f"next={(nxt or {}).get('id') or '-'} gst_source={payload.get('gst_source_found')}\n"
        )


def write_final(cycles: List[Dict[str, Any]], end: datetime) -> None:
    n_ok = sum(1 for c in cycles if c.get("gates_ok"))
    nxt = next_pending()
    body = "\n".join(
        [
            "# 早报 · Civil Buddy 过夜评测迭代",
            "",
            f"- 截止: {end.isoformat()}",
            f"- 写于: {now_s()}",
            f"- 轮次: {len(cycles)} · 快闸绿 {n_ok}",
            f"- HEAD: `{head()}`",
            f"- 下一未做刀: {(nxt or {}).get('id') or '队列空'}",
            f"- 行业总判: **部分合格**（脚本未改口）",
            "",
            "## 轮次",
            "",
        ]
    )
    for c in cycles:
        body += (
            f"- cycle {c.get('n')}: gates={c.get('gates_ok')} eval={c.get('eval_live_ok')} "
            f"gst_source_found={c.get('gst_source_found')} rolled_back={c.get('rolled_back')}\n"
        )
    body += "\n## 闸口径\n\n- submit_blocked 仍 true\n- 不判定可以投标 / 可以开工\n- 装箱断线 UNSPECIFIED\n"
    FINAL.write_text(body, encoding="utf-8")
    log("OVERNIGHT_DEADLINE_DONE")
    with HEART.open("a", encoding="utf-8") as f:
        f.write("DONE overnight-civil\n")


def acquire() -> None:
    PID.write_text(str(os.getpid()), encoding="utf-8")
    LOCK.write_text(json.dumps({"pid": os.getpid(), "ts": now_s()}, indent=2), encoding="utf-8")


def main() -> int:
    end = parse_end()
    os.environ["AUTONOMY_END_TS"] = end.isoformat()
    acquire()
    load_queue()
    if detect_p11_done():
        mark_queue("P1-1", "done", "test_tender_handoff PASS")
    log(f"LOOP START end={end.isoformat()} rem_h={remaining_s(end)/3600:.2f} head={head()[:8]}")
    cycles: List[Dict[str, Any]] = []
    n = 0
    once = (os.environ.get("OVERNIGHT_ONCE") or "").strip() in {"1", "true", "yes"}
    sleep_sec = int(os.environ.get("OVERNIGHT_SLEEP_SEC") or "2400")
    fail_streak = 0
    while remaining_s(end) > 90:
        n += 1
        t0 = time.time()
        skip_slow = (os.environ.get("OVERNIGHT_SKIP_SLOW") or "").strip() in {"1", "true", "yes"}
        slow = (n == 1 or n % 3 == 0) and not skip_slow
        log(f"cycle#{n} slow={slow} rem_h={remaining_s(end)/3600:.2f}")
        start_head = head()
        gates = run_gates(slow=slow)
        try:
            elive = eval_live_local()
        except Exception as e:
            elive = {"ok": False, "error": str(e)[:200]}
        web = live_web()
        if gates["ok"] and elive.get("ok"):
            fail_streak = 0
        else:
            fail_streak += 1
        rec = {
            "n": n,
            "ts": now_s(),
            "head": start_head,
            "gates_ok": bool(gates["ok"]),
            "eval_live_ok": bool(elive.get("ok")),
            "gst_source_found": web.get("gst_source_found"),
            "kb_has_gst_source": web.get("kb_has_gst_source"),
            "rolled_back": False,
            "slow": slow,
            "fail_streak": fail_streak,
            "duration_s": int(time.time() - t0),
        }
        (OUT / f"cycle-{n:03d}.json").write_text(
            json.dumps({"rec": rec, "gates": gates, "eval_live": {k: elive.get(k) for k in ("ok", "verdict", "gates") if k in elive}, "live_web": web}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        cycles.append(rec)
        write_status(n, rec, end)
        if fail_streak >= 3:
            log("three consecutive gate fails — eval-only until deadline")
        if once:
            break
        nap = min(sleep_sec, max(30, remaining_s(end) - 60))
        if remaining_s(end) < 120:
            break
        log(f"sleep {nap:.0f}s")
        time.sleep(nap)
    write_final(cycles, end)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
