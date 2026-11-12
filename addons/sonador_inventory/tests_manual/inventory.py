# Inventory end-to-end: every document, FIFO and average cost, cost of sales, voids, GRN -> bill,
# and the stock records always equal to the general ledger. Dates in 2030, clear of real data. Rolled back.
from datetime import date
from odoo import Command
from odoo.exceptions import UserError

ok = []


def check(cond, msg):
    print(('PASS ' if cond else 'FAIL ') + msg)
    ok.append(bool(cond))


def entry(move):
    e = {}
    for l in move.line_ids:
        e[l.account_id.code] = round(e.get(l.account_id.code, 0) + l.balance, 2)
    return {k: v for k, v in e.items() if v}


env.company.fiscalyear_lock_date = False
company = env.company
Acc = lambda c: env['account.account'].search([('code', '=', c), ('company_ids', 'in', company.id)], limit=1)  # noqa: E731
R = env['sb.report']
SV, V = env['sb.stock.voucher'], env['sb.voucher']
wh = env['stock.warehouse'].search([('company_id', '=', company.id)], limit=1)
wh2 = env['stock.warehouse'].create({'name': 'Test Site Store', 'code': 'TSS', 'company_id': company.id})
fifo_c = env['product.category'].search([('name', '=', 'Merchandise (FIFO)')], limit=1)
avco_c = env['product.category'].search([('name', '=', 'Raw Materials & Supplies (AVCO)')], limit=1)
vat = env['account.tax'].browse(1)
F = env['product.product'].create({'name': 'Test Laptop (FIFO)', 'default_code': 'T-LAP', 'categ_id': fifo_c.id,
                                   'is_storable': True, 'type': 'consu', 'list_price': 200, 'sb_reorder_point': 20, 'sb_reorder_qty': 30})
A = env['product.product'].create({'name': 'Test Cement (AVCO)', 'default_code': 'T-CEM', 'categ_id': avco_c.id,
                                   'is_storable': True, 'type': 'consu', 'list_price': 100})
check(F.sb_item_kind == 'stock' and F.cost_method == 'fifo' and A.cost_method == 'average', 'stock items with FIFO and average costing')
supplier = env['res.partner'].create({'name': 'Test Supplier PLC', 'supplier_rank': 1})
customer = env['res.partner'].create({'name': 'Test Customer PLC', 'customer_rank': 1})
inv_f, inv_a = Acc('235100'), Acc('235200')
bank = Acc('11000001')
D = lambda m, d: date(2030, m, d)  # noqa: E731


def gl_agrees(label):
    doc = R.get_report('inv_gl_rec', {'date_to': '2030-12-31'})
    total = next(r for r in doc['rows'] if r['id'] == 'total')['values']
    check(abs(total['diff']) < 0.01, '%s: stock records = GL (stock %s, GL %s)' % (label, round(total['stock'], 2), round(total['gl'], 2)))


# 1. opening stock
os_ = SV.create({'voucher_type': 'opening', 'date': D(7, 1), 'warehouse_id': wh.id,
                 'line_ids': [Command.create({'product_id': F.id, 'quantity': 10, 'unit_cost': 100}),
                              Command.create({'product_id': A.id, 'quantity': 10, 'unit_cost': 50})]})
os_.action_post()
check(os_.name.startswith('OS/') and entry(os_.move_id) == {'235100': 1000.0, '235200': 500.0, '409000': -1500.0},
      'opening stock %s: %s' % (os_.name, entry(os_.move_id)))
check(F.with_context(location=wh.lot_stock_id.id).qty_available == 10 and A.standard_price == 50, 'on hand 10, average cost 50')

# 2. GRN then the bill from it at a higher price
grn = SV.create({'voucher_type': 'grn', 'date': D(7, 10), 'partner_id': supplier.id, 'warehouse_id': wh.id, 'reference': 'DN-77',
                 'line_ids': [Command.create({'product_id': F.id, 'quantity': 10, 'unit_cost': 120})]})
