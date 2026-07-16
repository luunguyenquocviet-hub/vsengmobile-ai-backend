# controllers/order_controller.py
"""
ĐẶT HÀNG + THANH TOÁN (COD / Chuyển khoản QR VietQR)

Luồng COD     : POST /orders → tạo đơn 'cho_xac_nhan', thanh toán khi nhận hàng.
Luồng QR bank : POST /orders (payment_method='bank_qr') → trả thông tin QR VietQR
                (đúng số tiền + nội dung DH{order_id}) → khách quét chuyển khoản →
                xác nhận tiền vào bằng 1 trong 2 cách:
                  - Tự động : trang checkout poll GET /payment/bank/check/{id}
                              (backend hỏi SePay — chỉ quyền ĐỌC biến động số dư)
                  - Thủ công: admin bấm "Đã nhận tiền" trên trang quản lý
Giá LUÔN lấy từ MySQL products (không tin giá client gửi lên) → chống sửa giá.
"""
import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from typing import List, Literal, Optional

from config.database import get_mysql
from utils import bankqr

router = APIRouter()

TAG_ORDER = "🛒 Đặt hàng & Thanh toán"


# ==========================================
# Schemas
# ==========================================
class OrderItemIn(BaseModel):
    item_id: int
    quantity: int = Field(default=1, ge=1, le=50)


class OrderCreate(BaseModel):
    user_id: int
    receiver_name: str = Field(min_length=2, max_length=100)
    receiver_phone: str = Field(min_length=8, max_length=15)
    shipping_address: str = Field(min_length=5, max_length=255)
    note: Optional[str] = Field(default=None, max_length=255)
    payment_method: Literal["cod", "bank_qr"] = "cod"
    items: List[OrderItemIn] = Field(min_length=1)


