# Bank reconciliation tests. Rolled back at the end.
import base64, io
from odoo import Command, fields
from odoo.exceptions import UserError
V = env['sb.voucher']
def acc(c):
    # a standard-chart account the company never used is archived (hidden): the tests use it, so it comes back here
    a = env['account.account'].with_context(active_test=False).search([('code', '=', c)], limit=1)
    if a and not a.active:
        a.active = True
    return a
ok = []


def check(cond, msg):
    print(('PASS ' if cond else 'FAIL ') + msg)
    ok.append(bool(cond))


J = env['account.journal']
cbe = J.create({'name': 'CBE Test', 'type': 'bank', 'code': 'CBT'}).default_account_id
cust = env['res.partner'].create({'name': 'Rec Customer'})
vend = env['res.partner'].create({'name': 'Rec Vendor'})
D = fields.Date.from_string


def receipt(amount, day, ref):
    v = V.create({'voucher_type': 'receipt', 'partner_id': cust.id, 'date': D(day), 'reference': ref,
                  'cash_line_ids': [Command.create({'account_id': cbe.id, 'method': 'transfer', 'reference': ref})],
                  'line_ids': [Command.create({'account_id': acc('120200').id, 'name': 'Income', 'amount': amount})]})
    v.action_post()
    return v


def payment(amount, day, ref):
    v = V.create({'voucher_type': 'payment', 'partner_id': vend.id, 'date': D(day), 'reference': ref,
                  'cash_line_ids': [Command.create({'account_id': cbe.id, 'method': 'transfer', 'reference': ref})],
                  'line_ids': [Command.create({'account_id': acc('625200').id, 'name': 'Rent', 'amount': amount})]})
    v.action_post()
    return v


receipt(50000, '2026-09-12', 'DEP-1')
receipt(20000, '2026-09-25', 'DEP-2')
payment(15000, '2026-09-20', 'CHQ-101')
payment(4000, '2026-10-05', 'CHQ-102')      # after statement date
receipt(8000, '2026-10-08', 'DEP-3')         # deposit in transit (statement date 10 Oct)

# ---- manual reconciliation
R = env['sb.bank.rec']
r = R.create({'account_id': cbe.id, 'date': D('2026-10-10'), 'statement_balance': 54990})
r.action_load()
check(len(r.line_ids) == 5, 'loads every uncleared bank line up to the statement date (%d)' % len(r.line_ids))
check(abs(r.difference - 54990) < 0.01, 'nothing ticked: difference is the statement balance')
for l in r.line_ids:
    if (l.ref or '').split(' ')[0] in ('DEP-1', 'DEP-2', 'CHQ-101'):
        l.cleared = True
r.invalidate_recordset()
check(abs(r.difference - (-10)) < 0.01, 'after ticking, difference -10 = bank charge not yet in books (%s)' % r.difference)
try:
    r.action_finish(); check(False, 'cannot finish with a difference')
except UserError:
    check(True, 'cannot finish with a difference')
r.charges = 10
r.action_post_adjustments()
r.invalidate_recordset()
check(abs(r.difference) < 0.01 and len(r.adjust_voucher_ids) == 1, 'charges posted and auto-ticked: difference 0')
check(abs(r.outstanding_in - 8000) < 0.01 and abs(r.outstanding_out - 4000) < 0.01, 'deposit in transit 8,000; outstanding cheque 4,000')
r.action_finish()
check(r.state == 'done' and all(l.move_line_id.sb_cleared == l.cleared for l in r.line_ids), 'finished: cleared flags written to the ledger')
doc = env['sb.report'].get_report('bank_rec', {'rec_id': r.id, 'date_from': '2026-09-01', 'date_to': '2026-10-10'})
diff = next(x for x in doc['rows'] if x['id'] == 'diff')['values']['amount']
check(abs(diff) < 0.01 and any(c['label'] == 'Status' and c['value'] == 'Reconciled' for c in doc['summary']),
      'reconciliation report: statement + transit - outstanding = books')

# ---- next month by statement import (CSV, CBE style)
csv_text = '''Account statement,,,,,
Date,Particulars,Reference,Debit,Credit,Balance
09/10/2026,Cheque 102,CHQ-102,"4,000.00",,50990.00
12/10/2026,Cash deposit,DEP-3,,"8,000.00",58990.00
20/10/2026,Transfer in,DEP-4,,"12,500.00",71490.00
31/10/2026,Service charge,SC-10,25.00,,71465.00
31/10/2026,Interest,INT-10,,40.00,71505.00
'''
receipt(12500, '2026-10-19', 'DEP-4')
r2 = R.create({'account_id': cbe.id, 'date': D('2026-10-31')})
check(abs(r2.opening_balance - 54990) < 0.01, 'next reconciliation opens with last statement balance')
r2.write({'statement_file': base64.b64encode(csv_text.encode()), 'statement_filename': 'cbe_oct.csv'})
r2.action_import()
check(len(r2.stmt_ids) == 5 and abs(r2.statement_balance - 71505) < 0.01, 'CSV imported: 5 lines, ending balance from the file')
matched = r2.stmt_ids.filtered('matched_line_id')
check(len(matched) == 3, 'auto-matched cheque, deposit in transit and new deposit (%d)' % len(matched))
un = r2.stmt_ids.filtered(lambda s: not s.matched_line_id)
un.filtered(lambda s: s.amount < 0).account_id = acc('625600')
un.filtered(lambda s: s.amount > 0).account_id = acc('120100')
r2.action_create_from_statement()
r2.invalidate_recordset()
check(not r2.stmt_ids.filtered(lambda s: not s.matched_line_id) and abs(r2.difference) < 0.01,
      'unmatched charge and interest became vouchers and matched: difference %s' % r2.difference)
