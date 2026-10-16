"""Nairobi Securities Exchange (Kenya) data, sourced from afx.kwayisi.org.

The source publishes daily closing prices (no intraday open/high/low), the last
10 sessions' volumes, fundamentals and sector for every listed company.
"""
import html
import json
import re
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

BASE = "https://afx.kwayisi.org"
HEADERS = {"User-Agent": "Mozilla/5.0 (StockWatcher)"}

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
LISTING_FILE = DATA_DIR / "nse_listing.json"

# Used only if the listing page can't be fetched.
FALLBACK = {
    "SCOM": "Safaricom Plc", "EQTY": "Equity Group Holdings", "KCB": "KCB Group", "EABL": "East African Breweries",
    "COOP": "Co-operative Bank of Kenya", "ABSA": "Absa Bank Kenya", "SCBK": "Standard Chartered Bank Kenya",
    "NCBA": "NCBA Group", "SBIC": "Stanbic Holdings", "DTK": "Diamond Trust Bank Kenya", "IMH": "I&M Holdings",
    "BAT": "British American Tobacco Kenya", "KEGN": "KenGen", "KPLC": "Kenya Power & Lighting", "KQ": "Kenya Airways",
    "JUB": "Jubilee Holdings", "BRIT": "Britam Holdings", "KNRE": "Kenya Re-Insurance", "NSE": "Nairobi Securities Exchange",
    "TOTL": "TotalEnergies Marketing Kenya", "CTUM": "Centum Investment", "NMG": "Nation Media Group",
    "SASN": "Sasini", "KUKZ": "Kakuzi", "CIC": "CIC Insurance Group", "HFCK": "HF Group", "CRWN": "Crown Paints Kenya",
}

_listing: list[dict] = []
_listing_lock = threading.Lock()
_page_cache: dict[str, tuple[float, str]] = {}
_page_lock = threading.Lock()
PAGE_TTL = 600  # source updates once per trading day


def _get(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.text


def _num(s: str | None) -> float | None:
    if not s:
        return None
    s = s.strip().replace(",", "").replace("%", "")
    mult = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}.get(s[-1:].upper(), 1)
    if mult != 1:
        s = s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return None


# ---------------------------------------------------------------- listing

def load_listing() -> list[dict]:
    with _listing_lock:
        if _listing:
            return _listing
        rows = None
        if LISTING_FILE.exists() and time.time() - LISTING_FILE.stat().st_mtime < 24 * 3600:
            rows = json.loads(LISTING_FILE.read_text(encoding="utf-8"))
        if rows is None:
            try:
                page = _get(f"{BASE}/nse/")
                table = page[page.index("<th>Ticker"):]
                table = table[:table.index("</table>")]
                rows = []
                for cls, slug, name, ticker in re.findall(
                        r'<tr( class=ss)?><td><a href=https://afx\.kwayisi\.org/nse/([a-z0-9-]+)\.html title="([^"]+)">([^<]+)</a>',
                        table):
                    rows.append({"symbol": ticker.strip().upper(), "name": html.unescape(name),
                                 "slug": slug, "suspended": bool(cls)})
                if rows:
                    LISTING_FILE.write_text(json.dumps(rows), encoding="utf-8")
                else:
                    rows = None
            except Exception:
                rows = None
        if rows is None:
            rows = [{"symbol": k, "name": v, "slug": k.lower(), "suspended": False} for k, v in FALLBACK.items()]
        _listing.extend(rows)
        return _listing


def _slug(symbol: str) -> str:
    for r in load_listing():
        if r["symbol"] == symbol:
            return r["slug"]
    return symbol.lower()


def _page(symbol: str) -> str:
    now = time.time()
    with _page_lock:
        hit = _page_cache.get(symbol)
        if hit and now - hit[0] < PAGE_TTL:
            return hit[1]
    text = _get(f"{BASE}/nse/{_slug(symbol)}.html")
    with _page_lock:
        _page_cache[symbol] = (now, text)
    return text


# ---------------------------------------------------------------- prices

def get_history(symbol: str) -> pd.DataFrame:
    """~1 year of daily closes. Open/High/Low equal Close (source has closes only)."""
    try:
        js = _get(f"{BASE}/chart/nse/{_slug(symbol)}")
    except requests.HTTPError:
        raise LookupError(f"No market data for {symbol}")
    pts = re.findall(r'd\("(\d{4}-\d{2}-\d{2})"\),([\d.]+)\]', js)
    if not pts:
        raise LookupError(f"No market data for {symbol}")
    idx = pd.to_datetime([d for d, _ in pts])
    close = pd.Series([float(c) for _, c in pts], index=idx)
    close = close[close.index >= close.index[-1] - pd.DateOffset(years=1)]
    df = pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "Volume": np.nan})

    # Volumes (and today's range) for the most recent sessions come from the quote page.
    try:
        page = _page(symbol)
        hist = page[page.index("<table data-hist"):]
        hist = hist[:hist.index("</table>")]
        for d, vol in re.findall(r"<tr><td>(\d{4}-\d{2}-\d{2})<td>([\d,]*)", hist):
            ts = pd.Timestamp(d)
            if ts in df.index and vol:
                df.loc[ts, "Volume"] = float(vol.replace(",", ""))
        lo = re.search(r"Day.s Low Price<td>([\d.,]+)", page)
        hi = re.search(r"Day.s High Price<td>([\d.,]+)", page)
        last = df.index[-1]
        if lo and hi:
            df.loc[last, "Low"] = min(_num(lo.group(1)), df.loc[last, "Close"])
            df.loc[last, "High"] = max(_num(hi.group(1)), df.loc[last, "Close"])
    except Exception:
        pass
    return df


def get_meta(symbol: str) -> dict:
    try:
        page = _page(symbol)
    except Exception:
        return {"sector": None, "industry": None}

    def field(label):
        m = re.search(rf"<dt>{label}<dd>([^<]+)", page)
        return html.unescape(m.group(1).strip()) if m else None

    def val(label):
        m = re.search(rf"<td>{label}<td[^>]*>([^<]*)", page)
        return _num(m.group(1)) if m else None

    summary = None
    m = re.search(r"<p>([^<]*?(?:was incorporated|is a|engages)[^<]*(?:<em>[^<]*</em>[^<]*)*)", page)
    if m:
        summary = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()[:600]
    return {
        "sector": field("Sector"),
        "industry": field("Industry"),
        "market_cap": val("Market Capitalization"),
        "pe": val("Price/Earning Ratio"),
        "eps": val("Earnings Per Share"),
        "dps": val("Dividend Per Share"),
        "dividend_yield": val("Dividend Yield"),
        "shares_outstanding": val("Shares Outstanding"),
        "summary": summary,
    }


def nasi() -> dict | None:
    """Current NSE All Share Index level (the source has no index history)."""
    try:
        page = _get(f"{BASE}/nse/")
        m = re.search(r"NASI Index.*?<td>([\d.,]+)\s*<span[^>]*>\(([-+][\d.,]+)\)", page, re.S)
        if not m:
            return None
        price, chg = _num(m.group(1)), _num(m.group(2))
        return {"symbol": "NASI", "name": "NASI", "price": price,
                "day_change_pct": chg / (price - chg) * 100 if price and price != chg else None}
    except Exception:
        return None
