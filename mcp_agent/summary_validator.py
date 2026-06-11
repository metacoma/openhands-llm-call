#!/usr/bin/env python3
"""Summary structured-text validation, repair, and safe fallback for role runs.

Provides:
- ``validate_summary`` — validates summary (structured text or JSON) against the control-plane schema.
- ``repair_summary`` — renders a repair prompt for invalid summaries.
- ``safe_fallback_summary`` — generates a safe fallback when parsing fails.
- ``derive_reviewer_action_from_main_artifact`` — extracts ACTION from reviewer's main artifact.
"""

import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger("openhands-mcp")

# Required fields in the control-summary schema
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

# Structured-text markers
_SUMMARY_BEGIN = "ROLE_SUMMARY_BEGIN"
_SUMMARY_END = "ROLE_SUMMARY_END"


# ---------------------------------------------------------------------------
# Structured-text parser
# ---------------------------------------------------------------------------

def _parse_structured_summary(
    role: str,
    summary_artifact_name: str,
    text: str,
) -> dict[str, Any]:
    """Parse a structured-text summary block and validate it.

    Returns a dict with ``valid=True`` (parsed data) or ``valid=False`` (error).
    """
    # Extract content between markers if present
    begin_idx = text.find(_SUMMARY_BEGIN)
    end_idx = text.find(_SUMMARY_END)

    if begin_idx != -1 and end_idx != -1 and end_idx > begin_idx:
        block = text[begin_idx + len(_SUMMARY_BEGIN):end_idx].strip()
        markers_found = True
    else:
        block = text.strip()
        markers_found = False

    # Parse header fields by line prefixes (case-insensitive)
    fields: dict[str, str] = {}
    blockers: list[str] = []
    in_blockers = False

    for raw_line in block.split("\n"):
        stripped = raw_line.strip()
        if not stripped:
            continue

        if in_blockers:
            # Blocker lines start with "- " or just text until end marker
            if stripped.startswith("- "):
                blockers.append(stripped[2:].strip())
            elif stripped.startswith("-"):
                blockers.append(stripped[1:].strip())
            else:
                # Non-bullet line ends blocker section
                in_blockers = False
                # Fall through to field parsing below

        if not in_blockers:
            m = re.match(
                r"^(STATUS|ROLE|PRIMARY_ARTIFACT|BLOCKERS|BLOCKING|RISK|ACTION|SUMMARY)\s*:\s*(.*)",
                stripped,
                re.IGNORECASE,
            )
            if m:
                key = m.group(1).upper()
                val = m.group(2).strip()
                fields[key] = val
                if key == "BLOCKERS":
                    in_blockers = True
                continue

    # If BLOCKERS header was found but no bullet lines followed, collectors stay empty
    # (treated as empty list)

    # Normalise values
    status = fields.get("STATUS", "").strip().lower() if fields.get("STATUS") else ""
    role_val = fields.get("ROLE", "").strip()
    summary_val = fields.get("SUMMARY", "").strip()
    primary_artifact = fields.get("PRIMARY_ARTIFACT", "").strip()
    blocking_raw = fields.get("BLOCKING", "").strip().lower()
    risk_raw = fields.get("RISK", "").strip().upper()
    action_raw = fields.get("ACTION", "").strip().upper()

    # Convert BLOCKING: yes/no/true/false/y/n/1/0 → bool
    blocking = None
    if blocking_raw in ("yes", "true", "y", "1"):
        blocking = True
    elif blocking_raw in ("no", "false", "n", "0"):
        blocking = False

    # Convert RISK: NONE → None
    risk_level = None
    if risk_raw in ("LOW", "MEDIUM", "HIGH"):
        risk_level = risk_raw
    elif risk_raw == "NONE":
        risk_level = None

    # Convert ACTION: NONE → None
    action = None
    if action_raw in ("PASS", "BLOCKER"):
        action = action_raw
    elif action_raw == "NONE":
        action = None

    # Parse blockers
    blocking_summary: list[str] = []
    if blockers:
        blocking_summary = [b for b in blockers if b.lower() != "none"]
    if not blocking_summary and in_blockers is False and "BLOCKERS" not in fields:
        blocking_summary = []

    # Check markers found (required fields must be present)
    if markers_found:
        missing = set()
        for req in ("STATUS", "ROLE", "SUMMARY", "PRIMARY_ARTIFACT", "BLOCKING", "RISK", "ACTION", "BLOCKERS"):
            if req not in fields:
                missing.add(req)
        if missing:
            return {
                "valid": False,
                "error": {
                    "type": "SummaryMissingFields",
                    "message": f"Missing required fields: {', '.join(sorted(missing))}",
                },
            }

    # Build a temporary dict for the shared validation logic
    data: dict[str, Any] = {
        "status": status,
        "role": role_val,
        "summary": summary_val,
        "primary_artifact_name": primary_artifact,
        "blocking": blocking,
        "risk_level": risk_level,
        "action": action,
        "blocking_summary": blocking_summary,
    }

    # Check required fields (for non-marker case)
    if not markers_found:
        missing = set()
        for req_name, data_key in [
            ("status", "status"),
            ("role", "role"),
            ("summary", "summary"),
            ("primary_artifact_name", "primary_artifact_name"),
            ("blocking", "blocking"),
            ("risk_level", "risk_level"),
            ("action", "action"),
            ("blocking_summary", "blocking_summary"),
        ]:
            if data_key not in data or data[data_key] is None or data[data_key] == []:
                # Only flag if the field is truly absent (not just empty string for summary)
                pass  # We'll check via the _REQUIRED_FIELDS logic below
        # Use the same required-fields check as JSON path
        present_keys = set(fields.keys())
        required_lower = {f.lower() for f in _REQUIRED_FIELDS}
        missing = required_lower - present_keys
        if missing:
            return {
                "valid": False,
                "error": {
                    "type": "SummaryMissingFields",
                    "message": f"Missing required fields: {', '.join(sorted(missing))}",
                },
            }

    # Check forbidden fields — match only when they appear as field names
    # (followed by ':' or at end-of-line) to avoid false positives from
    # natural-language text such as "the next role should be X".
    text_lower = text.lower()
    _forbidden_pattern = re.compile(
        r"(?:^|[\s:])\s*(next_role|ready_for_next_role)\s*:",
        re.IGNORECASE,
    )
    if _forbidden_pattern.search(text_lower):
        return {
            "valid": False,
            "error": {
                "type": "SummaryForbiddenFields",
                "message": "Forbidden fields present: next_role, ready_for_next_role",
            },
        }

    # Validate status
    if status not in _VALID_STATUSES:
        return {
            "valid": False,
            "error": {
                "type": "SummaryInvalidField",
                "message": f"Invalid status: {status!r}. Must be one of completed, blocked.",
            },
        }

    # Validate role matches executed role
    if role_val != role:
        return {
            "valid": False,
            "error": {
                "type": "SummaryRoleMismatch",
                "message": f"Summary role '{role_val}' does not match executed role '{role}'.",
            },
        }

    # Validate primary_artifact_name matches output_artifact
    if primary_artifact != summary_artifact_name:
        return {
            "valid": False,
            "error": {
                "type": "SummaryArtifactMismatch",
                "message": (
                    f"Summary primary_artifact_name '{primary_artifact}' "
                    f"does not match expected '{summary_artifact_name}'."
                ),
            },
        }

    # Validate blocking is boolean
    if blocking is None:
        return {
            "valid": False,
            "error": {
                "type": "SummaryInvalidField",
                "message": f"blocking must be 'yes' or 'no', got {blocking_raw!r}.",
            },
        }

    # Validate blocking_summary is a list (always is from our parser)
    if not isinstance(blocking_summary, list):
        return {
            "valid": False,
            "error": {
                "type": "SummaryInvalidField",
                "message": "blocking_summary must be a list.",
            },
        }

    # Validate risk_level
    if risk_level not in _VALID_RISK_LEVELS:
        return {
            "valid": False,
            "error": {
                "type": "SummaryInvalidField",
                "message": f"Invalid risk_level: {risk_raw!r}.",
            },
        }

    # Role-specific action validation
    if role == "reviewer":
        if action not in ("PASS", "BLOCKER"):
            return {
                "valid": False,
                "error": {
                    "type": "SummaryInvalidField",
                    "message": (
                        f"Reviewer action must be PASS or BLOCKER, got {action_raw!r}."
                    ),
                },
            }
    else:
        if action is not None:
            return {
                "valid": False,
                "error": {
                    "type": "SummaryInvalidField",
                    "message": (
                        f"Non-reviewer role '{role}' must have action=NONE, "
                        f"got {action_raw!r}."
                    ),
                },
            }

    # All validations passed — return parsed data
    data["valid"] = True
    return data


