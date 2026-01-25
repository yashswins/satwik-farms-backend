# Satwik Farms Backend

FastAPI backend for Satwik Farms Android app - handles order submission to Accu360.

## Setup

1. Copy `.env.example` to `.env` and fill in values
2. Install dependencies: `pip install -r requirements.txt`
3. Run locally: `python main.py`

## Deployment (Render.com)

1. Push to GitHub
2. Connect repo in Render Dashboard
3. Deploy using Blueprint (render.yaml)
4. Configure environment variables (API Key and Secret from Accu360)

## Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/orders` | POST | Create order |
| `/orders/{id}` | GET | Get order details |
| `/webhooks/accu360` | POST | Receive status updates |
