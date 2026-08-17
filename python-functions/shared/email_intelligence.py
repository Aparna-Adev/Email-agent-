import logging
import os
import re
from dataclasses import dataclass, field

from shared.email_cleaner import clean_email_body, extract_issue_summary
from shared.llm_email_intelligence import (
    STRUCTURED_EMAIL_INTELLIGENCE_RULES,
    STRUCTURED_EMAIL_INTELLIGENCE_SCHEMA,
    analyze_email_with_llm,
    format_copilot_thread_summary,
    generate_copilot_style_thread_summary,
    generate_incremental_thread_summary,
)


@dataclass(frozen=True)
class EmailIntelligence:
    client_name: str
    product_name: str
    priority: str
    priority_score: float
    priority_reason: str
    priority_confidence: float
    module: str
    domain: str
    intent: str
    module_confidence: float
    assigned_team: str
    assigned_team_confidence: float
    client_confidence: float
    product_confidence: float
    issue_summary_confidence: float
    review_required: bool
    routing: str
    summary: str
    issue_summary: str
    action_required: str
    thread_summary: str = ""
    current_status: str = ""
    latest_update: str = ""
    active_issue: str = ""
    active_request_type: str = ""
    communication_type: str = ""
    teams_template: str = ""
    executive_summary: str = ""
    issue_overview: str = ""
    key_details: list[str] = field(default_factory=list)
    actions_taken: list[str] = field(default_factory=list)
    requested_actions: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    business_impact: str = ""
    root_cause: str = ""
    teams_case_summary: str | None = None


NEGATIVE_URGENCY_PHRASES = (
    "not urgent",
    "not urgent anymore",
    "no longer urgent",
    "not a priority",
    "no immediate action",
)

RESOLVED_PHRASES = (
    "resolved now",
    "working now",
    "fixed now",
    "issue resolved",
    "no longer blocking",
    "not blocking anymore",
    "resolved",
)

PRIORITY_SIGNAL_GROUPS = (
    (
        "production impact",
        3.0,
        (
            "production down",
            "production is down",
            "production portal is down",
            "prod down",
            "portal is down",
            "system down",
        ),
    ),
    (
        "all users affected",
        2.7,
        ("all users", "everyone", "all employees", "entire team", "company wide"),
    ),
    (
        "system availability",
        2.2,
        ("down", "unavailable", "cannot access", "unable to access", "outage"),
    ),
    (
        "business operation impact",
        2.4,
        (
            "business impact",
            "operations blocked",
            "workflow blocked",
            "blocked",
            "cannot process",
            "not syncing",
            "sync failure",
            "email sync failure",
        ),
    ),
    (
        "business-critical module",
        1.7,
        ("payroll", "payment", "billing", "salary", "compliance"),
    ),
    (
        "security risk",
        3.0,
        ("security breach", "data breach", "unauthorized access", "phishing", "compromised"),
    ),
    (
        "SLA or escalation language",
        2.0,
        ("sla", "breach", "escalation", "escalated", "urgent", "asap", "high priority"),
    ),
    (
        "multiple users affected",
        1.4,
        ("multiple users", "many users", "several users", "team affected"),
    ),
    (
        "partial impact",
        0.8,
        ("partial issue", "intermittent", "some users", "sporadic"),
    ),
)

PRIORITY_DOWNGRADE_GROUPS = (
    ("negative urgency", 2.4, NEGATIVE_URGENCY_PHRASES),
    ("resolved or no longer blocking", 3.0, RESOLVED_PHRASES),
    (
        "single-user impact",
        1.1,
        ("single user", "one user", "one employee", "only one user"),
    ),
    ("workaround available", 0.9, ("workaround", "temporary fix", "can proceed")),
    (
        "informational",
        1.8,
        ("fyi", "for your information", "no action required", "informational"),
    ),
)

MODULE_RULES = (
    (
        "Email Integration",
        "Sync Services",
        (
            "email sync",
            "emails are not syncing",
            "not syncing",
            "mailbox",
            "graph api",
            "webhook",
            "oauth",
            "token",
            "inbox polling",
            "incoming emails",
            "email integration",
        ),
    ),
    (
        "Dashboard UI",
        "Frontend",
        ("dashboard", "ui", "screen", "layout", "button", "page rendering", "alignment"),
    ),
    (
        "Backend Services",
        "Platform",
        ("api", "database", "server", "backend", "failed job", "job failed", "login"),
    ),
    (
        "Infrastructure",
        "Cloud / DevOps",
        (
            "deployment",
            "server down",
            "azure function",
            "hosting",
            "timeout",
            "portal is down",
            "production down",
        ),
    ),
    (
        "QA / Testing",
        "Quality",
        ("testing", "uat", "qa", "regression", "test case", "validation"),
    ),
    (
        "Business Requirement",
        "Product / BA",
        ("requirement", "clarification", "approval", "workflow change", "report format"),
    ),
)


def _contains_any(content: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in content for keyword in keywords)


def _count_matches(content: str, keywords: tuple[str, ...]) -> int:
    return sum(1 for keyword in keywords if keyword in content)


def _clamp_confidence(value: float) -> float:
    return round(max(0.35, min(value, 0.96)), 2)


def _derive_client_from_sender(sender_email: str | None) -> str:
    sender = (sender_email or "").strip().lower()
    if "@" not in sender:
        return ""

    domain = sender.rsplit("@", 1)[1]
    first_label = domain.split(".", 1)[0]
    if not first_label:
        return ""

    return first_label.replace("-", " ").replace("_", " ").title()


