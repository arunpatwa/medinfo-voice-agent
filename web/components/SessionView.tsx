"use client";

import { useCallback, useState } from "react";
import {
  BarVisualizer,
  RoomAudioRenderer,
  StartAudio,
  VoiceAssistantControlBar,
  useVoiceAssistant,
} from "@livekit/components-react";
import type {
  AdverseEventPayload,
  Deck,
  DeckGotoPayload,
  TelemetryPayload,
} from "@/lib/types";
import { useAgentRpc } from "./useAgentRpc";
import { SlideDeck } from "./SlideDeck";
import { AdverseEventBanner } from "./AdverseEventBanner";
import { LatencyHud } from "./LatencyHud";

const STATE_COPY: Record<string, string> = {
  initializing: "connecting",
  listening: "listening",
  thinking: "thinking",
  speaking: "speaking",
  disconnected: "disconnected",
};

export function SessionView({ deck }: { deck: Deck }) {
  const [slide, setSlide] = useState(1);
  const [reason, setReason] = useState("");
  const [events, setEvents] = useState<AdverseEventPayload[]>([]);
  const [telemetry, setTelemetry] = useState<TelemetryPayload | null>(null);
  const [history, setHistory] = useState<TelemetryPayload[]>([]);

  const { state, audioTrack, agentTranscriptions } = useVoiceAssistant();

  const onGotoSlide = useCallback((p: DeckGotoPayload) => {
    setSlide(p.slide_id);
    setReason(p.reason);
  }, []);

  const onAdverseEvent = useCallback((p: AdverseEventPayload) => {
    setEvents((prev) => [...prev, p]);
  }, []);

  const onTelemetry = useCallback((p: TelemetryPayload) => {
    setTelemetry(p);
    setHistory((prev) => [...prev, p]);
  }, []);

  useAgentRpc({ onGotoSlide, onAdverseEvent, onTelemetry });

  const dismiss = useCallback((index: number) => {
    setEvents((prev) => prev.filter((_, i) => i !== index));
  }, []);

  // Agent transcript, newest last. Interruptions show up here naturally as
  // short, cut-off assistant turns.
  const lines = (agentTranscriptions ?? []).slice(-3);

  return (
    <div className="app">
      <AdverseEventBanner events={events} onDismiss={dismiss} />

      <div className="app__main">
        <SlideDeck deck={deck} currentSlide={slide} lastReason={reason} />

        <div className="app__side">
          <div className="statecard">
            <span className={`statecard__pill is-${state}`}>
              {STATE_COPY[state] ?? state}
            </span>
            <BarVisualizer
              state={state}
              trackRef={audioTrack}
              barCount={7}
              className="statecard__viz"
            />
            <p className="statecard__hint">
              Ask a question any time — you can talk over the agent to cut it off.
            </p>
          </div>

          <LatencyHud latest={telemetry} history={history} />
        </div>
      </div>

      <div className="app__transcript">
        {lines.length === 0 ? (
          <p className="transcript__empty">Waiting for the agent…</p>
        ) : (
          lines.map((t) => (
            <p key={t.id} className="transcript__line">
              {t.text}
            </p>
          ))
        )}
      </div>

      <div className="app__bar">
        <VoiceAssistantControlBar />
        <p className="app__disclaimer">{deck.disclaimer}</p>
      </div>

      {/* Plays the agent's audio. Without this the room connects silently. */}
      <RoomAudioRenderer />
      <StartAudio label="Enable audio" />
    </div>
  );
}
