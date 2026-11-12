"""Purchase cycle: PO, advance, two GRNs, over-delivery, one bill, payment with the advance (Eco Green data). Rolls back."""
from odoo.exceptions import UserError, ValidationError, AccessError
from odoo import Command, fields
ok=lambda c,m: print(('PASS ' if c else 'FAIL ')+m)
c=env.company; aml=env['account.move.line']
V=env['sb.voucher']; SV=env['sb.stock.voucher']; PO=env['purchase.order']
vendor=env['res.partner'].search([('supplier_rank','>',0),('ref','=','A-007')],limit=1)
rm=env['product.product'].search([('default_code','=','1501-0001')]); rm2=env['product.product'].search([('default_code','=','1501-0002')])
svc=env['product.product'].create({'name':'Transport (test)','type':'service','purchase_ok':True,'standard_price':0})
po=PO.create({'partner_id':vendor.id,'partner_ref':'SUP-INV-77','order_line':[
  Command.create({'product_id':rm.id,'product_qty':100,'price_unit':12.5}),
  Command.create({'product_id':rm2.id,'product_qty':50,'price_unit':10}),
  Command.create({'product_id':svc.id,'product_qty':1,'price_unit':900})]})
po.button_confirm(); ok(po.state=='purchase','PO confirmed %s total %s'%(po.name,po.amount_total))
ok(po.sb_receipt_status=='none' and po.sb_payment_status=='none','statuses start empty')
bank=env['account.account'].search([('account_type','=','asset_cash')],limit=1)
def pay(amount, po=None, apply=False):
    p=V.create({'voucher_type':'payment','partner_id':vendor.id,'payer_type':'vendor','sb_purchase_id':po.id if po else False,
                'received_amount':amount,'cash_account_id':bank.id})
    if apply:
        p._onchange_partner_load_open_items()
        for ap in p.apply_ids:
            if ap.line_id.move_id in apply:
                ap.amount_applied=ap.amount_residual; ap.pay=True
    p._onchange_sync_single_deposit()
    if not p.cash_line_ids: p.cash_line_ids=[Command.create({'account_id':bank.id,'amount':p.amount_cash})]
    p.action_post(); return p
adv=pay(1000, po)
env.invalidate_all()
ok(po.sb_payment_status=='advance' and abs(po.sb_amount_paid-1000)<0.01 and abs(po.sb_amount_advance-1000)<0.01,'advance 1000 on the PO: %s'%po.sb_payment_status)
# GRN 1: part
a=po.action_sb_receive(); g1=SV.browse(a['res_id'])
ok(len(g1.line_ids)==2 and g1.sb_purchase_id==po,'GRN prefilled with goods still to come (service excluded)')
for l in g1.line_ids: l.quantity = 40 if l.product_id==rm else 50
g1.action_post(); env.invalidate_all()
l1=po.order_line.filtered(lambda l:l.product_id==rm)
ok(abs(l1.sb_qty_received-40)<1e-6 and abs(l1.sb_qty_to_receive-60)<1e-6 and po.sb_receipt_status=='partial','batch 1: 40 of 100, partly received')
ok(abs(g1.line_ids.filtered(lambda l:l.product_id==rm).unit_cost-12.5)<1e-6,'GRN cost = PO price')
# GRN 2: over-receipt
a=po.action_sb_receive(); g2=SV.browse(a['res_id'])
ok(len(g2.line_ids)==1 and abs(g2.line_ids.quantity-60)<1e-6,'second GRN loads only the remaining 60')
g2.line_ids.quantity=70
try: g2.action_post(); ok(False,'over-receipt blocked')
except ValidationError as e: ok(True,'over-receipt blocked: %s'%str(e).split('\n')[0][:110])
g2.line_ids.quantity=60; g2.line_ids.unit_cost=99
g2.action_post(); env.invalidate_all()
ok(po.sb_receipt_status=='full' and abs(g2.line_ids.unit_cost-12.5)<1e-6,'batch 2: fully received, cost kept at PO price')
try:
    po.button_cancel(); ok(False,'cancel blocked')
except UserError: ok(True,'PO with posted GRNs cannot be cancelled')
# bill both GRNs + service
a=po.action_sb_bill(); bill=V.browse(a['res_id'])
print([(l.product_id.name,l.quantity,l.price_unit,l.account_id.code) for l in bill.line_ids], [(l.product_id.name,l.sb_qty_to_bill) for l in po.order_line]); ok(set(bill.sb_grn_ids.ids)=={g1.id,g2.id} and len(bill.line_ids)==4,'one bill for 2 GRNs + the service (%d lines)'%len(bill.line_ids))
bill.action_post(); env.invalidate_all()
grni=c.sb_grni_account_id
res=sum(aml.search([('account_id','=',grni.id),('move_id','in',(g1.move_id|g2.move_id|bill.move_id).ids)]).mapped('amount_residual'))
ok(abs(res)<0.01 and g1.billed and g2.billed,'GRNI cleared for both GRNs, both marked billed')
ok(po.sb_bill_status=='full','PO fully billed; billed %s'%po.sb_amount_billed)
ok(abs(po.sb_amount_due-(po.sb_amount_billed-1000))<0.01,'still to pay = billed - advance: %s'%po.sb_amount_due)
# final payment uses the advance
p2=V.create({'voucher_type':'payment','partner_id':vendor.id,'payer_type':'vendor','cash_account_id':bank.id})
p2._onchange_partner_load_open_items()
for ap in p2.apply_ids:
    if ap.line_id.move_id==bill.move_id or ap.line_id.move_id==adv.move_id:
        ap.amount_applied=ap.amount_residual; ap.pay=True
p2._compute_amounts(); p2.received_amount=p2.amount_cash
p2._onchange_sync_single_deposit()
if not p2.cash_line_ids: p2.cash_line_ids=[Command.create({'account_id':bank.id,'amount':p2.amount_cash})]
p2.action_post(); env.invalidate_all()
ok(bill.move_id.payment_state in ('paid','in_payment'),'bill settled with advance + cash (%s)'%bill.move_id.payment_state)
ok(po.sb_payment_status=='paid' and abs(po.sb_amount_due)<0.01,'PO paid: paid %s billed %s'%(po.sb_amount_paid,po.sb_amount_billed))
for k in ('po_status','po_payments'):
    d=env['sb.report'].get_report(k,{'all_orders':True,'date_from':'2026-07-08','date_to':'2027-07-07'}); ok(any(r.get('label')==po.name for r in d['rows']),'report %s lists the order'%k)
# void GRN frees quantity
env.cr.rollback()
