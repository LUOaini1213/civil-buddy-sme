#!/usr/bin/env python3
"""Civil Buddy · 土木版 Codex CLI.

  civil                       TUI，在当前文件夹里工作
  civil "任务"                一次性 exec
  civil exec "任务" | -       非交互执行；- 从标准输入读任务
  civil exec --jsonl "任务"   逐行 JSON 事件流（thread.started → turn.started → item.* → turn.completed）
  civil --mode model "任务"   模型驱动：模型自己选岗位、读资料、调工具（默认 steps：规则路由，不调模型）
  civil -C <文件夹> ...       把该文件夹当作业文件夹（同 cd 进去再运行）
  civil init                  在当前文件夹写一份 CIVIL.md（本工程说明，相当于 Codex 的 AGENTS.md）
  civil status                作业文件夹、工程说明、sandbox / approval、模型
  civil review <文稿>         不调模型：文稿里的数字在工地资料里有没有出处、有没有不该下的结论
  civil sandbox               系统级沙箱：本机内核能限制什么，并起一个受限进程当场自检
  civil plugin list | validate <目录> | install <目录或.zip> [--job] | remove <名> | trust <名> | untrust <名>
                              插件：自己公司的岗位（SOP + 表单模板 + 知识），纯声明、不含代码
  civil --sandbox-backend os  工具在被内核限制的进程里跑（只能写 .civil-buddy/out，不能起进程；Linux 上也不能联网）
  civil app                   打开工作台应用（浏览器里的网页工作台）
  civil desktop               原生桌面窗口：打开作业文件夹、交代任务、看步骤、当场审批、打开成稿、复核（Tk，无需安装任何东西）
  civil mcp --pack bid        IDE stdio MCP
  civil serve                 JSON-RPC app-server（土木 harness，不是官方 Codex 二进制）
  civil skills
  civil resume <thread|--last> [任务]
  civil help
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from packing_assistant.runtime.civil_config import CONFIRM, CONFIRM_EN  # noqa: E402,F401  (one definition)
VERBS = ("tui", "exec", "app", "mcp", "serve", "skills", "resume", "help", "init", "status", "review", "sandbox", "plugin", "desktop")


def run_task(
    text: str,
    *,
    skill: str = "",
    confirm: bool = False,
    session_id: str = "",
    thread_id: str = "",
    background: bool = False,
):
    if thread_id or background:
        from packing_assistant.runtime.threads import new_thread, run_on_thread, spawn

        if background and not thread_id:
            return spawn(text, skill=skill, confirm=confirm, title=text[:40])
        tid = thread_id or new_thread(text[:40], confirm=confirm).thread_id
        return run_on_thread(tid, text, skill=skill, confirm=confirm, background=background)
    from packing_assistant.runtime.turn import run_turn

    return run_turn(text, session_id=session_id or "civil-cli", skill=skill, confirm=confirm)


def list_skills():
    from packing_assistant.runtime.expert_skills import catalog

    return catalog()


def enter_workspace(folder: str = "") -> Optional[Path]:
    """-C names the job folder; otherwise the nearest CIVIL.md at or above cwd decides."""
    from packing_assistant.runtime.workspace import activate, find_job_root

    if folder:
        job = Path(folder).expanduser()
        if not job.is_dir():
            raise NotADirectoryError(f"-C 指向的不是文件夹：{folder}")
        os.chdir(job)
        return activate(job)
    job = find_job_root()
    return activate(job) if job else None


def display_path(path: str) -> str:
    """Relative to the job folder when the file is inside it, the way Codex prints repo paths."""
    from packing_assistant.runtime.workspace import active

    job = active()
    if job:
        try:
            return str(Path(path).resolve().relative_to(job))
        except (OSError, ValueError):
            pass
    return str(path)


def _file_paths(out: Dict[str, Any]) -> List[str]:
    paths: List[str] = []
    for item in out.get("files") or out.get("artifacts") or []:
        path = str(item.get("path") or "") if isinstance(item, dict) else str(item)
        if path and path not in paths:
            paths.append(path)
    return paths


def _print_out(out: dict, *, as_json: bool) -> int:
    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return 0 if out.get("ok") else 1
    if out.get("background"):
        print(f"thread {out.get('thread_id')} 后台 {out.get('state')}", file=sys.stderr)
        return 0
    eid = out.get("skill") or out.get("expert_id") or ""
    src = out.get("skill_source") or ""
    if eid:
        how = {"given": "显式", "model": "模型选用"}.get(src, "选用")
        print(f"skill ${eid} · {out.get('expert_name') or eid} · {how}", file=sys.stderr)
    else:
        print("skill （未选用）" if out.get("agent_mode") == "model" else "skill （未选用，路由器）", file=sys.stderr)
    kernel = (out.get("sandbox_backend") or {}).get("backend") or "app"
    print(
        f"mode {out.get('agent_mode') or 'steps'} · intent {out.get('intent')} · wrote {out.get('wrote')} · "
        f"submit_blocked {out.get('submit_blocked')} · "
        f"sandbox {out.get('sandbox_mode')}" + (f" + {kernel}" if kernel != "app" else "") + f" · approval {out.get('approval')}",
        file=sys.stderr,
    )
    if out.get("hitl_pending"):
        print(f"approval 高风险写盘须确认句：{CONFIRM}  |  sign-off sentence: {CONFIRM_EN}", file=sys.stderr)
    if out.get("thread_id"):
        print(f"thread {out.get('thread_id')}", file=sys.stderr)
    print(out.get("reply") or "")
    for path in _file_paths(out):
        print(f"  + {display_path(path)}", file=sys.stderr)
    return 0 if out.get("ok", True) else 1


def codex_event(event: Any) -> Optional[Dict[str, Any]]:
    """One bus event as one line of the --jsonl stream. run_ended is folded into turn.completed."""
    payload = dict(getattr(event, "payload", None) or {})
    kind = getattr(event, "type", "")
    base = {"run_id": getattr(event, "run_id", ""), "ts": round(float(getattr(event, "ts", 0.0)), 3)}
    if kind == "run_started":
        return {"type": "turn.started", **base, "intent": payload.get("intent"), "skill": payload.get("expert_id") or ""}
    if kind == "tool_call":
        item = {"type": "tool_call", "name": payload.get("name")}
        if payload.get("arguments"):
            item["arguments"] = payload["arguments"]
        return {"type": "item.started", **base, "item": item}
    if kind == "tool_result":
        return {"type": "item.completed", **base, "item": {"type": "tool_call", **payload}}
    if kind == "hitl":
        return {"type": "approval.required", **base, "confirm_sentence": CONFIRM, "confirm_sentence_en": CONFIRM_EN, **payload}
    if kind == "cancelled":
        return {"type": "turn.cancelled", **base, **payload}
    if kind == "plan":
        return {"type": "plan.updated", **base, **payload}
    if kind == "skill_loaded":
        return {"type": "item.completed", **base, "item": {"type": "skill", **payload}}
    if kind == "message":
        return {"type": "item.completed", **base, "item": {"type": "agent_message", **payload}}
    if kind == "guard":
        return {"type": "guard.flagged", **base, **payload}
    if kind == "deadlock":
        return {"type": "turn.failed", **base, "error_code": "deadlock", **payload}
    return None


def progress_line(event: Any) -> str:
    """One bus event as one short human line (exec prints these to stderr, the TUI inline)."""
    payload = dict(getattr(event, "payload", None) or {})
    kind = getattr(event, "type", "")
    if kind == "plan":
        marks = {"done": "x", "in_progress": ">", "pending": " "}
        return "\n".join(f"  [{marks.get(row.get('status'), ' ')}] {row.get('step')}" for row in payload.get("steps") or [])
    if kind == "skill_loaded":
        return f"  skill ${payload.get('id')} · {payload.get('name')}"
    if kind == "tool_call":
        arguments = payload.get("arguments") or {}
        brief = " ".join(str(value) for value in arguments.values() if isinstance(value, (str, int, float)))[:60]
        return f"  -> {payload.get('name')}" + (f" {brief}" if brief else "")
    if kind == "tool_result" and not payload.get("ok", True):
        return f"  !! {payload.get('name')}: {payload.get('error_code')}"
    if kind == "hitl":
        return f"  approval 高风险写盘须确认句：{CONFIRM}  |  sign-off sentence: {CONFIRM_EN}"
    if kind == "guard":
        parts = [label + "、".join(payload.get(key) or []) for key, label in (("untraced", "无出处的数字："), ("verdicts", "不该下的结论："))
                 if payload.get(key)]
        return "  guard " + "；".join(parts) if parts else ""
    if kind == "deadlock":
        return f"  deadlock {payload.get('reason') or payload.get('code') or ''}".rstrip()
    return ""


def with_progress(run, write) -> Dict[str, Any]:
    """Run a turn while ``write(line)`` receives each progress line as it happens."""
    from packing_assistant.runtime.bus import get_bus

    def forward(event: Any) -> None:
        line = progress_line(event)
        if line:
            write(line)

    unsubscribe = get_bus().subscribe(forward)
    try:
        return run()
    finally:
        unsubscribe()


def _emit(line: Dict[str, Any]) -> None:
    print(json.dumps(line, ensure_ascii=False, default=str), flush=True)


def run_jsonl(run, *, thread_id: str = "", last_message_file: str = "") -> int:
    """Run one turn while printing every event as a JSON line, as ``codex exec --json`` does."""
    from packing_assistant.runtime.bus import get_bus
    from packing_assistant.runtime.workspace import active

    _emit({"type": "thread.started", "thread_id": thread_id, "job_root": str(active() or "")})

    root: List[str] = []

    def forward(event: Any) -> None:
        line = codex_event(event)
        if not line:
            return
        # 模型驱动的一轮里，run_skill 会在内部再跑一遍确定性流程（它有自己的 run_id）。
        # 对读事件流的人来说那是这一轮里的一步，不是又一轮：不再报 turn.started，其余标 nested。
        if not root:
            root.append(line["run_id"])
        elif line["run_id"] != root[0]:
            if line["type"] == "turn.started":
                return
            line["nested"] = True
        _emit(line)

    unsubscribe = get_bus().subscribe(forward)
    try:
        out = run()
    except Exception as exc:  # noqa: BLE001 - the stream must end with a terminal event
        _emit({"type": "turn.failed", "error": f"{type(exc).__name__}: {exc}"})
        return 1
    finally:
        unsubscribe()
    done = {
        "type": "turn.completed" if out.get("ok", True) else "turn.failed",
        "ok": bool(out.get("ok", True)),
        "thread_id": out.get("thread_id") or thread_id,
        "skill": out.get("skill") or "",
        "agent_mode": out.get("agent_mode") or "",
        "intent": out.get("intent"),
        "wrote": bool(out.get("wrote")),
        "hitl_pending": bool(out.get("hitl_pending")),
        "submit_blocked": True,
        "error_code": out.get("error_code") or "",
        "files": [display_path(p) for p in _file_paths(out)],
        "reply": out.get("reply") or "",
    }
    for key in ("provenance", "plan", "usage", "mode_notice"):
        if out.get(key):
            done[key] = out[key]
    if last_message_file:
        Path(last_message_file).write_text(done["reply"], encoding="utf-8")
    _emit(done)
    return 0 if done["ok"] else 1


def cmd_app(port: Optional[int], *, no_browser: bool = False) -> int:
    from packing_assistant.runtime.launcher import run_workbench

    return run_workbench(port, no_browser=no_browser)


def cmd_mcp(pack: str, expert: str) -> int:
    demo = Path(__file__).resolve().parents[1] / "demo" / "mcp_stdio.py"
    args = [sys.executable, str(demo)]
    if expert:
        args.extend(["--expert", expert])
    elif pack:
        args.extend(["--pack", pack])
    else:
        args.extend(["--pack", "construction"])
    return int(subprocess.call(args))


def cmd_init() -> int:
    from packing_assistant.runtime.project_instructions import init
    from packing_assistant.runtime.workspace import activate

    path, created = init(Path.cwd())
    activate(Path.cwd())
    if created:
        print(f"已写入 {path.name}。填上项目、辖区等你确认过的事实；留空的栏在成稿里保持 UNSPECIFIED。")
    else:
        print(f"{path.name} 已存在，未改动。")
    return 0


def status_text() -> str:
    from packing_assistant.llm import llm_config
    from packing_assistant.runtime.civil_config import load_config
    from packing_assistant.runtime.workspace import describe

    from packing_assistant.runtime.turn import resolve_mode

    cfg, space, llm = load_config(), describe(), llm_config()
    running, why = resolve_mode()
    lines = [
        f"job      {space['job_root'] or '（未进入作业文件夹；civil init 或 civil -C <文件夹>）'}",
        f"state    {space['state_root'] or '仓库 demo/out'}",
        "CIVIL.md " + (", ".join(display_path(p) for p in space["instruction_files"]) or "（无）"),
    ]
    if space["slots"]:
        lines.append("slots    " + " · ".join(f"{k}={v}" for k, v in space["slots"].items()))
    lines += [
        f"sandbox  {cfg.sandbox}",
        sandbox_text()[0],
        f"approval {cfg.approval}",
        f"mode     {cfg.agent_mode}" + (f" → {running}" if running != cfg.agent_mode else "")
        + "  （steps 规则路由不调模型 · model 模型驱动 · auto 有模型就用；--mode / /mode / CIVIL_AGENT_MODE）",
        f"model    {llm['model']} @ {llm['base_url']}" + ("" if llm.get("api_key") else "  （未配置 Key）"),
    ]
    if why:
        lines.append("         " + why)
    lines += ["plugin   " + line for line in plugin_lines()]
    return "\n".join(lines)


_MARK = {True: "由内核限制", False: "未由内核限制"}


def sandbox_text(*, live: bool = False) -> tuple[str, int]:
    """What `civil sandbox` prints, and its exit code: 0 only when a confined worker proved itself."""
    from packing_assistant.runtime import os_sandbox
    from packing_assistant.runtime.workspace import active

    found = os_sandbox.describe()
    kernel = found["kernel"]
    lines = [f"backend  {found['asked']} → {found['running']}" + (f"  （{found['why']}）" if found["why"] else ""),
             f"kernel   {kernel['backend']}" + ("" if kernel["available"] else "  （不可用）"),
             "         " + " · ".join(f"{label} {_MARK[bool(kernel['enforces'][key])]}" for key, label in
                                      (("write", "写入"), ("spawn", "起进程"), ("network", "网络")))]
    if kernel.get("reason"):
        lines.append("         " + kernel["reason"])
    if not live:
        return "\n".join(lines), 0
    job = active()
    if job is None or not (kernel["available"] and kernel["enforces"]["write"]):
        lines.append("selftest 未运行：" + ("需要作业文件夹（civil init 或 -C）" if job is None else "本机内核无法限制写入"))
        return "\n".join(lines), 1
    try:
        with os_sandbox.Worker(job) as worker:
            checks = worker.call("selftest")["out"]
    except (os_sandbox.WorkerError, OSError) as exc:
        lines.append("selftest 工作进程没有进入受限状态：" + str(exc))
        return "\n".join(lines), 1
    wording = {"write_inside_state": "写 .civil-buddy/out", "write_job_folder": "写作业文件夹本身", "write_home": "写用户目录",
               "write_system_temp": "写系统临时目录", "spawn_process": "起子进程", "inet_socket": "建 inet 套接字"}
    lines.append("selftest（受限进程里当场试，答案来自操作系统）")
    lines += [f"         {wording[key]:<18} {value}" for key, value in checks.items()]
    if not kernel["enforces"]["network"]:
        lines.append("         （本平台的网络拒绝只在应用层，不是内核）")
    held = checks["write_inside_state"] == "allowed" and all(checks[k] == "denied" for k in ("write_job_folder", "write_home", "write_system_temp"))
    return "\n".join(lines), 0 if held else 1


def plugin_lines() -> List[str]:
    from packing_assistant.runtime import plugins

    lines = []
    for plugin in plugins.installed():
        trust = "已信任" if plugin.trusted else "未信任：岗位一律按高风险，写盘要确认句"
        lines.append(f"{plugin.name} {plugin.manifest.get('version') or ''} · {plugin.scope} · {trust} · "
                     + "、".join(f"${s.id}" for s in plugin.skills))
        lines += ["    ! " + warning for warning in plugin.warnings]
    lines += ["  x " + problem for problem in plugins.problems()]
    return lines


def cmd_plugin(rest: List[str], *, job_scope: bool) -> int:
    from packing_assistant.runtime import plugins

    action, target = (rest[0] if rest else "list"), (" ".join(rest[1:]).strip() if len(rest) > 1 else "")
    try:
        if action == "list":
            print("\n".join(plugin_lines()) or "还没有安装插件。civil plugin install <目录或 .zip>")
            return 0
        if not target:
            print("civil plugin " + action + " <名称或路径>", file=sys.stderr)
            return 2
        if action == "validate":
            plugin = plugins.read_plugin(Path(target))
            print(f"{plugin.name}：{len(plugin.skills)} 个技能，格式有效。" + "".join("\n  ! " + w for w in plugin.warnings))
            return 0
        if action == "install":
            plugin = plugins.install(Path(target), scope="job" if job_scope else "user")
            print(f"已安装 {plugin.name}（{plugin.scope}）：" + "、".join(f"${s.id}" for s in plugin.skills)
                  + "\n未信任：它的岗位一律按高风险处理。看过内容后可 civil plugin trust " + plugin.name
                  + "".join("\n  ! " + w for w in plugin.warnings))
            return 0
        if action == "remove":
            print("已移除 " + target if plugins.remove(target) else "没有安装 " + target)
            return 0
        if action in {"trust", "untrust"}:
            plugin = plugins.set_trust(target, action == "trust")
            print(f"{plugin.name}：" + ("已信任，按它自己标的风险级别执行（改动插件内容后信任自动失效）。" if plugin.trusted else "已取消信任。"))
            return 0
    except plugins.PluginError as exc:
        print("插件无效：" + str(exc), file=sys.stderr)
        return 1
    print("civil plugin list | validate | install | remove | trust | untrust", file=sys.stderr)
    return 2


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--cd", "-C", default="", metavar="DIR", help="作业文件夹（默认：当前目录向上最近的 CIVIL.md）")
    p.add_argument("--skill", "-s", default="", help="强制 skill id")
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--session", default="")
    p.add_argument("--thread", default="")
    p.add_argument("--last", action="store_true", help="resume：接着最近一个 thread")
    p.add_argument("--bg", action="store_true")
    p.add_argument("--job", action="store_true", help="plugin install：装进当前作业文件夹，随文件夹走")
    p.add_argument("--launcher", default="", metavar="DIR", help="desktop：在 DIR 里写一个双击即开的 Civil Buddy.pyw，不打开窗口")
    p.add_argument("--json", action="store_true", help="结束后打印完整结果（一个 JSON）")
    p.add_argument("--jsonl", action="store_true", help="逐行 JSON 事件流")
    p.add_argument("--output-last-message", "-o", default="", metavar="FILE", help="把最终回复另存到文件")
    p.add_argument("--mode", default="", help="steps | model | auto")
    p.add_argument("--sandbox-backend", default="", help="app | os | auto")
    p.add_argument("--sandbox", default="", help="read-only | workspace-write")
    p.add_argument("--approval", default="", help="untrusted | on-request | never")
    p.add_argument("--list-skills", action="store_true")
    p.add_argument("--port", type=int, default=None, help="工作台端口，默认 CIVIL_PORT 或 8765")
    p.add_argument("--no-browser", action="store_true", help="启动工作台后不自动打开浏览器")
    p.add_argument("--pack", default="")
    p.add_argument("--expert", default="")
    p.add_argument("rest", nargs="*", help="任务或 resume 的 thread id")


def _stderr(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


def _finish(out: Dict[str, Any], args: argparse.Namespace) -> int:
    if args.output_last_message:
        Path(args.output_last_message).write_text(str(out.get("reply") or ""), encoding="utf-8")
    return _print_out(out, as_json=args.json)


_VALUE_OPTIONS = frozenset({"--cd", "-C", "--skill", "-s", "--session", "--thread", "--output-last-message", "-o",
                            "--sandbox", "--approval", "--port", "--pack", "--expert", "--mode", "--sandbox-backend", "--launcher"})


def split_verb(argv: List[str]) -> tuple[str, List[str]]:
    """The verb is the first word that is not an option or an option's value."""
    skip = False
    for index, token in enumerate(argv):
        if skip:
            skip = False
            continue
        if token.startswith("-") and token != "-":
            skip = token in _VALUE_OPTIONS
            continue
        if token in VERBS:
            return token, argv[:index] + argv[index + 1:]
        break
    return "", argv


