import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_FUNCTIONS_ROOT = PROJECT_ROOT / "python-functions"
VALIDATION_INPUT_DIR = PROJECT_ROOT / "validation_emails"
VALIDATION_OUTPUT_DIR = PROJECT_ROOT / "validation_results"
REPORT_PATH = VALIDATION_OUTPUT_DIR / "email_intelligence_validation_report.csv"

sys.path.insert(0, str(PYTHON_FUNCTIONS_ROOT))

from shared.email_cleaner import clean_email_body, extract_issue_summary  # noqa: E402
from shared.email_intelligence import extract_email_intelligence  # noqa: E402
from shared.teams_notifier import build_teams_card_payload  # noqa: E402


REPORT_COLUMNS = [
    "file_name",
    "subject",
    "cleaned_body",
    "issue_summary",
    "priority",
    "priority_score",
    "priority_confidence",
    "priority_reason",
    "module",
    "module_confidence",
    "domain",
    "intent",
    "assigned_team",
    "assigned_team_confidence",
    "review_required",
    "routing",
    "cleaning_pass",
    "issue_pass",
    "priority_pass",
    "routing_pass",
    "confidence_pass",
    "overall_pass",
    "failure_reason",
]

NOISE_MARKERS = (
    "confidentiality notice",
    "this email and any attachments",
    "original message",
    "forwarded message",
    "sent from my iphone",
    "sent from outlook",
    "sent from my samsung",
    "<p>",
    "<div>",
    "<b>",
)


@dataclass(frozen=True)
class SampleEmail:
    file_name: str
    case_name: str
    subject: str
    body: str


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _case_file_name(path: Path, case_name: str | None) -> str:
    if not case_name:
        return path.name

    safe_case_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", case_name).strip("_")
    return f"{path.name}::{safe_case_name}"


def _parse_email_blocks(path: Path) -> list[SampleEmail]:
    text = _normalize_newlines(path.read_text(encoding="utf-8-sig"))
    matches = list(re.finditer(r"(?m)^Email\s+\d+\s*$", text))

    if not matches:
        subject_match = re.search(r"(?im)^Subject:\s*(?P<subject>.+)$", text)
        subject = subject_match.group("subject").strip() if subject_match else path.stem
        body = (
            text[subject_match.end() :].strip()
            if subject_match
            else text.strip()
        )
        return [
            SampleEmail(
                file_name=path.name,
                case_name=path.stem,
                subject=subject,
                body=body,
            )
        ]

    samples: list[SampleEmail] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[start:end].strip()
        subject_match = re.search(r"(?im)^Subject:\s*(?P<subject>.+)$", block)
        if not subject_match:
            continue

        subject = subject_match.group("subject").strip()
        body = block[subject_match.end() :].strip()
        samples.append(
            SampleEmail(
                file_name=_case_file_name(path, match.group(0).strip()),
                case_name=match.group(0).strip(),
                subject=subject,
                body=body,
            )
        )

    return samples


def load_samples() -> list[SampleEmail]:
    samples: list[SampleEmail] = []
    for path in sorted(VALIDATION_INPUT_DIR.glob("*")):
        if not path.is_file():
            continue
        samples.extend(_parse_email_blocks(path))

    return samples


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _expected_priority(content: str) -> set[str]:
    if _contains_any(
        content,
        (
            "critical",
            "outage",
            "unavailable",
            "down",
            "all users",
            "unable to authenticate",
            "payment failure",
            "transaction failures",
        ),
    ):
        return {"Critical", "High"}

    if _contains_any(
        content,
        (
            "urgent",
            "immediate",
            "escalation",
            "multiple",
            "several",
            "production",
            "business units",
            "complaint",
        ),
    ):
        return {"High", "Critical", "Medium"}

    if _contains_any(
        content,
        (
            "feature request",
            "enhancement request",
            "newsletter",
            "product update",
            "announcement",
            "scheduled maintenance",
            "license renewal",
            "planned service upgrade",
            "dark mode",
        ),
    ):
        return {"Low", "Medium"}

    return {"Low", "Medium", "High"}


def _expected_teams(content: str) -> set[str]:
    if _contains_any(content, ("dashboard", "button", "ui", "screen", "alignment", "page")):
        return {"Frontend / UI Team", "Platform / Backend"}
    if _contains_any(content, ("email", "notification", "mail", "inbox", "sync")):
        return {"Platform / Backend", "Backend Team"}
    if _contains_any(content, ("database", "api", "server", "job", "login", "authentication", "portal")):
        return {"Backend Team", "Platform / Backend", "Infrastructure Team"}
    if _contains_any(content, ("deployment", "infrastructure", "vpn", "timeout", "cpu", "outage")):
        return {"Infrastructure Team", "Backend Team"}
    if _contains_any(content, ("testing", "uat", "qa", "regression")):
        return {"QA Team"}
    if _contains_any(content, ("requirement", "clarification", "approval", "workflow", "report format", "feature request", "enhancement request")):
        return {"Business Analyst", "Frontend / UI Team"}

    return {
        "Platform / Backend",
        "Backend Team",
        "Infrastructure Team",
        "Frontend / UI Team",
        "QA Team",
        "Business Analyst",
        "General Queue / Human Review",
    }


def _validate_cleaning(cleaned_body: str) -> tuple[bool, str | None]:
    lower = cleaned_body.lower()
    if not cleaned_body.strip():
        return False, "cleaned body is empty"
    if _contains_any(lower, NOISE_MARKERS):
        return False, "cleaned body still contains quoted/footer/html noise"

    return True, None


