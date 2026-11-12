# Sales flow on Eco Green data. Rolled back.
from datetime import date, datetime
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


c = env.company
c.fiscalyear_lock_date = False
P = env['product.product']
item = lambda code: P.search([('default_code', '=', code)], limit=1)  # noqa: E731
aqua, one = item('1504-3001'), item('1504-1001')
wh = env['stock.warehouse'].search([('company_id', '=', c.id)], limit=1)
stock = wh.lot_stock_id
avail = lambda p: p.with_context(location=stock.id).qty_available  # noqa: E731
R = env['sb.report']
gl0 = next(r for r in R.get_report('inv_gl_rec', {'date_to': '2026-12-31'})['rows'] if r['id'] == 'total')['values']['diff']
cust = env['res.partner'].create({'name': 'Test Farm Union', 'customer_rank': 1})
a0, o0 = avail(aqua), avail(one)

so = env['sale.order'].create({'partner_id': cust.id, 'warehouse_id': wh.id, 'order_line': [
    Command.create({'product_id': aqua.id, 'product_uom_qty': 1000, 'price_unit': 50, 'tax_ids': [Command.clear()]}),
    Command.create({'product_id': one.id, 'product_uom_qty': 300, 'price_unit': 110, 'tax_ids': [Command.clear()]})]})
so.action_confirm()
check(so.state == 'sale' and not so.picking_ids, 'order %s confirmed, no Odoo delivery picking created' % so.name)
check(so.sb_status == 'to_invoice' and avail(aqua) == a0, 'nothing moved on confirmation')

inv = env['sb.voucher'].browse(so.action_sb_invoice()['res_id'])
check(inv.voucher_type == 'sale' and inv.sb_deliver_later and len(inv.line_ids) == 2, 'invoice drafted from the order (deliver later)')
inv.action_post()
e = entry(inv.move_id)
check(abs(avail(aqua) - (a0 - 1000)) < 1e-6 and abs(avail(one) - (o0 - 300)) < 1e-6, 'invoice posted %s: goods no longer available' % inv.name)
disp = wh.sb_dispatch_location_id
check(disp.usage == 'customer' and abs(sum(env['stock.move'].search([('location_dest_id', '=', disp.id), ('product_id', '=', aqua.id),
      ('state', '=', 'done')]).mapped('quantity')) - 1000) < 1e-6, '1,000 L of Aquapure moved to "Awaiting dispatch"')
check(e.get('5000-0007', 0) > 0 and e.get('4000-0001', 0) == -83000.0, 'revenue 83,000 and cost of sales %s on the invoice' % e.get('5000-0007'))
do = inv.sb_delivery_id
lines = {l.product_id: l for l in do.line_ids}
check(do and do.name.startswith('DO/') and lines[aqua].qty_remaining == 1000 and lines[one].qty_remaining == 300,
      'delivery order %s opened: 1,000 + 300 to deliver' % do.name)
env.invalidate_all()
check(so.sb_status == 'to_deliver' and so.order_line[0].sb_qty_invoiced == 1000, 'order: invoiced, to deliver')

b1 = env['sb.delivery.batch'].browse(do.action_new_batch()['res_id'])
b1.line_ids.filtered(lambda l: l.product_id == aqua).quantity = 600
b1.write({'vehicle': '3-A12345 AA', 'driver': 'Test Driver', 'loaded_time': datetime(2026, 10, 18, 8), 'gate_time': datetime(2026, 10, 18, 9)})
b1.action_validate()
env.invalidate_all()
lines = {l.product_id: l for l in do.line_ids}
check(b1.state == 'done' and lines[aqua].qty_remaining == 400 and lines[one].qty_remaining == 0, 'batch %s: 600 + 300 delivered, 400 left' % b1.name)
b2 = env['sb.delivery.batch'].create({'delivery_id': do.id, 'vehicle': 'X', 'line_ids': [Command.create({'product_id': aqua.id, 'quantity': 500})]})
try:
    b2.action_validate()
    check(False, 'loading more than invoiced is refused')
