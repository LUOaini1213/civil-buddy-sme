"""Tender <-> packing link from a browser: upload the two files, get the statements back.

Until this module the link (packing_assistant/tender_packing_link.py, tool ``tender.packing_link``) ran only from
the CLI, the TUI or an agent turn, and only on files that already sat in the server's CIVIL_JOB_ROOT. A browser had
no way to put a tender and a panel list in front of it.

    POST /api/tender/link           multipart: tender (.md/.docx/.pdf) + panel_list (.xlsx/.csv) [+ session_id,
                                    container_type, project_name] -> statements, sha256s, what went stale
                                    (no session_id: a new session of its own, returned as session_id)
    POST /api/tender/link/demo      no input: the SYNTHETIC examples/facade-demo files, rev A then rev B
    GET  /api/tender/link/file/...  one of the four deliverables of a job
    GET  /demo                      the English page for both

Every route here sits behind the access guard (CIVIL_TOKEN); none is added to the public list. Each request gets
its own job folder under the data volume (``$PACKING_OUTPUT_DIR/web-link/<session>/<job>/``, already a sandbox
root), and the tool reads it through the same guard as every job-file read: office_job._resolve_job_file (inside the
job folder) and sandbox.assert_open (inside the sandbox roots, no secret names). The job folder is bound with
office_job.job_root_scope, a context variable: CIVIL_JOB_ROOT is never touched and no sandbox root is added.
The tool runs through a ToolEngine (contract, policy, timeout, audit) built for the request, so a bad upload cannot
trip the circuit breaker of the engine the agent turns share. Deliverables are written by the engine's
write_deliverable (sandbox write guard). Nothing here calls a model, and nothing here can approve anything: the
record stays submit_blocked, confirmed_by_person false.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
import shutil
import threading
import time
import zipfile
from contextvars import ContextVar
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

ROOT = Path(__file__).resolve().parents[1]
PAGES = Path(__file__).resolve().parent / "pages"
DEMO_DIR = ROOT / "examples" / "facade-demo"
DEMO_TENDER, DEMO_REV_A, DEMO_REV_B = "facade_itt_doc.md", "facade_panels.xlsx", "facade_panels_rev_b.xlsx"

#: extension -> what the bytes must look like. Nothing else is accepted.
TENDER_TYPES = {".md": "text", ".docx": "docx", ".pdf": "pdf"}
PANEL_TYPES = {".xlsx": "xlsx", ".csv": "text"}
MB = 1024 * 1024
#: an Office file is a zip; its members are what openpyxl / the docx reader inflate. The largest panel list in the
#: repository unpacks to 0.4 MB; 20 MB leaves room for a Word tender with pictures and keeps two parses on a 2 GB box.
MAX_UNZIPPED = 20 * MB
#: rows of a panel list (all sheets, or lines of a CSV), counted in the bytes before openpyxl sees them. The largest
#: list in the repository has 573 rows; openpyxl alone takes ~5 s on 5,000 rows and ~13 s on 10,000 on a laptop.
MAX_PANEL_ROWS = 5000
#: cells of a panel list: 5,000 rows of 20 columns. A few rows of 16,000 columns cost openpyxl as much as many rows.
MAX_PANEL_CELLS = 100_000
KEEP_JOBS_PER_SESSION = 10
KEEP_JOBS_TOTAL = 200
KEEP_DEMO_SESSIONS = 5
SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
JOB_RE = re.compile(r"^\d{8}T\d{12}Z-[0-9a-f]{6}$")
CONTAINER_RE = re.compile(r"^[0-9A-Za-z]{2,8}$")


def _limit_mb(name: str, default: float) -> int:
    try:
        return int(float(os.getenv(name) or default) * MB)
    except ValueError:
        return int(default * MB)


def max_tender_bytes() -> int:
    return _limit_mb("CIVIL_LINK_MAX_TENDER_MB", 10)


def max_panel_bytes() -> int:
    return _limit_mb("CIVIL_LINK_MAX_PANEL_MB", 5)


def max_panel_rows() -> int:
    try:
        return max(1, int(os.getenv("CIVIL_LINK_MAX_PANEL_ROWS") or MAX_PANEL_ROWS))
    except ValueError:
        return MAX_PANEL_ROWS


def max_request_bytes() -> int:
    return max_tender_bytes() + max_panel_bytes() + 64 * 1024     # the rest of the form and the multipart framing


#: runs at once on this server; a third caller gets 429 instead of queueing behind a 60 s tool timeout
_RUNS = threading.BoundedSemaphore(int(os.getenv("CIVIL_LINK_CONCURRENCY") or 2))
#: seconds a busy (429) caller is told to wait before retrying
RETRY_AFTER_S = "5"
_SESSION_LOCKS: Dict[str, threading.Lock] = {}
# Includes requests waiting for the same session lock: its prior record must
# survive until that request can compare against it. Registration, job creation
# and retention decisions share this lock, so pruning cannot race a new owner.
_SESSION_USERS: Dict[str, int] = {}
_DRAINING_SESSIONS: set[str] = set()
_LOCKS_GUARD = threading.RLock()
# Tools can return a timeout while their worker is still alive. A request keeps
# every engine/run pair until when_idle confirms that its workers have exited.
_RUN_WORKERS: ContextVar[Optional[List[Tuple[Any, str]]]] = ContextVar("web_link_workers", default=None)

router = APIRouter()


class Refusal(Exception):
    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status, self.code, self.detail = status, code, detail

    def response(self) -> JSONResponse:
        headers = {"Retry-After": RETRY_AFTER_S} if self.status == 429 else None  # busy: when to come back
        return JSONResponse({"ok": False, "error_code": self.code, "detail": self.detail}, status_code=self.status,
                            headers=headers)


def output_root() -> Path:
    """The data volume: $PACKING_OUTPUT_DIR (/app/output in the image), else <repo>/output. Both are sandbox roots."""
    raw = (os.getenv("PACKING_OUTPUT_DIR") or "").strip()
    base = Path(raw).expanduser() if raw else ROOT / "output"
    return (base if base.is_absolute() else ROOT / base).resolve()


def link_root() -> Path:
    return output_root() / "web-link"


def _session_lock(session: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _SESSION_LOCKS.setdefault(session, threading.Lock())


#: names Windows opens as a device whatever the extension (CON.md is the console, not a file)
_DEVICE_STEM = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\..*)?$", re.I)


def _safe_name(filename: str, ext: str, fallback: str) -> str:
    """The client's file name, reduced to [A-Za-z0-9._-], with the extension we checked. Never a path, never a
    Windows device name."""
    stem = Path(str(filename or "").replace("\\", "/")).name
    stem = stem[: -len(ext)] if stem.lower().endswith(ext) else Path(stem).stem
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")[:80]
    if _DEVICE_STEM.match(stem):
        stem += "_"
    return (stem or fallback) + ext


def _check_bytes(data: bytes, kind: str, label: str) -> None:
    """The bytes must be what the extension says. A renamed file is refused before any parser sees it."""
    if not data:
        raise Refusal(400, "empty_file", f"the {label} is empty")
    if kind == "pdf":
        if not data.startswith(b"%PDF-"):
            raise Refusal(415, "type_mismatch", f"the {label} is named .pdf but is not a PDF")
        return
    if kind == "text":
        if b"\x00" in data:
            raise Refusal(415, "type_mismatch", f"the {label} is not a text file (it contains NUL bytes)")
        try:
            data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise Refusal(415, "type_mismatch", f"the {label} is not UTF-8 text; save it as UTF-8 and try again") from None
        return
    member = {"docx": "word/document.xml", "xlsx": "xl/workbook.xml"}[kind]
    if not data.startswith(b"PK\x03\x04"):
        raise Refusal(415, "type_mismatch", f"the {label} is named .{kind} but is not an Office file")
    try:
        with zipfile.ZipFile(BytesIO(data)) as z:
            infos = z.infolist()
            names = {i.filename for i in infos}
            unzipped = sum(i.file_size for i in infos)
    except zipfile.BadZipFile:
        raise Refusal(415, "type_mismatch", f"the {label} is a damaged Office file") from None
    if member not in names:
        raise Refusal(415, "type_mismatch", f"the {label} is named .{kind} but has no {member}")
    if unzipped > MAX_UNZIPPED:
        raise Refusal(413, "too_large", f"the {label} unpacks to more than {MAX_UNZIPPED // MB} MB")


#: the sheet elements that decide what openpyxl builds: a row (its number), a cell (its column), the sheet's stated
#: size, and a merged range (openpyxl makes one object per merged cell). Any namespace prefix.
_XML_EVENT = re.compile(rb"<(?:[A-Za-z_][\w.-]*:)?(row|c|dimension|mergeCell)(?=[\s>/])([^>]*)>")
_XML_R = re.compile(rb"""(?:^|\s)r\s*=\s*["']\s*([A-Za-z]{0,3})\s*(\d+)""")
_XML_REF = re.compile(rb"""(?:^|\s)ref\s*=\s*["']([^"']*)["']""")
_XML_CORNER = re.compile(rb"\$?([A-Za-z]{0,3})\$?(\d*)")
#: rows x columns of the grid openpyxl reads, gaps included: it pads every row to the widest column and every gap
#: between two row numbers with empty rows. 5,000 rows of 200 columns; a far cell costs as much as a full sheet.
MAX_PANEL_GRID = 1_000_000


