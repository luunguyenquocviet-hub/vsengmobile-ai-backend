# scripts/inspect_db.py — In cấu trúc bảng orders/order_items hiện có + số dòng dữ liệu
# Chạy:  python scripts/inspect_db.py   (dán output cho Claude xem)
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.database import get_mysql  # noqa: E402


def main():
    conn = get_mysql()
    try:
        cur = conn.cursor()
        for table in ("orders", "order_items", "users"):
            print(f"\n===== {table} =====")
            try:
                cur.execute(f"DESCRIBE {table}")
                for col in cur.fetchall():
                    # (Field, Type, Null, Key, Default, Extra)
                    print(f"  {col[0]:<22} {col[1]:<30} null={col[2]} key={col[3]} default={col[4]}")
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                print(f"  → số dòng: {cur.fetchone()[0]}")
            except Exception as e:
                print(f"  (lỗi: {e})")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
