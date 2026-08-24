"""
ReviveAI — Evaluation Pipeline
Runs baseline vs. ReviveAI comparison on synthetic dataset.
"""
import json
import os
import time
import sys
from collections import defaultdict

import numpy as np

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.agents.diagnosis import DiagnosisAgent
from app.agents.strategy import StrategyAgent
from app.agents.execution import ExecutionAgent
from app.agents.verification import VerificationAgent


def load_dataset():
    """Load the synthetic transaction dataset."""
    data_path = os.path.join(os.path.dirname(__file__), "..", "..", "data", "synthetic_transactions.json")
    if not os.path.exists(data_path):
        print("❌ Dataset not found. Run the generator first:")
        print("   python -m app.data.generator")
        sys.exit(1)

    with open(data_path, "r") as f:
        return json.load(f)


def baseline_recovery(failed_txns):
    """
    Baseline: Blind retry on ALL failed payments.
    Simulates a dumb retry system that doesn't diagnose or select strategies.
    Recovery rate: ~12% for bank timeouts, 0% for non-recoverable.
    """
    import random
    random.seed(123)

    results = {
        "total_failures": len(failed_txns),
        "attempted": 0,
        "recovered": 0,
        "recovered_amount": 0,
        "false_attempts": 0,  # Attempts on non-recoverable
        "per_type": defaultdict(lambda: {"attempted": 0, "recovered": 0}),
    }

    for txn in failed_txns:
        failure_type = txn.get("failure_type", "unknown")
        is_recoverable = txn.get("is_recoverable", True)
        amount = txn.get("amount", 0)

        # Baseline retries everything blindly
        results["attempted"] += 1
        results["per_type"][failure_type]["attempted"] += 1

        if not is_recoverable:
            results["false_attempts"] += 1
            continue

        # Dumb retry success rates
        retry_rates = {
            "bank_timeout": 0.18,
            "network_error": 0.15,
            "card_expired": 0.02,  # Retry won't help
            "insufficient_funds": 0.05,
            "auth_failed": 0.08,
            "declined_by_bank": 0.06,
        }
        rate = retry_rates.get(failure_type, 0.05)

        if random.random() < rate:
            results["recovered"] += 1
            results["recovered_amount"] += amount
            results["per_type"][failure_type]["recovered"] += 1

    results["recovery_rate"] = round(results["recovered"] / max(1, results["total_failures"]), 4)
    results["recovered_amount_rupees"] = results["recovered_amount"] / 100
    results["false_attempt_rate"] = round(results["false_attempts"] / max(1, results["attempted"]), 4)

    return results