def _column(letters: bytes) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + ch - 64
    return n


def _check_rows(data: bytes, kind: str, label: str) -> None:
    """A panel list with more rows (or cells) than one linked run can use is refused before any parser opens it.
    A CSV's lines are counted in the bytes (CR, LF and CRLF all end a line for the csv reader). In a workbook every
    part is scanned (a stream, at most MAX_UNZIPPED), not only xl/worksheets/: the workbook's relationships may put a
    sheet anywhere. Counted: row and cell elements, the highest row number and column (a 2 KB sheet with one cell in
    XFD1048576 makes openpyxl walk a million padded rows), the stated dimension and the merged area."""
    rows_cap = max_panel_rows()
    too_many = Refusal(413, "too_many_rows", f"the {label} has more than {rows_cap:,} rows; split it and link each part")
    if kind == "text":
        lines = len(re.findall(rb"\r\n|\r|\n", data))
        if lines + (0 if data.endswith((b"\n", b"\r")) else 1) > rows_cap:
            raise too_many
        return
    cells_cap = max(MAX_PANEL_CELLS, rows_cap * 20)
    grid_cap = max(MAX_PANEL_GRID, cells_cap)

    def too(what: str) -> Refusal:
        return Refusal(413, "too_many_rows", f"the {label} {what}; delete what lies outside the table, or split it "
                                             "and link each part")

    rows = cells = merged = 0
    with zipfile.ZipFile(BytesIO(data)) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            row_no = top_row = top_col = in_row = 0
            tail = b""
            with z.open(info) as member:
                while True:
                    chunk = member.read(1 << 20)
                    window = tail + chunk
                    # a tag split across two reads: everything from the last '<' waits for the next read
                    cut = window.rfind(b"<") if chunk else len(window)
                    cut = len(window) if cut <= 0 else cut
                    for m in _XML_EVENT.finditer(window, 0, cut):
                        tag, attrs = m.group(1), m.group(2)
                        if tag == b"row":
                            rows += 1
                            numbers = [int(n) for _, n in _XML_R.findall(attrs)]
                            row_no = max(numbers) if numbers else row_no + 1
                            top_row, in_row = max(top_row, row_no), 0
                        elif tag == b"c":
                            cells += 1
                            in_row += 1
                            refs = _XML_R.findall(attrs)
                            top_col = max([top_col, in_row] + [_column(c) for c, _ in refs if c])
                            top_row = max([top_row] + [int(n) for _, n in refs])
                        else:
                            ref = _XML_REF.search(attrs)
                            corners = [_XML_CORNER.fullmatch(p.strip()) for p in (ref.group(1) if ref else b"").split(b":")]
                            spots = [(_column(c.group(1)), int(c.group(2) or 0)) for c in corners if c]
                            if tag == b"dimension":
                                top_col = max([top_col] + [c for c, _ in spots])
                                top_row = max([top_row] + [r for _, r in spots])
                            elif len(spots) == 2:
                                (c1, r1), (c2, r2) = spots
                                merged += (abs(c2 - c1) + 1) * (abs(r2 - r1) + 1)
                    tail = window[cut:]
                    if rows > rows_cap or top_row > rows_cap:
                        raise too_many
                    if cells > cells_cap:
                        raise too(f"has more than {cells_cap:,} cells")
                    if top_row * top_col > grid_cap:
                        raise too(f"reaches row {top_row:,} and column {top_col:,}, more than {grid_cap:,} cells "
                                  "to read")
                    if merged > cells_cap:
                        raise too(f"merges more than {cells_cap:,} cells")
                    if not chunk:
                        break


