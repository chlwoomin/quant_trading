"""
universe.py
Screening universe provider.

Primary target: KOSPI200.
Fallback: the original 30-stock large-cap universe.
"""

import json
import os
import re
from datetime import datetime, timedelta


CACHE_DIR = os.path.join(os.path.dirname(__file__), "data")
KOSPI200_CACHE = os.path.join(CACHE_DIR, "kospi200_universe.json")


DEFAULT_TICKERS = [
    "005930", "000660", "035420", "005380", "051910",
    "006400", "035720", "028260", "207940", "066570",
    "105560", "055550", "032830", "086790", "003550",
    "018260", "011200", "034020", "096770", "010130",
    "009150", "000270", "012330", "033780", "021240",
    "003490", "047050", "009830", "006800", "010950",
]
DEFAULT_NAMES = [
    "삼성전자", "SK하이닉스", "NAVER", "현대차", "LG화학",
    "삼성SDI", "카카오", "삼성물산", "삼성바이오로직스", "LG전자",
    "KB금융", "신한지주", "삼성생명", "하나금융", "LG",
    "삼성에스디에스", "HMM", "한국전력", "SK이노베이션", "포스코홀딩스",
    "삼성전기", "기아", "현대모비스", "KT&G", "코웨이",
    "대한항공", "NH투자증권", "한화솔루션", "미래에셋증권", "영원무역",
]
DEFAULT_SECTORS = [
    "반도체", "반도체", "인터넷", "자동차", "화학",
    "2차전지", "인터넷", "지주", "바이오", "전자",
    "금융", "금융", "금융", "금융", "지주",
    "IT서비스", "해운", "유틸리티", "에너지", "철강",
    "전자부품", "자동차", "자동차부품", "담배", "생활용품",
    "항공", "금융", "태양광", "금융", "섬유",
]

SECTOR_MAP = {
    "Communication Services": "커뮤니케이션",
    "Constructions": "건설",
    "Consumer Discretionary": "경기소비재",
    "Consumer Staples": "필수소비재",
    "Energy & Chemicals": "에너지화학",
    "Financials": "금융",
    "Health Care": "헬스케어",
    "Heavy Industries": "중공업",
    "Industrials": "산업재",
    "IT": "IT",
    "Steels & Materials": "소재",
    "Utilities": "유틸리티",
}


def default_universe():
    return [
        {"ticker": t, "name": n, "sector": s, "source": "default30"}
        for t, n, s in zip(DEFAULT_TICKERS, DEFAULT_NAMES, DEFAULT_SECTORS)
    ]


def _read_cache(max_age_days=30):
    if not os.path.exists(KOSPI200_CACHE):
        return None
    try:
        with open(KOSPI200_CACHE, encoding="utf-8") as f:
            payload = json.load(f)
        fetched_at = datetime.fromisoformat(payload.get("fetched_at"))
        if datetime.now() - fetched_at > timedelta(days=max_age_days):
            return None
        rows = payload.get("rows") or []
        return rows if len(rows) >= 150 else None
    except Exception:
        return None


def _write_cache(rows, source):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(KOSPI200_CACHE, "w", encoding="utf-8") as f:
        json.dump({
            "fetched_at": datetime.now().isoformat(),
            "source": source,
            "count": len(rows),
            "rows": rows,
        }, f, ensure_ascii=False, indent=2)


def _load_from_pykrx():
    from pykrx import stock
    tickers = stock.get_index_portfolio_deposit_file("1028")
    if len(tickers) < 150:
        raise ValueError("pykrx KOSPI200 구성종목 부족")
    rows = []
    for ticker in tickers:
        try:
            name = stock.get_market_ticker_name(ticker) or ticker
        except Exception:
            name = ticker
        rows.append({"ticker": ticker, "name": name, "sector": "기타", "source": "pykrx"})
    return rows


def _load_from_wikipedia():
    import requests
    from bs4 import BeautifulSoup

    url = "https://en.wikipedia.org/wiki/KOSPI_200"
    html = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15).text
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    seen = set()

    for table in soup.find_all("table"):
        header = " ".join(th.get_text(" ", strip=True) for th in table.find_all("th"))
        if "Symbol" not in header or "GICS Sector" not in header:
            continue
        for tr in table.find_all("tr")[1:]:
            cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
            if len(cells) < 3:
                continue
            name, symbol, sector = cells[0], cells[1], cells[2]
            match = re.search(r"\b(\d{6})\b", symbol)
            if not match:
                continue
            ticker = match.group(1)
            if ticker in seen:
                continue
            seen.add(ticker)
            rows.append({
                "ticker": ticker,
                "name": name,
                "sector": SECTOR_MAP.get(sector, sector or "기타"),
                "source": "wikipedia",
            })

    if len(rows) < 150:
        raise ValueError(f"wikipedia KOSPI200 구성종목 부족: {len(rows)}")
    return rows[:200]


def get_screening_universe(refresh=False):
    """Return KOSPI200 universe, falling back to the original 30 names."""
    if not refresh:
        cached = _read_cache()
        if cached:
            return cached

    for loader in (_load_from_pykrx, _load_from_wikipedia):
        try:
            rows = loader()
            _write_cache(rows, rows[0].get("source", "unknown"))
            return rows
        except Exception:
            pass

    return default_universe()


if __name__ == "__main__":
    rows = get_screening_universe(refresh=True)
    print(f"universe_count={len(rows)} source={rows[0].get('source') if rows else 'N/A'}")
    for row in rows[:20]:
        print(row["ticker"], row["name"], row["sector"])
