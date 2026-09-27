"""Civil Codex host config. Same knobs as Codex: sandbox + approval.

Not a kernel jail. danger-full-access is intentionally absent: secrets and
generic spawn stay denied in packing_assistant.sandbox.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

_ROOT = Path(__file__).resolve().parents[2]
# The licensed sign-off sentence, the one place it is defined. A person types one of the two, exactly, in the turn it
# approves; every surface (CLI, TUI, desktop, workbench HTTP, gateway, the CAD / planning / logistics pages) accepts
# either and nothing else, and every scrub (history, memory, model output, MCP text) removes both.
CONFIRM = "我明白，将由持证人员签认"
CONFIRM_EN = "I understand; a licensed person will sign this off."
CONFIRM_SENTENCES = (CONFIRM, CONFIRM_EN)


def is_confirmation(value: Any, *, strip: bool = True) -> bool:
    """The whole value is one of the two sentences (surrounding blanks allowed unless ``strip`` is False). A flag, a
    lower-cased or shortened copy, or the sentence quoted inside other words is not."""
    if type(value) is not str:
        return False
    return (value.strip() if strip else value) in CONFIRM_SENTENCES


def contains_confirmation(text: Any) -> bool:
    """Detect a copy for scrubbing/notices; this substring check must never authorize a write."""
    return type(text) is str and any(sentence in text for sentence in CONFIRM_SENTENCES)


_CONFIRM_QUOTED = re.compile(
    r"```[\s\S]*?(?:```|\Z)|`[^`\n]*`|“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|\"[^\"\n]*\""
    r"|(?<!\w)'[^'\n]*'|^\s*>[^\n]*", re.M)
_CONFIRM_REFUSAL = re.compile(
    r"(?i)不同意|不确认|不签认|不授权|拒绝|暂不|先不|撤销(?:确认|签认|授权)|"
    r"(?:不要|不应|不能|尚未|还没|没有|不再)(?:确认|签认|授权|同意)|"
    r"\b(?:refus(?:e[ds]?|ing)|declin(?:e[ds]?|ing)|reject(?:s|ed|ing)?|withdraw(?:s|n|ing)?|revok(?:e[ds]?|ing))\b|"
    r"\b(?:not|never|don[’']t|do\s+not)\s+(?:approv(?:e[ds]?|ing)|confirm(?:s|ed|ing)?|agre(?:e[ds]?|eing)|"
    r"sign(?:s|ed|ing)?|authori[sz](?:e[ds]?|ing)|consent(?:s|ed|ing)?)\b|"
    r"(?:^|[.!?;\n])\s*(?:(?:actually|but)\s+)?no\b|\bor\s+not\s*\?|leave\s+signing\s+for\s+later")
_CONFIRM_REFERENCE = re.compile(
    r"(?i)(?:earlier|previous|historical|quoted|example|sample)\s+(?:confirmation|sign.off|statement|message)|"
    r"(?:确认|签认)(?:示例|样例)|(?:此前|上次|历史|引用)(?:的)?(?:确认|签认|消息)|"
    r"(?:said|wrote|typed)\s+(?:earlier|yesterday|previously)|"
    r"(?:said|says|wrote|writes|typed)\s*:")


def message_confirmation(text: Any) -> bool:
    """Accept an unquoted, standalone affirmative sentence in this current message only.

    The task may precede it (Chinese and English). Quoting, discussing, or refusing the sentence is not consent.
    Dedicated confirmation fields continue to use is_confirmation; stored text uses contains_confirmation only.
    """
    if type(text) is not str:
        return False
    clean = _CONFIRM_QUOTED.sub("", text)
    if _CONFIRM_REFUSAL.search(clean) or _CONFIRM_REFERENCE.search(clean):
        return False
    for sentence in CONFIRM_SENTENCES:
        for match in re.finditer(re.escape(sentence), clean):
            prefix, suffix = clean[:match.start()], clean[match.end():]
            # Do not split the semicolon or comma inside the confirmation itself.
            lead = re.split(r"[。！？.!?;；\n]", prefix)[-1]
            if (lead.strip() or suffix.lstrip().startswith(("?", "？", "吗"))
                    or (suffix.strip() and not re.match(r"^\s*(?:[。.!;；\n]|$)", suffix))):
                continue
            return True
    return False


def count_confirmations(text: str) -> int:
    return sum((text or "").count(sentence) for sentence in CONFIRM_SENTENCES)


def scrub_confirmations(text: str, replacement: str) -> str:
    """Text with both sentences replaced: a stored, quoted or model-written copy approves nothing."""
    out = text or ""
    for sentence in CONFIRM_SENTENCES:
        out = out.replace(sentence, replacement)
    return out


CONFIRM_PATTERN = "|".join(re.escape(sentence) for sentence in CONFIRM_SENTENCES)

SANDBOX_MODES = ("read-only", "workspace-write")
APPROVAL_MODES = ("untrusted", "on-request", "never")
# steps: 规则路由 + 确定性流程，不调模型（默认）。model: 模型驱动的循环（runtime/model_loop.py）。
# auto: 配了模型就用 model，没配或连不上就回到 steps。
AGENT_MODES = ("steps", "model", "auto")
# app: 应用层写根与密钥拒读（默认）。os: 工具在被内核限制的工作进程里跑（runtime/os_sandbox），启用不了就拒绝。
# auto: 本机内核支持且在作业文件夹里就用 os，否则 app，并说明原因。
SANDBOX_BACKENDS = ("app", "os", "auto")


@dataclass
class CivilConfig:
    sandbox: str = "workspace-write"
    approval: str = "on-request"
    max_steps: int = 8
    max_parallel: int = 4
    model: str = ""
    job_root: str = ""
    agent_mode: str = "steps"
    sandbox_backend: str = "app"

    def allow_write(self) -> bool:
        return self.sandbox == "workspace-write"

    def auto_confirm(self) -> bool:
        return self.approval == "never"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["confirm_sentence"] = CONFIRM
        d["confirm_sentence_en"] = CONFIRM_EN
        d["sandbox_modes"] = list(SANDBOX_MODES)
        d["approval_modes"] = list(APPROVAL_MODES)
        d["agent_modes"] = list(AGENT_MODES)
        return d


def _strip_mode(value: str, allowed: tuple[str, ...], default: str) -> str:
    v = (value or "").strip().lower().replace("_", "-")
    aliases = {
        "readonly": "read-only",
        "ro": "read-only",
        "write": "workspace-write",
        "ws": "workspace-write",
        "agent": "workspace-write",
        "full-auto": "never",
        "yolo": "never",
        "trusted": "on-request",
        "ask": "on-request",
        "onrequest": "on-request",
    }
    v = aliases.get(v, v)
    return v if v in allowed else default


# 值整个包在一对引号里时，引号内的 # 属于值（job_root = "C:/工地#2026"）。不认转义：Windows 反斜杠路径照原样读。
_QUOTED = re.compile(r"""\s*(?:"([^"]*)"|'([^']*)')\s*(?:#.*)?""")


