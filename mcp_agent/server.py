#!/usr/bin/env python3
"""MCP server that proxies to the OpenHands LLM Call FastAPI server.

Public API: exactly three MCP tools — role_list, role_call, role_wait.
"""

import hashlib
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Annotated, Literal

import requests
import uvicorn
from mcp.server.fastmcp import FastMCP
from pydantic import BeforeValidator
from pydantic_core import PydanticUseDefault

from .artifact_store import ArtifactStore
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

MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "8000"))

_fastmcp_host = os.getenv("FASTMCP_HOST", MCP_HOST)
_fastmcp_port = int(os.getenv("FASTMCP_PORT", str(MCP_PORT)))

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


# ---------------------------------------------------------------------------
# Pydantic BeforeValidator type aliases for LLM-friendly MCP arguments
# ---------------------------------------------------------------------------

def _normalize_mcp_string(v: Any) -> str:
    """Normalize a string argument that may be wrapped in scalar wrappers."""
    if v is None:
        return ""
    unwrapped = unwrap_scalar(v)
    if isinstance(unwrapped, str):
        return unwrapped
    return str(unwrapped)


def _normalize_mcp_int(v: Any) -> int:
    """Normalize an int argument that may be wrapped or string-encoded."""
    if v is None:
        raise PydanticUseDefault()
    return normalize_int(v, default=0)


def _normalize_mcp_bool(v: Any) -> bool:
    """Normalize a bool argument that may be wrapped or string-encoded."""
    if v is None:
        raise PydanticUseDefault()
    return normalize_bool(v, default=False)


def _normalize_mcp_role(v: Any) -> str:
    """Normalize a role name with case-insensitive matching and wrapper unwrapping."""
    raw = normalize_role(v)
    return raw.strip().lower()


McpString = Annotated[str, BeforeValidator(_normalize_mcp_string)]
McpInt = Annotated[int, BeforeValidator(_normalize_mcp_int)]
McpBool = Annotated[bool, BeforeValidator(_normalize_mcp_bool)]

_MCP_ROLES = Literal["scout", "architect", "coder", "reviewer", "publisher", "coder_fix"]
McpRole = Annotated[_MCP_ROLES, BeforeValidator(_normalize_mcp_role)]


# ---------------------------------------------------------------------------
# Private helpers used by role_call / role_wait
# ---------------------------------------------------------------------------

def unwrap_scalar(value: Any, extra_keys: list[str] | None = None) -> Any:
    """Unwrap a scalar value that may be wrapped in various MCP/LLM dict shapes."""
    if not isinstance(value, dict):
        return value
    if len(value) == 1:
        key = next(iter(value))
        known_keys = ("text", "value", "default", "name", "id", "artifact_id", "string", "content", "idempotency_key")
        if key in known_keys or (extra_keys and key in extra_keys):
            return unwrap_scalar(value[key])
        return value
    for key in ("text", "value", "name", "default", "id", "artifact_id"):
        if key in value:
            return unwrap_scalar(value[key])
    if extra_keys:
        for key in extra_keys:
            if key in value:
                return unwrap_scalar(value[key])
    return value


def normalize_role(value: Any) -> str:
    """Normalize a role name with case-insensitive matching and wrapper unwrapping."""
    if isinstance(value, str):
        return value.strip().lower()
    if isinstance(value, dict):
        for key in ("name", "role", "value", "id", "text"):
            if key in value:
                return normalize_role(value[key])
    return str(value).strip().lower()


def unwrap_text(value: Any) -> Any:
    """Accept raw MCP values — unwrap {"text": ...} wrappers."""
    if isinstance(value, dict) and "text" in value and len(value) == 1:
        return value["text"]
    return value


