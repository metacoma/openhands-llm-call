#!/usr/bin/env python3
"""Artifact store for role orchestration.

Provides ``save``, ``list``, and ``get`` operations for role artifacts.
Artifacts are stored under ``<state_dir>/<run_id>/`` alongside role
run records, with companion ``.meta.json`` files for metadata lookup.

Security: all artifact paths are validated to stay within the configured
state directory to prevent path traversal.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Strict allow-list for filesystem path components.
_SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _safe_component(value: str, field: str) -> str:
    """Validate a path component and return it unchanged.

    Rejects empty values, reserved components (``.`` / ``..``),
    path traversal characters, control characters, and any
    character outside the safe allow-list.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"Invalid {field}: empty value")
    if value in {".", ".."}:
        raise ValueError(f"Invalid {field}: reserved path component")
    if "/" in value or "\\" in value or ".." in value:
        raise ValueError(f"Invalid {field}: path traversal is not allowed")
    if any(ord(ch) < 32 for ch in value):
        raise ValueError(f"Invalid {field}: control characters are not allowed")
    if not _SAFE_COMPONENT_RE.fullmatch(value):
        raise ValueError(f"Invalid {field}: unsupported characters")
    return value


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


class ArtifactStore:
    """File-based artifact store for role orchestration.

    Parameters
    ----------
    state_dir :
        Base directory for artifacts.  Defaults to
        ``OPENHANDS_ROLE_STATE_DIR`` or ``.runs``.
    """

    def __init__(self, state_dir: Optional[str] = None) -> None:
        self.state_dir = Path(
            state_dir or os.getenv(
                "OPENHANDS_ROLE_STATE_DIR", ".runs"
            )
        )
        self.state_dir.mkdir(parents=True, exist_ok=True)

    # -- public API --------------------------------------------------------

    def save(
        self,
        run_id: str,
        role_run_id: str,
        role: str,
        artifact_name: str,
        content: str,
    ) -> Dict[str, Any]:
        """Save an artifact and return its metadata record.

        Parameters
        ----------
        run_id :
            The top-level run identifier.
        role_run_id :
            The role-specific run ID.
        role :
            The role name.
        artifact_name :
            Logical name (e.g. ``"scout_report"``).
        content :
            The artifact text content.

        Returns
        -------
        dict
            Artifact metadata record.
        """
        # Validate all user-controlled path components
        safe_run_id = _safe_component(run_id, "run_id")
        _safe_component(role_run_id, "role_run_id")
        _safe_component(artifact_name, "artifact_name")

        run_dir = self.state_dir / safe_run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        # Use role_run_id-based filename to avoid collisions
        safe_name = role_run_id.replace(":", "_")
        filename = f"{safe_name}_{artifact_name}.artifact"
        artifact_path = run_dir / filename

        # Write content
        artifact_path.write_text(content, encoding="utf-8")

        # Write companion metadata
        # Store artifact_path relative to state_dir for safe resolution
        rel_path = f"{safe_run_id}/{filename}"
        meta = {
            "artifact_name": artifact_name,
            "role": role,
            "role_run_id": role_run_id,
            "run_id": run_id,
            "artifact_path": rel_path,
            "created_at": _now_iso(),
        }
        meta_path = run_dir / f"{filename}.meta.json"
        meta_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        return meta

    def list(self, run_id: str) -> List[Dict[str, Any]]:
        """List all artifacts for a given *run_id*.

        Returns
        -------
        list[dict]
            List of artifact metadata records.
        """
        run_dir = self.state_dir / run_id
        if not run_dir.is_dir():
            return []

        artifacts: List[Dict[str, Any]] = []
        for meta_path in sorted(run_dir.glob("*.meta.json")):
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                # Validate the artifact file still exists and is within state_dir
                artifact_rel = data.get("artifact_path", "")
                full_path = self.state_dir / artifact_rel
                # Skip artifacts with escaped paths
                try:
                    self._ensure_under_state_dir(full_path)
                except ValueError:
                    continue
                if full_path.exists():
                    artifacts.append(data)
            except (json.JSONDecodeError, OSError):
                continue
        return artifacts

    def get(
        self,
        run_id: str,
        artifact_name: Optional[str] = None,
        role_run_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get a single artifact by *artifact_name* or *role_run_id*.

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
        dict or None
            Artifact metadata with ``content`` key, or None if not found.

        Raises
        ------
        ValueError
            If the resolved path would escape the state directory.
        """
        run_dir = self.state_dir / run_id
        if not run_dir.is_dir():
            return None

        # Search for the artifact
        candidates: List[Dict[str, Any]] = []
        for meta_path in sorted(run_dir.glob("*.meta.json")):
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            # Filter by artifact_name if provided
            if artifact_name and data.get("artifact_name") != artifact_name:
                continue

            # Filter by role_run_id if provided
            if role_run_id and data.get("role_run_id") != role_run_id:
                continue

            candidates.append(data)

        if not candidates:
            return None

        # If artifact_name is provided, prefer exact match
        if artifact_name:
            for c in candidates:
                if c.get("artifact_name") == artifact_name:
                    return self._read_artifact(c)
            return None

        # If role_run_id is provided, prefer exact match
        if role_run_id:
            for c in candidates:
                if c.get("role_run_id") == role_run_id:
                    return self._read_artifact(c)
            return None

        # Return the first candidate (shouldn't happen if called correctly)
        return self._read_artifact(candidates[0])

    # -- internals ---------------------------------------------------------

    def _ensure_under_state_dir(self, path: Path) -> Path:
        """Validate that *path* resolves within ``state_dir``.

        Raises ``ValueError`` if the resolved path escapes the state
        directory.  Uses ``Path.relative_to()`` instead of string
        prefix checks to avoid the sibling-prefix vulnerability.
        """
        resolved = path.resolve()
        state_resolved = self.state_dir.resolve()
        try:
            resolved.relative_to(state_resolved)
        except ValueError as exc:
            raise ValueError(
                f"Artifact path escapes state directory: {resolved}"
            ) from exc
        return resolved

    def _read_artifact(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        """Read artifact content and return augmented metadata.

        Validates that the resolved path stays within state_dir.
        """
        artifact_rel = meta.get("artifact_path", "")

        # Reject absolute paths stored in metadata
        if artifact_rel and os.path.isabs(artifact_rel):
            raise ValueError(
                f"Artifact path must be relative: {artifact_rel}"
            )

        full_path = self._ensure_under_state_dir(self.state_dir / artifact_rel)

        try:
            content = full_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise FileNotFoundError(
                f"Artifact not found on disk: {artifact_rel}"
            ) from exc

        result = dict(meta)
        result["content"] = content
        return result
