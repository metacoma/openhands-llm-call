#!/usr/bin/env python3
"""Role-level MCP tools and action/risk parsing helpers."""

import json
import logging
import os
import re
from typing import Any, Optional

from .roles import get_role, list_roles, RoleSpec
from .prompt_renderer import render_prompt
from .role_store import RoleRunStore, _generate_run_id, _generate_role_run_id

logger = logging.getLogger("openhands-mcp")


# ---------------------------------------------------------------------------
# Action/Risk parsing helpers
# ---------------------------------------------------------------------------


def parse_action(role: str, text: str) -> Optional[str]:
    """Extract the action from a role result.

    Parameters
    ----------
    role :
        The role name (used to determine parsing rules).
    text :
        The full result text.

    Returns
    -------
    str or None
        ``"PASS"`` / ``"BLOCKER"`` for reviewer,
        ``"READY"`` / ``"BLOCKED"`` for publisher,
        ``"CONTINUE"`` for all other completed roles.
        Returns ``None`` when the expected pattern is not found
        (e.g. reviewer forgot to emit ACTION).
    """
    if role == "reviewer":
        m = re.search(r"ACTION:\s*(PASS|BLOCKER)", text, re.IGNORECASE)
        return m.group(1).upper() if m else None
    elif role == "publisher":
        m = re.search(
            r"PUBLISH_STATUS:\s*(READY|BLOCKED)", text, re.IGNORECASE
        )
        return m.group(1).upper() if m else None
    # For all other completed roles default to CONTINUE.
    return "CONTINUE"


def parse_risk(text: str) -> Optional[str]:
    """Extract the risk level from text.

    Parameters
    ----------
    text :
        The full result text.

    Returns
    -------
    str or None
        ``"LOW"``, ``"MEDIUM"``, or ``"HIGH"``, or None if not found.
    """
    m = re.search(r"RISK:\s*(LOW|MEDIUM|HIGH)", text, re.IGNORECASE)
    return m.group(1).upper() if m else None


def make_summary(text: str, max_chars: int = 500) -> str:
    """Create a short summary of *text* (up to *max_chars* characters).

    Truncates at the last word boundary when exceeded.
    """
    if not text:
        return ""
    stripped = text.strip()
    if len(stripped) <= max_chars:
        return stripped
    # Truncate at last word boundary.
    truncated = stripped[:max_chars]
    last_space = truncated.rfind(" ")
    if last_space > 0:
        truncated = truncated[:last_space]
    return truncated + "..."


# ---------------------------------------------------------------------------
# Role run store singleton
# ---------------------------------------------------------------------------

_role_store: Optional[RoleRunStore] = None


def _get_role_store() -> RoleRunStore:
    """Return the module-level RoleRunStore singleton."""
    global _role_store
    if _role_store is None:
        state_dir = os.getenv("OPENHANDS_ROLE_STATE_DIR", ".runs")
        _role_store = RoleRunStore(state_dir)
    return _role_store


# ---------------------------------------------------------------------------
# MCP tool implementations
# ---------------------------------------------------------------------------


def role_list_impl() -> dict:
    """Implementation of the ``role_list`` MCP tool.

    Returns a dict with a ``roles`` key containing a list of role
    summaries (sanitized to not expose secrets).
    """
    roles = list_roles()
    # Sanitize: include model only because this is a local homelab
    # system; exclude it in production contexts.
    safe_roles = []
    for r in roles:
        safe_roles.append({
            "name": r["name"],
            "description": r["description"],
            "model": r["model"],
            "readonly": r["readonly"],
            "requires_artifacts": r["requires_artifacts"],
            "output_artifact": r["output_artifact"],
            "timeout_minutes": r["timeout_minutes"],
        })
    return {"roles": safe_roles}


