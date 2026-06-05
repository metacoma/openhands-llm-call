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
) -> dict:
    """Get the final result / answer of a completed OpenHands task.

    Args:
        task_id: The task_id returned by openhands_start_task.
        url: OpenHands LLM base URL override.

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
        return {
            "task_id": normalized_task_id,
            "conversation_id": conversation_id,
            "status": "completed",
            "answer": result.get("answer", "") if isinstance(result, dict) else result,
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

    The server renders the role-specific prompt, selects the model,
    and starts an OpenHands task using the existing backend.

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
        On failure: ``{status: "failed", error: {...}}``

    Example::

        {
            "run_id": "20260605-abc123",
            "role_run_id": "20260605-abc123-scout-1",
            "role": "scout",
            "status": "running",
            "poll_after_seconds": 30,
            "timeout_minutes": 60,
            "idempotent_reuse": false
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
    role_run_id: Any, include_full_result: Any = True
) -> dict:
    """Get the result of a completed role.

    If the role is not yet completed, returns status without full result.
    If completed, fetches the full result, saves the artifact, and
    derives summary/action/risk.

    Args:
        role_run_id: The role run ID returned by ``role_start``.
        include_full_result: If True (default), returns the full result
            text.  If False, omits ``full_result`` but still returns
            artifact metadata and a summary.

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
    normalized_role_run_id = _normalize_str_arg(role_run_id)
    normalized_include_full_result = _normalize_bool_arg(
        include_full_result, default=True
    )
    return _role_tools.role_result_impl(
        role_run_id=normalized_role_run_id,
        include_full_result=normalized_include_full_result,
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
    """Wait for a long-running role to finish using server-side polling.

    Use this after ``role_start`` instead of repeatedly calling ``role_status``.
    Returns completed result, terminal error, or bounded running state.

    Args:
        role_run_id: The role run ID returned by ``role_start``.
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
    normalized_role_run_id = _normalize_str_arg(role_run_id)
    if not normalized_role_run_id:
        return {
            "status": "failed",
            "error": {
                "type": "MissingRoleRunId",
                "message": "role_run_id is required",
                "retryable": False,
            },
        }
    normalized_timeout = _normalize_int_arg(timeout_seconds, default=None)
    normalized_poll_interval = _normalize_int_arg(
        poll_interval_seconds, default=None
    )
    normalized_return_result = _normalize_bool_arg(
        return_result, default=True
    )
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
def artifact_list(run_id: Any) -> dict:
    """List artifacts for a given run.

    Args:
        run_id: The top-level run identifier returned by ``role_start``.

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
    normalized_run_id = _normalize_str_arg(run_id)
    return _role_tools.artifact_list_impl(run_id=normalized_run_id)


@MCP.tool()
def artifact_get(
    run_id: Any = None,
    artifact_name: Any = None,
    role_run_id: Any = None,
) -> dict:
    """Get an artifact by name or role_run_id.

    Args:
        run_id: The top-level run identifier.
        artifact_name: Logical artifact name (e.g. ``"scout_report"``).
            Mutually exclusive with ``role_run_id``; if both are
            provided, ``artifact_name`` is preferred.
        role_run_id: The role-specific run ID.

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
    normalized_run_id = _normalize_str_arg(run_id)
    normalized_artifact_name = _normalize_str_arg(artifact_name)
    normalized_role_run_id = _normalize_str_arg(role_run_id)
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
