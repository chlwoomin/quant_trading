"""
backtest.py
멀티팩터 전략 백테스트 엔진

현재 전략 (factor_engine.py 기준) 을 시계열 데이터로 재현합니다.
- 주간 리밸런싱
- 퀄리티 35% + 밸류 35% + 모멘텀 30%
- KOSPI 200일 이동평균 리스크 오버레이
- 벤치마크(KOSPI buy & hold) 대비 성과 비교

사용법:
  python backtest.py               # 2년 시뮬레이션 백테스트
  python backtest.py --real        # 실제 데이터 백테스트 (yfinance)
  python backtest.py --years 3     # 3년 백테스트
  python backtest.py --seed 123    # 랜덤 시드 지정 (시뮬레이션 전용)
  python backtest.py --real --refresh  # 데이터 재수집 후 백테스트
"""

import sys
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

from strategy_manager import get_config

# ── 고정 상수 ─────────────────────────────────────────────────────────────────
INITIAL_CAPITAL  = 10_000_000
FEE_RATE         = 0.00015
TAX_RATE         = 0.0020
RISK_FREE_WEEKLY = 0.03 / 52
WARMUP_WEEKS     = 52   # MA 계산용 사전 데이터 (1년)

TICKERS = [
    "005930", "000660", "035420", "005380", "051910",
    "006400", "035720", "028260", "207940", "066570",
    "105560", "055550", "032830", "086790", "003550",
    "018260", "011200", "034020", "096770", "010130",
    "009150", "000270", "012330", "033780", "021240",
    "003490", "047050", "009830", "006800", "010950",
]
NAMES = [
    "삼성전자", "SK하이닉스", "NAVER", "현대차", "LG화학",
    "삼성SDI", "카카오", "삼성물산", "삼성바이오로직스", "LG전자",
    "KB금융", "신한지주", "삼성생명", "하나금융", "LG",
    "삼성에스디에스", "HMM", "한국전력", "SK이노베이션", "포스코홀딩스",
    "삼성전기", "기아", "현대모비스", "KT&G", "코웨이",
    "대한항공", "NH투자증권", "한화솔루션", "미래에셋증권", "영원무역",
]
SECTORS = [
    "반도체", "반도체", "인터넷", "자동차", "화학",
    "2차전지", "인터넷", "지주", "바이오", "전자",
    "금융", "금융", "금융", "금융", "지주",
    "IT서비스", "해운", "유틸리티", "에너지", "철강",
    "전자부품", "자동차", "자동차부품", "담배", "생활용품",
    "항공", "금융", "태양광", "금융", "섬유",
]
IS_FINANCE = [s in ("금융", "보험", "증권") for s in SECTORS]
N_STOCKS = len(TICKERS)


# ── 시뮬레이션 데이터 생성 ────────────────────────────────────────────────────
def generate_data(total_weeks: int, seed: int = 42) -> dict:
    """
    백테스트용 시계열 데이터 생성.
    - 주가: GBM (시장 베타 + 개별 변동성)
    - 펀더멘털: 분기마다 소폭 갱신
    - KOSPI: 별도 GBM
    """
    rng = np.random.default_rng(seed)

    # 개별 종목 파라미터
    betas       = rng.uniform(0.6, 1.4, N_STOCKS)   # 시장 민감도
    idio_vol    = rng.uniform(0.01, 0.03, N_STOCKS)  # 개별 주간 변동성
    init_price  = rng.integers(5_000, 100_000, N_STOCKS).astype(float)

    # KOSPI 경로 (약한 상승 추세)
    market_weekly_mu    = 0.001
    market_weekly_sigma = 0.015
    market_shocks = rng.normal(market_weekly_mu, market_weekly_sigma, total_weeks)
    kospi_prices  = np.empty(total_weeks)
    kospi_prices[0] = 2500.0
    for t in range(1, total_weeks):
        kospi_prices[t] = kospi_prices[t - 1] * (1 + market_shocks[t])

    # 개별 주가 경로
    stock_prices = np.empty((total_weeks, N_STOCKS))
    stock_prices[0] = init_price
    for t in range(1, total_weeks):
        idio_shocks = rng.normal(0, idio_vol, N_STOCKS)
        ret = betas * market_shocks[t] + idio_shocks
        stock_prices[t] = stock_prices[t - 1] * (1 + ret)

    # 펀더멘털 (초기값)
    pbr       = rng.uniform(0.4, 3.5, N_STOCKS)
    per       = rng.uniform(5.0, 40.0, N_STOCKS)
    roe       = rng.uniform(0.02, 0.25, N_STOCKS)
    gpa       = rng.uniform(0.05, 0.45, N_STOCKS)
    debt_ratio = rng.uniform(0.3, 3.5, N_STOCKS)
    mktcap_억  = rng.integers(3_000, 300_000, N_STOCKS).astype(float)

    # 분기마다 펀더멘털 소폭 변동
    fund_history = []
    cur = dict(pbr=pbr.copy(), per=per.copy(), roe=roe.copy(),
               gpa=gpa.copy(), debt_ratio=debt_ratio.copy(), mktcap_억=mktcap_억.copy())
    for t in range(total_weeks):
        if t > 0 and t % 13 == 0:  # 분기 (13주)
            cur["pbr"]        = np.clip(cur["pbr"]        * rng.uniform(0.9, 1.1, N_STOCKS), 0.2, 8.0)
            cur["per"]        = np.clip(cur["per"]        * rng.uniform(0.9, 1.1, N_STOCKS), 3.0, 80.0)
            cur["roe"]        = np.clip(cur["roe"]        * rng.uniform(0.9, 1.1, N_STOCKS), 0.01, 0.50)
            cur["gpa"]        = np.clip(cur["gpa"]        * rng.uniform(0.9, 1.1, N_STOCKS), 0.02, 0.60)
            cur["debt_ratio"] = np.clip(cur["debt_ratio"] * rng.uniform(0.9, 1.1, N_STOCKS), 0.1, 5.0)
        fund_history.append({k: v.copy() for k, v in cur.items()})

    return {
        "kospi":        kospi_prices,
        "stock_prices": stock_prices,   # shape: (total_weeks, N_STOCKS)
        "fund_history": fund_history,   # list[dict], len=total_weeks
    }


