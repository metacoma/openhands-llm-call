#!/usr/bin/env python3
"""MCP server that proxies to the OpenHands LLM Call FastAPI server.

Supports long-running OpenHands tasks via a non-blocking start + polling
pattern.  Task state is persisted as JSON files under a configurable
directory so that repeated MCP calls and process restarts do not lose data.
"""

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import requests
import uvicorn
from mcp.server.fastmcp import FastMCP

from .task_store import TaskStore
from . import role_tools as _role_tools

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("openhands-mcp")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

OPENHANDS_URL = os.getenv("OPENHANDS_URL", "http://localhost:8000").rstrip("/")

# MCP bind host/port – configurable via env, defaults to 127.0.0.1 for
# local development and 0.0.0.0 when running in Docker.
MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "8000"))

# FastMCP host/port – configurable via FASTMCP_* env vars, falling back
# to MCP_* vars.  Passed to the FastMCP constructor when supported.
_fastmcp_host = os.getenv("FASTMCP_HOST", MCP_HOST)
_fastmcp_port = int(os.getenv("FASTMCP_PORT", str(MCP_PORT)))

# Try to pass host/port to FastMCP constructor if supported.
# The installed mcp>=1.0.0 may or may not accept these parameters.
_fastmcp_kwargs: dict[str, Any] = {
    "host": _fastmcp_host,
    "port": _fastmcp_port,
}

try:
    MCP = FastMCP(
        "openhands-llm-mcp",
        **_fastmcp_kwargs,
        instructions="MCP server that wraps the OpenHands LLM Call FastAPI endpoints",
    )
    logger.info(
        "FastMCP initialized with host=%s port=%s",
        _fastmcp_host, _fastmcp_port,
    )
except TypeError:
    # FastMCP constructor does not accept host/port — fall back to defaults.
    # Binding is still controlled by uvicorn.run() below.
    MCP = FastMCP(
        "openhands-llm-mcp",
        instructions="MCP server that wraps the OpenHands LLM Call FastAPI endpoints",
    )
    logger.info(
        "FastMCP constructor does not accept host/port; "
        "binding controlled by uvicorn.run(host=%s, port=%s)",
        MCP_HOST, MCP_PORT,
    )

# Long-running task defaults
OPENHANDS_POLL_INTERVAL = int(
    os.getenv("OPENHANDS_POLL_INTERVAL_SECONDS", "10")
)
OPENHANDS_MAX_RUNTIME = int(
    os.getenv("OPENHANDS_MAX_RUNTIME_SECONDS", "7200")
)
OPENHANDS_REQUEST_TIMEOUT = int(
    os.getenv("OPENHANDS_REQUEST_TIMEOUT_SECONDS", "60")
)
# Module-level task store (created once on first tool call)
_store: TaskStore | None = None


def _get_store() -> TaskStore:
    global _store
    if _store is None:
        state_dir = os.getenv(
            "OPENHANDS_STATE_DIR", "/tmp/openhands-llm-call-state"
        )
        _store = TaskStore(state_dir)
    return _store


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Helper: start a conversation via FastAPI in non-blocking mode
# ---------------------------------------------------------------------------


