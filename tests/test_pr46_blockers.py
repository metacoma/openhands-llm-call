#!/usr/bin/env python3
"""Tests for PR #46 blocker fixes.

Covers:
1. No bare "import role_lifecycle" in mcp_agent/server.py
2. role_list scout workflow requires == []
3. mcp_tools_contract.md does not claim {"text": "..."} wrappers are rejected
4. _invalid_flat_role_call_error message does not reject scalar wrappers
5. README does not present generic OpenHands helpers as public Head-of-IT MCP tools
"""

import os
import shutil
import sys
import tempfile
import unittest

# Ensure the project root is on sys.path so imports work.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestNoBareRoleLifecycleImport(unittest.TestCase):
    """Task 1: Verify no bare 'import role_lifecycle' remains in server.py."""

    def test_no_bare_import_role_lifecycle(self):
        """server.py must not have bare 'import role_lifecycle' (without 'from')."""
        import re
        server_py = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "mcp_agent",
            "server.py",
        )
        content = open(server_py, encoding="utf-8").read()
        bare_imports = re.findall(
            r"^\s*import\s+role_lifecycle\s*$", content, re.MULTILINE
        )
        self.assertEqual(
            bare_imports, [],
            f"Found bare imports: {bare_imports}",
        )

    def test_top_level_role_lifecycle_import_exists(self):
        """server.py must have a top-level 'from . import role_lifecycle'."""
        server_py = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "mcp_agent",
            "server.py",
        )
        content = open(server_py, encoding="utf-8").read()
        self.assertIn(
            "from . import role_lifecycle",
            content,
            "Expected top-level 'from . import role_lifecycle' in server.py",
        )


class TestScoutWorkflowRequires(unittest.TestCase):
    """Task 2: Verify scout workflow requires == []."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_scout_requires_")
        os.environ["OPENHANDS_STATE_DIR"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_STATE_DIR", None)
        import mcp_agent.server as server_mod

        server_mod._store = None

    def test_scout_workflow_requires_empty(self):
        """scout role must have requires: [] in workflow steps."""
        from mcp_agent.server import role_list

        result = role_list()
        # Find the scout step in workflow
        for step in result.get("workflow", []):
            if step["role"] == "scout":
                self.assertEqual(
                    step["requires"],
                    [],
                    "scout workflow requires must be [] (scout must not require itself)",
                )
                break
        else:
            self.fail("scout role not found in workflow")

    def test_architect_workflow_requires_scout(self):
        """architect role must have requires: ['scout'] in workflow steps."""
        from mcp_agent.server import role_list

        result = role_list()
        for step in result.get("workflow", []):
            if step["role"] == "architect":
                self.assertIn(
                    "scout",
                    step["requires"],
                    "architect workflow must require scout (via scout_report artifact)",
                )
                break
        else:
            self.fail("architect role not found in workflow")


class TestContractNoTextWrapperRejection(unittest.TestCase):
    """Task 3: Verify mcp_tools_contract.md does not claim {"text": "..."} wrappers are rejected."""

    @staticmethod
    def _read_contract():
        contract_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "mcp_tools_contract.md",
        )
        return open(contract_path, encoding="utf-8").read()

    def test_contract_does_not_reject_text_wrapper(self):
        """mcp_tools_contract.md must not say {"text": "..."} wrappers are rejected."""
        content = self._read_contract()
        # The old claim was: 'If a {"text": "..." } wrapper is detected, the API returns InvalidFlatPayload'
        self.assertNotIn(
            "InvalidFlatPayload",
            content,
            "mcp_tools_contract.md must not reference 'InvalidFlatPayload' (wrong error type name); "
            "use 'InvalidFlatRoleCallPayload' if referring to old nested payloads",
        )

    def test_contract_mentions_tolerant_policy(self):
        """mcp_tools_contract.md must mention that scalar wrappers are normalized."""
        content = self._read_contract()
        # Should mention normalization of scalar wrappers
        self.assertIn(
            "normalized",
            content.lower(),
            "mcp_tools_contract.md should mention that scalar wrappers are normalized",
        )


class TestInvalidFlatRoleCallErrorMessage(unittest.TestCase):
    """Task 3b: Verify _invalid_flat_role_call_error message does not reject scalar wrappers."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_error_msg_")
        os.environ["OPENHANDS_STATE_DIR"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OPENHANDS_STATE_DIR", None)
        import mcp_agent.server as server_mod

        server_mod._store = None

    def test_error_message_no_text_wrapper_rejection(self):
        """Error message must not say 'Do not pass {"text": "..."} wrappers'."""
        from mcp_agent.server import _invalid_flat_role_call_error

        error = _invalid_flat_role_call_error("test_field")
        message = error["error"]["message"]
        self.assertNotIn(
            'Do not pass {"text": "..."}',
            message,
            "Error message must not reject scalar text wrappers",
        )
        # The error type should be InvalidFlatRoleCallPayload
        self.assertEqual(
            error["error"]["type"],
            "InvalidFlatRoleCallPayload",
            "Error type must be InvalidFlatRoleCallPayload",
        )


class TestREADMEInternalHelpers(unittest.TestCase):
    """Task 4: Verify README does not present generic OpenHands helpers as public Head-of-IT MCP tools."""

    @staticmethod
    def _read_readme():
        readme_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "README.md",
        )
        return open(readme_path, encoding="utf-8").read()

    def test_readme_no_generic_openhands_tools_heading(self):
        """README must not have '### Generic OpenHands tools' heading."""
        content = self._read_readme()
        self.assertNotIn(
            "### Generic OpenHands tools",
            content,
            "README must not present generic OpenHands helpers as public MCP tools",
        )

    def test_readme_architecture_diagram_internal(self):
        """Architecture diagram must mark generic tools as internal."""
        content = self._read_readme()
        # The architecture diagram should not have 'Generic OpenHands tools'
        self.assertNotIn(
            "Generic OpenHands tools",
            content,
            "Architecture diagram must not label generic tools as public",
        )

    def test_readme_has_internal_note(self):
        """README should have a note marking internal helpers."""
        content = self._read_readme()
        # Should mention that these are internal
        self.assertIn(
            "not for Head-of-IT",
            content,
            "README should mark internal helpers as 'not for Head-of-IT'",
        )


class TestREADMETroubleshootingFix(unittest.TestCase):
    """Task 5: Verify README troubleshooting does not tell user to call role_call for existing role."""

    @staticmethod
    def _read_readme():
        readme_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "README.md",
        )
        return open(readme_path, encoding="utf-8").read()

    def test_troubleshooting_follows_next_action(self):
        """Troubleshooting should tell user to follow next_action, usually role_wait."""
        content = self._read_readme()
        # Should mention role_wait in the troubleshooting section
        self.assertIn(
            "role_wait",
            content,
            "README troubleshooting should mention role_wait",
        )
        # Should mention next_action
        self.assertIn(
            "next_action",
            content,
            "README troubleshooting should mention next_action",
        )


if __name__ == "__main__":
    unittest.main()
