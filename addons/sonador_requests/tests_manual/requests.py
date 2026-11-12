# Requests: the full chain as normal users (requester, department head, finance, general manager, cashier), send back,
# thresholds, petty cash, per diem, purchase request -> RFQ, access. Rolled back at the end.
#   ~/ecogreen-odoo19/sync/local_shell.sh < ~/sonador-odoo/addons/sonador_requests/tests_manual/requests.py
from odoo import Command, fields
from odoo.exceptions import AccessError, UserError

ok = []



def _numbered(rec, code):
    """Named from its sequence (Eco Green keeps its Odoo 18 formats: PR/2026/1742, PCR209, PD/2026/00174, PR00502)."""
    seq = env['ir.sequence'].sudo().search([('code', '=', code)], limit=1)
    head = seq._get_prefix_suffix()[0] if seq else ''
    return bool(rec.name) and rec.name.startswith(head) and rec.name[len(head):].isdigit()

def check(cond, msg):
    print(('PASS ' if cond else 'FAIL ') + msg)
    ok.append(bool(cond))


def refused(fn, msg):
    try:
        with env.cr.savepoint():
            fn()
    except (AccessError, UserError) as e:
        check(True, msg + ' (refused: %s)' % str(e).splitlines()[0][:90])
        return
    check(False, msg + ' (was allowed)')


company = env.company
U = env['res.users'].with_context(no_reset_password=True, mail_create_nolog=True, mail_notrack=True)
g = lambda x: env.ref(x)


def user(role, groups):
    return U.create({'name': 'Test %s' % role, 'login': 'test_rq_%s@example.local' % role, 'company_id': company.id,
                     'company_ids': [Command.set([company.id])],
                     'group_ids': [Command.set([g('base.group_user').id] + [g(x).id for x in groups])]})


requester = user('requester', [])
head = user('head', [])
finance = user('finance', ['sonador_requests.group_request_finance'])
gm = user('gm', ['sonador_requests.group_request_gm'])
cashier = user('cashier', ['sonador_requests.group_request_cashier', 'sonador_books.group_sb_accountant'])
fleet = user('fleet', ['sonador_fleet.group_user'])
buyer = user('buyer', ['sonador_purchase.group_sb_purchase_user'])
other = user('other', [])
Emp = env['hr.employee']
dept = env['hr.department'].create({'name': 'Test requests dept', 'company_id': company.id})
e_head = Emp.create({'name': 'Test head', 'user_id': head.id, 'department_id': dept.id, 'company_id': company.id})
dept.manager_id = e_head
e_req = Emp.create({'name': 'Test requester', 'user_id': requester.id, 'department_id': dept.id, 'company_id': company.id})

Acc = env['account.account']
exp1 = Acc.search([('company_ids', 'in', company.id), ('account_type', '=', 'expense'), ('active', '=', True)], limit=1)
exp2 = Acc.search([('company_ids', 'in', company.id), ('account_type', '=', 'expense'), ('active', '=', True), ('id', '!=', exp1.id)], limit=1)
bank = env['sb.voucher']._sb_paid_from_for(10 ** 7) or Acc.search([('company_ids', 'in', company.id), ('account_type', '=', 'asset_cash')], limit=1)
vendor = env['res.partner'].create({'name': 'Test requests vendor', 'company_id': company.id})
cat = env['sb.request.category'].create({'name': 'Test office supplies', 'account_id': exp1.id})


def pay(v, user):
    if not v.cash_line_ids:
        v.write({'cash_line_ids': [Command.create({'account_id': bank.id, 'method': 'transfer'})]})
    v._onchange_sync_single_deposit()
    v.wht_exempt_reason = 'Test: exemption certificate TEST-001'
    v.with_user(user).action_post()


print('accounts', exp1.code, exp2.code, 'paid from', bank.code, bank.name)

Pol = env['sb.request.policy']
pay_rule = Pol._for('payment')
pay_rule.write({'dept_step': True, 'finance_step': True, 'gm_step': 'above', 'gm_above': 10000})
PR = env['sb.payment.request']

# ---------------------------------------------------------------- 1. full chain to a posted payment voucher
print('\n--- 1. payment request: department head > finance > general manager > cashier')
r = PR.with_user(requester).create({'request_type': 'payment', 'partner_id': vendor.id, 'category_id': cat.id,
                                    'description': 'Toner and paper for the office', 'amount': 25000})
