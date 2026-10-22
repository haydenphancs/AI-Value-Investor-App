"""
FMP Tools for Gemini Function Calling — enables the AI agent to autonomously
request additional financial data during the research phase.

The agent receives base data (profile, quote, income, balance, etc.) upfront.
These tools let it request ADDITIONAL data based on what it discovers — e.g.,
quarterly financials to spot trends, dividend history for yield analysis,
sector data for competitive context.

Uses google.genai types for Gemini-compatible function declarations.
"""

import logging
import math
from typing import Dict, Any, Callable, Awaitable, Optional

import httpx
from google.genai import types

from app.config import settings
from app.integrations.fmp import FMPClient
from app.log_redaction import redact_secrets
from app.services.market_movers_service import get_market_movers_service

logger = logging.getLogger(__name__)


# ── Tool-result error text (what the MODEL may see) ───────────────────────────
#
# ⚠️ A tool result is sent to a third-party LLM, and whatever the model quotes from it can
# land in `research_findings` → `research_reports.full_report` → the report detail API. FMP
# puts the key in the query string (`fmp.py`: `params["apikey"] = self.api_key`), and
# `_make_request_impl` re-raises a raw `httpx.HTTPStatusError` for every status it does not
# type (400/403/404/405/…) — whose `str()` is "Client error '404 …' for url
# '…/income-statement?symbol=X&limit=0&apikey=<KEY>'". These handlers used to return
# `{"error": str(e)}`, i.e. the production FMP key, verbatim, to Gemini. The chat door never
# did (`gemini._run_tool_handler` redacts); this door had no equivalent.

_TOOL_ERROR_MAX_CHARS = 200


def _scrub(text: Any, fmp: Any = None) -> str:
    """`redact_secrets` plus a literal replacement of the FMP key itself.

    The regex anchors on a parameter NAME (`apikey=`); the literal pass catches the key in
    any other shape (a repr, a re-encoded URL, a message someone formats differently).
    """
    out = redact_secrets(text)
    for secret in (getattr(settings, "FMP_API_KEY", None), getattr(fmp, "api_key", None)):
        # Length floor: never blank out a short/empty placeholder that would match everywhere.
        if isinstance(secret, str) and len(secret) >= 8:
            out = out.replace(secret, "***")
    return out


def tool_error_message(exc: BaseException, fmp: Any = None) -> str:
    """The `error` string a tool result may carry to the model. Never a credential, never a URL.

    An httpx error is reduced to a generic line (its message IS the request URL, and the
    model has no use for our endpoint or query string); anything else keeps its message —
    our typed FMP exceptions say something useful ("FMP rate limit hit on …") — scrubbed
    and capped. Details go to the log instead (`_log_tool_failure`).
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return f"upstream data request failed (HTTP {status})" if status else "upstream data request failed"
    if isinstance(exc, httpx.HTTPError):
        return f"upstream data request failed ({type(exc).__name__})"
    return _scrub(exc, fmp)[:_TOOL_ERROR_MAX_CHARS]


def _log_tool_failure(tool: str, ticker: str, exc: BaseException, fmp: Any = None) -> None:
    # Scrubbed here as well, not only by main.py's root SecretRedactingFilter: that filter
    # exists only where main.py configured logging (not in scripts, not under pytest).
    logger.warning(
        "Tool %s failed (ticker=%s): %s", tool, ticker or "-",
        _scrub(f"{type(exc).__name__}: {exc}", fmp),
    )


def _bounded_int(value: Any, default: int, hi: Optional[int] = None) -> int:
    """A model-chosen count, clamped to [1, hi]. `min(limit, 12)` alone let 0 and negatives
    through to FMP (a 4xx, i.e. the leak above) and `data[:-3]` silently dropped rows; a
    non-numeric or null `limit` raised outside the handler's `try`."""
    try:
        n = int(value) if value is not None and not isinstance(value, bool) else default
    except (TypeError, ValueError):
        n = default
    n = max(1, n)
    return min(n, hi) if hi is not None else n


def _ticker_arg(args: Dict[str, Any]) -> str:
    return str(args.get("ticker") or "").strip().upper()


