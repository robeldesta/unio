# Sales return approval (commercial approver before finance, Me > Approvals, send back with a reason, no self-approval)
# and Replacement (Peachtree's REP + OFF SET REP): the worked example of the report, to the cent. Rolled back at the end.
#   ~/ecogreen-odoo19/sync/local_shell.sh < ~/sonador-odoo/addons/sonador_returns/tests_manual/replacement.py
# Worked example: a cash sale of 10 @ 80 (cost 50); 10 come back damaged; the good stock now costs 55; 10 given in
# replacement: the customer ends at nil, sales returns and allowances at nil, cost of sales = 550 (the new goods),
# damaged stock 500 (the goods returned).
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError

assert env.cr.dbname not in ('ecogreen_test', 'ecogreen_web'), 'never on the live database'
ok = []


def check(cond, msg):
    print(('PASS ' if cond else 'FAIL ') + msg)
    ok.append(bool(cond))


def refused(fn, msg):
    try:
        with env.cr.savepoint():
            fn()
    except (AccessError, UserError, ValidationError) as e:
        check(True, msg + ' (refused: %s)' % str(e).splitlines()[0][:110])
        return
    check(False, msg + ' (was allowed)')


def near(a, b, tol=0.005):
    return abs((a or 0.0) - (b or 0.0)) <= tol


company = env.company
Stock = env['sb.stock']
acc = lambda code: env['account.account'].search([('code', '=', code), ('company_ids', 'in', company.id)], limit=1)  # noqa: E731
FG, DEF, COGS, DMG_EXP, RET, INC = (acc(c) for c in ('1504-0000', '1504-0001', '5000-0007', '5000-1013', '4000-0005', '4000-0002'))
BANK = env['account.account'].search([('account_type', '=', 'asset_cash'), ('company_ids', 'in', company.id)], limit=1)
lock = company._get_user_fiscal_lock_date(company.account_stock_journal_id)
today = fields.Date.context_today(env['res.users'])
d0 = max(lock + timedelta(days=1), today - timedelta(days=10))
d1, d2, d3 = d0 + timedelta(days=1), d0 + timedelta(days=2), d0 + timedelta(days=3)

g = env.ref
U = env['res.users'].with_context(no_reset_password=True, mail_create_nolog=True, mail_notrack=True)


def user(login, groups):
    return U.create({'name': 'T %s' % login, 'login': 't_rep_%s@example.local' % login, 'company_id': company.id,
                     'company_ids': [Command.set([company.id])], 'group_ids': [Command.set([g('base.group_user').id] + [g(x).id for x in groups])]})


store = user('store', ['sonador_inventory.group_sb_store_user'])
yohana = user('yohana', ['sonador_sale_approval.group_so_commercial'])
leul = user('leul', ['sonador_sale_approval.group_so_commercial'])
finance = user('finance', ['sonador_books.group_sb_accountant', 'sonador_inventory.group_sb_store_user'])
Emp = env['hr.employee'].sudo()
e_leul = Emp.create({'name': 'T Leul', 'user_id': leul.id, 'company_id': company.id})
Emp.create({'name': 'T Yohana', 'user_id': yohana.id, 'parent_id': e_leul.id, 'company_id': company.id})
company.sudo().write({'sb_sales_return_account_id': RET.id, 'sb_return_cost_account': 'cogs', 'sb_return_needs_invoice': True,
                      'sb_returns_always_damaged': True, 'sb_return_approval': True, 'sb_return_approver_id': yohana.id,
                      'sb_self_approval': 'up', 'sb_allow_negative_stock': False})
Series = env['sb.doc.series'].sudo()
if not Series.search([('company_id', '=', company.id), ('doc_type', '=', 'replacement')]):
    Series.create({'name': 'Replacement (REP)', 'doc_type': 'replacement', 'prefix': 'REP/', 'padding': 5, 'is_default': True,
                   'company_id': company.id})

Cat = env['product.category'].sudo()
c_def = Cat.create({'name': 'T Defect (AVCO)', 'property_cost_method': 'average', 'property_valuation': 'real_time',
                    'property_stock_valuation_account_id': DEF.id, 'property_account_expense_categ_id': DMG_EXP.id,
                    'property_account_income_categ_id': INC.id, 'sb_damaged_prefix': 'Defect Product - '})
