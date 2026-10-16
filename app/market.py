"""Market data for the Nairobi Securities Exchange: symbol search, price history,
technical analytics and portfolio math. Raw data comes from kenya.py."""
import json
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from . import kenya

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)
META_FILE = DATA_DIR / "meta_cache.json"

PERIOD_MONTHS = {"1M": 1, "2M": 2, "3M": 3, "6M": 6, "1Y": 12}
SYMBOL_RE = re.compile(r"^[A-Z0-9&\-]{1,20}$")

_hist_cache: dict[str, tuple[float, pd.DataFrame]] = {}
_hist_lock = threading.Lock()
_meta_lock = threading.Lock()
HIST_TTL = 600  # the source updates once per trading day
META_TTL = 24 * 3600


def clean(obj):
    """Make a structure JSON-safe (NaN/inf -> None, numpy -> python)."""
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return None if math.isnan(f) or math.isinf(f) else round(f, 4)
    if isinstance(obj, np.integer):
        return int(obj)
    return obj


def normalize_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if s.startswith("KE:"):  # older saved data used a market prefix
        s = s[3:]
    if s.endswith(".NR"):
        s = s[:-3]
    if not SYMBOL_RE.match(s):
        raise ValueError(f"Invalid symbol: {symbol!r}")
    return s


# ---------------------------------------------------------------- symbols

def symbol_name(symbol: str) -> str:
    for row in kenya.load_listing():
        if row["symbol"] == symbol:
            return row["name"]
    return symbol


def search_symbols(q: str, limit: int = 12) -> list[dict]:
    q = q.strip().upper()
    if not q:
        return []
    exact, prefix, name_hits = [], [], []
    for row in kenya.load_listing():
        sym, name = row["symbol"], row["name"].upper()
        if sym == q:
            exact.append(row)
        elif sym.startswith(q):
            prefix.append(row)
        elif q in name:
            name_hits.append(row)
    prefix.sort(key=lambda r: len(r["symbol"]))
    return [{k: r[k] for k in ("symbol", "name", "suspended")} for r in (exact + prefix + name_hits)[:limit]]


# ---------------------------------------------------------------- history

def get_history(symbol: str) -> pd.DataFrame:
    """About one year of daily closes, cached briefly. Raises LookupError if no data."""
    now = time.time()
    with _hist_lock:
        hit = _hist_cache.get(symbol)
        if hit and now - hit[0] < HIST_TTL:
            return hit[1]
    df = kenya.get_history(symbol)
    with _hist_lock:
        _hist_cache[symbol] = (now, df)
    return df


def get_histories(symbols: list[str]) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}

    def fetch(s):
        try:
            return s, get_history(s)
        except Exception:
            return s, None

    with ThreadPoolExecutor(max_workers=8) as pool:
        for s, df in pool.map(fetch, symbols):
            if df is not None:
                out[s] = df
    return out


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def window(df: pd.DataFrame, months: int) -> pd.DataFrame:
    start = df.index[-1] - pd.DateOffset(months=months)
    return df[df.index >= start]


def period_return(df: pd.DataFrame, months: int) -> float | None:
    """% change from the last close before the window to the latest close."""
    start = df.index[-1] - pd.DateOffset(months=months)
    before = df[df.index < start]
    base = before["Close"].iloc[-1] if len(before) else df["Close"].iloc[0]
    return (df["Close"].iloc[-1] / base - 1) * 100


def trend_label(price, sma20, sma50, rsi_v) -> str:
    if any(v is None or math.isnan(v) for v in (sma20, sma50)):
        return "Insufficient data"
    if price > sma20 > sma50:
        label = "Uptrend"
    elif price < sma20 < sma50:
        label = "Downtrend"
    else:
        label = "Sideways"
    if rsi_v is not None and not math.isnan(rsi_v):
        if rsi_v >= 70:
            label += " · overbought"
        elif rsi_v <= 30:
            label += " · oversold"
    return label


