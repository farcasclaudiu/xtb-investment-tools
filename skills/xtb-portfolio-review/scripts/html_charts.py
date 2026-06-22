"""Interactive Chart.js charts for the self-contained HTML report.

This module is the only place that knows about Chart.js. It reads the vendored
UMD bundle from assets/ and builds Chart.js config dicts (pure functions) plus
an HTML fragment that inlines the bundle, the data (JSON), and a render script.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
CHARTJS_PATH = ASSETS_DIR / "chartjs.umd.min.js"
CHARTJS_VERSION_PATH = ASSETS_DIR / "chartjs.VERSION"


def load_chartjs_inline() -> str:
    """Return the minified Chart.js UMD source, vendored under assets/."""
    if not CHARTJS_PATH.exists():
        raise FileNotFoundError(
            f"Chart.js bundle not found at {CHARTJS_PATH}. "
            "Re-vendor it (see assets/chartjs.VERSION)."
        )
    return CHARTJS_PATH.read_text(encoding="utf-8")


def _iso(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10]
    return str(value)


def _round_series(values) -> list[float]:
    return [round(float(v), 2) for v in values]


def evolution_chart_config(evolution_df: pd.DataFrame, currency: str) -> dict | None:
    """Build a Chart.js line-chart config for cost vs value over time.

    Returns None when there is no evolution data (caller omits the card).
    """
    if evolution_df is None or evolution_df.empty:
        return None
    labels = [_iso(d) for d in evolution_df.index]
    return {
        "type": "line",
        "data": {
            "labels": labels,
            "datasets": [
                {
                    "label": "Cost (invested)",
                    "data": _round_series(evolution_df["cost"]),
                    "borderColor": "#6b7280",
                    "backgroundColor": "#6b7280",
                    "borderWidth": 2,
                    "fill": False,
                    "pointRadius": 0,
                    "tension": 0.1,
                },
                {
                    "label": "Value (realized + unrealized)",
                    "data": _round_series(evolution_df["total_value"]),
                    "borderColor": "#2c5282",
                    "backgroundColor": "#2c5282",
                    "borderWidth": 2,
                    "fill": False,
                    "pointRadius": 0,
                    "tension": 0.1,
                },
                {
                    "label": "Cumulative realized P/L",
                    "data": _round_series(evolution_df["realized_pl"]),
                    "borderColor": "#f39c12",
                    "backgroundColor": "#f39c12",
                    "borderWidth": 1.5,
                    "borderDash": [6, 4],
                    "fill": False,
                    "pointRadius": 0,
                    "tension": 0.1,
                },
            ],
        },
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "interaction": {"mode": "index", "intersect": False},
            "plugins": {
                "legend": {"position": "bottom",
                           "labels": {"boxWidth": 12, "font": {"size": 12}}},
            },
            "scales": {
                "x": {"ticks": {"maxRotation": 45, "autoSkip": True}},
                "y": {"beginAtZero": False},
            },
        },
    }


DOUGHNUT_COLORS = [
    "#2c5282", "#1f9d55", "#f39c12", "#3498db", "#9b59b6",
    "#e67e22", "#16a085", "#34495e", "#e3342f", "#7f8c8d",
]


def review_charts_config(
    holdings: pd.DataFrame,
    flows: dict[str, float],
    income_by_period: pd.Series,
    currency: str,
) -> dict:
    """Build Chart.js configs for the three review charts.

    Returns {'holdings': cfg|None, 'cashflows': cfg|None, 'income': cfg|None}.
    Each is None when its source data is empty.
    """
    holdings_cfg = _holdings_config(holdings)
    cashflows_cfg = _cashflows_config(flows)
    income_cfg = _income_config(income_by_period)
    return {"holdings": holdings_cfg, "cashflows": cashflows_cfg, "income": income_cfg}


def _holdings_config(holdings: pd.DataFrame) -> dict | None:
    if holdings is None or holdings.empty:
        return None
    alloc_col = "market_value" if "market_value" in holdings.columns else "cost_basis"
    filtered = holdings.loc[holdings[alloc_col] > 0]
    if filtered.empty:
        return None
    values = _round_series(filtered[alloc_col])
    return {
        "type": "doughnut",
        "data": {
            "labels": [str(t) for t in filtered["ticker"].tolist()],
            "datasets": [{
                "data": values,
                "backgroundColor": [DOUGHNUT_COLORS[i % len(DOUGHNUT_COLORS)]
                                    for i in range(len(values))],
            }],
        },
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "plugins": {"legend": {"position": "right",
                                   "labels": {"boxWidth": 12, "font": {"size": 11}}}},
        },
    }


def _cashflows_config(flows: dict[str, float]) -> dict | None:
    if not flows:
        return None
    items = {
        "Deposits": float(flows.get("deposits", 0.0)),
        "Withdrawals": -float(flows.get("withdrawals", 0.0)),
        "Interest": float(flows.get("interest", 0.0)),
        "Dividends": float(flows.get("dividends", 0.0)),
        "Div.tax": float(flows.get("dividend_tax", 0.0)),
        "Currency conversions": float(flows.get("currency_conversions", 0.0)),
        "Invested": -float(flows.get("invested", 0.0)),
        "Proceeds": float(flows.get("proceeds", 0.0)),
        "FX fees": float(flows.get("conversion_fees", 0.0)),
        "Fees": -float(flows.get("fees", 0.0)),
    }
    items = {k: v for k, v in items.items() if abs(v) > 1e-9}
    if not items:
        return None
    labels = list(items.keys())
    values = _round_series(items.values())
    colors = ["#2ecc71" if v >= 0 else "#e74c3c" for v in items.values()]
    return {
        "type": "bar",
        "data": {"labels": labels,
                 "datasets": [{"label": "Cash flows", "data": values,
                               "backgroundColor": colors}]},
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "plugins": {"legend": {"display": False}},
            "scales": {"x": {"ticks": {"maxRotation": 30, "autoSkip": False}},
                       "y": {"beginAtZero": True}},
        },
    }


def _income_config(income_by_period: pd.Series) -> dict | None:
    if income_by_period is None or income_by_period.empty:
        return None
    return {
        "type": "bar",
        "data": {
            "labels": [str(i) for i in income_by_period.index],
            "datasets": [{"label": "Income",
                          "data": _round_series(income_by_period.tolist()),
                          "backgroundColor": "#3498db"}],
        },
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "plugins": {"legend": {"display": False}},
            "scales": {"x": {"ticks": {"maxRotation": 45, "autoSkip": False}},
                       "y": {"beginAtZero": True}},
        },
    }


_RENDER_SCRIPT = r"""
function _bootPortfolioCharts() {
  var block = document.getElementById('chart-data');
  if (!block) { return; }
  var data = JSON.parse(block.textContent);
  var ccy = data.currency || 'EUR';
  function fmt(v) {
    try { return new Intl.NumberFormat('en-US', {style: 'currency', currency: ccy}).format(v); }
    catch (e) { return String(v); }
  }
  function applyTooltip(cfg) {
    if (!cfg || !cfg.options) { return; }
    cfg.options.plugins = cfg.options.plugins || {};
    cfg.options.plugins.tooltip = cfg.options.plugins.tooltip || {};
    cfg.options.plugins.tooltip.callbacks = cfg.options.plugins.tooltip.callbacks || {};
    if (cfg.type === 'doughnut' || cfg.type === 'pie') {
      cfg.options.plugins.tooltip.callbacks.label = function (ctx) {
        var total = (ctx.dataset && ctx.dataset.data)
          ? ctx.dataset.data.reduce(function (a, b) { return a + (typeof b === 'number' ? b : 0); }, 0)
          : 0;
        var v = (typeof ctx.parsed === 'number') ? ctx.parsed : ctx.raw;
        var pct = total > 0 ? (v / total * 100) : 0;
        return (ctx.label ? ctx.label + ': ' : '') + fmt(v) + ' (' + pct.toFixed(1) + '%)';
      };
      return;
    }
    cfg.options.plugins.tooltip.callbacks.label = function (ctx) {
      var label = (ctx.dataset && ctx.dataset.label) ? ctx.dataset.label : '';
      var v = (ctx.parsed && Object.prototype.hasOwnProperty.call(ctx.parsed, 'y'))
        ? ctx.parsed.y : (typeof ctx.parsed === 'number' ? ctx.parsed : ctx.raw);
      return label ? (label + ': ' + fmt(v)) : fmt(v);
    };
  }
  function mount(id, cfg, plugins) {
    if (!cfg) { return; }
    var el = document.getElementById(id);
    if (!el) { return; }
    applyTooltip(cfg);
    var config = {type: cfg.type, data: cfg.data, options: cfg.options};
    if (plugins && plugins.length) { config.plugins = plugins; }
    new Chart(el.getContext('2d'), config);
  }
  var gainLossPlugin = {
    id: 'gainLoss',
    beforeDatasetsDraw: function (chart) {
      var ds = chart.data.datasets;
      if (ds.length < 2) { return; }
      var meta0 = chart.getDatasetMeta(0);
      var meta1 = chart.getDatasetMeta(1);
      var cost = ds[0].data;
      var value = ds[1].data;
      if (!meta0 || !meta1 || !meta0.data || !meta1.data) { return; }
      var ctx = chart.ctx;
      ctx.save();
      for (var i = 0; i < value.length - 1; i++) {
        var a0 = meta0.data[i], a1 = meta0.data[i + 1];
        var b0 = meta1.data[i], b1 = meta1.data[i + 1];
        if (!a0 || !a1 || !b0 || !b1) { continue; }
        var gain = (value[i] >= cost[i] && value[i + 1] >= cost[i + 1]);
        ctx.beginPath();
        ctx.moveTo(a0.x, a0.y); ctx.lineTo(a1.x, a1.y);
        ctx.lineTo(b1.x, b1.y); ctx.lineTo(b0.x, b0.y);
        ctx.closePath();
        ctx.fillStyle = gain ? 'rgba(31,157,85,0.25)' : 'rgba(227,52,47,0.25)';
        ctx.fill();
      }
      ctx.restore();
    }
  };
  mount('evolution-chart', data.evolution, [gainLossPlugin]);
  mount('holdings-chart', data.holdings);
  mount('cashflows-chart', data.cashflows);
  mount('income-chart', data.income);
}
if (document.readyState !== 'loading') { _bootPortfolioCharts(); }
else { document.addEventListener('DOMContentLoaded', _bootPortfolioCharts); }
"""


def render_charts_block(
    evolution_cfg: dict | None, review_cfg: dict, currency: str
) -> str:
    """Return the HTML fragment: canvases + inlined Chart.js + JSON + render script.

    Returns "" when there is nothing to render.
    """
    holdings_cfg = review_cfg.get("holdings") if review_cfg else None
    cashflows_cfg = review_cfg.get("cashflows") if review_cfg else None
    income_cfg = review_cfg.get("income") if review_cfg else None

    if evolution_cfg is None and not any([holdings_cfg, cashflows_cfg, income_cfg]):
        return ""

    parts: list[str] = []

    if evolution_cfg is not None:
        parts.append(
            "<div class='card chart full' id='charts'>\n"
            "  <h2>Portfolio Evolution — Cost vs Value</h2>\n"
            "  <div class='chart-wrap' style='height:380px'>"
            "<canvas id='evolution-chart'></canvas></div>\n"
            "</div>"
        )

    grid_cells = []
    if holdings_cfg is not None:
        grid_cells.append(
            "<div><h3>Holdings Allocation</h3>"
            "<div class='chart-wrap' style='height:300px'>"
            "<canvas id='holdings-chart'></canvas></div></div>"
        )
    else:
        grid_cells.append("<div><h3>Holdings Allocation</h3>"
                          "<p class='muted'>No open positions.</p></div>")
    if cashflows_cfg is not None:
        grid_cells.append(
            "<div><h3>Cash Flows</h3>"
            "<div class='chart-wrap' style='height:300px'>"
            "<canvas id='cashflows-chart'></canvas></div></div>"
        )
    else:
        grid_cells.append("<div><h3>Cash Flows</h3>"
                          "<p class='muted'>No cash flows.</p></div>")
    # Income is optional: the income cell is omitted entirely when there is no
    # income data, unlike holdings/cashflows which always render a cell with a
    # muted fallback.
    if income_cfg is not None:
        grid_cells.append(
            "<div><h3>Income Over Time</h3>"
            "<div class='chart-wrap' style='height:300px'>"
            "<canvas id='income-chart'></canvas></div></div>"
        )
    charts_id_attr = " id='charts'" if evolution_cfg is None else ""
    parts.append(
        f"<div class='card chart full'{charts_id_attr}>\n"
        "  <h2>Charts</h2>\n"
        "  <div class='chart-grid'>\n    " +
        "\n    ".join(grid_cells) + "\n  </div>\n"
        "</div>"
    )

    payload = {
        "currency": currency,
        "evolution": evolution_cfg,
        "holdings": holdings_cfg,
        "cashflows": cashflows_cfg,
        "income": income_cfg,
    }
    # Escape < and > so the JSON is always safe to inline inside a <script>
    # block, even if a label ever contained the literal "</script>".
    data_json = json.dumps(payload).replace("<", "\\u003c").replace(">", "\\u003e")

    parts.append(
        "<script>\n" + load_chartjs_inline() + "\n</script>\n"
        "<script type='application/json' id='chart-data'>" + data_json + "</script>\n"
        "<script>\n" + _RENDER_SCRIPT + "\n</script>"
    )
    return "\n".join(parts)
