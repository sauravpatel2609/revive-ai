import time
import uuid
import random
from typing import Optional

from app.models import RecoveryStatus

class VerificationAgent:
    """
    Listens to payment webhooks to confirm if a recovery action was successful.
    In demo mode, it probabilistically simulates the webhook response.
    """

    def __init__(self):
        self.verified_count = 0
        self.recovered_count = 0
        self.failed_count = 0
        self.total_recovered_amount = 0  # in paise

    def verify(self, execution_result: dict, txn: dict,
               diagnosis: dict, plan: dict,
               simulated: bool = True) -> dict:
        """
        Verify whether a recovery action succeeded.

        In production: listens to Razorpay webhooks (payment.captured, payment_link.paid).
        For demo: simulates outcome based on recovery probability.

        Returns verification result with recovery status and learning reward.
        """
        start_time = time.time()
        intervention_type = plan.get("intervention_type", "do_nothing")
        status = execution_result.get("status", "")

        # ── Skip non-actionable interventions ───────────────────────────
        if intervention_type in ("do_nothing", "human_escalation"):
            return self._make_result(
                execution_result, txn, RecoveryStatus.SKIPPED.value,
                0.0, 0,
                f"No verification needed for '{intervention_type}'.",
                start_time
            )

        if status in (RecoveryStatus.SKIPPED.value, RecoveryStatus.FAILED.value,
                      RecoveryStatus.AWAITING_APPROVAL.value):
            return self._make_result(
                execution_result, txn, status,
                0.0, 0,
                f"Execution status was '{status}' — skipping verification.",
                start_time
            )

        # ── Simulate or check real webhook ──────────────────────────────
        if simulated:
            recovered, amount = self._simulate_outcome(
                plan, txn, diagnosis
            )
        else:
            # In production: check webhook store for payment confirmation
            recovered, amount = self._check_webhook(execution_result)

        # ── Update counters ─────────────────────────────────────────────
        self.verified_count += 1
        if recovered:
            self.recovered_count += 1
            self.total_recovered_amount += amount
            recovery_status = RecoveryStatus.RECOVERED.value
            reward = 1.0
            reasoning = (
                f"✅ Recovery SUCCESSFUL! Amount: ₹{amount/100:,.0f}. "
                f"Intervention: {intervention_type}. "
                f"Payment confirmed via {'simulated webhook' if simulated else 'Razorpay webhook'}."
            )
        else:
            self.failed_count += 1
            recovery_status = RecoveryStatus.FAILED.value
            reward = 0.0
            reasoning = (
                f"❌ Recovery FAILED. Intervention: {intervention_type}. "
                f"Customer did not complete payment within verification window."
            )

        return self._make_result(
            execution_result, txn, recovery_status,
            reward, amount if recovered else 0,
            reasoning, start_time
        )

    def _simulate_outcome(self, plan, txn, diagnosis) -> tuple:
        """
        Simulate recovery outcome based on plan's recovery probability.
        Returns (recovered: bool, amount: int).
        """
        recovery_prob = plan.get("recovery_probability", 0.2)
        failure_type = diagnosis.get("failure_type", "unknown")
        intervention_type = plan.get("intervention_type", "")

        # Adjust probability based on context
        adjusted_prob = recovery_prob

        # Bank timeouts with smart retry have higher success
        if failure_type == "bank_timeout" and intervention_type == "smart_retry":
            adjusted_prob = min(0.9, adjusted_prob * 1.5)

        # Card expired with payment link has moderate success
        if failure_type == "card_expired" and "payment_link" in intervention_type:
            adjusted_prob = min(0.7, adjusted_prob * 1.2)

        # Subscription date shifts are quite effective
        if intervention_type == "subscription_date_shift":
            adjusted_prob = min(0.8, adjusted_prob * 1.4)

        # Insufficient funds — lower probability
        if failure_type == "insufficient_funds":
            adjusted_prob *= 0.7

        recovered = random.random() < adjusted_prob
        amount = txn.get("amount", 0) if recovered else 0

        return recovered, amount

    def _check_webhook(self, execution_result) -> tuple:
        """
        Check if a real Razorpay webhook confirmed recovery.
        Placeholder for production implementation.
        """
        # In production:
        # 1. Query webhook store for payment_link.paid / payment.captured events
        # 2. Match by payment_link_id or order_id
        # 3. Return (True, amount) if found, (False, 0) if not
        return False, 0

    def get_stats(self) -> dict:
        """Get verification statistics."""
        return {
            "verified": self.verified_count,
            "recovered": self.recovered_count,
            "failed": self.failed_count,
            "total_recovered_amount_paise": self.total_recovered_amount,
            "total_recovered_amount_rupees": self.total_recovered_amount / 100,
            "recovery_rate": (
                round(self.recovered_count / max(1, self.verified_count), 4)
            ),
        }

    def _make_result(self, execution_result, txn, status,
                     reward, recovered_amount, reasoning, start_time):
        """Create a verification result dict."""
        return {
            "intervention_id": execution_result.get("intervention_id", ""),
            "transaction_id": txn.get("id", ""),
            "status": status,
            "reward": reward,
            "recovered": status == RecoveryStatus.RECOVERED.value,
            "recovered_amount": recovered_amount,
            "recovered_amount_rupees": recovered_amount / 100 if recovered_amount else 0,
            "recovered_payment_id": f"pay_{uuid.uuid4().hex[:14]}" if recovered_amount else None,
            "reasoning": reasoning,
            "duration_ms": int((time.time() - start_time) * 1000),
        }
