"""Performance: goals, scorecards, review cycle (self, manager, acknowledge, 9-box, pay review), check-ins, 1:1s, praise,
and who may see / do what. Rolled back. Run: odoo shell -d ecogreen < tests_manual/perf.py"""
from odoo.exceptions import AccessError, ValidationError
ok = lambda c, m: print(('PASS ' if c else 'FAIL ') + m)  # noqa: E731
P = env['sb.perf']
E = env['hr.employee']
c = P.ctx(); ok(c['is_hr'] and c['months'], 'ctx: %d people, months %s..%s' % (len(c['people']), c['months'][-1]['label'], c['months'][0]['label']))
o = P.overview(); ok('wall' in o and 'dept_scores' in o, 'overview: dept scores %s' % o['dept_scores'][:2])
# a manager with a team and one of their people
mgr = E.search([('child_ids', '!=', False), ('active', '=', True), ('user_id', '!=', False)], limit=1)
emp = mgr.child_ids.filtered(lambda x: x.active and x.user_id)[:1]
ok(mgr and emp, 'manager %s with report %s' % (mgr.name, emp.name))
# goals: company -> department -> person
cg = P.goal_save({'name': 'Sell 1.5M litres this half', 'level': 'company', 'krs': [{'name': 'Litres', 'start_value': 0, 'target_value': 1500000, 'current_value': 600000}]})
dg = P.goal_save({'name': 'Addis sales +20%', 'level': 'department', 'department_id': emp.department_id.id, 'parent_id': cg['id'], 'progress_manual': 50, 'krs': []})
pg = P.goal_save({'name': 'Open 10 new retailers', 'level': 'person', 'employee_id': emp.id, 'parent_id': dg['id'],
                  'krs': [{'name': 'Retailers', 'start_value': 0, 'target_value': 10, 'current_value': 4}]})
ok(cg['progress'] == 40.0 and pg['progress'] == 40.0, 'key results drive progress (company 40%%, person 40%%)')
tree = P.goals('all')['goals']; ok(any(g['parent_id'] == cg['id'] for g in tree), 'goals aligned in a tree')
# scorecard
sales = env['sb.perf.kpi'].search([('department_id', '=', emp.department_id.id), ('source', '=', 'manual')], limit=1) or \
    env['sb.perf.kpi'].create({'name': 'Test KPI', 'department_id': emp.department_id.id, 'target': 100})
if not sales.assignment_ids:
    env['sb.perf.kpi.assign'].create({'kpi_id': sales.id, 'department_id': emp.department_id.id, 'weight': 0.5})
month = c['months'][1]['key']
sc = P.scorecard_save(emp.id, month, [{'kpi_id': sales.id, 'actual': sales.target * 0.9}])
row = [r for r in sc['rows'] if r['kpi_id'] == sales.id][0]
ok(row['score'] == 4 and abs(row['achievement'] - 90) < 0.01, 'manual KPI 90%% of target scores 4 (%s)' % row)
ok(sc['total'] is not None, 'weighted total %s%% (%s)' % (sc['total'], sc['rating']))
# review cycle
cyc = P.cycle_create({'name': 'Test cycle', 'kind': 'bi_annual', 'date_from': '2026-01-09', 'date_to': '2026-07-07',
                      'template_id': env.ref('sonador_performance.template_standard').id, 'self_review': True,
                      'department_id': emp.department_id.id})
cy = P.cycle_state(cyc['id'], 'open')
row = [r for r in cy['rows'] if r['employee']['id'] == emp.id][0]
ok(row['manager'] and row['self'], 'cycle: manager and self review for %s' % emp.name)
staff = not emp.child_ids.filtered('active')
rv = P.review(row['manager']['id'])
ok(len(rv['lines']) == (6 if staff else 11), 'form: %d criteria (%s form)' % (len(rv['lines']), 'staff' if staff else 'managerial'))
try:
    P.review_save(rv['id'], {'lines': [{'id': rv['lines'][0]['id'], 'score': 4}]}, True); ok(False, 'submit with missing scores')
except ValidationError:
    ok(True, 'cannot submit until every line is scored')
lines = [{'id': l['id'], 'score': 4 if i % 2 else 5} for i, l in enumerate(rv['lines'])]
rv = P.with_user(mgr.user_id).review_save(rv['id'], {'lines': lines, 'potential': '3', 'raise': 10, 'strengths': 'Reliable'}, True)
ok(rv['state'] == 'submitted' and rv['score'], 'manager submits: %s%% %s' % (rv['score'], rv['rating']))
ack = P.with_user(emp.user_id).review_acknowledge(rv['id'], 'Thank you')
ok(ack['state'] == 'acknowledged', 'employee acknowledges')
cy = P.cycle(cyc['id']); ok(any(cy['nine_box'].values()), '9-box placed: %s' % {k: len(v) for k, v in cy['nine_box'].items()})
wage0 = emp.current_version_id.wage
n = P.apply_raises(cyc['id'], '2026-11-01')
emp.invalidate_recordset()
new = emp.version_ids.filtered(lambda v: str(v.date_version) == '2026-11-01')
ok(n >= 1 and new and abs(new.wage - wage0 * 1.1) < 0.01 and new.sb_change_reason == 'salary', 'pay review: +10%% from 1 Nov 2026 (%s -> %s)' % (wage0, new.wage))
# check-ins, 1:1, praise
ci = P.with_user(emp.user_id).checkin_save({'mood': '5', 'wins': 'Closed 3 retailers', 'blockers': 'Truck late'})
ok(ci['mine']['wins'] == 'Closed 3 retailers', 'check-in sent')
team = P.with_user(mgr.user_id).checkins()
mine = [t for t in team['team'] if t['employee']['id'] == emp.id][0]
ok(mine['checkin'], 'manager sees it')
P.with_user(mgr.user_id).checkin_reply(mine['checkin']['id'], 'Will call logistics')
oo = P.with_user(mgr.user_id).one2one_save({'with_id': emp.id, 'notes': 'Q2 targets', 'items': [{'kind': 'agenda', 'name': 'Targets'}, {'kind': 'action', 'name': 'Send plan'}]})
ok(len(oo['items']) == 2 and oo['manager']['id'] == mgr.id, 'one-to-one with agenda and action')
w = P.with_user(emp.user_id).give_feedback({'to_id': mgr.id, 'kind': 'praise', 'value': 'teamwork', 'text': 'Great support this week'})
ok(w[0]['text'] == 'Great support this week', 'praise on the wall')
# permissions
other = E.search([('active', '=', True), ('id', 'not in', (emp | mgr).ids), ('user_id', '!=', False),
                  ('id', 'not in', E.search([('id', 'child_of', emp.id)]).ids)], limit=1)
try:
    P.with_user(emp.user_id).scorecard(other.id); ok(emp.user_id.has_group('sonador_books.group_sb_hr'), 'employee blocked from others\' scorecard')
except AccessError:
    ok(True, 'employee cannot open someone else\'s scorecard')
try:
    P.with_user(emp.user_id).goal_save({'name': 'X', 'level': 'company'}); ok(emp.user_id.has_group('sonador_books.group_sb_hr'), 'company goal blocked')
except AccessError:
    ok(True, 'employee cannot set company goals')
try:
    P.with_user(emp.user_id).scorecard_save(emp.id, month, []); ok(False, 'own results')
except AccessError:
    ok(True, 'employee cannot enter their own KPI results')
env.cr.rollback()
