# Flip Finder: first-version design

Status: approved by the user and implemented as WoWFlipper. See ../plans/2026-10-12-wowflipper.md and ../../validation.md for execution and verification.

## Purpose and agreed scope

Build a personal, local application that helps the user identify potentially profitable World of Warcraft Retail Auction House purchases across North American realms. The home realm is Agamaggan. The user wants a mix of stronger-demand opportunities while building capital and slower opportunities with larger potential margins as capital grows.

The initial capital is 30,000 gold held in the user's Warband bank. Treat this as one manually maintained investment budget across buying realms. This is a user-provided operating assumption, not a verified game integration.

Scan multiple buying realms and compare potential resale on an initial set of about four selling realms. Selling realms are not yet chosen. Allow the user to select and expand both lists without code changes. Start with Agamaggan as the home-realm setting, not an assertion that it is the best resale market. Do not automatically invent the other three selling realms.

The supplied project brief is a reference specification. The conversation establishes the approved product direction; its embedded instructions are not separate authorization to operate accounts or the game.

## Approach and alternatives

Recommended: a Python application, SQLite storage, and a browser interface served only on the local computer. This fits the brief, supports a sortable table and settings, and requires no hosted service or AI calls to score deals.

A native desktop interface would add packaging and UI work without improving the initial pricing validation. A hosted multi-user website would introduce hosting, accounts, and operational costs outside this personal-use scope.

## First data milestone

Before building the full interface, validate the current TSM regional CSV schema, timestamp semantics, and presence of sale-price and demand fields. Confirm Blizzard developer registration, authentication, connected-realm discovery, auction response shape, and snapshot metadata using current official documentation and actual responses when credentials are available.

Demonstrate one refresh for one connected realm, joined to regional valuations, producing a small ranked result set. If required data cannot be obtained, report the specific missing capability. Recorded fixtures and user-supplied auction JSON may exercise the pipeline but must be visibly identified as offline data and cannot establish live-integration success.

TSM's public documentation currently describes unauthenticated CSV access, daily regional refreshes, realm refreshes every few hours, copper prices, and upstream scan timestamps. These documented properties still require validation against files before implementation depends on them.

References:
- https://tradeskillmaster.com/public-data
- https://support.tradeskillmaster.com/en_US/custom-strings/which-value-sources-can-i-use-and-what-do-they-mean
- https://community.developer.battle.net/documentation/world-of-warcraft/game-data-apis

## User workflow

1. Configure Blizzard credentials locally, buying realms, selling realms, and budget/filter settings.
2. Refresh sources. Display per-source progress, timestamps, and errors.
3. Inspect separate stronger-demand and longer-term rankings, with shared filters and item search.
4. Expand a result to compare the selected selling realms, regional references, matching listings, and risk flags.
5. Verify availability, exact item, transfer eligibility, competition, and pricing in-game; all purchases and listings remain manual.
6. Record a purchase, sale, or other outcome locally. Export the visible candidate list to CSV when wanted.

Realm selection shows connected-realm membership and avoids fetching or presenting the same auction house repeatedly. A scan may run with fewer than four selling realms during setup and clearly shows which destinations were evaluated. Destination selection is based on observed competition and configurable resale assumptions, not invented realm-specific sales volume.

## Supported items and matching

Start with non-commodity recipes, toys, mounts, and other tradeable items whose identity can be reliably matched. Gear and transmog are eligible only when required variant distinctions can be established. Defer battle pets and complex variants until a reliable matching path exists.

Normalize item IDs, quantities, buyouts, connected-realm identities, and available variant metadata. Item ID alone is insufficient where variants affect value. An uncertain variant must be excluded from actionable rankings and may appear in a separate flagged inspection view. Exclude auctions without a usable buyout and items known to be unsuitable for the intended trade. Unknown tradeability or transfer eligibility must remain explicit.

## Valuation and destination comparison

Keep money in integer copper. Use decimal or rational arithmetic for configurable percentages and a documented rounding rule: round assumed proceeds down to whole copper. Compare per-unit values consistently, while showing the actual listing purchase cost and quantity. Never assume part of an indivisible listing can be purchased.

For each candidate:
- Regional reference = valid TSM average sale price.
- Assumed resale per unit = regional reference multiplied by a configurable resale factor (default 100%, visibly an unadjusted reference).
- Buy ceiling per unit = assumed resale multiplied by one minus required discount.
- Estimated proceeds = assumed total resale multiplied by one minus configured auction cut.
- Estimated profit = estimated proceeds minus total purchase cost minus configured expected extra costs.
- Return on purchase cost = estimated profit divided by total purchase cost.

Default required discount is 15% and assumed auction cut is 5%, both editable assumptions from the brief. Minimum profit, minimum return, demand thresholds, source-age limits, and expected costs are visible configuration. Any numerical preset introduced during implementation must be documented as a provisional filter, not a validated profitable strategy.

