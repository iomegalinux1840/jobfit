from jobfit.models import JobPosting, Profile
from jobfit.scoring import score_job


def test_score_explains_skill_matches_and_gaps():
    profile = Profile(skills=["python", "docker", "llms"], domains=["manufacturing"])
    job = JobPosting(
        "1",
        "fixture",
        "AI Automation Engineer",
        "Acme",
        description="Python and Docker for manufacturing automation",
    )
    result = score_job(profile, job)
    assert result.score > 0
    assert "python" in result.matched_skills
    assert "llms" in result.missing_skills
    assert any("Matches" in reason for reason in result.reasons)


def test_excluded_term_zeroes_score():
    profile = Profile(skills=["python"], excluded_terms=["crypto"])
    job = JobPosting(
        "1", "fixture", "Python Engineer", "Acme", description="Crypto platform"
    )
    result = score_job(profile, job)
    assert result.score == 0
    assert result.hard_exclusion == "crypto"
