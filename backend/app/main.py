"""
ReviveAI — Main FastAPI Application
Orchestrator for the revenue recovery pipeline.
"""
import json
import os
import asyncio
import time
import uuid
from collections import defaultdict
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.agents.diagnosis import DiagnosisAgent
from app.agents.strategy import StrategyAgent
from app.agents.execution import ExecutionAgent
from app.agents.verification import VerificationAgent
from app.safety.guardrails import safety_guard

# ── App Setup ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="ReviveAI",
    description="Autonomous Revenue Recovery for Razorpay Merchants",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Global State ────────────────────────────────────────────────────────────

diagnosis_agent = DiagnosisAgent()
strategy_agent = StrategyAgent()
execution_agent = ExecutionAgent()
verification_agent = VerificationAgent()

# In-memory stores (would be database in production)
transactions_store = []
failed_transactions = []
recovery_pipeline = []    # Full pipeline records
audit_log = []
ws_clients = set()        # WebSocket connections for live feed

# Running metrics
metrics = {
    "total_processed": 0,
    "total_failed": 0,
    "total_recovered": 0,
    "total_recovered_amount": 0,
    "total_skipped": 0,
    "total_errors": 0,
    "baseline_recovered": 0,
    "baseline_recovered_amount": 0,
    "start_time": time.time(),
}


# ── Pydantic Models ─────────────────────────────────────────────────────────

class PaymentWebhook(BaseModel):
    """Simulated Razorpay payment.failed webhook payload."""
    id: Optional[str] = None
    razorpay_payment_id: Optional[str] = None
    razorpay_order_id: Optional[str] = None
    merchant_id: str = "merch_techgear"
    customer_id: Optional[str] = None
    customer_email: Optional[str] = None
    customer_phone: Optional[str] = None
    amount: int = 100000
    currency: str = "INR"
    payment_method: Optional[str] = "upi"
    bank: Optional[str] = None
    card_network: Optional[str] = None
    status: str = "failed"
    error_code: Optional[str] = None
    error_description: Optional[str] = None
    error_source: Optional[str] = None
    failure_type: Optional[str] = None
    is_recoverable: Optional[bool] = None
    is_subscription: bool = False
    subscription_id: Optional[str] = None
    is_international: bool = False
    device_type: Optional[str] = "mobile"
    created_at: Optional[str] = None


class ApprovalRequest(BaseModel):
    """Human approval for high-value recovery."""
    intervention_id: str
    approved: bool
    approved_by: str = "operator"


# ── WebSocket ───────────────────────────────────────────────────────────────

