# Security

Please do not report security issues in public issues. Contact the maintainers
through the repository's private security reporting channel when one is
configured.

JobFit is designed for read-only job discovery. It must not be extended to
submit applications, send recruiter messages, bypass access controls, or
collect credentials without an explicit security review and human approval.

Cloud LLM providers receive a locally redacted resume only when the user
selects one and provides its API key through the environment. The redactor is
deterministic and supports common English/French contact and identity labels;
it is best-effort and cannot guarantee complete anonymization. Use heuristic
or Ollama mode for highly confidential resumes. Provider errors and LLM
diagnostics must redact API keys and must not echo resume content or matched
PII values.

Distance filtering is opt-in. When enabled, the configured origin and
non-remote job locations may be sent to the configured OpenStreetMap Nominatim
endpoint for geocoding, and successful results are cached in the local SQLite
database. Users operating in a sensitive environment should disable the
distance filter or configure a trusted internal geocoder with
`JOBFIT_GEOCODER_URL`.
