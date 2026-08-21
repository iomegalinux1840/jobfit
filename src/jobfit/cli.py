from __future__ import annotations

import argparse
import os
import sys

from .pipeline import run_pipeline
from .providers import PROVIDER_SPECS
from .sources import SITE_LABELS, JobSpyUnavailable


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobfit", description="Resume-powered job radar"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="collect, score, and show the job diff")
    run.add_argument(
        "--resume", required=True, help="path to TXT, Markdown, DOCX, or PDF resume"
    )
    run.add_argument("--fixture", help="offline JSON fixture instead of live JobSpy")
    run.add_argument("--database", default="data/jobfit.sqlite")
    run.add_argument(
        "--site", action="append", dest="sites", choices=list(SITE_LABELS), default=[]
    )
    run.add_argument("--location")
    run.add_argument(
        "--origin",
        help="city or postal code used to calculate non-remote job distance",
    )
    run.add_argument(
        "--max-distance-km",
        type=float,
        help="keep non-remote jobs within this distance of --origin",
    )
    run.add_argument("--hours-old", type=int, default=168)
    run.add_argument("--results-wanted", type=int, default=25)
    run.add_argument(
        "--query-workers",
        type=int,
        default=6,
        help="parallel JobSpy query workers (default: 6)",
    )
    run.add_argument(
        "--provider",
        choices=list(PROVIDER_SPECS),
        default=os.getenv("JOBFIT_LLM_PROVIDER", "heuristic"),
        help="LLM used to parse the resume (default: heuristic)",
    )
    run.add_argument(
        "--model", help="provider model override; also honors JOBFIT_MODEL"
    )
    run.add_argument("--ollama-model")
    run.add_argument("--no-tui", action="store_true")
    return parser


def _print_results(profile, summary) -> None:
    print(f"\nJOBFIT — {profile.name}")
    print(
        f"Run #{summary.run_id} | collected={summary.collected} unique={summary.unique} "
        f"new={summary.new} updated={summary.updated} unchanged={summary.unchanged}"
    )
    if not summary.diff:
        print("No new or changed jobs.")
        return
    print("\nDIFF")
    for result in summary.diff:
        print(
            f"{result.score:3d}% | {result.job.title[:48]:<48} | "
            f"{result.job.company[:24]:<24} | {result.job.location}"
        )
        if result.reasons:
            print("      " + " | ".join(result.reasons))


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if args.command != "run":
        return 2

    def execute(progress=None, provider_override=None, settings_override=None):
        return run_pipeline(
            resume_path=args.resume,
            database_path=args.database,
            fixture_path=args.fixture,
            sites=args.sites or None,
            location=args.location,
            hours_old=args.hours_old,
            results_wanted=args.results_wanted,
            query_workers=args.query_workers,
            origin=args.origin,
            max_distance_km=args.max_distance_km,
            llm_provider=provider_override or args.provider,
            llm_model=args.model,
            ollama_model=args.ollama_model,
            scan_settings=settings_override,
            progress=progress,
        )

    try:
        if args.no_tui:
            profile, summary = execute(print, args.provider)
            _print_results(profile, summary)
        else:
            from .tui import JobFitApp

            initial_provider = "ollama" if args.ollama_model else args.provider
            JobFitApp(
                execute,
                initial_provider=initial_provider,
                database_path=args.database,
                initial_sites=args.sites or None,
                initial_location=args.location or "",
                initial_origin=args.origin or "",
                initial_max_distance_km=args.max_distance_km,
            ).run()
    except (FileNotFoundError, ValueError, RuntimeError, JobSpyUnavailable) as exc:
        print(f"jobfit: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
