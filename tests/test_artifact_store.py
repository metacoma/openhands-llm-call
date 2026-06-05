#!/usr/bin/env python3
"""Tests for ArtifactStore (mcp_agent.artifact_store)."""

import json
import os
import shutil
import tempfile
import unittest


class TestArtifactStore(unittest.TestCase):
    """Test ArtifactStore class."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_artifact_store_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)

    def test_save_creates_file_and_metadata(self):
        """Save artifact creates file and metadata."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        meta = store.save(
            run_id="run-001",
            role_run_id="run-001-scout-1",
            role="scout",
            artifact_name="scout_report",
            content="# Scout Report\n\nAnalysis complete.",
        )

        self.assertEqual(meta["artifact_name"], "scout_report")
        self.assertEqual(meta["role"], "scout")
        self.assertEqual(meta["role_run_id"], "run-001-scout-1")
        self.assertEqual(meta["run_id"], "run-001")
        self.assertIn("created_at", meta)
        self.assertIn("artifact_path", meta)

    def test_list_returns_artifacts_for_run_id(self):
        """artifact_list lists artifacts for run_id."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()

        # Save two artifacts
        store.save(
            run_id="run-002",
            role_run_id="run-002-scout-1",
            role="scout",
            artifact_name="scout_report",
            content="scout content",
        )
        store.save(
            run_id="run-002",
            role_run_id="run-002-architect-1",
            role="architect",
            artifact_name="architect_plan",
            content="architect content",
        )

        artifacts = store.list("run-002")
        self.assertEqual(len(artifacts), 2)
        names = {a["artifact_name"] for a in artifacts}
        self.assertIn("scout_report", names)
        self.assertIn("architect_plan", names)

    def test_get_returns_artifact_content(self):
        """artifact_get returns artifact content."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()

        store.save(
            run_id="run-003",
            role_run_id="run-003-coder-1",
            role="coder",
            artifact_name="coder_report",
            content="## Coder Report\n\nImplementation complete.",
        )

        result = store.get("run-003", artifact_name="coder_report")
        self.assertIsNotNone(result)
        self.assertEqual(result["content"], "## Coder Report\n\nImplementation complete.")
        self.assertEqual(result["artifact_name"], "coder_report")

    def test_get_by_role_run_id(self):
        """artifact_get works with role_run_id."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()

        store.save(
            run_id="run-004",
            role_run_id="run-004-reviewer-1",
            role="reviewer",
            artifact_name="reviewer_report",
            content="ACTION: PASS\nRISK: LOW",
        )

        result = store.get("run-004", role_run_id="run-004-reviewer-1")
        self.assertIsNotNone(result)
        self.assertEqual(result["content"], "ACTION: PASS\nRISK: LOW")

    def test_get_unknown_artifact_returns_none(self):
        """artifact_get rejects unknown artifact."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()

        result = store.get("run-005", artifact_name="nonexistent")
        self.assertIsNone(result)

    def test_path_traversal_rejected(self):
        """artifact_get rejects path traversal."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()

        # Try to craft a malicious artifact path via save
        store.save(
            run_id="run-006",
            role_run_id="run-006-test-1",
            role="scout",
            artifact_name="test",
            content="test content",
        )

        # Now try to read a path that escapes state_dir
        # This would require crafting a malicious .meta.json file
        # The ArtifactStore.get() validates resolved paths
        import json as _json
        run_dir = os.path.join(self.tmpdir, "run-006")
        meta_files = [f for f in os.listdir(run_dir) if f.endswith(".meta.json")]
        if meta_files:
            meta_path = os.path.join(run_dir, meta_files[0])
            with open(meta_path, "r") as f:
                meta = _json.load(f)
            # Overwrite with malicious path
            meta["artifact_path"] = "../../../etc/passwd"
            with open(meta_path, "w") as f:
                _json.dump(meta, f)

            # Reading should raise ValueError
            with self.assertRaises(ValueError):
                store.get("run-006", artifact_name="test")


class TestArtifactToolImpl(unittest.TestCase):
    """Test artifact_list_impl and artifact_get_impl."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_artifact_tools_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)

    def test_artifact_list_impl(self):
        """artifact_list returns artifacts for run_id."""
        from mcp_agent.artifact_store import ArtifactStore
        from mcp_agent.role_tools import artifact_list_impl

        store = ArtifactStore()
        store.save(
            run_id="run-010",
            role_run_id="run-010-scout-1",
            role="scout",
            artifact_name="scout_report",
            content="scout",
        )

        result = artifact_list_impl(run_id="run-010")
        self.assertEqual(result["run_id"], "run-010")
        self.assertEqual(len(result["artifacts"]), 1)

    def test_artifact_get_impl_by_name(self):
        """artifact_get returns artifact content by name."""
        from mcp_agent.artifact_store import ArtifactStore
        from mcp_agent.role_tools import artifact_get_impl

        store = ArtifactStore()
        store.save(
            run_id="run-011",
            role_run_id="run-011-scout-1",
            role="scout",
            artifact_name="scout_report",
            content="scout report content",
        )

        result = artifact_get_impl(run_id="run-011", artifact_name="scout_report")
        self.assertEqual(result["content"], "scout report content")

    def test_artifact_get_impl_missing_run_id(self):
        """artifact_get returns error when both run_id and role_run_id are missing."""
        from mcp_agent.role_tools import artifact_get_impl

        result = artifact_get_impl()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "MissingRunId")
        self.assertIn("Provide either run_id or role_run_id", result["error"]["message"])

    def test_artifact_get_impl_by_role_run_id_only(self):
        """artifact_get works with role_run_id only (no run_id)."""
        from mcp_agent.artifact_store import ArtifactStore
        from mcp_agent.role_tools import artifact_get_impl
        from mcp_agent.role_store import RoleRunStore

        store = ArtifactStore()
        role_store = RoleRunStore()

        # Create a role run record
        role_store.create_role_run(
            role="scout",
            run_id="run-020",
            role_run_id="run-020-scout-1",
            openhands_task_id="task-020",
            repo="test/repo",
            branch="main",
            artifact_name="scout_report",
        )

        # Save an artifact
        store.save(
            run_id="run-020",
            role_run_id="run-020-scout-1",
            role="scout",
            artifact_name="scout_report",
            content="Scout analysis via role_run_id lookup.",
        )

        # Retrieve by role_run_id only
        result = artifact_get_impl(role_run_id="run-020-scout-1")
        self.assertNotEqual(result.get("status"), "failed")
        self.assertEqual(result["content"], "Scout analysis via role_run_id lookup.")
        self.assertEqual(result["artifact_name"], "scout_report")

    def test_artifact_get_impl_unknown_role_run_id(self):
        """artifact_get returns UnknownRoleRunId for unknown role_run_id."""
        from mcp_agent.role_tools import artifact_get_impl

        result = artifact_get_impl(role_run_id="nonexistent-role-run-999")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "UnknownRoleRunId")

    def test_artifact_get_impl_missing_artifact_name(self):
        """artifact_get returns MissingArtifactName when neither artifact_name nor role_run_id provided."""
        from mcp_agent.artifact_store import ArtifactStore
        from mcp_agent.role_tools import artifact_get_impl

        store = ArtifactStore()
        store.save(
            run_id="run-021",
            role_run_id="run-021-scout-1",
            role="scout",
            artifact_name="scout_report",
            content="content",
        )

        # Provide run_id but no artifact_name and no role_run_id
        result = artifact_get_impl(run_id="run-021")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "MissingArtifactName")

    def test_artifact_get_impl_unknown_artifact(self):
        """artifact_get returns error for unknown artifact."""
        from mcp_agent.role_tools import artifact_get_impl

        result = artifact_get_impl(run_id="run-999", artifact_name="nonexistent")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "ArtifactNotFound")


if __name__ == "__main__":
    unittest.main()
