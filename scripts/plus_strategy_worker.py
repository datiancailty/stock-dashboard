#!/usr/bin/env python3
"""Private ChatGPT Plus/Codex strategy worker for the stock dashboard.

The worker is intentionally local-only. It uses the normal local Codex CLI
login (ChatGPT subscription access), reads the private dashboard through the
same authenticated Supabase session boundary as the browser, and publishes
only validated derived results through one owner-scoped RPC.

It never reads, accepts, or emits an OpenAI Platform API key. The refresh token
is kept in the macOS Keychain; the user's password is used only by ``setup``
and is never written to disk.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pty
import re
import select
import shutil
import subprocess
import sys
import tempfile
import termios
import time
import unicodedata
from datetime import datetime
from getpass import getpass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from xml.sax.saxutils import escape as xml_escape
from zoneinfo import ZoneInfo

import requests


ROOT = Path(__file__).resolve().parents[1]
PAGE_ORIGIN = "https://datiancailty.github.io"
EXPECTED_SUPABASE_URL = "https://shfdpceuamzrftwufdwo.supabase.co"
BJ = ZoneInfo("Asia/Shanghai")
USERNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{1,30}[a-z0-9])$")
CODE_RE = re.compile(r"^[0-9]{6}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
WORKER_ID = "macos-local-codex"
AUTH_MODE = "chatgpt_subscription"
PROVIDER = "openai-codex"
MODEL = os.environ.get("CODEX_STRATEGY_MODEL", "gpt-5.6-luna").strip() or "gpt-5.6-luna"
ANALYSIS_SCHEMA_VERSION = 3
CONFIDENCE_SCALE = "research_match_percent_0_to_100"
CONFIDENCE_MEANING = "研究匹配度，不是涨跌概率、收益概率或自动下单依据"
DEFAULT_WORKER_DIR = Path(
    os.environ.get(
        "STOCK_DASHBOARD_WORKER_DIR",
        str(Path.home() / ".hermes" / "workspace" / "stock-dashboard-private-worker"),
    )
).expanduser()
CONFIG_NAME = "config.json"
KEYCHAIN_SERVICE = "hermes.stock-dashboard.strategy-worker.refresh-v1"
SCHEMA_PATH = ROOT / "scripts" / "plus_strategy_output.schema.json"
LAUNCH_LABEL = "com.hermes.stock-dashboard.strategy-worker"
LAUNCH_DIR = Path.home() / "Library" / "LaunchAgents"
LAUNCH_PATH = LAUNCH_DIR / f"{LAUNCH_LABEL}.plist"

ALLOWED_BRIEF_ACTIONS = {"分批买入", "暂不买入", "当前不买"}
ALLOWED_ADVICE_ACTIONS = {
    "继续观察",
    "等待接近5%",
    "可分批买入",
    "高性价比分批买",
    "小仓分批/等待",
    "做T卖出观察",
    "卖出区提醒",
    "暂不追入",
    "等待正式数据",
}
ALLOWED_FEEDBACK_STATUSES = {"executed", "not_executed", "deferred"}


class WorkerError(RuntimeError):
    """A safe, non-sensitive worker failure category."""

    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


def now_bj() -> datetime:
    return datetime.now(BJ)


def iso_now() -> str:
    return now_bj().isoformat(timespec="seconds")


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(json_bytes(value)).hexdigest()


def ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def atomic_write_text(path: Path, text: str, mode: int = 0o600) -> None:
    ensure_private_dir(path.parent)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        os.chmod(path, mode)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def canonical_username(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    if not USERNAME_RE.fullmatch(normalized):
        raise WorkerError("username_invalid")
    return normalized


def valid_https_url(value: str) -> str:
    parsed = urlparse(str(value or "").rstrip("/"))
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise WorkerError("supabase_url_invalid")
    if parsed.path not in ("", "/") or not parsed.netloc:
        raise WorkerError("supabase_url_invalid")
    return str(value).rstrip("/")


def runtime_config() -> dict[str, str]:
    path = ROOT / "assets" / "runtime-config.js"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise WorkerError("runtime_config_unavailable") from error
    url_match = re.search(r"\burl\s*:\s*(['\"])(.*?)\1", text)
    key_match = re.search(r"\banonKey\s*:\s*(['\"])(.*?)\1", text)
    if not url_match or not key_match:
        raise WorkerError("runtime_config_invalid")
    url = valid_https_url(url_match.group(2))
    if url != EXPECTED_SUPABASE_URL:
        raise WorkerError("runtime_config_invalid")
    anon_key = key_match.group(2).strip()
    if not anon_key or len(anon_key) > 512:
        raise WorkerError("runtime_config_invalid")
    return {"supabaseUrl": url, "supabaseAnonKey": anon_key}


def config_path(worker_dir: Path) -> Path:
    return worker_dir / CONFIG_NAME


def load_config(worker_dir: Path) -> dict[str, str]:
    path = config_path(worker_dir)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise WorkerError("worker_not_setup") from error
    except (OSError, ValueError) as error:
        raise WorkerError("worker_config_invalid") from error
    if not isinstance(value, dict):
        raise WorkerError("worker_config_invalid")
    required = {"schemaVersion", "supabaseUrl", "supabaseAnonKey", "username", "keychainService", "keychainAccount"}
    if not required.issubset(value):
        raise WorkerError("worker_config_invalid")
    if value.get("schemaVersion") != 1:
        raise WorkerError("worker_config_version_unsupported")
    # A config file must never become a password/token cache.
    forbidden = {"password", "access_token", "refresh_token", "service_role", "api_key", "openai_api_key"}
    if forbidden.intersection(str(key).lower() for key in value):
        raise WorkerError("worker_config_contains_credential")
    username = canonical_username(str(value.get("username", "")))
    service = str(value.get("keychainService", ""))
    account = str(value.get("keychainAccount", ""))
    if service != KEYCHAIN_SERVICE or account != username:
        raise WorkerError("worker_keychain_binding_invalid")
    supabase_url = valid_https_url(str(value["supabaseUrl"]))
    if supabase_url != EXPECTED_SUPABASE_URL:
        raise WorkerError("worker_config_invalid")
    anon_key = str(value["supabaseAnonKey"]).strip()
    if not anon_key or len(anon_key) > 512:
        raise WorkerError("worker_config_invalid")
    return {
        "schemaVersion": "1",
        "supabaseUrl": supabase_url,
        "supabaseAnonKey": anon_key,
        "username": username,
        "keychainService": service,
        "keychainAccount": account,
    }


def keychain_read(service: str, account: str) -> str:
    completed = subprocess.run(
        ["security", "find-generic-password", "-a", account, "-s", service, "-w"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    value = completed.stdout.strip()
    if completed.returncode != 0 or not value or "\n" in value or "\r" in value:
        raise WorkerError("worker_refresh_token_missing")
    return value


def keychain_write(service: str, account: str, value: str) -> None:
    if not value or "\n" in value or "\r" in value or "\x00" in value:
        raise WorkerError("worker_refresh_token_invalid")
    # ``security -w`` reads from its controlling terminal rather than ordinary
    # stdin. It also consumes each value only after printing its corresponding
    # prompt, so preloading a pipe/PTY can lose both values. ``pty.fork()``
    # gives the child its own no-echo controlling terminal; the parent waits
    # for each fixed prompt before supplying the opaque refresh token. Nothing
    # is placed in argv, shell history, a file, or terminal output.
    child_pid: int | None = None
    master_fd: int | None = None
    try:
        child_pid, master_fd = pty.fork()
        if child_pid == 0:
            try:
                attributes = termios.tcgetattr(0)
                attributes[3] &= ~termios.ECHO
                termios.tcsetattr(0, termios.TCSANOW, attributes)
                os.execvp(
                    "security",
                    ["security", "add-generic-password", "-U", "-a", account, "-s", service, "-w"],
                )
            except BaseException:
                os._exit(127)

        prompt_buffer = bytearray()
        first_sent = False
        second_sent = False
        deadline = time.monotonic() + 30
        exit_status: int | None = None
        while time.monotonic() < deadline:
            ended_pid, status = os.waitpid(child_pid, os.WNOHANG)
            if ended_pid == child_pid:
                exit_status = status
                break
            readable, _, _ = select.select([master_fd], [], [], 0.25)
            if not readable:
                continue
            try:
                chunk = os.read(master_fd, 4096)
            except OSError:
                chunk = b""
            if not chunk:
                continue
            prompt_buffer.extend(chunk.lower())
            # Keep the detector bounded. With ECHO disabled, the token itself
            # is never returned by the PTY; this buffer is never logged.
            if len(prompt_buffer) > 4096:
                del prompt_buffer[:-1024]
            if not first_sent and b"password data for new item:" in prompt_buffer:
                os.write(master_fd, (value + "\n").encode("utf-8"))
                first_sent = True
            if first_sent and not second_sent and b"retype password for new item:" in prompt_buffer:
                os.write(master_fd, (value + "\n").encode("utf-8"))
                second_sent = True
        if exit_status is None:
            os.kill(child_pid, 9)
            _, exit_status = os.waitpid(child_pid, 0)
        if not (first_sent and second_sent and os.WIFEXITED(exit_status) and os.WEXITSTATUS(exit_status) == 0):
            raise WorkerError("worker_keychain_write_failed")
    except (OSError, ValueError) as error:
        raise WorkerError("worker_keychain_write_failed") from error
    finally:
        if master_fd is not None:
            os.close(master_fd)
    try:
        stored = keychain_read(service, account)
    except WorkerError as error:
        raise WorkerError("worker_keychain_write_failed") from error
    if stored != value:
        raise WorkerError("worker_keychain_write_failed")


def keychain_delete(service: str, account: str) -> None:
    subprocess.run(
        ["security", "delete-generic-password", "-a", account, "-s", service],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def response_json(response: requests.Response) -> Any:
    try:
        return response.json()
    except (TypeError, ValueError) as error:
        raise WorkerError("private_service_invalid_response") from error


def login_request(supabase_url: str, username: str, password: str) -> dict[str, Any]:
    try:
        response = requests.post(
            f"{supabase_url}/functions/v1/username-login",
            headers={
                "Origin": PAGE_ORIGIN,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json={"username": username, "password": password},
            timeout=30,
        )
    except requests.RequestException as error:
        raise WorkerError("auth_service_unavailable") from error
    if response.status_code in (401, 403):
        raise WorkerError("invalid_credentials")
    if response.status_code != 200:
        raise WorkerError("auth_service_unavailable")
    payload = response_json(response)
    if not isinstance(payload, dict) or not isinstance(payload.get("access_token"), str) or not isinstance(
        payload.get("refresh_token"), str
    ):
        raise WorkerError("auth_service_invalid_session")
    return payload


def refresh_session(config: dict[str, str]) -> str:
    refresh_token = keychain_read(config["keychainService"], config["keychainAccount"])
    try:
        response = requests.post(
            f"{config['supabaseUrl']}/auth/v1/token?grant_type=refresh_token",
            headers={"apikey": config["supabaseAnonKey"], "Accept": "application/json", "Content-Type": "application/json"},
            json={"refresh_token": refresh_token},
            timeout=30,
        )
    except requests.RequestException as error:
        raise WorkerError("auth_service_unavailable") from error
    if response.status_code in (400, 401, 403):
        raise WorkerError("worker_login_expired")
    if not response.ok:
        raise WorkerError("auth_service_unavailable")
    payload = response_json(response)
    access_token = payload.get("access_token") if isinstance(payload, dict) else None
    next_refresh = payload.get("refresh_token") if isinstance(payload, dict) else None
    if not isinstance(access_token, str) or not access_token or not isinstance(next_refresh, str) or not next_refresh:
        raise WorkerError("auth_service_invalid_session")
    if next_refresh != refresh_token:
        keychain_write(config["keychainService"], config["keychainAccount"], next_refresh)
    return access_token


def unwrap_rpc(value: Any) -> Any:
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
        return value[0]
    return value


def private_rpc(config: dict[str, str], access_token: str, name: str, body: dict[str, Any]) -> Any:
    try:
        response = requests.post(
            f"{config['supabaseUrl']}/rest/v1/rpc/{name}",
            headers={
                "apikey": config["supabaseAnonKey"],
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=60,
        )
    except requests.RequestException as error:
        raise WorkerError("private_service_unavailable") from error
    if response.status_code in (401, 403):
        raise WorkerError("private_session_rejected")
    if response.status_code == 404:
        raise WorkerError("hosted_worker_contract_missing")
    if response.status_code in (409, 422):
        raise WorkerError("private_payload_rejected")
    if not response.ok:
        raise WorkerError("private_service_failed")
    return unwrap_rpc(response_json(response))


def finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def short_text(value: Any, limit: int, *, allow_empty: bool = True) -> str:
    text = str(value or "").strip()
    if not allow_empty and not text:
        raise WorkerError("private_source_shape_invalid")
    return text[:limit]


def normalized_position(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, Any] = {}
    zone = short_text(value.get("zone"), 20)
    if zone:
        result["zone"] = zone
    percent = finite_number(value.get("percent"))
    if percent is not None:
        result["percent"] = round(percent, 3)
    as_of = short_text(value.get("asOf"), 32)
    if as_of:
        result["asOf"] = as_of
    return result or None


def normalized_positions(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result = {}
    for period in ("day", "week", "month"):
        item = normalized_position(value.get(period))
        if item:
            result[period] = item
    return result


def normalized_trade(record: Any) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    code = short_text(record.get("code"), 12)
    if not CODE_RE.fullmatch(code):
        return None
    context = record.get("context") if isinstance(record.get("context"), dict) else {}
    result: dict[str, Any] = {
        "id": short_text(record.get("id"), 160),
        "date": short_text(record.get("date"), 16),
        "code": code,
        "name": short_text(record.get("name"), 80),
        "action": short_text(record.get("action"), 30),
    }
    price = finite_number(record.get("price"))
    shares = finite_number(record.get("shares"))
    if price is not None:
        result["price"] = round(price, 6)
    if shares is not None and shares.is_integer() and shares > 0:
        result["shares"] = int(shares)
    historical_yield = finite_number(context.get("yield"))
    if historical_yield is not None:
        result["historicalYield"] = round(historical_yield, 6)
    context_status = short_text(context.get("status"), 20)
    if context_status:
        result["contextStatus"] = context_status
    positions = normalized_positions(context.get("positions"))
    if positions:
        result["positions"] = positions
    return result


def trade_stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_stock: dict[str, dict[str, Any]] = {}
    buy_count = 0
    sell_count = 0
    for record in records:
        action = str(record.get("action") or "")
        is_buy = "买入" in action
        is_sell = "卖出" in action
        if is_buy:
            buy_count += 1
        if is_sell:
            sell_count += 1
        code = str(record.get("code") or "")
        if not CODE_RE.fullmatch(code) or not (is_buy or is_sell):
            continue
        item = by_stock.setdefault(
            code,
            {
                "code": code,
                "name": record.get("name") or code,
                "buyCount": 0,
                "sellCount": 0,
                "buyShares": 0,
                "sellShares": 0,
                "buyAmount": 0.0,
                "sellAmount": 0.0,
                "buyZones": {},
                "sellZones": {},
            },
        )
        side = "buy" if is_buy else "sell"
        item[f"{side}Count"] += 1
        shares = finite_number(record.get("shares")) or 0
        price = finite_number(record.get("price")) or 0
        if shares > 0 and shares.is_integer():
            item[f"{side}Shares"] += int(shares)
            item[f"{side}Amount"] += price * int(shares)
        positions = record.get("positions") if isinstance(record.get("positions"), dict) else {}
        day_zone = str((positions.get("day") or {}).get("zone") or "")
        if day_zone:
            zones = item[f"{side}Zones"]
            zones[day_zone] = zones.get(day_zone, 0) + 1
    for item in by_stock.values():
        buy_shares = item.pop("buyShares")
        sell_shares = item.pop("sellShares")
        buy_amount = item.pop("buyAmount")
        sell_amount = item.pop("sellAmount")
        item["avgBuyPrice"] = round(buy_amount / buy_shares, 6) if buy_shares else None
        item["avgSellPrice"] = round(sell_amount / sell_shares, 6) if sell_shares else None
    dates = [str(record.get("date")) for record in records if record.get("date")]
    return {
        "recordCount": len(records),
        "buyCount": buy_count,
        "sellCount": sell_count,
        "firstDate": min(dates) if dates else None,
        "lastDate": max(dates) if dates else None,
        "byStock": sorted(
            by_stock.values(),
            key=lambda item: item["buyCount"] + item["sellCount"],
            reverse=True,
        ),
    }


def normalized_market_stock(stock: Any) -> dict[str, Any] | None:
    if not isinstance(stock, dict):
        return None
    code = short_text(stock.get("code"), 12)
    if not CODE_RE.fullmatch(code):
        return None
    result: dict[str, Any] = {
        "code": code,
        "name": short_text(stock.get("name"), 80),
    }
    price = finite_number(stock.get("price"))
    annual = finite_number(stock.get("annualDividend")) or 0.0
    interim = finite_number(stock.get("interimDividend")) or 0.0
    total = annual + interim
    if price is not None and price > 0:
        result["price"] = round(price, 6)
        result["formalDividendYield"] = round(total / price * 100, 6)
    else:
        result["price"] = None
        result["formalDividendYield"] = None
    result["annualDividend"] = round(annual, 6)
    result["interimDividend"] = round(interim, 6)
    fiscal_year = short_text(stock.get("fiscalYear"), 20)
    if fiscal_year:
        result["fiscalYear"] = fiscal_year
    positions = normalized_positions(stock.get("positions"))
    if positions:
        result["positions"] = positions
    boll = stock.get("weeklyBoll") if isinstance(stock.get("weeklyBoll"), dict) else {}
    if boll:
        boll_result: dict[str, Any] = {}
        for key in ("asOf", "basis", "period"):
            value = short_text(boll.get(key), 64)
            if value:
                boll_result[key] = value
        for key in ("upper", "middle", "lower", "stddev", "multiplier"):
            value = finite_number(boll.get(key))
            if value is not None:
                boll_result[key] = round(value, 6)
        if boll_result:
            result["weeklyBoll"] = boll_result
    return result


def normalized_feedback(record: Any) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    status = short_text(record.get("status"), 20)
    if status not in ALLOWED_FEEDBACK_STATUSES:
        return None
    result = {"status": status}
    for source, target, limit in (("date", "date", 16), ("code", "code", 12), ("recommendationId", "recommendationId", 200)):
        value = short_text(record.get(source), limit)
        if value:
            result[target] = value
    return result


def recommendation_stats(records: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    buys = [record for record in records if record.get("action") == "分批买入"]
    successes = [record for record in buys if (record.get("evaluation") or {}).get("status") == "success"]
    failures = [record for record in buys if (record.get("evaluation") or {}).get("status") == "failed"]
    pending = [
        record
        for record in buys
        if (record.get("evaluation") or {}).get("status") in ("pending", "no_data", None)
    ]
    weekly_hits = [record for record in buys if (record.get("evaluation") or {}).get("weeklyUpperFirstAt")]
    resolved = len(successes) + len(failures)
    hit_days = [finite_number((record.get("evaluation") or {}).get("tradingDaysToHit")) for record in successes]
    calendar_days = [finite_number((record.get("evaluation") or {}).get("calendarDaysToHit")) for record in successes]
    weekly_days = [
        finite_number((record.get("evaluation") or {}).get("tradingDaysToWeeklyUpper")) for record in weekly_hits
    ]
    avg = lambda values: round(sum(x for x in values if x is not None) / len([x for x in values if x is not None]), 3) if any(x is not None for x in values) else None
    performance = {
        "criterion": "分批买入后30个交易日内，盘中最高价达到指令价+5%",
        "targetGainPct": 5,
        "windowTradingDays": 30,
        "totalCommands": len(records),
        "buyCommands": len(buys),
        "resolved": resolved,
        "successes": len(successes),
        "failures": len(failures),
        "pending": len(pending),
        "successRate": round(len(successes) / resolved * 100, 3) if resolved else None,
        "avgTradingDaysToHit": avg(hit_days),
        "avgCalendarDaysToHit": avg(calendar_days),
        "weeklyUpperHits": len(weekly_hits),
        "weeklyUpperRate": round(len(weekly_hits) / len(buys) * 100, 3) if buys else None,
        "avgTradingDaysToWeeklyUpper": avg(weekly_days),
    }
    recent = []
    for record in records[-40:]:
        evaluation = record.get("evaluation") if isinstance(record.get("evaluation"), dict) else {}
        item = {
            "date": short_text(record.get("recommendedAt") or record.get("date"), 32),
            "code": short_text(record.get("code"), 12),
            "action": short_text(record.get("action"), 30),
            "evaluation": {
                key: evaluation.get(key)
                for key in (
                    "status",
                    "observedTradingDays",
                    "maxGainPct",
                    "latestReturnPct",
                    "tradingDaysToHit",
                    "calendarDaysToHit",
                    "weeklyUpperFirstAt",
                    "tradingDaysToWeeklyUpper",
                )
                if evaluation.get(key) is not None
            },
        }
        if item["code"] and item["date"]:
            recent.append(item)
    return performance, recent


def build_context(part6: Any, market: Any, run_id: str, watchlist: Any = None) -> dict[str, Any]:
    if not isinstance(part6, dict) or not isinstance(market, dict):
        raise WorkerError("private_source_shape_invalid")
    trades_container = part6.get("trades") if isinstance(part6.get("trades"), dict) else {}
    feedback_container = part6.get("feedback") if isinstance(part6.get("feedback"), dict) else {}
    recommendations_container = part6.get("recommendations") if isinstance(part6.get("recommendations"), dict) else {}
    raw_trades = trades_container.get("records") if isinstance(trades_container.get("records"), list) else []
    raw_feedback = feedback_container.get("records") if isinstance(feedback_container.get("records"), list) else []
    raw_recommendations = recommendations_container.get("records") if isinstance(recommendations_container.get("records"), list) else []
    trades = [item for record in raw_trades if (item := normalized_trade(record)) is not None]
    trades.sort(key=lambda item: (str(item.get("date") or ""), str(item.get("id") or "")), reverse=True)
    feedback = [item for record in raw_feedback if (item := normalized_feedback(record)) is not None]
    recommendations = [record for record in raw_recommendations if isinstance(record, dict)]
    stocks = [item for stock in (market.get("stocks") if isinstance(market.get("stocks"), list) else []) if (item := normalized_market_stock(stock)) is not None]
    stocks.sort(key=lambda item: item["code"])
    if watchlist is not None:
        if not isinstance(watchlist, list):
            raise WorkerError("private_source_shape_invalid")
        watchlist_codes = {
            short_text(item.get("code"), 12)
            for item in watchlist
            if isinstance(item, dict) and CODE_RE.fullmatch(short_text(item.get("code"), 12))
        }
        stocks = [item for item in stocks if item["code"] in watchlist_codes]
    performance, recent_recommendations = recommendation_stats(recommendations)
    feedback_counts = {status: sum(item.get("status") == status for item in feedback) for status in ALLOWED_FEEDBACK_STATUSES}
    previous = part6.get("analysis") if isinstance(part6.get("analysis"), dict) else {}
    previous_summary = {
        "status": short_text(previous.get("status"), 30),
        "profileSummary": short_text(previous.get("profileSummary"), 1500),
        "learnedRules": [short_text(item, 500) for item in (previous.get("learnedRules") or []) if str(item).strip()][:12],
    }
    context = {
        "schemaVersion": 1,
        "runId": run_id,
        "generatedAt": iso_now(),
        "source": "authenticated Supabase private RPC",
        "fixedGuardrails": [
            "正式股息率使用已实施、上一完整年度的税前每股现金分红，不把当前年度中期事件提前并入正式分子。",
            "约5%开始分批买入，接近7%属于高性价比区；具体执行仍需用户确认。",
            "结合日线、周线、月线位置观察，不把位置标签单独当成买卖依据。",
            "4%为退出观察线，3.5%为最终退出线；模型不能改写固定底线。",
            "所有建议仅供人工复核，不自动下单、不修改执行参数。",
        ],
        "tradeHistory": {"stats": trade_stats(trades), "recent": trades[:180]},
        "feedback": {"stats": {"count": len(feedback), **feedback_counts}, "recent": feedback[-80:]},
        "recommendations": {"performance": performance, "recent": recent_recommendations},
        "currentStocks": stocks,
        "previousAnalysis": previous_summary,
    }
    return context


def prompt_for(context: dict[str, Any]) -> str:
    return (
        "你是谨慎的个人投资复盘助手。下面是已通过认证的当前用户私有股票研究数据，"
        "只能依据这些数据生成复盘摘要，不得补造行情、分红、持仓或用户反馈。\n"
        "固定原则不能被改写；learnedRules 只能描述观察到的偏好或待验证假设；"
        "不要承诺收益，不要输出思维链，不要自动交易，也不要建议修改程序参数。\n"
        "请严格只输出符合附带 JSON Schema 的 JSON 对象。briefCommand 只能选择一只当前股票；"
        "如果没有充分证据，使用‘当前不买’。建议必须带条件或明确的人工确认边界。"
        f"confidenceScale 必须精确为“{CONFIDENCE_SCALE}”；所有 confidence 必须为 0—100 的整数研究匹配度"
        "（例如 72 表示 72%，0.72 不合法；1 只表示 1%，不表示 100%）。"
        f"它仅表示当前建议与已确认规则、当前数据和有限样本的一致性，{CONFIDENCE_MEANING}。\n"
        "私有上下文如下：\n"
        + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    )


def validate_confidence(value: Any) -> int:
    if isinstance(value, bool):
        raise WorkerError("codex_output_invalid")
    number = finite_number(value)
    if number is None or number < 0 or number > 100 or not number.is_integer():
        raise WorkerError("codex_output_invalid")
    return int(number)


def validate_model_output(value: Any, universe: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkerError("codex_output_invalid")
    required = {"profileSummary", "learnedRules", "recordInsights", "briefCommand", "advice", "confidenceScale"}
    if set(value) != required or value.get("confidenceScale") != CONFIDENCE_SCALE:
        raise WorkerError("codex_output_invalid")
    summary = short_text(value.get("profileSummary"), 1500, allow_empty=False)
    rules = value.get("learnedRules")
    if not isinstance(rules, list) or len(rules) > 12 or any(not isinstance(item, str) or not item.strip() or len(item) > 500 for item in rules):
        raise WorkerError("codex_output_invalid")
    insights = value.get("recordInsights")
    if not isinstance(insights, list) or len(insights) > 20:
        raise WorkerError("codex_output_invalid")
    normalized_insights = {}
    for item in insights:
        if not isinstance(item, dict) or set(item) != {"id", "insight"}:
            raise WorkerError("codex_output_invalid")
        key = short_text(item.get("id"), 160)
        text = item.get("insight")
        if not RUN_ID_RE.fullmatch(key) or not isinstance(text, str) or not text.strip() or len(text) > 500:
            raise WorkerError("codex_output_invalid")
        if key in normalized_insights:
            raise WorkerError("codex_output_invalid")
        normalized_insights[key] = text.strip()
    brief = value.get("briefCommand")
    if not isinstance(brief, dict) or set(brief) != {"code", "action", "reason", "condition", "confidence"}:
        raise WorkerError("codex_output_invalid")
    brief_code = short_text(brief.get("code"), 6)
    brief_action = short_text(brief.get("action"), 20)
    if brief_code and (not CODE_RE.fullmatch(brief_code) or brief_code not in universe):
        raise WorkerError("codex_output_invalid")
    if brief_action not in ALLOWED_BRIEF_ACTIONS or (brief_action == "分批买入" and not brief_code):
        raise WorkerError("codex_output_invalid")
    normalized_brief = {
        "code": brief_code,
        "action": brief_action,
        "reason": short_text(brief.get("reason"), 350, allow_empty=False),
        "condition": short_text(brief.get("condition"), 250, allow_empty=False),
        "confidence": validate_confidence(brief.get("confidence")),
    }
    advice = value.get("advice")
    if not isinstance(advice, list) or len(advice) > 30:
        raise WorkerError("codex_output_invalid")
    normalized_advice = []
    for item in advice:
        if not isinstance(item, dict) or set(item) != {"code", "action", "reason", "confidence"}:
            raise WorkerError("codex_output_invalid")
        code = short_text(item.get("code"), 6)
        action = short_text(item.get("action"), 30)
        if not CODE_RE.fullmatch(code) or code not in universe or action not in ALLOWED_ADVICE_ACTIONS:
            raise WorkerError("codex_output_invalid")
        normalized_advice.append(
            {
                "code": code,
                "action": action,
                "reason": short_text(item.get("reason"), 600, allow_empty=False),
                "confidence": validate_confidence(item.get("confidence")),
            }
        )
    return {
        "profileSummary": summary,
        "learnedRules": [item.strip() for item in rules],
        "recordInsights": normalized_insights,
        "briefCommand": normalized_brief,
        "advice": normalized_advice,
        "confidenceScale": CONFIDENCE_SCALE,
    }


def codex_path() -> str:
    configured = os.environ.get("CODEX_PATH", "").strip()
    candidates = [configured, shutil.which("codex") or "", str(Path.home() / ".local" / "node" / "bin" / "codex")]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    raise WorkerError("codex_not_installed")


def check_codex_login(executable: str) -> None:
    try:
        completed = subprocess.run(
            [executable, "login", "status"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise WorkerError("codex_auth_unavailable") from error
    if completed.returncode != 0:
        raise WorkerError("codex_auth_unavailable")


def run_codex(context: dict[str, Any]) -> dict[str, Any]:
    executable = codex_path()
    check_codex_login(executable)
    if not SCHEMA_PATH.is_file():
        raise WorkerError("codex_schema_missing")
    prompt = prompt_for(context)
    with tempfile.TemporaryDirectory(prefix="hermes-plus-worker-", dir="/tmp") as directory:
        workdir = Path(directory)
        os.chmod(workdir, 0o700)
        try:
            subprocess.run(["git", "init", "--quiet"], cwd=workdir, capture_output=True, text=True, timeout=30, check=True)
            schema = workdir / "output.schema.json"
            output = workdir / "output.json"
            schema.write_text(SCHEMA_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            os.chmod(schema, 0o600)
            command = [
                executable,
                "exec",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "--ignore-rules",
                "--model",
                MODEL,
                "--output-schema",
                str(schema),
                "--output-last-message",
                str(output),
                "--cd",
                str(workdir),
                "-",
            ]
            completed = subprocess.run(
                command,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise WorkerError("codex_timeout") from error
        except (OSError, subprocess.CalledProcessError) as error:
            raise WorkerError("codex_runtime_failed") from error
        if completed.returncode != 0 or not output.is_file() or output.stat().st_size > 200_000:
            raise WorkerError("codex_runtime_failed")
        try:
            parsed = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise WorkerError("codex_output_invalid") from error
        return parsed


def health_payload(status: str, reason: str, run_id: str, input_sha: str, output_sha: str) -> dict[str, Any]:
    if status not in {"ok", "failed", "unknown"} or not SHA256_RE.fullmatch(input_sha) or not SHA256_RE.fullmatch(output_sha):
        raise WorkerError("worker_health_shape_invalid")
    return {
        "schemaVersion": 2,
        "checkedAt": iso_now(),
        "provider": PROVIDER,
        "model": MODEL,
        "check": "private_local_strategy_worker",
        "status": status,
        "reason": reason,
        "authMode": AUTH_MODE,
        "workerId": WORKER_ID,
        "runId": run_id,
        "inputSha256": input_sha,
        "outputSha256": output_sha,
        "platformApiKeyUsed": False,
    }


def analysis_payload(model_output: dict[str, Any], context: dict[str, Any], run_id: str, input_sha: str) -> dict[str, Any]:
    universe = {item["code"] for item in context["currentStocks"]}
    normalized = validate_model_output(model_output, universe)
    output = {
        "schemaVersion": ANALYSIS_SCHEMA_VERSION,
        "updatedAt": iso_now(),
        "model": MODEL,
        "provider": PROVIDER,
        "authMode": AUTH_MODE,
        "status": "success",
        "source": "private_local_codex_worker",
        "runId": run_id,
        "inputSha256": input_sha,
        "profileSummary": normalized["profileSummary"],
        "learnedRules": normalized["learnedRules"],
        "recordInsights": normalized["recordInsights"],
        "briefCommand": {**normalized["briefCommand"], "id": f"brief-{run_id}"},
        "confidenceScale": normalized["confidenceScale"],
        "confidenceMeaning": CONFIDENCE_MEANING,
        "feedbackStats": context["feedback"]["stats"],
        "recommendationPerformance": context["recommendations"]["performance"],
        "advice": normalized["advice"],
    }
    return output


def publish_result(
    config: dict[str, str],
    access_token: str,
    run_id: str,
    input_sha: str,
    analysis: dict[str, Any] | None,
    health: dict[str, Any],
) -> None:
    output_sha = str(health.get("outputSha256") or "")
    private_rpc(
        config,
        access_token,
        "personal_publish_strategy_worker_result",
        {
            "p_run_id": run_id,
            "p_input_sha256": input_sha,
            "p_output_sha256": output_sha,
            "p_analysis": analysis,
            "p_health": health,
        },
    )


def setup_worker(worker_dir: Path, requested_username: str | None) -> int:
    public_config = runtime_config()
    username_input = requested_username or input("Dashboard 用户名: ")
    username = canonical_username(username_input)
    password = getpass("Dashboard 密码（不会保存）: ")
    confirm = getpass("再次输入 Dashboard 密码: ")
    if password != confirm:
        raise WorkerError("password_mismatch")
    session = login_request(public_config["supabaseUrl"], username, password)
    access_token = str(session["access_token"])
    refresh_token = str(session["refresh_token"])
    keychain_write(KEYCHAIN_SERVICE, username, refresh_token)
    config = {
        "schemaVersion": 1,
        "supabaseUrl": public_config["supabaseUrl"],
        "supabaseAnonKey": public_config["supabaseAnonKey"],
        "username": username,
        "keychainService": KEYCHAIN_SERVICE,
        "keychainAccount": username,
    }
    verified = private_rpc(config, access_token, "personal_get_part6", {})
    if not isinstance(verified, dict):
        raise WorkerError("private_session_verification_failed")
    ensure_private_dir(worker_dir)
    atomic_write_text(config_path(worker_dir), json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": "setup_ok", "authMode": AUTH_MODE, "keychain": "configured", "privateRpc": "verified"}, ensure_ascii=False))
    return 0


def run_worker(worker_dir: Path, force: bool, automatic: bool = False) -> int:
    config = load_config(worker_dir)
    access_token = refresh_session(config)
    current_time = now_bj()
    if automatic and (current_time.weekday() >= 5 or (current_time.hour, current_time.minute) < (8, 45)):
        print(json.dumps({"status": "skipped", "reason": "not_due_in_asia_shanghai"}, ensure_ascii=False))
        return 0
    run_id = f"codex-{current_time.date().isoformat()}"
    part6 = private_rpc(config, access_token, "personal_get_part6", {})
    if not force and isinstance(part6, dict):
        previous_health = part6.get("api_health") if isinstance(part6.get("api_health"), dict) else {}
        if previous_health.get("authMode") == AUTH_MODE and previous_health.get("runId") == run_id:
            print(json.dumps({"status": "skipped", "reason": "today_already_attempted"}, ensure_ascii=False))
            return 0
    input_sha = sha256_json({"runId": run_id, "source": "authenticated Supabase private RPC", "worker": WORKER_ID})
    try:
        part1 = private_rpc(config, access_token, "personal_get_part1", {})
        if not isinstance(part1, dict) or not isinstance(part1.get("watchlist"), list):
            raise WorkerError("private_source_shape_invalid")
        market = private_rpc(config, access_token, "personal_get_part4", {})
        context = build_context(part6, market, run_id, part1["watchlist"])
        input_sha = sha256_json(context)
        model_output = run_codex(context)
        analysis = analysis_payload(model_output, context, run_id, input_sha)
        output_sha = sha256_json(analysis)
        health = health_payload(
            "ok",
            "ChatGPT Plus/Codex 本机 Worker 输出通过严格校验并已写入私有空间",
            run_id,
            input_sha,
            output_sha,
        )
        publish_result(config, access_token, run_id, input_sha, analysis, health)
        print(json.dumps({"status": "ok", "authMode": AUTH_MODE, "published": True}, ensure_ascii=False))
        return 0
    except WorkerError as error:
        failure_hash = sha256_json({"runId": run_id, "category": error.category, "worker": WORKER_ID})
        health = health_payload(
            "failed",
            {
                "private_source_shape_invalid": "私有策略数据结构未通过校验",
                "private_service_unavailable": "私有数据服务暂时不可用",
                "private_service_failed": "私有数据服务返回失败",
                "private_payload_rejected": "私有结果未通过服务端校验",
                "hosted_worker_contract_missing": "Hosted Worker 合同尚未执行",
                "private_session_rejected": "私有登录会话已失效",
                "codex_not_installed": "本机未找到 Codex CLI",
                "codex_auth_unavailable": "Codex ChatGPT 登录不可用或已过期",
                "codex_timeout": "Codex 本次运行超时",
                "codex_runtime_failed": "Codex 本次运行失败",
                "codex_output_invalid": "Codex 输出未通过严格 JSON 校验",
                "codex_schema_missing": "本机 Worker 输出合同缺失",
                "worker_login_expired": "本机 Worker 登录会话已过期，请重新 setup",
            }.get(error.category, "本机策略 Worker 本次运行未完成"),
            run_id,
            input_sha,
            failure_hash,
        )
        try:
            publish_result(config, access_token, run_id, input_sha, None, health)
            published = True
        except WorkerError:
            published = False
        print(json.dumps({"status": "failed", "category": error.category, "published": published}, ensure_ascii=False))
        return 0 if published else 1


def plist_text(worker_dir: Path) -> str:
    python_executable = sys.executable
    script = str(Path(__file__).resolve())
    log_dir = ensure_private_dir(worker_dir / "logs")
    codex_executable = os.environ.get("CODEX_PATH", "").strip() or (shutil.which("codex") or str(Path.home() / ".local" / "node" / "bin" / "codex"))
    args = [python_executable, script, "run", "--automatic"]
    args_xml = "".join(f"<string>{xml_escape(str(item))}</string>" for item in args)
    intervals = "".join(
        f"<dict><key>Weekday</key><integer>{weekday}</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>45</integer></dict>"
        for weekday in range(1, 6)
    )
    return f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">
<plist version=\"1.0\">
<dict>
  <key>Label</key><string>{xml_escape(LAUNCH_LABEL)}</string>
  <key>ProgramArguments</key><array>{args_xml}</array>
  <key>WorkingDirectory</key><string>{xml_escape(str(ROOT))}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONDONTWRITEBYTECODE</key><string>1</string>
    <key>CODEX_PATH</key><string>{xml_escape(str(codex_executable))}</string>
    <key>STOCK_DASHBOARD_WORKER_DIR</key><string>{xml_escape(str(worker_dir))}</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>StartCalendarInterval</key><array>{intervals}</array>
  <key>ThrottleInterval</key><integer>300</integer>
  <key>StandardOutPath</key><string>{xml_escape(str(log_dir / "worker-status.log"))}</string>
  <key>StandardErrorPath</key><string>{xml_escape(str(log_dir / "worker-error.log"))}</string>
</dict>
</plist>
"""


