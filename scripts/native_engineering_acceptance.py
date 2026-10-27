"""Strict, model-free acceptance of installed native engines on synthetic inputs.

Missing dependencies, skips, expected failures and an unexpectedly small suite
fail this gate. No installation, deployment or professional approval occurs.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SUITES = (
    ("scripts.test_frame_analysis", 16),
    ("scripts.test_engineering_section", 10),
    ("scripts.test_engineering_ifc", 9),
    ("scripts.test_engineering_api", 9),
)
STEP_SUITE = ("scripts.test_cad_step", 4)
DEPENDENCIES = (
    ("Pynite", "PyniteFEA", "3.2.0"),
    ("sectionproperties", "sectionproperties", "3.10.2"),
    ("ifcopenshell", "ifcopenshell", "0.8.5"),
    ("ifctester", "ifctester", "0.8.5"),
    ("ifcdiff", "ifcdiff", "0.8.5"),
    ("defusedxml", "defusedxml", "0.7.1"),
    ("ezdxf", "ezdxf", None),
    ("shapely", "shapely", None),
    ("trimesh", "trimesh", None),
    ("mapbox_earcut", "mapbox-earcut", None),
    ("fastapi", "fastapi", None),
    ("httpx", "httpx", None),
    ("multipart", "python-multipart", None),
    ("dotenv", "python-dotenv", None),
)
STEP_DEPENDENCIES = (("cadquery", "cadquery", None), ("OCP", "cadquery-ocp", None))
TRANSITIVE_DISTRIBUTIONS = (
    "numpy", "scipy", "numba", "llvmlite", "matplotlib", "cytriangle",
    "xmlschema", "elementpath", "deepdiff", "lxml", "starlette", "pydantic",
)


def offline_environment() -> dict[str, str]:
    env = dict(os.environ)
    for key in tuple(env):
        if key.endswith("_API_KEY"):
            env.pop(key)
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               PYTHON_DOTENV_DISABLED="1", PYTHONOPTIMIZE="0",
               MPLBACKEND="Agg", NUMBA_NUM_THREADS="1",
               OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1")
    return env


def dependency_snapshot(*, with_step=False) -> list[dict]:
    entries = []
    for module, distribution, pinned in DEPENDENCIES + (STEP_DEPENDENCIES if with_step else ()):
        entry = {"module": module, "distribution": distribution, "required_version": pinned}
        try:
            entry["available"] = importlib.util.find_spec(module) is not None
            entry["version"] = importlib.metadata.version(distribution)
        except (ImportError, ValueError, importlib.metadata.PackageNotFoundError):
            entry.update(available=False, version=None)
        entries.append(entry)
    return entries


def preflight_errors(entries: list[dict], *, with_step=False) -> list[str]:
    errors = []
    by_module = {entry["module"]: entry for entry in entries}
    for module, distribution, pinned in DEPENDENCIES + (STEP_DEPENDENCIES if with_step else ()):
        entry = by_module.get(module, {})
        if not entry.get("available") or not entry.get("version"):
            errors.append(f"Missing dependency: {distribution}")
        elif pinned is not None and entry["version"] != pinned:
            errors.append(f"{distribution}: expected {pinned}, installed {entry['version']}")
    if with_step and by_module.get("cadquery", {}).get("version"):
        from packaging.specifiers import SpecifierSet
        from packaging.version import Version, InvalidVersion
        try:
            supported = Version(by_module["cadquery"]["version"]) in SpecifierSet(">=2.8,<3")
        except InvalidVersion:
            supported = False
        if not supported:
            errors.append("CadQuery version must satisfy requirements-cad-step.txt: >=2.8,<3")
    return errors


def transitive_versions() -> dict[str, str | None]:
    versions = {}
    for distribution in TRANSITIVE_DISTRIBUTIONS:
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = None
    return versions


class EvidenceResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.successes = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.successes.append(test.id())


def run_suite(name: str, minimum: int, *, loader=None) -> dict:
    started = time.monotonic()
    suite = (loader or unittest.defaultTestLoader).loadTestsFromName(name)
    result = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(suite)
    report = {
        "module": name, "minimum_tests": minimum, "tests_run": result.testsRun,
        "passed_tests": result.successes,
        "failures": [{"test": test.id(), "detail": detail} for test, detail in result.failures],
        "errors": [{"test": test.id(), "detail": detail} for test, detail in result.errors],
        "skipped": [{"test": test.id(), "reason": reason} for test, reason in result.skipped],
        "expected_failures": [test.id() for test, _ in result.expectedFailures],
        "unexpected_successes": [test.id() for test in result.unexpectedSuccesses],
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    report["passed"] = (
        result.wasSuccessful() and result.testsRun >= minimum
        and len(result.successes) == result.testsRun
        and not result.skipped and not result.expectedFailures
    )
    return report


def source_snapshot(*, with_step=False) -> dict[str, str | None]:
    suites = SUITES + ((STEP_SUITE,) if with_step else ())
    names = {name.replace(".", "/") + ".py" for name, _ in suites}
    names.update({"scripts/native_engineering_acceptance.py", "scripts/test_cad_api.py",
                  "demo/engineering_api.py", "demo/cad_api.py",
                  "requirements-analysis.txt", "requirements-engineering.txt",
                  "requirements-cad.txt", "requirements-planning.txt"})
    if with_step:
        names.update({"requirements-cad-step.txt", "packing_assistant/cad3d/step.py",
                      "packing_assistant/cad3d/geometry.py",
                      "examples/cad-to-3d/synthetic-hollow-section-mm.dxf",
                      "examples/cad-to-3d/synthetic-section-config.json"})
    names.update(path.relative_to(ROOT).as_posix()
                 for path in (ROOT / "packing_assistant/engineering").rglob("*.py"))
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            if (ROOT / name).is_file() else None for name in sorted(names)}


def source_commit() -> str | None:
    # Do not climb into a parent checkout when testing an extracted package.
    if (ROOT / ".git").exists():
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                capture_output=True, text=True, timeout=10)
        return result.stdout.strip() if result.returncode == 0 else None
    manifest = ROOT / "release-manifest.json"
    return json.loads(manifest.read_text(encoding="utf-8")).get("source_commit") if manifest.is_file() else None


def run_acceptance(*, with_step=False) -> dict:
    started = time.monotonic()
    dependencies = dependency_snapshot(with_step=with_step)
    sources = source_snapshot(with_step=with_step)
    errors = preflight_errors(dependencies, with_step=with_step)
    if sys.flags.optimize:
        errors.append("Restart Python without -O: acceptance assertions must remain enabled")
    errors.extend(f"Missing acceptance source: {name}" for name, digest in sources.items() if digest is None)
    selected_suites = SUITES + ((STEP_SUITE,) if with_step else ())
    suites = [] if errors else [run_suite(name, minimum) for name, minimum in selected_suites]
    sources_unchanged = sources == source_snapshot(with_step=with_step)
    return {
        "schema": "civil-buddy.native-engineering-acceptance.v1",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "passed": not errors and sources_unchanged and len(suites) == len(selected_suites)
                  and all(suite["passed"] for suite in suites),
        "input_type": "synthetic", "same_machine": True,
        "with_step": with_step,
        "cross_computer_tested": False, "real_project_tested": False,
        "live_model_tested": False, "deployment_tested": False,
        "professional_approval": False,
        "python": platform.python_version(), "platform": platform.platform(),
        "source_commit": source_commit(), "source_files_sha256": sources,
        "source_identity_note": "HEAD or package-declared commit; file hashes describe actual tested bytes, not binary provenance",
        "sources_unchanged": sources_unchanged, "dependencies": dependencies,
        "transitive_versions": transitive_versions(),
        "preflight_errors": errors, "suites": suites,
        "tests_run": sum(suite["tests_run"] for suite in suites),
        "skipped": sum(len(suite["skipped"]) for suite in suites),
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="New JSON report path; existing files are never overwritten")
    parser.add_argument("--with-step", action="store_true", help="Also require installed CadQuery/OCP and zero-skip STEP tests")
    args = parser.parse_args(argv)
    report_path = args.report or ROOT / "output/native-engineering" / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    if report_path.exists():
        parser.error("Report already exists; choose a new path")
    sys.path.insert(0, str(ROOT))
    env = offline_environment()
    os.environ.clear()
    os.environ.update(env)
    report = run_acceptance(with_step=args.with_step)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2, allow_nan=False)
        output.write("\n")
    print(json.dumps({"passed": report["passed"], "tests_run": report["tests_run"],
                      "skipped": report["skipped"], "report": str(report_path),
                      "preflight_errors": report["preflight_errors"]}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
