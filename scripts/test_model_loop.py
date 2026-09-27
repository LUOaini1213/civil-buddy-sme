#!/usr/bin/env python3
"""The model-driven turn, driven by a scripted model so every run is the same.

What is pinned here, in the order the loop promises it:
  the model routes        it picks the post and the job files; the pipeline writes the deliverable
  model text stays out    nothing the model wrote reaches a draft — only the user's words and his files
  numbers need a source   an invented quantity in the reply is rewritten once, and listed if it survives
  approvals hold          a high-risk post does not write without the confirm sentence; read-only never writes
  the folder is the limit reads stay inside the job folder and behind the secret guard
  it ends                 repeated calls are refused, the step budget is a hard stop, a dead endpoint is an answer
No test here talks to a real model: ``complete`` is always a script.
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
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
for name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY", "CIVIL_API_KEY", "CIVIL_AGENT_MODE"):
    os.environ.pop(name, None)     # a key in the developer's shell must never reach a test

from packing_assistant import civil  # noqa: E402
from packing_assistant.runtime import model_client, model_loop, threads, workspace  # noqa: E402
from packing_assistant.runtime.model_client import ModelError, normalise  # noqa: E402
from packing_assistant.runtime.turn import resolve_mode, run_turn  # noqa: E402

TASK = "整理日报，日期：2031年5月6日，部位：东桥3号墩，天气：晴，出勤：钢筋工12人"
NOTES = "现场记录\n木工8人进场\n浇筑混凝土45方\n"
MARKER = "MODEL-WROTE-THIS-77"
PACKING_LIST = ("S/N,Description of Goods,Q'ty,L (mm),W (mm),H (mm),G.W. (kg)\n"
                "1,Steel bracket,4,1200,400,300,12.5\n2,Base plate,2,800,800,50,40\n")
TENDER = "第一章 投标人须知\n★工期60日历天。\n★投标保证金人民币20万元。\n"
RESPONSE = "投标响应\n我方承诺工期999日历天。\n投标保证金人民币20万元已备妥。\n"


class Script:
    """A model that says what the test tells it to: tool-call lists, a final string, or a function of the messages."""

    def __init__(self, *steps):
        self.steps, self.seen = list(steps), []

    def __call__(self, messages, tools=None, **_kwargs):
        self.seen.append({"messages": copy.deepcopy(messages), "tools": tools})
        step = self.steps.pop(0) if self.steps else "（脚本已用完）"
        if callable(step):
            step = step(messages)
        if isinstance(step, str):
            return {"content": step, "tool_calls": []}
        return {"content": "", "tool_calls": [{"id": f"call_{len(self.seen)}_{i}", "name": name, "arguments": arguments}
                                              for i, (name, arguments) in enumerate(step)]}


def last_tool_result(messages):
    return json.loads(next(m["content"] for m in reversed(messages) if m["role"] == "tool"))


def run_cli(argv, stdin: str = ""):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), patch.object(sys, "stdin", io.StringIO(stdin)):
        code = civil.main(argv)
    return code, out.getvalue(), err.getvalue()


class JobFolderCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="civil-model-")
        self.addCleanup(temporary.cleanup)
        self.addCleanup(os.chdir, Path.cwd())
        self.addCleanup(workspace.deactivate)
        for name in ("CIVIL_SANDBOX", "CIVIL_APPROVAL", "CIVIL_AGENT_MODE", "CIVIL_API_KEY", "CIVIL_API_BASE", "CIVIL_MODEL"):
            self.addCleanup(lambda key=name, old=os.environ.get(name): os.environ.__setitem__(key, old) if old is not None
                            else os.environ.pop(key, None))
        self.job = Path(temporary.name).resolve()
        (self.job / "CIVIL.md").write_text("- 项目：东桥改造工程（二标段）\n- 辖区：SG\n", encoding="utf-8")
        (self.job / "现场记录.txt").write_text(NOTES, encoding="utf-8")
        (self.job / "资料").mkdir()
        (self.job / "资料" / "packing.csv").write_text(PACKING_LIST, encoding="utf-8", newline="")
        (self.job / "tender.txt").write_text(TENDER, encoding="utf-8")
        (self.job / "response.txt").write_text(RESPONSE, encoding="utf-8")
        os.chdir(self.job)
        with patch.object(Path, "home", return_value=self.job / "no-home"):
            workspace.activate(self.job)

    def drafts(self):
        return sorted((self.job / ".civil-buddy" / "out").rglob("*.md"))


class RoutingTests(JobFolderCase):
    def test_the_model_routes_and_the_pipeline_writes(self):
        script = Script(
            [("update_plan", {"steps": [{"step": "读现场记录", "status": "in_progress"}, {"step": "出日报", "status": "pending"}]})],
            [("load_skill", {"skill_id": "pm-daily"})],
            [("list_job_files", {})],
            [("read_job_file", {"name": "现场记录.txt"})],
            [("run_skill", {"skill_id": "pm-daily", "files": ["现场记录.txt"], "task": MARKER})],
            "日报草稿已出：东桥3号墩，钢筋工12人、木工8人，浇筑混凝土45方。形象进度待填。",
        )
        out = model_loop.run_model_agent(TASK, session_id="civil-cli", complete=script)
        self.assertTrue(out["ok"], out)
        self.assertEqual((out["agent_mode"], out["skill"], out["skill_source"]), ("model", "pm-daily", "model"))
        self.assertEqual(out["tools_run"], ["update_plan", "load_skill", "list_job_files", "read_job_file", "run_skill"])
        self.assertTrue(out["wrote"] and out["submit_blocked"])
        self.assertEqual(out["provenance"], {"checked": True, "rewrites": 0, "untraced": [], "verdicts": []})
        self.assertEqual([row["status"] for row in out["plan"]], ["in_progress", "pending"])
        self.assertEqual(out["usage"], {"model_calls": 6, "tool_calls": 5})

        listed = json.loads(script.seen[3]["messages"][-1]["content"])["files"]
        self.assertEqual({row["name"] for row in listed}, {"现场记录.txt", "tender.txt", "response.txt", "资料/packing.csv"})
        kinds = [event["type"] for event in out["events"]]
        self.assertEqual(kinds[0], "run_started")
        self.assertEqual(kinds[-2:], ["message", "run_ended"])
        for kind in ("plan", "skill_loaded", "tool_call", "tool_result"):
            self.assertIn(kind, kinds)

        # the deliverable carries the user's words, the file he has in the folder, and CIVIL.md — not the model's
        draft = "\n".join(path.read_text(encoding="utf-8") for path in self.drafts())
        for fact in ("东桥3号墩", "钢筋工12人", "木工8人", "东桥改造工程（二标段）"):
            self.assertIn(fact, draft)
        self.assertNotIn(MARKER, draft)
        self.assertTrue(all(str(self.job) in row["path"] for row in out["files"]), out["files"])

    def test_a_skill_the_user_named_is_loaded_up_front(self):
        script = Script([("run_skill", {"skill_id": "pm-daily"})], "已出日报草稿。")
        out = model_loop.run_model_agent(TASK, session_id="civil-cli", expert_id="pm-daily", complete=script)
        self.assertEqual((out["skill"], out["skill_source"]), ("pm-daily", "given"))
        first = script.seen[0]["messages"]
        self.assertTrue(any(m["role"] == "system" and "$pm-daily" in m["content"] for m in first[1:]), first)

    def test_an_unknown_skill_or_tool_is_an_answer_not_a_crash(self):
        script = Script([("run_skill", {"skill_id": "no-such-post"})], [("rm_rf", {"path": "/"})], "没有对得上的岗位，未出稿。")
        out = model_loop.run_model_agent("做个不存在的事", session_id="civil-cli", complete=script)
        self.assertTrue(out["ok"])
        self.assertEqual(json.loads(script.seen[1]["messages"][-1]["content"])["error_code"], "unknown_skill")
        self.assertEqual(json.loads(script.seen[2]["messages"][-1]["content"])["error_code"], "unknown_tool")
        self.assertFalse(out["wrote"])
        self.assertEqual(self.drafts(), [])


class ProvenanceTests(JobFolderCase):
    def test_an_invented_number_gets_one_rewrite(self):
        script = Script("日报信息已收到：钢筋工12人。预计明天需要 25 人，费用约 3200 元。", "日报信息已收到：钢筋工12人。明日人数与费用待填 [A001]。")
        out = model_loop.run_model_agent(TASK, session_id="civil-cli", complete=script)
        self.assertEqual(out["provenance"], {"checked": True, "rewrites": 1, "untraced": [], "verdicts": []})
        self.assertEqual(out["usage"]["model_calls"], 2)      # the rewrite is a model call too
        self.assertNotIn("3200", out["reply"])
        asked = script.seen[1]["messages"][-1]["content"]
        self.assertIn("25 人", asked)
        self.assertIn("3200 元", asked)
        self.assertIsNone(script.seen[1]["tools"])      # the rewrite may not call tools
        guards = [e["payload"] for e in out["events"] if e["type"] == "guard"]
        self.assertEqual([g["action"] for g in guards], ["rewrite"])

    def test_what_survives_the_rewrite_is_listed_to_the_user(self):
        script = Script("预计需要 5 个柜。", "大约需要 5 个柜。")
        out = model_loop.run_model_agent("这批货要几个柜", session_id="civil-cli", complete=script)
        self.assertEqual(out["provenance"]["untraced"], ["5 个柜"])
        self.assertIn("⚠", out["reply"])
        self.assertIn("5 个柜", out["reply"].split("⚠", 1)[1])

    def test_a_verdict_gets_the_same_rewrite_and_is_struck_if_it_survives(self):
        # both sentences are verbatim from a live qwen2.5:3b run (2026-09-19)
        booked = "packing.csv 这份装箱单可以装在一个 40HQ 的柜子里。所有约束条件都满足，可以订舱。"
        script = Script([("pack_plan", {"file": "资料/packing.csv"})], booked, "引擎按 packing.csv 算出 1 个 40HQ；是否订舱由你们和货代决定。")
        out = model_loop.run_model_agent("packing.csv 要几个 40HQ", session_id="civil-cli", complete=script)
        self.assertEqual(out["provenance"], {"checked": True, "rewrites": 1, "untraced": [], "verdicts": []})
        self.assertIn("可以订舱", script.seen[2]["messages"][-1]["content"])
        self.assertIn("结论不由你下", script.seen[2]["messages"][-1]["content"])
        self.assertNotIn("可以订舱", out["reply"])

        compliant = "响应文件中的投标保证金为人民币 20 万元，符合招标文件的要求。"
        stubborn = Script([("tender_compare", {"tender_file": "tender.txt", "response_file": "response.txt"})], compliant, compliant)
        out = model_loop.run_model_agent("对照招标和响应", session_id="civil-cli", complete=stubborn)
        self.assertEqual(out["provenance"]["verdicts"], ["符合招标文件的要求"])
        body, warning = out["reply"].split("⚠", 1)
        self.assertNotIn("符合招标文件的要求", body)
        self.assertIn("此处结论不由本系统判定", body)
        self.assertIn("20 万元", body)                                   # the traced number stays
        self.assertIn("符合招标文件的要求", warning)
        guards = [e["payload"] for e in out["events"] if e["type"] == "guard"]
        self.assertEqual([(g["action"], g["verdicts"]) for g in guards],
                         [("rewrite", ["符合招标文件的要求"]), ("notice", ["符合招标文件的要求"])])

    def test_a_reply_stuck_in_a_loop_is_collapsed(self):
        # verbatim shape of a live qwen2.5:3b rewrite: one sentence repeated up to the token limit
        looped = "引擎按 packing.csv 算出方案，明细见 pack-plan.md。" + "订舱后，由用户确认系固方案并完成订舱手续。" * 40 + "订舱后，由用户确认系固方案并完成订舱"
        out = model_loop.run_model_agent("总结一下", session_id="civil-cli", complete=Script(looped))
        self.assertEqual(out["reply"].count("订舱后，由用户确认系固方案并完成订舱手续。"), 1)
        self.assertEqual(out["provenance"]["repeats_dropped"], 39)
        self.assertLess(len(out["reply"]), 120)
        text, dropped = model_loop.collapse_repeats("一、核对质保书。\n二、外观检查。\n三、见证取样。\n待填。\n待填。\n")
        self.assertEqual((dropped, text.count("待填。")), (0, 2))          # short repeated cells are not a loop

    def test_disclaiming_a_verdict_is_not_stating_it(self):
        reply = "装柜明细见 pack-plan.md。内部草稿，不可直接订舱；是否满足招标要求也不由我判断。"
        script = Script(reply)
        out = model_loop.run_model_agent("总结一下", session_id="civil-cli", complete=script)
        self.assertEqual((out["reply"], out["provenance"]["rewrites"], len(script.seen)), (reply, 0, 1))

    def test_engine_numbers_pass_and_a_changed_one_does_not(self):
        def honest(messages):
            plan = last_tool_result(messages)
            return f"引擎结果：{plan['n_boxes']} 箱，需要 {plan['containers_used']} 个 {plan['container_type']}。"

        script = Script([("pack_plan", {"file": "资料/packing.csv"})], honest)
        out = model_loop.run_model_agent("给 资料/packing.csv 出装柜方案", session_id="civil-cli", complete=script)
        plan = json.loads(script.seen[1]["messages"][-1]["content"])
        self.assertTrue(plan["ok"] and plan["containers_used"] >= 1, plan)
        self.assertEqual(out["provenance"]["untraced"], [])
        self.assertEqual(out["skill"], "pack-ship")
        report = (self.job / ".civil-buddy" / "out" / "civil-cli" / "pack-ship" / "pack-plan.md").read_text(encoding="utf-8")
        self.assertIn(f"用柜数：{plan['containers_used']}", report)
        self.assertIn("packing.csv", report)
        # seen live (qwen2.5:3b): the bare key payload_kg was reported as the cargo's weight. The model gets
        # the labelled report instead of keys it has to guess the meaning of.
        self.assertEqual(plan["report"], report)
        self.assertIn("柜体额定载重（不是货重）", report)
        self.assertNotIn("payload_kg", json.dumps(plan, ensure_ascii=False))

        wrong = plan["containers_used"] + 3
        lying = Script([("pack_plan", {"file": "资料/packing.csv"})], f"需要 {wrong} 个柜。", f"需要 {wrong} 个柜。")
        out = model_loop.run_model_agent("给 资料/packing.csv 出装柜方案", session_id="civil-cli", complete=lying)
        self.assertEqual(out["provenance"]["untraced"], [f"{wrong} 个柜"])

    def test_tender_compare_reports_the_conflict_the_tool_found(self):
        def honest(messages):
            return "对照完成：" + "；".join(last_tool_result(messages)["conflicts"]) + "。需人工核验，不可递交。"

        script = Script([("tender_compare", {"tender_file": "tender.txt", "response_file": "response.txt"})], honest)
        out = model_loop.run_model_agent("对照招标和我们的响应", session_id="civil-cli", complete=script)
        result = json.loads(script.seen[1]["messages"][-1]["content"])
        self.assertTrue(result["ok"], result)
        self.assertTrue(any("999" in note and "60" in note for note in result["conflicts"]), result["conflicts"])
        self.assertEqual(out["provenance"]["untraced"], [])
        self.assertTrue(any(f["name"].startswith("collaboration-review") for f in out["files"]), out["files"])

    def test_tender_compare_says_which_file_gave_no_text(self):
        from pypdf import PdfWriter

        for name in ("response.pdf", "tender.pdf"):
            writer = PdfWriter()
            writer.add_blank_page(width=300, height=300)   # a page and no text layer: what a scanner makes
            with (self.job / name).open("wb") as stream:
                writer.write(stream)
        script = Script([("tender_compare", {"tender_file": "tender.txt", "response_file": "response.pdf"})], "响应文件没读出来，需先 OCR。")
        model_loop.run_model_agent("对照招标和我们的响应", session_id="civil-cli", complete=script)
        result = json.loads(script.seen[1]["messages"][-1]["content"])
        self.assertTrue(result["ok"], result)
        self.assertEqual([(u["title"], u["role"]) for u in result["unreadable"]], [("response.pdf", "response")])
        self.assertIn("未能判断", result["summary"])
        self.assertEqual([row["status"] for row in result["rows"] if row["response"]], [], "nothing was read, nothing is quoted")

        script = Script([("tender_compare", {"tender_file": "tender.pdf", "response_file": "response.txt"})], "招标文件没读出来。")
        before = len(self.drafts())
        model_loop.run_model_agent("对照招标和我们的响应", session_id="civil-cli", complete=script)
        refused = json.loads(script.seen[1]["messages"][-1]["content"])
        self.assertEqual((refused["ok"], refused["error_code"]), (False, "unreadable"))
        self.assertIn("tender.pdf", refused["reason"])
        self.assertEqual(len(self.drafts()), before, "no tender text, nothing written")


class ApprovalTests(JobFolderCase):
    HIGH = "编一份临边防护安全交底，部位：东桥3号墩"

    def test_a_high_risk_post_does_not_write_without_the_sentence(self):
        script = Script([("run_skill", {"skill_id": "safety-brief"})], "安全交底是高风险岗位，需要你打确认句后才写盘。")
        out = model_loop.run_model_agent(self.HIGH, session_id="civil-cli", complete=script)
        blocked = json.loads(script.seen[1]["messages"][-1]["content"])
        self.assertEqual(blocked["error_code"], "approval_required")
        self.assertTrue(out["hitl_pending"])
        self.assertFalse(out["wrote"])
        self.assertEqual(self.drafts(), [])
        asked = [e["payload"] for e in out["events"] if e["type"] == "hitl"]
        self.assertEqual((len(asked), asked[0]["risk"], asked[0]["confirm_sentence"]), (1, "high", model_loop.CONFIRM))

    def test_an_inline_yes_lets_it_write_and_a_no_does_not(self):
        requests = []
        script = Script([("run_skill", {"skill_id": "safety-brief"})], "安全交底草稿已出，待持证人员签认。")
        out = model_loop.run_model_agent(self.HIGH, session_id="civil-cli", complete=script,
                                         approve=lambda request: requests.append(request) or True)
        self.assertEqual([r["name"] for r in requests], ["安全交底"])
        self.assertTrue(out["wrote"] and not out["hitl_pending"], out)
        self.assertTrue(self.drafts())

        declined = Script([("run_skill", {"skill_id": "safety-brief"})], "未获确认，未写盘。")
        out = model_loop.run_model_agent(self.HIGH, session_id="other-session", complete=declined, approve=lambda request: False)
        self.assertTrue(out["hitl_pending"] and not out["wrote"])

    def test_read_only_never_writes(self):
        os.environ["CIVIL_SANDBOX"] = "read-only"
        script = Script([("run_skill", {"skill_id": "pm-daily"})], [("pack_plan", {"file": "资料/packing.csv"})], "只读模式，未写盘。")
        out = model_loop.run_model_agent(TASK, session_id="civil-cli", complete=script)
        self.assertEqual(json.loads(script.seen[1]["messages"][-1]["content"])["error_code"], "read_only")
        plan = json.loads(script.seen[2]["messages"][-1]["content"])
        self.assertTrue(plan["ok"] and "未写盘" in plan["saved"], plan)       # computing is allowed; saving is not
        self.assertFalse(out["wrote"])
        self.assertEqual(self.drafts(), [])


class FolderLimitTests(JobFolderCase):
    def test_reads_stay_inside_the_job_folder(self):
        outside = self.job.parent / "outside-secret.txt"
        outside.write_text("合同价 123456 元", encoding="utf-8")
        self.addCleanup(outside.unlink)
        (self.job / ".env").write_text("CIVIL_API_KEY=DOTENV-MARKER-7731", encoding="utf-8")
        script = Script([("read_job_file", {"name": "../outside-secret.txt"})], [("read_job_file", {"name": str(outside)})],
                        [("read_job_file", {"name": ".env"})], [("run_skill", {"skill_id": "pm-daily", "files": [str(outside)]})], "读不到。")
        out = model_loop.run_model_agent("读一下外面的文件", session_id="civil-cli", complete=script)
        for index in (1, 2, 3, 4):
            self.assertEqual(json.loads(script.seen[index]["messages"][-1]["content"])["error_code"], "not_found")
        self.assertNotIn("123456", json.dumps(script.seen[-1]["messages"], ensure_ascii=False))
        self.assertNotIn("DOTENV-MARKER-7731", json.dumps(script.seen[-1]["messages"], ensure_ascii=False))
        self.assertFalse(out["wrote"])

    def test_a_bare_file_name_resolves_only_when_it_is_unique(self):
        # seen live (qwen2.5:3b): "资料/packing.csv" came back as "packing.csv", and the turn gave up
        self.assertEqual(model_loop._job_path("packing.csv"), self.job / "资料" / "packing.csv")
        self.assertEqual(model_loop._job_path("PACKING.CSV"), self.job / "资料" / "packing.csv")
        (self.job / "旧版").mkdir()
        (self.job / "旧版" / "packing.csv").write_text(PACKING_LIST, encoding="utf-8")
        self.assertIsNone(model_loop._job_path("packing.csv"))
        self.assertEqual(model_loop._job_path("旧版/packing.csv"), self.job / "旧版" / "packing.csv")

    def test_state_and_project_file_are_not_listed_as_material(self):
        model_loop.run_model_agent(TASK, session_id="civil-cli", complete=Script([("run_skill", {"skill_id": "pm-daily"})], "已出。"))
        names = {row["name"] for row in model_loop.job_files()}
        self.assertNotIn("CIVIL.md", names)
        self.assertFalse(any(name.startswith(".civil-buddy") for name in names), names)


class EndingTests(JobFolderCase):
    def test_a_repeated_call_is_refused_and_the_step_budget_is_a_hard_stop(self):
        loop = [("list_job_files", {})]
        script = Script(*[loop] * 6)
        out = model_loop.run_model_agent("列文件", session_id="civil-cli", complete=script, max_steps=4)
        self.assertEqual(out["error_code"], "max_steps")
        self.assertEqual(out["usage"]["model_calls"], 4)
        self.assertEqual(json.loads(script.seen[2]["messages"][-1]["content"])["error_code"], "repeated_call")
        self.assertIn("步数上限", out["reply"])

    def test_a_dead_endpoint_is_an_answer(self):
        def dead(messages, tools=None):
            raise ModelError("无法连接模型接口，请检查 Base URL 和网络。")

        out = model_loop.run_model_agent(TASK, session_id="civil-cli", complete=dead)
        self.assertEqual((out["ok"], out["error_code"], out["wrote"]), (False, "model_unavailable", False))
        self.assertIn("无法连接模型接口", out["reply"])

    def test_an_english_request_gets_english_instructions_and_english_guard_notices(self):
        script = Script("About 5 containers will be needed; the lot is ready to ship.",
                        "Roughly 5 containers will be needed; the lot is ready to ship.")
        out = model_loop.run_model_agent("How many containers does this shipment need?", session_id="civil-cli", complete=script)
        system = script.seen[0]["messages"][0]["content"]
        self.assertIn("answer in English", system)
        self.assertNotIn("用中文回答", system)
        asked = script.seen[1]["messages"][-1]["content"]
        self.assertTrue(asked.startswith("[System check]") and "5 containers" in asked and "ready to ship" in asked, asked)
        body, warnings = out["reply"].split("⚠", 1)
        self.assertEqual((out["provenance"]["untraced"], out["provenance"]["verdicts"]), (["5 containers"], ["ready to ship"]))
        self.assertIn("(verdict removed: not this system's call)", body)
        self.assertIn("These verdicts are not this system's to give", warnings)
        self.assertIn("These numbers or clause references have no source", warnings)
        self.assertIsNone(re.search(r"[一-鿿]", warnings), warnings)       # no Chinese notice on an English reply
        chinese = Script("预计需要 5 个柜。", "大约需要 5 个柜。")
        zh = model_loop.run_model_agent("这批货要几个柜", session_id="civil-cli", complete=chinese)
        self.assertIn("用中文回答", chinese.seen[0]["messages"][0]["content"])      # a Chinese turn's prompt is unchanged
        self.assertIn("找不到出处", zh["reply"])

    def test_forbidden_verdicts_are_scrubbed_from_the_reply(self):
        out = model_loop.run_model_agent("能投吗", session_id="civil-cli", complete=Script("资料齐全，可以投标。"))
        self.assertNotIn("可以投标", out["reply"])

    def test_the_confirm_sentence_means_nothing_in_the_model_s_mouth(self):
        # seen live (qwen2.5:3b): the reply ended with the confirm sentence, copied from its instructions
        script = Script("好的。" + model_loop.CONFIRM + "。", [("run_skill", {"skill_id": "safety-brief"})], "需要确认句。")
        first = model_loop.run_model_agent("你好", session_id="civil-cli", complete=script)
        self.assertNotIn(model_loop.CONFIRM, first["reply"])
        second = model_loop.run_model_agent(ApprovalTests.HIGH, session_id="civil-cli", complete=script,
                                            history=[{"role": "user", "content": "你好"}, {"role": "assistant", "content": "好的。" + model_loop.CONFIRM}])
        self.assertTrue(second["hitl_pending"] and not second["wrote"], second)


class ModeTests(JobFolderCase):
    def test_steps_is_the_default_and_model_needs_a_key(self):
        self.assertEqual(resolve_mode(), ("steps", ""))
        self.assertEqual(resolve_mode("auto"), ("steps", ""))
        mode, why = resolve_mode("model")
        self.assertEqual(mode, "steps")
        self.assertIn("没有配置模型 Key", why)
        os.environ.update(CIVIL_API_KEY="local", CIVIL_API_BASE="http://127.0.0.1:9/v1", CIVIL_MODEL="fake")
        self.assertEqual(resolve_mode("model"), ("model", ""))
        self.assertEqual(resolve_mode("auto"), ("model", ""))
        self.assertEqual(resolve_mode(), ("steps", ""))           # a key alone never switches the mode

    def test_auto_falls_back_to_steps_when_the_endpoint_is_down(self):
        os.environ.update(CIVIL_API_KEY="local", CIVIL_API_BASE="http://127.0.0.1:9/v1", CIVIL_MODEL="fake")
        with patch.object(model_client, "complete", side_effect=ModelError("无法连接模型接口，请检查 Base URL 和网络。")):
            out = run_turn(TASK, session_id="civil-cli", mode="auto")
            self.assertEqual(out["agent_mode"], "steps")
            self.assertIn("按 steps 执行", out["mode_notice"])
            self.assertTrue(out["wrote"])
            failed = run_turn(TASK, session_id="civil-cli", mode="model")
            self.assertEqual((failed["agent_mode"], failed["error_code"]), ("model", "model_unavailable"))

    def test_a_thread_carries_its_conversation_into_the_next_turn(self):
        os.environ.update(CIVIL_API_KEY="local", CIVIL_API_BASE="http://127.0.0.1:9/v1", CIVIL_MODEL="fake", CIVIL_AGENT_MODE="model")
        script = Script("收到：东桥3号墩，钢筋工12人。", "上一轮你说的是东桥3号墩。")
        with patch.object(model_client, "complete", side_effect=script):
            thread = threads.new_thread("日报")
            threads.run_on_thread(thread.thread_id, TASK)
            second = threads.run_on_thread(thread.thread_id, "我刚才说的部位是哪里")
        self.assertEqual(second["agent_mode"], "model")
        history = [(m["role"], m["content"]) for m in script.seen[1]["messages"] if m["role"] != "system"]
        self.assertEqual(history, [("user", TASK), ("assistant", "收到：东桥3号墩，钢筋工12人。"), ("user", "我刚才说的部位是哪里")])
        self.assertEqual([m["role"] for m in threads.load_rollout(thread.thread_id)], ["user", "assistant"] * 2)

    def test_exec_jsonl_in_model_mode_is_one_turn(self):
        os.environ.update(CIVIL_API_KEY="local", CIVIL_API_BASE="http://127.0.0.1:9/v1", CIVIL_MODEL="fake")
        script = Script([("update_plan", {"steps": [{"step": "出日报", "status": "in_progress"}]})],
                        [("run_skill", {"skill_id": "pm-daily", "files": ["现场记录.txt"]})],
                        "日报草稿已出：钢筋工12人、木工8人。")
        with patch.object(model_client, "complete", side_effect=script):
            code, out, _err = run_cli(["-C", str(self.job), "--mode", "model", "exec", "--jsonl", TASK])
        self.assertEqual(code, 0, out)
        events = [json.loads(line) for line in out.splitlines() if line.strip()]
        kinds = [e["type"] for e in events]
        self.assertEqual(kinds[0], "thread.started")
        self.assertEqual(kinds.count("turn.started"), 1)
        self.assertIn("plan.updated", kinds)
        nested = [e for e in events if e.get("nested")]
        self.assertTrue(nested and all(e["type"].startswith("item.") for e in nested), nested)
        done = events[-1]
        self.assertEqual((done["type"], done["agent_mode"], done["skill"], done["wrote"]), ("turn.completed", "model", "pm-daily", True))
        self.assertEqual(done["provenance"]["untraced"], [])
        self.assertEqual(done["usage"], {"model_calls": 3, "tool_calls": 2})
        self.assertTrue(any(f.endswith(".md") for f in done["files"]), done["files"])

        with patch.object(model_client, "complete", side_effect=Script("这是闲聊回复。")):
            code, out, err = run_cli(["-C", str(self.job), "--mode", "model", "exec", "你好"])
        self.assertEqual((code, out.strip()), (0, "这是闲聊回复。"))
        status = civil.status_text()
        self.assertIn("mode     model", status)
        self.assertNotIn("local", status.split("model    ", 1)[1].split("@", 1)[0])     # the key is never printed


class ClientShapeTests(unittest.TestCase):
    NAMES = {"load_skill", "run_skill"}

    def test_provider_shapes_are_normalised(self):
        got = normalise({"content": None, "tool_calls": [{"id": "a", "type": "function", "function": {
            "name": "load_skill", "arguments": "{\"skill_id\": \"pm-daily\"}"}}]}, self.NAMES)
        self.assertEqual(got, {"content": "", "tool_calls": [{"id": "a", "name": "load_skill", "arguments": {"skill_id": "pm-daily"}}]})
        parts = normalise({"content": [{"type": "text", "text": "你"}, {"type": "text", "text": "好"}]}, self.NAMES)
        self.assertEqual(parts, {"content": "你好", "tool_calls": []})
        broken = normalise({"tool_calls": [{"function": {"name": "run_skill", "arguments": "{not json"}}]}, self.NAMES)
        self.assertEqual(broken["tool_calls"][0]["arguments"], {"_unparsed": "{not json"})

    def test_a_tool_call_written_as_text_is_still_a_tool_call(self):
        tagged = normalise({"content": "先读 SOP。<tool_call>{\"name\": \"load_skill\", \"arguments\": {\"skill_id\": \"pm-daily\"}}</tool_call>"}, self.NAMES)
        self.assertEqual((tagged["content"], tagged["tool_calls"][0]["name"], tagged["tool_calls"][0]["arguments"]),
                         ("先读 SOP。", "load_skill", {"skill_id": "pm-daily"}))
        bare = normalise({"content": "{\"name\": \"run_skill\", \"parameters\": {\"skill_id\": \"pm-daily\"}}"}, self.NAMES)
        self.assertEqual((bare["content"], bare["tool_calls"][0]["arguments"]), ("", {"skill_id": "pm-daily"}))
        other = normalise({"content": "{\"name\": \"format_disk\", \"arguments\": {}}"}, self.NAMES)
        self.assertEqual(other["tool_calls"], [])

    def test_no_key_is_said_plainly_and_nothing_is_sent(self):
        with patch("httpx.post") as post, patch("packing_assistant.llm.llm_config", return_value={"api_key": "", "base_url": "x", "model": "m"}):
            with self.assertRaises(ModelError):
                model_client.complete([{"role": "user", "content": "hi"}])
            post.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
