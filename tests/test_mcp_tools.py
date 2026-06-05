#!/usr/bin/env python3
"""Tests for MCP tool logic in mcp_agent.server.

Uses unittest.mock to mock requests.post/get so no real services are needed.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Ensure the project root is on sys.path so imports work.
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestMcpTools(unittest.TestCase):
    """Unit tests for MCP tool functions."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_mcp_tools_")
        # Force the state dir so the store is isolated.
        os.environ["OPENHANDS_STATE_DIR"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_STATE_DIR", None)
        # Reset the module-level store so the next test gets a fresh one.
        import mcp_agent.server as server_mod
        server_mod._store = None

    # -- openhands_start_task -----------------------------------------------

    @patch("mcp_agent.server.requests.post")
    def test_start_task_returns_task_id_quickly(self, mock_post):
        """Starting a task returns task_id without waiting for completion."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "conversation_id": "conv-mock-123",
            "status": "no_wait",
            "answer": "",
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        from mcp_agent.server import openhands_start_task

        result = openhands_start_task(
            prompt="Test prompt",
            api_key="test-key",
        )

        self.assertEqual(result["status"], "running")
        self.assertIn("task_id", result)
        self.assertEqual(result["conversation_id"], "conv-mock-123")
        self.assertIn("Poll with openhands_get_task_status", result.get("message", ""))

        # Verify requests.post was called with no_wait=True.
        call_args = mock_post.call_args
        payload = call_args[1]["json"]
        self.assertTrue(payload["no_wait"])

    @patch("mcp_agent.server.requests.post")
    def test_start_task_handles_timeout(self, mock_post):
        """OpenHands API timeout is handled as retryable error."""
        import requests as req

        mock_post.side_effect = req.exceptions.Timeout("Connection timed out")

        from mcp_agent.server import openhands_start_task

        result = openhands_start_task(
            prompt="Test prompt",
            api_key="test-key",
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "RequestTimeout")
        self.assertTrue(result["error"]["retryable"])

    @patch("mcp_agent.server.requests.post")
    def test_start_task_handles_connection_error(self, mock_post):
        """Connection error is handled as retryable."""
        import requests as req

        mock_post.side_effect = req.exceptions.ConnectionError("No route to host")

        from mcp_agent.server import openhands_start_task

        result = openhands_start_task(
            prompt="Test prompt",
            api_key="test-key",
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "ConnectionError")
        self.assertTrue(result["error"]["retryable"])

    @patch("mcp_agent.server.requests.post")
    def test_idempotency_key_prevents_duplicate(self, mock_post):
        """Idempotency key returns existing task instead of creating duplicate."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "conversation_id": "conv-dup-123",
            "status": "no_wait",
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        from mcp_agent.server import openhands_start_task

        # First call creates the task.
        result1 = openhands_start_task(
            prompt="Test prompt",
            api_key="test-key",
            idempotency_key="idem-abc",
        )
        self.assertEqual(result1["status"], "running")
        task_id_1 = result1["task_id"]

        # Second call with same key returns the existing task.
        result2 = openhands_start_task(
            prompt="Different prompt",
            api_key="test-key",
            idempotency_key="idem-abc",
        )
        self.assertEqual(result2["task_id"], task_id_1)
        self.assertIn("Idempotent match", result2.get("message", ""))

        # requests.post should have been called only once.
        self.assertEqual(mock_post.call_count, 1)

    # -- openhands_get_task_status -------------------------------------------

    @patch("mcp_agent.server.requests.get")
    def test_get_status_unknown_task_returns_error(self, mock_get):
        """Polling an unknown task returns clear error."""
        from mcp_agent.server import openhands_get_task_status

        result = openhands_get_task_status(task_id="unknown-task-id")

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "UnknownTaskId")
        self.assertFalse(result["error"]["retryable"])

    @patch("mcp_agent.server.requests.get")
    def test_get_status_running_task_returns_running(self, mock_get):
        """Running task remains running when no final answer exists."""
        from mcp_agent.server import openhands_start_task, openhands_get_task_status

        # Create a task first.
        with patch("mcp_agent.server.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "conversation_id": "conv-running",
                "status": "no_wait",
            }
            mock_resp.raise_for_status = MagicMock()
            mock_post.return_value = mock_resp

            start_result = openhands_start_task(prompt="Test", api_key="test-key")
            task_id = start_result["task_id"]

        # Now mock the status poll.
        mock_get.return_value.json.return_value = {
            "conversation_id": "conv-running",
            "status": "running",
            "answer": "",
            "execution_status": "running",
        }
        mock_get.return_value.raise_for_status = MagicMock()

        result = openhands_get_task_status(task_id=task_id)
        self.assertEqual(result["status"], "running")
        self.assertIn("conv-running", result.get("conversation_id", ""))

    @patch("mcp_agent.server.requests.get")
    def test_get_status_completed_task_returns_answer(self, mock_get):
        """Completed task returns final answer."""
        from mcp_agent.server import openhands_start_task, openhands_get_task_status

        # Create a task.
        with patch("mcp_agent.server.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "conversation_id": "conv-done",
                "status": "no_wait",
            }
            mock_resp.raise_for_status = MagicMock()
            mock_post.return_value = mock_resp

            start_result = openhands_start_task(
                prompt="Test", api_key="test-key"
            )
            task_id = start_result["task_id"]

        # Mock the status poll returning completed.
        mock_get.return_value.json.return_value = {
            "conversation_id": "conv-done",
            "status": "completed",
            "answer": "This is the final answer.",
            "execution_status": "finished",
        }
        mock_get.return_value.raise_for_status = MagicMock()

        result = openhands_get_task_status(task_id=task_id)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["answer"], "This is the final answer.")

    # -- openhands_get_task_result -------------------------------------------

    @patch("mcp_agent.server.requests.get")
    def test_get_result_completed_task_returns_answer(self, mock_get):
        """Get result for a completed task returns answer."""
        from mcp_agent.server import openhands_start_task, openhands_get_task_result

        # Create a task.
        with patch("mcp_agent.server.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "conversation_id": "conv-result",
                "status": "no_wait",
            }
            mock_resp.raise_for_status = MagicMock()
            mock_post.return_value = mock_resp

            start_result = openhands_start_task(
                prompt="Test", api_key="test-key"
            )
            task_id = start_result["task_id"]

        # Mark task as completed in the store.
        from mcp_agent.server import _get_store
        store = _get_store()
        store.update_task(
            task_id,
            status="completed",
            result={
                "answer": "Result answer text",
                "completed_at": "2025-01-01T00:00:00+00:00",
                "duration_seconds": 3600,
            },
        )

        result = openhands_get_task_result(task_id=task_id)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["answer"], "Result answer text")

    @patch("mcp_agent.server.requests.get")
    def test_get_result_running_task_returns_still_running(self, mock_get):
        """Get result for a running task returns 'still running'."""
        from mcp_agent.server import openhands_start_task, openhands_get_task_result

        # Create a task.
        with patch("mcp_agent.server.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "conversation_id": "conv-still-running",
                "status": "no_wait",
            }
            mock_resp.raise_for_status = MagicMock()
            mock_post.return_value = mock_resp

            start_result = openhands_start_task(
                prompt="Test", api_key="test-key"
            )
            task_id = start_result["task_id"]

        result = openhands_get_task_result(task_id=task_id)
        self.assertEqual(result["status"], "running")
        self.assertIn("still running", result.get("message", "").lower())

    # -- openhands_get_task_events -------------------------------------------

    @patch("mcp_agent.server.requests.get")
    def test_get_events_unknown_task_returns_error(self, mock_get):
        """Get events for unknown task returns clear error."""
        from mcp_agent.server import openhands_get_task_events

        result = openhands_get_task_events(task_id="unknown-task-id")

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "UnknownTaskId")

    @patch("mcp_agent.server.requests.get")
    def test_get_events_returns_events_list(self, mock_get):
        """Get events returns events list from FastAPI."""
        from mcp_agent.server import openhands_start_task, openhands_get_task_events

        # Create a task.
        with patch("mcp_agent.server.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "conversation_id": "conv-events",
                "status": "no_wait",
            }
            mock_resp.raise_for_status = MagicMock()
            mock_post.return_value = mock_resp

            start_result = openhands_start_task(
                prompt="Test", api_key="test-key"
            )
            task_id = start_result["task_id"]

        mock_get.return_value.json.return_value = {
            "events": [
                {"id": "evt-1", "kind": "MessageEvent", "source": "agent"},
                {"id": "evt-2", "kind": "MessageEvent", "source": "user"},
            ],
            "count": 2,
        }
        mock_get.return_value.raise_for_status = MagicMock()

        result = openhands_get_task_events(task_id=task_id)
        self.assertEqual(result["count"], 2)
        self.assertEqual(len(result["events"]), 2)

    # -- openhands_cancel_task -----------------------------------------------

    @patch("mcp_agent.server.requests.post")
    def test_cancel_terminal_task_noop(self, mock_post):
        """Cancelling a terminal task is a no-op."""
        from mcp_agent.server import openhands_start_task

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "conversation_id": "conv-cancel",
            "status": "no_wait",
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        start_result = openhands_start_task(
            prompt="Test", api_key="test-key"
        )
        task_id = start_result["task_id"]

        # Mark as completed.
        from mcp_agent.server import _get_store
        store = _get_store()
        store.update_task(task_id, status="completed")

        from mcp_agent.server import openhands_cancel_task
        result = openhands_cancel_task(task_id=task_id)
        self.assertEqual(result["status"], "completed")
        self.assertIn("already in terminal state", result.get("message", ""))

    # -- call_llm backward compatibility -------------------------------------

    @patch("mcp_agent.server.requests.post")
    def test_call_llm_no_wait_returns_task_id(self, mock_post):
        """call_llm with conversation_id uses existing conv path (no_wait implied)."""
        from mcp_agent.server import call_llm

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "conversation_id": "conv-bc-123",
            "status": "completed",
            "answer": "existing answer",
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        result = call_llm(
            prompt="Test prompt",
            api_key="test-key",
            conversation_id="conv-bc-123",
        )

        # With conversation_id the direct FastAPI path is used.
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["conversation_id"], "conv-bc-123")

    @patch("mcp_agent.server.requests.post")
    def test_call_llm_default_is_nonblocking(self, mock_post):
        """call_llm default (no wait_seconds) returns task_id, not blocking."""
        from mcp_agent.server import call_llm

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "conversation_id": "conv-nb-456",
            "status": "no_wait",
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        result = call_llm(
            prompt="Test prompt",
            api_key="test-key",
        )

        # Default is now non-blocking: returns task_id.
        self.assertIn("task_id", result)
        self.assertIn("Poll with openhands_get_task_status", result.get("message", ""))


