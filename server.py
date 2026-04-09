"""
server.py
24/7 퀀트 시스템 서버 데몬

스케줄:
  [주간]  일요일 22:00 KST  — 전체 파이프라인 (AI분석 + 검증 + 리밸런싱 + 리포트발송)
  [일별]  평일 16:10 KST    — 장 마감 후 가격 업데이트 + 스탑로스 체크
  [장중]  평일 9:00~15:30   — 매 30분 스탑로스 모니터링

실행:
  C:/.../Python39/python.exe -X utf8 server.py          # 포그라운드
  C:/.../Python39/python.exe -X utf8 server.py --test   # 스케줄 확인 후 즉시 종료

systemd 등록 (Linux 서버):
  sudo cp quant_system.service /etc/systemd/system/
  sudo systemctl enable quant_system
  sudo systemctl start quant_system

의존성:
  pip install apscheduler pytz
"""

import sys
import os
import logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

# ── APScheduler 설치 확인 ─────────────────────────────────────────────────────
try:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron       import CronTrigger
    import pytz
    SCHEDULER_OK = True
except ImportError:
    SCHEDULER_OK = False

LOGS_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

# ── 로깅 설정 ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(LOGS_DIR, "server.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger("quant_server")

KST = pytz.timezone("Asia/Seoul") if SCHEDULER_OK else None


# ── 잡 함수들 ─────────────────────────────────────────────────────────────────
def job_weekly_pipeline():
    """일요일 22:00 — 전체 파이프라인"""
    logger.info("=== 주간 파이프라인 시작 ===")
    try:
        from weekly_pipeline import run_weekly
        run_weekly(use_ai=True, use_real=True)
        logger.info("=== 주간 파이프라인 완료 ===")
    except Exception as e:
        logger.error(f"주간 파이프라인 오류: {e}", exc_info=True)
        _alert_error(f"주간 파이프라인 오류: {e}")


def job_daily_price_update():
    """평일 16:10 — 장 마감 후 가격 업데이트"""
    logger.info("--- 일별 가격 업데이트 시작 ---")
    try:
        from price_updater import update_portfolio_prices
        from notifier      import send_stop_loss_alert
        result = update_portfolio_prices()
        if result["stop_losses"]:
            send_stop_loss_alert(result["stop_losses"])
            logger.warning(f"스탑로스 발동: {len(result['stop_losses'])}건")
        logger.info(f"가격 업데이트 완료: 평가액 {result['total_value']:,.0f}원 ({result['pnl_pct']:+.2f}%)")
    except Exception as e:
        logger.error(f"가격 업데이트 오류: {e}", exc_info=True)


def job_intraday_monitor():
    """평일 장중 매 30분 — 스탑로스 모니터링"""
    now = datetime.now()
    logger.debug(f"장중 모니터링 {now.strftime('%H:%M')}")
    try:
        from price_updater import update_portfolio_prices
        from notifier      import send_stop_loss_alert
        result = update_portfolio_prices()
        if result["stop_losses"]:
            send_stop_loss_alert(result["stop_losses"])
            logger.warning(f"[장중] 스탑로스 발동: {len(result['stop_losses'])}건")
    except Exception as e:
        logger.error(f"장중 모니터링 오류: {e}")


def _alert_error(msg: str):
    try:
        from notifier import send_system_alert
        send_system_alert("ERROR", msg)
    except Exception:
        pass


# ── 스케줄러 설정 ─────────────────────────────────────────────────────────────
def setup_scheduler() -> "BlockingScheduler":
    scheduler = BlockingScheduler(timezone=KST)

    # 주간 파이프라인: 매주 일요일 22:00
    scheduler.add_job(
        job_weekly_pipeline,
        CronTrigger(day_of_week="sun", hour=22, minute=0, timezone=KST),
        id="weekly_pipeline",
        name="주간 파이프라인",
        misfire_grace_time=3600,
    )

    # 일별 가격 업데이트: 평일 16:10 (장 마감 40분 후)
    scheduler.add_job(
        job_daily_price_update,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=10, timezone=KST),
        id="daily_price",
        name="일별 가격 업데이트",
        misfire_grace_time=600,
    )

    # 장중 모니터링: 평일 9:00~15:30 매 30분
    scheduler.add_job(
        job_intraday_monitor,
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute="0,30", timezone=KST),
        id="intraday_monitor",
        name="장중 스탑로스 모니터링",
        misfire_grace_time=300,
    )

    return scheduler


# ── 스케줄 출력 ───────────────────────────────────────────────────────────────
def print_schedule(scheduler):
    print("\n등록된 스케줄:")
    print(f"{'이름':<22} {'다음 실행 (KST)'}")
    print("-" * 52)
    now = datetime.now(KST)
    for job in scheduler.get_jobs():
        try:
            next_run = job.trigger.get_next_fire_time(None, now)
            next_str = next_run.astimezone(KST).strftime("%Y-%m-%d %H:%M %Z") if next_run else "N/A"
        except Exception:
            next_str = "N/A"
        print(f"  {job.name:<20} {next_str}")
    print()


# ── 메인 ─────────────────────────────────────────────────────────────────────
def main():
    if not SCHEDULER_OK:
        print("APScheduler 미설치. 다음 명령어로 설치하세요:")
        print("  pip install apscheduler pytz")
        sys.exit(1)

    test_mode = "--test" in sys.argv

    logger.info("퀀트 시스템 서버 시작")
    logger.info(f"Python: {sys.executable}")
    logger.info(f"현재 시각(KST): {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S %Z')}")

    scheduler = setup_scheduler()
    print_schedule(scheduler)

    if test_mode:
        logger.info("테스트 모드 — 스케줄 확인 후 종료")
        return

    # 시작 알림
    try:
        from notifier import send_system_alert
        send_system_alert("INFO", "퀀트 시스템 서버가 시작되었습니다.")
    except Exception:
        pass

    logger.info("스케줄러 실행 중... (Ctrl+C로 종료)")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("서버 종료")
        try:
            from notifier import send_system_alert
            send_system_alert("WARN", "퀀트 시스템 서버가 종료되었습니다.")
        except Exception:
            pass


if __name__ == "__main__":
    main()
