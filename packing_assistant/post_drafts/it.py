"""IT post drafts from explicitly supplied, sanitized facts; no system operations."""

from __future__ import annotations

import html
import ipaddress
import re
import unicodedata
from collections.abc import Sequence

_MISSING = "UNSPECIFIED（待填）"
_REDACTED = "[敏感内容已省略]"
_TOOLS = {"it-ops": "it-ops__runbook", "it-data": "it-data__backup", "it-app": "it-app__srs"}
_FIELDS = {
    "project": ("项目名称", "项目", "Project"), "jurisdiction": ("辖区", "Jurisdiction"),
    "module": ("业务模块", "模块名称", "模块", "Module"),
    "system": ("系统名称", "系统名", "平台名称", "系统", "平台", "System"),
    "owner": ("数据负责人", "数据主人", "业务负责人", "负责人"),
    "scope": ("适用范围", "业务范围", "范围"),
    "role": ("使用人岗位", "用户角色", "岗位", "角色", "Role"),
    "permission": ("申请权限", "权限范围", "权限", "动作"),
    "application": ("申请事项", "变更事项"),
    "requester": ("申请人",), "approver": ("业务审批人", "审批人"),
    "start": ("拟生效时间", "生效时间", "开始时间"),
    "expiry": ("到期时间", "到期日", "收回时间", "截止时间"),
    "withdrawer": ("收回人",), "ticket": ("变更工单号", "工单号"),
    "window": ("变更窗口", "维护窗口", "窗口"),
    "rollback": ("回退步骤", "回退方案"), "review": ("权限复核周期", "复核周期"),
    "severity": ("故障级别", "故障分级", "严重程度"),
    "impact": ("故障现象", "影响范围", "影响面"),
    "escalation": ("故障升级路径", "升级路径", "升级链"),
    "contact": ("值班联系人", "联系人"), "threshold": ("升级条件", "升级时限"),
    "data": ("数据对象", "备份数据", "数据范围", "数据", "Data object"),
    "classification": ("数据分级", "系统分级", "重要级别", "分级"),
    "rpo": ("RPO目标", "RPO"), "rto": ("RTO目标", "RTO"),
    "cycle": ("备份周期", "备份频率"), "method": ("备份方式", "备份方法"),
    "media": ("备份介质", "介质"), "backup_window": ("备份窗口",),
    "retention": ("数据保留期限", "保留期限", "保存期限"),
    "location": ("存放位置类型", "存放类型", "备份位置类型"),
    "encryption": ("加密方案", "是否加密"), "drill_cycle": ("演练周期",),
    "drill_object": ("演练对象",), "drill_date": ("演练时间", "演练日期"),
    "isolation": ("隔离环境", "是否隔离"), "business_check": ("业务拉起记录",),
    "integrity": ("完整性检查记录", "完整性检查"), "checker": ("完整性检查人", "检查人"),
    "duration": ("实际恢复时长", "实际耗时"), "evidence": ("演练记录", "演练证据", "证据名称"),
    "failure": ("失败原因",), "handoff": ("移交责任人", "移交要求"),
    "background": ("当前现状", "业务现状", "现状", "背景"),
    "goal": ("建设目标", "业务目标", "目标"),
    "builder": ("建设方", "使用单位"), "site": ("使用现场", "使用场景"),
    "excluded": ("范围外", "排除范围", "不含范围"),
    "process": ("现有流程", "当前流程"), "target_process": ("目标流程", "拟议流程", "流程"),
    "scene": ("业务场景", "场景"), "feature": ("功能需求", "需求", "功能", "Feature"),
    "object": ("操作对象", "业务对象", "对象"), "rule": ("业务规则", "规则"),
    "exception": ("异常处理", "异常"), "acceptance": ("验收标准", "验收行为", "验收"),
    "concurrency": ("并发目标", "并发"), "offline": ("离线需求", "离线"),
    "source": ("主数据来源", "数据来源", "Master source"), "peer": ("对端系统", "接口系统", "Peer system"),
    "direction": ("接口方向", "方向"), "retry": ("失败重试", "重试规则"),
    "priority": ("优先级",), "delivery": ("期望交付时间", "期望时间"),
    "nonfunctional": ("非功能要求", "非功能需求", "性能要求", "Nonfunctional requirements"),
    "permission_source": ("权限依据", "权限来源"), "audit": ("审计证据", "审计记录"),
    "request_source": ("申请来源",), "approval_source": ("审批依据", "审批来源"),
    "recover_condition": ("回收条件",), "identity": ("身份鉴别方案", "身份鉴别"), "lifecycle": ("账号生命周期",),
    "incident": ("故障编号", "故障名称"), "incident_source": ("故障来源",), "recovery_record": ("用户恢复记录",),
    "rpo_source": ("RPO来源", "RPO依据"), "rto_source": ("RTO来源", "RTO依据"),
    "policy_source": ("备份策略来源",), "restore_steps": ("恢复步骤",), "drill_source": ("演练来源",),
    "duration_source": ("恢复时长来源",), "user_result": ("用户演练结果",), "pending_record": ("待核记录",),
    "requirement_id": ("需求编号", "Requirement ID"), "requirement_source": ("需求来源",),
    "interface_source": ("接口资料来源",), "user_implementation": ("用户实现记录",),
}
_ALIASES = {alias.casefold(): key for key, labels in _FIELDS.items() for alias in labels}
_LABEL = re.compile(
    r"(?<![A-Za-z0-9_])(" + "|".join(sorted(map(re.escape, _ALIASES), key=len, reverse=True))
    + r")\s*[:：=]\s*", re.IGNORECASE
)
_SECRET_NAME = (
    r"(?:api[ _-]?key|access[ _-]?(?:key(?:[ _-]?id)?|token)|client[ _-]?secret|"
    r"private[ _-]?key|secret(?:[ _-]?key)?|authorization|password|passwd|pwd|token|"
    r"connection[ _-]?string|user(?:name|[ _-]?id)?|dsn|endpoint|url|"
    r"密码|口令|密钥|令牌|私钥|连接串|接口地址|真实账号|登录账号|用户名|账号|许可号)"
)
_SECRET_START = re.compile(r"(?<![A-Za-z0-9_])" + _SECRET_NAME + r"[\"']?(?:\s*(?:[:：=]|为|是)\s*|[ \t]+(?=[^ \t|]))", re.I)
_QUOTED_SECRET = re.compile(
    _SECRET_START.pattern + r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')", re.I | re.S
)
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)", re.S | re.I)
_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s<>\"'，；]+", re.I)
_TOKEN = re.compile(
    r"\b(?:Bearer|Basic)\s+[^\s,;，；]+|\bsk-[A-Za-z0-9_-]{8,}|"
    r"\b(?:gh[pousr]_|github_pat_|hf_|xox[baprs]-)[A-Za-z0-9_-]{8,}|"
    r"\bAKIA[A-Z0-9]{16}\b|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", re.I
)
_ADDRESS = re.compile(
    r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?|"
    r"\b[^\s:@，；]+:[^\s@，；]+@[^\s，；]+|"
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|"
    r"\b(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?::\d+)?/[^\s，；]+|"
    r"\b(?:[A-Za-z0-9-]+\.)+(?:com|org|net|io|dev|local|internal|invalid|cn|sg)(?::\d+)?\b", re.I
)
_IPV6_CANDIDATE = re.compile(r"(?<![A-Za-z0-9])(?:\[[0-9A-Fa-f:.%]+\]|[0-9A-Fa-f]+:[0-9A-Fa-f:.]+:[0-9A-Fa-f:.]*|::(?!:)[0-9A-Fa-f:.]+)(?![A-Za-z0-9])")