# ── 팩터 스코어링 ─────────────────────────────────────────────────────────────
def zscore(arr: np.ndarray) -> np.ndarray:
    mu, sigma = arr.mean(), arr.std()
    if sigma < 1e-9:
        return np.zeros_like(arr)
    return (arr - mu) / sigma


def screen_stocks(prices_window: np.ndarray, fund: dict, config: dict = None) -> list:
    """
    prices_window: shape (total_weeks_so_far, N_STOCKS)
    반환: 선정된 종목 인덱스 리스트
    """
    cfg     = config or get_config()
    w       = cfg["factor_weights"]
    f       = cfg["filters"]
    top_n   = cfg["portfolio"]["top_n"]
    n       = len(prices_window)

    weeks_12m = min(52, n - 1)
    weeks_1m  = min(4,  n - 1)
    ret_12m = (prices_window[-1] / prices_window[-1 - weeks_12m]) - 1 if weeks_12m > 0 else np.zeros(N_STOCKS)
    ret_1m  = (prices_window[-1] / prices_window[-1 - weeks_1m])  - 1 if weeks_1m  > 0 else np.zeros(N_STOCKS)
    momentum = ret_12m - ret_1m

    mask = (
        (fund["mktcap_억"]  >= f["min_market_cap_억"]) &
        (fund["debt_ratio"] <= f["max_debt_ratio"])    &
        (fund["roe"]        >= f["min_roe"])           &
        (~np.array(IS_FINANCE))
    )
    indices = np.where(mask)[0]
    if len(indices) == 0:
        return []

    def z(arr): return zscore(arr[indices])

    total_score = (
        w["quality"]  * (z(fund["gpa"]) + z(fund["roe"])) / 2 +
        w["value"]    * (z(-fund["pbr"]) + z(-fund["per"])) / 2 +
        w["momentum"] * z(momentum)
    )

    ranked = indices[np.argsort(total_score)[::-1]]
    return ranked[:top_n].tolist()


# ── 리스크 신호 ───────────────────────────────────────────────────────────────
def get_risk_signal(kospi_history: np.ndarray, config: dict = None) -> dict:
    cfg      = config or get_config()
    ro       = cfg["risk_overlay"]
    ma_weeks = ro["ma_weeks"]

    ma = kospi_history[-ma_weeks:].mean() if len(kospi_history) >= ma_weeks else kospi_history.mean()
    current = kospi_history[-1]
    gap_pct = (current - ma) / ma * 100

    if current > ma:
        signal       = "STRONG_BULL" if gap_pct > ro["strong_bull_gap_pct"] else "NEUTRAL"
        equity_ratio = 1.00
    else:
        signal       = "STRONG_BEAR" if gap_pct < ro["strong_bear_gap_pct"] else "CAUTION"
        equity_ratio = ro["strong_bear_equity_ratio"] if signal == "STRONG_BEAR" else ro["caution_equity_ratio"]

    return {"signal": signal, "equity_ratio": equity_ratio, "gap_pct": round(gap_pct, 2)}


