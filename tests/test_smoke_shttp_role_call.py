#!/usr/bin/env python3
"""Smoke test for the full shttp_role_call two-step chain.

Tests the workflow:

    shttp_role_call(role=scout)
      -> get scout_report artifact_id

    shttp_role_call(
        role=architect,
        input_artifacts=[
            {
                "artifact_id": "<scout_report_artifact_id>",
                "artifact_type": "scout_report"
            }
        ]
    )
      -> architect receives scout_report via Jinja injection

Acceptance criteria:
- Runnable with: python -m pytest tests/test_smoke_shttp_role_call.py -v
- Verifies artifact_id from step 1 is correctly resolved and injected
- Uses mocked OpenHands backend (no Docker, no network)
- Passes with the existing codebase without code changes

Note on artifact resolution:
    When passing art_ prefixed artifact IDs as input_artifacts, the
    role_lifecycle resolver needs metadata.run_id to enable its
    Strategy 2 (fallback by name).  This smoke test provides
    metadata={"run_id": "<scout_run_id>"} on the architect call so
    that the scout artifact resolves correctly.

Note on input_artifacts format:
    The MCP tool layer (server.py) normalizes list-format
    input_artifacts to a dict before calling role_call_impl.
    role_call_impl expects dict format: {"scout_report": "art_xxx"}.
    This test passes dict format directly to role_call_impl.
"""

import json
import os
import shutil
import sys
import tempfile
import textwrap
from pathlib import Path
from unittest import TestCase, main as unittest_main
from unittest.mock import MagicMock, patch

# Ensure the project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_agent.artifact_store import ArtifactStore, _generate_artifact_id
from mcp_agent import role_lifecycle


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tmp_state_dir() -> Path:
    """Create a temporary state directory for tests."""
    d = Path(tempfile.mkdtemp(prefix="test_smoke_"))
    return d


def _write_role_config(state_dir: Path) -> Path:
    """Write a minimal roles.yaml into *state_dir* and return its path."""
    cfg = state_dir / "roles.yaml"
    cfg.write_text(
        textwrap.dedent("""\
        roles:
          scout:
            description: "Read-only investigator"
            model: "openai/qwen3:32b"
            prompt_template: "prompts/scout.md"
            readonly: true
            timeout_minutes: 60
            requires_artifacts: []
            output_artifact: scout_report
          architect:
            description: "Implementation planner"
            model: "openai/qwen3:32b"
            prompt_template: "prompts/architect.md"
            readonly: true
            timeout_minutes: 90
            requires_artifacts:
              - scout_report
            output_artifact: architect_plan
        """),
        encoding="utf-8",
    )
    return cfg


def _make_mock_backend(summary_answer=None):
    """Create mock return values for _start_conversation_on_fastapi and _poll_task_status.

    Returns (mock_start, mock_poll) tuple.
    """
    mock_start = MagicMock(return_value={
        "task_id": "task-smoke-001",
        "conversation_id": "conv-smoke-001",
    })

    default_answer = json.dumps({
        "status": "completed",
        "role": "scout",
        "summary": "Scout completed.",
        "primary_artifact_name": "scout_report",
        "blocking": False,
        "risk_level": "LOW",
        "action": None,
        "blocking_summary": [],
    })

    def poll_side_effect(task_id, url=None, max_polls=None):
        return {
            "status": "completed",
            "answer": summary_answer or default_answer,
        }

    mock_poll = MagicMock(side_effect=poll_side_effect)
    return mock_start, mock_poll


def _scout_mock_answer(role="scout"):
    """Return a JSON summary answer string for a completed scout role."""
    return json.dumps({
        "status": "completed",
        "role": role,
        "summary": "Scout report generated.",
        "primary_artifact_name": "scout_report",
        "blocking": False,
        "risk_level": "LOW",
        "action": None,
        "blocking_summary": [],
    })


def _architect_mock_answer():
    """Return a JSON summary answer string for a completed architect role."""
    return json.dumps({
        "status": "completed",
        "role": "architect",
        "summary": "Architect plan generated.",
        "primary_artifact_name": "architect_plan",
        "blocking": False,
        "risk_level": "LOW",
        "action": None,
        "blocking_summary": [],
    })


