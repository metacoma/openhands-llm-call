#!/usr/bin/env python3
"""Tests for malformed role_wait inputs and single-active-role behavior."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure the project root is on sys.path so imports work.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestMalformedRoleWaitInputs(unittest.TestCase):
    """Test that role_wait tolerates malformed LLM argument shapes."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_malformed_rw_")
        os.environ["OPENHANDS_STATE_DIR"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_STATE_DIR", None)
        import mcp_agent.server as server_mod
        server_mod._store = None

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_nested_role_run_id_with_nested_args(self, mock_impl):
        """role_wait extracts role_run_id and nested args from a dict."""
        mock_impl.return_value = {"status": "completed"}

        from mcp_agent.server import role_wait

        result = role_wait(
            role_run_id={
                "role_run_id": "abc-scout-1",
                "timeout_seconds": 600,
                "poll_interval_seconds": 15,
            },
        )

        self.assertEqual(result["status"], "completed")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertEqual(call_kwargs["role_run_id"], "abc-scout-1")
        self.assertEqual(call_kwargs["timeout_seconds"], 600)
        self.assertEqual(call_kwargs["poll_interval_seconds"], 15)

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_text_wrappers_for_all_args(self, mock_impl):
        """role_wait handles {\"text\": ...} wrappers for all args."""
        mock_impl.return_value = {"status": "completed"}

        from mcp_agent.server import role_wait

        result = role_wait(
            role_run_id={"text": "abc-scout-1"},
            timeout_seconds={"text": "600"},
            poll_interval_seconds={"text": "15"},
        )

        self.assertEqual(result["status"], "completed")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertEqual(call_kwargs["role_run_id"], "abc-scout-1")
        self.assertEqual(call_kwargs["timeout_seconds"], 600)
        self.assertEqual(call_kwargs["poll_interval_seconds"], 15)

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_plain_string_role_run_id_still_works(self, mock_impl):
        """role_wait with plain string role_run_id still works."""
        mock_impl.return_value = {"status": "completed"}

        from mcp_agent.server import role_wait

        result = role_wait(
            role_run_id="abc-scout-1",
            timeout_seconds=300,
            poll_interval_seconds=10,
        )

        self.assertEqual(result["status"], "completed")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertEqual(call_kwargs["role_run_id"], "abc-scout-1")
        self.assertEqual(call_kwargs["timeout_seconds"], 300)

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_nested_role_run_id_without_nested_args(self, mock_impl):
        """role_wait extracts role_run_id from nested dict without nested args."""
        mock_impl.return_value = {"status": "completed"}

        from mcp_agent.server import role_wait

        result = role_wait(
            role_run_id={
                "role_run_id": "abc-scout-1",
                "status": "running",
            },
        )

        self.assertEqual(result["status"], "completed")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertEqual(call_kwargs["role_run_id"], "abc-scout-1")

    @patch("mcp_agent.role_lifecycle.role_lifecycle_wait_impl")
    def test_mixed_wrappers_and_plain(self, mock_impl):
        """role_wait handles mixed plain and wrapped args."""
        mock_impl.return_value = {"status": "completed"}

        from mcp_agent.server import role_wait

        result = role_wait(
            role_run_id={"text": "abc-scout-1"},
            timeout_seconds=300,
            poll_interval_seconds={"text": "10"},
        )

        self.assertEqual(result["status"], "completed")
        mock_impl.assert_called_once()
        call_kwargs = mock_impl.call_args[1]
        self.assertEqual(call_kwargs["role_run_id"], "abc-scout-1")
        self.assertEqual(call_kwargs["timeout_seconds"], 300)
        self.assertEqual(call_kwargs["poll_interval_seconds"], 10)

