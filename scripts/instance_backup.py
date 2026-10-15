"""Offline, original-path-only backup/restore for one named Civil Buddy instance.

No product is started or stopped. Both launcher and Rust owner locks are held.
Payload limits: 100000 entries, 20 GiB total, 4 GiB per file. Manifest: 32 MiB.
SQLite inspection uses disposable copies, at most 256 databases and 60 seconds.
Owner queries read at most two rows and reject rows larger than 1 MiB.
Recognized credential paths are rejected before payload contents are read; this
is filename screening, not detection of credentials embedded in project files.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import sys
import tempfile
import time
import unicodedata

PRODUCT = "civil-buddy-named-instance-backup"
MAX_ENTRIES = 100000
MAX_BYTES = 20 * 1024 ** 3
MAX_FILE_BYTES = 4 * 1024 ** 3
MAX_MANIFEST_BYTES = 32 * 1024 ** 2
CHUNK = 1024 ** 2
MAX_DATABASES = 256
MAX_OWNER_ROW_BYTES = 1024 ** 2
SQLITE_SECONDS = 60
COORDINATION = ("host.lock", "runtime-owner.lock")
CREDENTIAL_NAMES = frozenset({".env", ".netrc", "_netrc", ".pypirc", ".npmrc", "credentials",
    "credentials.json", "credentials.yaml", "credentials.yml", "secrets.json", "secrets.yaml", "secrets.yml",
    "provider-config.json", "api-key.txt", "apikey.txt", "token.txt", "login.token", "access.token",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".ssh", ".aws", ".azure", ".gnupg", "gcloud"})
CREDENTIAL_SUFFIXES = (".env", ".key", ".pem", ".p12", ".pfx", ".kdbx", ".token")
CREDENTIAL_PREFIXES = (".env.", "login-token", "login_token", "provider-config", "provider_config",
                       "api-key", "api_key", "apikey")
RESERVED = {"CON", "PRN", "AUX", "NUL", "CLOCK$", "CONIN$", "CONOUT$"} | {
    prefix + str(number) for prefix in ("COM", "LPT") for number in range(1, 10)} | {
    prefix + number for prefix in ("COM", "LPT") for number in "¹²³"}


class BackupError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def fail(code: str) -> None:
    raise BackupError(code)


def valid_name(name: str, *, screen_credentials: bool = True) -> None:
    if (not name or name in {".", ".."} or len(name.encode("utf-8")) > 255 or name[-1:] in {".", " "}
            or any(ord(char) < 32 or char in '<>:"/\\|?*' for char in name)
            or name.split(".", 1)[0].upper() in RESERVED):
        fail("unsafe_path")
    lowered = name.casefold()
    if screen_credentials and (lowered in CREDENTIAL_NAMES or lowered.startswith(CREDENTIAL_PREFIXES)
                               or lowered.endswith(CREDENTIAL_SUFFIXES)):
        fail("recognized_credential_path")


def reject_links(path: Path) -> None:
    for item in reversed((path, *path.parents)):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            fail("linked_path")
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            fail("hardlinked_file")


def absolute_path(value: str | Path, *, resolve: bool = True) -> Path:
    path = Path(value)
    if not path.is_absolute() or (os.name == "nt" and path.drive.startswith("\\\\")):
        fail("absolute_local_path_required")
    for name in path.parts[1:]:
        valid_name(name, screen_credentials=False)
    if resolve:
        reject_links(path)
        path = path.resolve(strict=False)
        reject_links(path)
    elif str(path) != os.path.abspath(path):
        fail("noncanonical_identity_path")
    return path


def overlap(first: Path, second: Path) -> bool:
    return first.is_relative_to(second) or second.is_relative_to(first)


def identity(workspace: str | Path, base: str | Path, user: str, *, resolve: bool = True) -> dict:
    if not isinstance(user, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", user):
        fail("invalid_user_id")
    valid_name(user)
    work = absolute_path(workspace, resolve=resolve)
    state = absolute_path(base, resolve=resolve)
    if overlap(work, state):
        fail("workspace_state_overlap")
    digest = hashlib.sha256(os.path.normcase(str(work)).encode("utf-8")).hexdigest()
    leaf = state / "accounts" / user / "projects" / digest
    if resolve:
        reject_links(leaf)
        leaf = leaf.resolve(strict=False)
    return {"user_id": user, "workspace": str(work), "state_root": str(state),
            "instance_state": str(leaf), "workspace_sha256": digest}


def signature(info: os.stat_result) -> tuple:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)


def ownership_boundaries(workspace: Path, state: Path) -> None:
    """Match the launcher's directory separation guards without reading records."""
    def present(path: Path) -> bool:
        try:
            path.lstat()
            return True
        except FileNotFoundError:
            return False
    marker = Path(".civil-buddy/instance-owner.sqlite")
    for root, private in ((workspace, False), (state, True)):
        reject_links(root)
        if present(root / (marker if private else Path("identity.sqlite"))):
            fail("wrong_side_instance_ownership")
        for parent in root.parents:
            if present(parent / marker) or present(parent / "identity.sqlite"):
                fail("ancestor_instance_ownership")


