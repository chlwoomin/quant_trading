"""
scheduler.py
메인 스케줄러 — 전체 파이프라인 조율
매주 일요일 22:00 자동 실행 (cron: 0 22 * * 0)

사용법:
  python scheduler.py          # 즉시 1회 실행
  python scheduler.py --demo   # 3주치 시뮬레이션
  python scheduler.py --report # 현재 성과 리포트만 출력
"""

import sys
import os
import json
import time
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))

from factor_engine      import run_screening
from portfolio_manager  import init_db, rebalance, get_performance_summary
from risk_overlay       import get_kospi_ma200, get_risk_signal, \
                               check_individual_stop_loss, generate_risk_report
from report_generator   import generate_weekly_report


LOGS_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOGS_DIR, exist_ok=True)


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(os.path.join(LOGS_DIR, "scheduler.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ── 전체 파이프라인 ────────────────────────────────────────────────────────────
def run_pipeline():
    log("파이프라인 시작")

    # 1단계: DB 초기화
    init_db()

    # 2단계: 리스크 오버레이 먼저 체크
    log("리스크 오버레이 점검 중...")
    kospi_data  = get_kospi_ma200()
    risk_signal = get_risk_signal(kospi_data)
    perf        = get_performance_summary()
    stop_sigs   = check_individual_stop_loss(perf["holdings"])
    risk_report = generate_risk_report(kospi_data, risk_signal, stop_sigs)
    print(risk_report)

    # 시장 신호가 STRONG_BEAR 면 리밸런싱 스킵
    if risk_signal["signal"] == "STRONG_BEAR":
        log("STRONG_BEAR 신호 — 이번 주 리밸런싱 스킵, 현금 비중 유지")
        return

    # 3단계: 팩터 스크리닝
    log("멀티팩터 스크리닝 중...")
    screening = run_screening()

    # 4단계: 리밸런싱
    log("가상 포트폴리오 리밸런싱 중...")
    trade_result = rebalance(screening)

    print(f"""
  ─────────────────────────────────────
  리밸런싱 결과
  ─────────────────────────────────────
  매도  : {len(trade_result['trades']['sell'])}종목
  매수  : {len(trade_result['trades']['buy'])}종목
  유지  : {len(trade_result['trades']['hold'])}종목
  현금  : {trade_result['cash']:>12,}원
  평가액: {trade_result['total_value']:>12,}원
  누적수익: {trade_result['pnl_pct']:+.2f}%
  ─────────────────────────────────────""")

    # 5단계: 주간 리포트 생성
    log("주간 리포트 생성 중...")
    perf_updated = get_performance_summary()
    report = generate_weekly_report(
        perf_updated, screening, risk_signal, trade_result["trades"]
    )
    print(report)

    # 결과 저장
    result_path = os.path.join(
        LOGS_DIR, f"run_{date.today().isoformat()}.json"
    )
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump({
            "date":        date.today().isoformat(),
            "risk_signal": risk_signal,
            "trades":      trade_result["trades"],
            "performance": {
                "total_value": perf_updated["total_value"],
                "pnl_pct":     perf_updated["pnl_pct"],
            },
            "top10_selected": screening["selected"][:10],
        }, f, ensure_ascii=False, indent=2)

    log(f"완료 — 결과 저장: {result_path}")
    return trade_result


# ── 3주 데모 시뮬레이션 ───────────────────────────────────────────────────────
def run_demo():
    print("\n" + "="*52)
    print("  가상 포트폴리오 자동화 시스템 — 3주 데모")
    print("="*52)
    init_db()
    for week in range(1, 4):
        print(f"\n\n{'▶'*3}  {week}주차 실행  {'◀'*3}")
        run_pipeline()
        if week < 3:
            print("\n  (다음 주 시뮬레이션 중...)\n")
            time.sleep(1)

    print("\n\n" + "="*52)
    print("  최종 성과 요약")
    print("="*52)
    perf = get_performance_summary()
    print(f"  초기 자본  : {perf['initial_capital']:>12,}원")
    print(f"  현재 평가액: {perf['total_value']:>12,}원")
    print(f"  누적 손익  : {perf['pnl']:>+12,}원  ({perf['pnl_pct']:+.2f}%)")
    print(f"  보유 종목  : {perf['holdings_count']}개")
    print(f"  총 거래 수  : {perf['transaction_count']}건")
    print("="*52)


# ── 성과 리포트만 출력 ────────────────────────────────────────────────────────
def print_report():
    init_db()
    perf = get_performance_summary()
    print(f"""
{'='*52}
  현재 가상 포트폴리오 성과
{'='*52}
  초기 자본  : {perf['initial_capital']:>12,}원
  현재 평가액: {perf['total_value']:>12,}원
  누적 손익  : {perf['pnl']:>+12,}원  ({perf['pnl_pct']:+.2f}%)
  현금       : {perf['cash']:>12,}원
  보유 종목  : {perf['holdings_count']}개
  총 거래 수  : {perf['transaction_count']}건
{'='*52}""")

    if perf["holdings"]:
        print("  보유 종목 목록:")
        for h in perf["holdings"]:
            print(f"    {h['name']} ({h['ticker']}) — {h['shares']}주 @ {h['avg_price']:,}원")


# ── 진입점 ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if "--demo" in sys.argv:
        run_demo()
    elif "--report" in sys.argv:
        print_report()
    else:
        run_pipeline()
