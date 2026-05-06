# Multi-LLM Provider Architecture

## Decision: Per-Key Model Selection (Not Per-Org)

### Why This Approach?

| Decision | Trade-off |
|----------|-----------|
| **Per-Key Model Selection** ✅ Recommended | Superadmin has full control; models are tied to keys; billing is simple & predictable; easy migration path (old key = old model, new key = new model coexist) |
| Per-Org Model Selection ❌ Alternative | Orgs get flexibility; but complex billing (track model used per call), inconsistent if org has multiple keys, harder to phase out old models |

### How It Works

```
┌─────────────────────────────────────────────────────────────┐
│  Superadmin Dashboard (Settings → Admin → API Keys)         │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  Platform Keys (superadmin configures):                      │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ openai_key_1      → gpt-4o     (tier: premium, $X/mo)   │ │
│  │ openai_key_2      → gpt-4-turbo (tier: standard, $Y/mo) │ │
│  │ claude_key_1      → claude-opus (tier: premium)         │ │
│  │ gemini_key_1      → gemini-flash (tier: standard)       │ │
│  │ groq_key_1        → llama-3.1   (tier: free)            │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                               │
│  When a function runs:                                        │
│    org.api_key → model defaults to what was configured       │
│    (or null → use platform key's default)                    │
│                                                               │
│  Billing:                                                     │
│    Cost is implicit in the key (fixed price per org tier)    │
│    No need to track which model was used in each call        │
└─────────────────────────────────────────────────────────────┘
```

### Benefits

1. **Superadmin Control**
   - Phase out old models by rotating keys
   - Gradually migrate orgs to new models
   - Set cost tier per key (tier A uses expensive key, tier B uses cheap key)

2. **Clean Billing**
   - Cost = function(org tier, provider) — not function(org, model)
   - Simple: if org uses key X, cost is fixed
   - Predictable: no surprise charges for using GPT-4o vs GPT-3.5

