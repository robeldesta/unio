# Planning rules as normal users (a person and their line manager, made for the test). Rolled back at the end.
# Run: odoo shell -d <db> < sonador_planning/tests_manual/planning_rules.py
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError, ValidationError

from odoo.addons.sonador_planning.models.service import month_bounds

ok = []


def check(cond, msg):
    print(('PASS ' if cond else 'FAIL ') + msg)
    ok.append(bool(cond))


def raises(fn, exc=(UserError, ValidationError)):
    """In the screen a refused call changes nothing (the request rolls back); a savepoint does that here."""
    try:
        with env.cr.savepoint():
            fn()
    except exc as e:
        return str(e)
    return False


def codes(res, key=None):
    fs = res['plan'] if key is None else res['tasks'].get(key, [])
    return {(f['level'], f['code']) for f in fs}


company = env.company
company.write({'sb_pl_require_goal': False, 'sb_pl_min_tasks': 4})
grp = env.ref('base.group_user')
Users = env['res.users'].with_context(no_reset_password=True)
u_mgr = Users.create({'name': 'Test Planning Manager', 'login': 'test.pl.mgr@example.invalid', 'group_ids': [(6, 0, grp.ids)]})
u_me = Users.create({'name': 'Test Planning Person', 'login': 'test.pl.me@example.invalid', 'group_ids': [(6, 0, grp.ids)]})
E = env['hr.employee']
mgr = E.create({'name': 'Test Planning Manager', 'user_id': u_mgr.id, 'company_id': company.id})
role = env['sb.plan.role'].create({'name': 'Test Sales officer', 'item_ids': [
    (0, 0, {'name': 'Weekly sales report to the sales manager', 'done_when': '4 reports sent', 'mandatory': True})]})
me = E.create({'name': 'Test Planning Person', 'user_id': u_me.id, 'parent_id': mgr.id, 'company_id': company.id, 'sb_pl_role_id': role.id})
item = role.item_ids

today = fields.Date.context_today(env['res.users'].browse(u_me.id))
m0, m1, label = month_bounds(today + timedelta(days=60))  # a month nobody plans yet
S = env['sb.planning'].with_user(u_me)
SM = env['sb.planning'].with_user(u_mgr)
# the month must be open for planning: plan it as if it were that month
S = S.with_context(tz='Africa/Addis_Ababa')
d0, d1 = fields.Date.to_string(m0), fields.Date.to_string(m0 + timedelta(days=6))


def row(name, done='', qty='', unit='', key=None, **kw):
    return dict({'key': key or name[:10], 'name': name, 'done_when': done, 'target_qty': qty, 'unit': unit, 'date_from': d0, 'date_to': d1}, **kw)


# ---------------------------------------------------------------- the checker refuses nonsense, with a reason
res = S.check(fields.Date.to_string(m0), [])
check(('block', 'empty_plan') in codes(res), 'an empty plan is refused')
res = S.check(fields.Date.to_string(m0), [row('Collect overdue receivables from Oromia dealers', key='a')])
check(('block', 'no_measure') in codes(res, 'a'), 'a line with neither a target nor a "done when" is refused')
res = S.check(fields.Date.to_string(m0), [row('Collect overdue receivables from Oromia dealers', qty='0', key='a')])
check(('block', 'zero_target') in codes(res, 'a'), 'a target of 0 is refused')
res = S.check(fields.Date.to_string(m0), [row('Collect overdue receivables from Oromia dealers', qty='1200000', unit='ETB', key='a')])
check(not any(f['level'] == 'block' for f in res['tasks']['a']), 'what + target + week is enough for a line (no other column needed)')
check(('block', 'role_items') in codes(res), "a plan with none of the role's must-do items is refused, naming them")
res = S.check(fields.Date.to_string(m0), [row('Collect overdue receivables from Oromia dealers', qty='1200000', key='a'),
                                          row('Weekly sales report', done='4 reports sent', key='b', role_item_id=item.id)])
