"""Interactive Civil Codex TUI: slash commands, $skills, threads, approvals."""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

from packing_assistant.runtime.civil_config import CONFIRM, CONFIRM_EN, APPROVAL_MODES, SANDBOX_MODES, is_confirmation, confirms_in_message, load_config

HELP = """/help              本页
/status            作业文件夹 · CIVIL.md · sandbox · approval · 模型 · thread · 会话槽
/init              在当前文件夹写一份 CIVIL.md（本工程说明）
/model [名称]      查看 / 切换本进程用的模型（不写盘，不显示 Key）
/mode [模式]       steps 规则路由、不调模型 | model 模型驱动 | auto 有模型就用
/skills [词]       技能目录（name + description）
/approvals [mode]  untrusted | on-request | never
/sandbox [mode]    read-only | workspace-write
/new [标题]        新 thread（可并行）
/threads           列出 thread
/resume <id>       切到该 thread
/bg <任务>         在新 thread 后台跑
/files             本 thread 交付物
/plan              上一轮模型列的步骤
/review <文稿>     不调模型：文稿里的数字有没有出处、有没有不该下的结论
/plugins           已装插件、是否受信任、带来哪些岗位（安装与信任用 civil plugin …）
/confirm <签认句>  仅确认并重试当前待签认的原任务
/mcp               IDE/MCP 怎么挂
/quit              退出

任务直接回车。显式 skill： $construction  或  @施工方案
高风险写盘确认句：""" + CONFIRM + "\nSign-off sentence (English): " + CONFIRM_EN


def _enable_vt() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)
        mode = ctypes.c_uint()
        if k.GetConsoleMode(h, ctypes.byref(mode)):
            k.SetConsoleMode(h, mode.value | 0x0004)
    except Exception:
        pass


def _c(code: str, text: str) -> str:
    if not sys.stderr.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


class TuiState:
    def __init__(self) -> None:
        from packing_assistant.runtime.threads import new_thread

        self.cfg = load_config()
        self.thread = new_thread("主对话")
        self.confirm = False
        self.pending_text = ""
        self.pending_thread_id = ""
        self.last_skill = ""
        self.last_skill_source = ""
        self.last_plan: List[Dict[str, str]] = []


def _banner(st: TuiState) -> None:
    from packing_assistant.office_job import job_root, job_root_granted
    from packing_assistant.runtime.expert_skills import list_expert_skill_ids

    n = len(list_expert_skill_ids())
    root = str(job_root()) if job_root_granted() else st.cfg.job_root or "（cwd/.civil-buddy/out）"
    print(_c("1", "Civil Buddy · 土木版 Codex"))
    print(
        f"  thread {st.thread.thread_id}  sandbox {st.cfg.sandbox}  "
        f"approval {st.cfg.approval}  skills {n}"
    )
    print(f"  job {root}")
    print(_c("2", "  /help  /status  /init  /skills  /new  /bg  /approvals  /sandbox   空行退出"))
    print()


def _print_out(out: Dict[str, Any]) -> None:
    eid = out.get("skill") or out.get("expert_id") or ""
    src = out.get("skill_source") or ""
    if eid:
        how = {"given": "显式", "model": "模型选用"}.get(src, "选用")
        print(_c("36", f"skill ${eid} · {out.get('expert_name') or eid} · {how}"))
    else:
        print(_c("36", "skill （未选用）" if out.get("agent_mode") == "model" else "skill （路由器）"))
    bits = [
        f"mode {out.get('agent_mode') or 'steps'}",
        f"intent {out.get('intent')}",
        f"wrote {out.get('wrote')}",
        f"submit_blocked {out.get('submit_blocked')}",
    ]
    if out.get("sandbox_mode"):
        bits.append(f"sandbox {out.get('sandbox_mode')}")
    if out.get("approval"):
        bits.append(f"approval {out.get('approval')}")
    print(_c("2", " · ".join(bits)))
    if out.get("hitl_pending"):
        print(_c("33", f"approval 须确认句：{CONFIRM}  |  or type: {CONFIRM_EN}"))
    print(out.get("reply") or "")
    from packing_assistant.civil import _file_paths, display_path

    for path in _file_paths(out):
        print(_c("2", "  + " + display_path(path)))
    print()


def _slash_skills(q: str) -> str:
    from packing_assistant.runtime.expert_skills import format_catalog_listing

    return format_catalog_listing(q, limit=30)


def _slash_mcp() -> str:
    return (
        "IDE 面：Cursor / VS Code / Grok 挂 MCP，不另装专有插件二进制。\n"
        "  python -m packing_assistant.civil mcp --pack construction\n"
        "  配置样例：ide/cursor/mcp.json  ·  ide/vscode/mcp.json  ·  docs/civil-buddy/mcp-host.example.toml\n"
        "不要把 API Key 写进 MCP 配置。"
    )