3. **Audit Trail**
   - Full history: which keys, which models, when changed
   - No ambiguity about what model was used (it's in the key config)

4. **Flexibility for Large Orgs**
   - Premium org: given access to expensive key (GPT-4o)
   - Standard org: given access to cheaper key (GPT-3.5-turbo)
   - Each org gets ONE key per provider; model is fixed for that key

5. **Multi-Tenant Safety**
   - Org can't accidentally use GPT-4o and rack up charges
   - Org can't switch models without superadmin approval
   - Clear visibility into what each org has access to

---

## System Architecture

### File Structure

```
app/services/
├── claude.py                          # Claude-specific (refactored from before)
├── llm.py                             # Unified dispatcher + provider registry
└── providers/
    ├── __init__.py
    ├── claude.py                      # Claude wrapper (moved from app/services/)
    ├── openai.py                      # OpenAI wrapper (new)
    ├── gemini.py                      # Gemini wrapper (new)
    ├── deepseek.py                    # DeepSeek wrapper (new)
    └── groq.py                        # Groq wrapper (new)

app/api/routes/admin/
├── api_keys.py                        # Superadmin API key management endpoints
└── __init__.py

docs/
├── LLM_PROVIDERS.md                   # Usage guide for all providers
└── MULTI_LLM_ARCHITECTURE.md          # This file
```

### Dispatch Flow

```
User calls:
  llm_call(provider="openai", system="...", user="...", api_key=api_key)
                            ↓
                   [llm.py dispatcher]
                            ↓
            resolve provider module (openai.py)
                            ↓
      openai.openai_call(system, user, api_key=api_key)
                            ↓
          [Create OpenAI client, make request]
                            ↓
            return (response, {"input_tokens": N, ...})
```

### Provider Implementation Pattern

Each provider module (`providers/{provider}.py`) exports:

```python
# Key resolution
def resolve_{provider}_api_key(db, org_id) -> str | None

# Model defaults
def get_{provider}_default_model(db, api_key) -> str

# Immediate calls
def {provider}_call(system, user, *, api_key, model, max_tokens, ...) 
    -> tuple[response, token_stats]

# Batch API (if supported)
def {provider}_batch_create(requests, *, api_key) -> batch_id
def {provider}_batch_poll(batch_id, *, api_key) -> status
def {provider}_batch_iter_results(batch_id, *, api_key, poll=True, ...)
    -> Iterator[BatchResult]

# Batch Result dataclass
@dataclass
class BatchResult:
    custom_id: str
    text: str | None
    error: str | None
    input_tokens: int
    output_tokens: int
```

---

## Provider Comparison

| Feature | Claude | OpenAI | Gemini | DeepSeek | Groq |
|---------|--------|--------|--------|----------|------|
| **API Type** | Native | OpenAI-compat | Native SDK | OpenAI-compat | OpenAI-compat |
| **Batch Support** | ✅ Yes (Batch API) | ✅ Yes (Files) | ⚠️ Pseudo | ❌ No | ❌ No |
| **Vision** | ✅ Yes | ✅ Yes | ✅ Yes | ❌ No | ❌ No |
| **Cost (per 1M)** | $3–$15 | $5–$15 | $0.075–$0.30 | $0.14–$0.28 | $0.59–$0.79 |
| **Speed (latency)** | 2–5s | 2–5s | 2–5s | 1–3s | **<100ms** ⚡ |
| **Concurrency Limits** | High | High | High | Medium | High |
| **Best For** | General reasoning, long context | Industry standard, enterprise | Cost-efficient, vision | Balance of cost & quality | **Speed** (classification, simple tasks) |

### Use Case Recommendations

- **Long-context reasoning, safety-critical** → Claude Opus
- **Industry standard, enterprise-grade** → GPT-4o
- **Cost-sensitive, vision tasks** → Gemini Flash
- **Balance cost & quality** → DeepSeek Chat
- **Fast classification, real-time** → Groq Llama

---

## API Key Management

### Superadmin Operations

**Platform-level keys:**
```
POST /api/v1/admin/api-keys/platform
{
  "apiKey": "sk-proj-..."  // Full key, encrypted at rest
}

GET /api/v1/admin/api-keys/platform
→ { "keyFingerprint": "sk-proj-••••••••...••••", "status": "valid", ... }
```

**Per-org keys (if org has `byo_llm_keys` feature):**
```
POST /api/v1/admin/api-keys/orgs/{org_id}/key
{
  "apiKey": "sk-proj-..."
}

GET /api/v1/admin/api-keys/orgs
→ [
    {
      "orgId": 1,
      "orgName": "Acme Corp",
      "hasBYOFeature": true,
      "hasCustomKey": true,
      "keyFingerprint": "sk-proj-••••••••...",
      "monthlyTokens": 2845000,
      "monthlyCost": 142.25
    },
    ...
  ]
```

**Function-level overrides (optional):**
```
POST /api/v1/admin/api-keys/functions/{function_id}/override
{
  "provider": "custom",
  "apiKey": "sk-proj-..."
}
```

---

## Token Tracking & Billing

### How It Works

1. **Every API call returns token counts:**
   ```python
   response, tokens = llm_call(...)
   # tokens = {"input_tokens": 1234, "output_tokens": 567}
   ```

2. **Caller is responsible for tracking:**
   ```python
   def my_ml_function(db, org_id, data):
       response, tokens = llm_call(provider="openai", ...)
       
       # Log to activity/credit ledger
       cost_usd = (
           tokens["input_tokens"] * 0.000005 +
           tokens["output_tokens"] * 0.000015
       )
       credit_transaction = OrgCreditTransaction(
           org_id=org_id,
           amount=-cost_usd * 10000,  # Convert to credits
           action_type="openai_call",
       )
       db.add(credit_transaction)
       db.commit()
   ```

3. **Batch API (Claude/OpenAI) gets 50% discount:**
   ```python
   # Superadmin can adjust rates based on provider/model tier
   PROVIDER_RATES = {
       "claude": {"input": 0.000003, "output": 0.000015},  # USD per token
       "openai_batch": {"input": 0.0000025, "output": 0.0000075},  # 50% off
       "gemini": {"input": 0.000000075, "output": 0.00000030},
   }
   ```

---

## Security & Multi-Tenancy

### Key Isolation

- **Platform keys:** Never exposed to org-level API calls
  - Only superadmin sees full key (masked as fingerprint in UI)
  - Orgs see only that they're using "Platform default"

- **Org keys (BYO):** Only exposed to that org's API calls
  - Org sets their own key via settings
  - Other orgs cannot see or use it

### Permission Checks

- **Resolve key:** Check org's `byo_llm_keys` feature
  - Org lacks feature → force platform key (silently, no leak)
  - Org has feature → check org's setting first, fallback to platform

- **Set key:** Superadmin-only operation
  - Regular users cannot view or modify API keys
  - Audit log every key change

### Rate Limiting (Future)

```python
# Could add per-org rate limits per provider
RATE_LIMITS = {
    "free_tier": {"openai": 100_tokens/day, "groq": 1000_tokens/day},
    "pro_tier": {"openai": 10M_tokens/month, "groq": unlimited},
}
```

---

## Migration Path: From Claude-Only

**Old code (claude-specific):**
```python
from app.services.claude import claude_call
response, tokens = claude_call(system="...", user="...", api_key=api_key)
```

**New code (provider-agnostic):**
```python
from app.services.llm import llm_call, resolve_provider_api_key

# Resolve which provider/key to use
api_key = resolve_provider_api_key(db, "openai", org_id=org_id)

# Call generic dispatcher
response, tokens = llm_call(
    provider="openai",
    system="...",
    user="...",
    api_key=api_key,
)
```

**Gradual migration:**
1. Keep `app.services.claude` module intact for backward compatibility
2. New features use `app.services.llm` dispatcher
3. Gradually refactor old functions as they're touched

---

## Extending with New Providers

To add a new provider (e.g., Anthropic's new model or Mistral):

1. **Create `app/services/providers/{provider}.py`:**
   ```python
   def resolve_{provider}_api_key(db, org_id) -> str | None: ...
   def get_{provider}_default_model(db, api_key) -> str: ...
   def {provider}_call(system, user, *, api_key, model, max_tokens) -> tuple: ...
   def {provider}_batch_create(requests, *, api_key) -> str: ...  # if supported
   ```

2. **Update `app/services/llm.py` dispatcher:**
   ```python
   elif provider == "mistral":
       from app.services.providers.mistral import mistral_call
       return mistral_call(...)
   
   # Add to PROVIDER_INFO registry
   PROVIDER_INFO["mistral"] = {
       "name": "Mistral AI",
       "models": [...],
       "supports_batch": True,
       ...
   }
   ```

3. **Update config:**
   ```python
   # app/config.py
   mistral_api_key: str = ""
   ```

4. **Add to API key management:**
   ```python
   # app/api/routes/admin/api_keys.py
   # Add routes for /admin/api-keys/orgs/{org_id}/mistral
   ```

---

## Testing Strategy

### Unit Tests

```python
# tests/services/providers/test_openai.py
def test_openai_call():
    api_key = "sk-test-xxx"
    response, tokens = openai_call(
        system="You are helpful",
        user="Hello",
        api_key=api_key,
        model="gpt-3.5-turbo",
        max_tokens=10,
    )
    assert isinstance(response, str)
    assert tokens["input_tokens"] > 0
```

### Integration Tests

```python
# tests/test_llm_dispatcher.py
def test_llm_call_routes_to_correct_provider():
    for provider in ["claude", "openai", "gemini", "deepseek", "groq"]:
        response, tokens = llm_call(
            provider=provider,
            system="Test",
            user="Test",
            api_key="sk-test-" + provider,
        )
        # Verify response type
```

### Mocking for CI/CD

```python
# tests/conftest.py
@pytest.fixture(autouse=True)
def mock_api_calls(monkeypatch):
    """Mock all LLM provider calls in tests."""
    def mock_call(*args, **kwargs):
        return "Mock response", {"input_tokens": 10, "output_tokens": 5}
    
    monkeypatch.setattr("app.services.llm.llm_call", mock_call)
```

---

## Cost Analysis

### Token Pricing (per 1M tokens, USD)

```
Provider       Input    Output   Batch Discount
─────────────────────────────────────────────
Claude         $3.00    $15.00   N/A
OpenAI         $5.00    $15.00   50% (batches)
Gemini         $0.075   $0.30    N/A
DeepSeek       $0.14    $0.28    N/A
Groq           $0.59    $0.79    N/A
```

### Example: Classify 100k companies

**Using Claude Opus (batch):**
- Input: 100k companies × 500 tokens avg = 50M tokens
- Output: 100k × 100 tokens avg = 10M tokens
- Cost: (50M × $3 + 10M × $15) / 1M = $300
- With batch API (50% off): $150

**Using Groq Llama (immediate):**
- Same token usage
- Cost: (50M × $0.59 + 10M × $0.79) / 1M = $37.40
- ~4x cheaper, but slower (1-5s per call vs batch)

**Using Gemini Flash (immediate):**
- Cost: (50M × $0.075 + 10M × $0.30) / 1M = $7.50
- ~50x cheaper than Claude Opus, best for cost-sensitive tasks

---

## Roadmap

- [x] Claude provider (existing)
- [x] OpenAI provider
- [x] Gemini provider
- [x] DeepSeek provider
- [x] Groq provider
- [x] Unified dispatcher
- [x] API key management UI
- [ ] Per-model rate limiting
- [ ] Automatic failover (retry on another provider)
- [ ] Cost analytics dashboard
- [ ] Prompt caching metrics
- [ ] Multi-region API key support
- [ ] Custom fine-tuned model support
