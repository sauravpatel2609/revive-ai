"""
Synthetic data generator to mock Razorpay failed/successful transactions.
Used to create the demo dataset.
"""
import random
import datetime
import json
import uuid
import os
import math

import numpy as np

# Seed for reproducibility
random.seed(42)
np.random.seed(42)


# ── Constants ───────────────────────────────────────────────────────────────

MERCHANTS = [
    {"id": "merch_techgear", "name": "TechGear India", "category": "electronics", "avg_ticket": 3500},
    {"id": "merch_fashionhub", "name": "FashionHub", "category": "fashion", "avg_ticket": 1800},
    {"id": "merch_learnpro", "name": "LearnPro Academy", "category": "edtech", "avg_ticket": 5000},
    {"id": "merch_freshbites", "name": "FreshBites", "category": "food", "avg_ticket": 450},
    {"id": "merch_cloudstack", "name": "CloudStack SaaS", "category": "saas", "avg_ticket": 2999},
    {"id": "merch_fitzone", "name": "FitZone Gym", "category": "fitness", "avg_ticket": 1500},
    {"id": "merch_medplus", "name": "MedPlus Pharma", "category": "healthcare", "avg_ticket": 800},
    {"id": "merch_travelease", "name": "TravelEase", "category": "travel", "avg_ticket": 12000},
]

BANKS_UPI = ["SBI", "HDFC", "ICICI", "Axis", "Kotak", "PNB", "BOB", "Canara", "IndusInd", "Yes"]
BANKS_NB = ["SBI", "HDFC", "ICICI", "Axis", "Kotak", "PNB"]
CARD_NETWORKS = ["Visa", "Mastercard", "RuPay", "Amex"]
WALLETS = ["Paytm", "PhonePe", "Amazon Pay", "Freecharge"]
PAYMENT_METHODS = ["upi", "card", "netbanking", "wallet"]
PAYMENT_METHOD_WEIGHTS = [0.40, 0.35, 0.15, 0.10]
DEVICE_TYPES = ["mobile", "desktop"]
DEVICE_WEIGHTS = [0.72, 0.28]

# Failure distributions
FAILURE_TYPES = {
    "bank_timeout": {"count": 800, "error_code": "BAD_REQUEST_ERROR", "error_desc": "Payment processing didn't complete on time", "recoverable": True},
    "insufficient_funds": {"count": 500, "error_code": "BAD_REQUEST_ERROR", "error_desc": "Your payment didn't go through as it was declined by the bank. Try another payment method or contact your bank.", "recoverable": True},
    "card_expired": {"count": 300, "error_code": "BAD_REQUEST_ERROR", "error_desc": "The card is expired. Please use a different card.", "recoverable": True},
    "network_error": {"count": 250, "error_code": "GATEWAY_ERROR", "error_desc": "Payment was not completed due to a temporary issue. Please retry.", "recoverable": True},
    "auth_failed": {"count": 200, "error_code": "BAD_REQUEST_ERROR", "error_desc": "Payment was not authorized by the customer.", "recoverable": True},
    "declined_by_bank": {"count": 150, "error_code": "BAD_REQUEST_ERROR", "error_desc": "Payment declined by the issuing bank.", "recoverable": True},
    "fraud_suspected": {"count": 100, "error_code": "BAD_REQUEST_ERROR", "error_desc": "Payment declined due to suspected fraud.", "recoverable": False},
}


def generate_customer_pool(n=3000):
    """Generate a pool of unique customers."""
    customers = []
    first_names = ["Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh",
                   "Ananya", "Diya", "Myra", "Sara", "Priya", "Riya", "Neha",
                   "Rohan", "Karan", "Rahul", "Amit", "Sneha", "Ishita",
                   "Lakshmi", "Deepak", "Suresh", "Meena", "Kavita", "Raj",
                   "Pooja", "Akash", "Divya", "Harsh", "Nikhil", "Shruti"]
    last_names = ["Sharma", "Patel", "Kumar", "Singh", "Reddy", "Gupta", "Nair",
                  "Joshi", "Verma", "Iyer", "Das", "Rao", "Mishra", "Chopra",
                  "Banerjee", "Menon", "Pillai", "Chauhan", "Saxena", "Bhat"]
    domains = ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com"]

    for i in range(n):
        fn = random.choice(first_names)
        ln = random.choice(last_names)
        customers.append({
            "id": f"cust_{i:04d}",
            "name": f"{fn} {ln}",
            "email": f"{fn.lower()}.{ln.lower()}{random.randint(1,99)}@{random.choice(domains)}",
            "phone": f"+91{random.randint(7000000000, 9999999999)}",
            "preferred_method": random.choices(PAYMENT_METHODS, weights=PAYMENT_METHOD_WEIGHTS)[0],
            "risk_score": round(random.uniform(0, 1), 3),
            "transaction_count": 0,
        })
    return customers


