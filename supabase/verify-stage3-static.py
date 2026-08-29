#!/usr/bin/env python3
"""Static, no-network checks for the Stage 3 private-data design draft."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONTRACT_PATH = ROOT / "contracts" / "dashboard-private-data-schema-v1.template.json"
SQL_PATH = ROOT / "contracts" / "dashboard-private-data-schema-v1-stage3.sql"

contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
sql = SQL_PATH.read_text(encoding="utf-8")

assert contract["status"] == "design_only"
assert sql.startswith("-- Stock dashboard private identity/portfolio projection — Stage 3 design draft.")
assert "STATUS: DESIGN ONLY / DO NOT APPLY." in sql
assert "begin;" in sql and "commit;" in sql
assert sql.count("$$") % 2 == 0, "unbalanced dollar-quoted function bodies"

required_tables = {
    "app_usernames",
    "vps_private_scopes",
    "vps_private_scope_members",
    "vps_private_projection_state",
    "vps_private_sim_positions",
}
for table in required_tables:
    assert f"create table if not exists public.{table}" in sql, table
    assert f"alter table public.{table} enable row level security" in sql, table
    assert f"revoke all on table public.{table} from PUBLIC, anon, authenticated, service_role" in sql, table

for column in ("cost_basis", "market_value", "unrealized_pnl", "unrealized_pnl_pct"):
    assert f"{column} numeric" in sql, column
    assert f"{column} numeric" in sql and "generated always as" in sql

for marker in (
    "public.app_resolve_username(text)",
    "public.app_get_current_username()",
    "public.vps_private_can_view_scope(text)",
    "public.vps_private_get_portfolio()",
    "public.vps_sync_replace_private_projection(text, text, jsonb)",
    "public.vps_get_whitelist_control_state()",
    "public.vps_submit_whitelist_revision(text[], text, bigint)",
    "public.vps_submit_whitelist_revision(text[], text)",
):
    assert marker in sql, marker

assert "grant execute on function public.app_resolve_username(text) to service_role" in sql
assert "grant execute on function public.app_get_current_username() to authenticated" in sql
assert "grant execute on function public.vps_private_get_portfolio() to authenticated" in sql
assert "grant execute on function public.vps_sync_replace_private_projection(text, text, jsonb)\n  to service_role" in sql
assert "grant execute on function public.vps_submit_whitelist_revision(text[], text, bigint)\n  to authenticated" in sql
assert "revoke all on function public.vps_submit_whitelist_revision(text[], text)" in sql

for field in contract["projection_payload"]["derived_fields_rejected"]:
    # The writer's exact per-position unknown-key allow-list must not include
    # any database-computed amount.
    position_match = re.search(
        r"where key\.name not in \(\s*"
        r"'symbol', 'display_name', 'held_quantity', 'available_quantity',\s*"
        r"'average_cost_per_share', 'current_unadjusted_price', 'price_as_of',\s*"
        r"'data_status', 'quote_source_kind', 'position_state'\s*"
        r"\)\s*\)\s*then\s*raise exception 'Private position contains",
        sql,
        flags=re.S,
    )
    assert position_match, "private position allow-list not found"
    assert f"'{field}'" not in position_match.group(0), field

assert "delete from public.vps_private_sim_positions" in sql
assert "projection_sequence_value < existing_state.projection_sequence" in sql
assert "Private projection sequence conflicts with previous content" in sql
assert "p_expected_base_revision_no is distinct from current_base_revision_no" in sql
assert "status = 'superseded'" in sql
assert "p_symbols is null or cardinality(p_symbols) not between 1 and 50" in sql
assert "settings.expected_adapter_id <> 'v31f-15m-miaoxiang-sim-adapter'" in sql
assert "settings.default_mode" in sql
assert "'primary', 'stock-sim-v31f-15m', '模拟盘', true" in sql
assert "vps_private_scope_members" not in sql[sql.index("insert into public.vps_private_scopes"):]

# These are source-level data-boundary checks, not a SQL execution claim.
assert "cost_basis" in contract["private_projection"]["derived_fields_database_computed"]
assert contract["private_projection"]["direct_browser_table_dml"] is False
assert contract["whitelist"]["legacy_submit_rpc_execute"] is False
assert contract["whitelist"]["empty_list_allowed"] is False
assert contract["rls"]["anonymous_private_read"] is False

# Detect accidental public/browser secret-bearing grants in this draft.
for line in sql.splitlines():
    lowered = line.lower()
    if lowered.startswith("grant ") and "service_role" in lowered:
        assert "on table public.vps_private_" not in lowered

print("STAGE3_PRIVATE_SCHEMA_STATIC_CONTRACT_OK")
