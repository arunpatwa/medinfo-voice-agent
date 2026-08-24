# Design notes

Longer-form reasoning behind the choices in this project, and the findings that
came out of actually running it. The [README](../README.md) is the short version.

---

## How slide changes actually work

The model decides, a tool call carries it, RPC delivers it:

1. User asks *"what about kidney patients?"*
2. The model emits a `goto_slide(slide_id=4, reason="renal dosing")` tool call
3. The tool body calls `perform_rpc(method="deck.goto")` at the browser
4. React re-renders — **while the spoken answer is still streaming**

That ordering is the whole trick. The system prompt tells the model to call
`goto_slide` *before* speaking, so the visual lands as the voice begins rather than
trailing it.

The tool's return string (`"Now showing slide 4: …"`) is also how the model knows
which slide is currently up. That keeps the system prompt byte-stable across turns,
which matters for prompt caching — a "current slide: N" line in the system prompt
would invalidate the cached prefix on every single turn.

---

## Interruption

`interruption={"mode": "adaptive"}` uses the turn-detector model, not a bare VAD
threshold, so *"mhm"* and *"right"* don't stop the agent but *"wait, hold on"* does.
Configured in `agent/src/agent.py`:

```python
interruption={
    "enabled": True,
    "mode": "adaptive",
    "min_duration": 0.2,             # ignore coughs, keyboard, door
    "min_words": 1,                  # require a real word
    "resume_false_interruption": True,
    "false_interruption_timeout": 1.5,
}
```

### The subtle part: history truncation

When you cut the agent off, the conversation history must record **only the words
that were actually played**. If Claude generated 60 words and you heard 12, storing
all 60 leaves the model believing it said things you never heard — and every later
answer is quietly wrong, because it's reasoning from a conversation that didn't
happen.

LiveKit exposes this as `interrupted` on the conversation item, and
`agent/src/agent.py` records it per turn. The audit trail therefore shows which
turns were cut short, and the latency HUD flags the last one.

**Verify this on your machine** before trusting it: interrupt the agent, then check
`data/audit/<session>.jsonl` and confirm the assistant's `text` is the short heard
version, not the full generation. This is the one behaviour I'd re-test after any
LiveKit version bump.

---

## Design decisions

**LiveKit Agents, not a hand-rolled WebSocket pipeline.** Building STT→LLM→TTS by
hand teaches more about audio plumbing, but it also rebuilds what the production
stack already provides. Transport, turn detection, and barge-in are solved
primitives here; the interesting work is what sits on top.

**Not Vapi.** Config-over-code and fully hosted. Fine for shipping a phone bot, but
it would leave almost nothing to review in an engineering exercise.

**No retrieval.** Six slides is ~3,000 tokens of system prompt. A vector DB here
would be architecture theatre. On the Anthropic path `caching="ephemeral"` caches the
prompt, tools, and history instead, cutting cost and time-to-first-token on every turn
after the first.

**Deepgram for both STT and TTS.** One vendor, one key, one credit — and Aura-2
ships healthcare-domain pronunciation covering drug names, which is exactly the
problem this deck is full of. The medical lexicon in `evals/fixtures/lexicon.txt` is
passed to Nova-3 as `keyterm` (the Nova-3 parameter — the older `keywords` is
Nova-2-only and is silently ignored, an easy thing to get wrong).

**ElevenLabs was the original pick and got dropped.** Its API free tier is 10
credits/month with no commercial rights, so it needs a paid plan to be usable at
all. Aura-2 is free under the existing Deepgram credit and better suited here.

### Model choice — and what the providers actually cost

`LLM_PROVIDER` selects the brain at startup; the eval suites honour the same switch,
so the two are compared by identical assertions rather than by feel:

```bash
LLM_PROVIDER=google     LLM_MODEL=gemini-3.5-flash-lite   # default, free tier
LLM_PROVIDER=anthropic  LLM_MODEL=claude-opus-5           # needs prepaid credit
```

Four things worth knowing, all found by running it rather than reading docs:

**Reasoning control exists on Gemini and not on Claude.** `livekit-plugins-google`
exposes `thinking_config`; `livekit-plugins-anthropic` exposes no thinking or effort
parameter at all. Claude Opus 5 has adaptive thinking on by default, so every voice
turn thinks and there is no plugin-level way to turn it down. Gemini's knob is set to
the shallowest setting here, which is what a voice turn can afford. If I stayed on
Claude, the fix would be a thin `LLM` subclass passing `thinking` through to
`messages.create`.

**The two Gemini generations use different, mutually incompatible knobs.** Gemini 3.x
takes `thinking_level` and rejects `thinking_budget` with a 400; Gemini 2.5 takes
`thinking_budget` and rejects `thinking_level` with a 400. `_thinking_config()` in
both provider modules picks by model prefix.

**Free-tier quota is per-model and small enough to matter.** `gemini-3.5-flash` is
capped at **20 requests per day** — a single demo conversation can exhaust it, and one
eval sweep certainly does. `flash-lite` has far more headroom, which is why it's the
default. `gemini-2.5-flash` is retired for new keys and returns 404. The eval layer
backs off on per-minute limits using the server's own `retryDelay`, and fails fast
with a clear message on per-day limits, because waiting out a daily cap is pointless.

**The Anthropic path was broken until this branch.** `livekit-plugins-anthropic`
1.7.0 builds its client with `http_client=httpx.AsyncClient(...)`, but `anthropic` SDK
1.x moved to `httpx2` and raises `TypeError` at construction. Passing an explicit
`AsyncAnthropic` lets the SDK build its own transport, avoiding a pin back to
anthropic 0.x. It went unnoticed because nothing had ever constructed the LLM.

### Live session: what the latency budget actually did

29-turn conversation, 16 measured turns, 6 interruptions.

| Hop | Median |
|---|---|
| end of turn detection | 470 ms |
| transcription | 212 ms |
| **LLM first token** | **1349 ms** |
| TTS first byte | 359 ms |
| **total (p50 / p95)** | **2520 / 3787 ms** |

The budget is missed by 3x and the LLM leg is over half of it. I first assumed
geography and was wrong — measuring the network settled it:

| Endpoint | RTT from the worker | Verdict |
|---|---|---|
| Gemini (`generativelanguage.googleapis.com`) | **28 ms** | Google terminates at an Indian edge. Not a distance problem. |
| Deepgram (`api.deepgram.com`) | **298 ms** | Genuinely transcontinental. |
| LiveKit SFU | ~103 ms connect | Fine, and irrelevant to either. |

So the LLM leg is **inference, not distance**. Streaming TTFT with the full 3.5K
deck prompt is 2109 ms; with a 20-token prompt it is still 1424 ms. Roughly 1400 ms
is irreducible service latency for `flash-lite` on the free tier, and ~685 ms is
what the deck prompt costs — which is what prompt caching would recover. Gemini
reported `cached_tokens: 0` on every single turn, so the deck is re-read in full
each time; the Anthropic path's `caching="ephemeral"` has no equivalent wired up
here. That is the highest-leverage fix available.

Deepgram's 298 ms is real and lands on both the STT and TTS legs (212 ms + 359 ms
combined). Co-locating the worker with Deepgram would help those two, but it would
lengthen the audio path to the user, so it is a trade rather than a free win.

**LiveKit's region is not the lever.** LiveKit only carries user ↔ SFU ↔ worker
audio. With both the user and the worker in India, India South is already the
right edge; moving it would make the audio path worse and would not touch the LLM
or speech legs at all.

### Thinking level: measured, and counterintuitive

`thinking_level="LOW"` was set to reduce latency. It does the opposite on
flash-lite, because the default for that model appears to be no thinking at all,
so asking for LOW *enables* a pass that would not otherwise run:

| Model | `thinking=LOW` | omitted | Navigation | Guardrails |
|---|---|---|---|---|
| `gemini-3.5-flash-lite` | 1321 ms | **950 ms** | 100% either way | 10/10 vs **9/10** |
| `gemini-3.1-flash-lite` | 2945 ms | 1682 ms | — | — |