def _start_conversation_on_fastapi(
    prompt: str,
    api_key: str,
    llm_model: str | None = None,
    repo: str | None = None,
    branch: str | None = None,
    agent_type: str = "default",
    url: str | None = None,
    max_polls: int | None = None,
) -> dict[str, Any]:
    """POST /v1/call_lm with no_wait=True and return the response dict.

    Parameters
    ----------
    max_polls :
        Optional per-request max poll count.  If not provided, falls
        back to the global ``OPENHANDS_MAX_RUNTIME // OPENHANDS_POLL_INTERVAL``.
    repo :
        Deprecated.  Kept for backward compatibility but never included
        in the payload so OpenHands creates an empty/default sandbox.
    branch :
        Deprecated.  Kept for backward compatibility but never included
        in the payload.
    """
    base = (url or OPENHANDS_URL).rstrip("/")
    payload: dict[str, Any] = {
        "prompt": prompt,
        "api_key": api_key,
        "no_wait": True,
        "poll_interval": OPENHANDS_POLL_INTERVAL,
        "max_polls": max_polls or (
            OPENHANDS_MAX_RUNTIME // OPENHANDS_POLL_INTERVAL
        ),
    }
    if llm_model:
        payload["llm_model"] = llm_model
    # NOTE: repo and branch are intentionally NOT included in the
    # payload.  Repository instructions live in the prompt text so
    # OpenHands creates an empty/default sandbox with no selected
    # repository metadata.
    if agent_type:
        payload["agent_type"] = agent_type

    resp = requests.post(
        f"{base}/v1/call_lm",
        json=payload,
        timeout=OPENHANDS_REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------


@MCP.tool()
def openhands_start_task(
    prompt: str,
    api_key: str,
    llm_model: str | None = None,
    repo: str | None = None,
    branch: str | None = None,
    agent_type: str = "default",
    url: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """Start a new OpenHands task and return a task_id for polling.

    This tool returns immediately (non-blocking) so that long-running
    OpenHands jobs (30-50 min) do not block the MCP caller.

    Args:
        prompt: The task prompt for the agent.
        api_key: OpenHands API key.
        llm_model: LLM model override (e.g. openai/qwen3:32b).
        repo: Deprecated. Kept for backward compatibility but no longer
            passed to OpenHands as selected-repository metadata.
        branch: Deprecated. Kept for backward compatibility but no longer
            passed to OpenHands.
        agent_type: OpenHands agent type (default: 'default').
        url: OpenHands LLM base URL override.
        idempotency_key: Optional stable key to deduplicate retried calls.

    Returns:
        A dict with task_id, conversation_id, status, and a message
        instructing the caller to poll with openhands_get_task_status.

    Example::

        {
            "task_id": "abc123",
            "conversation_id": "conv-xyz",
            "status": "running",
            "created_at": "2025-01-01T00:00:00+00:00",
            "message": "Task started. Poll with openhands_get_task_status."
        }
    """
    store = _get_store()

    # Idempotency: if a task with the same key already exists and is
    # terminal, return it; if it is still running, return the existing
    # record so the caller does not get a duplicate job.
    if idempotency_key:
        existing = store.find_by_idempotency_key(idempotency_key)
        if existing:
            status = existing.get("status", "unknown")
            if status in ("completed", "failed", "cancelled", "timeout"):
                logger.info(
                    "Idempotency key %s matched terminal task %s; "
                    "returning existing task",
                    idempotency_key,
                    existing["task_id"],
                )
                return {
                    "task_id": existing["task_id"],
                    "conversation_id": existing.get("conversation_id"),
                    "status": status,
                    "created_at": existing.get("created_at"),
                    "message": (
                        f"Idempotent match: task {existing['task_id']} "
                        f"already completed with status '{status}'."
                    ),
                }
            logger.info(
                "Idempotency key %s matched running task %s; "
                "returning existing task to avoid duplicates",
                idempotency_key,
                existing["task_id"],
            )
            return {
                "task_id": existing["task_id"],
                "conversation_id": existing.get("conversation_id"),
                "status": status,
                "created_at": existing.get("created_at"),
                "message": (
                    f"Idempotent match: task {existing['task_id']} "
                    f"is already {status}. Poll for result."
                ),
            }

    # Start conversation on FastAPI (non-blocking).
    try:
        logger.info("Starting OpenHands conversation (no_wait=True)")
        result = _start_conversation_on_fastapi(
            prompt=prompt,
            api_key=api_key,
            llm_model=llm_model,
            repo=repo,
            branch=branch,
            agent_type=agent_type,
            url=url,
        )
    except requests.exceptions.Timeout as exc:
        logger.error("Timeout starting OpenHands conversation: %s", exc)
        return {
            "status": "failed",
            "error": {
                "type": "RequestTimeout",
                "message": f"Timeout calling FastAPI server: {exc}",
                "retryable": True,
            },
        }
    except requests.exceptions.ConnectionError as exc:
        logger.error("Connection error starting OpenHands conversation: %s", exc)
        return {
            "status": "failed",
            "error": {
                "type": "ConnectionError",
                "message": f"Cannot reach FastAPI server: {exc}",
                "retryable": True,
            },
        }
    except requests.exceptions.HTTPError as exc:
        logger.error("HTTP error starting OpenHands conversation: %s", exc)
        return {
            "status": "failed",
            "error": {
                "type": "HTTPError",
                "message": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}",
                "retryable": exc.response.status_code < 500,
            },
        }
    except Exception as exc:
        logger.error("Unexpected error starting OpenHands conversation: %s", exc)
        return {
            "status": "failed",
            "error": {
                "type": "UnexpectedError",
                "message": str(exc),
                "retryable": False,
            },
        }

    conversation_id = (
        result.get("conversation_id")
        or result.get("id")
        or result.get("app_conversation_id")
    )

    if not conversation_id:
        return {
            "status": "failed",
            "error": {
                "type": "MissingConversationId",
                "message": (
                    "FastAPI response did not include a conversation_id. "
                    f"Raw response: {json.dumps(result, ensure_ascii=False)[:500]}"
                ),
                "retryable": False,
            },
        }

    # Persist task record.
    task_record = store.create_task(
        conversation_id=conversation_id,
        prompt=prompt,
        idempotency_key=idempotency_key,
    )
    # Update conversation_id from the FastAPI response.
    store.update_task(task_record["task_id"], conversation_id=conversation_id)

    logger.info(
        "Task started: task_id=%s conversation_id=%s",
        task_record["task_id"],
        conversation_id,
    )

    return {
        "task_id": task_record["task_id"],
        "conversation_id": conversation_id,
        "status": "running",
        "created_at": task_record["created_at"],
        "message": "Task started. Poll with openhands_get_task_status.",
    }


@MCP.tool()
def openhands_get_task_status(
    task_id: Any,
    url: str | None = None,
) -> dict:
    """Get the status of a previously started OpenHands task.

    If the task is still running, this tool polls the FastAPI server
    to refresh the status.

    Args:
        task_id: The task_id returned by openhands_start_task.
        url: OpenHands LLM base URL override.

    Returns:
        A dict with task_id, conversation_id, status, duration_seconds,
        and a progress_hint.

    Example::

        {
            "task_id": "abc123",
            "conversation_id": "conv-xyz",
            "status": "running",
            "created_at": "2025-01-01T00:00:00+00:00",
            "updated_at": "2025-01-01T00:30:00+00:00",
            "duration_seconds": 1800,
            "last_event": "Agent is working on the task",
            "progress_hint": "OpenHands is still working"
        }
    """
    normalized_task_id = _normalize_str_arg(task_id)
    store = _get_store()
    task = store.get_task(normalized_task_id)

    if task is None:
        return {
            "status": "failed",
            "error": {
                "type": "UnknownTaskId",
                "message": f"No task found with task_id='{normalized_task_id}'.",
                "retryable": False,
            },
        }

    status = task.get("status", "unknown")
    conversation_id = task.get("conversation_id")

    # Terminal tasks: return cached state.
    if status in ("completed", "failed", "cancelled", "timeout"):
        result = task.get("result")
        if isinstance(result, dict):
            answer = result.get("answer", "")
        elif isinstance(result, str):
            answer = result
        else:
            answer = ""
        return {
            "task_id": normalized_task_id,
            "conversation_id": conversation_id,
            "status": status,
            "created_at": task.get("created_at"),
            "updated_at": task.get("updated_at"),
            "duration_seconds": _duration_seconds(
                task.get("created_at"), task.get("updated_at")
            ),
            "answer": answer,
        }

    # Running / queued: poll FastAPI for latest status.
    if not conversation_id:
        store.update_task(normalized_task_id, status="unknown", updated_at=_utcnow_iso())
        return {
            "task_id": normalized_task_id,
            "conversation_id": None,
            "status": "unknown",
            "created_at": task.get("created_at"),
            "updated_at": task.get("updated_at"),
            "duration_seconds": _duration_seconds(
                task.get("created_at"), task.get("updated_at")
            ),
            "progress_hint": "No conversation_id yet; waiting for OpenHands",
        }

    base = (url or OPENHANDS_URL).rstrip("/")
    try:
        resp = requests.get(
            f"{base}/v1/jobs/{conversation_id}",
            timeout=OPENHANDS_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        job_data = resp.json()
    except requests.exceptions.Timeout as exc:
        logger.warning(
            "Timeout polling job %s status: %s", conversation_id, exc
        )
        return {
            "task_id": task_id,
            "conversation_id": conversation_id,
            "status": status,
            "created_at": task.get("created_at"),
            "updated_at": task.get("updated_at"),
            "duration_seconds": _duration_seconds(
                task.get("created_at"), task.get("updated_at")
            ),
            "progress_hint": "Polling timed out; task may still be running.",
            "last_poll_error": str(exc),
        }
    except requests.exceptions.ConnectionError as exc:
        logger.warning(
            "Connection error polling job %s status: %s", conversation_id, exc
        )
        return {
            "task_id": normalized_task_id,
            "conversation_id": conversation_id,
            "status": status,
            "created_at": task.get("created_at"),
            "updated_at": task.get("updated_at"),
            "duration_seconds": _duration_seconds(
                task.get("created_at"), task.get("updated_at")
            ),
            "progress_hint": "Cannot reach server; task may still be running.",
            "last_poll_error": str(exc),
        }
    except Exception as exc:
        logger.warning(
            "Error polling job %s status: %s", conversation_id, exc
        )
        return {
            "task_id": normalized_task_id,
            "conversation_id": conversation_id,
            "status": status,
            "created_at": task.get("created_at"),
            "updated_at": task.get("updated_at"),
            "duration_seconds": _duration_seconds(
                task.get("created_at"), task.get("updated_at")
            ),
            "progress_hint": f"Poll error: {exc}",
            "last_poll_error": str(exc),
        }

    api_status = job_data.get("status", "unknown")
    exec_status = job_data.get("execution_status")

    # Map FastAPI status to our status model.
    if api_status == "completed" or exec_status in ("finished", "success"):
        new_status = "completed"
    elif api_status == "failed" or exec_status in ("failed", "error"):
        new_status = "failed"
    elif api_status == "not_found":
        new_status = "unknown"
    else:
        new_status = "running"

    # Update local store.
    update_fields: dict[str, Any] = {
        "status": new_status,
        "last_polled_at": _utcnow_iso(),
        "updated_at": _utcnow_iso(),
    }
    answer_for_return = ""
    if new_status == "completed":
        answer = job_data.get("answer", "")
        answer_for_return = answer
        update_fields["result"] = {
            "answer": answer,
            "completed_at": _utcnow_iso(),
            "duration_seconds": _duration_seconds(
                task.get("created_at"), _utcnow_iso()
            ),
        }
    elif new_status == "failed":
        update_fields["error"] = {
            "type": "OpenHandsApiError",
            "message": f"Task failed with execution_status={exec_status}",
            "retryable": False,
        }

    store.update_task(normalized_task_id, **update_fields)

    # Build progress hint.
    progress_hints = {
        "running": "OpenHands is still working",
        "queued": "Task is queued in OpenHands",
        "completed": "Task completed successfully",
        "failed": "Task failed — see error field for details",
        "unknown": "Unable to determine task status",
    }

    return {
        "task_id": normalized_task_id,
        "conversation_id": conversation_id,
        "status": new_status,
        "created_at": task.get("created_at"),
        "updated_at": update_fields.get("updated_at", task.get("updated_at")),
        "duration_seconds": _duration_seconds(
            task.get("created_at"),
            update_fields.get("updated_at", task.get("updated_at")),
        ),
        "execution_status": exec_status,
        "answer": answer_for_return,
        "last_event": job_data.get("answer", "")[:200] if new_status == "completed" else None,
        "progress_hint": progress_hints.get(new_status, "Unknown"),
    }


@MCP.tool()
def openhands_get_task_result(
    task_id: Any,
    url: str | None = None,
    force_refresh: bool = False,
) -> dict:
    """Get the final result / answer of a completed OpenHands task.

    Args:
        task_id: The task_id returned by openhands_start_task.
        url: OpenHands LLM base URL override.
        force_refresh: If True and the cached answer is empty, re-fetch
            from the OpenHands FastAPI backend instead of returning the
            cached empty answer.  This bypasses stale cached results
            when the LLM final answer may not have been captured on
            the first fetch.

    Returns:
        A dict with task_id, conversation_id, status, answer, and
        completed_at.

    Example::

        {
            "task_id": "abc123",
            "conversation_id": "conv-xyz",
            "status": "completed",
            "answer": "The final answer text...",
            "completed_at": "2025-01-01T01:00:00+00:00",
            "duration_seconds": 3600
        }
    """
    normalized_task_id = _normalize_str_arg(task_id)
    store = _get_store()
    task = store.get_task(normalized_task_id)

    if task is None:
        return {
            "status": "failed",
            "error": {
                "type": "UnknownTaskId",
                "message": f"No task found with task_id='{normalized_task_id}'.",
                "retryable": False,
            },
        }

    status = task.get("status", "unknown")
    conversation_id = task.get("conversation_id")

    # Already completed: return cached result.
    if status == "completed":
        result = task.get("result") or {}
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                result = {"answer": result}
        cached_answer = (
            result.get("answer", "") if isinstance(result, dict) else result
        )

        # If force_refresh is requested and the cached answer is empty,
        # re-fetch from FastAPI to avoid permanently caching empty answers.
        if force_refresh and not cached_answer.strip():
            if not conversation_id:
                return {
                    "task_id": normalized_task_id,
                    "conversation_id": conversation_id,
                    "status": "completed",
                    "answer": "",
                    "completed_at": None,
                    "duration_seconds": None,
                    "message": "No conversation_id; cannot force-refresh.",
                }

            base = (url or OPENHANDS_URL).rstrip("/")
            try:
                resp = requests.get(
                    f"{base}/v1/jobs/{conversation_id}",
                    timeout=OPENHANDS_REQUEST_TIMEOUT,
                )
                resp.raise_for_status()
                job_data = resp.json()
            except Exception as exc:
                logger.warning(
                    "Error force-refreshing result for task %s: %s",
                    normalized_task_id,
                    exc,
                )
                return {
                    "task_id": normalized_task_id,
                    "conversation_id": conversation_id,
                    "status": "completed",
                    "answer": cached_answer,
                    "completed_at": result.get("completed_at") if isinstance(result, dict) else None,
                    "duration_seconds": result.get("duration_seconds") if isinstance(result, dict) else None,
                    "force_refresh_error": str(exc),
                }

            api_status = job_data.get("status", "unknown")
            fresh_answer = job_data.get("answer", "")

            if api_status == "completed" or job_data.get("execution_status") in (
                "finished",
                "success",
            ):
                store.update_task(
                    normalized_task_id,
                    status="completed",
                    result={
                        "answer": fresh_answer,
                        "completed_at": _utcnow_iso(),
                        "duration_seconds": _duration_seconds(
                            task.get("created_at"), _utcnow_iso()
                        ),
                    },
                    updated_at=_utcnow_iso(),
                    last_polled_at=_utcnow_iso(),
                )
                return {
                    "task_id": normalized_task_id,
                    "conversation_id": conversation_id,
                    "status": "completed",
                    "answer": fresh_answer,
                    "completed_at": _utcnow_iso(),
                    "duration_seconds": _duration_seconds(
                        task.get("created_at"), _utcnow_iso()
                    ),
                }

            # API still shows non-completed; return cached empty answer
            return {
                "task_id": normalized_task_id,
                "conversation_id": conversation_id,
                "status": "completed",
                "answer": cached_answer,
                "completed_at": result.get("completed_at") if isinstance(result, dict) else None,
                "duration_seconds": result.get("duration_seconds") if isinstance(result, dict) else None,
                "force_refresh_status": api_status,
            }

        return {
            "task_id": normalized_task_id,
            "conversation_id": conversation_id,
            "status": "completed",
            "answer": cached_answer,
            "completed_at": result.get("completed_at") if isinstance(result, dict) else None,
            "duration_seconds": result.get("duration_seconds") if isinstance(result, dict) else None,
        }

    # Terminal non-completed: return error.
    if status in ("failed", "cancelled", "timeout"):
        error = task.get("error") or {}
        if isinstance(error, str):
            error = {"type": "TaskError", "message": error}
        return {
            "task_id": normalized_task_id,
            "conversation_id": conversation_id,
            "status": status,
            "error": error,
        }

    # Still running: try to fetch latest answer from FastAPI.
    if not conversation_id:
        return {
            "task_id": normalized_task_id,
            "conversation_id": None,
            "status": status,
            "message": "No conversation_id yet; task may still be starting.",
        }

    base = (url or OPENHANDS_URL).rstrip("/")
    try:
        resp = requests.get(
            f"{base}/v1/jobs/{conversation_id}",
            timeout=OPENHANDS_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        job_data = resp.json()
    except Exception as exc:
        logger.warning("Error fetching result for task %s: %s", normalized_task_id, exc)
        return {
            "task_id": normalized_task_id,
            "conversation_id": conversation_id,
            "status": status,
            "message": "Still running; could not fetch latest result.",
            "last_poll_error": str(exc),
        }

    api_status = job_data.get("status", "unknown")
    answer = job_data.get("answer", "")

    if api_status == "completed" or job_data.get("execution_status") in (
        "finished",
        "success",
    ):
        store.update_task(
            normalized_task_id,
            status="completed",
            result={
                "answer": answer,
                "completed_at": _utcnow_iso(),
                "duration_seconds": _duration_seconds(
                    task.get("created_at"), _utcnow_iso()
                ),
            },
            updated_at=_utcnow_iso(),
            last_polled_at=_utcnow_iso(),
        )
        return {
            "task_id": normalized_task_id,
            "conversation_id": conversation_id,
            "status": "completed",
            "answer": answer,
            "completed_at": _utcnow_iso(),
            "duration_seconds": _duration_seconds(
                task.get("created_at"), _utcnow_iso()
            ),
        }

    return {
        "task_id": normalized_task_id,
        "conversation_id": conversation_id,
        "status": "running",
        "message": "Task is still running. Poll again later.",
    }


@MCP.tool()
def openhands_get_task_events(
    task_id: Any,
    limit: Any = 50,
    url: str | None = None,
) -> dict:
    """Get events (logs) for a previously started OpenHands task.

    Events are fetched from the FastAPI server on each call; they are
    not cached locally to keep state small.

    Args:
        task_id: The task_id returned by openhands_start_task.
        limit: Maximum number of events to return (max 100).
        url: OpenHands LLM base URL override.

    Returns:
        A dict with task_id, events list, and count.

    Example::

        {
            "task_id": "abc123",
            "events": [...],
            "count": 10
        }
    """
    normalized_task_id = _normalize_str_arg(task_id)
    normalized_limit = _normalize_int_arg(limit, default=50)
    store = _get_store()
    task = store.get_task(normalized_task_id)

    if task is None:
        return {
            "status": "failed",
            "error": {
                "type": "UnknownTaskId",
                "message": f"No task found with task_id='{normalized_task_id}'.",
                "retryable": False,
            },
        }

    conversation_id = task.get("conversation_id")
    if not conversation_id:
        return {
            "task_id": normalized_task_id,
            "events": [],
            "count": 0,
            "message": "No conversation_id available for this task.",
        }

    base = (url or OPENHANDS_URL).rstrip("/")
    try:
        resp = requests.get(
            f"{base}/v1/jobs/{conversation_id}/events",
            params={"limit": min(max(normalized_limit, 1), 100)},
            timeout=OPENHANDS_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.Timeout as exc:
        logger.warning("Timeout fetching events for task %s: %s", normalized_task_id, exc)
        return {
            "task_id": normalized_task_id,
            "events": [],
            "count": 0,
            "error": {
                "type": "RequestTimeout",
                "message": f"Timeout fetching events: {exc}",
                "retryable": True,
            },
        }
    except requests.exceptions.ConnectionError as exc:
        logger.warning(
            "Connection error fetching events for task %s: %s", normalized_task_id, exc
        )
        return {
            "task_id": normalized_task_id,
            "events": [],
            "count": 0,
            "error": {
                "type": "ConnectionError",
                "message": f"Cannot reach server: {exc}",
                "retryable": True,
            },
        }
    except Exception as exc:
        logger.warning("Error fetching events for task %s: %s", normalized_task_id, exc)
        return {
            "task_id": normalized_task_id,
            "events": [],
            "count": 0,
            "error": {
                "type": "UnexpectedError",
                "message": str(exc),
                "retryable": False,
            },
        }

    events = data.get("events", [])

    # Update local events_summary for quick access.
    summary = [
        {
            "event_id": evt.get("id") or evt.get("event_id") or "",
            "kind": evt.get("kind") or evt.get("type") or "",
            "source": evt.get("source") or "",
        }
        for evt in events[:20]
    ]
    store.update_task(normalized_task_id, events_summary=summary)

    return {
        "task_id": normalized_task_id,
        "events": events,
        "count": len(events),
    }


@MCP.tool()
def openhands_cancel_task(task_id: Any) -> dict:
    """Cancel a previously started OpenHands task (best-effort).

    Note: OpenHands V1 API may not support task cancellation.  This
    tool marks the task as cancelled locally, but the remote conversation
    may continue running.

    Args:
        task_id: The task_id returned by openhands_start_task.

    Returns:
        A confirmation dict with the updated status.
    """
    normalized_task_id = _normalize_str_arg(task_id)
    store = _get_store()
    task = store.get_task(normalized_task_id)

    if task is None:
        return {
            "status": "failed",
            "error": {
                "type": "UnknownTaskId",
                "message": f"No task found with task_id='{normalized_task_id}'.",
                "retryable": False,
            },
        }

    current_status = task.get("status", "unknown")
    if current_status in ("completed", "failed", "cancelled", "timeout"):
        return {
            "task_id": normalized_task_id,
            "status": current_status,
            "message": f"Task is already in terminal state '{current_status}'.",
        }

    store.update_task(normalized_task_id, status="cancelled", updated_at=_utcnow_iso())
    logger.info("Task %s marked as cancelled (local only)", normalized_task_id)

    return {
        "task_id": normalized_task_id,
        "status": "cancelled",
        "message": (
            "Task marked as cancelled locally. "
            "Note: OpenHands API may not support remote cancellation; "
            "the conversation may continue running."
        ),
    }


@MCP.tool()
def call_llm(
    prompt: str,
    api_key: str,
    llm_model: str | None = None,
    repo: str | None = None,
    branch: str | None = None,
    agent_type: str = "default",
    url: str | None = None,
    conversation_id: str | None = None,
    poll_interval: int = 10,
    max_polls: int = 180,
    no_wait: bool = False,
    wait_seconds: int = 0,
) -> dict:
    """Create an OpenHands agent conversation and return the final LLM answer.

    This tool is backward-compatible with the original ``call_llm``.  By
    default it now runs in **non-blocking** mode (``no_wait=True``).  Use
    ``wait_seconds`` to request bounded blocking.

    Args:
        prompt: The task prompt for the agent.
        api_key: OpenHands API key.
        llm_model: LLM model override (e.g. openai/qwen3:32b).
        repo: Deprecated. Kept for backward compatibility but no longer
            passed to OpenHands as selected-repository metadata.
        branch: Deprecated. Kept for backward compatibility but no longer
            passed to OpenHands.
        agent_type: OpenHands agent type (default: 'default').
        url: OpenHands base URL override.
        conversation_id: Query existing conversation instead of creating new one.
        poll_interval: Polling interval in seconds.
        max_polls: Maximum polling attempts.
        no_wait: Only start conversation without waiting for completion.
        wait_seconds: Bounded blocking time (default 0 = non-blocking).
            If > 0, waits up to that many seconds before returning.

    Returns:
        If ``no_wait=True`` or ``wait_seconds`` expires: a dict with
        ``task_id`` and polling instructions.
        If ``wait_seconds`` is large enough for completion: a dict with
        ``answer``, ``conversation_id``, and ``status``.
    """
    base = (url or OPENHANDS_URL).rstrip("/")

    # --- mode: existing conversation (no_wait implied) -------------------
    if conversation_id:
        payload = {
            "conversation_id": conversation_id,
            "api_key": api_key,
            "events_limit": 100,
            "events_max_pages": 50,
            "final_fetch_delay": 30,
            "verbose_events": False,
        }
        resp = requests.post(
            f"{base}/v1/call_lm", json=payload, timeout=OPENHANDS_REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        return resp.json()

    # --- mode: start via new non-blocking flow --------------------------
    # Use openhands_start_task internally.
    start_result = openhands_start_task(
        prompt=prompt,
        api_key=api_key,
        llm_model=llm_model,
        repo=repo,
        branch=branch,
        agent_type=agent_type,
        url=url,
    )

    # If start failed, return the error immediately.
    if start_result.get("status") == "failed":
        return start_result

    task_id = start_result.get("task_id", "")

    # --- bounded wait if requested --------------------------------------
    if wait_seconds > 0:
        elapsed = 0
        while elapsed < wait_seconds:
            status_result = openhands_get_task_status(task_id=task_id, url=url)
            st = status_result.get("status", "unknown")
            if st == "completed":
                return {
                    "answer": status_result.get("answer", ""),
                    "conversation_id": status_result.get("conversation_id"),
                    "status": "completed",
                    "duration_seconds": status_result.get("duration_seconds"),
                }
            if st in ("failed", "cancelled", "timeout"):
                return {
                    "answer": "",
                    "conversation_id": status_result.get("conversation_id"),
                    "status": st,
                    "error": status_result.get("error"),
                }

            remaining = wait_seconds - elapsed
            sleep_time = min(OPENHANDS_POLL_INTERVAL, remaining)
            if sleep_time > 0:
                time.sleep(sleep_time)
            elapsed += sleep_time

        # Timeout: return task_id so caller can poll.
        return {
            "task_id": task_id,
            "conversation_id": start_result.get("conversation_id"),
            "status": "running",
            "message": (
                f"Waited {wait_seconds}s; task still running. "
                "Poll with openhands_get_task_status."
            ),
        }

    # --- no_wait (default) ----------------------------------------------
    return {
        "task_id": task_id,
        "conversation_id": start_result.get("conversation_id"),
        "status": start_result.get("status", "running"),
        "created_at": start_result.get("created_at"),
        "message": "Task started. Poll with openhands_get_task_status.",
    }


@MCP.tool()
def check_health() -> dict:
    """Check if the OpenHands LLM Call server is healthy."""
    resp = requests.get(f"{OPENHANDS_URL}/health", timeout=10)
    resp.raise_for_status()
    return resp.json()


@MCP.tool()
def check_job(uid: str, url: str | None = None) -> dict:
    """Check the status of an async LLM job by its UID.

    Args:
        uid: The job UID (conversation_id) returned by call_llm with no_wait=True.
        url: OpenHands base URL override (defaults to OPENHANDS_URL env var).
    """
    base = (url or OPENHANDS_URL).rstrip("/")
    resp = requests.get(f"{base}/v1/jobs/{uid}", timeout=120)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Role-level MCP tools
# ---------------------------------------------------------------------------


@MCP.tool()
def role_list() -> dict:
    """List all available worker roles.

    Returns a dict with a ``roles`` key containing a list of role
    summaries (name, description, model, readonly,
    requires_artifacts, output_artifact, timeout_minutes).

    Example::

        {
            "roles": [
                {
                    "name": "scout",
                    "description": "Read-only repository investigator...",
                    "model": "openai/qwen36-35b-a3b-coder",
                    "readonly": true,
                    "requires_artifacts": [],
                    "output_artifact": "scout_report",
                    "timeout_minutes": 60
                },
                ...
            ]
        }
    """
    return _role_tools.role_list_impl()


def _unwrap_arg(value: Any) -> Any:
    """Unwrap a scalar value that may be wrapped in a dict by OpenHands.

    OpenHands may serialize scalar arguments as objects like:
        {"default": 1800}  instead of  1800
        {"default": "x"}   instead of  "x"

    This helper extracts the inner value from both forms.
    """
    if isinstance(value, dict):
        for key in ("value", "default", "text", "prompt", "name", "id",
                     "user_task", "task"):
            if key in value:
                return value[key]
    return value


def _normalize_str_arg(value: Any) -> str | None:
    """Normalize a string argument that may be wrapped as a dict."""
    value = _unwrap_arg(value)
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _normalize_int_arg(
    value: Any, default: int | None = None
) -> int | None:
    """Normalize an integer argument that may be wrapped as a dict."""
    value = _unwrap_arg(value)
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return default
        try:
            return int(stripped)
        except ValueError:
            return default
    return default


def _normalize_bool_arg(
    value: Any, default: bool = False
) -> bool:
    """Normalize a boolean argument that may be wrapped as a dict."""
    value = _unwrap_arg(value)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return default


def _normalize_text_arg(value: Any) -> str | None:
    """Normalize a prompt/user_task argument that may be wrapped as a dict.

    OpenHands may serialize a plain string prompt as an object:
        {"text": "actual prompt text"}
    This helper extracts the string from both forms.
    """
    value = _unwrap_arg(value)
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


# ---------------------------------------------------------------------------
# New LLM-friendly normalization helpers (Section 2 of task)
# ---------------------------------------------------------------------------


def unwrap_text(value: Any) -> Any:
    """Accept raw MCP/SHTTP values.

    Examples:
    - {"text": "abc"} -> "abc"
    - {"text": True} -> True
    - "abc" -> "abc"
    - 123 -> 123
    """
    if isinstance(value, dict) and "text" in value and len(value) == 1:
        return value["text"]
    return value


def _unwrap_to_scalar(value: Any) -> Any:
    """Unwrap a scalar value that may be wrapped in a dict.

    Handles both MCP TextContent ({"text": ...}) and OpenHands scalar
    wrapping ({"default": ...}, {"value": ...}, etc.).
    """
    # Try MCP text wrapper first
    unwrapped = unwrap_text(value)
    if unwrapped is not value:
        return unwrapped
    # Fall back to general _unwrap_arg keys
    if isinstance(value, dict):
        for key in ("value", "default", "prompt", "name", "id",
                     "user_task", "task"):
            if key in value:
                return value[key]
    return value


def normalize_bool(value: Any, default: bool = False) -> bool:
    """Normalize a boolean value that may be wrapped or string-encoded."""
    value = _unwrap_to_scalar(value)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "y", "on"):
            return True
        if lowered in ("false", "0", "no", "n", "off"):
            return False
    return bool(value)


def normalize_int(value: Any, default: int | None = None) -> int | None:
    """Normalize an integer value that may be wrapped or string-encoded."""
    value = _unwrap_to_scalar(value)
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        return int(value.strip())
    raise ValueError(
        f"Expected int-compatible value, got {type(value).__name__}: {value!r}"
    )


def normalize_string(value: Any, field_name: str) -> str:
    """Normalize a string value that may be wrapped."""
    value = _unwrap_to_scalar(value)
    if isinstance(value, str):
        return value
    raise ValueError(
        f"{field_name} must be a string, got {type(value).__name__}: {value!r}"
    )


def normalize_role_run_id(value: Any) -> str:
    """Extract role_run_id from common LLM/MCP mistake shapes.

    Accepts:
    - "RUN-scout-1"
    - {"text": "RUN-scout-1"}
    - {"default": "RUN-scout-1"}
    - {"value": "RUN-scout-1"}
    - {"role_run_id": "RUN-scout-1", ...}
    - {"role_run_id": {"text": "RUN-scout-1"}}
    - {"role_run_id": {"role_run_id": "RUN-scout-1", ...}}
    """
    value = _unwrap_to_scalar(value)
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if "role_run_id" in value:
            return normalize_role_run_id(value["role_run_id"])
        if "id" in value:
            return normalize_role_run_id(value["id"])
        if "text" in value:
            return normalize_role_run_id(value["text"])
    raise ValueError(
        "role_run_id must be a string or an object containing role_run_id/text/id; "
        f"got {type(value).__name__}: {value!r}"
    )


def normalize_artifact_name(value: Any) -> str | None:
    """Normalize artifact_name that may be wrapped or nested."""
    value = _unwrap_to_scalar(value)
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if "name" in value:
            return normalize_artifact_name(value["name"])
        if "artifact_name" in value:
            return normalize_artifact_name(value["artifact_name"])
        if "text" in value:
            return normalize_artifact_name(value["text"])
    raise ValueError(
        "artifact_name must be a string or object containing name/artifact_name/text; "
        f"got {type(value).__name__}: {value!r}"
    )


def _build_invalid_role_run_id_error(field_name: str = "role_run_id") -> dict:
    """Build an LLM-friendly error for invalid role_run_id."""
    return {
        "status": "failed",
        "error": {
            "type": "InvalidRoleRunId",
            "message": (
                f"Invalid {field_name}. Expected a plain string like "
                f'"20260606-215637-1c1074-scout-1". You passed an object. '
                f"If this object came from role_start, pass only its "
                f'"{field_name}" field. Do not call role_start again. '
                f"Retry with the existing {field_name} string."
            ),
            "retryable": True,
        },
    }


def _build_another_role_running_error(
    active_id: str,
    active_role: str,
    active_status: str,
    refresh_failed: bool = False,
) -> dict:
    """Build an LLM-friendly error for single-active-role violation."""
    message = (
        "Another role is already running. This MCP server is configured for "
        "single-threaded model execution. Wait for the current role using "
        "role_wait before starting the next role."
    )
    if refresh_failed:
        message += (
            " The active role status could not be refreshed from OpenHands; "
            "the lock may be stale."
        )

    return {
        "error": "another_role_running",
        "message": message,
        "active_role_run_id": active_id,
        "active_role": active_role,
        "active_status": active_status,
        "next_action": {
            "tool": "role_wait",
            "arguments": {
                "role_run_id": active_id,
                "timeout_seconds": 1800,
                "poll_interval_seconds": 15,
                "return_result": True,
            },
        },
    }


@MCP.tool()
def role_start(
    role: str,
    prompt: Any = None,
    user_task: Any = None,
    repo: Any | None = None,
    base_branch: Any | None = None,
    branch: Any | None = None,
    context: dict | None = None,
    artifacts: dict | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """Start a named worker role as an OpenHands task.

    **Important: Only one role run may be active at a time.**
    This MCP server is configured for single-threaded model execution.
    Do not start another role until the previous role is completed.

    After this call, copy ONLY the string value of the ``role_run_id``
    field and pass it to ``role_wait``.  Do **not** pass the whole
    ``role_start`` response object to ``role_wait``.

    If ``role_wait`` arguments were malformed, retry ``role_wait`` with
    the existing ``role_run_id``.  Do **not** call ``role_start`` again.

    Args:
        role: The role name (e.g. ``"scout"``, ``"architect"``,
            ``"coder"``, ``"reviewer"``, ``"publisher"``).
        prompt: The user's task/prompt description.  Used as primary
            input when provided.  Accepts both plain strings and
            dict-wrapped values (e.g. ``{"text": "..."}``).
        user_task: Deprecated alias for ``prompt``.  Kept for backward
            compatibility.  If both are provided, ``prompt`` takes
            precedence.  Accepts both plain strings and dict-wrapped
            values.
        repo: Deprecated.  If provided as a dict it is normalized to a
            string or discarded.  No longer passed to OpenHands as
            selected-repository metadata.
        base_branch: Deprecated.  See ``repo``.
        branch: Deprecated.  See ``repo``.
        context: Optional dict with ``run_id`` and other context
            (may include ``idempotency_key``).
        artifacts: Mapping of artifact names to their text content
            (e.g. ``{"scout_report": "..."}``).
        idempotency_key: Optional stable key to deduplicate retried
            calls.  Top-level key takes precedence over
            ``context.idempotency_key``.

    Returns:
        On success: ``{run_id, role_run_id, role, status, poll_after_seconds,
        timeout_minutes, idempotent_reuse}``
        On failure: ``{status: "failed", error: {...}}`` or
        ``{error: "another_role_running", ...}``

    Example input::

        {
            "role": "scout",
            "prompt": "Investigate repository ...",
            "run_id": "ruby-grpc-client-20260606-215637",
            "idempotency_key": "scout-ruby-grpc-client"
        }

    Example output::

        {
            "role_run_id": "ruby-grpc-client-20260606-215637-scout-1",
            "status": "running"
        }
    """
    # Normalize prompt/user_task: handle both plain strings and
    # dict-wrapped values (e.g. {"text": "..."}) that OpenHands
    # may serialize.
    effective_prompt = _normalize_text_arg(prompt) or _normalize_text_arg(user_task)
    if not effective_prompt:
        return {
            "status": "failed",
            "error": {
                "type": "MissingPrompt",
                "message": "Either 'prompt' or 'user_task' is required.",
                "retryable": False,
            },
        }

    # Normalize repo/base_branch/branch: if they are dicts extract
    # string values; otherwise keep as-is or discard.
    def _normalize_str(val: Any) -> str | None:
        if val is None:
            return None
        if isinstance(val, str):
            return val
        if isinstance(val, dict):
            # Try common keys
            for key in ("url", "name", "value", "repo", "branch"):
                v = val.get(key)
                if isinstance(v, str) and v:
                    return v
            return None
        return str(val) if val else None

    effective_repo = _normalize_str(repo)
    effective_base_branch = _normalize_str(base_branch)
    effective_branch = _normalize_str(branch)

    # ------------------------------------------------------------------
    # Single-active-role protection (Section 7 of task)
    # ------------------------------------------------------------------
    from .role_tools import _find_active_role_run, _get_role_store

    active = _find_active_role_run(_get_role_store())
    if active is not None:
        active_id = active.get("role_run_id", "unknown")
        active_role = active.get("role", "unknown")
        active_status = active.get("status", "unknown")

        # Idempotent reuse of same active run: if the same
        # idempotency_key refers to the same already-running role,
        # return the existing run metadata instead of launching a
        # duplicate.
        effective_idem_key = idempotency_key or (
            context.get("idempotency_key") if context else None
        )
        if effective_idem_key:
            run_id_from_context = _normalize_str(
                context.get("run_id") if context else None
            )
            idem_scope = f"{run_id_from_context}:{role}:{effective_idem_key}"
            existing_id = _get_role_store().find_by_idempotency_scope(idem_scope)
            if existing_id == active_id:
                # Same active run — return it (idempotent reuse)
                logger.info(
                    "Idempotent reuse of same active role run %s",
                    active_id,
                )
                return {
                    "role_run_id": active_id,
                    "run_id": run_id_from_context,
                    "role": active_role,
                    "status": active_status,
                    "poll_after_seconds": 30,
                    "timeout_minutes": active.get("timeout_minutes", 60),
                    "idempotent_reuse": True,
                }

        # Reject with LLM-friendly error
        refresh_failed = active.get("_refresh_failed", False)
        return _build_another_role_running_error(
            active_id, active_role, active_status, refresh_failed=refresh_failed
        )

    return _role_tools.role_start_impl(
        role=role,
        user_task=effective_prompt,
        repo=effective_repo,
        base_branch=effective_base_branch,
        branch=effective_branch,
        context=context,
        artifacts=artifacts,
        idempotency_key=idempotency_key,
    )


@MCP.tool()
def role_status(role_run_id: Any) -> dict:
    """Single-shot diagnostic status check.

    **Diagnostic only.** Do not call repeatedly in a tight loop from an
    LLM orchestrator.  Use ``role_wait`` for normal long-running role
    orchestration.  Repeated identical ``role_status`` calls can trigger
    OpenHands' stuck-loop detector in the top-level orchestrator.

    Args:
        role_run_id: The role run ID returned by ``role_start``.

    Returns:
        Normalized status dict.

    Example::

        {
            "role_run_id": "20260605-abc123-scout-1",
            "run_id": "20260605-abc123",
            "role": "scout",
            "status": "running",
            "summary": "Short summary if available",
            "has_result": false
        }
    """
    normalized_role_run_id = _normalize_str_arg(role_run_id)
    return _role_tools.role_status_impl(role_run_id=normalized_role_run_id)


@MCP.tool()
def role_result(
    role_run_id: Any,
    include_full_result: Any = True,
    force_refresh: Any = False,
) -> dict:
    """Get the result of a completed role.

    **Pass ONLY the ``role_run_id`` string, not the whole role object.**
    Use ``role_wait`` first when possible.

    If the role is not yet completed, returns status without full result.
    If completed, fetches the full result, saves the artifact, and
    derives summary/action/risk.

    Args:
        role_run_id: The role run ID returned by ``role_start``.
            Accepts both plain strings and dict-wrapped values.
        include_full_result: If True (default), returns the full result
            text.  If False, omits ``full_result`` but still returns
            artifact metadata and a summary.
        force_refresh: If True and the cached answer is empty, re-fetch
            from the OpenHands FastAPI backend to bypass stale cached
            results.

    Returns:
        Structured role result.

    Example (full)::

        {
            "role_run_id": "20260605-abc123-scout-1",
            "run_id": "20260605-abc123",
            "role": "scout",
            "status": "completed",
            "action": "CONTINUE",
            "risk": null,
            "artifact_name": "scout_report",
            "artifact_path": "runs/20260605-abc123/01-scout.answer.md",
            "result_summary": "Short summary",
            "full_result": "Full markdown report"
        }

    Example (compact)::

        {
            "role_run_id": "20260605-abc123-scout-1",
            "run_id": "20260605-abc123",
            "role": "scout",
            "status": "completed",
            "action": "CONTINUE",
            "risk": null,
            "artifact_name": "scout_report",
            "artifact_path": "runs/20260605-abc123/01-scout.answer.md",
            "result_summary": "Short summary",
            "full_result": null,
            "full_result_omitted": true
        }
    """
    try:
        normalized_role_run_id = normalize_role_run_id(role_run_id)
    except ValueError:
        return _build_invalid_role_run_id_error("role_run_id")

    normalized_include_full_result = normalize_bool(
        include_full_result, default=True
    )
    normalized_force_refresh = normalize_bool(
        force_refresh, default=False
    )
    return _role_tools.role_result_impl(
        role_run_id=normalized_role_run_id,
        include_full_result=normalized_include_full_result,
        force_refresh=normalized_force_refresh,
    )


# ---------------------------------------------------------------------------
# role_wait — server-side polling
# ---------------------------------------------------------------------------


@MCP.tool()
def role_wait(
    role_run_id: Any,
    timeout_seconds: Any = None,
    poll_interval_seconds: Any = None,
    return_result: Any = True,
) -> dict:
    """Wait for an existing role run.

    **Pass ONLY the ``role_run_id`` string returned by ``role_start``.**

    Correct::

        {"role_run_id":"RUN-scout-1","timeout_seconds":1800,"poll_interval_seconds":15,"return_result":true}

    Incorrect::

        {"role_run_id":{"role_run_id":"RUN-scout-1","status":"running"}}

    If your previous ``role_wait`` call had malformed arguments, retry
    ``role_wait`` with the same ``role_run_id``.
    Do **not** start the role again.

    Args:
        role_run_id: The role run ID returned by ``role_start``.
            Accepts both plain strings and dict-wrapped values.
        timeout_seconds: Maximum seconds to wait (default 1800, clamped to [1, 7200]).
            Override with env var ``OPENHANDS_ROLE_WAIT_TIMEOUT_SECONDS``.
        poll_interval_seconds: Seconds between status checks (default 15, clamped to [5, 120]).
            Override with env var ``OPENHANDS_ROLE_WAIT_POLL_INTERVAL_SECONDS``.
        return_result: If True (default) and the role completed, inline the
            full result.  If False, return a compact response with
            ``result_available: true`` and ``next_action: "call role_result"``.

    Returns
    -------
    dict
        One of:

        **Completed with result** (return_result=True)::

            {
                "role_run_id": "...",
                "status": "completed",
                "has_result": true,
                "result": "...",
                "duration_seconds": 742
            }

        **Terminal failure**::

            {
                "role_run_id": "...",
                "status": "failed",
                "has_result": false,
                "error": {
                    "type": "RoleFailed",
                    "message": "...",
                    "retryable": true
                },
                "duration_seconds": 1234
            }

        **Bounded timeout** (role still running)::

            {
                "role_run_id": "...",
                "status": "running",
                "has_result": false,
                "wait_timed_out": true,
                "message": "Role is still running after bounded wait. Call role_wait again later.",
                "poll_after_seconds": 60,
                "duration_seconds": 1800
            }

    Example::

        {
            "role_run_id": "20260605-abc123-scout-1",
            "timeout_seconds": 1800,
            "poll_interval_seconds": 15,
            "return_result": true
        }
    """
    # Defensive parsing for nested LLM mistakes (Section 3 of task).
    # When the model passes the full role_start response as role_run_id,
    # extract the nested role_run_id and any nested timeout/poll/return args.
    raw_role_arg = role_run_id
    _timeout = timeout_seconds
    _poll = poll_interval_seconds
    _return = return_result

    if isinstance(raw_role_arg, dict):
        has_nested_timeout = "timeout_seconds" in raw_role_arg
        has_nested_poll = "poll_interval_seconds" in raw_role_arg
        has_nested_return = "return_result" in raw_role_arg

        if has_nested_timeout or has_nested_poll or has_nested_return:
            # LLM passed the full role_start response as role_run_id
            _timeout = _timeout if _timeout is not None else raw_role_arg.get("timeout_seconds")
            _poll = _poll if _poll is not None else raw_role_arg.get("poll_interval_seconds")
            _return = _return if _return is not None else raw_role_arg.get("return_result")

    try:
        normalized_role_run_id = normalize_role_run_id(raw_role_arg)
    except ValueError:
        return _build_invalid_role_run_id_error("role_run_id")

    if not normalized_role_run_id:
        return {
            "status": "failed",
            "error": {
                "type": "MissingRoleRunId",
                "message": "role_run_id is required",
                "retryable": False,
            },
        }

    normalized_timeout = normalize_int(_timeout, default=None)
    normalized_poll_interval = normalize_int(_poll, default=None)
    normalized_return_result = normalize_bool(_return, default=True)

    return _role_tools.role_wait_impl(
        role_run_id=normalized_role_run_id,
        timeout_seconds=normalized_timeout,
        poll_interval_seconds=normalized_poll_interval,
        return_result=normalized_return_result,
    )


# ---------------------------------------------------------------------------
# Artifact MCP tools
# ---------------------------------------------------------------------------


@MCP.tool()
def artifact_list(run_id: Any = None, role_run_id: Any = None) -> dict:
    """List artifacts for a given run.

    **Pass ``role_run_id`` as a plain string.**
    Do **not** pass the entire ``role_start`` or ``role_wait`` response object.

    Args:
        run_id: The top-level run identifier returned by ``role_start``.
        role_run_id: The role-specific run ID. If provided, the ``run_id``
            is resolved from the role run record.  Accepts both plain
            strings and dict-wrapped values.

    Returns:
        A dict with ``run_id`` and ``artifacts`` (list of artifact
        metadata records).

    Example::

        {
            "run_id": "20260605-abc123",
            "artifacts": [
                {
                    "artifact_name": "scout_report",
                    "role": "scout",
                    "role_run_id": "20260605-abc123-scout-1",
                    "artifact_path": "runs/20260605-abc123/...",
                    "created_at": "..."
                }
            ]
        }
    """
    # If role_run_id is provided, resolve to run_id (Section 6 of task).
    if role_run_id is not None:
        try:
            resolved_rid = normalize_role_run_id(role_run_id)
        except ValueError:
            return _build_invalid_role_run_id_error("role_run_id")
        store = _get_role_store()
        role_run = store.get_role_run(resolved_rid)
        if role_run is not None:
            run_id = role_run.get("run_id", run_id)

    normalized_run_id = _normalize_str_arg(run_id) if run_id is not None else None
    return _role_tools.artifact_list_impl(run_id=normalized_run_id)


@MCP.tool()
def artifact_get(
    run_id: Any = None,
    artifact_name: Any = None,
    role_run_id: Any = None,
) -> dict:
    """Read artifact content produced by a role run.

    **Prefer this tool over reading artifact_path from the sandbox filesystem.**

    **Pass ``role_run_id`` as a plain string.**
    Do **not** pass the entire ``role_start`` or ``role_wait`` response object.

    Args:
        run_id: The top-level run identifier.
        artifact_name: Logical artifact name (e.g. ``"scout_report"``).
            Mutually exclusive with ``role_run_id``; if both are
            provided, ``artifact_name`` is preferred.  Accepts both
            plain strings and dict-wrapped values.
        role_run_id: The role-specific run ID.  Accepts both plain
            strings and dict-wrapped values.

    Returns:
        Artifact metadata with ``content`` key, or an error dict.

    Example::

        {
            "run_id": "20260605-abc123",
            "artifact_name": "scout_report",
            "role": "scout",
            "role_run_id": "20260605-abc123-scout-1",
            "artifact_path": "runs/20260605-abc123/...",
            "content": "Full artifact text ..."
        }
    """
    try:
        normalized_role_run_id = (
            normalize_role_run_id(role_run_id) if role_run_id is not None else None
        )
    except ValueError:
        return _build_invalid_role_run_id_error("role_run_id")

    normalized_artifact_name = (
        normalize_artifact_name(artifact_name) if artifact_name is not None else None
    )
    normalized_run_id = _normalize_str_arg(run_id) if run_id is not None else None
    return _role_tools.artifact_get_impl(
        run_id=normalized_run_id,
        artifact_name=normalized_artifact_name,
        role_run_id=normalized_role_run_id,
    )


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _duration_seconds(start_iso: str | None, end_iso: str | None) -> int | None:
    """Compute duration in seconds between two ISO-8601 strings."""
    if not start_iso or not end_iso:
        return None
    try:
        start_dt = datetime.fromisoformat(start_iso)
        end_dt = datetime.fromisoformat(end_iso)
        diff = (end_dt - start_dt).total_seconds()
        return int(max(diff, 0))
    except (ValueError, TypeError):
        return None


if __name__ == "__main__":
    # Use explicit uvicorn startup so we can control the bind host/port.
    # FastMCP.run(transport="streamable-http") does not accept host/port
    # kwargs in the installed mcp>=1.0.0 version.
    app = MCP.streamable_http_app()
    uvicorn.run(app, host=MCP_HOST, port=MCP_PORT)
