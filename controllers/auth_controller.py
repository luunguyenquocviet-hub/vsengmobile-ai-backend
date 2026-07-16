# controllers/auth_controller.py
"""
QUÊN MẬT KHẨU bằng OTP 6 số qua email (login/register do hệ thống khác đảm nhiệm).

Luồng 3 bước:
  1. POST /auth/forgot-password  {email}                     → sinh OTP, gửi Gmail
  2. POST /auth/verify-otp       {email, otp}                → (tùy chọn) check OTP trước khi cho nhập MK mới
  3. POST /auth/reset-password   {email, otp, new_password}  → đổi mật khẩu (bcrypt)

An toàn:
  - Chỉ lưu SHA-256 của OTP (lộ DB cũng không lộ OTP), hết hạn sau 10 phút
  - Tối đa 5 lần nhập sai; xin OTP mới phải cách nhau ≥ 60 giây
  - Mật khẩu lưu bcrypt hash. (Nếu trang login của bạn là PHP password_verify()
    mà không nhận prefix $2b$, đổi `hashed` bên dưới: hashed.replace(b"$2b$", b"$2y$"))

Cấu hình .env: GMAIL_USER, GMAIL_APP_PASSWORD (App Password 16 ký tự, KHÔNG phải mật khẩu Gmail).
Chưa cấu hình → chạy CHẾ ĐỘ DEMO: OTP trả thẳng trong response để test.
"""
import os
import secrets
import hashlib
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import bcrypt
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from config.database import get_mysql

router = APIRouter()
TAG = "🔑 Quên mật khẩu"

OTP_TTL_MINUTES = 10
MAX_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 60


class ForgotPasswordIn(BaseModel):
    email: str = Field(min_length=5, max_length=255)


class VerifyOtpIn(BaseModel):
    email: str
    otp: str = Field(min_length=6, max_length=6)


class ResetPasswordIn(BaseModel):
    email: str
    otp: str = Field(min_length=6, max_length=6)
    new_password: str = Field(min_length=6, max_length=72)  # bcrypt giới hạn 72 byte


def _hash_otp(otp: str) -> str:
    return hashlib.sha256(otp.encode()).hexdigest()


def _smtp_configured() -> bool:
    return bool(os.getenv("GMAIL_USER", "").strip() and os.getenv("GMAIL_APP_PASSWORD", "").strip())


def _send_otp_email(to_email: str, otp: str) -> None:
    """Gửi OTP qua Gmail SMTP (SSL cổng 465). Lỗi đăng nhập/kết nối sẽ raise để endpoint báo rõ."""
    user = os.getenv("GMAIL_USER").strip()
    app_pw = os.getenv("GMAIL_APP_PASSWORD").strip().replace(" ", "")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[VsengMobile] Mã OTP đặt lại mật khẩu: {otp}"
    msg["From"] = f"VsengMobile <{user}>"
    msg["To"] = to_email
    text = (f"Mã OTP đặt lại mật khẩu của bạn là: {otp}\n"
            f"Mã có hiệu lực trong {OTP_TTL_MINUTES} phút. "
            f"Nếu không phải bạn yêu cầu, hãy bỏ qua email này.")
    html = f"""<div style="font-family:Arial,sans-serif;max-width:480px;margin:auto;
      border:1px solid #e2e8f0;border-radius:12px;padding:28px">
      <h2 style="color:#0f172a;margin-top:0">Đặt lại mật khẩu VsengMobile</h2>
      <p style="color:#334155">Mã OTP của bạn (hiệu lực {OTP_TTL_MINUTES} phút):</p>
      <div style="font-size:34px;font-weight:bold;letter-spacing:10px;text-align:center;
        background:#f1f5f9;border-radius:10px;padding:16px;color:#1d4ed8">{otp}</div>
      <p style="color:#64748b;font-size:13px;margin-bottom:0">
        Nếu không phải bạn yêu cầu, hãy bỏ qua email này — mật khẩu của bạn vẫn an toàn.</p></div>"""
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
        server.login(user, app_pw)
        server.sendmail(user, [to_email], msg.as_string())