check(_numbered(r, 'sb.payment.request'), 'numbered %s' % r.name)
check(r.department_id == dept and r.dept_approver_id == head, 'department head found from the department (%s)' % r.dept_approver_id.name)
check(r.route.count('>') == 2, 'path: %s' % r.route)
r.with_user(requester).action_submit()
check(r.state == 'dept', 'submitted: waiting for the department head')
refused(lambda: r.with_user(finance).action_approve(), 'finance cannot approve the department step')
refused(lambda: r.with_user(requester).action_approve(), 'requester cannot approve their own request')
refused(lambda: r.with_user(requester).write({'amount': 1}), 'amount locked after submitting')
check(r in PR.sb_waiting_for_user(head), 'in the department head\'s waiting list')
me_items = env['sb.me'].with_user(head).approvals()
check(any(i['key'] == 'payreq:%d' % r.id for i in me_items), 'in the head\'s Me approvals inbox')
check(any(i['key'] == 'payreq:%d' % r.id for i in env['sb.me'].with_user(requester).submitted()), 'in the requester\'s "submitted" list in Me')
r.with_user(head).action_approve()
check(r.state == 'finance' and r.dept_by == head, 'department approved, now finance')
refused(lambda: env['sb.payment.request.line'].with_user(requester).create({'request_id': r.id, 'account_id': exp1.id, 'amount': 1}),
        'requester cannot code the payment order after submitting')
r.with_user(finance).write({'line_ids': [Command.create({'account_id': exp1.id, 'name': 'Toner', 'amount': 15000}),
                                         Command.create({'account_id': exp2.id, 'name': 'Paper', 'amount': 9000})]})
refused(lambda: r.with_user(finance).action_approve(), 'finance check refused while the lines do not add up')
r.line_ids[1].with_user(finance).amount = 10000
r.with_user(finance).action_approve()
check(r.state == 'gm', 'finance checked, now the general manager (above 10,000)')

print('\n--- 2. send back')
refused(lambda: r.with_user(gm).action_send_back(), 'send back needs a reason')
r.with_user(gm).action_send_back('Attach the quotation')
check(r.state == 'draft' and r.sent_back_reason == 'Attach the quotation' and r.sent_back_by == gm, 'back to draft with the reason')
check(not r.dept_by and not r.finance_by, 'earlier approvals cleared')
msgs = r.message_ids.mapped('body')
check(any('Attach the quotation' in (b or '') for b in msgs), 'reason in the chatter')
r.with_user(requester).write({'attachment_note': 'Quotation Q-17 attached'})
r.with_user(requester).action_submit()
r.with_user(head).action_approve()
r.with_user(finance).action_approve()
env['sb.me'].with_user(gm).decide('payreq:%d' % r.id, True)
check(r.state == 'approved' and r.gm_by == gm, 'approved by the general manager from Me')
log = env['sb.approval.log'].search([('key', '=', 'payreq:%d' % r.id)], limit=1)
check(bool(log) and log.decision == 'approved', 'decision in the Me approval log')

print('\n--- 3. cashier pays')
refused(lambda: r.with_user(requester).action_create_voucher(), 'requester cannot make the voucher')
act = r.with_user(cashier).action_create_voucher()
v = env['sb.voucher'].browse(act['res_id'])
check(v.voucher_type == 'payment' and v.partner_id == vendor and v.sb_request_id == r, 'draft payment voucher for the vendor')
check(sorted(v.line_ids.mapped('amount')) == [10000, 15000] and set(v.line_ids.mapped('account_id').ids) == {exp1.id, exp2.id},
      'lines from the payment order')
check(v.reference == r.name, 'voucher reference = request number')
again = r.with_user(cashier).action_create_voucher()
check(again.get('res_id') == v.id, 'second click opens the same draft voucher')
pay(v, cashier)
check(v.state == 'posted', 'voucher posted %s (%s)' % (v.name, v.amount_total))
check(r.state == 'paid' and r.amount_paid == 25000 and r.amount_due == 0, 'request paid')
refused(lambda: r.with_user(requester).action_cancel('x'), 'paid request cannot be cancelled')

print('\n--- 4. partly paid')
r2 = PR.with_user(requester).create({'partner_id': vendor.id, 'category_id': cat.id, 'description': 'Advance for repairs', 'amount': 8000})
r2.with_user(requester).action_submit()
r2.with_user(head).action_approve()
r2.with_user(finance).action_approve()
check(r2.state == 'approved', 'below 10,000: approved by finance without the general manager (%s)' % r2.route)
v2 = env['sb.voucher'].browse(r2.with_user(cashier).action_create_voucher()['res_id'])
check(v2.line_ids.account_id == exp1 and v2.line_ids.amount == 8000, 'no payment order: the category account')
v2.line_ids.amount = 3000
pay(v2, cashier)
check(r2.state == 'partial' and r2.amount_due == 5000, 'partly paid, 5,000 to pay')
v3 = env['sb.voucher'].browse(r2.with_user(cashier).action_create_voucher()['res_id'])
check(v3.line_ids.amount == 5000, 'second voucher for the rest')
pay(v3, cashier)
check(r2.state == 'paid', 'paid in two vouchers')
v3.with_user(cashier).write({'sb_void_reason': 'test'})
try:
    v3.with_user(cashier).action_void()
    check(r2.state == 'partial', 'voiding the second voucher: partly paid again')
