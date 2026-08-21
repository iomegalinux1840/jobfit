# Contributing

Small, focused pull requests are preferred. Keep collection, scoring, and UI
concerns separate and add a focused test for behavior changes.

Before submitting a pull request:

```bash
python -m pytest
python -m jobfit.cli run --resume examples/resume.txt --fixture fixtures/jobs.json --no-tui
```

Do not commit resumes, scraped job descriptions, databases, cookies, proxy
credentials, or API keys. Use fixtures and synthetic data in tests.
