# Wealthfolio CSV Mapping

Load this when validating or debugging XTB to Wealthfolio exports.

## Required Header

`date,symbol,quantity,activityType,unitPrice,currency,fee,amount`

## XTB To Wealthfolio Mapping

- `Stock purchase` or `OPEN BUY` -> `BUY`
- `Stock sale`, `CLOSE SELL`, or `OPEN SELL` -> `SELL`
- `Stock sell` with `CLOSE BUY` -> `SELL` because XTB can encode sale close legs this way
- `Deposit` -> `DEPOSIT`
- `Withdrawal` -> `WITHDRAWAL`
- `Dividend` -> `DIVIDEND`
- `Dividend tax` -> `TAX`
- `Free funds interest` -> `INTEREST`
- `Currency conversion` -> `FEE`

## Row Rules

- `BUY` and `SELL`:
  - `symbol`: real ticker when available
  - `quantity`: parsed share count
  - `unitPrice`: parsed `@ price`
  - `fee`: inline trading fee if supported by the exporter, otherwise `0.00`
  - `amount`: blank
- Cash activities (`DEPOSIT`, `WITHDRAWAL`, `INTEREST`, `TAX`, `FEE`):
  - `symbol`: `$CASH-<CCY>`
  - `quantity`: `1`
  - `unitPrice`: `1`
  - `amount`: absolute cash value
- `DIVIDEND`:
  - Keep the real security ticker when available
  - Use `quantity = 1`, `unitPrice = 1`, and `amount` as the absolute dividend cash value

## Quantity Parsing

- For comments like `OPEN BUY 6 @ 301.50`, quantity is `6`.
- For split fills like `OPEN BUY 1/100 @ 14.3130`, quantity is the numerator `1`, not `0.01`.
- If no parseable quantity exists, the exporter may fall back to `abs(amount) / price`.

## Validation Commands

- Install dependencies:
  `<skill-folder>/scripts/setup-env.sh`
- Validate bundled tools:
  `<skill-folder>/scripts/validate-export.sh`
- Generate default CSV:
  `<skill-folder>/scripts/export-wealthfolio.sh <report.xlsx>`
- If working inside the original project repository, full tests are also useful:
  `.venv/bin/python -m pytest -q`

## Import Readiness Checks

- Header exactly matches the required schema.
- Activity types are among Wealthfolio-supported values used by the exporter.
- Trade rows have blank `amount`.
- Cash rows have nonblank positive `amount` and `$CASH-<CCY>` unless dividend ticker retention applies.
- `CLOSE BUY` stock-sale rows export as `SELL`.
- Split-fill rows use numerator quantity.
