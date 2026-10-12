#!/usr/bin/env python3
"""data(round2) 存储层测试：建库/导入幂等/dual 双写一致/回滚开关三态/audit 对拍/user_version。

用法: python scripts/test_storage_sqlite.py
全部用 tmp 路径（CB_DB_PATH / PACKING_OUTPUT_DIR），不污染真实 output/ 与 data/civilbuddy.db。
"""
from __future__ import annotations

from contextlib import closing
import json
import os
import subprocess
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}" + (f" · {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL {name} {detail}")


def _mk_fixture(out: Path) -> None:
    """构造最小 output/ 布局：2 个 session（各 2 run）+ 1 个 interrupted。"""
    (out / "sessions").mkdir(parents=True, exist_ok=True)
    (out / "traces").mkdir(parents=True, exist_ok=True)
    for i, sid in enumerate(("fix-alpha", "fix-beta")):
        for j in range(2):
            rid = f"2026010{i}{j}0000_{sid[:4]}{j}"
            rd = out / "runs" / rid
            rd.mkdir(parents=True, exist_ok=True)
            state = {
                "session_id": sid, "run_id": rid, "phase": "done" if j else "await_user_confirm",
                "user_action": "confirm" if j else None,
                "container_type": "40HQ", "boxes": [{"l": 1}] * (j + 1), "materials": [{"id": k} for k in range(j + 2)],
                "final_response": "ok" if j else None,
            }
            (rd / "session_state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            cp = {
                "schema": "packing.checkpoint.v1", "session_id": sid, "run_id": rid,
                "phase": state["phase"], "status": "done" if j else "interrupted",
                "interrupt": not j, "user_action": state["user_action"], "container_type": "40HQ",
                "n_boxes": j + 1, "saved_at": f"2026-01-0{i + 1}T0{j}:00:00+00:00",
            }
            (rd / "checkpoint.json").write_text(json.dumps(cp, ensure_ascii=False), encoding="utf-8")
            with (rd / "trace.jsonl").open("w", encoding="utf-8") as f:
                f.write(json.dumps({"type": "run_start", "run_id": rid, "session_id": sid, "seq": 1,
                                    "ts": cp["saved_at"], "t_ms": 1700000000000 + i * 100 + j}, ensure_ascii=False) + "\n")
                f.write(json.dumps({"type": "tool_start", "run_id": rid, "node": "n1", "tool": "run_packing", "seq": 2,
                                    "ts": cp["saved_at"], "t_ms": 1700000000001 + i * 100 + j}, ensure_ascii=False) + "\n")
        idx = {"session_id": sid, "thread_id": sid, "run_id": f"2026010{i}10000_{sid[:4]}1",
               "phase": "done", "status": "done", "saved_at": f"2026-01-0{i + 1}T01:00:00+00:00"}
        (out / "sessions" / f"{sid}.json").write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    print("== data(round2) storage sqlite tests ==")
    tmp = Path(tempfile.mkdtemp(prefix="cb_storage_test_"))
    out = tmp / "out"
    dbp = tmp / "civilbuddy.db"
    _mk_fixture(out)

    env_base = {k: v for k, v in os.environ.items() if k not in ("CB_STORAGE", "CB_DB_PATH", "PACKING_OUTPUT_DIR")}
    env_base["CB_DB_PATH"] = str(dbp)
    env_base["PACKING_OUTPUT_DIR"] = str(out)
    env_base["PACKING_TRACE_DIR"] = str(out / "traces")

    # fresh interpreter helper（避免单例/env 串扰）
    def run_py(code: str, env: dict) -> str:
        e = dict(env_base)
        e.update(env)
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(ROOT), env=e, timeout=180)
        if r.returncode != 0:
            raise RuntimeError(f"subprocess failed:\n{r.stdout}\n{r.stderr}")
        return r.stdout

    # ---- 1) 建库 + user_version + PRAGMA ----
    print("[1] 建库 / user_version / WAL / schema")
    st = __import__("packing_assistant.storage", fromlist=["Storage"]).Storage(db_path=dbp)
    check("user_version==1", st.user_version() == 1)
    c = sqlite3.connect(str(dbp))
    check("journal_mode=wal", str(c.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal")
    tabs = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    need = {"sessions", "runs", "events", "audit_decisions", "scores", "schema_migrations", "kb_index", "kb_chunks"}
    check("schema 全表存在", need <= tabs, f"missing={need - tabs}")
    check("created_at 列存在(scores)", "created_at" in {r[1] for r in c.execute("PRAGMA table_info(scores)")})
    c.close()

    # ---- 2) 导入幂等（跑两遍行数不变）----
    print("[2] 导入幂等")
    res1 = st.import_json(out, include_workbench=False)
    snap1 = st.stats()
    res2 = st.import_json(out, include_workbench=False)
    snap2 = st.stats()
    for k in ("sessions", "runs", "events", "audit_decisions", "scores"):
        check(f"幂等 {k}", snap1[k] == snap2[k], f"{snap1[k]} -> {snap2[k]}")
    check("导入数量", snap1["sessions"] == 2 and snap1["runs"] == 4 and snap1["events"] == 8,
          f"sessions={snap1['sessions']} runs={snap1['runs']} events={snap1['events']}")

    # ---- 3) dual 双写一致 ----
    print("[3] dual 双写一致")
    env_dual = {"CB_STORAGE": "dual"}
    out_dual = run_py(
        "import json\n"
        "from packing_assistant import storage as S\n"
        "from packing_assistant.session_store import save_session, load_session\n"
        "from packing_assistant.trace_events import append_trace_event\n"
        "S.reset_storage()\n"
        "state = {'session_id': 'dual-s1', 'run_id': 'dual-r1', 'phase': 'await_user_confirm',"
        " 'container_type': '20GP', 'boxes': [{'l': 2}], 'materials': [{'id': 1}, {'id': 2}]}\n"
        "r = save_session('dual-s1', state)\n"
        "ev = append_trace_event('dual-r1', {'type': 'hitl', 'node': 'team_a'})\n"
        "print(json.dumps({'result': r, 'ev': ev}))\n",
        env_dual,
    )
    import logging  # noqa: F401  (双写失败告警路径由 logger 覆盖)
    dual = json.loads(out_dual.strip().splitlines()[-1])
    idx_json = out / "sessions" / "dual-s1.json"
    state_json = out / "runs" / "dual-r1" / "session_state.json"
    trace_jsonl = out / "runs" / "dual-r1" / "trace.jsonl"
    check("dual JSON 落盘", idx_json.exists() and state_json.exists() and trace_jsonl.exists())
    row = st.load_session("dual-s1")
    file_state = json.loads(state_json.read_text(encoding="utf-8"))
    check("dual DB==JSON state deep-equal", row == file_state,
          "" if row == file_state else f"db_keys={sorted(row or {})}")
    db_ev = st.read_trace_events("dual-r1")
    file_ev = [json.loads(l) for l in trace_jsonl.read_text(encoding="utf-8").splitlines() if l.strip()]
    check("dual events DB==JSONL", db_ev == file_ev and len(db_ev) == 1)

    # ---- 4) 回滚开关三态 ----
    print("[4] 回滚开关三态")
    # json 模式：只写 JSON，DB 不增长
    before = st.stats()["sessions"]
    run_py(
        "from packing_assistant.session_store import save_session\n"
        "save_session('json-only-s', {'session_id': 'json-only-s', 'run_id': 'json-only-r', 'phase': 'done'})\n"
        "print('OK')\n",
        {"CB_STORAGE": "json"},
    )
    check("json 模式 DB 零写入", st.stats()["sessions"] == before and (out / "sessions" / "json-only-s.json").exists())
    # sqlite 模式：只写 DB，不写 JSONL；读 DB 优先
    out_sql = run_py(
        "import json, os\n"
        "from packing_assistant.session_store import save_session, load_session\n"
        "from packing_assistant.trace_events import append_trace_event, read_trace_jsonl\n"
        "S = __import__('packing_assistant.storage', fromlist=['x'])\n"
        "S.reset_storage()\n"
        "save_session('sql-s1', {'session_id': 'sql-s1', 'run_id': 'sql-r1', 'phase': 'running', 'boxes': []})\n"
        "append_trace_event('sql-r1', {'type': 'run_start', 'goal': 'x'})\n"
        "no_jsonl = not os.path.exists(os.path.join(os.environ['PACKING_OUTPUT_DIR'], 'runs', 'sql-r1', 'trace.jsonl'))\n"
        "back = load_session('fix-alpha')  # DB 有（导入过）也应能读到\n"
        "print(json.dumps({'no_jsonl': no_jsonl, 'loaded': load_session('sql-s1') is not None,"
        " 'legacy_ok': back is not None, 'n_ev': len(read_trace_jsonl('sql-r1'))}))\n",
        {"CB_STORAGE": "sqlite"},
    )
    sq = json.loads(out_sql.strip().splitlines()[-1])
    check("sqlite 模式不写 trace.jsonl", sq["no_jsonl"])
    check("sqlite 模式 save/load", sq["loaded"])
    check("sqlite 读旧数据(JSON 回退)", sq["legacy_ok"])
    check("sqlite events 读回", sq["n_ev"] == 1)
    # 非法值回落 sqlite（D-R4 起默认）
    mode_out = run_py(
        "from packing_assistant import storage as S\nprint(S.storage_mode())", {"CB_STORAGE": "bogus"}
    )
    check("非法 CB_STORAGE 回落", mode_out.strip().splitlines()[-1] == "sqlite")

    # ---- 5) audit SQL==JSON 对拍（本 fixture 内）----
    print("[5] audit 对拍（fixture）")
    out_parity = run_py(
        "import json, sys\nsys.path.insert(0, '.')\n"
        "from gateway.app import api_audit\n"
        "import os\n"
        "r = api_audit(session='fix-alpha')\n"
        "print(json.dumps(r, ensure_ascii=False, sort_keys=True))\n",
        {"CB_STORAGE": "sqlite"},
    )
    out_parity_json = run_py(
        "import json, sys\nsys.path.insert(0, '.')\n"
        "from gateway.app import api_audit\n"
        "r = api_audit(session='fix-alpha')\n"
        "print(json.dumps(r, ensure_ascii=False, sort_keys=True))\n",
        {"CB_STORAGE": "json"},
    )
    resp_sql = json.loads(out_parity.strip().splitlines()[-1])
    resp_json = json.loads(out_parity_json.strip().splitlines()[-1])
    check("audit SQL==JSON 全响应", resp_sql == resp_json,
          "" if resp_sql == resp_json else next(
              (f"{k}: {resp_json.get(k)} != {resp_sql.get(k)}"
               for k in resp_json if resp_json.get(k) != resp_sql.get(k)), "?"))
    check("audit schema civil.audit.v1", resp_sql.get("schema") == "civil.audit.v1")
    check("audit n_runs_matched", resp_sql.get("n_runs_matched") == 2)

    # ---- 6) prune（软删除）----
    print("[6] prune 软删除")
    with closing(sqlite3.connect(str(dbp))) as c2:
        c2.execute("UPDATE runs SET started_at='2020-01-01T00:00:00' WHERE session_id LIKE 'fix-%'")
        c2.commit()
    n0 = st.prune(keep_days=90, keep_min_per_session=3)
    check("keep_min_per_session 保护", n0 == 0, f"archived={n0}")
    n = st.prune(keep_days=90, keep_min_per_session=0)
    check("prune 归档旧 run", n == 4, f"archived={n}")
    with closing(sqlite3.connect(str(dbp))) as c2:
        archived = c2.execute("SELECT COUNT(*) FROM runs WHERE archived=1").fetchone()[0]
        ev_arch = c2.execute("SELECT COUNT(*) FROM events WHERE archived=1").fetchone()[0]
    check("archived 标记落库", archived == 4 and ev_arch == 8, f"runs={archived} events={ev_arch}")

    # ---- 7) durable checkpoint：SqliteSaver 同库 ----
    print("[7] SqliteSaver durable")
    out_lg = run_py(
        "import json, sqlite3, os\n"
        "from packing_assistant.lg_checkpoint import get_checkpointer, checkpoint_db_path\n"
        "cp = get_checkpointer()\n"
        "db = str(checkpoint_db_path())\n"
        "c = sqlite3.connect(db)\n"
        "tabs = {r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")}\n"
        "c.close()\n"
        "print(json.dumps({'backend': type(cp).__name__, 'path': db,"
        " 'has_cp_tables': {'checkpoints', 'writes'} <= tabs}))\n",
        {"CB_STORAGE": "sqlite", "PACKING_LG_CHECKPOINT_PATH": str(dbp)},
    )
    lg = json.loads(out_lg.strip().splitlines()[-1])
    check("checkpointer=SqliteSaver（非 MemorySaver 回退）", lg["backend"] == "SqliteSaver", lg["backend"])
    check("langgraph 表同库共存", lg["has_cp_tables"])

    print(f"\nRESULT: PASS={PASS} FAIL={FAIL}")
    st.close()
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
