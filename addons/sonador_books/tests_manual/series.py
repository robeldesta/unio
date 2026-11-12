import re

from odoo.exceptions import UserError, ValidationError
from odoo import Command, fields
ok=lambda c,m: print(('PASS ' if c else 'FAIL ')+m)
S=env['sb.doc.series']; V=env['sb.voucher']; SV=env['sb.stock.voucher']
print('series:', [(s.doc_type,s.kind,s.prefix,s.next_preview) for s in S.search([])])
bank=env['account.account'].search([('account_type','=','asset_cash')],limit=1)
cust=env['res.partner'].search([('customer_rank','>',0),('ref','=','A-040')],limit=1)
inc=env['account.account'].search([('code','=','4000-9999')])
def receipt(pad=None, series=None):
    r=V.create({'voucher_type':'receipt','partner_id':cust.id,'payer_type':'customer','received_amount':100,'cash_account_id':bank.id,
                'sb_pad_number':pad,'sb_series_id':series.id if series else False,
                'line_ids':[Command.create({'account_id':inc.id,'amount':100,'name':'test'})]})
    r._onchange_sync_single_deposit()
    if not r.cash_line_ids: r.cash_line_ids=[Command.create({'account_id':bank.id,'amount':100})]
    r.action_post(); return r
crv=S._for('receipt'); n0=crv._next_number(fields.Date.today())
r1=receipt(); ok(r1.name==crv._format(n0,fields.Date.today()),'receipt numbered from the series: %s'%r1.name)
try: receipt(pad=str(n0+3)); ok(False,'jump blocked')
except UserError as e: ok(True,'jumping ahead refused: %s'%str(e)[:120])
try: receipt(pad=str(n0)); ok(False,'reuse blocked')
except UserError as e: ok(True,'reusing a number refused')
r2=receipt(pad=str(n0+1)); ok(r2.name.endswith(str(n0+1).zfill(4)),'pad number equal to the next one accepted: %s'%r2.name)
sp=env['sb.doc.series.spoil'].create({'series_id':crv.id,'count':2,'reason':'torn'}); ok(sp.number_from.endswith(str(n0+2).zfill(4)) and sp.number_to.endswith(str(n0+3).zfill(4)),'2 spoiled numbers used up in order: %s-%s'%(sp.number_from,sp.number_to))
r3=receipt(); ok(r3.name.endswith(str(n0+4).zfill(4)),'next receipt continues after the spoiled ones: %s'%r3.name)
r2.sb_void_reason='test'; r2.action_void(); ok(r2.state=='void' and r2.name.endswith(str(n0+1).zfill(4)),'voided receipt keeps its number')
r3.action_edit(); r3.action_post(); ok(r3.name.endswith(str(n0+4).zfill(4)),'edit and repost keeps the number')
# second pad for cash sales: a fiscal cash register
fs=S.create({'name':'Cash register 2','doc_type':'cash_sale','prefix':'FS','padding':8,'number_from':8121})
fg=env['product.product'].search([('default_code','=like','1504-%'),('qty_available','>',5)],limit=1)
cs=V.create({'voucher_type':'cash_sale','partner_id':cust.id,'sb_series_id':fs.id,'sb_pad_number':'FS00008121','cash_account_id':bank.id,
             'line_ids':[Command.create({'product_id':fg.id,'quantity':1,'price_unit':100})]})
cs.line_ids._onchange_product(); cs.line_ids.price_unit=100
cs._onchange_sync_single_deposit()
if not cs.cash_line_ids: cs.cash_line_ids=[Command.create({'account_id':bank.id,'amount':cs.amount_total})]
cs.action_post(); ok(cs.name=='FS00008121','cash sale on the cash register series: %s'%cs.name)
cs.sb_void_reason='test'; cs.action_void()
cs2=V.create({'voucher_type':'cash_sale','partner_id':cust.id,'sb_series_id':fs.id,'cash_account_id':bank.id,'sb_pad_number':'FS00008122',
              'line_ids':[Command.create({'product_id':fg.id,'quantity':1,'price_unit':100})]})
