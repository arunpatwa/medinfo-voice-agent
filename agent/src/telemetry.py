"""Per-hop latency telemetry (FR-12, NFR-1).

LiveKit emits metrics objects on the session's `metrics_collected` event -- one
per pipeline stage (end-of-utterance detection, STT, LLM, TTS). We flatten
whichever fields are present into a single per-turn record, append it to JSONL
for the latency eval, and push it to the browser HUD over RPC.

Field names are read defensively via a candidate list rather than hard-coded,
because they vary across plugin versions. Set LOG_RAW_METRICS=1 once to dump the
actual attribute names your installed version emits, then trim the candidates.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("agent.telemetry")

# Candidate attribute names per logical measurement. First match wins.
_FIELD_CANDIDATES: dict[str, tuple[str, ...]] = {
    "end_of_utterance_ms": ("end_of_utterance_delay", "eou_delay"),
    "transcription_ms": ("transcription_delay",),
    "llm_ttft_ms": ("ttft", "time_to_first_token"),
    "llm_duration_ms": ("duration",),
    "tts_ttfb_ms": ("ttfb", "time_to_first_byte"),
    "audio_duration_ms": ("audio_duration",),
    "prompt_tokens": ("prompt_tokens", "input_tokens"),
    "completion_tokens": ("completion_tokens", "output_tokens"),
    "cached_tokens": ("prompt_cached_tokens", "cache_read_input_tokens"),
}

# Which measurements are seconds in the SDK and need scaling to milliseconds.
_SECONDS_FIELDS = {
    "end_of_utterance_ms",
    "transcription_ms",
    "llm_ttft_ms",
    "llm_duration_ms",
    "tts_ttfb_ms",
    "audio_duration_ms",
}


def _latency_dir() -> Path:
    path = Path(
        os.getenv("LATENCY_DIR") or Path(__file__).resolve().parents[2] / "data" / "latency"
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


def _extract(metrics: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for logical, candidates in _FIELD_CANDIDATES.items():
        for name in candidates:
            value = getattr(metrics, name, None)
            if value is None:
                continue
            if logical in _SECONDS_FIELDS and isinstance(value, (int, float)):
                out[logical] = round(float(value) * 1000, 1)
            else:
                out[logical] = value
            break
    return out


class Telemetry:
    """Accumulates metrics for the current turn and flushes on turn boundaries."""

    def __init__(self, session_id: str, rpc: Any | None = None) -> None:
        self.session_id = session_id
        self._rpc = rpc
        self._path = _latency_dir() / f"{session_id}.jsonl"
        self._turn: dict[str, Any] = {}
        self._logged_raw = False

    def on_metrics(self, metrics: Any) -> None:
        """Handler for the session's `metrics_collected` event."""
        if os.getenv("LOG_RAW_METRICS") and not self._logged_raw:
            logger.info(
                "raw metrics type=%s attrs=%s",
                type(metrics).__name__,
                sorted(a for a in dir(metrics) if not a.startswith("_")),
            )
            self._logged_raw = True

        extracted = _extract(metrics)
        if not extracted:
            return
        self._turn.setdefault("stages", []).append(
            {"stage": type(metrics).__name__, **extracted}
        )
        self._turn.update(extracted)

    async def flush(self, turn: int, **extra: Any) -> dict[str, Any]:
        """Write the accumulated turn record and push it to the HUD."""
        record = {
            "ts": time.time(),
            "session_id": self.session_id,
            "turn": turn,
            **self._turn,
            **extra,
        }
        record["time_to_first_audio_ms"] = self._time_to_first_audio(record)
        self._turn = {}

        try:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, separators=(",", ":"), default=str) + "\n")
        except OSError as exc:
            logger.error("latency write failed: %s", exc)

        if self._rpc is not None:
            await self._rpc.telemetry(record)
        return record

    @staticmethod
    def _time_to_first_audio(record: dict[str, Any]) -> float | None:
        """Perceived latency: end of user speech -> first audio byte out.

        This is the number that actually matters to the user, and the one
        NFR-1's p95 budget is written against.
        """
        legs = [
            record.get("end_of_utterance_ms"),
            record.get("transcription_ms"),
            record.get("llm_ttft_ms"),
            record.get("tts_ttfb_ms"),
        ]
        present = [x for x in legs if isinstance(x, (int, float))]
        return round(sum(present), 1) if present else None
