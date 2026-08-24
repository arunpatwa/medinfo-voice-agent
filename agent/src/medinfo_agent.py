"""The agent itself: instructions plus the two tools that drive the UI.

`goto_slide` is what makes slides follow the conversation instead of a clicker:
the model decides, the tool pushes an RPC, and the browser re-renders while the
spoken answer is still streaming.

`flag_adverse_event` exists because pharmacovigilance reporting is a legal
obligation on a 24-hour clock; any real pharma voice agent needs this path.
"""

from __future__ import annotations

import logging

from livekit.agents import Agent, RunContext, function_tool

from audit import AuditLog
from deck import Deck
from prompts import build_instructions
from rpc import RpcBridge

logger = logging.getLogger("agent.medinfo")

_SEVERITIES = ("non_serious", "serious", "unknown")


class MedInfoAgent(Agent):
    def __init__(self, deck: Deck, rpc: RpcBridge, audit: AuditLog, llm=None) -> None:
        super().__init__(instructions=build_instructions(deck), llm=llm)
        self._deck = deck
        self._rpc = rpc
        self._audit = audit
        self.current_slide = 1

    # ---------------------------------------------------------------- tools

    @function_tool()
    async def goto_slide(
        self,
        context: RunContext,
        slide_id: int,
        reason: str,
    ) -> str:
        """Change the slide the user is looking at.

        Call this whenever the user's question is better answered on a different
        slide than the one currently displayed. Call it BEFORE speaking your
        answer so the slide changes as you begin talking. Do not call it if the
        current slide already covers the question.

        Args:
            slide_id: The slide to display. Valid values are 1 through 6.
            reason: A short phrase naming the topic that triggered the jump,
                for example "renal dosing" or "boxed warning". Used for the
                audit trail; never spoken aloud.
        """
        slide = self._deck.slide(slide_id)
        if slide is None:
            valid = ", ".join(str(i) for i in self._deck.valid_ids)
            logger.warning("goto_slide rejected invalid id %s", slide_id)
            return f"No slide {slide_id} exists. Valid slides are {valid}. Stay on the current slide."

        previous = self.current_slide

        # The prompt tells the model not to call this when the current slide
        # already answers the question, but a live session showed 4 of 11 calls
        # were no-ops (N->N). Rather than fight the prompt, absorb it here: skip
        # the RPC round-trip and tell the model plainly where it already is.
        if slide_id == previous:
            self._audit.record(
                "slide_noop", slide=slide_id, slide_slug=slide.slug, reason=reason
            )
            logger.info("goto_slide %s was already displayed (%s)", slide_id, reason)
            return (
                f"Slide {slide_id}: {slide.title} is already on screen. "
                "Answer from this slide's content without changing slides."
            )

        self.current_slide = slide_id
        await self._rpc.goto_slide(slide_id, reason, slide.citation)
        self._audit.record(
            "slide_change",
            slide_from=previous,
            slide_to=slide_id,
            slide_slug=slide.slug,
            reason=reason,
        )
        logger.info("slide %s -> %s (%s)", previous, slide_id, reason)

        # The return value is what the model sees, so it doubles as the model's
        # only source of truth for which slide is currently displayed.
        return (
            f"Now showing slide {slide_id}: {slide.title}. "
            f"Answer the user's question from this slide's content."
        )

    @function_tool()
    async def flag_adverse_event(
        self,
        context: RunContext,
        term: str,
        verbatim: str,
        severity: str,
    ) -> str:
        """Log a suspected adverse event for pharmacovigilance reporting.

        You MUST call this whenever the user describes a side effect, adverse
        reaction, or negative experience with the product -- whether it happened
        to them or to someone else, and however casually they mention it. This is
        a regulatory obligation, not a judgement call. Call it before you finish
        your response.

        Args:
            term: The reported reaction in clinical terms, for example "nausea"
                or "lactic acidosis".
            verbatim: The user's own words describing what happened, quoted as
                closely as you can.
            severity: One of "serious" (hospitalisation, life-threatening,
                disability, death, congenital anomaly), "non_serious", or
                "unknown" if you cannot tell.
        """
        if severity not in _SEVERITIES:
            severity = "unknown"

        await self._rpc.adverse_event(term, verbatim, severity)
        self._audit.record(
            "adverse_event",
            category="pharmacovigilance",
            term=term,
            verbatim=verbatim,
            severity=severity,
            slide=self.current_slide,
            requires_followup=True,
        )
        logger.warning("ADVERSE EVENT flagged: %s (%s)", term, severity)

        return (
            f"Logged '{term}' as a suspected adverse event for reporting. "
            "Now acknowledge what the user described with care, tell them it has "
            "been recorded for safety reporting, and advise them to contact their "
            "healthcare provider. Do not diagnose and do not recommend any change "
            "to their treatment."
        )
