import { NextResponse } from "next/server";
import {
  AccessToken,
  RoomAgentDispatch,
  RoomConfiguration,
} from "livekit-server-sdk";

/**
 * Mints a short-lived LiveKit access token.
 *
 * Runs server-side so LIVEKIT_API_SECRET never reaches the browser.
 *
 * Note the explicit agent dispatch: a worker that registers with an
 * `agent_name` is NOT auto-dispatched into rooms. Without the RoomConfiguration
 * below, the client connects fine and then sits in an empty room forever with no
 * error — a confusing failure worth knowing about.
 */
export const revalidate = 0;

export async function GET() {
  const apiKey = process.env.LIVEKIT_API_KEY;
  const apiSecret = process.env.LIVEKIT_API_SECRET;
  const serverUrl = process.env.LIVEKIT_URL;
  const agentName = process.env.AGENT_NAME ?? "medinfo-agent";

  if (!apiKey || !apiSecret || !serverUrl) {
    return NextResponse.json(
      {
        error:
          "Missing LiveKit config. Set LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET in web/.env.local",
      },
      { status: 500 },
    );
  }

  const roomName = `medinfo-${Math.random().toString(36).slice(2, 10)}`;
  const identity = `user-${Math.random().toString(36).slice(2, 8)}`;

  const at = new AccessToken(apiKey, apiSecret, { identity, ttl: "30m" });
  at.addGrant({
    room: roomName,
    roomJoin: true,
    canPublish: true,
    canSubscribe: true,
    canPublishData: true,
  });
  at.roomConfig = new RoomConfiguration({
    agents: [new RoomAgentDispatch({ agentName })],
  });

  return NextResponse.json(
    {
      serverUrl,
      roomName,
      participantName: identity,
      participantToken: await at.toJwt(),
    },
    { headers: { "Cache-Control": "no-store" } },
  );
}
