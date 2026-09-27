"""Durable HITL checkpoint（文件持久化 · 可进程重启 resume）。

data(round2) 起改为 storage 薄壳：CB_STORAGE 三态分派（packing_assistant/storage.py）：
  json   = 纯 JSON 路径（本文件原实现，与 6df7e1c 等价）
  dual   = JSON 先写（权威），SQLite 双写尽力而为（失败仅告警）
  sqlite = 只写 SQLite；读 SQLite 优先、无则回退 JSON（旧数据可读）

路径（JSON 格式保留为导出/回滚通道）:
  output/runs/<run_id>/session_state.json   # 完整 state
  output/runs/<run_id>/checkpoint.json      # 轻量 interrupt 元数据
  output/sessions/<session_id>.json         # session → run_id 索引
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from packing_assistant.config import TRACE_DIR
from packing_assistant import storage as _storage

logger = logging.getLogger("civil.session_store")

RUNS_DIR = Path(TRACE_DIR).resolve().parent / "runs"
SESSIONS_DIR = Path(TRACE_DIR).resolve().parent / "sessions"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_name(session_id: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in str(session_id))[:180]


def session_state_path(run_id: str) -> Path:
    d = RUNS_DIR / str(run_id)
    d.mkdir(parents=True, exist_ok=True)
    return d / "session_state.json"


def checkpoint_meta_path(run_id: str) -> Path:
    d = RUNS_DIR / str(run_id)
    d.mkdir(parents=True, exist_ok=True)
    return d / "checkpoint.json"


def session_index_path(session_id: str) -> Path:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    return SESSIONS_DIR / f"{_safe_name(session_id)}.json"


def _atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    fd, tmp = tempfile.mkstemp(prefix=".ckpt_", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _checkpoint_status(state: Dict[str, Any]) -> str:
    phase = str(state.get("phase") or "")
    action = str(state.get("user_action") or "")
    if phase == "await_user_confirm":
        return "interrupted"
    if phase == "cancelled" or action == "cancel":
        return "cancelled"
    if phase in ("done", "team_b_done") or action == "confirm":
        # confirm 后可能仍在跑 B；finalize 后 phase 多为 done
        if phase == "await_user_confirm":
            return "interrupted"
        return "done" if phase in ("done", "cancelled") or state.get("final_response") else "resumed"
    if state.get("final_response") and phase not in ("await_user_confirm",):
        return "done"
    return "running"


def build_checkpoint_meta(state: Dict[str, Any]) -> Dict[str, Any]:
    sid = str(state.get("session_id") or "default")
    rid = str(state.get("run_id") or sid)
    status = _checkpoint_status(state)
    boxes = state.get("boxes") or []
    return {
        "schema": "packing.checkpoint.v1",
        "thread_id": sid,
        "session_id": sid,
        "run_id": rid,
        "phase": state.get("phase"),
        "status": status,
        "interrupt": status == "interrupted",
        "user_action": state.get("user_action"),
        "container_type": state.get("container_type"),
        "n_boxes": len(boxes),
        "n_materials": len(state.get("materials") or []),
        "packing_plan_id": state.get("packing_plan_id"),
        "saved_at": state.get("_session_saved_at") or _now_iso(),
        "resume": {
            "endpoint": "POST /api/confirm",
            "or": "POST /api/checkpoints/{thread_id}/resume",
            "body": {
                "session_id": sid,
                "action": "confirm",
                "container_type": state.get("container_type") or "40HQ",
                "max_containers": int(state.get("max_containers") or 0),
            },
        },
        "paths": {
            "session_state": str(session_state_path(rid)),
            "checkpoint": str(checkpoint_meta_path(rid)),
        },
    }


def _db_write(sid: str, rid: str, s: Dict[str, Any], meta: Dict[str, Any]) -> None:
    """SQLite 侧写入：sessions 行 + runs 行（双写期 runs 表自维护，不依赖重导入）。"""
    st = _storage.get_storage()
    st.save_session(sid, s, meta=meta)
    st.upsert_run(
        {
            "run_id": rid,
            "session_id": sid,
            "app": "packing",
            "source": "gateway",
            "started_at": meta.get("saved_at"),
            "phase": s.get("phase"),
            "status": meta.get("status"),
            "container_type": s.get("container_type"),
            "n_boxes": meta.get("n_boxes"),
            "checkpoint_json": json.dumps(meta, ensure_ascii=False, default=str),
            "run_dir": str(RUNS_DIR / rid),
        }
    )


class SessionPersistError(RuntimeError):
    """SQLite stayed locked through every retry, so the session was not saved.

    No JSON copy is written in its place: load_session reads SQLite first, so a JSON-only save would be
    read back as the older SQLite row after a restart (the two stores would disagree). The previously
    saved state stays intact in both; the caller reports the failure.
    """


def _write_attempts() -> int:
    try:
        return max(1, int(os.getenv("CB_SQLITE_WRITE_ATTEMPTS") or 3))
    except ValueError:
        return 3


#: pause before the 2nd, 3rd, ... attempt; each attempt itself already waits busy_timeout for the lock
_RETRY_BACKOFF_S = (0.2, 0.5, 1.0)


def _db_write_retrying(sid: str, rid: str, s: Dict[str, Any], meta: Dict[str, Any]) -> None:
    """_db_write, retried a bounded number of times while another writer holds the lock."""
    attempts = _write_attempts()
    for attempt in range(1, attempts + 1):
        try:
            _db_write(sid, rid, s, meta)
            return
        except Exception as exc:
            if not _storage.is_lock_error(exc):
                raise
            try:
                _storage.get_storage().rollback_write()
            except Exception:
                logger.warning("rollback after a locked write failed", exc_info=True)
            if attempt == attempts:
                raise SessionPersistError(
                    f"session {sid} was not saved: the database stayed locked through {attempts} attempts"
                    f" of {_storage.busy_timeout_ms()} ms each ({exc})"
                ) from exc
            logger.warning("sqlite save_session: database locked (attempt %d of %d), retrying", attempt, attempts)
            time.sleep(_RETRY_BACKOFF_S[min(attempt - 1, len(_RETRY_BACKOFF_S) - 1)])


def save_session(session_id: str, state: Dict[str, Any]) -> Dict[str, str]:
    """Persist full pipeline state for later /api/confirm resume.

    In sqlite mode a lock that outlasts every retry raises SessionPersistError instead of quietly
    writing JSON only (see that class)."""
    sid = str(session_id or state.get("session_id") or "default")
    rid = str(state.get("run_id") or sid)
    s = dict(state)
    s.setdefault("session_id", sid)
    s.setdefault("run_id", rid)
    s["_session_saved_at"] = _now_iso()
    meta = build_checkpoint_meta(s)
    s["_checkpoint"] = meta

    mode = _storage.storage_mode()
    if mode == "sqlite":
        try:
            _db_write_retrying(sid, rid, s, meta)
        except SessionPersistError:
            logger.error("sqlite save_session failed: session %s NOT saved (database locked)", sid, exc_info=True)
            raise
        except Exception:
            logger.warning("sqlite save_session failed, fallback to JSON", exc_info=True)
        else:
            return {
                "session_id": sid,
                "thread_id": sid,
                "run_id": rid,
                "path": str(session_state_path(rid)),
                "status": str(meta.get("status")),
                "interrupt": bool(meta.get("interrupt")),
            }

    _atomic_write_json(session_state_path(rid), s)
    _atomic_write_json(checkpoint_meta_path(rid), meta)

    idx = {
        "session_id": sid,
        "thread_id": sid,
        "run_id": rid,
        "phase": s.get("phase"),
        "status": meta.get("status"),
        "interrupt": meta.get("interrupt"),
        "saved_at": s["_session_saved_at"],
        "n_boxes": meta.get("n_boxes"),
        "container_type": s.get("container_type"),
        "session_state": str(session_state_path(rid)),
        "checkpoint": str(checkpoint_meta_path(rid)),
        "schema": "packing.checkpoint.v1",
    }
    _atomic_write_json(session_index_path(sid), idx)
    if rid != sid:
        _atomic_write_json(session_index_path(rid), idx)

    if mode == "dual":
        try:
            _db_write(sid, rid, s, meta)
        except Exception:
            logger.warning("dual write to sqlite failed (non-blocking)", exc_info=True)

    return {
        "session_id": sid,
        "thread_id": sid,
        "run_id": rid,
        "path": str(session_state_path(rid)),
        "status": str(meta.get("status")),
        "interrupt": bool(meta.get("interrupt")),
    }


def load_session(session_id: str) -> Optional[Dict[str, Any]]:
    """Load state by session_id / thread_id / run_id; None if missing."""
    sid = str(session_id or "").strip()
    if not sid:
        return None

    if _storage.storage_mode() == "sqlite":
        try:
            st = _storage.get_storage().load_session(sid)
            if st is not None:
                return st
        except Exception:
            logger.warning("sqlite load_session failed, fallback to JSON", exc_info=True)

    idx_path = session_index_path(sid)
    if idx_path.exists():
        try:
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
            rid = str(idx.get("run_id") or sid)
            p = Path(idx.get("session_state") or session_state_path(rid))
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass

    p2 = session_state_path(sid)
    if p2.exists():
        try:
            return json.loads(p2.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def load_checkpoint_meta(session_id: str) -> Optional[Dict[str, Any]]:
    if _storage.storage_mode() == "sqlite":
        try:
            meta = _storage.get_storage().load_checkpoint_meta(session_id)
            if meta:
                return meta
        except Exception:
            logger.warning("sqlite load_checkpoint_meta failed, fallback to JSON", exc_info=True)
    state = load_session(session_id)
    if not state:
        # try meta-only via index
        idx_path = session_index_path(session_id)
        if idx_path.exists():
            try:
                idx = json.loads(idx_path.read_text(encoding="utf-8"))
                cp = Path(idx.get("checkpoint") or "")
                if cp.exists():
                    return json.loads(cp.read_text(encoding="utf-8"))
                return idx
            except Exception:
                return None
        return None
    return state.get("_checkpoint") or build_checkpoint_meta(state)


def session_exists(session_id: str) -> bool:
    return load_session(session_id) is not None


def list_checkpoints(
    *,
    limit: int = 50,
    pending_hitl_only: bool = False,
) -> List[Dict[str, Any]]:
    """List checkpoint indexes（sqlite 模式 SQL 直查，否则扫 output/sessions/）。"""
    if _storage.storage_mode() == "sqlite":
        try:
            items = _storage.get_storage().list_checkpoints(
                limit=limit, pending_hitl_only=pending_hitl_only
            )
            if items:
                return items
        except Exception:
            logger.warning("sqlite list_checkpoints failed, fallback to JSON", exc_info=True)

    if not SESSIONS_DIR.exists():
        return []
    items: List[Dict[str, Any]] = []
    seen_run: set = set()
    files = sorted(SESSIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files:
        try:
            idx = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        rid = str(idx.get("run_id") or "")
        if rid and rid in seen_run:
            continue
        if rid:
            seen_run.add(rid)
        # refresh status from live meta if present
        status = idx.get("status")
        interrupt = idx.get("interrupt")
        phase = idx.get("phase")
        cp_path = Path(idx.get("checkpoint") or "")
        if cp_path.exists():
            try:
                meta = json.loads(cp_path.read_text(encoding="utf-8"))
                status = meta.get("status", status)
                interrupt = meta.get("interrupt", interrupt)
                phase = meta.get("phase", phase)
                idx = {**idx, **{k: meta.get(k) for k in ("n_boxes", "container_type", "resume") if meta.get(k) is not None}}
            except Exception:
                pass
        if pending_hitl_only and not (
            interrupt or status == "interrupted" or phase == "await_user_confirm"
        ):
            continue
        items.append(
            {
                "thread_id": idx.get("thread_id") or idx.get("session_id"),
                "session_id": idx.get("session_id"),
                "run_id": rid,
                "phase": phase,
                "status": status,
                "interrupt": bool(interrupt or status == "interrupted"),
                "saved_at": idx.get("saved_at"),
                "n_boxes": idx.get("n_boxes"),
                "container_type": idx.get("container_type"),
                "resume": idx.get("resume"),
                "index_path": str(p),
            }
        )
        if len(items) >= limit:
            break
    return items


def mark_checkpoint(
    session_id: str,
    *,
    status: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Update checkpoint status after resume/cancel (keeps full state)."""
    state = load_session(session_id)
    if not state:
        return None
    state = dict(state)
    if status == "cancelled":
        state["phase"] = "cancelled"
        state["user_action"] = "cancel"
    elif status == "resumed":
        state["user_action"] = state.get("user_action") or "confirm"
        if state.get("phase") == "await_user_confirm":
            state["phase"] = "team_b_running"
    elif status == "done":
        state.setdefault("phase", "done")
    if extra:
        state.update(extra)
    return save_session(session_id, state)


def delete_checkpoint(session_id: str) -> bool:
    """Remove index pointers (does not delete run artifacts)."""
    sid = str(session_id or "").strip()
    if not sid:
        return False
    removed = False
    state = load_session(sid)
    if _storage.storage_mode() == "sqlite":
        try:
            removed = _storage.get_storage().delete_sessions([sid, str((state or {}).get("run_id") or "")]) > 0
        except Exception:
            logger.warning("sqlite delete_sessions failed", exc_info=True)
    for key in {sid, str((state or {}).get("run_id") or "")}:
        if not key:
            continue
        p = session_index_path(key)
        if p.exists():
            try:
                p.unlink()
                removed = True
            except OSError:
                pass
    return removed
