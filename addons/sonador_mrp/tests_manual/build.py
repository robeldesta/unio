# Build vouchers on Eco Green data: mix liquid (recipe version by date), fill 5 L Aquapure, costs, entries, void. Rolled back.
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


c = env.company
c.fiscalyear_lock_date = False
P = env['product.product']
item = lambda code: P.search([('default_code', '=', code)], limit=1)  # noqa: E731
SV, R = env['sb.stock.voucher'], env['sb.report']
liquid, aqua = item('LIQ-BULK'), item('1504-3001')
gl_before = next(r for r in R.get_report('inv_gl_rec', {'date_to': '2026-12-31'})['rows'] if r['id'] == 'total')['values']['diff']

check(env['mrp.bom']._sb_for(liquid, date(2026, 1, 10)).code.startswith('Formula A') and
      env['mrp.bom']._sb_for(liquid, date(2026, 8, 10)).code.startswith('Formula B'), 'recipe version picked by date (A before 17 Feb 2026, B after)')

mix = SV.create({'voucher_type': 'build', 'date': date(2026, 8, 10), 'build_product_id': liquid.id, 'build_qty': 1000, 'reference': 'Batch 1'})
mix._onchange_build_product()
mix.action_load_recipe()
nn1 = mix.line_ids.filtered(lambda l: l.product_id.default_code == '1501-0001')
check(len(mix.line_ids) == 10 and abs(nn1.qty_standard - 80) < 1e-6, 'mix 1,000 L: 10 raw materials, NN1 80 kg (0.08 / L)')
nn1.quantity = 84  # a little more used than the recipe
mix.action_post()
check(mix.state == 'posted' and mix.name.startswith('PB/') and mix.production_id.state == 'done', 'mix posted %s on manufacturing order %s' % (mix.name, mix.production_id.name))
check(abs(liquid.with_context(location=mix.warehouse_id.lot_stock_id.id).qty_available - 1000) < 1e-6, '1,000 L liquid in stock')
e = entry(mix.move_id)
check(abs(e.get('1505-0000', 0) - mix.build_value) < 0.01 and abs(e.get('1501-0000', 0) + mix.material_value) < 0.01 and not mix.labour_value,
      'mix entry: Dr WIP %s / Cr raw materials %s' % (e.get('1505-0000'), e.get('1501-0000')))
check(abs(nn1.qty_variance - 4) < 1e-6, 'usage variance recorded: NN1 +4 kg')

supplier = env['res.partner'].create({'name': 'Test Packaging Supplier', 'supplier_rank': 1})
SV.create({'voucher_type': 'grn', 'date': date(2026, 8, 10), 'partner_id': supplier.id,
           'line_ids': [Command.create({'product_id': item('1503-1001').id, 'quantity': 50, 'unit_cost': 85})]}).action_post()
fill = SV.create({'voucher_type': 'build', 'date': date(2026, 8, 11), 'build_product_id': aqua.id, 'build_qty': 500})
fill._onchange_build_product()
fill.action_load_recipe()
jer = fill.line_ids.filtered(lambda l: l.product_id.default_code == '1501-1001')
check(abs(jer.qty_standard - 100) < 1e-6 and len(fill.line_ids) == 7 and fill.build_kind == 'fprr', 'fill 500 L Aquapure is an FPRR: 100 jerricans, 7 components')
fill.action_post()
check(fill.name.startswith('FPRR/'), 'numbered %s' % fill.name)
e = entry(fill.move_id)
lab, moh = 500 * 2.1890, 500 * 2.6552
check(abs(fill.labour_value - lab) < 0.01 and abs(fill.overhead_value - moh) < 0.01, 'labour %.2f and overhead %.2f absorbed' % (lab, moh))
check(abs(fill.build_value - (fill.material_value + lab + moh)) < 0.02 and abs(e.get('1504-0000', 0) - fill.build_value) < 0.01,
      'finished goods %s = materials %s + labour + overhead (%s per litre)' % (round(fill.build_value, 2), round(fill.material_value, 2), round(fill.build_unit_cost, 4)))
