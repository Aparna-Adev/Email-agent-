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


def build_thread_content():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT conversation_id
        FROM email_threads
        WHERE thread_content IS NULL
        """
    )

    threads = cursor.fetchall()
    print(f"THREADS TO BUILD: {len(threads)}")

    updated = 0

    for thread in threads:
        conversation_id = thread.conversation_id

        cursor.execute(
            """
            SELECT sender_email, subject, received_at, cleaned_body
            FROM emails
            WHERE conversation_id = ?
            ORDER BY received_at ASC
            """,
            conversation_id,
        )

        emails = cursor.fetchall()

        parts = []

        for email in emails:
            part = f"""
From: {email.sender_email}
Received: {email.received_at}
Subject: {email.subject}

{email.cleaned_body or ""}
"""
            parts.append(part.strip())

        thread_content = "\n\n---\n\n".join(parts)

        cursor.execute(
            """
            UPDATE email_threads
            SET thread_content = ?
            WHERE conversation_id = ?
            """,
            thread_content,
            conversation_id,
        )

        updated += 1
        print(f"BUILT THREAD: {conversation_id}")

    conn.commit()
    cursor.close()
    conn.close()

    print("===================================")
    print(f"UPDATED THREADS: {updated}")
    print("DONE")


if __name__ == "__main__":
    build_thread_content()