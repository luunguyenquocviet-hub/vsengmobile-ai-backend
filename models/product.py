# models/product.py
from pydantic import BaseModel
from typing import List, Optional
import os
import pandas as pd
import xgboost as xgb

# ✅ Đường dẫn model neo theo vị trí file (tuyệt đối) — chạy uvicorn từ thư mục nào cũng tìm thấy.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.normpath(os.path.join(_THIS_DIR, "..", "ml_models", "xgb_product_classifier_model.json"))

_model: Optional[xgb.XGBClassifier] = None
_load_error: Optional[str] = None


def _get_model() -> xgb.XGBClassifier:
    """Nạp model 'lười' (lazy). File model thiếu → server vẫn chạy, chỉ phần gọi Engine ③ báo lỗi rõ."""
    global _model, _load_error
    if _model is not None:
        return _model
    if _load_error is not None:
        raise RuntimeError(_load_error)
    try:
        m = xgb.XGBClassifier()
        m.load_model(MODEL_PATH)
        _model = m
        return _model
    except Exception as e:
        _load_error = (
            f"Chưa thể nạp model '{MODEL_PATH}': {e}. "
            f"Hãy chạy `python data_prep.py` để huấn luyện và xuất model trước."
        )
        raise RuntimeError(_load_error)


def _get_feature_order(model: xgb.XGBClassifier) -> List[str]:
    """Lấy đúng thứ tự tên cột từ booster (luôn có sau load_model ở mọi phiên bản xgboost)."""
    booster = model.get_booster()
    if booster.feature_names:
        return list(booster.feature_names)
    names = getattr(model, "feature_names_in_", None)
    if names is not None:
        return list(names)
    raise RuntimeError("Model không chứa thông tin tên feature — file model có thể bị hỏng.")


def _predict_proba(feature_df: pd.DataFrame) -> List[float]:
    """
    LÕI dự đoán dùng chung. Nhận DataFrame đã ở ĐÚNG ĐƠN VỊ của model (giá = VND thật).
    Trả về XÁC SUẤT 'được thích' (0..1) cho từng hàng — đây là TÍN HIỆU CHẤT LƯỢNG liên tục.
    Dự đoán qua booster gốc (không phụ thuộc thuộc tính sklearn hay bị thiếu sau load_model).
    """
    model = _get_model()
    feature_order = _get_feature_order(model)

    df = feature_df.copy()
    for col in feature_order:
        if col not in df.columns:
            df[col] = 0
    df = df[feature_order]
    for col in feature_order:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    dmatrix = xgb.DMatrix(df, feature_names=feature_order)
    proba = model.get_booster().predict(dmatrix)
    return [float(p) for p in proba]


def _predict_liked(feature_df: pd.DataFrame) -> List[bool]:
    """Phiên bản nhãn 0/1: lấy ngưỡng 0.5 trên xác suất."""
    return [p >= 0.5 for p in _predict_proba(feature_df)]


# ============================================================
# ĐƯỜNG 1 — Endpoint /predict-home (bên ngoài gọi). Giá THÔ (×100000) → chia 100000.
# ============================================================
class ProductInfo(BaseModel):
    discount_price: float
    original_price: float
    liked_count: int
    number_of_ratings: int
    sold_quantity: int
    stock: int
    discount: str
    brand: str
    shop_location: str


def _df_from_products(products: List[ProductInfo]) -> pd.DataFrame:
    df = pd.DataFrame([p.model_dump() for p in products])
    df["discount_price"] = pd.to_numeric(df["discount_price"], errors="coerce").fillna(0) / 100000
    df["original_price"] = pd.to_numeric(df["original_price"], errors="coerce").fillna(0) / 100000
    df["brand_code"] = 0
    df["shop_location_code"] = 0
    return df


def predict_batch_products(products: List[ProductInfo]) -> List[bool]:
    return _predict_liked(_df_from_products(products))


# ============================================================
# ĐƯỜNG 2 — Luồng NỘI BỘ (trang chủ). Dữ liệu từ MySQL, giá ĐÃ là VND thật → KHÔNG chia thêm.
# Mỗi dict cần: discount_price, original_price, liked_count, number_of_ratings, sold_quantity, stock.
# ============================================================
def _df_from_real_vnd(rows: List[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows) if rows else pd.DataFrame()
    df["brand_code"] = 0
    df["shop_location_code"] = 0
    return df


def predict_liked_from_real_vnd(rows: List[dict]) -> List[bool]:
    """Nhãn 0/1 (giữ cho test/tiện dụng)."""
    return _predict_liked(_df_from_real_vnd(rows))


def predict_proba_from_real_vnd(rows: List[dict]) -> List[float]:
    """ĐIỂM CHẤT LƯỢNG liên tục (0..1) cho mỗi sản phẩm — dùng để SẮP THỨ TỰ, không loại bỏ."""
    return _predict_proba(_df_from_real_vnd(rows))