def normalize_bool(value: Any, default: bool = False) -> bool:
    """Normalize a boolean value that may be wrapped or string-encoded."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    if value is None:
        return default
    return bool(value)


def normalize_int(value: Any, default: int | None = None) -> int:
    """Normalize an integer value that may be wrapped or string-encoded."""
    if isinstance(value, dict):
        # Unwrap dict wrappers first
        for key in ("value", "default", "text", "id", "name"):
            if key in value:
                return normalize_int(value[key], default)
        return default if default is not None else 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return default if default is not None else 0
        return int(stripped)
    if value is None:
        return default if default is not None else 0
    return int(value)


def normalize_role_run_id(value: Any) -> str:
    """Extract role_run_id from common LLM/MCP mistake shapes."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("role_run_id", "id", "text"):
            if key in value:
                return normalize_role_run_id(value[key])
    raise ValueError(
        "role_run_id must be a string or an object containing role_run_id/text/id; "
        f"got {type(value).__name__}: {value!r}"
    )


def _build_invalid_role_run_id_error(field_name: str = "role_run_id") -> dict:
    """Build an LLM-friendly error for invalid role_run_id."""
    return {
        "status": "failed",
        "error": {
            "type": "InvalidRoleRunId",
            "message": (
                f"Invalid {field_name}. Expected a plain string like "
                f'"20260606-215637-1c1074-scout-1". You passed an object. '
                f"If this object came from a previous response, pass only its "
                f"'{field_name}' field. Retry with the existing {field_name} string."
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
    return {
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
                "timeout_seconds": 60,
                "poll_interval_seconds": 10,
            },
        },
    }


_ARTIFACT_FIELD_NAME_MAP: dict[str, str] = {
    "scout_report": "scout_report_artifact_id",
    "architect_plan": "architect_plan_artifact_id",
    "coder_report": "coder_report_artifact_id",
    "reviewer_report": "reviewer_report_artifact_id",
    "publisher_instructions": "publisher_instructions_artifact_id",
}


def _looks_like_old_nested_role_call_payload(value: Any) -> bool:
    """Return True if *value* resembles an old nested role_call payload."""
    if not isinstance(value, dict):
        return False
    keys = set(value.keys())
    if "input_artifacts" in keys:
        return True
    if "metadata" in keys:
        return True
    if {"role", "user_task", "idempotency_key"}.issubset(keys):
        return True
    return False


def _invalid_flat_role_call_error(field_name: str) -> dict:
    """Return InvalidFlatRoleCallPayload error dict for *field_name*."""
    return {
        "status": "failed",
        "error": {
            "type": "InvalidFlatRoleCallPayload",
            "message": (
                f"Field '{field_name}' must be a plain scalar value, not an object. "
                "Scalar wrappers like {{\"text\": \"...\"}} are normalized by the server. "
                "This error means a real nested payload was passed. "
                "Pass artifact ids in dedicated fields: scout_report_artifact_id, "
                "architect_plan_artifact_id, coder_report_artifact_id, "
                "reviewer_report_artifact_id, publisher_instructions_artifact_id."
            ),
            "correct_example": {
                "role": "scout",
                "user_task": "Analyze repository ...",
                "repository": "https://github.com/example/repo",
                "feature": "feature-name",
                "idempotency_key": "feature-scout",
            },
            "retryable": True,
        },
    }


def _looks_like_artifact_content(value: Any) -> bool:
    """Return True if *value* looks like full artifact content (not an artifact_id)."""
    if not isinstance(value, dict):
        return False
    if "content" in value and len(value) == 1:
        inner = value["content"]
        if isinstance(inner, str) and len(inner) > 20:
            return True
    if "artifact_content" in value:
        return True
    if "result" in value:
        return True
    if "messages" in value and isinstance(value["messages"], list):
        return True
    if "tool_calls" in value and isinstance(value["tool_calls"], list):
        return True
    return False


def _artifact_content_error(field_name: str) -> dict:
    """Return structured error for artifact content passed as artifact_id."""
    return {
        "status": "failed",
        "error": {
            "type": "ArtifactContentAsId",
            "retryable": True,
            "message": (
                f"Field '{field_name}' appears to contain artifact content, not an artifact ID. "
                "Pass only the artifact ID (e.g., 'art_abc123'), not the full content."
            ),
            "correct_example": {
                "role": "scout",
                "user_task": "Analyze repository ...",
                "repository": "https://github.com/example/repo",
                "feature": "feature-name",
                "scout_report_artifact_id": "art_abc123",
            },
            "do_not": [
                "Do not pass full artifact content as artifact_id.",
                "Do not pass nested metadata/input_artifacts/context.",
            ],
        },
    }


# Module-level state for loop detection
_invalid_call_fingerprints: dict[str, list[dict]] = {}
_LOOP_GUARD_MAX_REPEATED = 2
_LOOP_GUARD_TTL_SECONDS = 300


def _compute_payload_fingerprint(payload: dict) -> str:
    """Compute a hash fingerprint of a tool call payload for loop detection."""
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


def _check_loop_guard(tool_name: str, payload: dict) -> dict | None:
    """Check if the same invalid payload was repeated too many times."""
    fp = _compute_payload_fingerprint(payload)
    now = datetime.now(timezone.utc)

    if tool_name not in _invalid_call_fingerprints:
        _invalid_call_fingerprints[tool_name] = []

    _invalid_call_fingerprints[tool_name] = [
        entry for entry in _invalid_call_fingerprints[tool_name]
        if (now - entry["ts"]).total_seconds() < _LOOP_GUARD_TTL_SECONDS
    ]

    for entry in _invalid_call_fingerprints[tool_name]:
        if entry["fp"] == fp:
            entry["count"] += 1
            if entry["count"] > _LOOP_GUARD_MAX_REPEATED:
                return {
                    "status": "failed",
                    "error": {
                        "type": "RepeatedInvalidToolCall",
                        "retryable": False,
                        "message": (
                            f"The same invalid tool call was repeated {_LOOP_GUARD_MAX_REPEATED + 1} times. "
                            "Stop retrying and use correct_example exactly."
                        ),
                        "correct_example": {
                            "role": "scout",
                            "user_task": "Analyze repository ...",
                            "repository": "https://github.com/example/repo",
                            "feature": "feature-name",
                            "idempotency_key": "feature-scout",
                        },
                    },
                }
            return None

    _invalid_call_fingerprints[tool_name].append({
        "fp": fp,
        "count": 1,
        "ts": now,
    })
    return None


# ---------------------------------------------------------------------------
# Public MCP tools for Head of IT
# ---------------------------------------------------------------------------

@MCP.tool()
def role_list() -> dict:
    """List available specialist roles and return usage/routing hints for Head of IT. Call this first to learn flat role_call artifact fields and workflow."""
    roles = list_roles()

    def _compute_required_flat_fields(requires_artifacts):
        return [
            _ARTIFACT_FIELD_NAME_MAP.get(art, art + "_artifact_id")
            for art in requires_artifacts
        ]

    roles_with_flat_fields = []
    for r in roles:
        req = r.get("requires_artifacts", [])
        roles_with_flat_fields.append({
            "name": r["name"],
            "description": r.get("description", ""),
            "readonly": r["readonly"],
            "requires_artifacts": req,
            "required_flat_fields": _compute_required_flat_fields(req),
            "output_artifact_type": r.get("output_artifact", ""),
        })

    workflow_steps = []
    for i, r in enumerate(roles, start=1):
        req = r.get("requires_artifacts", [])
        produces = r.get("output_artifact", "")
        requires_list = []
        for r2 in roles:
            r2_output = r2.get("output_artifact", "")
            if r2_output and r2_output in req:
                requires_list.append(r2["name"])
        workflow_steps.append({
            "step": i,
            "role": r["name"],
            "requires": requires_list,
            "produces": produces,
        })

    return {
        "tools": {
            "allowed": ["role_list", "role_call", "role_wait"],
        },
        "workflow": workflow_steps,
        "rules": [
            "Use role_call to start a role.",
            "Use role_wait to wait for a started role.",
            "Never call role_call twice for polling.",
            "Never invent artifact ids.",
            "Pass artifact ids, not artifact contents.",
            "Only one role may run at a time.",
            "Use flat string fields only.",
        ],
        "examples": {
            "start_scout": {
                "tool": "role_call",
                "arguments": {
                    "role": "scout",
                    "user_task": "Analyze repository ...",
                    "repository": "https://github.com/metacoma/openhands-llm-call",
                    "feature": "llm-proof-mcp-tools",
                    "idempotency_key": "llm-proof-mcp-tools-scout",
                },
            },
            "wait": {
                "tool": "role_wait",
                "arguments": {
                    "role_run_id": "20260608-abc-scout-1",
                    "timeout_seconds": 60,
                    "poll_interval_seconds": 10,
                },
            },
        },
        "roles": roles_with_flat_fields,
        "public_tools": ["role_list", "role_call", "role_wait"],
        "flat_role_call_contract": {
            "use_only_flat_scalar_fields": True,
            "allowed_tools": ["role_list", "role_call", "role_wait"],
            "allowed_role_call_fields": [
                "role",
                "user_task",
                "repository",
                "feature",
                "scout_report_artifact_id",
                "architect_plan_artifact_id",
                "coder_report_artifact_id",
                "reviewer_report_artifact_id",
                "publisher_instructions_artifact_id",
                "idempotency_key",
            ],
            "artifact_fields": dict(_ARTIFACT_FIELD_NAME_MAP),
            "examples": {
                "start_scout": {
                    "tool": "role_call",
                    "arguments": {
                        "role": "scout",
                        "user_task": "Analyze repository ...",
                        "repository": "https://github.com/metacoma/openhands-llm-call",
                        "feature": "llm-proof-mcp-tools",
                        "idempotency_key": "llm-proof-mcp-tools-scout",
                    },
                },
            },
        },
        "routing_examples": {
            "architect_after_scout": {
                "role": "architect",
                "scout_report_artifact_id": "<artifacts.primary.artifact_id from scout role_wait>",
            },
            "coder_after_architect": {
                "role": "coder",
                "scout_report_artifact_id": "<scout_report artifact id>",
                "architect_plan_artifact_id": "<architect_plan artifact id>",
            },
            "reviewer_after_coder": {
                "role": "reviewer",
                "scout_report_artifact_id": "<scout_report artifact id>",
                "architect_plan_artifact_id": "<architect_plan artifact id>",
                "coder_report_artifact_id": "<coder_report artifact id>",
            },
            "publisher_after_pass": {
                "role": "publisher",
                "reviewer_report_artifact_id": "<reviewer_report artifact id>",
            },
        },
    }


@MCP.tool()
def role_call(
    role: McpRole,
    user_task: McpString,
    repository: McpString = "",
    feature: McpString = "",
    scout_report_artifact_id: McpString = "",
    architect_plan_artifact_id: McpString = "",
    coder_report_artifact_id: McpString = "",
    reviewer_report_artifact_id: McpString = "",
    publisher_instructions_artifact_id: McpString = "",
    idempotency_key: McpString = "",
) -> dict:
    """Start one specialist role. Use flat scalar fields only: role, user_task, repository, feature, *_artifact_id, idempotency_key. After role_call returns role_run_id, call role_wait with the same role_run_id. Do not call role_call again for polling. Artifact routing: architect needs scout_report_artifact_id; coder needs scout_report_artifact_id + architect_plan_artifact_id; reviewer needs scout_report_artifact_id + architect_plan_artifact_id + coder_report_artifact_id; publisher needs reviewer_report_artifact_id. For detailed routing, call role_list."""
    # ------------------------------------------------------------------
    # Debug logging
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
            role_shape = safe_json_shape(role)
            role_type = role_shape.get("type", type(role).__name__) if isinstance(role_shape, dict) else type(role).__name__
            role_preview_val = safe_json_shape(role).get("preview", str(role)[:200]) if isinstance(safe_json_shape(role), dict) else str(role)[:200]
            ut_shape = safe_json_shape(user_task)
            ut_type = ut_shape.get("type", type(user_task).__name__) if isinstance(ut_shape, dict) else type(user_task).__name__
            ut_len = ut_shape.get("len", len(str(user_task))) if isinstance(ut_shape, dict) else len(str(user_task))
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
    # Detect bad nested payload in ANY field (BLOCKER 1)
    # ------------------------------------------------------------------
    _raw_fields = {
        "role": role,
        "user_task": user_task,
        "repository": repository,
        "feature": feature,
        "scout_report_artifact_id": scout_report_artifact_id,
        "architect_plan_artifact_id": architect_plan_artifact_id,
        "coder_report_artifact_id": coder_report_artifact_id,
        "reviewer_report_artifact_id": reviewer_report_artifact_id,
        "publisher_instructions_artifact_id": publisher_instructions_artifact_id,
        "idempotency_key": idempotency_key,
    }

    _loop_guard_fp = None
    for field_name, raw_value in _raw_fields.items():
        if _looks_like_old_nested_role_call_payload(raw_value):
            _loop_guard_fp = _compute_payload_fingerprint(_raw_fields)
            loop_error = _check_loop_guard("role_call", _raw_fields)
            if loop_error:
                return loop_error
            return _invalid_flat_role_call_error(field_name)
        if _looks_like_artifact_content(raw_value):
            _loop_guard_fp = _compute_payload_fingerprint(_raw_fields)
            loop_error = _check_loop_guard("role_call", _raw_fields)
            if loop_error:
                return loop_error
            return _artifact_content_error(field_name)

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
    normalized_idempotency_key = unwrap_scalar(idempotency_key) or ""

    # ------------------------------------------------------------------
    # Validate artifact ID format on UNWRAPPED values
    # ------------------------------------------------------------------
    _unwrapped_artifact_fields = {
        "scout_report_artifact_id": normalized_scout_report,
        "architect_plan_artifact_id": normalized_architect_plan,
        "coder_report_artifact_id": normalized_coder_report,
        "reviewer_report_artifact_id": normalized_reviewer_report,
        "publisher_instructions_artifact_id": normalized_publisher_instructions,
    }

    for field_name, unwrapped_value in _unwrapped_artifact_fields.items():
        if unwrapped_value is None or unwrapped_value == "":
            continue
        raw_str = str(unwrapped_value) if unwrapped_value is not None else ""
        if raw_str and not raw_str.startswith("art_"):
            return {
                "status": "failed",
                "error": {
                    "type": "InvalidArtifactId",
                    "message": f"{field_name} must be an artifact id like art_..., got {unwrapped_value!r}",
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

    result = role_lifecycle.role_call_start_impl(
        role=normalized_role,
        user_task=str(normalized_user_task) if normalized_user_task else "",
        input_artifacts=internal_input_artifacts,
        metadata=internal_metadata,
        api_key="",
        llm_model=None,
        url=None,
        idempotency_key=str(normalized_idempotency_key) if normalized_idempotency_key else None,
    )

    # ------------------------------------------------------------------
    # Enrich running response with next_action and do_not for LLM guidance
    # ------------------------------------------------------------------
    if result.get("status") == "running":
        rid = result.get("role_run_id", "")
        result["next_action"] = {
            "tool": "role_wait",
            "arguments": {
                "role_run_id": rid,
                "timeout_seconds": 60,
                "poll_interval_seconds": 10,
            },
            "hint": "Use repeated short polling. Call role_wait again with the same role_run_id until status=completed.",
        }
        result["do_not"] = [
            "Do not call role_call again for this role_run_id.",
            "Do not start another role until this run is terminal.",
            "Use role_wait to wait for completion.",
        ]

    # ------------------------------------------------------------------
    # Enrich failed response with next_action / do_not for LLM guidance
    # ------------------------------------------------------------------
    if result.get("status") == "failed" and "error" in result:
        err = result["error"]
        err_type = err.get("type", "")

        if err_type == "MissingRequiredArtifact":
            _MISSING_ARTIFACT_TO_ROLE = {
                "scout_report": "scout",
                "architect_plan": "architect",
                "coder_report": "coder",
                "reviewer_report": "reviewer",
                "publisher_instructions": "publisher",
            }
            msg = err.get("message", "")
            missing_artifact = None
            if "missing required artifact: " in msg:
                missing_artifact = msg.split("missing required artifact: ")[1].split(".")[0].strip()
            producing_role = _MISSING_ARTIFACT_TO_ROLE.get(missing_artifact, "scout")
            err["next_action"] = {
                "tool": "role_call",
                "arguments_hint": {
                    "role": producing_role,
                },
            }
            err["do_not"] = [
                "Do not invent artifact ids.",
                "Do not pass full artifact text instead of artifact_id.",
            ]
        elif err_type == "AnotherRoleRunning":
            existing_rid = err.get("existing_role_run_id", "")
            err["next_action"] = {
                "tool": "role_wait",
                "arguments": {
                    "role_run_id": existing_rid,
                    "timeout_seconds": 60,
                    "poll_interval_seconds": 10,
                },
                "hint": "Use repeated short polling. Call role_wait again with the same role_run_id until status=completed.",
            }
            err["do_not"] = [
                "Do not call role_call again.",
                "Do not create a new idempotency_key.",
                "Do not start another role before the current one is terminal.",
            ]
        elif err_type == "UnknownRole":
            err["next_action"] = {
                "tool": "role_list",
                "arguments": {},
            }
            err["do_not"] = [
                "Do not invent role names.",
                "Call role_list to see available roles.",
            ]

    return result


def _make_response_nonce() -> str:
    """Generate a unique nonce for role_wait responses.

    Format: YYYYMMDDTHHMMSSffffffZ-<8-char-hex>
    Uses UTC time and uuid4 for uniqueness.
    """
    now = datetime.now(timezone.utc)
    return f"{now.strftime('%Y%m%dT%H%M%S')}{now.microsecond:06d}Z-{uuid.uuid4().hex[:8]}"


def normalize_request_nonce(value: Any) -> str | None:
    """Normalize request_nonce to a stable JSON-serializable string.

    - None → None (omitted from response)
    - str → returned unchanged
    - Other JSON-serializable values → json.dumps with sort_keys=True
    - Non-serializable → str(value) fallback
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except TypeError:
        return str(value)


@MCP.tool()
def role_wait(
    role_run_id: McpString,
    timeout_seconds: McpInt = 30,
    poll_interval_seconds: McpInt = 5,
    request_nonce: Any = None,
) -> dict:
    """Wait for a role_run_id returned by role_call. If status=running and timeout=true, call role_wait again with the same role_run_id. Never call role_call again for polling. When completed, use only control_summary and artifacts.primary.artifact_id/artifact_type. role_wait returns only status, control_summary, and artifact id references.

    IMPORTANT FOR LLM CALLERS:
    Always include request_nonce when calling role_wait.
    request_nonce is optional and may be any JSON value (string, number, boolean,
    object, array, or null). A UTC timestamp string is recommended, for example
    "2026-06-10T16:45:30Z". Objects are accepted too, but a plain string is
    preferred. The server normalizes request_nonce for traceability only. It does
    not affect polling, role state, artifacts, or result content. When provided,
    request_nonce is echoed back (normalized) in the response. The server also
    returns response_nonce independently (do not confuse the two)."""
    # Defensive parsing for nested LLM mistakes.
    raw_role_arg = role_run_id
    _timeout = timeout_seconds
    _poll = poll_interval_seconds

    if isinstance(raw_role_arg, dict):
        has_nested_timeout = "timeout_seconds" in raw_role_arg
        has_nested_poll = "poll_interval_seconds" in raw_role_arg

        if has_nested_timeout or has_nested_poll:
            _timeout = raw_role_arg.get("timeout_seconds") if has_nested_timeout else _timeout
            _poll = raw_role_arg.get("poll_interval_seconds") if has_nested_poll else _poll

    try:
        normalized_role_run_id = normalize_role_run_id(raw_role_arg)
    except ValueError:
        err = _build_invalid_role_run_id_error("role_run_id")
        err["response_nonce"] = _make_response_nonce()
        if request_nonce is not None:
            err["request_nonce"] = normalize_request_nonce(request_nonce)
        return err

    if not normalized_role_run_id:
        result = {
            "status": "failed",
            "error": {
                "type": "MissingRoleRunId",
                "message": "role_run_id is required",
                "retryable": False,
            },
        }
        result["response_nonce"] = _make_response_nonce()
        if request_nonce is not None:
            result["request_nonce"] = normalize_request_nonce(request_nonce)
        return result

    normalized_timeout = normalize_int(_timeout, default=30)
    normalized_poll_interval = normalize_int(_poll, default=5)

    # Call the new lifecycle-aware role_wait implementation
    from . import role_lifecycle

    result = role_lifecycle.role_lifecycle_wait_impl(
        role_run_id=normalized_role_run_id,
        timeout_seconds=normalized_timeout,
        poll_interval_seconds=normalized_poll_interval,
    )

    # ------------------------------------------------------------------
    # Enrich running response with next_action and do_not for LLM guidance
    # ------------------------------------------------------------------
    if result.get("status") == "running":
        rid = result.get("role_run_id", "")
        result["next_action"] = {
            "tool": "role_wait",
            "arguments": {
                "role_run_id": rid,
                "timeout_seconds": 60,
                "poll_interval_seconds": 10,
            },
            "hint": "Use repeated short polling. Call role_wait again with the same role_run_id until status=completed.",
        }
        result["do_not"] = [
            "Do not call role_call again.",
            "Do not start another role while this role is running.",
        ]

    # ------------------------------------------------------------------
    # Enrich completed response with next_action/arguments_hint for LLM guidance
    # ------------------------------------------------------------------
    if result.get("status") == "completed":
        role_name = result.get("role", "")
        artifacts = result.get("artifacts", {})
        primary_artifact_id = ""
        if isinstance(artifacts.get("primary"), dict):
            primary_artifact_id = artifacts["primary"].get("artifact_id", "")

        next_role_map = {
            "scout": "architect",
            "architect": "coder",
            "coder": "reviewer",
            "reviewer": "publisher",
            "publisher": None,
            "coder_fix": None,
        }
        next_role = next_role_map.get(role_name)

        if next_role:
            hint = {"role": next_role}
            _ROLE_OUTPUT_HINT_MAP = {
                "scout": ("architect", {"scout_report_artifact_id": primary_artifact_id}),
                "architect": ("coder", {"architect_plan_artifact_id": primary_artifact_id}),
                "coder": ("reviewer", {"coder_report_artifact_id": primary_artifact_id}),
                "reviewer": ("publisher", {"reviewer_report_artifact_id": primary_artifact_id}),
                "publisher": None,
                "coder_fix": None,
            }
            hint_entry = _ROLE_OUTPUT_HINT_MAP.get(role_name)
            if hint_entry:
                _, artifact_hint = hint_entry
                hint.update(artifact_hint)
            result["next_action"] = {
                "tool": "role_call",
                "arguments_hint": hint,
            }

    result["response_nonce"] = _make_response_nonce()
    if request_nonce is not None:
        result["request_nonce"] = normalize_request_nonce(request_nonce)
    return result


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    # ------------------------------------------------------------------
    # BLOCKER 4: Startup log proving which tools are public.
    # ------------------------------------------------------------------
    _public_tools = [t.name for t in MCP._tool_manager.list_tools()]
    logger.info("public_mcp_tools=%s", _public_tools)

    app = MCP.streamable_http_app()
    uvicorn.run(app, host=MCP_HOST, port=MCP_PORT)
