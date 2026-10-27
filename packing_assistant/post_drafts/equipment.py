"""Literal equipment, certificate, operator and maintenance records.

No inspection, capacity calculation, certificate validation or permission to
use machinery takes place. Independent devices and source files never borrow
each other's certificates, dates, operators or lease terms.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

from packing_assistant.post_drafts.planning import _inputs, _table
from packing_assistant.post_drafts.procurement import _source_name

MISSING = "[A001] / UNSPECIFIED"
TOOL = "equip__ledger"
DISCLAIMER = ("AI 草稿 · 内部讨论 · 不得作为法定专项方案 / 交底签认件。本稿仅整理用户资料，"
              "未检验设备、核验证件、计算额定能力或签发启用许可；不构成特种设备使用登记、安装验收签认、"
              "开复工依据或租赁结算批准。涉及安全的内容须由具备相应资格的人员复核签认。")
_DATE = r"(?:\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?|\d{1,2}月\d{1,2}[日号]|下周[一二三四五六日天]|本周[一二三四五六日天]|下周|本周|明天|昨天|昨日|今日|今天|\d{1,2}号)"
_MODEL = re.compile(r"(?<![A-Za-z0-9_.-])[A-Z]{1,12}\d+[A-Za-z0-9]*(?:[-_/][A-Za-z0-9]+)*(?![A-Za-z0-9_.-])", re.I)
_CODE = r"[A-Za-z0-9][A-Za-z0-9./_-]*"
_NAME = re.compile(r"施工升降机|旋挖钻(?:机)?|塔式起重机|汽车起重机|履带起重机|汽车吊|履带吊|塔吊|升降机|汽车泵|泵车|挖掘机|挖机|压路机|搅拌机|发电机|叉车|(?<![A-Za-z0-9_/-])(?:tower crane|mobile crane|crawler crane|excavator|forklift|hoist)(?![A-Za-z0-9_/-])", re.I)
_UNKNOWN = re.compile(r"还没|未提供|未给|未定|未报|未知|待填|待核|待补|没给|没定|没报|没排|没有|多少|UNSPECIFIED|TBD|N/A", re.I)
_UNSAFE = re.compile(r"例如|比如|假设|如果|示例|样例|自行计算|自己算|不采用|不按|别按|不要|不是|是否|[？?]")
_FIELDS = {
    "project": ("项目名称", "工程名称", "项目", "Project"), "jurisdiction": ("辖区", "Jurisdiction"),
    "name": ("设备名称", "机械名称", "名称", "设备", "Equipment", "Name"),
    "model": ("规格型号", "设备型号", "型号", "机型", "Model", "Specification"),
    "manufacturer": ("生产厂家", "制造商", "厂家", "品牌", "Manufacturer"),
    "device_id": ("设备编号", "机号", "设备ID", "Device ID"), "factory_id": ("出厂编号", "厂编号", "Factory ID"),
    "filing_id": ("备案号", "备案编号", "Filing ID"), "capacity": ("额定能力", "额定起重量", "吨位", "Capacity"),
    "quantity": ("设备数量", "台数", "数量", "Quantity"), "purpose": ("用途", "使用部位", "作业部位", "Purpose"),
    "ownership": ("自有或租赁", "权属", "产权", "Ownership"), "lessor": ("出租单位", "出租方", "租赁单位", "Lessor"),
    "lease_id": ("租赁合同编号", "租赁合同", "合同编号", "Lease ID"), "billing": ("计费方式", "租赁计费", "计费", "租赁方式", "Billing"),
    "rent": ("用户租金原文", "租金", "报价", "单价", "Rent"),
    "planned_arrival": ("计划进场日期", "计划进场", "拟进场日期", "Planned arrival"),
    "arrival": ("实际进场日期", "实际进场", "进场日期", "Arrival"),
    "planned_departure": ("计划退场日期", "计划退场", "Planned departure"), "departure": ("实际退场日期", "实际退场", "退场日期", "Departure"),
    "parking": ("停放位置", "停放部位", "停放地点", "Parking"), "state": ("当前状态", "用户状态", "设备状态", "状态", "State"),
    "installer": ("安装单位", "安装责任方", "Installer"), "remover": ("拆卸单位", "安拆单位", "Remover"),
    "owner": ("责任方", "责任单位", "设备责任人", "责任人", "Owner"), "source": ("证件来源", "来源文件", "资料来源", "来源", "Source"),
    "certificate": ("证书名称", "证件名称", "证件类型", "Certificate"), "cert_id": ("证书编号", "证件编号", "证号", "Certificate ID"),
    "certificate_expiry": ("证件有效期至", "证件有效期", "检定期限", "检定有效期", "有效期至", "有效期", "到期日", "Expiry"),
    "cert_date": ("检定日期", "检验日期", "证件签发日", "Inspection date"), "cert_state": ("证件状态", "报告状态", "证件收集状态", "Certificate state"),
    "product_id": ("产品合格证编号", "产品合格证", "合格证"), "registration_id": ("使用登记编号", "使用登记", "登记证"),
    "inspection_id": ("检验报告编号", "检验报告", "检测报告编号", "检测报告"), "verification_id": ("检定证书编号", "检定证书", "检定证"),
    "notice_id": ("安装告知编号", "安装告知"), "acceptance_id": ("安装验收报告编号", "安装验收报告"),
    "operator": ("操作人员", "作业人员", "司机", "Operator"), "operator_id": ("作业人员证件", "操作人员证号", "作业证", "操作证", "Operator licence"),
    "operator_expiry": ("人员证件有效期", "人员证件到期日", "Operator expiry"), "operator_scope": ("人员作业项目", "作业项目", "Licence scope"),
    "operator_state": ("人员证件状态", "上岗陈述", "Operator state"),
    "maintenance": ("维保计划", "维保事项", "维保", "保养计划", "Maintenance"),
    "frequency": ("维保周期", "保养周期", "点检周期", "周期", "Frequency"), "maintenance_date": ("计划维保日", "计划保养日"),
    "maintenance_owner": ("维保责任方", "维保单位", "保养责任人", "Maintenance owner"),
    "maintenance_actual": ("维保完成记录", "保养完成记录", "维保完成日期", "Maintenance completed"),
    "failure": ("故障记录", "故障", "异常", "Failure"), "stop": ("停用记录", "停用", "Stop record"),
    "repair": ("维修记录", "修理记录", "Repair"), "lifting": ("顶升计划", "附着计划", "Top climbing"),
}
ALIASES = {a.casefold(): key for key, aliases in _FIELDS.items() for a in aliases}
_LABEL = re.compile(r"^("+"|".join(re.escape(a) for a in sorted(ALIASES, key=len, reverse=True))+r")\s*[:：=]\s*(.*)$", re.I)
_CERT_TYPES = {"product_id": "产品合格证", "registration_id": "使用登记", "inspection_id": "检验 / 检测报告", "verification_id": "检定证书", "notice_id": "安装告知", "acceptance_id": "安装验收报告"}
_CERT_FIELDS = {"certificate", "cert_id", "certificate_expiry", "cert_date", "cert_state"}
_PERSON_FIELDS = {"operator", "operator_id", "operator_expiry", "operator_scope", "operator_state"}
_MAINT_FIELDS = {"maintenance", "frequency", "maintenance_date", "maintenance_owner", "maintenance_actual", "failure", "stop", "repair", "lifting"}


@dataclass
class Data:
    contexts: list[dict[str, str]] = field(default_factory=list)
    devices: list[dict[str, str]] = field(default_factory=list)
    certificates: list[dict[str, str]] = field(default_factory=list)
    persons: list[dict[str, str]] = field(default_factory=list)
    maintenance: list[dict[str, str]] = field(default_factory=list)
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
    if key in {"operator", "owner", "lessor", "installer", "maintenance_owner"} and (_UNKNOWN.search(value) or _UNSAFE.search(value)):
        return MISSING
    if key in {"device_id", "factory_id", "filing_id", "cert_id", "operator_id", "model", "capacity", "quantity", "rent", "planned_arrival", "arrival", "planned_departure", "departure", "certificate_expiry", "cert_date", "operator_expiry", "maintenance_date"}:
        if _UNSAFE.search(value) or _UNKNOWN.search(value):
            return MISSING
    if key in {"capacity", "quantity", "rent"}:
        if not re.search(r"(?<![A-Za-z0-9_.#/-])(?:\d+(?:,\d{3})*(?:\.\d+)?|[一二三四五六七八九十百]+)(?![0-9_.#-])", value):
            return MISSING
        if re.search(r"\d{4}[-/.年]\d|\d{1,2}月\d|第\d+版", value):
            return MISSING
    if key in {"planned_arrival", "arrival", "planned_departure", "departure", "cert_date", "maintenance_date"}:
        if not re.search(_DATE, value) or re.search(r"编号|证件|型号|图号|备案", value):
            return MISSING
    return value


def _scan(text):
    text = re.sub(r"^(?:本次实际情况|实际情况|真实记录)\s*[:：]\s*", "", text)
    match = _LABEL.match(text)
    if match:
        key, value = ALIASES[match.group(1).casefold()], match.group(2)
        frequency = re.search(r"每(?:月\d{1,2}号|\d+天(?:一次)?|天|周|月)", value)
        return [(key, value), ("frequency", frequency.group())] if key == "maintenance" and frequency else [(key, value)]
    # Unpunctuated labels are still bounded at the start, so company names
    # containing crane/elevator words cannot open another device record.
    bare = re.match(r"^(厂编号|出厂编号|备案号|机号|设备编号|型号|司机|安装单位|出租单位|合格证|使用登记|作业人员证件|作业证|有效期至|有效期|检定证书)\s*(.+)$", text)
    if not bare:
        bare = re.match(r"^(厂编号|出厂编号|备案号|机号|设备编号|型号)("+_CODE+r")$", text)
    if bare:
        key = ALIASES[bare.group(1).casefold()]
        if key == "operator":
            person = re.match(r"(.{2,8}?)(?=持证|上岗|证件|作业证|\s|$)", bare.group(2))
            if person:
                return [(key, person.group(1)), ("operator_state", bare.group(2)[person.end():])] if person.end() < len(bare.group(2)) else [(key, person.group(1))]
        return [(key, bare.group(2))]
    name = _NAME.search(text)
    facts = []
    if name and not _UNSAFE.search(text[:name.start()]):
        facts.append(("name", name.group()))
        models = list(_MODEL.finditer(text))
        if models:
            facts.append(("model", models[0].group()))
            brand = re.search(r"(中联|徐工|三一)\s*$", text[:models[0].start()])
            if brand:
                facts.append(("manufacturer", brand.group(1)))
        capacity = re.search(r"(?<![A-Za-z0-9_.-])(\d+(?:\.\d+)?\s*(?:吨|t\b))", text, re.I)
        if capacity:
            facts.append(("capacity", capacity.group(1)))
        quantity = re.search(r"([一二三四五六七八九十\d]+台)\s*(?:[\u4e00-\u9fffA-Z0-9/]{0,25})$", text[:name.start()])
        if quantity:
            facts.append(("quantity", quantity.group(1)))
    inline = re.compile(r"(产品合格证|合格证|使用登记|作业人员证件|作业证|厂编号|出厂编号|备案号|机号|设备编号)\s*[:：]?\s*("+_CODE+r")")
    for item in inline.finditer(text):
        facts.append((ALIASES[item.group(1).casefold()], item.group(2)))
    rental = re.search(r"(?:从|由)?\s*([^：:，,；;。]+?)(?:租来的|租的|租来)", text)
    if rental:
        facts += [("lessor", rental.group(1).strip()), ("ownership", "租赁")]
    elif re.search(r"(?:咱|我们|自己|本公司)(?:自己)?的|自有", text):
        facts.append(("ownership", "自有"))
    dates = list(re.finditer(_DATE, text))
    if dates and re.search(r"进场|到场", text):
        facts.append(("planned_arrival" if re.search(r"计划|拟|下周|明天|说", text) else "arrival", dates[-1].group()))
    if dates and "退场" in text:
        facts.append(("planned_departure" if re.search(r"计划|拟|下周|明天", text) else "departure", dates[-1].group()))
    if re.search(r"月保|定期保养|日常点检|维保", text):
        frequency = re.search(r"每(?:月\d{1,2}号|\d+天(?:一次)?|天|周|月)", text)
        facts.append(("maintenance", "月保" if "月保" in text else text))
        if frequency:
            facts.append(("frequency", frequency.group()))
    billing = re.search(r"包月|台班", text)
    if billing and "租赁" in text:
        facts.append(("billing", billing.group()))
    if re.search(r"故障|漏油|异响|损坏|失灵", text):
        facts.append(("failure", text))
    if re.search(r"已停用|已停机|正在停用", text):
        facts.append(("stop", text))
    return facts


def parse(raw):
    data = Data()
    scope = {"jurisdiction": "UNSPECIFIED"}
    data.contexts.append(scope)
    origin = ""
    device = cert = person = maintenance = None
    mode = "device"

    def new(collection, values=None, *, owner=None):
        row = {"_origin": origin, "_scope": len(data.contexts)-1, "_device": owner,
               "project": scope.get("project", MISSING), "jurisdiction": scope.get("jurisdiction", "UNSPECIFIED"), **(values or {})}
        getattr(data, collection).append(row)
        return row

    def device_ref():
        return next((i for i, row in enumerate(data.devices) if row is device), None)

    def put(target, key, value):
        value = _typed(key, value)
        if target.get(key) in {None, MISSING, "UNSPECIFIED"}:
            target[key] = value
        elif value != MISSING and value not in target[key].split("；"):
            target[key] += "；"+value
        if value not in {MISSING, "UNSPECIFIED"}:
            data.evidence.append({"_device": device_ref(), "object": target.get("certificate") or target.get("operator") or target.get("name") or MISSING,
                                  "field": _FIELDS[key][0], "value": value, "location": location})

    for content, is_table, offset, source in _inputs(raw):
        location = (origin+" · " if origin else "")+f"本轮输入第{raw[:offset].count(chr(10))+1}行（非原件页码）"
        if content is None:
            device = cert = person = maintenance = None
            mode = "device"
            continue
        marker = _source_name(source)
        if marker is not None or source.startswith(("【本轮用户要求", "【此前用户资料")):
            origin = marker if marker is not None else source.strip("【】")
            scope = {"_origin": origin, "jurisdiction": "UNSPECIFIED"}
            data.contexts.append(scope)
            device = cert = person = maintenance = None
            mode = "device"
            continue
        if is_table:
            pairs = [(ALIASES[label.casefold()], value) for label, value in content if label.casefold() in ALIASES]
        else:
            text = re.sub(r"^(?:写|出|生成|起草|编制)一份(?:设备台账|维保计划)\s+", "", source)
            pairs = _scan(text)
        if not pairs:
            continue
        stated = dict(pairs)
        if "project" in stated and not is_table and scope.get("project") not in {None, stated["project"]}:
            scope = {"_origin": origin, "jurisdiction": "UNSPECIFIED"}
            data.contexts.append(scope)
            device = cert = person = maintenance = None
            mode = "device"
        if is_table:
            device = new("devices")
            cert = person = maintenance = None
            mode = "device"
            cert_identities = set(_CERT_TYPES) | {"certificate", "cert_id"}
            pairs = ([p for p in pairs if p[0] in cert_identities]+[p for p in pairs if p[0] not in cert_identities])
        elif "name" in stated:
            named = _value(stated["name"])
            identifier = next((stated[k] for k in ("device_id", "factory_id", "filing_id") if k in stated and _typed(k, stated[k]) != MISSING), None)
            existing = [r for r in data.devices if r["_scope"] == len(data.contexts)-1 and r.get("name") == named
                        and identifier is not None and identifier in {r.get("device_id"), r.get("factory_id"), r.get("filing_id")}]
            device = existing[0] if len(existing) == 1 else new("devices")
            cert = person = maintenance = None
            mode = "device"
        elif any(k in stated for k in ("device_id", "factory_id", "filing_id")):
            identifier_key = next(k for k in ("device_id", "factory_id", "filing_id") if k in stated)
            identifier = _typed(identifier_key, stated[identifier_key])
            candidates = [r for r in data.devices if r["_scope"] == len(data.contexts)-1 and r.get(identifier_key) == identifier]
            if identifier != MISSING and device is not None and device.get(identifier_key) == identifier and len(candidates) > 1:
                device = new("devices")
                cert = person = maintenance = None
                mode = "device"
            elif identifier != MISSING and (device is None or device.get(identifier_key) not in {None, MISSING, identifier}):
                existing = [r for r in data.devices if r["_scope"] == len(data.contexts)-1 and r.get(identifier_key) == identifier]
                device = existing[0] if len(existing) == 1 else new("devices")
                cert = person = maintenance = None
                mode = "device"
        elif "model" in stated and _typed("model", stated["model"]) != MISSING and device is not None and device.get("model") not in {None, MISSING, _typed("model", stated["model"])}:
            # A changed, unqualified model is a new identity; no factory ID,
            # rental contract, certificate or operator can follow it silently.
            device = new("devices", {"name": device.get("name", MISSING)})
            cert = person = maintenance = None
            mode = "device"
        if "operator" in stated:
            person = new("persons", owner=device_ref())
        cert_keys = [k for k, _ in pairs if k in _CERT_TYPES]
        if "certificate" in stated or "cert_id" in stated and (cert is None or cert.get("cert_id") not in {None, MISSING, _typed("cert_id", stated["cert_id"])}):
            cert = new("certificates", owner=device_ref())
        for key, value in pairs:
            if key in {"project", "jurisdiction"}:
                target = device if device is not None else scope
                put(target, key, value)
                continue
            if key in _CERT_TYPES:
                cert = new("certificates", {"certificate": _CERT_TYPES[key]}, owner=device_ref())
                mode = "certificate"
                put(cert, "cert_id", value)
                continue
            if key in _CERT_FIELDS:
                if key == "certificate_expiry" and not is_table and mode == "person" and not re.match(r"检定|证件有效", source):
                    put(person, "operator_expiry", value)
                    continue
                if cert is None:
                    cert = new("certificates", owner=device_ref())
                if is_table and len(cert_keys) > 1 and key in {"certificate_expiry", "cert_date", "cert_state"} and cert.get("certificate"):
                    # A generic expiry for several certificates in one row
                    # is unassigned, not an expiry for the last named cert.
                    cert = new("certificates", owner=device_ref())
                mode = "certificate"
                put(cert, key, value)
                continue
            if key in _PERSON_FIELDS:
                if person is None:
                    person = new("persons", owner=device_ref())
                mode = "person"
                put(person, key, value)
                continue
            if key in _MAINT_FIELDS:
                if maintenance is None or key in {"maintenance", "failure", "maintenance_actual", "repair"} and maintenance.get(key):
                    maintenance = new("maintenance", owner=device_ref())
                mode = "maintenance"
                put(maintenance, key, value)
                continue
            if key == "source" and mode == "maintenance" and maintenance is not None:
                put(maintenance, key, value)
                continue
            if key == "source" and (cert is not None or mode == "person" or source.startswith("证件来源")):
                if mode == "person":
                    put(person, key, value)
                    continue
                if cert is None or is_table and len(cert_keys) > 1 and cert.get("certificate"):
                    cert = new("certificates", owner=device_ref())
                put(cert, key, value)
                continue
            if device is None:
                device = new("devices")
            put(device, key, value)
    data.contexts = [r for r in data.contexts if len(r) > 1] or data.contexts[:1]
    return data


def _owned(data, records):
    """Expose literal owner identity even when its ID was stated after a cert."""
    result = []
    for row in records:
        ref = row.get("_device")
        owner = data.devices[ref] if isinstance(ref, int) else {}
        result.append({"device_name": owner.get("name", MISSING), "device_id": owner.get("device_id", MISSING),
                       "factory_id": owner.get("factory_id", MISSING), "filing_id": owner.get("filing_id", MISSING),
                       "model": owner.get("model", MISSING), "owner_jurisdiction": owner.get("jurisdiction", "UNSPECIFIED"), **row})
    return result


def build_draft(post, tool_name, text):
    if post != "equip" or tool_name != TOOL:
        return None
    data = parse(text)
    owner = (("设备名称", "device_name"), ("设备编号 / 机号", "device_id"), ("厂编号", "factory_id"), ("备案号", "filing_id"), ("型号", "model"))
    origin = (("输入资料名", "_origin"), ("设备辖区", "owner_jurisdiction"))
    devices = data.devices
    certificates = _owned(data, data.certificates)
    persons = _owned(data, data.persons)
    maintenance = _owned(data, data.maintenance)
    return "\n".join([
        "# 设备台账 / 维保计划（AI 草稿 · 内部讨论）", "", DISCLAIMER, "", "- submit_blocked=true", "",
        _table(data.contexts, (("项目名称", "project"), ("辖区", "jurisdiction"), ("输入资料名", "_origin"))), "",
        "## 1 封面与范围声明", "", "编制、复核与签认：（空）。缺项保持待填；无证件不编进场结论，不核定设备或人员证件有效性。", "",
        "## 2 设备清单", "", _table(devices, (("设备名称", "name"), ("规格型号", "model"), ("用户品牌 / 厂家", "manufacturer"),
            ("设备编号 / 机号", "device_id"), ("厂编号 / 出厂编号", "factory_id"), ("备案号", "filing_id"), ("用户规格 / 吨位原文", "capacity"),
            ("用户台数原文", "quantity"), ("用途 / 使用部位", "purpose"), ("停放位置", "parking"), ("用户当前状态原文（未核）", "state"),
            ("项目名称", "project"), ("设备辖区", "jurisdiction"), ("输入资料名", "_origin"))), "",
        "型号中的数字不换算吨位、功率或台数；没有状态记录不默认为待进场或在用。", "",
        "## 3 进场验收与日期", "", _table(devices, (("设备名称", "name"), ("设备编号", "device_id"), ("计划进场日（非到场事实）", "planned_arrival"),
            ("用户实际进场日（未核）", "arrival"), ("计划退场日", "planned_departure"), ("用户实际退场日（未核）", "departure"), ("安装单位", "installer"),
            ("拆卸单位", "remover"), ("责任方", "owner")), extra=(("进场验收 / 启用签认", "未执行 /（空）"),)), "",
        "进场验收须核对实际铭牌、安全装置、随机资料及适用流程，本稿不代查验或签发使用登记。", "",
        "## 4 租赁与台班", "", _table(devices, (("设备名称", "name"), ("设备编号", "device_id"), ("自有或租赁 / 产权", "ownership"), ("出租单位", "lessor"),
            ("租赁合同编号", "lease_id"), ("计费方式 / 合同要素", "billing"), ("用户租金原文", "rent")), extra=(("本轮合价", "TBD"), ("台班 / 结算签认", "（空）"))), "",
        "合同安拆、顶升、维保、检测和停机费用分工需核对原件；不编租金、台班、折旧率或结算总额。", "",
        "## 5 维保计划与完成记录", "", _table(maintenance, owner+(("用户维保事项", "maintenance"), ("用户维保周期", "frequency"), ("计划维保日", "maintenance_date"),
            ("维保责任方", "maintenance_owner"), ("顶升 / 附着计划原文", "lifting"), ("用户维保完成记录（未核）", "maintenance_actual"), ("维保记录来源", "source"))+origin,
            extra=(("本轮实际维保 / 签认", "未执行 /（空）"),)), "",
        "写过计划不等于已经保养；不从周期倒算下次到期日，不代排顶升或附着工况。", "",
        "## 6 证件与检验台账", "", _table(certificates, owner+(("证书名称", "certificate"), ("用户证件编号", "cert_id"), ("证件来源", "source"),
            ("用户检定 / 签发日期", "cert_date"), ("用户检定期限 / 有效期原文", "certificate_expiry"), ("用户证件收集状态（未核）", "cert_state"))+origin,
            extra=(("原件与有效性核验", "本轮未核验"),)), "",
        "只抄用户给定证件原文；产品合格证、登记、安装、检验和人员证件分别登记。证件编号不代表收到原件或有效，缺失期限不由签发日推算。", "",
        "## 7 故障停用与人员接口", "", _table(maintenance, owner+(("用户故障记录（未核）", "failure"), ("用户停用记录（未核）", "stop"), ("用户维修记录（未核）", "repair"))+origin,
            extra=(("恢复运行 / 启用许可", "本稿不签发"),)), "",
        _table(persons, owner+(("司机 / 操作人员", "operator"), ("用户作业人员证件编号", "operator_id"), ("人员证件有效期原文", "operator_expiry"),
            ("用户人员作业项目", "operator_scope"), ("用户上岗陈述（未核）", "operator_state"), ("人员证件来源", "source"))+origin, extra=(("证件与设备对应核验", "本轮未核验"),)), "",
        "故障、停用、维修和用户恢复陈述不互相覆盖；不能凭此草稿投入使用。人员姓名与证号不互相代填。", "",
        "## 8 资料与证据位置", "", _table(_owned(data, data.evidence), owner+(("记录对象", "object"), ("记录项", "field"), ("用户证据原文", "value"), ("输入位置（非原件页码）", "location"))), "",
        "各设备进退场、台班、维保、故障、证件及租赁资料交责任人核对归档；现行依据与条款待提供，不从历史模板默写国家、版本或时限。", "",
    ])
