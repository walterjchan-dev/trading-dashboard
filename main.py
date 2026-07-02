from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
import yfinance as yf
import pandas as pd
from datetime import date, datetime, timedelta
import csv
import html
import json
import os
import urllib.parse
import urllib.request

app = FastAPI()
MONITOR_FILE = "monitor.json"
GAP_ALERTS_FILE = "gap_alerts.json"
PORTFOLIO_FILE = "portfolio.json"
PORTFOLIO_CSV_FILE = "portfolio.csv"
EARNINGS_CACHE_FILE = "earnings_cache.json"
EARNINGS_CACHE_TTL = timedelta(hours=24)
GAP_UP_THRESHOLD_PCT = 3.0
GAP_SUPPORT_NEAR_PCT = 0.75

def load_monitors():
    try:
        with open(MONITOR_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_monitors(monitors):
    with open(MONITOR_FILE, "w") as f:
        json.dump(monitors, f, indent=2)

def load_gap_alerts():
    try:
        with open(GAP_ALERTS_FILE, "r") as f:
            alerts = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

    return alerts if isinstance(alerts, dict) else {}

def save_gap_alerts(alerts):
    with open(GAP_ALERTS_FILE, "w") as f:
        json.dump(alerts, f, indent=2)

def load_portfolio():
    try:
        with open(PORTFOLIO_FILE, "r") as f:
            portfolio = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

    return {
        ticker.upper(): position
        for ticker, position in portfolio.items()
        if isinstance(position, dict)
    }

def save_portfolio(portfolio):
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(portfolio, f, indent=2)

def load_earnings_cache():
    try:
        with open(EARNINGS_CACHE_FILE, "r") as f:
            cache = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

    return cache if isinstance(cache, dict) else {}

def save_earnings_cache(cache):
    with open(EARNINGS_CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)

def parse_earnings_date(value):
    if value is None:
        return None

    if isinstance(value, (list, tuple, set, pd.Series, pd.Index)):
        dates = [parse_earnings_date(item) for item in value]
        dates = [item for item in dates if item]
        return min(dates) if dates else None

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    try:
        parsed = pd.to_datetime(value)
    except (TypeError, ValueError):
        return None

    if pd.isna(parsed):
        return None

    return parsed.date()

def parse_cache_datetime(value):
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None

    return parsed

def next_future_date(values):
    today = date.today()
    dates = []

    for value in values:
        parsed = parse_earnings_date(value)
        if parsed and parsed >= today:
            dates.append(parsed)

    return min(dates) if dates else None

def extract_calendar_dates(calendar):
    if calendar is None:
        return []

    if isinstance(calendar, dict):
        values = []

        for key, value in calendar.items():
            if "earnings" in str(key).lower():
                values.append(value)

        return values

    if isinstance(calendar, pd.DataFrame):
        values = []

        for column in calendar.columns:
            if "earnings" in str(column).lower():
                values.extend(calendar[column].dropna().tolist())

        index_labels = [str(item).lower() for item in calendar.index]
        for row_index, label in enumerate(index_labels):
            if "earnings" in label:
                values.extend(calendar.iloc[row_index].dropna().tolist())

        return values

    return []

def fetch_earnings_date(ticker):
    stock = yf.Ticker(ticker)

    try:
        earnings_dates = stock.get_earnings_dates(limit=12)
        if isinstance(earnings_dates, pd.DataFrame) and not earnings_dates.empty:
            upcoming = next_future_date(list(earnings_dates.index))
            if upcoming:
                return upcoming.isoformat()

            if "Earnings Date" in earnings_dates.columns:
                upcoming = next_future_date(earnings_dates["Earnings Date"].tolist())
                if upcoming:
                    return upcoming.isoformat()
    except Exception as error:
        print(f"Earnings dates unavailable for {ticker}: {error}")

    try:
        upcoming = next_future_date(extract_calendar_dates(stock.calendar))
        if upcoming:
            return upcoming.isoformat()
    except Exception as error:
        print(f"Earnings calendar unavailable for {ticker}: {error}")

    return None

def earnings_cache_entry_is_fresh(entry):
    if not isinstance(entry, dict):
        return False

    fetched_at = parse_cache_datetime(entry.get("fetched_at"))
    if not fetched_at or datetime.now() - fetched_at > EARNINGS_CACHE_TTL:
        return False

    earnings_date = parse_earnings_date(entry.get("date"))
    return earnings_date is None or earnings_date >= date.today()

def get_earnings_info(ticker, cache):
    ticker = ticker.upper()
    entry = cache.get(ticker)

    if not earnings_cache_entry_is_fresh(entry):
        entry = {
            "date": fetch_earnings_date(ticker),
            "fetched_at": datetime.now().isoformat(),
        }
        cache[ticker] = entry

    earnings_date = parse_earnings_date(entry.get("date"))
    if not earnings_date:
        return {
            "date_text": "-",
            "days": None,
            "risk": "none",
            "style": "",
            "reason": "",
        }

    days = (earnings_date - date.today()).days
    if days <= 7:
        risk = "high"
        style = "background:#fecaca;color:#7f1d1d;font-weight:bold;"
    elif days <= 14:
        risk = "caution"
        style = "background:#fef08a;color:#713f12;font-weight:bold;"
    else:
        risk = "normal"
        style = ""

    day_label = "today" if days == 0 else f"in {days} day{'s' if days != 1 else ''}"

    return {
        "date_text": earnings_date.strftime("%Y-%m-%d"),
        "days": days,
        "risk": risk,
        "style": style,
        "reason": f"earnings {day_label}",
    }

def get_csv_value(row, *names):
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    return ""

def load_portfolio_csv():
    portfolio = {}

    try:
        with open(PORTFOLIO_CSV_FILE, "r", newline="") as f:
            reader = csv.DictReader(f)

            for raw_row in reader:
                row = {
                    key.strip().lower().replace(" ", "_"): value.strip()
                    for key, value in raw_row.items()
                    if key
                }
                ticker = get_csv_value(
                    row,
                    "ticker",
                    "symbol",
                    "financial_instrument",
                    "instrument",
                ).upper()
                shares = safe_float(
                    get_csv_value(row, "shares", "quantity", "qty", "position")
                )
                average_price = safe_float(
                    get_csv_value(row, "average_price", "avg_price", "avg_cost", "average_cost")
                )
                currency = get_csv_value(row, "account_currency", "currency")
                notes = get_csv_value(row, "notes", "note")

                if not ticker or shares <= 0:
                    continue

                position = {
                    "shares": shares,
                    "average_price": average_price,
                    "currency": currency or "USD",
                }

                if notes:
                    position["notes"] = notes

                portfolio[ticker] = position
    except FileNotFoundError:
        return {}

    return portfolio

def count_portfolio_csv_rows():
    try:
        with open(PORTFOLIO_CSV_FILE, "r", newline="") as f:
            return sum(1 for row in csv.DictReader(f) if any(row.values()))
    except FileNotFoundError:
        return 0

def send_telegram_message(text):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("Telegram message skipped: missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return

    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
    }).encode()

    try:
        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data,
            timeout=8,
        ):
            pass
    except Exception as error:
        print(f"Telegram message failed: {error}")