except UserError:
    check(True, 'loading more than invoiced is refused')
try:
    inv.action_void()
    check(False, 'invoice with goods delivered cannot be voided')
except UserError:
    check(True, 'invoice with goods delivered cannot be voided')
bad = env['sb.delivery.batch'].create({'delivery_id': do.id, 'vehicle': 'X', 'loaded_time': datetime(2026, 10, 18, 10), 'gate_time': datetime(2026, 10, 18, 9),
                                       'line_ids': [Command.create({'product_id': aqua.id, 'quantity': 10})]})
try:
    bad.action_validate()
    check(False, 'gate exit before loading is refused')
except Exception:
    check(True, 'gate exit before loading is refused')
b2.line_ids.quantity = 400
b2.action_validate()
env.invalidate_all()
env.invalidate_all()
check(do.state == 'done' and so.sb_status == 'done', 'second batch: everything delivered, order done')

# partial invoicing and a cash sale for the rest
so2 = env['sale.order'].create({'partner_id': cust.id, 'warehouse_id': wh.id, 'order_line': [
    Command.create({'product_id': aqua.id, 'product_uom_qty': 100, 'price_unit': 50, 'tax_ids': [Command.clear()]})]})
so2.action_confirm()
i2 = env['sb.voucher'].browse(so2.action_sb_invoice()['res_id'])
i2.line_ids.quantity = 60
i2.action_post()
env.invalidate_all()
check(abs(so2.order_line.sb_qty_to_invoice - 40) < 1e-6, 'partial invoice 60 of 100: 40 left to invoice')
bank = env['account.account'].search([('code', '=', '1104-0002')], limit=1)
cs = env['sb.voucher'].browse(so2.action_sb_cash_sale()['res_id'])
cs.cash_account_id = bank
cs.action_post()
env.invalidate_all()
check(cs.voucher_type == 'cash_sale' and so2.order_line.sb_qty_to_invoice == 0 and cs.sb_delivery_id == i2.sb_delivery_id,
      'cash sale for the remaining 40 joins the same delivery order')
# void before delivery: goods come back
before = avail(aqua)
i2.action_void()
check(abs(avail(aqua) - (before + 60)) < 1e-6, 'void before delivery: 60 L back in available stock')
env.invalidate_all()
check(abs(i2.sb_delivery_id.line_ids.qty_remaining - 40) < 1e-6, 'delivery order now expects 40 (the cash sale)')

# counter sale: handed over at once
before = avail(aqua)
cnt = env['sb.voucher'].create({'voucher_type': 'cash_sale', 'partner_id': cust.id, 'cash_account_id': bank.id, 'sb_warehouse_id': wh.id,
                                'line_ids': [Command.create({'product_id': aqua.id, 'name': 'Aquapure', 'quantity': 10, 'price_unit': 50,
                                                             'account_id': env['account.account'].search([('code', '=', '4000-0001')]).id})]})
cnt.action_post()
check(not cnt.sb_delivery_id and abs(avail(aqua) - (before - 10)) < 1e-6, 'counter cash sale: straight to the customer, no delivery order')

gl1 = next(r for r in R.get_report('inv_gl_rec', {'date_to': '2026-12-31'})['rows'] if r['id'] == 'total')['values']['diff']
check(abs(gl1 - gl0) < 0.01, 'stock and GL still in step (gap unchanged %s -> %s)' % (round(gl0, 2), round(gl1, 2)))
und = R.get_report('undelivered', {})
check(any(r['values'].get('who') == cust.name for r in und['rows']), 'Invoiced-not-delivered report lists the open 40 L')
reg = R.get_report('delivery_register', {'date_from': '2026-07-08', 'date_to': '2027-07-07'})
check(sum(1 for r in reg['rows'] if r['kind'] == 'group') >= 2, 'delivery register shows the batches with vehicle and times')
oo = R.get_report('open_orders', {})
check(oo['rows'] is not None, 'open sales orders report renders')
pdf, _ = env['ir.actions.report']._render_qweb_pdf('sonador_sales.action_report_delivery_note', b1.ids)
check(len(pdf) > 1000, 'delivery note prints')

