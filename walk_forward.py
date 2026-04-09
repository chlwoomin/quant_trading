"""
walk_forward.py
실제 데이터 롤링 윈도우 워크포워드 테스트

전체 기간을 N년 창으로 슬라이딩하며 M번 백테스트를 실행합니다.
각 창은 독립적인 기간이므로 전략의 시간적 일관성을 검증할 수 있습니다.

  ├── [창 1] warmup(1년) ─── test(N년) ───┤
  │        ├── [창 2] warmup(1년) ─── test(N년) ───┤
  │        │        ├── [창 3] ...
  └── step_weeks씩 전진 ──────────────────────────────→ 시간

사용법:
  python walk_forward.py                          # 2년 창, 26주 간격, 캐시 사용
  python walk_forward.py --window 2 --step 26     # 명시적 지정
  python walk_forward.py --window 1 --step 13     # 1년 창, 분기 간격
  python walk_forward.py --total 6                # 총 6년치 데이터 수집
  python walk_forward.py --refresh                # 데이터 재수집
  python walk_forward.py --dart                   # DART 분기 펀더멘탈 사용 (look-ahead bias 제거)
  python walk_forward.py --dart --refresh-dart    # DART 데이터 재수집
  python walk_forward.py --config v3              # 특정 전략 버전 검증
  python walk_forward.py --compare                # 현재 vs 직전 버전 비교
"""

import sys
import os
import json
import warnings
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

warnings.filterwarnings("ignore")

from backtest import Backtest, compute_metrics, WARMUP_WEEKS
from strategy_manager import get_config, get_history

CACHE_DIR = os.path.join(os.path.dirname(__file__), "data")


# ── 데이터 수집 (walk_forward용 — 더 긴 기간) ────────────────────────────────
def fetch_real_data(total_years: int = 5, refresh: bool = False) -> dict:
    """
    total_years 년치 실제 데이터를 수집합니다.
    워크포워드용 별도 캐시(data/wf_*)를 사용합니다.
    """
    import yfinance as yf
    from data_fetcher import (YF_TICKERS, YF_KOSPI, NAMES, TICKERS,
                               fetch_fundamentals, build_backtest_data)

    cache_prices = os.path.join(CACHE_DIR, f"wf_{total_years}yr_prices.npz")
    cache_meta   = os.path.join(CACHE_DIR, f"wf_{total_years}yr_meta.json")
    cache_fund   = os.path.join(CACHE_DIR, f"wf_{total_years}yr_fund.json")

    # 캐시 확인
    if not refresh and all(os.path.exists(p) for p in [cache_prices, cache_meta, cache_fund]):
        try:
            npz  = np.load(cache_prices)
            with open(cache_meta, encoding="utf-8") as f:
                meta = json.load(f)
            with open(cache_fund, encoding="utf-8") as f:
                fund_save = json.load(f)

            fund_arrays  = {k: np.array(v) for k, v in fund_save.items()}
            n_weeks      = len(npz["kospi"])
            fund_history = [fund_arrays] * n_weeks

            print(f"  캐시 로드: {meta['dates'][0]} ~ {meta['dates'][-1]} ({n_weeks}주)")
            print(f"  수집 시각: {meta['fetched_at'][:16]}")
            return {
                "kospi": npz["kospi"], "stock_prices": npz["stock_prices"],
                "fund_history": fund_history, "dates": meta["dates"],
                "warmup_weeks": WARMUP_WEEKS, "is_real_data": True,
            }
        except Exception as e:
            print(f"  캐시 로드 실패: {e}")

    # 새로 수집
    print(f"\n{'='*56}")
    print(f"  실제 데이터 수집 시작 (yfinance, {total_years}년)")
    print(f"  ※ 30종목 펀더멘털 수집에 수분이 소요됩니다")
    print(f"{'='*56}")

    end_date   = datetime.today()
    start_date = end_date - timedelta(days=365 * (total_years + 1) + 60)

    all_tickers = YF_TICKERS + [YF_KOSPI]
    print("  주가 다운로드 중...")
    raw = yf.download(all_tickers, start=start_date.strftime("%Y-%m-%d"),
                      end=end_date.strftime("%Y-%m-%d"), progress=False, auto_adjust=True)

    if isinstance(raw.columns, pd.MultiIndex):
        close = raw["Close"]
    else:
        close = raw[["Close"]]

    weekly = close.resample("W-FRI").last().ffill()
    kospi_weekly  = weekly[YF_KOSPI].dropna()
    stocks_weekly = weekly[YF_TICKERS].ffill().bfill()

    print(f"  기간: {weekly.index[0].date()} ~ {weekly.index[-1].date()} ({len(weekly)}주)")

    fundamentals = fetch_fundamentals()
    data = build_backtest_data(stocks_weekly, kospi_weekly, fundamentals, WARMUP_WEEKS)

    # 캐시 저장
    os.makedirs(CACHE_DIR, exist_ok=True)
    np.savez(cache_prices, kospi=data["kospi"], stock_prices=data["stock_prices"])
    fund_save = {k: v.tolist() for k, v in data["fund_history"][0].items()}
    with open(cache_fund, "w", encoding="utf-8") as f:
        json.dump(fund_save, f, ensure_ascii=False)
    meta = {
        "dates": [str(d) if isinstance(d, str) else str(d.date()) for d in data["dates"]],
        "fetched_at": datetime.now().isoformat(),
    }
    with open(cache_meta, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)

    print(f"  캐시 저장: data/wf_{total_years}yr_*")
    return data


