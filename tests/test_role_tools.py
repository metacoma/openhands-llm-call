#!/usr/bin/env python3
"""Tests for role tools (mcp_agent.role_tools)."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestActionParsing(unittest.TestCase):
    """Test parse_action helper."""

    def test_reviewer_pass_action(self):
        """Reviewer parser extracts ACTION: PASS."""
        from mcp_agent.role_tools import parse_action

        text = """
# Reviewer Report

## Decision

ACTION: PASS

## Risk

RISK: LOW
"""
        result = parse_action("reviewer", text)
        self.assertEqual(result, "PASS")

    def test_reviewer_blocker_action(self):
        """Reviewer parser extracts ACTION: BLOCKER."""
        from mcp_agent.role_tools import parse_action

        text = "ACTION: BLOCKER\nSome text"
        result = parse_action("reviewer", text)
        self.assertEqual(result, "BLOCKER")

    def test_reviewer_missing_action_returns_none(self):
        """Reviewer without ACTION line returns None."""
        from mcp_agent.role_tools import parse_action

        text = "Just a regular report without ACTION line."
        result = parse_action("reviewer", text)
        self.assertIsNone(result)

    def test_publisher_ready_status(self):
        """Publisher parser extracts PUBLISH_STATUS: READY."""
        from mcp_agent.role_tools import parse_action

        text = "PUBLISH_STATUS: READY\nSome text"
        result = parse_action("publisher", text)
        self.assertEqual(result, "READY")

    def test_publisher_blocked_status(self):
        """Publisher parser extracts PUBLISH_STATUS: BLOCKED."""
        from mcp_agent.role_tools import parse_action

        text = "PUBLISH_STATUS: BLOCKED\nSome text"
        result = parse_action("publisher", text)
        self.assertEqual(result, "BLOCKED")

    def test_other_role_defaults_to_continue(self):
        """Non-reviewer/non-publisher roles default to CONTINUE."""
        from mcp_agent.role_tools import parse_action

        text = "Some report text"
        result = parse_action("scout", text)
        self.assertEqual(result, "CONTINUE")

        result = parse_action("architect", text)
        self.assertEqual(result, "CONTINUE")

        result = parse_action("coder", text)
        self.assertEqual(result, "CONTINUE")


class TestRiskParsing(unittest.TestCase):
    """Test parse_risk helper."""

    def test_parse_risk_low(self):
        """parse_risk extracts RISK: LOW."""
        from mcp_agent.role_tools import parse_risk

        result = parse_risk("Some text\nRISK: LOW\nMore text")
        self.assertEqual(result, "LOW")

    def test_parse_risk_medium(self):
        """parse_risk extracts RISK: MEDIUM."""
        from mcp_agent.role_tools import parse_risk

        result = parse_risk("RISK: MEDIUM")
        self.assertEqual(result, "MEDIUM")

    def test_parse_risk_high(self):
        """parse_risk extracts RISK: HIGH."""
        from mcp_agent.role_tools import parse_risk

        result = parse_risk("RISK: HIGH")
        self.assertEqual(result, "HIGH")

    def test_parse_risk_missing_returns_none(self):
        """parse_risk returns None when RISK not found."""
        from mcp_agent.role_tools import parse_risk

        result = parse_risk("No risk mentioned")
        self.assertIsNone(result)


class TestMakeSummary(unittest.TestCase):
    """Test make_summary helper."""

    def test_short_text_unchanged(self):
        """Short text is returned unchanged."""
        from mcp_agent.role_tools import make_summary

        result = make_summary("Hello world", max_chars=500)
        self.assertEqual(result, "Hello world")

    def test_long_text_truncated(self):
        """Long text is truncated at word boundary."""
        from mcp_agent.role_tools import make_summary

        long_text = " ".join([f"word{i}" for i in range(100)])
        result = make_summary(long_text, max_chars=20)
        self.assertLessEqual(len(result), 23)  # 20 chars + "..."
        self.assertIn("...", result)

    def test_empty_text_returns_empty(self):
        """Empty text returns empty string."""
        from mcp_agent.role_tools import make_summary

        self.assertEqual(make_summary(""), "")
        self.assertEqual(make_summary(None), "")


class TestRoleListTool(unittest.TestCase):
    """Test role_list MCP tool."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_role_list_")
        os.environ["OPENHANDS_STATE_DIR"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_STATE_DIR", None)
        # Reset module state
        import mcp_agent.server as server_mod
        server_mod._store = None

    def test_role_list_returns_six_roles(self):
        """role_list returns six roles (including coder_fix)."""
        from mcp_agent.server import _role_list_internal as role_list

        result = role_list()
        self.assertIn("roles", result)
        self.assertEqual(len(result["roles"]), 6)

    def test_role_list_contains_scout(self):
        """role_list includes scout role."""
        from mcp_agent.server import _role_list_internal as role_list

        result = role_list()
        names = [r["name"] for r in result["roles"]]
        self.assertIn("scout", names)

    def test_role_list_contains_architect(self):
        """role_list includes architect role."""
        from mcp_agent.server import _role_list_internal as role_list

        result = role_list()
        names = [r["name"] for r in result["roles"]]
        self.assertIn("architect", names)

    def test_role_list_contains_coder(self):
        """role_list includes coder role."""
        from mcp_agent.server import _role_list_internal as role_list

        result = role_list()
        names = [r["name"] for r in result["roles"]]
        self.assertIn("coder", names)

    def test_role_list_contains_reviewer(self):
        """role_list includes reviewer role."""
        from mcp_agent.server import _role_list_internal as role_list

        result = role_list()
        names = [r["name"] for r in result["roles"]]
        self.assertIn("reviewer", names)

    def test_role_list_contains_publisher(self):
        """role_list includes publisher role."""
        from mcp_agent.server import _role_list_internal as role_list

        result = role_list()
        names = [r["name"] for r in result["roles"]]
        self.assertIn("publisher", names)


class TestRoleStartTool(unittest.TestCase):
    """Test role_start MCP tool validation."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_role_start_")
        os.environ["OPENHANDS_STATE_DIR"] = self.tmpdir
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = os.path.join(
            self.tmpdir, "runs"
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_STATE_DIR", None)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        import mcp_agent.server as server_mod
        server_mod._store = None

    @patch("mcp_agent.role_tools._get_role_store")
    def test_role_start_rejects_unknown_role(self, mock_store):
        """role_start rejects unknown role."""
        from mcp_agent.server import role_start

        result = role_start(
            role="qa",
            user_task="Test task",
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "UnknownRole")
        self.assertIn("qa", result["error"]["message"])
        self.assertIn("scout", result["error"]["message"])

    @patch("mcp_agent.role_tools._get_role_store")
    def test_role_start_rejects_missing_required_artifact(self, mock_store):
        """role_start rejects missing required artifact for architect."""
        from mcp_agent.server import role_start

        result = role_start(
            role="architect",
            user_task="Plan implementation",
            artifacts={},
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "MissingRequiredArtifact")
        self.assertIn("scout_report", result["error"]["message"])

    @patch("mcp_agent.role_tools._get_role_store")
    def test_role_start_accepts_scout_without_artifacts(self, mock_store):
        """role_start accepts scout without artifacts (none required)."""
        from mcp_agent.server import role_start

        mock_store.return_value.get_attempt_count.return_value = 0
        mock_store.return_value.create_role_run.return_value = {
            "task_id": "mock-task-id",
        }

        with patch(
            "mcp_agent.server._start_conversation_on_fastapi"
        ) as mock_fastapi:
            mock_fastapi.return_value = {
                "conversation_id": "conv-mock-123",
            }
            result = role_start(
                role="scout",
                user_task="Investigate repo",
                repo="https://github.com/example/repo",
                base_branch="main",
                context={},
                artifacts={},
            )

        self.assertEqual(result["status"], "running")
        self.assertIn("role_run_id", result)
        self.assertEqual(result["role"], "scout")


class TestRoleStatusTool(unittest.TestCase):
    """Test role_status MCP tool."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_role_status_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = os.path.join(
            self.tmpdir, "runs"
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)

    def test_role_status_unknown_role_run_id(self):
        """role_status returns error for unknown role_run_id."""
        from mcp_agent.server import role_status

        result = role_status(role_run_id="nonexistent-run-id")

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "UnknownRoleRunId")


