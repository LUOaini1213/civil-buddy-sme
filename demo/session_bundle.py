"""Portable task backups: validated data only, always restored as a new task."""
from __future__ import annotations

from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path, PureWindowsPath
import re
import shutil
import stat
import time
from uuid import uuid4
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

import projects
import uploads
from packing_assistant.sandbox import assert_open, assert_write, guarded_write_bytes, guarded_write_text

SCHEMA = "civil.session.bundle.v1"
MAX_BYTES = 128 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024
MAX_ENTRIES = 2048
MAX_RUNS = 512
MANIFEST_LIMIT = 8 * 1024 * 1024
_FILE = re.compile(r"files/[0-9]{6}\.bin\Z")
_EXTENSIONS = {".md", ".txt", ".json", ".csv", ".log", ".docx", ".xlsx", ".pdf"}
from packing_assistant.runtime.civil_config import scrub_confirmations  # noqa: E402
_HISTORICAL_NOTE = "从备份恢复的历史协作，结论未核验；仅供查看，不续跑，不代表当前授权。"


class BundleError(ValueError):
    pass


def _import_jurisdiction(transcript: list[dict]) -> str:
    """Rebuild the global slot only from explicit user region fields.

    Archive metadata, assistant replies, object rows, and unlabelled country
    mentions cannot supply a default for the imported task.
    """
    from packing_assistant.jurisdiction import infer_jurisdiction

    field = re.compile(r"(?:^|[；;。])\s*(?:(?:请)?(?:更正|纠正|修正|更新)(?:一下)?\s*[:：]?\s*)?"
                       r"(?:辖区|适用辖区|适用地区|jurisdiction)\s*[:：=]\s*([^；;。\r\n]+)", re.I)
    objects = re.compile(r"(?:会议名称|构件编号|构件名称|构件|单体名称|单体|对象名称|对象|分区|房间|"
                         r"系统名称|系统|设备名称|设备|道路名称|路段名称|桥梁名称|桥名|隧道名称|码头名称|问题编号)\s*[:：=]")
    result = "UNSPECIFIED"
    for turn in transcript:
        if turn.get("role") != "user":
            continue
        values = []
        for line in turn.get("text", "").splitlines():
            if "|" in line or objects.search(line):
                continue
            values.extend(infer_jurisdiction(match[1]) for match in field.finditer(line))
        if values:
            regions = set(values)
            # Ambiguous mixed or explicitly withdrawn facts do not silently
            # reuse an older region. A later user field may establish it anew.
            result = next(iter(regions)) if len(regions) == 1 else (
                "DUAL" if "UNSPECIFIED" not in regions else "UNSPECIFIED")
    return result


def _text(value, limit=16000):
    if not isinstance(value, str) or len(value) > limit:
        raise BundleError("备份协作文字格式或长度无效")
    return scrub_confirmations(value, "[历史确认不生效]")


def _rows(value, limit=512):
    if not isinstance(value, list) or len(value) > limit:
        raise BundleError("备份协作列表无效或过长")
    return value


def _strings(value, limit=512):
    return [_text(item) for item in _rows(value, limit)]


def _historical_route(value):
    if not isinstance(value, dict):
        raise BundleError("备份路由格式无效")
    steps = []
    for row in _rows(value.get("steps", []), 32):
        steps.append({key: _text(row.get(key, ""), 256) for key in ("id", "expert_id", "label")}
                     | {"depends_on": _strings(row.get("depends_on", []), 32)})
    candidates = [{"expert_ids": [_text(x, 128) for x in _rows(row.get("expert_ids", []), 128)],
                   "label": _text(row.get("label", ""), 256), "reason": _text(row.get("reason", ""))}
                  for row in _rows(value.get("historical_candidates", value.get("candidates", [])), 32)]
    return {"expert_ids": [_text(x, 128) for x in _rows(value.get("expert_ids", []), 128)],
            "workflow": "", "original_workflow": _text(value.get("original_workflow", value.get("workflow", "")), 128),
            "intent": _text(value.get("intent", "chat"), 32), "ambiguous": False,
            "original_ambiguous": bool(value.get("original_ambiguous", value.get("ambiguous"))),
            "candidates": [], "historical_candidates": candidates, "steps": steps,
            "reason": _HISTORICAL_NOTE + " " + _text(value.get("reason", "")).removeprefix(_HISTORICAL_NOTE).strip(),
            "historical": True, "restored": True, "executable": False}


