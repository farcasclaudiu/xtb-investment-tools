# Portfolio Review Validation Checklist

Load this before saying an XTB portfolio review is ready.

## Commands

- Install dependencies:
  `<skill-folder>/scripts/setup-env.sh`
- Validate bundled tools:
  `<skill-folder>/scripts/validate-review.sh`
- Generate report and CSVs:
  `<skill-folder>/scripts/run-review.sh <report.xlsx>`
- Export PDF when requested:
  `<skill-folder>/scripts/export-pdf.sh results/<stem>_review.html`
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

- Use `scripts/export-pdf.sh`, not direct Chrome/Chromium CLI flags. In
  particular, do not use `--force-device-scale-factor` as a substitute for PDF
  print scale.
- The exporter must call Playwright/browser PDF generation with true
  `scale=0.8`, unless a user explicitly chooses a different scale.
- Before printing, it must wait for page load, network idle, `document.fonts`,
  and nonblank Chart.js canvases, then pause 3-5 seconds.
- After printing, it must render the PDF pages to PNG files and check the
  resulting page images. Do not claim PDF success until the rendered PNG pages
  exist and chart-heavy pages are visibly nonblank.
- If charts render correctly in browser HTML but appear narrow, stacked,
  left-offset, or blank in PDF, diagnose the PDF export path first; do not
  treat it as a live-data issue or change report CSS unless the user explicitly
  asks for a report style change.

## Useful Output Files

- `_holdings.csv`: shares, cost basis, market value, allocation, unrealized P/L, price source.
- `_cash_flows.csv`: deposits, withdrawals, invested, proceeds, dividends, tax, fees, ending cash.
- `_realized_pl.csv`: realized profit/loss by ticker.
- `_performance.csv`: portfolio value, total gain, return metrics, income yield.
- `_income.csv`: dividend and interest income over time.
- `_evolution.csv`: daily cost/value/realized series for charts.

## Reporting Style

Summarize computed facts and data-quality status. Avoid recommendations to buy, sell, rebalance, or time markets unless the user explicitly asks for financial planning context, and still frame it as educational analysis rather than advice.
