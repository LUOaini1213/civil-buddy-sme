#!/usr/bin/env python3
"""Extract a release, launch its real Windows entry, and verify offline HTTP/file flows."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import stat
import subprocess
import sys
import tempfile
from threading import Thread
import time
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4
from xml.etree import ElementTree
from xml.sax.saxutils import escape as xml_escape
import zipfile

ROOT = Path(__file__).resolve().parents[1]


class WindowsJob:
    """Own all descendants of the acceptance launcher, including venv redirects."""
    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class Limits(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", wintypes.DWORD), ("minimum", ctypes.c_size_t),
                        ("maximum", ctypes.c_size_t), ("active", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [("limits", Limits), ("io", ctypes.c_uint64 * 6),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.limits.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, process: subprocess.Popen) -> None:
        import ctypes
        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def extract_verified(archive: Path, target: Path) -> dict:
    with zipfile.ZipFile(archive) as package:
        seen = set()
        for item in package.infolist():
            name = PurePosixPath(item.filename)
            if name.is_absolute() or ".." in name.parts or "\\" in item.filename or ":" in item.filename:
                raise ValueError("Unsafe archive member")
            if stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError("Archive symlinks are forbidden")
            if item.filename.casefold() in seen:
                raise ValueError("Duplicate archive member")
            seen.add(item.filename.casefold())
            if name.name == ".env" or any(part in {"out", "output", "__pycache__", ".venv"} for part in name.parts):
                raise ValueError("Private/runtime content in archive")
            (target / item.filename).resolve().relative_to(target.resolve())
        manifest = json.loads(package.read("release-manifest.json"))
        expected = {entry["path"] for entry in manifest["files"]} | {"release-manifest.json"}
        if expected != set(package.namelist()):
            raise ValueError("Archive does not match its manifest")
        for entry in manifest["files"]:
            data = package.read(entry["path"])
            if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise ValueError("Archive checksum mismatch: " + entry["path"])
        package.extractall(target)
    return manifest


def clean_environment(python: Path) -> dict[str, str]:
    prefixes = ("CIVIL_", "PACKING_", "OPENAI_", "DEEPSEEK_", "LLM_")
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(prefixes) and key.upper() not in {"PYTHONPATH", "PYTHONHOME"}}
    env.update(CIVIL_PYTHON=str(python), PYTHONUTF8="1", PYTHON_DOTENV_DISABLED="1", PYTHONNOUSERSITE="1")
    return env


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request(base: str, path: str, body: dict | None = None, *, data: bytes | None = None,
            content_type: str = "application/json") -> bytes:
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = Request(base + path, data=data, headers={"Content-Type": content_type} if data is not None else {})
    with build_opener(ProxyHandler({})).open(req, timeout=35) as response:
        assert response.status == 200
        return response.read()


def chat(base: str, session: str, message: str, **fields: object) -> dict:
    stream = request(base, "/api/chat", {"session_id": session, "message": message, **fields}).decode("utf-8")
    events = []
    for chunk in re.split(r"\r?\n\r?\n", stream):
        lines = chunk.splitlines()
        name = next((line[6:].strip() for line in lines if line.startswith("event:")), "")
        data = "\n".join(line[5:].strip() for line in lines if line.startswith("data:"))
        if data:
            events.append((name, json.loads(data)))
    assert not [data for name, data in events if name == "error"], events
    done = [data for name, data in events if name == "done"]
    assert len(done) == 1, events
    assert done[0]["submit_blocked"] is True
    return done[0]


def upload(base: str, session: str, filename: str, data: bytes, content_type: str) -> dict:
    boundary = "civil-release-" + uuid4().hex
    prefix = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"session_id\"\r\n\r\n{session}\r\n"
              f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{filename}\"\r\n"
              f"Content-Type: {content_type}\r\n\r\n").encode("utf-8")
    multipart = prefix + data + f"\r\n--{boundary}--\r\n".encode("ascii")
    response = json.loads(request(base, "/api/upload", data=multipart,
                                  content_type="multipart/form-data; boundary=" + boundary))
    assert response["ok"] is True and len(response["files"]) == 1, response
    return response["files"][0]


def meeting_uploads() -> tuple[list[str], dict[str, bytes]]:
    """Real, small Office packages built without relying on a repository test or library."""
    headers = ["会议名称", "场地", "议程", "与会人员"]
    expected = ["专项协调会", "东楼301", "接口核对 < A&B | 记录", "张测试"]
    rows = [headers, expected]
    output = {"csv": ("\n".join(",".join(row) for row in rows) + "\n").encode("utf-8")}
    relationships = 'http://schemas.openxmlformats.org/package/2006/relationships'
    office = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    content_types = 'http://schemas.openxmlformats.org/package/2006/content-types'
    word = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    sheets = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    for kind in ("docx", "xlsx"):
        with BytesIO() as buffer:
            with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as package:
                if kind == "docx":
                    parts = {"word/document.xml": f'<w:document xmlns:w="{word}"><w:body><w:tbl>' + "".join(
                        '<w:tr>' + "".join('<w:tc><w:p><w:r><w:t>' + xml_escape(value) + '</w:t></w:r></w:p></w:tc>'
                                          for value in row) + '</w:tr>' for row in rows) + '</w:tbl></w:body></w:document>'}
                    main = "word/document.xml"
                    types = {main: "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"}
                else:
                    main = "xl/workbook.xml"
                    parts = {
                        main: f'<workbook xmlns="{sheets}" xmlns:r="{office}"><sheets><sheet name="会务资料" sheetId="1" r:id="rId1"/></sheets></workbook>',
                        "xl/_rels/workbook.xml.rels": f'<Relationships xmlns="{relationships}"><Relationship Id="rId1" Type="{office}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
                        "xl/worksheets/sheet1.xml": f'<worksheet xmlns="{sheets}"><dimension ref="A1:D2"/><sheetData>' + "".join(
                            f'<row r="{number}">' + "".join(f'<c r="{chr(65 + column)}{number}" t="inlineStr"><is><t>' + xml_escape(value) + '</t></is></c>'
                                                        for column, value in enumerate(row)) + '</row>'
                            for number, row in enumerate(rows, 1)) + '</sheetData></worksheet>',
                    }
                    types = {main: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
                             "xl/worksheets/sheet1.xml": "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"}
                parts["_rels/.rels"] = f'<Relationships xmlns="{relationships}"><Relationship Id="rId1" Type="{office}/officeDocument" Target="{main}"/></Relationships>'
                parts["[Content_Types].xml"] = f'<Types xmlns="{content_types}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>' + "".join(
                    f'<Override PartName="/{name}" ContentType="{value}"/>' for name, value in types.items()) + '</Types>'
                for name, body in parts.items():
                    package.writestr(name, body.encode("utf-8"))
            output[kind] = buffer.getvalue()
    return expected, output


def xlsx_text_cells(data: bytes) -> set[str]:
    """Read actual exported cells, including shared and inline strings, using stdlib."""
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    values: set[str] = set()
    with zipfile.ZipFile(BytesIO(data)) as book:
        assert book.testzip() is None and "xl/workbook.xml" in book.namelist()
        shared = []
        if "xl/sharedStrings.xml" in book.namelist():
            shared = ["".join(node.itertext()) for node in ElementTree.fromstring(book.read("xl/sharedStrings.xml")).findall("s:si", ns)]
        for name in book.namelist():
            if not name.startswith("xl/worksheets/") or not name.endswith(".xml"):
                continue
            for cell in ElementTree.fromstring(book.read(name)).findall(".//s:c", ns):
                if cell.get("t") == "inlineStr":
                    value = "".join(node.text or "" for node in cell.findall(".//s:t", ns))
                else:
                    value = cell.findtext("s:v", default="", namespaces=ns)
                    if cell.get("t") == "s":
                        value = shared[int(value)]
                values.add(value)
    return values


def docx_content(data: bytes) -> tuple[str, set[str]]:
    """Validate the real Word package and read its editable paragraphs/table cells."""
    word = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    relationships = "http://schemas.openxmlformats.org/package/2006/relationships"
    office = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    types = "http://schemas.openxmlformats.org/package/2006/content-types"
    ns = {"w": word}
    with zipfile.ZipFile(BytesIO(data)) as package:
        assert package.testzip() is None, "DOCX CRC check failed"
        assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml"} <= set(package.namelist())
        roots = {name: ElementTree.fromstring(package.read(name)) for name in package.namelist()
                 if name.endswith((".xml", ".rels"))}
        content_type = next((node.get("ContentType") for node in roots["[Content_Types].xml"].findall(
            f"{{{types}}}Override") if node.get("PartName") == "/word/document.xml"), None)
        assert content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
        main = [node for node in roots["_rels/.rels"].findall(f"{{{relationships}}}Relationship")
                if node.get("Type") == office + "/officeDocument"]
        assert len(main) == 1 and main[0].get("Target", "").lstrip("/") == "word/document.xml"
        assert main[0].get("TargetMode", "Internal") == "Internal"
        document = roots["word/document.xml"]
        assert document.tag == f"{{{word}}}document" and document.find("w:body", ns) is not None
        paragraphs = ["".join(node.text or "" for node in paragraph.findall(".//w:t", ns))
                      for paragraph in document.findall(".//w:p", ns)]
        cells = {"".join(node.text or "" for node in cell.findall(".//w:t", ns))
                 for cell in document.findall(".//w:tc", ns)}
        assert any(paragraph.strip() for paragraph in paragraphs), "DOCX has no readable document text"
        return "\n".join(paragraphs), cells


def office_attachment_flows(base: str, extracted: Path) -> list[dict]:
    expected, fixtures = meeting_uploads()
    results = []
    for kind, data in fixtures.items():
        sid = "office-" + uuid4().hex[:12]
        attached = upload(base, sid, "meeting." + kind, data, "application/octet-stream")
        done = chat(base, sid, "根据附件写一份会务清单，缺失保持待填", expert_ids=["admin-office"], attachments=[attached["id"]])
        assert done["ok"] and done["wrote"] and not done["hitl_pending"], done
        verified = set()
        for item in done["deliverables"]:
            path = Path(item["path"]).resolve()
            path.relative_to(extracted / "demo" / "out")
            downloaded = request(base, "/api/file?" + urlencode({"path": str(path)}))
            assert downloaded and downloaded == path.read_bytes(), item
            if path.suffix == ".md":
                import html
                text = html.unescape(downloaded.decode("utf-8"))
                assert all(value in text for value in expected), (kind, text)
                verified.add(".md")
            elif path.suffix == ".xlsx":
                cells = xlsx_text_cells(downloaded)
                assert all(value in cells for value in expected), (kind, cells)
                verified.add(".xlsx")
            elif path.suffix == ".docx":
                _, cells = docx_content(downloaded)
                assert all(value in cells for value in expected), (kind, cells)
                verified.add(".docx")
        assert verified == {".md", ".xlsx", ".docx"}, (kind, done)
        restored = json.loads(request(base, "/api/sessions/" + sid))
        assert restored["expert_ids"] == ["admin-office"], restored
        assert [item["id"] for item in restored["attachments"]] == [attached["id"]], restored
        assert {item["path"] for item in restored["deliverables"]} == {item["path"] for item in done["deliverables"]}
        results.append({"format": kind, "session_id": sid, "attachment_id": attached["id"],
                        "verified_fields": expected, "verified_exports": sorted(verified),
                        "restored": True, "deliverables": done["deliverables"]})
    return results


def session_transfer_flow(base: str, extracted: Path, sid: str) -> dict:
    """Export raw ZIP over HTTP, import a new task, and compare actual saved bytes."""
    output = (extracted / "demo" / "out").resolve()
    # Attachments now travel with their session under demo/out/<sid>/uploads.
    # Keep checking actual bytes from the extracted product, not the source repo.
    upload_root = output
    before = json.loads(request(base, "/api/sessions/" + sid))
    attachments = json.loads(request(base, "/api/attachments?" + urlencode({"session_id": sid})))["files"]
    assert before["transcript"] and before["deliverables"] and attachments, before

    def snapshot(directory: Path) -> dict[str, str]:
        return {path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in directory.rglob("*") if path.is_file()}

    def attachment_bytes(session: str, rows: list[dict]) -> Counter:
        content = Counter()
        for item in rows:
            directory = upload_root / session / "uploads"
            path = (directory / (item["id"] + ".bin")).resolve()
            path.relative_to(directory)
            data = path.read_bytes()
            assert len(data) == item["bytes"], item
            content[(item["name"], data)] += 1
        return content

    def deliverable_bytes(session: str, rows: list[dict]) -> Counter:
        content = Counter()
        for item in rows:
            path = Path(item["path"]).resolve()
            path.relative_to(output / session)
            data = request(base, "/api/file?" + urlencode({"path": str(path)}))
            assert data and data == path.read_bytes(), item
            if path.suffix == ".docx":
                docx_content(data)
            content[(item["name"], data)] += 1
        return content

    old_files = snapshot(output / sid)
    old_uploads = snapshot(upload_root / sid / "uploads")
    expected_attachments = attachment_bytes(sid, attachments)
    expected_deliverables = deliverable_bytes(sid, before["deliverables"])
    raw = request(base, "/api/sessions/" + sid + "/export")
    with zipfile.ZipFile(BytesIO(raw)) as bundle:
        assert bundle.testzip() is None
        manifest = json.loads(bundle.read("bundle.json"))
        assert manifest["schema"] == "civil.session.bundle.v1" and manifest["source_session"] == sid
        original_records = [json.loads(line) for line in (output / sid / "transcript.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        assert manifest["transcript"] == original_records
        assert len(manifest["attachments"]) == len(attachments)
        for descriptor in manifest["files"]:
            content = bundle.read(descriptor["path"])
            assert len(content) == descriptor["bytes"]
            assert hashlib.sha256(content).hexdigest() == descriptor["sha256"]
    imported = json.loads(request(base, "/api/session-import", data=raw, content_type="application/zip"))
    new_sid = imported["session_id"]
    assert imported["ok"] is True and imported["confirmation_reset"] is True, imported
    assert new_sid != sid and re.fullmatch(r"import-[0-9a-f]{20}", new_sid), imported
    assert imported["attachments"] == len(attachments)
    assert imported["deliverables"] == len(before["deliverables"])
    restored = json.loads(request(base, "/api/sessions/" + new_sid))
    restored_records = [json.loads(line) for line in (output / new_sid / "transcript.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert restored_records == original_records, "Full source records changed during import"
    assert restored["session_id"] == new_sid and restored["title"] == before["title"]
    assert restored["transcript"] == before["transcript"], "Imported transcript changed"
    assert restored["expert_ids"] == before["expert_ids"]
    assert restored["project_id"] == "p-inbox", restored
    copied_attachments = json.loads(request(base, "/api/attachments?" + urlencode({"session_id": new_sid})))["files"]
    assert {row["id"] for row in copied_attachments}.isdisjoint(row["id"] for row in attachments)
    assert attachment_bytes(new_sid, copied_attachments) == expected_attachments, "Imported attachment bytes changed"
    assert deliverable_bytes(new_sid, restored["deliverables"]) == expected_deliverables, "Imported deliverable bytes changed"
    selected_before = attachment_bytes(sid, before["attachments"])
    assert attachment_bytes(new_sid, restored["attachments"]) == selected_before, "Selected attachments changed"
    summary = json.loads((output / new_sid / "session.summary.json").read_text(encoding="utf-8"))
    assert summary["p0_confirmed"] is False
    assert json.loads(request(base, "/api/sessions/" + sid)) == before, "Source task changed after import"
    assert snapshot(output / sid) == old_files and snapshot(upload_root / sid / "uploads") == old_uploads
    return {"source_session": sid, "imported_session": new_sid, "archive_bytes": len(raw),
            "archive_sha256": hashlib.sha256(raw).hexdigest(), "transcript_messages": len(restored["transcript"]),
            "attachments": len(copied_attachments), "deliverables": len(restored["deliverables"]),
            "attachment_bytes_equal": True, "deliverable_bytes_equal": True, "source_unchanged": True,
            "confirmation_reset": True, "restored": True}


def rebuild_context_flow(base: str, extracted: Path, sid: str, *, other_session: str,
                         citations: list[dict]) -> dict:
    """Rebuild derived data while checking the original and neighboring task bytes."""
    output = (extracted / "demo" / "out").resolve()
    upload_root = (extracted / "demo" / "data" / "uploads").resolve()
    cache_names = {"context.summary.json", "context.last.json", "semantic.summary.json",
                   "semantic.attempt.json", "local-retrieval.sqlite3", "local-retrieval.sqlite3-wal",
                   "local-retrieval.sqlite3-shm", "local-retrieval.sqlite3-journal"}

    def snapshot(session: str, *, include_caches: bool) -> dict[str, str]:
        result = {}
        for folder in (output / session, upload_root / session):
            folder.resolve().relative_to(extracted.resolve())
            for path in folder.rglob("*"):
                if not path.is_file():
                    continue
                if not include_caches and path.parent == output / session and path.name in cache_names:
                    continue
                result[path.relative_to(extracted).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result

    assert sid != other_session and (output / other_session).is_dir()
    saved = json.loads(request(base, "/api/sessions/" + sid))
    attachments = json.loads(request(base, "/api/attachments?" + urlencode({"session_id": sid})))["files"]
    history_count = len((output / sid / "transcript.jsonl").read_text(encoding="utf-8").splitlines())
    settings = json.loads(request(base, "/api/llm-config"))
    originals = snapshot(sid, include_caches=False)
    neighboring = snapshot(other_session, include_caches=True)
    assert originals and neighboring
    rebuilt = json.loads(request(base, "/api/context/rebuild", {"session_id": sid}))
    assert rebuilt["ok"] and rebuilt["rebuild"]["semantic_cleared"], rebuilt
    assert rebuilt["rebuild"]["model_calls"] == 0, rebuilt
    assert rebuilt["rebuild"]["history_messages"] == history_count, rebuilt
    assert rebuilt["rebuild"]["attachments"] == len(attachments), rebuilt
    assert rebuilt["rebuild"]["sources"] == history_count + len(attachments), rebuilt
    assert rebuilt["context"]["used"] == rebuilt["context"]["pct"] == 0, rebuilt
    assert rebuilt["context"]["semantic"]["status"] == "reset", rebuilt
    assert rebuilt["context"]["semantic"]["model_calls"] == 0, rebuilt
    assert rebuilt["context"]["semantic"]["included"] is False, rebuilt
    for name in ("semantic.summary.json", "semantic.attempt.json"):
        assert not (output / sid / name).exists(), name
    detail = json.loads(request(base, "/api/context?" + urlencode({"session_id": sid})))
    expected_state = "missing" if settings["semantic_summary"] else "disabled"
    assert detail["semantic_status"]["state"] == expected_state, detail
    assert detail["semantic_status"]["enabled"] == settings["semantic_summary"], detail
    assert detail["semantic_memory_text"] == "" and detail["memory_status"]["state"] == "ready", detail
    assert detail["context"] == rebuilt["context"], detail
    assert json.loads(request(base, "/api/llm-config")) == settings, "Rebuilding changed global model settings"
    restored = json.loads(request(base, "/api/sessions/" + sid))
    for field in ("transcript", "attachments", "deliverables", "expert_ids", "project_id", "title"):
        assert restored[field] == saved[field], (field, restored)
    assert json.loads(request(base, "/api/attachments?" + urlencode({"session_id": sid})))["files"] == attachments
    for cite in citations:
        resolved = json.loads(request(base, cite["url"]))
        assert resolved["text"] == cite["snippet"] and resolved["source_id"] == cite["source_id"], (cite, resolved)
        assert resolved["start"] == cite["start"] and resolved["end"] == cite["end"], (cite, resolved)
    assert snapshot(sid, include_caches=False) == originals, "Rebuilding changed original task files"
    assert snapshot(other_session, include_caches=True) == neighboring, "Rebuilding changed another task"
    return {**rebuilt["rebuild"], "semantic_state": expected_state, "usage_reset": True,
            "original_files_unchanged": len(originals), "deliverables_preserved": len(saved["deliverables"]),
            "other_session": other_session, "other_task_unchanged": True, "settings_unchanged": True,
            "source_urls_preserved": [c["url"] for c in citations]}


def context_flow(base: str, extracted: Path) -> dict:
    sid = "context-" + uuid4().hex[:12]
    original = "Background fixture. " * 500 + "\n项目名称：上下文验收工程。\n独有标记：RAGRESTORE729。"
    done = chat(base, sid, original)
    assert done["ok"] and not done["wrote"]
    records = [json.loads(line) for line in (extracted / "demo" / "out" / sid / "transcript.jsonl").read_text(encoding="utf-8").splitlines()]
    assert records[0]["text"] == original and records[0]["id"]
    memory = json.loads(request(base, "/api/context?" + urlencode({"session_id": sid})))
    assert "上下文验收工程" in memory["memory_text"]
    hits = json.loads(request(base, "/api/context/search", {"session_id": sid, "query": "RAGRESTORE729"}))["citations"]
    assert hits and "RAGRESTORE729" in hits[0]["snippet"]
    assert json.loads(request(base, hits[0]["url"]))["text"] == hits[0]["snippet"]
    long_text = "普通资料背景。\n" * 5500 + "\nATTACH729验收位置：南门仓库二层。"
    chosen = upload(base, sid, "context-tail.txt", long_text.encode("utf-8"), "text/plain")
    hidden = upload(base, sid, "context-hidden.txt", "HIDDEN729：这份附件未被选择。".encode("utf-8"), "text/plain")
    body = {"session_id": sid, "query": "ATTACH729", "attachments": [chosen["id"]]}
    tails = json.loads(request(base, "/api/context/search", body))["citations"]
    tail = next(hit for hit in tails if hit["layer"] == "upload")
    assert tail["start"] > 20000 and "南门仓库二层" in tail["snippet"]
    without = json.loads(request(base, "/api/context/search", {"session_id": sid, "query": "ATTACH729"}))["citations"]
    assert not any(hit["layer"] == "upload" for hit in without)
    draft = chat(base, sid, "写一份项目日报模板，缺失项待填", expert_ids=["pm-daily"])
    assert draft["ok"] and draft["wrote"], draft
    assert {Path(item["path"]).suffix for item in draft["deliverables"]} >= {".md", ".docx", ".xlsx"}, draft
    assert all(Path(item["path"]).read_bytes() for item in draft["deliverables"])
    raw = request(base, "/api/sessions/" + sid + "/export")
    imported = json.loads(request(base, "/api/session-import", data=raw, content_type="application/zip"))["session_id"]
    restored_memory = json.loads(request(base, "/api/context?" + urlencode({"session_id": imported})))
    assert "上下文验收工程" in restored_memory["memory_text"]
    new_hits = json.loads(request(base, "/api/context/search", {"session_id": imported, "query": "RAGRESTORE729"}))["citations"]
    assert new_hits and new_hits[0]["source_id"] != hits[0]["source_id"]
    rebuilt = rebuild_context_flow(base, extracted, sid, other_session=imported, citations=[hits[0], tail])
    selected_after = json.loads(request(base, "/api/context/search", body))["citations"]
    assert any(hit["source_id"] == tail["source_id"] and "南门仓库二层" in hit["snippet"] for hit in selected_after)
    for query in ("ATTACH729", "HIDDEN729"):
        unselected = json.loads(request(base, "/api/context/search", {"session_id": sid, "query": query}))["citations"]
        assert not any(hit["layer"] == "upload" for hit in unselected), unselected
    excluded = json.loads(request(base, "/api/context/search", {
        "session_id": sid, "query": "HIDDEN729", "attachments": [chosen["id"]]}))["citations"]
    assert not any(hit["layer"] == "upload" for hit in excluded), excluded
    hidden_selected = json.loads(request(base, "/api/context/search", {
        "session_id": sid, "query": "HIDDEN729", "attachments": [hidden["id"]]}))["citations"]
    assert any("HIDDEN729" in hit["snippet"] and hit["layer"] == "upload" for hit in hidden_selected)
    rebuilt["attachment_selection_preserved"] = True
    return {"session_id": sid, "imported_session": imported, "full_message_bytes": len(original.encode("utf-8")),
            "attachment_offset": tail["start"], "memory_restored": True, "sources_verified": True,
            "unselected_attachment_excluded": True, "rebuild": rebuilt}


class SemanticLoopbackModel:
    """A bounded, serial local provider; no credentials or network model are used."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.errors: list[str] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def log_message(self, *args):
                pass

            def do_POST(self):
                try:
                    assert self.client_address[0] == "127.0.0.1"
                    assert self.path == "/v1/chat/completions"
                    size = int(self.headers.get("Content-Length", "0"))
                    assert 0 < size <= 1_048_576
                    data = json.loads(self.rfile.read(size))
                    owner.requests.append(data)
                    if data.get("stream"):
                        chunk = {"choices": [{"delta": {"content": "本地模型测试回答完成。"}, "finish_reason": "stop"}]}
                        body = "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\ndata: [DONE]\n\n"
                        content_type = "text/event-stream"
                    else:
                        source = json.loads(data["messages"][-1]["content"])["sources"][0]
                        quote = source["text"][:24]
                        item = {"kind": "goal", "text": "较早讨论强调接口协调。", "evidence": [{
                            "message_id": source["message_id"], "start": source["start"],
                            "end": source["start"] + len(quote), "quote": quote}]}
                        answer = json.dumps({"items": [item]}, ensure_ascii=False)
                        body = json.dumps({"choices": [{"message": {"role": "assistant", "content": answer}}]}, ensure_ascii=False)
                        content_type = "application/json"
                    encoded = body.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                    self.wfile.flush()
                except Exception as exc:
                    owner.errors.append(type(exc).__name__)
                    self.close_connection = True

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:" + str(self.server.server_port) + "/v1"
        self.thread = Thread(target=self.server.serve_forever, kwargs={"poll_interval": .05},
                             name="civil-release-loopback-model")
        self.thread.start()

    def close(self) -> None:
        try:
            self.server.shutdown()
        finally:
            self.server.server_close()
            self.thread.join(6)
        assert not self.thread.is_alive() and self.server.fileno() == -1, "Local model did not close"


