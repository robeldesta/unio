"""Round 5 financial-report fixes, as normal users (r5.* test logins: password <login>-R5test, local DB only).
Run: ~/ecogreen-odoo19/sync/local_shell.sh < tests_manual/reports_round5.py   (everything is rolled back)"""
# ---- t1.py
U=lambda l: env['res.users'].search([('login','=',l)])
acc,clerk,aud,hr,mgr=[U(x) for x in ('r5.accountant','r5.clerk','r5.auditor','r5.hr','r5.manager')]
print('== 2b payroll data')
for u in (aud,clerk,acc,mgr,hr):
    E=env['sb.employee'].with_user(u); L=env['sb.payroll.line'].with_user(u); Rn=env['sb.payroll.run'].with_user(u)
    c=env['sb.report'].with_user(u).get_center_data('payroll')
    print(u.login, E.search_count([]), L.search_count([]), Rn.search_count([]), 'center', len(c.get('employees',[])), len(c.get('runs',[])), c.get('restricted'))
# payroll menu visible?
for u in (acc,hr):
    m=env.ref('sonador_books.menu_sb_payroll_root')
    print('menu', u.login, m.id in env['ir.ui.menu'].with_user(u)._visible_menu_ids())
# a payroll payment voucher readable by accountant
v=env['sb.voucher'].search([('payroll_run_id','!=',False)],limit=1)
if v:
    try:
        d=env['sb.voucher'].with_user(acc).browse(v.id).web_read({'name':{},'payroll_run_id':{'fields':{'display_name':{}}}})
        print('voucher with payroll run read by accountant:', d[0]['name'], d[0]['payroll_run_id'])
    except Exception as e: print('voucher read ERR', type(e).__name__, str(e)[:200])
print('== 2a packs')
from odoo.exceptions import ValidationError, AccessError, UserError
P=env['sb.report.pack']
key=next(k for k,v in __import__('odoo.addons.sonador_books.models.report_catalog',fromlist=['x']).CATALOG_BY_KEY.items() if v['report']=='payroll_register' and v['priority']==1)
key2=next(k for k,v in __import__('odoo.addons.sonador_books.models.report_catalog',fromlist=['x']).CATALOG_BY_KEY.items() if v['report']=='trial_balance' and v['priority']==1)
print('keys', key, key2)
try:
    with env.cr.savepoint():
        P.with_user(acc).create({'name':'Acc pack','schedule':'monthly','partner_ids':[(6,0,[acc.partner_id.id])],'line_ids':[(0,0,{'report_key':key}),(0,0,{'report_key':key2})]})
    print('accountant payroll pack: CREATED (bad)')
except (ValidationError, AccessError) as e: print('accountant payroll pack refused:', str(e)[:160])
try:
    with env.cr.savepoint():
        P.with_user(hr).create({'name':'HR pack to accountant','schedule':'monthly','partner_ids':[(6,0,[acc.partner_id.id])],'line_ids':[(0,0,{'report_key':key})]})
    print('HR pack to non-HR recipient: CREATED (bad)')
except ValidationError as e: print('HR pack to non-HR refused:', str(e)[:160])
p=P.with_user(hr).create({'name':'HR pack','schedule':'monthly','partner_ids':[(6,0,[hr.partner_id.id])],'line_ids':[(0,0,{'report_key':key}),(0,0,{'report_key':key2})]})
docs,_,_=p.sudo()._docs(sending=True); print('HR pack rendered by system as owner:', [d['key'] for d in docs])
# a pack made earlier (before the check) by an accountant: rendered as its owner, payroll left out
q=P.sudo().create({'name':'Old pack','schedule':'monthly','line_ids':[(0,0,{'report_key':key2})]})
env.cr.execute("insert into sb_report_pack_line(pack_id, report_key, sequence) values (%s,%s,1)", (q.id, key))
env.cr.execute("update sb_report_pack set create_uid=%s where id=%s", (acc.id, q.id)); env.invalidate_all()
docs,_,_=q.sudo()._docs(sending=True); print('legacy accountant pack, cron render:', [d['key'] for d in docs])
docs,_,_=q.with_user(hr)._docs(); print('same pack printed on demand by HR:', [d['key'] for d in docs])
env.cr.rollback()
# ---- t2.py
from odoo.exceptions import ValidationError
U=lambda l: env['res.users'].search([('login','=',l)])
acc=U('r5.accountant')
try:
    with env.cr.savepoint():
        env['sb.report.pack'].with_user(acc).create({'name':'Acc pack','line_ids':[(0,0,{'report_key':'payroll_register'})]})
    print('created (bad)')
