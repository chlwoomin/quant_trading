"""
risk_overlay.py
절대모멘텀 리스크 오버레이: KOSPI 200일 이동평균 기반 현금 전환 신호

- yfinance(^KS11) 로 KOSPI 지수 조회 (주 소스)
- pykrx 를 보조 소스로 사용
- strategy_config.json의 risk_overlay 파라미터 사용
"""

import ast
import json
import os
from datetime import datetime, timedelta
from typing import Dict

from strategy_manager import get_config


KOSPI_CACHE_PATH = os.path.join(os.path.dirname(__file__), "data", "kospi_cache.json")


def _save_cache(data):
    os.makedirs(os.path.dirname(KOSPI_CACHE_PATH), exist_ok=True)
    with open(KOSPI_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_cache():
    if os.path.exists(KOSPI_CACHE_PATH):
        with open(KOSPI_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return None


def _is_plausible_kospi(current: float, ma200: float) -> bool:
    if current <= 1000 or current >= 10000 or ma200 <= 1000 or ma200 >= 10000:
        return False
    gap_pct = abs((current - ma200) / ma200 * 100)
    return gap_pct <= 100


def _build_result(dates, closes, source: str, today: str) -> Dict:
    if len(closes) < 5:
        raise ValueError(f"{source} KOSPI 데이터 부족")
    kospi_cur = float(closes[-1])
    ma200 = float(sum(closes[-200:]) / min(len(closes), 200))
    if not _is_plausible_kospi(kospi_cur, ma200):
        raise ValueError(f"{source} KOSPI 값 비정상: current={kospi_cur}, ma200={ma200}")
    gap_pct = (kospi_cur - ma200) / ma200 * 100
    return {
        "date": today,
        "last_trade_date": dates[-1],
        "kospi_current": round(kospi_cur, 2),
        "ma200": round(ma200, 2),
        "above_ma200": kospi_cur > ma200,
        "gap_pct": round(gap_pct, 2),
        "source": source,
        "dates": dates,
        "closes": [round(float(v), 2) for v in closes],
    }


def get_kospi_history(date_str: str = None, lookback_days: int = 430) -> Dict:
    """KOSPI 일별 종가 히스토리와 200일 이동평균 정보를 반환합니다."""
    today = date_str or datetime.today().strftime("%Y-%m-%d")
    end_dt = datetime.strptime(today, "%Y-%m-%d")
    start_dt = end_dt - timedelta(days=lookback_days)

    # 1차: Naver Finance 지수 JSON. yfinance ^KS11이 비정상 값을 줄 때가 있어 우선 사용합니다.
    try:
        import requests
        url = "https://api.finance.naver.com/siseJson.naver"
        params = {
            "symbol": "KOSPI",
            "requestType": "1",
            "startTime": start_dt.strftime("%Y%m%d"),
            "endTime": end_dt.strftime("%Y%m%d"),
            "timeframe": "day",
        }
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        rows = ast.literal_eval(response.text.strip())
        data_rows = [r for r in rows[1:] if isinstance(r, list) and len(r) >= 5]
        dates = [
            datetime.strptime(str(r[0]), "%Y%m%d").strftime("%Y-%m-%d")
            for r in data_rows
        ]
        closes = [float(r[4]) for r in data_rows]
        result = _build_result(dates, closes, "naver", today)
        _save_cache(result)
        return result
    except Exception:
        pass

    # 2차: pykrx 지수 API. 일부 환경에서는 KRX 지수명 테이블 조회가 실패할 수 있습니다.
    try:
        from pykrx import stock as pykrx_stock
        df = pykrx_stock.get_index_ohlcv_by_date(
            start_dt.strftime("%Y%m%d"), end_dt.strftime("%Y%m%d"), "1001"
        )
        if df.empty:
            raise ValueError("pykrx KOSPI 데이터 없음")
        dates = [idx.strftime("%Y-%m-%d") for idx in df.index]
        closes = df["종가"].astype(float).tolist()
        result = _build_result(dates, closes, "pykrx", today)
        _save_cache(result)
        return result
    except Exception:
        pass

    # 3차: yfinance. sanity check를 통과한 경우에만 사용합니다.
    try:
        import yfinance as yf
        raw = yf.download(
            "^KS11",
            start=start_dt.strftime("%Y-%m-%d"),
            end=(end_dt + timedelta(days=1)).strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True,
        )
        if isinstance(raw.columns, __import__("pandas").MultiIndex):
            closes_s = raw["Close"]["^KS11"].dropna()
        else:
            closes_s = raw["Close"].dropna()
        dates = [idx.strftime("%Y-%m-%d") for idx in closes_s.index]
        closes = closes_s.astype(float).tolist()
        result = _build_result(dates, closes, "yfinance", today)
        _save_cache(result)
        return result
    except Exception:
        pass

    cached = _load_cache()
    if cached:
        try:
            current = float(cached["kospi_current"])
            ma200 = float(cached["ma200"])
            if _is_plausible_kospi(current, ma200):
                cached["source"] = "cache"
                return cached
        except Exception:
            pass
    return {
        "date": today,
        "last_trade_date": today,
        "kospi_current": 2500.0,
        "ma200": 2500.0,
        "above_ma200": True,
        "gap_pct": 0.0,
        "source": "fallback",
        "dates": [],
        "closes": [],
    }


def get_kospi_ma200(date_str: str = None) -> Dict:
    """
    KOSPI 현재가와 200일 이동평균을 조회합니다.
    실패 시 마지막 저장값(data/kospi_cache.json)을 사용합니다.
    """
    data = get_kospi_history(date_str)
    return {
        "date": data["date"],
        "last_trade_date": data.get("last_trade_date"),
        "kospi_current": data["kospi_current"],
        "ma200": data["ma200"],
        "above_ma200": data["above_ma200"],
        "gap_pct": data["gap_pct"],
        "source": data.get("source", "unknown"),
    }


def get_risk_signal(kospi_data: Dict = None, config: dict = None) -> Dict:
    """
    리스크 신호 판단 (strategy_config.json risk_overlay 파라미터 사용)
    kospi_data가 None이면 자동 조회합니다.
    """
    if kospi_data is None:
        kospi_data = get_kospi_ma200()

    cfg = (config or get_config()).get("risk_overlay", {})
    gap = kospi_data["gap_pct"]

    if kospi_data["above_ma200"]:
        if gap > cfg.get("strong_bull_gap_pct", 5.0):
            signal, equity_ratio = "STRONG_BULL", 1.00
        else:
            signal, equity_ratio = "NEUTRAL", 1.00
    else:
        if gap < cfg.get("strong_bear_gap_pct", -10.0):
            signal, equity_ratio = "STRONG_BEAR", cfg.get("strong_bear_equity_ratio", 0.30)
        else:
            signal, equity_ratio = "CAUTION", cfg.get("caution_equity_ratio", 0.50)

    return {
        "signal":       signal,
        "equity_ratio": equity_ratio,
        "cash_ratio":   round(1 - equity_ratio, 2),
        "kospi":        kospi_data["kospi_current"],
        "ma200":        kospi_data["ma200"],
        "gap_pct":      kospi_data["gap_pct"],
        "source":       kospi_data.get("source", "unknown"),
    }


def check_individual_stop_loss(holdings: list, config: dict = None) -> list:
    """보유 종목 중 스탑로스 기준 이하 종목 반환"""
    cfg    = config or get_config()
    sl_pct = cfg["portfolio"]["stop_loss_pct"] * 100   # -8.0

    stop_signals = []
    for h in holdings:
        if h.get("current_price") and h.get("avg_price"):
            loss_pct = (h["current_price"] - h["avg_price"]) / h["avg_price"] * 100
            if loss_pct <= sl_pct:
                stop_signals.append({
                    "ticker":   h["ticker"],
                    "name":     h["name"],
                    "loss_pct": round(loss_pct, 2),
                    "action":   "STOP_LOSS",
                })
    return stop_signals
