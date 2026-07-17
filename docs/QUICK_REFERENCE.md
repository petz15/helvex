# Multi-LLM Quick Reference

## TL;DR: Per-Key Model Selection ✅

**Decision:** Models are tied to API keys, not orgs.  
**Why:** Simpler billing, better control, cleaner audit trail.  
**Cost:** 50% reduction via batch (Claude/OpenAI).  
**Speed:** Groq <100ms, Claude 2-5s, others 2-5s.

---

## Basic Usage

```python
from app.services.platform.llm import llm_call, resolve_provider_api_key

# Get the right key
api_key = resolve_provider_api_key(db, "openai", org_id=org_id)

# Call any provider
response, tokens = llm_call(
    provider="openai",  # or claude, gemini, deepseek, groq
    system="You are helpful",
    user="Summarize this...",
    api_key=api_key,
    model="gpt-4o",  # or None for default
    max_tokens=256,
)
```

---

## Provider Cheat Sheet

| Provider | Default Model | Speed | Cost | Best For |
|----------|---------------|-------|------|----------|
| **Claude** | claude-sonnet-4-6 | 2-5s | $3–$15/1M | Reasoning, long context |
| **OpenAI** | gpt-4o | 2-5s | $5–$15/1M | Enterprise standard |
| **Gemini** | gemini-2.0-flash | 2-5s | $0.075–$0.30/1M | Cost-efficient vision |
| **DeepSeek** | deepseek-chat | 1-3s | $0.14–$0.28/1M | Balanced |
| **Groq** | llama-3.1-405b | <100ms | $0.59–$0.79/1M | Speed (classification) |

---

## Batch Calls (Claude, OpenAI, Gemini)

```python
from app.services.platform.llm import llm_batch_create, llm_batch_iter_results

# Submit
batch_id = llm_batch_create(
    "claude",
    requests=[
        {
            "custom_id": "req-1",
            "params": {
                "model": "claude-opus-4-7",
                "max_tokens": 256,
                "system": "...",
                "messages": [{"role": "user", "content": "..."}],
            }
        },
        ...
    ],
    api_key=api_key,
)

# Poll & process
for result in llm_batch_iter_results(batch_id, provider="claude", api_key=api_key):
    if result.succeeded:
        print(f"{result.custom_id}: {result.parse_json()}")
    else:
        print(f"{result.custom_id}: ERROR - {result.error}")
```

---

## Configuration (`.env`)

```bash
# All optional—set via dashboard or .env
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-proj-...
GEMINI_API_KEY=AIzaSy...
DEEPSEEK_API_KEY=sk-...
GROQ_API_KEY=gsk_...
```

---

## Admin: Set Up Keys

```bash
# Set platform key
curl -X POST http://localhost:8000/api/v1/admin/api-keys/platform \
  -H "Authorization: Bearer $SUPERADMIN_TOKEN" \
  -d '{"apiKey": "sk-proj-..."}'

# Give org BYO key
curl -X POST http://localhost:8000/api/v1/admin/api-keys/orgs/123/key \
  -d '{"apiKey": "sk-proj-..."}'

# List all orgs + their keys
curl http://localhost:8000/api/v1/admin/api-keys/orgs
```

---

## Error Handling

```python
from openai import OpenAIError, APIError

try:
    response, tokens = llm_call(...)
except ValueError as e:
    # "API key not configured"
    pass
except APIError as e:
    # API error (rate limit, auth fail, etc.)
    pass
```

---

## Token Tracking

Every call returns tokens:
```python
response, tokens = llm_call(...)
input_tokens = tokens["input_tokens"]
output_tokens = tokens["output_tokens"]

# Log to credit ledger
cost = (input_tokens * rate["input"] + output_tokens * rate["output"]) / 1_000_000
```

---

## File Map

```
app/services/
├── claude.py                      # Claude (refactored)
├── llm.py                         # Dispatcher
└── providers/
    ├── claude.py, openai.py, ...  # Provider implementations

app/api/routes/admin/
└── api_keys.py                    # Superadmin API key management

docs/
├── LLM_PROVIDERS.md               # Full usage guide
├── MULTI_LLM_ARCHITECTURE.md      # Architecture & decisions
└── QUICK_REFERENCE.md             # This file
```

---

## Roadmap

- ✅ All 5 providers (Claude, OpenAI, Gemini, DeepSeek, Groq)
- ✅ Batch support (Claude, OpenAI, Gemini)
- ✅ Superadmin key management UI
- ⏳ Per-org rate limiting
- ⏳ Automatic failover
- ⏳ Cost analytics dashboard

---

## Support

**Docs:** See `docs/LLM_PROVIDERS.md`  
**Admin:** Go to **Settings → Admin → API Key Control Center**  
**API:** See `app/api/routes/admin/api_keys.py` for endpoints
