"""Deterministic, local-only redaction before a resume reaches a cloud LLM.

This is a privacy aid, not a guarantee of anonymization. The redactor removes
high-confidence contact and identity signals while preserving job-search
content such as skills, titles, employers, and work locations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CLOUD_PROVIDERS = frozenset({"openai", "openrouter", "anthropic", "xai", "gemini"})

_EMAIL_RE = re.compile(
    r"(?<![\w.+-])[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}(?![\w.-])",
    re.IGNORECASE,
)
_NORTH_AMERICAN_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]\d{3}[\s.-]\d{4}(?!\d)"
)
_FRENCH_PHONE_RE = re.compile(r"(?<!\d)(?:0\d(?:[ .-]?\d{2}){4})(?!\d)")
_INTERNATIONAL_PHONE_RE = re.compile(r"(?<!\d)\+\d{1,3}(?:[ .-]?\d{2,4}){2,5}(?!\d)")
_CANADA_POSTAL_RE = re.compile(
    r"\b[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z][ -]?\d[ABCEGHJ-NPRSTV-Z]\d\b",
    re.IGNORECASE,
)
_US_ZIP_RE = re.compile(r"\b\d{5}(?:-\d{4})?\b")

_CANADA_SIN_RE = re.compile(r"\b\d{3}[ -]?\d{3}[ -]?\d{3}\b")
_US_SSN_RE = re.compile(r"\b\d{3}[ -]?\d{2}[ -]?\d{4}\b")
_FRENCH_NIR_RE = re.compile(
    r"(?<!\d)[12]\s?\d{2}\s?(?:0[1-9]|1[0-2])\s?(?:\d{2}|2A|2B)"
    r"\s?\d{3}\s?\d{3}\s?\d{2}(?!\d)",
    re.IGNORECASE,
)

_SECTION_RE = re.compile(
    r"^\s*(?:"
    r"profile|professional summary|summary|experience|work experience|employment|"
    r"skills|technical skills|education|projects|certifications|references|"
    r"profil|résumé|resume|expérience|expériences|emploi|compétences|"
    r"formation|éducation|projets|certifications|références"
    r")\s*:?[\s—-]*$",
    re.IGNORECASE,
)
_NAME_STOPWORDS = {
    "about",
    "curriculum",
    "cv",
    "experience",
    "expérience",
    "profile",
    "profil",
    "resume",
    "résumé",
    "skills",
    "compétences",
    "summary",
    "sommaire",
}
_NAME_TOKEN_RE = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]*$")

_CONTACT_LABEL_RE = re.compile(
    r"\b(?:"
    r"address|home address|residential address|street|rue|adresse|domicile|"
    r"postal address|adresse postale"
    r")\b\s*(?::|#|-)?",
    re.IGNORECASE,
)
_IDENTITY_LABEL_RE = re.compile(
    r"\b(?:"
    r"passport(?:\s+(?:no|number|#))?|passport number|"
    r"driver(?:'s|’s)?\s+(?:licen[cs]e)(?:\s+(?:no|number|#))?|"
    r"permis de conduire(?:\s+(?:no|numéro|n°|#))?|"
    r"national\s+id(?:entity)?(?:\s+(?:no|number|#))?|"
    r"carte nationale d'identité|carte d'identité|"
    r"document\s+(?:id|number)|numéro\s+de\s+(?:passeport|document)|"
    r"id(?:entification)?\s+(?:no|number|numéro|n°)"
    r")\b\s*(?::|#|-)?",
    re.IGNORECASE,
)
_SOCIAL_LABEL_RE = re.compile(
    r"\b(?:"
    r"sin|ssn|nas|social\s+insurance(?:\s+number)?|social\s+security(?:\s+number)?|"
    r"numéro\s+d['’]assurance\s+sociale|numéro\s+de\s+sécurité\s+sociale|"
    r"n[°o]?\s+de\s+sécurité\s+sociale"
    r")\b\s*(?::|#|-)?",
    re.IGNORECASE,
)
_PERSONAL_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:linkedin\.com/in|github\.com)/[^\s)]+",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RedactionResult:
    """Redacted text and aggregate categories only; never stores matched values."""

    text: str
    counts: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def summary(self) -> str:
        if not self.counts:
            return "no high-confidence patterns found (best effort)"
        labels = ", ".join(
            f"{count} {category.replace('_', ' ')}"
            for category, count in self.counts.items()
        )
        return f"{labels} redacted (best effort)"


def _add_count(counts: dict[str, int], category: str, amount: int = 1) -> None:
    counts[category] = counts.get(category, 0) + amount


def _replace_matches(
    text: str,
    pattern: re.Pattern[str],
    placeholder: str,
    counts: dict[str, int],
    category: str,
) -> str:
    matches = list(pattern.finditer(text))
    if matches:
        _add_count(counts, category, len(matches))
        text = pattern.sub(placeholder, text)
    return text


def _looks_like_name(line: str) -> bool:
    candidate = re.split(r"\s*(?:[|•·]|\s-\s|—)\s*", line.strip(), maxsplit=1)[0]
    candidate = re.sub(r"\s+", " ", candidate)
    tokens = candidate.split(" ")
    if not 2 <= len(tokens) <= 4:
        return False
    if any(token.casefold() in _NAME_STOPWORDS for token in tokens):
        return False
    return all(_NAME_TOKEN_RE.fullmatch(token) for token in tokens)


def _header_name(text: str) -> str | None:
    for line in text.splitlines()[:8]:
        stripped = line.strip()
        if not stripped:
            continue
        if _SECTION_RE.match(stripped):
            return None
        candidate_line = _EMAIL_RE.sub("", stripped)
        candidate_line = _PERSONAL_URL_RE.sub("", candidate_line)
        candidate_line = _NORTH_AMERICAN_PHONE_RE.sub("", candidate_line)
        candidate_line = _FRENCH_PHONE_RE.sub("", candidate_line)
        candidate_line = _INTERNATIONAL_PHONE_RE.sub("", candidate_line)
        candidate = re.split(r"\s*(?:[|•·]|\s-\s|—)\s*", candidate_line, maxsplit=1)[
            0
        ].strip()
        if _looks_like_name(candidate):
            return candidate
        return None
    return None


def _replace_name(text: str, counts: dict[str, int]) -> str:
    name = _header_name(text)
    if not name:
        return text
    pattern = re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", re.IGNORECASE)
    matches = list(pattern.finditer(text))
    if not matches:
        return text
    _add_count(counts, "name", 1)
    return pattern.sub("[REDACTED_NAME]", text)


def _redact_labeled_lines(
    text: str,
    pattern: re.Pattern[str],
    placeholder: str,
    counts: dict[str, int],
    category: str,
) -> str:
    lines = text.splitlines(keepends=True)
    redacted = []
    for line in lines:
        if pattern.search(line):
            _add_count(counts, category)
            newline = "\n" if line.endswith("\n") else ""
            redacted.append(placeholder + newline)
        else:
            redacted.append(line)
    return "".join(redacted)


def _luhn_valid(digits: str) -> bool:
    total = 0
    for index, character in enumerate(reversed(digits)):
        value = int(character)
        if index % 2:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _redact_unlabelled_social_ids(text: str, counts: dict[str, int]) -> str:
    def replace_sin(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if _luhn_valid(digits):
            _add_count(counts, "social_id")
            return "[REDACTED_SOCIAL_ID]"
        return match.group(0)

    text = _CANADA_SIN_RE.sub(replace_sin, text)

    def replace_ssn(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if digits[:3] in {"000", "666"} or digits[:3].startswith("9"):
            return match.group(0)
        if digits[3:5] == "00" or digits[5:] == "0000":
            return match.group(0)
        _add_count(counts, "social_id")
        return "[REDACTED_SOCIAL_ID]"

    text = _US_SSN_RE.sub(replace_ssn, text)
    return _replace_matches(
        text, _FRENCH_NIR_RE, "[REDACTED_SOCIAL_ID]", counts, "social_id"
    )


def redact_for_cloud(text: str) -> RedactionResult:
    """Redact high-confidence English/French PII without using an LLM.

    The function intentionally redacts labelled identity/address lines as a
    whole. It does not guess at every person or city name in the work history.
    """
    counts: dict[str, int] = {}
    redacted = text
    redacted = _replace_name(redacted, counts)
    redacted = _replace_matches(
        redacted, _EMAIL_RE, "[REDACTED_EMAIL]", counts, "email"
    )
    redacted = _replace_matches(
        redacted,
        _NORTH_AMERICAN_PHONE_RE,
        "[REDACTED_PHONE]",
        counts,
        "phone",
    )
    redacted = _replace_matches(
        redacted, _FRENCH_PHONE_RE, "[REDACTED_PHONE]", counts, "phone"
    )
    redacted = _replace_matches(
        redacted,
        _INTERNATIONAL_PHONE_RE,
        "[REDACTED_PHONE]",
        counts,
        "phone",
    )
    redacted = _replace_matches(
        redacted, _PERSONAL_URL_RE, "[REDACTED_PERSONAL_URL]", counts, "personal_url"
    )
    redacted = _redact_labeled_lines(
        redacted,
        _SOCIAL_LABEL_RE,
        "[REDACTED_SOCIAL_ID]",
        counts,
        "social_id",
    )
    redacted = _redact_labeled_lines(
        redacted,
        _IDENTITY_LABEL_RE,
        "[REDACTED_ID]",
        counts,
        "identity_id",
    )
    redacted = _redact_labeled_lines(
        redacted,
        _CONTACT_LABEL_RE,
        "[REDACTED_ADDRESS]",
        counts,
        "address",
    )
    redacted = _redact_unlabelled_social_ids(redacted, counts)

    # Catch an unlabeled street/postal line only in the opening contact block.
    lines = redacted.splitlines(keepends=True)
    for index, line in enumerate(lines[:10]):
        if _CANADA_POSTAL_RE.search(line) or _US_ZIP_RE.search(line):
            if re.match(r"^\s*\d{1,6}\s+", line) and re.search(
                r"\b(?:street|st|road|rd|avenue|ave|rue|boulevard|blvd)\b",
                line,
                re.I,
            ):
                newline = "\n" if line.endswith("\n") else ""
                lines[index] = "[REDACTED_ADDRESS]" + newline
                _add_count(counts, "address")
    redacted = "".join(lines)
    return RedactionResult(redacted, counts)
