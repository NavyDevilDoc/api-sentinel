# API Sentinel

Automated API security checker. Drop it into any project, point it at your API, and get a color-coded terminal report with pytest-compatible exit codes. Zero security expertise required.

**The mental model:** an outlet power tester. Plug it in, it runs, it tells you what's broken, you unplug it.

## Installation

Requires Python 3.11+.

```bash
pip install -e .
```

For development (includes pytest, respx):

```bash
pip install -e ".[dev]"
```

For the local web UI (optional, see [Web UI](#web-ui-optional) below):

```bash
pip install -e ".[ui]"        # FastAPI + HTMX UI, runs on localhost
```

For LLM-powered narrative reports (optional):

```bash
pip install -e ".[gemini]"    # Google Gemini (default, free tier)
pip install -e ".[claude]"    # Anthropic Claude
pip install -e ".[openai]"    # OpenAI
pip install -e ".[llm]"       # All three cloud backends
# Ollama: no extra install needed (uses local HTTP API)
```

## Quickstart

### 1. Create a config file

Copy the example and edit it for your API:

```bash
cp sentinel_config.example.yaml sentinel_config.yaml
```

### 2. Set your API tokens

Create a `.env` file (or export the variables directly):

```bash
cp .env.example .env
# Edit .env with your actual API tokens
```

The config file references environment variable **names**, never raw token values:

```yaml
auth:
  token_primary: SENTINEL_TOKEN_A      # env var name, not the token itself
  token_secondary: SENTINEL_TOKEN_B    # optional; enables BOLA checks
```

### 3. Run the scan

```bash
sentinel
```

That's it. API Sentinel reads `sentinel_config.yaml` from the current directory, runs all enabled checks, and prints a color-coded report.

## Web UI (optional)

Prefer a browser over a config-file editor and memorized flags? Install the `[ui]` extra and launch the local UI.

```bash
pip install -e ".[ui]"
sentinel ui
```

The UI binds to `http://127.0.0.1:8765` by default, opens your browser automatically, and gives you:

- **Config viewer** at `/config` — read your `sentinel_config.yaml` with field-level visibility into the endpoint table, check settings, and which env var names are bound to which auth roles.
- **Config editor** at `/config/edit` — full HTMX-driven form for editing every field, adding/removing endpoint rows in-place, atomic-write to disk with optional `.bak` backup. Token fields are populated from your `SENTINEL_*` env vars — **the UI never accepts secret values as input**.
- **Scans** at `/scans` — kick off a scan, watch live progress via HTMX polling, browse a color-coded findings explorer once complete.
- **One-click actions on every finished scan** — severity filters (All / Critical only / Warning+ / Passing only), Download JSON (same schema as `sentinel scan --output json`), and Re-scan with same config.

### Launch flags

```bash
sentinel ui --port 9000              # bind to a different port
sentinel ui --no-browser             # don't auto-open the browser (headless / SSH)
sentinel ui --host 127.0.0.1         # the default; only loopback bind
```

If port 8765 is busy the launcher falls back to an OS-assigned free port and prints the URL prominently — no manual port hunting needed.

### Security model

The UI is **localhost-only by default** (binds `127.0.0.1`). No auth gate, no TLS, no remote access. It exists for local development; it is not a production-deploy surface.

The single hardest invariant: **the UI never accepts secret values as input.** Token fields are dropdowns populated server-side from env var names matching `SENTINEL_*`. A parametrized test (`tests/ui/test_env_var_isolation.py`) sets canary secret values and asserts no value appears in any response body or header across every UI route. New routes must be added to that test's `_ROUTES` list — the discipline is enforced in CI.

If you keep tokens in a `.env` file, the UI loads it once at startup (matching the CLI's behavior). Editing `.env` while the UI is running requires a UI restart to pick up the new values.

CI/CD integration continues to use the CLI exclusively — `sentinel scan` is the canonical pipeline entry point.

## CLI Reference

```
sentinel scan [--config PATH] [--output FORMAT] [--severity LEVEL]
              [--checks CATEGORIES] [--fail-on LEVEL]
              [--report llm] [--llm-backend BACKEND]

sentinel ui   [--host HOST] [--port PORT] [--no-browser]
sentinel init [--spec SPEC]    # reserved verb; OpenAPI auto-config coming
```

Bare `sentinel ...` (no subcommand) is equivalent to `sentinel scan ...` for backward compatibility with v0.1.0 invocations. CI scripts and existing usage do not need to change.

### Flags

| Flag | Values | Default | Description |
|---|---|---|---|
| `--config` | file path | `./sentinel_config.yaml` | Path to config file |
| `--output` | `terminal`, `json`, `both` | `terminal` | Output format |
| `--severity` | `critical`, `warning`, `info`, `all` | `all` | Minimum severity to display |
| `--checks` | `transport`, `headers`, `auth`, `authorization`, `rate_limit`, `input`, `all` | `all` | Check categories to run |
| `--fail-on` | `critical`, `warning`, `any` | `critical` | Exit code 1 threshold |
| `--report` | `llm` | none | Append LLM narrative report |
| `--llm-backend` | `gemini`, `claude`, `openai`, `ollama` | `gemini` | LLM provider for `--report llm` |

### Exit Codes

| Code | Meaning |
|---|---|
| 0 | All checks passed (or no findings at `--fail-on` threshold) |
| 1 | Findings exist at `--fail-on` threshold |
| 2 | Configuration error (missing file, invalid YAML, missing env var) |
| 3 | Network or connectivity error |

### Examples

Run all checks with terminal output:

```bash
sentinel
```

Run only transport and header checks:

```bash
sentinel --checks transport headers
```

Export JSON report alongside terminal output:

```bash
sentinel --output both
```

Fail the build on any warning or critical finding:

```bash
sentinel --fail-on warning
```

Show only critical findings:

```bash
sentinel --severity critical
```

Generate an LLM narrative report using Claude:

```bash
sentinel --report llm --llm-backend claude
```

Use a config file in a different location:

```bash
sentinel --config path/to/my_config.yaml
```

## Configuration Reference

The entire integration cost is one YAML file: `sentinel_config.yaml`.

### `meta` (required)

```yaml
meta:
  project: my-api                        # Project name (for reports)
  base_url: https://api.example.com      # Target API base URL
  timeout_seconds: 10                    # HTTP timeout (default: 10)
```

### `auth` (required)

```yaml
auth:
  token_primary: SENTINEL_TOKEN_A        # Env var name for primary token
  token_secondary: SENTINEL_TOKEN_B      # Env var name for secondary token (optional)
```

- Values are environment variable **names**, not raw tokens
- `token_secondary` is required only if `checks.authorization.enabled` is `true`
- Set the actual token values in your `.env` file or shell environment

### `endpoints` (required)

```yaml
endpoints:
  - path: /resource/{id}
    method: GET                          # HTTP method (default: GET)
    requires_auth: true                  # Needs auth token (default: true)
    test_ids: [1, 2, 3]                 # Values to substitute into {id}
    owned_by: token_primary              # Which token owns this resource
    rate_limit_sensitive: false           # Burst-test this endpoint (default: false)

  - path: /auth/login
    method: POST
    requires_auth: false
    rate_limit_sensitive: true
```

| Field | Type | Default | Description |
|---|---|---|---|
| `path` | string | (required) | URL path, supports `{id}` placeholder |
| `method` | string | `GET` | HTTP method |
| `requires_auth` | bool | `true` | Whether requests need an auth token |
| `test_ids` | list | `[]` | Values substituted into path placeholders |
| `owned_by` | string | `null` | `token_primary` or `token_secondary` (for BOLA checks) |
| `rate_limit_sensitive` | bool | `false` | Whether to burst-test this endpoint |

### `checks` (optional)

Each check category can be enabled/disabled independently. All are enabled by default.

#### Transport

```yaml
checks:
  transport:
    enabled: true
    require_https_redirect: true         # Test HTTP -> HTTPS redirect
    min_tls_version: "1.2"               # Minimum acceptable TLS version
    check_cert_expiry_days: 30           # Warn if cert expires within N days
```

**What it checks:** HTTPS enforcement, HTTP-to-HTTPS redirect, TLS version, certificate expiry, HSTS header.

#### Headers

```yaml
checks:
  headers:
    enabled: true
    required:                            # Headers that MUST be present
      - Strict-Transport-Security
      - X-Content-Type-Options
      - X-Frame-Options
    forbidden_leakage:                   # Headers that MUST NOT be present
      - X-Powered-By
      - Server
```

**What it checks:** Required security headers present, forbidden info-leaking headers absent, CORS wildcard detection.

#### Auth

```yaml
checks:
  auth:
    enabled: true
```

**What it checks:** Token rejection testing (no token, empty token, malformed token expect 401). Valid token acceptance. Public endpoint accessibility. 401 vs 403 correctness.

#### Authorization (BOLA)

```yaml
checks:
  authorization:
    enabled: true                        # Requires token_secondary
```

**What it checks:** Cross-user resource access. Tests whether User A's token can access resources owned by User B. Requires `owned_by` on endpoints and `token_secondary` in auth config.

#### Rate Limit

```yaml
checks:
  rate_limit:
    enabled: true
    request_burst: 20                    # Number of concurrent requests
    burst_window_seconds: 5              # Timeout for the burst
```

**What it checks:** Sends `request_burst` concurrent requests to endpoints flagged with `rate_limit_sensitive: true`. Expects 429 responses with Retry-After header.

#### Input Handling

```yaml
checks:
  input_handling:
    enabled: true
    max_payload_kb: 1024                 # Max payload size in KB (default: 1 MB)
```

**What it checks:** Oversized payload rejection (expects 413/400). Malformed Content-Type handling (expects 400/415). Injection probe strings in query parameters (SQL, XSS, SSTI, path traversal) -- expects 400, flags 500 as critical.

**Note:** The default 1 MB payload is enough to exercise rejection logic on most APIs. If you raise this much higher (e.g. 10+ MB) and the scan errors with a protocol-level exception, your hosting platform's edge layer may be choking on the upload. Either lower the value or be selective about which endpoints carry this check.

## OWASP API Security Top 10 Coverage

| OWASP Risk | Check Module | Coverage |
|---|---|---|
| BOLA (Broken Object Level Authorization) | authorization | Full |
| Broken Authentication | auth | Full |
| Broken Object Property Level Auth | authorization | Partial |
| Unrestricted Resource Consumption | rate_limit | Full |
| Broken Function Level Authorization | auth | Partial |
| Unrestricted Access to Sensitive Flows | rate_limit | Partial |
| SSRF | input_handling | Probe only |
| Security Misconfiguration | headers, transport | Full |
| Improper Inventory Management | headers | Partial |
| Unsafe Consumption of APIs | -- | Out of scope v1.0 |

## CI/CD Integration

API Sentinel is designed for CI/CD pipelines. The exit codes make it a drop-in quality gate.

### GitHub Actions

```yaml
- name: API Security Scan
  run: |
    pip install api-sentinel
    sentinel --config sentinel_config.yaml --fail-on critical
```

### GitLab CI

```yaml
security_scan:
  script:
    - pip install api-sentinel
    - sentinel --config sentinel_config.yaml --fail-on critical --output both
  artifacts:
    paths:
      - sentinel_report.json
```

### Generic Pipeline

```bash
# Fail the pipeline on any critical finding
sentinel --fail-on critical

# Fail on warnings too (stricter)
sentinel --fail-on warning

# Fail on any non-passing check (strictest)
sentinel --fail-on any

# Export JSON artifact for downstream consumption
sentinel --output json
```

The JSON export (`sentinel_report.json`) is structured for programmatic consumption:

```json
{
  "meta": { "tool": "api-sentinel", "version": "0.1.0", "project": "...", ... },
  "summary": { "total": 21, "passed": 18, "critical": 1, "warning": 2, ... },
  "results_by_category": { "transport": [...], "headers": [...], ... },
  "results": [...]
}
```

## LLM Report (Power User Feature)

Append a narrative security analysis generated by an LLM. The LLM reasons over the structured findings -- it does not make additional HTTP calls or re-test anything.

### Setup

1. Choose a backend and install its SDK (if needed):

```bash
pip install api-sentinel[gemini]     # Google Gemini (free tier available)
pip install api-sentinel[claude]     # Anthropic Claude
pip install api-sentinel[openai]     # OpenAI
# Ollama: no extra install (uses local HTTP API)
```

2. Set the API key:

```bash
# In .env or shell environment
SENTINEL_GEMINI_KEY=your-key-here
SENTINEL_CLAUDE_KEY=your-key-here
SENTINEL_OPENAI_KEY=your-key-here
# Ollama: no key needed -- just run `ollama serve`
```

3. Run with `--report llm`:

```bash
sentinel --report llm                              # Uses Gemini (default)
sentinel --report llm --llm-backend claude          # Uses Claude
sentinel --report llm --llm-backend ollama          # Uses local Ollama
```

The LLM report produces three sections:
- **Executive Summary** -- overall security posture and risk verdict
- **Prioritized Remediation** -- each finding with what/why/fix, ordered by severity
- **Business Risk Context** -- non-technical impact for each critical finding

Token values are redacted before findings are sent to the LLM.

## Troubleshooting

### "Config file not found" (exit code 2)

API Sentinel looks for `sentinel_config.yaml` in the current directory by default. Either:
- `cd` to the directory containing the file, or
- Use `--config path/to/sentinel_config.yaml`

### "Environment variable 'X' is not set" (exit code 2)

Your config references env var names that aren't set. Either:
- Create a `.env` file with the values (see `.env.example`)
- Export them in your shell: `export SENTINEL_TOKEN_A=your-token`

### "Authorization (BOLA) checks require 'token_secondary'" (exit code 2)

You have `checks.authorization.enabled: true` but no `token_secondary` in the auth section. Either:
- Add `token_secondary: YOUR_ENV_VAR` to the auth config, or
- Set `checks.authorization.enabled: false`

### Connection errors (exit code 3)

The target API is unreachable. Check that:
- The `base_url` is correct and the server is running
- You have network access to the target
- The timeout is sufficient (`meta.timeout_seconds`)

### "API Sentinel UI extras are not installed"

The web UI is an optional extra. Install it:

```bash
pip install -e ".[ui]"
```

### UI dropdown shows "no SENTINEL_* env vars in scope"

The UI loads env vars from `.env` (in the current working directory) plus your shell environment at launch time. If you set a new `SENTINEL_*` variable AFTER the UI is already running, restart the UI to pick it up. Persistent env vars (set via `[Environment]::SetEnvironmentVariable(..., "User")` on Windows or `~/.bashrc` on Unix) require a new shell session before launching the UI.

### "Port 8765 in use, listening on N instead"

The launcher's auto-fallback: another process holds 8765, so an OS-assigned free port is used instead. The actual URL is printed prominently. To force a specific port: `sentinel ui --port 9000`.

### LLM report: "requires additional dependencies"

Install the SDK for your chosen backend:

```bash
pip install api-sentinel[gemini]   # or [claude], [openai]
```

### LLM report: "Cannot connect to Ollama"

Ollama must be running locally. Start it with:

```bash
ollama serve
```

### Rate limit checks show no results

Only endpoints with `rate_limit_sensitive: true` are burst-tested. Add this flag to endpoints you want to test.

### BOLA checks show no results

BOLA checks require endpoints with both `owned_by` set and `requires_auth: true`, plus `token_secondary` configured. Verify all three are present.

## Project Structure

```
api_sentinel/
├── sentinel_config.yaml          # Your API config (not committed)
├── sentinel_config.example.yaml  # Example config (committed)
├── .env                          # Your tokens (not committed)
├── .env.example                  # Example env vars (committed)
├── pyproject.toml
│
├── sentinel/
│   ├── cli.py                    # CLI entry point (scan/ui/init dispatch)
│   ├── config.py                 # Pydantic config loader
│   ├── runner.py                 # Check orchestrator
│   ├── reporter.py               # Terminal + JSON output
│   ├── checks/
│   │   ├── base.py               # CheckResult model, BaseCheck ABC
│   │   ├── transport.py          # HTTPS, TLS, certs, HSTS
│   │   ├── headers.py            # Security headers, info leakage
│   │   ├── auth.py               # Token validation, 401/403
│   │   ├── authorization.py      # BOLA, cross-user access
│   │   ├── rate_limit.py         # Burst testing, 429 detection
│   │   └── input_handling.py     # Payload size, injection probes
│   ├── llm/
│   │   ├── prompt.py             # Structured LLM prompt
│   │   ├── gemini.py             # Google Gemini backend
│   │   ├── claude.py             # Anthropic Claude backend
│   │   ├── openai_backend.py     # OpenAI backend
│   │   └── ollama.py             # Ollama local backend
│   ├── utils/
│   │   ├── http_client.py        # Shared httpx client factory
│   │   └── env_loader.py         # Safe env var resolution
│   └── ui/                       # Optional [ui] extra (FastAPI + HTMX + Jinja)
│       ├── server.py             # FastAPI app factory
│       ├── launcher.py           # `sentinel ui` entry point
│       ├── routes/               # Page + HTMX fragment routes
│       ├── services/             # config_io, env_vars, form_parser, scan_runner
│       ├── templates/            # Jinja2 templates (+ partials/)
│       └── static/               # htmx.min.js (vendored), styles.css
│
└── tests/
    ├── conftest.py               # Shared fixtures
    ├── unit/                     # CLI, config, reporter, LLM tests
    ├── security/                 # Per-category check tests
    └── ui/                       # UI route + invariant tests
```

## License

MIT
