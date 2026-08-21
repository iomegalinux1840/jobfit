import sys
import threading
import time
import types

from jobfit.models import QuerySpec
from jobfit.sources import JobSpySource, from_mapping


def test_jobspy_adapter_isolated_from_network(monkeypatch):
    calls = {}

    class FakeFrame:
        def to_dict(self, orient):
            assert orient == "records"
            return [
                {
                    "site": "indeed",
                    "title": "AI Engineer",
                    "company": "Acme",
                    "job_url": "https://example.test/1",
                    "description": "Python automation",
                }
            ]

    def fake_scrape_jobs(**kwargs):
        calls.update(kwargs)
        return FakeFrame()

    fake_module = types.ModuleType("jobspy")
    fake_module.scrape_jobs = fake_scrape_jobs
    monkeypatch.setitem(sys.modules, "jobspy", fake_module)

    jobs = JobSpySource(
        ["indeed"],
        hours_old=48,
        easy_apply_only=True,
        enforce_annual_salary=True,
    ).fetch([QuerySpec("AI Engineer", is_remote=True)])

    assert len(jobs) == 1
    assert calls["is_remote"] is True
    assert calls["easy_apply"] is True
    assert calls["enforce_annual_salary"] is True
    assert calls["linkedin_fetch_description"] is True
    assert "hours_old" not in calls


def test_invalid_fixture_row_is_rejected():
    try:
        from_mapping("not an object")
    except TypeError as exc:
        assert "JSON objects" in str(exc)
    else:
        raise AssertionError("invalid row should be rejected")


def test_mapping_recovers_company_location_and_text_salary():
    job = from_mapping(
        {
            "title": "Automation Engineer",
            "company_name": "Acme Manufacturing",
            "city": "Montreal",
            "province": "QC",
            "salary": "$80k - $100k",
            "interval": "yearly",
            "currency": "CAD",
            "is_remote": "false",
        }
    )

    assert job.company == "Acme Manufacturing"
    assert job.location == "Montreal, QC"
    assert (job.salary_min, job.salary_max) == (80000, 100000)
    assert job.salary_interval == "yearly"
    assert job.salary_currency == "CAD"
    assert job.is_remote is False

    annual = from_mapping(
        {
            "title": "Technician",
            "company": "Acme",
            "min_amount": 25,
            "max_amount": 30,
            "interval": "hourly",
        },
        enforce_annual_salary=True,
    )
    assert (annual.salary_min, annual.salary_max, annual.salary_interval) == (
        52000,
        62400,
        "yearly",
    )


def test_jobspy_queries_run_in_parallel(monkeypatch):
    active = 0
    maximum = 0
    lock = threading.Lock()
    barrier = threading.Barrier(3)

    class FakeFrame:
        def to_dict(self, orient):
            return [{"site": "indeed", "title": "Engineer", "company": "Acme"}]

    def fake_scrape_jobs(**kwargs):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        barrier.wait(timeout=2)
        time.sleep(0.01)
        with lock:
            active -= 1
        return FakeFrame()

    fake_module = types.ModuleType("jobspy")
    fake_module.scrape_jobs = fake_scrape_jobs
    monkeypatch.setitem(sys.modules, "jobspy", fake_module)

    jobs = JobSpySource(["indeed"], max_workers=3).fetch(
        [QuerySpec("one"), QuerySpec("two"), QuerySpec("three")]
    )

    assert len(jobs) == 3
    assert maximum == 3
