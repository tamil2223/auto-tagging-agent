# Architecture

This document is the canonical source for system design, architecture boundaries, and production hardening direction.

## 1) Core Product Invariant

> Silent miscoding is worse than refusal.

Design consequence: any uncertain, invalid, or unavailable classification path must route to `REVIEW_QUEUE` or `UNKNOWN`, never silent `AUTO_TAG`.

## 2) End-to-End Flow

1. **Ingress**: `POST /transactions/tag` receives tenant-scoped transaction payload.
2. **Auth + tenant scope**: `X-API-Key` must match the target tenant in `data/tenants.json`.
3. **Idempotency guard**: same `(tenant_id, idempotency_key)` returns cached result; conflicting payload returns `409`.
4. **Rule-first routing**: exact normalized vendor key match in rule store yields deterministic `AUTO_TAG` (`source=rule`, `confidence=1.0`).
5. **LLM path (when no rule)**:
   - sanitize OCR text for prompt use,
   - build tenant-scoped CoA prompt + few-shot examples,
   - classify using provider chain (or deterministic fallback when live calls disabled).
6. **Output validation**: enforce schema and CoA membership.
7. **Confidence router**:
   - `>= auto_post_threshold` -> `AUTO_TAG`,
   - `>= review_threshold` -> `REVIEW_QUEUE`,
   - else -> `UNKNOWN`.
8. **Learning loop**:
   - reviewer resolves queue item (`accept`/`correct`),
   - correction can promote deterministic vendor rule for future transactions.

### Component Diagram (MVP)

```mermaid
flowchart TD
    Client[Client / Upstream Event Producer] --> API[FastAPI<br/>app/main.py]
    API --> Service[TaggingService<br/>app/services/tagging_service.py]

    Service --> Pre[Preprocessor<br/>normalize + sanitize OCR]
    Service --> Rule[RuleStore<br/>tenant vendor rules]
    Service --> LLM[LLMClassifier<br/>provider chain or deterministic fallback]
    Service --> Val[Validator + Router<br/>CoA check + confidence routing]

    LLM --> Prompt[llm_prompt.py]
    LLM --> Provider[llm_provider.py]
    LLM --> Fallback[llm_fallback.py]

    Provider --> ExtLLM[(Gemini / Claude / OpenAI)]

    Service --> Audit[(AuditLogStore<br/>SQLite)]
    Service --> Idem[(IdempotencyStore<br/>SQLite)]
    Service --> Review[(ReviewQueueStore<br/>SQLite)]
    Service --> Examples[(ConfirmedExampleStore<br/>SQLite)]
    Service --> Sync[MockAccountingSyncAdapter]

    Review --> Reviewer[Finance Reviewer]
    Reviewer --> API
    Service --> Rule

    subgraph TenantScope[Tenant Isolation Boundary]
      Rule
      Review
      Examples
      Audit
      Idem
    end
```

### Sequence Diagram (Tag + Review Loop)

```mermaid
sequenceDiagram
    participant Client
    participant API as FastAPI (/transactions/tag)
    participant Rule as RuleStore
    participant LLM as LLMClassifier
    participant Val as Validator+Router
    participant RQ as ReviewQueueStore
    participant Audit as AuditLog
    participant Sync as AccountingSync
    participant Reviewer

    Client->>API: POST /transactions/tag
    API->>Rule: match(tenant_id, vendor_key)
    alt Deterministic rule hit
        Rule-->>API: coa_account_id
        API->>Audit: append(AUTO_TAG, source=rule)
        API->>Sync: sync(AUTO_TAG)
        API-->>Client: TaggingResult(AUTO_TAG)
    else No rule
        API->>LLM: classify(transaction, tenant_coa)
        LLM-->>API: output or provider error
        API->>Val: validate + route_by_confidence
        alt AUTO_TAG
            API->>Audit: append(AUTO_TAG, source=llm)
            API->>Sync: sync(AUTO_TAG)
            API-->>Client: TaggingResult(AUTO_TAG)
        else REVIEW_QUEUE
            API->>RQ: add(review item)
            API->>Audit: append(REVIEW_QUEUE)
            API-->>Client: TaggingResult(REVIEW_QUEUE)
            Reviewer->>API: POST /review-queue/{tx_id}/resolve
            API->>RQ: resolve(tx_id)
            API->>Audit: append(AUTO_TAG reviewer result)
            opt action == correct
                API->>Rule: upsert vendor rule
            end
            API->>Sync: sync(AUTO_TAG reviewer result)
            API-->>Reviewer: ReviewResolveResponse
        else UNKNOWN
            API->>Audit: append(UNKNOWN)
            API-->>Client: TaggingResult(UNKNOWN)
        end
    end
```

