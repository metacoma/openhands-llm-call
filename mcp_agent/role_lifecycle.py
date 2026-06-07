#!/usr/bin/env python3
"""Two-step role lifecycle with in-conversation summary.

Provides ``start_role_v2_impl`` which implements the full two-step
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

from .artifact_store import ArtifactStore
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

        if status in ("completed", "failed", "cancelled", "timeout",
                       "canceled", "timed_out", "error",
                       "completed_empty_result"):
            return data

        # Still running — wait
        import time
        time.sleep(_OPENHANDS_POLL_INTERVAL)

    return {
        "status": "timed_out",
        "message": "Role timed out waiting for OpenHands response.",
    }


def start_role_v2_impl(
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
        Control summary with artifact paths, or an error dict.

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
        #   1. A path like "run_id/filename.artifact" (from ArtifactStore)
        #   2. A bare artifact name — fall back to searching by name
        #   3. A role_run_id — look up via role_store
        content = None

        # Strategy 1: Parse run_id + artifact_name from a path-like reference
        if "/" in ref_str or "\\" in ref_str:
            parts = ref_str.replace("\\", "/").split("/")
            if len(parts) >= 2:
                candidate_run_id = parts[0]
                filename = parts[-1]
                # Extract artifact_name from filename like "run-scout-1_scout_report.artifact"
                base = filename.replace(".artifact", "").replace(".meta.json", "")
                # artifact_name is the last underscore-separated segment
                candidate_artifact_name = base.rsplit("_", 1)[-1] if "_" in base else base
                try:
                    meta = artifact_store.get(candidate_run_id, artifact_name=artifact_name)
                    if meta and not meta.get("content_empty", True):
                        content = meta["content"]
                except ValueError:
                    pass  # Invalid run_id format — try next strategy

            # Strategy 2 removed: artifact_store.list("") always raises ValueError
            # because _safe_component rejects empty strings. If Strategy 1
            # fails, the artifact is genuinely not found.

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
    # Step 6: Create role run record
    # ------------------------------------------------------------------
    role_store = RoleRunStore()
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

    openhands_task_id = conv_response.get("task_id", "")
    conversation_id = conv_response.get("conversation_id", "")

    role_store.update_role_run(
        role_run_id,
        openhands_task_id=openhands_task_id,
        lifecycle_state="main_prompt_sent",
    )

    # ------------------------------------------------------------------
    # Step 8: Wait for main response
    # ------------------------------------------------------------------
    main_response_data = _poll_task_status(
        openhands_task_id, url=url
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

    role_store.update_role_run(
        role_run_id,
        artifact_path=primary_artifact_path,
        lifecycle_state="primary_artifact_saved",
    )

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
                    "artifact_name": role_spec.output_artifact,
                    "artifact_path": primary_artifact_path,
                },
                "summary": {
                    "artifact_name": role_spec.summary_artifact,
                    "artifact_path": None,
                },
            },
        }

    summary_task_id = summary_conv_response.get("task_id", "")

    # ------------------------------------------------------------------
    # Step 11: Wait for summary response
    # ------------------------------------------------------------------
    summary_response_data = _poll_task_status(
        summary_task_id, url=url
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
            repair_task_id = repair_conv_response.get("task_id", "")
            repair_response_data = _poll_task_status(
                repair_task_id, url=url
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
        summary_artifact_path = None
    else:
        summary_artifact_path = summary_meta["artifact_path"]

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
                "artifact_path": primary_artifact_path,
            },
            "summary": {
                "artifact_name": role_spec.summary_artifact,
                "artifact_path": summary_artifact_path,
            },
        }),
    )

    # ------------------------------------------------------------------
    # Step 17: Return control summary
    # ------------------------------------------------------------------
    return {
        "role_run_id": role_run_id,
        "status": "completed",
        "control_summary": control_summary,
        "artifacts": {
            "primary": {
                "artifact_name": role_spec.output_artifact,
                "artifact_path": primary_artifact_path,
            },
            "summary": {
                "artifact_name": role_spec.summary_artifact,
                "artifact_path": summary_artifact_path,
            },
        },
    }
