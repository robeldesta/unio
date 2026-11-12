"""Store documents posted by finance, and goods received on prepaid orders billed by themselves (Eco Green data, as the
real people: Meseret store, Zufan finance). Rolls back.
  ~/ecogreen-odoo19/sync/local_shell.sh < ~/sonador-odoo/addons/sonador_purchase/tests_manual/finance_posts.py"""
from odoo import Command, fields
from odoo.exceptions import UserError

res = []


def ok(c, m):
    print(('PASS ' if c else 'FAIL ') + m)
    res.append(bool(c))


try:
    c = env.company
    c.sudo().write({'sb_store_posts_by_finance': True, 'sb_po_auto_bill': True})
    U = env['res.users'].sudo()
    meseret = U.search([('login', '=ilike', 'meserets@ecogreen.et')], limit=1)
    zufan = U.search([('login', '=ilike', 'zufan@ecogreen.et')], limit=1)
    leul = U.search([('login', '=ilike', 'leulm@ecogreen.et')], limit=1)
    assert meseret and zufan and leul
    ok(meseret.has_group('sonador_inventory.group_sb_store_user') and not meseret.has_group('sonador_books.group_sb_clerk'),
       'Meseret: store clerk, no Accounting role')
    SV, V, PO, Me = env['sb.stock.voucher'], env['sb.voucher'], env['purchase.order'], env['sb.me']
    vendor = env['res.partner'].create({'name': 'Test Prepaid Supplier', 'supplier_rank': 1})
    rm = env['product.product'].search([('default_code', '=', '1501-0001')], limit=1)
    wh = env['stock.warehouse'].search([('company_id', '=', c.id)], limit=1)
    bank = env['account.journal'].search([('type', '=', 'bank'), ('company_id', '=', c.id)], limit=1).default_account_id
    grni, adv = c.sb_grni_account_id, c.sb_vendor_advance_account_id
    aml = env['account.move.line']

    # ------------------------------------------------------------------ 1. store -> finance
    g = SV.with_user(meseret).create({'voucher_type': 'grn', 'partner_id': vendor.id, 'warehouse_id': wh.id,
                                      'line_ids': [Command.create({'product_id': rm.id, 'quantity': 10, 'unit_cost': 0})]})
    ok(g.sb_finance_posts, 'store clerk sees Send to finance instead of Post')
    try:
        with env.cr.savepoint():
            g.with_user(meseret).action_post()
        ok(False, 'store clerk posted')
    except UserError as e:
        ok('posted by finance' in str(e), 'store clerk cannot post: %s' % str(e)[:90])
    act = g.with_user(meseret).action_sb_send_finance()
    msg = act['params']['message']
    ok(msg.startswith('Saved and sent to finance: they check the unit price and VAT, then post it.'), 'message: %s' % msg.split('\n')[0])
    ok('no unit price' in msg and act['params']['type'] == 'warning', 'zero price warned: %s' % msg.split('\n')[-1])
    ok(g.state == 'draft' and g.sb_sent_finance_by == meseret and g.sb_sent_finance_at, 'still a draft, sent by Meseret')
    key = 'store:%d' % g.id
    ok(key in [i['key'] for i in Me.with_user(zufan).approvals()], 'waits in Zufan\'s Approval Center')
    ok(key not in [i['key'] for i in Me.with_user(meseret).approvals()], 'not in the store clerk\'s own approvals')
    ok(key in [i['key'] for i in Me.with_user(meseret).submitted()], 'Meseret sees it under I submitted (waiting for finance)')
    it = [i for i in Me.with_user(zufan).approvals() if i['key'] == key][0]
    ok(any('no unit price' in d for d in it['details']) and it['approve_label'] == 'Post', 'the card says Check: no unit price; Approve = Post')
    Me.with_user(zufan).decide(key, False, 'Unit price missing: take it from the invoice')
    ok(g.sb_sent_back_reason == 'Unit price missing: take it from the invoice' and not g.sb_sent_finance_at and g.state == 'draft',
       'sent back with the reason')
    ok(key not in [i['key'] for i in Me.with_user(zufan).approvals()], 'no longer waits for finance')
    g.with_user(meseret).line_ids.unit_cost = 40  # far from the last cost
    last = g._sb_last_cost(rm)
    act = g.with_user(meseret).action_sb_send_finance()
    far = abs(40 - last) > 0.2 * abs(last) if last else False
    ok((('above' in act['params']['message'] or 'below' in act['params']['message']) == far),
       'price 20%% away from the last cost (%s) warned: %s' % (last, far))
    g.with_user(zufan).line_ids.unit_cost = 45  # finance corrects the price on the form
    Me.with_user(zufan).decide(key, True)
    ok(g.state == 'posted' and abs(g.amount_total - 450) < 0.01, 'finance posted it from Me (%s, %s)' % (g.name, g.amount_total))
    ok(any('Checked and posted by' in (m.body or '') for m in g.message_ids[:4]), 'chatter: checked and posted by Zufan')
    gi = SV.with_user(meseret).create({'voucher_type': 'giv', 'warehouse_id': wh.id, 'department': 'Mixing', 'sb_doc_kind': 'drm',
                                       'line_ids': [Command.create({'product_id': rm.id, 'quantity': 2})]})
    gi.with_user(meseret).action_sb_send_finance()
    Me.with_user(zufan).decide('store:%d' % gi.id, True)
    ok(gi.state == 'posted', 'a DRM GIV sent by the store, posted by finance: %s' % gi.name)
    c.sudo().sb_store_posts_by_finance = False
    g0 = SV.with_user(meseret).create({'voucher_type': 'giv', 'warehouse_id': wh.id, 'department': 'Mixing',
                                       'line_ids': [Command.create({'product_id': rm.id, 'quantity': 1})]})
    ok(not g0.sb_finance_posts, 'setting off: Post shown to the store as before')
    g0.with_user(meseret).action_post()
    ok(g0.state == 'posted', 'setting off: the store clerk posts as today')
    c.sudo().sb_store_posts_by_finance = True

    # ------------------------------------------------------------------ 2. prepaid purchase order billed by itself
    def pay(po, amount):
        p = V.with_user(zufan).create({'voucher_type': 'payment', 'partner_id': vendor.id, 'payer_type': 'vendor', 'sb_purchase_id': po.id,
                                       'received_amount': amount, 'cash_account_id': bank.id, 'withholding': False})
        p._onchange_sync_single_deposit()
        if not p.cash_line_ids:
            p.cash_line_ids = [Command.create({'account_id': bank.id, 'amount': p.amount_cash})]
        p.action_post()
        return p

    def vendor_balance():
        lines = aml.search([('partner_id', '=', vendor.id), ('parent_state', '=', 'posted'),
                            ('account_id', 'in', (adv | vendor.property_account_payable_id).ids)])
        return round(sum(lines.mapped('balance')), 2)

    def grni_of(*moves):
        return round(sum(aml.search([('account_id', '=', grni.id), ('move_id', 'in', [m.id for m in moves])]).mapped('balance')), 2)

    po = PO.create({'partner_id': vendor.id, 'order_line': [Command.create({'product_id': rm.id, 'product_qty': 100, 'price_unit': 50,
                                                                             'tax_ids': [Command.clear()]})]})
    po.button_confirm()
    if po.state == 'to approve':
        po.with_user(leul).button_approve()
    adv_pay = pay(po, 5000)
    env.invalidate_all()
    ok(po.sb_payment_status in ('advance', 'paid') and abs(po.sb_amount_advance - 5000) < 0.01, 'PO %s paid 5,000 in advance (%s %s)' % (po.name, po.sb_payment_status, po.sb_amount_advance))
    # partial receipt, sent by the store, posted by finance
    a = po.action_sb_receive()
    g1 = SV.browse(a['res_id'])
    g1.line_ids.quantity = 40
    g1.with_user(meseret).action_sb_send_finance()
    Me.with_user(zufan).decide('store:%d' % g1.id, True)
    env.invalidate_all()
    bill = g1.bill_many_ids | g1.bill_ids
    ok(g1.state == 'posted' and len(bill) == 1 and bill.state == 'posted', 'GRV posted by finance; supplier bill made and posted: %s'
       % ', '.join(bill.mapped('name')))
    ok(bill.sb_auto_bill and abs(bill.amount_total - 2000) < 0.01 and bill.sb_purchase_id == po, 'bill for what arrived: 40 x 50 = %s'
       % bill.amount_total)
    ok(bill.name.startswith(V.env['sb.doc.series']._for('bill', company=c).prefix.replace('{fy}', '')[:3]), 'numbered by the bill series')
    ok('Made automatically from %s' % g1.name in ' '.join(bill.message_ids.mapped('body')), 'bill chatter: made automatically from the GRV')
    ok(bill.move_id.payment_state in ('paid', 'in_payment'), 'advance applied: the bill is paid (%s)' % bill.move_id.payment_state)
    ok(abs(po.sb_amount_advance - 3000) < 0.01, 'what is left of the advance stays on the order: %s' % po.sb_amount_advance)
    ok(grni_of(g1.move_id, bill.move_id) == 0, 'goods received not invoiced cleared for this GRV')
    # the rest: 60 x 50 = 3,000, exactly what is left of the advance
    a = po.action_sb_receive()
    g2 = SV.browse(a['res_id'])
    g2.with_user(zufan).action_post()
    env.invalidate_all()
    bill2 = g2.bill_many_ids | g2.bill_ids
    ok(bill2.state == 'posted' and bill2.move_id.payment_state in ('paid', 'in_payment') and abs(po.sb_amount_advance) < 0.01,
       'second GRV: billed and settled by the rest of the advance')
    ok(vendor_balance() == 0, 'supplier balance (payable + advance) 0: %s' % vendor_balance())
    ok(po.sb_payment_status == 'paid', 'order paid')
    # over-advance: 6,000 paid for a 5,000 order
    po2 = PO.create({'partner_id': vendor.id, 'order_line': [Command.create({'product_id': rm.id, 'product_qty': 100, 'price_unit': 50,
                                                                              'tax_ids': [Command.clear()]})]})
    po2.button_confirm()
    if po2.state == 'to approve':
        po2.with_user(leul).button_approve()
    pay(po2, 6000)
    a = po2.action_sb_receive()
    g3 = SV.browse(a['res_id'])
    g3.with_user(zufan).action_post()
    env.invalidate_all()
    bill3 = g3.bill_many_ids | g3.bill_ids
    ok(bill3.state == 'posted' and bill3.move_id.payment_state in ('paid', 'in_payment') and abs(po2.sb_amount_advance - 1000) < 0.01,
       'over-advance: bill 5,000 settled, 1,000 of the advance stays open (%s)' % po2.sb_amount_advance)
    # not prepaid: nothing automatic
    po3 = PO.create({'partner_id': vendor.id, 'order_line': [Command.create({'product_id': rm.id, 'product_qty': 5, 'price_unit': 50})]})
    po3.button_confirm()
    if po3.state == 'to approve':
        po3.with_user(leul).button_approve()
    g4 = SV.browse(po3.action_sb_receive()['res_id'])
    g4.with_user(zufan).action_post()
    ok(not (g4.bill_ids | g4.bill_many_ids), 'not prepaid: no bill made')
    # the bill cannot be posted (books closed on the GRV date after it is posted): GRV posted, bill waits in Me
    po4 = PO.create({'partner_id': vendor.id, 'order_line': [Command.create({'product_id': rm.id, 'product_qty': 5, 'price_unit': 50,
                                                                              'tax_ids': [Command.clear()]})]})
    po4.button_confirm()
    if po4.state == 'to approve':
        po4.with_user(leul).button_approve()
    pay(po4, 250)
    g5 = SV.browse(po4.action_sb_receive()['res_id'])
    # make the bill fail: the supplier's payable account missing on the bill journal is hard to fake; a store clerk posting
    # with the setting off cannot post bills, which is the same path
    c.sudo().sb_store_posts_by_finance = False
    g5.with_user(meseret).action_post()
    env.invalidate_all()
    b5 = g5.bill_ids | g5.bill_many_ids
    ok(g5.state == 'posted' and b5.state == 'draft' and b5.sb_auto_bill_error, 'GRV posted; the bill stays a draft: %s'
       % (b5.sb_auto_bill_error or '')[:90])
    k5 = 'autobill:%d' % b5.id
    ok(k5 in [i['key'] for i in Me.with_user(zufan).approvals()], 'finance gets it in Me with the reason')
    Me.with_user(zufan).decide(k5, True)
    env.invalidate_all()
    ok(b5.state == 'posted' and not b5.sb_auto_bill_error and b5.move_id.payment_state in ('paid', 'in_payment'),
       'finance posted it from Me; advance applied')
finally:
    print('RESULT: %d/%d passed' % (sum(res), len(res)))
    env.cr.rollback()
