#!/usr/bin/env python3
"""从沪深北三大交易所官方接口生成 A 股模糊搜索目录（不消耗妙想/同花顺额度）。

原脚本使用东方财富公开接口 push2delay.eastmoney.com，该接口自 2026-10 起对连续
分页请求加强限流（约 5 个请求即封 IP），导致 2026-10/11 连续两个月月度更新失败。
本脚本改用三大交易所官方列表接口，免费、稳定、无分页限流：

  - 上交所主板/科创板：query.sse.com.cn（HTTPS JSON）
  - 深交所主板/创业板：szse.cn（xlsx，openpyxl 解析）
  - 北交所：bse.cn（POST JSON，需 Session 保持 cookie）

输出结构、市场判断、拼音算法、排序均与原脚本保持一致，仅更换数据源。
"""
import json
import time
from io import BytesIO
from pathlib import Path

import requests
from pypinyin import Style, lazy_pinyin

SESSION = requests.Session()

ROOT = Path(__file__).resolve().parents[1]

UA_CHROME81 = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/81.0.4044.138 Safari/537.36")
UA_CHROME110 = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36")

# 完整性校验：A 股当前上市总数约 5500+，留出少量新上市/退市波动的余量。
MIN_CATALOG = 5000


def aliases(name: str) -> tuple[str, str]:
    normalized = name.replace(" ", "")
    full = "".join(lazy_pinyin(normalized, style=Style.NORMAL, errors="ignore")).lower()
    initials = "".join(lazy_pinyin(normalized, style=Style.FIRST_LETTER, errors="ignore")).lower()
    return full, initials


def fetch_sse(stock_type: str) -> list[tuple[str, str]]:
    """上交所：stock_type '1'=主板A股，'8'=科创板。返回 [(code, name)]。"""
    url = "https://query.sse.com.cn/sseQuery/commonQuery.do"
    headers = {
        "Host": "query.sse.com.cn",
        "Pragma": "no-cache",
        "Referer": "https://www.sse.com.cn/assortment/stock/list/share/",
        "User-Agent": UA_CHROME81,
    }
    params = {
        "STOCK_TYPE": stock_type,
        "REG_PROVINCE": "",
        "CSRC_CODE": "",
        "STOCK_CODE": "",
        "sqlId": "COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L",
        "COMPANY_STATUS": "2,4,5,7,8",
        "type": "inParams",
        "isPagination": "true",
        "pageHelp.cacheSize": "1",
        "pageHelp.beginPage": "1",
        "pageHelp.pageSize": "10000",
        "pageHelp.pageNo": "1",
        "pageHelp.endPage": "1",
    }
    for attempt in range(3):
        try:
            response = SESSION.get(url, params=params, headers=headers, timeout=60)
            response.raise_for_status()
            data = response.json()
            rows = data.get("result") or []
            return [
                (str(row.get("A_STOCK_CODE") or "").strip(), str(row.get("SEC_NAME_CN") or "").strip())
                for row in rows if isinstance(row, dict)
            ]
        except (requests.RequestException, ValueError) as exc:
            if attempt == 2:
                raise RuntimeError(f"sse({stock_type}) fetch failed: {exc}") from exc
            time.sleep(5)
    raise RuntimeError(f"sse({stock_type}) fetch failed")


def fetch_szse() -> list[tuple[str, str]]:
    """深交所：返回 xlsx，A股代码/A股简称 两列。"""
    from openpyxl import load_workbook
    url = "https://www.szse.cn/api/report/ShowReport"
    headers = {
        "User-Agent": UA_CHROME81,
        "Referer": "https://www.szse.cn/market/product/stock/list/index.html",
    }
    params = {"SHOWTYPE": "xlsx", "CATALOGID": "1110", "TABKEY": "tab1", "random": "0.6935816432433362"}
    for attempt in range(3):
        try:
            response = SESSION.get(url, params=params, headers=headers, timeout=60)
            response.raise_for_status()
            wb = load_workbook(BytesIO(response.content))
            ws = wb.active
            result: list[tuple[str, str]] = []
            header_seen = False
            for row in ws.iter_rows(values_only=True):
                if not header_seen:
                    if row and "A股代码" in row and "A股简称" in row:
                        code_idx = row.index("A股代码")
                        name_idx = row.index("A股简称")
                        header_seen = True
                    continue
                if len(row) <= max(code_idx, name_idx):
                    continue
                code = str(row[code_idx] or "").strip()
                name = str(row[name_idx] or "").strip()
                if code and name:
                    result.append((code, name))
            if result:
                return result
            raise RuntimeError("szse empty")
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            if attempt == 2:
                raise RuntimeError(f"szse fetch failed: {exc}") from exc
            time.sleep(5)
    raise RuntimeError("szse fetch failed")


