#!/usr/bin/env bash
# test_mcp_call.sh — Submit an LLM call via the MCP agent, poll the job,
# and print the final answer.
#
# This script uses the MCP streamable-http transport to interact with the
# MCP agent server.
#
# Usage:
#   bin/test_mcp_call.sh                    # uses defaults (mock mode)
#   bin/test_mcp_call.sh --real             # uses real OpenHands backend
#   bin/test_mcp_call.sh --url http://...   # custom mcp_agent URL
#   bin/test_mcp_call.sh --delay 30         # mock delay in seconds
#
# Environment variables (override defaults):
#   MCP_AGENT_URL      — MCP agent server URL (default: http://localhost:8002)
#   OPENHANDS_API_KEY  — API key for real mode
#   LLM_MODEL          — LLM model name for real mode
#   MOCK_DELAY         — Mock completion delay in seconds (default: 60)

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
MCP_URL="${MCP_AGENT_URL:-http://localhost:8002}"
API_KEY="${OPENHANDS_API_KEY:-mock-api-key}"
LLM_MODEL="${LLM_MODEL:-mock-model}"
MOCK_DELAY="${MOCK_DELAY:-60}"
POLL_INTERVAL=5
MAX_POLLS=720

MODE="mock"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --url)
            MCP_URL="$2"; shift 2 ;;
        --real)
            MODE="real"; shift ;;
        --delay)
            MOCK_DELAY="$2"; shift 2 ;;
        --help|-h)
            head -20 "$0" | tail -16; exit 0 ;;
        *)
            echo "Unknown option: $1"; exit 1 ;;
    esac
done

# Remove trailing slash
MCP_URL="${MCP_URL%/}"

# ---------------------------------------------------------------------------
# Helper: send JSON-RPC to MCP agent
# ---------------------------------------------------------------------------
send_mcp_request() {
    local method="$1"
    local params="$2"
    local jsonrpc="2.0"
    local id=1

    curl -s -X POST "$MCP_URL/mcp" \
        -H "Content-Type: application/json" \
        -H "Accept: application/json, text/event-stream" \
        -d "{
            \"jsonrpc\": \"$jsonrpc\",
            \"method\": \"$method\",
            \"params\": $params,
            \"id\": $id
        }"
}

# ---------------------------------------------------------------------------
# Step 1: Initialize MCP session
# ---------------------------------------------------------------------------
echo "=== Initializing MCP session ==="
echo "  MCP URL: $MCP_URL"
echo ""

INIT_RESPONSE=$(send_mcp_request 'initialize' '{
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {"name": "test-mcp-client", "version": "1.0.0"}
}')

echo "Init response: $INIT_RESPONSE"
echo ""

# Send initialized notification (no response expected)
curl -s -X POST "$MCP_URL/mcp" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","method":"notifications/initialized","params":{},"id":2}' > /dev/null

# ---------------------------------------------------------------------------
# Step 2: Call call_llm tool with no_wait=true
# ---------------------------------------------------------------------------
echo "=== Calling call_llm tool (no_wait=true) ==="

CALL_RESPONSE=$(send_mcp_request 'tools/call' '{
    "name": "call_llm",
    "arguments": {
        "prompt": "Hello, world. Please respond with a short greeting.",
        "api_key": "'"$API_KEY"'",
        "llm_model": "'"$LLM_MODEL"'",
        "no_wait": true
    }
}')

echo "Call response: $CALL_RESPONSE"
echo ""

# Extract conversation_id (job UID) from MCP response
# MCP returns result wrapped in a "result" field
JOB_UID=$(echo "$CALL_RESPONSE" | python3 -c "
import sys, json
data = json.load(sys.stdin)
# MCP response may be a list of content blocks or a dict with 'result'
if isinstance(data, dict):
    result = data.get('result', data)
    if isinstance(result, dict):
        print(result.get('conversation_id', result.get('answer', '')))
    else:
        print('')
elif isinstance(data, list):
    for item in data:
        if isinstance(item, dict) and 'result' in item:
            r = item['result']
            print(r.get('conversation_id', r.get('answer', '')))
            break
" 2>/dev/null || echo "")

if [[ -z "$JOB_UID" || "$JOB_UID" == "None" ]]; then
    echo "ERROR: No conversation_id found in MCP response."
    echo "The MCP response format may differ. Trying to extract from raw response..."
    # Fallback: try to find any conversation_id in the JSON
    JOB_UID=$(echo "$CALL_RESPONSE" | python3 -c "
import sys, json
data = json.load(sys.stdin)
def find_uid(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == 'conversation_id' and v:
                return str(v)
            result = find_uid(v)
            if result:
                return result
    if isinstance(obj, list):
        for item in obj:
            result = find_uid(item)
            if result:
                return result
    return None
print(find_uid(data) or '')
" 2>/dev/null || echo "")
fi

if [[ -z "$JOB_UID" ]]; then
    echo "ERROR: Could not extract job UID from MCP response."
    exit 1
fi

echo "Job UID: $JOB_UID"
echo ""

# ---------------------------------------------------------------------------
# Step 3: Poll job status via the FastAPI server
# ---------------------------------------------------------------------------
echo "=== Polling job status ==="

# The openhands_llm service runs on port 8001 by default
LLM_URL="${OPENHANDS_LLM_URL:-http://localhost:8001}"

for i in $(seq 1 $MAX_POLLS); do
    STATUS_RESP=$(curl -s "$LLM_URL/v1/jobs/$JOB_UID")
    STATUS=$(echo "$STATUS_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "error")
    ANSWER=$(echo "$STATUS_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('answer',''))" 2>/dev/null || echo "")
    EXEC_STATUS=$(echo "$STATUS_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('execution_status',''))" 2>/dev/null || echo "")

    echo "  [$i/$MAX_POLLS] status=$STATUS exec_status=$EXEC_STATUS"

    if [[ "$STATUS" == "completed" ]]; then
        echo ""
        echo "=== Job completed ==="
        echo ""
        echo "$ANSWER"
        exit 0
    elif [[ "$STATUS" == "failed" ]]; then
        echo ""
        echo "=== Job failed ==="
        echo "Answer: $ANSWER"
        exit 1
    elif [[ "$STATUS" == "not_found" ]]; then
        echo ""
        echo "=== Job not found ==="
        exit 1
    fi

    sleep "$POLL_INTERVAL"
done

echo ""
echo "=== Timed out after $MAX_POLLS polls ==="
exit 1
