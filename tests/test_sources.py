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


def test_mapping_recovers_english_and_french_description_salary():
    english = from_mapping(
        {
            "title": "AI Operations Engineer",
            "description": (
                "Compensation: **C$130,000–C$200,000 annual base salary**."
            ),
        }
    )
    assert (english.salary_min, english.salary_max, english.salary_interval) == (
        130000,
        200000,
        "yearly",
    )
    assert english.salary_currency == "CAD"
    assert english.salary_source == "description"

    french = from_mapping(
        {
            "title": "Ingénieur automatisation",
            "description": "Salaire : 80\u202f000 $ à 100\u202f000 $ par année.",
        }
    )
    assert (french.salary_min, french.salary_max, french.salary_interval) == (
        80000,
        100000,
        "yearly",
    )


def test_description_hourly_salary_is_annualized():
    job = from_mapping(
        {
            "title": "Technicien",
            "description": "Taux horaire de 25 $ à 35 $.",
        },
        enforce_annual_salary=True,
    )

    assert (job.salary_min, job.salary_max, job.salary_interval) == (
        52000,
        72800,
        "yearly",
    )

    posted = from_mapping(
        {
            "title": "Consultant",
            "description": "39,19$ à 55,99$ hourly.",
        }
    )
    assert (posted.salary_min, posted.salary_max, posted.salary_interval) == (
        39.19,
        55.99,
        "hourly",
    )


def test_description_salary_fallback_ignores_unrelated_numbers():
    job = from_mapping(
        {
            "title": "Engineer",
            "description": "Competitive salary. 5 years of experience. Posted in 2026.",
        }
    )

    assert job.salary_min is None
    assert job.salary_max is None
    assert job.salary_source == ""


def test_structured_salary_remains_authoritative_over_description():
    job = from_mapping(
        {
            "title": "Engineer",
            "min_amount": 90000,
            "max_amount": 110000,
            "interval": "yearly",
            "currency": "CAD",
            "description": "Compensation: C$120,000–C$140,000 annual base salary.",
        }
    )

    assert (job.salary_min, job.salary_max) == (90000, 110000)
    assert job.salary_source == "structured"


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
