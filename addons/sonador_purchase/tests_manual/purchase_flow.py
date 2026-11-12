"""Purchase flow stress test. Every posting is checked against the rules, from the journal. Rolls back.

The rules (12 Nov 2026):
  Paid first
    CPV against the order:   Dr Advance (goods) · Dr Input VAT / Cr Bank
    GRN:                     Dr Inventory / Cr Advance, up to the order's open advance
  On credit
    GRN:                     Dr Inventory / Cr GRNI, for what the advance did not cover
    Purchase voucher (PV):   Dr GRNI · Dr Input VAT / Cr Supplier payable, for the order's un-invoiced GRNI only
    CPV:                     Dr Supplier payable / Cr Bank
  A CPV against an order pays its open PVs first, then goods not yet invoiced ahead (advance + VAT); beyond the order
  it is advance with no VAT. VAT is never booked twice for the same goods.
Withholding is switched off on the CPVs here; its rules are tested on their own.
Run: odoo shell -d <db> < addons/sonador_purchase/tests_manual/purchase_flow.py"""
from odoo import Command

results = []
def ok(cond, msg):
    results.append(bool(cond))
    print(('PASS ' if cond else 'FAIL ') + msg)

env = env(user=env.ref('base.user_admin'))
c = env.company
V, SV, PO = env['sb.voucher'], env['sb.stock.voucher'], env['purchase.order']
Acc, AML = env['account.account'], env['account.move.line']
rm_class = env['product.category'].search([('name', 'ilike', 'Raw Materials')], limit=1)
vat15 = env['account.tax'].search([('type_tax_use', '=', 'purchase'), ('amount', '=', 15), ('company_id', '=', c.id)], limit=1)
VAT = vat15.invoice_repartition_line_ids.filtered(lambda r: r.repartition_type == 'tax').account_id
ADV, GRNI = c.sb_vendor_advance_account_id, c.sb_grni_account_id
BANK = Acc.search([('account_type', '=', 'asset_cash'), ('company_ids', 'in', c.id)], limit=1)
INV = rm_class.property_stock_valuation_account_id or c.account_stock_valuation_id
n = [0]


def item(price, taxed=True):
    n[0] += 1
    return env['product.product'].create({'name': 'Stress item %d' % n[0], 'type': 'consu', 'is_storable': True,
                                          'categ_id': rm_class.id, 'standard_price': price, 'sale_ok': False,
                                          'supplier_taxes_id': [Command.set(vat15.ids if taxed else [])]})


def supplier(name):
    n[0] += 1
    return env['res.partner'].create({'name': name, 'supplier_rank': 1, 'is_company': True, 'vat': '00%08d' % n[0]})


class Book:
    """What the rules say each order holds (before VAT unless noted)."""
    def __init__(self):
        self.adv, self.grni, self.ap, self.ahead, self.invoiced, self.rate = {}, {}, {}, {}, {}, {}


book = Book()
book_vat = [0.0]   # input VAT the rules say was booked (CPVs paid ahead + purchase vouchers): the VAT return must show it
book_base = [0.0]  # and the purchases it is on (before VAT), as local purchases, not as imports


def vat_return():
    from odoo import fields as _f
    d = _f.Date.to_string(_f.Date.context_today(env.user))
    rows = {r.get('id'): r['values'] for r in env['sb.report'].get_report('vat_return', {'date_from': d, 'date_to': d})['rows']}
    std = sum(v.get('base', 0.0) for k, v in rows.items() if k and k.startswith('purchase_') and k[9:].isdigit())
    return {'tax': round(rows.get('purchase', {}).get('tax', 0.0), 2), 'base_std': round(std, 2),
            'customs': round(rows.get('purchase_customs', {}).get('tax', 0.0), 2)}


vat_start = vat_return()


def new_po(partner, lines, rate):
    """lines: [(product, qty, price, taxed)]"""
    po = PO.create({'partner_id': partner.id, 'order_line': [
        Command.create({'product_id': p.id, 'product_qty': q, 'price_unit': pr, 'tax_ids': [Command.set(vat15.ids if t else [])]})
        for p, q, pr, t in lines]})
    po.button_confirm()
    if po.state != 'purchase':
        po.button_approve()
    for d in (book.adv, book.grni, book.ap, book.ahead, book.invoiced):
        d[po.id] = 0.0
    book.rate[po.id] = rate
    return po


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


