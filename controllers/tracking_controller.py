# controllers/tracking_controller.py
from fastapi import APIRouter
from typing import Optional
from models.tracking import UserBehavior
from config.database import tracking_collection, get_mysql
from datetime import datetime, timedelta
import recommendation_logic as rec
from models.product import predict_proba_from_real_vnd  # Engine ③ — điểm chất lượng (0..1)

router = APIRouter()  # tag gắn theo TỪNG endpoint → Swagger nhóm theo trang

# Collection RIÊNG cho impression (sản phẩm đã HIỂN THỊ cho user trên feed).
# Tách khỏi user_tracking để 10 impression/lần tải feed không đè bẹp
# cửa sổ "100 hành vi gần nhất" mà cá nhân hóa đang đọc.
impressions_collection = tracking_collection.database["feed_impressions"]

POSITIVE_ACTIONS = ["buy", "add_to_cart", "add_to_wishlist", "wishlist", "click", "view"]
POSITIVE_SENTIMENT = "TÍCH CỰC"  # đã lược bỏ emoji khi nạp vào MySQL

# Trọng số tín hiệu chất lượng Engine ③ khi TRỘN vào thứ tự (0..1).
# Càng cao Engine ③ càng ảnh hưởng; để thấp để giữ cá nhân hóa của Engine ② làm chủ đạo.
ENGINE3_WEIGHT = 0.30


# ==========================================
# 📡 GHI NHẬN HÀNH VI → MongoDB (không đổi)
# ==========================================
@router.post("/track-behavior", tags=["👆 Tracking hành vi"])
def track_user_behavior(behavior: UserBehavior):
    behavior_dict = behavior.model_dump()
    tracking_collection.insert_one(behavior_dict)
    behavior_dict.pop('_id', None)
    return {"status": "success", "message": "Đã ghi nhận hành vi 🍃", "data_recorded": behavior_dict}


# ==========================================
# 🛍️ CATALOG — đọc từ MySQL
# ==========================================
def _get_products_by_ids(item_ids: list[int]) -> dict[int, dict]:
    if not item_ids:
        return {}
    conn = get_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        placeholders = ",".join(["%s"] * len(item_ids))
        cur.execute(f"""
            SELECT item_id, name, brand, brand_normalized, price,
                   number_of_ratings, product_image_link AS image
            FROM products WHERE item_id IN ({placeholders})
        """, item_ids)
        # Ép Decimal/None về float/int để recommendation_logic tính toán không lỗi kiểu
        out = {}
        for row in cur.fetchall():
            row["price"] = float(row["price"] or 0)
            row["number_of_ratings"] = int(row["number_of_ratings"] or 0)
            # ⚠️ KEY LUÔN str: Mongo trả item_id SỐ, MySQL trả CHUỖI — không ép
            # một kiểu thì pmap.get() trượt → trending mất tên/ảnh/giá.
            out[str(row["item_id"])] = row
        return out
    finally:
        conn.close()


