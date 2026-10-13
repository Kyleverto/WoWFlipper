# WoWFlipper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Build and publish a runnable local auction research application to Kyleverto/WoWFlipper, implementing the supplied first-version design.

**Architecture:** Python source adapters feed validated immutable snapshots into SQLite. A pure deal engine calculates opportunities; a transactional ledger and shopping service enforce shared capital. A loopback Flask server serves a browser interface with no hosted infrastructure.

**Tech Stack:** Python 3.11+, Flask, requests, SQLite, Decimal, pytest, vanilla JavaScript/CSS. Browser automation verifies user workflows. Pin tested dependency versions during implementation.

**Spec:** `docs/superpowers/specs/2026-10-12-flip-finder-design.md`, supplemented by the attached project brief. These are product requirements, not authorization to operate the game or third-party accounts.

## Global constraints

- Retail US initially; default home realm Agamaggan; never invent other selling realms.
- Local service on 127.0.0.1 only; no buying, posting, cancellation, game control, bank access, or hosted accounts.
- Integer copper; Decimal percentages; round proceeds down to whole copper.
- Initial capital 30,000g; stronger-demand allocation 20,000g; longer-term allocation 5,000g; reserve 5,000g; item exposure 3,000g.
- Default discount 15%; auction cut 5%; resale factor 100%. Other presets are explicitly provisional.
- Regional sales statistics are not destination-specific demand or guaranteed proceeds.
- Missing/stale source time and uncertain variants prevent actionable inclusion.
- Entire auction listings are indivisible; use total buyout and quantity consistently.
- Retain two successful raw snapshots per source; preserve immutable provenance referenced by outcomes.
- Never commit credentials, caches, databases, personal outcomes, or actual user settings.

## Verified starting point

The workspace has one committed design document, no application, a clean working tree, and no Git remote. The GitHub connector located the empty public repository `Kyleverto/WoWFlipper`, with push permission.

On October 12, 2026, downloaded the actual US TSM regional CSV to a temporary file. It contained 30,006 unique item IDs with these columns:

```text
itemId,name,marketValue,historical,avgSalePrice,saleRate,soldPerDay,updatedAt
```

All rows parsed numerically and had ISO timestamps; this particular file used `2026-10-12T08:26:39Z`. It contains no variant valuation key. Do not interpret that sample timestamp as a permanent cadence guarantee. Source: https://public-data.tradeskillmaster.com/retail/us/region/items.csv and https://tradeskillmaster.com/public-data.

The Blizzard documentation portal was reachable but its rendered API specification was not exposed by the text reader. OAuth, endpoint shapes, and auction timestamp behavior still require verification during Task 2. No authenticated Blizzard request has been made. Missing credentials must not stop offline implementation or be misreported as live success.

## Review focus

1. Nonfinite decimals and future timestamps: reject before scoring (Task 1).
2. Repeated HTTP mutations and concurrent tabs: idempotency and transactions prevent duplicate purchases/sales (Task 4).
3. Hostile source names and spreadsheet formulas: text-only rendering and safe CSV cells (Task 5).
4. External websites targeting localhost: Host/Origin checks and CSRF protection block writes (Task 5).
5. Process failure between raw-file write and database publication: prior successful snapshot remains usable (Task 2).

## File and interface map

```text
pyproject.toml                 package, runtime/test dependencies, command entry point
.gitignore                     secrets, runtime files, build/test caches
.env.example                   credential variable names only
README.md                      setup, credentials, imports, assumptions, verification
wowflipper/__main__.py         CLI and loopback launch
wowflipper/models.py           typed immutable domain records
wowflipper/settings.py         validated defaults and settings serialization
wowflipper/sources/http.py     bounded transport, retries, redacted errors
wowflipper/sources/tsm.py      regional CSV parser and downloader
wowflipper/sources/blizzard.py OAuth, realm discovery, auctions, item metadata
wowflipper/repository.py       schema, transactions, snapshot storage and retention
wowflipper/refresh.py          source orchestration and per-source progress
wowflipper/engine.py           pure matching, valuation, comparison and rankings
wowflipper/portfolio.py        shopping list, inventory and append-only outcomes
wowflipper/web.py              app factory and HTTP routes
wowflipper/templates/index.html browser shell
wowflipper/static/app.js       browser interaction and safe rendering
wowflipper/static/style.css    responsive interface
wowflipper/demo.py             explicit synthetic fixture loader
scripts/smoke_live.py          opt-in actual-source validation
 tests/                       mirrored unit and integration modules
.github/workflows/tests.yml    automated tests on supported Python versions
```