c_fg = Cat.create({'name': 'T Finished (AVCO)', 'property_cost_method': 'average', 'property_valuation': 'real_time',
                   'property_stock_valuation_account_id': FG.id, 'property_account_expense_categ_id': COGS.id,
                   'property_account_income_categ_id': INC.id, 'sb_damaged_categ_id': c_def.id})
X = env['product.product'].sudo().create({'name': 'T Rep Aquapure', 'is_storable': True, 'type': 'consu', 'categ_id': c_fg.id,
                                          'list_price': 80.0, 'taxes_id': [Command.clear()]})
wh = env['stock.warehouse'].search([('company_id', '=', company.id), ('code', '=', 'WH')], limit=1)
cust = env['res.partner'].create({'name': 'T Replacement Customer', 'customer_rank': 1})
SV, V = env['sb.stock.voucher'], env['sb.voucher']


def grn(day, qty, cost):
    r = SV.create({'voucher_type': 'grn', 'date': day, 'warehouse_id': wh.id, 'partner_id': cust.id,
                   'line_ids': [Command.create({'product_id': X.id, 'quantity': qty, 'unit_cost': cost})]})
    r.action_post()


def lines_of(move):
    out = {}
    for l in move.line_ids:
        out[l.account_id.code] = round(out.get(l.account_id.code, 0.0) + l.balance, 2)
    return {k: v for k, v in out.items() if v}


def customer_balance():
    env.flush_all()
    lines = env['account.move.line'].search([('partner_id', '=', cust.id), ('account_id.account_type', '=', 'asset_receivable'),
                                             ('parent_state', '=', 'posted')])
    return sum(lines.mapped('balance'))


# ------------------------------------------------------------------ the sale, and stock now costing 55
grn(d0, 100, 50.0)
sale = V.create({'voucher_type': 'cash_sale', 'partner_id': cust.id, 'date': d0, 'sb_warehouse_id': wh.id, 'sb_pad_number': 'FS99990901',
                 'line_ids': [Command.create({'product_id': X.id, 'quantity': 10, 'price_unit': 80.0, 'account_id': INC.id})],
                 'cash_line_ids': [Command.create({'account_id': BANK.id, 'method': 'cash'})]})
if 'sb_deliver_later' in sale._fields:
    sale.sb_deliver_later = False
sale.action_post()
check(sale.state == 'posted' and near(sale.sb_cogs, 500.0), '1. cash sale 10 @ 80 = 800.00 paid; cost of sales 500.00 (10 x 50)')
grn(d1, 90, 60.0)
check(near(Stock._avg_on(X, company, d2), 55.0), 'the good stock now costs 55.00 ((90 x 50 + 90 x 60) / 180)')

# ------------------------------------------------------------------ the return: store -> commercial approval -> finance
ret = SV.with_user(store).create({'voucher_type': 'customer_return', 'date': d2, 'warehouse_id': wh.id, 'partner_id': cust.id,
                                  'sb_origin_voucher_id': sale.id, 'sb_return_reason': 'damaged', 'sb_credit_note_number': 'CN 99911',
                                  'line_ids': [Command.create({'product_id': X.id, 'quantity': 10, 'sb_price': 80.0, 'sb_condition': 'good'})]})
refused(lambda: ret.with_user(store).action_sb_send_return_approval(), 'Eco Green: a return line "in good condition" (returns always go to damaged stock)')
ret.line_ids.sb_condition = 'damaged'
refused(lambda: ret.with_user(store).action_sb_send_finance(), 'straight to finance without the commercial approval')
refused(lambda: ret.with_user(finance).action_post(), 'finance cannot post a return not approved')
ret.with_user(store).action_sb_send_return_approval()
check(ret.sb_appr_state == 'waiting', 'the store sends it for approval')
me_y = env['sb.me'].with_user(yohana)
items = [i for i in me_y.approvals() if i['key'] == 'return:%d' % ret.id]
check(bool(items) and near(items[0]['amount'], 800.0) and any('10 ×' in d for d in items[0]['details']) and
      any('Reason' in d for d in items[0]['details']), 'Yohana has it in Me > Approvals: items, quantities, 800.00 and the reason')
