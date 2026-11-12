"""Sales and Inventory apps as a store clerk and a sales clerk (Eco Green data). Rolls back."""
from odoo.exceptions import AccessError, UserError
ok=lambda c,m: print(('PASS ' if c else 'FAIL ')+m)
U=env['res.users']
store=U.create({'name':'Test Store','login':'t_store','group_ids':[(6,0,[env.ref('sonador_inventory.group_sb_store_user').id])]})
sales=U.create({'name':'Test Sales','login':'t_sales','group_ids':[(6,0,[env.ref('sonador_sales.group_sb_sales_user').id])]})
def roots(u):
    m=env['ir.ui.menu'].with_user(u).load_menus(False)
    return sorted(m[i]['name'] for i in m['root']['children'])
print('store sees', roots(store)); print('sales sees', roots(sales))
ok('Accounting' not in roots(store) and 'Inventory' in roots(store),'store clerk: Inventory app, no Books')
ok('Accounting' not in roots(sales) and 'Sales' in roots(sales),'sales clerk: Sales app, no Books')
H=env['sb.app.home']
for u,app in ((store,'store'),(sales,'sales'),(env.user,'store'),(env.user,'sales')):
    d=H.with_user(u).get_home(app); print(app,u.login,[ (q['title'],q['count']) for q in d['queues']], [t['label'] for f in d['flows'] for t in f])
# store: GIV of a stocked item
SV=env['sb.stock.voucher'].with_user(store)
p=env['product.product'].search([('is_storable','=',True),('qty_available','>',10)],limit=1)
giv=SV.create({'voucher_type':'giv','department':'Factory','line_ids':[(0,0,{'product_id':p.id,'quantity':1})]})
giv.action_post(); ok(giv.state=='posted','store clerk posts GIV %s'%giv.name)
act=giv.action_gate_pass(); g=env['sb.gate.pass'].with_user(store).browse(act['res_id'])
ok(g.line_ids.product_id==p and g.purpose=='issue','gate pass from GIV with its items')
g.vehicle='3-12345'; g.action_issue(); ok(g.name.startswith('GP/'),'issued %s'%g.name)
pdf=env['ir.actions.report'].with_user(store)._render_qweb_pdf('sonador_inventory.report_gate_pass',g.ids)[0]; ok(pdf[:4]==b'%PDF','gate pass prints')
pdf=env['ir.actions.report'].with_user(store)._render_qweb_pdf('sonador_inventory.report_stock_voucher',giv.ids)[0]; ok(pdf[:4]==b'%PDF','GIV prints')
grn=env['sb.stock.voucher'].search([('voucher_type','=','grn'),('state','=','posted')],limit=1)
pdf=env['ir.actions.report'].with_user(store)._render_qweb_pdf('sonador_inventory.report_stock_voucher',grn.ids)[0]; ok(pdf[:4]==b'%PDF','GRN prints')
fp=env['sb.stock.voucher'].search([('voucher_type','in',('fprr','build')),('state','=','posted')],limit=1)
pdf=env['ir.actions.report'].with_user(store)._render_qweb_pdf('sonador_inventory.report_stock_voucher',fp.ids)[0]; ok(pdf[:4]==b'%PDF','FPRR prints')
g.action_out(); ok(g.state=='out','passed the gate')
try:
    giv.with_user(store).action_void(); ok(False,'store cannot void')
except (UserError,AccessError): ok(True,'store clerk cannot void')
adj=SV.create({'voucher_type':'adjustment','line_ids':[(0,0,{'product_id':p.id,'qty_counted':1})]})
try: adj.action_post(); ok(False,'store cannot adjust')
except (UserError,AccessError): ok(True,'store clerk cannot post stock adjustments')
# sales: invoice + cash sale
V=env['sb.voucher'].with_user(sales)
cust=env['res.partner'].search([('customer_rank','>',0),('name','not like','Unallocated')],limit=1)
fg=env['product.product'].search([('default_code','=like','1504-%'),('qty_available','>',5)],limit=1)
inv=V.create({'voucher_type':'sale','partner_id':cust.id,'sb_deliver_later':True,'line_ids':[(0,0,{'product_id':fg.id,'quantity':1,'price_unit':100})]})
for l in inv.line_ids: l._onchange_product()
inv.line_ids.price_unit=100
inv.action_post(); ok(inv.state=='posted','sales clerk posts a sales invoice %s'%inv.name)
rc=V.create({'voucher_type':'receipt','partner_id':cust.id,'received_amount':10})
try: rc.action_post(); ok(False,'sales cannot post receipts')
except (UserError,AccessError) as e: ok(True,'sales clerk cannot post receipts')
do=env['sb.delivery.order'].search([('voucher_ids','in',inv.id)],limit=1)
print('DO', do.name, do.state)
if do:
    b=do.with_user(sales).action_new_batch() if hasattr(do,'action_new_batch') else None
    batch=env['sb.delivery.batch'].search([('delivery_id','=',do.id)],limit=1)
    if batch:
        batch.with_user(store).write({'vehicle':'3-55555','driver':'Abebe'})
        for l in batch.line_ids: l.quantity=l.quantity or 1
        a=batch.with_user(store).action_gate_pass(); gp=env['sb.gate.pass'].browse(a['res_id'])
        ok(gp.vehicle=='3-55555' and gp.purpose=='delivery' and gp.line_ids,'gate pass from the truck load (%s)'%gp.source)
        batch.with_user(store).action_validate(); ok(batch.state=='done' and do.state=='done','store clerk dispatches the truck: %s %s'%(batch.state, do.state))
env.cr.rollback()
