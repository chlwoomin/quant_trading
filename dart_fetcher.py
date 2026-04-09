"""
dart_fetcher.py
DART OpenAPI를 이용한 분기별 펀더멘탈 히스토리 수집

API 키 발급:
  https://opendart.fss.or.kr → 회원가입 → API 신청 (즉시 발급)
.env에 추가:
  DART_API_KEY=your_key_here

수집 데이터 (분기별, 2개월 공시 지연 반영):
  자본총계, 자산총계, 부채총계, 매출총이익, 당기순이익
  → ROE = 당기순이익 / 자본총계
  → GP/A = 매출총이익 / 자산총계
  → 부채비율 = 부채총계 / 자본총계
  → BPS = 자본총계 / 발행주식수
  → EPS = 당기순이익(연환산) / 발행주식수
  → PBR = 해당주차 실제가격 / BPS
  → PER = 해당주차 실제가격 / EPS

공시 지연 (보수적 적용):
  Q1(3월말) → 5월 중순 공개   → 5/15 이후 주부터 적용
  Q2(6월말) → 8월 중순 공개   → 8/15 이후 주부터 적용
  Q3(9월말) → 11월 중순 공개  → 11/15 이후 주부터 적용
  FY(12월말) → 다음해 4월 공개 → 4/1 이후 주부터 적용

사용법:
  python dart_fetcher.py              # 30종목 5년치 수집 및 캐시
  python dart_fetcher.py --years 3    # 3년치
  python dart_fetcher.py --refresh    # 캐시 무시 재수집
  python dart_fetcher.py --test       # 삼성전자 1종목 테스트
"""

import os
import sys
import json
import time
import zipfile
import io
import warnings
import requests
import numpy as np
import pandas as pd
from datetime import datetime, date

warnings.filterwarnings("ignore")

# ── 설정 ──────────────────────────────────────────────────────────────────────
DART_BASE   = "https://opendart.fss.or.kr/api"
CACHE_DIR   = os.path.join(os.path.dirname(__file__), "data", "dart")
SLEEP_SEC   = 0.6   # API 요청 간격 (초과 요청 방지)

# 분기 코드 → (DART reprt_code, YTD 개월 수, 라벨)
REPRT = {
    1: ("11013",  3, "1Q"),
    2: ("11012",  6, "H1"),
    3: ("11014",  9, "9M"),
    4: ("11011", 12, "FY"),
}

# 각 분기 데이터가 공시되는 시점 (월, 일)
AVAIL_DATE = {
    1: (5,  15),   # Q1 → 5/15
    2: (8,  15),   # H1 → 8/15
    3: (11, 15),   # 9M → 11/15
    4: (4,   1),   # FY → 다음해 4/1
}

# 계정과목 검색 키 (여러 표현 대응)
ACCOUNT_KEYS = {
    "assets":       ["자산총계"],
    "liabilities":  ["부채총계"],
    "equity":       ["자본총계"],
    "gross_profit": ["매출총이익"],
    "net_income":   ["당기순이익", "당기순이익(손실)"],
    "revenue":      ["매출액", "수익(매출액)", "매출"],
}

from backtest import TICKERS, NAMES


# ── 환경변수 로드 ─────────────────────────────────────────────────────────────
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


def _get_api_key() -> str:
    key = os.environ.get("DART_API_KEY", "")
    if not key:
        raise SystemExit(
            "DART_API_KEY가 설정되지 않았습니다.\n"
            "  1. https://opendart.fss.or.kr 회원가입 → API 신청\n"
            "  2. .env 파일에 DART_API_KEY=your_key 추가"
        )
    return key


# ── corp_code 매핑 ─────────────────────────────────────────────────────────────
def load_corp_codes(api_key: str, refresh: bool = False) -> dict:
    """
    DART 전체 기업 고유번호 목록을 다운로드합니다.
    Returns: {stock_code: corp_code}  e.g. {"005930": "00126380"}
    """
    cache_path = os.path.join(CACHE_DIR, "corp_codes.json")
    os.makedirs(CACHE_DIR, exist_ok=True)

    if not refresh and os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)

    print("  DART 기업 코드 목록 다운로드 중...")
    resp = requests.get(f"{DART_BASE}/corpCode.xml",
                        params={"crtfc_key": api_key}, timeout=30)
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        xml_data = zf.read(zf.namelist()[0]).decode("utf-8")

    # XML 파싱 (외부 라이브러리 없이)
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml_data)
    corp_map = {}
    for item in root.findall("list"):
        stock_code = (item.findtext("stock_code") or "").strip()
        corp_code  = (item.findtext("corp_code")  or "").strip()
        if stock_code and corp_code:
            corp_map[stock_code] = corp_code

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(corp_map, f, ensure_ascii=False)

    print(f"  기업 코드 {len(corp_map):,}개 로드 완료")
    return corp_map