def handle_slash(line: str, st: TuiState) -> Optional[str]:
    raw = line.strip()
    if not raw.startswith("/"):
        return None
    parts = raw[1:].split(None, 1)
    cmd = (parts[0] or "").lower()
    arg = parts[1] if len(parts) > 1 else ""
    if cmd in {"help", "h", "?"}:
        return HELP
    if cmd == "quit" or cmd == "exit" or cmd == "q":
        raise SystemExit(0)
    if cmd == "status":
        from packing_assistant.runtime.memory import assemble_context, prompt_prefix

        from packing_assistant.civil import status_text

        ctx = assemble_context(st.thread.session_id)
        src = st.last_skill_source
        how = {"given": "显式", "matched": "规则选用", "model": "模型选用"}.get(src, "未点名")
        skill = f"${st.last_skill}" if st.last_skill else "（未点名）"
        return (
            f"thread   {st.thread.thread_id}\n"
            f"skill    {skill} · {how}\n"
            f"{status_text()}\n"
            f"confirm  {st.confirm}\n"
            f"{prompt_prefix(ctx) or '会话槽空'}"
        )
    if cmd == "init":
        from packing_assistant.runtime.project_instructions import init
        from packing_assistant.runtime.workspace import activate
        from pathlib import Path

        path, created = init(Path.cwd())
        activate(Path.cwd())
        return (f"已写入 {path.name}。填上你确认过的项目事实；留空的栏在成稿里保持 UNSPECIFIED。"
                if created else f"{path.name} 已存在，未改动。")
    if cmd == "model":
        from packing_assistant.llm import llm_config, set_runtime_llm

        current = llm_config()
        if arg.strip():
            set_runtime_llm({"api_key": current["api_key"], "base_url": current["base_url"], "model": arg.strip()})
            current = llm_config()
        key = "已配置" if current.get("api_key") else "未配置（走确定性 steps 路径）"
        return f"model = {current['model']}\nbase  = {current['base_url']}\nkey   = {key}"
    if cmd == "mode":
        from packing_assistant.runtime.civil_config import AGENT_MODES, _strip_mode
        from packing_assistant.runtime.turn import resolve_mode

        if arg.strip():
            picked = _strip_mode(arg, AGENT_MODES, "")
            if not picked:
                return "可选 " + " | ".join(AGENT_MODES)
            os.environ["CIVIL_AGENT_MODE"] = st.cfg.agent_mode = picked
        running, why = resolve_mode()
        return f"mode = {st.cfg.agent_mode}" + (f" → {running}\n{why}" if why else "")
    if cmd == "plan":
        if not st.last_plan:
            return "上一轮没有列步骤（steps 模式不列；model 模式里多步任务才列）。"
        marks = {"done": "x", "in_progress": ">", "pending": " "}
        return "\n".join(f"[{marks.get(row.get('status'), ' ')}] {row.get('step')}" for row in st.last_plan)
    if cmd == "plugins":
        from packing_assistant.civil import plugin_lines

        return "\n".join(plugin_lines()) or "还没有安装插件。civil plugin install <目录或 .zip>"
    if cmd == "review":
        from packing_assistant.runtime.review import review_file

        return review_file(arg)["reply"] if arg.strip() else "用法：/review pm-daily__log.md（文件名或相对作业文件夹的路径）"
    if cmd == "skills":
        return _slash_skills(arg)
    if cmd in {"approvals", "approval"}:
        if arg:
            from packing_assistant.runtime.civil_config import _strip_mode

            st.cfg.approval = _strip_mode(arg, APPROVAL_MODES, st.cfg.approval)
            os.environ["CIVIL_APPROVAL"] = st.cfg.approval
            return f"approval = {st.cfg.approval}"
        return "approval=" + st.cfg.approval + "  可选 " + " | ".join(APPROVAL_MODES)
    if cmd == "sandbox":
        if arg:
            from packing_assistant.runtime.civil_config import _strip_mode

            st.cfg.sandbox = _strip_mode(arg, SANDBOX_MODES, st.cfg.sandbox)
            os.environ["CIVIL_SANDBOX"] = st.cfg.sandbox
            return f"sandbox = {st.cfg.sandbox}"
        return "sandbox=" + st.cfg.sandbox + "  可选 " + " | ".join(SANDBOX_MODES)
    if cmd == "confirm":
        if not st.pending_text or st.pending_thread_id != st.thread.thread_id:
            return "没有当前待签认的任务；确认不能预先授权下一次操作。"
        if not is_confirmation(arg):
            return "请原样输入 /confirm " + CONFIRM + "（或 /confirm " + CONFIRM_EN + "）"
        out = submit_task(st, st.pending_text, confirmed=True)
        return str(out.get("reply") or "本次操作已结束。")
    if cmd == "new":
        from packing_assistant.runtime.threads import new_thread

        st.thread = new_thread(arg or "新对话")
        st.confirm, st.pending_text, st.pending_thread_id = False, "", ""
        return f"thread {st.thread.thread_id} · {st.thread.title}"
    if cmd == "threads":
        from packing_assistant.runtime.threads import list_threads, thread_status

        rows = list_threads()[:20]
        if not rows:
            return "还没有 thread。"
        lines = []
        for th in rows:
            mark = "*" if th.thread_id == st.thread.thread_id else " "
            stt = thread_status(th.thread_id)
            lines.append(f"{mark} {th.thread_id}  {stt.get('state')}  {th.title[:40]}")
        return "\n".join(lines)
    if cmd == "resume":
        from packing_assistant.runtime.threads import load_thread

        th = load_thread(arg.strip())
        if not th:
            return "未知 thread"
        st.thread = th
        st.confirm, st.pending_text, st.pending_thread_id = False, "", ""
        return f"切到 {th.thread_id} · {th.title}"
    if cmd == "files":
        arts = st.thread.artifacts
        if not arts:
            from packing_assistant.runtime.threads import load_thread

            fresh = load_thread(st.thread.thread_id)
            arts = (fresh.artifacts if fresh else []) or []
        return "\n".join(arts) if arts else "本 thread 还没有交付物。"
    if cmd == "mcp":
        return _slash_mcp()
    if cmd == "bg":
        if not arg.strip():
            return "用法：/bg 出一份税务日历"
        from packing_assistant.runtime.threads import spawn

        st.confirm, st.pending_text, st.pending_thread_id = False, "", ""
        got = spawn(arg.strip(), confirm=confirms_in_message(arg), title=arg.strip()[:40])
        return f"后台 thread {got.get('thread_id')}  state={got.get('state')}"
    return f"未知命令 /{cmd}。/help"


