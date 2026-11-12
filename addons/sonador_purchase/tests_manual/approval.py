from odoo import Command
from odoo.exceptions import UserError
ok=lambda c,m: print(('PASS ' if c else 'FAIL ')+m)
U=env['res.users']; g=lambda x: env.ref(x).id
clerk=U.create({'name':'T Clerk','login':'t_pc','group_ids':[(6,0,[g('sonador_purchase.group_sb_purchase_user')])]})
pm=U.create({'name':'T PM','login':'t_pm','group_ids':[(6,0,[g('sonador_purchase.group_sb_purchase_user'),g('purchase.group_purchase_manager')])]})
fin=U.create({'name':'T Fin','login':'t_fin','group_ids':[(6,0,[g('sonador_purchase.group_sb_purchase_user'),g('sonador_books.group_sb_manager')])]})
gm=U.create({'name':'T GM','login':'t_gm','group_ids':[(6,0,[g('sonador_purchase.group_sb_po_gm')])]})
print([r.display_name+' -> '+r.group_id.name for r in env['sb.po.approval.rule'].search([])])
vendor=env['res.partner'].search([('supplier_rank','>',0)],limit=1)
item=env['product.product'].search([('default_code','=','GEN-PURCHASE')])
def po(amount, user):
    o=env['purchase.order'].with_user(user).create({'partner_id':vendor.id,'order_line':[Command.create({'product_id':item.id,'product_qty':1,'price_unit':amount,'tax_ids':[Command.set([])]})]})
    o.button_confirm(); return o
o=po(1500,clerk); ok(o.state=='purchase','1,500: clerk confirms directly (%s)'%o.sb_approved_by.name)
o=po(15000,clerk); ok(o.state=='to approve' and o.sb_approver_group_id.id==g('purchase.group_purchase_manager'),'15,000: waits for the purchase manager')
try: o.with_user(clerk).button_approve(); ok(False,'clerk blocked')
except UserError as e: ok(True,'clerk cannot approve: %s'%str(e)[:80])
o.with_user(pm).button_approve(); ok(o.state=='purchase' and o.sb_approved_by==pm,'purchase manager approves 15,000')
o=po(30000,pm); ok(o.state=='to approve','30,000 by the purchase manager waits for finance')
try: o.with_user(pm).button_approve(); ok(False,'pm blocked')
except UserError: ok(True,'purchase manager cannot approve 30,000')
o.with_user(fin).button_approve(); ok(o.state=='purchase','finance (Books manager) approves 30,000')
o=po(80000,fin); ok(o.state=='to approve','80,000 waits for the general manager')
o.with_user(gm).button_approve(); ok(o.state=='purchase','general manager approves 80,000')
o=po(5000,gm); ok(o.state=='purchase','general manager confirms 5,000 directly (higher band covers lower)')
env.cr.rollback()
