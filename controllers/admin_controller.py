# controllers/admin_controller.py
"""
ADMIN — QUẢN LÝ ĐƠN HÀNG (dùng bởi web/admin_orders.html)

Luồng trạng thái hợp lệ (một chiều, không đi lùi):
    cho_xac_nhan ──→ da_xac_nhan ──→ dang_van_chuyen ──→ da_giao
         │                │
         └──→ da_huy ←────┘        (đang vận chuyển/đã giao thì KHÔNG hủy được)

Nghiệp vụ kèm theo khi chuyển:
    - da_huy   : hoàn tồn kho (stock += quantity từng item), lưu lý do hủy
    - da_giao  : cộng sold_quantity, đơn COD tự chuyển payment_status='paid'

⚠️ Đồ án: các endpoint admin chưa gắn xác thực. Khi triển khai thật cần thêm
   đăng nhập admin (JWT/API key) trước các route này.
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Literal, Optional

from config.database import get_mysql

router = APIRouter()
TAG = "🛠️ Admin — Đơn hàng"

ORDER_STATUSES = ("cho_xac_nhan", "da_xac_nhan", "dang_van_chuyen", "da_giao", "da_huy")

# Máy trạng thái: from → các trạng thái được phép chuyển đến
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "cho_xac_nhan":    {"da_xac_nhan", "da_huy"},
    "da_xac_nhan":     {"dang_van_chuyen", "da_huy"},
    "dang_van_chuyen": {"da_giao"},
    "da_giao":         set(),   # trạng thái cuối
    "da_huy":          set(),   # trạng thái cuối
}

STATUS_LABELS = {
    "cho_xac_nhan": "Chờ xác nhận", "da_xac_nhan": "Đã xác nhận",
    "dang_van_chuyen": "Đang vận chuyển", "da_giao": "Đã giao", "da_huy": "Đã hủy",
}


class StatusUpdateIn(BaseModel):
    status: Literal["cho_xac_nhan", "da_xac_nhan", "dang_van_chuyen", "da_giao", "da_huy"]
    cancel_reason: Optional[str] = Field(default=None, max_length=255)


# ==========================================
# 📊 ĐẾM ĐƠN THEO TRẠNG THÁI (badge trên tab)
# ==========================================
@router.get("/admin/orders/stats", tags=[TAG])
def order_stats():
    conn = get_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT status, COUNT(*) AS n FROM orders GROUP BY status")
        counts = {s: 0 for s in ORDER_STATUSES}
        for row in cur.fetchall():
            counts[row["status"]] = row["n"]
        cur.execute("""SELECT COALESCE(SUM(total_amount),0) AS revenue FROM orders
                       WHERE status='da_giao'""")
        revenue = int(cur.fetchone()["revenue"])
        return {"status": "success", "counts": counts,
                "total": sum(counts.values()), "delivered_revenue": revenue}
    finally:
        conn.close()


# ==========================================
# 📋 DANH SÁCH ĐƠN (lọc theo trạng thái + tìm kiếm + phân trang)
# ==========================================
@router.get("/admin/orders", tags=[TAG])
def list_orders(status: Optional[str] = Query(default=None),
                search: Optional[str] = Query(default=None, max_length=100),
                page: int = Query(default=1, ge=1),
                limit: int = Query(default=10, ge=1, le=100)):
    if status and status not in ORDER_STATUSES:
        raise HTTPException(status_code=400, detail=f"status phải thuộc {ORDER_STATUSES}")

    where, params = [], []
    if status:
        where.append("o.status=%s")
        params.append(status)
    if search and search.strip():
        s = f"%{search.strip()}%"
        where.append("(CAST(o.order_id AS CHAR) LIKE %s OR o.receiver_name LIKE %s OR o.receiver_phone LIKE %s)")
        params += [s, s, s]
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    conn = get_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(f"SELECT COUNT(*) AS n FROM orders o {where_sql}", params)
        total = cur.fetchone()["n"]

        cur.execute(f"""
            SELECT o.order_id, o.user_id, u.user_name, o.receiver_name, o.receiver_phone,
                   o.total_amount, o.payment_method, o.payment_status, o.status,
                   o.cancel_reason, o.created_at, o.updated_at,
                   (SELECT COALESCE(SUM(oi.quantity),0) FROM order_items oi
                    WHERE oi.order_id = o.order_id) AS item_count
            FROM orders o
            LEFT JOIN users u ON u.user_id = o.user_id
            {where_sql}
            ORDER BY o.created_at DESC, o.order_id DESC
            LIMIT %s OFFSET %s""", params + [limit, (page - 1) * limit])
        rows = cur.fetchall()
        for r in rows:
            r["total_amount"] = int(r["total_amount"] or 0)
            r["item_count"] = int(r["item_count"] or 0)
            r["status_label"] = STATUS_LABELS.get(r["status"], r["status"])
            r["allowed_next"] = sorted(ALLOWED_TRANSITIONS.get(r["status"], set()))
        return {"status": "success", "total": total, "page": page,
                "pages": max(1, -(-total // limit)), "orders": rows}
    finally:
        conn.close()


# ==========================================
# 🔎 CHI TIẾT 1 ĐƠN (modal trên UI)
# ==========================================
@router.get("/admin/orders/{order_id}", tags=[TAG])
def admin_order_detail(order_id: int):
    conn = get_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""SELECT o.*, u.user_name, u.email FROM orders o
                       LEFT JOIN users u ON u.user_id = o.user_id
                       WHERE o.order_id=%s""", (order_id,))
        order = cur.fetchone()
        if not order:
            raise HTTPException(status_code=404, detail="Đơn hàng không tồn tại")
        cur.execute("""SELECT oi.item_id, oi.product_name, oi.price, oi.quantity,
                              p.product_image_link AS image, p.brand
                       FROM order_items oi
                       LEFT JOIN products p ON p.item_id = oi.item_id
                       WHERE oi.order_id=%s""", (order_id,))
        items = cur.fetchall()
        order["total_amount"] = int(order["total_amount"] or 0)
        for it in items:
            it["price"] = int(it["price"] or 0)
            it["subtotal"] = it["price"] * it["quantity"]
        order["items"] = items
        order["status_label"] = STATUS_LABELS.get(order["status"], order["status"])
        order["allowed_next"] = sorted(ALLOWED_TRANSITIONS.get(order["status"], set()))
        return {"status": "success", "order": order}
    finally:
        conn.close()


