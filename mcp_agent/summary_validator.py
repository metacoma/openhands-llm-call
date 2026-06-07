#!/usr/bin/env python3
"""Summary JSON validation, repair, and safe fallback for role runs.

Provides:
- ``validate_summary`` — validates summary JSON against the control-plane schema.
- ``repair_summary`` — renders a repair prompt for invalid summaries.
- ``safe_fallback_summary`` — generates a safe fallback when parsing fails.
- ``derive_reviewer_action_from_main_artifact`` — extracts ACTION from reviewer's main artifact.
"""

import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger("openhands-mcp")

# Required fields in the control-summary JSON schema
_REQUIRED_FIELDS = {
    "status",
    "role",
    "summary",
    "primary_artifact_name",
    "blocking",
    "risk_level",
    "action",
    "blocking_summary",
}

# Forbidden fields that must not appear in summaries
_FORBIDDEN_FIELDS = {
    "next_role",
    "ready_for_next_role",
}

# Valid values for status
_VALID_STATUSES = {"completed", "blocked"}

# Valid values for risk_level
_VALID_RISK_LEVELS = {"LOW", "MEDIUM", "HIGH", None}


def validate_summary(
    role: str,
    summary_artifact_name: str,
    json_str: str,
) -> dict[str, Any]:
    """Validate summary JSON and return parsed dict or error dict.

    Parameters
    ----------
    role :
        The role name that produced the summary.
    summary_artifact_name :
        The expected primary artifact name (output_artifact from RoleSpec).
    json_str :
        The raw summary text to validate.

    Returns
    -------
    dict
        Parsed summary dict if valid, or an error dict with keys
        ``{"valid": False, "error": {...}}`` if invalid.
    """
    # Strip markdown code-block wrappers if present
    stripped = json_str.strip()
    if stripped.startswith("```"):
        lines = stripped.split("\n")
        # Remove first and last lines if they are code-block markers
        if len(lines) >= 2 and lines[-1].strip().startswith("```"):
            stripped = "\n".join(lines[1:-1]).strip()

    # Try JSON parse
    try:
        data = json.loads(stripped)
    except (json.JSONDecodeError, ValueError) as exc:
        return {
            "valid": False,
            "error": {
                "type": "SummaryParseError",
                "message": f"Summary JSON parsing failed: {exc}",
            },
        }

    if not isinstance(data, dict):
        return {
            "valid": False,
            "error": {
                "type": "SummaryParseError",
                "message": "Summary must be a JSON object.",
            },
        }

    # Check required fields
    missing = _REQUIRED_FIELDS - set(data.keys())
    if missing:
        return {
            "valid": False,
            "error": {
                "type": "SummaryMissingFields",
                "message": f"Missing required fields: {', '.join(sorted(missing))}",
            },
        }

    # Check forbidden fields
    extra = set(data.keys()) & _FORBIDDEN_FIELDS
    if extra:
        return {
            "valid": False,
            "error": {
                "type": "SummaryForbiddenFields",
                "message": f"Forbidden fields present: {', '.join(sorted(extra))}",
            },
        }

    # Validate status
    if data["status"] not in _VALID_STATUSES:
        return {
            "valid": False,
            "error": {
                "type": "SummaryInvalidField",
                "message": f"Invalid status: {data['status']!r}. Must be one of {sorted(_VALID_STATUSES)}.",
            },
        }

    # Validate role matches executed role
    if data["role"] != role:
        return {
            "valid": False,
            "error": {
                "type": "SummaryRoleMismatch",
                "message": f"Summary role '{data['role']}' does not match executed role '{role}'.",
            },
        }

    # Validate primary_artifact_name matches output_artifact
    if data["primary_artifact_name"] != summary_artifact_name:
        return {
            "valid": False,
            "error": {
                "type": "SummaryArtifactMismatch",
                "message": (
                    f"Summary primary_artifact_name '{data['primary_artifact_name']}' "
                    f"does not match expected '{summary_artifact_name}'."
                ),
            },
        }

    # Validate blocking is boolean
    if not isinstance(data["blocking"], bool):
        return {
            "valid": False,
            "error": {
                "type": "SummaryInvalidField",
                "message": f"blocking must be boolean, got {type(data['blocking']).__name__}.",
            },
        }

    # Validate blocking_summary is a list
    if not isinstance(data["blocking_summary"], list):
        return {
            "valid": False,
            "error": {
                "type": "SummaryInvalidField",
                "message": "blocking_summary must be a list.",
            },
        }

    # Validate risk_level
    if data["risk_level"] not in _VALID_RISK_LEVELS:
        return {
            "valid": False,
            "error": {
                "type": "SummaryInvalidField",
                "message": f"Invalid risk_level: {data['risk_level']!r}.",
            },
        }

    # Role-specific action validation
    if role == "reviewer":
        if data["action"] not in ("PASS", "BLOCKER"):
            return {
                "valid": False,
                "error": {
                    "type": "SummaryInvalidField",
                    "message": (
                        f"Reviewer action must be PASS or BLOCKER, got {data['action']!r}."
                    ),
                },
            }
    else:
        if data["action"] is not None:
            return {
                "valid": False,
                "error": {
                    "type": "SummaryInvalidField",
                    "message": (
                        f"Non-reviewer role '{role}' must have action=null, "
                        f"got {data['action']!r}."
                    ),
                },
            }

    # All validations passed — return parsed data
    data["valid"] = True
    return data


