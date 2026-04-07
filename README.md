# 한국 주식 멀티팩터 가상 포트폴리오 자동화 시스템

## 파일 구조
```
quant_system/
├── factor_engine.py      # 멀티팩터 스크리닝 엔진
├── portfolio_manager.py  # 가상 포트폴리오 관리 (SQLite)
├── risk_overlay.py       # 절대모멘텀 리스크 오버레이
├── report_generator.py   # Claude API 주간 리포트 생성
├── scheduler.py          # 메인 스케줄러 (진입점)
├── portfolio/            # DB 저장 위치
├── logs/                 # 실행 로그 + JSON 결과
└── README.md
```

## 전략 개요
- **팩터**: 퀄리티(GP/A+ROE) 35% + 가치(PBR+PER) 35% + 모멘텀(12M-1M) 30%
- **리스크 오버레이**: KOSPI 200일 MA 기반 현금 전환
- **손절**: 개별 종목 -8% 자동 손절 신호
- **리밸런싱**: 월 1회 (일요일 22:00 cron)

## 설치
```bash
pip install pandas numpy requests FinanceDataReader pykrx
```

## 실행
```bash
# 즉시 1회 실행
python scheduler.py

# 3주 시뮬레이션 데모
python scheduler.py --demo

# 현재 성과 확인
python scheduler.py --report
```

## 실제 데이터 연동 (현재는 샘플 데이터)

### factor_engine.py — get_sample_universe() 교체
```python
import pykrx.stock as stock
import FinanceDataReader as fdr

def get_real_universe(date_str):
    # KOSPI 200 구성 종목
    tickers = stock.get_index_portfolio_deposit_file("1028")  # KOSPI 200
    
    # 재무 데이터
    fundamental = stock.get_market_fundamental(date_str, date_str, tickers)
    
    # 수익률 (12M-1M)
    end = pd.to_datetime(date_str)
    start_12m = (end - pd.DateOffset(months=12)).strftime("%Y%m%d")
    start_1m  = (end - pd.DateOffset(months=1)).strftime("%Y%m%d")
    ...
```

### risk_overlay.py — get_kospi_ma200() 교체
```python
import FinanceDataReader as fdr

def get_real_kospi_ma200():
    df = fdr.DataReader('KS11', '2023-01-01')
    current = df['Close'].iloc[-1]
    ma200   = df['Close'].rolling(200).mean().iloc[-1]
    ...
```

## cron 스케줄링 (매주 일요일 22:00)
```bash
# crontab -e 에 추가
0 22 * * 0 cd /path/to/quant_system && python scheduler.py >> logs/cron.log 2>&1
```

## Claude API 리포트 연동
```bash
export ANTHROPIC_API_KEY="your-key-here"
python scheduler.py
```
API 키 없이도 기본 리포트 템플릿으로 동작합니다.

## 주의사항
- 이 시스템은 **교육/연구 목적**의 가상 투자 도구입니다
- 실제 투자에 사용 전 충분한 백테스트와 검증이 필요합니다
- 금융 투자는 원금 손실 위험이 있습니다
