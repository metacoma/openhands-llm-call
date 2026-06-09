#!/usr/bin/env python3
"""Role-level MCP tools and action/risk parsing helpers."""

import json
import logging
import math
import os
import re
import time
from typing import Any, Dict, Optional

from .roles import get_role, list_roles, RoleSpec
from .prompt_renderer import render_prompt
from .role_store import RoleRunStore, _generate_run_id, _generate_role_run_id
from .lock_manager import RoleLockManager
from .artifact_store import ArtifactStore

logger = logging.getLogger("openhands-mcp")


# ---------------------------------------------------------------------------
# Empty-result response helper (Issue 2: eliminate duplication)
# ---------------------------------------------------------------------------


def _build_empty_result_response(
    role_run_id: str,
    role_run: Optional[dict],
    last_status: str,
) -> dict:
    """Build a canonical empty-result response dict.

    Centralises the empty-result shape so that changes to the schema
    only need to be made in one place.
    """
    diagnostics = {
        "conversation_id": role_run.get("conversation_id") if role_run else None,
        "task_id": role_run["openhands_task_id"] if role_run else None,
        "last_status": last_status,
        "answer_empty": True,
        "final_answer_retry_seconds": int(
            os.getenv("OPENHANDS_FINAL_ANSWER_RETRY_SECONDS", "60")
        ),
    }
    return {
        "role_run_id": role_run_id,
        "run_id": role_run["run_id"] if role_run else None,
        "role": role_run["role"] if role_run else None,
        "status": "completed_empty_result",
        "has_result": False,
        "full_result": "",
        "result_summary": "",
        "artifact_name": role_run.get("artifact_name") if role_run else None,
        "artifact_path": None,
        "artifact_saved": False,
        "action": None,
        "risk": None,
        "error": {
            "type": "EmptyRoleResult",
            "message": (
                "Role completed but did not return a final LLM answer."
            ),
            "retryable": True,
            "suggested_next_action": (
                "Retry this role once with a stricter final-answer prompt."
            ),
        },
        "diagnostics": diagnostics,
        "timeout_minutes": role_run.get("timeout_minutes") if role_run else None,
    }


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


def role_status_impl(role_run_id: str) -> dict:
    """LEGACY — removed from public API. Kept only for backward compat."""
    raise NotImplementedError(
        "role_status_impl is removed; use role_wait instead."
    )


def role_result_impl(
    role_run_id: str,
    include_full_result: bool = True,
    force_refresh: bool = False,
) -> dict:
    """LEGACY — removed from public API. Kept only for backward compat."""
    raise NotImplementedError(
        "role_result_impl is removed; use role_wait instead."
    )


# ---------------------------------------------------------------------------
# role_wait — server-side polling
# ---------------------------------------------------------------------------

TERMINAL_STATUSES = frozenset(
    {
        "completed",
        "completed_empty_result",
        "failed",
        "cancelled",
        "canceled",
        "timeout",
        "timed_out",
        "stuck",
        "error",
    }
)


def _refresh_role_status_from_openhands(
    role_store: "RoleRunStore",
    role_run_id: str,
    task_id: str,
) -> Optional[dict]:
    """Refresh actual OpenHands task status for a role run.

    Returns the refreshed status dict, or None if refresh failed.
    """
    try:
        import os
        import requests

        base = os.getenv("OPENHANDS_URL", "http://localhost:8000").rstrip("/")
        resp = requests.get(
            f"{base}/v1/jobs/{task_id}",
            timeout=int(os.getenv("OPENHANDS_REQUEST_TIMEOUT_SECONDS", "60")),
        )
        resp.raise_for_status()
        job_data = resp.json()
        api_status = job_data.get("status", "unknown")
        exec_status = job_data.get("execution_status")
        if api_status == "completed" or exec_status in ("finished", "success"):
            return {"status": "completed"}
        elif api_status == "failed" or exec_status in ("failed", "error"):
            return {"status": "failed"}
        elif api_status == "not_found":
            return {"status": "unknown"}
        else:
            return {"status": "running"}
    except Exception as exc:
        logger.warning(
            "Failed to refresh OpenHands status for role_run_id=%s, "
            "task_id=%s: %s",
            role_run_id,
            task_id,
            exc,
        )
        return None


