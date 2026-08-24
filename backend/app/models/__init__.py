"""
ReviveAI Database Models — SQLAlchemy ORM
"""
import datetime
import enum
import uuid

from sqlalchemy import (
    Column, String, Float, Integer, DateTime, Boolean, Enum, Text, JSON,
    create_engine
)
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


# ── Enums ───────────────────────────────────────────────────────────────────

class PaymentStatus(str, enum.Enum):
    SUCCESS = "success"
    FAILED = "failed"
    RECOVERED = "recovered"
    RECOVERY_IN_PROGRESS = "recovery_in_progress"
    UNRECOVERABLE = "unrecoverable"


class FailureType(str, enum.Enum):
    BANK_TIMEOUT = "bank_timeout"
    INSUFFICIENT_FUNDS = "insufficient_funds"
    CARD_EXPIRED = "card_expired"
    NETWORK_ERROR = "network_error"
    AUTH_FAILED = "auth_failed"
    DECLINED_BY_BANK = "declined_by_bank"
    FRAUD_SUSPECTED = "fraud_suspected"
    DECLINED_BY_CARDHOLDER = "declined_by_cardholder"
    MANDATE_EXPIRED = "mandate_expired"
    UNKNOWN = "unknown"


class InterventionType(str, enum.Enum):
    SMART_RETRY = "smart_retry"
    PAYMENT_LINK = "payment_link"
    PAYMENT_LINK_WHATSAPP = "payment_link_whatsapp"
    PAYMENT_LINK_EMAIL = "payment_link_email"
    INVOICE_REISSUE = "invoice_reissue"
    SUBSCRIPTION_DATE_SHIFT = "subscription_date_shift"
    ALTERNATE_METHOD_SUGGEST = "alternate_method_suggest"
    DO_NOTHING = "do_nothing"
    HUMAN_ESCALATION = "human_escalation"


class RecoveryStatus(str, enum.Enum):
    PENDING = "pending"
    EXECUTING = "executing"
    AWAITING_CUSTOMER = "awaiting_customer"
    RECOVERED = "recovered"
    FAILED = "failed"
    SKIPPED = "skipped"
    AWAITING_APPROVAL = "awaiting_approval"
    EXPIRED = "expired"


# ── Models ──────────────────────────────────────────────────────────────────

class Transaction(Base):
    """Raw transaction data — both successful and failed."""
    __tablename__ = "transactions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    razorpay_payment_id = Column(String, unique=True, nullable=True)
    razorpay_order_id = Column(String, nullable=True)
    merchant_id = Column(String, nullable=False)
    customer_id = Column(String, nullable=True)
    customer_email = Column(String, nullable=True)
    customer_phone = Column(String, nullable=True)

    amount = Column(Float, nullable=False)          # in paise
    currency = Column(String, default="INR")
    payment_method = Column(String, nullable=True)  # upi, card, netbanking, wallet
    bank = Column(String, nullable=True)
    card_network = Column(String, nullable=True)

    status = Column(Enum(PaymentStatus), nullable=False)
    error_code = Column(String, nullable=True)
    error_description = Column(String, nullable=True)
    error_source = Column(String, nullable=True)

    is_subscription = Column(Boolean, default=False)
    subscription_id = Column(String, nullable=True)
    is_international = Column(Boolean, default=False)
    device_type = Column(String, nullable=True)     # mobile, desktop

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow,
                        onupdate=datetime.datetime.utcnow)


class Diagnosis(Base):
    """Root cause diagnosis for a failed payment."""
    __tablename__ = "diagnoses"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    transaction_id = Column(String, nullable=False)
    failure_type = Column(Enum(FailureType), nullable=False)
    root_cause = Column(String, nullable=True)
    severity = Column(String, nullable=True)           # low, medium, high, critical
    confidence = Column(Float, nullable=False)
    recovery_eligible = Column(Boolean, nullable=False)
    reasoning = Column(Text, nullable=True)
    features_used = Column(JSON, nullable=True)        # Feature importance for explainability

    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class Intervention(Base):
    """Recovery intervention plan and execution record."""
    __tablename__ = "interventions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    transaction_id = Column(String, nullable=False)
    diagnosis_id = Column(String, nullable=False)

    intervention_type = Column(Enum(InterventionType), nullable=False)
    channel = Column(String, nullable=True)             # sms, email, whatsapp
    confidence = Column(Float, nullable=False)
    recovery_probability = Column(Float, nullable=True)
    requires_approval = Column(Boolean, default=False)
    approved = Column(Boolean, nullable=True)
    approved_by = Column(String, nullable=True)

    # Execution details
    status = Column(Enum(RecoveryStatus), nullable=False, default=RecoveryStatus.PENDING)
    razorpay_payment_link_id = Column(String, nullable=True)
    razorpay_new_order_id = Column(String, nullable=True)
    message_sent = Column(Text, nullable=True)
    execution_error = Column(Text, nullable=True)

    # Outcome
    recovered_amount = Column(Float, nullable=True)     # in paise
    recovered_payment_id = Column(String, nullable=True)
    recovery_time_seconds = Column(Integer, nullable=True)

    # Strategy learning
    reward = Column(Float, nullable=True)               # For bandit: 1.0 if recovered, 0.0 if not
    strategy_reasoning = Column(Text, nullable=True)

    attempt_number = Column(Integer, default=1)
    idempotency_key = Column(String, unique=True, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow,
                        onupdate=datetime.datetime.utcnow)


class AuditLog(Base):
    """Immutable audit trail for every agent action."""
    __tablename__ = "audit_log"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    transaction_id = Column(String, nullable=False)
    agent = Column(String, nullable=False)              # diagnosis, strategy, execution, verification
    action = Column(String, nullable=False)
    input_data = Column(JSON, nullable=True)
    output_data = Column(JSON, nullable=True)
    reasoning = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
    error = Column(Text, nullable=True)
    duration_ms = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class MerchantState(Base):
    """Per-merchant state tracking for safety limits."""
    __tablename__ = "merchant_state"

    merchant_id = Column(String, primary_key=True)
    daily_recovery_total = Column(Float, default=0)     # in paise
    daily_recovery_count = Column(Integer, default=0)
    last_reset_date = Column(String, nullable=True)
    is_halted = Column(Boolean, default=False)
    halt_reason = Column(String, nullable=True)
    error_count_window = Column(Integer, default=0)
    window_start = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow,
                        onupdate=datetime.datetime.utcnow)


# ── Database Setup ──────────────────────────────────────────────────────────

def get_engine(database_url: str = "sqlite:///./reviveai.db"):
    """Create database engine."""
    return create_engine(database_url, echo=False)


def create_tables(engine):
    """Create all tables."""
    Base.metadata.create_all(engine)


def get_session(engine):
    """Create a session factory."""
    return sessionmaker(bind=engine)
