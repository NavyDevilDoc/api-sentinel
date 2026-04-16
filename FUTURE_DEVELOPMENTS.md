# FUTURE_DEVELOPMENTS.md — API Sentinel
**Feature Backlog & Enhancement Roadmap**
Last Updated: 2026-04-16

---

## How to Use This File

Features are grouped by category and tagged with a priority tier:

- 🔴 **Essential** — Closes a meaningful gap in v1.0. High integrity, high adoption impact.
- 🟡 **High Value** — Significantly improves usability or coverage. Strong candidates for v1.x.
- 🟢 **Nice to Have** — Polishes the experience or expands reach. Great for community contributions.
- 🌙 **Moonshot** — Ambitious, defining, worth planning toward.

No implementation order is implied within tiers. Sequence depends on user feedback
and open-source contributor interest after initial release.

---

## Category 1: Closing the OWASP Coverage Gaps

These features move the tool from ~65% OWASP Top 10 coverage to credible, comprehensive
security tooling. Highest integrity additions.

---

### GraphQL Support
**Tier:** 🔴 Essential
**OWASP:** Unrestricted Resource Consumption, Security Misconfiguration, Improper Inventory Management

GraphQL APIs have a distinct attack surface not covered by v1.0's REST-focused checks.
Add a `graphql:` section to `sentinel_config.yaml` and a dedicated check module covering:

- Introspection endpoint exposure (should be disabled in production)
- Deeply nested query attacks that exhaust compute resources
- Batching abuse (sending hundreds of operations in a single request)
- Field suggestion leakage (GraphQL hints at valid field names on typos)

**Implementation note:** Requires schema introspection as a first step. Use `httpx` to
fetch the schema, then generate targeted attack queries from it programmatically.

---

### OAuth 2.0 Flow Validation
**Tier:** 🔴 Essential
**OWASP:** Broken Authentication

Most production APIs use OAuth 2.0. Full flow automation (redirect handling) is explicitly
out of scope for v1.0, but meaningful partial coverage is achievable:

- Token expiry enforcement — does the server reject expired JWTs?
- Scope enforcement — can a token with read-only scope perform write operations?
- Redirect URI whitelisting — does the auth server accept arbitrary redirect URIs?
- Token reuse after logout/revocation

**Implementation note:** User provides tokens at various lifecycle stages via config.
The tool does not automate the redirect dance — it tests token behavior directly.

---

### SSRF Probing Module
**Tier:** 🔴 Essential
**OWASP:** Server-Side Request Forgery (SSRF)

Expand beyond the basic probe strings in v1.0 `input_handling.py`. A dedicated SSRF
module sends crafted payloads targeting:

- AWS metadata endpoint: `http://169.254.169.254/latest/meta-data/`
- GCP metadata endpoint: `http://metadata.google.internal/`
- Azure metadata endpoint: `http://169.254.169.254/metadata/instance`
- Internal loopback and RFC 1918 address ranges
- DNS rebinding payloads

Detects SSRF by analyzing response signatures, timing anomalies, and error message
content that indicates the server attempted to fetch the payload.

**Important:** Only run against APIs you own or have explicit written permission to test.
Add a prominent disclaimer to the user guide and a confirmation prompt for this module.

---

## Category 2: Developer Experience

Features that determine whether developers actually adopt the tool over existing
alternatives. Adoption impact is high.

---

### `sentinel init` — OpenAPI/Swagger Auto-Config
**Tier:** 🔴 Essential

The single highest-leverage DX feature. Drops config writing time from ~20 minutes
to one command.

```bash
sentinel init --spec ./openapi.json
sentinel init --spec https://api.example.com/openapi.json
```

Crawls the OpenAPI 3.0 or Swagger 2.0 spec and auto-generates a `sentinel_config.yaml`
populated with every endpoint, HTTP method, auth requirement, and parameter schema.
User reviews and adds token env var names, then runs.

