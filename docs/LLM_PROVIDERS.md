# LLM Provider Integration Guide

This document explains how to use the unified LLM provider system to call any language model provider (Claude, OpenAI, Gemini, DeepSeek, Groq).

## Architecture Overview

### Design Principles

1. **Per-Key Model Selection (Not Per-Org)**
   - Superadmin configures which models are available for each API key
   - Each key has a default model baked in (e.g., `openai_key_1` → `gpt-4o`)
   - Orgs can set their own keys if they have `byo_llm_keys` feature
   - Billing cost is implicit in the key assignment, not discovered at call time

2. **Security & Isolation**
   - Platform keys never exposed to orgs without `byo_llm_keys` feature
   - Key fingerprints only (first 8 + last 4 chars) shown in UI
   - All sensitive operations are superadmin-gated

3. **Provider-Specific Implementation**
   - Each provider has its own module: `app/services/platform/providers/{provider}.py`
   - Unified interface via `app/services/platform/llm.py` dispatcher
   - Batch API support varies by provider (Claude, OpenAI, Gemini support; DeepSeek, Groq don't)

---

## Quick Start

### 1. Configure API Keys (Superadmin Only)

Go to **Settings → Admin → API Key Control Center** (or API directly):

```bash
# Set platform key for OpenAI
POST /api/v1/admin/api-keys/platform
{
  "apiKey": "sk-proj-..."
}

# Enable BYO keys for an org
POST /api/v1/admin/api-keys/orgs/123/key
{
  "apiKey": "sk-proj-..."  # org's own key
}

# Configure function-level override
POST /api/v1/admin/api-keys/functions/claude_classify/override
{
  "provider": "custom",
  "apiKey": "sk-proj-..."
}
```

### 2. Use in Your Code

**Simple synchronous call:**
```python
from app.services.platform.llm import llm_call, resolve_provider_api_key
from sqlalchemy.orm import Session

def my_function(db: Session, org_id: int):
    # Get the right key for this org
    api_key = resolve_provider_api_key(db, "openai", org_id=org_id)
    if not api_key:
        raise ValueError("OpenAI API key not configured")
    
    # Call the model
    response, tokens = llm_call(
        provider="openai",
        system="You are a helpful assistant.",
        user="Summarize this data: ...",
        api_key=api_key,
        model="gpt-4o",  # or None to use key's default
        max_tokens=256,
    )
    
    # Track token usage
    print(f"Used {tokens['input_tokens']} input, {tokens['output_tokens']} output tokens")
    return response
```

**Batch processing (Claude, OpenAI, Gemini):**
```python
from app.services.platform.llm import llm_batch_create, llm_batch_iter_results

def batch_classify(db: Session, org_id: int, companies: list):
    api_key = resolve_provider_api_key(db, "claude", org_id=org_id)
    
    # Build batch requests
    requests = []
    for i, company in enumerate(companies):
        requests.append({
            "custom_id": f"company_{company.id}",
            "params": {
                "model": "claude-opus-4-7",
                "max_tokens": 256,
                "system": "You are a lead scorer...",
                "messages": [{"role": "user", "content": company.purpose}],
            }
        })
    
    # Submit
    batch_id = llm_batch_create("claude", requests, api_key=api_key)
    
    # Poll results
    for result in llm_batch_iter_results(
        batch_id,
        provider="claude",
        api_key=api_key,
        poll=True,  # Wait until batch completes
    ):
        if result.succeeded:
            score = result.parse_json()
            print(f"{result.custom_id}: {score}")
        else:
            print(f"{result.custom_id}: ERROR - {result.error}")
```

---

## Provider Details

### Claude (Anthropic)

**Config Key:** `anthropic_api_key`  
**Models:** `claude-opus-4-7`, `claude-sonnet-4-6`, `claude-haiku-4-5-20251001`  
**Batch Support:** ✅ Yes (Batch API)  
**Vision Support:** ✅ Yes  
**Cost (per 1M tokens):** ~$3 input / $15 output (USD)

```python
from app.services.platform.providers.claude import claude_call
response, tokens = claude_call(
    system="...",
    user="...",
    api_key=api_key,
    model="claude-sonnet-4-6",
    max_tokens=256,
    cache_system=True,  # Enable prompt caching
)
```

### OpenAI

**Config Key:** `openai_api_key`  
**Models:** `gpt-4o`, `gpt-4-turbo`, `gpt-4`, `gpt-3.5-turbo`  
**Batch Support:** ✅ Yes (File-based batches)  
**Vision Support:** ✅ Yes  
**Cost (per 1M tokens):** ~$5 input / $15 output (USD)

```python
from app.services.platform.providers.openai import openai_call
response, tokens = openai_call(
    system="...",
    user="...",
    api_key=api_key,
    model="gpt-4o",
    max_tokens=256,
    temperature=1.0,
)
```

### Google Gemini

**Config Key:** `gemini_api_key`  
**Models:** `gemini-2.0-flash`, `gemini-2.0-flash-lite`, `gemini-1.5-pro`  
**Batch Support:** ⚠️ Pseudo-batch (serial processing with polling)  
**Vision Support:** ✅ Yes  
**Cost (per 1M tokens):** ~$0.075 input / $0.30 output (USD)

```python
from app.services.platform.providers.gemini import gemini_call
response, tokens = gemini_call(
    system="...",
    user="...",
    api_key=api_key,
    model="gemini-2.0-flash",
    max_tokens=256,
    temperature=1.0,
)
```

### DeepSeek

**Config Key:** `deepseek_api_key`  
**Models:** `deepseek-chat`, `deepseek-coder`  
**Batch Support:** ❌ No  
**Vision Support:** ❌ No  
**Cost (per 1M tokens):** ~$0.14 input / $0.28 output (USD)

```python
from app.services.platform.providers.deepseek import deepseek_call
response, tokens = deepseek_call(
    system="...",
    user="...",
    api_key=api_key,
    model="deepseek-chat",
    max_tokens=256,
)
```

### Groq

**Config Key:** `groq_api_key`  
**Models:** `llama-3.1-405b-versatile`, `llama-3.1-70b-versatile`, `mixtral-8x7b-32768`  
**Batch Support:** ❌ No  
**Vision Support:** ❌ No  
**Cost (per 1M tokens):** ~$0.59 input / $0.79 output (USD)  
**Notable:** Ultra-fast inference (ms latency)

```python
from app.services.platform.providers.groq import groq_call
response, tokens = groq_call(
    system="...",
    user="...",
    api_key=api_key,
    model="llama-3.1-405b-versatile",
    max_tokens=256,
)
```

---

## Migrating Existing Code

### Before (Claude-specific):
```python
from app.services.scoring.claude import claude_call

response, tokens = claude_call(
    system="...",
    user="...",
    api_key=api_key,
    max_tokens=256,
)
```

### After (Provider-agnostic):
```python
from app.services.platform.llm import llm_call, resolve_provider_api_key

api_key = resolve_provider_api_key(db, "openai", org_id=org_id)
response, tokens = llm_call(
    provider="openai",
    system="...",
    user="...",
    api_key=api_key,
    max_tokens=256,
)
```

---

## Managing Provider Models

### Superadmin Configures Models

Each API key has a default model. Superadmin can set it via AppSetting:

```python
from app import crud

# Set default model for platform key
crud.upsert_app_setting(
    db,
    key="openai_default_model",
    value="gpt-4o",
)

# Override for specific org
crud.upsert_app_setting(
    db,
    key="openai_default_model",
    value="gpt-4-turbo",
    org_id=123,
)
```

Or via the dashboard: **Settings → Admin → API Keys → Organizations → Edit Org**

### Org Configures Own Model (BYO Key)

If org has `byo_llm_keys` feature, they set their own key + model via:

```python
# Org sets their custom key with preferred model
crud.upsert_app_setting(
    db,
    key="openai_api_key",
    value="sk-proj-mykey",
    org_id=org_id,
)
```

The model is baked into that key (they choose when creating it, or communicate it to superadmin).

---

## Token Tracking & Billing

All calls return token counts:

```python
response, tokens = llm_call(...)
input_tokens = tokens["input_tokens"]
output_tokens = tokens["output_tokens"]

# Store in activity log or credit transaction
credits_consumed = (input_tokens * rate["input"] + output_tokens * rate["output"]) / 1_000_000
```

Each provider module exports token usage; billing is done per-call or per-batch at the caller site.

---

## Error Handling

```python
from openai import OpenAIError
from groq import Groq

try:
    response, tokens = llm_call(
        provider="openai",
        system="...",
        user="...",
        api_key=api_key,
    )
except ValueError as e:
    # API key not configured
    print(f"Config error: {e}")
except Exception as e:
    # Network error, model error, etc.
    print(f"API error: {e}")
```

---

## Performance Tips

1. **Use Batch API for bulk operations**
   - Claude & OpenAI batches get 50% cost reduction
   - Batch processing is asynchronous (set it and forget it)

2. **Cache system prompts (Claude only)**
   - Use `cache_system=True` to enable prompt caching
   - Repeated prompts served from cache (90% input cost reduction after 1024 tokens)

3. **Choose the right model**
   - Use smaller models (Groq Llama, Gemini Flash) for fast, cheap inference
   - Use larger models (Claude Opus, GPT-4o) for complex reasoning
   - See cost table above

4. **Temperature tuning**
   - Lower temperature (0.0) for deterministic outputs (classification, parsing)
   - Higher temperature (1.0+) for creative outputs (brainstorming, summarization)

---

## Troubleshooting

**"API key not configured"**
- Superadmin hasn't set the provider key in Settings or `.env`
- Org doesn't have `byo_llm_keys` feature

**"Unknown provider: xyz"**
- Typo in provider name — must be one of: `claude`, `openai`, `gemini`, `deepseek`, `groq`

**"Batch API not supported for this provider"**
- DeepSeek and Groq don't support batch API
- Use `llm_call()` for synchronous calls instead

**Slow batch processing**
- Batches are queued and processed asynchronously
- Poll every 15-30s; don't expect immediate completion
- For Gemini pseudo-batches, processing is serial (slower)

---

## Future Enhancements

- [ ] Anthropic Batch API cost tracking
- [ ] Multi-provider failover (retry on another provider if one fails)
- [ ] Dynamic model selection based on org tier
- [ ] Per-model rate limiting
- [ ] Cache analytics (prompt cache hit rates)
