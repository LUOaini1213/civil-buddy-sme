"""Chat Completions with tool calling, for the model-driven turn (runtime/model_loop.py).

One function, ``complete(messages, tools)``, returning the assistant message as a dict with
``content`` and a normalised ``tool_calls`` list. Any OpenAI-compatible endpoint works — the
same ``CIVIL_API_BASE`` / ``CIVIL_API_KEY`` / ``CIVIL_MODEL`` the workbench uses, so DeepSeek,
OpenRouter, Bedrock's compatible endpoint, or a local Ollama (``http://127.0.0.1:11434/v1``,
any non-empty key) all plug in. Nothing is persisted and the key is never logged.

Small local models sometimes write a tool call into the text instead of the ``tool_calls``
field (``<tool_call>{…}</tool_call>`` or a bare JSON object); that form is accepted too.

A 429, a 5xx, a connect/read timeout or a dropped connection is retried at most twice with jittered backoff
inside the same CIVIL_MODEL_TIMEOUT budget (packing_assistant/model_retry.py). Only this request is repeated:
it has no side effects, and the tools a turn runs are run by the caller after it returns.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional
from uuid import uuid4


class ModelError(RuntimeError):
    """The model endpoint could not be used; the message is safe to show to the user."""


class ModelCancelled(ModelError):
    """Cancellation is not endpoint unavailability; auto must not retry in steps."""


_TAGGED = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


def _timeout() -> float:
    try:
        return max(5.0, float(os.getenv("CIVIL_MODEL_TIMEOUT") or 180))
    except ValueError:
        return 180.0


def _max_tokens() -> int:
    """A reply is a conclusion, file locations and open items — not an essay. CIVIL_MODEL_MAX_TOKENS changes it."""
    try:
        return max(64, int(os.getenv("CIVIL_MODEL_MAX_TOKENS") or 1500))
    except ValueError:
        return 1500


def _calls_from_text(content: str, names: set) -> List[Dict[str, Any]]:
    candidates = _TAGGED.findall(content or "")
    stripped = (content or "").strip()
    if not candidates and stripped.startswith("{") and stripped.endswith("}"):
        candidates = [stripped]
    calls = []
    for raw in candidates:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        name = data.get("name") if isinstance(data, dict) else None
        arguments = data.get("arguments", data.get("parameters", {})) if isinstance(data, dict) else {}
        if name in names and isinstance(arguments, dict):
            calls.append({"id": "call_" + uuid4().hex[:8], "name": name, "arguments": arguments})
    return calls


def normalise(message: Dict[str, Any], tool_names: Optional[set] = None) -> Dict[str, Any]:
    """``{"content": str, "tool_calls": [{"id", "name", "arguments": dict}]}`` from any provider shape."""
    content = message.get("content")
    if isinstance(content, list):   # some gateways return content parts
        content = "".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    content = str(content or "")
    calls: List[Dict[str, Any]] = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError:
                arguments = {"_unparsed": arguments[:500]}
        calls.append({"id": str(call.get("id") or "call_" + uuid4().hex[:8]),
                      "name": str(function.get("name") or call.get("name") or ""),
                      "arguments": arguments if isinstance(arguments, dict) else {}})
    if not calls and tool_names:
        calls = _calls_from_text(content, tool_names)
        if calls:
            content = _TAGGED.sub("", content).strip()
            if content.startswith("{") and content.endswith("}"):
                content = ""
    return {"content": content, "tool_calls": calls}


def complete(messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None, *,
             temperature: float = 0.2, max_tokens: Optional[int] = None, cancel_event: Any = None) -> Dict[str, Any]:
    import httpx

    from packing_assistant.llm import llm_config
    from demo.llm import ModelConnection
    from demo.turn_control import interrupt_event

    def check_cancelled() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise ModelCancelled("本轮已取消。")

    check_cancelled()
    config = llm_config()
    if not config.get("api_key"):
        raise ModelError("未配置模型 Key（CIVIL_API_KEY / OPENAI_API_KEY / DEEPSEEK_API_KEY）。本机 Ollama 可填任意非空值。")
    payload: Dict[str, Any] = {"model": config["model"], "messages": messages, "temperature": temperature, "stream": False,
                               "max_tokens": int(max_tokens or _max_tokens())}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    from packing_assistant import model_retry

    secrets = (config["api_key"],)

    def attempt(remaining: float) -> Any:
        try:
            # Reuse the workbench's transport tracker: a request awaiting response
            # headers has no response socket yet, so track streams as httpcore opens
            # them. interrupt_event shuts down that socket rather than waiting for
            # the full model timeout. It needs no UI ContextVar or active browser.
            with ModelConnection(timeout=min(_timeout(), remaining)) as connection, interrupt_event(connection, cancel_event):
                with connection.stream("POST", config["base_url"].rstrip("/") + "/chat/completions", json=payload,
                                       headers={"Authorization": "Bearer " + config["api_key"], "Content-Type": "application/json"}) as response:
                    with interrupt_event(response, cancel_event):
                        status = response.status_code
                        if status >= 400:
                            # The provider's body may echo the key or the submitted documents, and this message
                            # becomes the turn's reply: the redacted excerpt goes to the civil.model_retry log only
                            # (same rule as demo/llm.py, pinned by test_workbench_settings).
                            excerpt = model_retry.safe_excerpt(model_retry.read_body(response), secrets)
                            if model_retry.retryable_status(status):
                                hint = "请求受限（限流或额度）" if status == 429 else "模型服务暂不可用"
                                raise model_retry.Transient(
                                    lambda attempts: ModelError(
                                        f"模型接口返回 {status}：{hint}，已尝试 {attempts} 次，请稍后重试。"),
                                    reason=f"HTTP {status}", excerpt=excerpt,
                                    retry_after=model_retry.parse_retry_after(response.headers.get("retry-after")))
                            if excerpt:
                                model_retry.logger.warning("model_client.complete: HTTP %d, not retried - upstream "
                                                           "said: %s", status, excerpt)
                            raise ModelError(f"模型接口返回 {status}，请检查模型名、Key 与额度。")
                        response.read()
                        check_cancelled()
                        try:
                            return response.json()["choices"][0]["message"]
                        except (ValueError, KeyError, IndexError, TypeError):
                            raise ModelError("模型返回了无法解析的回复，请检查接口兼容性。") from None
        except httpx.TimeoutException as exc:
            check_cancelled()
            final = ModelError("模型响应超时，请稍后重试（CIVIL_MODEL_TIMEOUT 可调）。")
            if isinstance(exc, (httpx.ConnectTimeout, httpx.ReadTimeout)):
                raise model_retry.Transient(final, reason=type(exc).__name__) from None
            raise final from None
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            check_cancelled()
            final = ModelError("无法连接模型接口，请检查 Base URL 和网络。")
            if isinstance(exc, (httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError)):
                raise model_retry.Transient(final, reason=type(exc).__name__) from None   # reset / dropped
            raise final from None
        except InterruptedError:
            check_cancelled()
            raise

    def wait(seconds: float) -> None:
        check_cancelled()
        if cancel_event is not None:
            cancel_event.wait(seconds)
        else:
            time.sleep(seconds)
        check_cancelled()

    message = model_retry.run(attempt, budget_s=_timeout(), label="model_client.complete", wait=wait)
    check_cancelled()
    names = {t["function"]["name"] for t in tools or []}
    return normalise(message if isinstance(message, dict) else {}, names)
