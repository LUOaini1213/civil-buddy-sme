#!/usr/bin/env python3
"""Bounded retry of one model request (packing_assistant/model_retry.py) against a local fake endpoint.

Before 2026-09-28 every Python model call sent exactly one request: 500,500,200 ended the turn at the first 500,
a 429's Retry-After was ignored and its message blamed the model name and key. Now a 429 / 5xx / timeout /
dropped connection is retried at most twice, inside the caller's existing timeout; a 400 is not retried; a hung
endpoint is still bounded by the read timeout; and a tool that ran before the failure is not run again.
No key, no network: the server is 127.0.0.1 and the key is a fixture string.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "demo"))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from packing_assistant import model_retry  # noqa: E402

KEY = "local-fixture-key-SECRET"
OK = {"choices": [{"message": {"role": "assistant", "content": "done"}}]}


def final(text):
    return {"choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}]}


def tool_call(name, arguments):
    return {"choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": "",
        "tool_calls": [{"id": "call_1", "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}]}}]}


class Fake:
    """A scripted OpenAI-compatible endpoint. Each step is (status, body, headers), "hang" or "stream:<text>"."""

    def __init__(self, *steps):
        self.steps, self.stamps = list(steps), []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                fake.stamps.append(time.monotonic())
                step = fake.steps.pop(0) if fake.steps else (200, OK, {})
                if step == "hang":
                    time.sleep(20)
                    self.close_connection = True
                    return
                if isinstance(step, str) and step.startswith("stream:"):
                    text = json.dumps({"choices": [{"delta": {"content": step[7:]}, "finish_reason": "stop"}]})
                    data = f"data: {text}\n\ndata: [DONE]\n\n".encode()
                    status, headers, ctype = 200, {}, "text/event-stream"
                else:
                    status, body, headers = step
                    data, ctype = json.dumps(body).encode(), "application/json"
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                for k, v in headers.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}/v1"

    @property
    def gaps(self):
        return [self.stamps[i] - self.stamps[i - 1] for i in range(1, len(self.stamps))]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


E500 = (500, {"error": "upstream boom"}, {})


class Case(unittest.TestCase):
    def setUp(self):
        saved = {k: os.environ.get(k) for k in ("CIVIL_API_KEY", "CIVIL_API_BASE", "CIVIL_MODEL", "OPENAI_API_KEY",
                                                "DEEPSEEK_API_KEY", "LLM_API_KEY", "CIVIL_MODEL_TIMEOUT",
                                                "CIVIL_LLM_READ_TIMEOUT", "CIVIL_MODEL_RETRIES", "LLM_TIMEOUT")}
        self.addCleanup(lambda: [os.environ.__setitem__(k, v) if v is not None else os.environ.pop(k, None)
                                 for k, v in saved.items()])
        for k in saved:
            os.environ.pop(k, None)

    def serve(self, *steps) -> Fake:
        fake = Fake(*steps)
        self.addCleanup(fake.close)
        os.environ.update(CIVIL_API_KEY=KEY, CIVIL_API_BASE=fake.base, CIVIL_MODEL="fixture")
        return fake


class Policy(unittest.TestCase):
    def test_full_jitter_backoff_doubles_from_one_second_and_caps_at_eight(self):
        self.assertEqual([model_retry.backoff(n, rng=lambda: 1.0) for n in range(6)], [1, 2, 4, 8, 8, 8])
        self.assertEqual(model_retry.backoff(3, rng=lambda: 0.0), 0.0)
        self.assertEqual(model_retry.MAX_RETRIES, 2)

    def test_only_429_and_5xx_are_retryable_statuses(self):
        self.assertEqual([s for s in (400, 401, 403, 404, 408, 409, 422, 429, 500, 502, 503, 504, 599)
                          if model_retry.retryable_status(s)], [429, 500, 502, 503, 504, 599])

    def test_retry_after_seconds_and_http_date(self):
        self.assertEqual(model_retry.parse_retry_after("1"), 1.0)
        self.assertIsNone(model_retry.parse_retry_after(""))
        self.assertIsNone(model_retry.parse_retry_after("soon"))
        self.assertAlmostEqual(model_retry.parse_retry_after("Wed, 21 Oct 2015 07:28:05 GMT", now=1445412480.0), 5.0)

    def test_the_error_excerpt_drops_secrets_and_stops_at_300_characters(self):
        text = f"bad key {KEY}; Authorization: Bearer abc.def.ghi; token sk-abcdefghijklmnop " + "x " * 400
        out = model_retry.safe_excerpt(text, (KEY,))
        self.assertNotIn(KEY, out)
        self.assertNotIn("abc.def.ghi", out)
        self.assertNotIn("sk-abcdefghijklmnop", out)
        self.assertLessEqual(len(out), 300)

    def test_a_retry_that_would_not_fit_the_budget_is_not_made(self):
        calls, slept = [], []

        def attempt(remaining):
            calls.append(remaining)
            raise model_retry.Transient(RuntimeError("gave up"), reason="HTTP 503", retry_after=2.5)

        clock = iter([0.0, 0.0, 0.0, 0.1, 2.6, 2.6]).__next__
        with self.assertRaisesRegex(RuntimeError, "gave up"):
            model_retry.run(attempt, budget_s=4.0, label="t", wait=slept.append, clock=clock)
        self.assertEqual((len(calls), slept), (2, [2.5]))   # 4 s budget: one 2.5 s wait fits, a second does not

    def test_retry_after_over_the_cap_is_reported_not_waited(self):
        calls = []

        def attempt(remaining):
            calls.append(1)
            raise model_retry.Transient(RuntimeError("busy"), reason="HTTP 429", retry_after=60)

        with self.assertRaisesRegex(RuntimeError, "busy"), self.assertLogs("civil.model_retry") as logs:
            model_retry.run(attempt, budget_s=180, label="t", wait=self.fail)
        self.assertEqual(len(calls), 1)
        self.assertIn("over the 8 s cap", logs.output[-1])

    def test_civil_model_retries_can_turn_it_off(self):
        os.environ["CIVIL_MODEL_RETRIES"] = "0"
        self.addCleanup(os.environ.pop, "CIVIL_MODEL_RETRIES", None)
        self.assertEqual(model_retry.max_retries(), 0)
        os.environ["CIVIL_MODEL_RETRIES"] = "9"
        self.assertEqual(model_retry.max_retries(), 2)


class ModelClient(Case):
    """packing_assistant/runtime/model_client.complete: the model-driven turn's one request."""

    def complete(self):
        from packing_assistant.runtime import model_client
        return model_client.complete([{"role": "user", "content": "hi"}])

    def test_500_500_200_succeeds_on_the_third_attempt_and_logs_each_retry(self):
        fake = self.serve(E500, E500, (200, OK, {}))
        with self.assertLogs("civil.model_retry", "WARNING") as logs:
            self.assertEqual(self.complete()["content"], "done")
        self.assertEqual(len(fake.stamps), 3)
        self.assertEqual(len(logs.output), 2)
        self.assertIn("attempt 1/3 failed (HTTP 500)", logs.output[0])
        self.assertIn("upstream boom", logs.output[0])

    def test_429_with_retry_after_1_waits_about_a_second(self):
        fake = self.serve((429, {"error": "rate"}, {"Retry-After": "1"}), (200, OK, {}))
        self.assertEqual(self.complete()["content"], "done")
        self.assertEqual(len(fake.stamps), 2)
        self.assertGreaterEqual(fake.gaps[0], 0.95)
        self.assertLess(fake.gaps[0], 6.0)

    def test_400_is_not_retried_and_the_body_goes_to_the_log_not_the_reply(self):
        from packing_assistant.runtime.model_client import ModelError
        fake = self.serve((400, {"error": {"message": f"model 'fixture' does not exist (key {KEY}); private document"}},
                           {}))
        with self.assertRaises(ModelError) as caught, self.assertLogs("civil.model_retry", "WARNING") as logs:
            self.complete()
        self.assertEqual(len(fake.stamps), 1)
        self.assertIn("400", str(caught.exception))
        self.assertNotIn("private document", str(caught.exception))   # the message becomes the turn's reply
        self.assertNotIn(KEY, str(caught.exception))
        self.assertIn("does not exist", logs.output[-1])
        self.assertNotIn(KEY, "\n".join(logs.output))

    def test_a_5xx_body_stays_out_of_the_final_message(self):
        from packing_assistant.runtime.model_client import ModelError
        os.environ["CIVIL_MODEL_RETRIES"] = "0"
        self.serve((503, {"error": f"echoed {KEY} and a private document"}, {}))
        with self.assertRaises(ModelError) as caught, self.assertLogs("civil.model_retry", "WARNING") as logs:
            self.complete()
        self.assertIn("503", str(caught.exception))
        self.assertNotIn("private document", str(caught.exception))
        self.assertNotIn(KEY, str(caught.exception))
        self.assertNotIn(KEY, "\n".join(logs.output))

    def test_persistent_5xx_gives_up_after_three_attempts_and_does_not_blame_the_key(self):
        from packing_assistant.runtime.model_client import ModelError
        fake = self.serve(E500, E500, E500, (200, OK, {}))
        with patch("random.random", return_value=0.0), self.assertRaises(ModelError) as caught:
            self.complete()
        self.assertEqual(len(fake.stamps), 3)
        self.assertIn("已尝试 3 次", str(caught.exception))
        self.assertNotIn("Key", str(caught.exception))

    def test_a_hang_is_still_bounded_by_the_read_timeout(self):
        from packing_assistant.runtime.model_client import ModelError
        os.environ["CIVIL_MODEL_TIMEOUT"] = "5"
        fake = self.serve("hang", (200, OK, {}))
        started = time.monotonic()
        with self.assertRaisesRegex(ModelError, "超时"):
            self.complete()
        self.assertLess(time.monotonic() - started, 9.0)
        self.assertEqual(len(fake.stamps), 1)

    def test_a_cancel_during_the_backoff_stops_the_retry(self):
        from packing_assistant.runtime import model_client
        fake = self.serve((429, {"error": "rate"}, {"Retry-After": "5"}), (200, OK, {}))
        event = threading.Event()
        threading.Timer(1.0, event.set).start()
        started = time.monotonic()
        with self.assertRaises(model_client.ModelCancelled):
            model_client.complete([{"role": "user", "content": "hi"}], cancel_event=event)
        self.assertLess(time.monotonic() - started, 4.0)
        self.assertEqual(len(fake.stamps), 1)


