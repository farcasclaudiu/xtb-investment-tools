import argparse
import contextlib
import io
import json
import re
import warnings
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from html import escape
from pathlib import Path

import pandas as pd

import html_charts

REPORT_FILE: Path | None = None  # resolved per run via resolve_report_file()
POSITIONS_SHEET = "Closed Positions"
OPEN_POSITIONS_SHEET = "Open Positions"
CASH_SHEET = "Cash Operations"
HEADER_ROW = 4
RESULTS_DIR = Path("results")
DEFAULT_EMBEDDED_FX_FEE_RATE = 0.005

# XTB ticker codes that don't resolve on Yahoo → verified same-fund Yahoo symbols.
# Only add mappings confirmed to be the SAME fund (same ISIN/share class), never a
# proxy from a different fund (would produce wrong absolute prices).
#   MEUD.FR  = Amundi Core STOXX Europe 600 UCITS ETF (Euronext Paris: .FR / .PA)
SYMBOL_ALIASES = {
    "MEUD.FR": "MEUD.PA",
}

# Tickers intentionally left at cost (no trusted live price). `reason` is surfaced
# in the report so the decision is documented. A same-ISIN Yahoo symbol that
# *diverges* from the broker is NOT a valid price source — it's a different share
# class and would distort the valuation, so we hold at cost instead.
COST_FALLBACK_NOTES = {
    "SXXPIEX.DE": (
        "no trusted live price; the same-ISIN Yahoo symbol EXSA.DE diverges from "
        "the broker (different share class), so held at cost"
    ),
}


def suppress_openpyxl_default_style_warning() -> None:
    warnings.filterwarnings(
        "ignore",
        message=r"Workbook contains no default style, apply openpyxl's default",
        category=UserWarning,
        module=r"openpyxl\.styles\.stylesheet",
    )


suppress_openpyxl_default_style_warning()

# XTB "Type" values that represent trading activity (not cash transfers).
TRADE_TYPE_RE = re.compile(
    r"stock\s*(purchase|sale|buy|sell)|\bopen\b|\bclose\b",
    re.IGNORECASE,
)
# XTB comment: "OPEN BUY 6 @ 301.50", "CLOSE SELL 2 @ 14.31", ...
TRADE_COMMENT_RE = re.compile(r"(OPEN|CLOSE)\s+(BUY|SELL)\b", re.IGNORECASE)
PRICE_RE = re.compile(r"@\s*([\d.,]+)")
QTY_RE = re.compile(r"(?:OPEN|CLOSE)\s+(?:BUY|SELL)\s+([\d./]+)", re.IGNORECASE)
DIVIDEND_RE = re.compile(r"\bdividend|dywidend|dividende\b", re.IGNORECASE)
DIVIDEND_TAX_RE = re.compile(r"dividend\s*tax|tax.*dividend|withholding", re.IGNORECASE)
INTEREST_RE = re.compile(r"interest|free.?funds", re.IGNORECASE)
DEPOSIT_RE = re.compile(r"deposit|top.?up|deposit.?funds", re.IGNORECASE)
WITHDRAW_RE = re.compile(r"withdraw|withdrawal|payout", re.IGNORECASE)
CURRENCY_CONVERSION_RE = re.compile(r"currency\s*conversion", re.IGNORECASE)
CONVERSION_FEE_RE = re.compile(
    r"(conversion|fx).*(fee|commission)|fee.*(conversion|fx)|\bfx\b",
    re.IGNORECASE,
)
CONVERSION_RE = re.compile(
    r"currency\s*conversion|conversion\s*fee|fx",
    re.IGNORECASE,
)


def resolve_report_file(path: Path | str | None = None, *, auto_detect: bool = False) -> Path:
    """Resolve the XTB report file to process.

    Prefer an explicit ``path`` (from the CLI or a library call). Auto-detection
    of the single ``.xlsx`` in the current working directory is available only
    when ``auto_detect`` is true, skipping Excel lock files (``~$...``) and
    dotfiles.

    Raises FileNotFoundError when there is no explicit path and auto-detection
    is not enabled, or when there is no auto-detect candidate. Raises ValueError
    when several auto-detect candidates make the choice ambiguous. Works with
    any same-format XTB export regardless of account or period.
    """
    if path is not None:
        return Path(path)
    if not auto_detect:
        raise FileNotFoundError(
            "No .xlsx report path was provided. Pass it explicitly, e.g.: "
            "python main.py <report.xlsx>, or use --auto-detect to process "
            "the single .xlsx in the current directory."
        )

    candidates = [
        p for p in sorted(Path.cwd().glob("*.xlsx"))
        if not p.name.startswith(("~$", "."))
    ]
    if not candidates:
        raise FileNotFoundError(
            "No .xlsx report found in the current directory. "
            "Pass it explicitly, e.g.: python main.py <report.xlsx>"
        )
    if len(candidates) > 1:
        names = ", ".join(p.name for p in candidates)
        raise ValueError(
            f"Multiple .xlsx files found ({names}). "
            "Specify which one to use, e.g.: python main.py <report.xlsx>"
        )
    return candidates[0]


def detect_currency() -> str:
    name = REPORT_FILE.stem.upper()
    for prefix in ("EUR", "USD", "GBP", "PLN", "CHF", "JPY", "AUD", "CAD", "CZK", "HUF"):
        if name.startswith(prefix):
            return prefix
    return "EUR"


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------
def clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = (
        df.columns.astype(str)
        .str.strip()
        .str.lower()
        .str.replace(r"\s+", "_", regex=True)
        .str.replace(r"[^\w_]", "", regex=True)
    )
    return df


def find_column(df: pd.DataFrame, candidates: list[str], required: bool = True) -> str | None:
    normalized = {col.lower(): col for col in df.columns}
    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    for col in df.columns:
        for candidate in candidates:
            if candidate in col:
                return col
    if required:
        raise ValueError(
            f"Could not find any of these columns: {candidates}. "
            f"Available columns: {list(df.columns)}"
        )
    return None


def parse_numeric(series: pd.Series) -> pd.Series:
    return (
        series.astype(str)
        .str.replace(",", ".", regex=False)
        .str.replace(r"[^\d.\-]", "", regex=True)
        .replace("", pd.NA)
        .pipe(pd.to_numeric, errors="coerce")
        .fillna(0.0)
    )


def money(value: float) -> str:
    return f"{value:,.2f}"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load_meta() -> dict[str, str]:
    raw = pd.read_excel(REPORT_FILE, sheet_name=CASH_SHEET, header=None, nrows=4)
    meta = {"account": "", "period_from": "", "period_to": ""}
    for _, row in raw.iterrows():
        key = str(row.iloc[0]).strip().lower()
        val = "" if pd.isna(row.iloc[1]) else str(row.iloc[1]).strip()
        if "account" in key:
            meta["account"] = val
        elif "from" in key:
            meta["period_from"] = val
        elif "to" in key:
            meta["period_to"] = val
    return meta


def load_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, float]:
    if not REPORT_FILE.exists():
        raise FileNotFoundError(f"Could not find {REPORT_FILE.resolve()}")

    sheet_names = pd.ExcelFile(REPORT_FILE).sheet_names

    positions = pd.read_excel(REPORT_FILE, sheet_name=POSITIONS_SHEET, header=HEADER_ROW)
    cash_ops = pd.read_excel(REPORT_FILE, sheet_name=CASH_SHEET, header=HEADER_ROW)
    open_positions = (
        pd.read_excel(REPORT_FILE, sheet_name=OPEN_POSITIONS_SHEET, header=HEADER_ROW)
        if OPEN_POSITIONS_SHEET in sheet_names
        else pd.DataFrame()
    )

    positions = clean_columns(positions).dropna(how="all")
    cash_ops = clean_columns(cash_ops).dropna(how="all")
    open_positions = clean_columns(open_positions).dropna(how="all")

    # Capture the broker-reported "Total" row before dropping it (for reconciliation).
    broker_total = 0.0
    type_col = find_column(cash_ops, ["type", "operation"], required=False)
    amount_col = find_column(
        cash_ops, ["amount", "value", "net_amount", "cash", "change", "payment"],
        required=False,
    )
    if type_col and amount_col:
        total_mask = cash_ops[type_col].astype(str).str.strip().str.match(
            r"(?i)total", na=False
        )
        if total_mask.any():
            broker_total = float(parse_numeric(cash_ops.loc[total_mask, amount_col]).iloc[0])

    # Drop summary/total rows that carry no per-row detail.
    pos_col = find_column(positions, ["instrument"], required=False)
    if pos_col is not None:
        positions = positions.loc[
            ~positions[pos_col].astype(str).str.strip().str.match(
                r"(?i)total|profit/?loss|totals", na=False
            )
        ].copy()
    if type_col is not None:
        cash_ops = cash_ops.loc[
            ~cash_ops[type_col].astype(str).str.strip().str.match(
                r"(?i)total|profit/?loss|totals", na=False
            )
        ].copy()

    return positions, cash_ops, open_positions, broker_total


# ---------------------------------------------------------------------------
# Trade parsing (from Cash Operations comments)
# ---------------------------------------------------------------------------
@dataclass
class Trade:
    ticker: str
    action: str          # "open" or "close"
    side: str            # "buy" or "sell"
    shares: float
    price: float
    value: float         # gross cash magnitude (always positive)
    date: pd.Timestamp | None = None
    name: str = ""       # descriptive instrument label (e.g. "S&P 500")


def parse_quantity(token: str) -> float:
    token = token.strip()
    if "/" in token:
        num, den = token.split("/", 1)
        try:
            return float(num) / float(den) if float(den) != 0 else 0.0
        except ValueError:
            return 0.0
    try:
        return float(token.replace(",", "."))
    except ValueError:
        return 0.0


def normalize_trade_side(type_val: str, action: str, side: str) -> str:
    """Return economic side for XTB trade rows."""
    lowered_type = type_val.lower()
    if action == "close" and side == "buy" and "sell" in lowered_type:
        return "sell"
    return side


def parse_executed_quantity(comment: str, value: float, price: float) -> float:
    match = QTY_RE.search(comment)
    if match:
        token = match.group(1)
        if "/" in token:
            try:
                numerator = float(token.split("/", 1)[0].replace(",", "."))
                if numerator > 0:
                    return numerator
            except ValueError:
                pass
        parsed = parse_quantity(token)
        if parsed > 0:
            return parsed
    return round(abs(value) / price, 6) if price > 0 else 0.0


def extract_trades(cash_ops: pd.DataFrame) -> list[Trade]:
    type_col = find_column(cash_ops, ["type", "operation"], required=False)
    ticker_col = find_column(
        cash_ops, ["ticker", "symbol", "instrument", "market"], required=False
    )
    name_col = find_column(cash_ops, ["instrument", "name", "description"], required=False)
    amount_col = find_column(
        cash_ops, ["amount", "value", "net_amount", "cash", "change", "payment"],
        required=False,
    )
    date_col = find_column(
        cash_ops, ["time", "date", "operation_date", "booking_date", "transaction_date"],
        required=False,
    )
    comment_col = find_column(cash_ops, ["comment", "description", "details"], required=False)

    if not (type_col and ticker_col and amount_col):
        return []

    trades: list[Trade] = []
    for _, row in cash_ops.iterrows():
        type_val = str(row.get(type_col, "")).strip()
        comment = str(row.get(comment_col, "")) if comment_col else ""

        is_trade = bool(TRADE_TYPE_RE.search(type_val)) or bool(TRADE_COMMENT_RE.search(comment))
        if not is_trade:
            continue
        if "dividend" in type_val.lower() or "interest" in type_val.lower():
            continue

        match = TRADE_COMMENT_RE.search(comment)
        if match:
            action = match.group(1).lower()
            side = normalize_trade_side(type_val, action, match.group(2).lower())
        else:
            action = "open"
            lowered = type_val.lower()
            side = "buy" if any(t in lowered for t in ("buy", "purchase")) else "sell"

        value = parse_numeric(pd.Series([row[amount_col]])).iloc[0]
        value = abs(float(value))
        if value <= 0:
            continue

        price = 0.0
        price_match = PRICE_RE.search(comment)
        if price_match:
            price = parse_numeric(pd.Series([price_match.group(1)])).iloc[0]

        shares = parse_executed_quantity(comment, value, price)

        dt = pd.to_datetime(row.get(date_col), errors="coerce") if date_col else pd.NaT
        raw_name = ""
        if name_col:
            nv = row.get(name_col)
            raw_name = "" if pd.isna(nv) else str(nv).strip()
        trades.append(
            Trade(
                ticker=str(row[ticker_col]).strip(),
                action=action,
                side=side,
                shares=float(shares),
                price=float(price),
                value=float(value),
                date=None if pd.isna(dt) else dt,
                name=raw_name,
            )
        )
    return trades


