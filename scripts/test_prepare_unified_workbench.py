"""Offline Windows setup contracts. No wheels, model endpoints or installers run."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import sysconfig
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import prepare_unified_workbench as setup


def locked(version="1.2.3"):
    return ("# Offline fixture, not an installable dependency set.\n"
            f"Example_Package=={version} \\" + "\n"
            f"    --hash=sha256:{'1' * 64}\n"
            f"uvicorn[standard]==0.30.0 --hash=sha256:{'2' * 64}\n").encode("ascii")


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="civil-setup-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "中文 package with spaces"
        self.root.mkdir()
        (self.root / setup.LOCK_NAME).write_bytes(locked())
        (self.root / "scripts").mkdir()
        (self.root / "scripts/start_unified_workbench.py").write_text("# never executed fixture\n")
        (self.root / "bin").mkdir()
        self.binary = self.root / "bin/civil-workbench.exe"
        self.binary.write_bytes(b"unexecuted binary fixture")
        self.base = self.root / "base python.exe"
        self.base.write_bytes(b"unexecuted interpreter fixture")
        self.calls = []
        self.fail_install = False
        self.fail_check = False
        self.wrong_inventory = False
        self.wrong_inventory_paths = set()
        self.preflight_ok = True
        self.preflight_calls = []
        self.output = io.StringIO()
        self.addCleanup(self.output.close)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(self.output))
        self.stack.enter_context(patch.object(setup, "run_command", side_effect=self.command))
        self.stack.enter_context(patch.object(setup, "load_preflight", return_value=SimpleNamespace(
            run=self.preflight, format_report=lambda report, root: "fixture preflight")))

    def info(self, python):
        selected = Path(python)
        is_base = selected == self.base
        prefix = self.root / "base" if is_base else selected.parents[1]
        return dict(implementation="cpython", version=[3, 11, 9], platform="win32", bits=64,
                    build_platform="win-amd64", executable=str(selected), prefix=str(prefix), base_prefix=str(self.root / "base"))

    def command(self, args, root, **kwargs):
        self.calls.append(list(args))
        if setup.PROBE in args:
            return SimpleNamespace(returncode=0, stdout=json.dumps(self.info(args[0])).encode(), stderr=b"")
        if setup.INVENTORY_PROBE in args:
            data = setup.lock_versions((Path(args[0]).parents[1] / "requirements.lock").read_bytes())
            if self.wrong_inventory or Path(args[0]) in self.wrong_inventory_paths:
                data["example-package"] = "999.0.0"
            return SimpleNamespace(returncode=0, stdout=json.dumps(data).encode(), stderr=b"")
        if "venv" in args:
            environment = Path(args[-1])
            (environment / "Scripts").mkdir(exist_ok=True)
            (environment / "Scripts/python.exe").write_bytes(b"unexecuted venv interpreter")
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        return SimpleNamespace(returncode=int(("install" in args and self.fail_install)
                                             or ("check" in args and self.fail_check)), stdout=b"", stderr=b"private-output-not-shown")

    def preflight(self, **kwargs):
        self.preflight_calls.append(kwargs)
        return SimpleNamespace(ok=self.preflight_ok)

    def prepare(self, **kwargs):
        return setup.prepare(self.root, python=str(self.base), binary=self.binary, **kwargs)

    def selected_bytes(self):
        return (self.root / setup.RUNTIME_NAME / "selected.json").read_bytes()

    def files(self):
        return {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.root.rglob("*") if p.is_file()}

    def environment_files(self, environment):
        return {str(p.relative_to(environment)): p.read_bytes()
                for p in environment.rglob("*") if p.is_file()}

    def test_hash_lock_accepts_extras_and_rejects_unpinned_or_injected_sources(self):
        self.assertEqual(setup.lock_versions(locked()), {"example-package": "1.2.3", "uvicorn": "0.30.0"})
        for data in (b"example>=1.0", b"--extra-index-url https://invalid.example", locked() + locked(),
                     b"example @ https://invalid.example/pkg.whl", b"", b"-r other.txt"):
            with self.subTest(data=data), self.assertRaises(setup.PreparationError):
                setup.lock_versions(data)

    def test_prepare_uses_owned_final_path_and_hash_only_official_install(self):
        selected = self.prepare()
        digest = hashlib.sha256(locked()).hexdigest()
        expected = self.root / setup.RUNTIME_NAME / "py311" / digest[:16]
        self.assertEqual(selected, expected / "Scripts/python.exe")
        self.assertEqual(setup.prepared_python(self.root), selected)
        create = next(c for c in self.calls if "venv" in c)
        self.assertEqual(create[-1], str(expected))
        install = next(c for c in self.calls if "install" in c)
        for flag in ("--require-virtualenv", "--require-hashes", "--only-binary=:all:", "--no-cache-dir", "--no-compile"):
            self.assertIn(flag, install)
        self.assertEqual(install[install.index("--index-url") + 1], "https://pypi.org/simple")
        self.assertNotIn("--isolated", install)
        self.assertEqual(json.loads(self.selected_bytes())["lock_sha256"], digest)
        self.assertNotEqual(self.preflight_calls[-1]["port"], 8765)
        self.assertFalse((self.root / "demo/data").exists())

    def test_ready_environment_reuses_without_venv_creation_or_install(self):
        self.prepare()
        before = self.selected_bytes()
        self.calls.clear()
        self.prepare()
        self.assertFalse(any("venv" in c or "install" in c for c in self.calls))
        self.assertTrue(any("check" in c for c in self.calls))
        self.assertTrue(any(setup.INVENTORY_PROBE in c for c in self.calls))
        self.assertEqual(before, self.selected_bytes())

    def test_interrupted_owned_environment_retries_and_failure_preserves_old_selection(self):
        previous = self.prepare()
        before = self.selected_bytes()
        original = self.root / "customer-original.txt"
        original.write_text("must stay unchanged")
        (self.root / setup.LOCK_NAME).write_bytes(locked("1.2.4"))
        self.fail_install = True
        with self.assertRaisesRegex(setup.PreparationError, "previous selected"):
            self.prepare()
        self.assertEqual(before, self.selected_bytes())
        self.assertTrue(previous.is_file())
        digest = hashlib.sha256(locked("1.2.4")).hexdigest()
        environment, _ = setup.env_details(self.root, digest)
        self.assertTrue((environment / setup.OWNER_NAME).is_file())
        self.assertTrue((environment / "Scripts/python.exe").is_file())
        self.assertFalse((environment / "ready.json").exists())
        self.calls.clear()
        self.fail_install = False
        selected = self.prepare()
        self.assertEqual(selected, environment / "Scripts/python.exe")
        self.assertTrue(any("venv" in c for c in self.calls), "partial python.exe alone must not skip ensurepip repair")
        self.assertNotEqual(before, self.selected_bytes())
        self.assertEqual(original.read_text(), "must stay unchanged")
        self.assertTrue(previous.is_file())

    def test_wrong_installed_version_never_marks_ready_or_switches_selection(self):
        self.prepare()
        before = self.selected_bytes()
        (self.root / setup.LOCK_NAME).write_bytes(locked("1.2.4"))
        self.wrong_inventory = True
        with self.assertRaisesRegex(setup.PreparationError, "exactly match"):
            self.prepare()
        self.assertEqual(before, self.selected_bytes())
        env, _ = setup.env_details(self.root, hashlib.sha256(locked("1.2.4")).hexdigest())
        self.assertFalse((env / "ready.json").exists())

    def test_modified_ready_environment_is_not_silently_reinstalled(self):
        self.prepare()
        before = self.files()
        self.wrong_inventory = True
        self.calls.clear()
        with self.assertRaisesRegex(setup.PreparationError, "exactly match"):
            self.prepare()
        self.assertFalse(any("install" in c or "venv" in c for c in self.calls))
        self.assertEqual(before, self.files())

    def test_explicit_rebuild_uses_fresh_generation_and_retains_prior_bytes(self):
        previous = self.prepare()
        original = self.root / "customer-original.txt"
        original.write_bytes(b"original material must not change")
        previous_files = self.environment_files(previous.parents[1])
        previous_selection = self.selected_bytes()
        self.calls.clear()
        selected = self.prepare(rebuild=True)
        metadata = json.loads(self.selected_bytes())
        self.assertRegex(metadata["generation"], r"^[a-f0-9]{32}$")
        expected, owner = setup.env_details(self.root, hashlib.sha256(locked()).hexdigest(), metadata["generation"])
        self.assertEqual(selected, expected / "Scripts/python.exe")
        self.assertNotEqual(selected, previous)
        self.assertNotEqual(self.selected_bytes(), previous_selection)
        self.assertEqual(json.loads((expected / setup.OWNER_NAME).read_bytes()), owner)
        self.assertEqual(json.loads((expected / "ready.json").read_bytes()), {**owner, "ready": True})
        self.assertEqual((expected / "requirements.lock").read_bytes(), locked())
        self.assertEqual(setup.prepared_python(self.root), selected)
        self.assertEqual(previous_files, self.environment_files(previous.parents[1]))
        self.assertEqual(original.read_bytes(), b"original material must not change")
        self.assertTrue(any("venv" in c for c in self.calls))
        install = next(c for c in self.calls if "install" in c)
        self.assertEqual(install[-1], str(expected / "requirements.lock"))
        self.assertIn("--require-hashes", install)
        self.assertIn("--only-binary=:all:", install)

    def test_failed_rebuild_keeps_selected_environment_and_preserves_failed_candidates(self):
        previous = self.prepare()
        selection = self.selected_bytes()
        previous_files = self.environment_files(previous.parents[1])
        candidates = []
        for failure in ("install", "check", "preflight"):
            self.fail_install = failure == "install"
            self.fail_check = failure == "check"
            self.preflight_ok = failure != "preflight"
            self.calls.clear()
            with self.subTest(failure=failure), self.assertRaises(setup.PreparationError):
                self.prepare(rebuild=True)
            self.assertEqual(selection, self.selected_bytes())
            self.assertEqual(previous_files, self.environment_files(previous.parents[1]))
            created = Path(next(c[-1] for c in self.calls if "venv" in c))
            self.assertNotIn(created, candidates)
            candidates.append(created)
            self.assertTrue((created / setup.OWNER_NAME).is_file())
            self.assertFalse((created / "ready.json").exists())
            for candidate in candidates:
                self.assertTrue(candidate.is_dir(), "failed generations must remain available for diagnosis")
        self.fail_install = self.fail_check = False
        self.preflight_ok = True
        self.assertEqual(setup.prepared_python(self.root), previous)

    def test_plain_prepare_reuses_rebuilt_selection_without_resurrecting_legacy(self):
        legacy = self.prepare()
        selected = self.prepare(rebuild=True)
        before = self.files()
        self.calls.clear()
        self.assertEqual(self.prepare(), selected)
        self.assertFalse(any("venv" in c or "install" in c for c in self.calls))
        self.assertTrue(any("check" in c for c in self.calls))
        self.assertEqual(before, self.files())
        self.assertTrue(legacy.is_file())
        self.assertEqual(setup.prepared_python(self.root), selected)
        (self.root / setup.LOCK_NAME).write_bytes(locked("1.2.4"))
        new_default = self.prepare()
        environment, _ = setup.env_details(self.root, hashlib.sha256(locked("1.2.4")).hexdigest())
        self.assertEqual(new_default, environment / "Scripts/python.exe")
        self.assertTrue(selected.is_file())

    def test_selected_generation_reuse_does_not_inspect_unused_legacy_reparse(self):
        legacy = self.prepare()
        selected = self.prepare(rebuild=True)
        before = self.files()
        original = Path.lstat
        def attributes(path):
            if path == legacy.parents[1]:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return original(path)
        self.calls.clear()
        with patch.object(Path, "lstat", attributes):
            self.assertEqual(setup.prepared_python(self.root), selected)
            self.assertEqual(self.prepare(), selected)
        self.assertFalse(any("venv" in c or "install" in c for c in self.calls))
        self.assertTrue(any("check" in c for c in self.calls))
        self.assertEqual(before, self.files())

    def test_explicit_rebuild_recovers_modified_versions_without_reinstalling_old_environment(self):
        previous = self.prepare()
        self.wrong_inventory_paths.add(previous)
        before = self.files()
        self.calls.clear()
        with self.assertRaisesRegex(setup.PreparationError, "--rebuild"):
            self.prepare()
        self.assertEqual(before, self.files())
        self.assertFalse(any("venv" in c or "install" in c for c in self.calls))
        previous_files = self.environment_files(previous.parents[1])
        selected = self.prepare(rebuild=True)
        self.assertNotEqual(selected, previous)
        self.assertEqual(previous_files, self.environment_files(previous.parents[1]))
        self.assertEqual(setup.prepared_python(self.root), selected)

    def test_missing_selected_interpreter_requires_rebuild_and_retains_old_environment(self):
        selected = self.prepare(rebuild=True)
        selected.unlink()
        before = self.files()
        previous_files = self.environment_files(selected.parents[1])
        self.calls.clear()
        with patch.object(setup.subprocess, "call") as call:
            with self.assertRaisesRegex(setup.PreparationError, "--rebuild"):
                setup.launch(self.root, ["--check"])
            call.assert_not_called()
        with self.assertRaisesRegex(setup.PreparationError, "--rebuild"):
            self.prepare()
        self.assertFalse(any("venv" in c or "install" in c for c in self.calls))
        self.assertEqual(before, self.files())
        replacement = self.prepare(rebuild=True)
        self.assertNotEqual(replacement, selected)
        self.assertFalse(selected.exists())
        self.assertEqual(previous_files, self.environment_files(selected.parents[1]))
        self.assertEqual(setup.prepared_python(self.root), replacement)

    def test_rebuild_can_recover_invalid_selection_only_after_candidate_checks(self):
        previous = self.prepare()
        previous_files = self.environment_files(previous.parents[1])
        selection = self.root / setup.RUNTIME_NAME / "selected.json"
        selection.write_bytes(b"not valid JSON\n")
        self.fail_install = True
        with self.assertRaises(setup.PreparationError):
            self.prepare(rebuild=True)
        self.assertEqual(selection.read_bytes(), b"not valid JSON\n")
        self.assertEqual(previous_files, self.environment_files(previous.parents[1]))
        self.fail_install = False
        selected = self.prepare(rebuild=True)
        self.assertEqual(setup.prepared_python(self.root), selected)
        self.assertEqual(previous_files, self.environment_files(previous.parents[1]))

    def test_rebuild_preserves_damaged_prior_lock_and_unreadable_environment_metadata(self):
        for filename, damaged in (("requirements.lock", locked("9.9.9")),
                                  ("ready.json", b"invalid ready metadata\n"),
                                  (setup.OWNER_NAME, b"invalid owner metadata\n")):
            selected = self.prepare(rebuild=True)
            environment = selected.parents[1]
            (environment / filename).write_bytes(damaged)
            previous_files = self.environment_files(environment)
            self.calls.clear()
            with patch.object(setup.subprocess, "call") as call:
                with self.subTest(filename=filename), self.assertRaises(setup.PreparationError):
                    setup.launch(self.root, ["--check"])
                call.assert_not_called()
            self.assertFalse(any("venv" in c or "install" in c for c in self.calls))
            replacement = self.prepare(rebuild=True)
            self.assertNotEqual(replacement, selected)
            self.assertEqual(previous_files, self.environment_files(environment))
            self.assertEqual(setup.prepared_python(self.root), replacement)

    def test_generation_metadata_refuses_malformed_values_and_path_mismatches(self):
        self.prepare(rebuild=True)
        original = json.loads(self.selected_bytes())
        for value in (None, 123, [], "", "../outside", "A" * 32, "a" * 31, "a" * 33, "cmd & run"):
            record = {**original, "generation": value}
            setup.atomic_json(self.root / setup.RUNTIME_NAME / "selected.json", record)
            before = self.files()
            self.calls.clear()
            with self.subTest(value=value), self.assertRaises(setup.PreparationError):
                setup.prepared_python(self.root)
            with self.assertRaises(setup.PreparationError):
                self.prepare()
            self.assertFalse(any("venv" in c or "install" in c for c in self.calls))
            self.assertEqual(before, self.files())
        for record in ({key: value for key, value in original.items() if key != "generation"},
                       {**original, "environment": ".civil-buddy-runtime/py311/outside"},
                       {**original, "generation": "0" * 32}):
            setup.atomic_json(self.root / setup.RUNTIME_NAME / "selected.json", record)
            with self.subTest(record=record), self.assertRaises(setup.PreparationError):
                setup.prepared_python(self.root)

    def test_rebuild_generation_collision_never_modifies_existing_candidate(self):
        self.prepare()
        generation = "a" * 32
        environment, _ = setup.env_details(self.root, hashlib.sha256(locked()).hexdigest(), generation)
        environment.mkdir()
        (environment / "keep.txt").write_bytes(b"unknown collision must remain intact")
        before = self.files()
        self.calls.clear()
        with patch.object(setup.uuid, "uuid4", return_value=SimpleNamespace(hex=generation)):
            with self.assertRaisesRegex(setup.PreparationError, "already exists"):
                self.prepare(rebuild=True)
        self.assertEqual(before, self.files())
        self.assertFalse(any("venv" in c or "install" in c for c in self.calls))

    def test_rebuild_refuses_linked_selection_or_generated_path(self):
        self.prepare()
        generation = "b" * 32
        environment, _ = setup.env_details(self.root, hashlib.sha256(locked()).hexdigest(), generation)
        selection = self.root / setup.RUNTIME_NAME / "selected.json"
        original = Path.lstat
        before = self.files()
        for linked in (selection, environment):
            def attributes(path):
                if path == linked:
                    return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
                return original(path)
            self.calls.clear()
            with patch.object(Path, "lstat", attributes), patch.object(setup.uuid, "uuid4", return_value=SimpleNamespace(hex=generation)):
                with self.subTest(linked=linked), self.assertRaisesRegex(setup.PreparationError, "reparse"):
                    self.prepare(rebuild=True)
            self.assertFalse(any("venv" in c or "install" in c for c in self.calls))
            self.assertEqual(before, self.files())

    def test_rebuilt_selection_launch_remains_read_only_and_cli_modes_cannot_mix(self):
        selected = self.prepare(rebuild=True)
        before = self.files()
        self.calls.clear()
        with patch.object(setup.subprocess, "call", return_value=0) as call:
            self.assertEqual(setup.launch(self.root, ["--check"]), 0)
        self.assertEqual(call.call_args.args[0][0], str(selected))
        self.assertFalse(any("venv" in c or "install" in c for c in self.calls))
        self.assertEqual(before, self.files())
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            setup.main(["--check", "--rebuild"])
        self.assertEqual(error.exception.code, 2)
        with patch.object(setup, "ROOT", self.root), patch.object(setup, "prepare", return_value=selected) as prepare:
            self.assertEqual(setup.main(["--rebuild", "--python", str(self.base)]), 0)
        self.assertIs(prepare.call_args.kwargs["rebuild"], True)

    def test_preflight_and_pip_check_failure_retain_previous_selection(self):
        self.prepare()
        before = self.selected_bytes()
        (self.root / setup.LOCK_NAME).write_bytes(locked("1.2.4"))
        self.fail_check = True
        with self.assertRaisesRegex(setup.PreparationError, "consistency"):
            self.prepare()
        self.assertEqual(before, self.selected_bytes())
        self.fail_check = False
        self.preflight_ok = False
        with self.assertRaisesRegex(setup.PreparationError, "prerequisites"):
            self.prepare()
        self.assertEqual(before, self.selected_bytes())

    def test_unknown_runtime_and_unknown_environment_are_never_modified(self):
        runtime = self.root / setup.RUNTIME_NAME
        runtime.mkdir()
        (runtime / "keep.txt").write_text("unowned")
        before = self.files()
        for rebuild in (False, True):
            with self.subTest(rebuild=rebuild), self.assertRaises(setup.PreparationError):
                self.prepare(rebuild=rebuild)
            self.assertEqual(before, self.files())
        setup.atomic_json(runtime / "owner.json", setup.identity(self.root))
        env, _ = setup.env_details(self.root, hashlib.sha256(locked()).hexdigest())
        env.mkdir(parents=True)
        (env / "keep.txt").write_text("unowned environment")
        before_env = (env / "keep.txt").read_bytes()
        with self.assertRaises(setup.PreparationError):
            self.prepare()
        self.assertEqual((env / "keep.txt").read_bytes(), before_env)
        self.assertFalse((env / setup.OWNER_NAME).exists())
        unknown_files = self.environment_files(env)
        # A fresh generation may safely bypass an unknown baseline directory;
        # the package-level owner is valid, and that unknown directory is untouched.
        selected = self.prepare(rebuild=True)
        self.assertNotEqual(selected.parents[1], env)
        self.assertEqual(unknown_files, self.environment_files(env))

    def test_full_hash_collision_is_rejected_despite_short_directory_component(self):
        self.prepare()
        selected = json.loads(self.selected_bytes())
        changed = selected["lock_sha256"][:16] + "0" * 48
        selected["lock_sha256"] = changed
        setup.atomic_json(self.root / setup.RUNTIME_NAME / "selected.json", selected)
        with self.assertRaises(setup.PreparationError):
            setup.prepared_python(self.root)
        for malformed in (None, 123, [], "../../outside", "cmd & run"):
            selected["lock_sha256"] = malformed
            setup.atomic_json(self.root / setup.RUNTIME_NAME / "selected.json", selected)
            with self.subTest(malformed=malformed), self.assertRaises(setup.PreparationError):
                setup.prepared_python(self.root)

    def test_local_wheelhouse_never_uses_an_index(self):
        wheelhouse = self.root / "local wheels"
        wheelhouse.mkdir()
        self.prepare(wheelhouse=wheelhouse)
        install = next(c for c in self.calls if "install" in c)
        self.assertIn("--no-index", install)
        self.assertNotIn("--index-url", install)
        self.assertEqual(install[install.index("--find-links") + 1], str(wheelhouse))

    def test_check_and_launch_select_prepared_python_without_installation(self):
        selected = self.prepare()
        before = self.files()
        self.calls.clear()
        with patch.object(setup.subprocess, "call", return_value=0) as call:
            self.assertEqual(setup.launch(self.root, ["--check", "--port", "18881"]), 0)
        command = call.call_args.args[0]
        self.assertEqual(command[0], str(selected))
        self.assertIn("--check", command)
        self.assertEqual(command[command.index("--python") + 1], str(selected))
        self.assertEqual(command[command.index("--binary") + 1], str(self.binary))
        self.assertFalse(any("install" in c or "venv" in c for c in self.calls))
        self.assertEqual(before, self.files())

    def test_launch_cannot_bypass_prepared_python_with_argparse_abbreviations(self):
        for option in ("--python", "--python=C:/outside.exe", "--py", "--py=C:/outside.exe", "--pyth"):
            with self.subTest(option=option), self.assertRaisesRegex(setup.PreparationError, "prepared interpreter"):
                setup.launch(self.root, [option, "anything"])
        self.assertFalse((self.root / setup.RUNTIME_NAME).exists())

    def test_missing_or_tampered_selection_never_starts_or_prepares(self):
        before = self.files()
        with patch.object(setup.subprocess, "call") as call:
            with self.assertRaisesRegex(setup.PreparationError, "No prepared"):
                setup.launch(self.root, ["--check"])
            call.assert_not_called()
        self.assertEqual(before, self.files())
        selected = self.prepare()
        (selected.parents[1] / "requirements.lock").write_bytes(locked("9.9.9"))
        with self.assertRaisesRegex(setup.PreparationError, "snapshot changed"):
            setup.launch(self.root, ["--check"])

    def test_reparse_component_and_reparse_descendant_are_rejected(self):
        selected = self.prepare()
        directory = selected.parent
        original = Path.lstat
        def attributes(path):
            if path == directory:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return original(path)
        with patch.object(Path, "lstat", attributes):
            with self.assertRaisesRegex(setup.PreparationError, "reparse"):
                self.prepare()
        extra = selected.parents[1] / "Lib/site-packages/linked"
        extra.mkdir(parents=True)
        def descendant_attributes(path):
            if path == extra:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return original(path)
        with patch.object(Path, "lstat", descendant_attributes):
            with self.assertRaisesRegex(setup.PreparationError, "reparse"):
                self.prepare()

    def test_install_environment_disables_config_credentials_and_raw_failure_output(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "secret", "CIVIL_TOKEN": "secret", "PIP_INDEX_URL": "https://secret.invalid",
                                    "PIP_TARGET": "outside", "PYTHONPATH": "outside", "HTTP_PROXY": "https://secret.invalid"}):
            env = setup.clean_environment()
        for key in ("OPENAI_API_KEY", "CIVIL_TOKEN", "PIP_INDEX_URL", "PIP_TARGET", "PYTHONPATH", "HTTP_PROXY"):
            self.assertNotIn(key, env)
        self.assertEqual(env["PIP_CONFIG_FILE"], os.devnull)
        self.assertEqual(env["NETRC"], os.devnull)
        self.assertEqual(env["NO_PROXY"], "*")
        self.assertEqual(env["PIP_KEYRING_PROVIDER"], "disabled")
        self.fail_install = True
        with self.assertRaises(setup.PreparationError) as error:
            self.prepare()
        self.assertNotIn("private-output", str(error.exception) + self.output.getvalue())

    def test_wrong_architecture_or_python_version_fails_before_writing(self):
        for changed in ({"bits": 32}, {"build_platform": "win32"},
                        {"build_platform": "win-arm64", "machine": "AMD64"},
                        {"build_platform": "win-arm32"}, {"build_platform": ""},
                        {"version": [3, 12, 0]}, {"platform": "linux"}, {"implementation": "pypy"}):
            data = {**self.info(str(self.base)), **changed}
            with self.subTest(changed=changed), patch.object(setup, "run_command", return_value=SimpleNamespace(
                    returncode=0, stdout=json.dumps(data).encode(), stderr=b"")):
                with self.assertRaisesRegex(setup.PreparationError, "CPython 3.11"):
                    self.prepare()
                self.assertFalse((self.root / setup.RUNTIME_NAME).exists())


class InterpreterProbeTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows interpreter-build probe integration")
    def test_windows_probe_uses_build_metadata_with_processor_environment_removed(self):
        # platform.machine() reads PROCESSOR_ARCHITECTURE on Windows; the
        # installer intentionally strips it along with caller configuration.
        # Exercise the real isolated subprocess, rather than its JSON fixture.
        with tempfile.TemporaryDirectory(prefix="civil-probe-") as temp:
            with patch.dict(os.environ, {"PROCESSOR_ARCHITECTURE": "ARM64",
                                         "PROCESSOR_ARCHITEW6432": "ARM64",
                                         "DEEPSEEK_API_KEY": "never-forward-probe-fixture"}):
                env = setup.clean_environment()
                for key in ("PROCESSOR_ARCHITECTURE", "PROCESSOR_ARCHITEW6432", "DEEPSEEK_API_KEY"):
                    self.assertNotIn(key, env)
                result = setup.run_command([sys.executable, "-I", "-B", "-c", setup.PROBE],
                                           Path(temp), timeout=20)
                self.assertEqual(result.returncode, 0, "isolated interpreter probe failed")
                data = json.loads(result.stdout)
                self.assertEqual(data["build_platform"], sysconfig.get_platform())
                self.assertNotIn("never-forward-probe-fixture", result.stdout.decode("utf-8"))
                if sys.version_info[:2] == (3, 11) and sysconfig.get_platform() == "win-amd64":
                    self.assertEqual(setup.interpreter_info(sys.executable, Path(temp))["build_platform"],
                                     "win-amd64")
                else:
                    with self.assertRaises(setup.PreparationError):
                        setup.interpreter_info(sys.executable, Path(temp))


class BatchEntryTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows batch entry integration")
    def test_both_batch_locations_forward_spaces_and_never_install_in_stub(self):
        # Exercise cmd parsing, not a hand-rolled imitation. The stub receives all
        # arguments under a real Python; it never imports or runs preparation.
        with tempfile.TemporaryDirectory(prefix="civil-bat-") as temp:
            root = Path(temp) / "中文 package ! with spaces"
            scripts = root / "scripts"
            scripts.mkdir(parents=True)
            stub = scripts / "prepare_unified_workbench.py"
            stub.write_text("import json,sys\nprint(json.dumps(sys.argv[1:],ensure_ascii=True))\n", encoding="utf-8")
            env = os.environ.copy()
            env["CIVIL_PYTHON"] = sys.executable
            for name in ("prepare-unified-workbench.bat", "start-unified-workbench.bat"):
                for directory in (root, scripts):
                    target = directory / name
                    shutil.copyfile(ROOT / "scripts" / name, target)
                    command = ["cmd.exe", "/d", "/v:on", "/c", name, "--check", "--binary", r"C:\path with spaces\host.exe"]
                    result = subprocess.run(command, cwd=directory, env=env,
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
                    args = json.loads(result.stdout)
                    self.assertIn("--check", args)
                    self.assertIn("C:\\path with spaces\\host.exe", args)
                    if name.startswith("start-"):
                        self.assertEqual(args[:2], ["--launch", "--open"])
                    else:
                        self.assertNotIn("--launch", args)


if __name__ == "__main__":
    unittest.main()
