#!/usr/bin/env python3
"""FastAPI server wrapping openhands_llm_call.py logic."""

import io
import logging
import os
import sys
import contextlib
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import openhands_llm_call as oh

logger = logging.getLogger("openhands-llm")

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
        description="Existing conversation ID. With read_only_existing_conversation=false (default) and a non-empty prompt, sends a new message. With read_only_existing_conversation=true, collects existing answer without sending.",
    )
    poll_interval: int = Field(default=10, ge=1, le=120)
    max_polls: int = Field(default=180, ge=1, le=360)
    events_limit: int = Field(default=100, ge=1, le=100)
    events_max_pages: int = Field(default=50, ge=1)
    final_fetch_delay: int = Field(default=30, ge=0)
    ignore_done_status: bool = Field(default=False)
    stop_after_first_message: bool = Field(default=False)
    no_wait: bool = Field(default=False)
    read_only_existing_conversation: bool = Field(
        default=False,
        description="If true, collect existing conversation answer without sending a new prompt. Default false means conversation_id + prompt sends a new message.",
    )


class CallLMResponse(BaseModel):
    """Response from POST /v1/call_lm."""

    answer: str = ""
    conversation_id: str | None = None
    task_id: str | None = None
    job_id: str | None = None
    id: str | None = None
    app_conversation_id: str | None = None
    status: str  # "completed", "running", "no_wait", "error"


