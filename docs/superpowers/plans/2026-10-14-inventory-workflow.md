# Inventory workflow

Approved in chat on October 14, 2026. Extend the existing purchased-lot workflow with manual inventory management; preserve separate purchase lots and append-only financial history.

## Backend task
- Add inventory-update ledger events with purchase_id and status and/or destination. Validate status and configured destinations for new writes, require a held lot, and preserve request idempotency.
- Replay current status and destination without changing purchase history or cash. Existing purchases default to held. Expiration/cancellation with a lot returns it to ready; partial sales preserve status and full sales remove held inventory.
- Verify old data, financial invariance, retry safety, malformed/unknown/sold lots, partial sales, and transactional corrections.

## Frontend task
- Add Inventory beside Shopping and Ledger. Search by name/ID; filter by destination and manual status.
- Show purchase lot ID, quantity, purchase cost, remaining basis, buying realm/house, current destination, date and days held.
- Place status, destination, sale, expiration and fee actions at the lot. Net sale proceeds are entered after auction cut. Preserve per-lot input drafts and focus across refreshes and enforce safe retries after unknown mutation results.
- Keep financial history, outcome forms and correction tools in Ledger, including corrections of inventory updates.
- Verify actual handlers, filter behavior, request payloads, failed/retried mutations and polling draft preservation. Retain existing race tests.

## Integration
- Review backend and UI as one financial workflow. Run the complete Python/Node behavior suite, syntax and diff checks.
- Test synthetic lots in the browser: status/destination, partial sale, expiration, full sale, empty state and group cleanup. Never change real runtime data.
- Commit and push the reviewed implementation to the authorized GitHub repository, sync the primary checkout and archive the worktree.
