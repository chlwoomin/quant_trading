"""
risk_overlay.py
절대모멘텀 리스크 오버레이: KOSPI 200일 이동평균 기반 현금 전환 신호
"""

import random
from datetime import datetime, timedelta
from typing import Dict, Tuple


def get_kospi_ma200(date_str: str = None) -> Dict:
    """
    실제 환경: FinanceDataReader.DataReader('KS11') 사용
    여기서는 시뮬레이션 데이터 반환
    """
    # 시뮬레이션: KOSPI 현재가와 200일 MA
    kospi_current = random.uniform(2300, 2800)
    ma200 = random.uniform(2400, 2600)
    above_ma = kospi_current > ma200
    gap_pct = (kospi_current - ma200) / ma200 * 100

    return {
        "date":          date_str or datetime.today().strftime("%Y-%m-%d"),
        "kospi_current": round(kospi_current, 2),
        "ma200":         round(ma200, 2),
        "above_ma200":   above_ma,
        "gap_pct":       round(gap_pct, 2),
    }


def get_risk_signal(kospi_data: Dict) -> Dict:
    """
    리스크 신호 판단:
    - KOSPI > 200일 MA  → 정상 운용 (주식 100%)
    - KOSPI < 200일 MA  → 방어 모드 (주식 50%, 현금 50%)
    """
    above = kospi_data["above_ma200"]
    gap   = kospi_data["gap_pct"]

    if above:
        if gap > 5:
            signal = "STRONG_BULL"
            equity_ratio = 1.00
            desc = "강세장 — 풀 포지션 유지"
        else:
            signal = "NEUTRAL"
            equity_ratio = 1.00
            desc = "중립 — 정상 운용"
    else:
        if gap < -10:
            signal = "STRONG_BEAR"
            equity_ratio = 0.30
            desc = "강한 약세장 — 주식 30%, 현금 70%"
        else:
            signal = "CAUTION"
            equity_ratio = 0.50
            desc = "주의 — 주식 50%, 현금 50%"

    return {
        "signal":       signal,
        "equity_ratio": equity_ratio,
        "cash_ratio":   round(1 - equity_ratio, 2),
        "description":  desc,
        "kospi":        kospi_data["kospi_current"],
        "ma200":        kospi_data["ma200"],
        "gap_pct":      kospi_data["gap_pct"],
    }


def check_individual_stop_loss(holdings: list) -> list:
    """
    개별 종목 손절 체크: 매수가 대비 -8% 이하 시 매도 신호
    """
    stop_signals = []
    for h in holdings:
        if h.get("current_price") and h.get("avg_price"):
            loss_pct = (h["current_price"] - h["avg_price"]) / h["avg_price"] * 100
            if loss_pct <= -8:
                stop_signals.append({
                    "ticker":   h["ticker"],
                    "name":     h["name"],
                    "loss_pct": round(loss_pct, 2),
                    "action":   "STOP_LOSS"
                })
    return stop_signals


def generate_risk_report(kospi_data: Dict, signal: Dict, stop_signals: list) -> str:
    lines = [
        "=" * 52,
        f"  리스크 오버레이 점검 — {kospi_data['date']}",
        "=" * 52,
        f"  KOSPI 현재가 : {kospi_data['kospi_current']:,.2f}",
        f"  200일 MA     : {kospi_data['ma200']:,.2f}",
        f"  괴리율       : {kospi_data['gap_pct']:+.2f}%",
        f"  시장 신호    : {signal['signal']}",
        f"  판단         : {signal['description']}",
        f"  권장 주식 비중: {signal['equity_ratio']*100:.0f}%  "
        f"현금: {signal['cash_ratio']*100:.0f}%",
        "-" * 52,
    ]
    if stop_signals:
        lines.append(f"  ⚠ 손절 신호 {len(stop_signals)}건:")
        for s in stop_signals:
            lines.append(f"    - {s['name']} ({s['ticker']}): {s['loss_pct']:.2f}%")
    else:
        lines.append("  손절 신호: 없음")
    lines.append("=" * 52)
    return "\n".join(lines)
