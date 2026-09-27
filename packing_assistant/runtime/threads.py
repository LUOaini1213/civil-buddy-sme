"""Codex-app threads: one session per thread, parallel across threads.

Admission is serial per thread, including queued background turns. /new and /bg
get a new session_id. Thread snapshots are replaced atomically on disk.

Next to each snapshot is the thread's rollout, ``<thread_id>.rollout.jsonl``: one line per
message, appended after every turn. ``civil resume`` hands the tail of it to a model-driven
turn as conversation history, so "接着上次的说" means something.
"""

from __future__ import annotations

import json
import math
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path, PureWindowsPath
from threading import Lock
from typing import Any, Dict, List, Optional, Set
from uuid import uuid4

_ROOT = Path(__file__).resolve().parents[2]
from packing_assistant.runtime.paths import default_out_root
_DIR = default_out_root(_ROOT) / "_threads"
_LOCK = Lock()
_POOL: Optional[ThreadPoolExecutor] = None
_ACTIVE: Set[str] = set()


@dataclass
class CivilThread:
    thread_id: str
    session_id: str
    title: str = ""
    skill: str = ""
    state: str = "idle"
    confirm: bool = False
    last_text: str = ""
    last_reply: str = ""
    hitl_pending: bool = False
    wrote: bool = False
    artifacts: List[str] = field(default_factory=list)
    error: str = ""
    worktree: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _pool() -> ThreadPoolExecutor:
    global _POOL
    if _POOL is None:
        from packing_assistant.runtime.civil_config import load_config

        n = load_config().max_parallel
        _POOL = ThreadPoolExecutor(max_workers=n, thread_name_prefix="civil-th")
    return _POOL


def _path(thread_id: str) -> Path:
    if not isinstance(thread_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", thread_id):
        raise ValueError("invalid thread id")
    if PureWindowsPath(f"{thread_id}.json").is_reserved():
        raise ValueError("invalid thread id")
    return _DIR / f"{thread_id}.json"


def save_thread(th: CivilThread) -> None:
    path = _path(th.thread_id)
    _DIR.mkdir(parents=True, exist_ok=True)
    th.updated_at = time.time()
    temporary: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=_DIR, prefix=f".{th.thread_id}-", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(th.to_dict(), stream, ensure_ascii=False, indent=2)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_thread(thread_id: str) -> Optional[CivilThread]:
    try:
        p = _path(thread_id)
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    if not isinstance(raw, dict) or raw.get("thread_id") != thread_id:
        return None
    if any(key in raw and not isinstance(raw[key], bool) for key in ("confirm", "hitl_pending", "wrote")):
        return None
    worktree = raw.get("worktree") or ""
    if not isinstance(worktree, str):
        return None
    artifacts = raw.get("artifacts") or []
    if not isinstance(artifacts, list) or not all(isinstance(item, str) for item in artifacts):
        return None
    try:
        created_at = float(raw.get("created_at") or 0)
        updated_at = float(raw.get("updated_at") or 0)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(created_at) or not math.isfinite(updated_at):
        return None
    return CivilThread(
        thread_id=thread_id,
        session_id=str(raw.get("session_id") or thread_id),
        title=str(raw.get("title") or ""),
        skill=str(raw.get("skill") or ""),
        state=str(raw.get("state") or "idle"),
        confirm=bool(raw.get("confirm")),
        last_text=str(raw.get("last_text") or ""),
        last_reply=str(raw.get("last_reply") or ""),
        hitl_pending=bool(raw.get("hitl_pending")),
        wrote=bool(raw.get("wrote")),
        artifacts=artifacts,
        error=str(raw.get("error") or ""),
        worktree=worktree,
        created_at=created_at,
        updated_at=updated_at,
    )


ROLLOUT_MESSAGES = 12
ROLLOUT_CHARS = 1500


def _rollout_path(thread_id: str) -> Path:
    return _path(thread_id).with_suffix(".rollout.jsonl")


def append_rollout(thread_id: str, role: str, content: str, **extra: Any) -> None:
    """Best effort: a full disk must not turn a finished turn into a failed one."""
    try:
        path = _rollout_path(thread_id)
        _DIR.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"ts": round(time.time(), 3), "role": role, "content": content, **extra}, ensure_ascii=False)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
    except (OSError, ValueError):
        pass


def load_rollout(thread_id: str, *, limit: int = ROLLOUT_MESSAGES, chars: int = ROLLOUT_CHARS) -> List[Dict[str, str]]:
    """The last ``limit`` user/assistant messages, each cut to ``chars`` — history, not an archive."""
    try:
        lines = _rollout_path(thread_id).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError, UnicodeError):
        return []
    messages: List[Dict[str, str]] = []
    for raw in lines[-4 * max(1, limit):]:
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("role") in {"user", "assistant"} and isinstance(row.get("content"), str):
            messages.append({"role": row["role"], "content": row["content"][:chars]})
    return messages[-max(1, limit):]


