import re


GREETING_PATTERNS = [
    r"^hi\b.*",
    r"^hello\b.*",
    r"^dear\b.*",
    r"^good morning\b.*",
    r"^good afternoon\b.*",
    r"^good evening\b.*",
]

CLOSING_PATTERNS = [
    r"^thanks[,.!]*$",
    r"^thank you[,.!]*$",
    r"^regards[,.!]*$",
    r"^best regards[,.!]*$",
    r"^kind regards[,.!]*$",
    r"^sincerely[,.!]*$",
]

QUOTE_HEADER_PATTERNS = [
    r"^from:\s.*",
    r"^sent:\s.*",
    r"^to:\s.*",
    r"^cc:\s.*",
    r"^subject:\s.*",
    r"^on .* wrote:$",
    r"^-{2,}\s*original message\s*-{2,}",
    r"^forwarded message",
]

DISCLAIMER_KEYWORDS = [
    "confidentiality notice",
    "this email and any attachments",
    "intended recipient",
    "privileged and confidential",
    "please consider the environment",
    "disclaimer",
]


def clean_business_body(raw_body: str) -> str:
    """
    Clean Outlook email reply body.
    Preserve business meaning.
    Remove greetings, signatures, disclaimers, contacts, and quoted headers.
    """

    if not raw_body:
        return ""

    text = raw_body.replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)

    lines = [line.strip() for line in text.split("\n")]
    cleaned_lines = []

    for line in lines:

        if not line:
            continue

        lower = line.lower().strip()

        # Stop at quoted reply headers
        if any(re.match(pattern, lower, re.IGNORECASE)
               for pattern in QUOTE_HEADER_PATTERNS):
            break

        # Stop at disclaimer
        if any(keyword in lower for keyword in DISCLAIMER_KEYWORDS):
            break

        # Remove greetings
        if any(re.match(pattern, lower, re.IGNORECASE)
               for pattern in GREETING_PATTERNS):
            continue

        # Remove closings
        if any(re.match(pattern, lower, re.IGNORECASE)
               for pattern in CLOSING_PATTERNS):
            continue

        # Remove email addresses
        line = re.sub(
            r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
            "",
            line,
            flags=re.IGNORECASE
        )

        # Remove phone numbers
        line = re.sub(
            r"\+?\d[\d\s().-]{7,}\d",
            "",
            line
        )

        # Remove URLs
        line = re.sub(r"https?://[^\s]+", "", line)
        line = re.sub(r"www\.[^\s]+", "", line)

        line = line.strip(" -|:")

        if line:
            cleaned_lines.append(line)

    cleaned_text = " ".join(cleaned_lines)
    cleaned_text = re.sub(r"\s+", " ", cleaned_text).strip()

    return cleaned_text