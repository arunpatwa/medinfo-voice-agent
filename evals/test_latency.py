"""Latency budget eval (FR-11, NFR-1).

Reads the JSONL the agent's telemetry hook wrote during a real session and
asserts the p95 time-to-first-audio stays inside budget. No API calls -- this is
pure analysis of recorded runs, so it's free and fast.

Record a session first (talk to the agent for a dozen turns), then:

    .venv/bin/pytest evals/test_latency.py -s
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LATENCY_DIR = Path(os.getenv("LATENCY_DIR") or ROOT / "data" / "latency")

# NFR-1: perceived latency budget, end of user speech -> first audio out.
P95_BUDGET_MS = 1200.0
MIN_TURNS = 5

HOPS = (
    "end_of_utterance_ms",
    "transcription_ms",
    "llm_ttft_ms",
    "tts_ttfb_ms",
)


def _load_records() -> list[dict]:
    records: list[dict] = []
    for path in sorted(LATENCY_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(len(ordered) * pct))
    return ordered[idx]


@pytest.fixture(scope="module")
def records() -> list[dict]:
    found = _load_records()
    if not found:
        pytest.skip(
            f"no latency records in {LATENCY_DIR}. Record a session first: "
            "run the agent, talk to it, then re-run this suite."
        )
    return found


def test_latency_budget(records, capsys):
    ttfa = [
        r["time_to_first_audio_ms"]
        for r in records
        if isinstance(r.get("time_to_first_audio_ms"), (int, float))
    ]
    if len(ttfa) < MIN_TURNS:
        pytest.skip(f"only {len(ttfa)} usable turns, need {MIN_TURNS} for a p95")

    p50 = _percentile(ttfa, 0.50)
    p95 = _percentile(ttfa, 0.95)

    with capsys.disabled():
        print(f"\n  turns analysed: {len(ttfa)}")
        print(f"  time to first audio -- p50 {p50:.0f}ms  p95 {p95:.0f}ms  max {max(ttfa):.0f}ms")
        print(f"  budget: p95 <= {P95_BUDGET_MS:.0f}ms\n")

        print("  per-hop medians:")
        for hop in HOPS:
            vals = [r[hop] for r in records if isinstance(r.get(hop), (int, float))]
            if vals:
                print(f"    {hop:<22} {_percentile(vals, 0.50):>7.0f}ms")

        models = {r.get("model") for r in records if r.get("model")}
        if models:
            print(f"\n  models in this sample: {', '.join(sorted(models))}")
        if len(models) > 1:
            print("  (mixed models -- split the sample before drawing conclusions)")

    assert p95 <= P95_BUDGET_MS, (
        f"p95 time-to-first-audio {p95:.0f}ms exceeds the {P95_BUDGET_MS:.0f}ms budget. "
        "Check the per-hop medians above to see which leg is responsible."
    )


def test_interruptions_were_recorded(records, capsys):
    """Sanity check that the interrupt path actually fires and is observable.

    Skips rather than fails if you never interrupted during the recorded
    session -- absence of interruptions is not a defect, but it does mean FR-4
    went untested by this run.
    """
    interrupted = [r for r in records if r.get("interrupted")]
    if not interrupted:
        pytest.skip(
            "no interrupted turns in the sample -- talk over the agent during a "
            "recorded session to exercise FR-4"
        )
    with capsys.disabled():
        print(f"\n  interrupted turns: {len(interrupted)}/{len(records)}")
    assert all("turn" in r for r in interrupted)
