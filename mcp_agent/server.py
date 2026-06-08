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

from .artifact_store import ArtifactStore
from .task_store import TaskStore
from . import role_tools as _role_tools
from .roles import get_role, list_roles

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


def check_health() -> dict:
    """Check if the OpenHands LLM Call server is healthy."""
    resp = requests.get(f"{OPENHANDS_URL}/health", timeout=10)
    resp.raise_for_status()
    return resp.json()


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


def _role_list_internal() -> dict:
    """List all available worker roles.

    **Internal helper only** — not exposed as an MCP tool.
    Used internally by other legacy helpers.
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


def unwrap_scalar(value: Any, extra_keys: list[str] | None = None) -> Any:
    """Unwrap a scalar value that may be wrapped in various MCP/LLM dict shapes.

    Handles:
    - Plain scalar → returned as-is
    - {"text": "x"} → "x"
    - {"value": "x"} → "x"
    - {"default": "x"} → "x"
    - {"id": "x"} → "x"
    - {"artifact_id": "x"} → "x"
    - Extra keys passed in *extra_keys* (e.g. ["role", "idempotency_key"])

    If the dict has multiple keys and none match, return the original value.
    """
    # Plain scalar → pass through
    if not isinstance(value, dict):
        return value

    # Single-key dict → unwrap that key's value
    if len(value) == 1:
        key = next(iter(value))
        inner = value[key]
        # Recurse for nested wrappers
        result = unwrap_scalar(inner)
        return result

    # Multi-key dict → check known keys
    # Priority order: text, value, default, id, artifact_id, then extra_keys
    for key in ("text", "value", "default", "id", "artifact_id"):
        if key in value:
            return unwrap_scalar(value[key])

    # Check extra_keys (role-specific wrappers)
    if extra_keys:
        for key in extra_keys:
            if key in value:
                return unwrap_scalar(value[key])

    # Unknown multi-key dict → return as-is (caller will handle)
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


def normalize_role(value: Any) -> str:
    """Normalize a role name that may be wrapped in various MCP/LLM shapes.

    Accepts:
    - "scout"
    - {"text": "scout"}
    - {"name": "scout"}
    - {"role": "scout"}
    - {"name": {"text": "scout"}}
    - {"role": {"text": "scout"}}
    """
    if isinstance(value, str):
        return value.strip()

    if isinstance(value, dict):
        # Check common LLM/MCP wrapper keys in priority order
        for key in ("name", "role", "value", "id", "text"):
            if key in value:
                inner = value[key]
                # Recursively unwrap nested wrappers
                result = normalize_role(inner)
                return result

    # Fallback: convert to string (handles numbers, bools, etc.)
    return str(value).strip()


def _unwrap_dict_values(value: Any) -> Any:
    """Recursively unwrap dict values that may contain {\"text\": ...} wrappers.

    If *value* is a dict with a single \"text\" key, it is unwrapped recursively.
    If *value* is a dict with other keys, each value is unwrapped recursively.
    If a value is a string that looks like JSON, it is parsed as JSON.
    """
    if isinstance(value, dict):
        # Single-key {"text": ...} -> unwrap and recurse
        if len(value) == 1 and "text" in value:
            return _unwrap_dict_values(value["text"])
        # Multi-key dict -> unwrap each value
        result: dict[str, Any] = {}
        for k, v in value.items():
            result[k] = _unwrap_dict_values(v)
        return result

    if isinstance(value, str):
        # Try parsing as JSON in case the LLM sent a JSON string
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return _unwrap_dict_values(parsed)
        except (json.JSONDecodeError, TypeError):
            pass
        return value

    if isinstance(value, list):
        return [_unwrap_dict_values(item) for item in value]

    return value


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
    refresh_warning: str = "",
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

    result = {
        "error": "another_role_running",
        "message": message,
        "active_role_run_id": active_id,
        "active_role": active_role,
        "active_status": active_status,
        "refresh_failed": refresh_failed,
        "refresh_warning": refresh_warning,
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
    return result


# Legacy role tools — kept as internal helpers but NOT exposed to
# Head of IT.  Decorated with @MCP.tool() removed so they are invisible
# to MCP tool discovery.  Available for debugging as plain Python functions.
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
        refresh_warning = active.get("_refresh_warning", "")
        return _build_another_role_running_error(
            active_id, active_role, active_status,
            refresh_failed=refresh_failed,
            refresh_warning=refresh_warning,
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
# role_wait — server-side polling (public MCP tool)
# ---------------------------------------------------------------------------


@MCP.tool()
def role_wait(
    role_run_id: Any,
    timeout_seconds: Any = None,
    poll_interval_seconds: Any = None,
) -> dict:
    """Wait for a role run to complete (polling + summary).

    This is the **blocking** half of the two-step pattern:
    ``role_call`` → ``role_wait``.

    **Pass ONLY the ``role_run_id`` string returned by ``role_call``.**

    Correct::

        {"role_run_id":"20260607-xxx-scout-1","timeout_seconds":1800,"poll_interval_seconds":30}

    Incorrect::

        {"role_run_id":{"role_run_id":"20260607-xxx-scout-1","status":"running"}}

    If your previous ``role_wait`` call timed out, retry
    ``role_wait`` with the same ``role_run_id``.
    Do **not** start the role again.

    Args:
        role_run_id: The role run ID returned by ``role_call``.
            Accepts both plain strings and dict-wrapped values.
        timeout_seconds: Maximum seconds to wait (default 1800).
            Override with env var ``OPENHANDS_ROLE_WAIT_TIMEOUT_SECONDS``.
        poll_interval_seconds: Seconds between status checks (default 30).
            Override with env var ``OPENHANDS_ROLE_WAIT_POLL_INTERVAL_SECONDS``.

    Returns
    -------
    dict
        One of:

        **Completed**::

            {
                "status": "completed",
                "role_run_id": "...",
                "run_id": "...",
                "role": "...",
                "control_summary": {...},
                "artifacts": {
                    "primary": {"artifact_id": "...", "artifact_type": "...", "created_by": "..."},
                    "summary": {"artifact_id": "...", "artifact_type": "...", "created_by": "..."}
                }
            }

        **Timeout** (role still running)::

            {
                "status": "running",
                "role_run_id": "...",
                "run_id": "...",
                "role": "...",
                "timeout": true,
                "message": "Role is still running. Call role_wait again with the same role_run_id."
            }

        **Failed**::

            {
                "status": "failed",
                "role_run_id": "...",
                "run_id": "...",
                "role": "...",
                "error": {"type": "...", "message": "...", "retryable": true}
            }

    Example::

        {
            "role_run_id": "20260607-xxx-scout-1",
            "timeout_seconds": 1800,
            "poll_interval_seconds": 30
        }
    """
    # Defensive parsing for nested LLM mistakes.
    # When the model passes the full role_call response as role_run_id,
    # extract the nested role_run_id and any nested timeout/poll args.
    raw_role_arg = role_run_id
    _timeout = timeout_seconds
    _poll = poll_interval_seconds

    if isinstance(raw_role_arg, dict):
        has_nested_timeout = "timeout_seconds" in raw_role_arg
        has_nested_poll = "poll_interval_seconds" in raw_role_arg

        if has_nested_timeout or has_nested_poll:
            # LLM passed the full role_call response as role_run_id
            _timeout = _timeout if _timeout is not None else raw_role_arg.get("timeout_seconds")
            _poll = _poll if _poll is not None else raw_role_arg.get("poll_interval_seconds")

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

    # Call the new lifecycle-aware role_wait implementation
    from . import role_lifecycle

    return role_lifecycle.role_lifecycle_wait_impl(
        role_run_id=normalized_role_run_id,
        timeout_seconds=normalized_timeout,
        poll_interval_seconds=normalized_poll_interval,
    )


# ---------------------------------------------------------------------------
# Artifact MCP tools
# ---------------------------------------------------------------------------


def _artifact_list_internal(run_id: Any = None, role_run_id: Any = None) -> dict:
    """List artifacts for a given run.

    **Internal helper only** — not exposed as an MCP tool.
    Used by legacy v2 result path for artifact scanning.

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
# Legacy v2 Role tools — kept as internal helpers but NOT exposed to
# Head of IT.  The new canonical tool is ``role_call``.
# ---------------------------------------------------------------------------


def _internal_role_start(
    role: Any,
    user_task: Any,
    input_artifacts: Any = None,
    metadata: Any = None,
    api_key: Any = None,
    llm_model: Any = None,
    url: Any = None,
    idempotency_key: Any = None,
) -> dict:
    """Start a role using the artifact-reference API.

    **Internal helper only** — not exposed as an MCP tool.
    **Deprecated.** Use ``role_call`` instead.

    Accepts artifact IDs/paths (not content). MCP server resolves them.
    Returns control summary inline after two-step same-conversation lifecycle.

    Args:
        role: The role name (e.g. 'scout', 'architect', 'coder').
        user_task: The user task text. Required and must be non-empty.
        input_artifacts: Mapping of artifact names to their ID/path strings.
            The MCP server resolves these to content server-side.
        metadata: Optional metadata dict (e.g. {'repository': '...'}).
        api_key: OpenHands API key.
        llm_model: LLM model override.
        url: OpenHands LLM base URL override.
        idempotency_key: Optional stable key to deduplicate retried calls.

    Returns:
        A dict with ``role_run_id``, ``status``, ``control_summary``,
        and ``artifacts`` (primary and summary artifact references).

    Example::

        {
            "role": {"text": "architect"},
            "user_task": {"text": "Implement a Ruby gRPC client."},
            "input_artifacts": {
                "scout_report": {"text": "20260607-010712-scout_report.artifact"}
            },
            "metadata": {
                "repository": {"text": "https://github.com/example/repo"}
            }
        }
    """
    # Normalize inputs using existing helpers
    normalized_role = normalize_string(role, "role")
    normalized_user_task = unwrap_text(user_task)
    normalized_input_artifacts = (
        unwrap_text(input_artifacts)
        if input_artifacts is not None
        else None
    )
    normalized_metadata = (
        unwrap_text(metadata) if metadata is not None else None
    )
    normalized_api_key = unwrap_text(api_key) if api_key is not None else ""
    normalized_llm_model = unwrap_text(llm_model) if llm_model is not None else None
    normalized_url = unwrap_text(url) if url is not None else None
    normalized_idempotency_key = unwrap_text(
        idempotency_key
    ) if idempotency_key is not None else None

    # Convert input_artifacts from unwrapped dict to string-value mapping
    if isinstance(normalized_input_artifacts, dict):
        resolved_artifacts: dict[str, str] = {}
        for k, v in normalized_input_artifacts.items():
            resolved_artifacts[k] = unwrap_text(v) if v is not None else ""
        normalized_input_artifacts = resolved_artifacts
    elif isinstance(normalized_input_artifacts, str):
        try:
            normalized_input_artifacts = json.loads(normalized_input_artifacts)
        except (json.JSONDecodeError, TypeError):
            normalized_input_artifacts = {}
    else:
        normalized_input_artifacts = {}

    # Convert metadata from unwrapped dict to string-value mapping
    if isinstance(normalized_metadata, dict):
        resolved_metadata: dict[str, str] = {}
        for k, v in normalized_metadata.items():
            resolved_metadata[k] = unwrap_text(v) if v is not None else ""
        normalized_metadata = resolved_metadata
    elif isinstance(normalized_metadata, str):
        try:
            normalized_metadata = json.loads(normalized_metadata)
        except (json.JSONDecodeError, TypeError):
            normalized_metadata = {}
    else:
        normalized_metadata = {}

    # Import and call the lifecycle implementation
    from . import role_lifecycle

    return role_lifecycle.role_call_impl(
        role=normalized_role,
        user_task=str(normalized_user_task) if normalized_user_task else "",
        input_artifacts=normalized_input_artifacts,
        metadata=normalized_metadata,
        api_key=str(normalized_api_key) if normalized_api_key else "",
        llm_model=str(normalized_llm_model) if normalized_llm_model else None,
        url=str(normalized_url) if normalized_url else None,
        idempotency_key=str(normalized_idempotency_key) if normalized_idempotency_key else None,
    )


def _internal_role_wait(
    role_run_id: Any,
    timeout_seconds: Any = None,
    poll_interval_seconds: Any = None,
    return_result: Any = None,
) -> dict:
    """Wait for a role run and return its status.

    **Internal helper only** — not exposed as an MCP tool.
    **Deprecated.** Use ``role_call`` instead.

    **Note**: ``_internal_role_start`` executes the full lifecycle
    synchronously (main prompt + summary prompt) and returns the
    completed result. In most cases, ``_internal_role_wait`` is not
    needed because the result is already available from ``_internal_role_start``.

    This function is provided for compatibility with the established
    start → wait → result orchestration model.

    Args:
        role_run_id: The role run ID returned by ``_internal_role_start``.
        timeout_seconds: Maximum seconds to wait (default 1800).
        poll_interval_seconds: Seconds between status checks (default 15).
        return_result: If true, inline the control summary (default true).

    Returns:
        On success:
        {
            "status": "completed" | "running" | "failed",
            "role_run_id": "...",
            "control_summary": {...},  // if return_result=true and completed
            "artifacts": {...}          // if return_result=true and completed
        }
        On failure:
        {"status": "failed", "error": {...}}
    """
    # Normalize inputs
    try:
        normalized_role_run_id = normalize_role_run_id(role_run_id)
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

    normalized_return_result = normalize_bool(return_result, default=True)

    # Load role run record
    role_store = _role_tools._get_role_store()
    role_run = role_store.get_role_run(normalized_role_run_id)

    if role_run is None:
        return {
            "status": "failed",
            "error": {
                "type": "UnknownRoleRunId",
                "message": f"No role run found for role_run_id='{normalized_role_run_id}'.",
                "retryable": False,
            },
        }

    current_status = role_run.get("status", "unknown")

    # If already completed, return the result directly
    if current_status == "completed" and normalized_return_result:
        # Delegate to _internal_role_result for the full response shape
        return _internal_role_result(
            role_run_id=normalized_role_run_id,
            include_full_artifacts=False,
            return_control_summary=True,
        )

    # If not completed, poll using legacy wait (it handles OpenHands task polling)
    # This path is rarely taken because start_v2 is synchronous
    normalized_timeout = normalize_int(timeout_seconds, default=None)
    normalized_poll_interval = normalize_int(poll_interval_seconds, default=None)

    return _role_tools.role_wait_impl(
        role_run_id=normalized_role_run_id,
        timeout_seconds=normalized_timeout,
        poll_interval_seconds=normalized_poll_interval,
        return_result=normalized_return_result,
    )


def _internal_role_result(
    role_run_id: Any,
    include_full_artifacts: Any = None,
    return_control_summary: Any = None,
) -> dict:
    """Get result for a role run.

    **Internal helper only** — not exposed as an MCP tool.
    **Deprecated.** Use ``role_call`` instead.

    Returns control summary inline and artifact references (not content).

    Args:
        role_run_id: The role run ID returned by ``_internal_role_start``.
        include_full_artifacts: If true, include full artifact content.
        return_control_summary: If true, include the control summary.

    Returns:
        A dict with ``role_run_id``, ``status``, ``control_summary``,
        and ``artifacts`` (artifact references, optionally with content).
    """
    # Normalize inputs
    try:
        normalized_role_run_id = normalize_role_run_id(role_run_id)
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

    normalized_include_full = normalize_bool(include_full_artifacts, default=False)
    normalized_return_control = normalize_bool(return_control_summary, default=True)

    # Get role run record
    role_store = _role_tools._get_role_store()
    role_run = role_store.get_role_run(normalized_role_run_id)

    if role_run is None:
        return {
            "status": "failed",
            "error": {
                "type": "UnknownRoleRunId",
                "message": f"No role run found for role_run_id='{normalized_role_run_id}'.",
                "retryable": False,
            },
        }

    # Build base response
    run_id = role_run.get("run_id", "")
    result: dict[str, Any] = {
        "role_run_id": normalized_role_run_id,
        "run_id": run_id,
        "role": role_run.get("role"),
        "status": role_run.get("status", "unknown"),
    }

    # Parse result_summary if present (JSON string)
    control_summary = None
    if normalized_return_control and role_run.get("result_summary"):
        try:
            control_summary = json.loads(role_run["result_summary"])
        except (json.JSONDecodeError, TypeError):
            control_summary = {"raw": role_run["result_summary"]}

    if control_summary is not None:
        result["control_summary"] = control_summary

    # Add artifact paths
    artifact_store = ArtifactStore()
    artifacts_list = artifact_store.list(run_id) if run_id else []

    # Determine the expected summary artifact name from the role spec.
    # Falls back to _summary suffix if the role is unknown or has no
    # summary_artifact field.
    summary_artifact_name: str | None = None
    try:
        role_spec = get_role(role_run.get("role", ""))
        if role_spec and hasattr(role_spec, "summary_artifact"):
            summary_artifact_name = role_spec.summary_artifact
    except Exception:
        pass

    artifacts_result: dict[str, Any] = {}

    # Prefer artifact refs stored in the role run record (exact paths).
    # Fall back to scanning artifacts_list when the role run has no
    # stored artifact refs.
    stored_artifacts = None
    if role_run.get("artifacts"):
        try:
            stored_artifacts = json.loads(role_run["artifacts"])
        except (json.JSONDecodeError, TypeError):
            stored_artifacts = None

    if stored_artifacts and isinstance(stored_artifacts, dict):
        # Use stored artifact refs directly (exact paths from role result)
        for key in ("primary", "summary", "primaries"):
            if key in stored_artifacts:
                artifacts_result[key] = stored_artifacts[key]
    else:
        # Fallback: scan all artifacts in the run and classify them
        primary_artifacts: list[dict[str, str]] = []
        for art in artifacts_list:
            art_name = art.get("artifact_name", "")
            is_summary = (
                summary_artifact_name is not None
                and art_name == summary_artifact_name
            ) or (
                summary_artifact_name is None and art_name.endswith("_summary")
            )
            if is_summary:
                artifacts_result["summary"] = {
                    "artifact_name": art_name,
                    "artifact_path": art.get("artifact_path"),
                    "artifact_id": art.get("artifact_id", ""),
                }
            else:
                primary_artifacts.append({
                    "artifact_name": art_name,
                    "artifact_path": art.get("artifact_path"),
                    "artifact_id": art.get("artifact_id", ""),
                })
        # Store first primary for backward compatibility; keep all in a list
        if primary_artifacts:
            artifacts_result["primary"] = primary_artifacts[0]
            if len(primary_artifacts) > 1:
                artifacts_result["primaries"] = primary_artifacts

    # Ensure artifact_id is present in artifacts (backward compat)
    for key in ("primary", "summary"):
        if key in artifacts_result and isinstance(artifacts_result[key], dict):
            if "artifact_id" not in artifacts_result[key]:
                artifacts_result[key]["artifact_id"] = ""

    result["artifacts"] = artifacts_result

    # Optionally include full artifact content (exact path resolution)
    if normalized_include_full:
        warnings_list: list[str] = []

        # Determine which artifact entries to load content for
        artifacts_to_load: list[tuple[str, dict]] = []

        # Summary artifact
        if "summary" in artifacts_result:
            artifacts_to_load.append(("summary", artifacts_result["summary"]))

        # Primary artifact (first primary)
        if "primary" in artifacts_result:
            artifacts_to_load.append(("primary", artifacts_result["primary"]))

        # Additional primaries (if >1)
        if "primaries" in artifacts_result:
            for i, prim_entry in enumerate(artifacts_result["primaries"]):
                artifacts_to_load.append((f"primaries[{i}]", prim_entry))

        # Load content by exact path for each artifact entry
        for label, art_entry in artifacts_to_load:
            art_name = art_entry.get("artifact_name", "")
            art_path = art_entry.get("artifact_path", "")

            if not art_path:
                warnings_list.append(
                    f"missing artifact_path for {label} {art_name}"
                )
                continue

            try:
                full_meta = artifact_store.get_by_path(art_path)
                full_content = full_meta.get("content", "")

                # Validate artifact_name matches
                loaded_name = full_meta.get("artifact_name", "")
                if loaded_name and art_name and loaded_name != art_name:
                    warnings_list.append(
                        f"artifact name mismatch while loading full artifact "
                        f"{label}: expected {art_name}, got {loaded_name}"
                    )
                    # Still attach content but warn

                # Attach content to the artifact metadata object
                art_entry["content"] = full_content

                # Also set top-level key for backward compatibility
                if label == "summary":
                    result.setdefault("summary_artifact", full_content)
                elif label == "primary":
                    result.setdefault("primary_artifact", full_content)
                # primaries entries don't get top-level keys (no compat precedent)

            except (ValueError, FileNotFoundError) as exc:
                warnings_list.append(
                    f"failed to load full artifact {label} {art_name} at "
                    f"{art_path}: {exc}"
                )

        if warnings_list:
            result["warnings"] = warnings_list

    return result


# ---------------------------------------------------------------------------
# Public MCP tools for Head of IT
# ---------------------------------------------------------------------------


@MCP.tool()
def role_list() -> dict:
    """List available roles and their contracts for ``role_call``.

    Returns a minimal ``roles`` list with the fields Head of IT needs:
    name, readonly, requires_artifacts, output_artifact_type.

    Example::

        {
            "roles": [
                {
                    "name": "scout",
                    "readonly": true,
                    "requires_artifacts": [],
                    "output_artifact_type": "scout_report"
                },
                {
                    "name": "architect",
                    "readonly": true,
                    "requires_artifacts": ["scout_report"],
                    "output_artifact_type": "architect_plan"
                }
            ]
        }
    """
    roles = list_roles()
    return {
        "roles": [
            {
                "name": r["name"],
                "readonly": r["readonly"],
                "requires_artifacts": r.get("requires_artifacts", []),
                "output_artifact_type": r.get("output_artifact", ""),
            }
            for r in roles
        ]
    }


# ---------------------------------------------------------------------------
# Mapping from artifact type name to the flat role_call field name
# ---------------------------------------------------------------------------

_ARTIFACT_FIELD_NAME_MAP: dict[str, str] = {
    "scout_report": "scout_report_artifact_id",
    "architect_plan": "architect_plan_artifact_id",
    "coder_report": "coder_report_artifact_id",
    "reviewer_report": "reviewer_report_artifact_id",
    "publisher_instructions": "publisher_instructions_artifact_id",
}


@MCP.tool()
def role_call(
    role: Any,
    user_task: Any,
    repository: Any = "",
    feature: Any = "",
    scout_report_artifact_id: Any = "",
    architect_plan_artifact_id: Any = "",
    coder_report_artifact_id: Any = "",
    reviewer_report_artifact_id: Any = "",
    publisher_instructions_artifact_id: Any = "",
    api_key: Any = None,
    llm_model: Any = None,
    url: Any = None,
    idempotency_key: Any = None,
) -> dict:
    """Start a specialist role and return quickly with ``role_run_id``.

    This is the **only** public tool Head of IT uses to invoke a worker
    role.  It starts the role and returns immediately with
    ``status: "running"`` and ``role_run_id`` — it does **NOT** wait
    for completion.

    Use ``role_wait`` with the returned ``role_run_id`` to poll and
    wait for the final ``control_summary`` plus ``artifact_id``
    references.

    **All fields are plain scalar values.** Do NOT pass nested dicts or lists.

    Parameters
    ----------
    role : str
        The role name (e.g. ``"scout"``, ``"architect"``, ``"coder"``).
    user_task : str
        The user task text. Required and must be non-empty.
    repository : str
        Repository URL (e.g. ``"https://github.com/..."``).
    feature : str
        Feature name (e.g. ``"ruby-grpc-client"``).
    scout_report_artifact_id : str
        Artifact ID of the scout report (e.g. ``"art_..."``).
    architect_plan_artifact_id : str
        Artifact ID of the architect plan.
    coder_report_artifact_id : str
        Artifact ID of the coder report.
    reviewer_report_artifact_id : str
        Artifact ID of the reviewer report.
    publisher_instructions_artifact_id : str
        Artifact ID of the publisher instructions.
    api_key : str
        OpenHands API key.
    llm_model : str
        LLM model override.
    url : str
        OpenHands LLM base URL override.
    idempotency_key : str
        Optional stable key to deduplicate retried calls.

    Returns
    -------
    dict
        **Running** (new role started)::

            {
                "status": "running",
                "role_run_id": "...",
                "run_id": "...",
                "role": "...",
                "conversation_id": "...",
                "message": "Role started. Use role_wait with role_run_id."
            }

        **Dedup hit** (existing run)::

            {
                "status": "running" | "completed" | "failed",
                "role_run_id": "...",
                "run_id": "...",
                "role": "...",
                "_idempotent": True,
                ...
            }

        **Error** (validation failure)::

            {
                "status": "failed",
                "error": {"type": "...", "message": "...", "retryable": bool}
            }

    Example — scout::

        {
            "role": "scout",
            "user_task": "Research repo",
            "repository": "https://github.com/...",
            "feature": "ruby-grpc-client",
            "idempotency_key": "ruby-grpc-client-scout"
        }

    Example — architect::

        {
            "role": "architect",
            "user_task": "Plan Ruby client",
            "repository": "https://github.com/...",
            "feature": "ruby-grpc-client",
            "scout_report_artifact_id": "art_20260608-xxx_scout_report",
            "idempotency_key": "ruby-grpc-client-architect"
        }
    """
    # ------------------------------------------------------------------
    # Debug logging: raw MCP input shape
    # ------------------------------------------------------------------
    if os.getenv("MCP_DEBUG_ROLE_CALL", "").lower() in {"1", "true", "yes"}:
        from .safe_logging import (
            DEBUG_ROLE_CALL,
            correlate_id_from_args,
            format_correlation,
            safe_json_shape,
            safe_preview,
        )

        if DEBUG_ROLE_CALL:
            corr_id = correlate_id_from_args(
                role_run_id=None, run_id=None, idempotency_key=idempotency_key
            )

            # Safe shape of role
            role_shape = safe_json_shape(role)
            role_type = role_shape.get("type", type(role).__name__) if isinstance(role_shape, dict) else type(role).__name__
            role_preview_val = safe_json_shape(role).get("preview", str(role)[:200]) if isinstance(safe_json_shape(role), dict) else str(role)[:200]

            # Safe shape of user_task (preview only, first 200 chars)
            ut_shape = safe_json_shape(user_task)
            ut_type = ut_shape.get("type", type(user_task).__name__) if isinstance(ut_shape, dict) else type(user_task).__name__
            ut_len = ut_shape.get("len", len(str(user_task))) if isinstance(ut_shape, dict) else len(str(user_task))

            # idempotency_key shape
            ik_shape = safe_json_shape(idempotency_key) if idempotency_key is not None else None
            ik_type = ik_shape.get("type", "NoneType") if ik_shape and isinstance(ik_shape, dict) else "NoneType"

            logger.info(
                "role_call.input %s role_type=%s role_preview=%s user_task_type=%s user_task_len=%d idempotency_key_type=%s",
                format_correlation(corr_id, role=str(role)[:50]),
                role_type, safe_preview(str(role_preview_val), 100),
                ut_type, ut_len,
                ik_type,
            )

    # ------------------------------------------------------------------
    # Unwrap all scalar fields
    # ------------------------------------------------------------------
    normalized_role = normalize_role(role)
    normalized_user_task = unwrap_text(user_task)
    normalized_repository = unwrap_scalar(repository) or ""
    normalized_feature = unwrap_scalar(feature) or ""
    normalized_scout_report = unwrap_scalar(scout_report_artifact_id) or ""
    normalized_architect_plan = unwrap_scalar(architect_plan_artifact_id) or ""
    normalized_coder_report = unwrap_scalar(coder_report_artifact_id) or ""
    normalized_reviewer_report = unwrap_scalar(reviewer_report_artifact_id) or ""
    normalized_publisher_instructions = unwrap_scalar(publisher_instructions_artifact_id) or ""
    normalized_api_key = unwrap_text(api_key) if api_key is not None else ""
    normalized_llm_model = unwrap_text(llm_model) if llm_model is not None else None
    normalized_url = unwrap_text(url) if url is not None else None
    normalized_idempotency_key = unwrap_scalar(idempotency_key) or ""

    # ------------------------------------------------------------------
    # Validate artifact ID format (if provided, must start with "art_")
    # ------------------------------------------------------------------
    artifact_id_fields = {
        "scout_report_artifact_id": normalized_scout_report,
        "architect_plan_artifact_id": normalized_architect_plan,
        "coder_report_artifact_id": normalized_coder_report,
        "reviewer_report_artifact_id": normalized_reviewer_report,
        "publisher_instructions_artifact_id": normalized_publisher_instructions,
    }

    for field_name, artifact_id in artifact_id_fields.items():
        if artifact_id and not str(artifact_id).startswith("art_"):
            return {
                "status": "failed",
                "error": {
                    "type": "InvalidArtifactId",
                    "message": f"{field_name} must be an artifact id like art_..., got {artifact_id!r}",
                    "retryable": False,
                },
            }

    # ------------------------------------------------------------------
    # Detect bad nested payload (Test 9 — Option B)
    # ------------------------------------------------------------------
    if isinstance(role, dict) and "input_artifacts" in role:
        return {
            "status": "failed",
            "error": {
                "type": "InvalidFlatRoleCallPayload",
                "message": (
                    "role_call now uses flat scalar fields. Do not pass nested "
                    "input_artifacts or metadata. Pass artifact ids in dedicated "
                    "fields: scout_report_artifact_id, architect_plan_artifact_id, "
                    "coder_report_artifact_id, reviewer_report_artifact_id, "
                    "publisher_instructions_artifact_id."
                ),
                "retryable": False,
            },
        }

    # ------------------------------------------------------------------
    # Build internal input_artifacts dict from flat fields
    # ------------------------------------------------------------------
    internal_input_artifacts: dict[str, str] = {}
    if normalized_scout_report:
        internal_input_artifacts["scout_report"] = str(normalized_scout_report)
    if normalized_architect_plan:
        internal_input_artifacts["architect_plan"] = str(normalized_architect_plan)
    if normalized_coder_report:
        internal_input_artifacts["coder_report"] = str(normalized_coder_report)
    if normalized_reviewer_report:
        internal_input_artifacts["reviewer_report"] = str(normalized_reviewer_report)
    if normalized_publisher_instructions:
        internal_input_artifacts["publisher_instructions"] = str(normalized_publisher_instructions)

    # ------------------------------------------------------------------
    # Build internal metadata dict from flat fields
    # ------------------------------------------------------------------
    internal_metadata: dict[str, str] = {}
    if normalized_repository:
        internal_metadata["repository"] = str(normalized_repository)
    if normalized_feature:
        internal_metadata["feature"] = str(normalized_feature)

    # Import and call the lifecycle implementation (start-only, non-blocking)
    from . import role_lifecycle

    return role_lifecycle.role_call_start_impl(
        role=normalized_role,
        user_task=str(normalized_user_task) if normalized_user_task else "",
        input_artifacts=internal_input_artifacts,
        metadata=internal_metadata,
        api_key=str(normalized_api_key) if normalized_api_key else "",
        llm_model=str(normalized_llm_model) if normalized_llm_model else None,
        url=str(normalized_url) if normalized_url else None,
        idempotency_key=str(normalized_idempotency_key) if normalized_idempotency_key else None,
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