cs2.line_ids._onchange_product(); cs2.line_ids.price_unit=100
cs2._onchange_sync_single_deposit()
if not cs2.cash_line_ids: cs2.cash_line_ids=[Command.create({'account_id':bank.id,'amount':cs2.amount_total})]
cs2.action_post()
ok(cs2.name=='FS00008122' and cs.move_id.reversal_move_ids.name=='VOID/FS00008121',
   'a void takes no number from the series: next %s, reversal %s'%(cs2.name,cs.move_id.reversal_move_ids.name))
# GRN regular / DRM
vendor=env['res.partner'].search([('supplier_rank','>',0),('ref','=','A-007')],limit=1)
rm=env['product.product'].search([('default_code','=','1501-0001')])
g=SV.create({'voucher_type':'grn','partner_id':vendor.id,'sb_doc_kind':'drm','line_ids':[Command.create({'product_id':rm.id,'quantity':5,'unit_cost':10})]})
g.action_post(); ok(g.sb_series_id.kind=='drm' and g.name==g.sb_series_id._format(g.sb_series_id._parse(g.name),g.date),'DRM GRN numbered in the DRM series: %s'%g.name)
g2=SV.create({'voucher_type':'grn','partner_id':vendor.id,'line_ids':[Command.create({'product_id':rm.id,'quantity':5,'unit_cost':10})]})
g2.action_post(); ok(g2.sb_series_id.kind=='regular' and g2.sb_series_id.doc_type=='grn','regular GRN in the regular series: %s'%g2.name)
gv=SV.create({'voucher_type':'giv','sb_doc_kind':'drm','department':'Mixing','line_ids':[Command.create({'product_id':rm.id,'quantity':2})]})
gv.action_post(); ok(gv.sb_series_id.kind=='drm' and gv.sb_series_id.doc_type=='giv','DRM GIV: %s'%gv.name)
# SO: draft number, final number on confirmation
company=env.company; mode=(company.sb_so_numbering, company.sb_po_numbering, company.sb_number_without_year)
company.write({'sb_so_numbering':'confirm','sb_po_numbering':'confirm','sb_number_without_year':False})
so=env['sale.order'].create({'partner_id':cust.id,'order_line':[Command.create({'product_id':fg.id,'product_uom_qty':1,'price_unit':100})]})
q=so.name; so.with_context(sb_so_approved=True).action_confirm(); ok(so.sb_series_id.doc_type=='sale_order' and so.name==so.sb_series_id._format(so.sb_series_id._parse(so.name),so.sb_confirm_date) and so.sb_quotation_number==q,'sales order numbered on confirmation %s (quotation %s)'%(so.name,q))
so._action_cancel() if hasattr(so,'_action_cancel') else so.action_cancel()
try: so.unlink(); ok(False,'SO delete blocked')
except UserError: ok(True,'numbered sales order cannot be deleted (cancel only)')
# PO
po=env['purchase.order'].create({'partner_id':vendor.id,'order_line':[Command.create({'product_id':rm.id,'product_qty':3,'price_unit':10})]})
rfq=po.name; po.with_user(env.ref('base.user_admin')).button_confirm(); ok(po.sb_series_id.doc_type=='purchase_order' and rfq.startswith('RFQ/') and po.sb_rfq_number==rfq,'PO numbered on approval %s (RFQ %s)'%(po.name,rfq))
# number on creation (as one series), as Odoo 18 did: confirming / approving keeps it, a cancelled draft keeps its number
company.write({'sb_so_numbering':'create','sb_po_numbering':'create'})
so_s=S._for('sale_order'); nxt=so_s._format(so_s._next_number(fields.Date.today()),fields.Date.today())
so=env['sale.order'].create({'partner_id':cust.id,'order_line':[Command.create({'product_id':fg.id,'product_uom_qty':1,'price_unit':100})]})
ok(so.name==nxt and so.state=='draft' and so.sb_series_id==so_s,'sales order numbered on creation: %s'%so.name)
n=so.name; so.with_context(sb_so_approved=True).action_confirm(); ok(so.state=='sale' and so.name==n and not so.sb_quotation_number,'confirming keeps the number: %s (%s)'%(so.name,so.state))
so2=env['sale.order'].create({'partner_id':cust.id}); n2=so2.name; so2.action_cancel()
ok(so2.name==n2 and so2.name!=so.name,'a cancelled draft keeps its number: %s'%so2.name)
try: so2.unlink(); ok(False,'numbered draft delete blocked')
except UserError: ok(True,'a numbered draft cannot be deleted (cancelled, its number stays used)')
so3=env['sale.order'].create({'partner_id':cust.id}); ok(so_s._parse(so3.name)==so_s._parse(n2)+1,'the next order goes on after the cancelled one: %s'%so3.name)
po_s=S._for('purchase_order')
po=env['purchase.order'].create({'partner_id':vendor.id,'order_line':[Command.create({'product_id':rm.id,'product_qty':3,'price_unit':10})]})
n=po.name; ok(po.sb_series_id==po_s and n==po_s._format(po_s._parse(n),fields.Date.today()) and not n.startswith('RFQ'),'purchase order numbered on creation: %s'%n)
po.with_user(env.ref('base.user_admin')).button_confirm(); ok(po.name==n and not po.sb_rfq_number,'approval keeps the number: %s (%s)'%(po.name,po.state))
# numbers without the year: plain documents count on after the highest number, with or without the year
company.sb_number_without_year=True
nm=S._sb_plain_next('sb.stock.voucher','TRF',fields.Date.today(),company); ok(re.match(r'^TRF/\d{4}$',nm) is not None,'no year in a stock transfer number: %s'%nm)
company.sb_number_without_year=False
nm=S._sb_plain_next('sb.stock.voucher','TRF',fields.Date.today(),company); ok(re.match(r'^TRF/\d{2}-\d{2}/\d{4}$',nm) is not None or re.match(r'^TRF/\d{4}/\d{4}$',nm) is not None,'with the year by default: %s'%nm)
company.write({'sb_so_numbering':mode[0],'sb_po_numbering':mode[1],'sb_number_without_year':mode[2]})
gs=S._for('gate_pass')
if gs:
    gp=env['sb.gate.pass'].create({'line_ids':[Command.create({'name':'Test'})]}); want=gs._format(gs._next_number(fields.Date.today()),fields.Date.today())
    gp.action_issue(); ok(gp.name==want,'gate pass numbered in its series: %s'%gp.name)
# van
if 'sb.van.trip' in env:
    van=env['sb.van'].search([('salesperson_id','!=',False)],limit=1) if 'salesperson_id' in env['sb.van']._fields else env['sb.van'].search([],limit=1)
    if van:
        t=env['sb.van.trip'].create({'van_id':van.id,'line_ids':[Command.create({'product_id':fg.id,'qty_loaded':1})]})
        t.action_load() if hasattr(t,'action_load') else None
        vs=S._for('van_trip'); ok(t.name.startswith(vs.prefix if vs else 'VAN/'),'van sales order numbered when loaded: %s'%t.name)
d=env['sb.report'].get_report('numbering',{'date_from':'2026-07-08','date_to':'2027-07-07'})
bad=[r['values'] for r in d['rows'] if r['values'].get('status')=='Fix']
ok(not bad,'numbering check: no gaps, no duplicates (%d series)'%len(d['rows']))
crv._counter(fields.Date.today()).next_number += 1
d=env['sb.report'].get_report('numbering',{})
ok(any(r['values'].get('status')=='Fix' and r['values']['gaps'] for r in d['rows']),'a number bumped by hand shows as a gap: %s'%[r['values']['gaps'] for r in d['rows'] if r['values'].get('gaps')])
env.cr.rollback()