def log_monitor_on(ticker):
    send_telegram_message(f"Monitor ON: {ticker}")

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
        <a href="/monitors">Monitors</a> |
        <a href="/gap-up">Gap-Up Monitor</a> |
        <a href="/portfolio">Portfolio</a> |
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
            "ticker": ticker,
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

def as_series(value):
    if isinstance(value, pd.DataFrame):
        return value.iloc[:, 0]
    return value

def series_float(series, index):
    value = series.iloc[index]
    return float(value.iloc[0]) if hasattr(value, "iloc") else float(value)

def unique_watchlist_tickers():
    tickers = []
    seen = set()

    for filename in ("watchlist1.txt", "watchlist2.txt", "watchlist3.txt"):
        for ticker in load_watchlist(filename):
            if ticker not in seen:
                tickers.append(ticker)
                seen.add(ticker)

    return tickers

def get_previous_regular_close(ticker):
    daily = yf.download(
        ticker,
        period="10d",
        interval="1d",
        progress=False,
        auto_adjust=False,
    )

    if daily.empty:
        return None, None

    close = as_series(daily["Close"]).dropna()
    if close.empty:
        return None, None

    today = date.today()
    historical_close = close[close.index.date < today]
    if historical_close.empty:
        historical_close = close.iloc[:-1]

    if historical_close.empty:
        return None, None

    previous_close = float(historical_close.iloc[-1])
    previous_date = pd.Timestamp(historical_close.index[-1]).date()
    return previous_close, previous_date