r2.action_finish()
check(r2.state == 'done', 'second month reconciled')
try:
    r.action_reopen(); check(False, 'earlier reconciliation cannot be reopened before the later one')
except UserError:
    check(True, 'earlier reconciliation cannot be reopened before the later one')
r2.action_reopen()
check(r2.state == 'draft' and not env['account.move.line'].search_count([('sb_rec_id', '=', r2.id)]), 'reopen clears the flags')

# ---- Excel import with a single Amount column
import openpyxl
wb = openpyxl.Workbook(); ws = wb.active
ws.append(['Value Date', 'Narration', 'Amount', 'Running Balance'])
ws.append([fields.Date.from_string('2026-10-12'), 'Cash deposit DEP-3', 8000, 0])
ws.append(['20-Oct-2026', 'Transfer in', '12,500.00', 0])
buf = io.BytesIO(); wb.save(buf)
r3 = R.create({'account_id': cbe.id, 'date': D('2026-10-31')})
check(r3 == r2, 'a new reconciliation for the same account and date carries on with the one in progress (no second draft)')
r3.action_clear_none()
r3.write({'statement_file': base64.b64encode(buf.getvalue()), 'statement_filename': 'awash.xlsx'})
r3.action_import()
check(len(r3.stmt_ids) == 2 and len(r3.stmt_ids.filtered('matched_line_id')) == 2, 'Excel with a single Amount column imported and matched')

# ---- a difference left by a finished reconciliation is carried into the next one, and cleared there once
cbe2 = J.create({'name': 'CBE Test 2', 'type': 'bank', 'code': 'CBT2'}).default_account_id
v1 = V.create({'voucher_type': 'receipt', 'partner_id': cust.id, 'date': D('2026-09-01'),
               'cash_line_ids': [Command.create({'account_id': cbe2.id, 'method': 'transfer'})],
               'line_ids': [Command.create({'account_id': acc('120200').id, 'name': 'Income', 'amount': 1000})]})
v1.action_post()
ra = R.create({'account_id': cbe2.id, 'date': D('2026-09-10'), 'statement_balance': 1000})
ra.action_load(); ra.action_clear_all(); ra.action_finish()
# a rounding the books took in that month, absorbed by the finished reconciliation (as CBE's -0.01 of 10 Oct)
jv = env['account.move'].create({'move_type': 'entry', 'date': D('2026-09-05'), 'journal_id': J.search([('type', '=', 'general')], limit=1).id,
                                 'line_ids': [Command.create({'account_id': cbe2.id, 'debit': 0.05}),
                                              Command.create({'account_id': acc('625600').id, 'credit': 0.05})]})
jv.action_post()
jv.line_ids.filtered(lambda l: l.account_id == cbe2).write({'sb_cleared': True, 'sb_rec_id': ra.id})
ra.invalidate_recordset()
check(abs(ra.unexplained - 0.05) < 0.001, 'carried: the finished reconciliation now leaves 0.05 not explained (%s)' % ra.unexplained)
rb = R.create({'account_id': cbe2.id, 'date': D('2026-09-30'), 'statement_balance': 1000})
rb.action_load()
check(rb.carry_from_id == ra and abs(rb.carried_diff - 0.05) < 0.001 and abs(rb.unexplained - 0.05) < 0.001,
      'carried: the next reconciliation shows "carried from" %s: %s' % (ra.name, rb.carried_diff))
try:
    with env.cr.savepoint():
        rb.action_finish()
    check(False, 'carried: Finish refused while the carried difference is open')
except UserError as e:
    check('Clear carried difference' in str(e), 'carried: Finish refused, pointing to "Clear carried difference"')
rb.action_clear_carried()
rb.invalidate_recordset()
cv = rb.carry_voucher_ids
check(len(cv) == 1 and cv.state == 'posted' and cv.date == rb.date and not rb.carried_open and abs(rb.unexplained) < 0.001
      and not rb.line_ids.filtered(lambda l: l.move_line_id.move_id == cv.move_id),
      'carried: cleared by %s dated %s (not an item to tick); proof 0' % (cv.name, cv.date))
rb.action_load()
check(not rb.line_ids.filtered(lambda l: l.move_line_id.move_id == cv.move_id), 'carried: Load items does not bring the clearing entry back')
rb.action_finish()
check(rb.state == 'done', 'carried: Finish works after clearing')
check(any('cleared by' in (m.body or '') for m in ra.message_ids), 'carried: logged on the earlier reconciliation too')
rc = R.create({'account_id': cbe2.id, 'date': D('2026-10-10'), 'statement_balance': 1000})
rc.action_load()
check(not rc.carried_diff and abs(rc.unexplained) < 0.001 and not rc.line_ids, 'carried: the month after starts clean')
doc = env['sb.report'].get_report('bank_rec', {'rec_id': rc.id, 'date_from': '2026-09-10', 'date_to': '2026-10-10'})
check(any(c['label'] == 'Status' and c['value'].startswith('In progress') for c in doc['summary']),
      'report of a reconciliation in progress says so, not "Reconciled"')
print('RESULT', 'ALL PASS (%d checks)' % len(ok) if all(ok) else '%d FAILED of %d' % (ok.count(False), len(ok)))
env.cr.rollback()