# ── 단일 분기 재무제표 조회 ───────────────────────────────────────────────────
def fetch_one_quarter(corp_code: str, year: int, quarter: int,
                      api_key: str) -> dict:
    """
    DART fnlttSinglAcntAll API로 단일 분기 재무제표를 조회합니다.
    Returns: {"assets": float, "liabilities": float, "equity": float,
              "gross_profit": float, "net_income": float, "revenue": float}
             값이 없으면 None
    """
    reprt_code, months, _ = REPRT[quarter]

    # 연결 우선, 없으면 별도
    for fs_div in ("CFS", "OFS"):
        try:
            resp = requests.get(
                f"{DART_BASE}/fnlttSinglAcntAll.json",
                params={
                    "crtfc_key":  api_key,
                    "corp_code":  corp_code,
                    "bsns_year":  str(year),
                    "reprt_code": reprt_code,
                    "fs_div":     fs_div,
                },
                timeout=20,
            )
            data = resp.json()
            if data.get("status") != "000" or not data.get("list"):
                continue

            accounts = {item["account_nm"]: item for item in data["list"]}
            result = {}
            for key, names in ACCOUNT_KEYS.items():
                for nm in names:
                    if nm in accounts:
                        raw = accounts[nm].get("thstrm_amount", "").replace(",", "")
                        try:
                            result[key] = float(raw)
                        except (ValueError, TypeError):
                            result[key] = None
                        break
                else:
                    result[key] = None

            if any(v is not None for v in result.values()):
                result["months"] = months
                result["fs_div"] = fs_div
                return result

        except Exception:
            pass
        time.sleep(SLEEP_SEC)

    return {}


# ── 발행주식수 조회 ───────────────────────────────────────────────────────────
def fetch_shares(corp_code: str, year: int, api_key: str) -> int:
    """DART stockTotqySttus API로 연도별 발행주식수를 조회합니다."""
    try:
        resp = requests.get(
            f"{DART_BASE}/stockTotqySttus.json",
            params={"crtfc_key": api_key, "corp_code": corp_code,
                    "bsns_year": str(year), "reprt_code": "11011"},
            timeout=15,
        )
        data = resp.json()
        if data.get("status") != "000" or not data.get("list"):
            return 0
        for item in data["list"]:
            # 보통주 발행주식 합계
            if "보통주" in item.get("se", "") and "합계" in item.get("se", ""):
                raw = item.get("istc_totqy", "0").replace(",", "")
                return int(raw)
        # 첫 번째 항목 fallback
        raw = data["list"][0].get("istc_totqy", "0").replace(",", "")
        return int(raw)
    except Exception:
        return 0
    finally:
        time.sleep(SLEEP_SEC)


# ── 분기별 펀더멘탈 구축 ──────────────────────────────────────────────────────
def _normalize_amount(value, ref_equity_trillion=None):
    """
    DART 금액 단위 정규화 (원 단위로 통일).
    삼성전자 자본총계 ≈ 300조원 기준으로 단위 추정.
    """
    if value is None:
        return None
    # 300조(3e14) 이상이면 이미 원 단위
    # 300억(3e10) 수준이면 백만원 단위 → ×1e6
    # 300만(3e6) 수준이면 억원 단위 → ×1e8
    abs_v = abs(value)
    if abs_v >= 1e12:
        return value             # 원 단위
    elif abs_v >= 1e8:
        return value * 1_000_000  # 백만원 → 원
    elif abs_v >= 1e4:
        return value * 100_000_000  # 억원 → 원
    return value


