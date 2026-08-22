/**
 * Shared shapes for the deck and the agent->browser RPC payloads.
 *
 * These mirror `deck/*.json` and `agent/src/rpc.py`. Keep them in sync: the
 * deck JSON is the single source of truth and both sides read it.
 */

export interface Slide {
  id: number;
  slug: string;
  title: string;
  topics: string[];
  bullets: string[];
  narration: string;
  citation: string;
}

export interface Deck {
  deck_id: string;
  title: string;
  subtitle: string;
  disclaimer: string;
  source: string;
  slides: Slide[];
}

/** RPC: `deck.goto` — sent when the agent decides a different slide fits. */
export interface DeckGotoPayload {
  slide_id: number;
  reason: string;
  citation: string;
}

/** RPC: `alert.adverseEvent` — pharmacovigilance flag, must be visible. */
export interface AdverseEventPayload {
  term: string;
  verbatim: string;
  severity: "serious" | "non_serious" | "unknown";
}

/** RPC: `telemetry.turn` — per-hop latency for the HUD. */
export interface TelemetryPayload {
  turn: number;
  slide?: number;
  interrupted?: boolean;
  model?: string;
  end_of_utterance_ms?: number;
  transcription_ms?: number;
  llm_ttft_ms?: number;
  tts_ttfb_ms?: number;
  time_to_first_audio_ms?: number | null;
  prompt_tokens?: number;
  completion_tokens?: number;
  cached_tokens?: number;
}
