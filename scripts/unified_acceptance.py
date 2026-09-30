"""Bounded HTTP acceptance for the unified Rust workbench.

Default: test a server configured with model civil-scripted-acceptance.
--live: explicitly use the already configured server model; no keys are read.
--serve-model: run the local deterministic OpenAI-compatible test endpoint.

For a scripted server configure CIVIL_MODEL=civil-scripted-acceptance,
CIVIL_API_BASE=http://127.0.0.1:18889/v1 and a dummy CIVIL_API_KEY in that
server's environment. This script never changes a running server configuration.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4
from xml.etree import ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
MODEL = "civil-scripted-acceptance"
FILES = ["requirements.pdf", "quantities.xlsx", "report.docx"]
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
TASK = ("根据 requirements.pdf 中 sample_count 的明确要求，检查 quantities.xlsx 的 Counts!B2 "
        "和 report.docx 中 sample_count 段落，把这两处同时修改为 PDF 要求的数量。"
        "请实际读取三个原件并检索 PDF 要求；每份补丁携带 PDF 的完整原文引用、SHA256和定位，"
        "先预览再保存新副本。保持其他段落、表格、样式、页眉和未修改工作表不变，不覆盖原件。"
        "可委派只读资料子代理核对要求；最后列出两个真实新文件、来源及尚未完成的重算或渲染。")


def package(data):
    with ZipFile(BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def fixture(folder: Path, required=6):
    """Create real synthetic files. The prompt intentionally does not state the required number."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    folder = folder.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    if any((folder / name).exists() for name in FILES):
        raise ValueError("Acceptance fixtures already exist; use a fresh directory")
    if type(required) is not int or not 1 <= required <= 999:
        raise ValueError("Fixture requirement must be a bounded positive integer")
    writer = PdfWriter()
    page = writer.add_blank_page(width=420, height=595)
    font = writer._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")}))
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 35 530 Td (Synthetic acceptance requirement.) Tj 0 -24 Td (sample_count={required}) Tj 0 -24 Td (Use this value in the specimen register and report.) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    with (folder / FILES[0]).open("wb") as output:
        writer.write(output)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Counts"
    sheet.append(["Metric", "Value", "Comment", "Derived"])
    sheet.append(["sample_count", 4, "Keep this note", "=B2*2"])
    sheet.append(["untouched_count", 99, "Unchanged row"])
    sheet["B2"].font = Font(bold=True, color="003366")
    sheet["B2"].number_format = "0"
    workbook.create_sheet("Unchanged")["A1"] = "Keep this sheet"
    workbook.save(folder / FILES[1])
    workbook.close()
    parts = {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/><Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/></Types>',
        "_rels/.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        "word/document.xml": f'<w:document xmlns:w="{W}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body><w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t>Specimen report</w:t></w:r></w:p><w:p><w:r><w:rPr><w:b/></w:rPr><w:t>sample_count=4</w:t></w:r></w:p><w:p><w:r><w:t>Unchanged scope paragraph.</w:t></w:r></w:p><w:sectPr><w:headerReference w:type="default" r:id="rIdHeader"/><w:pgSz w:w="11906" w:h="16838"/></w:sectPr></w:body></w:document>',
        "word/styles.xml": f'<w:styles xmlns:w="{W}"><w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:rPr><w:rFonts w:ascii="Arial"/><w:sz w:val="24"/></w:rPr></w:style><w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/></w:style></w:styles>',
        "word/header1.xml": f'<w:hdr xmlns:w="{W}"><w:p><w:r><w:t>Unchanged project header</w:t></w:r></w:p></w:hdr>',
        "word/_rels/document.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/><Relationship Id="rIdHeader" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/></Relationships>',
    }
    with ZipFile(folder / FILES[2], "w") as archive:
        for name, value in parts.items():
            archive.writestr(name, '<?xml version="1.0" encoding="UTF-8"?>'+value)
    return {"workspace": str(folder), "files": FILES, "required_sample_count": required,
            "initial_sample_count": 4, "hashes": {name: sha256((folder/name).read_bytes()).hexdigest() for name in FILES}}


def _unwrap(value):
    if isinstance(value, dict) and value.get("ok") is False:
        raise ValueError("Tool failed: "+str(value.get("error")))
    while isinstance(value, dict) and isinstance(value.get("result"), dict):
        value = value["result"]
    return value


