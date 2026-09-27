"""ToolEngine: register → list → allow → validate → execute → audit.

Chat turns cannot write. Exclusive tools stay on their expert.
Does not re-pack; pack-ship handlers only project solver snapshots.
"""

from __future__ import annotations

import threading
import time
from contextvars import copy_context
from dataclasses import dataclass, field
from copy import deepcopy
from typing import Any, Callable, Dict, List, Optional

ERR_OK = "ok"
ERR_DENIED = "permission_denied"
ERR_INVALID = "invalid_args"
ERR_TIMEOUT = "timeout"
ERR_CIRCUIT = "circuit_open"
ERR_UNSPECIFIED = "unspecified"
ERR_MAX_STEPS = "max_steps"
ERR_DEADLOCK = "deadlock"
ERR_BUSY = "expert_busy"
ERR_CANCELLED = "cancelled"

WRITE_TOOLS = frozenset(
    {
        "write_deliverable",
        "spawn_helper",
        "tender.parse",
        "pack-ship__plan",
        "pack-ship__export",
    }
)

_PATH_KEYS = ("path", "write_path", "output_path", "dest", "file")
# A completed read can report an expected missing business object. Keep the
# error visible, but do not latch the shared tool off before that object exists.
# Exact tool/code pairs only: unreadable records, exceptions and timeouts fail.
_EXPECTED_ABSENCE = frozenset({("read_link_record", "no_link_record")})


def _write_path(args: Dict[str, Any]) -> Optional[str]:
    for key in _PATH_KEYS:
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _spawn_cmd(args: Dict[str, Any]) -> tuple[Any, Optional[str]]:
    if "command" in args and args.get("command") is not None:
        return args.get("command"), args.get("kind")
    if "spawn" in args and args.get("spawn") is not None:
        return args.get("spawn"), args.get("kind")
    if "argv" in args and args.get("argv") is not None:
        return args.get("argv"), args.get("kind")
    return None, None


@dataclass
class ToolSpec:
    name: str
    handler: Callable[[Dict[str, Any]], Any]
    schema_keys: tuple[str, ...] = ()
    expert_id: Optional[str] = None
    writes: bool = False
    timeout_s: float = 30.0
    input_schema: Optional[Dict[str, Any]] = None
    output_schema: Optional[Dict[str, Any]] = None


@dataclass
class Audit:
    name: str
    error_code: str
    duration_ms: int
    expert_id: str = ""


