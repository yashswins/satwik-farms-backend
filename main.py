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
ACCU360_DEFAULT_CITY = os.getenv("ACCU360_DEFAULT_CITY")
ACCU360_DEFAULT_PROVINCE = os.getenv("ACCU360_DEFAULT_PROVINCE")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "your-webhook-secret")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# API Authentication - Add these keys from your Android app
VALID_API_KEYS = [
    os.getenv("APP_API_KEY_DEBUG", "fa2582e2af1c20de90daf3e7fbfde118bd580c99c747e4de5d9556282dc77f59"),
    os.getenv("APP_API_KEY_RELEASE", "49f8ba687c8af783d5307e314421da6e2f84d0ac961339bc75cb4fee5d6b487c"),
]

# Database Setup
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,   # verify connection health before use (prevents stale SSL errors)
    pool_recycle=300,     # recycle connections every 5 min to avoid idle timeouts
    pool_size=5,          # base concurrent orders
    max_overflow=5,       # burst up to 10 total connections under peak load
)
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
    discount: Optional[float] = 0.0
    promo_code: Optional[str] = None

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

async def verify_api_key(x_api_key: str = Header(None)):
    """Verify API key from X-API-Key header"""
    if not x_api_key:
        raise HTTPException(
            status_code=401,
            detail="Missing API key. Include X-API-Key header."
        )

    if x_api_key not in VALID_API_KEYS:
        raise HTTPException(
            status_code=401,
            detail="Invalid API key"
        )

    return x_api_key

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
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

def safe_response_json(response: httpx.Response) -> dict:
    try:
        return response.json()
    except ValueError:
        return {}

# async def send_telegram(message: str) -> None:
#     """Send a Telegram notification to the shop owner. Never raises."""
#     if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
#         return
#     try:
#         async with httpx.AsyncClient(timeout=10.0) as client:
#             await client.post(
#                 f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
#                 json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
#             )
#     except Exception as e:
#         print(f"WARNING: Telegram send failed: {e}")

def save_order_to_db(
    db: Session,
    order_id: str,
    accu360_order_id: Optional[str],
    status: str,
    request: "CreateOrderRequest",
) -> None:
    """Upsert order in local DB — never raises."""
    try:
        existing = db.query(Order).filter(Order.id == order_id).first()
        if existing:
            existing.status = status
            if accu360_order_id:
                existing.accu360_order_id = accu360_order_id
            db.commit()
        else:
            db.add(Order(
                id=order_id,
                accu360_order_id=accu360_order_id,
                status=status,
                customer_name=request.customer_name,
                customer_phone=request.customer_phone,
                customer_address=request.customer_address,
                items=[item.model_dump() for item in request.items],
                subtotal=request.subtotal,
                delivery_fee=request.delivery_fee,
                total=request.total,
                delivery_notes=request.delivery_notes,
            ))
            db.commit()
    except Exception as db_err:
        print(f"WARNING: DB save failed for order {order_id}: {db_err}")
        try:
            db.rollback()
        except Exception:
            pass

async def find_or_create_customer(
    customer_name: str,
    customer_phone: str,
    customer_address: str
) -> str:
    """Find existing customer by phone or create new one. Returns customer name for Sales Order."""

    auth_headers = get_accu360_auth_header()

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Search for customer by phone number
        search_filters = f'[["mobile_no","like","%{customer_phone[-9:]}%"]]'
        search_url = f"{ACCU360_API_BASE_URL}/api/resource/Customer?filters={search_filters}&fields=[\"name\",\"customer_name\",\"mobile_no\"]"

        response = await client.get(
            search_url,
            headers=auth_headers
        )

        if response.status_code == 200:
            data = safe_response_json(response)
            customers = data.get("data", [])
            if customers:
                # Found existing customer - return the "name" field (customer ID)
                return customers[0].get("name", customer_name)

        # Customer not found - create new one
        new_customer = {
            "doctype": "Customer",
            "customer_name": customer_name,
            "customer_type": "Individual",
            "customer_group": "Individual",  # Adjust if your system uses different groups
            "territory": "All Territories",  # Adjust to your territory
            "mobile_no": customer_phone,
            "customer_full_name": customer_name,
            "mobile_number": customer_phone,
        }

        create_response = await client.post(
            f"{ACCU360_API_BASE_URL}/api/resource/Customer",
            headers=auth_headers,
            json=new_customer
        )

        if create_response.status_code in [200, 201]:
            created = safe_response_json(create_response)
            # Return the new customer's name (ID)
            return created.get("data", {}).get("name", customer_name)
        else:
            # If customer creation fails, try using customer_name directly
            # (in case it matches an existing customer)
            return customer_name

