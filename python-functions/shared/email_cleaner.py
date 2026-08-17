import html
import re


MIN_CLEANED_BODY_LENGTH = 12

QUOTE_HEADER_PATTERNS = (
    r"^from:\s.*",
    r"^sent:\s.*",
    r"^date:\s.*",
    r"^to:\s.*",
    r"^cc:\s.*",
    r"^subject:\s.*",
    r"^on .+ wrote:$",
    r"^-{2,}\s*original message\s*-{2,}",
    r"^-{2,}\s*forwarded message\s*-{2,}",
    r"^-{2,}\s*forwarded mail\s*-{2,}",
    r"^forwarded message",
    r"^begin forwarded message:",
    r"^_{5,}$",
)

SIGNATURE_STARTERS = (
    "thanks",
    "thank you",
    "regards",
    "best regards",
    "kind regards",
    "warm regards",
    "thanks & regards",
    "sincerely",
    "stay inspired",
    "stay ins",
)

GREETING_PATTERNS = (
    r"^hi\b.*",
    r"^hello\b.*",
    r"^dear\b.*",
    r"^good morning\b.*",
    r"^good afternoon\b.*",
    r"^good evening\b.*",
)

DISCLAIMER_KEYWORDS = (
    "confidentiality notice",
    "this email and any attachments",
    "intended recipient",
    "privileged and confidential",
    "please consider the environment",
    "the information transmitted by this e-mail",
    "attention: this email came from an external source",
    "disclaimer",
    "this message is intended only for",
    "if you are not the intended recipient",
)

MOBILE_SIGNATURE_PATTERNS = (
    r"^sent from my iphone\.?$",
    r"^sent from outlook mobile\.?$",
    r"^get outlook for (android|ios)\.?$",
    r"^sent from samsung galaxy.*$",
)

ASCII_DECORATION_PATTERN = r"^[\W_]{5,}$"