# ── 롤링 윈도우 슬라이싱 ──────────────────────────────────────────────────────
def make_windows(data: dict, window_years: int, step_weeks: int) -> list:
    """
    전체 데이터를 (warmup + window_years*52)짜리 창으로 나눕니다.

    Returns: list of (start_idx, end_idx, label)
    """
    total_weeks   = len(data["kospi"])
    window_weeks  = WARMUP_WEEKS + window_years * 52
    dates         = data["dates"]

    windows = []
    start = 0
    while start + window_weeks <= total_weeks:
        end   = start + window_weeks
        label = f"{dates[start]} ~ {dates[end - 1]}"
        windows.append((start, end, label))
        start += step_weeks

    return windows


# ── 단일 창 백테스트 ──────────────────────────────────────────────────────────
def run_window(data: dict, start: int, end: int, config: dict,
               dart_fund: list = None) -> dict:
    """슬라이스된 창으로 백테스트 실행"""
    fund_history = (dart_fund[start:end]
                    if dart_fund is not None
                    else data["fund_history"][start:end])
    sliced = {
        "kospi":        data["kospi"][start:end],
        "stock_prices": data["stock_prices"][start:end],
        "fund_history": fund_history,
        "warmup_weeks": WARMUP_WEEKS,
        "is_real_data": True,
    }
    bt = Backtest(data=sliced, config=config)
    bt._avg_prices = {}
    df = bt.run()
    return compute_metrics(df)


# ── 집계 통계 ─────────────────────────────────────────────────────────────────
def aggregate(results: list) -> dict:
    keys = [
        "total_return_pct", "bm_return_pct", "alpha_pct",
        "ann_return_pct",   "sharpe",         "max_dd_pct",
        "win_rate_pct",     "volatility_pct", "stop_losses",
    ]
    stats = {}
    for k in keys:
        vals = np.array([r[k] for r in results if k in r and r[k] is not None])
        if len(vals) == 0:
            continue
        stats[k] = {
            "mean":   float(np.mean(vals)),
            "median": float(np.median(vals)),
            "std":    float(np.std(vals)),
            "p25":    float(np.percentile(vals, 25)),
            "p75":    float(np.percentile(vals, 75)),
            "p5":     float(np.percentile(vals, 5)),
            "p95":    float(np.percentile(vals, 95)),
            "min":    float(np.min(vals)),
            "max":    float(np.max(vals)),
        }
    return stats


