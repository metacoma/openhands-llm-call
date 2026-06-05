# OpenHands Role-Orchestration Prompts

This archive contains prompts for the first-stage architecture:

```
User / Head-of-IT
  └── OpenHands chat (head_of_it.md prompt)
        └── MCP server (mcp_agent/server.py)
              ├── Existing tools: openhands_start_task, openhands_get_task_status,
              │   openhands_get_task_result, openhands_get_task_events,
              │   openhands_cancel_task, call_llm, check_health, check_job
              └── NEW role tools: role_list, role_start, role_status, role_result
                    ├── Role registry (config/roles.yaml)
                    ├── Prompt renderer (Jinja2)
                    └── Role run store (file-based JSON)
```

## First-Stage Role Orchestration

### Overview

The MCP server exposes **four new role-level tools** on top of the existing
OpenHands long-running task implementation.  The Head of IT uses these tools
to drive a pipeline of specialized roles (scout → architect → coder →
reviewer → publisher).

### Where Prompts Live

```text
prompts/
  head_of_it.md   — main orchestration prompt (used in the OpenHands chat)
  scout.md        — scout role template
  architect.md    — architect role template
  coder.md        — coder role template
  reviewer.md     — reviewer role template
  publisher.md    — publisher role template
```

The Head of IT prompt (`prompts/head_of_it.md`) is **not** itself a worker
role by default.  It is the prompt used in the main OpenHands chat that
calls the MCP role tools.

### Where Roles Config Lives

```text
config/roles.yaml
```

This file defines the five worker roles, their models, prompt templates,
required artifacts, and timeouts.  The path can be overridden via the
`ROLE_CONFIG_PATH` environment variable (default: `config/roles.yaml`).

### Role Definitions

| Role | Model | Timeout | Required Artifacts | Output Artifact |
|---|---|---|---|---|
| scout | openai/qwen36-35b-a3b-coder | 60 min | — | scout_report |
| architect | openai/qwen36-35b-a3b-coder | 60 min | scout_report | architect_plan |
| coder | openai/qwen36-35b-a3b-coder | 120 min | scout_report, architect_plan | coder_report |
| reviewer | openai/qwen36-27b-q8-mtp | 75 min | scout_report, architect_plan, coder_report | reviewer_report |
| publisher | openai/qwen36-35b-a3b-coder | 30 min | reviewer_report | publisher_instructions |

### Example MCP Sequence

```text
1. role_list()
   → Returns the list of available roles.

2. role_start(
      role="scout",
      user_task="Analyze repository and find where to implement feature X",
      repo="https://github.com/metacoma/example",
      base_branch="main",
      branch=null,
      context={},
      artifacts={}
   )
   → Returns: {run_id, role_run_id, role, status: "running", poll_after_seconds: 30}

3. role_status(role_run_id="...")
   → Poll until status == "completed"

4. role_result(role_run_id="...")
   → Returns: {status: "completed", action, risk, artifact_name,
                artifact_path, result_summary, full_result}

5. role_start(
      role="architect",
      user_task="Plan implementation",
      repo="https://github.com/metacoma/example",
      base_branch="main",
      artifacts={"scout_report": "<full_result from step 4>"}
   )
   → Returns: {run_id, role_run_id, role, status: "running", ...}

6. Continue the pipeline: role_status → role_result → role_start for each role.
```

### New MCP Tools

#### `role_list()`

List all available worker roles.

**Input:** none.

**Output:**

```json
{
  "roles": [
    {
      "name": "scout",
      "description": "Read-only repository investigator...",
      "model": "openai/qwen36-35b-a3b-coder",
      "readonly": true,
      "requires_artifacts": [],
      "output_artifact": "scout_report",
      "timeout_minutes": 60
    }
  ]
}
```

#### `role_start(role, user_task, repo, base_branch, branch, context, artifacts)`

Start a named worker role as an OpenHands task.

**Required inputs:** `role`, `user_task`

**Optional inputs:** `repo`, `base_branch`, `branch`, `context`, `artifacts`

