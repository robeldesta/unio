# GL integrity, auditor round 4 (defects 1-7): attacks as clerk / accountant / manager must be refused, normal work must go on.
# Run: ~/ecogreen-odoo19/sync/local_shell.sh < tests_manual/gl_round4.py   (database ecogreen; rolled back at the end)
import traceback
from datetime import date, datetime, timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.addons.sonador_books.models.ethiopian_calendar import fiscal_periods, fiscal_year_of

assert env.cr.dbname == 'ecogreen'
ok = []


def check(cond, msg):
    print(('PASS ' if cond else 'FAIL ') + msg)
    ok.append(bool(cond))


def refused(fn, label):
    try:
        with env.cr.savepoint():
            fn()
    except (AccessError, UserError, ValidationError, ValueError) as e:
        print('PASS refused: %s  -> "%s"' % (label, str(e).splitlines()[0][:220]))
        ok.append(True)
        return True
    print('FAIL NOT refused: ' + label)
    ok.append(False)
    return False


def works(fn, label):
    try:
        with env.cr.savepoint():
            res = fn()
        print('PASS works: ' + label)
        ok.append(True)
        return res
    except Exception as e:  # noqa: BLE001
        print('FAIL broken: %s  -> %s' % (label, e))
        traceback.print_exc()
        ok.append(False)
        return None


acc = lambda c: env['account.account'].search([('code', '=', c)], limit=1)  # noqa: E731
company = env.company
U = env['res.users'].with_context(no_reset_password=True)
u = {}
for role in ('clerk', 'accountant', 'manager'):
    u[role] = U.create({'name': 'R4 test %s' % role, 'login': 'r4test_%s@unio.local' % role, 'email': 'r4%s@unio.local' % role,
                        'company_id': company.id, 'company_ids': [Command.set([company.id])],
                        'group_ids': [Command.set([env.ref('sonador_books.group_sb_%s' % role).id])]})
clerk, accountant, manager = u['clerk'], u['accountant'], u['manager']
V = env['sb.voucher']
M = env['account.move']
bank, income = acc('1104-0001'), acc('4000-0001')
expense = env['account.account'].search([('account_type', '=', 'expense'), ('active', '=', True)], limit=1)
customer = env['res.partner'].create({'name': 'R4 Customer', 'customer_rank': 1})
supplier = env['res.partner'].create({'name': 'R4 Supplier PLC', 'supplier_rank': 1, 'is_company': True, 'vat': '0011223344'})
today = fields.Date.today()
d = date(2026, 10, 20)
cpv_journal = env['account.journal'].search([('code', '=', 'CPV')], limit=1)
jv_journal = env['account.journal'].search([('code', '=', 'JV')], limit=1)
print('LOCK', company.fiscalyear_lock_date, 'closed year through', company._sb_closed_through(), 'today', today)


def cash_sale(user, fs, vtype='cash_sale', amount=100):
    vals = {'voucher_type': vtype, 'date': d, 'partner_id': customer.id, 'sb_pad_number': fs,
            'line_ids': [Command.create({'name': 'Test', 'account_id': income.id, 'quantity': 1, 'price_unit': amount, 'tax_ids': []})]}
    if vtype == 'cash_sale':
        vals['cash_account_id'] = bank.id
    v = V.with_user(user).create(vals)
    v.with_user(user).action_post()
    return v


def journal(user, amount=10, day=d, post=True):
    v = V.with_user(user).create({'voucher_type': 'journal', 'date': day, 'line_ids': [
        Command.create({'account_id': expense.id, 'debit': amount}), Command.create({'account_id': bank.id, 'credit': amount})]})
    if post:
        v.with_user(user).action_post()
    return v


def raw_move(user, post=True, journal=jv_journal, **extra):
    m = M.with_user(user).create(dict({'move_type': 'entry', 'journal_id': journal.id, 'date': d, 'line_ids': [
        Command.create({'account_id': expense.id, 'debit': 5}), Command.create({'account_id': bank.id, 'credit': 5})]}, **extra))
    if post:
        m.with_user(user).action_post()
    return m


def bill(user, amount=1000):
    v = V.with_user(user).create({'voucher_type': 'bill', 'partner_id': supplier.id, 'date': d, 'line_ids': [Command.create(
        {'account_id': expense.id, 'name': 'Goods', 'quantity': 2, 'price_unit': amount / 2, 'tax_ids': []})]})
    v.with_user(user).action_post()
    return v


