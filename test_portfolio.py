import pandas as pd
import pytest

import main
from main import (
    Trade,
    analyze_cash_flows,
    analyze_holdings,
    analyze_income,
    analyze_open_positions,
    analyze_realized,
    analyze_concentration,
    analyze_income_quality,
    analyze_methodology_quality,
    analyze_return_contributions,
    build_executive_summary,
    build_external_cash_flows,
    clean_columns,
    compute_performance,
    compute_xirr,
    build_evolution_series,
    detect_currency,
    extract_trades,
    find_column,
    parse_numeric,
    parse_quantity,
    valuate_holdings,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
def make_cash_ops(rows):
    cols = ["Type", "Instrument", "Time", "Amount", "Comment", "Product"]
    return clean_columns(pd.DataFrame(rows, columns=cols))


def cash_row(type_, instrument, amount, comment="", time="2026-01-15 10:00:00"):
    return [type_, instrument, time, amount, comment, "My Trades"]


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------
class TestHelpers:
    def test_clean_columns_normalizes(self):
        df = pd.DataFrame(columns=["Open Price", "Profit/Loss", "  Ticker  "])
        out = clean_columns(df)
        assert list(out.columns) == ["open_price", "profitloss", "ticker"]

    def test_find_column_exact_and_partial(self):
        df = pd.DataFrame(columns=["ticker", "open_price"])
        assert find_column(df, ["ticker"]) == "ticker"
        assert find_column(df, ["price"]) == "open_price"
        assert find_column(df, ["missing"], required=False) is None

    def test_find_column_required_raises(self):
        df = pd.DataFrame(columns=["a"])
        with pytest.raises(ValueError):
            find_column(df, ["b"])

    def test_parse_numeric_european_and_dirty(self):
        # Comma-decimal supported; thousand-separators are intentionally NOT
        # supported (ambiguous with decimal dot).
        s = pd.Series(["1234,56", "-1809", " 12,5 €", "", "N/A"])
        out = parse_numeric(s).tolist()
        assert out == [1234.56, -1809.0, 12.5, 0.0, 0.0]


# ---------------------------------------------------------------------------
# parse_quantity
# ---------------------------------------------------------------------------
class TestParseQuantity:
    def test_integer(self):
        assert parse_quantity("6") == 6.0

    def test_decimal_comma(self):
        assert parse_quantity("12,5") == 12.5

    def test_fraction(self):
        assert parse_quantity("1/100") == 0.01

    def test_zero_denominator(self):
        assert parse_quantity("5/0") == 0.0

    def test_garbage(self):
        assert parse_quantity("abc") == 0.0


# ---------------------------------------------------------------------------
# detect_currency
# ---------------------------------------------------------------------------
class TestDetectCurrency:
    def test_from_filename(self, monkeypatch):
        monkeypatch.setattr(main, "REPORT_FILE", main.Path("USD_12345.xlsx"))
        assert detect_currency() == "USD"

    def test_default_eur(self, monkeypatch):
        monkeypatch.setattr(main, "REPORT_FILE", main.Path("report.xlsx"))
        assert detect_currency() == "EUR"


# ---------------------------------------------------------------------------
# extract_trades
# ---------------------------------------------------------------------------
class TestExtractTrades:
    def test_parses_open_buy(self):
        ops = make_cash_ops([
            cash_row("Stock purchase", "S&P 500", -14.31, "OPEN BUY 1/100 @ 14.3130"),
        ])
        trades = extract_trades(ops)
        assert len(trades) == 1
        t = trades[0]
        assert t.action == "open"
        assert t.side == "buy"
        # Fixture has no "Ticker" column, so find_column falls back to Instrument.
        assert t.ticker == "S&P 500"
        assert t.value == pytest.approx(14.31)
        assert t.price == pytest.approx(14.313)
        # split-fill notation uses the executed numerator, not rounded cash / price
        assert t.shares == pytest.approx(1.0)

    def test_split_fill_uses_numerator_not_cash_over_price(self):
        ops = make_cash_ops([
            cash_row("Stock purchase", "A", -14.31, "OPEN BUY 1/100 @ 14.3130"),
        ])
        trades = extract_trades(ops)
        assert trades[0].shares == pytest.approx(1.0)

    def test_ignores_deposits_and_interest(self):
        ops = make_cash_ops([
            cash_row("Deposit", "", 4000, "JP_MORGAN deposit"),
            cash_row("Free funds interest", "", 0.01),
            cash_row("Stock purchase", "AAPL", -100, "OPEN BUY 1 @ 100.00"),
        ])
        trades = extract_trades(ops)
        assert len(trades) == 1
        assert trades[0].ticker == "AAPL"

    def test_excludes_dividend_type(self):
        ops = make_cash_ops([
            cash_row("Dividend", "AAPL", 5.0, "Dividend payment"),
        ])
        assert extract_trades(ops) == []

    def test_close_sell_recognized(self):
        ops = make_cash_ops([
            cash_row("Stock sale", "AAPL", 110.0, "CLOSE SELL 1 @ 110.00"),
        ])
        trades = extract_trades(ops)
        assert trades[0].action == "close"
        assert trades[0].side == "sell"

    def test_close_buy_stock_sell_is_sale_close(self):
        ops = make_cash_ops([
            cash_row("Stock sell", "A", 150.0, "CLOSE BUY 1 @ 150.00"),
        ])
        trades = extract_trades(ops)
        assert len(trades) == 1
        assert trades[0].action == "close"
        assert trades[0].side == "sell"
        assert trades[0].value == pytest.approx(150.0)

    def test_missing_columns_returns_empty(self):
        ops = clean_columns(pd.DataFrame(columns=["a", "b"]))
        assert extract_trades(ops) == []

    def test_prefers_ticker_column(self):
        # Real XTB exports carry both `Ticker` (e.g. SPYL.DE) and `Instrument`
        # (descriptive). The real symbol must win so grouping/price lookup work.
        ops = clean_columns(pd.DataFrame(
            [["Stock purchase", "SPYL.DE", "S&P 500", "2026-01-15 10:00:00",
              -15.73, "OPEN BUY 1 @ 15.7300", "My Trades"]],
            columns=["Type", "Ticker", "Instrument", "Time", "Amount",
                     "Comment", "Product"],
        ))
        trades = extract_trades(ops)
        assert trades[0].ticker == "SPYL.DE"
        assert trades[0].name == "S&P 500"


# ---------------------------------------------------------------------------
# analyze_holdings (FIFO realized P/L)
# ---------------------------------------------------------------------------
class TestAnalyzeHoldings:
    def test_open_only(self):
        trades = [
            Trade("AAPL", "open", "buy", shares=10, price=100.0, value=1000.0),
            Trade("MSFT", "open", "buy", shares=5, price=200.0, value=1000.0),
        ]
        h, _ = analyze_holdings(trades)
        assert set(h["ticker"]) == {"AAPL", "MSFT"}
        aapl = h[h["ticker"] == "AAPL"].iloc[0]
        assert aapl["shares"] == 10.0
        assert aapl["cost_basis"] == pytest.approx(1000.0)
        assert aapl["avg_price"] == pytest.approx(100.0)

    def test_allocation_pct_sums_to_100(self):
        trades = [
            Trade("A", "open", "buy", shares=10, price=100.0, value=1000.0),
            Trade("B", "open", "buy", shares=5, price=200.0, value=1000.0),
        ]
        h, _ = analyze_holdings(trades)
        assert h["allocation_pct"].sum() == pytest.approx(100.0)

    def test_partial_close_fifo_realized(self):
        # Buy 10 @ 100, then close 4 @ 150 -> realized = 4*50 = 200, 6 left.
        trades = [
            Trade("AAPL", "open", "buy", shares=10, price=100.0, value=1000.0),
            Trade("AAPL", "close", "sell", shares=4, price=150.0, value=600.0),
        ]
        h, realized = analyze_holdings(trades)
        aapl = h[h["ticker"] == "AAPL"].iloc[0]
        assert aapl["shares"] == pytest.approx(6.0)
        assert aapl["cost_basis"] == pytest.approx(600.0)
        assert realized[realized["ticker"] == "AAPL"]["realized_pl"].iloc[0] == pytest.approx(200.0)

    def test_full_close_drops_from_holdings_keeps_realized(self):
        trades = [
            Trade("AAPL", "open", "buy", shares=10, price=100.0, value=1000.0),
            Trade("AAPL", "close", "sell", shares=10, price=120.0, value=1200.0),
        ]
        h, realized = analyze_holdings(trades)
        assert h.empty  # fully closed -> not an open holding
        assert set(realized["ticker"]) == {"AAPL"}
        assert realized["realized_pl"].iloc[0] == pytest.approx(200.0)

    def test_full_close_keeps_other_tickers(self):
        trades = [
            Trade("AAPL", "open", "buy", shares=10, price=100.0, value=1000.0),
            Trade("AAPL", "close", "sell", shares=10, price=130.0, value=1300.0),
            Trade("MSFT", "open", "buy", shares=2, price=50.0, value=100.0),
        ]
        h, realized = analyze_holdings(trades)
        assert set(h["ticker"]) == {"MSFT"}
        assert set(realized["ticker"]) == {"AAPL"}

    def test_multi_lot_fifo(self):
        # Lot1: 5 @ 100, Lot2: 5 @ 110. Close 6 -> 5 from lot1 + 1 from lot2.
        # cost = 500 + 110 = 610. proceeds 6*120=720. realized = 110.
        trades = [
            Trade("X", "open", "buy", shares=5, price=100.0, value=500.0),
            Trade("X", "open", "buy", shares=5, price=110.0, value=550.0),
            Trade("X", "close", "sell", shares=6, price=120.0, value=720.0),
        ]
        h, realized = analyze_holdings(trades)
        x = h[h["ticker"] == "X"].iloc[0]
        assert x["shares"] == pytest.approx(4.0)
        # remaining: 4 @ 110 = 440
        assert x["cost_basis"] == pytest.approx(440.0)
        assert realized[realized["ticker"] == "X"]["realized_pl"].iloc[0] == pytest.approx(110.0)

    def test_empty(self):
        h, realized = analyze_holdings([])
        assert h.empty
        assert realized.empty


# ---------------------------------------------------------------------------
# analyze_realized
# ---------------------------------------------------------------------------
class TestAnalyzeRealized:
    def test_from_closed_positions_sheet(self):
        positions = clean_columns(
            pd.DataFrame(
                {
                    "Instrument": ["AAPL", "MSFT"],
                    "Profit/Loss": [50.0, -20.0],
                }
            )
        )
        out = analyze_realized(positions, pd.DataFrame())
        assert len(out) == 2
        assert out["realized_pl"].sum() == pytest.approx(30.0)

    def test_fallback_to_trades_realized(self):
        realized_from_trades = pd.DataFrame(
            {"ticker": ["AAPL"], "realized_pl": [200.0]}
        )
        out = analyze_realized(pd.DataFrame(), realized_from_trades)
        assert out["realized_pl"].iloc[0] == pytest.approx(200.0)


# ---------------------------------------------------------------------------
# analyze_cash_flows
# ---------------------------------------------------------------------------
class TestAnalyzeCashFlows:
    def test_categorization(self):
        ops = make_cash_ops([
            cash_row("Stock purchase", "A", -100, "OPEN BUY 1 @ 100.00"),
            cash_row("Deposit", "", 1000, "deposit"),
            cash_row("Withdrawal", "", -200, "payout"),
            cash_row("Free funds interest", "", 0.5),
            cash_row("Dividend", "A", 10.0, "Dividend"),
            cash_row("Dividend tax", "A", -1.5, "Dividend tax"),
            cash_row("Currency conversion", "", -2.0, "fx"),
        ])
        trades = extract_trades(ops)
        flows, ending = analyze_cash_flows(ops, trades)
        assert flows["deposits"] == pytest.approx(1000.0)
        assert flows["withdrawals"] == pytest.approx(200.0)
        assert flows["interest"] == pytest.approx(0.5)
        assert flows["dividends"] == pytest.approx(10.0)
        assert flows["dividend_tax"] == pytest.approx(-1.5)
        assert flows["conversion_fees"] == pytest.approx(-2.0)
        assert flows["invested"] == pytest.approx(100.0)
        # ending = 1000 - 200 + 0.5 + 10 - 1.5 - 100 + (-2) = 707
        assert ending == pytest.approx(707.0)

    def test_sale_proceeds(self):
        ops = make_cash_ops([
            cash_row("Stock purchase", "A", -100, "OPEN BUY 1 @ 100.00"),
            cash_row("Stock sale", "A", 150, "CLOSE SELL 1 @ 150.00"),
        ])
        trades = extract_trades(ops)
        flows, ending = analyze_cash_flows(ops, trades)
        assert flows["invested"] == pytest.approx(100.0)
        assert flows["proceeds"] == pytest.approx(150.0)
        assert ending == pytest.approx(50.0)

    def test_close_buy_stock_sell_counts_as_proceeds(self):
        ops = make_cash_ops([
            cash_row("Stock purchase", "A", -100.0, "OPEN BUY 1 @ 100.00"),
            cash_row("Stock sell", "A", 150.0, "CLOSE BUY 1 @ 150.00"),
        ])
        trades = extract_trades(ops)
        flows, ending = analyze_cash_flows(ops, trades)
        assert flows["invested"] == pytest.approx(100.0)
        assert flows["proceeds"] == pytest.approx(150.0)
        assert ending == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# analyze_income
# ---------------------------------------------------------------------------
class TestAnalyzeIncome:
    def test_dividends_interest_and_monthly(self):
        ops = make_cash_ops([
            cash_row("Dividend", "A", 10.0, "Dividend", "2026-01-10 09:00:00"),
            cash_row("Free funds interest", "", 0.5, "", "2026-02-01 09:00:00"),
            cash_row("Deposit", "", 1000.0, "", "2026-01-05 09:00:00"),
        ])
        dividends, interest, series = analyze_income(ops)
        assert dividends == pytest.approx(10.0)
        assert interest == pytest.approx(0.5)
        assert "2026-01" in series.index
        assert series["2026-01"] == pytest.approx(10.0)
        assert series["2026-02"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# analyze_open_positions
# ---------------------------------------------------------------------------
class TestAnalyzeOpenPositions:
    def test_aggregates_value_and_pl(self):
        op = clean_columns(
            pd.DataFrame(
                {
                    "Instrument": ["A", "A", "B"],
                    "Current Value": [100.0, 50.0, 200.0],
                    "Profit/Loss": [10.0, -5.0, 20.0],
                }
            )
        )
        out = analyze_open_positions(op)
        a = out[out["ticker"] == "A"].iloc[0]
        assert a["current_value"] == pytest.approx(150.0)
        assert a["unrealized_pl"] == pytest.approx(5.0)
        assert out["current_value"].sum() == pytest.approx(350.0)

    def test_empty(self):
        out = analyze_open_positions(pd.DataFrame())
        assert out.empty

    def test_falls_back_to_valued_holdings(self):
        # No XTB Open Positions sheet, but live-valued holdings provided.
        valued = pd.DataFrame(
            {"ticker": ["A", "B"],
             "market_value": [1200.0, 800.0],
             "unrealized_pl": [200.0, -50.0]}
        )
        out = analyze_open_positions(pd.DataFrame(), valued)
        assert not out.empty
        a = out[out["ticker"] == "A"].iloc[0]
        assert a["current_value"] == pytest.approx(1200.0)
        assert a["unrealized_pl"] == pytest.approx(200.0)
        assert out["current_value"].sum() == pytest.approx(2000.0)


# ---------------------------------------------------------------------------
# compute_performance
# ---------------------------------------------------------------------------
class TestComputePerformance:
    def test_cost_basis_mode(self):
        holdings = pd.DataFrame(
            {"ticker": ["A"], "cost_basis": [1000.0]}
        )
        flows = {
            "deposits": 1500.0, "withdrawals": 0.0, "interest": 5.0,
            "dividends": 0.0, "dividend_tax": 0.0, "conversion_fees": 0.0,
            "invested": 1000.0, "proceeds": 0.0, "fees": 0.0,
        }
        perf = compute_performance(holdings, pd.DataFrame(), pd.DataFrame(), flows, 505.0, 505.0)
        assert perf["market_value"] == pytest.approx(1000.0)  # cost basis (no live sheet)
        assert perf["unrealized_pl"] == 0.0
        assert perf["portfolio_value"] == pytest.approx(1505.0)
        assert perf["net_deposited"] == pytest.approx(1500.0)
        # total_gain = unrealized + realized + income = 0 + 0 + 5 = 5
        assert perf["total_gain"] == pytest.approx(5.0)
        assert perf["reconciliation_diff"] == pytest.approx(0.0)

    def test_live_market_value(self):
        holdings = pd.DataFrame({"ticker": ["A"], "cost_basis": [1000.0]})
        op = pd.DataFrame({"ticker": ["A"], "current_value": [1200.0], "unrealized_pl": [200.0]})
        realized = pd.DataFrame({"ticker": ["B"], "realized_pl": [50.0]})
        flows = {
            "deposits": 1000.0, "withdrawals": 0.0, "interest": 0.0,
            "dividends": 10.0, "dividend_tax": 0.0, "conversion_fees": 0.0,
            "invested": 1000.0, "proceeds": 0.0, "fees": 0.0,
        }
        perf = compute_performance(holdings, op, realized, flows, 0.0, 0.0)
        assert perf["market_value"] == pytest.approx(1200.0)
        assert perf["total_gain"] == pytest.approx(200 + 50 + 10)


# ---------------------------------------------------------------------------
# Money-weighted return / XIRR
# ---------------------------------------------------------------------------
class TestMoneyWeightedReturn:
    def test_compute_xirr_one_year_gain(self):
        flows = [
            (pd.Timestamp("2024-01-01"), -1000.0),
            (pd.Timestamp("2025-01-01"), 1100.0),
        ]
        assert compute_xirr(flows) == pytest.approx(0.10, abs=0.001)

    def test_compute_xirr_requires_positive_and_negative_flows(self):
        assert compute_xirr([(pd.Timestamp("2024-01-01"), 1000.0)]) is None
        assert compute_xirr([(pd.Timestamp("2024-01-01"), -1000.0)]) is None

    def test_build_external_cash_flows_uses_deposits_withdrawals_and_terminal_value(self):
        cash_ops = make_cash_ops([
            cash_row("Deposit", "", 1000.0, "deposit", time="2024-01-01 10:00:00"),
            cash_row("Withdrawal", "", -100.0, "withdrawal", time="2024-06-01 10:00:00"),
            cash_row("Dividend", "AAA", 5.0, "Dividend", time="2024-07-01 10:00:00"),
        ])
        flows = build_external_cash_flows(
            cash_ops, terminal_value=1200.0, terminal_date=__import__("datetime").date(2025, 1, 1)
        )
        assert flows == [
            (pd.Timestamp("2024-01-01"), -1000.0),
            (pd.Timestamp("2024-06-01"), 100.0),
            (pd.Timestamp("2025-01-01"), 1200.0),
        ]

    def test_compute_performance_includes_money_weighted_return_when_cash_ops_provided(self):
        holdings = pd.DataFrame({"ticker": ["A"], "cost_basis": [1000.0]})
        op = pd.DataFrame({"ticker": ["A"], "current_value": [1100.0], "unrealized_pl": [100.0]})
        flows = {
            "deposits": 1000.0, "withdrawals": 0.0, "interest": 0.0,
            "dividends": 0.0, "dividend_tax": 0.0, "conversion_fees": 0.0,
            "invested": 1000.0, "proceeds": 0.0, "fees": 0.0,
        }
        cash_ops = make_cash_ops([
            cash_row("Deposit", "", 1000.0, "deposit", time="2024-01-01 10:00:00"),
        ])
        perf = compute_performance(
            holdings, op, pd.DataFrame(), flows, ending_cash=0.0, broker_total=0.0,
            cash_ops=cash_ops, terminal_date=__import__("datetime").date(2025, 1, 1),
        )
        assert perf["money_weighted_return_pct"] == pytest.approx(10.0, abs=0.1)


# ---------------------------------------------------------------------------
# Portfolio analysis summary helpers
# ---------------------------------------------------------------------------
class TestPortfolioAnalysisHelpers:
    def _holdings(self):
        return pd.DataFrame(
            [
                {
                    "ticker": "AAA",
                    "name": "Alpha",
                    "market_value": 1400.0,
                    "cost_basis": 1000.0,
                    "unrealized_pl": 400.0,
                    "return_pct": 40.0,
                    "weight_pct": 70.0,
                    "price_source": "live",
                },
                {
                    "ticker": "BBB",
                    "name": "Beta",
                    "market_value": 600.0,
                    "cost_basis": 800.0,
                    "unrealized_pl": -200.0,
                    "return_pct": -25.0,
                    "weight_pct": 30.0,
                    "price_source": "cost",
                },
            ]
        )

    def _flows(self):
        return {
            "deposits": 2500.0,
            "withdrawals": 0.0,
            "interest": 5.0,
            "dividends": 10.0,
            "dividend_tax": -2.0,
            "conversion_fees": 0.0,
            "invested": 1800.0,
            "proceeds": 0.0,
            "fees": 0.0,
        }

    def _perf(self):
        return {
            "cost_basis": 1800.0,
            "market_value": 2000.0,
            "unrealized_pl": 200.0,
            "realized_pl": 50.0,
            "income": 15.0,
            "total_gain": 265.0,
            "portfolio_value": 2700.0,
            "ending_cash": 700.0,
            "net_deposited": 2500.0,
            "total_return_pct": 10.6,
            "income_yield_pct": 0.83,
            "broker_total": 700.0,
            "reconciliation_diff": 0.0,
        }

    def test_build_executive_summary_surfaces_key_observations(self):
        rows = build_executive_summary(
            self._holdings(),
            pd.DataFrame({"ticker": ["ZZZ"], "realized_pl": [50.0]}),
            self._flows(),
            self._perf(),
        )
        summary = dict(rows)
        assert summary["Largest holding"] == "AAA (70.00%)"
        assert summary["Top unrealized winner"] == "AAA (+400.00)"
        assert summary["Top unrealized loser"] == "BBB (-200.00)"
        assert summary["Cash allocation"] == "25.93%"
        assert summary["Pricing warnings"] == "1 holding priced at cost"
        assert summary["Reconciliation"] == "OK"

    def test_analyze_concentration_flags_large_positions_and_cost_pricing(self):
        risk = analyze_concentration(self._holdings(), self._perf())
        assert risk["top_1_weight_pct"] == pytest.approx(70.0)
        assert risk["top_3_weight_pct"] == pytest.approx(100.0)
        assert risk["cash_weight_pct"] == pytest.approx(700 / 2700 * 100)
        assert risk["positions_over_20_pct"] == 2
        assert risk["cost_priced_positions"] == 1
        assert risk["risk_note"] == "High concentration: top holding is 70.00%."

    def test_analyze_return_contributions_combines_unrealized_and_realized(self):
        realized = pd.DataFrame(
            {"ticker": ["AAA", "ZZZ"], "realized_pl": [25.0, 50.0]}
        )
        out = analyze_return_contributions(self._holdings(), realized, self._perf())
        aaa = out[out["Ticker"] == "AAA"].iloc[0]
        zzz = out[out["Ticker"] == "ZZZ"].iloc[0]
        assert aaa["Unrealized P/L"] == pytest.approx(400.0)
        assert aaa["Realized P/L"] == pytest.approx(25.0)
        assert aaa["Total Contribution"] == pytest.approx(425.0)
        assert aaa["Contribution %"] == pytest.approx(425 / 265 * 100)
        assert zzz["Market Value"] == pytest.approx(0.0)
        assert zzz["Total Contribution"] == pytest.approx(50.0)

    def test_analyze_income_quality_summarizes_tax_drag_and_yield(self):
        quality = analyze_income_quality(self._flows(), self._perf())
        assert quality["gross_income"] == pytest.approx(15.0)
        assert quality["dividend_tax"] == pytest.approx(2.0)
        assert quality["net_income"] == pytest.approx(13.0)
        assert quality["tax_drag_pct"] == pytest.approx(2 / 15 * 100)
        assert quality["net_income_yield_pct"] == pytest.approx(13 / 1800 * 100)
        assert quality["income_mix"] == "66.67% dividends / 33.33% interest"

    def test_analyze_methodology_quality_summarizes_pricing_and_methods(self):
        quality = analyze_methodology_quality(self._holdings(), self._perf())
        assert quality == [
            ("Pricing coverage", "1 live / 1 cost fallback"),
            ("Cost fallback tickers", "BBB"),
            ("Cash reconciliation", "OK"),
            ("Realized P/L method", "Broker closed positions preferred; FIFO fallback"),
            ("Money-weighted return", "External deposits/withdrawals plus terminal portfolio value"),
            ("Valuation caveat", "Cost fallback positions carry zero unrealized P/L"),
        ]


# ---------------------------------------------------------------------------
# Live valuation (yfinance + math)
# ---------------------------------------------------------------------------
class TestValuateHoldings:
    def _holdings(self):
        return pd.DataFrame([
            {"ticker": "A", "name": "Alpha", "shares": 10.0,
             "cost_basis": 1000.0, "avg_price": 100.0, "allocation_pct": 50.0},
            {"ticker": "B", "name": "Beta", "shares": 5.0,
             "cost_basis": 1000.0, "avg_price": 200.0, "allocation_pct": 50.0},
        ])

    def test_live_and_cost_fallback(self):
        prices = {
            "A": {"price": 120.0, "fx": 1.0, "source": "live"},
            "B": None,
        }
        out = valuate_holdings(self._holdings(), prices)
        a = out[out["ticker"] == "A"].iloc[0]
        b = out[out["ticker"] == "B"].iloc[0]
        assert a["price_source"] == "live"
        assert a["last_price"] == pytest.approx(120.0)
        assert a["market_value"] == pytest.approx(1200.0)
        assert a["unrealized_pl"] == pytest.approx(200.0)
        assert b["price_source"] == "cost"
        assert b["last_price"] == pytest.approx(200.0)
        assert b["market_value"] == pytest.approx(1000.0)
        assert b["unrealized_pl"] == 0.0

    def test_weight_pct_by_market_value(self):
        prices = {
            "A": {"price": 120.0, "fx": 1.0, "source": "live"},
            "B": None,
        }
        out = valuate_holdings(self._holdings(), prices)
        # A mv=1200, B mv=1000 -> total 2200 (weight_pct rounded to 2 dp)
        a = out[out["ticker"] == "A"].iloc[0]
        b = out[out["ticker"] == "B"].iloc[0]
        assert a["weight_pct"] == pytest.approx(1200 / 2200 * 100, abs=0.01)
        assert b["weight_pct"] == pytest.approx(1000 / 2200 * 100, abs=0.01)
        assert out["weight_pct"].sum() == pytest.approx(100.0)

    def test_fx_conversion_applied(self):
        prices = {
            "A": {"price": 100.0, "fx": 0.9, "source": "live"},  # EUR-priced acnt
            "B": None,
        }
        out = valuate_holdings(self._holdings(), prices)
        a = out[out["ticker"] == "A"].iloc[0]
        assert a["last_price"] == pytest.approx(90.0)
        assert a["market_value"] == pytest.approx(900.0)

    def test_return_pct_computed(self):
        # A live-priced at +20% (mv 1200 vs cost 1000); B cost-fallback -> 0%.
        prices = {
            "A": {"price": 120.0, "fx": 1.0, "source": "live"},
            "B": None,
        }
        out = valuate_holdings(self._holdings(), prices)
        a = out[out["ticker"] == "A"].iloc[0]
        b = out[out["ticker"] == "B"].iloc[0]
        assert a["return_pct"] == pytest.approx(20.0)
        assert b["return_pct"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# _df_to_html per-column coloring
# ---------------------------------------------------------------------------
class TestDfToHtmlColoring:
    def test_colored_cols_get_pos_and_neg_classes(self):
        df = pd.DataFrame({"Name": ["A", "B"], "Return %": [5.0, -3.0]})
        html = main._df_to_html(df, {"Return %": ".2f"}, colored_cols={"Return %"})
        assert "class='pos'" in html
        assert "class='neg'" in html

    def test_non_colored_positive_value_no_pos_class(self):
        df = pd.DataFrame({"Shares": [10], "Return %": [5.0]})
        html = main._df_to_html(df, colored_cols={"Return %"})
        # Shares column is positive but not in colored_cols -> no pos class for it.
        # The Return % cell does get pos. Count exactly one 'pos'.
        assert html.count("class='pos'") == 1
        assert html.count("class='neg'") == 0

    def test_data_tables_are_marked_sortable(self):
        df = pd.DataFrame({"Ticker": ["B", "A"], "Market Value": [2.0, 10.0]})
        html = main._df_to_html(df)
        assert "<table class='data-table'>" in html
        assert "data-sortable='1'" in html

    def test_data_table_headers_include_term_tooltips(self):
        df = pd.DataFrame({"Unrealized P/L": [10.0], "Plain": ["x"]})
        html = main._df_to_html(df)
        assert "class='term-help'" in html
        assert "class='term-tip'" in html
        assert "Profit or loss on positions you still hold" in html
        assert "<th data-sortable='1' tabindex='0' aria-sort='none'>Plain</th>" in html


class TestFetchPrices:
    def test_returns_none_when_yfinance_missing(self, monkeypatch):
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *a, **k):
            if name == "yfinance":
                raise ImportError("no yfinance")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        main._PRICE_CACHE.clear()
        out = main.fetch_prices(["SPYL.DE"], __import__("datetime").date(2026, 6, 20), "EUR")
        assert out["SPYL.DE"] is None

    def test_uses_mocked_yfinance(self, monkeypatch):
        import datetime as dt
        main._PRICE_CACHE.clear()

        class FakeHist(dict):
            @property
            def empty(self):
                return False
            def __getitem__(self, k):
                return {"close": {0: 16.0}}
            @property
            def columns(self):
                return ["Close"]
            def __iter__(self):
                return iter([])
            def keys(self):
                return []
            @property
            def index(self):
                idx = pd.DatetimeIndex(["2026-06-19"]).tz_localize("Europe/Berlin")
                return idx
            def loc(self, *a, **k):
                return self

        class FakeTicker:
            def __init__(self, sym):
                self.sym = sym
            def history(self, **kw):
                df = pd.DataFrame(
                    {"Close": [16.0, 16.1, 16.15]},
                    index=pd.DatetimeIndex(
                        ["2026-06-17", "2026-06-18", "2026-06-19"]
                    ).tz_localize("Europe/Berlin"),
                )
                return df
            @property
            def fast_info(self):
                return {"currency": "EUR"}

        class FakeYF:
            def Ticker(self, sym):
                return FakeTicker(sym)

        monkeypatch.setattr(main, "_yf", lambda: FakeYF())
        out = main.fetch_prices(["SPYL.DE"], dt.date(2026, 6, 20), "EUR")
        info = out["SPYL.DE"]
        assert info is not None
        assert info["price"] == pytest.approx(16.15)
        assert info["currency"] == "EUR"
        assert info["fx"] == pytest.approx(1.0)
        assert info["as_of"] == dt.date(2026, 6, 19)
        assert info["source"] == "live"

    def test_failed_yfinance_lookup_returns_none_without_raising(self, monkeypatch):
        import datetime as dt
        main._PRICE_CACHE.clear()

        class FakeTicker:
            @property
            def fast_info(self):
                return {"currency": "EUR"}

            def history(self, **kw):
                raise RuntimeError("network unavailable")

        class FakeYF:
            def Ticker(self, sym):
                return FakeTicker()

        monkeypatch.setattr(main, "_yf", lambda: FakeYF())
        out = main.fetch_prices(["SPYL.DE"], dt.date(2026, 6, 20), "EUR")
        assert out["SPYL.DE"] is None


# ---------------------------------------------------------------------------
# Integration against the synthetic report file
# ---------------------------------------------------------------------------
class TestSyntheticReport:
    def test_reconciliation_matches_broker_total(self):
        _, cash_ops, _, broker_total = main.load_data()
        trades = extract_trades(cash_ops)
        flows, ending = analyze_cash_flows(cash_ops, trades)
        assert broker_total == pytest.approx(748.5)
        assert ending == pytest.approx(broker_total, abs=0.01)
        assert flows["deposits"] == pytest.approx(1000.0)
        assert flows["dividends"] == pytest.approx(10.0)
        assert flows["dividend_tax"] == pytest.approx(-1.5)
        assert flows["invested"] == pytest.approx(500.0)
        assert flows["proceeds"] == pytest.approx(240.0)

    def test_holdings_keyed_by_real_ticker(self):
        _, cash_ops, _, _ = main.load_data()
        holdings, _ = analyze_holdings(extract_trades(cash_ops))
        assert set(holdings["ticker"]) == {"DEMO.DE"}
        assert holdings.loc[0, "shares"] == pytest.approx(3.0)
        assert holdings["cost_basis"].sum() == pytest.approx(300.0)
        assert "name" in holdings.columns
        assert holdings["allocation_pct"].sum() == pytest.approx(100.0, abs=0.05)


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------
class TestHtmlReport:
    def _minimal_perf(self):
        return {
            "cost_basis": 1000.0, "market_value": 1000.0, "unrealized_pl": 0.0,
            "realized_pl": 0.0, "income": 0.01, "total_gain": 0.01,
            "portfolio_value": 1000.0, "ending_cash": 0.0, "net_deposited": 1000.0,
            "total_return_pct": 0.0, "income_yield_pct": 0.0,
            "broker_total": 0.0, "reconciliation_diff": 0.0,
        }

    def test_build_html_is_self_contained(self):
        holdings = pd.DataFrame(
            {"ticker": ["A"], "name": ["Alpha"], "shares": [10],
             "avg_price": [100.0], "cost_basis": [1000.0],
             "allocation_pct": [100.0], "last_price": [120.0],
             "market_value": [1200.0], "unrealized_pl": [200.0],
             "weight_pct": [100.0], "price_source": ["live"]}
        )
        review_cfg = main.html_charts.review_charts_config(
            holdings,
            {"deposits": 1000.0, "withdrawals": 0.0, "interest": 0.01,
             "dividends": 0.0, "dividend_tax": 0.0, "conversion_fees": 0.0,
             "invested": 1000.0, "proceeds": 0.0, "fees": 0.0},
            pd.Series(dtype=float), "EUR")
        html = main.build_html_report(
            "EUR", {"account": "1", "period_from": "x", "period_to": "y"},
            {"deposits": 1000.0, "withdrawals": 0.0, "interest": 0.01,
             "dividends": 0.0, "dividend_tax": 0.0, "conversion_fees": 0.0,
             "invested": 1000.0, "proceeds": 0.0, "fees": 0.0},
            0.0, holdings, pd.DataFrame(columns=["ticker", "current_value", "unrealized_pl"]),
            pd.DataFrame(columns=["ticker", "realized_pl"]),
            self._minimal_perf(), None, review_cfg,
        )
        assert html.startswith("<!DOCTYPE html>")
        assert "data:image/png;base64" not in html
        assert "<canvas" in html
        assert main.html_charts.load_chartjs_inline()[:200] in html
        assert "<table" in html
        assert "Portfolio Review" in html
        assert "live prices via yfinance" in html

    def test_evolution_chart_embedded_when_provided(self):
        holdings = pd.DataFrame(
            {"ticker": ["A"], "name": ["Alpha"], "shares": [10],
             "avg_price": [100.0], "cost_basis": [1000.0],
             "allocation_pct": [100.0], "last_price": [120.0],
             "market_value": [1200.0], "unrealized_pl": [200.0],
             "return_pct": [20.0], "weight_pct": [100.0], "price_source": ["live"]}
        )
        evolution_cfg = main.html_charts.evolution_chart_config(
            pd.DataFrame(
                {"cost": [1000.0], "realized_pl": [0.0], "total_value": [1100.0]},
                index=pd.to_datetime(["2024-01-01"]),
            ),
            "EUR")
        review_cfg = main.html_charts.review_charts_config(
            holdings,
            {"deposits": 1000.0, "withdrawals": 0.0, "interest": 0.0,
             "dividends": 0.0, "dividend_tax": 0.0, "conversion_fees": 0.0,
             "invested": 1000.0, "proceeds": 0.0, "fees": 0.0},
            pd.Series(dtype=float), "EUR")
        html = main.build_html_report(
            "EUR", {"account": "1", "period_from": "x", "period_to": "y"},
            {"deposits": 1000.0, "withdrawals": 0.0, "interest": 0.0,
             "dividends": 0.0, "dividend_tax": 0.0, "conversion_fees": 0.0,
             "invested": 1000.0, "proceeds": 0.0, "fees": 0.0},
            0.0, holdings, pd.DataFrame(columns=["ticker", "current_value", "unrealized_pl"]),
            pd.DataFrame(columns=["ticker", "realized_pl"]),
            self._minimal_perf(), evolution_cfg, review_cfg,
        )
        assert "Portfolio Evolution" in html
        assert "<canvas id='evolution-chart'></canvas>" in html
        assert "data:image/png;base64" not in html

    def test_return_pct_column_in_holdings_html(self):
        holdings = pd.DataFrame(
            {"ticker": ["A", "B"], "name": ["Alpha", "Beta"], "shares": [10, 5],
             "avg_price": [100.0, 200.0], "cost_basis": [1000.0, 1000.0],
             "allocation_pct": [50.0, 50.0], "last_price": [120.0, 200.0],
             "market_value": [1200.0, 1000.0], "unrealized_pl": [200.0, 0.0],
             "return_pct": [20.0, 0.0], "weight_pct": [54.5, 45.5],
             "price_source": ["live", "cost"]}
        )
        review_cfg = main.html_charts.review_charts_config(
            holdings,
            {"deposits": 1000.0, "withdrawals": 0.0, "interest": 0.0,
             "dividends": 0.0, "dividend_tax": 0.0, "conversion_fees": 0.0,
             "invested": 1000.0, "proceeds": 0.0, "fees": 0.0},
            pd.Series(dtype=float), "EUR")
        html = main.build_html_report(
            "EUR", {"account": "1", "period_from": "x", "period_to": "y"},
            {"deposits": 1000.0, "withdrawals": 0.0, "interest": 0.0,
             "dividends": 0.0, "dividend_tax": 0.0, "conversion_fees": 0.0,
             "invested": 1000.0, "proceeds": 0.0, "fees": 0.0},
            0.0, holdings, pd.DataFrame(columns=["ticker", "current_value", "unrealized_pl"]),
            pd.DataFrame(columns=["ticker", "realized_pl"]),
            self._minimal_perf(), None, review_cfg,
        )
        assert "Return %" in html
        # A's +20% return gets a pos (green) class.
        assert "class='pos'" in html

    def test_html_includes_analysis_upgrade_sections(self):
        holdings = pd.DataFrame(
            {"ticker": ["A", "B"], "name": ["Alpha", "Beta"], "shares": [10, 5],
             "avg_price": [100.0, 200.0], "cost_basis": [1000.0, 1000.0],
             "allocation_pct": [50.0, 50.0], "last_price": [140.0, 160.0],
             "market_value": [1400.0, 800.0], "unrealized_pl": [400.0, -200.0],
             "return_pct": [40.0, -20.0], "weight_pct": [63.64, 36.36],
             "price_source": ["live", "cost"]}
        )
        flows = {"deposits": 3000.0, "withdrawals": 0.0, "interest": 5.0,
                 "dividends": 10.0, "dividend_tax": -2.0, "conversion_fees": 0.0,
                 "invested": 2000.0, "proceeds": 0.0, "fees": 0.0}
        perf = {
            "cost_basis": 2000.0, "market_value": 2200.0, "unrealized_pl": 200.0,
            "realized_pl": 0.0, "income": 15.0, "total_gain": 215.0,
            "portfolio_value": 3200.0, "ending_cash": 1000.0,
            "net_deposited": 3000.0, "total_return_pct": 7.17,
            "income_yield_pct": 0.75, "broker_total": 1000.0,
            "reconciliation_diff": 0.0,
        }
        review_cfg = main.html_charts.review_charts_config(
            holdings, flows, pd.Series(dtype=float), "EUR")
        html = main.build_html_report(
            "EUR", {"account": "1", "period_from": "x", "period_to": "y"},
            flows, 1000.0, holdings,
            pd.DataFrame(columns=["ticker", "current_value", "unrealized_pl"]),
            pd.DataFrame(columns=["ticker", "realized_pl"]),
            perf, None, review_cfg,
        )
        assert "Executive Summary" in html
        assert "Concentration &amp; Risk" in html
        assert "Return Contribution" in html
        assert "Largest holding" in html
        assert "High concentration" in html

    def test_html_includes_money_weighted_return(self):
        holdings = pd.DataFrame(
            {"ticker": ["A"], "name": ["Alpha"], "shares": [10],
             "avg_price": [100.0], "cost_basis": [1000.0],
             "allocation_pct": [100.0], "last_price": [110.0],
             "market_value": [1100.0], "unrealized_pl": [100.0],
             "return_pct": [10.0], "weight_pct": [100.0],
             "price_source": ["live"]}
        )
        flows = {"deposits": 1000.0, "withdrawals": 0.0, "interest": 0.0,
                 "dividends": 0.0, "dividend_tax": 0.0, "conversion_fees": 0.0,
                 "invested": 1000.0, "proceeds": 0.0, "fees": 0.0}
        perf = self._minimal_perf() | {"money_weighted_return_pct": 10.0}
        review_cfg = main.html_charts.review_charts_config(
            holdings, flows, pd.Series(dtype=float), "EUR")
        html = main.build_html_report(
            "EUR", {"account": "1", "period_from": "x", "period_to": "y"},
            flows, 0.0, holdings,
            pd.DataFrame(columns=["ticker", "current_value", "unrealized_pl"]),
            pd.DataFrame(columns=["ticker", "realized_pl"]),
            perf, None, review_cfg,
        )
        assert "Money-weighted return" in html
        assert "+10.00 %" in html

    def test_html_includes_income_quality_section(self):
        holdings = pd.DataFrame(
            {"ticker": ["A"], "name": ["Alpha"], "shares": [10],
             "avg_price": [100.0], "cost_basis": [1000.0],
             "allocation_pct": [100.0], "last_price": [110.0],
             "market_value": [1100.0], "unrealized_pl": [100.0],
             "return_pct": [10.0], "weight_pct": [100.0],
             "price_source": ["live"]}
        )
        flows = {"deposits": 1000.0, "withdrawals": 0.0, "interest": 5.0,
                 "dividends": 10.0, "dividend_tax": -2.0, "conversion_fees": 0.0,
                 "invested": 1000.0, "proceeds": 0.0, "fees": 0.0}
        perf = self._minimal_perf() | {
            "cost_basis": 1000.0,
            "income": 15.0,
            "income_yield_pct": 1.5,
            "money_weighted_return_pct": None,
        }
        review_cfg = main.html_charts.review_charts_config(
            holdings, flows, pd.Series(dtype=float), "EUR")
        html = main.build_html_report(
            "EUR", {"account": "1", "period_from": "x", "period_to": "y"},
            flows, 0.0, holdings,
            pd.DataFrame(columns=["ticker", "current_value", "unrealized_pl"]),
            pd.DataFrame(columns=["ticker", "realized_pl"]),
            perf, None, review_cfg,
        )
        assert "Income Quality" in html
        assert "Tax drag" in html
        assert "Net income yield" in html

    def test_html_includes_methodology_data_quality_section(self):
        holdings = pd.DataFrame(
            {"ticker": ["A", "B"], "name": ["Alpha", "Beta"], "shares": [10, 5],
             "avg_price": [100.0, 200.0], "cost_basis": [1000.0, 1000.0],
             "allocation_pct": [50.0, 50.0], "last_price": [140.0, 200.0],
             "market_value": [1400.0, 1000.0], "unrealized_pl": [400.0, 0.0],
             "return_pct": [40.0, 0.0], "weight_pct": [58.33, 41.67],
             "price_source": ["live", "cost"]}
        )
        flows = {"deposits": 3000.0, "withdrawals": 0.0, "interest": 0.0,
                 "dividends": 0.0, "dividend_tax": 0.0, "conversion_fees": 0.0,
                 "invested": 2000.0, "proceeds": 0.0, "fees": 0.0}
        perf = self._minimal_perf() | {"reconciliation_diff": 0.0}
        review_cfg = main.html_charts.review_charts_config(
            holdings, flows, pd.Series(dtype=float), "EUR")
        html = main.build_html_report(
            "EUR", {"account": "1", "period_from": "x", "period_to": "y"},
            flows, 0.0, holdings,
            pd.DataFrame(columns=["ticker", "current_value", "unrealized_pl"]),
            pd.DataFrame(columns=["ticker", "realized_pl"]),
            perf, None, review_cfg,
        )
        assert "Methodology &amp; Data Quality" in html
        assert "Pricing coverage" in html
        assert "1 live / 1 cost fallback" in html
        assert "Cost fallback positions carry zero unrealized P/L" in html

    def test_html_embeds_sortable_table_script(self):
        holdings = pd.DataFrame(
            {"ticker": ["B", "A"], "name": ["Beta", "Alpha"], "shares": [5, 10],
             "avg_price": [200.0, 100.0], "cost_basis": [1000.0, 1000.0],
             "allocation_pct": [50.0, 50.0], "last_price": [200.0, 110.0],
             "market_value": [1000.0, 1100.0], "unrealized_pl": [0.0, 100.0],
             "return_pct": [0.0, 10.0], "weight_pct": [47.62, 52.38],
             "price_source": ["cost", "live"]}
        )
        flows = {"deposits": 2000.0, "withdrawals": 0.0, "interest": 0.0,
                 "dividends": 0.0, "dividend_tax": 0.0, "conversion_fees": 0.0,
                 "invested": 2000.0, "proceeds": 0.0, "fees": 0.0}
        review_cfg = main.html_charts.review_charts_config(
            holdings, flows, pd.Series(dtype=float), "EUR")
        html = main.build_html_report(
            "EUR", {"account": "1", "period_from": "x", "period_to": "y"},
            flows, 0.0, holdings,
            pd.DataFrame(columns=["ticker", "current_value", "unrealized_pl"]),
            pd.DataFrame(columns=["ticker", "realized_pl"]),
            self._minimal_perf(), None, review_cfg,
        )
        assert "function _bootSortableTables()" in html
        assert "data-sortable='1'" in html
        assert "aria-sort" in html

    def test_html_embeds_table_filter_script(self):
        holdings = pd.DataFrame(
            {"ticker": ["A", "B"], "name": ["Alpha", "Beta"], "shares": [10, 5],
             "avg_price": [100.0, 200.0], "cost_basis": [1000.0, 1000.0],
             "allocation_pct": [50.0, 50.0], "last_price": [110.0, 200.0],
             "market_value": [1100.0, 1000.0], "unrealized_pl": [100.0, 0.0],
             "return_pct": [10.0, 0.0], "weight_pct": [52.38, 47.62],
             "price_source": ["live", "cost"]}
        )
        flows = {"deposits": 2000.0, "withdrawals": 0.0, "interest": 0.0,
                 "dividends": 0.0, "dividend_tax": 0.0, "conversion_fees": 0.0,
                 "invested": 2000.0, "proceeds": 0.0, "fees": 0.0}
        review_cfg = main.html_charts.review_charts_config(
            holdings, flows, pd.Series(dtype=float), "EUR")
        html = main.build_html_report(
            "EUR", {"account": "1", "period_from": "x", "period_to": "y"},
            flows, 0.0, holdings,
            pd.DataFrame(columns=["ticker", "current_value", "unrealized_pl"]),
            pd.DataFrame(columns=["ticker", "realized_pl"]),
            self._minimal_perf(), None, review_cfg,
        )
        assert "function _bootTableFilters()" in html
        assert "table-filter" in html
        assert "Filter table" in html

    def test_html_includes_sticky_section_navigation(self):
        holdings = pd.DataFrame(
            {"ticker": ["A"], "name": ["Alpha"], "shares": [10],
             "avg_price": [100.0], "cost_basis": [1000.0],
             "allocation_pct": [100.0], "last_price": [110.0],
             "market_value": [1100.0], "unrealized_pl": [100.0],
             "return_pct": [10.0], "weight_pct": [100.0],
             "price_source": ["live"]}
        )
        flows = {"deposits": 1000.0, "withdrawals": 0.0, "interest": 0.0,
                 "dividends": 0.0, "dividend_tax": 0.0, "conversion_fees": 0.0,
                 "invested": 1000.0, "proceeds": 0.0, "fees": 0.0}
        review_cfg = main.html_charts.review_charts_config(
            holdings, flows, pd.Series(dtype=float), "EUR")
        html = main.build_html_report(
            "EUR", {"account": "1", "period_from": "x", "period_to": "y"},
            flows, 0.0, holdings,
            pd.DataFrame(columns=["ticker", "current_value", "unrealized_pl"]),
            pd.DataFrame(columns=["ticker", "realized_pl"]),
            self._minimal_perf(), None, review_cfg,
        )
        assert "<nav class='section-nav' aria-label='Report sections'>" in html
        assert "href='#summary'" in html
        assert "href='#holdings'" in html
        assert "href='#performance'" in html
        assert "id='summary'" in html
        assert "id='charts'" in html
        assert "id='holdings'" in html
        assert "id='performance'" in html

    def test_html_includes_print_stylesheet(self):
        holdings = pd.DataFrame(
            {"ticker": ["A"], "name": ["Alpha"], "shares": [10],
             "avg_price": [100.0], "cost_basis": [1000.0],
             "allocation_pct": [100.0], "last_price": [110.0],
             "market_value": [1100.0], "unrealized_pl": [100.0],
             "return_pct": [10.0], "weight_pct": [100.0],
             "price_source": ["live"]}
        )
        flows = {"deposits": 1000.0, "withdrawals": 0.0, "interest": 0.0,
                 "dividends": 0.0, "dividend_tax": 0.0, "conversion_fees": 0.0,
                 "invested": 1000.0, "proceeds": 0.0, "fees": 0.0}
        review_cfg = main.html_charts.review_charts_config(
            holdings, flows, pd.Series(dtype=float), "EUR")
        html = main.build_html_report(
            "EUR", {"account": "1", "period_from": "x", "period_to": "y"},
            flows, 0.0, holdings,
            pd.DataFrame(columns=["ticker", "current_value", "unrealized_pl"]),
            pd.DataFrame(columns=["ticker", "realized_pl"]),
            self._minimal_perf(), None, review_cfg,
        )
        assert "@media print" in html
        assert ".section-nav { display:none;" in html
        assert "break-inside:avoid" in html
        assert "box-shadow:none" in html

    def test_html_includes_finance_term_tooltips(self):
        holdings = pd.DataFrame(
            {"ticker": ["A"], "name": ["Alpha"], "shares": [10],
             "avg_price": [100.0], "cost_basis": [1000.0],
             "allocation_pct": [100.0], "last_price": [110.0],
             "market_value": [1100.0], "unrealized_pl": [100.0],
             "return_pct": [10.0], "weight_pct": [100.0],
             "price_source": ["cost"]}
        )
        flows = {"deposits": 1000.0, "withdrawals": 0.0, "interest": 0.0,
                 "dividends": 0.0, "dividend_tax": 0.0, "conversion_fees": 0.0,
                 "invested": 1000.0, "proceeds": 0.0, "fees": 0.0}
        review_cfg = main.html_charts.review_charts_config(
            holdings, flows, pd.Series(dtype=float), "EUR")
        html = main.build_html_report(
            "EUR", {"account": "1", "period_from": "x", "period_to": "y"},
            flows, 0.0, holdings,
            pd.DataFrame(columns=["ticker", "current_value", "unrealized_pl"]),
            pd.DataFrame(columns=["ticker", "realized_pl"]),
            self._minimal_perf(), None, review_cfg,
        )
        assert "What your portfolio is worth after including market value and cash" in html
        assert "answers: how did my money do, considering the dates I added or withdrew cash" in html
        assert "Tickers valued at cost because a trusted live price was unavailable" in html
        assert "aria-describedby='term-tip-" in html
        assert "class='term-icon'" in html
        assert "class='term-tip'" in html
        assert "title='" not in html

    def test_html_includes_beginner_guide_with_plain_language_explanations(self):
        holdings = pd.DataFrame(
            {"ticker": ["A"], "name": ["Alpha"], "shares": [10],
             "avg_price": [100.0], "cost_basis": [1000.0],
             "allocation_pct": [100.0], "last_price": [110.0],
             "market_value": [1100.0], "unrealized_pl": [100.0],
             "return_pct": [10.0], "weight_pct": [100.0],
             "price_source": ["cost"]}
        )
        flows = {"deposits": 1000.0, "withdrawals": 0.0, "interest": 0.0,
                 "dividends": 0.0, "dividend_tax": 0.0, "conversion_fees": 0.0,
                 "invested": 1000.0, "proceeds": 0.0, "fees": 0.0}
        review_cfg = main.html_charts.review_charts_config(
            holdings, flows, pd.Series(dtype=float), "EUR")
        html = main.build_html_report(
            "EUR", {"account": "1", "period_from": "x", "period_to": "y"},
            flows, 0.0, holdings,
            pd.DataFrame(columns=["ticker", "current_value", "unrealized_pl"]),
            pd.DataFrame(columns=["ticker", "realized_pl"]),
            self._minimal_perf(), None, review_cfg,
        )
        assert "Beginner Guide" in html
        assert "estimated selling value" in html
        assert "Unrealized profit is only a paper gain until you sell." in html
        assert "Money-weighted return is useful when you added money at different times." in html
        assert "cost fallback means the report could not find a trusted live price" in html

    def test_html_explains_more_page_terms(self):
        holdings = pd.DataFrame(
            {"ticker": ["A"], "name": ["Alpha"], "shares": [10],
             "avg_price": [100.0], "cost_basis": [1000.0],
             "allocation_pct": [100.0], "last_price": [110.0],
             "market_value": [1100.0], "unrealized_pl": [100.0],
             "return_pct": [10.0], "weight_pct": [100.0],
             "price_source": ["cost"]}
        )
        flows = {"deposits": 1000.0, "withdrawals": 0.0, "interest": 0.0,
                 "dividends": 0.0, "dividend_tax": 0.0, "conversion_fees": 0.0,
                 "invested": 1000.0, "proceeds": 0.0, "fees": 0.0}
        perf = self._minimal_perf() | {
            "broker_total": 1.0,
            "reconciliation_diff": -1.0,
        }
        review_cfg = main.html_charts.review_charts_config(
            holdings, flows, pd.Series(dtype=float), "EUR")
        html = main.build_html_report(
            "EUR", {"account": "1", "period_from": "x", "period_to": "y"},
            flows, 0.0, holdings,
            pd.DataFrame(columns=["ticker", "current_value", "unrealized_pl"]),
            pd.DataFrame({"ticker": ["A"], "realized_pl": [5.0]}),
            perf, None, review_cfg,
        )
        assert "A ticker is the short code used by markets and brokers" in html
        assert "realized_pl means realized profit or loss" in html
        assert "Computed ending cash is the cash balance calculated from all cash operations" in html
        assert "Broker &#x27;Total&#x27; (cash) is the cash total reported by XTB" in html
        assert "Difference shows computed cash minus broker-reported cash" in html
        assert "Status tells you whether the reconciliation check passed" in html


# ---------------------------------------------------------------------------
# fetch_price_history (mocked yfinance)
# ---------------------------------------------------------------------------
class TestFetchPriceHistory:
    def test_returns_none_when_yfinance_missing(self, monkeypatch):
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *a, **k):
            if name == "yfinance":
                raise ImportError("no yfinance")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        main._PRICE_HISTORY_CACHE.clear()
        import datetime as dt
        out = main.fetch_price_history(
            ["SPYL.DE"], dt.date(2026, 1, 1), dt.date(2026, 1, 10), "EUR"
        )
        assert out["SPYL.DE"] is None

    def test_uses_mocked_yfinance(self, monkeypatch):
        import datetime as dt
        main._PRICE_HISTORY_CACHE.clear()

        class FakeTicker:
            def __init__(self, sym):
                self.sym = sym

            def history(self, **kw):
                idx = pd.DatetimeIndex(["2026-01-01", "2026-01-02", "2026-01-03"]).tz_localize("Europe/Berlin")
                return pd.DataFrame({"Close": [100.0, 105.0, 110.0]}, index=idx)

            @property
            def fast_info(self):
                return {"currency": "EUR"}

        class FakeYF:
            def Ticker(self, sym):
                return FakeTicker(sym)

        monkeypatch.setattr(main, "_yf", lambda: FakeYF())
        out = main.fetch_price_history(
            ["SPYL.DE"], dt.date(2026, 1, 1), dt.date(2026, 1, 3), "EUR"
        )
        series = out["SPYL.DE"]
        assert series is not None
        assert series.iloc[-1] == pytest.approx(110.0)
        assert len(series) == 3


# ---------------------------------------------------------------------------
# build_evolution_series (replay logic)
# ---------------------------------------------------------------------------
class TestBuildEvolutionSeries:
    def _series(self, dates, closes, name="A"):
        idx = pd.DatetimeIndex(dates)
        return pd.Series(closes, index=idx, name=name)

    def test_buy_and_hold_with_rising_prices(self):
        trades = [Trade("A", "open", "buy", shares=10, price=100.0, value=1000.0,
                        date=pd.Timestamp("2026-01-01"))]
        prices = {"A": self._series(["2026-01-01", "2026-01-02", "2026-01-03"],
                                     [100.0, 110.0, 120.0])}
        out = build_evolution_series(trades, prices, __import__("datetime").date(2026, 1, 3))
        assert len(out) == 3
        assert out["cost"].iloc[0] == pytest.approx(1000.0)
        assert out["market_value"].iloc[0] == pytest.approx(1000.0)  # close 100
        assert out["market_value"].iloc[1] == pytest.approx(1100.0)  # close 110
        assert out["market_value"].iloc[2] == pytest.approx(1200.0)  # close 120
        assert (out["realized_pl"] == 0.0).all()
        assert out["total_value"].iloc[2] == pytest.approx(1200.0)

    def test_partial_close_realizes_pl(self):
        # Buy 10 @ 100 on Jan 1; close 4 @ 150 on Jan 2 -> realized 200.
        trades = [
            Trade("A", "open", "buy", shares=10, price=100.0, value=1000.0,
                  date=pd.Timestamp("2026-01-01")),
            Trade("A", "close", "sell", shares=4, price=150.0, value=600.0,
                  date=pd.Timestamp("2026-01-02")),
        ]
        prices = {"A": self._series(["2026-01-01", "2026-01-02", "2026-01-03"],
                                     [100.0, 150.0, 150.0])}
        out = build_evolution_series(trades, prices, __import__("datetime").date(2026, 1, 3))
        # Jan 1: 10 shares, no realized.
        assert out["cost"].iloc[0] == pytest.approx(1000.0)
        assert out["realized_pl"].iloc[0] == pytest.approx(0.0)
        # Jan 2: 6 shares left @ 100 = 600 cost; mv 6*150=900; realized 200.
        assert out["cost"].iloc[1] == pytest.approx(600.0)
        assert out["market_value"].iloc[1] == pytest.approx(900.0)
        assert out["realized_pl"].iloc[1] == pytest.approx(200.0)
        assert out["total_value"].iloc[1] == pytest.approx(1100.0)
        # Jan 3: same as Jan 2.
        assert out["total_value"].iloc[2] == pytest.approx(1100.0)

    def test_cost_fallback_ticker_held_at_cost(self):
        trades = [Trade("A", "open", "buy", shares=10, price=100.0, value=1000.0,
                        date=pd.Timestamp("2026-01-01"))]
        # No price series -> market value should equal cost (unrealized 0).
        out = build_evolution_series(trades, {}, __import__("datetime").date(2026, 1, 3))
        assert (out["market_value"] == out["cost"]).all()
        assert (out["market_value"] == 1000.0).all()

    def test_empty_when_no_dated_trades(self):
        out = build_evolution_series([], {}, __import__("datetime").date(2026, 1, 3))
        assert out.empty
