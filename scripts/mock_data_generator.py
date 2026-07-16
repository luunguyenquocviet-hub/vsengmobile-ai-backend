# mock_data_generator.py
"""
Sinh hành vi giả lập vào MongoDB (user_tracking) để test gợi ý.
item_id lấy từ MySQL products (sản phẩm thật) để /homepage-feed enrich được.

Chạy: python mock_data_generator.py
"""
import random
from datetime import datetime, timedelta
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # chạy được từ mọi nơi
from config.database import tracking_collection, get_mysql

SEARCH_QUERIES = ["samsung", "iphone", "nokia", "xiaomi", "oppo", "điện thoại giá rẻ"]
ACTIONS = (["view"] * 40 + ["click"] * 25 + ["search"] * 15 +
           ["add_to_cart"] * 10 + ["add_to_wishlist"] * 8 + ["buy"] * 2)
USER_IDS = list(range(1, 51))


def _load_item_ids(sample_size: int = 100) -> list[int]:
    """Lấy item_id thật từ MySQL products."""
    conn = get_mysql()
    try:
        cur = conn.cursor()
        cur.execute("SELECT item_id FROM products LIMIT 500")
        ids = [row[0] for row in cur.fetchall()]
    finally:
        conn.close()
    if not ids:
        print("⚠️ products trống — chạy import_to_mysql.py trước.")
        return list(range(1, 52))
    return random.sample(ids, min(sample_size, len(ids)))


def generate_mock_data(num_records: int = 1000) -> list[dict]:
    item_ids = _load_item_ids()
    data = []
    for _ in range(num_records):
        past = datetime.now() - timedelta(days=random.randint(0, 7), minutes=random.randint(0, 1440))
        action = random.choice(ACTIONS)
        ev = {"user_id": random.choice(USER_IDS), "action": action, "timestamp": past.isoformat()}
        if action == "search":
            ev["search_query"] = random.choice(SEARCH_QUERIES)
        else:
            ev["item_id"] = random.choice(item_ids)
        data.append(ev)
    return data


if __name__ == "__main__":
    data = generate_mock_data(1000)
    tracking_collection.insert_many(data)
    print(f"✅ Đã ghi {len(data)} hành vi giả lập vào user_tracking (Mongo).")