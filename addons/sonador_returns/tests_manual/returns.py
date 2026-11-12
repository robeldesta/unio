# Returns and damaged stock: SDV, sales return (RET) with its credit note, RPRV, their entries to the cent, the damaged
# location, the cost a return comes back at (the sale's), back-dated re-costing passed on, voids, closed periods, the
# store prepares / finance posts rule, ledger = stock records, trial balance. Rolled back at the end.
#   ~/ecogreen-odoo19/sync/local_shell.sh < ~/sonador-odoo/addons/sonador_returns/tests_manual/returns.py
# (or on ecogreen_stage with the docker compose exec pattern). Never on the live database.
from datetime import date, timedelta

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
Acc = env['account.account']
acc = lambda code: Acc.search([('code', '=', code), ('company_ids', 'in', company.id)], limit=1)  # noqa: E731
FG, DEF, COGS, DMG_EXP, RET, AR_SALES = acc('1504-0000'), acc('1504-0001'), acc('5000-0007'), acc('5000-1013'), acc('4000-0005'), acc('4000-0002')
RAW, WIP = acc('1501-0000'), acc('1505-0000')
assert FG and DEF and COGS and RET and RAW and WIP, 'Eco Green chart expected'

# open dates (after any closed period), receipts first within a day
lock = company._get_user_fiscal_lock_date(company.account_stock_journal_id)
d0 = max(lock + timedelta(days=1), fields.Date.context_today(env['res.users']) - timedelta(days=20))
d1, d2, d3, d4, d5, d6 = (d0 + timedelta(days=i) for i in range(1, 7))

# the generic rules (no commercial approval, good-condition lines allowed): replacement.py tests Eco Green's
company.sudo().write({'sb_sales_return_account_id': RET.id, 'sb_return_cost_account': 'cogs', 'sb_return_needs_invoice': True,
                      'sb_allow_negative_stock': False, 'sb_return_approval': False, 'sb_returns_always_damaged': False})
Cat = env['product.category'].sudo()
c_def = Cat.create({'name': 'T Defect (AVCO)', 'property_cost_method': 'average', 'property_valuation': 'real_time',
                    'property_stock_valuation_account_id': DEF.id, 'property_account_expense_categ_id': DMG_EXP.id,
                    'property_account_income_categ_id': AR_SALES.id, 'sb_damaged_prefix': 'Defect Product - '})
c_fg = Cat.create({'name': 'T Finished (AVCO)', 'property_cost_method': 'average', 'property_valuation': 'real_time',
                   'property_stock_valuation_account_id': FG.id, 'property_account_expense_categ_id': COGS.id,
                   'property_account_income_categ_id': AR_SALES.id, 'sb_damaged_categ_id': c_def.id})
c_raw = Cat.create({'name': 'T Recycled input (AVCO)', 'property_cost_method': 'average', 'property_valuation': 'real_time',
                    'property_stock_valuation_account_id': RAW.id, 'property_account_expense_categ_id': COGS.id,
                    'property_account_income_categ_id': AR_SALES.id})
P = env['product.product'].sudo()
X = P.create({'name': 'T 5 Liter Aquapure', 'is_storable': True, 'type': 'consu', 'categ_id': c_fg.id, 'list_price': 80.0,
              'taxes_id': [Command.clear()]})
Y = P.create({'name': 'T Recycled liquid', 'is_storable': True, 'type': 'consu', 'categ_id': c_raw.id, 'taxes_id': [Command.clear()]})
wh = env['stock.warehouse'].search([('company_id', '=', company.id), ('code', '=', 'WH')], limit=1)
cust = env['res.partner'].create({'name': 'T Return Customer', 'customer_rank': 1})
watch = FG | DEF | RAW | WIP | COGS | DMG_EXP | RET


def balances():
    env.flush_all()
    env.cr.execute("""SELECT l.account_id, SUM(l.balance) FROM account_move_line l JOIN account_move m ON m.id = l.move_id
                      WHERE m.state = 'posted' AND l.account_id = ANY(%s) GROUP BY 1""", (watch.ids,))
    return dict(env.cr.fetchall())


base = balances()


def moved(account):
    return float(balances().get(account.id, 0.0)) - float(base.get(account.id, 0.0))


