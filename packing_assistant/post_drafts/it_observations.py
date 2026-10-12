"""Scoped, sanitized IT observations. No network, credentials or operations.

Policies, requirements, permission applications, incidents, drills and
interfaces retain their own sources. Targets never become measured results.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import html
import re

from packing_assistant.post_drafts.it import _ALIASES, _FIELDS, _LABEL, _MISSING
from packing_assistant.post_drafts.planning import _inputs
from packing_assistant.post_drafts.procurement import _source_name
from packing_assistant.word_export import _table_cells

_NUMBER = r"(?:[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|[一二三四五六七八九十百]+|半)"
_TIME = r"(?:不超过\s*|最多\s*)?"+_NUMBER+r"\s*(?:分钟|小时|天|日|周|月|年|秒|min|minutes?|hours?|days?)"
_ROLES = r"企业管理员|项目经理|劳务管理员|施工员|劳务员|安全员|质量员|资料员|材料员|试验员|机管员|整改人|出纳|司机|商务只读|财务只读|商务|财务|工人|监理|业主|员工|审批人员"
_ROLE_ACTION = re.compile(r"^("+_ROLES+r")(?:手机上?|每天)?(?:可以|需要|负责|应当|应|可|能|要能|要)?\s*((?:拍照报|接单|提交|审批|审|录入|查询|查看|导出|登记|填报|填|上传|整改|审核|不用再导).+|审)$")
_UNKNOWN = re.compile(r"未提供|未给|未定|未知|未指派|未确定|暂无|没人|无人|不确定|待填|待核|待补|没给|没定|没报|还没|没有|没说|不清楚|得查|UNSPECIFIED|TBD|N/A|unknown|not provided|not assigned", re.I)
_UNSAFE = re.compile(r"例如|比如|假设|如果|示例|样例|自行计算|自己算|不采用|别按|[？?]|\b(?:hypothetical|for example)\b", re.I)
_PENDING = re.compile(r"预计|计划|拟|将|听说|据说|尚未|还没|未执行|没安排|未完成|没有|\b(?:planned|forecast|reportedly|not|will)\b", re.I)
_META = {"project", "jurisdiction", "system", "module", "builder", "site", "background", "goal", "scope", "excluded", "process", "target_process"}
_INCIDENTS = {"incident", "severity", "impact", "escalation", "contact", "threshold", "incident_source", "recovery_record"}
_DRILLS = {"drill_object", "drill_date", "isolation", "business_check", "integrity", "checker", "duration", "evidence", "failure", "drill_source", "duration_source", "restore_steps", "user_result"}
_INTERFACES = {"peer", "direction", "source", "retry", "interface_source"}
_IDENTITIES = {"role", "data", "feature", "requirement_id", "ticket", "scene", "object"}


@dataclass
class Data:
    contexts: list[dict] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    incidents: list[dict] = field(default_factory=list)
    drills: list[dict] = field(default_factory=list)
    interfaces: list[dict] = field(default_factory=list)
    provenance: list[dict] = field(default_factory=list)
    entries: list[tuple[str, str]] = field(default_factory=list)


def _value(value):
    value = value.strip(" \t\r\n,;，；。")
    return _MISSING if not value or value == "[敏感内容已省略]" or _UNKNOWN.fullmatch(value) else value


def _typed(key, value):
    value = _value(value)
    if key == "jurisdiction":
        return {"新加坡": "SG", "中国": "CN", "SG": "SG", "CN": "CN", "EU": "EU", "DUAL": "DUAL"}.get(value.upper(), "UNSPECIFIED")
    if key == "evidence" and (_UNKNOWN.search(value) or re.match(r"^(?:无(?:记录|证据|附件|演练记录)?|未附|未整理|未提交)(?:$|[，；\s])", value)):
        return _MISSING
    if key in {"rpo", "rto", "duration", "concurrency"}:
        if _UNKNOWN.search(value) or _UNSAFE.search(value) or key == "duration" and _PENDING.search(value):
            return _MISSING
        if key in {"rpo", "rto", "duration"} and not re.fullmatch(_TIME, value, re.I):
            return _MISSING
        if key == "concurrency" and not re.fullmatch(_NUMBER+r"\s*(?:人|用户|并发|个)?", value):
            return _MISSING
    if key in {"role", "owner", "requester", "approver", "withdrawer", "checker", "contact"} and (_UNKNOWN.search(value) or _UNSAFE.search(value) or "谁" in value):
        return _MISSING
    return value


def _feature(value):
    match = _ROLE_ACTION.match(value.strip())
    if match:
        return [("role", match.group(1)), ("feature", match.group(2))]
    return [("feature", value)]


def _details(key, value, post):
    if key == "feature" and post == "it-app":
        return _feature(value)
    if key in {"duration", "drill_date", "evidence", "business_check", "integrity", "user_result", "user_implementation", "recovery_record"} and _PENDING.search(value):
        return [("pending_record", _FIELDS[key][0]+"："+value)]
    result = [(key, value)]
    if _typed(key, value) == _MISSING and _value(value) != _MISSING:
        result.append(("pending_record", _FIELDS[key][0]+"："+value))
    return result


def _scan(post, text):
    text = re.sub(r"^(?:本次实际情况|实际情况|真实记录)\s*[:：]\s*", "", text)
    text = re.sub(r"^两个需求一块提\s*[:：]\s*", "", text)
    labels = list(_LABEL.finditer(text))
    if labels:
        return [(k, v) for index, match in enumerate(labels) for k, v in _details(_ALIASES[match.group(1).casefold()], text[match.end():labels[index+1].start() if index+1 < len(labels) else len(text)], post)]
    if _UNSAFE.search(text):
        return []
    pairs = []
    if re.fullmatch(r"(?:新加坡|SG|CN|中国)(?:\s+写草稿|\s+写内部草稿)?", text, re.I):
        pairs.append(("jurisdiction", "SG" if re.search(r"新加坡|SG", text, re.I) else "CN"))
    if post == "it-app":
        reference = re.match(r"(考勤|台班)(?:这个|那个)", text)
        if reference:
            pairs.append(("_reference", reference.group(1)))
        named = re.match(r"(?:一个是|另一个是)(.+)$", text)
        if named:
            pairs += [("module", named.group(1)), ("goal", named.group(1))]
        small = re.search(r"上个(.+?)的?小系统", text)
        if small:
            pairs += [("module", small.group(1).rstrip("的")), ("goal", small.group(1).rstrip("的"))]
        builder = re.match(r"建设方(?:是|为)(.+)$", text)
        if builder:
            pairs.append(("builder", builder.group(1)))
        site = re.match(r"(?:用在|使用现场是)(.+)$", text)
        if site:
            pairs.append(("site", site.group(1)))
        role = _ROLE_ACTION.match(text)
        if role:
            pairs += [("role", role.group(1)), ("feature", role.group(2))]
        if re.match(r"(?:无|没有|缺少).+?(?:不能|不得|不允许)", text):
            pairs.append(("rule", text))
        peer = re.search(r"(?:还要和|对接|和)(.+?(?:平台|系统|闸机))", text)
        if peer:
            pairs.append(("peer", peer.group(1)))
        if "门口闸机" in text:
            pairs += [("peer", "门口闸机"), ("direction", text)]
        delivery = re.search(r"(?:\d{1,2}月\d{1,2}日|国庆前|年底前|下月底|下月)", text)
        if delivery and re.search(r"试用|看到|交付|要|前", text):
            pairs.append(("delivery", delivery.group()))
        if re.search(r"(?:先不做|不含|排除|先不开发)", text):
            pairs.append(("excluded", text))
        if re.search(r"没说时间|未定时间", text):
            pairs.append(("pending_record", text))
    elif post == "it-data":
        system = re.search(r"([一-鿿A-Za-z0-9]+(?:系统|平台))按(核心级|重要级|一般级|终端级)管", text)
        if system:
            pairs += [("system", system.group(1)), ("classification", system.group(2))]
        datum = re.search(r"(?:工地那套|还有)(.+?(?:索引|台账|报表|数据库))", text)
        if datum:
            pairs.append(("data", datum.group(1)))
        owner = re.match(r"数据负责人(?:是|为)?(.+)$", text)
        if owner:
            pairs.append(("owner", owner.group(1)))
        for key, pattern in (("rpo", r"RPO\s*("+_TIME+r")"), ("rto", r"RTO\s*("+_TIME+r")"), ("rpo", r"最多容忍("+_TIME+r")"), ("retention", r"(?:保留|得留|需留|保存)\s*("+_TIME+r")")):
            match = re.search(pattern, text, re.I)
            if match:
                pairs.append((key, match.group(1)))
        cycle = re.search(r"一周全备一次|每周全量加每日增量|每日增量|每天全备一次", text)
        if cycle:
            pairs.append(("cycle", cycle.group()))
        media = re.match(r"备份介质(?:用|是|为)?(.+)$", text)
        if media:
            pairs.append(("media", media.group(1)))
        position = re.match(r"(?:副本放|先放|存放在)(.+)$", text)
        if position and not _UNKNOWN.search(text):
            pairs.append(("location", position.group(1)))
        if _UNKNOWN.search(text) and re.search(r"演练|存哪|RPO|RTO", text):
            pairs.append(("pending_record", text))
    elif post == "it-ops":
        system = re.match(r"(?:另外截标那天\s*)?([一-鿿A-Za-z0-9]+(?:系统|平台)|VPN)(?:这块|那头)?", text)
        if system:
            pairs.append(("system", system.group(1)))
        oa = re.search(r"("+_ROLES+r")(?:要|需要)\s*(OA)\s*的(.+)$", text)
        if oa:
            pairs += [("system", oa.group(2)), ("role", oa.group(1)), ("permission", oa.group(3))]
        role = re.search(r"(?:现场)?("+_ROLES+r")(?:这个岗位)?(?:只给|要能)(.+?)(?:两个动作)?$", text)
        if role:
            pairs += [("role", role.group(1)), ("permission", role.group(2).removesuffix("两个动作"))]
        for key, pattern in (("requester", r"^申请人\s*(.+)$"), ("approver", r"^业务审批人\s*(.+)$"), ("ticket", r"^工单号\s+(.+)$"), ("severity", r"按\s*(P[1-4])\s*记")):
            match = re.search(pattern, text, re.I)
            if match:
                pairs.append((key, match.group(1)))
        date = re.search(r"\d{4}-\d{2}-\d{2}", text)
        if date and re.search(r"生效|开通", text):
            pairs.append(("start", date.group()))
        if date and re.search(r"到期|收回|用到", text):
            pairs.append(("expiry", date.group()))
        person_role = re.match(r"(项目经理|"+_ROLES+r")([一-鿿]{2,4})要能(.+)$", text)
        if person_role:
            pairs += [("role", person_role.group(1)), ("requester", person_role.group(2)), ("permission", person_role.group(3))]
        if "VPN" in text and re.search(r"掉.*线|故障|中断", text):
            pairs += [("system", "VPN"), ("impact", text)]
        if _UNKNOWN.search(text) and re.search(r"审批|工单|权限", text):
            pairs.append(("pending_record", text))
    return pairs


def parse(post, text):
    # NFKC credential sanitization normalizes full-width colons. Restore only
    # the source-marker delimiter expected by the shared document reader.
    text = re.sub(r"(?m)^(\s*【(?:用户上传|所选附件)):", r"\1：", text)
    data = Data()
    scope = {"jurisdiction": "UNSPECIFIED"}
    data.contexts.append(scope)
    current, children, epoch, origin = None, {}, 0, ""

    def cid():
        return next(i for i, context in enumerate(data.contexts) if context is scope)

    def rid():
        return next((i for i, r in enumerate(data.rows) if r is current), None)

    def new_row(retain=()):
        row = {"_context": cid(), "_file": epoch, "_origin": origin}
        if current is not None:
            row.update({k: current[k] for k in retain if k in current})
        data.rows.append(row)
        return row

    def put(target, key, value):
        value = _typed(key, value)
        if target.get(key) in {None, _MISSING, "UNSPECIFIED"}:
            target[key] = value
        elif value != _MISSING and value not in target[key].split("；"):
            target[key] += "；"+value
        if value not in {_MISSING, "UNSPECIFIED"}:
            target.setdefault("_sources", {})[key] = position
            data.provenance.append({"_row": target.get("_row", rid()) if target is not scope else None, "_context": cid(), "field": _FIELDS[key][0], "value": value, "input_position": position})

    # A key/value form is not a table of records. Keep padded forms and ignore
    # a labelled row with nonempty extra cells, as the document reader requires.
    def inputs():
        offset, table_header = 0, None
        from packing_assistant.post_drafts.procurement import _literal_source
        # A stated conditional business rule is not an example of a live
        # system. Protect only the conditional word in allowlisted rule values
        # while the scenario filter continues to exclude narrative examples.
        conditional = "\uf100\uf101"
        while conditional in text:
            conditional += "\uf101"
        protected = []
        for line in text.splitlines(keepends=True):
            labels = list(_LABEL.finditer(line))
            cursor = 0
            for index, label in enumerate(labels):
                end = labels[index+1].start() if index+1 < len(labels) else len(line)
                protected.append(line[cursor:label.end()])
                value = line[label.end():end]
                key = _ALIASES[label.group(1).casefold()]
                protected.append(value.replace("如果", conditional) if key in {"rule", "exception", "acceptance"} else value)
                cursor = end
            protected.append(line[cursor:])
        safe = _literal_source("".join(protected))
        for line in safe.splitlines(keepends=True):
            stripped = line.strip()
            marker = _source_name(stripped)
            if marker is not None or stripped.startswith(("【本轮用户要求", "【此前用户资料")):
                table_header = None
                yield "marker", stripped, offset
            elif stripped.startswith("|"):
                cells = [html.unescape(c).strip() for c in _table_cells(stripped)]
                if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                    pass
                elif table_header is None:
                    table_header = cells
                elif any(c.casefold() in _ALIASES for c in table_header):
                    yield "row", [(k, cells[i] if i < len(cells) else "") for i, k in enumerate(table_header)], offset
                elif cells and cells[0].casefold() in _ALIASES and len(cells) >= 2 and cells[1] and not any(cells[2:]):
                    yield "pairs", _details(_ALIASES[cells[0].casefold()], cells[1], post), offset
            else:
                if table_header is not None:
                    yield "end_table", [], offset
                    table_header = None
                for clause, _, relative, _ in _inputs(line):
                    if clause is not None:
                        yield "pairs", _scan(post, clause.replace(conditional, "如果")), offset+relative
            offset += len(line)

    for kind, content, offset in inputs():
        position = (origin+" · " if origin else "")+f"脱敏输入第{text[:offset].count(chr(10))+1}行（非原件页码）"
        if kind == "marker":
            origin = _source_name(content) or content.strip("【】")
            epoch += 1
            scope = {"jurisdiction": "UNSPECIFIED", "_origin": origin}
            data.contexts.append(scope)
            current, children = None, {}
            continue
        if kind == "end_table":
            current, children = None, {}
            continue
        pairs = ([(key, v) for label, value in content if label.casefold() in _ALIASES for key, v in _details(_ALIASES[label.casefold()], value, post)] if kind == "row" else content)
        if not pairs:
            continue
        data.entries.extend((k, _typed(k, v)) for k, v in pairs if not k.startswith("_"))
        stated = dict(pairs)
        if "_reference" in stated:
            found = [c for c in data.contexts if c.get("_file") == epoch and stated["_reference"] in c.get("module", "")]
            if len(found) == 1:
                scope = found[0]
                current, children = None, {}
            else:
                scope = {"jurisdiction": "UNSPECIFIED", "_origin": origin, "_file": epoch}
                data.contexts.append(scope)
                current, children = None, {}
        if kind != "row" and any(k in stated for k in {"system", "module"}) and (any(scope.get(k) not in {None, _MISSING} for k in {"system", "module"} if k in stated)
                or current is not None or any(r.get("_context") == cid() for r in data.incidents)):
            retain = {"project", "jurisdiction", "_origin"} | ({"system"} if "module" in stated and "system" not in stated else set())
            scope = {k: v for k, v in scope.items() if k in retain}
            data.contexts.append(scope)
            current, children = None, {}
        elif kind != "row" and any(k in stated and scope.get(k) not in {None, _MISSING, "UNSPECIFIED", _typed(k, stated[k])} for k in _META - {"system", "module"}):
            scope = ({"jurisdiction": "UNSPECIFIED", "_origin": origin} if any(k in stated for k in {"project", "jurisdiction"}) else {**scope})
            scope["_sources"] = {k: v for k, v in scope.get("_sources", {}).items() if k not in stated}
            for key in _META:
                if key in stated:
                    scope.pop(key, None)
            data.contexts.append(scope)
            current, children = None, {}
        for key, value in pairs:
            if key in _META and kind != "row":
                put(scope, key, value)
        scope["_file"] = epoch
        if kind == "row":
            current, children = new_row(), {}
            # An explicitly named table system is independent of the preceding
            # narrative system, including its scope, builder and background.
            if "system" in stated and _typed("system", stated["system"]) != scope.get("system"):
                current["_independent_system"] = True
        elif current is not None and any(k in stated and current.get(k) not in {None, _MISSING} for k in _IDENTITIES | {"permission", "rpo", "rto", "approver", "requester"}):
            retain = ("role",) if ("feature" in stated or "permission" in stated) and "role" not in stated else ("data",) if any(k in stated for k in {"rpo", "rto"}) and "data" not in stated else ()
            current, children = new_row(retain), {}
        if current is None and any(k not in _META and not k.startswith("_") and k not in _INCIDENTS for k, _ in pairs):
            current = new_row()
        for key, value in pairs:
            if key.startswith("_") or key in _INCIDENTS | _DRILLS | (_INTERFACES if post == "it-app" else set()):
                continue
            if key in _META and kind != "row":
                continue
            put(current, key, value)
        for name, keys, identities in (("incidents", _INCIDENTS, {"incident", "severity", "impact"}), ("drills", _DRILLS, {"drill_object", "drill_date"}), ("interfaces", _INTERFACES if post == "it-app" else set(), {"peer"})):
            supplied = [(k, v) for k, v in pairs if k in keys]
            if not supplied:
                continue
            previous = children.get(name, {})
            fresh = any(k in stated and previous.get(k) not in {None, _MISSING} for k in identities | (keys - identities))
            if fresh or name not in children:
                target = {"_context": cid(), "_row": None if name == "incidents" else rid(), "_origin": origin}
                if name == "incidents" and kind == "row":
                    target.update({k: current[k] for k in _META if k in current})
                    target["_independent_system"] = current.get("_independent_system", False)
                    target["_sources"] = {k: v for k, v in current.get("_sources", {}).items() if k in _META}
                getattr(data, name).append(target)
                children[name] = target
            else:
                target = previous
            for key, value in supplied:
                put(target, key, value)
    return data


def owned(data, records):
    result = []
    for record in records:
        context = data.contexts[record.get("_context", 0)]
        parent = data.rows[record["_row"]] if isinstance(record.get("_row"), int) else {}
        if "drill_object" in record and parent.get("data") not in {None, _MISSING, record["drill_object"]}:
            # A named drill object differing from the policy's data object
            # cannot borrow that object's recovery targets or target sources.
            policy = {"data", "classification", "owner", "rpo", "rto", "rpo_source", "rto_source", "method", "cycle", "media", "backup_window", "retention", "location", "policy_source"}
            parent = {k: v for k, v in parent.items() if k not in policy}
            parent["_sources"] = {k: v for k, v in parent.get("_sources", {}).items() if k not in policy}
        if record.get("_independent_system") or parent.get("_independent_system"):
            context = {k: v for k, v in context.items() if k in {"project", "jurisdiction", "_origin", "_file"}}
        result.append({**context, **parent, **record, "_sources": {**context.get("_sources", {}), **parent.get("_sources", {}), **record.get("_sources", {})}})
    return result