def get_gap_up_data(ticker):
    daily = yf.download(
        ticker,
        period="30d",
        interval="1d",
        progress=False,
        auto_adjust=False,
    )

    if daily.empty:
        return None

    close_daily = as_series(daily["Close"]).dropna()
    if close_daily.empty:
        return None

    today = date.today()
    historical_close = close_daily[close_daily.index.date < today]
    if historical_close.empty:
        historical_close = close_daily.iloc[:-1]

    if historical_close.empty:
        return None

    previous_close = float(historical_close.iloc[-1])
    previous_date = pd.Timestamp(historical_close.index[-1]).date()
    if previous_close is None or previous_close <= 0:
        return None

    intraday = yf.download(
        ticker,
        period="5d",
        interval="5m",
        prepost=True,
        progress=False,
        auto_adjust=False,
    )

    if intraday.empty:
        return None

    open_price = as_series(intraday["Open"]).dropna()
    high = as_series(intraday["High"]).dropna()
    low = as_series(intraday["Low"]).dropna()
    close = as_series(intraday["Close"]).dropna()
    volume = as_series(intraday["Volume"]).fillna(0)

    if close.empty or low.empty:
        return None

    latest_date = pd.Timestamp(close.index[-1]).date()
    date_mask = close.index.date == latest_date
    today_close = close[date_mask]
    today_low = low[low.index.date == latest_date]
    today_high = high[high.index.date == latest_date]
    today_open = open_price[open_price.index.date == latest_date]
    today_volume = volume[volume.index.date == latest_date]

    if today_close.empty or today_low.empty or today_volume.empty:
        return None

    current_price = float(today_close.iloc[-1])
    first_price = float(today_open.iloc[0]) if not today_open.empty else current_price
    gap_price = first_price if first_price > 0 else current_price
    gap_pct = (gap_price - previous_close) / previous_close * 100

    typical_price = (
        today_high.reindex(today_close.index).ffill()
        + today_low.reindex(today_close.index).ffill()
        + today_close
    ) / 3
    cumulative_volume = today_volume.reindex(today_close.index).fillna(0).cumsum()
    cumulative_value = (typical_price * today_volume.reindex(today_close.index).fillna(0)).cumsum()
    vwap = None
    if not cumulative_volume.empty and float(cumulative_volume.iloc[-1]) > 0:
        vwap = float(cumulative_value.iloc[-1] / cumulative_volume.iloc[-1])

    ema22 = float(today_close.ewm(span=22, adjust=False).mean().iloc[-1])
    rsi_values = calc_rsi(today_close).dropna()
    rsi = float(rsi_values.iloc[-1]) if not rsi_values.empty else None
    intraday_volume = int(today_volume.sum())

    avg_daily_volume = None
    volume_strong = False
    daily_volume = as_series(daily["Volume"]).dropna()
    historical_volume = daily_volume[daily_volume.index.date < latest_date]
    if not historical_volume.empty:
        avg_daily_volume = float(historical_volume.tail(20).mean())
        elapsed_fraction = min(max(len(today_volume) * 5 / 390, 0.05), 1)
        expected_volume = avg_daily_volume * elapsed_fraction
        volume_strong = intraday_volume >= expected_volume * 1.2

    support_values = [value for value in (vwap, ema22) if value and value > 0]
    support_level = min(support_values) if support_values else None
    low_today = float(today_low.min())
    above_ema22 = current_price >= ema22
    above_vwap = vwap is not None and current_price >= vwap
    rsi_above_50 = rsi is not None and rsi >= 50
    near_support = (
        support_level is not None
        and low_today <= support_level * (1 + GAP_SUPPORT_NEAR_PCT / 100)
    )
    reclaimed_support = near_support and above_vwap and above_ema22
    gap_up = gap_pct >= GAP_UP_THRESHOLD_PCT

    if not gap_up:
        status = "NO GAP"
    elif current_price <= previous_close or (not above_vwap and not above_ema22):
        status = "GAP FAILED"
    elif reclaimed_support and rsi_above_50 and volume_strong:
        status = "PULLBACK BUY WATCH"
    elif current_price >= gap_price and rsi_above_50:
        status = "GAP HELD"
    else:
        status = "GAP UP WATCH"

    reasons = []
    reasons.append(
        f"Gap up {gap_pct:+.2f}%"
        if gap_up
        else f"Gap below threshold at {gap_pct:+.2f}%"
    )
    reasons.append("Volume above normal" if volume_strong else "Volume not above normal yet")
    reasons.append("Above EMA22" if above_ema22 else "Below EMA22")
    reasons.append("Above VWAP" if above_vwap else "Below VWAP")
    reasons.append("RSI above 50" if rsi_above_50 else "RSI below 50")
    if gap_up and status == "GAP UP WATCH":
        reasons.append("Wait for pullback to VWAP / EMA22")
    if status == "PULLBACK BUY WATCH":
        reasons.append("Pulled back near support and reclaimed VWAP / EMA22")

    return {
        "ticker": ticker,
        "status": status,
        "gap_pct": gap_pct,
        "gap_price": gap_price,
        "current_price": current_price,
        "previous_close": previous_close,
        "previous_date": previous_date,
        "intraday_volume": intraday_volume,
        "avg_daily_volume": avg_daily_volume,
        "volume_strong": volume_strong,
        "vwap": vwap,
        "ema22": ema22,
        "rsi": rsi,
        "above_vwap": above_vwap,
        "above_ema22": above_ema22,
        "near_support": near_support,
        "reclaimed_support": reclaimed_support,
        "reasons": reasons,
    }

def format_compact_volume(value):
    if value is None:
        return "N/A"

    number = float(value)
    if number >= 1_000_000:
        return f"{number / 1_000_000:.2f}M"
    if number >= 1_000:
        return f"{number / 1_000:.1f}K"
    return f"{number:.0f}"

def should_send_gap_alert(row, alert_memory):
    if row["status"] not in ("GAP UP WATCH", "PULLBACK BUY WATCH"):
        return False

    key = row["ticker"]
    current_state = f"{date.today().isoformat()}:{row['status']}:{row['gap_pct']:.2f}"
    if alert_memory.get(key) == current_state:
        return False

    alert_memory[key] = current_state
    return True

def send_gap_alert(row):
    lines = [
        "GAP-UP MONITOR",
        f"Ticker: {row['ticker']}",
        f"Gap: {row['gap_pct']:+.2f}%",
        f"Current price: {row['current_price']:.2f}",
        f"Previous close: {row['previous_close']:.2f}",
        "",
        f"Status: {row['status']}",
        "",
        "Reasons:",
    ]
    lines.extend(f"- {reason}" for reason in row["reasons"])
    send_telegram_message("\n".join(lines))

