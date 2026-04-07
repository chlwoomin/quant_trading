"""
weekly_pipeline.py
주간 자동화 파이프라인 메인 오케스트레이터

실행 흐름:
  1. 현재 포트폴리오 성과 수집
  2. 모의 백테스트 실행 → 성과 지표 계산
  3. AI(Claude) 전략 분석 → 파라미터 개선 제안
  4. 제안된 전략을 모의→실제 데이터로 검증
  5. 검증 통과 시 strategy_config.json 업데이트
  6. (검증된) 전략으로 팩터 스크리닝 → 리밸런싱
  7. 주간 리포트 생성 + 로그 저장

사용법:
  python weekly_pipeline.py              # 전체 파이프라인
  python weekly_pipeline.py --no-ai      # AI 분석 스킵 (전략 유지)
  python weekly_pipeline.py --no-real    # 실제 데이터 검증 스킵
  python weekly_pipeline.py --force-update  # 검증 없이 AI 제안 즉시 적용 (위험)
  python weekly_pipeline.py --report     # 현재 성과 출력만

크론 설정 (매주 일요일 22:00):
  0 22 * * 0 cd /path/to/quant_system && python weekly_pipeline.py >> logs/weekly.log 2>&1
"""

import sys
import os
import json
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(__file__))

from strategy_manager    import get_config, save_config, print_history
from portfolio_manager   import init_db, rebalance, get_performance_summary
from factor_engine       import run_screening
from risk_overlay        import get_kospi_ma200, get_risk_signal, \
                                check_individual_stop_loss, generate_risk_report
from report_generator    import generate_weekly_report
from backtest            import Backtest, compute_metrics
from ai_analyst          import analyze_and_suggest
from validation_pipeline import validate_strategy

LOGS_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOGS_DIR, exist_ok=True)


