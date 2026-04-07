import yfinance as yf

t = yf.Ticker("005930.KS")

print("=== 분기 재무제표 ===")
try:
    bs = t.quarterly_balance_sheet
    inc = t.quarterly_income_stmt
    print("Balance sheet rows:", bs.index.tolist()[:10])
    print("Income stmt rows:", inc.index.tolist()[:10])
    print("\nBalance sheet (최근 2분기):")
    print(bs.iloc[:5, :2])
    print("\nIncome stmt (최근 2분기):")
    print(inc.iloc[:5, :2])
except Exception as e:
    print(f"재무제표 오류: {e}")

print("\n=== info 키 확인 ===")
info = t.info
relevant_keys = ["trailingPE", "forwardPE", "priceToBook", "returnOnEquity",
                 "debtToEquity", "marketCap", "sharesOutstanding",
                 "bookValue", "earningsPerShare"]
for k in relevant_keys:
    print(f"  {k}: {info.get(k)}")
