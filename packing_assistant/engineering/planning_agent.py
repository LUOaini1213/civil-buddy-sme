"""Host-bound planning tools: explicit user inputs become reviewable proposals.

Neither model arguments nor a model reply can change a plan. Applying a cached
proposal is a separate host action; persistence and export gates stay in the host.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

from packing_assistant.runtime.cancel import check
from .planning import calculate, validate_plan

TOOL_NAMES = frozenset({"planning_inspect", "planning_explain", "planning_propose", "planning_undo"})
SCHEMA = "civil-buddy.planning.proposal.v1"
ID = r"[A-Za-z][A-Za-z0-9_-]{0,63}"
SET = r"(?:改为|改成|改到|设为|设置为|设置成|调整为|调整到|缩短到|延长到)"
HELP = (
    "支持用已有编号提出修改，例如：任务 B 工期改为 5 工作日；任务 B 进度改为 40%；"
    "任务 B 前置依赖改为 A FS+0；任务 B 添加前置依赖 C SS+2；任务 B 删除前置依赖 C SS；"
    "工作日改为周一至周五；添加假日 2026-10-01；开始日期改为 2026-10-08；"
    "资源 crew 容量改为 2；任务 B 资源 crew 需求改为 1。"
    "多项用分号分隔；依赖时距单位为工作日，设置关系时须明确 +0 或其他时距。"
    "先审阅原值→新值，再点击应用；对话本身不保存、不导出。"
)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def plan_digest(plan):
    return hashlib.sha256(_canonical(validate_plan(plan)).encode("utf-8")).hexdigest()


def _method(method):
    if not isinstance(method, str) or method not in {"cpm", "resource"}:
        raise ValueError("排程方法只能是 cpm 或 resource。")
    return method


def _text(message):
    if not isinstance(message, str) or not message.strip() or len(message) > 4000:
        raise ValueError("请输入 1–4000 字的明确排程要求。")
    return message.strip().strip("。.!！")


def _task(plan, ident):
    row = next((row for row in plan["tasks"] if row["id"] == ident), None)
    if row is None:
        raise ValueError(f"未知任务 {ident}；仅可修改当前计划已有编号。")
    return row


def _resource(plan, ident):
    row = next((row for row in plan["resources"] if row["id"] == ident), None)
    if row is None:
        raise ValueError(f"未知资源 {ident}；请先在参数面板创建资源。")
    return row


def _weekdays(value):
    numbers = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
    match = re.fullmatch(r"(?:周|星期)([一二三四五六日天])(?:至|到|-)(?:周|星期)?([一二三四五六日天])", value)
    if match:
        start, end = (numbers[n] for n in match.groups())
        if start > end:
            raise ValueError("跨周工作日请逐日列出，避免日历歧义。")
        return list(range(start, end + 1))
    values = re.split(r"[、,，]", value)
    days = []
    for item in values:
        match = re.fullmatch(r"(?:周|星期)([一二三四五六日天])", item)
        if not match:
            raise ValueError("工作日请明确写周一至周五，或周一、周三、周五。")
        days.append(numbers[match[1]])
    if len(set(days)) != len(days):
        raise ValueError("工作日不能重复。")
    return sorted(days)


def _command(plan, raw):
    # English aliases use the same bounded, full-match grammar and validators.
    # IDs retain their case; questions, negation and trailing clauses do not match.
    english = raw.strip().rstrip(".")
    match = re.fullmatch(rf"(?:please\s+)?set\s+(?:task\s+)?({ID})\s+duration\s+to\s+([0-9]+)\s+working\s+days?", english, re.I)
    if match:
        _task(plan, match[1])["duration"] = int(match[2]); return
    match = re.fullmatch(rf"(?:please\s+)?set\s+(?:task\s+)?({ID})\s+(?:progress|completion)\s+to\s+([0-9]+(?:\.[0-9]+)?)\s*%", english, re.I)
    if match:
        _task(plan, match[1])["progress"] = float(match[2]); return
    match = re.fullmatch(rf"(?:please\s+)?set\s+resource\s+({ID})\s+capacity\s+to\s+([0-9]+)", english, re.I)
    if match:
        _resource(plan, match[1])["capacity"] = int(match[2]); return
    match = re.fullmatch(rf"(?:please\s+)?set\s+(?:task\s+)?({ID})\s+resource\s+({ID})\s+(?:demand|quantity)\s+to\s+([0-9]+)", english, re.I)
    if match:
        row = _task(plan, match[1]); _resource(plan, match[2])
        row["resources"][match[2]] = int(match[3]); return
    # Removing whitespace is safe only because every remaining token is matched
    # against this bounded grammar; trailing text, questions and negation fail.
    text = re.sub(r"\s+", "", raw).strip("。")
    text = re.sub(r"^(?:请)?(?:把|将)?", "", text)
    match = re.fullmatch(rf"(?:任务)?({ID})(?:的)?(?:工期|持续时间){SET}([0-9]+)(?:个)?(?:工作日|天)", text)
    if match:
        _task(plan, match[1])["duration"] = int(match[2]); return
    match = re.fullmatch(rf"(?:任务)?({ID})(?:的)?(?:进度|完成率){SET}([0-9]+(?:\.[0-9]+)?)(?:%|％)", text)
    if match:
        _task(plan, match[1])["progress"] = float(match[2]); return
    match = re.fullmatch(rf"(?:任务)?({ID})(?:的)?(?:前置依赖|前置任务|依赖){SET}(.+)", text)
    if match:
        row, value = _task(plan, match[1]), match[2]
        if value in {"无", "空"}:
            row["dependencies"] = []; return
        dependencies = []
        for part in re.split(r"[,，、]", value):
            dep = re.fullmatch(rf"({ID})(FS|SS|FF|SF)([+-][0-9]+)(?:工作日|天)?", part, re.I)
            if not dep:
                raise ValueError("依赖须写已有任务编号、FS/SS/FF/SF 以及明确时距，例如 A FS+0。")
            _task(plan, dep[1])
            dependencies.append({"task_id": dep[1], "type": dep[2].upper(), "lag": int(dep[3])})
        row["dependencies"] = dependencies; return
    match = re.fullmatch(rf"(?:任务)?({ID})(?:的)?添加(?:前置)?依赖({ID})(FS|SS|FF|SF)([+-][0-9]+)(?:工作日|天)?", text, re.I)
    if match:
        row = _task(plan, match[1]); _task(plan, match[2])
        row["dependencies"].append({"task_id": match[2], "type": match[3].upper(), "lag": int(match[4])}); return
    match = re.fullmatch(rf"(?:任务)?({ID})(?:的)?删除(?:前置)?依赖({ID})(FS|SS|FF|SF)", text, re.I)
    if match:
        row = _task(plan, match[1]); _task(plan, match[2])
        old = row["dependencies"]
        new = [dep for dep in old if (dep["task_id"], dep["type"]) != (match[2], match[3].upper())]
        if len(old) == len(new):
            raise ValueError("该前置依赖不存在，未删除任何关系。")
        row["dependencies"] = new; return
    match = re.fullmatch(rf"(?:每周)?工作日{SET}(.+)", text)
    if match:
        plan["calendar"]["weekdays"] = _weekdays(match[1]); return
    match = re.fullmatch(rf"(?:计划)?(?:开始日期|开工日期|起点日期){SET}([0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}})", text)
    if match:
        plan["start_date"] = match[1]; return
    match = re.fullmatch(rf"(?:假日|节假日){SET}(.+)", text)
    if match:
        plan["calendar"]["holidays"] = [] if match[1] in {"无", "空"} else re.split(r"[,，、]", match[1]); return
    match = re.fullmatch(r"(添加|删除)(?:假日|节假日)([0-9]{4}-[0-9]{2}-[0-9]{2})", text)
    if match:
        dates = plan["calendar"]["holidays"]
        if match[1] == "添加":
            if match[2] in dates: raise ValueError("假日已存在。")
            dates.append(match[2])
        else:
            if match[2] not in dates: raise ValueError("该假日不存在。")
            dates.remove(match[2])
        return
    match = re.fullmatch(rf"资源({ID})(?:的)?(?:容量|可用量){SET}([0-9]+)", text)
    if match:
        _resource(plan, match[1])["capacity"] = int(match[2]); return
    match = re.fullmatch(rf"(?:任务)?({ID})(?:的)?资源({ID})(?:的)?(?:需求|用量){SET}([0-9]+)", text)
    if match:
        row = _task(plan, match[1]); _resource(plan, match[2])
        row["resources"][match[2]] = int(match[3]); return
    match = re.fullmatch(rf"(?:任务)?({ID})(?:的)?(?:删除|移除)资源({ID})", text)
    if match:
        row = _task(plan, match[1]); _resource(plan, match[2])
        if match[2] not in row["resources"]: raise ValueError("任务未分配该资源。")
        del row["resources"][match[2]]; return
    raise ValueError("未识别这条明确修改命令：" + raw[:100] + "。" + HELP)


def _changes(before, after):
    changes = []
    def add(path, old, new):
        if old != new:
            changes.append({"parameter": path, "before": deepcopy(old), "after": deepcopy(new)})
    add("start_date", before["start_date"], after["start_date"])
    for key in ("weekdays", "holidays"):
        add("calendar." + key, before["calendar"][key], after["calendar"][key])
    for old, new in zip(before["tasks"], after["tasks"]):
        for key in ("duration", "progress", "dependencies", "resources"):
            add(f"tasks.{old['id']}.{key}", old[key], new[key])
    for old, new in zip(before["resources"], after["resources"]):
        add(f"resources.{old['id']}.capacity", old["capacity"], new["capacity"])
    return changes


def propose_command(plan, message, method="cpm"):
    """Validate an atomic proposal without calculating/saving/applying it."""
    check()
    method, text = _method(method), _text(message)
    before = validate_plan(plan)
    after = deepcopy(before)
    base_method = method
    method_command = re.fullmatch(r"(?:请)?(?:按资源容量优化|优化资源排程|改用资源排程|改用关键路径排程|计算关键路径|重新计算关键路径|重新计算计划)", re.sub(r"\s+", "", text))
    english_method = re.fullmatch(r"(?:please\s+)?(?:(optimise|optimize)\s+for\s+resource\s+capacity|(?:calculate|recalculate)\s+(?:the\s+)?critical\s+path)", text, re.I)
    commands = [part.strip() for part in re.split(r"[;；\n]+", text) if part.strip()]
    if not 1 <= len(commands) <= 30:
        raise ValueError("每次请提交 1–30 项明确修改。")
    if method_command or english_method:
        method = ("resource" if english_method[1] else "cpm") if english_method else "resource" if "资源" in text else "cpm" if "关键路径" in text else base_method
    else:
        for command in commands:
            check()
            _command(after, command)
    after = validate_plan(after)
    changes = _changes(before, after)
    if method_command or english_method:
        changes.append({"parameter": "method", "before": base_method, "after": method})
    if not changes:
        raise ValueError("指定值与当前计划一致，没有待应用的修改。")
    check()
    return {"schema": SCHEMA, "base_digest": plan_digest(before), "base_method": base_method, "method": method,
            "source_text": text, "changes": changes, "plan": after}


def apply_proposal(plan, proposal, *, confirmed=False, current_method=None):
    """For a host's explicit Apply action, never exposed as a model tool."""
    check()
    if confirmed is not True:
        raise PermissionError("请先核对原值与新值，再由用户明确点击应用。")
    if not isinstance(proposal, dict) or proposal.get("schema") != SCHEMA:
        raise ValueError("排程建议格式不正确。")
    if proposal.get("base_digest") != plan_digest(plan):
        raise ValueError("原计划已发生变化，请基于最新计划重新提出建议。")
    if current_method is not None and _method(current_method) != proposal.get("base_method"):
        raise ValueError("排程方法已改变，请重新提出建议。")
    expected = propose_command(plan, proposal.get("source_text"), proposal.get("base_method"))
    if _canonical(expected) != _canonical(proposal):
        raise ValueError("建议内容与用户原话不一致，拒绝应用。")
    if expected["method"] == "resource":
        from .worker import run
        computed = run("planning_optimize", expected["plan"], timeout=40)
    else:
        computed = calculate(expected["plan"])
    check()
    return {**computed, "method": expected["method"]}


