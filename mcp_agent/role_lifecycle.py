#!/usr/bin/env python3
"""Two-step role lifecycle with in-conversation summary.

Provides ``role_call_impl`` which implements the full two-step
lifecycle: main prompt → response → summary prompt → response →
validation → control summary returned inline.

Lifecycle state machine:

    created
    → main_prompt_rendered
    → main_prompt_sent
    → main_response_received
    → primary_artifact_saved
    → summary_prompt_sent
    → summary_response_received
    → summary_artifact_saved
    → completed

If summary parsing fails:

    summary_response_received
    → summary_parse_failed
    → summary_repair_prompt_sent
    → summary_repair_response_received
    → summary_artifact_saved
    → completed

If repair also fails, complete with a safe fallback summary.
"""

import hashlib
import json
import logging
import os
import time
from typing import Any, Optional

import requests

from .artifact_store import ArtifactStore, _generate_artifact_id
from .lock_manager import RoleLockManager
from .prompt_renderer import render_prompt
from .role_store import RoleRunStore, _generate_run_id, _generate_role_run_id
from .roles import get_role, list_roles
from .summary_validator import (
    derive_reviewer_action_from_main_artifact,
    repair_summary,
    safe_fallback_summary,
    validate_summary,
)

logger = logging.getLogger("openhands-mcp")


