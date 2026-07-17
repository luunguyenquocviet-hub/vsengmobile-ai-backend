from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import datetime
import predict
from controllers.tracking_controller import router as tracking_router
from controllers.product_controller import router as product_router, coldstart_recommend
from controllers.debug_controller import router as debug_router      # 🔬 soi Engine ② (chỉ đọc)
from config.database import get_mysql

app = FastAPI(title="VsengMobile Hybrid AI Backend 🚀")

# CORS: cho phép trang test (mở từ file://) và frontend gọi API từ trình duyệt.
# Đang mở "*" cho dễ dev/test — khi triển khai thật nên giới hạn về domain của frontend.
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tracking_router, prefix="/api")
app.include_router(product_router, prefix="/api")
app.include_router(debug_router, prefix="/api")


class CommentRequest(BaseModel):
    user_id: int
    product_id: int
    noi_dung: str
    so_sao: int


@app.get("/", tags=["⚙️ Hệ thống"])
def home():
    return {"status": "Online", "message": "Trạm trung chuyển AI VsengMobile sẵn sàng!"}


# --- NHẬN BÌNH LUẬN MỚI → ghi vào MySQL reviews (is_analyzed=FALSE để nightly job chấm sau) ---
@app.post("/api/comments", tags=["📱 Trang chi tiết"])
def save_comment(data: CommentRequest):
    if not (1 <= data.so_sao <= 5):
        raise HTTPException(status_code=400, detail="Số sao phải từ 1 đến 5!")

    conn = get_mysql()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO reviews (item_id, user_name, comment, rating_star, is_analyzed)
            VALUES (%s, %s, %s, %s, FALSE)
        """, (data.product_id, str(data.user_id), data.noi_dung, data.so_sao))
        conn.commit()
    finally:
        conn.close()
    return {"status": "success", "message": "Đã lưu bình luận vào MySQL (chờ AI chấm điểm)"}


# --- GỢI Ý SẢN PHẨM TƯƠNG ĐỒNG (trang chi tiết) ---
# Engine① (CF) lo "sản phẩm tương tự". Nếu SP mới chưa có dữ liệu CF → fallback Engine③.

# 🔧 SỐ SẢN PHẨM TƯƠNG TỰ hiển thị ở trang chi tiết (cả Engine① lẫn Engine③).
# Muốn hiện nhiều/ít hơn → đổi MỘT chỗ này là đủ.
SO_GOI_Y_TUONG_TU = 6


@app.get("/api/recommend/{item_id}", tags=["📱 Trang chi tiết"])
def get_recommendations(item_id: str):
    # Lấy DƯ (top 20) vì bước sau sẽ lọc bớt id "mồ côi"
    ket_qua = predict.recommend_similar_items(item_id, top_n=20)

    # Engine① có dữ liệu tương đồng → LỌC MỒ CÔI rồi mới dùng.
    # Ma trận CF học từ hành vi Mongo nên chứa cả item đã bị loại khỏi MySQL
    # (phụ kiện/rác lọc lúc import). Không lọc → app tra thông tin không ra
    # → ô "sản phẩm tương tự" trống dù API có trả kết quả.
    if isinstance(ket_qua, dict) and ket_qua:
        conn = get_mysql()
        try:
            cur = conn.cursor()
            placeholders = ",".join(["%s"] * len(ket_qua))
            cur.execute(f"SELECT item_id FROM products WHERE item_id IN ({placeholders})",
                        list(ket_qua.keys()))
            ton_tai = {str(row[0]) for row in cur.fetchall()}
        finally:
            conn.close()

        hop_le = {k: v for k, v in ket_qua.items() if str(k) in ton_tai}
        top_n = dict(sorted(hop_le.items(), key=lambda kv: kv[1],
                            reverse=True)[:SO_GOI_Y_TUONG_TU])
        if top_n:
            return {"status": "success", "source": "engine1_cf",
                    "target_item": item_id, "recommendations": top_n}
        # toàn bộ id tương tự đều mồ côi → coi như Engine① mù, rơi xuống Engine③

    # Cold-start: Engine① mù vì sản phẩm mới → Engine③ gợi ý cùng brand, xếp theo chất lượng
    return {"status": "success", "source": "engine3_coldstart",
            "target_item": item_id,
            "recommendations": coldstart_recommend(item_id, top_n=SO_GOI_Y_TUONG_TU)}