_NO_TICKER = "ticker is required"


# ── Dividends and buybacks: never the raw cash-flow lines ─────────────────────
#
# FMP's `commonStockRepurchased`, `commonDividendsPaid`, `netDividendsPaid` and legacy
# `dividendsPaid` are not reliably what their names say (2026-10-22). INTC's −$181M / −$617M
# "repurchases" (Q2 2025 / Q2 2026) are employee RSU tax withholding — Intel has bought back
# nothing since 2021 — and its Q2 2026 −$14.3B `commonDividendsPaid` is a payment to a
# minority partner (SEC `PaymentsToMinorityShareholders`, the Apollo Ireland fab stake). A
# survey of 15 issuers x 8 quarters found 11 of 120 repurchase rows that were something else.
# Handed to the model raw, they read as "Intel returned ~$15B to shareholders" in the free
# text that feeds Stage A.
#
# So the cash-flow tools never select those lines (`_compress_financial_data`). The report's
# OWN ticker gets the Signal of Confidence figures instead — buybacks checked against the SEC
# filing (`services/_buyback_verification.py`), dividends counted only in a fiscal year the
# company paid a common dividend per share — the very object the report's capital-allocation
# card is built from, so the narrative and the card cannot disagree. Any other ticker (a
# competitor the model looked up) gets an explicit "left out" note: no figure, and no empty
# list a model would read as "pays nothing". A raw line merely ANNOTATED was rejected: the
# model reads the number, not the caveat.

_RETURNS_SOURCE_NOTE = (
    "Use these, never another cash-flow line, for dividends and buybacks. They are the "
    "figures the report's capital-allocation card shows: buybacks are checked against the "
    "company's SEC filings where available (employee tax withholding and preferred "
    "redemptions taken out), and dividends count only in fiscal years the company paid a "
    "common dividend per share. A null amount is a quarter whose cash flow was not "
    "reported, not a zero."
)
_RETURNS_OTHER_TICKER_NOTE = (
    "Dividend and buyback lines are left out of this statement on purpose: the raw lines "
    "can hold employee tax withholding, preferred-stock redemptions or payments to minority "
    "partners. Make no claim about this company's dividends or buybacks from it."
)
_RETURNS_UNAVAILABLE_NOTE = (
    "Checked dividend and buyback figures are unavailable for this company in this report. "
    "Unavailable is not zero: make no claim about its dividends or buybacks."
)
_RETURNS_NON_USD_NOTE = (
    "Yields and verdicts are withheld: the amounts are in {cur}, and the yields were "
    "measured against a US-dollar market cap."
)

#: Signal of Confidence `degraded` reasons that leave its figures intact — the collector's
#: `_FROZEN_FINANCIALS_IGNORABLE["signal_of_confidence"]`, which already refuses (None) a
#: build carrying any other reason. Re-checked here so an unsettled build (a `buyback_check`
#: that could not reach SEC = FMP's unchecked figures) never reaches the model as checked.
_RETURNS_TOLERATED_DEGRADATION = frozenset({"cash_flow_row"})


def _listing_key(ticker: Any) -> str:
    """`BRK.B` and `BRK-B` name one listing (FMP uses the dash)."""
    return str(ticker or "").strip().upper().replace(".", "-")


def _statement_currency(rows: Any) -> Optional[str]:
    """The one `reportedCurrency` the fetched statement rows declare, else None (none
    declared, or rows that disagree)."""
    codes = {
        r["reportedCurrency"].strip().upper()
        for r in (rows if isinstance(rows, list) else [])
        if isinstance(r, dict)
        and isinstance(r.get("reportedCurrency"), str)
        and r["reportedCurrency"].strip()
    }
    return codes.pop() if len(codes) == 1 else None


def _rounded(value: Any, places: int = 2) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(float(value), places) if math.isfinite(value) else None


