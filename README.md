# 📱 Gợi Ý Sản Phẩm – VsengMobile

![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![MySQL](https://img.shields.io/badge/MySQL-8.x-4479A1?style=for-the-badge&logo=mysql&logoColor=white)
![MongoDB](https://img.shields.io/badge/MongoDB-Atlas-47A248?style=for-the-badge&logo=mongodb&logoColor=white)
![scikit-learn](https://img.shields.io/badge/scikit--learn-1.4+-F7931E?style=for-the-badge&logo=scikitlearn&logoColor=white)
![XGBoost](https://img.shields.io/badge/XGBoost-2.0+-EB0028?style=for-the-badge)

**Hệ thống gợi ý sản phẩm thông minh cho sàn thương mại điện tử bán điện thoại**
sử dụng Item-based Collaborative Filtering, Behavior-based Personalization và XGBoost Ranking

---

## 📖 Giới thiệu

**VsengMobile Recommendation System** là backend hệ thống **gợi ý sản phẩm thông minh** cho ứng dụng thương mại điện tử chuyên bán điện thoại, được xây dựng với **FastAPI**, kết hợp nhiều engine AI chạy trên dữ liệu hành vi thật của người dùng.

Hệ thống gợi ý hoạt động dựa trên **3 engine chính** (+ 1 job cảm xúc):

| Engine | Mô tả |
|---|---|
| 🔥 **Trending (Cold-start)** | Gợi ý sản phẩm thịnh hành cho khách chưa đăng nhập / chưa có tương tác |
| 🎯 **Engine ① – Item-based CF** | Tìm sản phẩm tương tự bằng ma trận cosine similarity huấn luyện từ hành vi người dùng |
| 🧭 **Engine ② – Behavior-based** | Cá nhân hóa trang chủ theo hành vi thật: brand, từ khóa search, khoảng giá, sentiment |
| 🌲 **Engine ③ – XGBoost** | Mô hình phân loại XGBoost xếp hạng sản phẩm phù hợp cho từng user |
| 💬 **PhoBERT Sentiment** | Job chạy đêm chấm điểm cảm xúc bình luận tiếng Việt, cấp điểm cho Engine ② |

> **Nguyên tắc vàng:** Hành vi cá nhân LUÔN thắng thống kê nhóm — không hardcode định kiến "nam thích Samsung", mọi ưu tiên đều học từ dữ liệu.

---

## 🏗️ Kiến trúc hệ thống

```
┌────────────────────────────────────────────────────────────┐
│                     FastAPI Backend 🚀                     │
│                                                            │
│  ┌─────────────┐    ┌───────────────────────────────────┐  │
│  │   Client    │    │           API Routes              │  │
│  │  (App /     │◄──►│  /api/homepage-feed               │  │
│  │  Frontend)  │    │  /api/recommend/{item_id}         │  │
│  └─────────────┘    │  /api/track-behavior · /products  │  │
│                     └──────────────┬────────────────────┘  │
│                                    │                       │
│                     ┌──────────────▼────────────────────┐  │
│                     │      Recommendation Engines       │  │
│                     │  • Trending (Cold-start)          │  │
│                     │  • Engine ① Cosine Similarity     │  │
│                     │  • Engine ② Behavior-based        │  │
│                     │    (brand/search/price/sentiment) │  │
│                     │  • Engine ③ XGBoost Ranking       │  │
│                     └──────────────┬────────────────────┘  │
│                                    │                       │
│          ┌─────────────────────────▼─────────────────────┐ │
│          │                Data Layer                     │ │
│          │  MySQL: products·reviews·users                │ │
│          │  MongoDB: user_tracking (hành vi)             │ │
│          │  ml_models/*.json (ma trận + model XGBoost)   │ │
│          └───────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────┘
```

---

## 📁 Cấu trúc dự án

```
vsengmobile-ai-backend/
├── main.py                       # Khởi tạo FastAPI, mount routers
├── predict.py                    # Engine ① — cosine similarity
├── recommendation_logic.py       # Engine ② — bộ não cá nhân hóa theo hành vi
│
├── controllers/                  # API Routes
│   ├── product_controller.py     # Trang chủ, tìm kiếm, chi tiết, cold-start
│   ├── tracking_controller.py    # Tracking hành vi + homepage-feed
│   └── debug_controller.py       # Soi Engine ①② (chỉ đọc)
│
├── config/
│   └── database.py               # Kết nối MySQL (pool) + MongoDB
│
├── models/                       # Pydantic models
│   ├── product.py
│   └── tracking.py
│
├── ml_models/                    # Model đã huấn luyện
│   ├── shop_recommendation_model.json    # Ma trận cosine (Engine ①)
│   └── xgb_product_classifier_model.json # Model XGBoost (Engine ③)
│
├── scripts/                      # Huấn luyện & tiện ích
│   ├── retrain_model.py          # Huấn luyện lại Engine ①
│   ├── nightly_bert_update.py    # Job đêm chấm sentiment PhoBERT
│   ├── evaluate_engine3.py       # Đánh giá Engine ③
│   ├── data_prep.py              # Chuẩn bị dữ liệu
│   ├── import_to_mysql.py        # Nạp dữ liệu vào MySQL
│   └── ...
│
├── data/                         # Dữ liệu mẫu
│   ├── data.xlsx                 # Sản phẩm điện thoại
│   ├── data_danh_gia_AI_HoanChinh.xlsx  # Đánh giá đã chấm cảm xúc AI
│   └── users_1000.csv            # 1000 user giả lập
│
├── requirements.txt
└── .env.example                  # Mẫu cấu hình môi trường
```

---

## ⚙️ Cài đặt & Chạy

### Yêu cầu hệ thống

- **Python** ≥ 3.10
- **MySQL** 8.x (database `vsengmobile_store_db`)
- **MongoDB** (Atlas hoặc local) cho tracking hành vi

### Bước 1: Clone repository

```bash
git clone https://github.com/luunguyenquocviet-hub/vsengmobile-ai-backend.git
cd vsengmobile-ai-backend
```

### Bước 2: Tạo file cấu hình môi trường

```bash
# Copy file mẫu rồi điền thông tin MySQL / MongoDB của bạn
cp .env.example .env
```

### Bước 3: Cài đặt dependencies

```bash
pip install -r requirements.txt
```

### Bước 4: Nạp dữ liệu & chạy server

```bash
# Nạp dữ liệu mẫu vào MySQL (lần đầu)
python scripts/import_to_mysql.py

# Chạy server phát triển
uvicorn main:app --reload
```

Mở trình duyệt và truy cập: **http://localhost:8000/docs** (Swagger UI)

---

## 🔌 API Endpoints

### `GET /api/homepage-feed` — gợi ý trang chủ cá nhân hóa

| Tham số | Bắt buộc | Mô tả |
|---|---|---|
| `user_id` | ❌ | Có → Engine ②③ cá nhân hóa; không → Trending (cold-start) |
| `limit` | ❌ | Số sản phẩm trả về |

### Các endpoint chính khác

```bash
# Sản phẩm tương tự (Engine ① — trang chi tiết, fallback Engine ③)
GET /api/recommend/{item_id}

# Ghi nhận hành vi (view / click / add_to_cart / buy / skip ...)
POST /api/track-behavior

# Tìm kiếm & danh sách sản phẩm
GET /api/products/search?q=iphone
GET /api/products · GET /api/products/{item_id}

# Trang chủ phụ trợ
GET /api/top-trending · GET /api/recently-viewed?user_id=...

# Bình luận (ghi vào MySQL, chờ job PhoBERT chấm cảm xúc)
POST /api/comments

# Soi engine (debug, chỉ đọc)
GET /api/debug/engine1/{item_id} · GET /api/debug/engine2/{user_id}
```

**Response mẫu** (`GET /api/recommend/155310209`):

```json
{
  "status": "success",
  "source": "engine1_cf",
  "target_item": "155310209",
  "recommendations": {
    "155310210": 0.9231,
    "155310215": 0.8874,
    "155310208": 0.8412
  }
}
```

---

## 🧠 Chi tiết thuật toán gợi ý

### 1. Trending (Cold-start)

Khách chưa đăng nhập / chưa tương tác → xếp hạng sản phẩm theo tổng trọng số hành vi toàn shop, đảm bảo luôn có gợi ý ngay từ lần truy cập đầu.

### 2. Engine ① — Item-based Collaborative Filtering

1. Gom hành vi user–sản phẩm từ MongoDB thành ma trận tương tác
2. Tính **Cosine Similarity** giữa các sản phẩm → lưu `ml_models/shop_recommendation_model.json`
3. Trang chi tiết trả về top N sản phẩm có độ tương đồng cao nhất

### 3. Engine ② — Behavior-based Personalization

Mỗi hành vi có trọng số (`buy` 5.0 · `wishlist` 4.0 · `add_to_cart` 3.0 · `click` 2.0 · `view` 1.0 · `skip` −0.5) kèm **recency decay** (giảm nửa mỗi 3 ngày — gu mới thắng gu cũ). Điểm cuối của mỗi sản phẩm:

| Thành phần | Trọng số |
|---|---|
| Khớp brand user quan tâm | 0.30 |
| Tên khớp từ khóa vừa search | 0.25 |
| % đánh giá tích cực (AI sentiment) | 0.20 |
| Gần khoảng giá user hay xem | 0.15 |
| Độ phổ biến (tie-breaker) | 0.10 |

> Sentiment dùng **Bayesian smoothing** — chống "ít review nhưng toàn 5 sao leo top". User mới chưa có hành vi → fallback nhân khẩu học (học từ dữ liệu nhóm cùng tuổi/giới tính, không hardcode).

### 4. Engine ③ — XGBoost Ranking

Mô hình phân loại XGBoost huấn luyện trên đặc trưng user × sản phẩm, dự đoán xác suất phù hợp để xếp hạng feed trang chủ (`POST /api/predict-home`).

---

## 🐍 Huấn luyện & kiểm tra (offline)

Các script độc lập, không cần chạy server:

```bash
python scripts/retrain_model.py        # Huấn luyện lại ma trận Engine ①
python scripts/nightly_bert_update.py  # Job đêm: PhoBERT chấm cảm xúc review mới
python scripts/evaluate_engine3.py     # Đánh giá độ chính xác Engine ③
python scripts/test_engine3.py         # Test nhanh Engine ③
```

> Job PhoBERT đọc các review `is_analyzed=FALSE` trong MySQL, gán nhãn 🟢/🔴 rồi ghi ngược lại — Engine ② dùng tỉ lệ tích cực này làm điểm sentiment.

---

## 📊 Dữ liệu

Dữ liệu mẫu được lưu trong `data/`:

| File | Nội dung |
|---|---|
| `data.xlsx` | Danh mục sản phẩm điện thoại (tên, brand, giá, mô tả) |
| `data_danh_gia_AI_HoanChinh.xlsx` | Đánh giá người dùng đã chấm cảm xúc bằng AI |
| `users_1000.csv` | 1000 user giả lập (tuổi, giới tính) cho fallback nhân khẩu học |

---

## 🛠️ Tech Stack

| Công nghệ | Phiên bản | Mục đích |
|---|---|---|
| FastAPI | 0.110+ | API server |
| Uvicorn | 0.29+ | ASGI server |
| MySQL | 8.x | Nguồn sự thật: products, reviews, users |
| MongoDB | Atlas | Lưu hành vi người dùng (tracking) |
| pandas / numpy | 2.1+ / 1.26+ | Xử lý dữ liệu |
| scikit-learn | 1.4+ | Cosine similarity, train/test split, metrics |
| XGBoost | 2.0+ | Engine ③ — ranking |
| transformers + torch | 4.38+ / 2.1+ | PhoBERT — phân tích cảm xúc tiếng Việt |

---

## 👨‍💻 Tác giả

**Lưu Nguyễn Quốc Việt** – [@luunguyenquocviet-hub](https://github.com/luunguyenquocviet-hub)

---

<div align="center">
Made with ❤️ · FastAPI · Python · scikit-learn
</div>
