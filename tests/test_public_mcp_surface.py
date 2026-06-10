#!/usr/bin/env python3
"""Test that the MCP server exports exactly the expected public tools."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestPublicMCPToolSurface(unittest.TestCase):
    """Verify the public MCP tool surface is exactly {role_list, role_call, role_wait}."""

    def test_public_tools_are_exactly_three(self):
        """Assert set(tool_names) == {"role_list", "role_call", "role_wait"}."""
        from mcp_agent.server import MCP

        tool_names = {t.name for t in MCP._tool_manager.list_tools()}
        self.assertEqual(tool_names, {"role_list", "role_call", "role_wait"})


class TestNegativeAssertions(unittest.TestCase):
    """Negative assertions: forbidden terms must not appear in public output/docs/prompts."""

    def test_role_list_no_forbidden_fields(self):
        """role_list response does not contain forbidden_fields key."""
        from mcp_agent.server import role_list
        result = role_list()
        contract = result.get("flat_role_call_contract", {})
        self.assertNotIn("forbidden_fields", contract)

    def test_role_list_no_full_result_or_artifact_path_recursively(self):
        """role_list response does not contain full_result/artifact_path recursively."""
        import json
        from mcp_agent.server import role_list
        result = role_list()
        text = json.dumps(result)
        self.assertNotIn("full_result", text)
        self.assertNotIn("artifact_path", text)

    def test_head_of_it_prompt_no_forbidden_terms(self):
        """prompts/head_of_it.md does not contain full_result/artifact_path/return_result."""
        prompts_dir = os.path.join(os.path.dirname(__file__), "..", "prompts")
        content = Path(os.path.join(prompts_dir, "head_of_it.md")).read_text(encoding="utf-8")
        self.assertNotIn("full_result", content)
        self.assertNotIn("artifact_path", content)
        self.assertNotIn("return_result", content)

    def test_server_docstrings_no_forbidden_terms(self):
        """server.py public tool docstrings do not contain full_result/artifact_path/return_result."""
        server_path = os.path.join(os.path.dirname(__file__), "..", "mcp_agent", "server.py")
        content = Path(server_path).read_text(encoding="utf-8")
        # Extract docstrings from @MCP.tool() decorated functions
        # Check the full file for forbidden terms in docstring context
        self.assertNotIn("full_result", content)
        self.assertNotIn("artifact_path", content)
        self.assertNotIn("return_result", content)

    def test_rg_no_production_matches(self):
        """rg for role_call_impl/artifact_list_impl/role_list_impl returns no production matches."""
        import shutil
        if shutil.which("rg") is None:
            self.skipTest("ripgrep (rg) not installed")
        import subprocess
        result = subprocess.run(
            [
                "rg", "-n",
                "role_call_impl|artifact_list_impl|_role_call_impl_alias",
                "mcp_agent",
            ],
            capture_output=True, text=True,
            cwd=os.path.join(os.path.dirname(__file__), ".."),
        )
        self.assertEqual(
            result.returncode, 1,
            f"Found production matches for deleted functions:\n{result.stdout}",
        )

    def test_readme_no_shttp_role_wait(self):
        """README.md does not contain shttp_role_wait/shttp_role_call/shttp_role_result."""
        readme_path = os.path.join(os.path.dirname(__file__), "..", "README.md")
        content = Path(readme_path).read_text(encoding="utf-8")
        self.assertNotIn("shttp_role_wait", content)
        self.assertNotIn("shttp_role_call", content)
        self.assertNotIn("shttp_role_result", content)

    def test_head_of_it_prompt_no_shttp_role_wait(self):
        """prompts/head_of_it.md does not contain shttp_role_wait/shttp_role_call."""
        prompts_dir = os.path.join(os.path.dirname(__file__), "..", "prompts")
        content = Path(os.path.join(prompts_dir, "head_of_it.md")).read_text(encoding="utf-8")
        self.assertNotIn("shttp_role_wait", content)
        self.assertNotIn("shttp_role_call", content)

    def test_server_no_shttp_role_wait(self):
        """server.py does not contain shttp_role_wait/shttp_role_call in public-facing content."""
        server_path = os.path.join(os.path.dirname(__file__), "..", "mcp_agent", "server.py")
        content = Path(server_path).read_text(encoding="utf-8")
        self.assertNotIn("shttp_role_wait", content)
        self.assertNotIn("shttp_role_call", content)
        self.assertNotIn("shttp_role_result", content)

    def test_role_lifecycle_no_shttp_role_wait(self):
        """role_lifecycle.py does not contain shttp_role_wait/shttp_role_call."""
        lifecycle_path = os.path.join(
            os.path.dirname(__file__), "..", "mcp_agent", "role_lifecycle.py"
        )
        content = Path(lifecycle_path).read_text(encoding="utf-8")
        self.assertNotIn("shttp_role_wait", content)
        self.assertNotIn("shttp_role_call", content)

    def test_role_tools_no_shttp_role_wait(self):
        """role_tools.py does not contain shttp_role_wait/shttp_role_call."""
        tools_path = os.path.join(
            os.path.dirname(__file__), "..", "mcp_agent", "role_tools.py"
        )
        content = Path(tools_path).read_text(encoding="utf-8")
        self.assertNotIn("shttp_role_wait", content)
        self.assertNotIn("shttp_role_call", content)


class TestRequestNonceSchema(unittest.TestCase):
    """Verify request_nonce appears in the role_wait MCP tool schema."""

    def test_request_nonce_in_role_wait_schema(self):
        """role_wait MCP tool schema includes request_nonce parameter."""
        from mcp_agent.server import MCP

        for tool in MCP._tool_manager.list_tools():
            if tool.name == "role_wait":
                params = tool.parameters
                prop_names = set(params.get("properties", {}).keys())
                self.assertIn("request_nonce", prop_names)
                break
        else:
            self.fail("role_wait tool not found in MCP tool list")

    def test_request_nonce_is_optional_in_schema(self):
        """request_nonce is optional (not in required list) in role_wait schema."""
        from mcp_agent.server import MCP

        for tool in MCP._tool_manager.list_tools():
            if tool.name == "role_wait":
                params = tool.parameters
                required = set(params.get("required", []))
                self.assertNotIn("request_nonce", required)
                break
        else:
            self.fail("role_wait tool not found in MCP tool list")


if __name__ == "__main__":
    unittest.main()
