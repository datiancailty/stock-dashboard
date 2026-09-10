# Part 0 / Part 1 unified watchlist

## Product contract

- Part 0 is a read-only monitor. Its management entry opens Part 1; there is no second whitelist editor and no `vps_submit_whitelist_revision` call in the browser.
- Part 1 is the only Dashboard watchlist editor. Search/add/remove/revert changes an in-memory draft. A scoped confirmation precedes one authenticated `personal_replace_watchlist` call.
- Save preserves metadata on unchanged records and verifies exact membership and content by authenticated readback. A transport-ambiguous write is not retried automatically: save is disabled until the user re-reads the cloud list.
- Existing owner-scoped RPC authorization is unchanged. No SQL migration is required. A fresh preflight rejects an already-observed concurrent edit; the existing replace RPC is **not** an atomic compare-and-swap across devices. Do not advertise guaranteed simultaneous-edit conflict prevention; re-read and coordinate simultaneous edits. A future transactional version contract requires separately reviewed Hosted SQL.
- The saved Part 1 list drives Parts 2/3 rows, Part 4 event filtering, Part 5 selectors/current news, and Part 6 current-universe conditional cards and trade-stock selector. Drafts never change those Parts. Retained account holdings, trades, feedback, research and historical records are independent of membership.
- Membership is not readiness: new securities show pending quotes/BOLL/dividend evidence until the normal deterministic worker publishes them. Missing/conflicting cash remains null, not zero; no news, calendar event or AI study is invented to fill a row.
- During this release VPS target submission and script integration are explicitly deferred. The UI shows saved Dashboard list, old desired revision and actual active receipt separately. Updating the Dashboard list does not submit a desired VPS revision, change the active list, start a timer, modify an ARM or trade.

## Monitoring evidence

- The real `vps_private_get_portfolio` contract is one `primary` object. The UI also accepts the older array envelope, but an initialized envelope without a positive projection sequence and valid source timestamp is not treated as an observed account or empty holdings.
- Process state and indicator success have no dedicated fields in the current runtime RPC. The page says `未接入`; a strategy-cycle timestamp must not be interpreted as a self-check or successful indicator calculation.
- A runtime receipt older than 30 minutes is labelled `回执较旧`, not a claim that the VPS has failed. Future-dated receipts are untrusted for health. A recent `health_status=ok` is labelled as a receipt report, not a promise of future execution or order capability.
- All displayed runtime timestamps use Beijing time. The browser re-reads private RPCs every 15 minutes only while visible. It never contacts the VPS, providers or trading APIs.
- Signed-out pages are empty of private records; a private Part opens login. Sign-out clears draft labels, search and confirmation dialogs.

## Verification

Install test-only `jsdom` and `playwright` outside the deployed source tree, or expose them through `NODE_PATH`. No browser cache, screenshots or provider responses belong in this repository.

```sh
node --check assets/app.js
node --test scripts/test_dashboard_refresh_ui.cjs scripts/test_unified_watchlist_ui.cjs
# DASHBOARD_TEST_OUTPUT must be an absolute directory outside this repository.
node scripts/test_unified_watchlist_browser.cjs
```

The browser harness starts a loopback-only temporary server, uses a disposable browser context with synthetic records, blocks external requests, checks the complete 0–6 navigation at desktop/tablet/mobile widths, asserts Part 1 buttons and Part 0 columns are not clipped, exercises save/undo/readback/sign-out, captures local screenshots and closes its browser/server. It is not Hosted, production login or VPS acceptance evidence.

## Release boundaries

This release updates the frontend and the local dividend-stage isolation adapter in a new immutable worker bundle. The original strict `public_forward_basis.py` validator is unchanged: a conflicting report body is still rejected, and no third-party-code exemption is enabled. Only that exact identity-conflict failure can quarantine one stock's entire forward basis to an explicit NULL marker. All disputed forward amounts, periods and source evidence are discarded. Publication is permitted only if the independently validated implemented-cash record for every quarantined stock is `ready`; other validation/coverage/provider failures still abort before either dividend writer. The UI explicitly labels the selected implemented-cash fallback. Original source evidence and failed attempts remain in protected local storage, not in source archives. Only the worker `current` symlink changes; the LaunchAgent cadence, wrapper configuration, VPS scripts and VPS control state remain unchanged.

Source/tag/Release, served Pages bytes, authenticated owner data readback, real browser acceptance and actual VPS operation require separate evidence. Public rollback must not roll back private data or resurrect public personal-history files. VPS follow-up must use the saved owner-scoped Part 1 universe and independently verify historical-data readiness before any desired/active whitelist transition.