**Behavior:**
1. Load role spec from `config/roles.yaml`.
2. Validate required artifacts.
3. Render role-specific prompt via Jinja2.
4. Start OpenHands task using the existing FastAPI backend.
5. Store mapping from `role_run_id` → OpenHands `task_id`.
6. Return immediately (non-blocking).

**Output (success):**

```json
{
  "run_id": "20260605-abc123",
  "role_run_id": "20260605-abc123-scout-1",
  "role": "scout",
  "status": "running",
  "poll_after_seconds": 30
}
```

**Output (failure):**

```json
{
  "status": "failed",
  "error": {
    "type": "UnknownRole",
    "message": "Unknown role 'qa'. Available roles: scout, architect, coder, reviewer, publisher.",
    "retryable": false
  }
}
```

#### `role_status(role_run_id)`

Get the status of a previously started role.

**Input:** `role_run_id` (returned by `role_start`).

**Output:**

```json
{
  "role_run_id": "20260605-abc123-scout-1",
  "run_id": "20260605-abc123",
  "role": "scout",
  "status": "running|completed|failed|timeout|cancelled|unknown",
  "summary": "Short summary if available",
  "has_result": false
}
```

#### `role_result(role_run_id)`

Get the result of a completed role.

**Input:** `role_run_id` (returned by `role_start`).

**Output:**

```json
{
  "role_run_id": "20260605-abc123-scout-1",
  "run_id": "20260605-abc123",
  "role": "scout",
  "status": "completed",
  "action": "CONTINUE",
  "risk": null,
  "artifact_name": "scout_report",
  "artifact_path": "runs/20260605-abc123/01-scout.answer.md",
  "result_summary": "Short summary of the result",
  "full_result": "Full markdown report"
}
```

For the **reviewer** role, `action` is parsed from `ACTION: PASS` or
`ACTION: BLOCKER` in the result, and `risk` from `RISK: LOW|MEDIUM|HIGH`.

For the **publisher** role, `action` is parsed from `PUBLISH_STATUS: READY`
or `PUBLISH_STATUS: BLOCKED`.

### Artifact Storage

When a role completes, the full result is saved to:

```text
<state_dir>/<run_id>/<NN>-<role>.answer.md
```

For example:

```text
.runs/20260605-abc123/01-scout.answer.md
.runs/20260605-abc123/02-architect.answer.md
.runs/20260605-abc123/03-coder.answer.md
.runs/20260605-abc123/04-reviewer.answer.md
.runs/20260605-abc123/05-publisher.answer.md
```

The state directory is controlled by `OPENHANDS_ROLE_STATE_DIR` (default:
`.runs`).

### Backward Compatibility

Existing generic OpenHands tools (`openhands_start_task`,
`openhands_get_task_status`, `openhands_get_task_result`,
`openhands_get_task_events`, `openhands_cancel_task`, `call_llm`,
`check_health`, `check_job`) continue to work unchanged.  Role tools are
strictly additive.

## Files

- `prompts/head_of_it.md` — main orchestration prompt for the OpenHands chat.
- `prompts/scout.md` — read-only repository investigation role.
- `prompts/architect.md` — implementation planning role.
- `prompts/coder.md` — implementation role, creates branch and commits changes.
- `prompts/reviewer.md` — read-only review role, emits PASS/BLOCKER and risk.
- `prompts/publisher.md` — publish instructions role, never pushes and never creates PR.
- `config/roles.yaml` — role registry configuration.
- `roles.example.yaml` — example role registry (identical to config/roles.yaml).
- `mcp_tools_contract.md` — expected MCP tool behavior.
- `mcp_agent/server.py` — MCP server with existing tools + new role tools.
- `mcp_agent/roles.py` — role registry (loads and validates roles.yaml).
- `mcp_agent/prompt_renderer.py` — Jinja2 prompt template renderer.
- `mcp_agent/role_store.py` — file-based role run state store.
- `mcp_agent/role_tools.py` — MCP tool implementations + parsing helpers.