def _redact_ipv6(match):
    candidate = match.group().strip("[]")
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        # Sentence punctuation is not part of an IPv6 literal. Leave clock
        # times and other colon-separated business text unchanged.
        try:
            address = ipaddress.ip_address(candidate.rstrip("."))
        except ValueError:
            return match.group()
    return _REDACTED if address.version == 6 else match.group()


def _redact(text: str) -> str:
    """Discard credential values before parsing, including quoted multiline values.

    An unquoted credential assignment discards the rest of its physical line:
    guessing where a password ends could retain part of it as another field.
    Safe fields on subsequent lines remain available.
    """
    # Preserve table-cell boundaries while decoding credential obfuscation.
    # Uploaded Office/CSV values encode literal pipes as entities.
    sentinel = "\uf000"
    while sentinel in (text or ""):
        sentinel += "\uf000"
    protected = "".join(re.sub(r"&#(?:0*124|x0*7c);|&(?:vert|VerticalLine);", sentinel, line, flags=re.I)
                        if line.lstrip().startswith("|") else line for line in (text or "").splitlines(keepends=True))
    value = unicodedata.normalize("NFKC", html.unescape(protected))
    value = _PRIVATE_KEY.sub("\n" + _REDACTED + "\n", value)
    value = _QUOTED_SECRET.sub("\n" + _REDACTED + "\n", value)
    value = _URL.sub(_REDACTED, value)
    value = _TOKEN.sub(_REDACTED, value)
    value = _IPV6_CANDIDATE.sub(_redact_ipv6, value)
    value = _ADDRESS.sub(_REDACTED, value)
    lines = []
    for line in value.splitlines():
        match = _SECRET_START.search(line)
        lines.append(line[:match.start()].rstrip(" \t,;，；\"'") if match else line)
    return "\n".join(lines).replace(sentinel, "&#124;")