def semantic_summary_flow(base: str, extracted: Path, python: Path, *, other_session: str) -> dict:
    """Exercise the extracted product's summary, answer, source, and settings routes."""
    sid = "semantic-" + uuid4().hex[:12]
    output = (extracted / "demo" / "out").resolve()
    transcript = output / sid / "transcript.jsonl"

    def package_python(code: str, payload: dict) -> dict:
        bootstrap = "import json, pathlib, sys\nsys.path.insert(0, str(pathlib.Path('demo').resolve()))\n"
        completed = subprocess.run([str(python), "-c", bootstrap + code], cwd=extracted,
            env=clean_environment(python), input=json.dumps(payload, ensure_ascii=False),
            capture_output=True, text=True, encoding="utf-8", timeout=30, check=True)
        return json.loads(completed.stdout)

    # Seed through the extracted package's canonical storage API, preserving IDs
    # and long recent records; seeding itself never calls a model.
    seeded = package_python("""import projects
pathlib.Path(projects.__file__).resolve().relative_to(pathlib.Path.cwd())
sid = json.load(sys.stdin)['session_id']
root = pathlib.Path('demo/out').resolve()
projects.touch_session(root, sid, '语义摘要发布验收')
for i in range(8):
    projects.append_turn(root, sid, 'user', f'接口协调讨论记录{i}。' + '注意方案之间的衔接。' * 3)
for i in range(4):
    projects.append_turn(root, sid, 'assistant', f'近期原文标记{i}。' + '相关资料需要继续核对。' * 150)
print(json.dumps({'history': projects.read_full_history(root, sid)}, ensure_ascii=False))
""", {"session_id": sid})["history"]
    original = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]
    assert len(original) == len(seeded) == 12
    before = json.loads(request(base, "/api/llm-config"))
    assert not before["configured"] and not before["semantic_summary"], before
    model = SemanticLoopbackModel()
    result = {"session_id": sid, "provider": "loopback-only", "network_model_used": False}
    try:
        settings = json.loads(request(base, "/api/llm-config", {
            "api_key": "release-loopback-fixture", "base_url": model.url, "model": "release-local-model",
            "context_limit": 12000, "output_reserve": 1500, "semantic_summary": True}))
        assert settings["configured"] and settings["semantic_summary"] and settings["source"] == "runtime"
        assert settings["context"]["limit"] == 12000 and settings["context"]["reserve"] == 1500
        assert settings == json.loads(request(base, "/api/llm-config"))
        done = chat(base, sid, "请解释此前沟通中需要关注的问题？", expert_ids=["pm-daily"])
        assert done["ok"] and not done["wrote"] and "本地模型测试回答完成" in done["text"], done
        summaries = [r for r in model.requests if not r.get("stream")]
        answers = [r for r in model.requests if r.get("stream")]
        assert len(summaries) == len(answers) == 1 and not model.errors, model.errors
        summary, answer = summaries[0], answers[0]
        assert summary["max_tokens"] == 1024 and answer["max_tokens"] == 1500
        sources = json.loads(summary["messages"][-1]["content"])["sources"]
        assert [s["message_id"] for s in sources] == [r["id"] for r in seeded[:-4]], sources
        for source in sources:
            record = next(r for r in seeded if r["id"] == source["message_id"])
            assert source["text"] == record["content"][source["start"]:source["end"]]
        assert "较早讨论强调接口协调" in json.dumps(answer["messages"], ensure_ascii=False)
        assert not any(m["content"] == seeded[0]["content"] for m in answer["messages"])
        semantic = done["context"]["semantic"]
        assert semantic["status"] == "generated" and semantic["included"] and semantic["model_calls"] == 1, semantic
        assert done["context"]["semantic_replaced_messages"] == 8

        cache_path = (output / sid / "semantic.summary.json").resolve()
        cache_relative = cache_path.relative_to(extracted.resolve()).as_posix()
        cache_raw = cache_path.read_bytes()
        cache = json.loads(cache_raw)
        assert 0 < len(cache_raw) <= 65536 and not cache["verified"] and not cache["authorizes_actions"]
        raw_after = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]
        assert raw_after[:12] == original and len(raw_after) == 14, "Summary changed canonical history"
        cites = [c for c in done["citations"] if c.get("title", "").startswith("语义摘要原文")]
        assert cites, done["citations"]
        for cite in cites:
            parsed = urlsplit(cite["url"])
            query = parse_qs(parsed.query)
            assert not parsed.scheme and not parsed.netloc and parsed.path == "/api/context/source"
            assert query["session_id"] == [sid] and query["source_id"] == [cite["source_id"]]
            assert query["start"] == [str(cite["start"])] and query["end"] == [str(cite["end"])]
            resolved = json.loads(request(base, cite["url"]))
            assert resolved["text"] == cite["snippet"] and resolved["source_id"] == cite["source_id"]
            assert resolved["start"] == cite["start"] and resolved["end"] == cite["end"]
            record = next(r for r in seeded if r["id"] == resolved["message_id"])
            assert cite["snippet"] == record["content"][cite["start"]:cite["end"]]
        detail = json.loads(request(base, "/api/context?" + urlencode({"session_id": sid})))
        assert "较早讨论强调接口协调" in detail["semantic_memory_text"]
        assert "较早讨论强调接口协调" not in detail["memory_text"]

        disabled = json.loads(request(base, "/api/llm-config", {"semantic_summary": False}))
        assert disabled["configured"] and not disabled["semantic_summary"]
        off = chat(base, sid, "请继续解释资料核对的注意点？", expert_ids=["pm-daily"])
        assert off["ok"] and off["intent"] == "chat" and not off["wrote"] and "本地模型测试回答完成" in off["text"], off
        assert off["context"]["semantic"]["status"] == "disabled", off
        assert "semantic-memory" not in off["context"]["sources_used"], off
        assert len(model.requests) == 3 and len([r for r in model.requests if not r.get("stream")]) == 1
        assert all(r["model"] == "release-local-model" for r in model.requests)
        assert cache_path.read_bytes() == cache_raw and not model.errors
        final_raw = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]
        assert final_raw[:12] == original and len(final_raw) == 16

        # Retain the successful real cache until reset. A harmless old failure
        # marker verifies that the separate retry cache is also cleared.
        package_python("""import projects
sid = json.load(sys.stdin)['session_id']
path = projects._bounded_path(pathlib.Path('demo/out').resolve(), sid, 'semantic.attempt.json')
projects._write_atomic(path, json.dumps({'digest': 'release-rebuild-fixture', 'failed_at': 0}))
print(json.dumps({'written': path.is_file()}))
""", {"session_id": sid})
        assert (output / sid / "semantic.attempt.json").is_file()
        assert json.loads(request(base, "/api/context?" + urlencode({"session_id": sid})))["context"]["used"] > 0
        calls_before_rebuild = len(model.requests)
        first_rebuild = rebuild_context_flow(base, extracted, sid, other_session=other_session, citations=cites)
        rule_memory = (output / sid / "context.summary.json").read_bytes()
        enabled = json.loads(request(base, "/api/llm-config", {"semantic_summary": True}))
        assert enabled["configured"] and enabled["semantic_summary"]
        repeated_rebuild = rebuild_context_flow(base, extracted, sid, other_session=other_session, citations=cites)
        assert (output / sid / "context.summary.json").read_bytes() == rule_memory, "Repeated rebuild changed rule memory"
        assert len(model.requests) == calls_before_rebuild == 3 and not model.errors
        result["rebuild"] = {**first_rebuild, "repeated": repeated_rebuild, "idempotent": True,
                             "additional_model_calls": 0, "attempt_cache_cleared": True}

        # Use the packaged token estimator and final transport guard, never the
        # source checkout's implementation or an approximate character count.
        budgets = package_python("""import context
pathlib.Path(context.__file__).resolve().relative_to(pathlib.Path.cwd())
context.set_runtime_policy({'limit': 12000, 'reserve': 1500})
payload = json.load(sys.stdin)
summary_tokens = context.messages_tokens(payload['summary']['messages'])
assert summary_tokens <= 3500
answer_tokens = [context.validate_request(r['messages'])['used'] for r in payload['answers']]
assert all(r['max_tokens'] == 1500 for r in payload['answers'])
print(json.dumps({'summary_input_tokens': summary_tokens, 'summary_output_reserve': 1024,
    'answer_input_tokens': answer_tokens, 'answer_output_reserve': 1500, 'context_limit': 12000}))
""", {"summary": summary, "answers": [r for r in model.requests if r.get("stream")]})
        assert budgets["summary_input_tokens"] == semantic["input_tokens"]
        result.update(summary_calls=1, streaming_answer_calls=2, disabled_extra_summary_calls=0,
            disabled_cache_unchanged=True, cache=cache_relative, cache_bytes=len(cache_raw),
            source_urls=[c["url"] for c in cites], source_quotes_verified=True,
            full_history_preserved=True, seeded_messages=12, final_messages=16, budgets=budgets)
    finally:
        try:
            cleared = json.loads(request(base, "/api/llm-config", {"clear": True}))
            assert not cleared["configured"] and not cleared["semantic_summary"] and cleared["source"] == "env"
            assert cleared["context"]["limit"] == before["context"]["limit"]
            assert cleared["context"]["reserve"] == before["context"]["reserve"]
            assert cleared == json.loads(request(base, "/api/llm-config"))
            result["model_settings_cleared"] = True
        finally:
            model.close()
            result["provider_closed"] = True
    return result


