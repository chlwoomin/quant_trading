"""
portfolio_manager.py
가상 포트폴리오 관리: 매수/매도/성과 추적, SQLite 저장
"""

import sqlite3
import json
import os
from datetime import datetime, date
from typing import List, Dict, Optional


DB_PATH = os.path.join(os.path.dirname(__file__), "portfolio", "virtual_portfolio.db")
INITIAL_CASH = 10_000_000   # 가상 초기 자본: 1,000만원
TRANSACTION_FEE = 0.00015   # 수수료 0.015%
TRANSACTION_TAX = 0.0020    # 증권거래세 0.20% (매도 시)

# ── 브로커 모드 ───────────────────────────────────────────────────────────────
# BROKER_MODE=virtual  : pykrx 실제가 기반 가상 매매 (현재)
# BROKER_MODE=real     : 실제 증권사 API 매매 (미래 — KIS/키움 구현 필요)
BROKER_MODE = os.environ.get("BROKER_MODE", "virtual")


# ── DB 초기화 ─────────────────────────────────────────────────────────────────
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS portfolio_state (
            id          INTEGER PRIMARY KEY,
            date        TEXT NOT NULL,
            cash        REAL NOT NULL,
            total_value REAL NOT NULL,
            memo        TEXT
        );

        CREATE TABLE IF NOT EXISTS holdings (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker       TEXT NOT NULL,
            name         TEXT NOT NULL,
            shares       INTEGER NOT NULL,
            avg_price    REAL NOT NULL,
            buy_date     TEXT NOT NULL,
            current_price REAL,
            updated_at   TEXT
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            date       TEXT NOT NULL,
            type       TEXT NOT NULL,
            ticker     TEXT NOT NULL,
            name       TEXT NOT NULL,
            shares     INTEGER NOT NULL,
            price      REAL NOT NULL,
            amount     REAL NOT NULL,
            fee        REAL NOT NULL,
            tax        REAL NOT NULL,
            note       TEXT
        );

        CREATE TABLE IF NOT EXISTS performance_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            date         TEXT NOT NULL,
            total_value  REAL NOT NULL,
            cash         REAL NOT NULL,
            stock_value  REAL NOT NULL,
            daily_return REAL,
            cum_return   REAL,
            kospi_return REAL
        );
    """)
    conn.commit()

    # 초기 상태가 없으면 생성
    c.execute("SELECT COUNT(*) FROM portfolio_state")
    if c.fetchone()[0] == 0:
        today = date.today().isoformat()
        c.execute(
            "INSERT INTO portfolio_state (date, cash, total_value, memo) VALUES (?,?,?,?)",
            (today, INITIAL_CASH, INITIAL_CASH, "포트폴리오 초기화")
        )
        conn.commit()
        print(f"  포트폴리오 초기화: 가상 자본 {INITIAL_CASH:,.0f}원")

    conn.close()


# ── 현재 상태 조회 ────────────────────────────────────────────────────────────
def get_state() -> Dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    state = dict(c.execute(
        "SELECT * FROM portfolio_state ORDER BY id DESC LIMIT 1"
    ).fetchone())

    holdings = [dict(r) for r in c.execute(
        "SELECT * FROM holdings WHERE shares > 0"
    ).fetchall()]

    conn.close()
    return {"cash": state["cash"], "holdings": holdings,
            "total_value": state["total_value"]}


# ── 현재가 조회 (pykrx 실제가, 실패 시 DB 저장가 fallback) ──────────────────
def _get_real_price(ticker: str, fallback: float = None) -> float:
    """
    pykrx로 실제 현재가를 조회합니다.
    장 마감/주말/오류 시 fallback 가격(평균단가 또는 마지막 저장가)을 사용합니다.

    실제 매매 전환 시:
      BROKER_MODE=real 설정 후 이 함수를 증권사 API 호출로 교체하세요.
      예) KIS Open API: GET /uapi/domestic-stock/v1/quotations/inquire-price
    """
    try:
        from price_updater import get_current_price
        price = get_current_price(ticker)
        if price:
            return price
    except Exception:
        pass
    if fallback and fallback > 0:
        return fallback
    return 0.0  # 가격 조회 완전 실패 — 매매 건너뜀


# ── 리밸런싱 실행 ─────────────────────────────────────────────────────────────
def rebalance(screening_result: Dict) -> Dict:
    """
    스크리닝 결과를 받아 가상 포트폴리오를 리밸런싱합니다.
    - 기존 보유 중 탈락 종목 → 매도
    - 신규 선정 종목 → 동일 비중 매수
    """
    today = date.today().isoformat()
    state = get_state()
    cash = state["cash"]
    holdings = {h["ticker"]: h for h in state["holdings"]}
    selected_tickers = {r["ticker"] for r in screening_result["selected"]}

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    trades = {"sell": [], "buy": [], "hold": [], "errors": []}

    # ── 1단계: 탈락 종목 매도 ─────────────────────────────────────────────────
    for ticker, holding in holdings.items():
        if ticker not in selected_tickers:
            price = _get_real_price(ticker, holding.get("current_price") or holding["avg_price"])
            amount = holding["shares"] * price
            fee = amount * TRANSACTION_FEE
            tax = amount * TRANSACTION_TAX
            net = amount - fee - tax
            cash += net

            c.execute("""
                INSERT INTO transactions
                (date, type, ticker, name, shares, price, amount, fee, tax, note)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (today, "SELL", ticker, holding["name"],
                  holding["shares"], price, amount, fee, tax, "리밸런싱 매도"))
            c.execute("UPDATE holdings SET shares=0 WHERE ticker=?", (ticker,))

            pnl_pct = (price - holding["avg_price"]) / holding["avg_price"] * 100
            trades["sell"].append({
                "ticker": ticker, "name": holding["name"],
                "price": price, "pnl_pct": round(pnl_pct, 2)
            })

    # ── 2단계: 신규 종목 매수 (동일 비중) ────────────────────────────────────
    new_tickers = [r for r in screening_result["selected"]
                   if r["ticker"] not in holdings]

    if new_tickers:
        per_stock_budget = cash * 0.95 / len(new_tickers)  # 현금 5% 버퍼
        for item in new_tickers:
            ticker = item["ticker"]
            mock_price = _get_real_price(ticker)
            if not mock_price or mock_price <= 0:
                trades["errors"].append(f"{item['name']}: 가격 조회 실패")
                continue
            shares = max(1, int(per_stock_budget / mock_price))
            amount = shares * mock_price
            fee = amount * TRANSACTION_FEE
            total_cost = amount + fee

            if total_cost > cash:
                trades["errors"].append(f"{item['name']}: 잔고 부족 (주가 {mock_price:,.0f}원)")
                continue

            cash -= total_cost
            c.execute("""
                INSERT INTO transactions
                (date, type, ticker, name, shares, price, amount, fee, tax, note)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (today, "BUY", ticker, item["name"],
                  shares, mock_price, amount, fee, 0, "리밸런싱 매수"))

            existing = c.execute(
                "SELECT id FROM holdings WHERE ticker=?", (ticker,)
            ).fetchone()
            if existing:
                c.execute("""
                    UPDATE holdings SET shares=?, avg_price=?, updated_at=?
                    WHERE ticker=?
                """, (shares, mock_price, today, ticker))
            else:
                c.execute("""
                    INSERT INTO holdings
                    (ticker, name, shares, avg_price, buy_date, current_price, updated_at)
                    VALUES (?,?,?,?,?,?,?)
                """, (ticker, item["name"], shares, mock_price, today, mock_price, today))

            trades["buy"].append({
                "ticker": ticker, "name": item["name"],
                "shares": shares, "price": mock_price
            })

    # 보유 유지 종목
    for ticker in selected_tickers:
        if ticker in holdings:
            trades["hold"].append(ticker)

    # ── 3단계: 상태 업데이트 ─────────────────────────────────────────────────
    stock_value = sum(
        h[3] * _get_real_price(h[1], h[4])
        for h in c.execute("SELECT * FROM holdings WHERE shares > 0").fetchall()
    )
    total_value = cash + stock_value

    c.execute("""
        INSERT INTO portfolio_state (date, cash, total_value, memo)
        VALUES (?,?,?,?)
    """, (today, cash, total_value, f"리밸런싱: 매도{len(trades['sell'])} 매수{len(trades['buy'])}"))

    pnl_pct = (total_value - INITIAL_CASH) / INITIAL_CASH * 100
    c.execute("""
        INSERT INTO performance_log (date, total_value, cash, stock_value, cum_return)
        VALUES (?,?,?,?,?)
    """, (today, total_value, cash, stock_value, pnl_pct))

    conn.commit()
    conn.close()

    return {
        "date": today,
        "trades": trades,
        "cash": round(cash),
        "total_value": round(total_value),
        "pnl_pct": round(pnl_pct, 2),
    }


# ── 성과 리포트 ───────────────────────────────────────────────────────────────
def get_performance_summary() -> Dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    logs = [dict(r) for r in c.execute(
        "SELECT * FROM performance_log ORDER BY date"
    ).fetchall()]
    holdings = [dict(r) for r in c.execute(
        "SELECT * FROM holdings WHERE shares > 0"
    ).fetchall()]
    tx_count = c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    state = dict(c.execute(
        "SELECT * FROM portfolio_state ORDER BY id DESC LIMIT 1"
    ).fetchone())
    conn.close()

    total_value = state["total_value"]
    pnl = total_value - INITIAL_CASH
    pnl_pct = pnl / INITIAL_CASH * 100

    return {
        "total_value":    round(total_value),
        "initial_capital": INITIAL_CASH,
        "pnl":            round(pnl),
        "pnl_pct":        round(pnl_pct, 2),
        "cash":           round(state["cash"]),
        "holdings_count": len(holdings),
        "transaction_count": tx_count,
        "performance_log": logs,
        "holdings": holdings,
    }