def build_quarterly_fund(tickers: list, start_year: int, end_year: int,
                         api_key: str, refresh: bool = False) -> dict:
    """
    분기별 펀더멘탈 딕셔너리를 구축합니다.

    Returns:
      {
        "2020Q1": {0: {"roe":..., "gpa":..., "bps":..., "eps_annual":...,
                       "debt_ratio":..., "shares":...}, ...},
        "2020Q2": {...},
        ...
      }
    """
    cache_path = os.path.join(CACHE_DIR,
                               f"fund_{start_year}_{end_year}.json")
    os.makedirs(CACHE_DIR, exist_ok=True)

    if not refresh and os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            raw = json.load(f)
        # JSON key는 문자열이므로 int로 변환
        return {qk: {int(ik): iv for ik, iv in qv.items()}
                for qk, qv in raw.items()}

    corp_map = load_corp_codes(api_key)
    result   = {}
    total    = len(tickers) * (end_year - start_year + 1) * 4
    done     = 0

    # 연도별 발행주식수 캐시
    shares_cache = {}

    for idx, ticker in enumerate(tickers):
        corp_code = corp_map.get(ticker)
        if not corp_code:
            print(f"  [{idx+1:2d}/30] {NAMES[idx]}: corp_code 없음 — 스킵")
            continue

        for year in range(start_year, end_year + 1):
            # 발행주식수 (연 1회 조회)
            share_key = f"{ticker}_{year}"
            if share_key not in shares_cache:
                shares = fetch_shares(corp_code, year, api_key)
                shares_cache[share_key] = shares

            shares = shares_cache[share_key]

            for qtr in range(1, 5):
                qkey = f"{year}Q{qtr}"
                done += 1

                stmt = fetch_one_quarter(corp_code, year, qtr, api_key)
                if not stmt:
                    continue

                months = stmt.get("months", 3)

                # 금액 정규화 (백만원 → 원)
                equity   = _normalize_amount(stmt.get("equity"))
                assets   = _normalize_amount(stmt.get("assets"))
                liab     = _normalize_amount(stmt.get("liabilities"))
                gp       = _normalize_amount(stmt.get("gross_profit"))
                ni       = _normalize_amount(stmt.get("net_income"))

                if equity is None or assets is None or ni is None:
                    continue

                # 연환산 당기순이익
                ni_annual = ni * (12 / months) if months > 0 else ni

                # 지표 계산
                roe        = ni_annual / equity      if equity  > 0 else 0.08
                gpa        = gp / assets             if (gp and assets and assets > 0) else 0.15
                debt_ratio = liab / equity           if (liab and equity > 0) else 0.5
                bps        = equity / shares         if shares > 0 else 0
                eps_annual = ni_annual / shares      if shares > 0 else 0

                if qkey not in result:
                    result[qkey] = {}
                result[qkey][idx] = {
                    "roe":        float(np.clip(roe,        -1.0, 3.0)),
                    "gpa":        float(np.clip(gpa,         0.0, 1.0)),
                    "debt_ratio": float(np.clip(debt_ratio,  0.0, 20.0)),
                    "bps":        float(max(bps,   0)),
                    "eps_annual": float(eps_annual),
                    "shares":     int(shares),
                }

                pct = done / total * 100
                print(f"  [{idx+1:2d}/30] {NAMES[idx]:<12} {qkey}  "
                      f"ROE={roe:.1%}  GP/A={gpa:.1%}  "
                      f"부채비율={debt_ratio:.1f}  ({pct:.0f}%)", flush=True)

                time.sleep(SLEEP_SEC)

    # 저장
    save_data = {qk: {str(ik): iv for ik, iv in qv.items()}
                 for qk, qv in result.items()}
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)

    print(f"\n  분기별 펀더멘탈 저장: {cache_path}")
    return result


# ── 공시 지연 반영: 날짜 → 사용 가능한 분기 ──────────────────────────────────
def get_available_quarter(dt) -> tuple:
    """
    해당 날짜에 공시된 가장 최근 분기 반환 (공시 지연 2개월 반영).
    Returns: (year, quarter)  e.g. 2022-07-01 → (2022, 1)
    """
    if isinstance(dt, str):
        dt = pd.Timestamp(dt)
    y, m = dt.year, dt.month

    # FY는 다음해 4/1 공개
    if m < 4:                       return (y - 1, 4)
    elif m == 4 and dt.day < 1:     return (y - 1, 4)
    elif m < 5 or (m == 5 and dt.day < 15):  return (y - 1, 4)
    elif m < 8 or (m == 8 and dt.day < 15):  return (y,     1)
    elif m < 11 or (m == 11 and dt.day < 15): return (y,    2)
    else:                           return (y,     3)