class _Facts:
    """Compatibility facade over the scoped, credential-free observations."""
    def __init__(self, text: str, post: str = "it-app"):
        from packing_assistant.post_drafts.it_observations import parse
        self.text = _redact(text)
        self.data = parse(post, self.text)
        self.entries = self.data.entries

    def get(self, key: str, default: str = _MISSING) -> str:
        return next((value for name, value in self.entries if name == key), default)

    def records(self, split: Sequence[str] = ()) -> list[dict]:
        from packing_assistant.post_drafts.it_observations import owned
        records = owned(self.data, self.data.rows)
        present = {r.get("_context") for r in self.data.rows if not r.get("_independent_system")}
        for index, context in enumerate(self.data.contexts):
            if index not in present and any(not k.startswith("_") and (k != "jurisdiction" or v != "UNSPECIFIED") for k, v in context.items()):
                records.append({**context, "_context": index})
        return records or [{}]

    def children(self, kind: str) -> list[dict]:
        from packing_assistant.post_drafts.it_observations import owned
        return owned(self.data, getattr(self.data, kind))


def _cell(value: str) -> str:
    return html.escape(value, quote=False).replace("|", "&#124;").replace("\n", " ").replace("\r", " ")


def _table(title: str, headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = [f"## {title}", "", "| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows)
    return "\n".join(lines) + "\n\n"


def _fields(title: str, rows: Sequence[Sequence[str]]) -> str:
    return _table(title, ("栏位", "用户提供/待填"), rows)


def _get(record, key):
    return record.get(key, _MISSING)


def _source(record, key, explicit=None):
    if explicit and record.get(explicit) not in {None, _MISSING}:
        return record[explicit]
    return record.get("_sources", {}).get(key, _MISSING)


def _identity(record):
    return " / ".join(record[key] for key in ("system", "module", "data") if record.get(key) not in {None, _MISSING}) or _MISSING


def _pending(record, keys):
    missing = [_FIELDS[key][0] for key in keys if record.get(key) in {None, _MISSING}]
    return "；".join(filter(None, (record.get("pending_record"), "待核："+"、".join(missing) if missing else None))) or "用户资料未核验；责任人待签认"


def _scoped_fields(title, records, fields):
    out = ""
    for index, record in enumerate(records):
        suffix = "" if index == 0 else f" · 资料条目 {index+1}"
        out += _fields(title+suffix, [("系统 / 模块 / 数据归属", _identity(record)), ("项目", _get(record, "project")),
                                   ("辖区", record.get("jurisdiction", "UNSPECIFIED"))]
                       + [(label, _get(record, key)) for label, key in fields])
    return out


def _header(title: str, facts: _Facts) -> str:
    jurisdictions = {r.get("jurisdiction", "UNSPECIFIED") for r in facts.records()}
    jurisdictions.discard("UNSPECIFIED")
    jurisdiction = next(iter(jurisdictions)) if len(jurisdictions) == 1 else "DUAL" if jurisdictions else "UNSPECIFIED"
    return (
        f"# {title}（AI 草稿 · 内部讨论）\n\n"
        "> 草稿声明：仅供内部核对，不是批准制度、测评结论或上线批准；本稿未执行权限变更、备份或恢复。\n\n"
        f"- 辖区：{jurisdiction}\n"
        "- 辖区汇总不替代各对象归属；未提供的对象不沿用其他对象辖区。\n"
        "- 已填事实均来自用户输入，未核验；方案、目标和需求为拟议内容，缺项为 UNSPECIFIED。\n"
        "- 未签认；submit_blocked=true。凭据与接口实地址不纳入草稿，不附输入原文。\n"
        "- 资料位置指脱敏输入行，不是原件页码；签认和验收结果由责任人填写。\n\n"
    )


def _ops(facts: _Facts) -> str:
    records = facts.records()
    out = _header("账号与权限运维手册提纲", facts)
    out += _table("系统与权限申请矩阵", ("系统", "适用范围", "岗位角色", "拟申请动作/权限", "业务确认"), [
        tuple(_get(r, key) for key in ("system", "scope", "role", "permission"))+("",) for r in records])
    out += _table("权限依据与申请审批（未执行）", ("系统", "角色", "业务权限", "权限依据", "申请人", "业务审批人", "申请来源", "审批依据", "回收条件", "审计证据", "待核项", "审批签认"), [
        tuple(_get(r, key) for key in ("system", "role", "permission", "permission_source", "requester", "approver"))
        + (_source(r, "permission", "request_source"), _get(r, "approval_source"),
           r.get("recover_condition", r.get("expiry", _MISSING)), _get(r, "audit"),
           _pending(r, ("approver", "permission_source", "audit")), "") for r in records])
    out += _table("账号生命周期（拟议流程）", ("触发", "拟处理", "责任角色", "执行记录"), [
        ("入职/外部协作", "申请、业务审批、最小权限开通；外部角色注明到期日", _MISSING, ""),
        ("调岗/临时提权", "复核原权限，记录事由、窗口及收回人", _MISSING, ""),
        ("长期未使用/离职", "按公司确认周期冻结，离职关闭并留痕", _MISSING, ""),
    ])
    for index, r in enumerate(records):
        out += _fields("权限变更与留痕"+("" if not index else f" · 资料条目 {index+1}"), [
            ("系统 / 岗位归属", _identity(r)+" / "+_get(r, "role")),
            ("申请事项", _get(r, "application")), ("申请人", _get(r, "requester")),
            ("拟审批人", _get(r, "approver")), ("拟生效时间", _get(r, "start")),
            ("到期/收回时间", _get(r, "expiry")), ("收回人", _get(r, "withdrawer")),
            ("工单号（用户提供）", _get(r, "ticket")), ("审批签认", ""), ("实际生效记录", ""),
        ])
    incidents = facts.children("incidents") or records
    out += _table("故障分级与升级路径", ("系统", "故障级别/影响", "用户指定升级路径", "联系人", "升级条件/时限"), [
        (_get(r, "system"), "；".join(r[k] for k in ("severity", "impact") if r.get(k) not in {None, _MISSING}) or _MISSING,
         _get(r, "escalation"), _get(r, "contact"), _get(r, "threshold")) for r in incidents])
    out += _table("故障资料与恢复留痕（非本轮处置）", ("系统", "故障标识", "故障来源", "用户恢复记录（未核）", "待核项", "处置签认"), [
        (_get(r, "system"), _get(r, "incident"), _source(r, "impact", "incident_source"),
         _get(r, "recovery_record"), _pending(r, ("contact", "threshold", "recovery_record")), "") for r in incidents])
    out += "待业务确认的升级流程：一线记录现象与影响 → 二线运维 → 三线厂商/开发；安全事件按企业应急流程升级。联系人和时限不默认填入。\n\n"
    out += _scoped_fields("变更窗口与检查", records, [
        ("变更窗口", "window"), ("回退步骤", "rollback"), ("权限复核周期", "review"),
        ("用户指定身份鉴别", "identity"), ("用户指定生命周期", "lifecycle"), ("待核记录", "pending_record")])
    out += "权限原则（拟议）：一人一号、办公与特权角色分离、禁止共用管理员；仅授予完成任务所需权限。导出、批量删除等敏感动作单独审批。具体身份鉴别措施待确认；本稿不登记凭据，不执行开通或回收。\n"
    return out


def _data(facts: _Facts) -> str:
    records = facts.records()
    out = _header("数据备份与恢复策略草稿", facts)
    out += _table("范围与数据归属", ("项目", "辖区", "系统", "数据对象", "适用范围", "演练周期", "移交责任 / 要求"), [
        (_get(r, "project"), r.get("jurisdiction", "UNSPECIFIED"))+tuple(_get(r, key) for key in ("system", "data", "scope", "drill_cycle", "handoff")) for r in records])
    out += _table("系统与数据分级", ("系统", "数据对象", "用户指定分级", "数据负责人"), [
        tuple(_get(r, key) for key in ("system", "data", "classification", "owner")) for r in records])
    out += _table("业务恢复目标（拟议，非实测承诺）", ("系统", "RPO 目标", "RTO 目标", "业务确认"), [
        (_get(r, "system"), _get(r, "rpo"), _get(r, "rto"), "") for r in records])
    out += "RPO 是业务可容忍的数据丢失时间；RTO 是业务可容忍的中断时间。目标逐系统、逐数据对象由业务确认，不用目标代替实际恢复时长。\n\n"
    out += _table("备份计划（拟议，未执行）", ("系统", "方式", "周期", "介质", "窗口", "保留期限"), [
        tuple(_get(r, key) for key in ("system", "method", "cycle", "media", "backup_window", "retention")) for r in records])
    out += _table("存放与保护", ("系统", "位置类型（不含实地址）", "加密方案", "演练周期", "移交责任/要求"), [
        tuple(_get(r, key) for key in ("system", "location", "encryption", "drill_cycle", "handoff")) for r in records])
    out += _table("系统数据恢复目标与策略来源", ("系统数据", "数据负责人", "RPO目标", "RPO来源", "RTO目标", "RTO来源", "备份策略来源", "待核项", "业务确认"), [
        (_identity(r), _get(r, "owner"), _get(r, "rpo"), _source(r, "rpo", "rpo_source"),
         _get(r, "rto"), _source(r, "rto", "rto_source"), _source(r, "method", "policy_source"),
         _pending(r, ("owner", "rpo", "rto", "location")), "") for r in records])
    out += _table("数据对象备份与保留", ("系统数据", "方式", "周期", "介质", "窗口", "保留期限", "位置类型", "用户策略依据", "待核项"), [
        (_identity(r),)+tuple(_get(r, key) for key in ("method", "cycle", "media", "backup_window", "retention", "location"))
        + (_get(r, "policy_source"), _pending(r, ("media", "location"))) for r in records])
    drills = facts.children("drills") or [{**r, **{k: _MISSING for k in ("drill_object", "drill_date", "isolation", "business_check", "duration", "integrity", "checker", "evidence", "failure")}} for r in records]
    # A reported measured duration is not recovery evidence by itself.
    verified_fields = []
    for record in drills:
        r = {**record}
        if r.get("evidence") in {None, _MISSING} and r.get("duration") not in {None, _MISSING}:
            r["pending_record"] = "；".join(filter(None, (r.get("pending_record"), "用户口述实际恢复时长："+r["duration"]+"；未附演练记录")))
            r["duration"] = _MISSING
        verified_fields.append(r)
    out += _table("恢复演练记录（用户提供，未核验）", ("系统", "演练对象", "日期", "隔离环境", "业务拉起记录", "实际恢复时长"), [
        tuple(_get(r, key) for key in ("system", "drill_object", "drill_date", "isolation", "business_check", "duration")) for r in verified_fields])
    out += _table("恢复证据与核对", ("系统", "完整性检查记录", "检查人", "证据名称", "失败原因", "核对签认"), [
        tuple(_get(r, key) for key in ("system", "integrity", "checker", "evidence", "failure"))+("",) for r in verified_fields])
    out += _table("恢复步骤与演练证据归属", ("系统数据", "演练对象", "RPO来源", "RTO来源", "恢复步骤", "验证证据", "演练来源", "恢复时长来源", "用户结果（未核验）", "待核项", "签认"), [
        (_identity(r), _get(r, "drill_object"), _source(r, "rpo", "rpo_source"), _source(r, "rto", "rto_source"),
         _get(r, "restore_steps"), _get(r, "evidence"), _source(r, "evidence", "drill_source"),
         _source(r, "duration", "duration_source") if _get(r, "duration") != _MISSING else _MISSING,
         _get(r, "user_result"), _pending(r, ("evidence", "integrity", "business_check")), "") for r in verified_fields])
    out += "无原始记录时，成功恢复证据和实际时长均待填。备份成功不等于可恢复；本稿没有访问数据或执行备份、恢复、演练。适用制度、保护等级、项目移交范围均待责任人核对，不作合规或恢复能力结论。\n"
    return out


def _app(facts: _Facts) -> str:
    records = facts.records()
    out = _header("施工企业业务系统需求说明书草稿", facts)
    out += _scoped_fields("现状、目标与范围", records, [
        ("系统", "system"), ("业务模块", "module"), ("建设方", "builder"), ("使用现场", "site"),
        ("用户描述现状", "background"), ("拟议目标", "goal"), ("范围", "scope"), ("范围外", "excluded")])
    out += _scoped_fields("业务流程", records, [("现有流程（用户描述）", "process"), ("拟议目标流程", "target_process")])
    out += _table("角色与功能需求（逐项待确认）", ("系统", "角色", "场景", "动作/功能", "对象", "业务规则", "异常处理"), [
        tuple(_get(r, key) for key in ("system", "role", "scene", "feature", "object", "rule", "exception")) for r in records])
    out += _table("可观察验收标准（拟议，未验收）", ("角色", "功能", "用户提供的验收标准", "优先级", "验收结果"), [
        tuple(_get(r, key) for key in ("role", "feature", "acceptance", "priority"))+("",) for r in records])
    out += _table("需求来源与系统模块归属", ("系统模块", "需求编号", "角色", "场景", "功能", "数据对象", "验收条件", "优先级", "需求来源", "待核项", "用户实现记录（未核）", "验收签认"), [
        (_identity(r),)+tuple(_get(r, key) for key in ("requirement_id", "role", "scene", "feature"))
        + (r.get("object", r.get("data", _MISSING)), _get(r, "acceptance"), _get(r, "priority"),
           _source(r, "feature", "requirement_source"), _pending(r, ("scene", "object", "acceptance")),
           _get(r, "user_implementation"), "") for r in records])
    out += _scoped_fields("非功能与数据来源", records, [
        ("非功能要求（用户提出，待评估）", "nonfunctional"),
        ("并发目标（待评估）", "concurrency"), ("保存期限", "retention"), ("离线需求", "offline"),
        ("期望交付时间（用户期望，未承诺）", "delivery"), ("待核记录", "pending_record")])
    interfaces = facts.children("interfaces") or [{}]
    out += _table("接口边界（仅系统名称）", ("对端系统", "方向", "主数据来源", "失败重试", "接口文档核对"), [
        tuple(_get(r, key) for key in ("peer", "direction", "source", "retry"))+("",) for r in interfaces])
    out += _table("接口与主数据归属", ("系统模块", "关联需求", "对端系统", "方向", "主数据来源", "失败重试", "接口资料来源", "待核项", "接口签认"), [
        (_identity(r), _get(r, "feature"))+tuple(_get(r, key) for key in ("peer", "direction", "source", "retry"))
        + (_source(r, "peer", "interface_source"), _pending(r, ("direction", "source", "retry")), "") for r in interfaces])
    out += _table("交接检查", ("事项", "接口岗位/责任", "证据", "核对签认"), [
        ("角色最小权限、审计及权限移交", "it-ops / 业务负责人待指定", _MISSING, ""),
        ("恢复目标、备份与恢复演练", "it-data / 数据负责人待指定", _MISSING, ""),
        ("培训、使用说明与验收记录", "项目责任人待指定", _MISSING, ""),
    ])
    out += "未指定模块不默认纳入范围。验收应核对可观察行为与按角色拒绝访问等边界；没有证据时不填通过。需求与时间仅供评估，不形成交付、报价或上线承诺；本稿不开发或部署系统，弱电点位及桥架另交对应设计岗。\n"
    return out


def build_draft(expert_id: str, tool_name: str, text: str) -> str | None:
    """Return sanitized Markdown for the exact post/tool pair, with no I/O."""
    if _TOOLS.get(expert_id) != tool_name:
        return None
    facts = _Facts(text, expert_id)
    return {"it-ops": _ops, "it-data": _data, "it-app": _app}[expert_id](facts)
