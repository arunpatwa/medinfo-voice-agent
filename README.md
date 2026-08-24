# MedInfo Voice Brief

A voice agent that presents a six-slide medical information brief, jumps to
whichever slide answers your question, and can be cut off mid-sentence.

**Python** agent worker on **LiveKit**, **Deepgram** STT/TTS, **Gemini or Claude**
as the brain (swappable by env var), **React/TypeScript** frontend, containerised.

A personal project for learning how real-time voice agents actually behave —
turn-taking, barge-in, tool-driven UI, and how you evaluate any of it.

**▶ [Watch the demo](https://drive.google.com/file/d/1dA2HTdcs0VIOVm3QNeEnBwxtAWSE6NJk/view?usp=sharing)** —
slide jumps mid-sentence, barge-in, adverse-event capture, and the audit trail.

---

## Run it

```bash
git clone https://github.com/arunpatwa/synthio-ai-agent.git
cd synthio-ai-agent

cp .env.example .env            # fill in the keys below
cp .env.example web/.env.local  # Next.js only reads env files in its own dir

docker compose up --build       # api :8000 · web :3000 · agent worker
open http://localhost:3000
```

### Keys

Three free keys, no card, zero LLM spend on the defaults.

| Service | Where | Free tier |
|---|---|---|
| LiveKit Cloud | [cloud.livekit.io](https://cloud.livekit.io) | 1,000 agent-min/mo |
| Deepgram (STT + TTS) | [console.deepgram.com](https://console.deepgram.com) | $200 credit, no expiry |
| Google Gemini | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | Free tier |
| Anthropic *(optional)* | [platform.claude.com](https://platform.claude.com) | Prepaid, $5 min |

Only the selected provider's LLM key is required. `LLM_PROVIDER=google|anthropic`.

> A Claude Pro/Max subscription does **not** include API access — separate billing.

### Without Docker

```bash
python -m venv .venv
.venv/bin/pip install -r agent/requirements.txt -r api/requirements.txt

.venv/bin/uvicorn src.main:app --app-dir api --port 8000   # terminal 1
.venv/bin/python agent/src/agent.py start                  # terminal 2
cd web && npm install && npm run dev                       # terminal 3
```

### Quickest check: console mode

```bash
.venv/bin/python agent/src/agent.py console
```

Talks to the agent from the terminal — no browser, no LiveKit account. Needs only
`DEEPGRAM_API_KEY` and your LLM key. No slides (nothing to push RPC to), but it
proves STT, LLM, TTS and interruption all work before you wire up anything else.

---

## Try these

| Say this | What should happen |
|---|---|
| *"Give me an overview of metformin."* | Narrates slide 1 |
| *"What about patients with reduced kidney function?"* | Jumps to slide 4 **as it starts talking** |
| Interrupt mid-answer: *"wait — stop."* | Stops within ~300 ms |
| Say *"mhm"* mid-answer | Keeps talking — a backchannel is not an interruption |
| *"I got really nauseous taking it."* | Red banner; adverse event written to the audit trail |
| *"Should I take 1000 mg twice a day?"* | Declines, redirects to a provider |

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
                             Deepgram    Gemini | Claude   Deepgram
                             Nova-3 STT   (LLM_PROVIDER)   Aura-2 TTS
```

Three processes on purpose: the agent worker is long-lived and scales on
concurrent sessions, the API is stateless and scales on requests, the web app is
a frontend. In production those are three services with three autoscaling
policies.

**How slides follow the conversation:** the model emits a `goto_slide` tool call,
the tool pushes an RPC to the browser, React re-renders — all while the spoken
answer is still streaming, so the visual lands as the voice begins.

```
deck/metformin.json   the deck — read by the agent's prompt AND the frontend
agent/src/            LiveKit worker: agent, tools, prompts, rpc, audit, telemetry
api/src/main.py       FastAPI control plane
web/                  Next.js frontend
evals/                navigation · pronunciation · guardrails · latency
docs/design-notes.md  why things are built this way, and what measuring found
```

---

## Results

Measured on `gemini-3.5-flash-lite` + Deepgram Nova-3 / Aura-2.

| Metric | Target | Measured |
|---|---|---|
| Navigation accuracy | ≥ 90% | **100%** (23/23) |
| Adverse-event capture | 4/4 | **4/4** |
| False-positive AE guard | 3/3 | **3/3** |
| Refusal compliance (LLM-judged) | 3/3 | **3/3** |
| Pronunciation recall, with keyterms | report | **96.9%** (31/32) |
| Pronunciation recall, no keyterms | baseline | 93.8% (30/32) |
| Keyterm delta | > 0 | **+3.1%** |
| p95 time-to-first-audio | ≤ 1200 ms | **3787 ms — misses budget** ([why](docs/design-notes.md#live-session-what-the-latency-budget-actually-did)) |
| Interruption truncation | prefix of full reply | **verified** (15 of 76 words retained) |

```bash
.venv/bin/pytest                                 # all four suites
.venv/bin/pytest evals/test_navigation.py -s     # with the per-case report
```

Suites skip cleanly without keys, so a fresh clone runs green in CI.

Three findings worth the click through to [design notes](docs/design-notes.md):

- **Interruption truncation verified** — a cut-off turn is recorded as a strict
  prefix of the full answer, so the model never reasons from words the user
  never heard.
- **The latency miss is inference, not distance.** Gemini terminates 28 ms away;
  TTFT is still 1424 ms on a 20-token prompt.
- **`thinking_level="LOW"` is *slower* than omitting it** on flash-lite — and
  turning it off regressed a compliance case, so it stayed on.

---

## Known limitations

- **No end-to-end voice run is automated.** The LLM and speech layers are covered
  by evals against live APIs, but the browser-to-agent path is verified by hand.
- **p95 latency misses its 1200 ms budget** (3787 ms). Diagnosed, not yet fixed —
  Gemini context caching is the next step; `cached_tokens` is 0 on every turn.
- **Two turns changed the slide but produced no speech** (2 of 11). Not
  root-caused: immediate interruption, or the model ending its turn after the
  tool call.
- **Refusal evals lean on an LLM judge**, which has its own failure modes — one
  is documented in the design notes.
- **No auth, multi-tenancy, or rate limiting** on the API. Fine for a demo.
- **Deck content is illustrative**, derived from public FDA labelling for
  metformin. Not for clinical use.

---

## License

MIT — see [LICENSE](LICENSE).
