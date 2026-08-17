import os
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pyodbc

from shared.email_cleaner import (
    clean_email_body,
    extract_issue_summary,
    extract_latest_reply,
    get_graph_message_body,
)
from shared.email_intelligence import EmailIntelligence


@dataclass(frozen=True)
class WatchEmail:
    watch_email_id: int
    agent_id: int
    email: str
    status: str
    type: str | None = None


@dataclass(frozen=True)
class InsertResult:
    inserted: bool
    skipped_duplicate: bool
    duplicate_reason: str | None = None


NOISY_SYSTEM_SENDERS = {
    "no-reply@sharepointonline.com",
}


@dataclass(frozen=True)
class RouteMatchResult:
    matched: int
    no_match: int


@dataclass(frozen=True)
class DestinationAllowlistResult:
    approved: int
    blocked: int


@dataclass(frozen=True)
class DestinationApprovedSupportIntakeResult:
    approved: int


@dataclass(frozen=True)
class TeamsNotificationResult:
    sent: int
    failed: int
    channel_missing: int


@dataclass(frozen=True)
class InternalRoutingResult:
    ready: int
    no_match: int
    failed: int


@dataclass(frozen=True)
class AcknowledgementQueueResult:
    ready: int
    source_missing: int
    destination_invalid: int
    skipped_duplicate: int


@dataclass(frozen=True)
class PendingAcknowledgementEmail:
    id: int
    conversation_id: str | None
    subject: str | None
    sender_email: str | None
    acknowledgement_source_email: str | None
    acknowledgement_destination_email: str | None


@dataclass(frozen=True)
class PendingForwardEmail:
    id: int
    graph_message_id: str | None
    mailbox_email: str | None
    routed_to_email: str | None
    sender_email: str | None
    subject: str | None


@dataclass(frozen=True)
class ApprovedSupportIntakeEmail:
    id: int
    sender_email: str | None
    subject: str | None
    body_preview: str | None
    cleaned_body: str | None
    body: str | None
    received_at: datetime | None
    mailbox_email: str | None
    routed_to_email: str | None


@dataclass(frozen=True)
class PendingInternalRouteEmail:
    id: int
    sender_email: str | None
    subject: str | None
    body_preview: str | None


@dataclass(frozen=True)
class ReadyInternalRouteEmail:
    id: int
    sender_email: str | None
    subject: str | None
    body_preview: str | None
    received_at: datetime | None
    mailbox_email: str | None
    routed_to_email: str | None


@dataclass(frozen=True)
class PendingTeamsNotificationEmail:
    id: int
    agent_id: int
    graph_message_id: str | None
    conversation_id: str | None
    conversation_index: str | None
    source_email: str | None
    polled_mailbox: str | None
    watch_mailbox: str | None
    teams_from_email: str | None
    teams_channel_name: str | None
    sender_email: str | None
    original_sender_name: str | None
    original_sender_email: str | None
    support_mailbox: str | None
    routed_to_email: str | None
    subject: str | None
    body_preview: str | None
    cleaned_body: str | None
    has_attachments: bool | None
    received_at: datetime | None
    destination_organization: str | None
    destination_product_name: str | None
    issue_summary: str | None = None
    priority: str | None = None
    priority_score: float | None = None
    priority_reason: str | None = None
    priority_confidence: float | None = None
    module: str | None = None
    module_confidence: float | None = None
    domain: str | None = None
    intent: str | None = None
    assigned_team: str | None = None
    assigned_team_confidence: float | None = None
    review_required: bool | None = None
    routing: str | None = None
    thread_summary: str | None = None
    current_status: str | None = None
    communication_type: str | None = None
    teams_template: str | None = None


@dataclass(frozen=True)
class TeamsChannel:
    channel_name: str
    webhook_url: str


@dataclass(frozen=True)
class ThreadMemory:
    agent_id: int
    conversation_id: str
    last_message_id: str | None
    last_conversation_index: str | None
    last_processed_email_id: int | None
    last_processed_received_at: datetime | None
    last_reply_count: int | None
    last_thread_status: str | None
    last_processed_at: datetime | None
    thread_summary: str | None


@dataclass(frozen=True)
class ThreadCheckpointMessages:
    messages: list[dict[str, Any]]
    max_processed_email_id: int | None
    max_processed_received_at: datetime | None


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _odbc_bool_env(name: str, default: str) -> str:
    value = os.getenv(name, default).strip().lower()
    if value in {"1", "true", "yes"}:
        return "yes"
    if value in {"0", "false", "no"}:
        return "no"
    return value


def _connection_string() -> str:
    server = _required_env("DB_SERVER")
    database = _required_env("DB_NAME")
    user = _required_env("DB_USER")
    password = _required_env("DB_PASSWORD")
    driver = os.getenv("DB_ODBC_DRIVER", "ODBC Driver 17 for SQL Server")
    encrypt = _odbc_bool_env("DB_ENCRYPT", "yes")
    trust_server_certificate = _odbc_bool_env("DB_TRUST_SERVER_CERTIFICATE", "yes")

    return (
        f"DRIVER={{{driver}}};"
        f"SERVER={server};"
        f"DATABASE={database};"
        f"UID={user};"
        f"PWD={password};"
        f"Encrypt={encrypt};"
        f"TrustServerCertificate={trust_server_certificate};"
    )


def get_connection() -> pyodbc.Connection:
    return pyodbc.connect(_connection_string())


def get_active_agent_prompt_text(conn: pyodbc.Connection) -> str | None:
    cursor = conn.cursor()
    table_exists = cursor.execute(
        "SELECT CASE WHEN OBJECT_ID(N'dbo.agent_prompts', N'U') IS NULL THEN 0 ELSE 1 END"
    ).fetchval()

    if not table_exists:
        return None

    cursor.execute(
        """
        SELECT TOP (1)
            prompt_text
        FROM dbo.agent_prompts
        WHERE status = N'active'
          AND prompt_text IS NOT NULL
          AND LTRIM(RTRIM(prompt_text)) <> N''
        ORDER BY updated_at DESC, id DESC
        """
    )
    row = cursor.fetchone()
    return row.prompt_text if row else None


def _column_exists(conn: pyodbc.Connection, table_name: str, column_name: str) -> bool:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT CASE WHEN COL_LENGTH(?, ?) IS NULL THEN 0 ELSE 1 END",
        f"dbo.{table_name}",
        column_name,
    )
    return bool(cursor.fetchone()[0])


def get_active_watch_emails(conn: pyodbc.Connection) -> list[WatchEmail]:
    cursor = conn.cursor()
    has_type_column = cursor.execute(
        "SELECT CASE WHEN COL_LENGTH('dbo.watch_emails', 'type') IS NULL THEN 0 ELSE 1 END"
    ).fetchval()
    type_select = "type" if has_type_column else "CAST(NULL AS NVARCHAR(100))"

    cursor.execute(
        f"""
        SELECT
            id AS watch_email_id,
            agent_id,
            email,
            status,
            {type_select} AS type
        FROM dbo.watch_emails
        WHERE LOWER(LTRIM(RTRIM(status))) = 'active'
        ORDER BY id
        """
    )

    rows = cursor.fetchall()
    return [
        WatchEmail(
            watch_email_id=int(row.watch_email_id),
            agent_id=int(row.agent_id),
            email=row.email,
            status=row.status,
            type=row.type,
        )
        for row in rows
    ]


def get_delta_link(conn: pyodbc.Connection, watch_email_id: int) -> str | None:
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT delta_link
        FROM dbo.email_polling_state
        WHERE watch_email_id = ?
        """,
        watch_email_id,
    )
    row = cursor.fetchone()
    return row.delta_link if row else None


def save_delta_link(
    conn: pyodbc.Connection,
    watch_email: WatchEmail,
    delta_link: str | None,
) -> None:
    if not delta_link:
        return

    cursor = conn.cursor()
    cursor.execute(
        """
        MERGE dbo.email_polling_state AS target
        USING (
            SELECT
                ? AS watch_email_id,
                ? AS agent_id,
                ? AS mailbox_email,
                ? AS delta_link
        ) AS source
        ON target.watch_email_id = source.watch_email_id
        WHEN MATCHED THEN
            UPDATE SET
                agent_id = source.agent_id,
                mailbox_email = source.mailbox_email,
                delta_link = source.delta_link,
                last_polled_at = SYSUTCDATETIME(),
                updated_at = SYSUTCDATETIME()
        WHEN NOT MATCHED THEN
            INSERT (watch_email_id, agent_id, mailbox_email, delta_link, last_polled_at)
            VALUES (
                source.watch_email_id,
                source.agent_id,
                source.mailbox_email,
                source.delta_link,
                SYSUTCDATETIME()
            );
        """,
        watch_email.watch_email_id,
        watch_email.agent_id,
        watch_email.email,
        delta_link,
    )


def _parse_graph_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    parsed_value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed_value.tzinfo is None:
        return parsed_value

    return parsed_value.astimezone(timezone.utc).replace(tzinfo=None)


def _log_excerpt(value: str | None, limit: int = 1000) -> str:
    text = (value or "").replace("\r", " ").replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def get_thread_memory(
    conn: pyodbc.Connection,
    agent_id: int,
    conversation_id: str,
) -> dict[str, Any] | None:
    last_processed_email_id_select = (
        "last_processed_email_id"
        if _column_exists(conn, "email_thread_memory", "last_processed_email_id")
        else "CAST(NULL AS INT)"
    )
    last_processed_received_at_select = (
        "last_processed_received_at"
        if _column_exists(conn, "email_thread_memory", "last_processed_received_at")
        else "CAST(NULL AS DATETIME2)"
    )
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT TOP (1)
            agent_id,
            conversation_id,
            last_message_id,
            last_conversation_index,
            {last_processed_email_id_select} AS last_processed_email_id,
            {last_processed_received_at_select} AS last_processed_received_at,
            last_reply_count,
            last_thread_status,
            last_processed_at,
            thread_summary
        FROM dbo.email_thread_memory
        WHERE agent_id = ?
          AND conversation_id = ?
        ORDER BY id
        """,
        agent_id,
        conversation_id,
    )
    row = cursor.fetchone()
    if not row:
        return None

    return {
        "agent_id": int(row.agent_id),
        "conversation_id": row.conversation_id,
        "last_message_id": row.last_message_id,
        "last_conversation_index": row.last_conversation_index,
        "last_processed_email_id": (
            int(row.last_processed_email_id)
            if row.last_processed_email_id is not None
            else None
        ),
        "last_processed_received_at": row.last_processed_received_at,
        "last_reply_count": (
            int(row.last_reply_count)
            if row.last_reply_count is not None
            else None
        ),
        "last_thread_status": row.last_thread_status,
        "last_processed_at": row.last_processed_at,
        "thread_summary": row.thread_summary,
    }


