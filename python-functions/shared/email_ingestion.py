import logging
import os
from dataclasses import replace

from shared.db_client import (
    apply_destination_allowlist,
    get_active_agent_prompt_text,
    get_active_watch_emails,
    get_approved_support_intake_emails,
    get_connection,
    get_destination_approved_support_intake_emails,
    get_delta_link,
    get_pending_acknowledgement_emails,
    get_pending_internal_route_emails,
    get_pending_forward_emails,
    get_pending_teams_notifications,
    get_existing_graph_message_ids,
    get_existing_thread_reply_count,
    get_new_messages_after_checkpoint,
    get_thread_memory,
    has_sent_acknowledgement_for_thread,
    find_teams_route,
    insert_email_if_new,
    mark_acknowledgement_failed,
    mark_acknowledgement_sent,
    mark_acknowledgement_skipped_duplicate_thread,
    mark_email_forward_validation_failed,
    mark_email_ready_to_forward,
    mark_internal_team_route_failed,
    mark_internal_team_route_no_match,
    mark_internal_team_route_ready,
    mark_support_intake_failed,
    mark_support_intake_sent,
    mark_teams_failed,
    mark_teams_route_pending,
    mark_teams_sent,
    match_unrouted_emails,
    prepare_acknowledgement_queue,
    save_delta_link,
    save_acknowledgement_draft,
    save_teams_intelligence,
    update_thread_memory_summary_checkpoint,
    upsert_thread_memory,
)
from shared.graph_client import (
    fetch_inbox_messages,
    send_acknowledgement_email,
    send_support_intake_email,
)
from shared.email_intelligence import (
    build_email_intelligence_from_record,
    extract_incremental_thread_intelligence,
    extract_email_intelligence,
)
from shared.teams_notifier import (
    build_teams_card_payload,
    send_teams_card,
)
from shared.thread_delta_detector import (
    build_thread_states,
    detect_thread_delta,
)

ACKNOWLEDGEMENT_BODY = (
    "Hello,\n\n"
    "Thank you for contacting us. We have received your email and our team is reviewing it.\n\n"
    "We will get back to you as soon as possible.\n\n"
    "Regards,\n"
    "Customer Support Team"
)

INTERNAL_ROUTE_KEYWORDS = {
    "Development": (
        "bug",
        "error",
        "issue",
        "api",
        "server",
        "database",
        "production",
        "login",
        "failure",
    ),
    "Testing": (
        "test",
        "testing",
        "qa",
        "uat",
        "reproduce",
        "validation",
        "defect",
    ),
    "Design": (
        "ui",
        "ux",
        "screen",
        "layout",
        "design",
        "button",
        "page",
    ),
}


def _teams_notification_batch_limit() -> int:
    raw_limit = os.getenv("TEAMS_NOTIFICATION_BATCH_LIMIT", "50").strip()
    try:
        limit = int(raw_limit)
    except ValueError:
        logging.warning(
            "Invalid TEAMS_NOTIFICATION_BATCH_LIMIT=%s; defaulting to 50",
            raw_limit,
        )
        return 50

    return max(limit, 1)


def _llm_email_intelligence_enabled() -> bool:
    return os.getenv("USE_LLM_EMAIL_INTELLIGENCE", "false").lower() == "true"


def _log_email_lengths_before_intelligence(email) -> None:
    raw_body = getattr(email, "raw_body", None) or getattr(email, "body", None) or ""
    cleaned_body = getattr(email, "cleaned_body", None) or ""
    body_preview = getattr(email, "body_preview", None) or ""
    logging.info(
        "EMAIL LENGTHS raw=%s cleaned=%s preview=%s",
        len(raw_body or ""),
        len(cleaned_body or ""),
        len(body_preview or ""),
    )


def _message_with_id(messages: list[dict], email_id: int | None) -> dict | None:
    if email_id is None:
        return None
    for message in messages:
        if int(message["id"]) == int(email_id):
            return message
    return None


