from __future__ import annotations

from dataclasses import replace
from typing import ClassVar

from rich.text import Text

from .models import Profile, RunSummary, ScanSettings
from .providers import provider_label, provider_options
from .sources import SITE_LABELS
from .store import JobStore, company_key


def _cell(value: object, bold: bool = False) -> Text:
    return Text(str(value), style="bold" if bold else "")


def _salary(job) -> str:
    minimum, maximum = job.salary_min, job.salary_max
    if minimum is None and maximum is None:
        return "—"
    prefix = f"{job.salary_currency} " if job.salary_currency else ""
    suffix = f"/{job.salary_interval}" if job.salary_interval else ""
    if minimum is not None and maximum is not None:
        return f"{prefix}${minimum:,.0f}–${maximum:,.0f}{suffix}"
    amount = minimum if minimum is not None else maximum
    return f"{prefix}${amount:,.0f}{suffix}"


def _distance(job) -> str:
    return "—" if job.distance_km is None else f"{job.distance_km:.0f} km"


def _source_label(source: str) -> str:
    key = source.split(":", 1)[-1]
    return SITE_LABELS.get(key, key.replace("_", " ").title())


def build_textual_app(
    run_callable,
    initial_provider: str = "heuristic",
    database_path: str | None = None,
    initial_sites: list[str] | None = None,
    initial_location: str = "",
    initial_origin: str = "",
    initial_max_distance_km: float | None = None,
    initial_settings: ScanSettings | None = None,
):
    """Build the Textual app class so it can be exercised with App.run_test()."""
    try:
        from textual.app import App, ComposeResult
        from textual.containers import Horizontal, VerticalScroll
        from textual.screen import ModalScreen
        from textual.widgets import (
            Button,
            Checkbox,
            DataTable,
            Footer,
            Header,
            Input,
            Log,
            Select,
            Static,
        )
    except ImportError as exc:
        raise RuntimeError("Install Textual to use the TUI, or pass --no-tui") from exc

    class RemoveChoiceScreen(ModalScreen):
        CSS = """
        RemoveChoiceScreen { align: center middle; }
        #remove-dialog { width: 70; height: 11; border: round #c09cff; background: #171827; padding: 1 2; }
        """
        BINDINGS: ClassVar = [
            ("r", "remove_only", "Remove row"),
            ("b", "block_company", "Block company"),
            ("escape", "cancel", "Cancel"),
        ]

        def __init__(self, company: str):
            super().__init__()
            self.company = company

        def compose(self) -> ComposeResult:
            yield Static(
                f"REMOVE OPPORTUNITY\n\n{self.company}\n\n"
                "[R] remove this row only\n"
                "[B] remove row and block this company\n"
                "[Esc] cancel",
                id="remove-dialog",
            )

        def action_remove_only(self) -> None:
            self.dismiss("remove")

        def action_block_company(self) -> None:
            self.dismiss("block")

        def action_cancel(self) -> None:
            self.dismiss(None)

    class SettingsScreen(ModalScreen):
        BINDINGS: ClassVar = [("escape", "cancel", "Cancel")]

        CSS = """
        SettingsScreen { align: center middle; }
        #settings-dialog {
            width: 100;
            height: 34;
            border: round #77f29a;
            background: #171827;
            padding: 1 2;
        }
        #settings-sites { height: 3; }
        .settings-label { color: #77f29a; margin-top: 1; }
        .settings-input { width: 1fr; }
        #settings-actions { height: 3; margin-top: 1; }
        #settings-error { color: #ff7272; height: 2; }
        """

        def __init__(self, settings: ScanSettings):
            super().__init__()
            self.settings = settings

        def compose(self) -> ComposeResult:
            yield VerticalScroll(
                Static("JOBFIT SETTINGS", classes="settings-label"),
                Static("Job sources", classes="settings-label"),
                Horizontal(
                    *(
                        Checkbox(
                            label,
                            value=site in self.settings.sites,
                            id=f"settings-site-{site}",
                            classes="source-toggle",
                        )
                        for site, label in SITE_LABELS.items()
                    ),
                    id="settings-sites",
                ),
                Static("Search location", classes="settings-label"),
                Input(
                    value=self.settings.location,
                    placeholder="optional job-search location",
                    id="settings-location",
                    classes="settings-input",
                ),
                Static("Distance origin", classes="settings-label"),
                Input(
                    value=self.settings.origin,
                    placeholder="city or postal code",
                    id="settings-origin",
                    classes="settings-input",
                ),
                Static("Maximum distance (km)", classes="settings-label"),
                Input(
                    value=(
                        ""
                        if self.settings.max_distance_km is None
                        else str(self.settings.max_distance_km)
                    ),
                    placeholder="blank = no distance filter",
                    id="settings-max-distance",
                    classes="settings-input",
                ),
                Horizontal(
                    Input(
                        value=str(self.settings.hours_old),
                        placeholder="Hours old",
                        id="settings-hours-old",
                    ),
                    Input(
                        value=str(self.settings.results_wanted),
                        placeholder="Results per source",
                        id="settings-results-wanted",
                    ),
                    Input(
                        value=str(self.settings.query_workers),
                        placeholder="Parallel workers",
                        id="settings-query-workers",
                    ),
                    Input(
                        value=self.settings.country_indeed,
                        placeholder="Indeed country",
                        id="settings-country",
                    ),
                ),
                Checkbox(
                    "Easy apply only",
                    value=self.settings.easy_apply_only,
                    id="settings-easy-apply",
                ),
                Checkbox(
                    "Convert all salary values to annual salary",
                    value=self.settings.enforce_annual_salary,
                    id="settings-annual-salary",
                ),
                Checkbox(
                    "Fetch full LinkedIn descriptions (slower; improves salary data)",
                    value=self.settings.linkedin_fetch_description,
                    id="settings-linkedin-description",
                ),
                Static("", id="settings-error"),
                Horizontal(
                    Button("Save & re-scan", variant="success", id="settings-save"),
                    Button("Cancel", id="settings-cancel"),
                    id="settings-actions",
                ),
                id="settings-dialog",
            )

        def _integer(self, widget_id: str, label: str) -> int:
            value = self.query_one(f"#{widget_id}", Input).value.strip()
            try:
                parsed = int(value)
            except ValueError as exc:
                raise ValueError(f"{label} must be a whole number") from exc
            if parsed <= 0:
                raise ValueError(f"{label} must be greater than zero")
            return parsed

        def action_cancel(self) -> None:
            self.dismiss(None)

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "settings-cancel":
                self.dismiss(None)
                return
            try:
                sites = [
                    site
                    for site in SITE_LABELS
                    if self.query_one(f"#settings-site-{site}", Checkbox).value
                ]
                if not sites:
                    raise ValueError("Select at least one job source")
                raw_distance = self.query_one(
                    "#settings-max-distance", Input
                ).value.strip()
                max_distance = None if not raw_distance else float(raw_distance)
                if max_distance is not None and max_distance < 0:
                    raise ValueError("Maximum distance cannot be negative")
                updated = replace(
                    self.settings,
                    sites=sites,
                    location=self.query_one("#settings-location", Input).value.strip(),
                    origin=self.query_one("#settings-origin", Input).value.strip(),
                    max_distance_km=max_distance,
                    hours_old=self._integer("settings-hours-old", "Hours old"),
                    results_wanted=self._integer(
                        "settings-results-wanted", "Results per source"
                    ),
                    query_workers=self._integer(
                        "settings-query-workers", "Parallel workers"
                    ),
                    country_indeed=self.query_one(
                        "#settings-country", Input
                    ).value.strip()
                    or "Canada",
                    easy_apply_only=self.query_one(
                        "#settings-easy-apply", Checkbox
                    ).value,
                    enforce_annual_salary=self.query_one(
                        "#settings-annual-salary", Checkbox
                    ).value,
                    linkedin_fetch_description=self.query_one(
                        "#settings-linkedin-description", Checkbox
                    ).value,
                )
            except ValueError as exc:
                self.query_one("#settings-error", Static).update(str(exc))
                return
            self.dismiss(updated)

    class _App(App):
        TITLE = "JOBFIT — Personal Job Radar"
        CSS = """
        Screen { background: #10131c; color: #d8d9f0; }
        #brand { color: #77f29a; text-style: bold; height: 3; padding: 1 2; }
        #controls { height: 3; padding: 0 2; color: #b8b9d8; }
        #provider-label { width: 10; padding: 1 0; }
        #provider { width: 28; }
        #settings-button { width: 22; }
        #settings-summary { height: 2; padding: 0 2; color: #8f91b5; }
        #bottom-keymap { height: 2; padding: 0 2; color: #8f91b5; }
        #progress { height: 12; border: round #7777aa; color: #a5f5b8; }
        #jobs { height: 1fr; border: round #7777aa; }
        #detail { height: 1fr; border: round #7777aa; padding: 1 2; overflow-y: auto; }
        """
        BINDINGS: ClassVar = [
            ("r", "rescan", "Re-scan"),
            ("p", "focus_provider", "Provider"),
            ("s", "open_settings", "Settings"),
            ("space", "toggle_saved", "Save company"),
            ("d", "remove_or_block", "Remove/block"),
            ("b", "back_to_results", "Back from detail"),
            ("q", "quit", "Quit"),
            ("escape", "back_or_quit", "Back/Quit"),
        ]

        def __init__(self):
            super().__init__()
            self.selected_provider = initial_provider
            if database_path:
                store = JobStore(database_path)
                try:
                    loaded_settings = store.load_settings()
                finally:
                    store.close()
            else:
                loaded_settings = ScanSettings()
            self.settings = replace(initial_settings or loaded_settings)
            if initial_sites is not None:
                self.settings.sites = list(initial_sites)
            if initial_location:
                self.settings.location = initial_location
            if initial_origin:
                self.settings.origin = initial_origin
            if initial_max_distance_km is not None:
                self.settings.max_distance_km = initial_max_distance_km
            self.last_results = []
            self.scan_active = False

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            yield Static("JOBFIT — Personal Job Radar", id="brand")
            yield Horizontal(
                Static("LLM:", id="provider-label"),
                Select(provider_options(), value=initial_provider, id="provider"),
                Button("⚙ Settings [S]", id="settings-button"),
                id="controls",
            )
            yield Static(self._settings_summary(), id="settings-summary")
            yield Log(id="progress", highlight=True)
            yield DataTable(id="jobs", cursor_type="row")
            yield Static("", id="detail", markup=False)
            yield Static(
                "[↑↓] select   [Enter] detail/back   [Space] save company   "
                "[D] remove/block   [R] re-scan   [P] provider   [S] settings   "
                "[B/Esc] back   [Q] quit",
                id="bottom-keymap",
            )
            yield Footer()

        def on_mount(self) -> None:
            table = self.query_one("#jobs", DataTable)
            table.add_columns(
                "FIT",
                "STATUS",
                "TITLE",
                "COMPANY",
                "SOURCE",
                "LOCATION",
                "REMOTE",
                "EASY",
                "DIST",
                "SALARY",
            )
            table.focus()
            self._start_scan()

        def _start_scan(self) -> None:
            if self.scan_active:
                return
            self.scan_active = True
            self.last_results = []
            self.query_one("#progress", Log).clear()
            self.query_one("#jobs", DataTable).clear()
            self.query_one("#detail", Static).display = False
            self.query_one("#jobs", DataTable).display = True
            self.query_one("#progress", Log).display = True
            provider = self.selected_provider
            self._append_progress(f"Provider selected: {provider_label(provider)}")
            self._append_progress(
                "Sources selected: "
                + ", ".join(SITE_LABELS[site] for site in self.settings.sites)
            )
            if self.settings.max_distance_km is not None:
                self._append_progress(
                    f"Distance filter: {self.settings.max_distance_km:g} km from "
                    f"{self.settings.origin or 'origin not set'}"
                )
            self.run_worker(
                lambda: self._execute(provider, replace(self.settings)),
                thread=True,
            )

        def action_rescan(self) -> None:
            self._start_scan()

        def action_back_to_results(self) -> None:
            if self.query_one("#detail", Static).display:
                self._show_main()

        def action_back_or_quit(self) -> None:
            if self.query_one("#detail", Static).display:
                self._show_main()
            else:
                self.exit()

        def action_focus_provider(self) -> None:
            self.query_one("#provider", Select).focus()

        def action_open_settings(self) -> None:
            self.push_screen(
                SettingsScreen(replace(self.settings)), self._settings_saved
            )

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "settings-button":
                self.action_open_settings()

        def _settings_summary(self) -> str:
            sources = ", ".join(
                SITE_LABELS.get(site, site) for site in self.settings.sites
            )
            distance = (
                "any distance"
                if self.settings.max_distance_km is None
                else f"within {self.settings.max_distance_km:g} km"
            )
            return (
                f"Sources: {sources} | {distance} | "
                f"easy apply: {'on' if self.settings.easy_apply_only else 'all'} | "
                f"salary: {'annual' if self.settings.enforce_annual_salary else 'as posted'} | "
                f"LinkedIn details: {'on' if self.settings.linkedin_fetch_description else 'off'}"
            )

        def _settings_saved(self, settings: ScanSettings | None) -> None:
            if settings is None:
                return
            self.settings = settings
            if database_path:
                store = JobStore(database_path)
                try:
                    store.save_settings(settings)
                finally:
                    store.close()
            self.query_one("#settings-summary", Static).update(self._settings_summary())
            self._start_scan()

        def on_select_changed(self, event) -> None:
            if getattr(event.control, "id", None) == "provider":
                self.selected_provider = str(event.value)

        def _append_progress(self, message: str) -> None:
            self.query_one("#progress", Log).write_line("[✓] " + message)

        def _progress(self, message: str) -> None:
            self.call_from_thread(self._append_progress, message)

        def _execute(self, provider: str, settings: ScanSettings) -> None:
            try:
                profile, summary = run_callable(self._progress, provider, settings)
            except Exception as exc:
                self.call_from_thread(self._show_error, str(exc))
                return
            self.call_from_thread(self._show_results, profile, summary)

        def _show_error(self, message: str) -> None:
            self.scan_active = False
            self.query_one("#progress", Log).write_line(f"[!] {message}")

        def _show_results(self, profile: Profile, summary: RunSummary) -> None:
            self.scan_active = False
            self.last_results = list(summary.diff)
            self._populate_table()
            self.query_one("#progress", Log).write_line(
                f"[✓] Difference calculated: {summary.new} new jobs, "
                f"{summary.updated} updated"
            )
            self.query_one("#jobs", DataTable).focus()

        def _populate_table(self) -> None:
            table = self.query_one("#jobs", DataTable)
            table.clear()
            for result in self.last_results:
                saved = result.company_saved
                table.add_row(
                    _cell(f"{result.score}%", saved),
                    _cell(result.change_status, saved),
                    _cell(result.job.title[:48], saved),
                    _cell(result.job.company[:28], saved),
                    _cell(_source_label(result.job.source), saved),
                    _cell(result.job.location[:24], saved),
                    _cell("✓" if result.job.is_remote else "", saved),
                    _cell("✓" if result.job.easy_apply else "", saved),
                    _cell(_distance(result.job), saved),
                    _cell(_salary(result.job), saved),
                )

        def _selected_result(self):
            table = self.query_one("#jobs", DataTable)
            if not self.last_results or table.cursor_row < 0:
                return None
            return self.last_results[min(table.cursor_row, len(self.last_results) - 1)]

        def action_toggle_saved(self) -> None:
            result = self._selected_result()
            if result is None:
                return
            if not database_path:
                self._append_progress("Saving companies requires a database path")
                return
            store = JobStore(database_path)
            try:
                saved = store.toggle_saved_company(result.job.company)
            finally:
                store.close()
            target = company_key(result.job.company)
            for item in self.last_results:
                if company_key(item.job.company) == target:
                    item.company_saved = saved
            self._populate_table()
            self._append_progress(
                f"Company {'saved' if saved else 'unsaved'}: {result.job.company}"
            )
            self.query_one("#jobs", DataTable).focus()

        def action_remove_or_block(self) -> None:
            result = self._selected_result()
            if result is None:
                return
            self.remove_target_job_id = result.job.job_id
            self.push_screen(
                RemoveChoiceScreen(result.job.company), self._handle_remove_choice
            )

        def _handle_remove_choice(self, choice: str | None) -> None:
            if choice not in {"remove", "block"}:
                return
            target = next(
                (
                    item
                    for item in self.last_results
                    if item.job.job_id == self.remove_target_job_id
                ),
                None,
            )
            if target is None:
                return
            if choice == "block" and database_path:
                store = JobStore(database_path)
                try:
                    store.block_company(target.job.company)
                finally:
                    store.close()
                self._append_progress(f"Company blocked: {target.job.company}")
            elif choice == "remove":
                self._append_progress(f"Row removed: {target.job.title}")
            self.last_results = [
                item
                for item in self.last_results
                if item.job.job_id != target.job.job_id
            ]
            self._populate_table()
            self.query_one("#jobs", DataTable).focus()

        def on_data_table_row_selected(self, event) -> None:
            if getattr(event.data_table, "id", None) == "jobs":
                self._show_detail()

        def on_key(self, event) -> None:
            if event.key == "enter" and self.query_one("#detail", Static).display:
                self._show_main()

        def _show_detail(self) -> None:
            table = self.query_one("#jobs", DataTable)
            if not self.last_results or table.cursor_row < 0:
                return
            result = self.last_results[
                min(table.cursor_row, len(self.last_results) - 1)
            ]
            job = result.job
            lines = [
                f"FIT ESTIMATE: {result.score}%   STATUS: {result.change_status}",
                "",
                job.title,
                job.company,
                f"Location: {job.location or 'Not specified'}",
                f"Remote: {'✓' if job.is_remote else ''}",
                f"Distance: {_distance(job)}",
                f"Salary: {_salary(job)}",
                f"Salary source: {job.salary_source or 'Not provided'}",
                f"Easy apply: {'✓' if job.easy_apply else ''}",
                f"Saved company: {'✓' if result.company_saved else ''}",
                f"Source: {_source_label(job.source)}",
                f"Posted: {job.date_posted or 'Not specified'}",
                f"URL: {job.url or 'Not available'}",
                "",
                "MATCHED SKILLS",
                ", ".join(result.matched_skills) or "None detected",
                "",
                "MISSING / REVIEW",
                ", ".join(result.missing_skills) or "None detected",
                "",
                "WHY THIS SCORE",
                "\n".join(f"- {reason}" for reason in result.reasons)
                or "No explanation available",
                "",
                "DESCRIPTION",
                job.description or "No description available.",
                "",
                "Press Enter, B, or Esc to return to results.",
            ]
            self.query_one("#brand", Static).update("JOBFIT — Opportunity Detail")
            self.query_one("#detail", Static).update("\n".join(lines))
            self.query_one("#detail", Static).display = True
            table.display = False
            self.query_one("#progress", Log).display = False

        def _show_main(self) -> None:
            self.query_one("#brand", Static).update("JOBFIT — Personal Job Radar")
            self.query_one("#detail", Static).display = False
            self.query_one("#progress", Log).display = True
            table = self.query_one("#jobs", DataTable)
            table.display = True
            table.focus()

    return _App


class JobFitApp:
    """Small Textual shell kept separate so core logic remains usable headlessly."""

    def __init__(
        self,
        run_callable,
        initial_provider: str = "heuristic",
        database_path: str | None = None,
        initial_sites: list[str] | None = None,
        initial_location: str = "",
        initial_origin: str = "",
        initial_max_distance_km: float | None = None,
        initial_settings: ScanSettings | None = None,
    ):
        self.run_callable = run_callable
        self.initial_provider = initial_provider
        self.database_path = database_path
        self.initial_sites = initial_sites
        self.initial_location = initial_location
        self.initial_origin = initial_origin
        self.initial_max_distance_km = initial_max_distance_km
        self.initial_settings = initial_settings

    def run(self) -> None:
        build_textual_app(
            self.run_callable,
            self.initial_provider,
            self.database_path,
            self.initial_sites,
            self.initial_location,
            self.initial_origin,
            self.initial_max_distance_km,
            self.initial_settings,
        )().run()
