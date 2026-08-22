from jobfit.geo import GeoPoint, NominatimGeocoder, filter_jobs_by_distance
from jobfit.models import JobPosting
from jobfit.store import JobStore


def test_distance_filter_keeps_remote_and_near_jobs(tmp_path, monkeypatch):
    def fake_geocode(self, query):
        if query == "Montreal, QC":
            return GeoPoint(45.5017, -73.5673)
        if query == "Toronto, ON":
            return GeoPoint(43.6532, -79.3832)
        return GeoPoint(45.5088, -73.5540)

    monkeypatch.setattr("jobfit.geo.NominatimGeocoder.geocode", fake_geocode)
    store = JobStore(str(tmp_path / "jobs.sqlite"))
    try:
        jobs = filter_jobs_by_distance(
            [
                JobPosting("near", "fixture", "Near", "Acme", location="Laval, QC"),
                JobPosting("remote", "fixture", "Remote", "Remote Co", is_remote=True),
                JobPosting("far", "fixture", "Far", "Far Co", location="Toronto, ON"),
            ],
            origin="Montreal, QC",
            max_distance_km=10,
            store=store,
        )
    finally:
        store.close()

    assert {job.job_id for job in jobs} == {"near", "remote"}
    assert jobs[0].distance_km is not None


def test_distance_filter_geocodes_duplicate_locations_once(tmp_path, monkeypatch):
    calls = []

    def fake_geocode(self, query):
        calls.append(query)
        return GeoPoint(45.5017, -73.5673)

    monkeypatch.setattr("jobfit.geo.NominatimGeocoder.geocode", fake_geocode)
    store = JobStore(str(tmp_path / "jobs.sqlite"))
    try:
        jobs = filter_jobs_by_distance(
            [
                JobPosting("one", "fixture", "One", "Acme", location="Montreal, QC"),
                JobPosting("two", "fixture", "Two", "Acme", location="Montreal, QC"),
            ],
            origin="Montreal, QC",
            max_distance_km=10,
            store=store,
        )
    finally:
        store.close()

    assert len(jobs) == 2
    assert calls == ["Montreal, QC", "Montreal, QC"]


def test_geocoder_reuses_negative_cache(tmp_path, monkeypatch):
    store = JobStore(str(tmp_path / "jobs.sqlite"))
    store.save_geocode("unknown place", None)
    geocoder = NominatimGeocoder(store)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("negative cache should avoid a network request")

    monkeypatch.setattr("jobfit.geo.urllib.request.urlopen", fail_if_called)
    try:
        assert geocoder.geocode("Unknown Place") is None
        assert geocoder.network_requests == 0
        assert geocoder.cache_hits == 1
    finally:
        store.close()