def _build_incremental_or_fallback_intelligence(
    conn,
    email,
    system_prompt: str | None,
) -> tuple[object, bool]:
    _log_email_lengths_before_intelligence(email)

    if not _llm_email_intelligence_enabled():
        return extract_email_intelligence(email, system_prompt=system_prompt), True

    conversation_id = (email.conversation_id or "").strip()
    if not conversation_id:
        logging.info(
            "Thread memory check skipped: email_id=%s conversation_id=%s",
            email.id,
            conversation_id or None,
        )
        return extract_email_intelligence(email, system_prompt=system_prompt), True

    for attempt in range(2):
        retry_incremental_summary = attempt > 0
        thread_memory = get_thread_memory(conn, email.agent_id, conversation_id)
        if thread_memory:
            logging.info(
                (
                    "Thread memory found: conversation_id=%s "
                    "last_processed_email_id=%s "
                    "last_processed_received_at=%s"
                ),
                conversation_id,
                thread_memory.get("last_processed_email_id"),
                thread_memory.get("last_processed_received_at"),
            )
        else:
            logging.info("Thread memory not found: conversation_id=%s", conversation_id)

        previous_summary = (thread_memory or {}).get("thread_summary") or ""
        last_processed_email_id = (thread_memory or {}).get("last_processed_email_id")

        logging.info(
            (
                "Thread summarization checkpoint: conversation_id=%s "
                "last_processed_email_id=%s retry_incremental_summary=%s"
            ),
            conversation_id,
            last_processed_email_id,
            retry_incremental_summary,
        )

        checkpoint_messages = get_new_messages_after_checkpoint(
            conn,
            email.agent_id,
            conversation_id,
            last_processed_email_id,
        )
        new_messages = checkpoint_messages.messages
        candidate_new_message_ids = [message["id"] for message in new_messages]
        max_new_email_id = checkpoint_messages.max_processed_email_id

        logging.info(
            (
                "New thread messages count=%s conversation_id=%s "
                "candidate_new_message_ids=%s max_new_email_id=%s"
            ),
            len(new_messages),
            conversation_id,
            candidate_new_message_ids,
            max_new_email_id,
        )

        if not new_messages:
            logging.info("No new thread messages. Skipping incremental summarization.")
            return build_email_intelligence_from_record(email), False

        checkpoint_message = _message_with_id(new_messages, max_new_email_id)
        if int(email.id) != int(max_new_email_id):
            logging.info(
                (
                    "Skipping incremental summarization because email_id=%s is not "
                    "the checkpoint message for conversation_id=%s max_new_email_id=%s"
                ),
                email.id,
                conversation_id,
                max_new_email_id,
            )
            return build_email_intelligence_from_record(email), False

        logging.info(
            "Incremental summarization started: conversation_id=%s email_id=%s",
            conversation_id,
            email.id,
        )
        intelligence = extract_incremental_thread_intelligence(
            email=email,
            previous_summary=previous_summary,
            new_messages=new_messages,
            system_prompt=system_prompt,
        )
        update_succeeded = update_thread_memory_summary_checkpoint(
            conn=conn,
            agent_id=email.agent_id,
            conversation_id=conversation_id,
            latest_message_id=(
                (checkpoint_message or {}).get("graph_message_id")
            ),
            last_processed_email_id=max_new_email_id,
            last_processed_received_at=checkpoint_messages.max_processed_received_at,
            thread_status=intelligence.current_status or "open",
            thread_summary=intelligence.thread_summary,
            expected_last_processed_email_id=last_processed_email_id,
        )
        memory_update_conflict = not update_succeeded
        logging.info(
            (
                "Thread memory checkpoint update result: conversation_id=%s "
                "last_processed_email_id=%s memory_update_conflict=%s"
            ),
            conversation_id,
            max_new_email_id,
            memory_update_conflict,
        )

        if update_succeeded:
            logging.info(
                (
                    "Thread memory updated successfully: conversation_id=%s "
                    "last_processed_email_id=%s "
                    "last_processed_received_at=%s status=%s"
                ),
                conversation_id,
                max_new_email_id,
                checkpoint_messages.max_processed_received_at,
                intelligence.current_status or "open",
            )
            logging.info(
                "thread_summary_persisted conversation_id=%s formatted_thread_summary_length=%s",
                conversation_id,
                len(intelligence.thread_summary or ""),
            )
            logging.info(
                "Incremental summarization completed: conversation_id=%s email_id=%s",
                conversation_id,
                email.id,
            )
            return intelligence, True

        if attempt == 0:
            logging.info(
                (
                    "memory_update_conflict=true retry_incremental_summary=true "
                    "conversation_id=%s"
                ),
                conversation_id,
            )
            continue

        logging.warning(
            (
                "memory_update_conflict=true retry_incremental_summary=false "
                "conversation_id=%s"
            ),
            conversation_id,
        )
        return build_email_intelligence_from_record(email), False

    return build_email_intelligence_from_record(email), False