def install_schedule(worker_dir: Path, load: bool) -> int:
    config = load_config(worker_dir)
    keychain_read(config["keychainService"], config["keychainAccount"])
    check_codex_login(codex_path())
    LAUNCH_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(LAUNCH_DIR, 0o700)
    atomic_write_text(LAUNCH_PATH, plist_text(worker_dir), mode=0o600)
    if load:
        domain = f"gui/{os.getuid()}"
        subprocess.run(["launchctl", "bootout", domain, str(LAUNCH_PATH)], capture_output=True, text=True, timeout=30, check=False)
        completed = subprocess.run(["launchctl", "bootstrap", domain, str(LAUNCH_PATH)], capture_output=True, text=True, timeout=30, check=False)
        if completed.returncode != 0:
            raise WorkerError("launchd_load_failed")
    print(json.dumps({"status": "schedule_installed", "loaded": load, "label": LAUNCH_LABEL}, ensure_ascii=False))
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
    parser = argparse.ArgumentParser(description="Private ChatGPT Plus/Codex stock strategy worker")
    parser.add_argument("--worker-dir", type=Path, default=DEFAULT_WORKER_DIR)
    subparsers = parser.add_subparsers(dest="command", required=True)
    setup = subparsers.add_parser("setup", help="interactive first-time setup; password is not saved")
    setup.add_argument("--username", default=None)
    run = subparsers.add_parser("run", help="run at most one idempotent strategy update for the Asia/Shanghai date")
    run.add_argument("--force", action="store_true", help="explicit manual retry even if today's attempt exists")
    run.add_argument("--automatic", action="store_true", help="apply the weekday 08:45 Asia/Shanghai due gate")
    install = subparsers.add_parser("install-schedule", help="write the launchd plist; do not load unless --load is supplied")
    install.add_argument("--load", action="store_true")
    subparsers.add_parser("remove-schedule", help="unload and remove the launchd plist")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    worker_dir = args.worker_dir.expanduser().resolve()
    try:
        if args.command == "setup":
            return setup_worker(worker_dir, args.username)
        if args.command == "run":
            return run_worker(worker_dir, args.force, args.automatic)
        if args.command == "install-schedule":
            return install_schedule(worker_dir, args.load)
        if args.command == "remove-schedule":
            return remove_schedule()
        raise WorkerError("worker_command_invalid")
    except WorkerError as error:
        print(json.dumps({"status": "error", "category": error.category}, ensure_ascii=False))
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"status": "error", "category": "cancelled"}, ensure_ascii=False))
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
