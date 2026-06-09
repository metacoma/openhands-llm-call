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
    """Test artifact_list_impl (artifact_get_impl removed as legacy)."""

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


class TestArtifactStorePathValidation(unittest.TestCase):
    """Security tests for ArtifactStore path validation."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_artifact_sec_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)

    # -- valid save/read regression --

    def test_valid_save_and_read_still_works(self):
        """Normal save/read round-trip works."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        meta = store.save(
            run_id="run-valid-001",
            role_run_id="run-valid-001-scout-1",
            role="scout",
            artifact_name="scout_report",
            content="valid content",
        )
        result = store.get("run-valid-001", artifact_name="scout_report")
        self.assertIsNotNone(result)
        self.assertEqual(result["content"], "valid content")

    # -- traversal in artifact_name --

    def test_save_rejects_traversal_in_artifact_name_dotdot(self):
        """artifact_name '..' is rejected."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        with self.assertRaises(ValueError):
            store.save(
                run_id="run-100",
                role_run_id="run-100-scout-1",
                role="scout",
                artifact_name="..",
                content="evil",
            )

    def test_save_rejects_traversal_in_artifact_name_prefix(self):
        """artifact_name '../evil' is rejected."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        with self.assertRaises(ValueError):
            store.save(
                run_id="run-101",
                role_run_id="run-101-scout-1",
                role="scout",
                artifact_name="../evil",
                content="evil",
            )

    def test_save_rejects_traversal_in_artifact_name_slash(self):
        """artifact_name 'foo/evil' is rejected."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        with self.assertRaises(ValueError):
            store.save(
                run_id="run-102",
                role_run_id="run-102-scout-1",
                role="scout",
                artifact_name="foo/evil",
                content="evil",
            )

    def test_save_rejects_traversal_in_artifact_name_backslash(self):
        """artifact_name 'foo\\evil' is rejected."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        with self.assertRaises(ValueError):
            store.save(
                run_id="run-103",
                role_run_id="run-103-scout-1",
                role="scout",
                artifact_name="foo\\evil",
                content="evil",
            )

    # -- traversal in run_id --

    def test_save_rejects_traversal_in_run_id_dotdot(self):
        """run_id '..' is rejected."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        with self.assertRaises(ValueError):
            store.save(
                run_id="..",
                role_run_id="run-200-scout-1",
                role="scout",
                artifact_name="report",
                content="evil",
            )

    def test_save_rejects_traversal_in_run_id_prefix(self):
        """run_id '../run' is rejected."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        with self.assertRaises(ValueError):
            store.save(
                run_id="../run",
                role_run_id="run-201-scout-1",
                role="scout",
                artifact_name="report",
                content="evil",
            )

    def test_save_rejects_traversal_in_run_id_slash(self):
        """run_id 'foo/bar' is rejected."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        with self.assertRaises(ValueError):
            store.save(
                run_id="foo/bar",
                role_run_id="run-202-scout-1",
                role="scout",
                artifact_name="report",
                content="evil",
            )

    def test_save_rejects_traversal_in_run_id_backslash(self):
        """run_id 'foo\\bar' is rejected."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        with self.assertRaises(ValueError):
            store.save(
                run_id="foo\\bar",
                role_run_id="run-203-scout-1",
                role="scout",
                artifact_name="report",
                content="evil",
            )

    # -- traversal in role_run_id --

    def test_save_rejects_traversal_in_role_run_id_dotdot(self):
        """role_run_id '..' is rejected."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        with self.assertRaises(ValueError):
            store.save(
                run_id="run-300",
                role_run_id="..",
                role="scout",
                artifact_name="report",
                content="evil",
            )

    def test_save_rejects_traversal_in_role_run_id_prefix(self):
        """role_run_id '../role' is rejected."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        with self.assertRaises(ValueError):
            store.save(
                run_id="run-301",
                role_run_id="../role",
                role="scout",
                artifact_name="report",
                content="evil",
            )

    def test_save_rejects_traversal_in_role_run_id_slash(self):
        """role_run_id 'foo/bar' is rejected."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        with self.assertRaises(ValueError):
            store.save(
                run_id="run-302",
                role_run_id="foo/bar",
                role="scout",
                artifact_name="report",
                content="evil",
            )

    def test_save_rejects_traversal_in_role_run_id_backslash(self):
        """role_run_id 'foo\\bar' is rejected."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        with self.assertRaises(ValueError):
            store.save(
                run_id="run-303",
                role_run_id="foo\\bar",
                role="scout",
                artifact_name="report",
                content="evil",
            )

    # -- empty / control characters --

    def test_save_rejects_empty_run_id(self):
        """Empty run_id is rejected."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        with self.assertRaises(ValueError):
            store.save(
                run_id="",
                role_run_id="run-400-scout-1",
                role="scout",
                artifact_name="report",
                content="evil",
            )

    def test_save_rejects_control_characters(self):
        """Control characters in artifact_name are rejected."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        with self.assertRaises(ValueError):
            store.save(
                run_id="run-401",
                role_run_id="run-401-scout-1",
                role="scout",
                artifact_name="report\x00evil",
                content="evil",
            )

    # -- read-time path escape --

    def test_read_rejects_state_dir_escape(self):
        """Mutated metadata with '../' path is rejected on read."""
        import json as _json

        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()

        # Save a normal artifact first
        store.save(
            run_id="run-500",
            role_run_id="run-500-scout-1",
            role="scout",
            artifact_name="scout_report",
            content="normal",
        )

        # Mutate the metadata to point outside state_dir
        run_dir = os.path.join(self.tmpdir, "run-500")
        meta_files = [f for f in os.listdir(run_dir) if f.endswith(".meta.json")]
        self.assertTrue(len(meta_files) > 0)
        meta_path = os.path.join(run_dir, meta_files[0])
        with open(meta_path, "r") as f:
            meta = _json.load(f)
        meta["artifact_path"] = "../outside.artifact"
        with open(meta_path, "w") as f:
            _json.dump(meta, f)

        with self.assertRaises(ValueError):
            store.get("run-500", artifact_name="scout_report")

    # -- sibling-prefix escape (the core bug) --

    def test_sibling_prefix_escape_rejected(self):
        """A path under a sibling directory (e.g. state_evil) is rejected.

        This test proves that ``startswith()`` is no longer the security
        boundary — ``Path.relative_to()`` correctly distinguishes
        ``/tmp/state_evil`` from ``/tmp/state``.
        """
        import json as _json

        from mcp_agent.artifact_store import ArtifactStore

        # Create a state_dir and a sibling directory
        base = tempfile.mkdtemp(prefix="test_sibling_")
        state_dir = os.path.join(base, "state")
        evil_dir = os.path.join(base, "state_evil")
        os.makedirs(evil_dir, exist_ok=True)

        old_env = os.environ.get("OPENHANDS_ROLE_STATE_DIR")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = state_dir

        try:
            store = ArtifactStore()

            # Create a fake artifact under the *evil* sibling
            os.makedirs(evil_dir, exist_ok=True)
            evil_file = os.path.join(evil_dir, "evil.artifact")
            with open(evil_file, "w") as f:
                f.write("leaked")

            # Craft a metadata file that points to the evil sibling
            run_dir = os.path.join(state_dir, "run-600")
            os.makedirs(run_dir, exist_ok=True)
            meta = {
                "artifact_name": "evil",
                "role": "scout",
                "role_run_id": "run-600-scout-1",
                "run_id": "run-600",
                "artifact_path": "../state_evil/evil.artifact",
                "created_at": "2025-01-01T00:00:00+00:00",
            }
            meta_file = os.path.join(run_dir, "run-600-scout-1_evil.artifact.meta.json")
            with open(meta_file, "w") as f:
                _json.dump(meta, f)

            # Reading should raise ValueError
            with self.assertRaises(ValueError):
                store.get("run-600", artifact_name="evil")
        finally:
            if old_env:
                os.environ["OPENHANDS_ROLE_STATE_DIR"] = old_env
            else:
                os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
            shutil.rmtree(base, ignore_errors=True)


