def classify_thread_state(thread_state: dict) -> dict:
    latest_body = thread_state.get("latest_reply_body", "").lower()
    latest_subject = thread_state.get("latest_subject", "").lower()
    all_text = latest_subject + " " + latest_body

    if thread_state.get("thread_status") == "resolved":
        return {
            "is_relevant": True,
            "team": "General / Unclear",
            "category": "Resolved / Informational",
            "reason": "Latest thread state indicates the issue is resolved",
            "confidence": 0.75
        }

    if any(word in all_text for word in ["login", "backend", "server", "api", "database", "portal", "system"]):
        return {
            "is_relevant": True,
            "team": "Development",
            "category": "Technical Issue",
            "reason": "Thread contains technical/system issue keywords",
            "confidence": 0.85
        }

    if any(word in all_text for word in ["uat", "test", "testing", "qa", "bug"]):
        return {
            "is_relevant": True,
            "team": "QA/Testing",
            "category": "Testing / UAT",
            "reason": "Thread contains testing or UAT context",
            "confidence": 0.85
        }

    if any(word in all_text for word in ["approval", "requirement", "clarification", "client request", "report"]):
        return {
            "is_relevant": True,
            "team": "Business Analyst",
            "category": "Business Request",
            "reason": "Thread contains business requirement or approval context",
            "confidence": 0.8
        }

    if thread_state.get("escalated"):
        return {
            "is_relevant": True,
            "team": "Management / CEO Attention",
            "category": "Escalation",
            "reason": "Thread contains escalation indicators",
            "confidence": 0.9
        }

    return {
        "is_relevant": True,
        "team": "General / Unclear",
        "category": "General Business Email",
        "reason": "No strong team-specific thread pattern detected",
        "confidence": 0.45
    }