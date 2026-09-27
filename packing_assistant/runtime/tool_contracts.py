"""Versioned JSON Schema contracts shared by tool execution and MCP discovery.

The validator implements the small JSON Schema vocabulary used below. Unknown
keys are rejected for built-in tool inputs so misspelled arguments cannot be
silently ignored. Business missing values remain explicit in the draft.
"""
from __future__ import annotations

from copy import deepcopy
import math


def obj(properties=None, required=(), *, extra=False):
    return {"type": "object", "properties": properties or {},
            "required": list(required), "additionalProperties": extra}


TEXT = {"type": "string"}
BOOL = {"type": "boolean"}
MAP = {"type": "object"}
FILE = obj({"name": TEXT, "path": TEXT, "tool": TEXT}, ("path",), extra=True)
FILES = {"type": "array", "items": FILE}
COMMON = {"session_id": {"type": "string", "minLength": 1, "maxLength": 32},
          "text": TEXT, "task": TEXT, "intent": {"type": "string", "enum": ["chat", "run", "both"]},
          "expert_id": TEXT, "confirm_ok": BOOL, "p0_confirmed": BOOL,
          "jurisdiction": TEXT, "packing_summary": {"type": ["object", "null"]}}
MATERIAL_FIELDS = (
    "window constraints works jobs milestones trades labor plant equipment material materials items package "
    "samples notice reply_points work_item hazards controls inspection_lot site issues scenario certs item "
    "vendors vendor criteria period work_today watchouts note notes progress weather attendance resources "
    "hse role duties salary pay qualifications interview"
).split()
MATERIAL = {"type": ["string", "number", "array", "object"]}


def contract_for(name: str, *, exclusive: bool = False) -> dict:
    if name in {"jpj.catalog", "jpj.query", "literature.catalog", "literature.search"}:
        from packing_assistant.tools.readonly_sources import contract_for_source
        return contract_for_source(name)
    properties = deepcopy(COMMON)
    output = obj({"ok": BOOL}, extra=True)
    required = ()
    if exclusive and not name.startswith("pack-ship__") and name not in {"tender.packing_link", "read_link_record"}:
        properties.update({key: deepcopy(MATERIAL) for key in MATERIAL_FIELDS})
        properties["has_trial_data"] = BOOL
        output = obj({"wrote": BOOL, "files": FILES, "submit_blocked": {"const": True},
                      "hitl_pending": BOOL, "reply": TEXT}, ("wrote", "files", "submit_blocked"), extra=True)
    elif name.startswith("pack-ship__"):
        # materials 现在也接已解析的行数组，并可给 file_path —— 之前只收字符串，
        # 任何真实装箱表都在 schema 校验这一步就被拒掉，工具面等于不可用。
        properties.update(solver={"type": ["object", "null"]}, connected={"type": ["boolean", "null"]},
                          materials={"type": ["string", "array", "null"]}, file_path=TEXT,
                          container_type=TEXT, max_containers={"type": ["integer", "null"]})
        if name.endswith(("__ingest", "__vgm", "__booking_draft")):
            output = obj({"ok": BOOL, "n_rows": {"type": "integer"},
                          "needs_human": {"type": "array"}}, ("ok",), extra=True)
        elif name.endswith("__list"):
            output = obj({"ok": BOOL, "tools": {"type": "array"}}, ("ok", "tools"), extra=True)
        elif name.endswith("__health"):
            output = obj({"ok": BOOL, "connected": BOOL}, ("ok", "connected"), extra=True)
        else:
            output = obj({"ok": BOOL, "can_fit": {"type": ["boolean", "string"]}},
                         ("ok", "can_fit"), extra=True)
    elif name == "tender.parse":
        properties.update(source=TEXT, project_name=TEXT, ingest={"type": ["object", "null"]})
        required = ("text",)
        output = obj({"handoff": MAP, "matrix": MAP, "parse": MAP, "submit_blocked": {"const": True}},
                     ("handoff", "matrix", "parse", "submit_blocked"), extra=True)
    elif name == "tender.packing_link":
        properties.update(tender_path={"type": "string", "minLength": 1}, packing_list={"type": "string", "minLength": 1},
                          previous_path=TEXT, project_name=TEXT, container_type=TEXT)
        required = ("tender_path", "packing_list")
        output = obj({"ok": BOOL, "statements": {"type": "array"}, "record": MAP, "deliverables": {"type": "array"},
                      "submit_blocked": {"const": True}}, ("ok", "statements", "record", "deliverables", "submit_blocked"), extra=True)
    elif name == "read_link_record":
        # no path argument: the record is found by session, never named by the caller
        output = obj({"ok": BOOL, "statements": {"type": "array"}, "counts": MAP, "submit_blocked": {"const": True},
                      "confirmed_by_person": BOOL}, ("ok", "statements", "counts", "submit_blocked"), extra=True)
    elif name == "tender.review":
        properties.update(draft=TEXT, matrix={"type": ["object", "null"]},
                          tech_outline={"type": ["object", "null"]}, bidbook_markdown=TEXT)
    elif name == "write_deliverable":
        properties.update(path={"type": "string", "minLength": 1}, text=TEXT)
        required = ("path", "text")
        output = obj({"path": TEXT, "wrote": {"const": True}, "n_chars": {"type": "integer"}},
                     ("path", "wrote", "n_chars"))
    elif name == "spawn_helper":
        properties.update(command={"type": ["string", "array"]}, argv={"type": "array"},
                          spawn={"type": ["string", "array"]}, kind=TEXT)
        output = obj({"spawned": {"const": False}, "allowed": BOOL}, ("spawned", "allowed"), extra=True)
    elif name in {"civil.turn", "agent.turn"}:
        properties.update(skill=TEXT)
        required = ("text",)
    elif name in {"search_kb", "read_kb", "list_kb"}:
        properties.update(query=TEXT, q=TEXT, path=TEXT, uri=TEXT)
    elif name.endswith("__scan_forbidden") or name == "scan_forbidden":
        properties.update(draft=TEXT)
    return {"schema_version": "civil.tool.v1", "input_schema": obj(properties, required),
            "output_schema": output}


def validate(value, schema: dict, path: str = "arguments") -> str | None:
    """Return a field path and reason; never echo submitted data in errors."""
    if "const" in schema and (type(value) is not type(schema["const"]) or value != schema["const"]):
        return f"{path}: incorrect constant"
    types = schema.get("type", [])
    types = [types] if isinstance(types, str) else types
    matches = {"object": isinstance(value, dict), "array": isinstance(value, list),
               "string": isinstance(value, str), "boolean": type(value) is bool,
               "integer": type(value) is int,
               "number": type(value) in (int, float) and (type(value) is int or math.isfinite(value)),
               "null": value is None}
    if types and not any(matches.get(t, False) for t in types):
        return f"{path}: expected {'/'.join(types)}"
    if "enum" in schema and value not in schema["enum"]:
        return f"{path}: value outside allowed choices"
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", float("inf")):
            return f"{path}: invalid text length"
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                return f"{path}.{key}: required"
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key not in properties and schema.get("additionalProperties") is False:
                return f"{path}: unknown field"
            if key in properties:
                error = validate(item, properties[key], f"{path}.{key}")
                if error:
                    return error
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            error = validate(item, schema["items"], f"{path}[{i}]")
            if error:
                return error
    return None