def _get_internal_team_destination(classification: str) -> str | None:
    # MVP-only mapping. Move this to SQL/frontend configuration when team mailboxes are final.
    team_destinations = {
        "Development": "kevin.walker@laserbm.net",
        "Testing": "kevin.walker@laserbm.net",
        "Design": "kevin.walker@laserbm.net",
    }
    return team_destinations.get(classification)


def _classify_internal_email(subject: str | None, body_preview: str | None) -> str | None:
    content = f"{subject or ''} {body_preview or ''}".lower()
    for classification, keywords in INTERNAL_ROUTE_KEYWORDS.items():
        if any(keyword in content for keyword in keywords):
            return classification

    return None


def route_internal_team_emails(conn) -> dict[str, int]:
    pending_emails = get_pending_internal_route_emails(conn)
    ready_count = 0
    no_match_count = 0
    failed_count = 0

    logging.info("Pending internal route count: %s", len(pending_emails))

    for email in pending_emails:
        try:
            classification = _classify_internal_email(email.subject, email.body_preview)
            if not classification:
                mark_internal_team_route_no_match(conn, email.id)
                conn.commit()
                no_match_count += 1
                logging.info("Internal route no match: email_id=%s", email.id)
                continue

            routed_to_email = _get_internal_team_destination(classification)
            if not routed_to_email:
                raise RuntimeError(
                    f"No internal team destination configured for {classification}."
                )

            mark_internal_team_route_ready(
                conn,
                email.id,
                classification,
                routed_to_email,
            )
            conn.commit()
            ready_count += 1
            logging.info(
                "Internal route ready: email_id=%s classification=%s routed_to=%s",
                email.id,
                classification,
                routed_to_email,
            )
        except Exception as exc:
            conn.rollback()
            failed_count += 1
            logging.exception("Internal route failed: email_id=%s", email.id)
            try:
                mark_internal_team_route_failed(conn, email.id, str(exc))
                conn.commit()
            except Exception:
                conn.rollback()
                logging.exception(
                    "Internal route failure status update failed: email_id=%s",
                    email.id,
                )

    logging.info(
        "Internal routing completed: ready_to_route=%s no_match=%s failed=%s",
        ready_count,
        no_match_count,
        failed_count,
    )

    return {
        "ready_to_route": ready_count,
        "no_match": no_match_count,
        "failed": failed_count,
    }


def _acknowledgement_subject(subject: str | None) -> str:
    original_subject = (subject or "").strip()
    return f"Re: {original_subject}".strip()