def list_threads() -> List[CivilThread]:
    if not _DIR.is_dir():
        return []
    out: List[CivilThread] = []
    for p in _DIR.glob("*.json"):
        th = load_thread(p.stem)
        if th:
            out.append(th)
    out.sort(key=lambda th: th.updated_at, reverse=True)
    return out


def new_thread(title: str = "", *, confirm: bool = False, worktree: str = "") -> CivilThread:
    tid = f"t-{uuid4().hex[:8]}"
    th = CivilThread(
        thread_id=tid,
        session_id=tid,
        title=(title or "新对话").strip()[:80],
        confirm=False,  # approvals belong to an operation, never to a persisted thread
        worktree=(worktree or "").strip(),
    )
    save_thread(th)
    return th


def _failed(th: CivilThread, exc: Exception, *, code: str) -> Dict[str, Any]:
    th.state = "failed"
    th.error = str(exc)
    th.last_reply = str(exc)
    result = {"ok": False, "error": str(exc), "reply": str(exc), "error_code": code, "thread_id": th.thread_id}
    try:
        save_thread(th)
    except OSError as storage_error:
        result["storage_error"] = str(storage_error)
    return result


def _run_on_thread(th: CivilThread, text: str, *, skill: str, confirm: bool, approve: Any = None) -> Dict[str, Any]:
    try:
        from packing_assistant.runtime.turn import run_turn
        from packing_assistant.runtime.workspace_ctx import worktree_scope

        with worktree_scope(th.worktree):
            out = run_turn(
                text,
                session_id=th.session_id,
                skill=skill,
                confirm=confirm is True,
                history=load_rollout(th.thread_id),
                approve=approve,
            )
        th.skill = str(out.get("skill") or out.get("expert_id") or th.skill)
        th.last_reply = str(out.get("reply") or "")
        th.hitl_pending = bool(out.get("hitl_pending"))
        th.wrote = bool(out.get("wrote"))
        arts = out.get("artifacts") or out.get("files") or []
        th.artifacts = [str(a) for a in arts]
        th.state = ("cancelled" if out.get("cancelled") or out.get("state") == "cancelled" else
                    "waiting_hitl" if th.hitl_pending else ("done" if out.get("ok") else "failed"))
        th.error = str(out.get("error") or out.get("error_code") or "")
        save_thread(th)
        append_rollout(th.thread_id, "user", text)
        append_rollout(th.thread_id, "assistant", th.last_reply, skill=th.skill, files=th.artifacts,
                       agent_mode=str(out.get("agent_mode") or ""))
        return {**out, "thread_id": th.thread_id}
    except Exception as exc:  # noqa: BLE001
        return _failed(th, exc, code="thread_execution_failed")
    finally:
        with _LOCK:
            _ACTIVE.discard(th.thread_id)


def run_on_thread(
    thread_id: str,
    text: str,
    *,
    skill: str = "",
    confirm: bool = False,
    background: bool = False,
    approve: Any = None,
) -> Dict[str, Any]:
    """``approve(request) -> bool`` answers a high-risk write prompt inline; foreground turns only."""
    with _LOCK:
        th = load_thread(thread_id)
        if th is None:
            return {"ok": False, "error": "unknown thread", "error_code": "unknown_thread", "thread_id": thread_id}
        if thread_id in _ACTIVE:
            return {"ok": False, "error": "thread is busy", "error_code": "thread_busy", "thread_id": thread_id}
        _ACTIVE.add(thread_id)
        th.confirm = False  # discard legacy thread-wide approval before any new operation
        th.state = "running"
        th.last_text = text
        th.last_reply = ""
        th.hitl_pending = False
        th.wrote = False
        th.artifacts = []
        th.error = ""
        if not th.title or th.title == "新对话":
            th.title = (text or "").replace("\n", " ")[:40] or th.title
        try:
            save_thread(th)
            if background:
                _pool().submit(_run_on_thread, th, text, skill=skill, confirm=confirm)
        except Exception as exc:  # noqa: BLE001
            _ACTIVE.discard(thread_id)
            return _failed(th, exc, code="thread_start_failed")
        if background:
            return {"ok": True, "background": True, "thread_id": thread_id, "state": "running"}
    return _run_on_thread(th, text, skill=skill, confirm=confirm, approve=approve)


def spawn(text: str, *, skill: str = "", confirm: bool = False, title: str = "", worktree: str = "") -> Dict[str, Any]:
    th = new_thread(title or text, confirm=confirm, worktree=worktree)
    return run_on_thread(th.thread_id, text, skill=skill, confirm=confirm, background=True)


def thread_status(thread_id: str) -> Dict[str, Any]:
    with _LOCK:
        th = load_thread(thread_id)
        if not th:
            return {"ok": False, "error": "unknown thread", "error_code": "unknown_thread", "thread_id": thread_id}
        running = thread_id in _ACTIVE
    if running:
        th.state = "running"
    return {"ok": True, **th.to_dict(), "running": running}
