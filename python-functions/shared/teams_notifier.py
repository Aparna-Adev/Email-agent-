import logging
import re
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

from shared.email_cleaner import clean_email_body, extract_issue_summary
from shared.email_intelligence import EmailIntelligence


UNKNOWN_CUSTOMER = "Unknown Customer"
UNKNOWN_PRODUCT = "Unknown Product"
UNKNOWN_CONTACT = "Unknown Client Contact"
UNKNOWN_EMAIL = "Unknown Client Email"
NOT_AVAILABLE = "Not available"
REVIEW_CONFIDENCE_THRESHOLD = 0.60
PRODUCT_CONFIDENCE_THRESHOLD = 0.50
GENERAL_REVIEW_ROUTE = "General Queue / Human Review"
IST_TIMEZONE = ZoneInfo("Asia/Kolkata")

PRIORITY_CONFIG = {
    "critical": {
        "label": "Critical",
        "code": "P1",
        "emoji": "\U0001f534",
        "sla": "Response within 1 Hour",
    },
    "high": {
        "label": "High",
        "code": "P2",
        "emoji": "\U0001f7e0",
        "sla": "Response within 4 Hours",
    },
    "medium": {
        "label": "Medium",
        "code": "P3",
        "emoji": "\U0001f7e1",
        "sla": "Response within 1 Business Day",
    },
    "low": {
        "label": "Low",
        "code": "P4",
        "emoji": "\U0001f7e2",
        "sla": "Response within 2-3 Business Days",
    },
}

PRIORITY_CODE_TO_KEY = {
    "p1": "critical",
    "p2": "high",
    "p3": "medium",
    "p4": "low",
}

FREE_EMAIL_DOMAINS = {
    "aol",
    "gmail",
    "hotmail",
    "icloud",
    "live",
    "outlook",
    "protonmail",
    "yahoo",
}

PRODUCT_KEYWORDS = (
    ("laserbeam", "LaserBeam"),
    ("payroll", "Payroll"),
    ("compensation", "Compensation"),
    ("email sync", "Email Integration"),
    ("incoming emails", "Email Integration"),
    ("dashboard", "Dashboard"),
    ("access", "Access Control"),
    ("login", "Access Control"),
)

HONORIFIC_PATTERN = re.compile(
    r"^(mr|mrs|ms|miss|dr|prof|sir|madam)\.?\s+",
    re.IGNORECASE,
)

FORBIDDEN_NOTIFICATION_PATTERNS = (
    r"\bhi\s+team\b",
    r"\bthanks\b",
    r"\bregards\b",
    r"\bsent from (my )?iphone\b",
)


def build_channel_name(client_name: str | None, product_name: str | None) -> str:
    client = (client_name or "").strip()
    product = (product_name or "").strip()

    if client and product:
        return f"KT - {client} - {product}"
    if client:
        return f"KT - {client} - General"
    if product:
        return f"KT - Unknown Client - {product}"

    return "KT - General - Unclassified"


def _format_received_at(received_at) -> str:
    if not received_at:
        return datetime.now(IST_TIMEZONE).strftime("%d-%b-%Y %I:%M %p")

    return received_at.strftime("%d-%b-%Y %I:%M %p")


def _collapse(value: str | None, fallback: str = NOT_AVAILABLE) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text or fallback


def _value_from(source, field_name: str):
    if isinstance(source, dict):
        return source.get(field_name)
    return getattr(source, field_name, None)


def _list_values(value) -> list[str]:
    if isinstance(value, list):
        return [_collapse(item, fallback="") for item in value if _collapse(item, fallback="")]
    text = _collapse(value, fallback="")
    return [text] if text else []


def _join_values(values: list[str]) -> str:
    return "\n".join(f"- {value}" for value in values if value)


