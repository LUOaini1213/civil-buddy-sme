"""Offline bootstrap failures must be useful, bounded, and occur before stdin."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from packing_assistant import host_worker as host
from packing_assistant.runtime import os_sandbox
from packing_assistant.runtime.os_sandbox import worker


class BootstrapDiagnosticsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The runner supplies a new owned evidence folder. Keep its fixtures so
        # failed attempts can be inspected; these tests never remove job trees.
        parent = os.environ.get("CIVIL_BOOTSTRAP_TEST_ROOT")
        cls.root = Path(tempfile.mkdtemp(prefix="civil-bootstrap-", dir=parent)).resolve()
        cls.secret = "private-workspace-API_KEY-do-not-emit"

    def setUp(self):
        self.env = {"CIVIL_HOST_SANDBOX": "os", "CIVIL_HOST_WORKSPACE": str(self.root),
                    "CIVIL_HOST_MODULE": "packing_assistant.review_worker"}
        self.checks = {"write_inside_state": "allowed", "write_job_folder": "denied", "write_home": "denied",
                       "write_system_temp": "denied", "spawn_process": "denied", "inet_socket": "denied"}
        self.report = {"backend": "test-kernel", "enforces": {"write": True, "spawn": True, "network": False}}

    def run_main(self, *, prepare_error=None, confine_error=None, selftest_error=None, checks=None, env=None):
        output = type("Output", (), {"buffer": io.BytesIO()})()
        input_stream = MagicMock()
        input_stream.buffer.readline.side_effect = AssertionError("Bootstrap failure must not read stdin")
        with patch.dict(os.environ, env or self.env, clear=True), patch.object(tempfile, "tempdir", None), \
                patch.object(os_sandbox, "prepare", side_effect=prepare_error), \
                patch.object(worker, "_confine", return_value=self.report, side_effect=confine_error), \
                patch.object(worker, "selftest", return_value=checks if checks is not None else self.checks,
                             side_effect=selftest_error), \
                patch.object(host, "dispatch") as dispatch, \
                patch.object(host.sys, "stdout", output), patch.object(host.sys, "stderr", io.StringIO()), \
                patch.object(host.sys, "stdin", input_stream):
            self.assertEqual(host.main(), 0)
            dispatch.assert_not_called()
            input_stream.buffer.readline.assert_not_called()
        response = json.loads(output.buffer.getvalue())
        self.assertFalse(response["ok"])
        self.assertIsNone(response["call_id"])
        self.assertEqual(response["error"]["code"], "sandbox_unavailable")
        self.assertFalse(response["sandbox"]["available"])
        self.assertEqual(response["sandbox"]["backend"], "unavailable")
        self.assertFalse(any(response["sandbox"]["enforces"].values()))
        self.assertNotIn(self.secret, json.dumps(response))
        self.assertNotIn(str(self.root), json.dumps(response))
        return response

    def test_root_validation_failure_hides_filename(self):
        response = self.run_main(env={**self.env, "CIVIL_HOST_WORKSPACE": str(self.root / self.secret)})
        self.assertEqual(response["error"]["details"],
                         {"phase": "root_validation", "category": "os_error", "os_error_code": 2})

    def test_invalid_backend_hides_environment_value(self):
        response = self.run_main(env={**self.env, "CIVIL_HOST_SANDBOX": self.secret})
        self.assertEqual(response["sandbox"]["asked"], "invalid")
        self.assertEqual(response["error"]["details"],
                         {"phase": "root_validation", "category": "initialization_failed"})

    def test_missing_workspace_environment_hides_keyerror(self):
        response = self.run_main(env={"CIVIL_HOST_SANDBOX": "os"})
        self.assertEqual(response["error"]["details"],
                         {"phase": "root_validation", "category": "initialization_failed"})
        self.assertNotIn("CIVIL_HOST_WORKSPACE", json.dumps(response))

    def test_write_preparation_preserves_typed_win32_access_denied(self):
        error = OSError(self.secret)
        error.winerror = 5
        response = self.run_main(prepare_error=error)
        self.assertEqual(response["error"]["details"],
                         {"phase": "write_root_preparation", "category": "permission_denied", "os_error_code": 5})

    def test_write_preparation_initialization_failure(self):
        response = self.run_main(prepare_error=RuntimeError(self.secret))
        self.assertEqual(response["error"]["details"],
                         {"phase": "write_root_preparation", "category": "initialization_failed"})

    def test_kernel_failure_preserves_numeric_errno(self):
        response = self.run_main(confine_error=OSError(22, self.secret))
        self.assertEqual(response["error"]["details"],
                         {"phase": "kernel_confinement", "category": "os_error", "os_error_code": 22})

    def test_selftest_exception_is_distinct(self):
        response = self.run_main(selftest_error=PermissionError(13, self.secret))
        self.assertEqual(response["error"]["details"],
                         {"phase": "selftest", "category": "selftest_failed", "os_error_code": 13})

    def test_failed_selftest_contains_only_known_keys_and_statuses(self):
        checks = {**self.checks, "write_inside_state": "error: " + self.secret,
                  "write_system_temp": "unavailable", self.secret: self.secret}
        response = self.run_main(checks=checks)
        details = response["error"]["details"]
        self.assertEqual(set(details), {"phase", "category", "selftest"})
        self.assertEqual(details["phase"], "selftest")
        self.assertEqual(details["category"], "selftest_failed")
        self.assertEqual(set(details["selftest"]), set(self.checks))
        self.assertEqual(details["selftest"]["write_inside_state"], "unavailable")
        self.assertEqual(details["selftest"]["write_system_temp"], "unavailable")
        self.assertTrue(set(details["selftest"].values()) <= {"allowed", "denied", "unavailable"})

    def test_allowed_forbidden_probe_still_refuses_operation(self):
        for key in ("write_job_folder", "write_home", "spawn_process"):
            with self.subTest(key=key):
                response = self.run_main(checks={**self.checks, key: "allowed"})
                self.assertEqual(response["error"]["details"]["category"], "selftest_failed")

    def test_missing_required_selftest_probe_refuses_operation(self):
        checks = {key: value for key, value in self.checks.items() if key != "spawn_process"}
        response = self.run_main(checks=checks)
        details = response["error"]["details"]
        self.assertEqual(details["category"], "selftest_failed")
        self.assertEqual(details["selftest"], checks)
        self.assertNotIn("os_error_code", details)

    def test_unproved_kernel_restriction_still_refuses_operation(self):
        for key in ("write", "spawn"):
            with self.subTest(key=key):
                self.report["enforces"][key] = False
                response = self.run_main()
                self.assertEqual(response["error"]["details"]["category"], "selftest_failed")
                self.report["enforces"][key] = True

    def test_codes_are_bounded_typed_integers_without_text_parsing(self):
        for value in (None, True, False, 0, -1, 2_147_483_648, "5", 5.0):
            with self.subTest(value=value):
                error = OSError(self.secret + " (WinError 5)")
                error.winerror = value
                self.assertNotIn("os_error_code", host._bootstrap_details("kernel_confinement", error))
        error = OSError(13, self.secret)
        error.winerror = 1816
        self.assertEqual(host._bootstrap_details("kernel_confinement", error)["os_error_code"], 1816)
        error.winerror = 2_147_483_647
        self.assertEqual(host._bootstrap_details("kernel_confinement", error)["os_error_code"], 2_147_483_647)

    def test_success_sanitizes_optional_probe_diagnostics(self):
        checks = {**self.checks, "write_system_temp": "error: " + self.secret, self.secret: self.secret}
        with patch.dict(os.environ, self.env, clear=True), patch.object(tempfile, "tempdir", None), \
                patch.object(os_sandbox, "prepare"), patch.object(worker, "_confine", return_value=self.report), \
                patch.object(worker, "selftest", return_value=checks):
            root, report = host._bootstrap()
        self.assertEqual(root, self.root)
        self.assertTrue(report["available"])
        self.assertEqual(report["asked"], "os")
        self.assertTrue(report["enforces"]["write"])
        self.assertTrue(report["enforces"]["spawn"])
        self.assertFalse(report["enforces"]["network"])
        self.assertFalse(report["enforces"]["read"])
        self.assertEqual(report["selftest"]["write_system_temp"], "unavailable")
        self.assertNotIn(self.secret, json.dumps(report))

    @unittest.skipUnless(os.name == "nt", "Windows API errors are typed on Windows")
    def test_label_low_preserves_api_return_code(self):
        from packing_assistant.runtime.os_sandbox import _windows
        advapi, kernel = MagicMock(), MagicMock()
        advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.return_value = 1
        advapi.GetSecurityDescriptorSacl.return_value = 1
        advapi.SetNamedSecurityInfoW.return_value = 5
        with patch.object(_windows, "_dlls", return_value=(advapi, kernel)):
            with self.assertRaises(OSError) as raised:
                _windows.label_low(str(self.root / self.secret))
        self.assertEqual(raised.exception.winerror, 5)
        self.assertNotIn(self.secret, str(raised.exception))
        kernel.LocalFree.assert_called_once()


if __name__ == "__main__":
    unittest.main()
