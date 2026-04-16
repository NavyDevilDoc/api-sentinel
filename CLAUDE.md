# CLAUDE.md — API Sentinel
**Session Context & Architecture Governance**
Version: 0.1.0-spec | Author: Jeremy | Last Updated: 2026-04-15

---

## What This Project Is

API Sentinel is a standalone, developer-native Python CLI tool that performs automated
API security checks against any HTTP API. It drops into any project folder, reads a
single YAML config file, and produces a color-coded terminal report with pytest-compatible
exit codes. Zero security expertise required to operate.

**The mental model:** an outlet power tester. Plug it in, it runs, it tells you what's
broken, you unplug it. Green light means pass. Red light means something needs fixing.

---

## Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| Language | Python 3.11+ | Primary language, ML/backend alignment |
| HTTP client | httpx | Async-capable, better timeout control than requests |
| Config validation | pydantic | All data shapes — config, results, reports |
| Terminal output | rich | Color, tables, panels — no server process required |
| Config format | PyYAML | Single sentinel_config.yaml is the integration cost |
| Test runner | pytest | Checks are first-class pytest tests |
| CLI | argparse | Stdlib, no extra dependency |

---

## Hard Constraints

These do not get negotiated away during implementation.

1. **No mandatory external services.** Runs entirely locally against a target URL.
   No cloud accounts, no paid APIs required for core operation.
2. **Single config file is the entire integration cost.** One `sentinel_config.yaml`
   per project. That is the plug.
3. **pytest is the test runner backbone.** Every check is a pytest test. The tool runs
   standalone via CLI or as a test suite via `pytest tests/security/`.
4. **httpx over requests.** Async-capable, HTTP/2 support, precise timeout control.
5. **Pydantic for all data shapes.** Config validation, result models, report structures.
   No raw dicts passed between modules.
6. **Fail loudly and specifically.** A failed check must state exactly what failed,
   what was expected, and what was received. No vague messages.
7. **Secrets never in config files.** Tokens and keys are loaded from environment
   variables. The YAML references env var names, not values.
8. **Cybersecurity best practices from day one.** A tool that tests security cannot
   itself be a security liability.
9. **CLI only. No GUI, no Streamlit, no web server.** The outlet tester does not
   require a running process to give you a result.

---

## Explicit Out of Scope (v1.0)

- GUI or web interface of any kind
- OAuth 2.0 flow automation (redirect-based flows)
- GraphQL schema introspection and depth attacks
- WebSocket security testing
- Database-layer testing
- Authenticated scanning of third-party APIs you do not own
- Any feature requiring a paid or rate-limited external service in the core path

---

## Directory Structure

```
api_sentinel/
├── CLAUDE.md                        ← this file
├── PROGRESS.md                      ← newest-first prepended build log
├── README.md                        ← generated in Phase 9
├── sentinel_config.yaml             ← user-provided, project-specific
├── sentinel_config.example.yaml     ← committed to repo, tokens redacted
├── pyproject.toml
├── .env.example
│
├── sentinel/
│   ├── __init__.py
│   ├── cli.py                       ← entry point, argparse
│   ├── config.py                    ← pydantic config loader and validator
│   ├── runner.py                    ← orchestrates checks, collects results
│   ├── reporter.py                  ← rich terminal output + JSON export
│   │
│   ├── checks/
│   │   ├── __init__.py
│   │   ├── base.py                  ← CheckResult model, base class
│   │   ├── transport.py             ← HTTPS, TLS, redirects, cert expiry
│   │   ├── headers.py               ← security headers, info leakage
│   │   ├── auth.py                  ← token validation, 401/403 behavior
│   │   ├── authorization.py         ← BOLA, cross-user access
│   │   ├── rate_limit.py            ← throttling, 429 behavior
│   │   └── input_handling.py        ← payload size, malformed input, injection probes
│   │
│   └── utils/
│       ├── http_client.py           ← shared httpx client, retry logic
│       └── env_loader.py            ← safe env var resolution
│
└── tests/
    ├── conftest.py
    ├── unit/
    │   ├── test_config.py
    │   └── test_reporter.py
    └── security/
        ├── test_transport.py
        ├── test_headers.py
        ├── test_auth.py
        ├── test_authorization.py
        ├── test_rate_limit.py
        └── test_input_handling.py
```

---

## Config Schema

