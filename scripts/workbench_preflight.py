"""Read-only, offline launcher diagnostics; never import product modules."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess


# Keep aligned with the base requirements, not optional engineering/model packages.
# Metadata + module discovery avoids import-time caches, model downloads and app stores.
REQUIRED = {
    "langgraph": ("langgraph", "0.2.0"),
    "langgraph-checkpoint-sqlite": ("langgraph", "3.0.0"),
    "langchain-core": ("langchain_core", "0.3.0"),
    "langchain-openai": ("langchain_openai", "0.2.0"),
    "typing-extensions": ("typing_extensions", "4.8.0"),
    "matplotlib": ("matplotlib", "3.8.0"),
    "pydantic": ("pydantic", "2.0.0"),
    "python-dotenv": ("dotenv", "1.0.0"),
    "fastapi": ("fastapi", "0.110.0"),
    "uvicorn": ("uvicorn", "0.27.0"),
    "httpx": ("httpx", "0.27.0"),
    "python-multipart": ("multipart", "0.0.9"),
    "openpyxl": ("openpyxl", "3.1.0"),
    "pypdf": ("pypdf", "5.0.0"),
}
OPTIONAL = {
    "CAD": ("requirements-cad.txt", ("ezdxf", "shapely", "trimesh", "mapbox_earcut")),
    "engineering": ("requirements-engineering.txt", ("Pynite", "sectionproperties", "ifcopenshell", "ifctester", "ifcdiff")),
    "planning": ("requirements-planning.txt", ("ortools", "networkx", "defusedxml", "mpxj", "jpype")),
    "local voice": ("requirements-asr.txt", ("faster_whisper",)),
}
PROBE_TIMEOUT = 20
PROBE = r"""
import importlib.metadata as metadata, importlib.util, json, sys
request = json.loads(sys.argv[1])
def found(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False
required = {}
for package, module in request['required'].items():
    try:
        version = metadata.version(package) if found(module) else None
    except metadata.PackageNotFoundError:
        version = None
    required[package] = version
print(json.dumps({'version': list(sys.version_info[:3]), 'required': required,
                  'optional': {name: found(name) for name in request['optional']}}))
"""


@dataclass
class Report:
    python: str | None = None
    version: str | None = None
    errors: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)
    missing_optional: dict[str, list[str]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors and not self.missing_required


def diagnostic_environment() -> dict[str, str]:
    # An allowlist also removes unknown/custom provider credentials and PYTHONPATH.
    keep = {"PATH", "SYSTEMROOT", "WINDIR", "PATHEXT", "TEMP", "TMP", "APPDATA",
            "LOCALAPPDATA", "USERPROFILE", "LANG", "HOME"}
    env = {key: value for key, value in os.environ.items() if key.upper() in keep}
    env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", PYTHON_DOTENV_DISABLED="1",
               CIVIL_DOTENV_DISABLED="1")
    return env


def resolve_python(value: str) -> str:
    """Resolve PATH names with shell semantics; retain an explicit venv executable."""
    selected = Path(value).expanduser()
    if selected.is_absolute() or selected.parent != Path(".") or "/" in value or "\\" in value:
        candidate = selected.absolute()
    else:
        located = shutil.which(value)
        if not located:
            raise ValueError("Python was not found on PATH. Use --python with the full path to your Python 3.11+ environment.")
        candidate = Path(located).absolute()
    if not candidate.is_file():
        raise ValueError("Selected Python executable does not exist. Use --python with an existing Python 3.11+ executable.")
    # Do not resolve symlinks: on Unix a venv may link to its base interpreter.
    return str(candidate)


def check_port(port: int) -> str | None:
    if not 1 <= port <= 65535:
        return "Port must be between 1 and 65535. Choose a free port with --port 8766."
    try:
        with socket.socket() as listener:
            if os.name == "nt":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                # Match the POSIX server bind: a previous accepted connection can
                # leave TIME_WAIT after the listener closes. SO_REUSEADDR permits
                # that restart, while a live listener still blocks the bind.
                # Never enable SO_REUSEPORT, which could hide an active service.
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", port))
    except OSError:
        return f"Port {port} is occupied or unavailable. Stop the existing workbench, or choose a different free port with --port 8766."
    return None


def run(*, python: str, binary: Path, port: int, root: Path,
        env_file: Path | None = None) -> Report:
    report = Report()
    if not binary.is_file():
        report.errors.append("Rust executable missing. Use the release package executable via --binary, or run cargo build --release --manifest-path workbench/Cargo.toml.")
    problem = check_port(port)
    if problem:
        report.errors.append(problem)
    if env_file is not None and not env_file.is_file():
        report.errors.append("Selected --env-file does not exist. Choose an existing configuration file or omit --env-file for offline use.")
    try:
        report.python = resolve_python(python)
    except ValueError as exc:
        report.errors.append(str(exc))
        return report
    payload = {"required": {name: item[0] for name, item in REQUIRED.items()},
               "optional": sorted({name for _, modules in OPTIONAL.values() for name in modules})}
    try:
        result = subprocess.run([report.python, "-I", "-B", "-c", PROBE, json.dumps(payload)],
                                cwd=root, env=diagnostic_environment(), timeout=PROBE_TIMEOUT,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if result.returncode or len(result.stdout) > 65536:
            raise ValueError("probe failed")
        data = json.loads(result.stdout)
        version = data["version"]
        if len(version) != 3 or any(type(part) is not int for part in version):
            raise ValueError("invalid version")
        report.version = ".".join(map(str, version))
        if tuple(version) < (3, 11, 0):
            report.errors.append("Python 3.11 or newer is required. Select it with --python.")
        for name, (_, minimum) in REQUIRED.items():
            installed = data["required"][name]
            match = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?(?:$|[.+-])", installed or "")
            if not match or tuple(int(part or 0) for part in match.groups()) < tuple(map(int, minimum.split("."))):
                report.missing_required.append(f"{name}>={minimum}")
        for group, (_, modules) in OPTIONAL.items():
            missing = [name for name in modules if data["optional"].get(name) is not True]
            if missing:
                report.missing_optional[group] = missing
    except subprocess.TimeoutExpired:
        report.errors.append(f"Python dependency check exceeded {PROBE_TIMEOUT} seconds. Verify the selected environment, then retry --check.")
    except (OSError, ValueError, KeyError, TypeError):
        # Raw subprocess output/errors can contain environment secrets: never echo them.
        report.errors.append("Python dependency check failed. Select a working Python 3.11+ environment with --python and retry --check.")
    return report


def powershell_quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def format_report(report: Report, root: Path) -> str:
    lines = ["Civil Buddy offline preflight / 启动前检查"]
    if report.python:
        lines.append(f"Python: {report.python}" + (f" ({report.version})" if report.version else ""))
    for error in report.errors:
        lines.append(f"[FAIL] {error}")
    if report.missing_required:
        lines.append("[FAIL] Required packages missing or below minimum version: " + ", ".join(report.missing_required))
        lines.append("Install with PowerShell: & " + powershell_quote(report.python) + " -m pip install -r "
                     + powershell_quote(root / "requirements.txt") + " -r " + powershell_quote(root / "requirements-documents.txt"))
    for group, missing in report.missing_optional.items():
        lines.append(f"[OPTIONAL] {group}: " + ", ".join(missing) + "; base startup is not blocked.")
        lines.append("  Install if needed: & " + powershell_quote(report.python) + " -m pip install -r "
                     + powershell_quote(root / OPTIONAL[group][0]))
    lines.append("[OK] Startup prerequisites passed." if report.ok else "[FAIL] Fix the required items above, then rerun --check.")
    lines.append("Offline metadata/module checks only; no product/provider started, no model downloaded. Optional native libraries/models and live service health are not verified.")
    return "\n".join(lines)


def load_environment_file(python: str, path: Path, environment: dict[str, str], root: Path) -> dict[str, str]:
    """Normal launch only: read user-selected dotenv with the selected interpreter.

    This is configuration loading, not preflight. Ambient values are supplied for
    existing dotenv interpolation semantics; neither output nor errors are logged.
    """
    code = "import json,sys; from dotenv import dotenv_values; print(json.dumps({k:v for k,v in dotenv_values(sys.argv[1]).items() if v is not None}))"
    try:
        result = subprocess.run([python, "-I", "-B", "-c", code, str(path.resolve())],
                                cwd=root, env=environment, timeout=10, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if result.returncode or len(result.stdout) > 1_048_576:
            raise ValueError("invalid configuration")
        values = json.loads(result.stdout)
        if not isinstance(values, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in values.items()):
            raise ValueError("invalid configuration")
        return values
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise ValueError("Cannot read --env-file with the selected Python. Check the file and python-dotenv installation; configuration values are not logged.") from None
