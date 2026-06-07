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
        # Should fall back to safe summary: reviewer fallback returns BLOCKER
        # when action cannot be derived from the main artifact.
        self.assertTrue(result["control_summary"].get("valid"))
        self.assertEqual(result["control_summary"]["action"], "BLOCKER")
        self.assertTrue(result["control_summary"].get("blocking"))

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


class TestV2ArtifactStorage(unittest.TestCase):
    """Test that v2 artifacts are stored through ArtifactStore correctly."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_v2_artifacts_")
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
    def test_primary_artifact_saved_via_artifact_store(self, mock_start, mock_poll):
        """Scout primary artifact is saved through ArtifactStore, not legacy store."""
        from mcp_agent.artifact_store import ArtifactStore
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
        self.assertIn("artifacts", result)
        self.assertIn("primary", result["artifacts"])
        primary_path = result["artifacts"]["primary"]["artifact_path"]
        self.assertIsNotNone(primary_path)

        # Verify artifact exists in ArtifactStore by listing its run directory
        state_dir = os.path.join(self.tmpdir, "runs")
        full_primary_path = os.path.join(state_dir, primary_path)
        self.assertTrue(os.path.exists(full_primary_path))
        # Verify .meta.json companion exists
        meta_path = full_primary_path + ".meta.json"
        self.assertTrue(os.path.exists(meta_path))
        meta = json.loads(open(meta_path).read())
        self.assertEqual(meta["artifact_name"], "scout_report")
        self.assertEqual(meta["role"], "scout")

    @patch("mcp_agent.role_lifecycle._poll_task_status")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_summary_artifact_saved_separately(self, mock_start, mock_poll):
        """Summary artifact is a different file from primary."""
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

        # Primary and summary should have different artifact names
        primary_name = result["artifacts"]["primary"]["artifact_name"]
        summary_name = result["artifacts"]["summary"]["artifact_name"]
        self.assertNotEqual(primary_name, summary_name)
        self.assertEqual(primary_name, "scout_report")
        self.assertEqual(summary_name, "scout_summary")

    @patch("mcp_agent.role_lifecycle._poll_task_status")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_summary_does_not_overwrite_primary(self, mock_start, mock_poll):
        """Saving summary does not overwrite primary artifact."""
        from mcp_agent.artifact_store import ArtifactStore
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

        primary_path = result["artifacts"]["primary"]["artifact_path"]
        summary_path = result["artifacts"]["summary"]["artifact_path"]

        # Verify both artifacts exist and are different files
        self.assertIsNotNone(primary_path)
        self.assertIsNotNone(summary_path)
        self.assertNotEqual(primary_path, summary_path)

        state_dir = os.path.join(self.tmpdir, "runs")
        full_primary = os.path.join(state_dir, primary_path)
        full_summary = os.path.join(state_dir, summary_path)
        self.assertTrue(os.path.exists(full_primary))
        self.assertTrue(os.path.exists(full_summary))

        # Verify primary content is non-empty (it stores the LLM response)
        primary_content = open(full_primary).read()
        self.assertTrue(len(primary_content.strip()) > 0)


class TestV2EndToEndChain(unittest.TestCase):
    """Mocked end-to-end v2 chain: scout → architect → coder → reviewer."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_v2_chain_")
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
    def test_full_v2_chain_artifact_refs_flow(self, mock_start, mock_poll):
        """Artifact refs flow through the v2 chain correctly."""
        from mcp_agent.artifact_store import ArtifactStore
        from mcp_agent.role_lifecycle import start_role_v2_impl

        produced_artifacts = {}

        def make_scout_poll():
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

        def make_architect_poll():
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

        def make_coder_poll():
            return {
                "status": "completed",
                "answer": json.dumps({
                    "status": "completed",
                    "role": "coder",
                    "summary": "Coder completed.",
                    "primary_artifact_name": "coder_report",
                    "blocking": False,
                    "risk_level": "LOW",
                    "action": None,
                    "blocking_summary": [],
                }),
            }

        def make_reviewer_poll():
            return {
                "status": "completed",
                "answer": json.dumps({
                    "status": "completed",
                    "role": "reviewer",
                    "summary": "Reviewer completed.",
                    "primary_artifact_name": "reviewer_report",
                    "blocking": False,
                    "risk_level": "MEDIUM",
                    "action": "PASS",
                    "blocking_summary": [],
                }),
            }

        # Step 1: Scout
        mock_start.return_value = {
            "task_id": "task-scout",
            "conversation_id": "conv-scout",
        }
        mock_poll.side_effect = lambda *a, **k: make_scout_poll()

        scout_result = start_role_v2_impl(
            role="scout",
            user_task="Implement a Ruby gRPC client for freeplane_plugin_grpc.",
            metadata={"repository": "https://github.com/metacoma/freeplane_plugin_grpc"},
            api_key="test-key",
        )

        self.assertEqual(scout_result["status"], "completed")
        scout_artifact_path = scout_result["artifacts"]["primary"]["artifact_path"]
        produced_artifacts["scout_report"] = scout_artifact_path

        # Step 2: Architect (with scout_report)
        mock_start.return_value = {
            "task_id": "task-arch",
            "conversation_id": "conv-arch",
        }
        mock_poll.side_effect = lambda *a, **k: make_architect_poll()

        architect_result = start_role_v2_impl(
            role="architect",
            user_task="Plan implementation.",
            input_artifacts={
                "scout_report": scout_artifact_path,
            },
            api_key="test-key",
        )

        self.assertEqual(architect_result["status"], "completed")
        architect_artifact_path = architect_result["artifacts"]["primary"]["artifact_path"]
        produced_artifacts["architect_plan"] = architect_artifact_path

        # Step 3: Coder (with scout_report + architect_plan)
        mock_start.return_value = {
            "task_id": "task-coder",
            "conversation_id": "conv-coder",
        }
        mock_poll.side_effect = lambda *a, **k: make_coder_poll()

        coder_result = start_role_v2_impl(
            role="coder",
            user_task="Implement feature.",
            input_artifacts={
                "scout_report": scout_artifact_path,
                "architect_plan": architect_artifact_path,
            },
            api_key="test-key",
        )

        self.assertEqual(coder_result["status"], "completed")
        coder_artifact_path = coder_result["artifacts"]["primary"]["artifact_path"]
        produced_artifacts["coder_report"] = coder_artifact_path

        # Step 4: Reviewer (with all artifacts)
        mock_start.return_value = {
            "task_id": "task-rev",
            "conversation_id": "conv-rev",
        }
        mock_poll.side_effect = lambda *a, **k: make_reviewer_poll()

        reviewer_result = start_role_v2_impl(
            role="reviewer",
            user_task="Review implementation.",
            input_artifacts={
                "scout_report": scout_artifact_path,
                "architect_plan": architect_artifact_path,
                "coder_report": coder_artifact_path,
            },
            api_key="test-key",
        )

        self.assertEqual(reviewer_result["status"], "completed")
        self.assertEqual(reviewer_result["control_summary"]["action"], "PASS")

        # Verify all artifacts exist in ArtifactStore
        state_dir = os.path.join(self.tmpdir, "runs")
        for role_name, art_path in produced_artifacts.items():
            full_path = os.path.join(state_dir, art_path)
            self.assertTrue(os.path.exists(full_path), f"Artifact {role_name} not found at {full_path}")