class DemoLLM(Case):
    """demo/llm.py: the workbench's chat() and stream_plain()."""

    def test_chat_retries_5xx_and_honours_retry_after(self):
        from llm import chat
        fake = self.serve(E500, (429, {"error": "rate"}, {"Retry-After": "1"}), (200, OK, {}))
        self.assertEqual(chat([{"role": "user", "content": "hi"}])["content"], "done")
        self.assertEqual(len(fake.stamps), 3)
        self.assertGreaterEqual(fake.gaps[1], 0.95)

    def test_chat_400_is_one_request_and_the_body_stays_out_of_the_message(self):
        from llm import LLMError, chat
        fake = self.serve((400, {"error": f"echoed {KEY} and a private document"}, {}))
        with self.assertRaises(LLMError) as caught:
            chat([{"role": "user", "content": "hi"}])
        self.assertEqual(len(fake.stamps), 1)
        self.assertNotIn("private document", str(caught.exception))

    def test_chat_hang_is_bounded_by_the_read_timeout(self):
        from llm import LLMError, chat
        os.environ["CIVIL_LLM_READ_TIMEOUT"] = "3"
        fake = self.serve("hang", (200, OK, {}))
        started = time.monotonic()
        with self.assertRaisesRegex(LLMError, "超时"):
            chat([{"role": "user", "content": "hi"}])
        self.assertLess(time.monotonic() - started, 7.0)
        self.assertEqual(len(fake.stamps), 1)

    def test_a_cancel_while_the_request_is_in_flight_sends_nothing_more_and_logs_no_retry(self):
        import logging

        import turn_control
        from llm import LLMError, chat
        for how in ("turn", "event"):
            with self.subTest(how=how):
                fake = self.serve("hang", (200, OK, {}))
                control, event = turn_control.TurnControl("retry-cancel-" + how), threading.Event()
                threading.Timer(1.0, control.request_cancel if how == "turn" else event.set).start()
                records = []
                handler = logging.Handler(logging.DEBUG)
                handler.emit = records.append
                log = logging.getLogger("civil.model_retry")
                log.addHandler(handler)
                self.addCleanup(log.removeHandler, handler)
                started = time.monotonic()
                with self.assertRaises(LLMError), turn_control.using(control):
                    chat([{"role": "user", "content": "hi"}], cancel_event=event)
                self.assertLess(time.monotonic() - started, 4.0)
                time.sleep(1.5)   # a retry, had one been scheduled, would have been sent by now
                self.assertEqual(len(fake.stamps), 1)
                self.assertFalse([r for r in records if "retrying" in r.getMessage()])

    def test_stream_retries_before_the_first_text_only(self):
        from llm import stream_plain
        fake = self.serve((503, {"error": "warming up"}, {}), "stream:你好")
        self.assertEqual("".join(stream_plain([{"role": "user", "content": "hi"}])), "你好")
        self.assertEqual(len(fake.stamps), 2)


