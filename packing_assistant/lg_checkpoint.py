"""LangGraph 持久化 checkpointer（Sqlite · 断进程可 resume）。

环境变量:
  PACKING_LG_CHECKPOINT=1     启用（默认 1）
  PACKING_LG_CHECKPOINT_PATH  sqlite 路径（默认 data/civilbuddy.db，与业务表同库共存——
                              data-plan §1.1/audit C2：SqliteSaver setup() 只 CREATE TABLE
                              IF NOT EXISTS 自己的 checkpoints/writes 等表，互不冲突）

data(round2)：修复假 durable——此前未装 langgraph-checkpoint-sqlite 时静默回退
MemorySaver（进程重启即丢）。现在：装包即 SqliteSaver；回退必打 WARNING。

用法:
  app = create_team_a_app(checkpointer=get_checkpointer())
  app.invoke(state, config=thread_config(session_id))
  snap = get_thread_state(session_id)  # 重启后读取
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from packing_assistant.storage import default_db_path

logger = logging.getLogger("civil.lg_checkpoint")

_LOCK = threading.Lock()
_SAVER = None
_CONN = None


def checkpoint_enabled() -> bool:
    v = (os.getenv("PACKING_LG_CHECKPOINT") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def checkpoint_db_path() -> Path:
    raw = (os.getenv("PACKING_LG_CHECKPOINT_PATH") or "").strip()
    if raw:
        return Path(raw)
    # 默认与业务库同文件（data/civilbuddy.db）；旧默认 output/langgraph_checkpoints.db 弃用
    return default_db_path()


def get_checkpointer():
    """单例 SqliteSaver；不可用或关闭时返回 None（回退 MemorySaver 时打 WARNING）。"""
    global _SAVER, _CONN
    if not checkpoint_enabled():
        return None
    with _LOCK:
        if _SAVER is not None:
            return _SAVER
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
        except Exception:
            logger.warning(
                "langgraph-checkpoint-sqlite 未安装：LangGraph checkpoint 回退 MemorySaver"
                "（进程重启即丢，durable 语义由 session_store JSON/SQLite 承担）。"
                "修复：pip install langgraph-checkpoint-sqlite",
                exc_info=True,
            )
            try:
                from langgraph.checkpoint.memory import MemorySaver

                _SAVER = MemorySaver()
                return _SAVER
            except Exception:
                return None
        path = checkpoint_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # SqliteSaver 需要保持连接存活；连接纪律与 storage 一致（WAL + busy_timeout）
        _CONN = sqlite3.connect(str(path), check_same_thread=False, timeout=5.0)
        _CONN.execute("PRAGMA journal_mode=WAL")
        _CONN.execute("PRAGMA busy_timeout=5000")
        _SAVER = SqliteSaver(_CONN)
        try:
            _SAVER.setup()  # CREATE TABLE IF NOT EXISTS checkpoints/writes/...（幂等）
        except Exception:
            logger.warning("SqliteSaver.setup() failed（首次写入时会重试）", exc_info=True)
        return _SAVER


def thread_config(thread_id: str, *, checkpoint_ns: str = "") -> Dict[str, Any]:
    tid = str(thread_id or "default")
    cfg: Dict[str, Any] = {"configurable": {"thread_id": tid}}
    if checkpoint_ns:
        cfg["configurable"]["checkpoint_ns"] = checkpoint_ns
    return cfg


def invoke_with_checkpoint(app: Any, state: Dict[str, Any], thread_id: str) -> Dict[str, Any]:
    """invoke 并写入 LangGraph checkpoint。"""
    cp = get_checkpointer()
    config = thread_config(thread_id)
    if cp is None:
        return app.invoke(state)
    # No fallback: the old `except TypeError / except Exception: return app.invoke(state)` caught the node's own
    # error and re-invoked without a thread_id, so a compiled graph with a checkpointer raised an unrelated
    # "Checkpointer requires ... thread_id" ValueError and the real failure was lost. A compiled graph accepts
    # config with or without a checkpointer, so the node's error now reaches the caller as it was raised.
    return app.invoke(state, config=config)


def get_thread_state(thread_id: str, app: Any = None) -> Optional[Dict[str, Any]]:
    """从 checkpointer 取最新 values（需传入已 compile 的 app）。"""
    cp = get_checkpointer()
    if cp is None or app is None:
        return None
    try:
        snap = app.get_state(thread_config(thread_id))
        if snap is None:
            return None
        values = getattr(snap, "values", None) or (snap.get("values") if isinstance(snap, dict) else None)
        if isinstance(values, dict) and values:
            return dict(values)
    except Exception:
        return None
    return None


def list_thread_ids(*, limit: int = 50) -> list:
    """尽力列出 sqlite 中的 thread（失败则空）。"""
    path = checkpoint_db_path()
    if not path.exists():
        return []
    try:
        conn = sqlite3.connect(str(path))
        cur = conn.execute(
            "SELECT DISTINCT thread_id FROM checkpoints ORDER BY rowid DESC LIMIT ?",
            (int(limit),),
        )
        rows = [r[0] for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []
