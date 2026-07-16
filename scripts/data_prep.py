import os
import pandas as pd
import unicodedata
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report
import xgboost as xgb

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # thư mục gốc dự án

# ============================================================
# BỘ LỌC PHỤ KIỆN — ĐỒNG BỘ với import_to_mysql.py
# (để Engine ③ được train trên ĐÚNG tập điện thoại như catalog MySQL,
#  không lẫn ốp/cường lực/linh kiện/rác)
# ============================================================
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
MIN_PHONE_PRICE = 150000  # VND; phụ kiện đa số < 150K


def is_real_phone(name, num_ratings, price_vnd) -> bool:
    nl = unicodedata.normalize("NFC", str(name)).lower()
    if any(kw in nl for kw in JUNK_KEYWORDS):
        return False
    if (num_ratings or 0) <= 0:
        return False
    if (price_vnd or 0) < MIN_PHONE_PRICE:
        return False
    return True


print("1. Đọc dữ liệu từ file Excel... 📊")
df_id = pd.read_excel(os.path.join(_ROOT, 'data', 'data.xlsx'), sheet_name=0)
df_data = pd.read_excel(os.path.join(_ROOT, 'data', 'data.xlsx'), sheet_name=1)
df_rating = pd.read_excel(os.path.join(_ROOT, 'data', 'data.xlsx'), sheet_name=2)

for df in [df_id, df_data, df_rating]:
    for col in ['item_id', 'shop_id']:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()

# ✅ (2a) KHỬ TRÙNG SẢN PHẨM — giữ dòng đầu (giống import_to_mysql.py)
df_id = df_id.drop_duplicates(subset='item_id', keep='first')
df_data = df_data.drop_duplicates(subset='item_id', keep='first')

print("2. Gộp các bảng dữ liệu... 🔗")
df_item = pd.merge(df_id, df_data, on=['item_id', 'shop_id'], how='inner')

# ✅ (1) LỌC PHỤ KIỆN — chỉ giữ điện thoại nguyên chiếc
_namecol = 'name' if 'name' in df_item.columns else next(
    (c for c in df_item.columns if 'name' in c.lower()), None)
_pvnd = (pd.to_numeric(df_item.get('discount_price'), errors='coerce').fillna(0) // 100000)
_nr = pd.to_numeric(df_item.get('number_of_ratings'), errors='coerce').fillna(0)
_mask = [is_real_phone(n, nr, pv) for n, nr, pv in zip(df_item[_namecol], _nr, _pvnd)]
_before = len(df_item)
df_item = df_item[_mask].copy()
print(f"   🧹 Lọc phụ kiện: giữ {len(df_item)}/{_before} sản phẩm "
      f"(loại {_before - len(df_item)} phụ kiện/rác)")

df_full = pd.merge(df_rating, df_item, on='item_id', how='inner')

# ✅ (2b) KHỬ TRÙNG BÌNH LUẬN — bỏ review trùng cmt_id
if 'cmt_id' in df_full.columns:
    _b = len(df_full)
    df_full = df_full.drop_duplicates(subset='cmt_id', keep='first')
    print(f"   🧹 Khử review trùng cmt_id: còn {len(df_full)}/{_b} dòng")

print("3. Làm sạch và chuẩn hóa các cột số... 🛠️")
numeric_cols = ['discount_price', 'original_price', 'liked_count', 'number_of_ratings', 'sold_quantity', 'stock']
for col in numeric_cols:
    if col in df_full.columns:
        df_full[col] = pd.to_numeric(df_full[col], errors='coerce').fillna(0)

df_full['discount_price'] = df_full['discount_price'] / 100000
df_full['original_price'] = df_full['original_price'] / 100000

print("4. Xử lý cột giảm giá và số sao... ✂️")
if 'discount' in df_full.columns:
    df_full['discount'] = df_full['discount'].astype(str).str.replace('%', '')
    df_full['discount'] = pd.to_numeric(df_full['discount'], errors='coerce').fillna(0)

if 'rating_star_x' in df_full.columns:
    df_full['rating_star_x'] = pd.to_numeric(df_full['rating_star_x'], errors='coerce').fillna(0)
    df_full['is_liked'] = (df_full['rating_star_x'] >= 4).astype(int)

print("5. Mã hóa các cột dạng chữ danh mục... 🏷️")
if 'brand' in df_full.columns:
    df_full['brand'] = df_full['brand'].fillna('No Brand')
    df_full['brand_code'] = df_full['brand'].astype('category').cat.codes
if 'shop_location' in df_full.columns:
    df_full['shop_location_code'] = df_full['shop_location'].astype('category').cat.codes

print("6. Tách đặc trưng X và nhãn mục tiêu y... 📐")
columns_to_drop = [
    'user_name', 'item_id', 'shop_id', 'name', 'shop_location', 'brand',
    'product_image_link', 'discount', 'rating_star_x', 'rating_star_y',
    'is_liked', 'comment', 'order_id', 'cmt_id'
]
X = df_full.drop(columns=columns_to_drop, errors='ignore')
y = df_full['is_liked'] if 'is_liked' in df_full.columns else pd.Series([0] * len(X))

for col in X.columns:
    X[col] = pd.to_numeric(X[col], errors='coerce').fillna(0)

print("7. Chia dữ liệu 80/20 và huấn luyện mô hình... 🧠")
if len(X) == 0:
    print("⚠️ Dữ liệu sau khi gộp đang trống. Kiểm tra lại data.xlsx!")
else:
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y)

    # ✅ (3) CÂN BẰNG LỚP — dữ liệu lệch ~92/8 nên model mặc định 'gật' gần hết.
    # scale_pos_weight = (số nhãn 0)/(số nhãn 1): hạ trọng số lớp đa số xuống,
    # ép model dám phán 'không thích' → có khả năng LỌC thật, thay vì toàn true.
    neg = int((y_train == 0).sum())
    pos = int((y_train == 1).sum())
    spw = (neg / pos) if pos else 1.0
    print(f"   ⚖️ scale_pos_weight = {spw:.4f}  (neg={neg}, pos={pos})")

    model = xgb.XGBClassifier(
        objective='binary:logistic', random_state=42,
        scale_pos_weight=spw, eval_metric='logloss')
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    accuracy = accuracy_score(y_test, y_pred)

    print("-" * 50)
    print(f"✅ Accuracy: {accuracy * 100:.2f}%")
    print("⚠️ ĐỪNG chỉ nhìn accuracy — với dữ liệu lệch, model 'gật hết' vẫn ~92%.")
    print("   Hãy nhìn precision/recall của LỚP 0 (không thích) bên dưới:")
    print(classification_report(y_test, y_pred, digits=3,
                                target_names=['không thích (0)', 'thích (1)']))
    print("-" * 50)

    model.save_model(os.path.join(_ROOT, 'ml_models', 'xgb_product_classifier_model.json'))
    print("🚀 Đã xuất: xgb_product_classifier_model.json")