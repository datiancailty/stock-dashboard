"""Pure news normalization/fingerprints and disclosed-number estimates.

Extracted without semantic changes from the historical public news writer.
No provider/config/file/network work, and no independent official verification.
"""
from __future__ import annotations
import hashlib
import html
import re


def clean_text(value: object) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip().replace("，", ",")
    # PDF 文本常把 90.09 或 1,234.56 拆成“90. 09”“1, 234. 56”。
    return re.sub(r"(?<=\d)\s*([.,])\s*(?=\d)", r"\1", text)


def money_value(number: str, unit: str) -> float:
    value = float(number.replace(",", ""))
    return value * {"亿元": 1e8, "亿": 1e8, "万元": 1e4, "万": 1e4, "元": 1}.get(unit, 1)


def first_money(text: str, labels: str) -> tuple[float | None, str | None]:
    pattern = rf"(?:{labels})[^。；]{{0,45}}?([0-9][0-9,]*(?:\.[0-9]+)?)\s*(亿元|亿|万元|万|元)"
    match = re.search(pattern, text)
    return (money_value(match.group(1), match.group(2)), match.group(0)) if match else (None, None)


def first_shares(text: str) -> tuple[float | None, str | None]:
    match = re.search(r"(?:总股本|股份总数)[^。；]{0,35}?([0-9][0-9,]*(?:\.[0-9]+)?)\s*(亿股|万股|股)", text)
    if not match:
        return None, None
    value = float(match.group(1).replace(",", "")) * {"亿股": 1e8, "万股": 1e4, "股": 1}[match.group(2)]
    return value, match.group(0)


def extract_estimate(text: str, title: str) -> dict:
    combined = f"{title} {text}"
    status = "信息提示"
    if any(word in title for word in ("利润分配预案", "分红预案")):
        status = "正式预案"
    elif "实施公告" in title:
        status = "已实施"
    elif any(word in combined for word in ("利润分配预案", "分红预案", "尚需提交", "董事会审议")):
        status = "正式预案"
    elif "现金红利发放日" in combined:
        status = "已实施"
    elif any(word in combined for word in ("股东回报规划", "现金分红规划", "分红比例", "用于分红", "分红承诺")):
        status = "政策预估"

    exact_patterns = [
        r"每股(?:派发|分配)?现金红利(?:人民币)?\s*([0-9]+(?:\.[0-9]+)?)\s*元",
        r"每股现金红利(?:人民币)?\s*([0-9]+(?:\.[0-9]+)?)\s*元",
        r"每\s*10\s*股(?:派发|派送|派)?(?:现金红利)?(?:人民币)?\s*([0-9]+(?:\.[0-9]+)?)\s*元",
        r"10\s*派\s*([0-9]+(?:\.[0-9]+)?)\s*元",
    ]
    for index, pattern in enumerate(exact_patterns):
        match = re.search(pattern, combined)
        if match:
            per_share = float(match.group(1)) / (10 if index >= 2 else 1)
            return {
                "estimatedDividendPerShare": round(per_share, 6),
                "calculation": f"公告直接披露：{match.group(0)}" + (f"，{match.group(1)} ÷ 10 = {per_share:g}元/股" if index >= 2 else ""),
                "confidence": "高" if status in ("已实施", "正式预案") else "中",
                "status": status,
                "inputs": {"directPerShare": per_share},
            }

    ratio_match = re.search(r"(?:现金分红比例|分红比例|拿出|用于分红|现金分红)[^。；%]{0,45}?([0-9]+(?:\.[0-9]+)?)\s*%", combined)
    if not ratio_match:
        ratio_match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%[^。；]{0,35}?(?:用于|拿出|用作|作为)[^。；]{0,18}?(?:现金)?分红", combined)
    ratio = float(ratio_match.group(1)) / 100 if ratio_match else None
    distribution_base, base_quote = first_money(combined, r"归属于上市公司股东(?:的)?净利润|归母净利润|预计净利润|营业收入")
    shares, shares_quote = first_shares(combined)
    total_dividend, total_quote = first_money(combined, r"现金分红总额|拟派发现金红利|分红金额")

    if total_dividend and shares:
        per_share = total_dividend / shares
        return {
            "estimatedDividendPerShare": round(per_share, 6),
            "calculation": f"现金分红总额 {total_dividend:g}元 ÷ 总股本 {shares:g}股 = {per_share:g}元/股",
            "confidence": "中",
            "status": status,
            "inputs": {"totalDividend": total_dividend, "shares": shares, "quotes": [total_quote, shares_quote]},
        }
    if ratio and distribution_base and shares:
        per_share = distribution_base * ratio / shares
        return {
            "estimatedDividendPerShare": round(per_share, 6),
            "calculation": f"公告披露基数 {distribution_base:g}元 × 分红比例 {ratio * 100:g}% ÷ 总股本 {shares:g}股 = {per_share:g}元/股",
            "confidence": "中",
            "status": "政策预估" if status == "信息提示" else status,
            "inputs": {"distributionBase": distribution_base, "payoutRatio": ratio, "shares": shares, "quotes": [base_quote, ratio_match.group(0), shares_quote]},
        }
    return {
        "estimatedDividendPerShare": None,
        "calculation": "公告尚未同时披露可计算每股分红所需的明确数字",
        "confidence": "待补充",
        "status": status,
        "inputs": {"payoutRatio": ratio, "distributionBase": distribution_base, "shares": shares},
    }


def identify_stock(item: dict, stocks: list[dict]) -> dict | None:
    haystack = " ".join(clean_text(item.get(key)) for key in ("code", "entityFullName", "title", "content"))
    for stock in stocks:
        if stock["code"] in haystack or stock["name"] in haystack:
            return stock
    return None


def item_id(stock: dict, item: dict) -> str:
    basis = "|".join((stock["code"], clean_text(item.get("title")), str(item.get("date") or ""), str(item.get("jumpUrl") or "")))
    return hashlib.sha256(basis.encode()).hexdigest()[:20]