async def _read_upload(form: Any, field: str, types: Dict[str, str], limit: int, label: str) -> Tuple[str, str, bytes]:
    upload = form.get(field)
    if not isinstance(upload, UploadFile):
        raise Refusal(422, "missing_file", f"send the {label} as the multipart file field '{field}'")
    name = str(upload.filename or "")
    ext = Path(name.replace("\\", "/")).suffix.lower()
    if ext not in types:
        raise Refusal(415, "type_not_allowed", f"the {label} must be one of {', '.join(sorted(types))}, not '{ext or name}'")
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise Refusal(413, "too_large", f"the {label} is larger than {limit // MB} MB")
    _check_bytes(data, types[ext], label)
    if types is PANEL_TYPES:
        _check_rows(data, types[ext], label)
    return ext, name, data


def _new_job(session_dir: Path) -> Path:
    with _LOCKS_GUARD:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")     # to the microsecond: ids sort in run order
        job = session_dir / f"{stamp}-{secrets.token_hex(3)}"
        job.mkdir(parents=True, exist_ok=False)
        return job


def _jobs(session_dir: Path) -> List[Path]:
    """Job folders of one session, oldest first (the id starts with a UTC timestamp to the microsecond)."""
    if not session_dir.is_dir():
        return []
    return sorted(p for p in session_dir.iterdir() if p.is_dir() and JOB_RE.match(p.name))