def payable(b):
    return b.move_id.line_ids.filtered(lambda l: l.account_id.account_type == 'liability_payable')


def payment(user, b, amount):
    v = V.with_user(user).create({'voucher_type': 'payment', 'partner_id': supplier.id, 'date': d, 'wht_supply': 'goods',
                                  'cash_line_ids': [Command.create({'account_id': bank.id, 'method': 'transfer'})],
                                  'apply_ids': [Command.create({'line_id': payable(b).id, 'amount_applied': amount, 'pay': True})]})
    v._onchange_sync_single_deposit()
    v.with_user(user).action_post()
    return v


# ====================================================================== 1. fiscal receipts
print('\n--- 1. fiscal receipts (FS00001848 exists)')
fs1848 = V.search([('name', '=', 'FS00001848'), ('state', '=', 'posted')], limit=1)
check(bool(fs1848), 'FS00001848 is on file (%s)' % fs1848.voucher_type)
for typed in ('FS00001848A', 'FS00001848 X', 'FS 1848 COPY', 'FS00001848/XYZ', 'FSS00001848', 'FSN 1848', 'FS N 1848', 'FS 1,848',
              'FS00001848', 'FS-00001848', 'FS No. 1848', 'ＦＳ００００１８４８', '1848', 'FS 1.848', 'FS00001848-1', 'XFS00001848'):
    refused(lambda: cash_sale(clerk, typed), 'clerk cash sale "%s"' % typed)
refused(lambda: cash_sale(clerk, 'FS00001848A', vtype='sale'), 'clerk credit invoice FS00001848A')
refused(lambda: cash_sale(clerk, 'FS00991999Q'), 'unknown letter after a new number FS00991999Q')
new = works(lambda: cash_sale(clerk, 'FS00991971'), 'clerk cash sale with a new fiscal number FS00991971')
check(new.state == 'posted', 'new cash sale posted as %s' % new.name)
refused(lambda: cash_sale(clerk, 'FS 991,971'), 'FS 991,971 is FS00991971 again')
works(lambda: cash_sale(clerk, 'FS00991972/CLA', vtype='sale'), 'a known register (…/CLA) still accepted')
works(lambda: cash_sale(clerk, 'FS 00991973'), 'spaces between letters and number still accepted')
works(lambda: cash_sale(clerk, '00991974'), 'a bare number still accepted (takes the register)')
works(lambda: cash_sale(clerk, 'CSI 0991975', vtype='sale'), 'the CSI register (from the books) accepted')
refused(lambda: cash_sale(clerk, 'FS00001848-5'), 'FS00001848-5 (a number cut in two)')
works(lambda: cash_sale(clerk, 'VAN-074-FS00991979'), 'the van register VAN-nnn-FS (from the books) accepted')
work = works(lambda: cash_sale(clerk, 'FS00991976'), 'cash sale FS00991976 to void')
vv = cash_sale(clerk, 'FS00991977')
vv.with_user(accountant).action_void()
refused(lambda: cash_sale(clerk, 'FS00991977'), 'a voided number is not issued again')
company.sb_fiscal_registers = 'MOR'
works(lambda: cash_sale(clerk, 'MOR 0991978'), 'a register added in settings (MOR) accepted')
company.sb_fiscal_registers = False

# ====================================================================== 2. duplicate document numbers across journals
print('\n--- 2. entry numbers')
jv_name = V.search([('name', '=like', 'JV-1%'), ('state', '=', 'posted')], limit=1).name
for typed, jr in ((jv_name, cpv_journal), ('BR-P/0034', jv_journal), ('FS00001848', cpv_journal), ('VOID/JV-094', cpv_journal)):
    refused(lambda: raw_move(accountant, journal=jr, name=typed), 'accountant posts a raw entry typed "%s" in %s' % (typed, jr.code))
    refused(lambda: raw_move(manager, journal=jr, name=typed), 'manager posts a raw entry typed "%s"' % typed)
refused(lambda: raw_move(accountant, post=False).with_user(accountant).write({'name': jv_name}), 'accountant renames a draft entry %s' % jv_name)
refused(lambda: M.with_user(accountant).with_context(default_name=jv_name).create(
    {'move_type': 'entry', 'journal_id': cpv_journal.id, 'date': d}), 'accountant passes default_name')
