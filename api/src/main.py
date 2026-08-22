"""Control-plane API.

Deliberately a separate service from the agent worker. The two have different
scaling profiles: agent workers are long-lived and scale on concurrent voice
sessions, while this is stateless and scales on request volume. Splitting them
is what you would do in production, so it is what the demo does.

Responsibilities:
  - serve the deck to the frontend (same JSON the agent puts in its prompt)
  - expose the audit trail for a session (the compliance/explainability story)
  - accept audit records from agents that have no shared volume
  - health check for orchestrators
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

_ROOT = Path(__file__).resolve().parents[2]
DECK_DIR = Path(os.getenv("DECK_DIR") or _ROOT / "deck")
AUDIT_DIR = Path(os.getenv("AUDIT_DIR") or _ROOT / "data" / "audit")
LATENCY_DIR = Path(os.getenv("LATENCY_DIR") or _ROOT / "data" / "latency")

app = FastAPI(
    title="Synthio MedInfo Control Plane",
    description="Deck delivery and audit trail for the voice slide agent.",
    version="0.1.0",
)

# The frontend is served from a different origin in dev (3000 vs 8000).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class AuditRecord(BaseModel):
    """Loose by design -- the agent evolves its fields faster than this schema."""

    session_id: str
    ts: float
    kind: str

    model_config = {"extra": "allow"}


class HealthResponse(BaseModel):
    status: str
    deck_dir_ok: bool
    audit_dir_ok: bool
    decks: list[str] = Field(default_factory=list)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            # A torn final line is expected if we read while the agent writes.
            continue
    return records


def _safe_session_path(directory: Path, session_id: str) -> Path:
    """Resolve a session file, refusing anything that escapes the directory."""
    candidate = (directory / f"{session_id}.jsonl").resolve()
    if not str(candidate).startswith(str(directory.resolve())):
        raise HTTPException(status_code=400, detail="invalid session id")
    return candidate


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        deck_dir_ok=DECK_DIR.is_dir(),
        audit_dir_ok=AUDIT_DIR.is_dir(),
        decks=sorted(p.stem for p in DECK_DIR.glob("*.json")) if DECK_DIR.is_dir() else [],
    )


@app.get("/api/deck/{deck_id}")
def get_deck(deck_id: str) -> dict[str, Any]:
    """Serve the deck the frontend renders.

    Same file the agent renders into its system prompt, so the slides on screen
    and the content the model answers from can never drift apart.
    """
    if not deck_id.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(status_code=400, detail="invalid deck id")
    path = DECK_DIR / f"{deck_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"no deck '{deck_id}'")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/sessions")
def list_sessions() -> dict[str, Any]:
    if not AUDIT_DIR.is_dir():
        return {"sessions": []}
    sessions = sorted(
        ({"session_id": p.stem, "size_bytes": p.stat().st_size, "modified": p.stat().st_mtime}
         for p in AUDIT_DIR.glob("*.jsonl")),
        key=lambda s: s["modified"],
        reverse=True,
    )
    return {"sessions": sessions}


@app.get("/api/sessions/{session_id}/audit")
def get_audit(session_id: str, kind: str | None = None) -> dict[str, Any]:
    """Full turn-by-turn audit trail for one session.

    This is the explainability endpoint: for any answer the agent gave, you can
    see the transcript, which slide was showing, which tools fired, and whether
    the turn was cut short by an interruption.
    """
    records = _read_jsonl(_safe_session_path(AUDIT_DIR, session_id))
    if not records:
        raise HTTPException(status_code=404, detail=f"no audit trail for '{session_id}'")
    if kind:
        records = [r for r in records if r.get("kind") == kind]
    return {
        "session_id": session_id,
        "count": len(records),
        "adverse_events": sum(1 for r in records if r.get("kind") == "adverse_event"),
        "slide_changes": sum(1 for r in records if r.get("kind") == "slide_change"),
        "interruptions": sum(1 for r in records if r.get("interrupted")),
        "records": records,
    }


@app.get("/api/sessions/{session_id}/latency")
def get_latency(session_id: str) -> dict[str, Any]:
    """Per-turn latency records, used by the latency eval and the HUD."""
    records = _read_jsonl(_safe_session_path(LATENCY_DIR, session_id))
    if not records:
        raise HTTPException(status_code=404, detail=f"no latency data for '{session_id}'")
    ttfa = sorted(
        r["time_to_first_audio_ms"]
        for r in records
        if isinstance(r.get("time_to_first_audio_ms"), (int, float))
    )
    summary: dict[str, Any] = {"turns": len(records)}
    if ttfa:
        summary["p50_ms"] = ttfa[len(ttfa) // 2]
        summary["p95_ms"] = ttfa[min(len(ttfa) - 1, int(len(ttfa) * 0.95))]
        summary["max_ms"] = ttfa[-1]
    return {"session_id": session_id, "summary": summary, "records": records}


@app.post("/api/audit", status_code=202)
def post_audit(record: AuditRecord) -> dict[str, str]:
    """Ingest an audit record from an agent with no shared volume.

    Agents write locally when they can; this is the fallback path so a record is
    never simply dropped.
    """
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    path = _safe_session_path(AUDIT_DIR, record.session_id)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record.model_dump(), separators=(",", ":"), default=str) + "\n")
    return {"status": "accepted"}