def generate_amount(merchant, is_subscription=False):
    """Generate realistic transaction amount in paise."""
    if is_subscription:
        plans = [29900, 49900, 99900, 199900, 299900]  # ₹299 to ₹2999
        return random.choice(plans)

    avg = merchant["avg_ticket"] * 100  # convert to paise
    std = avg * 0.5
    amount = max(10000, int(np.random.normal(avg, std)))  # min ₹100
    amount = min(amount, 5000000)  # max ₹50,000
    return round(amount, -2)  # round to nearest ₹1


def generate_timestamp(base_date, hour_bias=None):
    """Generate realistic transaction timestamp with business-hour bias."""
    if hour_bias is None:
        # Weight towards business hours and evening shopping
        hour = random.choices(
            range(24),
            weights=[1,1,1,1,1,2,3,5,8,10,10,9,8,7,8,9,10,10,9,8,7,5,3,2]
        )[0]
    else:
        hour = hour_bias

    minute = random.randint(0, 59)
    second = random.randint(0, 59)
    day_offset = random.randint(0, 29)

    ts = base_date - datetime.timedelta(days=day_offset)
    ts = ts.replace(hour=hour, minute=minute, second=second)
    return ts


def generate_successful_payment(customer, merchant, base_date, is_subscription=False):
    """Generate a successful payment transaction."""
    method = random.choices(PAYMENT_METHODS, weights=PAYMENT_METHOD_WEIGHTS)[0]

    txn = {
        "id": str(uuid.uuid4()),
        "razorpay_payment_id": f"pay_{uuid.uuid4().hex[:14]}",
        "razorpay_order_id": f"order_{uuid.uuid4().hex[:14]}",
        "merchant_id": merchant["id"],
        "customer_id": customer["id"],
        "customer_email": customer["email"],
        "customer_phone": customer["phone"],
        "amount": generate_amount(merchant, is_subscription),
        "currency": "INR",
        "payment_method": method,
        "bank": random.choice(BANKS_UPI) if method == "upi" else
                random.choice(BANKS_NB) if method == "netbanking" else None,
        "card_network": random.choice(CARD_NETWORKS) if method == "card" else None,
        "status": "success",
        "error_code": None,
        "error_description": None,
        "error_source": None,
        "is_subscription": is_subscription,
        "subscription_id": f"sub_{uuid.uuid4().hex[:10]}" if is_subscription else None,
        "is_international": random.random() < 0.03,
        "device_type": random.choices(DEVICE_TYPES, weights=DEVICE_WEIGHTS)[0],
        "created_at": generate_timestamp(base_date).isoformat(),
    }
    customer["transaction_count"] += 1
    return txn


def generate_failed_payment(customer, merchant, failure_type, failure_info, base_date):
    """Generate a failed payment transaction."""
    # Bias method based on failure type
    if failure_type == "bank_timeout":
        method = random.choices(["upi", "netbanking", "card"], weights=[0.6, 0.25, 0.15])[0]
    elif failure_type in ("card_expired", "auth_failed"):
        method = "card"
    elif failure_type == "insufficient_funds":
        method = random.choices(PAYMENT_METHODS, weights=[0.3, 0.4, 0.2, 0.1])[0]
    else:
        method = random.choices(PAYMENT_METHODS, weights=PAYMENT_METHOD_WEIGHTS)[0]

    # Bank timeout clustering (simulate bank downtime)
    hour_bias = None
    if failure_type == "bank_timeout":
        # Cluster timeouts in specific hours (simulating bank maintenance)
        hour_bias = random.choices([2, 3, 14, 15], weights=[3, 3, 2, 2])[0]
        bank = random.choices(BANKS_UPI, weights=[3,1,1,1,1,1,1,1,1,1])[0]  # SBI overweight
    else:
        bank = random.choice(BANKS_UPI) if method == "upi" else \
               random.choice(BANKS_NB) if method == "netbanking" else None

    # Amount bias for insufficient funds
    if failure_type == "insufficient_funds":
        amount = generate_amount(merchant)
    else:
        amount = generate_amount(merchant)

    is_sub = failure_type == "card_expired" and random.random() < 0.6

    txn = {
        "id": str(uuid.uuid4()),
        "razorpay_payment_id": f"pay_{uuid.uuid4().hex[:14]}",
        "razorpay_order_id": f"order_{uuid.uuid4().hex[:14]}",
        "merchant_id": merchant["id"],
        "customer_id": customer["id"],
        "customer_email": customer["email"],
        "customer_phone": customer["phone"],
        "amount": amount,
        "currency": "INR",
        "payment_method": method,
        "bank": bank,
        "card_network": random.choice(CARD_NETWORKS) if method == "card" else None,
        "status": "failed",
        "error_code": failure_info["error_code"],
        "error_description": failure_info["error_desc"],
        "error_source": "bank" if failure_type in ("bank_timeout", "declined_by_bank", "insufficient_funds") else "gateway",
        "failure_type": failure_type,
        "is_subscription": is_sub,
        "subscription_id": f"sub_{uuid.uuid4().hex[:10]}" if is_sub else None,
        "is_international": random.random() < 0.03,
        "device_type": random.choices(DEVICE_TYPES, weights=DEVICE_WEIGHTS)[0],
        "created_at": generate_timestamp(base_date, hour_bias).isoformat(),
        "is_recoverable": failure_info["recoverable"],
    }
    customer["transaction_count"] += 1
    return txn


