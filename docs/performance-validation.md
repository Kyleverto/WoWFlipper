# Results performance and US realm selection — 2026-10-13

A copied local dataset contained 39 connected auction houses and 1,408,477 listings. Tests did not modify the live market database, settings, credentials, or ledger.

| Request | Before | After |
| --- | ---: | ---: |
| Source/status page data | 5.325 s | 0.004 s |
| First opportunity calculation | 28.257 s | 18.843 s |
| Repeated opportunity calculation | 32.689 s | 0.001 s |
| Exclusion page, after calculation | — | 0.001 s |
| Different result sort, after calculation | — | 0.001 s |

These are local measurements, not latency guarantees. The post-change calculation used a fixed evaluation time of 2026-10-13 06:15 UTC so the saved source data remained fresh for equivalence checking. Both versions found 38 eligible listings and 1,408,439 exclusions. The full opportunity-page JSON was byte-for-byte equal after canonical serialization at the same fixed evaluation time. The first calculation after a data/settings change still scans the selected snapshots; the browser now explicitly displays loading rather than an empty table.

The analysis cache is process-local and single-entry per repository. Settings, realm mapping, snapshot revisions, and source freshness changes invalidate it. Concurrent requests share the build lock. The status endpoint reads compact provenance metadata instead of materializing all auction records.

Realm membership comes from Blizzard's realm-detail `category` field. Only exact `United States` records are offered as new choices. Legacy selections remain visible and removable. Search only hides rows; it does not clear selections. Bulk buying controls do not change selling selections.
