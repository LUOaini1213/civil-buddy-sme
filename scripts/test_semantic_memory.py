#!/usr/bin/env python3
"""Offline semantic-summary planning, provenance, persistence and failure probes."""
from __future__ import annotations

from copy import deepcopy
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
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
from demo import context
from demo import semantic_memory as memory


def history(count=8):
    return [{"id": f"message-{i}", "role": "user" if i % 2 == 0 else "assistant",
             "content": "项目目标为整理东桥的施工资料。" if i % 2 == 0 else "已列出待补资料，尚未核验。"}
            for i in range(count)]


def history_extra():
    return [{"id": "new-tail", "role": "assistant", "content": "追加原文。"}]


def answer(plan, text="历史提到整理东桥施工资料", *, source_index=0, kind="goal"):
    source = plan["sources"][source_index]
    quote = source["text"][:min(100, len(source["text"]))]
    return json.dumps({"items": [{"text": text, "kind": kind, "evidence": [{
        "message_id": source["message_id"], "start": source["start"],
        "end": source["start"] + len(quote), "quote": quote}]}]}, ensure_ascii=False)


class SemanticMemory(unittest.TestCase):
    def setUp(self):
        (ROOT / "output").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="semantic-memory-", dir=ROOT / "output")
        self.root = Path(self.temp.name)
        self.sid = "semantic-test"
        self.rows = history()
        self.estimator = patch.object(context, "_offline_encoding", return_value=None)
        self.estimator.start()

    def tearDown(self):
        self.estimator.stop()
        self.temp.cleanup()

    def plan(self, rows=None, **kwargs):
        return memory.prepare(self.root, self.sid, self.rows if rows is None else rows,
                              input_budget=kwargs.get("input_budget", 8192),
                              output_budget=kwargs.get("output_budget", 4096))

    def save(self, plan=None, text=None):
        plan = self.plan() if plan is None else plan
        return memory.persist(self.root, self.sid, memory.accept(plan, text or answer(plan)))

    def test_recent_four_and_no_eligible_history(self):
        self.assertIsNone(self.plan(history(4)))
        plan = self.plan()
        self.assertEqual([s["message_id"] for s in plan["sources"]], [r["id"] for r in self.rows[:4]])
        self.assertEqual(plan["covered_messages"], 4)
        self.assertEqual(plan["remaining_messages"], 0)
        self.assertFalse((self.root / self.sid).exists())

    def test_only_committed_unique_history_and_valid_budgets(self):
        for field, value in (("id", "current"), ("id", "client-1"), ("role", "system"), ("content", {})):
            rows = history()
            rows[0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.plan(rows)
        rows = history()
        rows[1]["id"] = rows[0]["id"]
        with self.assertRaises(ValueError):
            self.plan(rows)
        for value in (False, 0, -1, 3.5):
            with self.subTest(budget=value), self.assertRaises(ValueError):
                self.plan(input_budget=value)
        with self.assertRaises(ValueError):
            memory.prepare(self.root, self.sid, self.rows, 8000, 1000, keep_recent=3)

    def test_complete_request_budget_and_insufficient_budget(self):
        plan = self.plan(input_budget=1250)
        self.assertIsNotNone(plan)
        self.assertLessEqual(context.messages_tokens(memory.messages(plan)), 1250)
        self.assertEqual(plan["input_tokens"], context.messages_tokens(plan["messages"]))
        self.assertIsNone(self.plan(input_budget=10))
        copied = memory.messages(plan)
        copied[0]["content"] = "changed"
        self.assertNotEqual(memory.messages(plan)[0]["content"], "changed")

    def test_semantic_paraphrase_keeps_grounded_user_and_assistant_unverified(self):
        plan = self.plan()
        user = memory.accept(plan, answer(plan))["cache"]["segments"][-1]["items"][0]
        self.assertEqual(user["trust"], "user_stated_unverified")
        self.assertFalse(user["verified"])
        assistant = memory.accept(plan, answer(plan, "助手曾表示已整理资料，但仍未核验", source_index=1, kind="result"))
        self.assertEqual(assistant["cache"]["segments"][-1]["items"][0]["trust"], "assistant_unverified")
        self.assertFalse(assistant["cache"]["authorizes_actions"])

    def test_rejects_forged_source_range_quote_and_boolean_offsets(self):
        plan = self.plan()
        for key, value in (("message_id", "foreign-task"), ("start", -1), ("start", True),
                           ("end", 9999), ("quote", "原文没有的内容")):
            reply = json.loads(answer(plan))
            reply["items"][0]["evidence"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                memory.accept(plan, json.dumps(reply, ensure_ascii=False))

    def test_numbers_must_be_grounded_in_same_item(self):
        self.rows[0]["content"] = "工期60天，现场机械三台。"
        plan = self.plan()
        memory.accept(plan, answer(plan, "用户提到60天和三台，待核。"))
        for claim in ("用户提到61天。", "现场机械七台。", "数量600台。", "金额-60元。"):
            with self.subTest(claim=claim), self.assertRaises(ValueError):
                memory.accept(plan, answer(plan, claim))

    def test_numeric_quotes_must_cover_whole_original_value(self):
        for original, fragment in (("12", "1"), ("12", "2"), ("1.25", "1"),
                                   ("1.25", "25"), ("-12", "12"), ("1e3", "1"), ("1e-3", "3")):
            self.rows[0]["content"] = "输入数值为" + original + "米，待核。"
            plan = self.plan()
            start = self.rows[0]["content"].index(fragment)
            reply = {"items": [{"kind": "constraint", "text": "历史输入为" + fragment + "米。",
                                "evidence": [{"message_id": self.rows[0]["id"], "start": start,
                                              "end": start + len(fragment), "quote": fragment}]}]}
            with self.subTest(original=original, fragment=fragment), self.assertRaisesRegex(ValueError, "截断原文数值"):
                memory.accept(plan, json.dumps(reply, ensure_ascii=False))
            reply["items"][0]["text"] = "历史输入为" + original + "米。"
            start = self.rows[0]["content"].index(original)
            reply["items"][0]["evidence"][0].update(start=start, end=start + len(original), quote=original)
            memory.accept(plan, json.dumps(reply, ensure_ascii=False))

    def test_plan_cut_avoids_number_when_whole_token_can_fit_next_request(self):
        row = {"id": "number-boundary", "role": "user", "content": "a" * 500 + "1234567890米"}
        self.rows = [row] + history(4)
        desired_cut = 505  # A naïve token budget lands inside the ten-digit number.
        source = {"message_id": row["id"], "role": row["role"], "start": 0,
                  "end": desired_cut, "text": row["content"][:desired_cut]}
        budget = context.messages_tokens(memory._messages([source]))
        plan = self.plan(input_budget=budget)
        self.assertEqual(plan["sources"][0]["end"], 500)
        self.assertEqual(plan["sources"][0]["text"], "a" * 500)
        self.save(plan, '{"items":[]}')
        plan = self.plan(input_budget=budget)
        self.assertTrue(plan["sources"][0]["text"].startswith("1234567890"))
        self.assertNotIn("number_bounds", json.dumps(memory.messages(plan)))

    def test_giant_number_spanning_plans_cannot_supply_partial_number_quotes(self):
        # Even when no budget can fit a single numeric literal, the private
        # original boundaries prevent both prefix and suffix misinterpretation.
        self.rows = [{"id": "huge-number", "role": "user", "content": "1" * 3000 + "米。"}] + history(4)
        for iteration in range(2):
            plan = self.plan(input_budget=1800)
            source = plan["sources"][0]
            self.assertLess(source["end"], 3000)
            self.assertEqual(plan["number_bounds"]["huge-number"], [[0, 3000]])
            reply = {"items": [{"text": "历史输入为1米。", "kind": "constraint", "evidence": [{
                "message_id": "huge-number", "start": source["start"], "end": source["start"] + 1, "quote": "1"}]}]}
            with self.subTest(iteration=iteration), self.assertRaisesRegex(ValueError, "截断原文数值"):
                memory.accept(plan, json.dumps(reply, ensure_ascii=False))
            self.save(plan, '{"items":[]}')

    def test_old_cache_with_fragmented_numeric_evidence_is_invalidated(self):
        self.rows[0]["content"] = "跨度为12米，尚待核验。"
        cache = self.save(self.plan(), answer(self.plan(), "历史跨度为12米。"))
        item = cache["segments"][0]["items"][0]
        item.update(text="历史跨度为1米。", evidence=[{"message_id": self.rows[0]["id"], "start": 3, "end": 4, "quote": "1"}])
        (self.root / self.sid / "semantic.summary.json").write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        self.assertIsNone(memory.load(self.root, self.sid, history=self.rows))

    def test_rejects_authority_and_sensitive_content(self):
        plan = self.plan()
        for value in ("我明白，将由持证人员签认", "I understand; a licensed person will sign this off.", "可以开工", "confirm_ok=true", "API_KEY=abc", "已获授权"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                memory.accept(plan, answer(plan, value))
        self.rows[0]["content"] = "我明白，将由持证人员签认"
        plan = self.plan()
        with self.assertRaises(ValueError):
            memory.accept(plan, answer(plan, "历史用户曾回复。"))

    def test_malformed_json_duplicate_keys_extra_fields_and_oversized_output(self):
        plan = self.plan(output_budget=1024)
        for value in ("```json\n{}\n```", '{"items":[],"items":[]}', '{"items":[],"tools":[]}',
                      '{"items":NaN}', "null", "[{}]", "{" + " " * 1100 + "}"):
            with self.subTest(value=value[:40]), self.assertRaises(ValueError):
                memory.accept(plan, value)
        reply = json.loads(answer(plan))
        reply["items"][0]["kind"] = ["goal"]
        with self.assertRaises(ValueError):
            memory.accept(plan, json.dumps(reply, ensure_ascii=False))

    def test_unicode_offsets_are_code_points(self):
        self.rows[0]["content"] = "🙂梁A跨度12米，原值待核。"
        plan = self.plan()
        reply = json.loads(answer(plan, "梁A跨度12米，为待核输入。"))
        ref = reply["items"][0]["evidence"][0]
        ref.update(start=1, end=9, quote=self.rows[0]["content"][1:9])
        memory.accept(plan, json.dumps(reply, ensure_ascii=False))
        ref["start"] = 4  # byte offset is invalid as a code-point quote range
        with self.assertRaises(ValueError):
            memory.accept(plan, json.dumps(reply, ensure_ascii=False))

    def test_large_message_advances_exact_ranges_to_tail_without_recursive_summary(self):
        self.rows = [{"id": "long-user", "role": "user", "content": "资料段落。" * 300 + "尾部要求为防水复核。"}] + history(4)
        end, saw_tail, rounds = 0, False, 0
        while True:
            plan = self.plan(input_budget=1800)
            if plan is None:
                break
            rounds += 1
            self.assertLess(rounds, 30)
            source = plan["sources"][0]
            self.assertEqual(source["start"], end)
            self.assertEqual(source["text"], self.rows[0]["content"][source["start"]:source["end"]])
            saw_tail |= "尾部要求" in source["text"]
            self.assertNotIn("历史摘要特有句子", plan["messages"][-1]["content"])
            end = source["end"]
            cache = self.save(plan, answer(plan, "历史摘要特有句子"))
            self.assertIsNotNone(memory.load(self.root, self.sid, history=self.rows))
        self.assertGreater(rounds, 1)
        self.assertTrue(saw_tail)
        self.assertEqual(end, len(self.rows[0]["content"]))
        self.assertEqual(cache["covered_messages"], 1)
        self.assertTrue(cache["retained_coverage_complete"])

    def test_incremental_appends_only_newly_eligible_raw_records(self):
        self.save()
        self.rows += [{"id": "new-1", "role": "user", "content": "请继续整理。"},
                      {"id": "new-2", "role": "assistant", "content": "等待资料。"}]
        plan = self.plan()
        self.assertEqual([s["message_id"] for s in plan["sources"]], ["message-4", "message-5"])
        self.assertEqual(plan["cache"]["covered_messages"], 4)
        self.assertEqual(plan["covered_messages"], 6)
        self.assertNotIn("历史提到整理东桥施工资料", plan["messages"][-1]["content"])

    def test_changed_removed_reordered_history_invalidates(self):
        self.save()
        for change in (lambda r: r[0].update(content="已变更的原始资料"), lambda r: r.pop(0), lambda r: r.reverse()):
            rows = deepcopy(self.rows)
            change(rows)
            self.assertIsNone(memory.load(self.root, self.sid, history=rows))
        self.assertIsNotNone(memory.load(self.root, self.sid, history=self.rows))

    def test_recent_correction_suppresses_cache_and_restart_marks_skipped_history(self):
        self.save()
        self.rows.append({"id": "correction", "role": "user", "content": "更正：项目为西桥，旧名称有误。"})
        self.assertIsNone(memory.load(self.root, self.sid, history=self.rows))
        self.assertIsNone(self.plan())
        self.rows += [{"id": f"tail-{i}", "role": "assistant", "content": "新消息"} for i in range(4)]
        plan = self.plan()
        self.assertEqual(plan["sources"][0]["message_id"], "correction")
        cache = self.save(plan, answer(plan, "更正后用户称项目为西桥。"))
        self.assertEqual(cache["skipped_messages"], 8)
        self.assertFalse(cache["retained_coverage_complete"])
        self.assertIsNotNone(memory.load(self.root, self.sid, history=self.rows))

    def test_uncovered_long_tail_correction_prevents_stale_front_summary(self):
        self.rows = [{"id": "long-user", "role": "user", "content": "项目原名东桥。" * 400 + "更正：项目为西桥。" + "待核原始资料。" * 300}] + history(4)
        plan = self.plan(input_budget=1800)
        self.assertGreater(plan["sources"][0]["start"], 1000)
        self.assertTrue(plan["sources"][0]["text"].startswith("更正"))
        cache = self.save(plan, answer(plan, "用户更正项目为西桥。"))
        self.assertGreater(cache["skipped_chars"], 1000)
        self.assertIsNotNone(memory.load(self.root, self.sid, history=self.rows))
        next_plan = self.plan(input_budget=1800)
        self.assertEqual(next_plan["range_start"], plan["range_end"])

    def test_all_supported_correction_words_stay_invalid_in_later_turns(self):
        self.save()
        for word in ("修正", "更新", "改成", "调整为", "作废", "撤销", "取消此前"):
            rows = self.rows + [{"id": "latest", "role": "user", "content": word + "：项目为西桥。"},
                                {"id": "later", "role": "assistant", "content": "继续整理"}]
            with self.subTest(word=word):
                self.assertIsNone(memory.load(self.root, self.sid, history=rows))
                self.assertIsNone(self.plan(rows))

    def test_same_key_new_value_without_correction_words_invalidates(self):
        self.rows[0]["content"] = "截止日期：月底前。"
        self.save(self.plan(), answer(self.plan(), "用户提到月底前交付。"))
        self.rows += [{"id": "replacement", "role": "user", "content": "截止日期：本周五。"},
                      {"id": "next-turn", "role": "assistant", "content": "继续整理。"}]
        self.assertIsNone(memory.load(self.root, self.sid, history=self.rows))
        self.assertIsNone(self.plan())
        self.rows += [{"id": f"later-{i}", "role": "assistant", "content": "继续。"} for i in range(3)]
        plan = self.plan()
        self.assertEqual(plan["sources"][0]["message_id"], "replacement")
        self.assertNotIn("月底前", plan["messages"][-1]["content"])

    def test_long_field_replacement_invalidates_before_and_after_restart(self):
        self.rows[0]["content"] = "任务目标：整理东桥施工资料。"
        self.save(self.plan(), answer(self.plan(), "旧目标是整理东桥施工资料。"))
        replacement = "任务目标：" + "只讨论西桥施工资料" * 500 + "。"
        self.assertGreater(len(replacement), memory.task_memory.MAX_EXCERPT)
        self.assertTrue(memory.current_revision(self.rows, replacement))
        self.rows += [{"id": "long-goal", "role": "user", "content": replacement},
                      {"id": "later-turn", "role": "assistant", "content": "待核。"}]
        self.assertEqual(memory.task_memory.build(self.rows)["stats"]["omitted_long_excerpts"], 1)
        self.assertIsNone(memory.load(self.root, self.sid, history=self.rows))
        history_file = self.root / "long-history.json"
        history_file.write_text(json.dumps(self.rows, ensure_ascii=False), encoding="utf-8")
        code = ("import json,sys; from pathlib import Path; from demo import semantic_memory as m; "
                "r=Path(sys.argv[1]); h=json.loads((r/'long-history.json').read_text(encoding='utf-8')); "
                "print(m.load(r,sys.argv[2],history=h) is None)")
        result = subprocess.run([sys.executable, "-c", code, str(self.root), self.sid], cwd=ROOT,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "True")
        self.rows += [{"id": f"mature-{i}", "role": "assistant", "content": "继续。"} for i in range(3)]
        plan = self.plan(input_budget=1800)
        self.assertEqual(plan["sources"][0]["message_id"], "long-goal")
        cache = self.save(plan, answer(plan, "用户给出了新的西桥资料目标。"))
        self.assertTrue(cache["pending_revision"])
        self.assertEqual(memory.render(cache), "")
        self.assertEqual(self.plan(input_budget=1800)["range_start"], plan["range_end"])

    def test_current_revision_covers_all_fields_without_reclassifying_questions_or_repeats(self):
        for key in ("任务目标", "输出格式", "截止日期"):
            rows = [{"id": "original", "role": "user", "content": key + "：甲。"}]
            with self.subTest(key=key):
                self.assertTrue(memory.current_revision(rows, key + "：乙" * 4500 + "。"))
                self.assertFalse(memory.current_revision(rows, key + "：甲。"))
                self.assertFalse(memory.current_revision(rows, key + "：乙？"))
        self.assertTrue(memory.current_revision([], "以周五交付为准。"))
        self.assertFalse(memory.current_revision([], "请解释施工资料的常见问题？"))
        with self.assertRaises(ValueError):
            memory.current_revision(self.rows, None)

    def test_partial_long_replacement_progresses_but_not_rendered_until_covered(self):
        self.rows[0]["content"] = "任务目标：原先的目标。"
        self.rows += [{"id": "replacement", "role": "user", "content": "任务目标：" + "新的工作要求" * 250 + "。"}]
        self.rows += [{"id": f"later-{i}", "role": "assistant", "content": "继续。"} for i in range(4)]
        plan = self.plan(input_budget=1800)
        cache = self.save(plan, answer(plan, "用户提供了新的任务目标。"))
        self.assertTrue(cache["pending_revision"])
        self.assertEqual(memory.render(cache), "")
        self.assertIsNone(memory.load(self.root, self.sid, history=self.rows))
        next_plan = self.plan(input_budget=1800)
        self.assertEqual(next_plan["range_start"], plan["range_end"])

    def test_malformed_segment_ranges_and_coverage_flags_do_not_fake_completeness(self):
        cached = self.save()
        path = self.root / self.sid / "semantic.summary.json"
        mutations = [lambda c: c["segments"][0].update(range_end={"index": 2, "offset": 0}),
                     lambda c: c["segments"][0].update(range_start={"index": 3, "offset": 0}),
                     lambda c: c.update(segments=[]),
                     lambda c: c.update(skipped_messages=-1),
                     lambda c: c.update(skipped_messages=True)]
        for change in mutations:
            value = deepcopy(cached)
            change(value)
            path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            self.assertIsNone(memory.load(self.root, self.sid, history=self.rows))
        cached.update(covered_messages=999, remaining_messages=0, retained_coverage_complete=True)
        path.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")
        loaded = memory.load(self.root, self.sid, history=self.rows + history_extra())
        self.assertEqual(loaded["covered_messages"], 4)
        self.assertEqual(loaded["remaining_messages"], 1)
        self.assertFalse(loaded["retained_coverage_complete"])

    def test_safe_sessions_cross_session_root_and_mutated_accept_rejected(self):
        for sid in ("../escape", "COM1", "_index", "bad/id"):
            with self.subTest(sid=sid), self.assertRaises(ValueError):
                memory.prepare(self.root, sid, self.rows, 8000, 1000)
        plan = self.plan()
        accepted = memory.accept(plan, answer(plan))
        with self.assertRaises(ValueError):
            memory.persist(self.root, "another-session", accepted)
        with self.assertRaises(ValueError):
            memory.persist(self.root / "another-root", self.sid, accepted)
        accepted["cache"]["verified"] = True
        with self.assertRaises(ValueError):
            memory.persist(self.root, self.sid, accepted)
        plan["sources"][0]["text"] = "forged"
        with self.assertRaises(ValueError):
            memory.accept(plan, answer(plan))

    def test_stale_concurrent_plan_cannot_overwrite_and_persist_not_arbitrary_cache(self):
        first, second = self.plan(), self.plan()
        with self.assertRaises(ValueError):
            memory.persist(self.root, self.sid, first)
        cached = self.save(first)
        with self.assertRaises(ValueError):
            memory.persist(self.root, self.sid, memory.accept(second, answer(second)))
        with self.assertRaises(ValueError):
            memory.persist(self.root, self.sid, cached)
        self.assertEqual(memory.load(self.root, self.sid, history=self.rows)["segments"], cached["segments"])

    def test_real_64kb_limit_with_multiple_large_validated_segments(self):
        for turn in range(5):
            plan = self.plan(output_budget=40_000)
            value = json.loads(answer(plan, "历史陈述" + "资料" * 350))
            value["items"] *= 12
            cache = self.save(plan, json.dumps(value, ensure_ascii=False))
            if turn < 4:
                self.rows += [{"id": f"large-{turn}", "role": "user", "content": "新增待核资料。"}]
        self.assertLessEqual((self.root / self.sid / "semantic.summary.json").stat().st_size, 65_536)
        self.assertGreater(cache["evicted_segments"], 0)
        self.assertEqual(cache["covered_messages"], 8)

    def test_atomic_replace_failure_preserves_previous_cache_and_cleans_temp(self):
        self.save()
        target = self.root / self.sid / "semantic.summary.json"
        before = target.read_bytes()
        self.rows += [{"id": "append-a", "role": "user", "content": "继续"}]
        plan = self.plan()
        accepted = memory.accept(plan, answer(plan))
        with patch.object(Path, "replace", side_effect=OSError("injected replace failure")), self.assertRaises(OSError):
            memory.persist(self.root, self.sid, accepted)
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(list(target.parent.glob("*.tmp")), [])

    def test_corrupted_or_copied_cache_never_reused(self):
        self.save()
        target = self.root / self.sid / "semantic.summary.json"
        other = self.root / "other-session" / target.name
        other.parent.mkdir()
        other.write_bytes(target.read_bytes())
        self.assertIsNone(memory.load(self.root, "other-session", history=self.rows))
        for value in (b"{corrupt", b"x" * (memory.MAX_CACHE_BYTES + 1)):
            target.write_bytes(value)
            self.assertIsNone(memory.load(self.root, self.sid, history=self.rows))

    def test_restart_reads_persisted_provenance_without_process_seal(self):
        self.save()
        history_file = self.root / "test-history.json"
        history_file.write_text(json.dumps(self.rows, ensure_ascii=False), encoding="utf-8")
        code = ("import json,sys; from pathlib import Path; from demo import semantic_memory as m; "
                "r=Path(sys.argv[1]); h=json.loads((r/'test-history.json').read_text(encoding='utf-8')); "
                "c=m.load(r,sys.argv[2],history=h); print(json.dumps({'covered':c['covered_messages'],'rendered':bool(m.render(c))}))")
        result = subprocess.run([sys.executable, "-c", code, str(self.root), self.sid], cwd=ROOT,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"covered": 4, "rendered": True})

    def test_cache_eviction_caps_bytes_preserves_cursor_and_reports_partial(self):
        # Real incremental commits with a small cap exercise the same bounded
        # eviction algorithm without generating hundreds of irrelevant records.
        with patch.object(memory, "MAX_CACHE_BYTES", 2700):
            for turn in range(6):
                plan = self.plan()
                cache = self.save(plan)
                if turn < 5:
                    self.rows.append({"id": f"extra-{turn}", "role": "user", "content": "追加资料仍待核验。"})
            path = self.root / self.sid / "semantic.summary.json"
            self.assertLessEqual(path.stat().st_size, 2700)
            self.assertGreater(cache["evicted_segments"], 0)
            self.assertEqual(cache["cursor"]["index"], 9)
            self.assertFalse(cache["retained_coverage_complete"])
            self.assertGreater(json.loads(memory.render(cache))["evicted_segments"], 0)

    def test_render_never_cuts_json_or_reference_and_evidence_deduplicates(self):
        plan = self.plan()
        value = json.loads(answer(plan))
        value["items"] *= 3
        cache = self.save(plan, json.dumps(value, ensure_ascii=False))
        self.assertEqual(len(memory.evidence(cache)), 1)
        self.assertEqual(memory.render(cache, max_chars=10), "")
        for budget in (500, 650, 1200, 6000):
            rendered = memory.render(cache, max_chars=budget)
            self.assertLessEqual(len(rendered), budget)
            if rendered:
                data = json.loads(rendered)
                self.assertFalse(data["authorizes_actions"])
                for item in data["items"]:
                    self.assertEqual(item["evidence"], value["items"][0]["evidence"])

    def test_empty_summary_can_advance_without_fabricated_render(self):
        plan = self.plan()
        cache = self.save(plan, '{"items":[]}')
        self.assertEqual(cache["covered_messages"], 4)
        self.assertEqual(memory.render(cache), "")
        self.assertEqual(memory.evidence(cache), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
