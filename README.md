# Stock Watcher

A personal trading dashboard for the **Nairobi Securities Exchange (NSE Kenya)**: watchlist, charts with indicators, portfolio P&L, dividends and risk, and AI insights (Groq by default, or Claude).

## Run

Double-click `run.bat` (first run creates `.venv` and installs dependencies), then open http://127.0.0.1:8000.

Or manually:

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m uvicorn app.main:app --port 8000
```

### Enable AI insights

Copy `.env.example` to `.env` and set `GROQ_API_KEY` (from https://console.groq.com/keys). The default model is `openai/gpt-oss-120b`, which can search the web for recent news. Everything except the AI panels works without a key.

To use Claude instead, set `AI_PROVIDER=anthropic` and `ANTHROPIC_API_KEY` in `.env`.

## Features

- **Search** every NSE-listed company by name or ticker ("Safaricom", "EQTY"); press `/` to focus.
- **Stock view**: price chart for 1M/2M/3M/6M/1Y with SMA 20/50, recent volume and RSI 14; period return, trend, 52-week range, volatility, max drawdown, and fundamentals (P/E, EPS, dividend per share, dividend yield, market cap, sector).
- **Watchlist overview**: all watchlist stocks rebased to 0% on one chart, plus 1M/2M/3M returns, RSI and trend.
- **Portfolio**: add buy lots (several per stock), value and P&L in KES, today's move, expected annual dividend income, allocation by stock/sector, volatility, concentration and correlation.
- **AI** (with web search for recent news and dividend announcements): per-stock trade insight, full portfolio review, and free-form questions about your holdings and watchlist.

## Data

- Prices and fundamentals come from [afx.kwayisi.org](https://afx.kwayisi.org/nse/), which publishes NSE **daily closing** prices (so charts are line/area, not candlesticks), volume for the last 10 sessions, and company fundamentals. It updates once per trading day.
- The NASI index level is shown in the top bar. No daily NASI history is available, so the app has no market benchmark or beta.
- Your watchlist and holdings live in `data/store.json` on this machine.
- AI settings (provider, model, reasoning effort, web search) are in `.env.example`.