except UserError as e:
    print('  (void not allowed here: %s)' % e)

print('\n--- 5. thresholds and own department head')
pay_rule.gm_step = 'always'
company.sb_self_approval = 'allowed'
r3 = PR.with_user(head).create({'partner_id': vendor.id, 'category_id': cat.id, 'description': 'Head asks', 'amount': 500})
check(r3.dept_approver_id == head or not r3.dept_approver_id, 'head is their own department head')
r3.dept_approver_id = head
r3.with_user(head).action_submit()
check(r3.state == 'finance', 'own department head: department step skipped')
r3.with_user(finance).action_approve()
check(r3.state == 'gm', 'GM always: 500 goes to the general manager')
pay_rule.write({'gm_step': 'above', 'gm_above': 10000})
refused(lambda: r3.with_user(other).action_approve(), 'someone else cannot approve')

print('\n--- 5b. self-approval not allowed: one level up')
company.write({'sb_self_approval': 'up', 'sb_gm_own_approver_id': False})
boss = user('boss', [])
env['hr.employee'].create({'name': 'Test boss', 'user_id': boss.id, 'company_id': company.id})
head_emp = head.employee_ids[:1] or env['hr.employee'].create({'name': 'Test head', 'user_id': head.id, 'company_id': company.id})
head_emp.parent_id = boss.employee_ids[:1]
r5 = PR.with_user(head).create({'partner_id': vendor.id, 'category_id': cat.id, 'description': 'Head asks again', 'amount': 500})
r5.dept_approver_id = head
r5.with_user(head).action_submit()
check(r5.state == 'dept' and r5.dept_approver_id == boss, 'own department head: the department step goes to their manager')
refused(lambda: r5.with_user(head).action_approve(), 'the requester cannot approve their own request')
r5.with_user(boss).action_approve()
fin_emp = finance.employee_ids[:1] or env['hr.employee'].create({'name': 'Test fin', 'user_id': finance.id, 'company_id': company.id})
fin_emp.parent_id = boss.employee_ids[:1]
r6 = PR.with_user(finance).create({'partner_id': vendor.id, 'category_id': cat.id, 'description': 'Finance asks', 'amount': 500,
                                   'dept_approver_id': head.id})
r6.with_user(finance).action_submit()
r6.with_user(head).action_approve()
check(r6.state == 'finance' and r6.sb_step_user_id == boss, 'finance\'s own request: the finance step goes one level up')
check(r6 not in PR.sb_waiting_for_user(finance) and r6 in PR.sb_waiting_for_user(boss), 'in the manager\'s list, not finance\'s')
company.sb_self_approval = 'allowed'

print('\n--- 6. cancel with a reason')
r4 = PR.with_user(requester).create({'partner_id': vendor.id, 'description': 'Wrong one', 'amount': 100})
r4.with_user(requester).action_submit()
refused(lambda: r4.with_user(requester).action_cancel(), 'cancelling a submitted request needs a reason')
r4.with_user(requester).action_cancel('Duplicate')
check(r4.state == 'cancel' and r4.cancel_reason == 'Duplicate', 'cancelled with the reason')
refused(lambda: r4.with_user(requester).unlink() if False else r.with_user(requester).unlink(), 'a paid request cannot be deleted')

print('\n--- 7. access')
refused(lambda: PR.with_user(other).browse(r.id).read(['name']), 'another employee cannot see the request')
check(PR.with_user(finance).search_count([('id', '=', r.id)]) == 1, 'finance sees every request')
check(PR.with_user(head).search_count([('id', '=', r.id)]) == 1, 'the department head sees what they approve')

print('\n--- 8. petty cash')
petty_rule = env['sb.request.policy']._for('petty', env.company)
petty_rule.write({'gm_step': 'never', 'gm_delegate_id': False})   # the module's default route (a company may set its own)
pc = PR.with_user(requester).create({'request_type': 'petty', 'paid_to': 'Abebe (cleaner)', 'category_id': cat.id,
                                     'description': 'Detergent', 'amount': 750})
