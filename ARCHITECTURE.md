# ReviveAI — Architecture Notes

## System Overview

ReviveAI is a multi-agent autonomous revenue recovery system built on Razorpay's payment infrastructure. When a payment fails, ReviveAI automatically:

1. **Diagnoses** the root cause using ML (XGBoost)
2. **Selects** the optimal recovery strategy using contextual bandits (Thompson Sampling)
3. **Executes** bounded recovery actions via Razorpay APIs
4. **Verifies** the outcome and feeds results back to improve future decisions

## Agent Design Principles


### Why 4 agents instead of 1 LLM?

Each agent has a fundamentally different computational requirement:

| Agent | Reasoning Type | Model | Why This Model |
|:---|:---|:---|:---|
| **Diagnosis** | Classification | XGBoost | Structured tabular data. GBTs outperform LLMs on tabular classification. <1ms inference, interpretable. |
| **Strategy** | Optimization | Thompson Sampling Bandit | Multi-armed bandit with mathematical convergence guarantees. Balances explore/exploit. |
| **Execution** | Deterministic | API orchestration (no AI) | Financial actions must be deterministic. Idempotency keys, circuit breakers. |
| **Verification** | Event-driven | State machine | Webhook monitoring with timeout escalation. No AI needed. |

### Agent 1: Diagnosis Agent

**Input:** Failed payment webhook (error code, method, amount, bank, time, device, customer history)

**Processing:**
- Extracts 15 features from transaction data
- XGBoost classifier trained on labeled synthetic data
- Falls back to rule-based classification if model unavailable
- Outputs: failure_type, confidence, recovery_eligible, reasoning

**Key Features Used:**
1. Payment method (UPI/Card/NB/Wallet)
2. Transaction amount
3. Device type
4. Hour of day
5. Error source (bank/gateway)
6. Bank identity
7. Card network
8. Is subscription
9. Is international
10. Customer transaction history
11-15. Error description keyword signals (timeout, expired, fraud, declined)

**Guardrails:**
- Confidence < 0.6 → human review
- Never classifies as "fraud" without multi-signal confirmation

### Agent 2: Strategy Agent

**Input:** Diagnosis result + transaction context

**Processing:**
- Filters eligible interventions based on failure type
- Thompson Sampling selects from eligible arms
- Context: failure_type × payment_method × device_type
- Outputs: intervention_type, channel, recovery_probability, message

**Intervention Library:**
| Intervention | When Used | Base Success Rate |
|:---|:---|:---|
| Smart Retry | Bank timeout, network error | 35% |
| Payment Link (SMS) | All recoverable types | 25% |
| Payment Link (WhatsApp) | All recoverable types | 30% |
| Payment Link (Email) | Card expired, insufficient funds | 15% |
| Invoice Reissue | Insufficient funds, declined | 20% |
| Subscription Date Shift | Bank timeout (subscriptions) | 40% |
| Alternate Method Suggest | Card expired, auth failed | 18% |
| Do Nothing | Fraud, cardholder declined | 0% |

**Guardrails:**
- Max 2 recovery attempts per failure
- Human approval for amounts > ₹10,000
- 30-minute cooldown between attempts
- Customer opt-out respected

### Agent 3: Execution Agent

**Input:** Intervention plan

**Processing:**
- Routes to correct API handler
- Creates Razorpay Payment Links / Orders / Invoices
- Sends personalized recovery messages
- All calls use idempotency keys

**Razorpay APIs Used:**
- `POST /v1/payment_links` — Create payment link
- `POST /v1/orders` — Create new order for retry
- `POST /v1/invoices` — Issue invoice with extended terms
- `PATCH /v1/subscriptions/{id}` — Shift billing date

**Guardrails:**
- Idempotency keys prevent duplicate execution
- Transaction amount limits enforced
- Circuit breaker on API failures
- Rollback capability

### Agent 4: Verification Agent

**Input:** Execution result + Razorpay webhooks

**Processing:**
- Monitors for `payment.captured` / `payment_link.paid` webhooks
- Confirms recovery success/failure
- Computes reward signal for bandit learning
- Updates running statistics

**Guardrails:**
- 24-hour verification timeout
- Unresolved ≠ failed (marked "unresolved")
- Immutable audit log

## Safety Architecture

```
┌─────────────────────────────────────────────────────┐
│                  SAFETY LAYERS                       │
├─────────────────────────────────────────────────────┤
│ Layer 1: Authorization Boundaries                    │
│   • Can only create payment links, retry, update sub│
│   • CANNOT refund, payout, or transfer              │
│                                                      │
│ Layer 2: Transaction Limits                          │
│   • Per-recovery: ₹50,000                           │
│   • Per-merchant daily: ₹5,00,000                   │
│   • High-value (>₹10K): requires human approval     │
│                                                      │
│ Layer 3: Confidence Thresholds                       │
│   • Diagnosis < 0.6 → human review                  │
│   • Recovery probability < 0.3 → skip               │
│   • Strategy confidence < 0.5 → human approval      │
│                                                      │
│ Layer 4: Circuit Breaker                             │
│   • Error rate > 10% in 15min → auto-halt           │
│   • Global emergency stop                            │
│   • Per-merchant halt                                │
│                                                      │
│ Layer 5: Audit Trail                                 │
│   • Every decision logged with reasoning             │
│   • Immutable append-only log                        │
│   • Full explainability chain                        │
│                                                      │
│ Layer 6: Hallucination Protection                    │
│   • LLM NEVER makes financial decisions              │
│   • All financial logic is deterministic             │
│   • API calls validated against allowlist            │
└─────────────────────────────────────────────────────┘
```

## Technology Stack

| Component | Technology | Rationale |
|:---|:---|:---|
| Backend | Python 3.11 + FastAPI | Best ecosystem for ML + async web |
| ML Classification | XGBoost | SOTA for tabular data, fast, interpretable |
| Strategy Optimization | Thompson Sampling | Mathematically principled explore/exploit |
| Real-time Communication | WebSockets | Live dashboard updates |
| Database | SQLite (dev) | Zero-config for hackathon demo |
| Frontend | React + Vite | Fast dev cycle, component-based |
| Styling | Vanilla CSS | Maximum control, no framework bloat |
| Payment APIs | Razorpay Test Mode | Full lifecycle simulation |

## Data Flow

```
1. Payment fails on Razorpay
2. Webhook POST to /api/webhook/payment-failed
3. Safety pre-check (limits, halts)
4. Diagnosis Agent classifies root cause
5. Strategy Agent selects intervention
6. [If high-value] → Human approval gate
7. Execution Agent calls Razorpay API
8. Verification Agent monitors outcome
9. Learning: reward fed back to bandit
10. Dashboard updated via WebSocket
```

## Evaluation Methodology

- **Dataset:** 10,000 synthetic transactions (7,000 success + 2,300+ failures across 8 types)
- **Baseline:** Blind retry system (retries everything, no diagnosis)
- **Comparison:** Side-by-side on same dataset, controlled random seeds
- **Metrics:** Recovery rate, revenue recovered (₹), false attempt rate, diagnosis accuracy (F1), automation rate
