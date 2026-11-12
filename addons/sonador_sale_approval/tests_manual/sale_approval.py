# Sales approvals: salesperson confirms -> finance check -> commercial approval -> confirmed. Rolled back.
#   ~/ecogreen-odoo19/sync/local_shell.sh < ~/sonador-odoo/addons/sonador_sale_approval/tests_manual/sale_approval.py
from odoo import Command
from odoo.exceptions import UserError
ok = []


def check(c, m):
    print(('PASS ' if c else 'FAIL ') + m)
    ok.append(bool(c))


def refused(fn, m):
    try:
        with env.cr.savepoint():
            fn()
    except UserError as e:
        check(True, '%s (refused: %s)' % (m, str(e)[:80]))
        return
    check(False, m + ' (was allowed)')


c = env.company
U = env['res.users'].with_context(no_reset_password=True)
mk = lambda n, gs: U.create({'name': 'T %s' % n, 'login': 't_soa_%s@example.local' % n, 'company_id': c.id, 'company_ids': [Command.set([c.id])],
                             'group_ids': [Command.set([env.ref('base.group_user').id] + [env.ref(x).id for x in gs])]})
sp = mk('sales', ['sonador_sales.group_sb_sales_user'])
fin = mk('fin', ['sonador_sale_approval.group_so_finance', 'sonador_sales.group_sb_sales_user'])
com = mk('com', ['sonador_sale_approval.group_so_commercial', 'sonador_sales.group_sb_sales_user'])
cust = env['res.partner'].create({'name': 'T SOA customer'})
prod = env['product.product'].search([('sale_ok', '=', True)], limit=1)


def order(amount):
    return env['sale.order'].with_user(sp).create({'partner_id': cust.id, 'order_line': [Command.create({'product_id': prod.id, 'product_uom_qty': 1, 'price_unit': amount})]})


c.write({'sb_so_approval': False})
o0 = order(100)
o0.with_user(sp).action_confirm()
check(o0.state == 'sale', 'switch off: confirmed at once')
c.write({'sb_so_approval': True, 'sb_so_finance_step': True, 'sb_so_commercial_step': True, 'sb_so_approval_above': 0})
o = order(50000)
o.with_user(sp).action_confirm()
check(o.state == 'draft' and o.sb_appr_state == 'finance', 'confirm = submitted for the finance check')
refused(lambda: o.with_user(sp).action_confirm(), 'confirming again while waiting')
refused(lambda: o.with_user(com).action_sb_so_approve(), 'commercial cannot do the finance check')
check(any(i['key'] == 'so:%d' % o.id for i in env['sb.me'].with_user(fin).approvals()), 'in finance\'s Me inbox')
env['sb.me'].with_user(fin).decide('so:%d' % o.id, True)
check(o.sb_appr_state == 'commercial' and o.sb_finance_by == fin, 'finance checked from Me')
refused(lambda: o.with_user(com).action_sb_so_send_back(), 'send back needs a reason')
o.with_user(com).action_sb_so_send_back('Price below the list')
check(o.sb_appr_state == 'sent_back' and o.state == 'draft', 'sent back with the reason')
o.with_user(sp).action_confirm()
o.with_user(fin).action_sb_so_approve()
o.with_user(com).action_sb_so_approve()
check(o.sb_appr_state == 'approved' and o.state == 'sale' and o.sb_commercial_by == com, 'commercial approval confirms the order')
c.sb_so_approval_above = 1000
o2 = order(500)
o2.with_user(sp).action_confirm()
check(o2.state == 'sale', 'at or below the limit: confirmed at once')
print('%d/%d passed' % (sum(ok), len(ok)))
env.cr.rollback()
