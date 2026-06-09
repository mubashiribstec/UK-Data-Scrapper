import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

UK_POSTCODE_RE = re.compile(r"[A-Z]{1,2}[0-9][0-9A-Z]?\s?[0-9][A-Z]{2}", re.IGNORECASE)
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
PHONE_UK_RE = re.compile(
    r"(?:\+44|0044|0)[\s\-.]?(?:\d[\s\-.]?){9,11}"
)

FAKE_EMAIL_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg", ".css", ".gif", ".ico", ".woff"}
NOREPLY_PATTERNS = re.compile(r"noreply|no-reply|donotreply|do-not-reply", re.I)
CURRENT_YEARS = {"2023", "2024", "2025", "2026", "2027"}


def clean_phone(raw: str) -> Optional[str]:
    """Validate and normalise a UK phone number."""
    if not raw:
        return None
    # Reject if contains decimal point (SVG coordinate artefact)
    if "." in raw and re.search(r"\d+\.\d+", raw):
        return None

    try:
        import phonenumbers
        num = phonenumbers.parse(raw, "GB")
        if not phonenumbers.is_valid_number(num):
            return None
        digits_only = re.sub(r"\D", "", raw)
        if len(digits_only) < 10:
            return None
        if digits_only in CURRENT_YEARS:
            return None
        return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
    except Exception:
        return None


def clean_email(raw: str) -> Optional[str]:
    """Validate and normalise an email address."""
    if not raw:
        return None
    raw = raw.strip().lower()
    _, _, ext = raw.rpartition(".")
    if ext in {s.lstrip(".") for s in FAKE_EMAIL_SUFFIXES}:
        return None
    if NOREPLY_PATTERNS.search(raw):
        return None
    try:
        from email_validator import validate_email, EmailNotValidError
        valid = validate_email(raw, check_deliverability=False)
        return valid.normalized
    except Exception:
        # Fallback: simple format check
        if EMAIL_RE.match(raw) and "@" in raw:
            return raw
        return None


def extract_phones(text: str) -> list[str]:
    """Extract and clean UK phone numbers from free text."""
    if not text:
        return []
    raw_matches = PHONE_UK_RE.findall(text)
    cleaned = []
    seen = set()
    for match in raw_matches:
        phone = clean_phone(match)
        if phone and phone not in seen:
            # Extra filter: reject if year-like sequence in digits
            digits = re.sub(r"\D", "", phone)
            if any(year in digits for year in ["2023", "2024", "2025", "2026"]):
                continue
            seen.add(phone)
            cleaned.append(phone)
    return cleaned


def extract_emails(text: str) -> list[str]:
    """Extract and clean email addresses from free text."""
    if not text:
        return []
    raw_matches = EMAIL_RE.findall(text)
    cleaned = []
    seen = set()
    for match in raw_matches:
        email = clean_email(match)
        if email and email not in seen:
            seen.add(email)
            cleaned.append(email)
    return cleaned


def parse_salary(text: str) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """Parse salary strings to (min, max, period)."""
    if not text:
        return None, None, None

    text_lower = text.lower().strip()

    if re.search(r"competitive|negotiable|depending|doe|tbc|tbf|market rate", text_lower):
        return None, None, None

    period = None
    if re.search(r"\bhour(ly)?\b|/hr\b|per hour", text_lower):
        period = "hourly"
    elif re.search(r"\byear\b|\bannum\b|annual|p\.a\.|per year", text_lower):
        period = "annual"

    amounts = re.findall(r"£([\d,]+(?:\.\d{1,2})?)", text)
    parsed = []
    for a in amounts:
        try:
            parsed.append(float(a.replace(",", "")))
        except ValueError:
            pass

    if not parsed:
        return None, None, period

    if len(parsed) == 1:
        if "up to" in text_lower:
            return None, parsed[0], period
        return parsed[0], parsed[0], period

    sal_min, sal_max = min(parsed), max(parsed)

    # Compute annual equivalent if hourly
    if period == "hourly":
        hourly_min = sal_min
        hourly_max = sal_max
        # Also expose annual equivalent as extra annotation (not stored separately here)
        _ = hourly_min * 37.5 * 52  # noqa

    return sal_min, sal_max, period


def parse_location(location_text: str) -> tuple[Optional[str], Optional[str]]:
    """Extract city and postcode from a location string."""
    if not location_text:
        return None, None

    postcode_match = UK_POSTCODE_RE.search(location_text)
    postcode = postcode_match.group().upper() if postcode_match else None

    # City: first part before comma
    clean = re.sub(r"\bUnited Kingdom\b|\bUK\b", "", location_text, flags=re.I)
    parts = clean.split(",")
    city = parts[0].strip() if parts else None
    if city and len(city) < 2:
        city = None

    return city, postcode


def sort_emails_by_priority(emails: list[str]) -> list[str]:
    """Sort emails: hr@ / recruitment@ first."""
    priority_patterns = re.compile(r"^(hr|recruitment|jobs|careers|talent|people)@", re.I)
    priority = [e for e in emails if priority_patterns.match(e)]
    rest = [e for e in emails if not priority_patterns.match(e)]
    return priority + rest
