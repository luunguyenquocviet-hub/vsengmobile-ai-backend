"""
ĐO Engine ③ (XGBoost) như một bộ XẾP HẠNG chất lượng + độ phủ/cold-start của Engine ①.
Chạy ở thư mục gốc:   python evaluate_engine3.py

Cần: data.xlsx, xgb_product_classifier_model.json (chạy data_prep.py trước), xgboost.
Tập test = 20% TÁCH GIỐNG data_prep (random_state=42, stratify) → dữ liệu model CHƯA thấy.

Vì dữ liệu lệch (~92% nhãn 'thích'), hãy nhìn ROC-AUC / PR-AUC làm chính:
  • ROC-AUC = 0.5 → xếp hạng vô dụng; càng gần 1.0 càng tốt.
  • PR-AUC chỉ có nghĩa khi so với BASE RATE (tỉ lệ 'thích' nền). Cao hơn base rate mới là có ích.
  • precision@k cao phần lớn do base rate cao — so với base rate mới biết model có 'nhấc' đúng đồ tốt lên đầu không.
"""
import os, json, unicodedata
import pandas as pd
import numpy as np

_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_XLSX = os.path.join(_DIR, "..", "data", "data.xlsx")
MODEL_PATH = os.path.join(_DIR, "..", "ml_models", "xgb_product_classifier_model.json")
CF_PATH = os.path.join(_DIR, "..", "ml_models", "shop_recommendation_model.json")

JUNK_KEYWORDS = [
    'ốp', 'vỏ ', 'cường lực', 'miếng dán', 'dán ', 'tai nghe', 'cáp sạc', 'sạc dự phòng',
    'pin dự phòng', 'bao da', 'kính cường', 'kính camera', 'kính lưng', 'kính sau',
    'giá đỡ', 'bút cảm ứng', 'lá vàng', 'móc khóa', 'dây đeo', 'gậy chụp', 'thần tài',
    'bao silicon', 'case ', 'loa trong', 'loa ', 'phím nokia', 'phím ', 'nắp lưng',
    'nắp ', 'sườn', 'jack', 'cọc sạc', 'chân sạc', 'module', 'mạch', 'linh kiện',
    'camera sau', 'camera trước', 'khay sim', 'main ', 'cảm ứng', 'ron ', 'skin ',
    'que chọc', 'que chọt', 'miếng lau', 'mô hình', 'nhẫn', 'nhẩn', 'decor', 'sim ghép',
    'đầu đọc', 'màn hình tab', 'bộ màn hình', 'màn hình oppo', 'màn hình nokia',
    'màn hình samsung', 'xác ',
]
MIN_PHONE_PRICE = 150000


def is_real_phone(name, num_ratings, price_vnd):
    nl = unicodedata.normalize("NFC", str(name)).lower()
    if any(kw in nl for kw in JUNK_KEYWORDS):
        return False
    if (num_ratings or 0) <= 0:
        return False
    if (price_vnd or 0) < MIN_PHONE_PRICE:
        return False
    return True


def build_dataset():
    """Dựng lại X, y SẠCH y hệt data_prep.py (lọc phụ kiện + khử trùng)."""
    df_id = pd.read_excel(DATA_XLSX, sheet_name=0)
    df_data = pd.read_excel(DATA_XLSX, sheet_name=1)
    df_rating = pd.read_excel(DATA_XLSX, sheet_name=2)
    for df in [df_id, df_data, df_rating]:
        for c in ['item_id', 'shop_id']:
            if c in df.columns:
                df[c] = df[c].astype(str).str.strip()
    df_id = df_id.drop_duplicates(subset='item_id', keep='first')
    df_data = df_data.drop_duplicates(subset='item_id', keep='first')
    df_item = pd.merge(df_id, df_data, on=['item_id', 'shop_id'], how='inner')

    nc = 'name' if 'name' in df_item.columns else next(c for c in df_item.columns if 'name' in c.lower())
    pv = pd.to_numeric(df_item.get('discount_price'), errors='coerce').fillna(0) // 100000
    nr = pd.to_numeric(df_item.get('number_of_ratings'), errors='coerce').fillna(0)
    df_item = df_item[[is_real_phone(n, r, p) for n, r, p in zip(df_item[nc], nr, pv)]].copy()
    n_phones = df_item['item_id'].nunique()

    df_full = pd.merge(df_rating, df_item, on='item_id', how='inner')
    if 'cmt_id' in df_full.columns:
        df_full = df_full.drop_duplicates(subset='cmt_id', keep='first')

    for col in ['discount_price', 'original_price', 'liked_count', 'number_of_ratings', 'sold_quantity', 'stock']:
        if col in df_full.columns:
            df_full[col] = pd.to_numeric(df_full[col], errors='coerce').fillna(0)
    df_full['discount_price'] = df_full['discount_price'] / 100000
    df_full['original_price'] = df_full['original_price'] / 100000
    df_full['rating_star_x'] = pd.to_numeric(df_full['rating_star_x'], errors='coerce').fillna(0)
    df_full['is_liked'] = (df_full['rating_star_x'] >= 4).astype(int)
    df_full['brand'] = df_full.get('brand', pd.Series()).fillna('No Brand')
    df_full['brand_code'] = df_full['brand'].astype('category').cat.codes
    df_full['shop_location_code'] = df_full['shop_location'].astype('category').cat.codes

    drop = ['user_name', 'item_id', 'shop_id', 'name', 'shop_location', 'brand',
            'product_image_link', 'discount', 'rating_star_x', 'rating_star_y',
            'is_liked', 'comment', 'order_id', 'cmt_id']
    X = df_full.drop(columns=drop, errors='ignore')
    y = df_full['is_liked']
    for c in X.columns:
        X[c] = pd.to_numeric(X[c], errors='coerce').fillna(0)
    return X, y, n_phones


