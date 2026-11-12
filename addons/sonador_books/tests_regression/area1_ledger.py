# Acceptance checklist, area 1: Ledger integrity and close (items 1.1-1.10).
# Every check acts as a real user (clerk, accountant, manager, made here); superuser only for setup. Rolled back at the end.
# Run: docker compose exec -T odoo sh -c 'odoo shell -d <copy of the books> --no-http ...' < tests_regression/area1_ledger.py
import time
import traceback
from collections import defaultdict
from datetime import date, datetime, timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.sonador_books.models.ethiopian_calendar import fiscal_periods, fiscal_year_of

assert env.cr.dbname not in ('ecogreen_test', 'ecogreen_web'), 'never on a live database'
results = defaultdict(list)
timing = {}
item = ['1.0']
REFUSALS = (AccessError, UserError, ValidationError, ValueError)


def check(cond, msg):
    print('%s %s %s' % ('PASS' if cond else 'FAIL', item[0], msg))
    results[item[0]].append(bool(cond))
    return bool(cond)


def refused(fn, label):
    """The attempt must be refused; whatever it did is undone either way, so a check that fails does not spill over."""
    sp = env.cr.savepoint()
    try:
        fn()
        env.flush_all()
    except REFUSALS as e:
        return check(True, 'refused: %s -> "%s"' % (label, str(e).splitlines()[0][:150]))
    finally:
        sp.close(rollback=True)
        env.invalidate_all()
    return check(False, 'NOT refused: %s' % label)


def refused_or_unchanged(fn, read, label):
    """A write of a control field is refused, or (a field Odoo recomputes) leaves the stored value as it was."""
    before = read()
    try:
        with env.cr.savepoint():
            fn()
            env.flush_all()
            env.invalidate_all()
            after = read()
            if after == before:
                return check(True, 'ignored (value kept): %s' % label)
            raise AssertionError('changed %r -> %r' % (before, after))
    except REFUSALS as e:
        return check(True, 'refused: %s -> "%s"' % (label, str(e).splitlines()[0][:150]))
    except AssertionError as e:
        return check(False, 'NOT refused: %s (%s)' % (label, e))


def works(fn, label):
    try:
        with env.cr.savepoint():
            res = fn()
        check(True, 'works: %s' % label)
        return res
    except Exception as e:  # noqa: BLE001
        check(False, 'broken: %s -> %s' % (label, str(e).splitlines()[0][:200]))
        traceback.print_exc()
        return None


def start(no, title):
    item[0] = no
    timing[no] = time.time()
    print('\n=== %s %s' % (no, title))


# ---------------------------------------------------------------------------------------------- setup (superuser)
company = env.company
U = env['res.users'].with_context(no_reset_password=True)
stamp = datetime.now().strftime('%H%M%S')


def make_user(role, groups):
    return U.create({'name': 'A1 %s' % role, 'login': 'a1_%s_%s@unio.test' % (role, stamp), 'email': 'a1%s%s@unio.test' % (role, stamp),
                     'company_id': company.id, 'company_ids': [Command.set([company.id])],
                     'group_ids': [Command.set([env.ref(g).id for g in groups])]})


clerk = make_user('clerk', ['sonador_books.group_sb_clerk'])
accountant = make_user('accountant', ['sonador_books.group_sb_accountant'])
manager = make_user('manager', ['sonador_books.group_sb_manager'])
store = make_user('store', ['sonador_books.group_sb_clerk'] + (['sonador_inventory.group_sb_store_user']
                                                               if env.ref('sonador_inventory.group_sb_store_user', raise_if_not_found=False) else []))
V, M, SV = env['sb.voucher'], env['account.move'], env['sb.stock.voucher'] if 'sb.stock.voucher' in env else None
A = env['account.account']


def acc(code):
    return A.search([('code', '=', code), ('company_ids', 'in', company.id)], limit=1)


bank = acc('1104-0001') or A.search([('account_type', '=', 'asset_cash'), ('company_ids', 'in', company.id)], limit=1)
income = acc('4000-0001') or A.search([('account_type', '=', 'income'), ('company_ids', 'in', company.id)], limit=1)
expense = A.search([('account_type', '=', 'expense'), ('active', '=', True), ('company_ids', 'in', company.id)], limit=1)
other_income = A.search([('account_type', '=', 'income_other'), ('active', '=', True), ('company_ids', 'in', company.id)], limit=1)
customer = env['res.partner'].create({'name': 'A1 Customer %s' % stamp, 'customer_rank': 1})
supplier = env['res.partner'].create({'name': 'A1 Supplier %s' % stamp, 'supplier_rank': 1, 'is_company': True})
today = fields.Date.context_today(env['res.users'])
lock0 = company.fiscalyear_lock_date
closed0 = company._sb_closed_through()
cur_fy = fiscal_year_of(today)
d = max(today - timedelta(days=1), (lock0 or date(2000, 1, 1)) + timedelta(days=1))  # an open day that has passed
jv_journal = env['account.journal'].search([('code', '=', 'JV'), ('company_id', '=', company.id)], limit=1)
print('setup: company %s, books closed through %s (year closed through %s), today %s, test day %s'
      % (company.name, lock0, closed0, today, d))


def journal(user, amount=10, day=None, post=True, credit_amount=None):
    v = V.with_user(user).create({'voucher_type': 'journal', 'date': day or d, 'line_ids': [
        Command.create({'account_id': expense.id, 'debit': amount}),
        Command.create({'account_id': bank.id, 'credit': amount if credit_amount is None else credit_amount})]})
    if post:
        v.with_user(user).action_post()
    return v


def receipt(user, amount=50, post=True, day=None):
    v = V.with_user(user).create({'voucher_type': 'receipt', 'date': day or d, 'partner_id': customer.id, 'received_amount': amount,
                                  'cash_line_ids': [Command.create({'account_id': bank.id, 'method': 'transfer', 'amount_currency': amount})],
                                  'line_ids': [Command.create({'account_id': other_income.id, 'amount': amount, 'name': 'A1 test'})]})
    if post:
        v.with_user(user).action_post()
    return v


