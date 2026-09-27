#!/usr/bin/env python3
"""Offline packaging/bootstrap regressions; real HTTP acceptance has a separate smoke entry."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import build_workbench_release as release
import start_workbench as starter

ROOT = Path(__file__).resolve().parents[1]


class TrialPackTests(unittest.TestCase):
    def setUp(self) -> None:
        output = ROOT / "output"
        output.mkdir(exist_ok=True)
        output.resolve().relative_to(ROOT.resolve())
        self.temp = tempfile.TemporaryDirectory(prefix="test-trial-pack-", dir=output)
        self.root = Path(self.temp.name).resolve()
        self.root.relative_to(output.resolve())
        self.addCleanup(self.temp.cleanup)
        self.assets = [f".agents/skills/expert-{i}/SKILL.md" for i in range(66)] + [
            ".agents/skills/civil-buddy/SKILL.md", "demo/kb/company/README.md"]
        for name in [*release.EXPLICIT, *self.assets,
                     *("demo/static/" + name for name in release.STATIC),
                     "scripts/start-workbench.bat", "packing_assistant/civil.py", "demo/app.py"]:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n", encoding="utf-8")
        self.tracked = patch.object(release, "tracked_assets", return_value=self.assets)
        self.tracked.start()
        self.addCleanup(self.tracked.stop)

    def test_version_traversal_is_rejected_before_any_write(self) -> None:
        for version in ("../escape", "1.0.0/escape", "1.0.0\\escape", "", "-1", "1.0.0;whoami", "1.0.0 " + "x" * 60):
            with self.subTest(version=version), self.assertRaises(ValueError):
                release.build_release(self.root, version)
        self.assertFalse((self.root / "dist").exists())

    def test_allowlist_excludes_secrets_user_content_and_caches(self) -> None:
        rejected = [".env", "demo/.env", "demo/data/user_catalog.json", "demo/out/session/draft.md",
                    "output/run.json", "demo/kb/company/private-upload.md", "demo/static/private.txt",
                    "packing_assistant/__pycache__/secret.py", "packing_assistant/private.key"]
        for name in rejected:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("must not ship", encoding="utf-8")
        included = release.release_inputs(self.root)
        self.assertFalse(set(rejected) & set(included))
        self.assertIn(".env.example", included)
        self.assertIn("demo/static/chat-stream.js", included)

    def test_every_static_file_the_real_page_loads_is_allowlisted(self) -> None:
        # The release copies demo/static by allowlist; a script the page loads but the list
        # forgets ships a dead button (voice.js was missed once). Read the real page, not the fixture.
        import re

        # Inspect every actual product page, so newly routed tools cannot ship
        # without their HTML, scripts, styles or local third-party notices.
        loaded = set()
        for page in (ROOT / "demo" / "static").glob("*.html"):
            self.assertIn(page.name, release.STATIC, page.name)
            html = page.read_text(encoding="utf-8")
            loaded.update(m.split("?", 1)[0] for m in re.findall(r'(?:src|href)="/static/([^"]+)"', html))
        self.assertIn("voice.js", loaded)
        self.assertFalse(loaded - set(release.STATIC), sorted(loaded - set(release.STATIC)))
        included = release.release_inputs(self.root)
        for name in ("demo/static/voice.js", "demo/asr_lexicon.txt", "requirements-asr.txt",
                     "requirements-logistics.txt", "requirements-logistics-ocr.txt",
                     "scripts/prepare_logistics_ocr.py", "docs/civil-buddy/logistics-workbench.md"):
            self.assertIn(name, included)

    def test_modules_service_worker_and_engineering_pages_ship_with_their_local_dependencies(self) -> None:
        # Follow actual entry points and ESM imports: checking only index.html misses the
        # extracted modules, and can ship a homepage that loads app.js but never starts.
        import re

        static = ROOT / "demo" / "static"
        pending = ["index.html", "agent.html", "cad.html", "engineering.html", "engineering-schedule.html", "sw.js"]
        loaded = set()
        while pending:
            name = pending.pop()
            if name in loaded:
                continue
            loaded.add(name)
            self.assertIn(name, release.STATIC, f"unshipped page dependency: {name}")
            source = static / name
            self.assertTrue(source.is_file(), name)
            text = source.read_text(encoding="utf-8")
            if source.suffix == ".html":
                pending.extend(re.findall(r'(?:src|href)=[\"\']/static/([^\"\'?]+)', text))
            elif source.suffix == ".js":
                imports = re.findall(r'\b(?:from\s*|import\s*\(?\s*)[\"\'](\.[^\"\']+)[\"\']', text)
                pending.extend((source.parent / value).resolve().relative_to(static.resolve()).as_posix() for value in imports)
        self.assertIn("modules/session-nav.js", loaded)
        self.assertIn("modules/turn-stream.js", loaded)
        self.assertIn("cad-viewer.js", loaded)
        self.assertIn("vendor/three/three.core.js", loaded)
        self.assertIn("engineering-schedule-state.js", loaded)
        self.assertIn("vendor/frappe-gantt-1.2.2/frappe-gantt.es.js", loaded)

    def test_actual_zip_contains_hidden_skills_and_verified_manifest(self) -> None:
        archive, stage = release.build_release(self.root, "1.2.3-test")
        self.assertTrue(stage.is_dir())
        with zipfile.ZipFile(archive) as output:
            manifest = json.loads(output.read("release-manifest.json"))
            self.assertEqual(manifest["runtime"], "python")
            self.assertEqual(manifest["expert_skills"], 66)
            self.assertEqual(len([n for n in output.namelist() if n.startswith(".agents/skills/")]), 67)
            for entry in manifest["files"]:
                data = output.read(entry["path"])
                self.assertEqual(len(data), entry["bytes"])
                self.assertEqual(hashlib.sha256(data).hexdigest(), entry["sha256"])
            readme = output.read("README.md").decode("utf-8")
            self.assertIn("无需 API Key", readme)
            self.assertIn("Python 3.10", readme)
            self.assertIn("TRY.md", output.namelist())

    def test_failed_archive_creation_preserves_previous_zip(self) -> None:
        archive, _ = release.build_release(self.root, "1.2.3")
        before = archive.read_bytes()
        with patch.object(release.zipfile.ZipFile, "write", side_effect=OSError("fixture disk failure")):
            with self.assertRaises(OSError):
                release.build_release(self.root, "1.2.3")
        self.assertEqual(archive.read_bytes(), before)
        self.assertEqual(list(archive.parent.glob(".workbench-*.zip")), [])

    def test_required_inputs_missing_fail_before_staging(self) -> None:
        (self.root / "demo/static/chat-stream.js").unlink()
        with self.assertRaises(FileNotFoundError):
            release.build_release(self.root, "1.0.0")
        self.assertFalse((self.root / "dist").exists())

    def test_path_escapes_cannot_be_copied(self) -> None:
        for name in ("../escape", "/escape", "C:/escape", "demo\\escape"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                release.checked_path(self.root, name)

    def test_reparse_inputs_rejected(self) -> None:
        source = self.root / "demo/static/app.js"
        original = source.lstat()
        fake = type("ReparseStat", (), {"st_file_attributes": 0x400, "st_mode": original.st_mode})()
        with patch.object(Path, "lstat", lambda p: fake if p == source else original):
            with self.assertRaises(ValueError):
                release.checked_path(self.root, "demo/static/app.js")


class BootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cwd = Path.cwd()
        self.path = list(sys.path)
        self.addCleanup(os.chdir, self.cwd)
        self.addCleanup(lambda: setattr(sys, "path", self.path))

    def test_existing_interpreter_is_never_silently_modified(self) -> None:
        with patch.dict(os.environ, {"CIVIL_PYTHON": sys.executable, "CIVIL_BOOTSTRAPPED": ""}), \
                patch.object(starter, "missing_dependencies", return_value=["fastapi"]), \
                patch.object(starter, "install_local") as install, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(starter.main(["--no-browser"]), 1)
        install.assert_not_called()
        self.assertIn("CIVIL_PYTHON lacks", err.getvalue())

    def test_setup_failure_is_actionable_and_no_server_starts(self) -> None:
        with patch.dict(os.environ, {"CIVIL_PYTHON": "", "CIVIL_BOOTSTRAPPED": ""}), \
                patch.object(starter, "missing_dependencies", return_value=["fastapi"]), \
                patch.object(starter, "install_local", side_effect=subprocess.CalledProcessError(1, ["pip"])), \
                patch.object(starter.runpy, "run_module") as run, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(starter.main(["--setup", "--no-browser"]), 1)
        run.assert_not_called()
        self.assertIn("retry start-workbench.bat --setup", err.getvalue())

    def test_incomplete_install_does_not_loop_forever(self) -> None:
        with patch.dict(os.environ, {"CIVIL_BOOTSTRAPPED": "1"}), \
                patch.object(starter, "missing_dependencies", return_value=["fastapi"]), \
                patch.object(starter, "install_local") as install, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(starter.main(["--no-browser"]), 1)
        install.assert_not_called()

    def test_only_local_venv_receives_pip_install(self) -> None:
        root = ROOT / "output" / "bootstrap-fixture"
        interpreter = starter.local_python(root)
        with patch.object(Path, "is_file", return_value=True), patch.object(starter.subprocess, "run") as pip:
            self.assertEqual(starter.install_local(root), interpreter)
        self.assertEqual(pip.call_args.args[0], [str(interpreter), "-m", "pip", "install", "-r", str(root / "requirements.txt")])


if __name__ == "__main__":
    unittest.main()