def get_thread_summary(conversation_id: str) -> dict[str, Any] | None:
    if not (conversation_id or "").strip():
        return None

    with get_connection() as conn:
        return _get_thread_summary_with_connection(conn, conversation_id)


def _get_thread_summary_with_connection(
    conn: pyodbc.Connection,
    conversation_id: str,
) -> dict[str, Any] | None:
    optional_columns = {
        "unresolved_status": (
            "unresolved_status"
            if _column_exists(conn, "email_thread_memory", "unresolved_status")
            else "CAST(NULL AS NVARCHAR(50))"
        ),
        "current_priority": (
            "current_priority"
            if _column_exists(conn, "email_thread_memory", "current_priority")
            else "CAST(NULL AS NVARCHAR(50))"
        ),
        "current_assigned_team": (
            "current_assigned_team"
            if _column_exists(conn, "email_thread_memory", "current_assigned_team")
            else "CAST(NULL AS NVARCHAR(100))"
        ),
        "current_module": (
            "current_module"
            if _column_exists(conn, "email_thread_memory", "current_module")
            else "CAST(NULL AS NVARCHAR(100))"
        ),
        "current_intent": (
            "current_intent"
            if _column_exists(conn, "email_thread_memory", "current_intent")
            else "CAST(NULL AS NVARCHAR(100))"
        ),
    }
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT TOP (1)
            conversation_id,
            thread_summary,
            last_message_id AS latest_message_id,
            last_conversation_index AS latest_conversation_index,
            {optional_columns["unresolved_status"]} AS unresolved_status,
            {optional_columns["current_priority"]} AS current_priority,
            {optional_columns["current_assigned_team"]} AS current_assigned_team,
            {optional_columns["current_module"]} AS current_module,
            {optional_columns["current_intent"]} AS current_intent
        FROM dbo.email_thread_memory
        WHERE conversation_id = ?
        ORDER BY updated_at DESC, id DESC
        """,
        conversation_id,
    )
    row = cursor.fetchone()
    if not row:
        return None

    return {
        "conversation_id": row.conversation_id,
        "thread_summary": row.thread_summary or "",
        "latest_message_id": row.latest_message_id,
        "latest_conversation_index": row.latest_conversation_index or "",
        "unresolved_status": row.unresolved_status or "",
        "current_priority": row.current_priority or "",
        "current_assigned_team": row.current_assigned_team or "",
        "current_module": row.current_module or "",
        "current_intent": row.current_intent or "",
    }


def get_recent_thread_messages(
    conversation_id: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    if not (conversation_id or "").strip():
        return []

    safe_limit = max(int(limit or 5), 1)
    with get_connection() as conn:
        cursor = conn.cursor()
        cleaned_body_select = (
            "email.cleaned_body"
            if _column_exists(conn, "emails", "cleaned_body")
            else "CAST(NULL AS NVARCHAR(MAX))"
        )
        conversation_index_select = (
            "email.conversation_index"
            if _column_exists(conn, "emails", "conversation_index")
            else "CAST(NULL AS NVARCHAR(MAX))"
        )
        cursor.execute(
            f"""
            SELECT *
            FROM (
                SELECT TOP ({safe_limit})
                    email.id,
                    email.sender_email,
                    email.sender_name,
                    email.subject,
                    email.received_at,
                    {cleaned_body_select} AS cleaned_body,
                    {conversation_index_select} AS conversation_index
                FROM dbo.emails AS email
                WHERE email.conversation_id = ?
                ORDER BY email.received_at DESC, email.id DESC
            ) AS recent_messages
            ORDER BY received_at ASC, id ASC
            """,
            conversation_id,
        )
        return [
            {
                "id": int(row.id),
                "sender_email": row.sender_email or "",
                "sender_name": row.sender_name or "",
                "subject": row.subject or "",
                "received_at": row.received_at,
                "cleaned_body": row.cleaned_body or "",
            }
            for row in cursor.fetchall()
        ]


def get_new_messages_after_checkpoint(
    conn: pyodbc.Connection,
    agent_id: int,
    conversation_id: str,
    last_processed_email_id: int | None,
) -> ThreadCheckpointMessages:
    if not (conversation_id or "").strip() or agent_id is None:
        return ThreadCheckpointMessages([], None, None)

    cleaned_body_select = (
        "email.cleaned_body"
        if _column_exists(conn, "emails", "cleaned_body")
        else "CAST(NULL AS NVARCHAR(MAX))"
    )
    body_select = (
        "email.body"
        if _column_exists(conn, "emails", "body")
        else "CAST(NULL AS NVARCHAR(MAX))"
    )
    conversation_index_select = (
        "email.conversation_index"
        if _column_exists(conn, "emails", "conversation_index")
        else "CAST(NULL AS NVARCHAR(MAX))"
    )
    original_sender_email_select = (
        "email.original_sender_email"
        if _column_exists(conn, "emails", "original_sender_email")
        else "CAST(NULL AS NVARCHAR(255))"
    )
    original_sender_name_select = (
        "email.original_sender_name"
        if _column_exists(conn, "emails", "original_sender_name")
        else "CAST(NULL AS NVARCHAR(255))"
    )

    filters = ["email.conversation_id = ?"]
    params: list[Any] = [conversation_id]

    filters.append("email.agent_id = ?")
    params.append(agent_id)

    filters.append("(? IS NULL OR email.id > ?)")
    params.extend([last_processed_email_id, last_processed_email_id])

    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT
            email.id,
            email.agent_id,
            email.graph_message_id,
            email.conversation_id,
            {conversation_index_select} AS conversation_index,
            email.sender_email,
            email.sender_name,
            {original_sender_email_select} AS original_sender_email,
            {original_sender_name_select} AS original_sender_name,
            email.subject,
            email.received_at,
            email.body_preview,
            COALESCE({cleaned_body_select}, {body_select}, email.body_preview) AS body_text
        FROM dbo.emails AS email
        WHERE {" AND ".join(filters)}
        ORDER BY email.received_at ASC, email.id ASC
        """,
        *params,
    )

    messages = [
        {
            "id": int(row.id),
            "agent_id": int(row.agent_id) if row.agent_id is not None else None,
            "graph_message_id": row.graph_message_id,
            "conversation_id": row.conversation_id,
            "conversation_index": row.conversation_index,
            "sender_email": row.sender_email or "",
            "sender_name": row.sender_name or "",
            "original_sender_email": row.original_sender_email or "",
            "original_sender_name": row.original_sender_name or "",
            "subject": row.subject or "",
            "received_at": row.received_at,
            "body_preview": row.body_preview or "",
            "body_text": row.body_text or "",
        }
        for row in cursor.fetchall()
    ]

    if not messages:
        return ThreadCheckpointMessages([], None, None)

    max_message = max(messages, key=lambda message: int(message["id"]))
    return ThreadCheckpointMessages(
        messages=messages,
        max_processed_email_id=int(max_message["id"]),
        max_processed_received_at=max_message.get("received_at"),
    )