def main(argv: Optional[List[str]] = None) -> int:
    verb, argv = split_verb(list(sys.argv[1:] if argv is None else argv))
    p = argparse.ArgumentParser(prog="civil", add_help=True)
    _common(p)
    args = p.parse_intermixed_args(argv)      # `civil plugin install --job <dir>`: options may sit between the words
    if args.sandbox:
        os.environ["CIVIL_SANDBOX"] = args.sandbox
    if args.approval:
        os.environ["CIVIL_APPROVAL"] = args.approval
    if args.mode:
        os.environ["CIVIL_AGENT_MODE"] = args.mode
    if args.sandbox_backend:
        os.environ["CIVIL_SANDBOX_BACKEND"] = args.sandbox_backend
    try:
        enter_workspace(args.cd)
    except (OSError, PermissionError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    rest: List[str] = list(args.rest or [])
    if args.list_skills or verb == "skills":
        rows = list_skills()
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            from packing_assistant.runtime.expert_skills import format_catalog_listing

            print(format_catalog_listing())
        return 0
    if verb == "help":
        from packing_assistant.civil_tui import HELP

        print(__doc__.strip() + "\n\nTUI 内：\n" + HELP)
        return 0
    if verb == "init":
        return cmd_init()
    if verb == "status":
        print(status_text())
        return 0
    if verb == "sandbox":
        text, code = sandbox_text(live=True)
        print(text)
        return code
    if verb == "plugin":
        return cmd_plugin(rest, job_scope=args.job)
    if verb == "desktop":
        from packing_assistant.desktop.app import main as desktop_main, write_launcher
        from packing_assistant.runtime.workspace import active

        if args.launcher:
            print(f"已写入 {write_launcher(args.launcher, str(active() or ''))}：双击即开，不弹控制台。")
            return 0
        return desktop_main([str(active())] if active() else rest[:1])
    if verb == "review":
        from packing_assistant.runtime.review import review_file

        if not rest:
            print("civil review <文稿>    （文件名或相对作业文件夹的路径）", file=sys.stderr)
            return 2
        found = review_file(" ".join(rest))
        print(json.dumps(found, ensure_ascii=False, indent=2, default=str) if args.json else found["reply"])
        return 0 if found.get("ok") and found.get("clean") else 1 if found.get("ok") else 2
    if verb == "app":
        return cmd_app(args.port, no_browser=args.no_browser)
    if verb == "mcp":
        return cmd_mcp(args.pack or "construction", args.expert)
    if verb == "serve":
        from packing_assistant.runtime.app_server import serve_stdio

        print("civil-app-server/v1 stdio · 不是 openai/codex", file=sys.stderr)
        return serve_stdio()
    if verb == "tui":
        from packing_assistant.civil_tui import run_tui

        return run_tui()
    if verb == "resume":
        from packing_assistant.runtime.threads import list_threads, run_on_thread

        if args.last:
            recent = list_threads()
            if not recent:
                print("还没有 thread 可以接着做。", file=sys.stderr)
                return 2
            tid, task = recent[0].thread_id, " ".join(rest).strip() or "继续"
        elif rest:
            tid, task = rest[0], " ".join(rest[1:]).strip() or "继续"
        else:
            print("civil resume <thread_id> [任务]  或  civil resume --last [任务]", file=sys.stderr)
            return 2
        def resumed() -> Dict[str, Any]:
            return run_on_thread(tid, task, skill=args.skill, confirm=args.confirm)

        if args.jsonl:
            return run_jsonl(resumed, thread_id=tid, last_message_file=args.output_last_message)
        return _finish(resumed() if args.json else with_progress(resumed, _stderr), args)

    text = " ".join(rest).strip()
    if text == "-":
        text = sys.stdin.read().strip()
    if verb == "exec" and not text:
        print("civil exec 需要任务文本（或用 - 从标准输入读）", file=sys.stderr)
        return 2
    if not text and verb in {"", "tui"}:
        from packing_assistant.civil_tui import run_tui

        return run_tui()
    if not text:
        print("需要任务文本，或直接运行 civil 进入 TUI", file=sys.stderr)
        return 2

    def turn() -> Dict[str, Any]:
        return run_task(
            text,
            skill=args.skill,
            confirm=args.confirm,
            session_id=args.session or args.thread or "civil-cli",
            thread_id=args.thread,
            background=args.bg,
        )

    if args.jsonl:
        return run_jsonl(turn, thread_id=args.thread, last_message_file=args.output_last_message)
    return _finish(turn() if args.json else with_progress(turn, _stderr), args)


if __name__ == "__main__":
    raise SystemExit(main())