def stock_value(p):
    return Stock._state_before(p, company, date(2100, 1, 1))


def ledger_equals_stock(label):
    items = {FG: [X], RAW: [Y]}
    d = X.product_tmpl_id.sb_damaged_product_id
    if d:
        items[DEF] = [d]
    for account, prods in items.items():
        sv = sum(stock_value(p)[1] for p in prods)
        check(near(moved(account), sv, 0.02), '%s: ledger %s moved %.2f = stock value %.2f' % (label, account.code, moved(account), sv))


def tb_balanced(label):
    env.flush_all()
    env.cr.execute("SELECT COALESCE(SUM(l.debit) - SUM(l.credit), 0) FROM account_move_line l JOIN account_move m ON m.id = l.move_id "
                   "WHERE m.state = 'posted' AND m.company_id = %s", (company.id,))
    check(near(float(env.cr.fetchone()[0]), 0.0, 0.01), '%s: trial balance balances' % label)


def lines_of(move):
    out = {}
    for l in move.line_ids:
        out[l.account_id.code] = round(out.get(l.account_id.code, 0.0) + l.balance, 2)
    return {k: v for k, v in out.items() if v}


SV = env['sb.stock.voucher']
V = env['sb.voucher']


def grn(day, qty, cost, product=X):
    g = SV.create({'voucher_type': 'grn', 'date': day, 'warehouse_id': wh.id, 'partner_id': cust.id,
                   'line_ids': [Command.create({'product_id': product.id, 'quantity': qty, 'unit_cost': cost})]})
    g.action_post()
    return g


fs = [99990710]


def fiscal_no():
    fs[0] += 1
    return 'FS%08d' % fs[0]


# ------------------------------------------------------------------ users: the store prepares, finance posts
U = env['res.users'].with_context(no_reset_password=True, mail_create_nolog=True, mail_notrack=True)
g = env.ref
store = U.create({'name': 'T Store', 'login': 't_ret_store@example.local', 'company_id': company.id, 'company_ids': [Command.set([company.id])],
                  'group_ids': [Command.set([g('base.group_user').id, g('sonador_inventory.group_sb_store_user').id])]})
finance = U.create({'name': 'T Finance', 'login': 't_ret_fin@example.local', 'company_id': company.id, 'company_ids': [Command.set([company.id])],
                    'group_ids': [Command.set([g('base.group_user').id, g('sonador_books.group_sb_accountant').id,
                                               g('sonador_inventory.group_sb_store_user').id])]})

# ------------------------------------------------------------------ 1. stock and a sale
grn(d0, 100, 50.0)
sale = V.create({'voucher_type': 'sale', 'partner_id': cust.id, 'date': d1, 'sb_warehouse_id': wh.id, 'sb_pad_number': fiscal_no(),
                 'line_ids': [Command.create({'product_id': X.id, 'quantity': 10, 'price_unit': 80.0, 'account_id': AR_SALES.id})]})
sale.action_post()
check(sale.state == 'posted' and near(sale.sb_cogs, 500.0), 'sale of 10 @ 80 posted, cost of sales 500.00 (10 x 50)')
grn(d2, 100, 60.0)
avg_before = Stock._avg_on(X, company, d3)
check(near(avg_before, 10500.0 / 190), 'good stock average after the second receipt 55.2632 (10,500 / 190)')

# ------------------------------------------------------------------ 2. sales return (RET) against the invoice
ret = SV.with_user(store).create({'voucher_type': 'customer_return', 'date': d3, 'warehouse_id': wh.id, 'partner_id': cust.id,
                                  'sb_origin_voucher_id': sale.id, 'sb_found_by': 'Store keeper', 'sb_credit_note_number': 'CN 99901',
                                  'line_ids': [Command.create({'product_id': X.id, 'quantity': 4, 'sb_price': 75.0, 'sb_condition': 'damaged'}),
                                               Command.create({'product_id': X.id, 'quantity': 2, 'sb_price': 80.0, 'sb_condition': 'good'})]})
