#!/usr/bin/env python3
"""Actual SQLite close, concurrent cursor retirement and durable restarts."""
from __future__ import annotations

from contextlib import closing
import gc
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import warnings
import weakref
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from packing_assistant import lg_checkpoint, storage
from packing_assistant.sqlite_resources import ManagedConnection, ManagedCursor


class SqliteLifecycle(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="civil-sqlite-lifecycle-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.db = self.root / "test.db"

    def store(self):
        result = storage.Storage(self.db)
        self.addCleanup(result.close)
        return result

    def test_close_retires_writer_and_idle_reader_without_gc_or_implicit_commit(self):
        st = self.store()
        st.save_session("committed", {"phase": "done"})
        writer, reader = st.write_conn, st.read_conn()
        writer.execute("INSERT INTO events(run_id,type) VALUES('partial','unfinished')").close()
        st.close()
        self.assertTrue(writer.closed and reader.closed)
        reopened = self.store()
        self.assertEqual("done", reopened.load_session("committed")["phase"])
        self.assertEqual([], reopened.read_trace_events("partial"))
        self.assertEqual(0, reopened.count("events"))

    def test_close_is_idempotent_and_does_not_reopen_storage(self):
        st = self.store()
        st.close()
        st.close()
        for operation in (st.read_conn, lambda: st.write_conn, lambda: st.count("sessions"),
                          lambda: st.save_session("unexpected", {})):
            with self.assertRaisesRegex(sqlite3.ProgrammingError, "Storage is closed"):
                operation()

    def test_close_preserves_borrowed_live_cursor_until_explicit_release(self):
        st = self.store()
        conn = st.read_conn()
        cursor = conn.execute("SELECT 7 UNION ALL SELECT 11")
        st.close()
        self.assertFalse(conn.closed)
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 13")
        with self.assertRaises(sqlite3.ProgrammingError):
            cursor.execute("SELECT 13")
        self.assertEqual([(7,), (11,)], cursor.fetchall())
        cursor.close()
        self.assertTrue(conn.closed)
        cursor.close()
        self.assertEqual(0, conn._cursor_count)

    def test_cursor_collection_releases_a_retired_connection_without_warning(self):
        st = self.store()
        conn = st.read_conn()
        cursor = conn.execute("SELECT 7")
        st.close()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            del cursor
            gc.collect()
        self.assertTrue(conn.closed)
        self.assertFalse([w for w in caught if issubclass(w.category, ResourceWarning)])

    def test_thread_exit_retires_its_reader_even_when_an_external_reference_survives(self):
        st, captured = self.store(), []
        thread = threading.Thread(target=lambda: captured.append(st.read_conn()))
        thread.start()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(1, len(captured))
        self.assertTrue(captured[0].closed)

    def test_thread_exit_leaves_exported_cursor_usable_until_its_release(self):
        st, captured = self.store(), []
        thread = threading.Thread(target=lambda: captured.append(st.read_conn().execute("SELECT 17")))
        thread.start()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        cursor = captured[0]
        conn = cursor.connection
        self.assertFalse(conn.closed)
        self.assertEqual((17,), cursor.fetchone())
        cursor.close()
        self.assertTrue(conn.closed)

    def test_storage_close_retires_other_live_threads_idle_connections(self):
        st, captured = self.store(), []
        ready, stop = threading.Event(), threading.Event()

        def read():
            captured.append(st.read_conn())
            ready.set()
            stop.wait(5)

        thread = threading.Thread(target=read)
        thread.start()
        try:
            self.assertTrue(ready.wait(5))
            st.close()
            self.assertTrue(captured[0].closed)
        finally:
            stop.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())

    def test_close_during_an_inflight_sql_call_does_not_interrupt_it(self):
        st, captured, failures = self.store(), [], []
        entered, continue_query = threading.Event(), threading.Event()

        def slow(value):
            entered.set()
            if not continue_query.wait(5):
                raise RuntimeError("test release timed out")
            return value

        def read():
            try:
                conn = st.read_conn()
                captured.append(conn)
                conn.create_function("slow", 1, slow)
                with closing(conn.execute("SELECT slow(23)")) as cursor:
                    captured.append(cursor.fetchone())
            except BaseException as exc:
                failures.append(exc)

        thread = threading.Thread(target=read)
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            st.close()
            self.assertFalse(captured[0].closed)
        finally:
            continue_query.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual([], failures)
        self.assertEqual((23,), captured[1])
        self.assertTrue(captured[0].closed)

    def test_many_threads_release_only_their_own_reader(self):
        st, connections, failures = self.store(), [], []
        ready = threading.Barrier(9)
        done = threading.Event()

        def worker():
            try:
                conn = st.read_conn()
                connections.append(conn)
                ready.wait(5)
                done.wait(5)
                if conn.execute("SELECT 29").fetchone() != (29,):
                    raise AssertionError("wrong thread-local read result")
            except BaseException as exc:
                failures.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        try:
            ready.wait(5)
            self.assertEqual(8, len({id(c) for c in connections}))
            self.assertTrue(all(not c.closed for c in connections))
        finally:
            done.set()
            for thread in threads:
                thread.join(5)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual([], failures)
        self.assertTrue(all(c.closed for c in connections))

    def test_a_manually_closed_reader_can_be_reacquired(self):
        st = self.store()
        previous = st.read_conn()
        previous.close()
        current = st.read_conn()
        self.assertIsNot(previous, current)
        self.assertEqual(0, st.count("sessions"))

    def test_failed_query_does_not_keep_a_cursor_lease(self):
        st = self.store()
        conn = st.read_conn()
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("SELECT * FROM absent_table")
        gc.collect()
        self.assertEqual(0, conn._cursor_count)
        st.close()
        self.assertTrue(conn.closed)

    def test_cursor_factory_and_sqlite_row_factory_contracts_are_retained(self):
        st = self.store()
        conn = st.read_conn()
        conn.row_factory = sqlite3.Row
        with closing(conn.cursor()) as cursor:
            self.assertIsInstance(cursor, ManagedCursor)
            self.assertEqual(31, cursor.execute("SELECT 31 AS n").fetchone()["n"])
        cursor = conn.cursor(factory=sqlite3.Cursor)
        self.assertIs(type(cursor), sqlite3.Cursor)
        st.close()
        self.assertEqual(37, cursor.execute("SELECT 37 AS n").fetchone()["n"])
        cursor.close()
        # Non-managed caller factories release ownership at collection.
        del cursor
        gc.collect()
        self.assertTrue(conn.closed)

    def test_gc_of_unclosed_store_actually_closes_handles(self):
        st = storage.Storage(self.db)
        writer, reader = st.write_conn, st.read_conn()
        reference = weakref.ref(st)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            del st
            gc.collect()
        self.assertIsNone(reference())
        self.assertTrue(writer.closed and reader.closed)
        self.assertFalse([w for w in caught if issubclass(w.category, ResourceWarning)])

    def test_pragma_failure_closes_the_unpublished_connection_and_preserves_error(self):
        real_connect, captured = sqlite3.connect, []

        class FailingConnection(ManagedConnection):
            def execute(self, *args, **kwargs):
                raise sqlite3.OperationalError("synthetic pragma failure")

        def connect(*args, **kwargs):
            kwargs["factory"] = FailingConnection
            conn = real_connect(*args, **kwargs)
            captured.append(conn)
            return conn

        with patch.object(storage.sqlite3, "connect", side_effect=connect):
            with self.assertRaisesRegex(sqlite3.OperationalError, "synthetic pragma failure"):
                storage.Storage(self.db)
        self.assertEqual(1, len(captured))
        self.assertTrue(captured[0].closed)

    def test_schema_failure_closes_the_published_writer(self):
        captured = []

        def fail(st):
            captured.append(st.write_conn)
            raise RuntimeError("synthetic schema failure")

        with patch.object(storage.Storage, "_ensure_schema", fail):
            with self.assertRaisesRegex(RuntimeError, "synthetic schema failure"):
                storage.Storage(self.db)
        self.assertTrue(captured[0].closed)

    def test_one_failed_close_does_not_skip_other_owned_handles(self):
        st = self.store()
        writer, reader = st.write_conn, st.read_conn()
        original = ManagedConnection.retire

        def fail_once(conn):
            if conn is writer:
                raise sqlite3.OperationalError("synthetic writer close failure")
            return original(conn)

        with patch.object(ManagedConnection, "retire", fail_once):
            with self.assertRaisesRegex(sqlite3.OperationalError, "synthetic writer close failure"):
                st.close()
        self.assertTrue(reader.closed)
        writer.close()

    def test_singleton_reset_retires_old_readers_and_reopens_the_same_durable_data(self):
        saved = storage._STORAGE
        storage._STORAGE = None
        self.addCleanup(setattr, storage, "_STORAGE", saved)
        self.addCleanup(storage.reset_storage)
        with patch.dict(os.environ, {"CB_DB_PATH": str(self.db)}):
            first = storage.get_storage()
            first.save_session("kept", {"phase": "done"})
            conn = first.read_conn()
            storage.reset_storage()
            self.assertTrue(conn.closed)
            second = storage.get_storage()
            self.assertIsNot(first, second)
            self.assertEqual("done", second.load_session("kept")["phase"])

    def test_normal_process_exit_closes_local_singleton_and_checkpoint_handles(self):
        code = (
            "import os, sqlite3\n"
            "from packing_assistant import storage, lg_checkpoint\n"
            "from packing_assistant.sqlite_resources import ManagedConnection\n"
            "class Tracked(ManagedConnection):\n"
            " def close(self):\n"
            "  was_closed=self.closed\n"
            "  super().close()\n"
            "  if not was_closed: print('closed-owned-handle', flush=True)\n"
            "storage.ManagedConnection=lg_checkpoint.ManagedConnection=Tracked\n"
            "local=storage.Storage(os.environ['CB_DB_PATH'])\n"
            "local.read_conn()\n"
            "storage.get_storage().read_conn()\n"
            "lg_checkpoint.get_checkpointer()\n"
        )
        env = {**os.environ, "CB_DB_PATH": str(self.db), "PACKING_LG_CHECKPOINT_PATH": str(self.db),
               "PYTHON_DOTENV_DISABLED": "1", "PACKING_LG_CHECKPOINT": "1", "PYTHONUTF8": "1"}
        result = subprocess.run([sys.executable, "-W", "always::ResourceWarning", "-c", code],
                                cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(5, result.stdout.count("closed-owned-handle"), result.stdout)
        self.assertNotIn("unclosed database", result.stderr)


class CheckpointLifecycle(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="civil-cp-lifecycle-")
        self.addCleanup(directory.cleanup)
        self.db = Path(directory.name) / "checkpoint.db"
        env = patch.dict(os.environ, {"PACKING_LG_CHECKPOINT": "1", "PACKING_LG_CHECKPOINT_PATH": str(self.db)})
        env.start()
        self.addCleanup(env.stop)
        saved = lg_checkpoint._SAVER, lg_checkpoint._CONN
        lg_checkpoint._SAVER = lg_checkpoint._CONN = None
        self.addCleanup(lambda: setattr(lg_checkpoint, "_SAVER", saved[0]))
        self.addCleanup(lambda: setattr(lg_checkpoint, "_CONN", saved[1]))
        self.addCleanup(lg_checkpoint.close_checkpointer)

    def test_checkpoint_close_releases_singleton_and_durable_thread_ids_survive_restart(self):
        saver = lg_checkpoint.get_checkpointer()
        conn = lg_checkpoint._CONN
        conn.execute("INSERT INTO checkpoints(thread_id,checkpoint_ns,checkpoint_id) VALUES('kept','','1')").close()
        conn.commit()
        lg_checkpoint.close_checkpointer()
        self.assertTrue(conn.closed)
        self.assertIsNone(lg_checkpoint._SAVER)
        self.assertIsNone(lg_checkpoint._CONN)
        lg_checkpoint.close_checkpointer()
        self.assertIsNot(saver, lg_checkpoint.get_checkpointer())
        self.assertEqual(["kept"], lg_checkpoint.list_thread_ids())

    def test_checkpoint_retirement_does_not_interrupt_its_existing_read_cursor(self):
        lg_checkpoint.get_checkpointer()
        conn = lg_checkpoint._CONN
        cursor = conn.execute("SELECT 41")
        lg_checkpoint.close_checkpointer()
        self.assertFalse(conn.closed)
        self.assertEqual((41,), cursor.fetchone())
        cursor.close()
        self.assertTrue(conn.closed)

    def test_checkpoint_pragma_failure_leaves_no_half_initialized_singleton(self):
        real_connect, captured = sqlite3.connect, []

        class FailingConnection(ManagedConnection):
            def execute(self, *args, **kwargs):
                raise sqlite3.OperationalError("synthetic checkpoint pragma failure")

        def connect(*args, **kwargs):
            kwargs["factory"] = FailingConnection
            conn = real_connect(*args, **kwargs)
            captured.append(conn)
            return conn

        with patch.object(lg_checkpoint.sqlite3, "connect", side_effect=connect):
            with self.assertRaisesRegex(sqlite3.OperationalError, "synthetic checkpoint pragma failure"):
                lg_checkpoint.get_checkpointer()
        self.assertIsNone(lg_checkpoint._CONN)
        self.assertIsNone(lg_checkpoint._SAVER)
        self.assertTrue(captured[0].closed)

    def test_interrupted_checkpoint_setup_closes_the_unpublished_handle(self):
        from langgraph.checkpoint.sqlite import SqliteSaver
        real_connect, captured = sqlite3.connect, []

        def connect(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            captured.append(conn)
            return conn

        with patch.object(lg_checkpoint.sqlite3, "connect", side_effect=connect), \
                patch.object(SqliteSaver, "setup", side_effect=KeyboardInterrupt("synthetic interruption")):
            with self.assertRaises(KeyboardInterrupt):
                lg_checkpoint.get_checkpointer()
        self.assertTrue(captured[0].closed)
        self.assertIsNone(lg_checkpoint._CONN)
        self.assertIsNone(lg_checkpoint._SAVER)

    def test_retryable_checkpoint_setup_failure_retains_a_real_saver_not_memory(self):
        from langgraph.checkpoint.sqlite import SqliteSaver
        original = SqliteSaver.setup
        calls = []

        def first_failure(saver):
            calls.append(1)
            if len(calls) == 1:
                raise sqlite3.OperationalError("synthetic setup busy")
            return original(saver)

        with patch.object(SqliteSaver, "setup", first_failure):
            with self.assertLogs("civil.lg_checkpoint", level="WARNING"):
                saver = lg_checkpoint.get_checkpointer()
            self.assertIsInstance(saver, SqliteSaver)
            self.assertFalse(lg_checkpoint._CONN.closed)
            with saver.cursor(transaction=False) as cursor:
                self.assertEqual((43,), cursor.execute("SELECT 43").fetchone())
        self.assertTrue(saver.is_setup)
        self.assertEqual(2, len(calls))
        lg_checkpoint.close_checkpointer()
        self.assertTrue(saver.conn.closed)

    def test_list_query_failure_still_closes_temporary_connection(self):
        with closing(sqlite3.connect(self.db)):
            pass  # Existing file with no checkpoint table.
        real_connect, captured = sqlite3.connect, []

        def connect(*args, **kwargs):
            kwargs["factory"] = ManagedConnection
            conn = real_connect(*args, **kwargs)
            captured.append(conn)
            return conn

        with warnings.catch_warnings(record=True) as caught, patch.object(lg_checkpoint.sqlite3, "connect", side_effect=connect):
            warnings.simplefilter("always", ResourceWarning)
            self.assertEqual([], lg_checkpoint.list_thread_ids())
            gc.collect()
        self.assertTrue(captured[0].closed)
        self.assertFalse([w for w in caught if issubclass(w.category, ResourceWarning)])

    def test_list_success_closes_both_cursor_and_temporary_connection(self):
        lg_checkpoint.get_checkpointer()
        real_connect, captured = sqlite3.connect, []

        def connect(*args, **kwargs):
            kwargs["factory"] = ManagedConnection
            conn = real_connect(*args, **kwargs)
            captured.append(conn)
            return conn

        with patch.object(lg_checkpoint.sqlite3, "connect", side_effect=connect):
            self.assertEqual([], lg_checkpoint.list_thread_ids())
        self.assertTrue(captured[0].closed)
        self.assertEqual(0, captured[0]._cursor_count)


if __name__ == "__main__":
    unittest.main(verbosity=2)