async def sync_customer_fields(
    customer_id: str,
    customer_name: str,
    customer_phone: str
) -> None:
    auth_headers = get_accu360_auth_header()

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{ACCU360_API_BASE_URL}/api/resource/Customer/{customer_id}"
            "?fields=[\"name\",\"customer_name\",\"mobile_no\",\"mobile_number\",\"customer_full_name\"]",
            headers=auth_headers
        )

        if response.status_code != 200:
            return

        data = safe_response_json(response).get("data", {})
        current_mobile_number = (data.get("mobile_number") or "").strip()
        current_mobile_no = (data.get("mobile_no") or "").strip()
        current_full_name = (data.get("customer_full_name") or "").strip()
        current_customer_name = (data.get("customer_name") or "").strip()

        should_update = (
            not current_full_name
            or not current_mobile_number
            or current_mobile_number == current_customer_name
            or current_mobile_number == current_full_name
        )

        if not should_update:
            return

        update_payload = {
            "customer_full_name": customer_name,
            "mobile_number": customer_phone,
            "mobile_no": customer_phone,
            "customer_name": customer_name
        }

        await client.put(
            f"{ACCU360_API_BASE_URL}/api/resource/Customer/{customer_id}",
            headers=auth_headers,
            json=update_payload
        )

async def create_shipping_address(
    customer_id: str,
    customer_name: str,
    customer_phone: str,
    customer_address: str
) -> str:
    if not ACCU360_DEFAULT_CITY or not ACCU360_DEFAULT_PROVINCE:
        raise HTTPException(
            status_code=500,
            detail="Accu360 address defaults not configured (city/province)"
        )

    auth_headers = get_accu360_auth_header()
    address_payload = {
        "doctype": "Address",
        "address_title": customer_name,
        "address_type": "Shipping",
        "address_line1": customer_address,
        "city": ACCU360_DEFAULT_CITY,
        "province": ACCU360_DEFAULT_PROVINCE,
        "phone": customer_phone,
        "links": [
            {
                "link_doctype": "Customer",
                "link_name": customer_id
            }
        ]
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{ACCU360_API_BASE_URL}/api/resource/Address",
            headers=auth_headers,
            json=address_payload
        )

        if response.status_code in [200, 201]:
            created = safe_response_json(response)
            address_name = (
                created.get("data", {}).get("name")
                or created.get("name")
            )
            if address_name:
                return address_name

        error_data = safe_response_json(response)
        error_detail = (
            error_data.get("error")
            or error_data.get("message")
            or error_data.get("detail")
        )
        if not error_detail:
            text = response.text.strip()
            error_detail = text if text else "Empty response from Accu360"
        raise HTTPException(status_code=502, detail=f"Accu360 error: {error_detail}")

# Endpoints
@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "accu360_configured": bool(ACCU360_API_KEY and ACCU360_API_SECRET)
    }