def idle_cancel_flow(base: str, extracted: Path) -> dict:
    sid = "idle-release-" + uuid4().hex[:12]
    response = json.loads(request(base, "/api/sessions/" + sid + "/cancel", {}))
    assert response == {"ok": True, "session_id": sid, "state": "idle",
                        "active": False, "cancel_requested": False}, response
    assert not (extracted / "demo" / "out" / sid).exists(), "Idle cancellation created a task"
    return response


def confirmation_flow(base: str, extracted: Path) -> dict:
    rejected = []
    for value in ("true", "yes", "false", 1, 0, None):
        sid = "invalid-gate-" + uuid4().hex[:12]
        try:
            request(base, "/api/chat", {"session_id": sid, "message": "写一份消防专篇",
                                        "expert_ids": ["fire-protect"], "confirm_ok": value})
        except HTTPError as exc:
            assert exc.code == 422, (value, exc.code, exc.read())
            exc.close()
        else:
            raise AssertionError(f"Nonboolean confirmation accepted: {value!r}")
        assert not (extracted / "demo" / "out" / sid).exists(), "Invalid confirmation reached session/tool execution"
        rejected.append({"value": value, "status": 422})
    sid = "false-gate-" + uuid4().hex[:12]
    blocked = chat(base, sid, "写一份消防专篇", expert_ids=["fire-protect"], confirm_ok=False)
    assert blocked["hitl_pending"] is True and not blocked["wrote"] and not blocked["deliverables"], blocked
    audit = json.loads(request(base, "/api/harness/audit/" + sid))
    assert audit["counts"]["tools"] == 0 and audit["counts"]["writes"] == 0, audit
    return {"rejected": rejected, "boolean_false_pending": True, "false_session_id": sid,
            "tool_calls": audit["counts"]["tools"], "writes": audit["counts"]["writes"]}


