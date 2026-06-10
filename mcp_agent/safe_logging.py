"""Safe logging helpers for diagnostic logging of the role_call → /v1/call_lm pipeline.

Provides sanitization helpers that prevent secrets from leaking into logs
and produce structured shape descriptions of MCP inputs and HTTP payloads.
"""

import logging
import os
from typing import Any

# ---------------------------------------------------------------------------
# Shared debug flag: OPENHANDS_LLM_CALL_DEBUG
# ---------------------------------------------------------------------------

_DEBUG_ENV_VAR = "OPENHANDS_LLM_CALL_DEBUG"
_TRUTHY_VALUES = {"1", "true", "yes", "on", "debug"}


def is_debug_enabled() -> bool:
    """Return True if OPENHANDS_LLM_CALL_DEBUG is set to a truthy value.

    Accepted truthy values (case-insensitive): 1, true, yes, on, debug.
    """
    return os.getenv(_DEBUG_ENV_VAR, "").lower() in _TRUTHY_VALUES


# Backward-compatibility alias for any code that imports DEBUG_ROLE_CALL directly.
DEBUG_ROLE_CALL = is_debug_enabled()


def debug_log(logger: logging.Logger, message: str, **fields) -> None:
    """Log a structured debug message when OPENHANDS_LLM_CALL_DEBUG is enabled.

    Parameters
    ----------
    logger :
        The logger to use (must be configured by the caller).
    message :
        A greppable message key, e.g. ``"job_poll"`` or ``"extraction_candidate"``.
    **fields :
        Key-value pairs appended as ``key=value`` to the log line.
        Long values are truncated to 500 chars; secrets are not filtered here
        — callers must avoid passing tokens/keys.
    """
    if not is_debug_enabled():
        return
    parts = [f"DEBUG {message}"]
    for k, v in sorted(fields.items()):
        val = str(v)
        if len(val) > 500:
            val = val[:500] + "..."
        parts.append(f"{k}={val}")
    logger.debug(" ".join(parts))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SENSITIVE_KEYS = {"api_key", "authorization", "token", "password", "secret"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def safe_preview(value: Any, limit: int = 300) -> str:
    """Return a safe preview of *value* as a string, truncated to *limit* chars.

    For strings: returns the string truncated to limit (with '...' suffix).
    For other types: returns str(value) truncated to limit.
    For None: returns '(none)'.
    """
    if value is None:
        return "(none)"
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return value[:limit] + "..."
    return str(value)[:limit]


def safe_json_shape(value: Any, preview_limit: int = 200) -> Any:
    """Return a safe, non-secret shape description of *value*.

    Returns a structure that mirrors the input but replaces secrets with
    redacted markers and long strings with {type, len, preview}.
    """
    if value is None:
        return {"type": "NoneType", "value": None}

    type_name = type(value).__name__

    if isinstance(value, bool):
        return {"type": "bool", "value": value}

    if isinstance(value, int):
        return {"type": "int", "value": value}

    if isinstance(value, float):
        return {"type": "float", "value": value}

    if isinstance(value, str):
        result: dict[str, Any] = {"type": "str"}
        if len(value) <= preview_limit:
            result["value"] = value
        else:
            result["len"] = len(value)
            result["preview"] = value[:preview_limit] + "..."
        return result

    if isinstance(value, (list, tuple)):
        return {
            "type": type_name,
            "len": len(value),
            "items_preview": [safe_json_shape(item, preview_limit) for item in value[:5]],
        }

    if isinstance(value, dict):
        result: dict[str, Any] = {"type": "dict", "keys": list(value.keys())}
        for k, v in value.items():
            if k.lower() in SENSITIVE_KEYS:
                result[k] = {"type": type(v).__name__, "redacted": True}
            else:
                result[k] = safe_json_shape(v, preview_limit)
        return result

    # Fallback for other types
    return {"type": type_name, "repr": str(value)[:preview_limit]}


def correlate_id_from_args(
    role_run_id: Any = None,
    run_id: Any = None,
    idempotency_key: Any = None,
) -> str:
    """Compute a correlation ID from available identifiers.

    Priority: role_run_id → run_id → idempotency_key → generated UUID.
    """
    if role_run_id and str(role_run_id).strip():
        return str(role_run_id).strip()
    if run_id and str(run_id).strip():
        return str(run_id).strip()
    if idempotency_key and str(idempotency_key).strip():
        return str(idempotency_key).strip()
    import uuid

    return f"gen-{uuid.uuid4().hex[:8]}"


def format_correlation(correlation_id: str, role: str = "", idempotency_key: str = "") -> str:
    """Format a correlation prefix string for log messages."""
    parts = [f"correlation_id={correlation_id}"]
    if role:
        parts.append(f"role={role}")
    if idempotency_key:
        parts.append(f"idempotency_key={idempotency_key}")
    return " ".join(parts)