def upsert_thread_summary(
    conversation_id: str,
    thread_summary: str,
    latest_message_id=None,
    latest_conversation_index: str | None = None,
    priority: str | None = None,
    assigned_team: str | None = None,
    module: str | None = None,
    intent: str | None = None,
    unresolved_status: str | None = None,
    agent_id: int | None = None,
    latest_processed_email_id: int | None = None,
    latest_processed_received_at: datetime | None = None,
) -> None:
    if not (conversation_id or "").strip() or not (thread_summary or "").strip():
        return

    with get_connection() as conn:
        cursor = conn.cursor()
        column_values: list[tuple[str, Any]] = [
            ("last_message_id", str(latest_message_id) if latest_message_id is not None else None),
            ("last_conversation_index", latest_conversation_index),
            ("thread_summary", thread_summary),
            ("last_processed_at", None),
            ("updated_at", None),
        ]
        optional_values = (
            ("unresolved_status", unresolved_status),
            ("current_priority", priority),
            ("current_assigned_team", assigned_team),
            ("current_module", module),
            ("current_intent", intent),
        )
        for column_name, value in optional_values:
            if _column_exists(conn, "email_thread_memory", column_name):
                column_values.append((column_name, value))
        checkpoint_values = (
            ("last_processed_email_id", latest_processed_email_id),
            ("last_processed_received_at", latest_processed_received_at),
        )
        for column_name, value in checkpoint_values:
            if _column_exists(conn, "email_thread_memory", column_name):
                column_values.append((column_name, value))

        update_fragments: list[str] = []
        update_values: list[Any] = []
        insert_columns = ["agent_id", "conversation_id"]
        insert_values: list[Any] = [agent_id or 1, conversation_id]
        insert_value_sql = ["?", "?"]

        for column_name, value in column_values:
            if column_name in {"last_processed_at", "updated_at"}:
                update_fragments.append(f"{column_name} = SYSUTCDATETIME()")
                insert_columns.append(column_name)
                insert_value_sql.append("SYSUTCDATETIME()")
                continue

            update_fragments.append(f"{column_name} = ?")
            update_values.append(value)
            insert_columns.append(column_name)
            insert_values.append(value)
            insert_value_sql.append("?")

        insert_column_sql = ", ".join(insert_columns)
        update_sql = ", ".join(update_fragments)
        values_sql = ", ".join(insert_value_sql)

        cursor.execute(
            f"""
            MERGE dbo.email_thread_memory AS target
            USING (
                SELECT ? AS agent_id, ? AS conversation_id
            ) AS source
            ON target.agent_id = source.agent_id
               AND target.conversation_id = source.conversation_id
            WHEN MATCHED THEN
                UPDATE SET {update_sql}
            WHEN NOT MATCHED THEN
                INSERT ({insert_column_sql})
                VALUES ({values_sql});
            """,
            agent_id or 1,
            conversation_id,
            *update_values,
            *insert_values,
        )
        conn.commit()


def upsert_thread_memory(
    conn: pyodbc.Connection,
    agent_id: int | None = None,
    thread_state: Any | None = None,
    thread_summary: str | None = None,
    conversation_id: str | None = None,
    last_conversation_index: str | None = None,
    thread_status: str | None = None,
    latest_message_id: str | None = None,
    latest_reply_count: int | None = None,
) -> None:
    if thread_state is not None:
        conversation_id = thread_state.conversation_id
        latest_message_id = thread_state.latest_message_id
        last_conversation_index = thread_state.latest_conversation_index
        latest_reply_count = thread_state.latest_reply_count
        thread_status = thread_state.thread_status

    if not (conversation_id or "").strip():
        return

    cursor = conn.cursor()
    cursor.execute(
        """
        MERGE dbo.email_thread_memory AS target
        USING (
            SELECT
                ? AS agent_id,
                ? AS conversation_id,
                ? AS last_message_id,
                ? AS last_conversation_index,
                ? AS last_reply_count,
                ? AS last_thread_status,
                ? AS thread_summary
        ) AS source
        ON target.agent_id = source.agent_id
           AND target.conversation_id = source.conversation_id
        WHEN MATCHED THEN
            UPDATE SET
                last_message_id = COALESCE(source.last_message_id, target.last_message_id),
                last_conversation_index = CASE
                    WHEN source.thread_summary IS NULL
                         AND target.thread_summary IS NOT NULL
                    THEN target.last_conversation_index
                    ELSE COALESCE(
                        source.last_conversation_index,
                        target.last_conversation_index
                    )
                END,
                last_reply_count = COALESCE(source.last_reply_count, target.last_reply_count),
                last_thread_status = COALESCE(
                    source.last_thread_status,
                    target.last_thread_status
                ),
                last_processed_at = SYSUTCDATETIME(),
                thread_summary = COALESCE(source.thread_summary, target.thread_summary),
                updated_at = SYSUTCDATETIME()
        WHEN NOT MATCHED THEN
            INSERT (
                agent_id,
                conversation_id,
                last_message_id,
                last_conversation_index,
                last_reply_count,
                last_thread_status,
                last_processed_at,
                thread_summary,
                updated_at
            )
            VALUES (
                source.agent_id,
                source.conversation_id,
                source.last_message_id,
                source.last_conversation_index,
                source.last_reply_count,
                source.last_thread_status,
                SYSUTCDATETIME(),
                source.thread_summary,
                SYSUTCDATETIME()
            );
        """,
        agent_id or 1,
        conversation_id,
        latest_message_id,
        last_conversation_index,
        latest_reply_count,
        thread_status,
        thread_summary,
    )


def update_thread_memory_summary_checkpoint(
    conn: pyodbc.Connection,
    agent_id: int,
    conversation_id: str,
    thread_summary: str,
    last_processed_email_id: int,
    last_processed_received_at: datetime | None,
    thread_status: str,
    latest_message_id: str | None,
    expected_last_processed_email_id: int | None,
) -> bool:
    if not (conversation_id or "").strip() or not (thread_summary or "").strip():
        return False

    cursor = conn.cursor()
    cursor.execute(
        """
        SET NOCOUNT ON;

        DECLARE @updated BIT = 0;

        UPDATE dbo.email_thread_memory WITH (UPDLOCK, HOLDLOCK)
        SET
            last_message_id = COALESCE(?, last_message_id),
            last_processed_email_id = ?,
            last_processed_received_at = ?,
            last_thread_status = ?,
            last_processed_at = SYSUTCDATETIME(),
            thread_summary = ?,
            updated_at = SYSUTCDATETIME()
        WHERE conversation_id = ?
          AND agent_id = ?
          AND (
                last_processed_email_id = ?
                OR (
                    last_processed_email_id IS NULL
                    AND ? IS NULL
                )
          );

        IF @@ROWCOUNT = 1
        BEGIN
            SET @updated = 1;
        END;

        IF @updated = 0
           AND ? IS NULL
           AND NOT EXISTS (
                SELECT 1
                FROM dbo.email_thread_memory WITH (UPDLOCK, HOLDLOCK)
                WHERE conversation_id = ?
                  AND agent_id = ?
           )
        BEGIN
            INSERT INTO dbo.email_thread_memory (
                agent_id,
                conversation_id,
                last_message_id,
                last_processed_email_id,
                last_processed_received_at,
                last_thread_status,
                last_processed_at,
                thread_summary,
                updated_at
            )
            VALUES (
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                SYSUTCDATETIME(),
                ?,
                SYSUTCDATETIME()
            );

            SET @updated = 1;
        END;

        SELECT @updated AS updated;
        """,
        latest_message_id,
        last_processed_email_id,
        last_processed_received_at,
        thread_status,
        thread_summary,
        conversation_id,
        agent_id,
        expected_last_processed_email_id,
        expected_last_processed_email_id,
        expected_last_processed_email_id,
        conversation_id,
        agent_id,
        agent_id,
        conversation_id,
        latest_message_id,
        last_processed_email_id,
        last_processed_received_at,
        thread_status,
        thread_summary,
    )
    row = cursor.fetchone()
    return bool(row and row.updated)


def get_existing_thread_reply_count(
    conn: pyodbc.Connection,
    agent_id: int,
    conversation_id: str,
) -> int:
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT COUNT(1) AS reply_count
        FROM dbo.emails
        WHERE agent_id = ?
          AND conversation_id = ?
        """,
        agent_id,
        conversation_id,
    )
    return int(cursor.fetchone().reply_count)


def get_existing_graph_message_ids(
    conn: pyodbc.Connection,
    graph_message_ids: list[str],
) -> set[str]:
    if not graph_message_ids:
        return set()

    placeholders = ", ".join("?" for _ in graph_message_ids)
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT graph_message_id
        FROM dbo.emails
        WHERE graph_message_id IN ({placeholders})
        """,
        *graph_message_ids,
    )
    return {row.graph_message_id for row in cursor.fetchall()}


def is_duplicate_email(
    cursor,
    source_mailbox: str,
    graph_message_id: str | None,
    sender_email: str | None,
    subject: str | None,
    received_at: datetime | None,
) -> tuple[bool, str]:
    if not graph_message_id:
        return True, "MISSING_GRAPH_MESSAGE_ID"

    cursor.execute(
        """
        SELECT TOP 1 id
        FROM dbo.emails WITH (UPDLOCK, HOLDLOCK)
        WHERE graph_message_id = ?
        """,
        graph_message_id,
    )

    if cursor.fetchone():
        return True, "DUPLICATE_GRAPH_MESSAGE_ID"

    sender = (sender_email or "").lower().strip()

    if sender in NOISY_SYSTEM_SENDERS:
        cursor.execute(
            """
            SELECT TOP 1 email.id
            FROM dbo.emails AS email WITH (UPDLOCK, HOLDLOCK)
            INNER JOIN dbo.watch_emails AS watch_email
                ON watch_email.id = email.watch_email_id
            WHERE LOWER(LTRIM(RTRIM(watch_email.email))) = LOWER(LTRIM(RTRIM(?)))
              AND LOWER(LTRIM(RTRIM(email.sender_email))) = LOWER(LTRIM(RTRIM(?)))
              AND ISNULL(email.subject, N'') = ISNULL(?, N'')
              AND email.received_at = ?
            """,
            source_mailbox,
            sender_email,
            subject,
            received_at,
        )

        if cursor.fetchone():
            return True, "DUPLICATE_SYSTEM_FINGERPRINT"

    return False, "NEW_EMAIL"