def _previous_record(session_dir: Path, current: Path) -> Optional[Path]:
    """The link record of the latest earlier job in the same session: what the new statements are compared with."""
    from packing_assistant.tender_packing_link import LINK_FILE

    for job in reversed(_jobs(session_dir)):
        if job != current and (job / "out" / LINK_FILE).is_file():
            return job / "out" / LINK_FILE
    return None


def _prune(root: Path) -> None:
    """Bounded disk: 10 jobs per session, 200 jobs in all, the 5 latest demo sessions. Only folders this module
    named (session and job patterns) inside the web-link root are ever removed. Active and queued sessions are
    retained in full; the limits are reapplied when the last request leaves."""
    with _LOCKS_GUARD:
        _prune_idle(root)


def _prune_idle(root: Path) -> None:
    """Caller holds _LOCKS_GUARD through the checks and deletion, excluding registration and job creation."""
    if not root.is_dir():
        return
    protected = set(_SESSION_USERS)
    sessions = [p for p in root.iterdir() if p.is_dir() and SESSION_RE.match(p.name)]
    demos = sorted((p for p in sessions if p.name.startswith("demo-")), key=lambda p: p.stat().st_mtime)
    for old in demos[:-KEEP_DEMO_SESSIONS]:
        if old.name not in protected:
            shutil.rmtree(old, ignore_errors=True)
    everything: List[Path] = []
    for session in sessions:
        jobs = _jobs(session)
        if session.name in protected:
            everything += jobs
            continue
        for old in jobs[:-KEEP_JOBS_PER_SESSION]:
            shutil.rmtree(old, ignore_errors=True)
        everything += jobs[-KEEP_JOBS_PER_SESSION:]
    everything.sort(key=lambda p: p.name)
    for old in everything[:-KEEP_JOBS_TOTAL]:
        if old.parent.name not in protected:
            shutil.rmtree(old, ignore_errors=True)
    for session in sessions:
        # every upload without a session_id has a folder of its own: once its last job is pruned, the empty folder
        # goes too, or the web-link root grows by one folder per upload (demo sessions are kept by count above)
        if session.name not in protected and not session.name.startswith("demo-") and session.is_dir():
            try:
                session.rmdir()             # only an empty folder; one that still holds a job stays
            except OSError:
                pass


def _write_input(job: Path, name: str, data: bytes) -> Path:
    from packing_assistant.sandbox import guarded_write_bytes

    return guarded_write_bytes(job / name, data)


def _statement(s: Dict[str, Any]) -> Dict[str, Any]:
    from packing_assistant.tender_packing_link import KIND_TITLE, _figure_text

    return {"id": s["id"], "clause": s.get("clause"), "kind": s["kind"], "title": KIND_TITLE.get(s["kind"], s["kind"]),
            "status": s["status"], "owner": s["owner"], "figure": _figure_text(s.get("figures") or {}),
            "text": s["text"], "sha256": s["sha256"]}