def pass_rate(results: list, gates: dict) -> dict:
    n = len(results)
    if n == 0:
        return {"sharpe": 0, "mdd": 0, "alpha": 0, "all": 0, "n": 0}

    min_sharpe = gates.get("min_sharpe",    0.2)
    max_mdd    = gates.get("max_mdd_pct",  -30.0)
    min_alpha  = gates.get("min_alpha_pct",  0.0)

    p_sharpe = sum(1 for r in results if r.get("sharpe",     0) >= min_sharpe)
    p_mdd    = sum(1 for r in results if r.get("max_dd_pct", 0) >= max_mdd)
    p_alpha  = sum(1 for r in results if r.get("alpha_pct",  0) >= min_alpha)
    p_all    = sum(1 for r in results
                   if r.get("sharpe",     0) >= min_sharpe
                   and r.get("max_dd_pct", 0) >= max_mdd
                   and r.get("alpha_pct",  0) >= min_alpha)

    return {
        "sharpe": round(p_sharpe / n * 100, 1),
        "mdd":    round(p_mdd    / n * 100, 1),
        "alpha":  round(p_alpha  / n * 100, 1),
        "all":    round(p_all    / n * 100, 1),
        "n": n,
    }


# ── 리포트 출력 ───────────────────────────────────────────────────────────────
def print_report(label: str, results: list, stats: dict, rates: dict,
                 windows: list, config: dict, window_years: int, step_weeks: int,
                 use_dart: bool = False):
    bar = "=" * 66
    fw    = config.get("factor_weights", {})
    pf    = config.get("portfolio", {})
    gates = config.get("validation_gates", {})
    n     = len(results)

    print(f"\n{bar}")
    print(f"  워크포워드 테스트 결과  —  {label}")
    print(f"  {n}개 창 × {window_years}년  |  창 간격 {step_weeks}주({step_weeks/52:.1f}년)")
    print(f"  팩터: 퀄리티 {fw.get('quality',0):.0%}  밸류 {fw.get('value',0):.0%}  "
          f"모멘텀 {fw.get('momentum',0):.0%}  |  "
          f"상위 {pf.get('top_n',0)}종목  스탑로스 {pf.get('stop_loss_pct',0):.0%}")
    dart_note = "DART 분기 펀더멘탈 (look-ahead bias 제거)" if use_dart else "현재 시점 값 고정 (look-ahead bias 있음)"
    print(f"  ※ 펀더멘탈: {dart_note}")
    print(bar)

    # 창별 결과 테이블
    print(f"\n  {'창':<4} {'기간':<24} {'수익률':>8} {'Alpha':>8} "
          f"{'Sharpe':>7} {'MDD':>8} {'판정':>6}")
    print(f"  {'-'*64}")
    for i, (res, (_, _, lbl)) in enumerate(zip(results, windows)):
        ret    = res.get("total_return_pct", 0)
        alpha  = res.get("alpha_pct",        0)
        sharpe = res.get("sharpe",           0)
        mdd    = res.get("max_dd_pct",       0)

        passed = (sharpe >= gates.get("min_sharpe", 0.2)
                  and mdd  >= gates.get("max_mdd_pct", -30.0)
                  and alpha >= gates.get("min_alpha_pct", 0.0))
        mark = "✅" if passed else "❌"

        # 기간 라벨 (yyyy-mm-dd ~ yyyy-mm-dd → 연월만 표시)
        short = lbl[:7] + " ~ " + lbl[13:20] if len(lbl) >= 20 else lbl[:23]
        print(f"  {i+1:<4} {short:<24} {ret:>+7.1f}%  {alpha:>+7.1f}%  "
              f"{sharpe:>6.2f}  {mdd:>+7.1f}%  {mark}")

    # 집계 통계
    print(f"\n  {'[ 집계 통계 ]':-<60}")

    def row(lbl, key, fmt="{:+.2f}%"):
        s = stats.get(key)
        if not s:
            return
        print(f"  {lbl:<20} 중앙값 {fmt.format(s['median']):>9}  "
              f"평균 {fmt.format(s['mean']):>9}  "
              f"[P25~P75: {fmt.format(s['p25'])} ~ {fmt.format(s['p75'])}]")

    row("전략 누적 수익률",  "total_return_pct")
    row("벤치마크 수익률",   "bm_return_pct")
    row("Alpha",            "alpha_pct")
    row("연환산 수익률",     "ann_return_pct")
    row("샤프 비율",         "sharpe",       "{:.3f}")
    row("MDD",              "max_dd_pct")
    row("주간 승률",         "win_rate_pct",  "{:.1f}%")

    # 통과율
    print(f"\n  {'[ 검증 게이트 통과율 ]':-<60}")
    print(f"  Sharpe ≥ {gates.get('min_sharpe',0.2):.1f}   : "
          f"{rates['sharpe']:>5.1f}%  ({int(rates['sharpe']*n/100)}/{n}개 창 통과)")
    print(f"  MDD ≥ {gates.get('max_mdd_pct',-30.0):.0f}%    : "
          f"{rates['mdd']:>5.1f}%  ({int(rates['mdd']*n/100)}/{n}개 창 통과)")
    print(f"  Alpha ≥ {gates.get('min_alpha_pct',0.0):.0f}%    : "
          f"{rates['alpha']:>5.1f}%  ({int(rates['alpha']*n/100)}/{n}개 창 통과)")
    print(f"  {'─'*56}")

    grade = ("✅ 통과" if rates["all"] >= 70
             else "⚠️  불안정" if rates["all"] >= 40
             else "❌ 부적합")
    print(f"  전체 동시 통과율    : {rates['all']:>5.1f}%  →  {grade}")
    print(f"{bar}\n")


