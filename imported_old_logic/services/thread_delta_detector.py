def detect_thread_delta(thread_state, stored_memory):
    """
    Decide whether a thread is new, changed, or already processed.
    """

    latest_reply = thread_state.get("latest_reply", {})
    latest_message_id = latest_reply.get("message_id")
    reply_count = thread_state.get("reply_count", 0)

    if stored_memory is None:
        return {
            "should_process": True,
            "delta_type": "new_thread",
            "reason": "No stored thread memory found"
        }

    if stored_memory.get("last_message_id") != latest_message_id:
        return {
            "should_process": True,
            "delta_type": "new_reply",
            "reason": "Latest message id changed"
        }

    if stored_memory.get("last_reply_count") != reply_count:
        return {
            "should_process": True,
            "delta_type": "reply_count_changed",
            "reason": "Reply count changed"
        }

    if stored_memory.get("last_thread_status") != thread_state.get("thread_status"):
        return {
            "should_process": True,
            "delta_type": "status_changed",
            "reason": "Thread status changed"
        }

    return {
        "should_process": False,
        "delta_type": "no_change",
        "reason": "Thread already processed"
    }
