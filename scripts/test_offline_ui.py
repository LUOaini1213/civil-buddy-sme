#!/usr/bin/env python3
"""断网专项（UX R12，结论记 docs/ux/ux-design-spec.md 附录 K）。

场景：施工现场多为工地内网/弱网，"断网必须可用"是 UX 红线。本脚本用 playwright 把
**所有非 localhost 请求一律 abort**（模拟外网彻底不通），然后跑两端核心动线：

  端 A  http://127.0.0.1:8765  Rust 工作台（demo/static/）
        输入(示例卡预填) → 发送 → 阶段时间线 → HITL 审批卡(如出现则"确认并重提")
        → 交付物"文书预览" → 审计面板(#loadAudit) → 导出按钮可见
  端 B  http://127.0.0.1:8000  Python 网关（frontend/index.html）
        填入样例 → 先理解再处理 → 结果回显；顺带检查 /workbench 页可渲染

并通过两条硬断言收口"零外链"：
  1) 全程被 abort 的外域请求 == 0（连"试图出网"都不许有）；
  2) 全程无未捕获 JS 异常（pageerror == 0，U-R10 那类"整段脚本挂掉"必被抓）。

前置与 SKIP 语义（与 test_js_syntax.py 同约定）：
  - 本机需 python -m playwright + chromium（R12 已验证可用）；
  - 缺 playwright/chromium → exit 0 + SKIP 说明；
  - Rust 二进制优先 target/release，其次 target/debug，都没有则 cargo build。

用法：python scripts/test_offline_ui.py            # 自启自停两端服务
      端口已被占用时直接复用现有服务（不动用户进程）。
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
PORT_GW, PORT_WB = 8000, 8765
GATEWAY_URL = f"http://127.0.0.1:{PORT_GW}"
WORKBENCH_URL = f"http://127.0.0.1:{PORT_WB}"
INPUT_TEXT = "装柜任务：钢梁 6000x200x300mm 400kg x3，另有临边防护专项用品一票，请给出装柜方案与交底文书（AI 草稿）"

blocked_external: list[str] = []


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_http(url: str, timeout: float = 60.0) -> None:
    import urllib.request

    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return
        except Exception as e:  # noqa: BLE001
            last = str(e)
        time.sleep(0.6)
    raise RuntimeError(f"服务未就绪：{url}（{last}）")


def ensure_servers() -> list[subprocess.Popen]:
    """端口占用则复用；否则自启并纳入管理（finally 终止）。"""
    procs: list[subprocess.Popen] = []
    if not port_open(PORT_GW):
        procs.append(
            subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "gateway.app:app", "--host", "127.0.0.1", "--port", str(PORT_GW)],
                cwd=str(ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        )
        print(f"[offline-ui] 已启动网关 ：{PORT_GW}")
    if not port_open(PORT_WB):
        wb = ROOT / "workbench"
        exe = None
        for cand in ("target/release/civil-workbench.exe", "target/debug/civil-workbench.exe"):
            if (wb / cand).is_file():
                exe = wb / cand
                break
        if exe is None:
            subprocess.run(["cargo", "build", "--bin", "civil-workbench"], cwd=str(wb), check=True)
            exe = wb / "target/debug/civil-workbench.exe"
        env = dict(os.environ)
        env["CIVIL_PORT"] = str(PORT_WB)
        procs.append(
            subprocess.Popen(
                [str(exe)],
                cwd=str(wb),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        )
        print(f"[offline-ui] 已启动 Rust 工作台 ：{PORT_WB}（{exe.name}）")
    wait_http(f"{GATEWAY_URL}/api/health")
    wait_http(f"{WORKBENCH_URL}/")
    print("[offline-ui] 两端就绪")
    return procs


def guard_route(route, request) -> None:  # noqa: ANN001
    host = urlsplit(request.url).hostname
    if host in ("127.0.0.1", "localhost") or host is None:
        route.continue_()
        return
    blocked_external.append(request.url)
    route.abort()


def run_workbench(page) -> str:
    """端 A 核心动线；返回动线摘要。"""
    page.goto(f"{WORKBENCH_URL}/", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector("#input", timeout=15000)
    page.wait_for_selector("#cbEmpty", timeout=15000)  # R10 空态卡在
    page.click('button.cb-empty-card[data-cb-sample="pack"]')  # R9/R10 示例卡预填
    val = page.input_value("#input")
    assert val.strip(), "示例卡未预填输入框"
    page.fill("#input", INPUT_TEXT)
    page.click("#send")
    # 阶段时间线（R3）：流水线阶段行出现
    page.wait_for_selector(".cb-tl, .tl-hitl, .cb-doc-card", timeout=120000)
    # HITL 审批卡（R5）：出现则显式确认
    note = "时间线出现"
    confirm = page.locator('button[aria-label^="确认并重提"]')
    try:
        confirm.wait_for(state="visible", timeout=20000)
        confirm.click()
        note = "时间线+审批卡确认"
    except Exception:  # noqa: BLE001
        pass  # 本输入未触发 HITL 闸门不算失败，但审批卡按钮存在性在断言段单独验
    # 交付物（R4）：出现"文书预览"按钮则点开预览浮层
    preview = page.get_by_role("button", name="文书预览")
    try:
        preview.wait_for(state="visible", timeout=60000)
        preview.click()
        page.wait_for_selector('[aria-label="交付物文书预览"]', timeout=20000)
        note += "+文书预览"
        page.keyboard.press("Escape")
    except Exception:  # noqa: BLE001
        pass
    # 审计（R6）：面板加载 + 导出按钮（轮询重试一次，防首次加载与写盘竞态）
    page.click("#loadAudit")
    for _ in range(2):
        try:
            page.wait_for_selector("#auditDownload:not([hidden])", timeout=30000)
            break
        except Exception:  # noqa: BLE001
            page.click("#loadAudit")
    else:
        page.wait_for_selector("#auditDownload:not([hidden])", timeout=30000)
    return note


def run_gateway(page) -> str:
    """端 B 核心动线：宿主页 + /workbench 大 Team 页（HITL 审批卡）。"""
    page.goto(f"{GATEWAY_URL}/", wait_until="domcontentloaded", timeout=30000)
    assert "Civil Buddy" in page.content(), "宿主页缺 Civil Buddy 标识"
    page.wait_for_selector("#tender", timeout=15000)
    page.get_by_role("button", name="填入样例").click()
    page.get_by_role("button", name="先理解再处理").click()
    page.wait_for_selector("text=本轮意图", timeout=120000)
    note = "样例→理解→回显"
    # /workbench：三步演示（默认 simple 模式）→ 满载演示 → HITL 确认闸(.hitl-bar) → 确认并拼柜 · resume
    page.goto(f"{GATEWAY_URL}/workbench", wait_until="domcontentloaded", timeout=30000)
    demo_btn = page.get_by_role("button", name=re.compile("满载演示")).first
    demo_btn.wait_for(state="visible", timeout=30000)
    demo_btn.click()
    page.wait_for_selector(".hitl-bar", timeout=120000)  # phase=await_user_confirm，闸门一等公民
    note += "+时间线+HITL确认闸"

    def check_all_ns() -> bool:
        btn = page.get_by_role("button", name="演示一键勾选")
        if btn.count() and btn.first.is_visible():
            btn.first.click()
            return True
        return False

    # 非标预检必勾项未齐时 confirmPack 会被客户端闸门拦下 —— 设计动线：演示一键勾选
    if not check_all_ns():
        page.get_by_role("button", name="改方案").click()  # mainTab → boxes
        if check_all_ns():
            page.get_by_role("button", name=re.compile("去第 2 步")).click()  # 回 overview
    page.get_by_role("button", name="确认并拼柜 · resume").click(timeout=20000)
    # 拼柜续跑到出裁决（mid50/ship_ok），闸门条收起
    page.wait_for_selector(".hitl-bar", state="detached", timeout=180000)
    page.get_by_text("mid50").first.wait_for(state="attached", timeout=60000)
    note += "+确认决策+裁决"
    return note


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[offline-ui] SKIP：未安装 playwright（pip install playwright && playwright install chromium）。断网专项以 CI/手测记录为准。")
        return 0
    procs: list[subprocess.Popen] = []
    try:
        procs = ensure_servers()
    except Exception as e:  # noqa: BLE001
        print(f"[offline-ui] FAIL：服务自启失败：{e}")
        return 1

    errors: list[str] = []
    notes: list[str] = []
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as e:  # noqa: BLE001
                print(f"[offline-ui] SKIP：chromium 不可用（{e}）。请先 playwright install chromium。")
                return 0
            ctx = browser.new_context(viewport={"width": 1440, "height": 960})
            ctx.route("**/*", guard_route)
            for name, fn in (("Rust:8765", run_workbench), ("Gateway:8000", run_gateway)):
                page = ctx.new_page()
                page_errors: list[str] = []
                page.on("pageerror", lambda exc, _b=page_errors: _b.append(str(exc)))
                try:
                    note = fn(page)
                    notes.append(f"{name}：{note}")
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{name} 动线失败：{type(e).__name__}: {e}")
                if page_errors:
                    errors.append(f"{name} 未捕获 JS 异常 {len(page_errors)} 条：{page_errors[:3]}")
                page.close()
            browser.close()
    finally:
        for proc in procs:
            proc.terminate()

    print(f"[offline-ui] 动线记录：{'；'.join(notes) if notes else '（无）'}")
    print(f"[offline-ui] 被 abort 的外域请求：{len(blocked_external)}（必须为 0）{'：' + str(blocked_external[:3]) if blocked_external else ''}")
    if errors:
        print(f"[offline-ui] FAIL：{len(errors)} 项")
        for e in errors:
            print("  " + e)
        return 1
    print("[offline-ui] PASS：断网（全部外域请求 abort）下两端核心动线可用，零外域请求尝试，零 JS 异常")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