def _tool_history(messages):
    calls, records = {}, []
    for message in messages:
        for call in message.get("tool_calls") or []:
            function = call.get("function", {})
            calls[call["id"]] = (function.get("name"), json.loads(function.get("arguments", "{}")))
        if message.get("role") == "tool":
            name, arguments = calls.get(message.get("tool_call_id"), (None, {}))
            try:
                result = json.loads(message.get("content") or "{}")
            except ValueError:
                result = {"ok": False, "error": "Tool returned non-JSON content"}
            records.append({"name": name, "arguments": arguments, "result": result})
    return records


def _calls(items):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": "acceptance_"+uuid4().hex[:12], "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}
        for name, args in items]}


def scripted_message(payload):
    """State-free fixture policy: derive edits from real tool outputs, never touch files."""
    messages = payload.get("messages", [])
    records = _tool_history(messages)
    is_child = any(str(m.get("content", "")).startswith("你是只读") and "子代理" in str(m.get("content", "")) for m in messages if m.get("role") == "system")
    if is_child:
        if not records:
            return _calls([("search_sources", {"query": "sample_count"})])
        result = _unwrap(records[-1]["result"])
        return {"role": "assistant", "content": "已读取定位资料；以下是原文检索结果（仍需主代理核对）："+json.dumps(result, ensure_ascii=False)}
    available = {t.get("function", {}).get("name") for t in payload.get("tools", [])}
    if not records:
        first = [("load_skill", {"skill_id": id}) for id in ("doc-pdf", "doc-spreadsheet", "doc-word")]
        first += [("read_file", {"source": FILES[0], "operation": "read", "arguments": {"pages": [1]}}),
                  ("read_file", {"source": FILES[1], "operation": "inspect"}),
                  ("read_file", {"source": FILES[2], "operation": "inspect"}),
                  ("search_sources", {"query": "sample_count"})]
        if "delegate" in available:
            first.append(("delegate", {"tasks": [{"role": "evidence", "goal": "只读检索requirements.pdf中的sample_count要求，给出完整source/hash/locator/quote，不改文件。"}]}))
        return _calls(first)
    for record in records:
        if record["result"].get("ok") is False:
            return {"role": "assistant", "content": "验收工具链有失败，未声称任务完成："+json.dumps(record["result"], ensure_ascii=False)}
    reads = [r for r in records if r["name"] == "read_file"]
    def read(source, operation):
        return next((_unwrap(r["result"]) for r in reversed(reads) if r["arguments"].get("source") == source and r["arguments"].get("operation", "read") == operation), None)
    word_inspect, sheet_inspect = read(FILES[2], "inspect"), read(FILES[1], "inspect")
    pdf_read = read(FILES[0], "read")
    if not all((word_inspect, sheet_inspect, pdf_read)):
        raise ValueError("Scripted provider needs actual inspect/read tool results")
    pdf_text = "\n".join(p["text"] for p in pdf_read["pages"])
    match = re.search(r"sample_count\s*=\s*(\d+)", pdf_text)
    if not match:
        raise ValueError("No source requirement was read from PDF")
    required = int(match.group(1))
    target_node = next(n for n in word_inspect["nodes"] if re.search(r"sample_count\s*=\s*\d+", n["text"]))
    sheet = next(s["name"] for s in sheet_inspect["sheets"] if s["name"] == "Counts")
    if read(FILES[2], "read") is None or read(FILES[1], "read") is None:
        return _calls([("read_file", {"source": FILES[2], "operation": "read", "arguments": {"block_ids": [target_node["id"]]}}),
                       ("read_file", {"source": FILES[1], "operation": "read", "arguments": {"sheet": sheet, "range": "A1:D3"}})])
    word_read, sheet_read = read(FILES[2], "read"), read(FILES[1], "read")
    old_text = next(b["text"] for b in word_read["blocks"] if b["id"] == target_node["id"])
    old_cell = next(c for row in sheet_read["rows"] for c in row if c["cell"] == "B2")
    search = next(_unwrap(r["result"]) for r in reversed(records) if r["name"] == "search_sources")
    evidence = next(h for h in search["hits"] if h.get("source") == FILES[0] and re.search(r"sample_count\s*=\s*"+str(required)+r"\b", h.get("quote", "")))
    word_patch = {"source": FILES[2], "expected_sha256": word_read["source_sha256"], "patches": [
        {"op": "replace_paragraph", "paragraph_id": target_node["id"], "expected_text": old_text,
         "text": re.sub(r"(sample_count\s*=\s*)\d+", lambda m: m.group(1)+str(required), old_text), "evidence": [evidence]}]}
    sheet_patch = {"source": FILES[1], "expected_sha256": sheet_read["source_sha256"], "patches": [
        {"op": "set_cell", "sheet": sheet, "cell": "B2", "expected": {"type": old_cell["type"], "value": old_cell["value"]},
         "value": {"type": "number", "value": required}, "evidence": [evidence]}]}
    plans = [word_patch, sheet_patch]
    previewed = {r["arguments"]["source"] for r in records if r["name"] == "preview_document" and r["result"].get("ok") is True}
    if any(p["source"] not in previewed for p in plans):
        return _calls([("preview_document", p) for p in plans])
    applied = {r["arguments"]["source"] for r in records if r["name"] == "apply_document" and r["result"].get("ok") is True}
    if any(p["source"] not in applied for p in plans):
        return _calls([("apply_document", p) for p in plans if p["source"] not in applied])
    return {"role": "assistant", "content": f"依据PDF原文，已将Word段落与Excel Counts!B2中的sample_count改为{required}，保存两个新副本，原件保留。修改有原件hash和PDF页引用；未进行Excel重算或视觉渲染，不能将结构回读视为工程签认。"}