def get_price_structure(df, timeframe):
    interval_minutes = {"15m": 15, "30m": 30, "1h": 60}
    last_candle_start = pd.Timestamp(df.index[-1])

    if last_candle_start.tzinfo is None:
        now = pd.Timestamp.now()
    else:
        now = pd.Timestamp.now(tz=last_candle_start.tzinfo)

    last_candle_complete = now >= (
        last_candle_start + pd.Timedelta(minutes=interval_minutes[timeframe])
    )
    completed = df.iloc[-5:] if last_candle_complete else df.iloc[-6:-1]

    if len(completed) < 5:
        return {"structure": "N/A", "structure_score": 0}

    highs = completed["High"]
    lows = completed["Low"]
    if isinstance(highs, pd.DataFrame):
        highs = highs.iloc[:, 0]
    if isinstance(lows, pd.DataFrame):
        lows = lows.iloc[:, 0]

    comparisons = []
    structure_score = 0

    for index in range(1, len(completed)):
        higher_high = highs.iloc[index] > highs.iloc[index - 1]
        higher_low = lows.iloc[index] > lows.iloc[index - 1]
        lower_high = highs.iloc[index] < highs.iloc[index - 1]
        lower_low = lows.iloc[index] < lows.iloc[index - 1]

        structure_score += int(higher_high) + int(higher_low)
        structure_score -= int(lower_high) + int(lower_low)
        comparisons.append((higher_high, higher_low, lower_high, lower_low))

    strong_comparisons = sum(
        1 for higher_high, higher_low, _, _ in comparisons
        if higher_high and higher_low
    )
    latest_higher_high, latest_higher_low, latest_lower_high, latest_lower_low = comparisons[-1]

    if latest_lower_high and latest_lower_low:
        structure = "🔴 Lower Low"
    elif strong_comparisons >= 3:
        structure = "🟢 HH/HL Strong"
    elif not latest_higher_low:
        structure = "🟠 Weakening"
    else:
        structure = "🟡 Pullback"

    return {
        "structure": structure,
        "structure_score": structure_score,
    }

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
    price_structure = get_price_structure(df, timeframe)

    return {
        "ticker": ticker,
        "price": price,
        "now": now,
        "prev1": prev1,
        "prev2": prev2,
        "score": score,
        "trend": trend,
        **price_structure,
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

def get_monitor_score(data_15m, data_1h):
    score = 0
    reasons = []

    if data_15m:
        if data_15m["now"] >= 50:
            score += 2
            reasons.append("15m RSI bullish")
        if data_15m["now"] > data_15m["prev1"]:
            score += 2
            reasons.append("15m RSI improving")
        if data_15m["structure_score"] > 0:
            score += 1
            reasons.append("15m HH/HL positive")

    if data_1h:
        if data_1h["now"] >= 50:
            score += 2
            reasons.append("1H RSI bullish")
        if data_1h["now"] > data_1h["prev1"]:
            score += 2
            reasons.append("1H RSI improving")

    if data_15m and data_1h and data_15m["now"] > data_15m["prev1"] and data_1h["now"] > data_1h["prev1"]:
        score += 1
        reasons.append("15m and 1H aligned")

    return min(score, 10), ", ".join(reasons) if reasons else "No bullish confirmation yet"

def get_bottom_timeframe_data(ticker, timeframe):
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

    open_price = as_series(df["Open"])
    low = as_series(df["Low"])
    close = as_series(df["Close"])
    rsi = calc_rsi(close).dropna()
    rsi_ma = rsi.rolling(14).mean().dropna()
    ema21 = close.ewm(span=21, adjust=False).mean()

    if len(rsi) < 3 or len(rsi_ma) < 2 or len(ema21) < 2 or len(close) < 2:
        return None

    now = series_float(rsi, -1)
    prev1 = series_float(rsi, -2)
    current_rsi_ma = series_float(rsi_ma, -1)
    previous_rsi_ma = series_float(rsi_ma, -2)
    current_close = series_float(close, -1)
    previous_close = series_float(close, -2)
    current_ema21 = series_float(ema21, -1)
    previous_ema21 = series_float(ema21, -2)
    current_open = series_float(open_price, -1)
    current_low = series_float(low, -1)
    previous_low = series_float(low, -2)
    price_vs_ema_pct = (current_close - current_ema21) / current_ema21 * 100

    if now > prev1:
        trend = "Rising"
    elif now < prev1:
        trend = "Falling"
    else:
        trend = "Flat"

    return {
        "price": current_close,
        "rsi": now,
        "rsi_ma": current_rsi_ma,
        "rsi_rising": now > prev1,
        "rsi_above_50": now > 50,
        "crossed_above_rsi_ma": prev1 <= previous_rsi_ma and now > current_rsi_ma,
        "price_reclaimed_ema21": previous_close <= previous_ema21 and current_close > current_ema21,
        "price_above_ema21": current_close > current_ema21,
        "higher_low_detected": current_low > previous_low,
        "bullish_candle": current_close > current_open,
        "price_vs_ema21": (
            f"Above EMA21 by {price_vs_ema_pct:.2f}%"
            if current_close >= current_ema21
            else f"Below EMA21 by {abs(price_vs_ema_pct):.2f}%"
        ),
        "trend": trend,
    }

def get_bottom_signal(ticker):
    data_15m = get_bottom_timeframe_data(ticker, "15m")
    data_1h = get_bottom_timeframe_data(ticker, "1h")
    score = 0
    reasons = []

    if data_15m:
        if data_15m["rsi_rising"]:
            score += 1
            reasons.append("15m RSI rising")
        if data_15m["rsi_above_50"]:
            score += 1
            reasons.append("15m RSI above 50")
        if data_15m["crossed_above_rsi_ma"]:
            score += 2
            reasons.append("15m RSI crossed above RSI MA")
        if data_15m["price_above_ema21"]:
            score += 2
            reasons.append("price above EMA21")
        if data_15m["higher_low_detected"]:
            score += 1
            reasons.append("higher low detected")

    if data_1h:
        if data_1h["rsi_rising"]:
            score += 1
            reasons.append("1H RSI rising")
        if data_1h["rsi_above_50"]:
            score += 2
            reasons.append("1H RSI above 50")

    score = min(score, 10)
    if reasons:
        reason = ", ".join(reasons[:3])
        if score >= 8 and data_1h and data_1h["rsi_above_50"]:
            reason = f"{reason}, higher-timeframe confirmation"
        elif score >= 5:
            reason = f"{reason}, good watch candidate"
            if data_1h and not data_1h["rsi_above_50"]:
                reason = f"{reason}; 1H RSI still below 50"
    elif data_15m and data_15m["rsi"] < data_15m["rsi_ma"]:
        reason = "Weak signal: RSI still below MA"
    else:
        reason = "Weak signal: no bottom confirmation yet"

    return {
        "ticker": ticker,
        "price": data_15m["price"] if data_15m else data_1h["price"] if data_1h else None,
        "score": score,
        "reason": reason,
        "rsi_15m": data_15m["rsi"] if data_15m else None,
        "rsi_1h": data_1h["rsi"] if data_1h else None,
        "price_vs_ema21": data_15m["price_vs_ema21"] if data_15m else "N/A",
        "rsi_trend": (
            f"15m: {data_15m['trend'] if data_15m else 'N/A'} / "
            f"1H: {data_1h['trend'] if data_1h else 'N/A'}"
        ),
    }

def safe_float(value, default=0):
    try:
        return float(str(value).replace(",", "").replace("'", ""))
    except (TypeError, ValueError):
        return default

def get_payload_value(payload, *names):
    for name in names:
        value = payload.get(name)
        if value not in (None, ""):
            return value

    lowered_names = {name.lower() for name in names}
    for key, value in payload.items():
        if str(key).lower() in lowered_names and value not in (None, ""):
            return value

    return None

def format_payload_number(value, decimals=2):
    number = safe_float(value, None)
    if number is None:
        return str(value) if value not in (None, "") else None

    return f"{number:.{decimals}f}"

def format_payload_score(value):
    number = safe_float(value, None)
    if number is None:
        return str(value) if value not in (None, "") else None

    return f"{number:g}/10"

def format_payload_bool(value):
    if value in (None, ""):
        return None

    if isinstance(value, bool):
        return "true" if value else "false"

    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "y"):
        return "true"
    if text in ("false", "0", "no", "n"):
        return "false"

    return str(value)