class TestTaskStorePersistence(unittest.TestCase):
    """Test that persisted task can be read back from storage."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_persist_")
        os.environ["OPENHANDS_STATE_DIR"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_STATE_DIR", None)
        import mcp_agent.server as server_mod
        server_mod._store = None

    def test_persisted_task_read_back(self):
        """Task state survives process restart (file-based persistence)."""
        from mcp_agent.server import openhands_start_task

        # First "process": create a task.
        with patch("mcp_agent.server.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "conversation_id": "conv-persist-test",
                "status": "no_wait",
            }
            mock_resp.raise_for_status = MagicMock()
            mock_post.return_value = mock_resp

            start_result = openhands_start_task(
                prompt="Persist test", api_key="test-key"
            )
            task_id = start_result["task_id"]

        # Simulate process restart: clear the module-level store.
        import mcp_agent.server as server_mod
        server_mod._store = None

        # Second "process": read the task back.
        from mcp_agent.server import _get_store
        store2 = _get_store()
        reloaded = store2.get_task(task_id)

        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded["task_id"], task_id)
        self.assertEqual(reloaded["conversation_id"], "conv-persist-test")
        self.assertEqual(reloaded["prompt"], "Persist test")


class TestMcpHostPortConfig(unittest.TestCase):
    """Test MCP host/port configuration parsing."""

    def test_mcp_host_port_defaults(self):
        """Default MCP_HOST is 127.0.0.1 and MCP_PORT is 8000."""
        # Clear any env vars that might be set
        os.environ.pop("MCP_HOST", None)
        os.environ.pop("MCP_PORT", None)
        # Re-import to get fresh module-level values
        import importlib
        import mcp_agent.server as server_mod
        importlib.reload(server_mod)
        self.assertEqual(server_mod.MCP_HOST, "127.0.0.1")
        self.assertEqual(server_mod.MCP_PORT, 8000)

    def test_mcp_host_port_from_env(self):
        """MCP_HOST=0.0.0.0 and MCP_PORT=8000 are parsed correctly."""
        os.environ["MCP_HOST"] = "0.0.0.0"
        os.environ["MCP_PORT"] = "8000"
        import importlib
        import mcp_agent.server as server_mod
        importlib.reload(server_mod)
        self.assertEqual(server_mod.MCP_HOST, "0.0.0.0")
        self.assertEqual(server_mod.MCP_PORT, 8000)

    def test_mcp_host_port_custom_values(self):
        """Custom MCP_HOST and MCP_PORT are parsed correctly."""
        os.environ["MCP_HOST"] = "192.168.1.100"
        os.environ["MCP_PORT"] = "9999"
        import importlib
        import mcp_agent.server as server_mod
        importlib.reload(server_mod)
        self.assertEqual(server_mod.MCP_HOST, "192.168.1.100")
        self.assertEqual(server_mod.MCP_PORT, 9999)


class TestNoRepoInPayload(unittest.TestCase):
    """Test that repo/branch are not included in FastAPI payload."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_no_repo_")
        os.environ["OPENHANDS_STATE_DIR"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_STATE_DIR", None)
        import mcp_agent.server as server_mod
        server_mod._store = None

    @patch("mcp_agent.server.requests.post")
    def test_start_task_no_repo_in_payload(self, mock_post):
        """_start_conversation_on_fastapi does not include repo/branch in payload."""
        from mcp_agent.server import _start_conversation_on_fastapi

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "conversation_id": "conv-no-repo",
            "status": "no_wait",
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        _start_conversation_on_fastapi(
            prompt="Test prompt",
            api_key="test-key",
            llm_model="gpt-4",
            repo="owner/repo",  # Even with repo passed, it should not be in payload
            branch="main",
        )

        # Verify the payload sent to FastAPI
        call_args = mock_post.call_args
        payload = call_args[1]["json"]
        self.assertNotIn("repo", payload)
        self.assertNotIn("branch", payload)
        self.assertEqual(payload["prompt"], "Test prompt")
        self.assertTrue(payload["no_wait"])


if __name__ == "__main__":
    unittest.main()