def insert_email_if_new(
    conn: pyodbc.Connection,
    watch_email: WatchEmail,
    message: dict[str, Any],
) -> InsertResult:
    graph_message_id = message.get("id")
    sender = message.get("from", {}).get("emailAddress", {})
    sender_email = sender.get("address")
    sender_name = sender.get("name")
    subject = message.get("subject")
    received_at = _parse_graph_datetime(message.get("receivedDateTime"))
    raw_body = get_graph_message_body(message)
    latest_reply = extract_latest_reply(raw_body)
    cleaned_body = clean_email_body(latest_reply)
    issue_summary = extract_issue_summary(subject, cleaned_body)
    cursor = conn.cursor()

    logging.info(
        "EMAIL LENGTHS raw=%s cleaned=%s preview=%s",
        len(raw_body or ""),
        len(cleaned_body or ""),
        len(message.get("bodyPreview") or ""),
    )

    is_duplicate, duplicate_reason = is_duplicate_email(
        cursor,
        watch_email.email,
        graph_message_id,
        sender_email,
        subject,
        received_at,
    )

    if is_duplicate:
        return InsertResult(
            inserted=False,
            skipped_duplicate=True,
            duplicate_reason=duplicate_reason,
        )

    insert_columns = [
        "agent_id",
        "watch_email_id",
        "graph_message_id",
        "conversation_id",
        "sender_email",
        "sender_name",
        "subject",
        "received_at",
        "body_preview",
        "has_attachments",
        "processed_status",
    ]
    insert_values: list[Any] = [
        watch_email.agent_id,
        watch_email.watch_email_id,
        graph_message_id,
        message.get("conversationId"),
        sender_email,
        sender_name,
        subject,
        received_at,
        message.get("bodyPreview"),
        bool(message.get("hasAttachments", False)),
        "pending",
    ]

    optional_columns = (
        ("original_sender_name", sender_name),
        ("original_sender_email", sender_email),
        ("conversation_index", message.get("conversationIndex")),
        ("internet_message_id", message.get("internetMessageId")),
        ("cleaned_body", cleaned_body),
    )
    for column_name, value in optional_columns:
        if _column_exists(conn, "emails", column_name):
            insert_columns.append(column_name)
            insert_values.append(value)

    placeholders = ", ".join("?" for _ in insert_columns)
    columns = ", ".join(f"[{column}]" for column in insert_columns)

    cursor.execute(
        f"""
        SET NOCOUNT ON;

        INSERT INTO dbo.emails ({columns})
        VALUES ({placeholders})

        SELECT CAST(1 AS BIT) AS inserted;
        """,
        *insert_values,
    )

    inserted = bool(cursor.fetchone().inserted)
    return InsertResult(inserted=inserted, skipped_duplicate=not inserted)


def match_unrouted_emails(conn: pyodbc.Connection) -> RouteMatchResult:
    cursor = conn.cursor()
    cursor.execute(
        """
        SET NOCOUNT ON;

        DECLARE @matched_count INT = 0;
        DECLARE @no_match_count INT = 0;

        ;WITH route_candidates AS (
            SELECT
                email.id AS email_id,
                route.id AS matched_route_id,
                route.source AS routed_to_email,
                route.source AS support_mailbox,
                CAST(NULL AS NVARCHAR(255)) AS teams_from_email,
                CAST(NULL AS NVARCHAR(255)) AS teams_channel_name,
                1 AS route_priority
            FROM dbo.emails AS email
            CROSS APPLY (
                SELECT TOP (1)
                    route_rules.id,
                    route_rules.source
                FROM dbo.route_rules AS route_rules
                WHERE route_rules.agent_id = email.agent_id
                  AND route_rules.status = N'Active'
                  AND route_rules.rule_name IS NOT NULL
                  AND route_rules.source IS NOT NULL
                  AND LOWER(LTRIM(RTRIM(route_rules.rule_name))) =
                      LOWER(LTRIM(RTRIM(email.sender_email)))
                ORDER BY route_rules.id
            ) AS route
            WHERE (
                    email.route_status IS NULL
                    OR email.route_status IN (N'unrouted', N'no_match')
                  )
              AND email.agent_id IS NOT NULL
              AND email.sender_email IS NOT NULL
              AND (
                    email.forward_status IS NULL
                    OR email.forward_status <> N'forwarded'
                  )

            UNION ALL

            SELECT
                email.id AS email_id,
                CAST(NULL AS INT) AS matched_route_id,
                support_source.email AS routed_to_email,
                support_source.email AS support_mailbox,
                watch_email.email AS teams_from_email,
                COALESCE(
                    teams_route.teams_channel_name,
                    N'KT - '
                        + LTRIM(RTRIM(destination_email.organization))
                        + N' - '
                        + LTRIM(RTRIM(destination_email.product_name))
                ) AS teams_channel_name,
                2 AS route_priority
            FROM dbo.emails AS email
            INNER JOIN dbo.watch_emails AS watch_email
                ON watch_email.id = email.watch_email_id
            CROSS APPLY (
                SELECT TOP (1)
                    destination_email.organization,
                    destination_email.product_name
                FROM dbo.destination_emails AS destination_email
                WHERE destination_email.agent_id = email.agent_id
                  AND destination_email.status = N'Active'
                  AND destination_email.organization IS NOT NULL
                  AND LTRIM(RTRIM(destination_email.organization)) <> N''
                  AND destination_email.product_name IS NOT NULL
                  AND LTRIM(RTRIM(destination_email.product_name)) <> N''
                  AND LOWER(LTRIM(RTRIM(destination_email.email))) =
                      LOWER(LTRIM(RTRIM(email.sender_email)))
                ORDER BY destination_email.id
            ) AS destination_email
            CROSS APPLY (
                SELECT TOP (1)
                    source_email.id,
                    source_email.email
                FROM dbo.source_emails AS source_email
                WHERE source_email.agent_id = email.agent_id
                  AND source_email.status = N'Active'
                  AND source_email.email IS NOT NULL
                  AND LOWER(LTRIM(RTRIM(source_email.email))) <>
                      LOWER(LTRIM(RTRIM(watch_email.email)))
                ORDER BY source_email.id
            ) AS support_source
            OUTER APPLY (
                SELECT TOP (1)
                    teams_route_config.teams_channel_name
                FROM dbo.teams_route_config AS teams_route_config
                WHERE teams_route_config.routing_status = N'ACTIVE'
                  AND LOWER(LTRIM(RTRIM(teams_route_config.source_email))) =
                      LOWER(LTRIM(RTRIM(watch_email.email)))
                  AND LOWER(LTRIM(RTRIM(teams_route_config.teams_channel_name))) =
                      LOWER(LTRIM(RTRIM(
                          N'KT - '
                          + LTRIM(RTRIM(destination_email.organization))
                          + N' - '
                          + LTRIM(RTRIM(destination_email.product_name))
                      )))
                ORDER BY teams_route_config.id
            ) AS teams_route
            WHERE (
                    email.route_status IS NULL
                    OR email.route_status IN (N'unrouted', N'no_match')
                  )
              AND email.agent_id IS NOT NULL
              AND email.watch_email_id IS NOT NULL
              AND email.sender_email IS NOT NULL
              AND LOWER(LTRIM(RTRIM(email.sender_email))) NOT LIKE N'%@laserbm.net'
              AND (
                    email.forward_status IS NULL
                    OR email.forward_status <> N'forwarded'
                  )
        ),
        matched_routes AS (
            SELECT
                email_id,
                matched_route_id,
                routed_to_email,
                support_mailbox,
                teams_from_email,
                teams_channel_name
            FROM (
                SELECT
                    route_candidates.*,
                    ROW_NUMBER() OVER (
                        PARTITION BY route_candidates.email_id
                        ORDER BY route_candidates.route_priority, route_candidates.matched_route_id
                    ) AS route_rank
                FROM route_candidates
            ) AS ranked_routes
            WHERE route_rank = 1
        )
        UPDATE email
        SET
            matched_route_id = matched_routes.matched_route_id,
            routed_to_email = matched_routes.routed_to_email,
            support_mailbox = COALESCE(
                matched_routes.support_mailbox,
                email.support_mailbox
            ),
            teams_from_email = COALESCE(
                matched_routes.teams_from_email,
                email.teams_from_email
            ),
            teams_channel_name = COALESCE(
                matched_routes.teams_channel_name,
                email.teams_channel_name
            ),
            route_status = N'matched',
            routed_at = SYSUTCDATETIME(),
            forward_status = CASE
                WHEN email.forward_status IN (
                    N'validation_failed',
                    N'blocked_destination',
                    N'failed'
                ) THEN N'pending'
                ELSE email.forward_status
            END,
            forward_error = CASE
                WHEN email.forward_status IN (
                    N'validation_failed',
                    N'blocked_destination',
                    N'failed'
                ) THEN NULL
                ELSE email.forward_error
            END
        FROM dbo.emails AS email
        INNER JOIN matched_routes
            ON matched_routes.email_id = email.id;

        SET @matched_count = @@ROWCOUNT;

        UPDATE dbo.emails
        SET route_status = N'no_match'
        WHERE (route_status IS NULL OR route_status = N'unrouted')
          AND (
                forward_status IS NULL
                OR forward_status <> N'forwarded'
              );

        SET @no_match_count = @@ROWCOUNT;

        SELECT @matched_count AS matched, @no_match_count AS no_match;
        """
    )

    row = cursor.fetchone()
    return RouteMatchResult(matched=int(row.matched), no_match=int(row.no_match))