def regular(path: Path) -> os.stat_result:
    reject_links(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        fail("regular_file_required")
    if info.st_size > MAX_FILE_BYTES:
        fail("file_size_limit")
    return info


@contextmanager
def open_read(path: Path, expected: tuple | None = None):
    info = regular(path)
    if expected is not None and signature(info) != expected:
        fail("file_changed")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        if signature(os.fstat(fd)) != signature(info):
            fail("file_changed")
        reject_links(path)
        with os.fdopen(fd, "rb", closefd=False) as stream:
            yield stream
        if signature(os.fstat(fd)) != signature(info) or signature(regular(path)) != signature(info):
            fail("file_changed")
    finally:
        os.close(fd)


def hash_file(path: Path, expected: tuple | None = None) -> str:
    result = hashlib.sha256()
    size = regular(path).st_size
    count = 0
    with open_read(path, expected) as source:
        while block := source.read(CHUNK):
            count += len(block)
            if count > size or count > MAX_FILE_BYTES:
                fail("file_changed_or_limit")
            result.update(block)
    if count != size:
        fail("file_changed")
    return result.hexdigest()


def copy_file(source: Path, target: Path, expected: tuple, expected_hash: str | None = None) -> str:
    reject_links(target)
    digest = hashlib.sha256()
    written = 0
    with open_read(source, expected) as stream:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
                     | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as output:
            while block := stream.read(CHUNK):
                written += len(block)
                if written > MAX_FILE_BYTES or written > expected[3]:
                    fail("file_changed_or_limit")
                digest.update(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
    if written != expected[3] or (expected_hash is not None and digest.hexdigest() != expected_hash):
        fail("file_hash_mismatch")
    reject_links(target)
    return digest.hexdigest()


def inventory(roots: dict[str, Path], *, omitted: tuple[str, ...] = ()) -> tuple[list[dict], dict[str, tuple]]:
    entries = []
    signatures = {}
    total = 0
    collision_keys = set()
    for label, root in roots.items():
        reject_links(root)
        if not root.is_dir():
            fail("source_directory_missing")
        pending = [(root, label)]
        while pending:
            folder, relative = pending.pop()
            info = folder.lstat()
            if not stat.S_ISDIR(info.st_mode):
                fail("directory_changed")
            signatures[relative] = signature(info)
            entries.append({"path": relative, "type": "directory"})
            with os.scandir(folder) as children:
                for child in children:
                    name = child.name
                    valid_name(name)
                    rel = relative + "/" + name
                    if ((name == "identity.sqlite" and rel != "state/identity.sqlite")
                            or (rel.endswith("/.civil-buddy/instance-owner.sqlite")
                                and rel != "workspace/.civil-buddy/instance-owner.sqlite")):
                        fail("nested_instance_ownership")
                    key = unicodedata.normalize("NFC", rel).casefold()
                    if key in collision_keys:
                        fail("colliding_paths")
                    collision_keys.add(key)
                    # DirEntry's cached Windows attributes have no reliable inode
                    # or link count. Security checks require a fresh filesystem stat.
                    details = Path(child.path).lstat()
                    if stat.S_ISLNK(details.st_mode) or getattr(details, "st_file_attributes", 0) & 0x400:
                        fail("linked_path")
                    if rel in omitted:
                        if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
                            fail("invalid_coordination_file")
                        continue
                    if len(entries) + len(pending) >= MAX_ENTRIES:
                        fail("entry_limit")
                    if stat.S_ISDIR(details.st_mode):
                        pending.append((Path(child.path), rel))
                    elif stat.S_ISREG(details.st_mode):
                        if details.st_nlink != 1:
                            fail("hardlinked_file")
                        if details.st_size > MAX_FILE_BYTES:
                            fail("file_size_limit")
                        total += details.st_size
                        if total > MAX_BYTES:
                            fail("total_size_limit")
                        signatures[rel] = signature(details)
                        entries.append({"path": rel, "type": "file", "size": details.st_size})
                    else:
                        fail("special_file")
            reject_links(folder)
            if signature(folder.lstat()) != signature(info):
                fail("directory_changed")
    if len(entries) > MAX_ENTRIES:
        fail("entry_limit")
    return sorted(entries, key=lambda row: row["path"]), signatures


def path_for(roots: dict[str, Path], relative: str) -> Path:
    parts = PurePosixPath(relative).parts
    return roots[parts[0]].joinpath(*parts[1:])


def windows_lock(fd: int, *, whole_file: bool):
    import ctypes
    from ctypes import wintypes
    import msvcrt
    class Overlapped(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                    ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD), ("hEvent", wintypes.HANDLE)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped)]
    kernel.LockFileEx.restype = wintypes.BOOL
    region = Overlapped()
    low, high = (0xffffffff, 0xffffffff) if whole_file else (1, 0)
    if not kernel.LockFileEx(msvcrt.get_osfhandle(fd), 3, 0, low, high, ctypes.byref(region)):
        fail("instance_is_running_or_lock_unavailable")
    return region  # Keep OVERLAPPED storage alive for the handle lifetime.