grn.action_post()
check(entry(grn.move_id) == {'235100': 1200.0, '305100': -1200.0}, 'GRN %s: Dr inventory / Cr GRNI 1,200' % grn.name)
doc = R.get_report('grni', {'date_to': '2030-07-31'})
check(any(r['values'].get('ref') == grn.name for r in doc['rows']), 'GRN shows as received, not billed')
act = grn.action_create_bill()
bill = V.browse(act['res_id'])
bill.line_ids.write({'price_unit': 125, 'tax_ids': [Command.set(vat.ids)]})
bill.action_post()
grni_open = sum(env['account.move.line'].search([('account_id', '=', Acc('305100').id), ('reconciled', '=', False),
                                                  ('move_id', 'in', (grn.move_id | bill.move_id | bill.sb_cost_move_ids).ids)]).mapped('balance'))
cost = entry(bill.sb_cost_move_ids)
check(grn.billed and abs(grni_open) < 0.01 and cost == {'305100': -50.0, '511200': 50.0} and not bill.sb_stock_move_ids,
      'bill %s at 125 clears GRNI, price difference 50 to 511200, no second receipt' % bill.name)

# 3. cash purchase receives stock at the posted cost (VAT excluded)
cp = V.create({'voucher_type': 'cash_purchase', 'date': D(7, 12), 'partner_id': supplier.id, 'cash_account_id': bank.id,
               'line_ids': [Command.create({'product_id': A.id, 'name': 'Cement', 'quantity': 10, 'price_unit': 70,
                                            'account_id': Acc('621800').id, 'tax_ids': [Command.set(vat.ids)]})]})
cp.action_post()
check(cp.line_ids.account_id == inv_a and abs(A.standard_price - 60) < 0.001,
      'cash purchase goes to inventory (%s); average cost now %s (expected 60)' % (cp.line_ids.account_id.code, A.standard_price))
check(entry(cp.move_id).get('235200') == 700.0, 'inventory debited 700, VAT kept apart')

# 4. cash sale: FIFO cost of sales on the same voucher
cs = V.create({'voucher_type': 'cash_sale', 'date': D(7, 15), 'partner_id': customer.id, 'cash_account_id': bank.id,
               'line_ids': [Command.create({'product_id': F.id, 'name': 'Laptop', 'quantity': 15, 'price_unit': 200,
                                            'account_id': Acc('110000').id, 'tax_ids': [Command.set(vat.ids)]})]})
cs.action_post()
e = entry(cs.move_id)
check(e.get('511100') == 1600.0 and e.get('235100') == -1600.0 and cs.sb_cogs == 1600.0,
      'cash sale %s: FIFO cost 10 x 100 + 5 x 120 = 1,600 (%s)' % (cs.name, e))
try:
    big = V.create({'voucher_type': 'cash_sale', 'date': D(7, 16), 'partner_id': customer.id, 'cash_account_id': bank.id,
                    'line_ids': [Command.create({'product_id': F.id, 'name': 'Laptop', 'quantity': 1000, 'price_unit': 200,
                                                 'account_id': Acc('110000').id})]})
    big.action_post()
    check(False, 'selling more than on hand is refused')
except UserError as ex:
    check('Not enough stock' in str(ex), 'selling more than on hand is refused')

# 5. sales invoice: average cost of sales added by the invoice
si = V.create({'voucher_type': 'sale', 'date': D(7, 18), 'partner_id': customer.id,
               'line_ids': [Command.create({'product_id': A.id, 'name': 'Cement', 'quantity': 5, 'price_unit': 100,
                                            'account_id': Acc('110000').id, 'tax_ids': [Command.set(vat.ids)]})]})
si.action_post()
e = entry(si.move_id)
check(e.get('511100') == 300.0 and e.get('235200') == -300.0, 'sales invoice %s: average cost 5 x 60 = 300 (%s)' % (si.name, e))

# 6. GIV to a department, transfer, stock count
giv = SV.create({'voucher_type': 'giv', 'date': D(7, 20), 'warehouse_id': wh.id, 'department': 'Site A',
                 'line_ids': [Command.create({'product_id': A.id, 'quantity': 3})]})
