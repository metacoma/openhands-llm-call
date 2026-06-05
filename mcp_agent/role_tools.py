#!/usr/bin/env python3
"""Role-level MCP tools and action/risk parsing helpers."""

import json
import logging
import math
import os
import re
from typing import Any, Dict, Optional

from .roles import get_role, list_roles, RoleSpec
from .prompt_renderer import render_prompt
from .role_store import RoleRunStore, _generate_run_id, _generate_role_run_id
from .lock_manager import RoleLockManager
from .artifact_store import ArtifactStore

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


def make_summary(text: str, max_chars: int = 700) -> str:
    """Create a short summary of *text* (up to *max_chars* characters).

    Improvements over the previous implementation:
    - Strips markdown headings (lines starting with ``#``) lightly.
    - Removes excessive blank lines (collapses 3+ consecutive blanks to 2).
    - Caps at *max_chars* (default 700) at a word boundary.
    - Preserves useful first lines.
    - Never returns an empty string when *text* is non-empty.
    """
    if not text:
        return ""
    stripped = text.strip()
    if not stripped:
        return ""

    # 1. Lightly strip markdown headings (remove lines that are only headings)
    lines = stripped.split("\n")
    filtered_lines = []
    for line in lines:
        # Keep lines that are not pure heading lines (e.g. "## Title")
        if re.match(r"^\s*#+\s*\S", line):
            # Keep the first few heading lines (up to 2) for context
            if len(filtered_lines) < 2:
                filtered_lines.append(line)
            continue
        filtered_lines.append(line)

    # 2. Remove excessive blank lines (collapse 3+ to 2)
    collapsed: list[str] = []
    blank_count = 0
    for line in filtered_lines:
        if line.strip() == "":
            blank_count += 1
            if blank_count <= 2:
                collapsed.append(line)
        else:
            blank_count = 0
            collapsed.append(line)

    result = "\n".join(collapsed)

    # 3. Cap at max_chars
    if len(result) <= max_chars:
        return result

    truncated = result[:max_chars]
    # Truncate at last word boundary
    last_space = truncated.rfind(" ")
    if last_space > 0:
        truncated = truncated[:last_space]
    else:
        # No word boundary found — hard truncate
        truncated = truncated.rstrip()

    # Ensure we don't return empty string when input was non-empty
    if not truncated:
        truncated = "..."

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
# Helper functions
# ---------------------------------------------------------------------------

import hashlib
from datetime import datetime, timezone, timedelta


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _lock_expires_at() -> str:
    """Return an ISO-8601 expiry time 180 minutes from now."""
    ttl_minutes = int(
        os.getenv("OPENHANDS_ROLE_LOCK_TTL_MINUTES", "180")
    )
    expiry = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    return expiry.isoformat()


