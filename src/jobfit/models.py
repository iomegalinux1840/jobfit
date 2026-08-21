from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Profile:
    """Normalized resume signals used by the query planner and scorer."""

    name: str = "Candidate"
    summary: str = ""
    target_titles: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    remote_preference: str = "any"
    excluded_terms: list[str] = field(default_factory=list)
    years_experience: int | None = None


@dataclass
class QuerySpec:
    search_term: str
    location: str | None = None
    is_remote: bool | None = None


@dataclass
class ScanSettings:
    """Persisted JobSpy and location settings used by the TUI scan."""

    sites: list[str] = field(default_factory=lambda: ["linkedin", "indeed", "google"])
    location: str = ""
    origin: str = ""
    max_distance_km: float | None = None
    hours_old: int = 168
    results_wanted: int = 25
    query_workers: int = 6
    country_indeed: str = "Canada"
    easy_apply_only: bool = False
    enforce_annual_salary: bool = True
    linkedin_fetch_description: bool = True


@dataclass
class JobPosting:
    job_id: str
    source: str
    title: str
    company: str
    url: str = ""
    location: str = ""
    description: str = ""
    date_posted: str = ""
    is_remote: bool | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_interval: str = ""
    salary_currency: str = ""
    salary_source: str = ""
    easy_apply: bool | None = None
    latitude: float | None = None
    longitude: float | None = None
    distance_km: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class FitResult:
    job: JobPosting
    score: int
    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    matched_domains: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    hard_exclusion: str | None = None
    change_status: str = ""
    company_saved: bool = False


@dataclass
class RunSummary:
    run_id: int
    collected: int
    unique: int
    new: int
    updated: int
    unchanged: int
    diff: list[FitResult] = field(default_factory=list)
