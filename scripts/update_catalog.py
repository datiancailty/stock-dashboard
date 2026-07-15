#!/usr/bin/env python3
"""从东方财富公开行情接口生成 A 股模糊搜索目录（不消耗妙想额度）。"""
import json
import time
from pathlib import Path

import requests
from pypinyin import Style, lazy_pinyin
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 stock-dashboard-catalog/1.0"})
SESSION.mount("https://", HTTPAdapter(max_retries=Retry(total=5, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504))))

ROOT = Path(__file__).resolve().parents[1]
API = "https://push2delay.eastmoney.com/api/qt/clist/get"
MARKETS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"


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
    response = SESSION.get(API, params=params, timeout=30)
    response.raise_for_status()
    data = response.json().get("data") or {}
    return data.get("diff") or [], int(data.get("total") or 0)


def main() -> None:
    rows, total = fetch_page(1)
    all_rows = list(rows)
    pages = (total + 99) // 100
    for page in range(2, pages + 1):
        page_rows, _ = fetch_page(page)
        all_rows.extend(page_rows)
        time.sleep(0.04)

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
    output = ROOT / "data/stock-catalog.json"
    output.write_text(json.dumps(catalog, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(json.dumps({"ok": True, "stocks": len(catalog), "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
