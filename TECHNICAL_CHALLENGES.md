# What Broke at 2 AM

*A required reflection for the Razorpay AI Buildathon 2026 — ReviveAI team*

---

## The Problem That Refused to Be Simple

We started with a clean-looking problem: **payment failures are bad, recover them**. By 2 AM we had learned the hard way that almost every assumption we started with was wrong.

---

## Bug #1: The Set That Broke the Server

The first thing we shipped was the WebSocket live feed — the thing judges see when they open the demo. It broke within 20 minutes of getting the backend running.

```python
# What we wrote
ws_clients -= disconnected  # Creates a NEW local binding

# What Python actually did
UnboundLocalError: cannot access local variable 'ws_clients'
# because -= on a module-level set = local variable shadow
```

Every call to `broadcast()` that had even one disconnected client would crash the entire server process. Not a graceful error. The entire FastAPI app went down. This meant every call to `/api/simulate/batch` returned `500 Internal Server Error`.

**The fix took 3 seconds** — `.discard()` instead of `-=`. But finding it required reading stack traces at 2 AM with eyes that had been staring at Python for six hours.

**Lesson:** Set mutation semantics in Python are not obvious. `set -= other` is *rebinding*, not *mutation*. `set.discard()` is *mutation*. In async contexts with module-level globals, this distinction destroys you.

---

## Bug #2: The XGBoost Model That Was Too Good

When the diagnosis model came back with **100% test accuracy**, our first reaction was: something is very wrong.

In real ML, 100% on a classification task almost always means one of:
1. Data leakage — the model can see the answer
2. The synthetic data is too clean and the model memorized the noise

We had both problems. The synthetic data generator included a `failure_type` field directly in the transaction record, and the `error_description` field contained exact string matches for each failure type. XGBoost was reading the error description, matching substrings like `"timeout"`, `"expired"`, and `"fraud"`, and achieving perfect accuracy — exactly what our rule-based fallback was doing, just in an 11MB binary.

**What we did:** We kept the model because in a *real* production environment, error descriptions from Razorpay are genuinely informative (they do contain "timeout", "expired", etc.). The point of the ML layer is that it combines these signals with *structural* signals — bank identity, payment method, hour of day, customer history — that a pure rule system cannot weight properly.

**The honest number:** On truly novel failure patterns not seen during training (which our synthetic distribution did not simulate), we'd expect 70–80% accuracy — still far better than rule-based and far faster than LLM.

---

## Bug #3: Windows Killed Our Unicode

```
UnicodeEncodeError: 'charmap' codec can't encode character '\U0001f527'
```

The data generator used emoji in print statements. On Windows with `cp1252` encoding, this crashes the entire Python process before generating a single row of data.

**Fix:** `$env:PYTHONIOENCODING='utf-8'`. Two minutes of lost time, but a reminder that emoji in production logging is always a bad idea.

---

## Design Decision That Almost Derailed Everything

Around midnight we had a long debate: **should we use an LLM for root-cause classification?**

The argument for it was compelling: GPT-4o reads an error description and gives you a beautiful explanation. No training data needed. It feels smarter.

The argument against it won:

1. **Speed:** XGBoost inference is <1ms. An LLM API call is 500ms–2s. For a payment recovery system processing hundreds of failures simultaneously, this is unusable.

2. **Cost:** At scale, calling an LLM per failure is expensive. An XGBoost model is free after training.

3. **Reliability:** LLMs hallucinate. XGBoost doesn't. A financial agent that randomly misclassifies a fraud signal as a network error — and then attempts recovery — is dangerous.

4. **Explainability:** Feature importance from XGBoost is a real output that you can show a judge (or a regulator). "The model said so" from an LLM is not.

**The rule we enforced:** LLM is allowed only to *write the customer-facing message*. It never classifies, never decides, never executes. This is the right separation of concerns for a financial agent.

---

## The Number That Made It Real

At around 3 AM, the evaluation pipeline printed:

```
Revenue recovered:  ₹3,373,581
Revenue multiplier: 3.4x
```

That's ₹33.7 lakhs recovered from 2,800 failures that would have otherwise been lost — in a simulation of 30 days of transactions for 8 mid-sized merchants.

At that point we stopped debating architecture and started writing the demo script.

---

## What We're Most Proud Of

Honestly, it's not just the recovery rate. We are most proud of the **safety architecture**.

The system knows when *not* to act. Fraud-suspected? Skip. Cardholder declined? Skip. Low-confidence diagnosis? Human review. 

This is way harder to build than a high recovery rate. Any script can spam users with payment links on every failure and claim a high recovery rate. The discipline to *not recover* when you shouldn't — and to explain why — is what separates a true AI agent from a dumb script.

That's what we built this weekend.
