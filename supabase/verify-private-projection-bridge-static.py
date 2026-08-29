#!/usr/bin/env python3
"""Static checks for the forward-only Stage-3 v2 private projection bridge."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SQL_PATH = ROOT / "migrations" / "20260829005000_vps_sync_private_projection_bridge.sql"
POSTFLIGHT_PATH = ROOT / "contracts" / "verify-private-projection-bridge-hosted-postflight.sql"
sql = SQL_PATH.read_text(encoding="utf-8")
postflight = POSTFLIGHT_PATH.read_text(encoding="utf-8")

assert sql.startswith("-- Stage 3 private projection bridge")
assert "STATUS: REVIEWED LOCAL CANDIDATE / MANUAL HOSTED SQL EXECUTION ONLY." in sql
assert sql.count("begin;") == 1 and sql.count("commit;") == 1
assert sql.count("$$") % 2 == 0
assert "alter function public.vps_sync_ingest_report(text,jsonb) rename to vps_sync_ingest_report_v2_base" in sql
assert "public.vps_sync_ingest_report_v2_base(p_device_id, p_report)" in sql
assert "public.vps_sync_replace_private_projection(" in sql
assert "'primary'" in sql
assert "if p_report ? 'private_projection'" in sql
assert "jsonb_typeof(p_report -> 'private_projection') <> 'object'" in sql
assert "revoke all on function public.vps_sync_ingest_report(text, jsonb)" in sql
assert "revoke all on function public.vps_sync_ingest_report_v2_base(text, jsonb)" in sql
assert "service_role" in sql
assert not re.search(r"(?:service[_ -]?role|hmac|provider[_ -]?api|password).{0,80}[=:][^\n]{8,}", sql, re.I)

assert postflight.startswith("-- Private projection bridge Hosted postflight")
assert "to_regprocedure('public.vps_sync_ingest_report(text,jsonb)')" in postflight
assert "to_regprocedure('public.vps_sync_ingest_report_v2_base(text,jsonb)')" in postflight
assert "vps_sync_replace_private_projection(text,text,jsonb)" in postflight
assert "has_function_privilege('service_role'" in postflight
assert "pg_get_functiondef" in postflight

print("PRIVATE_PROJECTION_BRIDGE_STATIC_CONTRACT_OK")
