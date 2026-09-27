"""Model-free packing boundary and real parser/HITL/export integration checks."""
from pathlib import Path
import os
import secrets
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class UnifiedPackingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="civil-packing-tests-")
        cls.output = Path(cls.temp.name)
        cls.token = secrets.token_urlsafe(48)
        cls.env = patch.dict(os.environ, {
            "PYTHON_DOTENV_DISABLED": "1", "PACKING_LLM_AGENT": "0", "PACKING_SKIP_SKJOLBER": "1",
            "PACKING_OUTPUT_DIR": str(cls.output), "PACKING_TRACE_DIR": str(cls.output / "traces"),
            "CB_DB_PATH": str(cls.output / "packing.db"), "CB_STORAGE": "json",
            "PACKING_LG_CHECKPOINT_PATH": str(cls.output / "checkpoints.db"),
            "CIVIL_DOMAIN_TOKEN": cls.token, "CIVIL_OUT_ROOT": str(cls.output / "domains"),
            "CIVIL_DATA_ROOT": str(cls.output / "data"), "CIVIL_DOMAIN_WORKSPACE": str(cls.output / "domains"),
        })
        cls.env.start()
        from fastapi.testclient import TestClient
        from demo.domain_service import app
        cls.client = TestClient(app, headers={"Authorization": "Bearer " + cls.token})

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        from packing_assistant import storage, lg_checkpoint
        storage.reset_storage()
        if lg_checkpoint._CONN is not None:
            lg_checkpoint._CONN.close()
            lg_checkpoint._CONN = None
            lg_checkpoint._SAVER = None
        import gc
        gc.collect()
        cls.env.stop()
        cls.temp.cleanup()

    def test_page_assets_and_health_expose_actual_packing(self):
        page = self.client.get("/packing")
        self.assertEqual(page.status_code, 200)
        self.assertIn('/packing/static/vendor/vue.min.js', page.text)
        self.assertEqual(self.client.get('/packing/static/vendor/vue.min.js').status_code, 200)
        self.assertEqual(self.client.get('/packing/static/vendor/app.py').status_code, 404)
        caps = self.client.get('/packing/api/health').json()['unified_packing']
        self.assertTrue(caps['available'])
        self.assertFalse(caps['model_calls'])

    def test_generic_dispatch_model_mode_and_arbitrary_path_are_not_exposed(self):
        for path in ('agent', 'turn', 'mcp/tools/call'):
            self.assertEqual(self.client.post('/packing/api/' + path, json={}).status_code, 404)
        for body in ({'mode': 'llm_toolcall'}, {'agent_mode': 'auto'}, {'ns_llm_enrich': True}, {'packing_options': {'ns_llm_enrich': True}}):
            self.assertEqual(self.client.post('/packing/api/pipeline', json=body).status_code, 400)
        self.assertEqual(self.client.post('/packing/api/table/parse/json', json={'path': str(ROOT / 'AGENTS.md')}).status_code, 400)
        self.assertEqual(self.client.get('/packing/api/artifact', params={'path': str(ROOT / 'AGENTS.md')}).status_code, 403)

    def test_actual_uploaded_table_waits_for_confirmation_then_exports(self):
        raw = b'name,quantity,length_mm,width_mm,height_mm,weight_kg\nSynthetic crate,1,800,600,400,20\n'
        parsed = self.client.post('/packing/api/table/parse', files={'file': ('fixture.csv', raw, 'text/csv')})
        self.assertEqual(parsed.status_code, 200, parsed.text)
        materials = parsed.json()['materials']
        self.assertEqual(len(materials), 1)
        sid = 'synthetic-unified-packing'
        payload = {'materials': materials, 'preset': '', 'session_id': sid, 'mode': 'steps',
                   'enable_auto_confirm': False, 'save_artifacts': False,
                   'packing_options': {'crate_passthrough': True, 'multi_start': False}}
        result = self.client.post('/packing/api/pipeline', json=payload)
        self.assertEqual(result.status_code, 200, result.text)
        state = result.json().get('public', result.json())
        self.assertEqual(state['phase'], 'await_user_confirm', result.text)
        confirmed = self.client.post('/packing/api/confirm', json={'session_id': sid, 'action': 'confirm', 'container_type': '40HQ'})
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertNotEqual(confirmed.json()['phase'], 'await_user_confirm')
        export = self.client.post('/packing/api/export/shipment', json={'session_id': sid})
        self.assertEqual(export.status_code, 200, export.text)
        download = self.client.get('/packing' + export.json()['download_url'])
        self.assertEqual(download.status_code, 200)
        self.assertTrue(download.content.startswith(b'PK'))
        Path(export.json()['xlsx_path']).resolve().relative_to(self.output.resolve())


if __name__ == '__main__':
    unittest.main()
