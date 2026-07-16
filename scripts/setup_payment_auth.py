# scripts/setup_payment_auth.py
"""
Chuẩn bị DB cho luồng ĐẶT HÀNG + THANH TOÁN + QUÊN MẬT KHẨU:
  1. Tạo bảng orders, order_items          (đơn hàng)
  2. Tạo bảng password_resets              (OTP quên mật khẩu)
  3. Thêm cột password vào users (nếu chưa có)
  4. Seed ~12 đơn hàng mẫu để trang admin có dữ liệu demo (bỏ qua nếu đã có đơn)

Chạy từ THƯ MỤC GỐC dự án:   python scripts/setup_payment_auth.py
Chạy lại nhiều lần vẫn an toàn (idempotent).
"""
import os
import sys
import random
from datetime import datetime, timedelta

# Cho phép chạy từ thư mục gốc: thêm root vào sys.path để import config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.database import get_mysql  # noqa: E402

# Trạng thái đơn hàng — dùng slug tiếng Việt không dấu, khớp với admin_controller
ORDER_STATUSES = ("cho_xac_nhan", "da_xac_nhan", "dang_van_chuyen", "da_giao", "da_huy")

DDL = [
    # --- orders: 1 dòng = 1 đơn hàng ---
    """
    CREATE TABLE IF NOT EXISTS orders (
        order_id        INT AUTO_INCREMENT PRIMARY KEY,
        user_id         INT NOT NULL,
        receiver_name   VARCHAR(100) NOT NULL,
        receiver_phone  VARCHAR(15)  NOT NULL,
        shipping_address VARCHAR(255) NOT NULL,
        note            VARCHAR(255) DEFAULT NULL,
        total_amount    DECIMAL(15,0) NOT NULL DEFAULT 0,
        payment_method  ENUM('cod','vnpay','bank_qr') NOT NULL DEFAULT 'cod',
        payment_status  ENUM('unpaid','paid','failed') NOT NULL DEFAULT 'unpaid',
        status          ENUM('cho_xac_nhan','da_xac_nhan','dang_van_chuyen','da_giao','da_huy')
                        NOT NULL DEFAULT 'cho_xac_nhan',
        cancel_reason   VARCHAR(255) DEFAULT NULL,
        vnp_txn_ref     VARCHAR(50)  DEFAULT NULL,
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX idx_status (status),
        INDEX idx_user   (user_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    # --- order_items: các sản phẩm trong đơn (snapshot tên + giá tại thời điểm mua) ---
    # KHÔNG đặt FK sang products(item_id) để tránh lệch kiểu cột giữa các máy;
    # tính toàn vẹn do API đảm bảo khi tạo đơn.
    """
    CREATE TABLE IF NOT EXISTS order_items (
        id           INT AUTO_INCREMENT PRIMARY KEY,
        order_id     INT NOT NULL,
        item_id      BIGINT NOT NULL,
        product_name VARCHAR(255) DEFAULT NULL,
        price        DECIMAL(15,0) NOT NULL,
        quantity     INT NOT NULL DEFAULT 1,
        FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE,
        INDEX idx_order (order_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    # --- password_resets: OTP quên mật khẩu (lưu HASH của OTP, không lưu OTP thô) ---
    """
    CREATE TABLE IF NOT EXISTS password_resets (
        id         INT AUTO_INCREMENT PRIMARY KEY,
        email      VARCHAR(255) NOT NULL,
        otp_hash   VARCHAR(64)  NOT NULL,
        expires_at DATETIME NOT NULL,
        attempts   INT NOT NULL DEFAULT 0,
        used       TINYINT(1) NOT NULL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_email (email)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]


def _table_exists(cur, name: str) -> bool:
    cur.execute("""SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES
                   WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s""", (name,))
    return cur.fetchone()[0] > 0


def _column_exists(cur, table: str, col: str) -> bool:
    cur.execute("""SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
                   WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s""", (table, col))
    return cur.fetchone()[0] > 0


def drop_legacy_empty_tables(cur, conn):
    """DB có thể còn bảng orders/order_items PHIÊN BẢN CŨ (thiết kế trước, thiếu cột
    receiver_name/product_name...). Nếu bảng cũ đang TRỐNG → xóa để DDL tạo lại theo
    schema mới. Nếu bảng cũ CÓ dữ liệu → dừng lại, không tự ý xóa."""
    # Xóa bảng con (order_items) trước để không vướng khóa ngoại
    for table, marker_col in (("order_items", "product_name"), ("orders", "receiver_name")):
        if not _table_exists(cur, table) or _column_exists(cur, table, marker_col):
            continue  # chưa có bảng, hoặc đã đúng schema mới
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        if cur.fetchone()[0] > 0:
            raise SystemExit(
                f"⛔ Bảng '{table}' theo schema CŨ đang có dữ liệu — không tự xóa.\n"
                f"   Hãy backup rồi tự chạy: DROP TABLE {table}; sau đó chạy lại script này.")
        cur.execute(f"DROP TABLE IF EXISTS {table}")
        print(f"   ♻️ Đã xóa bảng '{table}' schema cũ (đang trống) — sẽ tạo lại theo schema mới")
    conn.commit()


def ensure_payment_method_enum(cur):
    """Bảng orders tạo trước khi có 'bank_qr' → mở rộng ENUM payment_method (an toàn, không mất dữ liệu)."""
    cur.execute("""SELECT COLUMN_TYPE FROM INFORMATION_SCHEMA.COLUMNS
                   WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='orders' AND COLUMN_NAME='payment_method'""")
    row = cur.fetchone()
    if row and "bank_qr" not in str(row[0]):
        cur.execute("ALTER TABLE orders MODIFY payment_method "
                    "ENUM('cod','vnpay','bank_qr') NOT NULL DEFAULT 'cod'")
        print("   ➕ Đã thêm 'bank_qr' vào ENUM orders.payment_method")
    else:
        print("   ✓ orders.payment_method đã có 'bank_qr'")


def ensure_password_column(cur):
    """Thêm cột users.password nếu chưa có (kiểm tra INFORMATION_SCHEMA — chạy được cả MySQL lẫn MariaDB)."""
    cur.execute("""
        SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME='users' AND COLUMN_NAME='password'
    """)
    if cur.fetchone()[0] == 0:
        cur.execute("ALTER TABLE users ADD COLUMN password VARCHAR(255) DEFAULT NULL")
        print("   ➕ Đã thêm cột users.password (VARCHAR 255, lưu bcrypt hash)")
    else:
        print("   ✓ users.password đã tồn tại")


def seed_mock_orders(conn):
    """Seed đơn mẫu cho trang admin demo. Chỉ seed khi bảng orders đang TRỐNG."""
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT COUNT(*) AS n FROM orders")
    if cur.fetchone()["n"] > 0:
        print("   ✓ orders đã có dữ liệu — bỏ qua seed")
        return

    cur.execute("SELECT item_id, name, price FROM products WHERE stock > 5 ORDER BY sold_quantity DESC LIMIT 40")
    products = cur.fetchall()
    cur.execute("SELECT user_id, user_name FROM users LIMIT 50")
    users = cur.fetchall()
    if not products or not users:
        print("   ⚠️ products/users trống — chạy import_to_mysql.py + nạp users trước, bỏ qua seed")
        return

    dia_chi = ["12 Nguyễn Văn Bảo, Gò Vấp, TP.HCM", "220 Lê Duẩn, Hải Châu, Đà Nẵng",
               "35 Cầu Giấy, Hà Nội", "78 Trần Hưng Đạo, Q.1, TP.HCM",
               "156 Nguyễn Trãi, Thanh Xuân, Hà Nội", "45 Hùng Vương, TP. Huế"]
    # Phân bổ trạng thái để demo đủ các tab
    plan = (["cho_xac_nhan"] * 4 + ["da_xac_nhan"] * 3 +
            ["dang_van_chuyen"] * 2 + ["da_giao"] * 2 + ["da_huy"] * 1)

    ins_order = """INSERT INTO orders (user_id, receiver_name, receiver_phone, shipping_address,
                   total_amount, payment_method, payment_status, status, cancel_reason, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""
    ins_item = """INSERT INTO order_items (order_id, item_id, product_name, price, quantity)
                  VALUES (%s,%s,%s,%s,%s)"""

    for i, status in enumerate(plan):
        u = random.choice(users)
        chosen = random.sample(products, random.randint(1, 3))
        pm = random.choice(["cod", "cod", "bank_qr"])
        # Đơn CK QR chưa hủy coi như đã thanh toán; COD chỉ 'paid' khi đã giao
        if status == "da_giao":
            pay = "paid"
        elif pm == "bank_qr" and status != "da_huy":
            pay = "paid"
        else:
            pay = "unpaid"
        created = datetime.now() - timedelta(days=random.randint(0, 14),
                                             hours=random.randint(0, 23), minutes=random.randint(0, 59))
        total = 0
        items = []
        for p in chosen:
            qty = random.randint(1, 2)
            price = int(float(p["price"] or 0))
            total += price * qty
            items.append((p["item_id"], p["name"], price, qty))

        cur.execute(ins_order, (u["user_id"], u["user_name"], f"09{random.randint(10000000, 99999999)}",
                                random.choice(dia_chi), total, pm, pay, status,
                                "Khách đổi ý không mua nữa" if status == "da_huy" else None, created))
        oid = cur.lastrowid
        for it in items:
            cur.execute(ins_item, (oid, *it))
    conn.commit()
    print(f"   🌱 Đã seed {len(plan)} đơn hàng mẫu (đủ 5 trạng thái)")


def main():
    conn = get_mysql()
    try:
        cur = conn.cursor()
        print("🔧 Kiểm tra bảng cũ...")
        drop_legacy_empty_tables(cur, conn)
        print("🔧 Tạo bảng...")
        for i, ddl in enumerate(DDL, 1):
            cur.execute(ddl)
            print(f"   ✓ DDL {i}/{len(DDL)} OK")
        ensure_password_column(cur)
        ensure_payment_method_enum(cur)
        conn.commit()
        seed_mock_orders(conn)
        print("✅ Xong! DB sẵn sàng cho API thanh toán + quên mật khẩu + admin đơn hàng.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
