import os
import sys
import pyodbc
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

from imported_old_logic.preprocessing.clean_body import clean_business_body

load_dotenv()

DB_SERVER = os.getenv("DB_SERVER")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")


def get_connection():
    connection_string = (
        "DRIVER={ODBC Driver 17 for SQL Server};"
        f"SERVER={DB_SERVER};"
        f"DATABASE={DB_NAME};"
        f"UID={DB_USER};"
        f"PWD={DB_PASSWORD};"
        "TrustServerCertificate=yes;"
    )
    return pyodbc.connect(connection_string)


def process_pending_emails():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, body_preview
        FROM emails
        WHERE processed_status = 'pending'
        """
    )

    rows = cursor.fetchall()

    print(f"PENDING EMAILS FOUND: {len(rows)}")

    updated_count = 0

    for row in rows:
        email_id = row.id
        raw_body = row.body_preview or ""

        cleaned_body = clean_business_body(raw_body)

        cursor.execute(
            """
            UPDATE emails
            SET cleaned_body = ?,
                processed_status = 'cleaned'
            WHERE id = ?
            """,
            cleaned_body,
            email_id,
        )

        updated_count += 1
        print(f"CLEANED EMAIL ID: {email_id}")

    conn.commit()
    cursor.close()
    conn.close()

    print("===================================")
    print(f"UPDATED COUNT: {updated_count}")
    print("DONE")


if __name__ == "__main__":
    process_pending_emails()