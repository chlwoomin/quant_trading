"""
monte_carlo.py
모의 데이터 몬테카를로 검증 파이프라인

다양한 랜덤 시드로 백테스트를 N회 반복 실행하여
전략의 통계적 안정성(일관성)을 검증합니다.

사용법:
  python monte_carlo.py                        # 현재 전략, 50회, 2년
  python monte_carlo.py --runs 100             # 100회
  python monte_carlo.py --years 3              # 3년 기간
  python monte_carlo.py --config v5            # strategy_history/v5_*.json 사용
  python monte_carlo.py --compare              # 현재 전략 vs 직전 버전 비교
  python monte_carlo.py --runs 50 --years 2 --compare
"""

import sys
import json
import os
import numpy as np
from concurrent.futures import ProcessPoolExecutor, as_completed

from backtest import Backtest, compute_metrics, generate_data, WARMUP_WEEKS
from strategy_manager import get_config, get_history


# ── 단일 시드 백테스트 (병렬 실행용 톱레벨 함수) ─────────────────────────────
def _run_one(args):
    seed, years, config = args
    total_weeks = WARMUP_WEEKS + years * 52
    data = generate_data(total_weeks, seed=seed)
    bt = Backtest(data=data, config=config)
    bt._avg_prices = {}
    df = bt.run()
    return compute_metrics(df)


