# openhands-llm-call

FastAPI wrapper around the OpenHands V1 API + MCP agent for LLM interaction.

## Architecture

```
Client ──► mcp_agent (port 8002) ──► openhands_llm (port 8001) ──► OpenHands V1 API
                                                    │
                                                    ▼
                                              Mock server (port 8003)
```

- **openhands_llm** — FastAPI server that wraps OpenHands V1 API calls. Supports both synchronous (blocking) and asynchronous (fire-and-forget with job UID) modes.
- **mcp_agent** — MCP (Model Context Protocol) server that exposes the LLM call as a tool, enabling integration with AI agents. Supports long-running tasks via a non-blocking start + polling pattern.
- **mock_server** — Fake OpenHands V1 backend for testing without a real OpenHands instance.

## Quick start

### With mock backend (testing)

```bash
docker compose -f docker-compose.test.yml up --build
```

This starts three services:
- **mock_server** on port `8003` — fake OpenHands V1 API
- **openhands_llm** on port `8001` — FastAPI server pointing to the mock
- **mcp_agent** on port `8002` — MCP agent pointing to openhands_llm

### With real OpenHands backend

```bash
export OPENHANDS_API_KEY=your-api-key
export OPENHANDS_URL=http://your-openhands-instance:3000
export LLM_MODEL=openai/qwen3:32b

docker compose -f docker-compose.yml up --build
```

This starts two services:
- **openhands_llm** on port `8001`
- **mcp_agent** on port `8002`

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `OPENHANDS_URL` | `http://localhost:3000` | OpenHands V1 API base URL |
| `OPENHANDS_API_KEY` | _(required)_ | Bearer API key for OpenHands |
| `LLM_MODEL` | _(optional)_ | Default LLM model |
| `OPENHANDS_POLL_INTERVAL_SECONDS` | `10` | Polling interval for long-running tasks (seconds) |
| `OPENHANDS_MAX_RUNTIME_SECONDS` | `7200` | Maximum runtime for long-running tasks (seconds, 2 hours) |
| `OPENHANDS_REQUEST_TIMEOUT_SECONDS` | `60` | HTTP request timeout for API calls (seconds) |
| `OPENHANDS_STATE_DIR` | `/tmp/openhands-llm-call-state` | Directory for task state persistence (JSON files) |
| `OPENHANDS_LLM_PORT` | `8001` | Port for openhands_llm service |
| `MCP_AGENT_PORT` | `8002` | Port for mcp_agent service |
| `MOCK_SERVER_PORT` | `8003` | Port for mock_server service |
| `MOCK_DELAY` | `60` | Mock conversation completion delay (seconds) |

## Testing with bin scripts

### Prerequisites

1. Start the mock backend:
   ```bash
   docker compose -f docker-compose.test.yml up --build
   ```

2. Ensure `curl` and `python3` are available on your host.

### Test via FastAPI directly

```bash
# Use mock backend (default)
bin/test_call_llm.sh

# Custom URL
bin/test_call_llm.sh --url http://localhost:8001

# Custom mock delay (faster testing)
MOCK_DELAY=10 bin/test_call_llm.sh

# Custom prompt
bin/test_call_llm.sh --prompt "Explain quantum computing in 3 sentences"

# Custom polling interval
bin/test_call_llm.sh --poll-interval 10 --max-polls 360
```

**What it does:**
1. Sends a POST request to `/v1/call_lm` with `no_wait=true`
2. Prints the returned job UID (conversation_id)
3. Polls `/v1/jobs/{uid}` every 5 seconds until the job completes
4. Prints the final answer when the job is done

### Test via MCP agent

```bash
# Use mock backend (default)
bin/test_mcp_call.sh

# Custom MCP agent URL
bin/test_mcp_call.sh --url http://localhost:8002
```

**What it does:**
1. Initializes an MCP session with the agent
2. Calls the `call_llm` tool with `no_wait=true`
3. Extracts the job UID from the MCP response
4. Polls the job status and prints the final answer

### Expected output (mock mode)

```
=== Submitting LLM call ===
  Mode:     mock
  URL:      http://localhost:8001

Job UID: mock-abc123def456

=== Polling job status ===
  [1/720] status=running exec_status=running
  [2/720] status=running exec_status=running
  ...
  [12/720] status=running exec_status=running
  [13/720] status=completed exec_status=finished

=== Job completed ===

This is a mock LLM response from the test backend.

The mock server simulated a 60-second LLM call and returned
this answer as the final result.
```

