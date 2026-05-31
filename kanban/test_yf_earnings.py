import yfinance as yf
from datetime import datetime, date

t = yf.Ticker("AAPL")
cal = t.calendar
print(cal)
if cal and "Earnings Date" in cal:
    dates = cal["Earnings Date"]
    if dates:
        print("Next earnings:", dates[0])
