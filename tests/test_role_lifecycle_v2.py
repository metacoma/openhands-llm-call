#!/usr/bin/env python3
"""Tests for two-step role lifecycle (mcp_agent.role_lifecycle)."""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch


class TestRoleLifecycleValidation(unittest.TestCase):
    """Test v2 role start validation."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_lifecycle_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = os.path.join(
            self.tmpdir, "runs"
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        # Reset module state
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None

    def _mock_both(self, summary_answer=None):
        """Create mocks for both _start_conversation_on_fastapi and _poll_task_status."""
        mock_start = MagicMock(return_value={
            "task_id": "task-001",
            "conversation_id": "conv-001",
        })

        def poll_side_effect(task_id, url=None, max_polls=None):
            return {
                "status": "completed",
                "answer": summary_answer or json.dumps({
                    "status": "completed",
                    "role": "scout",
                    "summary": "Scout completed.",
                    "primary_artifact_name": "scout_report",
                    "blocking": False,
                    "risk_level": "LOW",
                    "action": None,
                    "blocking_summary": [],
                }),
            }

        mock_poll = MagicMock(side_effect=poll_side_effect)
        return mock_start, mock_poll

    @patch("mcp_agent.role_lifecycle._poll_task_status")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_scout_start_with_user_task_succeeds(self, mock_start, mock_poll):
        """Starting scout with only user_task succeeds."""
        from mcp_agent.role_lifecycle import start_role_v2_impl

        mock_start.return_value = {
            "task_id": "task-001",
            "conversation_id": "conv-001",
        }
        mock_poll.return_value = {
            "status": "completed",
            "answer": json.dumps({
                "status": "completed",
                "role": "scout",
                "summary": "Scout completed.",
                "primary_artifact_name": "scout_report",
                "blocking": False,
                "risk_level": "LOW",
                "action": None,
                "blocking_summary": [],
            }),
        }

        result = start_role_v2_impl(
            role="scout",
            user_task="Investigate the repo",
            api_key="test-key",
        )

        self.assertEqual(result["status"], "completed")
        self.assertIn("control_summary", result)
        self.assertIn("artifacts", result)
        self.assertIn("primary", result["artifacts"])
        self.assertIn("summary", result["artifacts"])

    @patch("mcp_agent.role_lifecycle._poll_task_status")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_architect_start_with_scout_report_succeeds(self, mock_start, mock_poll):
        """Starting architect with valid scout_report succeeds."""
        from mcp_agent.role_lifecycle import start_role_v2_impl
        from mcp_agent.artifact_store import ArtifactStore

        # Create a scout_report artifact in the store so resolution succeeds
        store = ArtifactStore()
        store.save(
            run_id="test-run-001",
            role_run_id="test-run-001-scout-1",
            role="scout",
            artifact_name="scout_report",
            content="# Scout Report\n\nRepository analyzed.",
        )

        mock_start.return_value = {
            "task_id": "task-002",
            "conversation_id": "conv-002",
        }
        mock_poll.return_value = {
            "status": "completed",
            "answer": json.dumps({
                "status": "completed",
                "role": "architect",
                "summary": "Architect completed.",
                "primary_artifact_name": "architect_plan",
                "blocking": False,
                "risk_level": "LOW",
                "action": None,
                "blocking_summary": [],
            }),
        }

        result = start_role_v2_impl(
            role="architect",
            user_task="Plan implementation",
            input_artifacts={
                "scout_report": "test-run-001/test-run-001-scout-1_scout_report.artifact",
            },
            api_key="test-key",
        )

        self.assertEqual(result["status"], "completed")
        self.assertIn("control_summary", result)

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_architect_start_without_scout_report_fails(self, mock_start):
        """Starting architect without scout_report fails before role start."""
        from mcp_agent.role_lifecycle import start_role_v2_impl

        result = start_role_v2_impl(
            role="architect",
            user_task="Plan implementation",
            input_artifacts={},
            api_key="test-key",
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "MissingRequiredArtifact")
        self.assertIn("scout_report", result["error"]["message"])

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_coder_start_without_architect_plan_fails(self, mock_start):
        """Starting coder without architect_plan fails before role start."""
        from mcp_agent.role_lifecycle import start_role_v2_impl

        result = start_role_v2_impl(
            role="coder",
            user_task="Implement feature",
            input_artifacts={
                "scout_report": "scout_report_path",
            },
            api_key="test-key",
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "MissingRequiredArtifact")
        self.assertIn("architect_plan", result["error"]["message"])

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_reviewer_start_without_coder_report_fails(self, mock_start):
        """Starting reviewer without coder_report fails before role start."""
        from mcp_agent.role_lifecycle import start_role_v2_impl

        result = start_role_v2_impl(
            role="reviewer",
            user_task="Review implementation",
            input_artifacts={
                "scout_report": "scout_report_path",
                "architect_plan": "architect_plan_path",
            },
            api_key="test-key",
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "MissingRequiredArtifact")
        self.assertIn("coder_report", result["error"]["message"])

    def test_unknown_role_fails(self):
        """Starting with unknown role fails."""
        from mcp_agent.role_lifecycle import start_role_v2_impl

        result = start_role_v2_impl(
            role="unknown_role",
            user_task="Test task",
            api_key="test-key",
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "UnknownRole")
        self.assertIn("unknown role", result["error"]["message"])

    def test_missing_user_task_fails(self):
        """Starting any role without user_task fails."""
        from mcp_agent.role_lifecycle import start_role_v2_impl

        result = start_role_v2_impl(
            role="scout",
            user_task="",
            api_key="test-key",
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "MissingUserTask")
        self.assertEqual(result["error"]["message"], "user_task is required")

    def test_empty_user_task_fails(self):
        """Starting with whitespace-only user_task fails."""
        from mcp_agent.role_lifecycle import start_role_v2_impl

        result = start_role_v2_impl(
            role="scout",
            user_task="   ",
            api_key="test-key",
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "MissingUserTask")

    @patch("mcp_agent.role_lifecycle._poll_task_status")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_coder_fix_role_exists(self, mock_start, mock_poll):
        """coder_fix role can be started."""
        from mcp_agent.role_lifecycle import start_role_v2_impl
        from mcp_agent.artifact_store import ArtifactStore

        # Create required artifacts in the store so resolution succeeds
        store = ArtifactStore()
        for art_name, content in [
            ("architect_plan", "# Architect Plan\n\nPlan content."),
            ("coder_report", "# Coder Report\n\nImplementation done."),
            ("reviewer_report", "# Reviewer Report\n\nReview passed."),
        ]:
            store.save(
                run_id="test-run-cf",
                role_run_id=f"test-run-cf-{art_name}-1",
                role=art_name.split("_")[0],
                artifact_name=art_name,
                content=content,
            )

        mock_start.return_value = {
            "task_id": "task-003",
            "conversation_id": "conv-003",
        }
        mock_poll.return_value = {
            "status": "completed",
            "answer": json.dumps({
                "status": "completed",
                "role": "coder_fix",
                "summary": "Coder fix completed.",
                "primary_artifact_name": "coder_fix_result",
                "blocking": False,
                "risk_level": "LOW",
                "action": None,
                "blocking_summary": [],
            }),
        }

        result = start_role_v2_impl(
            role="coder_fix",
            user_task="Fix blockers",
            input_artifacts={
                "architect_plan": "test-run-cf/test-run-cf-architect_plan-1_architect_plan.artifact",
                "coder_report": "test-run-cf/test-run-cf-coder_report-1_coder_report.artifact",
                "reviewer_report": "test-run-cf/test-run-cf-reviewer_report-1_reviewer_report.artifact",
            },
            api_key="test-key",
        )

        self.assertEqual(result["status"], "completed")
        self.assertIn("control_summary", result)


class TestRoleLifecycleLifecycleState(unittest.TestCase):
    """Test lifecycle state transitions."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_lifecycle_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = os.path.join(
            self.tmpdir, "runs"
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None

    @patch("mcp_agent.role_lifecycle._poll_task_status")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_main_response_only_does_not_complete(self, mock_start, mock_poll):
        """Role run is not completed after main response only."""
        from mcp_agent.role_lifecycle import start_role_v2_impl
        from mcp_agent.role_store import RoleRunStore

        call_count = [0]

        def start_side_effect(*args, **kwargs):
            call_count[0] += 1
            return {
                "task_id": f"task-{call_count[0]}",
                "conversation_id": "conv-main",
            }

        mock_start.side_effect = start_side_effect

        def poll_side_effect(task_id, url=None, max_polls=None):
            return {
                "status": "completed",
                "answer": json.dumps({
                    "status": "completed",
                    "role": "scout",
                    "summary": "Scout completed.",
                    "primary_artifact_name": "scout_report",
                    "blocking": False,
                    "risk_level": "LOW",
                    "action": None,
                    "blocking_summary": [],
                }),
            }

        mock_poll.side_effect = poll_side_effect

        result = start_role_v2_impl(
            role="scout",
            user_task="Investigate the repo",
            api_key="test-key",
        )

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["control_summary"].get("valid"))

        # Verify the role run record has completed lifecycle state
        role_store = RoleRunStore(os.path.join(self.tmpdir, "runs"))
        role_run_id = result["role_run_id"]
        role_run = role_store.get_role_run(role_run_id)
        self.assertIsNotNone(role_run)
        self.assertEqual(role_run.get("lifecycle_state"), "completed")

    @patch("mcp_agent.role_lifecycle._poll_task_status")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_summary_prompt_sent_in_same_conversation(self, mock_start, mock_poll):
        """Summary prompt is sent in the same conversation as the main role prompt."""
        from mcp_agent.role_lifecycle import start_role_v2_impl

        conversations_used = []

        def start_side_effect(*args, **kwargs):
            conv_id = kwargs.get("conversation_id") or "new"
            conversations_used.append(conv_id)
            return {
                "task_id": f"task-{len(conversations_used)}",
                "conversation_id": conv_id,
            }

        mock_start.side_effect = start_side_effect

        def poll_side_effect(task_id, url=None, max_polls=None):
            return {
                "status": "completed",
                "answer": json.dumps({
                    "status": "completed",
                    "role": "scout",
                    "summary": "Scout completed.",
                    "primary_artifact_name": "scout_report",
                    "blocking": False,
                    "risk_level": "LOW",
                    "action": None,
                    "blocking_summary": [],
                }),
            }

        mock_poll.side_effect = poll_side_effect

        result = start_role_v2_impl(
            role="scout",
            user_task="Investigate the repo",
            api_key="test-key",
        )

        # Both calls should use the same conversation_id
        self.assertEqual(len(conversations_used), 2)
        self.assertEqual(
            conversations_used[0],
            conversations_used[1],
            "Summary should be sent in the same conversation",
        )

    @patch("mcp_agent.role_lifecycle._poll_task_status")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_role_run_completes_after_summary_saved(self, mock_start, mock_poll):
        """Role run completes only after summary response is saved."""
        from mcp_agent.role_lifecycle import start_role_v2_impl
        from mcp_agent.role_store import RoleRunStore

        call_count = [0]

        def start_side_effect(*args, **kwargs):
            call_count[0] += 1
            return {
                "task_id": f"task-{call_count[0]}",
                "conversation_id": "conv-main",
            }

        mock_start.side_effect = start_side_effect

        def poll_side_effect(task_id, url=None, max_polls=None):
            return {
                "status": "completed",
                "answer": json.dumps({
                    "status": "completed",
                    "role": "scout",
                    "summary": "Scout completed.",
                    "primary_artifact_name": "scout_report",
                    "blocking": False,
                    "risk_level": "LOW",
                    "action": None,
                    "blocking_summary": [],
                }),
            }

        mock_poll.side_effect = poll_side_effect

        result = start_role_v2_impl(
            role="scout",
            user_task="Investigate the repo",
            api_key="test-key",
        )

        self.assertEqual(result["status"], "completed")

        role_store = RoleRunStore(os.path.join(self.tmpdir, "runs"))
        role_run = role_store.get_role_run(result["role_run_id"])
        self.assertIsNotNone(role_run)
        self.assertEqual(role_run.get("status"), "completed")
        self.assertEqual(
            role_run.get("lifecycle_state"), "completed"
        )


