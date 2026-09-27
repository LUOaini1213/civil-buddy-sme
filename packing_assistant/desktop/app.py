"""Civil Buddy as a native desktop app:  python -m packing_assistant.desktop [作业文件夹]   ·   civil desktop

A real window with native widgets (Tk — in every standard Python, so nothing to install), not a
browser tab and not a terminal. It is a front-end of the same runtime as `civil`: open a job
folder, say what you need, watch the steps as they happen, answer the approval prompt in a
dialog, open what was written, review a document. All behaviour lives in desktop/controller.py;
this file only draws.

A turn blocks, so it runs on a worker thread; everything that touches a widget happens on the Tk
thread, fed through a queue. The approval question crosses that boundary the other way: the
worker waits on an event while the dialog is open.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from packing_assistant.desktop.controller import CONFIRM, CONFIRM_EN, DesktopController, DesktopError
from packing_assistant.runtime.civil_config import is_confirmation

TITLE = "Civil Buddy · 土木版 Codex"
_FONT = ("Microsoft YaHei UI", 10) if sys.platform == "win32" else ("PingFang SC", 12) if sys.platform == "darwin" else ("Noto Sans CJK SC", 10)


def open_with_system(path: str) -> None:
    """The user double-clicked a deliverable: hand it to whatever the OS opens it with."""
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - the user's own file, opened on their double-click
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])  # noqa: S603


class CivilDesktop:
    def __init__(self, controller: Optional[DesktopController] = None, *, folder: str = "") -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk, self.ttk = tk, ttk
        self.controller = controller or DesktopController()
        self.events: "queue.Queue[tuple]" = queue.Queue()
        self.busy = False
        self.root = tk.Tk()
        self.root.title(TITLE)
        self.root.geometry("1180x760")
        self.root.minsize(900, 560)
        self._build()
        self.root.after(60, self._drain)
        if folder:
            self._open(folder)
        else:
            self._say("system", "先点「打开作业文件夹」：Civil Buddy 在你的工地文件夹里干活，会话和文书都放在它的 .civil-buddy/out 里。")
        self._refresh()

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        tk, ttk = self.tk, self.ttk
        top = ttk.Frame(self.root, padding=(10, 8))
        top.pack(fill="x")
        ttk.Button(top, text="打开作业文件夹…", command=self._choose_folder).pack(side="left")
        self.job_label = ttk.Label(top, text="（未打开）", font=_FONT)
        self.job_label.pack(side="left", padx=10)
        # Shown only while the open folder has no CIVIL.md. A button, not a question at start-up:
        # a modal box on opening a folder is a thing to click away, and it blocks everything behind it.
        self.instructions_button = ttk.Button(top, text="写 CIVIL.md 模板", command=self._write_instructions)
        ttk.Button(top, text="沙箱自检", command=self._sandbox_check).pack(side="right")
        self.backend = ttk.Combobox(top, values=("app", "os", "auto"), width=6, state="readonly")
        self.backend.pack(side="right", padx=(4, 10))
        self.backend.bind("<<ComboboxSelected>>", lambda _e: self._switch(self.controller.set_backend, self.backend.get()))
        ttk.Label(top, text="沙箱").pack(side="right")
        self.mode = ttk.Combobox(top, values=("steps", "model", "auto"), width=7, state="readonly")
        self.mode.pack(side="right", padx=(4, 10))
        self.mode.bind("<<ComboboxSelected>>", lambda _e: self._switch(self.controller.set_mode, self.mode.get()))
        ttk.Label(top, text="模式").pack(side="right")

        self.status = ttk.Label(self.root, text="", padding=(10, 4), anchor="w")
        self.status.pack(side="bottom", fill="x")
        bottom = ttk.Frame(self.root, padding=(10, 6))
        bottom.pack(side="bottom", fill="x")
        self.entry = tk.Text(bottom, height=3, wrap="word", font=_FONT, padx=8, pady=6)
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Control-Return>", lambda _e: (self._send(), "break")[1])
        self.send = ttk.Button(bottom, text="发送  Ctrl+Enter", command=self._send)
        self.send.pack(side="left", padx=(8, 0), fill="y")

        # The fixed-height rows are packed first; the body takes what is left. Packed last-to-first the
        # other way round, a tall transcript pushes the status bar out of the window.
        body = ttk.Panedwindow(self.root, orient="horizontal")
        body.pack(side="top", fill="both", expand=True, padx=10)
        left = ttk.Frame(body)
        ttk.Label(left, text="对话").pack(anchor="w")
        self.thread_list = tk.Listbox(left, width=24, exportselection=False, font=_FONT, activestyle="none")
        self.thread_list.pack(fill="both", expand=True)
        self.thread_list.bind("<<ListboxSelect>>", self._pick_thread)
        ttk.Button(left, text="新对话", command=self._new_thread).pack(fill="x", pady=4)
        body.add(left, weight=0)

        middle = ttk.Frame(body)
        self.transcript = tk.Text(middle, wrap="word", state="disabled", font=_FONT, padx=10, pady=8, spacing3=4)
        scroll = ttk.Scrollbar(middle, command=self.transcript.yview)
        self.transcript.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.transcript.pack(fill="both", expand=True)
        for tag, options in (("user", {"foreground": "#0b57d0", "spacing1": 10}), ("reply", {"spacing1": 4}),
                             ("progress", {"foreground": "#707070"}), ("system", {"foreground": "#8a5a00"}),
                             ("warn", {"foreground": "#b3261e"})):
            self.transcript.tag_configure(tag, **options)
        body.add(middle, weight=1)

        right = ttk.Frame(body)
        ttk.Label(right, text="本轮文件（双击打开）").pack(anchor="w")
        self.file_list = tk.Listbox(right, width=34, exportselection=False, font=_FONT, activestyle="none")
        self.file_list.pack(fill="both", expand=True)
        self.file_list.bind("<Double-Button-1>", self._open_file)
        ttk.Button(right, text="复核所选文稿", command=self._review).pack(fill="x", pady=4)
        body.add(right, weight=0)

        self.files: list = []
        self.thread_ids: list = []

    # ------------------------------------------------------------------ drawing helpers
    def _say(self, tag: str, text: str) -> None:
        self.transcript.configure(state="normal")
        self.transcript.insert("end", ("你：" if tag == "user" else "") + text.rstrip() + "\n", tag)
        self.transcript.configure(state="disabled")
        self.transcript.see("end")

    def _refresh(self) -> None:
        try:
            info = self.controller.status()
        except Exception as exc:  # noqa: BLE001 - a status line must never take the window down
            self.status.configure(text=f"状态读取失败：{exc}")
            return
        self.job_label.configure(text=info["job"] or "（未打开）")
        if info["job"] and not info["instructions"]:
            self.instructions_button.pack(side="left")
        else:
            self.instructions_button.pack_forget()
        self.mode.set(info["mode"]["asked"])
        self.backend.set(info["backend"]["asked"])
        kernel = info["backend"]["kernel"]
        held = "、".join(label for key, label in (("write", "写入"), ("spawn", "起进程"), ("network", "网络")) if kernel["enforces"][key])
        parts = [f"模式 {info['mode']['running']}", f"沙箱 {info['sandbox']} · {info['backend']['running']}"
                 + (f"（内核限制：{held}）" if info["backend"]["running"] == "os" else ""),
                 f"审批 {info['approval']}", f"模型 {info['model']['name']}" + ("" if info["model"]["configured"] else "（未配置）"),
                 f"插件 {len(info['plugins'])}"]
        why = " ".join(filter(None, (info["mode"]["why"], info["backend"]["why"])))
        self.status.configure(text="   ·   ".join(parts) + (f"      {why}" if why else ""))
        self.thread_ids = []
        self.thread_list.delete(0, "end")
        for row in self.controller.threads():
            self.thread_ids.append(row["id"])
            self.thread_list.insert("end", ("● " if row["current"] else "   ") + row["title"][:22])
        self.files = self.controller.files()
        self.file_list.delete(0, "end")
        for row in self.files:     # the name first — a long path in a narrow list shows everything but the name
            folder = Path(row["shown"]).parent.name
            self.file_list.insert("end", row["name"] + (f"   · {folder}" if folder else ""))

    def _guard(self, action, *args: Any, **kwargs: Any) -> Any:
        try:
            return action(*args, **kwargs)
        except DesktopError as exc:
            self._say("warn", str(exc))
        return None

    # ------------------------------------------------------------------ actions
    def _choose_folder(self) -> None:
        from tkinter import filedialog

        folder = filedialog.askdirectory(title="选择作业文件夹", mustexist=True)
        if folder:
            self._open(folder)

    def _open(self, folder: str) -> None:
        if self._guard(self.controller.open_job, folder) is None:
            return
        self._say("system", f"已进入 {self.controller.job}")
        if not self.controller.has_instructions():
            self._say("system", "这个文件夹还没有 CIVIL.md（本工程说明：项目、辖区、业主……）。需要的话点上面的「写 CIVIL.md 模板」，"
                                "只填你确认过的内容；留空的栏在成稿里保持 UNSPECIFIED。")
        self._refresh()

    def _write_instructions(self) -> None:
        if self.controller.job is not None and self._guard(self.controller.open_job, str(self.controller.job), write_instructions=True) is not None:
            self._say("system", "已写入 CIVIL.md，用记事本就能改。")
            self._refresh()

    def _switch(self, setter, value: str) -> None:
        self._guard(setter, value)
        self._refresh()

    def _new_thread(self) -> None:
        if self._guard(self.controller.new_thread) is not None:
            self.transcript.configure(state="normal")
            self.transcript.delete("1.0", "end")
            self.transcript.configure(state="disabled")
            self._refresh()

    def _pick_thread(self, _event: Any = None) -> None:
        picked = self.thread_list.curselection()
        if not picked or self.busy or self.thread_ids[picked[0]] == self.controller.thread_id:
            return
        said = self._guard(self.controller.switch_thread, self.thread_ids[picked[0]])
        if said is None:
            return
        self.transcript.configure(state="normal")
        self.transcript.delete("1.0", "end")
        self.transcript.configure(state="disabled")
        for message in said:
            self._say("user" if message["role"] == "user" else "reply", message["content"])
        self._refresh()

    def _open_file(self, _event: Any = None) -> None:
        picked = self.file_list.curselection()
        if picked:
            try:
                open_with_system(self.files[picked[0]]["path"])
            except OSError as exc:
                self._say("warn", f"打不开：{exc}")

    def _review(self) -> None:
        picked = self.file_list.curselection()
        if not picked:
            self._say("system", "先在右边选一份文稿（.md / .docx / .xlsx）。")
            return
        found = self._guard(self.controller.review, self.files[picked[0]]["path"])
        if found is not None:
            self._say("system" if found.get("clean") else "warn", found["reply"])

    def _sandbox_check(self) -> None:
        self._say("system", self.controller.sandbox_report())

    def _send(self) -> None:
        text = self.entry.get("1.0", "end").strip()
        if self.busy or not text:
            return
        self.entry.delete("1.0", "end")
        self._say("user", text)
        self.busy = True
        self.send.configure(state="disabled", text="处理中…")
        threading.Thread(target=self._work, args=(text,), name="civil-desktop-turn", daemon=True).start()

    # ------------------------------------------------------------------ worker thread <-> Tk thread
    def _work(self, text: str) -> None:
        def approve(request: Dict[str, Any]) -> bool:
            answer: Dict[str, bool] = {}
            done = threading.Event()
            self.events.put(("approve", request, answer, done))
            done.wait()
            return bool(answer.get("ok"))

        try:
            out = self.controller.submit(text, on_progress=lambda line: self.events.put(("progress", line)), approve=approve)
            self.events.put(("done", out))
        except DesktopError as exc:
            self.events.put(("failed", str(exc)))
        except Exception as exc:  # noqa: BLE001 - the window stays up and says what happened
            self.events.put(("failed", f"{type(exc).__name__}: {exc}"))

    def _drain(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "progress":
                    self._say("progress", event[1])
                elif event[0] == "approve":
                    _kind, request, answer, done = event
                    answer["ok"] = self._ask_approval(request)
                    done.set()
                elif event[0] in {"done", "failed"}:
                    self._finish(event)
        except queue.Empty:
            pass
        self.root.after(60, self._drain)

    def _finish(self, event: tuple) -> None:
        if event[0] == "failed":
            self._say("warn", event[1])
        else:
            out = event[1]
            skill = out.get("skill") or ""
            if skill:
                self._say("progress", f"  岗位 ${skill} · {out.get('expert_name') or skill}")
            self._say("warn" if not out.get("ok", True) or out.get("hitl_pending") else "reply", str(out.get("reply") or ""))
        self.busy = False
        self.send.configure(state="normal", text="发送  Ctrl+Enter")
        self._refresh()

    def _ask_approval(self, request: Dict[str, Any]) -> bool:
        """Codex asks before a risky command; this asks before a high-risk post writes. Modal, on the Tk thread."""
        tk, ttk = self.tk, self.ttk
        dialog = tk.Toplevel(self.root)
        dialog.title("需要你的确认")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)
        ttk.Label(dialog, padding=(16, 14, 16, 4), font=_FONT, wraplength=440, justify="left",
                  text=f"「{request.get('name') or '本次任务'}」是高风险岗位（risk={request.get('risk')}），要写盘。\n"
                       f"成稿只是内部讨论草稿，须由持证人员审核签认。\n\n同意，就原样输入确认句：\n{CONFIRM}\n\nTo approve in English, type exactly:\n{CONFIRM_EN}").pack()
        typed = tk.StringVar()
        field = ttk.Entry(dialog, textvariable=typed, width=46, font=_FONT)
        field.pack(padx=16, pady=6)
        field.focus_set()
        result = {"ok": False}

        def close(agree: bool) -> None:
            result["ok"] = agree and is_confirmation(typed.get())     # the field holds the sentence alone
            dialog.destroy()

        buttons = ttk.Frame(dialog, padding=(16, 6, 16, 14))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="不写盘", command=lambda: close(False)).pack(side="right")
        ttk.Button(buttons, text="确认并写盘", command=lambda: close(True)).pack(side="right", padx=8)
        dialog.bind("<Return>", lambda _e: close(True))
        dialog.bind("<Escape>", lambda _e: close(False))
        dialog.protocol("WM_DELETE_WINDOW", lambda: close(False))
        self.root.wait_window(dialog)
        return result["ok"]

    def run(self) -> int:
        self.root.mainloop()
        return 0


def write_launcher(folder: str, job: str = "") -> Path:
    """A double-clickable ``Civil Buddy.pyw`` — .pyw runs under pythonw, so no console window opens.

    It is a file the user asked for, in the folder they named; nothing is put on the desktop or in a
    start menu, and nothing is registered with the system.
    """
    repo = Path(__file__).resolve().parents[2]
    target = Path(folder).expanduser() / "Civil Buddy.pyw"
    target.write_text("# Civil Buddy desktop launcher (generated by `civil desktop --launcher`)\n"
                      "import sys\n"
                      f"sys.path.insert(0, {str(repo)!r})\n"
                      "from packing_assistant.desktop.app import main\n"
                      f"raise SystemExit(main([{str(Path(job).resolve())!r}]))\n" if job else
                      "# Civil Buddy desktop launcher (generated by `civil desktop --launcher`)\n"
                      "import sys\n"
                      f"sys.path.insert(0, {str(repo)!r})\n"
                      "from packing_assistant.desktop.app import main\n"
                      "raise SystemExit(main([]))\n", encoding="utf-8")
    return target


def main(argv: Optional[list] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        import tkinter  # noqa: F401
    except ImportError:
        print("这台机器的 Python 没有 Tk（Linux 上装 python3-tk）。命令行照常可用：python -m packing_assistant.civil", file=sys.stderr)
        return 2
    folder = args[0] if args else ""
    if not folder:
        from packing_assistant.runtime.workspace import find_job_root

        found = find_job_root()
        folder = str(found) if found else ""
    try:
        return CivilDesktop(folder=folder).run()
    except Exception as exc:  # noqa: BLE001 - usually: no display
        print(f"桌面窗口没有打开：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
