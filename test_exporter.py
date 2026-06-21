import csv

import pandas as pd
import pytest

import exporter
from exporter import _trade_quantity, build_rows, classify, export
import main


def _cash_ops(rows):
    cols = ["Type", "Instrument", "Time", "Amount", "Comment", "Product"]
    return main.clean_columns(pd.DataFrame(rows, columns=cols))


def _row(type_, instr, amount, comment="", time="2026-02-18 09:00:00"):
    return [type_, instr, time, amount, comment, "My Trades"]


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------
class TestClassify:
    def test_buy(self):
        assert classify("Stock purchase", "OPEN BUY 6 @ 301.50") == "BUY"

    def test_close_sell(self):
        assert classify("Stock sale", "CLOSE SELL 2 @ 100.00") == "SELL"

    def test_close_buy_stock_sell(self):
        assert classify("Stock sell", "CLOSE BUY 1 @ 150.00") == "SELL"

    def test_open_sell_short(self):
        assert classify("Stock purchase", "OPEN SELL 2 @ 100.00") == "SELL"

    def test_deposit(self):
        assert classify("Deposit", "deposit funds") == "DEPOSIT"

    def test_withdrawal(self):
        assert classify("Withdrawal", "payout") == "WITHDRAWAL"

    def test_dividend(self):
        assert classify("Dividend", "Dividend payment") == "DIVIDEND"

    def test_dividend_tax(self):
        assert classify("Dividend tax", "Dividend tax") == "TAX"

    def test_interest(self):
        assert classify("Free funds interest", "") == "INTEREST"

    def test_fx(self):
        assert classify("Currency conversion", "fx fee") == "FEE"

    def test_unknown(self):
        assert classify("Something else", "") is None


# ---------------------------------------------------------------------------
# _trade_quantity
# ---------------------------------------------------------------------------
class TestTradeQuantity:
    def test_integer_token(self):
        assert _trade_quantity("OPEN BUY 6 @ 301.50", 1809.0, 301.5) == 6.0

    def test_fraction_token_numerator(self):
        assert _trade_quantity("OPEN BUY 1/100 @ 14.3130", 14.31, 14.313) == 1.0

    def test_fraction_with_rounded_cash_still_uses_numerator(self):
        assert _trade_quantity("OPEN BUY 1/100 @ 14.3130", 14.31, 14.313) == 1.0

    def test_fraction_99(self):
        assert _trade_quantity("OPEN BUY 99/100 @ 14.3130", 1416.99, 14.313) == 99.0

    def test_fallback_value_over_price(self):
        assert _trade_quantity("no token here", 1000.0, 100.0) == 10.0


# ---------------------------------------------------------------------------
# build_rows
# ---------------------------------------------------------------------------
class TestBuildRows:
    def test_full_mapping(self):
        ops = _cash_ops([
            _row("Stock purchase", "Stoxx Europe 600", -1809, "OPEN BUY 6 @ 301.50"),
            _row("Stock purchase", "S&P 500", -14.31, "OPEN BUY 1/100 @ 14.3130"),
            _row("Stock purchase", "S&P 500", -1416.99, "OPEN BUY 99/100 @ 14.3130"),
            _row("Deposit", "", 4000, "JP_MORGAN deposit"),
            _row("Free funds interest", "", 0.01, ""),
        ])
        rows = build_rows(ops, "EUR")
        assert [r["activityType"] for r in rows] == ["BUY", "BUY", "BUY", "DEPOSIT", "INTEREST"]

        buys = [r for r in rows if r["activityType"] == "BUY"]
        assert buys[0]["symbol"] == "Stoxx Europe 600"
        assert buys[0]["quantity"] == 6.0
        assert buys[0]["unitPrice"] == 301.5
        assert buys[1]["symbol"] == "S&P 500"
        assert buys[1]["quantity"] == 1.0
        assert buys[2]["quantity"] == 99.0

        deposit = next(r for r in rows if r["activityType"] == "DEPOSIT")
        assert deposit["symbol"] == "$CASH-EUR"
        assert deposit["quantity"] == 1.0
        assert deposit["unitPrice"] == 1.0
        assert deposit["amount"] == 4000.0
        assert deposit["fee"] == 0.0

    def test_dividend_and_tax(self):
        ops = _cash_ops([
            _row("Dividend", "AAPL", 10.0, "Dividend"),
            _row("Dividend tax", "AAPL", -1.5, "Dividend tax"),
        ])
        rows = build_rows(ops, "EUR")
        div = next(r for r in rows if r["activityType"] == "DIVIDEND")
        tax = next(r for r in rows if r["activityType"] == "TAX")
        assert div["symbol"] == "AAPL"
        assert div["quantity"] == 1.0
        assert div["amount"] == 10.0
        assert tax["symbol"] == "$CASH-EUR"
        assert tax["amount"] == 1.5
        assert tax["fee"] == 0.0

    def test_close_buy_stock_sell_exports_sell_row(self):
        ops = _cash_ops([
            _row("Stock sell", "A", 150.0, "CLOSE BUY 1 @ 150.00"),
        ])
        rows = build_rows(ops, "EUR")
        assert len(rows) == 1
        assert rows[0]["activityType"] == "SELL"
        assert rows[0]["symbol"] == "A"
        assert rows[0]["quantity"] == 1.0
        assert rows[0]["unitPrice"] == 150.0


# ---------------------------------------------------------------------------
# export (file output + schema)
# ---------------------------------------------------------------------------
class TestExport:
    def test_synthetic_report(self, tmp_path):
        out = export(main.REPORT_FILE, tmp_path / "wf.csv")
        with out.open() as f:
            reader = csv.DictReader(f)
            assert reader.fieldnames == [
                "date", "symbol", "quantity", "activityType",
                "unitPrice", "currency", "fee", "amount",
            ]
            rows = list(reader)

        buys = [r for r in rows if r["activityType"] == "BUY"]
        assert buys[0]["quantity"] == "5"
        assert buys[0]["symbol"] == "DEMO.DE"
        assert buys[0]["amount"] == ""  # trades: amount auto-calculated by Wealthfolio
        sells = [r for r in rows if r["activityType"] == "SELL"]
        assert sells[0]["quantity"] == "2"
        assert sells[0]["unitPrice"] == "120"
        assert all(r["currency"] == "EUR" for r in rows)
        for r in rows:
            for k in ("date", "symbol", "quantity", "activityType", "unitPrice", "currency", "fee"):
                assert r[k] != ""
            if r["activityType"] not in ("BUY", "SELL"):
                assert r["amount"] != ""

    def test_default_output_stems_from_input(self, tmp_path):
        prev = main.RESULTS_DIR
        main.RESULTS_DIR = tmp_path
        try:
            out = export(main.REPORT_FILE)
        finally:
            main.RESULTS_DIR = prev
        expected = f"{main.REPORT_FILE.stem}_wealthfolio.csv"
        assert out.name == expected
        assert out.parent == tmp_path

    def test_empty_input(self, tmp_path, monkeypatch):
        empty = main.clean_columns(
            pd.DataFrame(columns=["Type", "Instrument", "Time", "Amount", "Comment", "Product"])
        )
        monkeypatch.setattr(exporter, "build_rows", lambda *a, **k: [])
        monkeypatch.setattr(main, "load_data", lambda: (pd.DataFrame(), empty, pd.DataFrame(), 0.0))
        out = export(main.REPORT_FILE, tmp_path / "empty.csv")
        with out.open() as f:
            assert f.readline().strip() == ",".join(exporter.FIELDS)
            assert f.read() == ""
