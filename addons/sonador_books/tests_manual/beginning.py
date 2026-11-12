"""Beginning balances: chart of accounts, customer items, receipts against them. Rolls back.
Run: odoo shell -d <db> < tests_manual/beginning.py"""
from odoo.exceptions import ValidationError, UserError
B=env['sb.beginning']; V=env['sb.voucher']; c=env.company
ok=lambda cond,msg: print(('PASS ' if cond else 'FAIL ')+msg)
eq=c.sb_opening_equity_account_id
def bal(acc): return sum(env['account.move.line'].search([('account_id','=',acc.id),('parent_state','=','posted')]).mapped('balance'))
key='2025-07-07'
cbe=env['account.account'].search([('code','=','11000001')])
e0=bal(eq)
b0=sum(env['account.move.line'].search([('account_id','=',cbe.id),('parent_state','=','posted'),('date','<=',key)]).mapped('balance'))
cap=env['account.account'].search([('account_type','=','equity'),('id','!=',eq.id)],limit=1)
tb=B._opening_tb(); diff=tb['diff']
ok(abs(tb['debit']-tb['credit']-diff)<0.01,'opening trial balance: debits %s, credits %s, out of balance %s'%(tb['debit'],tb['credit'],diff))
try: B.bb_accounts_save(key,{str(cbe.id):b0+1000.0}); ok(False,'unbalanced save refused')
except UserError as e: ok('does not balance' in str(e),'unbalanced opening refused: %s'%str(e)[:110])
c0=sum(env['account.move.line'].search([('account_id','=',cap.id),('parent_state','=','posted'),('date','<=',key)]).mapped('balance'))
# debit the bank, credit capital: balanced once the existing difference is closed too
r=B.bb_accounts_save(key,{str(cbe.id):b0+1000.0-diff,str(cap.id):c0-1000.0})
row=[x for x in r['rows'] if x['id']==cbe.id][0]
ok(abs(row['amount']-(b0+1000-diff))<0.01 and abs(r['tb']['diff'])<0.01,'balanced opening saved: %s, debits = credits = %s'%(row['amount'],r['tb']['debit']))
ok(abs(bal(eq))<0.01 or abs(sum(env['account.move.line'].search([('account_id','=',eq.id),('parent_state','=','posted'),('date','<=',key)]).mapped('balance')))<0.01,
   'clearing account at zero on the opening date')
ok(not [x for x in r['rows'] if x['id']==eq.id],'clearing account not on the grid')
v=V.search([('sb_ob_kind','=','account'),('sb_ob_period','=',key)])
n0=v[-1:].name
r=B.bb_accounts_save(key,{str(cbe.id):b0-diff,str(cap.id):c0})
v=V.search([('sb_ob_kind','=','account'),('sb_ob_period','=',key)])
ok(len(v)==1 and v.name==n0 and abs(r['tb']['diff'])<0.01,'one opening voucher, number kept %s, still balanced'%v.name)
# customer items
p=env['res.partner'].create({'name':'BB Test Customer','customer_rank':1,'ref':'T-001'})
ar=p.property_account_receivable_id
ar0=sum(env['account.move.line'].search([('account_id','=',ar.id),('parent_state','=','posted'),('date','<=',key)]).mapped('balance'))
rows=[{'number':'INV-0901','date':'2025-04-26','po':'PO-7','amount':78080.0,'account_id':ar.id},
      {'number':'INV-0902','date':'2025-05-08','amount':12810.0}]
d=B.bb_partner_save('customer',p.id,rows)
ok(len(d['rows'])==2 and abs(d['balance']-90890)<0.01,'two customer invoices, balance %s'%d['balance'])
ok(d['rows'][0]['period'],'period of item: %s'%d['rows'][0]['period'])
try:
    B.bb_partner_save('customer',p.id,rows+[{'number':'INV-0999','date':'2025-09-01','amount':5}]); ok(False,'late date refused')
except ValidationError as e: ok(True,'late date refused: %s'%e)
lst=B.bb_partners('customer')
me=[x for x in lst['partners'] if x['id']==p.id][0]
ok(abs(me['amount']-90890)<0.01 and me['count']==2,'balances tab')
acc=B.bb_accounts()['rows']; arrow=[x for x in acc if x['id']==ar.id][0]
ok(abs(arrow["amount"]-ar0-90890)<0.01 and arrow['source']=='customer','A/R on the opening balance includes the items: %s'%arrow['amount'])
ob=B._opening_voucher()
ok(len(env['account.move'].search([('journal_id.code','=','OB'),('state','=','posted'),('date','=',key)]))==1 and
   ob.line_ids.filtered(lambda l:l.name=='INV-0901' and l.sb_ob_kind=='customer'),'customer items are lines of the one opening entry %s'%ob.name)
# receipt applying INV-0901
rc=V.create({'voucher_type':'receipt','partner_id':p.id,'date':'2025-08-01','received_amount':30000})
rc._onchange_partner_load_open_items()
ap=rc.apply_ids.filtered(lambda a:a.document=="INV-0901")[:1]; print([(a.document,a.kind,a.invoice_date) for a in rc.apply_ids])
ok(ap and ap.kind=='invoice' and str(ap.invoice_date)=='2025-04-26','apply grid shows INV-0901 dated 26 Apr 2025, kind invoice')
ap.amount_applied=30000; ap.pay=True
rc.cash_line_ids=[(0,0,{'account_id':cbe.id,'amount':30000})] if not rc.cash_line_ids else rc.cash_line_ids
rc.cash_line_ids[:1].write({'account_id':cbe.id})
rc.action_post()
d=B.bb_partner_items('customer',p.id)
r1=[x for x in d['rows'] if x['number']=='INV-0901'][0]
ok(abs(r1['paid']-30000)<0.01,'paid since shows %s'%r1['paid'])
rows=[dict(x) for x in d['rows']]
for x in rows:
    if x['number']=='INV-0901': x['amount']=80000.0; x['po']='PO-8'
d=B.bb_partner_save('customer',p.id,rows)
r1=[x for x in d['rows'] if x['number']=='INV-0901'][0]
ok(abs(r1['amount']-80000)<0.01 and abs(r1['paid']-30000)<0.01,'amount raised, payment still applied')
for x in rows:
    if x['number']=='INV-0901': x['amount']=20000.0
try: B.bb_partner_save('customer',p.id,rows); ok(False,'below paid refused')
except ValidationError as e: ok(True,'below paid refused')
env.invalidate_all()
d=B.bb_partner_items('customer',p.id)
rows=[x for x in d['rows'] if x['number']!='INV-0902']
d=B.bb_partner_save('customer',p.id,rows)
ok(len(d['rows'])==1,'INV-0902 removed')
try: B.bb_partner_save('customer',p.id,[]); ok(False,'paid item removal refused')
except ValidationError: ok(True,'paid item cannot be removed')
h=B.bb_partner_history(p.id)
s=h['sides'][0]
print('history', s['since_label'], s['last_payment'], s['beginning'], s['total_out'], [ (r['label'],r['out']) for r in s['rows'] if r['out']])
rep=env['sb.report']
aged=rep.get_report('aged',{'date_to':'2025-08-31','partner_type':'customer'}) if hasattr(rep,'get_report') else None
print('aged available', bool(aged))
env.cr.rollback()
