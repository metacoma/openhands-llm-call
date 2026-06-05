#!/usr/bin/env python3
"""Prompt template rendering using Jinja2."""

import os
from typing import Any

from jinja2 import Environment, FileSystemLoader, Undefined


# Custom Undefined subclass that renders as empty string for
# missing variables (matching the {{ var | default("") }} pattern).
# It also returns itself for attribute access and calls so that
# chained expressions like ``{{ missing.items() }}`` stay silent.
class _EmptyUndefined(Undefined):
    def __str__(self) -> str:
        return ""

    def __repr__(self) -> str:
        return "<empty>"

    def __getattr__(self, name: str) -> "_EmptyUndefined":
        # Return self for any attribute access so that chained calls
        # (e.g. ``missing.items()``) stay undefined rather than raising.
        return self

    def __call__(self, *args, **kwargs) -> "_EmptyUndefined":
        # Calling an undefined variable (e.g. ``missing()``) returns
        # itself so the chain continues silently.
        return self


def render_prompt(
    template_path: str,
    variables: dict[str, Any],
    template_root: str | None = None,
) -> str:
    """Render a prompt template with the given variables.

    Parameters
    ----------
    template_path :
        Path to the template file relative to *template_root*
        (e.g. ``prompts/scout.md``).
    variables :
        Mapping of variable names to their values.  Every Jinja2
        placeholder in the template must have a corresponding key
        here.  Missing keys render as empty strings.
    template_root :
        Root directory for template resolution.
        Defaults to the current working directory.

    Returns
    -------
    str
        The rendered prompt string.

    Raises
    ------
    FileNotFoundError
        If the template file does not exist.
    """
    root = template_root or os.getcwd()

    env = Environment(
        loader=FileSystemLoader(root),
        undefined=_EmptyUndefined,
    )

    try:
        template = env.get_template(template_path)
    except Exception:
        raise FileNotFoundError(
            f"Prompt template not found: {template_path}"
        )

    return template.render(**variables)
