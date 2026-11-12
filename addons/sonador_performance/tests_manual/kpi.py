"""KPI engine: performance levels, lower-is-better, frequency, assignment precedence, templates, exact scorecard maths
(stored results match a hand calculation and the old engine's points), computed measures registry, key results fed
from KPIs and plan tasks, the Me card, and (on Eco Green) the KPIs brought over. Rolled back.
Run: odoo shell -d ecogreen < tests_manual/kpi.py"""
ok_all = []


def ok(c, m):
    ok_all.append(bool(c))
    print(('PASS ' if c else 'FAIL ') + m)


P = env['sb.perf']
K, A, V, S = env['sb.perf.kpi'], env['sb.perf.kpi.assign'], env['sb.perf.kpi.value'], env['sb.perf.kpi.score']
co = env.company
dept = env['hr.department'].create({'name': 'KPI Test Dept', 'company_id': co.id})
job = env['hr.job'].create({'name': 'KPI Test Job', 'company_id': co.id})
user = env['res.users'].create({'name': 'KPI Tester', 'login': 'kpi.tester@example.test', 'company_id': co.id, 'company_ids': [(6, 0, [co.id])]})
emp = env['hr.employee'].create({'name': 'KPI Tester', 'department_id': dept.id, 'job_id': job.id, 'company_id': co.id, 'user_id': user.id})
other = env['hr.employee'].create({'name': 'KPI Other', 'department_id': dept.id, 'company_id': co.id})
months = P._months()
month = [m for m in months if m['no'] % 3][-1]          # not a quarter end
qmonth = [m for m in months if m['no'] % 3 == 0][-1]   # a quarter end

# ------------------------------------------------------------------ levels and direction
k1 = K.create({'name': 'T sales %', 'department_id': dept.id, 'target': 200.0, 'unit': '%'})
k1.action_default_levels()
ok(len(k1.level_ids) == 5 and k1.bands == '>=0.95=5; >=0.85=4; >=0.75=3; >=0.6=2; <0.6=1', 'standard levels written, bands follow: %s' % k1.bands)
cases = [(190, 5, 'Excellent'), (189.98, 4, 'Very good'), (170, 4, 'Very good'), (150, 3, 'Good'), (120, 2, 'Fair'),
         (119.98, 1, 'Poor'), (0, 1, 'Poor'), (260, 5, 'Excellent')]
res = [(a,) + k1._score(a / 200.0)[:2] for a, _s, _n in cases]
ok(all(r[1] == c[1] and r[2] == c[2] for r, c in zip(res, cases)), 'levels at the edges (95%%, 94.99%%, 59.99%%, 130%%): %s' % res)
k1.level_ids.filtered(lambda l: l.score == 5).min_pct = 100.0
ok(k1._score(0.99)[0] == 4 and k1.bands.startswith('>=1=5'), 'editing a level moves the band (99%% now scores 4): %s' % k1.bands)
k1.level_ids.filtered(lambda l: l.score == 5).min_pct = 95.0
k2 = K.create({'name': 'T days to close', 'department_id': dept.id, 'target': 7.0, 'higher_is_better': False, 'unit': 'days'})
k3 = K.create({'name': 'T quarterly audit', 'department_id': dept.id, 'target': 10.0, 'frequency': 'quarterly'})
for k, w in ((k1, 0.5), (k2, 0.3), (k3, 0.2)):
    A.create({'kpi_id': k.id, 'department_id': dept.id, 'weight': w})

# ------------------------------------------------------------------ scorecard maths, exactly
sc = P.scorecard_save(emp.id, month['key'], [{'kpi_id': k1.id, 'actual': 172}, {'kpi_id': k2.id, 'actual': 10}])
r1, r2 = [next(r for r in sc['rows'] if r['kpi_id'] == k.id) for k in (k1, k2)]
ok(r1['achievement'] == 86.0 and r1['score'] == 4 and r1['level'] == 'Very good', 'higher is better: 172 of 200 = 86%% -> 4 (%s)' % r1['level'])
ok(r2['achievement'] == 70.0 and r2['score'] == 2, 'lower is better: 10 days vs 7 = 70%% -> 2 (%s)' % r2['level'])
ok(not any(r['kpi_id'] == k3.id for r in sc['rows']), 'quarterly KPI not on a mid-quarter month (%s, fiscal month %s)' % (month['label'], month['no']))
pts = 4 * 0.5 + 2 * 0.3
ok(abs(sc['points'] - pts) < 1e-9 and abs(sc['max_points'] - 4.0) < 1e-9 and sc['total'] == round(pts / 4.0 * 100, 1),
   'total = (4x0.5 + 2x0.3) / (5x0.8) = %s%% (got %s%%)' % (round(pts / 4.0 * 100, 1), sc['total']))
ach = (0.86 * 0.5 + 0.7 * 0.3) / 0.8 * 100
ok(abs(sc['achievement'] - round(ach, 1)) < 1e-9, 'weighted achievement %s%%' % sc['achievement'])
st = S.search([('employee_id', '=', emp.id), ('month', '=', month['start'])])
ok(len(st) == 1 and st.percent == sc['total'] and abs(st.points - pts) < 1e-6 and st.results == 2 and st.kpis == 2,
   'stored scorecard result follows the entries: %s%% %s, %s of %s KPIs' % (st.percent, st.rating, st.results, st.kpis))