def send_pending_acknowledgement_emails(conn) -> dict[str, int]:
    pending_emails = get_pending_acknowledgement_emails(conn)
    sent_count = 0
    failed_count = 0
    skipped_duplicate_count = 0

    logging.info("Acknowledgement candidates found: %s", len(pending_emails))

    for email in pending_emails:
        graph_send_completed = False
        try:
            if has_sent_acknowledgement_for_thread(conn, email.id):
                mark_acknowledgement_skipped_duplicate_thread(conn, email.id)
                conn.commit()
                skipped_duplicate_count += 1
                logging.info(
                    "Acknowledgement skipped duplicate thread: email_id=%s conversation_id=%s",
                    email.id,
                    email.conversation_id,
                )
                continue

            acknowledgement_subject = _acknowledgement_subject(email.subject)
            save_acknowledgement_draft(
                conn,
                email.id,
                acknowledgement_subject,
                ACKNOWLEDGEMENT_BODY,
            )
            logging.info(
                "Sending acknowledgement from %s to %s",
                email.acknowledgement_source_email,
                email.acknowledgement_destination_email,
            )
            send_acknowledgement_email(
                email.acknowledgement_source_email,
                email.acknowledgement_destination_email,
                acknowledgement_subject,
                ACKNOWLEDGEMENT_BODY,
            )
            graph_send_completed = True
            mark_acknowledgement_sent(conn, email.id)
            conn.commit()
            sent_count += 1
        except Exception as exc:
            conn.rollback()
            failed_count += 1
            if graph_send_completed:
                logging.warning(
                    (
                        "Acknowledgement email may already have been delivered "
                        "before DB update failure."
                    )
                )
            logging.exception("Acknowledgement send failed: email_id=%s", email.id)
            try:
                mark_acknowledgement_failed(conn, email.id, str(exc))
                conn.commit()
            except Exception:
                conn.rollback()
                logging.exception(
                    "Acknowledgement failure status update failed: email_id=%s",
                    email.id,
                )

    logging.info(
        "Acknowledgement sent: %s failed=%s skipped_duplicate=%s",
        sent_count,
        failed_count,
        skipped_duplicate_count,
    )

    return {
        "sent": sent_count,
        "failed": failed_count,
        "skipped_duplicate": skipped_duplicate_count,
    }


def _support_intake_subject(subject: str | None) -> str:
    original_subject = (subject or "").strip() or "(no subject)"
    return f"[Support Intake] {original_subject}"


def _support_intake_body(email) -> str:
    received_at = email.received_at.isoformat(sep=" ") if email.received_at else ""
    full_body_for_support = (
        getattr(email, "cleaned_body", None)
        or getattr(email, "body", None)
        or getattr(email, "body_preview", None)
        or ""
    )
    return (
        f"Client Email: {email.sender_email or ''}\n"
        f"BA Mailbox: {email.mailbox_email or ''}\n"
        f"Received: {received_at}\n"
        f"Original Subject: {email.subject or ''}\n\n"
        "Full Email Body:\n\n"
        f"{full_body_for_support}"
    )


