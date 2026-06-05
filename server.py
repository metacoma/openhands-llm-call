#!/usr/bin/env python3
"""FastAPI server wrapping openhands_llm_call.py logic."""

import io
import os
import sys
import contextlib
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import openhands_llm_call as oh

# ---------------------------------------------------------------------------
# Config / defaults
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL = os.getenv("OPENHANDS_URL", "http://localhost:3000")
DEFAULT_LLM_MODEL = os.getenv("LLM_MODEL")

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CallLMRequest(BaseModel):
    """Parameters for POST /v1/call_lm."""

    prompt: str
    llm_model: str | None = Field(
        default=None, description="LLM model override (e.g. openai/qwen3:32b)"
    )
    repo: str | None = Field(
        default=None, description="GitHub repo full name (owner/repo)"
    )
    branch: str | None = Field(default=None, description="Branch to use")
    agent_type: str = Field(default="default", description="OpenHands agent type")
    url: str | None = Field(default=None, description="OpenHands base URL override")
    api_key: str | None = Field(default=None, description="API key override")
    conversation_id: str | None = Field(
        default=None,
        description="Query existing conversation instead of creating a new one",
    )
    poll_interval: int = Field(default=10, ge=1, le=120)
    max_polls: int = Field(default=180, ge=1, le=360)
    events_limit: int = Field(default=100, ge=1, le=100)
    events_max_pages: int = Field(default=50, ge=1)
    final_fetch_delay: int = Field(default=30, ge=0)
    ignore_done_status: bool = Field(default=False)
    stop_after_first_message: bool = Field(default=False)
    no_wait: bool = Field(default=False)


class CallLMResponse(BaseModel):
    """Response from POST /v1/call_lm."""

    answer: str
    conversation_id: str | None = None
    status: str  # "completed", "no_wait", "error"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _capture(func) -> tuple[str, Any]:
    """Run *func*, capture stdout, return (captured_text, return_value)."""
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        result = func()
    return captured.getvalue(), result


def _build_prompt(user_prompt: str) -> str:
    """Apply the same suffix the CLI applies in main()."""
    return (
        f"""
{user_prompt}

Important:
- When you are done, send a final plain-text answer to the user.
- Do not leave the answer only inside a tool call, command output, file content, or code block.
- The final answer should summarize what you found or changed.
""".strip()
    )


def _resolve_url(req: CallLMRequest) -> str:
    return (req.url or os.getenv("OPENHANDS_URL") or DEFAULT_BASE_URL).rstrip("/")


def _resolve_api_key(req: CallLMRequest) -> str:
    key = req.api_key or os.getenv("OPENHANDS_API_KEY")
    if not key:
        raise HTTPException(
            status_code=400,
            detail=(
                "OPENHANDS_API_KEY environment variable or request "
                "api_key field is required"
            ),
        )
    return key


# ---------------------------------------------------------------------------
# Core execution flow
# ---------------------------------------------------------------------------


def _execute(req: CallLMRequest) -> dict[str, Any]:
    """Run the OpenHands conversation flow and return the final result dict."""
    base_url = _resolve_url(req)
    api_key = _resolve_api_key(req)

    # --- mode: existing conversation -----------------------------------------
    if req.conversation_id:
        answers = oh.collect_existing_conversation_answer(
            base_url=base_url,
            api_key=api_key,
            conversation_id=req.conversation_id,
            events_limit=req.events_limit,
            events_max_pages=req.events_max_pages,
            final_fetch_delay=req.final_fetch_delay,
            verbose_events=False,
        )
        final_answer = oh.extract_final_answer(answers)
        return {
            "answer": final_answer,
            "conversation_id": req.conversation_id,
            "status": "completed",
        }

    # --- mode: new conversation ----------------------------------------------
    if not req.llm_model and DEFAULT_LLM_MODEL:
        llm_model = DEFAULT_LLM_MODEL
    elif req.llm_model:
        llm_model = req.llm_model
    else:
        raise HTTPException(
            status_code=400,
            detail=(
                "LLM model is required: pass llm_model in request body "
                "or set LLM_MODEL env var"
            ),
        )

    prompt = _build_prompt(req.prompt)

    # Suppress CLI prints from start_v1_app_conversation
    _capture(lambda: None)  # no-op capture to verify helper works

    result = oh.start_v1_app_conversation(
        base_url=base_url,
        api_key=api_key,
        repo=req.repo,
        branch=req.branch,
        prompt=prompt,
        llm_model=llm_model,
        agent_type=req.agent_type,
    )

    task_id = (
        result.get("id")
        or result.get("task_id")
        or result.get("start_task_id")
    )
    conversation_id = (
        result.get("app_conversation_id")
        or result.get("conversation_id")
    )

    if not conversation_id:
        if not task_id:
            raise HTTPException(
                status_code=502,
                detail="OpenHands V1 response has neither conversation_id nor task id",
            )
        # Suppress poll_v1_start_task prints
        _, conversation_id = _capture(
            lambda: oh.poll_v1_start_task(
                base_url=base_url,
                api_key=api_key,
                task_id=task_id,
                interval=5,
                attempts=60,
            )
        )

    if req.no_wait:
        return {
            "answer": "",
            "conversation_id": conversation_id,
            "status": "no_wait",
        }

    # Suppress run_and_collect_message_events prints
    _, answers = _capture(
        lambda: oh.run_and_collect_message_events(
            base_url=base_url,
            api_key=api_key,
            conversation_id=conversation_id,
            poll_interval=req.poll_interval,
            max_polls=req.max_polls,
            events_limit=req.events_limit,
            events_max_pages=req.events_max_pages,
            final_fetch_delay=req.final_fetch_delay,
            verbose_events=False,
            ignore_done_status=req.ignore_done_status,
            stop_after_first_message=req.stop_after_first_message,
        )
    )

    final_answer = oh.extract_final_answer(answers)

    return {
        "answer": final_answer,
        "conversation_id": conversation_id,
        "status": "completed",
    }


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="OpenHands LLM Call Server",
    description=(
        "FastAPI wrapper around the OpenHands V1 API. "
        "Creates agent conversations and returns the final LLM answer."
    ),
)


@app.post("/v1/call_lm", response_model=CallLMResponse)
def call_lm(req: CallLMRequest) -> JSONResponse:
    """Create an OpenHands agent conversation and return the final answer."""
    try:
        result = _execute(req)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"OpenHands API error: {exc}"
        ) from exc

    return JSONResponse(
        content={
            "answer": result["answer"],
            "conversation_id": result["conversation_id"],
            "status": result["status"],
        }
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