def generate_dataset():
    """Generate the full 10,000-transaction synthetic dataset."""
    print("🔧 Generating synthetic dataset...")

    base_date = datetime.datetime(2026, 8, 20, 12, 0, 0)
    customers = generate_customer_pool(3000)
    transactions = []

    # 1. Generate 7,000 successful payments
    print("  ✅ Generating 7,000 successful payments...")
    for i in range(6800):
        customer = random.choice(customers)
        merchant = random.choice(MERCHANTS)
        txn = generate_successful_payment(customer, merchant, base_date)
        transactions.append(txn)

    # 200 successful subscription renewals
    for i in range(200):
        customer = random.choice(customers)
        merchant = random.choice([m for m in MERCHANTS if m["category"] in ("saas", "edtech", "fitness")])
        txn = generate_successful_payment(customer, merchant, base_date, is_subscription=True)
        transactions.append(txn)

    # 2. Generate 2,300 failed payments
    print("  ❌ Generating 2,300 failed payments...")
    for failure_type, info in FAILURE_TYPES.items():
        for i in range(info["count"]):
            # Use customers with higher risk scores for fraud
            if failure_type == "fraud_suspected":
                eligible = [c for c in customers if c["risk_score"] > 0.7]
                customer = random.choice(eligible) if eligible else random.choice(customers)
            else:
                customer = random.choice(customers)

            merchant = random.choice(MERCHANTS)
            txn = generate_failed_payment(customer, merchant, failure_type, info, base_date)
            transactions.append(txn)

    # 3. Add "declined by cardholder" (non-recoverable, tests guardrail)
    print("  🛑 Generating 200 non-recoverable failures...")
    for i in range(200):
        customer = random.choice(customers)
        merchant = random.choice(MERCHANTS)
        txn = generate_failed_payment(
            customer, merchant, "declined_by_bank",
            {"error_code": "BAD_REQUEST_ERROR", "error_desc": "Payment declined by cardholder.", "recoverable": False},
            base_date
        )
        txn["failure_type"] = "declined_by_cardholder"
        txn["is_recoverable"] = False
        transactions.append(txn)

    # 4. Generate 300 failed subscription renewals
    print("  🔄 Generating 300 failed subscription renewals...")
    sub_failure_types = ["card_expired", "insufficient_funds", "bank_timeout"]
    for i in range(300):
        customer = random.choice(customers)
        ft = random.choice(sub_failure_types)
        merchant = random.choice([m for m in MERCHANTS if m["category"] in ("saas", "edtech", "fitness")])
        txn = generate_failed_payment(customer, merchant, ft, FAILURE_TYPES[ft], base_date)
        txn["is_subscription"] = True
        txn["subscription_id"] = f"sub_{uuid.uuid4().hex[:10]}"
        transactions.append(txn)

    # Shuffle
    random.shuffle(transactions)

    # Summary stats
    total = len(transactions)
    successes = sum(1 for t in transactions if t["status"] == "success")
    failures = sum(1 for t in transactions if t["status"] == "failed")
    recoverable = sum(1 for t in transactions if t.get("is_recoverable", False))
    non_recoverable = failures - recoverable

    print(f"\n📊 Dataset Summary:")
    print(f"  Total transactions: {total}")
    print(f"  Successful: {successes}")
    print(f"  Failed: {failures}")
    print(f"    Recoverable: {recoverable}")
    print(f"    Non-recoverable: {non_recoverable}")

    # Failure breakdown
    failure_counts = {}
    for t in transactions:
        if t["status"] == "failed":
            ft = t.get("failure_type", "unknown")
            failure_counts[ft] = failure_counts.get(ft, 0) + 1
    print(f"\n  Failure breakdown:")
    for ft, count in sorted(failure_counts.items(), key=lambda x: -x[1]):
        print(f"    {ft}: {count}")

    # Save
    output_dir = os.path.join(os.path.dirname(__file__), "..", "..", "data")
    os.makedirs(output_dir, exist_ok=True)

    output_path = os.path.join(output_dir, "synthetic_transactions.json")
    with open(output_path, "w") as f:
        json.dump(transactions, f, indent=2, default=str)
    print(f"\n💾 Saved to {output_path}")

    # Also save customers
    customers_path = os.path.join(output_dir, "customers.json")
    with open(customers_path, "w") as f:
        json.dump(customers, f, indent=2, default=str)
    print(f"💾 Saved customers to {customers_path}")

    return transactions


if __name__ == "__main__":
    generate_dataset()
