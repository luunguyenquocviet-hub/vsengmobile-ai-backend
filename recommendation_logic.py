# recommendation_logic.py
"""
================================================================
BỘ NÃO GỢI Ý CÁ NHÂN HÓA — phiên bản theo yêu cầu thực tế
================================================================
Shop chỉ bán ĐIỆN THOẠI. Triết lý thiết kế:

  1. Chưa đăng nhập           → Trending (cold start).
  2. Đăng nhập + có tương tác → cá nhân hóa theo HÀNH VI thật của họ.
  3. Đăng nhập + chưa tương tác → tạm dùng NHÂN KHẨU HỌC (tuổi/giới tính)
                                   nhưng học từ DỮ LIỆU, không hardcode định kiến.

NGUYÊN TẮC VÀNG: Hành vi cá nhân LUÔN thắng thống kê nhóm.
Một người nam search "iPhone" suốt → gợi iPhone, kệ "nam thường thích Samsung".

KHÔNG hardcode "nam → Samsung". Thay vào đó hỏi dữ liệu:
"nhóm user cùng tuổi + giới tính này đã mua/thích brand nào nhiều nhất?"
→ nếu dữ liệu nói nam mua Apple nhiều nhất thì ưu tiên Apple. Đúng thực tế.

NGÂN SÁCH: KHÔNG đoán từ tuổi (16 tuổi vẫn có thể được mua iPhone Pro Max,
hoặc phải mua máy 5tr). Thay vào đó SUY TỪ GIÁ những máy họ đã xem/mua.
→ tự phòng ngừa mọi hoàn cảnh gia đình mà không cần biết hoàn cảnh.
"""
import math
from typing import Optional


# ==========================================
# TRỌNG SỐ HÀNH VI (giống bảng trending, có skip âm)
# ==========================================
ACTION_WEIGHTS = {
    "buy": 5.0,
    "add_to_wishlist": 4.0,
    "wishlist": 4.0,        # phòng khi frontend dùng tên ngắn
    "add_to_cart": 3.0,
    "click": 2.0,
    "view": 1.0,
    "search": 0.0,          # search không gắn item_id, xử lý riêng qua từ khóa
    "skip": -0.5,           # ✅ skip = điểm TRỪ, chỉ áp cho đúng item bị skip
}

# Trọng số các thành phần điểm cuối (cộng lại = 1.0)
SCORE_WEIGHTS = {
    "brand": 0.30,      # khớp brand user quan tâm
    "search": 0.25,     # tên SP khớp từ khóa vừa search
    "price": 0.15,      # gần khoảng giá user hay xem
    "sentiment": 0.20,  # % đánh giá tích cực từ AI
    "popularity": 0.10, # độ phổ biến (tie-breaker)
}

SENTIMENT_POSITIVE_LABEL = "🟢 TÍCH CỰC"
MIN_POSITIVE_RATIO = 0.5  # cổng lọc: loại SP có < 50% đánh giá tích cực (khi có review)

# Bayesian smoothing cho sentiment — chống "ít review nhưng toàn 5 sao leo top".
# Kéo tỉ lệ tích cực của sản phẩm ít review về trung bình chung cho tới khi đủ bằng chứng.
#   adjusted = (positive + C × PRIOR) / (total + C)
# C = số "review ảo": càng lớn càng cần nhiều review thật mới thoát khỏi mức trung bình.
SENTIMENT_PRIOR = 0.8         # tỉ lệ tích cực trung bình kỳ vọng toàn shop
SENTIMENT_SMOOTHING_C = 10    # 1 review/100% → kéo về ~0.82; 60 review/92% gần như giữ nguyên
MIN_REVIEWS_FOR_TOP = 5       # cần tối thiểu bao nhiêu review mới được full điểm phổ biến


# ==========================================
# PHẦN 1: PHÂN TÍCH HÀNH VI USER (pure functions)
# ==========================================
# ⏳ ĐỘ TƯƠI (recency decay): hành vi càng cũ càng nhẹ ký.
# Trọng số nhân 0.5 mỗi RECENCY_HALF_LIFE_DAYS ngày tuổi → gu MỚI thắng gu CŨ
# nhanh hơn (khắc phục "thao tác 20 lần mà feed đổi quá chậm").
RECENCY_HALF_LIFE_DAYS = 3.0

def compute_brand_interest(
    interactions: list[dict],
    item_brand_map: dict[int, str],
    now=None,  # datetime hiện tại — truyền vào để áp recency decay (None = tắt decay)
) -> dict[str, float]:
    """
    Từ lịch sử tương tác → điểm quan tâm mỗi brand.

    interactions: [{"action": "click", "item_id": 123}, ...]
    item_brand_map: {123: "apple", 456: "samsung", ...}
    Trả về: {"apple": 7.0, "samsung": 2.0, ...} (đã chuẩn hóa về 0..1)

    skip bị trừ điểm cho đúng brand của item đó, nhưng điểm sàn là 0
    (skip 1 máy Samsung không biến cả Samsung thành "ghét").
    """
    raw: dict[str, float] = {}
    for it in interactions:
        item_id = it.get("item_id")
        action = it.get("action", "")
        if item_id is None:
            continue
        # item_id từ Mongo thường là SỐ, map dựng từ MySQL có thể key CHUỖI
        # → tra cả hai kiểu, nếu không mọi tương tác bị bỏ qua (gu luôn rỗng).
        brand = item_brand_map.get(item_id)
        if brand is None:
            brand = item_brand_map.get(str(item_id))
        if not brand:
            continue
        weight = ACTION_WEIGHTS.get(action, 0.0)
        ts = it.get("timestamp")
        if now is not None and ts is not None:
            try:  # hành vi 3 ngày tuổi chỉ còn 50% sức nặng, 6 ngày còn 25%...
                age_days = max(0.0, (now - ts).total_seconds() / 86400.0)
                weight = weight * (0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS))
            except TypeError:
                pass  # timestamp kiểu lạ → bỏ decay cho riêng bản ghi này
        raw[brand] = raw.get(brand, 0.0) + weight

    # Đưa điểm âm về 0 (không để 1 brand thành điểm âm kéo cả công thức)
    raw = {b: max(0.0, s) for b, s in raw.items()}

    # Chuẩn hóa về 0..1 để cộng với các thành phần khác
    max_score = max(raw.values()) if raw else 0.0
    if max_score <= 0:
        return {}
    return {b: s / max_score for b, s in raw.items()}


