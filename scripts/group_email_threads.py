import os
import pyodbc
from dotenv import load_dotenv

load_dotenv()

DB_SERVER = os.getenv("DB_SERVER")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")


def get_connection():
    return pyodbc.connect(
        "DRIVER={ODBC Driver 17 for SQL Server};"
        f"SERVER={DB_SERVER};"
        f"DATABASE={DB_NAME};"
        f"UID={DB_USER};"
        f"PWD={DB_PASSWORD};"
        "TrustServerCertificate=yes;"
    )


def group_email_threads():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            conversation_id,
            MAX(subject) AS thread_subject,
            COUNT(*) AS email_count,
            MAX(received_at) AS latest_received_at
        FROM emails
        WHERE conversation_id IS NOT NULL
        GROUP BY conversation_id
        """
    )

    rows = cursor.fetchall()
    print(f"THREADS FOUND: {len(rows)}")

    inserted = 0
    updated = 0

    for row in rows:
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM email_threads
            WHERE conversation_id = ?
            """,
            row.conversation_id,
        )

        exists = cursor.fetchone()[0]

        if exists:
            cursor.execute(
                """
                UPDATE email_threads
                SET thread_subject = ?,
                    email_count = ?,
                    latest_received_at = ?
                WHERE conversation_id = ?
                """,
                row.thread_subject,
                row.email_count,
                row.latest_received_at,
                row.conversation_id,
            )
            updated += 1
        else:
            cursor.execute(
                """
                INSERT INTO email_threads (
                    conversation_id,
                    thread_subject,
                    email_count,
                    latest_received_at,
                    thread_status
                )
                VALUES (?, ?, ?, ?, 'pending')
                """,
                row.conversation_id,
                row.thread_subject,
                row.email_count,
                row.latest_received_at,
            )
            inserted += 1

    conn.commit()
    cursor.close()
    conn.close()

    print("===================================")
    print(f"INSERTED THREADS: {inserted}")
    print(f"UPDATED THREADS: {updated}")
    print("DONE")


if __name__ == "__main__":
    group_email_threads()