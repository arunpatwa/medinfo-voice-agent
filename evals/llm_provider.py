"""Provider-neutral LLM access for the eval suites.

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
from dataclasses import dataclass, field
from typing import Any

MAX_OUTPUT_TOKENS = 1024

DEFAULT_MODELS = {
    "anthropic": "claude-opus-5",
    "google": "gemini-2.5-flash",
}

KEY_ENV = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "google": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
}


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

    def ask(self, system: str, user: str, tools: list[dict]) -> LLMReply:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=system,
            tools=[{**t, "strict": True} for t in tools],
            thinking={"type": "adaptive"},
            # Slide routing is a shallow decision; low effort keeps evals cheap.
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": user}],
        )
        calls, text = [], []
        for block in response.content:
            kind = getattr(block, "type", None)
            if kind == "tool_use":
                calls.append(ToolCall(block.name, dict(block.input or {})))
            elif kind == "text":
                text.append(block.text)
        return LLMReply(" ".join(text), tuple(calls))


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

    def ask(self, system: str, user: str, tools: list[dict]) -> LLMReply:
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
        response = self._client.models.generate_content(
            model=self.model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                temperature=0.6,
                tools=[types.Tool(function_declarations=declarations)],
                # Gemini 2.5 thinks by default; disable it so eval latency and
                # cost reflect the voice configuration.
                thinking_config=types.ThinkingConfig(
                    thinking_budget=int(os.getenv("GEMINI_THINKING_BUDGET", "0"))
                ),
                # We want the raw tool call, not an auto-executed round trip.
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
            ),
        )

        calls = [
            ToolCall(fc.name, dict(fc.args or {}))
            for fc in (getattr(response, "function_calls", None) or [])
        ]
        text_parts: list[str] = []
        for candidate in getattr(response, "candidates", None) or []:
            content = getattr(candidate, "content", None)
            for part in (getattr(content, "parts", None) or []):
                if getattr(part, "text", None):
                    text_parts.append(part.text)
                # Fallback if the convenience accessor is unavailable.
                fc = getattr(part, "function_call", None)
                if fc is not None and not calls:
                    calls.append(ToolCall(fc.name, dict(fc.args or {})))
        return LLMReply(" ".join(text_parts), tuple(calls))


def build_provider(provider: str | None = None):
    provider = provider or selected_provider()
    model = selected_model(provider)
    if provider == "anthropic":
        return AnthropicProvider(model)
    return GoogleProvider(model)