# ---------------------------------------------------------------------------
# Live market prices (yfinance)
# ---------------------------------------------------------------------------
def _parse_as_of(meta: dict[str, str]) -> date:
    """Valuation date = report 'Date to'. Falls back to today."""
    raw = meta.get("period_to", "")
    ts = pd.to_datetime(raw, errors="coerce")
    if pd.isna(ts):
        return date.today()
    return ts.date()


def _yf():
    """Lazy import so tests / offline runs don't require yfinance."""
    import yfinance as yf
    return yf


def _history(ticker, **kwargs):
    """Call yfinance history while suppressing noisy transport diagnostics."""
    with contextlib.redirect_stderr(io.StringIO()):
        return ticker.history(**kwargs)


_PRICE_CACHE: dict[str, dict | None] = {}


def fetch_prices(
    tickers: list[str],
    as_of: date,
    account_currency: str,
) -> dict[str, dict | None]:
    """Fetch last available close on/before `as_of` for each ticker.

    Returns {ticker: {"price", "currency", "fx", "as_of", "source"} | None}.
    Never raises — failed lookups map to None (caller falls back to cost).
    """
    out: dict[str, dict | None] = {}
    missing = [t for t in tickers if t not in _PRICE_CACHE]
    if missing:
        try:
            yf = _yf()
        except Exception:
            for t in missing:
                _PRICE_CACHE[t] = None
        else:
            start = as_of - timedelta(days=14)
            end = as_of + timedelta(days=1)  # history `end` is exclusive
            for t in missing:
                fetch_sym = SYMBOL_ALIASES.get(t, t)
                _PRICE_CACHE[t] = _fetch_one(
                    yf, fetch_sym, start, end, as_of, account_currency
                )
    for t in tickers:
        out[t] = _PRICE_CACHE.get(t)
    return out


def _fetch_one(yf, ticker, start, end, as_of, account_currency) -> dict | None:
    sym = ticker.strip().upper()
    if not sym or sym == "NAN":
        return None
    for _attempt in range(2):  # one retry on transient failure
        try:
            tk = yf.Ticker(sym)
            hist = _history(tk, start=start, end=end, auto_adjust=False)
            if hist is None or hist.empty:
                continue
            # Normalize to naive dates for comparison (history is tz-aware).
            idx_naive = pd.to_datetime(hist.index).tz_localize(None)
            mask = idx_naive <= pd.Timestamp(as_of)
            hist = hist.loc[mask]
            if hist.empty:
                continue
            close = float(hist["Close"].iloc[-1])
            price_date = pd.to_datetime(hist.index[-1]).tz_localize(None).date()
            try:
                cur = (tk.fast_info.get("currency") or "").upper()
            except Exception:
                cur = ""
            if not cur:
                cur = account_currency.upper()
            fx = 1.0
            if cur and cur != account_currency.upper():
                fx = _fx_rate(yf, cur, account_currency.upper())
                if fx is None:
                    return None
            return {
                "price": close,
                "currency": cur,
                "fx": fx,
                "price_local": close,
                "as_of": price_date,
                "source": "live",
            }
        except Exception:
            continue
    return None


def _fx_rate(yf, from_cur: str, to_cur: str) -> float | None:
    pair = f"{from_cur}{to_cur}=X"
    try:
        tk = yf.Ticker(pair)
        hist = _history(tk, period="5d", auto_adjust=False)
        if hist is None or hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Historical daily closes (for the evolution chart)
# ---------------------------------------------------------------------------
_PRICE_HISTORY_CACHE: dict[str, pd.Series | None] = {}


def fetch_price_history(
    tickers: list[str],
    start: date,
    end: date,
    account_currency: str,
) -> dict[str, pd.Series | None]:
    """Fetch daily closes in account currency for each ticker over [start, end].

    Returns {ticker: pd.Series (naive-date index -> close in acct ccy) | None}.
    Never raises — failed lookups map to None (caller falls back to cost).
    Only call for tickers already valued "live"; cost-fallback tickers are held
    flat at cost by ``build_evolution_series``.
    """
    out: dict[str, pd.Series | None] = {}
    missing = [t for t in tickers if t not in _PRICE_HISTORY_CACHE]
    if missing:
        try:
            yf = _yf()
        except Exception:
            for t in missing:
                _PRICE_HISTORY_CACHE[t] = None
        else:
            # Pad a week back so `asof` has a prior close on the first trade day.
            fetch_start = start - timedelta(days=7)
            fetch_end = end + timedelta(days=1)  # history `end` is exclusive
            for t in missing:
                _PRICE_HISTORY_CACHE[t] = _fetch_history_one(
                    yf, t, fetch_start, fetch_end, account_currency
                )
    for t in tickers:
        out[t] = _PRICE_HISTORY_CACHE.get(t)
    return out


def _fetch_history_one(
    yf, ticker: str, start: date, end: date, account_currency: str
) -> pd.Series | None:
    sym = SYMBOL_ALIASES.get(ticker, ticker).strip().upper()
    if not sym or sym == "NAN":
        return None
    try:
        tk = yf.Ticker(sym)
        hist = _history(tk, start=start, end=end, auto_adjust=False)
        if hist is None or hist.empty:
            return None
        idx = pd.to_datetime(hist.index).tz_localize(None)
        closes = pd.Series(hist["Close"].values, index=idx, name=ticker)
        try:
            cur = (tk.fast_info.get("currency") or "").upper()
        except Exception:
            cur = ""
        if not cur:
            cur = account_currency.upper()
        if cur and cur != account_currency.upper():
            fx_series = _fx_history(yf, cur, account_currency.upper(), start, end)
            if fx_series is None:
                return None
            fx_vals = fx_series.reindex(closes.index, method="ffill")
            closes = (closes * fx_vals).dropna()
        return closes.sort_index()
    except Exception:
        return None


def _fx_history(yf, from_cur: str, to_cur: str, start: date, end: date) -> pd.Series | None:
    pair = f"{from_cur}{to_cur}=X"
    try:
        tk = yf.Ticker(pair)
        hist = _history(tk, start=start, end=end, auto_adjust=False)
        if hist is None or hist.empty:
            return None
        idx = pd.to_datetime(hist.index).tz_localize(None)
        return pd.Series(hist["Close"].values, index=idx).sort_index()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Live valuation per holding
# ---------------------------------------------------------------------------
def valuate_holdings(
    holdings: pd.DataFrame,
    prices: dict[str, dict | None],
) -> pd.DataFrame:
    """Add last_price, market_value, unrealized_pl, return_pct, price_source, weight_pct."""
    df = holdings.copy()
    if df.empty:
        for col in ("last_price", "market_value", "unrealized_pl",
                    "return_pct", "price_source", "weight_pct"):
            df[col] = pd.Series(dtype=float if col != "price_source" else object)
        return df

    last_price = []
    market_value = []
    unrealized_pl = []
    source = []
    for _, row in df.iterrows():
        info = prices.get(row["ticker"])
        if info and info.get("price"):
            price = float(info["price"]) * float(info.get("fx", 1.0))
            mv = float(row["shares"]) * price
            last_price.append(round(price, 6))
            market_value.append(round(mv, 4))
            unrealized_pl.append(round(mv - float(row["cost_basis"]), 4))
            source.append("live")
        else:
            last_price.append(float(row["avg_price"]))
            market_value.append(float(row["cost_basis"]))
            unrealized_pl.append(0.0)
            source.append("cost")
    df["last_price"] = last_price
    df["market_value"] = market_value
    df["unrealized_pl"] = unrealized_pl
    df["price_source"] = source
    df["return_pct"] = df.apply(
        lambda r: round(r["unrealized_pl"] / r["cost_basis"] * 100, 2)
        if r["cost_basis"] else 0.0,
        axis=1,
    )
    total_mv = df["market_value"].sum()
    df["weight_pct"] = (
        (df["market_value"] / total_mv * 100).round(2) if total_mv else 0.0
    )
    return df