class JobStatusResponse(BaseModel):
    """Response from GET /v1/jobs/{uid}."""

    conversation_id: str | None = None
    status: str  # "running", "completed", "failed", "not_found"
    answer: str = ""
    execution_status: str | None = None


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

    # --- mode: existing conversation (read-only) -----------------------------
    if req.conversation_id and req.read_only_existing_conversation:
        logger.info(
            "call_lm.existing_conversation_read_only conversation_id=%s",
            req.conversation_id,
        )
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

    # --- mode: send new message to existing conversation ---------------------
    if req.conversation_id and req.prompt:
        endpoint = f"/api/v1/app-conversations/{req.conversation_id}/send-message"
        logger.info(
            "call_lm.existing_conversation_send endpoint=%s conversation_id=%s prompt_len=%d no_wait=%s",
            endpoint,
            req.conversation_id,
            len(req.prompt),
            req.no_wait,
        )
        response = oh.send_message_to_existing_conversation(
            base_url=base_url,
            api_key=api_key,
            conversation_id=req.conversation_id,
            prompt=req.prompt,
            timeout=60,
        )
        if req.no_wait:
            return {
                "answer": "",
                "conversation_id": req.conversation_id,
                "task_id": req.conversation_id,
                "job_id": req.conversation_id,
                "status": "running",
            }

        # Poll for the new answer on the existing conversation
        answers = oh.run_and_collect_message_events(
            base_url=base_url,
            api_key=api_key,
            conversation_id=req.conversation_id,
            poll_interval=req.poll_interval,
            max_polls=req.max_polls,
            events_limit=req.events_limit,
            events_max_pages=req.events_max_pages,
            final_fetch_delay=req.final_fetch_delay,
            verbose_events=False,
            ignore_done_status=req.ignore_done_status,
            stop_after_first_message=req.stop_after_first_message,
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

    # NOTE: repo and branch are always passed as None so OpenHands
    # creates an empty/default sandbox.  Repository instructions live
    # in the prompt text.
    result = oh.start_v1_app_conversation(
        base_url=base_url,
        api_key=api_key,
        repo=None,
        branch=None,
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
# Job status helpers
# ---------------------------------------------------------------------------


def _get_job_status(uid: str, base_url: str, api_key: str) -> dict[str, Any]:
    """Check the status of a job by UID (conversation_id).

    Returns a dict with keys: conversation_id, status, answer, execution_status.
    """
    conversation = oh.get_v1_conversation(
        base_url=base_url,
        api_key=api_key,
        conversation_id=uid,
    )

    if not conversation:
        return {
            "conversation_id": uid,
            "status": "not_found",
            "answer": "",
            "execution_status": None,
        }

    # Detect raw API wrapper responses (e.g. {"items": []}) that do not
    # represent an actual conversation object.  A real conversation always
    # carries at least one of these identifying fields.
    if not conversation.get("id") and not conversation.get(
        "app_conversation_id"
    ) and not conversation.get("conversation_id"):
        return {
            "conversation_id": uid,
            "status": "not_found",
            "answer": "",
            "execution_status": None,
        }

    exec_status = conversation.get("execution_status")
    is_done = oh.conversation_is_done(conversation)

    if is_done:
        # Extract final answer from events
        events = oh.search_v1_events(
            base_url=base_url,
            api_key=api_key,
            conversation_id=uid,
            limit=100,
            max_pages=50,
        )
        answers = oh.collect_final_text_from_events(events)
        final_answer = oh.extract_final_answer([answers]) if answers else ""

        if exec_status in ("failed", "error"):
            job_status = "failed"
        else:
            job_status = "completed"

        return {
            "conversation_id": uid,
            "status": job_status,
            "answer": final_answer,
            "execution_status": exec_status,
        }

    return {
        "conversation_id": uid,
        "status": "running",
        "answer": "",
        "execution_status": exec_status,
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

# ---------------------------------------------------------------------------
# Validation error handler — logs 422 detail for debugging
# ---------------------------------------------------------------------------

SENSITIVE_KEYS = {"api_key", "authorization", "token", "password", "secret"}


def _sanitize_body_for_log(body_bytes: bytes) -> dict:
    """Parse JSON body and redact sensitive fields for logging."""
    try:
        import json
        body = json.loads(body_bytes)
        if isinstance(body, dict):
            for key in list(body.keys()):
                if key.lower() in SENSITIVE_KEYS:
                    body[key] = "***REDACTED***"
        return body
    except (json.JSONDecodeError, TypeError):
        return {"raw_preview": body_bytes[:1000].decode("utf-8", errors="replace")}


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError):
    """Log validation errors with body shape and return controlled 422."""
    body_bytes = await request.body()

    # Log the validation error with sanitized body
    logger.error(
        "request.validation_error path=%s errors=%s body_preview=%s",
        request.url.path,
        exc.errors(),
        str(_sanitize_body_for_log(body_bytes))[:1000],
    )

    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
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

    response_content = {
        "answer": result["answer"],
        "conversation_id": result["conversation_id"],
        "status": result["status"],
    }
    if result.get("task_id"):
        response_content["task_id"] = result["task_id"]
    if result.get("job_id"):
        response_content["job_id"] = result["job_id"]
    if result.get("id"):
        response_content["id"] = result["id"]
    if result.get("app_conversation_id"):
        response_content["app_conversation_id"] = result["app_conversation_id"]
    return JSONResponse(content=response_content)


def _resolve_job_url() -> str:
    """Resolve OpenHands base URL for job status checks."""
    return DEFAULT_BASE_URL.rstrip("/")


def _resolve_job_api_key() -> str:
    """Resolve API key for job status checks."""
    key = os.getenv("OPENHANDS_API_KEY")
    if not key:
        raise HTTPException(
            status_code=500,
            detail="OPENHANDS_API_KEY environment variable is required for job status checks",
        )
    return key


@app.get("/v1/jobs/{uid}", response_model=JobStatusResponse)
def get_job_status(uid: str) -> JSONResponse:
    """Check the status of an async LLM job by its UID (conversation_id).

    Returns job status and, if completed, the final answer.
    """
    base_url = _resolve_job_url()
    api_key = _resolve_job_api_key()

    try:
        job = _get_job_status(uid, base_url, api_key)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"OpenHands API error: {exc}"
        ) from exc

    return JSONResponse(
        content={
            "conversation_id": job["conversation_id"],
            "status": job["status"],
            "answer": job["answer"],
            "execution_status": job["execution_status"],
        }
    )


@app.get("/v1/jobs/{uid}/events")
def get_job_events(uid: str, limit: int = 50) -> JSONResponse:
    """Get events for a job (conversation)."""
    base_url = _resolve_job_url()
    api_key = _resolve_job_api_key()

    try:
        events = oh.search_v1_events(
            base_url=base_url,
            api_key=api_key,
            conversation_id=uid,
            limit=min(limit, 100),
            max_pages=50,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"OpenHands API error: {exc}"
        ) from exc

    return JSONResponse(
        content={
            "events": events,
            "count": len(events),
        }
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
