# MedInfo Voice Brief

A voice agent that presents a six-slide medical information brief, jumps to whichever
slide answers your question, and can be cut off mid-sentence.

Built for the Synthio Labs take-home. Stack matches theirs: **Python** agent worker on
**LiveKit** with **Deepgram** STT/TTS, **Claude** as the brain, **React/TypeScript**
frontend, everything containerised.

---

## Try it in 90 seconds

```bash
cp .env.example .env          # fill in four keys (see below)
cp .env.example web/.env.local

docker compose up --build     # api :8000, web :3000, agent worker
open http://localhost:3000
```

Then run this script — it exercises every requirement:

| Say this | What should happen |
|---|---|
| *"Give me an overview of metformin."* | Narrates slide 1 |
| *"What's the mechanism?"* | Jumps to slide 2 **as it starts talking**, not after |
| *"What about patients with reduced kidney function?"* | Non-linear jump straight to slide 4 |
| Interrupt mid-answer: *"wait — stop."* | Stops within ~300 ms, responds to the new input |
| Say *"mhm"* mid-answer | Keeps talking. A backchannel is not an interruption |
| *"I got really nauseous taking it."* | Red banner; adverse event written to the audit trail |
| *"Should I take 1000 mg twice a day?"* | Declines, redirects to a provider, logs the refusal |

### Keys you need

All free to start; nothing recurring. Total spend for a full build is under $10.