class ReleaseServer:
    def __init__(self, root: Path, python: Path, port: int, log: Path, *, setup: bool = False):
        self.root, self.python, self.port, self.log = root, python, port, log
        self.setup = setup
        self.base = f"http://127.0.0.1:{port}"
        self.process: subprocess.Popen | None = None

    def start(self) -> dict:
        self.handle = self.log.open("ab")
        self.job = WindowsJob() if os.name == "nt" else None
        if os.name == "nt":
            command = ["cmd.exe", "/d", "/c", "start-workbench.bat", "--no-browser", "--port", str(self.port)]
        else:
            command = [str(self.python), "scripts/start_workbench.py", "--no-browser", "--port", str(self.port)]
        if self.setup:
            command.append("--setup")
        self.command = command
        env = clean_environment(self.python)
        if self.setup:
            for key in list(env):
                if key.upper().startswith("PIP_"):
                    env.pop(key)
            env.update(PIP_CONFIG_FILE=os.devnull, PIP_INDEX_URL="https://pypi.org/simple",
                       PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INPUT="1",
                       PIP_CACHE_DIR=str(self.root / ".cache" / "pip"))
        self.process = subprocess.Popen(command, cwd=self.root, env=env,
                                        stdout=self.handle, stderr=subprocess.STDOUT,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                                        start_new_session=os.name != "nt")
        if self.job is not None:
            self.job.assign(self.process)
        (self.log.parent / (self.log.stem + ".pid")).write_text(str(self.process.pid), encoding="ascii")
        deadline = time.monotonic() + (900 if self.setup else 40)
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("Release entry exited early; inspect " + str(self.log))
            try:
                health = json.loads(request(self.base, "/api/health"))
                if health.get("product") == "civil-codex":
                    assert health["ok"] is True and health["has_key"] is False, health
                    assert health["job"]["granted"] is False, health
                    return health
            except OSError:
                pass
            time.sleep(0.15)
        raise RuntimeError("Release entry did not become healthy; inspect " + str(self.log))

    def close(self) -> None:
        if getattr(self, "job", None) is not None:
            self.job.close()
        if self.process is not None:
            if self.process.poll() is None:
                if os.name == "nt":
                    # Closing the owned Job kills cmd, bootstrap and Uvicorn.
                    # Terminate is a fallback for a failed job assignment.
                    if self.job is None:
                        self.process.terminate()
                else:
                    import signal
                    os.killpg(self.process.pid, signal.SIGTERM)
                self.process.wait(timeout=10)
            self.handle.close()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", self.port)) != 0:
                    return
            time.sleep(0.1)
        raise AssertionError("The release left its HTTP port open after process cleanup")


