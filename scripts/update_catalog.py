#!/usr/bin/env python3
"""从东方财富公开行情接口生成 A 股模糊搜索目录（不消耗妙想额度）。

2026-11 修复：东方财富对连续分页请求加强了限流（约 30 次后持续返回 502）。
原脚本每页仅 sleep 0.04s，60 页连续请求必触发限流。本修复：
  1) 每页间隔提升到 3.0s，把请求速率降到限流阈值以下；
  2) 502/429/503/504 视为限流，退避 60s 后重试（覆盖限流窗口），单页最多 6 次；
  3) 保持「全有或全无」语义：任何一页最终失败仍抛异常退出，不写不完整目录。
"""
import json
import time
from pathlib import Path

import requests
from pypinyin import Style, lazy_pinyin

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 stock-dashboard-catalog/1.0"})

ROOT = Path(__file__).resolve().parents[1]
API = "https://push2delay.eastmoney.com/api/qt/clist/get"
MARKETS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"

# 限流相关参数（依据 2026-10/11 两次失败日志 + 本地实测）
PAGE_INTERVAL = 3.0          # 每页间隔（秒），降低请求频率
RATE_LIMIT_STATUS = (429, 500, 502, 503, 504)   # 限流/网关错误状态码
RETRY_BACKOFF = 60.0         # 命中限流时的退避秒数，覆盖限流窗口
MAX_PAGE_RETRIES = 6         # 单页最多重试次数


def aliases(name: str) -> tuple[str, str]:
    normalized = name.replace(" ", "")
    full = "".join(lazy_pinyin(normalized, style=Style.NORMAL, errors="ignore")).lower()
    initials = "".join(lazy_pinyin(normalized, style=Style.FIRST_LETTER, errors="ignore")).lower()
    return full, initials


def fetch_page(page: int) -> tuple[list[dict], int]:
    params = {
        "pn": page, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2,
        "fid": "f12", "fs": MARKETS, "fields": "f12,f14",
    }
    for attempt in range(MAX_PAGE_RETRIES):
        try:
            response = SESSION.get(API, params=params, timeout=30)
        except requests.RequestException as exc:
            print(f"page {page} transport error ({exc.__class__.__name__}), retrying", flush=True)
            time.sleep(RETRY_BACKOFF)
            continue
        if response.status_code in RATE_LIMIT_STATUS:
            print(f"page {page} rate-limited (HTTP {response.status_code}), retrying", flush=True)
            time.sleep(RETRY_BACKOFF)
            continue
        if response.status_code != 200:
            response.raise_for_status()
        data = response.json().get("data") or {}
        diff = data.get("diff") or []
        total = int(data.get("total") or 0)
        if not diff:
            # 空结果可能是限流伪装或翻页越界；翻页越界只会出现在最后一页之后，
            # 而 main() 严格按 total 计算页数，所以这里空结果一律重试。
            print(f"page {page} empty response, retrying", flush=True)
            time.sleep(RETRY_BACKOFF)
            continue
        return diff, total
    raise RuntimeError(f"page {page} fetch failed after {MAX_PAGE_RETRIES} retries")


def main() -> None:
    rows, total = fetch_page(1)
    all_rows = list(rows)
    pages = (total + 99) // 100
    print(f"total={total} pages={pages}", flush=True)
    for page in range(2, pages + 1):
        time.sleep(PAGE_INTERVAL)
        page_rows, _ = fetch_page(page)
        all_rows.extend(page_rows)

    seen: set[str] = set()
    catalog = []
    for row in all_rows:
        code, name = str(row.get("f12") or "").strip(), str(row.get("f14") or "").strip()
        if not (code.isdigit() and len(code) == 6 and name) or code in seen:
            continue
        seen.add(code)
        full, initials = aliases(name)
        market = "SH" if code.startswith(("5", "6", "9")) else "SZ"
        catalog.append({"code": code, "name": name, "market": market, "pinyin": full, "initials": initials})

    catalog.sort(key=lambda item: item["code"])

    # 完整性校验（fail-closed）：目录规模必须与接口 total 大致一致，防止限流导致的
    # 静默缺页被写出。A 股约 5000+，留出少量非股票条目被过滤的余量。
    if len(catalog) < 5000 or (total and len(catalog) < total - 200):
        raise RuntimeError(f"catalog incomplete: got {len(catalog)}, expected ~{total}")

    output = ROOT / "data/stock-catalog.json"
    output.write_text(json.dumps(catalog, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(json.dumps({"ok": True, "stocks": len(catalog), "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