@dataclass
class ToolEngine:
    tools: Dict[str, ToolSpec] = field(default_factory=dict)
    _fail_streak: Dict[str, int] = field(default_factory=dict)
    circuit_threshold: int = 3
    audit_log: List[Audit] = field(default_factory=list)
    ledger: Any = None
    _workers: Dict[str, set] = field(default_factory=dict, repr=False)
    _worker_lock: Any = field(default_factory=threading.Lock, repr=False)

    def when_idle(self, run_id: str, callback: Callable[[], None]) -> None:
        """Release run resources only after timed-out workers really finish."""
        with self._worker_lock:
            pending = tuple(self._workers.get(run_id, ()))
        if not pending:
            callback()
            return

        def finish() -> None:
            for done in pending:
                done.wait()
            callback()

        threading.Thread(target=finish, name="civil-tool-release", daemon=True).start()

    def register(
        self,
        name: str,
        handler: Callable[[Dict[str, Any]], Any],
        *,
        schema_keys: tuple[str, ...] = (),
        expert_id: Optional[str] = None,
        writes: bool = False,
        timeout_s: float = 30.0,
        input_schema: Optional[Dict[str, Any]] = None,
        output_schema: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.tools[name] = ToolSpec(
            name=name,
            handler=handler,
            schema_keys=schema_keys,
            expert_id=expert_id,
            writes=writes,
            timeout_s=timeout_s,
            input_schema=deepcopy(input_schema),
            output_schema=deepcopy(output_schema),
        )

    def list(self, *, expert_id: Optional[str] = None) -> List[str]:
        names = []
        for spec in self.tools.values():
            if spec.expert_id and expert_id and spec.expert_id != expert_id:
                continue
            names.append(spec.name)
        return names

    def schemas_for(self, expert_id: Optional[str] = None) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for spec in self.tools.values():
            if spec.expert_id and expert_id and spec.expert_id != expert_id:
                continue
            rows.append(
                {
                    "name": spec.name,
                    "writes": spec.writes,
                    "expert_id": spec.expert_id,
                    "schema_keys": list(spec.schema_keys),
                    "timeout_s": spec.timeout_s,
                    "schema_version": "civil.tool.v1",
                    "input_schema": deepcopy(spec.input_schema),
                    "output_schema": deepcopy(spec.output_schema),
                }
            )
        return rows

    def allow(
        self,
        name: str,
        *,
        expert_id: str = "",
        intent: str = "run",
        cancelled: bool = False,
    ) -> Optional[str]:
        if cancelled:
            return ERR_DENIED
        spec = self.tools.get(name)
        if spec is None:
            return ERR_INVALID
        if intent == "chat" and spec.writes:
            return ERR_DENIED
        if spec.expert_id and expert_id and spec.expert_id != expert_id:
            return ERR_DENIED
        if self._fail_streak.get(name, 0) >= self.circuit_threshold:
            return ERR_CIRCUIT
        return None

    def admit(
        self,
        name: str,
        arguments: Any = None,
        *,
        expert_id: str = "",
        intent: str = "run",
        cancelled: bool = False,
        circuit: bool = True,
    ) -> tuple[Any, Optional[Dict[str, Any]]]:
        """Contract, then policy: (decision, None) or (None, refusal). Also for callers that run the handler themselves;
        those never feed the fail streak, so they pass circuit=False rather than inherit a latch they cannot reset."""
        args = {} if arguments is None else arguments
        spec = self.tools.get(name)
        from packing_assistant.runtime.tool_contracts import validate
        problem = ("arguments: expected object" if not isinstance(args, dict) else
                   validate(args, spec.input_schema) if spec and spec.input_schema else None)
        if problem:
            self.audit_log.append(Audit(name, ERR_INVALID, 0, expert_id))
            return None, {"ok": False, "error_code": ERR_INVALID, "name": name,
                          "reason": "工具参数不符合契约：" + problem}
        from packing_assistant.runtime.policy import evaluate as policy_evaluate

        pol = policy_evaluate(
            tool=name,
            spec=spec,
            expert_id=expert_id,
            intent=intent,
            args=args,
            cancelled=cancelled,
            ledger=self.ledger,
            fail_streak=self._fail_streak.get(name, 0) if circuit else 0,
            circuit_threshold=self.circuit_threshold,
        )
        if not pol.allow:
            rec = Audit(name=name, error_code=pol.err, duration_ms=0, expert_id=expert_id)
            self.audit_log.append(rec)
            out = {
                "ok": False,
                "error_code": pol.err,
                "name": name,
                "reason": pol.reason,
                "policy": pol.to_dict(),
            }
            if pol.sandbox:
                out["sandbox"] = pol.sandbox
                out["detail"] = pol.reason
            return None, out
        return pol, None

    def execute(
        self,
        name: str,
        arguments: Optional[Dict[str, Any]] = None,
        *,
        expert_id: str = "",
        intent: str = "run",
        cancelled: bool = False,
        run_id: str = "",
        wait_resources: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        t0 = time.perf_counter()
        args = {} if arguments is None else arguments
        pol, refused = self.admit(name, args, expert_id=expert_id, intent=intent, cancelled=cancelled)
        if refused is not None:
            return refused
        spec = self.tools[name]
        from packing_assistant.runtime.tool_contracts import validate
        for key in spec.schema_keys:
            if key not in args:
                rec = Audit(name=name, error_code=ERR_INVALID, duration_ms=0, expert_id=expert_id)
                self.audit_log.append(rec)
                return {
                    "ok": False,
                    "error_code": ERR_INVALID,
                    "name": name,
                    "missing": key,
                    "reason": f"拒绝：工具 {name} 缺少参数 {key}。",
                }
        if run_id and wait_resources:
            from packing_assistant.runtime.deadlock import get_watch

            # Authorization precedes acquisition, and a contended batch rolls
            # back only its new resources while preserving the run's old holds.
            d = get_watch().begin(run_id, holds=wait_resources)
            if not d.allow:
                self.audit_log.append(Audit(name, d.err, 0, expert_id))
                return {"ok": False, "error_code": d.err, "name": name,
                        "reason": d.reason, "cycle": list(d.cycle), "deadlock": d.to_dict()}
        sandbox_info: Optional[Dict[str, Any]] = pol.sandbox
        if self.ledger is not None:
            self.ledger.charge(steps=1, tokens=pol.token_cost)
        box: Dict[str, Any] = {}
        from packing_assistant.runtime import cancel as cancellation

        timed_out = threading.Event()
        worker_done = threading.Event()

        def _run() -> None:
            try:
                with cancellation.scope(*cancellation.current_keys(), run_id, event=timed_out):
                    cancellation.check()
                    box["data"] = spec.handler(args)
                    box["err"] = None
            except Exception as e:  # noqa: BLE001 — surface as timeout/invalid, not invent numbers
                box["err"] = e
            finally:
                with self._worker_lock:
                    if run_id in self._workers:
                        self._workers[run_id].discard(worker_done)
                        if not self._workers[run_id]:
                            self._workers.pop(run_id, None)
                    worker_done.set()

        ctx = copy_context()
        th = threading.Thread(target=ctx.run, args=(_run,), daemon=True)
        if run_id:
            with self._worker_lock:
                self._workers.setdefault(run_id, set()).add(worker_done)
        try:
            th.start()
        except Exception:
            with self._worker_lock:
                self._workers.get(run_id, set()).discard(worker_done)
                if not self._workers.get(run_id):
                    self._workers.pop(run_id, None)
                worker_done.set()
            raise
        th.join(spec.timeout_s)
        ms = int((time.perf_counter() - t0) * 1000)
        if th.is_alive():
            timed_out.set()  # stop this worker at its next cooperative checkpoint
            self._fail_streak[name] = self._fail_streak.get(name, 0) + 1
            self.audit_log.append(Audit(name=name, error_code=ERR_TIMEOUT, duration_ms=ms, expert_id=expert_id))
            return {
                "ok": False,
                "error_code": ERR_TIMEOUT,
                "name": name,
                "duration_ms": ms,
                "worker_running": not worker_done.is_set(),
                "reason": f"失败：工具 {name} 下游超时（{spec.timeout_s}s），已请求协作停止；工作线程退出前仍保留资源占用。",
            }
        if box.get("err") is not None:
            err = box["err"]
            if isinstance(err, cancellation.RunCancelled):
                self.audit_log.append(Audit(name, ERR_CANCELLED, ms, expert_id))
                return {"ok": False, "cancelled": True, "error_code": ERR_CANCELLED,
                        "name": name, "duration_ms": ms,
                        "reason": "本轮已取消，工具在协作检查点停止。"}
            if isinstance(err, PermissionError):
                self.audit_log.append(Audit(name=name, error_code=ERR_DENIED, duration_ms=ms, expert_id=expert_id))
                denied: Dict[str, Any] = {
                    "ok": False,
                    "error_code": ERR_DENIED,
                    "name": name,
                    "detail": str(err)[:200],
                    "duration_ms": ms,
                }
                if sandbox_info:
                    denied["sandbox"] = sandbox_info
                return denied
            self._fail_streak[name] = self._fail_streak.get(name, 0) + 1
            self.audit_log.append(Audit(name=name, error_code=ERR_INVALID, duration_ms=ms, expert_id=expert_id))
            return {
                "ok": False,
                "error_code": ERR_INVALID,
                "name": name,
                "detail": str(err)[:200],
                "reason": f"失败：工具 {name} 报错 {str(err)[:120]}",
            }
        data = box.get("data")
        failed = isinstance(data, dict) and data.get("ok") is False
        if not failed and spec.output_schema:
            problem = validate(data, spec.output_schema, "result")
            if problem:
                self.audit_log.append(Audit(name, ERR_INVALID, ms, expert_id))
                return {"ok": False, "error_code": ERR_INVALID, "name": name,
                        "reason": "工具返回值不符合契约：" + problem,
                        "duration_ms": ms, "contract_error": True}
        error_code = str(data.get("error_code") or ERR_UNSPECIFIED) if failed else ERR_OK
        fault = failed and (name, error_code) not in _EXPECTED_ABSENCE
        self._fail_streak[name] = self._fail_streak.get(name, 0) + 1 if fault else 0
        self.audit_log.append(Audit(name=name, error_code=error_code, duration_ms=ms, expert_id=expert_id))
        out: Dict[str, Any] = {
            "ok": not failed,
            "error_code": error_code,
            "name": name,
            "data": data,
            "duration_ms": ms,
            "reason": f"失败：工具 {name} 未完成（{error_code}）。" if failed else pol.reason,
            "policy": pol.to_dict(),
        }
        if sandbox_info:
            out["sandbox"] = sandbox_info
        if isinstance(data, dict):
            out.update({k: data[k] for k in data if k not in out})
        return out


_ENGINE: Optional[ToolEngine] = None


def _pack_handler(name: str):
    def _h(args: Dict[str, Any]) -> Any:
        from packing_assistant.tools.pack_ship_mcp import call_tool

        return call_tool(name, args)

    return _h


def _write_deliverable(args: Dict[str, Any]) -> Any:
    from packing_assistant.sandbox import guarded_write_text

    path = str(args.get("path") or "")
    text = str(args.get("text") or "")
    target = guarded_write_text(path, text)
    return {"path": str(target), "wrote": True, "n_chars": len(text)}


def _spawn_helper(args: Dict[str, Any]) -> Any:
    from packing_assistant.sandbox import request_spawn

    decision = request_spawn(args.get("command"), kind=args.get("kind"))
    if not decision.allowed:
        raise PermissionError(decision.reason)
    return {
        "allowed": True,
        "reason": decision.reason,
        "spawned": False,
        "note": "sandbox allowlisted; agent does not exec a shell",
    }


def _tender_parse(args: Dict[str, Any]) -> Any:
    from packing_assistant.tools.tender_parse import run_tender_pipeline

    packing = args.get("packing_summary")
    ingest = args.get("ingest")
    return run_tender_pipeline(
        str(args.get("text") or ""),
        source=str(args.get("source") or "tool-engine"),
        project_name=str(args.get("project_name") or "幕墙项目投标应答（草稿）"),
        p0_confirmed=args.get("p0_confirmed") is True,
        packing_summary=packing if isinstance(packing, dict) else None,
        ingest=ingest if isinstance(ingest, dict) else None,
    )


def _tender_packing_link(args: Dict[str, Any]) -> Any:
    """ITT + panel list in one run: logistics clauses, a plan under the clause's container type, statements tied to
    clause and plan figure, the link record (and what changed since the previous one). Reads only; the agent loop
    writes the deliverables through write_deliverable."""
    from pathlib import Path

    from packing_assistant.tender_packing_link import earlier_exports, load_previous, run_link

    previous = str(args.get("previous_path") or "")
    return run_link(str(args.get("tender_path") or ""), str(args.get("packing_list") or ""),
                    previous=load_previous(previous), project_name=str(args.get("project_name") or ""),
                    exports=earlier_exports(str(Path(previous).parent)) if previous else (),
                    container_type=str(args.get("container_type") or "") or None)


def _read_link_record(args: Dict[str, Any]) -> Any:
    """The latest tender <-> packing link record as labelled facts (tender_packing_link.link_record_view): this
    session's own record, else the newest one in the job folder's output. Reads only, and takes no path, so a model
    cannot point it at another file; a question about the clauses or the plan is answered from what it returns."""
    import json

    from packing_assistant.runtime import agent_loop
    from packing_assistant.runtime.workspace_ctx import current_worktree
    from packing_assistant.tender_packing_link import latest_link_record, link_record_view

    root = agent_loop._out_root()
    sid = str(args.get("session_id") or "")
    path = latest_link_record([root] if current_worktree() else [], root / agent_loop._safe_sid(sid) if sid else None)
    if path is None:
        return {"ok": False, "error_code": "no_link_record",
                "reason": "No tender-packing link record in this job folder yet: run the link first (name one tender file and "
                          "one panel list and ask for the logistics response)."}
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "error_code": "unreadable", "reason": f"{path.name} could not be read."}
    if not isinstance(record, dict) or record.get("schema") != "tender.packing_link.v1":
        return {"ok": False, "error_code": "unreadable", "reason": f"{path.name} is not a link record."}
    try:
        where = path.relative_to(root.parent.parent).as_posix()
    except ValueError:
        where = path.name
    return link_record_view(record, where=where)


def _tender_review(args: Dict[str, Any]) -> Any:
    from packing_assistant.tools.tender_review import review_draft

    return review_draft(
        draft=str(args.get("draft") or args.get("text") or ""),
        matrix=args.get("matrix") if isinstance(args.get("matrix"), dict) else None,
        packing_summary=args.get("packing_summary")
        if isinstance(args.get("packing_summary"), dict)
        else None,
        tech_outline=args.get("tech_outline") if isinstance(args.get("tech_outline"), dict) else None,
        bidbook_markdown=str(args.get("bidbook_markdown") or ""),
    )


def _exclusive_handler(name: str):
    def _h(args: Dict[str, Any]) -> Any:
        from packing_assistant.expert_turn import run_named_exclusive

        return run_named_exclusive(name, args)

    return _h


def _register_exclusives(eng: ToolEngine) -> None:
    from packing_assistant.expert_roster import list_experts

    skip = set(eng.tools)
    for exp in list_experts():
        if exp.category == "plugin":
            continue        # a plugin's post has no handler here: it is drafted from its own template (agent_loop._draft_md)
        for name in exp.exclusive:
            if name in skip:
                continue
            # These handlers create expert drafts. A "list" can be a written
            # checklist (admin-office, lab-sample, env), not a read operation.
            # Actual read-only packing handlers are registered explicitly above.
            writes = True
            timeout = 60.0 if "fill_scheme" in name or name.endswith("scheme_draft") else 30.0
            eng.register(
                name,
                _exclusive_handler(name),
                expert_id=exp.id,
                writes=writes,
                timeout_s=timeout,
            )
            skip.add(name)


def default_engine() -> ToolEngine:
    eng = ToolEngine()
    eng.register("pack-ship__list", _pack_handler("pack-ship__list"), expert_id="pack-ship", writes=False)
    eng.register("pack-ship__health", _pack_handler("pack-ship__health"), expert_id="pack-ship", writes=False)
    eng.register("pack-ship__plan", _pack_handler("pack-ship__plan"), expert_id="pack-ship", writes=True)
    eng.register("pack-ship__export", _pack_handler("pack-ship__export"), expert_id="pack-ship", writes=True)
    for name in ("pack-ship__ingest", "pack-ship__vgm", "pack-ship__booking_draft"):
        eng.register(name, _pack_handler(name), expert_id="pack-ship", writes=False)
    eng.register("tender.parse", _tender_parse, writes=True)
    eng.register("tender.review", _tender_review, writes=False)
    eng.register("tender.packing_link", _tender_packing_link, expert_id="bid-parse", writes=False, timeout_s=60.0)
    eng.register("read_link_record", _read_link_record, expert_id="bid-parse", writes=False, timeout_s=15.0)
    eng.register(
        "write_deliverable",
        _write_deliverable,
        schema_keys=("path",),
        writes=True,
    )
    eng.register("spawn_helper", _spawn_helper, writes=True)
    _register_exclusives(eng)
    from packing_assistant.tools.readonly_sources import register_tools
    register_tools(eng)
    from packing_assistant.runtime.tool_contracts import contract_for
    for spec in eng.tools.values():
        contract = contract_for(spec.name, exclusive=bool(spec.expert_id))
        spec.input_schema = contract["input_schema"]
        spec.output_schema = contract["output_schema"]
    return eng


def get_engine() -> ToolEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = default_engine()
    return _ENGINE
