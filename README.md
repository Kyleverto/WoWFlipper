# WoWFlipper

A local retail US auction research desk for recipes, mounts and toys. It combines public TradeSkillMaster regional statistics with Blizzard connected-realm auction snapshots, estimates resale proceeds after fees, and tracks a manually maintained shopping list and outcome ledger. It never buys, posts, cancels auctions or controls the game. Check each auction, item variant, tradeability, destination and transfer eligibility in game before acting.

## Install and run

Requires Python 3.11 or newer. From the project directory on macOS/Linux:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
wowflipper --demo
```

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install .
wowflipper --demo
```

If script activation is disabled, use `.\.venv\Scripts\python.exe` and `.\.venv\Scripts\wowflipper.exe` directly. Open [the local desk](http://127.0.0.1:8765). `--port 8766` chooses another port. The service binds only to 127.0.0.1 and is intended for one local user.

The default data directory is `~/.local/share/wowflipper` on every OS; override with `--data-dir PATH`. Demo mode uses its `offline-demo` subdirectory and synthetic data with visible offline labels. Live mode refuses a demo database. Offline imports remain labelled as imported evidence; use a separate data directory if you want to keep imports apart from live data. Runtime databases, caches, outcomes and credentials belong outside Git.

## Credentials and live refresh

Create a Blizzard developer client at [the official developer portal](https://develop.battle.net/). Keep the client secret local. `.env.example` lists the two required variables, but **the application does not load `.env` automatically**. Set them in the same shell that starts the server:

```sh
export BLIZZARD_CLIENT_ID='your-client-id'
export BLIZZARD_CLIENT_SECRET='your-client-secret'
wowflipper
```

PowerShell:

```powershell
$env:BLIZZARD_CLIENT_ID = 'your-client-id'
$env:BLIZZARD_CLIENT_SECRET = 'your-client-secret'
wowflipper
```

Use local shell input or a secret manager; do not paste secrets into chat, commit them or include them in screenshots. TSM's public US CSV requires no credential. Blizzard OAuth client credentials are used only by the backend. Refresh discovers realm slugs and their connected auction houses; realms sharing a house are deduplicated. The default buying realm is Agamaggan (`agamaggan`). Select your own selling realm(s) explicitly in Settings; none are invented or preselected. Realm selection uses Blizzard’s explicit United States category, excluding Oceanic, Brazil and Latin America from new choices. Search narrows the displayed list without clearing checked realms. Select all US buying realms adds every discovered US realm; Clear buying selection clears buying selections only. Previously selected non-US or unclassified realms remain labeled so you can explicitly remove them. After upgrading, refresh once to retrieve category labels for older cached realm lists. Discovery currently requests details for each realm and can take time. Refresh shows progress and isolated source failures. Zero qualifying rows is a valid result.

Opportunity analysis is reused across page, search and ranking requests. New snapshots, saved settings, realm mappings and source freshness boundaries invalidate the analysis. The first calculation after a change can still take longer; the page displays loading and error states instead of implying an empty result. Keep the server running while sources refresh.

## Research assumptions

The browser displays and accepts monetary settings and ledger amounts in **gold** (up to four decimal places); storage, APIs and CSV monetary fields use **integer copper**: 10,000 copper = 1 gold. Default capital is 30,000g, with 20,000g for stronger demand, 5,000g longer term, 5,000g reserve, and 3,000g maximum item exposure. Shopping and held inventory both consume allocations and exposure; every listing is indivisible and stack quantity counts against the copy limit. Defaults of 15% discount, 5% auction cut and 100% regional average sale price as resale estimate are assumptions. Other filters (100g minimum profit, 10% minimum ROI, 0.01 sale rate/sold per day, one copy, 48-hour TSM age and 2-hour auction age) are provisional, unvalidated presets. Tune them to your own risk and evidence.

Expected proceeds are rounded down after the auction cut; profit subtracts whole-listing buyout and configured extra costs. Regional sale rate and sold-per-day are regional historical measures, not destination-specific demand or promised proceeds. Selling-realm comparisons show exact eligible auction competition; absent competition does not prove demand. Cross-house transfer eligibility is assumed and flagged for manual verification. Pets, gear and uncertain variants are inspection-only; uncertain category, tradeability, missing valuation or unknown/stale timestamps can exclude a row or add risk. Live enrichment is bounded to 100 uncached plausible item lookups per house per refresh; toy membership discovery is incremental (50 per refresh).

Freshness uses upstream source time, never download time. Blizzard auction time comes from Last-Modified; missing/invalid time excludes actionable evidence. TSM time comes from CSV `updatedAt`. Cached revalidation does not make old data fresh. The desk retains two successful raw snapshots per source and preserves provenance referenced by outcomes. CSV export applies the current visible query, ranking and sort and includes source times, provenance, risks and mode labels.

## Offline imports

Import the public regional CSV or Blizzard-shaped JSON through the desk. Supply an explicit timezone-aware ISO source time (for example `2026-10-12T12:00:00Z`), and a numeric connected-house ID for auction JSON. Missing source time excludes actionable results. Regional CSV requires `itemId,name,marketValue,historical,avgSalePrice,saleRate,soldPerDay,updatedAt`; price fields are copper. Malformed/conflicting rows cause import rejection.

Auction JSON must contain a nonempty `auctions` array with positive `id`, `item.id`, `quantity`, and whole-listing `buyout`. To qualify, imported entries also need a supported `category` and explicit metadata; standard Blizzard JSON alone does not establish these:

```json
{"auctions":[{"id":123,"item":{"id":456},"quantity":1,"buyout":1000000,"category":"recipe","metadata":{"simple_identity":true,"tradeability":true}}]}
```

Do not claim simple identity for ambiguous bonus lists, modifiers or pet fields: the engine checks actual variant fields too. Import metadata and source time are user assertions, not verified live facts. Refresh must discover the selected realm-to-house mapping (demo has synthetic mapping). Imports are useful for inspection even when evidence prevents actionable rows.

## Shopping and outcomes

Selecting a row creates a plan, not a purchase. Record a purchase only after buying manually; enter actual whole-listing cost, which is rechecked against budgets while preserving the quoted snapshot. Sales use actual **net proceeds received**, after auction cut, rather than gross sale price. Record extra fees separately only when not already included in net proceeds; do not deduct the cut twice. Partial sales allocate lot cost proportionally, with the remainder reconciled on the final sale. Expiration, cancellation and rejection do not remove held inventory; record any actual unrecovered fees separately.

Corrections append immutable events targeting the original event ID. Replacement fields update the current effective event; later corrections build on those changes. A null replacement voids it; restoring after void uses the original event as its base. History dependencies and cash balances are replayed before accepting a correction (a purchase cannot be voided while dependent sales remain). Corrections record actual history; corrected holdings that exceed current planning allocations, exposure or copy limits are flagged in the summary rather than rejected. New purchases remain constrained by those planning limits. History preserves original events and corrections. Balance adjustments require a note. Initial capital is frozen after the first outcome; later cash changes require a balance adjustment. The ledger is a local record of what you enter, not bank/game reconciliation or tax accounting.

## Validation and development

```sh
python -m pip install '.[test]'
python -m pytest -q
python scripts/smoke_live.py --tsm-only
python scripts/smoke_live.py --data-dir ~/.local/share/wowflipper
```

The Python test suite also runs JavaScript behavior tests when Node.js is installed; those tests are skipped on Python-only installations.

Smoke is opt-in and uses real source adapters. Full smoke reads configured settings, discovers realms, fetches configured houses, enriches metadata and runs the opportunity query in temporary storage without changing your ledger. It reports source states and row counts without credentials or upstream bodies. Exit codes: 0 success (including zero qualifying rows), 1 validation/source failure, 2 missing credentials or demo storage. Run full smoke after exporting credentials; source freshness/eligibility may still prevent opportunities. CI runs tests on Python 3.11 and 3.14 and checks a wheel installation outside the checkout. See [validation evidence and remaining checks](docs/validation.md).

Opportunity results show at most 100 rows per page with an eligible total and page controls. Export visible CSV uses the accepted search, ranking, sort and current page. Inspection exclusions load only when opened and show at most 100 per page with their own total and controls. CSV includes explicit numerical assumptions and compact JSON destination evidence (competition, risks, source/fetch timestamps, source key, snapshot ID/hash and mode).
