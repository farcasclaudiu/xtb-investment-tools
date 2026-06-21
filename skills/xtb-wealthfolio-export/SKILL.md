---
name: xtb-wealthfolio-export
description: Use when converting XTB brokerage .xlsx exports to Wealthfolio-compatible CSV imports, validating brokerage export rows, checking transaction activity mappings, reviewing trade/dividend/cash classifications, or debugging exporter.py output.
---

# XTB Wealthfolio Export

Use this skill to create and validate Wealthfolio CSV files from XTB `Cash Operations` data from a copied skill folder. The skill bundles the required Python tools in `scripts/`, so it can run without the original repository as long as Python dependencies are installed.

## Example Prompts

- Use the XTB Wealthfolio export skill to convert `report.xlsx` into a Wealthfolio-compatible CSV.
- Validate the generated Wealthfolio CSV and check trade, dividend, tax, deposit, and withdrawal mappings.
- Export my XTB brokerage history to Wealthfolio CSV and write the output to `results/import.csv`.

## Workflow

1. Identify the target workbook. If omitted and exactly one non-lock `.xlsx` exists in the current working directory, the exporter can auto-detect it.
2. Ensure dependencies are available:
   `<skill-folder>/scripts/setup-env.sh`
3. Validate the bundled tools:
   `<skill-folder>/scripts/validate-export.sh`
4. Create the Wealthfolio CSV from the directory where outputs should be written:
   `<skill-folder>/scripts/export-wealthfolio.sh <report.xlsx>`
5. If the user needs a custom path, run:
   `<skill-folder>/scripts/export-wealthfolio.sh <report.xlsx> -o <output.csv>`
6. Inspect the generated CSV header and a sample of rows before saying it is import-ready.
7. If row classification looks suspicious, read `references/wealthfolio-csv.md` and compare activity mappings.

## Bundled Tools

- `scripts/exporter.py`: standalone XTB to Wealthfolio CSV exporter.
- `scripts/main.py`: shared XTB parsing helpers used by the exporter.
- `scripts/html_charts.py` and `scripts/assets/`: bundled because `main.py` imports the report helper.
- `scripts/export-wealthfolio.sh`: shell wrapper that runs the bundled exporter.
- `scripts/validate-export.sh`: dependency and schema smoke check.
- `scripts/setup-env.sh`: creates `.venv` in the current working directory and installs dependencies.
- `scripts/requirements.txt`: Python dependencies.

## References

- Read `references/wealthfolio-csv.md` for Wealthfolio schema, XTB activity mapping, and known XTB comment quirks.

## Guardrails

- Do not hand-edit exported CSV rows unless the user asks; prefer fixing `scripts/exporter.py` when mappings are wrong.
- Keep `BUY` and `SELL` trade rows with blank `amount`; Wealthfolio calculates trade amount from `quantity * unitPrice`.
- For pure cash activities, use `$CASH-<CCY>` and set `quantity = 1`, `unitPrice = 1`, and `amount` to the absolute cash value.
