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


class TestPromptNormalization(unittest.TestCase):
    """Tests for _normalize_text_arg and role_start prompt compatibility."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_prompt_norm_")
        os.environ["OPENHANDS_STATE_DIR"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_STATE_DIR", None)
        import mcp_agent.server as server_mod
        server_mod._store = None

    # -- _normalize_text_arg direct tests -----------------------------------

    def test_normalize_text_arg_plain_string(self):
        """_normalize_text_arg('x') returns 'x'."""
        from mcp_agent.server import _normalize_text_arg

        self.assertEqual(_normalize_text_arg("x"), "x")

    def test_normalize_text_arg_dict_text(self):
        """_normalize_text_arg({'text': 'x'}) returns 'x'."""
        from mcp_agent.server import _normalize_text_arg

        self.assertEqual(_normalize_text_arg({"text": "x"}), "x")

    def test_normalize_text_arg_dict_prompt(self):
        """_normalize_text_arg({'prompt': 'x'}) returns 'x'."""
        from mcp_agent.server import _normalize_text_arg

        self.assertEqual(_normalize_text_arg({"prompt": "x"}), "x")

    def test_normalize_text_arg_dict_value(self):
        """_normalize_text_arg({'value': 'x'}) returns 'x'."""
        from mcp_agent.server import _normalize_text_arg

        self.assertEqual(_normalize_text_arg({"value": "x"}), "x")

    def test_normalize_text_arg_dict_user_task(self):
        """_normalize_text_arg({'user_task': 'x'}) returns 'x'."""
        from mcp_agent.server import _normalize_text_arg

        self.assertEqual(_normalize_text_arg({"user_task": "x"}), "x")

    def test_normalize_text_arg_dict_task(self):
        """_normalize_text_arg({'task': 'x'}) returns 'x'."""
        from mcp_agent.server import _normalize_text_arg

        self.assertEqual(_normalize_text_arg({"task": "x"}), "x")

    def test_normalize_text_arg_none(self):
        """_normalize_text_arg(None) returns None."""
        from mcp_agent.server import _normalize_text_arg

        self.assertIsNone(_normalize_text_arg(None))

    def test_normalize_text_arg_empty_dict(self):
        """_normalize_text_arg({}) returns str({})."""
        from mcp_agent.server import _normalize_text_arg

        # Falls back to str() for dicts without known keys
        self.assertEqual(_normalize_text_arg({}), "{}")

    def test_normalize_text_arg_int(self):
        """_normalize_text_arg(42) returns '42'."""
        from mcp_agent.server import _normalize_text_arg

        self.assertEqual(_normalize_text_arg(42), "42")

    # -- role_start with plain string prompt --------------------------------

    @patch("mcp_agent.server._role_tools.role_start_impl")
    def test_role_start_plain_string_prompt(self, mock_impl):
        """role_start with plain string prompt works and passes normalized text."""
        mock_impl.return_value = {"status": "running", "run_id": "test-run-1"}

        from mcp_agent.server import role_start

        result = role_start(role="scout", prompt="Analyze repository ...")

        self.assertEqual(result["status"], "running")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertEqual(call_kwargs["user_task"], "Analyze repository ...")

    # -- role_start with wrapped prompt object ------------------------------

    @patch("mcp_agent.server._role_tools.role_start_impl")
    def test_role_start_wrapped_prompt_object(self, mock_impl):
        """role_start with {'text': '...'} prompt normalizes correctly."""
        mock_impl.return_value = {"status": "running", "run_id": "test-run-2"}

        from mcp_agent.server import role_start

        result = role_start(
            role="scout", prompt={"text": "Analyze repository ..."}
        )

        self.assertEqual(result["status"], "running")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertEqual(call_kwargs["user_task"], "Analyze repository ...")

    # -- role_start with wrapped user_task object ---------------------------

    @patch("mcp_agent.server._role_tools.role_start_impl")
    def test_role_start_wrapped_user_task_object(self, mock_impl):
        """role_start with {'text': '...'} user_task normalizes correctly."""
        mock_impl.return_value = {"status": "running", "run_id": "test-run-3"}

        from mcp_agent.server import role_start

        result = role_start(
            role="scout", user_task={"text": "Analyze repository ..."}
        )

        self.assertEqual(result["status"], "running")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertEqual(call_kwargs["user_task"], "Analyze repository ...")

    # -- role_start with prompt taking precedence over user_task ------------

    @patch("mcp_agent.server._role_tools.role_start_impl")
    def test_role_start_prompt_takes_precedence(self, mock_impl):
        """When both prompt and user_task are provided, prompt takes precedence."""
        mock_impl.return_value = {"status": "running", "run_id": "test-run-4"}

        from mcp_agent.server import role_start

        result = role_start(
            role="scout",
            prompt={"text": "prompt value"},
            user_task={"text": "user_task value"},
        )

        self.assertEqual(result["status"], "running")
        call_kwargs = mock_impl.call_args[1]
        self.assertEqual(call_kwargs["user_task"], "prompt value")

    # -- role_start with missing prompt -------------------------------------

    def test_role_start_missing_prompt_returns_error(self):
        """role_start with None prompt and user_task returns MissingPrompt error."""
        from mcp_agent.server import role_start

        result = role_start(role="scout", prompt=None, user_task=None)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "MissingPrompt")
        self.assertFalse(result["error"]["retryable"])

    # -- role_start with empty string prompt --------------------------------

    @patch("mcp_agent.server._role_tools.role_start_impl")
    def test_role_start_empty_string_prompt(self, mock_impl):
        """role_start with empty string prompt returns MissingPrompt error."""
        from mcp_agent.server import role_start

        result = role_start(role="scout", prompt="")

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "MissingPrompt")

    # -- FastMCP host/port config tests -------------------------------------

    def test_fastmcp_host_from_env(self):
        """FASTMCP_HOST env var is read correctly."""
        os.environ["FASTMCP_HOST"] = "0.0.0.0"
        os.environ["FASTMCP_PORT"] = "8000"
        import importlib
        import mcp_agent.server as server_mod
        importlib.reload(server_mod)
        self.assertEqual(server_mod._fastmcp_host, "0.0.0.0")
        self.assertEqual(server_mod._fastmcp_port, 8000)

    def test_fastmcp_host_fallback_to_mcp_host(self):
        """FASTMCP_HOST falls back to MCP_HOST when not set."""
        os.environ.pop("FASTMCP_HOST", None)
        os.environ.pop("FASTMCP_PORT", None)
        os.environ["MCP_HOST"] = "192.168.1.100"
        os.environ["MCP_PORT"] = "9999"
        import importlib
        import mcp_agent.server as server_mod
        importlib.reload(server_mod)
        self.assertEqual(server_mod._fastmcp_host, "192.168.1.100")
        self.assertEqual(server_mod._fastmcp_port, 9999)

    def test_fastmcp_host_precedence_over_mcp(self):
        """FASTMCP_HOST takes precedence over MCP_HOST."""
        os.environ["FASTMCP_HOST"] = "10.0.0.1"
        os.environ["FASTMCP_PORT"] = "7777"
        os.environ["MCP_HOST"] = "192.168.1.100"
        os.environ["MCP_PORT"] = "9999"
        import importlib
        import mcp_agent.server as server_mod
        importlib.reload(server_mod)
        self.assertEqual(server_mod._fastmcp_host, "10.0.0.1")
        self.assertEqual(server_mod._fastmcp_port, 7777)


class TestWrappedScalarArgs(unittest.TestCase):
    """Tests for OpenHands-wrapped scalar argument compatibility.

    OpenHands may serialize scalar arguments as objects like:
        {"default": 1800}  instead of  1800
        {"default": "x"}   instead of  "x"

    These tests verify that all affected MCP tools accept both plain scalars
    and wrapped dict values.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_wrapped_scalar_")
        os.environ["OPENHANDS_STATE_DIR"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_STATE_DIR", None)
        import mcp_agent.server as server_mod
        server_mod._store = None

    # -- _unwrap_arg direct tests -------------------------------------------

    def test_unwrap_arg_default_key(self):
        """_unwrap_arg({'default': 'x'}) returns 'x'."""
        from mcp_agent.server import _unwrap_arg

        self.assertEqual(_unwrap_arg({"default": "x"}), "x")

    def test_unwrap_arg_value_key(self):
        """_unwrap_arg({'value': 'x'}) returns 'x'."""
        from mcp_agent.server import _unwrap_arg

        self.assertEqual(_unwrap_arg({"value": "x"}), "x")

    def test_unwrap_arg_text_key(self):
        """_unwrap_arg({'text': 'x'}) returns 'x'."""
        from mcp_agent.server import _unwrap_arg

        self.assertEqual(_unwrap_arg({"text": "x"}), "x")

    def test_unwrap_arg_prompt_key(self):
        """_unwrap_arg({'prompt': 'x'}) returns 'x'."""
        from mcp_agent.server import _unwrap_arg

        self.assertEqual(_unwrap_arg({"prompt": "x"}), "x")

    def test_unwrap_arg_name_key(self):
        """_unwrap_arg({'name': 'x'}) returns 'x'."""
        from mcp_agent.server import _unwrap_arg

        self.assertEqual(_unwrap_arg({"name": "x"}), "x")

    def test_unwrap_arg_id_key(self):
        """_unwrap_arg({'id': 'x'}) returns 'x'."""
        from mcp_agent.server import _unwrap_arg

        self.assertEqual(_unwrap_arg({"id": "x"}), "x")

    def test_unwrap_arg_plain_scalar(self):
        """_unwrap_arg('x') returns 'x'."""
        from mcp_agent.server import _unwrap_arg

        self.assertEqual(_unwrap_arg("x"), "x")

    def test_unwrap_arg_int_scalar(self):
        """_unwrap_arg(42) returns 42."""
        from mcp_agent.server import _unwrap_arg

        self.assertEqual(_unwrap_arg(42), 42)

    def test_unwrap_arg_empty_dict(self):
        """_unwrap_arg({}) returns {}."""
        from mcp_agent.server import _unwrap_arg

        self.assertEqual(_unwrap_arg({}), {})

    def test_unwrap_arg_none(self):
        """_unwrap_arg(None) returns None."""
        from mcp_agent.server import _unwrap_arg

        self.assertIsNone(_unwrap_arg(None))

    # -- _normalize_str_arg direct tests ------------------------------------

    def test_normalize_str_arg_plain(self):
        """_normalize_str_arg('x') returns 'x'."""
        from mcp_agent.server import _normalize_str_arg

        self.assertEqual(_normalize_str_arg("x"), "x")

    def test_normalize_str_arg_wrapped_default(self):
        """_normalize_str_arg({'default': 'x'}) returns 'x'."""
        from mcp_agent.server import _normalize_str_arg

        self.assertEqual(_normalize_str_arg({"default": "x"}), "x")

    def test_normalize_str_arg_none(self):
        """_normalize_str_arg(None) returns None."""
        from mcp_agent.server import _normalize_str_arg

        self.assertIsNone(_normalize_str_arg(None))

    def test_normalize_str_arg_int(self):
        """_normalize_str_arg(42) returns '42'."""
        from mcp_agent.server import _normalize_str_arg

        self.assertEqual(_normalize_str_arg(42), "42")

    # -- _normalize_int_arg direct tests ------------------------------------

    def test_normalize_int_arg_plain_int(self):
        """_normalize_int_arg(1800) returns 1800."""
        from mcp_agent.server import _normalize_int_arg

        self.assertEqual(_normalize_int_arg(1800), 1800)

    def test_normalize_int_arg_wrapped_default(self):
        """_normalize_int_arg({'default': 1800}) returns 1800."""
        from mcp_agent.server import _normalize_int_arg

        self.assertEqual(_normalize_int_arg({"default": 1800}), 1800)

    def test_normalize_int_arg_wrapped_string(self):
        """_normalize_int_arg({'default': '1800'}) returns 1800."""
        from mcp_agent.server import _normalize_int_arg

        self.assertEqual(_normalize_int_arg({"default": "1800"}), 1800)

    def test_normalize_int_arg_none_returns_default(self):
        """_normalize_int_arg(None, default=42) returns 42."""
        from mcp_agent.server import _normalize_int_arg

        self.assertEqual(_normalize_int_arg(None, default=42), 42)

    def test_normalize_int_arg_float(self):
        """_normalize_int_arg(1800.7) returns 1800."""
        from mcp_agent.server import _normalize_int_arg

        self.assertEqual(_normalize_int_arg(1800.7), 1800)

    def test_normalize_int_arg_bool(self):
        """_normalize_int_arg(True) returns 1."""
        from mcp_agent.server import _normalize_int_arg

        self.assertEqual(_normalize_int_arg(True), 1)
        self.assertEqual(_normalize_int_arg(False), 0)

    # -- _normalize_bool_arg direct tests -----------------------------------

    def test_normalize_bool_arg_plain_true(self):
        """_normalize_bool_arg(True) returns True."""
        from mcp_agent.server import _normalize_bool_arg

        self.assertTrue(_normalize_bool_arg(True))

    def test_normalize_bool_arg_plain_false(self):
        """_normalize_bool_arg(False) returns False."""
        from mcp_agent.server import _normalize_bool_arg

        self.assertFalse(_normalize_bool_arg(False))

    def test_normalize_bool_arg_wrapped_default(self):
        """_normalize_bool_arg({'default': True}) returns True."""
        from mcp_agent.server import _normalize_bool_arg

        self.assertTrue(_normalize_bool_arg({"default": True}))

    def test_normalize_bool_arg_wrapped_string_true(self):
        """_normalize_bool_arg({'default': 'true'}) returns True."""
        from mcp_agent.server import _normalize_bool_arg

        self.assertTrue(_normalize_bool_arg({"default": "true"}))

    def test_normalize_bool_arg_wrapped_string_yes(self):
        """_normalize_bool_arg({'default': 'yes'}) returns True."""
        from mcp_agent.server import _normalize_bool_arg

        self.assertTrue(_normalize_bool_arg({"default": "yes"}))

    def test_normalize_bool_arg_none_returns_default(self):
        """_normalize_bool_arg(None, default=False) returns False."""
        from mcp_agent.server import _normalize_bool_arg

        self.assertFalse(_normalize_bool_arg(None, default=False))

    def test_normalize_bool_arg_int(self):
        """_normalize_bool_arg(1) returns True."""
        from mcp_agent.server import _normalize_bool_arg

        self.assertTrue(_normalize_bool_arg(1))
        self.assertFalse(_normalize_bool_arg(0))

    # -- role_wait with wrapped arguments -----------------------------------

    @patch("mcp_agent.server._role_tools.role_wait_impl")
    def test_role_wait_wrapped_timeout_seconds(self, mock_impl):
        """role_wait accepts wrapped timeout_seconds."""
        mock_impl.return_value = {
            "status": "completed",
            "has_result": False,
            "result_available": True,
        }

        from mcp_agent.server import role_wait

        result = role_wait(
            role_run_id="role-1",
            timeout_seconds={"default": 1800},
            poll_interval_seconds={"default": 15},
            return_result={"default": True},
        )

        self.assertEqual(result["status"], "completed")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertEqual(call_kwargs["timeout_seconds"], 1800)
        self.assertEqual(call_kwargs["poll_interval_seconds"], 15)
        self.assertTrue(call_kwargs["return_result"])

    @patch("mcp_agent.server._role_tools.role_wait_impl")
    def test_role_wait_wrapped_role_run_id(self, mock_impl):
        """role_wait accepts wrapped role_run_id."""
        mock_impl.return_value = {"status": "running"}

        from mcp_agent.server import role_wait

        result = role_wait(
            role_run_id={"default": "role-1"},
            timeout_seconds=1,
        )

        self.assertEqual(result["status"], "running")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertEqual(call_kwargs["role_run_id"], "role-1")

    @patch("mcp_agent.server._role_tools.role_wait_impl")
    def test_role_wait_missing_role_run_id_returns_error(self, mock_impl):
        """role_wait with empty/None role_run_id returns MissingRoleRunId error."""
        from mcp_agent.server import role_wait

        result = role_wait(role_run_id={"default": ""})

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "MissingRoleRunId")
        self.assertFalse(result["error"]["retryable"])
        mock_impl.assert_not_called()

    @patch("mcp_agent.server._role_tools.role_wait_impl")
    def test_role_wait_plain_scalars_still_work(self, mock_impl):
        """role_wait with plain scalars still works."""
        mock_impl.return_value = {"status": "completed", "has_result": False}

        from mcp_agent.server import role_wait

        result = role_wait(
            role_run_id="role-1",
            timeout_seconds=1800,
            poll_interval_seconds=15,
            return_result=True,
        )

        self.assertEqual(result["status"], "completed")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertEqual(call_kwargs["role_run_id"], "role-1")
        self.assertEqual(call_kwargs["timeout_seconds"], 1800)

    # -- artifact_get with wrapped arguments --------------------------------

    @patch("mcp_agent.server._role_tools.artifact_get_impl")
    def test_artifact_get_wrapped_run_id_and_artifact_name(self, mock_impl):
        """artifact_get accepts wrapped run_id and artifact_name."""
        mock_impl.return_value = {
            "run_id": "test-run",
            "artifact_name": "scout_report",
            "content": "report content",
        }

        from mcp_agent.server import artifact_get

        result = artifact_get(
            run_id={"default": "20260605-abc123"},
            artifact_name={"default": "scout_report"},
        )

        self.assertEqual(result["run_id"], "test-run")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertEqual(call_kwargs["run_id"], "20260605-abc123")
        self.assertEqual(call_kwargs["artifact_name"], "scout_report")

    @patch("mcp_agent.server._role_tools.artifact_get_impl")
    def test_artifact_get_wrapped_role_run_id(self, mock_impl):
        """artifact_get accepts wrapped role_run_id."""
        mock_impl.return_value = {
            "role_run_id": "test-role-run",
            "content": "artifact content",
        }

        from mcp_agent.server import artifact_get

        result = artifact_get(
            role_run_id={"default": "20260605-abc123-scout-1"}
        )

        self.assertEqual(result["role_run_id"], "test-role-run")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertEqual(call_kwargs["role_run_id"], "20260605-abc123-scout-1")

    @patch("mcp_agent.server._role_tools.artifact_get_impl")
    def test_artifact_get_plain_scalars_still_work(self, mock_impl):
        """artifact_get with plain scalars still works."""
        mock_impl.return_value = {"artifact_name": "test", "content": "data"}

        from mcp_agent.server import artifact_get

        result = artifact_get(
            run_id="20260605-abc123",
            artifact_name="scout_report",
        )

        self.assertEqual(result["artifact_name"], "test")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertEqual(call_kwargs["run_id"], "20260605-abc123")

    # -- role_status with wrapped role_run_id -------------------------------

    @patch("mcp_agent.server._role_tools.role_status_impl")
    def test_role_status_wrapped_role_run_id(self, mock_impl):
        """role_status accepts wrapped role_run_id."""
        mock_impl.return_value = {"status": "running"}

        from mcp_agent.server import role_status

        result = role_status(role_run_id={"default": "role-1"})

        self.assertEqual(result["status"], "running")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertEqual(call_kwargs["role_run_id"], "role-1")

    @patch("mcp_agent.server._role_tools.role_status_impl")
    def test_role_status_plain_role_run_id_still_works(self, mock_impl):
        """role_status with plain role_run_id still works."""
        mock_impl.return_value = {"status": "completed"}

        from mcp_agent.server import role_status

        result = role_status(role_run_id="role-1")

        self.assertEqual(result["status"], "completed")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertEqual(call_kwargs["role_run_id"], "role-1")

    # -- role_result with wrapped arguments ---------------------------------

    @patch("mcp_agent.server._role_tools.role_result_impl")
    def test_role_result_wrapped_role_run_id(self, mock_impl):
        """role_result accepts wrapped role_run_id."""
        mock_impl.return_value = {"status": "completed", "result": "data"}

        from mcp_agent.server import role_result

        result = role_result(role_run_id={"default": "role-1"})

        self.assertEqual(result["status"], "completed")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertEqual(call_kwargs["role_run_id"], "role-1")

    @patch("mcp_agent.server._role_tools.role_result_impl")
    def test_role_result_wrapped_include_full_result(self, mock_impl):
        """role_result accepts wrapped include_full_result."""
        mock_impl.return_value = {"status": "completed"}

        from mcp_agent.server import role_result

        result = role_result(
            role_run_id="role-1",
            include_full_result={"default": False},
        )

        self.assertEqual(result["status"], "completed")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertFalse(call_kwargs["include_full_result"])

    @patch("mcp_agent.server._role_tools.role_result_impl")
    def test_role_result_plain_scalars_still_work(self, mock_impl):
        """role_result with plain scalars still works."""
        mock_impl.return_value = {"status": "completed"}

        from mcp_agent.server import role_result

        result = role_result(
            role_run_id="role-1",
            include_full_result=True,
        )

        self.assertEqual(result["status"], "completed")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertTrue(call_kwargs["include_full_result"])

    # -- artifact_list with wrapped run_id ----------------------------------

    @patch("mcp_agent.server._role_tools.artifact_list_impl")
    def test_artifact_list_wrapped_run_id(self, mock_impl):
        """artifact_list accepts wrapped run_id."""
        mock_impl.return_value = {"run_id": "test-run", "artifacts": []}

        from mcp_agent.server import _artifact_list_internal as artifact_list

        result = artifact_list(run_id={"default": "20260605-abc123"})

        self.assertEqual(result["run_id"], "test-run")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertEqual(call_kwargs["run_id"], "20260605-abc123")

    @patch("mcp_agent.server._role_tools.artifact_list_impl")
    def test_artifact_list_plain_run_id_still_works(self, mock_impl):
        """artifact_list with plain run_id still works."""
        mock_impl.return_value = {"run_id": "test-run", "artifacts": []}

        from mcp_agent.server import _artifact_list_internal as artifact_list

        result = artifact_list(run_id="20260605-abc123")

        self.assertEqual(result["run_id"], "test-run")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertEqual(call_kwargs["run_id"], "20260605-abc123")

    # -- openhands_get_task_status with wrapped task_id ---------------------

    @patch("mcp_agent.server.requests.get")
    def test_openhands_get_task_status_wrapped_task_id(self, mock_get):
        """openhands_get_task_status accepts wrapped task_id."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "status": "completed",
            "answer": "test answer",
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        from mcp_agent.server import openhands_start_task, openhands_get_task_status
        from mcp_agent.server import _get_store

        # Create a task first
        mock_post = MagicMock()
        mock_resp_post = MagicMock()
        mock_resp_post.json.return_value = {
            "conversation_id": "conv-mock",
            "status": "no_wait",
        }
        mock_resp_post.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp_post

        with patch("mcp_agent.server.requests.post", mock_post):
            openhands_start_task(
                prompt="Test prompt", api_key="test-key"
            )

        store = _get_store()
        task_ids = store.list_tasks()
        task_id = task_ids[0] if task_ids else "test-task-1"

        # Mark the task as completed so the endpoint returns the cached result
        store.update_task(task_id, status="completed", result={"answer": "test answer"})

        result = openhands_get_task_status(
            task_id={"default": task_id}
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["answer"], "test answer")

    # -- openhands_get_task_result with wrapped task_id ---------------------

    @patch("mcp_agent.server.requests.get")
    def test_openhands_get_task_result_wrapped_task_id(self, mock_get):
        """openhands_get_task_result accepts wrapped task_id."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "status": "completed",
            "answer": "test answer",
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        from mcp_agent.server import openhands_start_task, openhands_get_task_result
        from mcp_agent.server import _get_store

        # Create a task first
        mock_post = MagicMock()
        mock_resp_post = MagicMock()
        mock_resp_post.json.return_value = {
            "conversation_id": "conv-mock",
            "status": "no_wait",
        }
        mock_resp_post.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp_post

        with patch("mcp_agent.server.requests.post", mock_post):
            openhands_start_task(
                prompt="Test prompt", api_key="test-key"
            )

        store = _get_store()
        task_ids = store.list_tasks()
        task_id = task_ids[0] if task_ids else "test-task-1"

        # Mark the task as completed so the endpoint returns the cached result
        store.update_task(task_id, status="completed", result={"answer": "test answer"})

        result = openhands_get_task_result(
            task_id={"default": task_id}
        )

        self.assertEqual(result["status"], "completed")

    # -- openhands_get_task_events with wrapped task_id and limit -----------

    @patch("mcp_agent.server.requests.get")
    def test_openhands_get_task_events_wrapped_task_id_and_limit(self, mock_get):
        """openhands_get_task_events accepts wrapped task_id and limit."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"events": [], "count": 0}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        from mcp_agent.server import openhands_start_task, openhands_get_task_events
        from mcp_agent.server import _get_store

        # Create a task first
        mock_post = MagicMock()
        mock_resp_post = MagicMock()
        mock_resp_post.json.return_value = {
            "conversation_id": "conv-mock",
            "status": "no_wait",
        }
        mock_resp_post.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp_post

        with patch("mcp_agent.server.requests.post", mock_post):
            openhands_start_task(
                prompt="Test prompt", api_key="test-key"
            )

        store = _get_store()
        task_ids = store.list_tasks()
        task_id = task_ids[0] if task_ids else "test-task-1"

        # Set conversation_id so events can be fetched
        store.update_task(task_id, conversation_id="conv-mock")

        result = openhands_get_task_events(
            task_id={"default": task_id},
            limit={"default": 25},
        )

        self.assertEqual(result["count"], 0)

    # -- openhands_cancel_task with wrapped task_id -------------------------

    @patch("mcp_agent.server.requests.post")
    def test_openhands_cancel_task_wrapped_task_id(self, mock_post):
        """openhands_cancel_task accepts wrapped task_id."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "conversation_id": "conv-mock",
            "status": "no_wait",
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        from mcp_agent.server import openhands_start_task, openhands_cancel_task
        from mcp_agent.server import _get_store

        # Create a task first
        openhands_start_task(
            prompt="Test prompt", api_key="test-key"
        )

        store = _get_store()
        task_ids = store.list_tasks()
        task_id = task_ids[0] if task_ids else "test-task-1"

        result = openhands_cancel_task(
            task_id={"default": task_id}
        )

        self.assertEqual(result["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
