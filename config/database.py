# config/database.py
import os
from pymongo import MongoClient
from dotenv import load_dotenv
import mysql.connector
from mysql.connector import pooling

load_dotenv()

# ============================================================
# MongoDB — CHỈ còn giữ luồng hành vi (user_tracking)
# ============================================================
uri = os.getenv("MONGODB_URL")
mongo_client = MongoClient(uri)

# ✅ ĐỔI TÊN: shopee_recommendation_db → shop_recommendation_db
# Mongo không đổi tên DB tại chỗ; chỉ cần trỏ tên mới, DB mới tự tạo khi ghi.
# Sau khi chạy ổn, bạn xóa hẳn DB cũ "shopee_recommendation_db" trên Atlas.
db = mongo_client["shop_recommendation_db"]
tracking_collection = db["user_tracking"]

# ============================================================
# MySQL — nguồn sự thật: products, reviews, users, orders, order_items
# ============================================================
MYSQL_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "database": os.getenv("MYSQL_DB", "vsengmobile_store_db"),
}

# Dùng connection pool để không phải mở kết nối mới mỗi truy vấn
_mysql_pool = pooling.MySQLConnectionPool(pool_name="rec_pool", pool_size=5, **MYSQL_CONFIG)


def get_mysql():
    """
    Lấy 1 connection từ pool. LUÔN .close() sau khi dùng (close = trả lại pool).
    Dùng kèm dictionary=True ở cursor để lấy kết quả dạng dict cho dễ đọc.
    """
    return _mysql_pool.get_connection()