def payable(po):
    return po.partner_id.commercial_partner_id.property_account_payable_id


def receive(po, qtys, label, user=None):
    """user: who posts it (a store clerk receives without seeing the order's payments)."""
    po_u = po.with_user(user) if user else po
    g = SV.with_user(user).browse(po_u.action_sb_receive()['res_id']) if user else SV.browse(po.action_sb_receive()['res_id'])
    if user:
        ok(g.received_by == user.name, '  %s: received by %s, filled in' % (label, g.received_by))
        ok(round(sum(g.line_ids.mapped('value')), 2) > 0, '  %s: line values shown before posting' % label)
    for l in g.line_ids:
        l.quantity = qtys.get(l.product_id, 0)
    g.line_ids.filtered(lambda l: not l.quantity).unlink()
    g.action_post()
    value = round(sum(l.quantity * po.order_line.filtered(lambda x: x.product_id == l.product_id)[:1].price_unit for l in g.line_ids), 2)
    from_adv = min(value, book.adv[po.id])
    book.adv[po.id] -= from_adv
    book.grni[po.id] += value - from_adv
    check(g.move_id, want((INV, value, 0), (ADV, 0, from_adv), (GRNI, 0, value - from_adv)), label)
    ok(all(l.partner_id == po.partner_id for l in g.move_id.line_ids if l.account_id in (ADV | GRNI)), '  %s: supplier on its lines' % label)
    return g


def invoice(po, label):
    """The order's purchase voucher for what is on GRNI."""
    try:
        act = po.action_sb_bill()
    except Exception as e:
        ok(book.grni[po.id] == 0, '%s: refused (%s)%s' % (label, str(e).splitlines()[0][:120], '' if book.grni[po.id] == 0 else ' but GRNI holds %s' % book.grni[po.id]))
        return None
    pv = V.browse(act['res_id'])
    pv.action_post()
    net = round(book.grni[po.id], 2)
    vat = round(net * book.rate[po.id], 2)
    book.ap[po.id] += net + vat
    book.invoiced[po.id] += net
    book_vat[0] += vat
    book_base[0] += net if vat else 0.0
    book.grni[po.id] = 0.0
    check(pv.move_id, want((GRNI, net, 0), (VAT, vat, 0), (payable(po), 0, net + vat)), label)
    return pv


def pay(po, amount, label):
    p = V.create({'voucher_type': 'payment', 'partner_id': po.partner_id.id, 'payer_type': 'vendor', 'sb_purchase_id': po.id,
                  'received_amount': amount, 'cash_account_id': BANK.id})
    p._onchange_sync_single_deposit()
    if 'wht_supply' in p._fields:
        p.wht_supply = 'goods'
    p.withholding = False
    p.wht_exempt_reason = 'Stress test: withholding is tested on its own'
    if not p.cash_line_ids:
        p.cash_line_ids = [Command.create({'account_id': BANK.id, 'amount': p.amount_cash})]
    try:
        p.action_post()
    except Exception as e:
        ok(False, '%s: not posted (%s)' % (label, str(e).splitlines()[0][:160]))
        return p
    r = book.rate[po.id]
    to_pv = round(min(amount, book.ap[po.id]), 2)
    book.ap[po.id] -= to_pv
    rest = round(amount - to_pv, 2)
    left = round(po.amount_untaxed - book.ahead[po.id] - book.invoiced[po.id], 2)
    covered = min(rest, round(left * (1 + r), 2))
    net = round(covered / (1 + r), 2)
    vat = round(covered - net, 2)
    ahead = round(net + rest - covered, 2)
    book.adv[po.id] += ahead
    book.ahead[po.id] += ahead
    book_vat[0] += vat
    book_base[0] += net if vat else 0.0
    check(p.move_id, want((payable(po), to_pv, 0), (ADV, ahead, 0), (VAT, vat, 0), (BANK, 0, amount)), label)
    return p


def bal(partner, account):
    return round(sum(AML.search([('partner_id', '=', partner.id), ('account_id', '=', account.id), ('parent_state', '=', 'posted')]).mapped('balance')), 2)


