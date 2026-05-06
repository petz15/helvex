# API Integration Guide: Multi-LLM Provider System

## How Keys Survive Redeployment

### Two Tiers of Persistence

```
┌─────────────────────────────────────────────────────────────────┐
│                     KEY PERSISTENCE LAYERS                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  1. PLATFORM KEYS (Superadmin-Level)                            │
│  ├─ Storage: Environment variables / Kubernetes Secrets         │
│  ├─ Read at startup: app/config.py settings.openai_api_key     │
│  ├─ Survives redeployment: Via secrets manager                 │
│  └─ Example: OPENAI_API_KEY=sk-proj-...                         │
│                                                                   │
│  2. ORG KEYS (Per-Organization)                                 │
│  ├─ Storage: PostgreSQL app_setting table                       │
│  ├─ Read at runtime: crud.get_effective_setting(...)           │
│  ├─ Survives redeployment: DB persists across pod restart       │
│  ├─ Query: SELECT * FROM app_setting WHERE key='openai_api_key' │
│  └─ Set by: Superadmin dashboard /admin/api-keys               │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

### Kubernetes Deployment Example

**Secret definition (secrets.yaml):**
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: llm-keys
type: Opaque
stringData:
  ANTHROPIC_API_KEY: sk-ant-...
  OPENAI_API_KEY: sk-proj-...
  GEMINI_API_KEY: AIzaSy...
  DEEPSEEK_API_KEY: sk-...
  GROQ_API_KEY: gsk_...
```

**Pod deployment (deployment.yaml):**
```yaml
spec:
  containers:
    - name: app
      env:
        - name: OPENAI_API_KEY
          valueFrom:
            secretKeyRef:
              name: llm-keys
              key: OPENAI_API_KEY
        - name: ANTHROPIC_API_KEY
          valueFrom:
            secretKeyRef:
              name: llm-keys
              key: ANTHROPIC_API_KEY
        # ... etc for other providers
```

**After redeployment:**
- Platform keys are re-injected from Kubernetes Secrets → app/config.py
- Org keys are fetched from PostgreSQL at runtime (no redeployment needed)
- Both persist seamlessly across pod restarts

---

## API Endpoint Integration

### Option 1: API Specifies Provider + Model (Recommended)

The API request specifies which provider and model to use. The wrapper resolves the API key from organization context.

**Request structure:**
```json
{
  "provider": "openai",
  "model": "gpt-4o",
  "system": "You are a lead scorer...",
  "data": { "company": {...} }
}
```

**Implementation:**
```python
from fastapi import APIRouter, Depends
from app.services.llm import llm_call
from app.database import get_db
from sqlalchemy.orm import Session

router = APIRouter()

@router.post("/classify")
async def classify_company(
    request: ClassifyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Classify a company using any LLM provider.
    
    The org_id is derived from current_user, and the API key is resolved
    automatically based on the requested provider.
    """
    org_id = current_user.org_id
    
    response, tokens = llm_call(
        provider=request.provider,  # "openai", "gemini", etc.
        system="You are evaluating Swiss companies...",
        user=f"Company: {request.company_name}\nPurpose: {request.purpose}",
        model=request.model or None,  # None → uses provider's default
        max_tokens=256,
        db=db,                         # Needed for key resolution
        org_id=org_id,                 # Org context
    )
    
    return {
        "score": parse_score(response),
        "tokens_used": tokens,
    }
```

### Option 2: API Has Predefined Provider + Model

The API endpoint is hardcoded to use a specific provider. The wrapper auto-resolves the key.

**Implementation:**
```python
@router.post("/companies/score")
async def score_company(
    request: CompanyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Score a company using Groq (fast, cheap inference)."""
    
    response, tokens = llm_call(
        provider="groq",                    # Fixed
        system="Score this company 0-100",
        user=request.company_description,
        model="llama-3.1-405b-versatile",  # Fixed
        db=db,
        org_id=current_user.org_id,
    )
    
    return {"score": int(response)}
```

### Option 3: Explicit API Key (Advanced)

For admin operations or testing, pass the API key directly.

