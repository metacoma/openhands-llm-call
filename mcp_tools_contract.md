# MCP Tools Contract

**Public API — exactly three tools: `role_list`, `role_call`, `role_wait`.**

All other tools are internal legacy and hidden from MCP discovery. See [docs/legacy_internal.md](docs/legacy_internal.md) for legacy documentation.

## Public MCP Tools

### 1. role_list

Lists available roles, workflow, rules, and examples. Call this first.

**Response structure:**

```json
{
  "tools": {
    "allowed": ["role_list", "role_call", "role_wait"],
    "forbidden": ["shttp_role_call", "shttp_role_list", "shttp_role_wait", "role_start", "role_status", "role_result", "artifact_get", "*_v2"]
  },
  "workflow": [
    {"step": 1, "role": "scout", "requires": [], "produces": "scout_report"},
    {"step": 2, "role": "architect", "requires": ["scout"], "produces": "architect_plan"},
    {"step": 3, "role": "coder", "requires": ["scout", "architect"], "produces": "coder_report"},
    {"step": 4, "role": "reviewer", "requires": ["scout", "architect", "coder"], "produces": "reviewer_report"},
    {"step": 5, "role": "publisher", "requires": ["reviewer"], "produces": "publisher_instructions"},
    {"step": 6, "role": "coder_fix", "requires": ["architect", "coder", "reviewer"], "produces": "coder_fix_result"}
  ],
  "rules": [
    "Use role_call to start a role.",
    "Use role_wait to wait for a started role.",
    "Never call role_call twice for polling.",
    "Never invent artifact ids.",
    "Pass artifact ids, not artifact contents.",
    "Only one role may run at a time.",
    "Use flat string fields only."
  ],
  "examples": {
    "start_scout": {
      "tool": "role_call",
      "arguments": {
        "role": "scout",
        "user_task": "Analyze repository ...",
        "repository": "https://github.com/metacoma/openhands-llm-call",
        "feature": "llm-proof-mcp-tools",
        "idempotency_key": "llm-proof-mcp-tools-scout"
      }
    },
    "wait": {
      "tool": "role_wait",
      "arguments": {
        "role_run_id": "20260608-abc-scout-1",
        "timeout_seconds": 1800,
        "poll_interval_seconds": 30,
        "return_result": true
      }
    }
  },
  "roles": [...],
  "public_tools": ["role_list", "role_call", "role_wait"],
  "flat_role_call_contract": {...},
  "routing_examples": {...}
}
```

### 2. role_call

Starts one specialist role. Returns immediately with `status: "running"` and `role_run_id`.

**Input (flat scalar fields only):**

| Field | Required | Description |
|---|---|---|
| `role` | Yes | Role name: `scout`, `architect`, `coder`, `reviewer`, `publisher`, `coder_fix` |
| `user_task` | Yes | Plain string task description |
| `repository` | No | Repository URL or path |
| `feature` | No | Short stable feature name |
| `scout_report_artifact_id` | Conditional | Artifact ID from scout (for architect/coder/reviewer) |
| `architect_plan_artifact_id` | Conditional | Artifact ID from architect (for coder/reviewer/coder_fix) |
| `coder_report_artifact_id` | Conditional | Artifact ID from coder (for reviewer/coder_fix/publisher) |
| `reviewer_report_artifact_id` | Conditional | Artifact ID from reviewer (for publisher/coder_fix) |
| `publisher_instructions_artifact_id` | Conditional | Artifact ID from publisher |
| `idempotency_key` | No | Stable key for deduplication |

**Example — scout:**

```json
{
  "role": "scout",
  "user_task": "Analyze repository https://github.com/metacoma/openhands-llm-call and find how to make MCP tools LLM-proof.",
  "repository": "https://github.com/metacoma/openhands-llm-call",
  "feature": "llm-proof-mcp-tools",
  "idempotency_key": "llm-proof-mcp-tools-scout"
}
```

**Example — architect (after scout):**

```json
{
  "role": "architect",
  "user_task": "Plan implementation based on scout report.",
  "scout_report_artifact_id": "art_20260608_abc_scout_report",
  "idempotency_key": "llm-proof-mcp-tools-architect"
}
```

**Response — running:**

```json
{
  "status": "running",
  "role": "scout",
  "role_run_id": "20260608-abc-scout-1",
  "message": "Role started successfully.",
  "next_action": {
    "tool": "role_wait",
    "arguments": {
      "role_run_id": "20260608-abc-scout-1",
      "timeout_seconds": 1800,
      "poll_interval_seconds": 30,
      "return_result": true
    }
  },
  "do_not": [
    "Do not call role_call again for this role_run_id.",
    "Do not start another role until this run is terminal.",
    "Use role_wait to wait for completion."
  ]
}
```

**Response — completed:**

```json
{
  "status": "completed",
  "role": "scout",
  "role_run_id": "20260608-abc-scout-1",
  "control_summary": {
    "blocking": false,
    "action": "CONTINUE",
    "risk_level": "LOW"
  },
  "artifacts": {
    "primary": {
      "artifact_id": "art_20260608_abc_scout_report",
      "artifact_type": "scout_report"
    }
  },
  "next_action": {
    "tool": "role_call",
    "arguments_hint": {
      "role": "architect",
      "scout_report_artifact_id": "art_20260608_abc_scout_report"
    }
  }
}
```

**Response — failed:**

```json
{
  "status": "failed",
  "role": "scout",
  "role_run_id": "20260608-abc-scout-1",
  "error": {
    "type": "RoleExecutionFailed",
    "retryable": false,
    "message": "Human-readable explanation.",
    "next_action": {
      "tool": "role_list",
      "arguments": {}
    }
  }
}
```

### 3. role_wait

