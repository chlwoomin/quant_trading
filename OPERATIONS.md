# 운영 가이드

## 서버 접속

```bash
ssh -i "C:\Users\woomin\.ssh\quant_server" ec2-user@ec2-3-36-109-13.ap-northeast-2.compute.amazonaws.com
```

---

## 웹 대시보드

로컬 PC와 서버 양쪽에서 각각 한 단계씩 실행합니다.

**1단계 — 로컬 PC: SSH 터널 연결**
```powershell
ssh -i "C:\Users\woomin\.ssh\Quant_Server_Key.pem" -L 8080:localhost:8080 ec2-user@ec2-3-36-109-13.ap-northeast-2.compute.amazonaws.com
```

**2단계 — 서버(SSH 세션 안): 대시보드 실행**
```bash
source ~/venv/bin/activate
python -X utf8 ~/quant_trading/dashboard.py
```

**3단계 — 브라우저에서 접속**
```
http://localhost:8080
```

> SSH 연결을 끊으면 터널도 닫히므로 대시보드 접속이 끊깁니다.  
> 백그라운드 실행이 필요하면 `nohup python -X utf8 ~/quant_trading/dashboard.py &` 사용.

---

## 현황 확인

```bash
# 포트폴리오 현황 (보유종목 · 손익 · 레짐)
python -X utf8 ~/quant_trading/status.py

# 30초마다 자동 갱신
python -X utf8 ~/quant_trading/status.py --watch
```

---

## 서버 관리

```bash
# 상태 확인
sudo systemctl status quant_system

# 시작 / 중지 / 재시작
sudo systemctl start quant_system
sudo systemctl stop quant_system
sudo systemctl restart quant_system      # 코드 변경 후 반드시 실행

# 부팅 자동 시작 등록 / 해제
sudo systemctl enable quant_system
sudo systemctl disable quant_system
```

---

## 로그 확인

```bash
# 실시간 로그 스트림
tail -f ~/quant_trading/logs/server.log

# 오늘 로그만
grep "$(date +%Y-%m-%d)" ~/quant_trading/logs/server.log

# 오류 · 경고만
grep "ERROR\|WARN" ~/quant_trading/logs/server.log | tail -20

# 주간 파이프라인 결과 JSON
cat ~/quant_trading/logs/weekly_$(date +%Y-%m-%d).json | python -m json.tool
```

---

## 수동 실행

```bash
cd ~/quant_trading
source ~/venv/bin/activate

# 주간 파이프라인 즉시 실행 (AI 포함)
python -X utf8 weekly_pipeline.py

# AI 없이 빠르게
python -X utf8 weekly_pipeline.py --no-ai

# 가격 업데이트 + 스탑로스 체크
python -X utf8 price_updater.py

# 다음 스케줄 실행 시간 확인
python -X utf8 server.py --test
```

---

## 자동 스케줄

| 시간 | 동작 |
|---|---|
| 일요일 22:00 KST | AI 분석 + 전략 검증 + 리밸런싱 + 디스코드 리포트 |
| 평일 16:10 KST | 장 마감 후 가격 업데이트 + 스탑로스 체크 |
| 평일 9:00~15:30 (30분) | 장중 스탑로스 모니터링 |

---

## DB 직접 조회

```bash
sqlite3 ~/quant_trading/portfolio/virtual_portfolio.db
```

```sql
-- 현재 보유 종목
SELECT name, shares, avg_price, current_price FROM holdings WHERE shares > 0;

-- 최근 포트폴리오 상태
SELECT date, total_value, memo FROM portfolio_state ORDER BY id DESC LIMIT 10;

-- 최근 거래 내역
SELECT date, type, name, shares, price FROM transactions ORDER BY id DESC LIMIT 20;

-- 종료
.quit
```

---

## 전략 변경

```bash
# 파라미터 수정
nano ~/quant_trading/strategy_config.json

# 수정 후 즉시 적용 (파이프라인 수동 실행)
python -X utf8 ~/quant_trading/weekly_pipeline.py --no-real
```

---

## 코드 업데이트 (로컬 → 서버)

**로컬 PowerShell에서:**
```powershell
# 변경된 파일만 전송
scp -i "C:\Users\woomin\.ssh\quant_server" "C:\Users\woomin\Desktop\Project\quant_system\<파일명>" ec2-user@ec2-3-36-109-13.ap-northeast-2.compute.amazonaws.com:~/quant_trading/

# 전체 프로젝트 동기화
scp -i "C:\Users\woomin\.ssh\quant_server" -r "C:\Users\woomin\Desktop\Project\quant_system\*" ec2-user@ec2-3-36-109-13.ap-northeast-2.compute.amazonaws.com:~/quant_trading/
```

**서버에서 서비스 재시작:**
```bash
sudo systemctl restart quant_system
```

---

## 문제 해결

### 서버가 시작되지 않을 때
```bash
# 상세 오류 확인
sudo journalctl -u quant_system -n 50 --no-pager

# Python 경로 확인
/home/ec2-user/venv/bin/python -X utf8 ~/quant_trading/server.py --test
```

### 디스코드 알림이 안 올 때
```bash
python -X utf8 ~/quant_trading/notifier.py
```

### 가격 업데이트가 안 될 때
```bash
python -X utf8 -c "from price_updater import get_current_price; print(get_current_price('005930'))"
```

### DB 초기화 (포트폴리오 리셋)
```bash
rm ~/quant_trading/portfolio/virtual_portfolio.db
python -X utf8 ~/quant_trading/weekly_pipeline.py --no-ai
```
