# openhands-llm-call

## Overview

`openhands-llm-call` provides a role-based orchestration layer on top of the
OpenHands LLM Call FastAPI backend.  A Head-of-IT agent drives a pipeline of
specialized worker roles (scout → architect → coder → reviewer → publisher)
through MCP tool calls.

## Architecture

```
User / Head-of-IT
  └── OpenHands chat (prompts/head_of_it.md)
        └── MCP server (mcp_agent/server.py)
              ├── Generic OpenHands tools
              │   openhands_start_task, openhands_get_task_status,
              │   openhands_get_task_result, openhands_get_task_events,
              │   openhands_cancel_task, call_llm, check_health, check_job
              ├── Role tools
              │   role_list, role_start, role_status, role_result
              ├── Artifact tools
              │   artifact_list, artifact_get
              ├── Role registry (config/roles.yaml)
              ├── Prompt renderer (mcp_agent/prompt_renderer.py)
              ├── Role run store (mcp_agent/role_store.py)
              ├── Lock manager (mcp_agent/lock_manager.py)
              └── Artifact store (mcp_agent/artifact_store.py)
```

## Installation

1. Clone this repository.
2. Install MCP agent dependencies:

   ```bash
   pip install -r mcp_agent/requirements.txt
   ```

3. (Optional) Install OpenHands LLM Call backend:

   ```bash
   pip install -r openhands_llm/requirements.txt
   ```

## Configuration

### OpenHands backend

| Environment variable | Default | Description |
|---|---|---|
| `OPENHANDS_URL` | `http://localhost:8000` | Base URL of the OpenHands FastAPI server |
| `OPENHANDS_API_KEY` | *(empty)* | API key forwarded to the backend |
| `OPENHANDS_MAX_RUNTIME_SECONDS` | `7200` | Default max runtime (seconds) for tasks |
| `OPENHANDS_POLL_INTERVAL_SECONDS` | `10` | Polling interval (seconds) |
| `OPENHANDS_STATE_DIR` | `/tmp/openhands-llm-call-state` | Directory for generic task persistence |

### Role configuration

| Environment variable | Default | Description |
|---|---|---|
| `ROLE_CONFIG_PATH` | `config/roles.yaml` | Path to the role YAML config |
| `OPENHANDS_ROLE_STATE_DIR` | `.runs` | Directory for role run state, artifacts, and locks |
| `OPENHANDS_ROLE_LOCK_TTL_MINUTES` | `180` | Stale lock timeout (minutes) |

### MCP server bind host/port

| Environment variable | Default      | Description |
|---|---|---|
| `MCP_HOST` | `127.0.0.1` | Bind address for the MCP server. Use `0.0.0.0` in Docker. |
| `MCP_PORT` | `8000` | Bind port for the MCP server. |

### Role configuration

Worker roles are defined in `config/roles.yaml`.  Each role specifies:

- `model` — LLM model to use
- `prompt_template` — Path to the Jinja2 prompt template
- `readonly` — Whether the role is read-only (no code changes)
- `timeout_minutes` — Maximum runtime for this role
- `requires_artifacts` — List of artifact names this role needs
- `output_artifact` — Logical name for this role's output artifact

### Prompts

Worker prompts live in `prompts/<role>.md`:

```text
prompts/
  scout.md        — read-only repository investigation
  architect.md    — implementation planning
  coder.md        — code implementation (mutating)
  reviewer.md     — review with ACTION/RISK output
  publisher.md    — publish instructions with PUBLISH_STATUS
```

The Head-of-IT orchestration prompt lives at `prompts/head_of_it.md`.  It is
meant to be used as the main OpenHands chat prompt, not as a worker role.

## MCP Tools

### Generic OpenHands tools

