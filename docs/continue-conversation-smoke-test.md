# Smoke Test: Continue Conversation Flow

## Purpose
Verify that summary/repair prompts are sent to an existing OpenHands conversation
via the `/api/v1/app-conversations/{id}/send-message` endpoint.

## Prerequisites
- OpenHands instance running at `$OPENHANDS_URL` (e.g., http://localhost:3000)
- Valid `$OPENHANDS_API_KEY`
- PR #38 deployed (branch `coder/continue-conversation`)

## Test Steps

### 1. Start a main conversation
```bash
curl -i "$OPENHANDS_URL/api/v1/app-conversations" \
  -H "Authorization: Bearer $OPENHANDS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "sandbox_id": null,
    "conversation_id": null,
    "initial_message": {
      "role": "user",
      "content": [{"type": "text", "text": "Fix PR #38"}],
      "run": true
    },
    "agent_type": "default"
  }'
```
Capture the `app_conversation_id` from the response.

### 2. Send summary prompt via /v1/call_lm
```bash
curl -i "http://localhost:8001/v1/call_lm" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Summarize the changes made",
    "conversation_id": "<CAPTURED_ID>",
    "no_wait": true,
    "api_key": "mock-api-key"
  }'
```

Expected response:
```json
{
  "answer": "",
  "conversation_id": "<CAPTURED_ID>",
  "task_id": "<CAPTURED_ID>",
  "job_id": "<CAPTURED_ID>",
  "status": "running"
}
```

### 3. Verify in OpenHands UI
- Open `$OPENHANDS_URL/conversations/<CAPTURED_ID>` in a browser
- Confirm that a new user message with text "Summarize the changes made" appears
- Confirm that the agent processes the summary prompt

### 4. Verify control_summary parsing
- Wait for the conversation to complete
- Check that `control_summary` is parsed from the assistant's response to the summary prompt, NOT from the main answer

## Acceptance Criteria
- [ ] Summary prompt appears in OpenHands chat as a new user message
- [ ] `/v1/call_lm` response has `task_id == conversation_id` and `job_id == conversation_id`
- [ ] `control_summary` is parsed from the summary prompt response, not the main answer
- [ ] No fallback summary is triggered (summary validation passes)