class SharedLLM(Case):
    """packing_assistant/llm.chat (LangChain, LLM_TIMEOUT 8 s, SDK retries off)."""

    def test_500_then_200_returns_the_reply_and_400_is_not_retried(self):
        from packing_assistant import llm
        fake = self.serve(E500, (200, final("local reply"), {}))
        self.assertEqual(llm.chat("system", "user"), "local reply")
        self.assertEqual(len(fake.stamps), 2)
        fake = self.serve((400, {"error": {"message": f"bad request {KEY}"}}, {}))
        out = llm.chat("system", "user")
        self.assertTrue(out.startswith("[LLM_ERROR]"), out)
        self.assertNotIn(KEY, out)
        self.assertEqual(len(fake.stamps), 1)

    def test_a_refused_connection_is_not_retried(self):
        # langchain-openai 1.x wraps the SDK error again, so httpx.ConnectError is two causes down; before this
        # was checked through the whole chain, a dead endpoint was retried until the 8 s budget ran out.
        import socket
        from packing_assistant import llm
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        os.environ.update(CIVIL_API_KEY=KEY, CIVIL_API_BASE=f"http://127.0.0.1:{port}/v1", CIVIL_MODEL="fixture")
        with self.assertNoLogs("civil.model_retry", "WARNING"):
            out = llm.chat("system", "user")
        self.assertTrue(out.startswith("[LLM_ERROR]"), out)
        self.assertNotIn(KEY, out)


