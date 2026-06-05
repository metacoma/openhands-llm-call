#!/usr/bin/env bash
# test_call_llm.sh — Submit an LLM call via the FastAPI server, poll the job,
# and print the final answer.
#
# Usage:
#   bin/test_call_llm.sh                    # uses defaults (mock mode)
#   bin/test_call_llm.sh --real             # uses real OpenHands backend
#   bin/test_call_llm.sh --url http://...   # custom openhands_llm URL
#   bin/test_call_llm.sh --delay 30         # mock delay in seconds
#   bin/test_call_llm.sh --prompt "Hello"   # custom prompt
#
# Environment variables (override defaults):
#   OPENHANDS_LLM_URL  — FastAPI server URL (default: http://localhost:8001)
#   OPENHANDS_API_KEY  — API key for real mode
#   LLM_MODEL          — LLM model name for real mode
#   MOCK_DELAY         — Mock completion delay in seconds (default: 60)

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
LLM_URL="${OPENHANDS_LLM_URL:-http://localhost:8001}"
API_KEY="${OPENHANDS_API_KEY:-mock-api-key}"
LLM_MODEL="${LLM_MODEL:-mock-model}"
MOCK_DELAY="${MOCK_DELAY:-60}"
POLL_INTERVAL=5
MAX_POLLS=720  # 60 minutes max

MODE="mock"
CUSTOM_PROMPT=""

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --url)
            LLM_URL="$2"; shift 2 ;;
        --real)
            MODE="real"; shift ;;
        --delay)
            MOCK_DELAY="$2"; shift 2 ;;
        --prompt)
            CUSTOM_PROMPT="$2"; shift 2 ;;
        --poll-interval)
            POLL_INTERVAL="$2"; shift 2 ;;
        --max-polls)
            MAX_POLLS="$2"; shift 2 ;;
        --help|-h)
            head -20 "$0" | tail -16; exit 0 ;;
        *)
            echo "Unknown option: $1"; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Submit LLM call (no_wait=true)
# ---------------------------------------------------------------------------
echo "=== Submitting LLM call ==="
echo "  Mode:     $MODE"
echo "  URL:      $LLM_URL"
echo "  Poll int: ${POLL_INTERVAL}s"
echo "  Max polls: $MAX_POLLS"
echo ""

if [[ "$MODE" == "real" ]]; then
    RESPONSE=$(curl -s -X POST "$LLM_URL/v1/call_lm" \
        -H "Content-Type: application/json" \
        -d "{
            \"prompt\": \"${CUSTOM_PROMPT:-Hello, world. Please respond with a short greeting.\",
            \"api_key\": \"${API_KEY}\",
            \"llm_model\": \"${LLM_MODEL}\",
            \"no_wait\": true
        }")
else
    RESPONSE=$(curl -s -X POST "$LLM_URL/v1/call_lm" \
        -H "Content-Type: application/json" \
        -d "{
            \"prompt\": \"${CUSTOM_PROMPT:-Hello, world. Please respond with a short greeting.\",
            \"api_key\": \"${API_KEY}\",
            \"llm_model\": \"${LLM_MODEL}\",
            \"no_wait\": true
        }")
fi

# Extract conversation_id (job UID)
JOB_UID=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('conversation_id',''))" 2>/dev/null || echo "")

if [[ -z "$JOB_UID" ]]; then
    echo "ERROR: No conversation_id in response:"
    echo "$RESPONSE"
    exit 1
fi

echo "Job UID: $JOB_UID"
echo ""

# ---------------------------------------------------------------------------
# Poll job status until completion
# ---------------------------------------------------------------------------
echo "=== Polling job status ==="

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
