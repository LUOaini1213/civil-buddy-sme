#!/usr/bin/env python3
"""Cancellation through the workbench API, real blocked HTTP and tool boundaries."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from threading import Event, Thread, enumerate as threads
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "demo"))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from fastapi.testclient import TestClient
import app as workbench
import chat_service
import llm as demo_llm
import store
import turn_control
import uploads
from packing_assistant import expert_turn, llm
from packing_assistant.runtime import agent_loop, memory, session_handoff, session_packing
from packing_assistant.runtime.scheduler import Scheduler
from packing_assistant.runtime.tool_engine import get_engine
from test_workbench_flow import sse_events


def eventually(predicate, seconds=3):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("Condition did not become true before deadline")


class BlockedModel(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.server.send_headers:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            chunk = ('data: ' + json.dumps({"choices": [{"delta": {"content": "已读取一段"}}]}, ensure_ascii=False) + '\n\n').encode()
            self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
            self.wfile.flush()
        self.server.started.set()
        self.connection.settimeout(0.1)
        while not self.server.stop_probe.is_set():
            try:
                if not self.connection.recv(1):
                    self.server.disconnected.set()
                    break
            except socket.timeout:
                continue
            except OSError:
                self.server.disconnected.set()
                break


class WorkbenchCancelTests(unittest.TestCase):
    def setUp(self):
        output = ROOT / "output"
        output.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="test-workbench-cancel-", dir=output)
        self.root = Path(temporary.name).resolve()
        self.root.relative_to(output.resolve())
        self.addCleanup(temporary.cleanup)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {"PYTHON_DOTENV_DISABLED": "1", "CIVIL_JOB_ROOT": "",
            "CIVIL_SANDBOX": "workspace-write", "CIVIL_APPROVAL": "on-request"}))
        self.scheduler = Scheduler()
        for module, attribute, value in (
            (workbench, "OUT_ROOT", self.root), (agent_loop, "_OUT", self.root),
            (memory, "_OUT", self.root), (expert_turn, "_OUT", self.root),
            (session_handoff, "_DIR", self.root), (session_packing, "_DIR", self.root),
            (uploads, "UPLOAD_ROOT", self.root), (store, "DATA", self.root / "catalog.json"),
            (chat_service, "_ACTIVE", set()),
            (llm, "_RUNTIME_LLM", {"api_key": "", "base_url": "http://127.0.0.1:1", "model": "cancel-test"}),
        ):
            self.stack.enter_context(patch.object(module, attribute, value))
        self.stack.enter_context(patch.object(agent_loop, "get_scheduler", return_value=self.scheduler))
        self.stack.enter_context(patch.object(llm, "chat", side_effect=AssertionError("No online model allowed")))
        self.client = self.stack.enter_context(TestClient(workbench.app))
        self.pool = self.stack.enter_context(ThreadPoolExecutor(max_workers=3))
        self.sid = "cancel-" + uuid4().hex[:16]

    def post(self, message="你好", **options):
        return self.client.post("/api/chat", json={"message": message, "session_id": self.sid, **options})

    def cancel(self, sid=None):
        response = self.client.post(f"/api/sessions/{sid or self.sid}/cancel")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def done(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        results = [e["data"] for e in sse_events(response) if e["event"] == "done"]
        self.assertEqual(len(results), 1, response.text)
        return results[0]

    def assert_released(self):
        self.assertNotIn(self.sid, chat_service._ACTIVE)
        self.assertNotIn(self.sid, self.scheduler._locks)
        self.assertFalse(turn_control.status(self.sid)["active"])

    def test_idle_and_finished_cancel_are_explicit_noops(self):
        self.assertEqual(self.cancel()["state"], "idle")
        self.assertFalse(self.cancel()["cancel_requested"])
        self.assertEqual(self.done(self.post())["state"], "done")
        completed = self.cancel()
        self.assertFalse(completed["cancel_requested"])
        self.assertEqual(completed["state"], "done")
        self.assert_released()

    def test_short_lease_remains_usable_for_export_and_release_is_idempotent(self):
        lease = chat_service.SessionLease(self.sid)
        with self.assertRaises(chat_service.SessionBusy):
            chat_service.SessionLease(self.sid)
        lease.release()
        lease.release()
        self.assert_released()
        self.assertTrue(self.done(self.post())["ok"])

    def test_terminal_state_is_persisted_before_session_can_be_reused(self):
        entered, proceed = Event(), Event()
        original = chat_service._write_state

        def paused(path, payload):
            if payload.get("session_id") == self.sid and payload.get("state") == "done":
                entered.set()
                self.assertTrue(proceed.wait(5), "test must release the terminal state write")
            return original(path, payload)

        with patch.object(chat_service, "_write_state", side_effect=paused):
            future = self.pool.submit(self.post)
            try:
                self.assertTrue(entered.wait(3), "producer did not reach terminal persistence")
                state = chat_service.turn_state_on_disk(self.root, self.sid)
                self.assertEqual(state["state"], "running")
                self.assertTrue(turn_control.status(self.sid)["active"])
                self.assertIn(self.sid, chat_service._ACTIVE)
                self.assertEqual(self.post().status_code, 409)
                self.assertEqual(chat_service.live_seq(self.sid)["turn_id"], state["turn_id"])
            finally:
                proceed.set()
                response = future.result(timeout=5)
        self.assertTrue(self.done(response)["ok"])
        self.assert_released()
        completed = chat_service.turn_state_on_disk(self.root, self.sid)
        self.assertEqual(completed["state"], "done")
        self.assertTrue(completed["finished_at"])
        self.assertTrue(self.done(self.post())["ok"])
        self.assertNotEqual(chat_service.turn_state_on_disk(self.root, self.sid)["turn_id"], completed["turn_id"])
        previous = chat_service._read_state(chat_service._state_path(self.root, self.sid, completed["turn_id"]))
        self.assertEqual(previous, completed)

    def test_terminal_persistence_failure_does_not_strand_session_or_consumer(self):
        with patch.object(chat_service, "_finish_state", side_effect=OSError("fixture state write failed")), \
                self.assertLogs(chat_service.logger, level="ERROR") as logs:
            response = self.pool.submit(self.post).result(timeout=5)
        self.assertTrue(self.done(response)["ok"])
        self.assertTrue(any("Turn finalization failed" in message for message in logs.output))
        self.assert_released()
        self.assertTrue(self.done(self.post())["ok"])

    def test_cancel_during_admission_prevents_any_tool(self):
        entered, proceed = Event(), Event()
        original = chat_service.prepare_turn

        def paused(*args, **kwargs):
            entered.set()
            self.assertTrue(proceed.wait(4))
            return original(*args, **kwargs)

        with patch.object(chat_service, "prepare_turn", side_effect=paused):
            future = self.pool.submit(self.post, "写一份项目日报", expert_ids=["pm-daily"])
            self.assertTrue(entered.wait(3))
            try:
                self.assertTrue(self.cancel()["cancel_requested"])
            finally:
                proceed.set()
            done = self.done(future.result(timeout=4))
        self.assertTrue(done["cancelled"])
        self.assertFalse(done["wrote"])
        self.assertFalse(list(self.root.rglob("*.xlsx")))
        self.assert_released()

    def _blocked_model_case(self, send_headers):
        server = ThreadingHTTPServer(("127.0.0.1", 0), BlockedModel)
        server.send_headers = send_headers
        server.started, server.disconnected, server.stop_probe = Event(), Event(), Event()
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            config = {"api_key": "local-probe-not-a-secret", "base_url": f"http://127.0.0.1:{server.server_port}", "model": "probe"}
            with patch.dict(llm._RUNTIME_LLM, config), patch.object(workbench, "has_key", return_value=True):
                future = self.pool.submit(self.post, "解释一下土木资料整理")
                self.assertTrue(server.started.wait(3))
                started = time.monotonic()
                response = self.cancel()
                self.assertTrue(response["cancel_requested"])
                done = self.done(future.result(timeout=3))
                self.assertLess(time.monotonic() - started, 3)
                self.assertTrue(server.disconnected.wait(2), "The blocked upstream socket was not interrupted")
            self.assertEqual(done["state"], "cancelled")
            self.assertFalse(done["ok"])
            self.assertFalse(done["deliverables"])
            restored = self.client.get(f"/api/sessions/{self.sid}").json()
            self.assertEqual(restored["turn_state"]["state"], "cancelled")
            self.assertIn("已取消", restored["transcript"][-1]["text"])
            self.assert_released()
            eventually(lambda: not any(t.name == "civil-model-" + self.sid for t in threads()))
            self.assertTrue(self.done(self.post())["ok"])
        finally:
            server.stop_probe.set()
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_blocked_real_http_model_connection_is_closed_and_partial_restored(self):
        self._blocked_model_case(send_headers=True)

    def test_model_waiting_for_response_headers_is_cancelled_without_leaking_reader(self):
        self._blocked_model_case(send_headers=False)

    def test_current_tool_finishes_files_then_cancel_stops_later_posts(self):
        engine = get_engine()
        original = engine.execute
        entered, proceed = Event(), Event()
        calls = []

        def paused(name, *args, **kwargs):
            calls.append(name)
            result = original(name, *args, **kwargs)
            if name == "pm-daily__log":
                entered.set()
                self.assertTrue(proceed.wait(5))
            return result

        with patch.object(engine, "execute", side_effect=paused):
            future = self.pool.submit(self.post, "写一份本岗草稿", expert_ids=["pm-daily", "admin-office"])
            try:
                # The first real tool must finish its files before cancellation is tested.
                # Allow loaded builders to reach that boundary; post-cancel limits stay below.
                self.assertTrue(entered.wait(30), "first tool did not finish its files")
                requested = self.cancel()
                self.assertEqual(requested["state"], "cancelling")
                self.assertTrue(requested["active"])
                self.assertEqual(self.post().status_code, 409)
                self.assertEqual(self.cancel()["state"], "cancelling")
                other = self.client.post("/api/chat", json={"message": "你好", "session_id": "other-" + uuid4().hex[:16]})
                self.assertTrue(self.done(other)["ok"])
            finally:
                proceed.set()
            done = self.done(future.result(timeout=4))
        self.assertTrue(done["cancelled"])
        self.assertTrue(done["wrote"])
        self.assertEqual(calls, ["pm-daily__log"])
        self.assertGreaterEqual({Path(f["path"]).suffix for f in done["deliverables"]}, {".md", ".xlsx"})
        for item in done["deliverables"]:
            response = self.client.get("/api/file", params={"path": item["path"]})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, Path(item["path"]).read_bytes())
        detail = self.client.get(f"/api/sessions/{self.sid}").json()
        self.assertEqual(detail["deliverables"], done["deliverables"])
        self.assertEqual(detail["turn_state"]["state"], "cancelled")
        self.assert_released()
        self.assertTrue(self.done(self.post())["ok"])

    def test_cancel_at_tool_boundary_prevents_follow_on_export(self):
        event = Event()
        calls = []

        class Engine:
            def execute(self, name, *args, **kwargs):
                calls.append(name)
                event.set()
                return {"ok": True, "data": {"markdown": "# pending export"}}

        with patch.object(agent_loop, "_plan_calls", return_value={"calls": [{"name": "pack-ship__export", "arguments": {}}]}):
            result = agent_loop.run_agent("写一份装柜作业单", session_id=self.sid, expert_id="pack-ship", p0_confirmed=True,
                                          tools=Engine(), scheduler=self.scheduler, cancel_event=event)
        self.assertEqual(calls, ["pack-ship__export"])
        self.assertEqual(result["state"], "cancelled")
        self.assertFalse(result["wrote"])
        self.assertFalse(result["files"])
        self.assert_released()

    # A disconnect detaches; it does not cancel. The turn keeps its lease, finishes and persists,
    # and the page picks the result up from GET /api/sessions/{sid}. What ends a turn is the stop
    # button (POST /cancel) or, with nobody connected for too long, the server itself.

    def _detached_stream(self, runner):
        lease = chat_service.SessionLease(self.sid)
        turn = chat_service.prepare_turn(self.root, {"message": "你好", "session_id": self.sid})
        stream = chat_service.stream_turn(self.root, turn, key_available=True, plain_runner=runner, lease=lease)
        next(stream)
        return lease, stream

    def test_disconnected_consumer_lets_the_turn_finish_and_keeps_the_session_busy_meanwhile(self):
        entered, proceed = Event(), Event()

        def slow(_):
            entered.set()
            proceed.wait(4)
            yield {"event": "done", "data": {"text": "后台写完的回复"}}

        lease, stream = self._detached_stream(slow)
        try:
            self.assertTrue(entered.wait(2))
            stream.close()
            # Mirrors the transport background/finally callback; it must not free
            # a still-running producer early, which owns result persistence.
            lease.disconnect()
            time.sleep(0.3)
            self.assertTrue(turn_control.status(self.sid)["active"], "a disconnect must not end the turn")
            self.assertFalse(turn_control.status(self.sid)["cancel_requested"])
            with self.assertRaises(chat_service.SessionBusy):
                chat_service.SessionLease(self.sid)
            proceed.set()
            eventually(lambda: not turn_control.status(self.sid)["active"])
        finally:
            proceed.set()
            stream.close()
        detail = self.client.get(f"/api/sessions/{self.sid}").json()
        self.assertEqual(detail["turn_state"]["state"], "done")
        self.assertIn("后台写完的回复", detail["transcript"][-1]["text"])
        self.assertNotIn("已取消", detail["transcript"][-1]["text"])
        self.assert_released()

    def test_a_turn_nobody_returns_to_is_stopped_by_the_server_and_says_so(self):
        entered, proceed = Event(), Event()

        def stuck(_):
            entered.set()
            proceed.wait(6)
            yield {"event": "token", "data": {"text": "应被丢弃"}}

        with patch.dict(os.environ, {"CIVIL_DETACHED_TURN_SECONDS": "0.4"}):
            lease, stream = self._detached_stream(stuck)
            try:
                self.assertTrue(entered.wait(2))
                started = time.monotonic()
                stream.close()
                lease.disconnect()
                eventually(lambda: not turn_control.status(self.sid)["active"])
                waited = time.monotonic() - started
            finally:
                proceed.set()
                stream.close()
        self.assertGreaterEqual(waited, 0.4, "stopped before the limit: that is the old contract")
        detail = self.client.get(f"/api/sessions/{self.sid}").json()
        self.assertEqual(detail["turn_state"]["state"], "cancelled")
        self.assertIn("页面断开后一直没有回来", detail["transcript"][-1]["text"])
        self.assertNotIn("应被丢弃", detail["transcript"][-1]["text"])
        self.assert_released()

    def test_the_limit_is_bounded_and_zero_is_the_old_contract(self):
        for raw, expected in (("", 600.0), ("90", 90.0), ("0", 0.0), ("-1", 600.0), ("inf", 600.0),
                              ("nan", 600.0), ("soon", 600.0)):
            with patch.dict(os.environ, {"CIVIL_DETACHED_TURN_SECONDS": raw}):
                self.assertEqual(chat_service.detached_turn_seconds(), expected, raw)
        entered, proceed = Event(), Event()

        def stuck(_):
            entered.set()
            proceed.wait(4)
            yield {"event": "token", "data": {"text": "应被丢弃"}}

        with patch.dict(os.environ, {"CIVIL_DETACHED_TURN_SECONDS": "0"}):
            lease, stream = self._detached_stream(stuck)
            try:
                self.assertTrue(entered.wait(2))
                stream.close()
                lease.disconnect()
                eventually(lambda: not turn_control.status(self.sid)["active"], seconds=1.5)
            finally:
                proceed.set()
                stream.close()
        self.assertEqual(self.client.get(f"/api/sessions/{self.sid}").json()["turn_state"]["state"], "cancelled")
        self.assert_released()

    def test_a_finished_turn_leaves_no_watchdog_behind(self):
        def quick(_):
            yield {"event": "done", "data": {"text": "很快就好"}}

        with patch.dict(os.environ, {"CIVIL_DETACHED_TURN_SECONDS": "30"}):
            lease, stream = self._detached_stream(quick)
            list(stream)
            lease.disconnect()
        eventually(lambda: not turn_control.status(self.sid)["active"])
        eventually(lambda: not any(t.name == "civil-detached-" + self.sid for t in threads()))
        self.assert_released()

    def _real_http_disconnect(self, after_disconnect, tail_tokens=0):
        import httpx
        import uvicorn

        proceed = Event()

        def paused(_):
            yield {"event": "token", "data": {"text": "部分回复"}}
            proceed.wait(8)
            for index in range(tail_tokens):
                yield {"event": "token", "data": {"text": f"第{index}段"}}
            yield {"event": "done", "data": {"text": "完成回复"}}

        with socket.socket() as bound:
            bound.bind(("127.0.0.1", 0))
            port = bound.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(workbench.app, host="127.0.0.1", port=port,
                                               log_level="error", access_log=False, lifespan="off"))
        worker = Thread(target=server.run, daemon=True)
        with patch.object(workbench, "run_plain", side_effect=paused), patch.object(workbench, "has_key", return_value=True):
            worker.start()
            try:
                eventually(lambda: server.started)
                with httpx.Client(timeout=3) as client:
                    with client.stream("POST", f"http://127.0.0.1:{port}/api/chat",
                                       json={"message": "你好", "session_id": self.sid}) as response:
                        self.assertEqual(response.status_code, 200)
                        for line in response.iter_lines():
                            if "部分回复" in line:
                                break
                    # The socket is gone. Long enough for the server to have noticed (it writes a
                    # heartbeat every 0.5 s), and the turn must still be there.
                    time.sleep(1.2)
                    running = client.get(f"http://127.0.0.1:{port}/api/sessions/{self.sid}").json()
                    self.assertTrue(running["turn_state"]["active"], "a dropped connection must not end the turn")
                    self.assertFalse(running["turn_state"]["cancel_requested"])
                    after_disconnect(client, port, proceed)
                    eventually(lambda: not turn_control.status(self.sid)["active"])
                    restored = client.get(f"http://127.0.0.1:{port}/api/sessions/{self.sid}").json()
                self.assert_released()
                return restored
            finally:
                proceed.set()
                server.should_exit = True
                worker.join(timeout=4)
                self.assertFalse(worker.is_alive())

    def test_real_http_disconnect_lets_the_turn_finish_without_stranding_a_lease(self):
        restored = self._real_http_disconnect(lambda client, port, proceed: proceed.set())
        self.assertEqual(restored["turn_state"]["state"], "done")
        self.assertIn("完成回复", restored["transcript"][-1]["text"])
        self.assertNotIn("已取消", restored["transcript"][-1]["text"])

    def test_a_long_reply_after_a_real_disconnect_is_not_stuck_behind_a_full_queue(self):
        # Every token is an event and the relay queue holds 64. A real disconnect does not close
        # the stream generator, so unless the lease says "detached" the producer waits for a
        # reader that never comes: the turn never ends and the session stays busy for good.
        restored = self._real_http_disconnect(lambda client, port, proceed: proceed.set(), tail_tokens=300)
        self.assertEqual(restored["turn_state"]["state"], "done")
        self.assertIn("完成回复", restored["transcript"][-1]["text"])

    def test_a_detached_turn_is_still_stopped_by_an_explicit_cancel(self):
        def stop(client, port, proceed):
            answer = client.post(f"http://127.0.0.1:{port}/api/sessions/{self.sid}/cancel").json()
            self.assertTrue(answer["cancel_requested"])

        restored = self._real_http_disconnect(stop)
        self.assertEqual(restored["turn_state"]["state"], "cancelled")
        self.assertIn("部分回复", restored["transcript"][-1]["text"])
        self.assertIn("本轮已取消", restored["transcript"][-1]["text"])
        self.assertNotIn("页面断开后", restored["transcript"][-1]["text"])

if __name__ == "__main__":
    unittest.main()
