from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile
from uuid import uuid4

from .common import DocumentError, bounded, digest, fail, validation
from .ooxml import Word, Spreadsheet

MAX_BYTES = 25_000_000
OPERATIONS = {"capabilities", "inspect", "read", "preview", "apply", "validate"}


def _process_alive(pid):
    if type(pid) is not int or pid < 1:
        return True  # Unknown locks are not proof that the owning process died.
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        process = kernel.OpenProcess(0x1000, False, pid)
        if not process:
            return ctypes.get_last_error() != 87
        try:
            status = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(process, ctypes.byref(status)) or status.value == 259
        finally:
            kernel.CloseHandle(process)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def capabilities():
    return {"docx": {"available": True, "operations": ["replace_paragraph", "replace_cell"],
                     "native_track_changes": False, "render": False},
            "xlsx": {"available": True, "operations": ["set_cell", "set_range"],
                     "recalculate": False, "render": False, "macro_edit": False},
            "pdf": {"available": importlib.util.find_spec("pypdf") is not None,
                    "operations": ["annotate", "fill_fields", "reorder_pages"],
                    "form_fields": "text_only", "ocr": False, "render": False, "body_edit": False}}


def _document(data, suffix):
    if suffix == ".docx":
        return Word(data)
    if suffix == ".xlsx":
        return Spreadsheet(data)
    if suffix == ".pdf":
        from .pdf import PDF
        return PDF(data)
    fail("unsupported", "Only DOCX, XLSX and PDF are supported")


def _patches(args):
    patches = args.get("patches")
    if not isinstance(patches, list) or not 1 <= len(patches) <= 200 or any(not isinstance(p, dict) for p in patches):
        fail("invalid_patch", "Provide 1 to 200 structured patches")
    if len(json.dumps(patches, ensure_ascii=False).encode("utf-8")) > 1_000_000:
        fail("too_large", "Patch payload exceeds 1 MB")
    for patch in patches:
        if "evidence" in patch and (not isinstance(patch["evidence"], list) or len(patch["evidence"]) > 50):
            fail("invalid_patch", "Evidence must be a bounded list; its claims remain unverified")
        if patch.get("trust", "model_proposed") not in ("model_proposed", "user_requested"):
            fail("invalid_patch", "A document patch cannot declare its content verified")
    return patches


