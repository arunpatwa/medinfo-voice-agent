"""Deck loading and prompt rendering.

The deck JSON in `deck/` is the single source of truth: this module renders it
into the agent's system prompt, and the control-plane API serves the same file
to the frontend. There is deliberately no retrieval layer -- six slides is
~1.5K tokens, which fits in the prompt with room to spare.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _deck_dir() -> Path:
    """Resolve the deck directory, honouring DECK_DIR for container layouts."""
    if env := os.getenv("DECK_DIR"):
        return Path(env)
    # agent/src/deck.py -> repo root -> deck/
    return Path(__file__).resolve().parents[2] / "deck"


@dataclass(frozen=True)
class Slide:
    id: int
    slug: str
    title: str
    topics: tuple[str, ...]
    bullets: tuple[str, ...]
    narration: str
    citation: str


@dataclass(frozen=True)
class Deck:
    deck_id: str
    title: str
    subtitle: str
    disclaimer: str
    source: str
    slides: tuple[Slide, ...]

    def slide(self, slide_id: int) -> Slide | None:
        return next((s for s in self.slides if s.id == slide_id), None)

    @property
    def valid_ids(self) -> tuple[int, ...]:
        return tuple(s.id for s in self.slides)

    def render_for_prompt(self) -> str:
        """Render the full deck as the stable, cacheable part of the prompt.

        Kept deterministic (no timestamps, no dict reordering) so the prefix is
        byte-identical across turns and stays eligible for prompt caching.
        """
        lines = [
            f"DECK: {self.title}",
            f"SUBTITLE: {self.subtitle}",
            f"SOURCE: {self.source}",
            "",
        ]
        for s in self.slides:
            lines.append(f"--- SLIDE {s.id} ({s.slug}): {s.title} ---")
            lines.append(f"Covers questions about: {', '.join(s.topics)}")
            for b in s.bullets:
                lines.append(f"  - {b}")
            # Spoken script, already phrased for TTS (numbers written out,
            # initialisms hyphenated). Use it verbatim for walkthroughs.
            lines.append(f"  Spoken script: {s.narration}")
            lines.append(f"  Citation: {s.citation}")
            lines.append("")
        return "\n".join(lines)


@lru_cache(maxsize=8)
def load_deck(deck_id: str | None = None) -> Deck:
    deck_id = deck_id or os.getenv("DECK_ID", "metformin")
    path = _deck_dir() / f"{deck_id}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Deck(
        deck_id=raw["deck_id"],
        title=raw["title"],
        subtitle=raw.get("subtitle", ""),
        disclaimer=raw.get("disclaimer", ""),
        source=raw.get("source", ""),
        slides=tuple(
            Slide(
                id=s["id"],
                slug=s["slug"],
                title=s["title"],
                topics=tuple(s.get("topics", ())),
                bullets=tuple(s.get("bullets", ())),
                narration=s.get("narration", ""),
                citation=s.get("citation", ""),
            )
            for s in raw["slides"]
        ),
    )


def lexicon() -> list[str]:
    """Medical terms passed to Deepgram STT as keyterms to boost recognition."""
    path = Path(__file__).resolve().parents[2] / "evals" / "fixtures" / "lexicon.txt"
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
