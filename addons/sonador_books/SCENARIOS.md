# Sonador Books: scenario matrix

Status as of 16 Oct 2026. The tests are in `tests_manual/smoke.py`; each run posts every scenario and rolls everything back.
62 checks, all passing.

Status key: **worked** = already worked before this sweep · **fixed now** = built or fixed in this sweep · **deferred** = not done yet (reason given).

| # | Scenario | Voucher types | Status | Test / notes |
|---|---|---|---|---|
| 1 | Split tender across several banks + cash + mobile money (telebirr) | Cash Sale, Receipt, Cash Purchase, Payment | fixed now | "Deposited to / Paid from" grid: one row per bank/cash/mobile account with method, amount, reference. The entry has one bank line per row. Posting is blocked until "Remaining to allocate" is 0. A single row follows the total automatically. |
| 2 | Bank charges deducted on a row | same | fixed now | Row "Bank Charges": the bank gets the net amount and the charge is booked to 625600. |
| 3 | Mixed currencies on one voucher (USD bank + ETB cash) | same | fixed now | A USD row takes the account currency and the day's rate. The line is booked as USD amount + ETB equivalent. There is no FX difference because the voucher is in ETB. |
| 4 | Foreign-currency invoice (USD) settled in ETB, realised FX | Sales Invoice / Bill + Receipt / Payment | fixed now | "Invoice Currency" on invoices and bills. The receipt shows the USD balance, settles at the receipt-date rate, and Odoo posts the exchange gain/loss (test: 500 gain). |
| 5 | Year-end revaluation of open foreign balances | - | deferred | `om_currency_revaluation` is installed but not configured. It needs revaluation accounts and a first real USD balance to test against. |
| 6 | One receipt for many invoices / many receipts for one invoice / partial | Receipt, Payment | worked | Tests: one CRV with one invoice paid + one partial; a second CRV settles the rest. |
| 7 | Settlement discount / rounding write-off | Receipt, Payment | fixed now | "Discount / Write-off" column per open item. Receipts post to 110900 Sales discounts allowed, payments to 120001 discount received. |
| 8 | Advances both sides, overpayment | Receipt, Payment | worked (last round) | Unapplied amounts go to 305900 Customer advances / 225300 Vendor advances and are offered on the next voucher. |
| 9 | Receipts / payments not tied to invoices (capital, loans, taxes, salaries ...) | Receipt, Payment | worked (last round) | Purposes pre-fill the GL line. |
| 10 | Cheque received, post-dated, deposited | Receipt / Cash Sale + Cheques | fixed now | Cheque rows go to 211100 Cheques on hand. Banking → Cheques → "Deposit / Clear" creates a Bank Transfer to the chosen bank. A post-dated cheque can't be deposited before its date. |
| 11 | Received cheque bounced (before or after deposit) | Cheques | fixed now | Posts a JV: Dr customer (receivable) / Cr cheques on hand or bank, plus bank charges. The amount comes back on the customer's next receipt. |
| 12 | Cheque issued, outstanding, cleared, cancelled | Payment / Cash Purchase + Cheques | fixed now | Issued cheques go to 306500 Outstanding cheques. "Clear" moves them to the bank. "Cancel" makes the vendor owed again, offered on the next payment. |
| 13 | Withholding both sides with receipt number and date | Receipt, Payment, Cash Sale, Cash Purchase | fixed now | "Withholding Receipt No./Date" fields. The number appears on the withholding GL line. |
| 14 | Sales return / credit note against an invoice | Sales Return | fixed now | "Return / Credit Note" button copies the lines (reduce the qty). It settles the open invoice straight away. |
| 15 | Return against a cash sale → refund | Sales Return + Payment | fixed now | The customer gets a credit, which a Payment (CPV) offers as a refund, or the next Receipt can use it. |
| 16 | Purchase return / debit note, vendor refund | Purchase Return + Receipt | fixed now | Against an open bill it settles it. On a paid bill the vendor owes us, and a Receipt offers it. |
| 17 | Mixed VAT 15% / 0% / exempt on one invoice | Sales Invoice, Cash Sale, Bill ... | worked | Per-line taxes. Test: 3 lines, 150 VAT. |
| 18 | TOT (turnover tax) for non-VAT sales | Sales Invoice, Cash Sale | fixed now | Taxes "TOT 2%" and "TOT 10%" added, posting to 300900 TOT payable. |
| 19 | Non-VAT customer / out-of-scope line | all item vouchers | worked | Remove the tax on the line. |
| 20 | Void each type, including after partial settlement | all | worked + fixed | Invoices with payments must have the receipt voided first. A receipt with a deposited cheque, or an advance already used elsewhere, is blocked with a message. |
| 21 | Void in a closed period | all | worked | Reversal dated today (first open date). Posting into a closed period is refused. |
| 22 | Staff advance issued and recovered | Payment + Receipt | worked | 220300 is a receivable, so the receipt offers it as an open item. |
| 23 | Petty cash imprest (top-up, spend) | Bank Transfer + Cash Purchase | fixed now | New Petty Cash account (211200) and journal. Top-up by Bank Transfer, spending by Cash Purchase from Petty cash. |
| 24 | Bank transfer, one source to several destinations + fee | Bank Transfer | fixed now | Destination rows. |
| 25 | Opening balances for the 2025/26 import | Opening Balances (OB) | fixed now | GL, bank, customer and vendor lines (one per open invoice, with reference and due date). The difference goes to 409000 Opening balance equity. Customer/vendor lines become open items on receipts and payments. |
| 26 | New bank / USD / mobile-money accounts | Banking → Bank & Cash Accounts | fixed now | Each journal creates its own GL account, automatically mapped to IFRS "Cash and cash equivalents". |
| 27 | Bank book / cash book show each row with its reference | reports | fixed now | Each bank line is labelled method + slip/cheque/transaction no. |
| 28 | Cost of sales for stock items sold by Cash Sale | Cash Sale | deferred | Credit invoices post COGS (Odoo). Cash sales don't move stock value yet. This belongs with the GRN/GIV stock slice. |
| 29 | Bank reconciliation against statements | - | deferred | Next slice: Peachtree-style "tick cleared items" per bank, using the row references. |
| 30 | Undo a cheque deposit (void the deposit transfer) | Cheques | deferred | The cheque status is not reset automatically when its deposit transfer is voided. Bounce covers the common case. |

Accounts added in this sweep: 211100 Cheques on hand, 211200 Petty cash, 306500 Outstanding cheques, 110900 Sales discounts allowed, 300900 TOT payable, 409000 Opening balance equity. All are mapped to IFRS. Settings: Sonador Books → Setup → Settings.
