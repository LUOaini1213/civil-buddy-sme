"""Tender <-> packing link from a browser: upload the two files, get the statements back.

Until this module the link (packing_assistant/tender_packing_link.py, tool ``tender.packing_link``) ran only from
the CLI, the TUI or an agent turn, and only on files that already sat in the server's CIVIL_JOB_ROOT. A browser had
no way to put a tender and a panel list in front of it.

    POST /api/tender/link           multipart: tender (.md/.docx/.pdf) + panel_list (.xlsx/.csv) [+ session_id,
                                    container_type, project_name] -> statements, sha256s, what went stale
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
#: an Office file is a zip; its members are what openpyxl / the docx reader inflate
MAX_UNZIPPED = 100 * MB
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


def max_tender_text_bytes() -> int:
    """Tender TEXT one upload may carry (a .md file's bytes; a .docx / .pdf's text once read): more is refused, 413.
    A run is budgeted for a tender's logistics part, not for megabytes of text inside the tool's 60 s."""
    try:
        return int(float(os.getenv("CIVIL_LINK_MAX_TENDER_TEXT_KB") or 400) * 1024)
    except ValueError:
        return 400 * 1024


def _text_too_large(size: int) -> "Refusal":
    return Refusal(413, "too_large", f"the tender has {size // 1024} kB of text; the link reads at most "
                                     f"{max_tender_text_bytes() // 1024} kB per upload. Upload the part of the tender with "
                                     "the delivery, packing and transport clauses on its own")


def max_request_bytes() -> int:
    return max_tender_bytes() + max_panel_bytes() + 64 * 1024     # the rest of the form and the multipart framing


#: runs at once on this server; a third caller gets 429 instead of queueing behind a 60 s tool timeout
_RUNS = threading.BoundedSemaphore(int(os.getenv("CIVIL_LINK_CONCURRENCY") or 2))
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
        return JSONResponse({"ok": False, "error_code": self.code, "detail": self.detail}, status_code=self.status)


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


def _safe_name(filename: str, ext: str, fallback: str) -> str:
    """The client's file name, reduced to [A-Za-z0-9._-], with the extension we checked. Never a path."""
    stem = Path(str(filename or "").replace("\\", "/")).name
    stem = stem[: -len(ext)] if stem.lower().endswith(ext) else Path(stem).stem
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")[:80]
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


def _write_input(job: Path, name: str, data: bytes) -> Path:
    from packing_assistant.sandbox import guarded_write_bytes

    return guarded_write_bytes(job / name, data)


def _statement(s: Dict[str, Any]) -> Dict[str, Any]:
    from packing_assistant.tender_packing_link import KIND_TITLE, _figure_text

    return {"id": s["id"], "clause": s.get("clause"), "kind": s["kind"], "title": KIND_TITLE.get(s["kind"], s["kind"]),
            "status": s["status"], "owner": s["owner"], "figure": _figure_text(s.get("figures") or {}),
            "text": s["text"], "sha256": s["sha256"]}


def _run_job(session: str, job: Path, tender: Path, panel: Path, uploaded: Dict[str, str],
             container_type: str = "", project_name: str = "") -> Dict[str, Any]:
    """One linked run in ``job``: the tool through a ToolEngine, the deliverables through write_deliverable."""
    from packing_assistant.office_job import job_root_scope
    from packing_assistant.runtime.tool_engine import default_engine
    from packing_assistant.tender_packing_link import tender_text_limit

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
    with job_root_scope(job), tender_text_limit(max_tender_text_bytes()) as text_seen:
        result = engine.execute("tender.packing_link", args, expert_id="bid-parse", intent="run",
                                run_id=run_id)
    if text_seen.get("too_large"):
        raise _text_too_large(text_seen["too_large"])
    if not result.get("ok"):
        raise Refusal(422, str(result.get("error_code") or "link_failed"),
                      "the link did not run: " + str(result.get("detail") or result.get("reason") or "")[:300])
    data = result["data"]
    out = job / "out"
    files = []
    for item in data.get("deliverables") or []:
        wrote = engine.execute("write_deliverable", {"path": str(out / item["name"]), "text": item["text"]},
                               intent="run", run_id=run_id)
        if not wrote.get("ok"):
            raise Refusal(500, str(wrote.get("error_code") or "write_failed"),
                          f"{item['name']} was not written: {wrote.get('detail') or wrote.get('reason')}")
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
            session = str(form.get("session_id") or "web").strip()
            if not SESSION_RE.match(session) or session.startswith("demo-"):
                raise Refusal(400, "bad_session", "session_id: 1-64 of A-Z a-z 0-9 _ - (not starting with demo-)")
            container_type = str(form.get("container_type") or "").strip()
            if container_type and not CONTAINER_RE.match(container_type):
                raise Refusal(400, "bad_container_type", "container_type: a code such as 40HQ, or leave it empty")
            project_name = str(form.get("project_name") or "").strip()[:120]
            tender = await _read_upload(form, "tender", TENDER_TYPES, max_tender_bytes(), "tender")
            if tender[0] == ".md" and len(tender[2]) > max_tender_text_bytes():
                raise _text_too_large(len(tender[2]))       # before a slot or a job folder is taken
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
