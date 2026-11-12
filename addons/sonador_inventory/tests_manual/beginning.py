"""Inventory beginning balances (Eco Green data). Rolls back. Run: odoo shell -d ecogreen < tests_manual/beginning.py"""
from odoo.exceptions import ValidationError
B=env['sb.beginning']; S=env['sb.stock']; c=env.company
ok=lambda cond,msg: print(('PASS ' if cond else 'FAIL ')+msg)
aml=env['account.move.line']
def gl(acc): return round(sum(aml.search([('account_id','=',acc.id),('parent_state','=','posted')]).mapped('balance')),2)
key='2025-07-07'
it=B.bb_items(key); rows={r['id']:r for r in it['rows']}
r=[x for x in it['rows'] if x['qty']>0][0]; p=env['product.product'].browse(r['id'])
acc=S._valuation_account(p,c); eq=c.sb_opening_equity_account_id
g0, e0, oh0 = gl(acc), gl(eq), p.qty_available
res=B.bb_items_save(key,[{'id':p.id,'qty':r['qty']+10,'value':round(r['value']+1000,2)}])
n=[x for x in res['rows'] if x['id']==p.id][0]
ok(abs(n['qty']-(r['qty']+10))<1e-6 and abs(n['value']-(r['value']+1000))<0.01,'item %s changed to %s / %s'%(p.default_code,n['qty'],n['value']))
env.invalidate_all()
ok(abs(gl(acc)-(g0+1000))<0.01 and abs(gl(eq)-(e0-1000))<0.01,'GL inventory +1000, opening equity -1000')
ok(abs(p.qty_available-(oh0+10))<1e-6,'on hand +10')
v=env['sb.stock.voucher'].search([('voucher_type','=','opening'),('sb_ob_period','=',key)])
ok(len(v)==1 and abs(v.amount_total-(it['total']+1000))<0.01,'OS total %s'%v.amount_total)
ok(abs(res['not_itemised']-it['not_itemised'])<0.01,'not itemised unchanged')
# back
res=B.bb_items_save(key,[{'id':p.id,'qty':r['qty'],'value':r['value']}])
env.invalidate_all()
ok(abs(gl(acc)-g0)<0.01 and abs(p.qty_available-oh0)<1e-6,'restored')
# new period: Before
q=[x for x in it['rows'] if not x['qty']][0]
res=B.bb_items_save('2024-07-07',[{'id':q['id'],'qty':5,'value':500}])
ok(abs(res['total']-500)<0.01 and res['vouchers'],'before-period OS created %s'%res['vouchers'])
a=B.bb_accounts('2024-07-07'); qa=S._valuation_account(env['product.product'].browse(q['id']),c)
row=[x for x in a['rows'] if x['id']==qa.id][0]
ok(abs(row['amount']-500)<0.01 and abs(row['sub']-500)<0.01,'account grid shows items 500 on %s'%qa.code)
try: B.bb_items_save(key,[{'id':q['id'],'qty':0,'value':10}]); ok(False,'value without qty')
except ValidationError: ok(True,'value without quantity refused')
# account grid typing a total on an inventory account keeps the item part
res=B.bb_accounts_save('2024-07-07',{str(qa.id):800})
row=[x for x in res['rows'] if x['id']==qa.id][0]
ok(abs(row['amount']-800)<0.01 and abs(row['sub']-500)<0.01,'typed 800: items 500 + not itemised 300')
env.cr.rollback()