def compute_budget_band(
    interactions: list[dict],
    item_price_map: dict[int, float],
) -> Optional[tuple[float, float]]:
    """
    Suy khoảng giá user quan tâm TỪ GIÁ những máy họ đã tương tác.
    Ưu tiên tín hiệu mạnh (buy/cart/wishlist); nếu không có thì dùng click/view.

    Trả về (low, high) hoặc None nếu chưa đủ dữ liệu (→ không lọc giá).
    Dùng làm "điểm gần giá", không phải filter cứng → tránh kết quả rỗng.
    """
    strong_actions = {"buy", "add_to_cart", "add_to_wishlist", "wishlist"}

    strong_prices = []
    weak_prices = []
    for it in interactions:
        item_id = it.get("item_id")
        price = item_price_map.get(item_id)
        if price is None:
            price = item_price_map.get(str(item_id))
        if price is None or price <= 0:
            continue
        if it.get("action") in strong_actions:
            strong_prices.append(price)
        elif it.get("action") in ("click", "view"):
            weak_prices.append(price)

    # Ưu tiên nhóm tín hiệu mạnh, nhưng chỉ khi đủ ≥2 điểm để đáng tin;
    # không đủ thì dùng nhóm yếu; vẫn không đủ thì gộp cả hai.
    if len(strong_prices) >= 2:
        prices = strong_prices
    elif len(weak_prices) >= 2:
        prices = weak_prices
    else:
        prices = strong_prices + weak_prices
    if len(prices) < 2:
        return None

    prices.sort()
    median = prices[len(prices) // 2]
    # Dải mềm quanh giá điển hình: 0.6× đến 1.5× median
    return (median * 0.6, median * 1.5)


def price_proximity_score(price: float, band: Optional[tuple[float, float]]) -> float:
    """Điểm 0..1: =1 nếu giá nằm trong dải user hay xem, giảm dần khi ra xa."""
    if band is None or price <= 0:
        return 0.5  # chưa biết gu giá → trung tính, không thưởng không phạt
    low, high = band
    if low <= price <= high:
        return 1.0
    center = (low + high) / 2
    distance = abs(price - center) / center if center > 0 else 1.0
    return max(0.0, 1.0 - distance)  # càng xa càng về 0


# ==========================================
# PHẦN 2: TÍNH ĐIỂM 1 SẢN PHẨM (pure function)
# ==========================================
def score_candidate(
    candidate: dict,
    brand_interest: dict[str, float],
    budget_band: Optional[tuple[float, float]],
    recent_keywords: list[str],
    sentiment: dict,
) -> Optional[float]:
    """
    Tính điểm tổng 1 điện thoại ứng viên.
    Trả về None nếu bị loại bởi cổng sentiment (< 50% tích cực dù đã có review).

    candidate: {item_id, name, brand_normalized, price, number_of_ratings}
    sentiment: {"positive_ratio": 0.8, "total_reviews": 10, "avg_confidence": 0.9}
    """
    # Cổng sentiment: chỉ loại khi ĐÃ có review mà đa số không tích cực
    pos_ratio = sentiment.get("positive_ratio", None)
    total_reviews = sentiment.get("total_reviews", 0)
    if total_reviews > 0 and pos_ratio is not None and pos_ratio < MIN_POSITIVE_RATIO:
        return None

    brand = candidate.get("brand_normalized", "")
    name_lower = candidate.get("name", "").lower()
    price = candidate.get("price", 0) or 0
    num_ratings = candidate.get("number_of_ratings", 0) or 0

    # 1. Điểm brand
    s_brand = brand_interest.get(brand, 0.0)

    # 2. Điểm khớp từ khóa search (tên chứa từ khóa nào user vừa tìm)
    s_search = 0.0
    for kw in recent_keywords:
        if kw.lower().strip() in name_lower:
            s_search = 1.0
            break

    # 3. Điểm gần giá
    s_price = price_proximity_score(price, budget_band)

    # 4. Điểm sentiment — DÙNG BAYESIAN SMOOTHING thay vì tỉ lệ thô.
    #    Sản phẩm ít review bị kéo về trung bình chung cho tới khi đủ bằng chứng,
    #    nên "1 review 100% tích cực" không còn ăn điểm ngang "60 review 92%".
    if total_reviews > 0 and pos_ratio is not None:
        positive_count = pos_ratio * total_reviews
        s_senti = (positive_count + SENTIMENT_SMOOTHING_C * SENTIMENT_PRIOR) / (
            total_reviews + SENTIMENT_SMOOTHING_C
        )
    else:
        s_senti = 0.5  # chưa có review → trung tính

    # 5. Điểm phổ biến (log để 10000 đánh giá không áp đảo 100)
    s_pop = math.log(num_ratings + 1) / math.log(10001)  # chuẩn hóa ~0..1
    s_pop = min(1.0, s_pop)

    total = (
        SCORE_WEIGHTS["brand"] * s_brand
        + SCORE_WEIGHTS["search"] * s_search
        + SCORE_WEIGHTS["price"] * s_price
        + SCORE_WEIGHTS["sentiment"] * s_senti
        + SCORE_WEIGHTS["popularity"] * s_pop
    )
    return round(total, 4)


# ==========================================
# PHẦN 3: GHÉP NỐI (gọi pure functions ở trên)
# ==========================================
def rank_candidates(
    candidates: list[dict],
    brand_interest: dict[str, float],
    budget_band: Optional[tuple[float, float]],
    recent_keywords: list[str],
    sentiment_map: dict[int, dict],
    exclude_item_ids: set[int],
    limit: int = 10,
) -> list[dict]:
    """Tính điểm tất cả ứng viên, loại SP đã mua, sắp xếp, trả top N."""
    scored = []
    for cand in candidates:
        item_id = cand.get("item_id")
        if item_id in exclude_item_ids:
            continue  # đã mua rồi thì không gợi lại chính nó
        sentiment = sentiment_map.get(item_id, {})
        score = score_candidate(cand, brand_interest, budget_band, recent_keywords, sentiment)
        if score is None:
            continue  # bị cổng sentiment loại
        scored.append({**cand, "score": score, "sentiment": sentiment})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]


