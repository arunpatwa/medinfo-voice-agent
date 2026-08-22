"""Provider-neutral tool specs shared by the eval suites.

Deliberately plain JSON Schema rather than a provider SDK type, so the same
definition can be handed to Claude or Gemini. evals/llm_provider.py does the
per-provider conversion.

These mirror the @function_tool signatures in agent/src/medinfo_agent.py. If
they drift, the evals stop testing the thing that actually ships.
"""

from __future__ import annotations

GOTO_SLIDE = {
    "name": "goto_slide",
    "description": (
        "Change the slide the user is looking at. Call this whenever the user's "
        "question is better answered on a different slide than the one currently "
        "displayed. Do not call it if the current slide already covers the question."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "slide_id": {
                "type": "integer",
                "description": "The slide to display. Valid values are 1 through 6.",
            },
            "reason": {
                "type": "string",
                "description": "Short phrase naming the topic that triggered the jump.",
            },
        },
        "required": ["slide_id", "reason"],
        "additionalProperties": False,
    },
}

FLAG_ADVERSE_EVENT = {
    "name": "flag_adverse_event",
    "description": (
        "Log a suspected adverse event for pharmacovigilance reporting. You MUST "
        "call this whenever the user describes a side effect, adverse reaction, or "
        "negative experience with the product, whether it happened to them or to "
        "someone else."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "term": {"type": "string", "description": "The reaction in clinical terms."},
            "verbatim": {"type": "string", "description": "The user's own words."},
            "severity": {
                "type": "string",
                "enum": ["serious", "non_serious", "unknown"],
                "description": "Seriousness classification.",
            },
        },
        "required": ["term", "verbatim", "severity"],
        "additionalProperties": False,
    },
}