def _parse_toml_lite(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    section = ""
    for raw in (text or "").splitlines():
        head, _, tail = raw.partition("=")
        quoted = _QUOTED.fullmatch(tail) if "#" not in head else None
        line = (raw if quoted else raw.split("#", 1)[0]).strip()
        if not line:
            continue
        if not quoted and line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        key = k.strip()
        if section and section not in {"civil", "workspace", ""}:
            key = f"{section}.{key}"
        out[key] = (quoted.group(1) or quoted.group(2) or "") if quoted else v.strip().strip('"').strip("'")
    return out


def _apply_map(cfg: CivilConfig, kv: Dict[str, str]) -> None:
    if "sandbox" in kv:
        cfg.sandbox = _strip_mode(kv["sandbox"], SANDBOX_MODES, cfg.sandbox)
    if "approval" in kv:
        cfg.approval = _strip_mode(kv["approval"], APPROVAL_MODES, cfg.approval)
    if "max_steps" in kv:
        try:
            cfg.max_steps = max(1, min(32, int(kv["max_steps"])))
        except ValueError:
            pass
    if "max_parallel" in kv:
        try:
            cfg.max_parallel = max(1, min(8, int(kv["max_parallel"])))
        except ValueError:
            pass
    if "model" in kv:
        cfg.model = kv["model"]
    if "agent_mode" in kv:
        cfg.agent_mode = _strip_mode(kv["agent_mode"], AGENT_MODES, cfg.agent_mode)
    if "sandbox_backend" in kv:
        cfg.sandbox_backend = _strip_mode(kv["sandbox_backend"], SANDBOX_BACKENDS, cfg.sandbox_backend)
    if kv.get("job_root"):
        cfg.job_root = kv["job_root"]
    if kv.get("workspace.job_root"):
        cfg.job_root = kv["workspace.job_root"]


def config_paths() -> list[Path]:
    cwd = Path.cwd()
    home = Path.home() / ".civil-buddy" / "config.toml"
    return [
        _ROOT / "civil.toml",
        cwd / "civil.toml",
        cwd / ".civil-buddy" / "config.toml",
        home,
    ]


def load_config() -> CivilConfig:
    cfg = CivilConfig()
    for path in config_paths():
        if not path.is_file():
            continue
        try:
            _apply_map(cfg, _parse_toml_lite(path.read_text(encoding="utf-8-sig")))
        except OSError:
            continue
    env_s = os.environ.get("CIVIL_SANDBOX") or ""
    env_a = os.environ.get("CIVIL_APPROVAL") or ""
    if env_s:
        cfg.sandbox = _strip_mode(env_s, SANDBOX_MODES, cfg.sandbox)
    if env_a:
        cfg.approval = _strip_mode(env_a, APPROVAL_MODES, cfg.approval)
    if os.environ.get("CIVIL_JOB_ROOT"):
        cfg.job_root = os.environ["CIVIL_JOB_ROOT"]
    if os.environ.get("CIVIL_AGENT_MODE"):
        cfg.agent_mode = _strip_mode(os.environ["CIVIL_AGENT_MODE"], AGENT_MODES, cfg.agent_mode)
    if os.environ.get("CIVIL_SANDBOX_BACKEND"):
        cfg.sandbox_backend = _strip_mode(os.environ["CIVIL_SANDBOX_BACKEND"], SANDBOX_BACKENDS, cfg.sandbox_backend)
    return cfg


def high_risk_unconfirmed(*, risk: str, confirmed: bool) -> bool:
    """High-risk write needs the confirm sentence. Chat is not a write."""
    return (risk or "low") == "high" and confirmed is not True


def hitl_reply(who: str = "", *, english: bool = False) -> str:
    label = (who or "").strip()
    if english:
        return (f"{label or 'This post'} is a high-risk post: nothing was written. A licensed person types the sign-off "
                f"sentence \"{CONFIRM_EN}\" (or 「{CONFIRM}」) in the turn that writes it.")
    prefix = f"高风险岗 {label} " if label else "高风险岗 "
    return f"{prefix}写盘须确认句「{CONFIRM}」。本轮未写盘。"


def decide_gate(
    *,
    intent: str,
    risk: str,
    confirmed: bool,
    cfg: Optional[CivilConfig] = None,
) -> str:
    """Return go | hitl | read_only."""
    c = cfg or load_config()
    if intent == "chat":
        return "go"
    if not c.allow_write():
        return "read_only"
    if high_risk_unconfirmed(risk=risk, confirmed=confirmed):
        return "hitl"
    if c.auto_confirm() or confirmed:
        return "go"
    if c.approval == "untrusted":
        return "hitl"
    return "go"
