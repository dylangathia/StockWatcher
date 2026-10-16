"""AI insights streamed to the browser as plain text (Markdown).

Provider is chosen with AI_PROVIDER: "groq" (default, via the Groq API) or "anthropic" (Claude).
"""
import json
import os
import re
from datetime import date

PROVIDER = os.environ.get("AI_PROVIDER", "groq").strip().lower()
WEB_SEARCH = os.environ.get("AI_WEB_SEARCH", "1") != "0"

GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_REASONING = os.environ.get("GROQ_REASONING_EFFORT", "medium")  # low | medium | high

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
CLAUDE_EFFORT = os.environ.get("CLAUDE_EFFORT", "medium")

SYSTEM = """You are the analyst inside Stock Watcher, a tool a retail trader uses to track stocks listed on the Nairobi Securities Exchange (NSE Kenya) and their own portfolio. All prices are in Kenyan shillings (KES); the broad market index is NASI.

The data is daily closing prices only (no intraday open/high/low), with volume for roughly the last 10 sessions. Many NSE counters trade thinly, so weigh liquidity when you interpret moves, and treat dividends as a large part of total return on this market. Retail traders here generally can't short sell, so frame ideas as buy, add, hold, trim or sell, never as short positions.

You get structured market data computed by the app (prices, returns, RSI, moving averages, volatility, drawdowns, fundamentals such as P/E and dividend yield, portfolio weights). Treat those numbers as ground truth and reason from them; don't invent figures. When web search is available, use it to check recent news, results, dividend announcements and book-closure dates, or sector events that explain the price action, and say where facts came from.

How to write:
- Lead with the conclusion in one or two sentences, then the evidence.
- Be concrete: cite levels (support/resistance from the period high/low and moving averages), percentages, and dates.
- Separate what the data shows from your interpretation, and give the bull case and the bear case when relevant.
- Name the specific risks (concentration, momentum fading, overbought/oversold, earnings dates, sector exposure).
- Use short Markdown sections with headings and bullets. Keep it tight: a trader reads this between trades.
- End with one line noting this is analysis, not personalised investment advice. Don't add any other disclaimers."""

NO_KEY = ("**AI insights need an API key.** Copy `.env.example` to `.env`, set `{var}`, and restart the app. "
          "Charts, watchlist and portfolio work without it.")


def stream_insight(prompt: str):
    """Yield Markdown text chunks. Errors are reported inline so the UI can show them."""
    user = f"Today is {date.today().isoformat()}.\n\n{prompt}"
    if PROVIDER == "anthropic":
        yield from _stream_claude(user)
    else:
        yield from _stream_groq(user)


# ---------------------------------------------------------------- Groq

# Browser search answers carry inline citation markers like 【2†L6-L10】; they don't render as links.
_CITATION = re.compile(r"【[^】]*】")


def _strip_citations(chunks):
    """Remove citation markers from a text stream, even when one is split across chunks."""
    buf = ""
    for text in chunks:
        buf += text
        cut = buf.rfind("【")
        if cut != -1 and "】" not in buf[cut:]:
            ready, buf = buf[:cut], buf[cut:]  # hold back an unfinished marker
        else:
            ready, buf = buf, ""
        ready = _CITATION.sub("", ready)
        if ready:
            yield ready
    if buf:
        yield _CITATION.sub("", buf)


def _stream_groq(user: str):
    import groq

    try:
        client = groq.Groq()
    except groq.GroqError:
        yield NO_KEY.format(var="GROQ_API_KEY")
        return

    search = WEB_SEARCH
    while True:
        try:
            stream = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                max_completion_tokens=8192,
                reasoning_effort=GROQ_REASONING,
                stream=True,
                **({"tools": [{"type": "browser_search"}], "tool_choice": "auto"} if search else {}),
            )

            def texts():
                finish = None
                for chunk in stream:
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    if choice.delta.content:
                        yield choice.delta.content
                    finish = choice.finish_reason or finish
                if finish == "length":
                    yield "\n\n*(Response truncated.)*"

            yield from _strip_citations(texts())
            return
        except groq.AuthenticationError:
            yield "**AI unavailable:** the Groq API key is invalid. Update `GROQ_API_KEY` in `.env` and restart."
            return
        except groq.RateLimitError:
            yield "\n\n**Rate limited** by Groq. Try again in a minute."
            return
        except groq.BadRequestError as e:
            if search:
                search = False  # model or account doesn't accept browser search here: answer from the data alone
                continue
            yield f"\n\n**AI request failed:** {e.message}"
            return
        except groq.APIStatusError as e:
            yield f"\n\n**AI service error ({e.status_code}).** Try again shortly."
            return
        except groq.APIConnectionError:
            yield "\n\n**Couldn't reach the Groq API.** Check your internet connection."
            return
        except groq.APIError as e:  # e.g. an error event mid-stream
            yield f"\n\n**AI request failed:** {e.message}"
            return


# ---------------------------------------------------------------- Anthropic (Claude)