refused(lambda: ret.with_user(store).action_post(), 'a store clerk cannot post a sales return')
ret.with_user(store).action_sb_send_finance()
check(bool(ret.sb_sent_finance_at), 'the store sends the sales return to finance')
too_many = SV.create({'voucher_type': 'customer_return', 'date': d3, 'warehouse_id': wh.id, 'partner_id': cust.id, 'sb_origin_voucher_id': sale.id,
                      'sb_credit_note_number': 'CN 99909', 'line_ids': [Command.create({'product_id': X.id, 'quantity': 11, 'sb_price': 80.0})]})
refused(lambda: too_many.action_post(), 'a return of more than was sold (11 of 10)')
no_inv = SV.create({'voucher_type': 'customer_return', 'date': d3, 'warehouse_id': wh.id, 'partner_id': cust.id,
                    'line_ids': [Command.create({'product_id': X.id, 'quantity': 1, 'sb_price': 80.0})]})
refused(lambda: no_inv.action_post(), 'a sales return without its invoice (Eco Green: required)')
ret.with_user(finance).action_sb_finance_post()
ret = ret.with_env(env)
D = X.product_tmpl_id.sb_damaged_product_id
cn = ret.sb_credit_note_id
check(ret.state == 'posted' and ret.name.startswith('RET/'), 'finance posts it: %s' % ret.name)
check(bool(D) and D.categ_id == c_def and D.name == 'Defect Product - T 5 Liter Aquapure', 'damaged counterpart made: %s' % D.name)
check(cn.state == 'posted' and cn.voucher_type == 'sale_return' and cn.origin_id == sale, 'credit note %s posted against %s' % (cn.name, sale.name))
L = lines_of(cn.move_id)
check(near(L.get('4000-0005'), 460.0), 'CN: Dr Sales returns and allowances 460.00 (4 x 75 + 2 x 80: the return price)')
check(near(-sum(v for k, v in L.items() if k.startswith('1400') or k.startswith('121') or k.startswith('110')), 460.0) or
      near(sum(l.balance for l in cn.move_id.line_ids if l.account_id.account_type == 'asset_receivable'), -460.0),
      'CN: Cr customer 460.00')
check(near(L.get('1504-0001'), 200.0), 'CN: Dr damaged inventory 1504-0001 200.00 (4 x 50: the cost they were sold at, not the 55.26 average)')
check(near(L.get('1504-0000'), 100.0), 'CN: Dr inventory 1504-0000 100.00 (2 good units x 50)')
check(near(L.get('5000-0007'), -300.0), 'CN: Cr cost of sales 5000-0007 300.00 (the sale\'s own cost reversed)')
dmg = Stock._sb_damaged_location(wh)
check(near(Stock._on_hand(D, dmg), 4.0) and dmg.complete_name.endswith('/Damaged'), 'the 4 damaged units are in %s' % dmg.complete_name)
check(near(Stock._on_hand(X, wh.lot_stock_id), 192.0), 'good stock: 190 + 2 good units back = 192')
check(near(ret.amount_total, 300.0), 'RET value at cost 300.00')
refused(lambda: sale.action_void(), 'the invoice cannot be voided while its return stands')
ledger_equals_stock('after the return')

# ------------------------------------------------------------------ 3. store damage (SDV)
avg4 = Stock._avg_on(X, company, d4)
sdv = SV.with_user(store).create({'voucher_type': 'damage', 'date': d4, 'warehouse_id': wh.id, 'sb_found_by': 'Night guard',
                                  'sb_return_reason': 'damaged', 'memo': 'Jerricans crushed by forklift',
                                  'line_ids': [Command.create({'product_id': X.id, 'quantity': 5})]})