def raw_move(user, post=True, debit=5, credit=5, **extra):
    m = M.with_user(user).create(dict({'move_type': 'entry', 'journal_id': jv_journal.id, 'date': d, 'line_ids': [
        Command.create({'account_id': expense.id, 'debit': debit}), Command.create({'account_id': bank.id, 'credit': credit})]}, **extra))
    if post:
        m.with_user(user).action_post()
    return m


def cash_sale(user, fs, vtype='cash_sale', amount=100):
    vals = {'voucher_type': vtype, 'date': d, 'partner_id': customer.id, 'sb_pad_number': fs,
            'line_ids': [Command.create({'name': 'A1', 'account_id': income.id, 'quantity': 1, 'price_unit': amount, 'tax_ids': []})]}
    if vtype == 'cash_sale':
        vals['cash_account_id'] = bank.id
    v = V.with_user(user).create(vals)
    v.with_user(user).action_post()
    return v


def reverse(user, moves):
    w = env['account.move.reversal'].with_user(user).with_context(active_model='account.move', active_ids=moves.ids).create(
        {'journal_id': moves[:1].journal_id.id, 'date': d, 'reason': 'test'})
    return w.refund_moves()


def last_log():
    return env['sb.lock.log'].sudo().search([('company_id', '=', company.id)], limit=1)


PL = ('income', 'income_other', 'expense', 'expense_depreciation', 'expense_direct_cost')

# ============================================================================================== 1.1
start('1.1', 'every posted entry balances; an unbalanced entry is refused through screens and API')
unbal = V.with_user(accountant).create({'voucher_type': 'journal', 'date': d, 'line_ids': [
    Command.create({'account_id': expense.id, 'debit': 100}), Command.create({'account_id': bank.id, 'credit': 90})]})
refused(lambda: unbal.with_user(accountant).action_post(), 'accountant posts an unbalanced journal voucher (100 / 90)')
refused(lambda: unbal.with_user(manager).action_post(), 'manager posts it')
refused(lambda: raw_move(accountant, debit=100, credit=90), 'accountant posts an unbalanced journal entry (ORM / RPC)')
refused(lambda: raw_move(manager, debit=100, credit=90), 'manager posts an unbalanced journal entry')
bal = raw_move(accountant, post=False)
refused(lambda: (bal.line_ids[0].with_user(accountant).write({'debit': 50}), bal.with_user(accountant).action_post()),
        'accountant unbalances a draft entry by a line write, then posts')
posted = journal(accountant)
refused(lambda: posted.move_id.line_ids[0].with_user(manager).write({'debit': 999}), 'manager unbalances a posted entry line')
env.flush_all()
env.cr.execute("""SELECT m.name, ROUND(SUM(l.balance)::numeric, 2) FROM account_move_line l JOIN account_move m ON m.id = l.move_id
                  WHERE m.state = 'posted' AND m.company_id = %s GROUP BY m.id, m.name HAVING ABS(SUM(l.balance)) > 0.005 LIMIT 5""",
               (company.id,))
bad = env.cr.fetchall()
check(not bad, 'every posted entry in the books balances (independent SQL)%s' % (': %s' % bad if bad else ''))
env.cr.execute("SELECT ROUND(SUM(balance)::numeric, 2) FROM account_move_line WHERE parent_state = 'posted' AND company_id = %s", (company.id,))
check(abs(float(env.cr.fetchone()[0] or 0)) < 0.01, 'the trial balance balances (independent SQL)')

# ============================================================================================== 1.2
start('1.2', 'posted entries, vouchers and stock documents change only through Edit, Void, Correct or Year-End Closing')
rc = receipt(clerk)
check(rc.state == 'posted', 'clerk posts a receipt %s' % rc.name)
for who, usr in (('clerk', clerk), ('accountant', accountant), ('manager', manager)):
    refused(lambda usr=usr: rc.with_user(usr).write({'date': d - timedelta(days=1)}), '%s changes the date of a posted voucher' % who)
    refused(lambda usr=usr: rc.with_user(usr).write({'partner_id': supplier.id}), '%s changes the partner of a posted voucher' % who)
    refused(lambda usr=usr: rc.line_ids.with_user(usr).write({'amount': 9999}), '%s changes a posted voucher line amount' % who)
    refused(lambda usr=usr: rc.line_ids.with_user(usr).unlink(), '%s deletes a posted voucher line' % who)
    refused(lambda usr=usr: rc.with_user(usr).unlink(), '%s deletes a posted voucher' % who)
    refused(lambda usr=usr: rc.move_id.with_user(usr).button_draft(), '%s resets the voucher\'s entry to draft' % who)
    refused(lambda usr=usr: rc.move_id.with_user(usr).button_cancel(), '%s cancels the voucher\'s entry' % who)
    refused(lambda usr=usr: rc.move_id.line_ids[0].with_user(usr).write({'account_id': expense.id}), '%s changes a posted entry line\'s account' % who)
    refused(lambda usr=usr: rc.move_id.with_user(usr).unlink(), '%s deletes a posted entry' % who)
refused(lambda: reverse(manager, rc.move_id), 'manager reverses a voucher entry outside Void (reversal wizard)')
plain = raw_move(accountant)
for who, usr in (('accountant', accountant), ('manager', manager)):
    refused(lambda usr=usr: plain.with_user(usr).button_draft(), '%s resets a posted journal entry to draft' % who)
    refused(lambda usr=usr: plain.with_user(usr).write({'date': d - timedelta(days=1)}), '%s changes a posted entry date' % who)
    refused(lambda usr=usr: plain.with_user(usr).write({'state': 'draft'}), '%s writes state=draft on a posted entry' % who)
refused(lambda: rc.with_user(clerk).action_edit(), 'clerk Edit of a posted voucher (needs accountant)')
jv = journal(accountant)
name, move = jv.name, jv.move_id
works(lambda: jv.with_user(accountant).action_edit(), 'accountant Edit opens the posted voucher')
jv.with_user(accountant).action_edit() if jv.state == 'posted' else None
jv.with_user(accountant).write({'line_ids': [Command.update(jv.line_ids[0].id, {'debit': 12}), Command.update(jv.line_ids[1].id, {'credit': 12})]})
works(lambda: jv.with_user(accountant).action_post(), 'edited voucher posted again')
check(jv.state == 'posted' and jv.name == name and jv.move_id == move and abs(move.amount_total - 12) < 0.01,
      'Edit kept the number %s and the entry, new amount %.2f' % (jv.name, move.amount_total))