def calculate_priority(
    content: str,
    module: str,
    client_confidence: float,
    reply_count: int | None = None,
) -> tuple[str, float, str, float]:
    score = 0.0
    positive_reasons: list[str] = []
    downgrade_reasons: list[str] = []

    for reason, weight, keywords in PRIORITY_SIGNAL_GROUPS:
        if _contains_any(content, keywords):
            score += weight
            positive_reasons.append(reason)

    for reason, weight, keywords in PRIORITY_DOWNGRADE_GROUPS:
        if _contains_any(content, keywords):
            score -= weight
            downgrade_reasons.append(reason)

    if module in {"Email Integration", "Backend Services", "Infrastructure"}:
        score += 0.6
        positive_reasons.append(f"{module} module")
    elif module in {"Dashboard UI", "Business Requirement"}:
        score -= 0.3
        downgrade_reasons.append(f"{module} usually lower SLA")

    if client_confidence >= 0.85:
        score += 0.3
        positive_reasons.append("known client context")

    if reply_count and reply_count >= 3:
        score += 1.0
        positive_reasons.append("repeated replies / thread depth")

    score = round(max(score, 0.0), 1)

    resolved_or_not_urgent = bool(downgrade_reasons) and _contains_any(
        content,
        NEGATIVE_URGENCY_PHRASES + RESOLVED_PHRASES,
    )
    if score >= 8:
        priority = "Critical"
    elif score >= 5:
        priority = "High"
    elif score >= 3:
        priority = "Medium"
    else:
        priority = "Low"

    signal_count = len(positive_reasons) + len(downgrade_reasons)
    confidence = _clamp_confidence(0.52 + min(signal_count, 5) * 0.08)
    if resolved_or_not_urgent:
        confidence = max(confidence, 0.78)
    if priority == "Critical" and confidence < 0.60:
        priority = "High"

    if priority == "Low" and resolved_or_not_urgent:
        reason = "Resolved or not-urgent language downgraded the email priority."
    elif positive_reasons:
        reason = (
            f"{', '.join(positive_reasons[:3]).capitalize()} detected; "
            "no stronger downgrade outweighed the operational impact."
        )
    else:
        reason = "No strong production, all-user, SLA, or business-blocking signal was detected."

    return priority, score, reason, confidence


def classify_module(content: str) -> tuple[str, str, float]:
    best_module = "Unclear"
    best_domain = "General"
    best_score = 0

    for module, domain, keywords in MODULE_RULES:
        score = _count_matches(content, keywords)
        if score > best_score:
            best_module = module
            best_domain = domain
            best_score = score

    if best_score <= 0:
        return "Unclear", "General", 0.45

    return best_module, best_domain, _clamp_confidence(0.56 + best_score * 0.12)


def classify_domain(module: str) -> str:
    for rule_module, domain, _ in MODULE_RULES:
        if rule_module == module:
            return domain

    return "General"


def classify_intent(content: str) -> tuple[str, float]:
    if _contains_any(content, RESOLVED_PHRASES + NEGATIVE_URGENCY_PHRASES):
        return "Resolved / Informational", 0.86
    if _contains_any(content, ("production", "down", "not syncing", "failed", "blocked", "unable")):
        return "Incident", 0.78
    if _contains_any(content, ("requirement", "clarification", "approval", "workflow change")):
        return "Request / Clarification", 0.72
    if _contains_any(content, ("alignment", "layout", "button", "ui")):
        return "Defect / Enhancement", 0.68

    return "General Inquiry", 0.50


def map_module_to_team(module: str, module_confidence: float) -> tuple[str, float]:
    team_by_module = {
        "Email Integration": "Platform / Backend",
        "Dashboard UI": "Frontend / UI Team",
        "Backend Services": "Backend Team",
        "Infrastructure": "Infrastructure Team",
        "QA / Testing": "QA Team",
        "Business Requirement": "Business Analyst",
    }
    assigned_team = team_by_module.get(module, "General Queue / Human Review")
    confidence = _clamp_confidence(module_confidence + 0.04)

    if confidence < 0.60:
        return "General Queue / Human Review", confidence

    return assigned_team, confidence


def _extract_product_name(content: str) -> tuple[str, float]:
    if "laserbeam" in content:
        return "LaserBeam", 0.86
    if "payroll" in content:
        return "Payroll", 0.84
    if "compensation" in content:
        return "Compensation", 0.84
    if _contains_any(content, ("login", "access", "rbac")):
        return "Access Control", 0.72
    if _contains_any(content, ("database", "sync", "email")):
        return "Data Platform", 0.58

    return "Unknown Product", 0.44


def _action_required_for_team(assigned_team: str) -> str:
    actions = {
        "Platform / Backend": "Platform / Backend should investigate integration and service behavior.",
        "Backend Team": "Backend team should investigate API, database, or job failure details.",
        "Infrastructure Team": "Infrastructure team should review hosting, timeout, or service availability.",
        "Frontend / UI Team": "Frontend / UI Team should review the dashboard UI issue.",
        "QA Team": "QA team should validate impact and reproduction details.",
        "Business Analyst": "Business Analyst should review requirement scope and next steps.",
        "General Queue / Human Review": "Human review required before routing or escalation.",
        "Support": "Support team should triage and coordinate the next action.",
    }
    return actions.get(assigned_team, actions["General Queue / Human Review"])


def routing_for_intelligence(review_required: bool, assigned_team: str | None) -> str:
    if review_required:
        return "General Queue / Human Review"

    return (assigned_team or "").strip() or "General Queue / Human Review"


