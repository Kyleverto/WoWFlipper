# Refresh memory investigation — October 17, 2026

The reported Windows screenshot showed Python using approximately 20 GB. Source inspection and a reproducible synthetic profile confirmed two scaling issues: refresh retained all downloaded and previous auction-house snapshots until publication, while cached analysis retained every selected house and an in-memory dictionary for each rejected listing. Snapshot publication also deep-copied repeated nested item metadata.

## Changes

- Download workers stage full snapshots in private temporary files before completing. Three downloads remain concurrent; metadata planning and publication read individual houses. Original payload bytes, Last-Modified headers, timestamps and cross-house metadata discovery remain intact. Staging is removed when refresh ends.
- Buying-house analysis retains eligible candidates and snapshot headers. A second pass keeps all destination asks for relevant identities, preserving existing price/variant/tradeability comparison rules while dropping unrelated destination records. Sibling realms still share one destination snapshot and index.
- Inspection exclusions use a disk-paged temporary SQLite sequence with a 256-row write buffer. Cached results and old requests keep their own immutable sequence alive; releasing the last reference removes its scratch files. Pages, total counts and exclusion reasons are unchanged.
- Snapshot serialization visits records without recursively copying their metadata graph. Synthesized archive JSON is streamed, preserving the prior format and hash. Original raw archives and conditional reuse remain unchanged. This requires no database migration.

## Reproduction and measurements

Run `PYTHONPATH=. python scripts/profile_memory.py --houses 24 --rows 2000` from the checkout with Python 3.11 or later. The script uses temporary synthetic data, no credentials or network, and retains an existing query while refreshing to model an open browser. One percent of listings qualify. Measurements below use macOS arm64/Python 3.12 and tracemalloc; they are not Windows process-memory predictions or live-data ceilings.

| Houses × rows | Retained query before / after | Refresh Python allocation peak before / after | Process peak RSS before / after |
| --- | --- | --- | --- |
| 8 × 2,000 | 13.04 / 0.25 MiB | 30.63 / 7.09 MiB | 104.66 / 52.55 MiB |
| 24 × 2,000 | 39.24 / 0.65 MiB | 85.30 / 7.50 MiB | 250.20 / 54.67 MiB |

A separate 3,000-row fixture with shared nested metadata reduced publication peak allocations from 28,315,353 to 7,678,293 bytes. JSON parsing still materializes one full house, and eligible listings/relevant destination asks still consume memory. Temporary disk space and additional local reads replace region-wide retention.

## Verification

Full checkout suite: **524 passed in 11.67 seconds**. Regressions track both original downloads and materialized staged snapshots, raw-record release across buying houses, overlapping buying/selling selections, exact destination comparison equivalence, threaded exclusion paging, cache replacement cleanup, serialization memory, archive bytes/hashes, conditional reuse and publication failures. Independent refresh, query and repository reviews found no blocking correctness issues. Existing source freshness, lookup budgets, financial history, manual/automatic refresh singleton and new-opportunity filtering tests remain green.
