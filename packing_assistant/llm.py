"""统一 LLM 客户端（OpenAI 兼容 Chat Completions；DeepSeek 可选）。"""

from __future__ import annotations

import json
import os
import re
from threading import RLock
from typing import Any, Dict, List, Optional

_RUNTIME_LOCK = RLock()
_RUNTIME_LLM: Optional[Dict[str, str]] = None


def runtime_llm() -> Optional[Dict[str, str]]:
    """A snapshot of the in-memory override; never persisted to session state."""
    with _RUNTIME_LOCK:
        return dict(_RUNTIME_LLM) if _RUNTIME_LLM is not None else None


def set_runtime_llm(config: Optional[Dict[str, str]]) -> None:
    """Replace the process override, or clear it to resume environment defaults."""
    global _RUNTIME_LLM
    if config is not None:
        if any(not isinstance(config.get(name), str) for name in ("api_key", "base_url", "model")):
            raise ValueError("模型配置字段必须为文本")
        config = {name: config[name] for name in ("api_key", "base_url", "model")}
    with _RUNTIME_LOCK:
        _RUNTIME_LLM = config


def _first(*names: str) -> str:
    for name in names:
        val = (os.getenv(name) or "").strip()
        if val:
            return val
    return ""


def llm_config() -> Dict[str, str]:
    """
    OpenAI 兼容 Chat Completions。试用者自带 Key，不必 DeepSeek。

    优先级：
    0) 工作台本次进程内的模型设置（不写盘）
    1) CIVIL_API_KEY + CIVIL_API_BASE + CIVIL_MODEL
    2) OPENAI_API_KEY / LLM_API_KEY + OPENAI_BASE_URL
    3) DEEPSEEK_API_KEY + 官方 base（仍可用）
    """
    override = runtime_llm()
    if override is not None:
        return override
    api_key = _first("CIVIL_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY", "DEEPSEEK_API_KEY")
    explicit_base = _first(
        "CIVIL_API_BASE", "OPENAI_BASE_URL", "LLM_BASE_URL", "DEEPSEEK_BASE_URL"
    )
    generic = bool(_first("CIVIL_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY"))
    if explicit_base:
        base_url = explicit_base
    elif generic:
        base_url = "https://api.openai.com/v1"
    else:
        base_url = "https://api.deepseek.com"
    model = _first("CIVIL_MODEL", "LLM_MODEL", "DEEPSEEK_MODEL", "OPENAI_MODEL")
    if not model:
        model = (
            "deepseek-flash"
            if "deepseek" in base_url.lower()
            else "gpt-4o-mini"
        )
    return {"api_key": api_key, "base_url": base_url.rstrip("/"), "model": model}


def llm_available() -> bool:
    return bool(llm_config().get("api_key"))


def _refused(exc: BaseException) -> bool:
    """True when an httpx.ConnectError (refused, DNS, TLS) is anywhere in the cause chain. langchain-openai 1.x
    re-raises the SDK's APIConnectionError as its own OpenAIConnectionError, so the httpx error is two causes
    down, not one."""
    seen = 0
    while exc is not None and seen < 8:
        if type(exc).__name__ == "ConnectError":
            return True
        exc, seen = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__), seen + 1
    return False


def chat(
    system: str,
    user: str,
    *,
    temperature: float = 0.2,
    max_tokens: int = 2000,
) -> Optional[str]:
    """调用 Chat Completions；失败返回 None。

    429 / 5xx / 超时 / 连接被重置时最多重试 2 次（packing_assistant/model_retry.py），全部尝试和等待都在
    LLM_TIMEOUT 之内；SDK 自带的重试保持关闭（max_retries=0），因为它也会重试 408/409 这类 4xx。"""
    cfg = llm_config()
    if not cfg["api_key"]:
        return None
    from packing_assistant import model_retry

    try:
        import openai
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import HumanMessage, SystemMessage

        # 短超时：避免 UI/pipeline 被远端 API 挂死（默认 8s，可用 LLM_TIMEOUT 覆盖）
        _to = float(os.getenv("LLM_TIMEOUT") or 8)

        def attempt(remaining: float) -> Any:
            llm = ChatOpenAI(
                model=cfg["model"],
                api_key=cfg["api_key"],
                base_url=cfg["base_url"],
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=min(_to, remaining),
                max_retries=0,
            )
            try:
                return llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
            except openai.APIStatusError as exc:
                if not model_retry.retryable_status(exc.status_code):
                    raise
                raise model_retry.Transient(
                    exc, reason=f"HTTP {exc.status_code}",
                    retry_after=model_retry.parse_retry_after(exc.response.headers.get("retry-after")),
                    excerpt=model_retry.safe_excerpt(exc.body if exc.body is not None else "", (cfg["api_key"],)),
                ) from None
            except openai.APIConnectionError as exc:   # includes APITimeoutError
                if _refused(exc):
                    raise                               # refused: a dead endpoint, not a blip
                raise model_retry.Transient(exc, reason=type(exc).__name__) from None

        resp = model_retry.run(attempt, budget_s=_to, label="llm.chat(langchain)")
        text = str(resp.content or "").strip()
        return text or None
    except Exception as e:
        return f"[LLM_ERROR] {type(e).__name__}: {model_retry.safe_excerpt(e, (cfg['api_key'],))}"


def chat_json_array(system: str, user: str) -> Optional[List[Dict[str, Any]]]:
    text = chat(system, user, temperature=0.1, max_tokens=3000)
    if not text or text.startswith("[LLM_ERROR]"):
        return None
    return _extract_json_array(text)


def _extract_json_array(text: str) -> Optional[List[Dict[str, Any]]]:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "materials" in data:
            return data["materials"]
    except json.JSONDecodeError:
        pass
    start, end = text.find("["), text.rfind("]")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            return None
    return None
