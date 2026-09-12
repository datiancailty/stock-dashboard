# Expected-base whitelist RPC forward repair

## Scope

The three-argument `vps_submit_whitelist_revision(text[], text, bigint)` has `RETURNS TABLE` output variables named `status` and `revision_no`. Unqualified table-column references can therefore raise PostgreSQL `42702` before a desired revision is created.

The forward migration `20260912000000_vps_submit_expected_base_ambiguity_fix.sql` qualifies four query blocks only: pending-base read, active-base fallback, global revision allocation, and same-device supersession. It does not alter the two-argument historical overload, roles/grants, tables, rows, default `DRY_RUN` mode, snapshot binding, expected-base check, expiry, immutable contract text, or member normalization.

## Apply boundary

1. Operator first verifies the live three-argument function body and permissions through a read-only catalogue query.
2. Operator manually executes the complete transaction-wrapped migration in Supabase SQL Editor. Do not automate the editor.
3. Original body MD5: `17ff3d784f7695565f678fb22c7945fc`. Repaired body MD5: `5a5e440ab70ab2a46e9d1aa0cff0f457`.
4. An unknown body, changed argument names, unexpected search path/security-definer state, or privilege drift aborts before replacement. Reapplying the exact repaired body is permitted.
5. The migration calls no whitelist writer/pull RPC and changes no whitelist row. A later authorized submission must read the current Part 1 list and current expected-base revision immediately before writing, then reconcile the exact desired membership after the response.
6. A submitted desired revision is not VPS staging, activation, provider readiness, or trading permission. Preserve these separate proof gates.

## Reproducible local tests

The regression uses real local PostgreSQL through `@electric-sql/pglite@0.5.8`, including its real `pgcrypto` extension. Auth users and stock symbols are synthetic. It has no Hosted, provider, broker, SSH, or browser access.

With PGlite available to Node module resolution:

```sh
# Expected RED: SQLSTATE 42702 for ambiguous status.
node scripts/test_vps_submit_revision.cjs

# GREEN: the same path against the candidate, plus contract safeguards.
node scripts/test_vps_submit_revision.cjs --candidate supabase/migrations/20260912000000_vps_submit_expected_base_ambiguity_fix.sql
```

For an external isolated test dependency directory, `PGLITE_REQUIRE_FROM` may name its package.json. No production dependency or worker configuration is changed.

The test verifies pending and active-only bases, stale-base rejection, count and symbol validation, exact wire/member hashes, preservation of historical items, other-device isolation, unchanged function ACL/security settings, denied unauthorized callers, repeat-apply row preservation, and unknown-body refusal. Local tests do not prove Hosted application or real VPS activation.
