"""What the desktop app does, with no window in sight.

Everything the native app can do goes through this class, and none of it imports tkinter: the
window (desktop/app.py) only draws what comes out of here. That is what lets the behaviour be
tested on a machine with no display, and it keeps the app a front-end of the same runtime the
CLI uses — threads, modes, the OS sandbox, approvals, plugins, review — not a second product.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from packing_assistant.runtime.civil_config import CONFIRM, CONFIRM_EN, message_confirmation  # noqa: E402,F401  (one definition)
Progress = Callable[[str], None]
Approve = Callable[[Dict[str, Any]], bool]


class DesktopError(RuntimeError):
    """Something the window should say to the user as is."""


class DesktopController:
    def __init__(self) -> None:
        self.job: Optional[Path] = None
        self.thread_id = ""
        self.confirmed = False
        self.last_out: Dict[str, Any] = {}

    # ------------------------------------------------------------------ the job folder
    def open_job(self, folder: str, *, write_instructions: bool = False) -> Dict[str, Any]:
        from packing_assistant.runtime.project_instructions import init
        from packing_assistant.runtime.threads import list_threads, new_thread
        from packing_assistant.runtime.workspace import activate

        path = Path(folder).expanduser()
        if not path.is_dir():
            raise DesktopError(f"不是文件夹：{folder}")
        try:
            self.job = activate(path)
        except (PermissionError, OSError) as exc:
            raise DesktopError(str(exc)) from exc
        os.chdir(self.job)
        if write_instructions:
            init(self.job)
        existing = list_threads()
        self.thread_id = existing[0].thread_id if existing else new_thread("主对话").thread_id
        self.confirmed, self.last_out = False, {}
        return self.status()

    def has_instructions(self) -> bool:
        return bool(self.job and (self.job / "CIVIL.md").is_file())

    def status(self) -> Dict[str, Any]:
        from packing_assistant.llm import llm_config
        from packing_assistant.runtime import os_sandbox, plugins
        from packing_assistant.runtime.civil_config import load_config
        from packing_assistant.runtime.project_instructions import load
        from packing_assistant.runtime.turn import resolve_mode

        cfg, llm = load_config(), llm_config()
        running, why = resolve_mode()
        sandbox = os_sandbox.describe()
        return {"job": str(self.job or ""), "instructions": self.has_instructions(), "slots": dict(load().slots) if self.job else {},
                "mode": {"asked": cfg.agent_mode, "running": running, "why": why},
                "backend": {"asked": sandbox["asked"], "running": sandbox["running"], "why": sandbox["why"], "kernel": sandbox["kernel"]},
                "sandbox": cfg.sandbox, "approval": cfg.approval, "model": {"name": llm["model"], "configured": bool(llm.get("api_key"))},
                "plugins": [{"name": p.name, "trusted": p.trusted, "skills": [s.id for s in p.skills]} for p in plugins.installed()],
                "thread": self.thread_id, "confirmed": self.confirmed}

    # ------------------------------------------------------------------ switches (process-wide, like the TUI's slash commands)
    @staticmethod
    def _choose(variable: str, value: str, allowed) -> str:
        from packing_assistant.runtime.civil_config import _strip_mode

        picked = _strip_mode(value, tuple(allowed), "")
        if not picked:
            raise DesktopError("可选：" + " | ".join(allowed))
        os.environ[variable] = picked
        return picked

    def set_mode(self, mode: str) -> str:
        from packing_assistant.runtime.civil_config import AGENT_MODES

        return self._choose("CIVIL_AGENT_MODE", mode, AGENT_MODES)

    def set_backend(self, backend: str) -> str:
        from packing_assistant.runtime.civil_config import SANDBOX_BACKENDS

        return self._choose("CIVIL_SANDBOX_BACKEND", backend, SANDBOX_BACKENDS)

    # ------------------------------------------------------------------ threads
    def _need_job(self) -> None:
        if self.job is None:
            raise DesktopError("先打开一个作业文件夹：会话、文书都放在它的 .civil-buddy/out 里。")

    def threads(self) -> List[Dict[str, Any]]:
        from packing_assistant.runtime.threads import list_threads

        if self.job is None:
            return []
        return [{"id": t.thread_id, "title": t.title or "新对话", "state": t.state, "current": t.thread_id == self.thread_id}
                for t in list_threads()[:50]]

    def new_thread(self, title: str = "新对话") -> str:
        from packing_assistant.runtime.threads import new_thread

        self._need_job()
        self.thread_id, self.last_out = new_thread(title).thread_id, {}
        self.confirmed = False
        return self.thread_id

    def switch_thread(self, thread_id: str) -> List[Dict[str, str]]:
        """Make it current and return what was said in it, for the transcript pane."""
        from packing_assistant.runtime.threads import load_rollout, load_thread

        found = load_thread(thread_id)
        if found is None:
            raise DesktopError("没有这个对话")
        self.thread_id, self.confirmed, self.last_out = thread_id, False, {}
        return load_rollout(thread_id, limit=200, chars=20000)

    # ------------------------------------------------------------------ one turn
    def submit(self, text: str, *, on_progress: Optional[Progress] = None, approve: Optional[Approve] = None) -> Dict[str, Any]:
        """Blocking — the window calls this from a worker thread. ``approve`` is asked at most once per turn."""
        from packing_assistant.civil import with_progress
        from packing_assistant.runtime.threads import run_on_thread

        self._need_job()
        task = (text or "").strip()
        if not task:
            raise DesktopError("先写要办的事")
        asked: List[bool] = []

        def ask(request: Dict[str, Any]) -> bool:
            asked.append(bool(approve(request)) if approve is not None else False)
            return asked[-1]

        def turn(confirmed: bool) -> Dict[str, Any]:
            return with_progress(lambda: run_on_thread(self.thread_id, task, confirm=confirmed, approve=ask), on_progress or (lambda _line: None))

        self.confirmed = False
        out = turn(message_confirmation(task))
        if out.get("hitl_pending") and not asked and ask({"name": out.get("expert_name") or "本次写盘", "risk": "high", "confirm_sentence": CONFIRM, "confirm_sentence_en": CONFIRM_EN}):
            out = turn(True)        # steps mode stops first; agreed on the spot, the same words run again
        self.last_out = out
        return out

    # ------------------------------------------------------------------ what came out
    def files(self) -> List[Dict[str, str]]:
        from packing_assistant.civil import _file_paths, display_path
        from packing_assistant.runtime.threads import load_thread

        paths = _file_paths(self.last_out) if self.last_out else []
        if not paths and self.thread_id:
            thread = load_thread(self.thread_id)
            paths = list(thread.artifacts) if thread else []
        return [{"name": Path(p).name, "path": p, "shown": display_path(p)} for p in paths]

    def review(self, name: str) -> Dict[str, Any]:
        from packing_assistant.runtime.review import review_file

        self._need_job()
        return review_file(name)

    def sandbox_report(self) -> str:
        from packing_assistant.civil import sandbox_text

        return sandbox_text(live=self.job is not None)[0]