def _derive_customer_from_sender(sender_email: str | None) -> str:
    sender = (sender_email or "").strip().lower()
    if "@" not in sender:
        return UNKNOWN_CUSTOMER

    domain = sender.rsplit("@", 1)[1]
    first_label = domain.split(".", 1)[0].strip()
    if not first_label or first_label in FREE_EMAIL_DOMAINS:
        return UNKNOWN_CUSTOMER

    return first_label.replace("-", " ").replace("_", " ").title()


def _resolve_customer(email, intelligence: EmailIntelligence) -> str:
    configured_customer = _collapse(
        getattr(email, "destination_organization", None),
        fallback="",
    )
    if configured_customer:
        return configured_customer

    intelligence_customer = _collapse(intelligence.client_name, fallback="")
    if (
        intelligence_customer
        and intelligence_customer.lower() not in {"unknown client", "needs review"}
        and intelligence.client_confidence >= REVIEW_CONFIDENCE_THRESHOLD
    ):
        return intelligence_customer

    derived_customer = _derive_customer_from_sender(_resolve_client_email(email))
    if derived_customer != UNKNOWN_CUSTOMER:
        return derived_customer

    return UNKNOWN_CUSTOMER


def _resolve_client_contact(email) -> str:
    original_sender_name = (getattr(email, "original_sender_name", None) or "").strip()
    sender_name = (getattr(email, "sender_name", None) or "").strip()
    name = original_sender_name or sender_name

    name = re.sub(r"<[^>]*>", "", name)
    name = re.sub(
        r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "",
        name,
        flags=re.IGNORECASE,
    )
    name = HONORIFIC_PATTERN.sub("", name).strip(" ,;-")

    if name and "@" not in name:
        return _collapse(name, UNKNOWN_CONTACT)

    return UNKNOWN_CONTACT


def _resolve_client_email(email) -> str:
    original_sender_email = (getattr(email, "original_sender_email", None) or "").strip()
    legacy_sender_email = (getattr(email, "sender_email", None) or "").strip()

    return original_sender_email or legacy_sender_email or UNKNOWN_EMAIL


def _resolve_source_mailbox(email) -> str:
    support_mailbox = (getattr(email, "support_mailbox", None) or "").strip()
    routed_to_email = (getattr(email, "routed_to_email", None) or "").strip()
    teams_from_email = (getattr(email, "teams_from_email", None) or "").strip()
    source_email = (getattr(email, "source_email", None) or "").strip()

    return support_mailbox or routed_to_email or teams_from_email or source_email or NOT_AVAILABLE


def _resolve_watch_mailbox(email) -> str:
    return (
        (getattr(email, "watch_mailbox", None) or "").strip()
        or (getattr(email, "mailbox_email", None) or "").strip()
        or NOT_AVAILABLE
    )


def _keyword_product(subject: str | None, body: str | None) -> str:
    content = f"{subject or ''} {body or ''}".lower()
    for keyword, product in PRODUCT_KEYWORDS:
        if keyword in content:
            return product

    return UNKNOWN_PRODUCT


def _resolve_product(email, intelligence: EmailIntelligence) -> str:
    configured_product = _collapse(
        getattr(email, "destination_product_name", None),
        fallback="",
    )
    if configured_product:
        return configured_product

    keyword_product = _keyword_product(
        getattr(email, "subject", None),
        getattr(email, "cleaned_body", None) or getattr(email, "body_preview", None),
    )
    if keyword_product != UNKNOWN_PRODUCT:
        return keyword_product

    product_name = _collapse(intelligence.product_name, fallback="")
    if (
        product_name
        and product_name.lower() not in {"needs review", "unknown product", "unclassified"}
        and intelligence.product_confidence >= PRODUCT_CONFIDENCE_THRESHOLD
    ):
        return product_name

    return UNKNOWN_PRODUCT


def _normalize_priority(priority: str | None) -> str:
    normalized = (priority or "").strip().lower()
    priority_key = PRIORITY_CODE_TO_KEY.get(normalized, normalized)
    return PRIORITY_CONFIG.get(priority_key, PRIORITY_CONFIG["low"])["label"]


