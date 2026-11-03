"""Read-only IFC information delivery checks and version comparison."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from importlib.metadata import version

MAX_IFC_BYTES = 8 * 1024 * 1024
MAX_IDS_BYTES = 512 * 1024
GLOBAL_ID = re.compile(r"[0-3][0-9A-Za-z_$]{21}\Z")


def _source(value: dict, suffix: str, limit: int) -> tuple[bytes, dict]:
    if not isinstance(value, dict) or set(value) != {"name", "data_b64"}:
        raise ValueError("上传文件字段无效，只接受文件名与文件内容。")
    name = value["name"]
    if (not isinstance(name, str) or not name.lower().endswith(suffix) or len(name) > 200
            or any(c in name for c in "/\\:") or any(ord(c) < 32 for c in name)):
        raise ValueError(f"请选择 {suffix} 文件，文件名不得含路径。")
    try:
        raw = base64.b64decode(value["data_b64"], validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("文件内容编码无效。") from exc
    if not raw or len(raw) > limit:
        raise ValueError(f"{suffix} 文件为空或超过 {limit // 1024} KiB。")
    return raw, {"name": name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def validate_sources(action: str, payload: dict) -> None:
    """Validate bounded file envelopes without importing any native IFC engine."""
    fields = {"check": (("ifc", ".ifc", MAX_IFC_BYTES), ("ids", ".ids", MAX_IDS_BYTES)),
              "diff": (("old", ".ifc", MAX_IFC_BYTES), ("new", ".ifc", MAX_IFC_BYTES))}.get(action)
    if fields is None or not isinstance(payload, dict) or set(payload) != {name for name, _, _ in fields}:
        raise ValueError("IFC 输入只接受本操作要求的文件内容对象。")
    for name, suffix, limit in fields:
        _source(payload[name], suffix, limit)


def _model(value):
    import ifcopenshell
    raw, source = _source(value, ".ifc", MAX_IFC_BYTES)
    try:
        text = raw.decode("utf-8-sig")
        if not text.lstrip().startswith("ISO-10303-21;"):
            raise ValueError()
        # IfcOpenShell tolerates missing STEP terminators. Refuse truncated
        # uploads before parsing, while allowing trailing STEP block comments.
        end = len(text.rstrip())
        while text[max(0, end - 2):end] == "*/":
            end = text.rfind("/*", 0, end - 2)
            if end < 0:
                raise ValueError()
            while end and text[end - 1].isspace():
                end -= 1
        if not text[:end].endswith("END-ISO-10303-21;"):
            raise ValueError()
        model = ifcopenshell.file.from_string(text)
    except Exception as exc:
        raise ValueError("IFC 解析失败，请上传有效的 IFC STEP 文件。") from exc
    if model.schema not in {"IFC2X3", "IFC4", "IFC4X3", "IFC4X3_ADD2"}:
        raise ValueError("暂不支持该 IFC schema。")
    elements = model.by_type("IfcRoot")
    ids = [element.GlobalId for element in elements]
    if (len(elements) > 20000 or len(ids) != len(set(ids))
            or any(not isinstance(value, str) or not GLOBAL_ID.fullmatch(value) for value in ids)):
        raise ValueError("IFC 超过 20000 个根对象，或存在无效/缺失/重复 GlobalId。")
    return model, source


def _compared_ids(model):
    """Use the same object population as IfcDiff's default comparison."""
    spatial = "IfcSpatialStructureElement" if model.schema == "IFC2X3" else "IfcSpatialElement"
    return {element.GlobalId for element in model.by_type("IfcElement") + model.by_type(spatial)
            if not element.is_a("IfcFeatureElement")}


