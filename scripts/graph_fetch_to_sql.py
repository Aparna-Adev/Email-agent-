import os
import requests
import pyodbc
from dotenv import load_dotenv

load_dotenv()

TENANT_ID = os.getenv("TENANT_ID")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
MAILBOX_USER = os.getenv("MAILBOX_USER")

DB_SERVER = os.getenv("DB_SERVER")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

# -----------------------------
# GET GRAPH ACCESS TOKEN
# -----------------------------

token_url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"

token_data = {
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "scope": "https://graph.microsoft.com/.default",
    "grant_type": "client_credentials",
}

token_response = requests.post(token_url, data=token_data)

if token_response.status_code != 200:
    print("TOKEN ERROR")
    print(token_response.text)
    raise SystemExit()

access_token = token_response.json()["access_token"]

print("TOKEN SUCCESS")

# -----------------------------
# FETCH EMAILS
# -----------------------------

headers = {
    "Authorization": f"Bearer {access_token}",
    "Accept": "application/json",
}

graph_url = (
    f"https://graph.microsoft.com/v1.0/users/{MAILBOX_USER}/mailFolders/inbox/messages"
    "?$top=10"
    "&$select=id,conversationId,subject,from,receivedDateTime,bodyPreview,hasAttachments"
)

mail_response = requests.get(graph_url, headers=headers)

if mail_response.status_code != 200:
    print("MAIL FETCH ERROR")
    print(mail_response.text)
    raise SystemExit()

emails = mail_response.json().get("value", [])

print(f"FETCHED EMAILS: {len(emails)}")

# -----------------------------
# SQL CONNECTION
# -----------------------------

connection_string = (
    f"DRIVER={{ODBC Driver 17 for SQL Server}};"
    f"SERVER={DB_SERVER};"
    f"DATABASE={DB_NAME};"
    f"UID={DB_USER};"
    f"PWD={DB_PASSWORD};"
    f"TrustServerCertificate=yes;"
)

conn = pyodbc.connect(connection_string)
cursor = conn.cursor()

print("SQL CONNECTED")

# -----------------------------
# INSERT EMAILS
# -----------------------------

inserted_count = 0
skipped_count = 0

for email in emails:

    graph_message_id = email.get("id")
    conversation_id = email.get("conversationId")

    sender = email.get("from", {}).get("emailAddress", {})

    sender_email = sender.get("address")
    sender_name = sender.get("name")

    subject = email.get("subject")
    received_at = email.get("receivedDateTime")
    body_preview = email.get("bodyPreview")
    has_attachments = email.get("hasAttachments", False)

    # DUPLICATE CHECK

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM emails
        WHERE graph_message_id = ?
        """,
        graph_message_id,
    )

    exists = cursor.fetchone()[0]

    if exists:
        skipped_count += 1
        print(f"SKIPPED: {subject}")
        continue

    # INSERT

    cursor.execute(
        """
        INSERT INTO emails (
            graph_message_id,
            conversation_id,
            sender_email,
            sender_name,
            subject,
            received_at,
            body_preview,
            has_attachments
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        graph_message_id,
        conversation_id,
        sender_email,
        sender_name,
        subject,
        received_at,
        body_preview,
        has_attachments,
    )

    inserted_count += 1

    print(f"INSERTED: {subject}")

conn.commit()

print("===================================")
print(f"INSERTED COUNT: {inserted_count}")
print(f"SKIPPED COUNT: {skipped_count}")
print("DONE")