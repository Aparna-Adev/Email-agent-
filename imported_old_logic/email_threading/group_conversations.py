from preprocessing.clean_body import clean_business_body
from fetch.fetch_attachments import fetch_message_attachments, analyze_attachments
from preprocessing.latest_reply_extractor import extract_latest_reply


def group_conversations(messages, access_token=None, user_id=None):
    """
    Group Outlook emails using conversationId.
    Preserve each reply separately.
    Clean each reply body safely.
    Detect image and attachment details when access_token and user_id are provided.
    """

    grouped = {}

    for message in messages:
        conversation_id = (
            message.get("conversationId")
            or message.get("internetMessageId")
            or message.get("id")
        )

        grouped.setdefault(conversation_id, []).append(message)

    standalone_emails = []
    threaded_conversations = []

    for conversation_id, items in grouped.items():

        sorted_items = sorted(
            items,
            key=lambda x: x.get("receivedDateTime") or ""
        )

        replies = []

        for index, msg in enumerate(sorted_items, start=1):

            unique_body = msg.get("uniqueBody") or {}
            body = msg.get("body") or {}

            # IMPORTANT:
            # Prefer full body first because Outlook forwarded emails
            # may have empty or incomplete uniqueBody.
            raw_body = (
                body.get("content")
                or unique_body.get("content")
                or ""
            )

            latest_reply = extract_latest_reply(raw_body)
            cleaned_body = clean_business_body(latest_reply)

            attachment_info = {
                "image_present": False,
                "attachment_present": False,
                "attachments": []
            }

            if msg.get("hasAttachments") and access_token and user_id:
                attachments = fetch_message_attachments(
                    access_token=access_token,
                    user_id=user_id,
                    message_id=msg.get("id")
                )
                attachment_info = analyze_attachments(attachments)

            elif msg.get("hasAttachments"):
                attachment_info["attachment_present"] = True

            replies.append({
                "index": index,
                "conversation_id": conversation_id,
                "message_id": msg.get("id"),
                "subject": msg.get("subject", ""),
                "received_datetime": msg.get("receivedDateTime"),
                "body": cleaned_body,
                "image_present": attachment_info["image_present"],
                "attachment_present": attachment_info["attachment_present"],
                "attachments": attachment_info["attachments"]
            })

        thread = {
            "conversation_id": conversation_id,
            "thread_type": "threaded" if len(replies) > 1 else "standalone",
            "reply_count": len(replies),
            "replies": replies
        }

        if len(replies) > 1:
            threaded_conversations.append(thread)
        else:
            standalone_emails.append(thread)

    return {
        "standalone_emails": standalone_emails,
        "threaded_conversations": threaded_conversations
    }