def ledger_ok(partner, label):
    pos = [k for k in book.adv if PO.browse(k).partner_id == partner]
    adv = round(sum(book.adv[k] for k in pos), 2)
    grni = round(sum(book.grni[k] for k in pos), 2)
    ap = round(sum(book.ap[k] for k in pos), 2)
    acc_ap = payable(PO.browse(pos[0]))
    got = (bal(partner, ADV), -bal(partner, GRNI), -bal(partner, acc_ap))
    ok(got == (adv, grni, ap), '%s: advance / GRNI / payable %s, rules %s' % (label, got, (adv, grni, ap)))


print('accounts: inventory %s · advance %s · GRNI %s · VAT %s · bank %s' % (INV.code, ADV.code, GRNI.code, VAT.code, BANK.code))

print('\n--- 1. half paid ahead; receipts run past the advance; the rest on a purchase voucher')
s = supplier('Stress Cement Factory'); a = item(1250)
po = new_po(s, [(a, 80, 1250, True)], .15)                   # 100,000 + 15,000
pay(po, 57500, 'CPV half ahead')
receive(po, {a: 30}, 'GRN 30 (inside the advance)')
receive(po, {a: 30}, 'GRN 30 (crosses the advance)')
receive(po, {a: 20}, 'GRN 20 (on credit)')
invoice(po, 'PV for the credit part only')
pay(po, 57500, 'CPV pays the PV')
ledger_ok(s, 'after 1')

print('\n--- 2. paid more than the order')
s = supplier('Stress Over-payee'); b = item(500)
po = new_po(s, [(b, 40, 500, True)], .15)                    # 20,000 + 3,000
pay(po, 25000, 'CPV 25,000 on a 23,000 order')
receive(po, {b: 40}, 'GRN everything')
invoice(po, 'PV refused: nothing on credit')
ledger_ok(s, 'after 2 (2,000 beyond the order stays as advance)')

print('\n--- 3. on credit, odd amounts, paid in two parts')
s = supplier('Stress Odd Amounts'); o = item(333.33)
po = new_po(s, [(o, 7, 333.33, True)], .15)
receive(po, {o: 3}, 'GRN 3 × 333.33')
receive(po, {o: 4}, 'GRN 4 × 333.33')
invoice(po, 'PV for both GRNs')
pay(po, 1150.00, 'CPV part of the PV')
pay(po, round(book.ap[po.id], 2), 'CPV the rest of the PV')
ledger_ok(s, 'after 3')

print('\n--- 4. two orders of one supplier')
s = supplier('Stress Twin Orders'); t = item(1000)
po1 = new_po(s, [(t, 10, 1000, True)], .15)
po2 = new_po(s, [(t, 10, 1000, True)], .15)
pay(po1, 11500, 'CPV ahead for order 1')
receive(po2, {t: 10}, 'GRN order 2 (must not use order 1\'s advance)')
receive(po1, {t: 10}, 'GRN order 1 (its own advance)')
invoice(po2, 'PV order 2')
pay(po2, 11500, 'CPV order 2 pays its PV')
ledger_ok(s, 'after 4')

print('\n--- 5. VAT-free item on credit')
s = supplier('Stress VAT-free Farm'); f = item(60, taxed=False)
po = new_po(s, [(f, 500, 60, False)], 0.0)
receive(po, {f: 500}, 'GRN 500 × 60')
invoice(po, 'PV with no VAT')
pay(po, 30000, 'CPV 30,000')
ledger_ok(s, 'after 5')

print('\n--- 6. GRN voided after it used the advance')
s = supplier('Stress Void'); v = item(2000)
po = new_po(s, [(v, 10, 2000, True)], .15)
pay(po, 23000, 'CPV ahead 23,000')
g = receive(po, {v: 6}, 'GRN 6')
try:
    g.sb_void_reason = 'Stress test: wrong quantity'
    g.action_void()
    book.adv[po.id] += 12000
    ok(True, 'GRN voided')
except Exception as e:
    ok(False, 'GRN void refused: %s' % str(e).splitlines()[0][:160])
receive(po, {v: 10}, 'GRN 10 after the void')
ledger_ok(s, 'after 6')