def inspect_plan(context):
    """Recompute deterministic CPM evidence; distinguish the saved resource run."""
    if not isinstance(context, dict) or "plan" not in context:
        raise ValueError("请先选择一份已有施工计划。")
    method = _method(context.get("method", "cpm"))
    computed = calculate(context["plan"])
    result = computed["result"]
    check()
    return {"project": deepcopy(context.get("project") or {}), "method": method,
            "plan": computed["plan"], "analysis_method": "cpm",
            "duration_workdays": result["duration_workdays"], "finish_date": result["finish_date"],
            "critical_task_ids": result["critical_task_ids"], "tasks": result["tasks"],
            "resource_conflicts": result.get("resource_conflicts", []),
            "resources": result.get("resources", []), "warnings": result.get("warnings", []),
            "current_result": deepcopy(context.get("result")) if method == "resource" else result,
            "notice": "关键任务指无资源约束 CPM 下总时差为零的任务，列表不代表唯一串行线路。"
                      "资源冲突也以 CPM 日期检查；资源方案的实际日期单独展示，不把它的空时差解释成零。"}


def operation(message, context=None):
    text = _text(message)
    if re.fullmatch(r"(?:please\s+)?(?:check|inspect|review)\s+(?:the\s+|current\s+)?(?:plan|schedule)\??", text, re.I):
        return "planning_inspect"
    if re.fullmatch(r"(?:please\s+)?undo\s+(?:the\s+)?last\s+save", text, re.I):
        return "planning_undo"
    if re.fullmatch(r"(?:请)?(?:撤销|撤回)(?:上一次|上次|刚才的)?(?:保存|修改|操作)?", text):
        return "planning_undo"
    if re.search(r"改用|计算|优化", text):
        return "planning_propose"
    if re.search(r"关键|资源(?:冲突|超配|瓶颈)|(?:为何|为什么).*延期", text):
        # Do not mistake a compound edit request for an explanation and lose it.
        if not re.search(SET + r"|添加|删除|移除", text):
            return "planning_explain"
    if re.fullmatch(r"(?:请)?(?:检查|查看|分析|解释)(?:一下)?(?:当前|这份|已有)?(?:施工)?(?:计划|排程)(?:情况|状态)?[？?]?", text):
        return "planning_inspect"
    return "planning_propose"


