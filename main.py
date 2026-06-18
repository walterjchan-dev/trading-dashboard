from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
import yfinance as yf
import pandas as pd
from datetime import datetime
import html
import json
import os
import urllib.parse
import urllib.request

app = FastAPI()
MONITOR_FILE = "monitor.json"

def load_monitors():
    try:
        with open(MONITOR_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_monitors(monitors):
    with open(MONITOR_FILE, "w") as f:
        json.dump(monitors, f, indent=2)

def log_monitor_on(ticker):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("Telegram log skipped: missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return

    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": f"Monitor ON: {ticker}",
    }).encode()

    try:
        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data,
            timeout=5,
        ):
            pass
    except Exception as error:
        print(f"Telegram log failed: {error}")

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


RSI_TIMEFRAMES = {
    "15m": {"label": "15 Minute", "period": "5d", "interval": "15m"},
    "30m": {"label": "30 Minute", "period": "1mo", "interval": "30m"},
    "1h": {"label": "1 Hour", "period": "3mo", "interval": "1h"},
}

def calc_rsi(close, length=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1/length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/length, adjust=False).mean()

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def get_rsi_data(ticker, timeframe):
    config = RSI_TIMEFRAMES[timeframe]
    df = yf.download(
        ticker,
        period=config["period"],
        interval=config["interval"],
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

def get_snapshot_setup(rsi):
    if rsi >= 60:
        return "🟢 Strong"
    if rsi >= 50:
        return "🟡 Bullish"
    if rsi >= 40:
        return "🟠 Weakening"
    return "🔴 Weak"

def get_snapshot_trend(now, prev1, prev2):
    if now > prev1 > prev2:
        return "↑↑ Improving"
    if now < prev1 < prev2:
        return "↓↓ Weakening"
    if now > prev1:
        return "↑ Improving"
    if now < prev1:
        return "↓ Weakening"
    return "→ Flat"

def build_dashboard(title, watchlist, dashboard_path, timeframe="15m"):
    if timeframe not in RSI_TIMEFRAMES:
        timeframe = "15m"

    timeframe_label = RSI_TIMEFRAMES[timeframe]["label"]
    data = []
    monitors = load_monitors()

    for ticker in watchlist:
        result = get_rsi_data(ticker, timeframe)

        if result:
            if result["now"] >= 60:
                result["setup"] = "🟢 Strong"
            elif result["now"] >= 50:
                result["setup"] = "🟡 Bullish"
            elif result["now"] >= 40:
                result["setup"] = "🟠 Weakening"
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

        monitor_status = monitors.get(d["ticker"], "OFF")
        monitor_color = "#2e7d32" if monitor_status == "ON" else "#777"
        rows += f"""
        
        <tr style="background-color:{color}">
            <td>{d['ticker']}</td>
            <td>
                <a class="snapshot-button" href="/snapshot/{urllib.parse.quote(d['ticker'])}?return_to={urllib.parse.quote(dashboard_path)}&timeframe={timeframe}">
                    Snapshot
                </a>
            </td>
            <td>
                <form method="post" action="/monitor?ticker={d['ticker']}&return_to={dashboard_path}&timeframe={timeframe}">
                    <button type="submit" style="background:{monitor_color};color:white;border:0;padding:6px 12px;cursor:pointer;">
                        {monitor_status}
                    </button>
                </form>
            </td>
            <td>{d['price']:.2f}</td>
            <td>{d['now']:.2f}</td>
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
            .timeframe-toggle {{ margin: 15px 0 20px; }}
            .timeframe-toggle a {{
                display: inline-block;
                padding: 8px 14px;
                border: 1px solid #777;
                border-radius: 5px;
                text-decoration: none;
                font-size: 15px;
            }}
            .timeframe-toggle a.active {{
                background: #2e7d32;
                color: white;
                border-color: #2e7d32;
            }}
            .snapshot-button {{
                display: inline-block;
                background: #1565c0;
                color: white;
                padding: 6px 10px;
                border-radius: 5px;
                text-decoration: none;
                font-size: 14px;
                margin: 0;
            }}
        </style>
    </head>
    <body>
        {nav()}

        <h2>{title}</h2>
        <p>Updated: {updated}</p>
        <div class="timeframe-toggle">
            RSI Timeframe:
            <a class="{"active" if timeframe == "15m" else ""}" href="{dashboard_path}?timeframe=15m">15 Minute</a>
            <a class="{"active" if timeframe == "30m" else ""}" href="{dashboard_path}?timeframe=30m">30 Minute</a>
            <a class="{"active" if timeframe == "1h" else ""}" href="{dashboard_path}?timeframe=1h">1 Hour</a>
        </div>

        <table>
            <tr>
                <th>Ticker</th>
                <th>Multi-Timeframe</th>
                <th>Monitor</th>
                <th>Price</th>
                <th>RSI {timeframe_label}</th>
                <th>Setup</th>
                <th>Previous Bar</th>
                <th>Two Bars Ago</th>
                <th>Score</th>
                <th>Trend</th>
            </tr>
            {rows}
        </table>
    </body>
    </html>
    """

@app.get("/snapshot/{ticker}", response_class=HTMLResponse)
def multi_timeframe_snapshot(
    ticker: str,
    return_to: str = "/dashboard",
    timeframe: str = "15m",
):
    ticker = ticker.strip().upper()

    if return_to not in ("/dashboard", "/dashboard2", "/dashboard3"):
        return_to = "/dashboard"
    if timeframe not in RSI_TIMEFRAMES:
        timeframe = "15m"

    snapshot_data = []
    alignment_score = 0

    for timeframe_key, config in RSI_TIMEFRAMES.items():
        result = get_rsi_data(ticker, timeframe_key)

        if result:
            improving = result["now"] > result["prev1"] > result["prev2"]
            alignment_score += int(improving)
            result["timeframe"] = config["label"]
            result["setup"] = get_snapshot_setup(result["now"])
            result["snapshot_trend"] = get_snapshot_trend(
                result["now"],
                result["prev1"],
                result["prev2"],
            )

        snapshot_data.append((config["label"], result))

    rows = ""
    latest_price = None

    for label, result in snapshot_data:
        if not result:
            rows += f"""
            <tr>
                <td><strong>{label}</strong></td>
                <td colspan="6">Data unavailable</td>
            </tr>
            """
            continue

        if latest_price is None:
            latest_price = result["price"]

        row_class = "aligned" if result["now"] > result["prev1"] > result["prev2"] else ""
        rows += f"""
        <tr class="{row_class}">
            <td><strong>{label}</strong></td>
            <td>{result['now']:.2f}</td>
            <td>{result['prev1']:.2f}</td>
            <td>{result['prev2']:.2f}</td>
            <td>{result['setup']}</td>
            <td>{result['snapshot_trend']}</td>
            <td>{result['score']:+.2f}</td>
        </tr>
        """

    safe_ticker = html.escape(ticker)
    price_text = f"${latest_price:.2f}" if latest_price is not None else "Price unavailable"
    stars = "★" * alignment_score + "☆" * (3 - alignment_score)
    alignment_label = (
        "Fully aligned"
        if alignment_score == 3
        else "Partially aligned"
        if alignment_score > 0
        else "Not aligned"
    )
    updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    back_url = f"{return_to}?timeframe={timeframe}"

    return f"""
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{
                font-family: Arial, sans-serif;
                margin: 16px;
                color: #1f2937;
                background: #f5f7fa;
            }}
            .snapshot {{
                max-width: 900px;
                margin: 0 auto;
                background: white;
                padding: 18px;
                border-radius: 12px;
                box-shadow: 0 2px 10px rgba(0, 0, 0, 0.08);
            }}
            .top-line {{
                display: flex;
                justify-content: space-between;
                align-items: baseline;
                gap: 10px;
                flex-wrap: wrap;
            }}
            h1 {{ margin: 0; font-size: 26px; }}
            .price {{ font-size: 20px; font-weight: bold; }}
            .updated {{ color: #6b7280; font-size: 12px; margin: 5px 0 14px; }}
            table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
            th, td {{
                border: 1px solid #d1d5db;
                padding: 8px 6px;
                text-align: center;
                white-space: nowrap;
            }}
            th {{ background: #e5e7eb; }}
            tr.aligned {{ background: #dcfce7; }}
            .alignment {{
                margin-top: 14px;
                padding: 12px;
                border-radius: 8px;
                text-align: center;
                background: #eef2ff;
                font-size: 18px;
                font-weight: bold;
            }}
            .stars {{ color: #e6a700; letter-spacing: 2px; }}
            .back {{
                display: inline-block;
                margin-top: 14px;
                color: #1565c0;
                text-decoration: none;
                font-weight: bold;
            }}
            @media (max-width: 650px) {{
                body {{ margin: 6px; }}
                .snapshot {{ padding: 10px; border-radius: 7px; }}
                h1 {{ font-size: 22px; }}
                table {{ font-size: 10px; }}
                th, td {{ padding: 6px 3px; }}
                .alignment {{ font-size: 15px; }}
            }}
        </style>
    </head>
    <body>
        <div class="snapshot">
            <div class="top-line">
                <h1>{safe_ticker} Multi-Timeframe Snapshot</h1>
                <div class="price">{price_text}</div>
            </div>
            <div class="updated">Updated: {updated}</div>

            <table>
                <tr>
                    <th>Timeframe</th>
                    <th>RSI</th>
                    <th>Previous</th>
                    <th>2 Bars Ago</th>
                    <th>Setup</th>
                    <th>Trend</th>
                    <th>Score</th>
                </tr>
                {rows}
            </table>

            <div class="alignment">
                Alignment: <span class="stars">{stars}</span>
                ({alignment_score}/3 Improving) — {alignment_label}
            </div>

            <a class="back" href="{back_url}">← Back to dashboard</a>
        </div>
    </body>
    </html>
    """

@app.post("/monitor")
def toggle_monitor(ticker: str, return_to: str = "/dashboard", timeframe: str = "15m"):
    monitors = load_monitors()
    ticker = ticker.upper()
    monitors[ticker] = "OFF" if monitors.get(ticker) == "ON" else "ON"
    save_monitors(monitors)

    if monitors[ticker] == "ON":
        log_monitor_on(ticker)

    if return_to not in ("/dashboard", "/dashboard2", "/dashboard3"):
        return_to = "/dashboard"

    if timeframe not in RSI_TIMEFRAMES:
        timeframe = "15m"

    return RedirectResponse(f"{return_to}?timeframe={timeframe}", status_code=303)

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(timeframe: str = "15m"):
    return build_dashboard("Watchlist 1", load_watchlist("watchlist1.txt"), "/dashboard", timeframe)

@app.get("/dashboard2", response_class=HTMLResponse)
def dashboard2(timeframe: str = "15m"):
    return build_dashboard("Watchlist 2", load_watchlist("watchlist2.txt"), "/dashboard2", timeframe)

@app.get("/dashboard3", response_class=HTMLResponse)
def dashboard3(timeframe: str = "15m"):
    return build_dashboard("Watchlist 3", load_watchlist("watchlist3.txt"), "/dashboard3", timeframe)

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
