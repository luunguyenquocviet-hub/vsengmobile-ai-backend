# nightly_bert_update.py
"""
Job chạy ĐỊNH KỲ (mỗi đêm, qua cron/Task Scheduler) — KHÔNG chạy cùng server.
Đọc các bình luận CHƯA chấm trong MySQL reviews, phân tích cảm xúc bằng PhoBERT,
rồi GHI kết quả (ai_sentiment, ai_confidence, is_analyzed) ngược lại MySQL.

✅ Trước đây job này đọc/ghi collection Mongo. Giờ reviews đã ở MySQL nên job
cũng làm việc trên MySQL — một nguồn sự thật duy nhất cho bình luận.

Chạy: python nightly_bert_update.py
Yêu cầu: pip install transformers torch
"""
from transformers import pipeline
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # chạy được từ mọi nơi
from config.database import get_mysql

MODEL_NAME = "wonrax/phobert-base-vietnamese-sentiment"  # model đã fine-tune cho tiếng Việt
BATCH = 500

# Ánh xạ nhãn model → nhãn lưu trong DB (không emoji, khớp POSITIVE_SENTIMENT ở controller)
LABEL_MAP = {"POS": "TÍCH CỰC", "NEG": "TIÊU CỰC", "NEU": "TRUNG TÍNH"}


def run():
    print("🤖 Nạp model PhoBERT...")
    clf = pipeline("sentiment-analysis", model=MODEL_NAME, truncation=True, max_length=256)

    conn = get_mysql()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT id, comment FROM reviews
            WHERE is_analyzed = FALSE AND comment IS NOT NULL AND comment <> ''
            LIMIT %s
        """, (BATCH,))
        rows = cur.fetchall()

        if not rows:
            print("✅ Không có bình luận mới cần chấm.")
            return

        print(f"⏳ Đang chấm {len(rows)} bình luận...")
        update_cur = conn.cursor()
        for r in rows:
            result = clf(str(r["comment"]))[0]
            sentiment = LABEL_MAP.get(result["label"], "TRUNG TÍNH")
            confidence = round(float(result["score"]), 4)
            update_cur.execute("""
                UPDATE reviews
                SET ai_sentiment = %s, ai_confidence = %s, is_analyzed = TRUE
                WHERE id = %s
            """, (sentiment, confidence, r["id"]))
        conn.commit()
        print(f"✅ Đã chấm xong {len(rows)} bình luận, ghi vào MySQL.")
    finally:
        conn.close()


if __name__ == "__main__":
    run()