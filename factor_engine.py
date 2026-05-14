"""
factor_engine.py
멀티팩터 스크리닝 엔진 (GP/A 퀄리티 + 저PBR+고ROE 가치 + 12M-1M 모멘텀)

데이터 소스:
  - 펀더멘탈 (PBR, PER, ROE, 시총, 부채비율): yfinance .info
  - GP/A: yfinance quarterly 재무제표
  - 모멘텀: yfinance 52주 주가 (12M-1M)
  - 캐시: data/universe_YYYY-MM-DD.json (당일 1회 수집)
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json
import os
import warnings

warnings.filterwarnings("ignore")

from strategy_manager import get_config
from universe import DEFAULT_TICKERS, DEFAULT_NAMES, DEFAULT_SECTORS, get_screening_universe

CACHE_DIR = os.path.join(os.path.dirname(__file__), "data")

TICKERS = DEFAULT_TICKERS
NAMES = DEFAULT_NAMES
SECTORS = DEFAULT_SECTORS


# ── 실제 유니버스 수집 ────────────────────────────────────────────────────────
def get_real_universe(date_str: str = None) -> pd.DataFrame:
    """
    yfinance로 실제 펀더멘탈 + 모멘텀 데이터를 수집합니다.
    당일 캐시가 있으면 바로 반환합니다.

    수집 항목:
      PBR, PER, ROE       — yfinance .info
      GP/A                — 분기 재무제표 (Gross Profit / Total Assets)
      부채비율             — Debt/Equity × 0.01
      시총(억)             — marketCap / 1e8
      모멘텀(12M-1M)       — 52주 주가 기반
    """
    today = date_str or datetime.today().strftime("%Y-%m-%d")
    base_universe = get_screening_universe()
    universe_name = "kospi200" if len(base_universe) >= 150 else "default30"
    cache_path = os.path.join(CACHE_DIR, f"universe_{universe_name}_{today}.json")
    os.makedirs(CACHE_DIR, exist_ok=True)

    # 당일 캐시 로드
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            rows = json.load(f)
        print(f"  유니버스 캐시 로드: {today} ({len(rows)}종목)")
        return pd.DataFrame(rows)

    try:
        import yfinance as yf
    except ImportError:
        print("  ⚠ yfinance 미설치 — 기본값 사용")
        return _fallback_universe()

    tickers = [row["ticker"] for row in base_universe]
    names = [row["name"] for row in base_universe]
    sectors = [row.get("sector", "기타") for row in base_universe]

    print(f"  yfinance 데이터 수집 중 ({len(tickers)}종목, {universe_name})...")

    # ── 주가 일괄 다운로드 (모멘텀 계산용) ──────────────────────────────────
    yf_tickers = [t + ".KS" for t in tickers]
    end_dt  = datetime.today()
    start_dt = end_dt - timedelta(days=380)   # 52주 + 여유

    try:
        raw = yf.download(
            yf_tickers, start=start_dt.strftime("%Y-%m-%d"),
            end=end_dt.strftime("%Y-%m-%d"),
            progress=False, auto_adjust=True,
        )
        if isinstance(raw.columns, pd.MultiIndex):
            prices = raw["Close"]
        else:
            prices = raw[["Close"]]
        prices = prices.ffill().bfill()
    except Exception as e:
        print(f"  주가 다운로드 실패: {e}")
        prices = pd.DataFrame()

    def _momentum(yf_ticker):
        if prices.empty or yf_ticker not in prices.columns:
            return 0.0, 0.0, 0.0
        col = prices[yf_ticker].dropna()
        if len(col) < 5:
            return 0.0, 0.0, 0.0
        cur   = col.iloc[-1]
        w52   = col.iloc[-min(252, len(col))]
        w4    = col.iloc[-min(20,  len(col))]
        r12m  = cur / w52 - 1 if w52 > 0 else 0.0
        r1m   = cur / w4  - 1 if w4  > 0 else 0.0
        return round(r12m, 4), round(r1m, 4), round(r12m - r1m, 4)

    # ── 종목별 펀더멘탈 수집 ─────────────────────────────────────────────────
    rows = []
    for idx, (ticker, name, sector, yf_ticker) in enumerate(zip(tickers, names, sectors, yf_tickers), 1):
        r12m, r1m, mom = _momentum(yf_ticker)

        try:
            t    = yf.Ticker(yf_ticker)
            info = t.info

            pbr        = info.get("priceToBook")
            # priceToBook이 None이면 시총/Tangible Book Value로 직접 계산
            if not pbr:
                try:
                    mktcap = info.get("marketCap") or 0
                    bs_tmp = t.quarterly_balance_sheet
                    if "Tangible Book Value" in bs_tmp.index and mktcap > 0:
                        tbv = float(bs_tmp.loc["Tangible Book Value"].iloc[0])
                        if tbv > 0:
                            pbr = mktcap / tbv
                except Exception:
                    pass
            per        = info.get("trailingPE") or info.get("forwardPE")
            roe        = info.get("returnOnEquity")
            mktcap_억  = (info.get("marketCap") or 0) / 1e8
            de         = info.get("debtToEquity")
            debt_ratio = de / 100 if de else 0.5   # % → 배수

            # GP/A: 분기 재무제표
            gpa = 0.15
            try:
                inc = t.quarterly_income_stmt
                bs  = t.quarterly_balance_sheet
                if "Gross Profit" in inc.index and "Total Assets" in bs.index:
                    gp = inc.loc["Gross Profit"].iloc[0]
                    ta = bs.loc["Total Assets"].iloc[0]
                    if ta and ta > 0:
                        gpa = float(np.clip(gp / ta, 0.0, 1.0))
            except Exception:
                pass

        except Exception:
            pbr = per = roe = mktcap_억 = debt_ratio = None
            gpa = 0.15

        rows.append({
            "ticker":       ticker,
            "name":         name,
            "sector":       sector,
            "market_cap_억": float(np.clip(mktcap_억 or 100, 0, 1e7)),
            "pbr":          float(np.clip(pbr  or 1.5,  0.1, 20.0)),
            "per":          float(np.clip(per  or 15.0, 1.0, 200.0)),
            "roe":          float(np.clip(roe  or 0.05, -1.0, 3.0)),
            "gpa":          float(gpa),
            "debt_ratio":   float(np.clip(debt_ratio, 0.0, 10.0)),
            "ret_12m":      r12m,
            "ret_1m":       r1m,
            "momentum":     mom,
        })
        print(f"    [{idx:3d}/{len(tickers)}] {name:<18} PBR={rows[-1]['pbr']:.2f}  "
              f"PER={rows[-1]['per']:.1f}  ROE={rows[-1]['roe']:.1%}  MOM={mom:+.1%}")

    # 캐시 저장
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f"  캐시 저장: {cache_path}")

    return pd.DataFrame(rows)


def _fallback_universe() -> pd.DataFrame:
    """yfinance 완전 실패 시 고정 기본값으로 DataFrame 반환"""
    rows = [{
        "ticker": t, "name": n, "sector": s,
        "market_cap_억": 5000, "pbr": 1.5, "per": 15.0,
        "roe": 0.08, "gpa": 0.15, "debt_ratio": 0.5,
        "ret_12m": 0.0, "ret_1m": 0.0, "momentum": 0.0,
    } for t, n, s in zip(TICKERS, NAMES, SECTORS)]
    return pd.DataFrame(rows)


# ── 필터링 ────────────────────────────────────────────────────────────────────
def apply_filters(df: pd.DataFrame, config: dict = None) -> pd.DataFrame:
    f = (config or get_config())["filters"]
    original = len(df)
    df = df[df["market_cap_억"] >= f["min_market_cap_억"]]
    df = df[df["debt_ratio"]    <= f["max_debt_ratio"]]
    df = df[df["roe"]           >= f["min_roe"]]
    if f.get("exclude_finance"):
        finance_terms = ["금융", "보험", "증권", "Financials"]
        df = df[~df["sector"].astype(str).isin(finance_terms)]
    print(f"  필터링: {original}종목 → {len(df)}종목")
    return df.copy()


# ── Z-스코어 ─────────────────────────────────────────────────────────────────
def zscore(series: pd.Series) -> pd.Series:
    mu, sigma = series.mean(), series.std()
    if sigma == 0:
        return pd.Series(0.0, index=series.index)
    return (series - mu) / sigma


def compute_factor_scores(df: pd.DataFrame, config: dict = None) -> pd.DataFrame:
    w = (config or get_config())["factor_weights"]

    df["z_gpa"]     = zscore(df["gpa"])
    df["z_roe"]     = zscore(df["roe"])
    df["z_quality"] = (df["z_gpa"] + df["z_roe"]) / 2

    df["z_pbr"]   = zscore(-df["pbr"])
    df["z_per"]   = zscore(-df["per"])
    df["z_value"] = (df["z_pbr"] + df["z_per"]) / 2

    df["z_momentum"] = zscore(df["momentum"])

    df["total_score"] = (
        w["quality"]  * df["z_quality"]  +
        w["value"]    * df["z_value"]    +
        w["momentum"] * df["z_momentum"]
    )
    return df


# ── 메인 스크리닝 ─────────────────────────────────────────────────────────────
def run_screening(date_str: str = None, config: dict = None) -> dict:
    cfg   = config or get_config()
    top_n = cfg["portfolio"]["top_n"]

    if date_str is None:
        date_str = datetime.today().strftime("%Y-%m-%d")

    print(f"\n{'='*52}")
    print(f"  멀티팩터 스크리닝: {date_str}  (전략 v{cfg.get('version','?')})")
    print(f"{'='*52}")

    universe = get_real_universe(date_str)
    print(f"  유니버스: {len(universe)}종목")

    filtered = apply_filters(universe, cfg)
    scored   = compute_factor_scores(filtered, cfg)
    ranked   = scored.sort_values("total_score", ascending=False)
    selected = ranked.head(top_n)

    result = {
        "date":             date_str,
        "universe_count":   len(universe),
        "filtered_count":   len(filtered),
        "selected": selected[[
            "ticker", "name", "sector",
            "total_score", "z_quality", "z_value", "z_momentum",
            "pbr", "per", "roe", "gpa", "momentum", "market_cap_억",
        ]].round(3).to_dict("records"),
        "factor_weights":   cfg["factor_weights"],
        "strategy_version": cfg.get("version"),
    }

    n_selected = len(result["selected"])
    print(f"\n  TOP {n_selected} 선정 완료 (요청 {top_n}, 필터 후 {len(filtered)}종목)")
    print(f"  {'종목명':<16} {'점수':>6} {'퀄리티':>7} {'가치':>6} {'모멘텀':>7} "
          f"{'PBR':>5} {'PER':>6} {'ROE':>6}")
    print(f"  {'-'*60}")
    for r in result["selected"][:10]:
        print(f"  {r['name']:<16} {r['total_score']:>6.3f} "
              f"{r['z_quality']:>7.3f} {r['z_value']:>6.3f} {r['z_momentum']:>7.3f} "
              f"{r['pbr']:>5.2f} {r['per']:>6.1f} {r['roe']:>5.1%}")
    if n_selected > 10:
        print(f"  ... 외 {n_selected-10}종목")

    return result


if __name__ == "__main__":
    result = run_screening()
