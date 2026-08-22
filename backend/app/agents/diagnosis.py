import time
import uuid
import json
import os
import pickle

import numpy as np

try:
    import xgboost as xgb
    from sklearn.preprocessing import LabelEncoder
    HAS_XGBOOST = True
except ImportError:
    HAS_XGBOOST = False

from app.models import FailureType

# ── Feature Engineering ─────────────────────────────────────────────────────

# Mappings for categorical encoding
METHOD_MAP = {"upi": 0, "card": 1, "netbanking": 2, "wallet": 3}
DEVICE_MAP = {"mobile": 0, "desktop": 1}
ERROR_SOURCE_MAP = {"bank": 0, "gateway": 1, "customer": 2, "internal": 3}
BANK_MAP = {b: i for i, b in enumerate([
    "SBI", "HDFC", "ICICI", "Axis", "Kotak", "PNB", "BOB",
    "Canara", "IndusInd", "Yes", None
])}
CARD_NETWORK_MAP = {"Visa": 0, "Mastercard": 1, "RuPay": 2, "Amex": 3, None: 4}
MERCHANT_CATEGORY_MAP = {
    "electronics": 0, "fashion": 1, "edtech": 2, "food": 3,
    "saas": 4, "fitness": 5, "healthcare": 6, "travel": 7
}

# Failure type label mapping
FAILURE_LABELS = [
    "bank_timeout", "insufficient_funds", "card_expired",
    "network_error", "auth_failed", "declined_by_bank",
    "fraud_suspected", "declined_by_cardholder", "mandate_expired", "unknown"
]
LABEL_TO_IDX = {l: i for i, l in enumerate(FAILURE_LABELS)}
IDX_TO_LABEL = {i: l for l, i in LABEL_TO_IDX.items()}

# Which failure types are recoverable
RECOVERABLE_TYPES = {
    "bank_timeout", "insufficient_funds", "card_expired",
    "network_error", "auth_failed", "declined_by_bank"
}
NON_RECOVERABLE_TYPES = {
    "fraud_suspected", "declined_by_cardholder"
}


def extract_features(txn: dict) -> np.ndarray:
    """Extract 15 features from a transaction for XGBoost classification."""
    amount = txn.get("amount", 0)
    hour = 12
    try:
        created = txn.get("created_at", "")
        if "T" in str(created):
            hour = int(str(created).split("T")[1].split(":")[0])
    except (ValueError, IndexError):
        hour = 12

    features = [
        METHOD_MAP.get(txn.get("payment_method"), 0),          # 0: method
        amount / 100.0,                                         # 1: amount in rupees
        DEVICE_MAP.get(txn.get("device_type"), 0),             # 2: device
        hour,                                                   # 3: hour of day
        ERROR_SOURCE_MAP.get(txn.get("error_source"), 3),      # 4: error source
        BANK_MAP.get(txn.get("bank"), 10),                     # 5: bank
        CARD_NETWORK_MAP.get(txn.get("card_network"), 4),      # 6: card network
        1 if txn.get("is_subscription") else 0,                # 7: is subscription
        1 if txn.get("is_international") else 0,               # 8: is international
        txn.get("customer_transaction_count", 0),              # 9: customer history
        len(txn.get("error_description", "") or ""),           # 10: error desc length
        1 if "timeout" in (txn.get("error_description", "") or "").lower() else 0,  # 11
        1 if "expired" in (txn.get("error_description", "") or "").lower() else 0,  # 12
        1 if "fraud" in (txn.get("error_description", "") or "").lower() else 0,    # 13
        1 if "declined" in (txn.get("error_description", "") or "").lower() else 0, # 14
    ]
    return np.array(features, dtype=np.float32)


# ── Rule-Based Fallback ─────────────────────────────────────────────────────

def rule_based_diagnosis(txn: dict) -> dict:
    """
    Fallback rule-based classification using error codes and descriptions.
    Returns diagnosis dict with lower confidence than ML model.
    """
    error_desc = (txn.get("error_description", "") or "").lower()
    error_code = txn.get("error_code", "") or ""
    method = txn.get("payment_method", "")

    # Rule matching (order matters — more specific first)
    if "fraud" in error_desc or "suspicious" in error_desc:
        return _make_diagnosis(txn, "fraud_suspected", 0.85, False,
                               "Error description contains fraud indicators")

    if "expired" in error_desc and method == "card":
        return _make_diagnosis(txn, "card_expired", 0.90, True,
                               "Card expiry detected from error message")

    if "cardholder" in error_desc and "declined" in error_desc:
        return _make_diagnosis(txn, "declined_by_cardholder", 0.88, False,
                               "Cardholder intentionally declined — non-recoverable")

    if "timeout" in error_desc or "didn't complete on time" in error_desc:
        return _make_diagnosis(txn, "bank_timeout", 0.80, True,
                               "Timeout pattern detected — likely bank-side issue")

    if "insufficient" in error_desc or "balance" in error_desc:
        return _make_diagnosis(txn, "insufficient_funds", 0.82, True,
                               "Insufficient funds indicated by bank response")

    if error_code == "GATEWAY_ERROR":
        return _make_diagnosis(txn, "network_error", 0.75, True,
                               "Gateway error — likely transient network issue")

    if "not authorized" in error_desc or "authentication" in error_desc:
        return _make_diagnosis(txn, "auth_failed", 0.78, True,
                               "Authentication/authorization failure")

    if "declined" in error_desc:
        return _make_diagnosis(txn, "declined_by_bank", 0.70, True,
                               "Generic bank decline — may be recoverable with retry")

    return _make_diagnosis(txn, "unknown", 0.40, True,
                           "Could not determine failure type from available signals")


