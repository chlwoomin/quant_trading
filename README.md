# 한국 주식 멀티팩터 가상 포트폴리오 자동화 시스템

KOSPI 대형주를 대상으로 퀄리티·밸류·모멘텀 팩터 스크리닝을 수행하고,  
Claude AI가 매주 전략을 분석·검증하여 자동으로 가상 포트폴리오를 운용합니다.

> **교육·연구 목적 시스템입니다. 실제 투자에 사용하지 마세요.**

---

## 두 가지 파이프라인

### Pipeline 1 — 수동 전략 검증
전략 파라미터를 직접 수정하고 실제 데이터로 백테스트합니다.

```bash
# 1. 전략 파라미터 확인/수정
python -X utf8 strategy_manager.py

# 2. 워크포워드 테스트 (실제 데이터 롤링 윈도우)
.\run.bat walkforward
.\run.bat walkforward --dart          # DART 분기 펀더멘탈 사용 (look-ahead bias 제거)

# 3. 몬테카를로 검증 (모의 데이터 50회 반복)
.\run.bat montecarlo --runs 50
```

### Pipeline 2 — 자동 라이브 서버 (가상 매매)
서버를 띄우면 스케줄에 따라 자동으로 실행됩니다.

```bash
# 서버 시작
.\run.bat server

# 현황 확인
.\run.bat status
.\run.bat status --watch    # 30초마다 자동 갱신
```

**자동 스케줄:**
| 시간 | 동작 |
|---|---|
| 일요일 22:00 KST | AI 분석 + 전략 검증 + 리밸런싱 + 디스코드 리포트 |
| 평일 16:10 KST | 장 마감 후 가격 업데이트 + 스탑로스 체크 |
| 평일 9:00~15:30 (30분) | 장중 스탑로스 모니터링 |

---

## 전략 개요

| 팩터 | 구성 | 비중 |
|---|---|---|
| 퀄리티 | GP/A + ROE | 35% |
| 밸류 | 저PBR + 저PER | 35% |
| 모멘텀 | 12M-1M 수익률 | 30% |

- **리스크 오버레이**: KOSPI 200일 MA 기반 레짐 감지
  - STRONG_BULL / NEUTRAL → 주식 100%
  - CAUTION → 주식 50%
  - STRONG_BEAR → 주식 30% (리밸런싱 스킵)
- **스탑로스**: 개별 종목 -8% 자동 가상 매도
- **리밸런싱**: 주간 동일 비중, 현금 5% 버퍼 유지
- **거래 비용**: 수수료 0.015% + 세금 0.20% 반영

---

## 파일 구조

```
quant_system/
├── server.py               # 24/7 APScheduler 데몬 (메인 진입점)
├── weekly_pipeline.py      # 주간 7단계 파이프라인 오케스트레이터
├── strategy_config.json    # 전략 파라미터 단일 진실 공급원
├── strategy_manager.py     # 설정 버전 관리 + strategy_history/ 백업
│
├── factor_engine.py        # yfinance 실제 데이터 기반 팩터 스크리닝
├── portfolio_manager.py    # 가상 포트폴리오 SQLite CRUD + 손익 계산
├── risk_overlay.py         # KOSPI 200MA 레짐 감지 (yfinance ^KS11)
├── price_updater.py        # pykrx 가격 업데이트 + 스탑로스 실행
├── backtest.py             # 모의/실제 데이터 백테스트 + 성과 지표
│
├── ai_analyst.py           # Claude API 전략 분석 + 개선 제안
├── validation_pipeline.py  # 2단계 전략 검증 (모의 → 실제)
├── walk_forward.py         # 실제 데이터 롤링 윈도우 워크포워드
├── monte_carlo.py          # 모의 데이터 N회 반복 몬테카를로
├── dart_fetcher.py         # DART OpenAPI 분기별 펀더멘탈 히스토리
│
├── report_builder.py       # 텍스트(디스코드) + HTML(이메일) 리포트
├── notifier.py             # Gmail SMTP + 디스코드 웹훅 알림
├── status.py               # 실시간 CLI 대시보드
│
├── run.bat                 # Windows 단축 명령어
├── quant_system.service    # systemd 서비스 파일 (Linux 배포용)
├── portfolio/
│   └── virtual_portfolio.db   # SQLite DB
├── data/                   # yfinance 캐시 (universe_YYYY-MM-DD.json 등)
├── logs/                   # 실행 로그 + 주간 JSON 결과
└── strategy_history/       # 전략 버전 백업
```

---

## 설치

```bash
pip install pandas numpy requests pykrx anthropic apscheduler pytz yfinance
```

> Python 3.9 필수 — pykrx가 3.10+ 에서 불안정합니다.  
> Windows: `C:/Users/<user>/AppData/Local/Programs/Python/Python39/python.exe`

---

## 환경 설정

`.env` 파일을 프로젝트 루트에 생성하세요:

