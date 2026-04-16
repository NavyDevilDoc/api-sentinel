# PROGRESS.md — API Sentinel Build Log

Newest entries first.

---

## Phase 9 — User Guide & README

**Status:** Complete
**Date:** 2026-04-16

### Completed

- Full README.md written as a proper user guide (not just a project description)
- Sections: Installation, Quickstart (3-step), CLI Reference (all flags with examples),
  Configuration Reference (every field documented), OWASP Coverage Map,
  CI/CD Integration (GitHub Actions, GitLab CI, generic pipeline), LLM Report Setup,
  Troubleshooting (8 common issues with solutions), Project Structure
- CLI examples scale from simple (`sentinel`) to advanced (`--report llm --llm-backend claude`)
- Config reference documents every field with types, defaults, and what each check category tests
- CI/CD section shows JSON artifact export and `--fail-on` usage for pipeline gates
- Troubleshooting section covers all exit codes and common setup issues

### Decisions

- **README as user guide, not developer docs:** The spec says "the guide makes the tool fully
  accessible without reading source code." Every section is user-facing. No internal architecture
  discussion — that stays in CLAUDE.md.
- **3-step quickstart:** Copy config, set tokens, run. Minimum viable path to a working scan.
- **OWASP coverage table included:** Directly from the spec, showing which checks map to which
  OWASP API Top 10 risks. Helps users understand the security value.

---

## Phase 8 — LLM Report

**Status:** Complete
**Date:** 2026-04-15

### Completed

- LLM report package (sentinel/llm/) with dispatcher, prompt template, and 4 backends
- Structured prompt template (sentinel/llm/prompt.py) producing: executive summary,
  prioritized remediation steps, and business risk context per critical finding
- Backend dispatcher (sentinel/llm/__init__.py) with lazy SDK import, API key resolution,
  and LLMBackendError exception hierarchy
- Gemini backend (gemini-2.0-flash, sync SDK wrapped in asyncio.to_thread)
- Claude backend (claude-sonnet-4-20250514, native async via anthropic SDK)
- OpenAI backend (gpt-4o-mini, native async via openai SDK)
- Ollama backend (llama3.2, httpx POST to localhost:11434, no SDK needed)
- CLI integration: `--report llm --llm-backend {gemini,claude,openai,ollama}` renders
  narrative in a Rich Panel below the terminal report
- Optional dependencies in pyproject.toml: `pip install api-sentinel[claude]` etc.
- Token redaction applied before findings reach the LLM (reuses build_report_data + _redact_tokens)
- Extracted build_report_data() from render_json_report() for shared use by JSON export and LLM feature
- _collect_redact_values() helper in CLI to avoid duplication between JSON and LLM code paths
- 16 new tests all working without LLM SDKs or network access

### Issues Addressed

- **importlib.import_module mock interference:** Initially patched `importlib.import_module`
  globally in tests, which broke subsequent `patch()` target resolution (unittest.mock uses
  importlib internally). Fixed by using `patch.dict("sys.modules", {...})` to inject mock
  backend modules directly, avoiding any importlib patching. For the "missing SDK" test,
  setting the module to `None` in sys.modules triggers `ImportError` on import.
- **Second asyncio.run() call in CLI:** The `--report llm` code path calls `asyncio.run()`
  a second time (after the initial `run_checks()` call). This works because the first event
  loop completes fully before the second starts. Acceptable for v1; can be refactored to a
  single async main() later if needed.

### Decisions

- **LLM SDKs as optional dependencies:** Core tool never requires LLM SDKs. Users install
  what they need: `pip install api-sentinel[gemini]`, `[claude]`, `[openai]`, or `[llm]` for all.
  Ollama needs no extra deps (uses httpx).
- **Lazy SDK import with clear error messages:** If the SDK isn't installed, the user sees
  exactly which pip command to run. If the API key isn't set, they see the env var name.
- **LLM failures are non-fatal:** The security report is the primary output. LLM errors
  print a yellow warning and don't change the exit code.
- **120-second timeout for Ollama:** Local LLM inference is slow. Fixed timeout is
  acceptable for v1; could be configurable later.