def get_webhook_score(payload, signal):
    raw_score = get_payload_value(payload, "score")
    score = safe_float(raw_score, None)

    if score and score > 0:
        return score

    signal_text = str(signal or "").upper()
    if "MOMENTUM" in signal_text:
        return 7

    return score

def get_position_details(ticker, price, portfolio):
    position = portfolio.get(ticker, {})
    shares = safe_float(position.get("shares", 0))
    average_price = safe_float(
        position.get("average_price", position.get("avg_price", 0))
    )
    currency = position.get("account_currency", position.get("currency", ""))

    if shares <= 0:
        return {
            "owned": False,
            "position": "-",
            "shares": "-",
            "avg_cost": "-",
            "pl_pct": "-",
        }

    pl_pct = None
    if average_price > 0 and price is not None:
        pl_pct = (price - average_price) / average_price * 100

    return {
        "owned": True,
        "position": f"{shares:g}",
        "shares": f"{shares:g}",
        "avg_cost": f"{average_price:.2f} {currency}".strip(),
        "pl_pct": f"{pl_pct:+.2f}%" if pl_pct is not None else "-",
    }

def build_dashboard(title, watchlist, dashboard_path, timeframe="15m"):
    if timeframe not in RSI_TIMEFRAMES:
        timeframe = "15m"

    timeframe_label = RSI_TIMEFRAMES[timeframe]["label"]
    data = []
    monitors = load_monitors()
    portfolio = load_portfolio()
    earnings_cache = load_earnings_cache()

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
        position = get_position_details(d["ticker"], d["price"], portfolio)
        earnings = get_earnings_info(d["ticker"], earnings_cache)
        ticker_style = "font-weight:bold;color:#0f5132;" if position["owned"] else ""
        owned_style = "outline:2px solid #86efac;" if position["owned"] else ""
        rows += f"""
        
        <tr style="background-color:{color};{owned_style}">
            <td style="{ticker_style}">{d['ticker']}</td>
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
            <td>{position['position']}</td>
            <td>{position['avg_cost']}</td>
            <td>{position['pl_pct']}</td>
            <td style="{earnings['style']}">{earnings['date_text']}</td>
            <td>{d['now']:.2f}</td>
            <td>{d['setup']}</td>
            <td>{d['prev1']:.2f}</td>
            <td>{d['prev2']:.2f}</td>
            <td>{d['score']:+.2f}</td>
            <td>{d['trend']}</td>
            <td>{d['structure']}</td>
            <td>{d['structure_score']:+d}</td>
        </tr>
        """

    save_earnings_cache(earnings_cache)
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
                <th>Position</th>
                <th>Avg Cost</th>
                <th>Unrealized P/L %</th>
                <th>Earnings Date</th>
                <th>RSI {timeframe_label}</th>
                <th>Setup</th>
                <th>Previous Bar</th>
                <th>Two Bars Ago</th>
                <th>Score</th>
                <th>Trend</th>
                <th>HH/HL</th>
                <th>Structure Score</th>
            </tr>
            {rows}
        </table>
    </body>
    </html>
    """

@app.post("/webhook")
async def webhook(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return {"ok": False, "error": "Invalid JSON"}

    ticker = str(get_payload_value(payload, "ticker", "symbol") or "").upper()
    signal = get_payload_value(payload, "signal")
    price = get_payload_value(payload, "price")
    timeframe = get_payload_value(payload, "timeframe", "interval")
    score = format_payload_score(get_webhook_score(payload, signal))

    lines = ["Trading Dashboard Alert", ""]
    if ticker:
        lines.append(f"Ticker: {ticker}")
    if timeframe:
        lines.append(f"Timeframe: {timeframe}")
    if price not in (None, ""):
        lines.append(f"Price: {price}")

    if signal:
        lines.extend(["", "Signal:", str(signal)])

    optional_fields = (
        ("RSI", format_payload_number(get_payload_value(payload, "rsi"))),
        ("RSI MA", format_payload_number(get_payload_value(payload, "rsi_ma"))),
        ("EMA21", format_payload_number(get_payload_value(payload, "ema21"))),
        ("BB Upper", format_payload_number(get_payload_value(payload, "bb_upper"))),
        ("BB Lower", format_payload_number(get_payload_value(payload, "bb_lower"))),
        ("BB Distance", format_payload_number(get_payload_value(payload, "bb_distance"))),
        ("ATR", format_payload_number(get_payload_value(payload, "atr"))),
        ("Volume", get_payload_value(payload, "volume")),
        ("Bar Time", get_payload_value(payload, "bar_time")),
        ("Score", score),
        ("RSI Above MA", format_payload_bool(get_payload_value(payload, "rsi_above_ma"))),
        ("RSI Above 50", format_payload_bool(get_payload_value(payload, "rsi_above_50"))),
        ("Above EMA21", format_payload_bool(get_payload_value(payload, "above_ema21"))),
        ("RSI Cross MA", format_payload_bool(get_payload_value(payload, "rsi_cross_ma"))),
        ("RSI Cross 50", format_payload_bool(get_payload_value(payload, "rsi_cross_50"))),
        ("Price Cross EMA", format_payload_bool(get_payload_value(payload, "price_cross_ema"))),
    )

    if any(value not in (None, "") for _, value in optional_fields):
        lines.append("")
        lines.extend(
            f"{label}: {value}"
            for label, value in optional_fields
            if value not in (None, "")
        )

    if ticker:
        price_number = safe_float(price, None)
        position = get_position_details(ticker, price_number, load_portfolio())
        if position["owned"]:
            position_text = (
                f"Held: {position['shares']} shares, "
                f"avg {position['avg_cost']}, P/L {position['pl_pct']}"
            )
        else:
            position_text = "Not currently held / watchlist only."
        lines.extend(["", f"Position: {position_text}"])

    send_telegram_message("\n".join(lines))

    return {"ok": True, "received": payload}

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
                <td colspan="8">Data unavailable</td>
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
            <td>{result['structure']}</td>
            <td>{result['structure_score']:+d}</td>
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
                max-width: 1100px;
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
                    <th>HH/HL</th>
                    <th>Structure</th>
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

@app.get("/monitors", response_class=HTMLResponse)
def monitors_page():
    monitors = load_monitors()
    portfolio = load_portfolio()
    earnings_cache = load_earnings_cache()
    monitored_tickers = sorted(
        ticker.upper()
        for ticker, status in monitors.items()
        if status == "ON"
    )
    rows_data = []

    for ticker in monitored_tickers:
        rows_data.append(get_bottom_signal(ticker))

    rows_data.sort(key=lambda row: (-row["score"], row["ticker"]))
    rows = ""

    for row in rows_data:
        score = row["score"]
        color = "#dcfce7" if score >= 8 else "#fef9c3" if score >= 5 else "#fee2e2"
        price = f"{row['price']:.2f}" if row["price"] is not None else "N/A"
        rsi_15m = f"{row['rsi_15m']:.2f}" if row["rsi_15m"] is not None else "N/A"
        rsi_1h = f"{row['rsi_1h']:.2f}" if row["rsi_1h"] is not None else "N/A"
        position = get_position_details(row["ticker"], row["price"], portfolio)
        earnings = get_earnings_info(row["ticker"], earnings_cache)
        reason = row["reason"]
        if earnings["reason"]:
            reason = f"{reason}, {earnings['reason']}"
        ticker_style = "font-weight:bold;color:#0f5132;" if position["owned"] else ""
        owned_style = "outline:2px solid #86efac;" if position["owned"] else ""
        safe_ticker = html.escape(row["ticker"])

        rows += f"""
        <tr style="background:{color};{owned_style}">
            <td style="{ticker_style}">{safe_ticker}</td>
            <td>
                <form method="post" action="/monitor?ticker={urllib.parse.quote(row['ticker'])}&return_to=/monitors">
                    <button type="submit" class="monitor-button">ON</button>
                </form>
            </td>
            <td><strong>{score}/10</strong></td>
            <td class="reason">{html.escape(reason)}</td>
            <td>{price}</td>
            <td>{position['position']}</td>
            <td>{position['avg_cost']}</td>
            <td>{position['pl_pct']}</td>
            <td style="{earnings['style']}">{earnings['date_text']}</td>
            <td>{rsi_15m}</td>
            <td>{rsi_1h}</td>
            <td>{html.escape(row['price_vs_ema21'])}</td>
            <td>{html.escape(row['rsi_trend'])}</td>
        </tr>
        """

    if not rows:
        rows = """
        <tr>
            <td colspan="13">No tickers are currently being monitored.</td>
        </tr>
        """

    save_earnings_cache(earnings_cache)
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
            .reason {{ text-align: left; }}
            .monitor-button {{
                background: #2e7d32;
                color: white;
                border: 0;
                padding: 6px 12px;
                cursor: pointer;
            }}
        </style>
    </head>
    <body>
        {nav()}

        <h2>Bottom Signal</h2>
        <p>Updated: {updated}</p>
        <table>
            <tr>
                <th>Ticker</th>
                <th>Monitor</th>
                <th>Bottom Score</th>
                <th>Reason</th>
                <th>Price</th>
                <th>Position</th>
                <th>Avg Cost</th>
                <th>Unrealized P/L %</th>
                <th>Earnings Date</th>
                <th>15m RSI</th>
                <th>1H RSI</th>
                <th>Price vs EMA21</th>
                <th>RSI Trend</th>
            </tr>
            {rows}
        </table>
    </body>
    </html>
    """

