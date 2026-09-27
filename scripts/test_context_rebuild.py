"""Task context repair through real HTTP, with byte-for-byte original retention."""
from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import test_workbench_flow as flow
import chat_service
import context
import local_retrieval
import projects
import session_context
import uploads


class ContextRebuildTests(unittest.TestCase):
    def setUp(self):
        flow.WorkbenchFlowTests.setUp(self)
        self.addCleanup(context.set_runtime_policy, context.runtime_policy())
        self.addCleanup(context.set_semantic_summary, context.semantic_summary_enabled())
        context.set_semantic_summary(True)
        projects.touch_session(self.root, self.sid, "重建验收任务")
        projects.append_turn(self.root, self.sid, "user", "任务目标：整理东桥资料。\n项目名称：保留工程。\n我明白，将由持证人员签认")
        self.folder = self.root / self.sid
        self.attachment = uploads.save_upload(self.sid, "资料.txt", "独立附件标记ZX45：南门交付。".encode())
        self.hidden = uploads.save_upload(self.sid, "另份资料.txt", "另份附件标记ZX46：西楼核对。".encode())
        for name, value in (("session.summary.json", '{"project":"original","P0":"original"}'),
                            ("collaboration.summary.json", '{"fixture":"preserved collaboration"}'),
                            ("runs/prior/workbench.json", '{"fixture":"preserved run"}'),
                            ("deliverables/prior/original.md", "原有交付物")):
            path = self.folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")
        session_context.persist(self.root, self.sid, {"used": 123, "note": "old report"})
        (self.folder / "semantic.summary.json").write_text('{"old":"summary"}', encoding="utf-8")
        (self.folder / "semantic.attempt.json").write_text('{"old":"attempt"}', encoding="utf-8")
        originals = [self.folder / "transcript.jsonl", self.folder / "session.meta.json",
            self.folder / "session.summary.json", self.folder / "collaboration.summary.json",
            self.folder / "runs/prior/workbench.json", self.folder / "deliverables/prior/original.md"]
        originals += [p for p in uploads.session_uploads_dir(self.sid).rglob("*") if p.is_file()]
        self.originals = {path: path.read_bytes() for path in originals}

    def rebuild(self):
        return self.client.post("/api/context/rebuild", json={"session_id": self.sid})

    def assert_originals(self):
        for path, expected in self.originals.items():
            self.assertEqual(path.read_bytes(), expected, path.name)
        self.no_model.assert_not_called()

    def caches(self):
        return {p.name: p.read_bytes() for p in self.folder.iterdir() if p.is_file()
                and (p.name.startswith("context.") or p.name.startswith("semantic.") or p.name.startswith("local-retrieval"))}

    @unittest.skipUnless(os.name == "nt", "Windows shared-read replacement retry")
    def test_context_report_retries_transient_windows_reader_lock(self):
        replace = Path.replace
        attempts = []
        def busy_once(source, target):
            if Path(target).name == "context.last.json":
                attempts.append(target)
                if len(attempts) == 1:
                    raise PermissionError(5, "synthetic reader sharing conflict")
            return replace(source, target)
        with patch.object(Path, "replace", busy_once):
            session_context.persist(self.root, self.sid, {"used": 321, "note": "new report"})
        self.assertEqual(len(attempts), 2)
        self.assertEqual(json.loads((self.folder / "context.last.json").read_text(encoding="utf-8"))["used"], 321)
        self.assertEqual(list(self.folder.glob(".context.last.json.*.tmp")), [])
        self.assert_originals()

    def test_context_report_permanent_replace_failure_retains_previous_cache(self):
        target = self.folder / "context.last.json"
        original = target.read_bytes()
        replace = Path.replace
        def denied(source, destination):
            if Path(destination) == target:
                raise PermissionError(5, "synthetic persistent refusal")
            return replace(source, destination)
        with patch.object(Path, "replace", denied), self.assertRaises(PermissionError):
            session_context.persist(self.root, self.sid, {"used": 999})
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(list(self.folder.glob(".context.last.json.*.tmp")), [])
        self.assert_originals()

    def test_corrupt_caches_rebuild_from_originals_and_clear_model_summary(self):
        (self.folder / "context.summary.json").write_text("broken JSON", encoding="utf-8")
        (self.folder / "local-retrieval.sqlite3").write_bytes(b"broken index")
        response = self.rebuild()
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["rebuild"]["history_messages"], 1)
        self.assertEqual(data["rebuild"]["attachments"], 2)
        self.assertEqual(data["rebuild"]["sources"], 3)
        self.assertTrue(data["rebuild"]["semantic_cleared"])
        self.assertEqual(data["rebuild"]["model_calls"], 0)
        self.assertIn("保留工程", data["memory_text"])
        self.assertNotIn("我明白，将由持证人员签认", data["memory_text"])
        self.assertFalse(data["summary"]["authorizes_actions"])
        self.assertEqual(data["semantic_status"]["state"], "missing")
        self.assertEqual(data["semantic_memory_text"], "")
        self.assertEqual(data["context"]["used"], 0)
        self.assertEqual(data["context"]["semantic"]["status"], "reset")
        for name in ("semantic.summary.json", "semantic.attempt.json"):
            self.assertFalse((self.folder / name).exists())
        self.assertTrue(context.semantic_summary_enabled())
        hits = local_retrieval.search(self.root, self.sid, "ZX45", attachment_ids=[self.attachment["id"]])
        self.assertTrue(hits)
        self.assertFalse(local_retrieval.search(self.root, self.sid, "ZX46", attachment_ids=[self.attachment["id"]]))
        self.assertFalse(local_retrieval.search(self.root, self.sid, "ZX45", attachment_ids=[]))
        cite = session_context.citation(self.sid, hits[0])
        self.assertEqual(self.client.get(cite["url"]).json()["text"], cite["snippet"])
        self.assert_originals()
        self.assertFalse(chat_service.session_detail(self.root, self.sid)["turn_state"]["active"])

    def test_rebuild_replaces_corrupt_chunks_even_when_fingerprints_match(self):
        with closing(sqlite3.connect(self.folder / "local-retrieval.sqlite3")) as db, db:
            db.execute("UPDATE chunks SET text='incorrect cached excerpt',tokens='wincorrect'")
        self.assertEqual(self.rebuild().status_code, 200)
        hits = local_retrieval.search(self.root, self.sid, "ZX45", attachment_ids=[self.attachment["id"]])
        self.assertTrue(hits)
        self.assertIn("南门交付", hits[0]["text"])
        self.assert_originals()

    def test_busy_task_rejects_rebuild_without_mutation(self):
        before = self.caches()
        lease = chat_service.SessionLease(self.sid)
        try:
            self.assertEqual(self.rebuild().status_code, 409)
        finally:
            lease.release()
        self.assertEqual(self.caches(), before)
        self.assert_originals()

    def test_read_only_mode_rejects_before_any_cache_change(self):
        before = self.caches()
        with patch.dict(os.environ, {"CIVIL_SANDBOX": "read-only"}):
            response = self.rebuild()
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.caches(), before)
        self.assert_originals()

    def test_bad_payload_missing_task_and_path_traversal_do_not_create_tasks(self):
        for payload, status in (({"session_id": "missing-task"}, 404),
                                ({"session_id": "../outside"}, 400),
                                ({"session_id": self.sid, "clear_originals": True}, 422),
                                ({"session_id": None}, 422)):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post("/api/context/rebuild", json=payload).status_code, status)
        self.assertFalse((self.root / "missing-task").exists())
        self.assert_originals()

    def test_corrupt_original_transcript_is_not_silently_treated_as_complete(self):
        path = self.folder / "transcript.jsonl"
        with path.open("ab") as handle:
            handle.write(b"invalid original row\n")
        self.originals[path] = path.read_bytes()
        before = self.caches()
        response = self.rebuild()
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("原始对话含损坏记录", response.text)
        self.assertEqual(self.caches(), before)
        self.assert_originals()

    def test_index_failure_does_not_clear_old_summary_or_leak_error_details(self):
        before = self.caches()
        with patch.object(local_retrieval, "sync_session", side_effect=OSError("private-error-fixture")):
            response = self.rebuild()
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("private-error-fixture", response.text)
        self.assertEqual(self.caches(), before)
        self.assert_originals()
        self.assertEqual(self.rebuild().status_code, 200)  # Lease was released after failure.

    def test_incomplete_attachment_is_not_silently_removed_from_last_good_index(self):
        directory = uploads.session_uploads_dir(self.sid)
        for extension, damaged in (("json", b"invalid metadata"), ("json", None),
                                   ("bin", None), ("txt", None), ("txt", b"short"), ("bin", b"short")):
            with self.subTest(extension=extension, damaged=damaged):
                path = directory / (self.attachment["id"] + "." + extension)
                original = path.read_bytes()
                before = self.caches()
                try:
                    if damaged is None:
                        path.unlink()
                    else:
                        path.write_bytes(damaged)
                    response = self.rebuild()
                    self.assertEqual(response.status_code, 400, response.text)
                    self.assertEqual(self.caches(), before)
                    self.assertTrue(local_retrieval.search(self.root, self.sid, "ZX45", attachment_ids=[self.attachment["id"]]))
                finally:
                    path.write_bytes(original)
        self.assert_originals()

    @unittest.skipUnless(os.name == "nt", "Windows case-insensitive attachment paths")
    def test_uppercase_extensions_preserve_readable_windows_attachment(self):
        directory = uploads.session_uploads_dir(self.sid)
        for extension in ("json", "txt", "bin"):
            path = directory / (self.attachment["id"] + "." + extension)
            path.rename(path.with_suffix("." + extension.upper()))
        self.assertEqual(len(uploads.list_uploads(self.sid)), 2)
        response = self.rebuild()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["rebuild"]["attachments"], 2)
        self.assertTrue(local_retrieval.search(self.root, self.sid, "ZX45", attachment_ids=[self.attachment["id"]]))
        self.assert_originals()

    def test_derived_directory_error_does_not_block_session_restore_or_remove_directory(self):
        path = self.folder / "context.summary.json"
        path.unlink()
        path.mkdir()
        child = path / "retain.txt"
        child.write_text("retain this directory", encoding="utf-8")
        data = self.client.get("/api/context", params={"session_id": self.sid})
        self.assertEqual(data.status_code, 200)
        self.assertEqual(data.json()["memory_status"]["state"], "unavailable")
        self.assertEqual(self.client.get("/api/sessions/" + self.sid).status_code, 200)
        self.assertEqual(self.rebuild().status_code, 400)
        self.assertEqual(child.read_text(encoding="utf-8"), "retain this directory")
        self.assert_originals()

    def test_repeat_rebuild_is_idempotent_and_cache_states_are_explicit(self):
        before = self.client.get("/api/context", params={"session_id": self.sid}).json()
        self.assertEqual(before["semantic_status"]["state"], "stale")
        self.assertEqual(self.rebuild().status_code, 200)
        snapshot = self.caches()
        self.assertEqual(self.rebuild().status_code, 200)
        after = self.client.get("/api/context", params={"session_id": self.sid}).json()
        self.assertEqual(after["semantic_status"]["state"], "missing")
        self.assertEqual(after["memory_status"]["state"], "ready")
        self.assertEqual(self.caches()["context.summary.json"], snapshot["context.summary.json"])
        context.set_semantic_summary(False)
        disabled = self.client.get("/api/context", params={"session_id": self.sid}).json()
        self.assertEqual(disabled["semantic_status"]["state"], "disabled")
        self.assert_originals()


if __name__ == "__main__":
    unittest.main()