def check_model(payload: dict) -> dict:
    validate_sources("check", payload)
    from ifctester import ids, reporter
    from defusedxml.ElementTree import fromstring
    if not isinstance(payload, dict) or set(payload) != {"ifc", "ids"}:
        raise ValueError("IFC 检查只接受一份 IFC 和一份 IDS。")
    model, source = _model(payload["ifc"])
    raw, rules_source = _source(payload["ids"], ".ids", MAX_IDS_BYTES)
    try:
        xml = raw.decode("utf-8-sig")
        tree = fromstring(xml, forbid_dtd=True, forbid_entities=True, forbid_external=True)
        if tree.tag != "{http://standards.buildingsmart.org/IDS}ids":
            raise ValueError("IDS 根元素或命名空间不正确")
        rules = ids.from_string(xml, validate=True)
        if not rules.specifications or len(rules.specifications) > 100:
            raise ValueError("需包含 1–100 条 IDS specification")
    except Exception as exc:
        raise ValueError("IDS 内容无效；不接受 DTD、外部实体或空规则。") from exc
    rules.validate(model)
    report_writer = reporter.Json(rules)
    report = report_writer.report()
    # The JSON reporter contains source elements, IDs and reasons; it is never
    # rendered as HTML from the upload and does not write back to the IFC.
    report = json.loads(json.dumps(report, default=report_writer.encode, ensure_ascii=False, allow_nan=False))
    return {"kind": "ifc_check", "engine": "IfcTester", "engine_version": version("ifctester"),
            "source": source, "rules_source": rules_source, "ifc_schema": model.schema, "report": report,
            "notes": ["仅检查本次 IDS 信息交付要求，不代表几何碰撞、结构安全或规范审查结论。",
                      "报告对应上传原件 SHA-256；原模型未修改。"]}


def compare_models(payload: dict) -> dict:
    validate_sources("diff", payload)
    from ifcdiff import IfcDiff
    if not isinstance(payload, dict) or set(payload) != {"old", "new"}:
        raise ValueError("版本对比只接受原版与新版 IFC。")
    old, old_source = _model(payload["old"])
    new, new_source = _model(payload["new"])
    if old.schema != new.schema:
        raise ValueError("请先使用相同 IFC schema 导出两个版本。")
    comparison = IfcDiff(old, new, relationships=["geometry", "attributes", "type", "property", "container", "aggregate", "classification"], is_shallow=False)
    comparison.diff()
    changed = json.loads(json.dumps(comparison.change_register, default=comparison.json_dump_default, allow_nan=False))
    def info(model, guid):
        entity = model.by_guid(guid)
        return {"global_id": guid, "type": entity.is_a(), "name": entity.Name or "未命名"}
    old_ids = _compared_ids(old)
    new_ids = _compared_ids(new)
    warnings = ["按 GlobalId 对齐构件；重新导出导致编号变化时，可能被计为新增/删除。",
                "几何表达方式变化也可能记为改变；差异列表不等于碰撞或规范检查。",
                "统计范围为构件与空间对象，按 IfcDiff 默认规则排除 IfcFeatureElement（如开孔）。"]
    if old_ids and new_ids and not old_ids.intersection(new_ids):
        warnings.insert(0, "两个版本没有共同构件 GlobalId，请先核对是否为同一模型的连续版本。")
    return {"kind": "ifc_diff", "engine": "IfcDiff", "engine_version": version("ifcdiff"),
            "old_source": old_source, "new_source": new_source, "ifc_schema": new.schema,
            "added": [info(new, guid) for guid in sorted(comparison.added_elements)],
            "deleted": [info(old, guid) for guid in sorted(comparison.deleted_elements)],
            "changed": [{**info(new, guid), "changes": changes} for guid, changes in sorted(changed.items())],
            "unchanged_count": len(old_ids.intersection(new_ids) - set(changed)), "notes": warnings}


def synthetic_examples() -> dict[str, bytes]:
    """A tiny synthetic IFC/IDS pair, intentionally missing a wall Name."""
    import ifcopenshell
    import ifcopenshell.guid
    from ifctester import ids
    model = ifcopenshell.file(schema="IFC4")
    wall = model.create_entity("IfcWall", GlobalId=ifcopenshell.guid.compress("11111111111111111111111111111111"), Name=None)
    before = model.to_string().encode("utf-8")
    wall.Name = "Synthetic wall A"
    model.create_entity("IfcWall", GlobalId=ifcopenshell.guid.compress("22222222222222222222222222222222"), Name="Synthetic wall B")
    rules = ids.Ids(title="Synthetic competition example: wall names")
    specification = ids.Specification(name="Wall Name is required", minOccurs=1, ifcVersion=["IFC4"])
    specification.applicability.append(ids.Entity(name="IFCWALL"))
    specification.requirements.append(ids.Attribute(name="Name"))
    rules.specifications.append(specification)
    return {"old.ifc": before, "new.ifc": model.to_string().encode("utf-8"), "requirements.ids": rules.to_string().encode("utf-8")}
