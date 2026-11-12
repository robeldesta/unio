"""People app: every screen call and edit, then rolled back. Run: odoo shell -d ecogreen < tests_manual/people.py"""
from odoo.exceptions import AccessError
ok = lambda c, m: print(('PASS ' if c else 'FAIL ') + m)  # noqa: E731
S = env['sb.people']
c = S.ctx(); ok(c['is_hr'] and c['departments'] and c['types'], 'ctx: %d departments, %d positions' % (len(c['departments']), len(c['jobs'])))
h = S.home(); ok(h['kpis']['headcount'] > 0, 'home: headcount %s, by dept %s, alerts %d, missing %s' % (
    h['kpis']['headcount'], h['by_dept'][:3], len(h.get('alerts', [])), [(m['label'], m['count']) for m in h.get('missing', [])][:4]))
d = S.directory(); ok(d['count'] == h['kpis']['headcount'], 'directory: %d active' % d['count'])
ok(S.directory(status='left')['count'] > 0, 'directory: people who left %d' % S.directory(status='left')['count'])
ok(S.directory(search=d['rows'][0]['name'][:5])['count'] >= 1, 'directory search')
o = S.org_chart(); roots = [n for n in o['nodes'] if not n['parent_id']]
ok(len(o['nodes']) == d['count'], 'org chart: %d nodes, %d at the top' % (len(o['nodes']), len(roots)))
eid = next(n['id'] for n in o['nodes'] if n['manager_id'])
p = S.profile(eid); ok(p['level'] == 'hr' and p['history'] and p['personal'] is not None, 'profile %s: %d versions, timeline %d' % (
    p['card']['name'], len(p['history']), len(p['timeline'])))
p = S.save_personal(eid, {'sb_tin': '0012345678', 'sb_region': 'addis_ababa', 'sb_woreda': '05', 'sex': 'female', 'bank_acc': '1000999888777', 'bank_name': 'Commercial Bank of Ethiopia'})
emp = env['hr.employee'].browse(eid)
ok(p['personal']['sb_tin'] == '0012345678' and emp.sb_payroll_id.tin == '0012345678' and emp.sb_payroll_id.bank_account == '1000999888777',
   'personal saved, payroll profile follows (TIN, bank)')
p = S.job_change(eid, {'date': '2026-11-01', 'job': 'Senior Sales Officer', 'wage': 15000, 'taxable': 2000, 'nontaxable': 1500, 'reason': 'promotion'})
ok(len(p['history']) >= 2 and any(x['job'] == 'Senior Sales Officer' for x in p['history']), 'job change from 1 Nov 2026 kept as a new version')
ok(any('Promotion' in t['text'] for t in p['timeline']), 'timeline shows the promotion')
p = S.save_rows(eid, 'contacts', [{'name': 'Almaz Tesfaye', 'relationship': 'spouse', 'phone': '0911000000', 'primary': True}])
ok(p['contacts'][0]['name'] == 'Almaz Tesfaye', 'emergency contact saved')
p = S.save_rows(eid, 'documents', [{'doc_type': 'fayda', 'name': 'FAN-1234', 'issue_date': '2025-01-01', 'expiry_date': '2026-11-15'}])
ok(p['documents'][0]['state'] == 'expiring', 'document expiring in 60 days flagged')
p = S.save_rows(eid, 'assets', [{'name': 'Dell Latitude', 'category': 'laptop', 'serial': 'SN123', 'issued_on': '2026-01-10'}])
ok(p['assets'][0]['serial'] == 'SN123', 'asset recorded')
h = S.home(); ok(any(a['kind'] == 'document' for a in h['alerts']), 'home: expiring document in Needs attention')
new = S.hire({'name': 'Test Hire Person', 'job': 'Driver', 'department_id': c['departments'][0]['id'], 'start': '2026-10-20', 'wage': 6000, 'type': 'employee'})
ne = env['hr.employee'].browse(new)
ok(ne.sb_code and ne.version_id.trial_date_end and ne.sb_payroll_id.basic_salary == 6000,
   'hire: %s, probation ends %s (60 working days, Mon-Sat), payroll profile made' % (ne.sb_code, ne.version_id.trial_date_end))
p = S.depart(new, {'date': '2026-12-31', 'reason_id': env['hr.departure.reason'].search([], limit=1).id})
ok(not ne.active and not ne.sb_payroll_id.active and ne.sb_payroll_id.end_date, 'leaving recorded: archived, payroll profile ended')
# self-service
user = env['res.users'].search([('employee_ids', '!=', False), ('share', '=', False), ('id', '!=', env.ref('base.user_admin').id)], limit=1)
if user:
    me = user.employee_ids[:1]
    SS = S.with_user(user)
    ok(SS.ctx()['me'] == me.id and not SS.ctx()['is_hr'] or user.has_group('sonador_books.group_sb_hr'), 'self-service: %s sees own record' % user.name)
    if not user.has_group('sonador_books.group_sb_hr'):
        mine = SS.profile(me.id); ok(mine['level'] == 'self' and 'personal' in mine, 'own profile with personal details')
        other = next(n['id'] for n in o['nodes'] if n['id'] != me.id and n['id'] not in env['hr.employee'].search([('id', 'child_of', me.id)]).ids)
        op = SS.profile(other); ok(op['level'] == 'public' and 'personal' not in op and 'history' not in op, 'someone else: public card only')
        try:
            SS.save_personal(me.id, {'sb_tin': 'X'}); ok(False, 'employee cannot change TIN')
        except AccessError:
            ok(True, 'employee cannot change TIN (HR only)')
        r = SS.save_personal(me.id, {'private_phone': '0922000000'}); ok(r['personal']['private_phone'] == '0922000000', 'employee updates own phone')
env.cr.rollback()
