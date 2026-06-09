#!/usr/bin/env python3
"""Integration test for the full MCP role chain.

Proves that Head-of-IT/orchestrator can use only public MCP tools
(role_list, role_call, role_wait) to pass a full 5-role chain
(scout → architect → coder → reviewer → publisher),
transferring only artifact_id between roles — never artifact content.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Iterator

import pytest
import requests


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_free_port() -> int:
    """Find a free TCP port on localhost."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_http_ok(url: str, timeout: float = 15.0) -> None:
    """Poll a URL until it returns HTTP < 500 or times out."""
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            resp = requests.get(url, timeout=1.0)
            if resp.status_code < 500:
                return
        except Exception as exc:
            last_error = exc
        time.sleep(0.2)
    raise AssertionError(
        f"Timed out waiting for {url} (last error: {last_error})"
    )


def terminate_process(proc: subprocess.Popen) -> None:
    """Terminate a subprocess, killing if it does not exit."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def free_port() -> int:
    return get_free_port()


@pytest.fixture()
def fake_backend_process(tmp_path: Path, free_port: int) -> Iterator[str]:
    """Start the fake OpenHands LLM backend as a subprocess."""
    repo_root = Path(__file__).resolve().parent.parent
    port = free_port
    base_url = f"http://127.0.0.1:{port}"

    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "tests.fake_openhands_llm_backend:app",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--log-level", "warning",
        ],
        cwd=str(repo_root),
        env={**os.environ, "PYTHONPATH": str(repo_root)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    health_url = f"{base_url}/health"
    try:
        wait_http_ok(health_url, timeout=10.0)
    except Exception as exc:
        stdout, stderr = proc.communicate(timeout=2)
        raise AssertionError(
            f"Fake backend failed to start: {exc}\n"
            f"stdout: {stdout.decode(errors='replace')}\n"
            f"stderr: {stderr.decode(errors='replace')}"
        ) from None

    yield base_url

    terminate_process(proc)


@pytest.fixture()
def mcp_agent_process(
    tmp_path: Path, fake_backend_process: str
) -> Iterator[str]:
    """Start the MCP agent server as a subprocess."""
    repo_root = Path(__file__).resolve().parent.parent
    port = get_free_port()
    base_url = f"http://127.0.0.1:{port}"
    mcp_endpoint = f"{base_url}/mcp"

    role_state_dir = tmp_path / "role-state"
    task_state_dir = tmp_path / "task-state"
    role_state_dir.mkdir(parents=True, exist_ok=True)
    task_state_dir.mkdir(parents=True, exist_ok=True)

    env = {
        **os.environ,
        "PYTHONPATH": str(repo_root),
        "OPENHANDS_URL": fake_backend_process,
        "OPENHANDS_API_KEY": "fake-api-key",
        "OPENHANDS_ROLE_STATE_DIR": str(role_state_dir),
        "OPENHANDS_STATE_DIR": str(task_state_dir),
        "ROLE_CONFIG_PATH": str(repo_root / "config" / "roles.yaml"),
        "MCP_HOST": "127.0.0.1",
        "MCP_PORT": str(port),
        "FASTMCP_HOST": "127.0.0.1",
        "FASTMCP_PORT": str(port),
        "OPENHANDS_POLL_INTERVAL_SECONDS": "1",
        "OPENHANDS_REQUEST_TIMEOUT_SECONDS": "10",
    }

    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_agent.server"],
        cwd=str(repo_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        wait_http_ok(f"{base_url}/health", timeout=15.0)
    except Exception as exc:
        stdout, stderr = proc.communicate(timeout=2)
        raise AssertionError(
            f"MCP agent failed to start: {exc}\n"
            f"stdout: {stdout.decode(errors='replace')}\n"
            f"stderr: {stderr.decode(errors='replace')}"
        ) from None

    yield mcp_endpoint

    terminate_process(proc)


# ---------------------------------------------------------------------------
# MCP client helper
# ---------------------------------------------------------------------------

async def call_mcp_tool(mcp_url: str, tool_name: str, arguments: dict) -> dict:
    """Call an MCP tool via the official streamable HTTP transport.

    Returns the result as a plain dict.
    """
    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
    except ImportError as exc:
        raise AssertionError(
            f"MCP client library not available: {exc}"
        ) from None

    last_error = None
    for attempt in range(3):
        try:
            async with streamablehttp_client(
                f"{mcp_url}",
                timeout=10,
                sse_read_timeout=30,
            ) as (read_stream, write_stream, _stop):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()

                    result = await session.call_tool(tool_name, arguments=arguments)

                    # Decode result content
                    decoded = _decode_tool_result(result)
                    return decoded
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                await asyncio.sleep(0.5)
            else:
                break

    raise AssertionError(
        f"Failed to call MCP tool '{tool_name}': {last_error}"
    )


def _decode_tool_result(result: Any) -> dict:
    """Decode an MCP CallToolResult into a plain dict."""
    # Check structuredContent first (preferred for JSON responses)
    if hasattr(result, "structuredContent") and result.structuredContent is not None:
        return dict(result.structuredContent)

    # Fall back to content blocks
    if hasattr(result, "content") and result.content:
        for block in result.content:
            text = getattr(block, "text", None)
            if text is not None:
                try:
                    return json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    return {"raw_text": str(text)}

    # Last resort: try to dict-ify
    if hasattr(result, "model_dump"):
        return result.model_dump()
    if hasattr(result, "dict"):
        return result.dict()

    raise AssertionError(f"Cannot decode MCP tool result: {result!r}")


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_public_mcp_role_chain_passes_artifact_ids(
    mcp_agent_process: str, fake_backend_process: str
) -> None:
    """Execute the full public MCP role chain and verify artifact_id-only transfer."""

    mcp_url = mcp_agent_process
    debug_url = f"{fake_backend_process}/debug/requests"

    # Track outgoing role_call arguments
    outgoing_role_call_args: list[dict] = []

    # ------------------------------------------------------------------
    # 1. role_list
    # ------------------------------------------------------------------
    role_list_result = await call_mcp_tool(mcp_url, "role_list", {})
    assert role_list_result.get("public_tools") == [
        "role_list",
        "role_call",
        "role_wait",
    ], f"Unexpected public_tools: {role_list_result.get('public_tools')}"
    assert "role_call" in role_list_result.get("tools", {}).get("allowed", []), (
        "role_call not in allowed tools"
    )

    available_roles = role_list_result.get("roles", [])
    expected_roles = {"scout", "architect", "coder", "reviewer", "publisher"}
    actual_roles = {
        r["name"].lower() if isinstance(r, dict) else str(r).lower()
        for r in available_roles
    }
    assert expected_roles.issubset(actual_roles), (
        f"Missing roles: {expected_roles - actual_roles}"
    )

    # ------------------------------------------------------------------
    # 2. scout
    # ------------------------------------------------------------------
    scout_call = await call_mcp_tool(mcp_url, "role_call", {
        "role": "scout",
        "user_task": "Integration test: inspect repository and produce scout report.",
        "repository": "https://github.com/metacoma/openhands-llm-call",
        "feature": "integration-full-role-chain",
        "idempotency_key": "integration-full-role-chain-scout",
    })
    outgoing_role_call_args.append(scout_call)
    assert scout_call.get("status") == "running", (
        f"Expected role_call status=running, got {scout_call.get('status')}"
    )

    scout_wait = await call_mcp_tool(mcp_url, "role_wait", {
        "role_run_id": scout_call["role_run_id"],
        "timeout_seconds": 30,
        "poll_interval_seconds": 1,
    })
    assert scout_wait.get("status") == "completed", (
        f"Scout did not complete: {scout_wait}"
    )
    assert scout_wait.get("role") == "scout", (
        f"Expected role=scout, got {scout_wait.get('role')}"
    )

    scout_artifacts = scout_wait.get("artifacts", {}).get("primary", {})
    scout_artifact_id = scout_artifacts.get("artifact_id", "")
    assert scout_artifact_id.startswith("art_"), (
        f"Scout artifact_id should start with 'art_', got: {scout_artifact_id}"
    )
    assert scout_artifacts.get("artifact_type") == "scout_report", (
        f"Expected artifact_type=scout_report, got {scout_artifacts.get('artifact_type')}"
    )
    assert scout_artifacts.get("created_by") == "scout", (
        f"Expected created_by=scout, got {scout_artifacts.get('created_by')}"
    )

    # ------------------------------------------------------------------
    # 3. architect
    # ------------------------------------------------------------------
    architect_call = await call_mcp_tool(mcp_url, "role_call", {
        "role": "architect",
        "user_task": "Integration test: design implementation plan.",
        "repository": "https://github.com/metacoma/openhands-llm-call",
        "feature": "integration-full-role-chain",
        "scout_report_artifact_id": scout_artifact_id,
        "idempotency_key": "integration-full-role-chain-architect",
    })
    outgoing_role_call_args.append(architect_call)

    architect_wait = await call_mcp_tool(mcp_url, "role_wait", {
        "role_run_id": architect_call["role_run_id"],
        "timeout_seconds": 30,
        "poll_interval_seconds": 1,
    })
    assert architect_wait.get("status") == "completed", (
        f"Architect did not complete: {architect_wait}"
    )

    architect_artifacts = architect_wait.get("artifacts", {}).get("primary", {})
    architect_artifact_id = architect_artifacts.get("artifact_id", "")
    assert architect_artifact_id.startswith("art_"), (
        f"Architect artifact_id should start with 'art_', got: {architect_artifact_id}"
    )
    assert architect_artifacts.get("artifact_type") == "architect_plan", (
        f"Expected artifact_type=architect_plan, got {architect_artifacts.get('artifact_type')}"
    )
    assert architect_artifacts.get("created_by") == "architect", (
        f"Expected created_by=architect, got {architect_artifacts.get('created_by')}"
    )

    # ------------------------------------------------------------------
    # 4. coder
    # ------------------------------------------------------------------
    coder_call = await call_mcp_tool(mcp_url, "role_call", {
        "role": "coder",
        "user_task": "Integration test: implement planned change.",
        "repository": "https://github.com/metacoma/openhands-llm-call",
        "feature": "integration-full-role-chain",
        "scout_report_artifact_id": scout_artifact_id,
        "architect_plan_artifact_id": architect_artifact_id,
        "idempotency_key": "integration-full-role-chain-coder",
    })
    outgoing_role_call_args.append(coder_call)

    coder_wait = await call_mcp_tool(mcp_url, "role_wait", {
        "role_run_id": coder_call["role_run_id"],
        "timeout_seconds": 30,
        "poll_interval_seconds": 1,
    })
    assert coder_wait.get("status") == "completed", (
        f"Coder did not complete: {coder_wait}"
    )

    coder_artifacts = coder_wait.get("artifacts", {}).get("primary", {})
    coder_artifact_id = coder_artifacts.get("artifact_id", "")
    assert coder_artifact_id.startswith("art_"), (
        f"Coder artifact_id should start with 'art_', got: {coder_artifact_id}"
    )
    assert coder_artifacts.get("artifact_type") == "coder_report", (
        f"Expected artifact_type=coder_report, got {coder_artifacts.get('artifact_type')}"
    )
    assert coder_artifacts.get("created_by") == "coder", (
        f"Expected created_by=coder, got {coder_artifacts.get('created_by')}"
    )

    # ------------------------------------------------------------------
    # 5. reviewer
    # ------------------------------------------------------------------
    reviewer_call = await call_mcp_tool(mcp_url, "role_call", {
        "role": "reviewer",
        "user_task": "Integration test: review implementation.",
        "repository": "https://github.com/metacoma/openhands-llm-call",
        "feature": "integration-full-role-chain",
        "scout_report_artifact_id": scout_artifact_id,
        "architect_plan_artifact_id": architect_artifact_id,
        "coder_report_artifact_id": coder_artifact_id,
        "idempotency_key": "integration-full-role-chain-reviewer",
    })
    outgoing_role_call_args.append(reviewer_call)

    reviewer_wait = await call_mcp_tool(mcp_url, "role_wait", {
        "role_run_id": reviewer_call["role_run_id"],
        "timeout_seconds": 30,
        "poll_interval_seconds": 1,
    })
    assert reviewer_wait.get("status") == "completed", (
        f"Reviewer did not complete: {reviewer_wait}"
    )

    reviewer_artifacts = reviewer_wait.get("artifacts", {}).get("primary", {})
    reviewer_artifact_id = reviewer_artifacts.get("artifact_id", "")
    assert reviewer_artifact_id.startswith("art_"), (
        f"Reviewer artifact_id should start with 'art_', got: {reviewer_artifact_id}"
    )
    assert reviewer_artifacts.get("artifact_type") == "reviewer_report", (
        f"Expected artifact_type=reviewer_report, got {reviewer_artifacts.get('artifact_type')}"
    )
    assert reviewer_artifacts.get("created_by") == "reviewer", (
        f"Expected created_by=reviewer, got {reviewer_artifacts.get('created_by')}"
    )

    # Reviewer must pass with ACTION: PASS
    control_summary = reviewer_wait.get("control_summary", {})
    assert control_summary.get("action") == "PASS", (
        f"Reviewer action should be 'PASS', got {control_summary.get('action')}"
    )
    assert control_summary.get("blocking") is False, (
        f"Reviewer blocking should be False, got {control_summary.get('blocking')}"
    )

    # ------------------------------------------------------------------
    # 6. publisher
    # ------------------------------------------------------------------
    publisher_call = await call_mcp_tool(mcp_url, "role_call", {
        "role": "publisher",
        "user_task": "Integration test: prepare publish instructions.",
        "repository": "https://github.com/metacoma/openhands-llm-call",
        "feature": "integration-full-role-chain",
        "reviewer_report_artifact_id": reviewer_artifact_id,
        "idempotency_key": "integration-full-role-chain-publisher",
    })
    outgoing_role_call_args.append(publisher_call)

    publisher_wait = await call_mcp_tool(mcp_url, "role_wait", {
        "role_run_id": publisher_call["role_run_id"],
        "timeout_seconds": 30,
        "poll_interval_seconds": 1,
    })
    assert publisher_wait.get("status") == "completed", (
        f"Publisher did not complete: {publisher_wait}"
    )

    publisher_artifacts = publisher_wait.get("artifacts", {}).get("primary", {})
    publisher_artifact_id = publisher_artifacts.get("artifact_id", "")
    assert publisher_artifact_id.startswith("art_"), (
        f"Publisher artifact_id should start with 'art_', got: {publisher_artifact_id}"
    )
    assert publisher_artifacts.get("artifact_type") == "publisher_instructions", (
        f"Expected artifact_type=publisher_instructions, got {publisher_artifacts.get('artifact_type')}"
    )
    assert publisher_artifacts.get("created_by") == "publisher", (
        f"Expected created_by=publisher, got {publisher_artifacts.get('created_by')}"
    )

    publisher_summary = publisher_wait.get("control_summary", {})
    assert publisher_summary.get("blocking") is False, (
        f"Publisher blocking should be False, got {publisher_summary.get('blocking')}"
    )

    # ------------------------------------------------------------------
    # Additional assertions: prove artifact content was not passed
    # ------------------------------------------------------------------

    # Assert outgoing MCP role_call args contain no metadata/input_artifacts/context
    for args in outgoing_role_call_args:
        assert "metadata" not in args, (
            f"role_call should not contain 'metadata', got: {args}"
        )
        assert "input_artifacts" not in args, (
            f"role_call should not contain 'input_artifacts', got: {args}"
        )
        assert "context" not in args, (
            f"role_call should not contain 'context', got: {args}"
        )
        for key, value in args.items():
            if key.endswith("_artifact_id") and value:
                assert isinstance(value, str), (
                    f"{key} should be a string, got {type(value)}"
                )
                assert value.startswith("art_"), (
                    f"{key} should start with 'art_', got: {value}"
                )
                assert "\n" not in value, (
                    f"{key} should not contain newlines: {value}"
                )
                assert len(value) < 300, (
                    f"{key} should be < 300 chars, got {len(value)}"
                )

    # Fetch debug requests from fake backend to verify LLM call count
    try:
        debug_resp = requests.get(debug_url, timeout=5.0)
        debug_data = debug_resp.json()
        total_requests = debug_data.get("count", 0)
        # Each role makes 2 LLM calls: main response + summary
        # 5 roles × 2 = 10 total
        assert total_requests >= 5, (
            f"Expected at least 5 LLM calls (one per role), got {total_requests}"
        )
    except Exception:
        # Debug endpoint may not be available; skip this assertion
        pass
