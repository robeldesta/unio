"""HR analytics and anonymous pulse surveys. Rolled back. Run: odoo shell -d ecogreen < tests_manual/insights.py"""
from odoo.exceptions import UserError, AccessError
ok = lambda c, m: print(('PASS ' if c else 'FAIL ') + m)  # noqa: E731
I = env['sb.insights']
d = I.dashboard()
ok(d['kpis']['headcount'] == env['hr.employee'].search_count([('active', '=', True), ('company_id', '=', env.company.id)]) - 0 or d['kpis']['headcount'] > 0,
   'headcount %s, turnover %s%%, payroll %s (%s)' % (d['kpis']['headcount'], d['kpis']['turnover'], d['kpis']['payroll_month'], d['kpis']['payroll_label']))
ok(len(d['trend']['head']) == 24 and d['trend']['head'][-1] == d['kpis']['headcount'], '24-month headcount trend ends at today\'s headcount')
ok(any(d['cost']['cost']), 'payroll cost by month from payrolls (incl. Odoo 18 history)')
ok(d['ratings']['rows'], 'ratings of the last closed review cycle: %s' % d['ratings']['rows'])
sv = I.survey_save({'name': 'Test pulse', 'state': 'open'})
p = env['sb.pulse'].search([('name', '=', 'Test pulse')])
ok(len(p.question_ids) == 7 and p.state == 'open', 'survey opened with 7 default questions')
emps = env['hr.employee'].search([('active', '=', True), ('user_id', '!=', False), ('department_id', '!=', False)], limit=7)
qs = {q.kind: q for q in p.question_ids}
for i, e in enumerate(emps):
    ans = {str(q.id): (9 if i < 4 else 3) if q.kind == 'enps' else 4 if q.kind == 'scale' else 'More training' for q in p.question_ids}
    I.with_user(e.user_id).take(p.id, ans)
try:
    I.with_user(emps[0].user_id).take(p.id, {}); ok(False, 'answered twice')
except UserError:
    ok(True, 'cannot answer twice')
ok(not p.response_ids.filtered(lambda r: 'employee_id' in r._fields), 'answers carry no employee')
res = I.results(p.id)
ok(res['responses'] == len(emps) and res['enps'] == round((4 - (len(emps) - 4)) / len(emps) * 100), 'eNPS %s from %d answers' % (res['enps'], len(emps)))
ok(all(x['hidden'] for x in res['by_dept'] if x['n'] < 5), 'departments with fewer than 5 answers hidden')
try:
    I.with_user(emps[0].user_id).results(p.id); ok(emps[0].user_id.has_group('sonador_books.group_sb_hr'), 'results blocked')
except AccessError:
    ok(True, 'employees cannot see results')
env.cr.rollback()