refused(lambda: jv.with_user(clerk).action_void(), 'clerk Void (needs accountant)')
jv2 = journal(accountant)
refused(lambda: jv2.with_user(accountant).action_void(), 'accountant Void without a reason')
jv2.sudo().sb_void_reason = 'A1 test void'
works(lambda: jv2.with_user(accountant).action_void(), 'accountant Void with a reason')
jv2.sudo().sb_void_reason = 'A1 test void'
jv2.with_user(accountant).action_void() if jv2.state == 'posted' else None
check(jv2.state == 'void' and jv2.move_id.reversal_move_ids, 'voided voucher %s: entry reversed (%s)' % (jv2.name, jv2.move_id.reversal_move_ids[:1].name))
if SV is not None:
    prod = env['product.product'].search([('is_storable', '=', True), ('active', '=', True)], limit=1)
    grn = SV.with_user(accountant).create({'voucher_type': 'grn', 'date': d, 'partner_id': supplier.id,
                                           'line_ids': [Command.create({'product_id': prod.id, 'quantity': 10, 'unit_cost': 20})]})
    works(lambda: grn.with_user(accountant).action_post(), 'accountant posts a GRN (finance posts store documents)')
    grn.with_user(accountant).action_post() if grn.state != 'posted' else None
    if grn.state == 'posted':
        for who, usr in (('accountant', accountant), ('manager', manager)):
            refused(lambda usr=usr: grn.line_ids.with_user(usr).write({'quantity': 99}), '%s changes a posted GRN line' % who)
            refused(lambda usr=usr: grn.with_user(usr).write({'date': d - timedelta(days=1)}), '%s changes a posted GRN date' % who)
            refused(lambda usr=usr: grn.move_id.with_user(usr).button_draft(), '%s resets the GRN entry to draft' % who)
        refused(lambda: reverse(accountant, grn.move_id), 'reversal wizard on a GRN entry')
        works(lambda: grn.with_user(accountant).action_correct(), 'accountant Correct on the posted GRN')
        grn.with_user(accountant).action_correct() if grn.state == 'posted' else None
        grn.with_user(accountant).line_ids.write({'unit_cost': 21})
        works(lambda: grn.with_user(accountant).action_post(), 'corrected GRN posted again')
        grn.with_user(accountant).action_post() if grn.state != 'posted' else None
        check(grn.state == 'posted' and abs(grn.move_id.amount_total - 210) < 0.01, 'GRN %s rewritten at 210.00 by Correct (%.2f)'
              % (grn.name, grn.move_id.amount_total))
    else:
        check(False, 'GRN could not be posted for the stock checks')

# ============================================================================================== 1.3
start('1.3', 'no client call writes control or settlement fields')
inv = V.with_user(clerk).create({'voucher_type': 'sale', 'date': d, 'partner_id': customer.id, 'sb_pad_number': 'CSI 0%s' % stamp[-6:],
                                 'line_ids': [Command.create({'name': 'A1', 'account_id': income.id, 'quantity': 1, 'price_unit': 300, 'tax_ids': []})]})
try:
    inv.with_user(clerk).action_post()
except Exception as e:  # noqa: BLE001
    print('   (credit invoice on a CSI pad not accepted: %s)' % str(e)[:120])
    inv = cash_sale(clerk, 'FS0099%s' % stamp[-4:])
im = inv.move_id
recv = im.line_ids.filtered(lambda l: l.account_id.account_type in ('asset_receivable', 'asset_cash'))[:1] or im.line_ids[:1]
for who, usr in (('clerk', clerk), ('manager', manager)):
    for field, value in (('state', 'draft'), ('payment_state', 'paid'), ('amount_residual', 0.0), ('amount_total', 1.0),
                         ('sequence_number', 999999), ('sequence_prefix', 'X/'), ('name', 'A1-FORGED'),
                         ('inalterable_hash', 'forged'), ('secure_sequence_number', 999), ('posted_before', False),
                         ('sb_closing', True), ('reversed_entry_id', plain.id)):
        if field not in im._fields:
            continue
        refused_or_unchanged(lambda usr=usr, field=field, value=value: im.with_user(usr).write({field: value}),
                             lambda field=field: im.sudo()[field], '%s writes %s on a posted entry' % (who, field))
    for field, value in (('amount_residual', 0.0), ('reconciled', True), ('matching_number', 'A1'), ('balance', 1.0),
                         ('debit', 1.0), ('credit', 1.0), ('full_reconcile_id', False), ('parent_state', 'draft'),
                         ('date', d - timedelta(days=3))):
        if field not in recv._fields:
            continue
        refused_or_unchanged(lambda usr=usr, field=field, value=value: recv.with_user(usr).write({field: value}),
                             lambda field=field: recv.sudo()[field], '%s writes %s on a posted entry line' % (who, field))
    for field, value in (('state', 'void'), ('state', 'draft'), ('move_id', plain.id), ('name', 'A1-FORGED'), ('sb_editing', True)):
        refused_or_unchanged(lambda usr=usr, field=field, value=value: inv.with_user(usr).write({field: value}),
                             lambda field=field: inv.sudo()[field], '%s writes %s=%s on a posted voucher' % (who, field, value))
refused(lambda: V.with_user(clerk).create({'voucher_type': 'journal', 'date': d, 'state': 'posted'}), 'clerk creates a voucher already posted')
refused(lambda: V.with_user(clerk).with_context(default_state='posted').create({'voucher_type': 'journal', 'date': d}),
        'clerk creates a voucher with default_state=posted in the context')
refused(lambda: raw_move(clerk, post=False, state='posted'), 'clerk creates a journal entry already posted')
refused(lambda: raw_move(manager, post=False, sb_closing=True), 'manager creates an entry flagged as year-end closing')
if recv.account_id.reconcile or recv.account_id.account_type in ('asset_receivable', 'liability_payable'):
    refused(lambda: env['account.partial.reconcile'].with_user(manager).create(
        {'debit_move_id': recv.id, 'credit_move_id': recv.id, 'amount': 1, 'debit_amount_currency': 1, 'credit_amount_currency': 1}),
        'manager creates a partial reconcile by hand')
