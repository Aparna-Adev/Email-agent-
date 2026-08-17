import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import msal
import requests

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]
MESSAGE_SELECT_FIELDS = (
    "id,conversationId,conversationIndex,internetMessageId,subject,from,"
    "receivedDateTime,bodyPreview,body,uniqueBody,hasAttachments"
)


@dataclass(frozen=True)
class GraphFetchResult:
    messages: list[dict[str, Any]]
    delta_link: str | None


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def get_access_token() -> str:
    tenant_id = _required_env("TENANT_ID")
    client_id = _required_env("CLIENT_ID")
    client_secret = _required_env("CLIENT_SECRET")

    app = msal.ConfidentialClientApplication(
        client_id=client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        client_credential=client_secret,
    )
    token_result = app.acquire_token_for_client(scopes=GRAPH_SCOPE)

    access_token = token_result.get("access_token")
    if not access_token:
        error_description = token_result.get("error_description") or token_result
        raise RuntimeError(f"Failed to acquire Microsoft Graph token: {error_description}")

    return access_token


def fetch_inbox_messages(
    mailbox_email: str | None = None,
    delta_link: str | None = None,
    top: int = 25,
) -> GraphFetchResult:
    mailbox = (mailbox_email or os.getenv("MAILBOX_USER", "")).strip()
    if not mailbox:
        raise RuntimeError("Mailbox email is required. MAILBOX_USER is fallback only.")

    top = max(1, min(top, 100))
    access_token = get_access_token()
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    encoded_mailbox = quote(mailbox, safe="")
    url = delta_link or (
        f"{GRAPH_BASE_URL}/users/{encoded_mailbox}/mailFolders/inbox/messages/delta"
        f"?$top={top}"
        f"&$select={MESSAGE_SELECT_FIELDS}"
    )

    messages: list[dict[str, Any]] = []
    latest_delta_link: str | None = delta_link
    while url:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code >= 400:
            raise RuntimeError(
                f"Graph inbox fetch failed for {mailbox}: "
                f"{response.status_code} {response.text}"
            )

        payload = response.json()
        messages.extend(payload.get("value", []))
        latest_delta_link = payload.get("@odata.deltaLink") or latest_delta_link
        url = payload.get("@odata.nextLink")

    return GraphFetchResult(messages=messages, delta_link=latest_delta_link)


def send_support_intake_email(
    source_mailbox: str,
    target_mailbox: str,
    subject: str,
    body: str,
) -> None:
    source_mailbox = (source_mailbox or "").strip()
    target_mailbox = (target_mailbox or "").strip()
    if not source_mailbox:
        raise RuntimeError("Source mailbox is required for Graph sendMail.")
    if not target_mailbox:
        raise RuntimeError("Target mailbox is required for Graph sendMail.")

    access_token = get_access_token()
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    encoded_mailbox = quote(source_mailbox, safe="")
    url = f"{GRAPH_BASE_URL}/users/{encoded_mailbox}/sendMail"
    payload = {
        "message": {
            "subject": subject,
            "body": {
                "contentType": "Text",
                "content": body,
            },
            "toRecipients": [
                {
                    "emailAddress": {
                        "address": target_mailbox,
                    }
                }
            ],
        },
        "saveToSentItems": True,
    }

    response = requests.post(url, headers=headers, json=payload, timeout=30)
    if response.status_code >= 400:
        raise RuntimeError(
            f"Graph support intake send failed from {source_mailbox} "
            f"to {target_mailbox}: {response.status_code} {response.text}"
        )


def send_acknowledgement_email(
    source_mailbox: str,
    target_mailbox: str,
    subject: str,
    body: str,
) -> None:
    source_mailbox = (source_mailbox or "").strip()
    target_mailbox = (target_mailbox or "").strip()
    if not source_mailbox:
        raise RuntimeError("Source mailbox is required for Graph acknowledgement sendMail.")
    if not target_mailbox:
        raise RuntimeError("Customer mailbox is required for Graph acknowledgement sendMail.")

    access_token = get_access_token()
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    encoded_mailbox = quote(source_mailbox, safe="")
    url = f"{GRAPH_BASE_URL}/users/{encoded_mailbox}/sendMail"
    payload = {
        "message": {
            "subject": subject,
            "body": {
                "contentType": "Text",
                "content": body,
            },
            "toRecipients": [
                {
                    "emailAddress": {
                        "address": target_mailbox,
                    }
                }
            ],
        },
        "saveToSentItems": True,
    }

    response = requests.post(url, headers=headers, json=payload, timeout=30)
    if response.status_code >= 400:
        raise RuntimeError(
            f"Graph acknowledgement send failed from {source_mailbox} "
            f"to {target_mailbox}: {response.status_code} {response.text}"
        )
