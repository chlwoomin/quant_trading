"""
notifier.py
주간 리포트를 이메일 + 디스코드로 발송

환경변수 설정 (.env 또는 시스템 환경변수):
  EMAIL_FROM        발신 이메일 (Gmail 권장)
  EMAIL_PASSWORD    Gmail 앱 비밀번호 (2단계 인증 후 생성)
  EMAIL_TO          수신 이메일 (쉼표 구분 가능: a@b.com,c@d.com)
  DISCORD_WEBHOOK   디스코드 웹훅 URL
                    (서버 설정 → 채널 편집 → 연동 → 웹후크 → URL 복사)

Gmail 앱 비밀번호 발급:
  Google 계정 → 보안 → 2단계 인증 사용 → 앱 비밀번호 생성
"""

import os
import smtplib
import ssl
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import date

from report_builder import build_text_report, build_html_report

# ── 환경변수 로드 (.env 파일 지원) ────────────────────────────────────────────
def _load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

_load_env()


# ── 이메일 발송 ───────────────────────────────────────────────────────────────
def send_email(subject: str, html_body: str, text_body: str = "") -> bool:
    """
    Gmail SMTP로 HTML 이메일을 발송합니다.
    Returns: True(성공), False(실패 또는 미설정)
    """
    sender   = os.environ.get("EMAIL_FROM", "")
    password = os.environ.get("EMAIL_PASSWORD", "")
    to_raw   = os.environ.get("EMAIL_TO", "")

    if not all([sender, password, to_raw]):
        print("  이메일 미설정 — 스킵 (EMAIL_FROM / EMAIL_PASSWORD / EMAIL_TO 필요)")
        return False

    recipients = [r.strip() for r in to_raw.split(",") if r.strip()]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = ", ".join(recipients)

    if text_body:
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as server:
            server.login(sender, password)
            server.sendmail(sender, recipients, msg.as_string())
        print(f"  이메일 발송 완료 → {', '.join(recipients)}")
        return True
    except Exception as e:
        print(f"  이메일 발송 실패: {e}")
        return False


# ── 디스코드 발송 ─────────────────────────────────────────────────────────────
def send_discord(message: str) -> bool:
    """
    디스코드 웹훅으로 메시지를 발송합니다.
    2000자 초과 시 자동으로 분할 발송합니다.
    Returns: True(성공), False(실패 또는 미설정)
    """
    webhook_url = os.environ.get("DISCORD_WEBHOOK", "")

    if not webhook_url:
        print("  디스코드 미설정 — 스킵 (DISCORD_WEBHOOK 필요)")
        return False

    MAX   = 1900  # 코드블록 여유분 포함해 2000자 이하 유지
    parts = [message[i:i+MAX] for i in range(0, len(message), MAX)]

    ok = True
    for part in parts:
        try:
            resp = requests.post(webhook_url, json={"content": part}, timeout=10)
            if resp.status_code not in (200, 204):
                print(f"  디스코드 오류: {resp.status_code} {resp.text[:100]}")
                ok = False
        except Exception as e:
            print(f"  디스코드 발송 실패: {e}")
            ok = False

    if ok:
        print("  디스코드 발송 완료")
    return ok


# ── 스탑로스 즉시 알림 ────────────────────────────────────────────────────────
def send_stop_loss_alert(stop_losses: list):
    """스탑로스 발동 시 즉시 알림 발송"""
    if not stop_losses:
        return
    lines = ["⚠️ **스탑로스 발동 알림**", ""]
    for s in stop_losses:
        lines.append(f"🔴 {s['name']} ({s['ticker']}): {s['loss_pct']:+.1f}% → 가상 매도 완료")
    lines += ["", "_퀀트 시스템 자동 알림_"]
    msg = "\n".join(lines)
    send_discord(msg)


# ── 시스템 상태 알림 ──────────────────────────────────────────────────────────
def send_system_alert(level: str, message: str):
    """
    시스템 오류/경고를 디스코드로 즉시 발송
    level: "INFO" | "WARN" | "ERROR"
    """
    emoji = {"INFO": "ℹ️", "WARN": "⚠️", "ERROR": "🚨"}.get(level, "📢")
    msg = f"{emoji} **퀀트 시스템 알림**\n\n{message}"
    send_discord(msg)


# ── 주간 리포트 통합 발송 ─────────────────────────────────────────────────────
def send_weekly_report(report_data: dict) -> dict:
    """
    주간 리포트를 이메일 + 디스코드로 발송합니다.

    report_data 구조 (weekly_pipeline.py에서 전달):
      - date, strategy_version
      - performance: total_value, pnl, pnl_pct, cash, holdings_count, holdings, transaction_count
      - sim_metrics: sharpe, max_dd_pct, alpha_pct, total_return_pct, bm_return_pct, win_rate_pct
      - risk_signal: signal, gap_pct
      - ai_analysis: should_update, reasoning, market_assessment
      - strategy_update: updated, old_version, new_version, changes, validation
      - trades: sell, buy, hold

    Returns: {"email": bool, "discord": bool}
    """
    d   = report_data.get("date", date.today().isoformat())
    ver = report_data.get("strategy_version", "?")
    upd = report_data.get("strategy_update", {})

    subject = f"[퀀트] 주간 리포트 {d} | v{ver}"
    if upd.get("updated"):
        subject += f" ⚡ 전략 v{upd.get('old_version')}→v{upd.get('new_version')} 업데이트"

    html_body = build_html_report(report_data)
    text_body = build_text_report(report_data)

    email_ok   = send_email(subject, html_body, text_body)
    discord_ok = send_discord(text_body)

    return {"email": email_ok, "discord": discord_ok}


# ── 진입점 (테스트) ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    sample = {
        "date": date.today().isoformat(),
        "strategy_version": 1,
        "performance": {
            "total_value": 10_177_823, "pnl": 177_823, "pnl_pct": 1.78,
            "cash": 500_000, "holdings_count": 12, "transaction_count": 8,
            "holdings": [
                {"name": "삼성전자",  "ticker": "005930", "shares": 10, "avg_price": 72_000},
                {"name": "SK하이닉스","ticker": "000660", "shares": 5,  "avg_price": 180_000},
            ],
        },
        "sim_metrics": {
            "total_return_pct": 11.78, "bm_return_pct": 9.78, "alpha_pct": 2.00,
            "sharpe": 0.241, "max_dd_pct": -9.76, "win_rate_pct": 57.3,
        },
        "risk_signal": {"signal": "STRONG_BULL", "gap_pct": 7.69},
        "ai_analysis": {
            "should_update": False,
            "reasoning": "현재 전략이 벤치마크 대비 양호하여 변경 불필요합니다.",
            "market_assessment": "KOSPI가 200일선을 7.7% 상회하며 강세장 지속 중입니다.",
        },
        "strategy_update": {"updated": False},
        "trades": {"sell": [], "buy": [], "hold": ["005930", "000660"]},
    }

    print("테스트 리포트 발송 중...")
    result = send_weekly_report(sample)
    print(f"이메일: {'성공' if result['email'] else '실패/미설정'}")
    print(f"디스코드: {'성공' if result['discord'] else '실패/미설정'}")
