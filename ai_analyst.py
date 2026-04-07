"""
ai_analyst.py
Claude API를 활용한 주간 전략 분석 및 파라미터 개선 제안

역할:
  - 지난주 포트폴리오 성과 + 백테스트 지표를 분석
  - 팩터 가중치, 필터, 리스크 오버레이 파라미터 조정 제안
  - JSON 구조화 응답으로 자동화 파이프라인에 연결

의존성:
  pip install anthropic
"""

import json
import os
import copy
from datetime import datetime

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False


# ── 분석 프롬프트 ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """당신은 한국 주식 멀티팩터 투자 전략을 관리하는 퀀트 애널리스트입니다.

담당 전략:
- 퀄리티(GP/A + ROE) + 밸류(저PBR + 저PER) + 모멘텀(12M-1M) 팩터
- KOSPI 200일 이동평균 기반 절대 모멘텀 리스크 오버레이
- 주간 리밸런싱, 동일 비중, 개별 스탑로스

분석 원칙:
1. 작은 변화를 권장하세요. 한 번에 가중치를 ±10%p 이상 바꾸지 마세요.
2. 최근 시장 레짐(KOSPI 상황)을 반드시 고려하세요.
3. 데이터가 부족하면(백테스트 기간 < 1년) should_update=false를 반환하세요.
4. 검증 지표(Sharpe, MDD, Alpha)가 이미 양호하면 변경을 자제하세요.

응답은 반드시 다음 JSON 형식으로만 답하세요. 다른 텍스트는 포함하지 마세요:
{
  "should_update": true 또는 false,
  "reasoning": "변경 이유 또는 유지 이유 (한국어, 2-3문장)",
  "market_assessment": "현재 시장 상황 평가 (한국어, 1문장)",
  "suggested_changes": {
    "factor_weights": {"quality": 0.35, "value": 0.35, "momentum": 0.30},
    "filters": {"min_roe": 0.05, "max_debt_ratio": 2.0, "min_market_cap_억": 500},
    "portfolio": {"stop_loss_pct": -0.08, "top_n": 25},
    "risk_overlay": {"strong_bull_gap_pct": 5.0, "strong_bear_gap_pct": -10.0}
  }
}
suggested_changes에는 변경이 필요한 항목만 포함하세요 (변경 없는 항목은 생략).
"""


def _build_user_message(config: dict, backtest_metrics: dict, portfolio_perf: dict,
                         regime_history: dict = None) -> str:
    """분석 요청 메시지를 구성합니다."""

    fw = config["factor_weights"]
    fi = config["filters"]
    po = config["portfolio"]
    ro = config["risk_overlay"]

    lines = [
        f"## 현재 전략 설정 (v{config.get('version', '?')})",
        f"팩터 가중치: 퀄리티 {fw['quality']:.0%}  밸류 {fw['value']:.0%}  모멘텀 {fw['momentum']:.0%}",
        f"필터: 시총 ≥ {fi['min_market_cap_억']}억  부채비율 ≤ {fi['max_debt_ratio']:.0%}  ROE ≥ {fi['min_roe']:.0%}",
        f"포트폴리오: 상위 {po['top_n']}종목  스탑로스 {po['stop_loss_pct']:.0%}",
        f"리스크 오버레이: 강세 기준 {ro['strong_bull_gap_pct']:+.1f}%  약세 기준 {ro['strong_bear_gap_pct']:+.1f}%",
        "",
        "## 백테스트 성과 지표",
    ]

    if backtest_metrics:
        lines += [
            f"누적 수익률: {backtest_metrics.get('total_return_pct', 'N/A'):+.2f}%",
            f"벤치마크(KOSPI): {backtest_metrics.get('bm_return_pct', 'N/A'):+.2f}%",
            f"초과 수익률(Alpha): {backtest_metrics.get('alpha_pct', 'N/A'):+.2f}%",
            f"연환산 수익률: {backtest_metrics.get('ann_return_pct', 'N/A'):+.2f}%",
            f"연환산 변동성: {backtest_metrics.get('volatility_pct', 'N/A'):.2f}%",
            f"샤프 비율: {backtest_metrics.get('sharpe', 'N/A'):.3f}",
            f"최대 낙폭(MDD): {backtest_metrics.get('max_dd_pct', 'N/A'):+.2f}%",
            f"벤치마크 대비 주간 승률: {backtest_metrics.get('win_rate_pct', 'N/A'):.1f}%",
            f"스탑로스 발동 횟수: {backtest_metrics.get('stop_losses', 'N/A')}건",
            f"백테스트 기간: {backtest_metrics.get('n_weeks', 'N/A')}주",
        ]
    else:
        lines.append("백테스트 데이터 없음")

    lines += ["", "## 현재 포트폴리오 상태"]
    if portfolio_perf:
        lines += [
            f"현재 평가액: {portfolio_perf.get('total_value', 0):,.0f}원",
            f"누적 손익: {portfolio_perf.get('pnl_pct', 0):+.2f}%",
            f"보유 종목: {portfolio_perf.get('holdings_count', 0)}개",
            f"총 거래 수: {portfolio_perf.get('transaction_count', 0)}건",
        ]
    else:
        lines.append("포트폴리오 데이터 없음 (초기 상태)")

    if regime_history:
        lines += ["", "## 최근 시장 레짐"]
        for regime, weeks in regime_history.items():
            lines.append(f"  {regime}: {weeks}주")

    lines += [
        "",
        f"분석 일시: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "위 정보를 바탕으로 전략 파라미터 조정 여부를 판단하고 JSON 응답을 반환하세요.",
    ]

    return "\n".join(lines)


def analyze_and_suggest(
    config: dict,
    backtest_metrics: dict,
    portfolio_perf: dict,
    regime_history: dict = None,
) -> dict:
    """
    Claude API로 전략을 분석하고 파라미터 개선을 제안합니다.

    Returns:
        {
            "should_update": bool,
            "reasoning": str,
            "market_assessment": str,
            "suggested_config": dict (변경 적용된 전체 config),
            "suggested_changes": dict (변경된 항목만),
            "raw_response": str
        }
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")

    if not ANTHROPIC_AVAILABLE or not api_key:
        return _fallback_analysis(config, backtest_metrics)

    client = anthropic.Anthropic(api_key=api_key)

    user_msg = _build_user_message(config, backtest_metrics, portfolio_perf, regime_history)

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = response.content[0].text.strip()

        # JSON 파싱
        parsed = _parse_response(raw)
        suggested_config = _apply_changes(config, parsed.get("suggested_changes", {}))

        return {
            "should_update":    parsed.get("should_update", False),
            "reasoning":        parsed.get("reasoning", ""),
            "market_assessment": parsed.get("market_assessment", ""),
            "suggested_config": suggested_config,
            "suggested_changes": parsed.get("suggested_changes", {}),
            "raw_response":     raw,
        }

    except Exception as e:
        print(f"  AI 분석 오류: {e}")
        return _fallback_analysis(config, backtest_metrics)