def collaboration_flow(base: str, extracted: Path) -> dict:
    sid = "collab-" + uuid4().hex[:12]
    tender = upload(base, sid, "招标原文.txt", "投标人须具备建筑工程施工资质。\n未实质性响应作废标。\n交货期90个日历天。\n技术标评分：施工组织设计25分。\n★深基坑专项方案须编制。".encode(), "text/plain")
    response = upload(base, sid, "投标响应.txt", "投标人须具备建筑工程施工资质。营业执照扫描件待补。".encode(), "text/plain")
    roles = {tender["id"]: "tender", response["id"]: "response"}
    done = chat(base, sid, "全面检查投标响应并汇总缺项。", attachments=list(roles), attachment_roles=roles)
    assert done["ok"] and done["wrote"] and not done["hitl_pending"], done
    value = done["collaboration"]
    assert value["state"] == "done" and len(value["children"]) == 2, value
    assert all(c["status"] == "done" for c in value["children"]), value
    assert value["review"]["handoff_unchanged"] and value["review"]["response_evidence_supplied"]
    assert value["aggregate_metrics"]["model_calls"] == 0
    for item in done["deliverables"]:
        path = Path(item["path"]).resolve()
        path.relative_to(extracted / "demo" / "out" / sid)
        assert request(base, "/api/file?" + urlencode({"path": str(path)})) == path.read_bytes()
    restored = json.loads(request(base, "/api/sessions/" + sid))
    assert restored["collaboration"]["parent_run_id"] == value["parent_run_id"]
    assert restored["attachment_roles"] == roles
    capability = json.loads(request(base, "/api/experts/bid-tech/capability"))
    assert capability["inputs"] and capability["acceptance"] and capability["tools"]
    return {"session_id": sid, "run_id": value["parent_run_id"], "state": value["state"],
            "files": len(done["deliverables"]), "metrics": value["aggregate_metrics"]}


