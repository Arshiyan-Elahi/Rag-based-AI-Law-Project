from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

from dotenv import dotenv_values, load_dotenv
from langchain_openai import ChatOpenAI
from openai import OpenAI


_LOGGER = logging.getLogger(__name__)
_LOGGED_ONCE = False
_LOCK = threading.Lock()
_CHAT_LLM_CACHE: dict[tuple[float, Optional[int], int, str, str], ChatOpenAI] = {}
_OPENAI_CLIENT_CACHE: dict[tuple[str, str, str], OpenAI] = {}
_ENV_FILE_PATH = Path(__file__).resolve().parents[2] / ".env"

# Ensure chatbot runtime uses repository .env values even when shell env
# contains stale overrides from earlier sessions.
load_dotenv(_ENV_FILE_PATH, override=True)


@dataclass(frozen=True)
class LocalLLMConfig:
    provider: str
    base_url: str
    model: str
    api_key: str


def get_local_llm_config() -> LocalLLMConfig:
    file_env = dotenv_values(_ENV_FILE_PATH) if _ENV_FILE_PATH.exists() else {}
    provider = str(file_env.get("LLM_PROVIDER") or os.getenv("LLM_PROVIDER", "local_openai")).strip().lower()
    base_url = str(file_env.get("LOCAL_LLM_BASE_URL") or os.getenv("LOCAL_LLM_BASE_URL", "http://192.168.100.15:1234/v1")).strip()
    model = str(file_env.get("LOCAL_LLM_MODEL") or os.getenv("LOCAL_LLM_MODEL", "qwen/qwen2.5-vl-7b:2")).strip()
    api_key = str(file_env.get("LOCAL_LLM_API_KEY") or os.getenv("LOCAL_LLM_API_KEY", "local-key")).strip() or "local-key"
    return LocalLLMConfig(
        provider=provider,
        base_url=base_url,
        model=model,
        api_key=api_key,
    )


def log_active_provider_once() -> None:
    global _LOGGED_ONCE
    if _LOGGED_ONCE:
        return
    cfg = get_local_llm_config()
    msg = f"[llm-provider] provider={cfg.provider} base_url={cfg.base_url} model={cfg.model}"
    print(msg, flush=True)
    _LOGGER.info(msg)
    _LOGGED_ONCE = True


def create_chat_llm(
    *,
    temperature: float = 0.2,
    max_output_tokens: Optional[int] = None,
    max_retries: int = 1,
    use_cache: bool = True,
) -> ChatOpenAI:
    cfg = get_local_llm_config()
    if cfg.provider != "local_openai":
        raise RuntimeError(
            f"Unsupported LLM_PROVIDER='{cfg.provider}'. Expected 'local_openai'."
        )
    log_active_provider_once()
    cache_key = (float(temperature), max_output_tokens, int(max_retries), cfg.base_url, cfg.model)
    if use_cache:
        with _LOCK:
            cached = _CHAT_LLM_CACHE.get(cache_key)
            if cached is not None:
                return cached

    llm = ChatOpenAI(
        base_url=cfg.base_url,
        api_key=cfg.api_key,
        model=cfg.model,
        temperature=temperature,
        max_tokens=max_output_tokens,
        timeout=float(os.getenv("LOCAL_LLM_TIMEOUT_SECONDS", "120")),
        max_retries=max_retries,
    )
    if use_cache:
        with _LOCK:
            _CHAT_LLM_CACHE[cache_key] = llm
    return llm


def create_openai_client(*, use_cache: bool = True) -> OpenAI:
    cfg = get_local_llm_config()
    if cfg.provider != "local_openai":
        raise RuntimeError(
            f"Unsupported LLM_PROVIDER='{cfg.provider}'. Expected 'local_openai'."
        )
    log_active_provider_once()
    cache_key = (cfg.provider, cfg.base_url, cfg.model)
    if use_cache:
        with _LOCK:
            cached = _OPENAI_CLIENT_CACHE.get(cache_key)
            if cached is not None:
                return cached
    client = OpenAI(
        base_url=cfg.base_url,
        api_key=cfg.api_key,
    )
    if use_cache:
        with _LOCK:
            _OPENAI_CLIENT_CACHE[cache_key] = client
    return client


def is_local_llm_unreachable_error(exc: Exception) -> bool:
    msg = str(exc or "").lower()
    markers = (
        "connection refused",
        "max retries exceeded",
        "timed out",
        "timeout",
        "failed to establish a new connection",
        "connecterror",
        "apiconnectionerror",
        "service unavailable",
        "503",
        "502",
        "504",
    )
    return any(m in msg for m in markers)

