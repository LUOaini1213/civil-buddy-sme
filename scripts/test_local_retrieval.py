#!/usr/bin/env python3
"""Full-history and actual SQLite retrieval regressions, entirely offline."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from demo import local_retrieval as rag
from demo import projects
from packing_assistant.filesystem import native_path


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="civil-local-retrieval-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sid = "sess-rag"

    def history(self, content, identifier="msg-one"):
        return {"id": identifier, "role": "user", "content": content, "ts": 123}

    def attachment(self, content, identifier="file-one", name="会议资料.txt"):
        return {"id": identifier, "name": name, "text": content}

    def sync(self, history=None, attachments=None):
        return rag.sync_session(self.root, self.sid, history or [], attachments or [])

    def assert_offsets(self, hits):
        self.assertTrue(hits)
        for hit in hits:
            full = rag.source(self.root, self.sid, hit["source_id"])
            self.assertEqual(hit["text"], full["text"][hit["start"]:hit["end"]])
            self.assertEqual(full["end"], len(full["text"]))

    def test_long_text_and_more_than_200_messages_remain_complete(self):
        first = "铺垫文字。" * 2200 + "首轮档案：玄武岩试桩复核编号XYZ987。"
        projects.append_turn(self.root, self.sid, "user", first)
        for number in range(205):
            projects.append_turn(self.root, self.sid, "assistant", f"日常消息 {number}")
        last = "末轮资料" * 2300 + "末尾完整标记"
        projects.append_turn(self.root, self.sid, "user", last)
        full = projects.read_full_history(self.root, self.sid)
        self.assertEqual(len(full), 207)
        self.assertEqual(full[0]["content"], first)
        self.assertEqual(full[-1]["content"], last)
        self.assertEqual(len({h["id"] for h in full}), 207)
        self.assertEqual(projects.read_full_history(self.root, self.sid), full)
        ui = projects.session_detail(self.root, self.sid)
        self.assertEqual(len(ui["transcript"]), 200)
        self.assertTrue(ui["truncated"])
        self.assertTrue(all(set(h) == {"ts", "role", "text"} for h in ui["transcript"]))
        self.assertLessEqual(len(ui["transcript"][-1]["text"].encode("utf-8")), 8000)
        self.assertNotIn("XYZ987", "".join(h["text"] for h in ui["transcript"]))
        self.sync(full)
        hits = rag.search(self.root, self.sid, "XYZ987")
        self.assertEqual(hits[0]["message_id"], full[0]["id"])
        self.assertGreater(hits[0]["start"], 8000)
        self.assertIn("XYZ987", hits[0]["text"])
        self.assert_offsets(hits)

    def test_legacy_messages_get_stable_distinct_ids_without_rewrite(self):
        path = self.root / self.sid / "transcript.jsonl"
        path.parent.mkdir()
        row = {"ts": 1, "role": "user", "text": "旧版完整记录" * 1700}
        original = ((json.dumps(row, ensure_ascii=False) + "\n") * 2).encode("utf-8")
        path.write_bytes(original)
        old = projects.read_full_history(self.root, self.sid)
        self.assertNotEqual(old[0]["id"], old[1]["id"])
        self.assertTrue(all(h["id"].startswith("legacy-") for h in old))
        self.assertEqual(old[0]["content"], row["text"])
        self.assertEqual(path.read_bytes(), original)
        projects.append_turn(self.root, self.sid, "assistant", "新增")
        self.assertEqual(projects.read_full_history(self.root, self.sid)[:2], old)
        self.assertTrue(path.read_bytes().startswith(original))

    def test_duplicate_persisted_ids_do_not_overwrite_index_sources(self):
        path = self.root / self.sid / "transcript.jsonl"
        path.parent.mkdir()
        path.write_text("".join(json.dumps({"id": "same", "ts": 1, "role": "user", "text": value}) + "\n"
                                for value in ("firstfact", "secondfact")), encoding="utf-8")
        rows = projects.read_full_history(self.root, self.sid)
        self.assertEqual(len({r["id"] for r in rows}), 2)
        self.sync(rows)
        self.assertNotEqual(rag.search(self.root, self.sid, "firstfact")[0]["source_id"],
                            rag.search(self.root, self.sid, "secondfact")[0]["source_id"])

    def test_interrupted_legacy_record_is_preserved_and_new_turn_readable(self):
        path = self.root / self.sid / "transcript.jsonl"
        path.parent.mkdir()
        raw = b'{"ts":1,"role":"user","text":"kept"}\ninvalid\n{"text":'
        path.write_bytes(raw)
        projects.append_turn(self.root, self.sid, "assistant", "恢复记录")
        full = projects.read_full_history(self.root, self.sid)
        self.assertEqual([h["content"] for h in full], ["kept", "恢复记录"])
        self.assertTrue(path.read_bytes().startswith(raw + b"\n"))
        self.assertTrue(projects.session_detail(self.root, self.sid)["truncated"])

    def test_missing_history_does_not_create_a_session(self):
        self.assertEqual(projects.read_full_history(self.root, self.sid), [])
        self.assertEqual(rag.search(self.root, self.sid, "资料"), [])
        self.assertIsNone(rag.source(self.root, self.sid, "src-" + "0" * 32))
        self.assertFalse((self.root / self.sid).exists())

    def assert_read_only_index(self):
        path = rag._path(self.root, self.sid)
        original = path.read_bytes()
        with closing(rag._connect(path, write=False)) as connection:
            self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("CREATE TABLE forbidden_write(value TEXT)")
            # Prove the URI itself is read-only, independently of query_only.
            connection.execute("PRAGMA query_only=OFF")
            with self.assertRaises(sqlite3.OperationalError) as denied:
                connection.execute("CREATE TABLE forbidden_write(value TEXT)")
            self.assertEqual(denied.exception.sqlite_errorcode & 0xFF, sqlite3.SQLITE_READONLY)
        self.assertEqual(path.read_bytes(), original)
        missing = path.with_name("not-created.sqlite3")
        with self.assertRaises(sqlite3.OperationalError):
            rag._connect(missing, write=False)
        self.assertFalse(missing.exists(), "Read-only connection must not create a missing index")

    def test_read_only_uri_escapes_spaces_unicode_and_query_delimiters(self):
        self.root = self.root / "资料 # %3f with spaces"
        text = "escapedpathkeyword 原文未改动"
        self.sync(attachments=[self.attachment(text)])
        hits = rag.search(self.root, self.sid, "escapedpathkeyword", ["file-one"])
        self.assertEqual(hits[0]["text"], text)
        self.assert_offsets(hits)
        self.assert_read_only_index()

    @unittest.skipUnless(os.name == "nt", "Windows extended-path regression")
    def test_read_only_uri_handles_real_windows_extended_long_path(self):
        # Both I/O and cleanup use the same extended spelling; no global Windows
        # policy changes or ignored cleanup failures are needed.
        temp = tempfile.TemporaryDirectory(prefix="extended-index-", dir=native_path(self.root))
        self.addCleanup(temp.cleanup)
        self.root = (Path(temp.name) / ("private-state-" + "x" * 40) / "accounts"
                     / "acceptance_alice" / "projects" / ("a" * 64) / "domains")
        path = rag._path(self.root, self.sid)
        self.assertGreater(len(str(path)), 260)
        self.assertTrue(str(path).startswith("\\\\?\\"))
        text = "extendedpathkeyword 完整检索正文"
        self.sync(attachments=[self.attachment(text)])
        hits = rag.search(self.root, self.sid, "extendedpathkeyword", ["file-one"])
        self.assertEqual(hits[0]["text"], text)
        self.assert_offsets(hits)
        self.assert_read_only_index()

    def test_concurrent_append_keeps_full_text_and_unique_ids(self):
        texts = [f"编号{i}：" + "完整原文" * 2200 for i in range(24)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda text: projects.append_turn(self.root, self.sid, "user", text), texts))
        rows = projects.read_full_history(self.root, self.sid)
        self.assertEqual({r["content"] for r in rows}, set(texts))
        self.assertEqual(len({r["id"] for r in rows}), len(texts))

    def test_full_history_capacity_errors_do_not_silently_trim_or_modify(self):
        projects.append_turn(self.root, self.sid, "user", "already kept")
        path = self.root / self.sid / "transcript.jsonl"
        raw = path.read_bytes()
        with patch.object(projects, "HISTORY_MESSAGE_MAX_BYTES", 2), self.assertRaises(ValueError):
            projects.append_turn(self.root, self.sid, "user", "too big")
        with patch.object(projects, "HISTORY_MAX_BYTES", 2), self.assertRaises(ValueError):
            projects.read_full_history(self.root, self.sid)
        with patch.object(projects, "HISTORY_MAX_MESSAGES", 0), self.assertRaises(ValueError):
            projects.read_full_history(self.root, self.sid)
        self.assertEqual(path.read_bytes(), raw)

    def test_chinese_english_and_exact_unicode_offsets_beyond_prefix(self):
        text = "普通内容。\r\n" * 1700 + "🌉\x00资料：龙门吊例检，ABC987；a|b &lt; x<10。\r\n终章"
        self.sync(attachments=[self.attachment(text)])
        for query in ("龙门吊例检", "吊", "abc987"):
            hits = rag.search(self.root, self.sid, query, ["file-one"])
            self.assertEqual(hits[0]["attachment_id"], "file-one")
            self.assertGreater(hits[0]["start"], 8000)
            self.assertIn("龙门吊例检", hits[0]["text"])
            self.assert_offsets(hits)
        full = rag.source(self.root, self.sid, hits[0]["source_id"])
        self.assertEqual(full["text"], text)

    def test_only_selected_attachments_and_current_session_are_searchable(self):
        history = [self.history("共同关键词 共用历史")]
        files = [self.attachment("共同关键词 甲秘密", "file-a"),
                 self.attachment("共同关键词 乙秘密", "file-b")]
        self.sync(history, files)
        for selected in (None, []):
            hits = rag.search(self.root, self.sid, "共同关键词", selected)
            self.assertEqual({h["kind"] for h in hits}, {"history"})
        hits = rag.search(self.root, self.sid, "共同关键词", ["file-b"])
        self.assertEqual({h.get("attachment_id") for h in hits if h["kind"] == "attachment"}, {"file-b"})
        rag.sync_session(self.root, "other-session", [], [self.attachment("共同关键词 他人资料", "file-b")])
        foreign = rag.search(self.root, "other-session", "共同关键词", ["file-b"])[0]
        self.assertIsNone(rag.source(self.root, self.sid, foreign["source_id"]))
        self.assertNotIn("他人资料", "".join(h["text"] for h in hits))
        self.assertEqual(rag.search(self.root, self.sid, "共同关键词", ["unknown"], kind="attachment"), [])

    def test_empty_query_fallback_keeps_each_selected_first_chunk(self):
        self.sync([self.history("填充历史" * 3000)],
                  [self.attachment("甲首段" * 500, "file-a"), self.attachment("乙首段" * 500, "file-b")])
        hits = rag.search(self.root, self.sid, "", ["file-b", "file-a"], limit=2)
        self.assertEqual([h["attachment_id"] for h in hits], ["file-b", "file-a"])
        self.assertTrue(all(h["start"] == 0 for h in hits))
        self.assert_offsets(hits)
        self.assertEqual(rag.search(self.root, self.sid, "", []), [])
        self.assertEqual(rag.search(self.root, self.sid, "", ["file-a"], kind="history"), [])
        self.assertEqual(len(rag.search(self.root, self.sid, "", ["file-a"], limit=1)), 1)

    def test_kind_filter_does_not_let_history_crowd_out_attachments(self):
        self.sync([self.history("sharedword " * 2500)],
                  [self.attachment("sharedword attachment fact")])
        hits = rag.search(self.root, self.sid, "sharedword", ["file-one"], limit=1, kind="attachment")
        self.assertEqual(hits[0]["kind"], "attachment")
        hits = rag.search(self.root, self.sid, "sharedword", ["file-one"], kind="history")
        self.assertTrue(all(h["kind"] == "history" for h in hits))

    def test_chinese_question_excludes_common_single_character_noise(self):
        history = [self.history("交付日期：2026年10月20日", "fact")]
        history += [self.history(f"这是测试对话，第{i}轮", f"noise-{i}") for i in range(300)]
        self.sync(history)
        hits = rag.search(self.root, self.sid, "交付日期是什么？")
        self.assertEqual([h["message_id"] for h in hits], ["fact"])
        self.assertIn("2026年10月20日", hits[0]["text"])
        self.assertTrue(rag.search(self.root, self.sid, "是"))
        self.assertEqual(rag.search(self.root, self.sid, "竣工验收怎么做？"), [])

    def test_exact_repeated_question_does_not_outrank_answer_facts(self):
        query = "交付日期是什么？"
        history = [self.history("交付日期：2026年10月20日", "fact"),
                   self.history(query, "repeat-one"), self.history(query, "repeat-two"),
                   self.history("交付日期是什么？补充：业主要求十月底前完成。", "question-with-fact")]
        history += [self.history(query, f"repeat-{i}") for i in range(260)]
        self.sync(history)
        hits = rag.search(self.root, self.sid, query, limit=2)
        self.assertEqual({h["message_id"] for h in hits}, {"fact", "question-with-fact"})
        self.assertTrue(all(h["score"] > 0 for h in hits))

    def test_sync_is_idempotent_updates_stable_sources_and_removes_omissions(self):
        a, b = self.attachment("oldkeyword", "file-a"), self.attachment("removedkeyword", "file-b")
        first = self.sync(attachments=[a, b])
        hit = rag.search(self.root, self.sid, "", ["file-a"])[0]
        unchanged = self.sync(attachments=[a, b])
        self.assertEqual(unchanged["updated"], 0)
        self.assertEqual(unchanged["unchanged"], 2)
        self.assertEqual(unchanged["chunks"], first["chunks"])
        changed = self.sync(attachments=[self.attachment("newkeyword", "file-a")])
        self.assertEqual((changed["updated"], changed["removed"], changed["sources"]), (1, 1, 1))
        self.assertEqual(rag.source(self.root, self.sid, hit["source_id"])["text"], "newkeyword")
        self.assertEqual(rag.search(self.root, self.sid, "oldkeyword", ["file-a"]), [])
        self.assertEqual(rag.search(self.root, self.sid, "removedkeyword", ["file-b"]), [])

    def test_schema_change_and_deleted_cache_rebuild_preserve_source_ids(self):
        files = [self.attachment("migrationkeyword")]
        self.sync(attachments=files)
        source_id = rag.search(self.root, self.sid, "", ["file-one"])[0]["source_id"]
        path = self.root / self.sid / "local-retrieval.sqlite3"
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("PRAGMA user_version=1")
        result = self.sync(attachments=files)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(rag.search(self.root, self.sid, "migrationkeyword", ["file-one"])[0]["source_id"], source_id)
        path.unlink()
        self.sync(attachments=files)
        self.assertEqual(rag.search(self.root, self.sid, "", ["file-one"])[0]["source_id"], source_id)

    def test_corrupt_cache_is_rebuilt_without_touching_canonical_files(self):
        projects.append_turn(self.root, self.sid, "user", "完整历史原文")
        history = projects.read_full_history(self.root, self.sid)
        self.sync(history, [self.attachment("完整附件原文")])
        source_id = rag.search(self.root, self.sid, "", ["file-one"])[0]["source_id"]
        transcript = self.root / self.sid / "transcript.jsonl"
        raw = transcript.read_bytes()
        original = self.root / self.sid / "attachment-original.txt"
        original.write_bytes(b"original attachment bytes")
        path = self.root / self.sid / "local-retrieval.sqlite3"
        path.write_bytes(b"corrupt database, not sqlite")
        with self.assertRaises(rag.RetrievalError):
            rag.search(self.root, self.sid, "原文", ["file-one"])
        result = self.sync(history, [self.attachment("完整附件原文")])
        self.assertTrue(result["rebuilt"])
        self.assertEqual(rag.source(self.root, self.sid, source_id)["text"], "完整附件原文")
        self.assertEqual(transcript.read_bytes(), raw)
        self.assertEqual(original.read_bytes(), b"original attachment bytes")

    def test_failed_update_rolls_back_without_losing_prior_sources(self):
        self.sync(attachments=[self.attachment("originalkeyword")])
        with patch.object(rag, "_tokens", side_effect=RuntimeError("injected indexing failure")):
            with self.assertRaises(RuntimeError):
                self.sync(attachments=[self.attachment("changedkeyword")])
        hits = rag.search(self.root, self.sid, "originalkeyword", ["file-one"])
        self.assertEqual(hits[0]["text"], "originalkeyword")
        self.assertEqual(rag.search(self.root, self.sid, "changedkeyword", ["file-one"]), [])

    def test_limits_validate_before_mutation_and_leave_index_readable(self):
        self.sync(attachments=[self.attachment("retainedkeyword")])
        cases = (("MAX_SOURCE_CHARS", 1), ("MAX_TOTAL_CHARS", 1), ("MAX_CHUNKS", 0), ("MAX_SOURCES", 0))
        for constant, value in cases:
            with self.subTest(constant=constant), patch.object(rag, constant, value):
                with self.assertRaises(rag.RetrievalError):
                    self.sync(attachments=[self.attachment("changedkeyword")])
        with self.assertRaises(rag.RetrievalError):
            self.sync(attachments=[self.attachment("one"), self.attachment("duplicate id")])
        self.assertEqual(rag.search(self.root, self.sid, "retainedkeyword", ["file-one"])[0]["text"], "retainedkeyword")

    def test_invalid_paths_and_identifiers_fail_without_aliasing(self):
        for sid in ("../sess-rag", "sess-rag/", "sess 中文", "_index", "COM1"):
            with self.subTest(sid=sid), self.assertRaises(ValueError):
                rag.sync_session(self.root, sid, [], [])
            with self.subTest(sid=sid), self.assertRaises(ValueError):
                projects.read_full_history(self.root, sid)
        for source_id in ("../transcript.jsonl", "src-" + "0" * 31, "", None):
            with self.subTest(source_id=source_id), self.assertRaises(ValueError):
                rag.source(self.root, self.sid, source_id)
        with self.assertRaises(ValueError):
            rag.search(self.root, self.sid, "query", ["../file"])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_sql_like_query_is_data_and_invalid_limits_are_explicit(self):
        self.sync(attachments=[self.attachment("needle")])
        self.assertEqual(rag.search(self.root, self.sid, "' OR 1=1; DROP TABLE sources; --", ["file-one"]), [])
        self.assertEqual(rag.search(self.root, self.sid, "NEEDLE", ["file-one"])[0]["text"], "needle")
        for limit in (0, -1, True, "6"):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                rag.search(self.root, self.sid, "needle", ["file-one"], limit=limit)
        with self.assertRaises(ValueError):
            rag.search(self.root, self.sid, "needle", ["file-one"], kind="all-sessions")

    def test_real_concurrent_sync_and_search_have_no_partial_snapshots(self):
        files = [self.attachment("concurrentkeyword " * 1600)]
        self.sync(attachments=files)

        def work(index):
            if index % 2:
                return self.sync(attachments=files)["chunks"]
            hits = rag.search(self.root, self.sid, "concurrentkeyword", ["file-one"])
            self.assert_offsets(hits)
            return len(hits)

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(work, range(32)))
        self.assertTrue(all(result > 0 for result in results))
        self.assertEqual(self.sync(attachments=files)["updated"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
