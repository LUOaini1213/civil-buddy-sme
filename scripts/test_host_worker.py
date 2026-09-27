"""Offline tests of the deterministic bootstrap and actual sandbox boundary."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packing_assistant.host_worker import _checked_path, dispatch


class HostWorkerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="civil-host-worker-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()

    def request(self, operation="capabilities", **fields):
        return {"version": 1, "call_id": "test-call", "workspace": str(self.root), "operation": operation, **fields}

    def process(self, module, request, *, backend="app"):
        keys = ("SystemRoot", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP", "LANG", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "HOME")
        env = {key: os.environ[key] for key in keys if key in os.environ}
        env.update(CIVIL_HOST_WORKSPACE=str(self.root), CIVIL_HOST_MODULE=module, CIVIL_HOST_SANDBOX=backend,
                   PYTHON_DOTENV_DISABLED="1", PYTHONDONTWRITEBYTECODE="1", OPENBLAS_NUM_THREADS="1")
        value = json.dumps(request).encode() + b"\n" if not isinstance(request, bytes) else request
        child = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "packing_assistant/host_worker.py")],
                               input=value, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               cwd=ROOT, env=env, timeout=30,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(child.returncode, 0, child.stderr.decode(errors="replace"))
        return json.loads(child.stdout)

    def test_unknown_modules_paths_secrets_and_workspace_confusion_are_denied(self):
        for path in ("../outside", ".env", ".env ", "keys/token.pem", "file:stream", "nul.txt"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                _checked_path(self.root, path)
        with self.assertRaises(ValueError):
            dispatch("subprocess", self.request(), self.root)
        with self.assertRaises(ValueError):
            dispatch("packing_assistant.host_worker", self.request(workspace=str(self.root.parent)), self.root)
        outside = self.root.parent / "outside.txt"
        with self.assertRaises(ValueError):
            dispatch("packing_assistant.documents.worker", self.request("read", source=str(outside)), self.root)

    def test_engineering_dispatch_is_direct_and_does_not_call_old_spawn_wrapper(self):
        payload = {"document": {"entities": []}, "config": {"mode": "section"}}
        with patch("packing_assistant.engineering.worker.dispatch", return_value={"calculated": True}) as calculation, \
                patch("packing_assistant.engineering.worker.run", side_effect=AssertionError("nested spawn")):
            result = dispatch("packing_assistant.engineering.worker", self.request("section", payload=payload), self.root)
        calculation.assert_called_once_with("section", payload)
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"], {"calculated": True})
        with self.assertRaises(ValueError):
            dispatch("packing_assistant.engineering.worker", self.request("ifc_check", payload={"path": "secret.ifc"}), self.root)

    def test_app_mode_is_explicit_and_never_claims_kernel_enforcement(self):
        result = self.process("packing_assistant.host_worker", self.request())
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["sandbox"]["backend"], "application-policy")
        self.assertFalse(any(result["sandbox"]["enforces"].values()))
        self.assertTrue(result["result"]["documents"]["xlsx"]["available"])

    def test_failure_to_bootstrap_never_parses_or_dispatches_the_request(self):
        result = self.process("packing_assistant.host_worker", b"definitely invalid json\n", backend="unsupported")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "sandbox_unavailable")
        self.assertFalse((self.root / ".civil-buddy").exists())

    def test_secret_reads_and_nonfinite_json_are_rejected(self):
        (self.root / ".env").write_text("TOKEN=not-for-model", encoding="utf-8")
        result = self.process("packing_assistant.documents.worker", self.request("read", source=".env"))
        self.assertFalse(result["ok"])
        self.assertNotIn("not-for-model", json.dumps(result))
        result = self.process("packing_assistant.host_worker", b'{"value":NaN}\n')
        self.assertFalse(result["ok"])

    def test_fixed_review_service_reuses_asserted_verdict_filter(self):
        result = self.process("packing_assistant.review_worker", self.request("verdicts", texts=["可以开工。", "需由持证人员复核。"]), backend="os")
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["result"]["results"][0]["found"])
        self.assertNotEqual(result["result"]["results"][0]["text"], "可以开工。")
        self.assertFalse(result["result"]["results"][1]["found"])
        rejected = self.process("packing_assistant.review_worker", self.request("run_agent", texts=[]))
        self.assertFalse(rejected["ok"])

    @unittest.skipUnless(sys.platform == "win32" or sys.platform.startswith("linux"), "OS backend is not implemented here")
    def test_real_os_probe_and_retrieval_write_inside_allowed_root(self):
        probe = self.process("packing_assistant.host_worker", self.request(), backend="os")
        self.assertTrue(probe["ok"], probe)
        sandbox = probe["sandbox"]
        self.assertTrue(sandbox["enforces"]["write"])
        self.assertTrue(sandbox["enforces"]["spawn"])
        self.assertFalse(sandbox["enforces"]["read"])
        self.assertEqual(sandbox["selftest"]["write_job_folder"], "denied")
        self.assertEqual(sandbox["selftest"]["spawn_process"], "denied")
        if sys.platform == "win32":
            self.assertFalse(sandbox["enforces"]["network"])
        (self.root / "material.md").write_text("Concrete volume is documented as 12 cubic metres.", encoding="utf-8")
        result = self.process("packing_assistant.retrieval.worker", self.request("index", sources=["material.md"]), backend="os")
        self.assertTrue(result["ok"], result)
        self.assertTrue((self.root / ".civil-buddy/out/retrieval/sources.sqlite3").is_file())
        result = self.process("packing_assistant.retrieval.worker", self.request("search", sources=["material.md"], query="Concrete volume"), backend="os")
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["result"]["hits"], result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