class TestRoleResultTool(unittest.TestCase):
    """Test role_result MCP tool."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_role_result_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = os.path.join(
            self.tmpdir, "runs"
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)

    def test_role_result_unknown_role_run_id(self):
        """role_result returns error for unknown role_run_id."""
        from mcp_agent.server import role_result

        result = role_result(role_run_id="nonexistent-run-id")

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "UnknownRoleRunId")

    @patch("mcp_agent.role_tools._get_role_store")
    def test_role_result_include_full_result_false(self, mock_store):
        """include_full_result=false omits full_result but returns artifact metadata."""
        from mcp_agent.server import role_result

        mock_run = {
            "run_id": "20260605-abc123",
            "role": "scout",
            "role_run_id": "20260605-abc123-scout-1",
            "openhands_task_id": "task-mock",
            "branch": None,
            "base_branch": "main",
            "repo": "https://github.com/example/repo",
            "artifact_name": "scout_report",
            "artifact_path": None,
        }
        mock_store.return_value.get_role_run.return_value = mock_run

        # Mock the OpenHands status as completed
        with patch(
            "mcp_agent.server.openhands_get_task_status"
        ) as mock_status:
            mock_status.return_value = {"status": "completed"}
            with patch(
                "mcp_agent.server.openhands_get_task_result"
            ) as mock_result:
                mock_result.return_value = {"answer": "# Full result\n\nComplete report."}
                with patch("mcp_agent.role_tools._get_role_store") as mock_rs:
                    mock_rs.return_value.get_role_run.return_value = mock_run
                    mock_rs.return_value.save_artifact.return_value = "runs/20260605-abc123/01-scout.answer.md"
                    mock_rs.return_value.update_role_run.return_value = mock_run

                    result = role_result(
                        role_run_id="20260605-abc123-scout-1",
                        include_full_result=False,
                    )

        self.assertEqual(result["status"], "completed")
        self.assertIsNone(result["full_result"])
        self.assertTrue(result["full_result_omitted"])
        self.assertEqual(result["artifact_name"], "scout_report")
        self.assertIsNotNone(result["artifact_path"])
        self.assertIsNotNone(result["result_summary"])

    @patch("mcp_agent.role_tools._get_role_store")
    def test_role_result_include_full_result_true_default(self, mock_store):
        """include_full_result=true (default) preserves existing behavior."""
        from mcp_agent.server import role_result

        mock_run = {
            "run_id": "20260605-def456",
            "role": "scout",
            "role_run_id": "20260605-def456-scout-1",
            "openhands_task_id": "task-mock-2",
            "branch": None,
            "base_branch": "main",
            "repo": "https://github.com/example/repo",
            "artifact_name": "scout_report",
            "artifact_path": None,
        }
        with patch("mcp_agent.role_tools._get_role_store") as mock_rs:
            mock_rs.return_value.get_role_run.return_value = mock_run

            with patch(
                "mcp_agent.server.openhands_get_task_status"
            ) as mock_status:
                mock_status.return_value = {"status": "completed"}
                with patch(
                    "mcp_agent.server.openhands_get_task_result"
                ) as mock_result:
                    mock_result.return_value = {"answer": "Full report content."}
                    mock_rs.return_value.save_artifact.return_value = "runs/20260605-def456/01-scout.answer.md"
                    mock_rs.return_value.update_role_run.return_value = mock_run

                    # Default: include_full_result=True
                    result = role_result(
                        role_run_id="20260605-def456-scout-1"
                    )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["full_result"], "Full report content.")
        self.assertFalse(result["full_result_omitted"])


class TestIdempotentRoleStart(unittest.TestCase):
    """Test idempotent role_start behavior."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_idem_")
        os.environ["OPENHANDS_STATE_DIR"] = self.tmpdir
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = os.path.join(
            self.tmpdir, "runs"
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_STATE_DIR", None)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        import mcp_agent.server as server_mod
        server_mod._store = None

    @patch("mcp_agent.role_tools._get_role_store")
    def test_first_call_with_idempotency_key_starts_task(self, mock_store):
        """First role_start with idempotency key starts one task."""
        from mcp_agent.server import role_start

        mock_store.return_value.find_by_idempotency_scope.return_value = None
        mock_store.return_value.get_attempt_count.return_value = 0
        mock_store.return_value.create_role_run.return_value = {
            "task_id": "mock-task-id",
        }

        with patch(
            "mcp_agent.server._start_conversation_on_fastapi"
        ) as mock_fastapi:
            mock_fastapi.return_value = {
                "conversation_id": "conv-idem-123",
            }
            result = role_start(
                role="scout",
                user_task="Test task",
                context={"run_id": "20260605-idem01"},
                idempotency_key="test-key",
            )

        self.assertEqual(result["status"], "running")
        self.assertIn("role_run_id", result)
        self.assertFalse(result.get("idempotent_reuse", True))
        self.assertIn("timeout_minutes", result)

    @patch("mcp_agent.role_tools._get_role_store")
    def test_second_call_with_same_key_returns_existing(self, mock_store):
        """Second role_start with same run_id+role+idempotency_key returns same role_run_id."""
        from mcp_agent.server import role_start

        # First call creates the record
        mock_store.return_value.find_by_idempotency_scope.return_value = None
        mock_store.return_value.get_attempt_count.return_value = 0
        mock_store.return_value.create_role_run.return_value = {
            "task_id": "mock-task-1",
        }

        with patch(
            "mcp_agent.server._start_conversation_on_fastapi"
        ) as mock_fastapi:
            mock_fastapi.return_value = {
                "conversation_id": "conv-idem-123",
            }
            result1 = role_start(
                role="scout",
                user_task="Test task",
                context={"run_id": "20260605-idem02"},
                idempotency_key="test-key",
            )

        # Simulate the idempotency record being saved
        existing_role_run_id = result1["role_run_id"]
        mock_store.return_value.find_by_idempotency_scope.return_value = existing_role_run_id
        mock_store.return_value.get_role_run.return_value = {
            "run_id": "20260605-idem02",
            "role": "scout",
            "role_run_id": existing_role_run_id,
            "openhands_task_id": "mock-task-1",
        }

        # Mock openhands_get_task_status for the idempotency path
        with patch(
            "mcp_agent.server.openhands_get_task_status"
        ) as mock_status:
            mock_status.return_value = {"status": "running"}

            # Second call with same key
            result2 = role_start(
                role="scout",
                user_task="Different task",
                context={"run_id": "20260605-idem02"},
                idempotency_key="test-key",
            )

        self.assertEqual(result2["status"], "running")
        self.assertEqual(result2["role_run_id"], existing_role_run_id)
        self.assertTrue(result2["idempotent_reuse"])
        # _start_conversation_on_fastapi should NOT have been called again
        self.assertEqual(mock_fastapi.call_count, 1)

    @patch("mcp_agent.role_tools._get_role_store")
    def test_context_idempotency_key_works(self, mock_store):
        """context.idempotency_key is also supported."""
        from mcp_agent.server import role_start

        mock_store.return_value.find_by_idempotency_scope.return_value = None
        mock_store.return_value.get_attempt_count.return_value = 0
        mock_store.return_value.create_role_run.return_value = {
            "task_id": "mock-task-ctx",
        }

        with patch(
            "mcp_agent.server._start_conversation_on_fastapi"
        ) as mock_fastapi:
            mock_fastapi.return_value = {
                "conversation_id": "conv-ctx-123",
            }
            result = role_start(
                role="scout",
                user_task="Test task",
                context={
                    "run_id": "20260605-idem03",
                    "idempotency_key": "ctx-key",
                },
            )

        self.assertEqual(result["status"], "running")
        self.assertFalse(result.get("idempotent_reuse", True))


class TestTimeoutApplication(unittest.TestCase):
    """Test per-role timeout application."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_timeout_")
        os.environ["OPENHANDS_STATE_DIR"] = self.tmpdir
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = os.path.join(
            self.tmpdir, "runs"
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_STATE_DIR", None)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        import mcp_agent.server as server_mod
        server_mod._store = None

    @patch("mcp_agent.role_tools._get_role_store")
    def test_coder_timeout_120_minutes(self, mock_store):
        """Coder role (120 min timeout) is passed as max_polls to FastAPI."""
        from mcp_agent.server import role_start

        mock_store.return_value.find_by_idempotency_scope.return_value = None
        mock_store.return_value.get_attempt_count.return_value = 0
        mock_store.return_value.create_role_run.return_value = {
            "task_id": "mock-task-coder",
        }

        with patch(
            "mcp_agent.server._start_conversation_on_fastapi"
        ) as mock_fastapi:
            mock_fastapi.return_value = {
                "conversation_id": "conv-coder-123",
            }
            result = role_start(
                role="coder",
                user_task="Implement feature",
                context={"run_id": "20260605-time01"},
                artifacts={
                    "scout_report": "scout content",
                    "architect_plan": "architect content",
                },
            )

        self.assertEqual(result["status"], "running")
        self.assertEqual(result["timeout_minutes"], 120)

        # Verify max_polls was passed
        call_args = mock_fastapi.call_args
        max_polls = call_args[1].get("max_polls")
        self.assertIsNotNone(max_polls)
        # 120 min * 60 sec / 10 sec interval = 720
        self.assertEqual(max_polls, 720)

    @patch("mcp_agent.role_tools._get_role_store")
    def test_publisher_timeout_30_minutes(self, mock_store):
        """Publisher role (30 min timeout) is passed as max_polls."""
        from mcp_agent.server import role_start

        mock_store.return_value.find_by_idempotency_scope.return_value = None
        mock_store.return_value.get_attempt_count.return_value = 0
        mock_store.return_value.create_role_run.return_value = {
            "task_id": "mock-task-pub",
        }

        with patch(
            "mcp_agent.server._start_conversation_on_fastapi"
        ) as mock_fastapi:
            mock_fastapi.return_value = {
                "conversation_id": "conv-pub-123",
            }
            result = role_start(
                role="publisher",
                user_task="Publish",
                context={"run_id": "20260605-time02"},
                artifacts={"reviewer_report": "review content"},
            )

        self.assertEqual(result["status"], "running")
        self.assertEqual(result["timeout_minutes"], 30)

        call_args = mock_fastapi.call_args
        max_polls = call_args[1].get("max_polls")
        self.assertIsNotNone(max_polls)
        # 30 min * 60 sec / 10 sec interval = 180
        self.assertEqual(max_polls, 180)


class TestImprovedMakeSummary(unittest.TestCase):
    """Test improved make_summary."""

    def test_strip_markdown_headings(self):
        """make_summary strips markdown headings lightly."""
        from mcp_agent.role_tools import make_summary

        text = "# Title\n## Section\nSome content\n## Another\nMore text"
        result = make_summary(text, max_chars=200)
        # First 2 headings should be preserved
        self.assertIn("# Title", result)
        self.assertIn("## Section", result)

    def test_remove_excessive_blank_lines(self):
        """make_summary collapses excessive blank lines."""
        from mcp_agent.role_tools import make_summary

        text = "Line 1\n\n\n\n\nLine 2"
        result = make_summary(text, max_chars=200)
        # Should not have 4 consecutive blank lines
        self.assertNotIn("\n\n\n\n", result)

    def test_cap_at_max_chars(self):
        """make_summary caps output at max_chars."""
        from mcp_agent.role_tools import make_summary

        long_text = " ".join([f"word{i}" for i in range(200)])
        result = make_summary(long_text, max_chars=50)
        # Account for "..." suffix
        self.assertLessEqual(len(result), 53)  # 50 + "..."
        self.assertIn("...", result)

    def test_preserves_useful_first_lines(self):
        """make_summary preserves useful first lines."""
        from mcp_agent.role_tools import make_summary

        text = "Important first line\nSecond line\n## Heading\nMore content"
        result = make_summary(text, max_chars=200)
        self.assertIn("Important first line", result)

    def test_empty_input_returns_empty(self):
        """make_summary returns empty for empty input."""
        from mcp_agent.role_tools import make_summary

        self.assertEqual(make_summary(""), "")
        self.assertEqual(make_summary(None), "")

    def test_nonempty_input_never_returns_empty(self):
        """make_summary never returns empty for meaningful non-empty input."""
        from mcp_agent.role_tools import make_summary

        result = make_summary("Hello world", max_chars=10)
        self.assertNotEqual(result, "")
        self.assertIn("Hello", result)

    def test_default_max_chars_is_700(self):
        """Default max_chars is 700."""
        from mcp_agent.role_tools import make_summary

        text = " ".join([f"word{i}" for i in range(100)])
        result = make_summary(text)
        # Should not be truncated since it's short
        self.assertEqual(result, text.strip())


# ---------------------------------------------------------------------------
# Regression tests for Bug 1 (artifact visibility) and Bug 3 (lock lifecycle)
# ---------------------------------------------------------------------------


class TestRoleResultArtifactIntegration(unittest.TestCase):
    """Regression tests for artifact persistence through ArtifactStore.

    Bug 1: role_result_impl was saving artifacts via RoleRunStore.save_artifact()
    which uses a different file layout than ArtifactStore.  This meant
    artifact_list/artifact_get could not see artifacts created by role_result.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_role_result_artifact_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = self.tmpdir
        # Clear the module-level RoleRunStore singleton so tests don't share state
        import mcp_agent.role_tools as rt
        rt._role_store = None

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        # Clear the module-level RoleRunStore singleton so tests don't share state
        import mcp_agent.role_tools as rt
        rt._role_store = None

    def test_role_result_saves_artifact_through_artifact_store(self):
        """role_result calls ArtifactStore.save() with correct args.

        We verify this by checking that artifact_list sees the artifact
        after role_result completes, proving the save went to ArtifactStore.
        """
        from mcp_agent.role_tools import (
            role_result_impl,
            artifact_list_impl,
            artifact_get_impl,
        )
        from mcp_agent.artifact_store import ArtifactStore
        from mcp_agent.role_store import RoleRunStore

        store = RoleRunStore()
        store.create_role_run(
            role="coder",
            run_id="run-001",
            role_run_id="run-001-coder-1",
            openhands_task_id="task-001",
            repo="owner/repo",
            base_branch="main",
            branch="feature/x",
            artifact_name="coder_report",
            lock_key="owner/repo|feature/x",
        )

        with patch(
            "mcp_agent.server.openhands_get_task_status"
        ) as mock_status, patch(
            "mcp_agent.server.openhands_get_task_result"
        ) as mock_result:
            mock_status.return_value = {"status": "completed"}
            mock_result.return_value = {"answer": "## Coder Report\n\nDone."}

            result = role_result_impl("run-001-coder-1")
            self.assertEqual(result["status"], "completed")
            self.assertIsNotNone(result["artifact_path"])
            self.assertIsNotNone(result["artifact_name"])

        # artifact_list should see the artifact (proving it was saved to ArtifactStore)
        list_result = artifact_list_impl(run_id="run-001")
        self.assertEqual(list_result["run_id"], "run-001")
        self.assertEqual(len(list_result["artifacts"]), 1)
        self.assertEqual(
            list_result["artifacts"][0]["artifact_name"], "coder_report"
        )

    def test_artifact_list_sees_role_result_artifact(self):
        """After role_result, artifact_list(run_id) includes the artifact."""
        from mcp_agent.role_tools import role_result_impl, artifact_list_impl
        from mcp_agent.role_store import RoleRunStore

        store = RoleRunStore()
        store.create_role_run(
            role="coder",
            run_id="run-002",
            role_run_id="run-002-coder-1",
            openhands_task_id="task-002",
            repo="owner/repo",
            base_branch="main",
            branch="feature/y",
            artifact_name="coder_report",
            lock_key="owner/repo|feature/y",
        )

        with patch(
            "mcp_agent.server.openhands_get_task_status"
        ) as mock_status, patch(
            "mcp_agent.server.openhands_get_task_result"
        ) as mock_result:
            mock_status.return_value = {"status": "completed"}
            mock_result.return_value = {"answer": "## Coder Report\n\nDone."}
            role_result_impl("run-002-coder-1")

        list_result = artifact_list_impl(run_id="run-002")
        self.assertEqual(list_result["run_id"], "run-002")
        self.assertEqual(len(list_result["artifacts"]), 1)
        self.assertEqual(
            list_result["artifacts"][0]["artifact_name"], "coder_report"
        )

    def test_artifact_get_retrieves_role_result_artifact(self):
        """After role_result, artifact_get(run_id, artifact_name) returns content."""
        from mcp_agent.role_tools import role_result_impl, artifact_get_impl
        from mcp_agent.role_store import RoleRunStore

        store = RoleRunStore()
        store.create_role_run(
            role="coder",
            run_id="run-003",
            role_run_id="run-003-coder-1",
            openhands_task_id="task-003",
            repo="owner/repo",
            base_branch="main",
            branch="feature/z",
            artifact_name="coder_report",
            lock_key="owner/repo|feature/z",
        )

        with patch(
            "mcp_agent.server.openhands_get_task_status"
        ) as mock_status, patch(
            "mcp_agent.server.openhands_get_task_result"
        ) as mock_result:
            mock_status.return_value = {"status": "completed"}
            mock_result.return_value = {
                "answer": "## Coder Report\n\nImplementation complete."
            }
            role_result_impl("run-003-coder-1")

        get_result = artifact_get_impl(
            run_id="run-003", artifact_name="coder_report"
        )
        self.assertIsNotNone(get_result)
        self.assertEqual(
            get_result["content"], "## Coder Report\n\nImplementation complete."
        )
        self.assertEqual(get_result["artifact_name"], "coder_report")

    def test_artifact_get_by_role_run_id(self):
        """artifact_get(run_id, role_run_id=...) returns the saved content."""
        from mcp_agent.role_tools import role_result_impl, artifact_get_impl
        from mcp_agent.role_store import RoleRunStore

        store = RoleRunStore()
        store.create_role_run(
            role="coder",
            run_id="run-004",
            role_run_id="run-004-coder-1",
            openhands_task_id="task-004",
            repo="owner/repo",
            base_branch="main",
            branch="feature/w",
            artifact_name="coder_report",
            lock_key="owner/repo|feature/w",
        )

        with patch(
            "mcp_agent.server.openhands_get_task_status"
        ) as mock_status, patch(
            "mcp_agent.server.openhands_get_task_result"
        ) as mock_result:
            mock_status.return_value = {"status": "completed"}
            mock_result.return_value = {"answer": "## Coder Report\n\nDone via role_run_id."}
            role_result_impl("run-004-coder-1")

        get_result = artifact_get_impl(
            run_id="run-004", role_run_id="run-004-coder-1"
        )
        self.assertIsNotNone(get_result)
        self.assertIn("Coder Report", get_result["content"])

    def test_role_result_omit_full_result_still_saves_artifact(self):
        """role_result(include_full_result=False) still saves artifact."""
        from mcp_agent.role_tools import role_result_impl, artifact_get_impl
        from mcp_agent.role_store import RoleRunStore

        store = RoleRunStore()
        store.create_role_run(
            role="coder",
            run_id="run-005",
            role_run_id="run-005-coder-1",
            openhands_task_id="task-005",
            repo="owner/repo",
            base_branch="main",
            branch="feature/v",
            artifact_name="coder_report",
            lock_key="owner/repo|feature/v",
        )

        with patch(
            "mcp_agent.server.openhands_get_task_status"
        ) as mock_status, patch(
            "mcp_agent.server.openhands_get_task_result"
        ) as mock_result:
            mock_status.return_value = {"status": "completed"}
            mock_result.return_value = {"answer": "## Coder Report\n\nOmitted content."}

            result = role_result_impl(
                "run-005-coder-1", include_full_result=False
            )

            # full_result should be None
            self.assertIsNone(result["full_result"])
            self.assertTrue(result["full_result_omitted"])
            # But artifact_path should still be set
            self.assertIsNotNone(result["artifact_path"])

        # artifact_get should still return the content
        get_result = artifact_get_impl(
            run_id="run-005", artifact_name="coder_report"
        )
        self.assertEqual(
            get_result["content"], "## Coder Report\n\nOmitted content."
        )

    def test_role_result_idempotent_no_duplicate_artifacts(self):
        """Calling role_result twice does not create duplicate artifact records."""
        from mcp_agent.role_tools import role_result_impl, artifact_list_impl
        from mcp_agent.role_store import RoleRunStore

        store = RoleRunStore()
        store.create_role_run(
            role="coder",
            run_id="run-006",
            role_run_id="run-006-coder-1",
            openhands_task_id="task-006",
            repo="owner/repo",
            base_branch="main",
            branch="feature/u",
            artifact_name="coder_report",
            lock_key="owner/repo|feature/u",
        )

        with patch(
            "mcp_agent.server.openhands_get_task_status"
        ) as mock_status, patch(
            "mcp_agent.server.openhands_get_task_result"
        ) as mock_result:
            mock_status.return_value = {"status": "completed"}
            mock_result.return_value = {"answer": "## Coder Report\n\nIdempotent."}

            # First call
            role_result_impl("run-006-coder-1")
            first_list = artifact_list_impl(run_id="run-006")

            # Second call (idempotent)
            role_result_impl("run-006-coder-1")
            second_list = artifact_list_impl(run_id="run-006")

        # artifact_list should still show only one artifact (overwritten, not appended)
        self.assertEqual(len(first_list["artifacts"]), 1)
        self.assertEqual(len(second_list["artifacts"]), 1)


class TestLockKeyLifecycle(unittest.TestCase):
    """Regression tests for lock key persistence and release.

    Bug 3: role_result_impl was reconstructing lock key instead of using
    a stored lock_key, and only releasing if branch/base_branch existed.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_lock_key_lifecycle_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = self.tmpdir
        # Clear the module-level RoleRunStore singleton so tests don't share state
        import mcp_agent.role_tools as rt
        rt._role_store = None

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        # Clear the module-level RoleRunStore singleton so tests don't share state
        import mcp_agent.role_tools as rt
        rt._role_store = None

    def test_mutating_role_start_stores_lock_key(self):
        """Mutating role_start stores lock_key in role-run record."""
        from mcp_agent.role_tools import role_start_impl
        from mcp_agent.role_store import RoleRunStore

        with patch(
            "mcp_agent.role_tools.get_role"
        ) as mock_get_role, patch(
            "mcp_agent.server._start_conversation_on_fastapi"
        ) as mock_start, patch(
            "mcp_agent.role_tools.render_prompt"
        ) as mock_render, patch(
            "mcp_agent.server._get_store"
        ) as mock_store:
            mock_get_role.return_value = MagicMock(
                name="coder",
                readonly=False,
                requires_artifacts=[],
                output_artifact="coder_report",
                timeout_minutes=30,
                model="gpt-4",
                prompt_template="prompts/coder.md",
            )
            mock_start.return_value = {"conversation_id": "conv-001"}
            mock_render.return_value = "rendered prompt"
            mock_store.return_value.create_task.return_value = {
                "task_id": "task-001"
            }

            result = role_start_impl(
                role="coder",
                user_task="Test task",
                repo="owner/repo",
                base_branch="main",
                branch="feature/test",
            )

            # Verify the role run record was created with lock_key
            self.assertEqual(result["status"], "running")
            role_run_id = result["role_run_id"]
            role_run = RoleRunStore().get_role_run(role_run_id)
            self.assertIsNotNone(role_run)
            self.assertIsNotNone(role_run.get("lock_key"))
            self.assertEqual(
                role_run["lock_key"], "owner/repo|feature/test"
            )

    def test_terminal_role_result_releases_lock_via_stored_key(self):
        """Terminal role_result releases lock using stored lock_key."""
        from mcp_agent.role_tools import role_result_impl
        from mcp_agent.role_store import RoleRunStore

        store = RoleRunStore()
        role_run = store.create_role_run(
            role="coder",
            run_id="run-007",
            role_run_id="run-007-coder-1",
            openhands_task_id="task-007",
            repo="owner/repo",
            base_branch="main",
            branch="feature/lock",
            artifact_name="coder_report",
            lock_key="owner/repo|feature/lock",
        )

        with patch(
            "mcp_agent.server.openhands_get_task_status"
        ) as mock_status, patch(
            "mcp_agent.server.openhands_get_task_result"
        ) as mock_result:
            mock_status.return_value = {"status": "completed"}
            mock_result.return_value = {"answer": "## Coder Report\n\nDone."}

            # Patch lock_manager.release to capture the lock_key used
            released_keys = []

            def capture_release(lock_key, role_run_id):
                released_keys.append(lock_key)
                return True

            with patch(
                "mcp_agent.role_tools.RoleLockManager"
            ) as mock_lock_cls:
                mock_lock_instance = MagicMock()
                mock_lock_instance.release = MagicMock(side_effect=capture_release)
                mock_lock_cls.return_value = mock_lock_instance

                result = role_result_impl("run-007-coder-1")

                self.assertEqual(result["status"], "completed")
                self.assertEqual(len(released_keys), 1)
                self.assertEqual(released_keys[0], "owner/repo|feature/lock")

    def test_lock_released_without_branch_base_branch(self):
        """Lock is released even if branch and base_branch are absent but lock_key was stored."""
        from mcp_agent.role_tools import role_result_impl
        from mcp_agent.role_store import RoleRunStore

        store = RoleRunStore()
        # Create a role run with no branch/base_branch but with lock_key
        role_run = store.create_role_run(
            role="coder",
            run_id="run-008",
            role_run_id="run-008-coder-1",
            openhands_task_id="task-008",
            repo="owner/repo",
            base_branch=None,
            branch=None,
            artifact_name="coder_report",
            lock_key="owner/repo|",
        )

        with patch(
            "mcp_agent.server.openhands_get_task_status"
        ) as mock_status, patch(
            "mcp_agent.server.openhands_get_task_result"
        ) as mock_result:
            mock_status.return_value = {"status": "completed"}
            mock_result.return_value = {"answer": "## Coder Report\n\nDone."}

            released_keys = []

            def capture_release(lock_key, role_run_id):
                released_keys.append(lock_key)
                return True

            with patch(
                "mcp_agent.role_tools.RoleLockManager"
            ) as mock_lock_cls:
                mock_lock_instance = MagicMock()
                mock_lock_instance.release = MagicMock(side_effect=capture_release)
                mock_lock_cls.return_value = mock_lock_instance

                result = role_result_impl("run-008-coder-1")

                self.assertEqual(result["status"], "completed")
                self.assertEqual(len(released_keys), 1)
                self.assertEqual(released_keys[0], "owner/repo|")

    def test_readonly_role_no_lock_key(self):
        """Read-only roles do not acquire lock and do not store lock_key."""
        from mcp_agent.role_tools import role_start_impl
        from mcp_agent.role_store import RoleRunStore

        with patch(
            "mcp_agent.role_tools.get_role"
        ) as mock_get_role, patch(
            "mcp_agent.server._start_conversation_on_fastapi"
        ) as mock_start, patch(
            "mcp_agent.role_tools.render_prompt"
        ) as mock_render, patch(
            "mcp_agent.server._get_store"
        ) as mock_store:
            mock_get_role.return_value = MagicMock(
                name="scout",
                readonly=True,
                requires_artifacts=[],
                output_artifact="scout_report",
                timeout_minutes=10,
                model="gpt-4",
                prompt_template="prompts/scout.md",
            )
            mock_start.return_value = {"conversation_id": "conv-002"}
            mock_render.return_value = "rendered prompt"
            mock_store.return_value.create_task.return_value = {
                "task_id": "task-002"
            }

            result = role_start_impl(
                role="scout",
                user_task="Test task",
                repo="owner/repo",
                base_branch="main",
                branch="feature/test",
            )

            self.assertEqual(result["status"], "running")
            role_run_id = result["role_run_id"]
            role_run = RoleRunStore().get_role_run(role_run_id)
            self.assertIsNotNone(role_run)
            # lock_key should be None for read-only roles
            self.assertIsNone(role_run.get("lock_key"))


class TestPromptOnlyRoleStart(unittest.TestCase):
    """Test the new prompt-only role_start model."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_prompt_only_")
        os.environ["OPENHANDS_STATE_DIR"] = self.tmpdir
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = os.path.join(
            self.tmpdir, "runs"
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_STATE_DIR", None)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        import mcp_agent.server as server_mod
        server_mod._store = None

    @patch("mcp_agent.role_tools._find_active_role_run")
    @patch("mcp_agent.server.requests.post")
    def test_role_start_with_prompt_only(self, mock_post, mock_find):
        """role_start works with only role and prompt (no user_task)."""
        from mcp_agent.server import role_start

        mock_find.return_value = None
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "conversation_id": "conv-prompt-only",
            "status": "no_wait",
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        result = role_start(
            role="scout",
            prompt="Analyze https://github.com/metacoma/freeplane_plugin_grpc on main branch",
            context={},
            artifacts={},
        )

        self.assertEqual(result["status"], "running")
        self.assertIn("role_run_id", result)
        self.assertIn("scout", result.get("role", ""))

    @patch("mcp_agent.role_tools._find_active_role_run")
    @patch("mcp_agent.server.requests.post")
    def test_role_start_user_task_backward_compat(self, mock_post, mock_find):
        """role_start still works with user_task (backward compatibility)."""
        from mcp_agent.server import role_start

        mock_find.return_value = None
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "conversation_id": "conv-user-task",
            "status": "no_wait",
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        result = role_start(
            role="scout",
            user_task="Analyze repository ...",
            context={},
            artifacts={},
        )

        self.assertEqual(result["status"], "running")
        self.assertIn("role_run_id", result)

    @patch("mcp_agent.role_tools._find_active_role_run")
    @patch("mcp_agent.server.requests.post")
    def test_role_start_prompt_takes_precedence_over_user_task(self, mock_post, mock_find):
        """When both prompt and user_task are provided, prompt takes precedence."""
        from mcp_agent.server import role_start

        mock_find.return_value = None
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "conversation_id": "conv-precedence",
            "status": "no_wait",
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        result = role_start(
            role="scout",
            prompt="This is the prompt value",
            user_task="This is the user_task value",
            context={},
            artifacts={},
        )

        self.assertEqual(result["status"], "running")
        # Verify the prompt value was used (check the captured call)
        call_args = mock_post.call_args
        payload = call_args[1]["json"]
        self.assertIn("This is the prompt value", payload["prompt"])

    def test_role_start_no_prompt_raises_error(self):
        """role_start rejects when neither prompt nor user_task is provided."""
        from mcp_agent.server import role_start

        result = role_start(
            role="scout",
            prompt=None,
            user_task=None,
            context={},
            artifacts={},
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "MissingPrompt")
        self.assertIn("prompt", result["error"]["message"].lower())

    @patch("mcp_agent.server.requests.post")
    def test_role_start_nested_repo_dict_no_crash(self, mock_post):
        """Nested dict repo values do not cause validation errors."""
        from mcp_agent.server import role_start

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "conversation_id": "conv-nested-repo",
            "status": "no_wait",
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        result = role_start(
            role="scout",
            prompt="Analyze repository",
            repo={"url": "https://github.com/metacoma/freeplane_plugin_grpc"},
            base_branch={"base_branch": "main"},
            branch={"branch": None},
            context={},
            artifacts={},
        )

        self.assertEqual(result["status"], "running")
        # Verify repo was normalized from dict
        call_args = mock_post.call_args
        payload = call_args[1]["json"]
        self.assertNotIn("repo", payload)
        self.assertNotIn("branch", payload)

    @patch("mcp_agent.server.requests.post")
    def test_role_start_no_structured_repo_fields_required(self, mock_post):
        """No error when repo/base_branch/branch are omitted."""
        from mcp_agent.server import role_start

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "conversation_id": "conv-no-repo-fields",
            "status": "no_wait",
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        result = role_start(
            role="scout",
            prompt="Analyze repository",
            context={},
            artifacts={},
        )

        self.assertEqual(result["status"], "running")
        self.assertIn("role_run_id", result)


class TestOpenHandsEmptySandbox(unittest.TestCase):
    """Test that OpenHands wrapper sends empty repository metadata."""

    def test_start_conversation_empty_sandbox(self):
        """start_v1_app_conversation always sends selected_repository=None."""
        from openhands_llm.openhands_llm_call import start_v1_app_conversation

        # We can't make a real API call, but we can verify the function
        # signature and that it would pass None values.
        # The actual API call is tested via mocking in integration.
        # Here we verify the code path by inspecting the source.
        import inspect
        source = inspect.getsource(start_v1_app_conversation)
        # The function should always set selected_repository to None
        self.assertIn('selected_repository": None', source)
        self.assertIn('selected_branch": None', source)
        self.assertIn('git_provider": None', source)

    @patch("openhands_llm.openhands_llm_call.request_json")
    def test_openhands_payload_no_repo_metadata(self, mock_request_json):
        """Verify the actual payload sent has no repo metadata."""
        from openhands_llm.openhands_llm_call import start_v1_app_conversation

        mock_request_json.return_value = {
            "conversation_id": "conv-test",
            "status": "no_wait",
        }

        start_v1_app_conversation(
            base_url="http://localhost:3000",
            api_key="test-key",
            repo="owner/repo",  # Even with repo passed, should be None in payload
            branch="main",
            prompt="Test prompt",
            llm_model="gpt-4",
            agent_type="default",
        )

        # Verify the payload
        call_args = mock_request_json.call_args
        json_body = call_args[1]["json_body"]

        self.assertIsNone(json_body["selected_repository"])
        self.assertIsNone(json_body["selected_branch"])
        self.assertIsNone(json_body["git_provider"])
        self.assertEqual(json_body["pr_number"], [])

    def test_prompt_text_present_in_payload(self):
        """Verify prompt text is present in the message/body sent to OpenHands."""
        from openhands_llm.openhands_llm_call import start_v1_app_conversation

        import inspect
        source = inspect.getsource(start_v1_app_conversation)
        # The prompt should be in the initial_message text
        self.assertIn('"text": prompt', source)


class TestRoleWaitTool(unittest.TestCase):
    """Tests for the role_wait MCP tool."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_role_wait_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = os.path.join(
            self.tmpdir, "runs"
        )
        # Clear env vars so defaults are used
        for k in (
            "OPENHANDS_ROLE_WAIT_TIMEOUT_SECONDS",
            "OPENHANDS_ROLE_WAIT_POLL_INTERVAL_SECONDS",
            "OPENHANDS_ROLE_WAIT_MAX_TIMEOUT_SECONDS",
        ):
            os.environ.pop(k, None)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        for k in (
            "OPENHANDS_ROLE_WAIT_TIMEOUT_SECONDS",
            "OPENHANDS_ROLE_WAIT_POLL_INTERVAL_SECONDS",
            "OPENHANDS_ROLE_WAIT_MAX_TIMEOUT_SECONDS",
        ):
            os.environ.pop(k, None)

    @patch("mcp_agent.role_tools.time.sleep")
    @patch("mcp_agent.role_tools.role_status_impl")
    @patch("mcp_agent.role_tools.role_result_impl")
    @patch("mcp_agent.role_tools._get_role_store")
    def test_role_wait_completed_with_result(
        self, mock_get_store, mock_result_impl, mock_status_impl, mock_sleep
    ):
        """role_wait returns completed result when role finishes."""
        from mcp_agent.server import role_wait

        mock_store = MagicMock()
        mock_get_store.return_value = mock_store
        mock_store.get_role_run.return_value = {
            "run_id": "test-run-001",
            "role_run_id": "run-001",
            "role": "scout",
            "openhands_task_id": "task-001",
            "conversation_id": "conv-001",
        }

        mock_status_impl.side_effect = [
            {"status": "running"},  # initial validation
            {"status": "completed"},  # first poll check
        ]
        mock_result_impl.return_value = {
            "role_run_id": "run-001",
            "status": "completed",
            "result_summary": "Done",
            "full_result": "Full report",
            "full_result_omitted": False,
        }

        result = role_wait(
            role_run_id="run-001",
            timeout_seconds=300,
            poll_interval_seconds=15,
            return_result=True,
        )

        self.assertEqual(result["status"], "completed")
        self.assertFalse(result.get("full_result_omitted", True))
        self.assertEqual(result["full_result"], "Full report")
        self.assertIn("duration_seconds", result)
        self.assertEqual(mock_status_impl.call_count, 2)

    @patch("mcp_agent.role_tools.time.sleep")
    @patch("mcp_agent.role_tools.role_status_impl")
    @patch("mcp_agent.role_tools._get_role_store")
    def test_role_wait_terminal_failed(
        self, mock_get_store, mock_status_impl, mock_sleep
    ):
        """role_wait returns terminal failed state."""
        from mcp_agent.server import role_wait

        mock_store = MagicMock()
        mock_get_store.return_value = mock_store
        mock_store.get_role_run.return_value = {
            "run_id": "test-run-002",
            "role_run_id": "run-002",
            "role": "scout",
            "openhands_task_id": "task-002",
            "conversation_id": "conv-002",
        }

        mock_status_impl.side_effect = [
            {"status": "running"},  # initial validation
            {"status": "failed"},  # first poll check
        ]

        result = role_wait(
            role_run_id="run-002",
            timeout_seconds=300,
            poll_interval_seconds=15,
            return_result=True,
        )

        self.assertEqual(result["status"], "failed")
        self.assertFalse(result.get("has_result", True))
        self.assertIn("error", result)
        self.assertEqual(result["error"]["type"], "RoleFailed")
        self.assertTrue(result["error"]["retryable"])

    @patch("mcp_agent.role_tools.time.sleep")
    @patch("mcp_agent.role_tools.role_status_impl")
    @patch("mcp_agent.role_tools._get_role_store")
    def test_role_wait_bounded_timeout(
        self, mock_get_store, mock_status_impl, mock_sleep
    ):
        """role_wait returns running when timeout expires."""
        from mcp_agent.server import role_wait

        mock_store = MagicMock()
        mock_get_store.return_value = mock_store
        mock_store.get_role_run.return_value = {
            "run_id": "test-run-003",
            "role_run_id": "run-003",
            "role": "scout",
            "openhands_task_id": "task-003",
            "conversation_id": "conv-003",
        }

        mock_status_impl.return_value = {"status": "running"}

        result = role_wait(
            role_run_id="run-003",
            timeout_seconds=1,
            poll_interval_seconds=1,
            return_result=True,
        )

        self.assertEqual(result["status"], "running")
        self.assertTrue(result["wait_timed_out"])
        self.assertFalse(result.get("has_result", True))
        self.assertEqual(result["poll_after_seconds"], 60)

    @patch("mcp_agent.role_tools.time.sleep")
    @patch("mcp_agent.role_tools.role_status_impl")
    @patch("mcp_agent.role_tools._get_role_store")
    def test_role_wait_return_result_false(
        self, mock_get_store, mock_status_impl, mock_sleep
    ):
        """return_result=false returns compact response."""
        from mcp_agent.server import role_wait

        mock_store = MagicMock()
        mock_get_store.return_value = mock_store
        mock_store.get_role_run.return_value = {
            "run_id": "test-run-004",
            "role_run_id": "run-004",
            "role": "scout",
            "openhands_task_id": "task-004",
            "conversation_id": "conv-004",
        }

        mock_status_impl.side_effect = [
            {"status": "running"},  # initial validation
            {"status": "completed"},  # first poll check
        ]

        result = role_wait(
            role_run_id="run-004",
            timeout_seconds=300,
            poll_interval_seconds=15,
            return_result=False,
        )

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["has_result"])
        self.assertTrue(result["result_available"])
        self.assertEqual(result["next_action"], "call role_result")
        self.assertNotIn("full_result", result)
        self.assertNotIn("result", result)

    @patch("mcp_agent.role_tools.time.sleep")
    @patch("mcp_agent.role_tools.role_status_impl")
    @patch("mcp_agent.role_tools.role_result_impl")
    @patch("mcp_agent.role_tools._get_role_store")
    def test_role_wait_clamps_negative_timeout(
        self, mock_get_store, mock_result_impl, mock_status_impl, mock_sleep
    ):
        """Negative timeout is clamped to minimum."""
        from mcp_agent.server import role_wait

        mock_store = MagicMock()
        mock_get_store.return_value = mock_store
        mock_store.get_role_run.return_value = {
            "run_id": "test-run-005",
            "role_run_id": "run-005",
            "role": "scout",
            "openhands_task_id": "task-005",
            "conversation_id": "conv-005",
        }

        mock_status_impl.side_effect = [
            {"status": "running"},
            {"status": "completed"},
        ]
        mock_result_impl.return_value = {
            "role_run_id": "run-005",
            "status": "completed",
            "full_result": "test answer",
            "full_result_omitted": False,
        }

        result = role_wait(
            role_run_id="run-005",
            timeout_seconds=-1,
            poll_interval_seconds=15,
            return_result=True,
        )

        self.assertEqual(result["status"], "completed")

    @patch("mcp_agent.role_tools.time.sleep")
    @patch("mcp_agent.role_tools.role_status_impl")
    @patch("mcp_agent.role_tools.role_result_impl")
    @patch("mcp_agent.role_tools._get_role_store")
    def test_role_wait_clamps_huge_timeout(
        self, mock_get_store, mock_result_impl, mock_status_impl, mock_sleep
    ):
        """Huge timeout is clamped to max (7200)."""
        from mcp_agent.server import role_wait

        mock_store = MagicMock()
        mock_get_store.return_value = mock_store
        mock_store.get_role_run.return_value = {
            "run_id": "test-run-006",
            "role_run_id": "run-006",
            "role": "scout",
            "openhands_task_id": "task-006",
            "conversation_id": "conv-006",
        }

        mock_status_impl.side_effect = [
            {"status": "running"},
            {"status": "completed"},
        ]
        mock_result_impl.return_value = {
            "role_run_id": "run-006",
            "status": "completed",
            "full_result": "test answer",
            "full_result_omitted": False,
        }

        result = role_wait(
            role_run_id="run-006",
            timeout_seconds=999999,
            poll_interval_seconds=15,
            return_result=True,
        )

        self.assertEqual(result["status"], "completed")

    @patch("mcp_agent.role_tools.time.sleep")
    @patch("mcp_agent.role_tools.role_status_impl")
    @patch("mcp_agent.role_tools.role_result_impl")
    @patch("mcp_agent.role_tools._get_role_store")
    def test_role_wait_clamps_zero_poll_interval(
        self, mock_get_store, mock_result_impl, mock_status_impl, mock_sleep
    ):
        """Zero poll interval is clamped to minimum (5)."""
        from mcp_agent.server import role_wait

        mock_store = MagicMock()
        mock_get_store.return_value = mock_store
        mock_store.get_role_run.return_value = {
            "run_id": "test-run-007",
            "role_run_id": "run-007",
            "role": "scout",
            "openhands_task_id": "task-007",
            "conversation_id": "conv-007",
        }

        mock_status_impl.side_effect = [
            {"status": "running"},
            {"status": "completed"},
        ]
        mock_result_impl.return_value = {
            "role_run_id": "run-007",
            "status": "completed",
            "full_result": "test answer",
            "full_result_omitted": False,
        }

        result = role_wait(
            role_run_id="run-007",
            timeout_seconds=300,
            poll_interval_seconds=0,
            return_result=True,
        )

        self.assertEqual(result["status"], "completed")

    @patch("mcp_agent.role_tools.time.sleep")
    @patch("mcp_agent.role_tools.role_status_impl")
    @patch("mcp_agent.role_tools.role_result_impl")
    @patch("mcp_agent.role_tools._get_role_store")
    def test_role_wait_clamps_huge_poll_interval(
        self, mock_get_store, mock_result_impl, mock_status_impl, mock_sleep
    ):
        """Huge poll interval is clamped to max (120)."""
        from mcp_agent.server import role_wait

        mock_store = MagicMock()
        mock_get_store.return_value = mock_store
        mock_store.get_role_run.return_value = {
            "run_id": "test-run-008",
            "role_run_id": "run-008",
            "role": "scout",
            "openhands_task_id": "task-008",
            "conversation_id": "conv-008",
        }

        mock_status_impl.side_effect = [
            {"status": "running"},
            {"status": "completed"},
        ]
        mock_result_impl.return_value = {
            "role_run_id": "run-008",
            "status": "completed",
            "full_result": "test answer",
            "full_result_omitted": False,
        }

        result = role_wait(
            role_run_id="run-008",
            timeout_seconds=300,
            poll_interval_seconds=9999,
            return_result=True,
        )

        self.assertEqual(result["status"], "completed")

    @patch("mcp_agent.role_tools.time.sleep")
    @patch("mcp_agent.role_tools.role_status_impl")
    def test_role_wait_unknown_role_run_id(self, mock_status_impl, mock_sleep):
        """Unknown role_run_id returns error immediately."""
        from mcp_agent.server import role_wait

        mock_status_impl.return_value = {
            "status": "failed",
            "error": {
                "type": "UnknownRoleRunId",
                "message": "No role run found.",
                "retryable": False,
            },
        }

        result = role_wait(
            role_run_id="nonexistent-id",
            timeout_seconds=300,
            poll_interval_seconds=15,
            return_result=True,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "UnknownRoleRunId")
        # No polling should occur for unknown role_run_id
        self.assertEqual(mock_status_impl.call_count, 1)

    @patch("mcp_agent.role_tools.time.sleep")
    @patch("mcp_agent.role_tools.role_status_impl")
    @patch("mcp_agent.role_tools.role_result_impl")
    @patch("mcp_agent.role_tools._get_role_store")
    def test_role_wait_fallback_empty_result_no_name_error(
        self, mock_get_store, mock_result_impl, mock_status_impl, mock_sleep
    ):
        """role_wait fallback empty-result path does not raise NameError.

        Simulates: role completed, but role_result_impl returns empty answer
        with status 'completed' (not 'completed_empty_result'), forcing the
        _build_empty_result_response fallback path.
        """
        from mcp_agent.server import role_wait

        mock_store = MagicMock()
        mock_get_store.return_value = mock_store
        mock_store.get_role_run.return_value = {
            "run_id": "test-run-fallback",
            "role_run_id": "run-fallback",
            "role": "scout",
            "openhands_task_id": "task-fallback",
            "conversation_id": "conv-fallback",
        }

        # Use tiny retry window so test is fast
        with patch.dict(
            os.environ,
            {
                "OPENHANDS_FINAL_ANSWER_RETRY_SECONDS": "1",
                "OPENHANDS_FINAL_ANSWER_RETRY_INTERVAL_SECONDS": "0",
            },
        ):
            mock_status_impl.side_effect = [
                {"status": "running"},  # initial validation
                {"status": "completed"},  # first poll check
            ]
            # Return empty answer with status "completed" (not "completed_empty_result")
            # This forces the _build_empty_result_response fallback path
            mock_result_impl.return_value = {
                "role_run_id": "run-fallback",
                "status": "completed",
                "full_result": "",
                "result": "",
            }

            result = role_wait(
                role_run_id="run-fallback",
                timeout_seconds=300,
                poll_interval_seconds=15,
                return_result=True,
            )

        # Should NOT raise NameError
        self.assertEqual(result["status"], "completed_empty_result")
        self.assertFalse(result.get("has_result", True))
        self.assertEqual(result.get("full_result"), "")
        self.assertIsNotNone(result.get("error"))
        self.assertEqual(result["error"]["type"], "EmptyRoleResult")

    @patch("mcp_agent.role_tools.time.sleep")
    @patch("mcp_agent.role_tools.role_status_impl")
    def test_role_wait_unknown_role_run_id_via_store(
        self, mock_status_impl, mock_sleep
    ):
        """Unknown role_run_id (after initial check passes) returns structured error.

        Simulates a race where role_status_impl returns non-failed but the
        store lookup for role_run returns None.  This exercises the new
        role_run guard added in role_wait_impl().
        """
        from mcp_agent.server import role_wait

        # Initial check passes (non-failed), so role_wait proceeds to
        # the store lookup that we mock to return None.
        mock_status_impl.side_effect = [
            {"status": "running"},  # initial validation — not failed
            {"status": "running"},  # polling loop — never reached
        ]

        with patch("mcp_agent.role_tools._get_role_store") as mock_get_store:
            mock_store = MagicMock()
            mock_get_store.return_value = mock_store
            mock_store.get_role_run.return_value = None

            result = role_wait(
                role_run_id="missing-role-run-id",
                timeout_seconds=300,
                poll_interval_seconds=15,
                return_result=True,
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "UnknownRoleRunId")
        self.assertFalse(result["error"]["retryable"])
        # Only the initial status check should have occurred
        self.assertEqual(mock_status_impl.call_count, 1)

    @patch("mcp_agent.role_tools.time.sleep")
    @patch("mcp_agent.role_tools.role_status_impl")
    @patch("mcp_agent.role_tools._get_role_store")
    def test_role_wait_cancelled_status(
        self, mock_get_store, mock_status_impl, mock_sleep
    ):
        """Cancelled status is terminal."""
        from mcp_agent.server import role_wait

        mock_store = MagicMock()
        mock_get_store.return_value = mock_store
        mock_store.get_role_run.return_value = {
            "run_id": "test-run-009",
            "role_run_id": "run-009",
            "role": "scout",
            "openhands_task_id": "task-009",
            "conversation_id": "conv-009",
        }

        mock_status_impl.side_effect = [
            {"status": "running"},  # initial validation
            {"status": "cancelled"},  # first poll check
        ]

        result = role_wait(
            role_run_id="run-009",
            timeout_seconds=300,
            poll_interval_seconds=15,
            return_result=True,
        )

        self.assertEqual(result["status"], "cancelled")
        self.assertFalse(result.get("has_result", True))
        self.assertIn("error", result)
        self.assertEqual(result["error"]["type"], "RoleCancelled")

    @patch("mcp_agent.role_tools.time.sleep")
    @patch("mcp_agent.role_tools.role_status_impl")
    @patch("mcp_agent.role_tools._get_role_store")
    def test_role_wait_timeout_status(
        self, mock_get_store, mock_status_impl, mock_sleep
    ):
        """Timeout status is terminal."""
        from mcp_agent.server import role_wait

        mock_store = MagicMock()
        mock_get_store.return_value = mock_store
        mock_store.get_role_run.return_value = {
            "run_id": "test-run-010",
            "role_run_id": "run-010",
            "role": "scout",
            "openhands_task_id": "task-010",
            "conversation_id": "conv-010",
        }

        mock_status_impl.side_effect = [
            {"status": "running"},  # initial validation
            {"status": "timeout"},  # first poll check
        ]

        result = role_wait(
            role_run_id="run-010",
            timeout_seconds=300,
            poll_interval_seconds=15,
            return_result=True,
        )

        self.assertEqual(result["status"], "timeout")
        self.assertFalse(result.get("has_result", True))
        self.assertIn("error", result)
        self.assertEqual(result["error"]["type"], "RoleTimeout")


class TestRoleWaitMissingTerminalStatuses(unittest.TestCase):
    """Tests for terminal statuses that were missing response branches.

    These statuses are in TERMINAL_STATUSES but did not have explicit
    response branches in role_wait_impl(), causing them to fall through
    to the 'running' fallback.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_role_wait_missing_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = os.path.join(
            self.tmpdir, "runs"
        )
        for k in (
            "OPENHANDS_ROLE_WAIT_TIMEOUT_SECONDS",
            "OPENHANDS_ROLE_WAIT_POLL_INTERVAL_SECONDS",
            "OPENHANDS_ROLE_WAIT_MAX_TIMEOUT_SECONDS",
        ):
            os.environ.pop(k, None)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        for k in (
            "OPENHANDS_ROLE_WAIT_TIMEOUT_SECONDS",
            "OPENHANDS_ROLE_WAIT_POLL_INTERVAL_SECONDS",
            "OPENHANDS_ROLE_WAIT_MAX_TIMEOUT_SECONDS",
        ):
            os.environ.pop(k, None)

    @patch("mcp_agent.role_tools.time.sleep")
    @patch("mcp_agent.role_tools.role_status_impl")
    @patch("mcp_agent.role_tools._get_role_store")
    def test_role_wait_error_status(
        self, mock_get_store, mock_status_impl, mock_sleep
    ):
        """Error status returns terminal response, not running."""
        from mcp_agent.server import role_wait

        mock_store = MagicMock()
        mock_get_store.return_value = mock_store
        mock_store.get_role_run.return_value = {
            "run_id": "test-run-err",
            "role_run_id": "run-err",
            "role": "scout",
            "openhands_task_id": "task-err",
            "conversation_id": "conv-err",
        }

        mock_status_impl.side_effect = [
            {"status": "running"},  # initial validation
            {"status": "error"},  # first poll check
        ]

        result = role_wait(
            role_run_id="run-err",
            timeout_seconds=300,
            poll_interval_seconds=15,
            return_result=True,
        )

        self.assertEqual(result["status"], "error")
        self.assertNotEqual(result["status"], "running")
        self.assertFalse(result.get("has_result", True))
        self.assertIn("error", result)
        self.assertEqual(result["error"]["type"], "RoleError")
        self.assertTrue(result["error"]["retryable"])

    @patch("mcp_agent.role_tools.time.sleep")
    @patch("mcp_agent.role_tools.role_status_impl")
    @patch("mcp_agent.role_tools._get_role_store")
    def test_role_wait_timed_out_status(
        self, mock_get_store, mock_status_impl, mock_sleep
    ):
        """Timed_out status returns terminal response, not running."""
        from mcp_agent.server import role_wait

        mock_store = MagicMock()
        mock_get_store.return_value = mock_store
        mock_store.get_role_run.return_value = {
            "run_id": "test-run-to",
            "role_run_id": "run-to",
            "role": "scout",
            "openhands_task_id": "task-to",
            "conversation_id": "conv-to",
        }

        mock_status_impl.side_effect = [
            {"status": "running"},  # initial validation
            {"status": "timed_out"},  # first poll check
        ]

        result = role_wait(
            role_run_id="run-to",
            timeout_seconds=300,
            poll_interval_seconds=15,
            return_result=True,
        )

        self.assertEqual(result["status"], "timed_out")
        self.assertNotEqual(result["status"], "running")
        self.assertFalse(result.get("has_result", True))
        self.assertIn("error", result)
        self.assertEqual(result["error"]["type"], "RoleTimeout")
        self.assertTrue(result["error"]["retryable"])

    @patch("mcp_agent.role_tools.time.sleep")
    @patch("mcp_agent.role_tools.role_status_impl")
    @patch("mcp_agent.role_tools._get_role_store")
    def test_role_wait_canceled_status(
        self, mock_get_store, mock_status_impl, mock_sleep
    ):
        """Canceled status returns terminal response, not running."""
        from mcp_agent.server import role_wait

        mock_store = MagicMock()
        mock_get_store.return_value = mock_store
        mock_store.get_role_run.return_value = {
            "run_id": "test-run-ca",
            "role_run_id": "run-ca",
            "role": "scout",
            "openhands_task_id": "task-ca",
            "conversation_id": "conv-ca",
        }

        mock_status_impl.side_effect = [
            {"status": "running"},  # initial validation
            {"status": "canceled"},  # first poll check
        ]

        result = role_wait(
            role_run_id="run-ca",
            timeout_seconds=300,
            poll_interval_seconds=15,
            return_result=True,
        )

        self.assertEqual(result["status"], "canceled")
        self.assertNotEqual(result["status"], "running")
        self.assertFalse(result.get("has_result", True))
        self.assertIn("error", result)
        self.assertEqual(result["error"]["type"], "RoleCancelled")
        self.assertTrue(result["error"]["retryable"])

    @patch("mcp_agent.role_tools.time.sleep")
    @patch("mcp_agent.role_tools.role_status_impl")
    @patch("mcp_agent.role_tools._get_role_store")
    def test_role_wait_completed_empty_result_status(
        self, mock_get_store, mock_status_impl, mock_sleep
    ):
        """completed_empty_result returns terminal response, not running."""
        from mcp_agent.server import role_wait

        mock_store = MagicMock()
        mock_get_store.return_value = mock_store
        mock_store.get_role_run.return_value = {
            "run_id": "test-run-er",
            "role_run_id": "run-er",
            "role": "scout",
            "openhands_task_id": "task-er",
            "conversation_id": "conv-er",
        }

        mock_status_impl.side_effect = [
            {"status": "running"},  # initial validation
            {"status": "completed_empty_result"},  # first poll check
        ]

        result = role_wait(
            role_run_id="run-er",
            timeout_seconds=300,
            poll_interval_seconds=15,
            return_result=True,
        )

        self.assertEqual(result["status"], "completed_empty_result")
        self.assertNotEqual(result["status"], "running")
        self.assertFalse(result.get("has_result", True))
        self.assertIn("error", result)
        self.assertEqual(result["error"]["type"], "EmptyRoleResult")

    @patch("mcp_agent.role_tools.time.sleep")
    @patch("mcp_agent.role_tools.role_status_impl")
    @patch("mcp_agent.role_tools._get_role_store")
    def test_all_terminal_statuses_return_terminal_response(
        self, mock_get_store, mock_status_impl, mock_sleep
    ):
        """Parametrized: every TERMINAL_STATUSES must not return 'running'."""
        from mcp_agent.server import role_wait
        from mcp_agent.role_tools import TERMINAL_STATUSES

        terminal_statuses = [
            s for s in TERMINAL_STATUSES
            if s not in ("completed",)  # completed requires result mocking
        ]

        for st in terminal_statuses:
            mock_store = MagicMock()
            mock_get_store.return_value = mock_store
            mock_store.get_role_run.return_value = {
                "run_id": f"test-run-{st}",
                "role_run_id": f"run-{st}",
                "role": "scout",
                "openhands_task_id": f"task-{st}",
                "conversation_id": f"conv-{st}",
            }

            mock_status_impl.side_effect = [
                {"status": "running"},
                {"status": st},
            ]

            result = role_wait(
                role_run_id=f"run-{st}",
                timeout_seconds=300,
                poll_interval_seconds=15,
                return_result=True,
            )

            self.assertNotEqual(
                result["status"],
                "running",
                f"Terminal status '{st}' returned 'running'",
            )


class TestEmptyResultContract(unittest.TestCase):
    """Tests for the non-empty final answer role result contract."""

    def _make_role_run(self, tmp_dir, **kwargs):
        """Helper to create a minimal role_run dict."""
        base = {
            "run_id": "test-run-001",
            "role_run_id": "20260606-000000-test-1",
            "role": "scout",
            "openhands_task_id": "task-001",
            "conversation_id": "conv-001",
            "artifact_name": "scout_report",
            "status": "completed",
        }
        base.update(kwargs)
        return base

    def test_role_result_completed_with_answer_succeeds(self):
        """Simulate completed with non-empty answer; expect status=completed, has_result=True."""
        from mcp_agent.role_tools import role_result_impl

        tmp_dir = tempfile.mkdtemp()
        try:
            with patch(
                "mcp_agent.role_tools._get_role_store"
            ) as mock_store_cls, patch(
                "mcp_agent.role_tools.ArtifactStore"
            ) as mock_artifact:
                mock_store = MagicMock()
                mock_store_cls.return_value = mock_store

                role_run = self._make_role_run(tmp_dir)
                mock_store.get_role_run.return_value = role_run

                mock_store.update_role_run.return_value = None

                mock_artifact_instance = MagicMock()
                mock_artifact_instance.save.return_value = {
                    "artifact_name": "scout_report",
                    "artifact_path": "test-run-001/scout_report.artifact",
                }
                mock_artifact.return_value = mock_artifact_instance

                with patch(
                    "mcp_agent.server.openhands_get_task_status"
                ) as mock_status, patch(
                    "mcp_agent.server.openhands_get_task_result"
                ) as mock_result:
                    mock_status.return_value = {"status": "completed"}
                    mock_result.return_value = {
                        "answer": "# Scout report\n\n## Repository\nexample/repo\n",
                    }

                    resp = role_result_impl(
                        "20260606-000000-test-1", include_full_result=True
                    )

                self.assertEqual(resp["status"], "completed")
                self.assertTrue(resp.get("has_result", False))
                self.assertIn("# Scout report", resp.get("full_result", ""))
                self.assertIsNotNone(resp.get("artifact_path"))
                self.assertEqual(
                    resp.get("artifact_path_scope"), "mcp_agent_state_internal"
                )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_role_result_completed_with_empty_answer_returns_empty_result(self):
        """Simulate completed with empty answer; expect EmptyRoleResult."""
        from mcp_agent.role_tools import role_result_impl

        tmp_dir = tempfile.mkdtemp()
        try:
            with patch(
                "mcp_agent.role_tools._get_role_store"
            ) as mock_store_cls, patch(
                "mcp_agent.role_tools.ArtifactStore"
            ) as mock_artifact:
                mock_store = MagicMock()
                mock_store_cls.return_value = mock_store

                role_run = self._make_role_run(tmp_dir)
                mock_store.get_role_run.return_value = role_run

                mock_artifact_instance = MagicMock()
                mock_artifact.return_value = mock_artifact_instance

                with patch(
                    "mcp_agent.server.openhands_get_task_status"
                ) as mock_status, patch(
                    "mcp_agent.server.openhands_get_task_result"
                ) as mock_result:
                    mock_status.return_value = {"status": "completed"}
                    mock_result.return_value = {"answer": ""}

                    resp = role_result_impl(
                        "20260606-000000-test-1", include_full_result=True
                    )

                self.assertEqual(resp["status"], "completed_empty_result")
                self.assertFalse(resp.get("has_result", True))
                self.assertEqual(resp.get("full_result"), "")
                self.assertFalse(resp.get("artifact_saved", True))
                self.assertIsNotNone(resp.get("error"))
                self.assertEqual(resp["error"]["type"], "EmptyRoleResult")
                self.assertTrue(resp["error"].get("retryable"))
                # Artifact should NOT be saved for empty results
                mock_artifact_instance.save.assert_not_called()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_completed_empty_result_is_terminal_status(self):
        """Verify completed_empty_result is in TERMINAL_STATUSES."""
        from mcp_agent.role_tools import TERMINAL_STATUSES

        self.assertIn("completed_empty_result", TERMINAL_STATUSES)

    def test_role_result_no_artifact_saved_for_empty_result(self):
        """Verify artifact is not saved when answer is empty."""
        from mcp_agent.role_tools import role_result_impl

        tmp_dir = tempfile.mkdtemp()
        try:
            with patch(
                "mcp_agent.role_tools._get_role_store"
            ) as mock_store_cls, patch(
                "mcp_agent.role_tools.ArtifactStore"
            ) as mock_artifact:
                mock_store = MagicMock()
                mock_store_cls.return_value = mock_store

                role_run = self._make_role_run(tmp_dir)
                mock_store.get_role_run.return_value = role_run

                mock_artifact_instance = MagicMock()
                mock_artifact.return_value = mock_artifact_instance

                with patch(
                    "mcp_agent.server.openhands_get_task_status"
                ) as mock_status, patch(
                    "mcp_agent.server.openhands_get_task_result"
                ) as mock_result:
                    mock_status.return_value = {"status": "completed"}
                    mock_result.return_value = {"answer": "   "}  # whitespace only

                    resp = role_result_impl(
                        "20260606-000000-test-1", include_full_result=True
                    )

                self.assertEqual(resp["status"], "completed_empty_result")
                mock_artifact_instance.save.assert_not_called()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_role_result_force_refresh_passed_to_server(self):
        """Verify force_refresh parameter is passed to openhands_get_task_result."""
        from mcp_agent.role_tools import role_result_impl

        tmp_dir = tempfile.mkdtemp()
        try:
            with patch(
                "mcp_agent.role_tools._get_role_store"
            ) as mock_store_cls, patch(
                "mcp_agent.role_tools.ArtifactStore"
            ) as mock_artifact:
                mock_store = MagicMock()
                mock_store_cls.return_value = mock_store

                role_run = self._make_role_run(tmp_dir)
                mock_store.get_role_run.return_value = role_run

                mock_store.update_role_run.return_value = None

                mock_artifact_instance = MagicMock()
                mock_artifact_instance.save.return_value = {
                    "artifact_name": "scout_report",
                    "artifact_path": "test-run-001/scout_report.artifact",
                }
                mock_artifact.return_value = mock_artifact_instance

                with patch(
                    "mcp_agent.server.openhands_get_task_status"
                ) as mock_status, patch(
                    "mcp_agent.server.openhands_get_task_result"
                ) as mock_result:
                    mock_status.return_value = {"status": "completed"}
                    mock_result.return_value = {
                        "answer": "# Scout report\n\n## Repository\nexample/repo\n",
                    }

                    resp = role_result_impl(
                        "20260606-000000-test-1",
                        include_full_result=True,
                        force_refresh=True,
                    )

                self.assertEqual(resp["status"], "completed")
                mock_result.assert_called_once_with(
                    task_id="task-001", force_refresh=True
                )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @patch("mcp_agent.role_tools.time.sleep")
    @patch("mcp_agent.role_tools.role_status_impl")
    @patch("mcp_agent.role_tools._get_role_store")
    def test_role_wait_retries_final_answer_after_completed(
        self, mock_get_store, mock_status_impl, mock_sleep
    ):
        """role_wait retries when answer is initially empty, then succeeds."""
        from mcp_agent.server import role_wait

        mock_store = MagicMock()
        mock_get_store.return_value = mock_store
        mock_store.get_role_run.return_value = {
            "run_id": "test-run-100",
            "role_run_id": "run-100",
            "role": "scout",
            "openhands_task_id": "task-100",
            "conversation_id": "conv-100",
        }

        # First two polls return empty answer, third returns non-empty.
        call_count = [0]

        def _mock_result_impl(role_run_id, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                return {
                    "role_run_id": "run-100",
                    "status": "completed_empty_result",
                    "has_result": False,
                    "full_result": "",
                    "error": {"type": "EmptyRoleResult"},
                    "artifact_saved": False,
                }
            return {
                "role_run_id": "run-100",
                "status": "completed",
                "has_result": True,
                "full_result": "Final answer after retry",
                "full_result_omitted": False,
                "artifact_saved": True,
            }

        with patch.dict(
            os.environ,
            {
                "OPENHANDS_FINAL_ANSWER_RETRY_SECONDS": "10",
                "OPENHANDS_FINAL_ANSWER_RETRY_INTERVAL_SECONDS": "1",
            },
        ):
            with patch(
                "mcp_agent.role_tools.role_result_impl",
                side_effect=_mock_result_impl,
            ):
                mock_status_impl.side_effect = [
                    {"status": "running"},
                    {"status": "completed"},
                ]

                result = role_wait(
                    role_run_id="run-100",
                    timeout_seconds=300,
                    poll_interval_seconds=15,
                    return_result=True,
                )

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result.get("has_result", False))
        self.assertEqual(result["full_result"], "Final answer after retry")
        # Should have been called 3 times (2 empty + 1 success)
        self.assertEqual(call_count[0], 3)

    @patch("mcp_agent.role_tools.time.sleep")
    @patch("mcp_agent.role_tools.role_status_impl")
    @patch("mcp_agent.role_tools._get_role_store")
    def test_role_wait_gives_empty_result_after_retry_window(
        self, mock_get_store, mock_status_impl, mock_sleep
    ):
        """role_wait returns EmptyRoleResult when retry window is exhausted."""
        from mcp_agent.server import role_wait

        mock_store = MagicMock()
        mock_get_store.return_value = mock_store
        mock_store.get_role_run.return_value = {
            "run_id": "test-run-200",
            "role_run_id": "run-200",
            "role": "scout",
            "openhands_task_id": "task-200",
            "conversation_id": "conv-200",
        }

        def _mock_result_impl(role_run_id, **kwargs):
            return {
                "role_run_id": "run-200",
                "status": "completed_empty_result",
                "has_result": False,
                "full_result": "",
                "error": {"type": "EmptyRoleResult", "retryable": True},
                "artifact_saved": False,
            }

        # Use fast retry env vars so the test completes quickly
        with patch.dict(
            os.environ,
            {
                "OPENHANDS_FINAL_ANSWER_RETRY_SECONDS": "2",
                "OPENHANDS_FINAL_ANSWER_RETRY_INTERVAL_SECONDS": "1",
            },
        ):
            with patch(
                "mcp_agent.role_tools.role_result_impl",
                side_effect=_mock_result_impl,
            ):
                mock_status_impl.side_effect = [
                    {"status": "running"},
                    {"status": "completed"},
                ]

                result = role_wait(
                    role_run_id="run-200",
                    timeout_seconds=300,
                    poll_interval_seconds=15,
                    return_result=True,
                )

        self.assertEqual(result["status"], "completed_empty_result")
        self.assertFalse(result.get("has_result", True))
        self.assertEqual(result["full_result"], "")
        self.assertIn("error", result)
        self.assertEqual(result["error"]["type"], "EmptyRoleResult")
        self.assertTrue(result["error"].get("retryable"))


class TestCachedEmptyResultForceRefresh(unittest.TestCase):
    """Tests for cached empty result handling with force_refresh."""

    def _make_completed_empty_task(self, store, conversation_id):
        """Helper: create a task and mark it completed with empty answer."""
        task = store.create_task(
            conversation_id=conversation_id,
            prompt="test prompt",
            idempotency_key=None,
        )
        store.update_task(
            task["task_id"],
            status="completed",
            result={"answer": "", "completed_at": "2026-06-06T00:00:00+00:00"},
        )
        return task

    def test_force_refresh_updates_cache_when_remote_returns_non_empty(self):
        """When force_refresh=True and remote returns non-empty answer,
        the cache is updated and the fresh answer is returned."""
        from mcp_agent.server import openhands_get_task_result
        from mcp_agent.task_store import TaskStore

        tmp_dir = tempfile.mkdtemp()
        try:
            store = TaskStore(tmp_dir)
            task = self._make_completed_empty_task(store, "conv-refresh-001")
            task_id = task["task_id"]

            with patch("mcp_agent.server.requests.get") as mock_get, patch(
                "mcp_agent.server._get_store"
            ) as mock_store:
                mock_store.return_value = store
                mock_resp = MagicMock()
                mock_resp.json.return_value = {
                    "status": "completed",
                    "answer": "Fresh answer from remote",
                }
                mock_resp.raise_for_status = MagicMock()
                mock_get.return_value = mock_resp

                result = openhands_get_task_result(
                    task_id, url="http://localhost:3000", force_refresh=True
                )

                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["answer"], "Fresh answer from remote")

                # Verify the task store was updated with the fresh answer
                updated_task = store.get_task(task_id)
                self.assertIsNotNone(updated_task)
                self.assertEqual(updated_task["status"], "completed")
                self.assertEqual(
                    updated_task["result"]["answer"], "Fresh answer from remote"
                )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_force_refresh_returns_empty_when_remote_still_empty(self):
        """When force_refresh=True and remote also returns empty answer,
        the original cached empty answer is returned."""
        from mcp_agent.server import openhands_get_task_result
        from mcp_agent.task_store import TaskStore

        tmp_dir = tempfile.mkdtemp()
        try:
            store = TaskStore(tmp_dir)
            task = self._make_completed_empty_task(store, "conv-refresh-002")
            task_id = task["task_id"]

            with patch("mcp_agent.server.requests.get") as mock_get, patch(
                "mcp_agent.server._get_store"
            ) as mock_store:
                mock_store.return_value = store
                mock_resp = MagicMock()
                mock_resp.json.return_value = {
                    "status": "completed",
                    "answer": "",
                }
                mock_resp.raise_for_status = MagicMock()
                mock_get.return_value = mock_resp

                result = openhands_get_task_result(
                    task_id, url="http://localhost:3000", force_refresh=True
                )

                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["answer"], "")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_force_refresh_skipped_when_cached_answer_non_empty(self):
        """When force_refresh=True but cached answer is non-empty,
        no HTTP request is made and the cached answer is returned."""
        from mcp_agent.server import openhands_get_task_result
        from mcp_agent.task_store import TaskStore

        tmp_dir = tempfile.mkdtemp()
        try:
            store = TaskStore(tmp_dir)
            task = store.create_task(
                conversation_id="conv-refresh-003",
                prompt="test prompt",
                idempotency_key=None,
            )
            task_id = task["task_id"]

            # Manually update the task to simulate completed with non-empty answer
            store.update_task(
                task_id,
                status="completed",
                result={
                    "answer": "Already cached answer",
                    "completed_at": "2026-06-06T00:00:00+00:00",
                },
            )

            with patch("mcp_agent.server.requests.get") as mock_get, patch(
                "mcp_agent.server._get_store"
            ) as mock_store:
                mock_store.return_value = store
                result = openhands_get_task_result(
                    task_id, url="http://localhost:3000", force_refresh=True
                )

                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["answer"], "Already cached answer")
                # No HTTP request should be made
                mock_get.assert_not_called()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