@app.get("/gap-up", response_class=HTMLResponse)
def gap_up_page():
    alert_memory = load_gap_alerts()
    portfolio = load_portfolio()
    rows_data = []

    for ticker in unique_watchlist_tickers():
        row = get_gap_up_data(ticker)
        if row and row["status"] != "NO GAP":
            rows_data.append(row)
            if should_send_gap_alert(row, alert_memory):
                send_gap_alert(row)

    save_gap_alerts(alert_memory)

    status_rank = {
        "PULLBACK BUY WATCH": 0,
        "GAP HELD": 1,
        "GAP UP WATCH": 2,
        "GAP FAILED": 3,
    }
    rows_data.sort(key=lambda row: (status_rank.get(row["status"], 9), -row["gap_pct"], row["ticker"]))

    rows = ""
    for row in rows_data:
        status = row["status"]
        color = {
            "PULLBACK BUY WATCH": "#dcfce7",
            "GAP HELD": "#dbeafe",
            "GAP UP WATCH": "#fef9c3",
            "GAP FAILED": "#fee2e2",
        }.get(status, "#f5f5f5")
        price = row["current_price"]
        position = get_position_details(row["ticker"], price, portfolio)
        ticker_style = "font-weight:bold;color:#0f5132;" if position["owned"] else ""
        owned_style = "outline:2px solid #86efac;" if position["owned"] else ""
        rsi = f"{row['rsi']:.2f}" if row["rsi"] is not None else "N/A"
        vwap = f"{row['vwap']:.2f}" if row["vwap"] is not None else "N/A"
        avg_volume = format_compact_volume(row["avg_daily_volume"])
        reasons = "<br>".join(html.escape(reason) for reason in row["reasons"])

        rows += f"""
        <tr style="background:{color};{owned_style}">
            <td style="{ticker_style}">{html.escape(row['ticker'])}</td>
            <td><strong>{html.escape(status)}</strong></td>
            <td>{row['gap_pct']:+.2f}%</td>
            <td>{row['current_price']:.2f}</td>
            <td>{row['gap_price']:.2f}</td>
            <td>{row['previous_close']:.2f}</td>
            <td>{format_compact_volume(row['intraday_volume'])}</td>
            <td>{avg_volume}</td>
            <td>{vwap}</td>
            <td>{row['ema22']:.2f}</td>
            <td>{rsi}</td>
            <td>{position['position']}</td>
            <td>{position['pl_pct']}</td>
            <td class="reason">{reasons}</td>
        </tr>
        """

    if not rows:
        rows = """
        <tr>
            <td colspan="14">No watchlist tickers are gapping up more than 3% right now.</td>
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
            .reason {{ text-align: left; min-width: 220px; }}
            .note {{ color: #555; max-width: 900px; line-height: 1.4; }}
        </style>
    </head>
    <body>
        {nav()}

        <h2>Gap-Up Monitor</h2>
        <p>Updated: {updated}</p>
        <p class="note">
            Flags watchlist tickers gapping up more than {GAP_UP_THRESHOLD_PCT:.0f}% from the previous regular-session close.
            Telegram alerts are sent only for GAP UP WATCH and PULLBACK BUY WATCH. A pullback buy watch requires RSI above 50
            and price holding/reclaiming VWAP and EMA22.
        </p>

        <table>
            <tr>
                <th>Ticker</th>
                <th>Status</th>
                <th>Gap</th>
                <th>Current</th>
                <th>Premarket/Open</th>
                <th>Prev Close</th>
                <th>Volume</th>
                <th>Avg Volume</th>
                <th>VWAP</th>
                <th>EMA22</th>
                <th>RSI</th>
                <th>Position</th>
                <th>P/L %</th>
                <th>Reasons</th>
            </tr>
            {rows}
        </table>
    </body>
    </html>
    """

