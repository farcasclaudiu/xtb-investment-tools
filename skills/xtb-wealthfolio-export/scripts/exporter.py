"""XTB report → Wealthfolio CSV exporter.

Wealthfolio expects a CSV with this header:
    date,symbol,quantity,activityType,unitPrice,currency,fee,amount

Activity-type mapping from an XTB "Cash Operations" sheet:
    Stock purchase (OPEN BUY ...)   -> BUY   (qty=shares, unitPrice=price)
    Stock sale    (CLOSE SELL ...)  -> SELL  (qty=shares, unitPrice=price)
    Stock sale    (OPEN SELL ...)   -> SELL  (short open, qty=shares)
    Deposit                         -> DEPOSIT
    Withdrawal                      -> WITHDRAWAL
    Dividend                        -> DIVIDEND
    Dividend tax                    -> TAX
    Free funds interest             -> INTEREST
    Currency conversion             -> FEE

Cash activities (DEPOSIT/WITHDRAWAL/DIVIDEND/INTEREST/TAX/FEE) carry their
value in `amount` with `quantity=1`, `unitPrice=1`; `symbol` is `$CASH-<CCY>`
for pure-cash rows and the real ticker for dividends. The `fee` column is only
used for inline BUY/SELL commissions; trades leave `amount` blank (it is
auto-calculated as quantity * unitPrice by Wealthfolio).

Run:
    python exporter.py                       # writes results/<stem>_wealthfolio.csv
    python exporter.py -o my.csv EUR_xxx.xlsx
"""
import argparse
import csv
from pathlib import Path

import pandas as pd

import main
from main import (
    CONVERSION_RE,
    DEPOSIT_RE,
    DIVIDEND_RE,
    DIVIDEND_TAX_RE,
    INTEREST_RE,
    PRICE_RE,
    TRADE_COMMENT_RE,
    WITHDRAW_RE,
    find_column,
    parse_numeric,
)

FIELDS = ["date", "symbol", "quantity", "activityType", "unitPrice", "currency", "fee", "amount"]

# XTB trade comment captures both the action (OPEN/CLOSE) and side (BUY/SELL).
SHORT_OPEN_RE = __import__("re").compile(r"OPEN\s+SELL", __import__("re").IGNORECASE)
QTY_RE = __import__("re").compile(r"(?:OPEN|CLOSE)\s+(?:BUY|SELL)\s+([\d./]+)", __import__("re").IGNORECASE)


def _trade_quantity(comment: str, value: float, price: float) -> float:
    """Derive executed shares from an XTB trade comment.

    XTB writes split fills as "N/M @ price" where N is this fill's share count
    and M the parent order size (e.g. "1/100" = 1 share). Prefer the numerator;
    fall back to cash / price.
    """
    m = QTY_RE.search(comment)
    if m:
        token = m.group(1)
        if "/" in token:
            try:
                numerator = float(token.split("/", 1)[0])
                if numerator > 0:
                    return numerator
            except ValueError:
                pass
        else:
            try:
                return float(token.replace(",", "."))
            except ValueError:
                pass
    return round(abs(value) / price, 6) if price > 0 else 0.0


def classify(type_val: str, comment: str) -> str | None:
    text = f"{type_val} {comment}".lower()
    if DIVIDEND_TAX_RE.search(text):
        return "TAX"
    if DIVIDEND_RE.search(text):
        return "DIVIDEND"
    if INTEREST_RE.search(text):
        return "INTEREST"
    if CONVERSION_RE.search(text):
        return "FEE"
    if WITHDRAW_RE.search(text):
        return "WITHDRAWAL"
    if DEPOSIT_RE.search(text):
        return "DEPOSIT"
    if TRADE_COMMENT_RE.search(comment):
        lowered_comment = comment.lower()
        lowered_type = type_val.lower()
        is_sell = (
            "close sell" in lowered_comment
            or SHORT_OPEN_RE.search(comment)
            or ("close buy" in lowered_comment and "sell" in lowered_type)
        )
        return "SELL" if is_sell else "BUY"
    return None


def _fmt_date(val) -> str:
    dt = pd.to_datetime(val, errors="coerce")
    if pd.isna(dt):
        return ""
    return dt.strftime("%Y-%m-%d")


