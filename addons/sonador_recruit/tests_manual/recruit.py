"""Recruitment: opening, candidate, pipeline moves, interview and scorecard (interviewer only), not continuing and back,
hire into People (onboarding starts, opening closes when filled), funnel. Rolled back.
Run: odoo shell -d ecogreen < tests_manual/recruit.py"""
from odoo.exceptions import AccessError, UserError
ok = lambda c, m: print(('PASS ' if c else 'FAIL ') + m)  # noqa: E731
R = env['sb.recruit']
mgr = env['hr.employee'].search([('child_ids', '!=', False), ('active', '=', True), ('user_id', '!=', False)], limit=1)
jid = R.opening_save({'name': 'Field Agronomist (test)', 'openings': 1, 'department_id': mgr.department_id.id, 'manager_id': mgr.id,
                      'salary_from': 12000, 'salary_to': 15000, 'open': True})
ok(any(j['id'] == jid for j in R.openings()['jobs']), 'opening listed')
a = R.application_save({'job_id': jid, 'expected': 14000, 'candidate': {'name': 'Test Candidate Abebe', 'email': 'abebe.test@example.com',
                                                                        'phone': '0911223344', 'education': 'BSc Plant Science'}})
ok(a['stage'] == 'new' and a['candidate']['email'] == 'abebe.test@example.com', 'candidate added in New')
R.move(a['id'], 'screening')
iv = R.interview_save({'application_id': a['id'], 'when': '2026-10-25T10:00', 'kind': 'panel', 'interviewer_ids': [mgr.id]})
app = R.application(a['id'])
ok(app['stage'] == 'interview' and len(app['interviews']) == 1, 'scheduling an interview moves the candidate to Interview')
other = env['hr.employee'].search([('active', '=', True), ('user_id', '!=', False), ('id', '!=', mgr.id),
                                   ('user_id.group_ids', 'not in', env.ref('sonador_books.group_sb_hr').id)], limit=1)
try:
    R.with_user(other.user_id).interview_save({'id': iv['id'], 'score_skills': 5}); ok(False, 'non-interviewer scores')
except AccessError:
    ok(True, 'only the interviewers score')
s = R.with_user(mgr.user_id).interview_save({'id': iv['id'], 'score_skills': 5, 'score_experience': 4, 'score_attitude': 4, 'score_communication': 3,
                                             'recommendation': 'yes', 'done': True})
ok(s['score'] == 4.0, 'scorecard average 4.0')
ok(any(i['id'] == iv['id'] for i in R.with_user(mgr.user_id).my_interviews()), 'interviewer sees it under My Interviews')
try:
    R.move(a['id'], 'hired'); ok(False, 'hired by moving')
except UserError:
    ok(True, 'Hired only through Hire')
R.reject(a['id'], env.ref('sonador_recruit.reason_salary').id)
ok(not env['sb.recruit.application'].browse(a['id']).active, 'not continuing (withdrew: salary)')
R.restore(a['id'])
eid = R.hire(a['id'], {'start': '2026-11-01', 'wage': 13500})
e = env['hr.employee'].browse(eid)
ok(e.name == 'Test Candidate Abebe' and e.job_title == 'Field Agronomist (test)' and e.parent_id == mgr and e.current_version_id.wage == 13500,
   'hired into People: position, manager, pay')
ok(env['sb.journey'].search_count([('employee_id', '=', eid), ('kind', '=', 'onboarding')]) == 1, 'onboarding started')
ok(not env['hr.job'].browse(jid).sb_open, 'opening closed once filled')
st = R.analytics()
ok(st['total'] >= 2588 and st['funnel'][0]['count'] == st['total'], 'funnel: %s applications, %s hired' % (st['total'], st['hired']))
env.cr.rollback()
