# GL controls (auditor round 2, items A-H): attacks as each role must be refused, legitimate flows must work.
# Run: ~/ecogreen-odoo19/sync/local_shell.sh < tests_manual/gl_controls.py   (database ecogreen; rolled back at the end)
import traceback
from datetime import date, timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.addons.sonador_books.models.series import fy_label

assert env.cr.dbname == 'ecogreen'
ok = []


def check(cond, msg):
    print(('PASS ' if cond else 'FAIL ') + msg)
    ok.append(bool(cond))


def refused(fn, label):
    try:
        with env.cr.savepoint():
            fn()
    except (AccessError, UserError, ValidationError) as e:
        print('PASS refused: %s  -> "%s"' % (label, str(e).splitlines()[0][:170]))
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
for role in ('auditor', 'clerk', 'accountant', 'manager'):
    u[role] = U.create({'name': 'GL test %s' % role, 'login': 'gltest_%s@unio.local' % role, 'company_id': company.id,
                        'company_ids': [Command.set([company.id])],
                        'group_ids': [Command.set([env.ref('sonador_books.group_sb_%s' % role).id])]})
clerk, accountant, manager = u['clerk'], u['accountant'], u['manager']
V = env['sb.voucher']
M = env['account.move']
bank, income = acc('1104-0001'), acc('4000-0001')
expense = env['account.account'].search([('account_type', '=', 'expense'), ('active', '=', True)], limit=1)
equity = acc('3000-0003')
customer = env['res.partner'].create({'name': 'GL Test Customer', 'customer_rank': 1})
supplier = env['res.partner'].create({'name': 'GL Test Supplier', 'supplier_rank': 1})
d = date(2026, 10, 10)
jv_journal = env['account.journal'].search([('code', '=', 'JV')], limit=1)
closing = M.search([('sb_closing', '=', True), ('state', '=', 'posted')], limit=1)
closed_before = company._sb_closed_through()
print('closing entry', closing.name, 'closed through', closed_before)


def cash_sale(user, fs=None, vtype='cash_sale', amount=100):
    vals = {'voucher_type': vtype, 'date': d, 'partner_id': customer.id,
            'line_ids': [Command.create({'name': 'Test', 'account_id': income.id, 'quantity': 1, 'price_unit': amount})]}
    if vtype == 'cash_sale':
        vals['cash_account_id'] = bank.id
    if fs:
        vals['sb_pad_number'] = fs
    v = V.with_user(user).create(vals)
    v.with_user(user).action_post()
    return v


def journal(user, amount=10, post=True):
    v = V.with_user(user).create({'voucher_type': 'journal', 'date': d, 'line_ids': [
        Command.create({'account_id': expense.id, 'debit': amount}), Command.create({'account_id': bank.id, 'credit': amount})]})
    if post:
        v.with_user(user).action_post()
    return v


def raw_move(user, post=True, **extra):
    m = M.with_user(user).create(dict({'move_type': 'entry', 'journal_id': jv_journal.id, 'date': d, 'line_ids': [
        Command.create({'account_id': expense.id, 'debit': 5}), Command.create({'account_id': bank.id, 'credit': 5})]}, **extra))
    if post:
        m.with_user(user).action_post()
    return m


def reverse(user, moves):
    w = env['account.move.reversal'].with_user(user).with_context(active_model='account.move', active_ids=moves.ids).create(
        {'journal_id': moves[:1].journal_id.id, 'date': d, 'reason': 'test'})
    return w.refund_moves()


# ====================================================================== stock (GRN / GIV / FPRR, Correct, Void)
print('\n--- stock')
SV = env['sb.stock.voucher']
prod = env['product.product'].search([('is_storable', '=', True), ('active', '=', True)], limit=1)
grn = SV.with_user(clerk).create({'voucher_type': 'grn', 'date': d, 'partner_id': supplier.id,
                                  'line_ids': [Command.create({'product_id': prod.id, 'quantity': 10, 'unit_cost': 20})]})
works(lambda: grn.with_user(clerk).action_post(), 'clerk posts a GRN')
check(grn.state == 'posted' and grn.move_id.state == 'posted', 'GRN %s posted with entry %s' % (grn.name, grn.move_id.name))
refused(lambda: reverse(accountant, grn.move_id), 'reversal wizard on a GRN entry')
refused(lambda: grn.move_id.with_user(manager).button_draft(), 'manager resets a GRN entry')
works(lambda: grn.with_user(accountant).action_correct(), 'accountant Correct on the GRN')
grn.with_user(accountant).line_ids.write({'unit_cost': 21})
works(lambda: grn.with_user(accountant).action_post(), 'corrected GRN posted again')
check(grn.state == 'posted' and abs(grn.move_id.amount_total - 210) < 0.01, 'GRN entry rewritten at 210 (%s)' % grn.move_id.amount_total)
giv = SV.with_user(clerk).create({'voucher_type': 'giv', 'date': d, 'line_ids': [Command.create({'product_id': prod.id, 'quantity': 2})]})
giv._onchange_default_account() if hasattr(giv, '_onchange_default_account') else None
works(lambda: giv.with_user(clerk).action_post(), 'clerk posts a GIV')
works(lambda: giv.with_user(accountant).action_void(), 'accountant voids the GIV')
check(giv.state == 'void', 'GIV void')
fp = SV.with_user(clerk).create({'voucher_type': 'fprr', 'date': d, 'line_ids': [Command.create({'product_id': prod.id, 'quantity': 3, 'unit_cost': 15})]})
fp._onchange_default_account()
works(lambda: fp.with_user(clerk).action_post(), 'clerk posts an FPRR')
works(lambda: fp.with_user(accountant).action_void(), 'accountant voids the FPRR')
works(lambda: grn.with_user(accountant).action_void(), 'accountant voids the GRN')
# a stock sale (cost of sales entry) voided
ss = V.with_user(clerk).create({'voucher_type': 'cash_sale', 'date': d, 'partner_id': customer.id, 'cash_account_id': bank.id, 'sb_pad_number': 'FS00991950',
                                'sb_sell_anyway': True,
                                'line_ids': [Command.create({'product_id': prod.id, 'name': prod.name, 'quantity': 1, 'price_unit': 50, 'account_id': income.id})]})