# ── 성과 지표 계산 (외부에서도 호출 가능) ──────────────────────────────────────
def compute_metrics(df: pd.DataFrame) -> dict:
    """백테스트 결과 DataFrame → 핵심 지표 dict 반환"""
    if df.empty:
        return {}

    n_weeks     = len(df)
    final       = df.iloc[-1]
    weekly_ret  = df["total_value"].pct_change().dropna()
    bm_weekly   = df["benchmark_value"].pct_change().dropna()

    total_return  = float(final["pnl_pct"])
    bm_return     = float(final["bm_pnl_pct"])
    ann_return    = ((1 + total_return / 100) ** (52 / n_weeks) - 1) * 100
    ann_bm_return = ((1 + bm_return / 100)    ** (52 / n_weeks) - 1) * 100
    vol           = float(weekly_ret.std() * (52 ** 0.5) * 100)
    sharpe        = (ann_return / 100 - 0.03) / (vol / 100) if vol > 0 else 0.0

    cum      = (1 + weekly_ret).cumprod()
    roll_max = cum.cummax()
    max_dd   = float(((cum - roll_max) / roll_max).min() * 100)

    alpha_weekly = weekly_ret.values - bm_weekly.values
    win_rate     = float((alpha_weekly > 0).mean() * 100)

    return {
        "total_return_pct":  round(total_return, 2),
        "bm_return_pct":     round(bm_return, 2),
        "alpha_pct":         round(total_return - bm_return, 2),
        "ann_return_pct":    round(ann_return, 2),
        "ann_bm_return_pct": round(ann_bm_return, 2),
        "volatility_pct":    round(vol, 2),
        "sharpe":            round(sharpe, 3),
        "max_dd_pct":        round(max_dd, 2),
        "win_rate_pct":      round(win_rate, 1),
        "n_weeks":           n_weeks,
        "total_buys":        int(df["buys"].sum()),
        "total_sells":       int(df["sells"].sum() + df["stop_losses"].sum()),
        "stop_losses":       int(df["stop_losses"].sum()),
        "final_value":       int(final["total_value"]),
    }


