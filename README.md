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
              ├── Public role tools
              │   role_list, role_call, role_wait
              ├── Artifact store (mcp_agent/artifact_store.py)
              ├── Role registry (config/roles.yaml)
              ├── Prompt renderer (mcp_agent/prompt_renderer.py)
              ├── Role run store (mcp_agent/role_store.py)
              └── Lock manager (mcp_agent/lock_manager.py)
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
| `role_list` | List available roles and their contracts |
| `role_call` | Start a specialist role (returns `role_run_id`) |
| `role_wait` | Wait for a role to complete (returns `control_summary` + `artifact_id`) |

#### Two-Step Pattern

The public API uses an explicit two-step pattern for role orchestration:

```text
1. role_call(role="scout", user_task="...")
   → returns: {status: "running", role_run_id: "...", run_id: "..."}

2. role_wait(role_run_id="...")
   → returns: {status: "completed", control_summary: {...}, artifacts: {...}}
```

`role_call` starts a specialist and returns **immediately** with `role_run_id`.
It does NOT wait for completion.

`role_wait` polls the role until it completes (or times out) and returns
`control_summary` plus `artifact_id` references — never artifact content.

**Never call `role_call` repeatedly for polling.** If a role is running,
call `role_wait` with the same `role_run_id`.

#### `role_list`

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

#### `role_call`

Starts a specialist role and returns **immediately** with `role_run_id`.
Does NOT wait for completion.

**All fields are plain scalar values.** Do NOT pass `metadata` dict. Do NOT pass `input_artifacts` list.

**Example — scout call (step 1):**

```json
{
  "role": "scout",
  "user_task": "Analyze repository https://github.com/metacoma/example",
  "repository": "https://github.com/metacoma/example",
  "feature": "feature-name",
  "idempotency_key": "feature-scout"
}
```

**Response (step 1):**

```json
{
  "status": "running",
  "role_run_id": "20260607-xxx-scout-1",
  "run_id": "20260607-xxx",
  "role": "scout",
  "message": "Role started. Use role_wait with role_run_id to wait for completion."
}
```

Then wait for completion (step 2):

```json
{
  "role_run_id": "20260607-xxx-scout-1",
  "timeout_seconds": 1800,
  "poll_interval_seconds": 30
}
```

**Response (step 2):**

```json
{
  "status": "completed",
  "role_run_id": "20260607-xxx-scout-1",
  "run_id": "20260607-xxx",
  "role": "scout",
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

**Example — architect call with scout_report_artifact_id (step 1):**

```json
{
  "role": "architect",
  "user_task": "Plan implementation of feature X.",
  "repository": "https://github.com/metacoma/example",
  "feature": "feature-name",
  "scout_report_artifact_id": "art_20260607-xxx_scout_1_scout_report",
  "idempotency_key": "feature-architect"
}
```

**Parameters (flat scalar fields only):**

| Parameter | Required | Description |
|---|---|---|
| `role` | Yes | The role name (e.g. `scout`, `architect`, `coder`, `reviewer`, `publisher`) |
| `user_task` | Yes | The task description |
| `repository` | No | Repository URL (e.g. `https://github.com/...`) |
| `feature` | No | Feature name (e.g. `ruby-grpc-client`) |
| `scout_report_artifact_id` | Conditional | Artifact ID of scout report (`art_...`) |
| `architect_plan_artifact_id` | Conditional | Artifact ID of architect plan (`art_...`) |
| `coder_report_artifact_id` | Conditional | Artifact ID of coder report (`art_...`) |
| `reviewer_report_artifact_id` | Conditional | Artifact ID of reviewer report (`art_...`) |
| `publisher_instructions_artifact_id` | Conditional | Artifact ID of publisher instructions (`art_...`) |
| `idempotency_key` | No | Deduplication key |

> **Note:** `api_key`, `llm_model`, and `url` are internal-only. They are read from environment variables (`OPENHANDS_API_KEY`, `OPENHANDS_LLM_MODEL`, `OPENHANDS_URL`) and must **not** be passed by Head of IT.

### Internal helpers (not exposed to Head of IT)