def role_start_impl(
    role: str,
    user_task: str,
    repo: Optional[str] = None,
    base_branch: Optional[str] = None,
    branch: Optional[str] = None,
    context: Optional[dict] = None,
    artifacts: Optional[dict] = None,
) -> dict:
    """Implementation of the ``role_start`` MCP tool.

    Parameters
    ----------
    role :
        The role name (e.g. ``"scout"``).
    user_task :
        The user's original task description.
    repo :
        GitHub repo URL or owner/repo string.
    base_branch :
        The base branch to use.
    branch :
        Optional feature branch (may be None to auto-create).
    context :
        Optional dict with ``run_id`` and other context.
    artifacts :
        Mapping of artifact names to their text content.

    Returns
    -------
    dict
        On success: ``{run_id, role_run_id, role, status, poll_after_seconds}``
        On failure: ``{status: "failed", error: {...}}``
    """
    # 1. Load role spec
    try:
        role_spec = get_role(role)
    except KeyError as exc:
        return {
            "status": "failed",
            "error": {
                "type": "UnknownRole",
                "message": str(exc),
                "retryable": False,
            },
        }

    # 2. Validate required artifacts
    provided_artifacts = artifacts or {}
    for req in role_spec.requires_artifacts:
        if req not in provided_artifacts:
            return {
                "status": "failed",
                "error": {
                    "type": "MissingRequiredArtifact",
                    "message": (
                        f"Role '{role}' requires artifact '{req}', "
                        f"but it was not provided."
                    ),
                    "retryable": False,
                },
            }

    # 3. Determine run_id (reuse existing or generate new)
    ctx = context or {}
    run_id = ctx.get("run_id") or _generate_run_id()

    # 4. Determine attempt count and role_run_id
    store = _get_role_store()
    attempt = store.get_attempt_count(run_id, role) + 1
    role_run_id = _generate_role_run_id(run_id, role, attempt)

    # 5. Render prompt
    template_vars: dict[str, Any] = {
        "user_task": user_task,
        "repo": repo or "",
        "base_branch": base_branch or "",
        "branch": branch or "",
        "context": json.dumps(ctx) if ctx else "",
    }
    template_vars.update(provided_artifacts)

    try:
        rendered_prompt = render_prompt(
            role_spec.prompt_template, template_vars
        )
    except FileNotFoundError as exc:
        return {
            "status": "failed",
            "error": {
                "type": "PromptTemplateNotFound",
                "message": str(exc),
                "retryable": False,
            },
        }

    logger.info(
        "role_start: role=%s run_id=%s role_run_id=%s "
        "prompt_length=%d",
        role,
        run_id,
        role_run_id,
        len(rendered_prompt),
    )

    # 6. Start OpenHands task via existing FastAPI helper
    from .server import _start_conversation_on_fastapi, _get_store

    api_key = os.getenv("OPENHANDS_API_KEY", "")
    if not api_key:
        logger.warning(
            "OPENHANDS_API_KEY not set; task may fail authentication"
        )

    try:
        fastapi_result = _start_conversation_on_fastapi(
            prompt=rendered_prompt,
            api_key=api_key,
            llm_model=role_spec.model,
            repo=repo,
            branch=branch,
        )
    except Exception as exc:
        return {
            "status": "failed",
            "error": {
                "type": "TaskStartFailed",
                "message": f"Failed to start OpenHands task: {exc}",
                "retryable": True,
            },
        }

    conversation_id = (
        fastapi_result.get("conversation_id")
        or fastapi_result.get("id")
        or fastapi_result.get("app_conversation_id")
    )

    if not conversation_id:
        return {
            "status": "failed",
            "error": {
                "type": "MissingConversationId",
                "message": (
                    "FastAPI response did not include a conversation_id."
                ),
                "retryable": False,
            },
        }

    # 7. Create task record in existing TaskStore
    task_store = _get_store()
    task_record = task_store.create_task(
        conversation_id=conversation_id,
        prompt=rendered_prompt,
        idempotency_key=role_run_id,
    )

    # 8. Create role run record
    store.create_role_run(
        role=role,
        run_id=run_id,
        role_run_id=role_run_id,
        openhands_task_id=task_record["task_id"],
        repo=repo,
        base_branch=base_branch,
        branch=branch,
        artifact_name=role_spec.output_artifact,
    )

    logger.info(
        "role_start: started task_id=%s for role_run_id=%s",
        task_record["task_id"],
        role_run_id,
    )

    return {
        "run_id": run_id,
        "role_run_id": role_run_id,
        "role": role,
        "status": "running",
        "poll_after_seconds": 30,
    }