# ==========================================
# 🎨 ĐA DẠNG HÓA (diversity) — chống "bong bóng 1 hãng"
# Vấn đề: xem 1 hãng quá nhiều → brand_interest hãng đó ≈ 1.0 → cả feed chỉ còn 1 hãng
# (over-personalization). Giải pháp: mỗi hãng chiếm tối đa MAX_BRAND_SHARE của feed;
# vị trí còn lại nhường cho ứng viên ĐIỂM CAO NHẤT của các hãng khác (khám phá).
# ==========================================
MAX_BRAND_SHARE = 0.6  # 1 hãng tối đa 60% feed (10 SP → tối đa 6)
EXPLORE_SLOTS = 2      # mỗi trang dành 2 chỗ "khám phá" cho hãng CHƯA có mặt trong trang


def diversify_by_brand(ranked: list[dict], limit: int = 10,
                       max_share: float = MAX_BRAND_SHARE,
                       explore_slots: int = EXPLORE_SLOTS) -> list[dict]:
    """Nhận danh sách ĐÃ xếp theo điểm giảm dần, trả về top `limit` vừa bám gu vừa đa dạng.

    Hai lớp bảo vệ:
    1. QUOTA HÃNG (max_share): chặn ĐỘC QUYỀN — không hãng nào quá 60% trang.
    2. KHE KHÁM PHÁ (explore_slots): chặn "LIÊN MINH" — khi 2-3 hãng người dùng
       quan tâm chiếm sạch top điểm (vd sau vài lần search), quota vẫn đúng luật
       mà feed vẫn một màu. Nên mỗi trang giữ lại vài chỗ cho sản phẩm ĐIỂM CAO
       NHẤT của những hãng chưa xuất hiện trong trang (serendipity/exploration).

    Thiếu hàng ở bước nào thì lấp dần: quota → bỏ quota (thà trùng hãng hơn thiếu).
    """
    cap = max(1, math.ceil(limit * max_share))
    slots = min(max(0, explore_slots), limit - 1)
    core_target = limit - slots

    def _brand(it: dict) -> str:
        return str(it.get("brand_normalized") or it.get("brand") or "").strip().lower()

    picked, counts, picked_ids = [], {}, set()

    def _take(it: dict):
        picked.append(it)
        picked_ids.add(it.get("item_id"))
        b = _brand(it)
        counts[b] = counts.get(b, 0) + 1

    # 1) LÕI (bám gu): theo điểm giảm dần, tôn trọng quota hãng
    for it in ranked:
        if len(picked) >= core_target:
            break
        if counts.get(_brand(it), 0) < cap:
            _take(it)

    # 2) KHÁM PHÁ: điểm cao nhất trong các hãng chưa góp mặt ở trang này
    seen_brands = set(counts)
    for it in ranked:
        if len(picked) >= limit:
            break
        if it.get("item_id") in picked_ids:
            continue
        b = _brand(it)
        if b not in seen_brands:
            _take(it)
            seen_brands.add(b)

    # 3) LẤP ĐẦY nếu vẫn thiếu: trước theo quota, cuối cùng bỏ quota
    for respect_cap in (True, False):
        for it in ranked:
            if len(picked) >= limit:
                break
            if it.get("item_id") in picked_ids:
                continue
            if respect_cap and counts.get(_brand(it), 0) >= cap:
                continue
            _take(it)

    return picked


