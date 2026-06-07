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


def _generate_artifact_id(
    run_id: str, role: str, attempt: int = 1, artifact_type: str = ""
) -> str:
    """Generate a stable, opaque artifact ID.

    Format: art_<run_id>_<role>_<attempt>_<artifact_type>
    Example: art_20260607-124753-abc123_scout_1_scout_report
    """
    return f"art_{run_id}_{role}_{attempt}_{artifact_type}"


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
        artifact_id: Optional[str] = None,
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
        artifact_id :
            Opaque artifact identifier.  Auto-generated if not provided.

        Returns
        -------
        dict
            Artifact metadata record with ``artifact_id``, ``artifact_type``
            (alias for ``artifact_name``), ``role``, ``role_run_id``,
            ``run_id``, ``created_at``, ``size_bytes``, plus legacy
            ``artifact_name`` and ``artifact_path`` keys for backward compat.
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

        # Generate artifact_id if not provided
        if artifact_id is None:
            # Extract attempt from role_run_id (last segment after last '-')
            parts = role_run_id.rsplit("-", 1)
            attempt = int(parts[-1]) if parts[-1].isdigit() else 1
            artifact_id = _generate_artifact_id(
                safe_run_id, role, attempt, artifact_name
            )

        # Write companion metadata
        # Store artifact_path relative to state_dir for safe resolution
        rel_path = f"{safe_run_id}/{filename}"
        meta = {
            "artifact_id": artifact_id,
            "artifact_name": artifact_name,
            "artifact_type": artifact_name,
            "role": role,
            "role_run_id": role_run_id,
            "run_id": run_id,
            "artifact_path": rel_path,
            "created_at": _now_iso(),
            "size_bytes": len(content.encode("utf-8")),
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

        Raises
        ------
        ValueError
            If *run_id* contains path traversal or invalid characters.
        """
        safe_run_id = _safe_component(run_id, "run_id")
        run_dir = self._ensure_under_state_dir(self.state_dir / safe_run_id)
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
            If *run_id* contains path traversal or invalid characters, or
            if the resolved path would escape the state directory.
        """
        safe_run_id = _safe_component(run_id, "run_id")
        run_dir = self._ensure_under_state_dir(self.state_dir / safe_run_id)
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

    def get_by_path(self, artifact_path: str) -> Dict[str, Any]:
        """Read exactly the artifact file referenced by *artifact_path*.

        Parameters
        ----------
        artifact_path :
            Relative path to an artifact file, e.g.
            ``"run-id/role_run_id_artifact_name.artifact"``.
            Resolved relative to ``state_dir``.

        Returns
        -------
        dict
            Artifact metadata with ``content`` key, consistent with
            ``get()`` return shape.

        Raises
        ------
        ValueError
            If path traversal is detected, metadata is missing/invalid,
            or metadata path does not match the requested path.
        FileNotFoundError
            If the artifact file does not exist on disk.
        """
        if not isinstance(artifact_path, str) or not artifact_path:
            raise ValueError("invalid artifact path")

        # --- Path traversal protection ---
        root = self.state_dir.resolve()
        candidate = (root / artifact_path).resolve()
        try:
            candidate.is_relative_to(root)
        except AttributeError:
            # Fallback for Python < 3.9
            try:
                candidate.relative_to(root)
            except ValueError:
                raise ValueError("invalid artifact path") from None
        if not candidate.is_relative_to(root):
            raise ValueError("invalid artifact path")

        # --- Require .artifact suffix ---
        if not candidate.name.endswith(".artifact"):
            raise ValueError("invalid artifact path")

        # --- Check artifact file exists ---
        if not candidate.exists():
            raise FileNotFoundError(f"artifact not found: {artifact_path}")

        # --- Read companion metadata ---
        meta_path = Path(str(candidate) + ".meta.json")
        if not meta_path.exists():
            raise ValueError(f"artifact metadata not found: {artifact_path}")

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError(f"invalid artifact metadata: {artifact_path}") from exc

        # --- Validate metadata matches path ---
        stored_path = meta.get("artifact_path", "")
        if stored_path != artifact_path:
            # Also try normalized comparison
            try:
                stored_resolved = (root / stored_path).resolve()
                if stored_resolved != candidate:
                    raise ValueError("artifact metadata path mismatch")
            except (ValueError, OSError):
                raise ValueError("artifact metadata path mismatch")

        # --- Read content and return augmented metadata ---
        meta_with_content = dict(meta)
        meta_with_content["content"] = candidate.read_text(encoding="utf-8")
        meta_with_content["content_empty"] = not meta_with_content["content"].strip()
        meta_with_content["valid_role_report"] = bool(meta_with_content["content"].strip())
        return meta_with_content

    def get_content_by_id(self, artifact_id: str) -> str:
        """Resolve an *artifact_id* to its content string.

        This is an **internal server-only** method used during Jinja
        prompt rendering.  It is NOT exposed as an MCP tool so that
        Head of IT never receives artifact content.

        Parameters
        ----------
        artifact_id :
            Opaque artifact identifier (e.g. ``"art_run-scout-1_scout_report"``).

        Returns
        -------
        str
            The artifact content.

        Raises
        ------
        ValueError
            If the artifact cannot be found or resolved.
        """
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ValueError("invalid artifact_id")

        # Strip the "art_" prefix to get the run_id portion
        if artifact_id.startswith("art_"):
            remainder = artifact_id[4:]
        else:
            remainder = artifact_id

        # Parse: <run_id>_<role>_<attempt>_<artifact_type>
        # run_id may contain dashes and colons, so we need to be careful.
        # Strategy: scan all artifacts in state_dir for a matching artifact_id.
        for meta_path in self.state_dir.rglob("*.meta.json"):
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if data.get("artifact_id") == artifact_id:
                # Validate the artifact file still exists
                artifact_rel = data.get("artifact_path", "")
                full_path = self.state_dir / artifact_rel
                try:
                    self._ensure_under_state_dir(full_path)
                except ValueError:
                    continue
                if full_path.exists():
                    return full_path.read_text(encoding="utf-8")

        raise ValueError(f"artifact not found: {artifact_id}")

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
        result["content_empty"] = not content.strip()
        result["valid_role_report"] = bool(content.strip())
        return result
