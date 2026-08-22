# JobFit

JobFit is an open-source, resume-powered job radar. It turns a resume into
search queries, collects jobs through [JobSpy](https://github.com/speedyapply/JobSpy),
calculates an explainable fit estimate, stores history in SQLite, and shows only
new or changed jobs in a terminal UI.

The MVP deliberately does not auto-apply, send messages, or store credentials.
The score is an estimate to help a person review opportunities, not an
automated hiring decision.

The current MVP keeps previously seen jobs in SQLite and marks jobs absent
from a later fetch as retained history; it does not delete them. A job's URL
is the preferred identity, with a title/company/location fallback. Resume
text is read for the run but is not written to the database. Provider errors
fail the run rather than presenting an incomplete diff as complete. The score
formula is versioned as `0.1` until its weights are deliberately revised.

## Quick start

Use Python 3.10+ because JobSpy requires it:

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

Run the offline demo with the included sample resume and fixture jobs:

```bash
jobfit run --resume examples/resume.txt --fixture fixtures/jobs.json
```

Run it with your own resume. DOCX and PDF extraction use local macOS tools
when available, and plain text/Markdown are supported everywhere:

```bash
jobfit run \
  --resume /path/to/your/resume.docx \
  --site linkedin --site indeed --site google \
  --location "Montreal, QC" \
  --origin "H1A 0A1" --max-distance-km 50 \
  --hours-old 168
```

The database defaults to `data/jobfit.sqlite`. To show a text-only result,
add `--no-tui`. To use a local Ollama model for profile/query suggestions,
add `--provider ollama --model MODEL`. JobSpy queries run concurrently by
default; use `--query-workers N` to tune that concurrency. The default
provider is the offline heuristic parser, so the fixture demo never needs an
API key.

Cloud provider examples:

```bash
export OPENAI_API_KEY="..."
jobfit run --resume /path/to/resume.docx --provider openai --model gpt-4.1-mini

export OPENROUTER_API_KEY="..."
jobfit run --resume /path/to/resume.docx --provider openrouter --model openai/gpt-4.1-mini
```

To troubleshoot one provider without sending a resume or collecting jobs, run
a minimal JSON probe:

```bash
jobfit diagnose-llm --provider openrouter --model openai/gpt-4.1-mini
```

The diagnostic reports the endpoint, model, HTTP timing, response keys,
choice/output structure, finish reason, extracted-text path, character count,
and JSON parsing result. It never prints the prompt, resume, API key, or model
response text. The same redacted diagnostic events appear in the TUI during a
normal scan for OpenAI, OpenRouter, Anthropic, xAI, Gemini, and Ollama.

For a local reference of the supported environment variables, see
`.env.example`. It intentionally contains no credentials. Do not commit a
real `.env` file, a resume, scraped job exports, or the local SQLite database.

The TUI provider selector also supports OpenAI Responses, OpenRouter Chat,
Anthropic Messages, xAI Responses, Google Gemini, Ollama, and the offline
heuristic parser. Keys are read only from environment variables:
`OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, `XAI_API_KEY`,
and `GEMINI_API_KEY`.

Selecting a cloud provider sends the resume text to that provider for profile
extraction. Before that call, JobFit performs a local, deterministic English and
French privacy pass that replaces detected names in the resume header, email
addresses, phone numbers, labelled social/identity numbers, personal profile
URLs, and contact-address lines. The original resume is not sent by this
step; the redacted text is used only for cloud profile extraction. This is a
best-effort aid, not a guarantee of anonymization. Use `heuristic` or Ollama
when the resume must remain fully local.

The progress panel reports only redaction categories and counts, never the
matched values. JobFit deliberately does not use an LLM to find the PII before
the privacy boundary. Names in work history and ordinary job locations are
preserved unless they match the locally detected header name.

When a scan is running, the top progress panel reports resume loading, LLM
profile parsing, query generation, selected sources, parallel JobSpy
collection counts, duplicate filtering, distance filtering, rating, and the
final difference. Open `Settings [S]` to configure the sources, search
location, origin city/postal code, maximum non-remote distance, freshness,
results per source, parallel workers, Indeed country, `Easy Apply only`, and
annual-salary normalization. You can also enable full LinkedIn description
fetching, which is slower but gives JobSpy more text from which to recover
salary information. These settings are saved in the local SQLite database and
reused on the next launch. The results table opens in `DIFF` mode and shows
new or updated jobs. Press `V` to toggle to `ALL HISTORY`, which loads the
deduplicated jobs persisted in SQLite, including jobs not returned by the
latest scan. Running a new scan returns the table to `DIFF` mode. Locations
are geocoded only when the distance filter is enabled, cached in SQLite, and
jobs with unknown distance
remain visible with `—` so the app does not silently discard them. Press `R`
to re-run, `P` to focus the provider selector, `V` to toggle diff/history, use
the arrow keys to select a job, press `Space` to save or unsave its company,
press `D` to remove the row or block its company, and press `Enter` to open its
detail page or return to the results. Saved companies are shown in bold;
blocked companies are excluded from later scans.

The results table includes the normalized source (`SOURCE`), remote status,
easy-apply flag, distance, and salary. With annual normalization enabled,
hourly, daily, weekly, and monthly values are converted using standard
work-year multipliers before display and persistence.

The progress labels make the LLM boundary explicit:

- `LLM stage`: only the selected provider extracts the resume profile.
- `LOCAL stage`: profile normalization, query generation, filtering, scoring,
  persistence, and diff calculation are deterministic Python logic.
- `SOURCE stage`: JobSpy and the selected job boards fetch postings; no LLM is
  involved.

Job statuses mean:

- `NEW`: JobFit has never stored this job identity.
- `UPDATED`: JobFit saw it before, but meaningful content changed, including
  title, location, description, remote status, salary, coordinates, posting
  date, or fit score.
- `SEEN`: the job was fetched again without meaningful changes; it is kept in
  history but omitted from the diff view.

Salary and company values are source-dependent. JobFit now accepts JobSpy's
standard fields plus common employer/company aliases and salary text such as
`$80k - $100k`; a job remains blank when the source did not publish that
information. See the [JobSpy source schema](https://github.com/speedyapply/JobSpy#jobpost-schema)
for the fields available from each job board.

## Design

```text
resume -> profile agent -> query plan -> JobSpy -> normalization
        -> explainable scoring -> SQLite history/diff -> Textual TUI
```

The LLM-shaped boundary is intentionally narrow: it may suggest profile
fields and search queries, while validation, deduplication, persistence, and
the final score are deterministic and testable.

Provider implementations are deliberately normalized behind one function,
but their wire formats remain explicit: OpenAI/xAI use Responses-style
requests, OpenRouter uses its OpenAI-compatible chat endpoint, Anthropic uses
Messages, Gemini uses `generateContent`, and Ollama uses its local generate
endpoint. This keeps provider changes isolated from the scoring and TUI layers.

## Development

```bash
python -m pytest
python -m jobfit.cli run --resume examples/resume.txt --fixture fixtures/jobs.json --no-tui
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md) before
opening a pull request.
