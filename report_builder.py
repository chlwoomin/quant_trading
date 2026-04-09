"""
report_builder.py
주간 리포트 빌더 — HTML(이메일) + 텍스트(텔레그램) 두 가지 형식으로 생성

리포트 3섹션:
  1. 성과보고서    — 평가액, 손익, 보유 종목, 주간 수익
  2. AI 분석 결과  — 시장 레짐, AI 판단, 주요 분석
  3. 전략 업데이트 제안서 — 변경 여부, before/after 비교
"""

from datetime import date


# ── 텍스트 리포트 (디스코드용) ────────────────────────────────────────────────
def build_text_report(data: dict) -> str:
    """디스코드 Markdown 형식 리포트 (2000자 청크 분할은 notifier에서 처리)"""
    d           = data.get("date", date.today().isoformat())
    perf        = data.get("performance", {})
    metrics     = data.get("sim_metrics", {})
    risk        = data.get("risk_signal", {})
    ai          = data.get("ai_analysis", {})
    upd         = data.get("strategy_update", {})
    trades      = data.get("trades", {})
    ver         = data.get("strategy_version", "?")
    cfg         = data.get("current_config", {})

    regime_emoji = {
        "STRONG_BULL": "🟢", "NEUTRAL": "🟡",
        "CAUTION": "🟠", "STRONG_BEAR": "🔴",
    }.get(risk.get("signal", ""), "⚪")

    lines = [
        f"📊 *퀀트 시스템 주간 리포트*",
        f"📅 {d}  |  전략 v{ver}",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "📈 *1. 성과보고서*",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"• 현재 평가액: `{perf.get('total_value', 0):>12,.0f}원`",
        f"• 누적 손익:  `{perf.get('pnl', 0):>+12,.0f}원` ({perf.get('pnl_pct', 0):+.2f}%)",
        f"• 현금 비중:  `{perf.get('cash', 0):>12,.0f}원`",
        f"• 보유 종목:  {perf.get('holdings_count', 0)}개",
        f"• 총 거래 수: {perf.get('transaction_count', 0)}건",
    ]

    # 백테스트 요약
    if metrics:
        lines += [
            "",
            f"📉 *백테스트 지표 (2년 시뮬레이션)*",
            f"• 누적 수익률: {metrics.get('total_return_pct', 0):+.2f}%  (벤치마크 {metrics.get('bm_return_pct', 0):+.2f}%)",
            f"• Alpha: {metrics.get('alpha_pct', 0):+.2f}%  |  Sharpe: {metrics.get('sharpe', 0):.3f}",
            f"• MDD: {metrics.get('max_dd_pct', 0):.1f}%  |  주간 승률: {metrics.get('win_rate_pct', 0):.1f}%",
        ]

    # 이번 주 매매
    sells = trades.get("sell", [])
    buys  = trades.get("buy",  [])
    hold  = trades.get("hold", [])
    lines += [
        "",
        f"🔄 *이번 주 매매*: 매도 {len(sells)}  매수 {len(buys)}  유지 {len(hold)}",
    ]
    if sells:
        lines.append("  매도: " + ", ".join(t.get("name", t.get("ticker", "?")) for t in sells[:5]))
    if buys:
        lines.append("  매수: " + ", ".join(t.get("name", t.get("ticker", "?")) for t in buys[:5]))
    errors = trades.get("errors", [])
    if errors:
        lines.append("  ⚠ 오류: " + ", ".join(errors[:3]))

    # AI 분석
    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "🤖 *2. AI 시장 분석*",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"{regime_emoji} 시장 레짐: *{risk.get('signal', 'N/A')}*  (KOSPI 200MA {risk.get('gap_pct', 0):+.2f}%)",
    ]
    if ai.get("market_assessment"):
        lines.append(f"💡 {ai['market_assessment']}")
    if ai.get("reasoning"):
        lines.append(f"\n📝 분석: {ai['reasoning']}")

    # 현재 전략 파라미터
    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "⚙️ *3. 현재 전략 파라미터*",
        "━━━━━━━━━━━━━━━━━━━━━━",
    ]
    if cfg:
        fw  = cfg.get("factor_weights", {})
        flt = cfg.get("filters", {})
        ro  = cfg.get("risk_overlay", {})
        pf  = cfg.get("portfolio", {})
        lines += [
            f"**팩터 가중치**: 퀄리티 {fw.get('quality', 0):.0%}  밸류 {fw.get('value', 0):.0%}  모멘텀 {fw.get('momentum', 0):.0%}",
            f"**스크리닝 필터**: 시총 ≥ {flt.get('min_market_cap_억', 0)}억  ROE ≥ {flt.get('min_roe', 0):.0%}  부채비율 ≤ {flt.get('max_debt_ratio', 0):.0f}배",
            f"**포트폴리오**: 상위 {pf.get('top_n', 0)}종목  스탑로스 {pf.get('stop_loss_pct', 0):.0%}  주식비중 {pf.get('equity_budget', 0):.0%}",
            f"**리스크 오버레이**: MA {ro.get('ma_weeks', 0)}주  경계({ro.get('caution_equity_ratio', 0):.0%}) / 약세({ro.get('strong_bear_equity_ratio', 0):.0%})",
        ]
    else:
        lines.append("  (전략 정보 없음)")

    # 전략 업데이트 결과
    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "🔄 *4. 전략 업데이트*",
        "━━━━━━━━━━━━━━━━━━━━━━",
    ]
    if upd.get("updated"):
        old_v = upd.get("old_version", "?")
        new_v = upd.get("new_version", "?")
        lines.append(f"⚡ *전략 업데이트: v{old_v} → v{new_v}*")
        changes = upd.get("changes", {})
        if "factor_weights" in changes:
            fw = changes["factor_weights"]
            lines.append(f"  팩터 가중치: 퀄리티 {fw.get('quality', 0):.0%}  밸류 {fw.get('value', 0):.0%}  모멘텀 {fw.get('momentum', 0):.0%}")
        if "portfolio" in changes:
            lines.append(f"  포트폴리오: {changes['portfolio']}")
        val = upd.get("validation", {})
        sim_ok  = "✓" if val.get("sim_passed")  else "✗"
        real_ok = "✓" if val.get("real_passed") else "✗  (스킵)" if val.get("real_skipped") else "✗"
        lines.append(f"  검증: 1차(모의) {sim_ok}  |  2차(실제) {real_ok}")
    else:
        lines.append(f"✅ 전략 변경 없음 (현재 v{ver} 유지)")
        if ai.get("reasoning"):
            lines.append(f"  이유: {ai.get('reasoning', '')[:100]}")

    lines += ["", f"_퀀트 시스템 자동 발송 — {d}_"]

    return "\n".join(lines)