@app.post("/orders")
async def create_order(
    request: CreateOrderRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_api_key)
):
    """Create order and submit to Accu360"""

    # Validate SKUs
    missing_skus = [item.product_id for item in request.items if not item.accu360_sku]
    if missing_skus:
        raise HTTPException(
            status_code=400,
            detail=f"Missing accu360_sku for products: {', '.join(missing_skus)}"
        )

    # Generate order ID and save to DB immediately — order is never lost even if Accu360 fails
    order_id = generate_order_id()
    save_order_to_db(db, order_id, None, "queued", request)

    items_summary = "\n".join(
        f"  • {item.name} x{item.quantity} @ TSH {item.unit_price:,.0f}"
        for item in request.items
    )

    try:
        # Find or create customer in Accu360
        customer_id = await find_or_create_customer(
            customer_name=request.customer_name,
            customer_phone=request.customer_phone,
            customer_address=request.customer_address
        )
        await sync_customer_fields(
            customer_id=customer_id,
            customer_name=request.customer_name,
            customer_phone=request.customer_phone
        )
        shipping_address_name = await create_shipping_address(
            customer_id=customer_id,
            customer_name=request.customer_name,
            customer_phone=request.customer_phone,
            customer_address=request.customer_address
        )

        # Build Frappe Sales Order payload
        discount = request.discount or 0.0
        accu360_payload = {
            "doctype": "Sales Order",
            "customer": customer_id,
            "delivery_date": (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d"),
            "po_no": order_id,
            "customer_address": shipping_address_name,
            "shipping_address_name": shipping_address_name,
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
            "instructions": request.delivery_notes or ""
        }
        if discount > 0:
            accu360_payload["apply_discount_on"] = "Grand Total"
            accu360_payload["discount_amount"] = discount
        if request.promo_code:
            accu360_payload["coupon_code"] = request.promo_code

        # Submit to Accu360 (Frappe API)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{ACCU360_API_BASE_URL}/api/resource/Sales Order",
                headers=get_accu360_auth_header(),
                json=accu360_payload
            )

        if response.status_code not in [200, 201]:
            error_data = safe_response_json(response)
            error_detail = (
                error_data.get("error")
                or error_data.get("message")
                or error_data.get("detail")
                or response.text.strip()
                or "Accu360 rejected the order"
            )
            raise HTTPException(status_code=502, detail=f"Accu360 error: {error_detail}")

        accu360_data = safe_response_json(response)
        if not accu360_data:
            raise HTTPException(status_code=502, detail="Accu360 returned empty or invalid response")

        # Frappe returns {"data": {"name": "SAL-ORD-XXXXX", ...}}
        accu360_order_id = accu360_data.get("data", {}).get("name", order_id)

        # Update local DB to pending (non-fatal)
        save_order_to_db(db, order_id, accu360_order_id, "pending", request)

        # background_tasks.add_task(
        #     send_telegram,
        #     f"🛒 <b>New Order Placed</b>\n"
        #     f"<b>Order:</b> {order_id} → {accu360_order_id}\n"
        #     f"<b>Customer:</b> {request.customer_name}\n"
        #     f"<b>Phone:</b> {request.customer_phone}\n"
        #     f"<b>Address:</b> {request.customer_address}\n"
        #     f"<b>Items:</b>\n{items_summary}\n"
        #     f"<b>Total:</b> TSH {request.total:,.0f}"
        # )

        return {
            "success": True,
            "order_id": order_id,
            "accu360_order_id": accu360_order_id,
            "status": "pending",
            "message": "Order submitted successfully",
            "created_at": datetime.utcnow().isoformat()
        }

    except HTTPException as exc:
        # Mark order as failed
        save_order_to_db(db, order_id, None, "failed", request)
        # await send_telegram(
        #     f"❌ <b>Order FAILED — Action Required</b>\n"
        #     f"<b>Order:</b> {order_id}\n"
        #     f"<b>Customer:</b> {request.customer_name}\n"
        #     f"<b>Phone:</b> {request.customer_phone}\n"
        #     f"<b>Address:</b> {request.customer_address}\n"
        #     f"<b>Items:</b>\n{items_summary}\n"
        #     f"<b>Total:</b> TSH {request.total:,.0f}\n"
        #     f"<b>Error:</b> {exc.detail}\n"
        #     f"⚠️ Contact customer and process manually if needed."
        # )
        raise

    except Exception as exc:
        save_order_to_db(db, order_id, None, "failed", request)
        # await send_telegram(
        #     f"🚨 <b>Order Exception</b>\n"
        #     f"<b>Order:</b> {order_id}\n"
        #     f"<b>Customer:</b> {request.customer_name} / {request.customer_phone}\n"
        #     f"<b>Error:</b> {str(exc)[:300]}"
        # )
        raise HTTPException(status_code=500, detail="Internal server error")

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
