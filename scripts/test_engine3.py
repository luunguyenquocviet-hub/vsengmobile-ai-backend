"""
Smoke test ĐỘC LẬP cho Engine ③ (XGBoost Content-Based).

Chạy TỪ THƯ MỤC GỐC dự án:
    python test_engine3.py

CHỈ import models.product (pandas + xgboost), KHÔNG đụng MySQL/MongoDB — kiểm tra Engine ③
tách biệt, không cần CSDL. Test cả 2 đường:
  (A) predict_batch_products      → đường /predict-home, giá THÔ (×100000), tự chia.
  (B) predict_liked_from_real_vnd → đường NỘI BỘ trang chủ, giá ĐÃ là VND thật (không chia).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # chạy được từ mọi nơi
from models.product import (
    ProductInfo, predict_batch_products, predict_liked_from_real_vnd,
)


def banner(t): print("\n" + "=" * 8 + " " + t + " " + "=" * 8)


# ---------- (A) Đường /predict-home: giá THÔ ----------
banner("(A) predict_batch_products — giá thô (×100000)")
mau_tho = [
    ProductInfo(discount_price=250_000_000, original_price=300_000_000, liked_count=1200,
                number_of_ratings=340, sold_quantity=2500, stock=80,
                discount="17%", brand="Xiaomi", shop_location="TP. Hồ Chí Minh"),
    ProductInfo(discount_price=5_000_000, original_price=5_500_000, liked_count=3,
                number_of_ratings=1, sold_quantity=2, stock=5,
                discount="9%", brand="No Brand", shop_location="Hà Nội"),
]
try:
    kq_a = predict_batch_products(mau_tho)
except RuntimeError as e:
    print("❌ Engine ③ CHƯA chạy được:", e); raise SystemExit(1)
for i, (p, v) in enumerate(zip(mau_tho, kq_a)):
    print(f"  [{i}] {p.brand:10s} | được gợi ý? -> {v}")


# ---------- (B) Đường nội bộ trang chủ: giá VND thật ----------
banner("(B) predict_liked_from_real_vnd — giá VND thật (như MySQL)")
mau_that = [
    {"discount_price": 2_500_000, "original_price": 3_000_000, "liked_count": 1200,
     "number_of_ratings": 340, "sold_quantity": 2500, "stock": 80},          # SP mạnh
    {"discount_price": 500_000, "original_price": 550_000, "liked_count": 2,
     "number_of_ratings": 1, "sold_quantity": 1, "stock": 5},                # SP yếu
    {"discount_price": 8_000_000},  # thiếu hầu hết feature -> core tự điền 0
]
kq_b = predict_liked_from_real_vnd(mau_that)
for i, (r, v) in enumerate(zip(mau_that, kq_b)):
    print(f"  [{i}] giá {int(r['discount_price']):>10,}đ | được gợi ý? -> {v}")

print("\n✅ Engine ③ chạy được ở cả 2 đường — model nạp và dự đoán thành công.")