works(lambda: ss.with_user(clerk).action_post(), 'clerk posts a cash sale of stock')
if ss.sb_cost_move_ids:
    refused(lambda: reverse(accountant, ss.sb_cost_move_ids[:1]), 'reversal wizard on a cost of sales entry')
works(lambda: ss.with_user(accountant).action_void(), 'accountant voids the stock sale (goods and cost of sales back)')

# ====================================================================== legit voucher flows (roles)
print('\n--- vouchers: create / post / Edit / Discard / Void per role')
cs = works(lambda: cash_sale(clerk, 'FS00990001'), 'clerk posts a cash sale')
cs = V.search([('name', '=', 'FS00990001')], limit=1) or cash_sale(clerk, 'FS00990001')
check(cs.state == 'posted' and cs.move_id.state == 'posted', 'cash sale %s posted with its entry' % cs.name)
refused(lambda: V.with_user(u['auditor']).create({'voucher_type': 'journal', 'date': d}), 'auditor creates a voucher')
refused(lambda: cs.with_user(clerk).action_edit(), 'clerk Edit of a posted voucher')
refused(lambda: cs.with_user(clerk).action_void(), 'clerk Void')
jv_draft = journal(clerk, post=False)
refused(lambda: jv_draft.with_user(clerk).action_post(), 'clerk posts a journal voucher')
jv_draft.with_user(accountant).action_post()
check(jv_draft.state == 'posted', 'accountant posts the journal voucher')
# Edit -> change -> post again (same number, entry rebuilt)
rc = works(lambda: V.with_user(clerk).create({'voucher_type': 'receipt', 'date': d, 'partner_id': customer.id, 'received_amount': 50,
                                             'cash_line_ids': [Command.create({'account_id': bank.id, 'method': 'transfer', 'amount_currency': 50})],
                                             'line_ids': [Command.create({'account_id': income.id, 'amount': 50, 'name': 'x'})]}),
           'clerk creates a receipt')
rc = V.search([('partner_id', '=', customer.id), ('voucher_type', '=', 'receipt')], limit=1) or V.with_user(clerk).create(
    {'voucher_type': 'receipt', 'date': d, 'partner_id': customer.id, 'received_amount': 50,
     'cash_line_ids': [Command.create({'account_id': bank.id, 'method': 'transfer', 'amount_currency': 50})],
     'line_ids': [Command.create({'account_id': income.id, 'amount': 50, 'name': 'x'})]})
rc.with_user(clerk).action_post()
check(rc.state == 'posted', 'clerk posts a receipt %s' % rc.name)
name, move = jv_draft.name, jv_draft.move_id
jv_draft.with_user(accountant).action_edit()
check(jv_draft.state == 'draft' and jv_draft.sb_editing and move.state == 'posted', 'accountant Edit: voucher open, entry still posted')
jv_draft.with_user(accountant).write({'line_ids': [Command.update(jv_draft.line_ids[0].id, {'debit': 12}),
                                                   Command.update(jv_draft.line_ids[1].id, {'credit': 12})]})
jv_draft.with_user(accountant).action_post()
check(jv_draft.state == 'posted' and jv_draft.name == name and jv_draft.move_id == move and abs(move.amount_total - 12) < 0.01,
      'edited voucher reposted, same number and entry, new amount')
jv_draft.with_user(accountant).action_edit()
jv_draft.with_user(accountant).write({'memo': 'changed and discarded'})
jv_draft.with_user(accountant).action_discard_edit()
check(jv_draft.state == 'posted' and not jv_draft.sb_editing and jv_draft.memo != 'changed and discarded', 'Discard returns to the posted voucher')
works(lambda: cs.with_user(accountant).action_void(), 'accountant voids the cash sale')
check(cs.state == 'void' and cs.move_id.reversal_move_ids, 'cash sale void, entry reversed')

# ====================================================================== A. reversal wizard
print('\n--- A. reversal wizard')
refused(lambda: reverse(clerk, closing), 'clerk reverses the year-end closing entry %s' % closing.name)
refused(lambda: reverse(manager, closing), 'manager reverses the year-end closing entry')
refused(lambda: closing.with_user(manager)._reverse_moves([{'date': d}], cancel=True), 'manager _reverse_moves on the closing entry')
refused(lambda: closing.sudo()._reverse_moves([{'date': d}], cancel=True), 'superuser _reverse_moves on the closing entry (wizard only)')
check(company._sb_closed_through() == closed_before, 'year still closed through %s' % closed_before)
refused(lambda: reverse(accountant, rc.move_id), 'accountant reverses a voucher entry outside Void (%s)' % rc.name)
refused(lambda: reverse(manager, rc.move_id), 'manager reverses a voucher entry outside Void')
refused(lambda: reverse(accountant, cs.move_id.reversal_move_ids), 'accountant reverses the VOID reversal of a voucher')
plain = raw_move(accountant)
refused(lambda: reverse(clerk, plain), 'clerk reverses a posted journal entry (needs Accountant)')
works(lambda: reverse(accountant, plain), 'accountant reverses a plain journal entry with the wizard')

# ====================================================================== B. control flags
print('\n--- B. control flags')
refused(lambda: raw_move(clerk, post=False, sb_closing=True), 'clerk creates an entry with sb_closing')
refused(lambda: raw_move(manager, post=False, sb_closing=True), 'manager creates an entry with sb_closing')
refused(lambda: M.with_user(manager).with_context(default_sb_closing=True).create(
    {'move_type': 'entry', 'journal_id': jv_journal.id, 'date': d}), 'manager creates with default_sb_closing in the context')
