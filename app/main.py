"""Stock Watcher: FastAPI app serving the NSE Kenya dashboard and its JSON API."""
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import FastAPI, HTTPException, Query  # noqa: E402
from fastapi.concurrency import run_in_threadpool  # noqa: E402
from fastapi.responses import FileResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from . import ai, kenya, market, store  # noqa: E402

STATIC = Path(__file__).resolve().parent.parent / "static"
app = FastAPI(title="Stock Watcher")


def sym(symbol: str) -> str:
    try:
        return market.normalize_symbol(symbol)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.on_event("startup")
async def warm_symbols():
    await run_in_threadpool(kenya.load_listing)


# ---------------------------------------------------------------- market data

@app.get("/api/search")
async def search(q: str = Query("", max_length=40)):
    return await run_in_threadpool(market.search_symbols, q)


@app.get("/api/stock")
async def stock(symbol: str, period: str = "2M"):
    s = sym(symbol)
    try:
        return await run_in_threadpool(market.analyze, s, period)
    except LookupError as e:
        raise HTTPException(404, str(e))


@app.get("/api/meta")
async def meta(symbol: str):
    m = await run_in_threadpool(market.get_meta, sym(symbol))
    return {k: v for k, v in m.items() if not k.startswith("_")}


@app.get("/api/indices")
async def indices():
    nasi = await run_in_threadpool(kenya.nasi)
    return [market.clean(nasi)] if nasi else []


@app.get("/api/compare")
async def compare(symbols: str, period: str = "2M"):
    syms = [sym(s) for s in symbols.split(",") if s.strip()][:12]
    return await run_in_threadpool(market.compare_series, syms, period)


# ---------------------------------------------------------------- watchlist

@app.get("/api/watchlist")
async def watchlist():
    return await run_in_threadpool(market.quotes, store.get_watchlist())


@app.post("/api/watchlist/{symbol}")
async def watch_add(symbol: str):
    return store.add_watch(sym(symbol))


@app.delete("/api/watchlist/{symbol}")
async def watch_remove(symbol: str):
    return store.remove_watch(sym(symbol))


# ---------------------------------------------------------------- portfolio

class LotIn(BaseModel):
    symbol: str
    qty: float = Field(gt=0)
    price: float = Field(gt=0)
    date: str | None = None
    note: str | None = Field(None, max_length=200)


class LotPatch(BaseModel):
    qty: float | None = Field(None, gt=0)
    price: float | None = Field(None, gt=0)
    date: str | None = None
    note: str | None = Field(None, max_length=200)


@app.get("/api/portfolio")
async def get_portfolio():
    return await run_in_threadpool(market.portfolio, store.get_lots())


@app.post("/api/holdings")
async def add_holding(lot: LotIn):
    return store.add_lot(sym(lot.symbol), lot.qty, lot.price, lot.date, lot.note)


@app.patch("/api/holdings/{lot_id}")
async def edit_holding(lot_id: str, patch: LotPatch):
    lot = store.update_lot(lot_id, **patch.model_dump())
    if not lot:
        raise HTTPException(404, "Holding not found")
    return lot


@app.delete("/api/holdings/{lot_id}")
async def delete_holding(lot_id: str):
    if not store.remove_lot(lot_id):
        raise HTTPException(404, "Holding not found")
    return {"ok": True}


# ---------------------------------------------------------------- AI

def _stream(prompt: str) -> StreamingResponse:
    return StreamingResponse(ai.stream_insight(prompt), media_type="text/plain; charset=utf-8",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/ai/stock")
async def ai_stock(symbol: str, period: str = "2M"):
    s = sym(symbol)
    try:
        analysis = await run_in_threadpool(market.analyze, s, period)
    except LookupError as e:
        raise HTTPException(404, str(e))
    meta_ = await run_in_threadpool(market.get_meta, s)
    holding = None
    if any(l["symbol"] == s for l in store.get_lots()):
        pf = await run_in_threadpool(market.portfolio, store.get_lots())
        holding = next((h for h in pf["holdings"] if h["symbol"] == s), None)
    return _stream(ai.stock_prompt(analysis, meta_, holding))


@app.post("/api/ai/portfolio")
async def ai_portfolio():
    pf = await run_in_threadpool(market.portfolio, store.get_lots())
    if not pf["holdings"]:
        raise HTTPException(400, "Add some holdings first")
    return _stream(ai.portfolio_prompt(pf))


class Question(BaseModel):
    question: str = Field(min_length=2, max_length=1000)


@app.post("/api/ai/ask")
async def ai_ask(q: Question):
    pf = await run_in_threadpool(market.portfolio, store.get_lots())
    watch = await run_in_threadpool(market.quotes, store.get_watchlist())
    return _stream(ai.question_prompt(q.question, pf, watch))


# ---------------------------------------------------------------- frontend

app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")
