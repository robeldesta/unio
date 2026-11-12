# Payroll tests with Robel's figures. Rolled back at the end.
from odoo import fields
ok = []


def check(cond, msg):
    print(('PASS ' if cond else 'FAIL ') + msg)
    ok.append(bool(cond))


env.company.fiscalyear_lock_date = False
E = env['sb.employee']
robel = E.create({'name': 'Test GM', 'job': 'General Manager', 'basic_salary': 36000})
eyer = E.create({'name': 'Test Finance', 'job': 'Finance', 'basic_salary': 20000, 'nontaxable_allowance': 2800})
check(robel.partner_id and eyer.partner_id, 'employees get a contact for their salary balance')
run = env['sb.payroll.run'].create({'period': '2023-01', 'pay_date': fields.Date.from_string('2030-08-06')})  # test year, clear of real data
run.line_ids = [(0, 0, {'employee_id': robel.id, 'basic': 36000}),
                (0, 0, {'employee_id': eyer.id, 'basic': 20000, 'nontaxable_allowance': 2800})]
lr, le = run.line_ids.sorted('id')
check(abs(lr.income_tax - 10550) < 0.01 and abs(lr.pension_ee - 2520) < 0.01 and abs(lr.net - 22930) < 0.01,
      'GM: PAYE %s, pension %s, net %s (expected 10,550 / 2,520 / 22,930)' % (lr.income_tax, lr.pension_ee, lr.net))
check(abs(le.income_tax - 4950) < 0.01 and abs(le.pension_ee - 1400) < 0.01 and abs(le.net - 16450) < 0.01,
      'Finance: PAYE %s, pension %s, net %s (expected 4,950 / 1,400 / 16,450)' % (le.income_tax, le.pension_ee, le.net))
check(run.name.startswith('Payroll Hamle') and run.date == run._period()[3], 'posts on the last day of the Ethiopian month (%s)' % run.date)
check(abs(run.total_tax - 15500) < 0.01 and abs(run.total_pension_ee + run.total_pension_er - 10080) < 0.01,
      'month totals: PAYE 15,500, pension 10,080 (7%% + 11%%)')
run.action_post()
e = {}
for l in run.voucher_id.move_id.line_ids:
    e[l.account_id.code] = round(e.get(l.account_id.code, 0) + l.balance, 2)
check(e == {'611100': 56000.0, '612100': 2800.0, '613100': 6160.0, '300400': -39380.0, '300800': -15500.0, '300300': -10080.0},
      'payroll entry %s' % e)
run.action_pay()
p = run.payment_id
pe = {}
for l in p.move_id.line_ids:
    pe[l.account_id.code] = round(pe.get(l.account_id.code, 0) + l.balance, 2)
bank = run.pay_account_id.code
check(run.state == 'paid' and pe == {'300400': 39380.0, bank: -39380.0} and p.date == run.pay_date,
      'net salaries paid on 6 Aug by %s: %s' % (p.name, pe))
open_sal = env['account.move.line'].search([('account_id.code', '=', '300400'), ('reconciled', '=', False),
                                            ('partner_id', 'in', (robel.partner_id | eyer.partner_id).ids)])
check(not open_sal, 'salary payable cleared per employee')
R = env['sb.report']
o = {'date_from': fields.Date.to_string(run.date_from), 'date_to': fields.Date.to_string(run.date)}
reg = R.get_report('payroll_register', o)
t = next(r for r in reg['rows'] if r['id'] == 'total')['values']
check(abs(t['net'] - 39380) < 0.01 and abs(t['gross'] - 58800) < 0.01, 'payroll register totals')
paye = R.get_report('paye_declaration', o)
check(abs(next(r for r in paye['rows'] if r['id'] == 'total')['values']['income_tax'] - 15500) < 0.01, 'PAYE declaration 15,500')
po = R.get_report('poessa_declaration', o)
check(abs(next(r for r in po['rows'] if r['id'] == 'total')['values']['total'] - 10080) < 0.01, 'POESSA declaration 10,080')
pdf = R.render_pdf(R.payslip_doc(run))
check(pdf[:4] == b'%PDF', 'payslips PDF')
run.action_reset()
check(run.state == 'draft' and run.voucher_id.id is False, 'reset voids the entry and the payment')
print('RESULT', 'ALL PASS (%d checks)' % len(ok) if all(ok) else '%d FAILED of %d' % (ok.count(False), len(ok)))
env.cr.rollback()