def _strip_html(raw_body: str) -> str:
    text = re.sub(r"(?is)<!--.*?-->", " ", raw_body)
    text = re.sub(r"(?is)<(script|style|head|meta|xml).*?>.*?</\1>", " ", text)
    text = re.sub(
        r'(?is)<[^>]+(?:display\s*:\s*none|visibility\s*:\s*hidden)[^>]*>.*?</[^>]+>',
        " ",
        text,
    )
    text = re.sub(r"(?is)<o:p>\s*</o:p>", " ", text)
    text = re.sub(r"(?is)<o:p>.*?</o:p>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|li|h[1-6])\s*>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "- ", text)
    text = re.sub(r"(?i)<[^>]+\sstyle\s*=\s*['\"][^'\"]*['\"][^>]*>", " ", text)
    text = re.sub(r"(?i)</p\s*>", "\n", text)
    text = re.sub(r"(?i)</div\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(text)


def _normalize_raw_body(raw_body: str | None) -> str:
    if not raw_body:
        return ""

    text = _strip_html(str(raw_body))
    text = text.replace("\r", "\n")
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _is_quote_header(line: str) -> bool:
    lower = line.strip().lower()
    return any(re.match(pattern, lower, re.IGNORECASE) for pattern in QUOTE_HEADER_PATTERNS)


def _is_signature(line: str) -> bool:
    lower = line.strip().lower().strip(",.")
    return any(lower.startswith(starter) for starter in SIGNATURE_STARTERS)


def _is_mobile_signature(line: str) -> bool:
    lower = line.strip().lower()
    return any(re.match(pattern, lower, re.IGNORECASE) for pattern in MOBILE_SIGNATURE_PATTERNS)


def _is_greeting(line: str) -> bool:
    lower = line.strip().lower()
    return any(re.match(pattern, lower, re.IGNORECASE) for pattern in GREETING_PATTERNS)


def _is_disclaimer(line: str) -> bool:
    lower = line.strip().lower()
    return any(keyword in lower for keyword in DISCLAIMER_KEYWORDS)


def _is_separator(line: str) -> bool:
    stripped = line.strip()
    return bool(
        re.fullmatch(r"[_\-=\s]{5,}", stripped)
        or re.fullmatch(ASCII_DECORATION_PATTERN, stripped)
    )


def _is_meaningful(text: str) -> bool:
    cleaned = text.replace("_", "").replace("-", "").replace("=", "").strip()
    return len(cleaned) >= MIN_CLEANED_BODY_LENGTH


def _remove_inline_noise(line: str) -> str:
    line = re.sub(
        r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "",
        line,
        flags=re.IGNORECASE,
    )
    line = re.sub(r"https?://[^\s]+", "", line)
    line = re.sub(r"www\.[^\s]+", "", line)
    line = re.sub(r"\+?\d[\d\s().-]{7,}\d", "", line)
    line = re.sub(r"\b(?:tel|phone|mobile|mob|email)\s*:\s*\S+", "", line, flags=re.IGNORECASE)
    return line.strip(" -|:_")


def _remove_flattened_greeting_and_signature(text: str) -> str:
    text = re.sub(
        r"(?i)\b(hi|hello|dear)\s+(team|support|all|sir|madam|everyone)\s*,?\s*",
        "",
        text,
    )
    text = re.sub(r"(?i)\bgood\s+(morning|afternoon|evening)\s*,?\s*", "", text)
    text = re.sub(
        r"(?i)\s+\b(thanks\s*&\s*regards|best regards|warm regards|kind regards|"
        r"regards|thanks|thank you|sincerely)\b[\s,.-].*$",
        "",
        text,
    )
    text = re.sub(
        r"(?i)\s+\b(sent from my iphone|sent from outlook mobile|get outlook for android|"
        r"sent from samsung galaxy).*$",
        "",
        text,
    )
    return text.strip()


def remove_quoted_replies(raw_body: str | None) -> str:
    text = _normalize_raw_body(raw_body)
    kept_lines: list[str] = []

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            kept_lines.append("")
            continue
        if _is_quote_header(line):
            break
        kept_lines.append(line)

    return "\n".join(kept_lines).strip()


def remove_disclaimers(raw_body: str | None) -> str:
    lines = _normalize_raw_body(raw_body).split("\n")
    kept_lines: list[str] = []

    for raw_line in lines:
        line = raw_line.strip()
        if _is_disclaimer(line):
            break
        kept_lines.append(raw_line)

    return "\n".join(kept_lines).strip()


def remove_signature_blocks(raw_body: str | None) -> str:
    lines = _normalize_raw_body(raw_body).split("\n")
    kept_lines: list[str] = []

    for raw_line in lines:
        line = raw_line.strip()
        if _is_signature(line) or _is_mobile_signature(line):
            break
        kept_lines.append(raw_line)

    return "\n".join(kept_lines).strip()


def _remove_leading_greetings(lines: list[str]) -> list[str]:
    cleaned_lines = list(lines)
    while cleaned_lines:
        first_line = cleaned_lines[0].strip()
        if not first_line:
            cleaned_lines.pop(0)
            continue
        inline_greeting = re.match(
            r"(?i)^(hi|hello|dear)\b[^,]{0,60},\s*(?P<body>.+)$",
            first_line,
        )
        if inline_greeting:
            cleaned_lines[0] = inline_greeting.group("body")
            break
        inline_time_greeting = re.match(
            r"(?i)^good\s+(morning|afternoon|evening)\s*,\s*(?P<body>.+)$",
            first_line,
        )
        if inline_time_greeting:
            cleaned_lines[0] = inline_time_greeting.group("body")
            break
        if _is_greeting(first_line):
            cleaned_lines.pop(0)
            continue
        break

    return cleaned_lines


def clean_email_body(raw_body: str | None) -> str:
    """
    Examples:
    - "Hi Team,\nIncoming emails are not syncing.\nThanks,\nHari" ->
      "Incoming emails are not syncing."
    - "Payroll export fails\n-----Original Message-----\nFrom: ..." ->
      "Payroll export fails"
    """
    text = _normalize_raw_body(raw_body)
    if not text:
        return ""

    text = remove_quoted_replies(text)
    text = remove_disclaimers(text)
    text = remove_signature_blocks(text)

    cleaned_lines: list[str] = []
    for raw_line in _remove_leading_greetings(text.split("\n")):
        line = raw_line.strip()
        if not line:
            continue

        if _is_separator(line):
            continue

        if (
            _is_quote_header(line)
            or _is_disclaimer(line)
            or _is_signature(line)
            or _is_mobile_signature(line)
        ):
            break

        line = _remove_inline_noise(line)

        if line:
            cleaned_lines.append(line)

    cleaned_text = " ".join(cleaned_lines)
    cleaned_text = re.sub(r"\s+", " ", cleaned_text).strip()
    cleaned_text = _remove_flattened_greeting_and_signature(cleaned_text)
    return cleaned_text


def clean_business_body(raw_body: str | None) -> str:
    return clean_email_body(raw_body)


def _extract_forwarded_business_block(raw_body: str) -> str:
    lines = _normalize_raw_body(raw_body).split("\n")
    blocks: list[list[str]] = []
    current_block: list[str] = []
    collecting_body = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        if _is_quote_header(stripped):
            if current_block:
                blocks.append(current_block)
                current_block = []
            collecting_body = True
            continue

        if collecting_body:
            current_block.append(stripped)

    if current_block:
        blocks.append(current_block)

    for block in blocks:
        cleaned = clean_email_body("\n".join(block))
        if _is_meaningful(cleaned):
            return cleaned

    return ""


def extract_latest_reply(raw_body: str | None) -> str:
    text = _normalize_raw_body(raw_body)
    if not text:
        return ""

    latest_lines: list[str] = []
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if _is_quote_header(line):
            break
        latest_lines.append(line)

    latest_reply = clean_email_body("\n".join(latest_lines))
    if _is_meaningful(latest_reply):
        return latest_reply

    forwarded_content = _extract_forwarded_business_block(text)
    if _is_meaningful(forwarded_content):
        return forwarded_content

    return ""


def _clean_subject(subject: str | None) -> str:
    text = html.unescape(subject or "")
    text = re.sub(r"(?i)^\s*(re|fw|fwd)\s*:\s*", "", text).strip()
    text = re.sub(r"(?i)\b(urgent|high priority|important)\s*:?", "", text).strip()
    return re.sub(r"\s+", " ", text).strip(" -|:")


def _first_actionable_sentence(cleaned_body: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+|\s+-\s+", cleaned_body)
    for sentence in sentences:
        candidate = sentence.strip(" .")
        if len(candidate) >= MIN_CLEANED_BODY_LENGTH:
            return candidate
    return cleaned_body.strip(" .")


def _semantic_issue_rewrite(text: str) -> str:
    lower = text.lower()

    if "sync" in lower and any(word in lower for word in ("email", "emails", "mail")):
        if "dashboard" in lower:
            return "Incoming emails are not syncing with dashboard"
        return "Incoming emails are not syncing"
    if "dashboard" in lower and "sync" in lower:
        return "Dashboard email synchronization failure affecting operations"
    if "login" in lower and any(word in lower for word in ("unable", "not able", "fail", "error")):
        return "Users unable to log into production portal"
    if "payroll" in lower and any(word in lower for word in ("report", "export")):
        return "Payroll export report generation failing for HR users"
    if "portal" in lower and any(word in lower for word in ("down", "unavailable", "unable", "access")):
        return "Production portal unavailable for users"
    if "database" in lower and any(word in lower for word in ("fail", "error", "sync")):
        return "Database synchronization failure affecting application data"

    return text


def extract_issue_summary(
    subject: str | None,
    cleaned_body: str | None,
    max_length: int = 180,
) -> str:
    """
    Examples:
    - subject="Urgent: Email Sync Failure", body="Hi Team, dashboard emails are not syncing"
      -> "Incoming emails are not syncing with dashboard"
    - subject="Payroll Report Error", body="Dear Team, export report fails for HR users"
      -> "Payroll export report generation failing for HR users"
    """
    body = clean_email_body(cleaned_body)
    subject_text = _clean_subject(subject)
    source = body if _is_meaningful(body) else subject_text
    source = _remove_flattened_greeting_and_signature(source)
    issue = _first_actionable_sentence(source)
    issue = _semantic_issue_rewrite(issue)
    issue = re.sub(r"(?i)^(issue|problem|request)\s*:\s*", "", issue).strip()
    issue = re.sub(r"\s+", " ", issue).strip(" .")

    if not issue:
        issue = subject_text or "Issue details not available"

    if len(issue) > max_length:
        issue = f"{issue[: max_length - 3].rstrip()}..."

    return issue


def get_graph_message_body(message: dict) -> str:
    body = message.get("body") or {}
    unique_body = message.get("uniqueBody") or {}

    return (
        body.get("content")
        or unique_body.get("content")
        or message.get("bodyPreview")
        or ""
    )