rec_line = env['account.move.line'].search([('parent_state', '=', 'posted'), ('reconciled', '=', True), ('company_id', '=', company.id),
                                            ('date', '>', lock0 or date(2000, 1, 1))], limit=1)
if rec_line:
    refused(lambda: rec_line.with_user(clerk).remove_move_reconcile(), 'clerk unreconciles a posted line (%s)' % rec_line.move_id.name)
    refused(lambda: (rec_line.matched_debit_ids | rec_line.matched_credit_ids).with_user(clerk).unlink(), 'clerk deletes a reconciliation link')
    sp = env.cr.savepoint()
    try:
        before_n = len(rec_line.move_id.sudo().message_ids)
        (rec_line.matched_debit_ids | rec_line.matched_credit_ids).with_user(manager).unlink()
        check(len(rec_line.move_id.sudo().message_ids) > before_n and 'Unreconciled' in (rec_line.move_id.sudo().message_ids[:1].body or ''),
              'manager unreconciles (allowed for accountant and up): written in the entry\'s history')
    finally:
        sp.close(rollback=True)
        env.invalidate_all()

# ============================================================================================== 1.4
start('1.4', 'numbers unique per company, gap-free per series, voids never reused, one fiscal receipt space')
r1, r2, r3 = receipt(clerk), receipt(clerk), receipt(clerk)
S = env['sb.doc.series']
ser = r1.sb_series_id if 'sb_series_id' in r1._fields else S
n1, n2, n3 = (ser._parse(r.name) if ser else 0 for r in (r1, r2, r3))
check(ser and (n2 - n1, n3 - n2) == (1, 1), 'three receipts numbered one after another: %s, %s, %s' % (r1.name, r2.name, r3.name))
r2.sudo().sb_void_reason = 'A1 numbering test'
r2.with_user(accountant).action_void()
r4 = receipt(clerk)
check(r2.state == 'void' and r2.name and ser._parse(r4.name) == n3 + 1,
      'voided %s keeps its number; next receipt is %s (not reused)' % (r2.name, r4.name))
check(not r2.move_id.reversal_move_ids or not ser._parse(r2.move_id.reversal_move_ids[:1].name or '') == n3 + 2,
      'the void reversal takes no number of the series (%s)' % r2.move_id.reversal_move_ids[:1].name)
if ser:
    refused(lambda: receipt(clerk).with_user(clerk).write({}) if False else V.with_user(clerk).create(
        {'voucher_type': 'receipt', 'date': d, 'partner_id': customer.id, 'received_amount': 5, 'sb_pad_number': r1.name,
         'cash_line_ids': [Command.create({'account_id': bank.id, 'method': 'transfer', 'amount_currency': 5})],
         'line_ids': [Command.create({'account_id': other_income.id, 'amount': 5, 'name': 'x'})]}).with_user(clerk).action_post(),
        'a receipt typed with the number of %s (already used)' % r1.name)
    refused(lambda: V.with_user(clerk).create(
        {'voucher_type': 'receipt', 'date': d, 'partner_id': customer.id, 'received_amount': 5, 'sb_pad_number': ser._format(n3 + 9, d),
         'cash_line_ids': [Command.create({'account_id': bank.id, 'method': 'transfer', 'amount_currency': 5})],
         'line_ids': [Command.create({'account_id': other_income.id, 'amount': 5, 'name': 'x'})]}).with_user(clerk).action_post(),
        'a receipt jumping ahead (%s) leaves a gap' % ser._format(n3 + 9, d))
fs = 'FS0098%s' % stamp[-4:]
works(lambda: cash_sale(clerk, fs), 'cash sale on fiscal receipt %s' % fs)
cs = cash_sale(clerk, fs) if not V.search([('name', '=', fs)]) else V.search([('name', '=', fs)], limit=1)
digits = fs[2:]
for typed in (fs, 'FS-' + digits, 'FS/' + digits, 'F.S. ' + digits, 'FS No. ' + digits, 'fs no.' + digits.lstrip('0'), digits):
    refused(lambda typed=typed: cash_sale(clerk, typed, vtype='sale'), 'credit invoice typed "%s" (the same fiscal receipt)' % typed)
cs.sudo().sb_void_reason = 'A1'
cs.with_user(accountant).action_void()
refused(lambda: cash_sale(clerk, fs), 'the voided fiscal receipt %s issued again' % fs)
env.flush_all()
env.cr.execute("""SELECT name, COUNT(*) FROM account_move WHERE company_id = %s AND state = 'posted' AND name NOT IN ('/', '')
                  GROUP BY journal_id, name HAVING COUNT(*) > 1 LIMIT 5""", (company.id,))
dups = env.cr.fetchall()
check(not dups, 'no entry number twice in a journal (independent SQL)%s' % (': %s' % dups if dups else ''))
env.cr.execute("""SELECT name, COUNT(*) FROM sb_voucher WHERE company_id = %s AND state IN ('posted', 'void') AND name NOT IN ('/', '')
                  AND create_date > now() - interval '1 hour' GROUP BY name HAVING COUNT(*) > 1""", (company.id,))
check(not env.cr.fetchall(), 'no voucher number given twice in this run')
env.cr.execute("""SELECT name, COUNT(*) FROM sb_voucher WHERE company_id = %s AND state IN ('posted', 'void') AND name NOT IN ('/', '')
                  GROUP BY name HAVING COUNT(*) > 1 LIMIT 10""", (company.id,))
legacy = env.cr.fetchall()
print('   INFO voucher numbers held twice in the books brought over from the earlier systems: %s' % (legacy or 'none'))

# ============================================================================================== 1.5
start('1.5', 'creating or changing a number series needs a manager and is logged')
used_ser = ser
refused(lambda: S.with_user(accountant).create({'name': 'A1 pad', 'doc_type': 'receipt', 'prefix': 'A1R/', 'padding': 4}),
        'accountant adds a series')