def execute(context, name, args, *, user_text):
    check()
    if name not in TOOL_NAMES:
        return {"ok": False, "error_code": "unknown_tool", "reason": "排程对话没有此工具。"}
    if not isinstance(args, dict) or args:
        return {"ok": False, "error_code": "invalid_args", "reason": "排程工具不能接收模型提供的编号、数值、命令、路径或确认。"}
    if name == "planning_undo":
        if operation(user_text, context) != name:
            return {"ok": False, "error_code": "read_only_intent", "reason": "用户本轮未要求撤销。"}
        if context.get("can_undo") is not True and (context.get("project") or {}).get("can_undo") is not True:
            return {"ok": False, "error_code": "no_undo", "reason": "当前计划没有可撤销的已保存版本，未执行撤销。"}
        return {"ok": True, "planning_action": "undo", "summary": "请核对将恢复的保存版本，再点击确认撤销；当前计划未修改。"}
    if name == "planning_propose":
        proposal = propose_command(context["plan"], user_text, context.get("method", "cpm"))
        return {"ok": True, "summary": "已形成待确认建议，当前计划未修改、未保存。", "planning_proposal": proposal}
    return {"ok": True, "summary": "已检查当前计划，以下依据来自实际 CPM 计算。", "inspection": inspect_plan(context)}