```env
ANTHROPIC_API_KEY=sk-ant-api03-...        # console.anthropic.com에서 발급
DISCORD_WEBHOOK=https://discord.com/api/webhooks/...
EMAIL_FROM=your@gmail.com
EMAIL_PASSWORD=xxxx-xxxx-xxxx-xxxx        # Gmail 앱 비밀번호
EMAIL_TO=your@gmail.com
```

- **ANTHROPIC_API_KEY**: Claude.ai 구독과 별개, console.anthropic.com에서 별도 발급
- **DISCORD_WEBHOOK**: 서버 설정 → 채널 편집 → 연동 → 웹후크 → URL 복사
- **EMAIL_PASSWORD**: Google 계정 → 보안 → 2단계 인증 → 앱 비밀번호 생성

API 키 없이도 규칙 기반 fallback으로 작동하지만, AI 분석 실패 시 디스코드 경고가 발송됩니다.

---

## 주요 명령어 (Windows)

```bash
.\run.bat server                          # 24/7 서버 시작
.\run.bat pipeline                        # 주간 파이프라인 수동 실행
.\run.bat pipeline --no-ai               # AI 분석 없이 실행
.\run.bat status                          # 현재 현황 확인
.\run.bat backtest                        # 모의 백테스트
.\run.bat walkforward                     # 워크포워드 테스트
.\run.bat walkforward --dart              # DART 펀더멘탈 포함
.\run.bat montecarlo --runs 50            # 몬테카를로 50회
.\run.bat dart                            # DART 데이터 수집
.\run.bat report                          # 리포트 미리보기
.\run.bat strategy                        # 전략 설정 확인
```

---

## AWS EC2 배포

```bash
# 1. Ubuntu 22.04, t3.small 인스턴스 생성

# 2. SSH 접속 후 Python 3.9 설치
sudo apt update && sudo apt install -y python3.9 python3.9-pip python3.9-venv

# 3. 프로젝트 업로드 (로컬에서)
scp -i your-key.pem -r ./quant_system ubuntu@<EC2_IP>:~/quant_system

# 4. 가상환경 + 의존성
cd ~/quant_system
python3.9 -m venv venv
source venv/bin/activate
pip install pandas numpy requests pykrx anthropic apscheduler pytz yfinance

# 5. .env 파일 작성
nano .env

# 6. 동작 확인
python -X utf8 server.py --test
python -X utf8 weekly_pipeline.py --no-ai

# 7. systemd 서비스 등록
sudo cp quant_system.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable quant_system
sudo systemctl start quant_system

# 로그 확인
tail -f ~/quant_system/logs/server.log
```

---

## 전략 파라미터 (`strategy_config.json`)

```json
{
  "version": 1,
  "factor_weights": {"quality": 0.35, "value": 0.35, "momentum": 0.30},
  "filters": {
    "min_market_cap_억": 500,
    "max_debt_ratio": 2.0,
    "min_roe": 0.05,
    "exclude_finance": true
  },
  "risk_overlay": {
    "ma_weeks": 40,
    "strong_bull_gap_pct": 5.0,
    "strong_bear_gap_pct": -10.0,
    "caution_equity_ratio": 0.50,
    "strong_bear_equity_ratio": 0.30
  },
  "portfolio": {"top_n": 25, "stop_loss_pct": -0.08, "equity_budget": 0.95},
  "validation_gates": {"min_sharpe": 0.2, "max_mdd_pct": -30.0, "min_alpha_pct": 0.0}
}
```

Claude AI가 매주 이 파라미터를 검토하고, 검증 통과 시 자동으로 업데이트합니다.

---

## SQLite 스키마 (`portfolio/virtual_portfolio.db`)

| 테이블 | 내용 |
|---|---|
| `portfolio_state` | 일별 평가액 스냅샷 |
| `holdings` | 현재 보유 종목 + 평균 단가 + 현재가 |
| `transactions` | 전체 매수/매도 내역 |
| `performance_log` | 일별 수익률 지표 |

---

## 실제 매매 전환 (향후)

`portfolio_manager.py`의 `_get_real_price()` 함수에서:

```python
# BROKER_MODE=real 설정 후 아래 부분을 증권사 API로 교체
# 예) KIS Open API: GET /uapi/domestic-stock/v1/quotations/inquire-price
```

`.env`에 `BROKER_MODE=real` 추가 후 KIS/키움 API 구현으로 실제 매매 전환 가능합니다.

---

## 주의사항

- 이 시스템은 **교육·연구 목적**의 가상 투자 도구입니다
- 실제 투자 전 충분한 검증과 전문가 상담이 필요합니다
- 금융 투자는 원금 손실 위험이 있습니다
- pykrx의 `get_market_fundamental` / `get_market_cap`은 KRX 사이트 변경으로 사용 불가 — 펀더멘탈은 yfinance로 대체
