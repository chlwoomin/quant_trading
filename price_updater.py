"""
price_updater.py
pykrx를 이용한 가상 포트폴리오 실시간 가격 업데이트

역할:
  - 보유 종목의 현재가를 실제 시세로 갱신 (SQLite holdings 테이블)
  - 스탑로스(-8% 기본) 발동 여부 체크 → 즉시 가상 매도
  - 일일 성과(daily_return, portfolio 평가액) 기록

장중 30분마다 호출하거나 장 마감 후 1회 호출해도 됩니다.
Python 3.9 환경 필요 (pykrx가 Python 3.9에서 안정 동작 확인됨).
"""

import os
import sqlite3
from datetime import date, datetime

try:
    from pykrx import stock as pykrx_stock
    PYKRX_OK = True
except Exception:
    PYKRX_OK = False

from portfolio_manager import DB_PATH, TRANSACTION_FEE, TRANSACTION_TAX, INITIAL_CASH
from strategy_manager  import get_config


# ── 현재가 조회 ───────────────────────────────────────────────────────────────
def _get_price_yfinance(ticker: str):
    """yfinance로 .KS 종목 현재가 조회 (fallback용)"""
    try:
        import yfinance as yf
        from datetime import timedelta
        end_dt  = date.today()
        start_dt = (end_dt - timedelta(days=10)).strftime("%Y-%m-%d")
        raw = yf.download(
            ticker + ".KS", start=start_dt, progress=False, auto_adjust=True
        )
        if isinstance(raw.columns, __import__("pandas").MultiIndex):
            col = raw["Close"][ticker + ".KS"].dropna()
        else:
            col = raw["Close"].dropna()
        if not col.empty:
            return float(col.iloc[-1])
    except Exception:
        pass
    return None


def get_current_price(ticker: str):
    """
    pykrx로 해당 종목의 당일 종가(또는 최근 거래일 종가)를 반환합니다.
    pykrx 실패 시 yfinance로 fallback.
    """
    # ── 1차: pykrx ──────────────────────────────────────────────────────────
    if PYKRX_OK:
        try:
            from datetime import timedelta
            today = date.today().strftime("%Y%m%d")
            df = pykrx_stock.get_market_ohlcv_by_date(today, today, ticker)
            if df.empty:
                start = (date.today() - timedelta(days=7)).strftime("%Y%m%d")
                df = pykrx_stock.get_market_ohlcv_by_date(start, today, ticker)
            if not df.empty:
                price = float(df["종가"].iloc[-1])
                # sanity: 가격이 100원 미만이면 데이터 오류로 간주
                if price >= 100:
                    return price
        except Exception:
            pass

    # ── 2차: yfinance ────────────────────────────────────────────────────────
    return _get_price_yfinance(ticker)


def get_all_current_prices(tickers: list) -> dict:
    """여러 종목 현재가를 dict로 반환합니다. {ticker: price}"""
    prices = {}
    for ticker in tickers:
        p = get_current_price(ticker)
        if p:
            prices[ticker] = p
    return prices


# ── 가상 포트폴리오 가격 갱신 ─────────────────────────────────────────────────
def update_portfolio_prices() -> dict:
    """
    보유 종목 현재가를 실제 시세로 업데이트하고,
    스탑로스 발동 종목은 가상 매도 처리합니다.

    Returns: {
        "updated": int,        # 가격 업데이트된 종목 수
        "stop_losses": list,   # 스탑로스 발동 종목 목록
        "total_value": float,
        "pnl_pct": float,
    }
    """
    today = date.today().isoformat()
    config = get_config()
    sl_pct = config["portfolio"]["stop_loss_pct"]

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    holdings = [dict(r) for r in c.execute(
        "SELECT * FROM holdings WHERE shares > 0"
    ).fetchall()]

    if not holdings:
        conn.close()
        return {"updated": 0, "stop_losses": [], "total_value": 0, "pnl_pct": 0}

    tickers = [h["ticker"] for h in holdings]
    prices  = get_all_current_prices(tickers)

    updated     = 0
    stop_losses = []
    cash_row    = c.execute(
        "SELECT cash FROM portfolio_state ORDER BY id DESC LIMIT 1"
    ).fetchone()
    cash = float(cash_row["cash"]) if cash_row else INITIAL_CASH

    for h in holdings:
        ticker = h["ticker"]
        price  = prices.get(ticker)
        if not price:
            continue

        # 현재가 업데이트
        c.execute(
            "UPDATE holdings SET current_price=?, updated_at=? WHERE ticker=?",
            (price, today, ticker)
        )
        updated += 1

        # 스탑로스 체크
        pnl_ratio = (price - h["avg_price"]) / h["avg_price"]
        if pnl_ratio <= sl_pct:
            proceeds = h["shares"] * price
            fee = proceeds * TRANSACTION_FEE
            tax = proceeds * TRANSACTION_TAX
            net = proceeds - fee - tax
            cash += net

            c.execute("""
                INSERT INTO transactions
                (date, type, ticker, name, shares, price, amount, fee, tax, note)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (today, "SELL", ticker, h["name"],
                  h["shares"], price, proceeds, fee, tax,
                  f"스탑로스 자동 매도 ({pnl_ratio:.1%})"))
            c.execute("UPDATE holdings SET shares=0 WHERE ticker=?", (ticker,))

            stop_losses.append({
                "ticker": ticker,
                "name":   h["name"],
                "loss_pct": round(pnl_ratio * 100, 2),
                "price":  price,
            })
            print(f"  ⚠ 스탑로스 발동: {h['name']} ({ticker}) {pnl_ratio:.1%}")

    # 포트폴리오 평가액 계산
    remaining = [dict(r) for r in c.execute(
        "SELECT * FROM holdings WHERE shares > 0"
    ).fetchall()]
    stock_value = sum(
        h["shares"] * (prices.get(h["ticker"]) or h.get("current_price") or h["avg_price"])
        for h in remaining
    )
    total_value = cash + stock_value
    pnl_pct     = (total_value - INITIAL_CASH) / INITIAL_CASH * 100

    # portfolio_state & performance_log 기록
    c.execute(
        "INSERT INTO portfolio_state (date, cash, total_value, memo) VALUES (?,?,?,?)",
        (today, cash, total_value, f"가격 업데이트: {updated}종목")
    )
    c.execute("""
        INSERT INTO performance_log (date, total_value, cash, stock_value, cum_return)
        VALUES (?,?,?,?,?)
    """, (today, total_value, cash, stock_value, pnl_pct))

    conn.commit()
    conn.close()

    result = {
        "updated":     updated,
        "stop_losses": stop_losses,
        "total_value": round(total_value),
        "pnl_pct":     round(pnl_pct, 2),
    }
    print(f"  가격 업데이트 완료: {updated}종목  "
          f"평가액 {total_value:,.0f}원 ({pnl_pct:+.2f}%)")
    return result


if __name__ == "__main__":
    print("가상 포트폴리오 가격 업데이트 중...")
    result = update_portfolio_prices()
    if result["stop_losses"]:
        print(f"\n⚠ 스탑로스 발동 {len(result['stop_losses'])}건:")
        for s in result["stop_losses"]:
            print(f"  {s['name']} ({s['ticker']}): {s['loss_pct']:+.1f}%")
