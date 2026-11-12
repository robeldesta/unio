"""Payroll: fill from People / Time Off / loans / pay items, overtime rates, tax, posting by cost centre, payment,
bank file data, payslip access, final settlement. Rolled back. Run: odoo shell -d ecogreen < tests_manual/payroll.py"""
from datetime import date
from odoo.exceptions import AccessError
ok = lambda c, m: print(('PASS ' if c else 'FAIL ') + m)  # noqa: E731
S = env['sb.payroll.service']
L = env['sb.payroll.line']
ok(L._tax(10000, env.company) == 1650.0 and L._tax(2000, env.company) == 0 and L._tax(20000, env.company) == 4950.0,
   'PAYE 1395/2025: 2,000 -> 0; 10,000 -> 1,650; 20,000 -> 4,950')
e = env['hr.employee'].search([('active', '=', True), ('user_id', '!=', False), ('current_version_id.wage', '>', 5000),
                               ('sb_pay_profile_id', '!=', False)], limit=1)
e.sb_pension = True
v = e.current_version_id
wage = v.wage
# a loan, a deduction item and 4 unpaid days in the month
env['sb.payroll.loan'].create({'employee_id': e.id, 'kind': 'advance', 'amount': 3000, 'installment': 1000, 'start': date(2026, 9, 11)})
env['sb.payroll.benefit'].create({'employee_id': e.id, 'kind': 'deduction', 'name': 'Union dues', 'amount': 50})
unpaid = env.ref('sonador_timeoff.type_unpaid')
lv = env['sb.leave'].create({'employee_id': e.id, 'type_id': unpaid.id, 'date_from': date(2026, 10, 13), 'date_to': date(2026, 10, 16), 'state': 'approved'})
run_id = S.run_create('2019-04', 'Test Tikimt payroll', 'regular')  # Tikimt 2019 E.C. (11 Oct - 9 Nov 2026): period 4 of the year from Hamle
run = env['sb.payroll.run'].browse(run_id)
line = run.line_ids.filtered(lambda l: l.hr_employee_id == e)
ok(line and abs(line.pay_days - 26) < 0.01, '%s: 26 paid days (4 unpaid leave days) - %s' % (e.name, line.pay_days))
ok(abs(line.basic - round(wage * 26 / 30, 2)) < 0.02, 'basic prorated: %s of %s' % (line.basic, wage))
ok(line.loan_deduction == 1000 and line.benefit_deduction == 50, 'advance 1,000 and union dues 50 deducted')
r = S.line_save(line.id, {'ot_day': 10, 'ot_holiday': 4, 'commission': 2000})
line.invalidate_recordset()
expected_ot = round((10 * 1.5 + 4 * 2.5) * wage / 208, 2)
ok(abs(line.overtime - expected_ot) < 0.02, 'overtime 10 h x1.5 + 4 h x2.5 = %s (%s)' % (expected_ot, line.overtime))
ok(abs(line.taxable - (line.basic + line.house + line.position + line.taxable_allowance + line.overtime + 2000)) < 0.01, 'taxable includes overtime and commission')
ok(abs(line.income_tax - L._tax(line.taxable, env.company)) < 0.01 and abs(line.pension_ee - round(line.basic * 0.07, 2)) < 0.01, 'tax on taxable pay, pension 7% on basic')
ok(abs(line.net - (line.gross - line.income_tax - line.pension_ee - 1050 - line.other_deduction)) < 0.02, 'net = gross - tax - pension - deductions')
before = sum(run.line_ids.mapped('net'))
S.run_action(run.id, 'post')
mv = run.voucher_id.move_id
ok(run.state == 'posted' and abs(sum(mv.line_ids.mapped('debit')) - sum(mv.line_ids.mapped('credit'))) < 0.01, 'posted, entry balanced (%s lines)' % len(mv.line_ids))
net_acc = env.company.sb_pay_net_account_id
posted_net = -sum(mv.line_ids.filtered(lambda l: l.account_id == net_acc).mapped('balance'))
held = sum(b.amount for b in run.line_ids.benefit_item_ids if not b.account_id)
ok(abs(posted_net - before - held) < 0.05, 'net pay on %s per employee, plus deductions without an account still owed (%s)' % (net_acc.code, held))
prof = e.sb_pay_profile_id
ok(any(l.account_id == prof.overtime_account_id and l.debit for l in mv.line_ids), 'overtime booked to the cost centre (%s)' % prof.overtime_account_id.code)
loan_acc = e.sb_staff_account_id or env.company.sb_pay_loan_account_id
ok(any(l.account_id == loan_acc and l.credit >= 1000 for l in mv.line_ids), 'advance recovery credited to %s' % loan_acc.code)
loan = e.sb_loan_ids[:1]; loan.invalidate_recordset(); ok(abs(loan.remaining - 2000) < 0.01, 'advance: 2,000 left after posting')
S.run_action(run.id, 'pay', {'pay_date': '2026-11-09', 'pay_account_id': env['account.account'].search([('account_type', '=', 'asset_cash')], limit=1).id})
ok(run.state == 'paid' and run.payment_id.state == 'posted', 'salary payment recorded')
# self-service
mine = S.with_user(e.user_id).employee()
ok(any(s['run_id'] == run.id for s in mine['slips']), 'employee sees their payslip')
other = env['hr.employee'].search([('active', '=', True), ('id', '!=', e.id)], limit=1)
try:
    S.with_user(e.user_id).employee(other.id); ok(e.user_id.has_group('sonador_books.group_sb_hr'), 'blocked')
except AccessError:
    ok(True, 'employee cannot see someone else\'s pay')
# history from Odoo 18
h = env['sb.payroll.line'].search([('hist', '=', True)], limit=1)
ok(not h or (h.run_id.sb_migrated and h.income_tax == h.hist_tax and h.net == h.hist_net), 'Odoo 18 payslips keep the tax and net paid')
# final settlement: reduction of workforce, no notice
st = S.settlement(e.id, '2026-11-30', 'reduction', False, False)
keys = {l['key'] for l in st['lines']}
ok({'severance', 'notice_pay'} <= keys, 'reduction without notice: severance + 60 days + notice pay (%s)' % [(l['label'][:30], l['amount']) for l in st['lines']])
yrs = st['years']
exp_days = min(min(yrs, 1) * 30 + max(yrs - 1, 0) * 10, 360)
sev = sum(l['amount'] for l in st['lines'] if l['key'] == 'severance' and 'Reduction' not in l['label'])
ok(abs(sev - exp_days * st['daily']) < st['daily'] * 0.2, 'severance %s days for %s years (art. 40)' % (round(exp_days, 1), yrs))
st2 = S.settlement(e.id, '2026-11-30', 'resign', True, False)
ok(('severance' in {l['key'] for l in st2['lines']}) == (st2['years'] >= 5), 'resignation: severance only after 5 years (art. 39(1)(h))')
env.cr.rollback()