def fetch_bse() -> list[tuple[str, str]]:
    """北交所：POST 分页，返回 null([{...}])，字段 xxzqdm/xxzqjc。"""
    url = "https://www.bse.cn/nqxxController/nqxxCnzq.do"
    headers = {
        "User-Agent": UA_CHROME110,
        "Referer": "https://www.bse.cn/nq/listedcompany.html",
    }
    payload = {
        "page": "0", "typejb": "T", "xxfcbj[]": "2", "xxzqdm": "",
        "sortfield": "xxzqdm", "sorttype": "asc",
    }

    def parse_page(text: str) -> list[dict]:
        start = text.find("[")
        if start < 0:
            raise ValueError("bse response has no JSON array")
        data = json.loads(text[start:-1])
        return data[0].get("content") or []

    result: list[tuple[str, str]] = []
    total_pages = None
    for attempt in range(3):
        try:
            response = SESSION.post(url, data=payload, headers=headers, timeout=60)
            response.raise_for_status()
            first = json.loads(response.text[response.text.find("["):-1])
            total_pages = int(first[0].get("totalPages") or 1)
            result.extend(
                (str(row.get("xxzqdm") or "").strip(), str(row.get("xxzqjc") or "").strip())
                for row in parse_page(response.text)
            )
            break
        except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
            if attempt == 2:
                raise RuntimeError(f"bse fetch failed: {exc}") from exc
            time.sleep(5)

    for page in range(1, total_pages):
        payload["page"] = str(page)
        for attempt in range(3):
            try:
                response = SESSION.post(url, data=payload, headers=headers, timeout=60)
                response.raise_for_status()
                result.extend(
                    (str(row.get("xxzqdm") or "").strip(), str(row.get("xxzqjc") or "").strip())
                    for row in parse_page(response.text)
                )
                break
            except (requests.RequestException, ValueError) as exc:
                if attempt == 2:
                    raise RuntimeError(f"bse page {page} failed: {exc}") from exc
                time.sleep(5)
        time.sleep(0.2)
    return result


def main() -> None:
    pairs: list[tuple[str, str]] = []
    pairs.extend(fetch_sse("1"))    # 上交所主板
    pairs.extend(fetch_sse("8"))    # 上交所科创板
    pairs.extend(fetch_szse())      # 深交所主板+创业板
    pairs.extend(fetch_bse())       # 北交所

    seen: set[str] = set()
    catalog = []
    for code, name in pairs:
        code = code.strip()
        name = name.strip()
        if not (code.isdigit() and len(code) == 6 and name) or code in seen:
            continue
        seen.add(code)
        full, initials = aliases(name)
        market = "SH" if code.startswith(("5", "6", "9")) else "SZ"
        catalog.append({"code": code, "name": name, "market": market, "pinyin": full, "initials": initials})

    catalog.sort(key=lambda item: item["code"])

    # 完整性校验（fail-closed）：防止某个交易所静默失败导致目录残缺。
    if len(catalog) < MIN_CATALOG:
        raise RuntimeError(f"catalog incomplete: got {len(catalog)}, expected >= {MIN_CATALOG}")

    output = ROOT / "data/stock-catalog.json"
    output.write_text(json.dumps(catalog, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(json.dumps({"ok": True, "stocks": len(catalog), "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