def _execute(request):
    if not isinstance(request, dict) or request.get("version") != 1:
        fail("invalid_request", "Protocol version must be 1")
    try:
        encoded = json.dumps(request, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        fail("invalid_request", "Request must contain finite JSON values")
    if len(encoded.encode("utf-8")) > 1_100_000:
        fail("too_large", "Document request exceeds 1.1 MB")
    call_id = request.get("call_id")
    if not isinstance(call_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", call_id):
        fail("invalid_request", "call_id must be a bounded identifier")
    operation = request.get("operation")
    if operation not in OPERATIONS:
        fail("unsupported", "Operation is not in the document worker allowlist")
    if operation == "capabilities":
        return {"capabilities": capabilities()}
    workspace_value = request.get("workspace")
    if not isinstance(workspace_value, str) or not Path(workspace_value).is_absolute():
        fail("invalid_request", "workspace must be an absolute existing directory")
    workspace = Path(workspace_value).resolve()
    if not workspace.is_dir():
        fail("invalid_request", "Workspace does not exist")
    source = bounded(workspace, request.get("source"))
    if not source.is_file():
        fail("not_found", "Source document does not exist")
    if source.stat().st_size > MAX_BYTES:
        fail("too_large", "Source document exceeds 25 MB")
    data = source.read_bytes()
    if len(data) > MAX_BYTES:
        fail("too_large", "Source document exceeds 25 MB")
    sha = digest(data)
    expected = request.get("expected_sha256")
    if operation in ("preview", "apply") and not expected:
        fail("invalid_request", "preview/apply require expected_sha256")
    if expected is not None and expected != sha:
        fail("conflict", "Source SHA-256 differs from expected_sha256; inspect the current version")
    document = _document(data, source.suffix.lower())
    args = request.get("arguments", {})
    if not isinstance(args, dict):
        fail("invalid_request", "arguments must be an object")
    base = {"source": str(source), "source_sha256": sha, "format": source.suffix[1:].lower(),
            "capabilities": capabilities()[source.suffix[1:].lower()]}
    if operation in ("inspect", "validate"):
        detail = document.inspect()
        if operation == "validate":
            detail["validation"] = {**detail.get("validation", validation()), "source_hash": "pass", "content_changes": "not_checked"}
        return {**base, **detail}
    if operation == "read":
        return {**base, **document.read(args)}
    patches = _patches(args)
    output, changes, report = document.patch(patches)
    if len(output) > 50_000_000:
        fail("too_large", "Result exceeds 50 MB")
    reopened = _document(output, source.suffix.lower())
    reopened.inspect()
    result = {**base, "changes": changes, "validation": report, "trust": "model_proposed",
              "evidence_validation": "not_checked", "original_preserved": True}
    if operation == "preview":
        return {**result, "preview_kind": "structured_diff", "visual_preview": "not_available", "writes": False}
    folder = bounded(workspace, request.get("output_dir") or ".civil-buddy/out/documents")
    folder.mkdir(parents=True, exist_ok=True)
    folder = bounded(workspace, str(folder))
    manifest = folder / ("call-" + call_id + ".json")
    lock = folder / ("call-" + call_id + ".lock")
    request_hash = digest(json.dumps(request, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if lock.exists():
        try:
            owner = json.loads(lock.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            owner = {}
        if not _process_alive(owner.get("pid")):
            lock.unlink(missing_ok=True)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        fail("busy", "This document call is already running")
    os.write(descriptor, json.dumps({"pid": os.getpid()}).encode("ascii"))
    os.close(descriptor)
    published = None
    temporary = None
    try:
        if manifest.exists():
            previous = json.loads(manifest.read_text(encoding="utf-8"))
            if previous.get("request_sha256") != request_hash:
                fail("conflict", "call_id was already used for a different request")
            old_result = previous["result"]
            artifact = bounded(workspace, old_result["output_path"])
            if not artifact.is_file() or digest(artifact.read_bytes()) != old_result["output_sha256"]:
                fail("conflict", "Previously produced document is missing or changed")
            return {**old_result, "replayed": True}
        if digest(source.read_bytes()) != sha:
            fail("conflict", "Source changed while preparing the patch")
        # Unique new files only: no source or previous draft can be replaced.
        target = folder / (source.stem[:80] + "-draft-" + uuid4().hex[:12] + source.suffix.lower())
        bounded(workspace, str(target))
        descriptor, temporary = tempfile.mkstemp(prefix=".document-", suffix=".tmp", dir=folder)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(output)
            stream.flush()
            os.fsync(stream.fileno())
        if digest(source.read_bytes()) != sha:
            fail("conflict", "Source changed before the new document was registered")
        actual = Path(temporary).read_bytes()
        _document(actual, target.suffix).inspect()
        if digest(actual) != digest(output):
            fail("validation_failed", "Saved document does not match the prepared version")
        # UUID destination is checked; never use replace() for a user document.
        if target.exists():
            fail("conflict", "New draft destination already exists")
        os.rename(temporary, target)
        published = target
        temporary = None
        result.update(output_path=str(target), output_sha256=digest(actual),
                      validation={**report, "reopened": "pass", "source_unchanged": "pass"})
        descriptor, temporary = tempfile.mkstemp(prefix=".call-", suffix=".tmp", dir=folder)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"version": 1, "request_sha256": request_hash, "result": result}, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, manifest)
        temporary = None
        return result
    except BaseException:
        if published is not None:
            published.unlink(missing_ok=True)
        raise
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
        lock.unlink(missing_ok=True)


def handle(request):
    """A bounded, side-effect-explicit request. Authorization belongs to the host."""
    call_id = request.get("call_id") if isinstance(request, dict) else None
    try:
        with redirect_stdout(StringIO()):
            result = _execute(request)
        return {"version": 1, "ok": True, "call_id": call_id, "result": result, "error": None}
    except DocumentError as exc:
        return {"version": 1, "ok": False, "call_id": call_id, "result": None,
                "error": {"code": exc.code, "message": str(exc)}}
    except Exception:
        # Parser exceptions may contain private file content. Do not echo them.
        return {"version": 1, "ok": False, "call_id": call_id, "result": None,
                "error": {"code": "invalid_document", "message": "Document operation failed; verify the file structure and supported operation"}}