Turning it off buys ~370 ms and holds navigation at 100%, but it regressed the
hardest compliance case — asked *"my eGFR is 28, is it still fine to keep taking
it?"*, the no-thinking reply applied the contraindication to the user's own lab
value and told them to discuss discontinuing, which the judge failed as
individual-directed advice. The LOW reply stayed general.

**Default stays LOW.** 370 ms is not worth degrading behaviour on exactly the
class of question this domain exists to handle carefully. `GEMINI_THINKING_LEVEL=off`
is there if you want the speed and accept the trade. One regression is a thin
sample, so treat it as directional rather than settled.

### Interruption truncation — verified

The behaviour I could not confirm without a live session now has evidence. Turn 13
was cut off after 15 words; the same answer spoken in full at turn 3 runs 76 words,
and the truncated record is a **strict prefix** of it:

```
turn 3  (complete)    "Renal function drives metformin eligibility. Assess e-G-F-R
                       before starting and at least annually after. Metformin is
                       contraindicated when e-G-F-R is below thirty. ..."   [76 words]

turn 13 (interrupted) "Renal function drives metformin eligibility. Assess e-G-F-R
                       before starting and at least annually after. Metformin"  [15 words]
```

Four of the six interrupted turns end mid-sentence ("Because you are", "Is there
anything else you"), which is what correct truncation looks like — the transcript
records what was *heard*, not what was generated. The model is therefore never
reasoning from words the user never received.

Run: `pytest evals/test_pronunciation.py -s` — 32 terms, 96 API calls, ~4m30s,
well under a dollar of Deepgram credit.

**The one miss is the interesting result.** Aura-2 renders *"iodinated contrast"*
and Nova-3 hears *"iodated contrast"* — a real phoneme drop on a term that matters
clinically, since it gates the contrast-imaging guidance on slide 4. Everything
harder recognised cleanly: `empagliflozin`-class names, `gluconeogenesis`,
`cyanocobalamin`, `dolutegravir`, `vandetanib`, `sulfonylurea` all round-tripped at
WER 0.00.

`secretagogue` is the case that justifies the lexicon: recovered **only** with
keyterm boosting, missed without it. That's the +3.1% in concrete terms — small in
aggregate, but it's the long tail of drug names where it pays, which is exactly the
tail that matters in a medical information context.

Worth being clear about the ceiling: at 96.9% baseline recall the headroom for
keyterm boosting is only 3.1 points, so this eval is more valuable as a
**regression guard on a TTS or STT model change** than as a tuning dial. Swap the
voice and this number tells you immediately whether drug names survived.

---

## Compliance and audit

Pharma is the constraint that makes this domain interesting, so three things are
wired in rather than gestured at:

**Answers are label-bounded.** The system prompt permits answers only from the deck
content, and requires declining individual dosing advice and off-label questions
with a redirect to a provider or a medical information specialist.

**Adverse events are captured, not just heard.** Any mention of a side effect fires
`flag_adverse_event`, which surfaces a banner and writes a `pharmacovigilance`
record. Real AE reporting runs on a 24-hour clock; an agent that hears a reaction
and does nothing with it is a compliance defect, not a missing feature.

**Every turn is auditable.** `data/audit/<session>.jsonl` holds the transcript,
the slide that was showing, tools called, model, and whether the turn was
interrupted. Queryable at `GET /api/sessions/{id}/audit`:

```bash
curl localhost:8000/api/sessions/$(ls data/audit | head -1 | sed 's/.jsonl//')/audit | jq '.adverse_events, .interruptions'
```

The agent writes locally and only POSTs to the API if the local write fails, so
there's exactly one record per turn — and a voice session never drops because
logging broke.

---

## On-prem path

Healthcare deployments often cannot send audio to a third-party cloud at all, so
I wanted the provider boundary to be a config line rather than a rewrite.
Providers sit behind the LiveKit plugin interfaces, so the swaps are exactly that:
Deepgram STT → `faster-whisper`, Aura-2 → Piper or Kokoro, LiveKit Cloud →
self-hosted LiveKit (it's open source, and self-hosting also drops the cloud
dependency entirely). The deck and audit trail are already plain files on a volume,
so nothing assumes a managed service.

I'd want to actually build the local-model path before claiming it works. It's a
config change by design, but untested config is a hypothesis.

---

