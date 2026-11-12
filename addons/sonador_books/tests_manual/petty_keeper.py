"""The petty cash keeper posts the payment vouchers paid from the box she keeps (and nothing else); journal vouchers on an
open-numbering series. Eco Green data (Zewdinesh, Zufan). Rolls back.
  ~/ecogreen-odoo19/sync/local_shell.sh < ~/sonador-odoo/addons/sonador_books/tests_manual/petty_keeper.py"""
from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError

res = []


def ok(c, m):
    print(('PASS ' if c else 'FAIL ') + m)
    res.append(bool(c))


def refused(fn, what):
    try:
        with env.cr.savepoint():
            fn()
        ok(False, 'refused: ' + what)
        return ''
    except (UserError, AccessError, ValidationError) as e:
        ok(True, 'refused: %s (%s)' % (what, str(e).split('\n')[0][:110]))
        return str(e)


try:
    c = env.company
    U = env['res.users'].sudo()
    zew = U.search([('login', '=ilike', 'zewdinesh@ecogreen.et')], limit=1)
    zufan = U.search([('login', '=ilike', 'zufan@ecogreen.et')], limit=1)
    A = env['account.account']
    box = A.search([('code', '=', '1101-0000'), ('company_ids', 'in', c.id)], limit=1)
    box2 = A.search([('code', '=', '1101-0008'), ('company_ids', 'in', c.id)], limit=1)
    bank = env['account.journal'].search([('type', '=', 'bank'), ('company_id', '=', c.id)], limit=1).default_account_id
    expense = A.search([('company_ids', 'in', c.id), ('account_type', '=', 'expense'), ('active', '=', True)], limit=1)
    (box | box2).write({'sb_custodian_id': zew.id})
    petty = env.ref('sonador_books.group_sb_petty_cash')
    zew.write({'group_ids': [Command.set([env.ref('base.group_user').id, petty.id])]})
    ok(not zew.has_group('sonador_books.group_sb_clerk') and not zew.has_group('sonador_books.group_sb_auditor'),
       'Zewdinesh: Petty cash keeper only, no Clerk / Auditor role')
    # cash in the box (a top-up from the bank, by the system)
    jv = env['account.move']._sb_reversal_journal(c)
    top = env['account.move'].create({'move_type': 'entry', 'journal_id': jv.id, 'date': fields.Date.today(), 'line_ids': [
        Command.create({'account_id': box.id, 'debit': 20000}), Command.create({'account_id': bank.id, 'credit': 20000})]})
    top.action_post()

    # what she sees
    menus = env['ir.ui.menu'].with_user(zew)._visible_menu_ids()
    ok(env.ref('sonador_books.menu_sb_petty_cash_root').id in menus and env.ref('sonador_books.menu_sb_root').id not in menus,
       'menus: Petty Cash, not Accounting')
    env['sb.voucher'].with_user(zew).get_views([(False, 'list'), (False, 'form')])
    ok(True, 'the payment list and form open for her')
    V = env['sb.voucher'].with_user(zew).with_context(default_voucher_type='payment')
    v = V.create({'voucher_type': 'payment', 'payer_type': 'employee', 'memo': 'Taxi and stamps',
                  'line_ids': [Command.create({'account_id': expense.id, 'name': 'Taxi', 'amount': 350})]})
    ok(v.cash_account_id == box, 'new payment pays from her box by default (%s)' % v.cash_account_id.code)
    v._onchange_sync_single_deposit()
    if not v.cash_line_ids:
        v.cash_line_ids = [Command.create({'account_id': box.id, 'method': 'cash'})]
        v._onchange_sync_single_deposit()
    v.action_post()
    ok(v.state == 'posted' and v.move_id.state == 'posted', 'she posted %s from %s without the Clerk role' % (v.name, box.code))
    v2 = V.create({'voucher_type': 'payment', 'payer_type': 'employee', 'cash_account_id': box2.id, 'payment_method': 'cash',
                   'cash_line_ids': [Command.create({'account_id': box2.id, 'method': 'cash'})],
                   'line_ids': [Command.create({'account_id': expense.id, 'name': 'Water', 'amount': 1})]})
    v2._onchange_sync_single_deposit()
    refused(lambda: v2.action_post(), 'her second box goes below zero (overdraft / petty cash rules kept)') \
        if env['account.move.line'].search_count([('account_id', '=', box2.id)]) == 0 else None
    # paid from elsewhere: Clerk rule as today
    vb = V.create({'voucher_type': 'payment', 'payer_type': 'employee', 'cash_account_id': bank.id,
                   'cash_line_ids': [Command.create({'account_id': bank.id, 'method': 'transfer'})],
                   'line_ids': [Command.create({'account_id': expense.id, 'name': 'Rent', 'amount': 100})]})
    vb._onchange_sync_single_deposit()
    e = refused(lambda: vb.action_post(), 'a payment from the bank')
    ok('Clerk' in e, 'the message names the Clerk role')
    vm = V.create({'voucher_type': 'payment', 'payer_type': 'employee', 'cash_account_id': box.id,
                   'cash_line_ids': [Command.create({'account_id': box.id, 'method': 'cash'}), Command.create({'account_id': bank.id, 'method': 'transfer', 'amount': 1})],
                   'line_ids': [Command.create({'account_id': expense.id, 'name': 'Mixed', 'amount': 2})]})
    refused(lambda: vm.action_post(), 'part from her box, part from the bank')
    vt = V.create({'voucher_type': 'payment', 'payer_type': 'employee', 'cash_account_id': box.id,
                   'cash_line_ids': [Command.create({'account_id': box.id, 'method': 'cash'})],
                   'line_ids': [Command.create({'account_id': bank.id, 'name': 'To the bank', 'amount': 100})]})
    vt._onchange_sync_single_deposit()
    refused(lambda: vt.action_post(), 'cash from her box into another cash / bank account (a transfer)')
    refused(lambda: env['sb.voucher'].with_user(zew).create({'voucher_type': 'cash_purchase', 'cash_account_id': box.id,
                                                              'cash_line_ids': [Command.create({'account_id': box.id})],
                                                              'line_ids': [Command.create({'account_id': expense.id, 'amount': 5})]}).action_post(),
            'a cash purchase (not a payment voucher)')
    refused(lambda: v.with_user(zew).action_edit(), 'editing her posted voucher (Accountant)')
    refused(lambda: v.with_user(zew).action_void(), 'voiding her posted voucher (Accountant)')
    # what she sees of the vouchers
    other = env['sb.voucher'].search([('voucher_type', '=', 'payment'), ('state', '=', 'posted'), ('create_uid', '!=', zew.id),
                                      ('cash_account_id', 'not in', (box | box2).ids),
                                      ('cash_line_ids.account_id', 'not in', (box | box2).ids)], limit=1)
    ok(other, 'a payment from elsewhere to compare: %s' % other.name)
    seen = env['sb.voucher'].with_user(zew).search([])
    ok(v in seen and other not in seen and all(x.voucher_type == 'payment' for x in seen),
       'she sees payments from her boxes only (%d), not the bank payments' % len(seen))
    refused(lambda: other.with_user(zew).read(['name']), 'reading a bank payment')
    ok(env['sb.voucher'].with_user(zufan).search_count([('id', '=', other.id)]) == 1, 'finance still sees everything')

    # ------------------------------------------------------------------ journal vouchers: open numbering
    S = env['sb.doc.series']
    series = S._for('journal', company=c)
    series.sudo().sb_open_numbering = True

    def journal(number, amount=10):
        j = env['sb.voucher'].with_user(zufan).create({'voucher_type': 'journal', 'sb_pad_number': number, 'line_ids': [
            Command.create({'account_id': expense.id, 'debit': amount}), Command.create({'account_id': bank.id, 'credit': amount})]})
        j.action_post()
        return j

    nxt = series.next_preview
    j1 = journal('  JV/TB/SENE2054 ')
    ok(j1.name == 'JV/TB/SENE2054', 'typed number kept, trimmed: %s' % j1.name)
    e = refused(lambda: journal('jv/tb/sene 2054'), 'the same number typed again (case, spaces aside)')
    ok(j1.name in e, 'the message names the document that has it')
    refused(lambda: journal('JV/' + 'X' * 45), 'a number longer than 40 characters')
    j2 = journal(False)
    ok(j2.name == nxt, 'left blank: the next number of the series (%s)' % j2.name)
    j3 = journal('JV/BB/BANKS/2099-07-08')
    ok(j3.name == 'JV/BB/BANKS/2099-07-08', 'any text: %s' % j3.name)
    # (the whole Document Numbering Check runs out of memory on this copy: a fiscal receipt numbered 96,530,897 in "FS")
    used = S._used_numbers(series, series._key(fields.Date.today()))
    ok(j2.name in sum(used.values(), []) and not any(j1.name in v or j3.name in v for v in used.values()),
       'numbering check: typed numbers are not series numbers (no gaps from them), JV-157 is')
    refused(lambda: j1.with_user(zufan).write({'name': 'JV-999'}), 'renumbering a posted journal voucher')
finally:
    print('RESULT: %d/%d passed' % (sum(res), len(res)))
    env.cr.rollback()
