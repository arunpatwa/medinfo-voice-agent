"use client";

import { useEffect } from "react";
import { useRoomContext } from "@livekit/components-react";
import type {
  AdverseEventPayload,
  DeckGotoPayload,
  TelemetryPayload,
} from "@/lib/types";

type Handlers = {
  onGotoSlide: (p: DeckGotoPayload) => void;
  onAdverseEvent: (p: AdverseEventPayload) => void;
  onTelemetry: (p: TelemetryPayload) => void;
};

/** Minimal shape of what LiveKit hands an RPC handler. */
type Invocation = { payload: string };

/**
 * Registers the RPC methods the Python agent calls to drive this UI.
 *
 * The agent is the source of truth for which slide is showing — it decides via
 * a Claude tool call and pushes the result here. That is what makes the slide
 * change *while* the answer is being spoken rather than after it.
 *
 * Registration happens on mount, before the mic is published, so an opening
 * `deck.goto` from the agent can't arrive before there's a handler for it.
 */
export function useAgentRpc({
  onGotoSlide,
  onAdverseEvent,
  onTelemetry,
}: Handlers): void {
  const room = useRoomContext();

  useEffect(() => {
    if (!room) return;

    const lp = room.localParticipant;
    // `registerRpcMethod` lives on localParticipant in current livekit-client,
    // but older builds expose it on the room. Prefer the former, fall back.
    const target: {
      registerRpcMethod?: (m: string, h: (d: Invocation) => Promise<string>) => void;
      unregisterRpcMethod?: (m: string) => void;
    } =
      typeof lp?.registerRpcMethod === "function"
        ? (lp as never)
        : (room as never);

    if (typeof target.registerRpcMethod !== "function") {
      console.error("[rpc] no registerRpcMethod available on this livekit-client build");
      return;
    }

    const parse = <T,>(raw: string, label: string): T | null => {
      try {
        return JSON.parse(raw) as T;
      } catch (err) {
        console.error(`[rpc] ${label}: bad JSON payload`, raw, err);
        return null;
      }
    };

    const methods: Record<string, (d: Invocation) => Promise<string>> = {
      "deck.goto": async (data) => {
        const p = parse<DeckGotoPayload>(data.payload, "deck.goto");
        if (!p) return "error";
        onGotoSlide(p);
        return "ok";
      },
      "alert.adverseEvent": async (data) => {
        const p = parse<AdverseEventPayload>(data.payload, "alert.adverseEvent");
        if (!p) return "error";
        onAdverseEvent(p);
        return "ok";
      },
      "telemetry.turn": async (data) => {
        const p = parse<TelemetryPayload>(data.payload, "telemetry.turn");
        if (!p) return "error";
        onTelemetry(p);
        return "ok";
      },
    };

    for (const [name, handler] of Object.entries(methods)) {
      target.registerRpcMethod(name, handler);
    }

    return () => {
      if (typeof target.unregisterRpcMethod !== "function") return;
      for (const name of Object.keys(methods)) {
        target.unregisterRpcMethod(name);
      }
    };
  }, [room, onGotoSlide, onAdverseEvent, onTelemetry]);
}
