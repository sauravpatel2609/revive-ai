import time
import uuid
import random
import math
from collections import defaultdict

import numpy as np

from app.config import config
from app.models import InterventionType, RecoveryStatus

# ── Intervention Library ────────────────────────────────────────────────────

INTERVENTION_LIBRARY = {
    InterventionType.SMART_RETRY: {
        "description": "Retry payment via Razorpay with same/different method",
        "applicable_failures": ["bank_timeout", "network_error"],
        "channel": None,
        "base_success_rate": 0.35,
        "cost": 0,  # No additional cost
    },
    InterventionType.PAYMENT_LINK: {
        "description": "Send a new payment link via SMS",
        "applicable_failures": ["bank_timeout", "insufficient_funds", "card_expired",
                                 "network_error", "auth_failed", "declined_by_bank"],
        "channel": "sms",
        "base_success_rate": 0.25,
        "cost": 0.50,  # SMS cost
    },
    InterventionType.PAYMENT_LINK_WHATSAPP: {
        "description": "Send personalized payment link via WhatsApp",
        "applicable_failures": ["bank_timeout", "insufficient_funds", "card_expired",
                                 "network_error", "auth_failed", "declined_by_bank"],
        "channel": "whatsapp",
        "base_success_rate": 0.30,
        "cost": 0.80,
    },
    InterventionType.PAYMENT_LINK_EMAIL: {
        "description": "Send payment recovery email with link",
        "applicable_failures": ["card_expired", "insufficient_funds", "auth_failed",
                                 "declined_by_bank"],
        "channel": "email",
        "base_success_rate": 0.15,
        "cost": 0.10,
    },
    InterventionType.INVOICE_REISSUE: {
        "description": "Reissue invoice with extended due date and payment link",
        "applicable_failures": ["insufficient_funds", "declined_by_bank"],
        "channel": "email",
        "base_success_rate": 0.20,
        "cost": 0.10,
    },
    InterventionType.SUBSCRIPTION_DATE_SHIFT: {
        "description": "Shift subscription billing date to avoid known issues",
        "applicable_failures": ["bank_timeout", "insufficient_funds"],
        "channel": None,
        "base_success_rate": 0.40,
        "cost": 0,
        "requires_subscription": True,
    },
    InterventionType.ALTERNATE_METHOD_SUGGEST: {
        "description": "Suggest customer use a different payment method",
        "applicable_failures": ["card_expired", "auth_failed", "declined_by_bank"],
        "channel": "sms",
        "base_success_rate": 0.18,
        "cost": 0.50,
    },
    InterventionType.DO_NOTHING: {
        "description": "No action — failure is non-recoverable or customer opted out",
        "applicable_failures": ["fraud_suspected", "declined_by_cardholder"],
        "channel": None,
        "base_success_rate": 0.0,
        "cost": 0,
    },
    InterventionType.HUMAN_ESCALATION: {
        "description": "Escalate to human operator with full context",
        "applicable_failures": ["unknown"],
        "channel": None,
        "base_success_rate": 0.0,
        "cost": 0,
    },
}


# ── Contextual Bandit (Thompson Sampling) ───────────────────────────────────

class ThompsonBandit:
    """
    Thompson Sampling contextual bandit for intervention selection.

    Each context (failure_type × method × device) has per-arm beta distributions.
    We sample from the posteriors and pick the arm with the highest sample.
    """

    def __init__(self):
        # Beta distribution parameters: (alpha, beta) per arm per context
        # alpha = successes + 1, beta = failures + 1 (uninformative prior)
        self.arms = {}  # context_key → {arm_name: (alpha, beta)}

    def _context_key(self, failure_type: str, method: str, device: str) -> str:
        return f"{failure_type}|{method or 'na'}|{device or 'na'}"

    def _get_params(self, context_key: str, arm: str):
        if context_key not in self.arms:
            self.arms[context_key] = {}
        if arm not in self.arms[context_key]:
            self.arms[context_key][arm] = (1.0, 1.0)  # Uninformative prior
        return self.arms[context_key][arm]

    def select(self, failure_type: str, method: str, device: str,
               eligible_arms: list) -> tuple:
        """
        Select the best arm via Thompson Sampling.
        Returns (selected_arm, sampled_value, all_samples).
        """
        context_key = self._context_key(failure_type, method, device)
        samples = {}

        for arm in eligible_arms:
            alpha, beta = self._get_params(context_key, arm)
            sample = np.random.beta(alpha, beta)
            samples[arm] = round(float(sample), 4)

        if not samples:
            return InterventionType.DO_NOTHING.value, 0.0, {}

        best_arm = max(samples, key=samples.get)
        return best_arm, samples[best_arm], samples

    def update(self, failure_type: str, method: str, device: str,
               arm: str, reward: float):
        """
        Update the beta distribution for this arm in this context.
        reward: 1.0 for success, 0.0 for failure.
        """
        context_key = self._context_key(failure_type, method, device)
        alpha, beta = self._get_params(context_key, arm)

        if reward > 0.5:
            alpha += 1.0
        else:
            beta += 1.0

        self.arms[context_key][arm] = (alpha, beta)

    def get_stats(self) -> dict:
        """Get current bandit statistics."""
        stats = {}
        for ctx, arms in self.arms.items():
            stats[ctx] = {
                arm: {"alpha": a, "beta": b, "mean": round(a / (a + b), 4)}
                for arm, (a, b) in arms.items()
            }
        return stats


