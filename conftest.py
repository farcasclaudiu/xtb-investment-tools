"""Shared pytest fixtures."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import main


def write_synthetic_xtb_report(path: Path) -> Path:
    """Write a minimal non-sensitive XTB-style workbook for integration tests."""
    closed_positions = pd.DataFrame(columns=["Instrument", "Ticker", "Profit/Loss"])
    open_positions = pd.DataFrame(
        [
            {
                "Instrument": "Demo Equity",
                "Ticker": "DEMO.DE",
                "Market Value": 300.0,
                "Current Value": 300.0,
                "Unrealized P/L": 0.0,
                "Open Price": 100.0,
                "Market Price": 100.0,
            }
        ]
    )
    cash_ops = pd.DataFrame(
        [
            ["Deposit", "", "", "2026-01-01 09:00:00", 1000.0, "deposit funds", "Cash"],
            [
                "Stock purchase",
                "Demo Equity",
                "DEMO.DE",
                "2026-01-02 09:00:00",
                -500.0,
                "OPEN BUY 5 @ 100.00",
                "My Trades",
            ],
            ["Dividend", "Demo Equity", "DEMO.DE", "2026-01-03 09:00:00", 10.0, "Dividend", "Cash"],
            [
                "Dividend tax",
                "Demo Equity",
                "DEMO.DE",
                "2026-01-03 09:01:00",
                -1.5,
                "Dividend tax",
                "Cash",
            ],
            [
                "Stock sale",
                "Demo Equity",
                "DEMO.DE",
                "2026-01-04 09:00:00",
                240.0,
                "CLOSE SELL 2 @ 120.00",
                "My Trades",
            ],
            ["Total", "", "", "", 748.5, "", ""],
        ],
        columns=["Type", "Instrument", "Ticker", "Time", "Amount", "Comment", "Product"],
    )

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        closed_positions.to_excel(writer, sheet_name=main.POSITIONS_SHEET, index=False, startrow=4)
        open_positions.to_excel(writer, sheet_name=main.OPEN_POSITIONS_SHEET, index=False, startrow=4)
        cash_ops.to_excel(writer, sheet_name=main.CASH_SHEET, index=False, startrow=4)

        cash_sheet = writer.book[main.CASH_SHEET]
        cash_sheet.cell(row=1, column=1, value="Account")
        cash_sheet.cell(row=1, column=2, value="DEMO-ACCOUNT")
        cash_sheet.cell(row=2, column=1, value="Date from")
        cash_sheet.cell(row=2, column=2, value="2026-01-01")
        cash_sheet.cell(row=3, column=1, value="Date to")
        cash_sheet.cell(row=3, column=2, value="2026-01-04")

    return path


@pytest.fixture(autouse=True)
def _synthetic_report(tmp_path):
    previous = main.REPORT_FILE
    main.REPORT_FILE = write_synthetic_xtb_report(tmp_path / "EUR_demo_report.xlsx")
    try:
        yield
    finally:
        main.REPORT_FILE = previous
