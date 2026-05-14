"""
data_fetcher.py
yfinance를 이용한 실제 한국 주식 데이터 수집 및 캐싱

수집 내용:
  - 주가 (weekly close): yfinance 배치 다운로드
  - KOSPI 지수: yfinance ^KS11
  - 펀더멘털 (PBR, PER, ROE, 시총): yfinance .info + 분기 재무제표

사용법:
  python data_fetcher.py             # 2년치 데이터 수집
  python data_fetcher.py --years 3   # 3년치
  python data_fetcher.py --refresh   # 캐시 무시하고 재수집
"""

import os
import sys
import json
import warnings
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

warnings.filterwarnings("ignore")

try:
    import yfinance as yf
except ImportError:
    raise SystemExit("yfinance가 설치되지 않았습니다: pip install yfinance")

from universe import DEFAULT_TICKERS, DEFAULT_NAMES, DEFAULT_SECTORS, get_screening_universe

# ── 종목 정의 ─────────────────────────────────────────────────────────────────
TICKERS = DEFAULT_TICKERS
NAMES = DEFAULT_NAMES
SECTORS = DEFAULT_SECTORS

YF_TICKERS = [t + ".KS" for t in TICKERS]
YF_KOSPI   = "^KS11"
CACHE_DIR  = os.path.join(os.path.dirname(__file__), "data")


def _universe_rows():
    return get_screening_universe()


def _universe_parts():
    rows = _universe_rows()
    tickers = [r["ticker"] for r in rows]
    names = [r["name"] for r in rows]
    sectors = [r.get("sector", "기타") for r in rows]
    return rows, tickers, names, sectors, [t + ".KS" for t in tickers]


# ── 가격 데이터 수집 ──────────────────────────────────────────────────────────
def fetch_prices(start: str, end: str) -> tuple[pd.DataFrame, pd.Series]:
    """
    주가와 KOSPI를 주간(금요일 기준)으로 다운로드합니다.
    Returns: (stock_weekly, kospi_weekly)
    """
    print("  주가 다운로드 중...")
    _, _, _, _, yf_tickers = _universe_parts()
    all_tickers = yf_tickers + [YF_KOSPI]
    raw = yf.download(all_tickers, start=start, end=end, progress=False, auto_adjust=True)

    # MultiIndex: (field, ticker) → Close만 추출
    if isinstance(raw.columns, pd.MultiIndex):
        close = raw["Close"]
    else:
        close = raw[["Close"]]

    # 주간 리샘플 (주 마지막 거래일)
    weekly = close.resample("W-FRI").last().ffill()

    kospi_weekly  = weekly[YF_KOSPI].dropna()
    stocks_weekly = weekly[[c for c in yf_tickers if c in weekly.columns]].copy()

    # 데이터 없는 종목 처리: ffill → 남은 NaN은 시장 평균으로 채움
    stocks_weekly = stocks_weekly.ffill().bfill()

    print(f"  기간: {weekly.index[0].date()} ~ {weekly.index[-1].date()} ({len(weekly)}주)")
    missing = stocks_weekly.isna().sum()
    if missing.any():
        print(f"  결측 종목: {missing[missing > 0].to_dict()}")

    return stocks_weekly, kospi_weekly


