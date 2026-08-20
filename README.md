# ReviveAI 

**Built for the Razorpay AI Buildathon 2026 (Track 03: AI Revenue Recovery)**

ReviveAI is our multi-agent AI system designed to tackle a massive problem for merchants: cart abandonment due to payment failures. Instead of blindly retrying a payment and hoping for the best, ReviveAI diagnoses *why* a Razorpay payment failed, selects the best recovery strategy, executes the action via Razorpay APIs, and verifies the outcome. 

In our testing, it recovers 3x more revenue than blind retries.

## 🧠 How it Works

We built four distinct AI agents to handle the pipeline:

1. **Diagnosis Agent:** Uses XGBoost to classify the failure root cause from 15+ signals (like bank timeouts, insufficient funds, etc.).
2. **Strategy Agent:** Uses a Contextual Bandit (Thompson Sampling) to figure out the best way to get the money (e.g. should we send a WhatsApp link? An email? Or just retry?).
3. **Execution Agent:** Actually generates the Razorpay Payment Links and calls the Razorpay APIs using test keys.
4. **Verification Agent:** Confirms the recovery and logs the audit trail.

## 🚀 Quick Start

### Prerequisites
- Python 3.11+
- Node.js 20+
- PostgreSQL 15+ (or SQLite for local dev)

### Backend Setup
```bash
cd backend
python -m venv venv
venv\Scripts\activate     # Windows
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

### Frontend Setup
```bash
cd frontend
npm install
npm run dev
```

### Generate Synthetic Data
```bash
cd backend
python -m app.data.generator
```

### Run Evaluation
```bash
cd backend
python -m app.evaluation.run
```

## 📊 Evaluation Results

| Metric | Baseline (Blind Retry) | ReviveAI |
|:---|:---|:---|
| Recovery Rate (all failures) | ~12% | **>35%** |
| Recovery Rate (recoverable) | ~20% | **>55%** |
| Root-cause F1 | N/A | **>0.85** |
| False Recovery Attempts | 100% | **<5%** |
| Revenue Recovered Multiplier | 1x | **>3x** |

## 🔒 Safety & Guardrails

- Confidence thresholds at every decision point
- Human approval for high-value recoveries (>₹10,000)
- Idempotency keys on all API calls
- Emergency stop (global + per-merchant)
- Immutable audit trail with full reasoning chain
- LLM NEVER makes financial decisions — only generates explanations

## 📁 Project Structure

```
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI application
│   │   ├── config.py            # Configuration
│   │   ├── models/              # Database models
│   │   ├── agents/              # AI Agents
│   │   │   ├── diagnosis.py     # Diagnosis Agent (XGBoost)
│   │   │   ├── strategy.py      # Strategy Agent (Bandit)
│   │   │   ├── execution.py     # Execution Agent (API calls)
│   │   │   └── verification.py  # Verification Agent
│   │   ├── razorpay/            # Razorpay API integration
│   │   ├── data/                # Synthetic data generation
│   │   ├── evaluation/          # Evaluation pipeline
│   │   └── safety/              # Guardrails & safety
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.jsx
│   │   ├── components/          # Dashboard components
│   │   └── index.css
│   └── package.json
└── README.md
```

## 🏗️ Built With

- **Backend**: Python, FastAPI, XGBoost, scikit-learn
- **Frontend**: React, Vite, vanilla CSS
- **Database**: SQLite (dev) / PostgreSQL (prod)
- **AI**: XGBoost, Thompson Sampling Bandits, LLM (GPT-4o-mini)
- **Payment**: Razorpay Test Mode APIs

## 📝 License

MIT — Built for Razorpay AI Buildathon 2026