print('\n--- 7. an open PV and goods not yet invoiced: the CPV pays the PV first, then ahead')
s = supplier('Stress Mixed'); m = item(1000)
po = new_po(s, [(m, 20, 1000, True)], .15)                   # 20,000 + 3,000
receive(po, {m: 8}, 'GRN 8 on credit')
invoice(po, 'PV for 8')
pay(po, 23000, 'CPV the whole order: PV 9,200, then 12 ahead (13,800 with VAT)')
receive(po, {m: 12}, 'GRN the other 12 (from the advance)')
ledger_ok(s, 'after 7')

print('\n--- 8. VAT never twice: everything invoiced, then paid')
s = supplier('Stress Double VAT'); d = item(750)
po = new_po(s, [(d, 40, 750, True)], .15)                    # 30,000 + 4,500
receive(po, {d: 40}, 'GRN all')
invoice(po, 'PV all')
pay(po, 34500, 'CPV 34,500 pays the PV only, no new VAT')
ledger_ok(s, 'after 8')

print('\n--- 9. the store clerk receives against a prepaid order (no access to payments)')
clerk = env['res.users'].create({'name': 'Stress Store Clerk', 'login': 'stress-clerk@test.local',
                                 'group_ids': [Command.set([env.ref('base.group_user').id, env.ref('sonador_inventory.group_sb_store_user').id])]})
s = supplier('Stress Clerk Supplier'); k = item(900)
po = new_po(s, [(k, 10, 900, True)], .15)
pay(po, 10350, 'CPV ahead (finance)')
try:
    receive(po, {k: 10}, 'GRN posted by the store clerk', user=clerk)
except Exception as e:
    ok(False, 'GRN posted by the store clerk: refused (%s)' % str(e).splitlines()[0][:150])
ledger_ok(s, 'after 9')

print('\n--- 10. withholding: on the value before VAT, above the threshold before VAT')
WHT = c.sb_wht_payable_account_id
def pay_wht(po, amount):
    p = V.create({'voucher_type': 'payment', 'partner_id': po.partner_id.id, 'payer_type': 'vendor', 'sb_purchase_id': po.id,
                  'received_amount': amount, 'cash_account_id': BANK.id})
    p._onchange_sync_single_deposit(); p.wht_supply = 'goods'; p._onchange_sync_single_deposit()
    if not p.cash_line_ids:
        p.cash_line_ids = [Command.create({'account_id': BANK.id, 'amount': p.amount_cash})]
    p.action_post()
    return p
rate = c.sb_wht_rate
for label, before_vat, vat_on, credit in (('paid ahead 20,000 + VAT', 20000.0, True, False), ('pays a purchase voucher of 30,000 + VAT', 30000.0, True, True),
                                          ('paid ahead 18,000 + VAT (under the threshold before VAT, over it with VAT)', 18000.0, True, False)):
    s = supplier('WHT %s' % label[:20]); s.sb_trade_license = 'TL-%d' % n[0]
    k = item(100)
    po = new_po(s, [(k, before_vat / 100, 100, vat_on)], .15)
    if credit:
        receive(po, {k: before_vat / 100}, '  GRN'); invoice(po, '  PV')
    p = pay_wht(po, round(before_vat * 1.15, 2))
    want_wht = round(before_vat * rate / 100, 2) if before_vat > c.sb_wht_goods_threshold else 0.0
    got = round(sum(l.credit for l in p.move_id.line_ids if l.account_id == WHT), 2)
    ok(got == want_wht, 'withholding, %s: %s (rules: %g%% of %s before VAT = %s)' % (label, got, rate, before_vat, want_wht))
    if not credit:   # paid ahead: the CPV books the VAT (a purchase voucher's was counted when it was entered)
        book_vat[0] += round(before_vat * .15, 2); book_base[0] += before_vat

print('\n--- VAT return')
end = vat_return()
d_tax, d_base, d_customs = (round(end[k] - vat_start[k], 2) for k in ('tax', 'base_std', 'customs'))
ok(d_tax == round(book_vat[0], 2), 'the VAT return shows all the input VAT booked: return %s, booked %s' % (d_tax, round(book_vat[0], 2)))
ok(d_customs == 0, 'none of it filed as import VAT paid at customs (these are local purchases): %s' % d_customs)
ok(d_base == round(book_base[0], 2), 'the purchases it is on are declared, at 15%%: base %s, rules %s' % (d_base, round(book_base[0], 2)))

print('\n%d of %d checks pass' % (sum(results), len(results)))
env.cr.rollback()
