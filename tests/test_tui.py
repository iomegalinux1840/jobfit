import asyncio

from jobfit.models import FitResult, JobPosting, Profile, RunSummary
from jobfit.store import JobStore
from jobfit.tui import build_textual_app


def test_tui_renders_diff_without_network():
    asyncio.run(_exercise_tui())


def test_tui_saves_and_blocks_selected_company(tmp_path):
    asyncio.run(_exercise_company_actions(tmp_path / "preferences.sqlite"))


async def _exercise_tui():
    result = FitResult(
        JobPosting("fixture:1", "fixture", "AI Engineer", "Acme", location="Montreal"),
        score=88,
        change_status="NEW",
    )
    summary = RunSummary(1, 1, 1, 1, 0, 0, [result])

    def execute(progress, provider, settings):
        assert provider == "heuristic"
        assert settings.sites == ["linkedin", "indeed", "google"]
        assert settings.origin == ""
        assert settings.max_distance_km is None
        progress("fixture loaded")
        return Profile(name="Test Candidate"), summary

    app = build_textual_app(execute)()
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        table = app.query_one("#jobs")
        assert table.row_count == 1
        assert app.query_one("#brand") is not None
        await pilot.press("enter")
        await pilot.pause()
        assert app.query_one("#detail").display is True
        await pilot.press("enter")
        assert app.query_one("#detail").display is False
        await pilot.press("enter")
        await pilot.pause()
        assert app.query_one("#detail").display is True
        await pilot.press("b")
        assert app.query_one("#detail").display is False


async def _exercise_company_actions(database_path):
    result = FitResult(
        JobPosting(
            "fixture:1",
            "fixture",
            "AI Engineer",
            "Acme",
            location="Montreal",
            is_remote=True,
            salary_min=90000,
            salary_max=110000,
        ),
        score=88,
        change_status="NEW",
    )
    summary = RunSummary(1, 1, 1, 1, 0, 0, [result])

    def execute(progress, provider, settings):
        assert settings.sites == ["indeed"]
        assert settings.origin == "Montreal, QC"
        assert settings.max_distance_km == 25
        return Profile(name="Test Candidate"), summary

    app = build_textual_app(
        execute,
        database_path=str(database_path),
        initial_sites=["indeed"],
        initial_origin="Montreal, QC",
        initial_max_distance_km=25,
    )()
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        await pilot.press("s")
        await pilot.pause()
        assert app.screen.query_one("#settings-save") is not None
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        assert result.company_saved is True

        store = JobStore(str(database_path))
        try:
            assert store.saved_companies() == {"acme"}
        finally:
            store.close()

        await pilot.press("d")
        await pilot.pause()
        await pilot.press("b")
        await pilot.pause()

        store = JobStore(str(database_path))
        try:
            assert store.blocked_companies() == {"acme"}
        finally:
            store.close()
        assert app.query_one("#jobs").row_count == 0
