# Security policy

## Reporting a vulnerability

Please report security issues privately through
[GitHub's private vulnerability reporting](https://github.com/suchipizza/pigtail/security/advisories/new)
rather than a public issue. You should receive a response within a few days.

## Scope notes

- Pigtail runs locally with your API keys. Keys are read from environment variables or a local
  `.env` file and are never written to bundles, reports or logs (logs redact key-like strings).
- Reports are static HTML. They embed bundle data as JSON and render all text with escaping; links
  to sources are restricted to `http(s)` URLs. If you find a way to inject script through a source
  title or claim, please report it.
- Pigtail fetches third-party web pages. It does not execute their JavaScript.