# ==========================================
# 🔄 CHUYỂN TRẠNG THÁI ĐƠN
# ==========================================
@router.put("/admin/orders/{order_id}/status", tags=[TAG])
def update_order_status(order_id: int, data: StatusUpdateIn):
    new_status = data.status
    conn = get_mysql()
    try:
        conn.start_transaction()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT order_id, status, payment_method, payment_status FROM orders WHERE order_id=%s FOR UPDATE",
                    (order_id,))
        order = cur.fetchone()
        if not order:
            raise HTTPException(status_code=404, detail="Đơn hàng không tồn tại")

        current = order["status"]
        if new_status == current:
            raise HTTPException(status_code=400, detail=f"Đơn đã ở trạng thái '{STATUS_LABELS[current]}'")
        if new_status not in ALLOWED_TRANSITIONS[current]:
            raise HTTPException(
                status_code=400,
                detail=f"Không thể chuyển '{STATUS_LABELS[current]}' → '{STATUS_LABELS[new_status]}'. "
                       f"Được phép: {', '.join(STATUS_LABELS[s] for s in ALLOWED_TRANSITIONS[current]) or 'không (trạng thái cuối)'}")

        # --- Nghiệp vụ kèm theo ---
        if new_status == "da_huy":
            # Hoàn tồn kho từng sản phẩm trong đơn
            cur.execute("SELECT item_id, quantity FROM order_items WHERE order_id=%s", (order_id,))
            for it in cur.fetchall():
                cur.execute("UPDATE products SET stock = stock + %s WHERE item_id=%s",
                            (it["quantity"], it["item_id"]))
            cur.execute("UPDATE orders SET status=%s, cancel_reason=%s WHERE order_id=%s",
                        (new_status, (data.cancel_reason or "").strip() or None, order_id))
        elif new_status == "da_giao":
            # Cộng lượt bán; COD giao xong = đã thu tiền
            cur.execute("SELECT item_id, quantity FROM order_items WHERE order_id=%s", (order_id,))
            for it in cur.fetchall():
                cur.execute("UPDATE products SET sold_quantity = COALESCE(sold_quantity,0) + %s WHERE item_id=%s",
                            (it["quantity"], it["item_id"]))
            if order["payment_method"] == "cod":
                cur.execute("UPDATE orders SET status=%s, payment_status='paid' WHERE order_id=%s",
                            (new_status, order_id))
            else:
                cur.execute("UPDATE orders SET status=%s WHERE order_id=%s", (new_status, order_id))
        else:
            cur.execute("UPDATE orders SET status=%s WHERE order_id=%s", (new_status, order_id))

        conn.commit()
        return {"status": "success",
                "message": f"Đơn #{order_id}: {STATUS_LABELS[current]} → {STATUS_LABELS[new_status]}",
                "order_id": order_id, "old_status": current, "new_status": new_status,
                "allowed_next": sorted(ALLOWED_TRANSITIONS[new_status])}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Lỗi cập nhật trạng thái: {e}")
    finally:
        conn.close()


# ==========================================
# 💵 XÁC NHẬN ĐÃ NHẬN TIỀN (thủ công — cho đơn chuyển khoản QR)
# Admin thấy tiền vào app ngân hàng → bấm nút này. Không phụ thuộc SePay.
# ==========================================
@router.put("/admin/orders/{order_id}/mark-paid", tags=[TAG])
def mark_order_paid(order_id: int):
    conn = get_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT payment_status, payment_method FROM orders WHERE order_id=%s", (order_id,))
        order = cur.fetchone()
        if not order:
            raise HTTPException(status_code=404, detail="Đơn hàng không tồn tại")
        if order["payment_status"] == "paid":
            raise HTTPException(status_code=400, detail="Đơn này đã ở trạng thái ĐÃ thanh toán")
        cur.execute("UPDATE orders SET payment_status='paid' WHERE order_id=%s", (order_id,))
        conn.commit()
        return {"status": "success", "message": f"Đơn #{order_id}: đã xác nhận ĐÃ NHẬN TIỀN",
                "order_id": order_id, "payment_status": "paid"}
    finally:
        conn.close()
