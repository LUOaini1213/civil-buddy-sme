"""Cheap tests of strict native acceptance; no optional engine is imported."""
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import native_engineering_acceptance as gate


class NativeGateTests(unittest.TestCase):
    def healthy_dependencies(self):
        return [{"module": module, "available": True, "version": pinned or "synthetic-version"}
                for module, _, pinned in gate.DEPENDENCIES]

    def run_case(self, case, minimum=1):
        loader = unittest.TestLoader()
        with patch.object(loader, "loadTestsFromName", return_value=loader.loadTestsFromTestCase(case)), \
                patch("sys.stderr", new_callable=io.StringIO):
            return gate.run_suite("synthetic.unit.fixture", minimum, loader=loader)

    def test_missing_and_wrong_version_dependencies_fail(self):
        self.assertEqual(gate.preflight_errors(self.healthy_dependencies()), [])
        entries = self.healthy_dependencies()
        entries[0]["available"] = False
        entries[1]["version"] = "not-the-pin"
        self.assertEqual(len(gate.preflight_errors(entries)), 2)
        self.assertEqual(len(gate.preflight_errors([])), len(gate.DEPENDENCIES))

    def test_skipped_case_is_not_green(self):
        class Case(unittest.TestCase):
            @unittest.skip("fixture missing dependency")
            def test_skip(self): pass
        report = self.run_case(Case)
        self.assertFalse(report["passed"])
        self.assertEqual(len(report["skipped"]), 1)

    def test_expected_failure_is_not_green(self):
        class Case(unittest.TestCase):
            @unittest.expectedFailure
            def test_expected(self): self.fail("fixture")
        self.assertFalse(self.run_case(Case)["passed"])

    def test_failure_and_unexpected_success_are_not_green(self):
        class Failure(unittest.TestCase):
            def test_fail(self): self.fail("fixture")
        class Unexpected(unittest.TestCase):
            @unittest.expectedFailure
            def test_unexpected(self): pass
        self.assertFalse(self.run_case(Failure)["passed"])
        self.assertFalse(self.run_case(Unexpected)["passed"])

    def test_error_and_empty_or_smaller_suite_are_not_green(self):
        class Error(unittest.TestCase):
            def test_error(self): raise ValueError("fixture")
        class Empty(unittest.TestCase): pass
        class Good(unittest.TestCase):
            def test_ok(self): pass
        self.assertFalse(self.run_case(Error)["passed"])
        self.assertFalse(self.run_case(Empty)["passed"])
        self.assertFalse(self.run_case(Good, minimum=2)["passed"])
        self.assertTrue(self.run_case(Good)["passed"])

    def test_missing_dependency_stops_before_native_import(self):
        with patch.object(gate, "dependency_snapshot", return_value=[]), \
                patch.object(gate, "run_suite") as run:
            report = gate.run_acceptance()
        run.assert_not_called()
        self.assertFalse(report["passed"])
        self.assertEqual(report["tests_run"], 0)
        self.assertFalse(report["live_model_tested"])
        self.assertFalse(report["real_project_tested"])
        self.assertFalse(report["cross_computer_tested"])

    def test_credentials_and_dotenv_are_excluded(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "fixture", "CUSTOM_API_KEY": "fixture"}):
            env = gate.offline_environment()
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("CUSTOM_API_KEY", env)
        self.assertEqual(env["PYTHON_DOTENV_DISABLED"], "1")
        self.assertEqual(env["PYTHONOPTIMIZE"], "0")

    def test_sources_changing_during_run_are_not_green(self):
        with patch.object(gate, "dependency_snapshot", return_value=self.healthy_dependencies()), \
                patch.object(gate, "source_snapshot", side_effect=[{"fixture.py": "before"}, {"fixture.py": "after"}]), \
                patch.object(gate, "run_suite", return_value={"passed": True, "tests_run": 16, "skipped": []}):
            report = gate.run_acceptance()
        self.assertFalse(report["passed"])
        self.assertFalse(report["sources_unchanged"])

    def test_historical_report_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "historical.json"
            path.write_bytes(b"historical fixture")
            with patch.object(gate, "run_acceptance") as run, \
                    patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit) as raised:
                gate.main(["--report", str(path)])
            self.assertEqual(raised.exception.code, 2)
            run.assert_not_called()
            self.assertEqual(path.read_bytes(), b"historical fixture")


if __name__ == "__main__":
    unittest.main(verbosity=2)
