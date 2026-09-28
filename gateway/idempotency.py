"""Client Idempotency-Key for the POSTs that start a run.

A browser or script that times out and sends the same request again must not start a second run. A caller
that wants that guarantee sends ``Idempotency-Key: <1-200 of A-Z a-z 0-9 . _ : ->``:

  * same key, same body, same session, within 24 h  -> the stored response, marked ``"replayed": true``
    (header ``Idempotency-Replayed: true``); nothing runs again
  * same key, different body                         -> 422 ``idempotency_key_reused``; nothing runs
  * no key                                           -> exactly as before

Only a completed 200 response is stored: a refusal, a 429 or a crash is not, so the retry runs. Two requests
with the same key at the same moment are serialised in this process, so the second one is the replay.
The store is the session store's database (``idempotency_keys`` in data/civilbuddy.db) when CB_STORAGE is
sqlite, and JSON files under ``$PACKING_OUTPUT_DIR/idempotency`` otherwise; entries older than the TTL
(``CIVIL_IDEMPOTENCY_TTL_S``, default 86400) are ignored and dropped on the next write.

JSON routes go through ``IdempotencyMiddleware`` (installed inside the access guard, so an unauthorised caller
never reaches a stored response). The multipart upload /api/tender/link fingerprints the parsed form instead of
the raw bytes (a browser picks a new multipart boundary on every send) and calls ``lookup`` / ``remember``
itself (gateway/web_link.py).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

from starlette.concurrency import run_in_threadpool

logger = logging.getLogger("civil.idempotency")

HEADER = "idempotency-key"
KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
#: a response larger than this is not stored (the retry then runs again, as without a key)
MAX_STORED_BYTES = 8 * 1024 * 1024
#: JSON POSTs that start a run (the pipeline, Team A, demos, a PDF run, a what-if) or the link demo
RUN_ROUTES = frozenset({
    "/api/pipeline", "/api/pipeline/profile", "/api/pipeline/trace", "/api/team-a", "/api/demo",
    "/api/run-pdf", "/api/whatif", "/api/tender/link/demo",
})


class KeyReused(Exception):
    """The key was already used in this session for a different body."""


class BadKey(ValueError):
    pass


def ttl_s() -> float:
    try:
        return max(1.0, float(os.getenv("CIVIL_IDEMPOTENCY_TTL_S") or 86400))
    except ValueError:
        return 86400.0


def request_key(headers: Any) -> Optional[str]:
    """The Idempotency-Key header, or None. A present but malformed key raises BadKey."""
    raw = headers.get(HEADER)
    if raw is None:
        return None
    key = str(raw).strip()
    if not KEY_RE.match(key):
        raise BadKey("Idempotency-Key: 1-200 of A-Z a-z 0-9 . _ : -")
    return key


def fingerprint(*parts: Any) -> str:
    blob = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _output_root() -> Path:
    raw = (os.getenv("PACKING_OUTPUT_DIR") or "").strip()
    root = Path(__file__).resolve().parents[1]
    base = Path(raw).expanduser() if raw else root / "output"
    return (base if base.is_absolute() else root / base).resolve()


def _file_for(scope: str, key: str) -> Path:
    return _output_root() / "idempotency" / (hashlib.sha256(f"{scope}\n{key}".encode("utf-8")).hexdigest() + ".json")


def _sqlite() -> bool:
    from packing_assistant import storage

    return storage.storage_mode() == "sqlite"


def _get(scope: str, key: str) -> Optional[Dict[str, Any]]:
    if _sqlite():
        from packing_assistant import storage

        return storage.get_storage().idem_get(scope, key)
    try:
        row = json.loads(_file_for(scope, key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return row if isinstance(row, dict) and row.get("scope") == scope and row.get("key") == key else None


def _put(scope: str, key: str, fp: str, status: int, response: Any) -> None:
    now = time.time()
    if _sqlite():
        from packing_assistant import storage

        storage.get_storage().idem_put(scope, key, fingerprint=fp, status=status, response=response,
                                       created_at=now, expire_before=now - ttl_s())
        return
    from packing_assistant.sandbox import guarded_write_text

    folder = _output_root() / "idempotency"
    if folder.is_dir():
        for old in folder.glob("*.json"):
            try:
                if old.stat().st_mtime < now - ttl_s():
                    old.unlink()
            except OSError:
                pass
    row = {"scope": scope, "key": key, "fingerprint": fp, "status": int(status), "response": response,
           "created_at": now}
    guarded_write_text(_file_for(scope, key), json.dumps(row, ensure_ascii=False, default=str))


def lookup(scope: str, key: str, fp: str) -> Optional[Dict[str, Any]]:
    """The stored {status, response} for this key, None if there is none (or it expired).
    Raises KeyReused when the key was stored for a different body."""
    row = _get(scope, key)
    if not row or float(row.get("created_at") or 0) < time.time() - ttl_s():
        return None
    if row.get("fingerprint") != fp:
        raise KeyReused("this Idempotency-Key was already used in this session for a different request")
    return {"status": int(row.get("status") or 200), "response": row.get("response")}


def remember(scope: str, key: str, fp: str, response: Any, status: int = 200) -> None:
    """Store a completed response. A store failure is logged: the run itself already succeeded."""
    try:
        size = len(json.dumps(response, ensure_ascii=False, default=str).encode("utf-8"))
        if size > MAX_STORED_BYTES:
            logger.warning("idempotency: response of %d bytes not stored (%s)", size, scope)
            return
        _put(scope, key, fp, status, response)
    except Exception:
        logger.error("idempotency: could not store the response for %s", scope, exc_info=True)


def replayed(stored: Dict[str, Any]) -> Any:
    body = stored["response"]
    return {**body, "replayed": True} if isinstance(body, dict) else body


_THREAD_GUARD = threading.Lock()
_THREAD_LOCKS: Dict[Tuple[str, str], Tuple[threading.Lock, int]] = {}


def _enter(ident: Tuple[str, str]) -> threading.Lock:
    with _THREAD_GUARD:
        lock, users = _THREAD_LOCKS.get(ident, (threading.Lock(), 0))
        _THREAD_LOCKS[ident] = (lock, users + 1)
        return lock


def _leave(ident: Tuple[str, str]) -> None:
    with _THREAD_GUARD:
        lock, users = _THREAD_LOCKS[ident]
        if users <= 1:
            del _THREAD_LOCKS[ident]
        else:
            _THREAD_LOCKS[ident] = (lock, users - 1)


@contextmanager
def key_lock(scope: str, key: str) -> Iterator[None]:
    """One request per (scope, key) at a time in this process (sync code on a worker thread)."""
    ident = (scope, key)
    lock = _enter(ident)
    try:
        with lock:
            yield
    finally:
        _leave(ident)


class _AsyncKeyLock:
    """The same per-key lock for the event loop. Polls instead of parking a thread on acquire(), so a client
    that goes away while waiting never leaves a thread that takes the lock later and holds it for good."""

    def __init__(self, scope: str, key: str) -> None:
        self.ident = (scope, key)

    async def __aenter__(self) -> None:
        self.lock = _enter(self.ident)
        try:
            while not self.lock.acquire(blocking=False):
                await asyncio.sleep(0.05)
        except BaseException:
            _leave(self.ident)
            raise

    async def __aexit__(self, *exc: Any) -> None:
        self.lock.release()
        _leave(self.ident)


def _json_response(status: int, body: Any, extra: Tuple[Tuple[bytes, bytes], ...] = ()) -> Tuple[dict, dict]:
    data = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode()), *extra]
    return ({"type": "http.response.start", "status": status, "headers": headers},
            {"type": "http.response.body", "body": data, "more_body": False})


class IdempotencyMiddleware:
    """Pure ASGI: replays a stored response for a repeated Idempotency-Key on RUN_ROUTES."""

    def __init__(self, app: Any, routes: frozenset = RUN_ROUTES) -> None:
        self.app = app
        self.routes = routes

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("method") != "POST" or scope.get("path") not in self.routes:
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        try:
            key = request_key(headers)
        except BadKey as exc:
            for message in _json_response(400, {"ok": False, "error_code": "bad_idempotency_key", "detail": str(exc)}):
                await send(message)
            return
        if key is None:
            await self.app(scope, receive, send)
            return
        chunks = []
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunks.append(message.get("body", b""))
            if not message.get("more_body"):
                break
        body = b"".join(chunks)
        try:
            parsed = json.loads(body) if body.strip() else None
        except ValueError:
            parsed = None
        session = str(parsed.get("session_id") or "") if isinstance(parsed, dict) else ""
        canonical = parsed if parsed is not None or not body.strip() else hashlib.sha256(body).hexdigest()
        route = f"{scope['path']}|{session}"
        fp = fingerprint(scope["path"], canonical)
        async with _AsyncKeyLock(route, key):
            try:
                stored = await run_in_threadpool(lookup, route, key, fp)
            except KeyReused as exc:
                for message in _json_response(422, {"ok": False, "error_code": "idempotency_key_reused",
                                                    "detail": str(exc)}):
                    await send(message)
                return
            if stored is not None:
                for message in _json_response(stored["status"], replayed(stored),
                                              ((b"idempotency-replayed", b"true"),)):
                    await send(message)
                return
            await self._run(scope, receive, send, body, route, key, fp)

    async def _run(self, scope: dict, receive: Any, send: Any, body: bytes, route: str, key: str, fp: str) -> None:
        sent_body = False
        state: Dict[str, Any] = {"status": 0, "json": False, "parts": [], "size": 0}

        async def replay_receive() -> dict:
            nonlocal sent_body
            if not sent_body:
                sent_body = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        async def capture_send(message: dict) -> None:
            if message["type"] == "http.response.start":
                state["status"] = int(message["status"])
                ctype = dict((k.lower(), v) for k, v in message.get("headers") or []).get(b"content-type", b"")
                state["json"] = ctype.startswith(b"application/json")
            elif message["type"] == "http.response.body" and state["size"] <= MAX_STORED_BYTES:
                part = message.get("body", b"")
                state["parts"].append(part)
                state["size"] += len(part)
            await send(message)

        await self.app(scope, replay_receive, capture_send)
        if state["status"] == 200 and state["json"] and state["size"] <= MAX_STORED_BYTES:
            try:
                response = json.loads(b"".join(state["parts"]))
            except ValueError:
                return
            await run_in_threadpool(remember, route, key, fp, response)