**Why it matters for open source:** Zero-friction onboarding is the single biggest
predictor of GitHub stars and repeat use. This feature makes the tool feel magical
on first contact.

---

### Watch Mode
**Tier:** 🟡 High Value

```bash
sentinel --watch
```

Re-runs affected checks automatically when `sentinel_config.yaml` changes. Useful
during active API hardening — make a fix, save the config, see the result immediately
without re-running manually. Behavior modeled on `pytest-watch`.

---

### `--diff` Flag — Security Posture Regression Detection
**Tier:** 🟡 High Value

```bash
sentinel --output json > before.json
# deploy changes
sentinel --output json > after.json
sentinel --diff before.json after.json
```

Compares two JSON export files and produces a change report: new findings, resolved
findings, severity changes. Makes security regression visible in pull request reviews
and post-deploy validation. Pairs naturally with CI pipelines.

---

### Timing Attack Detection
**Tier:** 🟡 High Value
**OWASP:** Broken Object Level Authorization (side-channel variant)

Measure response time variance between requests for valid vs. invalid resource IDs.
A statistically significant timing difference leaks resource existence even when the
server returns identical 404 responses for both cases.

Requires multiple samples per endpoint to establish a baseline. Use scipy or
statsmodels for significance testing. Flag as WARNING when variance exceeds threshold.

**Implementation note:** Results are probabilistic, not deterministic. Report the
confidence interval alongside the finding, not just pass/fail.

---

### Configurable Injection Payload Libraries
**Tier:** 🟡 High Value

Replace the static probe strings in v1.0 `input_handling.py` with a configurable
payload source:

```yaml
input_handling:
  payload_library: ./payloads/sqli.txt    # local file
  # or
  payload_library: seclists/sqli-blind    # named preset
```

Enables integration with established wordlists (SecLists, PayloadsAllTheThings)
without bundling them. Ship a minimal built-in library; let power users bring their own.

---

### VS Code Extension
**Tier:** 🟢 Nice to Have

A sidebar panel displaying the last Sentinel run results for the open project.
Color-coded findings, click-to-jump to the relevant config line, one-click re-run.

No functionality beyond the CLI — purely a visual layer over existing JSON output.
Good candidate for a community contribution after v1.0 establishes the JSON export
contract.

---

## Category 3: Reporting & Integrations

Features that make findings actionable across teams and toolchains.

---

### HTML Report Output
**Tier:** 🟡 High Value

```bash
sentinel --output html > report.html
```

Self-contained single-file HTML report. No external dependencies, no server required.
Shareable with non-technical stakeholders — a product manager or client can open it
in a browser without any tooling. Styled with inline CSS to match the Rich terminal
aesthetic.

---

### Markdown Report Output
**Tier:** 🟡 High Value

```bash
sentinel --output markdown
```

Produces output formatted for GitHub Flavored Markdown. Designed to be pasted
directly into a GitHub issue, PR comment, or wiki page. Findings render as
checkboxes — teams can track remediation progress in the issue itself.

---

### JUnit XML Output
**Tier:** 🟡 High Value

```bash
sentinel --output junit > sentinel-results.xml
```

JUnit XML is the lingua franca of CI test reporting. Jenkins, GitLab CI, CircleCI,
and most other platforms natively parse and visualize it. This format makes Sentinel
findings appear as first-class test results in any CI dashboard with zero extra
configuration.

---

### CI Workflow Generator
**Tier:** 🟡 High Value

```bash
sentinel --generate-ci github     # outputs .github/workflows/api-security.yml
sentinel --generate-ci gitlab     # outputs .gitlab-ci.yml security stage
sentinel --generate-ci circleci   # outputs .circleci/config.yml security job
```

Generates a ready-to-use CI configuration file for the specified platform. One command
and your security checks are running on every push. Removes the last friction point
between "local tool" and "integrated pipeline."

---

