"""Tax drafting inputs only: no embedded rates, legal determinations or network."""
from __future__ import annotations

import re

from packing_assistant.post_drafts.business_observations import (
    UNKNOWN, draft_tax as _draft_tax, tax_records,
)


def records(text: str, previous: str = "") -> list[dict]:
    return tax_records(text, previous=previous)


def explain_tax(text: str, previous: str = "") -> str:
    # Only a deliberate jurisdiction question can override the confirmed chat
    # context. Company names, filenames and invoice names never set draft zones.
    explicit = re.search(r"^(?:请问|关于)?\s*(中国|新加坡|CN|SG|EU)(?:的|\s).*?(?:税率|税种|申报|GST)", text, re.I)
    if explicit:
        previous = {"中国": "CN", "新加坡": "SG"}.get(explicit.group(1), explicit.group(1).upper())
    rows = records(text, previous=previous)
    zones = {row["zone"] for row in rows}
    zone = next(iter(zones)) if len(zones) == 1 else "DUAL"
    lines = [f"辖区：{zone}。GST 等税种的适用范围、税率和申报时限须按具体主体及期间核对。"]
    for row in rows:
        if row["rate"] != UNKNOWN:
            lines.append(f"用户提供税率：{row['rate']}（辖区 {row['zone']}；未核验，不认定为现行适用税率）。")
        else:
            lines.append("税率：UNSPECIFIED。请补明确地区、税种、期间及可定位的资料原文；本机历史知识条目不证明当前税率。")
    lines.append("本次未联网核验、未计算税额，也不构成税务意见。")
    return "\n".join(lines)


def draft_tax(text: str, disclaimer: str) -> str:
    return _draft_tax(text, disclaimer)