def _normalize_lock_key(
    repo: Optional[str],
    branch: Optional[str],
    base_branch: Optional[str],
) -> str:
    """Normalize repo/branch into a lock key string."""
    repo_str = (repo or "").rstrip("/").lower()
    branch_str = (branch or base_branch or "").rstrip("/").lower()
    return f"{repo_str}|{branch_str}"


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
    repo: Optional[Any] = None,
    base_branch: Optional[Any] = None,
    branch: Optional[Any] = None,
    context: Optional[dict] = None,
    artifacts: Optional[dict] = None,
    idempotency_key: Optional[str] = None,
) -> dict:
    """Implementation of the ``role_start`` MCP tool.

    Parameters
    ----------
    role :
        The role name (e.g. ``"scout"``).
    user_task :
        The user's original task description (the effective prompt).
    repo :
        Deprecated.  GitHub repo URL or owner/repo string.  May also
        be a dict; normalization is done by the caller.  No longer
        passed to OpenHands as selected-repository metadata.
    base_branch :
        Deprecated.  See ``repo``.
    branch :
        Deprecated.  See ``repo``.
    context :
        Optional dict with ``run_id`` and other context
        (may include ``idempotency_key``).
    artifacts :
        Mapping of artifact names to their text content.
    idempotency_key :
        Optional top-level idempotency key.  Takes precedence over
        ``context.idempotency_key``.

    Returns
    -------
    dict
        On success: ``{run_id, role_run_id, role, status, poll_after_seconds,
        timeout_minutes, idempotent_reuse}``
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

    # 4. Resolve idempotency key (top-level wins over context)
    effective_idem_key = idempotency_key
    if not effective_idem_key:
        effective_idem_key = ctx.get("idempotency_key")

    # 5. Idempotency check
    if effective_idem_key:
        scope = f"{run_id}:{role}:{effective_idem_key}"
        role_store = _get_role_store()
        existing_role_run_id = role_store.find_by_idempotency_scope(scope)
        if existing_role_run_id is not None:
            # Look up the existing role run to get its status
            existing_run = role_store.get_role_run(existing_role_run_id)
            if existing_run is not None:
                # Query underlying OpenHands task status
                from .server import openhands_get_task_status

                task_status = openhands_get_task_status(
                    task_id=existing_run["openhands_task_id"]
                )
                current_status = task_status.get("status", "unknown")
                return {
                    "run_id": run_id,
                    "role_run_id": existing_role_run_id,
                    "role": role,
                    "status": current_status,
                    "poll_after_seconds": 30,
                    "timeout_minutes": role_spec.timeout_minutes,
                    "idempotent_reuse": True,
                }
            # Existing idempotency record but corrupt/missing role run —
            # return error instead of silently creating a duplicate
            return {
                "status": "failed",
                "error": {
                    "type": "CorruptIdempotencyRecord",
                    "message": (
                        f"Idempotency scope '{scope}' maps to role_run_id "
                        f"'{existing_role_run_id}', but the role run "
                        f"record is missing or corrupt. Please use a "
                        f"different idempotency_key."
                    ),
                    "retryable": False,
                },
            }

    # 6. Determine attempt count and role_run_id
    role_store = _get_role_store()
    attempt = role_store.get_attempt_count(run_id, role) + 1
    role_run_id = _generate_role_run_id(run_id, role, attempt)

    # 7. Mutating role lock (only for readonly: false)
    # Store lock_key so role_result can release it via the exact key
    stored_lock_key: Optional[str] = None
    if not role_spec.readonly:
        lock_key = _normalize_lock_key(repo, branch, base_branch)
        lock_manager = RoleLockManager()
        lock_meta: Dict[str, Any] = {
            "role_run_id": role_run_id,
            "run_id": run_id,
            "role": role,
            "repo": repo or "",
            "branch": branch or "",
            "created_at": _now_iso(),
            "expires_at": _lock_expires_at(),
        }
        conflict = lock_manager.acquire(lock_key, lock_meta)
        if conflict is not None:
            return {
                "status": "failed",
                "error": {
                    "type": "MutatingRoleLockActive",
                    "message": (
                        f"Mutating role lock is active for "
                        f"repo/branch '{lock_key}'. "
                        f"Existing role_run_id: "
                        f"{conflict.get('role_run_id', 'unknown')}. "
                        f"Wait for the existing role to complete."
                    ),
                    "retryable": True,
                },
            }
        stored_lock_key = lock_key

    # 8. Render prompt
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

    # 9. Compute per-role timeout as max_polls
    from .server import _start_conversation_on_fastapi, _get_store

    poll_interval = int(
        os.getenv("OPENHANDS_POLL_INTERVAL_SECONDS", "10")
    )
    timeout_seconds = role_spec.timeout_minutes * 60
    max_polls = math.ceil(timeout_seconds / poll_interval)

    api_key = os.getenv("OPENHANDS_API_KEY", "")
    if not api_key:
        logger.warning(
            "OPENHANDS_API_KEY not set; task may fail authentication"
        )

    try:
        # NOTE: repo and branch are intentionally NOT passed to the
        # FastAPI payload so OpenHands creates an empty/default sandbox.
        # They are kept in template_vars for backward-compatible prompt
        # rendering and in the role run record for historical tracking.
        fastapi_result = _start_conversation_on_fastapi(
            prompt=rendered_prompt,
            api_key=api_key,
            llm_model=role_spec.model,
            repo=None,
            branch=None,
            max_polls=max_polls,
        )
    except Exception as exc:
        # Release lock on failure for mutating roles
        if not role_spec.readonly:
            lock_key = _normalize_lock_key(repo, branch, base_branch)
            lock_manager = RoleLockManager()
            lock_manager.release(lock_key, role_run_id)
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
        # Release lock on failure for mutating roles
        if not role_spec.readonly:
            lock_key = _normalize_lock_key(repo, branch, base_branch)
            lock_manager = RoleLockManager()
            lock_manager.release(lock_key, role_run_id)
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

    # 10. Create task record in existing TaskStore
    task_store = _get_store()
    task_record = task_store.create_task(
        conversation_id=conversation_id,
        prompt=rendered_prompt,
        idempotency_key=role_run_id,
    )

    # 11. Create role run record (persist lock_key for mutating roles)
    role_store.create_role_run(
        role=role,
        run_id=run_id,
        role_run_id=role_run_id,
        openhands_task_id=task_record["task_id"],
        repo=repo,
        base_branch=base_branch,
        branch=branch,
        artifact_name=role_spec.output_artifact,
        attempt=attempt,
        lock_key=stored_lock_key,
    )

    # 12. Save idempotency record if key was provided
    if effective_idem_key:
        scope = f"{run_id}:{role}:{effective_idem_key}"
        role_store.save_idempotency_record(scope, role_run_id)

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
        "timeout_minutes": role_spec.timeout_minutes,
        "idempotent_reuse": False,
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


def role_result_impl(
    role_run_id: str, include_full_result: bool = True
) -> dict:
    """Implementation of the ``role_result`` MCP tool.

    Parameters
    ----------
    role_run_id :
        The role run ID returned by ``role_start``.
    include_full_result :
        If True (default), returns the full result text.
        If False, omits ``full_result`` but still returns artifact
        metadata and a summary.
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
            "full_result_omitted": False,
            "message": (
                f"Role '{role_run['role']}' is still '{st}'. "
                "Poll role_status until completed."
            ),
        }

    # Fetch the full result
    from .server import openhands_get_task_result

    result = openhands_get_task_result(task_id=role_run["openhands_task_id"])
    full_result = result.get("answer", "") or ""

    # Save artifact through ArtifactStore (single persistence mechanism)
    artifact_store = ArtifactStore()
    artifact_name = role_run.get("artifact_name") or f"{role_run['role']}_output"
    artifact = artifact_store.save(
        run_id=role_run["run_id"],
        role_run_id=role_run_id,
        role=role_run["role"],
        artifact_name=artifact_name,
        content=full_result,
    )

    # Derive summary, action, risk
    result_summary = make_summary(full_result, max_chars=700)
    action = parse_action(role_run["role"], full_result)
    risk = parse_risk(full_result)

    # Normalize action for display
    display_action = action if action else "UNKNOWN"
    display_risk = risk if risk else "UNKNOWN"

    # Update role run record with artifact metadata and parsed fields
    store.update_role_run(
        role_run_id,
        artifact_path=artifact["artifact_path"],
        artifact_name=artifact["artifact_name"],
        result_summary=result_summary,
        action=display_action,
        risk=display_risk,
        status="completed",
    )

    # Release mutating lock on terminal status using stored lock_key
    stored_lock_key = role_run.get("lock_key")
    if stored_lock_key:
        lock_manager = RoleLockManager()
        lock_manager.release(stored_lock_key, role_run_id)

    # Build response
    response: dict = {
        "role_run_id": role_run_id,
        "run_id": role_run["run_id"],
        "role": role_run["role"],
        "status": "completed",
        "action": display_action,
        "risk": display_risk,
        "artifact_name": artifact["artifact_name"],
        "artifact_path": artifact["artifact_path"],
        "result_summary": result_summary,
        "full_result": full_result if include_full_result else None,
        "full_result_omitted": not include_full_result,
        "timeout_minutes": role_run.get("timeout_minutes"),
    }

    return response