# ── HTML 리포트 (이메일용) ─────────────────────────────────────────────────────
def build_html_report(data: dict) -> str:
    d       = data.get("date", date.today().isoformat())
    perf    = data.get("performance", {})
    metrics = data.get("sim_metrics", {})
    risk    = data.get("risk_signal", {})
    ai      = data.get("ai_analysis", {})
    upd     = data.get("strategy_update", {})
    trades  = data.get("trades", {})
    ver     = data.get("strategy_version", "?")

    regime_color = {
        "STRONG_BULL": "#22c55e", "NEUTRAL": "#eab308",
        "CAUTION": "#f97316",    "STRONG_BEAR": "#ef4444",
    }.get(risk.get("signal", ""), "#6b7280")

    pnl_pct  = perf.get("pnl_pct", 0)
    pnl_color = "#22c55e" if pnl_pct >= 0 else "#ef4444"

    sells = data.get("trades", {}).get("sell", [])
    buys  = data.get("trades", {}).get("buy",  [])
    hold  = data.get("trades", {}).get("hold", [])

    holdings_rows = ""
    for h in perf.get("holdings", [])[:15]:
        holdings_rows += f"""
        <tr>
          <td>{h.get('name','')}</td>
          <td style="font-family:monospace">{h.get('ticker','')}</td>
          <td style="text-align:right">{h.get('shares',0):,}주</td>
          <td style="text-align:right">{h.get('avg_price',0):,.0f}원</td>
        </tr>"""

    change_section = ""
    if upd.get("updated"):
        fw = upd.get("changes", {}).get("factor_weights", {})
        change_section = f"""
        <div style="background:#fef9c3;border-left:4px solid #eab308;padding:12px;margin:8px 0;border-radius:4px">
          <strong>⚡ 전략 업데이트: v{upd.get('old_version','?')} → v{upd.get('new_version','?')}</strong><br>
          {"팩터: 퀄리티 " + f"{fw.get('quality',0):.0%}" + " / 밸류 " + f"{fw.get('value',0):.0%}" + " / 모멘텀 " + f"{fw.get('momentum',0):.0%}" if fw else ""}
        </div>"""
    else:
        change_section = f"""
        <div style="background:#f0fdf4;border-left:4px solid #22c55e;padding:12px;margin:8px 0;border-radius:4px">
          ✅ 전략 변경 없음 (v{ver} 유지)
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="utf-8">
<style>
  body {{ font-family: 'Malgun Gothic', sans-serif; max-width: 700px; margin: 0 auto; background: #f8fafc; color: #1e293b; }}
  .card {{ background: white; border-radius: 12px; padding: 24px; margin: 16px 0; box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
  h1 {{ font-size: 1.4em; margin: 0 0 4px; }}
  h2 {{ font-size: 1.1em; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px; margin-top: 0; }}
  .metric {{ display: inline-block; background: #f1f5f9; border-radius: 8px; padding: 10px 16px; margin: 4px; text-align: center; }}
  .metric .val {{ font-size: 1.3em; font-weight: bold; }}
  .metric .lbl {{ font-size: 0.8em; color: #64748b; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.9em; }}
  th {{ background: #f1f5f9; padding: 8px; text-align: left; }}
  td {{ padding: 6px 8px; border-bottom: 1px solid #f1f5f9; }}
  .regime {{ display: inline-block; background: {regime_color}; color: white; border-radius: 20px; padding: 4px 14px; font-weight: bold; }}
  footer {{ text-align: center; color: #94a3b8; font-size: 0.8em; padding: 16px; }}
</style>
</head>
<body>
<div class="card">
  <h1>📊 퀀트 시스템 주간 리포트</h1>
  <div style="color:#64748b">{d} | 전략 v{ver}</div>
</div>

<div class="card">
  <h2>1. 성과보고서</h2>
  <div>
    <div class="metric"><div class="val">{perf.get('total_value',0):,.0f}원</div><div class="lbl">현재 평가액</div></div>
    <div class="metric"><div class="val" style="color:{pnl_color}">{pnl_pct:+.2f}%</div><div class="lbl">누적 수익률</div></div>
    <div class="metric"><div class="val">{perf.get('holdings_count',0)}개</div><div class="lbl">보유 종목</div></div>
    <div class="metric"><div class="val">{metrics.get('sharpe',0):.3f}</div><div class="lbl">Sharpe</div></div>
    <div class="metric"><div class="val">{metrics.get('max_dd_pct',0):.1f}%</div><div class="lbl">MDD</div></div>
    <div class="metric"><div class="val">{metrics.get('alpha_pct',0):+.1f}%</div><div class="lbl">Alpha</div></div>
  </div>

  <h3 style="margin-top:20px">이번 주 매매</h3>
  <p>매도 <b>{len(sells)}</b>종목 &nbsp;|&nbsp; 매수 <b>{len(buys)}</b>종목 &nbsp;|&nbsp; 유지 <b>{len(hold)}</b>종목</p>
  {"<p>매도: " + ", ".join(t.get("name","?") for t in sells[:8]) + "</p>" if sells else ""}
  {"<p>매수: " + ", ".join(t.get("name","?") for t in buys[:8])  + "</p>" if buys  else ""}

  <h3 style="margin-top:20px">보유 종목</h3>
  <table><tr><th>종목명</th><th>티커</th><th>수량</th><th>평균단가</th></tr>
  {holdings_rows}
  </table>
</div>

<div class="card">
  <h2>2. AI 시장 분석</h2>
  <p>시장 레짐: <span class="regime">{risk.get('signal','N/A')}</span>
     &nbsp; KOSPI 200MA 대비 <b>{risk.get('gap_pct',0):+.2f}%</b></p>
  {"<p>💡 " + ai.get("market_assessment","") + "</p>" if ai.get("market_assessment") else ""}
  {"<p>" + ai.get("reasoning","") + "</p>" if ai.get("reasoning") else ""}
</div>

<div class="card">
  <h2>3. 전략 업데이트 제안서</h2>
  {change_section}
  <table>
    <tr><th>지표</th><th>현재</th><th>목표</th></tr>
    <tr><td>누적 수익률</td><td>{metrics.get('total_return_pct',0):+.2f}%</td><td>벤치마크 초과</td></tr>
    <tr><td>샤프 비율</td><td>{metrics.get('sharpe',0):.3f}</td><td>≥ 0.2</td></tr>
    <tr><td>MDD</td><td>{metrics.get('max_dd_pct',0):.1f}%</td><td>≥ -30%</td></tr>
    <tr><td>Alpha</td><td>{metrics.get('alpha_pct',0):+.1f}%</td><td>≥ 0%</td></tr>
  </table>
</div>

<footer>퀀트 시스템 자동 발송 | {d}</footer>
</body></html>"""


