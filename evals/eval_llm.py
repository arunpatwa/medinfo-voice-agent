"""Provider-neutral LLM access for the eval suites.

Named eval_llm rather than llm_provider so it cannot shadow
agent/src/llm_provider.py -- conftest puts both directories on sys.path, and
two modules with the same name would resolve by path order.

The evals assert on behaviour (did the right tool fire with the right argument),
not on a vendor's response shape. This module normalises both so the same test
body runs against Claude and Gemini:

    LLM_PROVIDER=anthropic pytest evals/
    LLM_PROVIDER=google    pytest evals/

Unlike the live agent, the evals talk to the vendor SDKs directly, so they CAN
set reasoning depth on both providers. Keep that in mind when comparing eval
latency to agent latency -- they are not the same configuration.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

MAX_OUTPUT_TOKENS = 1024

# The Gemini free tier allows as few as 5 requests/minute per model, and an eval
# sweep makes dozens. Retrying on the server's own retryDelay is the difference
# between a suite that runs on a free key and one that only runs on a paid one.
MAX_RETRIES = 5
DEFAULT_BACKOFF_S = 20.0


def default_tool_result(name: str, args: dict) -> str:
    """Stand-in for what agent/src/medinfo_agent.py returns to the model.

    Kept faithful to the real tool bodies: the model's next turn depends on
    these strings, so an eval that invents different ones is testing a
    conversation that never happens.
    """
    if name == "goto_slide":
        return (
            f"Now showing slide {args.get('slide_id')}. "
            "Answer the user's question from this slide's content."
        )
    if name == "flag_adverse_event":
        return (
            f"Logged '{args.get('term')}' as a suspected adverse event for reporting. "
            "Now acknowledge what the user described with care, tell them it has been "
            "recorded for safety reporting, and advise them to contact their "
            "healthcare provider."
        )
    return "ok"

DEFAULT_MODELS = {
    "anthropic": "claude-opus-5",
    # flash-lite has far more generous free-tier daily quota than flash, which
    # matters for a sweep of dozens of calls.
    "google": "gemini-3.5-flash-lite",
}

KEY_ENV = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "google": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
}


class DailyQuotaExhausted(RuntimeError):
    """The per-day free-tier cap is gone; waiting will not help today."""


def _retry_delay_seconds(exc: Exception) -> float | None:
    """Seconds to wait per the API's own RetryInfo, or None if not rate-limited.

    Raises DailyQuotaExhausted for a per-DAY quota violation. That distinction
    matters: a per-minute cap clears in under a minute, but a per-day cap does
    not, and retrying against it just burns wall-clock before failing anyway.
    """
    status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    text = str(exc)
    if status != 429 and "RESOURCE_EXHAUSTED" not in text and "429" not in text:
        return None
    if "PerDay" in text or "per day" in text.lower():
        match = re.search(r"'quotaValue':\s*'(\d+)'", text)
        cap = match.group(1) if match else "?"
        raise DailyQuotaExhausted(
            f"free-tier daily cap of {cap} requests is exhausted for this model; "
            "switch models with EVAL_LLM_MODEL, or wait for the quota to reset"
        ) from exc
    match = re.search(r"[Rr]etry in ([0-9.]+)s", text)
    if match:
        return float(match.group(1)) + 1.0
    match = re.search(r"'retryDelay':\s*'(\d+)s'", text)
    if match:
        return float(match.group(1)) + 1.0
    return DEFAULT_BACKOFF_S


def _with_retries(call, label: str):
    """Run `call`, backing off on rate limits and transient 5xx."""
    last: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 - provider SDKs raise varied types
            last = exc
            delay = _retry_delay_seconds(exc)
            if delay is None and ("503" in str(exc) or "UNAVAILABLE" in str(exc)):
                delay = 5.0 * (attempt + 1)
            if delay is None or attempt == MAX_RETRIES - 1:
                raise
            print(f"    [{label}] rate limited, waiting {delay:.0f}s "
                  f"(attempt {attempt + 1}/{MAX_RETRIES})", flush=True)
            time.sleep(delay)
    raise last  # pragma: no cover


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


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMReply:
    text: str
    tool_calls: tuple[ToolCall, ...] = ()

    @property
    def tool_names(self) -> set[str]:
        return {c.name for c in self.tool_calls}

    def arg(self, tool: str, key: str) -> Any | None:
        for c in self.tool_calls:
            if c.name == tool:
                return c.args.get(key)
        return None


def selected_provider() -> str:
    name = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()
    if name not in DEFAULT_MODELS:
        raise ValueError(f"unknown LLM_PROVIDER {name!r}")
    return name


def selected_model(provider: str | None = None) -> str:
    provider = provider or selected_provider()
    return (
        os.getenv("EVAL_LLM_MODEL")
        or os.getenv("LLM_MODEL")
        or DEFAULT_MODELS[provider]
    )


def missing_key(provider: str) -> str | None:
    """Name of the absent credential, or None if one is present."""
    names = KEY_ENV[provider]
    return None if any(os.getenv(n) for n in names) else " or ".join(names)


# --------------------------------------------------------------- anthropic


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str) -> None:
        import anthropic

        self.model = model
        self._client = anthropic.Anthropic()

    def ask(
        self, system: str, user: str, tools: list[dict], resolve_tools: bool = True
    ) -> LLMReply:
        messages: list[dict] = [{"role": "user", "content": user}]
        all_calls: list[ToolCall] = []
        text: list[str] = []

        # Two legs, mirroring the agent: the model usually spends leg one on a
        # tool call and only speaks on leg two, once the result comes back.
        for _ in range(2):
            response = self._request(system, messages, tools)
            calls, leg_text = self._parse(response)
            all_calls.extend(calls)
            text.extend(leg_text)
            if not calls or not resolve_tools:
                break
            messages.append({"role": "assistant", "content": response.content})
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": default_tool_result(
                                block.name, dict(block.input or {})
                            ),
                        }
                        for block in response.content
                        if getattr(block, "type", None) == "tool_use"
                    ],
                }
            )
        return LLMReply(" ".join(text), tuple(all_calls))

    @staticmethod
    def _parse(response):
        calls, text = [], []
        for block in response.content:
            kind = getattr(block, "type", None)
            if kind == "tool_use":
                calls.append(ToolCall(block.name, dict(block.input or {})))
            elif kind == "text":
                text.append(block.text)
        return calls, text

    def _request(self, system: str, messages: list[dict], tools: list[dict]):
        return _with_retries(
            lambda: self._client.messages.create(
                model=self.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=system,
                tools=[{**t, "strict": True} for t in tools],
                thinking={"type": "adaptive"},
                # Shallow decision; low effort keeps evals cheap.
                output_config={"effort": "low"},
                messages=messages,
            ),
            self.model,
        )


# ------------------------------------------------------------------ google


class GoogleProvider:
    name = "google"

    def __init__(self, model: str) -> None:
        from google import genai

        self.model = model
        self._genai = genai
        # Honour either env var name; genai only reads GOOGLE_API_KEY itself.
        key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        self._client = genai.Client(api_key=key)

    def ask(
        self, system: str, user: str, tools: list[dict], resolve_tools: bool = True
    ) -> LLMReply:
        from google.genai import types

        declarations = [
            types.FunctionDeclaration(
                name=t["name"],
                description=t["description"],
                # Gemini accepts raw JSON Schema here, so the neutral spec in
                # tool_specs.py passes straight through with no translation.
                parameters_json_schema=t["input_schema"],
            )
            for t in tools
        ]
        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            temperature=0.6,
            tools=[types.Tool(function_declarations=declarations)],
            # thinking_level vs thinking_budget is generation-dependent; see
            # _thinking_config.
            thinking_config=_thinking_config(self.model),
            # We want the raw tool call, not an auto-executed round trip.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        )

        contents: list = [
            types.Content(role="user", parts=[types.Part.from_text(text=user)])
        ]
        all_calls: list[ToolCall] = []
        text: list[str] = []

        # Two legs, mirroring the agent: Gemini typically returns only a
        # function call on leg one and speaks on leg two, once the tool result
        # is fed back. A single-leg eval would read an empty reply and
        # mistake it for a refusal failure.
        for _ in range(2):
            response = _with_retries(
                lambda: self._client.models.generate_content(
                    model=self.model, contents=contents, config=config
                ),
                self.model,
            )
            calls, leg_text, model_content = self._parse(response)
            all_calls.extend(calls)
            text.extend(leg_text)
            if not calls or not resolve_tools:
                break
            if model_content is not None:
                contents.append(model_content)
            contents.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_function_response(
                            name=c.name,
                            response={"result": default_tool_result(c.name, c.args)},
                        )
                        for c in calls
                    ],
                )
            )
        return LLMReply(" ".join(t for t in text if t), tuple(all_calls))

    @staticmethod
    def _parse(response):
        calls = [
            ToolCall(fc.name, dict(fc.args or {}))
            for fc in (getattr(response, "function_calls", None) or [])
        ]
        text_parts: list[str] = []
        model_content = None
        for candidate in getattr(response, "candidates", None) or []:
            content = getattr(candidate, "content", None)
            if content is not None and model_content is None:
                model_content = content
            for part in (getattr(content, "parts", None) or []):
                if getattr(part, "text", None):
                    text_parts.append(part.text)
                fc = getattr(part, "function_call", None)
                if fc is not None and not calls:
                    calls.append(ToolCall(fc.name, dict(fc.args or {})))
        return calls, text_parts, model_content


def build_provider(provider: str | None = None):
    provider = provider or selected_provider()
    model = selected_model(provider)
    if provider == "anthropic":
        return AnthropicProvider(model)
    return GoogleProvider(model)
