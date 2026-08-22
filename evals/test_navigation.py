"""Navigation accuracy eval (FR-11).

Does the model pick the right slide for a real question? This exercises exactly the
contract the live agent depends on -- same system prompt, same tool schema --
but with no audio, so it runs in seconds and costs cents.

Runs against whichever provider LLM_PROVIDER selects, so Claude and Gemini are
compared by the same assertions rather than by feel.

Note this eval configures reasoning depth directly through the vendor SDK. The
live agent cannot do that on every provider (see README, "Model choice"), so eval
and agent latency are not directly comparable.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tool_specs import GOTO_SLIDE

FIXTURES = Path(__file__).parent / "fixtures" / "navigation.yaml"
STARTING_SLIDE = 1
PASS_THRESHOLD = 0.90

def _cases() -> list[dict]:
    return yaml.safe_load(FIXTURES.read_text(encoding="utf-8"))


def _chosen_slide(reply) -> int | None:
    """The slide_id from the goto_slide call, or None if the model didn't jump."""
    value = reply.arg("goto_slide", "slide_id")
    return int(value) if value is not None else None


def _ask(llm, question: str, instructions: str):
    return llm.ask(
        system=instructions,
        user=f"[Currently showing slide {STARTING_SLIDE}.] {question}",
        tools=[GOTO_SLIDE],
    )


@pytest.mark.costly
@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["q"][:45])
def test_navigation_case(case, llm, instructions, llm_model, record_property):
    expected = case["slide"]
    chosen = _chosen_slide(_ask(llm, case["q"], instructions))

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
def test_navigation_accuracy(llm, instructions, llm_model, capsys):
    """Aggregate accuracy across all fixtures, with a per-case report.

    The parametrised test above tells you *which* cases fail; this one is the
    single number to put in the README.
    """
    cases = _cases()
    rows, correct = [], 0

    for case in cases:
        chosen = _chosen_slide(_ask(llm, case["q"], instructions))
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
        print(f"  provider: {llm.name}  model: {llm_model}\n")
        for ok, expected, chosen, q in rows:
            if not ok:
                print(f"    MISS  want {expected} got {chosen}  {q}")

    assert accuracy >= PASS_THRESHOLD, (
        f"navigation accuracy {accuracy:.1%} below {PASS_THRESHOLD:.0%} threshold"
    )
