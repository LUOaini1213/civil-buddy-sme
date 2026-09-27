#!/usr/bin/env python3
"""The network door of both web apps (packing_assistant/access_guard.py): this machine, or whoever holds CIVIL_TOKEN.

Loopback trust never survives a proxy, a token in the URL only sets an HttpOnly cookie, a
non-loopback bind without a token refuses to start, and the routes the review found open
(CORS echo, /api/artifact, the TMS mode switch, the model Base URL) are closed.
"""

from __future__ import annotations

from contextlib import ExitStack
import hmac
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "demo"))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import app as workbench  # demo/app.py
import uploads
from gateway import app as gateway
from packing_assistant import llm as shared_llm, run_artifacts, storage, tms_booking
from packing_assistant.ws_hub import HUB

TOKEN = "s3cret"
BEARER = {"Authorization": "Bearer " + TOKEN}
REMOTE = {"base_url": "http://remote.invalid", "client": ("192.0.2.1", 12345)}


def guard():
    from packing_assistant import access_guard  # imported late so a missing module fails only its own tests
    return access_guard


def scope(client, host, version="1.1", **headers):
    raw = [(b"host", host.encode())] + [(k.replace("_", "-").encode(), v.encode()) for k, v in headers.items()]
    return {"type": "http", "http_version": version, "client": (client, 5000) if client else None, "headers": raw}


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ))
        for key in ("CIVIL_TOKEN", "CIVIL_ALLOW_OPEN_LAN", "CIVIL_JOB_ROOT", "PACKING_TMS_MODE", "UVICORN_HOST"):
            os.environ.pop(key, None)
        tmp = Path(tempfile.mkdtemp(prefix="test-access-guard-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        self.tmp = tmp
        for module, name, value in ((workbench, "OUT_ROOT", tmp / "out"), (uploads, "UPLOAD_ROOT", tmp / "out"),
                                    (uploads, "LEGACY_UPLOAD_ROOT", tmp / "no-legacy")):
            self.stack.enter_context(patch.object(module, name, value))
        (tmp / "out").mkdir()
        # Startup maintenance must not back up a real database while this test starts the gateway.
        self.stack.enter_context(patch.object(storage, "storage_mode", return_value="json"))

    def detail(self, response) -> str:
        return response.json()["detail"]


class GatewayTests(Case):
    def test_gateway_cors_does_not_echo_foreign_origin(self) -> None:
        client = TestClient(gateway.app)
        evil = {"Origin": "https://evil.example"}
        response = client.get("/api/health", headers=evil)
        self.assertEqual(200, response.status_code)
        self.assertNotIn("access-control-allow-origin", response.headers)
        preflight = client.options("/api/tools", headers={**evil, "Access-Control-Request-Method": "POST"})
        self.assertNotIn("access-control-allow-origin", preflight.headers)
        for origin in ("http://127.0.0.1:8765", "http://localhost:8765", "http://[::1]:8765"):
            response = client.get("/api/health", headers={"Origin": origin})
            self.assertEqual(origin, response.headers.get("access-control-allow-origin"), origin)
            self.assertNotIn("access-control-allow-credentials", response.headers)
        response = client.get("/api/health", headers={"Origin": "http://127.0.0.1.evil.example"})
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_gateway_remote_without_token_is_refused(self) -> None:
        remote = TestClient(gateway.app, **REMOTE)
        for path in ("/api/artifact?path=README.md", "/api/tools", "/docs", "/openapi.json"):
            response = remote.get(path)
            self.assertEqual(403, response.status_code, (path, response.text))
            self.assertIn("只接受本机访问", self.detail(response))
        response = remote.post("/api/tms/booking/submit", json={"state": {"x": 1}, "mode": "http"})
        self.assertEqual(403, response.status_code, response.text)
        self.assertEqual(200, remote.get("/api/health").status_code)
        self.assertEqual(200, remote.get("/").status_code)

    def test_gateway_token_required_from_everyone_when_set(self) -> None:
        os.environ["CIVIL_TOKEN"] = TOKEN
        local, remote = TestClient(gateway.app), TestClient(gateway.app, **REMOTE)
        for client in (local, remote):
            response = client.get("/api/tools")
            self.assertEqual(401, response.status_code, response.text)
            self.assertIn("CIVIL_TOKEN", self.detail(response))
            self.assertEqual(401, client.get("/api/tools", headers={"Authorization": "Bearer wrong"}).status_code)
            self.assertEqual(200, client.get("/api/tools", headers=BEARER).status_code)
            self.assertEqual(200, client.get("/api/tools", headers={"Authorization": "bearer " + TOKEN}).status_code)
            self.assertEqual(200, client.get("/api/health").status_code)

    def test_browser_page_without_token_gets_an_html_401_and_no_app(self) -> None:
        # A browser opening a gated page (/demo) or a bad ?token= link gets a short page saying how to get access,
        # still 401 and with nothing of the app; API and socket paths keep their JSON / close codes.
        os.environ["CIVIL_TOKEN"] = TOKEN
        html = {"Accept": "text/html,application/xhtml+xml"}
        for client in (TestClient(gateway.app), TestClient(gateway.app, **REMOTE)):
            page = client.get("/demo", headers=html)
            self.assertEqual(401, page.status_code)
            self.assertTrue(page.headers["content-type"].startswith("text/html"))
            self.assertIn("Access token required", page.text)
            self.assertNotIn("/api/tender/link", page.text)
            api = client.get("/api/tools", headers=html)
            self.assertEqual(401, api.status_code)
            self.assertIn("CIVIL_TOKEN", self.detail(api))
            self.assertEqual(401, client.post("/demo", headers=html).status_code)
            bad = TestClient(gateway.app, follow_redirects=False).get("/", params={"token": "wrong"}, headers=html)
            self.assertEqual(401, bad.status_code)
            self.assertNotIn("set-cookie", bad.headers)
            self.assertIn("Access token required", bad.text)
            self.assertEqual(200, client.get("/demo", headers={**html, **BEARER}).status_code)

    def test_gateway_websocket_refused_for_remote(self) -> None:
        real = HUB.subscribe

        def quick(key):  # ws_session blocks 15 s per get; keep the worker thread short-lived
            q = real(key)
            get = q.get
            q.get = lambda block=True, timeout=None: get(block, 0.05)
            return q

        self.stack.enter_context(patch.object(HUB, "subscribe", quick))

        def refused(client, **kwargs):
            with self.assertRaises(WebSocketDisconnect) as caught:
                with client.websocket_connect("/ws/session/access-guard", **kwargs):
                    pass
            self.assertEqual(1008, caught.exception.code)

        def subscribed(client, **kwargs):
            with client.websocket_connect("/ws/session/access-guard", **kwargs) as ws:
                self.assertEqual("ws_subscribed", ws.receive_json()["type"])

        refused(TestClient(gateway.app, **REMOTE))
        subscribed(TestClient(gateway.app))
        os.environ["CIVIL_TOKEN"] = TOKEN
        refused(TestClient(gateway.app))
        subscribed(TestClient(gateway.app, **REMOTE), headers=BEARER)

    def test_tms_body_cannot_switch_to_live(self) -> None:
        client = TestClient(gateway.app)
        live = self.stack.enter_context(patch.object(tms_booking, "_http_submit", return_value={"ok": True, "mode": "http"}))
        stub = self.stack.enter_context(patch.object(tms_booking, "_stub_submit", return_value={"ok": True, "mode": "stub"}))
        for mode in ("http", "HTTP", "remote", None):
            response = client.post("/api/tms/booking/submit", json={"state": {"session_id": ""}, "mode": mode})
            self.assertEqual(200, response.status_code, response.text)
        live.assert_not_called()
        self.assertEqual(4, stub.call_count)
        # The server's own setting still decides; the body can only ask for the stub.
        os.environ["PACKING_TMS_MODE"] = "http"
        client.post("/api/tms/booking/submit", json={"state": {"session_id": ""}, "mode": "stub"})
        live.assert_not_called()
        client.post("/api/tms/booking/submit", json={"state": {"session_id": ""}})
        live.assert_called_once()

    def test_artifact_confined_to_output_and_runs(self) -> None:
        client = TestClient(gateway.app)

        def status(path) -> int:
            return client.get("/api/artifact", params={"path": str(path)}).status_code

        for path in (ROOT / "README.md", "README.md", ROOT / "requirements.txt", "output/../README.md"):
            self.assertEqual(403, status(path), path)
        (ROOT / "output").mkdir(exist_ok=True)
        out = Path(tempfile.mkdtemp(prefix="test-access-guard-", dir=ROOT / "output"))
        self.addCleanup(shutil.rmtree, out, True)
        (out / "note.md").write_text("# 草稿\n", encoding="utf-8")
        response = client.get("/api/artifact", params={"path": str(out / "note.md")})
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("# 草稿\n", response.json()["text"])
        self.assertEqual(403, status(out / ".." / ".." / "README.md"))
        # A relative path is the repo's output/, whatever the server's working directory.
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(self.tmp)
        self.assertEqual(200, status(f"output/{out.name}/note.md"))
        os.chdir(cwd)
        # Pipeline artifact_paths live in the runs dir, which PACKING_TRACE_DIR can move.
        runs = self.tmp / "runs"
        (runs / "r1").mkdir(parents=True)
        (runs / "r1" / "report.md").write_text("ok", encoding="utf-8")
        self.assertEqual(403, status(runs / "r1" / "report.md"))
        with patch.object(run_artifacts, "RUNS_DIR", runs):
            self.assertEqual(200, status(runs / "r1" / "report.md"))
        link = out / "escape.md"
        try:
            link.symlink_to(ROOT / "README.md")
        except (OSError, NotImplementedError):
            return  # Windows without the symlink privilege; CI Linux runs this
        self.assertEqual(403, status(link))


class ForwardingTests(Case):
    def test_forwarded_or_rebound_requests_are_not_local(self) -> None:
        client = TestClient(workbench.app)
        self.assertEqual(200, client.get("/api/catalog").status_code)
        for headers in ({"X-Forwarded-For": "203.0.113.9"}, {"Forwarded": "for=203.0.113.9"}, {"X-Real-IP": "203.0.113.9"},
                        {"Via": "1.1 proxy"}, {"X-Forwarded-Proto": "https"}, {"X-Forwarded-Host": "civil.example.com"},
                        {"X-Forwarded-Port": "443"}, {"Host": "evil.example"}, {"Referer": "https://civil.example.com/"},
                        {"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
            response = client.get("/api/catalog", headers=headers)
            self.assertEqual(403, response.status_code, headers)
            self.assertIn("CIVIL_TOKEN", self.detail(response))
        for headers in ({"Origin": "http://127.0.0.1:8765"}, {"Referer": "http://testserver/"}, {"Origin": "null"},
                        {"Sec-Fetch-Site": "same-origin"}):
            self.assertEqual(200, client.get("/api/catalog", headers=headers).status_code, headers)

    def test_http10_upstream_and_testclient_exception_are_not_local(self) -> None:
        is_local = guard().is_local
        self.assertTrue(is_local(scope("127.0.0.1", "127.0.0.1:8000")))
        self.assertTrue(is_local(scope("::1", "[::1]:8000")))
        self.assertTrue(is_local(scope("::ffff:127.0.0.1", "localhost:8000")))
        self.assertFalse(is_local(scope("127.0.0.1", "127.0.0.1:8000", "1.0")))  # bare nginx proxy_pass
        self.assertFalse(is_local(scope("127.0.0.1", "civil.example.com")))  # proxy_set_header Host $host
        self.assertFalse(is_local(scope("192.0.2.1", "127.0.0.1:8000")))
        self.assertFalse(is_local(scope(None, "127.0.0.1:8000")))
        self.assertTrue(is_local(scope("testclient", "testserver")))
        self.assertFalse(is_local(scope("testclient", "remote.invalid")))
        self.assertFalse(is_local(scope("testclient", "testserver", x_forwarded_for="127.0.0.1")))

    def test_real_uvicorn_proxy_shapes_are_not_local(self) -> None:
        import httpx
        import uvicorn
        from fastapi import FastAPI

        app = FastAPI()
        app.get("/api/x")(lambda: {"x": 1})
        app.add_middleware(guard().AccessGuard, public=lambda path: False)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 5)
        self.addCleanup(setattr, server, "should_exit", True)
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        base = f"http://127.0.0.1:{port}/api/x"
        with httpx.Client(trust_env=False, timeout=5) as http:
            self.assertEqual(200, http.get(base).status_code)
            self.assertEqual(200, http.get(f"http://localhost:{port}/api/x").status_code)
            # uvicorn trusts X-Forwarded-For from 127.0.0.1 and rewrites the client; the header still marks a proxy.
            for headers in ({"X-Forwarded-For": "203.0.113.9"}, {"X-Forwarded-Proto": "https"}, {"Host": "civil.example.com"}):
                self.assertEqual(403, http.get(base, headers=headers).status_code, headers)
        with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
            raw.sendall(f"GET /api/x HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
            data = b""
            while chunk := raw.recv(4096):
                data += chunk
        self.assertEqual(b"403", data.split(b"\r\n", 1)[0].split()[1])


class TokenTests(Case):
    def test_url_token_sets_httponly_cookie_once(self) -> None:
        os.environ["CIVIL_TOKEN"] = TOKEN
        client = TestClient(workbench.app, follow_redirects=False)
        response = client.get("/?token=" + TOKEN + "&a=1")
        self.assertEqual(303, response.status_code, response.text)
        self.assertEqual("/?a=1", response.headers["location"])
        cookie = response.headers["set-cookie"]
        for part in ("cb_token=" + TOKEN, "HttpOnly", "SameSite=Strict", "Path=/"):
            self.assertIn(part, cookie)
        self.assertNotIn("Secure", cookie)
        self.assertEqual("no-referrer", response.headers["referrer-policy"])
        self.assertEqual("no-store", response.headers["cache-control"])
        self.assertEqual(200, client.get("/api/catalog").status_code)  # the cookie, not the URL, authorises now

        fresh = TestClient(workbench.app, follow_redirects=False)
        response = fresh.get("/api/catalog", params={"token": TOKEN})
        self.assertEqual(303, response.status_code)
        self.assertEqual("/api/catalog", response.headers["location"])
        self.assertEqual(401, TestClient(workbench.app).post("/api/task-route", params={"token": TOKEN},
                                                             json={"message": "你好"}).status_code)
        wrong = TestClient(workbench.app, follow_redirects=False).get("/", params={"token": "wrong"})
        self.assertEqual(401, wrong.status_code)
        self.assertNotIn("set-cookie", wrong.headers)
        secure = TestClient(workbench.app, follow_redirects=False).get("/", params={"token": TOKEN},
                                                                       headers={"X-Forwarded-Proto": "https"})
        self.assertIn("Secure", secure.headers["set-cookie"])
        # The redirect stays on this host even for a path that looks like //host.
        bounced = TestClient(workbench.app, follow_redirects=False).get("http://testserver//evil.example/?token=" + TOKEN)
        self.assertEqual(303, bounced.status_code)
        self.assertFalse(bounced.headers["location"].startswith("//"), bounced.headers["location"])

    def test_stale_cookie_is_cleared(self) -> None:
        os.environ["CIVIL_TOKEN"] = TOKEN
        client = TestClient(workbench.app)
        client.cookies.set("cb_token", "rotated-away")
        response = client.get("/api/catalog")
        self.assertEqual(401, response.status_code)
        self.assertIn("cb_token=;", response.headers["set-cookie"])
        self.assertIn("Max-Age=0", response.headers["set-cookie"])
        self.assertNotIn("set-cookie", TestClient(workbench.app).get("/api/catalog").headers)

    def test_token_compare_is_constant_time(self) -> None:
        access_guard = guard()
        os.environ["CIVIL_TOKEN"] = TOKEN
        calls = []
        real = hmac.compare_digest

        def spy(a, b):
            calls.append((a, b))
            return real(a, b)

        with patch.object(access_guard.hmac, "compare_digest", spy):
            self.assertTrue(access_guard.authorised(scope("192.0.2.1", "civil.example.com", authorization="Bearer " + TOKEN)))
        self.assertEqual([(TOKEN.encode(), TOKEN.encode())], calls)
        self.assertTrue(all(type(value) is bytes for value in calls[0]))

    def test_health_hides_job_root_and_reports_auth_per_request(self) -> None:
        job = self.tmp / "job"
        job.mkdir()
        os.environ.update(CIVIL_TOKEN=TOKEN, CIVIL_JOB_ROOT=str(job))
        local, remote = TestClient(workbench.app), TestClient(workbench.app, **REMOTE)
        anonymous = local.get("/api/health").json()
        self.assertEqual("", anonymous["job"]["root"])
        self.assertTrue(anonymous["capabilities"]["auth"])
        signed_in = remote.get("/api/health", headers=BEARER).json()
        self.assertEqual(str(job), signed_in["job"]["root"])
        self.assertFalse(signed_in["capabilities"]["auth"])
        del os.environ["CIVIL_TOKEN"]
        self.assertEqual(str(job), local.get("/api/health").json()["job"]["root"])
        self.assertEqual("", remote.get("/api/health").json()["job"]["root"])

    def test_open_lan_opt_out(self) -> None:
        remote = TestClient(workbench.app, **REMOTE)
        response = remote.get("/api/catalog")
        self.assertEqual(403, response.status_code)
        self.assertIn("只接受本机访问", self.detail(response))
        os.environ["CIVIL_ALLOW_OPEN_LAN"] = "1"
        self.assertEqual(200, remote.get("/api/catalog").status_code)
        os.environ["CIVIL_TOKEN"] = TOKEN  # the opt-out never weakens a configured token
        self.assertEqual(401, remote.get("/api/catalog").status_code)


class StartupTests(Case):
    def test_uvicorn_cli_refuses_open_bind_without_token(self) -> None:
        apps = (("gateway", gateway.app), ("workbench", workbench.app))
        with patch.object(sys, "argv", ["/venv/bin/uvicorn", "x:app", "--host", "0.0.0.0", "--port", "8000"]):
            for name, app in apps:
                with self.subTest(app=name), self.assertRaises(RuntimeError) as caught:
                    with TestClient(app):
                        pass
                self.assertIn("拒绝启动", str(caught.exception))
                self.assertIn("CIVIL_TOKEN", str(caught.exception))
            os.environ["CIVIL_TOKEN"] = TOKEN
            for name, app in apps:
                with self.subTest(app=name, token=True), TestClient(app) as client:
                    self.assertEqual(200, client.get("/api/health").status_code)
        del os.environ["CIVIL_TOKEN"]
        with patch.object(sys, "argv", ["/venv/bin/uvicorn", "x:app", "--host", "127.0.0.1"]):
            for name, app in apps:
                with self.subTest(app=name, loopback=True), TestClient(app) as client:
                    self.assertEqual(200, client.get("/api/health").status_code)

    def test_serve_refuses_open_bind(self) -> None:
        import serve

        os.environ["CIVIL_HOST"] = "0.0.0.0"
        with patch("uvicorn.run") as run:
            with self.assertRaises(SystemExit) as caught:
                serve.main()
            self.assertIn("CIVIL_TOKEN", str(caught.exception.code))
            run.assert_not_called()
            for env in ({"CIVIL_TOKEN": TOKEN}, {"CIVIL_ALLOW_OPEN_LAN": "1"}):
                with patch.dict(os.environ, env):
                    serve.main()
            self.assertEqual(2, run.call_count)
            self.assertEqual("0.0.0.0", run.call_args.kwargs["host"])
        os.environ["CIVIL_HOST"] = "127.0.0.1"
        with patch("uvicorn.run") as run:
            serve.main()
            run.assert_called_once()

    def test_uvicorn_cli_host_parsing(self) -> None:
        host = guard().uvicorn_cli_host
        self.assertEqual("0.0.0.0", host(["/venv/bin/uvicorn", "gateway.app:app", "--host", "0.0.0.0"], {}))
        self.assertEqual("::", host(["uvicorn", "a:b", "--host=::"], {}))
        self.assertEqual("0.0.0.0", host(["uvicorn", "a:b"], {"UVICORN_HOST": "0.0.0.0"}))
        self.assertEqual("127.0.0.1", host(["/py/lib/site-packages/uvicorn/__main__.py", "a:b"], {}))  # python -m uvicorn
        self.assertEqual("0.0.0.0", host(["C:/py/Scripts/uvicorn.exe", "a:b", "--host", "0.0.0.0"], {}))
        self.assertIsNone(host(["pytest", "--host", "0.0.0.0"], {}))
        self.assertIsNone(host(["/opt/uvicorn-apps/run.py", "--host", "0.0.0.0"], {}))
        refusal = guard().open_bind_refusal
        self.assertIsNone(refusal("127.0.0.1"))
        self.assertIsNone(refusal("[::1]"))
        self.assertIsNone(refusal("localhost"))
        self.assertIn("CIVIL_TOKEN", refusal("0.0.0.0"))
        self.assertIn("CIVIL_TOKEN", refusal("192.168.1.20"))


class ModelSettingsTests(Case):
    def test_model_base_url_change_requires_key(self) -> None:
        from model_settings import set_settings

        for key in ("CIVIL_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY", "DEEPSEEK_API_KEY",
                    "CIVIL_API_BASE", "OPENAI_BASE_URL", "LLM_BASE_URL", "DEEPSEEK_BASE_URL"):
            os.environ.pop(key, None)
        self.stack.enter_context(patch.object(shared_llm, "_RUNTIME_LLM", None))
        set_settings({"api_key": "stored-fixture-key", "base_url": "https://api.example.test/v1", "model": "m1"})
        before = shared_llm.llm_config()
        for moved in ("https://attacker.example/v1", "https://api.example.test/other", "http://api.example.test/v1"):
            with self.subTest(base=moved), self.assertRaises(ValueError) as caught:
                set_settings({"base_url": moved, "model": "m2"})
            self.assertIn("API Key", str(caught.exception))
            self.assertEqual(before, shared_llm.llm_config())
        response = TestClient(workbench.app).post("/api/llm-config", json={"base_url": "https://attacker.example/v1"})
        self.assertEqual(400, response.status_code, response.text)
        self.assertEqual(before, shared_llm.llm_config())
        # What the settings page re-posts on every save is the same place, so the stored key stays.
        for same in ("https://api.example.test/v1/", "HTTPS://API.EXAMPLE.TEST:443/v1"):
            set_settings({"base_url": same, "model": "m3"})
            self.assertEqual("stored-fixture-key", shared_llm.llm_config()["api_key"])
        set_settings({"base_url": "https://other.example/v1", "api_key": "new-fixture-key"})
        self.assertEqual(("https://other.example/v1", "new-fixture-key"),
                         (shared_llm.llm_config()["base_url"], shared_llm.llm_config()["api_key"]))


if __name__ == "__main__":
    unittest.main()