def get_pending_forward_emails(conn: pyodbc.Connection) -> list[PendingForwardEmail]:
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            email.id,
            email.graph_message_id,
            watch_email.email AS mailbox_email,
            email.routed_to_email,
            email.sender_email,
            email.subject
        FROM dbo.emails AS email
        LEFT JOIN dbo.watch_emails AS watch_email
            ON watch_email.id = email.watch_email_id
        WHERE email.route_status = N'matched'
          AND email.graph_message_id IS NOT NULL
          AND email.routed_to_email IS NOT NULL
          AND (
              email.forward_status IS NULL
              OR email.forward_status IN (
                  N'pending',
                  N'failed',
                  N'validation_failed',
                  N'blocked_destination'
              )
          )
        ORDER BY email.id
        """
    )

    return [
        PendingForwardEmail(
            id=int(row.id),
            graph_message_id=row.graph_message_id,
            mailbox_email=row.mailbox_email,
            routed_to_email=row.routed_to_email,
            sender_email=row.sender_email,
            subject=row.subject,
        )
        for row in cursor.fetchall()
    ]


def mark_email_ready_to_forward(conn: pyodbc.Connection, email_id: int) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE dbo.emails
        SET
            forward_status = N'ready_to_forward',
            forward_error = NULL
        WHERE id = ?
        """,
        email_id,
    )


def mark_email_forward_validation_failed(
    conn: pyodbc.Connection,
    email_id: int,
    error: str,
) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE dbo.emails
        SET
            forward_status = N'validation_failed',
            forward_error = ?
        WHERE id = ?
        """,
        error,
        email_id,
    )


def apply_destination_allowlist(conn: pyodbc.Connection) -> DestinationAllowlistResult:
    cursor = conn.cursor()
    cursor.execute(
        """
        SET NOCOUNT ON;

        DECLARE @approved_count INT = 0;
        DECLARE @blocked_count INT = 0;

        UPDATE email
        SET
            forward_status = N'approved_destination',
            forward_error = NULL
        FROM dbo.emails AS email
        WHERE email.route_status = N'matched'
          AND email.forward_status = N'ready_to_forward'
          AND email.sender_email IS NOT NULL
          AND LOWER(LTRIM(RTRIM(email.sender_email))) NOT LIKE N'%@laserbm.net'
          AND EXISTS (
              SELECT 1
              FROM dbo.destination_emails AS destination_email
              WHERE destination_email.agent_id = email.agent_id
                AND destination_email.status = N'Active'
                AND LOWER(LTRIM(RTRIM(destination_email.email))) =
                    LOWER(LTRIM(RTRIM(email.sender_email)))
          );

        SET @approved_count = @@ROWCOUNT;

        UPDATE email
        SET
            forward_status = N'blocked_destination',
            forward_error = N'Client email is not approved in Customer Emails configuration.'
        FROM dbo.emails AS email
        WHERE email.route_status = N'matched'
          AND email.forward_status = N'ready_to_forward'
          AND (
              email.sender_email IS NULL
              OR LOWER(LTRIM(RTRIM(email.sender_email))) NOT LIKE N'%@laserbm.net'
          )
          AND NOT EXISTS (
              SELECT 1
              FROM dbo.destination_emails AS destination_email
              WHERE destination_email.agent_id = email.agent_id
                AND destination_email.status = N'Active'
                AND LOWER(LTRIM(RTRIM(destination_email.email))) =
                    LOWER(LTRIM(RTRIM(email.sender_email)))
          );

        SET @blocked_count = @@ROWCOUNT;

        SELECT @approved_count AS approved, @blocked_count AS blocked;
        """
    )

    row = cursor.fetchone()
    return DestinationAllowlistResult(
        approved=int(row.approved),
        blocked=int(row.blocked),
    )


def get_approved_support_intake_emails(
    conn: pyodbc.Connection,
) -> list[ApprovedSupportIntakeEmail]:
    cursor = conn.cursor()
    cleaned_body_select = (
        "email.cleaned_body"
        if _column_exists(conn, "emails", "cleaned_body")
        else "CAST(NULL AS NVARCHAR(MAX))"
    )
    body_select = (
        "email.body"
        if _column_exists(conn, "emails", "body")
        else "CAST(NULL AS NVARCHAR(MAX))"
    )
    cursor.execute(
        f"""
        SELECT
            email.id,
            email.sender_email,
            email.subject,
            email.body_preview,
            {cleaned_body_select} AS cleaned_body,
            {body_select} AS body,
            email.received_at,
            watch_email.email AS mailbox_email,
            email.routed_to_email
        FROM dbo.emails AS email
        INNER JOIN dbo.watch_emails AS watch_email
            ON watch_email.id = email.watch_email_id
        WHERE email.forward_status = N'approved_destination'
          AND email.routed_to_email IS NOT NULL
          AND email.watch_email_id IS NOT NULL
          AND (
              email.sender_email IS NULL
              OR LOWER(LTRIM(RTRIM(email.sender_email))) NOT LIKE N'%@laserbm.net'
          )
        ORDER BY email.id
        """
    )

    return [
        ApprovedSupportIntakeEmail(
            id=int(row.id),
            sender_email=row.sender_email,
            subject=row.subject,
            body_preview=row.body_preview,
            cleaned_body=row.cleaned_body,
            body=row.body,
            received_at=row.received_at,
            mailbox_email=row.mailbox_email,
            routed_to_email=row.routed_to_email,
        )
        for row in cursor.fetchall()
    ]


def get_destination_approved_support_intake_emails(
    conn: pyodbc.Connection,
) -> DestinationApprovedSupportIntakeResult:
    cursor = conn.cursor()
    cursor.execute(
        """
        SET NOCOUNT ON;

        DECLARE @approved_count INT = 0;

        ;WITH eligible_support_intake AS (
            SELECT
                email.id AS email_id,
                source_email.email AS routed_to_email,
                source_email.email AS support_mailbox
            FROM dbo.emails AS email
            INNER JOIN dbo.watch_emails AS watch_email
                ON watch_email.id = email.watch_email_id
            CROSS APPLY (
                SELECT TOP (1)
                    source_email.email
                FROM dbo.source_emails AS source_email
                WHERE source_email.agent_id = email.agent_id
                  AND source_email.status = N'Active'
                  AND source_email.email IS NOT NULL
                  AND LOWER(LTRIM(RTRIM(source_email.email))) <>
                      LOWER(LTRIM(RTRIM(watch_email.email)))
                ORDER BY source_email.id
            ) AS source_email
            WHERE email.watch_email_id IS NOT NULL
              AND email.sender_email IS NOT NULL
              AND LOWER(LTRIM(RTRIM(email.sender_email))) NOT LIKE N'%@laserbm.net'
              AND (
                  email.forward_status IS NULL
                  OR email.forward_status IN (
                      N'pending',
                      N'ready_to_forward',
                      N'approved_destination',
                      N'failed'
                  )
              )
              AND EXISTS (
                  SELECT 1
                  FROM dbo.destination_emails AS destination_email
                  WHERE destination_email.agent_id = email.agent_id
                    AND destination_email.status = N'Active'
                    AND LOWER(LTRIM(RTRIM(destination_email.email))) =
                        LOWER(LTRIM(RTRIM(email.sender_email)))
              )
        )
        UPDATE email
        SET
            routed_to_email = eligible_support_intake.routed_to_email,
            support_mailbox = COALESCE(
                eligible_support_intake.support_mailbox,
                email.support_mailbox
            ),
            forward_status = N'approved_destination',
            forward_error = NULL
        FROM dbo.emails AS email
        INNER JOIN eligible_support_intake
            ON eligible_support_intake.email_id = email.id;

        SET @approved_count = @@ROWCOUNT;

        SELECT @approved_count AS approved;
        """
    )

    row = cursor.fetchone()
    return DestinationApprovedSupportIntakeResult(approved=int(row.approved))


def get_pending_internal_route_emails(
    conn: pyodbc.Connection,
) -> list[PendingInternalRouteEmail]:
    cursor = conn.cursor()
    body_select = (
        "COALESCE(cleaned_body, body_preview) AS body_preview"
        if _column_exists(conn, "emails", "cleaned_body")
        else "body_preview"
    )
    cursor.execute(
        f"""
        SELECT
            id,
            sender_email,
            subject,
            {body_select}
        FROM dbo.emails
        WHERE sender_email IS NOT NULL
          AND LOWER(LTRIM(RTRIM(sender_email))) LIKE N'%@laserbm.net'
          AND (
              internal_route_status IS NULL
              OR internal_route_status = N'pending'
          )
        ORDER BY id
        """
    )

    return [
        PendingInternalRouteEmail(
            id=int(row.id),
            sender_email=row.sender_email,
            subject=row.subject,
            body_preview=row.body_preview,
        )
        for row in cursor.fetchall()
    ]


def get_ready_internal_route_emails(
    conn: pyodbc.Connection,
) -> list[ReadyInternalRouteEmail]:
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            email.id,
            email.sender_email,
            email.subject,
            email.body_preview,
            email.received_at,
            watch_email.email AS mailbox_email,
            email.routed_to_email
        FROM dbo.emails AS email
        INNER JOIN dbo.watch_emails AS watch_email
            ON watch_email.id = email.watch_email_id
        WHERE email.route_status = N'matched'
          AND email.forward_status = N'ready_to_forward'
          AND email.routed_to_email IS NOT NULL
          AND email.watch_email_id IS NOT NULL
        ORDER BY email.id
        """
    )

    return [
        ReadyInternalRouteEmail(
            id=int(row.id),
            sender_email=row.sender_email,
            subject=row.subject,
            body_preview=row.body_preview,
            received_at=row.received_at,
            mailbox_email=row.mailbox_email,
            routed_to_email=row.routed_to_email,
        )
        for row in cursor.fetchall()
    ]