Domain records: `Valuation(item_id, name, avg_sale_price, sale_rate, sold_per_day, market_value, historical, updated_at)`; `Auction(region, house_id, auction_id, item_id, quantity, buyout, variant_key)`; `Snapshot(source_key, source_time, fetched_at, http_last_modified, mode, records)`; `Candidate(key, auction, valuation, proceeds, profit, roi, destinations, risks, explanation)`.

All monetary attributes are integer copper, rates are Decimal, times are timezone-aware UTC or explicitly unknown. Candidate identity is `(region, house_id, auction_id)`, not destination or ranking. Snapshot provenance also includes content hash and immutable source identifier. Public JSON serializes Decimal as strings and money as integer values within supported safe bounds.

### Task 1: Validated inputs and application package

**Files:** package metadata, models, settings, TSM parser; `tests/test_inputs.py`, small synthetic fixtures.

**Interfaces:** `parse_region(text: str) -> list[Valuation]`; `Settings.from_dict(data: dict) -> Settings`; `normalize_auction(data: dict, house_id: int, region: str) -> Auction | None`.

- [x] Write failing parser/settings tests. Use an explicit eight-column CSV fixture and this currency assertion:

```python
def test_regional_copper_and_demand_are_preserved():
    row = parse_region(CSV_FIXTURE)[0]
    assert row.avg_sale_price == 1_000_000_000
    assert row.sale_rate == Decimal('0.025')
    assert row.sold_per_day == Decimal('0.05')
    assert row.updated_at.utcoffset().total_seconds() == 0
```

- [x] Run `python -m pytest tests/test_inputs.py -q`, confirm missing implementation fails.
- [x] Implement strict CSV headers, finite/nonnegative rates, integral money, positive quantities, timezone-aware timestamps, settings bounds and unknown-age handling. Reject NaN/infinity, negative buyout, boolean IDs, future market timestamps beyond five minutes, and duplicate conflicting IDs. Extra CSV columns are allowed. Row failures are counted and exposed; missing required headers fail the entire source.
- [x] Use settings defaults of 100g minimum profit, 10% minimum return, 0.01 minimum sale rate, 0.01 sold/day, 48-hour TSM age, 2-hour auction age, one copy per item, zero extra costs. Label these unvalidated provisional filters. Validate allocation plus reserve against configured capital.
- [x] Run parser tests and a separate smoke parse of the downloaded real CSV. Record counts without committing downloaded data.
- [x] Commit package and input validation.

### Task 2: Source adapters and atomic persistence

**Files:** sources/http.py, sources/blizzard.py, repository.py, refresh.py; `tests/test_sources.py`, `tests/test_repository.py`.

**Interfaces:** `BlizzardClient.realms() -> list[dict]`; `BlizzardClient.auctions(house_id: int) -> Snapshot`; `Repository.publish(snapshot: Snapshot) -> int`; `Repository.latest(source_key: str) -> Snapshot | None`; `RefreshService.run(settings: Settings) -> dict`.

- [x] Write failing transport, deduplication and preservation tests with an injected fake HTTP transport:

```python
def test_failed_refresh_preserves_success(repo, valid_snapshot, malformed_snapshot):
    first_id = repo.publish(valid_snapshot)
    with pytest.raises(ValueError):
        repo.publish(malformed_snapshot)
    assert repo.latest(valid_snapshot.source_key).id == first_id
```

- [x] Run `python -m pytest tests/test_sources.py tests/test_repository.py -q` and confirm failures.
- [x] Verify current Blizzard OAuth and Game Data contracts from official documentation. Implement backend client-credentials auth, token-expiry caching and one renewal on 401. Read `BLIZZARD_CLIENT_ID` and `BLIZZARD_CLIENT_SECRET` from environment; never echo secret-bearing responses or headers.
- [x] Discover realm/connected-realm membership; deduplicate the union of selected buying/selling houses. Query realm auctions, not region commodities. Fetch cached item metadata to classify supported identities; do not guess categories from names. Unknown metadata goes to inspection.
- [x] Implement HTTPS-only allowlisted upstreams, finite response-size limits, connect/read timeouts, at most three attempts, and Retry-After handling. Long Retry-After defers the source instead of retrying early. Retry transient transport/429/5xx failures, not bad schemas or invalid credentials.
- [x] Store source scan time, HTTP Last-Modified, and local fetch time separately. Auction freshness derives from verified snapshot metadata; missing timestamp remains unknown. Conditional 304 retains original market time.
- [x] Write raw payload to a temporary file, atomically rename, then publish normalized records in one SQLite transaction. Cleanup of unreferenced payloads follows commit; keep two successful snapshots per source and compact provenance needed by ledger references.
- [x] Track per-source status and errors; partial failure retains previous records with their original age. Reject overlapping refresh requests; use one background worker and polling status endpoint.
- [x] Run tests including interrupted publication, truncated JSON, unknown timestamps, token expiry, 429, duplicate houses and partial realm failure; commit adapters and persistence.

