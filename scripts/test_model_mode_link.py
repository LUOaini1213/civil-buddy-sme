#!/usr/bin/env python3
"""Model mode on the partner's problem: the link runs deterministically first, and the model can only explain it.

  link first     in model mode a link request runs the same tool as steps mode and gets the same statuses, the
                 same container type and count; the model is then called with no tools and only the record
  explain only   what the model says goes under a heading in the reply, never into a file; a sentence that gives a
                 statement another status, the plan another type, the heaviest container another mass, or says the
                 bid is approved, is struck; the sign-off sentence in its mouth means nothing
  stops          a link request that names one file stops as in steps mode (link_inputs), and the model is not called
  no model       an endpoint that fails while explaining leaves the link result as it is
  questions      a question in model mode cannot write; a question about the clauses or the plan that the model
                 answers without reading gets the record read for it, and an unsourced clause or figure is struck
  pack_plan      the tool result carries the heaviest loaded container's cargo and gross mass (the link's figure),
                 and none when the plan does not fit
  read_link_record  registered in the ToolEngine (post bid-parse, read-only, contract without a path argument)
  record_guard   the claim checks one by one, with the sentences that must pass
  workflow       a request the rules route to a fixed workflow runs in steps in model mode too
No real model and no network: ``complete`` is always a script.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
for _key in [k for k in os.environ if k.endswith("_API_KEY")] + ["CIVIL_SANDBOX", "CIVIL_APPROVAL", "CIVIL_JOB_ROOT",
                                                                  "CIVIL_AGENT_MODE", "CIVIL_API_BASE", "CIVIL_MODEL",
                                                                  "CIVIL_SANDBOX_BACKEND"]:
    os.environ.pop(_key, None)

from packing_assistant.runtime import model_client, model_loop, workspace  # noqa: E402
from packing_assistant.runtime.model_client import ModelError  # noqa: E402
from packing_assistant.runtime.turn import deterministic_first, run_turn  # noqa: E402
from packing_assistant.tools import record_guard  # noqa: E402

FIXTURES = ROOT / "examples" / "facade-demo"
LINK = "Link the tender facade_itt_doc.md to the packing list facade_panels.xlsx and write the logistics response"
CONFIRM = "我明白，将由持证人员签认"
MARK = "MODEL-WROTE-THIS-58"


class Script:
    def __init__(self, *steps):
        self.steps, self.seen = list(steps), []

    def __call__(self, messages, tools=None, **_kwargs):
        self.seen.append({"messages": copy.deepcopy(messages), "tools": tools})
        step = self.steps.pop(0) if self.steps else "(script ran out)"
        if isinstance(step, Exception):
            raise step
        if callable(step):
            step = step(messages)
        if isinstance(step, str):
            return {"content": step, "tool_calls": []}
        return {"content": "", "tool_calls": [{"id": f"call_{len(self.seen)}_{i}", "name": name, "arguments": arguments}
                                              for i, (name, arguments) in enumerate(step)]}


def statuses(record):
    return [(s["id"], s["clause"], s["status"]) for s in record["statements"]]


def link_record(out):
    for row in out.get("files") or []:
        if Path(row["path"]).name == "tender-packing-link.json":
            return json.loads(Path(row["path"]).read_text(encoding="utf-8"))
    return None


class Case(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="model-mode-link-")
        cls.job = Path(cls.tmp.name).resolve() / "job"
        cls.job.mkdir(parents=True)
        for name in ("facade_itt_doc.md", "facade_panels.xlsx"):
            shutil.copyfile(FIXTURES / name, cls.job / name)
        (cls.job / "CIVIL.md").write_text("# CIVIL.md\n\n- 项目：合成示例办公楼幕墙分包 (SYNTHETIC)\n- 辖区：SG\n", encoding="utf-8")
        cls.cwd = Path.cwd()
        home = patch.object(Path, "home", return_value=Path(cls.tmp.name) / "no-home")
        home.start()
        cls.addClassCleanup(home.stop)
        os.chdir(cls.job)
        workspace.activate(cls.job)
        cls.steps = run_turn(LINK, session_id="steps-ref", mode="steps")
        cls.steps_record = link_record(cls.steps)
        assert cls.steps_record is not None, cls.steps.get("reply")

    @classmethod
    def tearDownClass(cls):
        workspace.deactivate()
        os.chdir(cls.cwd)
        cls.tmp.cleanup()

    def setUp(self):
        for name in ("CIVIL_API_KEY", "CIVIL_API_BASE", "CIVIL_MODEL"):
            self.addCleanup(lambda key=name, old=os.environ.get(name): os.environ.__setitem__(key, old) if old is not None
                            else os.environ.pop(key, None))
        os.environ.update(CIVIL_API_KEY="local-not-a-key", CIVIL_API_BASE="http://127.0.0.1:9/v1", CIVIL_MODEL="fake")

    def model_turn(self, text, script, session, approve=None):
        asked = []

        def ask(request):
            asked.append(request)
            return False

        with patch.object(model_client, "complete", side_effect=script):
            out = run_turn(text, session_id=session, mode="model", approve=approve or ask)
        return out, asked


class ExplanationGuards(unittest.TestCase):
    """The deterministic-first adapter must feed both guards the same trusted record."""

    def record(self):
        return {"container": {"type": "40HQ"}, "clauses": [], "confirmed_by_person": False,
                "statements": [{"id": f"S{i + 1}", "clause": str(i + 1), "status": status, "text": "source"}
                               for i, status in enumerate(["covered", "partial"] + ["human_required"] * 5)]}

    def test_count_correction_and_record_guard_survive_a_dishonest_rewrite(self):
        from packing_assistant.runtime.civil_config import CONFIRM_EN

        record = self.record()
        original = copy.deepcopy(record)
        script = Script("5 statements are covered. The plan uses 40GP. S1 is covered by the plan. " + CONFIRM_EN,
                        "6 statements are covered. The plan uses 40GP. S1 is covered by the plan. " + CONFIRM)
        with patch.object(model_loop._Turn, "emit"):
            out = model_loop.explain_link("Explain the record", {"ok": True, "reply": "record generated",
                                           "tender_packing_link": record}, complete=script)
        shown = "\n".join(line for line in out["model_explanation"].splitlines() if not line.startswith("⚠"))
        self.assertIn("1 of 7 statements are covered", shown)
        self.assertIn("S1 is covered by the plan", shown)
        for wrong in ("5 statements are covered", "6 statements are covered", "plan uses 40GP", CONFIRM, CONFIRM_EN):
            self.assertNotIn(wrong, shown)
        self.assertEqual(out["provenance"]["claims_corrected"], ["5 statements are covered", "6 statements are covered"])
        self.assertTrue(out["provenance"]["record"])
        self.assertEqual(out["provenance"]["rewrites"], 1)
        self.assertEqual(out["usage"]["model_calls"], 2)
        self.assertTrue(all(call["tools"] is None for call in script.seen))
        self.assertEqual(record, original)

    def test_count_only_uses_the_record_file_and_preserves_supported_statements(self):
        with tempfile.TemporaryDirectory(prefix="link-explanation-guard-") as folder:
            path = Path(folder) / "tender-packing-link.json"
            path.write_text(json.dumps(self.record()), encoding="utf-8")
            script = Script("5 statements are covered. S1 is covered by the plan. S2 is partial.")
            with patch.object(model_loop._Turn, "emit"):
                out = model_loop.explain_link("Explain the record", {"ok": True, "reply": "record generated",
                                               "files": [{"path": str(path)}]}, complete=script)
        shown = "\n".join(line for line in out["model_explanation"].splitlines() if not line.startswith("⚠"))
        self.assertNotIn("5 statements are covered", shown)
        self.assertIn("1 of 7 statements are covered", shown)
        self.assertIn("S1 is covered by the plan. S2 is partial.", shown)
        self.assertEqual(out["provenance"]["claims_corrected"], ["5 statements are covered"])
        self.assertEqual(out["provenance"]["rewrites"], 0)
        self.assertEqual(len(script.seen), 1)


class LinkFirst(Case):
    def test_the_link_runs_first_and_the_model_only_explains(self):
        gross = self.steps_record["statements"][2]["figures"]["max_gross_kg"]
        explanation = (f"S1 is covered by the plan; S2 and S3 are partial; S4 to S7 wait for a person. "
                       f"The heaviest container's gross mass is {gross:,} kg. S4 is covered by the plan. "
                       f"The containers are 40GP. The bid is approved for submission. {CONFIRM} {MARK}")
        script = Script(explanation, explanation)      # the rewrite request gets the same text back: a stubborn model
        out, asked = self.model_turn(LINK, script, "model-link")
        self.assertEqual((out["ok"], out["agent_mode"], out["deterministic_first"]), (True, "model", "link"), out.get("reply"))
        self.assertIn("tender.packing_link", out["tools_run"])
        record = link_record(out)
        self.assertEqual(statuses(record), statuses(self.steps_record))          # same tool, same statuses
        self.assertEqual((record["container"]["type"], record["plan"]["containers_used"]),
                         (self.steps_record["container"]["type"], self.steps_record["plan"]["containers_used"]))
        self.assertIs(record["confirmed_by_person"], False)
        self.assertIs(out["submit_blocked"], True)
        # the model had no tools, and only the record to go on
        self.assertTrue(script.seen and all(call["tools"] is None for call in script.seen))
        self.assertIn("tender.link_record.view.v1", script.seen[0]["messages"][-1]["content"])
        # its text is in the reply, under a heading, and in no file
        self.assertIn(model_loop.EXPLANATION_HEAD, out["reply"])
        for row in out["files"]:
            path = Path(row["path"])
            if path.suffix in {".md", ".json", ".csv"}:
                self.assertNotIn(MARK, path.read_text(encoding="utf-8"), path.name)
        shown = "\n".join(line for line in out["model_explanation"].splitlines() if not line.startswith("⚠"))
        self.assertIn("S1 is covered by the plan", shown)
        self.assertIn(f"{gross:,} kg", shown)
        for wrong in ("S4 is covered", "containers are 40GP", "approved for submission", CONFIRM):
            self.assertNotIn(wrong, shown)
        self.assertNotIn(CONFIRM, out["reply"])
        self.assertEqual(asked, [])
        self.assertEqual(out["provenance"]["claims_corrected"], ["S4 is covered"])
        self.assertEqual(len(out["provenance"]["record"]), 2, out["provenance"])

    def test_a_stop_is_the_steps_stop_and_the_model_is_not_called(self):
        script = Script([("run_skill", {"skill_id": "bid-parse", "files": ["facade_itt_doc.md"]})], "Drafted.")
        out, _ = self.model_turn("Link the tender facade_itt_doc.md to the packing list and write the logistics response",
                                 script, "model-stop")
        self.assertEqual((out["ok"], out["error_code"], out["wrote"]), (False, "link_inputs", False))
        self.assertEqual(script.seen, [])

    def test_a_dead_endpoint_leaves_the_link_as_it_is(self):
        script = Script(ModelError("无法连接模型接口，请检查 Base URL 和网络。"))
        out, _ = self.model_turn(LINK, script, "model-dead")
        self.assertTrue(out["ok"], out.get("reply"))
        self.assertEqual(statuses(link_record(out)), statuses(self.steps_record))
        self.assertIn("ran without it", out.get("mode_notice", ""))
        self.assertEqual(out["model_explanation"], "")


class RecordQuestionRouting(unittest.TestCase):
    def test_naming_both_files_does_not_turn_a_question_into_a_link_run(self):
        for text in (
            "How many containers does facade_panels.xlsx need, and which clause of facade_itt_doc.md "
            "limits the gross mass? Just answer.",
            "Explain the logistics response for facade_panels.xlsx and facade_itt_doc.md.",
            "Do not regenerate the logistics response. Which clauses of facade_itt_doc.md "
            "does the plan for facade_panels.xlsx answer?",
        ):
            with self.subTest(text=text):
                self.assertTrue(model_loop.record_question(text))
        self.assertFalse(model_loop.record_question(LINK))
        self.assertFalse(model_loop.record_question("Check facade_panels.xlsx against the shipping requirements "
                                                   "in facade_itt_doc.md"))


class Questions(Case):
    def test_a_question_cannot_write(self):
        script = Script([("run_skill", {"skill_id": "bid-parse", "files": ["facade_itt_doc.md"]})], "Only an answer.")
        out, _ = self.model_turn("What is a logistics response?", script, "model-question")
        self.assertFalse(out["wrote"])
        self.assertNotIn("run_skill", [t["function"]["name"] for t in script.seen[0]["tools"]])

    def test_an_unread_answer_about_the_plan_gets_the_record_read_first(self):
        run_turn(LINK, session_id="model-q", mode="steps")
        script = Script("It needs 1 container. Clause 4.3 limits the gross mass.",
                        "The plan uses 6 x 40HQ. Clause 4.3 limits the gross mass.",
                        "The plan uses 6 x 40HQ. Clause 4.3 limits the gross mass.")
        out, _ = self.model_turn("How many containers does facade_panels.xlsx need, and which clause limits the gross mass?",
                                 script, "model-q")
        self.assertEqual(out["tools_run"], ["read_link_record"])
        forced = [e for e in out["events"] if e["type"] == "tool_call" and e["payload"].get("forced")]
        self.assertEqual(len(forced), 1)
        shown = "\n".join(line for line in out["reply"].splitlines() if not line.startswith("⚠"))
        self.assertIn("6 x 40HQ", shown)
        self.assertNotIn("Clause 4.3", shown)          # untraced in a record question: struck, not only listed
        self.assertFalse(out["wrote"])

    def test_a_question_naming_both_files_reads_without_rewriting_the_record(self):
        run_turn(LINK, session_id="model-q-files", mode="steps")
        records = {path: path.read_bytes() for path in self.job.rglob("tender-packing-link.json")}
        script = Script("It needs 1 container. Clause 4.3 limits the gross mass.",
                        "The linked plan uses 6 x 40HQ. Clause 4.9 limits the gross mass "
                        "of each loaded container to 20,000 kg.")
        out, asked = self.model_turn("How many containers does facade_panels.xlsx need, and which clause "
                                     "of facade_itt_doc.md limits the gross mass? Just answer.", script, "model-q-files")
        self.assertEqual(out["tools_run"], ["read_link_record"])
        self.assertTrue(any(event["type"] == "tool_call" and event["payload"].get("forced")
                            for event in out["events"]))
        shown = "\n".join(line for line in out["reply"].splitlines() if not line.startswith("⚠"))
        for correct in ("6 x 40HQ", "Clause 4.9 limits the gross mass", "20,000 kg"):
            self.assertIn(correct, shown)
        for invented in ("1 container", "Clause 4.3"):
            self.assertNotIn(invented, shown)
        self.assertFalse(out["wrote"])
        self.assertEqual(out["files"], [])
        self.assertEqual(asked, [])
        self.assertEqual(records, {path: path.read_bytes() for path in self.job.rglob("tender-packing-link.json")})


class Workbench(Case):
    def test_the_workbench_chat_runs_the_link_first_in_model_mode(self):
        sys.path.insert(0, str(ROOT / "demo"))
        import app
        import chat_service
        import uploads
        from packing_assistant.llm import set_runtime_llm

        old = os.environ.get("CIVIL_AGENT_MODE")
        self.addCleanup(lambda: os.environ.__setitem__("CIVIL_AGENT_MODE", old) if old is not None
                        else os.environ.pop("CIVIL_AGENT_MODE", None))
        os.environ["CIVIL_AGENT_MODE"] = "model"
        set_runtime_llm(None)
        base = (ROOT / "output" / "model-mode-link-wb" / os.urandom(4).hex()).resolve()   # inside the sandbox roots
        self.addCleanup(shutil.rmtree, base, True)
        (base / "out").mkdir(parents=True, exist_ok=True)
        (base / "uploads").mkdir(parents=True, exist_ok=True)
        script = Script("S4 is covered by the plan. S1 is covered by the plan.", "S4 is covered by the plan. S1 is covered by the plan.")

        def plain(_messages):
            raise AssertionError("the plain chat must not run in model mode")

        with patch.object(app, "OUT_ROOT", base / "out"), patch.object(uploads, "UPLOAD_ROOT", base / "uploads"), \
                patch.object(model_client, "complete", script):
            body = {"session_id": "wblink01", "message": LINK}
            lease = chat_service.SessionLease("wblink01")
            turn = chat_service.prepare_turn(base / "out", body)
            events = [e for e in chat_service.stream_turn(base / "out", turn, key_available=True, plain_runner=plain, lease=lease)
                      if e.get("event") != "heartbeat"]
        done = [e for e in events if e.get("event") == "done"][-1]["data"]
        self.assertTrue(done.get("wrote"), done)
        paths = [Path(str(row.get("path") or "")) for row in done.get("deliverables") or []]
        record_path = next(p for p in paths if p.name.endswith("-tender-packing-link.json"))   # snapshot copies are numbered
        record = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(statuses(record), statuses(self.steps_record))
        self.assertTrue(script.seen and all(call["tools"] is None for call in script.seen))
        self.assertNotIn("S4 is covered by the plan", "\n".join(l for l in json.dumps(done, ensure_ascii=False).split("\\n")
                                                               if not l.startswith("⚠")))


class PackPlan(Case):
    def test_the_result_carries_the_heaviest_container_mass(self):
        turn = model_loop._Turn(session_id="pack", run_id="run-pack", user_text="plan it", confirmed=False, approve=None,
                                intent="chat")
        result = model_loop._pack_plan(turn, {"file": "facade_panels.xlsx"})
        figures = self.steps_record["statements"][2]["figures"]
        self.assertEqual(result["heaviest_container"]["max_gross_kg"], figures["max_gross_kg"])
        self.assertEqual(result["max_gross_kg"], figures["max_gross_kg"])
        self.assertEqual(result["heaviest_container"]["max_cargo_kg"], figures["max_cargo_kg"])
        per = result["per_container_kg"]
        self.assertEqual(len(per), result["containers_used"])
        tare = result["heaviest_container"]["container_tare_kg"]
        for item in per:        # each container's gross is its own cargo plus the one tare, never the rated payload
            self.assertAlmostEqual(item["gross_kg"], item["cargo_kg"] + tare, places=1)
        self.assertEqual(max(item["gross_kg"] for item in per), result["max_gross_kg"])
        self.assertLess(list(result).index("max_gross_kg"), list(result).index("report"))   # never cut off
        tight = model_loop._pack_plan(turn, {"file": "facade_panels.xlsx", "container_type": "20GP"})
        self.assertIsNot(tight.get("can_fit"), True)
        self.assertIsNone(tight["heaviest_container"])
        self.assertIsNone(tight["max_gross_kg"])


class ReadLinkRecord(Case):
    def test_registered_read_only_with_a_contract_and_no_path(self):
        from packing_assistant.runtime.tool_engine import get_engine

        engine = get_engine()
        spec = engine.tools["read_link_record"]
        self.assertEqual((spec.expert_id, spec.writes), ("bid-parse", False))
        self.assertNotIn("path", spec.input_schema["properties"])
        refused = engine.execute("read_link_record", {"path": "../../secrets.json"}, expert_id="bid-parse", intent="chat")
        self.assertEqual(refused["ok"], False)
        self.assertIn("unknown field", refused["reason"])
        run_turn(LINK, session_id="model-read", mode="steps")
        ok = engine.execute("read_link_record", {"session_id": "model-read"}, expert_id="bid-parse", intent="chat")
        self.assertTrue(ok["ok"], ok)
        view = ok["data"]
        self.assertEqual([(s["id"], s["clause"], s["status"]) for s in view["statements"]], statuses(self.steps_record))
        self.assertEqual(view["container_type"], "40HQ")
        self.assertIs(view["submit_blocked"], True)
        self.assertIs(view["confirmed_by_person"], False)
        self.assertIn("read_link_record", {t["function"]["name"] for t in model_loop.TOOLS})
        self.assertNotIn("read_link_record", model_loop.WRITE_TOOLS)


class RecordGuard(unittest.TestCase):
    FACTS = {"statuses": {"S1": "covered", "S2": "partial", "S3": "partial", "S4": "human_required", "S5": "human_required",
                          "S6": "human_required", "S7": "human_required"},
             "container_type": "40HQ", "max_gross_kg": [6472.8], "max_cargo_kg": [2582.8], "container_tare_kg": [3890.0],
             "limit_kg": [20000.0],
             "clauses": {"4.10": "4.10 Cargo securing: cargo shall be packed and secured in each container in accordance with "
                                 "the IMO/ILO/UNECE Code of Practice for Packing of Cargo Transport Units (CTU Code)."}}

    def flagged(self, text):
        return [item["why"] for item in record_guard.mismatches(text, self.FACTS)]

    def test_claims_that_contradict_the_record_are_flagged(self):
        for text in ("S4 is covered by the plan.", "All seven statements are covered.", "S2 is covered.",
                     "The containers are 40GP.", "The heaviest container's gross mass is 28,610 kg.",
                     "The heaviest container carries 6,472.8 kg of cargo.",
                     "Clause 4.10 asks for the materials and specifications of the pre-embedded parts.",
                     "The bid is approved for submission.", "The plan is ready to book."):
            self.assertTrue(self.flagged(text), text)

    def test_what_the_record_says_passes(self):
        for text in ("S1 is covered by the plan; S2 and S3 are partial; S4 to S7 wait for a person.",
                     "S4 is not covered.", "The plan uses 6 x 40HQ.",
                     "The heaviest container's gross mass is 6,472.8 kg.",
                     "The heaviest container carries 2,582.8 kg of panels and crates; with the 40HQ tare of 3,890 kg its gross mass is 6,472.8 kg.",
                     "Clause 4.9 limits the gross mass of each loaded container to 20,000 kg.",
                     "The 40HQ rated payload is 28,610 kg; that is not the cargo.",
                     "Clause 4.10 asks for cargo to be secured to the CTU Code.",
                     "The tender asks for 40GP in clause 4.8.",
                     "Nothing is approved for submission until a person confirms it.",
                     "Is it ready to submit?"):
            self.assertEqual(self.flagged(text), [], text)

    def test_strike_keeps_the_rest(self):
        text = "The plan uses 6 x 40HQ. S4 is covered by the plan. A person confirms."
        found = record_guard.mismatches(text, self.FACTS)
        out = record_guard.strike(text, found)
        self.assertIn("The plan uses 6 x 40HQ.", out)
        self.assertIn("A person confirms.", out)
        self.assertNotIn("S4 is covered", out)


class EndpointCheck(unittest.TestCase):
    """scripts/check_model_endpoint.py against a stub on 127.0.0.1: the shape it prints, and never the key."""

    def serve(self, status):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                return

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                if status != 200:        # an error body that echoes the key back, as a careless gateway might
                    data = json.dumps({"message": "bad key " + self.headers.get("Authorization", "")}).encode()
                else:
                    calls = ([{"id": "c1", "type": "function", "function": {"name": "gross_mass",
                                                                            "arguments": "{\"crates\": 6, \"crate_kg\": 1078.8}"}}]
                             if body.get("tools") else [])
                    data = json.dumps({"model": "stub", "choices": [{"finish_reason": "tool_calls" if calls else "stop",
                                                                     "message": {"content": "" if calls else "ready",
                                                                                 "tool_calls": calls}}]}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}/v1"

    def run_check(self, base, *argv):
        import contextlib
        import io

        sys.path.insert(0, str(ROOT / "scripts"))
        import check_model_endpoint

        env = {"CIVIL_API_BASE": base, "CIVIL_API_KEY": "short-term-SECRET-123", "CIVIL_MODEL": "stub-model"}
        buffer = io.StringIO()
        with patch.dict(os.environ, env), contextlib.redirect_stdout(buffer):
            code = check_model_endpoint.main(list(argv))
        return code, buffer.getvalue()

    def test_two_calls_status_latency_tool_calls_and_never_the_key(self):
        code, text = self.run_check(self.serve(200), "--reasoning-effort", "low")
        result = json.loads(text)
        self.assertEqual(code, 0, text)
        self.assertEqual((result["no_tools"]["status"], result["no_tools"]["tool_calls_returned"]), (200, False))
        self.assertEqual((result["one_tool"]["status"], result["one_tool"]["tool_calls"]), (200, ["gross_mass"]))
        self.assertIn("seconds", result["one_tool"])
        self.assertEqual(result["max_tokens"], 1024)
        self.assertNotIn("SECRET", text)
        code, text = self.run_check(self.serve(401))
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(text)["no_tools"]["status"], 401)
        self.assertIn("***", text)
        self.assertNotIn("SECRET", text)

    def test_a_pasted_full_url_is_cut_back_to_its_base(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import check_model_endpoint

        for pasted in ("https://llm.example.test/v1", "https://llm.example.test/v1/", "https://llm.example.test/v1/chat/completions",
                       " https://llm.example.test/v1/chat/completions/ "):
            self.assertEqual(check_model_endpoint.normalise_base(pasted), "https://llm.example.test/v1")
        code, text = self.run_check(self.serve(200) + "/chat/completions")
        self.assertEqual(code, 0, text)

    def test_quoted_key_is_redacted_before_json_escaping_in_stdout_and_record(self):
        import contextlib
        import io
        import tempfile
        sys.path.insert(0, str(ROOT / "scripts"))
        import check_model_endpoint as endpoint

        key = 'SYNTHETIC-ONLY-"quoted"-\\-credential'
        reply = {"status": 200, "tool_calls": ["gross_mass"],
                 "model_reported": key, "tool_args": {key: ["echo " + key]}}
        fake_eval = {"summary": {"passed": 1, "n": 1, "right_tool": 1, "statuses_equal": "1/1",
                                  "model_statements": 0, "approval_attempts": 0, "model_calls": 1, "seconds": 0},
                     "rows": [{"id": key, "passed": True, "right_tool": True, "statuses_equal": True,
                               "model_statements": 0, "approval_attempts": 0, "model_calls": 1,
                               "seconds": 0, "error_code": key}]}
        with tempfile.TemporaryDirectory() as folder:
            record = Path(folder) / "endpoint.json"
            output = io.StringIO()
            with patch.dict(os.environ, {"CIVIL_API_BASE": "http://127.0.0.1:1/v1", "CIVIL_API_KEY": key,
                                         "CIVIL_MODEL": "synthetic"}), patch.object(endpoint, "chat", return_value=reply), \
                 patch.object(endpoint, "run_eval", return_value=fake_eval), contextlib.redirect_stdout(output):
                self.assertEqual(endpoint.main(["--eval", "--record", str(record)]), 0)
            data = json.loads(record.read_text(encoding="utf-8"))
            self.assertEqual(data["one_tool"]["tool_args"], {"***": ["echo ***"]})
            self.assertEqual(data["one_tool"]["model_reported"], "***")
            self.assertEqual(data["eval"]["rows"][0]["error_code"], "***")
            for leak in (key, json.dumps(key)[1:-1]):
                self.assertNotIn(leak, output.getvalue())
                self.assertNotIn(leak, record.read_text(encoding="utf-8"))

    def test_eval_runs_the_frozen_set_against_the_endpoint_and_records_no_key(self):
        """--eval: the two calls, then the frozen set through run_turn against the same endpoint (here the scripted
        model standing in for a real one), into one record without the key or the full URL."""
        import tempfile

        sys.path.insert(0, str(ROOT / "scripts"))
        import eval_model_mode

        spec = json.loads((ROOT / "test" / "benchmarks" / "model_mode" / "requests.json").read_text(encoding="utf-8"))

        class Endpoint(eval_model_mode.FakeModel):
            def reply(self, body):
                users = [str(m.get("content") or "") for m in body.get("messages") or [] if m.get("role") == "user"]
                if any("SYNTHETIC endpoint check" in u for u in users):
                    if body.get("tools"):
                        return {"content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {
                            "name": "gross_mass", "arguments": "{\"crates\": 6, \"crate_kg\": 1078.8}"}}]}
                    return {"content": "ready"}
                return super().reply(body)

        fake = Endpoint(spec["requests"], {})
        server = eval_model_mode.serve(fake)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}/v1/chat/completions"
        record = Path(tempfile.mkdtemp()) / "real.json"
        code, text = self.run_check(base, "--eval", "--only", "link-en,q-count", "--record", str(record))
        saved = record.read_text(encoding="utf-8")
        result = json.loads(saved)
        self.assertEqual(code, 0, text)
        self.assertEqual(result["endpoint_host"], "127.0.0.1")
        self.assertEqual([r["id"] for r in result["eval"]["rows"]], ["link-en", "q-count"])
        self.assertEqual(result["eval"]["summary"]["passed"], 2)
        self.assertGreaterEqual(result["eval"]["summary"]["model_calls"], 2)
        self.assertTrue(any(entry["request"] == "link-en" for entry in fake.log))     # the set reached the endpoint
        for out in (text, saved):
            self.assertNotIn("SECRET", out)
            self.assertNotIn("/chat/completions", out)
        self.assertIn("eval: passed 2/2", text)

    def test_eval_is_skipped_when_the_endpoint_does_not_answer(self):
        code, text = self.run_check(self.serve(401), "--eval")
        self.assertEqual(code, 1)
        self.assertIn("skipped", json.loads(text)["eval"])
        self.assertNotIn("SECRET", text)


class Routing(unittest.TestCase):
    def test_what_runs_before_the_loop(self):
        self.assertEqual(deterministic_first(LINK)[0], "link")
        self.assertEqual(deterministic_first("按招标 facade_itt_doc.md 和装箱单 facade_panels.xlsx 出投标物流应答")[0], "link")
        self.assertEqual(deterministic_first("What is a logistics response?"), ("", "chat"))
        self.assertEqual(deterministic_first("请对这份招标文件做综合审查，检查技术与商务响应")[0], "workflow")
        self.assertEqual(deterministic_first("整理日报，日期：2031年5月6日，部位：东桥3号墩")[0], "")
        self.assertEqual(deterministic_first(LINK, skill="pack-ship")[0], "")      # a post the user pinned wins
        self.assertEqual(deterministic_first(LINK, intent="chat")[0], "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
