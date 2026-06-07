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

import json
import logging
import os
from typing import Any, Optional

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

    Returns
    -------
    dict
        Response from the OpenHands API.
    """
    import requests

    base = (url or _OPENHANDS_URL).rstrip("/")
    payload: dict[str, Any] = {
        "prompt": prompt,
        "api_key": api_key,
        "no_wait": True,
        "poll_interval": _OPENHANDS_POLL_INTERVAL,
        "max_polls": max_polls or (
            _OPENHANDS_MAX_RUNTIME // _OPENHANDS_POLL_INTERVAL
        ),
    }
    if llm_model:
        payload["llm_model"] = llm_model
    if conversation_id:
        payload["conversation_id"] = conversation_id

    resp = requests.post(
        f"{base}/v1/call_lm",
        json=payload,
        timeout=_OPENHANDS_REQUEST_TIMEOUT,
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

    Parameters
    ----------
    role :
        The role name (e.g. ``"scout"``, ``"architect"``).
    user_task :
        The user task text. Must be non-empty.
    input_artifacts :
        Mapping of artifact names to their ID/path strings.
        The MCP server resolves these to content server-side.
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
        or an error dict.  The public response never contains
        ``full_result``, artifact ``content``, or ``artifact_path``.

    Raises
    ------
    ValueError
        If validation fails (e.g. unknown role, missing user_task,
        missing required artifacts).
    """
    if input_artifacts is None:
        input_artifacts = {}
    if metadata is None:
        metadata = {}

    # Always normalize input_artifacts — handles list-of-objects, wrapped
    # dict values like {"text": "art_xxx"}, or plain strings.
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

        # Try to resolve the reference to actual content.
        # The reference can be:
        #   0. An artifact_id (starts with "art_") — resolve via get_content_by_id
        #   1. A path like "run_id/filename.artifact" (from ArtifactStore)
        #   2. A bare artifact name — fall back to searching by name
        #   3. A role_run_id — look up via role_store
        content = None

        # --- Strategy 0: Resolve by artifact_id ---
        # Artifact IDs generated by _generate_artifact_id start with "art_".
        # This is the primary resolution path for the new shttp_role_call API.
        if ref_str.startswith("art_"):
            try:
                content = artifact_store.get_content_by_id(ref_str)
            except ValueError:
                pass  # Not found by id — fall through to other strategies

        # --- Strategy 1: Exact path resolution (path-like references) ---
        # A reference is path-like if it contains '/' and ends with '.artifact'.
        # This avoids false positives for logical artifact IDs.
        if content is None and "/" in ref_str and ref_str.endswith(".artifact"):
            try:
                meta = artifact_store.get_by_path(ref_str)
                # Validate artifact_name matches the input_artifacts key
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
        # If ref_str is not path-like, try to resolve by artifact_name.
        if content is None:
            try:
                meta = artifact_store.get(
                    metadata.get("run_id", ""), artifact_name=artifact_name
                )
                if meta and not meta.get("content_empty", True):
                    content = meta["content"]
            except ValueError:
                pass  # Invalid run_id — skip fallback

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

    # Add metadata
    if metadata.get("repository"):
        template_vars["repo"] = str(metadata["repository"])
    if metadata.get("base_branch"):
        template_vars["base_branch"] = str(metadata["base_branch"])
    if metadata.get("branch"):
        template_vars["branch"] = str(metadata["branch"])
    if metadata.get("context"):
        template_vars["context"] = str(metadata["context"])

    # Inject artifact contents into template variables.
    # Use artifact name directly as the Jinja2 variable name — the
    # mapping was identity (artifact_name → same variable name) and
    # the Jinja2 templates already expect the artifact name as the
    # variable key, so no translation is needed.
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
    # Step 6: Idempotency check — reuse existing run if same key
    # ------------------------------------------------------------------
    role_store = RoleRunStore()
    idempotency_scope = None
    if idempotency_key:
        idempotency_scope = f"{role}:{idempotency_key}"
        existing_role_run_id = role_store.find_by_idempotency_scope(idempotency_scope)
        if existing_role_run_id is not None:
            # Return the existing role run status instead of creating a new one.
            existing_run = role_store.get_role_run(existing_role_run_id)
            if existing_run is not None:
                existing_status = existing_run.get("status", "unknown")
                if existing_status == "completed":
                    # Return the completed result
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
                                artifacts_result[key] = stored_artifacts[key]
                    return {
                        "role_run_id": existing_run.get("role_run_id", ""),
                        "run_id": existing_run.get("run_id", ""),
                        "role": role,
                        "status": "completed",
                        "control_summary": control_summary or {},
                        "artifacts": artifacts_result if artifacts_result else {},
                        "_idempotent": True,
                    }
                # Still running or failed — return current status
                return {
                    "role_run_id": existing_run.get("role_run_id", ""),
                    "run_id": existing_run.get("run_id", ""),
                    "role": role,
                    "status": existing_status,
                    "message": f"Idempotent key '{idempotency_key}' is already in use (status: {existing_status}).",
                    "_idempotent": True,
                }

    # ------------------------------------------------------------------
    # Step 7: Create role run record
    # ------------------------------------------------------------------
    run_id = _generate_run_id()
    attempt = role_store.get_attempt_count(run_id, role) + 1
    role_run_id = _generate_role_run_id(run_id, role, attempt)

    # Save idempotency record with role_run_id (not run_id)
    if idempotency_key:
        role_store.save_idempotency_record(idempotency_scope, role_run_id)

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

    # ------------------------------------------------------------------
    # Step 7: Start OpenHands conversation (main prompt)
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
            role_run_id, lifecycle_state="error"
        )
        return {
            "status": "failed",
            "error": {
                "type": "ConversationStartError",
                "message": f"Failed to start OpenHands conversation: {exc}",
                "retryable": True,
            },
        }

    # Unified job_id: try multiple possible field names from OpenHands response
    job_id = (
        conv_response.get("task_id")
        or conv_response.get("conversation_id")
        or conv_response.get("id")
        or conv_response.get("app_conversation_id")
        or ""
    )

    if not job_id:
        role_store.update_role_run(
            role_run_id, lifecycle_state="error"
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

    role_store.update_role_run(
        role_run_id,
        openhands_task_id=job_id,
        lifecycle_state="main_prompt_sent",
    )

    # ------------------------------------------------------------------
    # Step 8: Wait for main response
    # ------------------------------------------------------------------
    main_response_data = _poll_task_status(
        job_id, url=url
    )
    main_status = main_response_data.get("status", "unknown")

    if main_status not in ("completed", "completed_empty_result"):
        role_store.update_role_run(
            role_run_id,
            status=main_status,
            lifecycle_state="error",
        )
        return {
            "status": "failed",
            "error": {
                "type": "MainResponseError",
                "message": f"Main response ended with status: {main_status}",
                "retryable": True,
            },
        }

    main_response = main_response_data.get("answer", "") or ""

    role_store.update_role_run(
        role_run_id,
        lifecycle_state="main_response_received",
    )

    # ------------------------------------------------------------------
    # Step 9: Save primary artifact via ArtifactStore
    # ------------------------------------------------------------------
    primary_meta = artifact_store.save(
        run_id=run_id,
        role_run_id=role_run_id,
        role=role,
        artifact_name=role_spec.output_artifact,
        content=main_response,
    )

    if primary_meta is None:
        return {
            "status": "failed",
            "error": {
                "type": "ArtifactSaveError",
                "message": "Failed to save primary artifact.",
                "retryable": True,
            },
        }

    primary_artifact_path = primary_meta["artifact_path"]
    primary_artifact_id = primary_meta.get("artifact_id", "")

    # ------------------------------------------------------------------
    # Step 10: Render and send summary prompt (same conversation)
    # ------------------------------------------------------------------
    try:
        summary_prompt = render_prompt(
            template_path="prompts/summaries/role_summary.md",
            variables={
                "role": role,
                "primary_artifact_name": role_spec.output_artifact,
            },
        )
    except FileNotFoundError:
        # Fallback: inline summary prompt
        summary_prompt = (
            f"Summarize your previous answer for the orchestrator.\n\n"
            f"Return compact JSON only.\n"
            f"Do not include Markdown.\n"
            f"Do not include code blocks.\n"
            f"Do not decide the next role.\n"
            f"Do not include routing advice.\n\n"
            f"Schema:\n"
            f'{{"status": "completed" | "blocked", '
            f'"role": "{role}", '
            f'"summary": "<short factual summary>", '
            f'"primary_artifact_name": "{role_spec.output_artifact}", '
            f'"blocking": true | false, '
            f'"risk_level": "LOW" | "MEDIUM" | "HIGH" | null, '
            f'"action": "PASS" | "BLOCKER" | null, '
            f'"blocking_summary": ["..."]}}\n\n'
            f"Rules:\n"
            f"- Only reviewer may set action to PASS or BLOCKER.\n"
            f"- Non-reviewer roles must set action to null.\n"
            f"- Do not include next_role.\n"
            f"- Do not include ready_for_next_role.\n"
        )

    role_store.update_role_run(
        role_run_id,
        lifecycle_state="summary_prompt_sent",
    )

    try:
        summary_conv_response = _start_conversation_on_fastapi(
            prompt=summary_prompt,
            api_key=api_key or os.getenv("OPENHANDS_API_KEY", ""),
            llm_model=llm_model,
            conversation_id=conversation_id,  # Same conversation!
            url=url,
        )
    except Exception as exc:
        # Summary prompt failed — complete with fallback
        role_store.update_role_run(
            role_run_id,
            lifecycle_state="completed",
        )
        control_summary = safe_fallback_summary(
            role=role,
            summary_artifact_name=role_spec.summary_artifact,
            is_reviewer=(role == "reviewer"),
            main_artifact_content=main_response,
        )
        return {
            "role_run_id": role_run_id,
            "status": "completed",
            "control_summary": control_summary,
            "artifacts": {
                "primary": {
                    "artifact_id": primary_artifact_id,
                    "artifact_type": role_spec.output_artifact,
                    "created_by": role,
                },
                "summary": {
                    "artifact_id": "",
                    "artifact_type": role_spec.summary_artifact,
                    "created_by": role,
                },
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
    # Step 11: Wait for summary response
    # ------------------------------------------------------------------
    summary_response_data = _poll_task_status(
        summary_job_id, url=url
    )
    summary_text = summary_response_data.get("answer", "") or ""

    role_store.update_role_run(
        role_run_id,
        lifecycle_state="summary_response_received",
    )

    # ------------------------------------------------------------------
    # Step 12: Validate/parse summary
    # ------------------------------------------------------------------
    control_summary = validate_summary(
        role=role,
        summary_artifact_name=role_spec.output_artifact,
        json_str=summary_text,
    )

    if not control_summary.get("valid"):
        # ------------------------------------------------------------------
        # Step 13: Repair if parsing fails
        # ------------------------------------------------------------------
        role_store.update_role_run(
            role_run_id,
            lifecycle_state="summary_parse_failed",
        )

        repair_prompt_text = repair_summary(
            role=role,
            summary_artifact_name=role_spec.output_artifact,
        )

        role_store.update_role_run(
            role_run_id,
            lifecycle_state="summary_repair_prompt_sent",
        )

        try:
            repair_conv_response = _start_conversation_on_fastapi(
                prompt=repair_prompt_text,
                api_key=api_key or os.getenv("OPENHANDS_API_KEY", ""),
                llm_model=llm_model,
                conversation_id=conversation_id,  # Same conversation!
                url=url,
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
            repair_response_data = _poll_task_status(
                repair_job_id, url=url
            )
            repair_text = repair_response_data.get("answer", "") or ""
            role_store.update_role_run(
                role_run_id,
                lifecycle_state="summary_repair_response_received",
            )

            control_summary = validate_summary(
                role=role,
                summary_artifact_name=role_spec.output_artifact,
                json_str=repair_text,
            )

    # ------------------------------------------------------------------
    # Step 14: If still invalid, use safe fallback
    # ------------------------------------------------------------------
    if not control_summary.get("valid"):
        control_summary = safe_fallback_summary(
            role=role,
            summary_artifact_name=role_spec.summary_artifact,
            is_reviewer=(role == "reviewer"),
            main_artifact_content=main_response,
        )

    # ------------------------------------------------------------------
    # Step 15: Save summary artifact via ArtifactStore
    # ------------------------------------------------------------------
    summary_meta = artifact_store.save(
        run_id=run_id,
        role_run_id=role_run_id,
        role=role,
        artifact_name=role_spec.summary_artifact,
        content=summary_text,
    )

    if summary_meta is None:
        # Defensive: generate a synthetic artifact_id when save() fails.
        # This should not happen in practice since save() always returns meta.
        summary_artifact_id = _generate_artifact_id(
            run_id, role, 1, role_spec.summary_artifact
        )
    else:
        summary_artifact_id = summary_meta.get("artifact_id", "")

    role_store.update_role_run(
        role_run_id,
        lifecycle_state="summary_artifact_saved",
    )

    # ------------------------------------------------------------------
    # Step 16: Mark completed and persist artifact metadata
    # ------------------------------------------------------------------
    role_store.update_role_run(
        role_run_id,
        status="completed",
        result_summary=json.dumps(control_summary),
        lifecycle_state="completed",
        artifacts=json.dumps({
            "primary": {
                "artifact_name": role_spec.output_artifact,
                "artifact_id": primary_artifact_id,
                "artifact_path": primary_artifact_path,
            },
            "summary": {
                "artifact_name": role_spec.summary_artifact,
                "artifact_id": summary_artifact_id,
            },
        }),
    )

    # ------------------------------------------------------------------
    # Step 17: Return control summary
    # ------------------------------------------------------------------
    return {
        "role_run_id": role_run_id,
        "run_id": run_id,
        "role": role,
        "status": "completed",
        "control_summary": control_summary,
        "artifacts": {
            "primary": {
                "artifact_id": primary_artifact_id,
                "artifact_type": role_spec.output_artifact,
                "created_by": role,
            },
            "summary": {
                "artifact_id": summary_artifact_id,
                "artifact_type": role_spec.summary_artifact,
                "created_by": role,
            },
        },
    }


# Backward-compatible alias for existing callers/tests.
# Prefer direct import of role_call_impl.
_role_call_impl_alias = role_call_impl