class TestSummaryValidationInLifecycle(unittest.TestCase):
    """Test summary validation behavior in the lifecycle."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_lifecycle_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = os.path.join(
            self.tmpdir, "runs"
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None

    @patch("mcp_agent.role_lifecycle._poll_task_status")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_summary_json_parsed_and_returned(self, mock_start, mock_poll):
        """Summary JSON is parsed and returned as control summary."""
        from mcp_agent.role_lifecycle import start_role_v2_impl

        call_count = [0]

        def start_side_effect(*args, **kwargs):
            call_count[0] += 1
            return {
                "task_id": f"task-{call_count[0]}",
                "conversation_id": "conv-main",
            }

        mock_start.side_effect = start_side_effect

        def poll_side_effect(task_id, url=None, max_polls=None):
            if task_id == "task-main":
                return {
                    "status": "completed",
                    "answer": json.dumps({
                        "status": "completed",
                        "role": "scout",
                        "summary": "Scout completed.",
                        "primary_artifact_name": "scout_report",
                        "blocking": False,
                        "risk_level": "LOW",
                        "action": None,
                        "blocking_summary": [],
                    }),
                }
            else:
                return {
                    "status": "completed",
                    "answer": json.dumps({
                        "status": "completed",
                        "role": "scout",
                        "summary": "Scout completed.",
                        "primary_artifact_name": "scout_report",
                        "blocking": False,
                        "risk_level": "LOW",
                        "action": None,
                        "blocking_summary": [],
                    }),
                }

        mock_poll.side_effect = poll_side_effect

        result = start_role_v2_impl(
            role="scout",
            user_task="Investigate the repo",
            api_key="test-key",
        )

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["control_summary"].get("valid"))
        self.assertEqual(
            result["control_summary"]["summary"], "Scout completed."
        )

    @patch("mcp_agent.role_lifecycle._poll_task_status")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_summary_repair_attempted_on_parse_failure(self, mock_start, mock_poll):
        """Summary JSON repair is attempted once if parsing fails."""
        from mcp_agent.role_lifecycle import start_role_v2_impl

        call_count = [0]
        poll_count = [0]

        def start_side_effect(*args, **kwargs):
            call_count[0] += 1
            return {
                "task_id": f"task-{call_count[0]}",
                "conversation_id": "conv-main",
            }

        mock_start.side_effect = start_side_effect

        def poll_side_effect(task_id, url=None, max_polls=None):
            poll_count[0] += 1
            if poll_count[0] == 1:
                # First poll (main response) — valid JSON
                return {
                    "status": "completed",
                    "answer": json.dumps({
                        "status": "completed",
                        "role": "scout",
                        "summary": "Scout completed.",
                        "primary_artifact_name": "scout_report",
                        "blocking": False,
                        "risk_level": "LOW",
                        "action": None,
                        "blocking_summary": [],
                    }),
                }
            elif poll_count[0] == 2:
                # Second poll (summary response) — invalid JSON
                return {
                    "status": "completed",
                    "answer": "{invalid json}",
                }
            else:
                # Third poll (repair response) — valid JSON
                return {
                    "status": "completed",
                    "answer": json.dumps({
                        "status": "completed",
                        "role": "scout",
                        "summary": "Scout completed (repaired).",
                        "primary_artifact_name": "scout_report",
                        "blocking": False,
                        "risk_level": "LOW",
                        "action": None,
                        "blocking_summary": [],
                    }),
                }

        mock_poll.side_effect = poll_side_effect

        result = start_role_v2_impl(
            role="scout",
            user_task="Investigate the repo",
            api_key="test-key",
        )

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["control_summary"].get("valid"))
        # Should have used repair
        self.assertEqual(call_count[0], 3)

    @patch("mcp_agent.role_lifecycle._poll_task_status")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_summary_output_does_not_allow_next_role(self, mock_start, mock_poll):
        """Summary output does not allow next_role."""
        from mcp_agent.role_lifecycle import start_role_v2_impl

        call_count = [0]

        def start_side_effect(*args, **kwargs):
            call_count[0] += 1
            return {
                "task_id": f"task-{call_count[0]}",
                "conversation_id": "conv-main",
            }

        mock_start.side_effect = start_side_effect

        def poll_side_effect(task_id, url=None, max_polls=None):
            if task_id == "task-main":
                return {
                    "status": "completed",
                    "answer": json.dumps({
                        "status": "completed",
                        "role": "scout",
                        "summary": "Scout completed.",
                        "primary_artifact_name": "scout_report",
                        "blocking": False,
                        "risk_level": "LOW",
                        "action": None,
                        "blocking_summary": [],
                    }),
                }
            else:
                # Summary with next_role (forbidden)
                return {
                    "status": "completed",
                    "answer": json.dumps({
                        "status": "completed",
                        "role": "scout",
                        "summary": "Scout completed.",
                        "primary_artifact_name": "scout_report",
                        "blocking": False,
                        "risk_level": "LOW",
                        "action": None,
                        "blocking_summary": [],
                        "next_role": "architect",  # Forbidden!
                    }),
                }

        mock_poll.side_effect = poll_side_effect

        result = start_role_v2_impl(
            role="scout",
            user_task="Investigate the repo",
            api_key="test-key",
        )

        # Should fall back to safe summary since next_role is forbidden
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["control_summary"].get("valid"))
        # The fallback should not have next_role
        self.assertNotIn("next_role", result["control_summary"])

    @patch("mcp_agent.role_lifecycle._poll_task_status")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_summary_output_does_not_allow_ready_for_next_role(self, mock_start, mock_poll):
        """Summary output does not allow ready_for_next_role."""
        from mcp_agent.role_lifecycle import start_role_v2_impl

        call_count = [0]

        def start_side_effect(*args, **kwargs):
            call_count[0] += 1
            return {
                "task_id": f"task-{call_count[0]}",
                "conversation_id": "conv-main",
            }

        mock_start.side_effect = start_side_effect

        def poll_side_effect(task_id, url=None, max_polls=None):
            if task_id == "task-main":
                return {
                    "status": "completed",
                    "answer": json.dumps({
                        "status": "completed",
                        "role": "scout",
                        "summary": "Scout completed.",
                        "primary_artifact_name": "scout_report",
                        "blocking": False,
                        "risk_level": "LOW",
                        "action": None,
                        "blocking_summary": [],
                    }),
                }
            else:
                # Summary with ready_for_next_role (forbidden)
                return {
                    "status": "completed",
                    "answer": json.dumps({
                        "status": "completed",
                        "role": "scout",
                        "summary": "Scout completed.",
                        "primary_artifact_name": "scout_report",
                        "blocking": False,
                        "risk_level": "LOW",
                        "action": None,
                        "blocking_summary": [],
                        "ready_for_next_role": True,  # Forbidden!
                    }),
                }

        mock_poll.side_effect = poll_side_effect

        result = start_role_v2_impl(
            role="scout",
            user_task="Investigate the repo",
            api_key="test-key",
        )

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["control_summary"].get("valid"))
        self.assertNotIn("ready_for_next_role", result["control_summary"])

    @patch("mcp_agent.role_lifecycle._poll_task_status")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_reviewer_summary_must_contain_action(self, mock_start, mock_poll):
        """Reviewer summary must contain action=PASS or action=BLOCKER."""
        from mcp_agent.role_lifecycle import start_role_v2_impl
        from mcp_agent.artifact_store import ArtifactStore

        # Create required artifacts in the store so resolution succeeds
        store = ArtifactStore()
        for art_name, content in [
            ("scout_report", "# Scout Report\n\nFacts gathered."),
            ("architect_plan", "# Architect Plan\n\nPlan defined."),
            ("coder_report", "# Coder Report\n\nACTION: BLOCKER\n\nFix needed."),
        ]:
            store.save(
                run_id="test-run-rv",
                role_run_id=f"test-run-rv-{art_name}-1",
                role=art_name.split("_")[0],
                artifact_name=art_name,
                content=content,
            )

        call_count = [0]

        def start_side_effect(*args, **kwargs):
            call_count[0] += 1
            return {
                "task_id": f"task-{call_count[0]}",
                "conversation_id": "conv-main",
            }

        mock_start.side_effect = start_side_effect

        def poll_side_effect(task_id, url=None, max_polls=None):
            if task_id == "task-main":
                return {
                    "status": "completed",
                    "answer": json.dumps({
                        "status": "completed",
                        "role": "reviewer",
                        "summary": "Review completed.",
                        "primary_artifact_name": "reviewer_report",
                        "blocking": False,
                        "risk_level": "LOW",
                        "action": None,
                        "blocking_summary": [],
                    }),
                }
            else:
                # Reviewer summary with action=null (invalid for reviewer)
                return {
                    "status": "completed",
                    "answer": json.dumps({
                        "status": "completed",
                        "role": "reviewer",
                        "summary": "Review completed.",
                        "primary_artifact_name": "reviewer_report",
                        "blocking": False,
                        "risk_level": "LOW",
                        "action": None,  # Invalid for reviewer!
                        "blocking_summary": [],
                    }),
                }

        mock_poll.side_effect = poll_side_effect

        result = start_role_v2_impl(
            role="reviewer",
            user_task="Review implementation",
            input_artifacts={
                "scout_report": "test-run-rv/test-run-rv-scout_report-1_scout_report.artifact",
                "architect_plan": "test-run-rv/test-run-rv-architect_plan-1_architect_plan.artifact",
                "coder_report": "test-run-rv/test-run-rv-coder_report-1_coder_report.artifact",
            },
            api_key="test-key",
        )

        self.assertEqual(result["status"], "completed")
        # Should fall back to safe summary with BLOCKER
        self.assertTrue(result["control_summary"].get("valid"))
        self.assertEqual(
            result["control_summary"]["action"], "BLOCKER"
        )

    @patch("mcp_agent.role_lifecycle._poll_task_status")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_non_reviewer_summary_has_action_null(self, mock_start, mock_poll):
        """Non-reviewer summary must have action=null."""
        from mcp_agent.role_lifecycle import start_role_v2_impl

        call_count = [0]

        def start_side_effect(*args, **kwargs):
            call_count[0] += 1
            return {
                "task_id": f"task-{call_count[0]}",
                "conversation_id": "conv-main",
            }

        mock_start.side_effect = start_side_effect

        def poll_side_effect(task_id, url=None, max_polls=None):
            if task_id == "task-main":
                return {
                    "status": "completed",
                    "answer": json.dumps({
                        "status": "completed",
                        "role": "scout",
                        "summary": "Scout completed.",
                        "primary_artifact_name": "scout_report",
                        "blocking": False,
                        "risk_level": "LOW",
                        "action": None,
                        "blocking_summary": [],
                    }),
                }
            else:
                # Scout summary with action=PASS (invalid for non-reviewer)
                return {
                    "status": "completed",
                    "answer": json.dumps({
                        "status": "completed",
                        "role": "scout",
                        "summary": "Scout completed.",
                        "primary_artifact_name": "scout_report",
                        "blocking": False,
                        "risk_level": "LOW",
                        "action": "PASS",  # Invalid for non-reviewer!
                        "blocking_summary": [],
                    }),
                }

        mock_poll.side_effect = poll_side_effect

        result = start_role_v2_impl(
            role="scout",
            user_task="Investigate the repo",
            api_key="test-key",
        )

        self.assertEqual(result["status"], "completed")
        # Should fall back to safe summary with action=null
        self.assertTrue(result["control_summary"].get("valid"))
        self.assertIsNone(result["control_summary"]["action"])


class TestRawArtifactContentNotRequired(unittest.TestCase):
    """Test that raw artifact content is not required in request payload."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_lifecycle_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = os.path.join(
            self.tmpdir, "runs"
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None

    @patch("mcp_agent.role_lifecycle._poll_task_status")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_artifact_contents_loaded_server_side(self, mock_start, mock_poll):
        """Artifact contents are loaded server-side into the main prompt."""
        from mcp_agent.role_lifecycle import start_role_v2_impl
        from mcp_agent.artifact_store import ArtifactStore

        # Create a scout_report artifact in the store so resolution succeeds
        store = ArtifactStore()
        store.save(
            run_id="test-run-as",
            role_run_id="test-run-as-scout-1",
            role="scout",
            artifact_name="scout_report",
            content="# Scout Report\n\nRepository analyzed.\n\nKey findings:\n- Proto files found\n- Ruby bindings generated",
        )

        captured_prompts = []

        def start_side_effect(*args, **kwargs):
            prompt = args[0] if args else kwargs.get("prompt", "")
            captured_prompts.append(prompt)
            return {
                "task_id": f"task-{len(captured_prompts)}",
                "conversation_id": "conv-main",
            }

        mock_start.side_effect = start_side_effect

        def poll_side_effect(task_id, url=None, max_polls=None):
            return {
                "status": "completed",
                "answer": json.dumps({
                    "status": "completed",
                    "role": "architect",
                    "summary": "Architect completed.",
                    "primary_artifact_name": "architect_plan",
                    "blocking": False,
                    "risk_level": "LOW",
                    "action": None,
                    "blocking_summary": [],
                }),
            }

        mock_poll.side_effect = poll_side_effect

        # Pass artifact reference (path, not content)
        result = start_role_v2_impl(
            role="architect",
            user_task="Plan implementation",
            input_artifacts={
                "scout_report": "test-run-as/test-run-as-scout-1_scout_report.artifact",
            },
            api_key="test-key",
        )

        self.assertEqual(result["status"], "completed")
        # The first prompt should contain the artifact content (loaded server-side)
        self.assertTrue(len(captured_prompts) >= 1)
        self.assertIn("# Scout Report", captured_prompts[0])
        self.assertIn("Proto files found", captured_prompts[0])


