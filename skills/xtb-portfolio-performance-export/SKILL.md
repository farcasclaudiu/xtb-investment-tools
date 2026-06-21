---
name: xtb-portfolio-performance-export
description: Use when converting XTB brokerage .xlsx exports to Portfolio Performance-compatible CSV files, validating Portfolio Transactions and Account Transactions outputs, or explaining the Portfolio Performance import workflow.
version: 1.0.1
---

# XTB Portfolio Performance Export

Use this skill to create and validate Portfolio Performance CSV files from XTB
`Cash Operations` data using the bundled `exporter.py`.

## Workflow

1. Identify the target workbook. If omitted and exactly one non-lock `.xlsx`
   exists, the exporter can auto-detect it.
2. Run exporter validation before trusting an import file:
   `<skill-folder>/scripts/validate-export.sh`
3. Create the Portfolio Performance CSV files:
   `<skill-folder>/scripts/export-portfolio-performance.sh <report.xlsx>`
4. If the user needs a custom directory, run:
   `<skill-folder>/scripts/export-portfolio-performance.sh <report.xlsx> -o <output-dir>`
5. Inspect the generated CSV headers and a sample of rows before saying they
   are import-ready.
6. Read `references/portfolio-performance-csv.md` before explaining import
   steps, transaction mappings, or limitations.

## Outputs

- `results/<stem>_portfolio_performance_portfolio_transactions.csv`
- `results/<stem>_portfolio_performance_account_transactions.csv`

## Guardrails

- Import Portfolio Transactions before Account Transactions so securities
  exist before dividend rows are matched.
- Use UTF-8, semicolon delimiter, and first-line header in Portfolio
  Performance.
- Refer to Portfolio Performance UI accounts as `Deposit Account` and
  `Securities Account`; keep CSV field names literal as `Cash Account` and
  `Securities Account`.
- Do not claim the generated files are fully imported until the user has
  reviewed Portfolio Performance's wizard preview/status column.
- Keep multi-currency caveats visible: this first exporter uses the account
  currency and deterministic account labels, with optional CLI overrides.
