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

### Public Role Tools

| Tool | Purpose |
|---|---|
| `shttp_role_list` | List available roles and their contracts |
| `shttp_role_call` | Call a specialist (single tool for all role orchestration) |

#### `shttp_role_list`

Returns a list of available roles with their contracts.

**Example response:**

```json
{
  "roles": [
    {
      "name": "scout",
      "readonly": true,
      "requires_artifacts": [],
      "output_artifact_type": "scout_report"
    },
    {
      "name": "architect",
      "readonly": true,
      "requires_artifacts": ["scout_report"],
      "output_artifact_type": "architect_plan"
    }
  ]
}
```

#### `shttp_role_call`

The only public tool Head of IT uses to invoke a worker role. Executes the
full two-step lifecycle (main prompt → summary prompt) synchronously and
returns `control_summary` plus `artifact_id` references — never artifact content.

**Example — scout call:**

```json
{
  "role": "scout",
  "user_task": "Analyze repository https://github.com/metacoma/example",
  "input_artifacts": [],
  "metadata": {
    "repository": "https://github.com/metacoma/example",
    "base_branch": "main"
  }
}
```

**Response:**

```json
{
  "role_run_id": "20260607-xxx-scout-1",
  "run_id": "20260607-xxx",
  "role": "scout",
  "status": "completed",
  "control_summary": {
    "status": "DONE",
    "role": "scout",
    "summary": "Repository analyzed.",
    "blocking": false,
    "risk_level": "LOW",
    "action": null
  },
  "artifacts": {
    "primary": {
      "artifact_id": "art_20260607-xxx_scout_1_scout_report",
      "artifact_type": "scout_report",
      "created_by": "scout"
    },
    "summary": {
      "artifact_id": "art_20260607-xxx_scout_1_control_summary",
      "artifact_type": "control_summary",
      "created_by": "scout"
    }
  }
}
```

**Example — architect call with artifact_id:**

```json
{
  "role": "architect",
  "user_task": "Plan implementation of feature X.",
  "input_artifacts": [
    {
      "artifact_id": "art_20260607-xxx_scout_1_scout_report",
      "artifact_type": "scout_report"
    }
  ],
  "metadata": {
    "repository": "https://github.com/metacoma/example",
    "base_branch": "main"
  }
}
```

**Parameters:**

| Parameter | Required | Description |
|---|---|---|
| `role` | Yes | The role name (e.g. `scout`, `architect`, `coder`, `reviewer`, `publisher`) |
| `user_task` | Yes | The task description |
| `input_artifacts` | No | List of `{"artifact_id": "...", "artifact_type": "..."}` dicts |
| `metadata` | No | Dict with `repository`, `base_branch`, etc. |
| `api_key` | No | OpenHands API key |
| `llm_model` | No | LLM model override |
| `url` | No | OpenHands LLM base URL override |
| `idempotency_key` | No | Deduplication key |

### Legacy Tools (not exposed to Head of IT)

The following tools are kept as internal helpers but are **not** visible to
Head of IT. They may be enabled via `EXPOSE_LEGACY_ROLE_TOOLS=true`.

| Tool | Purpose |
|---|---|
| `role_list` | List available worker roles (backward compat) |
| `role_start` | Legacy role start |
| `role_wait` | Legacy server-side polling |
| `role_status` | Single-shot diagnostic status check |
| `role_result` | Get result of a completed role |
| `artifact_get` | Read artifact content (debug only) |
| `shttp_role_start_v2` | Legacy v2 role start (deprecated) |
| `shttp_role_wait_v2` | Legacy v2 wait (deprecated) |
| `shttp_role_result_v2` | Legacy v2 result (deprecated) |


### Example full role chain (v2)

```text
1. shttp_role_start_v2(scout, user_task="...")
   → control_summary.status = "completed"

2. shttp_role_start_v2(architect, user_task="...", input_artifacts={scout_report: "<path>"})
   → control_summary.status = "completed"

3. shttp_role_start_v2(coder, user_task="...", input_artifacts={scout_report: "<path>", architect_plan: "<path>"})
   → control_summary.status = "completed"

4. shttp_role_start_v2(reviewer, user_task="...", input_artifacts={scout_report: "<path>", architect_plan: "<path>", coder_report: "<path>"})
   → control_summary.action = "PASS" or "BLOCKER"

5. If action = PASS:
     shttp_role_start_v2(publisher, ...)
   If action = BLOCKER:
     shttp_role_start_v2(coder_fix, ...)
```