refused(lambda: sdv.with_user(store).action_post(), 'a store clerk cannot post a store damage voucher')
big = SV.create({'voucher_type': 'damage', 'date': d4, 'warehouse_id': wh.id, 'line_ids': [Command.create({'product_id': X.id, 'quantity': 500})]})
refused(lambda: big.action_post(), 'store damage of more than is on hand')
sdv.with_user(store).action_sb_send_finance()
sdv.with_user(finance).action_sb_finance_post()
sdv = sdv.with_env(env)
exp = round(5 * avg4, 2)
check(sdv.state == 'posted' and sdv.name.startswith('SDV/'), 'SDV posted: %s' % sdv.name)
check(near(avg4, 10600.0 / 192), 'average on the SDV date 55.2083 (10,600 / 192: the good returns came back at 50)')
check(lines_of(sdv.move_id) == {'1504-0001': exp, '1504-0000': -exp}, 'SDV: Dr 1504-0001 / Cr 1504-0000 %.2f (5 x average)' % exp)
check(near(Stock._on_hand(D, dmg), 9.0) and near(Stock._on_hand(X, wh.lot_stock_id), 187.0), 'damaged 9 units, good 187 units')
check(near(Stock._avg_on(X, company, d5), avg4, 0.0001), 'the good stock average is untouched by the SDV')
check(near(Stock._avg_on(D, company, d5), (200.0 + exp) / 9, 0.0001), 'damaged pool average %.4f ((200 + %.2f) / 9): its own pool' % ((200 + exp) / 9, exp))
refused(lambda: SV.create({'voucher_type': 'damage', 'date': d4, 'warehouse_id': wh.id,
                           'line_ids': [Command.create({'product_id': D.id, 'quantity': 1})]}).action_post(), 'damaging damaged stock')
ledger_equals_stock('after the SDV')

# ------------------------------------------------------------------ 4. recycled product receiving (RPRV) and its use
rprv = SV.with_user(store).create({'voucher_type': 'rprv', 'date': d5, 'warehouse_id': wh.id, 'memo': 'Refilled',
                                   'line_ids': [Command.create({'product_id': D.id, 'quantity': 9, 'sb_side': 'out'}),
                                                Command.create({'product_id': Y.id, 'quantity': 8, 'sb_side': 'in'})]})
refused(lambda: rprv.with_user(store).action_post(), 'a store clerk cannot post an RPRV')
rprv.with_user(finance).action_post()
rprv = rprv.with_env(env)
dval = 200.0 + exp
check(rprv.name.startswith('RPRV/'), 'RPRV posted: %s' % rprv.name)
check(lines_of(rprv.move_id) == {'1501-0000': round(dval, 2), '1504-0001': -round(dval, 2)},
      'RPRV: Dr recycled input 1501-0000 / Cr damaged 1504-0001 %.2f (all 9 damaged units)' % dval)
y_in = rprv.line_ids.filtered(lambda l: l.sb_side == 'in')
check(near(y_in.unit_cost, dval / 8, 0.0001), 'recycled input unit cost %.4f (%.2f / 8 received: the recovery loss is in the cost)' % (dval / 8, dval))
check(near(Stock._on_hand(D, dmg), 0.0), 'damaged stock empty')
giv = SV.create({'voucher_type': 'giv', 'date': d6, 'warehouse_id': wh.id, 'account_id': WIP.id, 'department': 'Production',
                 'line_ids': [Command.create({'product_id': Y.id, 'quantity': 8})]})
giv.action_post()
check(near(giv.amount_total, round(dval, 2)), 'production takes the recycled input at its cost: GIV %.2f to work in process' % giv.amount_total)
ledger_equals_stock('after the RPRV and its use')
tb_balanced('after the documents')

# ------------------------------------------------------------------ 5. a receipt dated earlier: everything re-costed and passed on
grn(d1, 100, 80.0)  # same day as the sale: receipts first, the sale now costs (5,000 + 8,000) / 200 = 65
sale.invalidate_recordset()
check(near(sale.sb_cogs, 650.0), 'the sale is re-costed: 650.00 (10 x 65)')
d_moves = cn.sb_stock_move_ids.filtered(lambda m: m.product_id == D and not m.sb_reversal_of_id)
check(near(sum(abs(m.value) for m in d_moves), 260.0), 'the damaged return follows the sale: 4 x 65 = 260.00')
avg4b = Stock._avg_on(X, company, d4)
sdv_out = sdv.stock_move_ids.filtered(lambda m: m.product_id == X)
sdv_in = sdv.stock_move_ids.filtered(lambda m: m.product_id == D)
check(near(abs(sdv_out.value), round(5 * avg4b, 2), 0.02) and near(abs(sdv_in.value), abs(sdv_out.value)),
      'SDV re-costed at the new average %.4f: out %.2f = damaged in %.2f' % (avg4b, abs(sdv_out.value), abs(sdv_in.value)))