async def broadcast(event_type: str, data: dict):
    """Broadcast event to all connected WebSocket clients."""
    message = json.dumps({"type": event_type, "data": data, "timestamp": time.time()})
    disconnected = set()
    for ws in ws_clients:
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.add(ws)
    for ws in disconnected:
        ws_clients.discard(ws)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for live dashboard feed."""
    await websocket.accept()
    ws_clients.add(websocket)
    try:
        # Send initial state
        await websocket.send_text(json.dumps({
            "type": "init",
            "data": get_dashboard_data(),
            "timestamp": time.time()
        }))
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_clients.discard(websocket)


# ── Core Pipeline ───────────────────────────────────────────────────────────

async def process_failed_payment(txn: dict) -> dict:
    """
    Core pipeline: Diagnose → Strategize → Execute → Verify → Learn

    This is the killer loop.
    """
    pipeline_record = {
        "id": str(uuid.uuid4()),
        "transaction": txn,
        "diagnosis": None,
        "plan": None,
        "execution": None,
        "verification": None,
        "status": "processing",
        "created_at": time.time(),
    }

    merchant_id = txn.get("merchant_id", "unknown")

    # ── Safety pre-check ────────────────────────────────────────────────
    allowed, reason = safety_guard.validate_action(merchant_id, txn.get("amount", 0))
    if not allowed:
        pipeline_record["status"] = "blocked"
        pipeline_record["block_reason"] = reason
        _log_audit(txn, "safety", "block", {"reason": reason})
        await broadcast("blocked", {
            "transaction_id": txn.get("id", ""),
            "reason": reason,
        })
        return pipeline_record

    # ── Step 1: DIAGNOSE ────────────────────────────────────────────────
    diagnosis = diagnosis_agent.diagnose(txn)
    pipeline_record["diagnosis"] = diagnosis
    _log_audit(txn, "diagnosis", "classify", diagnosis)

    await broadcast("diagnosis", {
        "transaction_id": txn.get("id", ""),
        "failure_type": diagnosis["failure_type"],
        "confidence": diagnosis["confidence"],
        "recovery_eligible": diagnosis["recovery_eligible"],
        "reasoning": diagnosis["reasoning"],
    })

    # ── Step 2: STRATEGIZE ──────────────────────────────────────────────
    plan = strategy_agent.select_strategy(diagnosis, txn)
    pipeline_record["plan"] = plan
    _log_audit(txn, "strategy", "select", plan)

    await broadcast("strategy", {
        "transaction_id": txn.get("id", ""),
        "intervention_type": plan["intervention_type"],
        "channel": plan.get("channel"),
        "confidence": plan["confidence"],
        "recovery_probability": plan.get("recovery_probability", 0),
        "requires_approval": plan.get("requires_approval", False),
        "reasoning": plan.get("strategy_reasoning", ""),
    })

    # ── Step 3: EXECUTE ─────────────────────────────────────────────────
    exec_result = execution_agent.execute(plan, txn, diagnosis)
    pipeline_record["execution"] = exec_result
    _log_audit(txn, "execution", exec_result.get("intervention_type", ""), exec_result)

    await broadcast("execution", {
        "transaction_id": txn.get("id", ""),
        "intervention_type": plan["intervention_type"],
        "status": exec_result.get("status", ""),
        "payment_link_id": exec_result.get("payment_link_id"),
        "reasoning": exec_result.get("reasoning", ""),
    })

    # ── Step 4: VERIFY ──────────────────────────────────────────────────
    verify_result = verification_agent.verify(exec_result, txn, diagnosis, plan)
    pipeline_record["verification"] = verify_result
    _log_audit(txn, "verification", "verify", verify_result)

    # ── Step 5: LEARN ───────────────────────────────────────────────────
    reward = verify_result.get("reward", 0)
    if plan["intervention_type"] not in ("do_nothing", "human_escalation"):
        strategy_agent.update_reward(diagnosis, txn, plan["intervention_type"], reward)

    # ── Update metrics ──────────────────────────────────────────────────
    metrics["total_processed"] += 1
    metrics["total_failed"] += 1

    if verify_result.get("recovered"):
        metrics["total_recovered"] += 1
        metrics["total_recovered_amount"] += txn.get("amount", 0)
        pipeline_record["status"] = "recovered"
    elif plan["intervention_type"] in ("do_nothing", "human_escalation"):
        metrics["total_skipped"] += 1
        pipeline_record["status"] = "skipped"
    else:
        pipeline_record["status"] = "attempted_not_recovered"

    safety_guard.record_action(merchant_id, verify_result.get("recovered", False))

    await broadcast("recovery", {
        "transaction_id": txn.get("id", ""),
        "recovered": verify_result.get("recovered", False),
        "amount": verify_result.get("recovered_amount_rupees", 0),
        "intervention_type": plan["intervention_type"],
        "failure_type": diagnosis["failure_type"],
        "reasoning": verify_result.get("reasoning", ""),
        "metrics": get_live_metrics(),
    })

    recovery_pipeline.append(pipeline_record)
    return pipeline_record


def _log_audit(txn, agent, action, data):
    """Append to immutable audit log."""
    audit_log.append({
        "id": str(uuid.uuid4()),
        "transaction_id": txn.get("id", ""),
        "agent": agent,
        "action": action,
        "data": data,
        "timestamp": time.time(),
    })


# ── API Endpoints ───────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"name": "ReviveAI", "version": "1.0.0", "status": "running"}


@app.get("/api/health")
async def health():
    return {"status": "healthy", "uptime": time.time() - metrics["start_time"]}


@app.post("/api/webhook/payment-failed")
async def receive_payment_failed(webhook: PaymentWebhook):
    """
    Receive a payment.failed webhook (simulated or real Razorpay).
    Triggers the full recovery pipeline.
    """
    txn = webhook.dict()
    if not txn.get("id"):
        txn["id"] = str(uuid.uuid4())

    result = await process_failed_payment(txn)
    diag = result.get("diagnosis") or {}
    plan = result.get("plan") or {}
    verify = result.get("verification") or {}
    return JSONResponse(content={
        "status": result.get("status", "processed"),
        "transaction_id": txn["id"],
        "diagnosis": diag.get("failure_type"),
        "intervention": plan.get("intervention_type"),
        "recovered": verify.get("recovered", False),
    })


@app.post("/api/simulate/batch")
async def simulate_batch(count: int = Query(default=50, le=500)):
    """
    Simulate a batch of failed payments from the synthetic dataset.
    Processes them through the full pipeline with live WebSocket updates.
    """
    data_path = os.path.join(os.path.dirname(__file__), "..", "data", "synthetic_transactions.json")
    if not os.path.exists(data_path):
        raise HTTPException(404, "Synthetic dataset not found. Run: python -m app.data.generator")

    with open(data_path, "r") as f:
        all_txns = json.load(f)

    failed = [t for t in all_txns if t.get("status") == "failed"][:count]
    results = []

    for i, txn in enumerate(failed):
        result = await process_failed_payment(txn)
        results.append({
            "index": i,
            "transaction_id": txn.get("id", ""),
            "failure_type": result.get("diagnosis", {}).get("failure_type", ""),
            "intervention": result.get("plan", {}).get("intervention_type", ""),
            "recovered": result.get("verification", {}).get("recovered", False),
            "amount": txn.get("amount", 0) / 100,
        })

        # Small delay for WebSocket visual effect
        if ws_clients:
            await asyncio.sleep(0.05)

    return {
        "processed": len(results),
        "recovered": sum(1 for r in results if r["recovered"]),
        "metrics": get_live_metrics(),
        "results": results,
    }


@app.post("/api/approve")
async def approve_intervention(req: ApprovalRequest):
    """Human approval for high-value recovery actions."""
    for record in recovery_pipeline:
        if record.get("plan", {}).get("id") == req.intervention_id:
            record["plan"]["approved"] = req.approved
            record["plan"]["approved_by"] = req.approved_by
            return {"status": "approved" if req.approved else "rejected"}
    raise HTTPException(404, "Intervention not found")


@app.post("/api/emergency-stop")
async def emergency_stop(halt: bool = True, merchant_id: Optional[str] = None):
    """Emergency stop — global or per-merchant."""
    if merchant_id:
        safety_guard.set_merchant_halt(merchant_id, halt)
        await broadcast("emergency_stop", {"merchant_id": merchant_id, "halted": halt})
        return {"status": "merchant_halted" if halt else "merchant_released", "merchant_id": merchant_id}
    else:
        safety_guard.set_global_halt(halt)
        await broadcast("emergency_stop", {"global": True, "halted": halt})
        return {"status": "global_halt" if halt else "global_release"}


@app.get("/api/metrics")
async def get_metrics():
    """Get current system metrics."""
    return get_live_metrics()


@app.get("/api/dashboard")
async def dashboard():
    """Get full dashboard data."""
    return get_dashboard_data()


@app.get("/api/audit")
async def get_audit(limit: int = 100):
    """Get audit log entries."""
    return {"entries": audit_log[-limit:], "total": len(audit_log)}


@app.get("/api/pipeline")
async def get_pipeline(limit: int = 50):
    """Get pipeline execution records."""
    records = recovery_pipeline[-limit:]
    return {
        "records": [
            {
                "id": r.get("id", ""),
                "status": r.get("status", ""),
                "transaction_id": r.get("transaction", {}).get("id", ""),
                "amount": r.get("transaction", {}).get("amount", 0) / 100,
                "merchant": r.get("transaction", {}).get("merchant_id", ""),
                "failure_type": r.get("diagnosis", {}).get("failure_type", ""),
                "confidence": r.get("diagnosis", {}).get("confidence", 0),
                "intervention": r.get("plan", {}).get("intervention_type", ""),
                "recovered": r.get("verification", {}).get("recovered", False),
                "recovered_amount": r.get("verification", {}).get("recovered_amount_rupees", 0),
                "reasoning": r.get("diagnosis", {}).get("reasoning", ""),
                "created_at": r.get("created_at", 0),
            }
            for r in records
        ],
        "total": len(recovery_pipeline),
    }


@app.get("/api/strategy-stats")
async def get_strategy_stats():
    """Get bandit strategy statistics."""
    return strategy_agent.get_stats()


@app.post("/api/train")
async def train_model():
    """Train the XGBoost diagnosis model on synthetic data."""
    data_path = os.path.join(os.path.dirname(__file__), "..", "data", "synthetic_transactions.json")
    if not os.path.exists(data_path):
        raise HTTPException(404, "Generate data first: python -m app.data.generator")

    with open(data_path, "r") as f:
        transactions = json.load(f)

    result = diagnosis_agent.train_model(transactions)
    return {"status": "trained", "result": result}


# ── Helper Functions ────────────────────────────────────────────────────────

def get_live_metrics() -> dict:
    """Get current live metrics."""
    total = max(1, metrics["total_failed"])
    return {
        "total_processed": metrics["total_processed"],
        "total_failed": metrics["total_failed"],
        "total_recovered": metrics["total_recovered"],
        "total_recovered_amount_rupees": metrics["total_recovered_amount"] / 100,
        "recovery_rate": round(metrics["total_recovered"] / total, 4),
        "total_skipped": metrics["total_skipped"],
        "uptime_seconds": round(time.time() - metrics["start_time"], 0),
        "verification_stats": verification_agent.get_stats(),
    }


def get_dashboard_data() -> dict:
    """Get comprehensive dashboard data."""
    # Failure type distribution
    failure_dist = defaultdict(int)
    intervention_dist = defaultdict(int)
    recovery_by_type = defaultdict(lambda: {"attempted": 0, "recovered": 0})

    for record in recovery_pipeline:
        ft = record.get("diagnosis", {}).get("failure_type", "unknown")
        it = record.get("plan", {}).get("intervention_type", "unknown")
        failure_dist[ft] += 1
        intervention_dist[it] += 1

        if it not in ("do_nothing", "human_escalation"):
            recovery_by_type[ft]["attempted"] += 1
            if record.get("verification", {}).get("recovered"):
                recovery_by_type[ft]["recovered"] += 1

    return {
        "metrics": get_live_metrics(),
        "failure_distribution": dict(failure_dist),
        "intervention_distribution": dict(intervention_dist),
        "recovery_by_type": dict(recovery_by_type),
        "recent_pipeline": [
            {
                "id": r.get("id", "")[:8],
                "status": r.get("status", ""),
                "failure_type": r.get("diagnosis", {}).get("failure_type", ""),
                "intervention": r.get("plan", {}).get("intervention_type", ""),
                "recovered": r.get("verification", {}).get("recovered", False),
                "amount": r.get("transaction", {}).get("amount", 0) / 100,
                "confidence": r.get("diagnosis", {}).get("confidence", 0),
                "merchant": r.get("transaction", {}).get("merchant_id", ""),
            }
            for r in recovery_pipeline[-20:]
        ],
        "safety": {
            "global_halt": safety_guard.check_global_halt(),
            "merchant_halts": dict(safety_guard.merchant_halts),
        },
    }


# ── Run ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