# ── 펀더멘털 데이터 수집 ──────────────────────────────────────────────────────
def fetch_fundamentals() -> dict:
    """
    yfinance에서 각 종목의 펀더멘털 지표를 수집합니다.
    - 현재 ROE, 시총, Debt/Equity: .info
    - 분기 재무제표: quarterly_balance_sheet + quarterly_income_stmt
      → PBR (Tangible Book Value 기반), GP/A 계산

    Returns: dict[ticker_idx → dict of fundamental values]
    """
    _, tickers, names, _, yf_tickers = _universe_parts()
    print(f"  펀더멘털 수집 중 ({len(tickers)}종목)...")
    result = {}

    for i, (ticker, name, yf_ticker) in enumerate(zip(tickers, names, yf_tickers)):
        try:
            t = yf.Ticker(yf_ticker)
            info = t.info

            # 기본값
            roe        = info.get("returnOnEquity") or 0.08
            mktcap_억  = (info.get("marketCap") or 0) / 1e8
            de_ratio   = (info.get("debtToEquity") or 50) / 100   # % → ratio
            pbr        = info.get("priceToBook")
            per        = info.get("trailingPE") or info.get("forwardPE")

            # 분기 재무제표로 PBR/GPA 보완
            if pbr is None or per is None:
                try:
                    bs  = t.quarterly_balance_sheet
                    inc = t.quarterly_income_stmt
                    shares = info.get("sharesOutstanding", 0)
                    cur_price = info.get("currentPrice") or info.get("regularMarketPrice") or 0

                    # PBR 계산 (Tangible Book Value 사용)
                    if pbr is None and "Tangible Book Value" in bs.index and shares > 0 and cur_price > 0:
                        tbv = bs.loc["Tangible Book Value"].iloc[0]
                        bvps = tbv / shares
                        pbr = cur_price / bvps if bvps > 0 else 1.0

                    # PER 계산 (Net Income 사용)
                    if per is None and "Net Income" in inc.index and shares > 0 and cur_price > 0:
                        ni = inc.loc["Net Income"].iloc[0]
                        eps = ni / shares
                        per = cur_price / eps if eps > 0 else 15.0

                    # GP/A 계산
                    gpa = 0.15  # 기본값
                    if "Gross Profit" in inc.index and "Total Assets" in bs.index:
                        gp = inc.loc["Gross Profit"].iloc[0]
                        ta = bs.loc["Total Assets"].iloc[0]
                        if ta > 0:
                            gpa = float(gp / ta)
                except Exception:
                    gpa = 0.15
            else:
                gpa = 0.15

            result[i] = {
                "roe":        float(np.clip(roe,       0.01, 0.60)),
                "gpa":        float(np.clip(gpa,       0.01, 0.60)),
                "pbr":        float(np.clip(pbr or 1.5, 0.2, 10.0)),
                "per":        float(np.clip(per or 15.0, 3.0, 80.0)),
                "debt_ratio": float(np.clip(de_ratio,  0.05, 5.0)),
                "mktcap_억":  float(max(mktcap_억, 100)),
            }
            print(f"    [{i+1:3d}/{len(tickers)}] {name}: ROE={result[i]['roe']:.2%}  "
                  f"PBR={result[i]['pbr']:.2f}  PER={result[i]['per']:.1f}")

        except Exception as e:
            print(f"    [{i+1:3d}/{len(tickers)}] {name}: 오류 → 기본값 사용 ({e})")
            result[i] = {
                "roe": 0.08, "gpa": 0.15, "pbr": 1.5, "per": 15.0,
                "debt_ratio": 0.5, "mktcap_억": 5000,
            }

    return result


# ── 데이터 구조 빌드 ──────────────────────────────────────────────────────────
def build_backtest_data(stocks_weekly: pd.DataFrame,
                        kospi_weekly: pd.Series,
                        fundamentals: dict,
                        warmup_weeks: int = 52) -> dict:
    """
    Backtest 클래스가 사용하는 data 딕셔너리를 구성합니다.

    fund_history:
      - 펀더멘털은 yfinance 현재 값 기반 (고정)
      - 실제 가치는 매 분기 변하지만, 과거 API 없이는 현재값이 최선
    """
    rows, tickers, names, sectors, yf_tickers = _universe_parts()
    n_stocks = len(tickers)
    n_weeks  = len(stocks_weekly)

    # KOSPI 정렬 (stocks_weekly 인덱스 기준)
    kospi_aligned = kospi_weekly.reindex(stocks_weekly.index).ffill().bfill()

    # 주가 행렬: (n_weeks, n_stocks)
    price_matrix = np.zeros((n_weeks, n_stocks))
    for i, yf_ticker in enumerate(yf_tickers):
        if yf_ticker in stocks_weekly.columns:
            price_matrix[:, i] = stocks_weekly[yf_ticker].values
        else:
            price_matrix[:, i] = np.full(n_weeks, 50_000.0)

    # 펀더멘털: 고정값 (매주 동일)
    fund_arrays = {
        "roe":        np.array([fundamentals.get(i, {}).get("roe",       0.08) for i in range(n_stocks)]),
        "gpa":        np.array([fundamentals.get(i, {}).get("gpa",       0.15) for i in range(n_stocks)]),
        "pbr":        np.array([fundamentals.get(i, {}).get("pbr",       1.5)  for i in range(n_stocks)]),
        "per":        np.array([fundamentals.get(i, {}).get("per",       15.0) for i in range(n_stocks)]),
        "debt_ratio": np.array([fundamentals.get(i, {}).get("debt_ratio",0.5)  for i in range(n_stocks)]),
        "mktcap_억":  np.array([fundamentals.get(i, {}).get("mktcap_억", 5000) for i in range(n_stocks)]),
        "is_finance": np.array([s in ("금융", "보험", "증권", "Financials") for s in sectors]),
    }
    fund_history = [fund_arrays] * n_weeks   # 동일 참조 (고정값이므로 OK)

    return {
        "kospi":        kospi_aligned.values,
        "stock_prices": price_matrix,
        "fund_history": fund_history,
        "dates":        stocks_weekly.index.tolist(),
        "warmup_weeks": warmup_weeks,
        "is_real_data": True,
        "tickers": tickers,
        "names": names,
        "sectors": sectors,
        "universe_name": "kospi200" if n_stocks >= 150 else "default30",
    }


