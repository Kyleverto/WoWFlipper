# Mount and decor identity correction — October 16, 2026

## Root cause and scope

A read-only inspection of the local runtime database found 641 recognized mount listings: 612 failed the old blanket variant rule and 29 failed discount requirements when evaluated at their snapshot fetch times. No decor items had cached classification. Representative decor auctions had context 13 and modifiers 28/39/40/42, so the refresh prefilter rejected their identity before downloading item metadata. Publication and the engine repeated the blanket rule, so even cached categories could not qualify.

Discovery now validates a bounded union of supported raw shapes for enabled ordinary categories, then checks financial plausibility separately from category-dependent identity. Final publication and eligibility check the untouched raw metadata against the discovered category. Identity, freshness, demand, budget, profit and per-house metadata limits otherwise remain in place. Pets retain exact species/level/quality/breed identity.

Mounts allow nonnegative context and modifier 28 (content tuning). Decor additionally allows modifiers 39 (crafting skill-line ability), 40 (crafting data), and 42 (crafting skill watermark). Other categories retain the strict simple-item policy. Crafting quality 38, nonempty bonus lists, unknown fields, duplicate fields/modifier types, and malformed/noncanonical values remain excluded. Source records and purchased quote metadata are never stripped.

## Source evidence and limits

Three authenticated reads of the official Blizzard item endpoint confirmed item IDs 242948 (Loch Modan Bearskin Rug), 245514 (Pandaren Wooden Table), and 262449 (Embossed Sin’dorei Fur Rug) as Housing class 20 / Decor subclass 0, non-equippable, level 1, Uncommon. These public item responses contained no crafting-quality field. The Uncommon rarity alone does not prove the absence of a crafted quality variant.

Across 59 cached listings in three houses, these items had context 13 and modifiers 28/39/40/42, with fixed crafting-data IDs per item and varying watermarks. No quality modifier 38 or bonus lists appeared. The interpretation of crafting provenance as non-identity metadata is a scoped application policy supported by the observed listings and source definitions, not an explicit Blizzard guarantee.

[TrinityCore’s item modifier definitions](https://github.com/TrinityCore/TrinityCore/blob/master/src/server/game/Entities/Item/ItemDefines.h) identify the modifier names. Its [item code](https://github.com/TrinityCore/TrinityCore/blob/master/src/server/game/Entities/Item/Item.cpp) uses content tuning for item-level calculations, reinforcing the need to keep gear excluded. [Blizzard’s generated housing API documentation, mirrored by Gethe](https://github.com/Gethe/wow-ui-source/blob/live/Interface/AddOns/Blizzard_APIDocumentationGenerated/HousingCatalogUIDocumentation.lua), accepts item ID for the corresponding housing catalog entry. Unknown or quality-bearing variants are not merged by this correction.

## Verification

The clean baseline passed 370 tests. New pipeline regressions first failed on the old code: cold metadata was never requested; warmed categories still produced zero opportunities. After the change, both paths produce eligible mount/decor rows, compare same-item competition across differing origin/crafting metadata, record purchases, and export valid backups with raw variants intact. Negative tests retain quality, bonus, unknown field and malformed-data exclusions.

No actual runtime database, inventory, ledger or credential file was changed. Users must restart the updated app and refresh sources to rebuild prior cached classifications and identity flags. Full verification and publication results are reported with the release.

Independent controller verification passed **455 tests in 11.25 seconds**; Git whitespace checks passed. The corrected wheel built successfully (SHA256 `fbf382e8bb813344eaf7fd8f1e6859ef6d606913264453e128f22aed77e6f2dd`). It was installed in a separate environment and checked from `/tmp`, with imports resolving to its site-packages. Installed mount and decor metadata cases qualified; adding crafting-quality modifier 38 still excluded them. No browser UI changed.
