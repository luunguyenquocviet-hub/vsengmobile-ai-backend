# controllers/product_controller.py
from fastapi import APIRouter, HTTPException
from typing import List, Optional, Optional
from models.product import ProductInfo, predict_batch_products, predict_proba_from_real_vnd
from config.database import get_mysql

router = APIRouter()


@router.post("/predict-home", tags=["⚙️ AI nội bộ (Engine ③)"])
def predict_home_recommendations(products: List[ProductInfo]):
    try:
        predictions_list = predict_batch_products(products)
    except RuntimeError as e:
        # Model XGBoost chưa được train/xuất file — báo lỗi rõ thay vì 500 mù mờ
        raise HTTPException(status_code=503, detail=str(e))

    recommended_indexes = [i for i, liked in enumerate(predictions_list) if liked]
    return {
        "success": True,
        "total_received": len(products),
        "total_recommended": len(recommended_indexes),
        "recommended_item_indexes": recommended_indexes,
    }


# ============================================================
# Engine ③ — FALLBACK COLD-START cho TRANG CHI TIẾT
# Dùng khi Engine① (CF) chưa có dữ liệu về sản phẩm đang xem (SP mới, chưa ai tương tác).
# Lấy sản phẩm CÙNG BRAND (tương đồng nội dung thô) rồi xếp theo điểm chất lượng Engine③.
# Không có brand → lấy nhóm bán chạy làm ứng viên. price/original_price từ MySQL ĐÃ là VND thật.
# ============================================================
_ENGINE3_FEATURES = ("discount_price", "original_price", "liked_count",
                     "number_of_ratings", "sold_quantity", "stock")


def coldstart_recommend(item_id: str, top_n: int = 6) -> list[dict]:
    conn = get_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT brand_normalized FROM products WHERE item_id=%s", (item_id,))
        row = cur.fetchone()
        brand = (row or {}).get("brand_normalized")

        cols = """item_id, name, brand, price, product_image_link AS image,
                  price AS discount_price, original_price, liked_count,
                  number_of_ratings, sold_quantity, stock"""
        cands = []
        if brand and brand != "unknown":
            cur.execute(f"SELECT {cols} FROM products "
                        f"WHERE brand_normalized=%s AND item_id<>%s LIMIT 200",
                        (brand, item_id))
            cands = cur.fetchall()
        if not cands:
            # Brand lạ HOẶC "một mình một hãng" (không còn máy nào cùng brand)
            # → lấy nhóm bán chạy làm ứng viên, tránh trả list rỗng ra app
            cur.execute(f"SELECT {cols} FROM products WHERE item_id<>%s "
                        f"ORDER BY sold_quantity DESC LIMIT 200", (item_id,))
            cands = cur.fetchall()
    finally:
        conn.close()

    if not cands:
        return []

    feats = [{k: float(c.get(k) or 0) for k in _ENGINE3_FEATURES} for c in cands]
    try:
        scores = predict_proba_from_real_vnd(feats)
    except RuntimeError:
        scores = [0.0] * len(cands)  # model chưa sẵn sàng → vẫn trả ứng viên (điểm 0)

    for c, s in zip(cands, scores):
        c["engine3_score"] = round(float(s), 4)
        for k in _ENGINE3_FEATURES:
            c.pop(k, None)  # bỏ feature thô, chỉ giữ info hiển thị + điểm
    cands.sort(key=lambda x: x["engine3_score"], reverse=True)

    # KHỬ TRÙNG MODEL: dữ liệu Shopee có cùng một máy được NHIỀU shop đăng
    # ("[Giá Sốc] Samsung J2 Pro..." / "Điện thoại samsung j2 pro..."). Nếu không khử,
    # mục "tương tự" thành 6 listing của cùng 1-2 model. Chuẩn hóa tên → mỗi model 1 chỗ.
    _STOP = {"điện", "thoại", "dien", "thoai", "chính", "hãng", "chinh", "hang",
             "giá", "sốc", "gia", "soc", "máy", "may", "mới", "moi", "cũ", "cu",
             "đẹp", "dep", "zin", "sim", "2sim", "ram", "gb", "bộ", "nhớ", "bo",
             "nho", "bảo", "hành", "bao", "hanh", "hàng", "likenew", "fullbox"}

    def _model_key(name: str) -> str:
        import re as _re
        n = _re.sub(r"[\[\(].*?[\]\)]", " ", str(name).lower())  # bỏ [..] (..)
        toks = [t for t in _re.findall(r"[a-z0-9]+", n) if t not in _STOP]
        return " ".join(toks[:4]) or n.strip()[:30]

    seen_models, unique, dup = set(), [], []
    for c in cands:
        k = _model_key(c.get("name", ""))
        (unique if k not in seen_models else dup).append(c)
        seen_models.add(k)
    if len(unique) < top_n:  # thiếu model khác nhau → đành lấy lại listing trùng
        unique.extend(dup[: top_n - len(unique)])
    return unique[:top_n]