except ValidationError as e: print('refused:', str(e)[:120])
v=env['sb.voucher'].search([('voucher_type','=','payment'),('state','=','posted')],limit=1)
run=env['sb.payroll.run'].search([],limit=1)
env.cr.execute('update sb_voucher set payroll_run_id=%s where id=%s',(run.id,v.id)); env.invalidate_all()
try:
    d=env['sb.voucher'].with_user(acc).browse(v.id).web_read({'name':{},'payroll_run_id':{'fields':{'display_name':{}}}})
    print('voucher read by accountant:', d[0]['name'], d[0]['payroll_run_id'])
except Exception as e: print('voucher read ERR', type(e).__name__, str(e)[:200])
env.cr.rollback()
# ---- t3.py
from odoo import fields
U=lambda l: env['res.users'].search([('login','=',l)])
acc=U('r5.accountant'); R=env['sb.report'].with_user(acc)
def row(doc, pred):
    return next((r for r in doc['rows'] if pred(r)), None)
print('== 5 P&L drill FY2017')
o={'date_from':'2024-07-08','date_to':'2025-07-07'}
pl=R.get_report('profit_loss', o)
r=row(pl, lambda r: r.get('code')=='4000-0001')
print('P&L row', r['values']['p0'], 'drill', r['drill'])
gl=R.get_report('general_ledger', dict(r['drill'], comparison='none'), expanded=['a%d'%r['drill']['account_ids'][0]])
c=[x for x in gl['rows'] if x.get('closing')]
print('GL ends', c[0]['values']['balance'] if c else None, 'chips', gl['chips'])
print('== 4 prior years after reopen')
cl=env['account.move'].search([('sb_closing','=',True)],limit=1)
print('closing', cl.name, cl.date)
from odoo.addons.sonador_books.models.period import YEAR_END
env.company.sudo().with_context(sb_year_end=YEAR_END).fiscalyear_lock_date=False
rev=cl.sudo().with_context(sb_year_end=YEAR_END)._reverse_moves([{'date': fields.Date.to_date('2025-12-01'), 'ref':'reopen test'}]); rev.sb_closing=True; rev.with_context(sb_year_end=YEAR_END).action_post()
print('reversal', rev.name, rev.date, rev.state)
fy=R._fy_start(fields.Date.to_date('2026-07-08'))
d=R._prior_results_drill(fy); print('drill after reopen', d['date_from'], '..', d['date_to'], 'OK' if d['date_from']<=d['date_to'] else 'INVERTED')
print('== 6 analytic split')
an=env['account.analytic.account'].search([],limit=2)
print('analytics', an.mapped('name'))
if len(an)==2:
    a_exp=env['account.account'].search([('account_type','=','expense')],limit=1); a_cash=env['account.account'].search([('account_type','=','asset_cash')],limit=1)
    m=env['account.move'].sudo().create({'move_type':'entry','date':'2026-08-01','journal_id':env['account.journal'].search([('type','=','general')],limit=1).id,
      'line_ids':[(0,0,{'account_id':a_exp.id,'debit':1000,'analytic_distribution':{str(an[0].id):50,str(an[1].id):50}}),(0,0,{'account_id':a_cash.id,'credit':1000})]})
    m.action_post()
    o={'date_from':'2026-07-08','date_to':'2026-10-31','move_ids':[m.id]}
    vals=[]
    for ids in ([an[0].id],[an[1].id],[an[0].id,an[1].id]):
        tb=R.get_report('trial_balance', dict(o, analytic_ids=ids)); vals.append(row(tb, lambda r: r.get('code')==a_exp.code)['values']['debit'])
    print('A, B, A+B filter:', vals, 'A+B==total' , abs(vals[0]+vals[1]-1000)<0.01)
    gl=R.get_report('general_ledger', dict(o, analytic_ids=[an[0].id]), expanded=['a%d'%a_exp.id])
    print('GL line', [(x['values'].get('debit'), x['values'].get('name')) for x in gl['rows'] if x['id'].startswith('l')])