#: what a browser is told when the tool fails, by error code. The engine's own reason is in Chinese and can carry a
#: server path, so it goes to the job's log (run-error.log, never served) and the server log, not into the reply.
_FAILURE_TEXT = {
    "timeout": "it took longer than {timeout:g} s and was stopped. A panel list of a few hundred rows can take that "
               "long; link a shorter list, or try again when the server is less busy",
    "invalid_args": "{what}. A file may be damaged, protected by a password, not what its extension says, or laid out "
                    "in a way the link does not read; open it on your computer, save it again and upload it again",
    "permission_denied": "the server refused to open one of the uploaded files",
    "circuit_open": "the link failed several times in a row and is paused; try again in a minute",
    "cancelled": "the run was cancelled",
}
#: the one part of a tool reason that is safe and useful to pass on: which uploaded file could not be read
_UNREADABLE = re.compile(r"^(the (?:tender|panel list) [A-Za-z0-9._-]{1,90} could not be read)")
_LOG = logging.getLogger("civil.web_link")


def _failure_text(code: str, result: Dict[str, Any], engine: Any) -> str:
    """Fixed English text for a failed tool call: nothing of the raw reason but the name of an unreadable file."""
    spec = getattr(engine, "tools", {}).get("tender.packing_link")
    text = _FAILURE_TEXT.get(code, "the link reported an error ({code}); the reason is in the job's log")
    which = _UNREADABLE.match(str(result.get("detail") or result.get("reason") or ""))
    return text.format(timeout=float(getattr(spec, "timeout_s", 60) or 60), code=re.sub(r"[^a-z_]", "", code)[:40],
                       what=which.group(1) if which else "the two files could not be used")


def _log_failure(job: Path, what: str, result: Dict[str, Any]) -> None:
    """The raw reason, for whoever runs the server: the server log and run-error.log in the job folder."""
    raw = str(result.get("detail") or result.get("reason") or "")[:2000]
    _LOG.warning("web link %s failed in job %s: %s %s", what, job.name, result.get("error_code"), raw)
    try:
        from packing_assistant.sandbox import guarded_write_bytes

        line = f"{datetime.now(timezone.utc).isoformat()} {what}: {result.get('error_code')} {raw}\n"
        log = job / "run-error.log"
        before = log.read_bytes() if log.is_file() else b""
        guarded_write_bytes(log, before + line.encode("utf-8"))
    except Exception:  # noqa: BLE001 - the log is a convenience; the refusal still goes out
        _LOG.exception("web link: run-error.log not written in job %s", job.name)


def _run_job(session: str, job: Path, tender: Path, panel: Path, uploaded: Dict[str, str],
             container_type: str = "", project_name: str = "") -> Dict[str, Any]:
    """One linked run in ``job``: the tool through a ToolEngine, the deliverables through write_deliverable."""
    from packing_assistant.office_job import job_root_scope
    from packing_assistant.runtime.tool_engine import default_engine

    t0 = time.perf_counter()
    previous = _previous_record(job.parent, job)
    engine = default_engine()
    run_id = f"web-link-{session}-{job.name}"
    workers = _RUN_WORKERS.get()
    if workers is not None:
        workers.append((engine, run_id))
    args: Dict[str, Any] = {"tender_path": str(tender), "packing_list": str(panel),
                            "previous_path": str(previous) if previous else ""}
    if container_type:
        args["container_type"] = container_type
    if project_name:
        args["project_name"] = project_name
    with job_root_scope(job):
        result = engine.execute("tender.packing_link", args, expert_id="bid-parse", intent="run",
                                run_id=run_id)
    if not result.get("ok"):
        code = str(result.get("error_code") or "link_failed")
        _log_failure(job, "tender.packing_link", result)
        raise Refusal(422, code, "the link did not run: " + _failure_text(code, result, engine))
    data = result["data"]
    out = job / "out"
    files = []
    for item in data.get("deliverables") or []:
        wrote = engine.execute("write_deliverable", {"path": str(out / item["name"]), "text": item["text"]},
                               intent="run", run_id=run_id)
        if not wrote.get("ok"):
            _log_failure(job, "write_deliverable " + item["name"], wrote)
            raise Refusal(500, str(wrote.get("error_code") or "write_failed"),
                          f"{item['name']} could not be written on the server; the reason is in the job's log")
        files.append({"name": item["name"], "url": f"/api/tender/link/file/{session}/{job.name}/{item['name']}"})
    record = data["record"]
    changes = record.get("changes_since_previous")
    plan = record.get("plan") or None
    inputs = record["inputs"]
    counts = {k: sum(1 for s in record["statements"] if s["status"] == k) for k in ("covered", "partial", "gap", "human_required")}
    return {
        "ok": True,
        "session_id": session,
        "job_id": job.name,
        "previous_job_id": previous.parent.parent.name if previous else None,
        "reply": data.get("reply"),
        "container": {k: record["container"].get(k) for k in ("type", "source", "clause", "reason")},
        "clauses": [{k: c.get(k) for k in ("clause", "kinds", "text")} for c in record["clauses"]],
        "statements": [_statement(s) for s in record["statements"]],
        "counts": counts,
        "plan": ({k: plan.get(k) for k in ("container_type", "containers_used", "can_fit", "n_boxes")} if plan else None),
        "plan_refusal": record.get("plan_refusal"),
        "inputs": {"tender": {**inputs["tender"], "uploaded_as": uploaded.get("tender")},
                   "panel_list": {**inputs["panel_list"], "uploaded_as": uploaded.get("panel_list")},
                   "plan": inputs["plan"]},
        "changes_since_previous": changes,
        "stale_statements": list((changes or {}).get("needs_reconfirmation") or []),
        "withdrawn": [w.get("label") or w.get("id") for w in (changes or {}).get("withdrawn") or []],
        "files": files,
        "confirmed_by_person": record.get("confirmed_by_person") is True,
        "submit_blocked": record.get("submit_blocked") is not False,
        "duration_ms": int((time.perf_counter() - t0) * 1000),
    }


