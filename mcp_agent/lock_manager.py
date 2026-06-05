#!/usr/bin/env python3
"""File-based lock manager for mutating roles.

Provides simple file-based locking for roles where ``readonly: false``.
Locks are stored under ``<state_dir>/locks/`` keyed by a SHA-256 hash
of the lock key string so filenames are filesystem-safe.
"""

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Optional


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(s: str) -> datetime:
    """Parse an ISO-8601 string to a timezone-aware datetime."""
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class RoleLockManager:
    """File-based lock manager for mutating roles.

    Parameters
    ----------
    lock_dir :
        Directory for lock files.  Defaults to ``<state_dir>/locks/``
        where *state_dir* is ``OPENHANDS_ROLE_STATE_DIR`` or ``.runs``.
    ttl_minutes :
        Stale-lock timeout in minutes.  Defaults to
        ``OPENHANDS_ROLE_LOCK_TTL_MINUTES`` env var or 180.
    """

    def __init__(
        self,
        lock_dir: Optional[str] = None,
        ttl_minutes: Optional[int] = None,
    ) -> None:
        self.ttl_minutes = ttl_minutes or int(
            os.getenv("OPENHANDS_ROLE_LOCK_TTL_MINUTES", "180")
        )
        self.lock_dir = Path(
            lock_dir or os.path.join(
                os.getenv("OPENHANDS_ROLE_STATE_DIR", ".runs"), "locks"
            )
        )
        self.lock_dir.mkdir(parents=True, exist_ok=True)

    # -- public API --------------------------------------------------------

    def acquire(
        self, lock_key: str, metadata: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Acquire a lock for *lock_key*.

        Parameters
        ----------
        lock_key :
            A human-readable key (e.g. ``"repo|branch"``).
        metadata :
            Dict of metadata to persist with the lock (run_id, role,
            repo, branch, role_run_id, created_at, expires_at, …).
            If ``created_at`` is not provided, it is set to now.

        Returns
        -------
        dict or None
            **None** if the lock was acquired successfully.
            An existing lock dict if a conflict is detected.
        """
        # Ensure created_at is set
        if "created_at" not in metadata:
            metadata = dict(metadata)
            metadata["created_at"] = _now_iso()

        existing = self.get(lock_key)
        if existing is not None:
            if self._is_stale(existing):
                # Stale lock — overwrite with new metadata
                self._write_lock(lock_key, metadata)
                return None
            return existing  # conflict

        self._write_lock(lock_key, metadata)
        return None

    def release(self, lock_key: str, role_run_id: str) -> bool:
        """Release a lock identified by *lock_key*.

        Verifies that the stored ``role_run_id`` matches to avoid
        releasing a lock held by a different run.

        Returns
        -------
        bool
            True if the lock was released, False if not found or mismatch.
        """
        lock_data = self.get(lock_key)
        if lock_data is None:
            return False
        if lock_data.get("role_run_id") != role_run_id:
            # Protect against releasing another run's lock
            return False
        lock_path = self._lock_path(lock_key)
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass
        return True

    def get(self, lock_key: str) -> Optional[Dict[str, Any]]:
        """Load lock metadata for *lock_key*, or None if not found."""
        lock_path = self._lock_path(lock_key)
        if not lock_path.exists():
            return None
        try:
            data = json.loads(lock_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return data

    # -- internals ---------------------------------------------------------

    def _is_stale(self, lock_data: Dict[str, Any]) -> bool:
        """Return True if *lock_data* is older than the TTL."""
        created_str = lock_data.get("created_at")
        if not created_str:
            return True
        try:
            created = _parse_iso(created_str)
        except (ValueError, TypeError):
            return True
        expires = created + timedelta(minutes=self.ttl_minutes)
        return datetime.now(timezone.utc) > expires

    def _lock_path(self, lock_key: str) -> Path:
        """Return the filesystem path for a lock key."""
        sha = hashlib.sha256(lock_key.encode("utf-8")).hexdigest()[:32]
        return self.lock_dir / f"{sha}.json"

    def _write_lock(
        self, lock_key: str, metadata: Dict[str, Any]
    ) -> None:
        """Atomically write a lock file for *lock_key*."""
        lock_path = self._lock_path(lock_key)
        entry = {"lock_key": lock_key, **metadata}
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self.lock_dir), suffix=".tmp", prefix="lock_"
        )
        try:
            os.write(
                fd,
                json.dumps(entry, indent=2, ensure_ascii=False).encode(
                    "utf-8"
                ),
            )
            os.fsync(fd)
            os.close(fd)
            os.rename(tmp_path, str(lock_path))
        except Exception:
            try:
                os.close(fd)  # type: ignore[possibly-unbound]
            except OSError:
                pass
            raise
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
