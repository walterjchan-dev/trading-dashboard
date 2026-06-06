from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import yfinance as yf
import pandas as pd
from datetime import datetime

app = FastAPI()

def load_watchlist(filename):
    try:
        with open(filename, "r") as f:
            return [
                line.strip().upper()
                for line in f
                if line.strip() and not line.strip().startswith("#")
            ]
    except FileNotFoundError:
        return []


MARKET = {
    "QQQ": "QQQ",
    "SPY": "SPY",
    "Oil": "CL=F",
    "10Y Yield": "^TNX",
    "VIX": "^VIX",
    "Bitcoin": "BTC-USD",
}

def nav():
    return """
    <div style="margin-bottom:20px;">
        <a href="/dashboard">Watchlist 1</a> |
        <a href="/dashboard2">Watchlist 2</a> |
        <a href="/dashboard3">Watchlist 3</a> |
        <a href="/market">Market Overview</a>
    </div>
    """

def get_market_data():
    data = []

    for name, ticker in MARKET.items():
        df = yf.download(
            ticker,
            period="10d",
            interval="1d",
            progress=False,
            auto_adjust=True
        )

        if df.empty or len(df) < 2:
            continue

        close = df["Close"]

        last = float(close.iloc[-1].iloc[0]) if hasattr(close.iloc[-1], "iloc") else float(close.iloc[-1])
        prev = float(close.iloc[-2].iloc[0]) if hasattr(close.iloc[-2], "iloc") else float(close.iloc[-2])

        change_pct = (last - prev) / prev * 100

        if change_pct > 1:
            signal = "🟢 Strong Up"
        elif change_pct > 0:
            signal = "🟡 Up"
        elif change_pct > -1:
            signal = "🟠 Slight Down"
        else:
            signal = "🔴 Down"

        data.append({
            "name": name,
            "price": last,
            "change_pct": change_pct,
            "signal": signal
        })

    return data


def get_rsi_1h(ticker):
    df = yf.download(
        ticker,
        period="10d",
        interval="1h",
        progress=False,
        auto_adjust=True
    )

    if df.empty:
        return None

    close = df["Close"]
    rsi = calc_rsi(close).dropna()

    if len(rsi) == 0:
        return None

    value = rsi.iloc[-1]

    if hasattr(value, "iloc"):
        value = value.iloc[0]

    return float(value)

def calc_rsi(close, length=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1/length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/length, adjust=False).mean()

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def get_rsi_data(ticker):
    df = yf.download(
        ticker,
        period="5d",
        interval="15m",
        progress=False,
        auto_adjust=True
    )

    if df.empty:
        return None

    close = df["Close"]
    rsi = calc_rsi(close).dropna()

    if len(rsi) < 3:
        return None

    now = float(rsi.iloc[-1].iloc[0]) if hasattr(rsi.iloc[-1], "iloc") else float(rsi.iloc[-1])
    prev1 = float(rsi.iloc[-2].iloc[0]) if hasattr(rsi.iloc[-2], "iloc") else float(rsi.iloc[-2])
    prev2 = float(rsi.iloc[-3].iloc[0]) if hasattr(rsi.iloc[-3], "iloc") else float(rsi.iloc[-3])
    score = now - prev2

    if now > prev1 > prev2:
        trend = "↑↑ Strong"
    elif now < prev1 < prev2:
        trend = "↓↓ Weak"
    elif now > prev1:
        trend = "↑ Improving"
    elif now < prev1:
        trend = "↓ Weakening"
    else:
        trend = "→ Flat"

    price = float(close.iloc[-1].iloc[0]) if hasattr(close.iloc[-1], "iloc") else float(close.iloc[-1])

    return {
        "ticker": ticker,
        "price": price,
        "now": now,
        "prev1": prev1,
        "prev2": prev2,
        "score": score,
        "trend": trend,
    }

