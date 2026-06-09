# openhands-llm-call

## Overview

`openhands-llm-call` provides a role-based orchestration layer on top of the
OpenHands LLM Call FastAPI backend.  A Head-of-IT agent drives a pipeline of
specialized worker roles (scout → architect → coder → reviewer → publisher)
through MCP tool calls.

## Head of IT public MCP flow

The Head of IT orchestrates specialist roles through exactly **three** public MCP tools:

- `role_list` — List available roles and get routing documentation
- `role_call` — Start exactly one specialist role
- `role_wait` — Wait for a role to complete (polling)

### role_call uses flat scalar fields only

Pass **plain scalar string values** for all flat fields. The server normalizes accidental scalar wrappers (`{"text": ...}`, `{"value": ...}`, `{"default": ...}`) so they are accepted — prefer plain scalars. Do **not** intentionally pass nested objects:

- `metadata` (dict/object)
- `input_artifacts` (list/dict)
- artifact content

Pass artifact ids via dedicated flat fields:

- `scout_report_artifact_id` — from scout `artifacts.primary.artifact_id`
- `architect_plan_artifact_id` — from architect `artifacts.primary.artifact_id`
- `coder_report_artifact_id` — from coder `artifacts.primary.artifact_id`
- `reviewer_report_artifact_id` — from reviewer `artifacts.primary.artifact_id`
- `publisher_instructions_artifact_id` — from publisher `artifacts.primary.artifact_id`

### Example: architect step

```json
{
  "role": "architect",
  "user_task": "Plan Ruby gRPC client",
  "repository": "https://github.com/metacoma/freeplane_plugin_grpc",
  "feature": "ruby-grpc-client",
  "scout_report_artifact_id": "art_20260608-xxx_scout_report",
  "idempotency_key": "ruby-grpc-client-architect"
}
```

### Example: role_wait step

```json
{
  "role_run_id": "20260608-xxx-architect-1",
  "timeout_seconds": 60,
  "poll_interval_seconds": 10
}
```

If the response returns `status: "running"` with `timeout: true`, call `role_wait` again with the **same** `role_run_id`. Never call `role_call` again for polling.

#### Repeated short polling for long-running roles

For long-running roles, **do not** call `role_wait` once with a huge timeout.
Call `role_wait` repeatedly with `timeout_seconds` around 30–60 and `poll_interval_seconds` around 5–10.

A `running`/`timeout` response means the role is still alive; call `role_wait` again with the same `role_run_id`.

**Example flow:**

1. Start the role:

```json
{"role": "coder", "user_task": "..."}
```

2. Wait with short polling:

```json
{"role_run_id": "...", "timeout_seconds": 60, "poll_interval_seconds": 10}
```

3. Repeat step 2 until you receive:

```json
{"status": "completed", "artifacts": {"primary": {"artifact_id": "..."}}}
```

## Architecture

```
User / Head-of-IT
  └── OpenHands chat (prompts/head_of_it.md)
        └── MCP server (mcp_agent/server.py)
              ├── Public MCP tools: role_list, role_call, role_wait
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

Exactly three public MCP tools are exported:

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
  "timeout_seconds": 60,
  "poll_interval_seconds": 10
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

### Example full role chain

Each role uses the **two-step pattern**: `role_call` → `role_wait`.

```text
1. role_call(role="scout", user_task="Research repo", repository="https://github.com/...", feature="feature-name", idempotency_key="feature-scout")
   → {status: "running", role_run_id: "20260608-xxx-scout-1"}

2. role_wait(role_run_id="20260608-xxx-scout-1")
   → {status: "completed", artifacts: {primary: {artifact_id: "art_..._scout_report"}}}

3. role_call(role="architect", user_task="Plan implementation", scout_report_artifact_id="art_..._scout_report", idempotency_key="feature-architect")
   → {status: "running", role_run_id: "20260608-xxx-architect-1"}

