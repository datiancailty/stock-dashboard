#!/usr/bin/env python3
"""Daily local-only Part 1–4 private-data refresh with fail-closed gates.

This worker performs one weekday Asia/Shanghai refresh after 18:05: a rolling
35-day official Part 4 announcement scan and an independent private quote
refresh. Future-dividend snapshots require this run's announcement success.
It does not access GitHub write paths, Codex, VPS, orders, accounts, or strategy
parameters. A failed stage prevents the daily success state from advancing;
independent successful stages may still publish their own complete snapshots.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
BEIJING = ZoneInfo("Asia/Shanghai")
LAUNCH_LABEL = "com.datiancailty.stock-dashboard.part4-daily"
LAUNCH_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_LABEL}.plist"
RUNTIME_DIR = Path.home() / ".hermes" / "workspace" / "stock-dashboard-private-runtime"
LOG_DIR = RUNTIME_DIR / "part4-daily-logs"
STATE_PATH = RUNTIME_DIR / "part4-daily-state.json"
DUE_HOUR = 18
DUE_MINUTE = 5


class DailySyncError(RuntimeError):
    """A sanitized category suitable for private runtime logs."""


def ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def atomic_write(path: Path, content: str, mode: int = 0o600) -> None:
    ensure_private_dir(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.chmod(tmp, mode)
    tmp.replace(path)
    os.chmod(path, mode)


def load_state() -> dict[str, Any]:
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        return {}


def read_mx_credential() -> str:
    """Read only a current-user-owned, private, non-symlink credential.

    Open relative to a verified directory fd, then validate the file fd before
    reading: pathname replacement must not bypass permissions or symlink checks.
    """
    path = Path.home() / ".hermes/workspace/stock-dashboard-private-runtime/credentials/mx-apikey"
    try:
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(directory)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise DailySyncError("daily_mx_credential_invalid")
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(fd, "r", encoding="utf-8") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                    raise DailySyncError("daily_mx_credential_invalid")
                value = stream.read().strip()
                if not value or "\x00" in value:
                    raise DailySyncError("daily_mx_credential_invalid")
                return value
        finally:
            os.close(directory)
    except (OSError, UnicodeError):
        raise DailySyncError("daily_mx_credential_invalid") from None


def run_checked(args: list[str]) -> dict[str, Any]:
    env = os.environ.copy()
    needs_mx = Path(args[0]).name == "personal_future_dividend_grid_sync.py" or "--include-structured-pre-disclosures" in args
    if needs_mx and not env.get("MX_APIKEY"):
        env["MX_APIKEY"] = read_mx_credential()
    completed = subprocess.run(
        [sys.executable, *args], cwd=ROOT, capture_output=True, text=True, timeout=600, check=False, env=env
    )
    # Child workers emit only their documented sanitized JSON summaries. Never
    # forward raw stdout/stderr into state or output.
    try:
        payload = json.loads(completed.stdout.strip())
    except (json.JSONDecodeError, TypeError):
        raise DailySyncError("daily_child_response_invalid") from None
    if not isinstance(payload, dict):
        raise DailySyncError("daily_child_response_invalid")
    if completed.returncode != 0 or payload.get("status") != "ok":
        category = payload.get("category")
        secret = env.get("MX_APIKEY")
        if not isinstance(category, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,119}", category) or (secret and secret in category):
            category = "daily_child_failed"
        raise DailySyncError(category)
    if payload.get("coverageComplete") is not True or payload.get("published") is not True:
        raise DailySyncError("daily_child_coverage_or_publish_incomplete")
    return payload


def sync_once(automatic: bool) -> int:
    now = datetime.now(BEIJING)
    day = now.date().isoformat()
    state = load_state()
    if automatic:
        if now.weekday() >= 5:
            print(json.dumps({"status": "skipped", "category": "non_weekday", "date": day}, ensure_ascii=False))
            return 0
        if (now.hour, now.minute) < (DUE_HOUR, DUE_MINUTE):
            print(json.dumps({"status": "skipped", "category": "not_due", "date": day}, ensure_ascii=False))
            return 0
        if state.get("successfulDate") == day:
            print(json.dumps({"status": "skipped", "category": "already_succeeded", "date": day}, ensure_ascii=False))
            return 0
    start = (now.date() - timedelta(days=34)).isoformat()
    end = day
    stages: dict[str, dict[str, Any]] = {}
    payloads: dict[str, dict[str, Any]] = {}
    commands = {
        "notices": [
            "scripts/part4_official_announcement_sync.py", "sync", "--from", start, "--to", end,
        ],
        "quotes": ["scripts/personal_market_snapshot_sync.py", "--publish"],
        "future": ["scripts/personal_future_dividend_grid_sync.py"],
    }
    for name, command in commands.items():
        if name == "future" and stages["notices"]["status"] != "ok":
            stages[name] = {"status": "skipped", "category": "notice_dependency_failed_preserving"}
            continue
        try:
            payloads[name] = run_checked(command)
        except subprocess.TimeoutExpired:
            stages[name] = {"status": "error", "category": "daily_child_timeout"}
        except DailySyncError as error:
            stages[name] = {"status": "error", "category": str(error)}
        except OSError:
            stages[name] = {"status": "error", "category": "daily_child_execution_failed"}
        except Exception:
            # Isolate an unexpected stage fault, but never count it as success
            # or print exception text (which may contain private child output).
            stages[name] = {"status": "error", "category": "daily_child_unexpected_error"}
        else:
            stages[name] = {"status": "ok", "category": "complete"}

    complete = all(stage["status"] == "ok" for stage in stages.values())
    category = "complete" if complete else "daily_partial_failure"
    if complete:
        try:
            atomic_write(STATE_PATH, json.dumps({"successfulDate": day, "completedAt": now.isoformat(timespec="seconds")}, ensure_ascii=False) + "\n")
        except OSError:
            category = "daily_state_write_failed"
    succeeded = category == "complete"
    notices, quotes, future = (payloads.get(name, {}) for name in commands)
    print(json.dumps({
        "status": "ok" if succeeded else "error", "category": category,
        "date": day, "windowStart": start, "windowEnd": end, "stages": stages,
        "quoteSucceeded": stages["quotes"]["status"] == "ok",
        "noticeWatchlistCount": notices.get("watchlistCount"), "noticeStored": notices.get("privateWrite", {}).get("stored"),
        "quoteStored": quotes.get("stored"), "futureGridStored": future.get("stored"),
        "coverageComplete": complete, "published": complete, "private_payload_not_emitted": True,
    }, ensure_ascii=False))
    return 0 if succeeded else 2


def plist_text() -> str:
    # launchd uses the host timezone. Derive the host-local instant corresponding
    # to 18:05 Asia/Shanghai, while the script itself enforces the China-time gate.
    now_bj = datetime.now(BEIJING)
    target_bj = now_bj.replace(hour=DUE_HOUR, minute=DUE_MINUTE, second=0, microsecond=0)
    host_target = target_bj.astimezone()
    intervals = "".join(
        f"<dict><key>Weekday</key><integer>{weekday}</integer><key>Hour</key><integer>{host_target.hour}</integer><key>Minute</key><integer>{host_target.minute}</integer></dict>"
        for weekday in range(1, 8)
    )
    args = [sys.executable, str(Path(__file__).resolve()), "run", "--automatic"]
    args_xml = "".join(f"<string>{xml_escape(value)}</string>" for value in args)
    ensure_private_dir(LOG_DIR)
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>{LAUNCH_LABEL}</string>
<key>ProgramArguments</key><array>{args_xml}</array>
<key>WorkingDirectory</key><string>{xml_escape(str(ROOT))}</string>
<key>RunAtLoad</key><true/>
<key>StartCalendarInterval</key><array>{intervals}</array>
<key>ThrottleInterval</key><integer>300</integer>
<key>StandardOutPath</key><string>{xml_escape(str(LOG_DIR / 'status.log'))}</string>
<key>StandardErrorPath</key><string>{xml_escape(str(LOG_DIR / 'error.log'))}</string>
</dict></plist>
'''


def install_schedule(load: bool) -> int:
    ensure_private_dir(RUNTIME_DIR)
    ensure_private_dir(LOG_DIR)
    LAUNCH_PATH.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(LAUNCH_PATH.parent, 0o700)
    atomic_write(LAUNCH_PATH, plist_text())
    if load:
        domain = f"gui/{os.getuid()}"
        subprocess.run(["launchctl", "bootout", domain, str(LAUNCH_PATH)], capture_output=True, text=True, timeout=30, check=False)
        completed = subprocess.run(["launchctl", "bootstrap", domain, str(LAUNCH_PATH)], capture_output=True, text=True, timeout=30, check=False)
        if completed.returncode != 0:
            raise DailySyncError("launchd_load_failed")
    print(json.dumps({"status": "schedule_installed", "loaded": load, "label": LAUNCH_LABEL, "timezone": "Asia/Shanghai", "weekdayTarget": "18:05", "no_forced_wake": True}, ensure_ascii=False))
    return 0


def remove_schedule() -> int:
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(LAUNCH_PATH)], capture_output=True, text=True, timeout=30, check=False)
    try:
        LAUNCH_PATH.unlink()
    except FileNotFoundError:
        pass
    print(json.dumps({"status": "schedule_removed", "label": LAUNCH_LABEL}, ensure_ascii=False))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="daily private Part 1–4 data refresh")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run one full local refresh")
    run.add_argument("--automatic", action="store_true", help="apply weekday and 18:05 Asia/Shanghai idempotence gate")
    install = sub.add_parser("install-schedule", help="write LaunchAgent; use --load to activate")
    install.add_argument("--load", action="store_true")
    sub.add_parser("remove-schedule", help="unload and remove LaunchAgent")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "run":
            return sync_once(args.automatic)
        if args.command == "install-schedule":
            return install_schedule(args.load)
        return remove_schedule()
    except DailySyncError as error:
        print(json.dumps({"status": "error", "category": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