def _make_diagnosis(txn, failure_type, confidence, recoverable, reasoning):
    """Create a diagnosis result dict."""
    severity = "critical" if not recoverable else (
        "high" if confidence > 0.8 else "medium" if confidence > 0.6 else "low"
    )
    return {
        "id": str(uuid.uuid4()),
        "transaction_id": txn.get("id", ""),
        "failure_type": failure_type,
        "root_cause": failure_type,
        "severity": severity,
        "confidence": round(confidence, 4),
        "recovery_eligible": recoverable,
        "reasoning": reasoning,
        "features_used": None,
        "model_type": "rule_based",
    }


# ── XGBoost Model ───────────────────────────────────────────────────────────

class DiagnosisAgent:
    """
    Diagnosis Agent — classifies payment failure root cause.

    Uses XGBoost for structured classification with rule-based fallback.
    """

    def __init__(self, model_path=None):
        self.model = None
        self.model_path = model_path or os.path.join(
            os.path.dirname(__file__), "..", "..", "data", "diagnosis_model.pkl"
        )
        self._load_model()

    def _load_model(self):
        """Load trained XGBoost model if available."""
        if not HAS_XGBOOST:
            print("⚠️  XGBoost not installed — using rule-based diagnosis only")
            return
        if os.path.exists(self.model_path):
            with open(self.model_path, "rb") as f:
                self.model = pickle.load(f)
            print("✅ Loaded XGBoost diagnosis model")
        else:
            print("⚠️  No trained model found — using rule-based diagnosis (train with train_model())")

    def diagnose(self, txn: dict) -> dict:
        """
        Diagnose a failed payment.
        Returns diagnosis dict with failure_type, confidence, recovery_eligible, reasoning.
        """
        start_time = time.time()

        # If model is available, use ML
        if self.model is not None and HAS_XGBOOST:
            diagnosis = self._ml_diagnosis(txn)
        else:
            diagnosis = rule_based_diagnosis(txn)

        diagnosis["duration_ms"] = int((time.time() - start_time) * 1000)
        return diagnosis

    def _ml_diagnosis(self, txn: dict) -> dict:
        """Use XGBoost model for classification."""
        features = extract_features(txn).reshape(1, -1)
        proba = self.model.predict_proba(features)[0]
        predicted_idx = int(np.argmax(proba))
        confidence = float(proba[predicted_idx])
        failure_type = IDX_TO_LABEL.get(predicted_idx, "unknown")

        recoverable = failure_type in RECOVERABLE_TYPES
        severity = "critical" if not recoverable else (
            "high" if confidence > 0.8 else "medium" if confidence > 0.6 else "low"
        )

        # Feature importance for this prediction
        feature_names = [
            "method", "amount", "device", "hour", "error_source",
            "bank", "card_network", "is_subscription", "is_international",
            "customer_history", "error_desc_len", "has_timeout",
            "has_expired", "has_fraud", "has_declined"
        ]
        importance = None
        if hasattr(self.model, "feature_importances_"):
            importance = {
                name: round(float(imp), 4)
                for name, imp in zip(feature_names, self.model.feature_importances_)
            }

        return {
            "id": str(uuid.uuid4()),
            "transaction_id": txn.get("id", ""),
            "failure_type": failure_type,
            "root_cause": failure_type,
            "severity": severity,
            "confidence": round(confidence, 4),
            "recovery_eligible": recoverable,
            "reasoning": f"XGBoost classified as '{failure_type}' with {confidence:.1%} confidence. "
                         f"Top signals: error description keywords, payment method, bank identity.",
            "features_used": importance,
            "model_type": "xgboost",
        }

    def train_model(self, transactions: list):
        """
        Train XGBoost on labeled transaction data.
        transactions: list of dicts with 'failure_type' field.
        """
        if not HAS_XGBOOST:
            print("❌ XGBoost not installed. Install with: pip install xgboost scikit-learn")
            return

        from sklearn.model_selection import train_test_split

        failed = [t for t in transactions if t.get("status") == "failed" and t.get("failure_type")]
        if len(failed) < 50:
            print(f"❌ Not enough labeled failures ({len(failed)}). Need at least 50.")
            return

        print(f"🧠 Training XGBoost on {len(failed)} labeled failures...")

        X = np.array([extract_features(t) for t in failed])
        y = np.array([LABEL_TO_IDX.get(t["failure_type"], 9) for t in failed])

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )

        model = xgb.XGBClassifier(
            n_estimators=200,
            max_depth=6,
            learning_rate=0.1,
            objective="multi:softprob",
            num_class=len(FAILURE_LABELS),
            eval_metric="mlogloss",
            random_state=42,
            use_label_encoder=False,
        )
        model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)

        # Evaluate
        from sklearn.metrics import classification_report, accuracy_score
        y_pred = model.predict(X_test)
        acc = accuracy_score(y_test, y_pred)
        print(f"\n📊 Test Accuracy: {acc:.4f}")
        print("\nClassification Report:")
        target_names = [IDX_TO_LABEL[i] for i in sorted(set(y_test))]
        print(classification_report(y_test, y_pred, target_names=target_names, zero_division=0))

        # Save
        self.model = model
        os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
        with open(self.model_path, "wb") as f:
            pickle.dump(model, f)
        print(f"💾 Model saved to {self.model_path}")

        return {"accuracy": acc, "model_path": self.model_path}