def all_experts(base: str, catalog: dict, extracted: Path) -> list[dict]:
    results = []
    for index, expert in enumerate(catalog["experts"], 1):
        eid = expert["id"]
        sid = "post-" + uuid4().hex[:12]
        record = {"expert": eid, "session": sid}
        try:
            message = "写一份本岗草稿，所有未提供的内容保持待填。"
            if eid == "bid-parse":
                message += "\n以下是验收用招标原文：投标人必须提交营业执照复印件；施工工期为60天。"
            done = chat(base, sid, message,
                        expert_ids=[eid], confirm_ok=True, confirm_text="我明白，将由持证人员签认")
            record.update(ok=done.get("ok"), wrote=done.get("wrote"),
                          hitl_pending=done.get("hitl_pending", False), text=done.get("text", ""),
                          deliverables=done.get("deliverables", []), verified_docx=0)
            for item in record["deliverables"]:
                path = Path(item["path"]).resolve()
                path.relative_to(extracted / "demo" / "out")
                assert path.is_file() and path.stat().st_size > 0
                if path.suffix == ".md" and eid == "pack-ship":
                    text = path.read_text(encoding="utf-8")
                    assert "UNSPECIFIED" in text and "can_fit=true" not in text
                if path.suffix == ".docx":
                    docx_content(path.read_bytes())
                    record["verified_docx"] += 1
            assert done.get("ok") and done.get("wrote") and record["deliverables"], done
            assert record["verified_docx"] > 0, f"{eid} did not return a validated DOCX deliverable"
            record["verified"] = True
        except Exception as exc:
            record.update(verified=False, error=f"{type(exc).__name__}: {exc}")
        results.append(record)
        if index % 10 == 0 or index == len(catalog["experts"]):
            print(f"Release offline experts checked: {index}/{len(catalog['experts'])}", flush=True)
    return results


