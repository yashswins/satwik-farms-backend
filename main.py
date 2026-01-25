import os
import httpx
from datetime import datetime, timedelta
from typing import Optional
from fastapi import FastAPI, HTTPException, Header, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, String, Integer, Float, DateTime, Text, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from dotenv import load_dotenv

load_dotenv()

# Configuration
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./orders.db")
ACCU360_API_KEY = os.getenv("ACCU360_API_KEY")
ACCU360_API_SECRET = os.getenv("ACCU360_API_SECRET")
ACCU360_API_BASE_URL = os.getenv("ACCU360_API_BASE_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "your-webhook-secret")

# Database Setup
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Order(Base):
    __tablename__ = "orders"
    id = Column(String, primary_key=True)
    accu360_order_id = Column(String, nullable=True)
    status = Column(String, default="pending")
    customer_name = Column(String)
    customer_phone = Column(String)
    customer_address = Column(Text)
    items = Column(JSON)
    subtotal = Column(Float)
    delivery_fee = Column(Float)
    total = Column(Float)
    delivery_notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

Base.metadata.create_all(bind=engine)

# Pydantic Models
class OrderItem(BaseModel):
    product_id: str
    accu360_sku: str
    name: str
    quantity: int
    unit_price: float
    total_price: float

class CreateOrderRequest(BaseModel):
    customer_name: str
    customer_phone: str
    customer_address: str
    items: list[OrderItem]
    subtotal: float
    delivery_fee: float
    total: float
    delivery_notes: Optional[str] = None

class WebhookPayload(BaseModel):
    event: str
    order_id: str
    status: str
    timestamp: str

# FastAPI App
app = FastAPI(title="Satwik Farms Backend", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def generate_order_id():
    now = datetime.utcnow()
    import random
    return f"SF-{now.strftime('%Y%m%d')}-{random.randint(10000, 99999)}"

def get_accu360_auth_header() -> dict:
    """Get Accu360 API authentication header"""
    if not ACCU360_API_KEY or not ACCU360_API_SECRET:
        raise HTTPException(status_code=500, detail="Accu360 API credentials not configured")

    return {
        "Authorization": f"token {ACCU360_API_KEY}:{ACCU360_API_SECRET}",
        "Content-Type": "application/json"
    }

async def find_or_create_customer(
    customer_name: str,
    customer_phone: str,
    customer_address: str
) -> str:
    """Find existing customer by phone or create new one. Returns customer name for Sales Order."""

    auth_headers = get_accu360_auth_header()

    async with httpx.AsyncClient() as client:
        # Search for customer by phone number
        search_filters = f'[["mobile_no","like","%{customer_phone[-9:]}%"]]'
        search_url = f"{ACCU360_API_BASE_URL}/api/resource/Customer?filters={search_filters}&fields=[\"name\",\"customer_name\",\"mobile_no\"]"

        response = await client.get(
            search_url,
            headers=auth_headers
        )

        if response.status_code == 200:
            data = response.json()
            customers = data.get("data", [])
            if customers:
                # Found existing customer - return the "name" field (customer ID)
                return customers[0]["name"]

        # Customer not found - create new one
        new_customer = {
            "doctype": "Customer",
            "customer_name": customer_name,
            "customer_type": "Individual",
            "customer_group": "Individual",  # Adjust if your system uses different groups
            "territory": "All Territories",  # Adjust to your territory
            "mobile_no": customer_phone,
        }

        create_response = await client.post(
            f"{ACCU360_API_BASE_URL}/api/resource/Customer",
            headers=auth_headers,
            json=new_customer
        )

        if create_response.status_code in [200, 201]:
            created = create_response.json()
            # Return the new customer's name (ID)
            return created.get("data", {}).get("name", customer_name)
        else:
            # If customer creation fails, try using customer_name directly
            # (in case it matches an existing customer)
            return customer_name

# Endpoints
@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "accu360_configured": bool(ACCU360_API_KEY and ACCU360_API_SECRET)
    }

@app.post("/orders")
async def create_order(request: CreateOrderRequest, db: Session = Depends(get_db)):
    """Create order and submit to Accu360"""

    # Validate SKUs
    missing_skus = [item.product_id for item in request.items if not item.accu360_sku]
    if missing_skus:
        raise HTTPException(
            status_code=400,
            detail=f"Missing accu360_sku for products: {', '.join(missing_skus)}"
        )

    # Generate order ID
    order_id = generate_order_id()

    # Find or create customer in Accu360
    customer_id = await find_or_create_customer(
        customer_name=request.customer_name,
        customer_phone=request.customer_phone,
        customer_address=request.customer_address
    )

    # Build Frappe Sales Order payload
    accu360_payload = {
        "doctype": "Sales Order",
        "customer": customer_id,  # Links to Customer record in Accu360
        "delivery_date": (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d"),
        "po_no": order_id,  # External reference
        "items": [
            {
                "item_code": item.accu360_sku,
                "qty": item.quantity,
                "rate": item.unit_price,
                "delivery_date": (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")
            }
            for item in request.items
        ],
        "contact_phone": request.customer_phone,
        "shipping_address_name": request.customer_address,
        "instructions": request.delivery_notes or ""
    }

    # Submit to Accu360 (Frappe API)
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{ACCU360_API_BASE_URL}/api/resource/Sales Order",
            headers=get_accu360_auth_header(),
            json=accu360_payload
        )

        if response.status_code not in [200, 201]:
            # Store order as failed
            db_order = Order(
                id=order_id,
                status="failed",
                customer_name=request.customer_name,
                customer_phone=request.customer_phone,
                customer_address=request.customer_address,
                items=[item.model_dump() for item in request.items],
                subtotal=request.subtotal,
                delivery_fee=request.delivery_fee,
                total=request.total,
                delivery_notes=request.delivery_notes
            )
            db.add(db_order)
            db.commit()

            error_detail = response.json().get("error", "Unknown error")
            raise HTTPException(status_code=502, detail=f"Accu360 error: {error_detail}")

        accu360_data = response.json()
        # Frappe returns {"data": {"name": "SAL-ORD-XXXXX", ...}}
        accu360_order_id = accu360_data.get("data", {}).get("name", order_id)

        # Store order
        db_order = Order(
            id=order_id,
            accu360_order_id=accu360_order_id,
            status="pending",
            customer_name=request.customer_name,
            customer_phone=request.customer_phone,
            customer_address=request.customer_address,
            items=[item.model_dump() for item in request.items],
            subtotal=request.subtotal,
            delivery_fee=request.delivery_fee,
            total=request.total,
            delivery_notes=request.delivery_notes
        )
        db.add(db_order)
        db.commit()

        return {
            "order_id": order_id,
            "accu360_order_id": accu360_order_id,
            "status": "pending",
            "message": "Order submitted successfully",
            "created_at": db_order.created_at.isoformat()
        }

@app.get("/orders/{order_id}")
async def get_order(order_id: str, db: Session = Depends(get_db)):
    """Get order details"""
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    return {
        "order_id": order.id,
        "accu360_order_id": order.accu360_order_id,
        "status": order.status,
        "customer": {
            "name": order.customer_name,
            "phone": order.customer_phone,
            "address": order.customer_address
        },
        "items": order.items,
        "subtotal": order.subtotal,
        "delivery_fee": order.delivery_fee,
        "total": order.total,
        "delivery_notes": order.delivery_notes,
        "created_at": order.created_at.isoformat(),
        "updated_at": order.updated_at.isoformat()
    }

@app.post("/webhooks/accu360")
async def accu360_webhook(
    payload: WebhookPayload,
    x_accu360_signature: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Handle status updates from Accu360"""

    # Find order by accu360_order_id
    order = db.query(Order).filter(Order.accu360_order_id == payload.order_id).first()
    if not order:
        # Try by our order_id
        order = db.query(Order).filter(Order.id == payload.order_id).first()

    if order:
        order.status = payload.status
        order.updated_at = datetime.utcnow()
        db.commit()

    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