refused(lambda: plain.with_user(manager).write({'sb_closing': True}), 'manager marks a posted entry as closing')
refused(lambda: closing.with_user(manager).write({'sb_closing': False}), 'manager unmarks the closing entry')
refused(lambda: rc.move_id.with_user(manager).with_context(sb_year_end=True, sb_move_flow=True).write({'sb_closing': True}),
        'manager forges the flow keys as plain context values')
refused(lambda: V.with_user(clerk).create({'voucher_type': 'cash_sale', 'date': d, 'state': 'posted'}), 'clerk creates a voucher already posted')
refused(lambda: V.with_user(clerk).with_context(default_state='posted').create({'voucher_type': 'cash_sale', 'date': d}),
        'clerk creates a voucher with default_state=posted')
refused(lambda: V.with_user(clerk).create({'voucher_type': 'journal', 'date': d, 'sb_editing': True}), 'clerk creates a voucher flagged as being edited')
refused(lambda: V.with_user(accountant).create({'voucher_type': 'opening', 'date': d, 'sb_ob_kind': 'account'}), 'opening-balance flag outside the opening screen')
refused(lambda: V.with_user(clerk).create({'voucher_type': 'journal', 'date': d, 'move_id': plain.id}), 'clerk links a draft voucher to an existing entry')

# ====================================================================== C. posted voucher write guard
print('\n--- C. posted vouchers')
refused(lambda: rc.with_user(clerk).write({'state': 'draft', 'sb_editing': True}), 'clerk forges Edit (state=draft, sb_editing)')
refused(lambda: rc.with_user(clerk).write({'state': 'void'}), 'clerk sets state=void while the entry stays posted')
refused(lambda: rc.with_user(manager).write({'date': date(2026, 10, 1)}), 'manager changes a posted voucher\'s date')
refused(lambda: rc.with_user(clerk).write({'partner_id': supplier.id}), 'clerk changes the partner of a posted voucher')
refused(lambda: rc.line_ids.with_user(clerk).write({'amount': 9999}), 'clerk changes a posted voucher\'s line amount')
refused(lambda: rc.cash_line_ids.with_user(clerk).write({'cheque_state': 'cleared'}), 'clerk sets a cheque status directly')
refused(lambda: env['sb.voucher.line'].with_user(clerk).create({'voucher_id': rc.id, 'account_id': income.id, 'amount': 5}), 'clerk adds a line to a posted voucher')
refused(lambda: rc.line_ids.with_user(manager).unlink(), 'manager deletes a posted voucher line')
draft = journal(accountant, post=False)
refused(lambda: draft.with_user(manager).write({'state': 'posted'}), 'manager marks a draft voucher posted (no entry)')
works(lambda: rc.with_user(clerk).write({'wht_certificate_no': 'WHT-TEST-1'}), 'withholding receipt number can still be filled in')
ye_v = V.search([('move_id', '=', closing.id)], limit=1)
if ye_v:
    refused(lambda: ye_v.with_user(manager).write({'date': date(2025, 7, 1)}), 'manager moves the closing voucher\'s date')
    refused(lambda: ye_v.with_user(manager).write({'state': 'void'}), 'manager voids the closing voucher by write')

# ====================================================================== D. raw journal entries
print('\n--- D. raw journal entries')
refused(lambda: raw_move(clerk), 'clerk creates and posts a raw journal entry')
refused(lambda: raw_move(clerk, post=False).with_user(clerk).write({'state': 'posted'}), 'clerk posts a raw entry by writing state')
refused(lambda: raw_move(clerk, post=False, state='posted'), 'clerk creates an entry already posted')
works(lambda: raw_move(accountant), 'accountant posts a raw journal entry')
works(lambda: raw_move(env.user), 'superuser (sync scripts) posts a raw journal entry')

# ====================================================================== E. managers and posted entries
print('\n--- E. managers and posted entries')
refused(lambda: plain.with_user(manager).button_draft(), 'manager resets a posted journal entry to draft')
refused(lambda: plain.with_user(manager).button_cancel(), 'manager cancels a posted journal entry')
refused(lambda: plain.with_user(manager).write({'state': 'draft'}), 'manager writes state=draft')
refused(lambda: plain.line_ids[0].with_user(manager).write({'account_id': income.id}), 'manager changes a posted line\'s account')
refused(lambda: rc.move_id.with_user(manager).button_draft(), 'manager resets a voucher\'s entry to draft')
refused(lambda: closing.with_user(manager).button_draft(), 'manager resets the closing entry')

# ====================================================================== F. fiscal receipts
print('\n--- F. fiscal receipts')
works(lambda: cash_sale(clerk, 'FS00991847'), 'cash sale on FS00991847')
for typed in ('FS00991847', 'FS-00991847', 'FS/00991847', 'F.S. 00991847', 'FS No. 00991847', 'fs no.991847', '00991847'):
    refused(lambda typed=typed: cash_sale(clerk, typed, vtype='sale'), 'credit invoice typed "%s" (same receipt)' % typed)