check(abs(e.get('511510', 0) + lab) < 0.01 and abs(e.get('511520', 0) + moh) < 0.01 and abs(e.get('1505-0000', 0) + 500 * mix.build_unit_cost) < 1,
      'fill entry credits WIP (liquid), packaging, labour and overhead absorbed')
try:
    SV.create({'voucher_type': 'build', 'date': date(2026, 8, 12), 'build_product_id': aqua.id, 'build_qty': 10 ** 7}).action_post()
    check(False, 'building without enough components is refused')
except UserError:
    check(True, 'building without enough components is refused')
gl_after = next(r for r in R.get_report('inv_gl_rec', {'date_to': '2026-12-31'})['rows'] if r['id'] == 'total')['values']['diff']
check(abs(gl_after - gl_before) < 0.01, 'stock and GL still agree after builds (difference unchanged %s)' % round(gl_after, 2))

doc = R.get_report('cost_sheet', {'date_from': '2026-07-08', 'date_to': '2026-12-31'})
line = next(r for r in doc['rows'] if r['id'] == 'p%d' % aqua.id)['values']
check(abs(line['total_u'] - fill.build_unit_cost) < 0.001 and abs(line['labour_u'] - 2.189) < 0.001, 'cost sheet: Aquapure %.4f per litre' % line['total_u'])
mu = R.get_report('material_usage', {'date_from': '2026-07-08', 'date_to': '2026-12-31'})
check(any(r['values'].get('item', '').endswith('NN1 Raw Material') and abs(r['values']['var'] - 4) < 1e-6 for r in mu['rows'] if r['kind'] == 'line'),
      'material usage report shows the NN1 variance')
for key in ('production_register', 'recipe_list'):
    d = R.get_report(key, {'date_from': '2026-07-08', 'date_to': '2026-12-31'})
    check(len(d['rows']) > 1, '%s renders (%d rows)' % (key, len(d['rows'])))
cats = {x['key'] for x in R.get_catalog()['categories']}
check('production' in cats, 'Production section in the Report Center')

fill.action_void()
check(fill.state == 'void' and abs(aqua.with_context(location=fill.warehouse_id.lot_stock_id.id).qty_available
                                   - aqua.with_context(location=fill.warehouse_id.lot_stock_id.id, to_date='2026-08-10 23:59:59').qty_available) < 1e-6,
      'void fill: finished goods out, components back')
gl_void = next(r for r in R.get_report('inv_gl_rec', {'date_to': '2026-12-31'})['rows'] if r['id'] == 'total')['values']['diff']
check(abs(gl_void - gl_before) < 0.01, 'stock and GL agree after the void')
fp = SV.create({'voucher_type': 'fprr', 'date': date(2026, 8, 12), 'reference': 'Shift 2',
                'line_ids': [Command.create({'product_id': aqua.id, 'quantity': 200, 'unit_cost': 40}),
                             Command.create({'product_id': item('1506-3003').id, 'quantity': 100, 'unit_cost': 38})]})
fp._onchange_default_account()
fp.action_post()
e = entry(fp.move_id)
check(fp.name.startswith('FPRR/') and e == {'1504-0000': 11800.0, '1505-0000': -11800.0},
      'manual FPRR %s: 2 products, Dr finished goods / Cr WIP 11,800 (%s)' % (fp.name, e))
hist = SV.search([('voucher_type', '=', 'fprr'), ('sb_migrated', '=', True)], limit=1)
try:
    hist.action_void()
    check(False, 'converted Peachtree FPRRs cannot be voided')
except UserError:
    check(True, 'converted Peachtree FPRRs cannot be voided (%s)' % hist.name)
gl_fp = next(r for r in R.get_report('inv_gl_rec', {'date_to': '2026-12-31'})['rows'] if r['id'] == 'total')['values']['diff']
check(abs((gl_fp - gl_before) - 11800) < 0.01, 'FPRR without a recipe draws on un-itemised WIP: the GL-vs-stock gap moves by exactly 11,800')
print('RESULT %s (%d/%d)' % ('ALL PASS' if all(ok) else 'FAILURES', sum(ok), len(ok)))
env.cr.rollback()