### Webhook Notifications
**Tier:** 🟢 Nice to Have

```yaml
# sentinel_config.yaml
notifications:
  slack_webhook: SENTINEL_SLACK_WEBHOOK
  discord_webhook: SENTINEL_DISCORD_WEBHOOK
  on: [critical, run_complete]
```

Post results to a Slack channel or Discord server on run completion or on critical
findings. Simple to implement, high team visibility. A red finding in a shared channel
is much harder to ignore than a terminal output nobody's watching.

---

## Category 4: Depth & Intelligence

Features that add analytical capability beyond pass/fail checks.

---

### Authenticated Endpoint Discovery
**Tier:** 🟡 High Value
**OWASP:** Improper Inventory Management (Shadow APIs)

Given a valid token, crawl API response bodies and headers for linked resource URLs.
Follow `Location` headers, parse `_links` and `href` fields in JSON responses, and
surface endpoints not listed in `sentinel_config.yaml`.

Detects shadow APIs and undocumented routes — a direct hit on OWASP #9. Users review
the discovered endpoints and decide whether to add them to their config.

**Important:** Crawl depth must be configurable and capped. Default max depth: 2.

---

### Historical Baseline Tracking
**Tier:** 🟡 High Value

Store results in a local SQLite file (`.sentinel/history.db`) after every run.
Surface trend information in the terminal report:

```
⚠️  WARNING COUNT TREND: +3 over last 5 runs. Review recent changes.
```

No external service required. SQLite is stdlib-adjacent (via Python's built-in
`sqlite3` module). Enables the `--diff` flag to work without manually saving JSON
files.

---

## Category 5: The Moonshot

---

### Public Results Registry & Security Badge
**Tier:** 🌙 Moonshot

The feature that could define the tool's reputation in the open-source ecosystem.

**Concept:** An opt-in, anonymized public registry. When a developer runs Sentinel
against their own API and achieves a clean pass on all critical checks, they may
submit a verified result to the registry. In return, they receive a badge:

```markdown
[![API Sentinel](https://badge.apisentinel.dev/v1/myproject)](https://apisentinel.dev)
```

The badge displays in their README. It is the HTTPS padlock equivalent for API
security hygiene — a visible, verifiable signal that a project takes security seriously.

**What makes this different from existing badge services:**
- Cryptographically signed by the Sentinel CLI at run time, not self-reported
- Tied to a specific config hash and run timestamp — not "we passed once"
- Automatically expires after N days, encouraging regular re-runs
- Anonymized — the registry never stores endpoint paths, tokens, or response data;
  only the domain, pass/fail counts, and a signed timestamp

**Infrastructure required:**
- A lightweight public API (FastAPI on Railway fits perfectly)
- A signing scheme in the CLI (HMAC with a per-registration secret)
- A badge rendering endpoint (SVG, similar to shields.io)
- A public leaderboard page (optional — most-checked domains, recent passes)

**Why it creates a network effect:** Every README badge is a passive advertisement.
Developers click it, land on apisentinel.dev, and discover the tool. No marketing
required. The community builds itself.

**Implementation note:** This feature requires the v1.0 tool to be stable and trusted
first. Do not rush it. A compromised or gamed badge system would permanently damage
the tool's credibility. Launch this only when the check coverage is rigorous enough
that a "passing" result genuinely means something.

---

## Contribution Notes

When this project goes open source, the following features are best suited for
community contributions (low architectural risk, well-defined scope):

- Webhook notification backends (additional platforms beyond Slack/Discord)
- Additional CI workflow generators (Bitbucket, Azure DevOps, Jenkins)
- Payload library presets
- VS Code extension
- Additional LLM backends for `--report llm`

The following features should remain core-team controlled due to architectural impact:

- OpenAPI auto-config (`sentinel init`)
- Historical baseline tracking (touches the data model)
- Public results registry (security and trust implications)
- GraphQL support (requires significant check module redesign)
