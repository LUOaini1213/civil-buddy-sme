"""One deterministic request; Rust owns authorization, sessions and model calls.

Only this fixed bootstrap runs before confinement. Its policy is supplied through
the scrubbed host environment, never through model-selected request fields.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

# Launched with python -I: do not import from cwd, PYTHONPATH or user site.
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
MAX_INPUT = 32 * 1024 * 1024
MAX_OUTPUT = 12 * 1024 * 1024
MODULES = {"packing_assistant.documents.worker", "packing_assistant.engineering.worker",
           "packing_assistant.retrieval.worker", "packing_assistant.host_worker", "packing_assistant.review_worker"}


def _error(code, message, call_id=None):
    return {"version": 1, "ok": False, "call_id": call_id, "result": None,
            "error": {"code": code, "message": message}}


def _checked_path(root: Path, value, *, write=False):
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError("A bounded workspace path is required")
    given = Path(value)
    relative = given.relative_to(root) if given.is_absolute() else given
    if any(part in ("..", ".") for part in relative.parts):
        raise ValueError("Path traversal is not allowed")
    current = root
    for part in relative.parts:
        lower = part.lower()
        stem = lower.split(".")[0]
        if (lower in {".env", ".git", ".ssh"} or lower.startswith(".env.")
                or lower.endswith((".pem", ".key", ".p12", ".pfx", " ", "."))
                or stem in {"con", "prn", "aux", "nul"} or re.fullmatch(r"(?:com|lpt)[1-9]", stem)
                or any(ord(c) < 32 or c in ':\\<>"|?*' for c in part)):
            raise ValueError("Secret or unsupported path component")
        current = current / part
        try:
            metadata = current.lstat()
            reparse = bool(getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        except FileNotFoundError:
            reparse = False
        if current.is_symlink() or reparse:
            raise ValueError("Links and junctions are not permitted")
    resolved = current.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Path escapes workspace")
    if write and not resolved.is_relative_to(root / ".civil-buddy" / "out"):
        raise ValueError("Writes must remain in the workspace output directory")
    return resolved


def _bootstrap():
    asked = os.environ.get("CIVIL_HOST_SANDBOX", "os")
    if asked not in {"app", "os"}:
        raise ValueError("Unknown sandbox backend")
    root = Path(os.environ["CIVIL_HOST_WORKSPACE"]).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Workspace is not a directory")
    output = _checked_path(root, ".civil-buddy/out", write=True)
    temporary = _checked_path(root, ".civil-buddy/out/.tmp", write=True)
    real_temp = tempfile.gettempdir()
    if asked == "os":
        from packing_assistant.runtime.os_sandbox import prepare
        prepare(root)
    else:
        temporary.mkdir(parents=True, exist_ok=True)
    # All caches/temp files of trusted libraries stay under the allowed root.
    for key in ("TEMP", "TMP", "TMPDIR", "MPLCONFIGDIR", "NUMBA_CACHE_DIR", "XDG_CACHE_HOME"):
        os.environ[key] = str(temporary)
    tempfile.tempdir = str(temporary)
    if asked == "app":
        report = {"asked": "app", "backend": "application-policy", "available": True,
                  "enforces": {"write": False, "spawn": False, "network": False, "read": False},
                  "selftest": None}
    else:
        # Reuse only kernel primitives and their probes, never the old agent or
        # model_tool entry points. Confinement is irreversible in this process.
        from packing_assistant.runtime.os_sandbox.worker import _confine, selftest
        policy = {"job_root": str(root), "write_roots": [str(output), str(temporary)],
                  "network": False, "spawn": False, "real_temp": real_temp}
        report = _confine(policy)
        checks = selftest(policy)
        if (checks["write_inside_state"] != "allowed"
                or any(checks[key] != "denied" for key in ("write_job_folder", "write_home", "spawn_process"))
                or not report["enforces"].get("write") or not report["enforces"].get("spawn")):
            raise OSError("OS sandbox self-test did not prove required write and process restrictions")
        report.update(asked="os", available=True, selftest=checks)
        report["enforces"]["read"] = False
    report["pid"] = os.getpid()
    report["limitations"] = ["Reads are restricted by application path checks, not by the kernel"]
    if not report["enforces"]["network"]:
        report["limitations"].append("Network access is not denied by the kernel")
    return root, report


def _has(module):
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def _capabilities():
    from packing_assistant.documents.service import capabilities
    dependencies = {"frame": ("Pynite", "numpy"), "section": ("sectionproperties", "shapely"),
                    "ifc_check": ("ifcopenshell", "ifctester", "defusedxml"), "ifc_diff": ("ifcopenshell", "ifcdiff")}
    engineering = {name: {"available": all(_has(module) for module in modules),
                          "missing": [module for module in modules if not _has(module)],
                          "probe": "module_discovery_only"} for name, modules in dependencies.items()}
    return {"documents": capabilities(), "engineering": engineering,
            "retrieval": {"available": _has("packing_assistant.retrieval.worker"), "probe": "module_discovery_only"}}


def dispatch(module, request, root):
    """Internal dispatch for the confined process; has no process-spawn path."""
    if module not in MODULES or not isinstance(request, dict) or request.get("version") != 1:
        raise ValueError("Unknown service or protocol version")
    if not isinstance(request.get("call_id"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", request["call_id"]):
        raise ValueError("Invalid call identifier")
    if Path(request.get("workspace", "")).resolve() != root:
        raise ValueError("Request workspace differs from the host scope")
    request = dict(request)
    request["workspace"] = str(root)
    request["output_dir"] = str(_checked_path(root, ".civil-buddy/out", write=True))
    operation = request.get("operation")
    if module == "packing_assistant.host_worker":
        if operation != "capabilities":
            raise ValueError("Unknown host operation")
        return {"version": 1, "ok": True, "call_id": request["call_id"], "result": _capabilities(), "error": None}
    if module == "packing_assistant.documents.worker":
        if operation != "capabilities":
            source = _checked_path(root, request.get("source"))
            if not source.is_file():
                raise ValueError("Document source does not exist")
        from packing_assistant.documents.service import handle
        return handle(request)
    if module == "packing_assistant.retrieval.worker":
        sources = request.get("sources")
        if not isinstance(sources, list) or not 1 <= len(sources) <= 100:
            raise ValueError("Retrieval requires 1 to 100 selected sources")
        for source in sources:
            if not _checked_path(root, source).is_file():
                raise ValueError("Retrieval source does not exist")
        from packing_assistant.retrieval import handle
        return handle(request)
    if module == "packing_assistant.review_worker":
        if operation != "verdicts":
            raise ValueError("Unknown review operation")
        from packing_assistant.review_worker import handle
        return handle(request)
    if operation not in {"frame", "section", "ifc_check", "ifc_diff"}:
        raise ValueError("Unknown engineering operation")
    payload = request.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("Engineering payload must be an object")
    # Services accept finite structured values or uploaded bytes only; the old
    # engineering worker's argv/file protocol never reaches this bridge.
    if operation == "section" and set(payload) != {"document", "config"}:
        raise ValueError("Section accepts only a document and config object")
    if operation == "ifc_check" and set(payload) != {"ifc", "ids"}:
        raise ValueError("IFC checks accept uploaded IFC and IDS bytes only")
    if operation == "ifc_diff" and set(payload) != {"old", "new"}:
        raise ValueError("IFC comparison accepts two uploaded byte sources only")
    from packing_assistant.engineering.worker import dispatch as engineering_dispatch
    return {"version": 1, "ok": True, "call_id": request["call_id"],
            "result": engineering_dispatch(operation, payload), "error": None}


def main():
    protocol = sys.stdout.buffer
    sys.stdout = sys.stderr
    report = {"asked": os.environ.get("CIVIL_HOST_SANDBOX", "os"), "available": False,
              "backend": "unavailable", "enforces": {"write": False, "spawn": False, "network": False, "read": False}}
    call_id = None
    try:
        root, report = _bootstrap()  # before reading or parsing untrusted input
    except Exception:
        response = _error("sandbox_unavailable", "Worker confinement could not be established; no operation was executed")
    else:
        try:
            raw = sys.stdin.buffer.readline(MAX_INPUT + 2)
            if len(raw) > MAX_INPUT + 1 or not raw.endswith(b"\n"):
                raise ValueError("Request exceeds the worker input limit")
            request = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
            call_id = request.get("call_id") if isinstance(request, dict) else None
            response = dispatch(os.environ.get("CIVIL_HOST_MODULE"), request, root)
        except ImportError:
            response = _error("missing_dependency", "This worker dependency is not installed in the selected Python environment", call_id)
        except (ValueError, KeyError, TypeError):
            response = _error("invalid_request", "Unsupported or invalid bounded worker input", call_id)
        except Exception:
            response = _error("worker_failed", "Deterministic worker failed; source files were not authorized for replacement", call_id)
    response["sandbox"] = report
    encoded = (json.dumps(response, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    if len(encoded) > MAX_OUTPUT:
        response = _error("too_large", "Worker output exceeds 12 MiB", call_id)
        response["sandbox"] = report
        encoded = (json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8")
    protocol.write(encoded)
    protocol.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
