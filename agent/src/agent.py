"""LiveKit agent worker entrypoint.

Run locally:      python src/agent.py dev
Run in a room:    the worker registers under AGENT_NAME and LiveKit dispatches
                  it automatically when a client joins a room.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from livekit.agents import (
    AgentServer,
    AgentSession,
    JobContext,
    TurnHandlingOptions,
    cli,
    inference,
)
from livekit.plugins import deepgram

from audit import AuditLog
from deck import lexicon, load_deck
from llm_provider import build_llm, model_name, provider_name
from medinfo_agent import MedInfoAgent
from prompts import GREETING
from rpc import RpcBridge
from telemetry import Telemetry

_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_ROOT / ".env.local")
load_dotenv(_ROOT / ".env")

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("agent")


def _build_stt() -> deepgram.STT:
    """Deepgram STT with the medical lexicon boosted.

    `keyterm` is the Nova-3 parameter (the older `keywords` is Nova-2 only and
    is silently ignored by Nova-3). Boosting drug names materially improves
    recognition of things like "empagliflozin" and "hydrochlorothiazide".
    """
    model = os.getenv("STT_MODEL", "nova-3")
    terms = lexicon()
    logger.info("STT %s with %d boosted keyterms", model, len(terms))
    return deepgram.STT(
        model=model,
        keyterm=terms,
        # Dosing questions are full of numbers; render them as digits so the
        # model reads "500 mg" rather than "five hundred milligrams".
        numerals=True,
    )


def _safe_on(session: AgentSession, event: str, handler) -> None:
    """Attach an event handler, tolerating events absent in this SDK version."""
    try:
        session.on(event, handler)
        logger.debug("attached handler for %s", event)
    except Exception as exc:
        logger.warning("could not attach handler for '%s': %s", event, exc)


server = AgentServer()


@server.rtc_session(agent_name=os.getenv("AGENT_NAME", "medinfo-agent"))
async def medinfo_session(ctx: JobContext) -> None:
    session_id = f"{ctx.room.name}-{uuid.uuid4().hex[:8]}"
    ctx.log_context_fields = {"room": ctx.room.name, "session_id": session_id}

    deck = load_deck()
    rpc = RpcBridge(ctx.room)
    audit = AuditLog(session_id=session_id, room=ctx.room.name)
    telemetry = Telemetry(session_id=session_id, rpc=rpc)

    llm_provider = provider_name()
    llm_model = model_name(llm_provider)
    tts_model = os.getenv("TTS_MODEL", "aura-2-asteria-en")

    session = AgentSession(
        stt=_build_stt(),
        tts=deepgram.TTS(model=tts_model),
        turn_handling=TurnHandlingOptions(
            # Semantic + acoustic end-of-turn detection. Supplies its own VAD.
            turn_detection=inference.TurnDetector(),
            # FR-4 / FR-5. Adaptive mode uses the turn detector to tell a real
            # barge-in from a backchannel ("mhm", "right"), so the agent talks
            # through the latter instead of stopping dead on an acknowledgement.
            interruption={
                "enabled": True,
                "mode": "adaptive",
                # Ignore sub-200ms blips (coughs, keyboard, door) as interruptions.
                "min_duration": 0.2,
                # Require at least one real word, not just voiced noise.
                "min_words": 1,
                # If we stopped for what turned out to be nothing, pick the
                # sentence back up rather than leaving dead air.
                "resume_false_interruption": True,
                "false_interruption_timeout": 1.5,
            },
            # Start generating before end-of-turn is confirmed. This is where
            # most of the perceived latency in NFR-1 is recovered.
            preemptive_generation={"enabled": True, "preemptive_tts": True},
        ),
    )

    agent = MedInfoAgent(
        deck=deck,
        rpc=rpc,
        audit=audit,
        llm=build_llm(),
    )

    # ------------------------------------------------------------ telemetry
    def on_metrics(ev) -> None:
        telemetry.on_metrics(getattr(ev, "metrics", ev))

    _safe_on(session, "metrics_collected", on_metrics)

    # ------------------------------------------------- transcript + audit
    def on_conversation_item(ev) -> None:
        item = getattr(ev, "item", ev)
        role = str(getattr(item, "role", "unknown"))
        text = getattr(item, "text_content", None) or getattr(item, "content", "")
        interrupted = bool(getattr(item, "interrupted", False))

        if role == "user":
            audit.next_turn()

        audit.record(
            "transcript",
            role=role,
            text=text,
            slide=agent.current_slide,
            interrupted=interrupted,
            model=llm_model if role == "assistant" else None,
        )

        if role == "assistant":
            # Flush the accumulated per-hop metrics for this turn and push them
            # to the browser HUD. `interrupted` records whether the text above
            # is the full generation or only the part the user actually heard --
            # see the truncation note in the README.
            asyncio.create_task(
                telemetry.flush(
                    turn=audit.current_turn,
                    slide=agent.current_slide,
                    interrupted=interrupted,
                    model=llm_model,
                )
            )

    _safe_on(session, "conversation_item_added", on_conversation_item)

    # ----------------------------------------------------------- lifecycle
    await session.start(agent=agent, room=ctx.room)
    await ctx.connect()

    audit.record(
        "session_start",
        deck_id=deck.deck_id,
        llm_provider=llm_provider,
        llm_model=llm_model,
        stt_model=os.getenv("STT_MODEL", "nova-3"),
        tts_model=tts_model,
    )

    # Sync the deck to slide 1 in case the client connected mid-flight.
    await rpc.goto_slide(1, "session start", deck.slides[0].citation)

    await session.generate_reply(instructions=GREETING.format(title=deck.title))


if __name__ == "__main__":
    cli.run_app(server)