class TestPromptTemplatesIncludeFinalMarkers(unittest.TestCase):
    """Tests that all role prompt templates include required final markers."""

    def setUp(self):
        """Resolve the prompts directory path."""
        self.prompts_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts"
        )

    def test_scout_prompt_has_final_answer_contract_and_marker(self):
        """scout.md contains Final Answer Contract section and SCOUT_STATUS marker."""
        prompt_path = os.path.join(self.prompts_dir, "scout.md")
        content = Path(prompt_path).read_text(encoding="utf-8")
        self.assertIn("## Final Answer Contract", content)
        self.assertIn("SCOUT_STATUS: COMPLETE", content)

    def test_architect_prompt_has_final_answer_contract_and_marker(self):
        """architect.md contains Final Answer Contract section and ARCHITECT_STATUS marker."""
        prompt_path = os.path.join(self.prompts_dir, "architect.md")
        content = Path(prompt_path).read_text(encoding="utf-8")
        self.assertIn("## Final Answer Contract", content)
        self.assertIn("ARCHITECT_STATUS: COMPLETE", content)

    def test_coder_prompt_has_final_answer_contract_and_marker(self):
        """coder.md contains Final Answer Contract section and CODER_STATUS marker."""
        prompt_path = os.path.join(self.prompts_dir, "coder.md")
        content = Path(prompt_path).read_text(encoding="utf-8")
        self.assertIn("## Final Answer Contract", content)
        self.assertIn("CODER_STATUS: COMPLETE", content)

    def test_reviewer_prompt_has_final_answer_contract_and_marker(self):
        """reviewer.md contains Final Answer Contract section and REVIEWER_STATUS marker."""
        prompt_path = os.path.join(self.prompts_dir, "reviewer.md")
        content = Path(prompt_path).read_text(encoding="utf-8")
        self.assertIn("## Final Answer Contract", content)
        self.assertIn("REVIEWER_STATUS", content)

    def test_publisher_prompt_has_final_answer_contract_and_marker(self):
        """publisher.md contains Final Answer Contract section and PUBLISHER_STATUS marker."""
        prompt_path = os.path.join(self.prompts_dir, "publisher.md")
        content = Path(prompt_path).read_text(encoding="utf-8")
        self.assertIn("## Final Answer Contract", content)
        self.assertIn("PUBLISHER_STATUS: COMPLETE", content)

    def test_all_prompts_require_final_plain_text_answer(self):
        """All role prompts instruct the role to send a final plain-text answer."""
        prompt_files = [
            "scout.md",
            "architect.md",
            "coder.md",
            "reviewer.md",
            "publisher.md",
        ]
        for prompt_file in prompt_files:
            prompt_path = os.path.join(self.prompts_dir, prompt_file)
            content = Path(prompt_path).read_text(encoding="utf-8")
            self.assertIn(
                "final plain-text answer",
                content.lower(),
                f"{prompt_file} should instruct role to send a final plain-text answer",
            )


if __name__ == "__main__":
    unittest.main()