def _get_all_candidate_phones() -> list[dict]:
    conn = get_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT item_id, name, brand_normalized, price, number_of_ratings
            FROM products
        """)
        rows = cur.fetchall()
        # Ép Decimal/None về float/int cho cả pool ứng viên
        for row in rows:
            row["price"] = float(row["price"] or 0)
            row["number_of_ratings"] = int(row["number_of_ratings"] or 0)
        return rows
    finally:
        conn.close()


def _enrich_with_product_info(items: list[dict]) -> list[dict]:
    ids = [it["item_id"] for it in items if it.get("item_id") is not None]
    pmap = _get_products_by_ids(ids)
    out = []
    for it in items:
        p = pmap.get(str(it.get("item_id")), {})
        out.append({
            **it,
            "name": it.get("name") or p.get("name"),
            "brand": p.get("brand"),
            "price": it.get("price") if it.get("price") is not None else p.get("price"),
            "image": p.get("image"),
        })
    return out


# ==========================================
# 💬 SENTIMENT — đọc từ MySQL reviews (gom theo item_id)
# ==========================================
def _get_sentiment_map(item_ids: list[int]) -> dict[int, dict]:
    if not item_ids:
        return {}
    conn = get_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        placeholders = ",".join(["%s"] * len(item_ids))
        cur.execute(f"""
            SELECT item_id,
                   COUNT(*) AS total,
                   SUM(CASE WHEN ai_sentiment = %s THEN 1 ELSE 0 END) AS positive,
                   AVG(ai_confidence) AS avg_confidence
            FROM reviews
            WHERE item_id IN ({placeholders}) AND is_analyzed = TRUE
            GROUP BY item_id
        """, [POSITIVE_SENTIMENT] + item_ids)

        out = {}
        for r in cur.fetchall():
            total = int(r["total"] or 0)
            positive = int(r["positive"] or 0)
            out[r["item_id"]] = {
                "positive_ratio": round(positive / total, 3) if total else 0.0,
                "total_reviews": total,
                "avg_confidence": round(float(r["avg_confidence"] or 0.0), 3),
            }
        return out
    finally:
        conn.close()


# ==========================================
# 👤 HỒ SƠ USER — đọc từ MySQL users (tuổi/giới tính thật)
# ==========================================
def _get_user_profile(user_id: int) -> Optional[dict]:
    conn = get_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT age, gender FROM users WHERE user_id = %s", (user_id,))
        return cur.fetchone()
    finally:
        conn.close()


# ==========================================
# 🔥 TRENDING + HÀNH VI — đọc từ MongoDB (tracking)
# ==========================================
def _get_trending_items(fetch_n: int = 30) -> list[dict]:
    pipeline = [
        {"$match": {"item_id": {"$ne": None}}},
        {"$group": {"_id": "$item_id", "total_score": {"$sum": {"$switch": {"branches": [
            {"case": {"$eq": ["$action", "add_to_cart"]}, "then": 3.0},
            {"case": {"$eq": ["$action", "search"]}, "then": 2.0},
            {"case": {"$eq": ["$action", "click"]}, "then": 1.0},
            {"case": {"$eq": ["$action", "view"]}, "then": 0.1},
            {"case": {"$eq": ["$action", "skip"]}, "then": -0.5},
        ], "default": 0.0}}}}},
        {"$sort": {"total_score": -1}},
        {"$limit": max(1, int(fetch_n))}
    ]
    raw = list(tracking_collection.aggregate(pipeline))
    return [{"item_id": r["_id"], "trending_score": round(r["total_score"], 2)} for r in raw]


def _get_trending_feed(limit: int = 10, offset: int = 0) -> list[dict]:
    """Trending CHỐNG RÁC: hành vi có thể trỏ vào item_id không tồn tại trong MySQL
    (dữ liệu test cũ, sản phẩm bị lọc khi import...). Nếu cứ trả thẳng, app hiện
    'Sản phẩm #id' không tên không ảnh. Giải pháp: lấy dư ×3 từ Mongo, gắn thông tin,
    LOẠI item không tra được (không có name), rồi mới cắt trang."""
    pool = _get_trending_items(fetch_n=(offset + limit) * 3 + 20)
    enriched = _enrich_with_product_info(pool)
    clean = [x for x in enriched if x.get("name")]
    return clean[offset:offset + limit]


def _get_user_interactions(user_id: int, limit: int = 100) -> list[dict]:
    cur = tracking_collection.find(
        {"user_id": user_id, "item_id": {"$ne": None}},
        {"_id": 0, "action": 1, "item_id": 1, "timestamp": 1}
    ).sort("timestamp", -1).limit(limit)
    return list(cur)


def _get_recent_search_keywords(user_id: int, limit: int = 3) -> list[str]:
    raw = tracking_collection.find(
        {"user_id": user_id, "action": "search", "search_query": {"$ne": None}}
    ).sort("timestamp", -1).limit(limit)
    keywords = []
    for doc in raw:
        kw = (doc.get("search_query") or "").strip()
        if kw and kw not in keywords:
            keywords.append(kw)
    return keywords


def _get_purchased_item_ids(user_id: int) -> set[int]:
    cur = tracking_collection.find(
        {"user_id": user_id, "action": "buy", "item_id": {"$ne": None}},
        {"_id": 0, "item_id": 1}
    )
    return {doc["item_id"] for doc in cur}


def _brands_for_items(item_ids: list) -> dict[str, str]:
    """Tra brand_normalized cho một lô item_id → {str(item_id): brand}."""
    if not item_ids:
        return {}
    conn = get_mysql()
    try:
        c = conn.cursor()
        ph = ",".join(["%s"] * len(item_ids))
        c.execute(f"SELECT item_id, brand_normalized FROM products WHERE item_id IN ({ph})",
                  list(item_ids))
        return {str(r[0]): str(r[1]).strip().lower() for r in c.fetchall() if r[1]}
    finally:
        conn.close()


def _get_recently_purchased_brands(user_id: int,
                                   days: int = rec.POST_PURCHASE_DAYS) -> set[str]:
    """🛒 Hãng vừa MUA trong `days` ngày → hạ nhiệt (post-purchase suppression).

    ⚖️ TỰ GỠ (lift): nếu SAU lần mua gần nhất user VẪN tương tác với hãng đó
    (view/click/thêm giỏ) ≥ POST_PURCHASE_LIFT_MIN_EVENTS lần → họ còn nhu cầu
    (mua hộ, so sánh, đổi máy khác...) → KHÔNG hạ nhiệt hãng đó nữa.
    Tránh cảnh: vừa bấm Mua thử 1 máy Samsung là cả gu Samsung bị đè 14 ngày."""
    since = datetime.now() - timedelta(days=days)
    buys = list(tracking_collection.find(
        {"user_id": user_id, "action": "buy", "item_id": {"$ne": None},
         "timestamp": {"$gte": since}},
        {"_id": 0, "item_id": 1, "timestamp": 1},
    ))
    if not buys:
        return set()

    id2brand = _brands_for_items(list({b["item_id"] for b in buys}))
    brand_last_buy: dict[str, object] = {}
    for b in buys:
        br = id2brand.get(str(b["item_id"]))
        if br:
            cur_ts = brand_last_buy.get(br)
            if cur_ts is None or b["timestamp"] > cur_ts:
                brand_last_buy[br] = b["timestamp"]
    if not brand_last_buy:
        return set()

    # Tương tác SAU thời điểm mua sớm nhất → đếm theo hãng, chỉ tính sau lần mua của hãng đó
    earliest = min(brand_last_buy.values())
    eng = list(tracking_collection.find(
        {"user_id": user_id, "item_id": {"$ne": None},
         "action": {"$in": ["view", "click", "add_to_cart"]},
         "timestamp": {"$gt": earliest}},
        {"_id": 0, "item_id": 1, "timestamp": 1},
    ))
    eng_brand = _brands_for_items(list({e["item_id"] for e in eng})) if eng else {}
    post_buy_cnt: dict[str, int] = {}
    for e in eng:
        br = eng_brand.get(str(e["item_id"]))
        if br in brand_last_buy and e["timestamp"] > brand_last_buy[br]:
            post_buy_cnt[br] = post_buy_cnt.get(br, 0) + 1

    lifted = {b for b, n in post_buy_cnt.items()
              if n >= rec.POST_PURCHASE_LIFT_MIN_EVENTS}
    return set(brand_last_buy) - lifted


def _get_ignored_item_ids(user_id: int) -> set:
    """🙈 SKIP NGẦM: sản phẩm đã hiển thị ≥N lần mà user chưa từng view/click/giỏ/mua."""
    try:
        pipeline = [
            {"$match": {"user_id": user_id}},
            {"$group": {"_id": "$item_id", "n": {"$sum": 1}}},
            {"$match": {"n": {"$gte": rec.IMPLICIT_SKIP_MIN_IMPRESSIONS}}},
        ]
        impressed = {d["_id"] for d in impressions_collection.aggregate(pipeline)
                     if d["_id"] is not None}
        if not impressed:
            return set()
        engaged = tracking_collection.distinct(
            "item_id",
            {"user_id": user_id,
             "action": {"$in": ["view", "click", "add_to_cart", "buy"]}},
        )
        return impressed - set(engaged)
    except Exception:
        return set()  # skip ngầm là tín hiệu phụ — lỗi Mongo không được phá feed


def _log_impressions(user_id, items: list[dict]) -> None:
    """Ghi lại các sản phẩm ĐÃ TRẢ cho user (impression) — nguồn dữ liệu cho skip ngầm."""
    if user_id is None or not items:
        return
    try:
        now = datetime.now()
        docs = [{"user_id": user_id, "item_id": it.get("item_id"), "timestamp": now}
                for it in items if it.get("item_id") is not None]
        if docs:
            impressions_collection.insert_many(docs, ordered=False)
    except Exception:
        pass  # tracking phụ trợ — không bao giờ làm hỏng response


# ==========================================
# 🧮 NHÂN KHẨU HỌC — học từ dữ liệu (MySQL users + Mongo tracking + MySQL products)
# ==========================================
def _age_bucket(age: int) -> tuple[int, int]:
    if age < 23:
        return (0, 23)
    if age > 60:
        return (61, 200)
    return (23, 61)


def _get_demographic_brand_interest(age: int, gender: str) -> dict[str, float]:
    lo, hi = _age_bucket(age)
    # 1. Tìm user cùng nhóm tuổi + giới tính (MySQL)
    conn = get_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT user_id FROM users WHERE gender = %s AND age >= %s AND age < %s",
            (gender, lo, hi)
        )
        peer_ids = [r["user_id"] for r in cur.fetchall()]
    finally:
        conn.close()
    if not peer_ids:
        return {}

    # 2. Hành vi tích cực của nhóm đó (Mongo tracking)
    peer_interactions = list(tracking_collection.find(
        {"user_id": {"$in": peer_ids}, "action": {"$in": POSITIVE_ACTIONS}, "item_id": {"$ne": None}},
        {"_id": 0, "action": 1, "item_id": 1}
    ))
    if not peer_interactions:
        return {}

    # 3. Map item → brand (MySQL products), rồi dùng đúng công thức cũ
    item_ids = list({it["item_id"] for it in peer_interactions})
    products = _get_products_by_ids(item_ids)
    item_brand_map = {pid: p.get("brand_normalized", "") for pid, p in products.items()}
    return rec.compute_brand_interest(peer_interactions, item_brand_map)


# ==========================================
# 🎯 LÕI CÁ NHÂN HÓA
# ==========================================
def _get_personalized_feed(user_id: int, limit: int = 10,
                           offset: int = 0) -> tuple[Optional[list[dict]], str]:
    interactions = _get_user_interactions(user_id)
    recent_keywords = _get_recent_search_keywords(user_id, limit=3)

    interacted_ids = list({it["item_id"] for it in interactions})
    interacted_products = _get_products_by_ids(interacted_ids)
    item_brand_map = {pid: p.get("brand_normalized", "") for pid, p in interacted_products.items()}
    item_price_map = {pid: (p.get("price") or 0) for pid, p in interacted_products.items()}

    brand_interest = rec.compute_brand_interest(interactions, item_brand_map,
                                                now=datetime.now())
    budget_band = rec.compute_budget_band(interactions, item_price_map)

    # 🛒 Câu chuyện post-purchase: vừa mua điện thoại thì nhu cầu mua thêm máy
    # cùng hãng ≈ 0 → giảm boost hãng vừa mua trong 14 ngày (không xóa gu dài hạn).
    brand_interest = rec.apply_post_purchase_suppression(
        brand_interest, _get_recently_purchased_brands(user_id))

    has_behavior = bool(brand_interest) or bool(recent_keywords)
    tier = "behavior_personalized"

    if not has_behavior:
        profile = _get_user_profile(user_id)
        if profile and profile.get("age") and profile.get("gender"):
            brand_interest = _get_demographic_brand_interest(profile["age"], profile["gender"])
            tier = "demographic_coldstart"
        if not brand_interest:
            return (None, tier)

    candidates = _get_all_candidate_phones()
    candidate_ids = [c["item_id"] for c in candidates]
    sentiment_map = _get_sentiment_map(candidate_ids)
    exclude = _get_purchased_item_ids(user_id)

    ranked = rec.rank_candidates(
        candidates=candidates, brand_interest=brand_interest, budget_band=budget_band,
        recent_keywords=recent_keywords, sentiment_map=sentiment_map,
        exclude_item_ids=exclude, limit=len(candidates),  # chấm TOÀN BỘ → đa dạng hóa luôn có nguồn hãng khác
    )
    if not ranked:
        return (None, tier)

    # 🙈 SKIP NGẦM: hạ điểm sản phẩm đã hiển thị nhiều lần mà user không đoái hoài
    ranked = rec.apply_implicit_skip_penalty(ranked, _get_ignored_item_ids(user_id))

    # PHÂN TRANG + ĐA DẠNG HÓA THEO TỪNG TRANG: lặp "chọn 1 trang có quota hãng
    # rồi loại khỏi pool". Nhờ vậy TRANG NÀO cũng đa dạng (không chỉ trang đầu),
    # và cùng dữ liệu → cùng thứ tự → cuộn không bị trùng/sót sản phẩm giữa các trang.
    sequence: list[dict] = []
    remaining = ranked
    while remaining and len(sequence) < offset + limit:
        page = rec.diversify_by_brand(remaining, limit=limit)
        sequence.extend(page)
        picked = {p["item_id"] for p in page}
        remaining = [r for r in remaining if r["item_id"] not in picked]

    window = sequence[offset:offset + limit]
    if not window:
        return ([], tier)  # [] = HẾT trang (khác None = không cá nhân hóa được)
    return (_enrich_with_product_info(window), tier)


# ==========================================
# 🧠 ENGINE ③ — TÍN HIỆU CHẤT LƯỢNG để SẮP THỨ TỰ (không loại sản phẩm)
# (Engine ② quyết định độ phù hợp/cá nhân hóa; Engine ③ thêm điểm chất lượng tinh chỉnh thứ tự)
# ==========================================
def _get_engine3_features(item_ids: list[int]) -> dict[int, dict]:
    """Lấy đúng 6 cột số model cần, từ MySQL. price/original_price ĐÃ là VND thật (÷100000 lúc
    import) nên KHÔNG chia thêm. Đặt alias price→discount_price cho khớp tên feature của model."""
    if not item_ids:
        return {}
    conn = get_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        placeholders = ",".join(["%s"] * len(item_ids))
        cur.execute(f"""
            SELECT item_id, price AS discount_price, original_price,
                   liked_count, number_of_ratings, sold_quantity, stock
            FROM products WHERE item_id IN ({placeholders})
        """, item_ids)
        out = {}
        for r in cur.fetchall():
            for k in ("discount_price", "original_price", "liked_count",
                      "number_of_ratings", "sold_quantity", "stock"):
                r[k] = float(r[k] or 0)
            out[r["item_id"]] = r
        return out
    finally:
        conn.close()


def _rerank_with_engine3(feed: list[dict], limit: int) -> list[dict]:
    """Engine ③ KHÔNG loại sản phẩm. Nó chấm mỗi sản phẩm một điểm chất lượng (xác suất
    'được thích', 0..1) rồi TRỘN NHẸ vào thứ tự mà Engine ② đã sắp:

        final = (1 - w) * điểm_vị_trí_Engine② + w * điểm_Engine③      (w = ENGINE3_WEIGHT)

    'điểm_vị_trí_Engine②' suy từ thứ hạng sẵn có của feed (1.0 cho đầu danh sách → 0.0 cuối),
    nên cách trộn này dùng được cho mọi tier (trending/cá nhân hóa) bất kể thang điểm gốc.
    Gắn engine3_score (0..1) lên mỗi item để minh bạch. Lỗi/model thiếu → trả NGUYÊN feed.
    """
    n = len(feed)
    if n == 0:
        return feed
    try:
        ids = [it["item_id"] for it in feed if it.get("item_id") is not None]
        fmap = _get_engine3_features(ids)
        rows = [fmap.get(it.get("item_id"), {}) for it in feed]
        scores = predict_proba_from_real_vnd(rows)  # [0..1] mỗi item

        ranked = []
        for i, (it, s) in enumerate(zip(feed, scores)):
            base = 1.0 - (i / (n - 1)) if n > 1 else 1.0          # thứ hạng Engine ② → [0..1]
            final = (1 - ENGINE3_WEIGHT) * base + ENGINE3_WEIGHT * float(s)
            ranked.append({**it, "engine3_score": round(float(s), 4), "_final": final})

        ranked.sort(key=lambda x: x["_final"], reverse=True)
        for it in ranked:
            it.pop("_final", None)
        return ranked[:limit]
    except Exception:
        # Engine ③ chưa train/xuất model, hoặc lỗi khác → bỏ qua, feed giữ nguyên thứ tự Engine ②.
        return feed


# ==========================================
# 🏠 ENDPOINT TRANG CHỦ
# ==========================================
@router.get("/homepage-feed", tags=["🏠 Trang chủ"])
def get_homepage_feed(user_id: Optional[int] = None, limit: int = 10, offset: int = 0):
    if user_id is None:
        data = _get_trending_feed(limit, offset)
        return {"status": "success", "tier": "trending",
                "message": "Khách chưa đăng nhập — gợi ý sản phẩm thịnh hành",
                "data": _rerank_with_engine3(data, limit)}

    feed, tier = _get_personalized_feed(user_id, limit, offset)
    if feed is not None:  # [] vẫn trả về (nghĩa là HẾT trang), chỉ None mới rơi xuống trending
        feed = _rerank_with_engine3(feed, limit)
        _log_impressions(user_id, feed)  # 🙈 nguồn dữ liệu cho skip ngầm
        msg = {
            "behavior_personalized": "Gợi ý theo hành vi của bạn",
            "demographic_coldstart": "Gợi ý khởi đầu theo nhóm tuổi/giới tính",
        }.get(tier, "Gợi ý cá nhân hóa")
        return {"status": "success", "tier": tier, "message": msg,
                "data": feed}

    data = _rerank_with_engine3(_get_trending_feed(limit, offset), limit)
    _log_impressions(user_id, data)  # 🙈 nguồn dữ liệu cho skip ngầm
    return {"status": "success", "tier": "trending",
            "message": "Chưa đủ dữ liệu cá nhân hóa — gợi ý sản phẩm thịnh hành",
            "data": data}


@router.get("/recently-viewed", tags=["🏠 Trang chủ"])
def get_recently_viewed(user_id: int, limit: int = 10):
    """🕘 'Xem gần đây' — sản phẩm user đã MỞ TRANG CHI TIẾT, mới nhất trước.
    Khử trùng lặp giữ lần xem gần nhất; app hiển thị thành dải riêng trên trang chủ."""
    cur = tracking_collection.find(
        {"user_id": user_id, "action": "view", "item_id": {"$ne": None}},
        {"_id": 0, "item_id": 1, "timestamp": 1},
    ).sort("timestamp", -1).limit(60)
    limit = max(1, min(int(limit), 20))
    seen, ordered = set(), []
    for doc in cur:
        iid = doc["item_id"]
        if iid in seen:
            continue
        seen.add(iid)
        ordered.append({"item_id": iid})
        if len(ordered) >= limit * 3:  # lấy dư — phòng id mồ côi bị lọc bên dưới
            break
    enriched = [x for x in _enrich_with_product_info(ordered) if x.get("name")]
    return {"status": "success", "data": enriched[:limit]}


@router.get("/top-trending", tags=["🏠 Trang chủ"])
def get_top_trending(limit: int = 5):
    return {"status": "success", "message": f"Top {limit} thịnh hành 🔥",
            "data": _get_trending_feed(limit, 0)}