import html_charts

import json
import re


def test_load_chartjs_inline_returns_bundle():
    src = html_charts.load_chartjs_inline()
    assert isinstance(src, str)
    assert len(src) > 100000  # minified UMD is ~200 KB
    assert "Chart" in src  # Chart.js UMD defines Chart


def test_load_chartjs_inline_missing_file_raises(tmp_path, monkeypatch):
    import pytest
    missing = tmp_path / "nope.js"
    monkeypatch.setattr(html_charts, "CHARTJS_PATH", missing)
    with pytest.raises(FileNotFoundError, match=r"(?i)(assets|chart)"):
        html_charts.load_chartjs_inline()


import pandas as pd


def _evolution_df():
    idx = pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01"])
    return pd.DataFrame(
        {"cost": [1000.0, 1000.0, 1000.0],
         "realized_pl": [0.0, 10.0, 20.0],
         "total_value": [1000.0, 1050.0, 1080.0]},
        index=idx,
    )


def test_evolution_chart_config_empty_returns_none():
    assert html_charts.evolution_chart_config(pd.DataFrame(), "EUR") is None
    assert html_charts.evolution_chart_config(None, "EUR") is None


def test_evolution_chart_config_builds_line_chart():
    cfg = html_charts.evolution_chart_config(_evolution_df(), "EUR")
    assert cfg["type"] == "line"
    assert cfg["data"]["labels"] == ["2024-01-01", "2024-02-01", "2024-03-01"]
    ds = cfg["data"]["datasets"]
    assert len(ds) == 3
    assert ds[0]["label"] == "Cost (invested)"
    assert ds[0]["borderColor"] == "#6b7280"
    assert ds[1]["label"] == "Value (realized + unrealized)"
    assert ds[1]["borderColor"] == "#2c5282"
    assert ds[2]["label"] == "Cumulative realized P/L"
    assert ds[2]["borderColor"] == "#f39c12"
    assert ds[2]["borderDash"] == [6, 4]
    assert cfg["options"]["responsive"] is True
    assert cfg["options"]["maintainAspectRatio"] is False


def _holdings_df():
    return pd.DataFrame(
        {"ticker": ["A", "B", "C"],
         "name": ["Alpha", "Beta", "Gamma"],
         "market_value": [1200.0, 0.0, 800.0]}
    )


def _flows():
    return {"deposits": 1000.0, "withdrawals": 50.0, "interest": 0.0,
            "dividends": 4.0, "dividend_tax": 0.0, "conversion_fees": 0.0,
            "invested": 1500.0, "proceeds": 0.0, "fees": 1.0}


def _empty_flows():
    return {"deposits": 0.0, "withdrawals": 0.0, "interest": 0.0,
            "dividends": 0.0, "dividend_tax": 0.0, "conversion_fees": 0.0,
            "invested": 0.0, "proceeds": 0.0, "fees": 0.0}


def test_review_holdings_doughnut_filters_zero():
    cfg = html_charts.review_charts_config(
        _holdings_df(), _flows(), pd.Series(dtype=float), "EUR")
    h = cfg["holdings"]
    assert h["type"] == "doughnut"
    assert h["data"]["labels"] == ["A", "C"]  # B has market_value 0, dropped
    assert h["data"]["datasets"][0]["data"] == [1200.0, 800.0]
    assert len(h["data"]["datasets"][0]["backgroundColor"]) == len(h["data"]["datasets"][0]["data"])


def test_review_cashflows_signed_and_filtered():
    cfg = html_charts.review_charts_config(
        _holdings_df(), _flows(), pd.Series(dtype=float), "EUR")
    cf = cfg["cashflows"]
    assert cf["type"] == "bar"
    items = dict(zip(cf["data"]["labels"], cf["data"]["datasets"][0]["data"]))
    assert items["Withdrawals"] == -50.0   # negated
    assert items["Invested"] == -1500.0    # negated
    assert items["Fees"] == -1.0           # negated
    assert items["Dividends"] == 4.0
    # near-zero items (interest 0, div.tax 0, fx fees 0, proceeds 0) dropped
    assert "Interest" not in items
    assert "Proceeds" not in items
    colors = dict(zip(cf["data"]["labels"], cf["data"]["datasets"][0]["backgroundColor"]))
    assert colors["Invested"] == "#e74c3c"   # negative -> red
    assert colors["Deposits"] == "#2ecc71"   # positive -> green


def test_review_income_bar_mirrors_series():
    income = pd.Series([1.5, 2.5], index=["2024-01", "2024-02"])
    cfg = html_charts.review_charts_config(
        _holdings_df(), _empty_flows(), income, "EUR")
    inc = cfg["income"]
    assert inc["type"] == "bar"
    assert inc["data"]["labels"] == ["2024-01", "2024-02"]
    assert inc["data"]["datasets"][0]["data"] == [1.5, 2.5]
    assert inc["data"]["datasets"][0]["backgroundColor"] == "#3498db"


