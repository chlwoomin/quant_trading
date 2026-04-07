"""
portfolio_manager.py
가상 포트폴리오 관리: 매수/매도/성과 추적, SQLite 저장
"""

import sqlite3
import json
import os
from datetime import datetime, date
from typing import List, Dict, Optional
import random


DB_PATH = os.path.join(os.path.dirname(__file__), "portfolio", "virtual_portfolio.db")
INITIAL_CASH = 10_000_000   # 가상 초기 자본: 1,000만원
TRANSACTION_FEE = 0.00015   # 수수료 0.015%
TRANSACTION_TAX = 0.0020    # 증권거래세 0.20% (매도 시)


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


# ── 가상 현재가 조회 (실제 환경: pykrx로 대체) ───────────────────────────────
def get_mock_price(ticker: str, base_price: float = None) -> float:
    """실제 환경에서는 pykrx.stock.get_market_ohlcv() 사용"""
    if base_price is None:
        base_price = random.uniform(5000, 80000)
    change = random.uniform(-0.03, 0.03)
    return round(base_price * (1 + change))


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
            price = get_mock_price(ticker, holding["avg_price"])
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
            mock_price = random.randint(5000, 100000)
            shares = max(1, int(per_stock_budget / mock_price))
            amount = shares * mock_price
            fee = amount * TRANSACTION_FEE
            total_cost = amount + fee

            if total_cost > cash:
                trades["errors"].append(f"{item['name']}: 잔고 부족")
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
        h[3] * get_mock_price(h[1], h[4])
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