giv.action_post()
check(entry(giv.move_id) == {'621800': 180.0, '235200': -180.0}, 'GIV %s: 3 x 60 charged to 621800' % giv.name)
trf = SV.create({'voucher_type': 'transfer', 'date': D(7, 21), 'warehouse_id': wh.id, 'warehouse_dest_id': wh2.id,
                 'line_ids': [Command.create({'product_id': F.id, 'quantity': 2})]})
trf.action_post()
check(not trf.move_id and F.with_context(location=wh2.lot_stock_id.id).qty_available == 2, 'transfer: 2 laptops to the site store, no entry')
cnt = SV.create({'voucher_type': 'adjustment', 'date': D(7, 25), 'warehouse_id': wh.id,
                 'line_ids': [Command.create({'product_id': A.id, 'qty_counted': 11})]})
cnt.action_post()
check(cnt.line_ids.qty_on_hand == 12 and cnt.line_ids.quantity == -1 and entry(cnt.move_id) == {'511600': 60.0, '235200': -60.0},
      'count: books 12, counted 11, loss 60 (%s)' % entry(cnt.move_id))
gl_agrees('after documents')

# 7. sales return at the original cost, purchase return with a cost difference
sr = V.create({'voucher_type': 'sale_return', 'date': D(7, 26), 'partner_id': customer.id, 'origin_id': si.id,
               'line_ids': [Command.create({'product_id': A.id, 'name': 'Cement', 'quantity': 1, 'price_unit': 100,
                                            'account_id': Acc('110000').id, 'tax_ids': [Command.set(vat.ids)]})]})
sr.action_post()
e = entry(sr.move_id)
check(e.get('235200') == 60.0 and e.get('511100') == -60.0, 'sales return %s: back at the original cost 60 (%s)' % (sr.name, e))
pr = V.create({'voucher_type': 'purchase_return', 'date': D(7, 27), 'partner_id': supplier.id, 'origin_id': cp.id,
               'line_ids': [Command.create({'product_id': A.id, 'name': 'Cement', 'quantity': 1, 'price_unit': 70,
                                            'tax_ids': [Command.set(vat.ids)]})]})
pr.action_post()
ret = pr.sb_stock_move_ids.filtered(lambda m: not m.sb_reversal_of_id)
check(abs(abs(ret.value) - 70) < 0.01 and not pr.sb_cost_move_ids,
      'purchase return %s leaves at the debit-note price 70, no cost difference (Peachtree)' % pr.name)
gl_agrees('after returns')

# 8. void and edit
before = F.qty_available
cs.action_void()
check(cs.state == 'void' and F.qty_available == before + 15, 'void cash sale: 15 laptops back in stock')
gl_agrees('after void')
try:
    si.action_edit()
    check(False, 'an invoice with a credit note applied cannot be edited')
except UserError:
    check(True, 'an invoice with a credit note applied cannot be edited')
act = cp.action_edit()
copy = V.browse(act['res_id'])
check(cp.state == 'void' and copy.state == 'draft' and copy.line_ids.product_id == A, 'edit cash purchase with stock = void + editable copy')
copy.line_ids.price_unit = 72
copy.action_post()
gl_agrees('after edit')
grn2 = SV.create({'voucher_type': 'grn', 'date': D(8, 1), 'partner_id': supplier.id, 'warehouse_id': wh.id,
                  'line_ids': [Command.create({'product_id': F.id, 'quantity': 4, 'unit_cost': 130})]})
grn2.action_post()
grn2.action_void()
check(grn2.state == 'void', 'void GRN')
gl_agrees('after GRN void')

# 10. same-day receipt rule (average cost): receipts dated the same day count before issues and sales
Z = env['product.product'].create({'name': 'Test Paint (AVCO)', 'default_code': 'T-PNT', 'categ_id': avco_c.id, 'is_storable': True, 'type': 'consu'})
SV.create({'voucher_type': 'opening', 'date': D(9, 1), 'line_ids': [Command.create({'product_id': Z.id, 'quantity': 10, 'unit_cost': 50})]}).action_post()
painter = env['res.partner'].create({'name': 'Paint Buyer PLC', 'customer_rank': 1})
sale = V.create({'voucher_type': 'cash_sale', 'date': D(9, 5), 'partner_id': painter.id, 'cash_account_id': bank.id,
                 'line_ids': [Command.create({'product_id': Z.id, 'name': 'Paint', 'quantity': 5, 'price_unit': 120, 'account_id': Acc('110000').id})]})
