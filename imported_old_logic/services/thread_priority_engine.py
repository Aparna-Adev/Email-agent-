def calculate_thread_priority(thread_state: dict, classification: dict) -> dict:
    score = 0
    reasons = []

    latest_body = thread_state.get("latest_reply_body", "").lower()
    reply_count = thread_state.get("reply_count", 0)

    if thread_state.get("escalated"):
        score += 5
        reasons.append("Escalation detected in thread")

    if thread_state.get("thread_status") == "pending":
        score += 3
        reasons.append("Thread is pending")

    if reply_count >= 3:
        score += 2
        reasons.append("Multiple replies in conversation")

    if any(word in latest_body for word in ["urgent", "asap", "critical", "blocked", "production"]):
        score += 4
        reasons.append("Urgent or critical keyword found in latest reply")

    if classification.get("team") == "Management / CEO Attention":
        score += 4
        reasons.append("Requires management attention")

    if classification.get("category") in ["Technical Issue", "Escalation"]:
        score += 2
        reasons.append("High-impact category")

    if score >= 8:
        priority = "Critical"
    elif score >= 5:
        priority = "High"
    elif score >= 3:
        priority = "Medium"
    else:
        priority = "Low"

    return {
        "priority": priority,
        "score": score,
        "reasons": reasons
    }