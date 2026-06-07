#!/usr/bin/env python3
"""Role registry: load and validate role specs from config/roles.yaml."""

import os
from dataclasses import dataclass, asdict
from typing import Optional

import yaml


@dataclass
class RoleSpec:
    """Specification for a named worker role."""

    name: str
    description: str
    model: str
    prompt_template: str
    readonly: bool
    timeout_minutes: int
    requires_artifacts: list[str]
    output_artifact: str
    summary_artifact: str


_ROLES: dict[str, RoleSpec] | None = None

REQUIRED_FIELDS = {
    "description",
    "model",
    "prompt_template",
    "readonly",
    "timeout_minutes",
    "requires_artifacts",
    "output_artifact",
}

SUMMARY_ARTIFACT_DEFAULTS: dict[str, str] = {
    "scout": "scout_summary",
    "architect": "architect_summary",
    "coder": "coder_summary",
    "reviewer": "reviewer_summary",
    "coder_fix": "coder_fix_summary",
    "publisher": "publisher_summary",
}


def _get_config_path() -> str:
    """Return the role config path from env or the default."""
    return os.getenv("ROLE_CONFIG_PATH", "config/roles.yaml")


def load_roles(config_path: Optional[str] = None) -> dict[str, RoleSpec]:
    """Load and validate roles from a YAML config file.

    Parameters
    ----------
    config_path :
        Path to the YAML file.  Defaults to ``ROLE_CONFIG_PATH`` env var
        or ``config/roles.yaml``.

    Returns
    -------
    dict
        Mapping of role name → RoleSpec.

    Raises
    ------
    FileNotFoundError
        If the config file does not exist.
    ValueError
        If the config is missing required structure or fields.
    """
    global _ROLES
    path = config_path or _get_config_path()

    if not os.path.isfile(path):
        raise FileNotFoundError(f"Role config not found: {path}")

    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    if not isinstance(data, dict) or "roles" not in data:
        raise ValueError(
            f"Invalid role config: missing top-level 'roles' key in {path}"
        )

    roles: dict[str, RoleSpec] = {}
    for name, spec_dict in data["roles"].items():
        if not name or not isinstance(name, str):
            raise ValueError(f"Invalid role name in {path}")

        missing = REQUIRED_FIELDS - set(spec_dict.keys())
        if missing:
            raise ValueError(
                f"Role '{name}' missing required fields: "
                f"{', '.join(sorted(missing))}"
            )

        if not spec_dict.get("prompt_template"):
            raise ValueError(
                f"Role '{name}': prompt_template must be present"
            )

        if not spec_dict.get("output_artifact"):
            raise ValueError(
                f"Role '{name}': output_artifact must be present"
            )

        timeout = spec_dict.get("timeout_minutes")
        if not isinstance(timeout, int) or timeout <= 0:
            raise ValueError(
                f"Role '{name}': timeout_minutes must be a positive integer"
            )

        roles[name] = RoleSpec(
            name=name,
            description=spec_dict["description"],
            model=spec_dict["model"],
            prompt_template=spec_dict["prompt_template"],
            readonly=spec_dict["readonly"],
            timeout_minutes=spec_dict["timeout_minutes"],
            requires_artifacts=spec_dict.get("requires_artifacts") or [],
            output_artifact=spec_dict["output_artifact"],
            summary_artifact=spec_dict.get(
                "summary_artifact"
            ) or SUMMARY_ARTIFACT_DEFAULTS.get(name, f"{name}_summary"),
        )

    _ROLES = roles
    return roles


def get_role(name: str) -> RoleSpec:
    """Return the RoleSpec for *name*, loading the config if needed.

    Raises
    ------
    KeyError
        If the role does not exist.
    """
    global _ROLES
    if _ROLES is None:
        load_roles()
    if name not in _ROLES:
        available = ", ".join(sorted(_ROLES.keys()))
        raise KeyError(
            f"Unknown role '{name}'. Available roles: {available}."
        )
    return _ROLES[name]


def list_roles() -> list[dict]:
    """Return a list of role dicts (sanitized for MCP output).

    Does not expose secrets (model is included because this is a
    local homelab system).
    """
    global _ROLES
    if _ROLES is None:
        load_roles()
    return [asdict(r) for r in _ROLES.values()]