def _validate_issue(issue_summary: str, cleaned_body: str) -> tuple[bool, str | None]:
    if not issue_summary.strip():
        return False, "issue summary is empty"
    if issue_summary == "Issue details not available":
        return False, "issue summary fell back to generic placeholder"
    if len(issue_summary.split()) < 3 and len(cleaned_body.split()) >= 6:
        return False, "issue summary is too short for available body"

    return True, None


def _validate_priority(priority: str, content: str) -> tuple[bool, str | None]:
    expected = _expected_priority(content)
    if priority not in expected:
        return False, f"priority {priority} not in expected set {sorted(expected)}"

    return True, None


def _validate_routing(assigned_team: str, content: str) -> tuple[bool, str | None]:
    expected = _expected_teams(content)
    if assigned_team not in expected:
        return False, f"assigned team {assigned_team} not in expected set {sorted(expected)}"

    return True, None


def _validate_confidence(
    priority_confidence: float,
    module_confidence: float,
    assigned_team_confidence: float,
    review_required: bool,
) -> tuple[bool, str | None]:
    values = [priority_confidence, module_confidence, assigned_team_confidence]
    if any(value < 0.0 or value > 1.0 for value in values):
        return False, "confidence score outside 0..1 range"
    if (
        assigned_team_confidence < 0.60
        or module_confidence < 0.60
    ) and not review_required:
        return False, "low confidence item is not marked review_required"

    return True, None


def validate_sample(sample: SampleEmail) -> dict[str, object]:
    cleaned_body = clean_email_body(sample.body)
    issue_summary = extract_issue_summary(sample.subject, cleaned_body)
    email_record = SimpleNamespace(
        subject=sample.subject,
        cleaned_body=cleaned_body,
        body_preview=sample.body,
        sender_email="validation@example.com",
        destination_organization="Validation Client",
        destination_product_name="",
        received_at=None,
        reply_count=1,
        original_sender_name="",
        original_sender_email="validation@example.com",
        support_mailbox="support@example.com",
        routed_to_email="",
        teams_from_email="",
        source_email="",
    )
    intelligence = extract_email_intelligence(email_record)
    build_teams_card_payload(email_record, intelligence)

    content = f"{sample.subject} {cleaned_body} {issue_summary}".lower()
    cleaning_pass, cleaning_failure = _validate_cleaning(cleaned_body)
    issue_pass, issue_failure = _validate_issue(issue_summary, cleaned_body)
    priority_pass, priority_failure = _validate_priority(intelligence.priority, content)
    routing_pass, routing_failure = _validate_routing(intelligence.assigned_team, content)
    confidence_pass, confidence_failure = _validate_confidence(
        intelligence.priority_confidence,
        intelligence.module_confidence,
        intelligence.assigned_team_confidence,
        intelligence.review_required,
    )
    failures = [
        reason
        for reason in (
            cleaning_failure,
            issue_failure,
            priority_failure,
            routing_failure,
            confidence_failure,
        )
        if reason
    ]
    overall_pass = not failures

    return {
        "file_name": sample.file_name,
        "subject": sample.subject,
        "cleaned_body": cleaned_body,
        "issue_summary": issue_summary,
        "priority": intelligence.priority,
        "priority_score": intelligence.priority_score,
        "priority_confidence": intelligence.priority_confidence,
        "priority_reason": intelligence.priority_reason,
        "module": intelligence.module,
        "module_confidence": intelligence.module_confidence,
        "domain": intelligence.domain,
        "intent": intelligence.intent,
        "assigned_team": intelligence.assigned_team,
        "assigned_team_confidence": intelligence.assigned_team_confidence,
        "review_required": intelligence.review_required,
        "routing": intelligence.routing,
        "cleaning_pass": cleaning_pass,
        "issue_pass": issue_pass,
        "priority_pass": priority_pass,
        "routing_pass": routing_pass,
        "confidence_pass": confidence_pass,
        "overall_pass": overall_pass,
        "failure_reason": "; ".join(failures),
    }


def write_report(rows: list[dict[str, object]]) -> None:
    VALIDATION_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with REPORT_PATH.open("w", newline="", encoding="utf-8") as report_file:
        writer = csv.DictWriter(report_file, fieldnames=REPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def print_summary(rows: list[dict[str, object]]) -> None:
    total = len(rows)
    passed = sum(1 for row in rows if row["overall_pass"])
    failed = total - passed
    cleaning_failures = sum(1 for row in rows if not row["cleaning_pass"])
    issue_failures = sum(1 for row in rows if not row["issue_pass"])
    priority_failures = sum(1 for row in rows if not row["priority_pass"])
    routing_failures = sum(1 for row in rows if not row["routing_pass"])
    confidence_failures = sum(1 for row in rows if not row["confidence_pass"])

    print(f"total tested: {total}")
    print(f"passed: {passed}")
    print(f"failed: {failed}")
    print(f"cleaning failures: {cleaning_failures}")
    print(f"issue extraction failures: {issue_failures}")
    print(f"priority failures: {priority_failures}")
    print(f"routing failures: {routing_failures}")
    print(f"confidence failures: {confidence_failures}")
    print(f"report: {REPORT_PATH}")


def main() -> int:
    samples = load_samples()
    rows = [validate_sample(sample) for sample in samples]
    write_report(rows)
    print_summary(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