def shareholder_returns_block(soc: Any, currency: Optional[str]) -> Dict[str, Any]:
    """What a cash-flow tool result says about dividends and buybacks for the report's own
    ticker, from its Signal of Confidence build ``soc`` (None = refused or failed).

    Amounts are the service's per-quarter millions, in the statement's reporting currency
    (the service converts nothing). Its yields divide those amounts by a US-dollar market
    cap, so they — and the yield-based verdicts — are given only when ``currency`` is USD.
    Never raises: anything malformed is "unavailable", logged.
    """
    if soc is None:
        return {"available": False, "note": _RETURNS_UNAVAILABLE_NOTE}
    symbol = getattr(soc, "symbol", "?")
    try:
        degraded = getattr(soc, "degraded", None) or []
        blocking = [str(r) for r in degraded if str(r) not in _RETURNS_TOLERATED_DEGRADATION]
        if blocking:
            logger.warning(
                "[research-tool-returns-refused] ticker=%s: Signal of Confidence build is "
                "degraded (%s) — dividends/buybacks withheld from the model, not handed over "
                "unchecked", symbol, ", ".join(blocking),
            )
            return {"available": False, "note": _RETURNS_UNAVAILABLE_NOTE}

        quarters = []
        for dp in soc.data_points or []:
            reported = getattr(dp, "cash_flow_reported", True) is not False
            quarters.append({
                "period": dp.period,
                "dividends_paid": _rounded(dp.dividend_amount) if reported else None,
                "buybacks": _rounded(dp.buyback_amount) if reported else None,
            })

        s = soc.summary
        div = soc.dividend_info
        usd = currency == "USD"
        trailing: Dict[str, Any] = {
            "share_count_change_pct": (
                _rounded(s.share_count_change)
                if getattr(s, "share_count_change_known", True) else None
            ),
        }
        if usd:
            trailing.update({
                "dividend_yield_pct": _rounded(s.dividend_yield),
                "buyback_yield_pct": _rounded(s.buyback_yield),
                "total_yield_pct": _rounded(s.total_yield),
                "buyback_status": s.buyback_status,
                # "None" for a non-payer, as the report's card says — never a verdict on a
                # payout that does not exist.
                "dividend_status": div.status if div else "None",
            })
        block: Dict[str, Any] = {
            "available": True,
            "note": _RETURNS_SOURCE_NOTE,
            "amounts_in": f"{currency} millions" if currency else (
                "millions, in the statement's reporting currency"
            ),
            "quarters": quarters,
            "trailing_12_months": trailing,
            "annual_dividends_per_share": [
                {"fiscal_year": a.year, "per_share": _rounded(a.per_share, 4)}
                for a in (div.annual_dividends if div else [])
            ],
        }
        if not usd:
            block["yields_note"] = _RETURNS_NON_USD_NOTE.format(
                cur=currency or "an unknown reporting currency"
            )
        return block
    except Exception as e:  # a malformed build must cost the figures, never the tool
        logger.warning(
            "[research-tool-returns-malformed] ticker=%s: %s: %s — dividends/buybacks "
            "withheld from the model", symbol, type(e).__name__, e, exc_info=True,
        )
        return {"available": False, "note": _RETURNS_UNAVAILABLE_NOTE}


# ── Gemini Function Declarations ──────────────────────────────────────────────


def _dividend_history_licensed() -> bool:
    """True when FMP's `dividends` endpoint is inside the signed Order Form.

    Imported lazily so this module stays importable without dragging configuration in.
    """
    from app.integrations.fmp_entitlements import entitlement_error  # noqa: PLC0415

    return entitlement_error("dividends") is None