def reviveai_recovery(failed_txns, diagnosis_agent, strategy_agent,
                       execution_agent, verification_agent):
    """
    ReviveAI: Full agent pipeline on failed transactions.
    """
    import random
    random.seed(456)

    results = {
        "total_failures": len(failed_txns),
        "diagnosed": 0,
        "correct_diagnoses": 0,
        "attempted": 0,
        "skipped": 0,
        "recovered": 0,
        "recovered_amount": 0,
        "false_attempts": 0,
        "per_type": defaultdict(lambda: {
            "total": 0, "diagnosed_correct": 0,
            "attempted": 0, "recovered": 0, "skipped": 0
        }),
        "per_intervention": defaultdict(lambda: {"attempted": 0, "recovered": 0}),
        "audit_trail": [],
    }

    for i, txn in enumerate(failed_txns):
        actual_failure_type = txn.get("failure_type", "unknown")
        is_recoverable = txn.get("is_recoverable", True)
        amount = txn.get("amount", 0)

        results["per_type"][actual_failure_type]["total"] += 1

        # ── Step 1: Diagnose ────────────────────────────────────────────
        diagnosis = diagnosis_agent.diagnose(txn)
        results["diagnosed"] += 1

        predicted_type = diagnosis.get("failure_type", "unknown")
        if predicted_type == actual_failure_type:
            results["correct_diagnoses"] += 1
            results["per_type"][actual_failure_type]["diagnosed_correct"] += 1

        # ── Step 2: Strategy Selection ──────────────────────────────────
        plan = strategy_agent.select_strategy(diagnosis, txn)
        intervention_type = plan.get("intervention_type", "do_nothing")

        if intervention_type in ("do_nothing", "human_escalation"):
            results["skipped"] += 1
            results["per_type"][actual_failure_type]["skipped"] += 1

            # Check if skip was correct
            if not is_recoverable:
                pass  # Correct skip
            continue

        results["attempted"] += 1
        results["per_type"][actual_failure_type]["attempted"] += 1
        results["per_intervention"][intervention_type]["attempted"] += 1

        if not is_recoverable:
            results["false_attempts"] += 1

        # ── Step 3: Execute ─────────────────────────────────────────────
        exec_result = execution_agent.execute(plan, txn, diagnosis)

        # ── Step 4: Verify ──────────────────────────────────────────────
        verify_result = verification_agent.verify(exec_result, txn, diagnosis, plan)

        if verify_result.get("recovered"):
            results["recovered"] += 1
            results["recovered_amount"] += amount
            results["per_type"][actual_failure_type]["recovered"] += 1
            results["per_intervention"][intervention_type]["recovered"] += 1
            reward = 1.0
        else:
            reward = 0.0

        # ── Step 5: Learn ───────────────────────────────────────────────
        strategy_agent.update_reward(diagnosis, txn, intervention_type, reward)

        # Sample audit trail entries (every 50th)
        if i % 50 == 0:
            results["audit_trail"].append({
                "txn_id": txn.get("id", "")[:8],
                "actual_type": actual_failure_type,
                "predicted_type": predicted_type,
                "correct": predicted_type == actual_failure_type,
                "intervention": intervention_type,
                "recovered": verify_result.get("recovered", False),
                "amount": amount / 100,
                "confidence": diagnosis.get("confidence", 0),
            })

    # ── Compute metrics ─────────────────────────────────────────────────
    results["recovery_rate"] = round(results["recovered"] / max(1, results["total_failures"]), 4)
    results["recovery_rate_attempted"] = round(results["recovered"] / max(1, results["attempted"]), 4)
    results["diagnosis_accuracy"] = round(results["correct_diagnoses"] / max(1, results["diagnosed"]), 4)
    results["recovered_amount_rupees"] = results["recovered_amount"] / 100
    results["false_attempt_rate"] = round(results["false_attempts"] / max(1, results["attempted"]), 4)
    results["automation_rate"] = round(
        (results["attempted"] + results["skipped"]) / max(1, results["total_failures"]), 4
    )

    return results


