"""Offline HTTP acceptance for actual engineering workers and persisted results."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from fastapi import FastAPI
from fastapi.testclient import TestClient
from demo import engineering_api as api
from packing_assistant.engineering.records import AnalysisStore
from packing_assistant.engineering.schedule import ScheduleStore
from packing_assistant.engineering import worker


class EngineeringHTTP(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for context in [patch.dict(os.environ, {"CIVIL_SANDBOX_ROOTS": str(self.root), "CIVIL_SANDBOX": "workspace-write"}),
                        patch.object(api, "RUNS", api.cad.MemoryStore(max_bytes=32*1024*1024)),
                        patch.object(api, "store", lambda: AnalysisStore(self.root / "analyses")),
                        patch.object(api, "schedule_store", lambda: ScheduleStore(self.root))]:
            context.start(); self.addCleanup(context.stop)
        app = FastAPI(); app.include_router(api.router); app.include_router(api.cad.router)
        self.client = TestClient(app); self.addCleanup(self.client.close)

    def test_pages_capabilities_unknown_operations_and_origin(self):
        for path in ("/engineering", "/engineering/schedule"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-cache")
        self.assertIn("frame", self.client.get("/api/engineering/capabilities").json()["tools"])
        response = self.client.post("/api/engineering/frame", json={"model": {}}, headers={"Origin": "https://example.invalid"})
        self.assertEqual(response.status_code, 403)
        with self.assertRaises(ValueError): worker.run("exec", {"code": "invalid"})
        deep_json = '{"ifc":' + '[' * 2000 + '0' + ']' * 2000 + '}'
        self.assertEqual(self.client.post("/api/engineering/ifc/check", content=deep_json, headers={"Content-Type": "application/json"}).status_code, 422)

    @unittest.skipUnless(api.capabilities()["tools"]["frame"]["available"], "Install requirements-engineering.txt")
    def test_real_frame_worker_save_restart_reopen_revision_and_export(self):
        model = self.client.get("/api/engineering/examples/frame").json()["model"]
        response = self.client.post("/api/engineering/frame", json={"model": model})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json(); result = data["result"]
        reactions = result["combinations"][0]["nodes"]
        self.assertAlmostEqual(sum(n["reaction_N"][1] for n in reactions), 4000)
        saved = self.client.post("/api/engineering/projects", json={"name": "Synthetic beam", "run_id": data["run_id"]})
        self.assertEqual(saved.status_code, 200, saved.text)
        identifier = saved.json()["project"]["id"]
        # New store instance and an empty transient run cache simulate restart.
        api.RUNS._items.clear()
        opened = self.client.get("/api/engineering/projects/" + identifier)
        self.assertEqual(opened.status_code, 200, opened.text)
        self.assertEqual(opened.json()["snapshot"]["inputs"], model)
        self.assertEqual(opened.json()["snapshot"]["result"], result)
        current = opened.json()["run_id"]
        save = {"name": "Synthetic beam", "run_id": current, "id": identifier, "expected_revision": 1}
        self.assertEqual(self.client.post("/api/engineering/projects", json=save).status_code, 200)
        self.assertEqual(self.client.post("/api/engineering/projects", json=save).status_code, 409)
        historical = self.client.get(f"/api/engineering/projects/{identifier}?version=1")
        self.assertEqual(historical.json()["version"], 1)
        self.assertEqual(historical.json()["project"]["revision"], 2)
        self.assertEqual(self.client.post("/api/engineering/export", json={"run_id": current, "confirmation": ""}).status_code, 403)
        exported = self.client.post("/api/engineering/export", json={"run_id": current, "confirmation": api.cad.CONFIRMATION})
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertEqual(exported.json()["result"], result)
        self.assertEqual(self.client.post("/api/engineering/frame", json={"model": {"code": "print(1)"}}).status_code, 422)
        self.assertEqual(self.client.get("/api/engineering/projects/" + identifier).json()["snapshot"]["result"], result)

    def test_invalid_ifc_file_envelopes_are_rejected_before_optional_engine_dispatch(self):
        with patch.object(api, "publish") as publish:
            for action, payload in (("check", {"ifc": []}),
                                    ("check", {"ifc": {"name": "../private.ifc", "data_b64": "eA=="},
                                               "ids": {"name": "requirements.ids", "data_b64": "eA=="}}),
                                    ("diff", {"old": {"name": "old.ifc", "data_b64": "!!!!"},
                                              "new": {"name": "new.ifc", "data_b64": "eA=="}})):
                with self.subTest(action=action, payload=payload):
                    self.assertEqual(422, self.client.post("/api/engineering/ifc/" + action, json=payload).status_code)
            publish.assert_not_called()

    @unittest.skipUnless(api.capabilities()["tools"]["section"]["available"], "Install requirements-engineering.txt")
    def test_section_worker_has_hole_and_no_length_then_reopens(self):
        from scripts.test_cad_api import fixture, config
        imported = self.client.post("/api/cad/import", files={"file": ("synthetic.dxf", fixture(), "application/dxf")}).json()
        cfg = config("section"); cfg["parameters"]["section"]["height_m"] = None
        response = self.client.post("/api/engineering/section", json={"document_id": imported["document_id"], "config": cfg})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertAlmostEqual(result["result"]["regions"][0]["area_mm2"], 1500000)
        saved = self.client.post("/api/engineering/projects", json={"name": "Synthetic hollow section", "run_id": result["run_id"]}).json()
        api.cad.DOCUMENTS._items.clear()
        reopened = self.client.get("/api/engineering/projects/" + saved["project"]["id"]).json()
        rerun = self.client.post("/api/engineering/section", json={"document_id": reopened["document_id"], "config": cfg})
        self.assertEqual(rerun.status_code, 200, rerun.text)
        self.assertEqual(rerun.json()["result"], result["result"])

    @unittest.skipUnless(api.capabilities()["tools"]["ifc_check"]["available"], "Install requirements-engineering.txt")
    def test_ifc_check_diff_real_worker_and_malformed_source(self):
        def source(name):
            data = self.client.get("/api/engineering/examples/ifc/" + name)
            self.assertEqual(data.status_code, 200)
            return {"name": name, "data_b64": base64.b64encode(data.content).decode()}
        old, new, ids = source("old.ifc"), source("new.ifc"), source("requirements.ids")
        response = self.client.post("/api/engineering/ifc/check", json={"ifc": old, "ids": ids})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["result"]["report"]["total_checks_fail"], 1)
        diff = self.client.post("/api/engineering/ifc/diff", json={"old": old, "new": new})
        self.assertEqual(diff.status_code, 200, diff.text)
        self.assertEqual(len(diff.json()["result"]["added"]), 1)
        self.assertEqual(len(diff.json()["result"]["changed"]), 1)
        self.assertEqual(self.client.post("/api/engineering/ifc/check", json={"path": "C:/private.ifc"}).status_code, 422)

    def test_schedule_routes_restore_conflict_and_no_path_escape(self):
        body = {"name": "Synthetic", "tasks": [{"id": "A", "name": "Demo", "start": "2026-09-21", "end": "2026-09-22", "progress": 0, "dependencies": []}]}
        response = self.client.post("/api/engineering/schedules", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        project = response.json()["project"]
        body.update(id=project["id"], expected_revision=project["revision"])
        body["tasks"][0]["progress"] = 50
        saved = self.client.post("/api/engineering/schedules", json=body)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(self.client.post("/api/engineering/schedules", json=body).status_code, 409)
        restored = self.client.post(f"/api/engineering/schedules/{project['id']}/undo", json={"expected_revision": saved.json()["project"]["revision"]})
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()["project"]["tasks"][0]["progress"], 0)
        self.assertEqual(self.client.get("/api/engineering/schedules/not-an-id").status_code, 422)

    def test_cancellation_before_dispatch_never_publishes(self):
        identifier = "a" * 32
        self.client.post(f"/api/engineering/operations/{identifier}/cancel")
        with patch.object(api, "publish") as publish:
            response = self.client.post("/api/engineering/frame", json={"model": {}}, headers={"X-CAD-Operation-ID": identifier})
            self.assertEqual(response.status_code, 499, response.text)
            publish.assert_not_called()

    def test_cancelled_save_never_replaces_a_version(self):
        from packing_assistant.runtime import cancel
        snapshot = {"kind": "frame", "inputs": {"source": "synthetic"}, "result": {"value": 1}}
        run_id = api.RUNS.put(snapshot)
        first = self.client.post("/api/engineering/projects", json={"name": "Synthetic", "run_id": run_id}).json()
        identifier = first["project"]["id"]
        before = (self.root / "analyses" / (identifier + ".json")).read_bytes()
        operation_id = "b" * 32
        self.client.post(f"/api/engineering/operations/{operation_id}/cancel")
        body = {"name": "Changed", "run_id": run_id, "id": identifier, "expected_revision": 1}
        response = self.client.post("/api/engineering/projects", json=body, headers={"X-CAD-Operation-ID": operation_id})
        self.assertEqual(response.status_code, 499, response.text)
        self.assertEqual((self.root / "analyses" / (identifier + ".json")).read_bytes(), before)
        # Cancellation after preparing a replacement must also preserve the old file.
        from demo.projects import _write_atomic
        def cancel_at_commit(path, data, **kwargs):
            def before_replace():
                raise cancel.RunCancelled("test cancellation")
            return _write_atomic(path, data, before_replace=before_replace)
        with patch("demo.projects._write_atomic", side_effect=cancel_at_commit):
            response = self.client.post("/api/engineering/projects", json=body)
        self.assertEqual(response.status_code, 499, response.text)
        self.assertEqual((self.root / "analyses" / (identifier + ".json")).read_bytes(), before)

    @unittest.skipUnless(api.capabilities()["tools"]["frame"]["available"], "Install requirements-engineering.txt")
    def test_timeout_terminates_worker(self):
        from packing_assistant.engineering.frame import synthetic_example
        with self.assertRaises(TimeoutError):
            worker.run("frame", synthetic_example(), timeout=.001)


if __name__ == "__main__":
    unittest.main(verbosity=2)
