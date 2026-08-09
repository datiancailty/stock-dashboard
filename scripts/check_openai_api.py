#!/usr/bin/env python3
"""每日验证策略自我更新所使用的 OpenAI API 密钥和模型权限。

该脚本只保存 API 是否可用、HTTP 状态和脱敏原因，不保存或输出密钥。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(os.getenv("STRATEGY_API_HEALTH_OUTPUT", str(ROOT / "data" / "strategy-api-health.json")))
MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
MODEL_URL = os.getenv(
    "OPENAI_CHAT_COMPLETIONS_URL",
    "https://api.openai.com/v1/chat/completions",
)
BJ = ZoneInfo("Asia/Shanghai")


def clean_detail(value: object) -> str:
    """将服务端错误压缩为不含凭证的短提示。"""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"Bearer\s+\S+", "Bearer [redacted]", text, flags=re.I)
    text = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "[redacted]", text)
    return text[:160]


def failure_reason(status_code: int | None, detail: str = "") -> str:
    if status_code in (401, 403):
        return "认证或模型权限失败"
    if status_code == 404:
        return "模型或接口不可用"
    if status_code == 429:
        return "额度或请求频率受限"
    if status_code is not None and status_code >= 500:
        return "OpenAI 服务临时错误"
    return detail or "API 请求失败"


def write_health(payload: dict) -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def main() -> int:
    checked_at = datetime.now(BJ).isoformat(timespec="seconds")
    key = os.getenv("OPENAI_API_KEY", "").strip()
    base = {
        "schemaVersion": 1,
        "checkedAt": checked_at,
        "provider": "official-openai",
        "model": MODEL,
        "check": "chat_completions",
    }

    if not key:
        payload = {
            **base,
            "status": "failed",
            "httpStatus": None,
            "reason": "未配置 OPENAI_API_KEY",
            "secretConfigured": False,
        }
        write_health(payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    request_body = {
        "model": MODEL,
        "messages": [
            {"role": "user", "content": "Reply with OK."},
        ],
        "max_tokens": 1,
    }
    try:
        response = requests.post(
            MODEL_URL,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=request_body,
            timeout=30,
        )
        if response.ok:
            payload = {
                **base,
                "status": "ok",
                "httpStatus": response.status_code,
                "reason": "API 调用成功",
                "secretConfigured": True,
            }
        else:
            detail = ""
            try:
                body = response.json()
                detail = ((body.get("error") or {}).get("message") or "") if isinstance(body, dict) else ""
            except (ValueError, TypeError):
                detail = ""
            payload = {
                **base,
                "status": "failed",
                "httpStatus": response.status_code,
                "reason": failure_reason(response.status_code, clean_detail(detail)),
                "detail": clean_detail(detail),
                "secretConfigured": True,
            }
    except requests.RequestException as error:
        payload = {
            **base,
            "status": "failed",
            "httpStatus": None,
            "reason": "无法连接 OpenAI API",
            "detail": clean_detail(error),
            "secretConfigured": True,
        }

    write_health(payload)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
