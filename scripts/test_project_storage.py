#!/usr/bin/env python3
"""Project/session storage regressions using isolated files and real writers."""
from __future__ import annotations

import json
import multiprocessing
import ntpath
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from demo import projects as pj
from packing_assistant.filesystem import native_path


def _worker(root: str, worker: int, start, results) -> None:
    try:
        if not start.wait(15):
            raise RuntimeError("start barrier timed out")
        for index in range(12):
            pj.create_project(Path(root), f"process-{worker}-{index}")
            pj.touch_session(Path(root), "sess-shared", f"process-{worker}-{index}")
            pj.append_turn(Path(root), "sess-shared", "user", f"process-{worker}-{index}")
        results.put(None)
    except Exception as exc:
        results.put(f"{type(exc).__name__}: {exc}")


def _hold_lock(root: str, locked, stop) -> None:
    with pj._mutation(Path(root)):
        locked.set()
        stop.wait(30)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="civil-project-storage-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _meta(self, sid="sess-record"):
        return self.root / sid / "session.meta.json"

    def _registry(self):
        path = self.root / "_index" / "projects.v1.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def test_ids_are_validated_without_aliasing_or_truncation(self):
        pj.set_session_meta(self.root, "sess-abcd", title="original")
        pj.set_session_meta(self.root, "a" * 32, title="long original")
        bad = ("sess-/a bcd", "sess-abcd/", "sess-abcd\n", "sess-中文abcd",
               "a" * 33, "_threads", "COM1", "LPT9", "..", "", None, 123)
        for sid in bad:
            with self.subTest(sid=sid), self.assertRaises(ValueError):
                pj.safe_session_id(sid)
            with self.subTest(sid=sid), self.assertRaises(ValueError):
                pj.set_session_meta(self.root, sid, title="wrong")
            # Legacy fire-and-forget hooks may ignore invalid IDs, but cannot
            # normalize them into another conversation.
            pj.touch_session(self.root, sid, "wrong")
            pj.append_turn(self.root, sid, "user", "wrong")
        self.assertEqual(pj.session_detail(self.root, "sess-abcd")["title"], "original")
        self.assertEqual(pj.session_detail(self.root, "a" * 32)["title"], "long original")
        self.assertEqual(pj.session_detail(self.root, "sess-abcd")["transcript"], [])
        for sid in ("abcd", "a_b-c", "-abcd", "a" * 32):
            self.assertEqual(pj.safe_session_id(sid), sid)
        for pid in ("p-01234567\n", "p-01234567/xx", None, 123):
            with self.subTest(pid=pid), self.assertRaises(ValueError):
                pj.safe_project_id(pid)

    def test_concurrent_threads_keep_every_project_and_turn(self):
        def update(index):
            item, reused = pj.create_project(self.root, f"thread-{index}")
            pj.touch_session(self.root, "sess-shared", f"turn-{index}")
            pj.append_turn(self.root, "sess-shared", "assistant", f"turn-{index}")
            return item["id"], reused

        with ThreadPoolExecutor(max_workers=12) as pool:
            created = list(pool.map(update, range(64)))
        self.assertEqual(len({pid for pid, _ in created}), 64)
        self.assertFalse(any(reused for _, reused in created))
        self.assertEqual(len(pj.load_registry(self.root)["projects"]), 64)
        detail = pj.session_detail(self.root, "sess-shared")
        self.assertEqual(detail["turns"], 64)
        self.assertEqual({row["text"] for row in detail["transcript"]},
                         {f"turn-{index}" for index in range(64)})

    def test_concurrent_create_is_idempotent(self):
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: pj.create_project(self.root, "Same project"), range(32)))
        self.assertEqual(len({item["id"] for item, _ in results}), 1)
        self.assertEqual(sum(not reused for _, reused in results), 1)
        self.assertEqual(len(pj.load_registry(self.root)["projects"]), 1)

    def test_concurrent_processes_keep_every_project_and_record(self):
        ctx = multiprocessing.get_context("spawn")
        start, results = ctx.Event(), ctx.Queue()
        workers = [ctx.Process(target=_worker, args=(str(self.root), i, start, results))
                   for i in range(4)]
        try:
            for worker in workers:
                worker.start()
            start.set()
            for worker in workers:
                worker.join(45)
                self.assertFalse(worker.is_alive(), "storage worker deadlocked")
                self.assertEqual(worker.exitcode, 0)
            self.assertEqual([results.get(timeout=5) for _ in workers], [None] * 4)
        finally:
            for worker in workers:
                if worker.is_alive():
                    worker.terminate()
                    worker.join(5)
            results.close()
            results.join_thread()
        registry = pj.load_registry(self.root)
        self.assertEqual(len(registry["projects"]), 48)
        self.assertEqual(len({row["id"] for row in registry["projects"]}), 48)
        detail = pj.session_detail(self.root, "sess-shared")
        self.assertEqual(detail["turns"], 48)
        self.assertEqual({row["text"] for row in detail["transcript"]},
                         {f"process-{w}-{i}" for w in range(4) for i in range(12)})

    @unittest.skipUnless(os.name == "nt", "Windows final-path normalization")
    def test_transient_windows_final_path_prefix_is_not_an_escape(self):
        # ntpath.realpath probes an existing file twice. If the second probe
        # loses a race with a rename/share lock, it retains the equivalent
        # extended-length prefix. Path.relative_to treats that as another drive.
        target = self.root / "sess-race" / "session.meta.json"
        target.parent.mkdir()
        target.write_text("{}", encoding="utf-8")
        final_path = ntpath._getfinalpathname
        calls = 0

        def raced(path):
            nonlocal calls
            if str(path).casefold() == str(target).casefold():
                calls += 1
                if calls == 2:
                    error = OSError("transient sharing violation")
                    error.winerror = 32
                    raise error
            return final_path(path)

        with patch.object(ntpath, "_getfinalpathname", side_effect=raced):
            result = pj._bounded_path(self.root, "sess-race", "session.meta.json")
        self.assertEqual(calls, 2)
        self.assertEqual(result, target)
        self.assertEqual(result.read_text(encoding="utf-8"), "{}")

    @unittest.skipUnless(os.name == "nt", "Windows extended-path containment")
    def test_extended_root_preserves_identity_and_rejects_traversal(self):
        extended = native_path(self.root)
        pj.touch_session(extended, "sess-native", "same physical session")
        normal = pj._bounded_path(self.root, "sess-native", "session.meta.json")
        native = pj._bounded_path(extended, "sess-native", "session.meta.json")
        self.assertFalse(str(normal).startswith("\\\\?\\"))
        self.assertTrue(str(native).startswith("\\\\?\\"))
        self.assertTrue(normal.samefile(native))
        self.assertEqual(pj.session_detail(self.root, "sess-native"),
                         pj.session_detail(extended, "sess-native"))
        for root in (self.root, extended):
            with self.subTest(root=str(root)), self.assertRaises(ValueError):
                pj._bounded_path(root, "..", "outside-store", "session.meta.json")

    @unittest.skipUnless(os.name == "nt", "Windows junction containment")
    def test_extended_root_rejects_real_junction_without_touching_target(self):
        import _winapi

        external = self.root / "external"
        external.mkdir()
        meta = external / "session.meta.json"
        original = b'{"title":"external","turns":1}'
        meta.write_bytes(original)
        link = self.root / "sess-junction"
        _winapi.CreateJunction(str(external), str(link))
        # Remove only this test-owned junction, not its target directory.
        self.addCleanup(link.rmdir)
        self.assertTrue(link.is_junction())
        for root in (self.root, native_path(self.root)):
            with self.subTest(root=str(root)), self.assertRaises(ValueError):
                pj._bounded_path(root, "sess-junction", "session.meta.json")
            with self.subTest(root=str(root)), self.assertRaises(ValueError):
                pj.set_session_meta(root, "sess-junction", title="must not overwrite")
        self.assertEqual(meta.read_bytes(), original)

    def test_concurrent_manual_metadata_is_not_lost(self):
        project, _ = pj.create_project(self.root, "Manual project")
        pj.touch_session(self.root, "sess-shared", "Automatic title")
        def update(index):
            if index == 5:
                return pj.set_session_meta(self.root, "sess-shared", title="Manual title")
            if index == 15:
                return pj.set_session_meta(self.root, "sess-shared", project_id=project["id"])
            return pj.touch_session(self.root, "sess-shared", "Unrelated automatic update")
        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(update, range(50)))
        detail = pj.session_detail(self.root, "sess-shared")
        self.assertEqual(detail["title"], "Manual title")
        self.assertEqual(detail["project_id"], project["id"])
        self.assertEqual(detail["turns"], 49)
        meta = json.loads(self._meta("sess-shared").read_text(encoding="utf-8"))
        self.assertEqual(meta["title_source"], "manual")
        self.assertEqual(meta["project_source"], "manual")

    def test_process_exit_releases_storage_lock(self):
        project, _ = pj.create_project(self.root, "Before interruption")
        ctx = multiprocessing.get_context("spawn")
        locked, stop = ctx.Event(), ctx.Event()
        worker = ctx.Process(target=_hold_lock, args=(str(self.root), locked, stop))
        worker.start()
        try:
            self.assertTrue(locked.wait(10))
        finally:
            if worker.is_alive():
                worker.terminate()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        added, _ = pj.create_project(self.root, "After interruption")
        self.assertEqual({row["id"] for row in pj.load_registry(self.root)["projects"]},
                         {project["id"], added["id"]})

    def test_corrupt_registry_is_recoverable_but_never_overwritten(self):
        pj.touch_session(self.root, "sess-record", "Still readable")
        original = b'{"projects":['
        path = self._registry()
        path.write_bytes(original)
        self.assertEqual(pj.list_sessions(self.root, recorded_only=True)["total"], 1)
        self.assertEqual(pj.session_detail(self.root, "sess-record")["title"], "Still readable")
        for operation in (
            lambda: pj.create_project(self.root, "New"),
            lambda: pj.patch_project(self.root, "p-01234567", name="New"),
            lambda: pj.merge_project(self.root, "p-01234567", "p-12345678"),
            lambda: pj.set_session_meta(self.root, "sess-record", title="New"),
            lambda: pj.touch_session(self.root, "sess-record", "New"),
        ):
            with self.assertRaisesRegex(ValueError, "原文件已保留"):
                operation()
            self.assertEqual(path.read_bytes(), original)
        self.assertEqual(pj.session_detail(self.root, "sess-record")["turns"], 1)

    def test_registry_recovers_valid_entries_without_erasing_invalid_ones(self):
        project, _ = pj.create_project(self.root, "Valid")
        pj.set_session_meta(self.root, "sess-record", project_id=project["id"])
        path = self._registry()
        value = json.loads(path.read_text(encoding="utf-8"))
        value["projects"] += [None, {"id": "p-abcdef12", "name": 123}]
        original = json.dumps(value).encode("utf-8")
        path.write_bytes(original)
        self.assertEqual(pj.list_projects(self.root)["projects"][0]["name"], "Valid")
        self.assertEqual(pj.session_detail(self.root, "sess-record")["project_id"], project["id"])
        with self.assertRaises(ValueError):
            pj.create_project(self.root, "New")
        self.assertEqual(path.read_bytes(), original)

    def test_duplicate_project_ids_and_wrong_schema_block_writes(self):
        project, _ = pj.create_project(self.root, "Valid")
        path = self._registry()
        for value in (
            {"projects": [project, dict(project, name="Conflicting name")]},
            {"schema": "unknown.version", "projects": []},
            {"projects": [dict(project, aliases=[None])]},
            {"projects": [dict(project, updated_at="broken")]},
            {"projects": [dict(project, name="\ud800")]},
        ):
            original = json.dumps(value).encode("utf-8")
            path.write_bytes(original)
            pj.list_projects(self.root)
            with self.assertRaises(ValueError):
                pj.create_project(self.root, "New")
            self.assertEqual(path.read_bytes(), original)

    def test_corrupt_metadata_recovers_history_and_remains_untouched(self):
        pj.touch_session(self.root, "sess-record", "Original")
        pj.append_turn(self.root, "sess-record", "user", "Preserved history")
        for original in (b'{"turns":', b"\xff", b"[]"):
            self._meta().write_bytes(original)
            detail = pj.session_detail(self.root, "sess-record")
            self.assertEqual(detail["transcript"][0]["text"], "Preserved history")
            self.assertEqual(detail["turns"], 0)
            self.assertEqual(pj.list_sessions(self.root, recorded_only=True)["total"], 1)
            for operation in (
                lambda: pj.touch_session(self.root, "sess-record", "New"),
                lambda: pj.set_session_meta(self.root, "sess-record", title="New"),
            ):
                with self.assertRaisesRegex(ValueError, "原文件已保留"):
                    operation()
                self.assertEqual(self._meta().read_bytes(), original)

    def test_invalid_metadata_fields_do_not_break_other_sessions(self):
        pj.touch_session(self.root, "sess-record", "Readable title")
        pj.touch_session(self.root, "sess-other", "Other title")
        value = json.loads(self._meta().read_text(encoding="utf-8"))
        value.update(turns="broken", updated_at=[], project_id=42)
        original = json.dumps(value).encode("utf-8")
        self._meta().write_bytes(original)
        detail = pj.session_detail(self.root, "sess-record")
        self.assertEqual(detail["title"], "Readable title")
        self.assertEqual(detail["turns"], 0)
        self.assertEqual(detail["project_id"], pj.INBOX_ID)
        self.assertEqual(pj.list_sessions(self.root, recorded_only=True)["total"], 2)
        with self.assertRaises(ValueError):
            pj.touch_session(self.root, "sess-record", "New")
        self.assertEqual(self._meta().read_bytes(), original)

    def test_metadata_identity_mismatch_does_not_restore_other_session(self):
        pj.touch_session(self.root, "sess-record", "Original")
        value = json.loads(self._meta().read_text(encoding="utf-8"))
        value.update(session_id="sess-other", title="Wrong session")
        original = json.dumps(value).encode("utf-8")
        self._meta().write_bytes(original)
        self.assertEqual(pj.session_detail(self.root, "sess-record")["title"], "sess-record")
        with self.assertRaisesRegex(ValueError, "身份不匹配"):
            pj.set_session_meta(self.root, "sess-record", title="Replacement")
        self.assertEqual(self._meta().read_bytes(), original)

    def test_atomic_failure_preserves_old_file_and_other_temporary_file(self):
        pj.touch_session(self.root, "sess-record", "Original")
        path = self._meta()
        original = path.read_bytes()
        other = path.with_suffix(path.suffix + ".tmp")
        other.write_text("another writer", encoding="utf-8")
        with patch.object(Path, "replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                pj.touch_session(self.root, "sess-record", "Failed update")
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(other.read_text(encoding="utf-8"), "another writer")
        self.assertEqual(list(path.parent.glob(".session.meta.json.*.tmp")), [])
        pj.touch_session(self.root, "sess-record", "Successful retry")
        self.assertEqual(pj.session_detail(self.root, "sess-record")["turns"], 2)

    def test_atomic_writers_use_distinct_temporary_files(self):
        path = self.root / "atomic.json"
        def update(index):
            text = json.dumps({"index": index, "padding": str(index) * 8000})
            pj._write_atomic(path, text)
        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(update, range(36)))
        value = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(value["padding"], str(value["index"]) * 8000)
        self.assertEqual(list(self.root.glob(".atomic.json.*.tmp")), [])

    def test_transcript_unicode_limit_preserves_maximum_complete_prefix(self):
        for index, text in enumerate(("a" * 9000, "😀" * 3000, "中" * 4000)):
            sid = f"sess-{index}"
            pj.append_turn(self.root, sid, "assistant", text)
            actual = pj.session_detail(self.root, sid)["transcript"][0]["text"]
            expected = text.encode("utf-8")[:pj.TEXT_MAX_BYTES].decode("utf-8", errors="ignore")
            self.assertEqual(actual, expected)
            self.assertLessEqual(len(actual.encode("utf-8")), pj.TEXT_MAX_BYTES)

    def test_transcript_recovers_valid_tail_with_bounded_reads(self):
        path = self.root / "sess-record" / "transcript.jsonl"
        path.parent.mkdir()
        data = [json.dumps({"ts": i, "role": "user", "text": str(i)}).encode() + b"\n"
                for i in range(pj.TRANSCRIPT_TAIL + 5)]
        data += [b"null\n", b"[]\n", b"\xff\n", b'{"ts":1,"role":"user","text":123}\n',
                 b'{"ts":1,"role":"user","text":"\\ud800"}\n',
                 b"x" * (2 * 1024 * 1024) + b"\n", b'{"interrupted":']
        path.write_bytes(b"".join(data))
        original_size = path.stat().st_size
        original_read = Path.read_text
        def read_text(candidate, *args, **kwargs):
            self.assertNotEqual(candidate, path, "transcript cannot be read into memory at once")
            return original_read(candidate, *args, **kwargs)
        with patch.object(Path, "read_text", read_text):
            detail = pj.session_detail(self.root, "sess-record")
        self.assertTrue(detail["truncated"])
        self.assertEqual([row["text"] for row in detail["transcript"]],
                         [str(i) for i in range(5, pj.TRANSCRIPT_TAIL + 5)])
        self.assertEqual(path.stat().st_size, original_size)

    def test_append_after_interrupted_record_preserves_new_turn(self):
        path = self.root / "sess-record" / "transcript.jsonl"
        path.parent.mkdir()
        original = b'{"ts":1,"role":"user","text":"incomplete'
        path.write_bytes(original)
        pj.append_turn(self.root, "sess-record", "assistant", "Recovered next turn")
        self.assertTrue(path.read_bytes().startswith(original + b"\n"))
        detail = pj.session_detail(self.root, "sess-record")
        self.assertTrue(detail["truncated"])
        self.assertEqual([row["text"] for row in detail["transcript"]], ["Recovered next turn"])

    def test_linked_session_is_neither_restored_nor_modified(self):
        external = self.root / "outside"
        external.mkdir()
        meta = external / "session.meta.json"
        original = b'{"title":"external","turns":1}'
        meta.write_bytes(original)
        link = self.root / "sess-linked"
        try:
            link.symlink_to(external, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"OS does not allow symlink creation: {exc.winerror if os.name == 'nt' else exc.errno}")
        with self.assertRaises(ValueError):
            pj.session_detail(self.root, "sess-linked")
        with self.assertRaises(ValueError):
            pj.set_session_meta(self.root, "sess-linked", title="Wrong")
        self.assertNotIn("sess-linked", [row["session_id"] for row in pj.list_sessions(self.root)["sessions"]])
        self.assertEqual(meta.read_bytes(), original)

    def test_invalid_index_cap_does_not_hide_last_session(self):
        pj.touch_session(self.root, "sess-record", "Visible")
        for raw in ("-1", "0", "invalid"):
            with patch.dict(os.environ, {pj._L["session_index_max_env"]: raw}):
                self.assertEqual(pj.list_sessions(self.root)["total"], 1)

    def test_normal_assignment_merge_archive_and_restore_contract(self):
        source, _ = pj.create_project(self.root, "Source")
        target, _ = pj.create_project(self.root, "Target")
        pj.touch_session(self.root, "sess-record", "Source activity")
        pj.append_turn(self.root, "sess-record", "user", "Question")
        pj.append_turn(self.root, "sess-record", "assistant", "Answer")
        pj.merge_project(self.root, source["id"], target["id"])
        detail = pj.session_detail(self.root, "sess-record")
        self.assertEqual(set(detail), set(pj.C["fields"]["session_detail"]))
        self.assertEqual(detail["project_id"], target["id"])
        self.assertFalse(detail["truncated"])
        self.assertEqual([row["text"] for row in detail["transcript"]], ["Question", "Answer"])
        pj.patch_project(self.root, target["id"], archived=True)
        self.assertEqual(pj.session_detail(self.root, "sess-record")["project_id"], pj.INBOX_ID)
        self.assertEqual(pj.list_sessions(self.root, recorded_only=True)["total"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
