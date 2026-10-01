#!/usr/bin/env python3
"""增量抓取自选股分红公告/新闻，去重保存并生成可审计的分红预估。"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parents[1]
API = "https://mkapi2.dfcfs.com/finskillshub/api/claw/news-search"
BJ = ZoneInfo("Asia/Shanghai")
MEMORY_PATH = ROOT / "data/news-memory.json"


from dashboard_news_normalization import (
    clean_text, money_value, first_money, first_shares, extract_estimate, identify_stock, item_id,
)


def query_news(stocks: list[dict], since: str) -> list[dict]:
    names = "、".join(stock["name"] for stock in stocks)
    query = (
        f"{names}自{since}以来，与现金分红、利润分配、分红派息实施、股东回报规划、"
        "分红比例承诺相关的最新公司公告和权威新闻。优先公司公告，保留原始来源链接。"
    )
    response = requests.post(
        API,
        headers={"apikey": os.environ["MX_APIKEY"], "Content-Type": "application/json"},
        json={"query": query},
        timeout=45,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != 0:
        raise RuntimeError(f"妙想资讯API错误: {payload.get('status')} {payload.get('message')}")
    return payload.get("data", {}).get("data", {}).get("llmSearchResponse", {}).get("data", []) or []


def main() -> None:
    if not os.getenv("MX_APIKEY"):
        raise SystemExit("缺少 MX_APIKEY")
    stocks = json.loads((ROOT / "data/stocks.json").read_text())
    if MEMORY_PATH.exists() and MEMORY_PATH.stat().st_size:
        try:
            old = json.loads(MEMORY_PATH.read_text())
        except json.JSONDecodeError:
            old = {"items": []}
    else:
        old = {"items": []}
    known = {item["id"] for item in old.get("items", [])}
    now = datetime.now(BJ)
    since = old.get("lastScanAt", f"{now.year - 1}-01-01")[:10]
    old_codes = set(old.get("trackedStockCodes", []))
    new_stocks = [stock for stock in stocks if stock["code"] not in old_codes]
    if old.get("items"):
        raw_items = query_news(stocks, since)
        # 新加入的股票需要补抓历史公告；旧股票仍只从上次扫描时间增量查询。
        if new_stocks:
            raw_items += query_news(new_stocks, f"{now.year - 1}-01-01")
    else:
        raw_items = query_news(stocks, f"{now.year - 1}-01-01")
    additions = []

    for raw in raw_items:
        stock = identify_stock(raw, stocks)
        if not stock:
            continue
        uid = item_id(stock, raw)
        if uid in known:
            continue
        title = clean_text(raw.get("title"))
        content = clean_text(raw.get("content"))
        estimate = extract_estimate(content, title)
        url = str(raw.get("jumpUrl") or "").strip()
        additions.append({
            "id": uid,
            "code": stock["code"],
            "name": stock["name"],
            "title": title,
            "publishedAt": str(raw.get("date") or ""),
            "type": str(raw.get("informationType") or "NEWS"),
            "source": clean_text(raw.get("source") or raw.get("insName") or "东方财富资讯"),
            "url": url if url.startswith(("http://", "https://")) else "",
            "summary": content[:420] + ("…" if len(content) > 420 else ""),
            "firstSeenAt": now.isoformat(timespec="seconds"),
            **estimate,
        })
        known.add(uid)

    merged = additions + old.get("items", [])
    merged.sort(key=lambda item: (item.get("publishedAt", ""), item.get("firstSeenAt", "")), reverse=True)
    result = {
        "updatedAt": now.isoformat(timespec="seconds"),
        "lastScanAt": now.isoformat(timespec="seconds"),
        "source": "东方财富妙想资讯搜索",
        "strategy": "增量搜索、唯一指纹去重；正式数据与政策预估分开保存；仅在数字充分时计算",
        "trackedStockCodes": [stock["code"] for stock in stocks],
        "items": merged[:1000],
    }
    MEMORY_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"ok": True, "searched": len(raw_items), "new": len(additions), "remembered": len(merged)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
