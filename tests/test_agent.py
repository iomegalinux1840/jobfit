from jobfit.agent import build_query_plan, heuristic_profile, profile_from_json


def test_profile_extracts_resume_signals():
    profile = heuristic_profile(
        "Alex\nPython, LLMs, Docker and manufacturing automation. 10 years"
    )
    assert "python" in profile.skills
    assert "llms" in profile.skills
    assert profile.years_experience == 10


def test_query_plan_is_bounded_and_unique():
    profile = heuristic_profile("Python, LLMs, manufacturing and automation")
    queries = build_query_plan(profile, limit=6)
    keys = [(query.search_term, query.location, query.is_remote) for query in queries]
    assert len(queries) <= 6
    assert len(keys) == len(set(keys))


def test_llm_profile_output_is_canonicalized_for_stable_queries():
    profile = profile_from_json(
        {
            "target_titles": [" Automation Engineer ", "AI Engineer", "AI Engineer"],
            "skills": ["Python", "LLMs", "python programming"],
            "domains": ["Manufacturing", "manufacturing"],
        },
        "Candidate\nPython and manufacturing",
    )

    assert profile.target_titles == ["AI Engineer", "Automation Engineer"]
    assert profile.skills == ["llms", "python"]
    assert profile.domains == ["manufacturing"]