def mark_internal_team_route_ready(
    conn: pyodbc.Connection,
    email_id: int,
    classification: str,
    routed_to_email: str,
) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE dbo.emails
        SET
            internal_classification = ?,
            internal_route_status = N'ready_to_route',
            internal_routed_to_email = ?,
            internal_route_error = NULL
        WHERE id = ?
        """,
        classification,
        routed_to_email,
        email_id,
    )


def mark_internal_team_route_no_match(conn: pyodbc.Connection, email_id: int) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE dbo.emails
        SET
            internal_classification = NULL,
            internal_route_status = N'no_match',
            internal_routed_to_email = NULL,
            internal_route_error = N'No internal routing keyword matched subject or body preview.'
        WHERE id = ?
        """,
        email_id,
    )


def mark_internal_team_route_failed(
    conn: pyodbc.Connection,
    email_id: int,
    error: str,
) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE dbo.emails
        SET
            internal_route_status = N'failed',
            internal_route_error = ?
        WHERE id = ?
        """,
        error,
        email_id,
    )


def mark_internal_route_sent(conn: pyodbc.Connection, email_id: int) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE dbo.emails
        SET
            forward_status = N'forwarded',
            forward_error = NULL
        WHERE id = ?
        """,
        email_id,
    )


def mark_internal_route_failed(
    conn: pyodbc.Connection,
    email_id: int,
    error: str,
) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE dbo.emails
        SET
            forward_status = N'failed',
            forward_error = ?
        WHERE id = ?
        """,
        error,
        email_id,
    )


def get_pending_teams_notifications(
    conn: pyodbc.Connection,
) -> list[PendingTeamsNotificationEmail]:
    cursor = conn.cursor()
    cleaned_body_select = (
        "email.cleaned_body"
        if _column_exists(conn, "emails", "cleaned_body")
        else "CAST(NULL AS NVARCHAR(MAX))"
    )
    original_sender_email_select = (
        "email.original_sender_email"
        if _column_exists(conn, "emails", "original_sender_email")
        else "CAST(NULL AS NVARCHAR(255))"
    )
    original_sender_name_select = (
        "email.original_sender_name"
        if _column_exists(conn, "emails", "original_sender_name")
        else "CAST(NULL AS NVARCHAR(255))"
    )

    def column_select(column_name: str, sql_type: str) -> str:
        if _column_exists(conn, "emails", column_name):
            return f"email.[{column_name}]"

        return f"CAST(NULL AS {sql_type})"

    cursor.execute(
        f"""
        SELECT
            email.id,
            email.agent_id,
            email.graph_message_id,
            {column_select("conversation_id", "NVARCHAR(255)")} AS conversation_id,
            {column_select("conversation_index", "NVARCHAR(MAX)")} AS conversation_index,
            COALESCE(
                email.teams_from_email,
                email.routed_to_email,
                email.support_mailbox
            ) AS source_email,
            watch_email.email AS polled_mailbox,
            watch_email.email AS watch_mailbox,
            email.teams_from_email,
            COALESCE(
                email.teams_channel_name,
                CONCAT(
                    N'KT - ',
                    COALESCE(destination_email.organization, N'UNKNOWN'),
                    N' - ',
                    COALESCE(destination_email.product_name, N'UNKNOWN')
                )
            ) AS teams_channel_name,
            email.sender_email,
            COALESCE({original_sender_name_select}, email.sender_name) AS original_sender_name,
            COALESCE({original_sender_email_select}, email.sender_email) AS original_sender_email,
            email.support_mailbox,
            email.routed_to_email,
            email.subject,
            email.body_preview,
            {cleaned_body_select} AS cleaned_body,
            {column_select("has_attachments", "BIT")} AS has_attachments,
            email.received_at,
            COALESCE(destination_email.organization, N'UNKNOWN') AS destination_organization,
            COALESCE(destination_email.product_name, N'UNKNOWN') AS destination_product_name,
            {column_select("issue_summary", "NVARCHAR(MAX)")} AS issue_summary,
            {column_select("priority", "NVARCHAR(50)")} AS priority,
            {column_select("priority_score", "FLOAT")} AS priority_score,
            {column_select("priority_reason", "NVARCHAR(MAX)")} AS priority_reason,
            {column_select("priority_confidence", "FLOAT")} AS priority_confidence,
            {column_select("module", "NVARCHAR(255)")} AS module,
            {column_select("module_confidence", "FLOAT")} AS module_confidence,
            {column_select("domain", "NVARCHAR(255)")} AS domain,
            {column_select("intent", "NVARCHAR(255)")} AS intent,
            {column_select("assigned_team", "NVARCHAR(255)")} AS assigned_team,
            {column_select("assigned_team_confidence", "FLOAT")} AS assigned_team_confidence,
            {column_select("review_required", "BIT")} AS review_required,
            {column_select("routing", "NVARCHAR(255)")} AS routing,
            {column_select("communication_type", "NVARCHAR(100)")} AS communication_type,
            {column_select("teams_template", "NVARCHAR(100)")} AS teams_template,
            thread_memory.thread_summary AS thread_summary,
            thread_memory.last_thread_status AS current_status
        FROM dbo.emails AS email
        LEFT JOIN dbo.watch_emails AS watch_email
            ON watch_email.id = email.watch_email_id
        LEFT JOIN dbo.email_thread_memory AS thread_memory
            ON thread_memory.agent_id = email.agent_id
           AND thread_memory.conversation_id = email.conversation_id
        OUTER APPLY (
            SELECT TOP (1)
                destination_email.organization,
                destination_email.product_name
            FROM dbo.destination_emails AS destination_email
            WHERE destination_email.agent_id = email.agent_id
              AND destination_email.status = N'Active'
              AND LOWER(LTRIM(RTRIM(destination_email.email))) =
                  LOWER(LTRIM(RTRIM(COALESCE({original_sender_email_select}, email.sender_email))))
            ORDER BY destination_email.id
        ) AS destination_email
        WHERE email.sender_email IS NOT NULL
          AND email.agent_id IS NOT NULL
          AND COALESCE(destination_email.organization, N'UNKNOWN') <> N'UNKNOWN'
          AND COALESCE(destination_email.product_name, N'UNKNOWN') <> N'UNKNOWN'
          AND (
              email.teams_status IS NULL
              OR email.teams_status = N'pending'
              OR email.teams_status = N'PENDING_ROUTE_CONFIG'
              OR email.teams_status = N'channel_missing'
          )
        ORDER BY email.id DESC
        """
    )

    return [
        PendingTeamsNotificationEmail(
            id=int(row.id),
            agent_id=int(row.agent_id),
            graph_message_id=row.graph_message_id,
            conversation_id=row.conversation_id,
            conversation_index=row.conversation_index,
            source_email=row.source_email,
            polled_mailbox=row.polled_mailbox,
            watch_mailbox=row.watch_mailbox,
            teams_from_email=row.teams_from_email,
            teams_channel_name=row.teams_channel_name,
            sender_email=row.sender_email,
            original_sender_name=row.original_sender_name,
            original_sender_email=row.original_sender_email,
            support_mailbox=row.support_mailbox,
            routed_to_email=row.routed_to_email,
            subject=row.subject,
            body_preview=row.body_preview,
            cleaned_body=row.cleaned_body,
            has_attachments=(
                bool(row.has_attachments)
                if row.has_attachments is not None
                else None
            ),
            received_at=row.received_at,
            destination_organization=row.destination_organization,
            destination_product_name=row.destination_product_name,
            issue_summary=row.issue_summary,
            priority=row.priority,
            priority_score=row.priority_score,
            priority_reason=row.priority_reason,
            priority_confidence=row.priority_confidence,
            module=row.module,
            module_confidence=row.module_confidence,
            domain=row.domain,
            intent=row.intent,
            assigned_team=row.assigned_team,
            assigned_team_confidence=row.assigned_team_confidence,
            review_required=(
                bool(row.review_required)
                if row.review_required is not None
                else None
            ),
            routing=row.routing,
            thread_summary=row.thread_summary,
            current_status=row.current_status,
            communication_type=row.communication_type,
            teams_template=row.teams_template,
        )
        for row in cursor.fetchall()
    ]


def get_active_teams_channel(
    conn: pyodbc.Connection,
    agent_id: int,
    channel_name: str,
) -> TeamsChannel | None:
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT TOP (1)
            channel_name,
            webhook_url
        FROM dbo.teams_channels
        WHERE agent_id = ?
          AND status = N'Active'
          AND LOWER(LTRIM(RTRIM(channel_name))) = LOWER(LTRIM(RTRIM(?)))
        ORDER BY id
        """,
        agent_id,
        channel_name,
    )
    row = cursor.fetchone()
    if not row:
        return None

    return TeamsChannel(
        channel_name=row.channel_name,
        webhook_url=row.webhook_url,
    )


