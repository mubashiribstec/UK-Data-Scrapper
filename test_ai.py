#!/usr/bin/env python3
"""Quick AI connectivity test — checks that a configured AI provider actually works.

Run this to verify your AI setup (Ollama / Gemini API / Anthropic) before a full
scrape. It shows the provider chain, checks each provider's reachability, then
sends a real test prompt and prints the response and which provider answered.

Usage:
    python test_ai.py                       # test the whole chain
    python test_ai.py --provider ollama     # force one provider
    python test_ai.py --prompt "say hello"  # custom prompt
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import Config
from utils.ai_client import (
    _build_chain,
    _list_ollama_models,
    _resolve_ollama_model,
    ask_ai,
)

GREEN, RED, YELLOW, DIM, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[2m", "\033[0m"


def ok(msg):
    print(f"  {GREEN}✓{RESET} {msg}")


def fail(msg):
    print(f"  {RED}✗{RESET} {msg}")


def warn(msg):
    print(f"  {YELLOW}!{RESET} {msg}")


def check_ollama(config):
    base = getattr(config, "ollama_base_url", "")
    if not base:
        return
    print(f"\nOllama  ({base})")
    requested = getattr(config, "ai_model", "llama3.2")
    available = _list_ollama_models(base, timeout=5)
    if available is None:
        fail(f"could not reach the Ollama server at {base}")
        warn("is Ollama running? try:  curl " + base.rstrip('/') + "/api/tags")
        return
    ok(f"server reachable — {len(available)} model(s) pulled: {available}")
    resolved = _resolve_ollama_model(requested, available)
    if resolved == requested:
        ok(f"requested model '{requested}' is pulled")
    elif resolved:
        warn(f"requested '{requested}' not found exactly — will auto-use '{resolved}'")
    else:
        fail(f"requested model '{requested}' not found and nothing matches")
        warn(f"pull it with:  ollama pull {requested}")


def check_gemini(config):
    if not getattr(config, "gemini_api_key", ""):
        return
    print(f"\nGemini API")
    ok(f"API key set (model: {getattr(config, 'gemini_model', '?')})")


def check_anthropic():
    import os
    if not os.getenv("ANTHROPIC_API_KEY"):
        return
    print("\nAnthropic API")
    ok("ANTHROPIC_API_KEY set")


def main():
    parser = argparse.ArgumentParser(description="Test that an AI provider is working")
    parser.add_argument("--provider", choices=["gemini", "ollama", "anthropic"],
                        help="Force a single provider instead of the whole chain")
    parser.add_argument("--prompt", default="Reply with exactly the word: PONG",
                        help="Prompt to send (default: a simple PONG check)")
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()

    config = Config()
    if args.provider:
        config.ai_provider = args.provider

    print("=" * 60)
    print("AI PROVIDER TEST")
    print("=" * 60)

    chain = _build_chain(config)
    if not chain:
        fail("No AI provider configured.")
        print(f"\n{DIM}Set one of these in your .env:{RESET}")
        print("  GEMINI_API_KEY=...        (free tier)")
        print("  OLLAMA_BASE_URL=http://localhost:11434  +  AI_MODEL=llama3.2:3b")
        print("  ANTHROPIC_API_KEY=...     (paid)")
        sys.exit(1)

    print(f"\nProvider chain (in order): {' → '.join(chain)}")

    # Per-provider reachability checks
    check_ollama(config)
    check_gemini(config)
    check_anthropic()

    # Live round-trip
    print(f"\n{'-' * 60}")
    print(f"Sending test prompt: {args.prompt!r}")
    start = time.time()
    response, provider = ask_ai(args.prompt, config, timeout=args.timeout)
    elapsed = time.time() - start

    print("-" * 60)
    if response:
        ok(f"{GREEN}AI is working{RESET} — answered by '{provider}' in {elapsed:.1f}s")
        print(f"\n{DIM}Response:{RESET}\n{response.strip()}")
        sys.exit(0)
    else:
        fail("No provider returned a response — AI is NOT working")
        print(f"\n{DIM}Check the log lines above for the specific error "
              f"(quota, unreachable server, missing model, etc.){RESET}")
        sys.exit(1)


if __name__ == "__main__":
    main()