# ── 주간 fund_history 변환 ───────────────────────────────────────────────────
def to_weekly_fund_history(quarterly_fund: dict,
                            weekly_dates: list,
                            price_matrix: np.ndarray,
                            fallback_fund: list) -> list:
    """
    분기별 펀더멘탈 → 주간 fund_history 리스트 변환.

    - 각 주차마다 가장 최근 공시 분기의 BPS/EPS 사용
    - PBR = 해당 주 실제가격 / BPS  (주가 반영, 분기마다 갱신)
    - PER = 해당 주 실제가격 / EPS  (연환산)
    - fallback_fund: DART 데이터 없는 종목의 기본값 (기존 yfinance 값)

    Returns: list[dict] — backtest.py fund_history 형식
    """
    from backtest import N_STOCKS

    n_stocks = N_STOCKS
    weekly_fund = []

    for t, dt in enumerate(weekly_dates):
        avail_year, avail_qtr = get_available_quarter(dt)
        qkey = f"{avail_year}Q{avail_qtr}"

        # 해당 분기 전후로 가장 가까운 유효 분기 탐색 (최대 6분기 전까지)
        quarter_data = None
        for lookback in range(7):
            y, q = avail_year, avail_qtr - lookback
            while q < 1:
                y -= 1
                q += 4
            k = f"{y}Q{q}"
            if k in quarterly_fund:
                quarter_data = quarterly_fund[k]
                break

        # 종목별 펀더멘탈 조립
        fund = {key: fallback_fund[t][key].copy() if hasattr(fallback_fund[t][key], 'copy')
                else fallback_fund[t][key]
                for key in fallback_fund[t]}

        # 배열로 재구성
        roe_arr        = fund["roe"].copy()
        gpa_arr        = fund["gpa"].copy()
        debt_arr       = fund["debt_ratio"].copy()
        mktcap_arr     = fund["mktcap_억"].copy()
        pbr_arr        = fund["pbr"].copy()
        per_arr        = fund["per"].copy()

        if quarter_data:
            cur_prices = price_matrix[t]  # shape: (N_STOCKS,)
            for i in range(n_stocks):
                d = quarter_data.get(i)
                if not d:
                    continue
                # 기본 지표 갱신
                roe_arr[i]    = d["roe"]
                gpa_arr[i]    = d["gpa"]
                debt_arr[i]   = d["debt_ratio"]

                # PBR, PER: 이번 주 실제 가격 기반 계산
                price = cur_prices[i]
                bps   = d["bps"]
                eps   = d["eps_annual"]
                if bps > 0 and price > 0:
                    pbr_arr[i] = float(np.clip(price / bps, 0.1, 20.0))
                if eps > 0 and price > 0:
                    per_arr[i] = float(np.clip(price / eps, 1.0, 100.0))

        weekly_fund.append({
            "roe":        roe_arr,
            "gpa":        gpa_arr,
            "pbr":        pbr_arr,
            "per":        per_arr,
            "debt_ratio": debt_arr,
            "mktcap_억":  mktcap_arr,
        })

    return weekly_fund


# ── 통합 로드 함수 ────────────────────────────────────────────────────────────
def load_or_build(start_year: int, end_year: int,
                  refresh: bool = False) -> dict:
    """
    캐시가 있으면 로드, 없으면 DART에서 수집합니다.
    Returns: quarterly_fund dict
    """
    api_key = _get_api_key()
    return build_quarterly_fund(TICKERS, start_year, end_year, api_key, refresh)


# ── 메인 (테스트/수집) ────────────────────────────────────────────────────────
if __name__ == "__main__":
    args    = sys.argv[1:]
    years   = 5
    refresh = "--refresh" in args
    test    = "--test" in args

    if "--years" in args:
        years = int(args[args.index("--years") + 1])

    api_key = _get_api_key()

    if test:
        # 삼성전자 단일 종목 1개 분기 테스트
        print("\n[테스트] 삼성전자 2023Q1 조회")
        corp_map = load_corp_codes(api_key)
        corp_code = corp_map.get("005930")
        if corp_code:
            stmt = fetch_one_quarter(corp_code, 2023, 1, api_key)
            print(json.dumps(stmt, ensure_ascii=False, indent=2))
            shares = fetch_shares(corp_code, 2023, api_key)
            print(f"발행주식수: {shares:,}주")
        sys.exit(0)

    end_year   = datetime.today().year
    start_year = end_year - years + 1

    print(f"\n[DART 펀더멘탈 수집] {start_year}~{end_year} ({years}년)")
    print(f"  30종목 × {years}년 × 4분기 = 약 {30*years*4}회 API 호출")
    print(f"  예상 소요 시간: 약 {30*years*4*SLEEP_SEC/60:.0f}~{30*years*4*SLEEP_SEC*2/60:.0f}분\n")

    fund = build_quarterly_fund(TICKERS, start_year, end_year, api_key, refresh)

    quarters = sorted(fund.keys())
    print(f"\n  수집된 분기: {len(quarters)}개  ({quarters[0]} ~ {quarters[-1]})")
    print(f"  데이터 있는 분기: "
          f"{sum(1 for q in fund.values() if len(q) >= 20)}/{len(quarters)}개 "
          f"(20종목 이상)")