class ConversationStartError(Exception):
    """Raised when starting an OpenHands conversation fails.

    Contains the HTTP status code and response body (when available)
    to aid debugging of 4xx/5xx errors from the /v1/call_lm endpoint.
    """

    def __init__(self, message: str, status_code: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def _unwrap_text(value: Any) -> Any:
    """Unwrap MCP-style ``{"text": "..."}`` values.

    Mirror of ``unwrap_text()`` from server.py, kept local to avoid
    circular imports.
    """
    if isinstance(value, dict) and "text" in value and len(value) == 1:
        return value["text"]
    return value


def resolve_input_artifacts(
    input_artifacts: Any,
) -> dict[str, str]:
    """Normalize *input_artifacts* to a ``{artifact_type: artifact_id}`` dict.

    Accepts both the new list-of-objects format and the legacy dict format::

        # New (preferred):
        [{"artifact_id": "art_xxx", "artifact_type": "scout_report"}, ...]

        # Legacy (backward compat):
        {"scout_report": "art_xxx", ...}

    Returns a plain ``{artifact_type: artifact_id}`` dict.
    """
    if input_artifacts is None:
        return {}

    if isinstance(input_artifacts, list):
        result: dict[str, str] = {}
        for entry in input_artifacts:
            if not isinstance(entry, dict):
                continue
            aid = _unwrap_text(entry.get("artifact_id", ""))
            atype = _unwrap_text(entry.get("artifact_type", ""))
            if aid and atype:
                result[str(atype)] = str(aid)
        return result

    if isinstance(input_artifacts, dict):
        result: dict[str, str] = {}
        for k, v in input_artifacts.items():
            result[k] = str(_unwrap_text(v)) if v is not None else ""
        return result

    # String — try JSON parse
    if isinstance(input_artifacts, str):
        try:
            parsed = json.loads(input_artifacts)
            return resolve_input_artifacts(parsed)
        except (json.JSONDecodeError, TypeError):
            return {}

    return {}


# Import from server module to avoid circular dependency at import time
# These are set at module load time from env vars
_OPENHANDS_URL = os.getenv("OPENHANDS_URL", "http://localhost:8000").rstrip("/")
_OPENHANDS_POLL_INTERVAL = int(
    os.getenv("OPENHANDS_POLL_INTERVAL_SECONDS", "10")
)
_OPENHANDS_MAX_RUNTIME = int(
    os.getenv("OPENHANDS_MAX_RUNTIME_SECONDS", "7200")
)
_OPENHANDS_REQUEST_TIMEOUT = int(
    os.getenv("OPENHANDS_REQUEST_TIMEOUT_SECONDS", "60")
)


def _start_conversation_on_fastapi(
    prompt: str,
    api_key: str,
    llm_model: Optional[str] = None,
    conversation_id: Optional[str] = None,
    url: Optional[str] = None,
    max_polls: Optional[int] = None,
    _correlation_id: Optional[str] = None,
) -> dict[str, Any]:
    """POST /v1/call_lm with no_wait=True and return the response dict.

    Parameters
    ----------
    prompt :
        The task prompt for the agent.
    api_key :
        OpenHands API key.
    llm_model :
        LLM model override.
    conversation_id :
        If provided, resume an existing conversation.
    url :
        OpenHands LLM base URL override.
    max_polls :
        Optional per-request max poll count.
    _correlation_id :
        Optional correlation ID for diagnostic logging.

    Returns
    -------
    dict
        Response from the OpenHands API.
    """
    import requests

    base = (url or _OPENHANDS_URL).rstrip("/")

    # Clamp max_polls to backend's le=360 constraint (CallLMRequest).
    computed_max_polls = _OPENHANDS_MAX_RUNTIME // _OPENHANDS_POLL_INTERVAL
    effective_max_polls = max_polls or computed_max_polls
    clamped_max_polls = min(effective_max_polls, 360)

    payload: dict[str, Any] = {
        "prompt": prompt,
        "api_key": api_key,
        "no_wait": True,
        "poll_interval": _OPENHANDS_POLL_INTERVAL,
        "max_polls": clamped_max_polls,
    }
    if llm_model:
        payload["llm_model"] = llm_model
    if conversation_id:
        payload["conversation_id"] = conversation_id

    # ------------------------------------------------------------------
    # Debug logging: outgoing payload shape
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
            corr_id = _correlation_id or correlate_id_from_args()

            payload_keys = list(payload.keys())
            prompt_type = type(prompt).__name__
            prompt_len = len(prompt) if isinstance(prompt, str) else 0
            api_key_type = type(api_key).__name__ if api_key else "NoneType"
            api_key_present = bool(api_key)
            conv_id_present = bool(conversation_id)

            logger.info(
                "call_lm.request %s url=%s payload_keys=%s prompt_type=%s prompt_len=%d api_key_present=%s api_key_type=%s conversation_id_present=%s no_wait=%s no_wait_type=%s poll_interval=%s poll_interval_type=%s max_polls=%s max_polls_type=%s",
                format_correlation(corr_id),
                f"{base}/v1/call_lm",
                payload_keys,
                prompt_type, prompt_len,
                api_key_present, api_key_type,
                conv_id_present,
                True, type(True).__name__,
                _OPENHANDS_POLL_INTERVAL, type(_OPENHANDS_POLL_INTERVAL).__name__,
                clamped_max_polls,
                type(clamped_max_polls).__name__,
            )

    resp = requests.post(
        f"{base}/v1/call_lm",
        json=payload,
        timeout=_OPENHANDS_REQUEST_TIMEOUT,
    )

    # ------------------------------------------------------------------
    # Debug logging: error response body on HTTP errors
    # ------------------------------------------------------------------
    if resp.status_code >= 400:
        if os.getenv("MCP_DEBUG_ROLE_CALL", "").lower() in {"1", "true", "yes"}:
            from .safe_logging import (
                DEBUG_ROLE_CALL,
                correlate_id_from_args,
                format_correlation,
            )

            if DEBUG_ROLE_CALL:
                corr_id = _correlation_id or correlate_id_from_args()
                logger.error(
                    "call_lm.response_error %s status=%d body=%s",
                    format_correlation(corr_id),
                    resp.status_code,
                    resp.text[:4000],
                )

        # Raise with body included in message
        raise requests.HTTPError(
            f"HTTP {resp.status_code}; body={resp.text[:1000]}",
            response=resp,
        )

    # ------------------------------------------------------------------
    # Debug logging: success response shape
    # ------------------------------------------------------------------
    if os.getenv("MCP_DEBUG_ROLE_CALL", "").lower() in {"1", "true", "yes"}:
        from .safe_logging import (
            DEBUG_ROLE_CALL,
            correlate_id_from_args,
            format_correlation,
        )

        if DEBUG_ROLE_CALL:
            corr_id = _correlation_id or correlate_id_from_args()
            result = resp.json()
            resp_keys = list(result.keys()) if isinstance(result, dict) else []
            resp_status = result.get("status", "(none)") if isinstance(result, dict) else "(none)"
            task_id_present = bool(result.get("task_id")) if isinstance(result, dict) else False
            conv_id_present = bool(result.get("conversation_id")) if isinstance(result, dict) else False
            app_conv_id_present = bool(result.get("app_conversation_id")) if isinstance(result, dict) else False

            logger.info(
                "call_lm.response_ok %s status=%d response_keys=%s response_status=%s task_id_present=%s conversation_id_present=%s app_conversation_id_present=%s",
                format_correlation(corr_id),
                resp.status_code,
                resp_keys,
                resp_status,
                task_id_present,
                conv_id_present,
                app_conv_id_present,
            )

    resp.raise_for_status()
    return resp.json()


def _poll_task_status(
    task_id: str,
    url: Optional[str] = None,
    max_polls: Optional[int] = None,
) -> dict[str, Any]:
    """Poll OpenHands task status until completed or timeout.

    Parameters
    ----------
    task_id :
        The OpenHands task ID.
    url :
        OpenHands LLM base URL override.
    max_polls :
        Optional per-request max poll count.

    Returns
    -------
    dict
        The final task status/response dict.
    """
    import requests

    base = (url or _OPENHANDS_URL).rstrip("/")
    polls = 0
    max_p = max_polls or (
        _OPENHANDS_MAX_RUNTIME // _OPENHANDS_POLL_INTERVAL
    )

    while polls < max_p:
        polls += 1
        resp = requests.get(
            f"{base}/v1/jobs/{task_id}",
            timeout=_OPENHANDS_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status", "unknown")
        execution_status = data.get("execution_status")

        # Normalize terminal states from status field
        if status in ("completed", "failed", "cancelled", "timeout",
                       "canceled", "timed_out", "error",
                       "completed_empty_result"):
            data["status"] = status
            return data

        # Also check execution_status as an alternative completion signal
        if execution_status in ("finished", "success"):
            data["status"] = "completed"
            return data

        if execution_status in ("failed", "error"):
            data["status"] = "failed"
            return data

        if execution_status in ("cancelled", "canceled"):
            data["status"] = "cancelled"
            return data

        if execution_status in ("timeout", "timed_out"):
            data["status"] = "timeout"
            return data

        # Still running — wait
        import time
        time.sleep(_OPENHANDS_POLL_INTERVAL)

    return {
        "status": "timed_out",
        "message": "Role timed out waiting for OpenHands response.",
    }


def _get_task_status_once(
    task_id: str,
    *,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Make a single HTTP GET to /v1/jobs/{task_id} and return the response.

    Does **NOT** poll or wait.  Returns immediately with whatever status
    the OpenHands API returns.

    Parameters
    ----------
    task_id :
        The OpenHands task ID.
    base_url :
        OpenHands LLM base URL override.

    Returns
    -------
    dict
        The raw API response with a private ``_normalized_status`` key
        indicating the terminal/running state.
    """
    base = (base_url or _OPENHANDS_URL).rstrip("/")
    resp = requests.get(
        f"{base}/v1/jobs/{task_id}",
        timeout=_OPENHANDS_REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()

    # Normalize terminal states (same logic as _poll_task_status)
    status = data.get("status", "unknown")
    execution_status = data.get("execution_status")

    if status in ("completed", "failed", "cancelled", "timeout",
                  "canceled", "timed_out", "error",
                  "completed_empty_result"):
        data["_normalized_status"] = status
        return data
    if execution_status in ("finished", "success"):
        data["_normalized_status"] = "completed"
        return data
    if execution_status in ("failed", "error"):
        data["_normalized_status"] = "failed"
        return data
    if execution_status in ("cancelled", "canceled"):
        data["_normalized_status"] = "cancelled"
        return data
    if execution_status in ("timeout", "timed_out"):
        data["_normalized_status"] = "timeout"
        return data

    # Still running
    data["_normalized_status"] = "running"
    return data


def _to_public_artifact_ref(raw: dict, *, role: str) -> dict[str, Any]:
    """Sanitize an artifact metadata dict to a public reference.

    Strips internal fields like ``artifact_path`` and ``content``.
    """
    return {
        "artifact_id": raw.get("artifact_id"),
        "artifact_type": raw.get("artifact_type") or raw.get("artifact_name"),
        "created_by": raw.get("created_by") or role,
    }


def role_call_start_impl(
    role: str,
    user_task: str,
    input_artifacts: Optional[dict[str, Any]] = None,
    metadata: Optional[dict[str, Any]] = None,
    api_key: str = "",
    llm_model: Optional[str] = None,
    url: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> dict[str, Any]:
    """Start a role and return quickly with ``status: "running"``.

    This is the **non-blocking** half of the two-step pattern:
    ``role_call`` -> ``role_wait``.

    Parameters
    ----------
    role :
        The role name (e.g. ``"scout"``, ``"architect"``).
    user_task :
        The user task text. Must be non-empty.
    input_artifacts :
        Mapping of artifact names to their ID/path strings.
    metadata :
        Optional metadata dict (e.g. ``{"repository": "..."}``).
    api_key :
        OpenHands API key.
    llm_model :
        LLM model override.
    url :
        OpenHands LLM base URL override.
    idempotency_key :
        Optional stable key to deduplicate retried calls.

    Returns
    -------
    dict
        One of:

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
    """
    if input_artifacts is None:
        input_artifacts = {}
    if metadata is None:
        metadata = {}

    # Always normalize input_artifacts
    input_artifacts = resolve_input_artifacts(input_artifacts)

    # ------------------------------------------------------------------
    # Step 1: Validate role
    # ------------------------------------------------------------------
    try:
        role_spec = get_role(role)
    except KeyError:
        available = ", ".join(sorted(r["name"] for r in list_roles()))
        return {
            "status": "failed",
            "error": {
                "type": "UnknownRole",
                "message": f"unknown role: {role}",
                "retryable": False,
            },
        }

    # ------------------------------------------------------------------
    # Step 2: Validate user_task
    # ------------------------------------------------------------------
    if not user_task or not str(user_task).strip():
        return {
            "status": "failed",
            "error": {
                "type": "MissingUserTask",
                "message": "user_task is required",
                "retryable": False,
            },
        }

    # ------------------------------------------------------------------
    # Step 3: Validate required artifacts
    # ------------------------------------------------------------------
    missing = []
    for req_artifact in role_spec.requires_artifacts:
        if req_artifact not in input_artifacts:
            missing.append(req_artifact)
    if missing:
        return {
            "status": "failed",
            "error": {
                "type": "MissingRequiredArtifact",
                "message": f"missing required artifact: {missing[0]}",
                "retryable": False,
            },
        }

    # ------------------------------------------------------------------
    # Step 4: Resolve artifact references (server-side loading)
    # ------------------------------------------------------------------
    artifact_contents: dict[str, str] = {}
    artifact_store = ArtifactStore()
    for artifact_name, artifact_ref in input_artifacts.items():
        ref_str = str(artifact_ref) if not isinstance(artifact_ref, str) else artifact_ref

        content = None

        # --- Strategy 0: Resolve by artifact_id ---
        if ref_str.startswith("art_"):
            try:
                content = artifact_store.get_content_by_id(ref_str)
            except ValueError:
                pass

        # --- Strategy 1: Exact path resolution ---
        if content is None and "/" in ref_str and ref_str.endswith(".artifact"):
            try:
                meta = artifact_store.get_by_path(ref_str)
                if meta.get("artifact_name") != artifact_name:
                    return {
                        "status": "failed",
                        "error": {
                            "type": "ArtifactNameMismatch",
                            "message": f"artifact name mismatch: expected {artifact_name}, got {meta.get('artifact_name')}",
                            "retryable": False,
                        },
                    }
                content = meta["content"]
            except (ValueError, FileNotFoundError) as exc:
                return {
                    "status": "failed",
                    "error": {
                        "type": "ArtifactNotFound" if isinstance(exc, FileNotFoundError) else "ArtifactReadError",
                        "message": str(exc),
                        "retryable": False,
                    },
                }

        # --- Strategy 2: Fallback to logical name resolution ---
        if content is None:
            try:
                meta = artifact_store.get(
                    metadata.get("run_id", ""), artifact_name=artifact_name
                )
                if meta and not meta.get("content_empty", True):
                    content = meta["content"]
            except ValueError:
                pass

        if content is None:
            return {
                "status": "failed",
                "error": {
                    "type": "ArtifactNotFound",
                    "message": f"artifact not found: {artifact_name}",
                    "retryable": False,
                },
            }

        if not content.strip():
            return {
                "status": "failed",
                "error": {
                    "type": "ArtifactReadError",
                    "message": f"failed to read artifact: {artifact_name}",
                    "retryable": False,
                },
            }

        artifact_contents[artifact_name] = content

    # ------------------------------------------------------------------
    # Step 5: Render main prompt
    # ------------------------------------------------------------------
    template_vars: dict[str, Any] = {
        "user_task": user_task,
    }

    if metadata.get("repository"):
        template_vars["repo"] = str(metadata["repository"])
    if metadata.get("base_branch"):
        template_vars["base_branch"] = str(metadata["base_branch"])
    if metadata.get("branch"):
        template_vars["branch"] = str(metadata["branch"])
    if metadata.get("context"):
        template_vars["context"] = str(metadata["context"])

    for artifact_name, content_ref in artifact_contents.items():
        template_vars[artifact_name] = content_ref

    try:
        main_prompt = render_prompt(
            template_path=role_spec.prompt_template,
            variables=template_vars,
        )
    except FileNotFoundError:
        return {
            "status": "failed",
            "error": {
                "type": "TemplateNotFound",
                "message": f"Prompt template not found: {role_spec.prompt_template}",
                "retryable": False,
            },
        }

    if not main_prompt or not main_prompt.strip():
        return {
            "status": "failed",
            "error": {
                "type": "EmptyPrompt",
                "message": "rendered prompt is empty",
                "retryable": False,
            },
        }

    # ------------------------------------------------------------------
    # Step 6: Idempotency check + fallback dedupe
    # ------------------------------------------------------------------
    role_store = RoleRunStore()
    idempotency_scope = None
    dedupe_key = None

    if idempotency_key:
        idempotency_scope = f"{role}:{idempotency_key}"
        existing_role_run_id = role_store.find_by_idempotency_scope(idempotency_scope)
        if existing_role_run_id is not None:
            existing_run = role_store.get_role_run(existing_role_run_id)
            if existing_run is not None:
                existing_status = existing_run.get("status", "unknown")
                if existing_status == "completed":
                    control_summary = None
                    if existing_run.get("result_summary"):
                        try:
                            control_summary = json.loads(existing_run["result_summary"])
                        except (json.JSONDecodeError, TypeError):
                            control_summary = {"raw": existing_run["result_summary"]}
                    stored_artifacts = None
                    if existing_run.get("artifacts"):
                        try:
                            stored_artifacts = json.loads(existing_run["artifacts"])
                        except (json.JSONDecodeError, TypeError):
                            stored_artifacts = None
                    artifacts_result: dict[str, Any] = {}
                    if stored_artifacts and isinstance(stored_artifacts, dict):
                        for key in ("primary", "summary"):
                            if key in stored_artifacts:
                                artifacts_result[key] = _to_public_artifact_ref(
                                    stored_artifacts[key], role=role,
                                )
                    return {
                        "role_run_id": existing_run.get("role_run_id", ""),
                        "run_id": existing_run.get("run_id", ""),
                        "role": role,
                        "status": "completed",
                        "control_summary": control_summary or {},
                        "artifacts": artifacts_result if artifacts_result else {},
                        "_idempotent": True,
                    }
                return {
                    "role_run_id": existing_run.get("role_run_id", ""),
                    "run_id": existing_run.get("run_id", ""),
                    "role": role,
                    "status": existing_status,
                    "message": f"Idempotent key '{idempotency_key}' is already in use (status: {existing_status}).",
                    "_idempotent": True,
                }
    else:
        # Fallback dedupe (no idempotency_key provided)
        task_hash = hashlib.sha256(user_task.strip().encode()).hexdigest()[:16]
        repo = str(metadata.get("repository", ""))
        feature = str(metadata.get("feature", ""))
        dedupe_key = f"{role}:{task_hash}:{repo}:{feature}"
        existing_role_run_id = role_store.find_by_idempotency_scope(
            f"fallback:{dedupe_key}"
        )
        if existing_role_run_id is not None:
            existing_run = role_store.get_role_run(existing_role_run_id)
            if existing_run is not None:
                existing_status = existing_run.get("status", "unknown")
                if existing_status == "completed":
                    control_summary = None
                    if existing_run.get("result_summary"):
                        try:
                            control_summary = json.loads(existing_run["result_summary"])
                        except (json.JSONDecodeError, TypeError):
                            control_summary = {"raw": existing_run["result_summary"]}
                    stored_artifacts = None
                    if existing_run.get("artifacts"):
                        try:
                            stored_artifacts = json.loads(existing_run["artifacts"])
                        except (json.JSONDecodeError, TypeError):
                            stored_artifacts = None
                    artifacts_result: dict[str, Any] = {}
                    if stored_artifacts and isinstance(stored_artifacts, dict):
                        for key in ("primary", "summary"):
                            if key in stored_artifacts:
                                artifacts_result[key] = _to_public_artifact_ref(
                                    stored_artifacts[key], role=role,
                                )
                    return {
                        "role_run_id": existing_run.get("role_run_id", ""),
                        "run_id": existing_run.get("run_id", ""),
                        "role": role,
                        "status": "completed",
                        "control_summary": control_summary or {},
                        "artifacts": artifacts_result if artifacts_result else {},
                        "_idempotent": True,
                    }
                return {
                    "role_run_id": existing_run.get("role_run_id", ""),
                    "run_id": existing_run.get("run_id", ""),
                    "role": role,
                    "status": existing_status,
                    "message": "Duplicate role run detected for this task. Use existing run.",
                    "_idempotent": True,
                }

    # ------------------------------------------------------------------
    # Step 7: Create role run record with status=starting
    # ------------------------------------------------------------------
    run_id = _generate_run_id()
    attempt = role_store.get_attempt_count(run_id, role) + 1
    role_run_id = _generate_role_run_id(run_id, role, attempt)

    role_run = role_store.create_role_run(
        role=role,
        run_id=run_id,
        role_run_id=role_run_id,
        openhands_task_id="",  # Will be set after conversation start
        repo=metadata.get("repository"),
        base_branch=metadata.get("base_branch"),
        branch=metadata.get("branch"),
        artifact_name=role_spec.output_artifact,
        attempt=attempt,
    )
    role_store.update_role_run(role_run_id, status="starting")

    # ------------------------------------------------------------------
    # Step 8: Start OpenHands conversation (main prompt)
    # ------------------------------------------------------------------
    try:
        conv_response = _start_conversation_on_fastapi(
            prompt=main_prompt,
            api_key=api_key or os.getenv("OPENHANDS_API_KEY", ""),
            llm_model=llm_model,
            url=url,
        )
    except Exception as exc:
        role_store.update_role_run(
            role_run_id,
            status="failed",
            lifecycle_state="failed",
            error=json.dumps({
                "type": "ConversationStartError",
                "message": f"Failed to start OpenHands conversation: {exc}",
            }, ensure_ascii=False),
        )
        error_type = "ConversationStartError"
        error_msg = f"Failed to start OpenHands conversation: {exc}"
        if isinstance(exc, ConversationStartError):
            error_msg = f"Failed to start OpenHands conversation: HTTP {exc.status_code}; body={exc.body}"
        elif isinstance(exc, requests.HTTPError) and hasattr(exc, "response") and exc.response is not None:
            error_msg = f"Failed to start OpenHands conversation: HTTP {exc.response.status_code}; body={exc.response.text[:1000]}"

        return {
            "status": "failed",
            "error": {
                "type": error_type,
                "message": error_msg,
                "retryable": True,
            },
        }

    # Unified job_id
    job_id = (
        conv_response.get("task_id")
        or conv_response.get("conversation_id")
        or conv_response.get("id")
        or conv_response.get("app_conversation_id")
        or ""
    )

    if not job_id:
        role_store.update_role_run(
            role_run_id,
            status="failed",
            lifecycle_state="failed",
        )
        return {
            "status": "failed",
            "error": {
                "type": "MissingJobId",
                "message": "OpenHands response did not include task_id/conversation_id/id/app_conversation_id",
                "retryable": False,
            },
        }

    conversation_id = (
        conv_response.get("conversation_id")
        or conv_response.get("id")
        or conv_response.get("app_conversation_id")
        or job_id
    )

    # Update with running state AFTER successful start
    role_store.update_role_run(
        role_run_id,
        openhands_task_id=job_id,
        status="running",
        lifecycle_state="main_prompt_sent",
        conversation_id=conversation_id,
    )

    # Save idempotency record AFTER successful start
    if idempotency_key:
        role_store.save_idempotency_record(idempotency_scope, role_run_id)
    elif dedupe_key:
        role_store.save_idempotency_record(f"fallback:{dedupe_key}", role_run_id)

    # ------------------------------------------------------------------
    # Return "running" response - do NOT wait
    # ------------------------------------------------------------------
    return {
        "status": "running",
        "role_run_id": role_run_id,
        "run_id": run_id,
        "role": role,
        "conversation_id": conversation_id,
        "message": "Role started. Use role_wait with role_run_id to wait for completion.",
    }


def role_lifecycle_wait_impl(
    role_run_id: str,
    timeout_seconds: Optional[int] = None,
    poll_interval_seconds: Optional[int] = None,
) -> dict[str, Any]:
    """Wait for a role run to complete (polling + summary).

    This is the **blocking** half of the two-step pattern.

    Parameters
    ----------
    role_run_id :
        The role run ID returned by ``role_call_start_impl``.
    timeout_seconds :
        Maximum seconds to wait (default 1800).
    poll_interval_seconds :
        Seconds between status checks (default 30).

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

        **Timeout**::

            {
                "status": "running",
                "role_run_id": "...",
                "run_id": "...",
                "role": "...",
                "timeout": True,
                "message": "Role is still running. Call role_wait again."
            }

        **Failed**::

            {
                "status": "failed",
                "role_run_id": "...",
                "run_id": "...",
                "role": "...",
                "error": {"type": "...", "message": "...", "retryable": bool}
            }
    """
    # ------------------------------------------------------------------
    # Normalize and bound arguments (Blocker 5)
    # ------------------------------------------------------------------
    _raw_timeout = _unwrap_text(timeout_seconds) if timeout_seconds is not None else None
    _raw_poll = _unwrap_text(poll_interval_seconds) if poll_interval_seconds is not None else None

    if _raw_timeout is None:
        _raw_timeout = int(os.getenv("OPENHANDS_ROLE_WAIT_TIMEOUT_SECONDS", "1800"))
    if _raw_poll is None:
        _raw_poll = int(os.getenv("OPENHANDS_ROLE_WAIT_POLL_INTERVAL_SECONDS", "30"))

    timeout_seconds = max(1, min(int(_raw_timeout), 24 * 3600))
    poll_interval_seconds = max(1, min(int(_raw_poll), 300))

    role_store = RoleRunStore()
    role_run = role_store.get_role_run(role_run_id)

    if role_run is None:
        return {
            "status": "failed",
            "role_run_id": role_run_id,
            "error": {
                "type": "RoleRunNotFound",
                "message": f"role_run_id '{role_run_id}' not found",
                "retryable": False,
            },
        }

    # Idempotent: if already completed with summary, return existing result
    if role_run.get("status") == "completed" and role_run.get("result_summary"):
        control_summary = None
        if role_run.get("result_summary"):
            try:
                control_summary = json.loads(role_run["result_summary"])
            except (json.JSONDecodeError, TypeError):
                control_summary = {"raw": role_run["result_summary"]}
        stored_artifacts = None
        if role_run.get("artifacts"):
            try:
                stored_artifacts = json.loads(role_run["artifacts"])
            except (json.JSONDecodeError, TypeError):
                stored_artifacts = None
        artifacts_result: dict[str, Any] = {}
        if stored_artifacts and isinstance(stored_artifacts, dict):
            for key in ("primary", "summary"):
                if key in stored_artifacts:
                    artifacts_result[key] = _to_public_artifact_ref(
                        stored_artifacts[key], role=role_run.get("role", ""),
                    )
        return {
            "status": "completed",
            "role_run_id": role_run.get("role_run_id", ""),
            "run_id": role_run.get("run_id", ""),
            "role": role_run.get("role", ""),
            "control_summary": control_summary or {},
            "artifacts": artifacts_result if artifacts_result else {},
        }

    # If already failed, return the error
    if role_run.get("status") == "failed":
        return {
            "status": "failed",
            "role_run_id": role_run_id,
            "run_id": role_run.get("run_id", ""),
            "role": role_run.get("role", ""),
            "error": {
                "type": "RoleFailed",
                "message": f"Role run ended with status: {role_run.get('status', 'unknown')}",
                "retryable": True,
            },
        }

    # Get conversation_id for same-conversation summary
    conversation_id = role_run.get("conversation_id", "") or ""

    # Poll until terminal or timeout (Blocker 1: use time.monotonic)
    deadline = time.monotonic() + timeout_seconds
    job_id = role_run.get("openhands_task_id", "")

    if not job_id:
        return {
            "status": "failed",
            "role_run_id": role_run_id,
            "error": {
                "type": "MissingJobId",
                "message": "No OpenHands task_id found in role run record",
                "retryable": False,
            },
        }

    # ------------------------------------------------------------------
    # Poll for main response (Blocker 1: use _get_task_status_once)
    # ------------------------------------------------------------------
    while time.monotonic() < deadline:
        main_response_data = _get_task_status_once(job_id)
        main_status = main_response_data.get("_normalized_status",
                                              main_response_data.get("status", "unknown"))

        if main_status in ("completed", "completed_empty_result"):
            break
        elif main_status in ("failed", "error", "cancelled"):
            role_store.update_role_run(
                role_run_id,
                status=main_status,
                lifecycle_state="error",
            )
            return {
                "status": "failed",
                "role_run_id": role_run_id,
                "run_id": role_run.get("run_id", ""),
                "role": role_run.get("role", ""),
                "error": {
                    "type": "MainResponseError",
                    "message": f"Main response ended with status: {main_status}",
                    "retryable": True,
                },
            }

        # Still running — wait
        if time.monotonic() >= deadline:
            break
        time.sleep(poll_interval_seconds)
    else:
        # Timeout
        return {
            "status": "running",
            "role_run_id": role_run_id,
            "run_id": role_run.get("run_id", ""),
            "role": role_run.get("role", ""),
            "timeout": True,
            "message": "Role is still running. Call role_wait again with the same role_run_id.",
        }

    main_response = main_response_data.get("answer", "") or ""

    role_store.update_role_run(
        role_run_id,
        lifecycle_state="main_response_received",
    )

    # ------------------------------------------------------------------
    # Save primary artifact
    # ------------------------------------------------------------------
    artifact_store = ArtifactStore()
    primary_meta = artifact_store.save(
        run_id=role_run.get("run_id", ""),
        role_run_id=role_run_id,
        role=role_run.get("role", ""),
        artifact_name=role_run.get("artifact_name", "unknown"),
        content=main_response,
    )

    if primary_meta is None:
        return {
            "status": "failed",
            "role_run_id": role_run_id,
            "error": {
                "type": "ArtifactSaveError",
                "message": "Failed to save primary artifact.",
                "retryable": True,
            },
        }

    primary_artifact_path = primary_meta["artifact_path"]
    primary_artifact_id = primary_meta.get("artifact_id", "")

    # ------------------------------------------------------------------
    # Check if summary was already done (idempotent role_wait)
    # ------------------------------------------------------------------
    if role_run.get("lifecycle_state") in ("completed", "summary_artifact_saved"):
        control_summary = None
        if role_run.get("result_summary"):
            try:
                control_summary = json.loads(role_run["result_summary"])
            except (json.JSONDecodeError, TypeError):
                control_summary = {"raw": role_run["result_summary"]}
        stored_artifacts = None
        if role_run.get("artifacts"):
            try:
                stored_artifacts = json.loads(role_run["artifacts"])
            except (json.JSONDecodeError, TypeError):
                stored_artifacts = None
        artifacts_result: dict[str, Any] = {}
        if stored_artifacts and isinstance(stored_artifacts, dict):
            for key in ("primary", "summary"):
                if key in stored_artifacts:
                    artifacts_result[key] = _to_public_artifact_ref(
                        stored_artifacts[key], role=role_run.get("role", ""),
                    )
        return {
            "status": "completed",
            "role_run_id": role_run.get("role_run_id", ""),
            "run_id": role_run.get("run_id", ""),
            "role": role_run.get("role", ""),
            "control_summary": control_summary or {},
            "artifacts": artifacts_result if artifacts_result else {},
        }

    # ------------------------------------------------------------------
    # Render and send summary prompt (same conversation)
    # ------------------------------------------------------------------
    role = role_run.get("role", "")
    from .roles import get_role as _get_role
    try:
        role_spec = _get_role(role)
    except KeyError:
        role_spec = None

    role_store.update_role_run(
        role_run_id,
        lifecycle_state="summary_prompt_sent",
    )

    try:
        summary_prompt = render_prompt(
            template_path="prompts/summaries/role_summary.md",
            variables={
                "role": role,
                "primary_artifact_name": role_spec.output_artifact if role_spec else "unknown",
            },
        )
    except FileNotFoundError:
        summary_prompt = (
            "Summarize your previous answer for the orchestrator.\n\n"
            "Return compact JSON only.\n"
            "Do not include Markdown.\n"
            "Do not include code blocks.\n"
            "Do not decide the next role.\n"
            "Do not include routing advice.\n\n"
            'Schema:\n'
            '{"status": "completed" | "blocked", '
            '"role": "%s", '
            '"summary": "<short factual summary>", '
            '"primary_artifact_name": "unknown", '
            '"blocking": true | false, '
            '"risk_level": "LOW" | "MEDIUM" | "HIGH" | null, '
            '"action": "PASS" | "BLOCKER" | null, '
            '"blocking_summary": ["..."]}\n\n'
            "Rules:\n"
            "- Only reviewer may set action to PASS or BLOCKER.\n"
            "- Non-reviewer roles must set action to null.\n"
            "- Do not include next_role.\n"
            "- Do not include ready_for_next_role.\n"
        ) % role

    try:
        summary_conv_response = _start_conversation_on_fastapi(
            prompt=summary_prompt,
            api_key=os.getenv("OPENHANDS_API_KEY", ""),
            conversation_id=conversation_id if conversation_id else None,
        )
    except Exception:
        # Save fallback summary artifact so repeated role_wait is idempotent (Blocker 2)
        fallback_content = json.dumps(safe_fallback_summary(
            role=role,
            summary_artifact_name=(role_spec.summary_artifact if role_spec else "control_summary"),
            is_reviewer=(role == "reviewer"),
            main_artifact_content=main_response,
        ), ensure_ascii=False)

        fallback_meta = artifact_store.save(
            run_id=role_run.get("run_id", ""),
            role_run_id=role_run_id,
            role=role,
            artifact_name=(role_spec.summary_artifact if role_spec else "control_summary"),
            content=fallback_content,
        )
        fallback_artifact_id = (fallback_meta.get("artifact_id", "") if fallback_meta else "")

        control_summary = json.loads(fallback_content)

        role_store.update_role_run(
            role_run_id,
            status="completed",
            result_summary=json.dumps(control_summary, ensure_ascii=False),
            lifecycle_state="completed",
            artifacts=json.dumps({
                "primary": {
                    "artifact_name": (role_spec.output_artifact if role_spec else "unknown"),
                    "artifact_id": primary_artifact_id,
                },
                "summary": {
                    "artifact_name": (role_spec.summary_artifact if role_spec else "control_summary"),
                    "artifact_id": fallback_artifact_id,
                },
            }, ensure_ascii=False),
        )

        return {
            "status": "completed",
            "role_run_id": role_run_id,
            "run_id": role_run.get("run_id", ""),
            "role": role,
            "control_summary": control_summary,
            "artifacts": {
                "primary": _to_public_artifact_ref({
                    "artifact_id": primary_artifact_id,
                    "artifact_type": (role_spec.output_artifact if role_spec else "unknown"),
                }, role=role),
                "summary": _to_public_artifact_ref({
                    "artifact_id": fallback_artifact_id,
                    "artifact_type": (role_spec.summary_artifact if role_spec else "control_summary"),
                }, role=role),
            },
        }

    summary_job_id = (
        summary_conv_response.get("task_id")
        or summary_conv_response.get("conversation_id")
        or summary_conv_response.get("id")
        or summary_conv_response.get("app_conversation_id")
        or ""
    )
    if not summary_job_id:
        summary_job_id = conversation_id or "unknown"

    # ------------------------------------------------------------------
    # Wait for summary response (use _get_task_status_once for consistency)
    # ------------------------------------------------------------------
    summary_response_data = _get_task_status_once(summary_job_id)
    summary_text = summary_response_data.get("answer", "") or ""

    role_store.update_role_run(
        role_run_id,
        lifecycle_state="summary_response_received",
    )

    # ------------------------------------------------------------------
    # Validate/parse summary
    # ------------------------------------------------------------------
    control_summary = validate_summary(
        role=role,
        summary_artifact_name=(role_spec.output_artifact if role_spec else "unknown"),
        json_str=summary_text,
    )

    if not control_summary.get("valid"):
        role_store.update_role_run(
            role_run_id,
            lifecycle_state="summary_parse_failed",
        )

        repair_prompt_text = repair_summary(
            role=role,
            summary_artifact_name=(role_spec.output_artifact if role_spec else "unknown"),
        )

        role_store.update_role_run(
            role_run_id,
            lifecycle_state="summary_repair_prompt_sent",
        )

        try:
            repair_conv_response = _start_conversation_on_fastapi(
                prompt=repair_prompt_text,
                api_key=os.getenv("OPENHANDS_API_KEY", ""),
                conversation_id=conversation_id if conversation_id else None,
            )
        except Exception:
            repair_conv_response = None

        if repair_conv_response:
            repair_job_id = (
                repair_conv_response.get("task_id")
                or repair_conv_response.get("conversation_id")
                or repair_conv_response.get("id")
                or repair_conv_response.get("app_conversation_id")
                or ""
            )
            if not repair_job_id:
                repair_job_id = conversation_id or "unknown"
            repair_response_data = _get_task_status_once(repair_job_id)
            repair_text = repair_response_data.get("answer", "") or ""
            role_store.update_role_run(
                role_run_id,
                lifecycle_state="summary_repair_response_received",
            )

            control_summary = validate_summary(
                role=role,
                summary_artifact_name=(role_spec.output_artifact if role_spec else "unknown"),
                json_str=repair_text,
            )

    # ------------------------------------------------------------------
    # If still invalid, use safe fallback
    # ------------------------------------------------------------------
    if not control_summary.get("valid"):
        control_summary = safe_fallback_summary(
            role=role,
            summary_artifact_name=(role_spec.summary_artifact if role_spec else "control_summary"),
            is_reviewer=(role == "reviewer"),
            main_artifact_content=main_response,
        )

    # ------------------------------------------------------------------
    # Save summary artifact
    # ------------------------------------------------------------------
    summary_meta = artifact_store.save(
        run_id=role_run.get("run_id", ""),
        role_run_id=role_run_id,
        role=role,
        artifact_name=(role_spec.summary_artifact if role_spec else "control_summary"),
        content=summary_text,
    )

    if summary_meta is None:
        summary_artifact_id = _generate_artifact_id(
            role_run.get("run_id", ""), role, 1,
            (role_spec.summary_artifact if role_spec else "control_summary")
        )
        summary_artifact_path = ""
    else:
        summary_artifact_id = summary_meta.get("artifact_id", "")
        summary_artifact_path = summary_meta.get("artifact_path", "")

    role_store.update_role_run(
        role_run_id,
        lifecycle_state="summary_artifact_saved",
    )

    # ------------------------------------------------------------------
    # Mark completed and persist artifact metadata
    # ------------------------------------------------------------------
    output_artifact_name = (role_spec.output_artifact if role_spec else "unknown")
    summary_artifact_name = (role_spec.summary_artifact if role_spec else "control_summary")

    role_store.update_role_run(
        role_run_id,
        status="completed",
        result_summary=json.dumps(control_summary),
        lifecycle_state="completed",
        artifacts=json.dumps({
            "primary": {
                "artifact_name": output_artifact_name,
                "artifact_id": primary_artifact_id,
                "artifact_path": primary_artifact_path,
            },
            "summary": {
                "artifact_name": summary_artifact_name,
                "artifact_id": summary_artifact_id,
                "artifact_path": summary_artifact_path,
            },
        }),
    )

    # ------------------------------------------------------------------
    # Return control summary (public response - no content, no artifact_path)
    # ------------------------------------------------------------------
    return {
        "status": "completed",
        "role_run_id": role_run_id,
        "run_id": role_run.get("run_id", ""),
        "role": role,
        "control_summary": control_summary,
        "artifacts": {
            "primary": {
                "artifact_id": primary_artifact_id,
                "artifact_type": output_artifact_name,
                "created_by": role,
            },
            "summary": {
                "artifact_id": summary_artifact_id,
                "artifact_type": summary_artifact_name,
                "created_by": role,
            },
        },
    }


def role_call_impl(
    role: str,
    user_task: str,
    input_artifacts: Optional[dict[str, Any]] = None,
    metadata: Optional[dict[str, Any]] = None,
    api_key: str = "",
    llm_model: Optional[str] = None,
    url: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> dict[str, Any]:
    """Two-step role lifecycle with in-conversation summary.

    **Deprecated**: Prefer ``role_call_start_impl`` + ``role_lifecycle_wait_impl``
    for the non-blocking two-step pattern.

    This function remains as a thin wrapper for backward compatibility:
    it calls ``role_call_start_impl`` then ``role_lifecycle_wait_impl``
    to preserve the existing blocking behavior for internal callers/tests.

    Parameters
    ----------
    role :
        The role name (e.g. ``"scout"``, ``"architect"``).
    user_task :
        The user task text. Must be non-empty.
    input_artifacts :
        Mapping of artifact names to their ID/path strings.
    metadata :
        Optional metadata dict (e.g. ``{"repository": "..."}``).
    api_key :
        OpenHands API key.
    llm_model :
        LLM model override.
    url :
        OpenHands LLM base URL override.
    idempotency_key :
        Optional stable key to deduplicate retried calls.

    Returns
    -------
    dict
        Control summary with ``artifact_id`` (not ``artifact_path``),
        or an error dict.
    """
    if input_artifacts is None:
        input_artifacts = {}
    if metadata is None:
        metadata = {}

    # Start the role (non-blocking)
    result = role_call_start_impl(
        role=role,
        user_task=user_task,
        input_artifacts=input_artifacts,
        metadata=metadata,
        api_key=api_key,
        llm_model=llm_model,
        url=url,
        idempotency_key=idempotency_key,
    )

    # If start returned "running", wait for completion
    if result.get("status") == "running" and not result.get("_idempotent"):
        role_run_id = result.get("role_run_id", "")
        if role_run_id:
            return role_lifecycle_wait_impl(
                role_run_id=role_run_id,
            )

    # Dedupe hit, error, or other non-running status - return as-is
    return result


# Backward-compatible alias for existing callers/tests.
# Prefer direct import of role_call_impl.
_role_call_impl_alias = role_call_impl

