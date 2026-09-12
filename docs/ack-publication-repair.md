# ACK publication repair — source and Hosted are separate gates

## Failure modes reproduced in local PostgreSQL

The full protocol-v2 publish wrapper chain is exercised, rather than testing only a browser target-submission RPC or a TypeScript validator:

1. `ack_id` is both a PL/pgSQL variable and an `ON CONFLICT` target column: `42702`.
2. The legacy implementation cannot resolve `digest` when pgcrypto is installed in `extensions` but its search path omits that schema: `42883`.
3. Legacy ACK and event conflict targets do not include the predicates needed to infer their existing partial unique indexes: `42P10`.
4. Direct execution of the legacy implementation must be closed to PUBLIC, anon, authenticated and service_role. The gateway's service-role authority remains limited to the signed request entry; nested SECURITY DEFINER owner calls remain functional.

These are source-level reproduction results. Production function identities and permissions must be inspected before executing the forward migration. The migration guards five implementation fingerprints, the exact receipt primary key, both partial indexes and the pgcrypto namespace. An unexpected definition or structural drift aborts the transaction.

## Minimal source changes

- One v2-base conflict target uses the verified existing primary-key constraint name.
- Two legacy conflict targets add their existing `IS NOT NULL` index predicates.
- Only the legacy/core search paths and execution ACLs are normalized.
- Mode, immutable revision/ACK checks, report projections, owner/security-definer properties and source/expiry semantics are retained.
- The migration defines functions and narrows permissions; it does not submit targets, synthesize ACKs, ingest a report, edit portfolio rows, or enable any worker/timer.

## Local verification

```sh
PGLITE_REQUIRE_FROM=/ABS/PGLITE-INSTALL/package.json node scripts/test_vps_publish_receipt.cjs \
  --candidate supabase/migrations/20260912001000_vps_ack_ingest_conflict_and_legacy_acl_fix.sql
```

The dependency must already be installed in a trusted local test environment. The suite uses synthetic identities and data only. It tests pgcrypto in both `extensions` and `public`, 28-symbol control reports, accepted ACK IDs, same-request retry, conflicting request/ACK reuse, immutable evidence, expiry, DRY_RUN-only behavior, null strategy/quote/account observations, exact symbol status, event idempotency, role denials, migration idempotence and unexpected-source fail-closed behavior.

The original source produces the documented PostgreSQL errors; the guarded candidate passes 88 checks. Diagnostic flags change only an isolated in-memory test database to expose the next failure; they are not production deployment procedures.

## Manual Hosted and recovery sequence

1. Obtain exact Hosted function fingerprints and aggregate receipt/ACL state with a user-executed read-only query.
2. Complete independent source review and preserve all failed local probes.
3. The user manually runs the forward migration in Supabase SQL Editor and returns `ack_repair_postflight`.
4. Refresh the owner-scoped economic/runtime baseline, verify the original durable operation/report/body hash, active package, stopped writers and unexpired data. A source-level repair does not prove an ACK was accepted.
5. If signed pull/local activation already committed but publication was rejected, resume the existing durable report ID/body only. Do not submit a new revision, replay strategy work, clear pending state, mark delivery manually or re-quarantine new unexpired ACKs.
6. Confirm Hosted accepted ACK IDs and the exact desired/active membership separately from normal entrypoint cutover, source archive/Release and authenticated UI acceptance.

No real account data, reports, configs, keys, screenshots, logs or runtime database backups belong in this source release.
