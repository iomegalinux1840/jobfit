"""Opt-in distance filtering with a small, local geocode cache."""

from __future__ import annotations

import json
import math
import os
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .models import JobPosting
from .store import JobStore


@dataclass(frozen=True)
class GeoPoint:
    latitude: float
    longitude: float


def _key(value: str) -> str:
    return " ".join(value.casefold().split())


def _point_from_job(job: JobPosting) -> GeoPoint | None:
    if job.latitude is None or job.longitude is None:
        return None
    return GeoPoint(job.latitude, job.longitude)


class NominatimGeocoder:
    """A deliberately serial geocoder with SQLite caching and a polite delay."""

    def __init__(
        self,
        store: JobStore,
        progress: Callable[[str], None] | None = None,
        min_interval: float = 1.0,
    ):
        self.store = store
        self.progress = progress
        self.min_interval = min_interval
        self.last_request = 0.0
        self.endpoint = os.getenv(
            "JOBFIT_GEOCODER_URL", "https://nominatim.openstreetmap.org/search"
        )

    def geocode(self, query: str) -> GeoPoint | None:
        query_key = _key(query)
        if not query_key:
            return None
        cached = self.store.get_geocode(query_key)
        if cached is not None:
            return GeoPoint(*cached)

        wait = self.min_interval - (time.monotonic() - self.last_request)
        if wait > 0:
            time.sleep(wait)
        params = urllib.parse.urlencode(
            {"q": query, "format": "jsonv2", "limit": 1, "countrycodes": "ca"}
        )
        request = urllib.request.Request(
            f"{self.endpoint}?{params}",
            headers={"User-Agent": "jobfit/0.1 open-source job radar"},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError):
            payload = []
        self.last_request = time.monotonic()
        if not isinstance(payload, list) or not payload:
            self.store.save_geocode(query_key, None)
            return None
        try:
            point = GeoPoint(float(payload[0]["lat"]), float(payload[0]["lon"]))
        except (KeyError, TypeError, ValueError):
            self.store.save_geocode(query_key, None)
            return None
        self.store.save_geocode(query_key, (point.latitude, point.longitude))
        return point


def _haversine_km(first: GeoPoint, second: GeoPoint) -> float:
    earth_radius_km = 6371.0088
    lat1, lat2 = math.radians(first.latitude), math.radians(second.latitude)
    delta_lat = lat2 - lat1
    delta_lon = math.radians(second.longitude - first.longitude)
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return earth_radius_km * 2 * math.asin(math.sqrt(value))


def filter_jobs_by_distance(
    jobs: Iterable[JobPosting],
    origin: str | None,
    max_distance_km: float | None,
    store: JobStore,
    progress: Callable[[str], None] | None = None,
) -> list[JobPosting]:
    jobs = list(jobs)
    if max_distance_km is None:
        return jobs
    if max_distance_km < 0:
        raise ValueError("Maximum distance must be zero or greater")
    if not origin or not origin.strip():
        raise ValueError("A distance origin is required with --max-distance-km")

    geocoder = NominatimGeocoder(store, progress=progress)
    origin_point = geocoder.geocode(origin.strip())
    if origin_point is None:
        raise ValueError(f"Could not geocode distance origin: {origin}")
    if progress:
        progress(f"LOCAL stage: distance origin resolved as {origin.strip()}")

    kept: list[JobPosting] = []
    removed = unknown = 0
    for job in jobs:
        location_lower = job.location.casefold()
        if job.is_remote is True or "remote" in location_lower:
            job.distance_km = None
            kept.append(job)
            continue
        point = _point_from_job(job) or geocoder.geocode(job.location)
        if point is None:
            unknown += 1
            kept.append(job)
            continue
        job.distance_km = _haversine_km(origin_point, point)
        if job.distance_km <= max_distance_km:
            kept.append(job)
        else:
            removed += 1
    if progress:
        progress(
            f"LOCAL stage: distance filter — {removed} non-remote jobs removed; "
            f"{unknown} jobs kept with unknown distance"
        )
    return kept