def _priority_details(priority: str | None) -> dict[str, str]:
    normalized = (priority or "").strip().lower()
    priority_key = PRIORITY_CODE_TO_KEY.get(normalized, normalized)
    return PRIORITY_CONFIG.get(priority_key, PRIORITY_CONFIG["low"])


def _resolve_request_type(email, intelligence: EmailIntelligence) -> str:
    active_request_type = _collapse(
        getattr(intelligence, "active_request_type", None),
        fallback="",
    )
    if active_request_type:
        return active_request_type

    communication_type = _collapse(
        getattr(intelligence, "communication_type", None),
        fallback="",
    )
    if communication_type:
        return communication_type

    explicit_request_type = (
        _collapse(getattr(email, "request_type", None), fallback="")
        or _collapse(getattr(email, "category", None), fallback="")
        or _collapse(getattr(email, "classification", None), fallback="")
    )
    if explicit_request_type:
        return explicit_request_type

    intelligence_request_type = (
        _collapse(getattr(intelligence, "category", None), fallback="")
        or _collapse(intelligence.module, fallback="")
        or _collapse(intelligence.intent, fallback="")
    )
    if intelligence_request_type and intelligence_request_type.lower() not in {
        "unclear",
        "not clear",
        "general inquiry",
    }:
        return intelligence_request_type

    content = (
        f"{getattr(email, 'subject', '') or ''} "
        f"{getattr(email, 'cleaned_body', '') or ''} "
        f"{getattr(email, 'body_preview', '') or ''} "
        f"{getattr(intelligence, 'active_issue', '') or ''} "
        f"{intelligence.issue_summary or ''}"
    ).lower()
    if "competenc" in content and any(
        word in content for word in ("block", "ineligible", "job status", "assign")
    ):
        return "Competency Assignment Blocker"
    if "ineligible" in content or "job status" in content:
        return "Employee Eligibility Issue"
    if any(word in content for word in ("year-end", "review", "questionnaire", "objective")):
        return "Performance Review Configuration Issue"
    if any(word in content for word in ("oracle", "weekly feed", "dependency", "waiting on")):
        return "Workflow Dependency Issue"
    if "production" in content and any(word in content for word in ("bug", "down", "unavailable", "outage")):
        return "Production Bug"
    if "compensation" in content:
        return "Compensation Issue"
    if "payroll" in content:
        return "Payroll Issue"
    if "login" in content or "access" in content:
        return "Access Issue"

    return "General Request"


def _resolve_routing(intelligence: EmailIntelligence) -> str:
    return (
        _collapse(intelligence.routing, fallback="")
        or _collapse(intelligence.assigned_team, fallback="")
        or GENERAL_REVIEW_ROUTE
    )


def _is_unknown(value: str, unknown_value: str) -> bool:
    return value.strip().lower() == unknown_value.lower()


def _review_required(
    intelligence: EmailIntelligence,
    customer: str,
    product: str,
    routing: str,
) -> bool:
    ambiguous_routing = (
        not routing
        or routing == GENERAL_REVIEW_ROUTE
        or "human review" in routing.lower()
    )
    conflicting_classifications = (
        bool(intelligence.assigned_team and intelligence.routing)
        and intelligence.assigned_team != intelligence.routing
        and intelligence.routing != GENERAL_REVIEW_ROUTE
    )

    return bool(
        intelligence.review_required
        or _is_unknown(customer, UNKNOWN_CUSTOMER)
        or _is_unknown(product, UNKNOWN_PRODUCT)
        or intelligence.client_confidence < REVIEW_CONFIDENCE_THRESHOLD
        or intelligence.priority_confidence < REVIEW_CONFIDENCE_THRESHOLD
        or intelligence.module_confidence < REVIEW_CONFIDENCE_THRESHOLD
        or intelligence.assigned_team_confidence < REVIEW_CONFIDENCE_THRESHOLD
        or intelligence.product_confidence < PRODUCT_CONFIDENCE_THRESHOLD
        or ambiguous_routing
        or conflicting_classifications
    )