class TestV2ResultArtifactLoading(unittest.TestCase):
    """Test shttp_role_result_v2 artifact loading."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_v2_result_")
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = os.path.join(
            self.tmpdir, "runs"
        )
        # Reset the role_tools singleton so it picks up the new state_dir
        import mcp_agent.role_tools as rt_mod
        rt_mod._role_store = None

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None
        import mcp_agent.role_tools as rt_mod
        rt_mod._role_store = None

    @patch("mcp_agent.role_lifecycle._poll_task_status")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_result_v2_no_undefined_run_id(self, mock_start, mock_poll):
        """result_v2 does not use undefined run_id variable."""
        from mcp_agent.artifact_store import ArtifactStore
        from mcp_agent.role_lifecycle import start_role_v2_impl
        from mcp_agent.server import shttp_role_result_v2

        # Create a role run record via start_role_v2_impl
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
            user_task="Test task",
            api_key="test-key",
        )

        self.assertEqual(result["status"], "completed")
        role_run_id = result["role_run_id"]

        # Verify the role run record exists in the store
        from mcp_agent.role_store import RoleRunStore
        rs = RoleRunStore()
        rr = rs.get_role_run(role_run_id)
        self.assertIsNotNone(rr, "Role run record should exist")

        # Extract run_id from the artifact path (format: run_id/filename.artifact)
        primary_path = result["artifacts"]["primary"]["artifact_path"]
        run_id = primary_path.split("/")[0] if "/" in primary_path else ""

        # Verify artifacts were saved to ArtifactStore
        store = ArtifactStore()
        artifacts = store.list(run_id) if run_id else []
        artifact_names = {a["artifact_name"] for a in artifacts}
        self.assertIn("scout_report", artifact_names)
        self.assertIn("scout_summary", artifact_names)

        # Now call result_v2 — this should not raise NameError
        # (Blocker 3: undefined run_id variable)
        try:
            result_v2 = shttp_role_result_v2(
                role_run_id=role_run_id,
                include_full_artifacts=False,
                return_control_summary=True,
            )
        except NameError as e:
            self.fail(f"shttp_role_result_v2 raised NameError: {e}")

        self.assertEqual(result_v2["status"], "completed")
        self.assertIn("artifacts", result_v2)
        self.assertIn("primary", result_v2["artifacts"])
        self.assertIn("summary", result_v2["artifacts"])

    @patch("mcp_agent.role_lifecycle._poll_task_status")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_result_v2_include_full_artifacts_works(self, mock_start, mock_poll):
        """result_v2 with include_full_artifacts=true loads content correctly."""
        from mcp_agent.artifact_store import ArtifactStore
        from mcp_agent.role_lifecycle import start_role_v2_impl
        from mcp_agent.server import shttp_role_result_v2

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
            user_task="Test task with content",
            api_key="test-key",
        )

        self.assertEqual(result["status"], "completed")
        role_run_id = result["role_run_id"]

        # Call result_v2 with full artifacts
        result_v2 = shttp_role_result_v2(
            role_run_id=role_run_id,
            include_full_artifacts=True,
            return_control_summary=True,
        )

        self.assertEqual(result_v2["status"], "completed")
        # Full artifact content should be included
        self.assertIn("primary_artifact", result_v2)
        self.assertIn("summary_artifact", result_v2)
        # Primary artifact contains the mock LLM response
        self.assertIn("Scout completed", result_v2["primary_artifact"])


class TestV2SummarySchema(unittest.TestCase):
    """Test v2 summary schema enforcement."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_v2_summary_")
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
    def test_v2_summary_rejects_next_role(self, mock_start, mock_poll):
        """v2 summary schema rejects/strips next_role."""
        from mcp_agent.role_lifecycle import start_role_v2_impl

        # Return a summary with next_role field
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
                "next_role": "architect",  # Should be rejected
            }),
        }

        result = start_role_v2_impl(
            role="scout",
            user_task="Test task",
            api_key="test-key",
        )

        self.assertEqual(result["status"], "completed")
        # The validator should have stripped or rejected next_role
        control_summary = result["control_summary"]
        self.assertNotIn("next_role", control_summary)

    @patch("mcp_agent.role_lifecycle._poll_task_status")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_reviewer_summary_requires_action_pass_or_blocker(self, mock_start, mock_poll):
        """Reviewer summary requires action=PASS or action=BLOCKER."""
        from mcp_agent.artifact_store import ArtifactStore
        from mcp_agent.role_lifecycle import start_role_v2_impl

        # Create required artifacts so reviewer passes validation
        store = ArtifactStore()
        store.save(
            run_id="test-rev-001",
            role_run_id="test-rev-001-scout-1",
            role="scout",
            artifact_name="scout_report",
            content="# Scout Report\n\nTest.",
        )
        store.save(
            run_id="test-rev-002",
            role_run_id="test-rev-002-arch-1",
            role="architect",
            artifact_name="architect_plan",
            content="# Architect Plan\n\nTest.",
        )
        store.save(
            run_id="test-rev-003",
            role_run_id="test-rev-003-coder-1",
            role="coder",
            artifact_name="coder_report",
            content="# Coder Report\n\nTest.",
        )

        call_count = [0]

        def poll_side_effect(task_id, url=None, max_polls=None):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call (main prompt) — return valid summary
                return {
                    "status": "completed",
                    "answer": json.dumps({
                        "status": "completed",
                        "role": "reviewer",
                        "summary": "Reviewer completed.",
                        "primary_artifact_name": "reviewer_report",
                        "blocking": False,
                        "risk_level": "MEDIUM",
                        "action": "PASS",  # Valid for reviewer
                        "blocking_summary": [],
                    }),
                }
            else:
                # Subsequent calls (summary/repair) — return valid summary
                return {
                    "status": "completed",
                    "answer": json.dumps({
                        "status": "completed",
                        "role": "reviewer",
                        "summary": "Reviewer summary.",
                        "primary_artifact_name": "reviewer_report",
                        "blocking": False,
                        "risk_level": "MEDIUM",
                        "action": "PASS",
                        "blocking_summary": [],
                    }),
                }

        mock_start.return_value = {
            "task_id": "task-001",
            "conversation_id": "conv-001",
        }
        mock_poll.side_effect = poll_side_effect

        result = start_role_v2_impl(
            role="reviewer",
            user_task="Review implementation.",
            input_artifacts={
                "scout_report": "test-rev-001/test-rev-001-scout-1_scout_report.artifact",
                "architect_plan": "test-rev-002/test-rev-002-arch-1_architect_plan.artifact",
                "coder_report": "test-rev-003/test-rev-003-coder-1_coder_report.artifact",
            },
            api_key="test-key",
        )

        self.assertEqual(result["status"], "completed")
        control_summary = result["control_summary"]
        self.assertIn("action", control_summary)
        self.assertEqual(control_summary["action"], "PASS")

    @patch("mcp_agent.role_lifecycle._poll_task_status")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_non_reviewer_summary_requires_action_null(self, mock_start, mock_poll):
        """Non-reviewer summary requires action=null."""
        from mcp_agent.role_lifecycle import start_role_v2_impl

        # Return a scout summary with action=PASS (invalid for non-reviewer)
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
                "action": "PASS",  # Invalid for non-reviewer
                "blocking_summary": [],
            }),
        }

        result = start_role_v2_impl(
            role="scout",
            user_task="Test task",
            api_key="test-key",
        )

        self.assertEqual(result["status"], "completed")
        # The validator should have set action to null
        control_summary = result["control_summary"]
        self.assertIsNone(control_summary["action"])


