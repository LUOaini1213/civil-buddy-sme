"""Acceptance fixture/oracle/provider tests; Rust HTTP acceptance is a separate run."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import unified_acceptance as acceptance
from packing_assistant.documents import handle as document
from packing_assistant.retrieval import handle as retrieval


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="civil-acceptance-test-")
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def test_real_fixtures_contain_conflicting_count_and_preserved_content(self):
        from openpyxl import load_workbook
        from pypdf import PdfReader
        meta = acceptance.fixture(self.root)
        self.assertIn("sample_count=6", PdfReader(self.root/"requirements.pdf").pages[0].extract_text())
        book = load_workbook(self.root/"quantities.xlsx")
        self.assertEqual(book["Counts"]["B2"].value, 4)
        self.assertEqual(book["Unchanged"]["A1"].value, "Keep this sheet")
        book.close()
        self.assertIn(b"sample_count=4", acceptance.package((self.root/"report.docx").read_bytes())["word/document.xml"])
        self.assertNotRegex(acceptance.TASK, r"(?<!\d)6(?!\d)")
        self.assertEqual(meta["required_sample_count"], 6)

    def _run_policy_with_real_tools(self, required):
        meta = acceptance.fixture(self.root, required)
        messages = [{"role": "system", "content": "你是Civil Buddy土木工作台的主代理。可委派只读子代理找证据。"},
                    {"role": "user", "content": acceptance.TASK}]
        observed, outputs = [], []
        for _ in range(10):
            message = acceptance.scripted_message({"messages": messages, "tools": []})
            messages.append(message)
            calls = message.get("tool_calls") or []
            if not calls:
                break
            for call in calls:
                name = call["function"]["name"]
                args = json.loads(call["function"]["arguments"])
                observed.append(name)
                if name == "load_skill":
                    result = {"skill_id": args["skill_id"], "sop": "Fixture capability SOP"}
                elif name == "search_sources":
                    result = retrieval({"version": 1, "call_id": uuid4().hex, "operation": "search", "workspace": str(self.root), "sources": acceptance.FILES, "query": args["query"]})
                elif name in ("read_file", "preview_document", "apply_document"):
                    op = args.get("operation", "read") if name == "read_file" else ("preview" if name == "preview_document" else "apply")
                    worker_args = args.get("arguments", {}) if name == "read_file" else {"patches": args["patches"]}
                    if name != "read_file":
                        for proposal in args["patches"]:
                            evidence = retrieval({"version": 1, "call_id": uuid4().hex, "operation": "verify", "workspace": str(self.root), "sources": acceptance.FILES, "references": proposal["evidence"]})
                            self.assertTrue(evidence["ok"], evidence)
                            self.assertTrue(evidence["result"]["valid"], evidence)
                    req = {"version": 1, "call_id": uuid4().hex, "operation": op, "workspace": str(self.root), "source": args["source"], "arguments": worker_args}
                    if "expected_sha256" in args:
                        req["expected_sha256"] = args["expected_sha256"]
                    result = document(req)
                    self.assertTrue(result["ok"], result)
                    if name == "apply_document":
                        output = result["result"]
                        outputs.append({"name": Path(output["output_path"]).name, "download_path": output["output_path"], "output_sha256": output["output_sha256"]})
                else:
                    self.fail("Unexpected scripted tool: "+name)
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result, ensure_ascii=False)})
        else:
            self.fail("Scripted fixture policy did not finish")
        return meta, outputs, observed, messages[-1]

    def test_scripted_policy_derives_nondefault_requirement_from_real_tool_results(self):
        meta, outputs, observed, final = self._run_policy_with_real_tools(9)
        result = acceptance.validate_outputs(self.root, meta, outputs)
        self.assertTrue(result["passed"], result)
        self.assertEqual(len(outputs), 2)
        self.assertIn("9", final["content"])
        self.assertLess(observed.index("preview_document"), observed.index("apply_document"))
        self.assertIn("search_sources", observed)

    def test_oracle_rejects_unchanged_files_even_when_provider_claims_success(self):
        meta = acceptance.fixture(self.root)
        outputs = [{"name": name, "download_path": str(self.root/name), "output_sha256": meta["hashes"][name]} for name in ("report.docx", "quantities.xlsx")]
        result = acceptance.validate_outputs(self.root, meta, outputs)
        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["docx_changed_and_preserved"])
        self.assertFalse(result["checks"]["xlsx_changed_and_preserved"])

    def test_oracle_rejects_modified_original_and_registered_hash_mismatch(self):
        meta, outputs, _, _ = self._run_policy_with_real_tools(6)
        wrong = deepcopy(outputs)
        wrong[0]["output_sha256"] = "0"*64
        self.assertFalse(acceptance.validate_outputs(self.root, meta, wrong)["passed"])
        (self.root/"requirements.pdf").write_bytes(b"changed original")
        self.assertFalse(acceptance.validate_outputs(self.root, meta, outputs)["checks"]["originals_unchanged"])

    def test_default_mode_never_starts_task_on_a_real_model(self):
        report_dir = self.root/"reports"
        with patch.object(acceptance, "http", return_value={"models": {"model": "deepseek-flash", "configured": True}}) as mock:
            result = acceptance.acceptance("http://127.0.0.1:8765", self.root/"workspace", report_dir)
        self.assertFalse(result["passed"])
        self.assertIn("--live", result["error"]["message"])
        self.assertEqual(mock.call_count, 1)
        self.assertTrue((report_dir/"report.json").is_file())

    def test_loopback_fake_openai_endpoint_has_valid_tool_calls(self):
        server = acceptance.model_server(0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            status = acceptance.http(base, "/health")
            self.assertEqual(status["model"], acceptance.MODEL)
            response = acceptance.http(base, "/v1/chat/completions", {"model": acceptance.MODEL, "messages": [{"role": "system", "content": "你是主代理"}], "tools": []})
            message = response["choices"][0]["message"]
            self.assertEqual(message["role"], "assistant")
            self.assertEqual(response["choices"][0]["finish_reason"], "tool_calls")
            self.assertTrue(any(c["function"]["name"] == "search_sources" for c in message["tool_calls"]))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
