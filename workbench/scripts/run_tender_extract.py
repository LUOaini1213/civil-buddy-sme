#!/usr/bin/env python3
"""Workbench bid-parse extract sidecar. Prints one JSON object (handoff fields).

Stdin: JSON {"tender_text": "...", "project_name": "..."} or raw text.
Uses packing_assistant.tools.tender_parse.workbench_bid_extract — same transform as packing handoff.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import zipfile
from pathlib import Path

MAX_FILES = 12
MAX_BYTES = 20 * 1024 * 1024


def read_originals(payload: dict) -> tuple[list[str], list[str]]:
    """Read only the validated current-session upload directory supplied by the host."""
    from packing_assistant import office_job

    files = payload.get("files") or []
    if not isinstance(files, list) or len(files) > MAX_FILES:
        raise ValueError("附件清单无效或数量超限")
    if not files:
        return [], []
    root = Path(str(payload.get("upload_dir") or ""))
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("附件目录无效")
    root = root.resolve(strict=True)
    bodies, names = [], []
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("附件条目无效")
        name = item.get("name")
        path = Path(str(item.get("path") or ""))
        if not isinstance(name, str) or not name or any(c in name for c in "\r\n/\\"):
            raise ValueError("附件名称无效")
        if not path.is_absolute() or path.parent.resolve(strict=True) != root:
            raise ValueError("附件路径超出当前会话")
        for part in (path, *path.parents):
            meta = part.lstat()
            if stat.S_ISLNK(meta.st_mode) or getattr(meta, "st_file_attributes", 0) & 0x400:
                raise ValueError("附件路径不允许链接或重解析点")
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            raise ValueError("附件原文件无效或过大")
        suffix = Path(name).suffix.lower()
        limit = office_job.DOCUMENT_FILE_CHARS
        if suffix == ".pdf":
            # A stream keeps this reader read-only: scans require explicit OCR elsewhere.
            with path.open("rb") as stream:
                body = office_job.pdf_document_text(stream)
        elif suffix == ".docx":
            with zipfile.ZipFile(path) as archive:
                entries = archive.infolist()
                if len(entries) > 10_000 or sum(e.file_size for e in entries) > 100 * 1024 * 1024:
                    raise ValueError("Word 解压大小超出限制")
            body = office_job._read_docx_text(path, limit + 1)
        elif suffix in (".txt", ".md"):
            with path.open(encoding="utf-8-sig", errors="replace") as stream:
                body = stream.read(limit + 1)
        else:
            raise ValueError("全文读取仅支持 PDF、DOCX、TXT、MD")
        if len(body.strip()) < 8:
            raise ValueError("附件未读取到有效文字；扫描 PDF 请先 OCR")
        if len(body) > limit:
            body = body[:limit] + "\n（未读完）附件超过全文读取上限，请拆分后重试。"
        bodies.append(f"### {name}\n{body}")
        names.append(name)
    return bodies, names


def main() -> int:
    root = Path(os.environ.get("PACKING_AGENT_ROOT") or "").expanduser()
    if not root.is_dir() or not (root / "packing_assistant").is_dir():
        here = Path(__file__).resolve()
        for cand in (here.parents[2], here.parents[1], Path.cwd()):
            if (cand / "packing_assistant").is_dir():
                root = cand
                break
    if not root.is_dir() or not (root / "packing_assistant").is_dir():
        print(json.dumps({"ok": False, "error": "PACKING_AGENT_ROOT missing"}))
        return 2
    sys.path.insert(0, str(root))

    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace") if not sys.stdin.isatty() else ""
    payload: dict = {}
    text = ""
    if raw.strip().startswith("{"):
        try:
            payload = json.loads(raw)
            text = str(payload.get("tender_text") or payload.get("text") or "")
        except json.JSONDecodeError:
            text = raw
    else:
        text = raw
    try:
        bodies, read = read_originals(payload)
    except Exception as exc:
        reason = str(exc) if isinstance(exc, ValueError) else "附件原文件读取失败，请检查文件格式和权限"
        sys.stdout.buffer.write(json.dumps({"ok": False, "error": reason}, ensure_ascii=False).encode("utf-8"))
        return 1
    text = "\n\n".join(([text] if text.strip() else []) + bodies)
    if not text.strip():
        text = " ".join(sys.argv[1:])
    from packing_assistant.tools.tender_parse import workbench_bid_extract

    out = workbench_bid_extract(
        text, project_name=str(payload.get("project_name") or "工作台招标解析")
    )
    out["files_read"] = read
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