# ==========================================
# 🛒 TẠO ĐƠN HÀNG (COD hoặc Chuyển khoản QR)
# ==========================================
@router.post("/orders", tags=[TAG_ORDER])
def create_order(data: OrderCreate):
    conn = get_mysql()
    try:
        conn.start_transaction()
        cur = conn.cursor(dictionary=True)

        # 1) User phải tồn tại
        cur.execute("SELECT user_id FROM users WHERE user_id=%s", (data.user_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail=f"user_id {data.user_id} không tồn tại")

        # 2) Gộp item trùng, lấy giá + tồn kho THẬT từ DB
        qty_map: dict[int, int] = {}
        for it in data.items:
            qty_map[it.item_id] = qty_map.get(it.item_id, 0) + it.quantity

        placeholders = ",".join(["%s"] * len(qty_map))
        cur.execute(f"""SELECT item_id, name, price, stock FROM products
                        WHERE item_id IN ({placeholders}) FOR UPDATE""", list(qty_map.keys()))
        products = {int(p["item_id"]): p for p in cur.fetchall()}

        total = 0
        order_items = []
        for item_id, qty in qty_map.items():
            p = products.get(item_id)
            if not p:
                raise HTTPException(status_code=404, detail=f"Sản phẩm {item_id} không tồn tại")
            if int(p["stock"] or 0) < qty:
                raise HTTPException(status_code=400,
                                    detail=f"'{p['name'][:50]}...' chỉ còn {p['stock']} máy (cần {qty})")
            price = int(float(p["price"] or 0))
            total += price * qty
            order_items.append((item_id, p["name"], price, qty))

        # 3) Ghi đơn + chi tiết, trừ kho ngay khi đặt (hủy đơn sẽ hoàn lại)
        cur.execute("""INSERT INTO orders (user_id, receiver_name, receiver_phone,
                       shipping_address, note, total_amount, payment_method)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (data.user_id, data.receiver_name.strip(), data.receiver_phone.strip(),
                     data.shipping_address.strip(), data.note, total, data.payment_method))
        order_id = cur.lastrowid

        for item_id, name, price, qty in order_items:
            cur.execute("""INSERT INTO order_items (order_id, item_id, product_name, price, quantity)
                           VALUES (%s,%s,%s,%s,%s)""", (order_id, item_id, name, price, qty))
            cur.execute("UPDATE products SET stock = stock - %s WHERE item_id=%s", (qty, item_id))

        # 4) Chuyển khoản QR → trả thông tin QR VietQR + nội dung CK để trang checkout hiển thị
        bank_transfer = None
        if data.payment_method == "bank_qr":
            try:
                cfg = bankqr.bank_config()
                bank_transfer = {
                    "qr_url": bankqr.build_qr_url(order_id, total),
                    "bank_id": cfg["bank_id"], "account_no": cfg["account_no"],
                    "account_name": cfg["account_name"],
                    "transfer_content": bankqr.transfer_content(order_id),
                    "amount": total,
                    "auto_check": bankqr.sepay_configured(),
                }
            except RuntimeError as e:  # chưa cấu hình tài khoản nhận tiền → báo rõ, rollback đơn
                raise HTTPException(status_code=503, detail=str(e))

        conn.commit()
        return {"status": "success", "order_id": order_id, "total_amount": total,
                "payment_method": data.payment_method,
                "order_status": "cho_xac_nhan", "bank_transfer": bank_transfer}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Lỗi tạo đơn: {e}")
    finally:
        conn.close()


# ==========================================
# 🔎 XEM ĐƠN HÀNG (khách tra cứu)
# ==========================================
@router.get("/orders/{order_id}", tags=[TAG_ORDER])
def get_order(order_id: int):
    conn = get_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""SELECT o.*, u.user_name FROM orders o
                       LEFT JOIN users u ON u.user_id = o.user_id
                       WHERE o.order_id=%s""", (order_id,))
        order = cur.fetchone()
        if not order:
            raise HTTPException(status_code=404, detail="Đơn hàng không tồn tại")
        cur.execute("""SELECT oi.item_id, oi.product_name, oi.price, oi.quantity,
                              p.product_image_link AS image
                       FROM order_items oi
                       LEFT JOIN products p ON p.item_id = oi.item_id
                       WHERE oi.order_id=%s""", (order_id,))
        order["items"] = cur.fetchall()
        order["total_amount"] = int(order["total_amount"] or 0)
        for it in order["items"]:
            it["price"] = int(it["price"] or 0)
        return {"status": "success", "order": order}
    finally:
        conn.close()


# ==========================================
# 🏦 CHUYỂN KHOẢN QR — kiểm tra tiền vào + webhook SePay
# ==========================================
def _mark_bank_paid(order_id: int) -> bool:
    """Đánh dấu đơn bank_qr đã thanh toán (idempotent). True nếu có cập nhật."""
    conn = get_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("UPDATE orders SET payment_status='paid' "
                    "WHERE order_id=%s AND payment_status<>'paid'", (order_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


@router.get("/payment/bank/check/{order_id}", tags=[TAG_ORDER])
def check_bank_payment(order_id: int):
    """Trang checkout gọi lặp lại (poll) sau khi hiện QR: hỏi SePay xem tiền đã vào chưa.
    Chưa cấu hình SEPAY_API_TOKEN → auto_check=false (chờ admin xác nhận thủ công)."""
    conn = get_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT order_id, total_amount, payment_method, payment_status "
                    "FROM orders WHERE order_id=%s", (order_id,))
        order = cur.fetchone()
    finally:
        conn.close()

    if not order:
        raise HTTPException(status_code=404, detail="Đơn hàng không tồn tại")
    if order["payment_status"] == "paid":
        return {"paid": True, "source": "db"}
    if order["payment_method"] != "bank_qr":
        raise HTTPException(status_code=400, detail="Đơn này không dùng chuyển khoản QR")
    if not bankqr.sepay_configured():
        return {"paid": False, "auto_check": False,
                "message": "Chưa cấu hình SEPAY_API_TOKEN — admin sẽ xác nhận thủ công khi thấy tiền vào"}

    try:
        txs = bankqr.fetch_recent_transactions(limit=30)
    except Exception as e:  # SePay lỗi/mạng rớt → vẫn cho poll tiếp, không 500
        return {"paid": False, "auto_check": True, "error": f"Không gọi được SePay: {e}"}

    tx = bankqr.find_matching_payment(txs, order_id, int(order["total_amount"]))
    if tx:
        _mark_bank_paid(order_id)
        return {"paid": True, "source": "sepay",
                "transaction": {"id": tx.get("id"), "amount_in": tx.get("amount_in"),
                                "content": tx.get("transaction_content"),
                                "date": tx.get("transaction_date")}}
    return {"paid": False, "auto_check": True}


@router.post("/payment/sepay/webhook", tags=[TAG_ORDER])
async def sepay_webhook(request: Request):
    """SePay bắn webhook khi có biến động số dư (cần URL public — ngrok/production).
    Chạy localhost thuần thì không cần: đã có polling ở /payment/bank/check."""
    body = await request.json()
    if str(body.get("transferType", "")).lower() != "in":
        return {"success": True, "skip": "không phải tiền vào"}

    m = re.search(r"DH\s*(\d+)", str(body.get("content") or ""), re.IGNORECASE)
    if not m:
        return {"success": True, "skip": "nội dung không có mã đơn DHxx"}
    order_id = int(m.group(1))
    amount = float(body.get("transferAmount") or 0)

    conn = get_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT total_amount, payment_method, payment_status FROM orders WHERE order_id=%s",
                    (order_id,))
        order = cur.fetchone()
    finally:
        conn.close()

    if not order or order["payment_method"] != "bank_qr":
        return {"success": True, "skip": f"không có đơn bank_qr #{order_id}"}
    if order["payment_status"] == "paid":
        return {"success": True, "skip": "đơn đã thanh toán trước đó"}
    if amount < float(order["total_amount"]):
        return {"success": True, "skip": "số tiền chưa đủ so với tổng đơn"}

    _mark_bank_paid(order_id)
    return {"success": True, "order_id": order_id, "marked": "paid"}
