"""Onboarding / offboarding: started by hire and leaving, owners by role, task permissions and actions, policies,
clearance of assets and loans, exit interview, certificate of service. Rolled back.
Run: odoo shell -d ecogreen < tests_manual/onboarding.py"""
from odoo.exceptions import AccessError, UserError
ok = lambda c, m: print(('PASS ' if c else 'FAIL ') + m)  # noqa: E731
O = env['sb.onboarding']
P = env['sb.people']
mgr = env['hr.employee'].search([('child_ids', '!=', False), ('active', '=', True), ('user_id', '!=', False)], limit=1)
eid = P.hire({'name': 'Onboard Test Person', 'job': 'Sales Officer', 'department_id': mgr.department_id.id, 'parent_id': mgr.id,
              'start': '2026-10-20', 'wage': 8000})
j = env['sb.journey'].search([('employee_id', '=', eid), ('kind', '=', 'onboarding')])
ok(j and len(j.task_ids) >= 10, 'hire starts onboarding: %d tasks' % len(j.task_ids))
t_mgr = j.task_ids.filtered(lambda t: t.role == 'manager')[:1]
t_it = j.task_ids.filtered(lambda t: t.role == 'it')[:1]
ok(t_mgr.owner_id == mgr and t_it.owner_id == env.company.sb_owner_it_id, 'owners: manager %s, IT %s' % (t_mgr.owner_id.name, t_it.owner_id.name))
d = O.with_user(mgr.user_id).task_done(t_mgr.id, True, 'Done')
ok(any(t['id'] == t_mgr.id and t['done'] for t in d['tasks']), 'the manager ticks a manager task')
other = env['hr.employee'].search([('active', '=', True), ('user_id', '!=', False), ('id', 'not in', (mgr | env.company.sb_owner_it_id).ids),
                                   ('user_id.group_ids', 'not in', env.ref('sonador_books.group_sb_hr').id)], limit=1)
try:
    O.with_user(other.user_id).task_done(t_it.id, True); ok(False, 'someone else ticks an IT task')
except AccessError:
    ok(True, 'an unrelated employee cannot tick an IT task')
# login task creates the login
e = env['hr.employee'].browse(eid)
O.task_done(t_it.id, True)
e.invalidate_recordset()
ok(e.user_id, 'IT task "create login" made a login: %s' % e.user_id.login)
# policies
t_pol = j.task_ids.filtered(lambda t: t.action == 'policies')[:1]
try:
    O.task_done(t_pol.id, True); ok(False, 'policies not acknowledged')
except UserError:
    ok(True, 'policy task waits until every policy is acknowledged')
for p in env['sb.policy'].search([]):
    O.with_user(e.user_id).acknowledge(p.id)
O.task_done(t_pol.id, True)
ok(t_pol.done, 'after acknowledging all %d policies the task closes' % env['sb.policy'].search_count([]))
# probation
t_prob = j.task_ids.filtered(lambda t: t.action == 'probation')[:1]
O.task_done(t_prob.id, True)
ok(e.current_version_id.trial_date_end <= __import__('odoo').fields.Date.today(), 'probation task confirms employment')
# offboarding through the final settlement, with an asset and a loan open
env['sb.people.asset'].create({'employee_id': e.id, 'name': 'Phone', 'serial': 'IMEI1', 'issued_on': '2026-10-20'})
env['sb.payroll.loan'].create({'employee_id': e.id, 'amount': 2000, 'installment': 500, 'start': '2026-11-01'})
env['sb.payroll.service'].settlement_make(e.id, '2026-11-30', 'resign', True, False)
off = env['sb.journey'].search([('employee_id', '=', eid), ('kind', '=', 'offboarding')])
ok(off and off.task_ids.filtered('asset_id') and off.task_ids.filtered('loan_id'), 'leaving starts offboarding with the phone and the loan as tasks')
ta = off.task_ids.filtered('asset_id')
O.task_done(ta.id, True)
ok(ta.asset_id.returned_on, 'store task marks the phone returned')
try:
    O.task_done(off.task_ids.filtered('loan_id').id, True); ok(False, 'loan outstanding')
except UserError:
    ok(True, 'finance cannot clear while 2,000 is outstanding')
tl = off.task_ids.filtered(lambda t: t.action == 'login')
O.task_done(tl.id, True)
ok(not e.user_id.active, 'IT task disables the login on leaving')
O.interview_save(off.id, {'exit_reason': 'Further studies', 'exit_rating': '4', 'rehire': 'yes'})
ok(off.exit_rating == '4' and off.rehire == 'yes', 'exit interview saved')
pdf = env['ir.actions.report']._render_qweb_pdf('sonador_onboarding.certificate', [e.id])[0]
ok(pdf[:4] == b'%PDF', 'certificate of service PDF (%d bytes)' % len(pdf))
imp = env['sb.journey'].search([('note', 'like', 'Odoo 18%')])
ok(len(imp) == 9, 'Odoo 18 resignations on file: %d' % len(imp))
env.cr.rollback()
