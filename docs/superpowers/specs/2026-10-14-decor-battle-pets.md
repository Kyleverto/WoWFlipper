# Decor and Battle pets coverage

User-approved release: add decor and battle pets to the existing opportunity, comparison, shopping and inventory workflows. Preserve recipes, mounts and toys. Gear and transmog remain excluded.

## Identity and valuation
Pet auctions retain their actual cage item ID and raw Blizzard variant fields. Distinguish species, level, quality and breed; never use the shared cage item ID as a pet's pricing, override or exposure identity. Valuation gains an optional pet species ID so existing snapshots and constructors stay readable. Species records use the separate tsm-pets:us source and its own timestamp/freshness/provenance.

The current public pet feed has one row per species. Display that limitation wherever resale assumptions are reviewed; do not invent level, quality or breed premiums. Destination asking-price comparisons match species, level, quality and breed exactly. Manual overrides are scoped to that exact variant. Quantity and exposure limits aggregate by species across variants, preserving limits without grouping unrelated pets together.

Ordinary item overrides and historical data remain compatible. A legacy item override for the cage must never affect a pet. Revalidation rejects changed pet identity even if the auction ID remains unchanged. Purchase history retains the variant and estimate recorded at purchase.

## Sources
Use Blizzard Housing/Decor item classification and explicit decor-to-item links, never a name heuristic or an assumption that decor IDs equal item IDs. Cache incremental decor discovery with visible pending progress. Retain known metadata on upstream failure. Battle pet metadata is cached by species, and unresolved/untradable pets remain excluded with inspectable reasons.

Refresh sources independently so a failed pet feed or metadata discovery cannot suppress ordinary categories. Retain existing request bounds, shared per-species/item deduplication and safe diagnostic messages. Prices and demand require known fresh source timestamps.

## User interface
Add Decor and Battle pets settings and opportunity category filtering, reflected in visible CSV export. Respect existing saved category choices. Show pet level, quality and breed throughout Opportunities, Compare, Shopping and Inventory. Support exact-variant override save/edit/clear, including management in Settings, with existing stale-response and draft protections. Preserve compact controls and grouped buying-house shopping.

## Verified source contracts
Live Blizzard decor index uses decor_items; decor details link the physical item through an items object. Example: decor533 links item245392, whose item_class is20 (Housing) and item_subclass0 (Decor). Public pet CSV headers are petSpeciesId,name,marketValue,historical,avgSalePrice,saleRate,soldPerDay,updatedAt. These checks used read-only public data; no credentials are stored in the repository.

## Verification
Exercise legacy snapshot loading; item/species/cage collisions; malformed pets and CSV; independent freshness and cache expiry; bounded refresh/failure isolation; exact comparisons; scoped overrides and invalidation; species exposure; shopping/purchase identity changes; all UI views, filters and retries. Run the full existing suite and synthetic browser walkthrough. Use a temporary data directory for QA; do not modify real purchases or source snapshots.
