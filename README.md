# XTB Portfolio Review & Wealthfolio Exporter

[![skills.sh](https://skills.sh/b/farcasclaudiu/xtb-investment-tools)](https://skills.sh/farcasclaudiu/xtb-investment-tools)

A set of Python tools that turn an **XTB brokerage report** (`.xlsx` export) into:

1. A complete, human-readable **portfolio review** (console and a self-contained HTML report with interactive, offline charts and analysis tables).
2. A **Wealthfolio-compatible CSV** so the same XTB history can be imported into the [Wealthfolio](https://wealthfolio.app/) portfolio tracker.

The parser is generic for XTB exports in this format. Tests generate a small
synthetic workbook at runtime, while personal brokerage exports should stay
local and untracked.

## Quick Start

### Use the skills in your own agent

This is the recommended path if you want an LLM or coding agent to run the XTB
workflows for you. Give your agent access to this repository and ask it to
follow [`INSTALL_FOR_AGENTS.md`](INSTALL_FOR_AGENTS.md). That file tells the
agent how to install or use the portable skill folders, validate them, and run
the right workflow for your XTB workbook.

Install prompt:

```text
Read https://github.com/farcasclaudiu/xtb-investment-tools/blob/main/INSTALL_FOR_AGENTS.md and install the XTB skills for your agent harness.
```

Portfolio review prompt examples:

```text
Use the XTB portfolio review skill to generate and verify a report for my XTB
workbook.
```

```text
Use the XTB portfolio review skill to generate the HTML report, export the CSV
tables, and summarize the reconciliation status and data-quality caveats.
```

```text
Use the XTB portfolio review skill with EUR_demo_report.xlsx as the input file.
Generate the review, run validation, and report the generated output paths.
```

Wealthfolio export prompt examples:

```text
Use the XTB Wealthfolio export skill to create and validate a Wealthfolio CSV
from my XTB workbook.
```

```text
Use the XTB Wealthfolio export skill to inspect the generated CSV rows and tell
me whether they are ready to import into Wealthfolio.
```

```text
Use the XTB Wealthfolio export skill with EUR_demo_report.xlsx as the input file
and write the Wealthfolio CSV to results/EUR_demo_report_wealthfolio.csv.
```

### Run the tools directly

From the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

.venv/bin/python main.py path/to/xtb-report.xlsx
.venv/bin/python exporter.py path/to/xtb-report.xlsx
```

Outputs are written to `results/`, including
`results/<stem>_review.html` for the portfolio review and
`results/<stem>_wealthfolio.csv` for the Wealthfolio import file. If there is
exactly one `.xlsx` file in the current folder, both tools can auto-detect it
when the path is omitted. Add `--csv` to the portfolio review command only when
you want the extra per-section CSV exports.

---

## Background: the XTB export format

An XTB report is an `.xlsx` file with a fixed layout:

- **Rows 1–4**: metadata (account number, report period).
- **Row 5** (`header=4`): the actual column headers.
- **Sheets**:
  - `Closed Positions` — realized trades, with a `Profit/Loss` column. May contain a
    `Profit/loss` summary row and/or be empty (all positions still open).
  - `Cash Operations` — every cash flow: stock purchases/sales, deposits, withdrawals,
    dividends, dividend tax, free-funds interest, currency conversions. Each trade row
    carries a comment like `OPEN BUY 6 @ 301.50` or `CLOSE SELL 2 @ 100.00`, and the
    sheet ends with a `Total` row (the broker-reported ending cash balance).

Two quirks the code handles explicitly:

- **Header is on row 5**, not row 1.
- **Split-fill quantity notation**: `OPEN BUY 1/100 @ 14.3130` means *1 share out of a
  100-share parent order* — the numerator is the executed quantity. The tools use the
  numerator (falling back to `cash / price`) rather than mis-reading `1/100` as `0.01`.
- **Stock-sale close notation**: some XTB stock-sale rows are written as
  `CLOSE BUY ...` while the row type is `Stock sell` and the amount is positive
  sale proceeds. The tools treat these as sales for holdings, cash flows, and
  Wealthfolio export.

---

## Files

### Source code

| File          | Purpose                                                                                                                                                                                                                                                                                                                                           |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `skills/xtb-portfolio-review/scripts/main.py` | **Portfolio review generator.** Parses the XTB report, reconstructs trades from Cash Operations comments, runs FIFO lot-matching for realized P/L, computes cash flows, holdings (cost basis), performance metrics, contribution/risk/income analysis, and reconciliation against the broker's `Total` row. Outputs a console report and a self-contained HTML report with interactive Chart.js charts and offline table tools (bundled inline, no internet required). |
| `skills/xtb-wealthfolio-export/scripts/exporter.py` | **XTB → Wealthfolio CSV exporter.** Maps each Cash Operation to a Wealthfolio row (`date,symbol,quantity,activityType,unitPrice,currency,fee`). |
| `main.py`, `exporter.py`, `html_charts.py` | Thin compatibility entry points that preserve the original repo commands/imports while delegating to the bundled skill implementations. |

### Tests

| File                | Purpose                                                                                                                                                                                     |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `test_portfolio.py` | Unit + integration tests for `main.py` (parsing, FIFO realized P/L, cash-flow categorization, income, open positions, performance, analysis helpers, reconciliation against the generated synthetic workbook, HTML structure and interactions). |
| `test_exporter.py`  | Tests for `exporter.py` (activity-type classification, the split-fill quantity parser, full row mapping, schema validation on the generated synthetic workbook, empty-input handling).                       |

### Local inputs

Personal `.xlsx` exports are not committed. Place your XTB report in the repo
folder when running the tools locally, or pass its path explicitly.

### Generated outputs (regenerated by running the tools)

All generated files are written to the **`results/`** folder (created
automatically) and **named after the input report**: for input
`EUR_demo_report.xlsx` every output uses that stem plus a descriptor, e.g.
`EUR_demo_report_review.html`.

| File                                              | Produced by   | Content                                                                     |
| ------------------------------------------------- | ------------- | --------------------------------------------------------------------------- |
| `results/<stem>_review.html`                      | `main.py`     | Self-contained HTML report with interactive Chart.js charts, analysis sections, sortable/filterable tables, sticky navigation, and print/PDF styles; works offline. |
| `results/<stem>_holdings.csv`                     | `main.py`     | Open holdings: ticker, shares, avg cost, cost basis, return %, allocation %.|
| `results/<stem>_cash_flows.csv`                   | `main.py`     | Aggregated cash flows (deposits, interest, dividends, invested, …).         |
| `results/<stem>_realized_pl.csv`                  | `main.py`     | Realized P/L per ticker.                                                    |
| `results/<stem>_open_positions.csv`               | `main.py`     | Live market value / unrealized P/L (when an `Open Positions` sheet exists). |
| `results/<stem>_performance.csv`                  | `main.py`     | Performance metrics (portfolio value, returns, yield).                      |
| `results/<stem>_income.csv`                       | `main.py`     | Income (dividends + interest) by month.                                     |
| `results/<stem>_evolution.csv`                    | `main.py`     | Daily cost / market value / realized P/L series (drives the evolution chart).|
| `results/<stem>_wealthfolio.csv`                  | `exporter.py` | Wealthfolio-importable transaction history.                                 |

---

## Setup

Requires Python 3.10+.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

> The HTML report bundles Chart.js v4.5.1 (vendored inside each relevant skill at `scripts/assets/chartjs.umd.min.js`, version pinned in `scripts/assets/chartjs.VERSION`) so its charts render interactively with no internet connection.

## Agent skills

This repository includes harness-neutral agent skills under `skills/` for users
who want an LLM or coding agent to operate the tools consistently. Each skill is
a self-contained folder with a `SKILL.md`, `references/`, and bundled
`scripts/`, so users can copy a skill folder to another machine and still run
the relevant XTB workflow without cloning the full repository.

Agents should start with [`INSTALL_FOR_AGENTS.md`](INSTALL_FOR_AGENTS.md) for
copy/install/use instructions.

| Skill | Purpose |
| ----- | ------- |
| `xtb-portfolio-review` | Generate and verify XTB portfolio review reports, including reconciliation, holdings, performance, income, risk, and data-quality caveats. |
| `xtb-wealthfolio-export` | Export and validate Wealthfolio-compatible CSV files from XTB reports, including activity mappings and import-readiness checks. |

Use the skill folder directly, or copy it into the skill/instruction directory
for your harness. With a generic LLM, ask it to read the relevant `SKILL.md`.
For Codex, you can also copy either folder into `~/.codex/skills/`, then invoke
it in a new session:

```text
Use $xtb-portfolio-review to generate and verify an XTB portfolio report.
Use $xtb-wealthfolio-export to create and validate a Wealthfolio CSV from an XTB report.
```

Each copied skill folder includes `scripts/requirements.txt` plus shell wrappers
for environment setup, validation, and execution. From the directory where you
want `.venv` and `results/` to live, install dependencies with:

```bash
skills/xtb-portfolio-review/scripts/setup-env.sh
skills/xtb-wealthfolio-export/scripts/setup-env.sh
```

## Usage

### Generate the portfolio review

```bash
.venv/bin/python main.py                                          # auto-detects the only .xlsx in the folder
.venv/bin/python main.py EUR_demo_report.xlsx                 # explicit report
.venv/bin/python main.py --csv                                    # also write the CSV outputs
```

By default only the self-contained **HTML report** (with inline interactive
charts and table tools) is written to `results/`. Pass `--csv` to additionally
export the per-section CSVs (holdings, cash flows, performance, …).

If no path is given and exactly one `.xlsx` is present in the current
directory, it is used automatically; if there are none or several, pass the
path explicitly. Any same-format XTB export works — the currency is
auto-detected from the filename prefix (e.g. `EUR_…`, `USD_…`).

### HTML report features

The generated review HTML is a single offline file. It includes:

- **Executive Summary** — largest holding, top unrealized winner/loser, cash
  allocation, pricing warnings, and reconciliation status.
- **Concentration & Risk** — top-1/top-3/top-5 position weights, cash weight,
  positions above 20%, and cost-priced position count.
- **Income Quality** — gross income, dividend tax, net income, tax drag, net
  income yield, and dividend/interest mix.
- **Methodology & Data Quality** — live-vs-cost pricing coverage, cost fallback
  tickers, reconciliation status, and the main calculation assumptions.
- **Return Contribution** — per-ticker market value, unrealized P/L, realized
  P/L, total contribution, and contribution % of total gain.
- **Interactive charts** — portfolio evolution, holdings allocation, cash flows,
  and income over time when data exists.
- **Offline table tools** — data tables are sortable and filterable in the
  browser without any external JavaScript.
- **Navigation and print support** — a sticky section nav for browsing and
  print/PDF styles for cleaner exported reports.

### Export to Wealthfolio CSV

```bash
.venv/bin/python exporter.py                          # uses the default report -> results/<stem>_wealthfolio.csv
.venv/bin/python exporter.py EUR_other.xlsx -o my.csv  # explicit input/output
```

### Run the tests

```bash
.venv/bin/python -m pytest -q
```

---

## How the review is computed

- **Trades** are reconstructed from the `OPEN/CLOSE BUY/SELL … @ price` comments in
  Cash Operations (the `Closed Positions` sheet is often empty for still-open accounts).
  Trades are keyed by the **real `Ticker`** column (e.g. `SPYL.DE`), so descriptive
  variants of the same instrument merge into a single holding. Trades are processed in
  **chronological order** — XTB sheets sometimes list a position's close leg before its
  open leg, so date-ordering is required for correct FIFO lot matching.
- **Holdings** are the net open lots per ticker at cost basis, with allocation %.
- **Live market value** is fetched via [`yfinance`](https://github.com/ranaroussi/yfinance)
  for the last trading day on/before the report's `Date to`. The close is taken in the
  symbol's native currency and converted to the account currency when needed. Any ticker
  that can't be priced (delisted / not on Yahoo) falls back to **cost basis** and is
  flagged `price_source = "cost"` in the holdings CSV and report.
- **Realized P/L** prefers the broker's `Closed Positions` `Profit/Loss` column; when that
  is absent, it falls back to **FIFO lot matching** from CLOSE trades.
- **Cash flows** are categorized (deposits, withdrawals, interest, dividends, dividend tax,
  FX fees, invested, proceeds) and reconciled against the broker's `Total` (ending cash).
- **Performance** combines **live market value** (or cost basis fallback) with cash to give
  portfolio value, total gain, total return %, money-weighted return (XIRR), and
  income yield. XIRR uses external deposits/withdrawals plus terminal portfolio
  value; dividends and interest are not treated as external cash flows unless
  they leave the account as withdrawals.
- **Return contribution** combines each open holding's unrealized P/L with any
  realized P/L by ticker, then expresses the result as a share of total gain.
- **Concentration & risk** is derived from market-value weights, cash weight, and
  pricing source coverage; it flags large top holdings and cost-priced positions.
- **Income quality** separates gross income, dividend tax, net income, tax drag,
  net income yield, and the dividend-vs-interest mix.
- **Evolution chart** replays the trades chronologically and, for each trading day,
  computes the open cost basis, the open market value (from historical closes via
  yfinance, falling back to cost for unpriced tickers), and cumulative realized P/L.
  The gap between the **Cost** and **Value (realized + unrealized)** lines is the total
  investment gain/loss. Daily series is persisted to `results/<stem>_evolution.csv`
  when `--csv` is used.

### Wealthfolio activity mapping

| XTB operation                                     | Wealthfolio `activityType` |
| ------------------------------------------------- | -------------------------- |
| `Stock purchase` / `OPEN BUY`                     | `BUY`                      |
| `Stock sale` / `CLOSE SELL` / `OPEN SELL` (short) | `SELL`                     |
| `Deposit` / `Withdrawal`                          | `DEPOSIT` / `WITHDRAWAL`   |
| `Dividend`                                        | `DIVIDEND`                 |
| `Free funds interest`                             | `INTEREST`                 |
| `Dividend tax`                                    | `TAX`                      |
| `Currency conversion`                             | `FEE`                      |

Per the Wealthfolio [CSV spec](https://wealthfolio.app/docs/guide/csv-import/), cash
activities (`DEPOSIT`/`WITHDRAWAL`/`DIVIDEND`/`INTEREST`/`TAX`/`FEE`) carry their total
value in the `amount` column with `quantity = 1` and `unitPrice = 1`; the `fee` column is
only used for inline `BUY`/`SELL` commissions. Pure-cash rows use the `$CASH-<CCY>` symbol
(e.g. `$CASH-EUR`), while `DIVIDEND` keeps the security's real ticker. `BUY`/`SELL` leave
`amount` blank — Wealthfolio auto-calculates it as `quantity × unitPrice`.

---

## Notes & limitations

- **Live prices** are daily closes from yfinance, taken for the last trading day on or
  before the report's `Date to`. A symbol that can't be resolved (e.g. some proprietary
  XTB instrument codes) is valued at cost and flagged with `price_source = "cost"`.
- **Cost fallback positions** carry zero unrealized P/L in the report, contribution
  table, and evolution chart. The methodology section lists every cost fallback ticker.
- **Money-weighted return (XIRR)** requires at least one external cash outflow and
  one inflow. When the dated cash-flow series cannot be solved, the report shows `n/a`.
- **Reconciliation** compares computed ending cash against the XTB `Total` row; it reports
  `[OK]` when they match within €0.01.
- **HTML interactions** (charts, sorting, filtering, sticky navigation) are all inline
  and offline; no CDN or network access is required to open the generated report.
- Thousand-separators are intentionally **not** parsed in numeric fields (ambiguous with
  decimal dot); XTB's plain decimal format is handled correctly.
- All generated artifacts go to `results/` (git-ignored via `.gitignore`).
