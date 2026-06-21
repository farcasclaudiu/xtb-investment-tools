"""XTB report -> Portfolio Performance CSV exporter.

Portfolio Performance imports CSV files by import type. This exporter writes
two semicolon-delimited UTF-8 files:

* Portfolio Transactions: buys and sells
* Account Transactions: deposits, dividends, taxes, interest, and transfers

Run:
    python exporter.py report.xlsx
    python exporter.py report.xlsx --output-dir results
"""
from __future__ import annotations

import argparse
import csv
import re
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
    parse_executed_quantity,
    parse_numeric,
)


PORTFOLIO_FIELDS = [
    "Date",
    "Type",
    "Shares",
    "Ticker Symbol",
    "Security Name",
    "Value",
    "Fees",
    "Taxes",
    "Note",
    "Securities Account",
    "Cash Account",
]

ACCOUNT_FIELDS = [
    "Date",
    "Type",
    "Value",
    "Ticker Symbol",
    "Security Name",
    "Shares",
    "Gross Amount",
    "Currency Gross Amount",
    "Note",
    "Cash Account",
    "Offset Account",
]

SHORT_OPEN_RE = re.compile(r"OPEN\s+SELL", re.IGNORECASE)
TRANSFER_RE = re.compile(r"\b(subaccount\s+transfer|transfer)\b", re.IGNORECASE)
TAX_RE = re.compile(r"\btax\b|withholding", re.IGNORECASE)


def default_cash_account(currency: str, account_prefix: str = "XTB") -> str:
    return f"{account_prefix} ({currency})"


def _fmt_date(val) -> str:
    dt = pd.to_datetime(val, errors="coerce")
    if pd.isna(dt):
        return ""
    return dt.strftime("%Y-%m-%d")


def _fmt_decimal(val) -> str:
    if val == "" or val is None:
        return ""
    num = float(val)
    return f"{num:.6f}".rstrip("0").rstrip(".")


def _clean_text(val) -> str:
    if val is None or pd.isna(val):
        return ""
    text = str(val).strip()
    return "" if text.lower() == "nan" else text


def _trade_type(type_val: str, comment: str) -> str | None:
    if not TRADE_COMMENT_RE.search(comment):
        return None
    lowered_comment = comment.lower()
    lowered_type = type_val.lower()
    is_sell = (
        "close sell" in lowered_comment
        or SHORT_OPEN_RE.search(comment)
        or ("close buy" in lowered_comment and "sell" in lowered_type)
    )
    return "Sell" if is_sell else "Buy"


def _account_type(type_val: str, comment: str, amount: float) -> str | None:
    text = f"{type_val} {comment}".lower()
    if DIVIDEND_TAX_RE.search(text) or TAX_RE.search(text):
        return "Taxes"
    if DIVIDEND_RE.search(text):
        return "Dividend"
    if INTEREST_RE.search(text):
        return "Interest"
    if CONVERSION_RE.search(text):
        return "Fees"
    if WITHDRAW_RE.search(text):
        return "Withdrawal"
    if DEPOSIT_RE.search(text):
        return "Deposit"
    if TRANSFER_RE.search(text):
        return "Transfer (Inbound)" if amount >= 0 else "Transfer (Outbound)"
    return None


