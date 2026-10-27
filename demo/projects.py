"""ux(round19) 项目 / 会话索引 —— Python 参考实现，Rust 侧 workbench/src/projects.rs 的镜像。

契约单源：``contract/projects.v1.json``（schema 名、保留 id、上限、字段清单）。
两侧都是消费者，不许各留一份手工副本 —— 与 ``contract/intents.v1.json`` 同一套纪律。

正常响应由 ``scripts/test_projects_parity.py`` 逐字段对拍。
写入使用进程间锁和原子替换；损坏记录可只读恢复，但不得作为空记录覆盖。
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import time
import tempfile
import threading
from collections import deque
from contextlib import contextmanager
from functools import wraps
from pathlib import Path, PureWindowsPath
from typing import Any
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT = REPO_ROOT / "contract" / "projects.v1.json"


def _contract() -> dict[str, Any]:
    """契约必须存在且可解析；缺失/损坏一律 fail-fast，不静默回退内联默认值
    —— 与 contract/README.md 对 intents.v1.json 的要求一致。"""
    with open(CONTRACT, encoding="utf-8") as f:
        return json.load(f)


C = _contract()
SCHEMA_PROJECTS = C["schemas"]["projects"]
SCHEMA_SESSIONS = C["schemas"]["sessions"]
SCHEMA_SESSION_DETAIL = C["schemas"]["session_detail"]
SCHEMA_SESSION_META = C["schemas"]["session_meta"]
INBOX_ID = C["inbox"]["id"]
INBOX_NAME = C["inbox"]["name"]
_L = C["limits"]
DEFAULT_LIMIT = _L["default_limit"]
MAX_LIMIT = _L["max_limit"]
NAME_MAX = _L["name_max_chars"]
TITLE_MAX = _L["title_max_chars"]
TRANSCRIPT_TAIL = _L["transcript_tail"]
TEXT_MAX_BYTES = _L["text_max_bytes"]
# Storage limits are separate from the UI's much smaller display limits.
HISTORY_MESSAGE_MAX_BYTES = 16 * 1024 * 1024
HISTORY_MAX_BYTES = 256 * 1024 * 1024
HISTORY_MAX_MESSAGES = 100_000
_PID_RE = re.compile(C["project_id_pattern"])
_SID = C["session_id"]


def _index_max() -> int:
    raw = os.environ.get(_L["session_index_max_env"], "")
    try:
        value = int(raw)
        return value if value > 0 else _L["session_index_max_default"]
    except (TypeError, ValueError):
        return _L["session_index_max_default"]


def _now() -> int:
    return int(time.time())


# ------------------------------------------------------------------ 守卫


def safe_session_id(s: str) -> str:
    """验证原值；拒绝非法字符、超长 ID 和保留名称，绝不静默归一化。"""
    if (not isinstance(s, str) or not _SID["min_len"] <= len(s) <= _SID["max_len"]
            or s.startswith("_") or not re.fullmatch(r"[A-Za-z0-9_-]+", s)
            or PureWindowsPath(s).is_reserved()):
        raise ValueError("session_id 无效")
    return s


def safe_project_id(s: str) -> str:
    if s == INBOX_ID:
        return s
    if isinstance(s, str) and _PID_RE.fullmatch(s):
        return s
    raise ValueError("project_id 无效")


def clean_name(s: str) -> str:
    t = (s or "").strip()
    if not t:
        raise ValueError("项目名不能为空")
    if any(ord(c) < 32 or ord(c) == 127 for c in t):
        raise ValueError("项目名含控制字符")
    return t[:NAME_MAX]  # 按字符数不是字节，中文名不炸


# ------------------------------------------------------------------ 路径


def _resolved_path(path: Path) -> Path:
    resolved = path.resolve()
    if os.name == "nt":
        # Windows realpath can retain \\?\ after a transient failure in its
        # second final-path probe. Normalize that equivalent spelling on both
        # sides before checking containment, without hiding actual link targets.
        value = str(resolved)
        if value.startswith("\\\\?\\UNC\\"):
            resolved = Path("\\\\" + value[8:])
        elif (value.startswith("\\\\?\\") and len(value) >= 7
              and value[4].isalpha() and value[5:7] == ":\\"):
            resolved = Path(value[4:])
    return resolved


def _bounded_path(out_root: Path, *parts: str) -> Path:
    from packing_assistant.filesystem import native_path

    extended = os.name == "nt" and str(out_root).startswith("\\\\?\\")
    root = _resolved_path(Path(out_root))
    path = root
    for part in parts:
        path = path / part
        checked = native_path(path) if extended else path
        if checked.is_symlink() or getattr(checked, "is_junction", lambda: False)():
            raise ValueError("项目或会话存储路径不能是链接")
    try:
        _resolved_path(path).relative_to(root)
    except ValueError:
        raise ValueError("项目或会话存储路径超出根目录") from None
    # Compare ordinary canonical paths above, but retain an explicitly supplied
    # extended spelling for actual I/O in a deep private named-instance store.
    return native_path(path) if extended else path


def _index_dir(out_root: Path) -> Path:
    return _bounded_path(out_root, "_index")


def _registry_path(out_root: Path) -> Path:
    return _bounded_path(out_root, "_index", "projects.v1.json")


def _meta_path(out_root: Path, sid: str) -> Path:
    return _bounded_path(out_root, sid, "session.meta.json")


def _transcript_path(out_root: Path, sid: str) -> Path:
    return _bounded_path(out_root, sid, "transcript.jsonl")


_THREAD_LOCK = threading.RLock()


@contextmanager
def _mutation(out_root: Path):
    """Serialize complete transactions across threads and server processes."""
    with _THREAD_LOCK:
        lock_path = _bounded_path(out_root, "_index", "projects.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a+b") as handle:
            if os.name == "nt":
                import msvcrt

                if handle.seek(0, os.SEEK_END) == 0:
                    handle.write(b"\0")
                    handle.flush()
                deadline = time.monotonic() + 30
                while True:
                    handle.seek(0)
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise OSError("项目存储繁忙，请稍后重试") from None
                        time.sleep(0.01)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _serialized(fn):
    @wraps(fn)
    def run(out_root: Path, *args, **kwargs):
        with _mutation(out_root):
            return fn(out_root, *args, **kwargs)
    return run


def _write_atomic(path: Path, text: str, *, before_replace=None) -> None:
    """同目录的独有临时文件，失败只清理本次写入，保留旧文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp",
                                         delete=False, newline="\n") as handle:
            tmp = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        deadline = time.monotonic() + 2
        while True:
            try:
                if before_replace is not None:
                    before_replace()
                tmp.replace(path)
                break
            except PermissionError:
                # Windows may briefly deny replacement while a concurrent
                # reader (or antivirus scanner) still has the old file open.
                if os.name != "nt" or time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