| Service | Where | Free allowance |
|---|---|---|
| LiveKit Cloud | [cloud.livekit.io](https://cloud.livekit.io) | Build tier: 1,000 agent-min/mo, no card |
| Deepgram (STT + TTS) | [console.deepgram.com](https://console.deepgram.com) | $200 credit, no expiry, no card |
| Anthropic | [platform.claude.com](https://platform.claude.com) | Prepaid, $5 minimum |

> A Claude Pro/Max subscription does **not** include API access — that's separate billing.

### Running without Docker

```bash
python -m venv .venv && .venv/bin/pip install -r agent/requirements.txt -r api/requirements.txt
.venv/bin/uvicorn src.main:app --app-dir api --reload --port 8000   # terminal 1
.venv/bin/python agent/src/agent.py dev                             # terminal 2
cd web && npm install && npm run dev                                # terminal 3
```

### Fastest way to hear it: console mode

```bash
.venv/bin/python agent/src/agent.py console
```

Talks to the agent straight from the terminal using your machine's mic and
speakers — no browser, no LiveKit room, no LiveKit account. Only needs
`DEEPGRAM_API_KEY` and `ANTHROPIC_API_KEY`. You lose the slides (there's no
frontend to push RPC to, so `goto_slide` calls log instead of rendering), but it's
the quickest way to check that STT, the LLM, TTS, and interruption all work before
wiring up anything else.

---

## Architecture

```
┌─────────────────────── BROWSER (Next.js + TS) ────────────────────────┐
│  SlideDeck   AgentStatePill   Transcript   AEBanner   LatencyHud      │
│      ▲             ▲                          ▲          ▲            │
│      └─────────────┴──────────────┬───────────┴──────────┘            │
│              registerRpcMethod:   │  deck.goto                        │
│                                   │  alert.adverseEvent               │
│                                   │  telemetry.turn                   │
│   LiveKit JS SDK ── mic out / agent audio in (WebRTC) ──────┐         │
└──────────┬──────────────────────────────────────────────────┼─────────┘
           │ GET /api/connection-details (mints JWT server-side)│
           │ GET /api/deck/metformin ──────┐                    │
           ▼                                ▼                   ▼
  ┌──────────────────────┐    ┌────────────────────┐    ┌────────────┐
  │ FastAPI control plane│    │ deck/metformin.json│    │ LiveKit SFU│
  │  /health             │◄───┤ single source of   │    │   (room)   │
  │  /api/deck/{id}      │    │ truth — read by    │    └─────┬──────┘
  │  /api/sessions/…     │    │ BOTH sides         │          │ audio
  │  /api/audit  (ingest)│    └─────────┬──────────┘          │
  └──────────┬───────────┘              │ into prompt         ▼
             ▼                 ┌────────┴───────────────────────────────┐
      data/audit/*.jsonl       │        PYTHON AGENT WORKER             │
      data/latency/*.jsonl     │  AgentServer / @server.rtc_session     │
             ▲                 │                                        │
             └─────────────────┤  AgentSession(                         │
                               │    stt = deepgram.STT(nova-3,+keyterm) │
                               │    tts = deepgram.TTS(aura-2)          │
                               │    turn_handling = {                   │
                               │      turn_detection: TurnDetector,     │
                               │      interruption: {adaptive},         │
                               │      preemptive_generation: on })      │
                               │                                        │
                               │  MedInfoAgent(Agent):                  │
                               │   @function_tool goto_slide ───────────┼──► RPC
                               │   @function_tool flag_adverse_event ───┼──► RPC
                               └────────────────┬───────────────────────┘
                                  ┌─────────────┼─────────────┐
                                  ▼             ▼             ▼
                             Deepgram      Anthropic      Deepgram
                             Nova-3 STT    Claude         Aura-2 TTS
```

Three processes on purpose. The agent worker is long-lived and scales on concurrent
sessions; the API is stateless and scales on request volume; the web app is a
frontend. In production those are three ECS services with three different
autoscaling policies — so they aren't one process here either.

### Repo layout

```
deck/metformin.json     the deck — read by the agent's prompt AND the frontend
agent/src/              LiveKit worker: agent.py, medinfo_agent.py, prompts.py,
                        deck.py, rpc.py, audit.py, telemetry.py
api/src/main.py         FastAPI control plane
web/                    Next.js frontend
evals/                  navigation, pronunciation, guardrail, latency suites
```

---

## How slide changes actually work

Claude decides, a tool call carries it, RPC delivers it:

1. User asks *"what about kidney patients?"*
2. Claude emits a `goto_slide(slide_id=4, reason="renal dosing")` tool call
3. The tool body calls `perform_rpc(method="deck.goto")` at the browser
4. React re-renders — **while the spoken answer is still streaming**

That ordering is the whole trick. The system prompt tells Claude to call `goto_slide`
*before* speaking, so the visual lands as the voice begins rather than trailing it.

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
would be architecture theatre. `caching="ephemeral"` on the Anthropic plugin caches
the prompt, tools, and history instead, which cuts both cost and time-to-first-token
on every turn after the first.

**Deepgram for both STT and TTS.** One vendor, one key, one credit — and Aura-2
ships healthcare-domain pronunciation covering drug names, which is exactly the
problem this deck is full of. The medical lexicon in `evals/fixtures/lexicon.txt` is
passed to Nova-3 as `keyterm` (the Nova-3 parameter — the older `keywords` is
Nova-2-only and is silently ignored, an easy thing to get wrong).

**ElevenLabs was the original pick and got dropped.** Its API free tier is 10
credits/month with no commercial rights, so it needs a paid plan to be usable at
all. Aura-2 is free under the existing Deepgram credit and better suited here.

### Model choice — an honest constraint

`livekit-plugins-anthropic` exposes `model`, `temperature`, `max_tokens`, and
`caching`, but **no `thinking` or `effort` parameter.** On Claude Opus 5 adaptive
thinking is on by default, so every voice turn thinks, and there's no way through
the plugin to lower the effort. That's latency the budget can't easily absorb.

So `LLM_MODEL` is an env var and the latency eval sweeps it:

```bash
LLM_MODEL=claude-opus-5    # default: best answers, thinking cost on every turn
LLM_MODEL=claude-haiku-4-5 # faster and cheaper; measure before deciding
```

Note the eval harness calls the Anthropic SDK directly, so it *can* set
`thinking={"type":"adaptive"}` and `effort: "low"` — meaning eval latency is not
directly comparable to live agent latency. Worth knowing before reading the numbers.

If I were taking this further, the fix is a thin custom `LLM` subclass that passes
`thinking` and `output_config` through to `messages.create`.

---

## Evals

The JD names evals twice, including *"voice evals (for pronunciation of complex
medical terms)"*. Four suites, all skipping cleanly without keys:

```bash
.venv/bin/pytest                              # everything
.venv/bin/pytest evals/test_navigation.py -s  # just navigation, with the report
```

**`test_navigation.py`** — 23 real questions → expected slide. Runs the live system
prompt and tool schema with no audio, so it's seconds and cents. Cases expecting
slide 1 accept "no jump", because not moving when the current slide already answers
the question is correct. Threshold: 90%.

**`test_pronunciation.py`** — the round-trip: term → Aura-2 TTS → audio → Nova-3 STT
→ compare. Each term is wrapped in a carrier sentence (*"The patient was prescribed
{term} yesterday"*), because both TTS and STT behave differently on isolated words
than on words in context. Every term is transcribed twice, with and without keyterm
boosting, so the report quantifies what the lexicon actually buys — and
`test_keyterm_boosting_does_not_hurt` fails if the lexicon is fighting the model
instead of helping it.

**`test_guardrails.py`** — does `flag_adverse_event` fire on a reported side effect,
and *not* fire on "what are the side effects?" (a false report is also a defect).
Plus a refusal smoke check. The refusal assertions are keyword heuristics, not
semantic judgement — a production version wants an LLM judge with a rubric.

**`test_latency.py`** — reads recorded telemetry, asserts p95 time-to-first-audio
≤ 1200 ms, prints per-hop medians so you can see which leg is at fault. Free, no API
calls. Record a session first, then run it.

### Measured numbers

| Metric | Target | Measured |
|---|---|---|
| Pronunciation recall, with keyterms | report | **96.9%** (31/32 terms) |
| Pronunciation recall, no keyterms | baseline | 93.8% (30/32) |
| Keyterm delta | > 0 | **+3.1%** |
| Navigation accuracy | ≥ 90% | _pending — needs Anthropic credit_ |
| p95 time-to-first-audio | ≤ 1200 ms | _pending — needs a recorded session_ |

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

The JD calls out *"modular and works in cloud or fully on-prem"*. Providers sit
behind the LiveKit plugin interfaces, so the swaps are config, not surgery:
Deepgram STT → `faster-whisper`, Aura-2 → Piper or Kokoro, LiveKit Cloud →
self-hosted LiveKit (it's open source, and self-hosting also drops the cloud
dependency entirely). The deck and audit trail are already plain files on a volume,
so nothing assumes a managed service.

I'd want to actually build the local-model path before claiming it works. It's a
config change by design, but untested config is a hypothesis.

---

## Known limitations

- **No live session recorded yet.** The code is verified — modules import, prompts
  render, tools are registered, `tsc` and `next build` pass clean, the API is smoke
  tested, and the latency analysis is validated against synthetic records. But no
  end-to-end voice run has happened, so the measured-numbers table above is empty
  and the truncation behaviour in particular is unverified.
- **Telemetry field names are read defensively.** `agent/src/telemetry.py` probes a
  candidate list per metric because LiveKit's metric attributes shift between
  plugin versions. Run once with `LOG_RAW_METRICS=1` to dump the real names, then
  trim the candidates.
- **Refusal evals are keyword-based**, so they can be fooled by a reply that hedges
  without actually declining.
- **Single-room, single-user.** No auth on the API, no multi-tenant deck scoping,
  no rate limiting. All fine for a demo, none of it fine for production.
- **Deck content is illustrative**, derived from public FDA labelling for metformin.
  Not for clinical use.
