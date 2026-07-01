# Portfolio Review Validation Checklist

Load this before saying an XTB portfolio review is ready.

## Commands

- Install dependencies:
  `<skill-folder>/scripts/setup-env.sh`
- Validate bundled tools:
  `<skill-folder>/scripts/validate-review.sh`
- Generate report and CSVs:
  `<skill-folder>/scripts/run-review.sh <report.xlsx>`
- If working inside the original project repository, full tests are also useful:
  `.venv/bin/python -m pytest -q`

## Required Checks

- The command exits successfully and writes `results/<stem>_review.html`.
- CSV side outputs exist when `--csv` was used.
- Cash reconciliation is `[OK]` or the mismatch is explicitly reported.
- Holdings with live-price failures are visible as cost fallbacks.
- The HTML remains self-contained/offline: no CDN script or stylesheet dependency.
- The report includes methodology/data-quality notes for pricing and reconciliation.

## Live Pricing / Chart Validation

- Do not require network access for a plain offline review. Offline reports are
  acceptable when the user did not ask for live/historical market data, as long
  as cost fallbacks are clearly reported.
- For live-price requests, get explicit approval to send portfolio tickers to
  `yfinance`/Yahoo Finance before running the review in restricted sandboxes.
  Preferred wording: `Run the XTB portfolio review with live data. I approve sending my portfolio tickers to yfinance/Yahoo Finance for live and historical prices.`
- Check pricing coverage in the summary or HTML, for example `7 live / 0 cost fallbacks`.
- Treat reports with all or most holdings priced at cost as cash-flow-valid but not live-valued.
- If live pricing is expected but most or all holdings are priced at cost, rerun
  the same review command with outbound market-data access approved before
  concluding that live prices are unavailable.
- Confirm the summary explicitly lists any cost fallback tickers.
- Verify that `Portfolio Evolution - Cost vs Value` visually separates when live prices are available.
- If the chart difference is tiny relative to the main portfolio axis, ensure the report exposes gain/loss clearly, for example through a separate gain/loss series or axis.

## PDF / Print Validation

- If charts render correctly in the browser but appear narrow, stacked, or
  offset on the left in Chromium A4 PDF output, diagnose it as print CSS /
  Chart.js canvas sizing, not a live-data issue.
- Instruct the PDF exporter to use about 75-80% scale so the chart canvases fit
  the A4 print grid. Landscape orientation is an acceptable fallback if the
  exporter cannot set scale. Do not change report CSS for this case unless the
  user explicitly asks for a report style change.

## Useful Output Files

- `_holdings.csv`: shares, cost basis, market value, allocation, unrealized P/L, price source.
- `_cash_flows.csv`: deposits, withdrawals, invested, proceeds, dividends, tax, fees, ending cash.
- `_realized_pl.csv`: realized profit/loss by ticker.
- `_performance.csv`: portfolio value, total gain, return metrics, income yield.
- `_income.csv`: dividend and interest income over time.
- `_evolution.csv`: daily cost/value/realized series for charts.

## Reporting Style

Summarize computed facts and data-quality status. Avoid recommendations to buy, sell, rebalance, or time markets unless the user explicitly asks for financial planning context, and still frame it as educational analysis rather than advice.