def precision_at_k(y_true, scores, k):
    y_true = np.asarray(y_true)
    k = min(k, len(scores))
    top = np.argsort(scores)[::-1][:k]
    return float(y_true[top].sum() / k)


def recall_at_k(y_true, scores, k):
    y_true = np.asarray(y_true)
    k = min(k, len(scores))
    top = np.argsort(scores)[::-1][:k]
    total_pos = y_true.sum()
    return float(y_true[top].sum() / total_pos) if total_pos else 0.0


def main():
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import roc_auc_score, average_precision_score, classification_report
    import xgboost as xgb

    print("⏳ Dựng dữ liệu sạch...")
    X, y, n_phones = build_dataset()
    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)

    if not os.path.exists(MODEL_PATH):
        raise SystemExit(f"❌ Chưa có {MODEL_PATH}. Chạy `python data_prep.py` trước.")
    model = xgb.XGBClassifier()
    model.load_model(MODEL_PATH)
    feat = list(model.get_booster().feature_names)
    dte = xgb.DMatrix(X_te[feat], feature_names=feat)
    scores = model.get_booster().predict(dte)
    y_te_arr = y_te.values

    base = float(y_te_arr.mean())
    print("\n" + "=" * 56)
    print("ENGINE ③ — XẾP HẠNG TRÊN TẬP TEST GIỮ LẠI (review-level)")
    print("=" * 56)
    print(f"Số mẫu test          : {len(y_te_arr):,}")
    print(f"Base rate ('thích')  : {base*100:.1f}%   ← mốc so sánh")
    print(f"ROC-AUC              : {roc_auc_score(y_te_arr, scores):.4f}   (0.5 = vô dụng, →1 = tốt)")
    print(f"PR-AUC (avg prec.)   : {average_precision_score(y_te_arr, scores):.4f}   (so với base {base:.3f})")
    print("\n precision@k / recall@k (xếp theo điểm Engine③):")
    for k in [5, 10, 20, 50]:
        p = precision_at_k(y_te_arr, scores, k)
        r = recall_at_k(y_te_arr, scores, k)
        print(f"   @{k:<3}  P={p:.3f}   R={r:.3f}")
    print("\n classification_report @ ngưỡng 0.5:")
    print(classification_report(y_te_arr, (scores >= 0.5).astype(int), digits=3,
                                target_names=['không thích (0)', 'thích (1)']))

    print("=" * 56)
    print("ENGINE ① — ĐỘ PHỦ & TỈ LỆ COLD-START")
    print("=" * 56)
    try:
        cf = json.load(open(CF_PATH, encoding='utf-8'))
        n_cf = len(cf)
        print(f"Số item trong ma trận CF      : {n_cf}")
        print(f"Số điện thoại thật trong data : {n_phones}")
        cov = n_cf / n_phones if n_phones else 0
        print(f"Độ phủ CF ước tính            : {cov*100:.1f}%")
        print(f"→ ~{(1-cov)*100:.1f}% sản phẩm rơi vào cold-start → Engine③ fallback.")
        print("  (Lưu ý: CF được dựng từ hành vi giả lập nên độ phủ thấp; số này minh hoạ")
        print("   vì sao cần Engine③ ở trang chi tiết, không phải con số sản xuất cuối cùng.)")
    except Exception as e:
        print("Không đọc được ma trận CF:", e)


if __name__ == "__main__":
    main()