plain = works(lambda: raw_move(accountant, journal=cpv_journal), 'accountant posts a raw entry numbered by the journal')
check(plain.name and plain.name != '/', 'numbered by the journal: %s' % plain.name)
from odoo.addons.sonador_books.models.move_guard import controlled
refused(lambda: controlled(M.with_user(accountant)).create({'move_type': 'entry', 'journal_id': cpv_journal.id, 'date': d, 'name': jv_name.replace('-', '/'),
        'line_ids': [Command.create({'account_id': expense.id, 'debit': 5}), Command.create({'account_id': bank.id, 'credit': 5})]})._post(),
        'even a flow cannot post %s again under another separator' % jv_name)

# ====================================================================== 3. future locks
print('\n--- 3. year end and periods')
W = env['sb.year.end.wizard'].with_user(manager)
cur = fiscal_year_of(today)
check(str(cur) not in [k for k, _l in W._year_choices()], 'the current year (ends %s) is not offered' % fiscal_periods(cur)[-1][3])
refused(lambda: W.create({'year': str(cur), 'retained_account_id': acc('3000-0003').id}).action_close_year(), 'manager closes the current year')
w2 = W.create({'year': str(cur - 1), 'retained_account_id': acc('3000-0003').id})
refused(lambda: (w2.write({'year': str(cur)}), w2.action_close_year()), 'current year written over RPC, then Close')
pw = env['sb.period.wizard'].with_user(manager).create({})
last_ended = pw._sb_last_ended()
print('   last ended period ends', last_ended)
refused(lambda: (pw.write({'close_to': date(2026, 12, 31)}), pw.action_close_confirm()), 'manager closes through 2026-12-31 over RPC')
refused(lambda: (pw.write({'close_to': today}), pw.action_close_confirm()), 'manager closes through today')
refused(lambda: (pw.write({'close_to': last_ended - timedelta(days=3)}), pw.action_close_confirm()), 'close through a date that is not a period end')
refused(lambda: pw.line_ids.filtered(lambda l: l.date_to > today)[:1].action_close_through(), 'Close through on the current period')
refused(lambda: company.with_user(manager).write({'fiscalyear_lock_date': date(2026, 12, 31)}), 'manager writes a future lock date on the company')
# (Edit in a locked period: defect 7) a voucher in the last ended period, then the period closed
old_jv = journal(accountant, day=last_ended - timedelta(days=2))
works(lambda: (pw.write({'close_to': last_ended}), pw.action_close_confirm()), 'manager closes through the last ended period %s' % last_ended)
company.invalidate_recordset()
check(company.fiscalyear_lock_date == last_ended, 'books closed through %s' % company.fiscalyear_lock_date)
log = env['sb.lock.log'].search([('company_id', '=', company.id)], limit=1)
check(log.event == 'lock' and log.new_date == last_ended, 'close logged: %s %s %s' % (log.event, log.new_date, log.note))

print('\n--- 7. Edit in a closed period')
refused(lambda: old_jv.with_user(accountant).action_edit(), 'accountant Edit of %s dated %s (closed)' % (old_jv.name, old_jv.date))
check(old_jv.state == 'posted' and not old_jv.sb_editing, 'voucher untouched')
prev = fiscal_periods(fiscal_year_of(last_ended))
start_prev = next(s for _n, _m, s, e in prev if e == last_ended)
refused(lambda: (pw.write({'close_to': start_prev - timedelta(days=1)}), pw.action_close_confirm()),
        'manager "closes" through an earlier date (a reopen without a reason)')
refused(lambda: (pw.write({'reopen_from': start_prev, 'reopen_reason': ''}), pw.action_reopen_confirm()), 'reopen without a reason')
refused(lambda: (pw.write({'reopen_from': last_ended + timedelta(days=1), 'reopen_reason': 'x'}), pw.action_reopen_confirm()),
        'reopen a period that is not closed (moves the lock forward)')
works(lambda: (pw.write({'reopen_from': start_prev, 'reopen_reason': 'R4 correction of a supplier bill'}), pw.action_reopen_confirm()),
      'manager reopens %s with a reason' % start_prev)
log = env['sb.lock.log'].search([('company_id', '=', company.id)], limit=1)
check(log.event == 'unlock' and 'R4 correction' in (log.note or '') and 'Reopened' in (log.note or ''),
      'reopen logged as "%s" / %s' % (log.event, log.note))