## LLM-safe MCP usage

### Correct role flow

1. Call ``role_start``.
2. Extract only the string field ``role_run_id``.
3. Call ``role_wait`` with flat JSON arguments.
4. Use ``artifact_get`` to read artifacts.
5. Pass artifact contents to the next role.
6. Start the next role only after the previous role has completed.

**Correct:**

```json
{
  "role_run_id": "RUN-scout-1",
  "timeout_seconds": 1800,
  "poll_interval_seconds": 15,
  "return_result": true
}
```

**Incorrect:**

```json
{
  "role_run_id": {
    "role_run_id": "RUN-scout-1",
    "status": "running"
  }
}
```

If ``role_wait`` fails because of malformed arguments, do **not** call ``role_start`` again.
Retry ``role_wait`` with the existing ``role_run_id``.

### Single-threaded execution

This server assumes the underlying model may only run one role at a time.

**Do not start multiple roles in parallel.**

**Correct:**

```text
role_start scout
role_wait scout
artifact_get scout
role_start architect
role_wait architect
artifact_get architect
role_start coder
role_wait coder
artifact_get coder
role_start reviewer
role_wait reviewer
artifact_get reviewer
```

**Incorrect:**

```text
role_start scout
role_start architect
role_start coder
```

If you attempt to start a second role while the first is still running, the server returns:

```json
{
  "error": "another_role_running",
  "message": "Another role is already running. This MCP server is configured for single-threaded model execution. Wait for the current role using role_wait before starting the next role.",
  "active_role_run_id": "20260606-215637-1c1074-scout-1",
  "active_role": "scout",
  "active_status": "running",
  "next_action": {
    "tool": "role_wait",
    "arguments": {
      "role_run_id": "20260606-215637-1c1074-scout-1",
      "timeout_seconds": 1800,
      "poll_interval_seconds": 15,
      "return_result": true
    }
  }
}
```

### Stale active lock prevention

The server prevents parallel role execution by scanning persisted role-run
records. To avoid **stale locks** (where a persisted `status: "running"`
persists after the actual OpenHands task has completed), the server
refreshes the actual OpenHands task status before treating a non-terminal
record as active.

If the refresh succeeds and the actual status is terminal, the persisted
record is updated and the lock is cleared automatically.

If the refresh fails (OpenHands unavailable), the server treats the role
as active and includes a warning in the error message.

### Troubleshooting

#### another_role_running

This means a previous role is still active or could not be proven terminal.

Use the `next_action` field and call `role_wait` with the provided
`role_run_id`.

Do not call `role_start` again unless the previous role reached a
terminal state.

If you see the message "The active role status could not be refreshed
from OpenHands; the lock may be stale," it means the server could not
verify whether the previous role has actually finished. In this case:

1. Check the OpenHands backend directly for the task status.
2. If the task has completed, you can manually delete or update the
   role-run JSON file in `OPENHANDS_ROLE_STATE_DIR` to clear the stale lock.
3. Alternatively, wait for the OpenHands backend to become available and
   retry.

#### Unknown or missing OpenHands task status

If the server cannot prove that an existing role has reached a terminal
status (e.g., OpenHands returns `"unknown"`, an empty response, or the
API is unavailable), it preserves single-threaded safety and treats the
role as still active.

In this case, `role_start` may return `another_role_running` with
`refresh_failed: true` and a `refresh_warning` explaining the issue.

**What to do:**

1. Follow the `next_action` field and call `role_wait` for the existing
   `role_run_id`.
2. Do NOT start another role until the previous role is confirmed terminal.
3. If the OpenHands backend is temporarily unavailable, wait and retry.
4. If the task has actually completed (verified externally), manually
   update or delete the role-run JSON file in `OPENHANDS_ROLE_STATE_DIR`
   to clear the stale lock.

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
