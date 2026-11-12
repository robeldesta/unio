"""Approval center in Me, as normal users (rolls back). Run: ~/ecogreen-odoo19/sync/local_shell.sh < this file"""
from odoo.exceptions import UserError

ok = []
def check(cond, msg):
    assert cond, msg
    ok.append(msg)

try:
    U = env['res.users']
    E = env['hr.employee']
    company = env.company
    # an employee and a manager, both plain internal users
    grp_user = env.ref('base.group_user')
    def mk(login, name, groups=()):
        u = U.with_context(no_reset_password=True).create({'login': login, 'name': name, 'company_id': company.id,
                                                           'company_ids': [(6, 0, [company.id])],
                                                           'group_ids': [(6, 0, [grp_user.id] + [env.ref(g).id for g in groups])]})
        return u
    boss = mk('t_boss@x', 'Test Boss', ['purchase.group_purchase_manager', 'sonador_purchase.group_sb_po_gm', 'sonador_me.group_gate'])
    staff = mk('t_staff@x', 'Test Staff', ['purchase.group_purchase_user'])
    eb = E.create({'name': 'Test Boss', 'user_id': boss.id, 'company_id': company.id})
    es = E.create({'name': 'Test Staff', 'user_id': staff.id, 'parent_id': eb.id, 'company_id': company.id})
    check(not boss._is_admin() and not staff._is_admin(), 'test users are not administrators')

    Me = env['sb.me']
    kinds = Me.with_user(boss)._approval_kinds()
    print('kinds', kinds)
    check({'leave', 'po', 'gate'} <= set(kinds), 'leave, purchase and gate are approval sources')

    # a purchase order waiting for approval, made by staff
    partner = env['res.partner'].create({'name': 'Test Supplier'})
    product = env['product.product'].search([('purchase_ok', '=', True)], limit=1)
    po = env['purchase.order'].with_user(staff).create({'partner_id': partner.id, 'user_id': staff.id,
                                                        'order_line': [(0, 0, {'product_id': product.id, 'product_qty': 1, 'price_unit': 10.0})]})
    po.sudo().write({'state': 'to approve'})
    items = Me.with_user(boss).approvals()
    keys = [i['key'] for i in items]
    check('po:%d' % po.id in keys, 'boss sees the PO waiting')
    check('po:%d' % po.id not in [i['key'] for i in Me.with_user(staff).approvals()], 'staff does not see it as theirs to approve')
    mine = Me.with_user(staff).submitted()
    m = [i for i in mine if i['key'] == 'po:%d' % po.id]
    check(m and m[0]['waiting_for'], 'staff sees it under "I submitted" with who it waits for: %s' % (m and m[0]['waiting_for']))

    # send back needs a reason
    try:
        Me.with_user(boss).decide('po:%d' % po.id, False, '')
        check(False, 'send back without reason refused')
    except UserError:
        check(True, 'send back without reason refused')
    # staff cannot decide something that is not theirs
    try:
        Me.with_user(staff).decide('po:%d' % po.id, True, '')
        check(False, 'staff cannot approve')
    except UserError:
        check(True, 'staff cannot approve what is not in their list')
    Me.with_user(boss).decide('po:%d' % po.id, False, 'Wrong supplier')
    check(po.state == 'draft', 'sent back PO is a draft again for the requester')
    h = Me.with_user(boss).history()
    check(h['decided'] and h['decided'][0]['decision'] == 'sent_back' and h['decided'][0]['note'] == 'Wrong supplier', 'history shows the send back with reason')
    check(Me.with_user(staff).history()['mine'][0]['title'], 'the submitter sees the decision on their item')
    # submit again and approve
    po.sudo().write({'state': 'to approve'})
    Me.with_user(boss).decide('po:%d' % po.id, True, '')
    check(po.state == 'purchase', 'approved PO is confirmed')

    # leave: staff asks, boss (manager) approves
    if 'sb.leave.service' in env:
        S = env['sb.leave.service'].with_user(staff)
        ctx = S.ctx()
        types = [t for t in ctx['types'] if t['mine']]
        if types:
            import datetime
            d = datetime.date.today() + datetime.timedelta(days=30)
            while d.weekday() >= 5:
                d += datetime.timedelta(days=1)
            try:
                S.request({'type_id': types[0]['id'], 'date_from': str(d), 'date_to': str(d), 'reason': 'test'}, submit=True)
                lv = [i for i in Me.with_user(boss).approvals() if i['kind'] == 'leave']
                check(lv, 'manager sees the leave request')
                check([i for i in Me.with_user(staff).submitted() if i['kind'] == 'leave'], 'employee sees the leave under I submitted')
                Me.with_user(boss).decide(lv[0]['key'], True, '')
                check(not [i for i in Me.with_user(boss).approvals() if i['key'] == lv[0]['key']], 'leave approved and gone from the list')
            except UserError as e:
                print('leave skipped:', e)

    # gate pass waiting at the gate
    gp = env['sb.gate.pass'].create({'purpose': 'delivery', 'partner_id': partner.id, 'vehicle': 'AA-3-12345', 'driver': 'Abebe',
                                     'line_ids': [(0, 0, {'name': 'Urea 50 kg', 'quantity': 10})]})
    gp.with_user(staff).sudo().action_issue()
    gk = 'gate:%d' % gp.id
    check(gk in [i['key'] for i in Me.with_user(boss).approvals()], 'gate security sees the issued gate pass')
    check(gk not in [i['key'] for i in Me.with_user(staff).approvals()], 'a user without the gate role does not')
    Me.with_user(boss).decide(gk, True, '')
    check(gp.state == 'out', 'let out at the gate')
    check(Me.with_user(boss).open_doc('po:%d' % po.id)['res_model'] == 'purchase.order', 'open goes to the document')
    check(isinstance(Me.with_user(boss).count(), int), 'badge count')
    d = Me.with_user(staff).data()
    check('sections' in d and 'notices' in d, 'Me page has sections registry and notices')
    print('\n'.join('OK  ' + m for m in ok))
    print('ALL PASSED', len(ok))
finally:
    env.cr.rollback()