```yaml
# sentinel_config.yaml

meta:
  project: my-api
  base_url: https://api.example.com
  timeout_seconds: 10

auth:
  # Values are env var names — never raw tokens
  token_primary: SENTINEL_TOKEN_A
  token_secondary: SENTINEL_TOKEN_B     # optional; enables BOLA checks

endpoints:
  - path: /resource/{id}
    method: GET
    requires_auth: true
    test_ids: [1, 2, 3]
    owned_by: token_primary

  - path: /user/profile
    method: GET
    requires_auth: true
    owned_by: token_primary

  - path: /auth/login
    method: POST
    requires_auth: false
    rate_limit_sensitive: true

checks:
  transport:
    enabled: true
    require_https_redirect: true
    min_tls_version: "1.2"
    check_cert_expiry_days: 30

  headers:
    enabled: true
    required:
      - Strict-Transport-Security
      - X-Content-Type-Options
      - X-Frame-Options
    forbidden_leakage:
      - X-Powered-By
      - Server

  auth:
    enabled: true

  authorization:
    enabled: true                       # requires token_secondary

  rate_limit:
    enabled: true
    request_burst: 20
    burst_window_seconds: 5

  input_handling:
    enabled: true
    max_payload_kb: 10240
```

---

## CheckResult Model

Every check produces exactly one `CheckResult`. This is the atom of the system.
The reporter, the JSON exporter, and the LLM report module all consume this model.

```python
class Severity(str, Enum):
    CRITICAL = "critical"
    WARNING  = "warning"
    INFO     = "info"
    PASS     = "pass"

class CheckResult(BaseModel):
    check_id:        str            # e.g. "transport.https_redirect"
    name:            str            # human-readable label
    severity:        Severity
    passed:          bool
    detail:          str            # what was actually found
    expected:        str            # what should have been found
    recommendation:  str            # one-line fix guidance
    endpoint:        str | None     # which endpoint, if applicable
    response_code:   int | None
    latency_ms:      float | None
```

---

## CLI Interface

```
usage: sentinel [-h] [--config PATH] [--output {terminal,json,both}]
                [--severity {critical,warning,info,all}]
                [--checks {transport,headers,auth,rate_limit,input,all}]
                [--fail-on {critical,warning,any}]
                [--report {llm}]
                [--llm-backend {gemini,claude,openai,ollama}]

Options:
  --config PATH          Path to sentinel_config.yaml (default: ./sentinel_config.yaml)
  --output               Output format: terminal, json, or both (default: terminal)
  --severity             Minimum severity level to display (default: all)
  --checks               Run only specific check categories (default: all)
  --fail-on              Exit code 1 if findings exist at this severity (default: critical)
  --report llm           Append an LLM-generated narrative report (Power User feature)
  --llm-backend          LLM provider for --report llm (default: gemini)
```

**Exit codes:**

| Code | Meaning |
|---|---|
| 0 | All checks passed, or no findings at --fail-on threshold |
| 1 | One or more findings at --fail-on threshold |
| 2 | Configuration error |
| 3 | Network or connectivity error |

Exit code behavior enables CI/CD integration — a pipeline fails on critical findings
without any extra tooling.

---

## Terminal Report Format (Rich)

```
╔══════════════════════════════════════════════════════╗
║              API SENTINEL — SECURITY REPORT          ║
║  Target: https://api.example.com      2026-04-15     ║
╚══════════════════════════════════════════════════════╝

  TRANSPORT                                    [5/5 passed]
  ✅  HTTPS enforced                            PASS
  ✅  TLS 1.3 detected                          PASS
  ✅  Certificate valid (expires 182 days)      PASS
  ✅  HSTS header present                       PASS
  ✅  HTTP redirects to HTTPS                   PASS

  HEADERS                                      [3/5 passed]
  ✅  X-Content-Type-Options present            PASS
  ✅  X-Frame-Options present                   PASS
  ⚠️   CORS allows wildcard origin              WARNING
  ❌  X-Powered-By leaks framework version      CRITICAL
  ⚠️   Server header exposes nginx version      WARNING

─────────────────────────────────────────────────────────
  SUMMARY    Critical: 1   Warnings: 2   Passed: 18/21
─────────────────────────────────────────────────────────

  CRITICAL FINDINGS
  ┌─────────────────────────────────────────────────────┐
  │ headers.xpoweredby                                  │
  │ X-Powered-By header exposes: Express 4.18.2         │
  │ Fix: app.disable('x-powered-by') or use helmet.js   │
  └─────────────────────────────────────────────────────┘
```

