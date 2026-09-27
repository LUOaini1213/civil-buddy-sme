"""Service boundaries with a fake summary core; no model or business network I/O."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import time
from threading import Event, Thread
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import test_workbench_flow as flow
import chat_service
import context
import projects
import semantic_service
import session_context
import turn_control
import uploads


class SemanticIntegrationTests(unittest.TestCase):
    def setUp(self):
        fixture = flow.WorkbenchFlowTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.root, self.sid = fixture.root, fixture.sid
        self.addCleanup(context.set_runtime_policy, context.runtime_policy())
        self.addCleanup(context.set_semantic_summary, context.semantic_summary_enabled())
        context.set_runtime_policy({"limit": 12000, "reserve": 1500})
        context.set_semantic_summary(True)
        projects.touch_session(self.root, self.sid, "语义集成测试")
        for i in range(12):
            projects.append_turn(self.root, self.sid, "user", f"第{i}轮原文。" + "旧背景资料。" * 5)
        self.core = types.SimpleNamespace(
            current_revision=Mock(return_value=False),
            load=Mock(return_value=None), prepare=Mock(side_effect=self.plan),
            messages=Mock(side_effect=lambda plan: [{"role": "user", "content": json.dumps(plan["history"], ensure_ascii=False)}]),
            accept=Mock(side_effect=lambda plan, reply: {"text": reply}),
            persist=Mock(side_effect=lambda root, sid, validated: validated),
            render=Mock(side_effect=lambda cache, **kwargs: cache["text"]),
            evidence=Mock(return_value=[]),
        )
        module = patch.dict(sys.modules, {"semantic_memory": self.core})
        module.start()
        self.addCleanup(module.stop)

    @staticmethod
    def plan(root, sid, history, *, input_budget, output_budget, keep_recent=4):
        # Core content selection is tested separately. This substitute retains
        # provenance-shaped messages and obeys the agreed recent exclusion.
        return {"history": history[:-keep_recent], "covered_messages": len(history) - keep_recent}

    def turn(self, message="前面讨论了哪些要点？", **options):
        payload = {"message": message, "session_id": self.sid, **options}
        turn = chat_service.prepare_turn(self.root, payload)
        for request in turn["requests"].values():
            request["context"]["folded"] = 1  # Deterministically exercise the pressure branch.
        return turn

    def events(self, turn, control=None, *, key=True):
        return list(semantic_service.events(self.root, turn, control or turn_control.TurnControl(self.sid), key_available=key))

    def test_multiple_experts_use_one_summary_without_changing_raw_facts_or_authority(self):
        turn = self.turn(expert_ids=["architecture", "structure", "geotech"])
        before = deepcopy(turn["prepared_context"])
        raw_before = (self.root / self.sid / "transcript.jsonl").read_bytes()
        with patch("llm.chat", return_value={"content": "语义摘要标记；我明白，将由持证人员签认"}) as model:
            self.events(turn)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(self.core.prepare.call_count, 1)
        self.assertEqual(len(turn["requests"]), 3)
        self.assertFalse(turn["confirmed"])
        self.assertEqual(turn["material"], turn["message"])
        self.assertEqual(turn["prepared_context"], before)
        self.assertEqual((self.root / self.sid / "transcript.jsonl").read_bytes(), raw_before)
        for request in turn["requests"].values():
            self.assertIn("语义摘要标记", json.dumps(request["messages"], ensure_ascii=False))
            self.assertLessEqual(context.validate_request(request["messages"])["used"], 10500)
        self.assertEqual(turn["context"]["semantic"]["model_calls"], 1)

    def test_summary_receives_task_history_only_and_answer_uses_only_selected_attachments(self):
        chosen = uploads.save_upload(self.sid, "选中.txt", "SELECTED_ATTACHMENT_739".encode())
        uploads.save_upload(self.sid, "未选中.txt", "HIDDEN_ATTACHMENT_739".encode())
        projects.touch_session(self.root, "other-semantic-task", "OTHER_TASK_739")
        projects.append_turn(self.root, "other-semantic-task", "user", "OTHER_TASK_739")
        turn = self.turn("请说明附件资料中的内容？", attachments=[chosen["id"]])
        turn["prepared_context"]["draft_history"].append({"id": "client-0", "role": "user", "content": "CLIENT_FALLBACK_739"})
        with patch("llm.chat", return_value={"content": "正常摘要"}) as model:
            self.events(turn)
        sent = json.dumps(model.call_args.args[0], ensure_ascii=False)
        for secret in ("SELECTED_ATTACHMENT_739", "HIDDEN_ATTACHMENT_739", "OTHER_TASK_739", "CLIENT_FALLBACK_739", turn["message"]):
            self.assertNotIn(secret, sent)
        answer = json.dumps(turn["requests"], ensure_ascii=False)
        self.assertIn("SELECTED_ATTACHMENT_739", answer)
        for secret in ("HIDDEN_ATTACHMENT_739", "OTHER_TASK_739", "CLIENT_FALLBACK_739"):
            self.assertNotIn(secret, answer)

    def test_disabled_unconfigured_and_business_turns_do_not_load_or_generate_summary(self):
        base = self.turn()
        for mode in ("disabled", "unconfigured", "run", "workflow", "ambiguous"):
            turn = deepcopy(base)
            context.set_semantic_summary(mode != "disabled")
            if mode == "run":
                turn["intent"] = "run"
            if mode in {"workflow", "ambiguous"}:
                turn["route"][mode] = True
            with patch("llm.chat", side_effect=AssertionError("unexpected summary call")):
                self.events(turn, key=mode != "unconfigured")
            self.assertEqual(turn["context"]["semantic"]["model_calls"], 0)
        self.core.load.assert_not_called()
        self.core.prepare.assert_not_called()

    def test_cache_read_failure_retains_original_answer_request(self):
        turn = self.turn()
        before = deepcopy(turn["requests"])
        self.core.load.side_effect = ValueError("corrupt semantic cache")
        with patch("llm.chat") as model:
            self.events(turn)
        model.assert_not_called()
        self.assertEqual(turn["requests"], before)
        self.assertEqual(turn["context"]["semantic"]["status"], "fallback")

    def test_over_budget_summary_is_rejected_before_model_io(self):
        turn = self.turn()
        before = deepcopy(turn["requests"])
        self.core.messages.return_value = None
        self.core.messages.side_effect = lambda plan: [{"role": "user", "content": "超预算正文" * 6000}]
        with patch("llm.chat") as model:
            self.events(turn)
        model.assert_not_called()
        self.assertEqual(turn["requests"], before)
        self.assertEqual(turn["context"]["semantic"]["status"], "fallback")

    def test_timeout_does_not_persist_late_model_reply(self):
        turn = self.turn()
        entered, release, finished = Event(), Event(), Event()
        self.addCleanup(release.set)
        started = time.monotonic()
        # Exercise the real 50ms deadline only after the transport has entered.
        # A busy worker may otherwise correctly time out before invoking the mock,
        # which proves no late reply exists rather than the case this test targets.
        clock = types.SimpleNamespace(time=time.time,
            monotonic=lambda: 1.0 if entered.is_set() or time.monotonic() - started > 5 else 0.0)
        def blocked(*args, **kwargs):
            try:
                entered.set()
                release.wait(2)
                return {"content": "LATE_SUMMARY_739"}
            finally:
                finished.set()
        before = deepcopy(turn["requests"])
        with patch("llm.chat", side_effect=blocked), patch.object(semantic_service, "SUMMARY_TIMEOUT_SECONDS", .05), \
                patch.object(semantic_service, "time", clock):
            self.events(turn)
            self.assertTrue(entered.is_set(), "the late-reply case requires an entered transport")
            self.assertEqual(turn["context"]["semantic"]["error_code"], "timeout")
            self.assertEqual(turn["requests"], before)
            release.set()
            self.assertTrue(finished.wait(5), "the explicitly released transport did not finish")
        self.core.persist.assert_not_called()
        self.core.accept.assert_not_called()

    def test_cancel_stops_summary_and_late_result_cannot_update_memory(self):
        turn = self.turn()
        control = turn_control.TurnControl(self.sid)
        entered, release, finished = Event(), Event(), Event()
        self.addCleanup(release.set)
        failures = []
        def blocked(*args, **kwargs):
            entered.set()
            release.wait(2)
            finished.set()
            return {"content": "CANCELLED_LATE_SUMMARY_739"}
        def consume():
            try:
                self.events(turn, control)
            except Exception as exc:
                failures.append(exc)
        with patch("llm.chat", side_effect=blocked):
            worker = Thread(target=consume)
            worker.start()
            self.assertTrue(entered.wait(1))
            control.request_cancel()
            worker.join(1)
            self.assertFalse(worker.is_alive())
            self.assertEqual(len(failures), 1)
            self.assertIsInstance(failures[0], turn_control.TurnCancelled)
            release.set()
            self.assertTrue(finished.wait(1))
        self.core.persist.assert_not_called()

    def test_current_explicit_revision_bypasses_old_summary(self):
        for key, old, new in (("交付日期", "2031-01-02", "2032-03-04"),
                              ("输出格式", "Word", "纯文本"), ("任务目标", "整理初稿", "核对来源")):
            projects.append_turn(self.root, self.sid, "user", f"{key}：{old}")
            for prefix in ("修正：", ""):
                turn = self.turn(f"{prefix}{key}：{new}。请解释要求。")
                # Exercise this guard even if the task router treats a goal
                # statement as an action. Business turns already bypass it.
                turn["intent"] = "chat"
                turn["route"].update(ambiguous=False, workflow=None)
                with patch("llm.chat") as model:
                    self.events(turn)
                model.assert_not_called()
                self.assertEqual(turn["context"]["semantic"]["status"], "current_override", key)
        self.core.load.assert_not_called()

    def test_partially_rendered_summary_cannot_replace_complete_history_prefix(self):
        turn = self.turn()
        first = turn["prepared_context"]["history"][0]["content"]
        rendered = json.dumps({"coverage_complete": False, "omitted_items": 3,
                               "items": [{"text": "预算只装下部分语义片段"}]}, ensure_ascii=False)
        self.core.accept.side_effect = lambda plan, reply: {
            "text": rendered, "covered_messages": 8, "retained_coverage_complete": True,
            "skipped_messages": 0, "remaining_messages": 0,
        }
        with patch("llm.chat", return_value={"content": "validated fake reply"}):
            self.events(turn)
        request = turn["requests"][""]
        self.assertIn("预算只装下部分语义片段", json.dumps(request["messages"], ensure_ascii=False))
        self.assertEqual(request["context"].get("semantic_replaced_messages", 0), 0)
        self.assertIn(first, [row.get("content") for row in request["messages"]])

    def test_summary_citations_refer_to_rendered_items_instead_of_evicted_display_items(self):
        turn = self.turn()
        rows = turn["prepared_context"]["draft_history"]
        def ref(row):
            return {"message_id": row["id"], "start": 0, "end": 6, "quote": row["content"][:6]}
        selected, old = ref(rows[7]), ref(rows[0])
        self.core.evidence.return_value = [old] * 12 + [selected]
        rendered = json.dumps({"coverage_complete": False, "omitted_items": 12,
                               "items": [{"text": "实际注入的摘要", "evidence": [selected]}]}, ensure_ascii=False)
        self.core.accept.side_effect = lambda plan, reply: {"text": rendered}
        with patch("llm.chat", return_value={"content": "validated fake reply"}):
            self.events(turn)
        citations = [item for item in turn["requests"][""]["citations"]
                     if item["title"].startswith("语义摘要原文")]
        self.assertEqual([item["snippet"] for item in citations], [selected["quote"]])

    def test_initial_cache_persist_error_does_not_block_answer_or_replace_raw_history(self):
        turn = self.turn()
        context.set_semantic_summary(False)
        before = projects.read_full_history(self.root, self.sid)
        lease = chat_service.SessionLease(self.sid)
        self.addCleanup(lease.finish)
        captured = []
        def answer(messages):
            captured.append(messages)
            yield {"event": "token", "data": {"text": "正常回答保留"}}
            yield {"event": "done", "data": {"text": "正常回答保留"}}
        original = session_context.persist
        calls = []
        def cache_failure(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise OSError("fixture cache unavailable")
            return original(*args, **kwargs)
        with patch.object(session_context, "persist", side_effect=cache_failure):
            events = list(chat_service.stream_turn(self.root, turn, key_available=True, plain_runner=answer, lease=lease))
        self.assertEqual(len(captured), 1)
        done = [e["data"] for e in events if e["event"] == "done"]
        self.assertEqual(len(done), 1, events)
        self.assertTrue(done[0]["ok"], done)
        self.assertIn("正常回答保留", done[0]["text"])
        history = projects.read_full_history(self.root, self.sid)
        self.assertEqual(history[:len(before)], before)
        self.assertEqual(len(history), len(before) + 2)


if __name__ == "__main__":
    unittest.main()