def save_teams_intelligence(
    conn: pyodbc.Connection,
    email_id: int,
    intelligence: EmailIntelligence,
) -> None:
    column_values = (
        ("extracted_client_name", intelligence.client_name),
        ("extracted_product_name", intelligence.product_name),
        ("ai_priority", intelligence.priority),
        ("ai_assigned_team", intelligence.assigned_team),
        ("ai_summary", intelligence.issue_summary),
        ("ai_action_required", intelligence.action_required),
        ("issue_summary", intelligence.issue_summary),
        ("priority", intelligence.priority),
        ("priority_score", intelligence.priority_score),
        ("priority_reason", intelligence.priority_reason),
        ("priority_confidence", intelligence.priority_confidence),
        ("module", intelligence.module),
        ("module_confidence", intelligence.module_confidence),
        ("domain", intelligence.domain),
        ("intent", intelligence.intent),
        ("assigned_team", intelligence.assigned_team),
        ("assigned_team_confidence", intelligence.assigned_team_confidence),
        ("review_required", bool(intelligence.review_required)),
        ("routing", intelligence.routing),
        ("communication_type", intelligence.communication_type),
        ("teams_template", intelligence.teams_template),
    )

    set_fragments = []
    values: list[Any] = []
    for column_name, value in column_values:
        if _column_exists(conn, "emails", column_name):
            set_fragments.append(f"[{column_name}] = ?")
            values.append(value)

    if not set_fragments:
        return

    cursor = conn.cursor()
    cursor.execute(
        f"""
        UPDATE dbo.emails
        SET {", ".join(set_fragments)}
        WHERE id = ?
        """,
        *values,
        email_id,
    )

def mark_teams_sent(
    conn: pyodbc.Connection,
    email_id: int,
    channel_name: str,
) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE dbo.emails
        SET
            teams_status = N'sent',
            teams_channel_name = ?,
            teams_sent_at = SYSUTCDATETIME(),
            teams_error = NULL
        WHERE id = ?
        """,
        channel_name,
        email_id,
    )


def mark_teams_failed(
    conn: pyodbc.Connection,
    email_id: int,
    error: str,
) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE dbo.emails
        SET
            teams_status = N'failed',
            teams_error = ?
        WHERE id = ?
        """,
        error,
        email_id,
    )


def mark_teams_channel_missing(
    conn: pyodbc.Connection,
    email_id: int,
    channel_name: str,
) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE dbo.emails
        SET
            teams_status = N'channel_missing',
            teams_channel_name = ?,
            teams_error = N'No active Teams webhook configured for channel.'
        WHERE id = ?
        """,
        channel_name,
        email_id,
    )


def mark_teams_route_pending(
    conn: pyodbc.Connection,
    email_id: int,
    channel_name: str | None = None,
) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE dbo.emails
        SET
            teams_status = N'PENDING_ROUTE_CONFIG',
            teams_channel_name = COALESCE(?, teams_channel_name),
            teams_error = N'Teams route configuration is pending.'
        WHERE id = ?
        """,
        channel_name,
        email_id,
    )


def mark_support_intake_sent(conn: pyodbc.Connection, email_id: int) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE dbo.emails
        SET
            forward_status = N'forwarded',
            forward_error = NULL
        WHERE id = ?
        """,
        email_id,
    )


def mark_support_intake_failed(
    conn: pyodbc.Connection,
    email_id: int,
    error: str,
) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE dbo.emails
        SET
            forward_status = N'failed',
            forward_error = ?
        WHERE id = ?
        """,
        error,
        email_id,
    )


def prepare_acknowledgement_queue(conn: pyodbc.Connection) -> AcknowledgementQueueResult:
    cursor = conn.cursor()
    cursor.execute(
        """
        SET NOCOUNT ON;

        DECLARE @ready_count INT = 0;
        DECLARE @source_missing_count INT = 0;
        DECLARE @destination_invalid_count INT = 0;
        DECLARE @skipped_duplicate_count INT = 0;

        UPDATE email
        SET
            acknowledgement_status = N'destination_invalid',
            acknowledgement_source_email = NULL,
            acknowledgement_destination_email = email.sender_email,
            acknowledgement_error = N'Client email is not approved in Customer Emails configuration.',
            acknowledgement_prepared_at = NULL
        FROM dbo.emails AS email
        WHERE (
              email.acknowledgement_status IS NULL
              OR email.acknowledgement_status IN (
                  N'pending',
                  N'failed'
              )
          )
          AND (
              email.sender_email IS NULL
              OR NOT EXISTS (
                  SELECT 1
                  FROM dbo.destination_emails AS destination_email
                  WHERE destination_email.agent_id = email.agent_id
                    AND LOWER(LTRIM(RTRIM(destination_email.status))) = N'active'
                    AND LOWER(LTRIM(RTRIM(destination_email.email))) =
                        LOWER(LTRIM(RTRIM(email.sender_email)))
              )
          );

        SET @destination_invalid_count = @@ROWCOUNT;

        UPDATE email
        SET
            acknowledgement_status = N'source_missing',
            acknowledgement_source_email = NULL,
            acknowledgement_destination_email = email.sender_email,
            acknowledgement_error = N'No active Source Email is configured for this agent.',
            acknowledgement_prepared_at = NULL
        FROM dbo.emails AS email
        WHERE (
              email.acknowledgement_status IS NULL
              OR email.acknowledgement_status IN (
                  N'pending',
                  N'failed'
              )
          )
          AND EXISTS (
              SELECT 1
              FROM dbo.destination_emails AS destination_email
              WHERE destination_email.agent_id = email.agent_id
                AND LOWER(LTRIM(RTRIM(destination_email.status))) = N'active'
                AND LOWER(LTRIM(RTRIM(destination_email.email))) =
                    LOWER(LTRIM(RTRIM(email.sender_email)))
          )
          AND NOT EXISTS (
              SELECT 1
              FROM dbo.source_emails AS source_email
              WHERE source_email.agent_id = email.agent_id
                AND LOWER(LTRIM(RTRIM(source_email.status))) = N'active'
                AND source_email.email IS NOT NULL
          );

        SET @source_missing_count = @@ROWCOUNT;

        ;WITH duplicate_threads AS (
            SELECT
                email.id AS email_id,
                source_email.email AS acknowledgement_source_email
            FROM dbo.emails AS email
            CROSS APPLY (
                SELECT TOP (1)
                    source_email.email
                FROM dbo.source_emails AS source_email
                WHERE source_email.agent_id = email.agent_id
                  AND LOWER(LTRIM(RTRIM(source_email.status))) = N'active'
                  AND source_email.email IS NOT NULL
                ORDER BY source_email.id
            ) AS source_email
            WHERE (
                  email.acknowledgement_status IS NULL
                  OR email.acknowledgement_status IN (
                      N'pending',
                      N'failed'
                  )
              )
              AND email.sender_email IS NOT NULL
              AND email.conversation_id IS NOT NULL
              AND EXISTS (
                  SELECT 1
                  FROM dbo.destination_emails AS destination_email
                  WHERE destination_email.agent_id = email.agent_id
                    AND LOWER(LTRIM(RTRIM(destination_email.status))) = N'active'
                    AND LOWER(LTRIM(RTRIM(destination_email.email))) =
                        LOWER(LTRIM(RTRIM(email.sender_email)))
              )
              AND EXISTS (
                  SELECT 1
                  FROM dbo.emails AS sent_email
                  WHERE sent_email.agent_id = email.agent_id
                    AND sent_email.id <> email.id
                    AND sent_email.conversation_id = email.conversation_id
                    AND sent_email.acknowledgement_status = N'sent'
              )
        )
        UPDATE email
        SET
            acknowledgement_status = N'skipped_duplicate_thread',
            acknowledgement_source_email = duplicate_threads.acknowledgement_source_email,
            acknowledgement_destination_email = email.sender_email,
            acknowledgement_error = NULL,
            acknowledgement_prepared_at = SYSUTCDATETIME()
        FROM dbo.emails AS email
        INNER JOIN duplicate_threads
            ON duplicate_threads.email_id = email.id;

        SET @skipped_duplicate_count = @@ROWCOUNT;

        ;WITH acknowledgement_candidates AS (
            SELECT
                email.id AS email_id,
                source_email.email AS acknowledgement_source_email
            FROM dbo.emails AS email
            CROSS APPLY (
                SELECT TOP (1)
                    source_email.email
                FROM dbo.source_emails AS source_email
                WHERE source_email.agent_id = email.agent_id
                  AND LOWER(LTRIM(RTRIM(source_email.status))) = N'active'
                  AND source_email.email IS NOT NULL
                ORDER BY source_email.id
            ) AS source_email
            WHERE (
                  email.acknowledgement_status IS NULL
                  OR email.acknowledgement_status IN (
                      N'pending',
                      N'failed'
                  )
              )
              AND email.sender_email IS NOT NULL
              AND EXISTS (
                  SELECT 1
                  FROM dbo.destination_emails AS destination_email
                  WHERE destination_email.agent_id = email.agent_id
                    AND LOWER(LTRIM(RTRIM(destination_email.status))) = N'active'
                    AND LOWER(LTRIM(RTRIM(destination_email.email))) =
                        LOWER(LTRIM(RTRIM(email.sender_email)))
              )
              AND (
                  email.conversation_id IS NULL
                  OR NOT EXISTS (
                      SELECT 1
                      FROM dbo.emails AS sent_email
                      WHERE sent_email.agent_id = email.agent_id
                        AND sent_email.id <> email.id
                        AND sent_email.conversation_id = email.conversation_id
                        AND sent_email.acknowledgement_status = N'sent'
                  )
              )
        )
        UPDATE email
        SET
            acknowledgement_status = N'pending',
            acknowledgement_source_email = acknowledgement_candidates.acknowledgement_source_email,
            acknowledgement_destination_email = email.sender_email,
            acknowledgement_error = NULL,
            acknowledgement_prepared_at = SYSUTCDATETIME()
        FROM dbo.emails AS email
        INNER JOIN acknowledgement_candidates
            ON acknowledgement_candidates.email_id = email.id;

        SET @ready_count = @@ROWCOUNT;

        SELECT
            @ready_count AS ready,
            @source_missing_count AS source_missing,
            @destination_invalid_count AS destination_invalid,
            @skipped_duplicate_count AS skipped_duplicate;
        """
    )

    row = cursor.fetchone()
    return AcknowledgementQueueResult(
        ready=int(row.ready),
        source_missing=int(row.source_missing),
        destination_invalid=int(row.destination_invalid),
        skipped_duplicate=int(row.skipped_duplicate),
    )