@contextmanager
def instance_locks(state: Path, *, create: bool):
    """Existing locks are read-only; acquire every existing lock before creating any."""
    reject_links(state)
    with ExitStack() as stack:
        regions = []
        missing = []
        for name in COORDINATION:
            path = state / name
            reject_links(path)
            if not path.exists():
                if not create:
                    fail("coordination_lock_missing")
                missing.append(path)
                continue
            info = regular(path)
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
            stack.callback(os.close, fd)
            if signature(os.fstat(fd)) != signature(info):
                fail("lock_path_changed")
            if os.name == "nt":
                regions.append(windows_lock(fd, whole_file=name == "runtime-owner.lock"))
            else:
                import fcntl
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    fail("instance_is_running_or_lock_unavailable")
            reject_links(path)
            if signature(regular(path)) != signature(info):
                fail("lock_path_changed")
        for path in missing:
            reject_links(path)
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
                         | getattr(os, "O_NOFOLLOW", 0), 0o600)
            stack.callback(os.close, fd)
            if path.name == "host.lock":
                os.write(fd, b"0")
                os.lseek(fd, 0, os.SEEK_SET)
            if os.name == "nt":
                regions.append(windows_lock(fd, whole_file=path.name == "runtime-owner.lock"))
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def canonical_record(value: str) -> str:
    if not isinstance(value, str):
        fail("owner_identity_mismatch")
    # Rust Windows canonicalize stores verbatim-drive paths. Strip only that
    # spelling prefix; do not resolve or read original directories during verify.
    if os.name == "nt" and value.startswith("\\\\?\\"):
        value = value[4:]
    return os.path.normcase(str(absolute_path(value, resolve=False)))


