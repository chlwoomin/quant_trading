import yfinance as yf
print("yfinance OK:", yf.__version__)
df = yf.download("005930.KS", start="2024-01-01", end="2024-01-10", progress=False)
print(df)
