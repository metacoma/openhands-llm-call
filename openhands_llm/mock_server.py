#!/usr/bin/env python3
"""Mock OpenHands V1 backend for testing.

Emulates the minimal subset of the OpenHands V1 API that openhands_llm_call.py
actually calls:

    POST   /api/v1/app-conversations      – create a conversation
    GET    /api/v1/app-conversations       – query conversations
    GET    /api/v1/conversation/{id}/events/search  – fetch events

The mock stores conversations in-memory and completes them after a configurable
delay (default 60 seconds).
"""

import os
import threading
import time
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse as FastAPIJSONResponse

# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------

conversations: dict[str, dict[str, Any]] = {}


def _make_mock_events(conversation_id: str) -> list[dict[str, Any]]:
    """Return a set of mock events that look like real OpenHands events."""
    answer_text = (
        "This is a mock LLM response from the test backend.\n\n"
        "The mock server simulated a 60-second LLM call and returned "
        "this answer as the final result."
    )
    return [
        {
            "id": f"evt-{conversation_id}-1",
            "kind": "MessageEvent",
            "source": "agent",
            "timestamp": time.time(),
            "llm_message": {
                "role": "assistant",
                "content": [{"type": "text", "text": answer_text}],
            },
        },
        {
            "id": f"evt-{conversation_id}-2",
            "kind": "MessageEvent",
            "source": "user",
            "timestamp": time.time(),
            "llm_message": {
                "role": "user",
                "content": [{"type": "text", "text": "(mock user prompt)"}],
            },
        },
    ]


def _schedule_completion(cid: str) -> None:
    """After MOCK_DELAY seconds, mark the conversation as completed."""
    delay = int(os.getenv("MOCK_DELAY", "60"))

    def _complete() -> None:
        conv = conversations.get(cid)
        if conv and conv["status"] == "running":
            conv["execution_status"] = "finished"
            conv["status"] = "completed"
            conv["events"] = _make_mock_events(cid)
            conv["completed_at"] = time.time()

    threading.Timer(delay, _complete).start()


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Mock OpenHands V1 Backend",
    description="Emulates the OpenHands V1 API for testing purposes.",
)


@app.post("/api/v1/app-conversations")
async def mock_create_conversation(request: Request) -> FastAPIJSONResponse:
    """Create a mock conversation."""
    body = await request.json() if await request.body() else {}

    conversation_id = f"mock-{uuid.uuid4().hex[:12]}"

    conversations[conversation_id] = {
        "id": conversation_id,
        "app_conversation_id": conversation_id,
        "conversation_id": conversation_id,
        "execution_status": "running",
        "status": "running",
        "events": [],
        "created_at": time.time(),
        "completed_at": None,
    }

    # Schedule completion after MOCK_DELAY seconds
    _schedule_completion(conversation_id)

    return FastAPIJSONResponse(
        content={
            "id": conversation_id,
            "app_conversation_id": conversation_id,
        }
    )


@app.get("/api/v1/app-conversations")
def mock_get_conversations(ids: str | None = None) -> FastAPIJSONResponse:
    """Query mock conversations by comma-separated ids."""
    if not ids:
        items = list(conversations.values())
        return FastAPIJSONResponse(content={"items": items})

    id_list = [i.strip() for i in ids.split(",") if i.strip()]
    items = [conversations[cid] for cid in id_list if cid in conversations]
    return FastAPIJSONResponse(content={"items": items})


@app.get("/api/v1/conversation/{conversation_id}/events/search")
def mock_get_events(conversation_id: str) -> FastAPIJSONResponse:
    """Return mock events for a conversation."""
    conv = conversations.get(conversation_id)
    if not conv:
        return FastAPIJSONResponse(
            content={"items": [], "next_page_id": None},
            status_code=404,
        )

    # Only return events if conversation is completed
    if conv.get("status") != "completed":
        return FastAPIJSONResponse(
            content={"items": [], "next_page_id": None},
        )

    events = conv.get("events", [])
    return FastAPIJSONResponse(
        content={
            "items": events,
            "next_page_id": None,
        }
    )


@app.get("/health")
def mock_health() -> dict[str, str]:
    return {"status": "ok", "mode": "mock"}