def print_compare(stats_a, stats_b, rates_a, rates_b,
                  results_a, results_b, label_a, label_b):
    bar = "=" * 66
    keys = [
        ("전략 누적 수익률", "total_return_pct", "{:+.2f}%"),
        ("Alpha",           "alpha_pct",         "{:+.2f}%"),
        ("연환산 수익률",    "ann_return_pct",    "{:+.2f}%"),
        ("샤프 비율",        "sharpe",            "{:.3f}"),
        ("MDD",             "max_dd_pct",         "{:+.2f}%"),
        ("주간 승률",        "win_rate_pct",       "{:.1f}%"),
    ]

    print(f"\n{bar}")
    print(f"  전략 비교  (중앙값 기준)")
    print(f"  {'항목':<20} {label_a:>14}  {label_b:>14}  {'차이':>8}")
    print(f"  {'-'*60}")
    for lbl, key, fmt in keys:
        va = stats_a.get(key, {}).get("median", 0)
        vb = stats_b.get(key, {}).get("median", 0)
        diff = vb - va
        sign = "+" if diff >= 0 else ""
        print(f"  {lbl:<20} {fmt.format(va):>14}  {fmt.format(vb):>14}  "
              f"{sign}{fmt.format(diff):>8}")

    n = max(rates_a["n"], rates_b["n"])
    print(f"\n  전체 통과율: {label_a} {rates_a['all']:.1f}%  →  {label_b} {rates_b['all']:.1f}%")
    delta = rates_b["all"] - rates_a["all"]
    verdict = ("개선됨 ✅" if delta > 2 else "악화됨 ❌" if delta < -2 else "유사함 ➡️")
    print(f"  판정: {verdict}  (차이 {delta:+.1f}%p)")
    print(f"{bar}\n")