@app.get("/portfolio", response_class=HTMLResponse)
def portfolio_page(imported: int = 0, error: str = ""):
    portfolio = load_portfolio()
    earnings_cache = load_earnings_cache()
    rows = ""

    for ticker, position in sorted(portfolio.items()):
        shares = safe_float(position.get("shares"))
        average_price = safe_float(position.get("average_price", position.get("avg_price")))
        currency = position.get("account_currency", position.get("currency", ""))
        notes = position.get("notes", "")
        earnings = get_earnings_info(ticker, earnings_cache)

        rows += f"""
        <tr>
            <td>{html.escape(ticker)}</td>
            <td>{shares:g}</td>
            <td>{average_price:.2f}</td>
            <td>{html.escape(str(currency))}</td>
            <td style="{earnings['style']}">{earnings['date_text']}</td>
            <td>{html.escape(str(notes))}</td>
        </tr>
        """

    if not rows:
        rows = """
        <tr>
            <td colspan="6">No portfolio holdings imported yet.</td>
        </tr>
        """

    save_earnings_cache(earnings_cache)
    csv_status = "Found" if os.path.exists(PORTFOLIO_CSV_FILE) else "Missing"
    csv_rows = count_portfolio_csv_rows()
    message = ""

    if imported:
        message = f"<p><strong>Imported {imported} holding(s) from {PORTFOLIO_CSV_FILE}.</strong></p>"
    elif error:
        message = f"<p><strong>{html.escape(error)}</strong></p>"
    elif csv_rows == 0:
        message = f"<p><strong>{PORTFOLIO_CSV_FILE} has no holdings yet. Add rows, then import again.</strong></p>"

    return f"""
    <html>
    <head>
        <style>
            body {{ font-family: Arial; margin: 30px; }}
            table {{ border-collapse: collapse; width: 100%; margin-top: 16px; }}
            th, td {{ border: 1px solid #ccc; padding: 10px; text-align: center; }}
            th {{ background: #eee; }}
            a {{ font-size: 18px; margin-right: 10px; }}
            button {{
                background: #1565c0;
                color: white;
                border: 0;
                padding: 8px 14px;
                cursor: pointer;
            }}
            .hint {{ color: #555; }}
        </style>
    </head>
    <body>
        {nav()}

        <h2>Portfolio</h2>
        <p class="hint">CSV file: {PORTFOLIO_CSV_FILE} ({csv_status}, {csv_rows} holding row(s))</p>
        <p class="hint">Expected columns: ticker, shares, average_price, account_currency, notes</p>
        {message}
        <form method="post" action="/portfolio/import-csv">
            <button type="submit">Import CSV into portfolio.json</button>
        </form>

        <table>
            <tr>
                <th>Ticker</th>
                <th>Shares</th>
                <th>Avg Cost</th>
                <th>Currency</th>
                <th>Earnings Date</th>
                <th>Notes</th>
            </tr>
            {rows}
        </table>
    </body>
    </html>
    """