# ── 로깅 ─────────────────────────────────────────────────────────────────────
def log(msg: str, level: str = "INFO"):
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line)
    with open(os.path.join(LOGS_DIR, "weekly.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ── STEP 1: 현재 포트폴리오 성과 수집 ─────────────────────────────────────────
def step_collect_performance() -> dict:
    log("STEP 1 — 포트폴리오 성과 수집")
    init_db()
    return get_performance_summary()


# ── STEP 2: 모의 백테스트 실행 ────────────────────────────────────────────────
def step_sim_backtest(config: dict) -> dict:
    log("STEP 2 — 모의 백테스트 실행 (104주)")
    try:
        bt = Backtest(backtest_weeks=104, seed=42, config=config)
        bt._avg_prices = {}
        df      = bt.run()
        metrics = compute_metrics(df)
        log(f"  Sharpe={metrics.get('sharpe', 0):.3f}  "
            f"MDD={metrics.get('max_dd_pct', 0):.1f}%  "
            f"Alpha={metrics.get('alpha_pct', 0):+.1f}%")
        return metrics
    except Exception as e:
        log(f"  백테스트 오류: {e}", "WARN")
        return {}


# ── STEP 3: AI 전략 분석 ──────────────────────────────────────────────────────
def step_ai_analysis(config: dict, metrics: dict, perf: dict) -> dict:
    log("STEP 3 — AI 전략 분석")

    # 최근 레짐 분포 (portfolio performance_log에서 추출 불가 → 간략히 없음)
    result = analyze_and_suggest(config, metrics, perf, regime_history=None)

    if result["should_update"]:
        log(f"  AI 변경 제안: {result['reasoning']}")
        if result["suggested_changes"]:
            log(f"  제안 변경 항목: {list(result['suggested_changes'].keys())}")
    else:
        log(f"  AI 유지 판단: {result['reasoning']}")

    return result


# ── STEP 4: 전략 검증 + 업데이트 결정 ────────────────────────────────────────
def step_validate_and_update(current_config: dict, ai_result: dict,
                              skip_real: bool = False, force: bool = False) -> dict:
    log("STEP 4 — 전략 검증 및 업데이트 결정")

    if not ai_result.get("should_update"):
        log("  AI가 변경 불필요 판단 → 현재 전략 유지")
        return current_config

    suggested = ai_result.get("suggested_config", current_config)

    if force:
        log("  FORCE 모드 — 검증 없이 즉시 적용", "WARN")
        save_config(suggested, reason=f"[FORCE] {ai_result.get('reasoning', '')}")
        return suggested

    # 검증
    val_result = validate_strategy(suggested, skip_real=skip_real)

    if val_result["passed"]:
        reason = f"AI 제안 검증 통과 | {ai_result.get('reasoning', '')}"
        save_config(suggested, reason=reason)
        log(f"  전략 업데이트 완료 → v{suggested.get('version', '?') + 1}")
        return suggested
    else:
        log(f"  검증 실패 → 현재 전략 유지 ({val_result['summary']})", "WARN")
        return current_config


# ── STEP 5: 리스크 오버레이 ───────────────────────────────────────────────────
def step_risk_overlay(perf: dict) -> dict:
    log("STEP 5 — 리스크 오버레이 점검")
    kospi_data  = get_kospi_ma200()
    risk_signal = get_risk_signal(kospi_data)
    stop_sigs   = check_individual_stop_loss(perf["holdings"])
    risk_report = generate_risk_report(kospi_data, risk_signal, stop_sigs)
    print(risk_report)
    log(f"  시장 신호: {risk_signal['signal']}  괴리율: {risk_signal['gap_pct']:+.2f}%")
    return risk_signal


# ── STEP 6: 팩터 스크리닝 + 리밸런싱 ─────────────────────────────────────────
def step_rebalance(config: dict, risk_signal: dict) -> dict:
    if risk_signal["signal"] == "STRONG_BEAR":
        log("  STRONG_BEAR 신호 — 리밸런싱 스킵, 현금 비중 유지", "WARN")
        return {}

    log("STEP 6 — 팩터 스크리닝")
    screening = run_screening(config=config)

    log("STEP 6 — 포트폴리오 리밸런싱")
    trade_result = rebalance(screening)

    log(f"  매도 {len(trade_result['trades']['sell'])}  "
        f"매수 {len(trade_result['trades']['buy'])}  "
        f"유지 {len(trade_result['trades']['hold'])}  "
        f"누적수익 {trade_result['pnl_pct']:+.2f}%")
    return trade_result


# ── STEP 7: 리포트 생성 + 로그 ────────────────────────────────────────────────
def step_report(config: dict, perf: dict, screening: dict,
                risk_signal: dict, trade_result: dict,
                sim_metrics: dict, ai_result: dict):
    log("STEP 7 — 주간 리포트 생성")

    # report_generator는 기존 포맷 유지
    if screening and trade_result:
        report = generate_weekly_report(perf, screening, risk_signal,
                                        trade_result.get("trades", {}))
        print(report)

    # JSON 로그 저장
    run_data = {
        "date":             date.today().isoformat(),
        "strategy_version": config.get("version"),
        "risk_signal":      risk_signal,
        "sim_metrics":      sim_metrics,
        "ai_analysis": {
            "should_update":    ai_result.get("should_update"),
            "reasoning":        ai_result.get("reasoning"),
            "market_assessment": ai_result.get("market_assessment"),
        },
        "trades":       trade_result.get("trades", {}),
        "performance": {
            "total_value": perf.get("total_value"),
            "pnl_pct":     perf.get("pnl_pct"),
        },
    }
    log_path = os.path.join(LOGS_DIR, f"weekly_{date.today().isoformat()}.json")
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(run_data, f, ensure_ascii=False, indent=2)
    log(f"  로그 저장: {log_path}")


# ── 메인 파이프라인 ───────────────────────────────────────────────────────────
def run_weekly(use_ai: bool = True, use_real: bool = True, force: bool = False):
    bar = "=" * 58
    print(f"\n{bar}")
    print(f"  주간 자동화 파이프라인  —  {date.today()}")
    print(f"{bar}")

    config = get_config()
    log(f"전략 v{config['version']} 로드 완료  ({config.get('update_reason', '')})")

    # 1. 성과 수집
    perf = step_collect_performance()

    # 2. 모의 백테스트
    sim_metrics = step_sim_backtest(config)

    # 3. AI 분석
    if use_ai:
        ai_result = step_ai_analysis(config, sim_metrics, perf)
    else:
        log("STEP 3 — AI 분석 스킵 (--no-ai)")
        ai_result = {"should_update": False, "reasoning": "스킵", "suggested_config": config, "suggested_changes": {}}

    # 4. 검증 + 업데이트
    config = step_validate_and_update(config, ai_result,
                                       skip_real=not use_real, force=force)

    # 5. 리스크 오버레이
    risk_signal = step_risk_overlay(perf)

    # 6. 리밸런싱
    trade_result = step_rebalance(config, risk_signal)

    # 7. 리포트
    perf_updated = get_performance_summary()
    screening    = {}
    if trade_result:
        from factor_engine import run_screening
        screening = run_screening(config=config)

    step_report(config, perf_updated, screening, risk_signal, trade_result, sim_metrics, ai_result)

    print(f"\n{bar}")
    log("파이프라인 완료")
    print(f"{bar}\n")


def print_report():
    """현재 성과만 출력"""
    init_db()
    perf = get_performance_summary()
    cfg  = get_config()
    print(f"""
{'='*52}
  현재 포트폴리오 성과  (전략 v{cfg['version']})
{'='*52}
  초기 자본  : {perf['initial_capital']:>12,}원
  현재 평가액: {perf['total_value']:>12,}원
  누적 손익  : {perf['pnl']:>+12,}원  ({perf['pnl_pct']:+.2f}%)
  현금       : {perf['cash']:>12,}원
  보유 종목  : {perf['holdings_count']}개
  총 거래 수  : {perf['transaction_count']}건
{'='*52}""")
    if perf["holdings"]:
        print("  보유 종목:")
        for h in perf["holdings"]:
            print(f"    {h['name']} ({h['ticker']}) — {h['shares']}주 @ {h['avg_price']:,}원")
    print()
    print_history()


# ── 진입점 ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    args = sys.argv[1:]

    if "--report" in args:
        print_report()
    else:
        use_ai   = "--no-ai"   not in args
        use_real = "--no-real" not in args
        force    = "--force-update" in args

        if force:
            print("  ⚠ FORCE 모드: 검증 없이 AI 제안을 즉시 적용합니다.")

        run_weekly(use_ai=use_ai, use_real=use_real, force=force)
