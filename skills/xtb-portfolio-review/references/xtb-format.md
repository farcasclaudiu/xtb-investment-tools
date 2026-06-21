# XTB Report Format Notes

Load this when XTB parsing details matter.

## Workbook Layout

- XTB exports are `.xlsx` files.
- Metadata is in rows 1-4.
- Column headers begin on row 5, so pandas should use `header=4`.
- Main sheets:
  - `Cash Operations`: trades, deposits, withdrawals, dividends, taxes, interest, conversions, and broker `Total` row.
  - `Closed Positions`: realized trade summary; can be empty for still-open accounts.
  - `Open Positions`: optional live/open-position sheet.

## Trade Reconstruction

- The review reconstructs trades primarily from `Cash Operations` comments such as `OPEN BUY 6 @ 301.50` and `CLOSE SELL 2 @ 100.00`.
- Use the real `Ticker` column as the instrument key, not only descriptive instrument text.
- Process trades chronologically before FIFO matching.
- Split-fill notation like `OPEN BUY 1/100 @ 14.3130` means executed quantity is `1`; use the numerator, not `0.01`.
- Some XTB stock sales appear as `CLOSE BUY` while the row type is `Stock sell` and amount is positive. Treat these economically as sales.

## Valuation

- Live prices come from `yfinance` daily closes at or before the report end date.
- Use trusted same-instrument symbol aliases only. Do not substitute a different share class as a proxy.
- If no trusted price exists, hold the ticker at cost and surface `price_source = cost` plus the reason.

## Cash And Performance

- Reconciliation compares computed ending cash with the broker `Total` row.
- Dividends and interest are internal cash flows unless withdrawn; do not count them as external cash flows for XIRR.
- XIRR may be `n/a` if the cash-flow signs or solver conditions are insufficient.
