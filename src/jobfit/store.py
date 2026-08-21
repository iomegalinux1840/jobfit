from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from .models import FitResult, RunSummary, ScanSettings
from .scoring import SCORE_VERSION


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _content_hash(result: FitResult) -> str:
    job = result.job
    content = "|".join(
        (
            job.title,
            job.company,
            job.location,
            job.description,
            job.date_posted,
            str(job.is_remote),
            str(job.easy_apply),
            str(job.salary_min),
            str(job.salary_max),
            job.salary_interval,
            job.salary_currency,
            str(job.latitude),
            str(job.longitude),
            str(result.score),
        )
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def company_key(company: str) -> str:
    return " ".join(company.casefold().split())


class JobStore:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path))
        self.connection.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              started_at TEXT NOT NULL,
              finished_at TEXT NOT NULL,
              collected INTEGER NOT NULL,
              unique_jobs INTEGER NOT NULL,
              new_jobs INTEGER NOT NULL,
              updated_jobs INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
              job_id TEXT PRIMARY KEY,
              source TEXT NOT NULL,
              title TEXT NOT NULL,
              company TEXT NOT NULL,
              url TEXT,
              location TEXT,
              description TEXT,
              date_posted TEXT,
              salary_min REAL,
              salary_max REAL,
              salary_interval TEXT,
              salary_currency TEXT,
              is_remote INTEGER,
              easy_apply INTEGER,
              latitude REAL,
              longitude REAL,
              distance_km REAL,
              score INTEGER NOT NULL,
              matched_skills TEXT NOT NULL,
              missing_skills TEXT NOT NULL,
              matched_domains TEXT NOT NULL,
              reasons TEXT NOT NULL,
              score_version TEXT NOT NULL,
              content_hash TEXT NOT NULL,
              first_seen TEXT NOT NULL,
              last_seen TEXT NOT NULL,
              last_run_id INTEGER NOT NULL,
              FOREIGN KEY(last_run_id) REFERENCES runs(id)
            );
            CREATE TABLE IF NOT EXISTS saved_companies (
              company_key TEXT PRIMARY KEY,
              company_name TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS blocked_companies (
              company_key TEXT PRIMARY KEY,
              company_name TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS geocode_cache (
              query_key TEXT PRIMARY KEY,
              latitude REAL,
              longitude REAL,
              resolved_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            """
        )
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(jobs)")}
        migrations = {
            "score_version": "ALTER TABLE jobs ADD COLUMN score_version TEXT NOT NULL DEFAULT '0.1'",
            "salary_min": "ALTER TABLE jobs ADD COLUMN salary_min REAL",
            "salary_max": "ALTER TABLE jobs ADD COLUMN salary_max REAL",
            "salary_interval": "ALTER TABLE jobs ADD COLUMN salary_interval TEXT",
            "salary_currency": "ALTER TABLE jobs ADD COLUMN salary_currency TEXT",
            "is_remote": "ALTER TABLE jobs ADD COLUMN is_remote INTEGER",
            "easy_apply": "ALTER TABLE jobs ADD COLUMN easy_apply INTEGER",
            "latitude": "ALTER TABLE jobs ADD COLUMN latitude REAL",
            "longitude": "ALTER TABLE jobs ADD COLUMN longitude REAL",
            "distance_km": "ALTER TABLE jobs ADD COLUMN distance_km REAL",
        }
        for column, statement in migrations.items():
            if column not in columns:
                self.connection.execute(statement)
        self.connection.commit()

    def saved_companies(self) -> set[str]:
        rows = self.connection.execute("SELECT company_key FROM saved_companies")
        return {str(row[0]) for row in rows}

    def blocked_companies(self) -> set[str]:
        rows = self.connection.execute("SELECT company_key FROM blocked_companies")
        return {str(row[0]) for row in rows}

    def toggle_saved_company(self, company: str) -> bool:
        key = company_key(company)
        if not key:
            return False
        existing = self.connection.execute(
            "SELECT 1 FROM saved_companies WHERE company_key = ?", (key,)
        ).fetchone()
        if existing:
            self.connection.execute(
                "DELETE FROM saved_companies WHERE company_key = ?", (key,)
            )
            saved = False
        else:
            self.connection.execute(
                "INSERT INTO saved_companies(company_key, company_name, created_at) VALUES (?, ?, ?)",
                (key, company.strip(), _now()),
            )
            saved = True
        self.connection.commit()
        return saved

    def block_company(self, company: str) -> None:
        key = company_key(company)
        if not key:
            return
        self.connection.execute(
            "INSERT OR IGNORE INTO blocked_companies(company_key, company_name, created_at) VALUES (?, ?, ?)",
            (key, company.strip(), _now()),
        )
        self.connection.commit()

    def load_settings(self) -> ScanSettings:
        row = self.connection.execute(
            "SELECT value FROM settings WHERE key = 'scan'"
        ).fetchone()
        if row is None:
            return ScanSettings()
        try:
            data = json.loads(row[0])
        except (TypeError, ValueError):
            return ScanSettings()
        if not isinstance(data, dict):
            return ScanSettings()
        defaults = ScanSettings()
        try:
            max_distance = data.get("max_distance_km", defaults.max_distance_km)
            return ScanSettings(
                sites=[str(site) for site in data.get("sites", defaults.sites)],
                location=str(data.get("location", defaults.location)),
                origin=str(data.get("origin", defaults.origin)),
                max_distance_km=(
                    None if max_distance in (None, "") else float(max_distance)
                ),
                hours_old=int(data.get("hours_old", defaults.hours_old)),
                results_wanted=int(data.get("results_wanted", defaults.results_wanted)),
                query_workers=int(data.get("query_workers", defaults.query_workers)),
                country_indeed=str(data.get("country_indeed", defaults.country_indeed)),
                easy_apply_only=bool(
                    data.get("easy_apply_only", defaults.easy_apply_only)
                ),
                enforce_annual_salary=bool(
                    data.get("enforce_annual_salary", defaults.enforce_annual_salary)
                ),
                linkedin_fetch_description=bool(
                    data.get(
                        "linkedin_fetch_description",
                        defaults.linkedin_fetch_description,
                    )
                ),
            )
        except (TypeError, ValueError):
            return ScanSettings()

    def save_settings(self, settings: ScanSettings) -> None:
        self.connection.execute(
            """
            INSERT INTO settings(key, value, updated_at) VALUES ('scan', ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              value=excluded.value,
              updated_at=excluded.updated_at
            """,
            (
                json.dumps(
                    {
                        "sites": settings.sites,
                        "location": settings.location,
                        "origin": settings.origin,
                        "max_distance_km": settings.max_distance_km,
                        "hours_old": settings.hours_old,
                        "results_wanted": settings.results_wanted,
                        "query_workers": settings.query_workers,
                        "country_indeed": settings.country_indeed,
                        "easy_apply_only": settings.easy_apply_only,
                        "enforce_annual_salary": settings.enforce_annual_salary,
                        "linkedin_fetch_description": settings.linkedin_fetch_description,
                    }
                ),
                _now(),
            ),
        )
        self.connection.commit()

    def get_geocode(self, query_key: str) -> tuple[float, float] | None:
        row = self.connection.execute(
            "SELECT latitude, longitude FROM geocode_cache WHERE query_key = ?",
            (query_key,),
        ).fetchone()
        if row is None or row[0] is None or row[1] is None:
            return None
        return float(row[0]), float(row[1])

    def save_geocode(self, query_key: str, point: tuple[float, float] | None) -> None:
        latitude, longitude = point if point is not None else (None, None)
        self.connection.execute(
            """
            INSERT INTO geocode_cache(query_key, latitude, longitude, resolved_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(query_key) DO UPDATE SET
              latitude=excluded.latitude,
              longitude=excluded.longitude,
              resolved_at=excluded.resolved_at
            """,
            (query_key, latitude, longitude, _now()),
        )
        self.connection.commit()

    def record_run(self, collected: int, results: Iterable[FitResult]) -> RunSummary:
        result_list = list(results)
        started = _now()
        try:
            self.connection.execute(
                "INSERT INTO runs(started_at, finished_at, collected, unique_jobs, new_jobs, updated_jobs) VALUES (?, ?, ?, ?, 0, 0)",
                (started, started, collected, len(result_list)),
            )
            run_id = int(
                self.connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            )
            new_count = updated_count = unchanged_count = 0
            diff: list[FitResult] = []
            previous_hashes: dict[str, str] = {}
            if result_list:
                placeholders = ",".join("?" for _ in result_list)
                rows = self.connection.execute(
                    f"SELECT job_id, content_hash FROM jobs WHERE job_id IN ({placeholders})",
                    [result.job.job_id for result in result_list],
                )
                previous_hashes = {str(row[0]): str(row[1]) for row in rows}
            for result in result_list:
                job = result.job
                digest = _content_hash(result)
                previous = previous_hashes.get(job.job_id)
                status = (
                    "NEW"
                    if previous is None
                    else ("UPDATED" if previous != digest else "SEEN")
                )
                result.change_status = status
                if status == "NEW":
                    new_count += 1
                    diff.append(result)
                elif status == "UPDATED":
                    updated_count += 1
                    diff.append(result)
                else:
                    unchanged_count += 1
                now = _now()
                self.connection.execute(
                    """
                INSERT INTO jobs(job_id, source, title, company, url, location, description, date_posted,
                  salary_min, salary_max, salary_interval, salary_currency, is_remote, easy_apply, latitude, longitude, distance_km, score,
                  matched_skills, missing_skills, matched_domains, reasons, score_version, content_hash, first_seen, last_seen, last_run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                  source=excluded.source, title=excluded.title, company=excluded.company, url=excluded.url,
                  location=excluded.location, description=excluded.description, date_posted=excluded.date_posted,
                  salary_min=excluded.salary_min, salary_max=excluded.salary_max, salary_interval=excluded.salary_interval,
                  salary_currency=excluded.salary_currency, is_remote=excluded.is_remote, easy_apply=excluded.easy_apply,
                  latitude=excluded.latitude,
                  longitude=excluded.longitude, distance_km=excluded.distance_km,
                  score=excluded.score, matched_skills=excluded.matched_skills, missing_skills=excluded.missing_skills,
                  matched_domains=excluded.matched_domains, reasons=excluded.reasons, score_version=excluded.score_version,
                  content_hash=excluded.content_hash,
                  last_seen=excluded.last_seen, last_run_id=excluded.last_run_id
                """,
                    (
                        job.job_id,
                        job.source,
                        job.title,
                        job.company,
                        job.url,
                        job.location,
                        job.description,
                        job.date_posted,
                        job.salary_min,
                        job.salary_max,
                        job.salary_interval,
                        job.salary_currency,
                        None if job.is_remote is None else int(job.is_remote),
                        None if job.easy_apply is None else int(job.easy_apply),
                        job.latitude,
                        job.longitude,
                        job.distance_km,
                        result.score,
                        json.dumps(result.matched_skills),
                        json.dumps(result.missing_skills),
                        json.dumps(result.matched_domains),
                        json.dumps(result.reasons),
                        SCORE_VERSION,
                        digest,
                        now,
                        now,
                        run_id,
                    ),
                )
            self.connection.execute(
                "UPDATE runs SET finished_at = ?, new_jobs = ?, updated_jobs = ? WHERE id = ?",
                (_now(), new_count, updated_count, run_id),
            )
            self.connection.commit()
            return RunSummary(
                run_id,
                collected,
                len(result_list),
                new_count,
                updated_count,
                unchanged_count,
                diff,
            )
        except Exception:
            self.connection.rollback()
            raise

    def close(self) -> None:
        self.connection.close()
