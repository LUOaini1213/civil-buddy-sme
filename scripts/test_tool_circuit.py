#!/usr/bin/env python3
"""The real ToolEngine fault circuit (not the session cost fuse): closed -> open -> half-open -> closed / re-open.

Before 2026-09-28 the circuit had no timer: after three consecutive faults a tool on the shared engine stayed
refused until the process restarted (a good call 121 s later still got circuit_open, handler count 3), and a
handler that answered invalid_args or permission_denied three times opened it too. Time is advanced by patching
time.monotonic, so these tests do not wait out the cool-down.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from packing_assistant.runtime.tool_engine import ERR_CIRCUIT, ToolEngine  # noqa: E402


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Flaky:
    """A handler whose next answers are scripted: "fault", "ok", "raise", "sleep" or an error code to return."""

    def __init__(self, *script: str) -> None:
        self.script = list(script)
        self.calls = 0

    def __call__(self, args):
        self.calls += 1
        step = self.script.pop(0) if self.script else "ok"
        if step == "ok":
            return {"ok": True}
        if step == "raise":
            raise RuntimeError("downstream exploded")
        if step == "sleep":
            time.sleep(0.5)
            return {"ok": True}
        if step == "fault":
            return {"ok": False, "error_code": "unspecified"}
        return {"ok": False, "error_code": step}


class CircuitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        patcher = patch("time.monotonic", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def engine(self, handler) -> ToolEngine:
        eng = ToolEngine(circuit_cooldown_s=45.0)
        eng.register("t", handler, timeout_s=2.0)
        return eng

    def trip(self, eng: ToolEngine) -> None:
        for _ in range(eng.circuit_threshold):
            self.assertEqual(eng.execute("t")["error_code"], "unspecified")
        self.assertEqual(eng.circuit_state("t"), "open")

    def test_default_cool_down_is_between_30_and_60_seconds(self):
        eng = ToolEngine()
        self.assertEqual(eng.circuit_threshold, 3)
        self.assertGreaterEqual(eng.circuit_cooldown_s, 30)
        self.assertLessEqual(eng.circuit_cooldown_s, 60)

    def test_three_faults_open_it_and_it_refuses_without_calling_the_handler_until_the_cool_down(self):
        h = Flaky("fault", "fault", "fault")
        eng = self.engine(h)
        self.trip(eng)
        self.clock.now += 44.9
        refused = eng.execute("t")
        self.assertEqual(refused["error_code"], ERR_CIRCUIT)
        self.assertEqual(refused["policy"]["code"], "circuit_open")
        self.assertEqual(h.calls, 3)

    def test_after_the_cool_down_one_trial_success_closes_it(self):
        h = Flaky("fault", "fault", "fault", "ok")
        eng = self.engine(h)
        self.trip(eng)
        self.clock.now += 45
        self.assertEqual(eng.circuit_state("t"), "half_open")
        self.assertTrue(eng.execute("t")["ok"])
        self.assertEqual(eng.circuit_state("t"), "closed")
        self.assertEqual(eng._fail_streak["t"], 0)
        self.assertTrue(eng.execute("t")["ok"])
        self.assertEqual(h.calls, 5)

    def test_a_failed_trial_re_opens_it_for_a_fresh_cool_down(self):
        h = Flaky("fault", "fault", "fault", "fault", "ok")
        eng = self.engine(h)
        self.trip(eng)
        self.clock.now += 50
        self.assertEqual(eng.execute("t")["error_code"], "unspecified")   # the trial, and it fails
        self.assertEqual(eng.circuit_state("t"), "open")
        self.clock.now += 44
        self.assertEqual(eng.execute("t")["error_code"], ERR_CIRCUIT)      # the new cool-down counts from the trial
        self.assertEqual(h.calls, 4)
        self.clock.now += 1
        self.assertTrue(eng.execute("t")["ok"])
        self.assertEqual(eng.circuit_state("t"), "closed")

    def test_a_crashing_or_timed_out_trial_also_re_opens_it(self):
        for step in ("raise", "sleep"):
            with self.subTest(step=step):
                h = Flaky("fault", "fault", "fault", step)
                eng = ToolEngine(circuit_cooldown_s=45.0)
                eng.register("t", h, timeout_s=0.1 if step == "sleep" else 2.0)
                self.trip(eng)
                self.clock.now += 45
                out = eng.execute("t")
                self.assertEqual(out["error_code"], "timeout" if step == "sleep" else "invalid_args")
                self.assertEqual(eng.circuit_state("t"), "open")

    def test_only_one_trial_runs_while_half_open(self):
        started, release = threading.Event(), threading.Event()
        calls = []

        def slow_ok(args):
            calls.append(1)
            if len(calls) > 3:
                started.set()
                release.wait(5)
                return {"ok": True}
            return {"ok": False, "error_code": "unspecified"}

        eng = ToolEngine(circuit_cooldown_s=45.0)
        eng.register("t", slow_ok, timeout_s=10)
        self.trip(eng)
        self.clock.now += 45
        box = {}
        trial = threading.Thread(target=lambda: box.setdefault("out", eng.execute("t")))
        trial.start()
        self.assertTrue(started.wait(5))
        second = eng.execute("t")
        self.assertEqual(second["error_code"], ERR_CIRCUIT)
        release.set()
        trial.join(5)
        self.assertTrue(box["out"]["ok"])
        self.assertEqual(len(calls), 4)
        self.assertEqual(eng.circuit_state("t"), "closed")

    def test_a_refused_trial_does_not_use_up_the_half_open_slot(self):
        h = Flaky("fault", "fault", "fault", "ok")
        eng = ToolEngine(circuit_cooldown_s=45.0)
        eng.register("t", h, writes=True)
        self.trip(eng)
        self.clock.now += 45
        chat = eng.execute("t", intent="chat")                       # policy denial: a write in a chat turn
        self.assertEqual(chat["error_code"], "permission_denied")
        self.assertEqual(eng.circuit_state("t"), "half_open")
        self.assertTrue(eng.execute("t")["ok"])
        self.assertEqual(eng.circuit_state("t"), "closed")

    def test_invalid_args_and_policy_denials_never_count_as_faults(self):
        for code in ("invalid_args", "permission_denied", "cancelled"):
            with self.subTest(returned=code):
                h = Flaky(*[code] * 5)
                eng = self.engine(h)
                seen = [eng.execute("t")["error_code"] for _ in range(5)]
                self.assertEqual(seen, [code] * 5)
                self.assertEqual(eng._fail_streak.get("t", 0), 0)
                self.assertEqual(eng.circuit_state("t"), "closed")
        # refused by contract or policy before the handler: never a fault either
        eng = ToolEngine(circuit_threshold=1)
        eng.register("w", Flaky(), writes=True, schema_keys=("path",),
                     input_schema={"type": "object", "properties": {"path": {"type": "string"}},
                                   "additionalProperties": False})
        for _ in range(3):
            self.assertEqual(eng.execute("w", {"nope": 1})["error_code"], "invalid_args")   # schema
            self.assertEqual(eng.execute("w", {})["error_code"], "invalid_args")            # missing key
            self.assertEqual(eng.execute("w", {"path": "x"}, intent="chat")["error_code"], "permission_denied")
        self.assertFalse(eng._fail_streak.get("w"))
        self.assertEqual(eng.circuit_state("w"), "closed")

    def test_a_verdict_between_faults_neither_resets_nor_adds_to_the_streak(self):
        h = Flaky("fault", "fault", "invalid_args", "fault")
        eng = self.engine(h)
        codes = [eng.execute("t")["error_code"] for _ in range(4)]
        self.assertEqual(codes, ["unspecified", "unspecified", "invalid_args", "unspecified"])
        self.assertEqual(eng.circuit_state("t"), "open")

    def test_admit_for_callers_that_run_the_handler_themselves_follows_the_same_states(self):
        eng = self.engine(Flaky("fault", "fault", "fault"))
        self.trip(eng)
        self.assertEqual(eng.admit("t")[1]["error_code"], ERR_CIRCUIT)
        self.assertIsNone(eng.admit("t", circuit=False)[1])
        self.clock.now += 45
        self.assertIsNone(eng.admit("t")[1])               # half-open: not refused, and no trial slot consumed
        self.assertTrue(eng.execute("t")["ok"])
        self.assertEqual(eng.circuit_state("t"), "closed")


if __name__ == "__main__":
    unittest.main()
