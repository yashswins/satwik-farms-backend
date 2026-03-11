# Satwik Farms — Order API

> FastAPI backend that bridges the Satwik Farms Android app with Accu360 (Frappe/ERPNext ERP).

**Deployed on:** Render.com

---

## What it does

The Android app cannot talk directly to Accu360 — it requires multi-step server-side logic (customer lookup, address creation, Sales Order submission). This service sits in between:

1. Receives an order from the Android app
2. Saves it locally with status `queued`
3. Looks up or creates the customer in Accu360 by phone number
4. Creates a shipping address in Accu360
5. Submits a Sales Order to the Accu360 Frappe API
6. Updates the local record with the Accu360 order ID and status `pending`
7. Receives webhook callbacks from Accu360 to sync order status

## Tech Stack

| | |
|---|---|
| Framework | FastAPI 0.109 |
| Server | Uvicorn |
| Database | SQLite via SQLAlchemy 2.0 |
| HTTP client | httpx (async) |
| Validation | Pydantic v2 |
| Deployment | Render.com (blueprint via `render.yaml`) |

## API Endpoints

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/health` | None | Health check |
| `POST` | `/orders` | `X-API-Key` | Submit order from app |
| `GET` | `/orders/{id}` | `X-API-Key` | Get order status |
| `POST` | `/webhooks/accu360` | Webhook secret | Receive ERP status updates |

Two API keys are configured — one for debug builds, one for release — so they can be rotated independently.

## Architecture

Single-file service (`main.py`). Accu360 submission runs as a **background task** so the app gets an immediate `202 Accepted` response.

```
Android App
    │  POST /orders  (X-API-Key)
    ▼
FastAPI  (main.py)
    ├── Save order locally  →  status: queued
    └── Background task:
          ├── GET /customer?phone=  →  create if missing
          ├── POST /address
          └── POST /sales-order  →  status: pending

Accu360 Webhook
    └── POST /webhooks/accu360  →  sync final status
```

## Running Locally

```bash
cp .env.example .env        # fill in Accu360 + API key values
pip install -r requirements.txt
python main.py
```

## Deployment

Push to GitHub and connect to Render — the `render.yaml` blueprint handles service configuration. Set environment variables in the Render dashboard.