y_move = rprv.stock_move_ids.filtered(lambda m: m.product_id == Y)
d_out = sum(abs(m.value) for m in rprv.stock_move_ids.filtered(lambda m: m.product_id == D))
check(near(abs(y_move.value), d_out, 0.02), 'RPRV re-costed: recycled input %.2f = damaged taken out %.2f' % (abs(y_move.value), d_out))
giv.invalidate_recordset()
check(near(giv.amount_total, abs(y_move.value), 0.02), 'and the GIV to production follows: %.2f' % giv.amount_total)
ledger_equals_stock('after the receipt dated earlier')
tb_balanced('after the re-costing')

# ------------------------------------------------------------------ 6. closed period
company.sudo().write({'fiscalyear_lock_date': d4})
late = SV.create({'voucher_type': 'damage', 'date': d4, 'warehouse_id': wh.id, 'line_ids': [Command.create({'product_id': X.id, 'quantity': 1})]})
refused(lambda: late.action_post(), 'an SDV dated in a closed period')
late_ret = SV.create({'voucher_type': 'customer_return', 'date': d3, 'warehouse_id': wh.id, 'partner_id': cust.id, 'sb_origin_voucher_id': sale.id,
                      'sb_credit_note_number': 'CN 99902', 'line_ids': [Command.create({'product_id': X.id, 'quantity': 1, 'sb_price': 80.0})]})
refused(lambda: late_ret.action_post(), 'a sales return dated in a closed period')
company.sudo().write({'fiscalyear_lock_date': lock})

# ------------------------------------------------------------------ 7. voids (the reverse order of use)
refused(lambda: rprv.with_context(sb_void_reason='test').action_void(), 'voiding the RPRV while production used its recycled input')
giv.with_context(sb_void_reason='test').action_void()
rprv.with_context(sb_void_reason='test').action_void()
check(rprv.state == 'void' and near(Stock._on_hand(D, dmg), 9.0), 'RPRV voided: the 9 damaged units are back')
sdv.with_context(sb_void_reason='test').action_void()
check(sdv.state == 'void' and near(Stock._on_hand(D, dmg), 4.0), 'SDV voided: 4 damaged units left (the return)')
refused(lambda: ret.action_void(), 'a sales return is voided only with a reason')
ret.write({'sb_void_reason': 'entered on the wrong customer'})
ret.action_void()
check(ret.state == 'void' and cn.state == 'void', 'RET voided with its credit note')
check(near(Stock._on_hand(D, dmg), 0.0) and near(Stock._on_hand(X, wh.lot_stock_id), 290.0), 'all damaged stock gone, good stock 290')
check(near(moved(RET), 0.0) and near(moved(DEF), 0.0), 'nothing left on sales returns and damaged inventory')
ledger_equals_stock('after the voids')
tb_balanced('after the voids')

# ------------------------------------------------------------------ 8. a return without an invoice (company allowing it): average cost
company.sudo().sb_return_needs_invoice = False
r2 = SV.create({'voucher_type': 'customer_return', 'date': d6, 'warehouse_id': wh.id, 'partner_id': cust.id, 'sb_credit_note_number': 'CN 99903',
                'line_ids': [Command.create({'product_id': X.id, 'quantity': 2, 'sb_price': 70.0})]})
avg6 = Stock._avg_on(X, company, d6)
r2.action_post()
check(near(lines_of(r2.sb_credit_note_id.move_id).get('1504-0001'), round(2 * avg6, 2), 0.02),
      'no invoice named: the damaged goods come back at the average on the return date (2 x %.4f)' % avg6)
company.sudo().sb_return_cost_account = 'damage'
r3 = SV.create({'voucher_type': 'customer_return', 'date': d6, 'warehouse_id': wh.id, 'partner_id': cust.id, 'sb_credit_note_number': 'CN 99904',
                'line_ids': [Command.create({'product_id': X.id, 'quantity': 1, 'sb_price': 70.0})]})
r3.action_post()
check('5000-1013' in lines_of(r3.sb_credit_note_id.move_id) and '5000-0007' not in lines_of(r3.sb_credit_note_id.move_id),
      'company choice "damaged class expense": Cr 5000-1013 Damage of Product (Peachtree DAM entries)')
ledger_equals_stock('at the end')
tb_balanced('at the end')

print('\n%s of %s passed' % (sum(ok), len(ok)))
env.cr.rollback()
