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
    "google": "gemini-3.5-flash",
}


def _thinking_config(model: str):
    """ThinkingConfig appropriate to the model generation, or None.

    The two knobs are not interchangeable and each generation rejects the other:

      * Gemini 2.5  -> thinking_budget (an int). thinking_level returns 400.
      * Gemini 3.x  -> thinking_level ("LOW"/"MEDIUM"/"HIGH").
                       thinking_budget returns 400.

    Voice turns want shallow reasoning, so the default is the cheapest setting
    each generation offers. GEMINI_THINKING_LEVEL=off omits the config entirely.
    """
    from google.genai import types

    level = os.getenv("GEMINI_THINKING_LEVEL", "LOW").strip().upper()
    if level in ("OFF", "", "DEFAULT"):
        return None
    if model.startswith("gemini-2"):
        budget = 0 if level == "LOW" else int(os.getenv("GEMINI_THINKING_BUDGET", "1024"))
        return types.ThinkingConfig(thinking_budget=budget)
    return types.ThinkingConfig(thinking_level=level)


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

    from livekit.plugins import google

    # Gemini models reason by default, which is latency a voice turn cannot
    # absorb. Note the Anthropic plugin exposes no equivalent control at all
    # (see README, "Model choice") -- this knob exists on one provider only.
    thinking = _thinking_config(model)
    logger.info(
        "LLM: google %s (thinking_level=%s)",
        model,
        getattr(thinking, "thinking_level", "model default"),
    )
    kwargs = {
        "model": model,
        "temperature": 0.6,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
    }
    if thinking is not None:
        kwargs["thinking_config"] = thinking
    return google.LLM(**kwargs)