- **Structured prompt, not freeform:** Fixed instruction set with severity counts and
  findings JSON. LLM produces exactly 3 sections. Constrained to 1500 words, markdown format.

### Testing

- 152 tests total (16 new): 6 prompt template tests, 7 dispatcher tests (routing, missing SDK,
  missing key, unknown backend, ollama no key, exception wrapping, redaction), 2 ollama backend
  tests (POST verification, connection refused), 1 edge case (empty results)
- All tests work without any LLM SDK installed
- Uses patch.dict("sys.modules") for mock backend injection, respx for Ollama HTTP mocking
- Zero regressions across Phases 1-7

---

## Phase 7 — JSON Export

**Status:** Complete
**Date:** 2026-04-15

### Completed

- Full `render_json_report()` implementation replacing Phase 1 stub (sentinel/reporter.py)
- Structured JSON output with 4 top-level sections: `meta`, `summary`, `results_by_category`, `results`
- `meta` section: tool name, version, project name, base_url, timestamp, duration_ms
- `summary` section: total/passed/critical/warning/info counts, checks_run, checks_skipped
- `results_by_category`: CheckResults grouped by category prefix (transport, headers, etc.)
- `results`: flat list of all CheckResult dicts (the Phase 8 LLM data contract)
- `_redact_tokens()` function: replaces known token values with `[REDACTED]` in all string fields
- CLI updated to pass `config` and resolved token values to the JSON export
- `--output json` writes file and prints path; `--output both` renders terminal + writes JSON
- 7 new tests covering JSON structure, meta fields, summary counts, category grouping, redaction, empty results

### Issues Addressed

- **No unforeseen issues.** The existing stub had the right shape — the expansion was additive.
  Token redaction uses exact-match replacement of resolved env var values, avoiding over-redaction.
  The `import os` in cli.py for best-effort token resolution is non-fatal (env vars may not
  be set at export time if checks resolved them independently).

### Decisions

- **Exact-match token redaction:** Scan all string fields for exact occurrences of resolved
  token values. This is precise — no regex guessing at what "looks like" a token. If the env
  vars aren't set at export time, redaction is simply skipped (best-effort).
- **Both flat and grouped results:** JSON includes both `results` (flat list) and
  `results_by_category` (grouped dict). The flat list is the data contract for Phase 8 LLM
  consumption. The grouped structure is convenient for programmatic consumers.
- **Config metadata in export:** Project name and base_url come from the config, not
  hardcoded. This makes the JSON self-describing for CI/CD artifact consumption.

### Testing

- 136 tests total (7 new): file creation, meta fields, summary counts, category grouping,
  flat results count, token redaction, empty results
- Zero regressions across Phases 1-6

### Open Questions for Phase 8

- **LLM prompt structure:** The spec says "structured, not freeform" prompt with findings JSON,
  severity counts, and fixed instructions for executive summary + prioritized remediation +
  business risk context. Need to design the exact prompt template.
- **Backend abstraction:** 4 backends (gemini, claude, openai, ollama) each have different
  SDKs/APIs. Need a clean abstraction that avoids installing all 4 SDKs as hard dependencies.

---

## Phase 6 — Input Handling

**Status:** Complete
**Date:** 2026-04-15

### Completed

- InputHandlingCheck module (sentinel/checks/input_handling.py) with 3 sub-check types:
  oversized payload rejection, malformed content-type handling, injection probe strings
- Endpoint-method-aware check selection: POST/PUT/PATCH get all 3 types; GET/DELETE get injection probes only
- 4 static injection probes: SQL (`' OR 1=1--`), XSS (`<script>alert(1)</script>`),
  SSTI (`{{7*7}}`), path traversal (`../../../etc/passwd`) — all read-only, never executed
- Probes sent as URL-encoded query parameters (`?probe={encoded_payload}`)
- Oversized payload: generates `(max_payload_kb + 1) * 1024` bytes, expects 413/400
- Malformed content-type: sends `text/plain` body to JSON endpoint, expects 400/415
- Token resolution and auth header handling for authenticated endpoints
- Registered in CHECK_REGISTRY — all 6 check categories now implemented
- Full test suite: 22 tests with small max_payload_kb (1 KB) for fast test execution