## 3) Architectural Invariants

| Invariant | Enforcement |
|---|---|
| LLM output must map to tenant CoA only | Validator + CoA membership check before routing |
| 4xx provider errors must not fan out to other providers | LLM classifier stops fallback on 4xx |
| 5xx/timeouts may fallback across providers | Provider chain retries/fallback |
| All decisions are auditable | Audit log append on every terminal result |
| Tenant isolation for reads/writes | Tenant-scoped stores + API key authorization |
| Replay safety in review resolve | Replays must match original `action` + `final_coa_account_id` or return `409` |

## 4) System Components

- **API layer**: `app/main.py` (thin route wiring + auth).
- **Application service**: `app/services/tagging_service.py` (orchestration, business flow).
- **Pipeline modules**:
  - `preprocessor.py` (normalize + OCR sanitization),
  - `rule_engine.py` (deterministic lookup),
  - `llm_prompt.py` / `llm_provider.py` / `llm_classifier.py` / `llm_fallback.py`,
  - `validator.py` and `router.py`.
- **Stores**:
  - `audit_log.py`,
  - `idempotency_store.py`,
  - `review_queue.py`,
  - `confirmed_example_store.py`,
  - `rule_store.py`.
- **Adapter**: `adapters/accounting_sync.py` (mock external accounting integration boundary).

## 5) API Contracts (MVP)

### `POST /transactions/tag`
- Input: `Transaction`
- Output: `TaggingResult`
- Error behaviors:
  - `422` schema violations,
  - `404` unknown tenant,
  - `409` idempotency payload conflict.

### `GET /review-queue/{tenant_id}`
- Returns pending review items for tenant.

### `POST /review-queue/{tx_id}/resolve`
- Input: `{tenant_id, action, final_coa_account_id, reviewer_id?}`
- Output: `ReviewResolveResponse`
- Behaviors:
  - `422` if final CoA not in tenant chart,
  - `404` if queue item missing,
  - `409` if replay payload conflicts with previously resolved payload.

### `GET /audit-log/{tenant_id}`, `GET /rules/{tenant_id}`
- Tenant-scoped read APIs for observability and deterministic rule inspection.

## 6) Failure-Mode Strategy

- **Provider 4xx**: terminal refusal path (`UNKNOWN`), no cross-provider retry.
- **Provider 429**: bounded retry on same provider, then fallback.
- **Provider timeout/5xx**: fallback to next provider; if exhausted -> `UNKNOWN`.
- **Invalid classifier output** (schema/CoA mismatch): `UNKNOWN`.
- **Low-confidence output**: `REVIEW_QUEUE` or `UNKNOWN` by threshold policy.
- **Empty CoA edge case**: deterministic fallback returns safe unknown-style response (no indexing crash path).

## 7) MVP vs Production Boundary

| Concern | MVP (this repo) | Production direction |
|---|---|---|
| Orchestration | `TaggingService`; sync in-process execution | Async workflow with queue + worker fleet |
| Persistence | SQLite (`data/runtime/state.db`) + JSON seed files | Postgres + migrations + backup/restore |
| Auth | Static per-tenant `X-API-Key` | OAuth2/API gateway/mTLS + key rotation |
| LLM integration | LiteLLM, env-driven provider chain | Circuit breakers, budget controls, tenant policies |
| PII | Regex redaction | DLP/NER + policy-based retention |
| Observability | Structured app logs | OpenTelemetry + SLOs + alerting |
| Concurrency control | In-process `threading.RLock` | Distributed locks / transactional constraints |

**Hard MVP deployment constraint**: single-process runtime only. `threading.RLock` is process-local and does not coordinate multi-worker or multi-replica deployments.

## 8) Open Production Architecture Questions

1. Can anonymized retrieval embeddings be shared across tenants, or must retrieval stay strictly tenant-siloed?
2. What queue/DLQ strategy should absorb downstream accounting API rate limits at month-end spikes?
3. Should classification occur at authorization, settlement, or both?
4. What optimistic-locking/version contract should the review queue expose for concurrent finance operators?
5. How should account/tax/tracking dependency graphs be encoded and validated once metadata dimensions expand beyond CoA?