check(('block', 'role_items') not in codes(res), 'tying a line to the role item clears that check')
dup = row('Visit 10 new farmer cooperatives in Adama', done='10 visit reports filed', key='x')
res = S.check(fields.Date.to_string(m0), [dup, dict(dup, key='y')])
check(('block', 'duplicate') in codes(res, 'y'), 'the very same line twice in one plan is refused')
res = S.check(fields.Date.to_string(m0), [dup, dict(dup, key='y', date_from=fields.Date.to_string(m0 + timedelta(days=7)),
                                                    date_to=fields.Date.to_string(m0 + timedelta(days=13)))])
check(not any(f['level'] == 'block' for f in res['tasks']['y']), 'the same work in another week is allowed (recurring work)')
res = S.check(fields.Date.to_string(m0), [dup, row('Visit 12 new farmer cooperatives in Adama', done='12 visit reports filed', key='z')])
check(('warn', 'similar') in codes(res, 'z') and not any(f['level'] == 'block' for f in res['tasks']['z']),
      'a look-alike line is only a warning (no more duplicate refusals)')
res = S.check(fields.Date.to_string(m0), [row('Support the sales team as needed in Adama', done='ongoing support', key='v')])
check(not any(f['level'] == 'block' for f in res['tasks']['v']) and ('warn', 'vague') in codes(res, 'v'),
      'vague wording warns, the manager decides')
res = S.check(fields.Date.to_string(m0), [row('asdf', done='asdf', key='g')])
check(any(f['level'] == 'block' for f in res['tasks']['g']), 'placeholder or random text is refused')

# ---------------------------------------------------------------- submit, edit while waiting, approve
rows = [row('Collect overdue receivables from Oromia dealers', qty='1200000', unit='ETB'),
        row('Weekly sales report', done='4 reports sent', role_item_id=item.id),
        row('Train 3 new sales clerks on the van sales app', done='3 clerks trained, sign-off sheet'),
        row('Open 2 new dealers in Bishoftu', qty='2', unit='dealers')]
out = S.save(fields.Date.to_string(m0), rows, True)
plan = env['sb.plan'].browse(out['plan']['id'])
check(plan.state == 'submitted', 'a plan with only what/target/week submits (%s)' % plan.state)
rows2 = [dict(t, key=t['key']) for t in out['tasks']]
rows2[0]['target_qty'] = 1500000
rows2.append(row('Close the Adama dealer complaint backlog', done='All 14 complaints answered in writing'))
out = S.save(fields.Date.to_string(m0), rows2, False)
check(plan.state == 'submitted' and len(plan.task_ids) == 5, 'the owner edits a submitted plan directly, it stays with the manager')
check(plan.task_ids.filtered(lambda t: t.target_qty == 1500000).review == 'pending', 'a changed line goes back to "not reviewed"')
rows3 = [dict(t) for t in out['tasks']] + [row('Weekly sales report', done='4 reports sent', role_item_id=item.id)]
rows3[-1].update(date_from=rows3[1]['date_from'], date_to=rows3[1]['date_to'])
msg = raises(lambda: S.save(fields.Date.to_string(m0), rows3, False))
check(msg and 'same line' in msg and len(plan.task_ids) == 5, 'an exact duplicate is refused on save, the plan stays as it was')
res = SM.review(plan.id)
SM.decide(plan.id, {str(t['id']): {'review': 'approved'} for t in res['tasks']}, 'Good plan')
check(plan.state == 'approved', 'the manager approves')