def send_pending_teams_notifications(conn, system_prompt: str | None = None) -> dict[str, int]:
    pending_emails = get_pending_teams_notifications(conn)
    batch_limit = _teams_notification_batch_limit()
    total_pending_emails = len(pending_emails)
    pending_emails = pending_emails[:batch_limit]
    cursor = conn.cursor()
    sent_count = 0
    failed_count = 0
    channel_missing_count = 0

    logging.info(
        "Teams pending notifications: total=%s processing=%s batch_limit=%s",
        total_pending_emails,
        len(pending_emails),
        batch_limit,
    )

    for email in pending_emails:
        try:
            has_persisted_intelligence = bool(
                email.issue_summary
                or email.priority
                or email.module
                or email.assigned_team
            )
            if has_persisted_intelligence:
                intelligence = build_email_intelligence_from_record(email)
                should_persist_intelligence = False
            else:
                intelligence, should_persist_intelligence = (
                    _build_incremental_or_fallback_intelligence(
                        conn,
                        email,
                        system_prompt,
                    )
                )

            if should_persist_intelligence:
                save_teams_intelligence(
                    conn,
                    email.id,
                    intelligence,
                )
                conn.commit()

            logging.info(
                (
                    "Teams intelligence resolved: email_id=%s persisted=%s "
                    "issue_summary=%s priority_score=%s "
                    "priority_confidence=%s module=%s "
                    "module_confidence=%s assigned_team=%s "
                    "assigned_team_confidence=%s review_required=%s "
                    "routing=%s"
                ),
                email.id,
                should_persist_intelligence,
                intelligence.issue_summary,
                intelligence.priority_score,
                intelligence.priority_confidence,
                intelligence.module,
                intelligence.module_confidence,
                intelligence.assigned_team,
                intelligence.assigned_team_confidence,
                intelligence.review_required,
                intelligence.routing,
            )
            organization_name = intelligence.client_name
            product_name = intelligence.product_name
            expected_channel = (
                f"KT - {(organization_name or '').strip()} - "
                f"{(product_name or '').strip()}"
            )
            polled_mailbox = (
                getattr(email, "polled_mailbox", None)
                or getattr(email, "watch_mailbox", None)
                or ""
            ).strip()
            email_source_email = (getattr(email, "source_email", None) or "").strip()
            support_mailbox = (getattr(email, "support_mailbox", None) or "").strip()
            routed_to_email = (getattr(email, "routed_to_email", None) or "").strip()

            source_email = (
                support_mailbox
                or routed_to_email
                or polled_mailbox
                or email_source_email
            )

            logging.info(
                (
                    "Teams route lookup context: email_id=%s "
                    "graph_message_id=%s polled_mailbox=%s "
                    "email.source_email=%s original_sender_email=%s "
                    "organization=%s product=%s expected_channel=%s"
                ),
                email.id,
                getattr(email, "graph_message_id", None),
                polled_mailbox or None,
                email_source_email or None,
                getattr(email, "original_sender_email", None) or email.sender_email,
                organization_name,
                product_name,
                expected_channel,
            )

            route = find_teams_route(
                cursor=cursor,
                source_email=source_email,
                organization_name=organization_name,
                product_name=product_name,
            )

            if route is None:
                teams_status = "PENDING_ROUTE_CONFIG"
                teams_channel_name = email.teams_channel_name
                webhook_url = None
            else:
                teams_status = "READY_TO_SEND"
                teams_channel_name = route["teams_channel_name"]
                webhook_url = route["webhook_url"]

            if teams_status == "READY_TO_SEND" and webhook_url:
                logging.info(
                    (
                        "Teams route resolved: %s original_sender_email=%s "
                        "support_mailbox=%s routed_to_email=%s"
                    ),
                    teams_channel_name,
                    getattr(email, "original_sender_email", None) or email.sender_email,
                    getattr(email, "support_mailbox", None),
                    getattr(email, "routed_to_email", None),
                )
                payload = build_teams_card_payload(email, intelligence)
                send_teams_card(webhook_url, payload)
                mark_teams_sent(conn, email.id, teams_channel_name)
                conn.commit()
                sent_count += 1
            else:
                mark_teams_route_pending(conn, email.id, teams_channel_name)
                conn.commit()
                channel_missing_count += 1
                logging.warning(
                    (
                        "Teams route pending. source_email=%s, "
                        "polled_mailbox=%s, email.source_email=%s, "
                        "organization=%s, product=%s, expected_channel=%s"
                    ),
                    source_email,
                    polled_mailbox or None,
                    email_source_email or None,
                    organization_name,
                    product_name,
                    expected_channel,
                )
        except Exception as exc:
            conn.rollback()
            failed_count += 1
            logging.exception("Teams notification failed: email_id=%s", email.id)
            try:
                mark_teams_failed(conn, email.id, str(exc))
                conn.commit()
            except Exception:
                conn.rollback()
                logging.exception(
                    "Teams notification failure status update failed: email_id=%s",
                    email.id,
                )

    logging.info(
        "Teams notification sent: %s failed=%s channel_missing=%s",
        sent_count,
        failed_count,
        channel_missing_count,
    )

    return {
        "sent": sent_count,
        "failed": failed_count,
        "channel_missing": channel_missing_count,
    }