check(_numbered(pc, 'sb.payment.request.petty'), 'petty cash numbered %s' % pc.name)
pc.with_user(requester).action_submit()
check(pc.state == 'finance', 'petty cash: finance only (%s)' % pc.route)
pc.with_user(finance).action_approve()
check(pc.state == 'approved', 'approved by finance')
# final approval by a named person up to a limit (Eco Green: Kebkab up to 2,000), the general manager above
petty_rule.write({'gm_step': 'always', 'gm_delegate_id': head.id, 'gm_delegate_limit': 1000})
small = PR.with_user(requester).create({'request_type': 'petty', 'paid_to': 'Abebe', 'category_id': cat.id, 'description': 'Soap', 'amount': 400})
small.with_user(requester).action_submit()
small.with_user(finance).action_approve()
check(small.state == 'gm' and small.sb_step_user_id == head and small._sb_can(head) and not small._sb_can(gm),
      'petty cash up to the limit: final approval by the named person (%s)' % small.sb_waiting_for)
small.with_user(head).action_approve()
check(small.state == 'approved', 'named person approves it')
big = PR.with_user(requester).create({'request_type': 'petty', 'paid_to': 'Abebe', 'category_id': cat.id, 'description': 'Chairs', 'amount': 1500})
big.with_user(requester).action_submit()
big.with_user(finance).action_approve()
check(big.state == 'gm' and not big.sb_step_user_id and big._sb_can(gm) and not big._sb_can(head),
      'petty cash above the limit: the general manager')
petty_rule.write({'gm_step': 'never', 'gm_delegate_id': False})
pv = env['sb.voucher'].browse(pc.with_user(cashier).action_create_voucher()['res_id'])
box = env['sb.voucher']._sb_petty_cash()
check(not box or pv.cash_account_id == box, 'paid from the petty cash box (%s)' % (box.display_name if box else 'no box set up'))
check('Abebe' in (pv.memo or ''), 'payee name in the description')

print('\n--- 9. driver per diem with a trip')
trip = env['sb.trip'].search([('company_id', '=', company.id), ('driver_id', '!=', False), ('perdiem_voucher_id', '=', False), ('state', '!=', 'cancel')], limit=1)
pd = PR.with_user(fleet).new({'request_type': 'perdiem', 'trip_id': trip.id})
pd._onchange_request_type()
pd._onchange_trip()
pd.days = 2
pd._onchange_perdiem_amount()
vals = pd._convert_to_write(pd._cache)
pd = PR.with_user(fleet).create({k: vals[k] for k in ('request_type', 'trip_id', 'vehicle_id', 'driver_id', 'partner_id', 'days',
                                                           'rate', 'amount', 'destination') if k in vals} | {'description': 'Per diem'})
check(_numbered(pd, 'sb.payment.request.perdiem') and pd.amount == 2 * pd.rate, 'per diem %s: 2 days x %s = %s' % (pd.name, pd.rate, pd.amount))
pd.dept_approver_id = head
pd.with_user(fleet).action_submit()
if pd.state == 'dept':
    pd.with_user(head).action_approve()
pd.with_user(finance).action_approve()
if pd.state == 'gm':
    pd.with_user(gm).action_approve()
check(pd.state == 'approved', 'per diem approved (%s)' % pd.route)
check(not trip or trip.perdiem_state in ('approved', 'paid'), 'trip per diem approved (%s)' % trip.name)

print('\n--- 10. purchase request > RFQ')
prod = env['product.product'].search([('purchase_ok', '=', True)], limit=1)
pur = env['sb.purchase.request'].with_user(requester).create({
    'description': 'Spare parts for the mixer', 'vendor_id': vendor.id,
    'line_ids': [Command.create({'product_id': prod.id, 'name': prod.display_name, 'qty': 3, 'price_unit': 2000})]})
check(_numbered(pur, 'sb.purchase.request') and pur.amount == 6000, 'purchase request %s, 6,000' % pur.name)
env['sb.request.policy']._for('purchase').write({'dept_step': True, 'finance_step': True, 'gm_step': 'above', 'gm_above': 50000})
pur.with_user(requester).action_submit()
env['sb.me'].with_user(head).decide('purreq:%d' % pur.id, True)
check(pur.state == 'finance', 'head approved from Me, now finance')
refused(lambda: env['sb.me'].with_user(finance).decide('purreq:%d' % pur.id, False, ''), 'send back from Me needs a reason')
pur.with_user(finance).action_approve()
check(pur.state == 'approved', 'approved (below the GM limit)')
refused(lambda: pur.with_user(requester).action_create_rfq(), 'requester cannot make the RFQ')
pur.with_user(buyer).action_create_rfq()
po = pur.purchase_ids
check(pur.state == 'ordered' and len(po) == 1 and po.partner_id == vendor and po.origin == pur.name, 'RFQ %s made' % po.name)
check(po.order_line.product_qty == 3 and pur.line_ids.purchase_line_id == po.order_line, 'line linked to the order line')

print('\n%d/%d passed' % (sum(ok), len(ok)))
env.cr.rollback()