works(lambda: cash_sale(clerk, 'FS00991847/CLA', vtype='sale'), 'FS00991847/CLA is another register: accepted')
bare = works(lambda: cash_sale(clerk, '00991848'), 'a bare number takes the fiscal series')
bare = V.search([('name', 'ilike', '991848')], limit=1)
check(bare.name == 'FS00991848' or bare.name.startswith('FS'), 'bare number written as the register prints it (%s)' % bare.name)
# a duplicate already in the data (entered before this check) shows in the Document Numbering Check
dup = cash_sale(clerk, 'FS00991900')
dup2 = cash_sale(clerk, 'FS00991901', vtype='sale')
dup2.sudo().move_id.name = 'FS-00991900'
env.flush_all()
env.cr.execute("UPDATE sb_voucher SET name = 'FS-00991900' WHERE id = %s", (dup2.id,))
dup2.invalidate_recordset()
rep = env['sb.report'].get_report('numbering', {})
fs_row = next((r for r in rep['rows'] if r['values']['series'] == env['sb.doc.series'].search([('prefix', '=', 'FS'), ('suffix', '=', False)], limit=1).name), None)
print('   FS dups', fs_row and len(fs_row['values']['dups'].split(', ')))
print('   numbering FS row:', fs_row and {k: fs_row['values'][k] for k in ('first', 'last', 'docs', 'status')},
      'dups:', fs_row and fs_row['values']['dups'][:80], 'gaps:', fs_row and fs_row['values']['gaps'][:80])
used = env['sb.doc.series']._used_numbers(env['sb.doc.series'].search([('prefix', '=', 'FS'), ('suffix', '=', False)], limit=1), '')
print('   991900 ->', used.get(991900))
check(len(used.get(991900) or []) == 2, 'Numbering Check lists FS00991900 used twice (cash sale + credit invoice)')
check(fs_row and fs_row['values']['gaps'], 'Numbering Check lists missing fiscal receipts')

# ====================================================================== G. closing voucher cannot be opened
print('\n--- G. closing voucher')
if ye_v:
    refused(lambda: ye_v.with_user(manager).action_edit(), 'manager Edit of the year-end closing voucher %s' % ye_v.name)
    refused(lambda: ye_v.with_user(manager).action_void(), 'manager Void of the year-end closing voucher')

# ====================================================================== year-end close -> reopen -> close; periods
print('\n--- year end and periods')
W = env['sb.year.end.wizard'].with_user(manager)
from odoo.addons.sonador_books.models.ethiopian_calendar import fiscal_year_of
fy26 = fiscal_year_of(date(2026, 7, 7))
w = W.create({'year': str(fy26), 'retained_account_id': equity.id})
works(lambda: w.action_close_year(), 'manager closes FY ending %s' % w.date_to)
w = W.create({'year': str(fy26), 'retained_account_id': equity.id})
w.action_close_year() if w.status == 'open' else None
check(w.status == 'closed' and company._sb_closed_through() == w.date_to, 'year closed, books locked through %s' % w.date_to)
v26 = w.closing_voucher_id
refused(lambda: reverse(manager, v26.move_id), 'reversal wizard on the new closing entry')
pw = env['sb.period.wizard'].with_user(manager).create({'fiscal_year': fy26})
refused(lambda: pw.line_ids.filtered(lambda l: l.fiscal_year == fy26)[-1].action_reopen_from(), 'reopen a period of the closed year')
works(lambda: W.create({'year': str(fy26), 'retained_account_id': equity.id}).action_reopen_year(), 'manager reopens the year in the wizard')
check(v26.state == 'void' and company._sb_closed_through() == closed_before, 'reopened: closing voucher void, closed through %s again' % closed_before)
works(lambda: W.create({'year': str(fy26), 'retained_account_id': equity.id}).action_close_year(), 'manager closes the year again')
check(company._sb_closed_through() == w.date_to, 'closed again')
works(lambda: W.create({'year': str(fy26), 'retained_account_id': equity.id}).action_reopen_year(), 'and reopens it again')
pw = env['sb.period.wizard'].with_user(manager).create({})
line = pw.line_ids.filtered(lambda l: l.date_to < d)[-1]
print('   before close', company.fiscalyear_lock_date, line.date_to, line.fiscal_year)
works(lambda: (line.with_user(manager).action_close_through(), pw.with_user(manager).action_close_confirm()), 'manager closes periods through %s (checklist confirmed)' % line.date_to)
print('   log', env['sb.lock.log'].search([], limit=3).mapped(lambda l: (l.event, l.old_date, l.new_date, l.note)))
company.invalidate_recordset()
through = pw.line_ids.filtered(lambda l: l.date_to < d)[-1:].date_to  # the wizard rebuilds its lines after a close
line = pw.line_ids.filtered(lambda l: l.date_to == through)
check(company.fiscalyear_lock_date == through, 'locked through %s (is %s)' % (through, company.fiscalyear_lock_date))
works(lambda: line.with_user(manager).action_reopen_from(), 'manager reopens the period')
refused(lambda: env['sb.period.wizard'].with_user(accountant).create({}).line_ids[0].action_close_through(), 'accountant closes periods')

# ====================================================================== opening balances
print('\n--- opening balances')
B = env['sb.beginning'].with_user(accountant)
ctx = B.bb_context()
print('   opening date', ctx.get('opening'), 'books start', ctx.get('books_start'))
refused(lambda: env['sb.beginning'].with_user(clerk).bb_accounts_save(None, {str(expense.id): 1.0}), 'clerk saves opening balances')
# the opening date is in the closed FY2025: reopen it in the wizard, save a change (balanced), close it again
fy25 = fiscal_year_of(closed_before)
works(lambda: W.create({'year': str(fy25), 'retained_account_id': equity.id}).action_reopen_year(), 'reopen FY ending %s for opening balances' % closed_before)
data = B.bb_accounts(None)
rows = {r['id']: r['amount'] for r in data['rows']}
works(lambda: B.bb_accounts_save(None, {str(expense.id): rows.get(expense.id, 0.0) + 7.0, str(bank.id): rows.get(bank.id, 0.0) - 7.0}),
      'accountant saves an opening balance change (opening voucher reposted)')
works(lambda: W.create({'year': str(fy25), 'retained_account_id': equity.id}).action_close_year(), 'close FY ending %s again' % closed_before)

