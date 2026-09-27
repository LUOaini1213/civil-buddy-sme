from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

DEMO_ROOT = Path(__file__).resolve().parent
REPO_ROOT = DEMO_ROOT.parent
KB_ROOT = DEMO_ROOT / "kb"
OUT_ROOT = DEMO_ROOT / "out"
DATA_ROOT = Path(os.environ.get("CIVIL_DATA_ROOT", str(DEMO_ROOT / "data"))).expanduser().resolve()
SKILL_HARD_RULES = REPO_ROOT / "skills" / "civil-buddy" / "references" / "hard-rules.md"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# repo-root .env first (shared DeepSeek/OpenAI keys), then demo/.env wins
# Honor the offline check switch with older python-dotenv releases as well.
if os.getenv("PYTHON_DOTENV_DISABLED") != "1":
    load_dotenv(REPO_ROOT / ".env")
    load_dotenv()
    load_dotenv(DEMO_ROOT / ".env", override=True)

# One workbench = one session root. CIVIL_OUT_ROOT lets a test run or a second instance keep
# its sessions (transcripts, runs, deliverables, uploads, events) away from demo/out.
if os.environ.get("CIVIL_OUT_ROOT"):
    OUT_ROOT = Path(os.environ["CIVIL_OUT_ROOT"]).expanduser().resolve()

from packing_assistant.llm import llm_config as _llm_config  # noqa: E402


def llm_api_key() -> str:
    return _llm_config().get("api_key") or ""


def llm_base_url() -> str:
    return (_llm_config().get("base_url") or "").rstrip("/")


def llm_model() -> str:
    return _llm_config().get("model") or ""


def __getattr__(name: str):
    if name == "DEEPSEEK_API_KEY":
        return llm_api_key()
    if name == "DEEPSEEK_BASE_URL":
        return llm_base_url()
    if name == "DEEPSEEK_MODEL":
        return llm_model()
    raise AttributeError(name)


MAX_AGENT_STEPS = int(os.environ.get("CIVIL_MAX_AGENT_STEPS", "8"))
