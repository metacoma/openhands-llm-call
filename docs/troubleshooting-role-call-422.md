# Debug role_call → /v1/call_lm 422 Errors

## Overview

When `role_call(role=scout)` fails with a 422 error, the diagnostic logging
below helps identify the exact cause.

## 1. Enable debug logs

Add to `docker-compose.yml` (both services):

```yaml
environment:
  MCP_DEBUG_ROLE_CALL: "1"
```

Or override via environment:

```bash
export MCP_DEBUG_ROLE_CALL=1
docker compose up -d --build mcp_agent openhands_llm
```

## 2. Run a minimal endpoint check

```bash
docker compose exec mcp_agent curl -sS http://openhands_llm:8000/v1/call_lm \
  -H "Content-Type: application/json" \
  -d '{"prompt":"ping","api_key":"dummy","no_wait":true,"poll_interval":10,"max_polls":3}'
```

Expected: `HTTP/1.1 200 OK`

## 3. Run role_call and watch logs

```bash
docker compose logs -f mcp_agent openhands_llm | egrep 'role_call|call_lm|validation_error'
```

## 4. Look for these log patterns

### Successful flow:
```
role_call.input correlation_id=... role_type=dict role_preview={'name': 'scout'} user_task_type=str user_task_len=312
role_call.normalized correlation_id=... role=scout user_task_type=str user_task_len=312
role_call.prompt_rendered correlation_id=... role=scout prompt_type=str prompt_len=4821 prompt_preview='You are scout...'
call_lm.request correlation_id=... url=http://openhands_llm:8000/v1/call_lm payload_keys=[...] prompt_type=str prompt_len=4821 api_key_present=true
call_lm.response_ok correlation_id=... status=200 response_keys=['status','task_id','conversation_id'] response_status=running task_id_present=true
```

### Error flow (422):
```
role_call.input correlation_id=... role_type=dict role_preview={'name': 'scout'} user_task_type=dict user_task_len=312
role_call.normalized correlation_id=... role=scout user_task_type=str user_task_len=312
role_call.prompt_rendered correlation_id=... role=scout prompt_type=str prompt_len=4821
call_lm.request correlation_id=... url=http://openhands_llm:8000/v1/call_lm payload_keys=[...] prompt_type=dict prompt_len=0  ← prompt is dict, not str!
call_lm.response_error correlation_id=... status=422 body={"detail":[{"loc":["body","prompt"],"msg":"Input should be a valid string"}]}
request.validation_error path=/v1/call_lm errors=[{"loc":["body","prompt"],"msg":"Input should be a valid string"}] body_preview={'prompt': {'text': '...'}}
```

## 5. Common causes of 422

| Symptom | Likely cause |
|---|---|
| `prompt` type is `dict` | MCP client sent `{"text": "..."}` instead of plain string |
| `api_key_present=false` | `OPENHANDS_API_KEY` env var not set, and no api_key passed |
| `poll_interval` out of range [1, 120] | Value too small or too large |
| `max_polls` out of range [1, 360] | Value too small or too large |

## 6. Disable debug logs

Remove or comment out `MCP_DEBUG_ROLE_CALL: "1"` from `docker-compose.yml`:

```bash
docker compose up -d --build mcp_agent openhands_llm
```