check(not [i for i in env['sb.me'].with_user(leul).approvals() if i['key'] == 'return:%d' % ret.id], 'Leul does not (Yohana is the named approver)')
refused(lambda: me_y.decide('return:%d' % ret.id, False, ''), 'send back without a reason')
me_y.decide('return:%d' % ret.id, False, 'Count the jerricans again')
check(ret.sb_appr_state == 'sent_back' and ret.sb_sent_back_reason == 'Count the jerricans again', 'sent back with the reason')
ret.with_user(store).action_sb_send_return_approval()
me_y.decide('return:%d' % ret.id, True, '')
check(ret.sb_appr_state == 'approved' and ret.sb_appr_by == yohana and bool(ret.sb_sent_finance_at), 'Yohana approves: it goes to finance')
fin_items = [i for i in env['sb.me'].with_user(finance).approvals() if i['key'] == 'store:%d' % ret.id]
check(bool(fin_items), 'finance has it in Me > Approvals')
refused(lambda: ret.with_env(env).action_sb_give_replacement(), 'a replacement before the return is posted')
env['sb.me'].with_user(finance).decide('store:%d' % ret.id, True, '')
ret = ret.with_env(env)
cn = ret.sb_credit_note_id
check(ret.state == 'posted' and cn.state == 'posted', '2. finance posts the return %s and its credit note %s' % (ret.name, cn.name))
check(lines_of(cn.move_id) == {'4000-0005': 800.0, cn.move_id.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable').account_id.code: -800.0,
                               '1504-0001': 500.0, '5000-0007': -500.0},
      'CN: Dr 4000-0005 800 / Cr customer 800; Dr damaged 1504-0001 500 (10 x 50, the sold cost) / Cr 5000-0007 500')
check(near(customer_balance(), -800.0) and near(ret.sb_owed, 800.0) and near(ret.sb_remaining, 800.0), 'the customer is owed 800.00')
check(near(Stock._avg_on(X, company, d3), 55.0), 'the good stock average is still 55.00')

# self-approval not allowed: Yohana's own return goes one level up (Leul)
own = SV.create({'voucher_type': 'customer_return', 'date': d2, 'warehouse_id': wh.id, 'partner_id': cust.id, 'sb_origin_voucher_id': sale.id,
                 'line_ids': [Command.create({'product_id': X.id, 'quantity': 1, 'sb_price': 80.0})]})
own.with_user(yohana).sudo().action_sb_send_return_approval()  # she prepares it herself (as her, with the document's rights)
check(own.sb_appr_user_id == leul, 'Yohana\'s own return goes to Leul (no self-approval)')
check(not [i for i in me_y.approvals() if i['key'] == 'return:%d' % own.id] and
      [i for i in env['sb.me'].with_user(leul).approvals() if i['key'] == 'return:%d' % own.id], 'it is in Leul\'s approvals, not Yohana\'s')

# ------------------------------------------------------------------ the replacement
act = ret.action_sb_give_replacement()
rep = V.browse(act['res_id'])
check(rep.state == 'draft' and rep.voucher_type == 'sale' and near(rep.amount_total, 800.0) and rep.line_ids.account_id == RET,
      'Give replacement: a sales invoice of 10 @ 80 = 800.00 on 4000-0005 (draft, items and quantities editable)')
rep.date = d3
Y = X.copy({'name': 'T Rep Other item'})
with env.cr.savepoint():
    rep.line_ids[:1].product_id = Y
    refused(lambda: rep.action_post(), 'a replacement with an item that was not returned (same items only)')
    rep.line_ids[:1].product_id = X
with env.cr.savepoint():
    rep.line_ids[:1].quantity = 11
    rep.line_ids[:1].price_unit = 800 / 11
    refused(lambda: rep.action_post(), 'a replacement of 11 when 10 were returned (even at no more value)')
    rep.line_ids[:1].write({'quantity': 10, 'price_unit': 80.0})
