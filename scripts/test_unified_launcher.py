"""Offline launcher credential boundary and domain persistence regressions."""
from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import io
import os
import socket
import subprocess
import time
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import start_unified_workbench as launcher
from scripts import test_unified_runtime_http as runtime_http
from scripts.test_unified_runtime_http import wait_listener_closed


class FakeProcess:
    def __init__(self):
        self.terminated = False
        self.waited = False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True

    def wait(self, timeout=None):
        self.waited = True
        return 0


class LauncherTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="civil-unified-launcher-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def test_owned_workspace_and_state_overlap_rejected_in_both_directions(self):
        owner = self.root / "alice-job" / ".civil-buddy" / "instance-owner.sqlite"
        owner.parent.mkdir(parents=True)
        owner.touch()
        with self.assertRaises(ValueError):
            launcher.validate_owned_directory(self.root / "alice-job" / "bob-state", state=True)
        with self.assertRaises(ValueError):
            launcher.validate_owned_directory(self.root, state=True)
        private = self.root / "private" / "alice-state"
        private.mkdir(parents=True)
        (private / "identity.sqlite").write_bytes(b"damaged state still must stay private")
        for candidate in (private, private / "data", private.parent):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                launcher.validate_owned_directory(candidate, state=False)
        launcher.validate_owned_directory(private.parent / "bob-state", state=True)

    def test_ownership_scan_refuses_unreadable_directories(self):
        with patch.object(launcher.os, "scandir", side_effect=PermissionError("synthetic denied")), self.assertRaises(PermissionError):
            launcher.validate_owned_directory(self.root, state=False)

    def test_named_launcher_rejects_foreign_workspace_state_before_spawning_helpers(self):
        binary = self.root / "host.exe"
        binary.touch()
        workspace = self.root / "bob-job"
        workspace.mkdir()
        marker = self.root / "alice-job/.civil-buddy/instance-owner.sqlite"
        marker.parent.mkdir(parents=True)
        marker.touch()
        token = self.root / "token.txt"
        token.write_text("x" * 40, encoding="utf-8")
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(sys, "argv", ["launcher", "--binary", str(binary), "--workspace", str(workspace),
                    "--state-root", str(self.root / "alice-job/state"), "--user-id", "bob", "--token-file", str(token)]), \
             patch.object(launcher.subprocess, "Popen") as start, contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            launcher.main()
        start.assert_not_called()

    def test_domain_process_has_no_provider_credentials_and_uses_state_directory(self):
        binary = self.root / "civil-workbench.exe"
        binary.write_bytes(b"test executable marker; never executed")
        state = self.root / "isolated-state"
        spawned = []

        def popen(argv, **kwargs):
            process = FakeProcess()
            spawned.append((argv, kwargs, process))
            return process

        inherited = {"PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
                     "DEEPSEEK_API_KEY": "fake-parent-key", "OPENAI_API_KEY": "fake-openai-key",
                     "ANTHROPIC_API_KEY": "fake-other-key", "JEV_API_KEY": "fake-jev-key",
                     "CIVIL_TEST_PRIVATE_SECRET": "fake-unknown-secret",
                     "CB_ASR_MODEL": "small", "HF_HOME": str(self.root / "ambient models"),
                     "HUGGINGFACE_HUB_CACHE": str(self.root / "ambient hub"),
                     "XDG_CACHE_HOME": str(self.root / "ambient cache"),
                     "PYTHONPATH": "untrusted-import-path", "CIVIL_DOMAIN_WORKSPACE": "old-ambient-folder"}
        config = {"DEEPSEEK_API_KEY": "fake-config-key", "JEV_API_KEY": "fake-config-jev",
                  "CB_ASR_MODEL": "tiny", "HF_HOME": str(self.root / "selected models"),
                  "HUGGINGFACE_HUB_CACHE": str(self.root / "selected hub"),
                  "XDG_CACHE_HOME": str(self.root / "selected cache"),
                  "HF_TOKEN": "fake-config-hf-token", "PYTHONPATH": "config-untrusted-import-path"}
        output = io.StringIO()
        selected_python = str(self.root / "environment with spaces" / "python.exe")
        with patch.dict(os.environ, inherited, clear=True), \
             patch.object(launcher.preflight, "run", return_value=launcher.preflight.Report(python=selected_python, version="3.11.0")), \
             patch.object(launcher.preflight, "load_environment_file", return_value=config) as load_config, \
             patch.object(launcher, "ROOT", self.root), \
             patch.object(sys, "argv", ["launcher", "--binary", str(binary), "--state-root", str(state), "--env-file", str(self.root / "selected.env")]), \
             patch.object(launcher.subprocess, "Popen", side_effect=popen), \
             patch.object(launcher, "ProcessFamily"), \
             patch.object(launcher.urllib.request, "urlopen", side_effect=lambda *_a, **_kw: contextlib.nullcontext(SimpleNamespace(status=200))), \
             patch.object(launcher.time, "sleep", side_effect=KeyboardInterrupt), \
             contextlib.redirect_stdout(output):
            launcher.main()
        self.assertEqual(len(spawned), 2)
        argv, options, domain = spawned[0]
        self.assertIn("demo.domain_service:app", argv)
        self.assertEqual(argv[0], selected_python)
        env = options["env"]
        self.assertFalse(any(("KEY" in key.upper() or "TOKEN" in key.upper() or "SECRET" in key.upper()) and key != "CIVIL_DOMAIN_TOKEN" for key in env), sorted(env))
        self.assertGreaterEqual(len(env["CIVIL_DOMAIN_TOKEN"]), 32)
        self.assertNotIn("PYTHONPATH", env)
        for key in ("CB_ASR_MODEL", "HF_HOME", "HUGGINGFACE_HUB_CACHE", "XDG_CACHE_HOME"):
            self.assertEqual(env[key], config[key])
        self.assertEqual(env["PYTHON_DOTENV_DISABLED"], "1")
        self.assertEqual(Path(env["CIVIL_DOMAIN_WORKSPACE"]), state / "domains")
        self.assertEqual(Path(env["CIVIL_OUT_ROOT"]), state / "domains")
        self.assertEqual(Path(env["CIVIL_SANDBOX_ROOTS"]), state / "domains")
        self.assertEqual(Path(env["PACKING_OUTPUT_DIR"]), state / "packing")
        self.assertEqual(Path(env["PACKING_TRACE_DIR"]), state / "packing" / "traces")
        self.assertEqual(Path(env["CB_DB_PATH"]), state / "packing" / "civilbuddy.db")
        self.assertEqual(Path(env["PACKING_LG_CHECKPOINT_PATH"]), state / "packing" / "checkpoints.db")
        self.assertEqual(env["PACKING_LLM_AGENT"], "0")
        host_env = spawned[1][1]["env"]
        self.assertEqual(host_env["CIVIL_PYTHON"], selected_python)
        self.assertEqual(load_config.call_args.args[0], selected_python)
        self.assertEqual(host_env["DEEPSEEK_API_KEY"], "fake-config-key")
        self.assertEqual(host_env["JEV_API_KEY"], "fake-config-jev")
        self.assertEqual(Path(host_env["CIVIL_STATE_ROOT"]), state)
        self.assertTrue(host_env["CIVIL_DOMAIN_URL"].startswith("http://127.0.0.1:"))
        self.assertEqual(env["CIVIL_DOMAIN_TOKEN"], host_env["CIVIL_DOMAIN_TOKEN"])
        self.assertEqual(host_env["PYTHON_DOTENV_DISABLED"], "1")
        self.assertEqual(host_env["PACKING_AGENT_URL"], host_env["CIVIL_DOMAIN_URL"] + "/packing")
        self.assertTrue(all(process.terminated and process.waited for _, _, process in spawned))
        self.assertNotIn("fake-config-key", output.getvalue())
        self.assertNotIn("fake-parent-key", output.getvalue())

    def test_ambient_asr_preferences_reach_sidecar_without_loading_env_file(self):
        binary = self.root / "host.exe"
        binary.touch()
        preferences = {"CB_ASR_MODEL": "small", "HF_HOME": str(self.root / "models with spaces"),
                       "HUGGINGFACE_HUB_CACHE": str(self.root / "hub with spaces"),
                       "XDG_CACHE_HOME": str(self.root / "cache with spaces")}
        spawned = []
        def popen(argv, **kwargs):
            spawned.append(kwargs["env"])
            return FakeProcess()
        inherited = {**preferences, "HF_TOKEN": "fake-hub-token", "OPENAI_API_KEY": "fake-provider-key",
                     "PYTHONPATH": "untrusted-import-path"}
        with patch.dict(os.environ, inherited, clear=True), \
             patch.object(launcher.preflight, "run", return_value=launcher.preflight.Report(python=sys.executable, version="3.11.0")), \
             patch.object(launcher.preflight, "load_environment_file") as load_config, \
             patch.object(launcher, "ROOT", self.root), \
             patch.object(launcher.subprocess, "Popen", side_effect=popen), \
             patch.object(launcher, "ProcessFamily"), \
             patch.object(launcher.urllib.request, "urlopen", side_effect=lambda *_a, **_kw: contextlib.nullcontext(SimpleNamespace(status=200))), \
             patch.object(launcher.time, "sleep", side_effect=KeyboardInterrupt), contextlib.redirect_stdout(io.StringIO()):
            launcher.main(["--binary", str(binary), "--state-root", str(self.root / "state")])
        load_config.assert_not_called()
        self.assertEqual(len(spawned), 2)
        self.assertEqual({key: spawned[0][key] for key in preferences}, preferences)
        for key in ("HF_TOKEN", "OPENAI_API_KEY", "PYTHONPATH"):
            self.assertNotIn(key, spawned[0])

    def test_named_launch_separates_workspace_state_and_never_passes_login_token_to_domain(self):
        binary = self.root / "host.exe"
        binary.touch()
        workspace = self.root / "private-job"
        workspace.mkdir()
        token_file = self.root / "login-token.txt"
        login_token = "test-login-token-" + "x" * 40
        token_file.write_text(login_token, encoding="utf-8")
        spawned, probes = [], []
        def popen(argv, **kwargs):
            process = FakeProcess()
            spawned.append((kwargs["env"], process))
            return process
        def probe(request, **kwargs):
            probes.append(request)
            return contextlib.nullcontext(SimpleNamespace(status=200))
        with patch.dict(os.environ, {}, clear=True), patch.object(launcher, "ROOT", self.root), \
             patch.object(launcher.preflight, "run", return_value=launcher.preflight.Report(python=sys.executable, version="3.11.0")), \
             patch.object(sys, "argv", ["launcher", "--binary", str(binary), "--state-root", str(self.root / "state"),
                    "--user-id", "colleague-a", "--workspace", str(workspace), "--token-file", str(token_file)]), \
             patch.object(launcher.subprocess, "Popen", side_effect=popen), \
             patch.object(launcher, "ProcessFamily"), \
             patch.object(launcher.urllib.request, "urlopen", side_effect=probe), \
             patch.object(launcher.time, "sleep", side_effect=KeyboardInterrupt), contextlib.redirect_stdout(io.StringIO()):
            launcher.main()
        domain, host = spawned[0][0], spawned[1][0]
        self.assertEqual(host["CIVIL_INSTANCE_USER"], "colleague-a")
        self.assertEqual(json.loads(host["CIVIL_ALLOWED_WORKSPACES"]), [str(workspace)])
        self.assertEqual(host["CIVIL_JOB_ROOT"], str(workspace))
        self.assertEqual(host["CIVIL_TOKEN_SHA256"], hashlib.sha256(login_token.encode()).hexdigest())
        self.assertNotIn(login_token, domain.values())
        self.assertNotIn(login_token, host.values())
        self.assertIn("accounts", Path(host["CIVIL_STATE_ROOT"]).parts)
        self.assertIn("colleague-a", Path(host["CIVIL_STATE_ROOT"]).parts)
        self.assertEqual(probes[0].get_header("Authorization"), "Bearer " + login_token)
        self.assertEqual(probes[1].get_header("Authorization"), "Bearer " + domain["CIVIL_DOMAIN_TOKEN"])

    def _apis(self):
        with patch.dict(os.environ, {"PYTHON_DOTENV_DISABLED": "1"}):
            from demo import config, engineering_api, planning_api
        return config, engineering_api, planning_api

    def test_shutdown_closes_helpers_and_descendants(self):
        # Wait for assignment before spawning the grandchild, then check its live socket.
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
        marker = self.root / "grandchild-ready"
        grandchild = ("import socket,time,pathlib; s=socket.socket(); "
                      f"s.bind(('127.0.0.1',{port})); s.listen(); "
                      f"pathlib.Path({str(marker)!r}).write_text('ready'); time.sleep(90)")
        parent = ("import subprocess,sys,time; sys.stdin.readline(); "
                  f"subprocess.Popen([sys.executable,'-c',{grandchild!r}], "
                  "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); time.sleep(90)")
        family = launcher.ProcessFamily()
        process = subprocess.Popen([sys.executable, "-c", parent], stdin=subprocess.PIPE,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                                   start_new_session=os.name != "nt")
        try:
            family.add(process)
            process.stdin.write(b"start\n")
            process.stdin.close()
            deadline = time.monotonic() + 20
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(marker.exists(), "descendant did not start")
            with socket.socket() as probe:
                self.assertEqual(probe.connect_ex(("127.0.0.1", port)), 0)
            family.close([process])
            process.wait(timeout=10)
            self.assertTrue(wait_listener_closed(port), "descendant listener survived process-family shutdown")
        finally:
            family.close([process])
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)

    def test_unified_schedule_and_planning_records_stay_in_state_domain_workspace(self):
        config, engineering, planning = self._apis()
        from packing_assistant.engineering.planning import calculate
        legacy = self.root / "legacy-repo"
        domain = self.root / "state" / "domains"
        domain.mkdir(parents=True)
        env = {"CIVIL_DOMAIN_WORKSPACE": str(domain), "CIVIL_SANDBOX_ROOTS": str(domain), "CIVIL_SANDBOX": "workspace-write"}
        with patch.dict(os.environ, env), patch.object(config, "REPO_ROOT", legacy):
            schedule = engineering.schedule_store()
            plan_store = planning.store()
            self.assertEqual(schedule.workspace, domain)
            self.assertEqual(plan_store.workspace, domain)
            task = {"id": "A", "name": "Fixture task", "start": "2026-09-21", "end": "2026-09-21", "progress": 0, "dependencies": []}
            saved_schedule = schedule.save(name="Fixture schedule", tasks=[task])
            calculated = calculate(planning.example())
            saved_plan = plan_store.save(name="Fixture plan", plan=calculated["plan"], result=calculated["result"])
            for store, saved in [(schedule, saved_schedule), (plan_store, saved_plan)]:
                path = store.root / (saved["id"] + ".json")
                self.assertTrue(path.is_file())
                path.relative_to(domain)
                self.assertEqual(store.open(saved["id"])["revision"], 1)
        self.assertFalse(legacy.exists(), "unified persistence created a legacy repository directory")

    def test_legacy_without_domain_environment_retains_repository_store_locations(self):
        config, engineering, planning = self._apis()
        legacy = self.root / "legacy-repo"
        with patch.dict(os.environ), patch.object(config, "REPO_ROOT", legacy):
            os.environ.pop("CIVIL_DOMAIN_WORKSPACE", None)
            schedule = engineering.schedule_store()
            plan_store = planning.store()
            self.assertEqual(schedule.workspace, legacy)
            self.assertEqual(plan_store.workspace, legacy)
            self.assertEqual(schedule.root, legacy / ".civil-buddy/out/engineering/schedules")
            self.assertEqual(plan_store.root, legacy / ".civil-buddy/out/engineering/plans")


