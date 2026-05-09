# Logic/memory_store.py

import json
from sqlalchemy.orm import Session
from models import UserProgress

# =====================================================
# 🔥 PERSISTENT STORE (PHASE 2)
# =====================================================
# We store the plan in the UserProgress table as a JSON string
# You will need to add `current_plan = Column(Text, nullable=True)` to UserProgress in models.py

def save_plan(db: Session, user_id: str, plan: dict):
    user = db.query(UserProgress).filter(UserProgress.user_id == user_id).first()
    if user:
        user.current_plan = json.dumps({
            "plan": plan,
            "current_step": 0
        })
        db.commit()

def get_plan(db: Session, user_id: str) -> dict:
    user = db.query(UserProgress).filter(UserProgress.user_id == user_id).first()
    if user and user.current_plan:
        try:
            return json.loads(user.current_plan)
        except json.JSONDecodeError:
            return None
    return None

def increment_step(db: Session, user_id: str):
    user = db.query(UserProgress).filter(UserProgress.user_id == user_id).first()
    if user and user.current_plan:
        try:
            data = json.loads(user.current_plan)
            data["current_step"] += 1
            user.current_plan = json.dumps(data)
            db.commit()
        except json.JSONDecodeError:
            pass

def reset_plan(db: Session, user_id: str):
    user = db.query(UserProgress).filter(UserProgress.user_id == user_id).first()
    if user:
        user.current_plan = None
        db.commit()