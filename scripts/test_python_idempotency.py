#!/usr/bin/env python3
"""Repeats, crashes and locks on the Python side must not duplicate or corrupt anything.

  tms        http mode: the request id comes from (session, plan sha256) and is sent as Idempotency-Key; a plan
             the TMS already booked is replayed from the ledger (one booking at the fake TMS, not two); a
             timed-out attempt is not recorded and its resubmit carries the same key
  key        Idempotency-Key on /api/tender/link and the run-starting JSON POSTs: same key + same body ->
             the stored response (replayed), no second job; same key + other body -> 422; concurrent sends
             of one key run once; the key never bypasses the access guard; entries expire (TTL)
  atomic     deliverable writes go through a temp file + os.replace: an exception or a kill mid-write leaves
             the old file (or none), never a truncated one; newline translation unchanged
  lock       save_session under a held SQLite lock retries, then raises SessionPersistError without writing a
             JSON-only copy (both stores keep the previous state); the gateway answers 503 with Retry-After
  events     UNIQUE(run_id, seq): a repeated event is one row; an existing DB with duplicates is migrated

Everything runs on temp folders, a temp database and a fake TMS on 127.0.0.1. No model, no network.
"""

from __future__ import annotations

import http.server
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="test-py-idem-"))
os.environ.update(PYTHON_DOTENV_DISABLED="1", CB_STORAGE="sqlite", CB_DB_PATH=str(TMP / "cb.db"),
                  PACKING_OUTPUT_DIR=str(TMP / "output"), PACKING_TRACE_DIR=str(TMP / "output" / "traces"))
for _key in [k for k in os.environ if k.endswith("_API_KEY")] + [
        "CIVIL_TOKEN", "CIVIL_JOB_ROOT", "PACKING_TMS_MODE", "PACKING_TMS_URL", "PACKING_TMS_TIMEOUT_S",
        "CB_SQLITE_BUSY_MS", "CB_SQLITE_WRITE_ATTEMPTS", "CIVIL_IDEMPOTENCY_TTL_S"]:
    os.environ.pop(_key, None)

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gateway import app as gateway  # noqa: E402
from gateway import idempotency  # noqa: E402
from packing_assistant import sandbox, session_store, storage, tms_booking, trace_events  # noqa: E402

DEMO = ROOT / "examples" / "facade-demo"
TOKEN = "t5-idempotency-token"


class Env:
    """Set environment variables for one test; restore them after."""

    def __init__(self, stack: ExitStack) -> None:
        self.stack = stack

    def set(self, **values: str) -> None:
        for k, v in values.items():
            old = os.environ.get(k)
            os.environ[k] = v
            self.stack.callback(lambda k=k, old=old: os.environ.pop(k, None) if old is None
                                else os.environ.__setitem__(k, old))


# ---------------------------------------------------------------- fake TMS


class FakeTMS(http.server.BaseHTTPRequestHandler):
    """Books once per Idempotency-Key, like a TMS that honours the header. Counts every POST."""

    posts: list = []
    bookings: dict = {}
    delay = 0.0

    def log_message(self, *args) -> None:
        pass

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        key = self.headers.get("Idempotency-Key")
        FakeTMS.posts.append({"key": key, "request_id": body.get("request_id")})
        ident = key or f"no-key-{len(FakeTMS.posts)}"
        FakeTMS.bookings.setdefault(ident, f"TMS-{len(FakeTMS.bookings) + 1:04d}")
        if FakeTMS.delay:
            time.sleep(FakeTMS.delay)
        data = json.dumps({"booking_id": FakeTMS.bookings[ident], "status": "booked"}).encode()
        try:
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            pass                        # the client gave up (timeout test)


def _plan_state(session: str = "tms-s1", run: str = "run-1", boxes: int = 1) -> dict:
    return {"session_id": session, "run_id": run, "container_type": "40HQ",
            "container_plan": {"containers_used": 2, "n0": 2, "can_fit": True},
            "boxes": [{"id": f"B{i}", "outer_L": 3000, "outer_W": 1100, "outer_H": 900, "gross_kg": 800}
                      for i in range(boxes)]}


class TmsBookingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeTMS)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        FakeTMS.posts, FakeTMS.bookings, FakeTMS.delay = [], {}, 0.0
        env = Env(self.stack)
        env.set(PACKING_TMS_MODE="http", PACKING_TMS_URL=f"http://127.0.0.1:{self.server.server_address[1]}")
        self.env = env

    def test_request_id_is_the_plan_not_the_call(self) -> None:
        a, b = tms_booking.build_booking_request(_plan_state()), tms_booking.build_booking_request(_plan_state())
        self.assertEqual(a["request_id"], b["request_id"])
        self.assertEqual(a["plan_sha256"], b["plan_sha256"])
        self.assertTrue(a["request_id"].startswith("bk-"))
        # a re-run of the same plan is the same plan; another session or another cargo list is not
        self.assertEqual(a["request_id"], tms_booking.build_booking_request(_plan_state(run="run-2"))["request_id"])
        self.assertNotEqual(a["request_id"], tms_booking.build_booking_request(_plan_state(session="s2"))["request_id"])
        self.assertNotEqual(a["request_id"], tms_booking.build_booking_request(_plan_state(boxes=2))["request_id"])

    def test_same_plan_books_once_and_is_replayed(self) -> None:
        first = tms_booking.submit_booking(_plan_state())
        second = tms_booking.submit_booking(_plan_state())
        self.assertTrue(first["ok"] and second["ok"], (first, second))
        self.assertEqual(1, len(FakeTMS.posts), "the second submit must not reach the TMS")
        self.assertEqual(first["request"]["request_id"], FakeTMS.posts[0]["key"])
        self.assertFalse(first["replayed"])
        self.assertTrue(second["replayed"])
        self.assertEqual(first["booking_summary"]["booking_id"], second["booking_summary"]["booking_id"])
        # another plan is another booking
        third = tms_booking.submit_booking(_plan_state(boxes=3))
        self.assertEqual(2, len(FakeTMS.posts))
        self.assertNotEqual(first["booking_summary"]["booking_id"], third["booking_summary"]["booking_id"])

    def test_timeout_is_not_recorded_and_resubmit_carries_the_same_key(self) -> None:
        self.env.set(PACKING_TMS_TIMEOUT_S="1")
        FakeTMS.delay = 2.5                 # the TMS books, but answers after the client gave up
        started = time.monotonic()
        first = tms_booking.submit_booking(_plan_state(session="tms-timeout"))
        self.assertLess(time.monotonic() - started, 2.4)
        self.assertFalse(first["ok"], first)
        FakeTMS.delay = 0.0
        second = tms_booking.submit_booking(_plan_state(session="tms-timeout"))
        self.assertTrue(second["ok"], second)
        self.assertFalse(second["replayed"], "a failed attempt must not be replayed as a booking")
        self.assertEqual(2, len(FakeTMS.posts))
        self.assertEqual(FakeTMS.posts[0]["key"], FakeTMS.posts[1]["key"])
        self.assertEqual(1, len(FakeTMS.bookings), "one key, so the TMS booked once")
        third = tms_booking.submit_booking(_plan_state(session="tms-timeout"))
        self.assertTrue(third["replayed"])
        self.assertEqual(2, len(FakeTMS.posts))

    def test_concurrent_submits_of_one_plan_book_once(self) -> None:
        FakeTMS.delay = 0.3
        results = []
        threads = [threading.Thread(target=lambda: results.append(tms_booking.submit_booking(_plan_state("tms-c"))))
                   for _ in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(1, len(FakeTMS.posts))
        self.assertEqual(3, sum(1 for r in results if r.get("replayed")))

    def test_gateway_submit_twice_books_once(self) -> None:
        client = TestClient(gateway.app)
        body = {"state": _plan_state(session="")}
        r1, r2 = client.post("/api/tms/booking/submit", json=body), client.post("/api/tms/booking/submit", json=body)
        self.assertEqual((200, 200), (r1.status_code, r2.status_code))
        self.assertEqual(1, len(FakeTMS.posts))
        self.assertTrue(r2.json()["replayed"])

    def test_stub_mode_is_unchanged(self) -> None:
        self.env.set(PACKING_TMS_MODE="stub")
        a, b = tms_booking.submit_booking(_plan_state()), tms_booking.submit_booking(_plan_state())
        self.assertEqual("stub", a["mode"])
        self.assertEqual(a["booking_id"], b["booking_id"])      # the same draft, rewritten in place
        self.assertNotIn("replayed", b)
        self.assertEqual(0, len(FakeTMS.posts))


# ---------------------------------------------------------------- Idempotency-Key


def _toy_app(calls: list, delay: float = 0.0) -> FastAPI:
    app = FastAPI()
    app.add_middleware(idempotency.IdempotencyMiddleware, routes=frozenset({"/run"}))

    @app.post("/run")
    def run(body: dict):
        calls.append(body)
        if delay:
            time.sleep(delay)
        if body.get("fail"):
            raise HTTPException(500, "failed")
        return {"ok": True, "n": len(calls), "session_id": body.get("session_id")}

    return app


class IdempotencyKeyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.env = Env(self.stack)
        self.env.set(PACKING_OUTPUT_DIR=str(Path(tempfile.mkdtemp(dir=TMP))))

    def test_same_key_same_body_runs_once(self) -> None:
        calls = []
        client = TestClient(_toy_app(calls))
        h = {"Idempotency-Key": "k-1"}
        a = client.post("/run", headers=h, json={"session_id": "s", "x": 1})
        b = client.post("/run", headers=h, json={"x": 1, "session_id": "s"})   # key order is not the body
        self.assertEqual(1, len(calls))
        self.assertEqual(a.json()["n"], b.json()["n"])
        self.assertTrue(b.json()["replayed"])
        self.assertNotIn("replayed", a.json())
        self.assertEqual("true", b.headers.get("idempotency-replayed"))

    def test_same_key_other_body_is_422_and_does_not_run(self) -> None:
        calls = []
        client = TestClient(_toy_app(calls))
        h = {"Idempotency-Key": "k-2"}
        client.post("/run", headers=h, json={"session_id": "s", "x": 1})
        r = client.post("/run", headers=h, json={"session_id": "s", "x": 2})
        self.assertEqual(422, r.status_code)
        self.assertEqual("idempotency_key_reused", r.json()["error_code"])
        self.assertEqual(1, len(calls))

    def test_the_key_is_per_session_and_optional(self) -> None:
        calls = []
        client = TestClient(_toy_app(calls))
        client.post("/run", headers={"Idempotency-Key": "k-3"}, json={"session_id": "a"})
        client.post("/run", headers={"Idempotency-Key": "k-3"}, json={"session_id": "b"})
        client.post("/run", json={"session_id": "a"})
        client.post("/run", json={"session_id": "a"})
        self.assertEqual(4, len(calls))
        self.assertEqual(400, client.post("/run", headers={"Idempotency-Key": "bad key!"}, json={}).status_code)
        self.assertEqual(4, len(calls))

    def test_a_failure_is_not_stored(self) -> None:
        calls = []
        client = TestClient(_toy_app(calls), raise_server_exceptions=False)
        h = {"Idempotency-Key": "k-4"}
        self.assertEqual(500, client.post("/run", headers=h, json={"fail": True}).status_code)
        self.assertEqual(500, client.post("/run", headers=h, json={"fail": True}).status_code)
        self.assertEqual(2, len(calls))

    def test_concurrent_sends_of_one_key_run_once(self) -> None:
        calls = []
        client = TestClient(_toy_app(calls, delay=0.4))
        out = []
        threads = [threading.Thread(target=lambda: out.append(
            client.post("/run", headers={"Idempotency-Key": "k-5"}, json={"session_id": "s"}).json()))
            for _ in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(1, len(calls))
        self.assertEqual(3, sum(1 for r in out if r.get("replayed")))

    def test_entries_expire(self) -> None:
        self.env.set(CIVIL_IDEMPOTENCY_TTL_S="1")
        calls = []
        client = TestClient(_toy_app(calls))
        client.post("/run", headers={"Idempotency-Key": "k-6"}, json={})
        time.sleep(1.2)
        r = client.post("/run", headers={"Idempotency-Key": "k-6"}, json={})
        self.assertEqual(2, len(calls))
        self.assertNotIn("replayed", r.json())

    def test_json_storage_mode_uses_files(self) -> None:
        self.env.set(CB_STORAGE="json")
        calls = []
        client = TestClient(_toy_app(calls))
        client.post("/run", headers={"Idempotency-Key": "k-7"}, json={"session_id": "j"})
        self.assertTrue(client.post("/run", headers={"Idempotency-Key": "k-7"}, json={"session_id": "j"}).json()["replayed"])
        self.assertEqual(1, len(calls))
        self.assertEqual(1, len(list((Path(os.environ["PACKING_OUTPUT_DIR"]) / "idempotency").glob("*.json"))))

    def test_gateway_pipeline_key_and_access_guard(self) -> None:
        client = TestClient(gateway.app)
        body = {"user_input": "steel frame", "session_id": "idem-pipeline", "save_artifacts": False,
                "enable_auto_confirm": True, "materials": [{"id": "m1", "part_no": "F", "length_mm": 3000,
                "width_mm": 200, "height_mm": 200, "total_weight_kg": 200, "qty": 1}]}
        h = {"Idempotency-Key": "pipe-1"}
        a = client.post("/api/pipeline", headers=h, json=body)
        b = client.post("/api/pipeline", headers=h, json=body)
        self.assertEqual((200, 200), (a.status_code, b.status_code), (a.text[:300], b.text[:300]))
        self.assertEqual(a.json()["run_id"], b.json()["run_id"])
        self.assertTrue(b.json()["replayed"])
        self.assertEqual(1, len(storage.get_storage().list_runs(session_id="idem-pipeline")))
        self.assertEqual(422, client.post("/api/pipeline", headers=h, json={**body, "container_type": "20GP"}).status_code)
        # the key sits inside the access guard: without the token there is no stored response to get
        self.env.set(CIVIL_TOKEN=TOKEN)
        remote = TestClient(gateway.app, base_url="http://remote.invalid", client=("192.0.2.1", 12345))
        self.assertEqual(401, remote.post("/api/pipeline", headers=h, json=body).status_code)

    def test_tender_link_same_upload_one_job(self) -> None:
        self.env.set(CIVIL_TOKEN=TOKEN)
        client = TestClient(gateway.app)
        root = Path(os.environ["PACKING_OUTPUT_DIR"]) / "web-link" / "idem-link"
        itt, rev_a, rev_b = (DEMO / n for n in ("facade_itt_doc.md", "facade_panels.xlsx", "facade_panels_rev_b.xlsx"))

        def send(panel: Path, key: str | None = "link-1"):
            h = {"Authorization": "Bearer " + TOKEN, **({"Idempotency-Key": key} if key else {})}
            return client.post("/api/tender/link", headers=h, data={"session_id": "idem-link"},
                               files={"tender": (itt.name, itt.read_bytes()), "panel_list": (panel.name, panel.read_bytes())})

        a, b = send(rev_a), send(rev_a)
        self.assertEqual((200, 200), (a.status_code, b.status_code), (a.text[:300], b.text[:300]))
        self.assertEqual(a.json()["job_id"], b.json()["job_id"])
        self.assertTrue(b.json()["replayed"])
        self.assertEqual(1, len(list(root.iterdir())))
        c = send(rev_b)
        self.assertEqual(422, c.status_code)
        self.assertEqual("idempotency_key_reused", c.json()["error_code"])
        self.assertEqual(1, len(list(root.iterdir())))
        self.assertEqual(400, send(rev_a, key="no spaces").status_code)
        # without a key a resend is a new job, as before
        self.assertEqual(200, send(rev_a, key=None).status_code)
        self.assertEqual(2, len(list(root.iterdir())))
        # no session_id: the server names a session; a resend with the key gets that first run back
        h = {"Authorization": "Bearer " + TOKEN, "Idempotency-Key": "link-unnamed"}
        files = lambda: {"tender": (itt.name, itt.read_bytes()), "panel_list": (rev_a.name, rev_a.read_bytes())}  # noqa: E731
        first = client.post("/api/tender/link", headers=h, files=files())
        again = client.post("/api/tender/link", headers=h, files=files())
        self.assertEqual((200, 200), (first.status_code, again.status_code), (first.text[:300], again.text[:300]))
        self.assertEqual((first.json()["session_id"], first.json()["job_id"]),
                         (again.json()["session_id"], again.json()["job_id"]))
        self.assertTrue(again.json()["replayed"])


# ---------------------------------------------------------------- atomic deliverable writes


class AtomicWriteTest(unittest.TestCase):
    def setUp(self) -> None:
        (TMP / "output").mkdir(parents=True, exist_ok=True)
        self.dir = Path(tempfile.mkdtemp(dir=TMP / "output"))
        self.target = self.dir / "report.md"

    def others(self) -> list:
        return sorted(p.name for p in self.dir.iterdir() if p.name != self.target.name)

    def test_exception_mid_write_keeps_the_old_file(self) -> None:
        sandbox.guarded_write_text(self.target, "v1 complete\n")
        with self.assertRaises(UnicodeEncodeError):
            sandbox.guarded_write_text(self.target, "v2 " + "x" * 100_000 + " é", encoding="ascii")
        self.assertEqual("v1 complete\n", self.target.read_text(encoding="utf-8"))
        self.assertEqual([], self.others())
        with self.assertRaises(TypeError):
            sandbox.guarded_write_bytes(self.dir / "new.bin", "not bytes")    # type: ignore[arg-type]
        self.assertFalse((self.dir / "new.bin").exists())
        self.assertEqual([], self.others())

    def test_bytes_on_disk_are_what_write_text_wrote(self) -> None:
        text = "line 1\nline 2 é\n"
        sandbox.guarded_write_text(self.target, text)
        reference = self.dir / "reference.md"
        reference.write_text(text, encoding="utf-8")
        self.assertEqual(reference.read_bytes(), self.target.read_bytes())
        sandbox.guarded_write_bytes(self.target, b"\x00\x01\r\n")
        self.assertEqual(b"\x00\x01\r\n", self.target.read_bytes())

    def _child(self, text_len: int, hook: str = "") -> subprocess.Popen:
        code = (f"import os, sys, time; sys.path.insert(0, {str(ROOT)!r})\n{hook}\n"
                "from packing_assistant import sandbox\n"
                f"sandbox.guarded_write_text({str(self.target)!r}, 'n' * {text_len})\n")
        return subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, env=os.environ.copy())

    def test_killed_after_the_bytes_before_the_rename(self) -> None:
        sandbox.guarded_write_text(self.target, "old")
        hook = "os.fsync = lambda fd: (print('READY', flush=True), time.sleep(60))"
        child = self._child(1_000_000, hook)
        self.assertEqual("READY", child.stdout.readline().strip())
        _kill(child)
        self.assertEqual("old", self.target.read_text(encoding="utf-8"))
        leftovers = self.others()
        self.assertEqual(1, len(leftovers))
        self.assertTrue(leftovers[0].startswith(".report.md.") and leftovers[0].endswith(".tmp"), leftovers)
        # the next write of that file clears what a writer killed over an hour ago left behind
        stale = self.dir / leftovers[0]
        os.utime(stale, (time.time() - 7200, time.time() - 7200))
        sandbox.guarded_write_text(self.target, "new")
        self.assertEqual([], self.others())

    def test_killed_at_any_moment_old_or_complete_never_partial(self) -> None:
        size = 64 * 1024 * 1024
        for delay in (0.0, 0.05, 0.2):
            sandbox.guarded_write_text(self.target, "old")
            child = self._child(size)
            deadline = time.monotonic() + 60
            while child.poll() is None and not self.others() and time.monotonic() < deadline:
                time.sleep(0.0005)
            time.sleep(delay)
            _kill(child)
            got = self.target.stat().st_size
            self.assertIn(got, (3, size), f"kill after {delay}s left {got} bytes under the deliverable's name")
            for p in self.dir.iterdir():
                if p != self.target:
                    p.unlink()


# ---------------------------------------------------------------- SQLite lock


def _kill(child: subprocess.Popen) -> None:
    """Kill the writer itself: on Windows a venv's python.exe is a launcher whose child is the interpreter."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(child.pid)], capture_output=True)
    else:
        child.kill()
    child.wait()
    if child.stdout:
        child.stdout.close()


def _hold_lock(db: str, seconds: float, started: threading.Event) -> None:
    c = sqlite3.connect(db, timeout=0, isolation_level=None)
    c.execute("BEGIN IMMEDIATE")
    c.execute("INSERT INTO runs(run_id, phase) VALUES('holder-' || hex(randomblob(4)), 'x')")
    started.set()
    time.sleep(seconds)
    c.execute("COMMIT")
    c.close()


class SqliteLockTest(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.env = Env(self.stack)
        self.db = str(Path(tempfile.mkdtemp(dir=TMP)) / "lock.db")
        self.env.set(CB_DB_PATH=self.db, CB_SQLITE_BUSY_MS="300", CB_SQLITE_WRITE_ATTEMPTS="3")
        storage.reset_storage()
        self.addCleanup(storage.reset_storage)
        self.sid = f"lock-{time.time_ns()}"
        session_store.save_session(self.sid, {"phase": "await_user_confirm", "run_id": self.sid})

    def hold(self, seconds: float) -> threading.Thread:
        started = threading.Event()
        t = threading.Thread(target=_hold_lock, args=(self.db, seconds, started))
        t.start()
        started.wait()
        return t

    def json_copy(self) -> Path:
        return session_store.RUNS_DIR / self.sid / "session_state.json"

    def test_lock_outlasting_the_retries_fails_loudly_and_writes_no_json(self) -> None:
        holder = self.hold(4.0)
        started = time.monotonic()
        with self.assertLogs("civil.session_store", level="ERROR"):
            with self.assertRaises(session_store.SessionPersistError):
                session_store.save_session(self.sid, {"phase": "done", "run_id": self.sid})
        waited = time.monotonic() - started
        holder.join()
        self.assertLess(waited, 3.5, "bounded: 3 attempts x 0.3 s + backoff")
        self.assertFalse(self.json_copy().exists(), "no JSON-only copy that a restart would disagree with")
        storage.reset_storage()                         # a restart
        self.assertEqual("await_user_confirm", session_store.load_session(self.sid)["phase"])

    def test_lock_released_during_the_retries_saves(self) -> None:
        holder = self.hold(0.5)
        session_store.save_session(self.sid, {"phase": "done", "run_id": self.sid})
        holder.join()
        storage.reset_storage()
        self.assertEqual("done", session_store.load_session(self.sid)["phase"])
        self.assertFalse(self.json_copy().exists())

    def test_gateway_answers_503(self) -> None:
        with patch.object(gateway, "save_session", side_effect=session_store.SessionPersistError("locked")):
            with self.assertRaises(HTTPException) as caught:
                gateway._store_session("gw-503", {"phase": "done"})
        self.assertEqual(503, caught.exception.status_code)
        self.assertEqual("5", caught.exception.headers["Retry-After"])
        self.assertEqual("done", gateway._SESSIONS["gw-503"]["phase"])       # RAM holds the new state

    def test_other_sqlite_errors_keep_the_json_fallback(self) -> None:
        with patch.object(session_store, "_db_write", side_effect=sqlite3.DatabaseError("disk I/O error")):
            session_store.save_session(self.sid, {"phase": "done", "run_id": self.sid})
        self.assertEqual("done", json.loads(self.json_copy().read_text(encoding="utf-8"))["phase"])


# ---------------------------------------------------------------- events (run_id, seq)


class EventsUniqueTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Path(tempfile.mkdtemp(dir=TMP)) / "events.db"

    def test_a_repeated_event_is_one_row(self) -> None:
        st = storage.Storage(self.db)
        ev = {"run_id": "r1", "seq": 1, "type": "tool_end", "tool": "x"}
        st.insert_event(ev)
        st.insert_event(ev)
        st.insert_events([ev, {"run_id": "r1", "seq": 2, "type": "done"}, {"run_id": "r1", "seq": 2, "type": "done"}])
        st.insert_events([{"run_id": "r1", "seq": None, "type": "legacy"}, {"run_id": "r1", "seq": None, "type": "legacy"}])
        rows = st.read_conn().execute("SELECT seq, type FROM events WHERE run_id='r1' ORDER BY id").fetchall()
        self.assertEqual([(1, "tool_end"), (2, "done"), (None, "legacy"), (None, "legacy")], rows)
        with self.assertLogs("civil.storage", level="WARNING"):
            st.insert_event({"run_id": "r1", "seq": 1, "type": "other"})
        self.assertEqual("tool_end", st.read_conn().execute(
            "SELECT type FROM events WHERE run_id='r1' AND seq=1").fetchone()[0])
        st.close()

    def test_an_existing_database_with_duplicates_is_migrated(self) -> None:
        c = sqlite3.connect(self.db)
        c.executescript("""CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
            seq INTEGER, ts TEXT, t_ms INTEGER, type TEXT NOT NULL, node TEXT, agent_id TEXT, parent_node TEXT,
            tool TEXT, status TEXT, duration_ms INTEGER, payload_json TEXT, archived INTEGER NOT NULL DEFAULT 0);""")
        for seq, typ in ((1, "run_start"), (2, "tool_end"), (2, "tool_end"), (3, "done"), (3, "done"), (None, "x")):
            c.execute("INSERT INTO events(run_id, seq, type, payload_json) VALUES(?,?,?,?)",
                      ("old", seq, typ, json.dumps({"run_id": "old", "seq": seq, "type": typ})))
        c.commit()
        c.close()
        with self.assertLogs("civil.storage", level="WARNING") as logs:
            st = storage.Storage(self.db)
        self.assertIn("removed 2 duplicate", "\n".join(logs.output))
        self.assertEqual([1, 2, 3, None], [e["seq"] for e in st.read_trace_events("old")])
        names = [r[1] for r in st.read_conn().execute("PRAGMA index_list(events)")]
        self.assertIn("ux_events_run_seq", names)
        st.close()
        storage.Storage(self.db).close()               # the second open does nothing

    def test_trace_append_twice_is_one_event(self) -> None:
        run = f"trace-{time.time_ns()}"
        trace_events.append_trace_event(run, {"type": "tool_end", "seq": 1, "tool": "x"}, also_global=False)
        trace_events.append_trace_event(run, {"type": "tool_end", "seq": 1, "tool": "x"}, also_global=False)
        self.assertEqual(1, len(trace_events.read_trace_jsonl(run)))
        self.assertEqual(1, storage.get_storage().read_conn().execute(
            "SELECT COUNT(*) FROM events WHERE run_id=?", (run,)).fetchone()[0])


if __name__ == "__main__":
    logging.basicConfig(level=logging.CRITICAL)
    try:
        unittest.main(verbosity=2)
    finally:
        storage.reset_storage()
        shutil.rmtree(TMP, ignore_errors=True)