def _portable_collaboration(value, artifacts):
    """Only declared document references travel; engine paths are not import input."""
    result = deepcopy(value)
    by_name = {}
    for index, item in enumerate(artifacts):
        by_name.setdefault(item.get("name"), []).append(index)
    for owner in [result, *result.get("children", [])]:
        files = []
        for item in owner.get("files", []):
            matches = by_name.get(item.get("name"), [])
            row = {"name": item.get("name", ""), "tool": item.get("tool", "")}
            # Ambiguous names remain in the run's download list, without guessing
            # which worker owns the artifact (e.g. two model-analysis.md files).
            if len(matches) == 1:
                row["artifact_ref"] = matches[0]
            files.append(row)
        owner["files"] = files
    return result


def _historical_collaboration(value, sid, artifacts=(), documents=()):
    """Rebuild a bounded display snapshot, never a runnable workflow manifest.

    Foreign source IDs and paths are labels only. Evidence is linked only when
    its literal quote has one unique location in the imported selected sources.
    """
    if not isinstance(value, dict) or len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > 2 * 1024 * 1024:
        raise BundleError("备份协作记录无效或过大")
    parent = "import-collab-" + uuid4().hex[:16]
    refs = {}

    def evidence(row):
        if not isinstance(row, dict):
            raise BundleError("备份协作来源格式无效")
        quote = _text(row.get("quote", ""))
        old_id = _text(row.get("source_id", ""), 512)
        matches = []
        for document in documents:
            start = document["text"].find(quote) if quote else -1
            if start >= 0 and document["text"].find(quote, start + 1) < 0:
                matches.append((document, start))
        item = {"quote": quote, "requirement_ref": _text(row.get("requirement_ref", ""), 256),
                "historical": True, "verified": False, "source_unavailable": len(matches) != 1}
        for key in ("kind", "category"):
            if key in row:
                item[key] = _text(row[key], 128)
        if len(matches) == 1:
            document, start = matches[0]
            item.update(source_id=document["source_id"], title=document["title"], start=start, end=start + len(quote))
            refs.setdefault(old_id, set()).add(document["source_id"])
        else:
            item.update(source_id="", title="历史引文（本地来源未唯一定位）")
        return item

    def comparison(rows):
        return [{"requirement_ref": _text(row.get("requirement_ref", ""), 256),
                 "requirement": _text(row.get("requirement", "")),
                 "status": (row.get("status") if row.get("status") in {"candidate_requires_review", "conflict_requires_review",
                                                                       "not_matched", "not_provided"}
                            else "candidate_requires_review" if row.get("response_evidence") else "not_provided"),
                 "original_status": _text(row.get("status", ""), 128), "verified": False,
                 "kinds": [_text(kind, 64) for kind in _rows(row.get("kinds", []), 16)],
                 "conflicts": [{key: _text(item.get(key, ""), 512) for key in ("label", "required", "offered", "note")}
                               for item in _rows(row.get("conflicts", []), 32) if isinstance(item, dict)],
                 "response_evidence": [evidence(e) for e in _rows(row.get("response_evidence", []))]}
                for row in _rows(rows)]

    def files(rows):
        result = []
        for row in _rows(rows):
            name = _name(row.get("name"))
            index = row.get("artifact_ref")
            if type(index) is int and 0 <= index < len(artifacts) and artifacts[index]["name"] == name:
                result.append({**artifacts[index], "tool": _text(row.get("tool", ""), 128)})
            else:
                result.append({"name": name, "available": False, "note": "请从任务交付物列表核对同名文件"})
        return result

    children = []
    originals = _rows(value.get("children", []), 16)
    for index, row in enumerate(originals):
        children.append({"task_id": parent + "-" + str(index + 1), "parent_run_id": parent,
            "skill": _text(row.get("skill", ""), 128), "status": "restored",
            "original_status": _text(row.get("original_status", row.get("status", "")), 128),
            "historical": True, "verified": False, "files": files(row.get("files", [])),
            "evidence": [evidence(e) for e in _rows(row.get("evidence", []))],
            "unresolved": _strings(row.get("unresolved", [])),
            "response_comparison": comparison(row.get("response_comparison", []))})
    original_review = value.get("review", {})
    if not isinstance(original_review, dict):
        raise BundleError("备份协作复核格式无效")
    gaps = []
    for row in _rows(original_review.get("gaps", [])):
        if not isinstance(row, dict):
            raise BundleError("备份协作缺项格式无效")
        gaps.append({key: _text(row.get(key, "")) for key in ("req_id", "title", "status")}
                    | {"response_evidence": [evidence(e) for e in _rows(row.get("response_evidence", []))]})
    review = {"gaps": gaps, "conflicts": [{"field": _text(row.get("field", ""), 256),
               "note": _text(row.get("note", "")), "status": "needs_review"}
               for row in _rows(original_review.get("conflicts", []))],
              "response_comparison": comparison(original_review.get("response_comparison", [])),
              "forbidden_hits": _strings(original_review.get("forbidden_hits", [])),
              "historical": True, "verified": False, "handoff_unchanged": False}
    review["n_gaps"] = len(gaps)
    review["evidence_count"] = len({(e["source_id"], e.get("start"), e.get("end"))
                                    for child in children for e in child["evidence"] if not e["source_unavailable"]})
    for row, child in zip(originals, children):
        conclusions = []
        for entry in _rows(row.get("conclusions", []), 32):
            originals_refs = _strings(entry.get("evidence_refs", []), 128)
            conclusions.append({"text": _text(entry.get("text", "")), "origin": "imported",
                "verified": False, "historical": True,
                "evidence_refs": sorted({ref for key in originals_refs for ref in refs.get(key, ())}),
                "source_unavailable": any(not refs.get(key) for key in originals_refs) or not originals_refs})
        child["conclusions"] = conclusions
    metrics = value.get("aggregate_metrics", value.get("metrics", {}))
    if not isinstance(metrics, dict):
        raise BundleError("备份协作预算格式无效")
    safe_metrics = {key: metrics[key] for key in ("duration_ms", "limit", "reserved_tokens", "input_tokens", "output_estimated", "model_calls")
                    if type(metrics.get(key)) is int and 0 <= metrics[key] <= 10**12}
    safe_metrics.update(historical=True, estimated=True, counter="utf8-bytes")
    return {"schema": "civil.tender.workflow.v1", "parent_run_id": parent, "session_id": sid,
            "state": "restored", "original_state": _text(value.get("original_state", value.get("state", "")), 128),
            "ok": False, "historical": True, "restored": True, "active": False, "resumable": False,
            "verified": False, "submit_blocked": True, "reply": _HISTORICAL_NOTE,
            "original_reply": _text(value.get("original_reply", value.get("reply", ""))),
            "children": children, "review": review, "aggregate_metrics": safe_metrics,
            "files": files(value.get("files", []))}


