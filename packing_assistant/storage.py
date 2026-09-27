"""统一 SQLite 持久层（data-plan M1/M2 · data/civilbuddy.db）。

职责：
  - 建库/建表/迁移（PRAGMA user_version + schema_migrations）
  - sessions/runs/events/audit_decisions/scores 五张业务表（kb_index/kb_chunks/kb_fts
    schema 先建好，检索引擎 D-R3/M3 使用）
  - 迁移导入器 import_json（幂等：按 run_id/session_id UPSERT）
  - 维护：backup(VACUUM INTO) / prune(软删除 archived)

回滚开关（环境变量 CB_STORAGE=json|dual|sqlite，默认 sqlite）：
  json   = 纯旧 JSON 代码路径（与 6df7e1c 等价，零风险回滚）
  dual   = 写路径 JSON+SQLite 双写（SQLite 失败仅告警不阻断），读路径仍 JSON
  sqlite = 读写均 SQLite 优先（读不到再回退 JSON，保证旧数据可读），JSON 降级为导出格式
           （D-R4 起默认；切换前已重导 + audit 对拍全绿，见 docs/data/d-r3-status.md）

连接纪律（audit C3）：每个连接 PRAGMA journal_mode=WAL + synchronous=NORMAL +
busy_timeout=5000 + foreign_keys=ON；进程内单写连接（模块级锁）+ 惰性 thread-local 读连接。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("civil.storage")

SCHEMA_VERSION = 1

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations(
  version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions(
  session_id     TEXT PRIMARY KEY,
  run_id         TEXT,
  phase          TEXT,
  status         TEXT,
  user_action    TEXT,
  container_type TEXT, n_boxes INTEGER, n_materials INTEGER,
  packing_plan_id TEXT,
  saved_at       TEXT NOT NULL,
  state_json     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_saved  ON sessions(saved_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);

CREATE TABLE IF NOT EXISTS runs(
  run_id       TEXT PRIMARY KEY,
  session_id   TEXT,
  app          TEXT NOT NULL DEFAULT 'packing',
  source       TEXT,
  expert_id    TEXT, category TEXT,
  started_at   TEXT, ended_at TEXT,
  phase        TEXT, status TEXT,
  container_type TEXT, n_boxes INTEGER,
  checkpoint_json TEXT,
  run_dir      TEXT,
  archived     INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY(session_id) REFERENCES sessions(session_id)
);
CREATE INDEX IF NOT EXISTS idx_runs_session ON runs(session_id, started_at);
CREATE INDEX IF NOT EXISTS idx_runs_expert  ON runs(expert_id, started_at);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at DESC);

CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  seq INTEGER, ts TEXT, t_ms INTEGER,
  type TEXT NOT NULL,
  node TEXT, agent_id TEXT, parent_node TEXT,
  tool TEXT, status TEXT, duration_ms INTEGER,
  payload_json TEXT,
  archived INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_run  ON events(run_id, id);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_tool ON events(tool);
CREATE INDEX IF NOT EXISTS idx_events_node ON events(node);

CREATE TABLE IF NOT EXISTS audit_decisions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT, run_id TEXT,
  action TEXT NOT NULL, operator TEXT,
  ts TEXT, detail_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_decisions_session ON audit_decisions(session_id, ts);

CREATE TABLE IF NOT EXISTS scores(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  case_id TEXT, run_id TEXT, session_id TEXT,
  passed INTEGER, score REAL, detail_json TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_scores_kind ON scores(kind, created_at DESC);

-- KB 索引（D-R3 / M3 使用，schema 先随 M1 建好，data-plan §1.2）
CREATE TABLE IF NOT EXISTS kb_index(
  path TEXT NOT NULL, kb TEXT NOT NULL,
  title TEXT, display TEXT,
  layer TEXT,
  category TEXT, expert_id TEXT,
  priority TEXT DEFAULT 'medium', tags_json TEXT, status TEXT DEFAULT 'active',
  mtime TEXT, size INTEGER, hash TEXT,
  boost REAL DEFAULT 0,
  PRIMARY KEY(kb, path)
);
CREATE TABLE IF NOT EXISTS kb_chunks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kb TEXT NOT NULL, path TEXT NOT NULL,
  heading TEXT, seq INTEGER, body TEXT,
  body_bigrams TEXT,
  FOREIGN KEY(kb, path) REFERENCES kb_index(kb, path) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chunks_path ON kb_chunks(kb, path);
CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5(
  body_bigrams, kb UNINDEXED, path UNINDEXED, heading UNINDEXED, tokenize='unicode61'
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_db_path() -> Path:
    """CB_DB_PATH if set, else data/civilbuddy.db (repo-relative, immune to cwd drift).

    Honouring the env var here (not only in Storage) keeps the LangGraph checkpointer and the
    startup backup on the same file as the business tables — the Docker image points it into
    its persisted volume.
    """
    raw = (os.getenv("CB_DB_PATH") or "").strip()
    return Path(raw) if raw else repo_root() / "data" / "civilbuddy.db"


def storage_mode() -> str:
    """CB_STORAGE=json|dual|sqlite（默认 sqlite；非法值回落 sqlite）。"""
    raw = (os.getenv("CB_STORAGE") or "sqlite").strip().lower()
    return raw if raw in ("json", "dual", "sqlite") else "sqlite"


class Storage:
    """薄壳统一持久层。线程模型：单写连接 + 模块级锁；读连接 thread-local。"""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or os.getenv("CB_DB_PATH") or default_db_path())
        self._lock = threading.RLock()
        self._write_conn: Optional[sqlite3.Connection] = None
        self._local = threading.local()
        self._ensure_schema()

    # ---------- 连接 ----------

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @property
    def write_conn(self) -> sqlite3.Connection:
        with self._lock:
            if self._write_conn is None:
                self._write_conn = self._connect()
            return self._write_conn

    def _read_conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        return conn

    def close(self) -> None:
        with self._lock:
            if self._write_conn is not None:
                try:
                    self._write_conn.commit()
                    self._write_conn.close()
                except Exception:
                    pass
                self._write_conn = None

    # ---------- schema ----------

    def _ensure_schema(self) -> None:
        with self._lock:
            conn = self.write_conn
            conn.executescript(_SCHEMA_SQL)
            cur = conn.execute("PRAGMA user_version").fetchone()
            current = int(cur[0] or 0)
            if current != SCHEMA_VERSION:
                conn.execute(f"PRAGMA user_version={int(SCHEMA_VERSION)}")
                conn.execute(
                    "INSERT OR REPLACE INTO schema_migrations(version, applied_at) VALUES(?,?)",
                    (SCHEMA_VERSION, _now_iso()),
                )
            conn.commit()

    def user_version(self) -> int:
        return int(self._read_conn().execute("PRAGMA user_version").fetchone()[0])

    # ---------- sessions ----------

    def save_session(self, session_id: str, state: dict, *, meta: dict | None = None) -> dict:
        """UPSERT sessions 行（state_json=完整 state 快照）。meta 由调用方（session_store）给出。"""
        sid = str(session_id or state.get("session_id") or "default")
        rid = str(state.get("run_id") or sid)
        m = meta or {}
        row = (
            sid,
            rid,
            str(state.get("phase") or m.get("phase") or ""),
            str(m.get("status") or ""),
            (str(state.get("user_action")) if state.get("user_action") is not None else None),
            (str(state.get("container_type")) if state.get("container_type") is not None else None),
            m.get("n_boxes"),
            m.get("n_materials"),
            m.get("packing_plan_id"),
            str(m.get("saved_at") or state.get("_session_saved_at") or _now_iso()),
            json.dumps(state, ensure_ascii=False, default=str),
        )
        with self._lock:
            self.write_conn.execute(
                "INSERT OR REPLACE INTO sessions"
                "(session_id, run_id, phase, status, user_action, container_type,"
                " n_boxes, n_materials, packing_plan_id, saved_at, state_json)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                row,
            )
            self.write_conn.commit()
        return {"session_id": sid, "run_id": rid, "saved_at": row[9]}

    def load_session(self, session_id: str) -> dict | None:
        sid = str(session_id or "").strip()
        if not sid:
            return None
        row = self._read_conn().execute(
            "SELECT state_json FROM sessions WHERE session_id=?", (sid,)
        ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except Exception:
            return None

    def load_checkpoint_meta(self, session_id: str) -> dict | None:
        st = self.load_session(session_id)
        if isinstance(st, dict) and st.get("_checkpoint"):
            return st["_checkpoint"]
        row = self._read_conn().execute(
            "SELECT checkpoint_json FROM runs WHERE session_id=? AND checkpoint_json IS NOT NULL"
            " ORDER BY started_at DESC LIMIT 1",
            (str(session_id or "").strip(),),
        ).fetchone()
        if row:
            try:
                return json.loads(row[0])
            except Exception:
                return None
        return None

    def list_checkpoints(
        self, *, limit: int = 50, pending_hitl_only: bool = False
    ) -> list[dict]:
        rows = self._read_conn().execute(
            "SELECT session_id, run_id, phase, status, user_action, container_type,"
            " n_boxes, saved_at, state_json FROM sessions"
            " ORDER BY saved_at DESC LIMIT ?",
            (max(1, int(limit) * 3),),
        ).fetchall()
        items: List[dict] = []
        seen_run: set = set()
        for sid, rid, phase, status, _ua, ctype, n_boxes, saved_at, state_json in rows:
            if rid and rid in seen_run:
                continue
            if rid:
                seen_run.add(rid)
            resume = None
            try:
                st = json.loads(state_json)
                cp = st.get("_checkpoint") if isinstance(st, dict) else None
                if isinstance(cp, dict):
                    resume = cp.get("resume")
                    status = cp.get("status", status)
                    phase = cp.get("phase", phase)
                    interrupt = cp.get("interrupt")
                else:
                    interrupt = status == "interrupted"
            except Exception:
                interrupt = status == "interrupted"
            if pending_hitl_only and not (interrupt or status == "interrupted" or phase == "await_user_confirm"):
                continue
            idx_path = Path(os.getenv("PACKING_OUTPUT_DIR", "output")) / "sessions" / f"{sid}.json"
            items.append(
                {
                    "thread_id": sid,
                    "session_id": sid,
                    "run_id": rid,
                    "phase": phase,
                    "status": status,
                    "interrupt": bool(interrupt or status == "interrupted"),
                    "saved_at": saved_at,
                    "n_boxes": n_boxes,
                    "container_type": ctype,
                    "resume": resume,
                    "index_path": str(idx_path) if idx_path.exists() else "",
                }
            )
            if len(items) >= max(1, int(limit)):
                break
        return items

    def mark_checkpoint(self, session_id: str, *, status: str, extra: dict | None = None) -> dict | None:
        st = self.load_session(session_id)
        if not st:
            return None
        st = dict(st)
        if status == "cancelled":
            st["phase"] = "cancelled"
            st["user_action"] = "cancel"
        elif status == "resumed":
            st["user_action"] = st.get("user_action") or "confirm"
            if st.get("phase") == "await_user_confirm":
                st["phase"] = "team_b_running"
        elif status == "done":
            st.setdefault("phase", "done")
        if extra:
            st.update(extra)
        meta = st.get("_checkpoint") if isinstance(st.get("_checkpoint"), dict) else {}
        meta = dict(meta)
        meta["status"] = status
        meta["interrupt"] = status == "interrupted"
        st["_checkpoint"] = meta
        self.save_session(session_id, st, meta=meta)
        return {"session_id": str(session_id), "status": status}

    # A run that reached one of these events finished or paused cleanly; it is not left mid-run.
    _SETTLED_EVENTS = "('done','hitl')"

    def stale_sessions(self, running_phases: tuple) -> List[tuple]:
        """(session_id, run_id, phase, state_json) for sessions a crash left mid-run: a running phase, or a
        run_start placeholder (ensure_run) whose run never reached a done/hitl event."""
        marks = ",".join("?" for _ in running_phases)
        return self._read_conn().execute(
            "SELECT session_id, run_id, phase, state_json FROM sessions s"
            f" WHERE s.phase IN ({marks})"
            " OR (s.status='placeholder' AND NOT EXISTS(SELECT 1 FROM events e"
            f" WHERE e.run_id=s.run_id AND e.type IN {self._SETTLED_EVENTS}))",
            tuple(running_phases),
        ).fetchall()

    def interrupt_orphan_runs(self, running_phases: tuple, *, ended_at: str) -> int:
        """runs rows with no outcome (status NULL or running) and no done/hitl event → phase interrupted."""
        marks = ",".join("?" for _ in running_phases)
        with self._lock:
            cur = self.write_conn.execute(
                "UPDATE runs SET phase='interrupted', status='aborted', ended_at=COALESCE(ended_at, ?)"
                f" WHERE archived=0 AND (phase IS NULL OR phase='' OR phase IN ({marks}))"
                " AND (status IS NULL OR status IN ('', 'running', 'resumed'))"
                f" AND NOT EXISTS(SELECT 1 FROM events e WHERE e.run_id=runs.run_id AND e.type IN {self._SETTLED_EVENTS})",
                (ended_at, *running_phases),
            )
            self.write_conn.commit()
            return int(cur.rowcount or 0)

    def delete_sessions(self, ids: List[str]) -> int:
        ids = [str(i) for i in (ids or []) if i]
        if not ids:
            return 0
        qmarks = ",".join("?" for _ in ids)
        with self._lock:
            cur = self.write_conn.execute(
                f"DELETE FROM sessions WHERE session_id IN ({qmarks})", ids
            )
            self.write_conn.commit()
            return int(cur.rowcount or 0)

    # ---------- runs / events ----------

    def upsert_run(self, run: dict) -> None:
        with self._lock:
            self.write_conn.execute(
                "INSERT OR REPLACE INTO runs"
                "(run_id, session_id, app, source, expert_id, category, started_at, ended_at,"
                " phase, status, container_type, n_boxes, checkpoint_json, run_dir)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(run.get("run_id") or ""),
                    run.get("session_id"),
                    str(run.get("app") or "packing"),
                    run.get("source"),
                    run.get("expert_id"),
                    run.get("category"),
                    run.get("started_at"),
                    run.get("ended_at"),
                    run.get("phase"),
                    run.get("status"),
                    run.get("container_type"),
                    run.get("n_boxes"),
                    run.get("checkpoint_json"),
                    run.get("run_dir"),
                ),
            )
            self.write_conn.commit()

    def ensure_run(self, run: dict) -> None:
        """INSERT OR IGNORE：双写期从 run_start 事件补 runs 行，不覆盖导入器的 checkpoint_json。

        runs.session_id 外键指向 sessions。run_start 事件常常先于该会话的第一次
        checkpoint 到达（并发 lane、或从不落盘的 run），此前这里直接违反外键，
        整条 run 的事件都回退到 JSONL（issue #22）。现在先补一行占位 session：
        status='placeholder'、state_json='{}'，之后 save_session 的 INSERT OR
        REPLACE 会把它换成真实快照。
        """
        sid = run.get("session_id")
        with self._lock:
            if sid:
                self.write_conn.execute(
                    "INSERT OR IGNORE INTO sessions"
                    "(session_id, run_id, phase, status, saved_at, state_json)"
                    " VALUES(?,?,?,?,?,?)",
                    (
                        str(sid),
                        str(run.get("run_id") or ""),
                        run.get("phase"),
                        "placeholder",
                        str(run.get("started_at") or _now_iso()),
                        "{}",
                    ),
                )
            self.write_conn.execute(
                "INSERT OR IGNORE INTO runs"
                "(run_id, session_id, app, source, started_at, phase, container_type, run_dir)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (
                    str(run.get("run_id") or ""),
                    run.get("session_id"),
                    str(run.get("app") or "packing"),
                    run.get("source"),
                    run.get("started_at"),
                    run.get("phase"),
                    run.get("container_type"),
                    run.get("run_dir"),
                ),
            )
            self.write_conn.commit()

    def insert_event(self, ev: dict) -> None:
        with self._lock:
            self.write_conn.execute(
                "INSERT INTO events"
                "(run_id, seq, ts, t_ms, type, node, agent_id, parent_node, tool, status,"
                " duration_ms, payload_json)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(ev.get("run_id") or ""),
                    ev.get("seq"),
                    ev.get("ts"),
                    ev.get("t_ms"),
                    str(ev.get("type") or "event"),
                    ev.get("node"),
                    ev.get("agent_id"),
                    ev.get("parent_node"),
                    ev.get("tool"),
                    ev.get("status"),
                    ev.get("duration_ms"),
                    json.dumps(ev, ensure_ascii=False, default=str),
                ),
            )
            self.write_conn.commit()

    def insert_events(self, events: List[dict]) -> None:
        if not events:
            return
        rows = [
            (
                str(ev.get("run_id") or ""),
                ev.get("seq"),
                ev.get("ts"),
                ev.get("t_ms"),
                str(ev.get("type") or "event"),
                ev.get("node"),
                ev.get("agent_id"),
                ev.get("parent_node"),
                ev.get("tool"),
                ev.get("status"),
                ev.get("duration_ms"),
                json.dumps(ev, ensure_ascii=False, default=str),
            )
            for ev in events
        ]
        with self._lock:
            self.write_conn.executemany(
                "INSERT INTO events"
                "(run_id, seq, ts, t_ms, type, node, agent_id, parent_node, tool, status,"
                " duration_ms, payload_json)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
            self.write_conn.commit()

    def read_trace_events(self, run_id: str, *, limit: int | None = 5000) -> List[dict]:
        """Read a bounded replay, or the complete trace for an explicit export."""
        rows = self._read_conn().execute(
            "SELECT payload_json FROM events WHERE run_id=? ORDER BY id LIMIT ?",
            (str(run_id), -1 if limit is None else max(1, int(limit))),
        ).fetchall()
        out = []
        for (payload,) in rows:
            try:
                out.append(json.loads(payload))
            except Exception:
                continue
        return out

    def run_first_event(self, run_id: str) -> dict | None:
        row = self._read_conn().execute(
            "SELECT payload_json FROM events WHERE run_id=? ORDER BY id LIMIT 1",
            (str(run_id),),
        ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except Exception:
            return None

    def list_runs(
        self,
        *,
        limit: int = 50,
        session_id: str | None = None,
        expert_id: str | None = None,
        tool: str | None = None,
    ) -> list[dict]:
        """SQL 直查 runs（sqlite 模式读路径）。tool 过滤走 events EXISTS。"""
        conds = ["archived=0"]
        args: List[Any] = []
        if session_id:
            conds.append("session_id=?")
            args.append(session_id)
        if expert_id:
            conds.append("expert_id=?")
            args.append(expert_id)
        if tool:
            conds.append("EXISTS(SELECT 1 FROM events e WHERE e.run_id=runs.run_id AND e.tool=?)")
            args.append(tool)
        args.append(max(1, int(limit)))
        rows = self._read_conn().execute(
            "SELECT run_id, session_id, app, source, expert_id, category, started_at, ended_at,"
            " phase, status, container_type, n_boxes, run_dir FROM runs"
            f" WHERE {' AND '.join(conds)} ORDER BY COALESCE(started_at,'') DESC LIMIT ?",
            args,
        ).fetchall()
        items = []
        for (
            rid, sid, app, source, expert, cat, started, ended,
            phase, status, ctype, n_boxes, run_dir,
        ) in rows:
            meta: Dict[str, Any] = {
                "run_id": rid,
                "session_id": sid,
                "app": app,
                "source": source,
                "expert_id": expert,
                "category": cat,
                "started_at": started,
                "ended_at": ended,
                "phase": phase,
                "status": status,
                "container_type": ctype,
                "n_boxes": n_boxes,
                "run_dir": run_dir,
            }
            idx = (Path(run_dir) / "index.json") if run_dir else None
            if idx and idx.exists():
                try:
                    meta.update(json.loads(idx.read_text(encoding="utf-8")))
                except Exception:
                    pass
            meta["has_trace_jsonl"] = bool(
                self._read_conn().execute(
                    "SELECT 1 FROM events WHERE run_id=? LIMIT 1", (rid,)
                ).fetchone()
            )
            meta["mtime"] = started or ""
            items.append(meta)
        return items

    def audit_runs_for_session(self, sid: str, cap: int = 200) -> List[dict]:
        """替代 /api/audit 全盘扫描：返回与 gateway._audit_runs_for_session 同构的 meta 列表。"""
        sid = str(sid or "").strip()
        if not sid:
            return []
        rows = self._read_conn().execute(
            "SELECT run_id, checkpoint_json, run_dir FROM runs"
            " WHERE archived=0 AND (session_id=? OR run_id=?)"
            " ORDER BY COALESCE(started_at,'') DESC LIMIT ?",
            (sid, sid, max(1, int(cap))),
        ).fetchall()
        found: List[dict] = []
        for rid, cp_json, run_dir in rows:
            meta: Dict[str, Any] = {"run_id": rid}
            if cp_json:
                try:
                    meta["checkpoint"] = json.loads(cp_json)
                except Exception:
                    pass
            first = self.run_first_event(rid)
            meta["first_event"] = first or {}
            try:
                meta["mtime"] = os.path.getmtime(run_dir) if run_dir and Path(run_dir).exists() else 0
            except Exception:
                meta["mtime"] = 0
            found.append(meta)
        found.sort(
            key=lambda m: ((m.get("first_event") or {}).get("t_ms") or 0, m.get("mtime") or 0)
        )
        return found

    def audit_session_list(self, limit: int = 24) -> List[dict]:
        rows = self._read_conn().execute(
            "SELECT session_id, run_id, status, user_action, saved_at FROM sessions"
            " ORDER BY saved_at DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
        return [
            {
                "session_id": r[0],
                "run_id": r[1],
                "status": r[2],
                "user_action": r[3],
                "saved_at": r[4],
            }
            for r in rows
        ]

    # ---------- audit_decisions / scores ----------

    def add_decision(self, *, session_id: str, run_id: str, action: str, operator: str,
                     ts: str | None = None, detail: dict | None = None) -> None:
        with self._lock:
            self.write_conn.execute(
                "INSERT INTO audit_decisions(session_id, run_id, action, operator, ts, detail_json)"
                " VALUES(?,?,?,?,?,?)",
                (session_id, run_id, str(action), operator, ts,
                 json.dumps(detail or {}, ensure_ascii=False, default=str)),
            )
            self.write_conn.commit()

    def add_score(self, kind: str, case_id: str, *, run_id: str | None = None,
                  session_id: str | None = None, passed: int | None = None,
                  score: float | None = None, detail: dict | None = None) -> None:
        with self._lock:
            self.write_conn.execute(
                "INSERT INTO scores(kind, case_id, run_id, session_id, passed, score, detail_json, created_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (str(kind), case_id, run_id, session_id, passed, score,
                 json.dumps(detail or {}, ensure_ascii=False, default=str), _now_iso()),
            )
            self.write_conn.commit()

    # ---------- KB 索引（D-R3/M3：写入口收敛于此，检索层 kb_search 调用） ----------

    def read_conn(self) -> sqlite3.Connection:
        """公开只读连接（thread-local，WAL 下与写连接互不阻塞）。"""
        return self._read_conn()

    def count(self, table: str) -> int:
        if table not in ("sessions", "runs", "events", "audit_decisions", "scores",
                         "kb_index", "kb_chunks", "kb_fts", "checkpoints"):
            raise ValueError(f"unexpected table {table}")
        return int(self._read_conn().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def kb_indexed(self, kb: str) -> Dict[str, tuple]:
        """{path: (mtime_iso, size)}——增量构建判据。"""
        rows = self._read_conn().execute(
            "SELECT path, mtime, size FROM kb_index WHERE kb=?", (str(kb),)
        ).fetchall()
        return {r[0]: (r[1] or "", int(r[2] or 0)) for r in rows}

    def kb_upsert_doc(self, doc: dict, chunks: List[dict]) -> None:
        """单文档原子换血：kb_index 行 + kb_chunks/kb_fts 全量重写（写钩子/构建共用）。"""
        with self._lock:
            conn = self.write_conn
            kb, path = str(doc["kb"]), str(doc["path"])
            conn.execute("DELETE FROM kb_fts WHERE kb=? AND path=?", (kb, path))
            conn.execute("DELETE FROM kb_chunks WHERE kb=? AND path=?", (kb, path))
            conn.execute(
                "INSERT OR REPLACE INTO kb_index"
                "(path, kb, title, display, layer, category, expert_id,"
                " priority, tags_json, status, mtime, size, hash, boost)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    path, kb, doc.get("title"), doc.get("display"), doc.get("layer"),
                    doc.get("category"), doc.get("expert_id"), doc.get("priority") or "medium",
                    doc.get("tags_json") or "[]", doc.get("status") or "active",
                    doc.get("mtime"), doc.get("size"), doc.get("hash"), float(doc.get("boost") or 0.0),
                ),
            )
            for ch in chunks:
                conn.execute(
                    "INSERT INTO kb_chunks(kb, path, heading, seq, body, body_bigrams)"
                    " VALUES(?,?,?,?,?,?)",
                    (kb, path, ch.get("heading"), int(ch.get("seq") or 0),
                     ch.get("body"), ch.get("body_bigrams")),
                )
                if ch.get("body_bigrams"):
                    conn.execute(
                        "INSERT INTO kb_fts(body_bigrams, kb, path, heading) VALUES(?,?,?,?)",
                        (ch["body_bigrams"], kb, path, ch.get("heading")),
                    )
            conn.commit()

    def kb_delete_doc(self, kb: str, path: str) -> bool:
        with self._lock:
            conn = self.write_conn
            conn.execute("DELETE FROM kb_fts WHERE kb=? AND path=?", (kb, path))
            cur = conn.execute("DELETE FROM kb_index WHERE kb=? AND path=?", (kb, path))
            conn.execute("DELETE FROM kb_chunks WHERE kb=? AND path=?", (kb, path))
            conn.commit()
            return bool(cur.rowcount)

    def kb_clear(self, kb: str) -> int:
        """全量重建前置：清空一个 kb 的全部索引行。"""
        with self._lock:
            conn = self.write_conn
            conn.execute("DELETE FROM kb_fts WHERE kb=?", (kb,))
            conn.execute("DELETE FROM kb_chunks WHERE kb=?", (kb,))
            cur = conn.execute("DELETE FROM kb_index WHERE kb=?", (kb,))
            conn.commit()
            return int(cur.rowcount or 0)

    # ---------- 维护 ----------

    def backup(self, dst: Path | None = None) -> Path:
        """VACUUM INTO 快照备份，保留最近 7 份。"""
        bdir = self.db_path.parent / "backup"
        bdir.mkdir(parents=True, exist_ok=True)
        if dst is None:
            dst = bdir / f"civilbuddy-{datetime.now():%Y%m%d}.db"
        if dst.exists():
            return dst
        with self._lock:
            self.write_conn.execute("VACUUM INTO ?", (str(dst),))
        backups = sorted(bdir.glob("civilbuddy-*.db"))
        for old in backups[:-7]:
            try:
                old.unlink()
            except OSError:
                pass
        return dst

    def prune(self, *, keep_days: int = 90, keep_min_per_session: int = 3) -> int:
        """软删除：超过 keep_days 的 runs/events 标记 archived=1（每 session 至少保留
        keep_min_per_session 条最新 run）。返回本次标记的 run 数。"""
        cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - keep_days * 86400))
        with self._lock:
            conn = self.write_conn
            protected = conn.execute(
                "SELECT session_id, COUNT(*) AS n FROM runs WHERE archived=0 GROUP BY session_id"
            ).fetchall()
            keep_map = {sid: n for sid, n in protected}
            rows = conn.execute(
                "SELECT run_id, session_id FROM runs WHERE archived=0 AND"
                " COALESCE(started_at,'') < ? ORDER BY COALESCE(started_at,'') ASC",
                (cutoff,),
            ).fetchall()
            to_archive = []
            for rid, sid in rows:
                if not sid:
                    # 无 session 归属的历史 run（如 workbench-bridge 导入）：按年龄直接归档，
                    # "每 session 保留 N 条"保护对空分组无意义，否则永远不可清理。
                    to_archive.append(rid)
                    continue
                if keep_map.get(sid, 0) < max(0, int(keep_min_per_session)):
                    continue
                to_archive.append(rid)
                keep_map[sid] = keep_map.get(sid, 0) - 1
            if not to_archive:
                return 0
            qmarks = ",".join("?" for _ in to_archive)
            conn.execute(f"UPDATE runs SET archived=1 WHERE run_id IN ({qmarks})", to_archive)
            conn.execute(
                f"UPDATE events SET archived=1 WHERE run_id IN ({qmarks})", to_archive
            )
            conn.commit()
            return len(to_archive)

    # ---------- 迁移导入（幂等） ----------

    def import_json(self, out_dir: Path | None = None, *, include_demo: bool = False,
                    include_workbench: bool = True) -> dict:
        """存量 JSON → SQLite。幂等：sessions/runs 按 id UPSERT，events 按 run 重放覆盖。"""
        out_dir = Path(out_dir or os.getenv("PACKING_OUTPUT_DIR", "output")).resolve()
        stats = {
            "sessions": 0, "runs": 0, "events": 0, "scores": 0,
            "workbench_runs": 0, "demo_runs": 0, "skipped": 0,
        }
        # 1) sessions 索引（output/sessions/*.json）
        sdir = out_dir / "sessions"
        if sdir.exists():
            for p in sorted(sdir.glob("*.json")):
                try:
                    idx = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    stats["skipped"] += 1
                    continue
                sid = str(idx.get("session_id") or p.stem)
                rid = str(idx.get("run_id") or sid)
                state: dict = {}
                state_path = idx.get("session_state")
                if state_path and Path(state_path).exists():
                    try:
                        state = json.loads(Path(state_path).read_text(encoding="utf-8"))
                    except Exception:
                        state = {}
                if not state:
                    alt = out_dir / "runs" / rid / "session_state.json"
                    if alt.exists():
                        try:
                            state = json.loads(alt.read_text(encoding="utf-8"))
                        except Exception:
                            state = {}
                meta = {}
                cp_path = idx.get("checkpoint")
                if cp_path and Path(cp_path).exists():
                    try:
                        meta = json.loads(Path(cp_path).read_text(encoding="utf-8"))
                    except Exception:
                        meta = {}
                if not state:
                    # 只有索引没有 state 的 session：用 idx 兜底成最小快照
                    state = {**idx, "_imported_index_only": True}
                state.setdefault("session_id", sid)
                state.setdefault("run_id", rid)
                self.save_session(sid, state, meta=meta or None)
                stats["sessions"] += 1
        # 2) runs（output/runs/<id>/）
        stats.update(self._import_runs_dir(out_dir / "runs", app="packing", source="gateway", base=out_dir))
        stats["runs"] = stats.pop("runs_imported", 0)
        stats["events"] = stats.pop("events_imported", 0)
        # 3) workbench 孤儿数据（audit B1-#7）
        wb = repo_root() / "workbench" / "output" / "runs"
        if include_workbench and wb.exists():
            wstats = self._import_runs_dir(
                wb, app="packing", source="workbench-bridge", base=wb.parent.parent,
                session_fallback="civil-buddy-sidecar",
            )
            stats["workbench_runs"] = wstats["runs_imported"]
            stats["events"] += wstats["events_imported"]
        # 4) workbench(Rust) Run 对象（demo/out/<sid>/runs/<rid>/trace.json）
        if include_demo:
            demo_root = repo_root() / "demo" / "out"
            if demo_root.exists():
                stats["demo_runs"] = self._import_demo_runs(demo_root)
        # 5) scores：phase0 / posts / kb SCORECARD（按 kind+case_id 覆盖，导入幂等）
        for kind, sub in (("phase0", "phase0"), ("post", "posts")):
            d = out_dir / sub
            if not d.exists():
                continue
            for p in sorted(d.glob("*.json")):
                try:
                    detail = json.loads(p.read_text(encoding="utf-8"))
                    with self._lock:
                        self.write_conn.execute(
                            "DELETE FROM scores WHERE kind=? AND case_id=?", (kind, p.stem)
                        )
                    self.add_score(kind, p.stem, detail={"file": p.name, "data": detail})
                    stats["scores"] += 1
                except Exception:
                    stats["skipped"] += 1
        sc = out_dir / "kb" / "SCORECARD.md"
        if sc.exists():
            try:
                with self._lock:
                    self.write_conn.execute(
                        "DELETE FROM scores WHERE kind=? AND case_id=?", ("kb_scorecard", "latest")
                    )
                self.add_score("kb_scorecard", "latest", detail={"raw": sc.read_text(encoding="utf-8")[:200000]})
                stats["scores"] += 1
            except Exception:
                pass
        return stats

    def _import_runs_dir(self, runs_dir: Path, *, app: str, source: str,
                         base: Path, session_fallback: str | None = None) -> dict:
        n_runs = 0
        n_events = 0
        for d in sorted(runs_dir.iterdir()):
            if not d.is_dir():
                continue
            rid = d.name
            cp: dict = {}
            cp_path = d / "checkpoint.json"
            if cp_path.exists():
                try:
                    cp = json.loads(cp_path.read_text(encoding="utf-8"))
                except Exception:
                    cp = {}
            state: dict = {}
            st_path = d / "session_state.json"
            if st_path.exists():
                try:
                    state = json.loads(st_path.read_text(encoding="utf-8"))
                except Exception:
                    state = {}
            sid = str(cp.get("session_id") or state.get("session_id") or session_fallback or "")
            first_ev: dict = {}
            events: List[dict] = []
            trace = d / "trace.jsonl"
            if trace.exists():
                with trace.open(encoding="utf-8", errors="ignore") as f:
                    for i, line in enumerate(f):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            ev = json.loads(line)
                        except Exception:
                            continue
                        if not first_ev:
                            first_ev = ev
                        ev.setdefault("run_id", rid)
                        ev.setdefault("seq", i + 1)
                        events.append(ev)
            # 会话行先于 run 行（FK：runs.session_id → sessions.session_id）
            had_session = bool(sid and self.load_session(sid))
            if sid and not had_session:
                if state:
                    state.setdefault("session_id", sid)
                    state.setdefault("run_id", rid)
                    self.save_session(sid, state, meta=cp or None)
                else:
                    self.save_session(sid, {"session_id": sid, "run_id": rid,
                                            "_imported_index_only": True})
            self.upsert_run(
                {
                    "run_id": rid,
                    "session_id": sid or None,
                    "app": app,
                    "source": source,
                    "expert_id": state.get("expert_id") or first_ev.get("expert_id"),
                    "category": state.get("category"),
                    "started_at": first_ev.get("ts") or cp.get("saved_at"),
                    "ended_at": (events[-1].get("ts") if events else None) or cp.get("saved_at"),
                    "phase": cp.get("phase") or state.get("phase"),
                    "status": cp.get("status"),
                    "container_type": cp.get("container_type") or state.get("container_type"),
                    "n_boxes": cp.get("n_boxes"),
                    "checkpoint_json": (json.dumps(cp, ensure_ascii=False, default=str) if cp else None),
                    "run_dir": str(d),
                }
            )
            # 会话行兜底补全：已有索引行则用完整 state 升级
            if state and sid and not had_session:
                state.setdefault("session_id", sid)
                state.setdefault("run_id", rid)
                self.save_session(sid, state, meta=cp or None)
            with self._lock:
                self.write_conn.execute("DELETE FROM events WHERE run_id=?", (rid,))
            self.insert_events(events)
            # 决策冗余表（/api/audit 置顶区直查源）；先清同 run 旧行保证导入幂等
            action = str(cp.get("user_action") or "") if isinstance(cp, dict) else ""
            if action in ("confirm", "cancel") and sid:
                auto = bool(first_ev.get("enable_auto_confirm"))
                with self._lock:
                    self.write_conn.execute("DELETE FROM audit_decisions WHERE run_id=?", (rid,))
                self.add_decision(
                    session_id=sid, run_id=rid, action=action,
                    operator="引擎（自动确认）" if (action == "confirm" and auto) else "本地用户",
                    ts=cp.get("saved_at"),
                    detail={"status": cp.get("status"), "phase": cp.get("phase"), "auto_confirm": auto},
                )
            n_runs += 1
            n_events += len(events)
        return {"runs_imported": n_runs, "events_imported": n_events}

    def _import_demo_runs(self, demo_root: Path) -> int:
        """demo/out/<sid>/runs/<rid>/trace.json（Rust Run 对象）→ runs + 合成 events。

        type 映射（导入器写死，data-plan §1.3）：Run.steps[i] → tool_end（含 expert/tool/ok/note）。
        """
        n = 0
        for trace in sorted(demo_root.glob("*/*/runs/*/trace.json")):
            try:
                run = json.loads(trace.read_text(encoding="utf-8"))
            except Exception:
                continue
            rid = str(run.get("run_id") or trace.parent.name)
            sid = str(run.get("session_id") or trace.parents[3].name)
            steps = run.get("steps") or []
            events = []
            for i, st in enumerate(steps):
                if not isinstance(st, dict):
                    continue
                ok = bool(st.get("ok"))
                events.append(
                    {
                        "run_id": rid,
                        "seq": i + 1,
                        "type": "tool_end",
                        "node": st.get("expert"),
                        "agent_id": st.get("expert"),
                        "tool": st.get("tool") or st.get("name"),
                        "status": "ok" if ok else "error",
                        "ts": None,
                        "t_ms": None,
                        "duration_ms": None,
                        "payload": st,
                        "schema": "workbench.run.step.v1",
                    }
                )
            if sid and not self.load_session(sid):
                self.save_session(sid, {"session_id": sid, "run_id": rid, "app": "workbench",
                                        "_imported_index_only": True})
            self.upsert_run(
                {
                    "run_id": rid,
                    "session_id": sid,
                    "app": "workbench",
                    "source": "demo",
                    "started_at": None,
                    "ended_at": None,
                    "phase": "done" if run.get("ok") else None,
                    "status": "done" if run.get("ok") else None,
                    "checkpoint_json": json.dumps(
                        {k: run.get(k) for k in ("intent", "mode", "jurisdiction")}, ensure_ascii=False
                    ),
                    "run_dir": str(trace.parent),
                }
            )
            if sid and not self.load_session(sid):
                self.save_session(sid, {"session_id": sid, "run_id": rid, "app": "workbench",
                                        "_imported_index_only": True})
            with self._lock:
                self.write_conn.execute("DELETE FROM events WHERE run_id=?", (rid,))
            self.insert_events(events)
            n += 1
        return n

    # ---------- 导出（JSON 作为导出格式） ----------

    def export_json(self, out_dir: Path) -> None:
        """DB → output/ 布局全量回导（DB 是权威、JSON 是快照）。"""
        out_dir = Path(out_dir)
        (out_dir / "sessions").mkdir(parents=True, exist_ok=True)
        (out_dir / "runs").mkdir(parents=True, exist_ok=True)
        for sid, state_json in self._read_conn().execute(
            "SELECT session_id, state_json FROM sessions"
        ).fetchall():
            try:
                st = json.loads(state_json)
            except Exception:
                continue
            rid = str(st.get("run_id") or sid)
            meta = st.get("_checkpoint") if isinstance(st.get("_checkpoint"), dict) else {}
            idx = {
                "session_id": sid,
                "thread_id": sid,
                "run_id": rid,
                "phase": st.get("phase"),
                "status": meta.get("status"),
                "interrupt": meta.get("interrupt"),
                "saved_at": st.get("_session_saved_at"),
                "n_boxes": meta.get("n_boxes"),
                "container_type": st.get("container_type"),
                "schema": "packing.checkpoint.v1",
            }
            (out_dir / "sessions" / f"{sid}.json").write_text(
                json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            rd = out_dir / "runs" / rid
            rd.mkdir(parents=True, exist_ok=True)
            (rd / "session_state.json").write_text(
                json.dumps(st, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
            if meta:
                (rd / "checkpoint.json").write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
                )
            events = self.read_trace_events(rid, limit=10**9)
            if events:
                with (rd / "trace.jsonl").open("w", encoding="utf-8") as f:
                    for ev in events:
                        f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")

    # ---------- 诊断 ----------

    def stats(self) -> dict:
        tables = ("sessions", "runs", "events", "audit_decisions", "scores",
                  "kb_index", "kb_chunks", "checkpoints")
        out: Dict[str, Any] = {
            "db_path": str(self.db_path),
            "user_version": self.user_version(),
            "size_bytes": self.db_path.stat().st_size if self.db_path.exists() else 0,
        }
        for t in tables:
            try:
                out[t] = self._read_conn().execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except Exception:
                out[t] = None
        out["archived_runs"] = self._read_conn().execute(
            "SELECT COUNT(*) FROM runs WHERE archived=1"
        ).fetchone()[0]
        return out


# ---------- 模块级单例 ----------

_STORAGE: Optional[Storage] = None
_STORAGE_LOCK = threading.Lock()


def get_storage() -> Storage:
    global _STORAGE
    with _STORAGE_LOCK:
        if _STORAGE is None:
            _STORAGE = Storage()
        return _STORAGE


def reset_storage() -> None:
    """测试用：关闭并丢弃单例（env 改变后重建）。"""
    global _STORAGE
    with _STORAGE_LOCK:
        if _STORAGE is not None:
            _STORAGE.close()
        _STORAGE = None


# ---------- CLI：python -m packing_assistant.storage --import|--stats|--prune ----------

def _main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Civil Buddy SQLite 持久层维护 CLI")
    ap.add_argument("--import", dest="do_import", action="store_true", help="导入存量 JSON（幂等）")
    ap.add_argument("--with-demo", action="store_true", help="导入器附带 demo/out Rust Run 对象")
    ap.add_argument("--stats", action="store_true", help="打印库统计")
    ap.add_argument("--prune", action="store_true", help="软删除归档（默认 dry-run，加 --apply 执行）")
    ap.add_argument("--days", type=int, default=90, help="prune 保留天数（默认 90）")
    ap.add_argument("--apply", action="store_true", help="prune 真执行（默认只列数量）")
    ap.add_argument("--backup", action="store_true", help="VACUUM INTO 备份一份")
    ap.add_argument("--db", default=None, help="覆盖 CB_DB_PATH")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    st = Storage(Path(args.db) if args.db else None)
    if args.do_import:
        t0 = time.time()
        res = st.import_json(include_demo=args.with_demo)
        res["elapsed_s"] = round(time.time() - t0, 1)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    if args.stats:
        print(json.dumps(st.stats(), ensure_ascii=False, indent=2))
    if args.prune:
        cutoff = time.strftime(
            "%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - args.days * 86400)
        )
        n = st._read_conn().execute(
            "SELECT COUNT(*) FROM runs WHERE archived=0 AND COALESCE(started_at,'') < ?",
            (cutoff,),
        ).fetchone()[0]
        if args.apply:
            kept = st.prune(keep_days=args.days)
            print(f"prune applied: archived {kept} runs (older than {cutoff})")
        else:
            print(f"prune dry-run: {n} runs older than {cutoff} would be archived (--apply 执行)")
    if args.backup:
        p = st.backup()
        print(f"backup -> {p}")
    if not (args.do_import or args.stats or args.prune or args.backup):
        ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