def _sentences(text: str, limit: int) -> str:
    cleaned = _collapse(text, fallback="")
    if not cleaned:
        return ""

    parts = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", cleaned)
        if sentence.strip()
    ]
    selected = parts[:limit] if parts else [cleaned]
    result = " ".join(selected).strip()
    if result and result[-1] not in ".!?":
        result = f"{result}."
    return result


def _strip_forbidden_notification_text(text: str) -> str:
    sanitized = text
    for pattern in FORBIDDEN_NOTIFICATION_PATTERNS:
        sanitized = re.sub(pattern, "", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"\s+", " ", sanitized).strip(" ,;-")
    return sanitized


def _business_issue_summary(email, intelligence: EmailIntelligence, product: str) -> str:
    cleaned_body = clean_email_body(
        getattr(email, "cleaned_body", None)
        or getattr(email, "body_preview", None)
        or ""
    )
    extracted_summary = clean_email_body(
        intelligence.issue_summary
        or extract_issue_summary(getattr(email, "subject", None), cleaned_body)
    )
    content = (
        f"{getattr(email, 'subject', '') or ''} "
        f"{cleaned_body} {extracted_summary}"
    ).lower()
    product_phrase = "" if product == UNKNOWN_PRODUCT else f" in {product}"

    if "compensation" in content and any(
        phrase in content for phrase in ("unable", "not able", "cannot", "can't", "failed")
    ):
        return _sentences(
            "A compensation entry cannot be added for a direct report"
            f"{product_phrase}, preventing completion of the expected compensation workflow.",
            3,
        )
    if "sync" in content and any(word in content for word in ("email", "emails", "mail")):
        return _sentences(
            "Incoming email synchronization is failing, which may prevent users "
            "from seeing current communication activity in the dashboard.",
            3,
        )
    if "portal" in content and any(word in content for word in ("down", "unavailable")):
        return _sentences(
            "The production portal appears unavailable for users, affecting access "
            "to critical business functionality.",
            3,
        )
    if "login" in content and any(word in content for word in ("unable", "failed", "error")):
        return _sentences(
            "Users are unable to sign in successfully, blocking access to the application.",
            3,
        )
    if "payroll" in content and any(word in content for word in ("report", "export", "calculation")):
        return _sentences(
            "Payroll processing or reporting is not completing as expected, requiring "
            "business validation before payroll activity continues.",
            3,
        )

    candidate = _strip_forbidden_notification_text(extracted_summary or cleaned_body)
    candidate = re.sub(
        r"(?i)\b(i am|i'm|we are|we're)\s+unable to\b",
        "Users are unable to",
        candidate,
    )
    candidate = re.sub(r"(?i)\bmy direct reports?\b", "a direct report", candidate)
    candidate = re.sub(r"(?i)^issue with\s+", "Client-reported issue with ", candidate)

    return _sentences(candidate or "Business issue details require review.", 3)


def _resolve_summary_with_source(
    email,
    intelligence: EmailIntelligence,
    product: str,
) -> tuple[str, str]:
    executive_summary = _collapse(
        getattr(intelligence, "executive_summary", None),
        fallback="",
    )
    thread_summary = _collapse(getattr(intelligence, "thread_summary", None), fallback="")
    if executive_summary:
        return _sentences(executive_summary, 3), "copilot_thread_summary"

    issue_summary = _collapse(intelligence.issue_summary, fallback="")
    if thread_summary and issue_summary and issue_summary == thread_summary:
        issue_summary = ""
    if issue_summary:
        return _sentences(issue_summary, 3), "issue_summary"

    if thread_summary:
        return _sentences(thread_summary, 3), "copilot_thread_summary"

    ai_summary = _collapse(getattr(email, "ai_summary", None), fallback="")
    if ai_summary:
        return _sentences(ai_summary, 3), "ai_summary"

    body_preview = _collapse(getattr(email, "body_preview", None), fallback="")
    if body_preview:
        return _sentences(body_preview, 3), "body_preview"

    return _business_issue_summary(email, intelligence, product), "business_issue_summary"


