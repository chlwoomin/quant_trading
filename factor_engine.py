"""
factor_engine.py
멀티팩터 스크리닝 엔진 (GP/A 퀄리티 + 저PBR+고ROE 가치 + 12M-1M 모멘텀)
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json
import os

try:
    import FinanceDataReader as fdr
    FDR_AVAILABLE = True
except ImportError:
    FDR_AVAILABLE = False

from strategy_manager import get_config

# ── 기본 설정 (strategy_config.json이 없을 때 폴백) ──────────────────────────
FACTOR_WEIGHTS = {
    "quality":  0.35,
    "value":    0.35,
    "momentum": 0.30,
}
FILTERS = {
    "min_market_cap_억": 500,
    "max_debt_ratio":    2.0,
    "min_roe":           0.05,
    "exclude_finance":   True,
}
TOP_N = 25


# ── 샘플 데이터 (실제 환경에서는 pykrx/FDR로 대체) ───────────────────────────
def get_sample_universe() -> pd.DataFrame:
    """
    실제 환경: pykrx.stock.get_market_fundamental() 로 대체
    여기서는 KOSPI 대형주 샘플 30종목을 시뮬레이션합니다.
    """
    np.random.seed(42)
    tickers = [
        "005930", "000660", "035420", "005380", "051910",
        "006400", "035720", "028260", "207940", "066570",
        "105560", "055550", "032830", "086790", "003550",
        "018260", "011200", "034020", "096770", "010130",
        "009150", "000270", "012330", "033780", "021240",
        "003490", "047050", "009830", "006800", "010950"
    ]
    names = [
        "삼성전자", "SK하이닉스", "NAVER", "현대차", "LG화학",
        "삼성SDI", "카카오", "삼성물산", "삼성바이오로직스", "LG전자",
        "KB금융", "신한지주", "삼성생명", "하나금융", "LG",
        "삼성에스디에스", "HMM", "한국전력", "SK이노베이션", "포스코홀딩스",
        "삼성전기", "기아", "현대모비스", "KT&G", "코웨이",
        "대한항공", "NH투자증권", "한화솔루션", "미래에셋증권", "영원무역"
    ]
    sectors = [
        "반도체", "반도체", "인터넷", "자동차", "화학",
        "2차전지", "인터넷", "지주", "바이오", "전자",
        "금융", "금융", "금융", "금융", "지주",
        "IT서비스", "해운", "유틸리티", "에너지", "철강",
        "전자부품", "자동차", "자동차부품", "담배", "생활용품",
        "항공", "금융", "태양광", "금융", "섬유"
    ]

    n = len(tickers)
    df = pd.DataFrame({
        "ticker":       tickers,
        "name":         names,
        "sector":       sectors,
        "market_cap_억": np.random.randint(3000, 300000, n),
        "pbr":          np.round(np.random.uniform(0.4, 3.5, n), 2),
        "per":          np.round(np.random.uniform(5, 40, n),  2),
        "roe":          np.round(np.random.uniform(0.02, 0.25, n), 3),
        "gpa":          np.round(np.random.uniform(0.05, 0.45, n), 3),
        "debt_ratio":   np.round(np.random.uniform(0.3, 3.5, n),  2),
        "ret_12m":      np.round(np.random.uniform(-0.30, 0.60, n), 3),
        "ret_1m":       np.round(np.random.uniform(-0.10, 0.10, n), 3),
    })

    # 모멘텀 = 12M - 1M (단기 역전 효과 제거)
    df["momentum"] = df["ret_12m"] - df["ret_1m"]
    return df


# ── 필터링 ────────────────────────────────────────────────────────────────────
def apply_filters(df: pd.DataFrame, config: dict = None) -> pd.DataFrame:
    f = (config or get_config())["filters"]
    original = len(df)
    df = df[df["market_cap_억"] >= f["min_market_cap_억"]]
    df = df[df["debt_ratio"]    <= f["max_debt_ratio"]]
    df = df[df["roe"]           >= f["min_roe"]]
    if f["exclude_finance"]:
        df = df[~df["sector"].isin(["금융", "보험", "증권"])]
    print(f"  필터링: {original}종목 → {len(df)}종목")
    return df.copy()


# ── Z-스코어 계산 ─────────────────────────────────────────────────────────────
def zscore(series: pd.Series) -> pd.Series:
    mu, sigma = series.mean(), series.std()
    if sigma == 0:
        return pd.Series(0, index=series.index)
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
    cfg      = config or get_config()
    top_n    = cfg["portfolio"]["top_n"]

    if date_str is None:
        date_str = datetime.today().strftime("%Y-%m-%d")

    print(f"\n{'='*50}")
    print(f"  멀티팩터 스크리닝 실행: {date_str}  (전략 v{cfg.get('version', '?')})")
    print(f"{'='*50}")

    universe = get_sample_universe()
    print(f"  유니버스: {len(universe)}종목")

    filtered = apply_filters(universe, cfg)
    scored   = compute_factor_scores(filtered, cfg)
    ranked   = scored.sort_values("total_score", ascending=False)
    selected = ranked.head(top_n)

    result = {
        "date":           date_str,
        "universe_count": len(universe),
        "filtered_count": len(filtered),
        "selected": selected[[
            "ticker", "name", "sector",
            "total_score", "z_quality", "z_value", "z_momentum",
            "pbr", "per", "roe", "gpa", "momentum", "market_cap_억"
        ]].round(3).to_dict("records"),
        "factor_weights": cfg["factor_weights"],
        "strategy_version": cfg.get("version"),
    }

    print(f"\n  TOP {top_n} 선정 완료")
    print(f"  {'종목명':<16} {'점수':>6} {'퀄리티':>7} {'가치':>6} {'모멘텀':>7}")
    print(f"  {'-'*48}")
    for r in result["selected"][:10]:
        print(f"  {r['name']:<16} {r['total_score']:>6.3f} "
              f"{r['z_quality']:>7.3f} {r['z_value']:>6.3f} {r['z_momentum']:>7.3f}")
    if top_n > 10:
        print(f"  ... 외 {top_n-10}종목")

    return result