# ==========================================
# B1 — XIN OTP
# ==========================================
@router.post("/auth/forgot-password", tags=[TAG])
def forgot_password(data: ForgotPasswordIn):
    email = data.email.strip().lower()
    conn = get_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        # Lưu ý bảo mật: production nên trả thông báo chung chung để khỏi lộ email nào
        # có tài khoản (user enumeration). Ở đồ án trả 404 rõ ràng cho dễ demo.
        cur.execute("SELECT user_id, user_name FROM users WHERE LOWER(email)=%s", (email,))
        user = cur.fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="Email này chưa đăng ký tài khoản")

        # Chống spam: OTP trước đó tạo chưa quá 60s → bắt chờ
        cur.execute("""SELECT TIMESTAMPDIFF(SECOND, created_at, NOW()) AS age
                       FROM password_resets WHERE email=%s AND used=0
                       ORDER BY id DESC LIMIT 1""", (email,))
        last = cur.fetchone()
        if last and last["age"] is not None and last["age"] < RESEND_COOLDOWN_SECONDS:
            raise HTTPException(status_code=429,
                                detail=f"Vui lòng chờ {RESEND_COOLDOWN_SECONDS - last['age']}s để xin mã mới")

        otp = f"{secrets.randbelow(10**6):06d}"
        cur.execute("UPDATE password_resets SET used=1 WHERE email=%s AND used=0", (email,))  # hủy OTP cũ
        cur.execute("""INSERT INTO password_resets (email, otp_hash, expires_at)
                       VALUES (%s, %s, DATE_ADD(NOW(), INTERVAL %s MINUTE))""",
                    (email, _hash_otp(otp), OTP_TTL_MINUTES))
        conn.commit()
    finally:
        conn.close()

    # Gửi email (hoặc chế độ demo nếu chưa cấu hình SMTP)
    if _smtp_configured():
        try:
            _send_otp_email(email, otp)
        except smtplib.SMTPAuthenticationError:
            raise HTTPException(status_code=502,
                                detail="Gmail từ chối đăng nhập — kiểm tra GMAIL_USER/GMAIL_APP_PASSWORD "
                                       "(phải là App Password, xem hướng dẫn trong notes/)")
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Không gửi được email: {e}")
        return {"status": "success",
                "message": f"Đã gửi mã OTP tới {email} (hiệu lực {OTP_TTL_MINUTES} phút)"}

    return {"status": "success", "demo_mode": True,
            "message": "CHƯA cấu hình GMAIL_USER/GMAIL_APP_PASSWORD trong .env — OTP trả kèm để demo",
            "demo_otp": otp}


def _get_valid_reset_row(cur, email: str, otp: str):
    """Lấy bản ghi OTP hợp lệ; sai thì tăng attempts và raise lỗi tương ứng."""
    cur.execute("""SELECT id, otp_hash, attempts, TIMESTAMPDIFF(SECOND, NOW(), expires_at) AS ttl
                   FROM password_resets WHERE email=%s AND used=0
                   ORDER BY id DESC LIMIT 1""", (email,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=400, detail="Chưa có yêu cầu OTP nào — hãy bấm 'Quên mật khẩu' trước")
    if row["ttl"] is None or row["ttl"] <= 0:
        raise HTTPException(status_code=400, detail="Mã OTP đã hết hạn — vui lòng xin mã mới")
    if row["attempts"] >= MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Nhập sai quá 5 lần — vui lòng xin mã OTP mới")
    if row["otp_hash"] != _hash_otp(otp):
        cur.execute("UPDATE password_resets SET attempts = attempts + 1 WHERE id=%s", (row["id"],))
        remaining = MAX_ATTEMPTS - row["attempts"] - 1
        raise HTTPException(status_code=400, detail=f"Mã OTP không đúng (còn {remaining} lần thử)")
    return row


# ==========================================
# B2 — (tùy chọn) KIỂM TRA OTP trước khi cho nhập mật khẩu mới
# ==========================================
@router.post("/auth/verify-otp", tags=[TAG])
def verify_otp(data: VerifyOtpIn):
    email = data.email.strip().lower()
    conn = get_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        try:
            _get_valid_reset_row(cur, email, data.otp.strip())
        finally:
            conn.commit()  # lưu số lần thử sai kể cả khi raise
        return {"status": "success", "message": "OTP hợp lệ — có thể đặt mật khẩu mới"}
    finally:
        conn.close()


# ==========================================
# B3 — ĐỔI MẬT KHẨU MỚI
# ==========================================
@router.post("/auth/reset-password", tags=[TAG])
def reset_password(data: ResetPasswordIn):
    email = data.email.strip().lower()
    conn = get_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        try:
            row = _get_valid_reset_row(cur, email, data.otp.strip())
        except HTTPException:
            conn.commit()  # lưu attempts khi nhập sai
            raise

        hashed = bcrypt.hashpw(data.new_password.encode("utf-8"), bcrypt.gensalt(rounds=12))
        cur.execute("UPDATE users SET password=%s WHERE LOWER(email)=%s", (hashed.decode(), email))
        cur.execute("UPDATE password_resets SET used=1 WHERE id=%s", (row["id"],))
        conn.commit()
        return {"status": "success", "message": "Đổi mật khẩu thành công — hãy đăng nhập bằng mật khẩu mới"}
    finally:
        conn.close()
