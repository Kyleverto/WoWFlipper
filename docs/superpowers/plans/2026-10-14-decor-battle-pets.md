# Decor and Battle pets implementation plan

Goal: Extend the approved existing market workflow to decor and battle pets with explicit pricing limitations.
Spec: ../specs/2026-10-14-decor-battle-pets.md
Architecture: Backward-compatible optional pet species valuations, canonical market identities, independent pet source snapshots, and exact-variant comparisons/overrides. Existing listing and ledger identifiers remain intact.

1. Core: add shared identity helpers and optional valuation species field; extend repository serialization, cache revision and scoped overrides; engine eligibility/comparisons; query freshness/provenance/category filtering; portfolio species exposure and revalidation; settings and guarded API/CSV. Add failing tests for collisions, variants, stale data, legacy compatibility and financial invariants before implementation.
2. Sources: add strict public pet CSV parsing and independent refresh; discover decor with verified item links/classification; cache bounded pet/decor metadata; preserve known data on failure. Add injectable fixture tests covering live schemas, invalid rows, batching and failure isolation.
3. Frontend: show category filters and pet details in all existing views; label aggregate species estimates; scope override editors and management to exact variants. Preserve ordinary handlers, epochs, drafts and compact controls. Add actual-handler regressions for scoped payloads, filters/export and archived pet labels.
4. Integration: run all tests, review the complete diff and fix actionable issues. Validate actual public read-only source parsing, then browser-test synthetic decor and multiple pet variants through selection, override, purchase and inventory. Commit/push the authorized release, sync the primary checkout and archive the isolated worktree.

Ownership: core worker owns domain/repository/engine/query/portfolio/settings/web; source worker owns source adapters/refresh/demo; frontend worker owns static/template/README and frontend tests. Shared contracts are coordinated by the root agent. No worker changes real runtime data or commits credentials.