def send_approved_support_intake_emails(conn) -> dict[str, int]:
    approved_emails = get_approved_support_intake_emails(conn)
    sent_count = 0
    failed_count = 0

    logging.info("Approved support intake count: %s", len(approved_emails))

    for email in approved_emails:
        logging.info(
            "Sending support intake email_id=%s from BA mailbox=%s to source mailbox=%s",
            email.id,
            email.mailbox_email,
            email.routed_to_email,
        )

        try:
            send_support_intake_email(
                email.mailbox_email,
                email.routed_to_email,
                _support_intake_subject(email.subject),
                _support_intake_body(email),
            )
            mark_support_intake_sent(conn, email.id)
            conn.commit()
            sent_count += 1
        except Exception as exc:
            conn.rollback()
            failed_count += 1
            logging.exception("Support intake send failed: email_id=%s", email.id)
            try:
                mark_support_intake_failed(conn, email.id, str(exc))
                conn.commit()
            except Exception:
                conn.rollback()
                logging.exception(
                    "Support intake failure status update failed: email_id=%s",
                    email.id,
                )

    logging.info(
        "Support intake send completed: sent=%s failed=%s",
        sent_count,
        failed_count,
    )

    return {
        "sent": sent_count,
        "failed": failed_count,
    }


def _validate_pending_forward_email(email) -> str | None:
    if not (email.mailbox_email or "").strip():
        return "Missing watch mailbox email for forwarding."
    if not (email.routed_to_email or "").strip():
        return "Missing routed_to_email for forwarding."
    if not (email.graph_message_id or "").strip():
        return "Missing graph_message_id for forwarding."

    return None


def prepare_forward_queue(conn) -> dict[str, int]:
    pending_forward_emails = get_pending_forward_emails(conn)
    ready_count = 0
    validation_failed_count = 0

    logging.info("Pending forward queue count: %s", len(pending_forward_emails))

    for email in pending_forward_emails:
        logging.info(
            "Preparing forward queue email_id=%s mailbox=%s routed_to=%s",
            email.id,
            email.mailbox_email,
            email.routed_to_email,
        )
        validation_error = _validate_pending_forward_email(email)

        if validation_error:
            mark_email_forward_validation_failed(conn, email.id, validation_error)
            validation_failed_count += 1
            logging.warning(
                "Forward queue validation failed: email_id=%s error=%s",
                email.id,
                validation_error,
            )
            continue

        mark_email_ready_to_forward(conn, email.id)
        ready_count += 1

    logging.info(
        "Forward queue preparation completed: ready_to_forward=%s validation_failed=%s",
        ready_count,
        validation_failed_count,
    )

    return {
        "ready_to_forward": ready_count,
        "validation_failed": validation_failed_count,
    }