### Issues Addressed

- **No unforeseen issues.** The established patterns carried over cleanly. Using
  `url__startswith=` in respx route matching handled query-param-appended URLs without issues.
  Small `max_payload_kb` in test fixtures avoids generating large payloads during testing.

### Decisions

- **Injection 200 = INFO, not CRITICAL:** A 200 means the server accepted the probe without
  crashing. The tool cannot determine if the input was used dangerously (would need DB/DOM
  inspection). INFO signals "missing input validation" without overclaiming. 500 is the real
  danger — unhandled input reaching the backend.
- **Malformed CT 200 = WARNING, not CRITICAL:** Some APIs legitimately accept text/plain.
  500 on malformed CT is CRITICAL (server crash).
- **401/403/404 on all checks = PASS:** If auth or routing rejects the request before input
  processing, that's acceptable behavior regardless of the input payload.
- **All endpoints tested:** Unlike rate_limit (only `rate_limit_sensitive` endpoints), input
  handling tests all endpoints. Every endpoint should handle bad input gracefully.

### Testing

- 129 tests total (22 new): 1 token resolution, 5 oversized payload (413/400/200/500/size verification),
  4 malformed CT (415/400/200/500), 6 injection probes (400/200/500/401/4-probes/GET endpoint),
  3 endpoint selection (POST all types/GET injection only/public no auth), 3 full integration
- Zero regressions across all prior phases
- No remaining placeholder tests — all 6 security test files fully implemented

---

## Phase 5 — Authorization (BOLA)

**Status:** Complete
**Date:** 2026-04-15

### Completed

- AuthorizationCheck module (sentinel/checks/authorization.py) with cross-user access testing
- Dual token resolution: both token_primary and token_secondary resolved at run start
- Token mapping: `owned_by` field determines which token is the "owner" and which is the "other"
- Owner access verification: confirms resource owner can access their own resource
- Cross-user access probe: tests whether the "other" token can access resources it shouldn't
- BOLA detection: 2xx from the other user's token = CRITICAL BOLA finding
- Full test suite: 16 tests covering token resolution, endpoint filtering, owner access,
  cross-user access, token mapping verification, and integration

### Issues Addressed

- **No unforeseen issues.** The dual-token resolution pattern and per-request header overrides
  worked cleanly. Token mapping via `owned_by` field was straightforward.

### Decisions

- **Cross-user 500 = WARNING, not CRITICAL:** A server error on cross-user access is suspicious
  but not a confirmed BOLA vulnerability. It could be a different bug triggered by the token.
- **Cross-user 404 = PASS:** Resource appearing not to exist for the other user is correct
  behavior — some APIs hide resources rather than returning 403.
- **Both tokens resolved upfront:** If either token fails to resolve, the entire check returns
  a single CRITICAL error and exits early. No partial execution.
- **Only owned_by + requires_auth endpoints tested:** Endpoints without `owned_by` or with
  `requires_auth=False` are skipped — BOLA testing requires both authentication and resource ownership.

### Testing

- 108 tests total at Phase 5 completion (16 new): 3 token resolution (missing primary/secondary/both),
  2 endpoint filtering (no BOLA endpoints/unresolvable path), 4 owner access (200/404/403/connection error),
  5 cross-user access (403/401/404/200-BOLA/500), 2 token mapping (primary→secondary, secondary→primary),
  2 full integration (result count/unique IDs)
- Zero regressions across Phases 1-4

### Open Questions for Phase 7

- **JSON export token redaction:** The spec says "JSON export redacts all token values before
  writing." Need to determine what token-like patterns to redact — Authorization header values
  in results? Or just ensure no raw tokens appear in CheckResult fields?

---

## Phase 4 — Rate Limiting

**Status:** Complete
**Date:** 2026-04-15

### Completed

- RateLimitCheck module (sentinel/checks/rate_limit.py) with async burst testing
- Concurrent request burst via `asyncio.create_task()` + `asyncio.wait(timeout=burst_window_seconds)`
- Response analysis: 429 detection (PASS), all-2xx detection (CRITICAL), mixed non-429 errors (CRITICAL)
- Retry-After header validation on 429 responses (PASS if present, WARNING if missing)
- Token resolution for authenticated rate-limited endpoints
- Endpoint filtering: only `rate_limit_sensitive=True` endpoints are burst-tested
- Refactored shared helpers (`endpoint_slug()`, `resolve_path()`) from auth.py into checks/base.py
- Full test suite: 16 tests covering all burst scenarios