**Implementation:**
```python
@router.post("/admin/test-llm")
async def test_llm(
    request: TestRequest,
    current_user: User = Depends(require_superadmin),
):
    """Test an LLM provider with explicit key."""
    
    response, tokens = llm_call(
        provider=request.provider,
        system=request.system,
        user=request.user,
        api_key=request.api_key,  # Explicit key, no db lookup
        model=request.model,
    )
    
    return {"response": response, "tokens": tokens}
```

---

## Practical Examples

### Example 1: Classification Endpoint (Flexible Provider)

```python
from pydantic import BaseModel

class ClassifyRequest(BaseModel):
    provider: str = "openai"  # User chooses, defaults to OpenAI
    model: str | None = None
    company_name: str
    purpose: str
    
@router.post("/companies/{company_id}/classify")
async def classify(
    company_id: int,
    request: ClassifyRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Classify a company with user-chosen LLM provider.
    
    Client can request:
    {
      "provider": "gemini",  # Cost-optimized
      "model": "gemini-2.0-flash",
      "company_name": "Acme Corp",
      "purpose": "Software development..."
    }
    """
    org_id = user.org_id
    
    # Validate provider is supported
    ALLOWED_PROVIDERS = ["claude", "openai", "gemini", "deepseek", "groq"]
    if request.provider not in ALLOWED_PROVIDERS:
        raise HTTPException(400, f"Provider must be one of {ALLOWED_PROVIDERS}")
    
    try:
        response, tokens = llm_call(
            provider=request.provider,
            system="""You are a lead quality scorer. Rate 0-100 based on:
            - Industry match (0-40 pts)
            - Geographic focus (0-30 pts)
            - Scale indicators (0-30 pts)
            
            Output ONLY a JSON object: {"score": N, "reasoning": "..."}""",
            user=f"Company: {request.company_name}\n Purpose: {request.purpose}",
            model=request.model,
            max_tokens=256,
            parse_json=True,
            db=db,
            org_id=org_id,
        )
        
        return {
            "company_id": company_id,
            "provider": request.provider,
            "model": request.model or "default",
            "score": response["score"],
            "reasoning": response["reasoning"],
            "tokens_used": tokens,
        }
    except ValueError as e:
        raise HTTPException(403, f"API key not configured: {e}")
    except Exception as e:
        raise HTTPException(500, f"Classification failed: {e}")
```

### Example 2: Batch Job Submission

```python
from app.models.job_run import JobRun

@router.post("/jobs/classify-bulk")
async def submit_batch_classify(
    request: BatchClassifyRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Submit a batch classification job.
    
    Payload:
    {
      "provider": "claude",
      "model": "claude-opus-4-7",
      "company_ids": [1, 2, 3, ...],
      "limit": 10000
    }
    """
    org_id = user.org_id
    
    # Build batch requests
    companies = db.query(Company).filter(Company.id.in_(request.company_ids)).all()
    requests_list = []
    
    for company in companies[:request.limit]:
        requests_list.append({
            "custom_id": f"company_{company.id}",
            "params": {
                "model": request.model or "claude-opus-4-7",
                "max_tokens": 256,
                "system": "Score this company...",
                "messages": [{"role": "user", "content": company.purpose}],
            }
        })
    
    try:
        # Submit batch
        batch_id = llm_batch_create(
            provider=request.provider,
            requests=requests_list,
            db=db,
            org_id=org_id,
        )
        
        # Create job record
        job = JobRun(
            org_id=org_id,
            job_type="llm_batch_classify",
            label=f"Batch classify {len(requests_list)} via {request.provider}",
            status="queued",
            params={
                "provider": request.provider,
                "batch_id": batch_id,
                "company_count": len(requests_list),
            }
        )
        db.add(job)
        db.commit()
        
        return {
            "job_id": job.id,
            "batch_id": batch_id,
            "company_count": len(requests_list),
            "status": "queued",
        }
    except NotImplementedError as e:
        raise HTTPException(400, f"Provider {request.provider} doesn't support batch API")
    except ValueError as e:
        raise HTTPException(403, str(e))
```

### Example 3: Key Resolution Flow