def poll_active_watch_mailboxes() -> dict[str, int]:
    totals = {
        "mailboxes": 0,
        "inserted": 0,
        "skipped": 0,
        "errors": 0,
        "route_matched": 0,
        "route_no_match": 0,
        "internal_ready_to_route": 0,
        "internal_no_match": 0,
        "internal_failed": 0,
        "forward_ready": 0,
        "forward_validation_failed": 0,
        "teams_sent": 0,
        "teams_failed": 0,
        "teams_channel_missing": 0,
        "forward_approved_destination": 0,
        "forward_blocked_destination": 0,
        "support_intake_approved_destination": 0,
        "support_intake_sent": 0,
        "support_intake_failed": 0,
        "acknowledgement_ready": 0,
        "acknowledgement_source_missing": 0,
        "acknowledgement_destination_invalid": 0,
        "acknowledgement_skipped_duplicate": 0,
        "acknowledgement_sent": 0,
        "acknowledgement_failed": 0,
    }

    with get_connection() as conn:
        active_agent_prompt = get_active_agent_prompt_text(conn)
        if active_agent_prompt:
            logging.info("Active agent prompt loaded for this function run.")
        else:
            logging.info("No active agent prompt found; using fallback prompt.")

        watch_emails = get_active_watch_emails(conn)
        totals["mailboxes"] = len(watch_emails)
        logging.info("Active watch emails found: %s", len(watch_emails))

        for watch_email in watch_emails:
            inserted_count = 0
            skipped_count = 0
            logging.info("Polling mailbox: %s", watch_email.email)

            try:
                delta_link = get_delta_link(conn, watch_email.watch_email_id)
                graph_result = fetch_inbox_messages(watch_email.email, delta_link=delta_link)
                active_messages = []

                for message in graph_result.messages:
                    if message.get("@removed"):
                        skipped_count += 1
                        continue

                    active_messages.append(message)

                thread_states = build_thread_states(active_messages)
                changed_thread_states = []
                changed_thread_count = 0
                unchanged_thread_count = 0

                for thread_state in thread_states:
                    graph_message_ids = [
                        message.get("id")
                        for message in thread_state.messages
                        if message.get("id")
                    ]
                    existing_graph_message_ids = get_existing_graph_message_ids(
                        conn,
                        graph_message_ids,
                    )
                    existing_reply_count = get_existing_thread_reply_count(
                        conn,
                        watch_email.agent_id,
                        thread_state.conversation_id,
                    )
                    effective_reply_count = existing_reply_count + sum(
                        1
                        for graph_message_id in graph_message_ids
                        if graph_message_id not in existing_graph_message_ids
                    )
                    thread_state = replace(
                        thread_state,
                        latest_reply_count=max(
                            thread_state.latest_reply_count,
                            effective_reply_count,
                        ),
                    )
                    stored_memory = get_thread_memory(
                        conn,
                        watch_email.agent_id,
                        thread_state.conversation_id,
                    )
                    delta = detect_thread_delta(thread_state, stored_memory)

                    if not delta.should_process:
                        unchanged_thread_count += 1
                        skipped_count += len(thread_state.messages)
                        logging.info(
                            "Skipping unchanged thread conversation_id=%s",
                            thread_state.conversation_id,
                        )
                        continue

                    changed_thread_count += 1
                    changed_thread_states.append(thread_state)
                    logging.info(
                        (
                            "Processing thread conversation_id=%s "
                            "delta_type=%s reason=%s"
                        ),
                        thread_state.conversation_id,
                        delta.delta_type,
                        delta.reason,
                    )

                    for message in thread_state.messages:
                        insert_result = insert_email_if_new(conn, watch_email, message)
                        if insert_result.inserted:
                            inserted_count += 1
                            sender = message.get("from", {}).get("emailAddress", {})
                            logging.info(
                                (
                                    "Email inserted. original_sender_email=%s "
                                    "support_mailbox=%s routed_to_email=%s "
                                    "subject=%s graph_message_id=%s"
                                ),
                                sender.get("address"),
                                None,
                                None,
                                message.get("subject"),
                                message.get("id"),
                            )
                        else:
                            sender = message.get("from", {}).get("emailAddress", {})
                            logging.info(
                                (
                                    "Email skipped as duplicate. reason=%s, "
                                    "sender=%s, subject=%s, graph_message_id=%s"
                                ),
                                insert_result.duplicate_reason,
                                sender.get("address"),
                                message.get("subject"),
                                message.get("id"),
                            )
                            skipped_count += 1

                logging.info(
                    "Thread delta detected: changed=%s unchanged=%s",
                    changed_thread_count,
                    unchanged_thread_count,
                )

                for thread_state in changed_thread_states:
                    upsert_thread_memory(
                        conn,
                        watch_email.agent_id,
                        thread_state,
                    )

                save_delta_link(conn, watch_email, graph_result.delta_link)
                conn.commit()
            except Exception:
                conn.rollback()
                totals["errors"] += 1
                logging.exception("Polling mailbox failed: %s", watch_email.email)
            finally:
                totals["inserted"] += inserted_count
                totals["skipped"] += skipped_count
                logging.info(
                    "Mailbox polling result: %s Inserted: %s Skipped duplicates: %s",
                    watch_email.email,
                    inserted_count,
                    skipped_count,
                )

        try:
            route_result = match_unrouted_emails(conn)
            conn.commit()
            totals["route_matched"] = route_result.matched
            totals["route_no_match"] = route_result.no_match
            logging.info(
                "Route matching completed: matched=%s no_match=%s",
                route_result.matched,
                route_result.no_match,
            )
        except Exception:
            conn.rollback()
            totals["errors"] += 1
            logging.exception("Route matching failed")

        try:
            internal_route_result = route_internal_team_emails(conn)
            totals["internal_ready_to_route"] = internal_route_result["ready_to_route"]
            totals["internal_no_match"] = internal_route_result["no_match"]
            totals["internal_failed"] = internal_route_result["failed"]
        except Exception:
            conn.rollback()
            totals["errors"] += 1
            logging.exception("Internal routing batch failed")

        try:
            forward_result = prepare_forward_queue(conn)
            conn.commit()
            totals["forward_ready"] = forward_result["ready_to_forward"]
            totals["forward_validation_failed"] = forward_result["validation_failed"]
        except Exception:
            conn.rollback()
            totals["errors"] += 1
            logging.exception("Forward queue preparation failed")

        try:
            allowlist_result = apply_destination_allowlist(conn)
            conn.commit()
            totals["forward_approved_destination"] = allowlist_result.approved
            totals["forward_blocked_destination"] = allowlist_result.blocked
            logging.info(
                "Destination allowlist completed: approved_destination=%s blocked_destination=%s",
                allowlist_result.approved,
                allowlist_result.blocked,
            )
        except Exception:
            conn.rollback()
            totals["errors"] += 1
            logging.exception("Destination allowlist validation failed")

        try:
            support_intake_approval_result = get_destination_approved_support_intake_emails(conn)
            conn.commit()
            totals["support_intake_approved_destination"] = (
                support_intake_approval_result.approved
            )
            logging.info(
                "Support intake destination approval completed: approved_destination=%s",
                support_intake_approval_result.approved,
            )
        except Exception:
            conn.rollback()
            totals["errors"] += 1
            logging.exception("Support intake destination approval failed")

        try:
            support_intake_result = send_approved_support_intake_emails(conn)
            totals["support_intake_sent"] = support_intake_result["sent"]
            totals["support_intake_failed"] = support_intake_result["failed"]
        except Exception:
            conn.rollback()
            totals["errors"] += 1
            logging.exception("Support intake send batch failed")

        try:
            teams_result = send_pending_teams_notifications(
                conn,
                system_prompt=active_agent_prompt,
            )
            totals["teams_sent"] = teams_result["sent"]
            totals["teams_failed"] = teams_result["failed"]
            totals["teams_channel_missing"] = teams_result["channel_missing"]
        except Exception:
            conn.rollback()
            totals["errors"] += 1
            logging.exception("Teams notification batch failed")

        try:
            acknowledgement_result = prepare_acknowledgement_queue(conn)
            conn.commit()
            totals["acknowledgement_ready"] = acknowledgement_result.ready
            totals["acknowledgement_source_missing"] = acknowledgement_result.source_missing
            totals["acknowledgement_destination_invalid"] = acknowledgement_result.destination_invalid
            totals["acknowledgement_skipped_duplicate"] = (
                acknowledgement_result.skipped_duplicate
            )
            logging.info(
                (
                    "Acknowledgement queue preparation completed: ready=%s "
                    "source_missing=%s destination_invalid=%s skipped_duplicate=%s"
                ),
                acknowledgement_result.ready,
                acknowledgement_result.source_missing,
                acknowledgement_result.destination_invalid,
                acknowledgement_result.skipped_duplicate,
            )
        except Exception:
            conn.rollback()
            totals["errors"] += 1
            logging.exception("Acknowledgement queue preparation failed")

        try:
            acknowledgement_send_result = send_pending_acknowledgement_emails(conn)
            totals["acknowledgement_sent"] = acknowledgement_send_result["sent"]
            totals["acknowledgement_failed"] = acknowledgement_send_result["failed"]
            totals["acknowledgement_skipped_duplicate"] += (
                acknowledgement_send_result["skipped_duplicate"]
            )
        except Exception:
            conn.rollback()
            totals["errors"] += 1
            logging.exception("Acknowledgement send batch failed")

    return totals