refused(lambda: S.with_user(clerk).create({'name': 'A1 pad', 'doc_type': 'receipt', 'prefix': 'A1R/', 'padding': 4}), 'clerk adds a series')
log_before = env['sb.lock.log'].sudo().search_count([('event', '=', 'series')])
new_ser = works(lambda: S.with_user(manager).create({'name': 'A1 pad %s' % stamp, 'doc_type': 'receipt', 'prefix': 'A1R%s/' % stamp[-3:],
                                                      'padding': 4}), 'manager adds a series')
check(env['sb.lock.log'].sudo().search_count([('event', '=', 'series')]) > log_before and 'A1 pad' in (last_log().note or ''),
      'new series written to the period log: "%s"' % (last_log().note or '')[:90])
if used_ser:
    refused(lambda: used_ser.with_user(accountant).write({'prefix': 'ZZ/'}), 'accountant changes the prefix of %s (in use)' % used_ser.name)
    refused(lambda: used_ser.with_user(accountant).write({'padding': (used_ser.padding or 4) + 1}), 'accountant changes its padding')
    refused(lambda: used_ser.with_user(manager).write({'prefix': new_ser.prefix}), 'manager gives it the numbers of another series')
    old_pad = used_ser.padding
    works(lambda: used_ser.with_user(manager).write({'padding': (old_pad or 4) + 1}), 'manager changes the padding of %s' % used_ser.name)
    check('->' in (last_log().note or '') and last_log().event == 'series' and last_log().user_id == manager,
          'change logged as was -> now by the manager: "%s"' % (last_log().note or '')[:100])
    refused(lambda: new_ser.with_user(accountant).write({'is_default': True}), 'accountant makes a series the default')
    refused(lambda: new_ser.with_user(accountant).write({'active': False}), 'accountant archives a series')

# ============================================================================================== 1.6
start('1.6', 'a period closes only when its checklist is clear; closed periods refuse posting')
pw = env['sb.period.wizard'].with_user(manager).create({})
last_end = pw._sb_last_ended()
period_start = [s for y in (cur_fy - 1, cur_fy) for _n, _m, s, e in fiscal_periods(y) if e == last_end][0]
print('   last ended period %s .. %s; books closed through %s' % (period_start, last_end, lock0))
refused(lambda: env['sb.period.wizard'].with_user(accountant).create({}).line_ids.filtered(lambda l: l.date_to == last_end).action_close_through(),
        'accountant closes a period (needs a manager)')
refused(lambda: (pw.write({'close_to': last_end}), env['sb.period.wizard'].with_user(clerk).browse(pw.id).action_close_confirm()),
        'clerk confirms a close')
def clear_the_period():
    """setup: the copy of the books has old drafts in the open periods; set them aside (rolled back with everything else)."""
    span = [('company_id', '=', company.id), ('date', '<=', last_end)] + ([('date', '>', lock0)] if lock0 else [])
    env['sb.voucher'].sudo().search(span + [('state', '=', 'draft')]).with_context(sb_test_setup=True).write({'date': last_end + timedelta(days=1)})
    env['account.move'].sudo().search(span + [('state', '=', 'draft')]).write({'date': last_end + timedelta(days=1)})
    if SV is not None:
        SV.sudo().search(span + [('state', '=', 'draft')]).write({'date': last_end + timedelta(days=1)})


sp = env.cr.savepoint()
try:
    clear_the_period()
    env.flush_all()
    # with the old drafts set aside, each of these alone keeps the period from closing
    probe = journal(accountant, day=last_end, post=False)  # a draft dated in the period
    refused(lambda: (pw.write({'close_to': last_end}), pw.action_close_confirm()), 'manager closes through %s with a draft voucher in it' % last_end)
    probe.sudo().unlink()
    ed = journal(accountant, day=last_end)
    ed.with_user(accountant).action_edit()
    refused(lambda: (pw.write({'close_to': last_end}), pw.action_close_confirm()), 'manager closes through %s with a voucher being edited' % last_end)
    ed.with_user(accountant).action_discard_edit()
    if SV is not None:
        corr = SV.with_user(accountant).create({'voucher_type': 'grn', 'date': last_end, 'partner_id': supplier.id,
                                                'line_ids': [Command.create({'product_id': prod.id, 'quantity': 1, 'unit_cost': 5})]})
        corr.with_user(accountant).action_post()
        corr.with_user(accountant).action_correct()
        refused(lambda: (pw.write({'close_to': last_end}), pw.action_close_confirm()), 'manager closes through %s with a GRN in correction' % last_end)
        corr.with_user(accountant).action_post()
    env.flush_all()
    checks = pw._sb_close_checks(last_end)
    drafts_ok = checks[0][0] == 'ok'
    print('   checklist after setting the old drafts aside:', [(c[0], str(c[1])[:40]) for c in checks])
    check(drafts_ok, 'checklist shows no drafts in the period')
    works(lambda: (pw.write({'close_to': last_end}), pw.action_close_confirm()), 'manager closes through %s with the checklist clear of drafts' % last_end)
    company.invalidate_recordset()
    check(company.fiscalyear_lock_date == last_end, 'books closed through %s' % company.fiscalyear_lock_date)
    log = last_log()
    check(log.event == 'lock' and log.new_date == last_end and log.user_id == manager, 'close logged: %s %s by %s, "%s"'
          % (log.event, log.new_date, log.user_id.name, log.note))
    refused(lambda: journal(accountant, day=last_end), 'accountant posts a voucher dated in the closed period')
    refused(lambda: journal(manager, day=last_end), 'manager posts a voucher dated in the closed period')
    refused(lambda: raw_move(accountant, date=last_end), 'accountant posts a journal entry in the closed period')
    refused(lambda: receipt(clerk, day=last_end), 'clerk posts a receipt in the closed period')
    old = V.search([('state', '=', 'posted'), ('date', '<=', last_end), ('date', '>', lock0 or date(2000, 1, 1)),
                    ('voucher_type', '=', 'journal')], limit=1)
    if old:
        refused(lambda: old.with_user(accountant).action_edit(), 'accountant Edit of %s in the closed period' % old.name)
        old.sudo().sb_void_reason = 'A1'
        res = None
        try:
            with env.cr.savepoint():
                old.with_user(accountant).action_void()
                res = old.move_id.reversal_move_ids[:1].date
        except REFUSALS as e:
            res = 'refused: %s' % str(e)[:80]
        check(res is not None and (isinstance(res, str) or res > last_end),
              'Void of %s in the closed period is refused or dated in the first open day (%s)' % (old.name, res))
