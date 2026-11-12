"""Production flow stress test. Every posting is checked against the rules, from the journal. Rolls back.

The rules (owner, 12 Nov 2026):
  GIV against a production order:   Dr Work in Progress / Cr Inventory (raw materials), at average cost (AVCO)
  FPRR (the floor's "Finish and post"):
                                    Dr Finished Goods / Cr WIP (that order's GIVs) · Cr labour absorbed · Cr overhead absorbed
  Raw materials leave stock once, on the GIV. Each order's FPRR takes its own GIVs only.
Run: odoo shell -d <db> < addons/sonador_factory/tests_manual/production_flow.py"""
from odoo import Command
from odoo.tests import Form

results = []
def ok(cond, msg):
    results.append(bool(cond))
    print(('PASS ' if cond else 'FAIL ') + msg)

env = env(user=env.ref('base.user_admin'))
c = env.company
SV, Cat, Acc, AML = env['sb.stock.voucher'], env['product.category'], env['account.account'], env['account.move.line']
RMC = Cat.search([('name', 'ilike', 'Raw Materials')], limit=1)
FGC = Cat.search([('name', 'ilike', 'Finished Goods')], limit=1)
RM, FG = RMC.property_stock_valuation_account_id, FGC.property_stock_valuation_account_id
WIP = c.account_production_wip_account_id
LAB, OVH = c.sb_labour_absorbed_account_id, c.sb_overhead_absorbed_account_id
print('accounts: raw materials %s · WIP %s · finished goods %s · labour %s · overhead %s' % (
    RM.code, WIP.code, FG.code, LAB.code, OVH.code))
ok(WIP and WIP.code.startswith('2411'), 'company has Work in Progress as its production WIP account: %s' % (WIP.display_name or 'none'))
ok(c.sb_fprr_cost_source == 'wip', 'FPRRs cost from work in process by default: %s' % c.sb_fprr_cost_source)


def item(name, categ, price=0, sale=False):
    return env['product.product'].create({'name': name, 'type': 'consu', 'is_storable': True, 'categ_id': categ.id,
                                          'standard_price': price, 'sale_ok': sale})


def entry(move):
    out = {}
    for l in move.line_ids:
        d, cr = out.get(l.account_id.code, (0.0, 0.0))
        out[l.account_id.code] = (round(d + l.debit, 2), round(cr + l.credit, 2))
    return {k: v for k, v in out.items() if v != (0.0, 0.0)}


def want(*rows):
    out = {}
    for acc, d, cr in rows:
        if round(d, 2) or round(cr, 2):
            od, oc = out.get(acc.code, (0.0, 0.0))
            out[acc.code] = (round(od + d, 2), round(oc + cr, 2))
    return out


def check(move, expected, label):
    got = entry(move) if move else {}
    ok(got == expected, '%s: %s' % (label, 'ok' if got == expected else 'got %s, rules say %s' % (got, expected)))


def bal(account):
    return round(sum(AML.search([('account_id', '=', account.id), ('parent_state', '=', 'posted')]).mapped('balance')), 2)


def on_hand(p):
    return env['sb.stock']._on_hand(p, env['stock.warehouse'].search([('company_id', '=', c.id)], limit=1).lot_stock_id)


start = {a: bal(a) for a in (RM, WIP, FG)}

# ------------------------------------------------------------------ materials bought at two prices
steel = item('Prod steel sheet', RMC)
paint = item('Prod paint tin', RMC)
door = item('Prod steel door', FGC, sale=True)
sup = env['res.partner'].create({'name': 'Prod Supplier', 'supplier_rank': 1})
for p, qty, cost in ((steel, 10, 1000), (steel, 10, 1300), (paint, 10, 400)):
    g = SV.create({'voucher_type': 'grn', 'partner_id': sup.id, 'line_ids': [Command.create({'product_id': p.id, 'quantity': qty, 'unit_cost': cost})]})
    g.action_post()
ok(round(steel.standard_price, 2) == 1150, 'steel at average cost 1,150 after buying at 1,000 and 1,300: %s' % steel.standard_price)

# ------------------------------------------------------------------ two orders on the floor at the same time
line = env['sb.mfg.line'].create({'name': 'Prod press line', 'labour_rate': 300, 'overhead_rate': 200})
bom = env['mrp.bom'].create({'product_tmpl_id': door.product_tmpl_id.id, 'product_qty': 1, 'bom_line_ids': [
    Command.create({'product_id': steel.id, 'product_qty': 1.2}), Command.create({'product_id': paint.id, 'product_qty': 0.4})]})
O = env['sb.mfg.order']
A = O.create({'product_id': door.id, 'bom_id': bom.id, 'qty_planned': 5, 'line_id': line.id, 'state': 'running'})
B = O.create({'product_id': door.id, 'bom_id': bom.id, 'qty_planned': 3, 'line_id': line.id, 'state': 'running'})


