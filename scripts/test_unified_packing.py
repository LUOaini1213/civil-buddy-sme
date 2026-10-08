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

    def test_virtual_piece_pipeline_refuses_after_both_auto_and_human_confirmation(self):
        original = {'id': 'SYN-SOLID', 'name': 'One solid member', 'quantity': 1, 'weight_kg': 2000,
                    'length_mm': 1200, 'width_mm': 120, 'height_mm': 100}
        for auto in (False, True):
            sid = 'synthetic-physical-piece-' + str(auto).lower()
            payload = {'materials': [original], 'preset': '', 'session_id': sid, 'mode': 'steps',
                       'enable_auto_confirm': auto, 'save_artifacts': False, 'container_type': '40HQ',
                       'packing_options': {'max_box_net_kg': 1500, 'clearance_mm': 0, 'multi_start': False,
                                           'standard_boxes': True, 'force_dense_sheets': False, 'crate_passthrough': False}}
            response = self.client.post('/packing/api/pipeline', json=payload)
            self.assertEqual(response.status_code, 200, response.text)
            state = response.json().get('public', response.json())
            self.assertFalse(state['ship_ok'])
            refusal = state['needs_human'][0]
            self.assertEqual(refusal['reason'], 'physical_split_not_authorized')
            for key, value in original.items():
                self.assertEqual(refusal['source_material'][key], value)
            if not auto:
                self.assertEqual(state['phase'], 'await_user_confirm')
                response = self.client.post('/packing/api/confirm', json={'session_id': sid, 'action': 'confirm', 'container_type': '40HQ'})
                self.assertEqual(response.status_code, 200, response.text)
                state = response.json().get('public', response.json())
            self.assertFalse(state['ship_ok'])
            self.assertFalse(state['container_plan']['can_fit'])
            self.assertEqual(state['container_plan']['layout'], [])
            self.assertEqual(state['needs_human'][0]['reason'], 'physical_split_not_authorized')
            files_before = set((self.output / 'exports').glob('*'))
            export = self.client.post('/packing/api/export/shipment', json={'session_id': sid})
            self.assertEqual(export.status_code, 409, export.text)
            self.assertIn('出运单未导出', export.json()['detail'])
            self.assertEqual(set((self.output / 'exports').glob('*')), files_before)

    def test_export_rechecks_cached_success_and_preserves_existing_files(self):
        from copy import deepcopy
        from packing_assistant.export_pack import export_shipment_xlsx, ShipmentSourceError
        original = {'id': 'SYN', 'name': 'Solid member', 'quantity': 1, 'weight_kg': 2000,
                    'length_mm': 1200, 'width_mm': 120, 'height_mm': 100}
        # Even a restored older session with cached success/manifests cannot
        # authorize virtual pieces or conflicting source weights.
        for conflict in (False, True):
            material = dict(original, **({'total_weight_kg': 1000} if conflict else {}))
            state = {'materials': [material], 'ship_ok': True,
                     'container_plan': {'can_fit': True, 'containers_used': 1},
                     'por_manifest': {'by_part': [{'part_no': 'OLD', 'total_kg': 1000}]},
                     'secure_work_order': {'items': []},
                     'boxes': [] if conflict else [{'contents': [{'source_material_id': 'SYN', 'split_of': 2}]}]}
            before = deepcopy(state)
            folder = self.output / ('blocked-export-' + str(conflict))
            folder.mkdir()
            sentinel = folder / 'existing.txt'
            sentinel.write_text('keep existing output')
            with self.assertRaises(ShipmentSourceError):
                export_shipment_xlsx(state, output_dir=folder)
            self.assertEqual(state, before)
            self.assertEqual(list(folder.iterdir()), [sentinel])
            self.assertEqual(sentinel.read_text(), 'keep existing output')


if __name__ == '__main__':
    unittest.main()
