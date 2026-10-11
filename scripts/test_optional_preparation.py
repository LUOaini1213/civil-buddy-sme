"""Offline regression for the fixed optional-environment native smoke contract."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


HELPER = Path(__file__).with_name("check_optional_preparation.py")
spec = importlib.util.spec_from_file_location("optional_preparation_check", HELPER)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


@pytest.mark.parametrize("args", [[], ["--profile"], ["--profile", "unknown"],
                                  ["--profile", "core", "--extra"], ["--help"]])
def test_invalid_cli_is_one_bounded_json_value_without_raw_arguments(args):
    out = subprocess.run([sys.executable, "-I", "-B", str(HELPER), *args],
                         capture_output=True, text=True, timeout=15)
    assert out.returncode == 2 and not out.stderr
    value = json.loads(out.stdout)
    assert value == {"passed": False, "profile": None, "checks": [
        {"check": "arguments", "module": "stdlib", "passed": False, "error": "invalid_arguments"}
    ]}
    assert str(HELPER) not in out.stdout and len(out.stdout) < 512


def test_core_cli_needs_no_optional_imports_and_reports_only_package_scope(monkeypatch):
    def forbidden_import(name):
        raise AssertionError("core must not import optional modules")
    monkeypatch.setattr(helper.importlib, "import_module", forbidden_import)
    assert helper.check_profile("core") == {"passed": True, "profile": "core", "checks": [
        {"check": "no_optional_packages_requested", "module": "stdlib", "passed": True}
    ]}
    out = subprocess.run([sys.executable, "-I", "-B", str(HELPER), "--profile", "core"],
                         capture_output=True, text=True, timeout=15)
    assert out.returncode == 0 and not out.stderr and json.loads(out.stdout)["passed"] is True


@pytest.mark.parametrize("exc, classification", [
    (ModuleNotFoundError("SECRET user-home token"), "module_missing"),
    (ImportError("SECRET DLL path"), "native_load_failed"),
    (OSError("SECRET PATH and credential"), "native_load_failed"),
    (RuntimeError("SECRET native failure"), "operation_failed"),
])
def test_failures_are_complete_and_classified_without_diagnostics(monkeypatch, exc, classification):
    seen = []
    def fail(name):
        seen.append(name)
        raise exc
    monkeypatch.setattr(helper.importlib, "import_module", fail)
    report = helper.check_profile("speech-planning")
    assert not report["passed"] and report["profile"] == "speech-planning"
    assert len(report["checks"]) == 8 and len(seen) == 8
    assert all(c["error"] == classification and c["exception"] == type(exc).__name__
               and c["passed"] is False for c in report["checks"])
    assert "SECRET" not in json.dumps(report) and len(json.dumps(report)) < 2048


def test_unusual_exception_class_name_does_not_leak(monkeypatch):
    private_error = type("PrivateProjectName", (Exception,), {})
    monkeypatch.setattr(helper.importlib, "import_module", lambda name: (_ for _ in ()).throw(private_error("secret")))
    report = helper.check_profile("speech")
    assert all(c["exception"] == "Exception" for c in report["checks"])
    assert "PrivateProjectName" not in json.dumps(report)


def test_foreign_module_origin_refuses_candidate_readiness(monkeypatch, tmp_path):
    monkeypatch.setattr(helper.importlib, "import_module", lambda name: SimpleNamespace(__file__=str(tmp_path / "external.py")))
    report = helper.check_profile("speech")
    assert not report["passed"]
    assert all(c["exception"] == "AssertionError" for c in report["checks"])
    assert str(tmp_path) not in json.dumps(report)


@pytest.mark.parametrize("supported, passed", [({"float32"}, False), ({"int8"}, False),
                                               ({"float32", "int8"}, True)])
def test_cpu_check_matches_product_int8_configuration(monkeypatch, supported, passed):
    monkeypatch.setattr(helper, "_optional_import", lambda name: SimpleNamespace(
        get_supported_compute_types=lambda device: supported if device == "cpu" else set()))
    if passed:
        helper._ctranslate2()
    else:
        with pytest.raises(AssertionError):
            helper._ctranslate2()


def test_engine_import_does_not_construct_model(monkeypatch):
    def never_construct(*args, **kwargs):
        raise AssertionError("model weights must never be loaded")
    monkeypatch.setattr(helper, "_optional_import", lambda name: SimpleNamespace(WhisperModel=never_construct))
    helper._faster_whisper()


def test_av_must_decode_expected_frames_not_merely_import(monkeypatch):
    class Container:
        streams = SimpleNamespace(audio=[object()])
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def decode(self, **kwargs):
            return [SimpleNamespace(sample_rate=16000, samples=1, planes=[b"\x01\x00"])]
    monkeypatch.setattr(helper, "_optional_import", lambda name: SimpleNamespace(open=lambda *a, **k: Container()))
    with pytest.raises(AssertionError):
        helper._av()


def test_xml_entities_must_be_rejected(monkeypatch):
    class EntitiesForbidden(Exception): pass
    class Root:
        tag = "project"
        def find(self, name): return SimpleNamespace(get=lambda key: "3")
    monkeypatch.setattr(helper, "_optional_import", lambda name: SimpleNamespace(
        EntitiesForbidden=EntitiesForbidden) if name.endswith("common")
        else SimpleNamespace(fromstring=lambda text: Root()))
    with pytest.raises(AssertionError):
        helper._defusedxml()


def test_existing_complete_environment_runs_all_fixed_operations_without_models():
    # This is optional local evidence, not a requirement that CI install speech.
    candidate = HELPER.parents[2] / "practical-complete-venv" / "Scripts" / "python.exe"
    if not candidate.is_file():
        pytest.skip("complete optional environment is not available locally")
    out = subprocess.run([str(candidate), "-I", "-B", str(HELPER), "--profile", "speech-planning"],
                         capture_output=True, text=True, timeout=120)
    report = json.loads(out.stdout)
    assert out.returncode == 0, report
    assert report["passed"] is True and report["profile"] == "speech-planning"
    assert len(report["checks"]) == 8 and all(c["passed"] for c in report["checks"])
    assert "model_ready" not in report and "microphone" not in report and len(out.stdout) < 2048
