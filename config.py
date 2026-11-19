"""Runtime configuration. Secrets come from the environment / .env only."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
KNOWLEDGE_PATH = ROOT / "knowledge.md"
HANDOFF_LOG = ROOT / "handoffs.log"
MEMORY_DB = DATA_DIR / "memory.sqlite"
WEBHOOK_LOG = LOG_DIR / "webhooks.jsonl"
COEXISTENCE_LOG = LOG_DIR / "coexistence.jsonl"
OUTBOUND_LOG = LOG_DIR / "outbound.jsonl"

load_dotenv(ROOT / ".env")

DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


GUPSHUP_API_KEY = _env("GUPSHUP_API_KEY")
GUPSHUP_APP_NAME = _env("GUPSHUP_APP_NAME", "JuanCamiloTestBot")
GUPSHUP_APP_ID = _env("GUPSHUP_APP_ID", "009b780e-e61b-4396-9cb2-d3faf84297eb")
GUPSHUP_SOURCE = _env("GUPSHUP_SOURCE", "34637274292").lstrip("+")
WABA_ID = _env("WABA_ID", "3666860806796820")
PHONE_NUMBER_ID = _env("PHONE_NUMBER_ID", "1378713898668233")
WEBHOOK_VERIFY_TOKEN = _env("WEBHOOK_VERIFY_TOKEN", "jc-digital-geneve")
# Optional shared secret for POST /webhook?token=... (empty = no check).
WEBHOOK_TOKEN = _env("WEBHOOK_TOKEN")
BUSINESS_NAME = "JC Digital Geneve"

HOST = _env("HOST", "0.0.0.0")
PORT = int(_env("PORT", "8080"))

# DRY_RUN defaults to true until a key is present and someone turns it off.
DRY_RUN = _bool("DRY_RUN", default=True) or not bool(GUPSHUP_API_KEY)

LLM_PROVIDER = _env("LLM_PROVIDER").lower()
LLM_API_KEY = _env("LLM_API_KEY")
LLM_MODEL = _env("LLM_MODEL")
LLM_BASE_URL = _env("LLM_BASE_URL")

# Provider auto-detect: only presence is used; values never logged.
PROVIDER_KEY_ENVS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "xai": "XAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "google": "GOOGLE_API_KEY",
    "together": "TOGETHER_API_KEY",
    "mistral": "MISTRAL_API_KEY",
}

MEMORY_LIMIT = 10
HANDOFF_MUTE_HOURS = 24

# Own business numbers: never auto-reply to these senders (echo / self).
OWN_NUMBERS = {
    GUPSHUP_SOURCE,
    GUPSHUP_SOURCE.lstrip("0"),
    "34637274292",
    "634274292",
    "637274292",
}


def load_knowledge() -> str:
    if KNOWLEDGE_PATH.exists():
        return KNOWLEDGE_PATH.read_text(encoding="utf-8")
    return ""
