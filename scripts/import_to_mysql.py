# import_to_mysql.py
"""
Nạp data.xlsx vào MySQL vsengmobile_store_db.
  - products  ← sheet ID + DATA (gộp theo item_id, quy đổi giá, chuẩn hóa brand)
  - reviews   ← sheet RATING (sentiment để trống, nightly_bert_update.py sẽ chấm sau;
                hoặc nếu có file data_danh_gia_AI_HoanChinh.xlsx thì nạp luôn sentiment)

KHÔNG nạp: users (bạn đã có dữ liệu thật), orders/order_items (do shop thật sinh ra
khi user mua; để test có thể dùng seed_mock_orders.py).

Chạy:  python import_to_mysql.py
Yêu cầu:  pip install pandas openpyxl mysql-connector-python
"""
import os
import math
import unicodedata
import pandas as pd
import mysql.connector

# ---- Cấu hình kết nối (sửa cho khớp XAMPP/MySQL của bạn) ----
DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "",
    "database": "vsengmobile_store_db",
}
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # thư mục gốc dự án
EXCEL_PATH = os.path.join(_ROOT, "data", "data.xlsx")
AI_REVIEWS_PATH = os.path.join(_ROOT, "data", "data_danh_gia_AI_HoanChinh.xlsx")  # tùy chọn, nếu có sẵn sentiment


def _clean_int(v, default=0):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return default
    return int(v)


# Từ khóa phụ kiện/linh kiện độ chính xác cao — sản phẩm có tên chứa các từ này
# gần như chắc chắn KHÔNG phải điện thoại nguyên chiếc (ốp, cường lực, camera sau,
# khay sim, main, màn hình rời, que chọc sim...). Đã loại các từ mơ hồ ("thẻ nhớ",
# "pin", "thay") vì chúng xuất hiện trong tên điện thoại thật.
JUNK_KEYWORDS = [
    'ốp', 'vỏ ', 'cường lực', 'miếng dán', 'dán ', 'tai nghe', 'cáp sạc', 'sạc dự phòng',
    'pin dự phòng', 'bao da', 'kính cường', 'kính camera', 'kính lưng', 'kính sau',
    'giá đỡ', 'bút cảm ứng', 'lá vàng', 'móc khóa', 'dây đeo', 'gậy chụp', 'thần tài',
    'bao silicon', 'case ', 'loa trong', 'loa ', 'phím nokia', 'phím ', 'nắp lưng',
    'nắp ', 'sườn', 'jack', 'cọc sạc', 'chân sạc', 'module', 'mạch', 'linh kiện',
    'camera sau', 'camera trước', 'khay sim', 'main ', 'cảm ứng', 'ron ', 'skin ',
    'que chọc', 'que chọt', 'miếng lau', 'mô hình', 'nhẫn', 'nhẩn', 'decor', 'sim ghép',
    'đầu đọc', 'màn hình tab', 'bộ màn hình', 'màn hình oppo', 'màn hình nokia',
    'màn hình samsung', 'xác ',
]

# Giá tối thiểu (VND) để coi là điện thoại nguyên chiếc. Trong bộ dữ liệu này,
# phụ kiện/linh kiện hầu hết dưới 150K, điện thoại thật từ 150K trở lên.
# Hạ xuống 100000 nếu bạn muốn giữ cả mấy Nokia phổ thông siêu rẻ (~100-149K).
MIN_PHONE_PRICE = 150000


def _is_real_phone(name: str, num_ratings: int, price_vnd: int) -> bool:
    """
    True nếu là điện thoại nguyên chiếc, False nếu là phụ kiện/linh kiện/rác.

    Lọc bằng 3 lớp: (1) chuẩn hóa NFC rồi so từ khóa — QUAN TRỌNG vì dữ liệu Shopee
    dùng unicode tổ hợp, không chuẩn hóa thì 'cường lực' không khớp; (2) phải có
    đánh giá; (3) giá >= ngưỡng điện thoại thật.
    Lưu ý: dữ liệu nguồn lẫn nhiều phụ tùng nên lọc đạt ~95%+, không tuyệt đối.
    """
    nl = unicodedata.normalize("NFC", str(name)).lower()
    if any(kw in nl for kw in JUNK_KEYWORDS):
        return False
    if num_ratings <= 0:
        return False
    if price_vnd < MIN_PHONE_PRICE:
        return False
    return True


def _insert_in_batches(conn, sql, rows, batch_size=1000):
    """
    Chèn theo từng lô nhỏ thay vì 1 câu INSERT khổng lồ.
    Tránh lỗi 'Got a packet bigger than max_allowed_packet' khi dữ liệu lớn
    (vd 61K review). Mỗi lô ~1000 dòng → gói tin luôn nhỏ, an toàn.
    """
    cur = conn.cursor()
    total = len(rows)
    for i in range(0, total, batch_size):
        cur.executemany(sql, rows[i:i + batch_size])
        conn.commit()
        print(f"      ...đã chèn {min(i + batch_size, total)}/{total}")