# ── 백테스트 엔진 ─────────────────────────────────────────────────────────────
class Backtest:
    def __init__(self, backtest_weeks: int = None, seed: int = 42,
                 data: dict = None, config: dict = None):
        self.config = config or get_config()
        if data is not None:
            self.data           = data
            warmup              = data.get("warmup_weeks", WARMUP_WEEKS)
            self.backtest_weeks = len(data["kospi"]) - warmup
            self.total_weeks    = len(data["kospi"])
            self._warmup        = warmup
        else:
            self.backtest_weeks = backtest_weeks or 104
            self.total_weeks    = WARMUP_WEEKS + self.backtest_weeks
            self.data           = generate_data(self.total_weeks, seed)
            self._warmup        = WARMUP_WEEKS

    def run(self) -> pd.DataFrame:
        prices  = self.data["stock_prices"]
        kospi   = self.data["kospi"]
        funds   = self.data["fund_history"]

        cash     = float(INITIAL_CAPITAL)
        holdings = {}   # {stock_idx: shares}
        records  = []

        # 벤치마크: 첫 주에 KOSPI buy & hold 가정
        warmup = self._warmup
        benchmark_units = INITIAL_CAPITAL / kospi[warmup]

        for week in range(self.backtest_weeks):
            t = warmup + week   # 전체 시계열 인덱스

            cur_prices = prices[t]
            fund       = funds[t]

            # 현재 포트폴리오 평가
            stock_value = sum(holdings.get(i, 0) * cur_prices[i] for i in range(N_STOCKS))
            total_value = cash + stock_value

            # 리스크 신호
            risk = get_risk_signal(kospi[:t + 1], self.config)

            # 스탑로스 체크
            sl_pct = self.config["portfolio"]["stop_loss_pct"]
            stop_loss_sold = []
            for idx in list(holdings.keys()):
                if holdings[idx] > 0:
                    buy_price = self._avg_prices.get(idx)
                    if buy_price and (cur_prices[idx] / buy_price - 1) <= sl_pct:
                        proceeds = holdings[idx] * cur_prices[idx]
                        fee = proceeds * FEE_RATE
                        tax = proceeds * TAX_RATE
                        cash += proceeds - fee - tax
                        stop_loss_sold.append(idx)
                        del holdings[idx]
                        if idx in self._avg_prices:
                            del self._avg_prices[idx]

            # STRONG_BEAR: 리밸런싱 스킵
            skip_rebalance = (risk["signal"] == "STRONG_BEAR")

            sells, buys = 0, 0
            if not skip_rebalance:
                selected = set(screen_stocks(prices[:t + 1], fund, self.config))

                # 탈락 종목 매도
                for idx in list(holdings.keys()):
                    if idx not in selected:
                        proceeds = holdings[idx] * cur_prices[idx]
                        fee = proceeds * FEE_RATE
                        tax = proceeds * TAX_RATE
                        cash += proceeds - fee - tax
                        del holdings[idx]
                        if idx in self._avg_prices:
                            del self._avg_prices[idx]
                        sells += 1

                # 신규 종목 매수 (동일 비중)
                new_buys = [i for i in selected if i not in holdings]
                if new_buys:
                    investable = cash * self.config["portfolio"]["equity_budget"]
                    per_stock  = investable / len(new_buys)
                    for idx in new_buys:
                        price = cur_prices[idx]
                        shares = int(per_stock / price)
                        if shares < 1:
                            continue
                        cost = shares * price * (1 + FEE_RATE)
                        if cost > cash:
                            continue
                        cash -= cost
                        holdings[idx] = shares
                        self._avg_prices[idx] = price
                        buys += 1

            # 재평가
            stock_value = sum(holdings.get(i, 0) * cur_prices[i] for i in range(N_STOCKS))
            total_value = cash + stock_value
            benchmark_value = benchmark_units * kospi[t]

            records.append({
                "week":            week + 1,
                "total_value":     round(total_value),
                "benchmark_value": round(benchmark_value),
                "cash":            round(cash),
                "stock_value":     round(stock_value),
                "n_holdings":      len(holdings),
                "sells":           sells,
                "buys":            buys,
                "stop_losses":     len(stop_loss_sold),
                "risk_signal":     risk["signal"],
                "kospi_gap_pct":   risk["gap_pct"],
                "pnl_pct":         round((total_value / INITIAL_CAPITAL - 1) * 100, 3),
                "bm_pnl_pct":      round((benchmark_value / INITIAL_CAPITAL - 1) * 100, 3),
            })

        return pd.DataFrame(records)

    def run_and_report(self) -> dict:
        """백테스트 실행, 리포트 출력, metrics dict 반환"""
        self._avg_prices = {}
        df      = self.run()
        metrics = compute_metrics(df)
        self._print_report(df)
        return metrics

    def _print_report(self, df: pd.DataFrame):
        final       = df.iloc[-1]
        weekly_ret  = df["total_value"].pct_change().dropna()
        bm_weekly   = df["benchmark_value"].pct_change().dropna()

        # ── 핵심 지표 계산 ──
        total_return    = final["pnl_pct"]
        bm_return       = final["bm_pct"] if "bm_pct" in df.columns else final["bm_pnl_pct"]
        n_weeks         = len(df)
        ann_return      = ((1 + total_return / 100) ** (52 / n_weeks) - 1) * 100
        ann_bm_return   = ((1 + bm_return / 100)    ** (52 / n_weeks) - 1) * 100

        vol             = weekly_ret.std() * (52 ** 0.5) * 100
        sharpe          = (ann_return / 100 - 0.03) / (vol / 100) if vol > 0 else 0

        # 최대 낙폭
        cum = (1 + weekly_ret).cumprod()
        roll_max = cum.cummax()
        drawdown = (cum - roll_max) / roll_max
        max_dd = drawdown.min() * 100

        # 벤치마크 대비 초과수익
        alpha_weekly = weekly_ret.values - bm_weekly.values
        win_rate     = (alpha_weekly > 0).mean() * 100

        # 거래 통계
        total_sells = df["sells"].sum() + df["stop_losses"].sum()
        total_buys  = df["buys"].sum()
        sl_count    = df["stop_losses"].sum()

        # 리스크 레짐 분포
        regime_counts = df["risk_signal"].value_counts()

        # ── 출력 ──────────────────────────────────────────────────────────────
        bar = "=" * 58

        print(f"\n{bar}")
        print(f"  멀티팩터 전략 백테스트 결과")
        print(f"  기간: {n_weeks}주 ({n_weeks / 52:.1f}년) | 초기 자본: {INITIAL_CAPITAL:,.0f}원")
        print(bar)

        print(f"\n  {'[ 수익률 ]':-<48}")
        print(f"  전략 누적 수익률    : {total_return:>+8.2f}%")
        print(f"  벤치마크(KOSPI) 수익: {bm_return:>+8.2f}%")
        print(f"  초과 수익률(Alpha)  : {total_return - bm_return:>+8.2f}%")
        print(f"  전략 연환산 수익률  : {ann_return:>+8.2f}%")
        print(f"  벤치마크 연환산     : {ann_bm_return:>+8.2f}%")

        print(f"\n  {'[ 리스크 ]':-<48}")
        print(f"  연환산 변동성       : {vol:>8.2f}%")
        print(f"  샤프 비율(Rf=3%)    : {sharpe:>8.2f}")
        print(f"  최대 낙폭(MDD)      : {max_dd:>+8.2f}%")
        print(f"  벤치마크 대비 승률  : {win_rate:>8.1f}%  (주간 기준)")

        print(f"\n  {'[ 거래 통계 ]':-<48}")
        print(f"  총 매수 횟수        : {total_buys:>8}건")
        print(f"  총 매도 횟수        : {total_sells:>8}건")
        print(f"  스탑로스 발동       : {sl_count:>8}건")
        print(f"  평균 보유 종목 수   : {df['n_holdings'].mean():>8.1f}종목")

        print(f"\n  {'[ 시장 레짐 분포 ]':-<48}")
        for signal, cnt in regime_counts.items():
            print(f"  {signal:<18}: {cnt:>4}주  ({cnt / n_weeks * 100:.1f}%)")

        print(f"\n  {'[ 자산 변화 ]':-<48}")
        print(f"  초기 자본           : {INITIAL_CAPITAL:>14,.0f}원")
        print(f"  최종 평가액(전략)   : {final['total_value']:>14,.0f}원")
        print(f"  최종 평가액(벤치)   : {final['benchmark_value']:>14,.0f}원")
        print(f"  순손익(전략)        : {final['total_value'] - INITIAL_CAPITAL:>+14,.0f}원")

        # 간이 수익 곡선
        print(f"\n  {'[ 연간 수익률 추이 ]':-<48}")
        print(f"  {'연도':>6} | {'전략':>8} | {'벤치마크':>8} | {'초과수익':>8}")
        print(f"  {'-'*40}")
        for yr in range(1, int(n_weeks / 52) + 1):
            start = max(0, yr * 52 - 52)
            end   = min(len(df) - 1, yr * 52 - 1)
            if start >= len(df):
                break
            s_ret = (df.iloc[end]["total_value"] / df.iloc[start]["total_value"] - 1) * 100
            b_ret = (df.iloc[end]["benchmark_value"] / df.iloc[start]["benchmark_value"] - 1) * 100
            print(f"  {yr:>2}년차  | {s_ret:>+7.2f}% | {b_ret:>+7.2f}% | {s_ret - b_ret:>+7.2f}%")

        is_real = self.data.get("is_real_data", False)
        print(f"\n{bar}\n")
        if is_real:
            print("  ※ 실제 시장 데이터 기반 결과입니다. (yfinance)")
            print("  ※ 펀더멘털(PBR/PER/ROE)은 현재 시점 값으로 고정 적용됩니다.")
            print("  ※ 모멘텀 팩터는 실제 가격 이력 기반으로 계산됩니다.")
        else:
            print("  ※ 시뮬레이션 데이터 기반 결과입니다.")
            print("  ※ 실제 성과는 --real 옵션으로 재실행 시 확인 가능합니다.")
        print(f"{bar}\n")


# ── 진입점 ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    years   = 2
    seed    = 42
    use_real = False
    refresh  = False

    args = sys.argv[1:]
    if "--years" in args:
        idx   = args.index("--years")
        years = int(args[idx + 1])
    if "--seed" in args:
        idx  = args.index("--seed")
        seed = int(args[idx + 1])
    if "--real" in args:
        use_real = True
    if "--refresh" in args:
        refresh = True

    if use_real:
        from data_fetcher import load_or_fetch
        print("  실제 데이터 백테스트 모드")
        data = load_or_fetch(years=years, refresh=refresh)
        bt   = Backtest(data=data)
        print(f"  백테스트 기간: {bt.backtest_weeks}주 ({bt.backtest_weeks / 52:.1f}년)")
    else:
        print(f"  시뮬레이션 백테스트: {years}년 ({years * 52}주) | seed={seed}")
        bt = Backtest(backtest_weeks=years * 52, seed=seed)

    bt.run_and_report()
