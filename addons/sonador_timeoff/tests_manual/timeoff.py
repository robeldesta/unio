"""Time off under the Labour Proclamation: entitlement, working days, holidays, sick pay tiers, limits, approvals.
Rolled back. Run: odoo shell -d ecogreen < tests_manual/timeoff.py"""
from datetime import date
from odoo.exceptions import AccessError, ValidationError
from odoo.addons.sonador_timeoff.models.leave import annual_entitlement
from odoo.addons.sonador_timeoff.models.holidays import holidays_for, orthodox_easter
ok = lambda c, m: print(('PASS ' if c else 'FAIL ') + m)  # noqa: E731
S = env['sb.leave.service']
ok([annual_entitlement(n) for n in (1, 2, 3, 4, 5, 10)] == [16, 16, 17, 17, 18, 20], 'art. 77: 16 days, +1 every 2 more years (years 1-5, 10)')
h = dict((n, d) for d, n, _c in holidays_for(2026))
ok(h['Enkutatash (Ethiopian New Year)'] == date(2026, 9, 11) and h['Meskel (Finding of the True Cross)'] == date(2026, 9, 27)
   and h['Fasika (Easter)'] == date(2026, 4, 12) and h['Adwa Victory Day'] == date(2026, 3, 2), 'holidays 2026: Enkutatash 11 Sep, Meskel 27 Sep, Fasika 12 Apr, Adwa 2 Mar')
mgr = env['hr.employee'].search([('child_ids', '!=', False), ('active', '=', True), ('user_id', '!=', False)], limit=1)
e = mgr.child_ids.filtered(lambda x: x.active and x.user_id)[:1]
v = e.current_version_id
v.write({'contract_date_start': date(2021, 3, 1), 'sex': 'male'})
a = S.annual(e, date(2026, 10, 18))
# years: 1 (2021-22) 16, 2 16, 3 17, 4 17, 5 (from 1 Mar 2025) 18, 6 (from 1 Mar 2026, part) 18 * part
ok(a['service_years'] == 5 and a['this_year'] == 18, 'year 6 of service: 18 days a year (%s)' % a['this_year'])
lapsed_expected = 16 + 16 + 17  # years ending Feb 2022, 2023, 2024 lapse two years later (2024, 2025, 2026)
ok(abs(a['lapsed'] - lapsed_expected) < 0.01, 'leave older than 2 years lapsed (art. 79(4)): %s days' % a['lapsed'])
ok(abs(a['available'] - (17 + 18 + 18 * 232 / 365.0)) < 0.2, 'available = year 4 + year 5 + year 6 so far: %s' % a['available'])
# working days: 48-hour week (Mon-Sat), Sundays and holidays skipped
wd = S.working_days(e, date(2026, 9, 7), date(2026, 9, 13))  # Mon 7 .. Sun 13, Enkutatash Fri 11
ok(wd == 5.0, 'Mon-Sun week with Enkutatash: 5 working days (%s)' % wd)
annual = env.ref('sonador_timeoff.type_annual')
r = S.request({'employee_id': e.id, 'type_id': annual.id, 'date_from': '2026-09-07', 'date_to': '2026-09-13', 'reason': 'Holiday'})
ok(r['days'] == 5 and r['state'] == 'submitted', 'annual leave request: 5 working days, waiting approval')
try:
    S.with_user(e.user_id).decide(r['id'], True); ok(False, 'self approval')
except AccessError:
    ok(True, 'an employee cannot approve their own leave')
d = S.with_user(mgr.user_id).decide(r['id'], True)
ok(d['state'] == 'approved', 'manager approves')
a2 = S.annual(e, date(2026, 10, 18)); ok(abs(a['available'] - a2['available'] - 5) < 0.01, 'balance down by 5 (oldest year used first)')
try:
    S.with_user(e.user_id).request({'employee_id': e.id, 'type_id': annual.id, 'date_from': '2026-10-01', 'date_to': '2026-12-31'}); ok(False, 'over balance')
except ValidationError:
    ok(True, 'more than the balance is refused (HR may override, with a warning)')
# sick leave tiers (art. 86)
sick = env.ref('sonador_timeoff.type_sick')
s1 = S.request({'employee_id': e.id, 'type_id': sick.id, 'date_from': '2026-01-01', 'date_to': '2026-02-14'}, True)
d = S.decide(s1['id'], True)
ok(d['pay'] == {'full': 30.0, 'half': 15.0, 'unpaid': 0.0}, '45 sick days: 30 full pay, 15 half pay (%s)' % d['pay'])
s2 = S.request({'employee_id': e.id, 'type_id': sick.id, 'date_from': '2026-03-01', 'date_to': '2026-05-04'}, True)
d = S.decide(s2['id'], True)
ok(d['pay'] == {'full': 0.0, 'half': 45.0, 'unpaid': 20.0}, 'next 65 days: 45 half, 20 unpaid (%s)' % d['pay'])
try:
    S.request({'employee_id': e.id, 'type_id': sick.id, 'date_from': '2026-06-01', 'date_to': '2026-08-31'}); ok(False, 'over 6 months')
except ValidationError:
    ok(True, 'sick leave over six months in twelve refused (art. 85(2))')
# limits
for t, df, dt, why in (('type_maternity_post', '2026-11-01', '2026-11-30', 'maternity is for women'),
                       ('type_marriage', '2026-11-02', '2026-11-06', 'marriage leave over 3 working days'),
                       ('type_unpaid', '2026-11-02', '2026-11-08', 'unpaid leave over 5 days')):
    try:
        S.request({'employee_id': e.id, 'type_id': env.ref('sonador_timeoff.' + t).id, 'date_from': df, 'date_to': dt}); ok(False, why)
    except ValidationError:
        ok(True, 'refused: %s' % why)
p = S.request({'employee_id': e.id, 'type_id': env.ref('sonador_timeoff.type_paternity').id, 'date_from': '2026-11-02', 'date_to': '2026-11-04'})
ok(p['days'] == 3, 'paternity: 3 consecutive days (art. 81(2))')
cal = S.with_user(mgr.user_id).calendar('2026-09-01'); row = [x for x in cal['rows'] if x['employee']['id'] == e.id]
ok(row and '2026-09-08' in row[0]['cells'] and any(d['holiday'] for d in cal['days']), 'team calendar shows the leave and Enkutatash')
b = S.balances(); ok(b['rows'] and b['total_liability'] >= 0, 'HR balances: %s days owed, liability %s ETB' % (b['total_days'], b['total_liability']))
env.cr.rollback()
