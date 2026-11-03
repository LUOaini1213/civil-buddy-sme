"""Safety and evidence checks for the real-host engineering runner, no solvers."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import native_engineering_runtime as native
from scripts.test_unified_runtime_http import instance_environment


class NativeRuntimeGate(unittest.TestCase):
    def test_model_free_environment_drops_provider_and_tracing_configuration(self):
        with patch.dict(os.environ, {"CIVIL_API_KEY": "fixture", "OPENAI_API_KEY": "fixture",
                                     "LANGSMITH_API_KEY": "fixture", "LANGCHAIN_TRACING_V2": "true",
                                     "CIVIL_API_BASE": "https://not-an-authorized-provider.invalid"}):
            env = instance_environment(None)
        self.assertNotIn("CIVIL_API_KEY", env)
        self.assertNotIn("CIVIL_API_BASE", env)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("LANGSMITH_API_KEY", env)
        self.assertEqual(env["LANGCHAIN_TRACING_V2"], "false")
        self.assertEqual(env["LANGSMITH_TRACING"], "false")
        self.assertEqual(env["PYTHON_DOTENV_DISABLED"], "1")

    def test_scripted_fixture_can_only_use_valid_loopback_port(self):
        env = instance_environment(12345)
        self.assertEqual(env["CIVIL_API_BASE"], "http://127.0.0.1:12345/v1")
        self.assertEqual(env["CIVIL_API_KEY"], "scripted-local-fixture")
        for bad in (0, -1, 65536, True, "https://provider.invalid"):
            with self.subTest(bad=bad), self.assertRaises(AssertionError):
                instance_environment(bad)

    def test_failed_preflight_never_constructs_or_launches_host(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            binary = root / "synthetic-not-executed.exe"
            binary.write_bytes(b"synthetic non-executed fixture")
            with patch.object(native, "preflight", return_value=([], ["missing fixture engine"])), \
                    patch.object(native, "Instance") as instance:
                report = native.run(binary, root / "report")
            instance.assert_not_called()
            self.assertFalse(report["passed"])
            self.assertEqual(report["checks"], {})
            self.assertFalse(report["real_provider_calls"])
            self.assertEqual(json.loads((root / "report/report.json").read_text())["preflight_errors"], ["missing fixture engine"])

    def test_step_preflight_failure_never_launches_host(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            binary = root / "synthetic-not-executed.exe"
            binary.write_bytes(b"synthetic non-executed fixture")
            with patch.object(native, "preflight", return_value=([], ["missing STEP kernel"])), \
                    patch.object(native, "Instance") as instance:
                report = native.run(binary, root / "report", with_step=True)
            instance.assert_not_called()
            self.assertTrue(report["with_step"])
            self.assertFalse(report["passed"])

    def test_existing_evidence_directory_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            previous = root / "previous"
            previous.mkdir()
            with self.assertRaises(FileExistsError), patch.object(native, "preflight") as preflight:
                native.run(root / "unused.exe", previous)
            preflight.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