def build_fmp_tool_declarations() -> types.Tool:
    """Build Gemini Tool with FMP function declarations for agentic research.

    ⚠️ A tool whose dataset is unlicensed is OMITTED, not left to fail quietly.
    `FMPClient.get_dividend_history` swallows the entitlement exception and returns `[]`,
    so `fetch_dividend_history` handed the model `{"dividends": []}` with no error marker
    — and the only honest reading of that payload is "this company has never paid a
    dividend". A 20-credit report could assert exactly that about KO, JNJ or PG. Stage A
    is also capped at four tool rounds, so one was being spent on a call structurally
    guaranteed to return nothing.

    Derived from the manifest, not hardcoded: buying "Market Calendar" restores the tool
    with no further code change, matching `analyst_section_available`'s contract.
    """
    declarations = [
            types.FunctionDeclaration(
                name="fetch_quarterly_financials",
                description=(
                    "Fetch quarterly financial statements to analyze seasonal trends, "
                    "recent quarter performance, or detect acceleration/deceleration "
                    "in revenue and margins."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "ticker": types.Schema(
                            type=types.Type.STRING,
                            description="Stock ticker symbol",
                        ),
                        "statement_type": types.Schema(
                            type=types.Type.STRING,
                            description="Type of financial statement",
                            enum=["income", "balance_sheet", "cash_flow"],
                        ),
                        "limit": types.Schema(
                            type=types.Type.INTEGER,
                            description="Number of quarters (default 8)",
                        ),
                    },
                    required=["ticker", "statement_type"],
                ),
            ),
            types.FunctionDeclaration(
                name="fetch_sector_performance",
                description=(
                    "Fetch current sector performance to contextualize the stock's "
                    "performance relative to its sector and broader market trends."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={},
                ),
            ),
            types.FunctionDeclaration(
                name="fetch_more_news",
                description=(
                    "Fetch additional recent news articles about a company to "
                    "identify catalysts, risks, or sentiment shifts."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "ticker": types.Schema(
                            type=types.Type.STRING,
                            description="Stock ticker symbol",
                        ),
                        "limit": types.Schema(
                            type=types.Type.INTEGER,
                            description="Number of articles (default 10)",
                        ),
                    },
                    required=["ticker"],
                ),
            ),
            types.FunctionDeclaration(
                name="fetch_extended_financials",
                description=(
                    "Fetch extended annual financial history (up to 10 years) "
                    "for long-term trend analysis on income, balance sheet, or cash flow."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "ticker": types.Schema(
                            type=types.Type.STRING,
                            description="Stock ticker symbol",
                        ),
                        "statement_type": types.Schema(
                            type=types.Type.STRING,
                            description="Type of financial statement",
                            enum=["income", "balance_sheet", "cash_flow"],
                        ),
                    },
                    required=["ticker", "statement_type"],
                ),
            ),
            types.FunctionDeclaration(
                name="research_complete",
                description=(
                    "Signal that you have gathered enough data and are ready to "
                    "produce the final analysis. Call this when you don't need "
                    "any additional financial data."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "summary": types.Schema(
                            type=types.Type.STRING,
                            description="Brief summary of key findings from your research",
                        ),
                    },
                    required=["summary"],
                ),
            ),
    ]
    if _dividend_history_licensed():
        declarations.append(
                types.FunctionDeclaration(
                    name="fetch_dividend_history",
                    description=(
                        "Fetch dividend payment history to analyze yield trends, "
                        "payout ratio sustainability, and dividend growth rate."
                    ),
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "ticker": types.Schema(
                                type=types.Type.STRING,
                                description="Stock ticker symbol",
                            ),
                            "limit": types.Schema(
                                type=types.Type.INTEGER,
                                description="Number of dividend records (default 20)",
                            ),
                        },
                        required=["ticker"],
                    ),
                )
        )
    return types.Tool(function_declarations=declarations)


# ── Tool Handlers ─────────────────────────────────────────────────────────────