def _resolve_summary(email, intelligence: EmailIntelligence, product: str) -> str:
    summary, _source = _resolve_summary_with_source(email, intelligence, product)
    return summary


def _business_impact(email, intelligence: EmailIntelligence, product: str) -> str:
    content = (
        f"{getattr(email, 'subject', '') or ''} "
        f"{getattr(email, 'cleaned_body', '') or ''} "
        f"{getattr(email, 'body_preview', '') or ''} "
        f"{intelligence.issue_summary or ''} {product}"
    ).lower()

    if "compensation" in content or "payroll" in content:
        impact = (
            "Compensation workflow disruption may affect employee compensation "
            "accuracy, approvals, or payroll readiness."
        )
    elif "sync" in content and any(word in content for word in ("email", "emails", "mail")):
        impact = (
            "Communication workflows may be disrupted if incoming emails are not "
            "available in the dashboard."
        )
    elif "production" in content or "portal" in content:
        impact = "Users may be unable to access critical business functionality."
    elif "login" in content or "access" in content:
        impact = "Affected users may be blocked from completing application workflows."
    else:
        impact = "Business users may be delayed until the reported workflow is validated."

    return _sentences(impact, 2)


def _recommended_actions(email, intelligence: EmailIntelligence, product: str) -> list[str]:
    content = (
        f"{getattr(email, 'subject', '') or ''} "
        f"{getattr(email, 'cleaned_body', '') or ''} "
        f"{getattr(email, 'body_preview', '') or ''} "
        f"{intelligence.issue_summary or ''} {product}"
    ).lower()

    if "compensation" in content or "payroll" in content:
        return [
            "Review compensation workflow configuration",
            "Validate direct report eligibility and permissions",
            "Check recent product or rule changes",
            "Confirm expected business workflow with the client contact",
        ]
    if "sync" in content and any(word in content for word in ("email", "emails", "mail")):
        return [
            "Review integration logs and sync job status",
            "Validate mailbox configuration and access tokens",
            "Investigate recent deployments or configuration changes",
            "Confirm affected users and expected communication workflow",
        ]
    if "production" in content or "portal" in content:
        return [
            "Review application availability and error logs",
            "Validate recent deployments and infrastructure health",
            "Confirm affected user scope",
            "Escalate through the configured incident route if impact is confirmed",
        ]

    return [
        "Review application logs",
        "Validate configuration",
        "Investigate recent deployments",
        "Confirm expected business workflow",
    ]


def _format_actions(actions: list[str]) -> str:
    return "\n".join(f"\u2022 {_collapse(action)}" for action in actions[:4])


def _value_or_fallback(*values, fallback: str = NOT_AVAILABLE) -> str:
    for value in values:
        text = _collapse(value, fallback="")
        if text:
            return text
    return fallback


def _list_or_fallback(*values, fallback: str = NOT_AVAILABLE) -> str:
    for value in values:
        items = _list_values(value)
        if items:
            return _join_values(items)
    return fallback


