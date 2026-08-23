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
    # resolve_tools=False keeps this to one API call per question: navigation
    # asserts on the tool call itself, so the spoken follow-up leg adds cost
    # without adding signal. The guardrail suite does need it.
    return llm.ask(
        system=instructions,
        user=f"[Currently showing slide {STARTING_SLIDE}.] {question}",
        tools=[GOTO_SLIDE],
        resolve_tools=False,
    )


def _is_correct(expected: int, chosen: int | None) -> bool:
    if expected == STARTING_SLIDE:
        # Staying put is correct when the current slide already answers it.
        return chosen in (None, STARTING_SLIDE)
    return chosen == expected


@pytest.fixture(scope="module")
def results(llm, instructions) -> list[dict]:
    """Run every fixture question exactly once.

    Module-scoped on purpose: the per-case test and the aggregate report both
    read from this, so the suite makes N calls rather than 2N. That matters on a
    rate-limited free tier, where the duplicate sweep was most of the cost.
    """
    out = []
    for case in _cases():
        chosen = _chosen_slide(_ask(llm, case["q"], instructions))
        out.append(
            {
                "q": case["q"],
                "expected": case["slide"],
                "chosen": chosen,
                "ok": _is_correct(case["slide"], chosen),
            }
        )
    return out


@pytest.mark.costly
@pytest.mark.parametrize(
    "index", range(len(_cases())), ids=lambda i: _cases()[i]["q"][:45]
)
def test_navigation_case(index, results, record_property):
    row = results[index]
    record_property("expected", row["expected"])
    record_property("chosen", row["chosen"])
    assert row["ok"], (
        f"{row['q']!r}\n  expected slide {row['expected']}, model chose {row['chosen']}"
    )


@pytest.mark.costly
def test_navigation_accuracy(results, llm, llm_model, capsys):
    """The single number to put in the README."""
    correct = sum(r["ok"] for r in results)
    accuracy = correct / len(results)

    with capsys.disabled():
        print(f"\n  navigation accuracy: {correct}/{len(results)} = {accuracy:.1%}")
        print(f"  provider: {llm.name}  model: {llm_model}\n")
        for r in results:
            if not r["ok"]:
                print(f"    MISS  want {r['expected']} got {r['chosen']}  {r['q']}")

    assert accuracy >= PASS_THRESHOLD, (
        f"navigation accuracy {accuracy:.1%} below {PASS_THRESHOLD:.0%} threshold"
    )