def build_tool_handlers(
    fmp: FMPClient,
    *,
    subject_ticker: str = "",
    signal_of_confidence: Any = None,
) -> Dict[str, Callable[..., Awaitable[Dict[str, Any]]]]:
    """Build async handler functions for each FMP tool.

    ``subject_ticker`` / ``signal_of_confidence``: the report's own ticker and its Signal of
    Confidence build (None when the collector refused or lost it). A cash-flow result for
    that ticker carries the build's checked dividends and buybacks; any other ticker's says
    they were left out ("Dividends and buybacks: never the raw cash-flow lines" above).
    """
    subject = _listing_key(subject_ticker)

    def _cash_flow_result(ticker: str, data: Any) -> Dict[str, Any]:
        result = _compress_financial_data(data, "cash_flow")
        if subject and _listing_key(ticker) == subject:
            result["shareholder_returns"] = shareholder_returns_block(
                signal_of_confidence, _statement_currency(data)
            )
        else:
            result["shareholder_returns"] = {
                "available": False, "note": _RETURNS_OTHER_TICKER_NOTE,
            }
        return result

    async def fetch_quarterly_financials(args: Dict[str, Any]) -> Dict[str, Any]:
        ticker = _ticker_arg(args)
        statement = args.get("statement_type", "income")
        limit = _bounded_int(args.get("limit"), 8, hi=12)  # 1..12 quarters
        if not ticker:
            return {"error": _NO_TICKER, "data": []}

        try:
            if statement == "income":
                data = await fmp.get_income_statement(ticker, "quarter", limit)
            elif statement == "balance_sheet":
                data = await fmp.get_balance_sheet(ticker, "quarter", limit)
            elif statement == "cash_flow":
                data = await fmp.get_cash_flow_statement(ticker, "quarter", limit)
                return _cash_flow_result(ticker, data)
            else:
                return {"error": f"Unknown statement type: {statement}"}

            return _compress_financial_data(data, statement)

        except Exception as e:
            _log_tool_failure("fetch_quarterly_financials", ticker, e, fmp)
            return {"error": tool_error_message(e, fmp), "data": []}

    async def fetch_dividend_history(args: Dict[str, Any]) -> Dict[str, Any]:
        ticker = _ticker_arg(args)
        limit = _bounded_int(args.get("limit"), 20)
        if not _dividend_history_licensed():
            # Belt-and-braces: the declaration is omitted above, so Gemini should never
            # reach here. If it does (a cached tool list, a hand-built call), an EXPLICIT
            # marker is the difference between "we are not allowed to look" and "this
            # company pays nothing" — `get_dividend_history` swallows the entitlement
            # exception and returns `[]`, which reads as the latter.
            return {"error": "not_licensed", "dividends": []}
        if not ticker:
            return {"error": _NO_TICKER, "dividends": []}
        try:
            data = await fmp.get_dividend_history(ticker, limit)
            return {"dividends": data[:limit]}
        except Exception as e:
            _log_tool_failure("fetch_dividend_history", ticker, e, fmp)
            return {"error": tool_error_message(e, fmp), "dividends": []}

    async def fetch_sector_performance(args: Dict[str, Any]) -> Dict[str, Any]:
        try:
            data = await get_market_movers_service().get_sector_performance()
            return {"sectors": data}
        except Exception as e:
            _log_tool_failure("fetch_sector_performance", "", e, fmp)
            return {"error": tool_error_message(e, fmp), "sectors": []}

    async def fetch_more_news(args: Dict[str, Any]) -> Dict[str, Any]:
        ticker = _ticker_arg(args)
        limit = _bounded_int(args.get("limit"), 10, hi=15)
        if not ticker:
            # NOT a formality: `get_stock_news` with no symbol makes FMP fall back to AAPL,
            # so an empty ticker handed the model Apple's news as this company's.
            return {"error": _NO_TICKER, "articles": []}
        try:
            data = await fmp.get_stock_news(ticker, limit)
            if getattr(data, "fetch_failed", False):
                # An OUTAGE, not a quiet week. `get_stock_news` degrades a non-quota
                # failure to an empty list that remembers it failed (`EmptyAfterFailure`);
                # iterating it here handed the model `{"articles": []}` — byte-identical
                # to "no coverage" — and a 20-credit report then narrated "there is no
                # recent news" and froze that claim in `ticker_report_data` for the whole
                # close-aligned window. Same shape the chat tool already returns.
                # `reason` is `f"{type(e).__name__}: {e}"` from fmp.py — an httpx URL, key
                # and all — so it is scrubbed before it is logged.
                logger.warning(
                    "Tool fetch_more_news: news feed FAILED for %s (%s) — reported as "
                    "unavailable, not empty", ticker, _scrub(getattr(data, "reason", ""), fmp),
                )
                return {
                    "error": "news feed unavailable (upstream fetch failed)",
                    "articles": [],
                    "note": "The news feed could not be reached; do not say there is no news.",
                }
            # Compress to key fields
            articles = []
            for a in data:
                articles.append({
                    "title": a.get("title", ""),
                    "date": a.get("publishedDate", "")[:10],
                    "text": (a.get("text", "") or "")[:300],
                    "sentiment": a.get("sentiment", ""),
                })
            return {"articles": articles}
        except Exception as e:
            _log_tool_failure("fetch_more_news", ticker, e, fmp)
            return {"error": tool_error_message(e, fmp), "articles": []}

    async def fetch_extended_financials(args: Dict[str, Any]) -> Dict[str, Any]:
        ticker = _ticker_arg(args)
        statement = args.get("statement_type", "income")
        if not ticker:
            return {"error": _NO_TICKER, "data": []}
        try:
            if statement == "income":
                data = await fmp.get_income_statement(ticker, "annual", 10)
            elif statement == "balance_sheet":
                data = await fmp.get_balance_sheet(ticker, "annual", 10)
            elif statement == "cash_flow":
                data = await fmp.get_cash_flow_statement(ticker, "annual", 10)
                return _cash_flow_result(ticker, data)
            else:
                return {"error": f"Unknown statement type: {statement}"}
            return _compress_financial_data(data, statement)
        except Exception as e:
            _log_tool_failure("fetch_extended_financials", ticker, e, fmp)
            return {"error": tool_error_message(e, fmp), "data": []}

    async def research_complete(args: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "complete", "summary": args.get("summary", "")}

    return {
        "fetch_quarterly_financials": fetch_quarterly_financials,
        "fetch_dividend_history": fetch_dividend_history,
        "fetch_sector_performance": fetch_sector_performance,
        "fetch_more_news": fetch_more_news,
        "fetch_extended_financials": fetch_extended_financials,
        "research_complete": research_complete,
    }


