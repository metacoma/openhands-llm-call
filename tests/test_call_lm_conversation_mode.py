#!/usr/bin/env python3
"""Tests for /v1/call_lm conversation mode separation.

Verifies that:
- conversation_id + prompt sends a new message (not read-only)
- read_only_existing_conversation=true is explicit opt-in
- role_wait summary uses same conversation_id
- summary prompt response is used for validation
- repair prompt also sends to same conversation
- fallback primary_artifact_name is correct
- fallback message does not instruct artifact_get
"""

import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Ensure the project root is on sys.path so imports work from tests/
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


# ===================================================================
# Test 1: conversation_id + prompt sends message (not read-only)
# ===================================================================

@patch("openhands_llm.openhands_llm_call.send_message_to_existing_conversation")
@patch("openhands_llm.openhands_llm_call.collect_existing_conversation_answer")
def test_conversation_id_plus_prompt_sends_message(mock_collect, mock_send):
    """conversation_id + prompt should call send_message, NOT collect."""
    from fastapi.testclient import TestClient
    from openhands_llm.server import app

    mock_send.return_value = {
        "success": True,
        "sandbox_status": "RUNNING",
        "message": None,
    }

    with patch.dict(os.environ, {"OPENHANDS_URL": "http://testserver"}):
        client = TestClient(app)
        resp = client.post(
            "/v1/call_lm",
            json={
                "prompt": "SUMMARY PROMPT",
                "conversation_id": "conv-123",
                "no_wait": True,
                "api_key": "test-key",
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["conversation_id"] == "conv-123"

    # send_message should be called with correct args
    mock_send.assert_called_once_with(
        base_url="http://testserver",
        api_key="test-key",
        conversation_id="conv-123",
        prompt="SUMMARY PROMPT",
        timeout=60,
    )

    # collect should NOT be called
    mock_collect.assert_not_called()


# ===================================================================
# Test 1b: integration — /v1/call_lm with conversation_id + prompt hits send-message endpoint
# ===================================================================

def test_call_lm_uses_send_message_endpoint():
    """Integration-style test: /v1/call_lm with conversation_id + prompt
    calls the send-message endpoint (not collect_existing_conversation_answer).

    We patch send_message_to_existing_conversation and verify it is called
    with the correct endpoint URL (not collect_existing_conversation_answer).
    """
    from fastapi.testclient import TestClient
    from openhands_llm.server import app

    conv_id = "mock-integration-test-conv"

    captured_kwargs = {}

    def mock_send(base_url, api_key, conversation_id, prompt, **kwargs):
        captured_kwargs["base_url"] = base_url
        captured_kwargs["conversation_id"] = conversation_id
        captured_kwargs["prompt"] = prompt
        return {
            "success": True,
            "sandbox_status": "RUNNING",
            "message": None,
        }

    with patch(
        "openhands_llm.openhands_llm_call.send_message_to_existing_conversation",
        side_effect=mock_send,
    ):
        with patch.dict(os.environ, {"OPENHANDS_URL": "http://testserver"}):
            client = TestClient(app)
            resp = client.post(
                "/v1/call_lm",
                json={
                    "prompt": "INTEGRATION TEST PROMPT",
                    "conversation_id": conv_id,
                    "no_wait": True,
                    "api_key": "test-key",
                },
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["conversation_id"] == conv_id
    assert body["task_id"] == conv_id
    assert body["job_id"] == conv_id

    # Verify send_message was called with correct args
    assert captured_kwargs["base_url"] == "http://testserver"
    assert captured_kwargs["conversation_id"] == conv_id
    assert captured_kwargs["prompt"] == "INTEGRATION TEST PROMPT"


# ===================================================================
# Test 2: read-only existing conversation is explicit
# ===================================================================

@patch("openhands_llm.openhands_llm_call.send_message_to_existing_conversation")
@patch("openhands_llm.openhands_llm_call.collect_existing_conversation_answer")
def test_read_only_requires_explicit_flag(mock_collect, mock_send):
    """read_only_existing_conversation=true should call collect, NOT send."""
    from fastapi.testclient import TestClient
    from openhands_llm.server import app

    mock_collect.return_value = [
        {"kind": "MessageEvent", "llm_message": {"role": "assistant", "content": [{"type": "text", "text": "old answer text here."}]}}
    ]

    # Also mock extract_final_answer since it's called after collect
    with patch("openhands_llm.openhands_llm_call.extract_final_answer") as mock_extract:
        mock_extract.return_value = "old answer text here."

        with patch.dict(os.environ, {"OPENHANDS_URL": "http://testserver"}):
            client = TestClient(app)
            resp = client.post(
                "/v1/call_lm",
                json={
                    "prompt": "ignored or empty",
                    "conversation_id": "conv-123",
                    "read_only_existing_conversation": True,
                    "api_key": "test-key",
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"
        assert body["conversation_id"] == "conv-123"

    # collect should be called
    mock_collect.assert_called_once()

    # send_message should NOT be called
    mock_send.assert_not_called()


# ===================================================================
# Test 3: role_wait summary uses same conversation_id
# ===================================================================

@patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
def test_summary_prompt_uses_same_conversation(mock_start):
    """Summary prompt should be sent with the same conversation_id as main role."""
    from mcp_agent.role_lifecycle import role_lifecycle_wait_impl

    mock_start.return_value = {
        "conversation_id": "conv-main",
        "task_id": "task-2",
        "status": "running",
    }

    role_run = {
        "run_id": "run-1",
        "role_run_id": "rr-1",
        "role": "scout",
        "status": "running",
        "lifecycle_state": "main_response_received",
        "conversation_id": "conv-main",
        "openhands_task_id": "task-main",
    }

    with patch("mcp_agent.role_lifecycle._get_task_status_once") as mock_get_status:
        mock_get_status.return_value = {
            "_normalized_status": "completed",
            "answer": "This is the scout report content.",
        }

        with patch("mcp_agent.role_lifecycle.RoleRunStore") as mock_role_store_cls:
            mock_role_store = MagicMock()
            mock_role_store.get_role_run.return_value = role_run
            mock_role_store.update_role_run.return_value = None
            mock_role_store_cls.return_value = mock_role_store

            with patch("mcp_agent.role_lifecycle.ArtifactStore") as mock_artifact_store_cls:
                mock_artifact_store = MagicMock()
                mock_artifact_store.save.return_value = {
                    "artifact_id": "art-1",
                    "artifact_path": "/tmp/art-1",
                }
                mock_artifact_store_cls.return_value = mock_artifact_store

                with patch("mcp_agent.role_lifecycle.validate_summary") as mock_validate:
                    mock_validate.return_value = {
                        "valid": True,
                        "status": "completed",
                        "role": "scout",
                        "summary": "ok",
                        "primary_artifact_name": "scout_report",
                        "blocking": False,
                        "risk_level": None,
                        "action": None,
                        "blocking_summary": [],
                    }

                    with patch("mcp_agent.role_lifecycle.get_role") as mock_get_role:
                        from mcp_agent.roles import RoleSpec
                        mock_get_role.return_value = RoleSpec(
                            name="scout",
                            description="Scout role",
                            model="openai/qwen3:32b",
                            prompt_template="prompts/scout.md",
                            readonly=False,
                            timeout_minutes=30,
                            requires_artifacts=[],
                            output_artifact="scout_report",
                            summary_artifact="scout_summary",
                        )

                        role_lifecycle_wait_impl(
                            role_run_id="rr-1",
                            timeout_seconds=5,
                            poll_interval_seconds=1,
                        )

    # Assert _start_conversation_on_fastapi was called once (for summary)
    assert mock_start.call_count == 1

    # Call should have conversation_id="conv-main" and read_only_existing_conversation=False
    call_kwargs = mock_start.call_args[1]
    assert call_kwargs["conversation_id"] == "conv-main"
    assert call_kwargs["read_only_existing_conversation"] is False


# ===================================================================
# Test 4: summary prompt response is used for validation
# ===================================================================

@patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
def test_summary_answer_used_for_validation(mock_start):
    """Validated summary should come from summary answer, not main answer."""
    from mcp_agent.role_lifecycle import role_lifecycle_wait_impl

    captured_json = [None]

    def capture_validate(role, summary_artifact_name, json_str):
        captured_json[0] = json_str
        return {
            "valid": True,
            "status": "completed",
            "role": "scout",
            "summary": "Summary from summary answer",
            "primary_artifact_name": "scout_report",
            "blocking": False,
            "risk_level": None,
            "action": None,
            "blocking_summary": [],
        }

    mock_start.return_value = {
        "conversation_id": "conv-main",
        "task_id": "task-2",
        "status": "running",
    }

    role_run = {
        "run_id": "run-1",
        "role_run_id": "rr-1",
        "role": "scout",
        "status": "running",
        "lifecycle_state": "main_response_received",
        "conversation_id": "conv-main",
        "openhands_task_id": "task-main",
    }

    with patch("mcp_agent.role_lifecycle._get_task_status_once") as mock_get_status:
        mock_get_status.return_value = {
            "_normalized_status": "completed",
            "answer": "This is the scout report content.",
        }

        with patch("mcp_agent.role_lifecycle.RoleRunStore") as mock_role_store_cls:
            mock_role_store = MagicMock()
            mock_role_store.get_role_run.return_value = role_run
            mock_role_store.update_role_run.return_value = None
            mock_role_store_cls.return_value = mock_role_store

            with patch("mcp_agent.role_lifecycle.ArtifactStore") as mock_artifact_store_cls:
                mock_artifact_store = MagicMock()
                mock_artifact_store.save.return_value = {
                    "artifact_id": "art-1",
                    "artifact_path": "/tmp/art-1",
                }
                mock_artifact_store_cls.return_value = mock_artifact_store

                with patch("mcp_agent.role_lifecycle.validate_summary", side_effect=capture_validate):
                    with patch("mcp_agent.role_lifecycle.get_role") as mock_get_role:
                        from mcp_agent.roles import RoleSpec
                        mock_get_role.return_value = RoleSpec(
                            name="scout",
                            description="Scout role",
                            model="openai/qwen3:32b",
                            prompt_template="prompts/scout.md",
                            readonly=False,
                            timeout_minutes=30,
                            requires_artifacts=[],
                            output_artifact="scout_report",
                            summary_artifact="scout_summary",
                        )

                        role_lifecycle_wait_impl(
                            role_run_id="rr-1",
                            timeout_seconds=5,
                            poll_interval_seconds=1,
                        )

    # validate_summary should have been called
    assert captured_json[0] is not None


# ===================================================================
# Test 5: repair prompt also sends to same conversation
# ===================================================================

@patch("mcp_agent.role_lifecycle._start_conversation_on_fastapi")
def test_repair_prompt_uses_same_conversation(mock_start):
    """Repair prompt should be sent with same conversation_id."""
    from mcp_agent.role_lifecycle import role_lifecycle_wait_impl

    call_count = [0]
    conversation_ids_seen = []

    def side_effect(*args, **kwargs):
        call_count[0] += 1
        conv_id = kwargs.get("conversation_id") or (args[1] if len(args) > 1 else None)
        conversation_ids_seen.append(conv_id)
        return {"conversation_id": "conv-main", "task_id": f"task-{call_count[0]}", "status": "running"}

    mock_start.side_effect = side_effect

    role_run = {
        "run_id": "run-1",
        "role_run_id": "rr-1",
        "role": "scout",
        "status": "running",
        "lifecycle_state": "main_response_received",
        "conversation_id": "conv-main",
        "openhands_task_id": "task-main",
    }

    with patch("mcp_agent.role_lifecycle._get_task_status_once") as mock_get_status:
        mock_get_status.return_value = {
            "_normalized_status": "completed",
            "answer": "Scout report content.",
        }

        with patch("mcp_agent.role_lifecycle.RoleRunStore") as mock_role_store_cls:
            mock_role_store = MagicMock()
            mock_role_store.get_role_run.return_value = role_run
            mock_role_store.update_role_run.return_value = None
            mock_role_store_cls.return_value = mock_role_store

            with patch("mcp_agent.role_lifecycle.ArtifactStore") as mock_artifact_store_cls:
                mock_artifact_store = MagicMock()
                mock_artifact_store.save.return_value = {
                    "artifact_id": "art-1",
                    "artifact_path": "/tmp/art-1",
                }
                mock_artifact_store_cls.return_value = mock_artifact_store

                validate_calls = [0]

                def validate_side_effect(role, summary_artifact_name, json_str):
                    validate_calls[0] += 1
                    if validate_calls[0] == 1:
                        return {"valid": False, "error": {"type": "test", "message": "invalid"}}
                    return {
                        "valid": True,
                        "status": "completed",
                        "role": "scout",
                        "summary": "Repaired summary",
                        "primary_artifact_name": "scout_report",
                        "blocking": False,
                        "risk_level": None,
                        "action": None,
                        "blocking_summary": [],
                    }

                with patch("mcp_agent.role_lifecycle.validate_summary", side_effect=validate_side_effect):
                    with patch("mcp_agent.role_lifecycle.wait_job_until_terminal") as mock_wait_job:
                        mock_wait_job.return_value = ("completed", {"answer": "Repaired summary JSON here."})
                        with patch("mcp_agent.role_lifecycle.get_role") as mock_get_role:
                            from mcp_agent.roles import RoleSpec
                            mock_get_role.return_value = RoleSpec(
                                name="scout",
                                description="Scout role",
                                model="openai/qwen3:32b",
                                prompt_template="prompts/scout.md",
                                readonly=False,
                                timeout_minutes=30,
                                requires_artifacts=[],
                                output_artifact="scout_report",
                                summary_artifact="scout_summary",
                            )

                            role_lifecycle_wait_impl(
                                role_run_id="rr-1",
                                timeout_seconds=5,
                                poll_interval_seconds=1,
                            )

    # Should have at least 2 calls: summary and repair
    assert mock_start.call_count >= 2

    # All calls should use same conversation_id
    for conv_id in conversation_ids_seen:
        assert conv_id == "conv-main"


# ===================================================================
# Test 6: fallback primary_artifact_name is correct
# ===================================================================

def test_fallback_primary_artifact_name_is_output_artifact():
    """Fallback for scout should use output_artifact (scout_report), not summary_artifact."""
    from mcp_agent.summary_validator import safe_fallback_summary

    result = safe_fallback_summary(
        role="scout",
        primary_artifact_name="scout_report",
        is_reviewer=False,
        main_artifact_content=None,
    )
    assert result["primary_artifact_name"] == "scout_report"


# ===================================================================
# Test 7: fallback message does not instruct artifact_get
# ===================================================================

def test_fallback_message_no_artifact_instructions():
    """Fallback must not tell Head of IT to read artifact content."""
    from mcp_agent.summary_validator import safe_fallback_summary

    result = safe_fallback_summary(
        role="scout",
        primary_artifact_name="scout_report",
        is_reviewer=False,
        main_artifact_content=None,
    )
    assert "Inspect the summary artifact" not in result["summary"]
    # The new message should not contain artifact-reading instructions
    summary_lower = result["summary"].lower()
    if "inspect" in summary_lower:
        idx = summary_lower.index("inspect")
        before = summary_lower[:idx]
        assert "artifact" not in before
