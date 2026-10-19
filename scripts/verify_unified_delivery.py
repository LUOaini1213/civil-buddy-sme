"""Offline delivery gate: selected Git commit plus a real extracted-package reader.

Uses installed Python dependencies, synthetic workbooks and no executable/model.
This is not a clean dependency install, GUI or second-computer acceptance test.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

try:
    from . import build_unified_release as release
except ImportError:
    import build_unified_release as release


READER_CHECK = r'''
import hashlib, json, sys
from pathlib import Path
import openpyxl

root, scratch = map(Path, sys.argv[1:3])
sys.path.insert(0, str(root))
from packing_assistant.tools import table_mapper
module = Path(table_mapper.__file__).resolve()
if not module.is_relative_to(root.resolve()):
    raise RuntimeError("Reader did not load from the extracted package")

def workbook(name, sheets):
    path = scratch / name
    book = openpyxl.Workbook()
    book.active.title = "Cover"
    book.active.append(["SYNTHETIC PROJECT", "DELIVERY TEST ONLY"])
    for title, mark in sheets:
        sheet = book.create_sheet(title)
        sheet.append(["Mark", "Description", "Qty", "Unit Wt (kg)", "L (mm)", "W (mm)", "H (mm)"])
        sheet.append([mark, "SYNTHETIC panel", 2, 100, 1000, 500, 200])
    book.active = 0
    book.save(path)
    book.close()
    return path

def parse(path):
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = table_mapper.parse_table_file(str(path))
    if hashlib.sha256(path.read_bytes()).hexdigest() != before:
        raise RuntimeError("Reader changed the original workbook")
    return result

covered = parse(workbook("covered.xlsx", [("East elevation", "P1")]))
if not (covered["ok"] and covered["reading"]["sheet"] == "East elevation"
        and len(covered["materials"]) == 1 and covered["stats"]["total_weight_kg"] == 200):
    raise RuntimeError("Covered material sheet was not read correctly: " + json.dumps(covered, ensure_ascii=True))
ambiguous = parse(workbook("ambiguous.xlsx", [("East elevation", "P1"), ("West elevation", "P2")]))
if ambiguous["ok"] or ambiguous["materials"] or not ambiguous["errors"]:
    raise RuntimeError("Ambiguous material sheets must fail without silently choosing or adding rows")
explicit = parse(workbook("explicit.xlsx", [("East elevation", "P1"), ("materials", "P2")]))
if not (explicit["ok"] and explicit["reading"]["sheet"] == "materials"
        and len(explicit["materials"]) == 1 and explicit["materials"][0]["id"] == "P2"):
    raise RuntimeError("Explicit materials sheet compatibility failed")
print(json.dumps({"module": str(module), "checks": {"cover_then_materials": True,
      "ambiguous_sheets_rejected": True, "explicit_materials_preserved": True,
      "originals_unchanged": True}}, ensure_ascii=True))
'''


def check_extracted(extracted: Path, scratch: Path) -> dict:
    scratch.mkdir(parents=True, exist_ok=True)
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith(
        ("CIVIL_", "PACKING_", "OPENAI_", "DEEPSEEK_", "LLM_", "PYTHONPATH", "PYTHONHOME"))
        and not key.upper().endswith("_API_KEY")}
    env.update(PYTHONUTF8="1", PYTHON_DOTENV_DISABLED="1")
    process = subprocess.run([sys.executable, "-I", "-B", "-c", READER_CHECK,
                              str(extracted.resolve()), str(scratch.resolve())],
                             cwd=scratch, env=env, capture_output=True, timeout=60)
    if process.returncode:
        raise ValueError("Extracted-package reader failed: " + process.stderr.decode("utf-8", "replace"))
    return json.loads(process.stdout)


def verify_delivery(archive: Path, source_root: Path, expected_commit: str, extracted: Path) -> dict:
    manifest = release.verify_source(archive, source_root, expected_commit)
    # Never merge into a previous extraction: otherwise a missing member can be
    # supplied by stale files. verify_source already checked every ZIP path.
    extracted.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive) as package:
        package.extractall(extracted)
    with tempfile.TemporaryDirectory(prefix="civil-delivery-inputs-") as scratch:
        reader = check_extracted(extracted, Path(scratch))
    return {"passed": True, "source_commit": manifest["source_commit"],
            "archive_sha256": release.digest(archive), "binary_sha256": manifest["binary"]["sha256"],
            "source_files_verified": len(manifest["files"]) - 1, "reader": reader,
            "scope": "Exact committed source; freshly extracted package reader; synthetic data and existing dependencies. No binary, GUI, fresh install or second-computer test."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True, help="Commit selected by the reviewer, not copied blindly from the package")
    parser.add_argument("--extract-dir", type=Path, help="Optional new directory to retain for further package acceptance")
    args = parser.parse_args()
    try:
        if args.extract_dir:
            report = verify_delivery(args.archive, args.source_root, args.expected_commit, args.extract_dir)
        else:
            with tempfile.TemporaryDirectory(prefix="civil-delivery-") as scratch:
                report = verify_delivery(args.archive, args.source_root, args.expected_commit, Path(scratch) / "package")
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"Delivery verification failed: {exc}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
