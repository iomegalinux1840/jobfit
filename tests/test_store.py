from jobfit.models import FitResult, JobPosting, ScanSettings
from jobfit.store import JobStore


def _result(description="Python and automation"):
    return FitResult(
        JobPosting(
            "fixture:1",
            "fixture",
            "Automation Engineer",
            "Acme",
            description=description,
        ),
        80,
        ["python"],
        [],
        [],
        ["Matches: python"],
    )


def test_store_shows_new_then_unchanged_then_updated(tmp_path):
    store = JobStore(str(tmp_path / "jobs.sqlite"))
    first = store.record_run(1, [_result()])
    second = store.record_run(1, [_result()])
    third = store.record_run(1, [_result("Python, automation and LLMs")])
    store.close()
    assert (first.new, first.updated, first.unchanged) == (1, 0, 0)
    assert (second.new, second.updated, second.unchanged) == (0, 0, 1)
    assert (third.new, third.updated, third.unchanged) == (0, 1, 0)


def test_store_loads_deduplicated_history_with_saved_company_state(tmp_path):
    store = JobStore(str(tmp_path / "history.sqlite"))
    first = _result()
    second = _result("Python, automation and LLMs")
    second.job.job_id = "fixture:2"
    second.job.title = "Another Automation Engineer"
    store.record_run(2, [first, second])
    store.toggle_saved_company("Acme")

    history = store.load_history()
    store.close()

    assert len(history) == 2
    assert {item.job.job_id for item in history} == {"fixture:1", "fixture:2"}
    assert all(item.change_status == "HISTORY" for item in history)
    assert all(item.company_saved for item in history)
    assert history[0].job.description


def test_store_persists_salary_provenance(tmp_path):
    store = JobStore(str(tmp_path / "salary-source.sqlite"))
    result = _result()
    result.job.salary_min = 80000
    result.job.salary_max = 100000
    result.job.salary_interval = "yearly"
    result.job.salary_currency = "CAD"
    result.job.salary_source = "description"
    store.record_run(1, [result])

    history = store.load_history()
    store.close()

    assert history[0].job.salary_source == "description"


def test_saved_and_blocked_companies_are_persistent(tmp_path):
    store = JobStore(str(tmp_path / "preferences.sqlite"))
    assert store.toggle_saved_company(" Acme  Inc. ") is True
    assert "acme inc." in store.saved_companies()
    assert store.toggle_saved_company("ACME INC.") is False
    store.block_company("Blocked Corp")
    store.close()

    reopened = JobStore(str(tmp_path / "preferences.sqlite"))
    assert "acme inc." not in reopened.saved_companies()
    assert "blocked corp" in reopened.blocked_companies()
    reopened.close()


def test_scan_settings_are_persistent(tmp_path):
    path = tmp_path / "settings.sqlite"
    store = JobStore(str(path))
    store.save_settings(
        ScanSettings(
            sites=["indeed"],
            location="Montreal, QC",
            origin="H1A 0A1",
            max_distance_km=40,
            hours_old=48,
            results_wanted=10,
            query_workers=3,
            country_indeed="Canada",
            easy_apply_only=True,
            enforce_annual_salary=False,
            linkedin_fetch_description=False,
        )
    )
    store.close()

    reopened = JobStore(str(path))
    settings = reopened.load_settings()
    reopened.close()
    assert settings.sites == ["indeed"]
    assert settings.location == "Montreal, QC"
    assert settings.origin == "H1A 0A1"
    assert settings.max_distance_km == 40
    assert settings.easy_apply_only is True
    assert settings.enforce_annual_salary is False
    assert settings.linkedin_fetch_description is False
