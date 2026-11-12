"""Dashboard speed: time each block for one person (odoo shell; changes nothing).

    DASH_LOGIN=leulm@ecogreen.et odoo shell -d <db> < tests_manual/speed.py
"""
import os
import time

env = env  # noqa: F821
u = env['res.users'].search([('login', '=', os.environ.get('DASH_LOGIN', 'admin'))], limit=1)
D = env(user=u.id)['sb.dashboard']


def t(label, fn):
    env.invalidate_all()
    for k in [k for k in env.cr.cache if str(k).startswith('sb_dashboard')]:
        del env.cr.cache[k]
    a = time.time()
    fn()
    print('%-22s %6.0f ms' % (label, (time.time() - a) * 1000))


t('approvals (Me)', lambda: env(user=u.id)['sb.me'].approvals())
t('needs (queues)', lambda: D._needs())
t('tiles', lambda: D._dash_tiles())
t('signals', lambda: D._dash_signals())
for name in ('_sig_sales_trend', '_sig_overdue', '_sig_cash_below_zero', '_sig_duplicate_journals', '_sig_future_entries',
             '_sig_no_lock', '_sig_negative_stock', '_sig_old_drafts', '_sig_old_approvals'):
    t('  ' + name, lambda n=name: getattr(D, n)())
t('charts', lambda: D._dash_charts())
t('data() total', lambda: D.data())
env.cr.rollback()
a = time.time()
D.data()
print('%-22s %6.0f ms' % ('data() again (kept)', (time.time() - a) * 1000))
a = time.time()
D.data(fresh=True)
print('%-22s %6.0f ms' % ('data(fresh=True)', (time.time() - a) * 1000))