def issue(order, qtys, label, user=None):
    """The store issues materials for a production order (as from the GIV form, with its onchanges)."""
    f = Form((SV.with_user(user) if user else SV).with_context(default_voucher_type='giv'))
    f.department = 'Production'
    f.sb_mfg_order_id = order
    for p, q in qtys.items():
        found = False
        for i in range(len(f.line_ids)):
            with f.line_ids.edit(i) as l:
                if l.product_id == p:
                    l.quantity = q
                    found = True
        if not found:
            with f.line_ids.new() as l:
                l.product_id = p
                l.quantity = q
    for i in reversed(range(len(f.line_ids))):
        with f.line_ids.edit(i) as l:
            if l.product_id not in qtys:
                l.quantity = 0
    g = f.save()
    g.line_ids.filtered(lambda l: not l.quantity).unlink()
    g.action_post()
    value = round(sum(qtys[p] * p.standard_price for p in qtys), 2)
    return g


g_a = issue(A, {steel: 6, paint: 2}, 'GIV for order A')
check(g_a.move_id, want((WIP, 7700, 0), (RM, 0, 7700)), 'GIV A: 6 steel × 1,150 + 2 paint × 400 → Dr WIP / Cr raw materials')
clerk = env['res.users'].create({'name': 'Prod Store Clerk', 'login': 'prod-clerk@test.local',
                                 'group_ids': [Command.set([env.ref('base.group_user').id, env.ref('sonador_inventory.group_sb_store_user').id])]})
try:
    g_b = issue(B, {steel: 4}, 'GIV for order B', user=clerk)
    ok(True, 'the store clerk picks the production order and posts the GIV')
except Exception as e:
    ok(False, 'the store clerk could not issue for the order: %s' % str(e).splitlines()[0][:150])
    g_b = issue(B, {steel: 4}, 'GIV for order B')
check(g_b.move_id, want((WIP, 4600, 0), (RM, 0, 4600)), 'GIV B: 4 steel × 1,150')
ok(g_a.line_ids.mapped('account_id') <= WIP or g_a.account_id == WIP, 'the GIV form shows WIP as what it charges')


def finish(order, minutes, good, label):
    env['sb.mfg.entry'].create({'order_id': order.id, 'minutes_run': minutes, 'good_qty': good})
    try:
        env['sb.factory'].finish(order.id, use_expected=True)
    except Exception as e:
        ok(False, '%s: not finished (%s)' % (label, str(e).splitlines()[0][:160]))
        return None
    order.invalidate_recordset()
    return order.voucher_id


v_a = finish(A, 120, 5, 'finish A')
if v_a:
    ok(v_a.voucher_type == 'fprr', 'A is finished on an FPRR (not a Build): %s' % v_a.voucher_type)
    check(v_a.move_id, want((FG, 8700, 0), (WIP, 0, 7700), (LAB, 0, 600), (OVH, 0, 400)),
          'FPRR A: Dr FG 8,700 / Cr WIP 7,700 (A\'s GIV only) · labour 2 h × 300 · overhead 2 h × 200')
ok(round(bal(WIP) - start[WIP], 2) == 4600, 'WIP after A holds only B\'s materials: %s' % round(bal(WIP) - start[WIP], 2))
v_b = finish(B, 60, 3, 'finish B')
if v_b:
    check(v_b.move_id, want((FG, 5100, 0), (WIP, 0, 4600), (LAB, 0, 300), (OVH, 0, 200)), 'FPRR B: Dr FG 5,100')
ok(round(bal(WIP) - start[WIP], 2) == 0, 'WIP empty when both orders are finished: %s' % round(bal(WIP) - start[WIP], 2))

# ------------------------------------------------------------------ stock moved once
ok(on_hand(steel) == 10 and on_hand(paint) == 8, 'raw materials left: steel %s (20 − 6 − 4), paint %s (10 − 2): taken once, on the GIVs' % (on_hand(steel), on_hand(paint)))
ok(round(bal(RM) - start[RM], 2) == 14700, 'raw materials in the books = what is left: 10 × 1,150 + 8 × 400 = 14,700; books %s' % round(bal(RM) - start[RM], 2))
ok(on_hand(door) == 8 and round(bal(FG) - start[FG], 2) == 13800, 'doors: %s in stock, %s in the books (rules 8 and 13,800)' % (on_hand(door), round(bal(FG) - start[FG], 2)))

# ------------------------------------------------------------------ nothing issued
C = O.create({'product_id': door.id, 'bom_id': bom.id, 'qty_planned': 2, 'line_id': line.id, 'state': 'running'})
env['sb.mfg.entry'].create({'order_id': C.id, 'minutes_run': 30, 'good_qty': 2})
try:
    env['sb.factory'].finish(C.id, use_expected=True)
    C.invalidate_recordset()
    ok(False, 'an order with no materials issued was finished (%s)' % C.voucher_id.name)
except Exception as e:
    msg = str(e).splitlines()[0]
    ok('issue' in msg.lower() or 'GIV' in msg, 'an order with no materials issued is refused, saying why: "%s"' % msg[:150])

# ------------------------------------------------------------------ an item saved without a class
f = Form(env['product.template'])
f.name = 'Prod bought item, no class chosen'
f.sale_ok = False
f.purchase_ok = True
t = f.save()
ok(t.categ_id == RMC, 'a bought-only item gets the Raw Materials class by itself: %s' % (t.categ_id.display_name or 'none'))

print('\n%d of %d checks pass' % (sum(results), len(results)))
env.cr.rollback()
