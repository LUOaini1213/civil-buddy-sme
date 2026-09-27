#!/usr/bin/env python3
"""JS 语法门禁（UX R12，固化 U-R10 的 esprima 检查为 CI 常规门禁）。

背景：R6 遗留过一个致命 JS 语法错误（demo/static/app.js 块注释内 `runs/*/trace.json`
的 `*/` 提前终止注释，导致 :8765 整个 app.js 无法执行），R10 用 esprima 才发现；
浏览器对语法错误的静默失败最伤演示，故固化为提交/CI 必跑门禁。

做法（node --check 是 parse-only，不执行任何代码，模式借自 web 惯例：
`find … -name '*.js' | while read; do node --check` / dev.to CI 门禁同款）：
  1) 收集 frontend/ demo/static/ workbench/src/ 的独立 *.js（排除 target/node_modules）；
  2) 抽取各 .html 内联 <script>（无 src 属性；type="module" 按 .mjs 检查）；
  3) 逐段 `node --check`；
  4) 附带 CSS 最小体检（纯 python、必跑）：.css 与 <style> 去注释去字符串后
     花括号必须配平（R11 大改主题变量后截断一类的损伤能被抓住）。

本机无 node：打印 SKIP 说明并 exit 0（四件套不因环境缺件挂掉）；
CI 的 ubuntu-latest runner 必有 node，该步骤必跑 —— 两头都不静默：
SKIP 时会打印"本地未校验 JS 语法，语法门禁以 CI 为准"。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("frontend", "demo/static", "gateway/pages", "workbench/src")
SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.S | re.I)
STYLE_RE = re.compile(r"<style\b[^>]*>(.*?)</style\s*>", re.S | re.I)


def collect() -> tuple[list[Path], list[tuple[str, str]], list[Path]]:
    """返回 (独立 js 文件, [(标签, 内容)] 内联脚本, css 文件)。"""
    js_files: list[Path] = []
    inline: list[tuple[str, str]] = []
    css_files: list[Path] = []
    for d in SCAN_DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or "target" in path.parts or "node_modules" in path.parts:
                continue
            if path.suffix.lower() == ".js":
                js_files.append(path)
            elif path.suffix.lower() == ".css":
                css_files.append(path)
            elif path.suffix.lower() == ".html":
                text = path.read_text(encoding="utf-8", errors="replace")
                for m in SCRIPT_RE.finditer(text):
                    attrs = m.group(1)
                    if re.search(r"\bsrc\s*=", attrs, re.I):
                        continue  # 外链/静态引用由 test_no_external_urls.py 把关
                    line = text[: m.start()].count("\n") + 1
                    kind = "mjs" if re.search(r'type\s*=\s*["\']module["\']', attrs, re.I) else "js"
                    inline.append((f"{path.relative_to(ROOT).as_posix()}:{line} <script>", (m.group(2), kind)))
                for m in STYLE_RE.finditer(text):
                    line = text[: m.start()].count("\n") + 1
                    css_files_label = f"{path.relative_to(ROOT).as_posix()}:{line} <style>"
                    inline.append((f"{css_files_label} [css]", (m.group(1), "css")))
    return js_files, inline, css_files


def css_balanced(text: str) -> bool:
    """去注释去字符串后数花括号。"""
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'', "''", text)
    return text.count("{") == text.count("}")


def main() -> int:
    js_files, inline, css_files = collect()
    n_standalone = len(js_files)
    n_inline = sum(1 for _, (c, k) in inline if k != "css")
    print(f"[js-syntax] 收集：独立 js {n_standalone} 个，内联 <script> {n_inline} 段，css {len(css_files)} 个")

    # CSS 体检：无 node 也必跑
    css_bad: list[str] = []
    for label, (content, kind) in inline:
        if kind == "css" and not css_balanced(content):
            css_bad.append(f"{label}: <style> 花括号不配平")
    for path in css_files:
        if not css_balanced(path.read_text(encoding="utf-8", errors="replace")):
            css_bad.append(f"{path.relative_to(ROOT).as_posix()}: 花括号不配平")
    if css_bad:
        print("[js-syntax] FAIL（css 体检）")
        for b in css_bad:
            print("  " + b)
        return 1
    print("[js-syntax] CSS 体检 PASS（花括号配平）")

    node = shutil.which("node") or shutil.which("node.exe")
    if node is None:
        print(
            "[js-syntax] SKIP：本机未找到 node，无法 node --check JS 语法（CSS 体检已跑）。"
            "语法门禁以 CI（ubuntu runner 自带 node）为准；如需本地跑请先安装 Node.js。"
        )
        return 0

    failures: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        jobs: list[tuple[str, Path]] = []
        for f in js_files:
            jobs.append((f.relative_to(ROOT).as_posix(), f))
        for i, (label, (content, kind)) in enumerate([x for x in inline if x[1][1] != "css"]):
            p = tdp / f"inline_{i:03d}.{kind}"
            p.write_text(content, encoding="utf-8")
            jobs.append((label, p))
        for label, path in jobs:
            r = subprocess.run(
                [node, "--check", str(path)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if r.returncode != 0:
                detail = (r.stderr or r.stdout or "").strip().splitlines()
                failures.append(f"{label}: {' | '.join(detail[:4])}")
    if failures:
        print(f"[js-syntax] FAIL：{len(failures)} 段 JS 语法错误（node --check）")
        for f in failures:
            print("  " + f)
        return 1
    print(f"[js-syntax] PASS：{n_standalone} 个独立 js + {n_inline} 段内联脚本全部 node --check 通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