def get_pending_acknowledgement_emails(
    conn: pyodbc.Connection,
) -> list[PendingAcknowledgementEmail]:
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            id,
            conversation_id,
            subject,
            sender_email,
            acknowledgement_source_email,
            acknowledgement_destination_email
        FROM dbo.emails
        WHERE acknowledgement_status = N'pending'
          AND sender_email IS NOT NULL
          AND acknowledgement_source_email IS NOT NULL
          AND acknowledgement_destination_email IS NOT NULL
        ORDER BY id
        """
    )

    return [
        PendingAcknowledgementEmail(
            id=int(row.id),
            conversation_id=row.conversation_id,
            subject=row.subject,
            sender_email=row.sender_email,
            acknowledgement_source_email=row.acknowledgement_source_email,
            acknowledgement_destination_email=row.acknowledgement_destination_email,
        )
        for row in cursor.fetchall()
    ]


def save_acknowledgement_draft(
    conn: pyodbc.Connection,
    email_id: int,
    subject: str,
    body: str,
) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE dbo.emails
        SET
            acknowledgement_subject = ?,
            acknowledgement_body = ?,
            acknowledgement_generated_at = SYSUTCDATETIME()
        WHERE id = ?
        """,
        subject,
        body,
        email_id,
    )


def has_sent_acknowledgement_for_thread(
    conn: pyodbc.Connection,
    email_id: int,
) -> bool:
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT CASE WHEN EXISTS (
            SELECT 1
            FROM dbo.emails AS email
            INNER JOIN dbo.emails AS sent_email
                ON sent_email.agent_id = email.agent_id
               AND sent_email.id <> email.id
               AND sent_email.conversation_id = email.conversation_id
               AND sent_email.acknowledgement_status = N'sent'
            WHERE email.id = ?
              AND email.conversation_id IS NOT NULL
        ) THEN 1 ELSE 0 END AS has_sent_acknowledgement
        """,
        email_id,
    )
    return bool(cursor.fetchone().has_sent_acknowledgement)


def mark_acknowledgement_sent(conn: pyodbc.Connection, email_id: int) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE dbo.emails
        SET
            acknowledgement_status = N'sent',
            acknowledgement_error = NULL,
            acknowledgement_sent_at = SYSUTCDATETIME()
        WHERE id = ?
        """,
        email_id,
    )


def mark_acknowledgement_failed(
    conn: pyodbc.Connection,
    email_id: int,
    error: str,
) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE dbo.emails
        SET
            acknowledgement_status = N'failed',
            acknowledgement_error = ?
        WHERE id = ?
        """,
        error,
        email_id,
    )


def mark_acknowledgement_skipped_duplicate_thread(
    conn: pyodbc.Connection,
    email_id: int,
) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE dbo.emails
        SET
            acknowledgement_status = N'skipped_duplicate_thread',
            acknowledgement_error = NULL
        WHERE id = ?
        """,
        email_id,
    )


def find_teams_route(cursor, source_email, organization_name=None, product_name=None):
    """
    Routing priority:
    1. source_email + organization_name + product_name
    2. source_email + exact KT channel name
    3. source_email + organization_name
    4. source_email + product_name
    5. no route found
    """
    channel_name = (
        f"KT - {(organization_name or '').strip()} - {(product_name or '').strip()}"
    )

    query = """
    SELECT TOP 1
        id,
        source_email,
        organization_name,
        product_name,
        teams_channel_name,
        webhook_url,
        routing_status
    FROM dbo.teams_route_config
    WHERE LOWER(LTRIM(RTRIM(source_email))) = LOWER(LTRIM(RTRIM(?)))
      AND routing_status = N'ACTIVE'
      AND (
            (organization_name = ? AND product_name = ?)
         OR LOWER(LTRIM(RTRIM(teams_channel_name))) = LOWER(LTRIM(RTRIM(?)))
         OR (organization_name = ? AND product_name IS NULL)
         OR (organization_name IS NULL AND product_name = ?)
      )
    ORDER BY
        CASE
            WHEN organization_name = ? AND product_name = ? THEN 1
            WHEN LOWER(LTRIM(RTRIM(teams_channel_name))) = LOWER(LTRIM(RTRIM(?))) THEN 2
            WHEN organization_name = ? AND product_name IS NULL THEN 3
            WHEN organization_name IS NULL AND product_name = ? THEN 4
            ELSE 5
        END;
    """

    cursor.execute(
        query,
        source_email,
        organization_name,
        product_name,
        channel_name,
        organization_name,
        product_name,
        organization_name,
        product_name,
        channel_name,
        organization_name,
        product_name,
    )

    row = cursor.fetchone()

    if not row:
        return None

    return {
        "route_id": row[0],
        "source_email": row[1],
        "organization_name": row[2],
        "product_name": row[3],
        "teams_channel_name": row[4],
        "webhook_url": row[5],
        "routing_status": row[6],
    }


def find_teams_route_for_support_mailbox(
    cursor,
    source_email_id,
    organization,
    product_name,
):
    channel_name = f"KT - {(organization or '').strip()} - {(product_name or '').strip()}"
    if not source_email_id or not organization or not product_name:
        return None

    cursor.execute(
        """
        SELECT TOP (1)
            teams_route_config.id,
            teams_route_config.source_email,
            teams_route_config.teams_channel_name,
            teams_route_config.routing_status,
            teams_route_config.webhook_url
        FROM dbo.teams_route_config AS teams_route_config
        INNER JOIN dbo.source_emails AS source_email
            ON LOWER(LTRIM(RTRIM(source_email.email))) =
               LOWER(LTRIM(RTRIM(teams_route_config.source_email)))
        WHERE source_email.id = ?
          AND teams_route_config.routing_status = N'ACTIVE'
          AND LOWER(LTRIM(RTRIM(teams_route_config.teams_channel_name))) =
              LOWER(LTRIM(RTRIM(?)))
        ORDER BY teams_route_config.id
        """,
        source_email_id,
        channel_name,
    )

    row = cursor.fetchone()
    if not row:
        return None

    return {
        "route_id": row[0],
        "from_email": row[1],
        "teams_channel": row[2],
        "status": row[3],
        "webhook_url": row[4],
    }