def analyze(symbol: str, period: str = "2M") -> dict:
    months = PERIOD_MONTHS.get(period, 2)
    df = get_history(symbol).copy()
    close = df["Close"]
    df["SMA20"] = close.rolling(20).mean()
    df["SMA50"] = close.rolling(50).mean()
    df["RSI"] = rsi(close)
    win = window(df, months)

    last = df.iloc[-1]
    prev_close = df["Close"].iloc[-2] if len(df) > 1 else last["Close"]
    rets = win["Close"].pct_change().dropna()
    running_max = win["Close"].cummax()
    drawdown = (win["Close"] / running_max - 1).min() * 100
    avg_vol = df["Volume"].mean()  # only the last ~10 sessions carry volume
    vol5 = df["Volume"].dropna().tail(5).mean()

    stats = {
        "price": last["Close"],
        "prev_close": prev_close,
        "day_change": last["Close"] - prev_close,
        "day_change_pct": (last["Close"] / prev_close - 1) * 100,
        "as_of": df.index[-1].strftime("%Y-%m-%d"),
        "period": period,
        "period_return_pct": period_return(df, months),
        "return_1m_pct": period_return(df, 1),
        "return_2m_pct": period_return(df, 2),
        "period_high": win["Close"].max(),
        "period_low": win["Close"].min(),
        "high_52w": df["Close"].max(),
        "low_52w": df["Close"].min(),
        "pct_from_52w_high": (last["Close"] / df["Close"].max() - 1) * 100,
        "volatility_ann_pct": rets.std() * math.sqrt(252) * 100 if len(rets) > 2 else None,
        "max_drawdown_pct": drawdown,
        "avg_volume": avg_vol,
        "volume_ratio_5d": vol5 / avg_vol if avg_vol else None,
        "rsi14": last["RSI"],
        "sma20": last["SMA20"],
        "sma50": last["SMA50"],
        "up_days": int((rets > 0).sum()),
        "down_days": int((rets < 0).sum()),
        "best_day_pct": rets.max() * 100 if len(rets) else None,
        "worst_day_pct": rets.min() * 100 if len(rets) else None,
        "day_low": last["Low"],
        "day_high": last["High"],
    }
    stats["trend"] = trend_label(stats["price"], stats["sma20"], stats["sma50"], stats["rsi14"])

    points = [
        {"time": ts.strftime("%Y-%m-%d"), "close": r.Close, "volume": r.Volume,
         "sma20": r.SMA20, "sma50": r.SMA50, "rsi": r.RSI}
        for ts, r in win.iterrows()
    ]
    return clean({"symbol": symbol, "name": symbol_name(symbol), "stats": stats, "points": points})


def quick_quote(symbol: str, df: pd.DataFrame) -> dict:
    close = df["Close"]
    prev = close.iloc[-2] if len(close) > 1 else close.iloc[-1]
    r = rsi(close).iloc[-1]
    sma20 = close.rolling(20).mean().iloc[-1]
    sma50 = close.rolling(50).mean().iloc[-1]
    return clean({
        "symbol": symbol,
        "name": symbol_name(symbol),
        "price": close.iloc[-1],
        "day_change_pct": (close.iloc[-1] / prev - 1) * 100,
        "return_1m_pct": period_return(df, 1),
        "return_2m_pct": period_return(df, 2),
        "return_3m_pct": period_return(df, 3),
        "rsi14": r,
        "trend": trend_label(close.iloc[-1], sma20, sma50, r),
        "spark": window(df, 2)["Close"].tolist(),
    })


def quotes(symbols: list[str]) -> list[dict]:
    hist = get_histories(symbols)
    return [quick_quote(s, hist[s]) if s in hist else {"symbol": s, "name": symbol_name(s), "error": "No data"}
            for s in symbols]


def compare_series(symbols: list[str], period: str) -> dict:
    """Each symbol's closes rebased to 0% at the start of the period."""
    months = PERIOD_MONTHS.get(period, 2)
    out = {}
    for s, df in get_histories(symbols).items():
        win = window(df, months)
        base = win["Close"].iloc[0]
        out[s] = [{"time": ts.strftime("%Y-%m-%d"), "value": (c / base - 1) * 100} for ts, c in win["Close"].items()]
    return clean(out)


# ---------------------------------------------------------------- fundamentals (cached on disk)