# ---------------------------------------------------------------------------
# Backward-compatible JSON parser (optional fallback)
# ---------------------------------------------------------------------------

def _try_json_parse(
    role: str,
    summary_artifact_name: str,
    text: str,
) -> dict[str, Any]:
    """Try to parse text as JSON (backward compatibility)."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.split("\n")
        if len(lines) >= 2 and lines[-1].strip().startswith("```"):
            stripped = "\n".join(lines[1:-1]).strip()

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

    data["valid"] = True
    return data


def validate_summary(
    role: str,
    summary_artifact_name: str,
    json_str: str,
) -> dict[str, Any]:
    """Validate summary and return parsed dict or error dict.

    Tries structured-text format first, then falls back to JSON
    (for backward compatibility with old prompts).

    Parameters
    ----------
    role :
        The role name that produced the summary.
    summary_artifact_name :
        The expected primary artifact name (output_artifact from RoleSpec).
    json_str :
        The raw summary text to validate (structured text or JSON).

    Returns
    -------
    dict
        Parsed summary dict if valid, or an error dict with keys
        ``{"valid": False, "error": {...}}`` if invalid.
    """
    # 1. Try structured-text parser first
    result = _parse_structured_summary(role, summary_artifact_name, json_str)
    if result.get("valid"):
        return result

    # 2. Fallback: try JSON parser for backward compatibility
    stripped = json_str.strip()
    if stripped.startswith("{") or stripped.startswith("```"):
        result = _try_json_parse(role, summary_artifact_name, json_str)
        if result.get("valid"):
            return result

    # 3. Return structured-text parse error
    return result


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
    primary_artifact_name: str,
    is_reviewer: bool = False,
    main_artifact_content: Optional[str] = None,
) -> dict[str, Any]:
    """Generate a safe fallback summary when parsing fails.

    Parameters
    ----------
    role :
        The role name.
    primary_artifact_name :
        The expected primary artifact name (e.g. output_artifact from RoleSpec).
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
            "primary_artifact_name": primary_artifact_name,
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
            f"The primary artifact was saved. Continue routing using artifact_id."
        ),
        "primary_artifact_name": primary_artifact_name,
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
