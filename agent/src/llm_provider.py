"""LLM provider factory.

The agent's brain is swappable by env var so the same deployment can run on
Claude or Gemini without a code change. That matters for two reasons: the
provider's free tier decides whether this demo costs anything to run, and the
on-prem story in the README depends on providers sitting behind an interface
rather than being hardwired.

    LLM_PROVIDER=anthropic   LLM_MODEL=claude-opus-5
    LLM_PROVIDER=google      LLM_MODEL=gemini-2.5-flash

Voice answers must be short and fast, so both branches cap output tokens and
suppress deep reasoning where the plugin allows it.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("agent.llm")

# Voice replies are two or three sentences. A hard cap is cheaper than trusting
# the prompt alone, and it bounds worst-case latency.
MAX_OUTPUT_TOKENS = 400

DEFAULT_MODELS = {
    "anthropic": "claude-opus-5",
    "google": "gemini-2.5-flash",
}


def provider_name() -> str:
    name = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()
    if name not in DEFAULT_MODELS:
        raise ValueError(
            f"unknown LLM_PROVIDER {name!r}; expected one of {sorted(DEFAULT_MODELS)}"
        )
    return name


def model_name(provider: str | None = None) -> str:
    provider = provider or provider_name()
    return os.getenv("LLM_MODEL") or DEFAULT_MODELS[provider]


def build_llm():
    """Return a LiveKit LLM instance for the configured provider."""
    provider = provider_name()
    model = model_name(provider)

    if provider == "anthropic":
        import anthropic as anthropic_sdk
        from livekit.plugins import anthropic

        logger.info("LLM: anthropic %s (prompt caching on)", model)
        return anthropic.LLM(
            model=model,
            # Supply the client ourselves. livekit-plugins-anthropic 1.7.0 builds
            # its default client with `http_client=httpx.AsyncClient(...)`, but
            # anthropic SDK 1.x moved to httpx2 and rejects an httpx transport
            # with a TypeError at construction. Letting the SDK build its own
            # transport avoids the mismatch without pinning anthropic back to 0.x.
            client=anthropic_sdk.AsyncAnthropic(max_retries=0),
            # Caches the system prompt (which carries the whole deck), the tool
            # schemas, and the chat history.
            caching="ephemeral",
            max_tokens=MAX_OUTPUT_TOKENS,
        )

    from google.genai import types
    from livekit.plugins import google

    # Gemini 2.5 models think by default, which is latency this budget cannot
    # absorb on every conversational turn. thinking_budget=0 disables it --
    # a control the Anthropic plugin does not expose (see README).
    budget = int(os.getenv("GEMINI_THINKING_BUDGET", "0"))
    logger.info("LLM: google %s (thinking_budget=%d)", model, budget)
    return google.LLM(
        model=model,
        temperature=0.6,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        thinking_config=types.ThinkingConfig(thinking_budget=budget),
    )