sale.action_post()
check(abs(sale.sb_cogs - 250) < 0.01, 'sale before the receipt is entered: 5 x 50 = 250')
SV.create({'voucher_type': 'grn', 'date': D(9, 5), 'partner_id': supplier.id,
           'line_ids': [Command.create({'product_id': Z.id, 'quantity': 10, 'unit_cost': 80})]}).action_post()
sale.invalidate_recordset()
check(abs(sale.sb_cogs - 325) < 0.01, 'same-day receipt entered later: the sale is re-costed at (500 + 800) / 20 = 65, cost 325 (%s)' % sale.sb_cogs)
rec = env['account.move'].search([('ref', '=', 'Cost recalculation (same-day receipt rule)'), ('date', '=', D(9, 5))])
check(rec and entry(rec[-1:]).get('511100') == 75.0, 'difference 75 posted on the sale date to cost of sales')
SV.create({'voucher_type': 'grn', 'date': D(9, 20), 'partner_id': supplier.id,
           'line_ids': [Command.create({'product_id': Z.id, 'quantity': 10, 'unit_cost': 200})]}).action_post()
back = SV.create({'voucher_type': 'giv', 'date': D(9, 10), 'line_ids': [Command.create({'product_id': Z.id, 'quantity': 2})]})
back.action_post()
check(abs(back.amount_total - 130) < 0.01, 'issue dated 10 Sep ignores the 20 Sep receipt: 2 x 65 = 130 (%s)' % back.amount_total)
gl_agrees('after same-day re-costing')

# 11. adjustment reasons and the Sage cost rule
env['sb.adjust.reason']._sb_defaults(company)
dam = env['sb.adjust.reason'].search([('code', '=', 'DAM'), ('company_id', '=', company.id)])
dam.account_id = Acc('511400')
W = env['product.product'].create({'name': 'Test Wire (AVCO)', 'default_code': 'T-WIR', 'categ_id': avco_c.id, 'is_storable': True, 'type': 'consu'})
SV.create({'voucher_type': 'opening', 'date': D(10, 1), 'line_ids': [Command.create({'product_id': W.id, 'quantity': 10, 'unit_cost': 40})]}).action_post()
SV.create({'voucher_type': 'grn', 'date': D(10, 20), 'partner_id': supplier.id,
           'line_ids': [Command.create({'product_id': W.id, 'quantity': 10, 'unit_cost': 100})]}).action_post()
loss = SV.create({'voucher_type': 'adjustment', 'date': D(10, 10), 'warehouse_id': wh.id, 'reason_id': dam.id})
loss._onchange_reason()
line = env['sb.stock.voucher.line'].new({'voucher_id': loss.id, 'product_id': W.id, 'quantity': -2})
line._onchange_product()
line._onchange_adjust_cost()
check(line.cost_locked and abs(line.unit_cost - 40) < 1e-6, 'loss: unit cost locked, shows the average on 10 Oct (40), not today\'s 70')
loss.write({'line_ids': [Command.create({'product_id': W.id, 'quantity': -2, 'unit_cost': 999})]})
loss.action_post()
check(loss.name.startswith('DAM/') and loss.account_id.code == '511400' and abs(loss.line_ids.value + 80) < 0.01,
      'damage %s: numbered by reason, charged to its account, valued 2 x 40 despite a typed 999' % loss.name)
gain = SV.create({'voucher_type': 'adjustment', 'date': D(10, 25), 'warehouse_id': wh.id, 'is_count': False,
                  'line_ids': [Command.create({'product_id': W.id, 'quantity': 3})]})