def inspect_databases(roots: dict[str, Path], entries: list[dict], binding: dict) -> None:
    deadline = time.monotonic() + SQLITE_SECONDS
    databases = []
    file_rows = {row["path"]: row for row in entries if row["type"] == "file"}
    mandatory = {"state/identity.sqlite": ("instance_owner", "workspace_root", binding["workspace"]),
                 "workspace/.civil-buddy/instance-owner.sqlite": ("workspace_owner", "state_root", binding["instance_state"])}
    if not set(mandatory).issubset(file_rows):
        fail("owner_database_missing")
    for relative in file_rows:
        path = path_for(roots, relative)
        with open_read(path) as stream:
            header = stream.read(16)
        if header == b"SQLite format 3\x00":
            databases.append(relative)
        elif relative in mandatory:
            fail("owner_database_invalid")
        elif relative in {"state/runtime.sqlite", "state/index.sqlite"}:
            fail("product_database_invalid")
        if relative.endswith(("-wal", "-shm")) and relative[:-4] not in file_rows:
            fail("orphan_sqlite_sidecar")
    if any(relative.endswith(("-wal", "-shm")) and relative[:-4] not in databases for relative in file_rows):
        fail("orphan_sqlite_sidecar")
    if len(databases) > MAX_DATABASES:
        fail("sqlite_inspection_limit")
    with tempfile.TemporaryDirectory(prefix="civil-instance-inspection-") as directory:
        scratch = Path(directory)
        for index, relative in enumerate(databases):
            if time.monotonic() >= deadline:
                fail("sqlite_inspection_timeout")
            dbdir = scratch / str(index)
            dbdir.mkdir()
            source = path_for(roots, relative)
            copied = dbdir / "data.sqlite"
            for suffix in ("", "-wal", "-shm"):
                key = relative + suffix
                if key in file_rows:
                    item = path_for(roots, key)
                    copy_file(item, Path(str(copied) + suffix), signature(regular(item)))
            snapshot = dbdir / "snapshot.sqlite"
            def progress(status, remaining, total):
                if time.monotonic() >= deadline:
                    fail("sqlite_inspection_timeout")
                if snapshot.exists() and snapshot.stat().st_size > MAX_FILE_BYTES:
                    fail("sqlite_snapshot_size_limit")
            try:
                # Only disposable copies may construct/rebuild an SQLite SHM.
                # mode=ro reads committed WAL; immutable=1 alone would miss it.
                with closing(sqlite3.connect(copied.as_uri() + "?mode=ro", uri=True, timeout=0)) as reader:
                    with closing(sqlite3.connect(snapshot)) as target:
                        reader.backup(target, pages=256, progress=progress, sleep=0)
                with closing(sqlite3.connect(snapshot.as_uri() + "?mode=ro&immutable=1", uri=True, timeout=0)) as db:
                    db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 10000)
                    db.execute("PRAGMA trusted_schema=OFF")
                    if db.execute("PRAGMA quick_check(20)").fetchall() != [("ok",)]:
                        fail("sqlite_integrity_failed")
                    if relative in mandatory:
                        # A corrupt owner table must not allocate an unbounded
                        # result set or huge identity fields. Runtime blobs keep
                        # their normal SQLite limit and are not selected here.
                        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_OWNER_ROW_BYTES)
                        table, column, expected = mandatory[relative]
                        if db.execute("SELECT type FROM sqlite_schema WHERE name=?", (table,)).fetchall() != [("table",)]:
                            fail("owner_database_invalid")
                        rows = db.execute(f"SELECT id,user_id,{column} FROM {table} LIMIT 2").fetchall()
                        if (len(rows) != 1 or rows[0][0] != 1 or rows[0][1] != binding["user_id"]
                                or canonical_record(rows[0][2]) != canonical_record(expected)):
                            fail("owner_identity_mismatch")
            except sqlite3.Error:
                fail("sqlite_integrity_or_owner_read_failed")