def _stream_claude(user: str):
    import anthropic

    messages = [{"role": "user", "content": user}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 4}] if WEB_SEARCH else []
    client = anthropic.Anthropic()

    for _ in range(4):  # extra rounds only for pause_turn continuations
        try:
            with client.beta.messages.stream(
                model=CLAUDE_MODEL,
                max_tokens=16000,
                system=SYSTEM,
                messages=messages,
                tools=tools,
                output_config={"effort": CLAUDE_EFFORT},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                for event in stream:
                    if event.type == "content_block_start" and event.content_block.type == "server_tool_use":
                        yield "\n\n*Searching recent news…*\n\n"
                    elif event.type == "content_block_delta" and event.delta.type == "text_delta":
                        yield event.delta.text
                final = stream.get_final_message()
        except anthropic.AuthenticationError:
            yield "**AI unavailable:** the Anthropic API key is invalid. Update `ANTHROPIC_API_KEY` in `.env` and restart."
            return
        except TypeError as e:
            if "authentication" not in str(e):
                raise
            yield NO_KEY.format(var="ANTHROPIC_API_KEY")
            return
        except anthropic.RateLimitError:
            yield "\n\n**Rate limited** by the Anthropic API. Try again in a minute."
            return
        except anthropic.BadRequestError as e:
            if tools and "web_search" in str(e):
                tools = []  # web search not enabled for this org: retry without it
                continue
            yield f"\n\n**AI request failed:** {e.message}"
            return
        except anthropic.APIStatusError as e:
            yield f"\n\n**AI service error ({e.status_code}).** Try again shortly."
            return
        except anthropic.APIConnectionError:
            yield "\n\n**Couldn't reach the Anthropic API.** Check your internet connection."
            return

        if final.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": final.content})
            continue
        if final.stop_reason == "refusal":
            yield "\n\n*The model declined to complete this analysis.*"
        elif final.stop_reason == "max_tokens":
            yield "\n\n*(Response truncated.)*"
        return


def stock_prompt(analysis: dict, meta: dict, holding: dict | None) -> str:
    closes = [{"d": p["time"], "c": p["close"], "v": p["volume"]} for p in analysis["points"][-45:]]
    meta = {k: v for k, v in meta.items() if not k.startswith("_")}
    parts = [
        f"Give me a trading insight on {analysis['name']} (NSE Kenya: {analysis['symbol']}).",
        "Cover: what the last 1–2 months of price action say (trend, momentum, liquidity), key levels to watch, "
        "any news, results or dividend events behind the move, how the valuation and dividend yield look, and "
        "what would change the picture. Finish with a short verdict on the setup (e.g. strong momentum / basing / "
        "breaking down) for a swing trader.",
        "Computed stats:\n" + json.dumps(analysis["stats"]),
        "Fundamentals (may be partial):\n" + json.dumps(meta),
        "Recent daily closes and volumes (volume only for the last ~10 sessions):\n" + json.dumps(closes),
    ]
    if holding:
        parts.append("I hold this stock: " + json.dumps({k: holding[k] for k in
                     ("qty", "avg_price", "invested", "value", "pnl", "pnl_pct", "weight_pct", "annual_dividend")
                     if k in holding}) + ". Comment on my position specifically.")
    return "\n\n".join(parts)


def portfolio_prompt(pf: dict) -> str:
    holdings = [{k: h.get(k) for k in ("symbol", "name", "sector", "qty", "avg_price", "price", "value", "pnl_pct",
                                         "weight_pct", "return_1m_pct", "return_2m_pct", "rsi14", "day_change_pct",
                                         "dividend_yield", "annual_dividend")}
                for h in pf["holdings"]]
    return "\n\n".join([
        "Review my NSE Kenya portfolio.",
        "Cover: overall health and 1–2 month performance, winners and laggards and why, dividend income, "
        "concentration, sector and liquidity risk, any holdings flashing technical warnings, and "
        "3–5 specific, prioritised actions to consider (trim, add, rebalance, set stop, watch an event). "
        "Check recent news for my largest positions.",
        "Totals:\n" + json.dumps(pf["totals"]),
        "Risk metrics (3-month daily data):\n" + json.dumps(pf["risk"]),
        "Sector allocation:\n" + json.dumps(pf["sectors"]),
        "Holdings:\n" + json.dumps(holdings),
    ])


def question_prompt(question: str, pf: dict, watch: list[dict]) -> str:
    holdings = [{k: h.get(k) for k in ("symbol", "sector", "qty", "avg_price", "price", "pnl_pct", "weight_pct",
                                         "return_1m_pct", "return_2m_pct", "rsi14", "dividend_yield")}
                for h in pf["holdings"]]
    watch = [{k: w.get(k) for k in ("symbol", "name", "price", "return_1m_pct", "return_2m_pct", "rsi14", "trend")}
             for w in watch]
    return "\n\n".join([
        "Answer my question using my portfolio and watchlist data below as context. Be direct.",
        f"Question: {question}",
        "Portfolio totals:\n" + json.dumps(pf.get("totals")),
        "Holdings:\n" + json.dumps(holdings),
        "Watchlist:\n" + json.dumps(watch),
    ])