def _parse_response(raw: str) -> dict:
    """Claude 응답에서 JSON을 추출합니다."""
    # 코드 블록 제거
    text = raw
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]

    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        # 중괄호 범위 추출 시도
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except Exception:
                pass
    return {"should_update": False, "reasoning": "응답 파싱 실패", "suggested_changes": {}}


def _apply_changes(base_config: dict, changes: dict) -> dict:
    """
    변경 사항을 기존 config에 병합합니다.
    중첩 dict는 딥 머지, 없는 키는 무시합니다.
    """
    result = copy.deepcopy(base_config)

    section_map = {
        "factor_weights": "factor_weights",
        "filters":        "filters",
        "portfolio":      "portfolio",
        "risk_overlay":   "risk_overlay",
    }

    for section_key, config_key in section_map.items():
        if section_key in changes and config_key in result:
            for k, v in changes[section_key].items():
                if k in result[config_key]:
                    result[config_key][k] = v

    # 팩터 가중치 합이 1이 되도록 정규화
    if "factor_weights" in changes:
        fw  = result["factor_weights"]
        total = sum(fw.values())
        if total > 0 and abs(total - 1.0) > 0.01:
            result["factor_weights"] = {k: round(v / total, 4) for k, v in fw.items()}

    return result


def _fallback_analysis(config: dict, backtest_metrics: dict) -> dict:
    """
    API 미연동 시 규칙 기반 폴백 분석.
    샤프 < 0.2 이면 모멘텀 비중을 소폭 조정 제안.
    """
    should_update = False
    reason        = "API 미연동 — 규칙 기반 분석"
    changes       = {}

    if backtest_metrics:
        sharpe  = backtest_metrics.get("sharpe", 0)
        sl_cnt  = backtest_metrics.get("stop_losses", 0)
        n_weeks = backtest_metrics.get("n_weeks", 0)

        if n_weeks < 52:
            reason = "백테스트 기간 부족 (< 1년) — 전략 유지"
        elif sharpe < 0.1:
            should_update = True
            reason = f"샤프 비율 {sharpe:.2f} 미달 — 모멘텀 비중 축소, 퀄리티 확대 제안"
            changes = {"factor_weights": {"quality": 0.40, "value": 0.35, "momentum": 0.25}}
        elif sl_cnt > 20:
            should_update = True
            reason = f"스탑로스 {sl_cnt}건 과다 — 스탑로스 기준 완화 제안"
            changes = {"portfolio": {"stop_loss_pct": -0.10}}
        else:
            reason = f"현재 전략 양호 (Sharpe {sharpe:.2f}) — 유지"

    suggested_config = _apply_changes(config, changes) if changes else copy.deepcopy(config)

    return {
        "should_update":    should_update,
        "reasoning":        reason,
        "market_assessment": "API 미연동으로 시장 평가 불가",
        "suggested_config": suggested_config,
        "suggested_changes": changes,
        "raw_response":     "",
    }


if __name__ == "__main__":
    from strategy_manager import get_config

    cfg = get_config()
    sample_metrics = {
        "total_return_pct": 11.78,
        "bm_return_pct":    9.78,
        "alpha_pct":        2.00,
        "ann_return_pct":   5.73,
        "volatility_pct":   11.31,
        "sharpe":           0.24,
        "max_dd_pct":       -9.76,
        "win_rate_pct":     57.3,
        "stop_losses":      17,
        "n_weeks":          104,
    }
    sample_perf = {"total_value": 9_948_554, "pnl_pct": -0.51, "holdings_count": 12, "transaction_count": 8}

    print("AI 전략 분석 실행 중...")
    result = analyze_and_suggest(cfg, sample_metrics, sample_perf)
    print(f"\n변경 여부: {result['should_update']}")
    print(f"시장 평가: {result['market_assessment']}")
    print(f"판단 근거: {result['reasoning']}")
    if result["suggested_changes"]:
        print(f"제안 변경: {json.dumps(result['suggested_changes'], ensure_ascii=False, indent=2)}")