def _template_sections(intelligence: EmailIntelligence, issue_summary: str) -> list[tuple[str, str]]:
    template = _collapse(getattr(intelligence, "teams_template", None), fallback="informational").lower()
    status = _collapse(getattr(intelligence, "current_status", None), fallback="open")

    if template == "incident":
        return [
            ("Issue Overview", _value_or_fallback(intelligence.issue_overview, issue_summary)),
            ("Business Impact", _value_or_fallback(intelligence.business_impact, fallback="Impact requires review.")),
            ("Root Cause", _value_or_fallback(intelligence.root_cause, fallback="Root cause not yet identified.")),
            ("Current Status", status),
            ("Action Required", _list_or_fallback(intelligence.requested_actions, intelligence.next_steps, intelligence.action_required)),
        ]
    if template == "release":
        return [
            ("Release Overview", _value_or_fallback(intelligence.executive_summary, issue_summary)),
            ("Modules Included", _list_or_fallback(intelligence.key_details)),
            ("Artifacts Shared", _list_or_fallback(intelligence.actions_taken)),
            ("Validation Required", _list_or_fallback(intelligence.requested_actions)),
            ("Next Steps", _list_or_fallback(intelligence.next_steps)),
        ]
    if template == "project":
        return [
            ("Project Overview", _value_or_fallback(intelligence.executive_summary, issue_summary)),
            ("Key Details", _list_or_fallback(intelligence.key_details)),
            ("Requested Actions", _list_or_fallback(intelligence.requested_actions)),
            ("Current Status", status),
            ("Next Steps", _list_or_fallback(intelligence.next_steps)),
        ]
    if template == "maintenance":
        return [
            ("Maintenance Overview", _value_or_fallback(intelligence.executive_summary, issue_summary)),
            ("Window/Impact", _value_or_fallback(intelligence.business_impact, fallback="Impact not specified.")),
            ("Completion Status", status),
            ("Next Steps", _list_or_fallback(intelligence.next_steps, intelligence.requested_actions)),
        ]
    if template == "access":
        return [
            ("Access Overview", _value_or_fallback(intelligence.issue_overview, intelligence.executive_summary, issue_summary)),
            ("User/Environment", _list_or_fallback(intelligence.key_details)),
            ("Status", status),
            ("Required Action", _list_or_fallback(intelligence.requested_actions, intelligence.next_steps, intelligence.action_required)),
        ]

    return [
        ("Information Summary", _value_or_fallback(intelligence.executive_summary, issue_summary)),
        ("Key Details", _list_or_fallback(intelligence.key_details)),
        ("Current State", status),
    ]


def _section_blocks(sections: list[tuple[str, str]]) -> list[dict]:
    blocks: list[dict] = []
    for title, value in sections:
        if not value or value == NOT_AVAILABLE:
            continue
        blocks.extend(
            [
                {
                    "type": "TextBlock",
                    "text": f"{title}:",
                    "weight": "Bolder",
                    "spacing": "Medium",
                    "wrap": True,
                },
                {
                    "type": "TextBlock",
                    "text": value,
                    "spacing": "Small",
                    "wrap": True,
                },
            ]
        )
    return blocks


def _resolve_email_id(email) -> str:
    return _collapse(getattr(email, "id", None), fallback=NOT_AVAILABLE)


def _resolve_thread_id(email) -> str:
    conversation_id = _collapse(getattr(email, "conversation_id", None), fallback="")
    conversation_index = _collapse(getattr(email, "conversation_index", None), fallback="")
    if conversation_id and conversation_index:
        return f"{conversation_id} / {conversation_index}"
    return conversation_id or conversation_index or NOT_AVAILABLE


def _valid_url(value: str | None) -> str | None:
    url = (value or "").strip()
    if not url:
        return None

    parsed_url = urlparse(url)
    if parsed_url.scheme in {"http", "https"} and parsed_url.netloc:
        return url

    return None


def _first_valid_url(email, *field_names: str) -> str | None:
    for field_name in field_names:
        url = _valid_url(getattr(email, field_name, None))
        if url:
            return url

    return None