def ask_approval(request: Dict[str, Any], *, read=input) -> bool:
    """Codex asks before a risky command runs; civil asks before a high-risk post writes."""
    print(_c("33", f"approval {request.get('name') or ''}（risk={request.get('risk')}）要写盘。"))
    print(_c("33", f"  同意就原样输入确认句：{CONFIRM}"))
    print(_c("33", f"  To approve, type exactly: {CONFIRM_EN}"))
    try:
        answer = read(_c("33", "  approve> ")).strip()
    except (EOFError, KeyboardInterrupt):
        return False
    return is_confirmation(answer)       # the approve> prompt asks for the sentence alone: exactly one of the two


def submit_task(st: TuiState, text: str, *, confirmed: bool = False, approve=None, on_progress=None) -> Dict[str, Any]:
    """Execute one current operation; an inline approval retries only these same words and is then consumed."""
    from packing_assistant.civil import with_progress
    from packing_assistant.runtime.threads import load_thread, run_on_thread

    st.confirm, st.pending_text, st.pending_thread_id = False, "", ""
    granted: List[bool] = []
    progress = on_progress or (lambda _text: None)

    def ask(request: Dict[str, Any]) -> bool:
        granted.append(bool(approve(request)) if approve is not None else False)
        return granted[-1]

    def turn(allow: bool) -> Dict[str, Any]:
        return run_on_thread(st.thread.thread_id, text, confirm=allow, approve=ask)

    out = with_progress(lambda: turn(confirmed is True or confirms_in_message(text)), progress)
    if out.get("hitl_pending") and not granted and ask(
            {"name": out.get("expert_name") or "本次写盘", "risk": "high"}):
        out = with_progress(lambda: turn(True), progress)
    fresh = load_thread(st.thread.thread_id)
    if fresh:
        st.thread = fresh
    st.last_skill = str(out.get("skill") or out.get("expert_id") or "")
    st.last_skill_source = str(out.get("skill_source") or "")
    st.last_plan = list(out.get("plan") or [])
    if out.get("hitl_pending"):
        st.pending_text, st.pending_thread_id = text, st.thread.thread_id
    return out


def run_tui() -> int:

    _enable_vt()
    st = TuiState()
    _banner(st)
    while True:
        try:
            line = input(_c("32", f"{st.thread.thread_id}> ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            return 0
        if line.startswith("/"):
            try:
                msg = handle_slash(line, st)
            except SystemExit:
                return 0
            if msg:
                print(msg)
                print()
            continue
        out = submit_task(st, line, approve=ask_approval, on_progress=lambda text: print(_c("2", text)))
        _print_out(out)
    return 0
