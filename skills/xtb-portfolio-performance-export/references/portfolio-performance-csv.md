# Portfolio Performance CSV Mapping

Load this when validating or explaining XTB to Portfolio Performance exports.

## Generated Files

- `<stem>_portfolio_performance_portfolio_transactions.csv`
  - Import type: `Portfolio Transactions`
  - Header: `Date;Type;Shares;Ticker Symbol;Security Name;Value;Fees;Taxes;Note;Securities Account;Cash Account`
- `<stem>_portfolio_performance_account_transactions.csv`
  - Import type: `Account Transactions`
  - Header: `Date;Type;Value;Ticker Symbol;Security Name;Shares;Gross Amount;Currency Gross Amount;Note;Cash Account;Offset Account`

Both files are UTF-8 CSV files with semicolon delimiters and a first-line
header.

Portfolio Performance's UI uses `Deposit Accounts` for cash/deposit accounts
and `Securities Accounts` for custody accounts. The CSV importer still names
the deposit-account field `Cash Account`; keep that header literal.

## XTB To Portfolio Performance Mapping

- `Stock purchase` or `OPEN BUY` -> Portfolio `Buy`
- `Stock sale`, `Stock sell`, `CLOSE SELL`, or `OPEN SELL` -> Portfolio `Sell`
- `Stock sell` with `CLOSE BUY` -> Portfolio `Sell`
- `Deposit` -> Account `Deposit`
- `Withdrawal` -> Account `Withdrawal`
- `Dividend` -> Account `Dividend`
- `Dividend tax`, `RO tax`, `Free funds interest tax`, or other tax-like rows -> Account `Taxes`
- `Free funds interest` -> Account `Interest`
- `Currency conversion` -> Account `Fees`
- `Subaccount transfer` or `Transfer` -> Account `Transfer (Inbound)` or `Transfer (Outbound)` by amount sign

## Import Steps

1. In Portfolio Performance, create or open the target portfolio file.
2. Ensure the Portfolio Performance `Securities Account` and `Deposit Account`
   exist, or select/create them in the import wizard. The default CSV names are
   `XTB` and `XTB (<CCY>)`.
3. Import the portfolio transactions CSV first with `File > Import > CSV files`.
4. Select type `Portfolio Transactions`.
5. Use `UTF-8`, delimiter `semicolon`, and enable `First line contains header`.
6. Confirm mappings for `Date`, `Type`, `Shares`, `Ticker Symbol`,
   `Security Name`, `Value`, `Fees`, `Taxes`, `Securities Account`, and
   `Cash Account`. In the CSV importer, `Cash Account` maps to the Portfolio
   Performance deposit account.
7. Finish that import and resolve any security matching prompts.
8. Import the account transactions CSV with `File > Import > CSV files`.
9. Select type `Account Transactions`.
10. Use the same CSV settings: `UTF-8`, semicolon delimiter, first line header.
11. Confirm mappings for `Date`, `Type`, `Value`, `Ticker Symbol`,
    `Security Name`, `Shares`, `Gross Amount`, `Currency Gross Amount`,
    `Cash Account`, and `Offset Account`. In the CSV importer, `Cash Account`
    maps to the Portfolio Performance deposit account.
12. Review the preview/status column before finishing, especially transfers,
    taxes, and dividends.

## Limitations

- The exporter does not create Portfolio Performance `.xml` portfolio files.
- It does not generate Portfolio Performance JSON import configurations.
- Multi-currency gross amount and exchange-rate fields are left blank unless a
  future XTB mapping can populate them safely.