finally:
    sp.close(rollback=True)
company.invalidate_recordset()
env.invalidate_all()

# ============================================================================================== 1.7
start('1.7', 'no lock or close into the future; reopening and lock exceptions need a manager and a reason, and are logged')
for who, usr in (('accountant', accountant), ('manager', manager)):
    refused(lambda usr=usr: company.with_user(usr).write({'fiscalyear_lock_date': today}), '%s locks the books through today' % who)
    refused(lambda usr=usr: company.with_user(usr).write({'fiscalyear_lock_date': today + timedelta(days=30)}), '%s locks the books into the future' % who)
    refused(lambda usr=usr: company.with_user(usr).write({'hard_lock_date': today + timedelta(days=1)}), '%s sets a future hard lock' % who)
pw = env['sb.period.wizard'].with_user(manager).create({})
refused(lambda: (pw.write({'close_to': today + timedelta(days=60)}), pw.action_close_confirm()), 'manager closes through a date 60 days ahead (RPC)')
refused(lambda: pw.line_ids.filtered(lambda l: l.date_to >= today)[:1].action_close_through(), 'Close through on the current period')
YE = env['sb.year.end.wizard']
refused(lambda: YE.with_user(manager).create({'year': str(cur_fy), 'retained_account_id': company.retained_earnings_account_id.id
                                               if 'retained_earnings_account_id' in company._fields else False}).action_close_year(),
        'manager closes the current fiscal year (has not ended)')
# reopen: needs a manager and a reason, logged. Close a past period first (setup), then reopen it.
sp = env.cr.savepoint()
try:
    clear_the_period()
    company.sudo().with_context(sb_lock_note='A1 setup').fiscalyear_lock_date = last_end
    pw = env['sb.period.wizard'].with_user(manager).create({})
    line = pw.line_ids.filtered(lambda l: l.date_from == period_start)[:1]
    refused(lambda: (pw.write({'reopen_from': period_start, 'reopen_reason': ''}), pw.action_reopen_confirm()), 'manager reopens without a reason')
    refused(lambda: (pw.write({'reopen_from': period_start, 'reopen_reason': 'x'}),
                     env['sb.period.wizard'].with_user(accountant).browse(pw.id).action_reopen_confirm()), 'accountant reopens a period')
    works(lambda: (pw.write({'reopen_from': period_start, 'reopen_reason': 'A1 correction of a supplier bill'}), pw.action_reopen_confirm()),
          'manager reopens %s with a reason' % period_start)
    log = last_log()
    check(log.event == 'unlock' and 'A1 correction' in (log.note or '') and log.user_id == manager,
          'reopen logged: %s by %s "%s"' % (log.event, log.user_id.name, log.note))
finally:
    sp.close(rollback=True)
company.invalidate_recordset()
LE = env['account.lock_exception']
exc_vals = {'company_id': company.id, 'lock_date_field': 'fiscalyear_lock_date', 'lock_date': lock0 - timedelta(days=30) if lock0 else today,
            'reason': 'A1 late supplier bill', 'end_datetime': fields.Datetime.now() + timedelta(days=2)}
refused(lambda: LE.with_user(accountant).create(dict(exc_vals)), 'accountant grants a lock date exception')
refused(lambda: LE.with_user(manager).create(dict(exc_vals, reason='')), 'manager grants an exception without a reason')
refused(lambda: LE.with_user(manager).create(dict(exc_vals, end_datetime=fields.Datetime.now() + timedelta(days=30))),
        'manager grants an exception for 30 days (more than 7)')
if closed0 and lock0 and lock0 <= closed0:
    refused(lambda: LE.with_user(manager).create(dict(exc_vals)), 'manager grants an exception into the closed year (reopen the year instead)')
sp = env.cr.savepoint()
try:
    company.sudo().with_context(sb_lock_note='A1 setup').fiscalyear_lock_date = last_end
    ex = works(lambda: LE.with_user(manager).create(dict(exc_vals, lock_date=last_end - timedelta(days=5))), 'manager grants a 2-day exception with a reason')
    log = last_log()
    check(log.event == 'exception' and 'A1 late supplier bill' in (log.note or ''), 'exception logged: "%s"' % (log.note or '')[:100])
    refused(lambda: ex.with_user(manager).write({'lock_date': last_end - timedelta(days=20)}), 'manager widens the exception after granting it')
finally:
    sp.close(rollback=True)
company.invalidate_recordset()

# ============================================================================================== 1.8
start('1.8', 'Year-End Closing posts to a retained-earnings account only and rolls the balance forward')
target_fy = fiscal_year_of(closed0 + timedelta(days=1)) if closed0 else cur_fy - 1  # the first year not closed yet
ye_start, ye_end = fiscal_periods(target_fy)[0][2], fiscal_periods(target_fy)[-1][3]
retained = company.retained_earnings_account_id if 'retained_earnings_account_id' in company._fields else A
if not retained:
    retained = A.search([('account_type', '=', 'equity_unaffected'), ('company_ids', 'in', company.id)], limit=1)
capital = A.search([('account_type', '=', 'equity'), ('company_ids', 'in', company.id), ('id', '!=', retained.id)], limit=1)
print('   year to close: %s (%s .. %s), retained earnings %s' % (target_fy, ye_start, ye_end, retained.display_name))
if ye_end >= today:
    check(True, 'no ended year is open to close (the last closed year ends %s)' % closed0)
