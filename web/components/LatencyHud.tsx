"use client";

import type { TelemetryPayload } from "@/lib/types";

interface Props {
  latest: TelemetryPayload | null;
  history: TelemetryPayload[];
}

const BUDGET_MS = 1200; // NFR-1 p95 target for time-to-first-audio.

function ms(v: number | null | undefined): string {
  return typeof v === "number" ? `${Math.round(v)}` : "—";
}

function p95(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))];
}

/**
 * Per-hop latency for the last turn, plus a running p95.
 *
 * You cannot improve voice UX you can't measure — the whole point of showing
 * this is that time-to-first-audio is the number the user actually feels, and
 * it's the sum of four separate hops that each need watching.
 */
export function LatencyHud({ latest, history }: Props) {
  const ttfaValues = history
    .map((h) => h.time_to_first_audio_ms)
    .filter((v): v is number => typeof v === "number");
  const running = p95(ttfaValues);
  const total = latest?.time_to_first_audio_ms ?? null;
  const overBudget = typeof total === "number" && total > BUDGET_MS;

  const hops: Array<[string, number | undefined]> = [
    ["end of turn", latest?.end_of_utterance_ms],
    ["transcribe", latest?.transcription_ms],
    ["LLM first token", latest?.llm_ttft_ms],
    ["TTS first byte", latest?.tts_ttfb_ms],
  ];

  return (
    <aside className="hud">
      <div className="hud__head">
        <span className="hud__label">latency</span>
        {latest?.model && <span className="hud__model">{latest.model}</span>}
      </div>

      <div className={`hud__total${overBudget ? " is-over" : ""}`}>
        <strong>{ms(total)}</strong>
        <span className="hud__unit">ms to first audio</span>
      </div>

      <dl className="hud__hops">
        {hops.map(([label, value]) => (
          <div key={label} className="hud__hop">
            <dt>{label}</dt>
            <dd>{ms(value)}</dd>
          </div>
        ))}
      </dl>

      <div className="hud__foot">
        <span>
          p95 {ms(running)} / {BUDGET_MS} budget
        </span>
        <span>{ttfaValues.length} turns</span>
      </div>

      {latest?.interrupted && (
        <p className="hud__flag">last turn was interrupted — reply truncated</p>
      )}
      {typeof latest?.cached_tokens === "number" && latest.cached_tokens > 0 && (
        <p className="hud__cache">{latest.cached_tokens} prompt tokens served from cache</p>
      )}
    </aside>
  );
}
