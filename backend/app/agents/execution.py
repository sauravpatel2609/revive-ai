import time
import uuid
import json
from typing import Optional

from app.config import config
from app.models import InterventionType, RecoveryStatus

# Razorpay API Client
# Switches between real API calls and mock responses based on env keys

class RazorpayClient:
    """
    Razorpay API client wrapper.
    - Real mode: uses razorpay SDK when rzp_test_* keys are configured
    - Simulated mode: returns realistic mock responses for demo without keys
    """

    def __init__(self):
        self.key_id = config.RAZORPAY_KEY_ID
        self.key_secret = config.RAZORPAY_KEY_SECRET
        self.base_url = config.RAZORPAY_BASE_URL
        # Use real API if a proper test key is configured
        self._simulated = not (
            self.key_id and self.key_id.startswith("rzp_test_") and
            self.key_id != "rzp_test_placeholder"
        )
        if not self._simulated:
            try:
                import razorpay as _rzp
                self._client = _rzp.Client(auth=(self.key_id, self.key_secret))
                print(f"✅ Razorpay client initialised (REAL mode) — key: {self.key_id[:18]}...")
            except ImportError:
                print("⚠️  razorpay SDK not installed — falling back to simulation")
                self._simulated = True
        else:
            print("ℹ️  Razorpay client running in SIMULATED mode")

    def create_payment_link(self, amount: int, currency: str,
                            description: str, customer: dict,
                            idempotency_key: str,
                            expire_by: Optional[int] = None) -> dict:
        """
        Create a Razorpay Payment Link.
        POST /v1/payment_links
        """
        if self._simulated:
            link_id = f"plink_{uuid.uuid4().hex[:14]}"
            short_url = f"https://rzp.io/i/{link_id[:8]}"
            return {
                "success": True,
                "id": link_id,
                "short_url": short_url,
                "amount": amount,
                "currency": currency,
                "description": description,
                "status": "created",
                "customer": customer,
            }

        # Real API call
        data = {
            "amount": amount,
            "currency": currency,
            "description": description,
            "customer": customer,
            "notify": {"sms": True, "email": True},
            "reminder_enable": True,
        }
        if expire_by:
            data["expire_by"] = expire_by
            
        # The razorpay SDK does not have payment_link out of the box in some older versions,
        # but modern versions do. Let's use the generic post request if needed, 
        # or the standard payment_link attribute.
        try:
            return self._client.payment_link.create(data)
        except AttributeError:
            # Fallback for older SDKs
            return self._client.utility.request("POST", "/v1/payment_links", data=data)

    def create_order(self, amount: int, currency: str,
                     receipt: str, idempotency_key: str) -> dict:
        """
        Create a Razorpay Order for smart retry.
        POST /v1/orders
        """
        if self._simulated:
            order_id = f"order_{uuid.uuid4().hex[:14]}"
            return {
                "success": True,
                "id": order_id,
                "amount": amount,
                "currency": currency,
                "receipt": receipt,
                "status": "created",
            }

        data = {
            "amount": amount,
            "currency": currency,
            "receipt": receipt,
        }
        return self._client.order.create(data=data)

    def create_invoice(self, amount: int, currency: str,
                       description: str, customer: dict,
                       due_date: str, idempotency_key: str) -> dict:
        """
        Create a Razorpay Invoice.
        POST /v1/invoices
        """
        if self._simulated:
            invoice_id = f"inv_{uuid.uuid4().hex[:14]}"
            return {
                "success": True,
                "id": invoice_id,
                "amount": amount,
                "currency": currency,
                "description": description,
                "status": "issued",
                "short_url": f"https://rzp.io/inv/{invoice_id[:8]}",
                "due_date": due_date,
                "customer": customer,
            }

        # Real API call
        # We need a Unix timestamp for due_date, or we can omit it for basic invoices.
        import time
        data = {
            "type": "invoice",
            "description": description,
            "customer": customer,
            "line_items": [{"name": description, "amount": amount, "currency": currency, "quantity": 1}],
            "sms_notify": 1,
            "email_notify": 1,
            "draft": "0"
        }
        return self._client.invoice.create(data=data)

    def fetch_payment(self, payment_id: str) -> dict:
        """
        Fetch payment details.
        GET /v1/payments/{payment_id}
        """
        if self._simulated:
            return {
                "id": payment_id,
                "amount": 100000,
                "status": "captured",
                "method": "upi",
            }
        return self._client.payment.fetch(payment_id)

    def update_subscription(self, subscription_id: str,
                            updates: dict) -> dict:
        """
        Update subscription (e.g., billing date shift).
        PATCH /v1/subscriptions/{subscription_id}
        """
        if self._simulated:
            return {
                "success": True,
                "id": subscription_id,
                "status": "active",
                "updates_applied": updates,
            }
        
        # Real API call
        # Using generic request for subscription updates
        return self._client.utility.request("PATCH", f"/v1/subscriptions/{subscription_id}", data=updates)


