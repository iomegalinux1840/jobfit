from jobfit.geo import GeoPoint, filter_jobs_by_distance
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
