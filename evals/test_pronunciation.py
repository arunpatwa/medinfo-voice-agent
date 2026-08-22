"""Voice eval: pronunciation of complex medical terms (FR-11).

This is the eval the JD actually names. The loop is a round-trip:

    term -> Aura-2 TTS -> audio -> Nova-3 STT -> transcript -> compare

If the synthesised speech is intelligible, a good recogniser gets the term back.
If Aura-2 mangles "empagliflozin", the transcript shows it, and you have a
number instead of an opinion.

Two design notes:

1. Terms are wrapped in a carrier sentence rather than synthesised bare. Both
   TTS and STT behave differently on isolated words than on words in context,
   and context is what actually happens in a conversation.

2. Each term is transcribed twice -- with and without keyterm boosting -- so the
   report quantifies what the medical lexicon is buying us. That is the number
   worth arguing about in a design review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pytest

CARRIER = "The patient was prescribed {term} yesterday."
TTS_MODEL_DEFAULT = "aura-2-asteria-en"
STT_MODEL_DEFAULT = "nova-3"

# Report-only floor. Deliberately lenient: this suite exists to produce a
# number, not to gate a build on a TTS vendor's phoneme coverage.
RECALL_FLOOR = 0.60


def _normalise(text: str) -> str:
    text = text.lower().replace("-", " ")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class Result:
    term: str
    transcript_boosted: str
    transcript_plain: str

    @property
    def hit_boosted(self) -> bool:
        return _normalise(self.term) in _normalise(self.transcript_boosted)

    @property
    def hit_plain(self) -> bool:
        return _normalise(self.term) in _normalise(self.transcript_plain)

    def wer(self, transcript: str) -> float:
        import jiwer

        reference = _normalise(CARRIER.format(term=self.term))
        hypothesis = _normalise(transcript)
        if not hypothesis:
            return 1.0
        return float(jiwer.wer(reference, hypothesis))


def _synthesise(client, text: str, model: str) -> bytes:
    """Aura-2 TTS -> WAV bytes."""
    return b"".join(
        client.speak.v1.audio.generate(
            text=text,
            model=model,
            container="wav",
            encoding="linear16",
            sample_rate=24000,
        )
    )


def _transcribe(client, audio: bytes, model: str, keyterms: list[str] | None) -> str:
    kwargs = {
        "request": audio,
        "model": model,
        "smart_format": True,
        "punctuate": True,
    }
    if keyterms:
        kwargs["keyterm"] = keyterms
    response = client.listen.v1.media.transcribe_file(**kwargs)
    try:
        return response.results.channels[0].alternatives[0].transcript or ""
    except (AttributeError, IndexError, TypeError):
        return ""


@pytest.fixture(scope="module")
def roundtrip(deepgram_client, lexicon) -> list[Result]:
    import os

    tts_model = os.getenv("TTS_MODEL", TTS_MODEL_DEFAULT)
    stt_model = os.getenv("STT_MODEL", STT_MODEL_DEFAULT)

    results = []
    for term in lexicon:
        audio = _synthesise(deepgram_client, CARRIER.format(term=term), tts_model)
        results.append(
            Result(
                term=term,
                transcript_boosted=_transcribe(deepgram_client, audio, stt_model, lexicon),
                transcript_plain=_transcribe(deepgram_client, audio, stt_model, None),
            )
        )
    return results


@pytest.mark.costly
def test_pronunciation_report(roundtrip, capsys):
    """Print the per-term table and assert a lenient overall floor."""
    boosted = sum(r.hit_boosted for r in roundtrip)
    plain = sum(r.hit_plain for r in roundtrip)
    total = len(roundtrip)

    with capsys.disabled():
        print(f"\n  Pronunciation round-trip: {total} terms\n")
        print(f"  {'term':<32} {'boosted':>8} {'plain':>7}  {'WER':>6}")
        print(f"  {'-' * 32} {'-' * 8} {'-' * 7}  {'-' * 6}")
        for r in sorted(roundtrip, key=lambda x: (x.hit_boosted, x.term)):
            print(
                f"  {r.term:<32} {'PASS' if r.hit_boosted else 'MISS':>8} "
                f"{'PASS' if r.hit_plain else 'MISS':>7}  "
                f"{r.wer(r.transcript_boosted):>6.2f}"
            )
        print(
            f"\n  recall with keyterms:    {boosted}/{total} = {boosted / total:.1%}"
        )
        print(f"  recall without keyterms: {plain}/{total} = {plain / total:.1%}")
        print(f"  keyterm delta:           {(boosted - plain) / total:+.1%}\n")
        for r in roundtrip:
            if not r.hit_boosted:
                print(f"    MISS {r.term!r} -> {r.transcript_boosted!r}")

    assert boosted / total >= RECALL_FLOOR, (
        f"round-trip recall {boosted / total:.1%} below {RECALL_FLOOR:.0%} floor -- "
        "the TTS voice is likely mangling drug names"
    )


@pytest.mark.costly
def test_keyterm_boosting_does_not_hurt(roundtrip):
    """Boosting should never make recognition worse than the plain baseline.

    A regression here means the lexicon is fighting the model rather than
    helping it -- usually a sign of too many or badly chosen keyterms.
    """
    boosted = sum(r.hit_boosted for r in roundtrip)
    plain = sum(r.hit_plain for r in roundtrip)
    assert boosted >= plain, (
        f"keyterm boosting reduced recall ({boosted} vs {plain} of {len(roundtrip)})"
    )
