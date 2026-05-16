# PROGRESS.md — API Sentinel Build Log

Newest entries first.

---

## Real-World Iteration + Engine Polish (Path A Step 4 outcomes)

**Status:** Complete
**Date:** 2026-05-16

The "Step 4 — real-world iteration" item from the prior polish entry was
owner-driven. Outcomes are recorded here, followed by three small engine
hardening fixes that fell out of the testing.

### Real-world findings against animeintel.app

- **Deployment-topology discovery (the big lesson).** Initial scans
  pointed Sentinel at `https://animeintel.app` and got mysterious
  `405 Method Not Allowed` responses on every POST endpoint. A curl
  test against the same URL revealed `Server: Vercel` and
  `Content-Disposition: inline; filename="index.html"` — `animeintel.app`
  is the Vercel-hosted **frontend**, not the API. The API lives at the
  Railway URL (`anime-rec-engine.up.railway.app`). The frontend's JS
  knows where the API is; external scanners hitting the frontend domain
  for `/api/*` get the SPA fallback `index.html` instead.
  - This is a meaningful lesson for the README's troubleshooting section
    (followup: add a "scan target should be the API host, not the frontend
    host" entry).
- **CRITICAL: HSTS missing on the Railway backend.** The Vercel frontend
  has `Strict-Transport-Security: max-age=63072000`, but the Railway
  backend at `anime-rec-engine.up.railway.app` does not. Frontend HSTS
  doesn't protect direct API consumers — actionable finding for the
  owner to patch via FastAPI middleware.
- **CORS coverage is uneven across paths.** Vercel's frontend root (`/`)
  carries `Access-Control-Allow-Origin: *`. The `/api/*` paths on Vercel
  return no CORS headers at all (which is why a browser fetch from
  `localhost:8765` was blocked by preflight failure). The Sentinel CORS
  check only probes the root, so it flagged the `*` as a WARNING but
  didn't surface the per-path inconsistency. Followup: extend the CORS
  check to probe configured endpoints, not just `/`.
- **The 405 → 422 transition confirmed Sentinel works correctly.** Once
  pointed at the actual Railway backend, the POST endpoints
  (`/api/dna`, `/api/contact`, `/api/search`, `/api/search-feedback`)
  responded `422 Unprocessable Entity` to Sentinel's body-less probes —
  exactly the pydantic body validation we expected. Endpoints
  `/api/show` and `/api/search` returned `429` due to per-IP daily quota
  burn from heavy iteration testing, not security issues.

### Engine polish (three small fixes)

These came out of two failure modes observed in real-world iteration:
- `LocalProtocolError: 11` crashed the entire scan when the
  `input_handling` check sent a 10+ MB payload over HTTP/2 to Railway.
- The runner had no per-category exception isolation, so one check's
  exception aborted the whole scan.

#### Fix 1 — Per-category exception isolation in `runner.py`

Wrapped the per-category `await check.run(...)` call in `try/except
Exception`. On exception, the runner appends a CRITICAL `CheckResult`
with `check_id = f"{category}.unhandled_exception"`, then continues
with remaining categories. The exception's type name and message are
surfaced in `detail`; the recommendation suggests typical mitigations
(adjust max_payload_kb, disable the category, etc.). `BaseException`
deliberately not caught — `KeyboardInterrupt` and `SystemExit` should
still propagate.

- File: `sentinel/runner.py`
- Tests: `tests/unit/test_runner.py` (3 new)
  - One check raising surfaces as a single CRITICAL finding with the
    correct type and message in detail.
  - Other check categories still run when one fails.
  - `run_checks()` returns a `RunResult` rather than propagating the
    exception (this property is what keeps the UI scan-state machine
    from flipping to "error" on every recoverable check failure).

#### Fix 2 — Default `max_payload_kb`: 10240 → 1024

The 10 MB default was overkill — it tests edge infrastructure limits
more than application logic. 1 MB is enough to exercise the rejection
path on every API I can think of, and stays clear of HTTP/2 upload
issues on hosting platforms like Railway, Vercel, and Cloudflare. Users
who legitimately want to verify large-body handling can raise the
value explicitly.

- File: `sentinel/config.py` — `InputHandlingCheckConfig.max_payload_kb`
- Also updated: `sentinel_config.example.yaml`, README config reference,
  with a note on raising the value (and the protocol-issue caveat).

#### Fix 3 — `http2=True` → `http2=False` in the http client

HTTP/2 was an early "looks nice to have" default that turned out to add
real compatibility risk against edge-fronted hosts without adding any
check coverage value. Sentinel tests application behavior, not server
protocol behavior. Switching to HTTP/1.1 only is a one-line change that
eliminates an entire class of `LocalProtocolError` symptoms.

- File: `sentinel/utils/http_client.py`
- The `httpx[http2]` dependency in `pyproject.toml` is left alone —
  `h2` install footprint is negligible and removing it would be a
  user-visible packaging change for marginal gain.

### Testing

- 3 new tests in `tests/unit/test_runner.py`.
- Full suite: **317 passed, 0 failures** (314 prior + 3 runner regression).
- No security/UI tests touched by these changes — the change to
  `http2=False` could in theory affect respx-mocked tests, but all 152
  v0.1.0 security tests still pass cleanly.

### Followups recorded but not yet done

- Surface a clearer error for the "scanning the frontend host instead
  of the API host" footgun. Probably a README troubleshooting entry plus
  a heuristic in transport.py that flags `Content-Disposition: index.html`
  in response headers.
- Extend the CORS check to probe each configured endpoint, not just `/`.
- These join the existing two 🔴 Essentials in FUTURE_DEVELOPMENTS.md
  (EndpointConfig body support, AuthConfig scheme selector).

---

## Post-UI Polish — Path A Steps 1-3

**Status:** Complete (Step 4 — real-world iteration — is owner-driven)
**Date:** 2026-05-16

Wrapping up the project for public release in the order set by the "Next
Steps" block of the UI-7 entry. Path B (start `sentinel init` immediately)
was offered and explicitly deferred.

### Completed

- **Step 1 — `FUTURE_DEVELOPMENTS.md` updated.** Two new 🔴 Essential
  entries added under Category 2, ahead of `sentinel init` which now
  formally lists them as prerequisites:
  - **EndpointConfig: Request Body Support** — adds `body:` literal to
    each endpoint so POST/PUT/PATCH checks can start from a valid
    payload and mutate from there. Without this, `rate_limit` testing
    on POST endpoints is effectively impossible (the burst trips
    body-validation 422s before the rate limiter sees the requests).
  - **AuthConfig: Auth Scheme Selector** — adds `scheme: bearer|basic|
    header|apikey` so APIs using anything other than `Authorization:
    Bearer` (HTTP Basic, custom header keys, query params) can be
    probed by the `auth`, `authorization`, and `rate_limit` checks.
    Without this, the auth check is hardcoded to Bearer.
  - Both were discovered during animeintel.app real-world testing
    (DNA endpoint + admin endpoints with Basic Auth).
- **Step 2 — Phase 8 LLM Panel Rich-markup bug fixed.** The TODO
  comment placed in `_run_scan` during UI-1 is now resolved:
  - Extracted `_render_llm_narrative(console, narrative, backend)`
    as a module-level helper. The body uses `rich.markup.escape()`
    before passing the narrative to `Panel(...)`, so `[bracket]`
    sequences in LLM output (e.g. "see [section 3]" or "the [403]
    response code") render literally instead of being silently parsed
    as unknown markup tags and dropped.
  - Three regression tests added to `tests/unit/test_cli.py`
    (`TestRenderLLMNarrative`): bracket text survives, status code
    brackets survive, panel title + backend subtitle present.
- **Step 3 — README updated to v0.1.0 + UI.** Substantial revision
  covering the UI surface:
  - Installation: added `pip install -e ".[ui]"` option.
  - **New "Web UI (optional)" top-level section** between Quickstart
    and CLI Reference — covers what the UI gives you (viewer / editor /
    scans / filters / export / re-scan), launch flags, the
    localhost-only security model, and the no-secret-values invariant
    enforced by `tests/ui/test_env_var_isolation.py`.
  - CLI Reference: updated usage lines to show `scan` / `ui` / `init`
    subcommands with an explicit "bare `sentinel` still works" note
    for backward compat.
  - Troubleshooting: 3 new entries for UI-specific issues (extras not
    installed, env var not visible after launch, port-in-use fallback).
  - Project Structure: added `sentinel/ui/` and `tests/ui/` to the tree.

### Decisions

- **Path A over Path B (defer `sentinel init`).** `sentinel init` is a
  multi-phase project on its own (schema extensions, OpenAPI parser,
  per-scheme auth, CLI + UI integration). Shipping the polish wins
  first means a confidently-public-shippable product NOW, with
  real-user feedback shaping `sentinel init` later instead of
  guessing at edge cases in isolation.
- **Phase 8 fix extracted into a helper, not inlined.** Inlining
  `escape(narrative)` would have been a one-line change but would
  have left the regression test awkward (mocking the whole
  `_run_scan` LLM branch). Extracting `_render_llm_narrative` gives
  a pure-function surface that's trivially testable with a `StringIO`-
  backed Console, no LLM mocks, no environment setup.
- **README UI section sits between Quickstart and CLI Reference.**
  Promotes it as an alternative primary path, not a niche power-user
  feature. The renegotiated "CLI primary, UI optional" architectural
  constraint stays intact — CI/CD section still emphasizes the CLI
  exclusively.

### Testing

- 3 new tests in `tests/unit/test_cli.py::TestRenderLLMNarrative`.
- Full suite: **314 passed, 0 failures** (311 prior + 3 LLM regression).
- README is documentation-only — no code paths changed beyond the
  Phase 8 fix.

### Step 4 — Real-world iteration (owner-driven)

This is Jeremy's call. Suggested workflow for the next pass against
animeintel.app:

- Add the remaining public endpoints to `sentinel_config.yaml`
  (`/api/search`, `/api/search/feedback` — schemas already shared in
  the conversation). Use `requires_auth: false`,
  `rate_limit_sensitive: false`, and keep `input_handling` /
  `rate_limit` globally disabled until the body-support feature
  lands. This pattern gives clean transport+headers+auth-skip coverage
  without false positives.
- Try the LLM report end-to-end: `sentinel scan --report llm
  --llm-backend gemini` (free tier). The Phase 8 fix means any
  `[bracket]` sequences in the model's narrative now render correctly.
  This validates the report path that's been untested in v0.1.0
  smoke runs.
- Stop excluding admin endpoints — they really do need the
  auth-scheme-selector roadmap item before Sentinel can probe them
  cleanly. Leave them out of the config for now.

---

## Phase UI-7 — Filters, JSON Export, Re-scan (UI track complete)

**Status:** Complete
**Date:** 2026-05-16

### Completed

- `sentinel/ui/services/scan_runner.py`: extended `ScanState` with two
  new fields:
  - `config: SentinelConfig` — snapshot captured at scan start. The JSON
    export uses this so the output matches the configuration that
    *produced* the findings, even if the file on disk is edited
    afterward.
  - `config_path: str` — preserved so the "Re-scan with same config"
    button can re-POST against the same path, picking up any edits made
    in between scans (the file IS the source of truth on re-scan).
  - `ScanRunner.start()` signature now takes `(config, config_path)`.
- `sentinel/ui/routes/scans.py`: two new routes + helpers.
  - `GET /scans/{id}/results?filter={all,critical,warning,pass}` —
    re-renders just the results panel with a severity filter applied.
    Categories that have no findings under the filter disappear from
    the output. Counts always reflect the full result set (so users
    can see "Critical only (0)" when nothing critical exists).
  - `GET /scans/{id}/export.json` — JSON download with
    `Content-Disposition: attachment`, suggested filename
    `sentinel-scan-{id[:8]}.json`. Reuses `build_report_data()` from
    the reporter so the schema matches `sentinel scan --output json`.
    Token values are redacted via `_collect_redact_values()` mirroring
    the CLI's behavior.
  - `_apply_severity_filter()` helper — pure function, takes results
    + filter name, returns filtered list. Easy to test in isolation.
- `sentinel/ui/templates/partials/scan_results.html`: substantial
  redesign.
  - Wrapped everything in `<div id="scan-results-area">` so the filter
    buttons can `outerHTML`-swap into it.
  - Added a `.scan-actions` row with "Download JSON" link and
    "Re-scan with same config" form.
  - Added a `.severity-filter` button row with four pills (All /
    Critical only / Warning+ / Passing only). Each shows the count for
    its filter (e.g. "Critical only (2)") so users see at a glance
    whether clicking the button will reveal anything.
  - Active filter button gets `.btn-active` class.
  - Empty filter result renders "No findings match the current filter."
- `sentinel/ui/static/styles.css`: +60 lines for the actions row,
  filter pills (default + hover + active states), and the rescan form
  layout.
- `sentinel/ui/routes/scans.py` (`view_scan` + `_render_status_fragment`):
  both render paths now pass `active_filter="all"` so the freshly-
  completed page shows the All button as active by default.
- `tests/ui/test_routes_scans.py`: 12 new tests across three classes.
  - `TestScanResultsFilter` (7): critical-only / warning+ / passing-only /
    all / unknown-falls-back-to-all / active-button-has-class / 404
    on unknown scan.
  - `TestScanExport` (4): correct headers + Content-Disposition, JSON
    body has expected meta/summary/results structure, **token values
    are redacted (`ZZZ-export-redact-canary-ZZZ` → `[REDACTED]`)**,
    404 on unknown scan.
  - `TestResultsPanelButtons` (1): rendered partial includes the
    download link and the rescan form with the original `config_path`.
- `tests/ui/test_env_var_isolation.py`: added `/scans/{id}/results`
  and `/scans/{id}/export.json` to `_ROUTES`. Parametrized canary
  test now exercises 12 routes (was 10).
- `tests/ui/test_scan_runner.py` + `tests/ui/test_routes_scans.py`:
  updated all `ScanState(...)` constructions and `runner.start(...)`
  calls for the new required fields. No new tests here for the
  signature change — the existing tests provide coverage simply by
  continuing to pass.

### Issues Addressed

- **Filter assertions targeted `check_id` text, but the template
  renders `r.name`.** First test run had 5 filter-test failures
  asserting `"transport.a" in response.text` — but the template only
  surfaces the human-readable `name` field, and my test helper was
  building `CheckResult(name=check_id.split(".")[-1])` → just `"a"`.
  Fix: build test results with `name=check_id` so the full identifier
  appears in the rendered HTML, making assertions clear.
- **Export-test project name asserted against `scan.project` but the
  payload comes from `config.meta.project`.** `build_report_data()`
  reads project metadata from the `SentinelConfig`, not from any
  scan-side field. Test was constructing a scan with
  `project="export-test"` but using the standard `_make_config()`
  helper whose `meta.project="test-proj"`. Fix: assert against
  `"test-proj"` (the value the user actually sees in their exported
  JSON) with a comment explaining the discrepancy.
- **Bulk `runner.start(...)` updates from earlier turns were
  incomplete.** Two test functions had multiple calls on separate
  lines that an earlier `replace_all` pass apparently didn't touch
  (still uncertain why — possibly stale file state from preceding
  edits). Caught by the test signature errors; fixed by targeted
  edits.

### Decisions

- **Filter buttons swap `#scan-results-area` (outerHTML), not the
  parent `#scan-status-area`.** Two reasons. First: the polling area
  has already stopped polling by the time filters can be clicked
  (filters only render in the complete state). Swapping into a
  smaller region keeps the metadata and status-line stable above the
  swapped block. Second: it'd be wasteful and visually noisy to
  redraw the whole status section just to filter findings.
- **Filter state lives in the URL of the swap GET, not in any
  session.** Per the original plan: filter resets on navigation
  (back/refresh/new tab). If users ask for persistence later,
  adding `?filter=critical` to the parent `/scans/{id}` page URL
  is a small change. v1 favors simplicity.
- **Counts always show the full set, not the filtered set.** The
  filter pills include counts in parentheses (e.g. "Critical only
  (2)"). The summary line at the top also always reflects the full
  result set. Users get a stable global view of severity counts
  regardless of which filter they're currently in.
- **JSON export reuses `build_report_data()` from
  `sentinel/reporter.py`.** Single source of truth for the JSON
  schema between CLI and UI. If the JSON contract changes, both
  paths automatically stay aligned. CI workflows can target either
  surface and produce identical artifacts.
- **Token redaction in export mirrors CLI behavior.** Even though
  the user is downloading their own data on their own machine, the
  JSON may be shared (CI artifacts, support tickets, regression
  diffs). Default-safe behavior wins.
- **`Content-Disposition: attachment` + `download` HTML attribute on
  the anchor.** Belt and suspenders. Some browsers respect the link
  attribute; others rely on the response header. Both are set.
- **`config` and `config_path` are both stored in `ScanState`.**
  `config` is the immutable snapshot for export (matches the actual
  scan). `config_path` is the live file pointer for re-scan (picks
  up edits). Two concerns, two fields, no ambiguity about which
  the user gets when.
- **Re-scan uses a plain `<form method="post">`, not HTMX.** The
  re-scan endpoint redirects (303) to the new scan's detail page.
  Plain form posts follow that natively — the browser navigates
  to the new URL, the back button works, the URL bar shows the
  right thing. HTMX would have required an `HX-Redirect` header
  workaround to achieve the same effect.

### Testing

- 14 new tests across `test_routes_scans.py` (12) and
  `test_env_var_isolation.py` (+2 parametrized routes: 10 → 12).
- Full suite: **311 passed, 0 failures** (152 pre-existing + 2 UI-0 +
  18 UI-1 + 17 UI-2 + 26 UI-3 + 15 UI-4 + 42 UI-5 + 25 UI-6 + 14 UI-7).
- The token-redaction test deserves a callout — it threads a canary
  string from a real `os.environ` set, through the seeded scan's
  detail field, into the exported JSON, and asserts the canary is
  GONE and `[REDACTED]` appears. End-to-end verification that the
  redaction pipeline still works when the export path is used.

### Next Steps (UI track complete)

The full v0.1.0 + UI overlay is now feature-complete: CLI, viewer,
editor, picker, scan execution, filter/export/re-scan. From this
point the work shifts from "build the UI" to "harden and ship":

- **Real-world testing against animeintel.app** (Jeremy's stated
  v0.1.0 next step from the original PROGRESS entry). The first
  smoke run confirmed end-to-end pipeline works; next pass is
  populating endpoints and triaging actual findings.
- **Phase 8 LLM Panel Rich-markup bug** (flagged in UI-1 PROGRESS,
  TODO in cli.py). One-line fix using `rich.markup.escape(narrative)`.
- **README update.** The README currently documents v0.1.0's CLI
  surface. Add a "UI" section: `pip install api-sentinel[ui]`,
  `sentinel ui` to launch, screenshot, security caveats
  (localhost-only, never accepts secret values as input).
- **Public repo flip.** When confident in real-world behavior.
- **Roadmap continuation.** With the UI shell in place, `sentinel
  init` (🔴 Essential OpenAPI auto-config) becomes much more
  valuable — it can pre-populate the editor form directly. That's
  likely the highest-leverage next feature.

---

## Phase UI-6 — Scan Execution + .env in UI

**Status:** Complete
**Date:** 2026-05-16

### Completed

- `sentinel/ui/services/scan_runner.py`: in-memory scan registry.
  - `ScanState` pydantic model: id (uuid4), target, project, status
    (running|complete|error), started_at, completed_at, run_result, error.
    Properties: `is_done`, `duration_ms` (live while running, frozen on
    completion).
  - `ScanRunner` class: `start(config)` returns a uuid4 scan_id and
    creates an `asyncio.create_task()` to run `run_checks()` in the
    background; `get(scan_id)` and `list_recent(limit=50)` for reads;
    `clear()` for tests. All operations are guarded by an `asyncio.Lock`.
  - Module-level singleton `scan_runner` — one per process.
- `sentinel/ui/routes/scans.py`: four routes wired around the runner.
  - `GET  /scans` — list page (recent scans + new-scan form).
  - `POST /scans` — load config from disk, start scan, 303-redirect to
    `/scans/{id}`. Same `?path=` query as the editor/viewer. Path
    traversal returns 400; missing/invalid config returns 400 with an
    inline message.
  - `GET  /scans/{id}` — detail page. Includes a `<div id="scan-status-area">`
    that contains the polling fragment.
  - `GET  /scans/{id}/status` — HTMX polling target. Returns the status
    fragment with current state. When the scan is still running, the
    fragment includes `hx-trigger="every 1s"`; when complete or errored,
    the trigger attribute is **omitted** so HTMX stops polling on its
    own — no JS needed, no explicit "stop polling" call.
- `sentinel/ui/templates/scans.html`: new-scan form + recent-scans table
  (started_at, project, target, status badge, duration, link).
- `sentinel/ui/templates/scan.html`: detail page with metadata `<dl>` and
  the polling `<div>` wrapper.
- `sentinel/ui/templates/partials/scan_status.html`: the polled fragment —
  spinner while running, error panel on failure, full results on success.
  Conditional `hx-trigger` attribute ends the polling loop automatically.
- `sentinel/ui/templates/partials/scan_results.html`: per-category
  findings list with severity badges, endpoint references, and (for
  failing checks) detail + recommendation in an inset block.
- `sentinel/ui/templates/config.html`: added "Run scan now" form below
  the path-picker so the viewer can launch a scan in one click.
- `sentinel/ui/templates/base.html`: added "Scans" nav link.
- `sentinel/ui/templates/home.html`: UI-6 moved to "Working today."
- `sentinel/ui/static/styles.css`: ~120 lines for the scans page table,
  status badges (running/complete/error), the running spinner with
  CSS-keyframe rotation, the scan summary line, and findings list
  styling with per-severity badge colors.
- `sentinel/ui/server.py`: registered the scans router.
- **`sentinel/cli.py`: `_run_ui()` now calls `load_dotenv_file()` before
  starting the launcher.** Brings the UI in line with the scan CLI: users
  who keep secrets in `.env` see those env vars in the picker without
  needing to set them in the parent shell.
- `tests/ui/test_scan_runner.py` (9 tests): start returns uuid +
  creates running state, complete transition, error transition,
  unknown-id returns None, list ordering by started_at desc, list
  respects limit, clear drops state, duration_ms live, duration_ms
  frozen.
- `tests/ui/test_routes_scans.py` (10 tests): GET list (empty state +
  form), POST start (303 redirect / missing config 400 / traversal 400),
  GET detail (unknown 404 / shows target after start), GET status
  fragment (unknown 404 / completed has results and no trigger /
  running includes the polling trigger).
- `tests/unit/test_cli.py`: added `test_ui_loads_dotenv_file` — writes
  a `.env` to a tmp cwd, asserts the env var is present when the
  launcher is invoked.
- `tests/ui/test_env_var_isolation.py`: added `/scans`, `/scans/{id}`
  (with a known-nonexistent uuid that 404s), and `/scans/{id}/status`
  to `_ROUTES`. The 404 paths must also be canary-free. Parametrized
  canary test now exercises 10 routes (was 7).

### Issues Addressed

- **FastAPI response-model machinery choked on `HTMLResponse |
  RedirectResponse` union return type.** First test run produced 61
  collection errors — every test that called `create_app()` failed at
  app-creation time. Root cause: FastAPI tries to build a Pydantic
  response model from the annotated return type, and the union of two
  starlette Response subclasses isn't a valid Pydantic field. Fix:
  remove the return type annotation on `start_scan` and add
  `response_model=None` to the decorator. The handler still returns
  the right Response subclass; FastAPI just stops trying to coerce.
- **Async autouse fixture was silently un-awaited.** The route tests
  in `test_routes_scans.py` are sync (TestClient), and pytest's
  `pytest-asyncio` plugin doesn't auto-run async fixtures inside sync
  tests — it creates the coroutine and then never awaits it, producing
  a `RuntimeWarning`. Fix: made the autouse fixture synchronous and
  cleared the singleton's `_scans` dict directly (bypassing the
  asyncio.Lock, which is safe in the single-threaded test context).
- **Windows asyncio.sleep precision caused a flaky ordering test.**
  `test_list_recent_orders_by_started_at_desc` used `asyncio.sleep(0.01)`
  between three `runner.start()` calls. On Windows the actual yield can
  be much coarser than 10 ms, occasionally producing started_at
  timestamps that were too close to sort deterministically. Fix: bumped
  to 0.05s — generous margin without making the test slow.
- **(Post-smoke-test) Status badge stuck at "RUNNING" after scan
  completed.** User ran a real scan against animeintel.app, got correct
  results in the body, but noticed the status badge in the page's
  metadata `<dl>` still said RUNNING. Root cause: the badge lived in
  the parent template's static metadata block, **outside** the polled
  `#scan-status-area` swap target. The polled fragment correctly swapped
  in the spinner → results, but the badge was rendered once at initial
  page load and never updated. Fix: moved the Status row out of
  `scan.html`'s `<dl>` and into the polled `scan_status.html` partial,
  so every poll carries a fresh badge. Added two regression tests
  (`test_status_fragment_running_badge`, `test_status_fragment_complete_badge`)
  that inject a known-state scan into the singleton and assert the
  correct CSS class appears.
- **(Post-smoke-test) Initial regression test used `asyncio.Event` for
  cross-context coordination — and it didn't work.** First attempt at
  the status-badge regression test created an `asyncio.Event` in the
  sync test scope, then tried to `.set()` it from sync code to release
  a slow_run task running in TestClient's event loop. TestClient runs
  the ASGI app in a portal/loop that doesn't share the test's sync
  context, so `.set()` never reached the awaiting task and the scan
  stayed "running" past the test's timeout. Rewrote both regression
  tests to inject `ScanState` directly into the singleton's `_scans`
  dict — no async coordination, no timing concerns, deterministic.

### Decisions

- **`asyncio.create_task` + module-level singleton for state.** Per
  the original plan and the no-multi-user-data-custody preference,
  scans live in memory only. The simplicity is the point: no schema,
  no migration risk, no on-disk leak surface. If users ask for
  persistence later, the SQLite-at-`.sentinel/runs.db` pattern from
  the "Historical Baseline Tracking" 🟡 roadmap item slots in cleanly.
- **303 See Other redirect on POST /scans success.** Standard
  Post-Redirect-Get. Plays nice with the browser's history/refresh
  behavior and HTMX. The detail page is the canonical URL for that
  scan — bookmarkable, shareable across browser sessions (until the
  server restarts and the registry drops).
- **HTMX polling stops via attribute absence, not explicit signaling.**
  The status fragment renders the `hx-trigger="every 1s"` attribute
  only when `scan.status == 'running'`. The next swap that arrives
  for a complete/error scan has no trigger → no further polls. No JS,
  no `htmx.trigger()` calls, no race conditions.
- **Polling target is `outerHTML` of the status div.** This means the
  swapped fragment replaces the wrapper element itself, including its
  attributes. That's how the polling-attribute-removal trick works:
  the new wrapper has no `hx-trigger`, so the polling loop dies.
- **Spinner is a CSS keyframe animation, not a JS thing.** Single
  Unicode glyph (⚐), rotated via `@keyframes spin`. Vendoring jQuery
  spinners or fancy SVG spinners would be over-engineering for v1.
- **In-memory clear() bypasses the lock in tests.** The asyncio.Lock
  exists to serialize concurrent state mutations during real scans.
  Test fixtures run between tests in a single-threaded context, so
  reaching directly into `_scans.clear()` is safe and avoids the
  unawaited-coroutine warning that would come from declaring the
  fixture async.
- **`.env` loaded once at UI startup, not per-request.** Matches the
  `scan` subcommand's behavior. Editing `.env` while the UI is running
  requires a restart — documented in the answer to the user's
  related question on the same day this phase shipped.

### Testing

- 25 new tests across `test_scan_runner.py` (9), `test_routes_scans.py`
  (12 — including the two post-smoke status-badge regression tests),
  and `test_cli.py` (1 for .env), plus 3 additional parametrized
  iterations in `test_env_var_isolation.py` (7 → 10 routes).
- Full suite: **297 passed, 0 failures** (152 pre-existing + 2 UI-0 +
  18 UI-1 + 17 UI-2 + 26 UI-3 + 15 UI-4 + 42 UI-5 incl. post-smoke +
  25 UI-6 incl. post-smoke).
- Scan-runner tests use a private `ScanRunner` instance per test (not
  the singleton) to avoid bleed. Route tests use the module singleton
  with the sync clear-fixture for the same reason.
- `slow_run_checks` + `asyncio.Event` lets a test pin a scan in the
  "running" state long enough to assert the polling fragment is
  rendered, then release the event so the task doesn't dangle.

### Open Questions for Phase UI-7 (Final UI Phase)

- **Severity filter buttons.** Plan: at the top of the results
  section, show "All / Critical+ / Warning+ / Pass" buttons that
  filter the visible findings via HTMX swap. The first three reload
  a filtered partial; "Pass" shows green-only.
- **Filter persistence across reload.** Server-side state means the
  filter resets when the user navigates away. Acceptable for v1 — the
  filter is meant for triage at the moment of viewing, not for saved
  views. If users ask, add a `?filter=critical` query param later.
- **CSV / JSON / Markdown export from a scan.** The CLI already
  produces JSON via `--output json`. The UI could surface a "Download
  JSON" button on the results page reusing `build_report_data()`
  from the reporter module. Small addition; worth slotting into UI-7.
- **Re-run from a finished scan.** A "Re-scan with same config" button
  on the detail page would be a one-line addition (resolve the same
  path, call `scan_runner.start`) and significantly improves the
  iterate-on-config-fix workflow.

---

## Phase UI-5 — Config Editor (Write)

**Status:** Complete
**Date:** 2026-05-16

### Completed

- `sentinel/ui/services/form_parser.py`: parses flat form data
  (`endpoints[0].path`, `checks.transport.enabled`) into a nested dict that
  pydantic can validate. Plus three helpers:
  - `split_lines()` — textarea → list of trimmed non-empty lines (handles CRLF)
  - `split_csv_ints()` — comma-separated → `list[int | str]` (matches the
    schema's `test_ids: list[int | str]`)
  - `loc_to_field_name()` — pydantic `("endpoints", 0, "path")` →
    `"endpoints[0].path"` so the template can match errors to fields.
- `sentinel/ui/services/config_writer.py`: `write_config_yaml(path, data,
  backup=False)`. Writes to `<path>.tmp`, optionally copies the existing
  file to `<path>.bak`, then `os.replace(tmp, path)` — atomic on POSIX
  and Windows. The user never observes a half-written config.
- `sentinel/ui/routes/config_editor.py`: four routes sharing one render
  path:
  - `GET /config/edit` — load existing config or render empty defaults
  - `POST /config/endpoints/add` — append blank row, re-render form
  - `POST /config/endpoints/remove?index=N` — drop indexed row, re-render
  - `POST /config/save` — coerce, validate via pydantic, atomic write
- `sentinel/ui/templates/config_edit.html` — full-page wrapper.
- `sentinel/ui/templates/partials/config_form.html` — the form itself:
  Meta + Auth (with token `<select>` populated by `/env-vars` via HTMX
  load-trigger) + Endpoints (dynamic list) + Checks (six categories with
  per-category settings) + save bar with backup checkbox + comment-loss
  warning.
- `sentinel/ui/templates/partials/endpoint_row.html` — one row per endpoint,
  uses bracket-dot field naming for round-trip with the parser.
- `sentinel/ui/static/styles.css`: ~190 lines appended for form layout
  (fieldsets, form rows, endpoint grid, check groups with inline settings,
  textareas, buttons with primary/secondary/remove variants, save bar,
  success/warning alerts).
- `sentinel/ui/templates/config.html`: added "Edit this config" link in
  the viewer's metadata line.
- `sentinel/ui/templates/home.html`: moved "Configuration editor" from
  "Coming next" to "Working today."
- `sentinel/ui/server.py`: registered the new router and **registered a
  custom Jinja filter `as_lines`** that renders `list[str]` or `str` as
  a newline-joined string. Used by the headers textareas so the template
  works on both shapes (model_dump → list, form-parsed → string).
- `tests/ui/test_form_parser.py` (21 tests): parser nesting/indexing/sparse
  cases, line splitter incl. CRLF, csv-int splitter with int/str fallback,
  loc-to-field-name for tuples with mixed strs/ints.
- `tests/ui/test_config_writer.py` (7 tests): write/overwrite, backup
  on/off, backup-skipped-on-first-write, no tmp file left behind,
  key-order preservation (sort_keys=False).
- `tests/ui/test_routes_config_editor.py` (11 tests): GET edit (200/loads
  values/first-run defaults/traversal-blocked), POST add (appends row),
  POST remove (drops row/out-of-range silent), POST save (writes
  YAML/422 on invalid/backup), and a load-edit-save round-trip that
  confirms semantic equivalence through `load_config()`.
- **`tests/ui/test_env_var_isolation.py`: added `/config/edit` to
  `_ROUTES`.** Parametrized canary test now exercises 7 routes (was 6).
  Critical bookkeeping.
- **Post-smoke-test additions (same day):**
  - Path picker form at the top of both `/config` and `/config/edit` —
    text input + Load/View button, so users can switch between config
    files without editing the URL bar.
  - Global nav updated to "Home · View Config · Edit Config" so the
    editor is one click from anywhere.
  - Server-rendered token `<option>` elements in the editor (see "Issues
    Addressed" for the HTMX bug that motivated this).

### Issues Addressed

- **Form-data partial renders crashed the template.** First test run
  produced 5 failures all matching `'dict object' has no attribute
  'transport'`. Root cause: form posts that don't include `checks.*`
  fields produce a parsed dict with no `checks` key, and the template
  unconditionally dereferences `data.checks.transport.enabled`.
  Fix: every render path now goes through `_normalize_for_template(data)`,
  which deep-merges the parsed form on top of `_empty_form_data()` to
  guarantee full shape.
- **"false" as a string is truthy in Jinja.** Form checkboxes use the
  hidden-input pattern (`<input type="hidden" value="false">` paired
  with `<input type="checkbox" value="true">`); when unchecked the form
  has `"false"` as a string. Jinja's `{% if value %}` treats any
  non-empty string as truthy — including `"false"` — so unchecked
  checkboxes would re-render as CHECKED after an HTMX swap. Fix:
  `_normalize_bool_strings()` recursively converts the literal strings
  `"true"`/`"false"` to real bools.
- **Textareas crashed on `list | join` when the data was a string.**
  Forms send the textarea contents as one big string; `model_dump`
  produces a list. The template's `(value or []) | join('\n')` worked
  on lists but called `join` on a string (which iterates characters).
  Fix: registered a custom `as_lines` Jinja filter that handles both
  shapes uniformly.
- **`endpoints: []` is required by pydantic — and the form parser
  produces a dict without `endpoints` when no rows are present.** A form
  with zero endpoint rows is valid intent but pydantic rejected it with
  "Field required". Fix: `_coerce_for_pydantic()` injects `endpoints: []`
  if absent before validation.
- **Test for invalid form data was relying on a separate bug.** The
  "422 on invalid form" test was using empty `base_url` to trigger
  validation failure — but `base_url: str` accepts empty strings as
  valid (pydantic 2 default). After the `endpoints` fix above made the
  form genuinely complete, that test started passing 200. Updated to
  use `timeout_seconds: "not-a-number"` which pydantic genuinely can't
  coerce to float. (Stricter validation on `base_url` non-emptiness is
  a candidate improvement for the underlying config model, but out of
  UI-5 scope.)
- **(Post-smoke-test) HTMX target inheritance obliterated the editor
  form.** First user smoke-test in the browser showed only two "no
  SENTINEL_* env vars in scope" lines and no form. Root cause: the
  `<form>` carries `hx-target="#config-form-wrapper"` for save dispatch.
  The two token `<select>` elements had `hx-trigger="load"` to populate
  from `/env-vars` — but HTMX inherits attributes including `hx-target`,
  so the load-triggered GETs each replaced the *entire form's contents*
  with their `<option>` response. Fix: drop HTMX from token selects
  and render `<option>` elements server-side in the route (`list_env_var_names()`
  injected via `_render`'s context). `/env-vars` endpoint still exists
  and is tested — just not used for initial population. Added a
  regression test (`test_token_selects_render_options_server_side`)
  that asserts the rendered selects do NOT carry `hx-get` and DO contain
  real `<option>` values.

### Decisions

- **Stateless form rendering across all four routes.** Every POST
  request carries the full current form state via `hx-include="#config-form"`.
  The server mutates the parsed dict (append row, remove row, validate +
  save), then re-renders the partial. There is no server-side draft
  store, no per-user session — the browser's form IS the source of
  truth. Trivially horizontally-scalable, but more importantly: no
  in-memory state means no leak surface, no stale-state bugs.
- **HTMX swap target is the wrapper, swap mode is innerHTML.** The
  page-level `<div id="config-form-wrapper">` contains the form
  partial. All HTMX responses (add/remove/save) return just the
  `partials/config_form.html` partial. `hx-swap="innerHTML"` replaces
  the wrapper's contents, including the success/error banner above
  the form.
- **422 on validation errors, 200 on save success.** Standards-compliant
  semantics — HTMX still renders the response body either way. The 422
  helps any future programmatic client distinguish save failures from
  network errors.
- **Atomic write via `<path>.tmp` + `os.replace`.** Works on both POSIX
  and Windows when src and dst share a filesystem (they do — tmp is in
  the same directory). The original file is unchanged until the
  rename, so a crash mid-write leaves the user with their pre-save
  config intact.
- **Backup is opt-in via checkbox.** Per the original plan. Default
  off. Surfaced prominently above the save button alongside the
  comment-loss warning. Backup file is `<path>.bak` regardless of the
  source filename.
- **Token fields use `<select hx-get="/env-vars" hx-trigger="load">`.**
  No free-text input ever. The placeholder option shows the currently
  selected name until the HTMX swap populates real options from the
  picker endpoint.
- **Custom Jinja filter over inline conditionals.** `data.field | as_lines`
  is readable; `{% if x is iterable and x is not string %}...{% else %}...{% endif %}` is not.
  The filter is registered in `server.py` next to the templates
  instance — natural locus.

### Testing

- 40 new tests across 4 files. Full suite: **270 passed, 0 failures**
  (152 pre-existing + 2 UI-0 + 18 UI-1 + 17 UI-2 + 26 UI-3 + 15 UI-4 +
  40 UI-5).
- Round-trip test (`TestRoundTrip::test_load_edit_save_preserves_content`)
  loads an existing config via the UI, posts it back via /config/save,
  then re-loads from disk through the CLI's `load_config()` and asserts
  semantic equivalence. Confirms the UI and CLI agree on schema
  interpretation.
- Bug-fix journey: first test run produced 5 failures (template
  partial-render crashes); after defaults-merge + bool-string fixes,
  down to 1; after endpoints-default fix, down to 1 (test relied on
  unrelated bug); after test fix, 0. All cycles took ~2 minutes each.

### Open Questions for Phase UI-6

- **Scan execution model.** Plan: `POST /scan` spawns an `asyncio` task
  via `asyncio.create_task()`, returns immediately with a scan UUID and
  a polling page. Background-task state lives in an in-memory dict
  keyed by UUID, holding `ScanState(status, started_at, completed_at,
  run_result, error)`. The polling endpoint uses HTMX
  `hx-trigger="every 1s"` to swap a status fragment.
- **Where does the scan find its config?** The editor saves the config
  to disk; the scan loads it. So `/scan` takes the same `?path=` param
  as the editor and re-reads from disk at scan start. Simpler than
  trying to share state between the editor and runner.
- **Live progress granularity.** The existing `run_checks()` returns
  one `RunResult` at the end — no progress callbacks. v1 plan: show
  three states (running → complete → error). For finer granularity
  (per-check progress) we'd need to add a callback hook to
  `BaseCheck.run()`, which crosses into the engine's surface. I'd
  defer that until users ask for it.

---

## Phase UI-4 — Env Var Picker Endpoint

**Status:** Complete
**Date:** 2026-05-16

### Completed

- `sentinel/ui/services/env_vars.py`: single-purpose service `list_env_var_names(prefix)`
  that returns sorted env var names matching a prefix. **Operates only on
  `os.environ.keys()` — never reads values.** That structural decision IS the
  security control; there is no code path that could surface a value.
- `DEFAULT_PREFIX = "SENTINEL_"` constant. Empty string passed as prefix falls
  back to the default rather than returning the whole environment — a footgun
  guard for URL-driven invocation.
- `sentinel/ui/routes/env.py`: GET `/env-vars?prefix=...&selected=...`. Returns
  an HTML fragment of `<option>` elements. Optional `selected` query param
  marks one option as `selected` if its name appears in the result. Designed
  for HTMX `<select hx-get="/env-vars" hx-trigger="load">` usage in the UI-5
  editor.
- `sentinel/ui/templates/partials/env_var_options.html`: renders `<option>`
  lines for every matched name, OR a single disabled placeholder with a
  helpful message when no names match (e.g.
  `<option value="" disabled selected>no SENTINEL_* env vars in scope &mdash;
  set one first (e.g. SENTINEL_TOKEN_PRIMARY)</option>`).
- `sentinel/ui/server.py`: registered the new router alongside pages and
  config_viewer.
- `tests/ui/test_services_env_vars.py` (6 tests): default-prefix filtering,
  alphabetical sort, custom prefix, empty-prefix fallback, no-match returns
  empty list, sanity check that the constant equals `SENTINEL_`.
- `tests/ui/test_routes_env.py` (8 tests): 200/HTML content type, options
  rendered for matching names, **values absent from response (canary check)**,
  non-matching names absent, custom prefix via query param, `selected` query
  marks the right option, empty-case renders a disabled help message,
  **XSS-safety** for the prefix reflection in the help text.
- **`tests/ui/test_env_var_isolation.py`: added `/env-vars` to `_ROUTES`.**
  The parametrized canary test now exercises 6 routes (was 5). Critical
  bookkeeping — every new route must be appended here.

### Issues Addressed

- **No unforeseen issues.** The service-then-route-then-template separation
  established in UI-3 carried over cleanly. The trickiest design choice was
  the empty-prefix fallback — an empty `?prefix=` could otherwise return every
  env var in the process, including unrelated ones the user might not realize
  were in scope. Falling back to `SENTINEL_` is the conservative default.

### Decisions

- **Fragment, not JSON.** The endpoint returns raw `<option>` elements (no
  wrapping `<select>`). HTMX's natural pattern is `<select hx-get="/env-vars"
  hx-trigger="load">` — the swap target is the inside of the select. JSON
  would have forced the UI-5 editor to build DOM in JavaScript, breaking the
  "no client-side state" discipline.
- **Empty-result placeholder is `disabled selected`.** The `disabled`
  attribute prevents the user from re-selecting that option after picking
  something else; `selected` makes it the default state on render. The
  `value=""` will be caught by server-side pydantic validation in UI-5 (token
  must be non-empty).
- **Prefix is parameterizable but defaults to `SENTINEL_`.** Forward-compatible
  without forcing a decision now. The LLM-key env vars (`SENTINEL_GEMINI_KEY`,
  `SENTINEL_CLAUDE_KEY`, etc.) already match the default prefix, so no
  special-casing.
- **XSS-safety relies on Jinja autoescape, with a regression test.** A
  malicious `?prefix=<script>...</script>` would otherwise reflect into the
  empty-state help message. Jinja autoescapes by default; the test asserts
  the literal `<script>` tag doesn't appear and the escaped form does. Belt
  and suspenders.
- **Service file is tiny on purpose.** `list_env_var_names()` is ~6 lines of
  body. Tempting to inline into the route, but separating it gives us a clean
  unit-testable surface and isolates the security-critical "never read
  values" discipline in one named function.

### Testing

- 14 new tests across `tests/ui/test_services_env_vars.py` (6) and
  `tests/ui/test_routes_env.py` (8), plus 1 additional parametrized iteration
  in `test_env_var_isolation.py` (5 → 6 routes).
- Full suite: **230 passed, 0 failures** (152 pre-existing + 2 UI-0 + 18 UI-1
  + 17 UI-2 + 26 UI-3 + 15 UI-4).
- Manual smoke check via TestClient confirmed populated and empty fragments
  render the expected HTML strings — no surprises in the rendered output.

### Open Questions for Phase UI-5

- **Form state between adds/removes/saves.** When the user adds an endpoint
  row via HTMX, the server holds no in-progress state — each request is the
  current full form. Plan: render the whole form on POST and let HTMX swap
  the whole form back. Simple and stateless. If perf becomes an issue with
  large configs, switch to per-row fragments.
- **YAML save: lose comments, document it.** Confirmed in the original plan
  (option (a) from the UI-3 governance discussion). The UI-5 editor will
  prepend a warning on the save button: "Saving overwrites the YAML file and
  will lose comments — back up before saving if you've hand-annotated it."
- **What counts as a valid save?** Pydantic validation must pass; bad input
  re-renders the form with field-level error markers (HTMX swap of the form
  partial with errors attached). No partial saves, no auto-save.
- **Backup-before-write?** I'll write the new YAML to a temp file, then
  atomic-rename — safer than truncate-and-write. Plus an optional
  `?backup=true` query param that writes `sentinel_config.yaml.bak` before
  the rename. Default off; surfaced as a checkbox in the UI.

---

## Phase UI-3 — Config Viewer (Read-Only)

**Status:** Complete
**Date:** 2026-05-16

### Completed

- `sentinel/ui/services/config_io.py`: UI-layer wrapper around the existing
  pydantic config loader. Three primitives:
  - `resolve_config_path()` — accepts `None`/`""`/relative/absolute. Relative
    paths must stay under cwd (traversal protection via `.resolve()` +
    `.relative_to()`); absolute paths are accepted as the user's explicit
    choice. Default is `cwd / "sentinel_config.yaml"` (matches the CLI).
  - `load_config_for_viewer()` — non-throwing variant that returns a
    `ConfigLoadResult(config, path, error)`. Pydantic ValidationError, YAML
    parse errors, and missing files all become structured error states the
    template renders inline.
  - `env_var_status()` — returns an `EnvVarStatus(name, is_set)` dataclass.
    **The value is never read into the struct, by design.** Only presence is
    captured. The structure guarantees the invariant — there is no field that
    could ever hold the resolved secret.
- `sentinel/ui/routes/config_viewer.py`: GET `/config?path=...`. Catches path
  traversal as 400, missing/invalid configs as 200 with an empty-state render,
  successful loads as 200 with the full structured view.
- `sentinel/ui/templates/config.html`: Meta block, Auth block (env var names
  only, with `(not set)` annotation for unresolved), Endpoints table (HTTP
  method chips, owner, rate-limit-sensitive flag), Checks table (each category
  with enabled state + key settings inline).
- `sentinel/ui/templates/base.html`: added `Config` nav link.
- `sentinel/ui/templates/home.html`: split the roadmap into "Working today" +
  "Coming next" — UI-3 now lives under Working today as a link to `/config`.
- `sentinel/ui/static/styles.css`: ~85 lines appended for the viewer
  (alerts, empty-state card, definition-list meta grid, endpoints/checks
  tables, HTTP method chips with per-method color, `.env-missing` warning).
- `sentinel/ui/server.py`: registered the new `config_viewer.router`.
- `tests/ui/test_config_io.py` (12 tests): path resolution including the
  traversal-rejection case, non-throwing load for missing/invalid/valid
  configs, `EnvVarStatus` invariant (set/unset paths, value-not-in-repr,
  value-not-in-display).
- `tests/ui/test_routes_config.py` (7 tests): empty-state render, valid-config
  render (meta/endpoints/token names visible), unresolved-token annotation,
  absolute-path override, traversal returns 400.
- `tests/ui/test_env_var_isolation.py` (7 tests): **the critical security
  invariant.** Sets `SENTINEL_TOKEN_PRIMARY` and `SENTINEL_TOKEN_SECONDARY` to
  distinctive canary strings, exercises every UI route (5 routes via
  `@pytest.mark.parametrize`), asserts the canaries appear in neither body nor
  headers. Plus a path-override variant and a sanity check that token *names*
  DO render (so the isolation test can't pass trivially by hiding everything).

### Issues Addressed

- **No unforeseen issues.** Patterns from UI-2 (FastAPI app factory, TestClient
  fixture, Jinja templates, lazy router imports) extended cleanly. Pydantic's
  `ValidationError` includes the full validation chain in its `str()`, which
  is verbose but informative — the empty-state template renders it under a
  `.muted` class so it's de-emphasized.

### Decisions

- **`EnvVarStatus` over a tuple or dict.** A named dataclass with exactly two
  fields (`name`, `is_set`) is harder to accidentally extend with a value
  field than a tuple or dict. The structural choice IS the security control.
- **Non-throwing config load for the UI.** The CLI's `load_config()` raises
  on every failure mode (matches the CLI's "exit early with a clear message"
  ethos). The UI needs to render error states *within* a page, so the wrapper
  catches and structures the failures. Same underlying load — different
  error-handling contract.
- **Path traversal returns 400, not 404 or 500.** 400 = "your request was
  malformed." Distinguishes it from "the file genuinely doesn't exist" (200
  with empty-state) and "something blew up" (500). The 400 page itself uses
  the same `config.html` template with an `alert-error` banner.
- **Token field is text, never an input.** Phase UI-5 (config editor) will
  use `<select>` populated from `/env-vars` for token fields. UI-3 is
  read-only, so it just renders the name + presence annotation. Either way,
  there is no UI surface where a user can type a token value.
- **Method chips with per-method color.** GET/POST/PUT/PATCH/DELETE each get
  a subtle background + foreground pair (blue/green/orange/purple/red). The
  same color language can later style finding-severity badges in UI-7.
- **Path-traversal test relies on `monkeypatch.chdir(tmp_path)`.** Both the
  TestClient and the route handler call `Path.cwd()` at request time, so
  changing cwd via monkeypatch is effective even though the client fixture
  was created earlier.

### Testing

- 26 new tests across `tests/ui/test_config_io.py` (12),
  `tests/ui/test_routes_config.py` (7), and `tests/ui/test_env_var_isolation.py`
  (7).
- Full suite: **215 passed, 0 failures** (152 pre-existing + 2 UI-0 + 18 UI-1
  + 17 UI-2 + 26 UI-3).
- The env-var isolation test is the most important new test in this phase.
  Every future phase that adds a route must append it to the `_ROUTES`
  parametrize list in `tests/ui/test_env_var_isolation.py`. The test docstring
  calls this out.

### Open Questions for Phase UI-4

- **Env var prefix filter.** Default `SENTINEL_*` for the token picker. The
  LLM report feature uses `SENTINEL_GEMINI_KEY` / `SENTINEL_CLAUDE_KEY` etc.,
  which already match the prefix — no special handling needed. Confirm.
- **Should the picker endpoint return JSON or HTML?** HTML fragment (an
  `<option>` list) is the HTMX-native answer; the editor in UI-5 will swap it
  into a `<select>`. JSON would force the editor to build DOM in JS, which
  defeats the no-JS-state discipline. Going with HTML.
- **Picker UI affordance for "no matching env vars."** If the user has no
  `SENTINEL_*` env vars set, the `<select>` will be empty. Should it instead
  render a help message ("no matching env vars in scope — set
  `SENTINEL_TOKEN_PRIMARY` first") plus a disabled placeholder option? I'd
  lean toward the latter for first-run UX.

---

## Phase UI-2 — Server Skeleton + Home Page

**Status:** Complete
**Date:** 2026-05-16

### Completed

- `sentinel/ui/server.py`: FastAPI app factory (`create_app()`). Mounts
  `/static`, registers the pages router, and exposes a cheap `/healthz`
  endpoint for the launcher and tests. Docs surfaces (`/docs`, `/redoc`,
  `/openapi.json`) are all disabled — this is a UI, not a public API.
- `sentinel/ui/routes/pages.py`: GET `/` renders `home.html` via Jinja with
  the current `sentinel.__version__` in the footer.
- `sentinel/ui/launcher.py`: port picker (`_pick_port`) with OS-assigned
  fallback when the requested port is busy, best-effort browser open from a
  daemon timer thread, uvicorn boot with quiet logging. Returns 0 on clean
  Ctrl+C.
- `sentinel/ui/templates/base.html`: minimal layout, links `/static/htmx.min.js`
  (deferred) and `/static/styles.css`. Header, nav, main content block, footer.
- `sentinel/ui/templates/home.html`: placeholder dashboard listing the phases
  ahead. Points users at the CLI for working scans today.
- `sentinel/ui/static/styles.css`: ~110 lines hand-written, CSS custom
  properties for the severity palette so later phases inherit the color
  language used in the Rich terminal report.
- `sentinel/ui/static/htmx.min.js`: HTMX 2.0.4 vendored from
  `https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js` (~51 KB). Pinned
  version, committed to the repo — no CDN dependency at runtime.
- `sentinel/cli.py`: `_run_ui_stub` replaced by `_run_ui`, which imports the
  launcher lazily and surfaces a clean Rich error if the [ui] extras aren't
  installed (rather than letting the raw ImportError traceback bubble up).
- `tests/ui/test_routes_pages.py` (11 tests): home route status/content-type/
  branding/static-asset linking, healthz response shape, static-asset
  serving for both CSS and JS, 404 for unknown static paths, three asserts
  that the OpenAPI/docs surfaces are off.
- `tests/ui/test_launcher.py` (6 tests): `_is_port_free` true/false paths,
  `_pick_port` returns-requested and falls-back paths, browser-open is called
  and silently swallows exceptions.
- `tests/unit/test_cli.py` updated: stub tests replaced with `TestUIDispatch`
  (2 tests) covering launcher dispatch and the missing-extras error path.

### Issues Addressed

- **HTMX vendoring vs CDN.** Considered loading HTMX from `unpkg.com` at
  runtime — rejected. Pinning + committing the file means offline use works,
  there's no supply-chain surprise on first run, and the bundle is
  reproducible. Cost: ~51 KB in the repo.
- **Browser-vs-server race.** `webbrowser.open()` fires before uvicorn finishes
  binding if called inline. Solution: schedule the browser open via a
  `threading.Timer(1.0)` (daemon thread) so uvicorn has a second to bind. If
  the browser still beats the bind, the user sees connection-refused once and
  refreshes — the URL is already printed prominently. Acceptable failure mode.

### Decisions

- **Templates as module-level state in `server.py`.** Jinja2's
  `Jinja2Templates` is stateless; sharing one instance across all route
  modules is idiomatic and avoids a separate `templating.py` file. Tradeoff:
  routes import `templates` from `server`, so `server.create_app()` does its
  router-includes via function-scope imports to keep the dependency direction
  clean.
- **Docs/redoc/openapi.json disabled.** This is a UI, not an external API.
  Reducing the route surface area means fewer accidentally-exposed endpoints
  to worry about during security review.
- **Quiet uvicorn defaults.** `log_level="warning"`, `access_log=False`.
  Routine GET requests don't deserve a per-request log line on the user's
  terminal; warnings and errors still surface.
- **Daemon timer for browser open.** `timer.daemon = True` so Ctrl+C tears
  the thread down without ceremony.
- **`/healthz` returns `{status, version}`.** The version is small enough to
  include unconditionally and gives the launcher a way to detect "we're talking
  to the right tool" in future probes.
- **Launcher accepts keyword-only args.** `launch(*, host, port, no_browser,
  console)` — keyword-only because the parameters are independent and easy to
  swap positionally otherwise.

### Testing

- 17 new tests across `tests/ui/test_routes_pages.py` (11) and
  `tests/ui/test_launcher.py` (6).
- Full suite: **189 passed, 0 failures** (152 pre-existing + 2 UI-0 + 18 UI-1
  + 17 UI-2).
- `launch()` itself is not unit-tested (it calls blocking `uvicorn.run`); the
  TestClient-backed route tests cover the request/response surface end-to-end.

### Open Questions for Phase UI-3

- **How does the read-only config viewer take its config path?** Default is
  `./sentinel_config.yaml` — same as the CLI. Should the UI accept `?path=`
  as a query parameter for flexibility, or always read from the working
  directory at launch time? I'll lean toward a `?path=` override with
  path-traversal protection (resolved path must stay under cwd, or be an
  absolute path the user explicitly typed).
- **What's the "no config found" state?** First-run users may have no
  `sentinel_config.yaml`. The viewer should render a clear "no config in this
  directory — create one with `sentinel init` or copy from the example" state
  rather than a 500.
- **Token field display.** The viewer must show env var *names* only (per the
  invariant). For an unresolved env var, do we show "SENTINEL_TOKEN_A (not
  set)" or just the name? I'll go with the former for clarity.

---

## Phase UI-1 — Subcommand Dispatch

**Status:** Complete
**Date:** 2026-05-16

### Completed

- Refactored `sentinel/cli.py` to use argparse subparsers. Three subcommands wired up:
  `scan` (carries all v0.1.0 flags, identical behavior), `ui` (stub with `--host`,
  `--port`, `--no-browser`), `init` (stub with `--spec`).
- Added `_normalize_argv()` pre-parse: if `sys.argv[1:]` doesn't start with a known
  subcommand or `-h`/`--help`, `"scan"` is injected as the first arg. This preserves
  every v0.1.0 invocation (`sentinel --config X.yaml` → `sentinel scan --config X.yaml`).
- Extracted the existing scan logic into `_run_scan()` — pure refactor, no behavior
  change. Added `_run_ui_stub()` and `_run_init_stub()` that print a clear "not yet
  implemented" message and exit 0.
- `main()` now accepts an optional `argv` parameter, defaulting to `sys.argv[1:]`.
  Makes dispatch testable without monkeypatching `sys.argv`.
- Updated CLAUDE.md's "CLI Interface" section to document the new three-subcommand
  shape alongside per-subcommand flag listings.
- Added `tests/unit/test_cli.py` with 18 tests across three test classes:
  argv normalization (7), parser wiring (8), and stub behavior (3).

### Issues Addressed

- **Rich markup ate the literal `[ui]` mention in the `sentinel ui` stub.** Rich
  parses `[ui]` as a markup tag, fails to find a matching `[/ui]` close tag, and
  silently drops the text. Fixed by escaping with `\\[ui]` in the source (renders
  as literal `[ui]` in terminal output). Added a regression assertion in
  `TestStubs::test_ui_stub_exits_zero_with_message` so the bug can't sneak back.
- **Analogous bug deferred for Phase 8 cleanup:** The LLM narrative is passed
  through a Rich `Panel` in `_run_scan`. If the LLM ever emits `[bracket]` text
  (e.g. "see [section 3]" or "the [403] response"), Rich will mangle it the same
  way. Marked with a TODO comment in cli.py pointing at the one-line fix
  (`rich.markup.escape(narrative)`). Out of UI-1 scope.

### Decisions

- **Pre-parse argv injection over argparse-native fallback.** Argparse's "no
  subcommand" handling is awkward (it leaves `args.command == None` and you can't
  cleanly delegate to a default subparser). The pre-parse approach is one extra
  function, ~6 lines, and is the only approach that preserves v0.1.0 invocations
  byte-for-byte. Confirmed with the user before implementation.
- **All three UI subparser flags wired in UI-1 even though the stub ignores them.**
  Locking the flag surface now means UI-2 just fills in the launcher logic without
  also changing the help output or accepted-flag set. Includes `--host`, `--port`,
  `--no-browser`.
- **`sentinel init` gets a reserved verb in UI-1.** The OpenAPI auto-config feature
  is a separate 🔴 Essential plan, but reserving the verb now means users won't see
  the surface change later. The stub prints a roadmap pointer. Confirmed with user.
- **`main()` accepts `argv` parameter.** The existing `[project.scripts] sentinel
  = "sentinel.cli:main"` invocation still works (calls with no args → defaults to
  `sys.argv[1:]`). Tests can now drive dispatch without `monkeypatch.setattr(sys,
  "argv", ...)`.
- **No defensive `--allow-external` flag in UI-1.** Defer to UI-2 where the launcher
  actually binds. Adding it now without enforcement would be theater.

### Testing

- 18 new tests in `tests/unit/test_cli.py`, all passing.
- Full suite: **172 passed, 0 failures** (152 pre-existing + 2 UI-0 + 18 UI-1).
- No test imports `build_parser` or `main` from `sentinel.cli`, so the cli refactor
  had zero collateral on the existing suite.

### Open Questions for Phase UI-2

- **Free-port fallback policy.** Plan calls for `--port 8765` default with auto-fallback
  if occupied. The fallback message needs to be unambiguous ("port 8765 in use, listening
  on 12345 instead") so users don't browse to the wrong port. Implementation will use
  `socket.bind(("127.0.0.1", 0))` to pick a free port from the OS.
- **Browser auto-open detection.** `webbrowser.open()` returns `True` even when no
  browser is actually launched on some headless Linux setups. We'll always print the
  URL prominently regardless, so the failure mode is benign.
- **App-factory vs module-level FastAPI instance.** I'll use `create_app()` factory
  pattern so tests can instantiate fresh apps without import-time side effects. This
  also leaves room for app-level config injection later (e.g. custom static paths).

---

## Phase UI-0 — Governance + UI Scaffolding

**Status:** Complete
**Date:** 2026-05-16

### Completed

- Renegotiated CLAUDE.md "Hard Constraint" #9 from "CLI only, no GUI, no Streamlit, no web server"
  to: CLI is the primary interface; an optional `[ui]` extra provides a localhost-only web
  frontend over the same runner/reporter primitives; UI never accepts secret values as input
  (only env var names); CI/CD continues to use the CLI exclusively. Recorded the renegotiation
  date inline in CLAUDE.md so the flip is auditable.
- Updated CLAUDE.md directory structure to show planned `sentinel/ui/` and `tests/ui/` layouts.
- Added `[ui]` extra to pyproject.toml: `fastapi>=0.110`, `uvicorn[standard]>=0.30`,
  `jinja2>=3.1`, `python-multipart>=0.0.9`. Core install is unchanged.
- Created `sentinel/ui/__init__.py` with an import gate: if FastAPI isn't importable, raise
  `ImportError` with the exact `pip install 'api-sentinel[ui]'` hint. Core tool never
  imports `sentinel.ui`, so users without the extra are unaffected.
- Created `tests/ui/test_import_gate.py` with two cases: gate passes when FastAPI is present,
  and gate raises a clear install hint when it is absent (via `monkeypatch.setitem(sys.modules,
  "fastapi", None)`).

### Decisions

- **Governance flip happens in writing, not silently.** The CLAUDE.md constraint was rewritten
  with an inline "renegotiated on 2026-05-16" note. Future readers can see *that* it changed
  and *when*, without spelunking commit history.
- **Stack: FastAPI + Jinja2 + HTMX (bundled), no JS framework, no CSS framework.** Async-native
  to match the existing runner, zero client-side state surface, no Node toolchain. HTMX will
  be vendored as a static asset in later phases — no CDN dependency at runtime.
- **`[ui]` is opt-in, never required.** Core `pip install api-sentinel` is unchanged in size
  and dependency surface. CI/CD users never pay the UI tax.
- **Gate checks only `fastapi`, not every UI dep.** Keeps the error message clean and the gate
  short; if `jinja2` or `python-multipart` are somehow missing, they'll fail at use-time with
  their own clear errors.
- **Goal shift recorded:** API Sentinel's audience is broadening from "tool for myself" to
  "OSS tool for novice engineers." Accessibility now outranks minimalism in tradeoff calls
  going forward.

### Testing

- 2 new tests in `tests/ui/test_import_gate.py`. `pytest tests/ui/test_import_gate.py` ⇒
  1 passed, 1 skipped (the "gate passes" case is auto-skipped via `pytest.importorskip` when
  the `[ui]` extras aren't installed — it activates as soon as the user runs
  `pip install -e ".[ui]"`).
- Pre-existing 152 tests unaffected by UI-0 changes. (In a fresh clone the existing suite
  needs `pip install -e ".[dev]"` to pull `respx`; that's clone-setup, not a UI-0 regression.)
- No source files in `sentinel/` other than the new `ui/__init__.py` were modified — runner,
  reporter, checks, CLI all unchanged.

### Open Questions for Phase UI-1

- **Argparse subparser fallback for bare `sentinel`.** The plan calls for `sentinel` with no
  args to behave exactly like `sentinel scan`. Argparse handles "no subcommand" awkwardly —
  the simplest implementation is a pre-parse pass that injects `scan` into `sys.argv` if no
  known subcommand is present. Will validate this preserves every existing CLI test unchanged.
- **`sentinel init` stub now or in its own phase later?** Phase UI-1 adds a `sentinel init`
  subparser purely to reserve the verb. The OpenAPI auto-config implementation is a separate
  🔴 Essential feature with its own plan. Confirming: the stub just prints "not yet
  implemented" and exits 0.

---

## v0.1.0 — Initial Release

**Status:** Complete
**Date:** 2026-04-16
**Repository:** https://github.com/NavyDevilDoc/api-sentinel (private)

### Summary

All 9 build phases complete. 152 tests passing. 43 files, 8,015 lines of code.
Pushed to GitHub as a private repository for pre-release testing against animeintel.app.

### Final Metrics

| Metric | Value |
|---|---|
| Source files | 22 (sentinel/) |
| Test files | 9 (tests/) |
| Total tests | 152 |
| Check categories | 6 (transport, headers, auth, authorization, rate_limit, input_handling) |
| LLM backends | 4 (gemini, claude, openai, ollama) |
| OWASP Top 10 coverage | ~65-70% |
| Python version | 3.11+ (tested on 3.14.2) |
| Platform tested | Windows 11 |

### Architecture Highlights

- **Pydantic for all data shapes** — config, results, reports. No raw dicts between modules.
- **Async from day one** — BaseCheck.run() is async; CLI uses asyncio.run(). Rate limit burst
  testing uses asyncio.create_task() + asyncio.wait() for true concurrent requests.
- **CHECK_REGISTRY pattern** — simple dict mapping category name to check class. Each phase
  added its entry without modifying the runner's core logic.
- **Token resolution inside checks** — each check module resolves its own env vars. The runner
  stays decoupled from auth concerns.
- **Optional LLM dependencies** — core tool has zero LLM SDK requirements. Users install what
  they need via pip extras.

### Issues Encountered and Resolved (across all phases)

1. **Windows cp1252 encoding** (Phase 1) — Rich Unicode chars fail on legacy terminal. Fixed
   with ASCII fallbacks and UTF-8 stdout wrapper.
2. **Rich ANSI in test assertions** (Phase 1) — force_terminal=True breaks string matching.
   Fixed with force_terminal=False in test console.
3. **TLS version detection** (Phase 2) — httpx doesn't expose negotiated TLS. Solved with
   raw ssl.SSLSocket via run_in_executor.
4. **Cert expiry off-by-one** (Phase 2) — timing-sensitive day count in tests. Fixed by
   asserting threshold instead of exact days.
5. **Shared helper refactor** (Phase 4) — endpoint_slug/resolve_path moved from auth.py to
   base.py when rate_limit.py needed them too.
6. **Async concurrency test flakiness** (Phase 4) — itertools.count()-based respx side_effect
   controls response ordering regardless of asyncio scheduling.
7. **importlib.import_module mock interference** (Phase 8) — global importlib patching broke
   unittest.mock internals. Fixed with patch.dict("sys.modules").

### Next Steps

- Test against animeintel.app with a real sentinel_config.yaml
- Review findings and triage false positives
- Address any issues discovered during real-world testing
- Make repository public when confident in results

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
