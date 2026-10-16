"""Watchlist and holdings, persisted to data/store.json."""
import json
import threading
import uuid

from .market import DATA_DIR

STORE_FILE = DATA_DIR / "store.json"
_lock = threading.Lock()

VERSION = 2
DEFAULT = {"version": VERSION, "watchlist": ["SCOM", "EQTY", "KCB", "EABL", "COOP"], "holdings": []}


def _load() -> dict:
    if not STORE_FILE.exists():
        return json.loads(json.dumps(DEFAULT))
    data = json.loads(STORE_FILE.read_text(encoding="utf-8"))
    if data.get("version", 1) < VERSION:
        data = _migrate_v1(data)
        _save(data)
    return data


def _migrate_v1(data: dict) -> dict:
    """v1 tracked NSE India and NSE Kenya ("KE:SCOM"). v2 is Kenya-only with plain tickers:
    keep the Kenyan entries, drop the Indian ones."""
    def ke(sym):
        return sym[3:] if sym.startswith("KE:") else None

    watch = [ke(s) for s in data.get("watchlist", []) if ke(s)]
    lots = [{**l, "symbol": ke(l["symbol"])} for l in data.get("holdings", []) if ke(l["symbol"])]
    return {"version": VERSION, "watchlist": watch, "holdings": lots}


def _save(data: dict) -> None:
    tmp = STORE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(STORE_FILE)


def get_watchlist() -> list[str]:
    with _lock:
        return _load()["watchlist"]


def add_watch(symbol: str) -> list[str]:
    with _lock:
        data = _load()
        if symbol not in data["watchlist"]:
            data["watchlist"].append(symbol)
            _save(data)
        return data["watchlist"]


def remove_watch(symbol: str) -> list[str]:
    with _lock:
        data = _load()
        data["watchlist"] = [s for s in data["watchlist"] if s != symbol]
        _save(data)
        return data["watchlist"]


def get_lots() -> list[dict]:
    with _lock:
        return _load()["holdings"]


def add_lot(symbol: str, qty: float, price: float, date: str | None, note: str | None) -> dict:
    lot = {"id": uuid.uuid4().hex[:10], "symbol": symbol, "qty": qty, "price": price, "date": date, "note": note}
    with _lock:
        data = _load()
        data["holdings"].append(lot)
        _save(data)
    return lot


def update_lot(lot_id: str, **fields) -> dict | None:
    with _lock:
        data = _load()
        for lot in data["holdings"]:
            if lot["id"] == lot_id:
                lot.update({k: v for k, v in fields.items() if v is not None})
                _save(data)
                return lot
    return None


def remove_lot(lot_id: str) -> bool:
    with _lock:
        data = _load()
        before = len(data["holdings"])
        data["holdings"] = [l for l in data["holdings"] if l["id"] != lot_id]
        _save(data)
        return len(data["holdings"]) < before
