"""Shared AI client with provider-chain failover: Gemini → Ollama → Anthropic.

All AI calls in the project (contact enrichment, description parsing) go
through ask_ai(), which also maintains the global, thread-safe call counter
used for run statistics and budgets.
"""

import json
import os
import re
import logging
import threading
from typing import Optional

import requests

from utils.retry import retry

logger = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_lock = threading.Lock()
_ai_call_counter = 0
_provider_failures: dict[str, int] = {}
_dead_providers: set[str] = set()

# A provider is skipped for the rest of the run after this many consecutive
# hard failures, so 50 jobs don't each wait out the same quota error.
_MAX_CONSECUTIVE_FAILURES = 2


@retry(max_attempts=2, base_delay=1.5, max_delay=5.0)
def _call_gemini(prompt: str, model: str, api_key: str, timeout: int,
                  use_search: bool = False) -> Optional[str]:
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 512,
            "temperature": 0,
            # Newer Gemini models ("thinking" models, e.g. behind the
            # gemini-flash-latest alias) spend part of maxOutputTokens on
            # internal reasoning before the visible answer. Disable it so
            # the full budget goes to the JSON response we asked for.
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    if use_search:
        # Search-grounding: Gemini runs a live Google search before answering,
        # instead of relying only on its training data.
        payload["tools"] = [{"google_search": {}}]

    resp = requests.post(
        GEMINI_URL.format(model=model),
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    if resp.status_code == 429:
        raise RuntimeError("Gemini quota exhausted (HTTP 429)")
    if resp.status_code == 403:
        raise RuntimeError("Gemini API key invalid or unauthorised (HTTP 403)")
    resp.raise_for_status()
    data = resp.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Gemini returned no candidates (prompt blocked? {data.get('promptFeedback')})")
    parts = candidates[0].get("content", {}).get("parts") or []
    if not parts:
        raise RuntimeError(f"Gemini returned empty content (finishReason={candidates[0].get('finishReason')})")
    return parts[0].get("text")


def _list_ollama_models(base_url: str, timeout: int = 5) -> Optional[list]:
    """Best-effort: list model names available on an Ollama server, for
    diagnostics when a requested model returns 404. Returns None on error."""
    try:
        resp = requests.get(f"{base_url}/api/tags", timeout=timeout)
        resp.raise_for_status()
        models = resp.json().get("models", [])
        return [m.get("name", "?") for m in models]
    except Exception:
        return None


@retry(max_attempts=2, base_delay=1.5, max_delay=5.0)
def _call_ollama(prompt: str, model: str, base_url: str, timeout: int) -> Optional[str]:
    base = base_url.rstrip("/")
    resp = requests.post(
        f"{base}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=timeout,
    )
    if resp.status_code == 404:
        # Almost always "model not pulled on this server". A RuntimeError
        # (not a RequestException) skips the @retry — a missing model won't
        # appear on retry — and names the models that ARE available.
        available = _list_ollama_models(base, timeout=5)
        if available is not None:
            raise RuntimeError(
                f"Ollama: model '{model}' not found on {base}. "
                f"Available models: {available}. "
                f"Set AI_MODEL env var to one of these."
            )
        raise RuntimeError(f"Ollama: model '{model}' not found on {base} (HTTP 404)")
    resp.raise_for_status()
    return resp.json().get("response", "")


def _call_anthropic(prompt: str, model: str, timeout: int) -> Optional[str]:
    import anthropic
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=model,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text if message.content else None


def _build_chain(config) -> list[str]:
    """Provider order: forced provider first if set, then the rest by default priority.

    API-only chain: Gemini → Ollama → Anthropic. No browser automation.
    """
    chain = []
    if getattr(config, "gemini_api_key", ""):
        chain.append("gemini")
    if getattr(config, "ollama_base_url", ""):
        chain.append("ollama")
    if os.getenv("ANTHROPIC_API_KEY"):
        chain.append("anthropic")

    forced = getattr(config, "ai_provider", "") or ""
    if forced:
        if forced in chain:
            chain.remove(forced)
        chain.insert(0, forced)
    return chain


def ask_ai(prompt: str, config, timeout: int = 60,
           use_search: bool = False) -> tuple[Optional[str], Optional[str]]:
    """Send a prompt down the provider chain, returning (response, provider_name).

    Counts one AI call per invocation regardless of how many providers were
    attempted. Returns (None, None) when every provider fails. The provider
    name (e.g. "gemini", "ollama") lets callers tag which fields were filled
    in by which AI service, instead of a generic "ai" label.

    use_search enables Gemini's live Google-search grounding for this call
    (ignored by providers that don't support it).
    """
    global _ai_call_counter
    with _lock:
        _ai_call_counter += 1
        call_no = _ai_call_counter

    chain = _build_chain(config)
    if not chain:
        logger.warning("AI: no provider configured (set GEMINI_API_KEY, OLLAMA_BASE_URL or ANTHROPIC_API_KEY)")
        return None, None

    for provider in chain:
        with _lock:
            if provider in _dead_providers:
                continue
        try:
            if provider == "gemini":
                result = _call_gemini(prompt, config.gemini_model, config.gemini_api_key,
                                      timeout, use_search=use_search)
            elif provider == "ollama":
                result = _call_ollama(prompt, getattr(config, "ai_model", "llama3.2"),
                                      config.ollama_base_url, timeout)
            elif provider == "anthropic":
                result = _call_anthropic(prompt, getattr(config, "anthropic_model",
                                                         "claude-haiku-4-5-20251001"), timeout)
            else:
                logger.warning(f"AI: unknown provider '{provider}', skipping")
                continue

            if result:
                with _lock:
                    _provider_failures[provider] = 0
                logger.debug(f"AI call #{call_no}: {provider} responded")
                return result, provider
            raise RuntimeError("empty response")

        except Exception as e:
            with _lock:
                _provider_failures[provider] = _provider_failures.get(provider, 0) + 1
                failures = _provider_failures[provider]
                if failures >= _MAX_CONSECUTIVE_FAILURES:
                    _dead_providers.add(provider)
            logger.warning(
                f"AI: {provider} failed ({e})"
                + (f" — disabled for the rest of this run" if failures >= _MAX_CONSECUTIVE_FAILURES else "")
                + (", failing over" if provider != chain[-1] else "")
            )

    logger.error("AI: all providers in the chain failed")
    return None, None


def parse_ai_json(text: str) -> Optional[dict]:
    """Strip markdown fences and parse a JSON object from an AI response."""
    if not text:
        return None
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except Exception:
                pass
    return None


def reset_counter():
    global _ai_call_counter
    with _lock:
        _ai_call_counter = 0
        _provider_failures.clear()
        _dead_providers.clear()


def get_call_count() -> int:
    with _lock:
        return _ai_call_counter