| Tool | Purpose |
|---|---|
| `openhands_start_task` | Start a non-blocking OpenHands task |
| `openhands_get_task_status` | Poll task status |
| `openhands_get_task_result` | Fetch final task result |
| `openhands_get_task_events` | Retrieve task event log |
| `openhands_cancel_task` | Cancel a running task |
| `call_llm` | High-level LLM call (backward-compatible) |
| `check_health` | Health-check the backend |
| `check_job` | Check an async LLM job by UID |

### Role tools

| Tool | Purpose |
|---|---|
| `role_list` | List available worker roles |
| `role_start` | Start a named worker role |
| `role_status` | Get status of a running role |
| `role_result` | Get the result of a completed role |

#### `role_start`

**Recommended (prompt-only) usage:**

```json
{
  "role": "scout",
  "prompt": "Analyze GitHub repository https://github.com/metacoma/example on main branch. Clone it if necessary. Do not modify files.",
  "context": {
    "run_id": "20260605-abc123",
    "idempotency_key": "initial-scout"
  },
  "artifacts": {}
}
```

**Backward-compatible usage (user_task):**

```json
{
  "role": "scout",
  "user_task": "Analyze repository and find where to implement feature X",
  "context": {
    "run_id": "20260605-abc123",
    "idempotency_key": "initial-scout"
  },
  "artifacts": {}
}
```

**Parameters:**

| Parameter | Required | Description |
|---|---|---|
| `role` | Yes | The role name (e.g. `scout`, `architect`, `coder`, `reviewer`, `publisher`) |
| `prompt` | Yes (or `user_task`) | The task/prompt description. Takes precedence over `user_task` |
| `user_task` | Yes (or `prompt`) | Deprecated alias for `prompt`. Kept for backward compatibility |
| `context` | No | Dict with `run_id`, `idempotency_key`, etc. |
| `artifacts` | No | Mapping of artifact names to content |
| `idempotency_key` | No | Top-level idempotency key (takes precedence over `context.idempotency_key`) |
| `repo` | No | Deprecated. If provided as a dict it is normalized to a string or discarded. No longer passed to OpenHands |
| `base_branch` | No | Deprecated. See `repo` |
| `branch` | No | Deprecated. See `repo` |

**Notes:**

- `repo`, `base_branch`, and `branch` are **deprecated**. Repository URL and branch should be included in the `prompt` text. OpenHands creates an empty/default sandbox with no selected repository metadata.
- `idempotency_key` (top-level or in `context`) prevents duplicate tasks on retry.
- `timeout_minutes` is included in the response from the role config.
- `idempotent_reuse: true` indicates the call was deduplicated.

For mutating roles (`readonly: false`, currently only `coder`), a file-based
lock is acquired on `repo|branch`. Concurrent starts for the same repo/branch
fail with a clear error.

#### `role_result`

```json
{
  "role_run_id": "20260605-abc123-scout-1",
  "include_full_result": false
}
```

- Default `include_full_result=true` preserves existing behavior.
- When `false`, `full_result` is `null` and `full_result_omitted` is `true`,
  but artifact metadata is still returned.

### Artifact tools

| Tool | Purpose |
|---|---|
| `artifact_list` | List artifacts for a run |
| `artifact_get` | Get an artifact by name or role_run_id |

#### `artifact_list`

```json
{
  "run_id": "20260605-abc123"
}
```

Returns `{run_id, artifacts: [...]}` where each artifact includes
`artifact_name`, `role`, `role_run_id`, `artifact_path`, `created_at`.

#### `artifact_get`

```json
{
  "run_id": "20260605-abc123",
  "artifact_name": "scout_report"
}
```

Or by role_run_id:

```json
{
  "run_id": "20260605-abc123",
  "role_run_id": "20260605-abc123-scout-1"
}
```

Returns artifact metadata with `content` field. Path traversal is
prevented — artifacts can only be read from the configured state directory.

## Head-of-IT Usage Example

A typical orchestration sequence:

```text
1. role_list()
   → Returns available roles

2. role_start(
      role="scout",
      user_task="Analyze repository and find where to implement feature X",
      repo="https://github.com/metacoma/example",
      base_branch="main",
      context={"run_id": "20260605-abc123", "idempotency_key": "initial-scout"}
   )
   → Returns: {run_id, role_run_id, role, status: "running", poll_after_seconds: 30, timeout_minutes: 60}

3. role_status(role_run_id="20260605-abc123-scout-1")
   → Poll until status == "completed"

4. role_result(role_run_id="20260605-abc123-scout-1", include_full_result=false)
   → Returns: {status: "completed", action, risk, artifact_path, result_summary, full_result: null, full_result_omitted: true}

5. artifact_get(run_id="20260605-abc123", artifact_name="scout_report")
   → Returns artifact content

6. role_start(
      role="architect",
      user_task="Plan implementation",
      repo="https://github.com/metacoma/example",
      base_branch="main",
      artifacts={"scout_report": "<content from step 5>"}
   )
   → Returns: {run_id, role_run_id, role, status: "running", ...}

7. Continue the pipeline: role_status → role_result → artifact_get → role_start
```

## Long-running task behavior

- **Per-role timeouts**: Each role in `config/roles.yaml` specifies
  `timeout_minutes`. The `coder` role defaults to 120 minutes; `publisher`
  to 30 minutes. This is converted to `max_polls` and passed to the
  OpenHands backend.

- **Mutating role locks**: Roles with `readonly: false` acquire a file-based
  lock on `repo|branch` before starting. Concurrent attempts for the same
  repo/branch fail fast with a clear error. Locks expire after
  `OPENHANDS_ROLE_LOCK_TTL_MINUTES` (default 180 min) for crash recovery.

- **Idempotent role_start**: Providing an `idempotency_key` (top-level or in
  `context`) deduplicates retry calls. The uniqueness scope is
  `run_id:role:idempotency_key`. Duplicate calls return the existing
  `role_run_id` with `idempotent_reuse: true`.

## Docker

Two services are defined in `docker-compose.yml`:

- `openhands_llm` — OpenHands LLM Call FastAPI backend
- `mcp_agent` — MCP server that proxies to the backend

```bash
docker-compose up
```

### MCP server access

**Transport:** Streamable HTTP (SHTTP)

**URL from same Docker network:**
```
http://mcp_agent:8000/mcp
```

**URL from host:**
```
http://127.0.0.1:8002/mcp
```

**Key:** empty or dummy

The MCP server binds to `0.0.0.0:8000` inside the container (configurable via `MCP_HOST`/`MCP_PORT` env vars). From the Docker host, use port `8002` (mapped via `docker-compose.yml`).

## Development

```bash
# Install dependencies
pip install -r mcp_agent/requirements.txt
pip install pytest

# Run tests
python -m pytest

# Byte-compile check
python -m compileall mcp_agent
```

## Testing

Tests are in `tests/`:

| File | Coverage |
|---|---|
| `test_role_tools.py` | parse_action, parse_risk, make_summary, role_list, role_start validation, role_status, role_result |
| `test_role_store.py` | RoleRunStore create/get/update/save_artifact/get_artifact/get_attempt_count |
| `test_roles.py` | load_roles, get_role, list_roles, validation |
| `test_task_store.py` | TaskStore CRUD, idempotency, persistence |
| `test_mcp_tools.py` | Mocked OpenHands integration for all generic tools |
| `test_prompt_renderer.py` | Jinja2 prompt rendering |
| `test_lock_manager.py` | Lock acquire/release/conflict/stale handling |
| `test_artifact_store.py` | Artifact save/list/get/path-traversal prevention |

New tests added for this hardening:

- Idempotency: first call creates task, duplicate call returns existing
- Timeout: per-role `timeout_minutes` converted to `max_polls`
- Locks: acquire, conflict, readonly bypass, release on completion, stale expiry
- Artifacts: save, list, get, path traversal rejection
- `include_full_result`: false omits full_result, true preserves old behavior