### Task 3: Pure deal engine and destination comparison

**Files:** engine.py; `tests/test_engine.py`.

**Interfaces:** `evaluate(auction: Auction, valuation: Valuation, settings: Settings, now: datetime) -> Candidate | None`; `rank(candidates: list[Candidate], strategy: str) -> list[Candidate]`; `compare_destinations(candidate, snapshots, settings) -> list[dict]`.

- [x] Write failing calculation tests, including the brief's example under permissive budget settings:

```python
def test_profit_after_cut(example_auction, example_value, permissive_settings, now):
    candidate = evaluate(example_auction, example_value, permissive_settings, now)
    assert candidate.proceeds == 950_000_000
    assert candidate.profit == 100_000_000
```

- [x] Run `python -m pytest tests/test_engine.py -q` and confirm failure.
- [x] Implement Decimal arithmetic and floor rounding. For quantity q, compare full buyout against per-unit ceiling multiplied by q; profit subtracts full purchase and configured per-listing costs. Never round a per-unit purchase down before threshold comparison.
- [x] Gate stale/unknown sources, invalid valuations, missing item metadata, unsupported complex variants and pets. Support confirmed simple non-commodity identities. Gear needs exact valuation correspondence, which the verified regional CSV does not supply: place ambiguous gear in inspection, not actionable rankings.
- [x] Rank stronger-demand by sold/day, sale rate, return, profit; longer-term by profit, return, sold/day, sale rate. Use stable auction identity as final tie-breaker. Return reasons and explicit risk flags.
- [x] Compare exact-match, fresh destination asking prices and quantities. Flag competition below assumed resale, absent destination evidence, and unverified transfer eligibility. Exclude the candidate listing itself from competition where source equals destination.
- [x] Test boundary equality, one-copper rounding, stacks, stale destinations, absent destinations, variant mismatch, and deterministic rankings; commit engine.

### Task 4: Shopping list and transactional outcome ledger

**Files:** portfolio.py, repository.py migrations; `tests/test_portfolio.py`.

**Interfaces:** `Portfolio.select(candidate_key, strategy, destination, request_id) -> dict`; `Portfolio.record(event: dict, request_id: str) -> dict`; `Portfolio.summary() -> dict`.

- [x] Write failing tests with synthetic candidate data:

```python
def test_duplicate_purchase_is_idempotent(portfolio, purchase_event):
    first = portfolio.record(purchase_event, request_id='purchase-1')
    second = portfolio.record(purchase_event, request_id='purchase-1')
    assert second == first
    assert portfolio.summary()['cash'] == 300_000_000 - purchase_event['cost']
```

- [x] Run `python -m pytest tests/test_portfolio.py -q` and confirm failure.
- [x] Store append-only purchase, partial-sale, fee, rejection, expiration, cancellation, balance-adjustment and correction events. Request IDs have a unique constraint; reusing an ID for different payloads is an error. Corrections reverse/replay a consistent event chain and reject negative inventory or invalid dependent sales.
- [x] Purchase reduces cash; sale adds entered net proceeds; separately recorded fees reduce cash. Expired/canceled/rejected events do not free inventory cost. Partial sale releases proportional cost basis; final unit absorbs residual copper. Manual balance adjustments require a note and never silently reset history.
- [x] Enforce shopping commitments plus existing inventory against item exposure/quantity and strategy allocations; enforce new spending against cash less reserve. Deduplicate auction identity across both rankings and destinations. Revalidate selections after settings/refresh changes and before recording a purchase.
- [x] Test partial sales, explicit fees, correction rollback, overselling, allocation exhaustion, reserve, held exposure, stale selections and concurrent duplicate requests. Commit portfolio.

