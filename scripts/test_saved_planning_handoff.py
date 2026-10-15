"""Offline saved-plan handoff checks using real API/storage and the fixed CPM."""
from copy import deepcopy
import hashlib
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
from demo import planning_api as api
from packing_assistant.engineering.planning import calculate
from packing_assistant.engineering.planning_binding import context, calculate_saved
from packing_assistant.engineering.planning_records import PlanningStore
from packing_assistant.host_worker import dispatch


class SavedPlanningHandoff(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="civil-saved-plan-handoff-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for manager in [patch.dict(os.environ, {"CIVIL_SANDBOX_ROOTS": str(self.root), "CIVIL_SANDBOX": "workspace-write"}),
                        patch.object(api, "store", lambda: PlanningStore(self.root)),
                        patch.object(api, "RUNS", api.cad.MemoryStore())]:
            manager.start()
            self.addCleanup(manager.stop)
        app = FastAPI()
        app.include_router(api.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def save(self):
        response = self.client.post(api.BASE + "/projects", json={"name": "Synthetic handoff", "plan": api.example(), "synthetic": True})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["project"]

    def test_saved_binding_is_ephemeral_canonical_and_survives_reopen(self):
        project = self.save()
        selection = project["agent_selection"]
        self.assertEqual(selection["kind"], "saved_planning")
        self.assertEqual(selection["project_id"], project["id"])
        self.assertEqual(selection["revision"], 1)
        self.assertIsNone(selection["source_sha256"])
        self.assertEqual(hashlib.sha256(project["agent_inputs_json"].encode()).hexdigest(), selection["inputs_sha256"])
        self.assertEqual(json.loads(project["agent_inputs_json"]), {"plan": project["plan"], "method": "cpm"})
        disk = json.loads((PlanningStore(self.root).root / (project["id"] + ".json")).read_text(encoding="utf-8"))
        self.assertNotIn("agent_selection", disk)
        self.assertNotIn("agent_inputs_json", disk)
        opened = self.client.get(api.BASE + "/projects/" + project["id"]).json()["project"]
        self.assertEqual(opened["agent_selection"], selection)
        self.assertEqual(opened["agent_inputs_json"], project["agent_inputs_json"])

    def test_calendar_resources_method_and_original_source_are_bound(self):
        project = self.save()
        original = project["agent_selection"]["inputs_sha256"]
        for change in (lambda p: p["plan"]["calendar"]["holidays"].append("2026-09-23"),
                       lambda p: p["plan"]["resources"][0].update(capacity=2),
                       lambda p: p.update(method="resource"),
                       lambda p: p["plan"]["tasks"][1].update(duration=5)):
            changed = deepcopy(project)
            change(changed)
            self.assertNotEqual(context(changed)["agent_selection"]["inputs_sha256"], original)
        imported = deepcopy(project)
        imported["import_source"] = {"source": {"sha256": "a" * 64}}
        self.assertEqual(context(imported)["agent_selection"]["source_sha256"], "a" * 64)

    def test_actual_cpm_worker_is_direct_source_preserving_and_has_conflict_evidence(self):
        project = self.save()
        before = deepcopy(project["plan"])
        payload = {"plan": before, "method": "cpm"}
        request = {"version": 1, "workspace": str(self.root), "call_id": "synthetic-plan", "operation": "planning_calculate", "payload": payload}
        with patch("packing_assistant.engineering.worker.run", side_effect=AssertionError("nested process forbidden")):
            response = dispatch("packing_assistant.engineering.worker", request, self.root)
        self.assertTrue(response["ok"])
        result = response["result"]
        self.assertEqual(result["inputs_sha256"], project["agent_selection"]["inputs_sha256"])
        self.assertEqual(result["result"]["duration_workdays"], 8)
        self.assertEqual(result["result"]["engine"]["name"], "civil-buddy-cpm")
        self.assertFalse(result["result"]["resource_feasible"])
        self.assertTrue(result["result"]["resource_conflicts"])
        self.assertEqual(result["plan"], before)
        self.assertEqual(PlanningStore(self.root).open(project["id"])["revision"], 1)

    def test_resource_dependency_failure_never_falls_back_to_cpm(self):
        plan = calculate(api.example())["plan"]
        with patch("packing_assistant.engineering.planning_optimize.optimize", side_effect=ImportError("synthetic missing dependency")), \
             patch("packing_assistant.engineering.planning.calculate", side_effect=AssertionError("CPM fallback forbidden")):
            with self.assertRaisesRegex(ValueError, "不会改算 CPM"):
                calculate_saved({"plan": plan, "method": "resource"})

    def test_unregistered_inputs_code_and_method_are_rejected(self):
        plan = calculate(api.example())["plan"]
        for payload in ({"plan": plan, "method": "model_guess"},
                        {"plan": plan, "method": "cpm", "code": "print('run')"},
                        {"plan": dict(plan, path="private.xlsx"), "method": "cpm"}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                calculate_saved(payload)


if __name__ == "__main__":
    unittest.main()