def test_review_empty_holdings_and_flows_are_none():
    empty_holdings = pd.DataFrame(columns=["ticker", "market_value"])
    cfg = html_charts.review_charts_config(
        empty_holdings, _empty_flows(), pd.Series(dtype=float), "EUR")
    assert cfg["holdings"] is None
    assert cfg["cashflows"] is None
    assert cfg["income"] is None


def test_review_holdings_doughnut_cycles_colors_past_palette():
    many = pd.DataFrame({
        "ticker": [f"T{i}" for i in range(12)],
        "market_value": [100.0] * 12,
    })
    cfg = html_charts.review_charts_config(
        many, _flows(), pd.Series(dtype=float), "EUR")
    h = cfg["holdings"]
    assert len(h["data"]["labels"]) == 12
    bg = h["data"]["datasets"][0]["backgroundColor"]
    assert len(bg) == 12  # not truncated to 10
    assert bg[0] == bg[10]  # cycles: index 10 wraps to index 0


def test_review_cashflows_near_zero_boundary():
    flows = {"deposits": 0.0, "withdrawals": 0.0, "interest": 2e-9,
             "dividends": 0.0, "dividend_tax": 0.0, "conversion_fees": 0.0,
             "invested": 0.0, "proceeds": 0.0, "fees": 0.0}
    cfg = html_charts.review_charts_config(
        _holdings_df(), flows, pd.Series(dtype=float), "EUR")
    # interest 2e-9 > 1e-9 stays; everything else is <= 1e-9 and dropped
    assert cfg["cashflows"]["data"]["labels"] == ["Interest"]


def test_review_holdings_all_zero_returns_none():
    all_zero = pd.DataFrame({"ticker": ["A", "B"], "market_value": [0.0, 0.0]})
    cfg = html_charts.review_charts_config(
        all_zero, _flows(), pd.Series(dtype=float), "EUR")
    assert cfg["holdings"] is None


def _full_review_cfg():
    return html_charts.review_charts_config(
        _holdings_df(), _flows(),
        pd.Series([1.0], index=["2024-01"]), "EUR")


def _full_evolution_cfg():
    return html_charts.evolution_chart_config(_evolution_df(), "EUR")


def test_render_charts_block_empty_when_nothing_to_show():
    empty_review = html_charts.review_charts_config(
        pd.DataFrame(columns=["ticker", "market_value"]),
        _empty_flows(), pd.Series(dtype=float), "EUR")
    block = html_charts.render_charts_block(None, empty_review, "EUR")
    assert block == ""


def test_render_charts_block_contains_canvases_scripts_and_data():
    block = html_charts.render_charts_block(
        _full_evolution_cfg(), _full_review_cfg(), "EUR")
    assert "<canvas id='evolution-chart'></canvas>" in block
    assert "<canvas id='holdings-chart'></canvas>" in block
    assert "<canvas id='cashflows-chart'></canvas>" in block
    assert "<canvas id='income-chart'></canvas>" in block
    assert "id='chart-data'" in block
    # inlined Chart.js bundle present
    assert html_charts.load_chartjs_inline()[:200] in block
    # render script with gain/loss plugin present
    assert "gainLoss" in block
    assert "beforeDatasetsDraw" in block
    assert "new Chart(" in block
    # no PNG embedding
    assert "data:image/png;base64" not in block


def test_render_charts_block_omits_evolution_when_none():
    block = html_charts.render_charts_block(None, _full_review_cfg(), "EUR")
    assert "<canvas id='evolution-chart'></canvas>" not in block
    assert "<canvas id='holdings-chart'></canvas>" in block


def test_render_charts_block_json_payload_parses():
    block = html_charts.render_charts_block(
        _full_evolution_cfg(), _full_review_cfg(), "EUR")
    m = re.search(
        r"<script type='application/json' id='chart-data'>(.*?)</script>",
        block, re.S)
    assert m, "chart-data JSON block not found"
    payload = json.loads(m.group(1))
    assert set(payload) == {"currency", "evolution", "holdings", "cashflows", "income"}
    assert payload["currency"] == "EUR"
    assert payload["evolution"]["type"] == "line"
    assert payload["holdings"]["type"] == "doughnut"


def test_render_charts_block_empty_state_fallbacks():
    # holdings None, cashflows None, income None — but evolution present so the
    # Charts card renders with muted fallbacks for holdings/cashflows.
    review = html_charts.review_charts_config(
        pd.DataFrame(columns=["ticker", "market_value"]),
        _empty_flows(), pd.Series(dtype=float), "EUR")
    block = html_charts.render_charts_block(_full_evolution_cfg(), review, "EUR")
    assert "No open positions." in block
    assert "No cash flows." in block
    assert "<canvas id='income-chart'></canvas>" not in block
