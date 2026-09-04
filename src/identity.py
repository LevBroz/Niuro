import re
import unicodedata

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")

CORPORATE_DOMAINS = {"fundo.com"}

TEST_EMAIL_LOCAL_RE = re.compile(r"^(test|qa|demo|dummy|sandbox)[._-]?\d*$")

PLACEHOLDER_NATIONAL_IDS = {"000-00-0000", "000000000", "111-11-1111", "123-45-6789"}

PLACEHOLDER_PHONES = {"5550000000", "0000000000", "1234567890"}


def strip_invisible(value):
    if value is None:
        return None
    cleaned = "".join(
        ch for ch in value if unicodedata.category(ch) != "Cf"
    )
    return cleaned


def normalize_national_id(value):
    value = strip_invisible(value)
    if value is None:
        return None
    digits = re.sub(r"\D", "", value)
    return digits or None


def normalize_email(value):
    value = strip_invisible(value)
    if value is None:
        return None
    return value.strip().lower() or None


def email_is_valid(value):
    value = normalize_email(value)
    return bool(value and EMAIL_RE.match(value))


def normalize_phone(value):
    value = strip_invisible(value)
    if value is None:
        return None
    digits = re.sub(r"\D", "", value)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits or None


def phone_is_valid(value):
    digits = normalize_phone(value)
    if digits is None or len(digits) != 10:
        return False
    if digits in PLACEHOLDER_PHONES:
        return False
    return digits[0] not in "01"


def national_id_is_usable(value):
    digits = normalize_national_id(value)
    if digits is None or len(digits) != 9:
        return False
    if value and value.strip() in PLACEHOLDER_NATIONAL_IDS:
        return False
    return len(set(digits)) > 1


def email_domain(value):
    value = normalize_email(value)
    if not value or "@" not in value:
        return None
    return value.rsplit("@", 1)[1]


def is_test_account(email, national_id, phone, has_real_activity):
    # a corporate address alone is not evidence: staff run genuine business
    if has_real_activity:
        return False, None

    normalized = normalize_email(email)
    domain = email_domain(normalized)
    local = normalized.rsplit("@", 1)[0] if normalized and "@" in normalized else None

    if domain in CORPORATE_DOMAINS and local and TEST_EMAIL_LOCAL_RE.match(local):
        return True, "corporate_domain_with_test_local_part"

    if national_id and national_id.strip() in PLACEHOLDER_NATIONAL_IDS:
        return True, "placeholder_national_id"

    if normalize_phone(phone) in PLACEHOLDER_PHONES and domain in CORPORATE_DOMAINS:
        return True, "placeholder_phone_on_corporate_domain"

    return False, None