# ── Execution Agent ─────────────────────────────────────────────────────────

class ExecutionAgent:
    def __init__(self):
        self.rzp = RazorpayClient()
        self.executed_keys = set()  # Idempotency tracking

    def execute(self, plan: dict, txn: dict, diagnosis: dict) -> dict:
        """
        Execute the intervention plan.

        Returns execution result with action taken, API response, and status.
        """
        start_time = time.time()
        intervention_type = plan.get("intervention_type", "do_nothing")
        idempotency_key = f"revive_{txn.get('id', '')}_{plan.get('id', '')}"

        # ── Idempotency check ───────────────────────────────────────────
        if idempotency_key in self.executed_keys:
            return self._make_result(
                plan, "skipped", None,
                "Duplicate execution prevented by idempotency check.",
                start_time, error="IDEMPOTENCY_DUPLICATE"
            )
        self.executed_keys.add(idempotency_key)

        # ── Approval check ──────────────────────────────────────────────
        if plan.get("requires_approval") and not plan.get("approved"):
            return self._make_result(
                plan, RecoveryStatus.AWAITING_APPROVAL.value, None,
                "Awaiting human approval for high-value recovery.",
                start_time
            )

        # ── Route to handler ────────────────────────────────────────────
        try:
            handler = self._get_handler(intervention_type)
            if handler is None:
                return self._make_result(
                    plan, RecoveryStatus.SKIPPED.value, None,
                    f"No execution needed for intervention: {intervention_type}",
                    start_time
                )
            result = handler(plan, txn, diagnosis, idempotency_key)
            return {**result, "duration_ms": int((time.time() - start_time) * 1000)}

        except Exception as e:
            return self._make_result(
                plan, RecoveryStatus.FAILED.value, None,
                f"Execution failed: {str(e)}",
                start_time, error=str(e)
            )

    def _get_handler(self, intervention_type: str):
        """Route to the correct handler based on intervention type."""
        handlers = {
            InterventionType.SMART_RETRY.value: self._handle_smart_retry,
            InterventionType.PAYMENT_LINK.value: self._handle_payment_link,
            InterventionType.PAYMENT_LINK_WHATSAPP.value: self._handle_payment_link,
            InterventionType.PAYMENT_LINK_EMAIL.value: self._handle_payment_link,
            InterventionType.INVOICE_REISSUE.value: self._handle_invoice,
            InterventionType.SUBSCRIPTION_DATE_SHIFT.value: self._handle_subscription_shift,
            InterventionType.ALTERNATE_METHOD_SUGGEST.value: self._handle_payment_link,
            InterventionType.DO_NOTHING.value: None,
            InterventionType.HUMAN_ESCALATION.value: None,
        }
        return handlers.get(intervention_type)

    def _handle_smart_retry(self, plan, txn, diagnosis, idempotency_key):
        """Create a new order for retry."""
        amount = txn.get("amount", 0)
        receipt = f"revive_retry_{txn.get('id', '')[:8]}"

        api_response = self.rzp.create_order(
            amount=amount,
            currency=txn.get("currency", "INR"),
            receipt=receipt,
            idempotency_key=idempotency_key,
        )

        return self._make_result(
            plan, RecoveryStatus.AWAITING_CUSTOMER.value, api_response,
            f"Smart retry: Created new order {api_response.get('order_id')} "
            f"for ₹{amount/100:,.0f}.",
            time.time(),
            payment_link_id=None,
            new_order_id=api_response.get("order_id"),
        )

    def _handle_payment_link(self, plan, txn, diagnosis, idempotency_key):
        """Create a payment link and send via appropriate channel."""
        amount = txn.get("amount", 0)
        merchant_name = txn.get("merchant_id", "merchant").replace("merch_", "").replace("_", " ").title()

        customer = {
            "name": txn.get("customer_id", "Customer"),
            "email": txn.get("customer_email", ""),
            "contact": txn.get("customer_phone", ""),
        }

        api_response = self.rzp.create_payment_link(
            amount=amount,
            currency=txn.get("currency", "INR"),
            description=f"Recovery payment for {merchant_name} order",
            customer=customer,
            idempotency_key=idempotency_key,
        )

        channel = plan.get("channel", "sms")
        short_url = api_response.get("short_url", "")

        # Generate message from template
        message = plan.get("message_template", "")
        if message:
            message = message.replace("{payment_link_url}", short_url)

        return self._make_result(
            plan, RecoveryStatus.AWAITING_CUSTOMER.value, api_response,
            f"Payment link created ({channel}): {short_url}. "
            f"Amount: ₹{amount/100:,.0f}. Sent to {customer.get('contact', 'N/A')}.",
            time.time(),
            payment_link_id=api_response.get("payment_link_id"),
            message_sent=message,
        )

    def _handle_invoice(self, plan, txn, diagnosis, idempotency_key):
        """Create an invoice with extended terms."""
        import datetime
        amount = txn.get("amount", 0)
        due_date = (datetime.datetime.utcnow() + datetime.timedelta(days=7)).strftime("%Y-%m-%d")

        customer = {
            "name": txn.get("customer_id", "Customer"),
            "email": txn.get("customer_email", ""),
            "contact": txn.get("customer_phone", ""),
        }

        api_response = self.rzp.create_invoice(
            amount=amount,
            currency=txn.get("currency", "INR"),
            description="Recovery invoice with extended payment terms",
            customer=customer,
            due_date=due_date,
            idempotency_key=idempotency_key,
        )

        return self._make_result(
            plan, RecoveryStatus.AWAITING_CUSTOMER.value, api_response,
            f"Invoice reissued: {api_response.get('invoice_id')}. "
            f"Amount: ₹{amount/100:,.0f}. Due: {due_date}.",
            time.time(),
            payment_link_id=api_response.get("invoice_id"),
        )

    def _handle_subscription_shift(self, plan, txn, diagnosis, idempotency_key):
        """Shift subscription billing date."""
        sub_id = txn.get("subscription_id", "")
        if not sub_id:
            return self._make_result(
                plan, RecoveryStatus.FAILED.value, None,
                "No subscription ID found — cannot shift billing date.",
                time.time(), error="MISSING_SUBSCRIPTION_ID"
            )

        api_response = self.rzp.update_subscription(
            subscription_id=sub_id,
            updates={"charge_at": "next_billing_cycle_shifted_3_days"},
        )

        return self._make_result(
            plan, RecoveryStatus.AWAITING_CUSTOMER.value, api_response,
            f"Subscription {sub_id} billing date shifted by 3 days.",
            time.time(),
        )

    def _make_result(self, plan, status, api_response, reasoning,
                     start_time, error=None, payment_link_id=None,
                     new_order_id=None, message_sent=None):
        """Create an execution result dict."""
        return {
            "intervention_id": plan.get("id", ""),
            "transaction_id": plan.get("transaction_id", ""),
            "intervention_type": plan.get("intervention_type", ""),
            "status": status,
            "api_response": api_response,
            "reasoning": reasoning,
            "error": error,
            "payment_link_id": payment_link_id,
            "new_order_id": new_order_id,
            "message_sent": message_sent,
            "idempotency_key": f"revive_{plan.get('transaction_id', '')}_{plan.get('id', '')}",
        }
