#!/usr/bin/env python3
"""Tests for prompt renderer (mcp_agent.prompt_renderer)."""

import json
import os
import shutil
import tempfile
import unittest


class TestPromptRenderer(unittest.TestCase):
    """Test prompt template rendering."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_prompt_")
        # Create a minimal template for testing
        self.template_path = os.path.join(self.tmpdir, "test_template.md")
        with open(self.template_path, "w") as f:
            f.write(
                "# Role: {{ name }}\n\n"
                "{{ user_task }}\n\n"
                "Repository: {{ repo | default(\"current repository\") }}\n\n"
                "Branch: {{ branch | default(\"main\") }}\n\n"
                "Context: {{ context | default(\"\") }}\n\n"
                "Artifacts:\n"
                "{% for key, value in artifacts.items() %}"
                "- {{ key }}: {{ value }}\n"
                "{% endfor %}\n"
            )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_render_basic_template(self):
        """Template renders with basic variables."""
        from mcp_agent.prompt_renderer import render_prompt

        result = render_prompt(
            template_path="test_template.md",
            variables={
                "name": "Scout",
                "user_task": "Investigate the repo",
                "repo": "https://github.com/example/repo",
                "branch": "develop",
                "context": "test context",
            },
            template_root=self.tmpdir,
        )

        self.assertIn("# Role: Scout", result)
        self.assertIn("Investigate the repo", result)
        self.assertIn("https://github.com/example/repo", result)
        self.assertIn("develop", result)
        self.assertIn("test context", result)

    def test_render_user_task_placeholder(self):
        """Prompt renderer renders {{ user_task }}."""
        from mcp_agent.prompt_renderer import render_prompt

        result = render_prompt(
            template_path="test_template.md",
            variables={
                "name": "Test",
                "user_task": "This is my custom task",
                "repo": "",
                "branch": "",
                "context": "",
            },
            template_root=self.tmpdir,
        )

        self.assertIn("This is my custom task", result)

    def test_render_default_filter(self):
        """Prompt renderer handles {{ context | default("") }}."""
        from mcp_agent.prompt_renderer import render_prompt

        # Render with context provided
        result_with = render_prompt(
            template_path="test_template.md",
            variables={
                "name": "Test",
                "user_task": "task",
                "repo": "",
                "branch": "",
                "context": "my context",
            },
            template_root=self.tmpdir,
        )
        self.assertIn("my context", result_with)

        # Render without context key — should use default("")
        result_without = render_prompt(
            template_path="test_template.md",
            variables={
                "name": "Test",
                "user_task": "task",
                "repo": "",
                "branch": "",
                # No "context" key
            },
            template_root=self.tmpdir,
        )
        # The default("") filter should produce an empty string
        self.assertIn("Context:", result_without)

    def test_render_missing_optional_values_become_empty(self):
        """Missing optional values render as empty string."""
        from mcp_agent.prompt_renderer import render_prompt

        result = render_prompt(
            template_path="test_template.md",
            variables={
                "name": "Test",
                "user_task": "task",
                "repo": "",
                "branch": "",
                "context": "",
            },
            template_root=self.tmpdir,
        )
        # All placeholders should be present, missing ones should be empty
        self.assertIn("Repository:", result)
        self.assertIn("Branch:", result)

    def test_render_artifact_variables(self):
        """Artifacts are injected into the template."""
        from mcp_agent.prompt_renderer import render_prompt

        result = render_prompt(
            template_path="test_template.md",
            variables={
                "name": "Architect",
                "user_task": "Plan implementation",
                "repo": "https://github.com/example/repo",
                "branch": "main",
                "context": "",
                "artifacts": {
                    "scout_report": "The scout found X files.",
                },
            },
            template_root=self.tmpdir,
        )

        self.assertIn("scout_report:", result)
        self.assertIn("The scout found X files.", result)

    def test_render_missing_template_raises_file_not_found(self):
        """Missing template file raises FileNotFoundError."""
        from mcp_agent.prompt_renderer import render_prompt

        with self.assertRaises(FileNotFoundError):
            render_prompt(
                template_path="nonexistent_template.md",
                variables={"name": "Test"},
                template_root=self.tmpdir,
            )

    def test_render_scout_template(self):
        """Render the actual scout.md template."""
        from mcp_agent.prompt_renderer import render_prompt

        repo_root = os.path.join(os.path.dirname(__file__), "..")
        result = render_prompt(
            template_path="prompts/scout.md",
            variables={
                "user_task": "Analyze repository structure",
                "repo": "https://github.com/metacoma/example",
                "base_branch": "main",
                "branch": "feature/test",
                "context": '{"run_id": "test-run"}',
            },
            template_root=repo_root,
        )

        self.assertIn("Analyze repository structure", result)
        self.assertIn("metacoma/example", result)
        self.assertIn("main", result)

    def test_render_architect_template_with_scout_report(self):
        """Render architect.md with scout_report injected."""
        from mcp_agent.prompt_renderer import render_prompt

        repo_root = os.path.join(os.path.dirname(__file__), "..")
        scout_report = (
            "# Scout Report\n\n"
            "## Task Understanding\n\n"
            "Investigate the repo.\n\n"
            "## Repository Facts\n\n"
            "Python project.\n"
        )

        result = render_prompt(
            template_path="prompts/architect.md",
            variables={
                "user_task": "Plan implementation",
                "repo": "https://github.com/metacoma/example",
                "base_branch": "main",
                "branch": None,
                "context": "",
                "scout_report": scout_report,
            },
            template_root=repo_root,
        )

        self.assertIn("# Scout Report", result)
        self.assertIn("Investigate the repo.", result)
        self.assertIn("Python project.", result)


if __name__ == "__main__":
    unittest.main()