def reply_for(results, context=None):
    """Tool-grounded reply: arbitrary model claims never certify a mutation."""
    errors = [str(row.get("reason") or "排程工具未完成。") for row in results if not row.get("ok")]
    if errors:
        return "本轮未应用或保存计划。\n" + "\n".join(dict.fromkeys(errors))
    if any(row.get("planning_action") == "undo" for row in results):
        return "已提出撤销请求，当前计划未修改。请核对将恢复的保存版本，再点击确认撤销。"
    proposals = [row["planning_proposal"] for row in results if row.get("planning_proposal")]
    if proposals:
        lines = ["已形成待确认建议，当前计划未修改、未保存。"]
        for item in proposals[-1]["changes"]:
            lines.append(f"- {item['parameter']}：{_canonical(item['before'])} → {_canonical(item['after'])}")
        return "\n".join(lines) + "\n请核对后点击应用；应用时会重新计算，失败则保留原计划。"
    inspections = [row["inspection"] for row in results if row.get("inspection")]
    if not inspections:
        return "本轮没有完成排程工具操作，计划未修改。\n" + HELP
    item = inspections[-1]
    lines = [f"当前计划的无资源约束 CPM 工期为 {item['duration_workdays']} 工作日，完成日期 {item['finish_date']}。",
             "总时差为零的任务：" + "、".join(item["critical_task_ids"]) + "。", item["notice"]]
    critical = set(item["critical_task_ids"])
    for task in [row for row in item["tasks"] if row["id"] in critical][:12]:
        predecessors = "、".join(f"{dep['task_id']} {dep['type']}{dep['lag']:+d}" for dep in task["dependencies"]) or "无"
        lines.append(f"{task['id']}：{task['start']} 至 {task['end']}，工期 {task['duration']} 工作日，总时差 {task['total_float']}；前置 {predecessors}。")
    for conflict in item["resource_conflicts"][:20]:
        lines.append(f"资源 {conflict['resource_id']} 在 {conflict['start']} 至 {conflict['end']}：需求 {conflict['demand']}，容量 {conflict['capacity']}；涉及 " + "、".join(conflict["task_ids"]) + "。")
    if not item["resource_conflicts"]:
        lines.append("已明确录入的资源在 CPM 日期下未出现超配。")
    if len(item["resource_conflicts"]) > 20:
        lines.append("其余冲突见完整检查结果。")
    current = item.get("current_result")
    if item["method"] == "resource" and current:
        lines.append(f"当前资源方案记录：{current['duration_workdays']} 工作日，完成日期 {current['finish_date']}。")
    return "\n".join(lines)
