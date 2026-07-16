#!/usr/bin/env python3
"""通过 GitHub Contents API 将本地 JSON 发布到 live 分支，支持大文件与重试。"""
from __future__ import annotations

import argparse
import base64
import json
import os
import time
from pathlib import Path

import requests


def request(session: requests.Session, method: str, url: str, **kwargs) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, 5):
        try:
            response = session.request(method, url, timeout=45, **kwargs)
            if response.status_code not in (409, 422, 429) and response.status_code < 500:
                return response
            last_error = RuntimeError(f"GitHub HTTP {response.status_code}: {response.text[:300]}")
        except requests.RequestException as error:
            last_error = error
        if attempt < 4:
            time.sleep(2 ** (attempt - 1))
    raise RuntimeError(f"GitHub发布重试失败: {last_error}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("remote_name")
    parser.add_argument("message")
    parser.add_argument("--branch", default="live")
    args = parser.parse_args()

    token = os.environ.get("GH_TOKEN")
    repository = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repository:
        raise SystemExit("缺少 GH_TOKEN 或 GITHUB_REPOSITORY")
    if not args.source.is_file() or not args.source.stat().st_size:
        raise SystemExit(f"待发布文件不存在或为空: {args.source}")

    # 先验证是合法 JSON，避免把半写入文件发布到 live。
    raw = args.source.read_bytes()
    json.loads(raw)
    encoded = base64.b64encode(raw).decode("ascii")

    api_base = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    api = f"{api_base}/repos/{repository}/contents/{args.remote_name}"
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })

    # 冲突时重新读取 SHA 并重试完整 PUT；请求正文通过 HTTP body 传输，
    # 不再放进命令行参数，因此不会出现 Argument list too long。
    for conflict_attempt in range(1, 4):
        current = request(session, "GET", api, params={"ref": args.branch})
        sha = current.json().get("sha") if current.status_code == 200 else None
        if current.status_code not in (200, 404):
            current.raise_for_status()
        payload = {
            "message": args.message,
            "content": encoded,
            "branch": args.branch,
        }
        if sha:
            payload["sha"] = sha
        result = request(session, "PUT", api, json=payload)
        if result.status_code in (200, 201):
            body = result.json()
            print(json.dumps({
                "ok": True,
                "remote": args.remote_name,
                "bytes": len(raw),
                "commit": (body.get("commit") or {}).get("sha", "")[:12],
            }, ensure_ascii=False))
            return
        if result.status_code not in (409, 422) or conflict_attempt == 3:
            result.raise_for_status()
        time.sleep(conflict_attempt)
    raise RuntimeError("GitHub发布因持续SHA冲突失败")


if __name__ == "__main__":
    main()