else:
    refused(lambda: YE.with_user(accountant).create({'year': str(target_fy), 'retained_account_id': retained.id}).action_close_year(),
            'accountant closes the year (needs a manager)')
    if capital:
        refused(lambda: YE.with_user(manager).create({'year': str(target_fy), 'retained_account_id': capital.id}).action_close_year(),
                'manager closes the year to %s (capital, not retained earnings)' % capital.display_name)
    sp = env.cr.savepoint()
    try:
        span = [('company_id', '=', company.id), ('date', '>=', ye_start), ('date', '<=', ye_end), ('state', '=', 'draft')]
        V.sudo().search(span).write({'date': ye_end + timedelta(days=1)}) if False else None
        blockers = YE.with_user(manager).create({'year': str(target_fy), 'retained_account_id': retained.id})._sb_blocking(ye_start, ye_end)
        if blockers:
            print('   setup: setting aside what blocks the close: %s' % '; '.join(blockers)[:200])
            V.sudo().search(span).unlink()
            M.sudo().search(span).unlink()
            if SV is not None:
                SV.sudo().search(span).write({'date': ye_end + timedelta(days=1)})
        env.flush_all()

        def bs_balances(on):
            env.flush_all()
            env.cr.execute("""SELECT a.id, ROUND(SUM(l.balance)::numeric, 2) FROM account_move_line l JOIN account_account a ON a.id = l.account_id
                              WHERE l.parent_state = 'posted' AND l.company_id = %s AND l.date <= %s AND a.account_type NOT IN %s
                              GROUP BY a.id""", (company.id, on, PL))
            return dict(env.cr.fetchall())

        before = bs_balances(ye_end)
        w = YE.with_user(manager).create({'year': str(target_fy), 'retained_account_id': retained.id})
        profit = w.profit
        works(lambda: w.action_close_year(), 'manager closes %s' % w.fy_label)
        w = YE.with_user(manager).create({'year': str(target_fy), 'retained_account_id': retained.id})
        w.action_close_year() if w.status == 'open' else None
        cv = w.closing_voucher_id
        check(bool(cv) and cv.move_id.sb_closing, 'closing voucher %s posted and marked as closing' % cv.name)
        other = cv.move_id.line_ids.filtered(lambda l: l.account_id != retained and l.account_id.account_type not in PL)
        check(not other, 'the closing entry touches no balance-sheet account but retained earnings%s' % (
            ': %s' % other.account_id.mapped('code') if other else ''))
        ret_line = cv.move_id.line_ids.filtered(lambda l: l.account_id == retained)
        check(abs(-sum(ret_line.mapped('balance')) - profit) < 0.01, 'retained earnings takes the year\'s result %.2f' % profit)
        env.flush_all()
        env.cr.execute("""SELECT ROUND(SUM(l.balance)::numeric, 2) FROM account_move_line l JOIN account_account a ON a.id = l.account_id
                          WHERE l.parent_state = 'posted' AND l.company_id = %s AND l.date BETWEEN %s AND %s
                          AND a.account_type IN %s""", (company.id, ye_start, ye_end, PL))
        check(abs(float(env.cr.fetchone()[0] or 0)) < 0.01, 'income and expense accounts at nil for %s after the close' % w.fy_label)
        after = bs_balances(ye_end)
        moved = {k: after.get(k, 0) - before.get(k, 0) for k in set(after) | set(before) if abs(float(after.get(k, 0) - before.get(k, 0))) > 0.005}
        check(set(moved) <= {retained.id}, 'only retained earnings moves on the balance sheet (%s)' % len(moved))
        nxt = bs_balances(ye_end + timedelta(days=1))
        check(all(abs(float(nxt.get(k, 0)) - float(after.get(k, 0))) < 0.005 or k for k in after)
              and abs(float(nxt.get(retained.id, 0)) - float(after.get(retained.id, 0))) < 0.01 + abs(float(sum(
                  l.balance for l in env['account.move.line'].search([('date', '=', ye_end + timedelta(days=1)), ('account_id', '=', retained.id),
                                                                      ('parent_state', '=', 'posted')])))),
              'the balance rolls into the next year (retained earnings opening = closing)')
        check(company.fiscalyear_lock_date == ye_end and last_log().event == 'close_year', 'books locked through %s, logged' % ye_end)
        refused(lambda: reverse(manager, cv.move_id), 'reversal wizard on the closing entry')
        refused(lambda: cv.with_user(manager).action_void(), 'manager Void of the closing voucher (only the wizard reopens a year)')
        refused(lambda: YE.with_user(manager).create({'year': str(target_fy), 'retained_account_id': retained.id}).action_reopen_year(),
                'manager reopens the year without a reason')
        works(lambda: YE.with_user(manager).create({'year': str(target_fy), 'retained_account_id': retained.id,
                                                    'reopen_reason': 'A1 audit adjustment'}).action_reopen_year(), 'manager reopens it with a reason')
    finally:
        sp.close(rollback=True)
company.invalidate_recordset()

# ============================================================================================== 1.9
start('1.9', 'chatter, attachments and the period log on posted documents cannot be forged, edited or deleted')
admin_partner = env.ref('base.user_admin').partner_id
target = rc.move_id
msg = env['mail.message'].with_user(clerk).create({'model': 'account.move', 'res_id': target.id, 'body': 'approved by the CEO',
                                                   'author_id': admin_partner.id, 'date': '2024-01-01 08:00:00', 'message_type': 'comment'})
check(msg.author_id == clerk.partner_id and msg.date > datetime(2026, 1, 1), 'forged message: author kept as %s, date %s' % (msg.author_id.name, msg.date))
m2 = target.with_user(accountant).message_post(body='note', author_id=admin_partner.id, date='2024-01-01 08:00:00',
                                               message_type='comment', subtype_xmlid='mail.mt_note')
check(m2.author_id == accountant.partner_id and m2.date > datetime(2026, 1, 1), 'message_post with another author and an old date: %s, %s'
      % (m2.author_id.name, m2.date))
refused(lambda: env['mail.message'].with_user(clerk).create({'model': 'res.partner', 'res_id': customer.id, 'body': 'x'})
        .with_user(clerk).write({'model': 'account.move', 'res_id': target.id}), 'clerk moves a message onto a posted entry')
