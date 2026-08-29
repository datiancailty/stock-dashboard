#!/usr/bin/env python3
"""No-network checks for the username-auth Edge Function candidate."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
FUNCTIONS = ROOT / "functions"
FILES = {
    "shared": FUNCTIONS / "_shared" / "username-auth.ts",
    "login": FUNCTIONS / "username-login" / "index.ts",
    "recovery": FUNCTIONS / "username-recovery-request" / "index.ts",
    "stage3": ROOT / "migrations" / "20260829003000_dashboard_private_data_schema_stage3.sql",
    "rate_sql": ROOT / "migrations" / "20260829004000_dashboard_auth_rate_limit.sql",
    "auth_postflight": ROOT / "contracts" / "verify-auth-hosted-postflight.sql",
}
for path in FILES.values():
    if not path.is_file():
        raise SystemExit(f"missing file: {path}")

shared = FILES["shared"].read_text()
login = FILES["login"].read_text()
recovery = FILES["recovery"].read_text()
stage3 = FILES["stage3"].read_text()
rate_sql = FILES["rate_sql"].read_text()
auth_postflight = FILES["auth_postflight"].read_text()
all_text = "\n".join([shared, login, recovery, stage3, rate_sql])

# No actual credential/token material may appear in these source artifacts.
for pattern in [
    r"sb_(?:secret|service_role)_[A-Za-z0-9._-]{12,}",
    r"sk-[A-Za-z0-9]{20,}",
    r"eyJ[A-Za-z0-9_-]{30,}\.[A-Za-z0-9_-]{20,}",
    r"-----BEGIN [A-Z ]+PRIVATE KEY-----",
]:
    if re.search(pattern, all_text):
        raise SystemExit(f"credential-like literal matched: {pattern}")

required_shared = [
    'DASHBOARD_ALLOWED_ORIGIN',
    'DASHBOARD_RECOVERY_REDIRECT_URL',
    'DASHBOARD_RATE_LIMIT_SECRET',
    'SUPABASE_SERVICE_ROLE_KEY',
    'SUPABASE_ANON_KEY',
    'app_resolve_username',
    'auth/v1/admin/users/',
    'auth/v1/token?grant_type=password',
    'auth/v1/recover',
    'cache-control": "no-store"',
]
for needle in required_shared:
    if needle not in shared:
        raise SystemExit(f"shared helper missing: {needle}")

assert 'USERNAME_RE = /^[a-z0-9](?:[a-z0-9._-]{1,30}[a-z0-9])$/' in shared
assert 'normalize("NFKC")' in shared
assert 'access-control-allow-origin": origin' in shared
assert 'access-control-allow-origin": "*"' not in all_text
assert 'localStorage' not in all_text
assert 'console.' not in all_text

for needle in [
    'isEndpointPath(request, "username-login")',
    'parseLoginInput',
    'rateLimitAdmit',
    'rateLimitRecordFailure',
    'verifyPassword',
    'jsonResponse(INVALID_CREDENTIALS, 401, origin)',
    'jsonResponse(RATE_LIMITED, 429, origin)',
    'TEMPORARILY_UNAVAILABLE',
    'Deno.serve(handleUsernameLogin)',
]:
    if needle not in login:
        raise SystemExit(f"login contract missing: {needle}")
for needle in [
    'isEndpointPath(request, "username-recovery-request")',
    'parseRecoveryInput',
    'sendRecovery',
    'GENERIC_RECOVERY_MESSAGE',
    'jsonResponse(GENERIC_RECOVERY_MESSAGE, 200, origin)',
    'Deno.serve(handleUsernameRecovery)',
]:
    if needle not in recovery:
        raise SystemExit(f"recovery contract missing: {needle}")

assert 'STATUS: REVIEWED LOCAL CANDIDATE / MANUAL HOSTED SQL EXECUTION ONLY.' in stage3
assert 'create or replace function public.vps_private_get_portfolio()' in stage3
assert 'grant execute on function public.vps_private_get_portfolio() to authenticated;' in stage3
assert 'create or replace function public.vps_private_get_runtime_display()' in stage3
assert 'latest four bounded' in stage3
assert 'grant execute on function public.vps_private_get_runtime_display() to authenticated;' in stage3
assert 'grant execute on function public.vps_sync_replace_private_projection(text, text, jsonb)' in stage3
assert "'DRY_RUN'" in stage3
assert "'primary', 'stock-sim-v31f-15m'" in stage3
assert 'vps_private_scope_members' in stage3
assert 'auth.users' in stage3
assert not re.search(r"insert\s+into\s+public\.app_usernames", stage3, re.I)
assert not re.search(r"insert\s+into\s+public\.vps_private_scope_members", stage3, re.I)

for needle in [
    'create table if not exists public.app_auth_rate_limit_windows',
    "interval '15 minutes'",
    "failed_count between 0 and 5",
    'extensions.digest',
    'grant execute on function public.app_auth_rate_limit_admit(text, text)\n  to service_role;',
    'grant execute on function public.app_auth_rate_limit_record_failure(text, text)\n  to service_role;',
]:
    if needle not in rate_sql:
        raise SystemExit(f"rate-limit SQL contract missing: {needle}")
for pattern in [
    r'create\s+or\s+replace\s+function\s+public\.app_auth_rate_limit_admit\s*\(\s*p_ip_hash',
    r'create\s+or\s+replace\s+function\s+public\.app_auth_rate_limit_record_failure\s*\(\s*p_ip_hash',
]:
    if not re.search(pattern, rate_sql, re.I | re.S):
        raise SystemExit(f"rate-limit SQL contract missing: {pattern}")
assert 'grant execute on function public.app_auth_rate_limit_admit(text, text)\n  to anon' not in rate_sql
assert 'grant execute on function public.app_auth_rate_limit_record_failure(text, text)\n  to authenticated' not in rate_sql
assert "to_regprocedure('public.app_auth_rate_limit_admit(text,text)')" in auth_postflight
assert "to_regprocedure('public.app_auth_rate_limit_record_failure(text,text)')" in auth_postflight
assert 'rate_limit_table_has_no_direct_privileges' in auth_postflight

for path in FILES.values():
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if line.rstrip(" \t") != line:
            raise SystemExit(f"trailing whitespace: {path}:{number}")

print(f"AUTH_EDGE_STATIC_CONTRACT_OK files={len(FILES)}")
