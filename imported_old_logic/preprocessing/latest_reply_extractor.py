import re
from email_reply_parser import EmailReplyParser


HEADER_PATTERNS = [
    r"^from:",
    r"^sent:",
    r"^date:",
    r"^to:",
    r"^cc:",
    r"^subject:",
    r"^-+\s*forwarded message\s*-+",
]

SIGNATURE_STARTERS = [
    "thanks",
    "thank you",
    "regards",
    "best regards",
    "kind regards",
    "stay inspired",
]

NOISE_LINES = [
    "private information",
    "attention: this email came from an external source",
]


def is_header_line(line: str) -> bool:
    text = line.strip().lower()
    return any(re.match(pattern, text, re.IGNORECASE) for pattern in HEADER_PATTERNS)


def is_signature_line(line: str) -> bool:
    text = line.strip().lower().strip(",.")
    return any(text.startswith(sig) for sig in SIGNATURE_STARTERS)


def is_noise_line(line: str) -> bool:
    text = line.strip().lower()
    return any(noise in text for noise in NOISE_LINES)


def is_separator_line(line: str) -> bool:
    text = line.strip()
    return bool(re.fullmatch(r"[_\-=\s]{5,}", text))


def is_meaningful_text(text: str) -> bool:
    cleaned = text.replace("_", "").replace("-", "").replace("=", "").strip()
    return len(cleaned) >= 3


def clean_lines(lines: list[str]) -> str:
    cleaned = []

    for line in lines:
        line = line.strip()

        if not line:
            continue

        if is_separator_line(line):
            continue

        if is_noise_line(line):
            continue

        if is_signature_line(line):
            break

        lower = line.lower()

        if "this email and any attachments" in lower:
            break

        if "the information transmitted by this e-mail" in lower:
            break

        line = re.sub(
            r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
            "",
            line,
            flags=re.IGNORECASE
        )

        line = re.sub(r"https?://[^\s]+", "", line)
        line = re.sub(r"www\.[^\s]+", "", line)
        line = re.sub(r"\+?\d[\d\s().-]{7,}\d", "", line)

        line = line.strip(" -|:_")

        if line:
            cleaned.append(line)

    return "\n".join(cleaned).strip()


def extract_first_forwarded_business_block(raw_body: str) -> str:
    lines = raw_body.replace("\r", "\n").split("\n")

    blocks = []
    current_block = []
    collecting_body = False

    for line in lines:
        stripped = line.strip()

        if not stripped:
            continue

        if is_header_line(stripped):
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
        cleaned = clean_lines(block)

        if is_meaningful_text(cleaned):
            return cleaned

    return ""


def extract_latest_reply(raw_body: str) -> str:
    """
    Extract latest meaningful reply from Outlook body.
    Supports normal replies and forwarded email chains.
    """

    if not raw_body:
        return ""

    parsed = EmailReplyParser.parse_reply(raw_body)
    parsed = clean_lines(parsed.splitlines())

    if is_meaningful_text(parsed):
        return parsed

    forwarded_content = extract_first_forwarded_business_block(raw_body)

    if is_meaningful_text(forwarded_content):
        return forwarded_content

    return ""