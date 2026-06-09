#!/usr/bin/env python3
"""Test that the MCP server exports exactly the expected public tools."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestPublicMCPToolSurface(unittest.TestCase):
    """Verify the public MCP tool surface is exactly {role_list, role_call, role_wait}."""

    def test_public_tools_are_exactly_three(self):
        """Assert set(tool_names) == {"role_list", "role_call", "role_wait"}."""
        from mcp_agent.server import MCP

        tool_names = {t.name for t in MCP._tool_manager.list_tools()}
        self.assertEqual(tool_names, {"role_list", "role_call", "role_wait"})

    def test_no_legacy_tools_exposed(self):
        """No legacy tool names appear in the public MCP surface."""
        from mcp_agent.server import MCP

        tool_names = {t.name for t in MCP._tool_manager.list_tools()}
        legacy_names = {
            "shttp_role_call", "shttp_role_list", "shttp_role_wait",
            "shtpp_role_call", "shtpp_role_list", "shtpp_role_wait",
            "role_start", "role_status", "role_result",
            "artifact_get", "artifact_list",
            "openhands_start_task", "openhands_get_task_status",
            "openhands_get_task_result", "openhands_get_task_events",
            "openhands_cancel_task",
            "call_llm", "check_health", "check_job",
        }
        overlap = tool_names & legacy_names
        self.assertEqual(overlap, set(), f"Legacy tools still exposed: {overlap}")


if __name__ == "__main__":
    unittest.main()
