"""Shared eval fixtures.

Every suite here skips cleanly when its API key is absent, so `pytest evals/`
works on a fresh clone and in CI without secrets -- it just reports skips
instead of failing. Nothing is silently passed over: skip reasons name the
missing key.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env.local")
load_dotenv(ROOT / ".env")

# The agent modules use flat imports (`from deck import ...`).
sys.path.insert(0, str(ROOT / "agent" / "src"))


@pytest.fixture(scope="session")
def deck():
    from deck import load_deck

    return load_deck("metformin")


@pytest.fixture(scope="session")
def instructions(deck):
    """The exact system prompt the live agent runs with."""
    from prompts import build_instructions

    return build_instructions(deck)


@pytest.fixture(scope="session")
def lexicon() -> list[str]:
    from deck import lexicon as _lexicon

    return _lexicon()


@pytest.fixture(scope="session")
def anthropic_client():
    if not os.getenv("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")
    import anthropic

    return anthropic.Anthropic()


@pytest.fixture(scope="session")
def deepgram_client():
    if not os.getenv("DEEPGRAM_API_KEY"):
        pytest.skip("DEEPGRAM_API_KEY not set")
    from deepgram import DeepgramClient

    return DeepgramClient(api_key=os.environ["DEEPGRAM_API_KEY"])


@pytest.fixture(scope="session")
def llm_model() -> str:
    return os.getenv("EVAL_LLM_MODEL") or os.getenv("LLM_MODEL", "claude-opus-5")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "costly: makes billable API calls")