# ------------------------------------------------------------------ 注册表


def _empty_registry() -> dict[str, Any]:
    return {"schema": SCHEMA_PROJECTS, "version": 1, "projects": []}


def _read_object(path: Path, *, strict: bool, label: str) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            return value
    except FileNotFoundError:
        return None
    except (OSError, ValueError, RecursionError):
        pass
    if strict:
        raise ValueError(f"{label}无法读取或已损坏；原文件已保留，请恢复后重试")
    return {}


def _nonnegative_int(value: Any) -> bool:
    return type(value) is int and value >= 0


def _valid_text(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        value.encode("utf-8")
        return True
    except UnicodeError:
        return False


def _valid_project(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    try:
        if safe_project_id(value.get("id")) == INBOX_ID:
            return False
        if "merged_into" in value:
            safe_project_id(value["merged_into"])
            return True
    except ValueError:
        return False
    return (
        _valid_text(value.get("name")) and bool(value["name"].strip())
        and isinstance(value.get("aliases", []), list)
        and all(_valid_text(alias) for alias in value.get("aliases", []))
        and type(value.get("archived", False)) is bool
        and all(_nonnegative_int(value.get(key, 0)) for key in ("created_at", "updated_at"))
    )


def load_registry(out_root: Path, *, _strict: bool = False) -> dict[str, Any]:
    """只读时恢复有效条目；写事务严格读取，损坏时绝不重建覆盖。"""
    p = _registry_path(out_root)
    v = _read_object(p, strict=_strict, label="项目索引")
    if v is None:
        return _empty_registry()
    if (not isinstance(v.get("projects"), list)
            or v.get("schema", SCHEMA_PROJECTS) != SCHEMA_PROJECTS
            or v.get("version", 1) != 1):
        if _strict:
            raise ValueError("项目索引结构损坏；原文件已保留，请恢复后重试")
        return _empty_registry()
    valid = []
    seen = set()
    for entry in v["projects"]:
        if not _valid_project(entry) or entry["id"] in seen:
            if _strict:
                raise ValueError("项目索引条目损坏；原文件已保留，请恢复后重试")
            continue
        valid.append(entry)
        seen.add(entry["id"])
    v["projects"] = valid
    return v


def _save_registry(out_root: Path, reg: dict[str, Any]) -> None:
    _write_atomic(_registry_path(out_root), json.dumps(reg, ensure_ascii=False, indent=2))


def _is_tombstone(p: dict) -> bool:
    return isinstance(p.get("merged_into"), str)


def _is_active(p: dict) -> bool:
    return not _is_tombstone(p) and not p.get("archived", False)


def _resolve_pid(reg: dict, pid: str) -> str:
    """跟随墓碑 merged_into **一跳**（限一跳防环）。空 / 不存在 / 已归档 → inbox。"""
    if not pid:
        return INBOX_ID
    lst = reg.get("projects", [])
    found = next((p for p in lst if p.get("id") == pid), None)
    if found is None:
        return INBOX_ID
    into = found.get("merged_into")
    if isinstance(into, str):
        hop = next((p for p in lst if p.get("id") == into), None)
        return into if (hop and _is_active(hop)) else INBOX_ID
    return pid if _is_active(found) else INBOX_ID


def _gen_pid(reg: dict) -> str:
    lst = reg.get("projects", [])
    seed = (_now() * 31 + len(lst)) & 0xFFFFFFFF
    while True:
        pid = f"p-{seed:08x}"
        if not any(p.get("id") == pid for p in lst):
            return pid
        seed = (seed + 1) & 0xFFFFFFFF


@_serialized
def create_project(out_root: Path, name: str) -> tuple[dict, bool]:
    name = clean_name(name)
    reg = load_registry(out_root, _strict=True)
    fold = name.casefold()
    for p in reg.get("projects", []):
        if not _is_active(p):
            continue
        names = [p.get("name", "")] + list(p.get("aliases") or [])
        if any((n or "").casefold() == fold for n in names):
            return p, True
    now = _now()
    item = {
        "id": _gen_pid(reg), "name": name, "aliases": [],
        "created_at": now, "updated_at": now, "archived": False,
    }
    reg.setdefault("projects", []).append(item)
    _save_registry(out_root, reg)
    return item, False


@_serialized
def patch_project(out_root: Path, pid: str, name: str | None = None,
                  archived: bool | None = None) -> dict:
    pid = safe_project_id(pid)
    if pid == INBOX_ID:
        raise ValueError("未归类是内置项目，不能改名或归档")
    reg = load_registry(out_root, _strict=True)
    for p in reg.get("projects", []):
        if p.get("id") != pid:
            continue
        if name is not None:
            n = clean_name(name)
            old = p.get("name", "")
            if old and old != n:
                al = list(p.get("aliases") or [])
                if old not in al:
                    al.append(old)
                p["aliases"] = al
            p["name"] = n
        if archived is not None:
            p["archived"] = bool(archived)
        p["updated_at"] = _now()
        _save_registry(out_root, reg)
        return p
    raise ValueError("项目不存在")


@_serialized
def merge_project(out_root: Path, src: str, into: str) -> dict:
    src = safe_project_id(src)
    into = safe_project_id(into)
    if src == into:
        raise ValueError("不能合并到自身")
    if INBOX_ID in (src, into):
        raise ValueError("未归类是内置项目，不参与合并")
    reg = load_registry(out_root, _strict=True)
    lst = reg.get("projects", [])
    s = next((p for p in lst if p.get("id") == src), None)
    if s is None:
        raise ValueError("源项目不存在")
    if not any(p.get("id") == into and _is_active(p) for p in lst):
        raise ValueError("目标项目不存在或已归档")
    carry = [s.get("name", "")] + list(s.get("aliases") or [])
    out = None
    for i, p in enumerate(lst):
        if p.get("id") == src:
            lst[i] = {"id": src, "merged_into": into}
        elif p.get("id") == into:
            al = list(p.get("aliases") or [])
            for c in carry:
                if c and c not in al:
                    al.append(c)
            p["aliases"] = al
            p["updated_at"] = _now()
            out = p
    _save_registry(out_root, reg)
    if out is None:
        raise ValueError("合并失败")
    return out


# ------------------------------------------------------------------ 会话侧车


def _load_meta(out_root: Path, sid: str, *, strict: bool = False) -> dict | None:
    p = _meta_path(out_root, sid)
    value = _read_object(p, strict=strict, label="会话元数据")
    if value is None:
        return None
    if (value.get("session_id", sid) != sid
            or value.get("schema", SCHEMA_SESSION_META) != SCHEMA_SESSION_META):
        if strict:
            raise ValueError("会话元数据身份不匹配；原文件已保留")
        return {}
    invalid = False
    for key in ("title", "project_id", "last_user"):
        if not _valid_text(value.get(key, "")):
            invalid = True
            value[key] = ""
    for key in ("title_source", "project_source"):
        if value.get(key, "auto") not in ("auto", "manual"):
            invalid = True
            value[key] = "auto"
    for key in ("turns", "created_at", "updated_at"):
        if not _nonnegative_int(value.get(key, 0)):
            invalid = True
            value[key] = 0
    if invalid and strict:
        raise ValueError("会话元数据字段损坏；原文件已保留，请恢复后重试")
    return value


def _match_project(reg: dict, text: str) -> str | None:
    """大小写不敏感子串包含 + 最长优先首命中。**永不新建项目。**"""
    hay = (text or "").casefold()
    if not hay.strip():
        return None
    best: tuple[int, str] | None = None
    for p in reg.get("projects", []):
        if not _is_active(p):
            continue
        pid = p.get("id") or ""
        if not pid:
            continue
        for n in [p.get("name", "")] + list(p.get("aliases") or []):
            if n and n.casefold() in hay:
                ln = len(n)
                if best is None or ln > best[0]:
                    best = (ln, pid)
    return best[1] if best else None


def _new_meta(sid: str) -> dict:
    return {
        "schema": SCHEMA_SESSION_META, "session_id": sid,
        "title": "", "title_source": "auto",
        "project_id": "", "project_source": "auto",
        "created_at": _now(), "turns": 0,
    }


@_serialized
def touch_session(out_root: Path, session: str, user_text: str, project_hint: str = "") -> None:
    try:
        sid = safe_session_id(session)
    except ValueError:
        return
    reg = load_registry(out_root, _strict=True)
    meta = _load_meta(out_root, sid, strict=True) or _new_meta(sid)
    if not meta.get("title") and (user_text or "").strip():
        meta["title"] = (user_text.splitlines() or [""])[0].strip()[:TITLE_MAX]
    if meta.get("project_source") != "manual":
        if project_hint:
            try:
                meta["project_id"] = _resolve_pid(reg, safe_project_id(project_hint))
                meta["project_source"] = "manual"
            except ValueError:
                pass
        elif not meta.get("project_id"):
            pid = _match_project(reg, user_text)
            if pid:
                meta["project_id"] = pid
                meta["project_source"] = "auto"
    meta["turns"] = int(meta.get("turns") or 0) + 1
    meta["updated_at"] = _now()
    if (user_text or "").strip():
        meta["last_user"] = user_text.strip()[:120]
    _write_atomic(_meta_path(out_root, sid), json.dumps(meta, ensure_ascii=False, indent=2))


@_serialized
def set_session_meta(out_root: Path, session: str, project_id: str | None = None,
                     title: str | None = None) -> dict:
    sid = safe_session_id(session)
    reg = load_registry(out_root, _strict=True)
    meta = _load_meta(out_root, sid, strict=True) or _new_meta(sid)
    if project_id is not None:
        pid = INBOX_ID if project_id == "" else safe_project_id(project_id)
        meta["project_id"] = _resolve_pid(reg, pid)
        meta["project_source"] = "manual"
    if title is not None:
        t = title.strip()
        if not t:
            raise ValueError("标题不能为空")
        meta["title"] = t[:TITLE_MAX]
        meta["title_source"] = "manual"
    meta["updated_at"] = _now()
    _write_atomic(_meta_path(out_root, sid), json.dumps(meta, ensure_ascii=False, indent=2))
    return meta


@_serialized
def append_turn(out_root: Path, session: str, role: str, text: str) -> None:
    try:
        sid = safe_session_id(session)
    except ValueError:
        return
    t = text or ""
    if not _valid_text(t) or not _valid_text(role) or not role or len(role) > 64:
        raise ValueError("对话角色或正文无效")
    if len(t.encode("utf-8")) > HISTORY_MESSAGE_MAX_BYTES:
        raise ValueError("单条完整对话超过 16 MB，请拆分后重试")
    line = json.dumps({"id": "msg-" + uuid4().hex, "ts": _now(), "role": role, "text": t}, ensure_ascii=False)
    p = _transcript_path(out_root, sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a+b") as f:
        if f.seek(0, os.SEEK_END):
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                # Keep an interrupted previous record, but do not glue the new
                # valid record onto its incomplete JSON.
                f.write(b"\n")
        f.write((line + "\n").encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())


# ------------------------------------------------------------------ 索引


def _scan_rows(out_root: Path, *, recorded_only: bool = False) -> list[dict]:
    """分级扫描：优先读几百字节的 meta，没有的降级为「目录名 + mtime」，
    **绝不打开 trace.json**。按 mtime 降序截断到 CIVIL_SESSION_INDEX_MAX。"""
    reg = load_registry(out_root)
    rows: list[dict] = []
    if not out_root.is_dir():
        return rows
    for ent in out_root.iterdir():
        if not ent.is_dir():
            continue
        name = ent.name
        try:
            sid = safe_session_id(name)
        except ValueError:
            continue  # _index / _threads 等在此被挡掉
        if sid != name:
            continue
        try:
            m = _load_meta(out_root, sid)
        except ValueError:
            continue  # A linked entry is not a recoverable local conversation.
        if recorded_only and m is None:
            continue  # Internal engine runs are not workbench conversations.
        try:
            mt = int(ent.stat().st_mtime)
        except OSError:
            mt = 0
        if m:
            rows.append({
                "session_id": sid,
                "title": m.get("title") or sid,
                "project_id": _resolve_pid(reg, m.get("project_id") or ""),
                "updated_at": int(m.get("updated_at") or mt),
                "turns": int(m.get("turns") or 0),
            })
        else:
            rows.append({
                "session_id": sid, "title": sid, "project_id": INBOX_ID,
                "updated_at": mt, "turns": 0,
            })
    rows.sort(key=lambda r: r["updated_at"], reverse=True)
    return rows[: _index_max()]


def list_sessions(out_root: Path, project_id: str = "", q: str = "",
                  limit: int = DEFAULT_LIMIT, offset: int = 0, *, recorded_only: bool = False) -> dict:
    lim = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    off = max(0, int(offset or 0))
    rows = _scan_rows(out_root, recorded_only=recorded_only)
    ql = (q or "").strip().casefold()
    sel = [
        r for r in rows
        if (not project_id or r["project_id"] == project_id)
        and (not ql or ql in r["title"].casefold() or ql in r["session_id"].casefold())
    ]
    return {
        "ok": True, "schema": SCHEMA_SESSIONS,
        "total": len(sel), "limit": lim, "offset": off,
        "sessions": sel[off: off + lim],
    }


def list_projects(out_root: Path, *, recorded_only: bool = False) -> dict:
    reg = load_registry(out_root)
    rows = _scan_rows(out_root, recorded_only=recorded_only)
    out = []
    for p in reg.get("projects", []):
        if not _is_active(p):
            continue
        pid = p.get("id") or ""
        out.append({
            "id": pid, "name": p.get("name", ""),
            "aliases": list(p.get("aliases") or []),
            "n_sessions": sum(1 for r in rows if r["project_id"] == pid),
            "updated_at": int(p.get("updated_at") or 0),
            "archived": False,
        })
    out.sort(key=lambda x: x["updated_at"], reverse=True)
    return {
        "ok": True, "schema": SCHEMA_PROJECTS, "projects": out,
        "inbox": {
            "id": INBOX_ID, "name": INBOX_NAME,
            "n_sessions": sum(1 for r in rows if r["project_id"] == INBOX_ID),
            "builtin": True,
        },
    }


def _history_records(path: Path, sid: str):
    """Read canonical records; old records receive stable, position-aware IDs."""
    # A stored string can occupy six JSON bytes per original control byte.
    line_limit = HISTORY_MESSAGE_MAX_BYTES * 6 + 2048
    try:
        handle = open(path, "rb")
    except FileNotFoundError:
        return
    with handle:
        while True:
            offset = handle.tell()
            line = handle.readline(line_limit + 1)
            if not line:
                break
            if len(line) > line_limit:
                while line and not line.endswith(b"\n"):
                    line = handle.readline(line_limit + 1)
                yield None
                continue
            if not line.strip():
                continue
            try:
                item = json.loads(line.decode("utf-8"))
                if (not isinstance(item, dict) or not _nonnegative_int(item.get("ts"))
                        or not _valid_text(item.get("role")) or not item["role"]
                        or len(item["role"]) > 64 or not _valid_text(item.get("text"))):
                    raise ValueError("invalid transcript record")
            except (UnicodeError, ValueError, RecursionError):
                yield None
                continue
            identifier = item.get("id")
            if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identifier):
                digest = hashlib.sha256(sid.encode("ascii") + b"\0" + str(offset).encode("ascii") + b"\0" + line).hexdigest()
                identifier = "legacy-" + digest[:32]
            yield {"id": identifier, "role": item["role"], "content": item["text"], "ts": item["ts"]}


def read_full_history(out_root: Path, session: str, *, strict: bool = False) -> list[dict]:
    """Return all stored message text; never apply the UI tail/byte truncation.

    Old transcripts remain readable without a destructive migration. Text
    already truncated by an older release cannot be reconstructed.
    """
    sid = safe_session_id(session)
    path = _transcript_path(out_root, sid)
    try:
        if path.stat().st_size > HISTORY_MAX_BYTES:
            raise ValueError("完整对话超过 256 MB，请拆分任务后检索")
    except FileNotFoundError:
        return []
    rows = []
    seen = set()
    for row in _history_records(path, sid):
        if row is None:
            if strict:
                raise ValueError("原始对话含损坏记录，未重建记忆；请先核对或恢复原始对话")
            continue
        if row["id"] in seen:
            # A copied legacy row must not replace another message in the index.
            row["id"] = "duplicate-" + hashlib.sha256(
                (sid + "\0" + str(len(rows)) + "\0" + row["id"]).encode("utf-8")
            ).hexdigest()[:32]
        seen.add(row["id"])
        rows.append(row)
        if len(rows) > HISTORY_MAX_MESSAGES:
            raise ValueError("完整对话超过 100000 条，请拆分任务后检索")
    return rows


def _read_transcript(path: Path) -> tuple[list[dict], bool]:
    """Render a bounded UI tail from the complete, append-only transcript."""
    tail: deque[dict] = deque(maxlen=TRANSCRIPT_TAIL)
    truncated = False
    for record in _history_records(path, path.parent.name):
        if record is None:
            truncated = True
            continue
        item = {"ts": record["ts"], "role": record["role"], "text": record["content"]}
        encoded = item["text"].encode("utf-8")
        if len(encoded) > TEXT_MAX_BYTES:
            item["text"] = encoded[:TEXT_MAX_BYTES].decode("utf-8", errors="ignore")
            truncated = True
        if len(tail) == TRANSCRIPT_TAIL:
            truncated = True
        tail.append(item)
    return list(tail), truncated


def session_detail(out_root: Path, session: str) -> dict:
    sid = safe_session_id(session)
    reg = load_registry(out_root)
    m = _load_meta(out_root, sid)
    d = _bounded_path(out_root, sid)
    try:
        mt = int(d.stat().st_mtime)
    except OSError:
        mt = 0
    turns, truncated = _read_transcript(_transcript_path(out_root, sid))
    return {
        "ok": True, "schema": SCHEMA_SESSION_DETAIL,
        "session_id": sid,
        "title": (m or {}).get("title") or sid,
        "project_id": _resolve_pid(reg, (m or {}).get("project_id") or ""),
        "updated_at": int((m or {}).get("updated_at") or mt),
        "turns": int((m or {}).get("turns") or 0),
        "transcript": turns,
        "truncated": truncated,
    }
