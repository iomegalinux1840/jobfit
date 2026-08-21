from __future__ import annotations

import re
from collections.abc import Iterable

from .agent import SKILL_ALIASES
from .models import FitResult, JobPosting, Profile

SCORE_VERSION = "0.1"


def _has(text: str, aliases: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(alias.lower() in lowered for alias in aliases)


def _ratio(count: int, total: int) -> float:
    return count / float(max(1, total))


def score_job(profile: Profile, job: JobPosting) -> FitResult:
    text = f"{job.title} {job.description} {job.company}"
    lowered = text.lower()
    for excluded in profile.excluded_terms:
        if excluded.lower() in lowered:
            return FitResult(
                job=job,
                score=0,
                reasons=[f"Excluded term: {excluded}"],
                hard_exclusion=excluded,
            )

    matched_skills = [
        skill
        for skill in profile.skills
        if _has(text, SKILL_ALIASES.get(skill, [skill]))
    ]
    missing_skills = [skill for skill in profile.skills if skill not in matched_skills]
    matched_domains = [
        domain for domain in profile.domains if domain.lower() in lowered
    ]

    skill_points = (
        35.0 * _ratio(len(matched_skills), len(profile.skills))
        if profile.skills
        else 0.0
    )
    domain_points = (
        15.0 * _ratio(len(matched_domains), len(profile.domains))
        if profile.domains
        else 7.5
    )
    title_points = (
        20.0
        if any(
            word in job.title.lower()
            for word in ("ai", "automation", "machine learning", "data", "procurement")
        )
        else 8.0
    )

    location_points = 0.0
    if not profile.locations or any(
        location.lower() in job.location.lower() for location in profile.locations
    ):
        location_points = 15.0
    elif job.is_remote or "remote" in job.location.lower() or "remote" in lowered:
        location_points = 12.0
    else:
        location_points = 4.0

    evidence_points = 15.0 if len(job.description) >= 250 else 8.0
    score = max(
        0,
        min(
            100,
            round(
                skill_points
                + domain_points
                + title_points
                + location_points
                + evidence_points
            ),
        ),
    )
    reasons = []
    if matched_skills:
        reasons.append(f"Matches: {', '.join(matched_skills[:6])}")
    if matched_domains:
        reasons.append(f"Domain: {', '.join(matched_domains[:4])}")
    if missing_skills:
        reasons.append(f"Review missing: {', '.join(missing_skills[:5])}")
    if re.search(r"\b(senior|lead|principal|manager|director)\b", job.title.lower()):
        reasons.append("Experienced-level title")
    return FitResult(
        job=job,
        score=score,
        matched_skills=matched_skills,
        missing_skills=missing_skills,
        matched_domains=matched_domains,
        reasons=reasons,
    )
