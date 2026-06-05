#!/usr/bin/env python3
"""File-based role run state store."""

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import uuid


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_run_id() -> str:
    """Generate a run_id in YYYYMMDD-HHMMSS-<short-random> format."""
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    random_part = uuid.uuid4().hex[:6]
    return f"{timestamp}-{random_part}"


def _generate_role_run_id(run_id: str, role: str, attempt: int) -> str:
    """Generate a role_run_id from its components."""
    return f"{run_id}-{role}-{attempt}"


ROLE_ORDER: dict[str, int] = {
    "scout": 1,
    "architect": 2,
    "coder": 3,
    "reviewer": 4,
    "publisher": 5,
}


def _artifact_filename(role: str, attempt: int) -> str:
    """Generate the artifact filename for a role and attempt."""
    order = ROLE_ORDER.get(role, 99)
    prefix = f"{order:02d}"
    if attempt > 1:
        return f"{prefix}-{role}-attempt{attempt}.answer.md"
    return f"{prefix}-{role}.answer.md"


class RoleRunStore:
    """File-based store for role run mappings and artifacts.

    Role run records are stored as individual JSON files under
    *state_dir* named ``<role_run_id>.json``.

    Artifacts are stored under ``<state_dir>/<run_id>/<NN>-<role>.answer.md``.

    Parameters
    ----------
    state_dir :
        Directory for role run state.  Defaults to
        ``OPENHANDS_ROLE_STATE_DIR`` env var or ``.runs``.
    """

    def __init__(self, state_dir: Optional[str] = None) -> None:
        self.state_dir = Path(
            state_dir or os.getenv(
                "OPENHANDS_ROLE_STATE_DIR", ".runs"
            )
        )
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # -- public API --------------------------------------------------------

    def create_role_run(
        self,
        role: str,
        run_id: str,
        role_run_id: str,
        openhands_task_id: str,
        repo: Optional[str] = None,
        base_branch: Optional[str] = None,
        branch: Optional[str] = None,
        artifact_name: Optional[str] = None,
        attempt: int = 1,
    ) -> dict[str, Any]:
        """Create a new role run record and persist it.

        Parameters
        ----------
        attempt :
            Attempt number for this role within the run.  Used to
            produce distinct artifact filenames on retry.

        Returns
        -------
        dict
            The new record dict.
        """
        run_dir = self.state_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        record = {
            "run_id": run_id,
            "role_run_id": role_run_id,
            "role": role,
            "openhands_task_id": openhands_task_id,
            "status": "running",
            "repo": repo,
            "base_branch": base_branch,
            "branch": branch,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "artifact_name": artifact_name,
            "artifact_path": None,
            "result_summary": None,
            "action": None,
            "risk": None,
            "attempt": attempt,
        }

        with self._lock:
            self._save_record(record)

        return record

    def get_role_run(self, role_run_id: str) -> Optional[dict[str, Any]]:
        """Load a role run record by role_run_id, or None if not found."""
        with self._lock:
            return self._load_record(role_run_id)

    def update_role_run(
        self, role_run_id: str, **fields: Any
    ) -> Optional[dict[str, Any]]:
        """Update specific fields of an existing role run record.

        Always updates ``updated_at``.  Returns the updated record,
        or None if the record did not exist.
        """
        with self._lock:
            record = self._load_record(role_run_id)
            if record is None:
                return None
            for key, value in fields.items():
                record[key] = value
            record["updated_at"] = _now_iso()
            self._save_record(record)
            return record

    def save_artifact(
        self, role_run_id: str, content: str
    ) -> Optional[str]:
        """Save artifact content and return the relative artifact path.

        Returns the relative path ``runs/<run_id>/<NN>-<role>.answer.md``
        or None if the role run record does not exist.
        """
        with self._lock:
            record = self._load_record(role_run_id)
            if record is None:
                return None

            run_dir = self.state_dir / record["run_id"]
            run_dir.mkdir(parents=True, exist_ok=True)

            filename = _artifact_filename(
                record["role"], record.get("attempt", 1)
            )
            artifact_path = run_dir / filename
            artifact_path.write_text(content, encoding="utf-8")

            rel_path = f"runs/{record['run_id']}/{filename}"
            record["artifact_path"] = rel_path
            self._save_record(record)

            return rel_path

    def get_artifact(self, role_run_id: str) -> Optional[str]:
        """Read artifact content from disk, or None if not found."""
        with self._lock:
            record = self._load_record(role_run_id)
            if record is None or not record.get("artifact_path"):
                return None

            # artifact_path is relative like "runs/run-id/01-scout.answer.md"
            parts = record["artifact_path"].split("/")
            if len(parts) >= 3:
                run_id = parts[1]
                filename = parts[2]
                filepath = self.state_dir / run_id / filename
            else:
                filepath = self.state_dir / record["artifact_path"]

            if filepath.exists():
                return filepath.read_text(encoding="utf-8")
            return None

    def get_attempt_count(self, run_id: str, role: str) -> int:
        """Count existing role run records for a role within a run."""
        with self._lock:
            count = 0
            for fname in self.state_dir.glob("*.json"):
                try:
                    data = json.loads(fname.read_text(encoding="utf-8"))
                    if (
                        data.get("run_id") == run_id
                        and data.get("role") == role
                    ):
                        count += 1
                except (json.JSONDecodeError, OSError):
                    continue
            return count

    # -- internals ---------------------------------------------------------

    def _record_path(self, role_run_id: str) -> Path:
        return self.state_dir / f"{role_run_id}.json"

    def _load_record(self, role_run_id: str) -> Optional[dict[str, Any]]:
        path = self._record_path(role_run_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return data

    def _save_record(self, record: dict[str, Any]) -> None:
        path = self._record_path(record["role_run_id"])
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), suffix=".tmp", prefix="role_"
        )
        try:
            os.write(
                fd,
                json.dumps(record, indent=2, ensure_ascii=False).encode(
                    "utf-8"
                ),
            )
            os.fsync(fd)
            os.close(fd)
            os.rename(tmp_path, str(path))
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
