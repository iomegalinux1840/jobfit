from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .models import JobPosting, QuerySpec


class JobSpyUnavailable(RuntimeError):
    pass


SITE_LABELS = {
    "linkedin": "LinkedIn",
    "indeed": "Indeed",
    "google": "Google Jobs",
    "zip_recruiter": "ZipRecruiter",
    "glassdoor": "Glassdoor",
    "bayt": "Bayt",
    "bdjobs": "BDJobs",
    "naukri": "Naukri",
}

_AMOUNT_PATTERN = re.compile(
    r"(?<![A-Za-z])(?P<number>[0-9]+,[0-9]{1,2}|[0-9]{1,3}(?:[ ,\u00a0\u202f][0-9]{3})+|[0-9]+(?:\.[0-9]+)?)(?P<suffix>\s*[kK])?(?![A-Za-z0-9])"
)

_SALARY_CONTEXT_PATTERN = re.compile(
    r"(?ix)"
    r"salary|salaries|compensation|pay\s+range|pay\s+rate|wage|wages|"
    r"remuneration|salaire|rémunération|taux\s+horaire|rémunération"
)
_SALARY_CURRENCY_PATTERN = re.compile(
    r"(?ix)\b(?:cad|can|ca|usd|eur|gbp)\b|(?:c|ca|us)\$|[$€£]"
)
_SALARY_INTERVAL_PATTERNS = (
    (
        "hourly",
        re.compile(
            r"(?ix)\b(?:hourly|per\s+hour|/\s*h(?:our)?s?|hr\.?|heure?s?|horaire|par\s+heure|/\s*heure)\b"
        ),
    ),
    (
        "monthly",
        re.compile(
            r"(?ix)\b(?:monthly|per\s+month|/\s*months?|month|mensuel(?:le)?|par\s+mois|/\s*mois)\b"
        ),
    ),
    (
        "weekly",
        re.compile(
            r"(?ix)\b(?:weekly|per\s+week|/\s*weeks?|week|hebdomadaire|par\s+semaine|/\s*semaine)\b"
        ),
    ),
    (
        "daily",
        re.compile(
            r"(?ix)\b(?:daily|per\s+day|/\s*days?|day|quotidien(?:ne)?|par\s+jour|/\s*jour)\b"
        ),
    ),
    (
        "yearly",
        re.compile(
            r"(?ix)\b(?:annual(?:ly)?|yearly|per\s+year|/\s*years?|yr\.?|"
            r"annuel(?:le)?|annuellement|par\s+année|par\s+an|/\s*an(?:née)?)\b"
        ),
    ),
)
_SALARY_RANGE_PATTERN = re.compile(
    r"(?i)(?:-|–|—|\bto\b|\bà\b|\bbetween\b.{0,40}\band\b)"
)


def _text(value: object) -> str:
    if value is None:
        return ""
    try:
        if isinstance(value, float) and math.isnan(value):
            return ""
    except TypeError:
        pass
    text = str(value).strip()
    return "" if text.casefold() in {"nan", "none", "nat", "<na>"} else text


def _amount_match_value(match: re.Match[str]) -> float:
    number = (
        match.group("number")
        .replace(" ", "")
        .replace("\u00a0", "")
        .replace("\u202f", "")
    )
    if "," in number and len(number.rsplit(",", 1)[1]) <= 2:
        amount = float(number.replace(",", "."))
    else:
        amount = float(number.replace(",", ""))
    return amount * 1000 if match.group("suffix") else amount


def _number(value: object) -> float | None:
    text = _text(value).replace("$", "").replace("€", "").replace("£", "")
    if not text:
        return None
    match = _AMOUNT_PATTERN.search(text)
    if not match:
        return None
    return _amount_match_value(match)


def _numbers(value: object) -> list[float]:
    text = _text(value).replace("$", "").replace("€", "").replace("£", "")
    values = []
    for match in _AMOUNT_PATTERN.finditer(text):
        values.append(_amount_match_value(match))
    return values


