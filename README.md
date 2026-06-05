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
- **mcp_agent** — MCP (Model Context Protocol) server that exposes the LLM call as a tool, enabling integration with AI agents.
- **mock_server** — Fake OpenHands V1 backend for testing without a real OpenHands instance.

## Quick start

### With mock backend (testing)

```bash
docker compose -f docker-compose.test.yml up --build
```

This starts three services:
- **mock_server** on port `8003` — fake OpenHands V1 API
- **openhands_llm** on port `8004` — FastAPI server pointing to the mock
- **mcp_agent** on port `8005` — MCP agent pointing to openhands_llm

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

# Use real backend
bin/test_call_llm.sh --real

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

The MCP agent exposes these tools:

- **call_llm** — Create an OpenHands agent conversation (same as POST /v1/call_lm)
- **check_health** — Check server health
- **check_job** — Check the status of an async job by UID (same as GET /v1/jobs/{uid})

## Stopping services

```bash
# Mock mode
docker compose -f docker-compose.test.yml down

# Real mode
docker compose -f docker-compose.yml down
```
