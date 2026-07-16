# utils/vnpay.py
"""
Tiện ích VNPay SANDBOX (v2.1.0) — build URL thanh toán + verify chữ ký trả về.

Quy tắc chữ ký của VNPay:
  - Sắp xếp tham số vnp_* theo ALPHABET, url-encode kiểu quote_plus, nối bằng '&'
  - HMAC-SHA512 chuỗi đó với vnp_HashSecret → gắn vào vnp_SecureHash
  - Khi verify: bỏ vnp_SecureHash / vnp_SecureHashType ra rồi làm y hệt và so sánh

Cấu hình trong .env:  VNPAY_TMN_CODE, VNPAY_HASH_SECRET, VNPAY_RETURN_URL
Đăng ký tài khoản test: https://sandbox.vnpayment.vn/devreg
"""
import os
import hmac
import hashlib
import urllib.parse
from datetime import datetime, timedelta, timezone

VNP_PAY_URL = "https://sandbox.vnpayment.vn/paymentv2/vpcpay.html"
_VN_TZ = timezone(timedelta(hours=7))  # vnp_CreateDate bắt buộc theo giờ GMT+7

DEFAULT_RETURN_URL = "http://localhost:8000/api/payment/vnpay/return"


def is_mock() -> bool:
    """VNPAY_MOCK=1 trong .env → dùng cổng thanh toán GIẢ LẬP chạy local.
    Luồng y hệt cổng thật (redirect → gateway → return URL có chữ ký HMAC hợp lệ),
    dùng khi chưa đăng ký được tài khoản sandbox. Có TmnCode thật thì xóa/để 0 là chạy cổng thật."""
    return os.getenv("VNPAY_MOCK", "").strip().lower() in ("1", "true", "yes")


def is_configured() -> bool:
    return is_mock() or bool(os.getenv("VNPAY_TMN_CODE", "").strip() and os.getenv("VNPAY_HASH_SECRET", "").strip())


def _config() -> tuple[str, str]:
    tmn = os.getenv("VNPAY_TMN_CODE", "").strip()
    secret = os.getenv("VNPAY_HASH_SECRET", "").strip()
    if is_mock():  # mock: chưa có thông tin thật thì dùng bộ khóa giả nội bộ
        return (tmn or "MOCKTMN1", secret or "MOCK_SECRET_VSENGMOBILE_2026")
    if not tmn or not secret:
        raise RuntimeError(
            "Chưa cấu hình VNPAY_TMN_CODE / VNPAY_HASH_SECRET trong .env. "
            "Đăng ký sandbox tại https://sandbox.vnpayment.vn/devreg rồi điền vào .env, "
            "hoặc đặt VNPAY_MOCK=\"1\" để demo bằng cổng giả lập."
        )
    return tmn, secret


def _hmac_sha512(secret: str, data: str) -> str:
    return hmac.new(secret.encode("utf-8"), data.encode("utf-8"), hashlib.sha512).hexdigest()


def _encode_sorted(params: dict) -> str:
    """Sắp theo key + quote_plus — PHẢI giống hệt lúc ký và lúc verify."""
    return urllib.parse.urlencode(sorted(params.items()), quote_via=urllib.parse.quote_plus)


def build_payment_url(txn_ref: str, amount_vnd: int, order_info: str,
                      client_ip: str = "127.0.0.1", locale: str = "vn") -> str:
    """
    Tạo URL redirect sang trang thanh toán VNPay sandbox.
    amount_vnd: số tiền VND THẬT (hàm tự nhân 100 theo chuẩn VNPay).
    """
    tmn, secret = _config()
    now = datetime.now(_VN_TZ)
    params = {
        "vnp_Version": "2.1.0",
        "vnp_Command": "pay",
        "vnp_TmnCode": tmn,
        "vnp_Amount": str(int(amount_vnd) * 100),
        "vnp_CurrCode": "VND",
        "vnp_TxnRef": txn_ref,
        "vnp_OrderInfo": order_info,
        "vnp_OrderType": "other",
        "vnp_Locale": locale,
        "vnp_ReturnUrl": os.getenv("VNPAY_RETURN_URL", DEFAULT_RETURN_URL),
        "vnp_IpAddr": client_ip,
        "vnp_CreateDate": now.strftime("%Y%m%d%H%M%S"),
        "vnp_ExpireDate": (now + timedelta(minutes=15)).strftime("%Y%m%d%H%M%S"),
    }
    query = _encode_sorted(params)
    # Mock: trỏ về cổng giả lập local thay vì sandbox VNPay (tham số + chữ ký giữ nguyên)
    base = (os.getenv("VNPAY_RETURN_URL", DEFAULT_RETURN_URL).replace("/vnpay/return", "/mock/gateway")
            if is_mock() else VNP_PAY_URL)
    return f"{base}?{query}&vnp_SecureHash={_hmac_sha512(secret, query)}"


def build_mock_return_url(params: dict, success: bool) -> str:
    """[MOCK] Tạo URL quay về return URL với bộ tham số + chữ ký y như VNPay thật gửi.
    success=False mô phỏng khách bấm hủy (mã 24)."""
    _, secret = _config()
    now = datetime.now(_VN_TZ)
    res = {
        "vnp_Amount": params.get("vnp_Amount", ""),
        "vnp_TmnCode": params.get("vnp_TmnCode", ""),
        "vnp_TxnRef": params.get("vnp_TxnRef", ""),
        "vnp_OrderInfo": params.get("vnp_OrderInfo", ""),
        "vnp_ResponseCode": "00" if success else "24",
        "vnp_TransactionStatus": "00" if success else "02",
        "vnp_TransactionNo": now.strftime("MOCK%H%M%S"),
        "vnp_BankCode": "NCB",
        "vnp_CardType": "ATM",
        "vnp_PayDate": now.strftime("%Y%m%d%H%M%S"),
    }
    query = _encode_sorted(res)
    return_url = params.get("vnp_ReturnUrl") or os.getenv("VNPAY_RETURN_URL", DEFAULT_RETURN_URL)
    return f"{return_url}?{query}&vnp_SecureHash={_hmac_sha512(secret, query)}"


def verify_signature(params: dict) -> bool:
    """Verify chữ ký trên query params VNPay gửi về (return URL / IPN)."""
    received = str(params.get("vnp_SecureHash", "")).lower()
    if not received:
        return False
    _, secret = _config()
    data = {k: v for k, v in params.items()
            if k.startswith("vnp_") and k not in ("vnp_SecureHash", "vnp_SecureHashType")}
    expected = _hmac_sha512(secret, _encode_sorted(data))
    return hmac.compare_digest(expected, received)
