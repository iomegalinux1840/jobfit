from pathlib import Path

from jobfit.models import Profile
from jobfit.pipeline import run_pipeline
from jobfit.store import JobStore


def test_local_search_also_fetches_countrywide_remote_jobs(monkeypatch, tmp_path):
    captured = []

    def fetch(self, queries, progress=None):
        captured.extend(queries)
        return []

    monkeypatch.setattr("jobfit.pipeline.JobSpySource.fetch", fetch)
    root = Path(__file__).parents[1]
    run_pipeline(
        str(root / "examples" / "resume.txt"),
        str(tmp_path / "jobs.sqlite"),
        location="Roberval, QC",
    )
    remote = [query for query in captured if query.is_remote is True]
    local = [query for query in captured if query.is_remote is not True]
    assert remote and local
    assert all(query.location == "Canada" for query in remote)
    assert all(query.location == "Roberval, QC" for query in local)


def test_pipeline_reports_scan_stages(tmp_path):
    root = Path(__file__).parents[1]
    messages = []
    _, summary = run_pipeline(
        str(root / "examples" / "resume.txt"),
        str(tmp_path / "jobs.sqlite"),
        fixture_path=str(root / "fixtures" / "jobs.json"),
        progress=messages.append,
    )

    assert summary.new == 3
    assert any(message == "Resume loaded" for message in messages)
    assert any(
        message.startswith("LOCAL stage: deterministic heuristic")
        for message in messages
    )
    assert any("deterministic query plan generated" in message for message in messages)
    assert any("collected 3 jobs" in message for message in messages)
    assert any("description fallback" in message for message in messages)
    assert any("TIMING stage: distance filtering" in message for message in messages)
    assert any("TIMING stage: blocking" in message for message in messages)
    assert any("TIMING stage: deterministic scoring" in message for message in messages)
    assert any("duplicate filtering" in message for message in messages)
    assert any("deterministic fit rating" in message for message in messages)
    assert any(message.startswith("LOCAL stage: history diff") for message in messages)


def test_pipeline_excludes_blocked_companies(tmp_path):
    root = Path(__file__).parents[1]
    database = tmp_path / "blocked.sqlite"
    store = JobStore(str(database))
    store.block_company("Northstar Manufacturing")
    store.close()

    _, summary = run_pipeline(
        str(root / "examples" / "resume.txt"),
        str(database),
        fixture_path=str(root / "fixtures" / "jobs.json"),
    )

    assert summary.unique == 2
    assert all("northstar" not in result.job.company.lower() for result in summary.diff)


def test_pipeline_sends_redacted_resume_to_cloud_provider(monkeypatch, tmp_path):
    root = Path(__file__).parents[1]
    resume = tmp_path / "resume.txt"
    resume.write_text(
        "Jane Doe\njane.doe@example.com | +1 514-555-1234\n"
        "Python, manufacturing, and automation\n",
        encoding="utf-8",
    )
    captured = {}

    def fake_build_profile(resume_text, provider, model, progress):
        captured["text"] = resume_text
        captured["provider"] = provider
        return Profile(name="Candidate", skills=["python"])

    monkeypatch.setattr("jobfit.pipeline.build_profile", fake_build_profile)
    messages = []
    run_pipeline(
        str(resume),
        str(tmp_path / "jobs.sqlite"),
        fixture_path=str(root / "fixtures" / "jobs.json"),
        llm_provider="openai",
        progress=messages.append,
    )

    assert captured["provider"] == "openai"
    assert "Jane Doe" not in captured["text"]
    assert "jane.doe@example.com" not in captured["text"]
    assert "514-555-1234" not in captured["text"]
    assert "Python, manufacturing, and automation" in captured["text"]
    assert any("privacy redaction" in message for message in messages)
