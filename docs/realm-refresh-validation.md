# Internal realm exclusion — 2026-10-13

The broad realm index included internal Account, INST, Auxiliary and Partner entries labeled United States. Category-only bulk selection therefore included realms without published connected auction houses.

Read-only validation against the configured live app found 72 successful and 98 failed auction-house fetches. The authenticated official connected-realm index contained 83 houses: all 72 successful houses were present and all 98 failed houses were absent. The corrected adapter was replayed against this recorded index and the saved realm catalog: 226 selectable United States realms, 72 distinct houses, and zero of the 98 failing internal houses selectable.

Eligibility now requires United States category plus connected-realm-index membership. Existing unsupported selections remain visible/removable and are skipped explicitly during refresh. Empty/malformed index data or failed discovery does not overwrite previously verified cache data. Offline status is not used as an exclusion, so maintenance does not itself change selection eligibility.

Source diagnostics expose allowlisted error codes and validated HTTP status numbers, never exception messages, credentials, headers or upstream response bodies. Live validation used only Blizzard's connected-realm index; it did not rerun the user's full refresh or modify their settings or ledger.