def repair_summary(role: str, summary_artifact_name: str) -> str:
    """Render the repair prompt for invalid summaries.

    Parameters
    ----------
    role :
        The role name that produced the summary.
    summary_artifact_name :
        The expected primary artifact name.

    Returns
    -------
    str
        The rendered repair prompt text.
    """
    from .prompt_renderer import render_prompt

    return render_prompt(
        template_path="prompts/summaries/role_summary_repair.md",
        variables={
            "role": role,
            "primary_artifact_name": summary_artifact_name,
        },
    )


def safe_fallback_summary(
    role: str,
    summary_artifact_name: str,
    is_reviewer: bool = False,
    main_artifact_content: Optional[str] = None,
) -> dict[str, Any]:
    """Generate a safe fallback summary when parsing fails.

    Parameters
    ----------
    role :
        The role name.
    summary_artifact_name :
        The expected primary artifact name.
    is_reviewer :
        Whether the role is reviewer.
    main_artifact_content :
        The raw main artifact content (used to derive action for reviewer).

    Returns
    -------
    dict
        A safe fallback summary dict.
    """
    # For reviewer, try to derive action from main artifact
    action = None
    if is_reviewer and main_artifact_content:
        derived = derive_reviewer_action_from_main_artifact(main_artifact_content)
        if derived:
            action = derived

    if is_reviewer and action is None:
        # Cannot derive safely — block the pipeline.
        # Reviewer is the only role that controls PASS/BLOCKER routing.
        # If we return action=null, Head of Engineering cannot safely route.
        return {
            "valid": True,
            "status": "blocked",
            "role": role,
            "summary": (
                "Reviewer completed, but MCP could not parse or derive "
                "PASS/BLOCKER from the summary."
            ),
            "primary_artifact_name": summary_artifact_name,
            "blocking": True,
            "risk_level": "HIGH",
            "action": "BLOCKER",
            "blocking_summary": [
                "Reviewer summary parsing failed and ACTION could not be "
                "derived safely.",
            ],
        }

    return {
        "valid": True,
        "status": "completed",
        "role": role,
        "summary": (
            f"Role completed, but summary parsing failed. "
            f"Inspect the summary artifact for details."
        ),
        "primary_artifact_name": summary_artifact_name,
        "blocking": False,
        "risk_level": None,
        "action": action,
        "blocking_summary": [],
    }


def derive_reviewer_action_from_main_artifact(
    main_artifact_content: str,
) -> Optional[str]:
    """Extract ACTION: PASS or ACTION: BLOCKER from reviewer's main artifact.

    Parameters
    ----------
    main_artifact_content :
        The raw text of the reviewer's primary artifact.

    Returns
    -------
    str or None
        ``"PASS"`` or ``"BLOCKER"`` if found, else ``None``.
    """
    m = re.search(
        r"ACTION:\s*(PASS|BLOCKER)",
        main_artifact_content,
        re.IGNORECASE,
    )
    return m.group(1).upper() if m else None
