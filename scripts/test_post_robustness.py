#!/usr/bin/env python3
"""Every post, fed input meant to break it: the draft still comes out, and comes out intact.

The writers are being taught to put the user's words into table cells. That is exactly where a
pipe, a newline, a Markdown table of the user's own or twenty thousand characters turn a draft into
something that no longer parses - or, worse, into something the user can steer. Each post's writer
is run through the ToolEngine entry (run_named_exclusive) on every hostile input below, and the
result is checked for the things that must hold whatever the input was.

Offline, no model, no key.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from packing_assistant import expert_turn  # noqa: E402
from packing_assistant.expert_roster import list_experts  # noqa: E402
from packing_assistant.runtime import agent_loop  # noqa: E402

# pack-ship runs the real packing solver through its own tools and has its own benchmarks.
POSTS = [(e.id, [t for t in e.exclusive if "fill_scheme" not in t][0])
         for e in list_experts() if e.exclusive and e.id != "pack-ship" and getattr(e, "category", "") != "plugin"]

INPUTS = {
    "empty": "",
    "blank": "   \n\t  \n",
    "junk": "随便写点",
    "punctuation": "；；，，、、：：|||",
    "pipes": "螺纹钢|HRB400|入库|35 吨|领用|12 吨",
    "newlines_in_a_value": "部位：3#楼\n地下室\n负责人：张伟",
    "user_table": "| 物资 | 入库 |\n| --- | --- |\n| 螺纹钢 | 35 吨 |\n| 水泥 | 80 吨 |",
    "html": "<script>alert(1)</script> 与 <b>粗体</b>，部位 3#楼",
    "markdown_heading": "# 我的标题\n## 第二级\n部位：3#楼，数量 35 吨",
    "very_long": "螺纹钢 HRB400 入库 35 吨，" * 800,
    "control_chars": "部位\x00：3#楼\x07，数量\x1b 35 吨",
    "rtl_and_emoji": "部位：3#楼 ‮ reversed ‬ 数量 35 吨",
    "only_numbers": "35 12 0.3 2026-09-18 3#楼",
    "english": "rebar HRB400 inbound 35 t, issued 12 t, keeper Zhang Wei",
}

# A conclusion no post may state on its own. Each is checked only where the writer's own fixed text
# cannot legitimately use it in a refusal, so the pattern requires an affirmative shape.
VERDICTS = (
    re.compile(r"(?<![不未])检验合格"), re.compile(r"(?<![不未])判定合格"), re.compile(r"(?<![不未])验收通过"),
    re.compile(r"(?<![不未])可以开工"), re.compile(r"(?<![不未])准予"), re.compile(r"(?<![不未])审批通过"),
)
DISCLAIMER_MARKS = ("AI 草稿", "内部讨论", "草稿", "AI draft")

# Posts whose writer still interpolates the user's words into a table row without escaping "|", so a
# pipe the user typed adds cells to that row and shifts every value after it one heading to the
# right. Measured 2026-09-20, before the writers were rewritten. This list only ever shrinks: the
# test fails both on a post that newly breaks and on one that is fixed but still listed.
PIPES_SHIFT_COLUMNS = {}


def table_rows(markdown: str) -> list[tuple[int, list[str]]]:
    return [(number, [cell.strip() for cell in line.strip().strip("|").split("|")])
            for number, line in enumerate(markdown.splitlines(), 1)
            if line.strip().startswith("|") and line.strip().endswith("|")]


class PostRobustness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "output").mkdir(exist_ok=True)
        cls._tmp = tempfile.TemporaryDirectory(prefix="post-robustness-", dir=ROOT / "output")
        cls.out = Path(cls._tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def draft(self, post: str, tool: str, text: str) -> str:
        with patch.object(expert_turn, "_OUT", self.out), patch.object(agent_loop, "_OUT", self.out):
            result = expert_turn.run_named_exclusive(
                tool, {"text": text, "confirm_ok": True, "session_id": f"rob-{post}"})
        self.assertIsInstance(result, dict)
        self.assertTrue(result.get("submit_blocked"), f"{post}: submit_blocked must stay true")
        path = next((f.get("path") for f in result.get("files") or [] if str(f.get("name") or "").endswith(".md")), None)
        self.assertIsNotNone(path, f"{post}: no markdown deliverable for this input")
        return Path(path).read_text(encoding="utf-8")

    def test_every_post_survives_every_hostile_input(self):
        for post, tool in POSTS:
            for name, text in INPUTS.items():
                with self.subTest(post=post, input=name):
                    markdown = self.draft(post, tool, text)
                    self.assertTrue(markdown.strip(), "empty deliverable")
                    self.assertTrue(any(mark in markdown for mark in DISCLAIMER_MARKS),
                                    "the draft disclaimer is gone")

    def ragged_tables(self, post: str, tool: str, text: str) -> list[str]:
        """Lines of a table whose cell count differs from the line above it, inside one table.

        A Markdown table is rows of the same width. A pipe the user typed, pasted along with a
        value, adds cells to that one row and shifts every value after it one column to the right -
        so a quantity ends up reported under someone else's heading.
        """
        rows = table_rows(self.draft(post, tool, text))
        ragged = []
        for index in range(1, len(rows)):
            number, cells = rows[index]
            previous_number, previous = rows[index - 1]
            if number != previous_number + 1 or len(cells) == len(previous):
                continue  # a different table, or a row of the expected width
            if all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells if cell):
                continue  # the separator line under a header
            ragged.append(f"line {number}: {len(cells)} cells against {len(previous)} above")
        return ragged

    def test_a_users_pipes_newlines_and_markdown_cannot_break_a_table(self):
        hostile = ("pipes", "newlines_in_a_value", "user_table", "markdown_heading", "control_chars")
        broken = {}
        for post, tool in POSTS:
            for name in hostile:
                if self.ragged_tables(post, tool, INPUTS[name]):
                    broken.setdefault(post, set()).add(name)
        new = {post: sorted(names - set(PIPES_SHIFT_COLUMNS.get(post, ()))) for post, names in broken.items()
               if names - set(PIPES_SHIFT_COLUMNS.get(post, ()))}
        fixed = {post: sorted(set(names) - broken.get(post, set())) for post, names in PIPES_SHIFT_COLUMNS.items()
                 if set(names) - broken.get(post, set())}
        self.assertEqual(new, {}, "a user's pipe shifts the columns of a table in these posts")
        self.assertEqual(fixed, {}, "these no longer break - remove them from PIPES_SHIFT_COLUMNS")

    def test_no_post_states_a_verdict_on_its_own(self):
        for post, tool in POSTS:
            for name in ("junk", "pipes", "only_numbers", "english"):
                with self.subTest(post=post, input=name):
                    markdown = self.draft(post, tool, INPUTS[name])
                    for verdict in VERDICTS:
                        self.assertIsNone(verdict.search(markdown), f"{post}/{name}: states {verdict.pattern}")

    def test_a_very_long_request_does_not_produce_an_unbounded_draft(self):
        for post, tool in POSTS:
            with self.subTest(post=post):
                markdown = self.draft(post, tool, INPUTS["very_long"])
                body = markdown.split("## 用户原文", 1)
                tail = body[1] if len(body) > 1 else markdown
                rows = [cells for _, cells in table_rows(tail)]
                widest = max((len(cell) for cells in rows for cell in cells), default=0)
                self.assertLess(widest, 2000, f"{post}: a table cell of {widest} characters")


if __name__ == "__main__":
    unittest.main(verbosity=1)
