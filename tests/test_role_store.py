#!/usr/bin/env python3
"""Tests for RoleRunStore (mcp_agent.role_store)."""

import json
import os
import shutil
import tempfile
import unittest


class TestRoleRunStore(unittest.TestCase):
    """Test RoleRunStore class."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_role_store_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)

    def test_create_role_run_stores_attempt(self):
        """RoleRunStore.create_role_run stores the passed attempt value."""
        from mcp_agent.role_store import RoleRunStore

        store = RoleRunStore()
        record = store.create_role_run(
            role="coder",
            run_id="20260605-abc123",
            role_run_id="20260605-abc123-coder-2",
            openhands_task_id="task-001",
            attempt=2,
        )

        self.assertEqual(record["attempt"], 2)

    def test_create_role_run_default_attempt_is_one(self):
        """RoleRunStore.create_role_run defaults attempt to 1."""
        from mcp_agent.role_store import RoleRunStore

        store = RoleRunStore()
        record = store.create_role_run(
            role="scout",
            run_id="20260605-def456",
            role_run_id="20260605-def456-scout-1",
            openhands_task_id="task-002",
        )

        self.assertEqual(record["attempt"], 1)

    def test_repeated_role_attempts_produce_distinct_artifact_filenames(self):
        """Repeated role attempts produce distinct artifact filenames."""
        from mcp_agent.role_store import RoleRunStore

        store = RoleRunStore()

        # First attempt
        record1 = store.create_role_run(
            role="coder",
            run_id="20260605-ghi789",
            role_run_id="20260605-ghi789-coder-1",
            openhands_task_id="task-010",
            attempt=1,
        )

        # Second attempt
        record2 = store.create_role_run(
            role="coder",
            run_id="20260605-ghi789",
            role_run_id="20260605-ghi789-coder-2",
            openhands_task_id="task-011",
            attempt=2,
        )

        # Save artifacts
        artifact_path1 = store.save_artifact(
            record1["role_run_id"], "first attempt result"
        )
        artifact_path2 = store.save_artifact(
            record2["role_run_id"], "second attempt result"
        )

        # Paths must differ
        self.assertIsNotNone(artifact_path1)
        self.assertIsNotNone(artifact_path2)
        self.assertNotEqual(artifact_path1, artifact_path2)

        # First attempt: 03-coder.answer.md
        self.assertIn("03-coder.answer.md", artifact_path1)
        # Second attempt: 03-coder-attempt2.answer.md
        self.assertIn("03-coder-attempt2.answer.md", artifact_path2)

    def test_save_and_get_artifact(self):
        """Artifact can be saved and retrieved."""
        from mcp_agent.role_store import RoleRunStore

        store = RoleRunStore()
        record = store.create_role_run(
            role="scout",
            run_id="20260605-jkl012",
            role_run_id="20260605-jkl012-scout-1",
            openhands_task_id="task-020",
            attempt=1,
        )

        content = "# Scout Report\n\nAnalysis complete."
        artifact_path = store.save_artifact(record["role_run_id"], content)

        self.assertIsNotNone(artifact_path)

        retrieved = store.get_artifact(record["role_run_id"])
        self.assertEqual(retrieved, content)

    def test_get_role_run_returns_record(self):
        """get_role_run returns the stored record."""
        from mcp_agent.role_store import RoleRunStore

        store = RoleRunStore()
        record = store.create_role_run(
            role="scout",
            run_id="20260605-mno345",
            role_run_id="20260605-mno345-scout-1",
            openhands_task_id="task-030",
            attempt=1,
        )

        loaded = store.get_role_run(record["role_run_id"])
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["role"], "scout")
        self.assertEqual(loaded["run_id"], "20260605-mno345")

    def test_get_role_run_missing_returns_none(self):
        """get_role_run returns None for missing role_run_id."""
        from mcp_agent.role_store import RoleRunStore

        store = RoleRunStore()
        result = store.get_role_run("nonexistent-id")
        self.assertIsNone(result)

    def test_update_role_run_updates_fields(self):
        """update_role_run updates specific fields."""
        from mcp_agent.role_store import RoleRunStore

        store = RoleRunStore()
        record = store.create_role_run(
            role="scout",
            run_id="20260605-pqr678",
            role_run_id="20260605-pqr678-scout-1",
            openhands_task_id="task-040",
            attempt=1,
        )

        updated = store.update_role_run(
            record["role_run_id"],
            status="completed",
            action="PASS",
            risk="LOW",
        )

        self.assertIsNotNone(updated)
        self.assertEqual(updated["status"], "completed")
        self.assertEqual(updated["action"], "PASS")
        self.assertEqual(updated["risk"], "LOW")

    def test_update_role_run_missing_returns_none(self):
        """update_role_run returns None for missing role_run_id."""
        from mcp_agent.role_store import RoleRunStore

        store = RoleRunStore()
        result = store.update_role_run(
            "nonexistent-id", status="completed"
        )
        self.assertIsNone(result)

    def test_get_artifact_missing_returns_none(self):
        """get_artifact returns None for missing role_run_id."""
        from mcp_agent.role_store import RoleRunStore

        store = RoleRunStore()
        result = store.get_artifact("nonexistent-id")
        self.assertIsNone(result)

    def test_save_artifact_missing_returns_none(self):
        """save_artifact returns None for missing role_run_id."""
        from mcp_agent.role_store import RoleRunStore

        store = RoleRunStore()
        result = store.save_artifact("nonexistent-id", "content")
        self.assertIsNone(result)

    def test_get_attempt_count(self):
        """get_attempt_count returns correct count for role within run."""
        from mcp_agent.role_store import RoleRunStore

        store = RoleRunStore()
        run_id = "20260605-stu901"

        # Create two coder attempts
        store.create_role_run(
            role="coder",
            run_id=run_id,
            role_run_id=f"{run_id}-coder-1",
            openhands_task_id="task-050",
            attempt=1,
        )
        store.create_role_run(
            role="coder",
            run_id=run_id,
            role_run_id=f"{run_id}-coder-2",
            openhands_task_id="task-051",
            attempt=2,
        )
        # Create one scout attempt (different role)
        store.create_role_run(
            role="scout",
            run_id=run_id,
            role_run_id=f"{run_id}-scout-1",
            openhands_task_id="task-052",
            attempt=1,
        )

        coder_count = store.get_attempt_count(run_id, "coder")
        scout_count = store.get_attempt_count(run_id, "scout")

        self.assertEqual(coder_count, 2)
        self.assertEqual(scout_count, 1)

    def test_save_and_find_idempotency_record(self):
        """Idempotency index: save and find by scope."""
        from mcp_agent.role_store import RoleRunStore

        store = RoleRunStore()
        scope = "20260605-abc123:scout:initial-scout"
        role_run_id = "20260605-abc123-scout-1"

        store.save_idempotency_record(scope, role_run_id)

        found = store.find_by_idempotency_scope(scope)
        self.assertEqual(found, role_run_id)

    def test_find_by_idempotency_scope_missing(self):
        """Idempotency index: missing scope returns None."""
        from mcp_agent.role_store import RoleRunStore

        store = RoleRunStore()
        found = store.find_by_idempotency_scope("nonexistent:scope")
        self.assertIsNone(found)

    def test_clear_idempotency_scope(self):
        """Idempotency index: clear removes scope."""
        from mcp_agent.role_store import RoleRunStore

        store = RoleRunStore()
        scope = "20260605-xyz999:architect:plan-key"
        role_run_id = "20260605-xyz999-architect-1"

        store.save_idempotency_record(scope, role_run_id)
        store.clear_idempotency_scope(scope)

        found = store.find_by_idempotency_scope(scope)
        self.assertIsNone(found)


if __name__ == "__main__":
    unittest.main()