# ---------------------------------------------------------------------------
# Holdings + realized P/L (FIFO)
# ---------------------------------------------------------------------------
def analyze_holdings(
    trades: list[Trade],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (open_holdings, realized_pl) using FIFO lot matching.

    realized_pl covers every ticker that had a closing trade, including those
    now fully closed (which no longer appear in open_holdings).

    Trades are processed in chronological order: a position cannot be closed
    before it is opened, and XTB sheets sometimes list close legs before their
    open legs (stable sort preserves sheet order for equal/unknown timestamps).
    """
    lots: dict[str, list[tuple[float, float]]] = {}      # ticker -> [(shares, price)]
    names: dict[str, str] = {}                            # ticker -> display name
    realized: dict[str, float] = {}

    _sort_key = lambda t: t.date if t.date is not None else pd.Timestamp.min
    for t in sorted(trades, key=_sort_key):
        bucket = lots.setdefault(t.ticker, [])
        if t.name and t.ticker not in names:
            names[t.ticker] = t.name
        if t.action == "open":
            if t.side == "buy":
                bucket.append((t.shares, t.price))
            else:  # opening a short
                bucket.append((-t.shares, t.price))
        else:  # close
            to_close = t.shares
            close_value = t.value
            cost_consumed = 0.0
            while to_close > 1e-9 and bucket:
                lot_shares, lot_price = bucket[0]
                if abs(lot_shares) < 1e-9:
                    bucket.pop(0)
                    continue
                # lot sign indicates long(+)/short(-); closing uses same magnitude.
                magnitude = min(abs(lot_shares), to_close)
                cost_consumed += magnitude * lot_price
                remaining = abs(lot_shares) - magnitude
                sign = 1 if lot_shares >= 0 else -1
                if remaining > 1e-9:
                    bucket[0] = (sign * remaining, lot_price)
                else:
                    bucket.pop(0)
                to_close -= magnitude
            # For a long close, proceeds (close_value) - cost = gain.
            realized[t.ticker] = realized.get(t.ticker, 0.0) + (close_value - cost_consumed)

    rows = []
    for ticker, bucket in lots.items():
        net_shares = sum(s for s, _ in bucket)
        if abs(net_shares) < 1e-4:
            continue  # fully closed (tolerance absorbs float residue) -> not an open holding
        long_shares = sum(s for s, _ in bucket if s > 0)
        cost_basis = sum(abs(s) * p for s, p in bucket)
        avg_price = cost_basis / long_shares if long_shares > 0 else 0.0
        rows.append(
            {
                "ticker": ticker,
                "name": names.get(ticker, ""),
                "shares": round(net_shares, 6),
                "cost_basis": round(cost_basis, 4),
                "avg_price": round(avg_price, 4),
            }
        )

    holdings_cols = ["ticker", "name", "shares", "cost_basis", "avg_price"]
    if rows:
        df = pd.DataFrame(rows).sort_values("cost_basis", ascending=False).reset_index(drop=True)
        total_cost = df["cost_basis"].sum()
        df["allocation_pct"] = (
            (df["cost_basis"] / total_cost * 100).round(2) if total_cost else 0.0
        )
    else:
        df = pd.DataFrame(columns=holdings_cols + ["allocation_pct"])

    realized_df = (
        pd.DataFrame(
            [{"ticker": k, "realized_pl": round(v, 4)} for k, v in realized.items() if abs(v) > 1e-9]
        )
        if realized
        else pd.DataFrame(columns=["ticker", "realized_pl"])
    )
    return df, realized_df


def analyze_realized(
    positions: pd.DataFrame, realized_from_trades: pd.DataFrame
) -> pd.DataFrame:
    # Prefer the broker's Closed Positions Profit/Loss when available.
    if not positions.empty:
        ticker_col = find_column(
            positions, ["ticker", "symbol", "instrument", "market"], required=False
        )
        pl_col = find_column(
            positions, ["profit_loss", "profitloss", "profit", "pnl", "result"],
            required=False,
        )
        if ticker_col and pl_col:
            return (
                positions.assign(_pl=parse_numeric(positions[pl_col]))
                .groupby(ticker_col)["_pl"]
                .sum()
                .reset_index()
                .rename(columns={ticker_col: "ticker", "_pl": "realized_pl"})
            )

    return realized_from_trades.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Cash flow analysis
# ---------------------------------------------------------------------------
def analyze_cash_flows(
    cash_ops: pd.DataFrame, trades: list[Trade]
) -> tuple[dict[str, float], float]:
    type_col = find_column(cash_ops, ["type", "operation"], required=False)
    amount_col = find_column(
        cash_ops, ["amount", "value", "net_amount", "cash", "change", "payment"],
        required=False,
    )
    comment_col = find_column(cash_ops, ["comment", "description", "details"], required=False)

    flows = {
        "deposits": 0.0,
        "withdrawals": 0.0,
        "interest": 0.0,
        "dividends": 0.0,
        "dividend_tax": 0.0,
        "currency_conversions": 0.0,
        "conversion_fees": 0.0,
        "estimated_embedded_fx_fees": 0.0,
        "invested": 0.0,
        "proceeds": 0.0,
        "fees": 0.0,
    }

    trade_ids = set()
    if comment_col:
        for _, row in cash_ops.iterrows():
            comment = str(row.get(comment_col, ""))
            if TRADE_COMMENT_RE.search(comment):
                trade_ids.add(row.name)

    if type_col and amount_col:
        for idx, row in cash_ops.iterrows():
            if idx in trade_ids:
                continue
            type_val = str(row.get(type_col, "")).strip()
            amount = float(parse_numeric(pd.Series([row[amount_col]])).iloc[0])
            text = f"{type_val} {row.get(comment_col, '')}".lower()

            if DIVIDEND_TAX_RE.search(text):
                flows["dividend_tax"] += amount
            elif "tax" in type_val.lower():
                flows["fees"] += abs(amount)
            elif DIVIDEND_RE.search(text):
                flows["dividends"] += amount
            elif INTEREST_RE.search(text):
                flows["interest"] += amount
            elif CONVERSION_FEE_RE.search(text):
                flows["conversion_fees"] += amount
            elif CURRENCY_CONVERSION_RE.search(text):
                flows["currency_conversions"] += amount
                flows["estimated_embedded_fx_fees"] += (
                    abs(amount) * DEFAULT_EMBEDDED_FX_FEE_RATE
                )
            elif WITHDRAW_RE.search(text):
                flows["withdrawals"] += abs(amount)
            elif DEPOSIT_RE.search(text):
                flows["deposits"] += abs(amount)

    # Trading cash impact from parsed trades (separates buys vs sells).
    for t in trades:
        if t.action == "open":
            if t.side == "buy":
                flows["invested"] += t.value
            else:
                flows["proceeds"] += t.value  # short sale proceeds
        else:  # close
            if t.side == "sell":
                flows["proceeds"] += t.value
            else:
                flows["invested"] += t.value  # buying to cover

    net_deposited = (
        flows["deposits"]
        + flows.get("currency_conversions", 0.0)
        - flows["withdrawals"]
    )
    ending_cash = (
        net_deposited
        + flows["interest"]
        + flows["dividends"]
        + flows["dividend_tax"]
        - flows["invested"]
        + flows["proceeds"]
        - flows["fees"]
        + flows["conversion_fees"]
    )
    return flows, ending_cash


def analyze_income(cash_ops: pd.DataFrame) -> tuple[float, float, pd.Series]:
    type_col = find_column(cash_ops, ["type", "operation"], required=False)
    amount_col = find_column(
        cash_ops, ["amount", "value", "net_amount", "cash", "change", "payment"],
        required=False,
    )
    date_col = find_column(
        cash_ops, ["time", "date", "operation_date", "booking_date", "transaction_date"],
        required=False,
    )
    comment_col = find_column(cash_ops, ["comment", "description", "details"], required=False)

    dividends = interest = 0.0
    monthly: dict[str, float] = {}

    if not (type_col and amount_col):
        return 0.0, 0.0, pd.Series(dtype=float)

    for _, row in cash_ops.iterrows():
        text = f"{row.get(type_col, '')} {row.get(comment_col, '') if comment_col else ''}".lower()
        amount = float(parse_numeric(pd.Series([row[amount_col]])).iloc[0])
        if DIVIDEND_RE.search(text):
            dividends += amount
            period = _period(row, date_col)
            if period:
                monthly[period] = monthly.get(period, 0.0) + amount
        elif INTEREST_RE.search(text):
            interest += amount
            period = _period(row, date_col)
            if period:
                monthly[period] = monthly.get(period, 0.0) + amount

    series = (
        pd.Series(monthly, name="income").sort_index() if monthly else pd.Series(dtype=float)
    )
    return dividends, interest, series


def _period(row: pd.Series, date_col: str | None) -> str | None:
    if not date_col:
        return None
    dt = pd.to_datetime(row.get(date_col), errors="coerce")
    if pd.isna(dt):
        return None
    return str(dt.to_period("M"))


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def analyze_open_positions(
    open_positions: pd.DataFrame,
    valued_holdings: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Live market value & unrealized P/L per ticker.

    Preference order:
    1. XTB 'Open Positions' sheet (broker live values) when present.
    2. Live-valued holdings (yfinance) when provided.
    Otherwise returns an empty frame so callers fall back to cost basis.
    """
    empty_cols = ["ticker", "current_value", "unrealized_pl"]

    if open_positions is not None and not open_positions.empty:
        ticker_col = find_column(
            open_positions, ["ticker", "symbol", "instrument", "market"], required=False
        )
        value_col = find_column(
            open_positions, ["current_value", "value", "market_value", "position_value"],
            required=False,
        )
        pl_col = find_column(
            open_positions, ["profit_loss", "profitloss", "profit", "pnl", "result", "unrealized"],
            required=False,
        )
        if ticker_col is not None and value_col is not None:
            df = open_positions.copy()
            df["_value"] = parse_numeric(df[value_col])
            df["_pl"] = parse_numeric(df[pl_col]) if pl_col else 0.0
            return (
                df.groupby(ticker_col)
                .agg(current_value=("_value", "sum"), unrealized_pl=("_pl", "sum"))
                .reset_index()
                .rename(columns={ticker_col: "ticker"})
                .sort_values("current_value", ascending=False)
                .reset_index(drop=True)
            )

    if valued_holdings is not None and not valued_holdings.empty:
        cols = {"ticker", "market_value", "unrealized_pl"}
        if cols.issubset(valued_holdings.columns):
            return (
                valued_holdings[["ticker", "market_value", "unrealized_pl"]]
                .rename(columns={"market_value": "current_value"})
                .sort_values("current_value", ascending=False)
                .reset_index(drop=True)
            )

    return pd.DataFrame(columns=empty_cols)


def compute_xirr(cash_flows: list[tuple[pd.Timestamp, float]]) -> float | None:
    """Return annualized IRR for dated cash flows, or None when unsolvable."""
    dated = [
        (pd.Timestamp(d).normalize(), float(v))
        for d, v in cash_flows
        if abs(float(v)) > 1e-9
    ]
    if not dated:
        return None
    if not any(v > 0 for _, v in dated) or not any(v < 0 for _, v in dated):
        return None
    dated.sort(key=lambda item: item[0])
    start = dated[0][0]
    if dated[-1][0] <= start:
        return None

    def npv(rate: float) -> float:
        total = 0.0
        for dt, amount in dated:
            years = (dt - start).days / 365.0
            total += amount / ((1.0 + rate) ** years)
        return total

    low = -0.9999
    high = 1.0
    low_val = npv(low)
    high_val = npv(high)
    while low_val * high_val > 0 and high < 1000.0:
        high *= 2.0
        high_val = npv(high)
    if low_val * high_val > 0:
        return None

    for _ in range(100):
        mid = (low + high) / 2.0
        mid_val = npv(mid)
        if abs(mid_val) < 1e-7:
            return mid
        if low_val * mid_val <= 0:
            high = mid
            high_val = mid_val
        else:
            low = mid
            low_val = mid_val
    return (low + high) / 2.0


def build_external_cash_flows(
    cash_ops: pd.DataFrame,
    terminal_value: float,
    terminal_date: date,
) -> list[tuple[pd.Timestamp, float]]:
    """Build investor-perspective external flows for money-weighted return."""
    type_col = find_column(cash_ops, ["type", "operation"], required=False)
    amount_col = find_column(
        cash_ops, ["amount", "value", "net_amount", "cash", "change", "payment"],
        required=False,
    )
    date_col = find_column(
        cash_ops, ["time", "date", "operation_date", "booking_date", "transaction_date"],
        required=False,
    )
    comment_col = find_column(cash_ops, ["comment", "description", "details"], required=False)
    if not (type_col and amount_col and date_col):
        return []

    flows: list[tuple[pd.Timestamp, float]] = []
    for _, row in cash_ops.iterrows():
        text = f"{row.get(type_col, '')} {row.get(comment_col, '') if comment_col else ''}".lower()
        dt = pd.to_datetime(row.get(date_col), errors="coerce")
        if pd.isna(dt):
            continue
        amount = float(parse_numeric(pd.Series([row[amount_col]])).iloc[0])
        if DEPOSIT_RE.search(text):
            flows.append((pd.Timestamp(dt).normalize(), -abs(amount)))
        elif WITHDRAW_RE.search(text):
            flows.append((pd.Timestamp(dt).normalize(), abs(amount)))
        elif CURRENCY_CONVERSION_RE.search(text):
            flows.append((pd.Timestamp(dt).normalize(), -amount))

    if terminal_value > 0:
        flows.append((pd.Timestamp(terminal_date).normalize(), float(terminal_value)))
    return sorted(flows, key=lambda item: item[0])


def compute_performance(
    holdings: pd.DataFrame,
    open_positions: pd.DataFrame,
    realized: pd.DataFrame,
    flows: dict[str, float],
    ending_cash: float,
    broker_total: float,
    cash_ops: pd.DataFrame | None = None,
    terminal_date: date | None = None,
) -> dict[str, float | None]:
    cost_basis = float(holdings["cost_basis"].sum()) if not holdings.empty else 0.0

    market_value = cost_basis
    unrealized_pl = 0.0
    if not open_positions.empty:
        market_value = float(open_positions["current_value"].sum())
        unrealized_pl = float(open_positions["unrealized_pl"].sum())

    realized_pl = float(realized["realized_pl"].sum()) if not realized.empty else 0.0
    income = flows["interest"] + flows["dividends"]

    portfolio_value = market_value + ending_cash
    net_deposited = (
        flows["deposits"]
        + flows.get("currency_conversions", 0.0)
        - flows["withdrawals"]
    )
    total_gain = unrealized_pl + realized_pl + income
    total_return_pct = (total_gain / net_deposited * 100) if net_deposited else 0.0
    income_yield_pct = (income / cost_basis * 100) if cost_basis else 0.0
    money_weighted_return_pct = None
    if cash_ops is not None and terminal_date is not None:
        external_flows = build_external_cash_flows(cash_ops, portfolio_value, terminal_date)
        xirr = compute_xirr(external_flows)
        money_weighted_return_pct = xirr * 100 if xirr is not None else None
    # XTB "Total" row = ending free cash, so reconcile cash (not portfolio value).
    diff = ending_cash - broker_total if broker_total else None

    return {
        "cost_basis": cost_basis,
        "market_value": market_value,
        "unrealized_pl": unrealized_pl,
        "realized_pl": realized_pl,
        "income": income,
        "total_gain": total_gain,
        "portfolio_value": portfolio_value,
        "ending_cash": ending_cash,
        "net_deposited": net_deposited,
        "total_return_pct": total_return_pct,
        "money_weighted_return_pct": money_weighted_return_pct,
        "income_yield_pct": income_yield_pct,
        "broker_total": broker_total,
        "reconciliation_diff": diff,
    }


def _holding_weights(holdings: pd.DataFrame) -> pd.Series:
    if holdings is None or holdings.empty:
        return pd.Series(dtype=float)
    if "weight_pct" in holdings.columns:
        return pd.to_numeric(holdings["weight_pct"], errors="coerce").fillna(0.0)
    if "market_value" not in holdings.columns:
        return pd.Series([0.0] * len(holdings), index=holdings.index)
    market_values = pd.to_numeric(holdings["market_value"], errors="coerce").fillna(0.0)
    total = float(market_values.sum())
    return market_values / total * 100 if total else market_values * 0.0


def analyze_concentration(holdings: pd.DataFrame, perf: dict[str, float]) -> dict[str, float | int | str]:
    """Summarize simple concentration and data-quality risk indicators."""
    weights = _holding_weights(holdings).sort_values(ascending=False)
    top_1 = float(weights.head(1).sum()) if not weights.empty else 0.0
    top_3 = float(weights.head(3).sum()) if not weights.empty else 0.0
    top_5 = float(weights.head(5).sum()) if not weights.empty else 0.0
    portfolio_value = float(perf.get("portfolio_value", 0.0) or 0.0)
    cash_weight = (
        float(perf.get("ending_cash", 0.0) or 0.0) / portfolio_value * 100
        if portfolio_value
        else 0.0
    )
    over_20 = int((weights > 20.0).sum()) if not weights.empty else 0
    cost_priced = 0
    if holdings is not None and not holdings.empty and "price_source" in holdings.columns:
        cost_priced = int((holdings["price_source"].astype(str) == "cost").sum())

    if top_1 >= 50.0:
        note = f"High concentration: top holding is {top_1:.2f}%."
    elif top_3 >= 75.0:
        note = f"Elevated concentration: top 3 holdings are {top_3:.2f}%."
    elif cost_priced:
        note = f"Data quality watch: {cost_priced} holding{'s' if cost_priced != 1 else ''} priced at cost."
    else:
        note = "No major concentration flags from position weights."

    return {
        "top_1_weight_pct": top_1,
        "top_3_weight_pct": top_3,
        "top_5_weight_pct": top_5,
        "cash_weight_pct": cash_weight,
        "positions_over_20_pct": over_20,
        "cost_priced_positions": cost_priced,
        "risk_note": note,
    }


def build_executive_summary(
    holdings: pd.DataFrame,
    realized: pd.DataFrame,
    flows: dict[str, float],
    perf: dict[str, float],
) -> list[tuple[str, str]]:
    """Return short reader-facing observations for the top of the HTML report."""
    del realized, flows  # Kept in the signature so callers pass the full analysis context.
    if holdings is None or holdings.empty:
        largest = "No open positions"
        winner = "None"
        loser = "None"
        cost_priced = 0
    else:
        weights = _holding_weights(holdings)
        largest_row = holdings.loc[weights.idxmax()]
        largest = f"{largest_row['ticker']} ({float(weights.loc[largest_row.name]):.2f}%)"

        unrealized = pd.to_numeric(holdings.get("unrealized_pl", 0.0), errors="coerce").fillna(0.0)
        winner_idx = unrealized.idxmax()
        loser_idx = unrealized.idxmin()
        winner_val = float(unrealized.loc[winner_idx])
        loser_val = float(unrealized.loc[loser_idx])
        winner = (
            f"{holdings.loc[winner_idx, 'ticker']} ({winner_val:+.2f})"
            if winner_val > 0
            else "None"
        )
        loser = (
            f"{holdings.loc[loser_idx, 'ticker']} ({loser_val:+.2f})"
            if loser_val < 0
            else "None"
        )
        cost_priced = (
            int((holdings["price_source"].astype(str) == "cost").sum())
            if "price_source" in holdings.columns
            else 0
        )

    portfolio_value = float(perf.get("portfolio_value", 0.0) or 0.0)
    cash_allocation = (
        float(perf.get("ending_cash", 0.0) or 0.0) / portfolio_value * 100
        if portfolio_value
        else 0.0
    )
    diff = perf.get("reconciliation_diff")
    if diff is None:
        recon = "Skipped"
    else:
        recon = "OK" if abs(float(diff)) < 0.01 else "CHECK"
    pricing = (
        "No cost-pricing fallbacks"
        if cost_priced == 0
        else f"{cost_priced} holding{'s' if cost_priced != 1 else ''} priced at cost"
    )

    return [
        ("Largest holding", largest),
        ("Top unrealized winner", winner),
        ("Top unrealized loser", loser),
        ("Cash allocation", f"{cash_allocation:.2f}%"),
        ("Pricing warnings", pricing),
        ("Reconciliation", recon),
    ]


def analyze_income_quality(
    flows: dict[str, float],
    perf: dict[str, float],
) -> dict[str, float | str]:
    """Summarize income, withholding/tax drag, and income yield on cost."""
    dividends = float(flows.get("dividends", 0.0) or 0.0)
    interest = float(flows.get("interest", 0.0) or 0.0)
    dividend_tax = abs(float(flows.get("dividend_tax", 0.0) or 0.0))
    gross_income = dividends + interest
    net_income = gross_income - dividend_tax
    tax_drag_pct = dividend_tax / gross_income * 100 if gross_income else 0.0
    cost_basis = float(perf.get("cost_basis", 0.0) or 0.0)
    net_income_yield_pct = net_income / cost_basis * 100 if cost_basis else 0.0
    if gross_income:
        dividend_mix = dividends / gross_income * 100
        interest_mix = interest / gross_income * 100
        income_mix = f"{dividend_mix:.2f}% dividends / {interest_mix:.2f}% interest"
    else:
        income_mix = "No income"
    return {
        "gross_income": gross_income,
        "dividend_tax": dividend_tax,
        "net_income": net_income,
        "tax_drag_pct": tax_drag_pct,
        "net_income_yield_pct": net_income_yield_pct,
        "income_mix": income_mix,
    }


def analyze_methodology_quality(
    holdings: pd.DataFrame,
    perf: dict[str, float],
) -> list[tuple[str, str]]:
    """Return report-method and data-quality notes for the HTML summary."""
    live_count = cost_count = 0
    fallback_tickers = []
    if holdings is not None and not holdings.empty and "price_source" in holdings.columns:
        src = holdings["price_source"].astype(str)
        live_count = int((src == "live").sum())
        cost_count = int((src == "cost").sum())
        if cost_count and "ticker" in holdings.columns:
            fallback_tickers = holdings.loc[src == "cost", "ticker"].astype(str).tolist()

    diff = perf.get("reconciliation_diff")
    if diff is None:
        recon = "Skipped"
    else:
        recon = "OK" if abs(float(diff)) < 0.01 else "CHECK"

    fallback_label = ", ".join(fallback_tickers) if fallback_tickers else "None"
    fallback_word = "fallback" if cost_count == 1 else "fallbacks"
    return [
        ("Pricing coverage", f"{live_count} live / {cost_count} cost {fallback_word}"),
        ("Cost fallback tickers", fallback_label),
        ("Cash reconciliation", recon),
        ("Realized P/L method", "Broker closed positions preferred; FIFO fallback"),
        ("Money-weighted return", "External deposits/withdrawals plus terminal portfolio value"),
        ("Valuation caveat", "Cost fallback positions carry zero unrealized P/L"),
    ]


def beginner_guide_rows() -> list[tuple[str, str]]:
    """Plain-language explanations for readers new to investing terms."""
    return [
        (
            "Market value",
            "Think of market value as today's estimated selling value. It is what the position appears to be worth now, not what you originally paid.",
        ),
        (
            "Unrealized profit",
            "Unrealized profit is only a paper gain until you sell. The price can still move up or down before that gain becomes real cash.",
        ),
        (
            "Realized profit",
            "Realized profit is the gain or loss after a position was sold. It is already locked in by a completed sale.",
        ),
        (
            "Money-weighted return",
            "Money-weighted return is useful when you added money at different times. It gives more weight to money that was invested for longer.",
        ),
        (
            "Cost fallback",
            "A cost fallback means the report could not find a trusted live price, so it uses what you paid. Treat those values as conservative placeholders, not confirmed market prices.",
        ),
        (
            "Concentration",
            "Concentration tells you whether too much of the portfolio depends on only a few holdings. A high number is not automatically bad, but it means those holdings matter more.",
        ),
    ]


def analyze_return_contributions(
    holdings: pd.DataFrame,
    realized: pd.DataFrame,
    perf: dict[str, float],
) -> pd.DataFrame:
    """Return ticker-level realized + unrealized contribution to total gain."""
    rows: dict[str, dict[str, float | str]] = {}
    if holdings is not None and not holdings.empty:
        for _, row in holdings.iterrows():
            ticker = str(row.get("ticker", "")).strip()
            if not ticker:
                continue
            rows[ticker] = {
                "Ticker": ticker,
                "Market Value": float(row.get("market_value", 0.0) or 0.0),
                "Unrealized P/L": float(row.get("unrealized_pl", 0.0) or 0.0),
                "Realized P/L": 0.0,
            }

    if realized is not None and not realized.empty and {"ticker", "realized_pl"}.issubset(realized.columns):
        grouped = realized.groupby("ticker")["realized_pl"].sum()
        for ticker, realized_pl in grouped.items():
            key = str(ticker)
            rows.setdefault(
                key,
                {
                    "Ticker": key,
                    "Market Value": 0.0,
                    "Unrealized P/L": 0.0,
                    "Realized P/L": 0.0,
                },
            )
            rows[key]["Realized P/L"] = float(realized_pl)

    if not rows:
        return pd.DataFrame(
            columns=[
                "Ticker", "Market Value", "Unrealized P/L",
                "Realized P/L", "Total Contribution", "Contribution %",
            ]
        )

    total_gain = float(perf.get("total_gain", 0.0) or 0.0)
    out = pd.DataFrame(rows.values())
    out["Total Contribution"] = out["Unrealized P/L"] + out["Realized P/L"]
    out["Contribution %"] = (
        out["Total Contribution"] / total_gain * 100 if abs(total_gain) > 1e-9 else 0.0
    )
    return out.sort_values("Total Contribution", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Portfolio evolution (cost vs realized + unrealized value over time)
# ---------------------------------------------------------------------------
def _replay_trade(
    lots: dict[str, list[tuple[float, float]]],
    realized: dict[str, float],
    trade: Trade,
) -> None:
    """Apply one trade to the open-lots state (mutates lots + realized)."""
    bucket = lots.setdefault(trade.ticker, [])
    if trade.action == "open":
        bucket.append((trade.shares, trade.price) if trade.side == "buy"
                      else (-trade.shares, trade.price))
        return
    # close
    to_close = trade.shares
    close_value = trade.value
    cost_consumed = 0.0
    while to_close > 1e-9 and bucket:
        lot_shares, lot_price = bucket[0]
        if abs(lot_shares) < 1e-9:
            bucket.pop(0)
            continue
        magnitude = min(abs(lot_shares), to_close)
        cost_consumed += magnitude * lot_price
        remaining = abs(lot_shares) - magnitude
        sign = 1 if lot_shares >= 0 else -1
        if remaining > 1e-9:
            bucket[0] = (sign * remaining, lot_price)
        else:
            bucket.pop(0)
        to_close -= magnitude
    realized[trade.ticker] = realized.get(trade.ticker, 0.0) + (close_value - cost_consumed)


def build_evolution_series(
    trades: list[Trade],
    price_history: dict[str, pd.Series | None],
    end_date: date,
) -> pd.DataFrame:
    """Replay trades daily and compute cost / market value / realized P/L series.

    Returns a DataFrame indexed by date with columns:
      ``cost`` (open cost basis), ``market_value`` (open lots at historical
      close, falling back to cost when no live series), ``realized_pl``
      (cumulative), ``total_value`` (market_value + realized_pl).

    The gap between ``cost`` and ``total_value`` is the total investment gain /
    loss. Tickers without a live price series contribute their open cost basis
    as market value (i.e. zero unrealized P/L), matching the holdings table.
    """
    empty = pd.DataFrame(
        columns=["cost", "market_value", "realized_pl", "total_value"]
    )
    dated = [t for t in trades if t.date is not None]
    if not dated:
        return empty

    sorted_trades = sorted(dated, key=lambda t: t.date)
    start_date = pd.Timestamp(sorted_trades[0].date).normalize()
    end_ts = pd.Timestamp(end_date)
    if end_ts < start_date:
        end_ts = start_date
    dates = pd.date_range(start=start_date, end=end_ts, freq="D")

    lots: dict[str, list[tuple[float, float]]] = {}
    realized: dict[str, float] = {}
    trade_idx = 0
    n = len(sorted_trades)
    rows = []
    for d in dates:
        while trade_idx < n and pd.Timestamp(sorted_trades[trade_idx].date).normalize() <= d:
            _replay_trade(lots, realized, sorted_trades[trade_idx])
            trade_idx += 1
        cost = 0.0
        market_value = 0.0
        for ticker, bucket in lots.items():
            series = price_history.get(ticker)
            for shares, lot_price in bucket:
                lot_cost = abs(shares) * lot_price
                cost += lot_cost
                if series is not None and len(series):
                    close = series.asof(d)
                    if close is not None and not pd.isna(close):
                        market_value += shares * float(close)
                    else:
                        market_value += lot_cost
                else:
                    market_value += lot_cost
        realized_total = sum(realized.values())
        rows.append({
            "cost": round(cost, 4),
            "market_value": round(market_value, 4),
            "realized_pl": round(realized_total, 4),
            "total_value": round(market_value + realized_total, 4),
        })
    df = pd.DataFrame(rows, index=dates)
    df.index.name = "date"
    return df


def print_report(
    currency: str,
    meta: dict[str, str],
    flows: dict[str, float],
    ending_cash: float,
    holdings: pd.DataFrame,
    open_positions: pd.DataFrame,
    realized: pd.DataFrame,
    perf: dict[str, float],
    dividends: float,
    interest: float,
    as_of: date | None = None,
    cost_fallback_tickers: list[str] | None = None,
) -> None:
    cost_fallback_tickers = cost_fallback_tickers or []
    print(f"\nPORTFOLIO REVIEW — XTB account {meta.get('account', '?')}")
    print("=" * 80)
    print(
        f"Period: {meta.get('period_from', '?')}  →  {meta.get('period_to', '?')}    "
        f"({currency})"
    )
    val_date = as_of.isoformat() if as_of else meta.get("period_to", "?")
    print(f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}    Valuation date: {val_date}")

    print("\nCASH FLOWS")
    print("-" * 80)
    print(f"  Deposits:              {money(flows['deposits']):>14}")
    print(f"  Withdrawals:          {money(-flows['withdrawals']):>14}")
    print(f"  Free-funds interest:   {money(flows['interest']):>14}")
    print(f"  Dividends received:    {money(flows['dividends']):>14}")
    print(f"  Dividend tax:         {money(flows['dividend_tax']):>14}")
    print(f"  Currency conversions: {money(flows.get('currency_conversions', 0.0)):>14}")
    print(
        "  Est. embedded FX fee: "
        f"{money(-flows.get('estimated_embedded_fx_fees', 0.0)):>14}"
    )
    print(f"  Invested (buys):      {money(-flows['invested']):>14}")
    print(f"  Proceeds (sales):      {money(flows['proceeds']):>14}")
    print(f"  FX conversion fees:   {money(flows['conversion_fees']):>14}")
    print(f"  Fees / commissions:   {money(-flows['fees']):>14}")
    print(f"  Ending cash balance:   {money(ending_cash):>14}")

    print("\nHOLDINGS (live market value)")
    print("-" * 80)
    if holdings.empty or holdings["market_value"].sum() == 0:
        print("  No open positions.")
    else:
        view = holdings[["ticker", "name", "shares", "last_price",
                         "market_value", "unrealized_pl", "return_pct",
                         "weight_pct", "price_source"]].copy()
        view.columns = ["Ticker", "Name", "Shares", "Last Price",
                        "Market Value", "Unrealized P/L", "Return %",
                        "Weight %", "Src"]
        print(view.to_string(index=False))
        print(f"\n  Total cost basis:      {money(perf['cost_basis']):>14}")
        print(f"  Total market value:    {money(perf['market_value']):>14}")
        if cost_fallback_tickers:
            print(f"  (priced at cost: {', '.join(cost_fallback_tickers)})")
            for tk in cost_fallback_tickers:
                if tk in COST_FALLBACK_NOTES:
                    print(f"    · {tk}: {COST_FALLBACK_NOTES[tk]}")

    print("\nOPEN POSITIONS (market value)")
    print("-" * 80)
    if open_positions is None or open_positions.empty:
        print("  No open positions.")
    else:
        view = open_positions.copy()
        view["weight_pct"] = (
            view["current_value"] / view["current_value"].sum() * 100
        ).round(2)
        view.columns = ["Ticker", "Market Value", "Unrealized P/L", "Weight %"]
        print(view.to_string(index=False))
        print(
            f"\n  Total market value:    {money(perf['market_value']):>14}"
            f"   Unrealized P/L: {money(perf['unrealized_pl']):>12}"
        )

    print("\nREALIZED P/L (closed positions)")
    print("-" * 80)
    if realized.empty or (realized["realized_pl"].abs().sum() == 0):
        print("  No realized gains/losses in this period.")
    else:
        print(realized.to_string(index=False))
        print(f"\n  Total realized P/L:    {money(perf['realized_pl']):>14}")

    print("\nPERFORMANCE")
    print("-" * 80)
    print(f"  Portfolio value:       {money(perf['portfolio_value']):>14}")
    print(f"    of which market val: {money(perf['market_value']):>14}")
    print(f"    of which cash:       {money(perf['ending_cash']):>14}")
    print(f"    of which cost basis: {money(perf['cost_basis']):>14}")
    print(f"  Net deposited:         {money(perf['net_deposited']):>14}")
    print(f"  Unrealized P/L:        {money(perf['unrealized_pl']):>14}")
    print(f"  Realized P/L:          {money(perf['realized_pl']):>14}")
    print(f"  Income (int. + div.):  {money(perf['income']):>14}")
    print(f"  Total gain:            {money(perf['total_gain']):>14}")
    print(f"  Total return:          {perf['total_return_pct']:>13.2f}%")
    if perf.get("money_weighted_return_pct") is not None:
        print(f"  Money-weighted return: {perf['money_weighted_return_pct']:>13.2f}%")
    else:
        print(f"  Money-weighted return: {'n/a':>14}")
    print(f"  Income yield (on cost):{perf['income_yield_pct']:>12.2f}%")

    print("\nRECONCILIATION")
    print("-" * 80)
    if perf["broker_total"]:
        diff = perf["reconciliation_diff"]
        status = "OK" if abs(diff) < 0.01 else "CHECK"
        print(
            f"  Computed ending cash:      {money(perf['ending_cash']):>10}\n"
            f"  Broker 'Total' (cash):     {money(perf['broker_total']):>10}\n"
            f"  Difference:               {money(diff):>10}   [{status}]"
        )
    else:
        print("  Broker 'Total' row not found — reconciliation skipped.")
    print()


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------
TERM_TOOLTIPS = {
    "Ticker": "A ticker is the short code used by markets and brokers to identify an investment, like a label on an exchange.",
    "ticker": "A ticker is the short code used by markets and brokers to identify an investment, like a label on an exchange.",
    "Name": "The longer human-readable name of the investment.",
    "Shares": "How many units of the investment you currently hold.",
    "Last Price": "The latest price used for one share or unit. If no trusted live price exists, this may be the average cost.",
    "Src": "Source of the price: live means fetched from market data; cost means the report used what you paid.",
    "Portfolio value": "What your portfolio is worth after including market value and cash.",
    "Market Value": "Today's estimated value for a holding. If the report cannot find a trusted price, it uses your original cost instead.",
    "market_value": "Market value is today's estimated value for a holding. If no trusted price is found, the report may use cost instead.",
    "Net deposited": "Total money added to the account, including converted cash credited into this account, minus withdrawals.",
    "Deposits": "Money you added to the brokerage account.",
    "Withdrawals": "Money you took out of the brokerage account.",
    "Free-funds interest": "Small interest paid by the broker on cash that was not invested.",
    "Dividends received": "Cash paid by investments, usually from company profits or fund distributions.",
    "Invested (buys)": "Money spent buying investments. It reduces cash but increases holdings.",
    "Proceeds (sales)": "Money received from selling investments. It increases cash.",
    "Currency conversions": "Cash credited to or debited from this account after converting another currency. This is funding principal, not a fee.",
    "FX conversion fees": "Explicit broker costs for currency conversion, when XTB exports them separately from the converted principal.",
    "Estimated embedded FX cost": "Estimated currency-conversion cost using the default 0.5% XTB rate. It is informational only because this EUR export does not contain a separate fee row.",
    "Fees / commissions": "Broker or transaction costs paid for account activity.",
    "Ending cash balance": "Cash left in the account after all deposits, withdrawals, trades, income, and fees.",
    "Total gain": "Unrealized gains plus realized gains plus income.",
    "Total return": "Total gain divided by net deposited. Simple return, not adjusted for deposit timing.",
    "Money-weighted return": "This answers: how did my money do, considering the dates I added or withdrew cash? It is useful when deposits happened at different times.",
    "Income yield (on cost)": "Income divided by the cost basis of current holdings.",
    "Cost basis": "The amount paid for the open position before any current market gain or loss.",
    "cost basis": "The amount paid for the open position before any current market gain or loss.",
    "Unrealized P/L": "Profit or loss on positions you still hold. It is not locked in until you sell.",
    "unrealized_pl": "unrealized_pl means unrealized profit or loss: the gain or loss on positions you still hold.",
    "Realized P/L": "Profit or loss from positions that were sold or closed.",
    "realized_pl": "realized_pl means realized profit or loss: the gain or loss already locked in by selling or closing a position.",
    "Return %": "Unrealized profit or loss divided by cost basis.",
    "Weight %": "The holding's share of total current market value.",
    "Cash allocation": "The share of the portfolio currently held as cash.",
    "Pricing warnings": "Positions that could not be priced from a trusted live source.",
    "Pricing coverage": "How many holdings use live prices versus cost fallback values.",
    "Cost fallback tickers": "Tickers valued at cost because a trusted live price was unavailable. Their real market value may be higher or lower.",
    "Cost fallback positions": "Positions valued at cost because a trusted live price was unavailable. Their real market value may be higher or lower.",
    "Reconciliation": "A check that the report's cash math matches the final cash amount shown by XTB.",
    "Cash reconciliation": "A check that the report's cash math matches the final cash amount shown by XTB.",
    "Computed ending cash": "The cash balance calculated by this report from deposits, withdrawals, trades, dividends, fees, and taxes.",
    "Broker 'Total' (cash)": "The final cash amount shown by XTB. This is cash left in the account, not the value of your stocks or ETFs.",
    "Difference": "Computed cash minus XTB cash. A value close to zero means the cash movements were read correctly.",
    "Status": "Shows whether the cash check passed or whether the numbers need attention.",
    "Gross income": "Dividends plus interest before dividend tax.",
    "Dividend tax": "Tax withheld from dividend payments.",
    "Net income": "Income remaining after dividend tax.",
    "Tax drag": "Dividend tax as a share of gross income.",
    "Net income yield": "Net income divided by the cost basis of current holdings.",
    "Income mix": "How much income came from dividends versus interest.",
    "Top 1 holding weight": "The largest single holding's share of current market value.",
    "Top 3 holdings weight": "The three largest holdings' combined share of current market value.",
    "Top 5 holdings weight": "The five largest holdings' combined share of current market value.",
    "Positions above 20%": "Number of holdings that each exceed 20% of current market value.",
    "Return Contribution": "How much each ticker contributed to total gain.",
    "Total Contribution": "Realized plus unrealized profit or loss for the ticker.",
    "Contribution %": "The ticker's contribution as a share of total gain.",
    "FIFO": "First in, first out: older purchase lots are treated as sold first.",
    "XIRR": "Annualized money-weighted return for cash flows on different dates.",
}

_TERM_TOOLTIP_SEQ = 0
_TERM_TOOLTIP_NOTES: list[tuple[int, str, str]] = []
_TERM_TOOLTIP_NOTE_INDEX: dict[str, int] = {}


def _reset_term_tooltips() -> None:
    global _TERM_TOOLTIP_SEQ
    _TERM_TOOLTIP_SEQ = 0
    _TERM_TOOLTIP_NOTES.clear()
    _TERM_TOOLTIP_NOTE_INDEX.clear()


def _term_note_number(text: str, help_text: str) -> int:
    key = text.strip()
    note_num = _TERM_TOOLTIP_NOTE_INDEX.get(key)
    if note_num is None:
        note_num = len(_TERM_TOOLTIP_NOTES) + 1
        _TERM_TOOLTIP_NOTE_INDEX[key] = note_num
        _TERM_TOOLTIP_NOTES.append((note_num, text, help_text))
    return note_num


def _tooltip_notes_html() -> str:
    if not _TERM_TOOLTIP_NOTES:
        return ""
    items = "".join(
        f"<li><strong>{escape(text)}</strong>: {escape(help_text)}</li>"
        for _, text, help_text in _TERM_TOOLTIP_NOTES
    )
    return (
        "<section class='tooltip-notes' aria-label='Tooltip notes'>"
        "<h2>Tooltip Notes</h2>"
        f"<ol>{items}</ol>"
        "</section>"
    )


def _label_html(label: str) -> str:
    global _TERM_TOOLTIP_SEQ
    text = str(label)
    help_text = TERM_TOOLTIPS.get(text.strip())
    if not help_text:
        return escape(text)
    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-") or "term"
    _TERM_TOOLTIP_SEQ += 1
    tip_id = f"term-tip-{slug}-{_TERM_TOOLTIP_SEQ}"
    note_num = _term_note_number(text, help_text)
    return (
        f"<span class='term-help' tabindex='0' "
        f"aria-describedby='{escape(tip_id)}' data-term-help='1'>"
        f"<span class='term-label'>{escape(text)}</span>"
        f"<span class='term-icon' aria-hidden='true'>?</span>"
        f"<span class='term-note-ref' aria-hidden='true'>[{note_num}]</span>"
        f"<span class='term-tip' id='{escape(tip_id)}' role='tooltip'>"
        f"{escape(help_text)}</span></span>"
    )


def _kv_table(rows: list[tuple[str, str]]) -> str:
    out = ["<table class='kv'>"]
    for label, value in rows:
        cls = " class='neg'" if value.strip().startswith("-") else ""
        out.append(f"<tr><th>{_label_html(label)}</th><td{cls}>{escape(value)}</td></tr>")
    out.append("</table>")
    return "\n".join(out)


def _df_to_html(
    df: pd.DataFrame,
    formats: dict[str, str] | None = None,
    colored_cols: set[str] | None = None,
) -> str:
    """Render a DataFrame to an HTML table.

    ``colored_cols`` (column labels) get ``pos``/``neg`` cell classes based on
    the cell's sign (green for >= 0, red for < 0) so P/L-style columns can be
    highlighted independently of other numeric columns.
    """
    formats = formats or {}
    colored_cols = colored_cols or set()
    if df.empty:
        return "<p class='muted'>No data.</p>"
    header = "".join(
        f"<th data-sortable='1' tabindex='0' aria-sort='none'>{_label_html(str(c))}</th>"
        for c in df.columns
    )
    body = []
    for _, row in df.iterrows():
        cells = []
        for col in df.columns:
            val = row[col]
            spec = formats.get(col)
            text = f"{val:{spec}}" if spec else (
                f"{val:,.2f}" if isinstance(val, float) else str(val)
            )
            cls = ""
            if col in colored_cols and isinstance(val, (int, float)):
                cls = " class='pos'" if val >= 0 else " class='neg'"
            elif isinstance(val, (int, float)) and val < 0:
                cls = " class='neg'"
            cells.append(f"<td{cls}>{escape(text)}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return (
        "<table class='data-table'><thead><tr>"
        f"{header}</tr></thead><tbody>{''.join(body)}</tbody></table>"
    )


SORTABLE_TABLES_SCRIPT = r"""
function _bootSortableTables() {
  function cellValue(row, index) {
    return (row.children[index] && row.children[index].textContent || '').trim();
  }
  function numericValue(text) {
    var normalized = text.replace(/[%\s,]/g, '');
    if (normalized === '') { return null; }
    var value = Number(normalized);
    return Number.isFinite(value) ? value : null;
  }
  function sortTable(table, index, direction) {
    var tbody = table.tBodies[0];
    if (!tbody) { return; }
    var rows = Array.prototype.slice.call(tbody.rows);
    rows.sort(function (a, b) {
      var av = cellValue(a, index);
      var bv = cellValue(b, index);
      var an = numericValue(av);
      var bn = numericValue(bv);
      var result;
      if (an !== null && bn !== null) {
        result = an - bn;
      } else {
        result = av.localeCompare(bv, undefined, {numeric: true, sensitivity: 'base'});
      }
      return direction === 'asc' ? result : -result;
    });
    rows.forEach(function (row) { tbody.appendChild(row); });
  }
  document.querySelectorAll('table.data-table th[data-sortable="1"]').forEach(function (th) {
    function activate() {
      var table = th.closest('table');
      var current = th.getAttribute('aria-sort') || 'none';
      var next = current === 'ascending' ? 'desc' : 'asc';
      table.querySelectorAll('th[aria-sort]').forEach(function (other) {
        other.setAttribute('aria-sort', 'none');
      });
      th.setAttribute('aria-sort', next === 'asc' ? 'ascending' : 'descending');
      sortTable(table, th.cellIndex, next);
    }
    th.addEventListener('click', activate);
    th.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        activate();
      }
    });
  });
}
if (document.readyState !== 'loading') { _bootSortableTables(); }
else { document.addEventListener('DOMContentLoaded', _bootSortableTables); }
"""


TABLE_FILTERS_SCRIPT = r"""
function _bootTableFilters() {
  document.querySelectorAll('table.data-table').forEach(function (table, index) {
    if (table.dataset.filterReady === '1') { return; }
    table.dataset.filterReady = '1';
    var input = document.createElement('input');
    input.className = 'table-filter';
    input.type = 'search';
    input.placeholder = 'Filter table';
    input.setAttribute('aria-label', 'Filter table');
    input.setAttribute('data-table-filter', String(index));
    table.parentNode.insertBefore(input, table);
    input.addEventListener('input', function () {
      var body = table.tBodies[0];
      if (!body) { return; }
      var query = input.value.trim().toLowerCase();
      Array.prototype.forEach.call(body.rows, function (row) {
        var text = row.textContent.toLowerCase();
        row.style.display = !query || text.indexOf(query) !== -1 ? '' : 'none';
      });
    });
  });
}
if (document.readyState !== 'loading') { _bootTableFilters(); }
else { document.addEventListener('DOMContentLoaded', _bootTableFilters); }
"""


def build_html_report(
    currency: str,
    meta: dict[str, str],
    flows: dict[str, float],
    ending_cash: float,
    holdings: pd.DataFrame,
    open_positions: pd.DataFrame,
    realized: pd.DataFrame,
    perf: dict[str, float],
    evolution_cfg: dict | None,
    review_cfg: dict,
    as_of: date | None = None,
    cost_fallback_tickers: list[str] | None = None,
) -> str:
    _reset_term_tooltips()
    cost_fallback_tickers = cost_fallback_tickers or []
    diff = perf["reconciliation_diff"]
    recon_status = "OK" if (diff is None or abs(diff) < 0.01) else "CHECK"

    has_open = not (open_positions is None or open_positions.empty)
    has_realized = not (realized.empty or realized["realized_pl"].abs().sum() == 0)
    val_date = as_of.isoformat() if as_of else meta.get("period_to", "")

    flows_rows = [
        ("Deposits", money(flows["deposits"])),
        ("Withdrawals", money(-flows["withdrawals"])),
        ("Free-funds interest", money(flows["interest"])),
        ("Dividends received", money(flows["dividends"])),
        ("Dividend tax", money(flows["dividend_tax"])),
        ("Currency conversions", money(flows.get("currency_conversions", 0.0))),
        (
            "Estimated embedded FX cost",
            money(-flows.get("estimated_embedded_fx_fees", 0.0)),
        ),
        ("Invested (buys)", money(-flows["invested"])),
        ("Proceeds (sales)", money(flows["proceeds"])),
        ("FX conversion fees", money(flows["conversion_fees"])),
        ("Fees / commissions", money(-flows["fees"])),
        ("Ending cash balance", money(ending_cash)),
    ]
    perf_rows = [
        ("Portfolio value", f"{money(perf['portfolio_value'])} {currency}"),
        ("  of which market value", money(perf["market_value"])),
        ("  of which cash", money(perf["ending_cash"])),
        ("  cost basis", money(perf["cost_basis"])),
        ("Net deposited", money(perf["net_deposited"])),
        ("Unrealized P/L", money(perf["unrealized_pl"])),
        ("Realized P/L", money(perf["realized_pl"])),
        ("Income (int. + div.)", money(perf["income"])),
        ("Total gain", money(perf["total_gain"])),
        ("Total return", f"{perf['total_return_pct']:.2f} %"),
        (
            "Money-weighted return",
            (
                f"{perf['money_weighted_return_pct']:+.2f} %"
                if perf.get("money_weighted_return_pct") is not None
                else "n/a"
            ),
        ),
        ("Income yield (on cost)", f"{perf['income_yield_pct']:.2f} %"),
    ]
    recon_rows = (
        [
            ("Computed ending cash", money(perf["ending_cash"])),
            ("Broker 'Total' (cash)", money(perf["broker_total"])),
            ("Difference", money(diff)),
            ("Status", recon_status),
        ]
        if perf["broker_total"]
        else [("Status", "Broker 'Total' not found")]
    )

    holdings_cols = ["ticker", "name", "shares", "last_price", "market_value",
                     "unrealized_pl", "return_pct", "weight_pct", "price_source"]
    holdings_rename = {
        "ticker": "Ticker", "name": "Name", "shares": "Shares",
        "last_price": "Last Price", "market_value": "Market Value",
        "unrealized_pl": "Unrealized P/L", "return_pct": "Return %",
        "weight_pct": "Weight %", "price_source": "Src",
    }
    if not holdings.empty and set(holdings_cols).issubset(holdings.columns):
        holdings_view = holdings[holdings_cols].rename(columns=holdings_rename)
    else:
        holdings_view = pd.DataFrame(columns=list(holdings_rename.values()))

    if has_open:
        total_val = float(open_positions["current_value"].sum()) or 1.0
        op_view = open_positions.assign(
            weight_pct=open_positions["current_value"] / total_val * 100
        ).rename(columns={
            "ticker": "Ticker", "current_value": "Market Value",
            "unrealized_pl": "Unrealized P/L", "weight_pct": "Weight %",
        })
        op_html = _df_to_html(op_view, {"Market Value": ".2f", "Unrealized P/L": ".2f", "Weight %": ".2f"})
    else:
        op_view = pd.DataFrame(columns=["Ticker", "Market Value", "Unrealized P/L", "Weight %"])
        op_html = '<p class="muted">No open positions.</p>'

    realized_html = (
        _df_to_html(realized, {"realized_pl": ".2f"})
        if has_realized
        else '<p class="muted">No realized gains/losses in this period.</p>'
    )

    summary_rows = build_executive_summary(holdings, realized, flows, perf)
    concentration = analyze_concentration(holdings, perf)
    concentration_rows = [
        ("Top 1 holding weight", f"{concentration['top_1_weight_pct']:.2f} %"),
        ("Top 3 holdings weight", f"{concentration['top_3_weight_pct']:.2f} %"),
        ("Top 5 holdings weight", f"{concentration['top_5_weight_pct']:.2f} %"),
        ("Cash allocation", f"{concentration['cash_weight_pct']:.2f} %"),
        ("Positions above 20%", str(concentration["positions_over_20_pct"])),
        ("Priced at cost", str(concentration["cost_priced_positions"])),
        ("Risk note", str(concentration["risk_note"])),
    ]
    income_quality = analyze_income_quality(flows, perf)
    dividend_tax_display = (
        0.0
        if abs(float(income_quality["dividend_tax"])) < 0.005
        else -float(income_quality["dividend_tax"])
    )
    income_quality_rows = [
        ("Gross income", money(float(income_quality["gross_income"]))),
        ("Dividend tax", money(dividend_tax_display)),
        ("Net income", money(float(income_quality["net_income"]))),
        ("Tax drag", f"{income_quality['tax_drag_pct']:.2f} %"),
        ("Net income yield", f"{income_quality['net_income_yield_pct']:.2f} %"),
        ("Income mix", str(income_quality["income_mix"])),
    ]
    methodology_rows = analyze_methodology_quality(holdings, perf)
    guide_rows = beginner_guide_rows()
    contributions = analyze_return_contributions(holdings, realized, perf)
    contribution_html = _df_to_html(
        contributions,
        {
            "Market Value": ".2f",
            "Unrealized P/L": ".2f",
            "Realized P/L": ".2f",
            "Total Contribution": ".2f",
            "Contribution %": ".2f",
        },
        colored_cols={"Unrealized P/L", "Realized P/L", "Total Contribution", "Contribution %"},
    )

    charts_block = html_charts.render_charts_block(
        evolution_cfg, review_cfg, currency)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Portfolio Review — {escape(meta.get('account', ''))}</title>
<style>
  :root {{
    --bg:#f5f6f8; --card:#fff; --ink:#1f2933; --muted:#6b7280;
    --pos:#1f9d55; --neg:#e3342f; --line:#e5e7eb; --accent:#2c5282;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
         font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }}
  .wrap {{ max-width:1080px; margin:0 auto; padding:32px 20px 64px; }}
  header.hero {{ background:linear-gradient(135deg,#2c5282,#2b6cb0); color:#fff;
                border-radius:14px; padding:28px 32px; margin-bottom:24px;
                box-shadow:0 8px 24px rgba(0,0,0,.08); }}
  header.hero h1 {{ margin:0 0 6px; font-size:26px; }}
  header.hero .sub {{ opacity:.9; font-size:14px; }}
  .grid {{ display:grid; gap:20px; grid-template-columns:repeat(2,1fr); }}
  @media (max-width:820px) {{ .grid {{ grid-template-columns:1fr; }} }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:12px;
           padding:20px 22px; box-shadow:0 1px 3px rgba(0,0,0,.04); }}
  .card.full {{ grid-column:1 / -1; }}
  h2 {{ margin:0 0 14px; font-size:16px; text-transform:uppercase; letter-spacing:.04em;
        color:var(--accent); }}
  table {{ border-collapse:collapse; width:100%; font-size:14px; }}
  th, td {{ padding:8px 10px; text-align:right; }}
  thead th {{ background:#f0f4f8; font-weight:600; color:#334155; }}
  .data-table th[data-sortable='1'] {{ cursor:pointer; user-select:none; }}
  .data-table th[aria-sort='ascending']::after {{ content:' ▲'; color:var(--muted); }}
  .data-table th[aria-sort='descending']::after {{ content:' ▼'; color:var(--muted); }}
  .table-filter {{ width:100%; max-width:280px; margin:0 0 10px; padding:7px 9px;
                   border:1px solid var(--line); border-radius:7px; font:inherit; }}
  .table-filter:focus {{ outline:2px solid #bfdbfe; border-color:#60a5fa; }}
  tbody tr:nth-child(even) {{ background:#fafbfc; }}
  td:first-child, th:first-child, .kv th {{ text-align:left; }}
  .kv th {{ width:62%; color:var(--muted); font-weight:500; }}
  .kv td {{ font-variant-numeric:tabular-nums; font-weight:600; }}
  td.neg, .neg {{ color:var(--neg); }}
  td.pos, .pos {{ color:var(--pos); }}
  .muted {{ color:var(--muted); font-style:italic; }}
  .chart-wrap {{ width:100%; }}
  .chart-grid {{ display:grid; gap:18px; grid-template-columns:repeat(3,1fr); }}
  @media (max-width:820px) {{ .chart-grid {{ grid-template-columns:1fr; }} }}
  .chart h3 {{ margin:0 0 8px; font-size:13px; color:var(--muted);
               text-transform:uppercase; letter-spacing:.03em; }}
  .metrics {{ display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin-bottom:16px; }}
  @media (max-width:820px) {{ .metrics {{ grid-template-columns:repeat(2,1fr); }} }}
  .metric {{ background:#f8fafc; border:1px solid var(--line); border-radius:10px; padding:14px; }}
  .metric .label {{ font-size:12px; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; }}
  .metric .value {{ font-size:20px; font-weight:700; margin-top:6px; font-variant-numeric:tabular-nums; }}
  .term-help {{ position:relative; display:inline-flex; align-items:center; gap:4px;
                cursor:help; border-bottom:1px dotted currentColor; text-decoration:none; }}
  .term-help:focus {{ outline:2px solid #bfdbfe; outline-offset:3px; border-radius:5px; }}
  .term-icon {{ display:inline-flex; align-items:center; justify-content:center;
                width:15px; height:15px; border-radius:50%; background:#dbeafe;
                color:#1e40af; font-size:10px; font-weight:800; line-height:1; }}
  .term-tip {{ position:absolute; left:0; bottom:calc(100% + 8px); z-index:30;
               min-width:220px; max-width:300px; padding:10px 11px; border-radius:8px;
               background:#111827; color:#fff; font-size:12px; line-height:1.35;
               text-transform:none; letter-spacing:0; font-weight:500; text-align:left;
               box-shadow:0 10px 24px rgba(15,23,42,.24); opacity:0;
               pointer-events:none; transform:translateY(4px); transition:opacity .15s ease, transform .15s ease; }}
  .term-tip::after {{ content:''; position:absolute; left:14px; top:100%;
                      border:6px solid transparent; border-top-color:#111827; }}
  .term-help:hover .term-tip, .term-help:focus .term-tip,
  .term-help:focus-within .term-tip {{ opacity:1; transform:translateY(0); }}
  .term-note-ref, .tooltip-notes {{ display:none; }}
  .section-nav {{ position:sticky; top:0; z-index:10; display:flex; gap:8px; flex-wrap:wrap;
                  align-items:center; background:rgba(245,246,248,.96); border:1px solid var(--line);
                  border-radius:10px; padding:10px; margin:-8px 0 18px; backdrop-filter:blur(8px); }}
  .section-nav a {{ color:var(--accent); text-decoration:none; font-size:13px; font-weight:700;
                    padding:6px 8px; border-radius:7px; }}
  .section-nav a:hover, .section-nav a:focus {{ background:#e8f0f8; outline:none; }}
  .card[id] {{ scroll-margin-top:72px; }}
  .badge {{ display:inline-block; padding:2px 10px; border-radius:999px; font-size:12px; font-weight:700; }}
  .badge.ok {{ background:#def7ec; color:#046c4e; }}
  .badge.check {{ background:#fde8e8; color:#9b1c1c; }}
  footer {{ margin-top:28px; color:var(--muted); font-size:12px; text-align:center; }}
  @media print {{
    body {{ background:#fff; color:#111827; }}
    .wrap {{ max-width:none; padding:0; }}
    .section-nav {{ display:none; }}
    header.hero {{ box-shadow:none; border-radius:0; margin-bottom:12px; }}
    .card, .metric {{ box-shadow:none; break-inside:avoid; page-break-inside:avoid; }}
    .grid, .metrics, .chart-grid {{ gap:10px; }}
    table {{ page-break-inside:auto; }}
    tr {{ break-inside:avoid; page-break-inside:avoid; }}
    a {{ color:inherit; text-decoration:none; }}
    .term-help {{ display:inline; border-bottom:0; cursor:default; }}
    .term-icon {{ display:none; }}
    .term-note-ref {{ display:inline; color:#4b5563; font-size:.78em;
                      vertical-align:super; margin-left:1px; }}
    .term-tip {{ display:none; }}
    .term-tip::after {{ content:none; }}
    .tooltip-notes {{ display:block; margin-top:18px; padding-top:12px;
                      border-top:1px solid #d1d5db; color:#374151;
                      break-inside:avoid; page-break-inside:avoid; }}
    .tooltip-notes h2 {{ color:#374151; }}
    .tooltip-notes ol {{ margin:0; padding-left:20px; }}
    .tooltip-notes li {{ margin:0 0 5px; font-size:12px; line-height:1.35; }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <header class="hero">
    <h1>Portfolio Review</h1>
    <div class="sub">XTB account <strong>{escape(meta.get('account', '?'))}</strong> ·
      {escape(meta.get('period_from', '?'))} → {escape(meta.get('period_to', '?'))} · {currency}</div>
    <div class="sub" style="margin-top:6px;font-size:12px;opacity:.8">Generated {escape(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))} · Valuation date {escape(val_date)}</div>
  </header>

  <nav class='section-nav' aria-label='Report sections'>
    <a href='#summary'>Summary</a>
    <a href='#charts'>Charts</a>
    <a href='#holdings'>Holdings</a>
    <a href='#cash-flows'>Cash Flows</a>
    <a href='#performance'>Performance</a>
    <a href='#reconciliation'>Reconciliation</a>
  </nav>

  <div class="metrics">
    <div class="metric"><div class="label">{_label_html('Portfolio value')}</div>
      <div class="value">{money(perf['portfolio_value'])}</div></div>
    <div class="metric"><div class="label">{_label_html('Net deposited')}</div>
      <div class="value">{money(perf['net_deposited'])}</div></div>
    <div class="metric"><div class="label">{_label_html('Total gain')}</div>
      <div class="value {'pos' if perf['total_gain']>=0 else 'neg'}">
        {money(perf['total_gain'])}</div></div>
    <div class="metric"><div class="label">{_label_html('Total return')}</div>
      <div class="value {'pos' if perf['total_return_pct']>=0 else 'neg'}">
        {perf['total_return_pct']:+.2f}%</div></div>
  </div>

  <div class="grid">
    <div class="card" id='summary'>
      <h2>Executive Summary</h2>
      {_kv_table(summary_rows)}
    </div>
    <div class="card">
      <h2>Concentration &amp; Risk</h2>
      {_kv_table(concentration_rows)}
    </div>
    <div class="card">
      <h2>Income Quality</h2>
      {_kv_table(income_quality_rows)}
    </div>
    <div class="card">
      <h2>Methodology &amp; Data Quality</h2>
      {_kv_table(methodology_rows)}
    </div>
    <div class="card full">
      <h2>Beginner Guide</h2>
      {_kv_table(guide_rows)}
    </div>
    <div class="card full">
      <h2>Return Contribution</h2>
      {contribution_html}
    </div>
  </div>

  {charts_block}

  <div class="grid">
    <div class="card" id='holdings'>
      <h2>Holdings (live market value)</h2>
      {_df_to_html(holdings_view, {'Last Price':'.4f', 'Market Value':'.2f', 'Unrealized P/L':'.2f', 'Return %':'.2f', 'Weight %':'.2f'}, colored_cols={'Unrealized P/L', 'Return %'})}
    </div>
    <div class="card" id='cash-flows'>
      <h2>Cash flows</h2>
      {_kv_table(flows_rows)}
    </div>

    <div class="card">
      <h2>Open positions (market value)</h2>
      {op_html}
    </div>

    <div class="card">
      <h2>Realized P/L (closed positions)</h2>
      {realized_html}
    </div>

    <div class="card" id='performance'>
      <h2>Performance</h2>
      {_kv_table(perf_rows)}
    </div>

    <div class="card" id='reconciliation'>
      <h2>Reconciliation</h2>
      {_kv_table(recon_rows)}
      <p class="muted">This check makes sure the report read your cash movements correctly. XTB's "Total" row is the cash left in your account at the end of the period, not the value of your stocks or ETFs. The report calculates the same cash balance from deposits, withdrawals, trades, dividends, fees, and taxes, then compares both numbers.</p>
    </div>
  </div>

  {_tooltip_notes_html()}

  <footer>Generated from {escape(REPORT_FILE.name)} on {escape(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))} · live prices via yfinance (as of {escape(val_date)}){' · priced at cost: ' + escape(', '.join(cost_fallback_tickers)) if cost_fallback_tickers else ''}{'<br>' + '<br>'.join(escape(f'{t}: {COST_FALLBACK_NOTES[t]}') for t in cost_fallback_tickers if t in COST_FALLBACK_NOTES) if any(t in COST_FALLBACK_NOTES for t in cost_fallback_tickers) else ''}</footer>
</div>
<script>
{SORTABLE_TABLES_SCRIPT}
{TABLE_FILTERS_SCRIPT}
</script>
</body>
</html>"""


def _output_name(descriptor: str, ext: str) -> Path:
    """Path in RESULTS_DIR named after the input report's stem.

    e.g. input ``EUR_SAMPLE_2026-01-01_2026-06-20.xlsx`` with
    ``("review", "html")`` -> ``results/EUR_SAMPLE_2026-01-01_2026-06-20_review.html``.
    Falls back to a ``portfolio`` stem when ``REPORT_FILE`` is unset.
    """
    stem = REPORT_FILE.stem if REPORT_FILE else "portfolio"
    return RESULTS_DIR / f"{stem}_{descriptor}.{ext}"


def write_html_report(html: str, path: Path | str | None = None) -> Path:
    path = Path(path) if path else _output_name("review", "html")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path


def _json_number(value: object) -> float:
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return 0.0


def write_summary_json(
    currency: str,
    flows: dict[str, float],
    perf: dict[str, float],
    holdings: pd.DataFrame,
    as_of: date,
    cost_fallback_tickers: list[str],
    review_path: Path | str,
) -> Path:
    """Write a bounded summary for agents to inspect before raw report text.

    The summary intentionally excludes free-text workbook fields such as
    comments and instrument names. Tickers are retained as portfolio identifiers;
    numeric metrics are rounded for stable, compact output.
    """
    top_holdings = []
    if not holdings.empty:
        fields = ["ticker", "shares", "market_value", "unrealized_pl", "weight_pct"]
        available = [field for field in fields if field in holdings.columns]
        top = holdings.sort_values("weight_pct", ascending=False).head(10)
        for row in top[available].to_dict(orient="records"):
            top_holdings.append({
                "ticker": str(row.get("ticker", "")),
                "shares": _json_number(row.get("shares")),
                "market_value": _json_number(row.get("market_value")),
                "unrealized_pl": _json_number(row.get("unrealized_pl")),
                "weight_pct": _json_number(row.get("weight_pct")),
            })

    summary = {
        "currency": currency,
        "valuation_as_of": as_of.isoformat(),
        "review_path": str(review_path),
        "cash_reconciliation": {
            "ending_cash": _json_number(perf.get("ending_cash")),
            "broker_total": _json_number(perf.get("broker_total")),
            "difference": _json_number(perf.get("reconciliation_diff")),
        },
        "performance": {
            "portfolio_value": _json_number(perf.get("portfolio_value")),
            "net_deposited": _json_number(perf.get("net_deposited")),
            "total_gain": _json_number(perf.get("total_gain")),
            "total_return_pct": _json_number(perf.get("total_return_pct")),
            "income_yield_pct": _json_number(perf.get("income_yield_pct")),
        },
        "cash_flows": {
            key: _json_number(flows.get(key))
            for key in (
                "deposits", "withdrawals", "currency_conversions", "interest",
                "dividends", "dividend_tax", "invested", "proceeds",
                "conversion_fees", "estimated_embedded_fx_fees", "fees",
            )
        },
        "top_holdings": top_holdings,
        "cost_fallback_tickers": [str(ticker) for ticker in cost_fallback_tickers],
    }
    path = _output_name("summary", "json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _persist_outputs(
    holdings: pd.DataFrame,
    open_positions: pd.DataFrame,
    realized: pd.DataFrame,
    flows: dict[str, float],
    perf: dict[str, float],
    income_by_period: pd.Series,
    evolution_df: pd.DataFrame | None = None,
    as_of: date | None = None,
    write_csv: bool = True,
) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if not write_csv:
        return
    out_holdings = holdings.drop(
        columns=[c for c in holdings.columns if c.startswith("_")], errors="ignore"
    )
    out_holdings.to_csv(_output_name("holdings", "csv"), index=False)

    op_out = open_positions.copy()
    if as_of is not None:
        op_out = op_out.assign(as_of=as_of.isoformat())
    op_out.to_csv(_output_name("open_positions", "csv"), index=False)
    realized.to_csv(_output_name("realized_pl", "csv"), index=False)
    pd.DataFrame([flows]).to_csv(_output_name("cash_flows", "csv"), index=False)
    perf_row = dict(perf)
    if as_of is not None:
        perf_row["valuation_as_of"] = as_of.isoformat()
    pd.DataFrame([perf_row]).to_csv(_output_name("performance", "csv"), index=False)
    income_by_period.rename("income").to_csv(_output_name("income", "csv"))
    if evolution_df is not None and not evolution_df.empty:
        evolution_df.to_csv(_output_name("evolution", "csv"))


def main(
    xlsx_path: Path | str | None = None,
    write_csv: bool = False,
    *,
    auto_detect: bool = False,
) -> None:
    global REPORT_FILE
    REPORT_FILE = resolve_report_file(xlsx_path, auto_detect=auto_detect)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    currency = detect_currency()
    meta = load_meta()
    as_of = _parse_as_of(meta)
    positions, cash_ops, open_positions_raw, broker_total = load_data()

    trades = extract_trades(cash_ops)
    holdings, realized_from_trades = analyze_holdings(trades)
    realized = analyze_realized(positions, realized_from_trades)

    prices = fetch_prices(
        holdings["ticker"].tolist(), as_of, currency
    ) if not holdings.empty else {}
    valued_holdings = valuate_holdings(holdings, prices)
    cost_fallback_tickers = list(
        valued_holdings.loc[valued_holdings["price_source"] == "cost", "ticker"]
    )

    open_positions = analyze_open_positions(open_positions_raw, valued_holdings)
    flows, ending_cash = analyze_cash_flows(cash_ops, trades)
    dividends, interest, income_by_period = analyze_income(cash_ops)
    perf = compute_performance(
        holdings, open_positions, realized, flows, ending_cash, broker_total,
        cash_ops=cash_ops, terminal_date=as_of,
    )

    print_report(
        currency, meta, flows, ending_cash, valued_holdings,
        open_positions, realized, perf, dividends, interest,
        as_of=as_of, cost_fallback_tickers=cost_fallback_tickers,
    )

    # Evolution chart: cost vs realized + unrealized value over time.
    # Only live-valued tickers get history; cost-fallback ones stay flat.
    evolution_df = pd.DataFrame()
    live_tickers = list(
        valued_holdings.loc[valued_holdings["price_source"] == "live", "ticker"]
    )
    first_trade_date = min(
        (t.date for t in trades if t.date is not None), default=None
    )
    if live_tickers and first_trade_date is not None:
        price_history = fetch_price_history(
            live_tickers, first_trade_date.date(), as_of, currency
        )
        evolution_df = build_evolution_series(trades, price_history, as_of)

    _persist_outputs(
        valued_holdings, open_positions, realized, flows, perf,
        income_by_period, evolution_df, as_of, write_csv=write_csv,
    )

    # Charts: interactive Chart.js, inlined into the self-contained HTML.
    evolution_cfg = html_charts.evolution_chart_config(evolution_df, currency)
    review_cfg = html_charts.review_charts_config(
        valued_holdings, flows, income_by_period, currency)

    # HTML report (self-contained, offline).
    html = build_html_report(
        currency, meta, flows, ending_cash, valued_holdings,
        open_positions, realized, perf, evolution_cfg, review_cfg,
        as_of=as_of, cost_fallback_tickers=cost_fallback_tickers,
    )
    out = write_html_report(html)
    summary_out = write_summary_json(
        currency, flows, perf, valued_holdings, as_of, cost_fallback_tickers, out
    )
    print(f"HTML report written to {out}")
    print(f"Summary written to {summary_out}")


def main_cli() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a portfolio review from an XTB .xlsx report."
    )
    parser.add_argument(
        "input", nargs="?", default=None,
        help="Path to the XTB .xlsx report.",
    )
    parser.add_argument(
        "--auto-detect", action="store_true",
        help="Process the single non-lock .xlsx in the current directory when "
             "no explicit input path is provided.",
    )
    parser.add_argument(
        "--csv", action="store_true",
        help="Also write CSV outputs (holdings, cash flows, performance, etc.). "
             "By default only the HTML report is written.",
    )
    args = parser.parse_args()
    try:
        main(args.input, write_csv=args.csv, auto_detect=args.auto_detect)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main_cli()