def build_dashboard(title, watchlist):
    data = []

    for ticker in watchlist:
        result = get_rsi_data(ticker)

        if result:
            result["rsi1h"] = get_rsi_1h(ticker)

            rsi15 = result["now"]
            rsi1h = result["rsi1h"]

            if rsi1h is None:
                result["setup"] = "⚪ No 1H Data"
            elif rsi15 > 50 and rsi1h > 50:
                result["setup"] = "🟢 Strong"
            elif rsi15 < 50 and rsi1h > 50:
                result["setup"] = "🟡 Pullback"
            elif rsi15 > 50 and rsi1h < 50:
                result["setup"] = "🔵 Early Reversal"
            else:
                result["setup"] = "🔴 Weak"

            data.append(result)

    data.sort(key=lambda x: x["score"], reverse=True)

    rows = ""

    for d in data:
        score = d["score"]

        if score > 3:
            color = "#90EE90"
        elif score > 0:
            color = "#FFFF99"
        elif score > -3:
            color = "#FFD580"
        else:
            color = "#FF9999"

        rsi1h_text = f"{d['rsi1h']:.2f}" if d["rsi1h"] is not None else "N/A"
        rows += f"""
        
        <tr style="background-color:{color}">
            <td>{d['ticker']}</td>
            <td>{d['price']:.2f}</td>
            <td>{d['now']:.2f}</td>
            <td>{rsi1h_text}</td>
            <td>{d['setup']}</td>
            <td>{d['prev1']:.2f}</td>
            <td>{d['prev2']:.2f}</td>
            <td>{d['score']:+.2f}</td>
            <td>{d['trend']}</td>
        </tr>
        """

    updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return f"""
    <html>
    <head>
        <meta http-equiv="refresh" content="60">
        <style>
            body {{ font-family: Arial; margin: 30px; }}
            table {{ border-collapse: collapse; width: 100%; }}
            th, td {{ border: 1px solid #ccc; padding: 10px; text-align: center; }}
            th {{ background: #eee; }}
            a {{ font-size: 18px; margin-right: 10px; }}
        </style>
    </head>
    <body>
        {nav()}

        <h2>{title}</h2>
        <p>Updated: {updated}</p>

        <table>
            <tr>
                <th>Ticker</th>
                <th>Price</th>
                <th>RSI15</th>
                <th>RSI1H</th>
                <th>Setup</th>
                <th>-15m</th>
                <th>-30m</th>
                <th>Score</th>
                <th>Trend</th>
            </tr>
            {rows}
        </table>
    </body>
    </html>
    """

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return build_dashboard("Watchlist 1", load_watchlist("watchlist1.txt"))

@app.get("/dashboard2", response_class=HTMLResponse)
def dashboard2():
    return build_dashboard("Watchlist 2", load_watchlist("watchlist2.txt"))

@app.get("/dashboard3", response_class=HTMLResponse)
def dashboard3():
    return build_dashboard("Watchlist 3", load_watchlist("watchlist3.txt"))

@app.get("/market", response_class=HTMLResponse)
def market():
    data = get_market_data()
    updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    rows = ""

    for d in data:
        rows += f"""
        <tr>
            <td>{d['name']}</td>
            <td>{d['price']:.2f}</td>
            <td>{d['change_pct']:+.2f}%</td>
            <td>{d['signal']}</td>
        </tr>
        """

    return f"""
    <html>
    <head>
        <meta http-equiv="refresh" content="60">
        <style>
            body {{ font-family: Arial; margin: 30px; }}
            table {{ border-collapse: collapse; width: 100%; }}
            th, td {{ border: 1px solid #ccc; padding: 10px; text-align: center; }}
            th {{ background: #eee; }}
            a {{ font-size: 18px; margin-right: 10px; }}
        </style>
    </head>
    <body>
        {nav()}

        <h2>Market Overview</h2>
        <p>Updated: {updated}</p>

        <table>
            <tr>
                <th>Market</th>
                <th>Price</th>
                <th>Daily Change</th>
                <th>Signal</th>
            </tr>
            {rows}
        </table>
    </body>
    </html>
    """