def _read_meta() -> dict:
    if META_FILE.exists():
        try:
            return json.loads(META_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def get_meta(symbol: str) -> dict:
    with _meta_lock:
        hit = _read_meta().get(symbol)
        if hit and time.time() - hit.get("_ts", 0) < META_TTL:
            return hit
    meta = clean(kenya.get_meta(symbol))
    meta["_ts"] = time.time()
    with _meta_lock:
        cache = _read_meta()
        cache[symbol] = meta
        META_FILE.write_text(json.dumps(cache), encoding="utf-8")
    return meta


def get_metas(symbols: list[str]) -> dict[str, dict]:
    with ThreadPoolExecutor(max_workers=6) as pool:
        return dict(zip(symbols, pool.map(get_meta, symbols)))


# ---------------------------------------------------------------- portfolio

def aggregate_lots(lots: list[dict]) -> dict[str, dict]:
    pos: dict[str, dict] = {}
    for lot in lots:
        p = pos.setdefault(lot["symbol"], {"qty": 0.0, "invested": 0.0, "lots": []})
        p["qty"] += lot["qty"]
        p["invested"] += lot["qty"] * lot["price"]
        p["lots"].append(lot)
    return pos


def portfolio(lots: list[dict]) -> dict:
    positions = aggregate_lots(lots)
    symbols = list(positions)
    if not symbols:
        return {"holdings": [], "totals": None, "risk": None, "sectors": []}
    hist = get_histories(symbols)
    metas = get_metas(symbols)

    holdings = []
    for s, p in positions.items():
        df = hist.get(s)
        price = prev = None
        if df is not None:
            price = df["Close"].iloc[-1]
            prev = df["Close"].iloc[-2] if len(df) > 1 else price
        value = price * p["qty"] if price is not None else None
        meta = metas.get(s, {})
        holdings.append({
            "symbol": s,
            "name": symbol_name(s),
            "sector": meta.get("sector") or "Other",
            "qty": p["qty"],
            "avg_price": p["invested"] / p["qty"] if p["qty"] else None,
            "invested": p["invested"],
            "price": price,
            "value": value,
            "pnl": value - p["invested"] if value is not None else None,
            "pnl_pct": (value / p["invested"] - 1) * 100 if value is not None and p["invested"] else None,
            "day_pnl": (price - prev) * p["qty"] if price is not None else None,
            "day_change_pct": (price / prev - 1) * 100 if price is not None else None,
            "return_1m_pct": period_return(df, 1) if df is not None else None,
            "return_2m_pct": period_return(df, 2) if df is not None else None,
            "rsi14": rsi(df["Close"]).iloc[-1] if df is not None else None,
            "dividend_yield": meta.get("dividend_yield"),
            # Expected annual dividends at the current dividend per share
            "annual_dividend": meta["dps"] * p["qty"] if meta.get("dps") else None,
            "lots": p["lots"],
        })

    priced = [h for h in holdings if h["value"] is not None]
    total_value = sum(h["value"] for h in priced)
    for h in holdings:
        h["weight_pct"] = h["value"] / total_value * 100 if h["value"] is not None and total_value else None
    holdings.sort(key=lambda h: h["value"] or 0, reverse=True)

    sectors: dict[str, float] = {}
    for h in priced:
        sectors[h["sector"]] = sectors.get(h["sector"], 0) + h["value"]
    sector_list = sorted(({"sector": k, "value": v, "weight_pct": v / total_value * 100}
                          for k, v in sectors.items()), key=lambda x: -x["value"]) if total_value else []

    invested_priced = sum(h["invested"] for h in priced)
    day_pnl = sum(h["day_pnl"] for h in priced)
    annual_div = sum(h["annual_dividend"] or 0 for h in holdings)
    totals = {
        "invested": sum(h["invested"] for h in holdings),
        "value": total_value,
        "pnl": total_value - invested_priced,
        "pnl_pct": (total_value / invested_priced - 1) * 100 if invested_priced else None,
        "day_pnl": day_pnl,
        "day_pnl_pct": day_pnl / (total_value - day_pnl) * 100 if total_value - day_pnl else None,
        "positions": len(holdings),
        "annual_dividend": annual_div,
        "dividend_yield_pct": annual_div / total_value * 100 if total_value else None,
    }
    return clean({"holdings": holdings, "totals": totals, "risk": _portfolio_risk(priced, hist, total_value),
                  "sectors": sector_list})


def _portfolio_risk(priced: list[dict], hist: dict, total_value: float) -> dict | None:
    if not priced or not total_value:
        return None
    weights = {h["symbol"]: h["value"] / total_value for h in priced}
    rets = pd.DataFrame({s: window(hist[s], 3)["Close"].pct_change() for s in weights if s in hist})
    rets = rets.dropna(how="all").fillna(0)
    port = sum(rets[s] * w for s, w in weights.items() if s in rets)
    risk = {
        "volatility_ann_pct": port.std() * math.sqrt(252) * 100 if len(port) > 5 else None,
        "top_holding_pct": max(weights.values()) * 100,
        # Effective number of positions (inverse Herfindahl index)
        "effective_positions": 1 / sum(w * w for w in weights.values()),
        # Period return using today's weights (approximation; ignores trades in between)
        "portfolio_return_1m_pct": sum(weights[s] * period_return(hist[s], 1) for s in weights if s in hist),
        "portfolio_return_2m_pct": sum(weights[s] * period_return(hist[s], 2) for s in weights if s in hist),
    }
    if rets.shape[1] > 1:
        corr = rets.corr()
        pairs = [(a, b, corr.loc[a, b]) for i, a in enumerate(corr.columns) for b in corr.columns[i + 1:]]
        pairs.sort(key=lambda x: -x[2] if not math.isnan(x[2]) else 0)
        risk["most_correlated"] = [{"a": a, "b": b, "corr": c} for a, b, c in pairs[:3]]
    return risk
