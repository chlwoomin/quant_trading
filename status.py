"""
status.py
실시간 포트폴리오 현황 대시보드

표시 내용:
  - 포트폴리오 총 평가액 / 손익 (실시간 가격 반영)
  - 보유 종목별 현재가 / 수익률
  - 현재 전략 파라미터 + 시장 레짐
  - 최근 거래 내역

사용법:
  python status.py          # 현황 출력
  python status.py --watch  # 30초마다 자동 갱신
"""

import os
import sys
import sqlite3
import time
from datetime import date, datetime

from portfolio_manager import DB_PATH, INITIAL_CASH, BROKER_MODE
from strategy_manager  import get_config
from price_updater     import get_current_price, PYKRX_OK


# ── 실시간 보유 종목 조회 ─────────────────────────────────────────────────────
def get_live_holdings() -> list:
    """
    DB 보유 종목에 실시간 현재가를 반영합니다.
    pykrx 불가 시 DB 저장가를 사용합니다.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    holdings = [dict(r) for r in conn.execute(
        "SELECT * FROM holdings WHERE shares > 0 ORDER BY name"
    ).fetchall()]
    conn.close()

    for h in holdings:
        live = get_current_price(h["ticker"]) if PYKRX_OK else None
        h["live_price"]   = live or h.get("current_price") or h["avg_price"]
        h["live_value"]   = round(h["shares"] * h["live_price"])
        h["pnl_pct"]      = round((h["live_price"] / h["avg_price"] - 1) * 100, 2)
        h["pnl_amt"]      = round((h["live_price"] - h["avg_price"]) * h["shares"])
        h["price_source"] = "실시간" if live else "저장가"

    return holdings


# ── 포트폴리오 요약 ───────────────────────────────────────────────────────────
def get_portfolio_summary(holdings: list) -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    state = dict(conn.execute(
        "SELECT * FROM portfolio_state ORDER BY id DESC LIMIT 1"
    ).fetchone())
    conn.close()

    cash        = state["cash"]
    stock_value = sum(h["live_value"] for h in holdings)
    total_value = cash + stock_value
    pnl         = total_value - INITIAL_CASH
    pnl_pct     = pnl / INITIAL_CASH * 100

    return {
        "total_value": round(total_value),
        "cash":        round(cash),
        "stock_value": round(stock_value),
        "pnl":         round(pnl),
        "pnl_pct":     round(pnl_pct, 2),
        "n_holdings":  len(holdings),
    }


# ── 시장 레짐 ─────────────────────────────────────────────────────────────────
def get_regime() -> dict:
    try:
        from risk_overlay import get_risk_signal as _get
        return _get()
    except Exception:
        return {"signal": "N/A", "gap_pct": 0}


# ── 최근 거래 ─────────────────────────────────────────────────────────────────
def get_recent_trades(n: int = 10) -> list:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM transactions ORDER BY id DESC LIMIT ?", (n,)
    ).fetchall()]
    conn.close()
    return rows


# ── 출력 ─────────────────────────────────────────────────────────────────────
def print_status():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    bar = "=" * 62

    holdings = get_live_holdings()
    summary  = get_portfolio_summary(holdings)
    config   = get_config()
    regime   = get_regime()
    trades   = get_recent_trades(8)

    pnl_sign = "+" if summary["pnl"] >= 0 else ""
    pnl_color_on  = "\033[92m" if summary["pnl"] >= 0 else "\033[91m"
    pnl_color_off = "\033[0m"

    regime_color = {
        "STRONG_BULL": "\033[92m", "NEUTRAL": "\033[93m",
        "CAUTION": "\033[93m",     "STRONG_BEAR": "\033[91m",
    }.get(regime.get("signal", ""), "")

    price_note = "실시간 (pykrx)" if PYKRX_OK else "저장가 (pykrx 미연결)"

    print(f"\n{bar}")
    print(f"  퀀트 시스템 현황   {now}   가격: {price_note}")
    print(f"  브로커 모드: {BROKER_MODE.upper()}")
    print(bar)

    # 포트폴리오 요약
    print(f"\n  [포트폴리오]")
    print(f"  총 평가액  : {summary['total_value']:>14,.0f}원")
    print(f"  누적 손익  : {pnl_color_on}{pnl_sign}{summary['pnl']:>+14,.0f}원  "
          f"({pnl_sign}{summary['pnl_pct']:.2f}%){pnl_color_off}")
    print(f"  주식 평가  : {summary['stock_value']:>14,.0f}원")
    print(f"  현금       : {summary['cash']:>14,.0f}원")
    print(f"  보유 종목  : {summary['n_holdings']}개")

    # 현재 전략
    fw = config.get("factor_weights", {})
    ro = config.get("risk_overlay",   {})
    pf = config.get("portfolio",      {})
    print(f"\n  [전략 v{config.get('version','?')}]")
    print(f"  팩터    : 퀄리티 {fw.get('quality',0):.0%}  "
          f"밸류 {fw.get('value',0):.0%}  "
          f"모멘텀 {fw.get('momentum',0):.0%}")
    print(f"  포트폴리오: 상위 {pf.get('top_n',0)}종목  "
          f"스탑로스 {pf.get('stop_loss_pct',0):.0%}  "
          f"주식비중 {pf.get('equity_budget',0):.0%}")
    signal_str = regime.get('signal', 'N/A')
    gap_pct    = regime.get('gap_pct', 0)
    print(f"  시장 레짐 : {regime_color}{signal_str}  "
          f"(KOSPI 200MA {gap_pct:+.2f}%)\033[0m")

    # 보유 종목 테이블
    if holdings:
        print(f"\n  [보유 종목]")
        print(f"  {'종목명':<12} {'티커':>7} {'수량':>6} "
              f"{'평균단가':>10} {'현재가':>10} {'수익률':>8} {'평가금액':>12}")
        print(f"  {'-'*68}")
        for h in holdings:
            color = "\033[92m" if h["pnl_pct"] >= 0 else "\033[91m"
            sign  = "+" if h["pnl_pct"] >= 0 else ""
            print(f"  {h['name']:<12} {h['ticker']:>7} {h['shares']:>6,}주 "
                  f"{h['avg_price']:>10,.0f} {h['live_price']:>10,.0f} "
                  f"{color}{sign}{h['pnl_pct']:>6.2f}%\033[0m "
                  f"{h['live_value']:>12,.0f}")
    else:
        print(f"\n  보유 종목 없음")

    # 최근 거래
    if trades:
        print(f"\n  [최근 거래]")
        print(f"  {'날짜':<12} {'구분':>5} {'종목':>10} "
              f"{'수량':>6} {'가격':>10} {'금액':>13}")
        print(f"  {'-'*60}")
        for t in trades:
            sign = "매수" if t["type"] == "BUY" else "매도"
            color = "\033[92m" if t["type"] == "BUY" else "\033[91m"
            print(f"  {t['date']:<12} {color}{sign}\033[0m "
                  f"{t['name']:>10} {t['shares']:>6,}주 "
                  f"{t['price']:>10,.0f} {t['amount']:>13,.0f}")

    print(f"\n{bar}\n")


# ── 메인 ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    watch = "--watch" in sys.argv
    interval = 30

    if "--interval" in sys.argv:
        idx = sys.argv.index("--interval")
        interval = int(sys.argv[idx + 1])

    if watch:
        print(f"  자동 갱신 모드: {interval}초마다 업데이트  (Ctrl+C 종료)")
        while True:
            os.system("cls" if os.name == "nt" else "clear")
            print_status()
            time.sleep(interval)
    else:
        print_status()