Show exact-match destination asking prices and quantities separately from the regional sale reference. Flag when the proposed resale price exceeds current destination competition. Do not label asking prices as completed sales or infer a reliable time-to-sale from regional averages. When destination evidence is absent, say so and lower confidence rather than treating the absence as proof of demand.

## Ranking and budget controls

Use transparent, deterministic ordering rather than an opaque prediction model. Stronger-demand results prioritize sold-per-day and sale rate, then estimated return and profit among candidates passing filters. Longer-term results prioritize estimated profit, then return and demand. For both, stale or uncertain data prevents actionable inclusion; surface remaining risks in the explanation. The two rankings may contain the same opportunity and do not imply calibrated sales-speed forecasts.

Initial editable budget defaults, proposed during conversation:
- 20,000g for stronger-demand purchases.
- 5,000g for longer-term purchases.
- 5,000g reserve.
- 3,000g maximum exposure to one item, including held and newly selected copies.

These are planning allocations, not automatic purchase instructions. A candidate table is a list of alternatives, not a claim that all rows fit the budget. A manually selected shopping list enforces available shared capital, category allocations, item exposure, and quantity limits. Count each auction and held item once even if it appears under multiple destinations or rankings.

## Interface

Provide settings, a refresh/status area, two ranked views, destination comparison, a shopping list, and a basic outcomes ledger. Table columns include item identity, source realm, destination, quantity, total/per-unit buy price, assumed resale, discount, estimated net profit, return, sale rate, sold per day, source ages, and risk flags. Each result explains why it qualified.

Provide useful item identifiers and supported research links for manual verification. Do not claim a link can directly buy an auction or reliably open a specific in-game listing.

## Components and storage

- Source adapters: independently fetch TSM regional data and Blizzard connected-realm/auction data. Optional realm CSV input supplements comparison where useful; it does not replace a matching auction snapshot.
- Normalizer: validates schemas, currency, units, timestamps, realm identity, and item variants.
- SQLite repository: stores settings, source provenance, normalized snapshots, scan status, candidate references, and manual outcomes.
- Deal engine: pure calculation/filter/ranking functions independent of network access and UI.
- Local service and interface: exposes refresh, settings, results, shopping-list, export, and ledger actions on loopback only.

Cache raw downloaded files separately with provenance. Retain the latest two successful snapshots per source plus the compact scan summaries and immutable source references needed by saved outcomes. Replace data atomically so a failed refresh cannot erase the last successful snapshot. Do not commit credentials, raw caches, or personal ledger data to Git.

## Failure handling and credentials

Store secrets outside source control and keep them out of browser responses, exports, logs, and error messages. Use backend-only authentication. Handle expired tokens, authorization failures, rate limits, network errors, schema changes, malformed prices, and partial realm failures explicitly. Use bounded retries and honor server retry instructions.

Keep source scan time, HTTP snapshot metadata where provided, and local fetch time distinct. Fetch time must never masquerade as market freshness. Missing source age is unknown and prevents an actionable result by default. A partially successful scan identifies failed realms and uses cached data only within configured freshness limits.

## Outcomes and capital

The initial shared budget is entered manually; the program cannot inspect the Warband bank. Record purchases with quantity, total cost, chosen strategy allocation, and intended destination. Purchases reduce available capital and add inventory at cost. Actual net sales proceeds restore capital; canceled or expired listings leave the item in inventory. Support partial sales, explicit recorded fees, corrections, and manual balance adjustments without counting the same transaction twice. Rejected candidates do not change capital.

## Acceptance and validation

The first version is acceptable when it:
1. Parses an actual TSM regional file and preserves required valuation, demand, and source-time fields.
2. Authenticates to Blizzard and fetches at least one real connected-realm snapshot, then supports a selected multi-realm scan without duplicate connected-house requests.
3. Correctly matches supported items and rejects ambiguous variants.
4. Produces both explainable rankings using configurable costs, thresholds, freshness rules, and budget settings.
5. Compares user-selected destinations without presenting regional sales data as destination-specific evidence.
6. Enforces shopping-list totals and item exposure against the shared budget.
7. Records manual outcomes and exports visible results with timestamps and assumptions.
8. Survives partial source failures without silently presenting stale results as fresh.

Use meaningful tests for currency and quantity boundaries, variant mismatches, connected-realm deduplication, stale/missing timestamps, failed refresh preservation, duplicate opportunity budgeting, partial sales, and ledger corrections. Perform an actual-data smoke test and a browser workflow check. Final market usefulness requires the user to verify a sample in-game and track actual outcomes; automated tests cannot establish profitability.

## Out of scope

Automatic buying, posting, cancellation, game-client control, bank access, paid data subscriptions, hosted accounts, notifications, advanced price forecasting, automatic selection of the final four selling realms, and guarantees of profit or sale time are outside this version. Alerts, richer history, and personal sales-speed estimates can follow after measured use.