# ── 히스토리에서 설정 로드 ───────────────────────────────────────────────────
def load_config_by_label(label: str) -> dict:
    if label == "current":
        return get_config()
    history = get_history()
    if label == "prev":
        if len(history) < 2:
            raise ValueError("이전 버전이 없습니다.")
        path = history[-2]["path"]
    elif label.startswith("v"):
        ver = int(label[1:])
        matched = [h for h in history if h["version"] == ver]
        if not matched:
            raise ValueError(f"버전 {ver}을 찾을 수 없습니다.")
        path = matched[-1]["path"]
    else:
        path = label
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── 메인 ─────────────────────────────────────────────────────────────────────
def main():
    args          = sys.argv[1:]
    window_years  = 2
    step_weeks    = 26
    total_years   = 5
    refresh       = "--refresh" in args
    refresh_dart  = "--refresh-dart" in args
    compare       = "--compare" in args
    use_dart      = "--dart" in args
    config_label  = "current"

    if "--window" in args:
        window_years = int(args[args.index("--window") + 1])
    if "--step" in args:
        step_weeks = int(args[args.index("--step") + 1])
    if "--total" in args:
        total_years = int(args[args.index("--total") + 1])
    if "--config" in args:
        config_label = args[args.index("--config") + 1]

    # ── 가격 데이터 수집 ───────────────────────────────────────────────────
    dart_tag = " + DART 펀더멘탈" if use_dart else ""
    print(f"\n[워크포워드{dart_tag}] 총 {total_years}년  |  "
          f"{window_years}년 창  |  {step_weeks}주({step_weeks/52:.1f}년) 간격")
    data = fetch_real_data(total_years=total_years, refresh=refresh)

    windows = make_windows(data, window_years, step_weeks)
    if not windows:
        needed = WARMUP_WEEKS + window_years * 52
        print(f"\n  ❌ 데이터 부족: {window_years}년 창에 {needed}주 필요, "
              f"현재 {len(data['kospi'])}주")
        print(f"  → --total {window_years + 2} 로 더 긴 기간을 수집하세요")
        return

    print(f"  생성된 창: {len(windows)}개")

    # ── DART 분기 펀더멘탈 로드 ───────────────────────────────────────────
    dart_fund = None
    if use_dart:
        from dart_fetcher import load_or_build, to_weekly_fund_history
        end_year   = datetime.today().year
        start_year = end_year - total_years + 1
        print(f"\n  DART 분기 펀더멘탈 로드 ({start_year}~{end_year})...")
        quarterly_fund = load_or_build(start_year, end_year,
                                       refresh=refresh_dart)
        print(f"  주간 fund_history 변환 중 ({len(data['dates'])}주)...")
        dart_fund = to_weekly_fund_history(
            quarterly_fund,
            data["dates"],
            data["stock_prices"],
            data["fund_history"],
        )
        print(f"  완료")

    def _run_all(config, label):
        print(f"\n  [{label}] {len(windows)}개 창 백테스트 실행 중...")
        results = []
        for i, (start, end, lbl) in enumerate(windows):
            print(f"    창 {i+1}/{len(windows)}: {lbl}", end="  ", flush=True)
            m = run_window(data, start, end, config, dart_fund)
            results.append(m)
            print(f"수익률 {m.get('total_return_pct',0):+.1f}%  "
                  f"Alpha {m.get('alpha_pct',0):+.1f}%  "
                  f"Sharpe {m.get('sharpe',0):.2f}")
        return results

    # ── 현재(또는 지정) 전략 ──────────────────────────────────────────────
    config_a = load_config_by_label(config_label)
    label_a  = f"v{config_a.get('version','?')}"
    if use_dart:
        label_a += "+DART"

    results_a = _run_all(config_a, label_a)
    stats_a   = aggregate(results_a)
    rates_a   = pass_rate(results_a, config_a.get("validation_gates", {}))
    print_report(label_a, results_a, stats_a, rates_a,
                 windows, config_a, window_years, step_weeks, use_dart)

    # ── 비교 모드 ──────────────────────────────────────────────────────────
    if compare:
        try:
            config_b = load_config_by_label("prev")
            label_b  = f"v{config_b.get('version','?')}"
            if use_dart:
                label_b += "+DART"
            results_b = _run_all(config_b, label_b)
            stats_b   = aggregate(results_b)
            rates_b   = pass_rate(results_b, config_b.get("validation_gates", {}))
            print_report(label_b, results_b, stats_b, rates_b,
                         windows, config_b, window_years, step_weeks, use_dart)
            print_compare(stats_b, stats_a, rates_b, rates_a,
                          results_b, results_a, label_b, label_a)
        except ValueError as e:
            print(f"  비교 스킵: {e}")

    # ── 결과 저장 ──────────────────────────────────────────────────────────
    suffix   = "_dart" if use_dart else ""
    out_path = (f"logs/wf_{label_a}_{len(windows)}w"
                f"_{window_years}yr_{step_weeks}step{suffix}.json")
    os.makedirs("logs", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "label": label_a, "n_windows": len(windows),
            "window_years": window_years, "step_weeks": step_weeks,
            "use_dart": use_dart,
            "windows": [lbl for _, _, lbl in windows],
            "results": results_a, "stats": stats_a, "pass_rates": rates_a,
        }, f, ensure_ascii=False, indent=2)
    print(f"  결과 저장: {out_path}")


if __name__ == "__main__":
    main()
