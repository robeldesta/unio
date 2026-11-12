"""Purchase clerk and store clerk on purchase orders (Eco Green data). Rolls back."""
from odoo.exceptions import UserError, AccessError, ValidationError
from odoo import Command
ok=lambda c,m: print(('PASS ' if c else 'FAIL ')+m)
U=env['res.users']
pu=U.create({'name':'T Purch','login':'t_purch','group_ids':[(6,0,[env.ref('sonador_purchase.group_sb_purchase_user').id])]})
st=U.create({'name':'T Store','login':'t_store2','group_ids':[(6,0,[env.ref('sonador_inventory.group_sb_store_user').id])]})
def roots(u):
    m=env['ir.ui.menu'].with_user(u).load_menus(False); return sorted(m[i]['name'] for i in m['root']['children'])
print('purchase clerk sees', roots(pu)); ok('Purchase' in roots(pu) and 'Accounting' not in roots(pu),'purchase clerk: Purchase app, no Books')
vendor=env['res.partner'].search([('supplier_rank','>',0),('ref','=','A-005')],limit=1)
rm=env['product.product'].search([('default_code','=','1501-0003')])
po=env['purchase.order'].with_user(pu).create({'partner_id':vendor.id,'order_line':[Command.create({'product_id':rm.id,'product_qty':20,'price_unit':11})]})
po.button_confirm(); ok(po.state=='purchase','purchase clerk raises and confirms %s'%po.name)
d=env['sb.app.home'].with_user(pu).get_home('purchase'); ok(d['queues'] and 'Advance Payment' not in [t['label'] for f in d['flows'] for t in f],'purchase home renders without payment tiles for a non-finance user')
try: po.with_user(pu).action_sb_bill(); ok(False,'purchase clerk cannot bill')
except (UserError,AccessError): ok(True,'purchase clerk cannot create bills')
a=po.with_user(st).action_sb_receive(); g=env['sb.stock.voucher'].with_user(st).browse(a['res_id'])
g.line_ids.quantity=8; g.action_post(); env.invalidate_all()
ok(po.sb_receipt_status=='partial' and abs(po.order_line.sb_qty_received-8)<1e-6,'store clerk receives 8 of 20 against the PO')
ds=env['sb.app.home'].with_user(st).get_home('store'); ok(any(q['key']=='expected' and q['count']>=1 for q in ds['queues']),'store home lists expected deliveries')
# GRN chosen from the Inventory app by picking the PO
g2=env['sb.stock.voucher'].with_user(st).new({'voucher_type':'grn','partner_id':vendor.id,'sb_purchase_id':po.id})
g2._onchange_sb_purchase(); ok(len(g2.line_ids)==1 and abs(g2.line_ids.quantity-12)<1e-6,'picking the PO on a new GRN loads the 12 still to come')
# void the GRN frees the quantity again
g.with_user(env.ref('base.user_admin')).action_void(); env.invalidate_all()
ok(po.sb_receipt_status=='none' and abs(po.order_line.sb_qty_to_receive-20)<1e-6,'voiding the GRN puts the 8 back to receive')
env.cr.rollback()
