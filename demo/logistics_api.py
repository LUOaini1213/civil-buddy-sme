"""Local logistics intake, source ledger, explicit proposals and bounded solver host."""
from __future__ import annotations

import base64
from copy import deepcopy
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import Field, StrictInt
from starlette.concurrency import run_in_threadpool

try:
    import engineering_api as eng
    from planning_api import read_upload
except ImportError:
    from demo import engineering_api as eng
    from demo.planning_api import read_upload

cad = eng.cad


async def local_request(request: Request):
    await cad.local_request(request)
    import ipaddress
    client = request.client.host if request.client else ""
    host = request.url.hostname or ""
    testing = client == "testclient" and host == "testserver"
    try:
        local_client = ipaddress.ip_address(client).is_loopback
    except ValueError:
        local_client = False
    if not testing and (not local_client or host not in {"localhost", "127.0.0.1", "::1"}):
        raise HTTPException(403, "物流工作台仅接受本机回环地址的请求。")


router = APIRouter(dependencies=[Depends(local_request)])
BASE = "/api/logistics"
DOCUMENTS = cad.MemoryStore(max_items=20, max_bytes=96 * 1024 * 1024)
PROPOSALS = cad.MemoryStore(max_items=40, max_bytes=32 * 1024 * 1024)


class SaveIn(eng.Input):
    document_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    name: str = Field(min_length=1, max_length=100)


class ChatIn(eng.RevisionIn):
    message: str = Field(min_length=1, max_length=4000)
    compare_project_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    locale: Literal["zh-CN", "en"] | None = None


class ChangesIn(eng.RevisionIn):
    changes: list[dict] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=4000)


class ExportIn(eng.RevisionIn):
    format: Literal["json", "xlsx", "zip"]
    confirmation: str = Field(max_length=80)


class PackIn(eng.RevisionIn):
    mode: Literal["packaged", "materials"]
    container_type: Literal["20GP", "40GP", "40HQ", "45HQ"]
    max_containers: StrictInt = Field(ge=1, le=40)
    confirmation: str = Field(max_length=80)


def store():
    import os
    from demo.config import REPO_ROOT
    from packing_assistant.logistics.records import LogisticsStore
    return LogisticsStore(Path(os.environ.get("CIVIL_DOMAIN_WORKSPACE", str(REPO_ROOT))))


def project_at(ident, revision):
    project = eng.storage_call(store().open, ident)
    if revision != project["revision"]:
        raise HTTPException(409, "台账已被其他页面修改，请重新打开；当前输入未覆盖。")
    return project


def project_context(project):
    return {"project": {k: project[k] for k in ("id", "name", "revision", "can_undo", "confirmed")},
            **{k: deepcopy(project[k]) for k in ("document", "audit", "summary")}}


def register_proposal(context, proposal=None, action=None):
    from packing_assistant.logistics.agent import apply_proposal
    from packing_assistant.logistics.records import digest
    from packing_assistant.runtime.cancel import check
    project = project_at(context["project"]["id"], context["project"]["revision"])
    if digest(context["document"]) != digest(project["document"]):
        raise ValueError("上下文不对应当前台账。")
    if bool(proposal) == bool(action):
        raise ValueError("建议必须且只能包含一种操作。")
    if proposal:
        apply_proposal(project["document"], proposal)
        changes = proposal["changes"]
    else:
        if action != "undo" or not project["can_undo"]:
            raise ValueError("当前没有可撤销的修订。")
        changes = [{"field": "revision", "before": project["revision"], "after": project["undo_target_revision"]}]
    check()
    return PROPOSALS.put({"project": context["project"], "base_digest": digest(project["document"]), "proposal": proposal, "action": action, "changes": changes})


def public_proposal(ident):
    data = cached(PROPOSALS, ident)
    return {"proposal_id": ident, **{k: data[k] for k in ("project", "action", "changes")}, "proposal": data["proposal"]}


def permission(phrase):
    cad.require_export_permission()
    if not cad.is_confirmation(phrase, strip=False):
        raise HTTPException(403, "请完整输入签认确认句：" + cad.CONFIRMATION + "（或 / or: " + cad.CONFIRM_EN + "）")


def cached(cache, ident):
    try:
        return cache.get(ident)
    except HTTPException as exc:
        if exc.status_code == 410:
            raise HTTPException(410, "单据或修订建议缓存已过期、服务已重启；请重新打开已保存台账，未保存单据请重新上传。") from exc
        raise