print('== 8 draft voucher in unposted report')
V=env['sb.voucher'].with_user(acc)
a1=env['account.account'].search([('code','=','1104-0002')]); a2=env['account.account'].search([('account_type','=','expense')],limit=1)
v=V.create({'voucher_type':'journal','date':'2026-08-05','memo':'R5 draft test','line_ids':[(0,0,{'account_id':a2.id,'debit':777.77,'name':'draft exp'}),(0,0,{'account_id':a1.id,'credit':777.77,'name':'draft bank'})]})
print('draft voucher', v.id, v.state, v.move_id.id)
o={'date_from':'2026-07-08','date_to':'2026-10-31'}
p=R.get_report('trial_balance', o); u=R.get_report('trial_balance', dict(o, posted_only=False))
dp=row(p, lambda r: r.get('code')==a2.code); du=row(u, lambda r: r.get('code')==a2.code)
print('expense debit posted', dp and dp['values']['debit'], 'unposted', du and du['values']['debit'], 'title', u['title'], '| total balanced', round(row(u,lambda r:r['id']=='total')['values']['closing'],2))
gl=R.get_report('general_ledger', dict(o, posted_only=False, account_ids=[a2.id]), expanded=['a%d'%a2.id])
print('GL draft line', [(x['values'].get('number'), x['values'].get('debit'), x.get('open')) for x in gl['rows'] if x['id'].startswith('l') and 'Draft' in str(x['values'].get('number'))])
print('moves left behind:', env['account.move'].search_count([('ref','ilike','R5 draft')]), 'voucher still draft', v.state, v.move_id.id)
env.cr.rollback()
# ---- t4.py
U=lambda l: env['res.users'].search([('login','=',l)])
acc=U('r5.accountant'); R=env['sb.report'].with_user(acc)
def row(doc, pred): return next((r for r in doc['rows'] if pred(r)), None)
plan=env['account.analytic.plan'].create({'name':'R5 cost centres'})
an=env['account.analytic.account'].create([{'name':'R5 A','plan_id':plan.id},{'name':'R5 B','plan_id':plan.id}])
a_exp=env['account.account'].search([('account_type','=','expense')],limit=1); a_cash=env['account.account'].search([('code','=','1104-0002')])
m=env['account.move'].sudo().create({'move_type':'entry','date':'2026-08-01','journal_id':env['account.journal'].search([('type','=','general')],limit=1).id,
  'line_ids':[(0,0,{'account_id':a_exp.id,'debit':1000,'analytic_distribution':{str(an[0].id):50,str(an[1].id):50}}),(0,0,{'account_id':a_cash.id,'credit':1000})]})
m.action_post()
o={'date_from':'2026-07-08','date_to':'2026-10-31','move_ids':[m.id]}
vals=[]
for ids in ([an[0].id],[an[1].id],[an[0].id,an[1].id]):
    tb=R.get_report('trial_balance', dict(o, analytic_ids=ids)); vals.append(row(tb, lambda r: r.get('code')==a_exp.code)['values']['debit'])
pl=R.get_report('profit_loss', dict(o, analytic_plan_ids=[plan.id]))
print('TB A, B, A+B:', vals, '| A+B = total:', abs(vals[0]+vals[1]-1000)<0.01, '| plan P&L profit', row(pl,lambda r:r['id']=='profit')['values']['p0'])
gl=R.get_report('general_ledger', dict(o, analytic_ids=[an[0].id]), expanded=['a%d'%a_exp.id])
print('GL line', [(x['values'].get('debit'), x['values'].get('name')) for x in gl['rows'] if x['id'].startswith('l')], 'account row', row(gl,lambda r:r['id']=='a%d'%a_exp.id)['values']['debit'])
env.cr.rollback()
# ---- t5.py
U=lambda l: env['res.users'].search([('login','=',l)])
acc=U('r5.accountant'); R=env['sb.report'].with_user(acc)
def row(doc, pred): return next((r for r in doc['rows'] if pred(r)), None)
for df,dt in (('2024-07-08','2025-07-07'),('2025-07-08','2026-07-07'),('2026-07-08','2026-10-31')):
    cf=R.get_report('cash_flow', {'date_from':df,'date_to':dt,'comparison':'last_year'})
    pl=R.get_report('profit_loss', {'date_from':df,'date_to':dt})
    print('==', df, dt, 'P&L profit', round(row(pl,lambda r:r['id']=='profit')['values']['p0'],2))
    for r in cf['rows']:
        if r['kind'] in ('section','total') or r['parent'] in ('operating','recon') and not r['label'].startswith(('(Increase','Increase')) or r['id'] in ('diff','open'):
            print('  %-75s %18s %18s | drills %s' % (r['label'][:75], '{:,.2f}'.format(r['values'].get('amount',0)), '{:,.2f}'.format(r['values'].get('p1',0)), {k:(v or {}).get('report') for k,v in (r.get('drills') or {}).items()}))
env.cr.rollback()