class ToolsAreNotRerun(Case):
    def test_a_5xx_after_a_tool_ran_retries_only_the_model_request(self):
        from packing_assistant.runtime import workspace
        from packing_assistant.runtime.turn import run_turn

        tmp = tempfile.TemporaryDirectory(prefix="civil-retry-")
        self.addCleanup(tmp.cleanup)
        job = Path(tmp.name).resolve()
        (job / "CIVIL.md").write_text("- 项目：东桥改造工程（二标段）\n- 辖区：SG\n", encoding="utf-8")
        (job / "现场记录.txt").write_text("现场记录\n木工8人进场\n浇筑混凝土45方\n", encoding="utf-8")
        self.addCleanup(os.chdir, Path.cwd())
        self.addCleanup(workspace.deactivate)
        os.chdir(job)
        with patch.object(Path, "home", return_value=job / "no-home"):
            workspace.activate(job)
        fake = self.serve((200, tool_call("run_skill", {"skill_id": "pm-daily", "files": ["现场记录.txt"]}), {}),
                          E500, (200, final("日报已整理。"), {}))
        with patch("random.random", return_value=0.0):
            out = run_turn("整理日报，日期：2031年5月6日，部位：东桥3号墩，天气：晴，出勤：钢筋工12人",
                           session_id="retry", mode="model")
        self.assertEqual(len(fake.stamps), 3, out.get("reply"))
        self.assertEqual(out.get("tools_run"), ["run_skill"], out.get("reply"))
        self.assertNotEqual(out.get("error_code"), "model_unavailable", out.get("reply"))
        drafts = sorted(p.name for p in (job / ".civil-buddy" / "out").rglob("pm-daily__*"))
        self.assertEqual(drafts, ["pm-daily__log.docx", "pm-daily__log.md", "pm-daily__log.xlsx"])


if __name__ == "__main__":
    unittest.main()
