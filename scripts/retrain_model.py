# retrain_model.py
import os
from dotenv import load_dotenv
from pymongo import MongoClient
import pandas as pd
from sklearn.metrics.pairwise import cosine_similarity

# 1. Kết nối database
load_dotenv()
client = MongoClient(os.getenv("MONGODB_URL"))
tracking_collection = client["shop_recommendation_db"]["user_tracking"]

# --- GIAI ĐOẠN 1: KÉO DỮ LIỆU (EXTRACT) ---
print("📥 Đang tải dữ liệu huấn luyện từ MongoDB...")

# Thiết lập bộ lọc: Chỉ lấy những document mà trường 'item_id' có tồn tại (True)
query = {"item_id": {"$exists": True}} 

# Thực thi lệnh find() với bộ lọc, và biến kết quả thành một danh sách (list)
raw_data = list(tracking_collection.find(query))

print(f"✅ Đã tải về {len(raw_data)} lượt tương tác hợp lệ (đã bỏ qua các lượt search).")

# --- GIAI ĐOẠN 2: TIÊU HÓA VÀ HỌC HỎI (TRAIN) ---
print("🧠 Đang xếp dữ liệu vào bảng để phân tích...")

# Biến danh sách thô thành bảng DataFrame của Pandas
df = pd.DataFrame(raw_data)

# In thử 5 dòng đầu tiên ra màn hình để kiểm tra
print(df.head())

# 1. Tạo từ điển thang điểm của bạn
score_mapping = {
    "view": 1,
    "click": 2,
    "add_to_wishlist": 3,
    "add_to_cart": 4,
    "buy": 5
}

# 2. Tạo thêm một cột mới tên là 'score' để dịch chữ thành số
df["score"] = df["action"].map(score_mapping)

print("\n📊 Bảng dữ liệu sau khi quy đổi thành điểm số:")
# Cắt lấy 3 cột quan trọng nhất để xem
print(df[["user_id", "item_id", "action", "score"]].head(10))

# 3. Gom nhóm theo User và Item, sau đó giữ lại điểm hành vi cao nhất
final_df = df.groupby(['user_id', 'item_id'])['score'].max().reset_index()

# ✅ Ép item_id về SỐ NGUYÊN trước khi pivot — Mongo hay trả float (155310209.0),
# không ép thì tên cột trong file JSON dính đuôi ".0" → predict.py dò id trượt hết.
final_df['item_id'] = final_df['item_id'].astype('int64')
final_df['user_id'] = final_df['user_id'].astype('int64')

print("\n🏆 Bảng dữ liệu tinh gọn cuối cùng (Sẵn sàng cho AI):")
print(final_df.head(10))

# 4. Dàn phẳng dữ liệu thành Ma trận (Pivot Table)
user_item_matrix = final_df.pivot_table(
    index='user_id',
    columns='item_id',
    values='score'
).fillna(0)  # Lấp số 0 vào những ô chưa có tương tác

print("\n🧮 Ma trận Người dùng - Sản phẩm (User-Item Matrix):")
print(user_item_matrix.head())

# 5. Tính toán độ tương đồng giữa các Sản phẩm (Item-Based Collaborative Filtering)
# Lệnh .T (Transpose) giúp xoay ngang ma trận để so sánh các Cột (Sản phẩm) với nhau
item_similarity = cosine_similarity(user_item_matrix.T)

# Chuyển kết quả tính được thành một bảng vuông vức cho dễ nhìn
item_similarity_df = pd.DataFrame(
    item_similarity, 
    index=user_item_matrix.columns, 
    columns=user_item_matrix.columns
)

print("\n🤝 Ma trận Độ tương đồng giữa các Sản phẩm (Item Similarity Matrix):")
print(item_similarity_df.iloc[:5, :5]) # In thử 5 hàng, 5 cột đầu tiên

# 6. Xuất "bộ não" ra file JSON để máy chủ Web sử dụng
# ⚠️ TÊN FILE PHẢI TRÙNG với file predict.py đọc lúc khởi động server.
# Tự nhận biết layout: file nằm trong scripts/ → gốc dự án là thư mục cha;
# có thư mục ml_models/ thì lưu vào đó, không thì lưu ngay gốc (layout cũ).
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here) if os.path.basename(_here) == "scripts" else _here
_mdir = os.path.join(_root, "ml_models")
model_path = os.path.join(_mdir if os.path.isdir(_mdir) else _root,
                          "shop_recommendation_model.json")
item_similarity_df.to_json(model_path)

print(f"\n💾 Đã lưu bộ não AI thành công vào file: {model_path}")