def _locked_run(session: str, work) -> Any:
    """At most CIVIL_LINK_CONCURRENCY runs at once (429 beyond), one at a time per session (so 'previous' is
    well defined). A timeout retains these resources until its actual tool workers have stopped."""
    if not _RUNS.acquire(blocking=False):
        raise Refusal(429, "busy", "another linked run is in progress on this server; try again in a few seconds")
    try:
        with _LOCKS_GUARD:
            if session in _DRAINING_SESSIONS:
                raise Refusal(429, "busy", f"session {session} is waiting for its previous tool worker to stop")
            lock = _session_lock(session)
            _SESSION_USERS[session] = _SESSION_USERS.get(session, 0) + 1
    except BaseException:
        _RUNS.release()
        raise
    acquired = False
    workers: List[Tuple[Any, str]] = []
    token = _RUN_WORKERS.set(workers)
    try:
        if not lock.acquire(timeout=90):
            raise Refusal(429, "busy", f"session {session} is still running its previous upload")
        acquired = True
        return work()
    finally:
        _RUN_WORKERS.reset(token)
        with _LOCKS_GUARD:
            if acquired:
                _DRAINING_SESSIONS.add(session)

        def release() -> None:
            try:
                with _LOCKS_GUARD:
                    if acquired:
                        _DRAINING_SESSIONS.discard(session)
                        lock.release()
                    _SESSION_USERS[session] -= 1
                    if not _SESSION_USERS[session]:
                        del _SESSION_USERS[session]
                        # every upload without a session_id has a session of its own now: the lock map must not
                        # grow with them. Registration happens under this guard, so nobody else holds this lock.
                        _SESSION_LOCKS.pop(session, None)
                    _prune(link_root())
            finally:
                _RUNS.release()

        def after_worker(index: int) -> None:
            if index == len(workers):
                release()
            else:
                engine, run_id = workers[index]
                engine.when_idle(run_id, lambda: after_worker(index + 1))

        after_worker(0)


def _upload_job(session: str, tender: Tuple[str, str, bytes], panel: Tuple[str, str, bytes],
                container_type: str, project_name: str) -> Dict[str, Any]:
    t_ext, t_name, t_data = tender
    p_ext, p_name, p_data = panel
    tender_name = _safe_name(t_name, t_ext, "tender")
    panel_name = _safe_name(p_name, p_ext, "panel-list")
    if panel_name.lower() == tender_name.lower():
        panel_name = "panels-" + panel_name
    job = _new_job(link_root() / session)
    tender_path, panel_path = _write_input(job, tender_name, t_data), _write_input(job, panel_name, p_data)
    uploaded = {"tender": hashlib.sha256(t_data).hexdigest(), "panel_list": hashlib.sha256(p_data).hexdigest()}
    return _run_job(session, job, tender_path, panel_path, uploaded, container_type, project_name)


