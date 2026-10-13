# Validation evidence

Checked October 12–13, 2026 on macOS arm64 with Python 3.12. Final local tests pass: **114 passed** (`PYTHONPATH=. .venv/bin/python -m pytest -q`). Tests cover source schemas/retry behavior, immutable provenance/storage, exact identity, freshness and valuation math, budget/ledger replay, web validation/security, visible/export query behavior and smoke error sanitization. New targeted regressions cover stale Valuation timestamps, successive sale corrections and restoration after void. Full configured-house smoke is tested with injected sources, not claimed as live Blizzard evidence.

## Actual public source

`.venv/bin/python scripts/smoke_live.py --tsm-only` used the real `TSMClient.region()` HTTP download and parser against the public retail US CSV. Result: **29,756 accepted rows, 250 rejected rows**, scan source time **2026-10-12 08:26:39+00:00**, exit 0. Invalid rows are omitted, not fabricated or repaired. Live accepted rows can change between downloads. This validates public download/schema parsing; it does not validate item variants, destination demand or profit.

## Installed package and local server

Built a wheel with `.venv/bin/python -m pip wheel --no-deps . --wheel-dir /tmp/wowflipper-task6-wheels`. Installed it into a new venv created with the supported project Python: `.venv/bin/python -m venv /tmp/wowflipper-task6-installed312`, followed by `/tmp/wowflipper-task6-installed312/bin/python -m pip install /tmp/wowflipper-task6-wheels/*.whl`.

All import/CLI/resource checks ran with working directory `/tmp`; module path resolved to `/tmp/wowflipper-task6-installed312/lib/python3.12/site-packages/wowflipper/__init__.py`, proving checkout imports were not involved. `wowflipper --help` exited 0. Installed Flask test-client requests to `/`, `/static/app.js`, `/static/style.css` returned 200 with 6,183 / 30,117 / 5,213 bytes. Started installed CLI with `--demo --data-dir /tmp/wowflipper-task6-start --port 8876`; it bound to 127.0.0.1 with debug off. Independent HTTP requests returned the same 200 responses and lengths; then the server was stopped. The initial attempt to create a venv using macOS system `python3` selected Python 3.9.6 and installation correctly refused the Python >=3.11 requirement. Recreated the fresh environment with Python 3.12 and all installation checks passed.

Task 5 records extensive real-browser demo checks for search/ranking/filter/export, destination selection, persistence, outcomes/corrections and deliberately reordered requests. Those browser checks are prior evidence; Task 6 did not repeat the full browser workflow.

## Remaining acceptance checks

Blizzard credentials are absent in the local process. Full live smoke returns exit **2** with a sanitized missing-credential message before any network call. **Live OAuth, current realm discovery, authenticated auction payload and Last-Modified behavior, item/toy enrichment, and full live pipeline remain unverified**. Fixture and mocked tests do not establish those live properties. After setting both environment variables in the same shell, run:

```sh
python scripts/smoke_live.py --data-dir ~/.local/share/wowflipper
```

Use your actual configured live data directory. The command reads settings in SQLite read-only mode, downloads real sources, refreshes selected houses and runs the shared query in disposable temporary storage. Exit 0 may have zero qualifying rows; pending toy membership or bounded enrichment can leave candidates excluded. Inspect reported source states/times as well as row counts. Source schemas, availability and API limits may change. Manual in-game checks of auctions, variants, transfer rules, tradeability and actual resale outcomes are still required.

CI is configured for Python 3.11 and 3.14, with read-only repository permissions, tests and isolated wheel/resource checks. These hosted runs have not yet executed; local evidence is Python 3.12 only. Linux/Windows setup instructions are provided, but neither OS was run locally. The whole-branch review and scoped fix review are complete. GitHub publication and hosted CI results are reported separately at delivery.

## Final review fixes

Final source-checkout verification: `PYTHONPATH=. .venv/bin/python -m pytest -q` → **114 passed in 0.95s**. The explicit checkout path matters because this venv also contains the earlier installed wheel. Ten new regression cases cover refresh metadata/competition with both house orders and cached/new metadata, resale-factor eligibility and exact cutoff, unique-item cache reads and the 100-request enrichment bound, indexed/scan comparison equivalence with a structural prohibition on repeated full scans, page-only comparison work, result/exclusion response bounds, matching page exports and explicit assumptions/destination provenance. Existing classification-only fixtures now provide fresh auction evidence and zero minimum return thresholds so they remain plausible under the shared engine prefilter. No timing thresholds were introduced.

Isolated Playwright with installed Chrome used a fresh synthetic repository at `/tmp/wowflipper-final-fixes-browser` and source server `http://127.0.0.1:8877`. Actual browser checks passed: 205 opportunities render pages of **100 / 100 / 5**, totals and previous/next controls match, CSV pages 2/3 contain **100 / 5** listings, search resets to page 1, and 251 exclusions make **zero inspection requests while collapsed**, then render **100 / 100 / 51** on lazy pages and clear when closed. Two sibling slugs both show connected house 2 and their shared membership. **Zero browser runtime errors**. JavaScript syntax check and `git diff --check` passed. Prior unrelated financial/browser workflows were not repeated. Existing asynchronous accepted-query protection, dirty correction drafts and idempotent mutation recovery remain in place.

The final fixes preserve source/fetch times, snapshot hashes and offline modes and include those fields with destination competition/risks in CSV. No live credentials or authenticated Blizzard checks were used. The final scoped review cleared all four findings. The controller independently reran all 114 tests (0.98s), JavaScript syntax validation, and the Git whitespace check successfully. The final wheel was rebuilt (SHA256 `5f60920a946402a34821aa760449bf1517a382a36daf382316e04108573f7edb`) and installed in `/tmp/wowflipper-final-installed`. From `/tmp`, imports resolved to site-packages, CLI help succeeded, and installed UI/static/state/results/exclusions/CSV endpoints all returned HTTP 200. GitHub publication and hosted CI status are reported at delivery.