# price levels: level + channel + distance band + quantity tier, equivalent items counted together
std = env['sb.price.level'].create({'name': 'Test Standard'})
agent = env['sb.price.level'].create({'name': 'Test Agent'})
avon = item('1506-3003')
grp = env['sb.price.group'].create({'name': 'Test 5 L', 'product_ids': [Command.set((aqua | avon).ids)]})
PR = env['sb.price.rule']
PR.create({'group_id': grp.id, 'level_id': std.id, 'channel': 'company', 'distance_max': 100, 'price': 60})
PR.create({'group_id': grp.id, 'level_id': std.id, 'channel': 'company', 'distance_min': 100.01, 'price': 64})
PR.create({'group_id': grp.id, 'level_id': std.id, 'channel': 'company', 'distance_max': 100, 'qty_min': 5000, 'price': 57})
PR.create({'product_id': aqua.id, 'level_id': agent.id, 'price': 52})
PR.create({'group_id': grp.id, 'channel': 'pickup', 'price': 55})
cust.write({'sb_price_level_id': std.id, 'sb_distance_km': 40})
q = env['sale.order'].create({'partner_id': cust.id, 'warehouse_id': wh.id, 'order_line': [Command.create({'product_id': aqua.id, 'product_uom_qty': 1000})]})
check(q.sb_price_level_id == std and q.order_line.price_unit == 60, 'standard, company delivery, 40 km: 60 (%s)' % q.order_line.price_unit)
q.sb_distance_km = 150
check(q.order_line.price_unit == 64, 'over 100 km: 64 (%s)' % q.order_line.price_unit)
q.sb_distance_km = 40
q.order_line = [Command.create({'product_id': avon.id, 'product_uom_qty': 4500})]
q.action_sb_update_prices()
check(all(l.price_unit == 57 for l in q.order_line), '1,000 Aquapure + 4,500 AVON count together: 5,000+ tier 57 on both')
q.sb_channel = 'pickup'
q.action_sb_update_prices()
check(all(l.price_unit == 55 for l in q.order_line), 'customer pick-up: 55')
q.sb_price_level_id = agent
q.sb_channel = 'company'
q.action_sb_update_prices()
aq_line = q.order_line.filtered(lambda l: l.product_id == aqua)
check(aq_line.price_unit == 52 and aq_line.sb_price_rule_id.level_id == agent, 'agent level: 52 for Aquapure')

# credit limit: block by default, warn when set to warn
lim = env['res.partner'].create({'name': 'Test Small Shop', 'customer_rank': 1})
lim.credit_limit = 10000
big = env['sale.order'].create({'partner_id': lim.id, 'warehouse_id': wh.id, 'order_line': [Command.create({'product_id': aqua.id, 'product_uom_qty': 500, 'price_unit': 50})]})
try:
    big.action_confirm()
    check(False, 'order over the credit limit is blocked')
except UserError as ex:
    check('credit limit' in str(ex), 'order over the credit limit is blocked (25,000 > 10,000)')
c.sb_credit_check = 'warn'
big.action_confirm()
check(big.state == 'sale' and any('credit limit' in (m.body or '') for m in big.message_ids), 'warn mode: confirmed, warning in the chatter')
c.sb_credit_check = 'block'
small = env['sale.order'].create({'partner_id': lim.id, 'warehouse_id': wh.id, 'order_line': [Command.create({'product_id': aqua.id, 'product_uom_qty': 10, 'price_unit': 50})]})
try:
    small.action_confirm()
    check(False, 'open orders count toward the limit')
except UserError:
    check(True, 'open (uninvoiced) orders count toward the limit')
print('RESULT %s (%d/%d)' % ('ALL PASS' if all(ok) else 'FAILURES', sum(ok), len(ok)))
env.cr.rollback()