@app.post("/portfolio/import-csv")
def import_portfolio_csv():
    portfolio = load_portfolio_csv()

    if not portfolio:
        error = urllib.parse.quote(f"No holdings found in {PORTFOLIO_CSV_FILE}")
        return RedirectResponse(f"/portfolio?error={error}", status_code=303)

    save_portfolio(portfolio)
    return RedirectResponse(f"/portfolio?imported={len(portfolio)}", status_code=303)

@app.post("/monitor")
def toggle_monitor(ticker: str, return_to: str = "/dashboard", timeframe: str = "15m"):
    monitors = load_monitors()
    ticker = ticker.upper()
    monitors[ticker] = "OFF" if monitors.get(ticker) == "ON" else "ON"
    save_monitors(monitors)

    if monitors[ticker] == "ON":
        log_monitor_on(ticker)

    if return_to not in ("/dashboard", "/dashboard2", "/dashboard3", "/monitors"):
        return_to = "/dashboard"

    if timeframe not in RSI_TIMEFRAMES:
        timeframe = "15m"

    if return_to == "/monitors":
        return RedirectResponse(return_to, status_code=303)

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
    portfolio = load_portfolio()
    updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    rows = ""

    for d in data:
        position = get_position_details(d["ticker"], d["price"], portfolio)
        ticker_style = "font-weight:bold;color:#0f5132;" if position["owned"] else ""
        owned_style = "outline:2px solid #86efac;" if position["owned"] else ""
        rows += f"""
        <tr style="{owned_style}">
            <td style="{ticker_style}">{d['name']}</td>
            <td>{d['price']:.2f}</td>
            <td>{position['position']}</td>
            <td>{position['avg_cost']}</td>
            <td>{position['pl_pct']}</td>
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
                <th>Position</th>
                <th>Avg Cost</th>
                <th>Unrealized P/L %</th>
                <th>Daily Change</th>
                <th>Signal</th>
            </tr>
            {rows}
        </table>
    </body>
    </html>
    """
