#!/usr/bin/env python3
"""JSON file-based task state persistence for the MCP agent.

Each task is stored as a single JSON file under a configurable directory.
Thread-safe via a module-level lock.  File writes use atomic rename
(temp-file-then-rename) to avoid corruption on crash.
"""

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_STATUSES = {
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
    "timeout",
    "unknown",
}

_TASK_FIELDS = (
    "task_id",
    "conversation_id",
    "status",
    "created_at",
    "updated_at",
    "last_polled_at",
    "prompt",
    "idempotency_key",
    "result",
    "error",
    "events_summary",
    "raw_response_path",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_task(task_id: str, conversation_id: str, prompt: str,
                  idempotency_key: Optional[str]) -> dict[str, Any]:
    """Return a new task record with sensible defaults."""
    return {
        "task_id": task_id,
        "conversation_id": conversation_id,
        "status": "queued",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "last_polled_at": None,
        "prompt": prompt,
        "idempotency_key": idempotency_key,
        "result": None,
        "error": None,
        "events_summary": [],
        "raw_response_path": None,
    }


# ---------------------------------------------------------------------------
# TaskStore
# ---------------------------------------------------------------------------


class TaskStore:
    """JSON-file-backed persistent store for OpenHands task records.

    Parameters
    ----------
    state_dir:
        Directory in which task JSON files are stored.  Created if missing.
    """

    def __init__(self, state_dir: str) -> None:
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # -- public API --------------------------------------------------------

    def create_task(self, conversation_id: str, prompt: str,
                    idempotency_key: Optional[str] = None) -> dict[str, Any]:
        """Create a new task record and persist it.

        Returns the new task record dict.
        """
        import uuid
        task_id = uuid.uuid4().hex
        record = _default_task(task_id, conversation_id, prompt,
                               idempotency_key)
        self._save_task(record)
        return record

    def get_task(self, task_id: str) -> Optional[dict[str, Any]]:
        """Load a task record by task_id, or *None* if not found."""
        with self._lock:
            return self._load_task(task_id)

    def update_task(self, task_id: str, **fields: Any) -> Optional[dict[str, Any]]:
        """Update specific fields of an existing task record.

        Always updates ``updated_at``.  Returns the updated record, or
        *None* if the task did not exist.
        """
        with self._lock:
            record = self._load_task(task_id)
            if record is None:
                return None
            for key, value in fields.items():
                if key in _TASK_FIELDS:
                    record[key] = value
            record["updated_at"] = _now_iso()
            self._save_task(record)
            return record

    def find_by_conversation_id(self, conversation_id: str) -> Optional[dict[str, Any]]:
        """Find the most recent task with the given conversation_id."""
        with self._lock:
            best: Optional[dict[str, Any]] = None
            best_time = ""
            for fname in self.state_dir.glob("*.json"):
                try:
                    data = json.loads(fname.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                if data.get("conversation_id") == conversation_id:
                    created = data.get("created_at", "")
                    if best is None or created > best_time:
                        best = data
                        best_time = created
            return best

    def find_by_idempotency_key(self, key: str) -> Optional[dict[str, Any]]:
        """Find a task with the given idempotency_key."""
        with self._lock:
            for fname in self.state_dir.glob("*.json"):
                try:
                    data = json.loads(fname.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                if data.get("idempotency_key") == key:
                    return data
            return None

    def list_tasks(self) -> list[str]:
        """Return all known task_ids."""
        with self._lock:
            ids: list[str] = []
            for fname in self.state_dir.glob("*.json"):
                try:
                    data = json.loads(fname.read_text(encoding="utf-8"))
                    if data.get("task_id"):
                        ids.append(data["task_id"])
                except (json.JSONDecodeError, OSError):
                    continue
            return ids

    # -- internals ---------------------------------------------------------

    def _task_path(self, task_id: str) -> Path:
        return self.state_dir / f"{task_id}.json"

    def _load_task(self, task_id: str) -> Optional[dict[str, Any]]:
        path = self._task_path(task_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return data

    def _save_task(self, record: dict[str, Any]) -> None:
        path = self._task_path(record["task_id"])
        # Atomic write: temp file + rename
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self.state_dir), suffix=".tmp", prefix="task_"
        )
        try:
            os.write(fd, json.dumps(record, indent=2, ensure_ascii=False).encode("utf-8"))
            os.fsync(fd)
            os.close(fd)
            os.rename(tmp_path, str(path))
        except Exception:
            os.close(fd)  # type: ignore[possibly-unbound]
            raise
        finally:
            # Cleanup temp file on error path above; on success the rename
            # already moved it, so this is a no-op if the file vanished.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
