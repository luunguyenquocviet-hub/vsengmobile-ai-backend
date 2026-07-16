import os
import pandas as pd
from tqdm import tqdm
import re
from transformers import pipeline

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # thư mục gốc dự án

# 1. Khởi tạo AI PhoBERT Sentiment (Bản ổn định nhất)
print("🧠 Đang tải AI PhoBERT Sentiment...")
model_name = "wonrax/phobert-base-vietnamese-sentiment"
analyzer = pipeline("sentiment-analysis", model=model_name)

# 2. Bộ lọc tắm rửa dữ liệu (Giữ nguyên 3 từ cơ bản)
def clean_text(text):
    if not isinstance(text, str):
        return ""
    text = text.lower()
    # Xóa các chữ kéo dài (ví dụ: hayyyyy -> hay)
    text = re.sub(r'([a-zàáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ])\1{2,}', r'\1', text)
    # Thay thế 3 từ viết tắt cốt lõi
    text = text.replace("sp", "sản phẩm").replace("đc", "được").replace("ko", "không")
    return text.strip()

# 3. Hàm xử lý cho từng dòng trong Excel
def analyze_row(text):
    cleaned_text = clean_text(text)
    
    # Nếu dòng trống thì bỏ qua
    if not cleaned_text:
        return pd.Series(["🟡 TRUNG TÍNH", 0.0, ""])
    
    # Cắt ngắn câu để tránh lỗi quá tải bộ nhớ AI
    cleaned_text = cleaned_text[:1500] 

    # Đưa cho AI phán xét
    # Bật chế độ tự động cắt từ (truncation=True) và giới hạn 256 (max_length=256)
    try:
        result = analyzer(cleaned_text, truncation=True, max_length=256)[0]
        
        label = result['label']
        if label == 'POS':
            sentiment = "🟢 TÍCH CỰC"
        elif label == 'NEG':
            sentiment = "🔴 TIÊU CỰC"
        else:
            sentiment = "🟡 TRUNG TÍNH"
            
        return pd.Series([sentiment, result['score'], cleaned_text])
        
    except Exception as e:
        # Tấm khiên bảo vệ: Nếu gặp lỗi phần cứng lạ nào đó, máy sẽ tự động 
        # gán nhãn Trung Tính thay vì văng luôn cả chương trình.
        return pd.Series(["🟡 TRUNG TÍNH", 0.0, cleaned_text])
    
    label = result['label']
    if label == 'POS':
        sentiment = "🟢 TÍCH CỰC"
    elif label == 'NEG':
        sentiment = "🔴 TIÊU CỰC"
    else:
        sentiment = "🟡 TRUNG TÍNH"
        
    return pd.Series([sentiment, result['score'], cleaned_text])

# ==========================================
# ⚙️ PHẦN CHẠY THỰC TẾ TRÊN FILE DỮ LIỆU
# ==========================================
if __name__ == "__main__":
    file_name = os.path.join(_ROOT, "data", "data.xlsx") 
    print(f"📂 Đang đọc file dữ liệu {file_name}...")
    
    try:
        df = pd.read_excel(file_name, sheet_name="RATING")
    except Exception as e:
        print(f"❌ Lỗi đọc file: {e}")
        exit()

    # Kích hoạt thanh tiến trình tqdm
    tqdm.pandas(desc="⏳ Đang phân tích")

    print(f"🚀 Bắt đầu chấm điểm {len(df)} dòng dữ liệu...")
    
    # Tạo 3 cột mới chứa kết quả của AI
    df[['AI_DanhGia', 'AI_DoTuTin', 'NoiDung_DaLoc']] = df['comment'].progress_apply(analyze_row)

    # Xuất ra file Excel mới
    output_file = os.path.join(_ROOT, "data", "data_danh_gia_AI_HoanChinh.xlsx")
    df.to_excel(output_file, index=False)
    
    print(f"\n✅ ĐÃ HOÀN THÀNH XUẤT SẮC! Mời bạn mở file '{output_file}' lên để xem kết quả.")