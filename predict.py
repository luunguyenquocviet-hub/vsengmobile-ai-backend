import os
import pandas as pd

print("🧠 Đang khởi động AI và tải ma trận...")
# Tự tìm file model: ưu tiên ml_models/ (cấu trúc mới), fallback nằm cạnh file này
# (cấu trúc cũ) → copy file này sang máy nào cũng chạy.
_BASE = os.path.dirname(os.path.abspath(__file__))
_MODEL_CANDIDATES = [
    os.path.join(_BASE, "ml_models", "shop_recommendation_model.json"),
    os.path.join(_BASE, "shop_recommendation_model.json"),
]
_MODEL_PATH = next((p for p in _MODEL_CANDIDATES if os.path.exists(p)), _MODEL_CANDIDATES[0])
# ⚠️ convert_axes=False BẮT BUỘC: item_id là số lớn (vd 155310209) — pandas mặc định
# đoán nhầm là mili-giây epoch và biến TÊN CỘT thành datetime 1970-01-02...
# → mọi lần dò id đều trượt → Engine① "mù" vĩnh viễn, tất cả rơi xuống cold-start.
ai_brain = pd.read_json(_MODEL_PATH, convert_axes=False)

# Chuẩn hóa nhãn: model cũ lưu item_id dạng float ("155310209.0") do Mongo trả số thực.
# Cắt đuôi ".0" để khớp với item_id API truyền vào ("155310209").
def _clean_label(x) -> str:
    s = str(x)
    return s[:-2] if s.endswith(".0") else s

ai_brain.columns = [_clean_label(c) for c in ai_brain.columns]
ai_brain.index = [_clean_label(i) for i in ai_brain.index]


def recommend_similar_items(target_item_id, top_n=3):
    """Engine ① — trả top N sản phẩm tương tự theo ma trận cosine.

    LƯU Ý KIỂU DỮ LIỆU: pd.read_json tự ép tên cột thành SỐ khi có thể,
    còn API truyền item_id dạng CHUỖI → phải dò cả hai kiểu, nếu không
    mọi sản phẩm đều bị coi là 'mới' và rơi nhầm xuống cold-start.
    """
    key = None
    if target_item_id in ai_brain.columns:
        key = target_item_id
    elif str(target_item_id) in ai_brain.columns:
        key = str(target_item_id)
    else:
        try:
            as_int = int(str(target_item_id).strip())
            if as_int in ai_brain.columns:
                key = as_int
        except (ValueError, TypeError):
            pass

    if key is None:
        return "Sản phẩm mới, chưa có dữ liệu để gợi ý."

    similarity_scores = ai_brain[key].drop(key)          # bỏ chính nó
    top_items = similarity_scores.sort_values(ascending=False).head(top_n)
    # Ép key về str + điểm về float để JSON trả ra sạch, không dính kiểu numpy
    return {str(k): float(v) for k, v in top_items.items()}


if __name__ == "__main__":
    print(recommend_similar_items(1))
    print(recommend_similar_items("1"))  # cả hai kiểu đều phải ra kết quả