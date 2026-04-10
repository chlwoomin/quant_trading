"""
dashboard.py
SSH 터널 전용 웹 대시보드 서버

사용법:
  python -X utf8 dashboard.py           # 기본 포트 8080
  python -X utf8 dashboard.py --port 9090

SSH 터널 연결 (로컬 PC에서):
  ssh -L 8080:localhost:8080 user@서버IP
  브라우저 → http://localhost:8080

보안: 127.0.0.1에만 바인딩 → 외부 네트워크 직접 접근 불가
"""

import json
import sys
import sqlite3
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

from portfolio_manager import DB_PATH, INITIAL_CASH, BROKER_MODE
from strategy_manager  import get_config
from price_updater     import get_current_price, PYKRX_OK


# ── 데이터 수집 (status.py 로직 재사용) ─────────────────────────────────────

def get_live_holdings():
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


def get_portfolio_summary(holdings):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM portfolio_state ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        return {"total_value": 0, "cash": 0, "stock_value": 0,
                "pnl": 0, "pnl_pct": 0, "n_holdings": 0}
    state = dict(row)
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


def get_regime():
    try:
        from risk_overlay import get_risk_signal as _get
        return _get()
    except Exception:
        return {"signal": "N/A", "gap_pct": 0}


def get_recent_trades(n=15):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM transactions ORDER BY id DESC LIMIT ?", (n,)
    ).fetchall()]
    conn.close()
    return rows


def get_performance_log(n=30):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM performance_log ORDER BY id DESC LIMIT ?", (n,)
    ).fetchall()]
    conn.close()
    return list(reversed(rows))


# ── HTML 생성 ─────────────────────────────────────────────────────────────────

def fmt_krw(n):
    return f"{int(n):,}원"

def pnl_class(v):
    return "pos" if v >= 0 else "neg"

def regime_badge(signal):
    colors = {
        "STRONG_BULL": "#22c55e",
        "NEUTRAL":     "#eab308",
        "CAUTION":     "#f97316",
        "STRONG_BEAR": "#ef4444",
    }
    color = colors.get(signal, "#6b7280")
    return f'<span class="badge" style="background:{color}">{signal}</span>'


