# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

한국 주식 멀티팩터 가상 포트폴리오 자동화 시스템 — KOSPI 절대 모멘텀 기반 리스크 오버레이를 적용하여 한국 주식을 스크리닝하고, SQLite로 가상 포트폴리오를 관리하는 교육용 퀀트 투자 시스템입니다.

## 두 가지 파이프라인

### Pipeline 1 — 수동 전략 검증 (백테스트)
```
전략 수정 → 검증 → 실제 데이터 백테스트 → 결과 확인
```
```bash
# 1. 전략 파라미터 직접 수정
#    strategy_config.json 편집 또는:
$PYTHON -X utf8 strategy_manager.py

# 2. 실제 데이터로 워크포워드 검증 (DART 분기 펀더멘탈 포함)
.\run.bat dart          # 최초 1회: DART 펀더멘탈 수집 (~20분)
.\run.bat walkforward --dart

# 3. 모의 데이터 통계 검증
.\run.bat montecarlo --runs 50
```

### Pipeline 2 — 자동 라이브 서버 (가상 매매)
```
server.py 실행 → 자동 스케줄 → 실시간 가격 기반 가상 매매
```
```bash
# 서버 시작
.\run.bat server

# 실시간 현황 확인
.\run.bat status           # 현재 보유/손익/전략/레짐
.\run.bat status --watch   # 30초마다 자동 갱신

# 실제 매매 전환 시
# .env에 BROKER_MODE=real 추가 후 broker API 구현
```

## 핵심 실행 명령어

```bash
PYTHON=C:/Users/woomin/AppData/Local/Programs/Python/Python39/python.exe

# 24/7 서버 데몬 (메인 진입점)
$PYTHON -X utf8 server.py          # 서버 시작 (APScheduler)
$PYTHON -X utf8 server.py --test   # 스케줄 확인 후 종료

# 주간 파이프라인 수동 실행
$PYTHON -X utf8 weekly_pipeline.py             # 전체 (AI + 실제검증 포함)
$PYTHON -X utf8 weekly_pipeline.py --no-ai     # AI 분석 스킵
$PYTHON -X utf8 weekly_pipeline.py --no-real   # 실제 데이터 검증 스킵
$PYTHON -X utf8 weekly_pipeline.py --report    # 성과 출력만

# 백테스트
$PYTHON -X utf8 backtest.py          # 시뮬레이션 (모의 데이터)
$PYTHON -X utf8 backtest.py --real   # 실제 데이터 (yfinance)

# 전략 설정 확인
$PYTHON -X utf8 strategy_manager.py

# 워크포워드 테스트 (실제 데이터 N년 창 × M번)
# 워크포워드 테스트 (실제 데이터 N년 창 × M번)
$PYTHON -X utf8 walk_forward.py                         # 2년 창, 26주 간격, 5년치 데이터
$PYTHON -X utf8 walk_forward.py --window 1 --step 13    # 1년 창, 분기 간격
$PYTHON -X utf8 walk_forward.py --total 6               # 6년치 데이터 수집
$PYTHON -X utf8 walk_forward.py --refresh               # 데이터 재수집
$PYTHON -X utf8 walk_forward.py --dart                  # DART 분기 펀더멘탈 사용 (look-ahead bias 제거)
$PYTHON -X utf8 walk_forward.py --compare               # 현재 vs 직전 버전 비교

# DART 펀더멘탈 수집 (walk_forward --dart 전에 실행)
$PYTHON -X utf8 dart_fetcher.py                         # 30종목 5년치 수집 (~20분)
$PYTHON -X utf8 dart_fetcher.py --test                  # 삼성전자 1종목 테스트

# 몬테카를로 검증 (모의 데이터 N회 반복)
$PYTHON -X utf8 monte_carlo.py                        # 현재 전략, 50회, 2년
$PYTHON -X utf8 monte_carlo.py --runs 100             # 100회
$PYTHON -X utf8 monte_carlo.py --years 3              # 3년 기간
$PYTHON -X utf8 monte_carlo.py --compare              # 현재 vs 직전 버전 비교
$PYTHON -X utf8 monte_carlo.py --config v3            # 특정 버전 검증

# 리포트 미리보기 (logs/report_preview.html 생성)
$PYTHON -X utf8 report_builder.py

# 알림 테스트 (.env 설정 후)
$PYTHON -X utf8 notifier.py
```

> Python 경로: `C:/Users/woomin/AppData/Local/Programs/Python/Python39/python.exe`
> Python 3.13은 numpy/pykrx 불안정으로 사용 불가 — 반드시 3.9 사용

## 환경 설정

`.env.example`을 `.env`로 복사하고 값을 채우세요:
```
ANTHROPIC_API_KEY=sk-ant-api03-...
EMAIL_FROM=your@gmail.com
EMAIL_PASSWORD=xxxx-xxxx-xxxx-xxxx   # Gmail 앱 비밀번호
EMAIL_TO=your@gmail.com
DISCORD_WEBHOOK=https://discord.com/api/webhooks/...
```

## 의존성

```bash
pip install pandas numpy requests FinanceDataReader pykrx anthropic apscheduler pytz yfinance
```

## 전체 파이프라인 아키텍처

