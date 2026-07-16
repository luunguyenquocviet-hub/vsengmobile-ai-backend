# models/tracking.py
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class UserBehavior(BaseModel):
    user_id: int
    action: str # Hỗ trợ: "view", "click", "search", "add_to_cart", "skip" 👈 Thêm skip vào đây
    item_id: Optional[int] = None
    search_query: Optional[str] = None
    timestamp: str = None

    def __init__(self, **data):
        super().__init__(**data)
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()