# ============================================================
# API SẢN PHẨM cho app/frontend — ⚠️ THỨ TỰ ROUTE QUAN TRỌNG:
# /products/search phải khai báo TRƯỚC /products/{item_id}; nếu ngược lại,
# chữ "search" bị FastAPI hiểu là một item_id → search luôn trả 422.
# (price trong MySQL ĐÃ là VND thật — trả thẳng, không đổi đơn vị)
# ============================================================
_PRODUCT_COLS = """item_id, name, brand, price, original_price,
                   product_image_link AS image, liked_count,
                   number_of_ratings, sold_quantity, stock"""


def _rows_to_json(rows: list[dict]) -> list[dict]:
    for r in rows:
        for k in ("price", "original_price"):
            if r.get(k) is not None:
                r[k] = float(r[k])
    return rows


@router.get("/products/search", tags=["🔎 Tìm kiếm & Sản phẩm"])
def search_products(q: str, limit: int = 20, offset: int = 0):
    """Tìm theo tên hoặc hãng (LIKE), ưu tiên bán chạy. Ô tìm kiếm của app gọi vào đây.
    Hỗ trợ phân trang: ?limit=20&offset=20 → trang 2 (app cuộn xuống tự nạp thêm)."""
    like = f"%{q.strip()}%"
    conn = get_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(f"""SELECT {_PRODUCT_COLS} FROM products
                        WHERE name LIKE %s OR brand LIKE %s
                        ORDER BY sold_quantity DESC LIMIT %s OFFSET %s""",
                    (like, like, min(int(limit), 50), max(0, int(offset))))
        rows = cur.fetchall()
    finally:
        conn.close()
    return {"status": "success", "count": len(rows), "data": _rows_to_json(rows)}


@router.get("/products", tags=["🔎 Tìm kiếm & Sản phẩm"])
def get_products(ids: str = "", search: Optional[str] = None,
                 brand: Optional[str] = None, limit: int = 20, offset: int = 0):
    """Hai chế độ trong một endpoint:
    • ?ids=1,2,3 → tra đúng các sản phẩm đó (app dùng để 'tô thịt' kết quả CF Engine①)
    • không có ids → danh sách (lọc search/brand tùy chọn), xếp theo bán chạy
    """
    conn = get_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        if ids.strip():
            id_list = [int(x) for x in ids.split(",") if x.strip().lstrip("-").isdigit()]
            if not id_list:
                return {"status": "success", "data": []}
            ph = ",".join(["%s"] * len(id_list))
            cur.execute(f"SELECT {_PRODUCT_COLS} FROM products WHERE item_id IN ({ph})", id_list)
        else:
            where, params = [], []
            if search:
                where.append("name LIKE %s"); params.append(f"%{search}%")
            if brand:
                where.append("brand_normalized = %s"); params.append(brand.strip().lower())
            wsql = ("WHERE " + " AND ".join(where)) if where else ""
            cur.execute(f"SELECT {_PRODUCT_COLS} FROM products {wsql} "
                        f"ORDER BY sold_quantity DESC LIMIT %s OFFSET %s",
                        (*params, max(1, min(int(limit), 50)), max(0, int(offset))))
        rows = cur.fetchall()
    finally:
        conn.close()
    return {"status": "success", "count": len(rows), "data": _rows_to_json(rows)}


@router.get("/products/{item_id}", tags=["📱 Trang chi tiết"])
def product_detail(item_id: int):
    """Chi tiết 1 sản phẩm + 10 bình luận gần nhất (kèm cảm xúc AI nếu đã chấm).
    Khai báo SAU /products/search — xem cảnh báo thứ tự route ở trên."""
    conn = get_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(f"SELECT {_PRODUCT_COLS} FROM products WHERE item_id = %s", (item_id,))
        p = cur.fetchone()
        if not p:
            raise HTTPException(status_code=404, detail="Không tìm thấy sản phẩm")
        cur.execute(
            """SELECT user_name, rating_star, comment, ai_sentiment, ai_confidence
               FROM reviews
               WHERE item_id = %s AND comment IS NOT NULL AND comment <> ''
               ORDER BY id DESC LIMIT 10""",
            (item_id,),
        )
        reviews = cur.fetchall()
    finally:
        conn.close()
    return {"status": "success", "data": _rows_to_json([p])[0], "reviews": reviews}