The following functions are kept as internal helpers in ``server.py``
without the ``@MCP.tool()`` decorator.  They are **not** visible to
Head of IT via MCP tool discovery.

| Function | Purpose |
|---|---|
| ``role_start_impl`` | Legacy role-start implementation (internal) |
| ``role_wait_impl`` | Legacy server-side polling (internal) |
| ``role_status`` | Single-shot diagnostic status check (internal) |
| ``role_result`` | Get result of a completed role (internal) |
| ``artifact_get`` | Read artifact content (debug only) |
| ``_internal_role_start`` | Legacy role start (deprecated, hidden) |
| ``_internal_role_wait`` | Legacy wait (deprecated, hidden) |
| ``_internal_role_result`` | Legacy result (deprecated, hidden) |


### Example full role chain

```text
1. role_call(
     role="scout",
     user_task="Research repo",
     repository="https://github.com/...",
     feature="feature-name",
     idempotency_key="feature-scout"
   )
   → control_summary.status = "completed"
   → artifacts.primary.artifact_id = "art_..._scout_report"

2. role_call(
     role="architect",
     user_task="Plan implementation",
     repository="https://github.com/...",
     feature="feature-name",
     scout_report_artifact_id="art_..._scout_report",
     idempotency_key="feature-architect"
   )
   → control_summary.status = "completed"
   → artifacts.primary.artifact_id = "art_..._architect_plan"

3. role_call(
     role="coder",
     user_task="Implement feature",
     repository="https://github.com/...",
     feature="feature-name",
     scout_report_artifact_id="art_..._scout_report",
     architect_plan_artifact_id="art_..._architect_plan",
     idempotency_key="feature-coder"
   )
   → control_summary.status = "completed"

4. role_call(
     role="reviewer",
     user_task="Review changes",
     repository="https://github.com/...",
     feature="feature-name",
     scout_report_artifact_id="art_..._scout_report",
     architect_plan_artifact_id="art_..._architect_plan",
     coder_report_artifact_id="art_..._coder_report",
     idempotency_key="feature-reviewer"
   )
   → control_summary.action = "PASS" or "BLOCKER"

5. If action = PASS:
     role_call(
       role="publisher",
       user_task="Prepare PR instructions",
       reviewer_report_artifact_id="art_..._reviewer_report",
       idempotency_key="feature-publisher"
     )
   If action = BLOCKER:
     role_call(
       role="coder_fix",
       user_task="Fix blockers",
       architect_plan_artifact_id="art_..._architect_plan",
       coder_report_artifact_id="art_..._coder_report",
       reviewer_report_artifact_id="art_..._reviewer_report",
       idempotency_key="feature-coder-fix"
     )
```

## How it works

### Single-role synchronous call

``role_call`` is the only public tool Head of IT uses to invoke a
worker role.  It executes the **full two-step lifecycle** synchronously:

1. Render the main prompt (with artifact content injected via Jinja).
2. Start an OpenHands conversation and wait for the main response.
3. Save the primary artifact via ``ArtifactStore``.
4. Send a summary prompt into the **same** ``conversation_id``.
5. Wait for the summary response, validate/repair it.
6. Save the summary artifact.
7. Return ``control_summary`` + ``artifact_id`` references only — never
   artifact content.

Head of IT calls ``role_call`` to start a role and ``role_wait`` to poll for completion.
Head of IT never calls ``role_start``, ``role_status``, ``role_result``, or ``artifact_get``.
The MCP server handles all waiting and artifact resolution internally.

### Single-threaded execution

This server assumes the underlying model may only run one role at a time.

**Do not start multiple roles in parallel.**  Call ``role_call``
sequentially — each call blocks until the role completes.

**Correct:**

```text
role_call scout
role_call architect
role_call coder
role_call reviewer
role_call publisher
```

**Incorrect:**

```text
role_call scout
role_call architect   ← do not start until scout completes
role_call coder       ← do not start until architect completes
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

**What to do:**

1. Follow the `next_action` field and call ``role_call`` for the
   existing role (it will return the existing result if already completed).
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