# ====================================================================== 4. posted bills frozen
print('\n--- 4. posted bill and entry fields')
b = bill(clerk)
inv_line = b.move_id.invoice_line_ids[:1]
prod = env['product.product'].search([], limit=1)
analytic = env['account.analytic.account'].search([], limit=1)
for field, value in (('price_unit', 1.0), ('quantity', 99), ('discount', 50), ('product_id', prod.id), ('name', 'changed'),
                     ('date_maturity', date(2030, 1, 1)), ('analytic_distribution', {str(analytic.id): 100} if analytic else {'1': 100})):
    refused(lambda: inv_line.with_user(clerk).write({field: value}), 'clerk writes %s on a posted bill line (%s)' % (field, b.name))
    refused(lambda: inv_line.with_user(manager).write({field: value}), 'manager writes %s on a posted bill line' % field)
refused(lambda: payable(b).with_user(clerk).write({'date_maturity': date(2030, 1, 1)}), 'clerk changes the payable line due date')
for field, value in (('invoice_date_due', date(2030, 1, 1)), ('payment_reference', 'forged'), ('checked', not b.move_id.checked),
                     ('partner_bank_id', env['res.partner.bank'].create({'partner_id': supplier.id, 'acc_number': 'R4-99887766'}).id), ('invoice_origin', 'x'), ('delivery_date', date(2026, 1, 1))):
    refused(lambda: b.move_id.with_user(clerk).write({field: value}), 'clerk writes %s on the posted bill' % field)
for field, value in (('invoice_date_due', date(2030, 1, 1)), ('payment_reference', 'forged')):
    refused(lambda: b.move_id.with_user(manager).write({field: value}), 'manager writes %s on the posted bill' % field)
works(lambda: b.move_id.with_user(accountant).write({'checked': not b.move_id.checked}), 'accountant marks the bill reviewed')
check(any('Reviewed' in (m.body or '') or 'review' in (m.body or '').lower() for m in b.move_id.message_ids[:2]), 'review noted in the chatter')

# ====================================================================== 6. unreconcile; regression: supplier payment
print('\n--- 6. reconciliation')
pay = works(lambda: payment(clerk, b, 1000), 'clerk pays the bill (payment voucher applies it)')
check(b.move_id.payment_state in ('paid', 'in_payment'), 'bill paid (%s)' % b.move_id.payment_state)
refused(lambda: payable(b).with_user(clerk).remove_move_reconcile(), 'clerk unreconciles the bill')
refused(lambda: payable(b).matched_debit_ids.with_user(clerk).unlink(), 'clerk deletes the partial reconcile')
refused(lambda: payable(b).full_reconcile_id.with_user(clerk).unlink(), 'clerk deletes the full reconcile')
lines = payable(b) | payable(b).matched_debit_ids.debit_move_id
works(lambda: payable(b).with_user(accountant).remove_move_reconcile(), 'accountant unreconciles')
check(b.move_id.payment_state == 'not_paid', 'bill open again')
for mv in lines.move_id:
    check(any('Unreconciled' in (m.body or '') and accountant.name in (m.body or '') for m in mv.message_ids[:3]),
          'unreconcile in the chatter of %s' % mv.name)
works(lambda: lines.with_user(accountant).reconcile(), 'accountant reconciles them again')

# ====================================================================== 5. audit trail
print('\n--- 5. chatter and attachments')
jv94 = M.search([('name', '=', 'JV-094')], limit=1) or plain
admin_partner = env.ref('base.user_admin').partner_id
msg = env['mail.message'].with_user(clerk).create({'model': 'account.move', 'res_id': jv94.id, 'body': 'approved by the CEO',
                                                   'author_id': admin_partner.id, 'date': '2024-01-01 08:00:00',
                                                   'message_type': 'comment', 'email_from': 'admin@example.com'})
check(msg.author_id == clerk.partner_id and msg.date > datetime(2026, 1, 1), 'forged message: author %s, date %s' % (msg.author_id.name, msg.date))
m2 = jv94.with_user(accountant).message_post(body='note', author_id=admin_partner.id, date='2024-01-01 08:00:00',
                                             message_type='comment', subtype_xmlid='mail.mt_note')
check(m2.author_id == accountant.partner_id and m2.date > datetime(2026, 1, 1), 'message_post with another author: %s, %s' % (m2.author_id.name, m2.date))
refused(lambda: env['mail.message'].with_user(clerk).create({'model': 'res.partner', 'res_id': customer.id, 'body': 'x'})
        .with_user(clerk).write({'model': 'account.move', 'res_id': jv94.id}), 'clerk moves a message onto an entry')