def source_response(data, filename):
    ext = Path(filename).suffix.lower()
    mime = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".txt": "text/plain", ".csv": "text/plain"}.get(ext, "application/octet-stream")
    disposition = "inline" if ext in {".pdf", ".png", ".jpg", ".jpeg"} else "attachment"
    return Response(data, media_type=mime, headers={"Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(Path(filename).name)}", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"})


@router.get("/logistics")
def page():
    return FileResponse(Path(__file__).parent / "static/logistics.html", headers={"Cache-Control": "no-cache"})


@router.get(BASE + "/capabilities")
def capabilities():
    from packing_assistant.logistics.ocr import capability
    return {"ok": True, "formats": ["xlsx", "xlsm", "csv", "tsv", "json", "pdf", "png", "jpg", "jpeg"],
            "ocr": {**capability(), "note": "解释器存在不代表模型已就绪；按实际解析结果报告。"},
            "limits": {"source_bytes": 8 * 1024 * 1024, "bundle_bytes": 24 * 1024 * 1024}, "packing_modes": ["packaged", "materials"]}


def intake(data, filename, backend="auto", *, synthetic=False):
    from packing_assistant.logistics.intake import parse_document
    from packing_assistant.logistics.ledger import audit_document, summarize
    from packing_assistant.runtime.cancel import check
    try:
        document = parse_document(data, filename, ocr_backend=backend)
    except ImportError as exc:
        raise HTTPException(503, str(exc)) from exc
    if synthetic:
        document["extraction"]["synthetic"] = True
    check()
    ident = DOCUMENTS.put({"document": document, "data": base64.b64encode(data).decode()})
    return {"document_id": ident, "document": document, "audit": audit_document(document), "summary": summarize(document), "source_url": BASE + "/documents/" + ident + "/source"}


@router.get(BASE + "/example")
async def example(request: Request):
    data = ("package_id,material_id,name,package_count,quantity,units_per_package,unit,length_mm,width_mm,height_mm,dimension_scope,net_kg,gross_kg,weight_scope\n"
            "DEMO-01,M-DEMO,合成演示包装箱,1,10,10,件,1000,800,600,package,100,120,package\n").encode("utf-8-sig")
    return await cad.operation(request, lambda: {**intake(data, "synthetic-packing-list.csv", synthetic=True), "synthetic": True})


@router.post(BASE + "/upload")
async def upload(request: Request, ocr_backend: Literal["auto", "none", "paddleocr"] = "auto"):
    data, filename = await read_upload(request, 8 * 1024 * 1024)
    return await cad.operation(request, lambda: intake(data, filename, ocr_backend))


@router.get(BASE + "/documents/{ident}/source")
def draft_source(ident: str):
    item = cached(DOCUMENTS, ident)
    return source_response(base64.b64decode(item["data"]), item["document"]["source"]["filename"])


@router.get(BASE + "/projects")
def projects():
    return {"projects": eng.storage_call(store().list_projects)}


@router.post(BASE + "/projects")
async def save(request: Request):
    body = await cad.read_json(request, SaveIn)
    item = cached(DOCUMENTS, body.document_id)
    return await cad.operation(request, lambda: {"project": eng.storage_call(store().create, body.name, item["document"], base64.b64decode(item["data"]))})


@router.get(BASE + "/projects/{ident}")
def open_project(ident: str, version: int | None = None):
    if version is not None and version < 1:
        raise HTTPException(422, "历史版本号无效。")
    return {"project": eng.storage_call(store().open, ident, version)}


@router.get(BASE + "/projects/{ident}/source")
def project_source(ident: str, version: int | None = None):
    eng.storage_call(store().open, ident, version)
    data, filename = eng.storage_call(store().source, ident)
    return source_response(data, filename)


@router.post(BASE + "/projects/{ident}/conversation")
async def conversation(ident: str, request: Request):
    from packing_assistant.logistics.agent import execute, operation, compare_documents
    body = await cad.read_json(request, ChatIn)
    def work():
        project = project_at(ident, body.expected_revision)
        context = project_context(project)
        context["locale"] = body.locale
        if body.compare_project_id:
            other = eng.storage_call(store().open, body.compare_project_id)
            reply = "Compared the saved ledgers by explicit material IDs. Neither ledger was changed." if body.locale == "en" else "已按两个保存台账的明确材料编号对照；未修改台账。"
            return {"ok": True, "reply": reply, "comparison": compare_documents(project["document"], other["document"]), "comparison_revision": other["revision"]}
        result = execute(context, operation(body.message, context), {}, body.message)
        if result.get("logistics_proposal") or result.get("logistics_action"):
            pid = register_proposal(context, result.get("logistics_proposal"), result.get("logistics_action"))
            result.update(public_proposal(pid))
        return result
    return await cad.operation(request, work)


@router.post(BASE + "/projects/{ident}/propose")
async def propose(ident: str, request: Request):
    from packing_assistant.logistics.agent import propose_changes
    body = await cad.read_json(request, ChangesIn)
    def work():
        context = project_context(project_at(ident, body.expected_revision))
        pid = register_proposal(context, propose_changes(context["document"], body.changes, body.reason))
        return public_proposal(pid)
    return await cad.operation(request, work)


@router.get(BASE + "/proposals/{ident}")
def get_proposal(ident: str):
    return public_proposal(ident)


@router.post(BASE + "/proposals/{ident}/apply")
async def apply(ident: str, request: Request):
    from packing_assistant.logistics.agent import apply_proposal
    from packing_assistant.logistics.records import digest
    body = await cad.read_json(request, eng.RevisionIn)
    def work():
        item = cached(PROPOSALS, ident)
        if body.expected_revision != item["project"]["revision"]:
            raise HTTPException(409, "建议绑定的版本与所确认版本不符。")
        current = project_at(item["project"]["id"], body.expected_revision)
        if digest(current["document"]) != item["base_digest"]:
            raise HTTPException(409, "建议对应的台账内容已变化。")
        if item["action"] == "undo":
            result = eng.storage_call(store().undo, current["id"], body.expected_revision)
        else:
            doc = apply_proposal(current["document"], item["proposal"])
            result = eng.storage_call(store().revise, current["id"], body.expected_revision, doc, expected_digest=item["base_digest"])
        return {"project": result}
    return await cad.operation(request, work)


@router.post(BASE + "/projects/{ident}/confirm")
async def confirm(ident: str, request: Request):
    body = await cad.read_json(request, eng.RevisionIn)
    return await cad.operation(request, lambda: {"project": eng.storage_call(store().confirm, ident, body.expected_revision)})


@router.post(BASE + "/projects/{ident}/undo")
async def undo(ident: str, request: Request):
    body = await cad.read_json(request, eng.RevisionIn)
    return await cad.operation(request, lambda: {"project": eng.storage_call(store().undo, ident, body.expected_revision)})


@router.post(BASE + "/projects/{ident}/pack")
async def pack(ident: str, request: Request):
    from packing_assistant.logistics.packing import run_pack as calculate
    body = await cad.read_json(request, PackIn)
    permission(body.confirmation)
    def work():
        project = project_at(ident, body.expected_revision)
        result = calculate(project, body.mode, body.container_type, body.max_containers)
        current = project_at(ident, body.expected_revision)
        if not current["confirmed"]:
            raise HTTPException(409, "计算期间台账确认已变化，未发布结果。")
        return {"ok": bool(result.get("ok")), "result": result, "revision": project["revision"], "mode": body.mode}
    return await cad.operation(request, work)


@router.post(BASE + "/projects/{ident}/export")
async def export(ident: str, request: Request):
    from packing_assistant.logistics.bundle import export_bundle, export_ledger
    body = await cad.read_json(request, ExportIn)
    permission(body.confirmation)
    def work():
        project = project_at(ident, body.expected_revision)
        if body.format == "zip":
            record = eng.storage_call(store()._read, ident)
            if record["revision"] != body.expected_revision:
                raise HTTPException(409, "导出期间台账已修改。")
            data, mime, filename = export_bundle(record), "application/zip", "logistics-project.zip"
        else:
            data, mime, filename = export_ledger(project, body.format)
        project_at(ident, body.expected_revision)
        from packing_assistant.runtime.cancel import check
        check()
        return Response(data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})
    return await cad.operation(request, work)


@router.post(BASE + "/import")
async def import_project(request: Request):
    from packing_assistant.logistics.bundle import import_bundle, MAX_ZIP
    data, filename = await read_upload(request, MAX_ZIP)
    if Path(filename).suffix.lower() != ".zip":
        raise HTTPException(422, "完整项目导入请使用 ZIP；Excel / JSON 请从上传单据入口提取。")
    return await cad.operation(request, lambda: {"project": eng.storage_call(store().import_record, import_bundle(data))})