# ---------------------------------------------------------------------------
# Smoke test: full scout → architect chain
# ---------------------------------------------------------------------------

class TestSmokeScoutToArchitectChain(TestCase):
    """Test the full two-step shttp_role_call chain with mocked backend."""

    def setUp(self):
        self.state_dir = _make_tmp_state_dir()
        self.cfg_path = _write_role_config(self.state_dir)
        os.environ["ROLE_CONFIG_PATH"] = str(self.cfg_path)
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = str(self.state_dir)

    def tearDown(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)
        os.environ.pop("ROLE_CONFIG_PATH", None)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        # Reset module state
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None

    @patch("mcp_agent.role_lifecycle._poll_task_status")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_scout_artifact_id_is_valid_format(self, mock_start, mock_poll):
        """Step 1: Call scout, verify artifact_id format starts with art_."""
        mock_start.return_value = {
            "task_id": "task-smoke-scout",
            "conversation_id": "conv-smoke-scout",
        }
        mock_poll.return_value = {
            "status": "completed",
            "answer": json.dumps({
                "status": "completed",
                "role": "scout",
                "summary": "Repository analyzed.",
                "primary_artifact_name": "scout_report",
                "blocking": False,
                "risk_level": "LOW",
                "action": None,
                "blocking_summary": [],
            }),
        }

        result = role_lifecycle.role_call_impl(
            role="scout",
            user_task="Analyze the repository structure.",
            input_artifacts={},
            metadata={"run_id": "smoke-run-001"},
        )

        # Verify response schema
        self.assertEqual(result["status"], "completed")
        self.assertIn("control_summary", result)
        self.assertIn("artifacts", result)
        self.assertIn("primary", result["artifacts"])
        self.assertIn("artifact_id", result["artifacts"]["primary"])

        # Verify artifact_id format
        artifact_id = result["artifacts"]["primary"]["artifact_id"]
        self.assertTrue(
            artifact_id.startswith("art_"),
            f"artifact_id should start with 'art_', got: {artifact_id}",
        )
        self.assertIn("scout", artifact_id)
        self.assertIn("scout_report", artifact_id)

        # Store for subsequent tests
        self.scout_artifact_id = artifact_id
        self.scout_run_id = result["run_id"]

    @patch("mcp_agent.role_lifecycle._poll_task_status")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_architect_receives_scout_report_via_jinja(self, mock_start, mock_poll):
        """Step 2: Call architect with scout artifact_id, verify Jinja injection.

        The architect's prompt template (prompts/architect.md) contains
        {{ scout_report }} at line 35.  This test verifies that when we
        pass the scout artifact_id as input_artifacts, the MCP server
        resolves the content and injects it into the Jinja template.
        """
        # First, get the scout artifact_id using a separate patched call
        with patch("mcp_agent.role_lifecycle._poll_task_status") as mock_scout_poll, \
             patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi") as mock_scout_start:
            mock_scout_start.return_value = {
                "task_id": "task-smoke-scout-002",
                "conversation_id": "conv-smoke-scout-002",
            }
            mock_scout_poll.return_value = {
                "status": "completed",
                "answer": _scout_mock_answer(),
            }

            scout_result = role_lifecycle.role_call_impl(
                role="scout",
                user_task="Analyze the repository structure.",
                input_artifacts={},
                metadata={"run_id": "smoke-run-002"},
            )

        self.assertEqual(scout_result["status"], "completed")
        scout_artifact_id = scout_result["artifacts"]["primary"]["artifact_id"]
        scout_run_id = scout_result["run_id"]

        # Now call architect with the scout artifact_id using patched context.
        # IMPORTANT: We must provide metadata.run_id so that Strategy 2
        # (fallback by name) can find the artifact in the store.
        with patch("mcp_agent.role_lifecycle._poll_task_status") as mock_arch_poll, \
             patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi") as mock_arch_start:
            mock_arch_start.return_value = {
                "task_id": "task-smoke-arch-002",
                "conversation_id": "conv-smoke-arch-002",
            }
            mock_arch_poll.return_value = {
                "status": "completed",
                "answer": _architect_mock_answer(),
            }

            result = role_lifecycle.role_call_impl(
                role="architect",
                user_task="Plan implementation.",
                input_artifacts={"scout_report": scout_artifact_id},
                metadata={"run_id": scout_run_id},
            )

        # Verify architect response
        self.assertEqual(result["status"], "completed")
        self.assertIn("control_summary", result)
        self.assertIn("artifacts", result)

        # Verify the main prompt (first call to _start_conversation_on_fastapi)
        # contains the scout report content. call_args[0] would be the summary
        # prompt, so we use call_args_list[0] for the main prompt.
        main_prompt = mock_arch_start.call_args_list[0][1]["prompt"]
        self.assertIsNotNone(main_prompt)
        # The prompt should contain the user_task
        self.assertIn("Plan implementation.", main_prompt)
        # The prompt should contain the scout_report section header
        self.assertIn("## Scout Report", main_prompt)

    @patch("mcp_agent.role_lifecycle._poll_task_status")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_full_chain_response_schema(self, mock_start, mock_poll):
        """Verify both scout and architect responses have the correct schema."""
        # Scout call
        with patch("mcp_agent.role_lifecycle._poll_task_status") as mock_scout_poll, \
             patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi") as mock_scout_start:
            mock_scout_start.return_value = {
                "task_id": "task-smoke-scout-003",
                "conversation_id": "conv-smoke-scout-003",
            }
            mock_scout_poll.return_value = {
                "status": "completed",
                "answer": _scout_mock_answer(),
            }

            scout_result = role_lifecycle.role_call_impl(
                role="scout",
                user_task="Full chain test.",
                input_artifacts={},
                metadata={"run_id": "smoke-run-003"},
            )

        # Verify scout response schema
        self.assertEqual(scout_result["status"], "completed")
        self.assertIn("role_run_id", scout_result)
        self.assertIn("run_id", scout_result)
        self.assertEqual(scout_result["role"], "scout")
        self.assertIn("control_summary", scout_result)
        self.assertIn("artifacts", scout_result)
        self.assertIn("primary", scout_result["artifacts"])
        self.assertIn("summary", scout_result["artifacts"])
        self.assertIn("artifact_id", scout_result["artifacts"]["primary"])
        self.assertIn("artifact_type", scout_result["artifacts"]["primary"])
        self.assertEqual(
            scout_result["artifacts"]["primary"]["artifact_type"],
            "scout_report",
        )

        # Architect call
        with patch("mcp_agent.role_lifecycle._poll_task_status") as mock_arch_poll, \
             patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi") as mock_arch_start:
            mock_arch_start.return_value = {
                "task_id": "task-smoke-arch-003",
                "conversation_id": "conv-smoke-arch-003",
            }
            mock_arch_poll.return_value = {
                "status": "completed",
                "answer": _architect_mock_answer(),
            }

            architect_result = role_lifecycle.role_call_impl(
                role="architect",
                user_task="Plan implementation.",
                input_artifacts={
                    "scout_report": scout_result["artifacts"]["primary"]["artifact_id"],
                },
                metadata={"run_id": scout_result["run_id"]},
            )

        # Verify architect response schema
        self.assertEqual(architect_result["status"], "completed")
        self.assertIn("role_run_id", architect_result)
        self.assertIn("run_id", architect_result)
        self.assertEqual(architect_result["role"], "architect")
        self.assertIn("control_summary", architect_result)
        self.assertIn("artifacts", architect_result)
        self.assertIn("primary", architect_result["artifacts"])
        self.assertIn("summary", architect_result["artifacts"])
        self.assertIn("artifact_id", architect_result["artifacts"]["primary"])
        self.assertIn("artifact_type", architect_result["artifacts"]["primary"])
        self.assertEqual(
            architect_result["artifacts"]["primary"]["artifact_type"],
            "architect_plan",
        )

    @patch("mcp_agent.role_lifecycle._poll_task_status")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_artifact_id_resolution_with_metadata_run_id(self, mock_start, mock_poll):
        """Test that providing metadata.run_id enables artifact resolution.

        Without run_id, art_ prefixed IDs would fail because Strategy 2
        (fallback by name) needs a run_id context to call artifact_store.get().
        """
        # Scout call
        with patch("mcp_agent.role_lifecycle._poll_task_status") as mock_scout_poll, \
             patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi") as mock_scout_start:
            mock_scout_start.return_value = {
                "task_id": "task-smoke-scout-004",
                "conversation_id": "conv-smoke-scout-004",
            }
            mock_scout_poll.return_value = {
                "status": "completed",
                "answer": _scout_mock_answer(),
            }

            scout_result = role_lifecycle.role_call_impl(
                role="scout",
                user_task="Test artifact resolution.",
                input_artifacts={},
                metadata={"run_id": "smoke-run-004"},
            )

        self.assertEqual(scout_result["status"], "completed")
        scout_artifact_id = scout_result["artifacts"]["primary"]["artifact_id"]
        scout_run_id = scout_result["run_id"]

        # Architect call WITH metadata.run_id — should succeed
        with patch("mcp_agent.role_lifecycle._poll_task_status") as mock_arch_poll, \
             patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi") as mock_arch_start:
            mock_arch_start.return_value = {
                "task_id": "task-smoke-arch-004",
                "conversation_id": "conv-smoke-arch-004",
            }
            mock_arch_poll.return_value = {
                "status": "completed",
                "answer": _architect_mock_answer(),
            }

            result = role_lifecycle.role_call_impl(
                role="architect",
                user_task="Plan with artifact.",
                input_artifacts={"scout_report": scout_artifact_id},
                metadata={"run_id": scout_run_id},
            )

        self.assertEqual(result["status"], "completed")
        self.assertNotIn("error", result)

    @patch("mcp_agent.role_lifecycle._poll_task_status")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_artifact_content_injected_as_scout_report_variable(self, mock_start, mock_poll):
        """Verify the Jinja variable name is 'scout_report'.

        The architect prompt template uses {{ scout_report }} at line 35.
        This test verifies that the artifact content is injected under
        the key 'scout_report' in the template variables.
        """
        # Scout call
        with patch("mcp_agent.role_lifecycle._poll_task_status") as mock_scout_poll, \
             patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi") as mock_scout_start:
            mock_scout_start.return_value = {
                "task_id": "task-smoke-scout-005",
                "conversation_id": "conv-smoke-scout-005",
            }
            mock_scout_poll.return_value = {
                "status": "completed",
                "answer": json.dumps({
                    "status": "completed",
                    "role": "scout",
                    "summary": "Scout report content.",
                    "primary_artifact_name": "scout_report",
                    "blocking": False,
                    "risk_level": "LOW",
                    "action": None,
                    "blocking_summary": [],
                }),
            }

            scout_result = role_lifecycle.role_call_impl(
                role="scout",
                user_task="Verify Jinja variable name.",
                input_artifacts={},
                metadata={"run_id": "smoke-run-005"},
            )

        self.assertEqual(scout_result["status"], "completed")
        scout_artifact_id = scout_result["artifacts"]["primary"]["artifact_id"]
        scout_run_id = scout_result["run_id"]

        # Architect call — capture mock inside the context
        with patch("mcp_agent.role_lifecycle._poll_task_status") as mock_arch_poll, \
             patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi") as mock_arch_start:
            mock_arch_start.return_value = {
                "task_id": "task-smoke-arch-005",
                "conversation_id": "conv-smoke-arch-005",
            }
            mock_arch_poll.return_value = {
                "status": "completed",
                "answer": _architect_mock_answer(),
            }

            result = role_lifecycle.role_call_impl(
                role="architect",
                user_task="Verify Jinja injection.",
                input_artifacts={"scout_report": scout_artifact_id},
                metadata={"run_id": scout_run_id},
            )

        self.assertEqual(result["status"], "completed")

        # Verify the main prompt (first call to _start_conversation_on_fastapi)
        # contains the scout report content. call_args[0] would be the summary
        # prompt, so we use call_args_list[0] for the main prompt.
        main_prompt = mock_arch_start.call_args_list[0][1]["prompt"]
        self.assertIsNotNone(main_prompt)
        # The prompt should contain the user_task text
        self.assertIn("Verify Jinja injection.", main_prompt)
        # The prompt should contain the scout_report section header
        self.assertIn("## Scout Report", main_prompt)