def build_rows(
    cash_ops: pd.DataFrame, currency: str
) -> list[dict[str, str | float]]:
    type_col = find_column(cash_ops, ["type", "operation"], required=False)
    ticker_col = find_column(
        cash_ops, ["ticker", "symbol", "instrument", "market"], required=False
    )
    amount_col = find_column(
        cash_ops, ["amount", "value", "net_amount", "cash", "change", "payment"],
        required=False,
    )
    date_col = find_column(
        cash_ops, ["time", "date", "operation_date", "booking_date", "transaction_date"],
        required=False,
    )
    comment_col = find_column(cash_ops, ["comment", "description", "details"], required=False)

    if not (type_col and amount_col):
        return []

    rows: list[dict[str, str | float]] = []
    for _, row in cash_ops.iterrows():
        type_val = str(row.get(type_col, "")).strip()
        comment = str(row.get(comment_col, "")) if comment_col else ""
        activity = classify(type_val, comment)
        if activity is None:
            continue

        amount = float(parse_numeric(pd.Series([row[amount_col]])).iloc[0])
        date = _fmt_date(row.get(date_col)) if date_col else ""
        cash_sym = f"$CASH-{currency}"
        ticker = str(row[ticker_col]).strip() if ticker_col and pd.notna(row.get(ticker_col)) else ""

        if activity in ("BUY", "SELL"):
            price = 0.0
            m = PRICE_RE.search(comment)
            if m:
                price = float(parse_numeric(pd.Series([m.group(1)])).iloc[0])
            quantity = _trade_quantity(comment, amount, price)
            rows.append({
                "date": date, "symbol": ticker or cash_sym, "quantity": quantity,
                "activityType": activity, "unitPrice": round(price, 6),
                "currency": currency, "fee": 0.0, "amount": "",
            })
        elif activity == "DIVIDEND":
            rows.append({
                "date": date, "symbol": ticker or cash_sym, "quantity": 1.0,
                "activityType": activity, "unitPrice": 1.0,
                "currency": currency, "fee": 0.0, "amount": round(abs(amount), 6),
            })
        elif activity in ("DEPOSIT", "WITHDRAWAL", "INTEREST", "TAX", "FEE"):
            rows.append({
                "date": date, "symbol": cash_sym, "quantity": 1.0,
                "activityType": activity, "unitPrice": 1.0,
                "currency": currency, "fee": 0.0, "amount": round(abs(amount), 6),
            })
    return rows


def export(
    xlsx_path: Path | str | None = None,
    output_path: Path | str | None = None,
) -> Path:
    main.REPORT_FILE = main.resolve_report_file(xlsx_path)
    currency = main.detect_currency()
    _, cash_ops, _, _ = main.load_data()
    rows = build_rows(cash_ops, currency)

    if output_path:
        out = Path(output_path)
    else:
        stem = main.REPORT_FILE.stem if main.REPORT_FILE else "portfolio"
        out = main.RESULTS_DIR / f"{stem}_wealthfolio.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for r in rows:
            amt = r["amount"]
            writer.writerow({
                "date": r["date"],
                "symbol": r["symbol"],
                "quantity": f"{r['quantity']:.6f}".rstrip("0").rstrip("."),
                "activityType": r["activityType"],
                "unitPrice": f"{r['unitPrice']:.6f}".rstrip("0").rstrip("."),
                "currency": r["currency"],
                "fee": f"{r['fee']:.2f}",
                "amount": "" if amt == "" else f"{amt:.6f}".rstrip("0").rstrip("."),
            })
    return out


def main_cli() -> None:
    p = argparse.ArgumentParser(description="Export XTB xlsx to Wealthfolio CSV.")
    p.add_argument("input", nargs="?", default=None,
                   help="Path to the XTB .xlsx report (auto-detected if omitted)")
    p.add_argument("-o", "--output", default=None,
                   help="Output CSV path (default: results/<stem>_wealthfolio.csv)")
    args = p.parse_args()
    try:
        out = export(args.input, args.output)
    except (FileNotFoundError, ValueError) as exc:
        p.error(str(exc))
    print(f"Wrote {out.resolve()} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main_cli()