# ── Strategy Agent ──────────────────────────────────────────────────────────

class StrategyAgent:
    """
    Strategy Agent — selects optimal recovery intervention.

    Uses Thompson Sampling bandit for exploration/exploitation
    with safety constraints and business rules.
    """

    def __init__(self):
        self.bandit = ThompsonBandit()
        self.intervention_counts = defaultdict(int)

    def select_strategy(self, diagnosis: dict, txn: dict,
                        attempt_number: int = 1) -> dict:
        """
        Select the optimal recovery strategy.

        Args:
            diagnosis: Output from Diagnosis Agent
            txn: Original transaction data
            attempt_number: Which recovery attempt this is (1-based)

        Returns:
            Intervention plan dict
        """
        start_time = time.time()
        failure_type = diagnosis.get("failure_type", "unknown")
        recovery_eligible = diagnosis.get("recovery_eligible", False)
        confidence = diagnosis.get("confidence", 0)
        amount = txn.get("amount", 0)

        # ── Safety checks ───────────────────────────────────────────────
        # 1. Non-recoverable → DO NOTHING
        if not recovery_eligible:
            return self._make_plan(
                txn, diagnosis, InterventionType.DO_NOTHING, 1.0, 0.0,
                f"Non-recoverable failure type: {failure_type}. No action taken.",
                start_time
            )

        # 2. Max attempts exceeded → DO NOTHING
        if attempt_number > config.MAX_RECOVERY_ATTEMPTS:
            return self._make_plan(
                txn, diagnosis, InterventionType.DO_NOTHING, 1.0, 0.0,
                f"Max recovery attempts ({config.MAX_RECOVERY_ATTEMPTS}) reached. Stopping.",
                start_time
            )

        # 3. Low diagnosis confidence → HUMAN ESCALATION
        if confidence < config.DIAGNOSIS_CONFIDENCE_THRESHOLD:
            return self._make_plan(
                txn, diagnosis, InterventionType.HUMAN_ESCALATION, 0.5, 0.0,
                f"Diagnosis confidence ({confidence:.2f}) below threshold ({config.DIAGNOSIS_CONFIDENCE_THRESHOLD}). "
                f"Escalating to human review.",
                start_time
            )

        # ── Find eligible interventions ─────────────────────────────────
        eligible = []
        for itype, info in INTERVENTION_LIBRARY.items():
            if failure_type in info["applicable_failures"]:
                # Skip subscription-specific interventions for non-subscriptions
                if info.get("requires_subscription") and not txn.get("is_subscription"):
                    continue
                if itype in (InterventionType.DO_NOTHING, InterventionType.HUMAN_ESCALATION):
                    continue
                eligible.append(itype.value)

        if not eligible:
            return self._make_plan(
                txn, diagnosis, InterventionType.HUMAN_ESCALATION, 0.5, 0.0,
                f"No eligible intervention for failure type: {failure_type}.",
                start_time
            )

        # ── Bandit selection ────────────────────────────────────────────
        selected_arm, sampled_value, all_samples = self.bandit.select(
            failure_type=failure_type,
            method=txn.get("payment_method", ""),
            device=txn.get("device_type", ""),
            eligible_arms=eligible
        )

        intervention_type = InterventionType(selected_arm)
        info = INTERVENTION_LIBRARY[intervention_type]

        # ── Determine if approval needed ────────────────────────────────
        requires_approval = (amount / 100) > config.HIGH_VALUE_THRESHOLD

        # ── Compute recovery probability ────────────────────────────────
        base_rate = info["base_success_rate"]
        # Adjust based on bandit's learned distribution
        recovery_probability = min(0.95, base_rate * (1 + sampled_value))

        # ── Determine channel ───────────────────────────────────────────
        channel = info.get("channel")
        if channel is None and intervention_type == InterventionType.SMART_RETRY:
            channel = "api"

        reasoning = (
            f"Selected '{intervention_type.value}' via Thompson Sampling. "
            f"Sampled value: {sampled_value:.3f}. "
            f"Base success rate: {base_rate:.0%}. "
            f"Estimated recovery probability: {recovery_probability:.0%}. "
            f"All arm samples: {json.dumps(all_samples)}."
        )

        self.intervention_counts[intervention_type.value] += 1

        return self._make_plan(
            txn, diagnosis, intervention_type,
            round(sampled_value, 4), round(recovery_probability, 4),
            reasoning, start_time,
            channel=channel,
            requires_approval=requires_approval,
        )

    def update_reward(self, diagnosis: dict, txn: dict,
                      intervention_type: str, reward: float):
        """Update bandit with outcome (1.0 = success, 0.0 = failure)."""
        self.bandit.update(
            failure_type=diagnosis.get("failure_type", "unknown"),
            method=txn.get("payment_method", ""),
            device=txn.get("device_type", ""),
            arm=intervention_type,
            reward=reward
        )

    def get_stats(self) -> dict:
        """Get bandit statistics and intervention distribution."""
        return {
            "bandit_stats": self.bandit.get_stats(),
            "intervention_counts": dict(self.intervention_counts),
        }

    def _make_plan(self, txn, diagnosis, intervention_type, confidence,
                   recovery_probability, reasoning, start_time,
                   channel=None, requires_approval=False):
        """Create an intervention plan dict."""
        import json as _json

        return {
            "id": str(uuid.uuid4()),
            "transaction_id": txn.get("id", ""),
            "diagnosis_id": diagnosis.get("id", ""),
            "intervention_type": intervention_type.value,
            "channel": channel,
            "confidence": confidence,
            "recovery_probability": recovery_probability,
            "requires_approval": requires_approval,
            "approved": None if requires_approval else True,
            "status": RecoveryStatus.AWAITING_APPROVAL.value if requires_approval
                      else RecoveryStatus.PENDING.value,
            "strategy_reasoning": reasoning,
            "message_template": self._get_message_template(
                intervention_type, diagnosis, txn
            ),
            "duration_ms": int((time.time() - start_time) * 1000),
        }

    def _get_message_template(self, intervention_type, diagnosis, txn):
        """Generate recovery message template."""
        amount_str = f"₹{txn.get('amount', 0) / 100:,.0f}"
        merchant = txn.get("merchant_id", "merchant").replace("merch_", "").replace("_", " ").title()

        templates = {
            InterventionType.PAYMENT_LINK: (
                f"Hi! Your payment of {amount_str} to {merchant} couldn't go through. "
                f"We've created a quick payment link for you. Tap here to complete your purchase: "
                f"{{payment_link_url}}"
            ),
            InterventionType.PAYMENT_LINK_WHATSAPP: (
                f"Hey there! 👋 Your {amount_str} payment to {merchant} hit a snag. "
                f"No worries — here's a quick link to complete it: {{payment_link_url}} "
                f"Feel free to try a different payment method if needed!"
            ),
            InterventionType.PAYMENT_LINK_EMAIL: (
                f"Your recent order with {merchant} is waiting!\n\n"
                f"Your payment of {amount_str} could not be processed. "
                f"Click below to complete your purchase using any payment method:\n\n"
                f"{{payment_link_url}}\n\n"
                f"This link expires in 24 hours."
            ),
            InterventionType.INVOICE_REISSUE: (
                f"Updated invoice for your {merchant} order ({amount_str}). "
                f"We've extended the payment deadline. Pay here: {{payment_link_url}}"
            ),
            InterventionType.ALTERNATE_METHOD_SUGGEST: (
                f"Hi! Your card payment to {merchant} was declined. "
                f"Try paying with UPI or another card: {{payment_link_url}}"
            ),
            InterventionType.SMART_RETRY: None,
            InterventionType.SUBSCRIPTION_DATE_SHIFT: None,
            InterventionType.DO_NOTHING: None,
            InterventionType.HUMAN_ESCALATION: None,
        }
        return templates.get(intervention_type)


import json