# ====================================================================== payroll, depreciation
print('\n--- payroll, depreciation')
run = env['sb.payroll.run'].search([('state', '=', 'draft'), ('line_ids', '!=', False)], limit=1)
if not run:
    try:
        S = env['sb.payroll.service']
        run = env['sb.payroll.run'].browse(S.run_create('2019-04', 'GL test payroll', 'regular'))
    except Exception as e:  # noqa: BLE001
        print('   (no payroll run could be made: %s)' % e)
if run and run.line_ids:
    hr = U.create({'name': 'GL test HR', 'login': 'gltest_hr@unio.local', 'company_id': company.id, 'company_ids': [Command.set([company.id])],
                   'group_ids': [Command.set([env.ref('sonador_books.group_sb_hr').id, env.ref('sonador_books.group_sb_manager').id]
                                             + ([env.ref('hr.group_hr_manager').id] if env.ref('hr.group_hr_manager', raise_if_not_found=False) else []))]})
    works(lambda: env['sb.payroll.service'].with_user(hr).run_action(run.id, 'post'), 'payroll run posted (%s)' % run.name)
    check(run.state == 'posted' and run.voucher_id.state == 'posted', 'payroll voucher %s posted' % run.voucher_id.name)
    works(lambda: env['sb.payroll.service'].with_user(hr).run_action(run.id, 'reset'), 'payroll reset (voucher voided)')
else:
    print('   payroll: no run with lines to post')
cat = env['account.asset.category'].create({'name': 'GL test equipment', 'account_asset_id': acc('1104-0001').id if False else
                                            env['account.account'].search([('account_type', '=', 'asset_fixed')], limit=1).id,
                                            'account_depreciation_id': env['account.account'].search([('account_type', '=', 'asset_fixed')], limit=1).id,
                                            'account_depreciation_expense_id': expense.id, 'journal_id': jv_journal.id,
                                            'method_number': 12, 'method_period': 1, 'open_asset': True})
asset = env['account.asset.asset'].create({'name': 'GL test printer', 'category_id': cat.id, 'value': 1200, 'date': date(2026, 9, 1),
                                            'first_depreciation_manual_date': date(2026, 9, 30)})
asset.validate()
line1 = asset.depreciation_line_ids.sorted('depreciation_date')[:1]
works(lambda: line1.with_user(manager).create_move(), 'manager (Odoo: depreciation lines are for accounting administrators) posts a depreciation line')
print('   depreciation lines', [(l.depreciation_date, l.move_id.name, l.move_id.state) for l in asset.depreciation_line_ids[:3]])
check(line1.move_id.state == 'posted', 'depreciation entry %s posted' % line1.move_id.name)
line2 = asset.depreciation_line_ids.sorted('depreciation_date')[1:2]
refused(lambda: line2.with_user(clerk).create_move(), 'clerk posts depreciation (raw entry, Accountant)')
works(lambda: env['account.asset.asset']._cron_generate_entries(), 'depreciation cron (superuser)')

# ====================================================================== H.
print('\n--- H.')
old = env['account.move.line'].search([('parent_state', '=', 'posted'), ('date', '<=', closed_before),
                                       ('account_id.account_type', '=', 'income')], limit=1).account_id
refused(lambda: old.with_user(manager).write({'account_type': 'asset_current'}), 'income account with closed-year entries -> balance sheet (%s)' % old.code)
refused(lambda: old.with_user(manager).write({'account_type': 'expense'}), 'income -> expense')
refused(lambda: old.with_user(manager).write({'account_type': 'income_other'}), 'income -> other income (any type change, round 3)')
fresh = env['account.account'].create({'name': 'GL test unused', 'code': '999991', 'account_type': 'expense'})
works(lambda: fresh.with_user(manager).write({'account_type': 'asset_current'}), 'unused account changes type freely')
fresh.with_user(manager).write({'name': 'GL test renamed', 'code': '999992', 'reconcile': True})
env.cr.precommit.run()
tracked = fresh.message_ids.tracking_value_ids.field_id.mapped('name')
check({'name', 'account_type', 'reconcile'} <= set(tracked), 'chart of accounts changes in the chatter: %s' % sorted(set(tracked)))
check('code' in tracked or 'code_store' in tracked, 'code change tracked')
warn = V.new({'date': fields.Date.today() + timedelta(days=90)})._onchange_sb_future_date()
check(warn and 'warning' in warn, 'voucher dated 90 days ahead: warning "%s"' % (warn and warn['warning']['message']))
check(not V.new({'date': fields.Date.today() + timedelta(days=10)})._onchange_sb_future_date(), 'no warning 10 days ahead')
refused(lambda: env['account.fiscal.year.closing'].with_user(manager).new({}).action_close(), 'fiscal year app: Year-End Closing')
refused(lambda: env['change.lock.date'].with_user(manager).new({}).update_lock_date(), 'fiscal year app: Lock Dates')
check(not any(env.ref(x).active for x in ('om_fiscal_year.menu_fiscal_year_closing', 'om_fiscal_year.menu_action_change_lock_date',
                                         'om_fiscal_year.menu_account_period_closing')), 'its menus are hidden')


# ====================================================================== ROUND 3 (auditor items A-L)
from odoo.addons.sonador_books.models.move_guard import controlled
admin = env.ref('base.user_admin')
stock_user = U.create({'name': 'GL test store', 'login': 'gltest_store@unio.local', 'company_id': company.id,
                       'company_ids': [Command.set([company.id])], 'group_ids': [Command.set([env.ref('stock.group_stock_manager').id,
                                                                                               env.ref('sonador_books.group_sb_clerk').id])]})