## API reference

### POST /v1/call_lm

Create an OpenHands agent conversation.

**Request body:**
```json
{
  "prompt": "Your task here",
  "api_key": "your-api-key",
  "llm_model": "openai/qwen3:32b",
  "no_wait": true,
  "repo": "owner/repo",
  "branch": "main",
  "agent_type": "default",
  "poll_interval": 10,
  "max_polls": 180
}
```

**Response (no_wait=false):**
```json
{
  "answer": "Final LLM answer text...",
  "conversation_id": "abc123",
  "status": "completed"
}
```

**Response (no_wait=true):**
```json
{
  "answer": "",
  "conversation_id": "abc123",
  "status": "no_wait"
}
```

### GET /v1/jobs/{uid}

Check the status of an async job.

**Response:**
```json
{
  "conversation_id": "abc123",
  "status": "running",
  "answer": "",
  "execution_status": "running"
}
```

Status values: `running`, `completed`, `failed`, `not_found`.

### GET /health

Health check endpoint.

**Response:**
```json
{"status": "ok"}
```

## MCP tools

### Long-running task tools (NEW)

These tools support the non-blocking start + polling pattern for long-running
OpenHands tasks (30–50+ minutes).

#### openhands_start_task

Start a new OpenHands task and return a `task_id` immediately.

```json
{
  "tool": "openhands_start_task",
  "arguments": {
    "prompt": "Fix bug X in repo Y",
    "api_key": "your-api-key",
    "llm_model": "openai/qwen3:32b",
    "repo": "owner/repo",
    "branch": "main",
    "idempotency_key": "optional-stable-key"
  }
}
```

**Response:**
```json
{
  "task_id": "abc123",
  "conversation_id": "conv-xyz",
  "status": "running",
  "created_at": "2025-01-01T00:00:00+00:00",
  "message": "Task started. Poll with openhands_get_task_status."
}
```

#### openhands_get_task_status

Poll the status of a previously started task.

```json
{
  "tool": "openhands_get_task_status",
  "arguments": {
    "task_id": "abc123"
  }
}
```

**Response (running):**
```json
{
  "task_id": "abc123",
  "conversation_id": "conv-xyz",
  "status": "running",
  "created_at": "2025-01-01T00:00:00+00:00",
  "updated_at": "2025-01-01T00:30:00+00:00",
  "duration_seconds": 1800,
  "execution_status": "running",
  "progress_hint": "OpenHands is still working"
}
```

**Response (completed):**
```json
{
  "task_id": "abc123",
  "conversation_id": "conv-xyz",
  "status": "completed",
  "answer": "The final answer text...",
  "duration_seconds": 3600
}
```

#### openhands_get_task_result

Get the final answer of a completed task.

```json
{
  "tool": "openhands_get_task_result",
  "arguments": {
    "task_id": "abc123"
  }
}
```

#### openhands_get_task_events

Get events (logs) for a task.

```json
{
  "tool": "openhands_get_task_events",
  "arguments": {
    "task_id": "abc123",
    "limit": 50
  }
}
```

**Response:**
```json
{
  "task_id": "abc123",
  "events": [...],
  "count": 10
}
```

#### openhands_cancel_task

Cancel a task (best-effort; OpenHands API may not support remote cancellation).

```json
{
  "tool": "openhands_cancel_task",
  "arguments": {
    "task_id": "abc123"
  }
}
```

### Backward-compatible tools

- **call_llm** — Create an OpenHands agent conversation. Now defaults to non-blocking mode (returns `task_id`). Use `wait_seconds` for bounded blocking.
- **check_health** — Check server health
- **check_job** — Check the status of an async job by UID (same as GET /v1/jobs/{uid})

### Long-running task workflow

For tasks that may take 30–50 minutes:

1. Call `openhands_start_task` → get `task_id`
2. Poll `openhands_get_task_status` every 10–30 seconds
3. When status is `completed`, call `openhands_get_task_result` for the answer
4. Optionally call `openhands_get_task_events` for intermediate logs

## Stopping services

```bash
# Mock mode
docker compose -f docker-compose.test.yml down

# Real mode
docker compose -f docker-compose.yml down
```
