"""Agent -> browser RPC bridge.

The agent drives the frontend by calling RPC methods the browser registered on
connect: `deck.goto`, `alert.adverseEvent`, `telemetry.turn`.

Everything here is best-effort. A failed RPC must never break the voice session,
so all calls swallow exceptions and log instead (NFR-2).
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("agent.rpc")

# LiveKit caps RPC payloads at 15 KiB (UTF-8).
_MAX_PAYLOAD_BYTES = 15 * 1024


class RpcBridge:
    """Sends RPC calls to whichever human participant is in the room."""

    def __init__(self, room: Any) -> None:
        self._room = room

    def _target_identity(self) -> str | None:
        """Pick the human participant to send to.

        Agents publish as participants too, so prefer a remote participant that
        isn't another agent. With a single-user demo room there is exactly one.
        """
        try:
            participants = list(self._room.remote_participants.values())
        except Exception:  # room not connected yet
            return None
        for p in participants:
            kind = str(getattr(p, "kind", "")).lower()
            if "agent" in kind:
                continue
            return p.identity
        return participants[0].identity if participants else None

    async def call(self, method: str, payload: dict[str, Any]) -> str | None:
        identity = self._target_identity()
        if identity is None:
            logger.warning("rpc %s skipped: no participant in room yet", method)
            return None

        body = json.dumps(payload, separators=(",", ":"))
        if len(body.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
            logger.error("rpc %s skipped: payload exceeds 15KiB", method)
            return None

        try:
            return await self._room.local_participant.perform_rpc(
                destination_identity=identity,
                method=method,
                payload=body,
            )
        except Exception as exc:
            # The browser may not have registered the handler yet, or the user
            # navigated away. Neither is fatal to the conversation.
            logger.warning("rpc %s to %s failed: %s", method, identity, exc)
            return None

    async def goto_slide(self, slide_id: int, reason: str, citation: str = "") -> None:
        await self.call(
            "deck.goto",
            {"slide_id": slide_id, "reason": reason, "citation": citation},
        )

    async def adverse_event(self, term: str, verbatim: str, severity: str) -> None:
        await self.call(
            "alert.adverseEvent",
            {"term": term, "verbatim": verbatim, "severity": severity},
        )

    async def telemetry(self, record: dict[str, Any]) -> None:
        await self.call("telemetry.turn", record)