# ── 캐시 저장/로드 ────────────────────────────────────────────────────────────
def save_cache(data: dict, cache_dir: str):
    os.makedirs(cache_dir, exist_ok=True)
    np.save(os.path.join(cache_dir, "kospi.npy"),        data["kospi"])
    np.save(os.path.join(cache_dir, "stock_prices.npy"), data["stock_prices"])

    # fund_history (첫 번째 항목만 저장 - 고정값)
    fund_save = {k: v.tolist() for k, v in data["fund_history"][0].items()}
    with open(os.path.join(cache_dir, "fundamentals.json"), "w", encoding="utf-8") as f:
        json.dump(fund_save, f, ensure_ascii=False, indent=2)

    # 메타 정보
    meta = {
        "dates":        [str(d.date()) for d in data["dates"]],
        "warmup_weeks": data["warmup_weeks"],
        "fetched_at":   datetime.now().isoformat(),
        "is_real_data": True,
        "tickers":      data.get("tickers", []),
        "names":        data.get("names", []),
        "sectors":      data.get("sectors", []),
        "universe_name": data.get("universe_name"),
    }
    with open(os.path.join(cache_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"  캐시 저장 완료: {cache_dir}/")


def load_cache(cache_dir: str):
    meta_path = os.path.join(cache_dir, "meta.json")
    if not os.path.exists(meta_path):
        return None
    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        if meta.get("universe_name") != "kospi200":
            print("  기존 캐시가 KOSPI200 기준이 아니어서 재수집합니다.")
            return None
        kospi        = np.load(os.path.join(cache_dir, "kospi.npy"))
        stock_prices = np.load(os.path.join(cache_dir, "stock_prices.npy"))
        with open(os.path.join(cache_dir, "fundamentals.json"), encoding="utf-8") as f:
            fund_save = json.load(f)

        fund_arrays  = {k: np.array(v) for k, v in fund_save.items()}
        n_weeks      = len(kospi)
        fund_history = [fund_arrays] * n_weeks

        print(f"  캐시 로드: {meta['dates'][0]} ~ {meta['dates'][-1]} ({n_weeks}주)")
        print(f"  수집 시각: {meta['fetched_at'][:16]}")
        return {
            "kospi":        kospi,
            "stock_prices": stock_prices,
            "fund_history": fund_history,
            "dates":        meta["dates"],
            "warmup_weeks": meta["warmup_weeks"],
            "is_real_data": True,
            "tickers":      meta.get("tickers", []),
            "names":        meta.get("names", []),
            "sectors":      meta.get("sectors", []),
            "universe_name": meta.get("universe_name"),
        }
    except Exception as e:
        print(f"  캐시 로드 실패: {e}")
        return None


# ── 메인 ─────────────────────────────────────────────────────────────────────
def load_or_fetch(years: int = 2, refresh: bool = False) -> dict:
    """
    캐시가 있으면 로드, 없거나 refresh=True이면 새로 수집합니다.
    """
    if not refresh:
        cached = load_cache(CACHE_DIR)
        if cached is not None:
            return cached

    print(f"\n{'='*52}")
    print(f"  실제 데이터 수집 시작 (yfinance, {years}년)")
    print(f"{'='*52}")

    warmup_weeks = 52
    total_years_needed = years + (warmup_weeks // 52) + 1

    end_date   = datetime.today()
    start_date = end_date - timedelta(days=365 * total_years_needed + 30)

    stocks_weekly, kospi_weekly = fetch_prices(
        start_date.strftime("%Y-%m-%d"),
        end_date.strftime("%Y-%m-%d"),
    )
    fundamentals = fetch_fundamentals()

    data = build_backtest_data(stocks_weekly, kospi_weekly, fundamentals, warmup_weeks)
    save_cache(data, CACHE_DIR)

    print(f"\n  수집 완료: {len(data['dates'])}주 데이터")
    return data


if __name__ == "__main__":
    refresh = "--refresh" in sys.argv
    years   = 2
    if "--years" in sys.argv:
        idx   = sys.argv.index("--years")
        years = int(sys.argv[idx + 1])

    data = load_or_fetch(years=years, refresh=refresh)
    print(f"\n  백테스트 가능 주수: {len(data['dates']) - data['warmup_weeks']}주")