class TestMCPStyleWrappedValues(unittest.TestCase):
    """Test that MCP-style wrapped values are correctly unwrapped in shttp_role_start_v2."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_lifecycle_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = os.path.join(
            self.tmpdir, "runs"
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None

    @patch("mcp_agent.role_lifecycle._poll_task_status")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_shttp_role_start_v2_unwraps_artifact_refs(self, mock_start, mock_poll):
        """shttp_role_start_v2 correctly unwraps MCP-style wrapped artifact references."""
        from mcp_agent.artifact_store import ArtifactStore
        from mcp_agent.server import shttp_role_start_v2

        # Create a scout_report artifact in the store so resolution succeeds
        store = ArtifactStore()
        store.save(
            run_id="test-run-mcp",
            role_run_id="test-run-mcp-scout-1",
            role="scout",
            artifact_name="scout_report",
            content="# Scout Report\n\nRepository analyzed.",
        )

        captured_prompts = []

        def start_side_effect(*args, **kwargs):
            prompt = args[0] if args else kwargs.get("prompt", "")
            captured_prompts.append(prompt)
            return {
                "task_id": f"task-{len(captured_prompts)}",
                "conversation_id": "conv-main",
            }

        mock_start.side_effect = start_side_effect

        def poll_side_effect(task_id, url=None, max_polls=None):
            return {
                "status": "completed",
                "answer": json.dumps({
                    "status": "completed",
                    "role": "architect",
                    "summary": "Architect completed.",
                    "primary_artifact_name": "architect_plan",
                    "blocking": False,
                    "risk_level": "LOW",
                    "action": None,
                    "blocking_summary": [],
                }),
            }

        mock_poll.side_effect = poll_side_effect

        # Pass artifact references as MCP-style wrapped dicts: {"text": "path"}
        result = shttp_role_start_v2(
            role={"text": "architect"},
            user_task={"text": "Plan implementation"},
            input_artifacts={
                "scout_report": {"text": "test-run-mcp/test-run-mcp-scout-1_scout_report.artifact"},
            },
            api_key={"text": "test-key"},
        )

        self.assertEqual(result["status"], "completed")
        # The first prompt should contain the artifact content (loaded server-side)
        self.assertTrue(len(captured_prompts) >= 1)
        self.assertIn("# Scout Report", captured_prompts[0])

    @patch("mcp_agent.role_lifecycle._poll_task_status")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_shttp_role_start_v2_unwraps_metadata(self, mock_start, mock_poll):
        """shttp_role_start_v2 correctly unwraps MCP-style wrapped metadata."""
        from mcp_agent.server import shttp_role_start_v2

        captured_prompts = []

        def start_side_effect(*args, **kwargs):
            prompt = args[0] if args else kwargs.get("prompt", "")
            captured_prompts.append(prompt)
            return {
                "task_id": f"task-{len(captured_prompts)}",
                "conversation_id": "conv-main",
            }

        mock_start.side_effect = start_side_effect

        def poll_side_effect(task_id, url=None, max_polls=None):
            return {
                "status": "completed",
                "answer": json.dumps({
                    "status": "completed",
                    "role": "scout",
                    "summary": "Scout completed.",
                    "primary_artifact_name": "scout_report",
                    "blocking": False,
                    "risk_level": "LOW",
                    "action": None,
                    "blocking_summary": [],
                }),
            }

        mock_poll.side_effect = poll_side_effect

        # Pass metadata as MCP-style wrapped dicts
        result = shttp_role_start_v2(
            role={"text": "scout"},
            user_task={"text": "Investigate the repo"},
            metadata={
                "repository": {"text": "https://github.com/example/repo"},
                "base_branch": {"text": "main"},
            },
            api_key={"text": "test-key"},
        )

        self.assertEqual(result["status"], "completed")
        # The prompt should contain the unwrapped metadata values
        self.assertTrue(len(captured_prompts) >= 1)
        self.assertIn("https://github.com/example/repo", captured_prompts[0])


if __name__ == "__main__":
    unittest.main()