def _build_card_actions(email) -> list[dict]:
    actions = []
    open_email_url = _first_valid_url(
        email,
        "web_link",
        "webLink",
        "email_web_link",
        "graph_web_link",
        "outlook_web_link",
    )
    ticket_url = _first_valid_url(
        email,
        "ticket_url",
        "ticket_link",
        "ticket_web_link",
        "view_ticket_url",
    )
    acknowledge_url = _first_valid_url(
        email,
        "acknowledgement_url",
        "acknowledge_url",
        "acknowledgement_endpoint",
    )

    if open_email_url:
        actions.append({"type": "Action.OpenUrl", "title": "Open Email", "url": open_email_url})
    if ticket_url:
        actions.append({"type": "Action.OpenUrl", "title": "View Ticket", "url": ticket_url})
    if acknowledge_url:
        actions.append({"type": "Action.OpenUrl", "title": "Acknowledge", "url": acknowledge_url})

    return actions


def build_teams_card_payload(email, intelligence: EmailIntelligence) -> dict:
    received_at = _format_received_at(email.received_at)
    customer = _resolve_customer(email, intelligence)
    client_email = _resolve_client_email(email)
    product = _resolve_product(email, intelligence)
    priority_details = _priority_details(intelligence.priority)
    severity = priority_details["label"]
    priority_code = priority_details["code"]
    assigned_team = _collapse(intelligence.assigned_team, fallback=GENERAL_REVIEW_ROUTE)
    request_type = _resolve_request_type(email, intelligence)
    logging.info("teams_header_request_type=%s", request_type)
    current_status = _collapse(
        getattr(intelligence, "current_status", None),
        fallback="open",
    )
    subject = _collapse(getattr(email, "subject", None), fallback=NOT_AVAILABLE)
    issue_summary, teams_summary_source = _resolve_summary_with_source(
        email,
        intelligence,
        product,
    )
    template_blocks = _section_blocks(_template_sections(intelligence, issue_summary))
    logging.info("teams_summary_source=%s", teams_summary_source)
    title = (
        f"{priority_details['emoji']} {priority_code} "
        f"{severity.upper()} {request_type.upper()}"
    )
    card_actions = _build_card_actions(email)
    card_content = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {
                "type": "TextBlock",
                "text": title,
                "weight": "Bolder",
                "size": "Medium",
                "wrap": True,
            },
            {
                "type": "TextBlock",
                "text": f"{customer} | {assigned_team}",
                "spacing": "Small",
                "wrap": True,
            },
            {
                "type": "TextBlock",
                "text": "Subject:",
                "weight": "Bolder",
                "spacing": "Medium",
                "wrap": True,
            },
            {
                "type": "TextBlock",
                "text": subject,
                "spacing": "Small",
                "wrap": True,
            },
            {
                "type": "TextBlock",
                "text": "Summary:",
                "weight": "Bolder",
                "spacing": "Medium",
                "wrap": True,
            },
            {
                "type": "TextBlock",
                "text": issue_summary,
                "spacing": "Small",
                "wrap": True,
            },
            {
                "type": "TextBlock",
                "text": "Details:",
                "weight": "Bolder",
                "spacing": "Medium",
                "wrap": True,
            },
            {
                "type": "FactSet",
                "facts": [
                    {"title": "Customer", "value": customer},
                    {"title": "Sender", "value": client_email},
                    {"title": "Request Type", "value": request_type},
                    {"title": "Severity", "value": severity},
                    {"title": "Priority", "value": priority_code},
                    {"title": "Status", "value": current_status},
                    {"title": "Received At", "value": received_at},
                    {"title": "SLA", "value": priority_details["sla"]},
                    {"title": "Assigned Team", "value": assigned_team},
                ],
            },
            *template_blocks,
        ],
    }

    if card_actions:
        card_content["actions"] = card_actions

    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": card_content,
            }
        ],
    }


def send_teams_card(webhook_url: str, payload: dict) -> None:
    parsed_url = urlparse((webhook_url or "").strip())
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise RuntimeError("Teams webhook URL is invalid.")

    response = requests.post(webhook_url, json=payload, timeout=30)
    if response.status_code >= 400:
        raise RuntimeError(
            f"Teams webhook send failed: {response.status_code} {response.text}"
        )
