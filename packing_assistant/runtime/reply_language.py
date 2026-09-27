"""Which language a reply to this request is written in: English for an English request, Chinese otherwise.

A request is English when, once file names, quoted text and links are set aside, it has no Chinese character and
at least two Latin words. A mixed request ("按 facade_panels.xlsx 装柜, 40HQ") stays Chinese, as before. Only the
notes this system writes itself follow it (the link reply, the question note, approval and guard notices); the
Chinese UI and the posts' own templates are not translated.
"""
from __future__ import annotations

import re

_SET_ASIDE = re.compile(r"```[\s\S]*?```|`[^`\n]*`|“[^”]*”|「[^」]*」|『[^』]*』|\"[^\"\n]*\"|https?://\S+"
                        r"|[\w.\-㐀-鿿]+\.(?:xlsx|xlsm|xls|csv|md|docx|doc|pdf|txt|json|dxf|dwg)\b", re.I)
_CJK = re.compile(r"[　-〿㐀-䶿一-鿿＀-￯]")
_WORD = re.compile(r"[A-Za-z]{2,}")


def english_request(text: object) -> bool:
    if not isinstance(text, str) or not text.strip():
        return False
    rest = _SET_ASIDE.sub(" ", text)
    return not _CJK.search(rest) and len(_WORD.findall(rest)) >= 2
