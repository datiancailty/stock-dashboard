#!/usr/bin/env python3
"""Local-only static regression checks for the unexecuted Stage-2 v2 migration.

This script deliberately does not connect to Supabase/Postgres and proves only
source-contract markers. Hosted migration/RPC/RLS/ACL behavior remains a
separate, explicitly approved verification gate.
"""
from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
MIGRATION = ROOT / "migrations" / "20260825003000_vps_sync_gateway_protocol_v2.sql"


def main() -> int:
    text = MIGRATION.read_text(encoding="utf-8")
    checks = {
        "transaction_wrapped": "begin;" in text and text.rstrip().endswith("commit;"),
        "dollar_quotes_even": text.count("$$") % 2 == 0,
        "no_gateway_secret_literal": "VPS_SYNC_SHARED_SECRET" not in text,
        "no_legacy_empty_whitelist_mode": "SUSPENDED_EMPTY" not in text,
        "pull_enforces_1_to_50_and_declared_count": (
            "selected_count not between 1 and 50" in text
            and "selected_count <> chosen.desired_symbol_count" in text
        ),
        "pull_recomputes_membership_hash": "recomputed_members_sha := public.vps_members_sha256(selected_symbol_array)" in text,
        "v2_revision_and_item_immutability": (
            "vps_v2_revision_immutability" in text
            and "vps_v2_revision_item_consistency" in text
        ),
        "ack_receipt_conflict_is_checked_after_lock": "stored_ack_body_sha is distinct from ack_body_sha" in text,
        "duplicate_ack_skips_legacy_reingest": (
            "if ack_receipt_rows = 1 then" in text
            and "legacy_acks := legacy_acks || jsonb_build_array(item)" in text
        ),
        "same_revision_generation_rollback_rejected": "attempts same-revision generation or pack rollback" in text,
        "expired_control_ack_rejected": "references an expired control revision" in text,
        "legacy_ingest_service_role_revoked": (
            "revoke all on function public.vps_sync_ingest_report_v1(text, jsonb) from public, service_role;" in text
        ),
        "delete_trigger_does_not_read_unassigned_new_record": "coalesce(new.revision_id, old.revision_id)" not in text,
        "ack_insert_uses_valid_diagnostics_form": "get diagnostics ack_receipt_rows = row_count;" in text,
    }
    failed = [name for name, passed in checks.items() if not passed]
    for name, passed in checks.items():
        print(f"{name}={'OK' if passed else 'FAIL'}")
    if failed:
        print("V2_SQL_STATIC_CONTRACT_CHECK_FAILED=" + ",".join(failed), file=sys.stderr)
        return 1
    print("V2_SQL_STATIC_CONTRACT_CHECK_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