# all results present: the same as Odoo 18's points (sum of level x weight out of 5 x sum of weights)
sc = P.scorecard_save(emp.id, qmonth['key'], [{'kpi_id': k1.id, 'actual': 200}, {'kpi_id': k2.id, 'actual': 7}, {'kpi_id': k3.id, 'actual': 5}])
o18_total, o18_max = 5 * 0.5 + 5 * 0.3 + 1 * 0.2, 5 * 1.0
ok(any(r['kpi_id'] == k3.id for r in sc['rows']) and sc['total'] == round(o18_total / o18_max * 100, 1),
   'quarter end: 3 KPIs, total %s%% = old engine %s / %s' % (sc['total'], o18_total, o18_max))
v = V.search([('kpi_id', '=', k1.id), ('employee_id', '=', emp.id), ('month', '=', qmonth['start'])])
v.actual = 100
st = S.search([('employee_id', '=', emp.id), ('month', '=', qmonth['start'])])
ok(st.points == 1 * 0.5 + 5 * 0.3 + 1 * 0.2, 'changing a result re-scores the stored card (%s points)' % st.points)
v.unlink()
ok(S.search([('employee_id', '=', emp.id), ('month', '=', qmonth['start'])]).results == 2, 'removing a result re-scores too')

# ------------------------------------------------------------------ assignment precedence and templates
A.create({'kpi_id': k1.id, 'job_id': job.id, 'weight': 0.4})
A.create({'kpi_id': k1.id, 'employee_id': emp.id, 'weight': 0.6})
w = {a.kpi_id.id: a.weight for a in P._assignments(emp)}
w2 = {a.kpi_id.id: a.weight for a in P._assignments(other)}
ok(w[k1.id] == 0.6 and w2[k1.id] == 0.5, 'person beats position beats department (%s, other %s)' % (w[k1.id], w2[k1.id]))
tpl = env['sb.perf.kpi.template'].create({'name': 'T job scorecard', 'job_id': job.id, 'line_ids': [
    (0, 0, {'kpi_id': k2.id, 'weight': 0.7, 'target': 5}), (0, 0, {'kpi_id': k3.id, 'weight': 0.3})]})
tpl.action_apply()
tpl.line_ids[0].weight = 0.65
tpl.action_apply()
ja = A.search([('template_id', '=', tpl.id)])
ok(len(ja) == 2 and set(ja.mapped('job_id').ids) == {job.id} and sorted(ja.mapped('weight')) == [0.3, 0.65] and tpl.weight_total == 0.95,
   'template applied twice: 2 position assignments, weights updated %s' % sorted(ja.mapped('weight')))
ok({a.kpi_id.id: a.target for a in P._assignments(other)}.get(k2.id) == 0.0 and
   {a.kpi_id.id: a.target for a in P._assignments(emp)}.get(k2.id) == 5.0, 'template target used for the position')

# ------------------------------------------------------------------ computed measures
keys = [m['key'] for m in K._measures()]
ok(all(x in keys for x in ('sales_amount', 'company_collections', 'on_time_delivery', 'overdue_receivables', 'goal_progress',
                           'plan_completion')), 'measures registry: %s' % ', '.join(keys))
kc = K.create({'name': 'T collections', 'source': 'auto', 'auto_key': 'company_collections', 'target': 1})
for cm in reversed(months):   # the latest month with receipts
    direct = sum(env['sb.voucher'].search([('voucher_type', '=', 'receipt'), ('state', '=', 'posted'), ('date', '>=', cm['start']),
                                           ('date', '<=', cm['end']), ('company_id', '=', co.id), ('partner_id.customer_rank', '>', 0)]).mapped('amount_total'))
    if direct:
        break
ok(direct and abs(kc._measure(emp, cm['start'], cm['end']) - direct) < 0.01, 'company collections %s in %s = receipts of the month' % (round(direct, 2), cm['label']))
kq = K.create({'name': 'T q collections', 'source': 'auto', 'auto_key': 'company_collections', 'target': 1, 'frequency': 'quarterly'})
d0, d1 = kq._window(qmonth)
ok(d1 == qmonth['end'] and d0 == qmonth['fy_starts'][qmonth['no'] - 3], 'quarterly measure covers the quarter %s..%s' % (d0, d1))
try:
    K.create({'name': 'T bad', 'source': 'auto'})
    ok(False, 'computed KPI without a measure refused')
except Exception:
    ok(True, 'computed KPI without a measure refused')

# ------------------------------------------------------------------ key results from a KPI and from plan tasks
goal = env['sb.perf.goal'].create({'name': 'T goal', 'level': 'person', 'employee_id': emp.id,
                                   'date_from': months[-3]['start'], 'date_to': months[-1]['end']})
