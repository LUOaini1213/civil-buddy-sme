#!/usr/bin/env python3
"""invoke_with_checkpoint lets a graph node's own error reach the caller.

Before 2026-09-28 packing_assistant/lg_checkpoint.py caught any exception from app.invoke(state, config) and
re-invoked without a config. On a real graph compiled with the shared SqliteSaver that second call fails at once
with "Checkpointer requires ... thread_id", so the caller saw that ValueError instead of the node's error (the
node itself ran once - no side effect was repeated, the real error was just lost).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import TypedDict
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"


class S(TypedDict, total=False):
    n: int
    seen: int


class NodeErrors(unittest.TestCase):
    def setUp(self):
        from packing_assistant import lg_checkpoint

        tmp = tempfile.TemporaryDirectory(prefix="civil-lg-")
        self.addCleanup(tmp.cleanup)
        env = patch.dict(os.environ, {"PACKING_LG_CHECKPOINT": "1",
                                      "PACKING_LG_CHECKPOINT_PATH": str(Path(tmp.name) / "cp.db")})
        env.start()
        self.addCleanup(env.stop)
        # a private saver for this test, and the module singleton put back afterwards
        saved = (lg_checkpoint._SAVER, lg_checkpoint._CONN)
        lg_checkpoint._SAVER = lg_checkpoint._CONN = None

        def restore():
            if lg_checkpoint._CONN is not None:
                lg_checkpoint._CONN.close()
            lg_checkpoint._SAVER, lg_checkpoint._CONN = saved

        self.addCleanup(restore)
        self.lg = lg_checkpoint

    def app(self, node):
        from langgraph.graph import END, StateGraph

        g = StateGraph(S)
        g.add_node("a", node)
        g.set_entry_point("a")
        g.add_edge("a", END)
        return g.compile(checkpointer=self.lg.get_checkpointer())

    def test_the_nodes_own_error_surfaces_and_the_node_runs_once(self):
        for exc_type in (RuntimeError, TypeError, ValueError):
            with self.subTest(error=exc_type.__name__):
                runs = []

                def node(state, exc_type=exc_type, runs=runs):
                    runs.append(1)
                    raise exc_type("the real node failure")

                with self.assertRaises(exc_type) as caught:
                    self.lg.invoke_with_checkpoint(self.app(node), {"n": 1}, f"thread-{exc_type.__name__}")
                self.assertEqual(str(caught.exception), "the real node failure")
                self.assertNotIn("thread_id", str(caught.exception))
                self.assertEqual(len(runs), 1)

    def test_a_good_run_is_still_checkpointed_under_its_thread(self):
        app = self.app(lambda state: {"seen": state.get("n", 0) + 1})
        self.assertEqual(self.lg.invoke_with_checkpoint(app, {"n": 4}, "thread-ok")["seen"], 5)
        self.assertEqual(self.lg.get_thread_state("thread-ok", app)["seen"], 5)

    def test_checkpoint_off_still_invokes_without_config(self):
        calls = []

        class App:
            def invoke(self, state):
                calls.append(state)
                return {"ok": True}

        with patch.dict(os.environ, {"PACKING_LG_CHECKPOINT": "0"}):
            self.assertEqual(self.lg.invoke_with_checkpoint(App(), {"n": 1}, "t"), {"ok": True})
        self.assertEqual(calls, [{"n": 1}])


if __name__ == "__main__":
    unittest.main()