def import_products(conn):
    print("📦 Nạp products...")
    ids = pd.read_excel(EXCEL_PATH, sheet_name="ID")
    data = pd.read_excel(EXCEL_PATH, sheet_name="DATA")

    # item_id trùng 2 dòng → giữ dòng đầu
    ids = ids.drop_duplicates(subset="item_id", keep="first")
    data = data.drop_duplicates(subset="item_id", keep="first")
    merged = pd.merge(ids, data, on="item_id", how="inner", suffixes=("", "_dup"))

    rows = []
    skipped = 0
    for _, r in merged.iterrows():
        num_ratings = _clean_int(r.get("number_of_ratings"))
        price_vnd = _clean_int(r.get("discount_price")) // 100000   # ÷100000 → VND thật
        # ✅ LỌC: bỏ phụ kiện/linh kiện/rác, chỉ giữ điện thoại nguyên chiếc
        if not _is_real_phone(r.get("name", ""), num_ratings, price_vnd):
            skipped += 1
            continue

        brand_raw = str(r.get("brand", "")).strip()
        has_brand = brand_raw and brand_raw.lower() != "nan"
        rows.append((
            int(r["item_id"]),
            _clean_int(r.get("shop_id")),
            str(r.get("shop_location", ""))[:100],
            str(r["name"])[:255],
            (brand_raw if has_brand else "Unknown")[:50],
            (brand_raw.lower() if has_brand else "unknown")[:50],
            _clean_int(r.get("sold_quantity")),
            _clean_int(r.get("stock")),
            price_vnd,
            _clean_int(r.get("original_price")) // 100000,
            str(r.get("discount", ""))[:10],
            _clean_int(r.get("liked_count")),
            round(float(r.get("rating_star", 0) or 0), 2),
            num_ratings,
            str(r.get("product_image_link", "")),
        ))
    print(f"   🧹 Đã loại {skipped} sản phẩm phụ kiện/linh kiện/rác.")

    cur = conn.cursor()
    cur.execute("DELETE FROM products")
    conn.commit()
    _insert_in_batches(conn, """
        INSERT INTO products
        (item_id, shop_id, shop_location, name, brand, brand_normalized,
         sold_quantity, stock, price, original_price, discount, liked_count,
         rating_star, number_of_ratings, product_image_link)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, rows)
    print(f"   ✅ {len(rows)} sản phẩm.")
    return {row[0] for row in rows}  # tập item_id hợp lệ


def import_reviews(conn, valid_item_ids):
    print("💬 Nạp reviews...")
    # Ưu tiên file đã có sentiment nếu tồn tại
    use_ai = os.path.exists(AI_REVIEWS_PATH)
    src = AI_REVIEWS_PATH if use_ai else EXCEL_PATH
    df = pd.read_excel(src, sheet_name=0 if use_ai else "RATING")
    print(f"   Nguồn: {src} ({'có' if use_ai else 'chưa có'} sentiment)")

    rows = []
    for _, r in df.iterrows():
        item_id = _clean_int(r.get("item_id"), default=-1)
        if item_id not in valid_item_ids:
            continue  # bỏ review mồ côi (không khớp sản phẩm nào)

        comment = r.get("comment")
        comment = None if (comment is None or (isinstance(comment, float) and math.isnan(comment))) else str(comment)

        if use_ai:
            sentiment = str(r.get("AI_DanhGia", "")).replace("🟢", "").replace("🔴", "").replace("🟡", "").strip() or None
            confidence = r.get("AI_DoTuTin")
            confidence = None if (confidence is None or (isinstance(confidence, float) and math.isnan(confidence))) else round(float(confidence), 4)
            analyzed = sentiment is not None
        else:
            sentiment, confidence, analyzed = None, None, False

        rows.append((
            _clean_int(r.get("cmt_id")),
            item_id,
            _clean_int(r.get("order_id")),
            str(r.get("user_name", ""))[:100],
            comment,
            _clean_int(r.get("rating_star")),
            sentiment,
            confidence,
            analyzed,
        ))

    cur = conn.cursor()
    cur.execute("DELETE FROM reviews")
    conn.commit()
    _insert_in_batches(conn, """
        INSERT INTO reviews
        (cmt_id, item_id, order_id, user_name, comment, rating_star,
         ai_sentiment, ai_confidence, is_analyzed)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, rows)
    print(f"   ✅ {len(rows)} bình luận.")


if __name__ == "__main__":
    conn = mysql.connector.connect(**DB_CONFIG)
    try:
        valid_ids = import_products(conn)
        import_reviews(conn, valid_ids)
        print("\n🎉 Hoàn tất. Nhớ chạy schema_mysql.sql TRƯỚC để tạo bảng.")
    finally:
        conn.close()