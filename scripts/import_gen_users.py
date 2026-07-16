import csv
import datetime
import random

# Các "nguyên liệu" để tự mix ra 1000 cái tên tiếng Việt ngẫu nhiên
ho = ["Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Huỳnh", "Phan", "Vũ", "Võ", "Đặng", "Bùi", "Đỗ", "Hồ", "Ngô", "Dương", "Lý"]
lot_nam = ["Văn", "Hữu", "Đức", "Công", "Quang", "Minh", "Xuân", "Thế", "Khắc", "Gia", "Tuấn", "Hải"]
ten_nam = ["An", "Bình", "Cường", "Dũng", "Huy", "Khang", "Khoa", "Long", "Nam", "Phúc", "Quân", "Sơn", "Thắng", "Tuấn", "Việt", "Phong"]

lot_nu = ["Thị", "Ngọc", "Phương", "Thanh", "Thu", "Hồng", "Kim", "Mai", "Bích", "Diệu", "Mỹ", "Quỳnh"]
ten_nu = ["Anh", "Chi", "Dung", "Hà", "Hoa", "Linh", "Lan", "My", "Nhung", "Oanh", "Trang", "Thảo", "Tiên", "Vy", "Yến", "Ly"]

domains = ["gmail.com", "yahoo.com", "outlook.com", "fpt.edu.vn", "icloud.com"]

# encoding='utf-8-sig' giúp Excel và MySQL đọc tiếng Việt có dấu không bị lỗi font
with open("users_1000.csv", "w", newline="", encoding="utf-8-sig") as file:
    writer = csv.writer(file)
    
    # Ghi Header khớp khít 100% với 6 cột trong Navicat của bạn
    writer.writerow(["user_id", "user_name", "age", "gender", "email", "created_at"])

    for i in range(1, 1001):
        gender = random.choice(["nam", "nữ"])
        
        if gender == "nam":
            name = f"{random.choice(ho)} {random.choice(lot_nam)} {random.choice(ten_nam)}"
        else:
            name = f"{random.choice(ho)} {random.choice(lot_nu)} {random.choice(ten_nu)}"
            
        age = random.randint(18, 55)
        email = f"user_{i:04d}@{random.choice(domains)}"
        
        # Lùi thời gian tạo tài khoản ngẫu nhiên trong vòng 2 năm đổ lại
        days_ago = random.randint(0, 730)
        random_dt = datetime.datetime.now() - datetime.timedelta(days=days_ago, hours=random.randint(0,23), minutes=random.randint(0,59))
        created_at = random_dt.strftime("%Y-%m-%d %H:%M:%S")

        writer.writerow([i, name, age, gender, email, created_at])

print("Đã kết xuất thành công 1000 user ra file users_1000.csv!")