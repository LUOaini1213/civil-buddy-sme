"""Offline release tests using disposable Git fixtures and a non-executed PE stub."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import build_unified_release as release


class UnifiedReleaseTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / "work" / "release-tests"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="fixture-", dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.root = self.folder / "repo"
        self.root.mkdir()
        self.output = self.folder / "deliverables"
        self.staging = self.folder / "staging"
        names = {*release.legacy.EXPLICIT, *release.EXTRA,
                 *("demo/static/" + name for name in release.legacy.STATIC),
                 *(f".agents/skills/expert-{index}/SKILL.md" for index in range(66)),
                 ".agents/skills/civil-buddy/SKILL.md", "demo/kb/general/README.md",
                 "scripts/start-workbench.bat", "packing_assistant/civil.py", "demo/app.py",
                 "workbench/src/main.rs", "workbench/src/lib.rs", "workbench/src/product/tools.rs"}
        for name in names:
            self.write(name, "synthetic fixture\n")
        self.git("init", "-q")
        self.git("config", "user.name", "Release fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "core.autocrlf", "false")
        self.git("add", ".")
        self.git("-c", "commit.gpgsign=false", "-c", "core.hooksPath=" + str(self.folder / "no-hooks"), "commit", "-qm", "fixture")
        self.binary = self.folder / "provided.exe"
        stub = bytearray(256)
        stub[:2] = b"MZ"
        struct.pack_into("<I", stub, 0x3C, 128)
        stub[128:132] = b"PE\0\0"
        struct.pack_into("<H", stub, 132, 0x8664)
        self.binary.write_bytes(stub)

    def write(self, name, content):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout

    def build(self, version="0.5.0-test"):
        return release.build_release(self.root, version, self.binary, self.output, self.staging)

    def test_zip_manifest_binary_and_chinese_runtime_instructions(self):
        archive, stage, manifest = self.build()
        self.assertEqual(release.verify_archive(archive), manifest)
        self.assertEqual(manifest["source_commit"], self.git("rev-parse", "HEAD").decode().strip())
        self.assertEqual(manifest["binary"]["architecture"], "x86_64")
        self.assertIn("not attested", manifest["binary"]["build_provenance"])
        with zipfile.ZipFile(archive) as package:
            names = set(package.namelist())
            for expected in ["bin/civil-workbench.exe", "workbench/src/main.rs", "workbench/src/product/tools.rs",
                             "workbench/Cargo.toml", "workbench/Cargo.lock", "scripts/start_unified_workbench.py",
                             "start-workbench.bat", "packing_assistant/civil.py", *release.EXTRA]:
                self.assertIn(expected, names)
            self.assertEqual(package.read(release.BINARY_NAME), self.binary.read_bytes())
            self.assertEqual(len([name for name in names if name.startswith(".agents/skills/")]), 67)
            for entry in manifest["files"]:
                data = package.read(entry["path"])
                self.assertEqual(len(data), entry["bytes"])
                self.assertEqual(hashlib.sha256(data).hexdigest(), entry["sha256"])
            readme = package.read("README.md").decode("utf-8")
            self.assertIn("Python 3.11", readme)
            self.assertIn("--binary bin/civil-workbench.exe", readme)
            self.assertIn("不会安装全局依赖", readme)
            self.assertIn("不自动安装任何依赖", readme)
            self.assertIn("start-workbench.bat", readme)
            self.assertIn("不证明该 exe 由本次源码编译", readme)
        self.assertEqual(json.loads(archive.with_suffix(".manifest.json").read_text(encoding="utf-8")), manifest)
        self.assertEqual(archive.with_suffix(".zip.sha256").read_text().split()[0], release.digest(archive))
        self.assertEqual((stage / release.BINARY_NAME).read_bytes(), self.binary.read_bytes())

    def test_private_runtime_untracked_and_uncommitted_sources_are_not_swept_in(self):
        private = [".env", "demo/.env", "demo/data/project.json", "demo/out/user.docx",
                   "workbench/target/local.exe", "workbench/src/private.env", "output/user.txt",
                   "docs/civil-buddy/architecture/customer-notes.md"]
        for name in private:
            self.write(name, "do not distribute synthetic private content\n")
        self.git("add", "--", *private)
        self.git("-c", "commit.gpgsign=false", "-c", "core.hooksPath=" + str(self.folder / "no-hooks"), "commit", "-qm", "unrelated private fixtures")
        untracked = ["packing_assistant/local_user_script.py", "demo/kb/general/upload.md", "workbench/src/not_committed.rs"]
        for name in untracked:
            self.write(name, "untracked fixture\n")
        self.git("add", "workbench/src/not_committed.rs")  # Staged is still not committed.
        archive, _, _ = self.build()
        with zipfile.ZipFile(archive) as package:
            self.assertFalse((set(private) | set(untracked)) & set(package.namelist()))
            self.assertIn(".env.example", package.namelist())

    def test_dirty_selected_source_or_missing_required_source_fails_before_output(self):
        self.write("packing_assistant/civil.py", "uncommitted runtime edit\n")
        with self.assertRaisesRegex(ValueError, "uncommitted changes"):
            self.build()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.staging.exists())
        self.git("restore", "--", "packing_assistant/civil.py")
        (self.root / "workbench/Cargo.lock").unlink()
        with self.assertRaises(FileNotFoundError):
            self.build()
        self.assertFalse(self.output.exists())

    def test_invalid_version_and_non_pe_binary_do_not_create_outputs(self):
        for version in ("../escape", "1.0.0/escape", "1.0.0;run", ""):
            with self.subTest(version=version), self.assertRaises(ValueError):
                self.build(version)
        self.binary.write_bytes(b"not a PE executable" * 20)
        with self.assertRaisesRegex(ValueError, "PE executable"):
            self.build()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.staging.exists())

    def test_archive_tampering_unlisted_private_members_and_case_duplicates_are_rejected(self):
        archive, _, manifest = self.build()
        with zipfile.ZipFile(archive) as package:
            original = {name: package.read(name) for name in package.namelist()}
        variants = [
            {**original, "README.md": b"tampered"},
            {**original, "demo/.env": b"synthetic-private"},
            {**original, "demo/kb/general/login-token.txt": b"synthetic-private"},
            {**original, "demo/kb/general/identity.sqlite-wal": b"synthetic-private"},
            {**original, "readme.md": original["README.md"]},
            {**original, "../outside": b"bad path"},
            {**original, "extra.txt": b"not in manifest"},
        ]
        for index, content in enumerate(variants):
            bad = self.folder / f"tampered-{index}.zip"
            with zipfile.ZipFile(bad, "w") as package:
                for name, data in content.items():
                    package.writestr(name, data)
            with self.subTest(index=index), self.assertRaises(ValueError):
                release.verify_archive(bad, manifest)

    def test_failed_zip_write_preserves_existing_archive_and_removes_only_its_temp(self):
        archive, _, _ = self.build()
        previous = archive.read_bytes()
        unrelated = self.output / "keep.txt"
        unrelated.write_text("keep", encoding="utf-8")
        with patch.object(release.zipfile.ZipFile, "write", side_effect=OSError("synthetic disk error")):
            with self.assertRaises(OSError):
                self.build()
        self.assertEqual(archive.read_bytes(), previous)
        self.assertEqual(unrelated.read_text(), "keep")
        self.assertFalse(list(self.output.glob(".unified-*.zip")))

    def test_required_builder_must_be_in_head_not_just_working_tree(self):
        self.git("rm", "--cached", "scripts/build_unified_release.py")
        self.git("-c", "commit.gpgsign=false", "-c", "core.hooksPath=" + str(self.folder / "no-hooks"), "commit", "-qm", "remove required builder from HEAD")
        self.assertTrue((self.root / "scripts/build_unified_release.py").exists())
        with self.assertRaisesRegex(ValueError, "must be committed"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_verify_cli_works_without_a_git_checkout_and_never_executes_binary(self):
        archive, _, _ = self.build()
        command = subprocess.run([sys.executable, str(ROOT / "scripts/build_unified_release.py"), "--verify", str(archive)],
                                 cwd=self.folder, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        report = json.loads(command.stdout)
        self.assertTrue(report["verified"])
        self.assertEqual(report["version"], "0.5.0-test")


if __name__ == "__main__":
    unittest.main()