gain.action_post()
check(abs(gain.line_ids.value - 3 * (400 - 80 + 1000) / 18) < 0.02, 'gain with no cost entered: average on 25 Oct (%.2f x 3)' % ((400 - 80 + 1000) / 18))
gain2 = SV.create({'voucher_type': 'adjustment', 'date': D(10, 26), 'warehouse_id': wh.id, 'is_count': False,
                   'line_ids': [Command.create({'product_id': W.id, 'quantity': 1, 'unit_cost': 55})]})
gain2.action_post()
check(abs(gain2.line_ids.value - 55) < 0.01, 'gain with a cost entered: 55 used')
gl_agrees('after reason adjustments')

# 9. reports
val = R.get_report('inv_valuation', {'date_to': '2030-12-31'})
lines = {r['values']['code']: r['values'] for r in val['rows'] if r['kind'] == 'line'}
check(abs(lines['T-LAP']['qty'] - F.qty_available) < 1e-6 and abs(lines['T-CEM']['qty'] - A.qty_available) < 1e-6,
      'valuation quantities match on hand (laptop %s, cement %s)' % (lines['T-LAP']['qty'], lines['T-CEM']['qty']))
mov = R.get_report('stock_movement', {'date_from': '2030-07-01', 'date_to': '2030-12-31', 'search': 'T-'})
ml = {r['values']['code']: r['values'] for r in mov['rows'] if r['kind'] == 'line'}
check(abs(ml['T-LAP']['close_value'] - lines['T-LAP']['value']) < 0.01, 'movement summary closing value = valuation')
card = R.get_report('stock_card', {'date_from': '2030-07-01', 'date_to': '2030-12-31', 'product_ids': [F.id]})
det = [r for r in card['rows'] if r['kind'] == 'detail' and r['values'].get('date')]
check(len(det) >= 6 and abs(det[-1]['values']['bal_qty'] - F.qty_available) < 1e-6, 'bin card: %d movements, running balance ends on hand' % len(det))
cogs = R.get_report('cogs_journal', {'date_from': '2030-07-01', 'date_to': '2030-12-31', 'search': 'Test Customer'})
t = next(r for r in cogs['rows'] if r['id'] == 'total')['values']
live = V.search([('partner_id', '=', customer.id), ('state', '=', 'posted'), ('voucher_type', 'in', ('cash_sale', 'sale', 'sale_return'))])
expected = sum(abs(sum(v.sb_stock_move_ids.filtered(lambda m: not m.sb_reversal_of_id).mapped('value'))) * (-1 if v.voucher_type == 'sale_return' else 1)
               for v in live)
check(abs(t['sales'] - 400) < 0.01 and abs(t['cost'] - expected) < 0.01,
      'COGS journal (voids left out): sales %s, cost %s (the 72 purchase correction flowed into later sales), margin %s%%'
      % (t['sales'], t['cost'], round(t['pct'], 1)))
wh_doc = R.get_report('stock_by_wh', {'date_to': '2030-12-31', 'search': 'T-LAP'})
check(any(r['values'].get('w%d' % wh2.id) == 2 for r in wh_doc['rows']), 'stock by warehouse shows the site store')
ro = R.get_report('reorder', {'search': 'T-LAP'})
check(any(r['values'].get('code') == 'T-LAP' for r in ro['rows']), 'reorder worksheet lists the laptop (below 20)')
cat = R.get_catalog()
avail = {r['key'] for r in cat['reports'] if r['priority'] == 1}
check({'inv_valuation', 'stock_card', 'cogs_journal', 'stock_movement', 'count_sheet', 'inv_gl_rec'} <= avail, 'inventory reports live in the Report Center')
for key in ('item_costing', 'price_list', 'grn_register', 'giv_register', 'adj_journal', 'count_sheet', 'grni'):
    d = R.get_report(key, {'date_from': '2030-07-01', 'date_to': '2030-12-31'})
    check(d['rows'] is not None, '%s renders (%d rows)' % (key, len(d['rows'])))
xlsx = R.render_xlsx([val])
check(len(xlsx) > 1000, 'valuation exports to Excel')

print('RESULT %s (%d/%d)' % ('ALL PASS' if all(ok) else 'FAILURES', sum(ok), len(ok)))
env.cr.rollback()