def build_html():
    now      = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    holdings = get_live_holdings()
    summary  = get_portfolio_summary(holdings)
    config   = get_config()
    regime   = get_regime()
    trades   = get_recent_trades(15)
    perf_log = get_performance_log(30)

    fw = config.get("factor_weights", {})
    ro = config.get("risk_overlay",   {})
    pf = config.get("portfolio",      {})
    vg = config.get("validation_gates", {})
    fi = config.get("filters",        {})

    pnl_sign  = "+" if summary["pnl"] >= 0 else ""
    pnl_cls   = pnl_class(summary["pnl"])

    # 보유 종목 행
    holding_rows = ""
    for h in holdings:
        cls  = pnl_class(h["pnl_pct"])
        sign = "+" if h["pnl_pct"] >= 0 else ""
        holding_rows += f"""
        <tr>
          <td>{h['name']}</td>
          <td class="mono">{h['ticker']}</td>
          <td class="right">{h['shares']:,}주</td>
          <td class="right mono">{h['avg_price']:,.0f}</td>
          <td class="right mono">{h['live_price']:,.0f}</td>
          <td class="right {cls}">{sign}{h['pnl_pct']:.2f}%</td>
          <td class="right mono">{h['pnl_amt']:+,.0f}</td>
          <td class="right mono">{h['live_value']:,.0f}</td>
          <td class="center tag-{h['price_source']}">{h['price_source']}</td>
        </tr>"""

    # 최근 거래 행
    trade_rows = ""
    for t in trades:
        label = "매수" if t["type"] == "BUY" else "매도"
        cls   = "pos" if t["type"] == "BUY" else "neg"
        trade_rows += f"""
        <tr>
          <td>{t['date']}</td>
          <td class="center {cls} bold">{label}</td>
          <td>{t['name']}</td>
          <td class="right mono">{t['shares']:,}주</td>
          <td class="right mono">{t['price']:,.0f}</td>
          <td class="right mono">{t['amount']:,.0f}</td>
        </tr>"""

    # 수익률 차트 데이터 (Chart.js)
    chart_labels = json.dumps([r.get("date","") for r in perf_log])
    chart_values = json.dumps([round(r.get("daily_return_pct", 0), 3) for r in perf_log])

    price_note = "실시간 (pykrx)" if PYKRX_OK else "저장가 (pykrx 미연결)"

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>퀀트 대시보드</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --bg:      #0f172a;
    --card:    #1e293b;
    --border:  #334155;
    --text:    #e2e8f0;
    --muted:   #94a3b8;
    --pos:     #4ade80;
    --neg:     #f87171;
    --accent:  #38bdf8;
    --accent2: #818cf8;
  }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ background:var(--bg); color:var(--text); font-family:'Segoe UI',system-ui,sans-serif;
          font-size:14px; line-height:1.6; }}

  header {{ background:var(--card); border-bottom:1px solid var(--border);
            padding:16px 24px; display:flex; justify-content:space-between; align-items:center; }}
  header h1 {{ font-size:18px; font-weight:700; color:var(--accent); letter-spacing:.5px; }}
  header .meta {{ color:var(--muted); font-size:12px; text-align:right; }}

  .grid {{ display:grid; gap:16px; padding:20px 24px; }}
  .grid-2 {{ grid-template-columns:repeat(auto-fit,minmax(360px,1fr)); }}
  .grid-4 {{ grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); }}

  .card {{ background:var(--card); border:1px solid var(--border); border-radius:12px; padding:20px; }}
  .card-title {{ font-size:11px; font-weight:600; text-transform:uppercase;
                 letter-spacing:.8px; color:var(--muted); margin-bottom:14px; }}

  /* KPI 카드 */
  .kpi {{ text-align:center; }}
  .kpi .val {{ font-size:26px; font-weight:700; letter-spacing:-.5px; margin:4px 0; }}
  .kpi .sub {{ font-size:12px; color:var(--muted); }}

  /* 테이블 */
  .tbl-wrap {{ overflow-x:auto; }}
  table {{ width:100%; border-collapse:collapse; }}
  th {{ font-size:11px; color:var(--muted); font-weight:600; text-transform:uppercase;
        padding:6px 10px; border-bottom:1px solid var(--border); }}
  td {{ padding:8px 10px; border-bottom:1px solid #1e293b; }}
  tr:hover td {{ background:rgba(255,255,255,.03); }}
  .right {{ text-align:right; }}
  .center {{ text-align:center; }}
  .mono {{ font-family:'Consolas','Courier New',monospace; }}
  .bold {{ font-weight:600; }}

  /* 색상 */
  .pos {{ color:var(--pos); }}
  .neg {{ color:var(--neg); }}
  .tag-실시간 {{ color:var(--accent); font-size:11px; }}
  .tag-저장가  {{ color:var(--muted);  font-size:11px; }}

  /* 배지 */
  .badge {{ display:inline-block; padding:3px 10px; border-radius:99px;
            font-size:12px; font-weight:700; color:#fff; }}

  /* 전략 파라미터 */
  .param-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(140px,1fr)); gap:10px; }}
  .param-item {{ background:#0f172a; border-radius:8px; padding:10px 14px; }}
  .param-item .pk {{ font-size:11px; color:var(--muted); margin-bottom:2px; }}
  .param-item .pv {{ font-size:15px; font-weight:600; color:var(--accent2); }}

  /* 팩터 바 */
  .factor-bar {{ display:flex; gap:0; border-radius:6px; overflow:hidden; height:28px; margin-top:10px; }}
  .fb-seg {{ display:flex; align-items:center; justify-content:center;
             font-size:11px; font-weight:600; color:#fff; }}

  canvas {{ max-height:180px; }}
  .no-data {{ color:var(--muted); text-align:center; padding:24px; }}

  /* 새로고침 */
  .refresh-bar {{ text-align:center; padding:8px; color:var(--muted); font-size:11px; }}
</style>
</head>
<body>

<header>
  <h1>📈 퀀트 시스템 대시보드</h1>
  <div class="meta">
    <div>{now}</div>
    <div>가격: {price_note} &nbsp;|&nbsp; 모드: <b>{BROKER_MODE.upper()}</b> &nbsp;|&nbsp; 30초마다 자동갱신</div>
  </div>
</header>

<!-- KPI 카드 4개 -->
<div class="grid grid-4">
  <div class="card kpi">
    <div class="card-title">총 평가액</div>
    <div class="val">{summary['total_value']:,.0f}<small style="font-size:14px">원</small></div>
    <div class="sub">초기자본 {INITIAL_CASH:,.0f}원</div>
  </div>
  <div class="card kpi">
    <div class="card-title">누적 손익</div>
    <div class="val {pnl_cls}">{pnl_sign}{summary['pnl']:,.0f}<small style="font-size:14px">원</small></div>
    <div class="sub {pnl_cls}">{pnl_sign}{summary['pnl_pct']:.2f}%</div>
  </div>
  <div class="card kpi">
    <div class="card-title">주식 / 현금</div>
    <div class="val" style="font-size:20px">
      {summary['stock_value']:,.0f}<small style="font-size:12px">원</small>
    </div>
    <div class="sub">현금 {summary['cash']:,.0f}원</div>
  </div>
  <div class="card kpi">
    <div class="card-title">시장 레짐</div>
    <div class="val" style="font-size:20px; margin-top:8px">
      {regime_badge(regime.get('signal','N/A'))}
    </div>
    <div class="sub">KOSPI 200MA {regime.get('gap_pct',0):+.2f}%</div>
  </div>
</div>

<!-- 수익률 차트 + 전략 파라미터 -->
<div class="grid grid-2">

  <div class="card">
    <div class="card-title">일별 수익률 (최근 30일)</div>
    {"<canvas id='perfChart'></canvas>" if perf_log else "<div class='no-data'>성과 데이터 없음</div>"}
  </div>

  <div class="card">
    <div class="card-title">전략 v{config.get('version','?')} 파라미터</div>
    <div class="param-grid">
      <div class="param-item"><div class="pk">퀄리티</div><div class="pv">{fw.get('quality',0):.0%}</div></div>
      <div class="param-item"><div class="pk">밸류</div><div class="pv">{fw.get('value',0):.0%}</div></div>
      <div class="param-item"><div class="pk">모멘텀</div><div class="pv">{fw.get('momentum',0):.0%}</div></div>
      <div class="param-item"><div class="pk">상위 종목</div><div class="pv">{pf.get('top_n',0)}개</div></div>
      <div class="param-item"><div class="pk">스탑로스</div><div class="pv neg">{pf.get('stop_loss_pct',0):.0%}</div></div>
      <div class="param-item"><div class="pk">주식 비중</div><div class="pv">{pf.get('equity_budget',0):.0%}</div></div>
      <div class="param-item"><div class="pk">MA 기간</div><div class="pv">{ro.get('ma_weeks',0)}주</div></div>
      <div class="param-item"><div class="pk">최소 시총</div><div class="pv">{fi.get('min_market_cap_억',0):,}억</div></div>
      <div class="param-item"><div class="pk">최소 ROE</div><div class="pv">{fi.get('min_roe',0):.0%}</div></div>
      <div class="param-item"><div class="pk">최대 부채</div><div class="pv">{fi.get('max_debt_ratio',0):.1f}x</div></div>
      <div class="param-item"><div class="pk">금융주 제외</div><div class="pv">{'예' if fi.get('exclude_finance') else '아니오'}</div></div>
      <div class="param-item"><div class="pk">Min Sharpe</div><div class="pv">{vg.get('min_sharpe',0)}</div></div>
    </div>
    <div style="margin-top:14px;">
      <div class="card-title" style="margin-bottom:6px">팩터 비중</div>
      <div class="factor-bar">
        <div class="fb-seg" style="width:{fw.get('quality',0)*100:.0f}%;background:#6366f1">
          퀄리티 {fw.get('quality',0):.0%}
        </div>
        <div class="fb-seg" style="width:{fw.get('value',0)*100:.0f}%;background:#22c55e">
          밸류 {fw.get('value',0):.0%}
        </div>
        <div class="fb-seg" style="width:{fw.get('momentum',0)*100:.0f}%;background:#f59e0b">
          모멘텀 {fw.get('momentum',0):.0%}
        </div>
      </div>
    </div>
  </div>

</div>

<!-- 보유 종목 -->
<div class="grid">
  <div class="card">
    <div class="card-title">보유 종목 ({summary['n_holdings']}개)</div>
    {"<div class='no-data'>보유 종목 없음</div>" if not holdings else f"""
    <div class="tbl-wrap">
    <table>
      <thead>
        <tr>
          <th>종목명</th><th>티커</th><th class="right">수량</th>
          <th class="right">평균단가</th><th class="right">현재가</th>
          <th class="right">수익률</th><th class="right">손익금</th>
          <th class="right">평가금액</th><th class="center">가격</th>
        </tr>
      </thead>
      <tbody>{holding_rows}</tbody>
    </table>
    </div>"""}
  </div>
</div>

<!-- 최근 거래 -->
<div class="grid">
  <div class="card">
    <div class="card-title">최근 거래 내역</div>
    {"<div class='no-data'>거래 내역 없음</div>" if not trades else f"""
    <div class="tbl-wrap">
    <table>
      <thead>
        <tr>
          <th>날짜</th><th class="center">구분</th><th>종목</th>
          <th class="right">수량</th><th class="right">가격</th><th class="right">금액</th>
        </tr>
      </thead>
      <tbody>{trade_rows}</tbody>
    </table>
    </div>"""}
  </div>
</div>

<div class="refresh-bar">30초마다 자동 새로고침 &nbsp;·&nbsp; SSH 터널 전용 (127.0.0.1)</div>

<script>
{"" if not perf_log else f"""
const ctx = document.getElementById('perfChart').getContext('2d');
new Chart(ctx, {{
  type: 'bar',
  data: {{
    labels: {chart_labels},
    datasets: [{{
      label: '일별 수익률 (%)',
      data: {chart_values},
      backgroundColor: {chart_values}.map(v => v >= 0 ? 'rgba(74,222,128,0.7)' : 'rgba(248,113,113,0.7)'),
      borderRadius: 3,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display:false }}, tooltip: {{ callbacks: {{
      label: ctx => ctx.parsed.y.toFixed(3) + '%'
    }} }} }},
    scales: {{
      x: {{ ticks: {{ color:'#94a3b8', font:{{ size:10 }} }}, grid:{{ color:'#1e293b' }} }},
      y: {{ ticks: {{ color:'#94a3b8', callback: v => v+'%' }}, grid:{{ color:'#334155' }} }}
    }}
  }}
}});
"""}
</script>

</body>
</html>"""


# ── HTTP 서버 ─────────────────────────────────────────────────────────────────

class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/index.html"):
            self.send_response(404)
            self.end_headers()
            return
        try:
            html = build_html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(html))
            # 외부 캐시/프록시 방지
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(html)
        except Exception as e:
            err = f"<pre>오류: {e}</pre>".encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(err)

    def log_message(self, fmt, *args):
        # 접속 로그 간략화
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {args[0]} {args[1]}")


def main():
    port = 8080
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])

    # localhost 전용 바인딩
    server = HTTPServer(("127.0.0.1", port), DashboardHandler)

    print(f"""
╔══════════════════════════════════════════════════════╗
║          퀀트 대시보드  —  SSH 터널 전용             ║
╠══════════════════════════════════════════════════════╣
║  서버: http://127.0.0.1:{port:<5}                      ║
║  외부 접근 차단 (127.0.0.1 전용 바인딩)             ║
╠══════════════════════════════════════════════════════╣
║  SSH 터널 연결 방법:                                 ║
║    ssh -L {port}:localhost:{port} user@서버IP            ║
║  브라우저: http://localhost:{port:<5}                      ║
╚══════════════════════════════════════════════════════╝
  Ctrl+C 로 종료
""")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n서버 종료.")


if __name__ == "__main__":
    main()