@router.post("/api/tender/link")
async def api_tender_link(request: Request):
    """Upload a tender and a panel list; run tender.packing_link on them in a job folder of their own."""
    try:
        length = request.headers.get("content-length")
        if length is None:
            raise Refusal(411, "length_required", "send the upload with a Content-Length header")
        if not length.isdigit() or int(length) > max_request_bytes():
            raise Refusal(413, "too_large", f"the upload is larger than {max_request_bytes() // MB} MB in all")
        if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
            raise Refusal(415, "multipart_required", "send multipart/form-data with the fields tender and panel_list")
        try:
            form = await request.form(max_files=2, max_fields=6)
        except Exception as e:  # noqa: BLE001 - Starlette's multipart errors: too many parts, bad framing
            raise Refusal(400, "bad_form", f"the form could not be read: {str(e)[:120]}") from None
        try:
            # no session_id: a session of its own, returned in the reply (a shared default would compare one
            # caller's upload with another caller's previous job)
            session = str(form.get("session_id") or "").strip() or "web-" + secrets.token_hex(8)
            if not SESSION_RE.match(session) or session.startswith("demo-"):
                raise Refusal(400, "bad_session", "session_id: 1-64 of A-Z a-z 0-9 _ - (not starting with demo-)")
            container_type = str(form.get("container_type") or "").strip()
            if container_type and not CONTAINER_RE.match(container_type):
                raise Refusal(400, "bad_container_type", "container_type: a code such as 40HQ, or leave it empty")
            project_name = str(form.get("project_name") or "").strip()[:120]
            tender = await _read_upload(form, "tender", TENDER_TYPES, max_tender_bytes(), "tender")
            panel = await _read_upload(form, "panel_list", PANEL_TYPES, max_panel_bytes(), "panel list")
        finally:
            await form.close()          # the spooled upload temp files; the bytes are in memory now
        return await run_in_threadpool(_locked_run, session,
                                       lambda: _upload_job(session, tender, panel, container_type, project_name))
    except Refusal as r:
        return r.response()


def _demo_job(session: str) -> Dict[str, Any]:
    t0 = time.perf_counter()
    source = {name: (DEMO_DIR / name).read_bytes() for name in (DEMO_TENDER, DEMO_REV_A, DEMO_REV_B)}
    runs = {}
    for rev, panel in (("rev_a", DEMO_REV_A), ("rev_b", DEMO_REV_B)):
        job = _new_job(link_root() / session)
        tender_path = _write_input(job, DEMO_TENDER, source[DEMO_TENDER])
        panel_path = _write_input(job, panel, source[panel])
        uploaded = {"tender": hashlib.sha256(source[DEMO_TENDER]).hexdigest(),
                    "panel_list": hashlib.sha256(source[panel]).hexdigest()}
        runs[rev] = _run_job(session, job, tender_path, panel_path, uploaded)
    return {"ok": True, "synthetic": True, "session_id": session, **runs,
            "stale_statements": runs["rev_b"]["stale_statements"],
            "duration_ms": int((time.perf_counter() - t0) * 1000)}


@router.post("/api/tender/link/demo")
async def api_tender_link_demo():
    """No input: copy the SYNTHETIC examples/facade-demo files into a fresh job, run rev A, then rev B against it."""
    session = "demo-" + secrets.token_hex(4)
    try:
        return await run_in_threadpool(_locked_run, session, lambda: _demo_job(session))
    except Refusal as r:
        return r.response()


@router.get("/api/tender/link/file/{session}/{job}/{name}")
def api_tender_link_file(session: str, job: str, name: str):
    from packing_assistant.tender_packing_link import BIDBOOK_FILE, LINK_FILE, PLAN_FILE, REPORT_FILE

    if not SESSION_RE.match(session) or not JOB_RE.match(job) or name not in (REPORT_FILE, BIDBOOK_FILE, LINK_FILE, PLAN_FILE):
        return JSONResponse({"ok": False, "error_code": "not_found", "detail": "no such deliverable"}, status_code=404)
    target = link_root() / session / job / "out" / name
    if not target.is_file():
        return JSONResponse({"ok": False, "error_code": "not_found", "detail": "no such deliverable"}, status_code=404)
    media = "application/json" if name.endswith(".json") else "text/markdown"
    return PlainTextResponse(target.read_text(encoding="utf-8"), media_type=f"{media}; charset=utf-8",
                             headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/demo")
def demo_page():
    """The English one-click page. Behind the token like every route but /, /workbench and /api/health."""
    return FileResponse(PAGES / "demo.html", media_type="text/html",
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
