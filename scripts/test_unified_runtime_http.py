#!/usr/bin/env python3
"""Manual/full acceptance against a real compiled Rust host and its Python sidecar.

  python scripts/test_unified_runtime_http.py --binary workbench/target/debug/civil-workbench.exe

Uses random loopback ports, synthetic inputs, fresh named accounts, a local scripted model,
and a fresh ignored output directory. Never connects to the user's running product or a real provider.
This is same-machine process/restart verification, not real-project or cross-computer acceptance.
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
import unified_acceptance as document_acceptance


def require(value, message):
    if not value:
        raise AssertionError(message)


def free_port():
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def request(base, path, body=None, *, token=None, cookie=None, form=False, content_type=None, timeout=30):
    headers = {"Origin": base, "Content-Type": content_type or ("application/x-www-form-urlencoded" if form else "application/json")}
    if token:
        headers["Authorization"] = "Bearer " + token
    if cookie:
        headers["Cookie"] = cookie
    raw = body if isinstance(body, bytes) else None if body is None else (urllib.parse.urlencode(body) if form else json.dumps(body)).encode("utf-8")
    message = urllib.request.Request(base + path, data=raw, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        response = opener.open(message, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        data = response.read(50_000_001)
        require(len(data) <= 50_000_000, "Response exceeds acceptance size limit")
        return response.status, response.headers, data


def json_request(base, path, body=None, *, token=None, cookie=None, expected=200):
    status, _headers, data = request(base, path, body, token=token, cookie=cookie)
    require(status == expected, f"{path}: expected HTTP {expected}, got {status}: {data[:300]!r}")
    return json.loads(data)


def port_closed(port):
    with socket.socket() as connection:
        connection.settimeout(0.25)
        return connection.connect_ex(("127.0.0.1", port)) != 0


def legacy_chat(instance, cookie, payload, output):
    status, headers, raw = request(instance.base, "/api/chat", payload, cookie=cookie, timeout=120)
    output.write_bytes(raw)
    require(status == 200 and "text/event-stream" in headers.get("Content-Type", ""),
            f"Legacy draft returned HTTP {status}: {raw[:300]!r}")
    events = []
    for block in raw.decode("utf-8").replace("\r\n", "\n").split("\n\n"):
        kind, data = "", []
        for line in block.splitlines():
            if line.startswith("event:"):
                kind = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if kind and data:
            events.append({"event": kind, "data": json.loads("\n".join(data))})
    require(not any(row["event"] == "error" for row in events), f"Legacy draft emitted an error; inspect {output}")
    done = [row["data"] for row in events if row["event"] == "done"]
    require(len(done) == 1, f"Legacy draft has no single completion; inspect {output}")
    return done[0]


def legacy_session_handoff(first, first_cookie, second, second_cookie, directory):
    """Legacy business sessions are portable ZIPs; /api/agent workspace turns are separate."""
    directory.mkdir()
    sid = "bundle-http-" + uuid4().hex[:16]
    filename = "site-record.txt"
    original = "合成验收资料，不代表真实工程。\n项目名称：HTTP交接试验\n部位：东侧试验段\n天气：多云\n".encode("utf-8")
    boundary = "civil-acceptance-" + uuid4().hex
    multipart = (f'--{boundary}\r\nContent-Disposition: form-data; name="session_id"\r\n\r\n{sid}\r\n'
                 f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{filename}"\r\n'
                 'Content-Type: text/plain; charset=utf-8\r\n\r\n').encode() + original + f"\r\n--{boundary}--\r\n".encode()
    status, _, raw = request(first.base, "/api/upload", multipart, cookie=first_cookie,
                             content_type="multipart/form-data; boundary=" + boundary)
    require(status == 200, f"Legacy attachment upload returned HTTP {status}: {raw[:300]!r}")
    upload = json.loads(raw)["files"][0]
    # Reference role deliberately selects the shared deterministic Python draft path.
    # A current confirmation in the original history must not authorize imported future work.
    message = "根据附件写一份项目日报模板，未提供的数据保留 UNSPECIFIED。\n我明白，将由持证人员签认"
    done = legacy_chat(first, first_cookie, {"session_id": sid, "message": message,
                       "expert_ids": ["pm-daily"], "attachments": [upload["id"]],
                       "attachment_roles": {upload["id"]: "reference"}}, directory / "source-draft.sse")
    require(done.get("wrote") is True and done.get("deliverables"), "Deterministic legacy draft did not produce artifacts")
    require(done.get("submit_blocked") is True, "Draft was incorrectly represented as releasable")
    before = json_request(first.base, "/api/sessions/" + sid, cookie=first_cookie)
    require(before.get("deliverables") and len(before.get("attachments", [])) == 1,
            "Legacy session details do not restore artifacts and selected attachments")

    def downloads(instance, cookie, session, attachments, deliverables):
        attachment_bytes, artifact_bytes = [], []
        for row in attachments:
            path = "/api/file?" + urllib.parse.urlencode({"session": session, "upload": row["id"]})
            code, _, body = request(instance.base, path, cookie=cookie)
            require(code == 200 and body, f"Attachment download failed with HTTP {code}")
            attachment_bytes.append((row["name"], body))
        for row in deliverables:
            path = "/api/file?" + urllib.parse.urlencode({"path": row["path"]})
            code, _, body = request(instance.base, path, cookie=cookie)
            require(code == 200 and body, f"Artifact download failed with HTTP {code}")
            artifact_bytes.append((row["name"], body))
        return Counter(attachment_bytes), Counter(artifact_bytes)

    originals = downloads(first, first_cookie, sid, before["attachments"], before["deliverables"])
    require(originals[0] == Counter([(filename, original)]), "Uploaded attachment bytes changed before export")
    foreign_file = "/api/file?" + urllib.parse.urlencode({"path": before["deliverables"][0]["path"]})
    require(request(second.base, foreign_file, cookie=second_cookie)[0] in (400, 403, 404),
            "Another named instance could read source artifacts without importing a package")
    status, headers, archive = request(first.base, f"/api/sessions/{sid}/export", cookie=first_cookie, timeout=120)
    require(status == 200 and "zip" in headers.get("Content-Type", ""), f"Session export failed with HTTP {status}")
    (directory / "legacy-session.zip").write_bytes(archive)
    with ZipFile(BytesIO(archive)) as zipped:
        require(zipped.testzip() is None, "Session export ZIP is corrupt")
        manifest = json.loads(zipped.read("bundle.json"))
        require(manifest["schema"] == "civil.session.bundle.v1" and manifest["source_session"] == sid,
                "Host returned an incomplete or unexpected session archive")
        for row in manifest["files"]:
            content = zipped.read(row["path"])
            require(len(content) == row["bytes"] and sha256(content).hexdigest() == row["sha256"], "Export descriptor checksum mismatch")
        bundled_uploads = Counter((row["name"], zipped.read(row["blob"])) for row in manifest["attachments"])
        bundled_artifacts = Counter((row["name"], zipped.read(row["blob"]))
                                    for run in manifest["runs"] for row in run.get("deliverables", []))
        require((bundled_uploads, bundled_artifacts) == originals, "Export archive omitted or changed original files")
    status, _, raw = request(second.base, "/api/session-import", archive, cookie=second_cookie,
                             content_type="application/zip", timeout=120)
    require(status == 200, f"Session import returned HTTP {status}: {raw[:300]!r}")
    imported = json.loads(raw)
    copied_sid = imported["session_id"]
    require(imported.get("ok") is True and imported.get("confirmation_reset") is True and copied_sid != sid,
            "Import did not create a new task with confirmation reset")
    restored = json_request(second.base, "/api/sessions/" + copied_sid, cookie=second_cookie)
    require(restored["transcript"] == before["transcript"], "Imported original conversation changed")
    require(restored["expert_ids"] == before["expert_ids"], "Imported selected expert changed")
    copied_uploads = restored["attachments"]
    require({row["id"] for row in copied_uploads}.isdisjoint({upload["id"]}), "Imported attachment identity was reused")
    require(restored["attachment_roles"] == {row["id"]: "reference" for row in copied_uploads}, "Attachment role mapping changed")
    require(downloads(second, second_cookie, copied_sid, copied_uploads, restored["deliverables"]) == originals,
            "Imported attachment or deliverable bytes changed")
    require(imported["attachments"] == len(copied_uploads) and imported["deliverables"] == len(restored["deliverables"]),
            "Import counts do not match restored files")
    # Inspect the actual persisted approval slot in addition to exercising the HTTP gate.
    summary = json.loads((second.private_root / "domains" / copied_sid / "session.summary.json").read_text(encoding="utf-8"))
    require(summary.get("p0_confirmed") is False, "Imported session retained source approval")
    denied = legacy_chat(second, second_cookie, {"session_id": copied_sid,
                         "message": "写一份消防专篇，缺失内容待填", "expert_ids": ["fire-protect"],
                         "attachments": [copied_uploads[0]["id"]],
                         "attachment_roles": {copied_uploads[0]["id"]: "reference"}}, directory / "imported-approval-gate.sse")
    require(denied.get("wrote") is False and denied.get("hitl_pending") is True and not denied.get("deliverables"),
            "Imported historical confirmation authorized a new high-risk draft")
    source_after = json_request(first.base, "/api/sessions/" + sid, cookie=first_cookie)
    require(source_after["transcript"] == before["transcript"] and
            downloads(first, first_cookie, sid, source_after["attachments"], source_after["deliverables"]) == originals,
            "Session handoff changed the source session")
    result = {"passed": True, "scope": "legacy_business_session_bundle", "synthetic": True,
              "source_session": sid, "imported_session": copied_sid, "different_named_instances": True,
              "attachments": len(copied_uploads), "deliverables": len(restored["deliverables"]),
              "archive_sha256": sha256(archive).hexdigest(), "attachment_and_artifact_bytes_equal": True,
              "source_artifact_cross_instance_access_blocked": True,
              "original_conversation_preserved": True, "source_unchanged": True, "confirmation_reset": True,
              "new_high_risk_operation_blocked": True, "rust_workspace_turns_included": False}
    (directory / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


class Instance:
    def __init__(self, binary, directory, state_root, workspace, user, model_port):
        self.binary, self.directory, self.state_root, self.workspace = binary, directory, state_root, workspace
        self.user, self.port, self.model_port = user, free_port(), model_port
        self.base = f"http://127.0.0.1:{self.port}"
        self.token = secrets.token_urlsafe(48)
        self.directory.mkdir(parents=True)
        self.workspace.mkdir(parents=True)
        self.token_file = self.directory / "login-token.txt"
        self.token_file.write_text(self.token, encoding="ascii")
        self.private_root = state_root / "accounts" / user / "projects" / sha256(os.path.normcase(str(workspace.resolve())).encode()).hexdigest()
        self.process = None
        self.log = None
        self.generation = 0
        self.domain_port = None

    def start(self):
        self.generation += 1
        self.stop_file = self.directory / f"stop-{self.generation}"
        self.log = (self.directory / f"launcher-{self.generation}.log").open("wb")
        environment = {key: value for key, value in os.environ.items()
                       if not key.upper().startswith(("CIVIL_", "OPENAI_", "ANTHROPIC_", "LLM_", "DEEPSEEK_", "ZAI_"))
                       and not key.upper().endswith("_API_KEY") and key not in {"PYTHONPATH", "PYTHONHOME"}}
        environment.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHON_DOTENV_DISABLED="1",
                           CIVIL_MODEL=document_acceptance.MODEL, CIVIL_API_KEY="scripted-local-fixture",
                           CIVIL_API_BASE=f"http://127.0.0.1:{self.model_port}/v1")
        command = [sys.executable, str(ROOT / "scripts/start_unified_workbench.py"), "--binary", str(self.binary),
                   "--python", sys.executable, "--state-root", str(self.state_root), "--workspace", str(self.workspace),
                   "--user-id", self.user, "--token-file", str(self.token_file), "--port", str(self.port),
                   "--stop-file", str(self.stop_file)]
        self.process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=self.log, stderr=subprocess.STDOUT,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            require(self.process.poll() is None, f"Launcher exited; inspect {self.directory} and {self.private_root}")
            try:
                status, _, _ = request(self.base, "/api/engineering/planning/capabilities", token=self.token, timeout=2)
                if status == 200:
                    log = (self.private_root / "domains.log").read_text(encoding="utf-8", errors="replace")
                    ports = re.findall(r"Uvicorn running on http://127\.0\.0\.1:(\d+)", log)
                    require(ports, "Cannot verify sidecar listener from startup log")
                    self.domain_port = int(ports[-1])
                    return
            except OSError:
                pass
            time.sleep(0.2)
        raise TimeoutError(f"Host/sidecar startup timed out; inspect {self.directory} and {self.private_root}")

    def stop(self):
        if self.process is None:
            return
        self.stop_file.write_text("stop", encoding="ascii")
        try:
            self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            # Exceptional cleanup only: terminate the owned process tree if its supervisor is stuck.
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"], capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
            else:
                self.process.terminate()
            self.process.wait(timeout=10)
            raise TimeoutError("Supervisor did not honor its shutdown sentinel")
        finally:
            self.log.close()
            self.process = None
        require(port_closed(self.port), "Rust host listener survived shutdown")
        if self.domain_port:
            require(port_closed(self.domain_port), "Python domain listener survived shutdown")

    def login(self):
        status, headers, _ = request(self.base, "/auth/login", {"token": self.token}, form=True)
        require(status in (302, 303), f"Login expected redirect, got {status}")
        full = headers.get("Set-Cookie", "")
        require("HttpOnly" in full and "SameSite=Strict" in full, "Login cookie lacks protections")
        cookie = full.split(";", 1)[0]
        json_request(self.base, "/api/agent/capabilities", cookie=cookie)
        return cookie


def run(binary, output):
    output.mkdir(parents=True, exist_ok=False)
    report = {"passed": False, "synthetic": True, "cross_computer": False, "real_provider_calls": False,
              "binary": str(binary), "checks": {}, "logs_root": str(output),
              "session_scopes": {"document_agent": "Rust /api/agent workspace turns and new document copies",
                                 "legacy_bundle": "Legacy /api/sessions ZIP transfers sources, artifacts and history; not Rust Agent workspace turns"}}
    provider = document_acceptance.model_server(0)
    worker = threading.Thread(target=provider.serve_forever, daemon=True)
    worker.start()
    first = Instance(binary, output / "alice", output / "state", output / "workspace-alice", "acceptance_alice", provider.server_port)
    second = Instance(binary, output / "bob", output / "state", output / "workspace-bob", "acceptance_bob", provider.server_port)
    try:
        first.start()
        require(request(first.base, "/api/agent/capabilities")[0] == 401, "Unauthenticated API must require login")
        require(request(first.base, "/auth/login", {"token": "invalid"}, form=True)[0] == 401, "Bad login was accepted")
        cookie = first.login()
        report["checks"]["login_and_cookie"] = True
        for page in ("/", "/cad", "/engineering/planning", "/logistics", "/packing"):
            status, headers, body = request(first.base, page, cookie=cookie)
            require(status == 200 and "html" in headers.get("Content-Type", "") and body, f"Page unavailable: {page}")
        for api in ("/api/cad/capabilities", "/api/engineering/planning/capabilities", "/api/logistics/capabilities", "/packing/api/health"):
            json_request(first.base, api, cookie=cookie)
        report["checks"]["domain_pages_and_apis"] = True
        plan = json_request(first.base, "/api/engineering/planning/example", cookie=cookie)["plan"]
        saved = json_request(first.base, "/api/engineering/planning/projects",
                             {"name": "Synthetic HTTP restart acceptance", "synthetic": True, "plan": plan}, cookie=cookie)["project"]
        project_path = "/api/engineering/planning/projects/" + saved["id"]
        first.stop()
        first.start()
        require(request(first.base, "/api/agent/capabilities", cookie=cookie)[0] == 401, "Pre-restart cookie was accepted")
        cookie = first.login()
        restored = json_request(first.base, project_path, cookie=cookie)["project"]
        for field in ("id", "revision", "name", "synthetic", "plan", "result", "method"):
            require(saved[field] == restored[field], f"Restart changed project field {field}")
        report["checks"]["project_restart_and_cookie_revocation"] = True
        report["project"] = {"id": saved["id"], "revision": saved["revision"], "synthetic": True}
        second.start()
        second_cookie = second.login()
        require(request(second.base, "/api/agent/capabilities", cookie=cookie)[0] == 401, "Another user's cookie was accepted")
        require(request(second.base, "/api/agent/capabilities", token=first.token)[0] == 401, "Another user's token was accepted")
        require(request(second.base, project_path, cookie=second_cookie)[0] in (403, 404), "Another user could read the project")
        require(json_request(second.base, "/api/engineering/planning/projects", cookie=second_cookie)["projects"] == [], "Another user's project appeared in listing")
        status, _, body = request(second.base, "/api/agent/workspaces", {"path": str(first.workspace)}, cookie=second_cookie)
        require(status in (400, 403) and "workspace is not allowed" in json.loads(body).get("detail", ""),
                f"Disallowed workspace registration was not rejected: HTTP {status}: {body[:300]!r}")
        workspaces = json_request(second.base, "/api/agent/workspaces", cookie=second_cookie)["workspaces"]
        require(not any(row.get("root") == str(first.workspace) for row in workspaces), "Disallowed workspace was persisted")
        report["checks"]["named_instance_isolation"] = True
        report["legacy_session_bundle"] = legacy_session_handoff(first, cookie, second, second_cookie, output / "legacy-bundle")
        report["checks"]["legacy_session_bundle_handoff"] = True
        second.stop()
        original_http = document_acceptance.http
        def authenticated_http(base, path, body=None, *, binary=False, timeout=30):
            status, _, data = request(base, path, body, token=first.token, timeout=timeout)
            require(200 <= status < 300, f"Document acceptance {path}: HTTP {status}: {data[:300]!r}")
            return data if binary else json.loads(data)
        document_acceptance.http = authenticated_http
        try:
            documents = document_acceptance.acceptance(first.base, first.workspace, output / "documents", timeout=240)
        finally:
            document_acceptance.http = original_http
        require(documents["passed"], f"Scripted document acceptance failed: {documents.get('error') or documents.get('validation')}")
        events = [json.loads(line) for line in (output / "documents/events.jsonl").read_text(encoding="utf-8").splitlines()]
        authorizations = [e["data"] for e in events if e.get("kind", e.get("event")) == "authorization"]
        require(authorizations and all(e.get("actor_id") == first.user and e.get("confirmation_scope") == "current_turn"
                                       and e.get("professional_signoff") is False for e in authorizations), "Missing or incorrect actor authorization audit")
        usage = documents["turn"]["result"].get("usage") or {}
        require(usage.get("model_calls", 0) > 0, "Missing model usage audit")
        report["checks"]["document_agent_originals_copies_actor_usage"] = True
        report["document_report"] = str(output / "documents/report.json")
        report["usage"] = usage
        first.stop()
        report["checks"]["both_processes_stopped"] = True
        report["passed"] = True
    except Exception as exc:
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        for instance in (second, first):
            try:
                instance.stop()
            except Exception as exc:
                report["passed"] = False
                report.setdefault("cleanup_errors", []).append(str(exc))
        provider.shutdown()
        provider.server_close()
        worker.join(timeout=3)
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True, help="Explicit freshly compiled Rust executable")
    parser.add_argument("--output", type=Path, help="Fresh output directory (must not exist)")
    args = parser.parse_args()
    binary = args.binary.resolve()
    if not binary.is_file():
        parser.error("--binary must name an existing executable")
    output = (args.output or ROOT / "output" / ("unified-runtime-http-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6])).resolve()
    report = run(binary, output)
    print(json.dumps({"passed": report["passed"], "report": str(output / "report.json"), "error": report.get("error")}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