def model_server(port=18889):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            body = json.dumps({"ok": True, "model": MODEL, "kind": "scripted_fixture_provider"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path not in ("/v1/chat/completions", "/chat/completions"):
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4_000_000:
                self.send_error(413)
                return
            try:
                payload = json.loads(self.rfile.read(length))
                message = scripted_message(payload)
                response = {"id": "scripted-"+uuid4().hex, "object": "chat.completion", "model": MODEL,
                            "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
                            "usage": {"prompt_tokens": max(1, length//4), "completion_tokens": max(1, len(json.dumps(message))//4)}}
                body = json.dumps(response, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
            except Exception as exc:
                body = json.dumps({"error": {"type": "scripted_fixture_error", "message": str(exc)}}, ensure_ascii=False).encode()
                self.send_response(422)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def http(base, path, body=None, *, binary=False, timeout=30):
    raw = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(base.rstrip("/")+path, data=raw, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read(50_000_001)
        if len(data) > 50_000_000:
            raise ValueError("HTTP response exceeds acceptance limit")
        return data if binary else json.loads(data)


def validate_outputs(workspace, meta, outputs):
    """Independent reopening: no calls to document worker or its validators."""
    from openpyxl import load_workbook
    required = meta["required_sample_count"]
    checks, details = {}, {}
    checks["originals_unchanged"] = all(sha256((workspace/name).read_bytes()).hexdigest() == sha for name, sha in meta["hashes"].items())
    for kind in ("docx", "xlsx"):
        candidates = [o for o in outputs if Path(o["name"]).suffix.lower() == "."+kind]
        checks[kind+"_artifact_exists"] = bool(candidates)
        valid = []
        for candidate in candidates:
            data = Path(candidate["download_path"]).read_bytes()
            expected_hash = candidate.get("output_sha256")
            hash_ok = bool(expected_hash) and sha256(data).hexdigest() == expected_hash
            before = package((workspace/("report.docx" if kind == "docx" else "quantities.xlsx")).read_bytes())
            after = package(data)
            allowed = {"word/document.xml"} if kind == "docx" else {"xl/workbook.xml", "xl/worksheets/sheet1.xml"}
            preserved = before.keys() == after.keys() and all(before[k] == after[k] for k in before if k not in allowed)
            if kind == "docx":
                root = ET.fromstring(after["word/document.xml"])
                paras = ["".join(n.text or "" for n in p.iter(f"{{{W}}}t")) for p in root.iter(f"{{{W}}}p")]
                exact = "sample_count="+str(required) in paras
                preserved = preserved and "Unchanged scope paragraph." in paras and "Specimen report" in paras
                target = next((p for p in root.iter(f"{{{W}}}p") if "".join(t.text or "" for t in p.iter(f"{{{W}}}t")) == "sample_count="+str(required)), None)
                style = target is not None and target.find(f"{{{W}}}r/{{{W}}}rPr/{{{W}}}b") is not None
            else:
                book = load_workbook(BytesIO(data), data_only=False)
                exact = book["Counts"]["B2"].value == required
                preserved = preserved and book["Counts"]["B3"].value == 99 and book["Counts"]["D2"].value == "=B2*2" and book["Unchanged"]["A1"].value == "Keep this sheet"
                style = book["Counts"]["B2"].font.bold and book["Counts"]["B2"].number_format == "0"
                book.close()
            item = {"artifact": candidate["name"], "required_value": required, "actual_value_matches": exact,
                    "unmodified_parts_preserved": preserved, "target_style_preserved": bool(style), "registered_hash_matches": hash_ok}
            valid.append(all((exact, preserved, style, hash_ok)))
            details.setdefault(kind, []).append(item)
        checks[kind+"_changed_and_preserved"] = any(valid)
    return {"passed": all(checks.values()), "checks": checks, "details": details,
            "rendering": "not_performed", "formula_recalculation": "not_performed", "engineering_approval": "not_evaluated"}


def acceptance(base, workspace, report_dir, *, live=False, timeout=360):
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Acceptance only connects to a loopback HTTP product server")
    report_dir.mkdir(parents=True, exist_ok=True)
    meta = fixture(workspace)
    report = {"mode": "live" if live else "scripted", "fixture": meta, "passed": False, "events_file": str(report_dir/"events.jsonl")}
    events = []
    try:
        capabilities = http(base, "/api/agent/capabilities")
        report["capabilities"] = capabilities
        if not live and capabilities.get("models", {}).get("model") != MODEL:
            raise ValueError("Default scripted acceptance requires model civil-scripted-acceptance; use --live explicitly for a real configured provider")
        if not capabilities.get("models", {}).get("configured"):
            raise ValueError("The product server has no configured model")
        registered = http(base, "/api/agent/workspaces", {"path": str(workspace.resolve())})
        workspace_id = registered["workspace"]["id"]
        session_id = "acceptance_"+uuid4().hex
        start = http(base, "/api/agent/turns", {"workspace": workspace_id, "session_id": session_id,
                    "message": TASK, "mode": "model", "sandbox": "workspace-write", "files": FILES,
                    "expert_id": "pm-daily"})  # Explicit human selection for this synthetic reporting task.
        turn_id = start["turn_id"]
        report.update(workspace_id=workspace_id, session_id=session_id, turn_id=turn_id)
        after, deadline = 0, time.monotonic()+timeout
        while True:
            query = urllib.parse.urlencode({"workspace": workspace_id, "session_id": session_id, "after_seq": after})
            snapshot = http(base, f"/api/agent/turns/{turn_id}/events?{query}")
            for event in snapshot.get("events", []):
                if event.get("seq", 0) > after:
                    events.append(event)
                    after = event["seq"]
            (report_dir/"events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False)+"\n" for e in events), encoding="utf-8")
            turn = snapshot["turn"]
            if turn.get("status") not in ("running", "cancelling") and after >= turn.get("last_seq", after):
                break
            if time.monotonic() >= deadline:
                http(base, f"/api/agent/turns/{turn_id}/cancel?"+urllib.parse.urlencode({"workspace": workspace_id, "session_id": session_id}), {})
                raise TimeoutError("Acceptance task exceeded its bounded wait and cancellation was requested")
            time.sleep(0.25)
        report["turn"] = turn
        outputs = []
        for artifact in (turn.get("result") or {}).get("artifacts", []):
            url = artifact.get("url", "")
            if not url.startswith("/api/agent/artifacts/"):
                raise ValueError("Unexpected artifact URL")
            data = http(base, url, binary=True)
            filename = Path(artifact["name"]).name
            downloaded = report_dir/filename
            downloaded.write_bytes(data)
            outputs.append({**artifact, "download_path": str(downloaded)})
        report["artifacts"] = outputs
        report["validation"] = validate_outputs(workspace, meta, outputs)
        names = [e.get("data", {}).get("name") for e in events if e.get("kind", e.get("event")) == "tool_finished"]
        report["tools_observed"] = names
        report["events_monotonic"] = all(events[i]["seq"] < events[i+1]["seq"] for i in range(len(events)-1))
        report["passed"] = turn.get("status") == "completed" and report["validation"]["passed"] and all(n in names for n in ("read_file", "search_sources", "preview_document", "apply_document")) and report["events_monotonic"]
    except Exception as exc:
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
    (report_dir/"report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve-model", action="store_true")
    parser.add_argument("--model-port", type=int, default=18889)
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--timeout", type=float, default=360)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if args.serve_model:
        server = model_server(args.model_port)
        print(f"Scripted model only: http://127.0.0.1:{server.server_port}/v1; model={MODEL}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    directory = (args.work_dir or ROOT/"work"/("unified-acceptance-"+time.strftime("%Y%m%d-%H%M%S")+"-"+uuid4().hex[:6])).resolve()
    reports = (args.report_dir or directory/"acceptance-report").resolve()
    report = acceptance(args.base, directory, reports, live=args.live, timeout=args.timeout)
    print(json.dumps({"passed": report["passed"], "mode": report["mode"], "report": str(reports/"report.json"), "error": report.get("error")}, ensure_ascii=False), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