4. role_wait(role_run_id="20260608-xxx-architect-1")
   → {status: "completed", artifacts: {primary: {artifact_id: "art_..._architect_plan"}}}

5. role_call(role="coder", user_task="Implement feature", scout_report_artifact_id="art_..._scout_report", architect_plan_artifact_id="art_..._architect_plan", idempotency_key="feature-coder")
   → {status: "running", role_run_id: "20260608-xxx-coder-1"}

6. role_wait(role_run_id="20260608-xxx-coder-1")
   → {status: "completed", artifacts: {primary: {artifact_id: "art_..._coder_report"}}}

7. role_call(role="reviewer", user_task="Review changes", scout_report_artifact_id="art_..._scout_report", architect_plan_artifact_id="art_..._architect_plan", coder_report_artifact_id="art_..._coder_report", idempotency_key="feature-reviewer")
   → {status: "running", role_run_id: "20260608-xxx-reviewer-1"}

8. role_wait(role_run_id="20260608-xxx-reviewer-1")
   → {status: "completed", control_summary: {action: "PASS"}}

9. If action = PASS:
     role_call(role="publisher", user_task="Prepare PR instructions", reviewer_report_artifact_id="art_..._reviewer_report", idempotency_key="feature-publisher")
     → role_wait(...)
   If action = BLOCKER:
     role_call(role="coder_fix", user_task="Fix blockers", architect_plan_artifact_id="art_..._architect_plan", coder_report_artifact_id="art_..._coder_report", reviewer_report_artifact_id="art_..._reviewer_report", idempotency_key="feature-coder-fix")
     → role_wait(...)
```

## How it works

The MCP server exposes three public tools for role orchestration:

- **`role_call`** — starts a specialist role and returns immediately with `status: "running"` and a `role_run_id`. It does **not** wait for completion.
- **`role_wait`** — polls a `role_run_id` until the role reaches a terminal status (`completed` or `failed`). Returns `control_summary` and artifact references.
- **`role_list`** — lists available roles and their artifact requirements.

The only valid lifecycle per role is:

```text
role_call → running (role_run_id) → role_wait → completed (artifacts)
```

Head of IT uses only ``role_call`` and ``role_wait``.
The MCP server handles all waiting and artifact resolution internally.

### Single-threaded execution

This server assumes the underlying model may only run one role at a time.

**Do not start multiple roles in parallel.**  Each role must complete via ``role_wait`` before the next ``role_call`` begins.

**Correct:**

```text
role_call scout → role_wait → role_call architect → role_wait → ...
```

**Incorrect:**

```text
role_call scout
role_call architect   ← do not start until scout completes via role_wait
role_call coder       ← do not start until architect completes via role_wait
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

1. Follow the `next_action` field and call ``role_wait`` for the
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

- **Idempotent role_call**: Providing an `idempotency_key` (top-level or in
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
| `test_role_tools.py` | role_wait integration, prompt template markers |
| `test_role_lifecycle.py` | role_call_start_impl, role_lifecycle_wait_impl |
| `test_role_call.py` | role_call validation, artifact ID resolution, loop guard |
| `test_role_lifecycle_wait.py` | role_wait wrapped args, terminal statuses |
| `test_role_store.py` | RoleRunStore create/get/update/save_artifact |
| `test_roles.py` | load_roles, get_role, list_roles, validation |
| `test_prompt_renderer.py` | Jinja2 prompt rendering |
| `test_lock_manager.py` | Lock acquire/release/conflict/stale handling |
| `test_stale_lock.py` | Stale lock detection and clearing |
| `test_artifact_store.py` | Artifact save/list/get/path-traversal prevention |
| `test_summary_validator.py` | Summary validation and repair |
| `test_safe_logging.py` | Safe diagnostic logging |
| `test_public_mcp_surface.py` | Public MCP tool surface = {role_list, role_call, role_wait} |
