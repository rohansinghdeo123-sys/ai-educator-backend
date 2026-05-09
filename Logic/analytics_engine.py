# Logic/analytics_engine.py

from models import TopicPerformance, TestHistory, UserProgress
from datetime import datetime, timedelta
import math

# =====================================================
# UPDATE PERFORMANCE (ENHANCED)
# =====================================================
def update_topic_performance(db, user_id, topic, correct_answers, total_questions, time_spent=0):
    record = db.query(TopicPerformance).filter(
        TopicPerformance.user_id == user_id,
        TopicPerformance.topic == topic
    ).first()

    if not record:
        record = TopicPerformance(
            user_id=user_id,
            topic=topic,
            attempts=0,
            correct=0,
            weak=False,
            avg_time_per_question=0.0,
            trend_score=0.0
        )
        db.add(record)
        db.commit()
        db.refresh(record)

    # Calculate new accuracy
    old_accuracy = record.accuracy
    record.attempts += total_questions
    record.correct += correct_answers
    new_accuracy = record.accuracy
    
    # Update trend score (improvement vs decline)
    record.trend_score = new_accuracy - old_accuracy
    
    # Update time per question
    if total_questions > 0:
        current_time_per_q = time_spent / total_questions
        if record.avg_time_per_question == 0:
            record.avg_time_per_question = current_time_per_q
        else:
            # Moving average
            record.avg_time_per_question = (record.avg_time_per_question * 0.7) + (current_time_per_q * 0.3)
    
    record.last_practiced = datetime.utcnow()
    record.weak = new_accuracy < 60.0

    db.commit()
    return record

# =====================================================
# COGNITIVE METRICS CALCULATOR
# =====================================================
def calculate_cognitive_metrics(db, user_id):
    """
    Calculates advanced behavioral metrics:
    - Focus Score: Based on consistency of answer times
    - Consistency Index: Frequency of sessions
    - Learning Efficiency: Accuracy / Time spent
    """
    sessions = db.query(TestHistory).filter(TestHistory.user_id == user_id).order_by(TestHistory.date.desc()).limit(10).all()
    
    if not sessions:
        return {"focus_score": 0.0, "consistency_index": 0.0, "learning_efficiency": 0.0}
    
    # 1. Focus Score (Avg of session focus scores)
    avg_focus = sum(s.focus_score for s in sessions) / len(sessions)
    
    # 2. Consistency Index (Sessions per week)
    last_10_days = (datetime.now().date() - sessions[-1].date).days if sessions else 0
    consistency = min(100.0, (len(sessions) / max(1, last_10_days)) * 20)
    
    # 3. Learning Efficiency (Accuracy / Time)
    total_acc = sum(s.accuracy_rate for s in sessions) / len(sessions)
    avg_time = sum(s.time_spent_seconds for s in sessions) / len(sessions)
    efficiency = min(100.0, (total_acc / max(1, avg_time)) * 100)
    
    # Update UserProgress
    user = db.query(UserProgress).filter(UserProgress.user_id == user_id).first()
    if user:
        user.focus_score = round(avg_focus, 2)
        user.consistency_index = round(consistency, 2)
        user.learning_efficiency = round(efficiency, 2)
        db.commit()
        
    return {
        "focus_score": round(avg_focus, 2),
        "consistency_index": round(consistency, 2),
        "learning_efficiency": round(efficiency, 2)
    }

# =====================================================
# 🔥 BLOOMBERG INTELLIGENCE ENGINE
# =====================================================
def get_user_analytics(db, user_id):
    """
    Generates high-density, actionable insights.
    """
    # 1. Fetch Data
    topics = db.query(TopicPerformance).filter(TopicPerformance.user_id == user_id).all()
    sessions = db.query(TestHistory).filter(TestHistory.user_id == user_id).order_by(TestHistory.date.desc()).limit(20).all()
    
    # 2. Cognitive Metrics
    cog_metrics = calculate_cognitive_metrics(db, user_id)
    
    # 3. Topic Heatmap & Weak Areas
    heatmap = []
    weak_areas = []
    for t in topics:
        heatmap.append({
            "topic": t.topic,
            "value": round(t.accuracy, 1),
            "attempts": t.attempts,
            "trend": round(t.trend_score, 1)
        })
        if t.weak:
            weak_areas.append({
                "topic": t.topic,
                "accuracy": round(t.accuracy, 1),
                "avg_time": round(t.avg_time_per_question, 1)
            })
            
    # 4. Performance Trends (Last 10 sessions)
    trends = []
    for s in reversed(sessions[:10]):
        trends.append({
            "date": s.date.isoformat(),
            "accuracy": s.accuracy_rate,
            "xp": s.xp_earned,
            "focus": s.focus_score
        })
        
    # 5. AI Insights Generation
    insights = []
    
    # Performance Insights
    if weak_areas:
        insights.append({
            "type": "performance",
            "message": f"Critical weakness detected in {weak_areas[0]['topic']}. Accuracy is below 60%.",
            "severity": "warning",
            "action_label": "REVISE NOW",
            "action_trigger": f"revision_{weak_areas[0]['topic']}"
        })
        
    # Behavioral Insights
    if cog_metrics['focus_score'] < 70:
        insights.append({
            "type": "behavior",
            "message": "Your focus score is declining. Consider shorter, high-intensity study blocks.",
            "severity": "info"
        })
        
    # Predictive Insights
    user = db.query(UserProgress).filter(UserProgress.user_id == user_id).first()
    if user and user.xp > 0:
        days_to_level = math.ceil((100 - (user.xp % 100)) / max(1, user.xp / max(1, user.total_tests)))
        insights.append({
            "type": "predictive",
            "message": f"At your current pace, you will reach Level {user.level + 1} in approximately {days_to_level} days.",
            "severity": "success"
        })

    return {
        "summary": {
            "total_topics": len(topics),
            "avg_accuracy": round(sum(t.accuracy for t in topics) / len(topics), 1) if topics else 0.0,
            "total_xp": user.xp if user else 0,
            "streak": user.streak if user else 0
        },
        "topic_heatmap": heatmap,
        "performance_trends": trends,
        "weak_areas": weak_areas[:3],
        "insights": insights,
        "cognitive_metrics": cog_metrics,
        "predictive_stats": {
            "days_to_next_level": days_to_level if 'days_to_level' in locals() else 0,
            "next_milestone": f"Level {user.level + 1}" if user else "N/A"
        }
    }