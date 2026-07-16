"""
CHẨN ĐOÁN "HÀNH VI MỒ CÔI" — các bản ghi trong Mongo trỏ vào item_id
KHÔNG tồn tại trong MySQL products (rác từ test/mock cũ, hoặc sản phẩm
bị lọc khi import). Chúng chiếm chỗ trong trending và làm bẩn dữ liệu học.

Chạy từ THƯ MỤC GỐC dự án:
    python check_orphan_behavior.py           # chỉ xem báo cáo
    python check_orphan_behavior.py --clean   # xem báo cáo + XÓA rác (hỏi xác nhận)
"""
import sys
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # chạy được từ mọi nơi
from config.database import tracking_collection, get_mysql

try:
    from controllers.tracking_controller import impressions_collection
except Exception:
    impressions_collection = None


def main():
    # 1. Gom mọi item_id từng xuất hiện trong hành vi + số bản ghi của nó
    pipeline = [
        {"$match": {"item_id": {"$ne": None}}},
        {"$group": {"_id": "$item_id", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]
    behav = {str(d["_id"]): (d["_id"], d["n"])
             for d in tracking_collection.aggregate(pipeline)}
    print(f"Tổng item_id khác nhau trong hành vi Mongo : {len(behav)}")

    if not behav:
        print("Mongo chưa có hành vi nào — không có gì để kiểm.")
        return

    # 2. Đối chiếu với MySQL products (so theo CHUỖI để né bẫy str/int)
    conn = get_mysql()
    try:
        cur = conn.cursor()
        keys = list(behav.keys())
        exist = set()
        for i in range(0, len(keys), 500):  # chia lô cho câu IN gọn
            chunk = keys[i:i + 500]
            ph = ",".join(["%s"] * len(chunk))
            cur.execute(f"SELECT item_id FROM products WHERE item_id IN ({ph})", chunk)
            exist |= {str(r[0]) for r in cur.fetchall()}
    finally:
        conn.close()

    orphans = {k: v for k, v in behav.items() if k not in exist}
    n_docs = sum(n for _, n in orphans.values())
    print(f"Item CÓ trong MySQL                        : {len(behav) - len(orphans)}")
    print(f"Item MỒ CÔI (không có trong products)      : {len(orphans)}  "
          f"({n_docs} bản ghi hành vi)")

    if orphans:
        print("\nTop 10 id mồ côi nhiều hành vi nhất (đây thường là rác test):")
        for k, (_, n) in sorted(orphans.items(), key=lambda x: -x[1][1])[:10]:
            print(f"   item_id {k:<15} — {n} bản ghi")

    # 3. Dọn nếu được yêu cầu
    if "--clean" in sys.argv and orphans:
        raw_ids = [v[0] for v in orphans.values()]  # giữ NGUYÊN kiểu gốc trong Mongo
        ok = input(f"\nXóa {n_docs} bản ghi hành vi của {len(orphans)} id mồ côi? (yes/no): ")
        if ok.strip().lower() == "yes":
            r1 = tracking_collection.delete_many({"item_id": {"$in": raw_ids}})
            print(f"✅ user_tracking: đã xóa {r1.deleted_count} bản ghi")
            if impressions_collection is not None:
                r2 = impressions_collection.delete_many({"item_id": {"$in": raw_ids}})
                print(f"✅ feed_impressions: đã xóa {r2.deleted_count} bản ghi")
            print("→ Trending sẽ sạch ngay ở lần gọi kế (không cần restart server).")
        else:
            print("Bỏ qua, không xóa gì.")
    elif orphans:
        print("\nMuốn dọn sạch: chạy lại với   python check_orphan_behavior.py --clean")


if __name__ == "__main__":
    main()