# ---------------------------------------------------------------------------
# Artifact tool implementations
# ---------------------------------------------------------------------------


def artifact_list_impl(run_id: str) -> dict:
    """Implementation of the ``artifact_list`` MCP tool.

    Parameters
    ----------
    run_id :
        The top-level run identifier.

    Returns
    -------
    dict
        ``{run_id, artifacts: [...]}``
    """
    store = ArtifactStore()
    try:
        artifacts = store.list(run_id)
    except ValueError as exc:
        return {
            "status": "failed",
            "error": {
                "type": "PathTraversalDetected",
                "message": str(exc),
                "retryable": False,
            },
        }
    return {"run_id": run_id, "artifacts": artifacts}


def artifact_get_impl(
    run_id: str | None = None,
    artifact_name: str | None = None,
    role_run_id: str | None = None,
) -> dict:
    """Implementation of the ``artifact_get`` MCP tool.

    Parameters
    ----------
    run_id :
        The top-level run identifier.
    artifact_name :
        Logical artifact name (e.g. ``"scout_report"``).
    role_run_id :
        Role-specific run ID.

    Returns
    -------
    dict
        Artifact metadata with ``content`` key, or an error dict.
    """
    # Resolve run_id and artifact_name from role_run_id if run_id is not provided
    if not run_id and role_run_id:
        role_store = _get_role_store()
        role_run = role_store.get_role_run(role_run_id)
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
        run_id = role_run.get("run_id")
        if not artifact_name:
            artifact_name = role_run.get("artifact_name")

    if not run_id:
        return {
            "status": "failed",
            "error": {
                "type": "MissingRunId",
                "message": (
                    "Missing run_id. Provide either run_id or role_run_id."
                ),
                "retryable": False,
            },
        }

    if not artifact_name and not role_run_id:
        return {
            "status": "failed",
            "error": {
                "type": "MissingArtifactName",
                "message": (
                    "Missing artifact_name. Provide artifact_name or role_run_id."
                ),
                "retryable": False,
            },
        }

    store = ArtifactStore()
    try:
        artifact = store.get(run_id, artifact_name=artifact_name, role_run_id=role_run_id)
    except ValueError as exc:
        return {
            "status": "failed",
            "error": {
                "type": "PathTraversalDetected",
                "message": str(exc),
                "retryable": False,
            },
        }
    except FileNotFoundError:
        return {
            "status": "failed",
            "error": {
                "type": "ArtifactNotFound",
                "message": (
                    f"Artifact not found for run_id='{run_id}'"
                    + (f", artifact_name='{artifact_name}'" if artifact_name else "")
                    + (f", role_run_id='{role_run_id}'" if role_run_id else "")
                ),
                "retryable": False,
            },
        }

    if artifact is None:
        return {
            "status": "failed",
            "error": {
                "type": "ArtifactNotFound",
                "message": (
                    f"Artifact not found for run_id='{run_id}'"
                    + (f", artifact_name='{artifact_name}'" if artifact_name else "")
                    + (f", role_run_id='{role_run_id}'" if role_run_id else "")
                ),
                "retryable": False,
            },
        }

    return artifact
