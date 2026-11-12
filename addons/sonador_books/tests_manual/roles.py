# Role tests: auditor / clerk / accountant / manager. Rolled back at the end.
from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
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


def denied(fn):
    try:
        fn()
    except (AccessError, UserError):
        return True
    return False


env.company.fiscalyear_lock_date = False
U = env['res.users'].with_context(no_reset_password=True)
users = {}
for role in ('auditor', 'clerk', 'accountant', 'manager'):
    users[role] = U.create({'name': 'Test %s' % role, 'login': 'test_%s@sonador.local' % role,
                            'group_ids': [Command.set([env.ref('sonador_books.group_sb_%s' % role).id])]})
check(users['manager'].has_group('account.group_account_manager') and users['clerk'].has_group('account.group_account_invoice')
      and not users['auditor'].has_group('account.group_account_invoice'), 'roles imply the right Odoo groups')
check(env.ref('base.user_admin').has_group('sonador_books.group_sb_manager'), 'admin is a Manager')
bank = acc('211001')
d = fields.Date.from_string('2026-10-10')
vals = {'voucher_type': 'cash_sale', 'date': d, 'cash_account_id': bank.id,
        'line_ids': [Command.create({'name': 'Hosting', 'account_id': acc('110300').id, 'price_unit': 100})]}
V = env['sb.voucher']
check(denied(lambda: V.with_user(users['auditor']).create(dict(vals))), 'auditor cannot create vouchers')
cs = V.with_user(users['clerk']).create(dict(vals))
cs.action_post()
check(cs.state == 'posted', 'clerk posts a cash sale')
check(denied(lambda: cs.with_user(users['clerk']).action_void()), 'clerk cannot void')
check(denied(lambda: cs.with_user(users['clerk']).action_edit()), 'clerk cannot edit a posted voucher')
jv = V.with_user(users['clerk']).create({'voucher_type': 'journal', 'date': d, 'line_ids': [
    Command.create({'account_id': acc('625100').id, 'debit': 10}), Command.create({'account_id': acc('305600').id, 'credit': 10})]})
check(denied(jv.action_post), 'clerk cannot post a journal voucher')
jv.with_user(users['accountant']).action_post()
check(jv.state == 'posted', 'accountant posts the journal voucher')
cs.with_user(users['accountant']).action_void()
check(cs.state == 'void', 'accountant voids')
rows = env['sb.report'].with_user(users['auditor']).get_report('trial_balance', {'date_from': '2026-07-08', 'date_to': '2027-07-07'})
check(rows['rows'], 'auditor reads the trial balance')
check(V.with_user(users['auditor']).search_count([]) > 0, 'auditor sees vouchers')
w = env['sb.period.wizard'].with_user(users['accountant']).create({})
check(denied(lambda: w.line_ids[0].with_user(users['accountant']).action_close_through()), 'accountant cannot close periods')
w2 = env['sb.period.wizard'].with_user(users['manager']).create({})
line = w2.line_ids.filtered(lambda l: l.fiscal_year == 2018)[:1]
line.with_user(users['manager']).action_close_through()
check(env.company.fiscalyear_lock_date == line.date_to, 'manager closes a period')
menus = env['ir.ui.menu'].with_user(users['auditor'])._visible_menu_ids()
check(env.ref('sonador_books.menu_sb_root').id in menus and env.ref('sonador_books.menu_sb_jv').id not in menus,
      'auditor sees the app but not accountant-only menus')
print('RESULT', 'ALL PASS (%d checks)' % len(ok) if all(ok) else '%d FAILED of %d' % (ok.count(False), len(ok)))
env.cr.rollback()