kr = env['sb.perf.kr'].create({'goal_id': goal.id, 'name': 'T KR from KPI', 'measure': 'kpi', 'kpi_id': k1.id, 'rollup': 'sum',
                               'target_value': 1000})
expect = sum(V.search([('kpi_id', '=', k1.id), ('employee_id', '=', emp.id), ('month', '>=', months[-3]['start'])]).mapped('actual'))
kr.action_refresh()
ok(kr.current_value == expect and abs(kr.progress - min(100, expect / 10.0)) < 1e-6,
   'KR from a KPI: %s (sum of results in the goal period), progress %s%%' % (kr.current_value, kr.progress))
if 'plan_task_ids' in kr._fields:
    tasks = env['sb.plan.task'].search([], limit=4)
    krp = env['sb.perf.kr'].create({'goal_id': goal.id, 'name': 'T KR from plan', 'measure': 'plan', 'plan_task_ids': [(6, 0, tasks.ids)]})
    krp.action_refresh()
    want = round(sum(100.0 if t.status == 'done' else float(t.progress or 0) for t in tasks) / len(tasks), 2) if tasks else 0.0
    ok(krp.current_value == want and krp.target_value == 100.0, 'KR from %d plan tasks: %s%% done (%s)' % (len(tasks), krp.current_value, krp.source_note))
    ok(abs(goal.progress - round((kr.progress + krp.progress) / 2, 1)) < 1e-6, 'goal progress = average of its key results (%s%%)' % goal.progress)
    pc = K.create({'name': 'T plan completion', 'source': 'auto', 'auto_key': 'plan_completion', 'target': 100})
    ok(isinstance(pc._measure(emp, month['start'], month['end']), float), 'plan completion measure runs')
g = P.goal_save({'name': 'T goal 2', 'level': 'person', 'employee_id': emp.id, 'date_from': str(months[-3]['start']),
                 'date_to': str(months[-1]['end']),
                 'krs': [{'name': 'From KPI', 'measure': 'kpi', 'kpi_id': k1.id, 'rollup': 'latest', 'target_value': 200, 'current_value': 999}]})
ok(g['krs'][0]['measure'] == 'kpi' and g['krs'][0]['current_value'] != 999, 'goal form saves a KPI-fed key result (now %s)' % g['krs'][0]['current_value'])

# ------------------------------------------------------------------ Me card
card = P.with_user(user).my_scorecard_card()
ok(card and card['kpis'] >= 2 and any(r['level'] for r in card['rows']), 'Me card: %s%% %s in %s, %d KRs' % (
    card and card['total'], card and card['rating'], card and card['month']['label'], len(card['krs']) if card else 0))
sec = [s for s in env['sb.me'].with_user(user).sections() if s['key'] == 'scorecard']
ok(sec and sec[0]['data'] and sec[0]['data']['employee_id'] == emp.id, 'Me shows the "My scorecard" section')
ok(P.ctx()['kpi_list'] and P.ctx()['frequency'], 'Performance app context lists KPIs and frequencies')

# ------------------------------------------------------------------ brought over from Odoo 18 (Eco Green only)
if env.cr.dbname in ('ecogreen', 'ecogreen_test'):
    kd = K.with_context(active_test=False).search([('legacy_ref', '=like', 'O18|kpi|%')])
    ok(len(kd) == 29, '29 Odoo 18 KPI definitions in the library (%d)' % len(kd))
    ok(len(kd.mapped('level_ids')) == 145, '145 performance levels (%d)' % len(kd.mapped('level_ids')))
    tp = env['sb.perf.kpi.template'].search([('legacy_ref', '=like', 'O18|tpl|%')])
    ok(len(tp) == 13 and len(tp.mapped('line_ids')) == 185, '13 templates, 185 lines (%d, %d)' % (len(tp), len(tp.mapped('line_ids'))))
    asg = A.search([('legacy_ref', '=like', 'O18|asg|%')])
    by = {}
    for a in asg.filtered(lambda a: a.department_id):
        by[a.department_id.name] = round(by.get(a.department_id.name, 0) + a.weight, 4)
    ok(len(asg) == 31 and all(abs(x - 1.0) < 1e-6 for x in by.values()), '31 assignments, department weights add to 100%%: %s' % by)
    auto = kd.filtered(lambda k: k.source == 'auto')
    ok(sorted(auto.mapped('auto_key')) == ['company_collections', 'company_sales_litres'], 'the two system KPIs computed in Unio')
    vals = V.search([('kpi_id', 'in', kd.ids)])
    bad = [x for x in vals if x.target and x.score != x.kpi_id._score(x.achievement)[0]]
    ok(not bad, 'every result scored by its levels (%d results)' % len(vals))
    sts = S.search([('employee_id.company_id', '=', co.id), ('results', '>', 0)], limit=40)
    mism = [s for s in sts if s.percent != (P._scorecard(s.employee_id, P._month_of(s.month))['total'] or 0.0)]
    ok(not mism, 'stored scorecards equal a fresh calculation (%d checked)' % len(sts))

print('KPI TESTS: %d passed, %d failed' % (sum(ok_all), len(ok_all) - sum(ok_all)))
env.cr.rollback()
