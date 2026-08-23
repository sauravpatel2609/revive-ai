"""
ReviveAI — Safety & Guardrails Module
Enforces all safety constraints on agent actions.
"""
import datetime
from app.config import config


class SafetyGuard:
    """
    Centralized safety enforcement for all agent actions.
    Implements: limits, circuit breaker, emergency stop, audit validation.
    """

    def __init__(self):
        self.global_halt = False
        self.merchant_halts = {}
        self.error_windows = {}  # merchant_id → [(timestamp, is_error)]

    def check_global_halt(self) -> bool:
        """Check if global emergency stop is active."""
        return self.global_halt

    def set_global_halt(self, halt: bool, reason: str = ""):
        """Set or release global emergency stop."""
        self.global_halt = halt

    def check_merchant_halt(self, merchant_id: str) -> bool:
        """Check if a specific merchant is halted."""
        return self.merchant_halts.get(merchant_id, False)

    def set_merchant_halt(self, merchant_id: str, halt: bool, reason: str = ""):
        """Set or release per-merchant halt."""
        self.merchant_halts[merchant_id] = halt

    def check_transaction_limit(self, amount: int) -> tuple:
        """
        Check if a single recovery action is within limits.
        amount: in paise
        Returns: (allowed: bool, reason: str)
        """
        amount_rupees = amount / 100
        if amount_rupees > config.PER_RECOVERY_LIMIT:
            return False, f"Amount ₹{amount_rupees:,.0f} exceeds per-recovery limit ₹{config.PER_RECOVERY_LIMIT:,}"
        return True, "Within limits"

    def record_action(self, merchant_id: str, success: bool):
        """Record an action for error rate tracking."""
        now = datetime.datetime.utcnow()
        if merchant_id not in self.error_windows:
            self.error_windows[merchant_id] = []

        self.error_windows[merchant_id].append((now, not success))

        # Clean old entries
        cutoff = now - datetime.timedelta(minutes=config.ERROR_RATE_WINDOW_MINUTES)
        self.error_windows[merchant_id] = [
            (ts, err) for ts, err in self.error_windows[merchant_id]
            if ts > cutoff
        ]

        # Check error rate
        window = self.error_windows[merchant_id]
        if len(window) >= 10:  # minimum sample
            error_count = sum(1 for _, err in window if err)
            error_rate = error_count / len(window)
            if error_rate > config.ERROR_RATE_HALT_THRESHOLD:
                self.set_merchant_halt(merchant_id, True,
                    f"Error rate {error_rate:.0%} exceeds threshold in {config.ERROR_RATE_WINDOW_MINUTES}min window")
                return

    def validate_action(self, merchant_id: str, amount: int) -> tuple:
        """
        Full validation before executing any action.
        Returns: (allowed: bool, reason: str)
        """
        if self.check_global_halt():
            return False, "Global emergency stop is active"

        if self.check_merchant_halt(merchant_id):
            return False, f"Merchant {merchant_id} is halted"

        allowed, reason = self.check_transaction_limit(amount)
        if not allowed:
            return allowed, reason

        return True, "Action approved"


# Singleton
safety_guard = SafetyGuard()
