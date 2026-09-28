#!/usr/bin/env python3
"""零外链断言（UX R12 收口，固化附录 A/K 红线）：施工现场多为工地内网/弱网，
界面必须断网可用 —— 浏览器可加载的任何资产里不允许出现 http(s):// 外链引用。

扫描范围（浏览器真正会解析/加载的资产，缺一不可）：
  frontend/  demo/static/  workbench/src/  下的 *.html *.css *.js *.webmanifest *.svg
  - workbench/src 目前没有前端资产（include_str! 只内联 contract/intents.v1.json、
    seed.json、yibiao-map.json 三份 JSON，见 workbench/src/agent.rs/catalog.rs/tier_map.rs）；
    纳入扫描是防将来有人在 rs 旁落 UI 文件时漏网。
  - .rs 不在扫描范围：Rust 侧的 https://（config.rs 的 LLM 端点、websearch.rs 的检索、
    eval_live.rs 的法规原文链接）都是服务器侧行为，浏览器从不加载，断网红线只约束界面。

判定流程（两步，避免把"注释里的文档 URL"误杀）：
  1) 注释遮蔽：HTML 去掉 <!-- -->；CSS 去掉 /* */；JS 去掉 /* */ 与行注释
     （带字符串感知的简易状态机，遮蔽字符一律换成空格、保留换行 → 行号不变）；
     HTML 里的内联 <script>/<style>（无 src 属性）分别按 JS/CSS 规则再遮蔽一遍。
  2) 在遮蔽后文本中找外链引用：
     - http(s)://… 任意位置（字符串里的也算 —— 字符串才是 fetch/src 的真实来源）；
     - 协议相对引用：src="//…"、href="//…"、css url(//…)（跟随页面协议照样出外网）。

白名单（三类，逐条可查；其余 host 一律违规）：
  W1 回环地址 127.0.0.1 / localhost —— 同机 API 端点，或给用户复制的启动命令文本
     （如 frontend/index.html 网关兜底卡里的 "uvicorn … http://127.0.0.1:8000"）；
  W2 www.w3.org —— SVG/XML 命名空间标识符（xmlns/xlink，按规范永不发起网络请求）；
  W3 只出现在注释里的文档/仓库 URL（第 1 步已遮蔽，marked/vue 的 LICENSE 头即此类）。

已知局限：JS 正则字面量内部若含 // 可能被当行注释遮蔽 —— 只会漏报不会误报；
漏网风险由 playwright 断网专项兜底（拦 abort 所有非 localhost 请求，见 spec 附录 K）。

自检方法（见 ux round12 记录）：往任一扫描文件注入 <script src="https://cdn.example.com/x.js">
本测试必须精确报出 file:line；删除后复绿。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("frontend", "demo/static", "workbench/src")
EXTS = {".html", ".css", ".js", ".webmanifest", ".svg"}

URL_RE = re.compile(r"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+")
# 协议相对引用（HTML 属性 / CSS url()）；排除注释自身以 // 开头的误配由遮蔽保证
PROTO_REL_RE = re.compile(r"""(?:\b(?:src|href|poster|data)\s*=|url\()\s*["']?//[^/\s"'>]""")

ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1", "www.w3.org"}

# 显式豁免（文件 → 精确 URL 集合，逐条写明"非资源加载"理由；新增豁免必须给理由）：
# - marked 的运行时错误提示字符串 "Please report this to https://github.com/markedjs/marked."
#   只是异常文案，浏览器从不 fetch 它；其余 host 一律违规。
# - ux(round17) 模型设置面板的供应商端点（demo/static/app.js 的 CB_LLM_VENDORS 预设、
#   index.html 里没有硬编码 URL）：这些是**表单默认值**，页面加载时不发任何请求；真正的
#   /chat/completions 由 Rust 侧 reqwest 发出（workbench/src/llm.rs），浏览器从不直连供应商。
#   断网红线约束的是"界面资产能否离线加载"，与用户自填的 LLM 端点是两回事——同 .rs 不入扫描的理由。
EXEMPT_URLS: dict[str, set[str]] = {
    "frontend/vendor/marked.min.js": {"https://github.com/markedjs/marked"},
    "demo/static/vendor/marked.min.js": {"https://github.com/markedjs/marked"},
    "demo/static/app.js": {
        "https://api.deepseek.com",
        "https://api.z.ai/api/paas/v4",
        "https://api.openai.com/v1",
    },
}


def violations_for_url(text: str, rel: str) -> list[str]:
    """对一段（已遮蔽注释的）文本做外链判定，返回违规描述。"""
    bad: list[str] = []
    exempt = EXEMPT_URLS.get(rel, set())
    for m in URL_RE.finditer(text):
        url = m.group(0).rstrip(".,;:")
        if url in exempt:
            continue
        host = re.sub(r"^[a-zA-Z]+://", "", url).split("/")[0].split("@")[-1]
        host = host.split(":")[0].lower().strip("[]")
        if host in ALLOWED_HOSTS:
            continue
        line = text.count("\n", 0, m.start()) + 1
        bad.append(f"{rel}:{line}: 外链资源引用 {url}（host={host or '?'} 不在白名单）")
    for m in PROTO_REL_RE.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        bad.append(f"{rel}:{line}: 协议相对外链引用（//host），页面托管在 http 时直连外网")
    return bad


def _mask(text: str, start: int, end: int, out: list[str]) -> None:
    """把 [start, end) 换成空格，保留换行（行号不变）。"""
    for i in range(start, min(end, len(out))):
        if out[i] != "\n":
            out[i] = " "


def mask_c_like(text: str, line_comments: bool) -> str:
    """JS/CSS 通用遮蔽：块注释必去；行注释可选；字符串保留（外链常藏在字符串里）。"""
    out = list(text)
    i, n = 0, len(text)
    NORMAL, LINE, BLOCK = 0, 1, 2
    mode = NORMAL
    while i < n:
        c = text[i]
        if mode == NORMAL:
            if c == "/" and i + 1 < n and text[i + 1] == "*" and not line_comments:
                # CSS：只有块注释
                out[i] = out[i + 1] = " "
                mode = BLOCK
                i += 2
                continue
            if line_comments and c == "/" and i + 1 < n:
                if text[i + 1] == "/":
                    out[i] = out[i + 1] = " "
                    mode = LINE
                    i += 2
                    continue
                if text[i + 1] == "*":
                    out[i] = out[i + 1] = " "
                    mode = BLOCK
                    i += 2
                    continue
            if c in ("'", '"', "`"):
                quote = c
                i += 1
                while i < n:
                    if text[i] == "\\":
                        i += 2
                        continue
                    if text[i] == quote:
                        i += 1
                        break
                    if quote != "`" and text[i] == "\n":
                        break  # 单行字符串未闭合，防吞整段
                    i += 1
                continue
            i += 1
        elif mode == LINE:
            if c == "\n":
                mode = NORMAL
            else:
                out[i] = " "
            i += 1
        else:  # BLOCK
            if c == "*" and i + 1 < n and text[i + 1] == "/":
                out[i] = out[i + 1] = " "
                mode = NORMAL
                i += 2
                continue
            if c != "\n":
                out[i] = " "
            i += 1
    return "".join(out)


def mask_css(text: str) -> str:
    return mask_c_like(text, line_comments=False)


def mask_js(text: str) -> str:
    return mask_c_like(text, line_comments=True)


def mask_html(text: str) -> str:
    """HTML：先遮 <!-- -->，再对内联 <script>（无 src）/ <style> 按语言遮注释。"""
    out = list(text)
    for m in re.finditer(r"<!--.*?-->", text, re.S):
        _mask(text, m.start(), m.end(), out)
    masked = "".join(out)

    def _mask_region(body_start: int, body_end: int, masker) -> str:
        inner = masked[body_start:body_end]
        return masked[:body_start] + masker(inner) + masked[body_end:]

    for m in re.finditer(r"<script\b([^>]*)>(.*?)</script\s*>", masked, re.S | re.I):
        attrs, body = m.group(1), m.group(2)
        if re.search(r"\bsrc\s*=", attrs, re.I):
            continue  # 外链风险在属性值里，URL_RE 直接抓
        masked = _mask_region(m.start(2), m.end(2), mask_js)
    for m in re.finditer(r"<style\b[^>]*>(.*?)</style\s*>", masked, re.S | re.I):
        masked = _mask_region(m.start(1), m.end(1), mask_css)
    return masked


def mask_file(path: Path, rel: str) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if rel.endswith(".html"):
        return mask_html(text)
    if rel.endswith(".css"):
        return mask_css(text)
    if rel.endswith((".webmanifest", ".svg")):
        return mask_html(text)  # svg 去注释即可；webmanifest 无注释等价于原样
    return mask_js(text)


def main() -> int:
    scanned = 0
    bad: list[str] = []
    for d in SCAN_DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in EXTS:
                continue
            s = str(path)
            if "target" in path.parts or "node_modules" in path.parts:
                continue
            rel = path.relative_to(ROOT).as_posix()
            scanned += 1
            masked = mask_file(path, rel)
            bad.extend(violations_for_url(masked, rel))

    # .webmanifest 附加断言：PWA 入口与图标必须是同源相对路径（断网/内网才装得成 PWA）
    for mf in (ROOT / "frontend" / "manifest.webmanifest", ROOT / "demo" / "static" / "manifest.webmanifest"):
        if not mf.is_file():
            continue
        rel = mf.relative_to(ROOT).as_posix()
        try:
            data = json.loads(mf.read_text(encoding="utf-8"))
        except ValueError as e:
            bad.append(f"{rel}: manifest 非法 JSON：{e}")
            continue
        for icon in data.get("icons", []) or []:
            src = str(icon.get("src", ""))
            if src.startswith(("http://", "https://", "//")):
                bad.append(f"{rel}: manifest icons src 必须是同源相对路径，实为 {src}")
        for key in ("start_url", "scope"):
            v = str(data.get(key, ""))
            if v.startswith(("http://", "https://", "//")):
                bad.append(f"{rel}: manifest {key} 必须是相对路径，实为 {v}")

    print(f"[no-external-urls] 扫描 {scanned} 个浏览器资产文件（frontend/ demo/static/ workbench/src/）")
    if bad:
        print(f"[no-external-urls] FAIL：{len(bad)} 处外链引用（红线：工地内网断网必须可用）")
        for b in bad:
            print("  " + b)
        return 1
    print("[no-external-urls] PASS：零外链（白名单仅回环地址 / www.w3.org 命名空间 / 注释内文档 URL）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
