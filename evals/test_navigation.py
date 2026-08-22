"""Navigation accuracy eval (FR-11).

Does Claude pick the right slide for a real question? This exercises exactly the
contract the live agent depends on -- same system prompt, same tool schema --
but with no audio, so it runs in seconds and costs cents.

Note this eval CAN configure thinking and effort, because it calls the Anthropic
SDK directly. The LiveKit plugin cannot (see README, "Model choice"), which is
why the live agent and this eval are not perfectly equivalent.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

FIXTURES = Path(__file__).parent / "fixtures" / "navigation.yaml"
STARTING_SLIDE = 1
PASS_THRESHOLD = 0.90

GOTO_SLIDE_TOOL = {
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
    "strict": True,
}


def _cases() -> list[dict]:
    return yaml.safe_load(FIXTURES.read_text(encoding="utf-8"))


def _chosen_slide(response) -> int | None:
    """The slide_id from the goto_slide call, or None if the model didn't jump."""
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "goto_slide":
            return block.input.get("slide_id")
    return None


def _ask(client, model: str, instructions: str, question: str):
    return client.messages.create(
        model=model,
        max_tokens=1024,
        system=instructions,
        tools=[GOTO_SLIDE_TOOL],
        thinking={"type": "adaptive"},
        # Slide routing is a shallow decision; low effort keeps the eval fast
        # and cheap without measurably hurting accuracy.
        output_config={"effort": "low"},
        messages=[
            {
                "role": "user",
                "content": (
                    f"[Currently showing slide {STARTING_SLIDE}.] {question}"
                ),
            }
        ],
    )


@pytest.mark.costly
@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["q"][:45])
def test_navigation_case(case, anthropic_client, instructions, llm_model, record_property):
    expected = case["slide"]
    response = _ask(anthropic_client, llm_model, instructions, case["q"])
    chosen = _chosen_slide(response)

    record_property("expected", expected)
    record_property("chosen", chosen)

    if expected == STARTING_SLIDE:
        # Staying put is correct when the current slide already answers it.
        assert chosen in (None, STARTING_SLIDE), (
            f"expected to stay on slide {STARTING_SLIDE} or jump to it, got {chosen}"
        )
    else:
        assert chosen == expected, (
            f"{case['q']!r}\n  expected slide {expected}, model chose {chosen}"
        )


@pytest.mark.costly
def test_navigation_accuracy(anthropic_client, instructions, llm_model, capsys):
    """Aggregate accuracy across all fixtures, with a per-case report.

    The parametrised test above tells you *which* cases fail; this one is the
    single number to put in the README.
    """
    cases = _cases()
    rows, correct = [], 0

    for case in cases:
        chosen = _chosen_slide(_ask(anthropic_client, llm_model, instructions, case["q"]))
        expected = case["slide"]
        ok = (
            chosen in (None, STARTING_SLIDE)
            if expected == STARTING_SLIDE
            else chosen == expected
        )
        correct += ok
        rows.append((ok, expected, chosen, case["q"]))

    accuracy = correct / len(cases)

    with capsys.disabled():
        print(f"\n  navigation accuracy: {correct}/{len(cases)} = {accuracy:.1%}")
        print(f"  model: {llm_model}\n")
        for ok, expected, chosen, q in rows:
            if not ok:
                print(f"    MISS  want {expected} got {chosen}  {q}")

    assert accuracy >= PASS_THRESHOLD, (
        f"navigation accuracy {accuracy:.1%} below {PASS_THRESHOLD:.0%} threshold"
    )
