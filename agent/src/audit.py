"""Structured audit trail (FR-10).

Every turn produces one JSONL record: what the user said, what the agent said,
which slide was showing, which tools fired, token usage, latency, and -- when
the user interrupted -- how much of the reply was actually heard.

One record, one sink, chosen in this order:
  1. Append to local JSONL. The control-plane API reads this directory, so a
     successful local write is already queryable -- no POST needed.
  2. If the local write fails (agent running in a container with no shared
     volume), POST to the API instead.

Writing to both would double-record every turn. Either way the session keeps
running (NFR-2) -- a voice call must never drop because logging failed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("agent.audit")


def _audit_dir() -> Path:
    path = Path(os.getenv("AUDIT_DIR") or Path(__file__).resolve().parents[2] / "data" / "audit")
    path.mkdir(parents=True, exist_ok=True)
    return path


class AuditLog:
    def __init__(self, session_id: str, room: str) -> None:
        self.session_id = session_id
        self.room = room
        self._path = _audit_dir() / f"{session_id}.jsonl"
        self._api_base = os.getenv("API_BASE_URL", "").rstrip("/")
        self._turn = 0
        self._api_broken = False

    def next_turn(self) -> int:
        self._turn += 1
        return self._turn

    @property
    def current_turn(self) -> int:
        return self._turn

    def record(self, kind: str, **fields: Any) -> dict[str, Any]:
        """Write one audit record. Returns it so callers can also inspect it."""
        entry: dict[str, Any] = {
            "ts": time.time(),
            "session_id": self.session_id,
            "room": self.room,
            "turn": self._turn,
            "kind": kind,
            **fields,
        }

        try:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, separators=(",", ":"), default=str) + "\n")
        except OSError as exc:
            # Local sink unavailable -- fall back to shipping it to the API so
            # the record isn't simply lost.
            logger.error("audit local write failed (%s); posting to API", exc)
            if self._api_base and not self._api_broken:
                asyncio.create_task(self._post(entry))

        return entry

    async def _post(self, entry: dict[str, Any]) -> None:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=2.0) as client:
                await client.post(f"{self._api_base}/api/audit", json=entry)
        except Exception as exc:
            # Degrade once, loudly, then stop trying for this session so we
            # don't spam a dead endpoint on every turn.
            self._api_broken = True
            logger.warning(
                "audit API unreachable (%s); falling back to local JSONL only", exc
            )