def offline_setup_failure(root: Path, python: Path, evidence: Path) -> dict:
    """Exercise real venv creation and a deterministic offline pip failure."""
    env = clean_environment(python)
    # Deliberately unavailable indexes: no package download, owner pip config or
    # system environment mutation is needed to test this first-start failure path.
    for key in list(env):
        if key.upper().startswith("PIP_"):
            env.pop(key)
    env.update(PIP_NO_INDEX="1", PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK="1")
    completed = subprocess.run([str(python), "scripts/start_workbench.py", "--setup", "--no-browser"],
                               cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=90)
    log = evidence / "offline-setup.log"
    log.write_text(completed.stdout + completed.stderr, encoding="utf-8")
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "Dependency setup failed" in completed.stderr
    assert "retry start-workbench.bat --setup" in completed.stderr
    interpreter = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    assert interpreter.is_file(), "real package-local virtual environment was not created"
    interpreter.parent.parent.resolve().relative_to(root.resolve())
    return {"created_local_venv": str(interpreter), "exit_code": completed.returncode,
            "network_disabled": True, "log": str(log)}


def smoke(archive: Path, python: Path, *, check_all_experts: bool = False, fresh_install: bool = False) -> Path:
    if not archive.is_file() or not python.is_file():
        raise FileNotFoundError("Archive and prepared Python interpreter must exist")
    output = ROOT / "output" / "release-smoke"
    output.mkdir(parents=True, exist_ok=True)
    evidence = Path(tempfile.mkdtemp(prefix="python-workbench-", dir=output)).resolve()
    evidence.relative_to(output.resolve())
    extracted = evidence / "extracted product"
    extracted.mkdir()
    manifest = extract_verified(archive, extracted)
    install_result = None
    if fresh_install:
        setup = ReleaseServer(extracted, python, free_port(), evidence / "first-install.log", setup=True)
        try:
            print("Installing dependencies in a fresh extracted package; log: " + str(setup.log), flush=True)
            setup.start()
        finally:
            setup.close()
        python = extracted / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        cfg = (extracted / ".venv" / "pyvenv.cfg").read_text(encoding="utf-8")
        assert "include-system-site-packages = false" in cfg.lower()
        site_check = "import fastapi, langgraph, openpyxl, pathlib, sys; p=pathlib.Path(sys.prefix); " \
                     "[pathlib.Path(m.__file__).resolve().relative_to(p) for m in (fastapi, openpyxl)]; print(sys.prefix)"
        prefix = subprocess.run([str(python), "-c", site_check], cwd=extracted, env=clean_environment(python),
                                check=True, capture_output=True, text=True, encoding="utf-8").stdout.strip()
        freeze = subprocess.run([str(python), "-m", "pip", "freeze"], cwd=extracted, env=clean_environment(python),
                                check=True, capture_output=True, text=True, encoding="utf-8").stdout
        (evidence / "installed-requirements.txt").write_text(freeze, encoding="utf-8")
        install_result = {"python": str(python), "prefix": prefix, "system_site_packages": False,
                          "log": str(setup.log), "installed_requirements": str(evidence / "installed-requirements.txt")}
    code = """import json, pathlib, sys
sys.path.insert(0, str(pathlib.Path('demo').resolve()))
import app, chat_service, packing_assistant.civil, packing_assistant.expert_turn
from packing_assistant.runtime import expert_skills
modules = [app, chat_service, packing_assistant.civil, packing_assistant.expert_turn]
paths = [str(pathlib.Path(m.__file__).resolve()) for m in modules]
for p in paths: pathlib.Path(p).relative_to(pathlib.Path.cwd())
assert len(expert_skills.list_expert_skill_ids()) == 66
print(json.dumps({'imports': paths, 'skills': 66, 'runtime_prefix': sys.prefix}))
"""
    origins = json.loads(subprocess.run([str(python), "-c", code], cwd=extracted,
                                        env=clean_environment(python), check=True, capture_output=True,
                                        text=True, encoding="utf-8").stdout)
    port = free_port()
    server = ReleaseServer(extracted, python, port, evidence / "startup.log")
    sid = "release-" + uuid4().hex[:12]
    result = {"archive": str(archive), "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
              "version": manifest["version"], "extracted": str(extracted), "interpreter": str(python),
              **origins, "session_id": sid}
    if install_result:
        result["fresh_install"] = install_result
    try:
        result["health"] = server.start()
        for capability in ("session_backup", "cancel", "word_export", "task_memory", "local_rag",
                           "task_routing", "expert_contracts", "tender_collaboration", "semantic_summary"):
            assert result["health"]["capabilities"][capability] is True, result["health"]
        collision = subprocess.run(server.command, cwd=extracted, env=clean_environment(python),
                                   capture_output=True, text=True, encoding="utf-8", timeout=15)
        assert collision.returncode == 1 and "端口" in collision.stderr + collision.stdout
        assert json.loads(request(server.base, "/api/health"))["ok"] is True
        result["port_collision_exit"] = collision.returncode
        html = request(server.base, "/").decode("utf-8")
        assets = re.findall(r'(?:src|href)="(/static/[^"?]+)', html)
        assert assets and "/static/chat-stream.js" in assets
        for asset in assets:
            assert request(server.base, asset), asset
        result["static_assets"] = assets
        catalog = json.loads(request(server.base, "/api/catalog"))
        result["catalog_experts"] = len(catalog["experts"])
        assert result["catalog_experts"] == 66, catalog
        hello = chat(server.base, sid, "你好")
        assert hello["intent"] == "chat" and not hello["wrote"] and "Civil Buddy" in hello["text"]
        project = json.loads(request(server.base, "/api/projects", {"name": "发布验收工程"}))["project"]
        material = "项目：发布验收工程\n辖区：中国大陆\n日期：2026-09-12\n天气：晴\n部位：东侧试验段\n现场记录：围挡已完成复查。"
        uploaded = upload(server.base, sid, "site-record.txt", material.encode("utf-8"), "text/plain")
        draft = chat(server.base, sid, "根据所选资料写一份项目日报模板，缺失内容保持待填", expert_ids=["pm-daily"],
                     project_id=project["id"], attachments=[uploaded["id"]])
        assert draft["ok"] and draft["wrote"], draft
        files = draft["deliverables"]
        assert {Path(item["path"]).suffix for item in files} >= {".md", ".xlsx", ".docx"}, files
        for item in files:
            path = Path(item["path"]).resolve()
            path.relative_to(extracted / "demo" / "out")
            data = request(server.base, "/api/file?" + urlencode({"path": str(path)}))
            assert data == path.read_bytes() and data
            if path.suffix == ".md":
                text = data.decode("utf-8")
                for expected in ("发布验收工程", "东侧试验段", "围挡已完成复查", "待填"):
                    assert expected in text, expected
            if path.suffix == ".xlsx":
                with zipfile.ZipFile(path) as book:
                    assert book.testzip() is None and "xl/workbook.xml" in book.namelist()
            if path.suffix == ".docx":
                text, cells = docx_content(data)
                for expected in ("发布验收工程", "东侧试验段", "围挡已完成复查", "待填"):
                    assert expected in text, expected
                assert "东侧试验段" in cells, "DOCX did not preserve the supplied site field in its table"
            item["sha256"] = hashlib.sha256(data).hexdigest()
        result["deliverables"] = files
        result["verified_word_exports"] = [item["name"] for item in files if Path(item["path"]).suffix == ".docx"]
        restored = json.loads(request(server.base, "/api/sessions/" + sid))
        assert len(restored["transcript"]) == 4
        assert restored["project_id"] == project["id"]
        assert restored["attachments"][0]["id"] == uploaded["id"]
        assert {d["path"] for d in restored["deliverables"]} == {d["path"] for d in files}
        result["session_transfer"] = session_transfer_flow(server.base, extracted, sid)
        result["idle_cancel"] = idle_cancel_flow(server.base, extracted)
        blocked = chat(server.base, "gate-" + uuid4().hex[:12], "写一份临边防护专项施工方案", expert_ids=["construction"])
        assert blocked["hitl_pending"] and not blocked["wrote"] and not blocked["deliverables"], blocked
        result["http_confirmation"] = confirmation_flow(server.base, extracted)
        result["office_attachment_flows"] = office_attachment_flows(server.base, extracted)
        result["context"] = context_flow(server.base, extracted)
        result["semantic"] = semantic_summary_flow(server.base, extracted, python, other_session=result["context"]["session_id"])
        result["collaboration"] = collaboration_flow(server.base, extracted)
        followup = chat(server.base, sid, "项目日报是什么意思？先别写", expert_ids=["pm-daily"])
        assert followup["intent"] == "chat" and not followup["wrote"]
        if check_all_experts:
            result["expert_runs"] = all_experts(server.base, catalog, extracted)
    finally:
        server.close()
    restarted = ReleaseServer(extracted, python, port, evidence / "restart.log")
    try:
        restarted.start()
        restored = json.loads(request(restarted.base, "/api/sessions/" + sid))
        assert len(restored["transcript"]) == 6
        assert {d["path"] for d in restored["deliverables"]} == {d["path"] for d in files}
        for item in files:
            assert hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest() == item["sha256"]
        result["restart_restored"] = True
        transfer = result["session_transfer"]
        imported = json.loads(request(restarted.base, "/api/sessions/" + transfer["imported_session"]))
        assert imported["transcript"] == restored["transcript"][:transfer["transcript_messages"]]
        assert len(imported["deliverables"]) == transfer["deliverables"]
        assert len(imported["attachments"]) == transfer["attachments"]
        expected_files = Counter((item["name"], item["sha256"]) for item in files)
        copied_files = Counter()
        for item in imported["deliverables"]:
            path = Path(item["path"]).resolve()
            path.relative_to(extracted / "demo" / "out" / transfer["imported_session"])
            data = request(restarted.base, "/api/file?" + urlencode({"path": str(path)}))
            assert data == path.read_bytes()
            copied_files[(item["name"], hashlib.sha256(data).hexdigest())] += 1
        assert copied_files == expected_files
        transfer["restart_restored"] = True
        restored_context = json.loads(request(restarted.base, "/api/context?" + urlencode({"session_id": result["context"]["imported_session"]})))
        assert "上下文验收工程" in restored_context["memory_text"]
        result["context"]["restart_restored"] = True
        collaboration = result["collaboration"]
        restored_collab = json.loads(request(restarted.base, "/api/sessions/" + collaboration["session_id"]))
        assert restored_collab["collaboration"]["parent_run_id"] == collaboration["run_id"]
        assert restored_collab["collaboration"]["state"] == "done"
        collaboration["restart_restored"] = True
    finally:
        restarted.close()
    result["process_cleanup"] = "both server trees exited and HTTP port closed"
    if not fresh_install:
        result["offline_setup_failure"] = offline_setup_failure(extracted, python, evidence)
    result["ok"] = all(item["verified"] for item in result.get("expert_runs", []))
    report = evidence / "acceptance.json"
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--all-experts", action="store_true", help="also verify offline draft requests for all 66 posts")
    parser.add_argument("--fresh-install", action="store_true", help="install requirements into a fresh package-local venv first (network)")
    args = parser.parse_args()
    report = smoke(args.archive.resolve(), args.python.resolve(), check_all_experts=args.all_experts, fresh_install=args.fresh_install)
    result = json.loads(report.read_text(encoding="utf-8"))
    print(("PASS" if result["ok"] else "FAIL") + " release HTTP/file acceptance: " + str(report))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