# server-side posting keeps its author (system flows)
m3 = jv94.sudo().message_post(body='system note', author_id=admin_partner.id)
check(m3.author_id == admin_partner, 'system message_post keeps its author')
# tracking on a voucher by a normal user
draft = journal(clerk, post=False) if False else V.with_user(clerk).create({'voucher_type': 'receipt', 'date': d, 'partner_id': customer.id})
env.flush_all()
env.cr.precommit.run()
draft.with_user(clerk).write({'partner_id': supplier.id, 'date': d - timedelta(days=1)})
env.flush_all()
env.cr.precommit.run()  # Odoo writes tracking messages when the request commits
tracked = draft.sudo().message_ids.filtered('tracking_value_ids')
check(tracked and tracked[0].author_id == clerk.partner_id, 'tracking message on a voucher by the clerk (%s)' % tracked[:1].author_id.name)
att = env['ir.attachment'].with_user(clerk).create({'name': 'invoice.pdf', 'raw': b'%PDF test', 'res_model': 'account.move', 'res_id': b.move_id.id})
refused(lambda: att.with_user(clerk).unlink(), 'clerk deletes an attachment of a posted bill')
refused(lambda: att.with_user(manager).unlink(), 'manager deletes it')
refused(lambda: att.with_user(clerk).write({'raw': b'other'}), 'clerk replaces its content')
refused(lambda: att.with_user(clerk).write({'res_id': plain.id}), 'clerk moves it to another entry')
datt = env['ir.attachment'].with_user(clerk).create({'name': 'draft.pdf', 'raw': b'x', 'res_model': 'sb.voucher', 'res_id': draft.id})
works(lambda: datt.with_user(clerk).unlink(), 'clerk deletes an attachment of a draft voucher')

# ====================================================================== regression: normal accounting work
print('\n--- regression')
jv = works(lambda: journal(accountant), 'accountant posts a JV')
check(jv.state == 'posted' and jv.move_id.message_ids, 'JV %s posted, chatter has %d messages' % (jv.name, len(jv.move_id.message_ids)))
name, move = jv.name, jv.move_id
jv.with_user(accountant).action_edit()
jv.with_user(accountant).write({'line_ids': [Command.update(jv.line_ids[0].id, {'debit': 12}), Command.update(jv.line_ids[1].id, {'credit': 12})]})
works(lambda: jv.with_user(accountant).action_post(), 'edited JV reposted in an open period')
check(jv.state == 'posted' and jv.name == name and jv.move_id == move and abs(move.amount_total - 12) < 0.01, 'same number %s, new amount' % jv.name)
works(lambda: jv.with_user(accountant).action_void(), 'accountant voids the JV (VOID/ reversal)')
b2 = works(lambda: bill(clerk, 500), 'clerk posts another supplier bill')
rc = V.with_user(clerk).create({'voucher_type': 'receipt', 'date': d, 'partner_id': customer.id, 'received_amount': 50,
                                'cash_line_ids': [Command.create({'account_id': bank.id, 'method': 'transfer', 'amount_currency': 50})],
                                'line_ids': [Command.create({'account_id': income.id, 'amount': 50, 'name': 'x'})]})
works(lambda: rc.with_user(clerk).action_post(), 'clerk posts a receipt')
works(lambda: rc.with_user(clerk).message_post(body='called the customer'), 'clerk writes a note on a posted receipt')

b3 = bill(clerk, 300)
env['account.account'].with_context(active_test=False).search([('code', '=', '211004')]).active = True  # archived in this copy


def register_payment():
    w = env['account.payment.register'].with_user(accountant).with_context(active_model='account.move', active_ids=b3.move_id.ids).create(
        {'journal_id': env['account.journal'].search([('type', '=', 'bank')], limit=1).id, 'payment_date': d})
    w._create_payments()
    return b3.move_id.payment_state


state = works(register_payment, 'accountant registers a payment with Odoo\'s own Pay button')
check(state in ('paid', 'in_payment'), 'bill paid through Odoo (%s)' % state)

print('RESULT', 'ALL PASS (%d checks)' % len(ok) if all(ok) else '%d FAILED of %d' % (ok.count(False), len(ok)))
env.cr.rollback()