---

## Build Phases

Implement in strict sequence. Each phase is independently testable before
proceeding to the next.

### Phase 1 — Skeleton
Config loader with pydantic validation. `CheckResult` model. CLI entry point
with argparse. Rich reporter stub (static output). pytest conftest.
No HTTP calls. Validates the entire project structure.

### Phase 2 — Transport & Headers
First live HTTP checks. Stateless, no auth required. Validates the httpx client
and establishes the check pattern all future phases will follow.
Checks: HTTPS enforcement, HTTP redirect, TLS version, certificate expiry,
HSTS, CORS, information-leaking headers.

### Phase 3 — Authentication Checks
Protected endpoint probing. Token rejection testing (expired, malformed, empty,
missing). 401 vs 403 correctness. Auth endpoint behavior.

### Phase 4 — Rate Limiting
Burst testing using async httpx concurrency. Validates 429 response and
Retry-After header. Separate handling for auth endpoints flagged as
`rate_limit_sensitive`.

### Phase 5 — Authorization (BOLA)
Cross-user resource access testing. Requires `token_secondary` to be configured.
Most impactful check category. Tests whether User A's token can access resources
owned by User B.

### Phase 6 — Input Handling
Oversized payload rejection. Malformed content-type handling. Basic injection
probe strings in query params (expects 400, flags 500 as critical finding).

### Phase 7 — JSON Export
`--output json` and `--output both` fully implemented. Structured JSON export
of all `CheckResult` objects. This is the data contract that Phase 8 consumes.
CI integration documented in PROGRESS.md.

### Phase 8 — LLM Report (Power User Feature)
Optional flag: `--report llm`. Serializes all `CheckResult` objects to JSON
and sends to a configured LLM backend. Returns a narrative report appended
below the Rich terminal output. User supplies their own API key via environment
variable — no quota burden on the tool.

**LLM backends supported:**

| Backend | Env var for key | Notes |
|---|---|---|
| gemini | SENTINEL_GEMINI_KEY | Default; free tier usable for dev |
| claude | SENTINEL_CLAUDE_KEY | Best narrative quality |
| openai | SENTINEL_OPENAI_KEY | |
| ollama | n/a (local) | No key required; fully offline |

The prompt sent to the LLM is structured, not freeform. It includes the full
findings JSON, severity counts, and a fixed instruction to produce: an executive
summary, prioritized remediation steps, and business risk context for each
critical finding. The LLM does not make additional HTTP calls or re-test anything.
It reasons only over the structured findings.

### Phase 9 — User Guide & README
Full `README.md` written as a proper user guide. Covers: installation,
quickstart, full config reference, all CLI flags with examples, CI/CD
integration walkthrough, the LLM report feature setup, and a troubleshooting
section. Complexity of CLI commands scales proportionally with program complexity —
the guide makes the tool fully accessible without reading source code.

---

## OWASP API Security Top 10 Coverage Map

| OWASP Risk | Check Module | Coverage |
|---|---|---|
| BOLA (Broken Object Level Authorization) | authorization.py | Full (with token_secondary) |
| Broken Authentication | auth.py | Full |
| Broken Object Property Level Auth | authorization.py | Partial |
| Unrestricted Resource Consumption | rate_limit.py | Full |
| Broken Function Level Authorization | auth.py | Partial |
| Unrestricted Access to Sensitive Flows | rate_limit.py | Partial |
| SSRF | input_handling.py | Probe only |
| Security Misconfiguration | headers.py, transport.py | Full |
| Improper Inventory Management | headers.py | Partial |
| Unsafe Consumption of APIs | Out of scope v1.0 | — |

Target: ~65-70% of OWASP Top 10 automatable surface covered at v1.0.

---

## Security Posture of the Tool Itself

- All secrets resolved from environment variables via `env_loader.py`
- No credentials written to disk, logs, or JSON export
- httpx configured with explicit timeouts on every request — no hanging calls
- Injection probe strings are static and read-only — the tool never executes
  responses it receives
- JSON export redacts all token values before writing

---

## Session Reload Checklist

When starting a new Claude Code session on this project:

1. Read `CLAUDE.md` (this file) — architecture and constraints
2. Read `PROGRESS.md` — current phase, last completed task, open decisions
3. Do not propose architecture changes without flagging them explicitly
4. Propose structure before writing implementation code
5. All new checks follow the `CheckResult` model exactly
6. All tests go in `tests/security/` and are runnable via `pytest`