# ── 여러 시드 실행 ────────────────────────────────────────────────────────────
def run_monte_carlo(config: dict, n_runs: int = 50, years: int = 2,
                    verbose: bool = True) -> list:
    """
    n_runs개의 랜덤 시드로 백테스트를 실행합니다.
    Returns: metrics dict 리스트
    """
    seeds = list(range(n_runs))
    args  = [(s, years, config) for s in seeds]

    results = []
    if verbose:
        print(f"  {n_runs}회 실행 중", end="", flush=True)

    # 병렬 실행 (CPU 코어 수 기준, 최대 4)
    max_workers = min(4, os.cpu_count() or 1)
    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_run_one, a): i for i, a in enumerate(args)}
        for fut in as_completed(futures):
            results.append(fut.result())
            if verbose and (len(results) % max(1, n_runs // 10) == 0):
                print(".", end="", flush=True)

    if verbose:
        print(" 완료")
    return results


# ── 집계 통계 ─────────────────────────────────────────────────────────────────
def aggregate(results: list) -> dict:
    """metrics 리스트 → 집계 통계 dict"""
    keys = [
        "total_return_pct", "bm_return_pct", "alpha_pct",
        "ann_return_pct",   "sharpe",         "max_dd_pct",
        "win_rate_pct",     "volatility_pct", "stop_losses",
    ]
    stats = {}
    for k in keys:
        vals = np.array([r[k] for r in results if k in r])
        if len(vals) == 0:
            continue
        stats[k] = {
            "mean":   float(np.mean(vals)),
            "median": float(np.median(vals)),
            "std":    float(np.std(vals)),
            "p5":     float(np.percentile(vals, 5)),
            "p25":    float(np.percentile(vals, 25)),
            "p75":    float(np.percentile(vals, 75)),
            "p95":    float(np.percentile(vals, 95)),
            "min":    float(np.min(vals)),
            "max":    float(np.max(vals)),
        }
    return stats


def pass_rate(results: list, gates: dict) -> dict:
    """검증 통과율 계산"""
    n = len(results)
    if n == 0:
        return {}

    min_sharpe  = gates.get("min_sharpe",    0.2)
    max_mdd     = gates.get("max_mdd_pct",  -30.0)
    min_alpha   = gates.get("min_alpha_pct",  0.0)

    passed_sharpe = sum(1 for r in results if r.get("sharpe",       0) >= min_sharpe)
    passed_mdd    = sum(1 for r in results if r.get("max_dd_pct",   0) >= max_mdd)
    passed_alpha  = sum(1 for r in results if r.get("alpha_pct",    0) >= min_alpha)
    passed_all    = sum(1 for r in results
                        if r.get("sharpe",     0) >= min_sharpe
                        and r.get("max_dd_pct", 0) >= max_mdd
                        and r.get("alpha_pct",  0) >= min_alpha)

    return {
        "sharpe":  round(passed_sharpe / n * 100, 1),
        "mdd":     round(passed_mdd    / n * 100, 1),
        "alpha":   round(passed_alpha  / n * 100, 1),
        "all":     round(passed_all    / n * 100, 1),
        "n":       n,
    }


# ── 리포트 출력 ───────────────────────────────────────────────────────────────
def print_report(label: str, stats: dict, rates: dict, config: dict,
                 n_runs: int, years: int):
    bar = "=" * 62
    fw  = config.get("factor_weights", {})
    pf  = config.get("portfolio", {})
    gates = config.get("validation_gates", {})

    print(f"\n{bar}")
    print(f"  몬테카를로 검증 결과  —  {label}")
    print(f"  {n_runs}회 × {years}년  |  시드 0~{n_runs-1}")
    print(f"  팩터: 퀄리티 {fw.get('quality',0):.0%}  밸류 {fw.get('value',0):.0%}  "
          f"모멘텀 {fw.get('momentum',0):.0%}  |  "
          f"상위 {pf.get('top_n',0)}종목  스탑로스 {pf.get('stop_loss_pct',0):.0%}")
    print(bar)

    def row(label, key, fmt="{:+.2f}%", sign=True):
        s = stats.get(key)
        if not s:
            return
        med  = fmt.format(s["median"])
        mean = fmt.format(s["mean"])
        p5   = fmt.format(s["p5"])
        p95  = fmt.format(s["p95"])
        print(f"  {label:<20} 중앙값 {med:>9}  평균 {mean:>9}  [P5~P95: {p5} ~ {p95}]")

    print(f"\n  {'[ 수익률 ]':-<56}")
    row("전략 누적 수익률",    "total_return_pct")
    row("벤치마크 수익률",     "bm_return_pct")
    row("Alpha",              "alpha_pct")
    row("연환산 수익률",       "ann_return_pct")

    print(f"\n  {'[ 리스크 ]':-<56}")
    row("샤프 비율",           "sharpe",       "{:.3f}", sign=False)
    row("MDD",                 "max_dd_pct",   "{:+.2f}%")
    row("연환산 변동성",       "volatility_pct", "{:.2f}%", sign=False)
    row("주간 승률",           "win_rate_pct",  "{:.1f}%", sign=False)

    print(f"\n  {'[ 검증 게이트 통과율 ]':-<56}")
    print(f"  Sharpe ≥ {gates.get('min_sharpe',0.2):.1f}   : {rates['sharpe']:>5.1f}%  "
          f"({int(rates['sharpe']*n_runs/100)}/{n_runs}회 통과)")
    print(f"  MDD ≥ {gates.get('max_mdd_pct',-30.0):.0f}%    : {rates['mdd']:>5.1f}%  "
          f"({int(rates['mdd']*n_runs/100)}/{n_runs}회 통과)")
    print(f"  Alpha ≥ {gates.get('min_alpha_pct',0.0):.0f}%    : {rates['alpha']:>5.1f}%  "
          f"({int(rates['alpha']*n_runs/100)}/{n_runs}회 통과)")
    print(f"  ─────────────────────────────────────────────────")

    grade = ("✅ 통과" if rates["all"] >= 70
             else "⚠️  불안정" if rates["all"] >= 40
             else "❌ 부적합")
    print(f"  전체 동시 통과율    : {rates['all']:>5.1f}%  →  {grade}")
    print(f"{bar}\n")


# ── 두 전략 비교 ──────────────────────────────────────────────────────────────
def print_compare(stats_a: dict, stats_b: dict,
                  rates_a: dict, rates_b: dict,
                  label_a: str, label_b: str):
    bar = "=" * 62
    keys = [
        ("전략 누적 수익률", "total_return_pct", "{:+.2f}%"),
        ("Alpha",          "alpha_pct",         "{:+.2f}%"),
        ("샤프 비율",       "sharpe",            "{:.3f}"),
        ("MDD",            "max_dd_pct",         "{:+.2f}%"),
        ("주간 승률",       "win_rate_pct",       "{:.1f}%"),
    ]

    print(f"\n{bar}")
    print(f"  전략 비교")
    print(f"  {'항목':<20} {label_a:>12}  {label_b:>12}  {'차이':>8}")
    print(f"  {'-'*56}")
    for lbl, key, fmt in keys:
        va = stats_a.get(key, {}).get("median", 0)
        vb = stats_b.get(key, {}).get("median", 0)
        diff = vb - va
        sign = "+" if diff > 0 else ""
        print(f"  {lbl:<20} {fmt.format(va):>12}  {fmt.format(vb):>12}  {sign}{fmt.format(diff):>8}")

    print(f"\n  전체 통과율: {label_a} {rates_a['all']:.1f}%  →  {label_b} {rates_b['all']:.1f}%")
    delta = rates_b["all"] - rates_a["all"]
    verdict = ("개선됨 ✅" if delta > 2 else "악화됨 ❌" if delta < -2 else "유사함 ➡️")
    print(f"  판정: {verdict}  (차이 {delta:+.1f}%p)")
    print(f"{bar}\n")


# ── 히스토리에서 설정 로드 ───────────────────────────────────────────────────
def load_config_by_label(label: str) -> dict:
    """
    label: "current" | "prev" | "v3" | 파일경로
    """
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
        path = label  # 직접 경로

    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── 메인 ─────────────────────────────────────────────────────────────────────
def main():
    args    = sys.argv[1:]
    n_runs  = 50
    years   = 2
    compare = "--compare" in args
    config_label = "current"

    if "--runs" in args:
        n_runs = int(args[args.index("--runs") + 1])
    if "--years" in args:
        years = int(args[args.index("--years") + 1])
    if "--config" in args:
        config_label = args[args.index("--config") + 1]

    # ── 현재(또는 지정) 전략 로드 ──────────────────────────────────────────
    config_a = load_config_by_label(config_label)
    label_a  = f"현재 전략 v{config_a.get('version','?')}"

    print(f"\n[몬테카를로 검증] {label_a}  |  {n_runs}회 × {years}년")
    results_a = run_monte_carlo(config_a, n_runs=n_runs, years=years)
    stats_a   = aggregate(results_a)
    rates_a   = pass_rate(results_a, config_a.get("validation_gates", {}))
    print_report(label_a, stats_a, rates_a, config_a, n_runs, years)

    # ── 비교 모드 ──────────────────────────────────────────────────────────
    if compare:
        try:
            config_b = load_config_by_label("prev")
            label_b  = f"직전 전략 v{config_b.get('version','?')}"
            print(f"[몬테카를로 검증] {label_b}  |  {n_runs}회 × {years}년")
            results_b = run_monte_carlo(config_b, n_runs=n_runs, years=years)
            stats_b   = aggregate(results_b)
            rates_b   = pass_rate(results_b, config_b.get("validation_gates", {}))
            print_report(label_b, stats_b, rates_b, config_b, n_runs, years)
            print_compare(stats_b, stats_a, rates_b, rates_a, label_b, label_a)
        except ValueError as e:
            print(f"  비교 스킵: {e}")

    # ── 결과 저장 ──────────────────────────────────────────────────────────
    out_path = f"logs/mc_{config_a.get('version','0')}_{n_runs}runs_{years}yr.json"
    os.makedirs("logs", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"label": label_a, "n_runs": n_runs, "years": years,
                   "stats": stats_a, "pass_rates": rates_a}, f,
                  ensure_ascii=False, indent=2)
    print(f"  결과 저장: {out_path}")


if __name__ == "__main__":
    main()
