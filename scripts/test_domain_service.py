#!/usr/bin/env python3
"""Offline HTTP contract for the private deterministic domain sidecar."""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
STATE = tempfile.TemporaryDirectory(prefix="civil-domain-test-")
STATE_ROOT = Path(STATE.name).resolve()
TOKEN = secrets.token_urlsafe(48)
os.environ.update(CIVIL_DOMAIN_TOKEN=TOKEN, CIVIL_OUT_ROOT=str(STATE_ROOT / "out"),
                  CIVIL_DATA_ROOT=str(STATE_ROOT / "data"), CIVIL_DOMAIN_WORKSPACE=str(STATE_ROOT / "out"),
                  CIVIL_SANDBOX_ROOTS=str(STATE_ROOT), PACKING_OUTPUT_DIR=str(STATE_ROOT / "packing"),
                  CIVIL_JOB_ROOT=str(STATE_ROOT / "out"), CIVIL_SANDBOX="workspace-write",
                  CIVIL_APPROVAL="on-request", PYTHON_DOTENV_DISABLED="1",
                  CIVIL_AGENT_MODE="model", OPENAI_API_KEY="offline-canary-never-send")

from fastapi.testclient import TestClient
# Simulate a pre-existing repository upload directory without touching real
# user data. Importing the sidecar must redirect both eager and lazy migration.
sys.path.insert(0, str(ROOT / "demo"))
import uploads as legacy_uploads
FOREIGN_UPLOADS = STATE_ROOT / "foreign-repository" / "uploads"
(FOREIGN_UPLOADS / "foreign-session").mkdir(parents=True)
(FOREIGN_UPLOADS / "foreign-session" / "private.txt").write_text("private canary", encoding="utf-8")
legacy_uploads.LEGACY_UPLOAD_ROOT = FOREIGN_UPLOADS
legacy_uploads._DEFAULT_LEGACY_ROOT = FOREIGN_UPLOADS
from demo import domain_service as domain


class DomainHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(domain.app)
        cls.client.__enter__()
        cls.auth = {"Authorization": "Bearer " + TOKEN}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def test_every_surface_requires_bearer_including_health(self):
        for path in ("/health", "/api/health", "/api/sessions", "/api/logistics/projects", "/packing/api/health",
                     "/api/asr/status", "/not-a-route"):
            for headers in ({}, {"Authorization": "Bearer wrong"}, {"Cookie": "cb_token=" + TOKEN}):
                with self.subTest(path=path, header=list(headers)):
                    self.assertEqual(self.client.get(path, headers=headers).status_code, 401)
        self.assertEqual(self.client.get("/health?token=" + TOKEN).status_code, 401)
        self.assertEqual(self.client.get("/health", headers=[("Authorization", "Bearer " + TOKEN),
                                                           ("Authorization", "Bearer " + TOKEN)]).status_code, 401)

    def test_missing_or_weak_token_fails_closed(self):
        for value in ("", "short", "a" * 64):
            with self.subTest(value=value), patch.dict(os.environ, {"CIVIL_DOMAIN_TOKEN": value}):
                self.assertEqual(self.client.get("/health", headers=self.auth).status_code, 503)
                with self.assertRaisesRegex(RuntimeError, "CIVIL_DOMAIN_TOKEN"):
                    with TestClient(domain.app):
                        pass

    def test_approved_route_contract_and_health(self):
        health = self.client.get("/health", headers=self.auth)
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["chat_runtime"], {"deterministic_legacy_tools": True, "model_loop": False})
        paths = {getattr(route, "path", "") for route in domain.app.routes}
        self.assertTrue(domain.LEGACY_PATHS <= paths, domain.LEGACY_PATHS - paths)
        for path in ("/api/sessions", "/api/projects", "/api/catalog", "/api/logistics/projects", "/api/asr/status"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path, headers=self.auth).status_code, 200)
        self.assertEqual(self.client.post("/api/task-route", headers=self.auth, json={"message": "读取箱单"}).status_code, 200)
        self.assertEqual(self.client.get("/api/workflows/test-session/unknown", headers=self.auth).status_code, 404)
        self.assertEqual(self.client.get("/api/deliverables.zip?session_id=test-session", headers=self.auth).status_code, 404)

    def test_admin_model_and_url_import_routes_are_absent(self):
        for path in ("/api/studio/tree", "/api/studio/file", "/api/local", "/api/config", "/api/llm-config",
                     "/api/upload-url", "/api/mcp/tools/call", "/openapi.json", "/docs"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path, headers=self.auth).status_code, 404)
                self.assertEqual(self.client.post(path, headers=self.auth, json={}).status_code, 404)

    def test_module_identity_and_storage_roots(self):
        for name in ("config", "cad_api", "engineering_api", "planning_api", "logistics_api", "chat_service", "uploads"):
            self.assertIs(importlib.import_module(name), importlib.import_module("demo." + name))
        self.assertEqual(domain.legacy.OUT_ROOT, STATE_ROOT / "out")
        self.assertEqual(importlib.import_module("store").DATA, STATE_ROOT / "data" / "user_catalog.json")
        self.assertEqual(importlib.import_module("logistics_api").store().workspace, STATE_ROOT / "out")
        self.assertEqual(os.environ["CIVIL_AGENT_MODE"], "steps")
        self.assertEqual(os.environ["PACKING_LLM_AGENT"], "0")
        self.assertNotIn("OPENAI_API_KEY", os.environ)

    def test_legacy_upload_migration_does_not_adopt_another_repository(self):
        self.assertEqual(legacy_uploads.LEGACY_UPLOAD_ROOT, STATE_ROOT / "data" / "uploads")
        self.assertEqual(legacy_uploads.list_uploads("foreign-session"), [])
        self.assertTrue((FOREIGN_UPLOADS / "foreign-session" / "private.txt").is_file())
        self.assertFalse((STATE_ROOT / "out" / "foreign-session" / "uploads" / "private.txt").exists())

    def test_fresh_runtime_process_inherits_output_root_and_cli_override(self):
        code = '''import importlib, json, os
from pathlib import Path
from packing_assistant.runtime import workspace
items = [(importlib.import_module(name), attr, sub) for name, attr, sub in workspace._TARGETS]
def roots(): return [str(getattr(module, attr)) for module, attr, sub in items]
configured = roots()
saved = os.environ.pop("CIVIL_OUT_ROOT")
for module, _, _ in items: importlib.reload(module)
legacy = roots()
os.environ["CIVIL_OUT_ROOT"] = saved
for module, _, _ in items: importlib.reload(module)
job = Path(saved).parent / "explicit-cli-job"
job.mkdir()
workspace.activate(job)
cli = roots()
workspace.deactivate()
print(json.dumps({"configured":configured,"legacy":legacy,"cli":cli,"restored":roots()}))
'''
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=dict(os.environ),
                                capture_output=True, text=True, encoding="utf-8", timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr)
        values = json.loads(result.stdout)
        for key, base in (("configured", STATE_ROOT / "out"), ("restored", STATE_ROOT / "out"),
                          ("legacy", ROOT / "demo" / "out"),
                          ("cli", STATE_ROOT / "explicit-cli-job" / ".civil-buddy" / "out")):
            self.assertEqual([Path(value) for value in values[key]], [base] * 5 + [base / "_threads"])

    def test_deterministic_draft_and_attachment_reopen_under_shared_root(self):
        sid = "domain-draft"
        uploaded = self.client.post("/api/upload", headers=self.auth, data={"session_id": sid},
                                    files={"files": ("fixture.txt", b"Synthetic source: no measured quantities supplied.", "text/plain")})
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        upload = uploaded.json()["files"][0]
        with patch("packing_assistant.runtime.model_loop.run_model_agent", side_effect=AssertionError("model called")) as model, \
             patch("socket.socket.connect", side_effect=AssertionError("network called")) as network:
            response = self.client.post("/api/chat", headers=self.auth, json={"session_id": sid,
                "message": "请生成项目日报模板，未提供的数据保留 UNSPECIFIED。", "expert_ids": ["pm-daily"],
                "attachments": [upload["id"]], "confirm_text": "我明白，将由持证人员签认"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("event: done", response.text)
            self.assertNotIn("event: error", response.text)
            model.assert_not_called()
            network.assert_not_called()
        detail = self.client.get("/api/sessions/" + sid, headers=self.auth).json()
        runs = domain.chat_service.read_runs(domain.config.OUT_ROOT, sid)
        files = [item for run in runs for item in run.get("deliverables", [])]
        self.assertTrue(files, response.text)
        for item in files:
            Path(item["path"]).resolve().relative_to(STATE_ROOT / "out")
            download = self.client.get("/api/file", headers=self.auth, params={"path": item["path"]})
            self.assertEqual(download.status_code, 200, download.text[:300])
        original = self.client.get("/api/file", headers=self.auth, params={"session": sid, "upload": upload["id"]})
        self.assertEqual(original.content, b"Synthetic source: no measured quantities supplied.")
        self.assertTrue(detail)

    def test_logistics_and_legacy_chat_work_without_network_or_model(self):
        example = self.client.get("/api/logistics/example", headers=self.auth)
        self.assertEqual(example.status_code, 200, example.text)
        saved = self.client.post("/api/logistics/projects", headers=self.auth,
                                 json={"document_id": example.json()["document_id"], "name": "Synthetic domain test"})
        self.assertEqual(saved.status_code, 200, saved.text)
        project = saved.json()["project"]["id"]
        with patch("packing_assistant.runtime.model_loop.run_model_agent", side_effect=AssertionError("model called")) as model, \
             patch("socket.socket.connect", side_effect=AssertionError("network called")) as network, \
             patch.dict(os.environ, {"OPENAI_API_KEY": "offline-canary-never-send"}):
            for sid, extra, message in (("domain-plain", {}, "你好"),
                                        ("domain-logistics", {"logistics_project_id": project}, "汇总当前箱单")):
                response = self.client.post("/api/chat", headers=self.auth,
                                            json={"session_id": sid, "message": message, **extra})
                self.assertEqual(response.status_code, 200, response.text)
                events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
                self.assertIn("event: done", response.text)
                self.assertNotIn("event: error", response.text)
                self.assertTrue(events)
                self.assertEqual(self.client.get("/api/sessions/" + sid, headers=self.auth).status_code, 200)
                self.assertEqual(self.client.get("/api/context?session_id=" + sid, headers=self.auth).status_code, 200)
            model.assert_not_called()
            network.assert_not_called()
        self.assertTrue((STATE_ROOT / "out" / "domain-plain").is_dir())


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        STATE.cleanup()
