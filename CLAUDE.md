# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

한국 주식 멀티팩터 가상 포트폴리오 자동화 시스템 — KOSPI 절대 모멘텀 기반 리스크 오버레이를 적용하여 한국 주식을 스크리닝하고, SQLite로 가상 포트폴리오를 관리하는 교육용 퀀트 투자 시스템입니다.

## 핵심 실행 명령어

```bash
# 주간 자동화 파이프라인 (메인)
C:/.../Python39/python.exe -X utf8 weekly_pipeline.py             # 전체 (AI + 실제검증 포함)
C:/.../Python39/python.exe -X utf8 weekly_pipeline.py --no-ai     # AI 분석 스킵
C:/.../Python39/python.exe -X utf8 weekly_pipeline.py --no-real   # 실제 데이터 검증 스킵
C:/.../Python39/python.exe -X utf8 weekly_pipeline.py --report    # 성과 출력만

# 백테스트
C:/.../Python39/python.exe -X utf8 backtest.py          # 시뮬레이션
C:/.../Python39/python.exe -X utf8 backtest.py --real   # 실제 데이터 (yfinance)

# 전략 설정 확인
C:/.../Python39/python.exe -X utf8 strategy_manager.py
```

> Python 경로: `C:/Users/woomin/AppData/Local/Programs/Python/Python39/python.exe`
> Python 3.13은 numpy 불안정으로 사용 불가

## 레거시 실행 방법 (scheduler.py)

```bash
python scheduler.py              # 전체 파이프라인 실행
python scheduler.py --demo       # 3주 시뮬레이션 데모
python scheduler.py --report     # 현재 성과 출력만
```

자연어 리포트를 위한 Claude API 연동 (선택):
```bash
export ANTHROPIC_API_KEY="your-key-here"
python scheduler.py
```

크론 스케줄링 (매주 일요일 22:00):
```bash
0 22 * * 0 cd /path/to/quant_system && python scheduler.py >> logs/cron.log 2>&1
```

## 의존성

`requirements.txt` 없음 — 수동 설치 필요:
```bash
pip install pandas numpy requests FinanceDataReader pykrx anthropic
```

## 전체 파이프라인 아키텍처

```
weekly_pipeline.py  (매주 일요일 22:00 cron)
  │
  ├─ STEP 1: portfolio_manager  → 현재 포트폴리오 성과 수집
  ├─ STEP 2: backtest.py        → 모의 데이터 백테스트 → 성과 지표 계산
  ├─ STEP 3: ai_analyst.py      → Claude API 분석 → 파라미터 개선 제안
  ├─ STEP 4: validation_pipeline.py → 제안 전략 검증
  │              ├─ 1차: 모의 백테스트 (Sharpe ≥ 0.2, MDD ≥ -30%)
  │              └─ 2차: yfinance 실제 백테스트 (Python 3.9 subprocess)
  │           → 통과 시 strategy_config.json 자동 업데이트
  ├─ STEP 5: risk_overlay.py    → KOSPI 200MA 리스크 신호
  ├─ STEP 6: factor_engine.py   → 팩터 스크리닝 → portfolio_manager 리밸런싱
  └─ STEP 7: report_generator.py → 주간 리포트 + logs/weekly_YYYY-MM-DD.json
```

### 전략 파라미터 관리
- **`strategy_config.json`**: 단일 진실 공급원 (factor_weights, filters, risk_overlay, portfolio, validation_gates)
- **`strategy_manager.py`**: 버전 관리, `strategy_history/` 폴더에 변경 이력 자동 백업
- 모든 모듈(`factor_engine`, `backtest`, `risk_overlay`)이 `get_config()`로 설정을 읽음

## 레거시 아키텍처

`scheduler.py`가 파이프라인을 오케스트레이션하며 다음 순서로 실행됩니다:

1. **DB 초기화** (`portfolio_manager.init_db`) — `portfolio/virtual_portfolio.db`에 SQLite 생성
2. **리스크 오버레이** (`risk_overlay.py`) — KOSPI 200일 이동평균 계산, 시장 레짐 결정(STRONG_BULL / NEUTRAL / CAUTION / STRONG_BEAR), 개별 종목 -8% 스탑로스 확인. STRONG_BEAR이면 리밸런싱 생략.
3. **팩터 스크리닝** (`factor_engine.run_screening`) — 퀄리티(35%) · 밸류(35%) · 모멘텀(30%)을 Z-score로 정규화해 종합 점수 산출, 필터 적용(시가총액 ≥ 500억, 부채비율 ≤ 200%, ROE ≥ 5%, 금융업 제외), 상위 25종목 선정.
4. **리밸런싱** (`portfolio_manager.rebalance`) — 동일 비중 매수/매도, SQLite에 기록.
5. **리포트 생성** (`report_generator.generate_weekly_report`) — `ANTHROPIC_API_KEY` 설정 시 Claude API 사용, 없으면 템플릿 폴백.
6. **결과 로깅** — `logs/run_YYYY-MM-DD.json`에 JSON, `logs/scheduler.log`에 텍스트 기록.

### 모듈 역할

| 파일 | 역할 |
|------|------|
| `scheduler.py` | 진입점, 파이프라인 오케스트레이션 |
| `factor_engine.py` | 종목 유니버스, 팩터 점수 산출, 필터링 |
| `portfolio_manager.py` | 가상 포트폴리오 상태, SQLite CRUD, 손익 계산 |
| `risk_overlay.py` | 시장 레짐 감지, 스탑로스 모니터링 |
| `report_generator.py` | Claude API 연동 리포트 생성 및 폴백 |

### SQLite 스키마 (`portfolio/virtual_portfolio.db`)

- `portfolio_state` — 일별 스냅샷
- `holdings` — 현재 보유 종목
- `transactions` — 거래 내역
- `performance_log` — 일별 수익률 지표

## 주요 설계 사항

- **샘플 데이터**: `factor_engine.get_sample_universe()`는 모의 랜덤 데이터를 사용합니다. 실제 운용 시 `pykrx` API 호출로 교체 필요.
- **레짐별 주식 비중**: STRONG_BULL / NEUTRAL → 100%; CAUTION → 50%; STRONG_BEAR → 30% (리밸런싱 전면 생략).
- **동일 비중**: 선택된 종목에 가용 현금의 95%를 균등 배분.
- **거래 비용**: 수수료 0.015%(매수·매도), 세금 0.20%(매도 전용), 손익에 모두 반영.
- **교육 목적**: 이 시스템은 연구·학습 전용이며 실제 투자에 사용하지 않습니다.
