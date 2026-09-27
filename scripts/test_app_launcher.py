#!/usr/bin/env python3
"""Offline app startup checks using real subprocesses and loopback HTTP."""

from __future__ import annotations

from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from packing_assistant.runtime import launcher


FIXTURE = r"""
import json, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer
port, token, mode = int(sys.argv[1]), sys.argv[2], sys.argv[3]
if mode == 'exit':
    print('fixture startup failure', file=sys.stderr, flush=True)
    raise SystemExit(7)
if mode == 'sleep':
    time.sleep(60)
if mode == 'slow':
    time.sleep(1.0)
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        status = 503 if mode == 'unhealthy' else 200
        self.send_response(status)
        self.send_header('X-Civil-Launch-ID', 'unrelated' if mode == 'impostor' else token)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'ok': True, 'product': 'civil-codex', 'port': os.environ.get('CIVIL_PORT')}).encode())
    def log_message(self, *args):
        pass
server = HTTPServer(('127.0.0.1', port), Handler)
if mode in ('finite', 'crash'):
    threading.Timer(1.0, lambda: os._exit(0 if mode == 'finite' else 9)).start()
server.serve_forever()
"""


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((launcher.HOST, 0))
        return sock.getsockname()[1]


def fixture_command(mode: str):
    return lambda port, token: [sys.executable, "-u", "-c", FIXTURE, str(port), token, mode]


class AppLauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.children = []
        self.output = io.StringIO()
        real_popen = subprocess.Popen

        def tracked_popen(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            self.children.append(process)
            return process

        self.redirect = redirect_stderr(self.output)
        self.redirect.__enter__()
        self.popen_patch = patch.object(launcher.subprocess, "Popen", side_effect=tracked_popen)
        self.popen_patch.start()
        self.browser_patch = patch.object(launcher.webbrowser, "open", return_value=True)
        self.browser = self.browser_patch.start()

    def tearDown(self) -> None:
        for process in self.children:
            launcher._stop_process(process)
        self.browser_patch.stop()
        self.popen_patch.stop()
        self.redirect.__exit__(None, None, None)

    def assert_children_stopped(self) -> None:
        self.assertTrue(self.children)
        self.assertTrue(all(process.poll() is not None for process in self.children))

    def test_port_validation_and_cli_precedence(self) -> None:
        self.assertEqual(launcher.resolve_port(None, {}), 8765)
        self.assertEqual(launcher.resolve_port(None, {"CIVIL_PORT": "8123"}), 8123)
        self.assertEqual(launcher.resolve_port(9000, {"CIVIL_PORT": "bad"}), 9000)
        for port in (0, -1, 65536, True):
            with self.subTest(port=port), self.assertRaises(launcher.AppLaunchError):
                launcher.resolve_port(port, {})
        with self.assertRaises(launcher.AppLaunchError):
            launcher.resolve_port(None, {"CIVIL_PORT": "bad"})

    def test_missing_dependency_is_actionable_before_spawning(self) -> None:
        with patch.object(launcher.importlib.util, "find_spec", return_value=None):
            self.assertEqual(launcher.run_workbench(free_port(), no_browser=True), 1)
        self.assertIn("pip install -r requirements.txt", self.output.getvalue())
        self.assertFalse(self.children)
        self.browser.assert_not_called()

    def test_occupied_port_is_not_reused(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
            occupied.bind((launcher.HOST, 0))
            occupied.listen()
            port = occupied.getsockname()[1]
            with self.assertRaisesRegex(launcher.AppLaunchError, "占用"):
                launcher.start_workbench(port)
        self.assertFalse(self.children)
        self.browser.assert_not_called()

    def test_slow_start_waits_for_owned_http_and_preserves_explicit_port(self) -> None:
        port = free_port()
        with patch.object(launcher, "_server_command", side_effect=fixture_command("slow")), \
             patch.dict(os.environ, {"CIVIL_PORT": "1"}):
            with launcher.start_workbench(port, startup_timeout=5) as server:
                opener = build_opener(ProxyHandler({}))
                with opener.open(server.url + "/api/health", timeout=2) as response:
                    self.assertEqual(response.headers[launcher.LAUNCH_HEADER], server.launch_id)
                    self.assertEqual(json.load(response)["port"], str(port))
                self.assertIsNone(server.process.poll())
        self.assert_children_stopped()

    def test_early_exit_is_reported_and_reaped(self) -> None:
        with patch.object(launcher, "_server_command", side_effect=fixture_command("exit")):
            with self.assertRaisesRegex(launcher.AppLaunchError, "退出码 7"):
                launcher.start_workbench(free_port(), startup_timeout=5)
        self.assert_children_stopped()
        self.browser.assert_not_called()
        self.assertIn("fixture startup failure", self.output.getvalue())

    def test_startup_timeout_stops_the_child(self) -> None:
        with patch.object(launcher, "_server_command", side_effect=fixture_command("sleep")):
            with self.assertRaisesRegex(launcher.AppLaunchError, "未就绪"):
                launcher.start_workbench(free_port(), startup_timeout=0.3)
        self.assert_children_stopped()
        self.browser.assert_not_called()

    def test_http_failure_is_not_considered_ready(self) -> None:
        wait_for_health = launcher._wait_for_health

        def wait_after_fixture_responds(server, *, timeout):
            # This case checks rejection of an actual HTTP failure. Process cold
            # startup has a separate budget so a busy machine does not turn it
            # into the connection-timeout case tested above.
            deadline = time.monotonic() + 10
            opener = build_opener(ProxyHandler({}))
            while True:
                self.assertIsNone(server.process.poll(), "HTTP fixture exited before responding")
                self.assertLess(time.monotonic(), deadline, "HTTP fixture never responded")
                try:
                    with opener.open(server.url + "/api/health", timeout=0.5):
                        self.fail("Unhealthy fixture unexpectedly returned a successful status")
                except HTTPError as response:
                    with response:
                        self.assertEqual(response.code, 503)
                        self.assertEqual(response.headers[launcher.LAUNCH_HEADER], server.launch_id)
                        self.assertTrue(json.load(response)["ok"])
                    break
                except (URLError, OSError):
                    time.sleep(0.02)
            return wait_for_health(server, timeout=timeout)

        with patch.object(launcher, "_server_command", side_effect=fixture_command("unhealthy")), \
             patch.object(launcher, "_wait_for_health", side_effect=wait_after_fixture_responds):
            with self.assertRaisesRegex(launcher.AppLaunchError, "HTTP 503"):
                launcher.start_workbench(free_port(), startup_timeout=1.5)
        self.assert_children_stopped()

    def test_unrelated_health_response_is_rejected(self) -> None:
        with patch.object(launcher, "_server_command", side_effect=fixture_command("impostor")):
            with self.assertRaisesRegex(launcher.AppLaunchError, "不属于本次启动"):
                launcher.start_workbench(free_port(), startup_timeout=5)
        self.assert_children_stopped()
        self.browser.assert_not_called()

    def test_cli_no_browser_uses_real_child_until_clean_exit(self) -> None:
        from packing_assistant.civil import main

        with patch.object(launcher, "_server_command", side_effect=fixture_command("finite")):
            self.assertEqual(main(["app", "--port", str(free_port()), "--no-browser"]), 0)
        self.browser.assert_not_called()
        self.assertIn("已就绪", self.output.getvalue())
        self.assert_children_stopped()

    def test_browser_failure_leaves_a_usable_url(self) -> None:
        self.browser.return_value = False
        with patch.object(launcher, "_server_command", side_effect=fixture_command("finite")):
            self.assertEqual(launcher.run_workbench(free_port()), 0)
        self.assertEqual(self.browser.call_count, 1)
        self.assertIn("请手动访问 http://127.0.0.1:", self.output.getvalue())
        self.assert_children_stopped()

    def test_service_exit_after_readiness_is_a_failure(self) -> None:
        with patch.object(launcher, "_server_command", side_effect=fixture_command("crash")):
            self.assertEqual(launcher.run_workbench(free_port(), no_browser=True), 9)
        self.assertIn("服务已退出（退出码 9）", self.output.getvalue())
        self.assert_children_stopped()

    def test_interrupt_closes_the_running_service(self) -> None:
        with patch.object(launcher, "_server_command", side_effect=fixture_command("ready")), \
             patch.object(launcher.WorkbenchServer, "wait", side_effect=KeyboardInterrupt):
            self.assertEqual(launcher.run_workbench(free_port(), no_browser=True), 130)
        self.assert_children_stopped()

    def test_real_workbench_http_health_and_cleanup(self) -> None:
        # Exercise the actual product command, app imports, Uvicorn header, and
        # health contract. Dotenv is disabled and no model requests are made.
        with launcher.start_workbench(free_port(), startup_timeout=20) as server:
            opener = build_opener(ProxyHandler({}))
            with opener.open(server.url + "/api/health", timeout=3) as response:
                health = json.load(response)
                self.assertTrue(health["ok"])
                self.assertEqual(health["product"], "civil-codex")
        self.assert_children_stopped()


if __name__ == "__main__":
    unittest.main()