class ListenerShutdownTests(unittest.TestCase):
    def listener(self, *, delayed_close=False):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(0.05)
        port = listener.getsockname()[1]
        stop = threading.Event()
        accepted = threading.Event()

        def serve():
            try:
                while not stop.is_set():
                    try:
                        connection, _ = listener.accept()
                    except socket.timeout:
                        continue
                    with connection:
                        accepted.set()
                    if delayed_close:
                        # Only start the delay after the helper has observed a live listener. This prevents
                        # scheduler timing from turning the test into a trivial already-closed-port check.
                        stop.wait(0.15)
                        return
            finally:
                listener.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()

        def cleanup():
            stop.set()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive(), "synthetic listener did not stop")

        self.addCleanup(cleanup)
        return port, accepted

    def test_listener_closing_after_first_probe_is_awaited(self):
        port, accepted = self.listener(delayed_close=True)
        self.assertTrue(wait_listener_closed(port, timeout=4))
        self.assertTrue(accepted.is_set(), "the first probe must see a live listener")

    def test_listener_surviving_deadline_is_still_a_failure(self):
        port, accepted = self.listener()
        started = time.monotonic()
        self.assertFalse(wait_listener_closed(port, timeout=0.2))
        self.assertTrue(accepted.is_set())
        self.assertLess(time.monotonic() - started, 1.5, "listener wait exceeded its bounded deadline")

    def test_connect_timeout_does_not_count_as_a_closed_listener(self):
        with patch.object(runtime_http.socket, "socket") as create:
            create.return_value.__enter__.return_value.connect_ex.return_value = errno.ETIMEDOUT
            self.assertFalse(runtime_http.port_closed(1))


if __name__ == "__main__":
    unittest.main()
