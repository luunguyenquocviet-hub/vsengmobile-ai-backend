# utils/bankqr.py
"""
CHUYỂN KHOẢN QR (VietQR) + tự động xác nhận tiền vào qua SePay.

Luồng:
  1. Khách chọn "Chuyển khoản QR" → backend tạo URL ảnh VietQR (img.vietqr.io)
     với đúng SỐ TIỀN + nội dung "DH{order_id}" → khách quét bằng app ngân hàng.
  2. Xác nhận tiền vào (2 lớp):
     - Tự động: backend gọi API SePay (đọc biến động số dư — CHỈ ĐỌC, không có
       quyền chuyển tiền) tìm giao dịch tiền VÀO có nội dung DH{id} + đủ tiền.
     - Thủ công: admin bấm "Đã nhận tiền" trên trang quản lý (không cần SePay).

.env:  BANK_ID, BANK_ACCOUNT_NO, BANK_ACCOUNT_NAME  (hiện QR)
       SEPAY_API_TOKEN                              (tự động check — my.sepay.vn → API Access)
"""
import os
import re
import json
import urllib.parse
import urllib.request

VIETQR_IMG = "https://img.vietqr.io/image/{bank}-{acc}-compact2.png"
SEPAY_TX_API = "https://my.sepay.vn/userapi/transactions/list"


def bank_config() -> dict:
    return {
        "bank_id": os.getenv("BANK_ID", "vietcombank").strip().lower(),
        "account_no": os.getenv("BANK_ACCOUNT_NO", "").strip(),
        "account_name": os.getenv("BANK_ACCOUNT_NAME", "").strip(),
    }


def is_configured() -> bool:
    return bool(bank_config()["account_no"])


def transfer_content(order_id: int) -> str:
    """Nội dung chuyển khoản duy nhất cho từng đơn — dùng để đối soát."""
    return f"DH{int(order_id)}"


def build_qr_url(order_id: int, amount_vnd: int) -> str:
    """URL ảnh VietQR: quét là ra sẵn số tiền + nội dung DH{id} (không sửa được số tiền...
    thực ra app cho sửa, nên khi đối soát vẫn PHẢI so số tiền >= tổng đơn)."""
    cfg = bank_config()
    if not cfg["account_no"]:
        raise RuntimeError("Chưa cấu hình BANK_ACCOUNT_NO trong .env — điền số tài khoản nhận tiền trước.")
    query = urllib.parse.urlencode({
        "amount": int(amount_vnd),
        "addInfo": transfer_content(order_id),
        "accountName": cfg["account_name"],
    }, quote_via=urllib.parse.quote)
    return VIETQR_IMG.format(bank=cfg["bank_id"], acc=cfg["account_no"]) + "?" + query


# ============================================================
# SePay — đọc danh sách giao dịch (Bearer token, CHỈ QUYỀN ĐỌC)
# ============================================================
def sepay_configured() -> bool:
    return bool(os.getenv("SEPAY_API_TOKEN", "").strip())


def fetch_recent_transactions(limit: int = 30) -> list[dict]:
    token = os.getenv("SEPAY_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Chưa cấu hình SEPAY_API_TOKEN trong .env")
    req = urllib.request.Request(
        f"{SEPAY_TX_API}?limit={int(limit)}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=12) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("transactions") or []


def content_matches(content: str, order_id: int) -> bool:
    """Nội dung CK có chứa DH{id} không (ngân hàng có thể chèn thêm chữ xung quanh).
    (?!\\d) để DH15 không khớp nhầm DH153."""
    return bool(re.search(rf"DH\s*{int(order_id)}(?!\d)", str(content or ""), re.IGNORECASE))


def find_matching_payment(transactions: list[dict], order_id: int, amount_vnd: int) -> dict | None:
    """Tìm giao dịch TIỀN VÀO khớp đơn: nội dung chứa DH{id} + số tiền đủ."""
    for t in transactions:
        amount_in = float(t.get("amount_in") or 0)
        if amount_in >= float(amount_vnd) and content_matches(t.get("transaction_content"), order_id):
            return t
    return None