```python
# Inside any API endpoint:

# Step 1: User context
org_id = current_user.org_id
provider = request_body.provider  # "openai"

# Step 2: llm_call auto-resolves key
response, tokens = llm_call(
    provider=provider,
    system="...",
    user="...",
    db=db,                 # Step 2 key resolution uses db
    org_id=org_id,
    # (no api_key parameter)
)

# INTERNALLY (inside llm.py):
# 1. resolve_provider_api_key(db, "openai", org_id)
#    ├─ Check if org has byo_llm_keys feature
#    ├─ If yes: fetch org's openai_api_key from app_setting
#    ├─ If no: use platform key from settings.openai_api_key
#    └─ Return api_key
#
# 2. get_provider_default_model(db, "openai", api_key)
#    ├─ Check if org/provider has default model setting
#    ├─ If not: use hardcoded default (openai → gpt-4o)
#    └─ Return model
#
# 3. Call openai.openai_call(system, user, api_key, model)
```

---

## Configuration Priority

When resolving API keys, this priority order is used:

```
1. Explicit api_key parameter (if provided)
   └─ Override everything, use exactly what's passed

2. Org-level BYO key (if org has byo_llm_keys feature)
   └─ crud.get_effective_setting(db, "openai_api_key", org_id=org_id)

3. Platform key (default)
   └─ settings.openai_api_key (from .env or Kubernetes Secret)

4. No key found
   └─ Raise ValueError("No openai API key configured")
```

---

## Error Handling

```python
from openai import OpenAIError, RateLimitError
from anthropic import APIError as AnthropicError

try:
    response, tokens = llm_call(
        provider="openai",
        system="...",
        user="...",
        db=db,
        org_id=org_id,
    )
except ValueError as e:
    # "Either api_key or db must be provided"
    # "No openai API key configured"
    logger.error(f"Configuration error: {e}")
    return {"error": "LLM not configured for your organization"}

except RateLimitError as e:
    # Provider-specific rate limit hit
    logger.warning(f"Rate limited by {request.provider}: {e}")
    return {"error": "Provider rate limited, retry in 60 seconds"}, 429

except AnthropicError as e:
    # Claude-specific error
    logger.error(f"Claude API error: {e}")
    return {"error": "Claude service error"}, 502

except Exception as e:
    # Network, timeout, unknown
    logger.exception(f"Unexpected LLM error: {e}")
    return {"error": "LLM service error"}, 500
```

---

## Testing

### Unit Test Example

```python
import pytest
from unittest.mock import patch, MagicMock

@pytest.fixture
def mock_llm():
    with patch("app.services.llm.llm_call") as mock:
        mock.return_value = ("Mock response", {"input_tokens": 10, "output_tokens": 5})
        yield mock

def test_classify_endpoint(mock_llm, client, db_session, user):
    response = client.post(
        "/companies/1/classify",
        json={
            "provider": "openai",
            "model": "gpt-4o",
            "company_name": "Acme",
            "purpose": "Software",
        },
        headers={"Authorization": f"Bearer {user.token}"},
    )
    
    assert response.status_code == 200
    mock_llm.assert_called_once()
    call_args = mock_llm.call_args
    assert call_args.kwargs["provider"] == "openai"
    assert call_args.kwargs["db"] is not None
    assert call_args.kwargs["org_id"] == user.org_id
```

---

## Summary

| Scenario | How to Call | Notes |
|----------|-----------|-------|
| **API picks provider** | `llm_call(..., provider="openai", db=db, org_id=org_id)` | Wrapper resolves key automatically |
| **Admin tests key** | `llm_call(..., api_key="sk-proj-xxx")` | Direct key, no resolution |
| **Batch job** | `llm_batch_create("claude", requests, db=db, org_id=org_id)` | Batch API with auto key resolution |
| **Hardcoded provider** | `llm_call(..., provider="groq", db=db, org_id=org_id)` | Fixed provider, flexible org context |

Keys persist via:
- **Platform keys**: Kubernetes Secrets → .env → app/config.py
- **Org keys**: PostgreSQL `app_setting` table → CRUD → resolved at runtime

No code changes needed after redeployment—both key types survive automatically.
