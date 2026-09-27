#!/usr/bin/env python3
"""Bring-your-own API key: OpenAI-compat works without DEEPSEEK_API_KEY."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packing_assistant.llm import llm_config  # noqa: E402


def _with_env(values: Dict[str, str]):
    keys = (
        "CIVIL_API_KEY",
        "CIVIL_API_BASE",
        "CIVIL_MODEL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_BASE_URL",
        "DEEPSEEK_MODEL",
        "OPENAI_MODEL",
    )
    old = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    os.environ.update(values)
    try:
        return llm_config()
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def main() -> int:
    openai = _with_env(
        {
            "OPENAI_API_KEY": "sk-test",
            "LLM_MODEL": "gpt-4o-mini",
        }
    )
    assert openai["api_key"] == "sk-test"
    assert "openai.com" in openai["base_url"], openai
    assert openai["model"] == "gpt-4o-mini"

    ds = _with_env({"DEEPSEEK_API_KEY": "sk-ds"})
    assert ds["api_key"] == "sk-ds"
    assert "deepseek" in ds["base_url"], ds
    assert ds["model"] == "deepseek-flash"

    civil = _with_env(
        {
            "CIVIL_API_KEY": "sk-civil",
            "DEEPSEEK_API_KEY": "sk-ds",
            "CIVIL_API_BASE": "https://api.moonshot.cn/v1",
            "CIVIL_MODEL": "moonshot-v1-8k",
        }
    )
    assert civil["api_key"] == "sk-civil"
    assert "moonshot" in civil["base_url"], civil
    assert civil["model"] == "moonshot-v1-8k"

    print("PASS llm_byok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
