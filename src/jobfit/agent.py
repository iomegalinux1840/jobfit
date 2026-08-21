from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable

from .models import Profile, QuerySpec
from .providers import (
    ProviderError,
    complete_profile_json,
    provider_label,
    resolve_config,
)

SKILL_ALIASES: dict[str, list[str]] = {
    "python": ["python"],
    "llms": ["llm", "llms", "large language model", "generative ai"],
    "machine learning": ["machine learning", "scikit-learn", "sklearn"],
    "rag": ["rag", "retrieval augmented generation", "retrieval-augmented"],
    "fastapi": ["fastapi"],
    "docker": ["docker", "containerization", "containers"],
    "business central": ["business central", "dynamics 365"],
    "procurement": ["procurement", "purchasing", "approvisionnement"],
    "manufacturing": ["manufacturing", "manufacturier", "industrial"],
    "automation": ["automation", "automatisation", "workflow automation"],
    "postgresql": ["postgresql", "postgres"],
    "redis": ["redis"],
    "typescript": ["typescript"],
    "erp": ["erp", "enterprise resource planning"],
    "data systems": ["data systems", "data platform", "data engineering"],
}

TITLE_HINTS = [
    "ai engineer",
    "applied ai engineer",
    "automation engineer",
    "manufacturing ai",
    "ai and automation",
    "machine learning engineer",
    "data engineer",
    "procurement automation",
]


def _contains(text: str, aliases: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(alias in lowered for alias in aliases)


def heuristic_profile(resume_text: str) -> Profile:
    text = resume_text.strip()
    lowered = text.lower()
    skills = [
        name for name, aliases in SKILL_ALIASES.items() if _contains(lowered, aliases)
    ]
    titles = [hint.title() for hint in TITLE_HINTS if hint in lowered]
    if not titles:
        titles = ["AI & Automation Specialist"]

    domains = [
        name
        for name in (
            "manufacturing",
            "procurement",
            "automation",
            "data systems",
            "ERP",
        )
        if name.lower() in lowered
    ]
    years_match = re.search(r"(\d{1,2})\s*\+?\s*years", lowered)
    years = int(years_match.group(1)) if years_match else None
    name = "Candidate"
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if first_line and len(first_line.split()) <= 6 and not first_line.endswith(":"):
        name = first_line

    locations = []
    for location in ("Montreal", "Montréal", "Quebec", "Québec", "Toronto", "Remote"):
        if location.lower() in lowered:
            locations.append(location)

    return Profile(
        name=name,
        summary=text[:500],
        target_titles=titles[:6],
        skills=skills,
        domains=domains,
        locations=locations,
        remote_preference="remote" if "remote" in lowered else "any",
        years_experience=years,
    )


def profile_from_json(data: dict, fallback_text: str) -> Profile:
    fallback = heuristic_profile(fallback_text)

    def values(value: object, fallback_values: list[str]) -> list[str]:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            value = fallback_values
        cleaned = []
        seen = set()
        for item in value:
            text = " ".join(str(item).split()).strip()
            key = text.casefold()
            if text and key not in seen:
                cleaned.append(text)
                seen.add(key)
        return cleaned or list(fallback_values)

    def canonical_skills(value: object) -> list[str]:
        result = []
        for item in values(value, fallback.skills):
            lowered = item.casefold()
            canonical = next(
                (
                    name
                    for name, aliases in SKILL_ALIASES.items()
                    if lowered == name or any(alias in lowered for alias in aliases)
                ),
                lowered,
            )
            if canonical not in result:
                result.append(canonical)
        return sorted(result)[:12]

    return Profile(
        name=str(data.get("name") or fallback.name),
        summary=str(data.get("summary") or fallback.summary),
        target_titles=sorted(
            values(data.get("target_titles"), fallback.target_titles),
            key=str.casefold,
        )[:6],
        skills=canonical_skills(data.get("skills")),
        domains=sorted(
            [item.casefold() for item in values(data.get("domains"), fallback.domains)],
        )[:6],
        locations=sorted(
            values(data.get("locations"), fallback.locations), key=str.casefold
        )[:6],
        remote_preference=str(
            data.get("remote_preference", fallback.remote_preference)
        ),
        excluded_terms=sorted(
            [item.casefold() for item in values(data.get("excluded_terms"), [])]
        )[:8],
        years_experience=data.get("years_experience", fallback.years_experience),
    )


def _profile_prompt(resume_text: str) -> str:
    schema = {
        "name": "string",
        "summary": "string",
        "target_titles": ["string"],
        "skills": ["string"],
        "domains": ["string"],
        "locations": ["string"],
        "remote_preference": "any|remote|onsite|hybrid",
        "excluded_terms": ["string"],
        "years_experience": "integer|null",
    }
    return (
        "Extract a conservative job-search profile from this resume. Use only "
        "skills, titles, domains, locations, and exclusions explicitly supported "
        "by the resume; do not invent qualifications. Return only valid JSON "
        "matching this shape. Keep at most 6 target titles, 12 skills, 6 domains, "
        "6 locations, and 8 excluded terms. Use stable, canonical names and do "
        "not add generic skills such as communication or teamwork unless they are "
        "central to the target role.\n"
        f"SHAPE: {json.dumps(schema)}\nRESUME:\n{resume_text}"
    )


def build_profile(
    resume_text: str,
    provider: str = "heuristic",
    model: str | None = None,
    ollama_model: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> Profile:
    if ollama_model and provider == "heuristic":
        provider = "ollama"
        model = ollama_model
    config = resolve_config(provider, model)
    if config.provider == "heuristic":
        if progress:
            progress("LOCAL stage: deterministic heuristic profile parser")
        return heuristic_profile(resume_text)
    if progress:
        progress(
            f"LLM stage: parsing resume with {provider_label(config.provider)} "
            f"({config.model})"
        )
    profile_data = complete_profile_json(_profile_prompt(resume_text), config)
    try:
        return profile_from_json(profile_data, resume_text)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ProviderError(
            f"{provider_label(config.provider)} returned an invalid profile"
        ) from exc


def build_query_plan(profile: Profile, limit: int = 6) -> list[QuerySpec]:
    queries: list[QuerySpec] = []
    titles = profile.target_titles or ["AI automation"]
    skills = profile.skills[:3]
    domains = profile.domains[:2]
    for title in titles:
        queries.append(
            QuerySpec(
                search_term=title,
                location=profile.locations[0] if profile.locations else None,
            )
        )
    if skills:
        queries.append(QuerySpec(search_term=" ".join(skills[:2])))
    if domains and skills:
        queries.append(QuerySpec(search_term=" ".join(domains[:1] + skills[:2])))
    if profile.remote_preference == "remote":
        queries.append(
            QuerySpec(search_term=" ".join(titles[0].split()[:4]), is_remote=True)
        )

    unique: list[QuerySpec] = []
    seen = set()
    for query in queries:
        key = (query.search_term.lower(), query.location, query.is_remote)
        if key not in seen:
            unique.append(query)
            seen.add(key)
    return unique[:limit]