print('\n--- R3 A. done stock moves of a posted document (OS/24-25/0001, closed year)')
os_doc = SV.search([('name', '=', 'OS/24-25/0001')], limit=1)
om = os_doc.stock_move_ids.filtered(lambda m: m.state == 'done')[:1]
onhand0, value0 = om.product_id.qty_available, sum(abs(m.value) for m in os_doc.stock_move_ids)
for who, usr in (('clerk', clerk), ('manager', manager), ('admin', admin), ('stock manager', stock_user)):
    refused(lambda usr=usr: om.with_user(usr).write({'quantity': om.quantity + 100}), '%s: done move quantity +100' % who)
    refused(lambda usr=usr: om.with_user(usr).write({'price_unit': 999}), '%s: done move price_unit' % who)
    refused(lambda usr=usr: om.with_user(usr).write({'value': 1}), '%s: done move value' % who)
    refused(lambda usr=usr: om.with_user(usr).write({'date': fields.Datetime.to_datetime('2024-01-01 09:00:00')}), '%s: done move date into a closed year' % who)
    refused(lambda usr=usr: om.with_user(usr).write({'sb_stock_voucher_id': False}), '%s: unlink the move from its document' % who)
    refused(lambda usr=usr: om.move_line_ids[:1].with_user(usr).write({'quantity': om.move_line_ids[:1].quantity + 100}), '%s: done move line quantity' % who)
    refused(lambda usr=usr: om.move_line_ids[:1].with_user(usr).write({'date': fields.Datetime.to_datetime('2024-01-01 09:00:00')}), '%s: done move line date' % who)
refused(lambda: om.with_user(stock_user).write({'value_manual': 1}), 'stock manager: Odoo valuation adjustment (value_manual)')
refused(lambda: env['product.value'].with_user(stock_user).create({'move_id': om.id, 'value': 1, 'product_id': om.product_id.id,
                                                                    'company_id': company.id}), 'stock manager: product.value on a done move')
refused(lambda: env['stock.move'].with_user(clerk).create({'product_id': om.product_id.id, 'product_uom_qty': 5, 'quantity': 5,
                                                           'product_uom': om.product_uom.id, 'location_id': om.location_id.id,
                                                           'location_dest_id': om.location_dest_id.id, 'state': 'done'}), 'clerk creates a done stock move')
refused(lambda: env['stock.move.line'].with_user(stock_user).create({'move_id': om.id, 'product_id': om.product_id.id, 'quantity': 5,
                                                                      'location_id': om.location_id.id, 'location_dest_id': om.location_dest_id.id}),
        'stock manager adds a line to a done move')
draft_move = env['stock.move'].with_user(clerk).create({'product_id': om.product_id.id, 'product_uom_qty': 5, 'product_uom': om.product_uom.id,
                                                        'location_id': om.location_id.id, 'location_dest_id': om.location_dest_id.id})
refused(lambda: draft_move.with_user(clerk).write({'state': 'done', 'quantity': 5}), 'clerk marks a draft move done by writing')
quant = env['stock.quant'].search([('product_id', '=', om.product_id.id), ('location_id.usage', '=', 'internal')], limit=1)
if quant:
    refused(lambda: quant.with_user(stock_user).write({'quantity': quant.quantity + 100}), 'stock manager writes a quant quantity')
refused(lambda: env['stock.quant'].with_user(stock_user).create({'product_id': om.product_id.id, 'location_id': om.location_dest_id.id,
                                                                 'quantity': 100}), 'stock manager creates a quant with a quantity')
om.invalidate_recordset()
check(om.product_id.qty_available == onhand0 and abs(sum(abs(m.value) for m in os_doc.stock_move_ids) - value0) < 0.01,
      'on hand and value unchanged (%s, %s)' % (onhand0, value0))
# legit: an inventory count through Odoo's inventory mode, and a stock adjustment document
wh_stock = env['stock.warehouse'].search([('company_id', '=', company.id)], limit=1).lot_stock_id
before = prod.with_context(location=wh_stock.id).qty_available
def odoo_count():
    q = env['stock.quant'].with_user(stock_user).with_context(inventory_mode=True).create(
        {'product_id': prod.id, 'location_id': wh_stock.id, 'inventory_quantity': before + 3})
    q.action_apply_inventory()
    return prod.with_context(location=wh_stock.id).qty_available
after = works(odoo_count, 'stock manager applies an inventory count (Odoo inventory mode)')
check(after is not None and abs(after - before - 3) < 1e-6, 'count applied: %s -> %s' % (before, after))
def adjust():
    a = SV.with_user(accountant).create({'voucher_type': 'adjustment', 'date': d, 'is_count': False,
                                         'line_ids': [Command.create({'product_id': prod.id, 'quantity': 2, 'unit_cost': 20})]})
    a._onchange_default_account() if hasattr(a, '_onchange_default_account') else None
    a.with_user(accountant).action_post()
    return a.state
check(works(adjust, 'accountant posts a stock adjustment document') == 'posted', 'adjustment posted')

print('\n--- R3 B. auto-post cron')
refused(lambda: raw_move(clerk, post=False, auto_post='at_date'), 'clerk creates an entry set to post automatically')
cm = raw_move(clerk, post=False)
refused(lambda: cm.with_user(clerk).write({'auto_post': 'at_date'}), 'clerk sets auto_post on a draft entry')
cm.with_user(accountant).write({'auto_post': 'at_date'})
cm.line_ids[:1].with_user(clerk).write({'name': 'changed by the clerk'})
am = raw_move(accountant, post=False)
am.with_user(accountant).write({'auto_post': 'at_date'})
Cron, Cursor = type(env['ir.cron']), type(env.cr)
saved = (Cron._commit_progress, Cursor.rollback)
Cron._commit_progress = lambda self, *a, **k: None  # the cron commits: not in a test
Cursor.rollback = lambda self: None
try:
    with env.cr.savepoint():
        M.sudo()._autopost_draft_entries(batch_size=1000)
        check(cm.state == 'draft' and cm.auto_post == 'no', 'cron does not post the entry a clerk wrote (%s, auto_post %s)' % (cm.state, cm.auto_post))
        print('   cron note:', (cm.message_ids[:1].body or '')[:200])
        check(am.state == 'posted', 'cron posts the accountant\'s entry (%s)' % am.state)
        raise UserError('roll back the cron')
