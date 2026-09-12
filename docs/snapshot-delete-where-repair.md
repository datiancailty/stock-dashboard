# API-session snapshot DELETE compatibility

## Failure boundary

The gateway intentionally maps multiple RPC failures to `502 gateway_unavailable`.
A successful SQL Editor rollback probe does not prove that the API/PostgREST
session accepts the same call. Session-loaded safety checks may differ.

The legacy ingestion function contains full-replacement display snapshots. Its
unqualified `DELETE` statements can be rejected with SQLSTATE `21000`,
`DELETE requires a WHERE clause`, before the publish transaction commits.
Inspect the historical Postgres error context before proposing this repair; do
not diagnose it solely from a generic HTTP status or retry a durable publication
to obtain evidence that already exists in logs.

## Deliberately minimal change

`20260913000000_vps_snapshot_delete_where_fix.sql` is a forward-only,
user-manual migration. It modifies only the two snapshot DELETE statements in
`vps_sync_ingest_report_legacy(text,jsonb)`:

- `vps_symbol_states`;
- `vps_sim_positions`.

Each DELETE is explicitly qualified by the primary-key set of the existing
snapshot. **It still replaces the entire old display snapshot**, not only stale
or removed members. This preserves the prior contract, including an explicitly
empty replacement and clearing values omitted by the next snapshot. It is not
a per-device redesign, an incremental upsert, a retention change, or a change to
holdings authority. The report transaction and its duplicate-row rejection
remain unchanged.

The migration checks the five-function source chain, expected owners and
`SECURITY DEFINER`/scalar-JSONB signatures, both non-null text primary keys,
and the effective legacy/publish ACL boundary. It preserves all `pg_proc`
metadata other than the verified function body. Unexpected drift aborts the
transaction. Applying the same repair again is a no-op only when all guards
still match.

It does not disable `safeupdate`, change API settings or grants, deploy Edge
code, ingest any report, mark an ACK delivered, modify a snapshot row, change
control membership, or run a worker. Later report ingestion retains the
existing snapshot-replacement effect; do not mislabel that later publication
as a database-read-only operation.

## Local verification

Use the existing PGlite development dependency:

```sh
node scripts/test_vps_publish_receipt.cjs \
  --candidate supabase/migrations/20260912001000_vps_ack_ingest_conflict_and_legacy_acl_fix.sql \
  --followup supabase/migrations/20260913000000_vps_snapshot_delete_where_fix.sql \
  --require-qualified-snapshot-deletes
```

If PGlite is installed in a separate tooling directory, set
`PGLITE_REQUIRE_FROM` to its `package.json` path. Tests run with network denied.
The source-level WHERE assertion fails on the predecessor function. The full
PostgreSQL chain exercises service-role entry, exact ACK IDs, idempotence,
identity/conflict/expiry rejection, ACLs, repeated repair and source-drift
refusal. Snapshot regressions cover nonempty replacement, same-count membership
change, obsolete/omitted fields, duplicate symbol/position rejection, invalid
position rollback, and explicit empty replacement without deleting the active
control revision.

**PGlite does not run the native pg-safeupdate extension.** The WHERE assertion
checks the corresponding source precondition; it is not an end-to-end live
API verdict. The upstream reference is
https://github.com/eradman/pg-safeupdate/blob/master/safeupdate.c .

## Hosted gate and durable recovery

1. The owner manually executes the complete guarded migration in SQL Editor.
   Never automate that editor or partially replay the script after an error.
2. Check `snapshot_delete_body_matches=true`, unchanged function settings,
   closed direct legacy execution, and allowed service-role publish execution.
3. Reconcile the existing receipt before another transport attempt. Preserve
   its request ID and byte-exact body. A new nonce is transport identity, not
   permission to submit a new report or repeat control activation.
4. Capture fresh control/economic baselines and check expiry and source binding.
   Only then use the already-reviewed recovery path for the existing receipt.
5. Independently verify Hosted receipt/ACK acceptance and active revision,
   local delivery markers, unchanged economic records, and off-host backup.
   A source release, manual migration result, or VPS-local active cache is not
   proof of complete Hosted activation or of a worker cycle.

Source archives must exclude screenshots, runtime reports, databases, private
lists, log payloads, secrets, and credentials. This source-only repair does not
change frontend assets or scheduler activation.