rep.invalidate_recordset()
rep.action_post()
check(rep.state == 'posted' and (rep.name or '').startswith('REP/'), '3. replacement posted: %s (the Replacement series, as Odoo 18)' % rep.name)
L = lines_of(rep.move_id)
ar = rep.move_id.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable').account_id.code
check(near(L.get(ar), 800.0) and near(L.get('4000-0005'), -800.0), 'OFF SET REP: Dr customer 800 / Cr 4000-0005 800')
check(near(L.get('5000-0007'), 550.0) and near(L.get('1504-0000'), -550.0), 'REP: Dr cost of sales 550 / Cr 1504-0000 550 (10 x 55, the new goods)')
pdf, _ = env['ir.actions.report']._render_qweb_pdf('sonador_returns.action_report_replacement', rep.ids)
html = env['ir.actions.report']._render_qweb_html('sonador_returns.action_report_replacement', rep.ids)[0].decode()
check(rep.sb_printable and rep.action_sb_print()['report_name'] == 'sonador_returns.report_replacement' and pdf[:4] == b'%PDF'
      and 'Inventory Replacement' in html and rep.name in html and ret.name in html and 'T Rep Aquapure' in html,
      'Print gives the Replacement printout (Odoo 18 Inventory Replacement layout): %s for %s' % (rep.name, ret.name))
ret.invalidate_recordset()
check(near(customer_balance(), 0.0), 'the customer is at nil (paid 800, owed 800, replaced 800)')
check(near(ret.sb_owed, 800.0) and near(ret.sb_replaced, 800.0) and near(ret.sb_remaining, 0.0) and near(ret.sb_refunded, 0.0),
      'RET shows Owed 800 / Replaced 800 / Refunded 0 / Remaining 0')
env.flush_all()
for code, want in (('4000-0005', 0.0), ('5000-0007', 550.0), ('1504-0001', 500.0)):
    got = sum(env['account.move.line'].search([('move_id', 'in', (sale.move_id | cn.move_id | rep.move_id).ids), ('account_id.code', '=', code)]).mapped('balance'))
    check(near(got, want), 'over the three documents %s: %.2f' % (code, want))
check(near(sum(env['account.move.line'].search([('move_id', 'in', (sale.move_id | cn.move_id | rep.move_id).ids),
                                                ('account_id.code', 'in', ('4000-0002',))]).mapped('balance')), -800.0),
      'sales 4000-0002: the 800.00 of the original sale only (the replacement is not a sale)')
if 'sb_delivery_id' in rep._fields and rep.sb_deliver_later:
    check(bool(rep.sb_delivery_id), 'the replacement goes out with a delivery order: %s' % rep.sb_delivery_id.display_name)
refused(lambda: ret.action_sb_give_replacement(), 'a second replacement when nothing is owed any more')
refused(lambda: ret.with_context(sb_void_reason='x').action_void(), 'voiding a return whose replacement is given')

# ------------------------------------------------------------------ partial replacement, then void
rep.with_context(sb_void_reason='test').action_void()
ret.invalidate_recordset()
check(rep.state == 'void' and near(ret.sb_remaining, 800.0), 'replacement voided: the customer is owed 800.00 again')
part = V.browse(ret.action_sb_give_replacement()['res_id'])
part.line_ids.quantity = 6
part.date = d3
part.action_post()
ret.invalidate_recordset()
check(near(ret.sb_replaced, 480.0) and near(ret.sb_remaining, 320.0), 'partial replacement of 6: Replaced 480 / Remaining 320 (still owed)')
more = V.browse(ret.action_sb_give_replacement()['res_id'])
check(near(more.line_ids.quantity, 4.0), 'the next replacement proposes the 4 not replaced yet')
more.line_ids.quantity = 5
refused(lambda: (setattr(more, 'date', d3), more.action_post()), '5 when only 4 are still to be replaced')
env.flush_all()
env.cr.execute("SELECT COALESCE(SUM(l.debit) - SUM(l.credit), 0) FROM account_move_line l JOIN account_move m ON m.id = l.move_id "
               "WHERE m.state = 'posted' AND m.company_id = %s", (company.id,))
check(near(float(env.cr.fetchone()[0]), 0.0, 0.01), 'trial balance balances')
print('\n%s of %s passed' % (sum(ok), len(ok)))
env.cr.rollback()
