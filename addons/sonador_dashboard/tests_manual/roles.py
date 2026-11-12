"""Dashboard by role: run in an odoo shell on a copy (never on a live database).

    odoo shell -d <db> < tests_manual/roles.py

Opens the dashboard as each person found by login (DASH_LOGINS, comma separated; default: the admin plus the first
store keeper and accountant found) and checks that:
  - it opens for everyone (no block may break the page),
  - a person without accounting rights sees no money (no bank, debtors or sales tiles, no money signals, no sales chart),
  - every tile, signal and queue that opens records points at a model the person can read,
  - the briefing has at least one sentence.
Prints what each one sees. Changes nothing (rolled back)."""
import os

env = env  # noqa: F821 - given by odoo shell

D = 'sb.dashboard'
MONEY_TILES = {'cash', 'ar', 'sales', 'out'}
MONEY_SIGNALS = {'sales_trend', 'overdue', 'cash_neg', 'dup_journals', 'future', 'no_lock'}


def users():
    logins = [x.strip() for x in os.environ.get('DASH_LOGINS', '').split(',') if x.strip()]
    U = env['res.users'].with_context(active_test=True)
    if logins:
        return U.search([('login', 'in', logins)])
    found = U.browse(2)
    store = U.search([('share', '=', False), ('group_ids', 'in', env.ref('stock.group_stock_user').ids),
                      ('group_ids', 'not in', env.ref('account.group_account_readonly').ids)], limit=1)
    acc = U.search([('share', '=', False), ('group_ids', 'in', env.ref('account.group_account_user').ids), ('id', '!=', 2)], limit=1)
    return found | store | acc


fails = []
for u in users():
    e = env(user=u.id)
    d = e[D].data()
    money = e.user.has_group('account.group_account_readonly')
    print('\n=== %s (%s) · %s · money=%s stock=%s' % (u.name, u.login, d['role'] or '-', d['money'], d['stock']))
    for p in d['brief']['paragraphs']:
        print('  brief:', p)
    print('  needs: %d approvals, queues: %s' % (d['needs']['approvals_total'], ', '.join('%s %d' % (q['title'], q['count']) for q in d['needs']['queues'])))
    print('  tiles:', ' | '.join('%s %s %s (%s)' % (t['label'], t['value'], t.get('unit', ''), t.get('sub', '')) for t in d['tiles']))
    for s in d['signals']:
        print('  [%s] %s: %s' % (s['level'], s['title'], s['figure']))
    print('  charts:', [c['title'] for c in d['charts']], '· ask:', d['ai'], '·', d['hidden'])
    if not d['brief']['paragraphs']:
        fails.append('%s: empty briefing' % u.login)
    if not money:
        leak = [t['key'] for t in d['tiles'] if t['key'] in MONEY_TILES] + [s['key'] for s in d['signals'] if s['key'] in MONEY_SIGNALS]
        leak += [c['key'] for c in d['charts'] if c['key'] == 'sales']
        if leak:
            fails.append('%s sees money without accounting rights: %s' % (u.login, leak))
    for x in d['tiles'] + d['signals'] + d['needs']['queues']:
        act = x.get('action') or ({'model': x['model'], 'domain': x['domain']} if x.get('model') else None)
        if act and not e[act['model']].has_access('read'):
            fails.append('%s: %s opens %s, which the role cannot read' % (u.login, x.get('key'), act['model']))
        if act:
            e[act['model']].search_count(act['domain'])  # the domain must be valid
env.cr.rollback()
print('\nFAIL:\n  ' + '\n  '.join(fails) if fails else '\nALL PASS')
