"""Acceptance fixture/oracle/provider tests; Rust HTTP acceptance is a separate run."""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager, redirect_stdout
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO, StringIO
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import urllib.parse
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

    def _run_policy_with_real_tools(self, required, meta=None):
        meta = acceptance.fixture(self.root, required) if meta is None else meta
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


class ProductStub:
    """Local HTTP product contract; all document edits use real offline workers.

    A configured DeepSeek name here is only a test of live evidence validation,
    never a call to DeepSeek or proof of real provider acceptance.
    """
    def __init__(self, workspace, *, model=acceptance.MODEL, host="127.0.0.1", token=None):
        self.workspace, self.model, self.host, self.token = workspace, model, host, token
        self.requests, self.artifacts, self.bytes = [], [], {}
        self.status, self.after_cancel, self.cancel_code = "completed", "cancelled", 200
        self.make_outputs, self.on_start, self.started, self.cancelled = True, None, False, False
        self.start_code, self.events_code, self.redirect = 200, 200, None
        self.reflection = None
        self.result_fields = {}
        self.receipt_model, self.receipts = model, True
        self.settlements = [
            {"input_tokens": 123, "output_tokens": 45, "model_calls": 1, "estimated": False},
            {"input_tokens": 100, "output_tokens": 200, "model_calls": 1, "estimated": True}]

    def create_outputs(self):
        policy = AcceptanceTests()
        policy.root = self.workspace
        meta = {"required_sample_count": 6, "hashes": {name: sha256((self.workspace/name).read_bytes()).hexdigest() for name in acceptance.FILES}}
        _, outputs, _, _ = policy._run_policy_with_real_tools(6, meta)
        for i, output in enumerate(outputs):
            name = output["name"]
            path = f"/api/agent/artifacts/a{i}?workspace=w1"
            self.bytes[path] = Path(output["download_path"]).read_bytes()
            self.artifacts.append({"id": f"a{i}", "name": name, "output_sha256": output["output_sha256"], "url": path})

    def snapshot(self, after):
        rows = [{"kind": "tool_finished", "data": {"name": name}}
                for name in ("read_file", "search_sources", "preview_document", "apply_document")]
        if self.receipts:
            rows += [{"kind": "model", "data": {"model": self.receipt_model, "usage": usage}} for usage in self.settlements]
        if self.reflection:
            rows.append({"kind": "status", "data": {"message": self.reflection}})
        rows = [{"seq": i+1, **row} for i, row in enumerate(rows)]
        result = {"artifacts": self.artifacts, "usage": {"limits": acceptance.SERVER_LIMITS,
                  "spent_tokens": sum(u["input_tokens"]+u["output_tokens"] for u in self.settlements),
                  "reserved_tokens": 0, "model_calls": sum(u["model_calls"] for u in self.settlements),
                  "reserved_model_calls": 0, "settlements": self.settlements}}
        result.update(self.result_fields)
        return {"events": [row for row in rows if row["seq"] > after],
                "turn": {"turn_id": "t1", "status": self.status, "last_seq": len(rows),
                         "result": result if self.status in acceptance.TERMINAL else None}}

    @contextmanager
    def serve(self):
        product = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def handle_request(self):
                product.requests.append((self.command, self.path, self.headers.get("Authorization")))
                if product.token and self.headers.get("Authorization") != "Bearer "+product.token:
                    return self.reply(401, {"error": "login required"})
                if product.redirect:
                    self.send_response(302)
                    self.send_header("Location", product.redirect)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                path = urllib.parse.urlsplit(self.path).path
                if path == "/api/agent/capabilities":
                    return self.reply(200, {"models": {"model": product.model, "configured": True,
                                                       "provider_host": product.host}, "notice": product.reflection})
                if path == "/api/agent/workspaces":
                    body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    if body["path"] != str(product.workspace.resolve()):
                        return self.reply(403, {"error": "workspace not allowed"})
                    return self.reply(200, {"workspace": {"id": "w1"}})
                if path == "/api/agent/turns":
                    product.started = True
                    if product.make_outputs:
                        product.create_outputs()
                    if product.on_start:
                        product.on_start()
                    return self.reply(product.start_code, {"turn_id": "t1"})
                if path.endswith("/cancel"):
                    product.cancelled = True
                    if product.cancel_code == 200:
                        product.status = product.after_cancel
                    return self.reply(product.cancel_code, {"ok": product.cancel_code == 200})
                if path.endswith("/events"):
                    after = int(urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)["after_seq"][0])
                    return self.reply(product.events_code, product.snapshot(after))
                if self.path in product.bytes:
                    return self.reply(200, product.bytes[self.path])
                return self.reply(404, {})

            def reply(self, status, value):
                data = value if isinstance(value, bytes) else json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/octet-stream" if isinstance(value, bytes) else "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = handle_request
            do_POST = handle_request

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


class HTTPAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="civil-acceptance-http-")
        self.root = Path(self.temp.name).resolve()
        self.workspace, self.reports = self.root/"workspace", self.root/"reports"
        self.token = "acceptance-login-"+uuid4().hex
        self.token_file = self.root/"login-token.txt"
        self.token_file.write_text(self.token, encoding="ascii")

    def tearDown(self):
        self.temp.cleanup()

    def run_product(self, product, **options):
        with product.serve() as base:
            return acceptance.acceptance(base, self.workspace, self.reports, **options)

    def test_named_auth_real_documents_originals_and_actual_estimated_usage(self):
        product = ProductStub(self.workspace, token=self.token)
        result = self.run_product(product, token_file=self.token_file)
        self.assertTrue(result["passed"], result.get("error") or result.get("validation"))
        self.assertTrue(result["originals"]["unchanged"])
        self.assertTrue(result["originals"]["snapshot_is_final"])
        self.assertTrue(all(header == "Bearer "+self.token for _, _, header in product.requests))
        self.assertEqual(result["usage"]["model_calls"], 2)
        self.assertEqual(result["usage"]["usage_by_measurement"]["provider_reported"]["input_tokens"], 123)
        self.assertEqual(result["usage"]["usage_by_measurement"]["estimated"]["output_tokens"], 200)
        self.assertTrue(result["server_limits"]["deployed_limits_verified"])
        self.assertFalse(result["usage"]["currency_budget_enforced_by_script"])
        self.assertEqual(result["validation"]["formula_recalculation"], "not_performed")
        self.assertNotIn(self.token, (self.reports/"report.json").read_text())

    def test_live_preflight_rejects_wrong_missing_or_scripted_configuration_without_start(self):
        scenarios = [
            (acceptance.MODEL, "api.deepseek.com", "scripted"),
            ("other-model", "api.deepseek.com", "--expected-model"),
            ("deepseek-flash", "wrong.example", "--expected-provider-host"),
            ("deepseek-flash", None, "unverified"),
        ]
        for i, (model, host, message) in enumerate(scenarios):
            with self.subTest(model=model, host=host):
                self.workspace, self.reports = self.root/f"workspace{i}", self.root/f"reports{i}"
                product = ProductStub(self.workspace, model=model, host=host)
                result = self.run_product(product, live=True, expected_model="deepseek-flash", expected_provider_host="api.deepseek.com")
                self.assertFalse(result["passed"])
                self.assertFalse(product.started)
                self.assertIn(message, result["error"]["message"])
                self.assertTrue(result["originals"]["unchanged"])

    def test_live_requires_explicit_expectations_before_network(self):
        with patch.object(acceptance, "http") as network:
            result = acceptance.acceptance("http://127.0.0.1:1", self.workspace, self.reports, live=True)
        network.assert_not_called()
        self.assertIn("--expected-model", result["error"]["message"])
        self.assertTrue(result["originals"]["unchanged"])

    def test_scripted_mode_refuses_external_and_unexposed_provider_hosts(self):
        for i, host in enumerate(("api.deepseek.com", None)):
            self.workspace, self.reports = self.root/f"workspace{i}", self.root/f"reports{i}"
            product = ProductStub(self.workspace, host=host)
            result = self.run_product(product)
            self.assertFalse(product.started)
            self.assertIn("loopback provider host", result["error"]["message"])

    def test_simulated_live_receipt_contract_and_bounded_scope(self):
        product = ProductStub(self.workspace, model="deepseek-flash", host="api.deepseek.com")
        result = self.run_product(product, live=True, expected_model="deepseek-flash", expected_provider_host="api.deepseek.com")
        self.assertTrue(result["passed"], result.get("error"))
        self.assertTrue(result["live_verified"])
        self.assertIn("no independent provider attestation", result["live_evidence"]["scope"])
        self.assertFalse(result["usage"]["provider_reported_is_billing_verified"])

    def test_live_cannot_pass_from_successful_documents_without_real_model_receipts(self):
        for i, receipt_model in enumerate((acceptance.MODEL, None, "unexpected-resolved-model")):
            self.workspace, self.reports = self.root/f"workspace{i}", self.root/f"reports{i}"
            product = ProductStub(self.workspace, model="deepseek-flash", host="api.deepseek.com")
            product.receipt_model, product.receipts = receipt_model, receipt_model is not None
            result = self.run_product(product, live=True, expected_model="deepseek-flash", expected_provider_host="api.deepseek.com")
            self.assertTrue(result["validation"]["passed"])
            self.assertFalse(result["passed"])
            self.assertFalse(result["live_verified"])

    def test_failed_and_cancelled_turns_still_report_modified_or_missing_originals(self):
        for i, status in enumerate(("failed", "cancelled", "interrupted")):
            self.workspace, self.reports = self.root/f"workspace{i}", self.root/f"reports{i}"
            product = ProductStub(self.workspace)
            product.status, product.make_outputs = status, False
            source = self.workspace/"requirements.pdf"
            product.on_start = (lambda: source.write_bytes(b"changed")) if i == 0 else source.unlink
            result = self.run_product(product)
            self.assertFalse(result["passed"])
            self.assertTrue(result["originals"]["checked"])
            self.assertFalse(result["originals"]["unchanged"])
            self.assertEqual(result["originals"]["files"]["requirements.pdf"]["status"], "changed" if i == 0 else "unavailable")
            self.assertTrue(result["originals"]["snapshot_is_final"])
            self.assertFalse(product.cancelled, "A failed, cancelled or recovered interrupted turn is already terminal")

    def test_recovered_interrupted_turn_is_terminal_without_timeout_or_cancel(self):
        product = ProductStub(self.workspace)
        product.status, product.make_outputs = "interrupted", False
        product.result_fields = {"reason": "runtime_restart"}
        result = self.run_product(product, timeout=0.03)
        self.assertEqual(result["turn"]["status"], "interrupted")
        self.assertFalse(result["passed"])
        self.assertEqual(result["error"], {"type": "TaskInterrupted", "message": "runtime_restart"})
        self.assertNotIn("cancellation", result)
        self.assertFalse(product.cancelled)
        self.assertTrue(result["originals"]["unchanged"])
        self.assertTrue(result["originals"]["snapshot_is_final"])

    def test_failed_provider_reason_reaches_cli_without_losing_originals_or_usage(self):
        product = ProductStub(self.workspace, token=self.token)
        product.status, product.make_outputs = "failed", False
        message = "provider HTTP 401; check configuration or retry later"
        product.result_fields = {"error": message+" "+self.token}
        with product.serve() as base:
            args = ["unified_acceptance.py", "--base", base, "--work-dir", str(self.workspace),
                    "--report-dir", str(self.reports), "--token-file", str(self.token_file)]
            stdout = StringIO()
            with patch.object(sys, "argv", args), redirect_stdout(stdout):
                code = acceptance.main()
        summary = json.loads(stdout.getvalue())
        result = json.loads((self.reports/"report.json").read_text(encoding="utf-8"))
        self.assertEqual(code, 1)
        self.assertEqual(summary["error"], result["error"])
        self.assertEqual(result["error"]["type"], "TaskFailed")
        self.assertIn(message, result["error"]["message"])
        self.assertNotIn(self.token, stdout.getvalue())
        self.assertNotIn(self.token, json.dumps(result))
        self.assertEqual(result["turn"]["status"], "failed")
        self.assertTrue(result["originals"]["unchanged"])
        self.assertEqual(result["usage"]["model_calls"], 2)
        self.assertTrue(result["server_limits"]["deployed_limits_verified"])

    def test_cancelled_task_has_typed_fallback_error_without_server_reason(self):
        product = ProductStub(self.workspace)
        product.status, product.make_outputs = "cancelled", False
        result = self.run_product(product)
        self.assertEqual(result["error"]["type"], "TaskCancelled")
        self.assertIn("cancelled", result["error"]["message"])
        self.assertTrue(result["originals"]["unchanged"])

    def test_timeout_cancels_then_observes_terminal_and_originals(self):
        product = ProductStub(self.workspace)
        product.status, product.make_outputs = "running", False
        result = self.run_product(product, timeout=0.03, cancel_timeout=0.2)
        self.assertFalse(result["passed"])
        self.assertEqual(result["error"]["type"], "TimeoutError")
        self.assertTrue(product.cancelled)
        self.assertTrue(result["cancellation"]["acknowledged"])
        self.assertTrue(result["cancellation"]["shutdown_verified"])
        self.assertFalse(result["cancellation"]["still_running"])
        self.assertEqual(result["turn"]["status"], "cancelled")
        self.assertTrue(result["originals"]["snapshot_is_final"])

    def test_cancel_ack_does_not_mean_stopped_and_poll_grace_is_bounded(self):
        product = ProductStub(self.workspace)
        product.status, product.after_cancel, product.make_outputs = "running", "cancelling", False
        started = time.monotonic()
        result = self.run_product(product, timeout=0.03, cancel_timeout=0.5)
        self.assertLess(time.monotonic()-started, 3)
        self.assertTrue(result["cancellation"]["acknowledged"])
        self.assertTrue(result["cancellation"]["still_running"])
        self.assertFalse(result["cancellation"]["shutdown_verified"])
        self.assertFalse(result["originals"]["snapshot_is_final"])
        self.assertTrue(result["originals"]["unchanged"])
        self.assertEqual(result["usage"]["source"], "model_events_partial")
        self.assertIsNone(result["usage"]["model_calls"])
        self.assertFalse(result["server_limits"]["deployed_limits_verified"])

    def test_cancel_failure_retains_observed_running_state_and_original_check(self):
        product = ProductStub(self.workspace)
        product.status, product.make_outputs, product.cancel_code = "running", False, 500
        result = self.run_product(product, timeout=0.02, cancel_timeout=0.06)
        self.assertFalse(result["cancellation"]["acknowledged"])
        self.assertIn("500", result["cancellation"]["request_error"]["message"])
        self.assertTrue(result["cancellation"]["still_running"])
        self.assertTrue(result["originals"]["checked"])

    def test_lost_start_response_does_not_claim_turn_stopped_or_final_preservation(self):
        product = ProductStub(self.workspace)
        product.start_code, product.make_outputs = 500, False
        result = self.run_product(product)
        self.assertTrue(product.started)
        self.assertTrue(result["originals"]["checked"])
        self.assertFalse(result["originals"]["snapshot_is_final"])
        self.assertFalse(result["cancellation"]["shutdown_verified"])
        self.assertIsNone(result["cancellation"]["still_running"])
        self.assertIn("start_idempotency_key", result)

    def test_keyboard_interrupt_requests_cancel_and_checks_originals(self):
        product = ProductStub(self.workspace)
        product.status, product.make_outputs = "running", False
        real_http = acceptance.http
        interrupted = False
        def interrupt_once(base, path, *args, **kwargs):
            nonlocal interrupted
            if "/events?" in path and not interrupted:
                interrupted = True
                (self.workspace/"requirements.pdf").write_bytes(b"changed before interrupt")
                raise KeyboardInterrupt()
            return real_http(base, path, *args, **kwargs)
        with patch.object(acceptance, "http", side_effect=interrupt_once):
            result = self.run_product(product)
        self.assertEqual(result["error"]["type"], "KeyboardInterrupt")
        self.assertTrue(product.cancelled)
        self.assertTrue(result["cancellation"]["shutdown_verified"])
        self.assertFalse(result["originals"]["unchanged"])

    def test_poll_failure_still_cancels_and_reports_unknown_shutdown(self):
        product = ProductStub(self.workspace)
        product.events_code, product.status, product.make_outputs = 500, "running", False
        result = self.run_product(product, cancel_timeout=0.04)
        self.assertTrue(product.cancelled)
        self.assertTrue(result["originals"]["checked"])
        self.assertFalse(result["cancellation"]["shutdown_verified"])
        self.assertIsNone(result["cancellation"]["still_running"])
        self.assertFalse(result["originals"]["snapshot_is_final"])

    def test_redirect_never_receives_login_token_and_errors_do_not_log_it(self):
        destination = ProductStub(self.workspace)
        product = ProductStub(self.workspace, token=self.token)
        with destination.serve() as target:
            product.redirect = target+"/capture?credential="+self.token
            result = self.run_product(product, token_file=self.token_file)
        self.assertIn("redirect refused", result["error"]["message"])
        self.assertEqual(destination.requests, [])
        self.assertEqual(len(product.requests), 1)
        self.assertNotIn(self.token, json.dumps(result))

    def test_reflected_token_is_redacted_in_report_and_events(self):
        product = ProductStub(self.workspace, token=self.token)
        product.reflection = "unexpected echo: "+self.token
        result = self.run_product(product, token_file=self.token_file)
        self.assertTrue(result["passed"])
        for name in ("report.json", "events.jsonl"):
            text = (self.reports/name).read_text()
            self.assertNotIn(self.token, text)
            self.assertIn("[REDACTED_LOGIN_TOKEN]", text)
        self.assertNotIn(self.token, json.dumps(result))

    def test_reflected_login_credential_in_artifact_is_not_written(self):
        product = ProductStub(self.workspace, token=self.token)
        product.on_start = lambda: product.bytes.update({path: self.token.encode() for path in product.bytes})
        result = self.run_product(product, token_file=self.token_file)
        self.assertFalse(result["passed"])
        self.assertIn("reflected login credential", result["error"]["message"])
        self.assertFalse((self.reports/"artifacts").exists())
        self.assertTrue(result["originals"]["unchanged"])

    def test_out_of_order_events_fail_and_still_check_originals(self):
        product = ProductStub(self.workspace)
        product.make_outputs = False
        original_snapshot = product.snapshot
        def invalid_snapshot(after):
            result = original_snapshot(after)
            result["events"].reverse()
            return result
        product.snapshot = invalid_snapshot
        result = self.run_product(product, cancel_timeout=0.04)
        self.assertFalse(result["passed"])
        self.assertIn("strictly increasing", result["error"]["message"])
        self.assertTrue(result["originals"]["checked"])

    def test_token_inside_source_workspace_rejected_before_material_creation(self):
        self.workspace.mkdir()
        inside = self.workspace/"login.txt"
        inside.write_text(self.token)
        with patch.object(acceptance, "http") as network:
            result = acceptance.acceptance("http://127.0.0.1:1", self.workspace, self.reports, token_file=inside)
        network.assert_not_called()
        self.assertIn("outside", result["error"]["message"])
        self.assertFalse((self.workspace/"requirements.pdf").exists())
        self.assertNotIn(self.token, json.dumps(result))


if __name__ == "__main__":
    unittest.main()