### Issues Addressed

- **Shared helper refactor:** `_endpoint_slug()` and `_resolve_path()` were duplicated concerns
  between auth.py and rate_limit.py. Moved to checks/base.py as public functions `endpoint_slug()`
  and `resolve_path()`, updated auth.py and its tests. Required adding a runtime `EndpointConfig`
  import to base.py (no circular dependency since config.py doesn't import from base.py).
- **Async concurrency testing with respx:** Used callable `side_effect` with `itertools.count()`
  to control how many 200s vs 429s are returned, regardless of asyncio scheduling order. This
  avoids test flakiness from nondeterministic concurrent execution order.

### Decisions

- **429 only, not 403:** Rate limiting must use proper HTTP 429 status code. Accepting 403
  as rate limiting would mask real authorization issues flagged by Phase 3.
- **Fire all at once, no pacing:** `burst_window_seconds` is the timeout for `asyncio.wait()`,
  not a pacing interval. This is the hardest test of rate limiting and simplest to implement.
- **One burst per endpoint:** No cross-endpoint rate limit detection. Each sensitive endpoint
  gets its own independent burst.
- **Cancel pending tasks after timeout:** Uses `asyncio.wait()` with done/pending sets.
  Pending tasks are cancelled; only completed responses are analyzed.

### Testing

- 91 tests total (16 new rate limit + 76 existing, with -1 placeholder replaced):
  no sensitive endpoints (INFO), token resolution, public vs authed endpoints,
  429 detection (pass/all-200/first-429/non-429-errors), Retry-After (present/missing/no-429),
  connection errors, path resolution, auth header verification, burst count validation, unique check_ids
- Shared helper refactor verified: all 27 auth tests pass with updated imports
- Zero regressions across all prior phases

### Open Questions for Phase 5

- **BOLA cross-user access testing:** The spec says "User A's token can access resources owned
  by User B." Need to determine how `owned_by` field maps to which token is used. If endpoint
  has `owned_by: token_primary`, the secondary token should NOT be able to access it.
- **Secondary token resolution:** Phase 5 will need `token_secondary` resolved for the first time.

---

## Phase 3 — Authentication Checks

**Status:** Complete
**Date:** 2026-04-15

### Completed

- AuthCheck module (sentinel/checks/auth.py) with per-endpoint authentication probing
- Token resolution via `resolve_env_var()` inside the check (no runner signature changes)
- Path template resolution: `{id}` placeholders substituted with first test_id via regex
- Endpoint slug generation for unique check_ids (`/resource/{id}` -> `resource_id`)
- Protected endpoint checks (4 per endpoint): no token, empty token, malformed token, valid token
- Public endpoint checks (2 per endpoint): accessible without auth, stable when auth is sent
- 401 vs 403 nuance: 403 for invalid auth is WARNING (wrong but not dangerous), 200 is CRITICAL (auth bypass)
- `_expect_401_result()` shared helper for consistent token rejection evaluation
- AuthCheck registered in runner.py CHECK_REGISTRY
- Full test suite: 27 tests covering all scenarios

### Issues Addressed

- **No unforeseen issues in Phase 3.** The established patterns from Phase 2 (respx mocking,
  per-request header overrides, BaseCheck interface) worked cleanly. Token resolution via
  `resolve_env_var()` inside the check avoided any runner modifications. The `client.request(method, path)`
  approach for HTTP method dispatch worked natively with httpx.

### Decisions

- **Token resolved inside the check, not the runner:** `AuthCheck.run()` calls `resolve_env_var()`
  directly. This keeps the BaseCheck.run() signature unchanged and avoids coupling the runner
  to auth-specific concerns. If the env var is missing, a single CRITICAL result is returned
  and the check exits early.
- **Per-request header overrides instead of multiple clients:** The shared unauthenticated
  client is reused. Each request passes explicit `headers={"Authorization": "Bearer ..."}` or
  omits the header entirely. httpx merges per-request headers with client defaults.
- **403 as WARNING for token rejection:** The spec calls for "401 vs 403 correctness" testing.
  Returning 403 for missing/invalid credentials is semantically wrong (403 means "authenticated
  but not authorized") but less dangerous than returning 200. Hence WARNING, not CRITICAL.
- **Public endpoint auth stability check:** Sending a valid token to a public endpoint
  shouldn't cause a 500. This catches APIs that break on unexpected Authorization headers.
  5xx is WARNING; other codes are PASS.

### Testing

- 76 tests total (27 new): 5 helper tests, 2 token resolution, 1 path resolution,
  6 token rejection (no/empty/malformed + 401/200/403/connection-error), 4 valid token
  (200/404/401/403), 4 public endpoint, 2 method dispatch, 3 full integration
- All tests use `@pytest.mark.asyncio`, `respx.mock`, and `@patch("sentinel.checks.auth.resolve_env_var")`
- Zero regressions in Phase 1-2 tests

### Open Questions for Phase 4

- **Rate limiting burst strategy:** The spec says "async httpx concurrency" for burst testing.
  Need to determine whether to use `asyncio.gather()` with multiple concurrent requests or
  a semaphore-based approach. Also need to handle APIs that have different rate limit windows.
- **429 response detection:** Some APIs return 429 with `Retry-After` header, others return
  403 or custom status codes for rate limiting. Should we only check for 429 or be more flexible?

---

## Phase 2 — Transport & Headers

**Status:** Complete
**Date:** 2026-04-15

### Completed

- TransportCheck module (sentinel/checks/transport.py) with 5 sub-checks:
  HTTPS enforced, HTTP-to-HTTPS redirect, TLS version, certificate expiry, HSTS header
- HeadersCheck module (sentinel/checks/headers.py) with 3 sub-check categories:
  required headers, forbidden leakage headers, CORS wildcard detection
- `_tls_info()` helper for shared SSL socket connection (TLS version + cert in one call)
- `parse_hostname_port()` utility in http_client.py
- CHECK_REGISTRY in runner.py mapping category names to check classes
- Runner upgraded from stub to live check execution with httpx client lifecycle
- Full test suites for transport (18 tests) and headers (11 tests) using respx and unittest.mock

### Issues Addressed

- **TLS version detection (Phase 1 open question, resolved):** httpx does not expose the
  negotiated TLS version. Solved by using stdlib `ssl` + `socket` in a `_tls_info()` helper
  that opens a raw SSL socket, reads `ssock.version()` and `ssock.getpeercert()`, then
  returns both. Runs via `asyncio.run_in_executor()` to avoid blocking the event loop.
  TLS version and certificate expiry share a single connection.
- **Certificate expiry off-by-one in tests:** `_make_cert_dict(90)` creates a cert expiring
  90 days from now, but `(expiry - now).days` can round down to 89 depending on timing.
  Fixed by asserting against the threshold value rather than the exact day count.
- **HTTPS redirect requires separate client:** The shared httpx client has `base_url` set
  to the HTTPS URL, so it cannot issue HTTP requests. The redirect check creates a temporary
  `httpx.AsyncClient` with the full HTTP URL. Tests use `respx.mock` with the HTTP URL
  pattern to intercept these requests.

### Decisions

- **CHECK_REGISTRY pattern:** Simple dict mapping category name to check class. Explicit,
  no plugin discovery overhead. Future phases add entries.
- **HSTS in transport, not headers:** HSTS is fundamentally transport security. The spec's
  example report lists it under TRANSPORT. The headers module handles configurable
  required/forbidden lists.
- **Headers checked against base URL root (/):** Security headers are server-wide policy.
  Checking every endpoint adds noise without value.
- **CORS absent = no result:** When no `Access-Control-Allow-Origin` header exists, no CORS
  result is produced (rather than producing a PASS or INFO).
- **Connection errors produce CRITICAL results:** If the server is unreachable, TLS/cert/HSTS
  checks return CRITICAL failures with descriptive error messages. The redirect check returns
  WARNING since port 80 being closed may be intentional.

### Testing

- 50 tests total (28 new): 18 transport + 11 headers + 16 Phase 1 tests + 4 remaining placeholders
- Transport tests: HTTPS enforced (pass/fail), redirect (pass/fail/non-HTTPS/connection-refused/disabled),
  TLS version (pass/fail/exact-min), cert expiry (pass/fail/expired/unparseable), TLS connection error,
  HSTS (present/absent), full integration run
- Headers tests: required headers (present/missing/mixed), forbidden headers (absent/present/value-captured),
  CORS (wildcard/specific/absent), empty lists, full integration run
- All tests use respx for HTTP mocking and unittest.mock.patch for TLS/cert layer
- Zero regressions in Phase 1 tests

### Open Questions for Phase 3

- **Auth token resolution timing:** Tokens are env var names in config. When should they be
  resolved to actual values -- at runner startup or when each check needs them? Runner startup
  is simpler but fails fast even for checks that don't need auth.
- **401 vs 403 semantics:** The spec says to test "401 vs 403 correctness." Need to define
  what correct behavior is (401 for missing auth, 403 for valid auth but insufficient permissions).

---

## Phase 1 — Skeleton

**Status:** Complete
**Date:** 2026-04-15

### Completed

- pyproject.toml with all dependencies (httpx[http2], pydantic, rich, PyYAML, python-dotenv)
- Config loader with full pydantic validation (sentinel/config.py)
- CheckResult model, Severity enum, and BaseCheck ABC (sentinel/checks/base.py)
- CLI entry point with argparse and all flags (sentinel/cli.py)
- Rich reporter with header panel, per-category groups, summary, critical findings (sentinel/reporter.py)
- Runner stub with RunResult model and check resolution logic (sentinel/runner.py)
- HTTP client factory with explicit timeouts and HTTP/2 (sentinel/utils/http_client.py)
- Env loader with clear error messages (sentinel/utils/env_loader.py)
- pytest conftest with shared fixtures
- Unit tests for config loading and reporter output
- Security test placeholders for all 6 check categories
- Example config and .env files

### Issues Addressed

- **Windows cp1252 encoding (UnicodeEncodeError):** Rich's Unicode box-drawing characters
  and emoji icons fail on the Windows legacy terminal which uses cp1252 encoding. Fixed by:
  (1) replacing Unicode separators with ASCII dashes (`"-" * 60`), (2) replacing emoji
  severity icons with ASCII text icons (`X`, `!`, `*`, `i`), and (3) adding a
  `_make_console()` helper that wraps stdout with UTF-8 encoding on Windows. Rich's Panel
  falls back to ASCII box chars automatically on legacy terminals.
- **Rich ANSI codes in test assertions:** Reporter tests initially used `force_terminal=True`
  which injected ANSI escape sequences into captured output, causing string assertions like
  `"Critical: 1"` to fail. Fixed by using `force_terminal=False, highlight=False` for the
  test capture console.

### Decisions

- **Async from day one:** BaseCheck.run() is async def; CLI uses asyncio.run(). Avoids sync-to-async retrofit in Phase 4.
- **python-dotenv included:** .env.example is a deliverable, implying .env file support. Not in original tech table but necessary.
- **yaml.safe_load only:** Security tool must not have YAML deserialization vulnerabilities.
- **CLI --checks mapping:** `input` maps to `input_handling` explicitly in cli.py.

### Testing

- 22 tests total: 10 unit tests (config + reporter) + 6 security placeholders + 6 integration checks
- `pip install -e ".[dev]"` verified on Python 3.14.2 / Windows 11
- `sentinel --help` prints all flags correctly
- `sentinel --config sentinel_config.example.yaml` renders empty report, exit code 0
- `sentinel --config nonexistent.yaml` prints clear error, exit code 2
- All module imports verified clean (no circular dependencies)

### Open Questions for Phase 2

- **TLS version detection:** httpx does not expose negotiated TLS version on response objects. Will likely need raw ssl.SSLSocket connection alongside httpx for transport.tls_version check.
- **Severity.PASS semantics:** PASS is not a severity level but a result status. --fail-on and --severity filter logic must explicitly exclude PASS from "finding" counts.
