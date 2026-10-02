"""Offline prerequisites, interpreter identity and non-mutating check-mode contract."""
from __future__ import annotations

import contextlib
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import start_unified_workbench as launcher
from scripts import workbench_preflight as preflight


class PreflightTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="civil preflight ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).absolute()
        self.binary = self.root / "civil-workbench.exe"
        self.binary.write_bytes(b"not executed")
        self.python = self.root / "environment with spaces" / ("python.exe" if os.name == "nt" else "python")
        self.python.parent.mkdir()
        self.python.touch()

    def data(self):
        return {"version": [3, 11, 7],
                "required": {name: minimum for name, (_, minimum) in preflight.REQUIRED.items()},
                "optional": {name: True for _, modules in preflight.OPTIONAL.values() for name in modules}}

    def probe(self, data=None, **kwargs):
        with patch.object(preflight, "check_port", return_value=None), \
             patch.object(preflight.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=json.dumps(data or self.data()).encode())) as run:
            report = preflight.run(python=str(self.python), binary=self.binary, port=8765, root=self.root, **kwargs)
        return report, run

    def test_absolute_path_with_spaces_and_path_name_resolve_same_interpreter(self):
        self.assertEqual(preflight.resolve_python(str(self.python)), str(self.python))
        with patch.object(preflight.shutil, "which", return_value=str(self.python)) as which:
            self.assertEqual(preflight.resolve_python("python"), str(self.python))
            which.assert_called_once_with("python")
        with patch.object(preflight.shutil, "which", return_value=None), self.assertRaisesRegex(ValueError, "--python"):
            preflight.resolve_python("missing-python")

    def test_probe_selected_interpreter_has_timeout_no_shell_no_credentials(self):
        secrets = {"OPENAI_API_KEY": "must-not-leak-openai", "DEEPSEEK_API_KEY": "must-not-leak-deepseek",
                   "CIVIL_TOKEN": "must-not-leak-token", "CUSTOM_PROVIDER_SECRET": "must-not-leak-custom",
                   "PYTHONPATH": "untrusted-path", "PYTHONSTARTUP": "untrusted-startup"}
        with patch.dict(os.environ, secrets):
            report, invocation = self.probe()
        self.assertTrue(report.ok)
        args, options = invocation.call_args
        self.assertEqual(args[0][0], str(self.python))
        self.assertEqual(args[0][1:3], ["-I", "-B"])
        self.assertEqual(options["timeout"], preflight.PROBE_TIMEOUT)
        self.assertFalse(options.get("shell", False))
        self.assertFalse(set(secrets) & set(options["env"]))
        self.assertEqual(options["env"]["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertFalse(any(value in preflight.format_report(report, self.root) for value in secrets.values()))

    def test_required_missing_or_old_provides_install_command_and_fails(self):
        data = self.data()
        data["required"].update(uvicorn=None, pydantic="1.10.0")
        report, _ = self.probe(data)
        self.assertFalse(report.ok)
        self.assertEqual(report.missing_required, ["pydantic>=2.0.0", "uvicorn>=0.27.0"])
        text = preflight.format_report(report, self.root)
        self.assertIn("& '" + str(self.python) + "' -m pip install", text)
        self.assertIn("requirements-documents.txt", text)

    def test_optional_missing_does_not_block_base_and_has_specific_install_command(self):
        data = self.data()
        data["optional"].update(faster_whisper=False, ezdxf=False)
        report, _ = self.probe(data)
        self.assertTrue(report.ok)
        self.assertEqual(report.missing_optional, {"CAD": ["ezdxf"], "local voice": ["faster_whisper"]})
        text = preflight.format_report(report, self.root)
        self.assertIn("requirements-asr.txt", text)
        self.assertIn("not verified", text)

    def test_probe_timeout_or_invalid_output_is_actionable_without_raw_secrets(self):
        for failure in (subprocess.TimeoutExpired("secret-command", 20, output=b"secret-output"), OSError("secret-error")):
            with self.subTest(failure=type(failure).__name__), patch.object(preflight, "check_port", return_value=None), \
                 patch.object(preflight.subprocess, "run", side_effect=failure):
                report = preflight.run(python=str(self.python), binary=self.binary, port=8765, root=self.root)
            self.assertFalse(report.ok)
            self.assertIn("--check", preflight.format_report(report, self.root))
            self.assertNotIn("secret-", preflight.format_report(report, self.root))
        with patch.object(preflight, "check_port", return_value=None), patch.object(preflight.subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout=b"secret-output")):
            report = preflight.run(python=str(self.python), binary=self.binary, port=8765, root=self.root)
        self.assertFalse(report.ok)
        self.assertNotIn("secret-output", preflight.format_report(report, self.root))

    def test_occupied_port_has_fix_and_is_not_silently_reassigned(self):
        for reuse in (False, True):
            with self.subTest(server_reuses_address=reuse), socket.socket() as listener:
                if reuse:
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind(("127.0.0.1", 0))
                listener.listen()
                port = listener.getsockname()[1]
                problem = preflight.check_port(port)
                self.assertIn(str(port), problem)
                self.assertIn("--port", problem)
            self.assertIsNone(preflight.check_port(port))
        self.assertIn("1 and 65535", preflight.check_port(0))

    @unittest.skipUnless(os.name == "posix", "POSIX server-side TCP TIME_WAIT restart contract")
    def test_closed_server_with_accepted_connection_can_restart_during_time_wait(self):
        with socket.socket() as listener:
            # Match asyncio/Tokio servers, without allowing concurrent listeners.
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.settimeout(2)
            port = listener.getsockname()[1]
            with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
                connection, _ = listener.accept()
                with connection:
                    connection.settimeout(2)
                    # The server actively closes first, leaving its port (rather
                    # than only the client's ephemeral port) in TIME_WAIT.
                    connection.shutdown(socket.SHUT_WR)
                    self.assertEqual(client.recv(1), b"")
                    client.shutdown(socket.SHUT_WR)
                    self.assertEqual(connection.recv(1), b"")
        with socket.socket() as naive_probe:
            with self.assertRaises(OSError) as occupied:
                naive_probe.bind(("127.0.0.1", port))
            self.assertEqual(occupied.exception.errno, errno.EADDRINUSE,
                             "Fixture must reproduce TIME_WAIT rejection without address reuse")
        self.assertIsNone(preflight.check_port(port))
        with socket.socket() as restarted:
            restarted.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            restarted.bind(("127.0.0.1", port))
            restarted.listen()
            self.assertIn("occupied", preflight.check_port(port), "A live restarted listener must still be refused")

    def test_platform_socket_policy_is_exclusive_on_windows_and_never_reuses_port(self):
        for platform in ("nt", "posix"):
            with self.subTest(platform=platform), patch.object(preflight.os, "name", platform), \
                 patch.object(preflight.socket, "SO_EXCLUSIVEADDRUSE", -5, create=True), \
                 patch.object(preflight.socket, "socket") as create:
                self.assertIsNone(preflight.check_port(8765))
                probe = create.return_value.__enter__.return_value
                option = -5 if platform == "nt" else socket.SO_REUSEADDR
                probe.setsockopt.assert_called_once_with(socket.SOL_SOCKET, option, 1)
                probe.bind.assert_called_once_with(("127.0.0.1", 8765))

    def test_check_returns_before_state_locks_configuration_or_product_start(self):
        state = self.root / "absent state"
        for ok in (True, False):
            report = preflight.Report(python=str(self.python), errors=[] if ok else ["Required item missing"])
            with self.subTest(ok=ok), patch.dict(os.environ, {}, clear=True), patch.object(launcher.preflight, "run", return_value=report), \
                 patch.object(launcher.preflight, "load_environment_file") as config, \
                 patch.object(launcher, "ProcessFamily") as family, patch.object(launcher.subprocess, "Popen") as spawn, \
                 patch.object(launcher.urllib.request, "urlopen") as network, patch.object(launcher.webbrowser, "open") as browser, \
                 contextlib.redirect_stdout(io.StringIO()):
                result = launcher.main(["--check", "--open", "--python", "python", "--binary", str(self.binary), "--state-root", str(state)])
            self.assertEqual(result, 0 if ok else 2)
            self.assertFalse(state.exists())
            for action in (config, family, spawn, network, browser):
                action.assert_not_called()

    def test_failed_normal_start_also_refuses_state_mutation(self):
        state = self.root / "absent state"
        with patch.dict(os.environ, {}, clear=True), patch.object(launcher.preflight, "run", return_value=preflight.Report(errors=["missing"])), \
             patch.object(launcher.subprocess, "Popen") as spawn, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(launcher.main(["--binary", str(self.binary), "--state-root", str(state)]), 2)
        spawn.assert_not_called()
        self.assertFalse(state.exists())

    def test_normal_launch_config_uses_selected_python_and_keeps_values_out_of_logs(self):
        config = self.root / "selected provider.env"
        config.write_text("CIVIL_API_KEY=${TEST_CONFIG_SECRET}\nCIVIL_MODEL=fixture-model\n", encoding="utf-8")
        environment = preflight.diagnostic_environment()
        environment["TEST_CONFIG_SECRET"] = "private-configuration-fixture"
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            parsed = preflight.load_environment_file(sys.executable, config, environment, self.root)
        self.assertEqual(parsed, {"CIVIL_API_KEY": "private-configuration-fixture", "CIVIL_MODEL": "fixture-model"})
        self.assertEqual(output.getvalue(), "")
        with patch.object(preflight.subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout=b"private-configuration-fixture")):
            with self.assertRaises(ValueError) as raised:
                preflight.load_environment_file(str(self.python), config, environment, self.root)
        self.assertNotIn("private-configuration-fixture", str(raised.exception))

    def test_real_check_in_empty_environment_is_read_only_and_finds_missing_packages(self):
        # A real venv verifies that the selected interpreter (not the test runner)
        # supplies diagnostics. An isolated product copy exposes accidental writes.
        environment = self.root / "empty Python environment"
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(environment)], check=True, timeout=30,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        selected = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        scripts = self.root / "product" / "scripts"
        scripts.mkdir(parents=True)
        for name in ("start_unified_workbench.py", "workbench_preflight.py"):
            shutil.copyfile(ROOT / "scripts" / name, scripts / name)
        config = self.root / "provider.env"
        config.write_text("OPENAI_API_KEY=read-only-secret\n", encoding="utf-8")
        workspace = self.root / "job"
        workspace.mkdir()
        (workspace / "source.txt").write_text("unchanged source", encoding="utf-8")
        token = self.root / "login.token"
        token.write_text("secret-login-" + "x" * 40, encoding="utf-8")
        state = self.root / "never-created"
        def snapshot():
            return {str(path.relative_to(self.root)): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in self.root.rglob("*") if path.is_file()}
        before = snapshot()
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
        env = preflight.diagnostic_environment()
        env.update(OPENAI_API_KEY="ambient-secret", PATH=str(selected.parent) + os.pathsep + env.get("PATH", ""))
        result = subprocess.run([sys.executable, "-B", str(scripts / "start_unified_workbench.py"), "--check", "--python", "python",
                                 "--binary", str(self.binary), "--state-root", str(state), "--port", str(port), "--env-file", str(config),
                                 "--user-id", "fixture", "--workspace", str(workspace), "--token-file", str(token)],
                                cwd=scripts.parent, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        text = result.stdout.decode("utf-8") + result.stderr.decode("utf-8")
        self.assertEqual(result.returncode, 2, text)
        self.assertIn(os.path.normcase(str(selected)), os.path.normcase(text))
        self.assertIn("uvicorn>=", text)
        self.assertNotIn("ambient-secret", text)
        self.assertNotIn("read-only-secret", text)
        self.assertNotIn(token.read_text(), text)
        self.assertFalse(state.exists())
        self.assertEqual(before, snapshot(), "--check modified a product/config/workspace file or wrote bytecode")


if __name__ == "__main__":
    unittest.main()
