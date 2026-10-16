# Automatic refresh and opportunity alerts — October 16, 2026

The server owns a single opt-in scheduler. Checks start immediately on enable, then wait 120 seconds after completion; manual refreshes use the same singleton worker. Selected source errors back off to at most 900 seconds. Automatic auction requests send the retained Last-Modified header and skip snapshot decoding, enrichment and publication when all sources and research configuration are unchanged. Changed runs advance one bounded toy discovery batch. Source freshness remains based on source timestamps.

A runtime first-seen index records eligible listing identities and update cursors separately from financial history. Initial observations, research-settings changes and restored datasets create quiet baselines. Current search/category matching is shared with opportunity queries; new-only filtering is shared with CSV export. Restores clear runtime tracking atomically. The web mutation gate prevents refresh starts and restores from interleaving alert-feed responses.

## Local verification

- Python 3.12, macOS: **511 tests passed in 11.11 seconds** using the full checkout suite.
- JavaScript syntax and Git whitespace checks passed.
- Regression coverage includes conditional 304 requests without auction decoding/publication, changed-house competition, valuation/configuration reassessment, scheduler timing/manual overlap/backoff/lifecycle, metadata-only source errors, quiet baselines and restore, disappearing/reappearing listings, in-flight refresh deferral, category/search matching, new-only CSV, browser permissions/audio failure handling, shared notification cursors, and stable new-only navigation.
- A real in-app browser used a temporary synthetic database and injected source completion, with no external API requests. Enabling automatic checks added two listings: the banner announced two new opportunities while the original three rows stayed visible. Show new displayed exactly two rows with a new_after CSV link; All opportunities displayed all five rows and removed that filter. Disabling automatic checks showed the off state. No notification permission was requested or sound played during this browser check; those paths use deterministic browser stubs in tests.

These checks do not establish current Blizzard availability, live latency, operating-system notification delivery, or in-game listing availability. Blizzard publishes snapshots rather than individual auction events. Keep the server running for checks and a browser tab open for alerts.
