"""Literal cash-plan records; no cash transfers, netting or financing advice.

Forecast and user-reported actual amounts are separate. Each transaction,
account and source owns its evidence; a signed valuation is not cash received.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

from packing_assistant.post_drafts.planning import _inputs, _table
from packing_assistant.post_drafts.procurement import _source_name

MISSING = "[A001] / UNSPECIFIED"
TOOL = "finance-fund__plan"
DISCLAIMER = ("AI 草稿 · 内部讨论 · 不得作为法定专项方案 / 交底签认件。本稿仅整理用户资金资料，"
              "不构成付款指令、银行划款依据或融资承诺。未核验银行流水、账户余额、合同或审批原件，"
              "不计算可支配现金、资金缺口或授信余量；正式资料与权限须由责任人复核签认。")
_NUMBER = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_CURRENCY = r"(?:(?<![A-Za-z0-9_])(?:SGD|CNY|RMB|USD|EUR|HKD|AUD|NZD|GBP|JPY|CHF|CAD|MYR|INR)(?![A-Za-z_])|人民币|美元|新加坡元|新币|欧元|S\$|US\$|[¥￥$€])"
_MONEY = re.compile(r"(?<![A-Za-z0-9_.#/-])(?:"+_CURRENCY+r"\s*"+_NUMBER+r"\s*(?:亿元|万元|亿|万|元)?|"+_NUMBER+r"\s*(?:亿元|万元|亿|万|元|美元|新币))(?![A-Za-z0-9_.#/-])", re.I)
_MONEY_VALUE = re.compile(r"(?:"+_CURRENCY+r"\s*)?"+_NUMBER+r"\s*(?:亿元|万元|亿|万|元|美元|新币)?", re.I)
_DATE = r"(?:\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?|\d{1,2}月\d{1,2}[日号]|\d{1,2}月底(?:前)?|月底(?:前)?|月末|本周|下周|本月|这个月|下月)"
_UNKNOWN = re.compile(r"未提供|未给|未定|未知|待填|待核|待补|没给|没定|没数|没报|还没|没有|不知道|多少|UNSPECIFIED|TBD|N/A", re.I)
_UNSAFE = re.compile(r"例如|比如|假设|如果|示例|样例|自行计算|自己算|不采用|不按|别按|不要|不是|是否|[？?]")
_NEGATED = re.compile(r"未到账|尚未到账|没有到账|还未到账|没到账|未支付|未付款|尚未支付|没有支付|还没(?:付|收|到)|没付|尚未收到|未收到|并未|不算|预计|计划|拟|将|说是|听说|据说")
_INCOME = re.compile(r"履约保证金退还|投标保证金退还|质量保证金返还|质保金返还|业主进度款|甲方进度款|工程进度款|进度款|预付款|竣工结算款|结算款|公司拨款|业主到账")
_PAYMENT = re.compile(r"脚手架租赁|机械租赁押金|机械租赁|材料款|钢筋款|人工费代发|人工费|工资(?!专户|专用账户)|现场经费|分包款|采购款|税费")
_ACCOUNT = re.compile(r"农民工工资专用账户|工资专用账户|工资专户|基本结算户|基本户|一般户")
_ACTUAL = re.compile(r"已到账|已经到账|已收到|已经收到|已支付|已经支付|已付款|已经付款|实际收款|实际付款|实收|实付")
_FIELDS = {
    "project": ("项目名称", "工程名称", "项目", "Project"), "jurisdiction": ("辖区", "Jurisdiction"),
    "unit": ("楼栋", "单位工程", "部位", "Building"),
    "period": ("计划期间", "计划周期", "计划期", "期间", "Period"), "currency": ("币种", "Currency"),
    "version": ("计划版本", "版本", "Version"), "prepared": ("编制人", "Prepared by"),
    "income": ("收款节点", "收入事项", "收入", "收款", "回款", "Income"),
    "payment": ("付款事项", "支出事项", "付款", "支出", "Payment"), "kind": ("收支类别", "类别", "Direction"),
    "item": ("节点名称", "款项名称", "事项", "款项", "Item"), "transaction_id": ("款项编号", "交易编号", "Transaction ID"),
    "counterparty": ("收款人", "付款人", "供应商", "供方", "对象", "Counterparty"),
    "account": ("收款账户", "到账账户", "付款账户", "账户", "Account"),
    "amount": ("用户金额", "收款金额", "付款金额", "计划金额", "预计金额", "金额", "Amount"),
    "actual_amount": ("用户实际收付金额", "实际金额", "Actual amount"),
    "received_amount": ("实际到账金额", "实际收款金额", "实收金额"),
    "paid_amount": ("实际付款金额", "已付金额", "实付金额"),
    "planned_date": ("计划收款日", "计划付款日", "计划日", "预计到账日", "节点日期", "Planned date"),
    "actual_date": ("实际到账日", "实际付款日", "到账日", "Actual date"),
    "state": ("到账状态", "付款状态", "状态", "确定性", "State"),
    "evidence": ("确定性证据", "回款证据", "付款依据", "余额证据", "证据", "依据", "Evidence"),
    "source": ("来源文件", "资料来源", "来源", "Source"), "contract": ("合同编号", "合同", "Contract"),
    "purpose": ("用途", "支付用途", "Purpose"), "method": ("付款方式", "计费方式", "支付方式", "Method"),
    "opening_amount": ("期初可用余额", "期初余额", "Opening balance"),
    "closing_amount": ("用户期末余额", "期末余额", "Closing balance"),
    "balance_source": ("余额来源", "余额来源文件", "银行对账单", "Balance source"),
    "lender": ("融资方", "贷款方", "Lender"), "finance_id": ("融资编号", "授信编号", "Finance ID"),
    "credit": ("用户授信额度", "授信额度", "Credit limit"), "restriction": ("融资限制", "资金限制", "限制", "Restriction"),
    "condition": ("审批条件", "支付条件", "Condition"), "approval": ("审批事项", "审批申请", "Approval item"),
    "approver": ("审批人", "审批方", "Approver"), "approval_state": ("审批状态", "Approval state"),
}
ALIASES = {a.casefold(): k for k, labels in _FIELDS.items() for a in labels}
_LABEL = re.compile(r"^("+"|".join(re.escape(a) for a in sorted(ALIASES, key=len, reverse=True))+r")\s*[:：=]\s*(.*)$", re.I)
_META = {"project", "jurisdiction", "period", "currency", "version", "prepared"}
_BALANCE = {"opening_amount", "closing_amount", "balance_source"}
_FINANCE = {"lender", "finance_id", "credit", "restriction"}
_APPROVAL = {"approval", "approver", "approval_state"}


@dataclass
class Data:
    contexts: list[dict[str, str]] = field(default_factory=list)
    income: list[dict[str, str]] = field(default_factory=list)
    payments: list[dict[str, str]] = field(default_factory=list)
    balances: list[dict[str, str]] = field(default_factory=list)
    financing: list[dict[str, str]] = field(default_factory=list)
    approvals: list[dict[str, str]] = field(default_factory=list)
    unassigned: list[dict[str, str]] = field(default_factory=list)
    evidence: list[dict[str, str]] = field(default_factory=list)


def _value(value):
    value = value.strip()
    if not re.search(r"&(?:#\d+|#x[\da-f]+|[a-z][\da-z]+);$", value, re.I):
        value = value.rstrip("，,；;。")
    return MISSING if not value or _UNKNOWN.fullmatch(value) else value


def _typed(key, value):
    value = _value(value)
    if key == "jurisdiction" and value == MISSING:
        return "UNSPECIFIED"
    if key in {"amount", "actual_amount", "received_amount", "paid_amount", "opening_amount", "closing_amount", "credit"}:
        if _UNSAFE.search(value) or _UNKNOWN.search(value) or not _MONEY_VALUE.fullmatch(value):
            return MISSING
    if key in {"planned_date", "actual_date"}:
        if _UNSAFE.search(value) or _UNKNOWN.search(value) or not re.fullmatch(_DATE, value):
            return MISSING
    if key in {"counterparty", "approver", "lender"} and (_UNSAFE.search(value) or _UNKNOWN.search(value)):
        return MISSING
    return value


def _scan(text):
    text = re.sub(r"^(?:本次实际情况|实际情况|真实记录)\s*[:：]\s*", "", text)
    actual_label = re.match(r"^(实际收款|实际付款|已到账|已支付|实收|实付)\s*[:：=]\s*(.*)$", text)
    if actual_label:
        label, value = actual_label.groups()
        direction = "income" if label in {"实际收款", "已到账", "实收"} else "payments"
        detail = _scan_details(value)
        if _MONEY_VALUE.fullmatch(value):
            detail.append(("amount", value))
        if not (_NEGATED.search(value) or _UNSAFE.search(value)):
            detail = [("actual_amount" if k == "amount" else "actual_date" if k == "planned_date" else k, v) for k, v in detail]
        identity = (_INCOME if direction == "income" else _PAYMENT).search(value)
        return ([("income" if direction == "income" else "payment", identity.group())] if identity
                else [("actual_direction", direction)])+detail
    match = (_LABEL.match(text)
             or re.match(r"^(收入|收款|回款|支出|付款)\s+(.+)$", text))
    if match:
        key, value = ALIASES[match.group(1).casefold()], match.group(2)
        if key not in {"income", "payment"}:
            return [(key, value)]
        return [(key, value), *_scan_details(value)]
    facts = []
    incoming, outgoing = _INCOME.search(text), _PAYMENT.search(text)
    if incoming and outgoing:
        return [("item", text), *_scan_details(text)]  # no guessed direction
    if incoming:
        facts.append(("income", incoming.group()))
    elif outgoing:
        facts.append(("payment", outgoing.group()))
    # A plan month is literal; never manufacture a year from today's date.
    if "资金计划" in text:
        period = re.search(r"\d{4}[-/.年]\d{1,2}(?:月)?|\d{1,2}月", text)
        if period:
            facts.append(("period", period.group()))
    elif text in {"本月", "这个月", "下月", "本周", "下周"}:
        facts.append(("period", text))
    facts += _scan_details(text)
    return facts


def _scan_details(text):
    facts = []
    if _UNSAFE.search(text):
        return facts
    actual = _ACTUAL.search(text) and not _NEGATED.search(text)
    if actual:
        facts.append(("actual_direction", "payments" if re.search(r"已支付|已经支付|已付款|已经付款|实际付款|实付", text) else "income"))
    money = list(_MONEY.finditer(text))
    # Several amounts with no labels cannot all become one transaction amount.
    if len(money) == 1:
        facts.append(("actual_amount" if actual else "amount", money[0].group().strip()))
    dates = list(re.finditer(_DATE, text))
    if len(dates) == 1 and re.search(r"计划|到|付|拨|收|月[底末]|说是", text):
        facts.append(("actual_date" if actual else "planned_date", dates[0].group()))
    account = _ACCOUNT.search(text)
    if account:
        facts.append(("account", account.group()))
    currency = re.search(_CURRENCY, text, re.I)
    if currency:
        facts.append(("currency", currency.group()))
    unit = re.search(r"\d+[#＃]楼|[一二三四五六七八九十\d]+号楼", text)
    if unit:
        facts.append(("unit", unit.group()))
    state = re.search(r"未报量|已报未批|已批未付|未到账|已到账|未支付|已支付", text)
    if state:
        facts.append(("state", text if _NEGATED.search(text) and _ACTUAL.search(text) else state.group()))
    if re.search(r"计量单|已签认|已经签认|银行流水|银行回单|实名制工资表|商务说|没批|批没批|合同写|等.*给", text):
        facts.append(("evidence", text))
    company = re.search(r"([\u4e00-\u9fff]{2,16}(?:钢贸|商砼|架管|机械|有限公司))", text)
    if company:
        value = re.sub(r"^.*?(?:要给|给|付给|付款给)", "", company.group())
        facts.append(("counterparty", value))
    method = re.search(r"按月付|按月付款|包月|按节点付款|一次性付款", text)
    if method:
        facts.append(("method", method.group()))
    purpose = re.search(r"[\u4e00-\u9fff]{2,8}班(?:人工费)?", text)
    if purpose:
        facts.append(("purpose", purpose.group()))
    return facts


def parse(raw):
    data = Data()
    scope = {"jurisdiction": "UNSPECIFIED"}
    data.contexts.append(scope)
    origin = ""
    current = None
    mode = "unassigned"

    def new(collection, values=None):
        row = {"_origin": origin, "_scope": len(data.contexts)-1,
               **{k: v for k, v in scope.items() if k in _META}, **(values or {})}
        getattr(data, collection).append(row)
        return row

    def put(row, key, value):
        value = _typed(key, value)
        if row.get(key) in {None, MISSING, "UNSPECIFIED"}:
            row[key] = value
        elif value != MISSING and value not in row[key].split("；"):
            row[key] += "；"+value
        if value not in {MISSING, "UNSPECIFIED"}:
            data.evidence.append({"item": row.get("item", MISSING), "account": row.get("account", MISSING),
                                  "field": _FIELDS[key][0], "value": value, "location": location})

    for content, is_table, offset, source in _inputs(raw):
        location = (origin+" · " if origin else "")+f"本轮输入第{raw[:offset].count(chr(10))+1}行（非原件页码）"
        if content is None:
            current, mode = None, "unassigned"
            continue
        marker = _source_name(source)
        if marker is not None or source.startswith(("【本轮用户要求", "【此前用户资料")):
            origin = marker if marker is not None else source.strip("【】")
            scope = {"_origin": origin, "jurisdiction": "UNSPECIFIED"}
            data.contexts.append(scope)
            current, mode = None, "unassigned"
            continue
        pairs = ([(ALIASES[label.casefold()], value) for label, value in content if label.casefold() in ALIASES]
                 if is_table else _scan(source))
        if not pairs:
            continue
        normalized = []
        for key, value in pairs:
            if key in {"received_amount", "paid_amount"}:
                if _typed(key, value) != MISSING:
                    normalized.append(("actual_direction", "income" if key == "received_amount" else "payments"))
                normalized.append(("actual_amount", value))
            else:
                normalized.append((key, value))
        pairs = normalized
        stated = dict(pairs)
        actual_directions = {v for k, v in pairs if k == "actual_direction"}
        declared = ("income" if "income" in stated and "payment" not in stated else "payments" if "payment" in stated and "income" not in stated
                    else {"收入": "income", "收款": "income", "income": "income", "支出": "payments", "付款": "payments", "payment": "payments"}.get(stated.get("kind", "").casefold()))
        conflicted = len(actual_directions) > 1 or bool(actual_directions and declared and declared not in actual_directions)
        local_meta = {k: _typed(k, v) for k, v in stated.items() if k in _META | {"unit"}}
        scope_identities = {"project", "version"} | ({"period", "currency", "jurisdiction"} if current is None else set())
        if not is_table and any(k in stated and scope.get(k) not in {None, MISSING, "UNSPECIFIED", _typed(k, stated[k])}
                                for k in scope_identities):
            scope = {"_origin": origin, "jurisdiction": "UNSPECIFIED"}
            data.contexts.append(scope)
            current, mode = None, "unassigned"
        if not is_table and current is not None and not any(k in stated for k in {"income", "payment", "kind", "item"}) and not actual_directions and any(k in stated and current.get(k) not in {None, MISSING, "UNSPECIFIED", _typed(k, stated[k])}
                                                       for k in {"period", "currency", "jurisdiction", "unit"}):
            current = new(mode, local_meta)
        if "actual_direction" in stated and not declared and not is_table and not conflicted and (mode != stated["actual_direction"] or current is None):
            mode = stated["actual_direction"]
            current = new(mode, local_meta)
        if conflicted:
            # Preserve the named planned node separately, but do not attribute
            # opposite-direction actual cash to it. All conflicting cash facts
            # remain an explicitly unassigned record, not a payment/receipt.
            for key in ("income", "payment"):
                if key in stated:
                    value = stated[key]
                    noun = (_INCOME if key == "income" else _PAYMENT).search(value)
                    new("income" if key == "income" else "payments", {**local_meta, "item": noun.group() if noun else _value(value)})
            mode, current = "unassigned", new("unassigned", {**local_meta, "evidence": source, "kind": "收付方向冲突，待核"})
        elif is_table or "income" in stated or "payment" in stated or "kind" in stated or "item" in stated:
            identities = [k for k in ("income", "payment") if k in stated]
            kind = _value(stated.get("kind", ""))
            direction = {"收款": "income", "收入": "income", "income": "income", "付款": "payments", "支出": "payments", "payment": "payments"}.get(kind.casefold())
            if len(identities) == 1:
                mode = "income" if identities[0] == "income" else "payments"
            elif len(identities) > 1:
                for key in identities:
                    new("income" if key == "income" else "payments", {**local_meta, "item": _value(stated[key])})
                mode = "unassigned"
            elif any(k in stated for k in _BALANCE):
                mode = "balances"
            elif any(k in stated for k in _FINANCE):
                mode = "financing"
            elif any(k in stated for k in _APPROVAL):
                mode = "approvals"
            else:
                mode = direction or "unassigned"
            current = new(mode, local_meta)
        elif any(k in stated for k in _BALANCE):
            if mode != "balances":
                provisional = current if mode == "unassigned" and current is not None and set(current) <= _META | {"_origin", "_scope", "account"} else None
                mode, current = "balances", new("balances", {"account": provisional["account"]} if provisional and "account" in provisional else None)
                if provisional is not None:
                    data.unassigned.remove(provisional)
        elif any(k in stated for k in _FINANCE):
            if mode != "financing" or any(k in stated and current.get(k) not in {None, MISSING, _value(stated[k])} for k in {"lender", "finance_id"}):
                mode, current = "financing", new("financing")
        elif any(k in stated for k in _APPROVAL):
            if mode != "approvals" or "approval" in stated and current.get("approval") not in {None, MISSING, _value(stated["approval"])}:
                mode, current = "approvals", new("approvals")
        elif "account" in stated and mode == "balances" and current.get("account") not in {None, MISSING, _value(stated["account"])}:
            current = new("balances")
        elif "counterparty" in stated and current is not None and current.get("counterparty") not in {None, MISSING, _value(stated["counterparty"])}:
            current = new(mode)
        elif "transaction_id" in stated and current is not None and current.get("transaction_id") not in {None, MISSING, _value(stated["transaction_id"])}:
            current = new(mode)
        # Metadata is copied only into its explicit row or current source scope,
        # never globally applied across files, projects or versions.
        for key, value in pairs:
            if key == "actual_direction":
                continue
            if key in {"income", "payment"}:
                if not conflicted and len(identities) == 1:
                    put(current, "item", _INCOME.search(value).group() if key == "income" and _INCOME.search(value)
                        else _PAYMENT.search(value).group() if key == "payment" and _PAYMENT.search(value) else value)
                continue
            if key in _META:
                put(current if current is not None else scope, key, value)
                continue
            if current is None:
                current = new("unassigned")
            put(current, key, value)
    data.contexts = [r for r in data.contexts if len(r) > 1] or data.contexts[:1]
    return data


def _balances(rows):
    """A number without its own source is not a cash-balance field."""
    result = []
    for row in rows:
        available = (row.get("balance_source") or row.get("source") or row.get("evidence"))
        if available and (_UNKNOWN.search(available) or re.search(r"待|以.*为准|需提供|未收到|尚未|没收到", available)):
            available = None
        result.append({**row, "opening_amount": row.get("opening_amount", MISSING) if available else MISSING,
                       "closing_amount": row.get("closing_amount", MISSING) if available else MISSING})
    return result


def build_draft(post, tool_name, text):
    if post != "finance-fund" or tool_name != TOOL:
        return None
    data = parse(text)
    provenance = (("项目名称", "project"), ("楼栋 / 单位工程", "unit"), ("计划期 / 期间", "period"), ("币种（用户原文）", "currency"),
                  ("账户（用户原文）", "account"), ("辖区", "jurisdiction"), ("输入资料名", "_origin"))
    cash = (("节点名称 / 收支事项", "item"), ("款项编号", "transaction_id"), ("对象 / 收付款方", "counterparty"), ("用户收支类别 / 归属待核", "kind"))
    forecast = (("预计 / 计划金额原文", "amount"), ("计划日（非收付事实）", "planned_date"))
    actual = (("用户实际收付金额（未核）", "actual_amount"), ("用户实际收付日（未核）", "actual_date"))
    detail = (("用户状态（未核）", "state"), ("确定性证据 / 待核资料", "evidence"), ("来源文件", "source"),
              ("合同编号", "contract"), ("用途", "purpose"), ("支付 / 计费方式", "method"), ("支付 / 审批条件", "condition"))
    columns = cash+forecast+actual+provenance+detail
    return "\n".join([
        "# 项目资金计划草稿（AI 草稿 · 内部讨论）", "", DISCLAIMER, "", "- submit_blocked=true", "",
        _table(data.contexts, (("项目名称", "project"), ("计划期 / 期间", "period"), ("币种", "currency"), ("辖区", "jurisdiction"),
                               ("计划版本", "version"), ("用户编制人", "prepared"), ("输入资料名", "_origin"))), "",
        "## 1 封面与范围声明", "", "编制、复核与签认：（空）。只整理本轮资料，不以当前日期、国家或币种替用户补齐空栏。", "",
        "## 2 期间与余额", "", _table(_balances(data.balances), provenance+(("用户期初余额（来源原件未核）", "opening_amount"),
            ("用户期末余额（来源原件未核）", "closing_amount"), ("余额来源", "balance_source"), ("资料来源", "source"), ("余额证据", "evidence")),
            extra=(("余额原件核验", "本轮未核验"),)), "",
        "余额无对应来源时不填入余额栏；有来源也只是用户资料登记，不视为本轮核定可支配金额。", "",
        "## 3 编制原则与融资约束", "", _table(data.financing, (("融资方", "lender"), ("融资 / 授信编号", "finance_id"), ("用户授信额度原文（未核）", "credit"),
            ("融资 / 资金限制", "restriction"), ("审批条件", "condition"), ("依据 / 证据", "evidence"), ("来源", "source"))+provenance,
            extra=(("本轮授信 / 可用额度核验", "未执行"),)), "",
        "收付计划、受限资金、专户及融资约束分别登记；不净额抵销、不换币、不编利率或借款方案。", "",
        "## 4 合同价款节点", "", _table(data.income+data.payments, cash+(("合同编号", "contract"), ("计划日", "planned_date"), ("证据 / 依据", "evidence"))+provenance), "",
        "节点与约定只抄用户。不从计量签认、申请、审批或催款推定已收付；未给合同条款、比例或时限保持待核。", "",
        "## 5 收入栏", "", _table(data.income, columns, extra=(("本轮到账原件核验", "未执行"),)), "",
        "预计回款与用户实际到账金额严格分栏。实际日期或到账状态不能替代未提供的实际金额；计量单签认不是银行到账凭证。本轮核定收款合计 TBD。", "",
        "## 6 支出栏", "", _table(data.payments, columns, extra=(("本轮付款 / 签认", "未执行 /（空）"),)), "",
        "付款需求不等于实际付款。账户按本笔资料登记，不将工资专户自动套用到材料款，也不以受限账户余额平衡其他账户。本轮核定付款合计 TBD。", "",
        "## 7 平衡试算", "", "| 本轮核定期初可用 | 本轮核定收款合计 | 本轮核定付款合计 | 期末 / 资金缺口 |\n| --- | --- | --- | --- |\n| TBD | TBD | TBD | 无法试算 |", "",
        "本轮未建立全量账目与账户、期间、币种、受限资金的核验口径，不计算净额、盈余、可付额度或资金覆盖结论。", "",
        "## 8 风险提示与未分派资料", "", _table(data.unassigned, columns+(("融资限制", "restriction"), ("审批条件", "condition"))), "",
        "身份、收支类别、账户或金额归属不明时单列待核，不借上一笔款项补齐；不同项目、版本、文件和币种不混为一笔。", "",
        "## 9 自检与审批跟踪", "", _table(data.approvals, (("审批事项", "approval"), ("审批方", "approver"), ("用户审批状态（未核）", "approval_state"),
            ("审批条件", "condition"), ("依据 / 证据", "evidence"), ("来源", "source"))+provenance, extra=(("本轮审批 / 付款签认", "未执行 /（空）"),)), "",
        "审批陈述仅为用户记录，不产生转账指令、批准状态或融资承诺；手续、依据与权限由责任人另行核对。", "",
        "## 10 禁令与证据位置", "", _table(data.evidence, (("记录节点 / 事项", "item"), ("记录账户", "account"), ("记录项", "field"),
            ("用户证据原文", "value"), ("输入位置（非原件页码）", "location"))), "",
        "无来源金额 [A001] / UNSPECIFIED。现行规则与合同原文待提供，不默写国家、法规版本、税率或付款义务。", "",
    ])
