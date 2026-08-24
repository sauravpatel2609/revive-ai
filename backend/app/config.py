"""
ReviveAI Configuration
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Application configuration."""

    # Razorpay Test Mode
    RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "rzp_test_placeholder")
    RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "placeholder_secret")
    RAZORPAY_BASE_URL = "https://api.razorpay.com/v1"

    # Database
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./reviveai.db")

    # AI Thresholds
    DIAGNOSIS_CONFIDENCE_THRESHOLD = 0.6  # Below this → human review
    RECOVERY_PROBABILITY_THRESHOLD = 0.3  # Below this → do not attempt
    STRATEGY_CONFIDENCE_THRESHOLD = 0.5   # Below this → human approval
    HIGH_VALUE_THRESHOLD = 10000          # ₹ — requires human approval above this

    # Safety Limits
    MAX_RECOVERY_ATTEMPTS = 2             # Per failed payment
    PER_RECOVERY_LIMIT = 50000            # ₹ max per recovery action
    DAILY_MERCHANT_LIMIT = 500000         # ₹ max per merchant per day
    ERROR_RATE_HALT_THRESHOLD = 0.10      # 10% error rate in 15 min → halt
    ERROR_RATE_WINDOW_MINUTES = 15

    # Intervention Cooldown
    COOLDOWN_MINUTES = 30                 # Min time between recovery attempts

    # WebSocket
    WS_PORT = 8000

    # LLM (optional — used for message personalization)
    LLM_API_KEY = os.getenv("OPENAI_API_KEY", "")
    LLM_MODEL = "gpt-4o-mini"
    LLM_ENABLED = bool(LLM_API_KEY)


config = Config()