# ── 진입점 (미리보기) ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json
    sample = {
        "date": "2026-04-07",
        "strategy_version": 1,
        "performance": {
            "total_value": 10_177_823, "pnl": 177_823, "pnl_pct": 1.78,
            "cash": 500_000, "holdings_count": 12, "transaction_count": 8,
            "holdings": [
                {"name": "삼성전자", "ticker": "005930", "shares": 10, "avg_price": 72_000},
                {"name": "SK하이닉스", "ticker": "000660", "shares": 5, "avg_price": 180_000},
            ],
        },
        "sim_metrics": {
            "total_return_pct": 11.78, "bm_return_pct": 9.78, "alpha_pct": 2.00,
            "sharpe": 0.241, "max_dd_pct": -9.76, "win_rate_pct": 57.3,
        },
        "risk_signal": {"signal": "STRONG_BULL", "gap_pct": 7.69},
        "ai_analysis": {
            "should_update": False,
            "reasoning": "현재 전략이 Alpha +2%로 벤치마크를 유지하고 있어 변경 불필요합니다.",
            "market_assessment": "KOSPI가 200일선을 7.7% 상회하며 강세장 지속 중입니다.",
        },
        "strategy_update": {"updated": False},
        "trades": {"sell": [], "buy": [], "hold": ["005930", "000660"]},
        "current_config": {
            "factor_weights": {"quality": 0.35, "value": 0.35, "momentum": 0.30},
            "filters": {"min_market_cap_억": 500, "max_debt_ratio": 2.0, "min_roe": 0.05},
            "risk_overlay": {"ma_weeks": 40, "caution_equity_ratio": 0.50, "strong_bear_equity_ratio": 0.30},
            "portfolio": {"top_n": 25, "stop_loss_pct": -0.08, "equity_budget": 0.95},
        },
    }

    print("=== 텍스트 리포트 ===")
    print(build_text_report(sample))

    html = build_html_report(sample)
    out = "logs/report_preview.html"
    import os; os.makedirs("logs", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nHTML 리포트 저장: {out}")