def _read(path: Path, root: Path, limit: int = MAX_EXPANDED) -> bytes:
    resolved = assert_open(path)
    if not resolved.is_relative_to(root.resolve()) or path.is_symlink():
        raise BundleError("备份文件不在当前任务目录内")
    if resolved.stat().st_size > limit:
        raise BundleError("任务数据超过备份大小上限")
    return resolved.read_bytes()


def _name(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 160:
        raise BundleError("备份文件名无效")
    path = PureWindowsPath(value)
    if (path.name != value or path.is_reserved() or value.endswith((".", " "))
            or any(ord(c) < 32 or c in '<>:"/\\|?*' for c in value)):
        raise BundleError("备份文件名无效")
    if path.suffix.lower() not in _EXTENSIONS:
        raise BundleError("备份中包含不支持的文书格式")
    return value


def _restore_budget(manifest: dict, content: dict[str, bytes]) -> None:
    """Bound materialized output, including repeated references and metadata."""
    runs, attachments = manifest["runs"], manifest["attachments"]
    if len(runs) > MAX_RUNS:
        raise BundleError("备份运行记录过多，请拆分任务")
    artifacts = [item for run in runs for item in run.get("deliverables", [])]
    files = 4 + len(runs) + len(artifacts) + 3 * len(attachments)
    if files > MAX_ENTRIES:
        raise BundleError("恢复后的任务文件过多，请拆分任务")
    # Count each write, even when multiple records reference one ZIP entry.
    copied = sum(len(content[item["blob"]]) for item in [*artifacts, *attachments])
    metadata = len(json.dumps(manifest, ensure_ascii=False).encode("utf-8"))
    # Reserve space for generated IDs/paths, metadata and extracted text caches.
    overhead = files * 8192 + len(attachments) * uploads.MAX_TEXT_CHARS * 4
    if copied + metadata + overhead > MAX_EXPANDED:
        raise BundleError("恢复后的任务数据超过大小上限，请拆分任务")


def export_session(root: Path, sid: str, archive_target=None):
    """Build the backup. archive_target=None returns the zip as bytes (tests, small sessions);
    a path writes the zip there so the HTTP route can stream the file instead of holding a
    second copy in memory."""
    from chat_service import read_runs, valid_session

    valid_session(sid)
    directory = root / sid
    metadata = directory / "session.meta.json"
    if not metadata.is_file():
        raise BundleError("当前任务尚无可备份记录")
    meta = json.loads(_read(metadata, root, MANIFEST_LIMIT))
    transcript_path = directory / "transcript.jsonl"
    transcript = [json.loads(line) for line in _read(transcript_path, root, MANIFEST_LIMIT).decode("utf-8").splitlines() if line.strip()] if transcript_path.exists() else []
    summary_path = directory / "session.summary.json"
    summary = json.loads(_read(summary_path, root, MANIFEST_LIMIT)) if summary_path.exists() else {}
    content: dict[str, bytes] = {}
    descriptors = []
    size = 0

    def add(data: bytes) -> str:
        nonlocal size
        size += len(data)
        if size > MAX_EXPANDED or len(content) >= MAX_ENTRIES - 1:
            raise BundleError("任务数据超过备份大小或文件数量上限")
        key = f"files/{len(content):06d}.bin"
        content[key] = data
        descriptors.append({"path": key, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        return key

    runs = deepcopy(read_runs(root, sid))
    for run in runs:
        if run.get("collaboration"):
            run["collaboration"] = _portable_collaboration(run["collaboration"], run.get("deliverables", []))
        for artifact in run.get("deliverables", []):
            name = _name(artifact.get("name"))
            path = Path(artifact["path"])
            artifact["blob"] = add(_read(path, directory))
            artifact["name"] = name
            artifact.pop("path", None)
    attachment_rows = []
    for attachment in uploads.list_uploads(sid):
        raw_path = uploads.session_uploads_dir(sid) / (attachment["id"] + ".bin")
        attachment_rows.append({"id": attachment["id"], "name": attachment["name"],
                                "blob": add(_read(raw_path, uploads.UPLOAD_ROOT, uploads.MAX_BYTES))})
    registry = projects.load_registry(root)
    project = next((p for p in registry["projects"] if p.get("id") == meta.get("project_id")), {})
    manifest = {"schema": SCHEMA, "source_session": sid, "title": meta.get("title") or sid,
                "project_name": project.get("name", ""), "transcript": transcript,
                "jurisdiction": summary.get("jurisdiction", "UNSPECIFIED"),
                "runs": runs, "attachments": attachment_rows, "files": descriptors}
    parent_path = directory / "collaboration.summary.json"
    if parent_path.exists():
        manifest["collaboration_summary"] = json.loads(_read(parent_path, root, 32_000))
    _restore_budget(manifest, content)
    encoded = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    if len(encoded) > MANIFEST_LIMIT:
        raise BundleError("任务记录超过备份大小上限")
    if archive_target is None:
        with BytesIO() as buffer:
            _write_zip(buffer, encoded, content)
            result = buffer.getvalue()
        if len(result) > MAX_BYTES:
            raise BundleError("备份包超过 128 MB，请拆分任务")
        return result
    target = Path(archive_target)
    with open(target, "wb") as fh:
        _write_zip(fh, encoded, content)
    if target.stat().st_size > MAX_BYTES:
        target.unlink(missing_ok=True)
        raise BundleError("备份包超过 128 MB，请拆分任务")
    return target


def _write_zip(fileobj, encoded: bytes, content: dict) -> None:
    with ZipFile(fileobj, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("bundle.json", encoded)
        for name, data in content.items():
            archive.writestr(name, data)


def _validated(data) -> tuple[dict, dict[str, bytes]]:
    size = data.stat().st_size if isinstance(data, Path) else len(data)
    if size > MAX_BYTES:
        raise BundleError("备份包不能超过 128 MB")
    try:
        with ZipFile(data if isinstance(data, Path) else BytesIO(data)) as archive:
            entries = archive.infolist()
            names = [item.filename for item in entries]
            if (len(entries) > MAX_ENTRIES or len(names) != len(set(names))
                    or sum(item.file_size for item in entries) > MAX_EXPANDED):
                raise BundleError("备份包文件数量或解压大小无效")
            for item in entries:
                if (item.filename != "bundle.json" and not _FILE.fullmatch(item.filename)
                        or item.flag_bits & 1 or stat.S_ISLNK(item.external_attr >> 16)):
                    raise BundleError("备份包包含无效路径或链接")
            if archive.getinfo("bundle.json").file_size > MANIFEST_LIMIT:
                raise BundleError("备份清单过大")
            manifest = json.loads(archive.read("bundle.json"))
            if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
                raise BundleError("不是 Civil Buddy 任务备份")
            descriptors = manifest.get("files")
            if not isinstance(descriptors, list):
                raise BundleError("备份文件清单无效")
            content = {}
            for item in descriptors:
                name = item["path"]
                if not _FILE.fullmatch(name) or name in content:
                    raise BundleError("备份文件清单无效")
                raw = archive.read(name)
                if len(raw) != item["bytes"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
                    raise BundleError("备份文件校验失败")
                content[name] = raw
            if set(names) != {"bundle.json", *content}:
                raise BundleError("备份文件与清单不一致")
        if not isinstance(manifest.get("title"), str) or not manifest["title"].strip():
            raise BundleError("备份标题无效")
        for key in ("transcript", "runs", "attachments"):
            if not isinstance(manifest.get(key), list):
                raise BundleError("备份记录格式无效")
        if not isinstance(manifest.get("jurisdiction", "UNSPECIFIED"), str):
            raise BundleError("备份辖区格式无效")
        _restore_budget(manifest, content)
        for turn in manifest["transcript"]:
            if turn.get("role") not in {"user", "assistant"} or not isinstance(turn.get("text"), str):
                raise BundleError("备份对话记录无效")
            if len(turn["text"].encode("utf-8")) > projects.HISTORY_MESSAGE_MAX_BYTES:
                raise BundleError("备份中的单条对话超过大小上限")
            if "ts" not in turn:
                turn["ts"] = 0  # Legacy bundles without a clock remain readable.
            if type(turn["ts"]) is not int or turn["ts"] < 0:
                raise BundleError("备份对话时间戳无效")
        if len(manifest["attachments"]) > uploads.MAX_FILES:
            raise BundleError("备份附件过多")
        attachment_ids = set()
        for row in manifest["attachments"]:
            if not re.fullmatch(r"[a-f0-9]{12}", row["id"]) or row["id"] in attachment_ids:
                raise BundleError("备份附件标识无效")
            attachment_ids.add(row["id"])
            uploads.extract_upload(_name(row["name"]), content[row["blob"]])
        for run in manifest["runs"]:
            if not isinstance(run, dict) or not isinstance(run.get("deliverables", []), list):
                raise BundleError("备份运行记录无效")
            if not isinstance(run.get("nodes", []), list) or not isinstance(run.get("attachments", []), list):
                raise BundleError("备份运行记录无效")
            for key in ("mtime", "intent", "expert_id", "error_code", "state"):
                if key in run and not isinstance(run[key], str):
                    raise BundleError("备份运行记录类型无效")
            if len(run.get("expert_id", "")) > 128:
                raise BundleError("备份岗位标识过长")
            if any(key in run and type(run[key]) is not bool for key in ("ok", "hitl_pending", "cancelled")):
                raise BundleError("备份运行状态类型无效")
            if (not isinstance(run.get("expert_ids", []), list) or len(run.get("expert_ids", [])) > 128
                    or not all(isinstance(x, str) and len(x) <= 128 for x in run.get("expert_ids", []))):
                raise BundleError("备份岗位列表无效")
            if any(not isinstance(node, dict) or any(not isinstance(node.get(key, ""), str) for key in ("kind", "title", "detail")) for node in run.get("nodes", [])):
                raise BundleError("备份审计记录无效")
            if any(value not in attachment_ids for value in run.get("attachments", [])):
                raise BundleError("备份缺少选中的附件")
            roles = run.get("attachment_roles", {})
            if not isinstance(roles, dict) or any(key not in run.get("attachments", []) or role not in {"tender", "response", "reference"}
                                                 for key, role in roles.items()):
                raise BundleError("备份附件用途必须属于该轮所选资料")
            if "route" in run:
                _historical_route(run["route"])
            if run.get("collaboration") is not None:
                _historical_collaboration(run["collaboration"], "validated")
            for artifact in run.get("deliverables", []):
                _name(artifact["name"])
                if not isinstance(artifact.get("expert", ""), str) or len(artifact.get("expert", "")) > 128:
                    raise BundleError("备份文书岗位标识无效")
                if artifact["blob"] not in content:
                    raise BundleError("备份缺少文书文件")
        if manifest.get("collaboration_summary") is not None:
            _historical_collaboration(manifest["collaboration_summary"], "validated")
        return manifest, content
    except BundleError:
        raise
    except (BadZipFile, KeyError, TypeError, ValueError, AttributeError, RuntimeError, NotImplementedError) as exc:
        raise BundleError("备份包损坏或格式不完整") from exc


def import_session(root: Path, data) -> dict:
    """data: the zip as bytes, or a Path to it on disk (the HTTP route spools the upload to a file)."""
    from packing_assistant.runtime.civil_config import load_config

    if not load_config().allow_write():
        raise PermissionError("当前为只读模式，不能导入任务")
    manifest, content = _validated(data)  # Validate everything before creating a directory.
    sid = "import-" + uuid4().hex[:20]
    target = assert_write(root / sid)
    if target.exists():  # attachments live inside the session directory now
        raise BundleError("新任务目录冲突，请重试")
    target.mkdir(parents=True, exist_ok=False)
    try:
        mapping = {}
        for row in manifest["attachments"]:
            saved = uploads.save_upload(sid, row["name"], content[row["blob"]])
            mapping[row["id"]] = saved["id"]
        transcript = manifest["transcript"]
        guarded_write_text(target / "transcript.jsonl", "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in transcript))
        # Derive identifiers exactly as the local index will; none are accepted
        # from the archive. Sources remain unavailable until persist succeeds.
        from local_retrieval import _source_id
        history_sources = [{"source_id": _source_id(sid, "history", row["id"]), "text": row["content"],
                            "title": f"导入对话 · 第 {i + 1} 条"}
                           for i, row in enumerate(projects.read_full_history(root, sid))]
        attachment_sources = {row["id"]: {"source_id": _source_id(sid, "attachment", row["id"]),
                                         "text": row["text"], "title": row["name"]}
                              for row in uploads.extracted_documents(sid, list(mapping.values()))}
        latest_collaboration = None
        for original in manifest["runs"]:
            run = {key: original[key] for key in ("schema", "mtime", "intent", "expert_id", "expert_ids", "ok", "hitl_pending", "nodes", "error_code", "state", "cancelled") if key in original}
            rid = uuid4().hex
            run.update(run_id=rid, attachments=[mapping[x] for x in original.get("attachments", [])], deliverables=[])
            run["attachment_roles"] = {mapping[key]: role for key, role in original.get("attachment_roles", {}).items()}
            if "route" in original:
                run["route"] = _historical_route(original["route"])
            run.setdefault("mtime", "")
            for index, artifact in enumerate(original.get("deliverables", [])):
                path = target / "deliverables" / rid / f"{index + 1}-{artifact['name']}"
                guarded_write_bytes(path, content[artifact["blob"]])
                run["deliverables"].append({"name": artifact["name"], "path": str(path),
                                            "expert": str(artifact.get("expert", run.get("expert_id", ""))), "run_id": rid})
            if original.get("collaboration") is not None:
                sources = [*history_sources, *[attachment_sources[key] for key in run["attachments"] if key in attachment_sources]]
                latest_collaboration = _historical_collaboration(original["collaboration"], sid, run["deliverables"], sources)
                run.update(collaboration=latest_collaboration, state="restored", historical=True,
                           original_state=original.get("original_state", original.get("state", "")), hitl_pending=False)
            guarded_write_text(target / "runs" / rid / "workbench.json", json.dumps(run, ensure_ascii=False))
        if latest_collaboration is None and manifest.get("collaboration_summary") is not None:
            latest_collaboration = _historical_collaboration(manifest["collaboration_summary"], sid)
        if latest_collaboration is not None:
            parent = {"schema": "civil.collaboration.memory.v1", "run_id": latest_collaboration["parent_run_id"],
                      "state": "restored", "historical": True, "verified": False, "resumable": False,
                      "note": _HISTORICAL_NOTE, "children": [{key: child[key] for key in ("skill", "status", "conclusions", "unresolved")}
                                                           for child in latest_collaboration["children"]],
                      "review": latest_collaboration["review"]}
            if len(json.dumps(parent, ensure_ascii=False).encode("utf-8")) > 8000:
                parent.pop("children")
                parent.pop("review")
                parent["note"] += " 详细内容见本任务历史协作记录。"
            guarded_write_text(target / "collaboration.summary.json", json.dumps(parent, ensure_ascii=False))
        zone = _import_jurisdiction(transcript)
        guarded_write_text(target / "session.summary.json", json.dumps({"jurisdiction": zone, "p0_confirmed": False}, ensure_ascii=False))
        # Rebuild derived memory/index from validated full records with NEW task IDs.
        # Imported summaries can never grant approval or carry another task's paths.
        from session_context import persist
        persist(root, sid)
        # Metadata is the visibility marker; publish only after every file is saved.
        title = manifest["title"][:projects.TITLE_MAX]
        meta = {"schema": projects.SCHEMA_SESSION_META, "session_id": sid, "title": title,
                "title_source": "manual", "project_id": projects.INBOX_ID, "project_source": "manual",
                "created_at": int(time.time()), "updated_at": int(time.time()), "turns": sum(t["role"] == "user" for t in transcript),
                "imported_project_name": str(manifest.get("project_name", ""))[:projects.NAME_MAX]}
        guarded_write_text(target / "session.meta.json", json.dumps(meta, ensure_ascii=False))
        return {"ok": True, "session_id": sid, "title": title, "attachments": len(mapping),
                "deliverables": sum(len(r.get("deliverables", [])) for r in manifest["runs"]),
                "project_name": meta["imported_project_name"], "confirmation_reset": True}
    except Exception:
        # Only the freshly reserved directories are removed, never an existing task.
        directory, base = target, root  # uploads/ is inside target
        if directory.resolve().parent == base.resolve() and directory.name == sid and not directory.is_symlink():
            if directory.exists():
                shutil.rmtree(directory)
        raise