def write_json_new(path: Path, value: dict) -> None:
    reject_links(path)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def read_manifest(folder: Path) -> dict:
    path = folder / "manifest.json"
    if regular(path).st_size > MAX_MANIFEST_BYTES:
        fail("manifest_size_limit")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                fail("duplicate_manifest_key")
            result[key] = value
        return result
    with open_read(path) as stream:
        try:
            manifest = json.loads(stream.read(MAX_MANIFEST_BYTES + 1), object_pairs_hook=unique)
        except (ValueError, UnicodeError):
            fail("manifest_invalid")
    expected = {"schema_version", "product", "complete", "created_at_utc", "identity", "entries", "omitted_coordination", "limits"}
    if (not isinstance(manifest, dict) or set(manifest) != expected or type(manifest["schema_version"]) is not int
            or manifest["schema_version"] != 1 or manifest["product"] != PRODUCT or manifest["complete"] is not True
            or not isinstance(manifest["created_at_utc"], str)
            or manifest["omitted_coordination"] != ["state/" + name for name in COORDINATION]
            or manifest["limits"] != limits()):
        fail("manifest_invalid")
    binding = manifest["identity"]
    if (not isinstance(binding, dict) or set(binding) != {"user_id", "workspace", "state_root", "instance_state", "workspace_sha256"}
            or binding != identity(binding.get("workspace", ""), binding.get("state_root", ""), binding.get("user_id", ""), resolve=False)):
        fail("manifest_identity_invalid")
    if not isinstance(manifest["entries"], list) or len(manifest["entries"]) > MAX_ENTRIES:
        fail("manifest_entry_limit")
    seen = set()
    total = 0
    rows = {}
    for row in manifest["entries"]:
        if not isinstance(row, dict) or row.get("type") not in {"file", "directory"}:
            fail("manifest_entry_invalid")
        fields = {"path", "type"} if row["type"] == "directory" else {"path", "type", "size", "sha256"}
        relative = row.get("path")
        if not isinstance(relative, str) or set(row) != fields:
            fail("manifest_entry_invalid")
        parts = relative.split("/")
        if parts[0] not in {"workspace", "state"} or PurePosixPath(relative).as_posix() != relative:
            fail("manifest_path_invalid")
        for part in parts:
            valid_name(part)
        if relative in {"state/" + name for name in COORDINATION}:
            fail("manifest_coordination_in_payload")
        key = unicodedata.normalize("NFC", relative).casefold()
        if key in seen:
            fail("colliding_paths")
        seen.add(key)
        rows[relative] = row
        if row["type"] == "file":
            if (type(row["size"]) is not int or not 0 <= row["size"] <= MAX_FILE_BYTES
                    or not isinstance(row["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", row["sha256"])):
                fail("manifest_file_invalid")
            total += row["size"]
    if total > MAX_BYTES:
        fail("total_size_limit")
    for relative, row in rows.items():
        if relative in {"workspace", "state"}:
            if row["type"] != "directory":
                fail("manifest_root_invalid")
        elif rows.get(str(PurePosixPath(relative).parent), {}).get("type") != "directory":
            fail("manifest_parent_missing")
    if not {"workspace", "state"}.issubset(rows):
        fail("manifest_root_invalid")
    return manifest


def limits() -> dict:
    return {"entries": MAX_ENTRIES, "total_bytes": MAX_BYTES, "file_bytes": MAX_FILE_BYTES,
            "manifest_bytes": MAX_MANIFEST_BYTES, "sqlite_databases": MAX_DATABASES, "sqlite_seconds": SQLITE_SECONDS}


def validate_backup(folder: Path, *, allow_incomplete: bool = False) -> tuple[dict, dict[str, tuple]]:
    reject_links(folder)
    expected_names = {"manifest.json", "workspace", "state"} | ({"incomplete.json"} if allow_incomplete else set())
    if not folder.is_dir() or {item.name for item in folder.iterdir()} != expected_names:
        fail("backup_incomplete_or_extra_root")
    root_signature = signature(folder.lstat())
    manifest_path = folder / "manifest.json"
    manifest_signature = signature(regular(manifest_path))
    manifest_hash = hash_file(manifest_path, manifest_signature)
    manifest = read_manifest(folder)
    if signature(regular(manifest_path)) != manifest_signature:
        fail("backup_changed_during_validation")
    roots = {"workspace": folder / "workspace", "state": folder / "state"}
    actual, signatures = inventory(roots)
    expected = manifest["entries"]
    if actual != [{key: value for key, value in row.items() if key != "sha256"} for row in expected]:
        fail("backup_tree_or_size_mismatch")
    for row in expected:
        if row["type"] == "file" and hash_file(path_for(roots, row["path"]), signatures[row["path"]]) != row["sha256"]:
            fail("backup_hash_mismatch")
    inspect_databases(roots, expected, manifest["identity"])
    after, after_signatures = inventory(roots)
    if (after != actual or after_signatures != signatures or signature(folder.lstat()) != root_signature
            or {item.name for item in folder.iterdir()} != expected_names
            or hash_file(manifest_path, manifest_signature) != manifest_hash):
        fail("backup_changed_during_validation")
    # Bind metadata as well as payload to the same validated snapshot. These
    # reserved keys cannot collide with workspace/ or state/ payload paths.
    signatures["."] = root_signature
    signatures["manifest.json"] = (*manifest_signature, manifest_hash)
    return manifest, signatures


def summary(operation: str, folder: Path, manifest: dict, signatures: dict[str, tuple]) -> dict:
    return {"passed": True, "operation": operation, "backup": str(folder),
            "files": sum(row["type"] == "file" for row in manifest["entries"]),
            "directories": sum(row["type"] == "directory" for row in manifest["entries"]),
            "bytes": sum(row.get("size", 0) for row in manifest["entries"]),
            "manifest_sha256": signatures["manifest.json"][-1]}


def backup(*, workspace: Path, state_root: Path, user_id: str, output: Path) -> dict:
    binding = identity(workspace, state_root, user_id)
    work, base, leaf = (Path(binding[key]) for key in ("workspace", "state_root", "instance_state"))
    ownership_boundaries(work, leaf)
    output = absolute_path(output)
    if overlap(output, work) or overlap(output, base) or output.exists():
        fail("backup_output_exists_or_overlaps")
    if not output.parent.is_dir():
        fail("backup_output_parent_missing")
    roots = {"workspace": work, "state": leaf}
    omitted = tuple("state/" + name for name in COORDINATION)
    with instance_locks(leaf, create=False):
        entries, before = inventory(roots, omitted=omitted)
        output.mkdir(mode=0o700)
        write_json_new(output / "incomplete.json", {"product": PRODUCT, "complete": False})
        destinations = {"workspace": output / "workspace", "state": output / "state"}
        for row in sorted(entries, key=lambda row: (row["path"].count("/"), row["path"])):
            target = path_for(destinations, row["path"])
            if row["type"] == "directory":
                reject_links(target)
                target.mkdir(mode=0o700)
            else:
                row["sha256"] = copy_file(path_for(roots, row["path"]), target, before[row["path"]])
        after, after_signatures = inventory(roots, omitted=omitted)
        if after != [{key: value for key, value in row.items() if key != "sha256"} for row in entries] or before != after_signatures:
            fail("source_changed_during_backup")
        for row in entries:
            if row["type"] == "file" and hash_file(path_for(roots, row["path"]), before[row["path"]]) != row["sha256"]:
                fail("source_changed_during_backup")
        inspect_databases(destinations, entries, binding)
        manifest = {"schema_version": 1, "product": PRODUCT, "complete": True,
                    "created_at_utc": datetime.now(timezone.utc).isoformat(), "identity": binding,
                    "entries": entries, "omitted_coordination": list(omitted), "limits": limits()}
        encoded = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
        if len(encoded) > MAX_MANIFEST_BYTES:
            fail("manifest_size_limit")
        write_json_new(output / "manifest.json", manifest)
        validated, validated_signatures = validate_backup(output, allow_incomplete=True)
        final, final_signatures = inventory(roots, omitted=omitted)
        if final != after or final_signatures != before:
            fail("source_changed_during_backup")
        ownership_boundaries(work, leaf)
        marker = output / "incomplete.json"
        regular(marker)
        marker.unlink()
        return summary("backup", output, validated, validated_signatures)


def empty_target(path: Path, *, state: bool) -> None:
    reject_links(path)
    if path.exists():
        if not path.is_dir():
            fail("restore_target_not_directory")
        allowed = set(COORDINATION) if state else set()
        children = list(path.iterdir())
        if any(child.name not in allowed for child in children):
            fail("restore_target_not_empty")
        for child in children:
            regular(child)


def restore(*, backup_path: Path, workspace: Path, state_root: Path, user_id: str) -> dict:
    folder = absolute_path(backup_path)
    manifest, backup_signatures = validate_backup(folder)
    binding = identity(workspace, state_root, user_id)
    if manifest["identity"] != binding:
        fail("restore_original_identity_required")
    work, base, leaf = (Path(binding[key]) for key in ("workspace", "state_root", "instance_state"))
    ownership_boundaries(work, leaf)
    if overlap(folder, work) or overlap(folder, base):
        fail("restore_backup_overlap")
    empty_target(work, state=False)
    empty_target(leaf, state=True)
    # All manifest/tree/hash/SQLite/identity validation preceded these mutations.
    reject_links(leaf.parent)
    leaf.mkdir(parents=True, exist_ok=True)
    with instance_locks(leaf, create=True):
        ownership_boundaries(work, leaf)
        empty_target(work, state=False)
        empty_target(leaf, state=True)
        reject_links(work.parent)
        work.mkdir(parents=True, exist_ok=True)
        sources = {"workspace": folder / "workspace", "state": folder / "state"}
        targets = {"workspace": work, "state": leaf}
        for row in sorted(manifest["entries"], key=lambda row: (row["path"].count("/"), row["path"])):
            if row["path"] in {"workspace", "state"}:
                continue
            destination = path_for(targets, row["path"])
            if row["type"] == "directory":
                reject_links(destination)
                destination.mkdir(mode=0o700)
            else:
                copy_file(path_for(sources, row["path"]), destination, backup_signatures[row["path"]], row["sha256"])
        actual, actual_signatures = inventory(targets, omitted=tuple("state/" + name for name in COORDINATION))
        if actual != [{key: value for key, value in row.items() if key != "sha256"} for row in manifest["entries"]]:
            fail("restored_tree_mismatch")
        for row in manifest["entries"]:
            if row["type"] == "file" and hash_file(path_for(targets, row["path"]), actual_signatures[row["path"]]) != row["sha256"]:
                fail("restored_hash_mismatch")
        inspect_databases(targets, manifest["entries"], binding)
        revalidated, after_signatures = validate_backup(folder)
        if revalidated != manifest or after_signatures != backup_signatures:
            fail("backup_changed_during_restore")
    return {**summary("restore", folder, manifest, after_signatures), "restored": True}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        fail("invalid_arguments")


def main(argv: list[str] | None = None) -> int:
    operation = None
    try:
        parser = Parser(description=__doc__, allow_abbrev=False)
        commands = parser.add_subparsers(dest="operation", required=True, parser_class=Parser)
        save = commands.add_parser("backup", allow_abbrev=False)
        check = commands.add_parser("verify", allow_abbrev=False)
        load = commands.add_parser("restore", allow_abbrev=False)
        for command in (save, load):
            command.add_argument("--workspace", type=Path, required=True)
            command.add_argument("--state-root", type=Path, required=True)
            command.add_argument("--user-id", required=True)
        save.add_argument("--output", type=Path, required=True)
        for command in (check, load):
            command.add_argument("--backup", type=Path, required=True)
        args = parser.parse_args(argv)
        operation = args.operation
        if operation == "backup":
            result = backup(workspace=args.workspace, state_root=args.state_root, user_id=args.user_id, output=args.output)
        elif operation == "restore":
            result = restore(backup_path=args.backup, workspace=args.workspace, state_root=args.state_root, user_id=args.user_id)
        else:
            folder = absolute_path(args.backup)
            manifest, signatures = validate_backup(folder)
            result = summary("verify", folder, manifest, signatures)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except BackupError as error:
        result = {"passed": False, "operation": operation, "error": error.code}
    except (OSError, ValueError, TypeError, KeyError):
        result = {"passed": False, "operation": operation, "error": "filesystem_or_metadata_failure"}
    print(json.dumps(result, ensure_ascii=False))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