def _find_active_role_run(role_store: "RoleRunStore") -> Optional[dict]:
    """Find any non-terminal role run in the store.

    Terminal statuses: see ``TERMINAL_STATUSES``.

    IMPORTANT: Before returning a candidate as active, this function
    refreshes its actual OpenHands task status. If the actual status
    is terminal, the persisted record is updated and the candidate is
    skipped. This prevents stale "running" locks from blocking future
    ``role_start`` calls.

    If the actual status cannot be refreshed (missing metadata or
    OpenHands unavailable), the candidate is returned as active with
    a clear error message including the ``role_run_id``.
    """
    state_dir = role_store.state_dir
    if not state_dir.exists():
        return None

    for filepath in state_dir.glob("*.json"):
        try:
            data = json.loads(filepath.read_text(encoding="utf-8"))
            # Only consider role-specific records (not generic tasks)
            if "role" not in data:
                continue
            status = data.get("status", "")
            if status in TERMINAL_STATUSES:
                continue

            # Candidate has a non-terminal persisted status.
            # Refresh actual OpenHands task status before trusting it.
            task_id = data.get("openhands_task_id")
            if task_id:
                refreshed = _refresh_role_status_from_openhands(
                    role_store, data["role_run_id"], task_id
                )
                if refreshed is not None:
                    # Refresh succeeded — check if actual status is terminal
                    actual_status = refreshed.get("status")

                    # Guard: missing, empty, or unknown status is NOT terminal.
                    # Treating it as terminal would risk clearing the active
                    # lock and violating single-threaded safety.
                    if not actual_status or actual_status == "unknown":
                        data["_refresh_failed"] = True
                        data["_refresh_warning"] = (
                            "OpenHands returned missing or unknown task status; "
                            "treating role as active to preserve single-threaded safety."
                        )
                        return data

                    if actual_status in TERMINAL_STATUSES:
                        # Persist the terminal status to clear the stale lock
                        role_store.update_role_run(
                            data["role_run_id"], status=actual_status
                        )
                        logger.info(
                            "Stale active lock cleared for role_run_id=%s "
                            "(persisted=%s, actual=%s)",
                            data["role_run_id"],
                            status,
                            actual_status,
                        )
                        continue  # Skip this candidate, continue scanning
                    else:
                        # Actual status is still non-terminal — return as active
                        return data

            # Cannot refresh (missing task_id or refresh failed) —
            # treat as active with a warning flag.
            # The caller (_build_another_role_running_error) will include
            # this information in the LLM-friendly error.
            data["_refresh_failed"] = True
            return data

        except (json.JSONDecodeError, OSError):
            continue

    return None


_DEFAULT_ROLE_WAIT_TIMEOUT = int(
    os.getenv("OPENHANDS_ROLE_WAIT_TIMEOUT_SECONDS", "1800")
)
_DEFAULT_ROLE_WAIT_MAX_TIMEOUT = int(
    os.getenv("OPENHANDS_ROLE_WAIT_MAX_TIMEOUT_SECONDS", "7200")
)
_DEFAULT_ROLE_WAIT_POLL_INTERVAL = int(
    os.getenv("OPENHANDS_ROLE_WAIT_POLL_INTERVAL_SECONDS", "15")
)
_MIN_POLL_INTERVAL = 5
_MAX_POLL_INTERVAL = 120


def role_wait_impl(
    role_run_id: str,
    timeout_seconds: int | None = None,
    poll_interval_seconds: int | None = None,
    return_result: bool = True,
) -> dict:
    """LEGACY — removed from public API. Kept only for backward compat."""
    raise NotImplementedError(
        "role_wait_impl is removed; use role_lifecycle_wait_impl instead."
    )


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

    # Ensure empty-content diagnostics are always present
    if "content_empty" not in artifact:
        artifact["content_empty"] = not artifact.get("content", "").strip()
    if "valid_role_report" not in artifact:
        artifact["valid_role_report"] = bool(artifact.get("content", "").strip())

    # Add warning for empty artifacts
    if artifact.get("content_empty"):
        artifact["valid_role_report"] = False
        artifact["warning"] = {
            "type": "EmptyArtifactContent",
            "message": "Artifact exists but content is empty.",
        }

    return artifact