env.flush_all()
env.cr.precommit.run()
old = target.sudo().message_ids.filtered(lambda m: m.author_id != clerk.partner_id)[:1] or target.sudo().message_ids[-1:]
for who, usr in (('clerk', clerk), ('manager', manager)):
    refused(lambda usr=usr: old.with_user(usr).write({'body': 'rewritten'}), '%s edits a message on a posted entry' % who)
    refused(lambda usr=usr: old.with_user(usr).unlink(), '%s deletes a message on a posted entry' % who)
    refused(lambda usr=usr: msg.with_user(usr).unlink(), '%s deletes their own message on a posted entry' % who)
    refused(lambda usr=usr: msg.with_user(usr).write({'date': '2024-01-01 08:00:00'}), '%s back-dates a message' % who)
trk = rc.sudo().message_ids.tracking_value_ids[:1] or target.sudo().message_ids.tracking_value_ids[:1]
if trk:
    refused(lambda: trk.with_user(manager).write({'new_value_char': 'forged'}), 'manager rewrites a tracking value')
    refused(lambda: trk.with_user(manager).unlink(), 'manager deletes a tracking value')
att = env['ir.attachment'].with_user(clerk).create({'name': 'bill.pdf', 'raw': b'%PDF test', 'res_model': 'account.move', 'res_id': target.id})
for who, usr in (('clerk', clerk), ('manager', manager)):
    refused(lambda usr=usr: att.with_user(usr).unlink(), '%s deletes an attachment of a posted entry' % who)
    refused(lambda usr=usr: att.with_user(usr).write({'raw': b'other'}), '%s replaces its content' % who)
    refused(lambda usr=usr: att.with_user(usr).write({'res_id': plain.id}), '%s moves it to another entry' % who)
draft_v = V.with_user(clerk).create({'voucher_type': 'receipt', 'date': d, 'partner_id': customer.id})
datt = env['ir.attachment'].with_user(clerk).create({'name': 'draft.pdf', 'raw': b'x', 'res_model': 'sb.voucher', 'res_id': draft_v.id})
works(lambda: datt.with_user(clerk).unlink(), 'clerk deletes an attachment of a draft voucher (allowed)')
works(lambda: rc.with_user(clerk).message_post(body='called the customer'), 'clerk adds a note on a posted voucher (allowed)')
log = env['sb.lock.log'].sudo().search([], limit=1) or env['sb.lock.log']._log(company, 'lock', note='A1 setup')
for who, usr in (('clerk', clerk), ('manager', manager)):
    refused(lambda usr=usr: log.with_user(usr).write({'note': 'rewritten'}), '%s edits the period log' % who)
    refused(lambda usr=usr: log.with_user(usr).unlink(), '%s deletes the period log' % who)
    refused(lambda usr=usr: env['sb.lock.log'].with_user(usr).create({'company_id': company.id, 'event': 'lock', 'note': 'forged'}),
            '%s writes a period log line by hand' % who)

# ============================================================================================== 1.10
start('1.10', 'merging customers or vendors with posted items needs a manager, never touches closed periods, is logged')
Merge = env['base.partner.merge.automatic.wizard']


def merge(user, partners, dst):
    w = Merge.with_user(user).create({'partner_ids': [Command.set(partners.ids)], 'dst_partner_id': dst.id})
    w.action_merge()


p1 = env['res.partner'].create({'name': 'A1 Merge One %s' % stamp, 'customer_rank': 1})
p2 = env['res.partner'].create({'name': 'A1 Merge Two %s' % stamp, 'customer_rank': 1})
customer_saved = customer
customer = p1
receipt(clerk)
customer = p2
receipt(clerk)
customer = customer_saved
# users who may merge contacts at all (Odoo's contact creation right), so the refusal tested is the books' own rule
clerk_c = make_user('clerkc', ['sonador_books.group_sb_clerk', 'base.group_partner_manager'])
acc_c = make_user('accc', ['sonador_books.group_sb_accountant', 'base.group_partner_manager'])
for who, usr in (('clerk (may edit contacts)', clerk_c), ('accountant (may edit contacts)', acc_c)):
    refused(lambda usr=usr: merge(usr, p1 | p2, p1), '%s merges two customers with posted items' % who)
p3 = env['res.partner'].create({'name': 'A1 Merge Three %s' % stamp, 'customer_rank': 1})
p4 = env['res.partner'].create({'name': 'A1 Merge Four %s' % stamp, 'customer_rank': 1})
works(lambda: merge(clerk_c, p3 | p4, p3), 'clerk merges two contacts with nothing posted (allowed)')
oldp = env['account.move.line'].search([('parent_state', '=', 'posted'), ('partner_id', '!=', False),
                                        ('date', '<=', lock0 or date(2000, 1, 1)), ('partner_id.customer_rank', '>', 0)], limit=1).partner_id
if oldp:
    refused(lambda: merge(manager, oldp | p1, p1), 'manager merges %s (items in the closed year) into another customer' % oldp.display_name)
else:
    print('   (no customer with items in the closed year to test)')
log_before = env['sb.lock.log'].sudo().search_count([('event', '=', 'partner_merge')])
works(lambda: merge(manager, p1 | p2, p1), 'manager merges two customers whose items are all in open periods')
merge(manager, p1 | p2, p1) if p2.exists() else None
check(not p2.exists() and env['sb.lock.log'].sudo().search_count([('event', '=', 'partner_merge')]) > log_before,
      'merge done and written to the period log: "%s"' % (env['sb.lock.log'].sudo().search([('event', '=', 'partner_merge')], limit=1).note or '')[:100])
check(any('merged' in (m.body or '') for m in p1.sudo().message_ids[:3]), 'merge noted in the kept customer\'s history')

# ---------------------------------------------------------------------------------------------- summary
print('\n=== SUMMARY area 1: Ledger integrity and close')
end = time.time()
nos = sorted(results, key=lambda k: [int(x) for x in k.split('.')])
for i, no in enumerate(nos):
    t = (timing.get(nos[i + 1]) if i + 1 < len(nos) else end) - timing.get(no, end)
    r = results[no]
    print('ITEM %s %s (%d/%d checks) %.1fs' % (no, 'PASS' if all(r) else 'FAIL', r.count(True), len(r), t))
print('AREA 1 %s: %d/%d items' % ('PASS' if all(all(r) for r in results.values()) else 'FAIL',
                                  sum(1 for r in results.values() if all(r)), len(results)))
env.cr.rollback()