def _first_text(row: dict, *keys: str) -> str:
    for key in keys:
        value = _text(row.get(key))
        if value:
            return value
    return ""


def _remote(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    text = _text(value).casefold()
    if text in {"true", "1", "yes", "y", "remote", "work from home"}:
        return True
    if text in {"false", "0", "no", "n", "onsite", "on-site", "on site"}:
        return False
    return None


def _salary_currency(value: str) -> str:
    tokens = [
        match.group(0).casefold() for match in _SALARY_CURRENCY_PATTERN.finditer(value)
    ]
    for token in tokens:
        if token in {"cad", "can", "ca", "c$", "ca$"}:
            return "CAD"
        if token in {"usd", "us$"}:
            return "USD"
        if token == "eur":
            return "EUR"
        if token == "gbp":
            return "GBP"
    if not tokens:
        return ""
    token = tokens[0]
    return {"€": "EUR", "£": "GBP"}.get(token, "")


def _salary_interval_from_text(value: str) -> str:
    for interval, pattern in _SALARY_INTERVAL_PATTERNS:
        if pattern.search(value):
            return interval
    return ""


def _salary_values_from_line(value: str) -> list[float]:
    """Return likely pay values from one salary-related description line.

    Description text contains many unrelated numbers (years of experience,
    dates, vacancy counts). We only accept small hourly-like values when they
    are explicitly currency-marked; annual and larger values must be at least
    10,000 unless currency-marked. This keeps the fallback useful without
    treating every number as pay.
    """

    interval = _salary_interval_from_text(value)
    minimum_by_interval = {
        "": 10000,
        "yearly": 10000,
        "monthly": 1000,
        "weekly": 200,
        "daily": 50,
        "hourly": 10,
    }
    values = []
    for match in _AMOUNT_PATTERN.finditer(value):
        amount = _amount_match_value(match)
        before = value[max(0, match.start() - 8) : match.start()]
        after = value[match.end() : min(len(value), match.end() + 8)]
        explicitly_marked = bool(
            re.search(r"(?ix)(?:cad|usd|eur|gbp|c\$|ca\$|us\$|[$€£])\s*$", before)
            or re.search(r"(?ix)^\s*(?:cad|usd|eur|gbp\b|[$€£])", after)
        )
        if (
            amount >= minimum_by_interval[interval]
            or explicitly_marked
            or (interval == "hourly" and amount <= 350)
        ):
            values.append(amount)
    if len(values) > 1 and not _SALARY_RANGE_PATTERN.search(value):
        return values[:1]
    return values


def _salary_from_description(row: dict) -> tuple[float | None, float | None, str, str]:
    description = _first_text(row, "description", "job_description", "summary")
    if not description:
        return None, None, "", ""

    # Salary disclosures are normally a single markdown/HTML line. Looking
    # line-by-line avoids accidentally pairing a salary keyword with unrelated
    # numbers from a distant paragraph.
    for raw_line in re.split(r"[\r\n]+", description):
        line = re.sub(r"[\\*_`]|&nbsp;", "", raw_line).strip()
        has_salary_context = bool(_SALARY_CONTEXT_PATTERN.search(line))
        has_explicit_interval = bool(_salary_interval_from_text(line))
        has_currency = bool(_SALARY_CURRENCY_PATTERN.search(line))
        if (
            not line
            or not has_salary_context
            and not (has_explicit_interval and has_currency)
        ):
            continue
        values = _salary_values_from_line(line)
        if not values:
            continue
        return (
            values[0],
            values[1] if len(values) > 1 else None,
            _salary_interval_from_text(line),
            _salary_currency(line),
        )
    return None, None, "", ""


def _salary(row: dict) -> tuple[float | None, float | None, str, str, str]:
    minimum = _number(
        row.get("min_amount")
        or row.get("min_salary")
        or row.get("salary_min")
        or row.get("compensation_min")
    )
    maximum = _number(
        row.get("max_amount")
        or row.get("max_salary")
        or row.get("salary_max")
        or row.get("compensation_max")
    )
    structured = minimum is not None or maximum is not None
    salary_text = _first_text(
        row, "salary", "salary_range", "salary_description", "compensation"
    )
    values = _numbers(salary_text)
    if minimum is None and values:
        minimum = values[0]
    if maximum is None and len(values) > 1:
        maximum = values[1]
    text_source = bool(values)
    description_min, description_max, description_interval, description_currency = (
        _salary_from_description(row)
    )
    if minimum is None:
        minimum = description_min
    if maximum is None:
        maximum = description_max
    interval = _first_text(row, "interval", "salary_interval", "pay_period")
    if not interval:
        interval = description_interval
    currency = _first_text(row, "currency", "salary_currency") or description_currency
    if structured and text_source:
        source = "structured+salary_text"
    elif structured:
        source = "structured"
    elif text_source:
        source = "salary_text"
    elif description_min is not None or description_max is not None:
        source = "description"
    else:
        source = ""
    return minimum, maximum, interval, currency, source


def _annualize(
    minimum: float | None, maximum: float | None, interval: str
) -> tuple[float | None, float | None, str]:
    multipliers = {
        "hour": 2080,
        "hourly": 2080,
        "day": 260,
        "daily": 260,
        "week": 52,
        "weekly": 52,
        "month": 12,
        "monthly": 12,
        "year": 1,
        "yearly": 1,
        "annual": 1,
        "annually": 1,
    }
    factor = multipliers.get(interval.casefold().strip())
    if factor is None:
        return minimum, maximum, interval
    return (
        None if minimum is None else minimum * factor,
        None if maximum is None else maximum * factor,
        "yearly",
    )


def from_mapping(
    row: dict, source: str = "fixture", enforce_annual_salary: bool = False
) -> JobPosting:
    if not isinstance(row, dict):
        raise TypeError("Job rows must be JSON objects")
    title = _first_text(row, "title", "job_title")
    company = _first_text(
        row, "company", "company_name", "employer", "organization", "employer_name"
    )
    location = _first_text(row, "location")
    if not location:
        location = ", ".join(
            part
            for part in (
                _first_text(row, "city"),
                _first_text(row, "state", "region", "province"),
                _first_text(row, "country", "country_code"),
            )
            if part
        )
    url = _first_text(row, "job_url", "url", "link")
    stable = url or "|".join(_text(value) for value in (title, company, location))
    job_id = f"{source}:{stable.lower()}"
    (
        salary_min,
        salary_max,
        salary_interval,
        detected_currency,
        detected_salary_source,
    ) = _salary(row)
    if enforce_annual_salary:
        salary_min, salary_max, salary_interval = _annualize(
            salary_min, salary_max, salary_interval
        )
    return JobPosting(
        job_id=job_id,
        source=source,
        title=title,
        company=company,
        url=url,
        location=location,
        description=_first_text(row, "description", "job_description", "summary"),
        date_posted=_first_text(row, "date_posted", "posted_date"),
        is_remote=_remote(row.get("is_remote")),
        salary_min=salary_min,
        salary_max=salary_max,
        salary_interval=salary_interval,
        salary_currency=(
            _first_text(row, "currency", "salary_currency") or detected_currency
        ),
        salary_source=(
            _first_text(row, "salary_source", "salary_basis") or detected_salary_source
        ),
        easy_apply=_remote(row.get("easy_apply")),
        latitude=_number(row.get("latitude") or row.get("lat")),
        longitude=_number(row.get("longitude") or row.get("lon") or row.get("lng")),
        raw=row,
    )


class FixtureSource:
    def __init__(self, path: str, enforce_annual_salary: bool = True):
        self.path = Path(path)
        self.enforce_annual_salary = enforce_annual_salary

    def fetch(
        self,
        queries: Sequence[QuerySpec],
        progress: Callable[[str], None] | None = None,
    ) -> list[JobPosting]:
        rows = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise TypeError("Fixture must contain a JSON array")
        if progress:
            progress(f"SOURCE stage: fixture loaded {len(rows)} jobs")
        return [
            from_mapping(
                row,
                source="fixture",
                enforce_annual_salary=self.enforce_annual_salary,
            )
            for row in rows
        ]


class JobSpySource:
    def __init__(
        self,
        sites: Sequence[str],
        hours_old: int = 168,
        results_wanted: int = 25,
        country_indeed: str = "Canada",
        max_workers: int = 6,
        easy_apply_only: bool = False,
        enforce_annual_salary: bool = True,
        linkedin_fetch_description: bool = True,
    ):
        self.sites = list(sites)
        self.hours_old = hours_old
        self.results_wanted = results_wanted
        self.country_indeed = country_indeed
        self.max_workers = max(1, max_workers)
        self.easy_apply_only = easy_apply_only
        self.enforce_annual_salary = enforce_annual_salary
        self.linkedin_fetch_description = linkedin_fetch_description

    def fetch(
        self,
        queries: Sequence[QuerySpec],
        progress: Callable[[str], None] | None = None,
    ) -> list[JobPosting]:
        try:
            from jobspy import scrape_jobs
        except ImportError as exc:
            raise JobSpyUnavailable(
                "Install the project dependencies to use live JobSpy collection"
            ) from exc

        query_list = list(queries)
        if not query_list:
            return []
        workers = min(self.max_workers, len(query_list))
        if progress:
            progress(
                f"SOURCE stage: JobSpy running {len(query_list)} queries in parallel "
                f"({workers} workers)"
            )

        def fetch_one(index: int, query: QuerySpec) -> tuple[int, list[JobPosting]]:
            kwargs = {
                "site_name": self.sites,
                "search_term": query.search_term,
                "location": query.location,
                "results_wanted": self.results_wanted,
                "country_indeed": self.country_indeed,
                "verbose": 0,
                "enforce_annual_salary": self.enforce_annual_salary,
                "linkedin_fetch_description": self.linkedin_fetch_description,
            }
            if self.easy_apply_only:
                kwargs["easy_apply"] = True
            # JobSpy documents that Indeed/LinkedIn do not accept every
            # freshness/filter combination in one request.
            if query.is_remote is None:
                kwargs["hours_old"] = self.hours_old
            if query.is_remote is not None:
                kwargs["is_remote"] = query.is_remote
            try:
                frame = scrape_jobs(**kwargs)
            except Exception as exc:
                raise RuntimeError(
                    f"JobSpy query failed for '{query.search_term}': {exc}"
                ) from exc
            jobs = []
            for row in frame.to_dict(orient="records"):
                source = f"jobspy:{_first_text(row, 'site', 'site_name') or 'unknown'}"
                jobs.append(
                    from_mapping(
                        row,
                        source=source,
                        enforce_annual_salary=self.enforce_annual_salary,
                    )
                )
            return index, jobs

        collected: list[JobPosting] = []
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(fetch_one, index, query)
                for index, query in enumerate(query_list, start=1)
            ]
            for future in as_completed(futures):
                index, jobs = future.result()
                collected.extend(jobs)
                if progress:
                    progress(
                        f"SOURCE stage: JobSpy query {index}/{len(query_list)} complete — "
                        f"{len(jobs)} jobs"
                    )
        return collected


def deduplicate_jobs(jobs: Iterable[JobPosting]) -> list[JobPosting]:
    unique = {}
    for job in jobs:
        key = job.url.lower() if job.url else job.job_id.lower()
        if key not in unique or len(job.description) > len(unique[key].description):
            unique[key] = job
    return list(unique.values())
