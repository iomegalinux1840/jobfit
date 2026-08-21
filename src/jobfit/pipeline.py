from __future__ import annotations

import time
from collections.abc import Callable, Sequence

from .agent import build_profile, build_query_plan
from .geo import filter_jobs_by_distance
from .models import Profile, RunSummary, ScanSettings
from .resume import load_resume_text
from .scoring import score_job
from .sources import FixtureSource, JobSpySource, deduplicate_jobs
from .store import JobStore, company_key

Progress = Callable[[str], None]


def run_pipeline(
    resume_path: str,
    database_path: str,
    fixture_path: str | None = None,
    sites: Sequence[str] | None = None,
    location: str | None = None,
    hours_old: int = 168,
    results_wanted: int = 25,
    query_workers: int = 6,
    origin: str | None = None,
    max_distance_km: float | None = None,
    llm_provider: str = "heuristic",
    llm_model: str | None = None,
    ollama_model: str | None = None,
    scan_settings: ScanSettings | None = None,
    progress: Progress | None = None,
) -> tuple[Profile, RunSummary]:
    if scan_settings is not None:
        sites = scan_settings.sites
        location = scan_settings.location or None
        origin = scan_settings.origin or None
        max_distance_km = scan_settings.max_distance_km
        hours_old = scan_settings.hours_old
        results_wanted = scan_settings.results_wanted
        query_workers = scan_settings.query_workers

    def report(message: str) -> None:
        if progress:
            progress(message)

    def timed(label: str, started: float) -> None:
        report(f"TIMING stage: {label} took {time.perf_counter() - started:.2f}s")

    started = time.perf_counter()
    report("Loading resume")
    resume_text = load_resume_text(resume_path)
    report("Resume loaded")
    timed("resume loading", started)
    selected_provider = (
        "ollama" if ollama_model and llm_provider == "heuristic" else llm_provider
    )
    selected_model = ollama_model or llm_model
    started = time.perf_counter()
    profile = build_profile(
        resume_text,
        provider=selected_provider,
        model=selected_model,
        progress=report,
    )
    report("LOCAL stage: profile output normalized and validated")
    report(
        f"Profile ready: {len(profile.skills)} skills, {len(profile.target_titles)} target titles"
    )
    timed("profile extraction and normalization", started)
    started = time.perf_counter()
    queries = build_query_plan(profile)
    if location:
        queries = [
            query.__class__(query.search_term, location, query.is_remote)
            for query in queries
        ]
    report(
        f"LOCAL stage: deterministic query plan generated ({len(queries)} queries; no LLM)"
    )
    timed("query planning", started)
    if scan_settings is not None:
        report(
            "JobSpy settings: "
            f"easy apply only={'on' if scan_settings.easy_apply_only else 'off'}, "
            f"salary={'annual' if scan_settings.enforce_annual_salary else 'as posted'}"
        )

    if fixture_path:
        source = FixtureSource(
            fixture_path,
            enforce_annual_salary=(
                True if scan_settings is None else scan_settings.enforce_annual_salary
            ),
        )
    else:
        source = JobSpySource(
            sites or ["linkedin", "indeed", "google"],
            hours_old=hours_old,
            results_wanted=results_wanted,
            max_workers=query_workers,
            country_indeed=(
                "Canada" if scan_settings is None else scan_settings.country_indeed
            ),
            easy_apply_only=(
                False if scan_settings is None else scan_settings.easy_apply_only
            ),
            enforce_annual_salary=(
                True if scan_settings is None else scan_settings.enforce_annual_salary
            ),
            linkedin_fetch_description=(
                True
                if scan_settings is None
                else scan_settings.linkedin_fetch_description
            ),
        )
    started = time.perf_counter()
    jobs = source.fetch(queries, progress=report)
    timed("source collection", started)
    unique_jobs = deduplicate_jobs(jobs)
    source_label = (
        "JobSpy" if not fixture_path else "Fixture / JobSpy-compatible source"
    )
    report(f"SOURCE stage: {source_label} collected {len(jobs)} jobs")
    started = time.perf_counter()
    salary_count = sum(
        job.salary_min is not None or job.salary_max is not None for job in unique_jobs
    )
    company_count = sum(bool(job.company) for job in unique_jobs)
    report(
        "LOCAL stage: normalized metadata — "
        f"salary on {salary_count}/{len(unique_jobs)}, "
        f"company on {company_count}/{len(unique_jobs)}"
    )
    report(
        "LOCAL stage: duplicate filtering — "
        f"{len(jobs) - len(unique_jobs)} duplicate jobs removed"
    )
    timed("normalization and deduplication", started)
    store = JobStore(database_path)
    try:
        started = time.perf_counter()
        distance_jobs = filter_jobs_by_distance(
            unique_jobs,
            origin=origin,
            max_distance_km=max_distance_km,
            store=store,
            progress=report,
        )
        blocked = store.blocked_companies()
        eligible_jobs = [
            job for job in distance_jobs if company_key(job.company) not in blocked
        ]
        report(
            "LOCAL stage: blocking — "
            f"{len(distance_jobs) - len(eligible_jobs)} jobs from blocked companies removed"
        )
        scored = [score_job(profile, job) for job in eligible_jobs]
        scored.sort(key=lambda result: (-result.score, result.job.title.lower()))
        report(
            f"LOCAL stage: deterministic fit rating — {len(scored)} jobs rated; no LLM"
        )
        timed("filtering and deterministic scoring", started)
        started = time.perf_counter()
        summary = store.record_run(len(jobs), scored)
        saved = store.saved_companies()
        for result in scored:
            result.company_saved = company_key(result.job.company) in saved
    finally:
        store.close()
    report(
        f"LOCAL stage: history diff — {summary.new} new jobs, {summary.updated} updated, "
        f"{summary.unchanged} unchanged"
    )
    timed("SQLite persistence and diff", started)
    return profile, summary