# ── Data Compression ─────────────────────────────────────────────────────────


def _compress_financial_data(
    data: list, statement_type: str
) -> Dict[str, Any]:
    """Compress financial data to fit within Gemini context limits."""
    if not data:
        return {"data": []}

    # Select key fields based on statement type
    # ⚠️ These are SELECT lists — `{k: item[k] for k in fields if k in item}` below — so a
    # name `/stable` no longer returns is not an error, it is a FIELD THAT SILENTLY
    # VANISHES from what the model is handed. Verified against a live AAPL payload:
    # `calendarYear` → `fiscalYear` (all three statements), `epsdiluted` → `epsDiluted`.
    # So Cay AI asked for an income statement and got back no EPS and no year label at all.
    #
    # BOTH spellings are listed rather than swapped: the select is presence-based, so the
    # dead name costs nothing and an upstream revert keeps working.
    #
    # ⚠️ Never add a dividend or buyback line to `cash_flow` (`commonStockRepurchased`,
    # `commonDividendsPaid`, `netDividendsPaid`, `dividendsPaid`): the handlers attach the
    # checked figures under `shareholder_returns` instead ("Dividends and buybacks: never
    # the raw cash-flow lines" above).
    key_fields = {
        "income": [
            "date", "fiscalYear", "calendarYear", "period", "revenue", "grossProfit",
            "operatingIncome", "netIncome", "epsDiluted", "epsdiluted", "eps",
            "operatingExpenses",
        ],
        "balance_sheet": [
            "date", "fiscalYear", "calendarYear", "period", "totalAssets",
            "totalLiabilities", "totalStockholdersEquity", "cashAndCashEquivalents",
            "totalDebt", "netDebt", "totalCurrentAssets", "totalCurrentLiabilities",
            "retainedEarnings",
        ],
        "cash_flow": [
            "date", "fiscalYear", "calendarYear", "period", "operatingCashFlow",
            "capitalExpenditure", "freeCashFlow",
        ],
    }

    fields = key_fields.get(statement_type, key_fields["income"])
    compressed = []
    for item in data:
        row = {k: item.get(k) for k in fields if k in item}
        compressed.append(row)

    return {"data": compressed}
