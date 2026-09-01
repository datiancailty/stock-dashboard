#!/usr/bin/env python3
"""Daily local-only Part 1–4 private-data refresh with fail-closed gates.

This worker performs one weekday Asia/Shanghai refresh after 18:05: a rolling
35-day official Part 4 announcement scan, then complete private quote and
future-dividend snapshots. It does not access GitHub write paths, Codex, VPS,
orders, accounts, or strategy parameters. A failed stage prevents the daily
success state from advancing, preserving previously successful Hosted data.
"""
from __future__ import annotations

import argparse
import json
import os
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


def run_checked(args: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, *args], cwd=ROOT, capture_output=True, text=True, timeout=600, check=False
    )
    # Child workers emit only their documented sanitized JSON summaries. Never
    # forward raw stdout/stderr into state or output.
    try:
        payload = json.loads(completed.stdout.strip())
    except (json.JSONDecodeError, TypeError):
        raise DailySyncError("daily_child_response_invalid") from None
    if completed.returncode != 0 or not isinstance(payload, dict) or payload.get("status") != "ok":
        raise DailySyncError(str(payload.get("category", "daily_child_failed")))
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
    try:
        notices = run_checked([
            "scripts/part4_official_announcement_sync.py", "sync", "--from", start, "--to", end,
            "--include-structured-pre-disclosures", "--structured-code", "600036",
        ])
        quotes = run_checked(["scripts/personal_market_snapshot_sync.py"])
        future = run_checked(["scripts/personal_future_dividend_grid_sync.py"])
    except subprocess.TimeoutExpired:
        print(json.dumps({"status": "error", "category": "daily_child_timeout", "date": day}, ensure_ascii=False))
        return 2
    except DailySyncError as error:
        print(json.dumps({"status": "error", "category": str(error), "date": day}, ensure_ascii=False))
        return 2

    atomic_write(STATE_PATH, json.dumps({"successfulDate": day, "completedAt": now.isoformat(timespec="seconds")}, ensure_ascii=False) + "\n")
    print(json.dumps({
        "status": "ok", "date": day, "windowStart": start, "windowEnd": end,
        "noticeWatchlistCount": notices.get("watchlistCount"), "noticeStored": notices.get("privateWrite", {}).get("stored"),
        "quoteStored": quotes.get("stored"), "futureGridStored": future.get("stored"),
        "coverageComplete": True, "published": True, "private_payload_not_emitted": True,
    }, ensure_ascii=False))
    return 0


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
