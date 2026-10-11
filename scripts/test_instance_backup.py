"""Synthetic named-instance backup contracts; no actual product or user data."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import instance_backup as tool


class BackupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="civil-instance-backup-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.work = self.root / "中文 project with spaces"
        self.base = self.root / "private state"
        self.user = "alice-1"
        self.binding = tool.identity(self.work, self.base, self.user)
        self.leaf = Path(self.binding["instance_state"])
        self.work.mkdir()
        self.leaf.mkdir(parents=True)
        (self.work / ".civil-buddy").mkdir()
        self.owner(self.leaf / "identity.sqlite", "instance_owner", "workspace_root", str(self.work))
        self.owner(self.work / ".civil-buddy/instance-owner.sqlite", "workspace_owner", "state_root", str(self.leaf))
        (self.leaf / "host.lock").write_bytes(b"0")
        (self.leaf / "runtime-owner.lock").write_bytes(b"nonempty-coordination-fixture")
        (self.work / "original.txt").write_text("immutable synthetic project material", encoding="utf-8")
        (self.work / "empty directory/empty child").mkdir(parents=True)
        (self.leaf / "empty state directory").mkdir()
        (self.leaf / "history.json").write_text('{"turns": [1, 2]}', encoding="utf-8")
        other = self.base / "accounts/bob/projects/not-selected"
        other.mkdir(parents=True)
        (other / "other-account.txt").write_bytes(b"never copied or modified")
        self.output = self.root / "backup copy"

    def owner(self, path, table, column, value, user=None):
        with contextlib.closing(sqlite3.connect(path)) as db:
            db.execute(f"CREATE TABLE {table}(id INTEGER PRIMARY KEY CHECK(id=1),user_id TEXT NOT NULL,{column} TEXT NOT NULL)")
            db.execute(f"INSERT INTO {table} VALUES(1,?,?)", (self.user if user is None else user, value))
            db.commit()

    def save(self, **kwargs):
        return tool.backup(workspace=self.work, state_root=self.base, user_id=self.user,
                           output=kwargs.pop("output", self.output), **kwargs)

    def restore(self, **kwargs):
        return tool.restore(backup_path=kwargs.pop("backup_path", self.output), workspace=kwargs.pop("workspace", self.work),
                            state_root=kwargs.pop("state_root", self.base), user_id=kwargs.pop("user_id", self.user), **kwargs)

    def hashes(self, root=None):
        root = self.root if root is None else root
        return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in root.rglob("*") if path.is_file()}

    def retain_originals(self):
        for source, target in ((self.work, self.root / "retained project"), (self.leaf, self.root / "retained named state")):
            self.assertTrue(source.is_relative_to(self.root) and target.is_relative_to(self.root))
            source.rename(target)

    def update_manifest(self, transform):
        path = self.output / "manifest.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        transform(value)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def test_end_to_end_retains_other_accounts_empty_directories_and_original_bytes(self):
        before = self.hashes()
        saved = self.save()
        self.assertTrue(saved["passed"])
        self.assertEqual({path: digest for path, digest in self.hashes().items() if not path.startswith("backup copy/")}, before)
        manifest, _ = tool.validate_backup(self.output)
        self.assertEqual(manifest["identity"], self.binding)
        self.assertEqual(manifest["omitted_coordination"], ["state/host.lock", "state/runtime-owner.lock"])
        self.assertFalse((self.output / "state/host.lock").exists())
        self.assertFalse((self.output / "state/runtime-owner.lock").exists())
        self.assertFalse((self.output / "state/accounts").exists())
        self.assertTrue((self.output / "workspace/empty directory/empty child").is_dir())
        self.retain_originals()
        result = self.restore()
        self.assertTrue(result["restored"])
        for path, digest in before.items():
            if path.endswith("host.lock") or path.endswith("runtime-owner.lock"):
                continue
            self.assertEqual(hashlib.sha256((self.root / path).read_bytes()).hexdigest(), digest)
        self.assertTrue((self.work / "empty directory/empty child").is_dir())
        self.assertTrue((self.leaf / "empty state directory").is_dir())
        self.assertTrue((self.root / "retained project/original.txt").is_file())

    def test_cli_emits_only_summary_json_and_verify_is_read_only(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(tool.main(["backup", "--workspace", str(self.work), "--state-root", str(self.base),
                                       "--user-id", self.user, "--output", str(self.output)]), 0)
        value = json.loads(output.getvalue())
        self.assertEqual(value["operation"], "backup")
        self.assertNotIn("immutable synthetic", output.getvalue())
        before = self.hashes()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(tool.main(["verify", "--backup", str(self.output)]), 0)
        self.assertEqual(json.loads(output.getvalue())["operation"], "verify")
        self.assertEqual(before, self.hashes())
        for arguments in (["verify"], ["backup", "--workspace", "relative"], ["not-an-operation"]):
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()) as error:
                self.assertEqual(tool.main(arguments), 2)
            self.assertFalse(json.loads(output.getvalue())["passed"])
            self.assertEqual(error.getvalue(), "")

    def test_restore_refuses_nonempty_targets_without_overwriting_any_file(self):
        self.save()
        before = self.hashes()
        with self.assertRaisesRegex(tool.BackupError, "restore_target_not_empty"):
            self.restore()
        self.assertEqual(before, self.hashes())
        self.retain_originals()
        self.work.mkdir()
        (self.work / "existing.txt").write_bytes(b"never overwritten")
        before = self.hashes()
        with self.assertRaisesRegex(tool.BackupError, "restore_target_not_empty"):
            self.restore()
        self.assertEqual(before, self.hashes())
        self.assertFalse(self.leaf.exists())

    def test_corrupt_missing_extra_and_malformed_backups_refuse_before_restore_target_writes(self):
        self.save()
        self.retain_originals()
        original = self.output / "workspace/original.txt"
        original_bytes = original.read_bytes()
        manifest = (self.output / "manifest.json").read_bytes()
        mutations = ("corrupt", "missing", "extra", "extra-root", "manifest", "missing-owner")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                if mutation == "corrupt":
                    original.write_bytes(b"same invalid material")
                elif mutation == "missing":
                    original.unlink()
                elif mutation == "extra":
                    (self.output / "workspace/unlisted.txt").write_bytes(b"unexpected")
                elif mutation == "extra-root":
                    (self.output / "surprise.txt").write_bytes(b"unexpected")
                elif mutation == "manifest":
                    (self.output / "manifest.json").write_bytes(b"not JSON")
                else:
                    (self.output / "state/identity.sqlite").rename(self.output / "removed-owner.sqlite")
                before = self.hashes()
                with self.assertRaises((tool.BackupError, OSError)):
                    self.restore()
                self.assertEqual(before, self.hashes())
                self.assertFalse(self.work.exists())
                self.assertFalse(self.leaf.exists())
                for extra in (self.output / "workspace/unlisted.txt", self.output / "surprise.txt"):
                    extra.unlink(missing_ok=True)
                if (self.output / "removed-owner.sqlite").exists():
                    (self.output / "removed-owner.sqlite").rename(self.output / "state/identity.sqlite")
                original.write_bytes(original_bytes)
                (self.output / "manifest.json").write_bytes(manifest)

    def test_manifest_unsafe_colliding_or_tampered_identity_paths_fail_before_targets_exist(self):
        self.save()
        self.retain_originals()
        path = self.output / "manifest.json"
        initial = path.read_bytes()
        for unsafe in ("workspace/../outside", "workspace/CON.txt", "workspace/trailing ", "workspace/C:stream",
                       "workspace/a\\b", "/absolute", "state/host.lock", "workspace//bad"):
            path.write_bytes(initial)
            self.update_manifest(lambda value: value["entries"].append({"path": unsafe, "type": "directory"}))
            with self.subTest(unsafe=unsafe), self.assertRaises(tool.BackupError):
                self.restore()
            self.assertFalse(self.work.exists())
            self.assertFalse(self.leaf.exists())
        path.write_bytes(initial)
        self.update_manifest(lambda value: value["entries"].append({"path": "workspace/ORIGINAL.TXT", "type": "file",
                                                                   "size": 0, "sha256": "0" * 64}))
        with self.assertRaisesRegex(tool.BackupError, "colliding_paths"):
            self.restore()
        path.write_bytes(initial)
        self.update_manifest(lambda value: value["identity"].update(user_id="bob"))
        with self.assertRaises(tool.BackupError):
            self.restore()
        self.assertFalse(self.work.exists())
        self.assertFalse(self.leaf.exists())

    def test_manifest_or_backup_root_mutation_during_validation_refuses_before_restore_targets(self):
        self.save()
        self.retain_originals()
        original = tool.inspect_databases
        manifest = self.output / "manifest.json"
        saved = manifest.read_bytes()
        for mutation in ("manifest", "extra-root"):
            def mutate(*args, **kwargs):
                original(*args, **kwargs)
                if mutation == "manifest":
                    self.update_manifest(lambda value: value.update(created_at_utc="concurrent replacement"))
                else:
                    (self.output / "concurrent extra file").write_bytes(b"unlisted")
            with self.subTest(mutation=mutation), patch.object(tool, "inspect_databases", side_effect=mutate):
                with self.assertRaises(tool.BackupError):
                    self.restore()
            self.assertFalse(self.work.exists())
            self.assertFalse(self.leaf.exists())
            manifest.write_bytes(saved)
            (self.output / "concurrent extra file").unlink(missing_ok=True)

    def test_original_paths_and_user_are_mandatory_and_base_state_is_not_leaf(self):
        self.save()
        self.retain_originals()
        for kwargs in ({"workspace": self.root / "different workspace"}, {"state_root": self.root / "different base"},
                       {"user_id": "bob"}, {"state_root": self.leaf}):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(tool.BackupError, "restore_original_identity_required"):
                self.restore(**kwargs)
            self.assertFalse(self.work.exists())
            self.assertFalse(self.leaf.exists())
        for user in ("_bad", "-bad", "", "a" * 65, "a/b", "CON"):
            with self.subTest(user=user), self.assertRaises(tool.BackupError):
                tool.identity(self.work, self.base, user)

    def test_current_owner_rows_must_match_both_databases(self):
        for relative, table, column in ((self.leaf / "identity.sqlite", "instance_owner", "workspace_root"),
                                        (self.work / ".civil-buddy/instance-owner.sqlite", "workspace_owner", "state_root")):
            with contextlib.closing(sqlite3.connect(relative)) as db:
                db.execute(f"UPDATE {table} SET user_id='different-user'")
                db.commit()
            output = self.root / ("invalid owner " + table)
            before = self.hashes()
            with self.assertRaisesRegex(tool.BackupError, "owner_identity_mismatch"):
                self.save(output=output)
            self.assertFalse((output / "manifest.json").exists())
            self.assertTrue((output / "incomplete.json").exists())
            self.assertEqual({path: digest for path, digest in self.hashes().items() if not path.startswith(output.name + "/")}, before)
            with contextlib.closing(sqlite3.connect(relative)) as db:
                db.execute(f"UPDATE {table} SET user_id=?", (self.user,))
                db.commit()

    def test_owner_inspection_rejects_many_rows_and_oversized_identity_fields_with_bounded_query(self):
        path = self.leaf / "identity.sqlite"
        path.unlink()
        with contextlib.closing(sqlite3.connect(path)) as db:
            db.execute("CREATE TABLE instance_owner(id INTEGER PRIMARY KEY,user_id TEXT,workspace_root TEXT)")
            db.executemany("INSERT INTO instance_owner VALUES(?,?,?)",
                           ((index, self.user, str(self.work)) for index in range(1, 1001)))
            db.commit()
        with self.assertRaisesRegex(tool.BackupError, "owner_identity_mismatch"):
            self.save(output=self.root / "multiple owners")
        with contextlib.closing(sqlite3.connect(path)) as db:
            db.execute("DELETE FROM instance_owner WHERE id != 1")
            db.execute("UPDATE instance_owner SET workspace_root=?", ("x" * (tool.MAX_OWNER_ROW_BYTES + 1),))
            db.commit()
        with self.assertRaisesRegex(tool.BackupError, "sqlite_integrity_or_owner_read_failed"):
            self.save(output=self.root / "huge owner field")
        # The owner-row bound must not restrict ordinary project document blobs.
        with contextlib.closing(sqlite3.connect(path)) as db:
            db.execute("UPDATE instance_owner SET workspace_root=?", (str(self.work),))
            db.commit()
        with contextlib.closing(sqlite3.connect(self.leaf / "runtime.sqlite")) as db:
            db.execute("CREATE TABLE turns(id INTEGER PRIMARY KEY,document BLOB)")
            db.execute("INSERT INTO turns VALUES(1,?)", (b"d" * (tool.MAX_OWNER_ROW_BYTES + 1),))
            db.commit()
        self.assertTrue(self.save()["passed"])

    def test_wal_owner_and_runtime_rows_are_checked_without_touching_raw_sources_or_backup(self):
        owner = self.leaf / "identity.sqlite"
        owner.unlink()
        connection = sqlite3.connect(owner)
        self.addCleanup(connection.close)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE instance_owner(id INTEGER PRIMARY KEY,user_id TEXT,workspace_root TEXT)")
        connection.execute("INSERT INTO instance_owner VALUES(1,?,?)", (self.user, str(self.work)))
        connection.commit()
        database = self.leaf / "runtime.sqlite"
        runtime = sqlite3.connect(database)
        self.addCleanup(runtime.close)
        runtime.execute("PRAGMA journal_mode=WAL")
        runtime.execute("CREATE TABLE turns(id INTEGER PRIMARY KEY,content TEXT)")
        runtime.execute("INSERT INTO turns VALUES(1,'committed in WAL')")
        runtime.commit()
        self.assertTrue(Path(str(owner) + "-wal").is_file())
        before = self.hashes()
        self.save()
        for path, digest in before.items():
            self.assertEqual(hashlib.sha256((self.root / path).read_bytes()).hexdigest(), digest)
        backup_before = self.hashes(self.output)
        tool.validate_backup(self.output)
        self.assertEqual(backup_before, self.hashes(self.output))
        for suffix in ("", "-wal", "-shm"):
            self.assertEqual(Path(str(owner) + suffix).read_bytes(), (self.output / ("state/identity.sqlite" + suffix)).read_bytes())
        connection.execute("UPDATE instance_owner SET user_id='other-wal-owner'")
        connection.commit()
        with self.assertRaisesRegex(tool.BackupError, "owner_identity_mismatch"):
            self.save(output=self.root / "wrong WAL owner")

    @unittest.skipUnless(os.name == "nt", "Rust Windows verbatim-drive ownership spelling")
    def test_rust_verbatim_drive_owner_records_match_exact_original_targets(self):
        for path, table, column, value in ((self.leaf / "identity.sqlite", "instance_owner", "workspace_root", self.work),
                                            (self.work / ".civil-buddy/instance-owner.sqlite", "workspace_owner", "state_root", self.leaf)):
            with contextlib.closing(sqlite3.connect(path)) as db:
                db.execute(f"UPDATE {table} SET {column}=?", ("\\\\?\\" + str(value),))
                db.commit()
        self.assertTrue(self.save()["passed"])

    def test_recognized_credential_paths_refuse_before_any_payload_read(self):
        for name in (".env", ".env.local", "provider.env", "id_rsa", "private.pem", "api_key.json", "login-token.txt",
                     "login_token.backup", "provider-config.toml", ".ssh"):
            path = self.work / name
            if name == ".ssh":
                path.mkdir()
                (path / "config").write_bytes(b"synthetic secret should not be read")
            else:
                path.write_bytes(b"synthetic secret should not be read")
            with self.subTest(name=name), patch.object(tool, "open_read") as read:
                with self.assertRaisesRegex(tool.BackupError, "recognized_credential_path"):
                    self.save()
                read.assert_not_called()
                self.assertFalse(self.output.exists())
            if path.is_dir():
                (path / "config").unlink()
                path.rmdir()
            else:
                path.unlink()

    def test_hardlinks_and_nested_instance_records_refuse_before_output_creation(self):
        linked = self.work / "hardlink.txt"
        os.link(self.work / "original.txt", linked)
        with self.assertRaisesRegex(tool.BackupError, "hardlinked_file"):
            self.save()
        self.assertFalse(self.output.exists())
        linked.unlink()
        nested = self.work / "nested/.civil-buddy"
        nested.mkdir(parents=True)
        (nested / "instance-owner.sqlite").write_bytes(b"foreign owner record must not be read")
        with self.assertRaisesRegex(tool.BackupError, "nested_instance_ownership"):
            self.save()
        self.assertFalse(self.output.exists())

    def test_owned_ancestor_and_wrong_side_markers_refuse_before_copy_or_restore(self):
        for target, private in ((self.work, False), (self.leaf, True)):
            wrong = target / (".civil-buddy/instance-owner.sqlite" if private else "identity.sqlite")
            wrong.parent.mkdir(exist_ok=True)
            wrong.write_bytes(b"foreign ownership record should never be opened")
            with patch.object(tool, "open_read") as read, self.assertRaisesRegex(tool.BackupError, "wrong_side_instance_ownership"):
                self.save()
            read.assert_not_called()
            self.assertFalse(self.output.exists())
            wrong.unlink()
            if private:
                wrong.parent.rmdir()
        self.save()
        self.retain_originals()
        for relative in (".civil-buddy/instance-owner.sqlite", "identity.sqlite"):
            marker = self.root / relative
            marker.parent.mkdir(exist_ok=True)
            marker.write_bytes(b"new ancestor owner record must not be read")
            before = self.hashes()
            with self.subTest(relative=relative), self.assertRaisesRegex(tool.BackupError, "ancestor_instance_ownership"):
                self.restore()
            self.assertFalse(self.work.exists())
            self.assertFalse(self.leaf.exists())
            self.assertEqual(before, self.hashes())
            # Verification deliberately remains independent of original paths.
            tool.validate_backup(self.output)
            marker.unlink()
            if relative.startswith(".civil-buddy/"):
                marker.parent.rmdir()

    def test_damaged_fixed_product_database_and_orphan_sidecar_are_invalid(self):
        for name in ("runtime.sqlite", "index.sqlite", "ordinary.sqlite-wal"):
            path = self.leaf / name
            path.write_bytes(b"damaged database header or orphan WAL")
            output = self.root / ("damaged " + name)
            with self.subTest(name=name), self.assertRaisesRegex(tool.BackupError, "product_database_invalid|orphan_sqlite_sidecar"):
                self.save(output=output)
            self.assertTrue((output / "incomplete.json").exists())
            with self.assertRaises(tool.BackupError):
                tool.validate_backup(output)
            path.unlink()
        # Recomputing every manifest hash cannot make a corrupt product DB valid.
        runtime = self.leaf / "runtime.sqlite"
        with contextlib.closing(sqlite3.connect(runtime)) as db:
            db.execute("CREATE TABLE turns(id INTEGER PRIMARY KEY)")
            db.commit()
        self.save()
        self.retain_originals()
        copied = self.output / "state/runtime.sqlite"
        copied.write_bytes(b"opaque bytes falsely presented as runtime database")
        digest = hashlib.sha256(copied.read_bytes()).hexdigest()
        def update(value):
            for row in value["entries"]:
                if row["path"] == "state/runtime.sqlite":
                    row.update(size=copied.stat().st_size, sha256=digest)
        self.update_manifest(update)
        before = self.hashes()
        with self.assertRaisesRegex(tool.BackupError, "product_database_invalid"):
            self.restore()
        self.assertFalse(self.work.exists())
        self.assertFalse(self.leaf.exists())
        self.assertEqual(before, self.hashes())

    def test_linked_source_and_backup_are_refused(self):
        target = self.root / "outside synthetic material"
        target.mkdir()
        linked = self.work / "link"
        try:
            os.symlink(target, linked, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                raise
            result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(linked), str(target)],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            if result.returncode:
                self.skipTest("Neither symlink nor junction fixture is available")
        try:
            with self.assertRaisesRegex(tool.BackupError, "linked_path"):
                self.save()
            self.assertFalse(self.output.exists())
        finally:
            os.rmdir(linked) if os.name == "nt" else linked.unlink()

    def test_interrupted_or_changed_copy_is_retained_incomplete_not_valid(self):
        original = tool.copy_file
        count = 0
        def interrupted(*args, **kwargs):
            nonlocal count
            count += 1
            result = original(*args, **kwargs)
            if count == 1:
                raise tool.BackupError("synthetic_interruption")
            return result
        before = self.hashes()
        with patch.object(tool, "copy_file", side_effect=interrupted), self.assertRaises(tool.BackupError):
            self.save()
        self.assertTrue((self.output / "incomplete.json").exists())
        with self.assertRaises(tool.BackupError):
            tool.validate_backup(self.output)
        self.assertEqual({path: digest for path, digest in self.hashes().items() if not path.startswith("backup copy/")}, before)
        count = 0
        second = self.root / "changed-source backup"
        def changed(source, destination, *args, **kwargs):
            nonlocal count
            result = original(source, destination, *args, **kwargs)
            count += 1
            if count == 1:
                (self.work / "original.txt").write_bytes(b"concurrent mutation detected")
            return result
        with patch.object(tool, "copy_file", side_effect=changed), self.assertRaises(tool.BackupError):
            self.save(output=second)
        self.assertTrue((second / "incomplete.json").exists())
        with self.assertRaises(tool.BackupError):
            tool.validate_backup(second)

    def test_path_replacement_race_and_destination_collision_fail_closed(self):
        original = tool.copy_file
        raced = False
        def changed(source, destination, expected, *args, **kwargs):
            nonlocal raced
            if not raced:
                raced = True
                saved = source.read_bytes()
                source.unlink()
                source.write_bytes(saved)
            return original(source, destination, expected, *args, **kwargs)
        with patch.object(tool, "copy_file", side_effect=changed), self.assertRaisesRegex(tool.BackupError, "file_changed"):
            self.save()
        output = self.root / "destination race"
        def collided(source, destination, *args, **kwargs):
            destination.write_bytes(b"unexpected file never overwritten")
            return original(source, destination, *args, **kwargs)
        with patch.object(tool, "copy_file", side_effect=collided), self.assertRaises(FileExistsError):
            self.save(output=output)
        self.assertIn(b"unexpected file never overwritten", [path.read_bytes() for path in output.rglob("*") if path.is_file()])
        with self.assertRaises(tool.BackupError):
            tool.validate_backup(output)

    def test_output_overlap_existing_output_and_bounded_inventory_are_refused(self):
        for output in (self.work / "backup", self.base / "backup", self.work, self.base, self.root):
            with self.subTest(output=output), self.assertRaises(tool.BackupError):
                self.save(output=output)
        for limit, value in (("MAX_ENTRIES", 2), ("MAX_BYTES", 10), ("MAX_FILE_BYTES", 10)):
            with self.subTest(limit=limit), patch.object(tool, limit, value), self.assertRaises(tool.BackupError):
                self.save()
            self.assertFalse(self.output.exists())

    @contextlib.contextmanager
    def held_lock(self, path, rust):
        code = r'''import ctypes,os,sys
from ctypes import wintypes
f=open(sys.argv[1],'rb')
if os.name=='nt':
 import msvcrt
 if sys.argv[2]=='rust':
  class O(ctypes.Structure):
   _fields_=[('a',ctypes.c_size_t),('b',ctypes.c_size_t),('c',wintypes.DWORD),('d',wintypes.DWORD),('e',wintypes.HANDLE)]
  k=ctypes.WinDLL('kernel32',use_last_error=True)
  k.LockFileEx.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(O)]
  k.LockFileEx.restype=wintypes.BOOL
  region=O()
  assert k.LockFileEx(msvcrt.get_osfhandle(f.fileno()),3,0,0xffffffff,0xffffffff,ctypes.byref(region))
 else: msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
else:
 import fcntl
 fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
open(sys.argv[3],'x').close()
sys.stdin.readline()
f.close()
'''
        ready = self.root / "lock fixture ready"
        process = subprocess.Popen([sys.executable, "-I", "-B", "-c", code, str(path), "rust" if rust else "launcher", str(ready)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.exists(), "Independent OS-lock fixture did not acquire its lock within 5 seconds")
            yield
        finally:
            if process.poll() is None:
                try:
                    process.communicate(b"\n", timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)
            self.assertEqual(process.wait(timeout=10), 0)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
            ready.unlink(missing_ok=True)

    def test_independent_launcher_and_rust_locks_refuse_backup_without_source_mutation(self):
        before = self.hashes()
        for name in tool.COORDINATION:
            with self.subTest(name=name), self.held_lock(self.leaf / name, name == "runtime-owner.lock"):
                with self.assertRaisesRegex(tool.BackupError, "instance_is_running_or_lock_unavailable"):
                    self.save()
                self.assertFalse(self.output.exists())
            self.assertEqual(before, self.hashes())

    def test_independent_restore_lock_refusal_creates_no_workspace_or_other_missing_lock(self):
        self.save()
        self.retain_originals()
        self.leaf.mkdir()
        owner = self.leaf / "runtime-owner.lock"
        owner.write_bytes(b"direct Rust host guard")
        before = self.hashes()
        with self.held_lock(owner, True), self.assertRaisesRegex(tool.BackupError, "instance_is_running_or_lock_unavailable"):
            self.restore()
        self.assertEqual(before, self.hashes())
        self.assertFalse((self.leaf / "host.lock").exists())
        self.assertFalse(self.work.exists())


if __name__ == "__main__":
    unittest.main()
