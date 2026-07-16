# setup_mongodb.py
"""
Tạo collection MongoDB cho phần LUỒNG HÀNH VI (telemetry).
Chỉ MỘT collection sống ở Mongo: user_tracking.
Mọi dữ liệu giao dịch (users, products, orders, reviews) đã ở MySQL.

  user_tracking: mỗi document = 1 sự kiện hành vi của 1 user
    { user_id, action, item_id?, search_query?, timestamp }
    action ∈ view | click | search | add_to_cart | add_to_wishlist | buy | skip

Chạy:  python setup_mongodb.py
Yêu cầu:  config/database.py trỏ tới Atlas (biến db), pip install pymongo
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # chạy được từ mọi nơi
from config.database import db


def setup_user_tracking():
    name = "user_tracking"
    existing = db.list_collection_names()

    if name not in existing:
        # Validator nhẹ: bắt buộc user_id, action, timestamp; item_id/search_query tùy chọn
        db.create_collection(name, validator={
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["user_id", "action", "timestamp"],
                "properties": {
                    "user_id": {"bsonType": ["int", "long"]},
                    "action": {
                        "enum": ["view", "click", "search", "add_to_cart",
                                 "add_to_wishlist", "wishlist", "buy", "skip"]
                    },
                    "item_id": {"bsonType": ["int", "long", "null"]},
                    "search_query": {"bsonType": ["string", "null"]},
                    "timestamp": {"bsonType": ["string", "date"]},
                }
            }
        })
        print(f"✅ Tạo collection '{name}' kèm schema validation.")
    else:
        print(f"ℹ️  Collection '{name}' đã tồn tại — bỏ qua tạo mới.")

    coll = db[name]
    # Index phục vụ truy vấn gợi ý
    coll.create_index("user_id")                       # lấy hành vi của 1 user
    coll.create_index([("user_id", 1), ("timestamp", -1)])  # hành vi gần nhất của user
    coll.create_index("item_id")                       # trending theo sản phẩm
    coll.create_index("action")                        # lọc theo loại hành vi
    print("✅ Đã tạo index cho user_tracking.")


if __name__ == "__main__":
    setup_user_tracking()
    print("\n🎉 MongoDB sẵn sàng. user_tracking nhận event qua POST /api/track-behavior.")