except UserError:
    pass
finally:
    Cron._commit_progress, Cursor.rollback = saved

print('\n--- R3 C. fiscal receipt look-alikes')
works(lambda: cash_sale(clerk, 'FS00991858'), 'cash sale FS00991858')
for typed in ('FS No00991858', 'FSNo 00991858', 'FS Nº 00991858', 'FS N° 00991858', 'ＦＳ００９９１８４８'):
    refused(lambda typed=typed: cash_sale(clerk, typed, vtype='sale'), 'credit invoice typed "%s" (same receipt)' % typed)
refused(lambda: cash_sale(clerk, 'FЅ' + '00991859'), 'Cyrillic look-alike "FЅ00991859" (non-Latin letter)')
refused(lambda: cash_sale(clerk, 'FЅ' + '00991858', vtype='sale'), 'Cyrillic look-alike of FS00991858')

print('\n--- R3 D. a voucher under Edit')
ed = journal(accountant)
ed.with_user(accountant).action_edit()
refused(lambda: ed.with_user(clerk).write({'memo': 'clerk memo', 'date': date(2026, 10, 1)}), 'clerk changes a voucher under edit')
refused(lambda: ed.line_ids[:1].with_user(clerk).write({'debit': 99}), 'clerk changes a line of a voucher under edit')
refused(lambda: ed.with_user(clerk).action_post(), 'clerk posts the edit')
refused(lambda: ed.with_user(clerk).action_discard_edit(), 'clerk discards the edit')
refused(lambda: ed.with_user(accountant).action_void(), 'void of a voucher under edit (discard or post first)')
check(ed.sb_edit_uid == accountant, 'Edit opened by recorded (%s)' % ed.sb_edit_uid.name)
works(lambda: ed.with_user(accountant).write({'memo': 'accountant memo'}), 'accountant changes it')
works(lambda: ed.with_user(accountant).action_post(), 'accountant posts the edit')
env.cr.precommit.run()
check(any('posted by' in (m.body or '') and accountant.name in (m.body or '') for m in ed.message_ids), 'chatter names who finished the edit')
ed.with_user(accountant).action_edit()
works(lambda: ed.with_user(manager).action_discard_edit(), 'manager discards an edit')

print('\n--- R3 E. cancelled entries that were posted')
cx = raw_move(accountant)
controlled(cx).button_cancel()
check(cx.state == 'cancel' and cx.posted_before, 'entry %s cancelled after posting' % cx.name)
refused(lambda: cx.line_ids[:1].with_user(clerk).write({'account_id': income.id}), 'clerk changes a line account of a cancelled posted entry')
refused(lambda: cx.with_user(clerk).write({'date': date(2026, 10, 1)}), 'clerk changes its date')
refused(lambda: cx.with_user(clerk).write({'ref': 'x', 'narration': 'y'}), 'clerk changes its reference')
refused(lambda: cx.with_user(manager).write({'posted_before': False}), 'manager clears posted_before')
refused(lambda: cx.with_user(manager).write({'name': '/'}), 'manager blanks its number')
refused(lambda: cx.with_user(manager).button_draft(), 'manager resets it to draft')
refused(lambda: cx.with_user(manager).unlink(), 'manager deletes it')
refused(lambda: raw_move(clerk, post=False).with_user(clerk).write({'posted_before': True}), 'clerk sets posted_before on a draft')

print('\n--- R3 F. history of stock and other financial documents')
sdoc = SV.search([('state', '=', 'posted'), ('message_ids', '!=', False)], limit=1)
if sdoc:
    msg = sdoc.message_ids[:1]
    refused(lambda: msg.with_user(clerk).write({'body': 'rewritten'}), 'clerk rewrites the chatter of %s' % sdoc.name)
    refused(lambda: msg.with_user(manager).unlink(), 'manager deletes the chatter of %s' % sdoc.name)
    refused(lambda: sdoc.with_user(manager)._message_update_content(msg, body='x'), 'chatter Edit on a stock document')
for model in ('sb.bank.rec', 'account.payment', 'sb.payroll.run'):
    m = env['mail.message'].search([('model', '=', model)], limit=1)
    if m:
        refused(lambda m=m: m.with_user(manager).unlink(), 'manager deletes the chatter of a %s' % model)

print('\n--- R3 G. lock date exceptions')
n0 = env['sb.lock.log'].search_count([])
exc = works(lambda: env['account.lock_exception'].with_user(manager).create({'tax_lock_date': date(2025, 1, 1), 'reason': 'GL test',
                                                                              'company_id': company.id}), 'manager grants a tax lock exception')
exc = env['account.lock_exception'].search([('reason', '=', 'GL test')], limit=1)
log = env['sb.lock.log'].search([('event', '=', 'exception')], limit=1)
check(exc and log.lock_field == 'tax_lock_date' and 'GL test' in (log.note or ''), 'exception logged: %s' % log.note)
if exc:
    exc.with_user(manager).action_revoke()
    check(env['sb.lock.log'].search([('event', '=', 'exception_end')], limit=1).note.endswith('(revoked)'), 'revocation logged')
    exc2 = env['account.lock_exception'].with_user(manager).create({'tax_lock_date': date(2025, 1, 1), 'reason': 'GL test 2',
                                                                    'end_datetime': fields.Datetime.now() - timedelta(hours=1), 'company_id': company.id})
    env['account.lock_exception']._sb_log_expired()
    check(exc2.sb_end_logged and 'expired' in env['sb.lock.log'].search([('event', '=', 'exception_end')], limit=1).note, 'expiry logged')
