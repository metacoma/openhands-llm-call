#!/usr/bin/env python3
"""Tests for RoleLockManager (mcp_agent.lock_manager)."""

import json
import os
import shutil
import tempfile
import time
import unittest


class TestRoleLockManager(unittest.TestCase):
    """Test RoleLockManager class."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_lock_mgr_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)

    def test_acquire_creates_lock_file(self):
        """Acquiring a lock creates a lock file."""
        from mcp_agent.lock_manager import RoleLockManager

        manager = RoleLockManager()
        metadata = {"role_run_id": "run-1", "role": "coder"}
        result = manager.acquire("repo|branch", metadata)

        self.assertIsNone(result)  # No conflict
        lock_data = manager.get("repo|branch")
        self.assertIsNotNone(lock_data)
        self.assertEqual(lock_data["role_run_id"], "run-1")

    def test_acquire_conflict_for_same_key(self):
        """Starting second mutating role for same repo/branch fails."""
        from mcp_agent.lock_manager import RoleLockManager

        manager = RoleLockManager()

        # First acquire succeeds
        metadata1 = {"role_run_id": "run-1", "role": "coder"}
        result1 = manager.acquire("repo|branch", metadata1)
        self.assertIsNone(result1)

        # Second acquire returns conflict
        metadata2 = {"role_run_id": "run-2", "role": "coder"}
        result2 = manager.acquire("repo|branch", metadata2)
        self.assertIsNotNone(result2)
        self.assertEqual(result2["role_run_id"], "run-1")

    def test_acquire_no_lock_for_readonly_role(self):
        """Read-only role does not acquire lock (tested at role_tools level)."""
        # This is tested in test_role_tools.py via the role_start flow
        pass

    def test_release_clears_lock(self):
        """Final status releases lock."""
        from mcp_agent.lock_manager import RoleLockManager

        manager = RoleLockManager()
        metadata = {"role_run_id": "run-1", "role": "coder"}
        manager.acquire("repo|branch", metadata)

        # Release with matching role_run_id
        released = manager.release("repo|branch", "run-1")
        self.assertTrue(released)

        # Lock should be gone
        lock_data = manager.get("repo|branch")
        self.assertIsNone(lock_data)

    def test_release_with_wrong_role_run_id_fails(self):
        """Release with wrong role_run_id does not clear lock."""
        from mcp_agent.lock_manager import RoleLockManager

        manager = RoleLockManager()
        metadata = {"role_run_id": "run-1", "role": "coder"}
        manager.acquire("repo|branch", metadata)

        # Release with wrong role_run_id
        released = manager.release("repo|branch", "run-999")
        self.assertFalse(released)

        # Lock should still exist
        lock_data = manager.get("repo|branch")
        self.assertIsNotNone(lock_data)

    def test_stale_lock_can_be_overwritten(self):
        """Stale lock can be overwritten."""
        from mcp_agent.lock_manager import RoleLockManager

        tmpdir = tempfile.mkdtemp(prefix="test_lock_stale_")
        try:
            # Create a lock with a very short TTL
            manager = RoleLockManager(lock_dir=os.path.join(tmpdir, "locks"), ttl_minutes=0)
            metadata = {
                "role_run_id": "run-stale",
                "role": "coder",
                "created_at": "2020-01-01T00:00:00+00:00",  # Very old
            }
            manager.acquire("repo-stale|branch", metadata)

            # Lock should be stale (TTL=0 means immediately stale)
            stale = manager.get("repo-stale|branch")
            self.assertIsNotNone(stale)

            # Try to acquire again — should succeed because stale
            new_metadata = {"role_run_id": "run-new", "role": "coder"}
            result = manager.acquire("repo-stale|branch", new_metadata)
            self.assertIsNone(result)  # No conflict (stale)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_stale_lock_overwritten_with_new_metadata(self):
        """After acquiring over a stale lock, get() returns the new metadata."""
        from mcp_agent.lock_manager import RoleLockManager

        tmpdir = tempfile.mkdtemp(prefix="test_lock_stale_meta_")
        try:
            manager = RoleLockManager(lock_dir=os.path.join(tmpdir, "locks"), ttl_minutes=0)

            # Create a stale lock
            stale_metadata = {
                "role_run_id": "run-stale",
                "role": "coder",
                "created_at": "2020-01-01T00:00:00+00:00",
            }
            manager.acquire("repo-stale2|branch", stale_metadata)

            # Acquire over stale lock with new metadata
            new_metadata = {
                "role_run_id": "run-new-123",
                "role": "coder",
            }
            result = manager.acquire("repo-stale2|branch", new_metadata)
            self.assertIsNone(result)  # Success

            # Verify the lock file now contains the new role_run_id
            updated = manager.get("repo-stale2|branch")
            self.assertIsNotNone(updated)
            self.assertEqual(updated["role_run_id"], "run-new-123")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_stale_lock_overwrite_new_role_run_id_in_file(self):
        """Lock file contains new role_run_id after stale overwrite."""
        from mcp_agent.lock_manager import RoleLockManager

        tmpdir = tempfile.mkdtemp(prefix="test_lock_stale_file_")
        try:
            manager = RoleLockManager(lock_dir=os.path.join(tmpdir, "locks"), ttl_minutes=0)

            # Create a stale lock with a known role_run_id
            stale_metadata = {
                "role_run_id": "run-old-456",
                "role": "coder",
                "created_at": "2020-01-01T00:00:00+00:00",
            }
            manager.acquire("repo-stale3|branch", stale_metadata)

            # Acquire over stale lock
            new_metadata = {
                "role_run_id": "run-new-789",
                "role": "coder",
            }
            manager.acquire("repo-stale3|branch", new_metadata)

            # Read the lock file directly to verify it was overwritten
            lock_data = manager.get("repo-stale3|branch")
            self.assertEqual(lock_data["role_run_id"], "run-new-789")

            # Verify the old role_run_id is no longer in the file
            self.assertNotIn("run-old-456", json.dumps(lock_data))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