# ==========================================
# CHẠY THỬ LOGIC (không cần Mongo) — kiểm chứng công thức
# ==========================================
if __name__ == "__main__":
    item_brand = {1: "apple", 2: "apple", 3: "samsung", 4: "xiaomi", 5: "apple"}
    item_price = {1: 30_000_000, 2: 33_000_000, 3: 22_000_000, 4: 6_000_000, 5: 28_000_000}

    # User này: click + wishlist toàn iPhone đắt tiền
    interactions = [
        {"action": "click", "item_id": 1},
        {"action": "add_to_wishlist", "item_id": 2},
        {"action": "view", "item_id": 5},
        {"action": "skip", "item_id": 4},  # skip Xiaomi giá rẻ
    ]

    brand_interest = compute_brand_interest(interactions, item_brand)
    budget = compute_budget_band(interactions, item_price)
    print("Brand interest:", brand_interest)
    print("Budget band:", tuple(round(x) for x in budget) if budget else None)

    candidates = [
        {"item_id": 10, "name": "iPhone 15 Pro Max", "brand_normalized": "apple", "price": 31_000_000, "number_of_ratings": 500},
        {"item_id": 11, "name": "Samsung Galaxy S24", "brand_normalized": "samsung", "price": 24_000_000, "number_of_ratings": 300},
        {"item_id": 12, "name": "Xiaomi Redmi Note 13", "brand_normalized": "xiaomi", "price": 5_000_000, "number_of_ratings": 1000},
        {"item_id": 13, "name": "iPhone 14", "brand_normalized": "apple", "price": 20_000_000, "number_of_ratings": 800},
    ]
    sentiment_map = {
        10: {"positive_ratio": 0.9, "total_reviews": 50, "avg_confidence": 0.95},
        11: {"positive_ratio": 0.7, "total_reviews": 30, "avg_confidence": 0.9},
        12: {"positive_ratio": 0.3, "total_reviews": 20, "avg_confidence": 0.8},  # bị loại
        13: {"positive_ratio": 0.85, "total_reviews": 40, "avg_confidence": 0.92},
    }

    ranked = rank_candidates(
        candidates, brand_interest, budget,
        recent_keywords=["iphone"], sentiment_map=sentiment_map,
        exclude_item_ids=set(), limit=10
    )
    print("\nKết quả xếp hạng:")
    for r in ranked:
        print(f"  {r['name']:28} score={r['score']:.4f}  ({r['brand_normalized']}, {r['price']:,.0f}đ)")
    print("\n→ iPhone lên đầu (brand + search + giá khớp), Xiaomi bị loại vì sentiment kém.")

# ==========================================
# 🛒 SAU KHI MUA (post-purchase suppression) — câu chuyện: mua xong 1 chiếc
# điện thoại thì nhu cầu mua thêm máy CÙNG HÃNG ngay lập tức gần như bằng 0.
# Giải pháp: trong POST_PURCHASE_DAYS ngày sau khi mua, boost của hãng vừa mua
# bị nhân POST_PURCHASE_SUPPRESSION (không xóa hẳn — gu dài hạn vẫn được nhớ).
# ==========================================
POST_PURCHASE_DAYS = 14
POST_PURCHASE_SUPPRESSION = 0.3  # giữ 30% boost cho hãng vừa mua
POST_PURCHASE_LIFT_MIN_EVENTS = 3  # sau khi mua mà vẫn tương tác hãng đó ≥N lần → GỠ hạ nhiệt



def apply_post_purchase_suppression(brand_interest: dict, purchased_brands: set,
                                    factor: float = POST_PURCHASE_SUPPRESSION) -> dict:
    if not purchased_brands or not brand_interest:
        return brand_interest
    pb = {str(b).strip().lower() for b in purchased_brands}
    return {b: (s * factor if str(b).strip().lower() in pb else s)
            for b, s in brand_interest.items()}


# ==========================================
# 🙈 SKIP NGẦM (implicit skip, phía server) — sản phẩm đã HIỂN THỊ cho user
# nhiều lần (impression) mà user không hề view/click/giỏ/mua → coi như
# "đã lướt qua", hạ điểm để nhường chỗ. Không cần app gửi sự kiện skip.
# ==========================================
IMPLICIT_SKIP_MIN_IMPRESSIONS = 3   # hiển thị ≥3 lần không đoái hoài → tính là skip
IMPLICIT_SKIP_PENALTY = 0.5         # điểm bị nhân 0.5 (giảm một nửa)


def apply_implicit_skip_penalty(ranked: list[dict], ignored_item_ids: set,
                                factor: float = IMPLICIT_SKIP_PENALTY) -> list[dict]:
    if not ignored_item_ids or not ranked:
        return ranked
    out = []
    for it in ranked:
        if it.get("item_id") in ignored_item_ids:
            it = {**it, "score": it.get("score", 0) * factor}
        out.append(it)
    out.sort(key=lambda x: x.get("score", 0), reverse=True)
    return out