def role_status_impl(role_run_id: str) -> dict:
    """Implementation of the ``role_status`` MCP tool.

    Parameters
    ----------
    role_run_id :
        The role run ID returned by ``role_start``.

    Returns
    -------
    dict
        Normalized status dict.
    """
    store = _get_role_store()
    role_run = store.get_role_run(role_run_id)

    if role_run is None:
        return {
            "status": "failed",
            "error": {
                "type": "UnknownRoleRunId",
                "message": (
                    f"No role run found for role_run_id='{role_run_id}'. "
                    "Verify the ID returned by role_start."
                ),
                "retryable": False,
            },
        }

    # Query the existing OpenHands task status
    from .server import openhands_get_task_status

    task_status = openhands_get_task_status(
        task_id=role_run["openhands_task_id"]
    )

    st = task_status.get("status", "unknown")
    has_result = st == "completed"

    summary: Optional[str] = None
    if has_result:
        answer = task_status.get("answer", "")
        if answer:
            summary = make_summary(answer, max_chars=200)

    return {
        "role_run_id": role_run_id,
        "run_id": role_run["run_id"],
        "role": role_run["role"],
        "status": st,
        "summary": summary,
        "has_result": has_result,
    }


def role_result_impl(role_run_id: str) -> dict:
    """Implementation of the ``role_result`` MCP tool.

    Parameters
    ----------
    role_run_id :
        The role run ID returned by ``role_start``.

    Returns
    -------
    dict
        Structured role result including artifact path and parsed fields.
    """
    store = _get_role_store()
    role_run = store.get_role_run(role_run_id)

    if role_run is None:
        return {
            "status": "failed",
            "error": {
                "type": "UnknownRoleRunId",
                "message": (
                    f"No role run found for role_run_id='{role_run_id}'. "
                    "Verify the ID returned by role_start."
                ),
                "retryable": False,
            },
        }

    # Check if the OpenHands task is completed
    from .server import openhands_get_task_status

    task_status = openhands_get_task_status(
        task_id=role_run["openhands_task_id"]
    )
    st = task_status.get("status", "unknown")

    if st != "completed":
        return {
            "role_run_id": role_run_id,
            "run_id": role_run["run_id"],
            "role": role_run["role"],
            "status": st,
            "action": None,
            "risk": None,
            "artifact_name": role_run.get("artifact_name"),
            "artifact_path": None,
            "result_summary": None,
            "full_result": None,
            "message": (
                f"Role '{role_run['role']}' is still '{st}'. "
                "Poll role_status until completed."
            ),
        }

    # Fetch the full result
    from .server import openhands_get_task_result

    result = openhands_get_task_result(task_id=role_run["openhands_task_id"])
    full_result = result.get("answer", "") or ""

    # Save artifact to disk
    artifact_path = store.save_artifact(role_run_id, full_result)

    # Derive summary, action, risk
    result_summary = make_summary(full_result, max_chars=500)
    action = parse_action(role_run["role"], full_result)
    risk = parse_risk(full_result)

    # Normalize action for display
    display_action = action if action else "UNKNOWN"
    display_risk = risk if risk else "UNKNOWN"

    # Update role run record with parsed fields
    store.update_role_run(
        role_run_id,
        action=display_action,
        risk=display_risk,
        result_summary=result_summary,
        status="completed",
    )

    return {
        "role_run_id": role_run_id,
        "run_id": role_run["run_id"],
        "role": role_run["role"],
        "status": "completed",
        "action": display_action,
        "risk": display_risk,
        "artifact_name": role_run.get("artifact_name"),
        "artifact_path": artifact_path,
        "result_summary": result_summary,
        "full_result": full_result,
    }
