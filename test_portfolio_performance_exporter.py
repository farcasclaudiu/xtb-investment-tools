import csv

import pandas as pd

import main
import portfolio_performance_exporter as pp


def _cash_ops(rows):
    cols = ["Type", "Instrument", "Ticker", "Time", "Amount", "Comment", "Product"]
    return main.clean_columns(pd.DataFrame(rows, columns=cols))


def _row(type_, instr="", ticker="", amount=0.0, comment="", time="2026-02-18 09:00:00"):
    return [type_, instr, ticker, time, amount, comment, "My Trades"]


def _read_semicolon_csv(path):
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter=";"))


def test_export_writes_two_semicolon_csvs_with_expected_headers(tmp_path):
    outputs = pp.export(main.REPORT_FILE, output_dir=tmp_path)

    portfolio_path = outputs["portfolio_transactions"]
    account_path = outputs["account_transactions"]
    assert portfolio_path.name == (
        f"{main.REPORT_FILE.stem}_portfolio_performance_portfolio_transactions.csv"
    )
    assert account_path.name == (
        f"{main.REPORT_FILE.stem}_portfolio_performance_account_transactions.csv"
    )

    with portfolio_path.open(encoding="utf-8") as f:
        assert f.readline().strip() == ";".join(pp.PORTFOLIO_FIELDS)
    with account_path.open(encoding="utf-8") as f:
        assert f.readline().strip() == ";".join(pp.ACCOUNT_FIELDS)

    portfolio_rows = _read_semicolon_csv(portfolio_path)
    account_rows = _read_semicolon_csv(account_path)

    assert [row["Type"] for row in portfolio_rows] == ["Buy", "Sell"]
    assert [row["Type"] for row in account_rows] == ["Deposit", "Dividend", "Taxes"]


def test_build_rows_separates_trade_and_cash_activity():
    ops = _cash_ops([
        _row("Deposit", amount=4000.0, comment="deposit funds"),
        _row("Stock purchase", "Demo Equity", "DEMO.DE", -500.0, "OPEN BUY 5 @ 100.00"),
        _row("Dividend", "Demo Equity", "DEMO.DE", 10.0, "Dividend"),
        _row("Dividend tax", "Demo Equity", "DEMO.DE", -1.5, "Dividend tax"),
        _row("Free funds interest", amount=0.03, comment="Free funds interest"),
        _row("RO tax", amount=-0.01, comment="Tax"),
        _row("Stock sell", "Demo Equity", "DEMO.DE", 240.0, "CLOSE BUY 2 @ 120.00"),
    ])

    portfolio_rows, account_rows = pp.build_rows(ops, "EUR")

    assert [row["Type"] for row in portfolio_rows] == ["Buy", "Sell"]
    assert portfolio_rows[0]["Shares"] == 5.0
    assert portfolio_rows[0]["Ticker Symbol"] == "DEMO.DE"
    assert portfolio_rows[0]["Security Name"] == "Demo Equity"
    assert portfolio_rows[0]["Value"] == 500.0
    assert portfolio_rows[0]["Securities Account"] == "XTB"
    assert portfolio_rows[0]["Cash Account"] == "XTB (EUR)"
    assert portfolio_rows[1]["Type"] == "Sell"

    assert [row["Type"] for row in account_rows] == [
        "Deposit",
        "Dividend",
        "Taxes",
        "Interest",
        "Taxes",
    ]
    dividend = account_rows[1]
    assert dividend["Ticker Symbol"] == "DEMO.DE"
    assert dividend["Security Name"] == "Demo Equity"
    assert dividend["Value"] == 10.0


def test_split_fill_quantity_uses_numerator():
    ops = _cash_ops([
        _row("Stock purchase", "S&P 500", "SPY.US", -14.31, "OPEN BUY 1/100 @ 14.3130"),
        _row("Stock purchase", "S&P 500", "SPY.US", -1416.99, "OPEN BUY 99/100 @ 14.3130"),
    ])

    portfolio_rows, account_rows = pp.build_rows(ops, "EUR")

    assert account_rows == []
    assert [row["Shares"] for row in portfolio_rows] == [1.0, 99.0]


def test_custom_account_names_are_used_in_rows():
    ops = _cash_ops([
        _row("Stock purchase", "Demo Equity", "DEMO.DE", -500.0, "OPEN BUY 5 @ 100.00"),
        _row("Deposit", amount=4000.0, comment="deposit funds"),
    ])

    portfolio_rows, account_rows = pp.build_rows(
        ops,
        "EUR",
        securities_account="Broker Securities",
        cash_account="Broker Cash EUR",
    )

    assert portfolio_rows[0]["Securities Account"] == "Broker Securities"
    assert portfolio_rows[0]["Cash Account"] == "Broker Cash EUR"
    assert account_rows[0]["Cash Account"] == "Broker Cash EUR"


def test_empty_input_writes_headers_only(tmp_path, monkeypatch):
    empty = main.clean_columns(
        pd.DataFrame(columns=["Type", "Instrument", "Ticker", "Time", "Amount", "Comment", "Product"])
    )
    monkeypatch.setattr(main, "load_data", lambda: (pd.DataFrame(), empty, pd.DataFrame(), 0.0))

    outputs = pp.export(main.REPORT_FILE, output_dir=tmp_path)

    for key, fields in (
        ("portfolio_transactions", pp.PORTFOLIO_FIELDS),
        ("account_transactions", pp.ACCOUNT_FIELDS),
    ):
        with outputs[key].open(encoding="utf-8") as f:
            assert f.readline().strip() == ";".join(fields)
            assert f.read() == ""
