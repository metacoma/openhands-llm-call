#!/usr/bin/env python3
"""Tests for role registry (mcp_agent.roles)."""

import os
import shutil
import tempfile
import unittest


class TestRolesYamlLoad(unittest.TestCase):
    """Test that config/roles.yaml loads successfully."""

    def test_load_roles_from_default_path(self):
        """roles.yaml loads successfully with default path."""
        from mcp_agent.roles import load_roles, _ROLES

        # Reset global state
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None

        roles = load_roles()
        self.assertIn("scout", roles)
        self.assertIn("architect", roles)
        self.assertIn("coder", roles)
        self.assertIn("reviewer", roles)
        self.assertIn("publisher", roles)
        self.assertIn("coder_fix", roles)

    def test_load_roles_from_custom_path(self):
        """roles.yaml loads from a custom config path."""
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None

        # Copy config to temp dir
        tmpdir = tempfile.mkdtemp(prefix="test_roles_")
        try:
            config_src = os.path.join(
                os.path.dirname(__file__), "..", "config", "roles.yaml"
            )
            config_dst = os.path.join(tmpdir, "roles.yaml")
            shutil.copy2(config_src, config_dst)

            from mcp_agent.roles import load_roles
            roles = load_roles(config_path=config_dst)
            self.assertEqual(len(roles), 6)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_load_roles_missing_file(self):
        """load_roles raises FileNotFoundError for missing config."""
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None

        from mcp_agent.roles import load_roles
        with self.assertRaises(FileNotFoundError):
            load_roles(config_path="/nonexistent/path/roles.yaml")

    def test_load_roles_invalid_config(self):
        """load_roles raises ValueError for invalid config structure."""
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None

        tmpdir = tempfile.mkdtemp(prefix="test_roles_")
        try:
            bad_config = os.path.join(tmpdir, "bad.yaml")
            with open(bad_config, "w") as f:
                f.write("no_roles_key: true\n")

            from mcp_agent.roles import load_roles
            with self.assertRaises(ValueError):
                load_roles(config_path=bad_config)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestGetRole(unittest.TestCase):
    """Test get_role function."""

    def setUp(self):
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None

    def tearDown(self):
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None

    def test_get_existing_role(self):
        """get_role returns RoleSpec for an existing role."""
        from mcp_agent.roles import get_role
        from dataclasses import fields

        role = get_role("scout")
        self.assertEqual(role.name, "scout")
        self.assertTrue(role.readonly)
        self.assertEqual(role.timeout_minutes, 60)

    def test_get_unknown_role_raises_key_error(self):
        """get_role raises KeyError for unknown role."""
        from mcp_agent.roles import get_role

        with self.assertRaises(KeyError) as ctx:
            get_role("qa")

        self.assertIn("qa", str(ctx.exception))
        self.assertIn("scout", str(ctx.exception))


class TestListRoles(unittest.TestCase):
    """Test list_roles function."""

    def setUp(self):
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None

    def tearDown(self):
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None

    def test_list_roles_returns_six_roles(self):
        """role_list returns six roles (including coder_fix)."""
        from mcp_agent.roles import list_roles
        roles = list_roles()
        self.assertEqual(len(roles), 6)

    def test_list_roles_contains_expected_fields(self):
        """Each role dict has expected fields including summary_artifact."""
        from mcp_agent.roles import list_roles
        roles = list_roles()

        expected_fields = {
            "name",
            "description",
            "model",
            "readonly",
            "requires_artifacts",
            "output_artifact",
            "summary_artifact",
            "timeout_minutes",
        }

        for role in roles:
            self.assertTrue(
                expected_fields.issubset(set(role.keys())),
                f"Missing fields: {expected_fields - set(role.keys())}",
            )

    def test_coder_fix_role_has_required_fields(self):
        """coder_fix role has all required fields."""
        from mcp_agent.roles import get_role

        role = get_role("coder_fix")
        self.assertEqual(role.name, "coder_fix")
        self.assertFalse(role.readonly)
        self.assertEqual(role.timeout_minutes, 120)
        self.assertIn("architect_plan", role.requires_artifacts)
        self.assertIn("coder_report", role.requires_artifacts)
        self.assertIn("reviewer_report", role.requires_artifacts)
        self.assertEqual(role.output_artifact, "coder_fix_result")
        self.assertEqual(role.summary_artifact, "coder_fix_summary")

    def test_summary_artifact_field_present_for_all_roles(self):
        """All roles have summary_artifact field."""
        from mcp_agent.roles import get_role

        for role_name in ["scout", "architect", "coder", "reviewer", "publisher", "coder_fix"]:
            role = get_role(role_name)
            self.assertIsNotNone(role.summary_artifact)
            self.assertTrue(
                role.summary_artifact.endswith("_summary"),
                f"Role {role_name} summary_artifact should end with '_summary'",
            )


class TestRoleSpecValidation(unittest.TestCase):
    """Test role spec validation."""

    def setUp(self):
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None

    def tearDown(self):
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None

    def test_missing_required_field_raises_value_error(self):
        """Missing required field raises ValueError."""
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None

        tmpdir = tempfile.mkdtemp(prefix="test_roles_")
        try:
            bad_config = os.path.join(tmpdir, "bad.yaml")
            with open(bad_config, "w") as f:
                f.write(
                    "roles:\n"
                    "  scout:\n"
                    "    description: test\n"
                    "    model: test\n"
                    "    timeout_minutes: 10\n"
                    "    requires_artifacts: []\n"
                    "    output_artifact: test\n"
                    # missing prompt_template and readonly
                )

            from mcp_agent.roles import load_roles
            with self.assertRaises(ValueError) as ctx:
                load_roles(config_path=bad_config)
            self.assertIn("prompt_template", str(ctx.exception))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_invalid_timeout_raises_value_error(self):
        """Invalid timeout_minutes raises ValueError."""
        import mcp_agent.roles as roles_mod
        roles_mod._ROLES = None

        tmpdir = tempfile.mkdtemp(prefix="test_roles_")
        try:
            bad_config = os.path.join(tmpdir, "bad.yaml")
            with open(bad_config, "w") as f:
                f.write(
                    "roles:\n"
                    "  scout:\n"
                    "    description: test\n"
                    "    model: test\n"
                    "    prompt_template: prompts/scout.md\n"
                    "    readonly: true\n"
                    "    timeout_minutes: -1\n"
                    "    requires_artifacts: []\n"
                    "    output_artifact: scout_report\n"
                )

            from mcp_agent.roles import load_roles
            with self.assertRaises(ValueError) as ctx:
                load_roles(config_path=bad_config)
            self.assertIn("timeout_minutes", str(ctx.exception))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