Waits for a role run to complete. Polls server-side.

**Input:**

| Field | Required | Default | Description |
|---|---|---|---|
| `role_run_id` | Yes | — | Plain string role run ID |
| `timeout_seconds` | No | 1800 | Max seconds to wait (bounded [1, 86400]) |
| `poll_interval_seconds` | No | 30 | Seconds between checks (bounded [1, 300]) |
| `return_result` | No | true | Whether to include result in response |

**Example:**

```json
{
  "role_run_id": "20260608-abc-scout-1",
  "timeout_seconds": 1800,
  "poll_interval_seconds": 30,
  "return_result": true
}
```

**Response — running (still in progress):**

```json
{
  "status": "running",
  "role_run_id": "20260608-abc-scout-1",
  "message": "Role is still running.",
  "next_action": {
    "tool": "role_wait",
    "arguments": {
      "role_run_id": "20260608-abc-scout-1",
      "timeout_seconds": 1800,
      "poll_interval_seconds": 30,
      "return_result": true
    }
  },
  "do_not": [
    "Do not call role_call again.",
    "Do not start another role while this role is running."
  ]
}
```

**Response — completed:**

```json
{
  "status": "completed",
  "role": "scout",
  "role_run_id": "20260608-abc-scout-1",
  "control_summary": {
    "blocking": false,
    "action": "CONTINUE",
    "risk_level": "LOW"
  },
  "artifacts": {
    "primary": {
      "artifact_id": "art_20260608_abc_scout_report",
      "artifact_type": "scout_report"
    }
  },
  "next_action": {
    "tool": "role_call",
    "arguments_hint": {
      "role": "architect",
      "scout_report_artifact_id": "art_20260608_abc_scout_report"
    }
  }
}
```

**Response — failed:**

```json
{
  "status": "failed",
  "role_run_id": "20260608-abc-scout-1",
  "error": {
    "type": "RoleExecutionFailed",
    "retryable": false,
    "message": "Human-readable explanation.",
    "next_action": {
      "tool": "role_list",
      "arguments": {}
    }
  }
}
```

## Workflow Example

```text
1. role_call(role="scout", user_task="...", repository="...", feature="...", idempotency_key="...")
   → status: "running", role_run_id: "..."

2. role_wait(role_run_id="...")
   → status: "completed", artifacts.primary.artifact_id: "art_..."

3. role_call(role="architect", user_task="...", scout_report_artifact_id="art_...", idempotency_key="...")
   → status: "running", role_run_id: "..."

4. role_wait(role_run_id="...")
   → status: "completed", artifacts.primary.artifact_id: "art_..."

5. role_call(role="coder", user_task="...", scout_report_artifact_id="art_...", architect_plan_artifact_id="art_...", idempotency_key="...")
   → status: "running", role_run_id: "..."

6. role_wait(role_run_id="...")
   → status: "completed" or "failed" with control_summary

7. If reviewer: role_call(role="reviewer", ..., coder_report_artifact_id="art_...", ...)
   → role_wait → action: "PASS" or "BLOCKER"

8. If PASS: role_call(role="publisher", ..., reviewer_report_artifact_id="art_...", ...)
   If BLOCKER: role_call(role="coder_fix", ..., architect_plan_artifact_id="art_...", coder_report_artifact_id="art_...", reviewer_report_artifact_id="art_...", ...)
```

## Flat Payload Rule

All string fields should be **plain strings**. The server accepts and normalizes accidental scalar wrappers (`{"text": ...}`, `{"value": ...}`, `{"default": ...}`, etc.) so they are accepted — prefer plain scalars.

- **Correct:** `"role": "scout"`
- **Also accepted (normalized):** `"role": {"value": "scout"}`, `"role": {"text": "scout"}`
- **Forbidden:** nested `metadata`, `input_artifacts`, or full artifact content as field values

Real nested payloads (metadata, input_artifacts, full artifact content) are rejected with a structured error that includes a `correct_example`.

## Artifact ID Rule

- Pass only `artifact_id` (e.g., `"art_20260608_abc_scout_report"`), not artifact content.
- The MCP server resolves artifact content server-side and injects it into the role's prompt via Jinja.
- Artifact IDs must start with `art_`.

## Error Handling Rule

All errors include:

- `error.type` — machine-readable error type
- `retryable` — whether retrying is safe
- `message` — human-readable explanation
- `next_action` or `correct_example` — guidance for the next step
- `do_not` — anti-patterns to avoid (when applicable)

Common error types:

| Type | Meaning | Action |
|---|---|---|
| `InvalidFlatRoleCallPayload` | Nested `metadata`/`input_artifacts` detected | Use `correct_example` |
| `ArtifactContentAsId` | Artifact content passed as artifact_id | Pass only the artifact ID |
| `RepeatedInvalidToolCall` | Same invalid call repeated >2 times | Stop retrying, use `correct_example` exactly |
| `MissingRequiredArtifact` | Required artifact_id not provided | Call `role_call` for the missing role |
| `AnotherRoleRunning` | A role is already running | Use `role_wait` with existing `role_run_id` |
| `UnknownRole` | Invalid role name | Call `role_list` |
| `InvalidArtifactId` | Artifact ID doesn't start with `art_` | Use a valid artifact ID |
| `MissingRoleRunId` | `role_run_id` not provided | Use the `role_run_id` from `role_call` |

## Forbidden Legacy Tools

The following tools are **hidden** from MCP discovery and must NOT be used:

- `shttp_role_call`, `shttp_role_list`, `shttp_role_wait`
- `role_start`, `role_status`, `role_result`
- `artifact_get`, `artifact_list`
- Any `*_v2` tools

See [docs/legacy_internal.md](docs/legacy_internal.md) for legacy documentation.
