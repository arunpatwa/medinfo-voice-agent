"use client";

import { useCallback, useEffect, useState } from "react";
import { LiveKitRoom } from "@livekit/components-react";
import type { Deck } from "@/lib/types";
import { SessionView } from "./SessionView";

interface Connection {
  serverUrl: string;
  roomName: string;
  participantName: string;
  participantToken: string;
}

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const DECK_ID = process.env.NEXT_PUBLIC_DECK_ID ?? "metformin";

export function VoiceApp() {
  const [deck, setDeck] = useState<Deck | null>(null);
  const [conn, setConn] = useState<Connection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);

  // Load the deck up front so the first slide is on screen before the agent
  // speaks. Same JSON the agent has in its prompt, served by the control plane.
  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE}/api/deck/${DECK_ID}`)
      .then((r) => {
        if (!r.ok) throw new Error(`deck fetch failed (${r.status})`);
        return r.json();
      })
      .then((d: Deck) => {
        if (!cancelled) setDeck(d);
      })
      .catch((e: Error) =>
        setError(
          `${e.message}. Is the control-plane API running on ${API_BASE}?`,
        ),
      );
    return () => {
      cancelled = true;
    };
  }, []);

  const connect = useCallback(async () => {
    setConnecting(true);
    setError(null);
    try {
      const res = await fetch("/api/connection-details");
      const body = await res.json();
      if (!res.ok) throw new Error(body.error ?? `token request failed (${res.status})`);
      setConn(body as Connection);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setConnecting(false);
    }
  }, []);

  if (error) {
    return (
      <main className="gate">
        <div className="gate__card">
          <h1>Something is not wired up</h1>
          <p className="gate__error">{error}</p>
          <p className="gate__help">
            Check <code>.env.local</code>, then confirm the API and agent worker
            are both running. See the README for the four keys you need.
          </p>
        </div>
      </main>
    );
  }

  if (!deck) {
    return (
      <main className="gate">
        <div className="gate__card">
          <p className="gate__loading">Loading deck…</p>
        </div>
      </main>
    );
  }

  if (!conn) {
    return (
      <main className="gate">
        <div className="gate__card">
          <p className="gate__eyebrow">Medical information brief</p>
          <h1>{deck.title}</h1>
          <p className="gate__sub">{deck.subtitle}</p>
          <ol className="gate__list">
            <li>The agent walks you through six slides out loud.</li>
            <li>Ask a question and it jumps to the slide that answers it.</li>
            <li>Talk over it any time to cut it off mid-sentence.</li>
          </ol>
          <button className="gate__cta" onClick={connect} disabled={connecting}>
            {connecting ? "Connecting…" : "Start session"}
          </button>
          <p className="gate__note">{deck.disclaimer}</p>
        </div>
      </main>
    );
  }

  return (
    <LiveKitRoom
      serverUrl={conn.serverUrl}
      token={conn.participantToken}
      connect
      audio
      video={false}
      onDisconnected={() => setConn(null)}
      onError={(e) => setError(e.message)}
    >
      <SessionView deck={deck} />
    </LiveKitRoom>
  );
}