class TestV2ExactPathResolution(unittest.TestCase):
    """Tests for v2 input artifact exact path resolution."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_v2_exact_path_")
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
    def test_v2_resolver_reads_exact_path(self, mock_start, mock_poll):
        """Test 3: v2 input resolver reads exact path for path-like refs."""
        from mcp_agent.artifact_store import ArtifactStore
        from mcp_agent.role_lifecycle import start_role_v2_impl

        store = ArtifactStore()
        meta1 = store.save(
            run_id="run-exact-1",
            role_run_id="run-exact-1-scout-1",
            role="scout",
            artifact_name="scout_report",
            content="FIRST scout report content",
        )
        meta2 = store.save(
            run_id="run-exact-1",
            role_run_id="run-exact-1-scout-2",
            role="scout",
            artifact_name="scout_report",
            content="SECOND scout report content",
        )

        mock_start.return_value = {
            "task_id": "task-1",
            "conversation_id": "conv-1",
        }
        mock_poll.return_value = {
            "status": "completed",
            "answer": json.dumps({
                "status": "completed",
                "role": "architect",
                "summary": "done",
                "primary_artifact_name": "architect_plan",
                "blocking": False,
                "risk_level": None,
                "action": None,
                "blocking_summary": [],
            }),
        }

        result = start_role_v2_impl(
            role="architect",
            user_task="Test task",
            input_artifacts={
                "scout_report": meta2["artifact_path"],
            },
            api_key="test-key",
        )

        self.assertEqual(result["status"], "completed")
        # Verify the main prompt (first call) contained SECOND content, not FIRST
        main_prompt = mock_start.call_args_list[0][1]["prompt"]
        self.assertIn("SECOND scout report content", main_prompt)
        self.assertNotIn("FIRST scout report content", main_prompt)

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_v2_resolver_artifact_name_mismatch(self, mock_start):
        """Test 4: artifact name mismatch fails before role execution."""
        from mcp_agent.artifact_store import ArtifactStore
        from mcp_agent.role_lifecycle import start_role_v2_impl

        store = ArtifactStore()
        plan_meta = store.save(
            run_id="run-mismatch-1",
            role_run_id="run-mismatch-1-architect-1",
            role="architect",
            artifact_name="architect_plan",
            content="architect plan content",
        )

        result = start_role_v2_impl(
            role="architect",
            user_task="Test task",
            input_artifacts={
                "scout_report": plan_meta["artifact_path"],
            },
            api_key="test-key",
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "ArtifactNameMismatch")
        self.assertIn("scout_report", result["error"]["message"])
        self.assertIn("architect_plan", result["error"]["message"])

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_v2_resolver_missing_path_fails(self, mock_start):
        """Test 5: missing artifact path fails clearly."""
        from mcp_agent.role_lifecycle import start_role_v2_impl

        result = start_role_v2_impl(
            role="architect",
            user_task="Test task",
            input_artifacts={
                "scout_report": "run-1/missing_scout_report.artifact",
            },
            api_key="test-key",
        )

        self.assertEqual(result["status"], "failed")
        self.assertIn("artifact not found", result["error"]["message"].lower())

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_v2_path_like_detection_narrow(self, mock_start):
        """Test: path-like detection only triggers on .artifact suffix."""
        from mcp_agent.role_lifecycle import start_role_v2_impl

        result = start_role_v2_impl(
            role="architect",
            user_task="Test task",
            input_artifacts={
                "scout_report": "some/path/without/artifact",
            },
            api_key="test-key",
        )
        # Should fail with ArtifactNotFound (no run_id context for fallback)
        self.assertEqual(result["status"], "failed")


class TestReviewerFallbackRegression(unittest.TestCase):
    """Ensure PR #22 reviewer fallback remains correct."""

    def test_reviewer_fallback_is_blocker(self):
        """Reviewer fallback without derivable action returns BLOCKER."""
        from mcp_agent.summary_validator import safe_fallback_summary

        fb = safe_fallback_summary(
            role="reviewer",
            summary_artifact_name="reviewer_summary",
            is_reviewer=True,
            main_artifact_content="",
        )
        self.assertTrue(fb["valid"])
        self.assertEqual(fb["action"], "BLOCKER")
        self.assertTrue(fb["blocking"])
        self.assertEqual(fb["risk_level"], "HIGH")

    def test_non_reviewer_fallback_has_null_action(self):
        """Non-reviewer fallback has action=null."""
        from mcp_agent.summary_validator import safe_fallback_summary

        fb = safe_fallback_summary(
            role="architect",
            summary_artifact_name="architect_summary",
            is_reviewer=False,
        )
        self.assertTrue(fb["valid"])
        self.assertIsNone(fb["action"])


if __name__ == "__main__":
    unittest.main()