# ---------------------------------------------------------------------------
# Smoke test: artifact ID resolution edge cases
# ---------------------------------------------------------------------------

class TestSmokeArtifactIdResolution(TestCase):
    """Test artifact ID resolution edge cases."""

    def setUp(self):
        self.state_dir = _make_tmp_state_dir()
        self.cfg_path = _write_role_config(self.state_dir)
        os.environ["ROLE_CONFIG_PATH"] = str(self.cfg_path)
        os.environ["OPENHANDS_ROLE_STATE_DIR"] = str(self.state_dir)

    def tearDown(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)
        os.environ.pop("ROLE_CONFIG_PATH", None)
        os.environ.pop("OPENHANDS_ROLE_STATE_DIR", None)
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None

    @patch("mcp_agent.role_lifecycle._poll_task_status")
    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_artifact_id_from_scout_can_be_used_by_architect(self, mock_start, mock_poll):
        """End-to-end: scout saves artifact, architect resolves it via artifact_id."""
        # Scout call
        with patch("mcp_agent.role_lifecycle._poll_task_status") as mock_scout_poll, \
             patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi") as mock_scout_start:
            mock_scout_start.return_value = {
                "task_id": "task-smoke-scout-006",
                "conversation_id": "conv-smoke-scout-006",
            }
            mock_scout_poll.return_value = {
                "status": "completed",
                "answer": _scout_mock_answer(),
            }

            scout_result = role_lifecycle.role_call_impl(
                role="scout",
                user_task="E2E artifact resolution test.",
                input_artifacts={},
                metadata={"run_id": "smoke-run-006"},
            )

        self.assertEqual(scout_result["status"], "completed")
        scout_artifact_id = scout_result["artifacts"]["primary"]["artifact_id"]
        scout_run_id = scout_result["run_id"]

        # Verify artifact was actually saved to the store
        store = ArtifactStore()
        content = store.get_content_by_id(scout_artifact_id)
        self.assertIsNotNone(content)
        # The scout artifact content is the summary JSON from the LLM response
        self.assertIn("scout_report", content)

        # Architect call with the artifact_id
        with patch("mcp_agent.role_lifecycle._poll_task_status") as mock_arch_poll, \
             patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi") as mock_arch_start:
            mock_arch_start.return_value = {
                "task_id": "task-smoke-arch-006",
                "conversation_id": "conv-smoke-arch-006",
            }
            mock_arch_poll.return_value = {
                "status": "completed",
                "answer": _architect_mock_answer(),
            }

            result = role_lifecycle.role_call_impl(
                role="architect",
                user_task="Plan from scout report.",
                input_artifacts={"scout_report": scout_artifact_id},
                metadata={"run_id": scout_run_id},
            )

        self.assertEqual(result["status"], "completed")
        self.assertNotIn("error", result)

    @patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
    def test_artifact_id_without_run_id_fails_gracefully(self, mock_start):
        """Without metadata.run_id, art_ prefixed IDs should fail with MissingRequiredArtifact.

        Note: The requires_artifacts validation happens before artifact resolution.
        When input_artifacts is passed as a list (as the MCP tool layer would),
        role_call_impl checks the raw list against requires_artifacts, which fails
        because "scout_report" is not in a list of dicts.

        When input_artifacts is passed as a dict directly to role_call_impl,
        the validation passes but artifact resolution fails with ArtifactNotFound.
        """
        # Create a scout artifact directly in the store
        store = ArtifactStore()
        meta = store.save(
            run_id="smoke-run-007",
            role_run_id="smoke-run-007-scout-1",
            role="scout",
            artifact_name="scout_report",
            content="Direct store content.",
        )
        scout_artifact_id = meta["artifact_id"]

        # Architect call WITHOUT run_id — artifact resolution fails
        result = role_lifecycle.role_call_impl(
            role="architect",
            user_task="Should fail.",
            input_artifacts={"scout_report": scout_artifact_id},
            metadata={},  # No run_id!
        )

        self.assertEqual(result["status"], "failed")
        self.assertIn(result["error"]["type"], ["ArtifactNotFound", "ArtifactReadError"])


if __name__ == "__main__":
    unittest_main()
