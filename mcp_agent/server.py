#!/usr/bin/env python3
"""MCP server that proxies to the OpenHands LLM Call FastAPI server."""

import os
import requests
from mcp.server.fastmcp import FastMCP

MCP = FastMCP(
    "openhands-llm-mcp",
    description="MCP server that wraps the OpenHands LLM Call FastAPI endpoints",
)

OPENHANDS_URL = os.getenv("OPENHANDS_URL", "http://localhost:8000").rstrip("/")


@MCP.tool()
def call_llm(
    prompt: str,
    api_key: str,
    llm_model: str | None = None,
    repo: str | None = None,
    branch: str | None = None,
    agent_type: str = "default",
    url: str | None = None,
    conversation_id: str | None = None,
    poll_interval: int = 10,
    max_polls: int = 180,
    no_wait: bool = False,
) -> dict:
    """Create an OpenHands agent conversation and return the final LLM answer.

    Args:
        prompt: The task prompt for the agent.
        api_key: OpenHands API key.
        llm_model: LLM model override (e.g. openai/qwen3:32b).
        repo: GitHub repo full name (owner/repo).
        branch: Branch to use.
        agent_type: OpenHands agent type (default: 'default').
        url: OpenHands base URL override.
        conversation_id: Query existing conversation instead of creating new one.
        poll_interval: Polling interval in seconds.
        max_polls: Maximum polling attempts.
        no_wait: Only start conversation without waiting for completion.
    """
    base = (url or OPENHANDS_URL).rstrip("/")
    payload = {
        "prompt": prompt,
        "api_key": api_key,
        "poll_interval": poll_interval,
        "max_polls": max_polls,
        "no_wait": no_wait,
    }
    if llm_model:
        payload["llm_model"] = llm_model
    if repo:
        payload["repo"] = repo
    if branch:
        payload["branch"] = branch
    if agent_type:
        payload["agent_type"] = agent_type
    if url:
        payload["url"] = url
    if conversation_id:
        payload["conversation_id"] = conversation_id

    resp = requests.post(f"{base}/v1/call_lm", json=payload, timeout=3600)
    resp.raise_for_status()
    return resp.json()


@MCP.tool()
def check_health() -> dict:
    """Check if the OpenHands LLM Call server is healthy."""
    resp = requests.get(f"{OPENHANDS_URL}/health", timeout=10)
    resp.raise_for_status()
    return resp.json()


@MCP.tool()
def check_job(uid: str, url: str | None = None) -> dict:
    """Check the status of an async LLM job by its UID.

    Args:
        uid: The job UID (conversation_id) returned by call_llm with no_wait=True.
        url: OpenHands base URL override (defaults to OPENHANDS_URL env var).
    """
    base = (url or OPENHANDS_URL).rstrip("/")
    resp = requests.get(f"{base}/v1/jobs/{uid}", timeout=120)
    resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    MCP.run(transport="streamable-http")