class TestArtifactStoreListGetPathValidation(unittest.TestCase):
    """Regression tests for ArtifactStore.list() and ArtifactStore.get() run_id validation."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_list_get_validation_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)

    # -- list() rejects traversal in run_id --

    def test_list_rejects_traversal_in_run_id_dotdot(self):
        """list() rejects run_id '..'."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        with self.assertRaises(ValueError):
            store.list("..")

    def test_list_rejects_traversal_in_run_id_prefix(self):
        """list() rejects run_id '../evil'."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        with self.assertRaises(ValueError):
            store.list("../evil")

    def test_list_rejects_traversal_in_run_id_slash(self):
        """list() rejects run_id 'foo/bar'."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        with self.assertRaises(ValueError):
            store.list("foo/bar")

    def test_list_rejects_traversal_in_run_id_backslash(self):
        """list() rejects run_id 'foo\\bar'."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        with self.assertRaises(ValueError):
            store.list("foo\\bar")

    # -- get() rejects traversal in run_id --

    def test_get_rejects_traversal_in_run_id_dotdot(self):
        """get() rejects run_id '..'."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        with self.assertRaises(ValueError):
            store.get("..", artifact_name="report")

    def test_get_rejects_traversal_in_run_id_prefix(self):
        """get() rejects run_id '../evil'."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        with self.assertRaises(ValueError):
            store.get("../evil", artifact_name="report")

    def test_get_rejects_traversal_in_run_id_slash(self):
        """get() rejects run_id 'foo/bar'."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        with self.assertRaises(ValueError):
            store.get("foo/bar", artifact_name="report")

    def test_get_rejects_traversal_in_run_id_backslash(self):
        """get() rejects run_id 'foo\\bar'."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        with self.assertRaises(ValueError):
            store.get("foo\\bar", artifact_name="report")

    # -- valid list() still works --

    def test_list_valid_run_id_returns_artifacts(self):
        """list() returns saved artifacts for a valid run_id."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()

        store.save(
            run_id="run-valid-001",
            role_run_id="run-valid-001-scout-1",
            role="scout",
            artifact_name="scout_report",
            content="scout content",
        )
        store.save(
            run_id="run-valid-001",
            role_run_id="run-valid-001-architect-1",
            role="architect",
            artifact_name="architect_plan",
            content="architect content",
        )

        artifacts = store.list("run-valid-001")
        self.assertEqual(len(artifacts), 2)
        names = {a["artifact_name"] for a in artifacts}
        self.assertIn("scout_report", names)
        self.assertIn("architect_plan", names)

    def test_list_nonexistent_run_id_returns_empty(self):
        """list() returns [] for a non-existent run_id."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        artifacts = store.list("run-nonexistent")
        self.assertEqual(artifacts, [])

    # -- valid get() still works --

    def test_get_valid_run_id_returns_artifact(self):
        """get() returns artifact content for a valid run_id."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()

        store.save(
            run_id="run-valid-002",
            role_run_id="run-valid-002-coder-1",
            role="coder",
            artifact_name="coder_report",
            content="## Coder Report\n\nImplementation complete.",
        )

        result = store.get("run-valid-002", artifact_name="coder_report")
        self.assertIsNotNone(result)
        self.assertEqual(result["content"], "## Coder Report\n\nImplementation complete.")
        self.assertEqual(result["artifact_name"], "coder_report")

    def test_get_by_role_run_id_still_works(self):
        """get() works with role_run_id for a valid run_id."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()

        store.save(
            run_id="run-valid-003",
            role_run_id="run-valid-003-reviewer-1",
            role="reviewer",
            artifact_name="reviewer_report",
            content="ACTION: PASS\nRISK: LOW",
        )

        result = store.get("run-valid-003", role_run_id="run-valid-003-reviewer-1")
        self.assertIsNotNone(result)
        self.assertEqual(result["content"], "ACTION: PASS\nRISK: LOW")


class TestEmptyArtifactDiagnostics(unittest.TestCase):
    """Tests for empty-content diagnostics in ArtifactStore."""

    def test_get_empty_content_returns_content_empty_true(self):
        """Save empty artifact; verify content_empty=True, valid_role_report=False."""
        from mcp_agent.artifact_store import ArtifactStore
        store = ArtifactStore(state_dir=tempfile.mkdtemp())
        meta = store.save(
            run_id="run-empty-001",
            role_run_id="run-empty-001-scout-1",
            role="scout",
            artifact_name="scout_report",
            content="",
        )
        result = store.get("run-empty-001", artifact_name="scout_report")
        self.assertIsNotNone(result)
        self.assertEqual(result["content"], "")
        self.assertTrue(result.get("content_empty"))
        self.assertFalse(result.get("valid_role_report"))

    def test_get_whitespace_only_content_returns_content_empty_true(self):
        """Save whitespace-only artifact; verify content_empty=True."""
        from mcp_agent.artifact_store import ArtifactStore
        store = ArtifactStore(state_dir=tempfile.mkdtemp())
        meta = store.save(
            run_id="run-empty-002",
            role_run_id="run-empty-002-scout-1",
            role="scout",
            artifact_name="scout_report",
            content="   \n\t  \n",
        )
        result = store.get("run-empty-002", artifact_name="scout_report")
        self.assertTrue(result.get("content_empty"))
        self.assertFalse(result.get("valid_role_report"))

    def test_get_nonempty_content_returns_valid(self):
        """Save non-empty artifact; verify content_empty=False, valid_role_report=True."""
        from mcp_agent.artifact_store import ArtifactStore
        store = ArtifactStore(state_dir=tempfile.mkdtemp())
        meta = store.save(
            run_id="run-valid-004",
            role_run_id="run-valid-004-scout-1",
            role="scout",
            artifact_name="scout_report",
            content="# Scout report\n\n## Repository\nexample/repo\n",
        )
        result = store.get("run-valid-004", artifact_name="scout_report")
        self.assertIsNotNone(result)
        self.assertFalse(result.get("content_empty"))
        self.assertTrue(result.get("valid_role_report"))


class TestArtifactStoreGetByPath(unittest.TestCase):
    """Tests for ArtifactStore.get_by_path() exact path resolution."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_get_by_path_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)

    def test_get_by_path_reads_exact_artifact(self):
        """Test 1: get_by_path reads the exact artifact saved."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        meta = store.save(
            run_id="run-1",
            role_run_id="run-1-scout-1",
            role="scout",
            artifact_name="scout_report",
            content="artifact one",
        )
        result = store.get_by_path(meta["artifact_path"])
        self.assertEqual(result["content"], "artifact one")
        self.assertEqual(result["artifact_name"], "scout_report")
        self.assertEqual(result["role_run_id"], "run-1-scout-1")

    def test_get_by_path_same_name_different_role_run_id(self):
        """Test 2: same artifact_name, different role_run_id — reads exact path."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        meta1 = store.save(
            run_id="run-1",
            role_run_id="run-1-scout-1",
            role="scout",
            artifact_name="scout_report",
            content="first",
        )
        meta2 = store.save(
            run_id="run-1",
            role_run_id="run-1-scout-2",
            role="scout",
            artifact_name="scout_report",
            content="second",
        )
        # Call get_by_path with the SECOND artifact's path
        result = store.get_by_path(meta2["artifact_path"])
        self.assertEqual(result["content"], "second")
        self.assertEqual(result["role_run_id"], "run-1-scout-2")
        # Verify it does NOT return the first artifact
        self.assertNotEqual(result["content"], "first")

    def test_get_by_path_missing_artifact_file(self):
        """Test 5: missing artifact path fails clearly."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        with self.assertRaises(FileNotFoundError) as ctx:
            store.get_by_path("run-1/missing_scout_report.artifact")
        self.assertIn("artifact not found", str(ctx.exception).lower())

    def test_get_by_path_missing_metadata(self):
        """Test 6: .artifact without .meta.json fails clearly."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        # Create artifact file manually without metadata
        run_dir = os.path.join(self.tmpdir, "run-1")
        os.makedirs(run_dir, exist_ok=True)
        artifact_file = os.path.join(run_dir, "run-1-scout-1_scout_report.artifact")
        with open(artifact_file, "w") as f:
            f.write("orphan content")
        with self.assertRaises(ValueError) as ctx:
            store.get_by_path("run-1/run-1-scout-1_scout_report.artifact")
        self.assertIn("metadata not found", str(ctx.exception).lower())

    def test_get_by_path_path_traversal_rejected(self):
        """Test 7: path traversal rejected."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        with self.assertRaises(ValueError) as ctx:
            store.get_by_path("../outside.artifact")
        self.assertIn("invalid artifact path", str(ctx.exception).lower())

    def test_get_by_path_metadata_path_mismatch(self):
        """Test: metadata artifact_path does not match requested path."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        meta = store.save(
            run_id="run-1",
            role_run_id="run-1-scout-1",
            role="scout",
            artifact_name="scout_report",
            content="test content",
        )
        # Tamper with metadata to have different artifact_path
        # artifact_path is like "run-1/run-1-scout-1_scout_report.artifact"
        meta_rel = meta["artifact_path"] + ".meta.json"
        meta_file = os.path.join(self.tmpdir, meta_rel)
        with open(meta_file, "r") as f:
            meta_data = json.load(f)
        meta_data["artifact_path"] = "run-1/tampered_scout_report.artifact"
        with open(meta_file, "w") as f:
            json.dump(meta_data, f)
        with self.assertRaises(ValueError) as ctx:
            store.get_by_path(meta["artifact_path"])
        self.assertIn("path mismatch", str(ctx.exception).lower())

    def test_get_by_path_invalid_suffix_rejected(self):
        """Test: non-.artifact suffix rejected."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        with self.assertRaises(ValueError):
            store.get_by_path("run-1/some_file.txt")

    def test_get_by_path_empty_path_rejected(self):
        """Test: empty path rejected."""
        from mcp_agent.artifact_store import ArtifactStore

        store = ArtifactStore()
        with self.assertRaises(ValueError):
            store.get_by_path("")


if __name__ == "__main__":
    unittest.main()
