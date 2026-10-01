"""Private Dashboard config, authentication, RPC and shared refresh lock.

No config/secret reads, network, AI or scheduling occur on import. The paused
AI entry point re-exports these functions; deterministic consumers use this
module without loading that entry point. Error categories and credential
bindings remain the historical wire/storage contract.
"""
from __future__ import annotations
import json
import pty
import re
import select
import subprocess
import termios
import unicodedata
from typing import Any
from urllib.parse import urlparse
import requests

import os,stat,time,fcntl
from pathlib import Path
from contextlib import contextmanager
@contextmanager
def session_lock(root=None,timeout=75):
    root=Path(root or Path.home()/'.hermes/workspace/stock-dashboard-private-runtime')
    if any(p.is_symlink() for p in (root,*root.parents)):raise ValueError('session_lock_invalid')
    root.mkdir(parents=True,mode=0o700,exist_ok=True)
    info=root.stat()
    if info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:raise ValueError('session_lock_invalid')
    fd=os.open(root/'auth-session.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600:raise ValueError('session_lock_invalid')
        deadline=time.monotonic()+timeout
        while True:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                if time.monotonic()>=deadline:raise ValueError('session_lock_timeout') from None
                time.sleep(0.1)
        yield
    finally:os.close(fd)


PAGE_ORIGIN = "https://datiancailty.github.io"


EXPECTED_SUPABASE_URL = "https://shfdpceuamzrftwufdwo.supabase.co"


USERNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{1,30}[a-z0-9])$")


def configured_worker_dir() -> Path:
    """Resolve per session, as the former dynamic worker import did."""
    return Path(os.environ.get(
        "STOCK_DASHBOARD_WORKER_DIR",
        str(Path.home() / ".hermes" / "workspace" / "stock-dashboard-private-worker"),
    )).expanduser()


DEFAULT_WORKER_DIR = configured_worker_dir()


CONFIG_NAME = "config.json"


KEYCHAIN_SERVICE = "hermes.stock-dashboard.strategy-worker.refresh-v1"


class WorkerError(RuntimeError):
    """A safe, non-sensitive worker failure category."""

    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


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
    if "DASHBOARD_SECRET_DIR" in os.environ:
        from dashboard_secret_store import SecretStoreError, read
        try:
            return read(service, account)
        except SecretStoreError as error:
            raise WorkerError(str(error)) from None
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
    if "DASHBOARD_SECRET_DIR" in os.environ:
        from dashboard_secret_store import SecretStoreError, write
        try:
            write(service, account, value)
        except SecretStoreError as error:
            raise WorkerError(str(error)) from None
        return
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
    if "DASHBOARD_SECRET_DIR" in os.environ:
        from dashboard_secret_store import SecretStoreError, delete
        try:
            delete(service, account)
        except SecretStoreError as error:
            raise WorkerError(str(error)) from None
        return
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
