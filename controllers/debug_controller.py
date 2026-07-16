# controllers/debug_controller.py
"""
🔬 SOI ENGINE ② — endpoint CHỈ ĐỌC, phục vụ trang web/debug_engine2.html.

Trả lời câu hỏi "Engine ② có đang chấm điểm đúng ý mình không?" bằng cách phơi ra
mọi số liệu trung gian của pipeline cá nhân hóa cho 1 user:
  tín hiệu vào (hành vi, từ khóa, gu hãng, dải giá, hãng vừa mua, sp bị ngó lơ)
  → điểm TỪNG THÀNH PHẦN của từng ứng viên (brand/search/price/sentiment/popularity)
  → phạt skip ngầm → đa dạng hóa trang đầu → trộn Engine ③.

⚠️ KHÔNG sửa engine: chỉ import và GỌI LẠI đúng các hàm tracking_controller /
recommendation_logic đang chạy thật. Khác endpoint thật ở 1 điểm: KHÔNG ghi
impression (xem debug bao nhiêu lần cũng không làm bẩn dữ liệu skip ngầm).
"""
import os
import math
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException

import predict  # Engine ① — ma trận cosine đã nạp sẵn trong RAM
import recommendation_logic as rec
from config.database import tracking_collection, get_mysql
from recommendation_logic import (SCORE_WEIGHTS, MIN_POSITIVE_RATIO,
                                  SENTIMENT_PRIOR, SENTIMENT_SMOOTHING_C)
from controllers.tracking_controller import (
    _get_user_interactions, _get_recent_search_keywords, _get_products_by_ids,
    _get_recently_purchased_brands, _get_user_profile, _get_demographic_brand_interest,
    _get_all_candidate_phones, _get_sentiment_map, _get_purchased_item_ids,
    _get_ignored_item_ids, _rerank_with_engine3,
)

router = APIRouter()


def _component_scores(cand: dict, brand_interest: dict, budget_band, recent_keywords, sentiment) -> dict:
    """Bản sao CHỈ ĐỂ HIỂN THỊ của rec.score_candidate — tách riêng từng điểm thành phần.
    Nếu sau này đổi công thức trong recommendation_logic thì cập nhật bản sao này cho khớp."""
    pos_ratio = sentiment.get("positive_ratio")
    total_reviews = sentiment.get("total_reviews", 0)
    bi_loai_sentiment = bool(total_reviews > 0 and pos_ratio is not None
                             and pos_ratio < MIN_POSITIVE_RATIO)

    name_lower = (cand.get("name") or "").lower()
    s_brand = brand_interest.get(cand.get("brand_normalized", ""), 0.0)
    kw_hit = next((kw for kw in recent_keywords if kw.lower().strip() in name_lower), None)
    s_search = 1.0 if kw_hit else 0.0
    s_price = rec.price_proximity_score(cand.get("price", 0) or 0, budget_band)
    if total_reviews > 0 and pos_ratio is not None:
        s_senti = (pos_ratio * total_reviews + SENTIMENT_SMOOTHING_C * SENTIMENT_PRIOR) \
                  / (total_reviews + SENTIMENT_SMOOTHING_C)
    else:
        s_senti = 0.5
    n = cand.get("number_of_ratings", 0) or 0
    s_pop = min(1.0, math.log(n + 1) / math.log(10001))

    return {
        "brand": round(s_brand, 3), "search": s_search, "price": round(s_price, 3),
        "sentiment": round(s_senti, 3), "popularity": round(s_pop, 3),
        "keyword_hit": kw_hit, "bi_loai_sentiment": bi_loai_sentiment,
        "reviews": total_reviews,
        "positive_ratio": round(pos_ratio, 2) if pos_ratio is not None else None,
    }


@router.get("/debug/engine2/{user_id}", tags=["🔬 Debug Engine ②"])
def debug_engine2(user_id: int, top: int = 20):
    # ========= 1. TÍN HIỆU ĐẦU VÀO (y hệt _get_personalized_feed) =========
    interactions = _get_user_interactions(user_id)
    recent_keywords = _get_recent_search_keywords(user_id, limit=3)

    interacted_ids = list({it["item_id"] for it in interactions})
    interacted_products = _get_products_by_ids(interacted_ids)
    item_brand_map = {pid: p.get("brand_normalized", "") for pid, p in interacted_products.items()}
    item_price_map = {pid: (p.get("price") or 0) for pid, p in interacted_products.items()}

    brand_interest_raw = rec.compute_brand_interest(interactions, item_brand_map,
                                                    now=datetime.now())
    budget_band = rec.compute_budget_band(interactions, item_price_map)
    purchased_brands = _get_recently_purchased_brands(user_id)
    brand_interest = rec.apply_post_purchase_suppression(dict(brand_interest_raw),
                                                         purchased_brands)

    # ========= 2. QUYẾT ĐỊNH TIER =========
    has_behavior = bool(brand_interest) or bool(recent_keywords)
    tier, tier_reason = "behavior_personalized", "Có hành vi (brand interest / từ khóa search)"
    profile = None
    if not has_behavior:
        profile = _get_user_profile(user_id)
        if profile and profile.get("age") and profile.get("gender"):
            brand_interest = _get_demographic_brand_interest(profile["age"], profile["gender"])
            tier, tier_reason = "demographic_coldstart", \
                f"Không có hành vi → dùng gu nhóm tuổi {profile['age']}/{profile['gender']}"
        if not brand_interest:
            tier, tier_reason = "trending", "Không hành vi + không suy ra gu từ hồ sơ → thịnh hành"

    # 15 hành vi gần nhất (kèm tên/hãng cho dễ đọc)
    hanh_vi = []
    for it in interactions[:15]:
        p = interacted_products.get(str(it.get("item_id")), {})
        hanh_vi.append({"action": it.get("action"), "item_id": it.get("item_id"),
                        "name": (p.get("name") or "?")[:60], "brand": p.get("brand"),
                        "timestamp": it.get("timestamp")})

    signals = {
        "so_hanh_vi_100_gan_nhat": len(interactions),
        "hanh_vi_gan_nhat": hanh_vi,
        "tu_khoa_search": recent_keywords,
        "brand_interest_truoc_suppression": {k: round(v, 3) for k, v in
                                             sorted(brand_interest_raw.items(),
                                                    key=lambda x: -x[1])},
        "brand_interest_sau_suppression": {k: round(v, 3) for k, v in
                                           sorted(brand_interest.items(), key=lambda x: -x[1])},
        "hang_vua_mua_bi_giam_boost": sorted(purchased_brands),
        "dai_gia_quen_thuoc": budget_band,
        "trong_so_diem": SCORE_WEIGHTS,
    }

    if tier == "trending":
        return {"status": "success", "user_id": user_id, "tier": tier,
                "tier_reason": tier_reason, "signals": signals, "bang_diem": [],
                "trang_1_cuoi_cung": []}

    # ========= 3. CHẤM ĐIỂM ỨNG VIÊN (đúng hàm thật) =========
    candidates = _get_all_candidate_phones()
    candidate_ids = [c["item_id"] for c in candidates]
    sentiment_map = _get_sentiment_map(candidate_ids)
    exclude = _get_purchased_item_ids(user_id)

    ranked = rec.rank_candidates(candidates=candidates, brand_interest=brand_interest,
                                 budget_band=budget_band, recent_keywords=recent_keywords,
                                 sentiment_map=sentiment_map, exclude_item_ids=exclude,
                                 limit=len(candidates))
    diem_truoc_phat = {r["item_id"]: r["score"] for r in ranked}

    ignored = _get_ignored_item_ids(user_id)
    ranked = rec.apply_implicit_skip_penalty(ranked, ignored)

    # ========= 4. BẢNG ĐIỂM TOP N (kèm điểm thành phần) =========
    bang_diem = []
    for i, r in enumerate(ranked[:top], 1):
        comp = _component_scores(r, brand_interest, budget_band, recent_keywords,
                                 r.get("sentiment", {}))
        bang_diem.append({
            "hang": i, "item_id": r["item_id"], "name": (r.get("name") or "")[:65],
            "brand": r.get("brand_normalized"), "price": r.get("price"),
            "thanh_phan": comp, "diem_engine2": r["score"],
            "bi_phat_skip": round(diem_truoc_phat.get(r["item_id"], r["score"]) - r["score"], 4),
        })

    # ========= 5. TRANG 1 THỰC TẾ: đa dạng hóa + trộn Engine ③ =========
    page1 = rec.diversify_by_brand(ranked, limit=10)
    final_page1 = _rerank_with_engine3(page1, 10)
    trang_1 = [{"vi_tri": i + 1, "item_id": it["item_id"],
                "name": (it.get("name") or "")[:60], "brand": it.get("brand_normalized"),
                "diem_engine2": it.get("score"), "engine3_score": it.get("engine3_score")}
               for i, it in enumerate(final_page1)]

    return {"status": "success", "user_id": user_id, "tier": tier,
            "tier_reason": tier_reason, "so_ung_vien_duoc_cham": len(ranked),
            "so_sp_bi_ngo_lo_dang_phat": len(ignored),
            "signals": signals, "bang_diem": bang_diem, "trang_1_cuoi_cung": trang_1}


# ============================================================
# 🔬 SOI ENGINE ① — ma trận cosine item-item (trang web/debug_engine1.html)
# ============================================================
def _mysql_product_map(ids: list) -> dict[str, dict]:
    """Tra name/brand các item_id trong MySQL — id nào vắng mặt = 'mồ côi'."""
    ids = [i for i in ids if str(i).lstrip("-").isdigit()]
    if not ids:
        return {}
    conn = get_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        ph = ",".join(["%s"] * len(ids))
        cur.execute(f"SELECT item_id, name, brand FROM products WHERE item_id IN ({ph})", ids)
        return {str(r["item_id"]): r for r in cur.fetchall()}
    finally:
        conn.close()


def _users_of_item(item_id: int) -> dict[int, list[str]]:
    """user_id → các hành vi user đó từng làm trên item này (đọc Mongo, giới hạn 2000 event)."""
    out: dict[int, list[str]] = {}
    cur = tracking_collection.find({"item_id": int(item_id)},
                                   {"_id": 0, "user_id": 1, "action": 1}).limit(2000)
    for ev in cur:
        u, a = ev.get("user_id"), ev.get("action")
        if u is not None:
            out.setdefault(int(u), []).append(str(a))
    return out


@router.get("/debug/engine1", tags=["🔬 Debug Engine ①"])
def debug_engine1_overview():
    """Sức khỏe ma trận CF: bao nhiêu item, bao nhiêu còn 'sống' trong MySQL,
    train lúc nào — kèm danh sách item sống để bấm soi từng cái."""
    cols = list(predict.ai_brain.columns)
    pmap = _mysql_product_map(cols)
    live = [{"item_id": c, "name": (pmap[c]["name"] or "")[:70], "brand": pmap[c]["brand"]}
            for c in cols if c in pmap]
    try:
        train_time = datetime.fromtimestamp(os.path.getmtime(predict._MODEL_PATH)) \
                             .strftime("%d/%m/%Y %H:%M")
    except Exception:
        train_time = "?"
    return {"status": "success", "tong_item_trong_ma_tran": len(cols),
            "so_item_song": len(live), "so_item_mo_coi": len(cols) - len(live),
            "train_luc": train_time,
            "goi_y": ("Tỉ lệ mồ côi cao → chạy: python scripts/check_orphan_behavior.py --clean "
                      "rồi python scripts/retrain_model.py, sau đó restart uvicorn"
                      if len(live) < len(cols) * 0.7 else "Ma trận khỏe"),
            "item_song": live}


@router.get("/debug/engine1/{item_id}", tags=["🔬 Debug Engine ①"])
def debug_engine1_item(item_id: str, top: int = 10):
    """Soi 1 sản phẩm: top tương tự THÔ từ ma trận (kèm cờ mồ côi), kết quả SAU LỌC
    (đúng thứ app nhận), và GIẢI THÍCH vì sao tương tự: những user nào đã tương tác
    chung cả 2 sản phẩm (bản chất của collaborative filtering)."""
    key = item_id if item_id in predict.ai_brain.columns else None
    self_info = _mysql_product_map([item_id]).get(str(item_id))

    if key is None:
        return {"status": "success", "item": {"item_id": item_id, "in_matrix": False,
                "in_mysql": bool(self_info),
                "name": (self_info or {}).get("name"), "brand": (self_info or {}).get("brand")},
                "ghi_chu": "Item KHÔNG có trong ma trận CF (chưa ai tương tác lúc train) "
                           "→ trang chi tiết sẽ rơi xuống Engine ③ cold-start.",
                "top_raw": [], "top_sau_loc": [], "giai_thich": []}

    sims = predict.ai_brain[key].drop(key).sort_values(ascending=False).head(top)
    sim_ids = [str(k) for k in sims.index]
    pmap = _mysql_product_map(sim_ids)

    top_raw = [{"item_id": sid, "score": round(float(s), 4), "in_mysql": sid in pmap,
                "name": (pmap.get(sid, {}).get("name") or "")[:70] or None,
                "brand": pmap.get(sid, {}).get("brand")}
               for sid, s in zip(sim_ids, sims.values)]
    top_sau_loc = [r for r in top_raw if r["in_mysql"]][:6]

    # Giải thích CF: user chung giữa item gốc và từng item tương tự hợp lệ
    users_goc = _users_of_item(int(item_id)) if str(item_id).lstrip("-").isdigit() else {}
    giai_thich = []
    for r in top_sau_loc:
        users_sim = _users_of_item(int(r["item_id"]))
        chung = sorted(set(users_goc) & set(users_sim))
        giai_thich.append({
            "item_id": r["item_id"], "name": r["name"], "score": r["score"],
            "so_user_chung": len(chung),
            "user_chung": [{"user_id": u,
                            "tren_item_goc": sorted(set(users_goc[u])),
                            "tren_item_tuong_tu": sorted(set(users_sim[u]))}
                           for u in chung[:8]],
        })

    return {"status": "success",
            "item": {"item_id": item_id, "in_matrix": True, "in_mysql": bool(self_info),
                     "name": (self_info or {}).get("name"), "brand": (self_info or {}).get("brand")},
            "so_user_da_tuong_tac_item_nay": len(users_goc),
            "top_raw": top_raw, "top_sau_loc": top_sau_loc, "giai_thich": giai_thich}
