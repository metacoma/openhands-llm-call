#!/usr/bin/env python3
"""Fake OpenHands LLM backend for integration testing.

Implements the minimal API that mcp_agent expects:
- POST /v1/call_lm
- GET  /v1/jobs/{uid}
- GET  /v1/jobs/{uid}/events
- GET  /health
- GET  /debug/requests  (test-only)

The fake backend is deterministic and completes immediately,
allowing the integration test to run in a few seconds.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List
from fastapi import FastAPI
from fastapi.responses import JSONResponse

logger = logging.getLogger("fake-openhands-llm")

app = FastAPI(title="Fake OpenHands LLM Backend")

# In-memory storage
_conversations: Dict[str, Dict[str, Any]] = {}
_request_log: List[Dict[str, Any]] = []
_counter = 0

_OUTPUT_ARTIFACT_MAP = {
    "scout": "scout_report",
    "architect": "architect_plan",
    "coder": "coder_report",
    "reviewer": "reviewer_report",
    "publisher": "publisher_instructions",
}

_ROLE_REPORT_TEMPLATES = {
    "scout": (
        "# Scout Report\n\n"
        "## Task Understanding\nIntegration test scout report.\n\n"
        "## Repository Facts\nTest repository.\n\n"
        "SCOUT_STATUS: COMPLETE"
    ),
    "architect": (
        "# Architect Plan\n\n"
        "## Implementation Plan\nIntegration test architect plan.\n\n"
        "ARCHITECT_STATUS: COMPLETE"
    ),
    "coder": (
        "# Coder Report\n\n"
        "## Implementation\nIntegration test coder report.\n\n"
        "CODER_STATUS: COMPLETE"
    ),
    "reviewer": (
        "# Reviewer Report\n\n"
        "## Decision\nACTION: PASS\n\n"
        "## Risk\nRISK: LOW\n\n"
        "## Summary\nReview passed.\n\n"
        "REVIEWER_STATUS: COMPLETE"
    ),
    "publisher": (
        "# Publisher Instructions\n\n"
        "## Publish Status\nPUBLISH_STATUS: READY\n\n"
        "## Repository State\nTest state.\n\n"
        "PUBLISHER_STATUS: COMPLETE"
    ),
}


def _determine_role(prompt: str) -> str:
    """Extract role name from a rendered prompt."""
    # Main role prompts start with "# Role: <Role>"
    m = re.search(
        r"#\s*Role:\s*(scout|architect|coder|reviewer|publisher)",
        prompt,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).lower()
    return "unknown"


def _is_summary_prompt(prompt: str) -> bool:
    """Check if the prompt is a summary request."""
    return "summarize your previous answer" in prompt.lower()


@app.post("/v1/call_lm")
async def call_lm(body: Dict[str, Any]) -> JSONResponse:
    global _counter
    _counter += 1

    prompt = body.get("prompt", "")
    conversation_id = body.get("conversation_id") or f"fake-{_counter}"

    # Store request for debug inspection
    _request_log.append({
        "prompt": prompt,
        "prompt_len": len(prompt),
        "conversation_id": conversation_id,
        "is_summary": _is_summary_prompt(prompt),
        "role": _determine_role(prompt),
        "no_wait": body.get("no_wait", False),
    })

    if _is_summary_prompt(prompt):
        role = _determine_role(prompt)
        output_artifact = _OUTPUT_ARTIFACT_MAP.get(role, "control_summary")

        if role == "reviewer":
            action_val: str | None = "PASS"
        else:
            action_val = None

        summary_json = json.dumps({
            "status": "completed",
            "role": role,
            "summary": f"{role.capitalize()} completed successfully.",
            "primary_artifact_name": output_artifact,
            "blocking": False,
            "risk_level": "LOW",
            "action": action_val,
            "blocking_summary": [],
        })

        _conversations[conversation_id] = {
            "conversation_id": conversation_id,
            "status": "completed",
            "answer": summary_json,
            "execution_status": "finished",
            "role": role,
            "is_summary": True,
        }
    else:
        role = _determine_role(prompt)
        answer = _ROLE_REPORT_TEMPLATES.get(
            role, f"# {role.capitalize()} Report\n\nIntegration test {role} report."
        )

        _conversations[conversation_id] = {
            "conversation_id": conversation_id,
            "status": "completed",
            "answer": answer,
            "execution_status": "finished",
            "role": role,
            "is_summary": False,
        }

    return JSONResponse(content={
        "answer": "",
        "conversation_id": conversation_id,
        "task_id": conversation_id,
        "status": "no_wait",
    })


@app.get("/v1/jobs/{uid}")
async def get_job_status(uid: str) -> JSONResponse:
    conv = _conversations.get(uid)
    if not conv:
        return JSONResponse(content={
            "conversation_id": uid,
            "status": "not_found",
            "answer": "",
            "execution_status": None,
        })

    return JSONResponse(content={
        "conversation_id": conv["conversation_id"],
        "status": conv["status"],
        "answer": conv["answer"],
        "execution_status": conv["execution_status"],
    })


@app.get("/v1/jobs/{uid}/events")
async def get_job_events(uid: str, limit: int = 50) -> JSONResponse:
    return JSONResponse(content={
        "events": [],
        "count": 0,
    })


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok", "mode": "fake-openhands-llm"}


@app.get("/debug/requests")
async def debug_requests() -> Dict[str, Any]:
    return {
        "requests": _request_log,
        "count": len(_request_log),
    }