def build_rows(
    cash_ops: pd.DataFrame,
    currency: str,
    *,
    securities_account: str = "XTB",
    cash_account: str | None = None,
    account_prefix: str = "XTB",
) -> tuple[list[dict[str, str | float]], list[dict[str, str | float]]]:
    """Build Portfolio Performance portfolio/account transaction rows."""
    cash_account = cash_account or default_cash_account(currency, account_prefix)

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

    if not (type_col and amount_col):
        return [], []

    portfolio_rows: list[dict[str, str | float]] = []
    account_rows: list[dict[str, str | float]] = []

    for _, row in cash_ops.iterrows():
        type_val = _clean_text(row.get(type_col))
        comment = _clean_text(row.get(comment_col)) if comment_col else ""
        amount = float(parse_numeric(pd.Series([row[amount_col]])).iloc[0])
        date = _fmt_date(row.get(date_col)) if date_col else ""
        ticker = _clean_text(row.get(ticker_col)) if ticker_col else ""
        security_name = _clean_text(row.get(name_col)) if name_col else ""

        trade_type = _trade_type(type_val, comment)
        if trade_type:
            price = 0.0
            price_match = PRICE_RE.search(comment)
            if price_match:
                price = float(parse_numeric(pd.Series([price_match.group(1)])).iloc[0])
            shares = parse_executed_quantity(comment, amount, price)
            portfolio_rows.append({
                "Date": date,
                "Type": trade_type,
                "Shares": shares,
                "Ticker Symbol": ticker,
                "Security Name": security_name,
                "Value": round(abs(amount), 6),
                "Fees": "",
                "Taxes": "",
                "Note": comment,
                "Securities Account": securities_account,
                "Cash Account": cash_account,
            })
            continue

        account_type = _account_type(type_val, comment, amount)
        if account_type is None:
            continue

        account_rows.append({
            "Date": date,
            "Type": account_type,
            "Value": round(abs(amount), 6),
            "Ticker Symbol": ticker if account_type == "Dividend" else "",
            "Security Name": security_name if account_type == "Dividend" else "",
            "Shares": "",
            "Gross Amount": "",
            "Currency Gross Amount": "",
            "Note": comment or type_val,
            "Cash Account": cash_account,
            "Offset Account": "",
        })

    return portfolio_rows, account_rows


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, str | float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter=";")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                field: _fmt_decimal(row[field])
                if isinstance(row.get(field), float)
                else row.get(field, "")
                for field in fields
            })


def export(
    xlsx_path: Path | str | None = None,
    output_dir: Path | str | None = None,
    *,
    securities_account: str = "XTB",
    cash_account: str | None = None,
    account_prefix: str = "XTB",
) -> dict[str, Path]:
    main.REPORT_FILE = main.resolve_report_file(xlsx_path)
    currency = main.detect_currency()
    _, cash_ops, _, _ = main.load_data()
    portfolio_rows, account_rows = build_rows(
        cash_ops,
        currency,
        securities_account=securities_account,
        cash_account=cash_account,
        account_prefix=account_prefix,
    )

    out_dir = Path(output_dir) if output_dir is not None else main.RESULTS_DIR
    stem = main.REPORT_FILE.stem if main.REPORT_FILE else "portfolio"
    outputs = {
        "portfolio_transactions": out_dir
        / f"{stem}_portfolio_performance_portfolio_transactions.csv",
        "account_transactions": out_dir
        / f"{stem}_portfolio_performance_account_transactions.csv",
    }

    _write_csv(outputs["portfolio_transactions"], PORTFOLIO_FIELDS, portfolio_rows)
    _write_csv(outputs["account_transactions"], ACCOUNT_FIELDS, account_rows)
    return outputs


def main_cli() -> None:
    parser = argparse.ArgumentParser(description="Export XTB xlsx to Portfolio Performance CSVs.")
    parser.add_argument(
        "input",
        nargs="?",
        default=None,
        help="Path to the XTB .xlsx report (auto-detected if omitted)",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default=None,
        help="Output directory (default: results)",
    )
    parser.add_argument(
        "--securities-account",
        default="XTB",
        help="Portfolio Performance securities account name (default: XTB)",
    )
    parser.add_argument(
        "--cash-account",
        default=None,
        help="Portfolio Performance cash account name (default: XTB (<CCY>))",
    )
    parser.add_argument(
        "--account-prefix",
        default="XTB",
        help="Prefix for the default cash account name (default: XTB)",
    )
    args = parser.parse_args()

    try:
        outputs = export(
            args.input,
            args.output_dir,
            securities_account=args.securities_account,
            cash_account=args.cash_account,
            account_prefix=args.account_prefix,
        )
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))

    for label, path in outputs.items():
        print(f"Wrote {label}: {path.resolve()} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main_cli()