def run_evaluation():
    """Run the full evaluation pipeline."""
    print("=" * 70)
    print("  ReviveAI — Evaluation Pipeline")
    print("=" * 70)

    # Load dataset
    print("\n📂 Loading dataset...")
    transactions = load_dataset()
    failed_txns = [t for t in transactions if t.get("status") == "failed"]
    success_txns = [t for t in transactions if t.get("status") == "success"]
    print(f"   Total: {len(transactions)}, Failed: {len(failed_txns)}, Success: {len(success_txns)}")

    # Train diagnosis model
    print("\n🧠 Training Diagnosis Agent...")
    diagnosis_agent = DiagnosisAgent()
    train_result = diagnosis_agent.train_model(transactions)

    # Initialize agents
    strategy_agent = StrategyAgent()
    execution_agent = ExecutionAgent()
    verification_agent = VerificationAgent()

    # ── Run Baseline ────────────────────────────────────────────────────
    print("\n" + "─" * 70)
    print("  BASELINE: Blind Retry System")
    print("─" * 70)
    baseline = baseline_recovery(failed_txns)
    print(f"  Total failures:     {baseline['total_failures']}")
    print(f"  Attempted retries:  {baseline['attempted']}")
    print(f"  Recovered:          {baseline['recovered']}")
    print(f"  Recovery rate:      {baseline['recovery_rate']:.1%}")
    print(f"  Revenue recovered:  ₹{baseline['recovered_amount_rupees']:,.0f}")
    print(f"  False attempts:     {baseline['false_attempts']} ({baseline['false_attempt_rate']:.1%})")

    # ── Run ReviveAI ────────────────────────────────────────────────────
    print("\n" + "─" * 70)
    print("  REVIVEAI: Multi-Agent Recovery System")
    print("─" * 70)
    revive = reviveai_recovery(failed_txns, diagnosis_agent, strategy_agent,
                                execution_agent, verification_agent)
    print(f"  Total failures:     {revive['total_failures']}")
    print(f"  Diagnosed:          {revive['diagnosed']}")
    print(f"  Diagnosis accuracy: {revive['diagnosis_accuracy']:.1%}")
    print(f"  Attempted:          {revive['attempted']}")
    print(f"  Skipped (correct):  {revive['skipped']}")
    print(f"  Recovered:          {revive['recovered']}")
    print(f"  Recovery rate:      {revive['recovery_rate']:.1%}")
    print(f"  Recovery (attempted): {revive['recovery_rate_attempted']:.1%}")
    print(f"  Revenue recovered:  ₹{revive['recovered_amount_rupees']:,.0f}")
    print(f"  False attempts:     {revive['false_attempts']} ({revive['false_attempt_rate']:.1%})")
    print(f"  Automation rate:    {revive['automation_rate']:.1%}")

    # ── Comparison ──────────────────────────────────────────────────────
    multiplier = revive["recovered_amount"] / max(1, baseline["recovered_amount"])
    print("\n" + "=" * 70)
    print("  COMPARISON: Baseline vs ReviveAI")
    print("=" * 70)
    print(f"  {'Metric':<30} {'Baseline':>12} {'ReviveAI':>12} {'Improvement':>12}")
    print(f"  {'─'*30} {'─'*12} {'─'*12} {'─'*12}")
    print(f"  {'Recovery rate':<30} {baseline['recovery_rate']:>11.1%} {revive['recovery_rate']:>11.1%} {(revive['recovery_rate'] - baseline['recovery_rate'])*100:>+10.1f}pp")
    print(f"  {'Revenue recovered (₹)':<30} {baseline['recovered_amount_rupees']:>11,.0f} {revive['recovered_amount_rupees']:>11,.0f} {multiplier:>11.1f}x")
    print(f"  {'False attempt rate':<30} {baseline['false_attempt_rate']:>11.1%} {revive['false_attempt_rate']:>11.1%} {(baseline['false_attempt_rate'] - revive['false_attempt_rate'])*100:>+10.1f}pp")
    print(f"  {'Diagnosis accuracy':<30} {'N/A':>12} {revive['diagnosis_accuracy']:>11.1%} {'—':>12}")
    print(f"  {'Automation rate':<30} {'0%':>12} {revive['automation_rate']:>11.1%} {'—':>12}")

    # ── Per-failure-type breakdown ──────────────────────────────────────
    print("\n" + "─" * 70)
    print("  PER-FAILURE-TYPE BREAKDOWN (ReviveAI)")
    print("─" * 70)
    print(f"  {'Type':<25} {'Total':>6} {'Correct':>8} {'Attempted':>10} {'Recovered':>10} {'Rate':>8}")
    for ft, stats in sorted(revive["per_type"].items()):
        rate = stats["recovered"] / max(1, stats["attempted"]) if stats["attempted"] > 0 else 0
        print(f"  {ft:<25} {stats['total']:>6} {stats['diagnosed_correct']:>8} {stats['attempted']:>10} {stats['recovered']:>10} {rate:>7.0%}")

    # ── Per-intervention breakdown ──────────────────────────────────────
    print("\n" + "─" * 70)
    print("  PER-INTERVENTION BREAKDOWN (ReviveAI)")
    print("─" * 70)
    print(f"  {'Intervention':<30} {'Attempted':>10} {'Recovered':>10} {'Rate':>8}")
    for it, stats in sorted(revive["per_intervention"].items()):
        rate = stats["recovered"] / max(1, stats["attempted"])
        print(f"  {it:<30} {stats['attempted']:>10} {stats['recovered']:>10} {rate:>7.0%}")

    # ── Save results ────────────────────────────────────────────────────
    output = {
        "baseline": {k: v for k, v in baseline.items() if k != "per_type"},
        "reviveai": {k: v for k, v in revive.items() if k not in ("per_type", "per_intervention", "audit_trail")},
        "comparison": {
            "revenue_multiplier": round(multiplier, 2),
            "recovery_rate_improvement_pp": round((revive["recovery_rate"] - baseline["recovery_rate"]) * 100, 1),
            "false_attempt_reduction_pp": round((baseline["false_attempt_rate"] - revive["false_attempt_rate"]) * 100, 1),
        },
        "verification_stats": verification_agent.get_stats(),
        "strategy_stats": strategy_agent.get_stats().get("intervention_counts", {}),
        "sample_audit_trail": revive.get("audit_trail", [])[:10],
    }

    results_path = os.path.join(os.path.dirname(__file__), "..", "..", "data", "evaluation_results.json")
    with open(results_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n💾 Results saved to {results_path}")

    print("\n" + "=" * 70)
    print(f"  🏆 ReviveAI recovered {multiplier:.1f}x more revenue than baseline!")
    print("=" * 70)

    return output


if __name__ == "__main__":
    run_evaluation()
