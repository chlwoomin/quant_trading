"""
report_generator.py
Claude API를 활용한 주간 포트폴리오 리포트 자동 생성
"""

import json
import os
import requests
from datetime import date


def generate_weekly_report(
    performance: dict,
    screening_result: dict,
    risk_signal: dict,
    trades: dict,
) -> str:
    """
    Claude API에게 데이터를 전달하고 자연어 리포트를 생성합니다.
    실제 환경에서는 ANTHROPIC_API_KEY 환경변수가 필요합니다.
    """

    prompt = f"""
당신은 국내 주식 퀀트 투자 전문가입니다.
아래 가상 포트폴리오 데이터를 분석하여 간결한 주간 리포트를 작성해주세요.

## 포트폴리오 현황
- 평가 날짜: {date.today().isoformat()}
- 초기 자본: {performance['initial_capital']:,}원
- 현재 평가액: {performance['total_value']:,}원
- 손익: {performance['pnl']:,}원 ({performance['pnl_pct']:+.2f}%)
- 현금: {performance['cash']:,}원
- 보유 종목 수: {performance['holdings_count']}개

## 이번 주 거래
- 매도: {len(trades.get('sell', []))}건
- 매수: {len(trades.get('buy', []))}건
- 유지: {len(trades.get('hold', []))}건

## 시장 리스크 신호
- KOSPI: {risk_signal['kospi']:,.2f}
- 200일 MA: {risk_signal['ma200']:,.2f}
- 신호: {risk_signal['signal']} — {risk_signal['description']}

## 분석 요청
다음 세 가지를 200자 이내로 각각 작성해주세요:
1. 이번 주 포트폴리오 평가 (한 문장)
2. 주요 리스크 요인 (한 문장)
3. 다음 주 주의사항 (한 문장)
"""

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        # API 키 없을 때 샘플 리포트 반환
        return _sample_report(performance, risk_signal, trades)

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 500,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        data = resp.json()
        return data["content"][0]["text"]
    except Exception as e:
        return f"[리포트 생성 오류: {e}]\n" + _sample_report(performance, risk_signal, trades)


def _sample_report(performance, risk_signal, trades) -> str:
    pnl = performance["pnl_pct"]
    signal = risk_signal["signal"]
    direction = "상승" if pnl > 0 else "하락"
    return f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  주간 포트폴리오 리포트 — {date.today().isoformat()}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. 이번 주 평가
   멀티팩터 가상 포트폴리오가 누적 {pnl:+.2f}% {direction}하며
   초기 대비 {'양호한' if pnl > 0 else '부진한'} 성과를 기록했습니다.

2. 주요 리스크 요인
   시장 신호가 {signal}로, {"현재 정상 운용 구간입니다." if "BULL" in signal or "NEUTRAL" in signal else "방어적 포지션 유지가 필요합니다."}

3. 다음 주 주의사항
   {"KOSPI 200일 이동평균 하회 시 현금 비중을 50%로 확대하고, 손절 기준(-8%)을 철저히 준수하세요." if "BEAR" in signal or "CAUTION" in signal else "팩터 스코어 상위 유지 여부를 점검하고 리밸런싱 필요 종목을 확인하세요."}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
