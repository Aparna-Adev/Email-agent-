from dataclasses import dataclass
from typing import Any

from shared.email_cleaner import extract_latest_reply, get_graph_message_body


@dataclass(frozen=True)
class ThreadState:
    conversation_id: str
    latest_message_id: str | None
    latest_conversation_index: str | None
    latest_reply_count: int
    latest_reply_body: str
    latest_subject: str | None
    latest_received_at: str | None
    messages: list[dict[str, Any]]
    thread_status: str


@dataclass(frozen=True)
class ThreadDelta:
    should_process: bool
    delta_type: str
    reason: str


RESOLVED_KEYWORDS = (
    "resolved",
    "fixed",
    "completed",
    "working now",
    "issue closed",
    "done",
    "successfully",
)

PENDING_KEYWORDS = (
    "please check",
    "any update",
    "still not working",
    "pending",
    "follow up",
    "waiting",
    "not resolved",
    "need update",
)


def _conversation_id_for_message(message: dict[str, Any]) -> str:
    return (
        message.get("conversationId")
        or message.get("internetMessageId")
        or message.get("id")
        or ""
    )


def _message_sort_key(message: dict[str, Any]) -> tuple[str, str]:
    return (
        message.get("receivedDateTime") or "",
        message.get("id") or "",
    )


def detect_thread_status(latest_reply_body: str) -> str:
    text = (latest_reply_body or "").lower()

    if any(keyword in text for keyword in RESOLVED_KEYWORDS):
        return "resolved"

    if any(keyword in text for keyword in PENDING_KEYWORDS):
        return "pending"

    return "active"


def build_thread_states(messages: list[dict[str, Any]]) -> list[ThreadState]:
    grouped: dict[str, list[dict[str, Any]]] = {}

    for message in messages:
        conversation_id = _conversation_id_for_message(message)
        if not conversation_id:
            continue

        grouped.setdefault(conversation_id, []).append(message)

    thread_states: list[ThreadState] = []
    for conversation_id, thread_messages in grouped.items():
        sorted_messages = sorted(thread_messages, key=_message_sort_key)
        latest_message = sorted_messages[-1]
        latest_reply_body = extract_latest_reply(
            get_graph_message_body(latest_message)
        )

        thread_states.append(
            ThreadState(
                conversation_id=conversation_id,
                latest_message_id=latest_message.get("id"),
                latest_conversation_index=latest_message.get("conversationIndex"),
                latest_reply_count=len(sorted_messages),
                latest_reply_body=latest_reply_body,
                latest_subject=latest_message.get("subject"),
                latest_received_at=latest_message.get("receivedDateTime"),
                messages=sorted_messages,
                thread_status=detect_thread_status(latest_reply_body),
            )
        )

    return thread_states


def detect_thread_delta(
    thread_state: ThreadState,
    stored_memory: dict[str, Any] | None,
) -> ThreadDelta:
    if stored_memory is None:
        return ThreadDelta(
            should_process=True,
            delta_type="new_thread",
            reason="No stored thread memory found",
        )

    if stored_memory.get("last_message_id") != thread_state.latest_message_id:
        return ThreadDelta(
            should_process=True,
            delta_type="new_reply",
            reason="Latest message id changed",
        )

    if stored_memory.get("last_reply_count") != thread_state.latest_reply_count:
        return ThreadDelta(
            should_process=True,
            delta_type="reply_count_changed",
            reason="Reply count changed",
        )

    return ThreadDelta(
        should_process=False,
        delta_type="no_change",
        reason="Thread already processed",
    )