refused(lambda: env['sb.lock.log'].search([], limit=1).with_user(manager).write({'note': 'x'}), 'the log cannot be changed')

print('\n--- R3 H. chart of accounts across a closed year')
asset = env['account.move.line'].search([('parent_state', '=', 'posted'), ('date', '<=', closed_before),
                                         ('account_id.account_type', '=', 'asset_current')], limit=1).account_id
for t in ('liability_current', 'equity', 'asset_receivable', 'asset_non_current', 'asset_cash'):
    refused(lambda t=t: asset.with_user(manager).write({'account_type': t}), '%s asset_current -> %s' % (asset.code, t))

print('\n--- R3 I. posted entries created through the context')
for usr in (clerk, accountant, manager):
    refused(lambda usr=usr: M.with_user(usr).with_context(default_state='posted').create({'move_type': 'entry', 'journal_id': jv_journal.id, 'date': d}),
            '%s creates an entry with default_state=posted' % usr.name)
refused(lambda: M.with_user(clerk).with_context(default_posted_before=True).create({'move_type': 'entry', 'journal_id': jv_journal.id, 'date': d}),
        'clerk creates an entry with default_posted_before')
refused(lambda: M.with_user(accountant).create({'move_type': 'entry', 'journal_id': jv_journal.id, 'date': d, 'state': 'posted'}),
        'accountant creates an entry already posted')

print('\n--- R3 J. series counters')
jv_series = env['sb.doc.series'].search([('doc_type', '=', 'journal'), ('is_default', '=', True), ('company_id', '=', company.id)], limit=1)
env['sb.doc.series.spoil'].with_user(accountant).create({'series_id': jv_series.id, 'reason': 'GL test spoiled', 'date': d})
spoiled = jv_series.spoil_ids[:1]
counter = jv_series._counter(d)
print('   spoiled', spoiled.number_from, 'counter', counter.next_number)
refused(lambda: counter.with_user(manager).write({'next_number': counter.next_number - 1}), 'manager rewinds the counter onto the spoiled number')
refused(lambda: counter.with_user(manager).write({'next_number': 1}), 'manager rewinds the counter to 1')
refused(lambda: counter.with_user(manager).write({'key': 'xx'}), 'manager changes the counter year')
nxt = counter.next_number
works(lambda: counter.with_user(manager).write({'next_number': nxt + 5}), 'manager moves the counter forward (new pad)')
log = env['sb.lock.log'].search([('event', '=', 'series')], limit=1)
check(log and str(nxt) in log.note and str(nxt + 5) in log.note, 'counter change logged: %s' % (log.note,))
cpv = env['sb.doc.series'].search([('doc_type', '=', 'payment'), ('is_default', '=', True), ('company_id', '=', company.id)], limit=1)
highest = max(cpv._sb_type_numbers(fy_label(d)) | cpv._existing_numbers(d), default=0)
check(cpv._next_number(d) > highest, 'CPV next %s is after the highest CPV used (%s), never a gap below it' % (cpv._next_number(d), highest))

petty = env['sb.doc.series'].search([('doc_type', '=', 'payment'), ('sb_cash_account_id', '!=', False), ('company_id', '=', company.id)], limit=1)
if petty:
    def petty_pay():
        v = V.with_user(clerk).create({'voucher_type': 'payment', 'date': d, 'partner_id': supplier.id, 'cash_account_id': petty.sb_cash_account_id.id,
                                       'cash_line_ids': [Command.create({'account_id': petty.sb_cash_account_id.id, 'method': 'cash', 'amount_currency': 7})],
                                       'line_ids': [Command.create({'account_id': expense.id, 'amount': 7, 'name': 'petty'})]})
        v.with_user(clerk).action_post()
        return v.name
    name = works(petty_pay, 'clerk pays from %s' % petty.sb_cash_account_id.display_name)
    check(name and name.startswith(petty.prefix.strip()), 'petty cash payment numbered in its pad: %s' % name)

print('\n--- R3 K. far-future dates')
def pay(user, day):
    v = V.with_user(user).create({'voucher_type': 'journal', 'date': day, 'line_ids': [
        Command.create({'account_id': expense.id, 'debit': 3}), Command.create({'account_id': bank.id, 'credit': 3})]})
    v.with_user(accountant).action_post()
    return v
refused(lambda: rc.copy({'date': date(2062, 10, 10)}).with_user(clerk).action_post(), 'clerk posts a receipt dated 2062')
refused(lambda: pay(accountant, date(2062, 10, 10)), 'accountant posts a voucher dated 2062 (after next fiscal year)')
far = fields.Date.today() + timedelta(days=90)
refused(lambda: rc.copy({'date': far}).with_user(clerk).action_post(), 'clerk posts a receipt dated %s (90 days ahead)' % far)
works(lambda: pay(accountant, far), 'accountant posts a voucher 90 days ahead')

print('\n--- R3 L. reference and memo of posted entries')
refused(lambda: plain.with_user(clerk).write({'ref': 'rewritten'}), 'clerk rewrites ref of a posted entry')
refused(lambda: plain.with_user(manager).write({'narration': 'rewritten'}), 'manager rewrites narration of a posted entry')
refused(lambda: closing.with_user(clerk).write({'ref': 'rewritten'}), 'clerk rewrites ref of the closing entry')
refused(lambda: closing.with_user(manager).write({'narration': 'rewritten'}), 'manager rewrites narration of the closing entry')

print('RESULT', 'ALL PASS (%d checks)' % len(ok) if all(ok) else '%d FAILED of %d' % (ok.count(False), len(ok)))
env.cr.rollback()
