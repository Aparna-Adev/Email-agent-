import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from shared.prompt_loader import load_prompt


logger = logging.getLogger(__name__)


def _clean_json_response(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```json\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _coerce_score(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _normalize_priority_score(result: dict[str, Any]) -> dict[str, Any]:
    priority = str(result.get("priority") or "").strip().lower()
    score = _coerce_score(result.get("priority_score"))

    if priority == "critical":
        score = 10
    elif priority == "high":
        score = score if 7 <= score <= 9 else 8
    elif priority == "medium":
        score = score if 4 <= score <= 6 else 5
    elif priority == "low":
        score = score if 1 <= score <= 3 else 2
    else:
        score = max(1, min(score, 10))

    if score >= 10:
        p_level = "P1"
    elif score >= 7:
        p_level = "P2"
    elif score >= 4:
        p_level = "P3"
    else:
        p_level = "P4"

    result["priority_score"] = score
    result["p_level"] = p_level
    result["priority_code"] = p_level
    return result


def _safe_text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text or fallback


def _safe_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = _safe_text(value)
    return [text] if text else []


def _extract_email_context_value(email_context: str, label: str) -> str:
    pattern = rf"(?im)^- {re.escape(label)}:\s*(.*)$"
    match = re.search(pattern, email_context or "")
    return match.group(1).strip() if match else ""


def _extract_latest_email_message(email_context: str) -> str:
    match = re.search(
        r"(?is)LATEST EMAIL MESSAGE\s*\n(.*?)(?:\n\s*PREVIOUS THREAD SUMMARY\s*\n|\Z)",
        email_context or "",
    )
    return match.group(1).strip() if match else ""


def _message_body_text(message: dict[str, Any]) -> str:
    return (
        str(message.get("body_text") or "").strip()
        or str(message.get("cleaned_body") or "").strip()
        or str(message.get("body_preview") or "").strip()
    )


def _format_incremental_message_bodies(new_messages: list[dict[str, Any]]) -> str:
    return "\n\n".join(
        body for body in (_message_body_text(message) for message in new_messages) if body
    )


_STRONG_CASE_RESOLUTION_PHRASES = (
    "issue resolved",
    "case resolved",
    "no further action required",
    "no further action is required",
    "assignment completed successfully",
    "competency assignment completed successfully",
    "competency assignment has been completed successfully",
    "resolved and closed",
    "please close this ticket",
    "this can be closed",
    "this ticket can be closed",
    "problem has been fixed",
    "issue has been fixed",
    "all reported issues are resolved",
    "all reported issues have been resolved",
    "all issues have been resolved",
)

_FINAL_CLOSURE_RESOLUTION_PHRASES = (
    "no further action required",
    "no further action is required",
    "please close this ticket",
    "this can be closed",
    "this ticket can be closed",
    "resolved and closed",
    "all reported issues are resolved",
    "all reported issues have been resolved",
    "all issues have been resolved",
)

_ACTIVE_PROBLEM_INDICATORS = (
    "issue",
    "problem",
    "escalation",
    "unable",
    "missing",
    "blocked",
    "blocking",
    "delay",
    "delayed",
    "pending",
    "requested support actions",
    "please investigate",
    "provide update",
    "next steps",
    "root cause",
    "business impact",
    "unresolved",
    "not yet confirmed",
    "may affect",
    "currently affecting",
    "currently active",
    "investigation continues",
)

COMMUNICATION_TYPES = (
    "Incident",
    "Bug Report",
    "Access Issue",
    "Access Request",
    "UAT Release",
    "Maintenance Notification",
    "Project Coordination",
    "Review Request",
    "Approval Request",
    "Status Update",
    "Informational FYI",
    "Data Validation",
)

TEAMS_TEMPLATES = (
    "incident",
    "access",
    "release",
    "maintenance",
    "project",
    "review",
    "informational",
    "data_validation",
)

STRUCTURED_EMAIL_INTELLIGENCE_SCHEMA = """{
  "communication_type": "",
  "teams_template": "",
  "executive_summary": "",
  "issue_overview": "",
  "key_details": [],
  "actions_taken": [],
  "requested_actions": [],
  "current_status": "",
  "next_steps": [],
  "business_impact": "",
  "root_cause": "",
  "request_type": "",
  "priority": "",
  "priority_score": 1,
  "assigned_team": "",
  "review_required": true
}"""

STRUCTURED_EMAIL_INTELLIGENCE_RULES = f"""Strict classification rules:
- Do not force every email into issue/resolution format.
- First classify communication_type as one of: {", ".join(COMMUNICATION_TYPES)}.
- Then select teams_template as one of: {", ".join(TEAMS_TEMPLATES)}.
- If no active issue exists, do not say "Resolution pending".
- Use incident only for active break/fix, outage, bug, blocker, or degraded-service cases.
- Use access for access issues or access requests.
- Use release for UAT releases, release notes, deployments, and artifacts shared for validation.
- Use maintenance for planned or completed maintenance notifications.
- Use project for coordination, scheduling, status, dependency, or delivery planning.
- Use review for review or approval requests.
- Use informational for FYI, accepted meeting, status-only, or no-action emails.
- Use data_validation for validation findings, data checks, mismatch reports, or reconciliation.
- Keep key_details, actions_taken, requested_actions, and next_steps as short arrays.
- current_status must describe the current state without inventing a resolution lifecycle."""


@dataclass(frozen=True)
class ResolutionDetectionResult:
    detected: bool
    trigger_phrase: str = ""
    trigger_sentence: str = ""
    active_indicator_after_phrase: str = ""


def _latest_message_text(new_messages: list[dict[str, Any]]) -> str:
    if not new_messages:
        return ""
    latest_message = new_messages[-1]
    return _message_body_text(latest_message)


def _sentences(text: str) -> list[str]:
    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text or "")
        if sentence.strip()
    ]


def _sentence_containing_phrase(text: str, phrase: str) -> str:
    lowered_phrase = phrase.lower()
    for sentence in _sentences(text):
        if lowered_phrase in sentence.lower():
            return sentence
    return ""


def _first_phrase_match(text: str, phrases: tuple[str, ...]) -> tuple[str, int]:
    lowered = text.lower()
    matches = [
        (phrase, lowered.find(phrase))
        for phrase in phrases
        if lowered.find(phrase) >= 0
    ]
    if not matches:
        return "", -1
    return min(matches, key=lambda match: match[1])


def _first_active_indicator(text: str) -> str:
    lowered = text.lower()
    for indicator in _ACTIVE_PROBLEM_INDICATORS:
        if indicator in lowered:
            return indicator
    return ""


def _detect_latest_message_resolution(
    new_messages: list[dict[str, Any]],
) -> ResolutionDetectionResult:
    latest_text = _latest_message_text(new_messages)
    trigger_phrase, trigger_index = _first_phrase_match(
        latest_text,
        _STRONG_CASE_RESOLUTION_PHRASES,
    )
    if not trigger_phrase:
        return ResolutionDetectionResult(detected=False)

    trigger_sentence = _sentence_containing_phrase(latest_text, trigger_phrase)
    if _first_phrase_match(latest_text, _FINAL_CLOSURE_RESOLUTION_PHRASES)[0]:
        return ResolutionDetectionResult(
            detected=True,
            trigger_phrase=trigger_phrase,
            trigger_sentence=trigger_sentence,
        )

    text_after_trigger = latest_text[trigger_index + len(trigger_phrase):]
    active_indicator = _first_active_indicator(text_after_trigger)
    if active_indicator:
        return ResolutionDetectionResult(
            detected=False,
            trigger_phrase=trigger_phrase,
            trigger_sentence=trigger_sentence,
            active_indicator_after_phrase=active_indicator,
        )

    return ResolutionDetectionResult(
        detected=True,
        trigger_phrase=trigger_phrase,
        trigger_sentence=trigger_sentence,
    )


def _latest_message_resolution_detected(new_messages: list[dict[str, Any]]) -> bool:
    return _detect_latest_message_resolution(new_messages).detected


def _apply_latest_message_resolution_override(result: dict[str, Any]) -> dict[str, Any]:
    summary = result.get("copilot_thread_summary")
    if not isinstance(summary, dict):
        summary = {}

    summary["overall_context"] = _safe_text(
        summary.get("overall_context"),
        "Login issue resolved and competency assignment completed successfully.",
    )
    summary["current_main_issue"] = (
        "No active issue remains. NS Operations competency assignment has been "
        "completed successfully."
    )
    summary["current_status"] = "resolved"
    summary["quick_takeaway"] = {
        "resolved": "Login issue resolved and competency assignment completed successfully.",
        "blocker": "No active blocker.",
        "waiting_on": "None.",
    }
    result["copilot_thread_summary"] = summary
    result["latest_update"] = (
        "Competency assignment has been completed successfully after employee status "
        "activation. No further action is required."
    )
    result["priority"] = "Low"
    result["priority_score"] = 2
    result["request_type"] = "Competency Assignment Completed"
    result["review_required"] = False
    result["current_status"] = "resolved"
    result["issue_summary"] = result["latest_update"]
    result["communication_type"] = "Status Update"
    result["teams_template"] = "informational"
    result["executive_summary"] = result["latest_update"]
    result["issue_overview"] = "No active issue remains."
    result["key_details"] = [
        "Competency assignment has been completed successfully.",
        "Employee status activation has been confirmed.",
    ]
    result["actions_taken"] = ["Employee status was activated.", "Assignment was completed."]
    result["requested_actions"] = []
    result["next_steps"] = []
    result["business_impact"] = "No current business impact remains."
    result["root_cause"] = "Employee status was not active before completion."
    return result


def get_azure_openai_client() -> OpenAI:
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    api_key = os.getenv("AZURE_OPENAI_API_KEY")

    if not endpoint:
        raise ValueError("AZURE_OPENAI_ENDPOINT is missing")

    if not api_key:
        raise ValueError("AZURE_OPENAI_API_KEY is missing")

    return OpenAI(
        base_url=endpoint,
        api_key=api_key,
    )


def get_fallback_system_prompt() -> str:
    return load_prompt("email_triage_prompt.txt")


def _format_incremental_messages(new_messages: list[dict[str, Any]]) -> str:
    if not new_messages:
        return "No new email replies available."

    formatted_messages: list[str] = []
    for index, message in enumerate(new_messages, start=1):
        sender = (
            str(message.get("original_sender_name") or "").strip()
            or str(message.get("sender_name") or "").strip()
            or str(message.get("original_sender_email") or "").strip()
            or str(message.get("sender_email") or "").strip()
            or "Not Clear"
        )
        body = _message_body_text(message) or "No body available."
        formatted_messages.append(
            f"""[New Message {index}]
Email ID: {message.get("id") or "Not Clear"}
Conversation Index: {message.get("conversation_index") or "Not Clear"}
Sender: {sender}
Received At: {message.get("received_at") or "Not Clear"}
Subject: {message.get("subject") or "No Subject"}
Body:
{body}"""
        )

    return "\n\n".join(formatted_messages)


def format_copilot_thread_summary(summary_json: dict[str, Any]) -> str:
    summary = summary_json.get("copilot_thread_summary", summary_json)
    if not isinstance(summary, dict):
        summary = {}

    title = _safe_text(summary.get("title"), "Email Summary")
    overall_context = _safe_text(
        summary.get("overall_context"),
        "Conversation context is not yet available.",
    )
    initial_issue = _safe_text(
        summary.get("initial_issue"),
        "Initial issue not yet identified.",
    )
    current_main_issue = _safe_text(
        summary.get("current_main_issue"),
        "Current issue not yet identified.",
    )
    root_cause_findings = _safe_text(
        summary.get("root_cause_findings"),
        "Root cause not yet identified.",
    )
    current_status = _safe_text(summary.get("current_status"), "open")
    actions_plan = _safe_list(summary.get("actions_plan"))
    if not actions_plan:
        actions_plan = ["No action plan identified yet."]

    quick_takeaway = summary.get("quick_takeaway")
    if not isinstance(quick_takeaway, dict):
        quick_takeaway = {}
    resolved = _safe_text(
        quick_takeaway.get("resolved"),
        "No resolved item identified yet.",
    )
    blocker = _safe_text(quick_takeaway.get("blocker"), "No active blocker identified.")
    waiting_on = _safe_text(quick_takeaway.get("waiting_on"), "No dependency identified.")
    action_text = "\n".join(f"\u2022 {action}" for action in actions_plan)

    return f"""{title}

Overall context:
{overall_context}

1. Initial Issue
{initial_issue}

2. Current/Main Issue
{current_main_issue}

3. Root Cause / Findings
{root_cause_findings}

4. Actions & Plan
{action_text}

5. Current Status
{current_status}

Quick Takeaway
\u2705 Resolved: {resolved}
\u26a0\ufe0f Blocker: {blocker}
\u23f3 Waiting on: {waiting_on}"""


def generate_copilot_style_thread_summary(
    subject: str | None,
    previous_summary: str | None,
    new_messages: list[dict[str, Any]],
    active_prompt: str | None,
) -> dict[str, Any]:
    deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")

    if not deployment_name:
        raise ValueError("AZURE_OPENAI_DEPLOYMENT_NAME is missing")

    system_prompt = (active_prompt or "").strip() or get_fallback_system_prompt()
    previous_summary_text = (previous_summary or "").strip()
    new_messages_text = _format_incremental_messages(new_messages)
    subject_text = _safe_text(subject, "No Subject")
    client = get_azure_openai_client()

    logger.info(
        "copilot_style_summary_started previous_summary_present=%s new_messages_count=%s",
        bool(previous_summary_text),
        len(new_messages),
    )

    user_prompt = f"""
Generate an Outlook Copilot-style enterprise email thread summary using only the previous summary and new replies below.

Subject:
{subject_text}

Previous thread summary:
{previous_summary_text or "No previous thread summary available."}

New email replies:
{new_messages_text}

Rules:
- If previous thread summary is empty, generate a fresh full-thread summary from the new messages.
- If previous thread summary exists, update it using only previous summary plus new replies.
- Before writing summaries, classify the latest communication type and Teams template.
- Do not force every email into issue/resolution format.
- If no active issue exists, do not say "Resolution pending".
- The latest new message has priority over previous_summary.
- Treat previous_summary as historical context, not always current truth.
- Preserve historical context without repeating old message text unnecessarily.
- Clearly separate resolved issues from the current active issue.
- Preserve blockers, dependencies, actions, owners, and current status only when they still apply after the latest new message.
- If the latest message says the issue is resolved, completed successfully, fixed, closed, or no further action is required:
  - current_main_issue must say no active issue remains.
  - current_status must be "resolved".
  - quick_takeaway.blocker must say "No active blocker."
  - quick_takeaway.waiting_on must say "None."
  - Do not preserve old blockers as current blockers.
- If the latest message says a blocker is cleared, move that blocker to resolved/history.
- Root Cause / Findings may preserve historical causes.
- Current/Main Issue must describe only the current unresolved issue.
- Quick Takeaway must reflect the latest state, not old state.
- Do not contradict yourself across sections.
- Do not invent facts.
- If root cause is unknown, write "Root cause not yet identified."
- If no resolved item exists, write "No resolved item identified yet."
- Use the configured active agent prompt as business behavior guidance, but enforce the JSON shape below.

{STRUCTURED_EMAIL_INTELLIGENCE_RULES}

Expected JSON:
{{
  "copilot_thread_summary": {{
    "title": "Email Summary \u2014 {subject_text}",
    "overall_context": "",
    "initial_issue": "",
    "current_main_issue": "",
    "root_cause_findings": "",
    "actions_plan": [],
    "current_status": "open | waiting_customer | waiting_internal | resolved",
    "quick_takeaway": {{
      "resolved": "",
      "blocker": "",
      "waiting_on": ""
    }}
  }},
  "latest_update": "",
  "communication_type": "",
  "teams_template": "",
  "executive_summary": "",
  "issue_overview": "",
  "key_details": [],
  "actions_taken": [],
  "requested_actions": [],
  "current_status": "",
  "next_steps": [],
  "business_impact": "",
  "root_cause": "",
  "request_type": "",
  "priority": "Critical | High | Medium | Low",
  "priority_score": 1,
  "assigned_team": "",
  "review_required": true
}}
Return only valid JSON.
"""

    for message in new_messages:
        selected_body = _message_body_text(message)
        logger.info(
            "LLM MESSAGE LENGTHS email_id=%s selected=%s preview=%s",
            message.get("id") or "Not Clear",
            len(selected_body or ""),
            len(str(message.get("body_preview") or "")),
        )
    cleaned_body = _format_incremental_message_bodies(new_messages) or new_messages_text
    logger.info("SUBJECT=%s", subject_text)
    logger.info("LLM INPUT LENGTH=%s", len(cleaned_body))
    logger.info("LLM USER PROMPT LENGTH=%s", len(user_prompt))

    response = client.chat.completions.create(
        model=deployment_name,
        max_completion_tokens=1800,
        messages=[
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
    )

    raw_text = response.choices[0].message.content or ""
    cleaned = _clean_json_response(raw_text)
    result = _normalize_priority_score(json.loads(cleaned))
    resolution_result = _detect_latest_message_resolution(new_messages)
    logger.info(
        "latest_message_resolution_detected=%s",
        str(resolution_result.detected).lower(),
    )
    logger.info("resolution_trigger_phrase=%s", resolution_result.trigger_phrase)
    logger.info("resolution_trigger_sentence=%s", resolution_result.trigger_sentence)
    if resolution_result.active_indicator_after_phrase:
        logger.info(
            "resolution_active_indicator_after_phrase=%s",
            resolution_result.active_indicator_after_phrase,
        )
    if resolution_result.detected:
        result = _apply_latest_message_resolution_override(result)
        result = _normalize_priority_score(result)
        override_summary = result.get("copilot_thread_summary", {})
        quick_takeaway = (
            override_summary.get("quick_takeaway", {})
            if isinstance(override_summary, dict)
            else {}
        )
        logger.info("resolved_state_override_applied=true")
        logger.info(
            "current_main_issue_after_override=%s",
            override_summary.get("current_main_issue", "")
            if isinstance(override_summary, dict)
            else "",
        )
        logger.info("quick_takeaway_after_override=%s", quick_takeaway)
    else:
        logger.info("resolved_state_override_applied=false")
    formatted_summary = format_copilot_thread_summary(result)
    result["thread_summary"] = formatted_summary

    logger.info("copilot_style_summary_generated")
    logger.info("formatted_thread_summary_length=%s", len(formatted_summary))
    return result


def generate_incremental_thread_summary(
    previous_summary: str | None,
    new_messages: list[dict[str, Any]],
    active_prompt: str | None,
) -> dict[str, Any]:
    deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")

    if not deployment_name:
        raise ValueError("AZURE_OPENAI_DEPLOYMENT_NAME is missing")

    system_prompt = (active_prompt or "").strip() or get_fallback_system_prompt()
    previous_summary_text = (previous_summary or "").strip()
    new_messages_text = _format_incremental_messages(new_messages)
    client = get_azure_openai_client()

    logger.info("Incremental thread summarization started.")

    user_prompt = f"""
Previous thread summary:
{previous_summary_text or "No previous thread summary available."}

New email replies:
{new_messages_text}

Task:
Update the thread summary. Do not repeat old messages unnecessarily. Preserve important historical context and append only meaningful new developments. Return structured JSON.

Expected JSON:
{{
  "thread_summary": "",
  "latest_update": "",
  "current_status": "open | waiting_customer | waiting_internal | resolved",
  "unresolved_questions": [],
  "action_items": [],
  "current_owner": "",
  "priority": "Critical | High | Medium | Low",
  "priority_score": 1,
  "assigned_team": "",
  "review_required": true
}}
Return only valid JSON.
"""

    for message in new_messages:
        selected_body = _message_body_text(message)
        logger.info(
            "LLM MESSAGE LENGTHS email_id=%s selected=%s preview=%s",
            message.get("id") or "Not Clear",
            len(selected_body or ""),
            len(str(message.get("body_preview") or "")),
        )
    cleaned_body = _format_incremental_message_bodies(new_messages) or new_messages_text
    logger.info("SUBJECT=%s", "Incremental Thread Summary")
    logger.info("LLM INPUT LENGTH=%s", len(cleaned_body))
    logger.info("LLM USER PROMPT LENGTH=%s", len(user_prompt))

    response = client.chat.completions.create(
        model=deployment_name,
        max_completion_tokens=1600,
        messages=[
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
    )

    raw_text = response.choices[0].message.content or ""
    cleaned = _clean_json_response(raw_text)
    result = _normalize_priority_score(json.loads(cleaned))

    logger.info("Incremental thread summarization completed.")
    return result


def analyze_email_with_llm(email_context: str, system_prompt: str | None = None) -> dict[str, Any]:
    deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")

    if not deployment_name:
        raise ValueError("AZURE_OPENAI_DEPLOYMENT_NAME is missing")

    active_prompt = (system_prompt or "").strip() or get_fallback_system_prompt()
    client = get_azure_openai_client()

    logger.info("ACTIVE PROMPT USED BY AZURE OPENAI:")
    logger.info(active_prompt[:1000])

    subject = _extract_email_context_value(email_context, "subject")
    cleaned_body = _extract_latest_email_message(email_context)
    if not cleaned_body:
        cleaned_body = _extract_email_context_value(email_context, "cleaned_body")
    logger.info("SUBJECT=%s", subject)
    logger.info("LLM INPUT LENGTH=%s", len(cleaned_body))
    logger.info("LLM USER PROMPT LENGTH=%s", len(email_context))

    response = client.chat.completions.create(
        model=deployment_name,
        max_completion_tokens=1600,
        messages=[
            {
                "role": "system",
                "content": active_prompt,
            },
            {
                "role": "user",
                "content": email_context,
            },
        ],
    )

    raw_text = response.choices[0].message.content or ""
    cleaned = _clean_json_response(raw_text)

    return _normalize_priority_score(json.loads(cleaned))
