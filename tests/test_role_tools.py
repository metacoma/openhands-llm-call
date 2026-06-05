#!/usr/bin/env python3
"""Tests for role tools (mcp_agent.role_tools)."""

import json
import os
import shutil
import tempfile
import unittest
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

    def test_role_list_returns_five_roles(self):
        """role_list returns five roles."""
        from mcp_agent.server import role_list

        result = role_list()
        self.assertIn("roles", result)
        self.assertEqual(len(result["roles"]), 5)

    def test_role_list_contains_scout(self):
        """role_list includes scout role."""
        from mcp_agent.server import role_list

        result = role_list()
        names = [r["name"] for r in result["roles"]]
        self.assertIn("scout", names)

    def test_role_list_contains_architect(self):
        """role_list includes architect role."""
        from mcp_agent.server import role_list

        result = role_list()
        names = [r["name"] for r in result["roles"]]
        self.assertIn("architect", names)

    def test_role_list_contains_coder(self):
        """role_list includes coder role."""
        from mcp_agent.server import role_list

        result = role_list()
        names = [r["name"] for r in result["roles"]]
        self.assertIn("coder", names)

    def test_role_list_contains_reviewer(self):
        """role_list includes reviewer role."""
        from mcp_agent.server import role_list

        result = role_list()
        names = [r["name"] for r in result["roles"]]
        self.assertIn("reviewer", names)

    def test_role_list_contains_publisher(self):
        """role_list includes publisher role."""
        from mcp_agent.server import role_list

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


if __name__ == "__main__":
    unittest.main()
