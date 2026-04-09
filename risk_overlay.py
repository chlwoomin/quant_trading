"""
risk_overlay.py
절대모멘텀 리스크 오버레이: KOSPI 200일 이동평균 기반 현금 전환 신호

- yfinance(^KS11) 로 KOSPI 지수 조회 (주 소스)
- pykrx 를 보조 소스로 사용
- strategy_config.json의 risk_overlay 파라미터 사용
"""

from datetime import datetime, timedelta
from typing import Dict

from strategy_manager import get_config


def get_kospi_ma200(date_str: str = None) -> Dict:
    """
    pykrx로 KOSPI 현재가와 200일 이동평균을 조회합니다.
    pykrx 미설치 또는 장 휴장 시 마지막 저장값(data/kospi_cache.json)을 사용합니다.
    """
    import os, json
    cache_path = os.path.join(os.path.dirname(__file__), "data", "kospi_cache.json")

    def _save_cache(data):
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def _load_cache():
        if os.path.exists(cache_path):
            with open(cache_path, encoding="utf-8") as f:
                return json.load(f)
        return None

    today = date_str or datetime.today().strftime("%Y-%m-%d")

    # ── 1차: yfinance ^KS11 ───────────────────────────────────────────────────
    try:
        import yfinance as yf
        end_dt  = datetime.strptime(today, "%Y-%m-%d")
        start_dt = (end_dt - timedelta(days=320)).strftime("%Y-%m-%d")
        raw = yf.download("^KS11", start=start_dt, end=today,
                          progress=False, auto_adjust=True)
        if isinstance(raw.columns, __import__("pandas").MultiIndex):
            closes_s = raw["Close"]["^KS11"].dropna()
        else:
            closes_s = raw["Close"].dropna()

        if len(closes_s) < 5:
            raise ValueError("yfinance KOSPI 데이터 부족")

        closes    = closes_s.values
        kospi_cur = float(closes[-1])
        ma200     = float(closes[-200:].mean()) if len(closes) >= 200 else float(closes.mean())
        gap_pct   = (kospi_cur - ma200) / ma200 * 100

        result = {
            "date":          today,
            "kospi_current": round(kospi_cur, 2),
            "ma200":         round(ma200, 2),
            "above_ma200":   kospi_cur > ma200,
            "gap_pct":       round(gap_pct, 2),
            "source":        "yfinance",
        }
        _save_cache(result)
        return result

    except Exception:
        pass

    # ── 2차: pykrx ───────────────────────────────────────────────────────────
    try:
        from pykrx import stock as pykrx_stock
        end   = datetime.strptime(today, "%Y-%m-%d")
        start = (end - timedelta(days=300)).strftime("%Y%m%d")
        end_s = end.strftime("%Y%m%d")

        df = pykrx_stock.get_market_ohlcv_by_date(start, end_s, "코스피")
        if df.empty:
            raise ValueError("pykrx KOSPI 데이터 없음")

        closes    = df["종가"].values.astype(float)
        kospi_cur = float(closes[-1])
        ma200     = float(closes[-200:].mean()) if len(closes) >= 200 else float(closes.mean())
        gap_pct   = (kospi_cur - ma200) / ma200 * 100

        result = {
            "date":          today,
            "kospi_current": round(kospi_cur, 2),
            "ma200":         round(ma200, 2),
            "above_ma200":   kospi_cur > ma200,
            "gap_pct":       round(gap_pct, 2),
            "source":        "pykrx",
        }
        _save_cache(result)
        return result

    except Exception:
        pass

    # ── 3차: 캐시 → fallback ─────────────────────────────────────────────────
    cached = _load_cache()
    if cached:
        cached["source"] = "cache"
        return cached
    return {
        "date": today, "kospi_current": 2500.0, "ma200": 2500.0,
        "above_ma200": True, "gap_pct": 0.0, "source": "fallback",
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