# ---------------------------------------------------------------- after approval: a change request, accepted in one click
msg = raises(lambda: S.save(fields.Date.to_string(m0), [dict(t) for t in S.month(fields.Date.to_string(m0))['tasks']], False))
check(msg and 'propose' in msg, 'an approved plan is not edited directly (no reset): the screen offers "Propose a change"')
cur = [dict(t) for t in S.month(fields.Date.to_string(m0))['tasks']]
cur[0]['target_qty'] = 1000000                     # change
dropped = cur.pop(3)                               # drop
cur.append(row('Collect 40 farmer testimonials for the radio campaign', qty='40', unit='testimonials'))  # add
msg = raises(lambda: S.propose_change(fields.Date.to_string(m0), cur, ''))
check(bool(msg), 'a change needs a short reason')
out = S.propose_change(fields.Date.to_string(m0), cur, 'Dealer stock-out: collections slower this month')
check(len(out['changes']) == 3 and plan.state == 'approved', '3 changes waiting; the plan stays approved meanwhile (%s)' %
      [c['kind'] for c in out['changes']])
check(any(t.name == dropped['name'] for t in plan.task_ids), 'nothing changes before the manager accepts')
res = SM.decide_changes(plan.id, [c['id'] for c in SM.review(plan.id)['changes']], [], '')
names = plan.task_ids.mapped('name')
check(dropped['name'] not in names and 'Collect 40 farmer testimonials for the radio campaign' in names and
      plan.task_ids.filtered(lambda t: t.name.startswith('Collect overdue')).target_qty == 1000000 and plan.state == 'approved',
      'one click applies all three changes; the plan stays approved')
msg = raises(lambda: S.decide_changes(plan.id, [], [], ''), Exception)
check(bool(msg), 'the owner cannot accept changes to their own plan')

# ---------------------------------------------------------------- send back keeps every line
n = len(plan.task_ids)
msg = raises(lambda: SM.send_back(plan.id, ''))
check(bool(msg), 'sending back needs a reason')
SM.send_back(plan.id, 'Add the Bishoftu dealer visits')
check(plan.state == 'returned' and len(plan.task_ids) == n and plan.manager_note, 'sent back with a reason, all %s lines kept' % n)

# ---------------------------------------------------------------- copy of last month with nothing changed: a warning
nxt0 = month_bounds(m1 + timedelta(days=1))[0]
shift = nxt0 - m0
same = [dict(row(t.name, done=t.done_when or '', qty=t.target_qty or '', key='c%d' % t.id),
             date_from=fields.Date.to_string(min(t.date_from + shift, month_bounds(nxt0)[1])),
             date_to=fields.Date.to_string(min(t.date_to + shift, month_bounds(nxt0)[1])), role_item_id=t.role_item_id.id or '')
        for t in plan.task_ids]
res = S.check(fields.Date.to_string(nxt0), same)
check(('warn', 'copied_all') in codes(res) and not res['blocking'], 'a copy of last month with nothing changed warns (manager decides)')

# ---------------------------------------------------------------- check-ins: one tap
plan.state = 'approved'
t = plan.task_ids.filtered(lambda x: x.date_from <= today <= x.date_to)[:1]
if not t:   # the test month is in the future: check in on a task running today, in this month
    p2 = env['sb.plan'].create({'employee_id': me.id, 'month': month_bounds(today)[0], 'month_end': month_bounds(today)[1],
                                'label': 'test', 'state': 'approved'})
    t = env['sb.plan.task'].create({'plan_id': p2.id, 'name': 'Reconcile CBE account', 'target_qty': 1, 'date_from': today, 'date_to': today})
S.checkin(t.id, fields.Date.to_string(today), 'on_track', 0, '')
check(t.checkin_ids and t.status == 'on_track', 'on track: one tap, no note or percentage needed')
S.checkin(t.id, fields.Date.to_string(today), 'done', None, '')
check(t.status == 'done' and t.progress == 100, 'done: one tap, no evidence note forced')
msg = raises(lambda: S.checkin(t.id, fields.Date.to_string(today), 'blocked', 0, ''))
check(bool(msg), 'blocked still asks what blocks it')

print('%s of %s passed' % (sum(ok), len(ok)))
env.cr.rollback()
print('rolled back')
