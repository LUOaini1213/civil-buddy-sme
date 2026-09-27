#!/usr/bin/env python3
"""The native desktop app: everything it does (controller, no display needed) and the window itself.

  controller   opens a job folder, keeps the thread, runs a turn with live progress lines, asks for the
               confirm sentence for each operation, lists what was written, reviews a document,
               switches mode and sandbox backend — and says plainly what it cannot do without a job folder
  window       a real Tk window driven end to end: type a task, the turn runs on a worker thread, the
               transcript and the file list fill in; the approval dialog takes the sentence and only the
               sentence. Skipped, loudly, where there is no display.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
for name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY", "CIVIL_API_KEY", "CIVIL_AGENT_MODE", "CIVIL_SANDBOX_BACKEND"):
    os.environ.pop(name, None)

from packing_assistant.desktop.controller import CONFIRM, CONFIRM_EN, DesktopController, DesktopError  # noqa: E402
from packing_assistant.runtime import plugins, workspace  # noqa: E402

DAILY = "整理日报，日期：2031年5月6日，部位：东桥3号墩，天气：晴，出勤：钢筋工12人"
BRIEF = "编一份临边防护安全交底，部位：东桥3号墩"


def display_problem() -> str:
    try:
        import tkinter

        probe = tkinter.Tk()
        probe.destroy()
        return ""
    except Exception as exc:  # noqa: BLE001 - ImportError (no python3-tk) or TclError (no display)
        return f"{type(exc).__name__}: {exc}"


NO_DISPLAY = display_problem()


class JobCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="civil-desktop-")
        self.addCleanup(temporary.cleanup)
        self.addCleanup(os.chdir, Path.cwd())
        self.addCleanup(plugins.reload)
        self.addCleanup(workspace.deactivate)
        for name in ("CIVIL_AGENT_MODE", "CIVIL_SANDBOX_BACKEND"):
            self.addCleanup(os.environ.pop, name, None)
        self.job = Path(temporary.name).resolve() / "东桥二标"
        self.job.mkdir()
        (self.job / "现场记录.txt").write_text("现场记录\n木工8人进场\n", encoding="utf-8")
        home = patch.object(Path, "home", return_value=self.job.parent / "home")
        home.start()
        self.addCleanup(home.stop)
        self.state = self.job / ".civil-buddy" / "out"


class ControllerTests(JobCase):
    def test_nothing_runs_without_a_job_folder(self):
        controller = DesktopController()
        self.assertEqual((controller.status()["job"], controller.threads(), controller.files()), ("", [], []))
        for action in (lambda: controller.submit(DAILY), controller.new_thread, lambda: controller.review("x.md")):
            with self.assertRaises(DesktopError):
                action()
        with self.assertRaises(DesktopError):
            controller.open_job(str(self.job / "不存在"))

    def test_opening_a_folder_gives_a_thread_and_optionally_the_project_file(self):
        controller = DesktopController()
        info = controller.open_job(str(self.job))
        self.assertEqual((info["job"], info["instructions"], info["mode"]["running"], info["backend"]["running"]),
                         (str(self.job), False, "steps", "app"))
        self.assertEqual([t["current"] for t in controller.threads()], [True])
        self.assertTrue(controller.open_job(str(self.job), write_instructions=True)["instructions"])
        self.assertEqual(len(controller.threads()), 1)                  # reopening reuses the latest thread

    def test_a_turn_streams_progress_and_lists_what_it_wrote(self):
        controller, lines = DesktopController(), []
        controller.open_job(str(self.job))
        out = controller.submit(DAILY, on_progress=lines.append)
        self.assertTrue(out["ok"] and out["wrote"], out.get("reply"))
        self.assertTrue(any("pm-daily__log" in line for line in lines), lines)
        shown = [f["shown"] for f in controller.files()]
        self.assertTrue(any(s.endswith("pm-daily__log.md") and s.startswith(".civil-buddy") for s in shown), shown)
        self.assertTrue(all(Path(f["path"]).is_file() for f in controller.files()))
        said = controller.switch_thread(controller.thread_id)
        self.assertEqual([(m["role"], m["content"][:4]) for m in said], [("user", "整理日报"), ("assistant", "项目日报")])
        with self.assertRaises(DesktopError):
            controller.submit("   ")

    def test_the_sentence_is_asked_once_per_operation_and_never_carried_forward(self):
        controller, asked = DesktopController(), []
        controller.open_job(str(self.job))
        refused = controller.submit(BRIEF, approve=lambda request: asked.append(request) or False)
        self.assertTrue(refused["hitl_pending"] and not refused["wrote"])
        self.assertEqual(list(self.state.rglob("safety-brief*.md")), [])
        granted = controller.submit(BRIEF, approve=lambda request: asked.append(request) or True)
        self.assertTrue(granted["wrote"] and not granted["hitl_pending"], granted.get("reply"))
        self.assertEqual([r["name"] for r in asked], ["安全交底", "安全交底"])
        self.assertFalse(controller.status()["confirmed"])
        again = controller.submit(BRIEF, approve=lambda request: asked.append(request) or False)
        self.assertFalse(again["wrote"])
        self.assertTrue(again["hitl_pending"])
        self.assertEqual(len(asked), 3)                                  # a new operation needs a new decision
        controller.switch_thread(controller.thread_id)
        self.assertFalse(controller.submit(BRIEF, approve=lambda _r: False)["wrote"])
        typed = DesktopController()
        typed.open_job(str(self.job))
        typed.new_thread()
        self.assertTrue(typed.submit(BRIEF + "。" + CONFIRM)["wrote"])     # typing the sentence in the task counts too
        self.assertFalse(typed.submit(BRIEF, approve=lambda _r: False)["wrote"])
        english = DesktopController()
        english.open_job(str(self.job))
        english.new_thread()
        self.assertTrue(english.submit(BRIEF + ". " + CONFIRM_EN)["wrote"])   # so does the English one
        self.assertFalse(english.submit(BRIEF, approve=lambda _r: False)["wrote"])
        lower = DesktopController()
        lower.open_job(str(self.job))
        lower.new_thread()
        self.assertFalse(lower.submit(BRIEF + ". " + CONFIRM_EN.lower(), approve=lambda _r: False)["wrote"])

    def test_switches_are_validated_and_review_goes_through(self):
        controller = DesktopController()
        controller.open_job(str(self.job))
        self.assertEqual((controller.set_mode("auto"), os.environ["CIVIL_AGENT_MODE"]), ("auto", "auto"))
        self.assertEqual(controller.status()["mode"]["running"], "steps")          # auto without a model is steps
        self.assertEqual(controller.set_backend("app"), "app")
        for setter in (controller.set_mode, controller.set_backend):
            with self.assertRaises(DesktopError):
                setter("turbo")
        (self.job / "周报.md").write_text("# 周报\n\n木工8人，架子工 9 人。\n", encoding="utf-8")
        self.assertEqual([n["text"] for n in controller.review("周报.md")["numbers"]], ["9 人"])
        first = controller.thread_id
        second = controller.new_thread("第二个")
        self.assertEqual([t["id"] for t in controller.threads() if t["current"]], [second])
        self.assertNotEqual(first, second)


class LauncherTests(JobCase):
    def test_the_launcher_is_a_plain_script_in_the_folder_that_was_named(self):
        from packing_assistant.desktop.app import write_launcher

        target = write_launcher(str(self.job.parent), str(self.job))
        self.assertEqual((target.name, target.parent), ("Civil Buddy.pyw", self.job.parent))
        source = target.read_text(encoding="utf-8")
        compile(source, str(target), "exec")
        self.assertIn(repr(str(ROOT)), source)
        self.assertIn(repr(str(self.job)), source)
        self.assertIn("main([])", write_launcher(str(self.job.parent)).read_text(encoding="utf-8"))


@unittest.skipIf(bool(NO_DISPLAY), f"no display for a real window here ({NO_DISPLAY})")
class WindowTests(JobCase):
    def window(self):
        """A real window that cannot outlive its test: a modal dialog nobody answers would otherwise wait forever."""
        from packing_assistant.desktop.app import CivilDesktop

        app = CivilDesktop(folder=str(self.job))

        def close_everything() -> None:
            for widget in list(app.root.winfo_children()):
                if widget.winfo_class() == "Toplevel":
                    widget.destroy()
            app.busy = False

        app.root.after(90_000, close_everything)
        self.addCleanup(lambda: app.root.winfo_exists() and app.root.destroy())
        return app

    @staticmethod
    def answer(app, sentence: str, tries: int = 300):
        """Answer the approval dialog whenever it appears — it opens when the blocked turn gets there.

        The confirm button is invoked rather than a key press generated: Tk drops generated key events
        for a window that is not mapped yet, and a dialog opened by a worker's request can be caught
        in exactly that state (that is how the first version of this test hung).
        """
        def act(left: int = tries) -> None:
            dialogs = [w for w in app.root.winfo_children() if w.winfo_class() == "Toplevel"]
            if dialogs:
                field = next(w for w in dialogs[0].winfo_children() if w.winfo_class() == "TEntry")
                field.delete(0, "end")
                field.insert(0, sentence)
                buttons = [b for frame in dialogs[0].winfo_children() if frame.winfo_class() == "TFrame"
                           for b in frame.winfo_children() if b.winfo_class() == "TButton"]
                next(b for b in buttons if "确认" in str(b.cget("text"))).invoke()
            elif left:
                app.root.after(100, lambda: act(left - 1))
        return act

    def pump(self, app, until, seconds: float = 60.0) -> None:
        deadline = time.monotonic() + seconds
        while not until() and time.monotonic() < deadline:
            app.root.update()
            time.sleep(0.02)
        self.assertTrue(until(), "the window did not get there in time")

    def text(self, app) -> str:
        return app.transcript.get("1.0", "end")

    def test_a_task_typed_into_the_window_runs_and_fills_it_in(self):
        app = self.window()
        app.root.update()
        self.assertIn(str(self.job), app.job_label.cget("text"))
        # no CIVIL.md yet: a button offers the template — opening a folder must never stop on a modal question
        self.assertTrue(app.instructions_button.winfo_ismapped())
        app.instructions_button.invoke()
        app.root.update()
        self.assertTrue((self.job / "CIVIL.md").is_file())
        self.assertFalse(app.instructions_button.winfo_ismapped())
        app.entry.insert("1.0", DAILY)
        app._send()
        self.assertTrue(app.busy)
        self.assertEqual(str(app.send.cget("state")), "disabled")
        self.pump(app, lambda: not app.busy)
        shown = self.text(app)
        for expected in ("你：整理日报", "pm-daily__log", "项目日报 已出内部讨论草稿"):
            self.assertIn(expected, shown)
        self.assertTrue(any(str(item).startswith("pm-daily__log.docx") for item in app.file_list.get(0, "end")))
        self.assertIn("模式 steps", app.status.cget("text"))
        app.root.update_idletasks()      # the status bar and the input row are inside the window, whatever the transcript wants
        for widget in (app.status, app.send):
            self.assertTrue(widget.winfo_ismapped())
            self.assertLessEqual(widget.winfo_y() + widget.winfo_height(), app.root.winfo_height() + 1)
        app.file_list.selection_set(0)
        app._review()
        self.assertIn("review ", self.text(app))

    def test_the_approval_dialog_takes_the_sentence_and_only_the_sentence(self):
        app = self.window()
        app.root.after(200, self.answer(app, "好的"))
        self.assertFalse(app._ask_approval({"name": "安全交底", "risk": "high"}))
        app.root.after(200, self.answer(app, CONFIRM))
        self.assertTrue(app._ask_approval({"name": "安全交底", "risk": "high"}))
        app.root.after(200, self.answer(app, CONFIRM_EN))
        self.assertTrue(app._ask_approval({"name": "安全交底", "risk": "high"}))
        app.root.after(200, self.answer(app, "No. " + CONFIRM_EN))       # the field holds the sentence alone
        self.assertFalse(app._ask_approval({"name": "安全交底", "risk": "high"}))

        app.entry.insert("1.0", BRIEF)
        app.root.after(200, self.answer(app, CONFIRM))   # keeps looking until the blocked turn opens the dialog
        app._send()
        self.pump(app, lambda: not app.busy)
        self.assertIn("安全交底 已出内部讨论草稿", self.text(app))
        self.assertTrue(list(self.state.rglob("safety-brief*.md")))


if __name__ == "__main__":
    print("display:", NO_DISPLAY or "available")
    unittest.main(verbosity=2)