### Task 5: Complete local browser workflow and export

**Files:** web.py, __main__.py, templates/index.html, static/app.js, static/style.css, demo.py; `tests/test_web.py`.

**Interfaces:** `create_app(data_dir: Path, testing: bool = False) -> Flask`; JSON routes for settings, realms, refresh/status, results, shopping and ledger; CSV export uses the same filter/ranking query as visible results.

- [x] Write failing integration tests:

```python
def test_external_origin_cannot_mutate(client):
    response = client.post('/api/settings', json={},
                           headers={'Origin': 'https://example.invalid'})
    assert response.status_code == 403
```

- [x] Run `python -m pytest tests/test_web.py -q` and confirm failure.
- [x] Implement loopback binding, allowlisted Host headers, same-origin checks, per-session CSRF tokens, no wildcard CORS and no debug mode. Settings API never returns credentials; only configuration status. Validate payload size and fields; return sanitized errors.
- [x] Build responsive navigation for Opportunities, Shopping, Ledger and Settings. Show both rankings, search, sortable columns, expandable destination comparison, source age/provenance, inspection exclusions, source progress and errors. Include all specified financial/demand columns and explicit manual-verification language.
- [x] Add editable buy/sell realm membership selection, budget/filter settings, shopping strategy/destination, purchase/sale/fee/adjustment/correction forms, and clear inventory status. Buttons report in-flight and error state without losing entered data.
- [x] Render external text with textContent or template escaping. Export only visible filtered results with timestamps, assumptions and risks. Prefix spreadsheet formula-leading textual cells; retain genuine numeric columns as numbers.
- [x] Provide an opt-in synthetic demo using an isolated data directory, prominent OFFLINE labels and no mixing into live capital/ledger. Provide explicit user-file CSV/auction JSON import with source-time entry; missing age remains excluded.
- [x] Test no secret leakage, bad origins/hosts, script-bearing item names, formula injection, invalid settings and export consistency. Run browser workflows: configure, refresh/demo, filter, compare, select, buy, partially sell, correct, export, restart and confirm persistence. Commit interface.

### Task 6: Live validation, documentation and GitHub delivery

**Files:** README.md, .env.example, scripts/smoke_live.py, .github/workflows/tests.yml; validation report under docs.

- [x] Implement opt-in smoke script that downloads TSM, discovers configured realms, fetches an authenticated auction snapshot, prints sanitized source/row counts and runs the pipeline. Zero qualifying results is valid; invented results are not.
- [x] Document venv install/run commands, environment credential setup, supported categories, freshness, assumed transfer eligibility, budget semantics, CSV export, imports/demo isolation and outcome correction. Include the requirement for in-game verification.
- [x] Add CI running `python -m pytest -q` on Python 3.11 and a current supported Python release. Build/install the package in a clean environment and verify the CLI launches.
- [x] Run the complete suite, browser workflow and actual TSM parse. Run Blizzard smoke when credentials are locally configured; otherwise report that precise unverified acceptance item and provide the executable command for the user. Do not ask the user to paste secrets into chat.
- [x] Review diff for defects and secret/runtime data; fix findings and rerun affected tests. Record completed checks and remaining live/in-game validation separately.
- [ ] Configure origin to the verified repository, inspect remote state again, commit intended source files and push a normal non-force branch update. If the remote has acquired content, fetch and reconcile without overwriting it. Use a feature branch/PR when integration requires review; otherwise publish initial implementation to the empty repository's main branch.
- [ ] Verify remote commit SHA and provide repository link, commit ID, local run command and material limitations. No completion claim until publication is confirmed.

## Plan self-review

All design sections map to the six tasks: adapters/storage (1–2), matching/valuation/rankings/destinations (3), budgets/outcomes (4), local UI/settings/export (5), live acceptance and delivery (6). The five review-focus risks are assigned concrete tests. Optional realm CSV enrichment is deferred because exact-match destination auction snapshots supply the initial comparison; pets and ambiguous gear remain inspection-only. No hosted deployment, automatic trading or extra selling realms are introduced.

## Execution review

The user approved subagent-driven execution. All six implementation tasks and their reviews are complete, including a whole-branch review and scoped fixes. Live Blizzard verification is deferred by user choice, with credential setup and smoke commands documented. Final publication/remote verification remain the delivery steps below.
