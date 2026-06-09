#!/usr/bin/env python3
"""Role-level MCP tools and internal helpers."""

import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from .role_store import RoleRunStore
from .lock_manager import RoleLockManager
from .artifact_store import ArtifactStore

logger = logging.getLogger("openhands-mcp")


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
    ``role_call`` calls.

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
    os.getenv("OPENHANDS_ROLE_WAIT_TIMEOUT_SECONDS", "30")
)
_DEFAULT_ROLE_WAIT_MAX_TIMEOUT = int(
    os.getenv("OPENHANDS_ROLE_WAIT_MAX_TIMEOUT_SECONDS", "240")
)
_DEFAULT_ROLE_WAIT_POLL_INTERVAL = int(
    os.getenv("OPENHANDS_ROLE_WAIT_POLL_INTERVAL_SECONDS", "5")
)
_MIN_POLL_INTERVAL = 5
_MAX_POLL_INTERVAL = 60