```
server.py  (24/7 APScheduler 데몬)
  │
  ├─ [일요일 22:00 KST]  weekly_pipeline.run_weekly()
  │     ├─ STEP 1: portfolio_manager      → 현재 포트폴리오 성과 수집
  │     ├─ STEP 2: backtest.Backtest      → 모의 데이터 백테스트 → 성과 지표
  │     ├─ STEP 3: ai_analyst             → Claude API 분석 → 파라미터 개선 제안
  │     ├─ STEP 4: validation_pipeline    → 제안 전략 2단계 검증
  │     │              ├─ 1차: 모의 백테스트 (Sharpe ≥ 0.2, MDD ≥ -30%, Alpha ≥ 0%)
  │     │              └─ 2차: yfinance 실제 백테스트 (Python 3.9 subprocess)
  │     │           → 통과 시 strategy_config.json 자동 업데이트
  │     ├─ STEP 5: risk_overlay           → KOSPI 200MA 리스크 신호
  │     ├─ STEP 6: factor_engine + portfolio_manager → 팩터 스크리닝 + 리밸런싱
  │     └─ STEP 7: report_builder + notifier → 이메일/디스코드 리포트 발송
  │
  ├─ [평일 16:10 KST]  price_updater.update_portfolio_prices()
  │     └─ pykrx 가격 업데이트 → 스탑로스 체크 → 즉시 알림
  │
  └─ [평일 9:00~15:30, 30분마다]  price_updater (장중 스탑로스 모니터링)
```

## 모듈 역할

| 파일 | 역할 |
|------|------|
| `server.py` | 24/7 데몬, APScheduler 스케줄 관리 |
| `weekly_pipeline.py` | 주간 7단계 파이프라인 오케스트레이터 |
| `strategy_config.json` | 전략 파라미터 단일 진실 공급원 |
| `strategy_manager.py` | 설정 버전 관리, `strategy_history/` 백업 |
| `factor_engine.py` | 종목 유니버스, 팩터 점수 산출, 필터링 |
| `portfolio_manager.py` | 가상 포트폴리오 상태, SQLite CRUD, 손익 계산 |
| `risk_overlay.py` | 시장 레짐 감지, 스탑로스 모니터링 |
| `backtest.py` | 모의/실제 데이터 백테스트, 성과 지표 계산 |
| `data_fetcher.py` | yfinance 실제 데이터 다운로드 + 캐시 |
| `ai_analyst.py` | Claude API 전략 분석 및 개선 제안 |
| `walk_forward.py` | 실제 데이터 롤링 윈도우 워크포워드 테스트, 창별 비교 |
| `dart_fetcher.py` | DART OpenAPI 분기별 PBR/PER/ROE 히스토리 수집, 캐시 |
| `monte_carlo.py` | 모의 데이터 N회 반복 검증, 통계 집계, 전략 비교 |
| `validation_pipeline.py` | 2단계 전략 검증 (모의 → 실제) |
| `status.py` | 실시간 현황 대시보드 (보유 종목, 손익, 전략, 레짐) |
| `price_updater.py` | 일중 pykrx 가격 업데이트, 스탑로스 실행 |
| `report_builder.py` | 텍스트(Discord) + HTML(이메일) 리포트 빌더 |
| `notifier.py` | Gmail SMTP + Discord 웹훅 알림 발송 |

## 전략 파라미터 (`strategy_config.json`)

```json
{
  "version": 1,
  "factor_weights": {"quality": 0.35, "value": 0.35, "momentum": 0.30},
  "filters": {"min_market_cap_억": 500, "max_debt_ratio": 2.0, "min_roe": 0.05, "exclude_finance": true},
  "risk_overlay": {"ma_weeks": 40, "strong_bull_gap_pct": 5.0, "strong_bear_gap_pct": -10.0,
                   "caution_equity_ratio": 0.50, "strong_bear_equity_ratio": 0.30},
  "portfolio": {"top_n": 25, "stop_loss_pct": -0.08, "equity_budget": 0.95},
  "validation_gates": {"min_sharpe": 0.2, "max_mdd_pct": -30.0, "min_alpha_pct": 0.0}
}
```

## SQLite 스키마 (`portfolio/virtual_portfolio.db`)

- `portfolio_state` — 일별 스냅샷
- `holdings` — 현재 보유 종목
- `transactions` — 거래 내역
- `performance_log` — 일별 수익률 지표

## 주요 설계 사항

- **팩터**: 퀄리티(GP/A + ROE) 35% · 밸류(저PBR + 저PER) 35% · 모멘텀(12M-1M) 30%
- **레짐별 주식 비중**: STRONG_BULL/NEUTRAL → 100%; CAUTION → 50%; STRONG_BEAR → 30% (리밸런싱 생략)
- **동일 비중**: 선택 종목에 가용 현금의 95%를 균등 배분
- **거래 비용**: 수수료 0.015%(매수·매도), 세금 0.20%(매도), 손익에 반영
- **pykrx 제약**: `get_market_fundamental` / `get_market_cap` 깨짐 (KRX 사이트 변경). `get_market_ohlcv_by_date`만 사용 가능. 펀더멘탈은 yfinance로 대체
- **Python 버전 격리**: Python 3.9만 사용. `validation_pipeline`에서 실제 백테스트 시 Python 3.9 subprocess 호출
- **교육 목적**: 이 시스템은 연구·학습 전용이며 실제 투자에 사용하지 않습니다