def _log_excerpt(value: str | None, limit: int = 1000) -> str:
    text = (value or "").replace("\r", " ").replace("\n", " ").strip()
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def _safe_str(value, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip() or default


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    if value is None:
        return default
    return bool(value)


def _safe_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = _safe_str(value)
    return [text] if text else []


def _normalize_priority(value: str) -> str:
    value = _safe_str(value, "Medium").title()
    allowed = {"Critical", "High", "Medium", "Low"}
    return value if value in allowed else "Medium"


def _normalize_team(value: str) -> str:
    value = _safe_str(value, "General Queue / Human Review")

    allowed = {
        "Development",
        "QA / Testing",
        "Business Analyst",
        "UI/UX",
        "Management / CEO Attention",
        "General Queue / Human Review",
        "Platform / Backend",
        "Backend Team",
        "Infrastructure Team",
        "Frontend / UI Team",
        "QA Team",
    }

    return value if value in allowed else "General Queue / Human Review"


def _normalize_unresolved_status(value: str) -> str:
    value = _safe_str(value, "Not Clear")
    allowed = {
        "Open",
        "Waiting for Customer",
        "Waiting for Internal Team",
        "Resolved",
        "Not Clear",
    }
    return value if value in allowed else "Not Clear"


def _section_text(text: str, heading: str) -> str:
    normalized_heading = re.escape(heading.rstrip(":"))
    pattern = (
        rf"^\s*\**\s*{normalized_heading}:?\s*\**\s*$\n"
        r"(.*?)(?=^\s*\**\s*(?:\d+\.\s+[^:\n]+:?|Quick Takeaway:?)\s*\**\s*$|\Z)"
    )
    match = re.search(pattern, text or "", flags=re.IGNORECASE | re.DOTALL | re.MULTILINE)
    if not match:
        return ""
    return " ".join(match.group(1).strip().split())


def _quick_takeaway_value(text: str, label: str) -> str:
    pattern = (
        rf"{re.escape(label)}:\s*"
        r"(.*?)(?=(?:[✅⚠⏳]\ufe0f?\s*)?(?:Resolved|Blocker|Waiting on):|\Z)"
    )
    match = re.search(pattern, text or "", flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return " ".join(match.group(1).strip().split())


def _meaningful_value(value: str, empty_markers: tuple[str, ...]) -> str:
    text = _safe_str(value)
    if not text:
        return ""
    lowered = text.lower()
    if any(marker in lowered for marker in empty_markers):
        return ""
    return text


_EMPTY_CURRENT_STATE_MARKERS = (
    "not yet identified",
    "not identified",
    "none",
    "n/a",
    "no current issue",
)

_CLEARED_BLOCKER_TERMS = (
    "blocker cleared",
    "blocker now cleared",
    "no active blocker",
    "no blocker",
    "cleared",
    "resolved",
    "status now active",
    "updated to active",
    "changed to active",
)

_PENDING_COMPLETION_TERMS = (
    "pending completion",
    "awaiting completion confirmation",
    "awaiting support completion",
    "waiting on support completion",
    "confirm once completed",
    "proceed with assignment",
    "assignment to proceed",
    "competency assignment may proceed",
    "competency assignment is awaiting",
    "competency assignment pending",
    "status now active",
    "status has changed to active",
    "status has been updated to active",
    "updated to active",
    "changed to active",
    "blocker now cleared",
    "blocker cleared",
)

_COMPETENCY_BLOCKER_TERMS = (
    "blocked",
    "blocker",
    "cannot proceed",
    "can not proceed",
    "ineligible",
    "job status = i",
    "job status is i",
    "job status i",
    "waiting on hr",
    "waiting on oracle",
    "oracle update",
    "weekly feed",
)

_ACCESS_TERMS = (
    "login",
    "sso",
    "unable to log",
    "cannot log",
    "can not log",
    "access issue",
    "username/password",
)

_WAITING_INTERNAL_TERMS = (
    "proceed with assignment",
    "confirm once completed",
    "awaiting support completion",
    "waiting on support completion",
    "assignment pending completion",
    "pending completion",
    "waiting on support",
    "waiting on development",
    "waiting on internal",
    "waiting on hr",
    "waiting for hr",
    "hr ",
    "oracle",
    "weekly feed",
    "internal team",
)

_WAITING_CUSTOMER_TERMS = (
    "waiting on customer",
    "waiting for customer",
    "awaiting customer",
    "waiting on client",
    "waiting for client",
    "awaiting client",
    "client response",
    "customer response",
)

_RESOLVED_CURRENT_TERMS = (
    "issue resolved",
    "no pending action",
    "no further action",
    "no further action is required",
    "case resolved",
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

_ASSIGNMENT_COMPLETED_TERMS = (
    "competency assignment completed successfully",
    "competency assignment has now been completed successfully",
    "competency assignment has been completed successfully",
    "assignment completed successfully",
)

_LATEST_RESOLUTION_TERMS = (
    "issue resolved",
    "no further action",
    "no further action is required",
    "case resolved",
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

_FINAL_CLOSURE_RESOLUTION_TERMS = (
    "no pending action",
    "no further action",
    "no further action is required",
    "please close this ticket",
    "this can be closed",
    "this ticket can be closed",
    "closed",
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


@dataclass(frozen=True)
class _ThreadCurrentState:
    current_issue: str
    current_status: str
    blocker: str
    waiting_on: str
    resolved: str
    latest_update: str

    @property
    def state_text(self) -> str:
        return " ".join(
            value
            for value in (
                self.current_issue,
                self.current_status,
                self.blocker,
                self.waiting_on,
                self.latest_update,
            )
            if _safe_str(value)
        )

    @property
    def resolution_text(self) -> str:
        return " ".join(
            value
            for value in (
                self.resolved,
                self.current_status,
                self.latest_update,
                self.current_issue,
            )
            if _safe_str(value)
        )


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = _safe_str(text).lower()
    return any(term in lowered for term in terms)


def _first_term_match(text: str, terms: tuple[str, ...]) -> tuple[str, int]:
    lowered = _safe_str(text).lower()
    matches = [(term, lowered.find(term)) for term in terms if lowered.find(term) >= 0]
    if not matches:
        return "", -1
    return min(matches, key=lambda match: match[1])


def _has_strong_resolution_without_later_active_problem(text: str) -> bool:
    resolution_term, resolution_index = _first_term_match(text, _LATEST_RESOLUTION_TERMS)
    if resolution_index < 0:
        return False
    if _has_any(text, _FINAL_CLOSURE_RESOLUTION_TERMS):
        return True

    text_after_resolution = _safe_str(text)[resolution_index + len(resolution_term):]
    return not _has_any(text_after_resolution, _ACTIVE_PROBLEM_INDICATORS)


def _assignment_completed(state: _ThreadCurrentState) -> bool:
    resolution_text = state.resolution_text
    if not _has_any(resolution_text, _ASSIGNMENT_COMPLETED_TERMS):
        return False
    return _has_strong_resolution_without_later_active_problem(resolution_text)


def _resolved_state_active(state: _ThreadCurrentState) -> bool:
    waiting_on_none = not _safe_str(state.waiting_on)
    blocker_cleared = not _safe_str(state.blocker)
    resolution_text = state.resolution_text
    strong_resolution = _has_any(resolution_text, _LATEST_RESOLUTION_TERMS)
    if not strong_resolution:
        return False

    final_closure = _has_any(resolution_text, _FINAL_CLOSURE_RESOLUTION_TERMS)
    clean_resolution = _has_strong_resolution_without_later_active_problem(resolution_text)
    status_resolved = _safe_str(state.current_status).lower() == "resolved"
    latest_resolved = _has_any(state.latest_update, _LATEST_RESOLUTION_TERMS)
    explicit_resolution = strong_resolution and blocker_cleared and waiting_on_none
    return (final_closure or clean_resolution) and (
        latest_resolved or explicit_resolution or status_resolved
    )


def _build_current_state(thread_summary: str, latest_update: str = "") -> _ThreadCurrentState:
    current_issue = _meaningful_value(
        _section_text(thread_summary, "2. Current/Main Issue"),
        _EMPTY_CURRENT_STATE_MARKERS,
    )
    current_status = _meaningful_value(
        _section_text(thread_summary, "5. Current Status"),
        _EMPTY_CURRENT_STATE_MARKERS,
    )
    raw_blocker = _meaningful_value(
        _quick_takeaway_value(thread_summary, "Blocker"),
        ("no active blocker", "no blocker", "none", "not identified"),
    )
    blocker = "" if _has_any(raw_blocker, _CLEARED_BLOCKER_TERMS) else raw_blocker
    waiting_on = _meaningful_value(
        _quick_takeaway_value(thread_summary, "Waiting on"),
        ("no dependency", "none", "not identified"),
    )
    resolved = _meaningful_value(
        _quick_takeaway_value(thread_summary, "Resolved"),
        ("no resolved item", "none", "not identified"),
    )
    return _ThreadCurrentState(
        current_issue=current_issue,
        current_status=current_status,
        blocker=blocker,
        waiting_on=waiting_on,
        resolved=resolved,
        latest_update=_safe_str(latest_update),
    )


def _derive_active_issue(
    thread_summary: str,
    latest_update: str = "",
) -> tuple[str, str, int, int, _ThreadCurrentState]:
    state = _build_current_state(thread_summary, latest_update)
    active_issue = ""
    source_section = ""
    resolved_state_active = _resolved_state_active(state)
    assignment_completed = _assignment_completed(state) or resolved_state_active

    if assignment_completed:
        active_issue = "Competency assignment completed successfully."
        source_section = (
            "Quick Takeaway.Resolved"
            if state.resolved
            else "Current Status"
            if state.current_status
            else "latest_update"
        )
    elif _has_any(state.state_text, _PENDING_COMPLETION_TERMS):
        active_issue = (
            state.current_issue or state.current_status or state.latest_update or state.waiting_on
        )
        source_section = (
            "Current/Main Issue"
            if state.current_issue
            else "Current Status"
            if state.current_status
            else "latest_update"
            if state.latest_update
            else "Quick Takeaway.Waiting on"
        )
    elif state.blocker:
        active_issue = state.blocker
        source_section = "Quick Takeaway.Blocker"
    elif state.current_issue:
        active_issue = state.current_issue
        source_section = "Current/Main Issue"
    elif state.waiting_on:
        active_issue = state.waiting_on
        source_section = "Quick Takeaway.Waiting on"
    elif state.latest_update:
        active_issue = state.latest_update
        source_section = "latest_update"
    else:
        active_issue = "Active issue requires review."
        source_section = "fallback"

    resolved_count = 1 if state.resolved else 0
    active_issue_count = (
        0 if assignment_completed else 1 if active_issue and source_section != "fallback" else 0
    )
    return active_issue, source_section, resolved_count, active_issue_count, state


def _derive_request_type_from_active_issue(
    active_issue: str,
    state: _ThreadCurrentState,
) -> str:
    content = f"{active_issue} {state.state_text}".lower()
    if "competenc" in content and (_assignment_completed(state) or _resolved_state_active(state)):
        return "Competency Assignment Completed"
    if "competenc" in content and _has_any(content, _PENDING_COMPLETION_TERMS):
        return "Competency Assignment Pending Completion"
    if "competenc" in content and _has_any(content, _COMPETENCY_BLOCKER_TERMS):
        return "Competency Assignment Blocker"
    if _has_any(content, _ACCESS_TERMS):
        return "Access Issue"
    if any(word in content for word in ("year-end", "review", "questionnaire", "objective")):
        return "Performance Review Configuration Issue"
    if any(word in content for word in ("oracle", "weekly feed", "dependency", "waiting on")):
        return "Workflow Dependency Issue"
    return "General Request"


def _resolve_status_by_rules(state: _ThreadCurrentState) -> str:
    content = state.state_text.lower()
    if _assignment_completed(state) or _resolved_state_active(state):
        return "resolved"
    if _has_any(content, _PENDING_COMPLETION_TERMS + _WAITING_INTERNAL_TERMS):
        return "waiting_internal"
    if _has_any(content, _WAITING_CUSTOMER_TERMS):
        return "waiting_customer"
    if _has_any(content, _RESOLVED_CURRENT_TERMS) and not _has_any(
        content,
        _PENDING_COMPLETION_TERMS
        + _WAITING_INTERNAL_TERMS
        + _WAITING_CUSTOMER_TERMS
        + _COMPETENCY_BLOCKER_TERMS,
    ) and _has_strong_resolution_without_later_active_problem(content):
        return "resolved"
    if _has_any(content, _COMPETENCY_BLOCKER_TERMS):
        return "waiting_internal"
    return "open"


def _generate_executive_summary(
    active_issue: str,
    state: _ThreadCurrentState,
    request_type: str,
    status: str,
) -> tuple[str, str]:
    content = state.state_text.lower()
    if request_type == "Competency Assignment Completed":
        return (
            "Competency assignment has been completed successfully after employee status "
            "activation. No further action is required.",
            "Quick Takeaway.Resolved",
        )
    if request_type == "Competency Assignment Pending Completion":
        if any(term in content for term in ("active", "oracle", "status")):
            active_sentence = "Employee status has been updated to Active in Oracle."
        else:
            active_sentence = "The competency assignment blocker has been cleared."
        return (
            f"{active_sentence} Competency assignment is awaiting completion confirmation.",
            "Current/Main Issue + Quick Takeaway.Waiting on",
        )
    if request_type == "Competency Assignment Blocker":
        summary = active_issue if active_issue.endswith(".") else f"{active_issue}."
        if state.waiting_on:
            summary = f"{summary} Waiting on {state.waiting_on.rstrip('.')}."
        return summary, "Current/Main Issue + Quick Takeaway.Waiting on"
    if request_type == "Access Issue":
        return (
            "The active issue concerns user access through login or SSO. "
            "Support must confirm access is restored before closure.",
            "Current/Main Issue",
        )
    if active_issue and active_issue != "Active issue requires review.":
        summary = active_issue if active_issue.endswith(".") else f"{active_issue}."
        if state.waiting_on and state.waiting_on.lower() not in summary.lower():
            summary = f"{summary} Waiting on {state.waiting_on.rstrip('.')}."
        return summary, "Current/Main Issue + Quick Takeaway.Waiting on"
    return (
        f"Active issue requires review; current status is {status}.",
        "fallback",
    )


def apply_thread_summary_quality_rules(intelligence: EmailIntelligence) -> EmailIntelligence:
    thread_summary = _safe_str(intelligence.thread_summary)
    latest_update = _safe_str(intelligence.latest_update)
    (
        active_issue,
        active_issue_source_section,
        resolved_count,
        active_issue_count,
        current_state,
    ) = _derive_active_issue(
        thread_summary,
        latest_update,
    )
    request_type = (
        _safe_str(intelligence.active_request_type)
        or _derive_request_type_from_active_issue(active_issue, current_state)
    )
    deterministic_status = _resolve_status_by_rules(current_state)
    original_status = _safe_str(intelligence.current_status, "open")
    status_overridden = bool(
        deterministic_status
        and original_status
        and deterministic_status.lower() != original_status.lower()
    )
    executive_summary, executive_summary_source_section = _generate_executive_summary(
        active_issue,
        current_state,
        request_type,
        deterministic_status,
    )

    logging.info("active_issue_source_section=%s", active_issue_source_section)
    logging.info("executive_summary_source_section=%s", executive_summary_source_section)
    logging.info("active_issue_detected=%s", active_issue)
    logging.info("derived_request_type=%s", request_type)
    logging.info("executive_summary_generated=%s", executive_summary)
    logging.info("resolved_issue_count=%s", resolved_count)
    logging.info("active_issue_count=%s", active_issue_count)
    logging.info("status_overridden_by_rules=%s", status_overridden)
    logging.info("final_thread_status=%s", deterministic_status)
    final_executive_summary = _safe_str(intelligence.executive_summary) or executive_summary
    final_issue_overview = _safe_str(intelligence.issue_overview) or active_issue

    return EmailIntelligence(
        **{
            **intelligence.__dict__,
            "priority": "Low" if request_type == "Competency Assignment Completed" else intelligence.priority,
            "priority_score": 2
            if request_type == "Competency Assignment Completed"
            else intelligence.priority_score,
            "review_required": False
            if request_type == "Competency Assignment Completed"
            else intelligence.review_required,
            "current_status": deterministic_status,
            "active_issue": active_issue,
            "active_request_type": request_type,
            "communication_type": _safe_str(intelligence.communication_type) or request_type,
            "teams_template": _safe_str(intelligence.teams_template) or "incident",
            "executive_summary": final_executive_summary,
            "issue_overview": final_issue_overview,
            "teams_case_summary": None,
            "summary": final_executive_summary,
            "issue_summary": _safe_str(intelligence.issue_summary) or final_issue_overview or final_executive_summary,
        }
    )


def _format_recent_thread_messages(recent_thread_messages: list[dict] | None) -> str:
    if not recent_thread_messages:
        return "No recent thread messages available"

    parts: list[str] = []
    for index, message in enumerate(recent_thread_messages, start=1):
        sender = (
            _safe_str(message.get("sender_name"))
            or _safe_str(message.get("sender_email"), "Not Clear")
        )
        received_at = _safe_str(message.get("received_at"), "Not Clear")
        body = clean_email_body(_safe_str(message.get("cleaned_body")))
        parts.append(
            f"""[Message {index}]
Sender: {sender}
Received At: {received_at}
Body:
{body or "No cleaned body available"}"""
        )

    return "\n\n".join(parts)


def _load_thread_context_for_llm(email) -> tuple[dict | None, list[dict]]:
    conversation_id = _safe_str(getattr(email, "conversation_id", None))
    if not conversation_id:
        logging.info("Thread summary not found: email has no conversation_id.")
        return None, []

    try:
        from shared.db_client import (  # pylint: disable=import-outside-toplevel
            get_recent_thread_messages,
            get_thread_summary,
        )

        thread_summary = get_thread_summary(conversation_id)
        if thread_summary and _safe_str(thread_summary.get("thread_summary")):
            logging.info("Thread summary found for conversation_id=%s", conversation_id)
        else:
            logging.info("Thread summary not found for conversation_id=%s", conversation_id)

        recent_messages = get_recent_thread_messages(conversation_id, limit=5)
        logging.info(
            "Recent thread messages count=%s conversation_id=%s",
            len(recent_messages),
            conversation_id,
        )
        return thread_summary, recent_messages
    except Exception:
        logging.exception(
            "Thread memory fetch failed. Continuing with latest email only."
        )
        return None, []


def _persist_thread_summary_from_llm(email, intelligence: EmailIntelligence, llm_data: dict) -> None:
    conversation_id = _safe_str(getattr(email, "conversation_id", None))
    thread_summary = _safe_str(llm_data.get("thread_summary"))
    if not conversation_id or not thread_summary:
        return

    try:
        from shared.db_client import upsert_thread_summary  # pylint: disable=import-outside-toplevel

        upsert_thread_summary(
            conversation_id=conversation_id,
            thread_summary=thread_summary,
            latest_message_id=getattr(email, "id", None),
            latest_conversation_index=getattr(email, "conversation_index", None),
            latest_processed_email_id=getattr(email, "id", None),
            latest_processed_received_at=getattr(email, "received_at", None),
            priority=intelligence.priority,
            assigned_team=intelligence.assigned_team,
            module=intelligence.module,
            intent=intelligence.intent,
            unresolved_status=_normalize_unresolved_status(
                llm_data.get("unresolved_status")
            ),
            agent_id=getattr(email, "agent_id", None),
        )
        logging.info(
            "Thread summary persisted successfully for conversation_id=%s",
            conversation_id,
        )
    except Exception:
        logging.exception(
            "Thread summary persistence failed for conversation_id=%s",
            conversation_id,
        )


def _build_email_context_for_llm(
    email,
    cleaned_body: str,
    issue_summary: str,
    previous_thread_summary: dict | None = None,
    recent_thread_messages: list[dict] | None = None,
) -> str:
    previous_summary = _safe_str(
        (previous_thread_summary or {}).get("thread_summary"),
        "No previous thread summary available",
    )
    recent_messages = _format_recent_thread_messages(recent_thread_messages)

    has_attachments = getattr(email, "has_attachments", None)
    attachment_info = (
        "Has attachments"
        if has_attachments is True
        else "No attachments"
        if has_attachments is False
        else "Not Clear"
    )

    return f"""
Email details:
- email_id: {_safe_str(getattr(email, "id", None), "Not Clear")}
- conversation_id: {_safe_str(getattr(email, "conversation_id", None), "Not Clear")}
- conversation_index: {_safe_str(getattr(email, "conversation_index", None), "Not Clear")}
- subject: {_safe_str(getattr(email, "subject", None), "*****No Subject*****")}
- sender_name: {_safe_str(getattr(email, "sender_name", None), _safe_str(getattr(email, "original_sender_name", None), "Not Clear"))}
- sender_email: {_safe_str(getattr(email, "sender_email", None), _safe_str(getattr(email, "original_sender_email", None), "Not Clear"))}
- received_time: {_safe_str(getattr(email, "received_at", None), "Not Clear")}
- cleaned_body: {cleaned_body}
- attachment_info: {attachment_info}
- customer_info: {_safe_str(getattr(email, "destination_organization", None), "Not Clear")}
- product_info: {_safe_str(getattr(email, "destination_product_name", None), "Not Clear")}
- source_mailbox: {_safe_str(getattr(email, "source_mailbox", None), _safe_str(getattr(email, "source_email", None), "Not Clear"))}
- routing_rules: source={_safe_str(getattr(email, "source_email", None), "Not Clear")}; teams_channel={_safe_str(getattr(email, "teams_channel_name", None), "Not Clear")}; routed_to={_safe_str(getattr(email, "routed_to_email", None), "Not Clear")}

LATEST EMAIL MESSAGE
{cleaned_body or "No cleaned body available"}

PREVIOUS THREAD SUMMARY
{previous_summary}

RECENT THREAD MESSAGES
{recent_messages}

Expected AI output JSON schema:
{STRUCTURED_EMAIL_INTELLIGENCE_SCHEMA}

Additional instructions:
Analyze the full thread context.
Give more importance to the latest email.
Use previous summary only as historical context.
Do not treat old resolved issues as still active unless the latest email says the issue continues.
Do not force every email into issue/resolution format.
If no active issue exists, do not say "Resolution pending".
Do not repeat quoted signatures or disclaimers.
{STRUCTURED_EMAIL_INTELLIGENCE_RULES}
Initial Rule-Based Issue Summary: {issue_summary}
Return only valid JSON.
"""


def _build_intelligence_from_llm(
    email,
    llm_data: dict,
    cleaned_body: str,
    fallback_issue_summary: str,
) -> EmailIntelligence:
    client_name = (
        _safe_str(getattr(email, "destination_organization", None))
        or _derive_client_from_sender(getattr(email, "sender_email", None))
        or "Unknown Client"
    )

    product_name = (
        _safe_str(getattr(email, "destination_product_name", None))
        or _safe_str(llm_data.get("product_name"))
        or "Unknown Product"
    )

    priority = _normalize_priority(llm_data.get("priority"))
    assigned_team = _normalize_team(llm_data.get("assigned_team"))
    communication_type = _safe_str(llm_data.get("communication_type"), "Informational FYI")
    teams_template = _safe_str(llm_data.get("teams_template"), "informational").lower()
    request_type = _safe_str(llm_data.get("request_type"))
    executive_summary = _safe_str(llm_data.get("executive_summary"))
    issue_overview = _safe_str(llm_data.get("issue_overview"))
    key_details = _safe_list(llm_data.get("key_details"))
    actions_taken = _safe_list(llm_data.get("actions_taken"))
    requested_actions = _safe_list(llm_data.get("requested_actions"))
    next_steps = _safe_list(llm_data.get("next_steps"))
    business_impact = _safe_str(llm_data.get("business_impact"))
    root_cause = _safe_str(llm_data.get("root_cause"))

    issue_summary = (
        issue_overview
        or executive_summary
        or _safe_str(llm_data.get("issue_summary"))
        or fallback_issue_summary
        or "Summary details not available"
    )

    module = _safe_str(llm_data.get("module"), _safe_str(llm_data.get("category"), "Unclear"))
    domain = _safe_str(llm_data.get("domain"), classify_domain(module))
    intent = _safe_str(llm_data.get("intent"), "General Inquiry")

    priority_score = _safe_float(llm_data.get("priority_score"), 0.0)
    priority_reason = _safe_str(
        llm_data.get("priority_reason"),
        "Priority generated by Azure OpenAI with rule-based fallback available.",
    )

    review_required = _safe_bool(
        llm_data.get("review_required"),
        assigned_team == "General Queue / Human Review",
    )

    valid_routing_values = {
        "Development",
        "QA / Testing",
        "Business Analyst",
        "UI/UX",
        "Management / CEO Attention",
        "General Queue / Human Review",
        "Platform / Backend",
        "Backend Team",
        "Infrastructure Team",
        "Frontend / UI Team",
        "QA Team",
    }

    llm_routing = _safe_str(llm_data.get("routing"))

    if llm_routing not in valid_routing_values:
        routing = routing_for_intelligence(review_required, assigned_team)
    else:
        routing = llm_routing

    recommended_actions = _safe_list(llm_data.get("recommended_actions"))
    recommended_action_text = "; ".join(requested_actions or next_steps or recommended_actions)
    action_required = _safe_str(
        llm_data.get("action_required"),
        recommended_action_text or _action_required_for_team(assigned_team),
    )
    thread_summary = _safe_str(llm_data.get("thread_summary"))
    current_status = _safe_str(
        llm_data.get("current_status"),
        _safe_str(llm_data.get("unresolved_status")),
    )
    latest_update = _safe_str(llm_data.get("latest_update"))

    return EmailIntelligence(
        client_name=client_name,
        product_name=product_name,
        priority=priority,
        priority_score=priority_score,
        priority_reason=priority_reason,
        priority_confidence=0.82,
        module=module,
        domain=domain,
        intent=intent,
        module_confidence=0.78,
        assigned_team=assigned_team,
        assigned_team_confidence=0.78,
        client_confidence=0.85 if client_name != "Unknown Client" else 0.50,
        product_confidence=0.85 if product_name != "Unknown Product" else 0.50,
        issue_summary_confidence=0.82,
        review_required=review_required,
        routing=routing,
        summary=issue_summary,
        issue_summary=issue_summary,
        action_required=action_required,
        thread_summary=thread_summary,
        current_status=current_status,
        latest_update=latest_update,
        communication_type=communication_type,
        teams_template=teams_template,
        active_request_type=request_type,
        executive_summary=executive_summary,
        issue_overview=issue_overview,
        key_details=key_details,
        actions_taken=actions_taken,
        requested_actions=requested_actions,
        next_steps=next_steps,
        business_impact=business_impact,
        root_cause=root_cause,
        teams_case_summary=None,
    )


def _issue_summary_from_incremental_result(llm_data: dict, fallback: str) -> str:
    return (
        _safe_str(llm_data.get("latest_update"))
        or _safe_str(llm_data.get("thread_summary"))
        or fallback
        or "Issue details not available"
    )


def extract_incremental_thread_intelligence(
    email,
    previous_summary: str | None,
    new_messages: list[dict],
    system_prompt: str | None = None,
) -> EmailIntelligence:
    raw_body = (
        getattr(email, "cleaned_body", None)
        or getattr(email, "body_preview", None)
        or ""
    )
    cleaned_body = clean_email_body(raw_body)
    fallback_issue_summary = extract_issue_summary(email.subject, cleaned_body)
    llm_data = generate_copilot_style_thread_summary(
        subject=getattr(email, "subject", None),
        previous_summary=previous_summary,
        new_messages=new_messages,
        active_prompt=system_prompt,
    )

    if not _safe_str(llm_data.get("thread_summary")):
        llm_data["thread_summary"] = format_copilot_thread_summary(llm_data)
    llm_data.setdefault(
        "issue_summary",
        _issue_summary_from_incremental_result(llm_data, fallback_issue_summary),
    )
    llm_data.setdefault("routing", llm_data.get("assigned_team"))
    llm_data.setdefault("priority_reason", "Priority generated from incremental thread context.")
    copilot_summary = llm_data.get("copilot_thread_summary")
    if isinstance(copilot_summary, dict):
        llm_data.setdefault("current_status", copilot_summary.get("current_status"))
        llm_data.setdefault("intent", copilot_summary.get("current_status") or "General Inquiry")
        actions_plan = copilot_summary.get("actions_plan")
        if isinstance(actions_plan, list):
            llm_data.setdefault("recommended_actions", actions_plan)
            llm_data.setdefault(
                "action_required",
                "; ".join(str(action).strip() for action in actions_plan if str(action).strip()),
            )
    else:
        llm_data.setdefault("intent", llm_data.get("current_status") or "General Inquiry")
    llm_data.setdefault("module", llm_data.get("category") or "Unclear")
    llm_data.setdefault("domain", classify_domain(_safe_str(llm_data.get("module"), "Unclear")))

    intelligence = _build_intelligence_from_llm(
        email=email,
        llm_data=llm_data,
        cleaned_body=cleaned_body,
        fallback_issue_summary=fallback_issue_summary,
    )
    return apply_thread_summary_quality_rules(intelligence)


def extract_email_intelligence(email, system_prompt: str | None = None) -> EmailIntelligence:
    raw_body = (
        getattr(email, "cleaned_body", None)
        or getattr(email, "body_preview", None)
        or ""
    )
    cleaned_body = clean_email_body(raw_body)
    issue_summary = extract_issue_summary(email.subject, cleaned_body)

    use_llm = os.getenv("USE_LLM_EMAIL_INTELLIGENCE", "false").lower() == "true"

    if use_llm:
        try:
            previous_thread_summary, recent_thread_messages = _load_thread_context_for_llm(
                email
            )
            email_context = _build_email_context_for_llm(
                email=email,
                cleaned_body=cleaned_body,
                issue_summary=issue_summary,
                previous_thread_summary=previous_thread_summary,
                recent_thread_messages=recent_thread_messages,
            )
            llm_data = analyze_email_with_llm(email_context, system_prompt=system_prompt)

            logging.info(
                "Azure OpenAI thread-aware intelligence generated successfully."
            )

            intelligence = _build_intelligence_from_llm(
                email=email,
                llm_data=llm_data,
                cleaned_body=cleaned_body,
                fallback_issue_summary=issue_summary,
            )
            _persist_thread_summary_from_llm(email, intelligence, llm_data)
            return intelligence

        except Exception:
            logging.exception(
                "Thread-aware LLM failed, falling back to rule-based intelligence."
            )

    content = f"{email.subject or ''} {cleaned_body} {issue_summary}".lower()
    client_name = (email.destination_organization or "").strip()
    client_confidence = 0.92
    if not client_name:
        client_name = _derive_client_from_sender(email.sender_email)
        client_confidence = 0.68 if client_name else 0.35

    product_name = (getattr(email, "destination_product_name", None) or "").strip()
    product_confidence = 0.90
    if not product_name:
        product_name, product_confidence = _extract_product_name(content)
    if product_confidence < 0.50:
        product_name = "Unknown Product"

    module, domain, module_confidence = classify_module(content)
    if module_confidence < 0.60:
        module = "Unclear"
        domain = classify_domain(module)
    intent, intent_confidence = classify_intent(content)
    assigned_team, assigned_team_confidence = map_module_to_team(
        module,
        module_confidence,
    )
    reply_count = getattr(email, "reply_count", None)
    priority, priority_score, priority_reason, priority_confidence = calculate_priority(
        content,
        module,
        client_confidence,
        reply_count=reply_count,
    )
    issue_summary_confidence = _clamp_confidence(
        0.58 + min(len(issue_summary), 120) / 400
    )
    review_required = (
        assigned_team_confidence < 0.60
        or priority_confidence < 0.60
        or module_confidence < 0.60
        or product_confidence < 0.50
        or issue_summary_confidence < 0.60
    )
    action_required = _action_required_for_team(assigned_team)
    routing = routing_for_intelligence(review_required, assigned_team)

    logging.info(
        "EMAIL INTELLIGENCE LENGTHS raw=%s cleaned=%s issue_summary=%s",
        len(raw_body or ""),
        len(cleaned_body or ""),
        len(issue_summary or ""),
    )

    return EmailIntelligence(
        client_name=client_name,
        product_name=product_name,
        priority=priority,
        priority_score=priority_score,
        priority_reason=priority_reason,
        priority_confidence=priority_confidence,
        module=module,
        domain=domain,
        intent=intent,
        module_confidence=module_confidence,
        assigned_team=assigned_team,
        assigned_team_confidence=assigned_team_confidence,
        client_confidence=client_confidence,
        product_confidence=product_confidence,
        issue_summary_confidence=max(issue_summary_confidence, intent_confidence - 0.08),
        review_required=review_required,
        routing=routing,
        summary=issue_summary,
        issue_summary=issue_summary,
        action_required=action_required,
        communication_type=intent,
        teams_template="incident" if intent == "Incident" else "informational",
        executive_summary=issue_summary,
        issue_overview=issue_summary if intent == "Incident" else "",
        key_details=[issue_summary],
        requested_actions=[action_required],
        next_steps=[action_required],
    )


def _fallback_float(value, default: float) -> float:
    if value is None:
        return default

    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fallback_bool(value, default: bool) -> bool:
    if value is None:
        return default

    return bool(value)


def build_email_intelligence_from_record(email) -> EmailIntelligence:
    issue_summary = (
        getattr(email, "issue_summary", None)
        or clean_email_body(
            getattr(email, "cleaned_body", None)
            or getattr(email, "body_preview", None)
            or ""
        )
        or getattr(email, "subject", None)
        or "Issue details not available"
    )
    priority = getattr(email, "priority", None) or "Medium"
    priority_score = _fallback_float(getattr(email, "priority_score", None), 0.0)
    priority_reason = (
        getattr(email, "priority_reason", None)
        or "Priority was not available; defaulted for safe review."
    )
    priority_confidence = _fallback_float(
        getattr(email, "priority_confidence", None),
        0.50,
    )
    assigned_team = (
        getattr(email, "assigned_team", None)
        or "General Queue / Human Review"
    )
    assigned_team_confidence = _fallback_float(
        getattr(email, "assigned_team_confidence", None),
        0.50,
    )
    module = getattr(email, "module", None) or "Unclear"
    module_confidence = _fallback_float(
        getattr(email, "module_confidence", None),
        0.50,
    )
    review_required = _fallback_bool(
        getattr(email, "review_required", None),
        assigned_team_confidence < 0.60 or module_confidence < 0.60,
    )
    routing = (
        getattr(email, "routing", None)
        or routing_for_intelligence(review_required, assigned_team)
    )
    thread_summary = getattr(email, "thread_summary", None) or ""
    current_status = getattr(email, "current_status", None) or ""
    executive_summary = getattr(email, "executive_summary", None) or ""

    intelligence = EmailIntelligence(
        client_name=(
            getattr(email, "destination_organization", None)
            or _derive_client_from_sender(getattr(email, "sender_email", None))
            or "Unknown Client"
        ),
        product_name=(
            getattr(email, "destination_product_name", None)
            or "Unknown Product"
        ),
        priority=priority,
        priority_score=priority_score,
        priority_reason=priority_reason,
        priority_confidence=priority_confidence,
        module=module,
        domain=getattr(email, "domain", None) or "General",
        intent=getattr(email, "intent", None) or "General Inquiry",
        module_confidence=module_confidence,
        assigned_team=assigned_team,
        assigned_team_confidence=assigned_team_confidence,
        client_confidence=_fallback_float(
            getattr(email, "client_confidence", None),
            0.50,
        ),
        product_confidence=_fallback_float(
            getattr(email, "product_confidence", None),
            0.50,
        ),
        issue_summary_confidence=_fallback_float(
            getattr(email, "issue_summary_confidence", None),
            0.50,
        ),
        review_required=review_required,
        routing=routing,
        summary=issue_summary,
        issue_summary=issue_summary,
        action_required=_action_required_for_team(assigned_team),
        thread_summary=thread_summary,
        current_status=current_status,
        communication_type=getattr(email, "communication_type", None) or "",
        teams_template=getattr(email, "teams_template", None) or "",
        executive_summary=executive_summary,
        issue_overview=getattr(email, "issue_overview", None) or issue_summary,
        key_details=_safe_list(getattr(email, "key_details", None)),
        actions_taken=_safe_list(getattr(email, "actions_taken", None)),
        requested_actions=_safe_list(getattr(email, "requested_actions", None)),
        next_steps=_safe_list(getattr(email, "next_steps", None)),
        business_impact=getattr(email, "business_impact", None) or "",
        root_cause=getattr(email, "root_cause", None) or "",
        teams_case_summary=None,
    )
    if intelligence.thread_summary:
        return apply_thread_summary_quality_rules(intelligence)
    return intelligence
