# Reap AI-CFO — Workflow 1: Transaction Auto-Tagging Agent

An MVP prototype for **Workflow 1 — Transaction auto-tagging and close acceleration** from the Reap take-home prompt.

The agent auto-tags each transaction to a tenant-specific Chart of Accounts (CoA), enforcing one non-negotiable safety constraint:

> **Silent miscoding is worse than refusal.**
> The system never guesses quietly. Low-confidence cases are routed to review or marked UNKNOWN.

---

## Candidate Info

- **Workflow Chosen**: **Workflow 1 — Transaction auto-tagging and close acceleration**

---

## Table of Contents

1. [Executive Summary & Product Philosophy](#1-executive-summary--product-philosophy)
2. [System Architecture](#2-system-architecture)
3. [How This Addresses Workflow 1 Challenges](#3-how-this-addresses-workflow-1-challenges)
4. [LLM Prompt Design](#4-llm-prompt-design)
5. [Data Schemas & Contracts](#5-data-schemas--contracts)
6. [Failure Modes & Edge Cases](#6-failure-modes--edge-cases)
7. [Evaluation Strategy](#7-evaluation-strategy)
8. [Architectural & Technology Choices](#8-architectural--technology-choices)
9. [Production-Readiness Considerations](#9-production-readiness-considerations)
10. [How to Run the MVP](#10-how-to-run-the-mvp)
11. [Explicit Assumptions](#11-explicit-assumptions)

---

## 1. Executive Summary & Product Philosophy

Finance teams spend significant time during close categorizing transactions against their accounting structure. For Reap's customers (mid-market, multi-currency), this is repetitive work at meaningful volume (~250 transactions per month). Today, that accounts for roughly a week of close work per month.

**This MVP's product philosophy is: correctness first, automation second.**

The agent is built around a **hybrid decision system**:

- **Deterministic rules** — fast, reliable, zero-cost for recurring vendor patterns per tenant.
- **LLM classification** — semantic categorization for novel cases, constrained strictly to the tenant's CoA.
- **Human-in-the-loop (HITL)** — any uncertain decision is routed to review. Zero silent errors is a hard invariant, not a nice-to-have.

**Primary success metric**: Auto-Tagging Rate (% of transactions tagged without human intervention), measured per tenant, trending upward over time as the rule store grows.

**Scope (MVP)**: CoA tagging only. Tax codes, tracking categories, and other metadata are intentionally deferred so the correctness-first loop stays high-quality and testable within the time budget.

---

## 2. System Architecture

### 2.1 High-Level Flow

```
[Transaction Event Stream]
        │
        ▼
[1. Pre-processing & Enrichment]
        │   - normalize vendor string (lowercase, collapse whitespace, strip punctuation)
        │   - attach OCR receipt snippet if available
        │   - attach tenant CoA list
        │   - attach tenant vendor→CoA rule store
        ▼
[2. Deterministic Rule Engine]
        │── (rule match found?) ──> AUTO_TAG (source=rule, conf=1.00)
        │                               │
        │                               ▼
        │                        [Mock Accounting Sync] + Audit Log
        ▼ (no match)
[3. PII Stripping]
        │   - strip cardholder name, card last-4, and any personal identifiers from ocr_text
        │   - MVP: regex-based scrubbing of card last-4 patterns and email addresses; named-entity recognition (NER) deferred to production
        │   - only sanitized fields are forwarded to the LLM
        ▼
[4. LLM Classification]  (provider chain: Gemini → Claude → OpenAI, on error/timeout)
        │   - system prompt: role + safety instruction
        │   - injected: tenant CoA list (id + name + description)
        │   - injected: few-shot examples from tenant history
        │   - injected: transaction fields + sanitized OCR snippet
        │   - constrained JSON output: {coa_account_id, confidence, reasoning}
        ▼
[5. Output Validation]
        │   - is JSON schema valid?
        │   - is coa_account_id present in the tenant's CoA?
        │   - is confidence in [0.0, 1.0]?
        │── (any check fails) ──> UNKNOWN + Audit Log
        ▼ (valid output)
[6. Confidence Router]
        │── (conf >= AUTO_POST_THRESHOLD)          ──> AUTO_TAG (source=llm)  ──> [Mock Accounting Sync] + Audit Log
        │── (REVIEW_THRESHOLD <= conf < AUTO_POST) ──> REVIEW_QUEUE           ──> Audit Log
        └── (conf < REVIEW_THRESHOLD)              ──> UNKNOWN                ──> Audit Log
        ▼
[7. Learning Loop]
        - reviewer accepts or corrects suggestion
        - correction → promoted to tenant-specific vendor rule
        - rule store persisted; next identical vendor → deterministic auto-tag
```

### 2.2 Key Invariants

| Invariant | Enforcement point |
| :--- | :--- |
| LLM must only suggest accounts from the tenant's CoA | Application-layer validation after LLM response (not just prompt) |
| Every decision is logged with source, confidence, and reasoning | Audit log written before any sync |
| Provider timeouts/5xx/connection errors → fallback chain exhausted → `UNKNOWN`, never auto-tag | `llm_classifier.py` fallback chain; final catch routes to `UNKNOWN` |
| Provider 4xx (e.g., prompt too long / content policy) → `UNKNOWN`, never auto-tag | `llm_classifier.py` treats 4xx as non-retriable (no fallback) to avoid leaking the same request to additional providers |
| Invalid schema or hallucinated CoA account → `UNKNOWN`, never auto-tag | Output validation layer (Step 5) |
| Thresholds are per-tenant configuration, not hardcoded | `TenantConfig` model |

### 2.3 Project Structure

```
auto-tagging-agent/
├── app/
│   ├── main.py                  # FastAPI entrypoint
│   ├── models.py                # Pydantic schemas (Transaction, CoAAccount, LLMOutput, etc.)
│   ├── config.py                # Tenant config loader (thresholds, CoA, rules)
│   ├── pipeline/
│   │   ├── preprocessor.py      # Vendor normalization, enrichment
│   │   ├── rule_engine.py       # Deterministic vendor→CoA matching
│   │   ├── llm_classifier.py    # Prompt construction + multi-provider LLM call via LiteLLM (Gemini → Claude → OpenAI)
│   │   ├── validator.py         # Output schema + CoA membership validation
│   │   └── router.py            # Confidence-based routing
│   ├── store/
│   │   ├── audit_log.py         # Append-only audit log (SQLite)
│   │   ├── idempotency_store.py # Idempotency cache (SQLite)
│   │   ├── review_queue.py      # Review queue + idempotent resolution replay (SQLite)
│   │   ├── confirmed_example_store.py # Few-shot confirmed examples (SQLite)
│   │   └── rule_store.py        # Tenant vendor→CoA rules (JSON-backed + runtime JSON overlay)
│   └── adapters/
│       └── accounting_sync.py   # Mock accounting platform sync
├── data/
│   ├── tenants.json             # Tenant configs + thresholds
│   ├── runtime/                 # Local runtime state (gitignored): SQLite DB + promoted rules
│   ├── coa/
│   │   ├── tenant_a.json        # Tenant A CoA
│   │   └── tenant_b.json        # Tenant B CoA
│   └── rules/
│       ├── tenant_a_rules.json  # Learned vendor rules for tenant A
│       └── tenant_b_rules.json
├── scripts/
│   ├── demo_scenario.py         # Runnable end-to-end demo (see §10)
│   └── smoke_test.sh            # Optional curl smoke against a running server
├── tests/
│   ├── test_rule_engine.py
│   ├── test_validator.py
│   ├── test_router.py
│   └── eval/
│       ├── eval_runner.py       # Offline eval harness
│       └── fixtures/
│           └── edge_cases.json  # Long-tail vendor eval dataset
├── pytest.ini                   # pytest import path hardening (repo root)
├── requirements.txt
└── README.md
```

### 2.4 MVP API contracts (FastAPI)

The MVP is intentionally API-first: the “transaction event stream” is simulated by HTTP endpoints. All endpoints are tenant-scoped via `tenant_id` in the request body or path.

**Authentication (MVP)**: tenant-scoped reads and writes require the `X-API-Key` header matching the per-tenant key in `data/tenants.json`. This is intentionally simple for the take-home; production would use OAuth2/mTLS/service accounts.

#### 2.4.1 Classify/tag a transaction

- **Method**: `POST`
- **Route**: `/transactions/tag`
- **Request body**: `Transaction` (see §5.1)
- **Response**: `TaggingResult` (see §5.4)

Success semantics:

- The endpoint always returns a `TaggingResult` even when the LLM fails, by routing to `UNKNOWN` (business-safe refusal). LLM failures are not surfaced as 5xx API failures.
- If `idempotency_key` has already been processed for this tenant, the endpoint returns the previously computed `TaggingResult`.

Error codes (minimal MVP set):

- **422 Unprocessable Entity**: request schema validation failed (Pydantic).
- **404 Not Found**: `tenant_id` does not exist.
- **409 Conflict**: `idempotency_key` was previously seen but the payload differs (guards against accidental reuse).

#### 2.4.2 Review queue actions (HITL)

- **List review items**
  - **Method**: `GET`
  - **Route**: `/review-queue/{tenant_id}`
  - **Response**: list of pending items (implementation detail; at minimum includes `tx_id`, suggested `coa_account_id`, `confidence`, `reasoning`)
- **Resolve a queued item**
  - **Method**: `POST`
  - **Route**: `/review-queue/{tx_id}/resolve`
  - **Request body**: `{ "tenant_id": "...", "action": "accept" | "correct", "final_coa_account_id": "..." }`
  - **Response**: updated `TaggingResult` + rule creation metadata if applicable
  - **422**: if `final_coa_account_id` is not in tenant CoA

Production note: for strict tenant isolation, prefer scoping the resolve route by tenant in the path (e.g., `POST /tenants/{tenant_id}/review-queue/{tx_id}/resolve`) rather than relying on `tenant_id` in the request body.

---

## 3. How This Addresses Workflow 1 Challenges

### 3.1 Confidence Thresholds & HITL Review

The LLM returns a structured object with three fields:

```json
{
  "coa_account_id": "6200",
  "confidence": 0.91,
  "reasoning": "Vendor 'aws-marketplace' matches cloud infrastructure patterns; closest account is 6200 (Cloud & Hosting)."
}
```

Routing logic (thresholds are per-tenant config knobs):

| Confidence range | Default threshold | Action |
| :--- | :--- | :--- |
| `>= AUTO_POST_THRESHOLD` | 0.85 | `AUTO_TAG` — sync to accounting platform |
| `>= REVIEW_THRESHOLD` | 0.50 | `REVIEW_QUEUE` — surfaced to finance team |
| `< REVIEW_THRESHOLD` | < 0.50 | `UNKNOWN` — logged, held for manual coding |
| Invalid schema or account not in CoA | — | `UNKNOWN` — always |

During cold start (no rules, no history), `AUTO_POST_THRESHOLD` is raised to **0.95** to bias toward review over silent errors.

The `reasoning` field is shown to the reviewer in the UI, making it easy to accept or correct the suggestion with context.

### 3.1.1 Deterministic rule matching (MVP spec)

To keep MVP behavior auditable and unambiguous, the deterministic rule engine uses:

- **Vendor key**: `vendor_key = normalize(vendor_raw)` where `normalize()` lowercases, strips punctuation, and collapses whitespace.
- **Match type**: **exact match only** on `(tenant_id, vendor_key)`.
- **Precedence**: deterministic rules run before any LLM call. A rule match yields `AUTO_TAG` with `source="rule"` and `confidence=1.0`.
- **Multiple matches**: not possible under exact-match keying. Regex/substring rules are explicitly out of scope for the MVP.

### 3.2 Per-Tenant CoA

Generic models fail because "Subscriptions" in Tenant A might map to `SaaS Tools (6100)`, while in Tenant B it maps to `COGS — Software (5050)`.

Every request is fully tenant-scoped:

- `tenant_id` is required on every transaction event.
- The LLM prompt is injected with that tenant's full CoA list.
- The LLM is explicitly instructed to choose **only** from the provided account IDs.
- The application validates the returned `coa_account_id` against the tenant CoA loaded at process startup (materialized as an in-memory `set` of valid IDs for fast membership checks) — this is the hard gate. A plausible-sounding account ID that doesn't exist in the tenant's CoA is treated as an invalid response.

**Example CoA injection in prompt** (see §4 for full prompt):

```
TENANT CHART OF ACCOUNTS:
- 6100 | SaaS Tools | Software subscriptions and SaaS platform fees
- 6200 | Cloud & Hosting | Cloud compute, storage, CDN, and hosting costs
- 7100 | Travel & Accommodation | Flights, hotels, ground transport for business travel
...
You MUST return a coa_account_id from the list above. Any other value is invalid.
```

### 3.3 Cold Start (No Historical Labels)

When a new tenant onboards with no historical rules:

1. Mark the tenant as `cold_start: true` in `data/tenants.json` (see `tenant_b` in the seed config).
2. The service applies an **effective** auto-post threshold of **0.95** for routing (even if `auto_post_threshold` remains at its nominal value for readability in config).
3. All classifier suggestions below the effective auto-post threshold go to `REVIEW_QUEUE` (or `UNKNOWN` if below the review threshold), avoiding early silent auto-posts.
4. Every reviewer decision is recorded and can be promoted to a rule immediately.
5. As the rule store grows, the auto-tag rate climbs naturally with zero risk of early silent miscoding.

This creates a deliberate trust-building ramp for each tenant.

### 3.4 Learning Loop

When a reviewer accepts or corrects a suggestion, the system records the outcome and optionally promotes it to a deterministic rule:

```
reviewer_action: { tx_id, final_coa_account_id, vendor_key, prior_suggestion, prior_confidence }
    │
    ├── always: write to audit log
    ├── always: update review queue status
    └── if promoted: write rule to rule store
              (tenant_id, vendor_key) → coa_account_id
```

**Why deterministic rules over RAG or fine-tuning for this MVP:**

- Rules are immediately auditable — a human can read and verify every rule.
- They are cheap (zero LLM cost for repeat vendors).
- They are consistent — same vendor always gets the same account.
- RAG is the logical next step once the correctness gate is validated and we have enough data to build a meaningful retrieval corpus.
- Fine-tuning is a later-stage optimization; it requires labeled data volume we don't have at MVP.

This creates a measurable improvement loop: as the rule store grows, Auto-Tagging Rate trends up and review queue volume trends down, both trackable per tenant over time.

**Known gap — rule trust asymmetry**: a single reviewer correction immediately becomes a deterministic rule. If the reviewer is wrong, all future transactions for that vendor are silently miscoded — the exact failure mode this system is designed to prevent. Mitigation: every rule carries `created_by`, `created_at`, and `source_tx_id` for full lineage; rule changes are append-only in the audit log; an admin role can review and revert any rule. A future enhancement is a rule confidence threshold requiring N concordant corrections before a rule auto-applies.

> **Implementation note (audit vs rule store)**: rule mutations are recorded as append-only events in the audit log for traceability, while the JSON-backed rule store is treated as the current materialized “latest state” used for deterministic matching.

### 3.5 "I Don't Know" — Zero Silent Errors

Any of the following routes the transaction to `UNKNOWN` or `REVIEW_QUEUE` — never to `AUTO_TAG`:

- Confidence below `REVIEW_THRESHOLD`
- LLM returns invalid JSON
- LLM returns a `coa_account_id` not in the tenant's CoA
- All providers in the fallback chain exhausted (timeout or error on every provider)
- Missing required transaction fields (`vendor`, `amount`, `tenant_id`)
- `confidence` value outside `[0.0, 1.0]`

Every outcome is written to the audit log before any further action. The accounting sync adapter is never called unless the routing decision is `AUTO_TAG`.

### 3.6 Long-Tail Vendor Evaluation

Standard vendors (AWS, Uber, Zoom) are easy. The long tail — unusual local vendors, foreign-language merchant names, one-time contractors — is where LLMs are most likely to hallucinate a plausible-but-wrong account.

**Eval strategy:**

1. Maintain a curated dataset of edge-case transactions in `tests/eval/fixtures/edge_cases.json`. Each fixture includes the vendor name, transaction context, correct CoA account, and a difficulty label (`easy` / `long-tail` / `ambiguous`).
2. Run `eval_runner.py` against this dataset on every prompt change.
3. Key metrics tracked:
   - **Precision on auto-tag**: of transactions routed to `AUTO_TAG`, what % had the correct CoA? Target: ≥ 98%.
   - **Long-tail UNKNOWN rate**: of `long-tail`-labeled fixtures, what % correctly routed to `REVIEW_QUEUE` or `UNKNOWN`? Target: ≥ 90%.
   - **Confidence calibration**: is a confidence of 0.85 actually right ~85% of the time? (Brier score or reliability diagram.)
4. A prompt change that drops long-tail UNKNOWN rate below 90% is a regression — block it regardless of overall precision improvement.

---

## 4. LLM Prompt Design

### 4.1 System Prompt

> **Decoding parameters**: `temperature=0` is used for all classification calls. This maximises reproducibility — the same vendor + CoA list should always produce the same account selection. Non-zero temperature is appropriate for generative tasks but wrong for deterministic classification.
>
> **Tenant name**: `tenant_name` is loaded from `TenantConfig` (e.g., `data/tenants.json`) keyed by `tenant_id` during prompt construction. It is included for human debuggability; the LLM does not require `tenant_id` to classify correctly.

```
You are a financial transaction classifier for a multi-tenant expense management platform.

Your job is to assign each transaction to the correct account in the tenant's Chart of Accounts (CoA).

Rules you must follow:
1. You MUST return a JSON object with exactly three fields: coa_account_id, confidence, reasoning.
2. coa_account_id MUST be one of the account IDs listed in the TENANT CHART OF ACCOUNTS below. Any other value is invalid.
3. confidence MUST be a float between 0.0 and 1.0 representing how certain you are.
4. If you are not confident (e.g., the vendor is ambiguous or could fit multiple accounts), return a lower confidence value. Do NOT guess at high confidence.
5. reasoning MUST be a single sentence explaining your choice.
6. Return ONLY the JSON object. No preamble, no markdown, no explanation outside the JSON.

TENANT CHART OF ACCOUNTS:
{coa_list}

TENANT NAME: {tenant_name}
```

### 4.2 User Prompt (per transaction)

```
Classify the following transaction.

TRANSACTION:
- Vendor: {vendor_normalized}
- Amount: {amount} {currency}
- Date: {date}
- Card/Bill: {transaction_type}
- OCR Receipt Snippet: {ocr_text if available, else "Not available"}

HISTORICAL EXAMPLES FOR THIS TENANT:
{few_shot_examples}

Respond with ONLY a JSON object: {"coa_account_id": "...", "confidence": 0.0, "reasoning": "..."}
```

### 4.3 Few-Shot Example Format

```json
[
  {
    "vendor": "aws-marketplace",
    "amount": 1240.00,
    "currency": "USD",
    "coa_account_id": "6200",
    "coa_name": "Cloud & Hosting",
    "reasoning": "AWS Marketplace is cloud infrastructure spend."
  },
  {
    "vendor": "grab-sg-0023",
    "amount": 18.50,
    "currency": "SGD",
    "coa_account_id": "7200",
    "coa_name": "Local Transport",
    "reasoning": "Grab is a ride-hailing service; categorized as local transport."
  }
]
```

Up to 5 examples are injected, selected randomly from the tenant's historical confirmed tags for the MVP. In production, this would be replaced by retrieval-based selection (most semantically similar vendor category), which improves long-tail performance — see §9.

**MVP few-shot selection spec**:

- **Source pool**: tenant’s historical, human-confirmed tags (e.g., prior `TaggingResult` with a final `coa_account_id` from the review/override path).
- **Sampling**: uniform random sample of up to 5 examples.
- **Exclusions**:
  - exclude examples whose normalized vendor key equals the current transaction’s vendor key
  - exclude malformed/incomplete examples
- **Reproducibility (recommended)**: seed sampling with a stable seed (e.g., `hash(tx_id)`), so demo output is deterministic across runs.

### 4.4 Expected LLM Response

```json
{
  "coa_account_id": "6200",
  "confidence": 0.93,
  "reasoning": "Vendor 'aws-marketplace' is a cloud infrastructure provider; matches account 6200 (Cloud & Hosting)."
}
```

---

## 5. Data Schemas & Contracts

### 5.1 Transaction Input

```python
class Transaction(BaseModel):
    tx_id: str
    tenant_id: str
    vendor_raw: str                      # raw merchant string from card network
    amount: Decimal                      # minor-unit handling for non-standard currencies (JPY=0 decimals, BHD=3) is delegated to the accounting sync adapter, which knows the target ledger's expectations
    currency: str                        # ISO 4217
    date: date
    transaction_type: Literal["card", "bill"]
    ocr_text: Optional[str] = None       # OCR'd receipt snippet
    idempotency_key: str                 # for safe retries
```

### 5.2 CoA Account

```python
class CoAAccount(BaseModel):
    account_id: str
    name: str
    description: str
    parent_id: Optional[str] = None      # for hierarchical CoA structures
```

### 5.3 LLM Output (validated before routing)

```python
class LLMClassificationOutput(BaseModel):
    coa_account_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
```

### 5.4 Tagging Result (audit record)

```python
class TaggingResult(BaseModel):
    tx_id: str
    tenant_id: str
    status: Literal["AUTO_TAG", "REVIEW_QUEUE", "UNKNOWN"]
    source: Literal["rule", "llm", "unknown"]
    coa_account_id: Optional[str]
    confidence: Optional[float]          # None when status=UNKNOWN and no LLM was called (e.g., empty vendor); 0.0 when LLM was called but all providers failed
    reasoning: Optional[str]
    timestamp: datetime
    idempotency_key: str
```

### 5.5 Vendor Rule

```python
class VendorRule(BaseModel):
    tenant_id: str
    vendor_key: str                      # normalized vendor string
    coa_account_id: str
    created_by: Literal["reviewer", "import"]
    created_at: datetime
    source_tx_id: Optional[str]          # the transaction that generated this rule
```

---

## 6. Failure Modes & Edge Cases

| Failure condition | Detection point | Handling |
| :--- | :--- | :--- |
| LLM 4xx (content filter, prompt too long) | `llm_classifier.py` try/catch | Log raw error; route to `UNKNOWN` immediately; **do not fall back** — the same prompt will fail on the next provider for the same reason, and would leak the request to a second vendor unnecessarily |
| LLM 429 (rate limited) | `llm_classifier.py` try/catch | Exponential backoff up to 2 retries on the **same provider**; only fall back to next provider if retries are exhausted; log each retry with wait duration |
| LLM 5xx / connection error | `llm_classifier.py` try/catch | Try next provider in fallback chain immediately (no retry on same provider); route to `UNKNOWN` only if all providers return errors; log each failure with status code |
| LLM API timeout | `llm_classifier.py` try/catch | Try next provider in fallback chain; route to `UNKNOWN` only if all providers time out; log each attempt and latency |
| End-to-end latency budget exceeded | `llm_classifier.py` deadline guard | A 15-second wall-clock budget covers the entire fallback chain; if the deadline is reached before a valid response, route to `UNKNOWN` regardless of remaining providers — prevents multi-timeout cascades (~90s worst case) from blocking the pipeline |
| LLM returns invalid JSON | `validator.py` JSON parse | Route to `UNKNOWN`; log raw response |
| LLM returns account not in tenant CoA | `validator.py` CoA membership check | Route to `UNKNOWN`; log the hallucinated ID |
| `confidence` outside `[0.0, 1.0]` | Pydantic field validator | Route to `UNKNOWN` |
| Confidence exactly at threshold boundary | `router.py` | `>= AUTO_POST_THRESHOLD` auto-tags; boundary is inclusive on the upper route |
| Empty or missing vendor string | `preprocessor.py` | Route to `UNKNOWN`; cannot normalize |
| Tenant CoA list exceeds token budget | `llm_classifier.py` | Truncate to top 50 accounts by usage frequency; log warning. Note: if the correct account falls outside the top 50, the LLM cannot select it — mitigated by instructing the LLM to return low confidence when no listed account fits, which routes the transaction to `REVIEW_QUEUE` rather than miscoding it. |
| Duplicate transaction (same `idempotency_key`) | `main.py` before pipeline | Return cached result; skip reprocessing |
| Reviewer corrects to an account not in CoA | `review_queue.py` endpoint | Reject with 422; return valid account list |
| Tenant renames or deletes a CoA account that an existing rule references | `rule_store.py` on CoA update event | Re-validate all rules against the updated CoA; flag stale rules for reviewer confirmation before they can auto-tag again |

---

## 7. Evaluation Strategy

### 7.1 Offline Eval Dataset

Located at `tests/eval/fixtures/edge_cases.json`. Structure:

```json
[
  {
    "tx_id": "eval_001",
    "tenant_id": "tenant_a",
    "vendor_raw": "PTTEP THAILAND FUEL 0049",
    "amount": 340.00,
    "currency": "THB",
    "expected_status": "REVIEW_QUEUE",
    "expected_coa_account_id": null,
    "difficulty": "long-tail",
    "note": "Obscure Thai fuel vendor; should not auto-tag"
  }
]
```

### 7.2 Metrics

| Metric | Definition | Target |
| :--- | :--- | :--- |
| **Auto-tag precision** | Correct CoA / total auto-tagged (measured against `edge_cases.json` fixture set, not live traffic — production ground truth requires human-reviewed labels) | ≥ 98% on fixture set |
| **Long-tail UNKNOWN rate** | `long-tail` fixtures routed to REVIEW or UNKNOWN / total `long-tail` | ≥ 90% |
| **Review rate** | Transactions routed to REVIEW / total | Informational (tracks threshold calibration) |
| **Confidence calibration** | Is conf=0.85 right ~85% of the time? | Brier score ≤ 0.05 |
| **Rule coverage** | Transactions handled by deterministic rule / total | Trending up over time |

**Known gap — LLM confidence calibration**: LLM self-reported confidence is not natively well-calibrated; a model returning 0.85 is not reliably correct 85% of the time. Brier score ≤ 0.05 is the target eval metric. If calibration drifts in production, a post-hoc calibration layer (Platt scaling or isotonic regression, fit on the eval fixture set) can be inserted between Step 5 (Output Validation) and Step 6 (Confidence Router) without changing the surrounding architecture.

### 7.3 Regression Gate

A prompt change, model upgrade, or threshold adjustment must pass the eval harness before deployment:

- Auto-tag precision must not drop below 98%.
- Long-tail UNKNOWN rate must not drop below 90%.
- Any regression on either metric blocks the change, even if overall accuracy improves.

### 7.4 Running Evals

```bash
python tests/eval/eval_runner.py --tenant tenant_a --fixture tests/eval/fixtures/edge_cases.json
python tests/eval/eval_runner.py --tenant tenant_b --fixture tests/eval/fixtures/edge_cases.json
```

Example output (as of the current `edge_cases.json` harness — 20 fixtures per tenant):

```text
Eval results - tenant_a
  Total fixtures:          20
  Auto-tag precision:      100.0%
  Long-tail UNKNOWN rate:  100.0%
  Review rate:             30.0%
  Brier score:             0.030
  Rule coverage:           10.0%
```

```text
Eval results - tenant_b
  Total fixtures:          20
  Auto-tag precision:      n/a (no AUTO_TAG fixtures in this run)
  Long-tail UNKNOWN rate:  100.0%
  Review rate:             65.0%
  Brier score:             0.040
  Rule coverage:           0.0%
```

Note: `tenant_b` is configured with `cold_start: true`, which raises the effective auto-post threshold; the fixture set is intentionally biased toward **REVIEW_QUEUE** / **UNKNOWN** rather than **AUTO_TAG**.

---

## 8. Architectural & Technology Choices

| Layer | Choice | Rationale |
| :--- | :--- | :--- |
| Framework | FastAPI | Async-native, schema-first with Pydantic, minimal boilerplate, easy to test |
| Validation | Pydantic v2 | Strict schema enforcement at I/O boundaries; catches LLM output errors before routing |
| Primary LLM | Google Gemini (`GOOGLE_API_KEY`) | Single key covers both transaction classification and Google Cloud OCR services (Document AI / Vision); reduces credential sprawl |
| Fallback LLM | Anthropic Claude (`CLAUDE_API_KEY`) | Invoked automatically if Gemini errors or times out; strong JSON compliance and instruction-following |
| Second Fallback LLM | OpenAI GPT-4o (`OPENAI_API_KEY`) | Final safety net if both Gemini and Claude are unavailable; ensures the pipeline never hard-fails on a single provider outage |
| LLM Abstraction | LiteLLM | Standardizes API calls across all three providers using the OpenAI request format; the fallback chain is ~5 lines of code rather than three separate SDKs with divergent timeout and error handling |
| Storage (MVP) | SQLite (`data/runtime/state.db`) + JSON seed files | Durable-enough local persistence for audit/idempotency/review/few-shot examples without standing up Postgres |
| Storage (production) | Postgres + Redis + append-only audit table | See §9 |
| Vendor normalization | Regex + `str.lower()` + whitespace collapse | Deterministic, fast, no external dependency |

**On JSON mode vs. function calling**: JSON mode was chosen over function calling because the output schema is simple (3 fields) and JSON mode produces fewer refusals on edge-case inputs. If the schema grows to include tax codes and tracking categories, function calling is the better fit.

**On model choice and fallback chain**: Gemini is the preferred primary LLM — a single `GOOGLE_API_KEY` also covers Google Cloud services (Document AI for invoice OCR, Vision API for receipt parsing), reducing credential sprawl. Claude is the first fallback, OpenAI GPT-4o the second.

**The chain is dynamic**: `llm_classifier.py` checks the environment at startup for available keys and builds the provider list in order. If an `APIConnectionError` or `Timeout` occurs, it falls back to the next available provider seamlessly — zero downtime for transaction classification, and no hard failure if a provider is temporarily unavailable.

**LiteLLM is used for abstraction**: rather than writing three separate SDK integrations with divergent error types and JSON mode implementations, LiteLLM standardizes all calls to the OpenAI request format. The fallback chain is ~5 lines of code. MVP scope note: if running with a single key, the chain degrades gracefully to that provider only — the demo works with just `OPENAI_API_KEY`.

---

## 9. Production-Readiness Considerations

### Must-haves before production

- **Immutable audit log**: every decision written to an append-only store with `tx_id`, `timestamp`, `source`, `confidence`, `coa_account_id`, and `reasoning`. No record is ever updated — corrections create new records.
- **Idempotency**: transactions are deduplicated by `idempotency_key` before entering the pipeline. Safe to retry on network failure.
- **PII stripping**: strip cardholder name, last 4 digits, and any personal identifiers from the `ocr_text` field before the LLM call.
- **Observability per tenant**:
  - Auto-tag rate (trending)
  - Review rate (trending)
  - UNKNOWN rate + top unknown vendors (actionable: which vendors need rules?)
  - LLM latency p50/p95
  - Token usage per request (cost control)
- **Eval harness**: run on every prompt or model change (see §7).
- **Tenant isolation**: every data access (CoA, rules, audit log, review queue) is scoped by `tenant_id` with server-side enforcement. A tenant must never be able to read or influence another tenant's data, rules, or audit trail — enforced at the repository layer, not just the API layer.
- **Rate limiting**: protect the LLM call path from burst traffic; queue overflow to REVIEW rather than dropping.
- **Per-tenant LLM spend cap**: a configurable monthly token budget per tenant; transactions arriving after the cap is reached are routed to `REVIEW_QUEUE` rather than triggering LLM calls, preventing runaway costs for high-volume or misconfigured tenants.

### Next-step architecture (post-MVP)

- **Postgres**: tenants, CoA, rules, transactions, audit log.
- **Redis + queue (e.g., SQS)**: async processing, retries, review workflow.
- **Vector store (e.g., pgvector)**: long-tail vendor retrieval — find the 5 most similar historical transactions to the current one and inject as few-shot examples. This replaces the current random-sample few-shot approach.
- **RAG over confirmed tags**: as the tagged transaction corpus grows, retrieval-augmented few-shot selection will improve long-tail performance measurably. This is the natural next step after deterministic rules.

---

## 10. How to Run the MVP

### Prerequisites

- Python 3.10+
- **No LLM keys are required** to run tests, the demo script, or the offline eval harness: when `LLM_ENABLE_LIVE_CALLS=false` (default), the service uses a deterministic classifier path for stable CI and local development.
- **Optional live provider calls**: set `LLM_ENABLE_LIVE_CALLS=true` and provide one or more provider keys. The system dynamically builds the fallback chain based on whichever keys are present:
  - `GOOGLE_API_KEY` — primary LLM (Gemini) + Google Cloud services (Document AI / Vision OCR)
  - `CLAUDE_API_KEY` — fallback LLM (Claude)
  - `OPENAI_API_KEY` — fallback LLM (GPT-4o)
- Recommended priority order if you have multiple keys: `GOOGLE_API_KEY` → `CLAUDE_API_KEY` → `OPENAI_API_KEY`

### Setup

```bash
cd auto-tagging-agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # optional: add provider keys + LLM_ENABLE_LIVE_CALLS=true for live calls
```

`.env.example`:

```env
# Live provider calls are opt-in for deterministic local dev/tests.
# Set to true when you want real provider chaining.
LLM_ENABLE_LIVE_CALLS=false

# Optional provider keys (used only when LLM_ENABLE_LIVE_CALLS=true).
# The fallback chain is built dynamically from whichever keys are present.
# Recommended order: Gemini → Claude → OpenAI.

# Primary LLM + Google Cloud services (Document AI, Vision OCR)
# GOOGLE_API_KEY=your_google_api_key_here

# Fallback LLM — invoked if Gemini fails or times out
# CLAUDE_API_KEY=your_claude_api_key_here

# Second fallback LLM — invoked if both Gemini and Claude are unavailable
# OPENAI_API_KEY=your_openai_api_key_here
```

### Run the API server

```bash
uvicorn app.main:app --reload
```

API docs available at `http://localhost:8000/docs`.

### Run tests

```bash
pytest
```

If your environment does not pick up `pytest.ini` for some reason, this also works:

```bash
python -m pytest
```

### Optional HTTP smoke test (requires a running server)

```bash
uvicorn app.main:app --reload
bash scripts/smoke_test.sh
```

### Demo scenario (recommended starting point)

Runs 4 transactions through the full pipeline and prints audit output to terminal:

```bash
python scripts/demo_scenario.py
```

**What the demo shows:**

```text
[Audit] tx=101 vendor=zoom-us        source=rule  conf=1.00 -> AUTO_TAG    account=6100 (SaaS Tools)
[Audit] tx=102 vendor=grab-sg-0023   source=llm   conf=0.65 -> REVIEW_QUEUE
         reasoning: "Grab is a ride-hailing service; could be Local Transport or Client Entertainment."

--- Reviewer corrects tx=102: account=7200 (Local Transport) ---
[Audit] tx=102 reviewer_override     final=7200   rule_created vendor_key=grab-sg-0023

[Audit] tx=103 vendor=grab-sg-0091   source=rule  conf=1.00 -> AUTO_TAG    account=7200 (Local Transport)
         (LLM bypassed — deterministic rule from reviewer correction)

[Audit] tx=104 vendor=PTTEP THAI 049 source=llm   conf=0.31 -> UNKNOWN
         reasoning: "Vendor is ambiguous; insufficient signal to classify confidently."
```

This output demonstrates the full safety story: rule-based auto-tag, LLM with medium confidence routed to review, learning loop creating a new rule, subsequent identical vendor bypassing LLM, and low-confidence correctly routing to UNKNOWN.

### Key API endpoints

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/transactions/tag` | Submit a transaction for tagging |
| `GET` | `/review-queue/{tenant_id}` | List pending review items |
| `POST` | `/review-queue/{tx_id}/resolve` | Accept or correct a suggestion |
| `GET` | `/audit-log/{tenant_id}` | View full audit trail |
| `GET` | `/rules/{tenant_id}` | View current vendor rule store |

---

## 11. Explicit Assumptions

- **CoA tagging only (MVP scope)**: The prompt specifies tax codes, tracking categories, and required metadata. The 4–6 hour time budget makes full implementation impractical without sacrificing correctness. Tax codes and tracking categories are architecturally anticipated but deferred.
- **Transaction event stream**: Simulated by the `POST /transactions/tag` HTTP endpoint and the demo script. In production this would be a Kafka or SQS consumer.
- **Accounting platform sync**: Represented by a mock adapter that logs the payload. Real integration (Xero, QuickBooks, NetSuite) is an adapter swap.
- **Vendor rules are keyed by normalized vendor string**: Lowercased, whitespace collapsed, punctuation stripped. E.g., `"AWS Marketplace, Inc."` → `"aws marketplace inc"`. Fuzzy matching is a next-step improvement.
- **Few-shot examples are sampled from confirmed tenant history**: For the MVP, up to 5 examples are selected randomly. In production, retrieval-based selection (most similar vendor category) is better.
- **Storage is local JSON seed files + SQLite runtime state**: sufficient for a take-home MVP demo; production storage choices are documented in §9.
- **Single primary LLM call per transaction**: fallback providers are only invoked if the primary errors or times out. For the MVP's narrow scope (CoA tagging only), a single well-structured prompt is sufficient; in the worst case (all prior providers fail) up to 3 calls are made, each with the same prompt and schema.
- **Rule writes are idempotent on `(tenant_id, vendor_key)`**: if two transactions for the same new vendor arrive simultaneously and both trigger reviewer corrections concurrently, the second write is a no-op (last-write-wins on an identical key). This prevents duplicate rule entries and makes the rule store safe under concurrent correction.
- **No PII in demo data**: Demo fixtures use synthetic data. PII scrubbing is listed as a production must-have in §9.

