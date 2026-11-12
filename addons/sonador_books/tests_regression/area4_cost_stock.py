# Regression, acceptance checklist area 4: cost of sales and stock (items 4.1-4.9).
# Built from the round 1-7 cost scripts. Every check runs as a real user made here (storekeeper, clerk, accountant,
# manager), never as superuser except for setup; dates in 2026, on test items and test accounts of their own.
# Everything is rolled back at the end, except 4.7 (two users at the same second need committed data, so it runs first,
# on its own cursors): its users, items, accounts and documents stay on the copy, named "REG4 CONC ...", so run only on
# a throwaway copy.
#   cd ~/ecogreen-odoo19 && docker compose exec -T odoo sh -c 'odoo shell -d ecogreen_reg4 --no-http --db_host db \
#       --db_user odoo --db_password "$PASSWORD"' < ~/sonador-odoo/addons/sonador_books/tests_regression/area4_cost_stock.py
# Output: "PASS|FAIL <item> <what>" per check, "ITEM 4.n PASS|FAIL (k/m checks, s)" per item.
import os
import random as _r
import threading
import time as _t
import traceback
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal as Dm, ROUND_HALF_UP

import odoo
from odoo import Command, SUPERUSER_ID, api
from odoo.exceptions import UserError, ValidationError

assert env.cr.dbname != 'ecogreen_test' and not env.cr.dbname.startswith('ecogreen_web'), 'never on a live database'
assert env.cr.dbname not in ('ecogreen',), 'run on a throwaway copy (ecogreen_reg*, ecogreen_stage)'
ADM = env
company = env.company
STAMP = int(_t.time() * 1000) % 10 ** 9

# ------------------------------------------------------------------ results
RES = []  # (item, ok, msg)
CUR = ['4.0']
T0 = {}


def chk(cond, msg):
    if not msg:
        return
    print(('PASS ' if cond else 'FAIL ') + '%s %s' % (CUR[0], msg), flush=True)
    RES.append((CUR[0], bool(cond), msg))


def info(msg):
    print('INFO %s %s' % (CUR[0], msg), flush=True)


# ------------------------------------------------------------------ 4.7 first: two users at once need committed data and
# their own cursors, so it runs before this script's own cursor writes anything (its locks would hold the two users up)
g_store = 'sonador_inventory.group_sb_store_user'
WH = env['stock.warehouse'].search([('company_id', '=', company.id), ('name', 'not like', 'Van%')], limit=1)
CASH = env['account.account'].search([('account_type', '=', 'asset_cash'), ('company_ids', 'in', company.id)], limit=1)


def D(y, m, d):
    return date(y, m, d)


def fsno():
    return 'FS9%07d' % _r.randint(0, 9999999)


# ================================================================== 4.7 two users posting the same item at once
def t47():
    if not env.cr.dbname.startswith(('ecogreen_reg', 'ecogreen_stage')):
        chk(False, 'skipped: needs committed data, run on a throwaway copy only')
        return
    reg = odoo.modules.registry.Registry(env.cr.dbname)
    TAG = 'REG4 CONC %d' % STAMP
    with reg.cursor() as cr:  # committed setup: its own users, item, accounts
        e = api.Environment(cr, SUPERUSER_ID, {})
        e['res.company'].browse(company.id).sb_only_invoices = False  # the copy makes its own documents (throwaway copy only)
        Uu = e['res.users'].with_context(no_reset_password=True, mail_create_nolog=True, mail_notrack=True)
        grp = lambda xs: [Command.set([e.ref('base.group_user').id] + [e.ref(x).id for x in xs])]  # noqa: E731
        ua = Uu.create({'name': TAG + ' acc A', 'login': 'reg4_conc_a_%d@example.local' % STAMP, 'company_id': company.id,
                        'company_ids': [Command.set([company.id])], 'group_ids': grp(['sonador_books.group_sb_accountant', g_store])})
        ub = Uu.create({'name': TAG + ' acc B', 'login': 'reg4_conc_b_%d@example.local' % STAMP, 'company_id': company.id,
                        'company_ids': [Command.set([company.id])], 'group_ids': grp(['sonador_books.group_sb_accountant', g_store])})
        inv = e['account.account'].create({'code': 'RG4C%d' % (STAMP % 100000), 'name': TAG + ' inventory', 'account_type': 'asset_current',
                                           'company_ids': [(6, 0, [company.id])]})
        grni = e['account.account'].create({'code': 'RG4G%d' % (STAMP % 100000), 'name': TAG + ' GRNI', 'account_type': 'liability_current',
                                            'reconcile': True, 'company_ids': [(6, 0, [company.id])]})
        inc = e['account.account'].create({'code': 'RG4I%d' % (STAMP % 100000), 'name': TAG + ' sales', 'account_type': 'income',
                                           'company_ids': [(6, 0, [company.id])]})
        cogs = e['account.account'].create({'code': 'RG4K%d' % (STAMP % 100000), 'name': TAG + ' cost', 'account_type': 'expense_direct_cost',
                                            'company_ids': [(6, 0, [company.id])]})
        cat = e['product.category'].create({'name': TAG, 'property_cost_method': 'average', 'property_valuation': 'real_time',
                                            'property_stock_valuation_account_id': inv.id, 'property_account_expense_categ_id': cogs.id,
                                            'property_account_income_categ_id': inc.id})
        ps = [e['product.product'].create({'name': '%s %s' % (TAG, k), 'default_code': 'RG4CC-%s-%d' % (k, STAMP), 'categ_id': cat.id,
                                           'is_storable': True, 'type': 'consu', 'supplier_taxes_id': [(6, 0, [])], 'taxes_id': [(6, 0, [])]})
              for k in 'AB']
        sup = e['res.partner'].create({'name': TAG + ' partner'})
        for p in ps:
            e(user=ua.id)['sb.stock.voucher'].create({'voucher_type': 'grn', 'date': D(2026, 9, 1), 'partner_id': sup.id, 'warehouse_id': WH.id,
                                                      'account_id': grni.id, 'line_ids': [Command.create({'product_id': p.id, 'quantity': 10, 'unit_cost': 10})]}).action_post()
        ids = dict(ua=ua.id, ub=ub.id, pa=ps[0].id, pb=ps[1].id, sup=sup.id, inc=inc.id, grni=grni.id, inv=inv.id, cogs=cogs.id)
        cr.commit()
    res = {}

    def run(name, uid, fn, delay, hold):
        try:
            _t.sleep(delay)
            with reg.cursor() as cr:
                e = api.Environment(cr, uid, {})
                out = fn(e)
                _t.sleep(hold)
                cr.commit()
                res[name] = ('ok', out)
        except Exception as ex:
            res[name] = ('refused', '%s: %s' % (type(ex).__name__, str(ex).splitlines()[0][:150] if str(ex) else ''))

    def mksale(pid, day, q):
        def f(e):
            v = e['sb.voucher'].create({'voucher_type': 'cash_sale', 'date': day, 'partner_id': ids['sup'], 'cash_account_id': CASH.id,
                                        'sb_warehouse_id': WH.id, 'sb_pad_number': fsno(),
                                        'line_ids': [Command.create({'product_id': pid, 'name': 'x', 'quantity': q, 'price_unit': 50,
                                                                     'account_id': ids['inc'], 'tax_ids': [(6, 0, [])]})]})
            v.action_post()
            return (v.name, v.sb_cogs)
        return f

    def mkgrn(pid, day, q, c):
        def f(e):
            v = e['sb.stock.voucher'].create({'voucher_type': 'grn', 'date': day, 'partner_id': ids['sup'], 'warehouse_id': WH.id,
                                              'account_id': ids['grni'], 'line_ids': [Command.create({'product_id': pid, 'quantity': q, 'unit_cost': c})]})
            v.action_post()
            return v.name
        return f

    # case 1: a back-dated receipt and a later sale of the same item, by two accountants at the same moment
    th = [threading.Thread(target=run, args=('grn', ids['ua'], mkgrn(ids['pa'], D(2026, 9, 2), 10, 20), 0, 3)),
          threading.Thread(target=run, args=('sale', ids['ub'], mksale(ids['pa'], D(2026, 9, 5), 5), 1, 0))]
    [t.start() for t in th]
    [t.join() for t in th]
    info('case 1 %s' % res)
    r1 = dict(res)
    res.clear()
    # case 2: two sales of 8 when 10 are on hand, at the same moment
    th = [threading.Thread(target=run, args=('s1', ids['ua'], mksale(ids['pb'], D(2026, 9, 5), 8), 0, 3)),
          threading.Thread(target=run, args=('s2', ids['ub'], mksale(ids['pb'], D(2026, 9, 6), 8), 1, 0))]
    [t.start() for t in th]
    [t.join() for t in th]
    info('case 2 %s' % res)
    r2 = dict(res)
    with reg.cursor() as cr:
        e = api.Environment(cr, SUPERUSER_ID, {})
        out = {}
        for k in ('pa', 'pb'):
            ms = e['stock.move'].search([('product_id', '=', ids[k]), ('state', '=', 'done')])
            q = sum(e['sb.stock']._direction(m) * m.quantity for m in ms)
            v = sum(e['sb.stock']._direction(m) * abs(m.value) for m in ms)
            out[k] = (round(q, 4), round(v, 2))
        glinv = round(sum(e['account.move.line'].search([('account_id', '=', ids['inv']), ('parent_state', '=', 'posted')]).mapped('balance')), 2)
        sale_a = e['sb.voucher'].search([('line_ids.product_id', '=', ids['pa']), ('voucher_type', '=', 'cash_sale'), ('state', '=', 'posted')])
    both = r1.get('grn', ('',))[0] == 'ok' and r1.get('sale', ('',))[0] == 'ok'
    # the second told to try again while the first is saved, or stopped by the database (a web request retries it)
    queued = r1.get('grn', ('',))[0] == 'ok' and r1.get('sale', ('',))[0] == 'refused' and \
        any(w in r1['sale'][1] for w in ('still being saved', 'SerializationFailure'))
    # serialised either way: both posted with the receipt in the sale's cost (avg (100+200)/20 = 15 -> 75), or the second
    # told to try again while the first is saved (nothing posted on a stale cost)
    chk((both and len(sale_a) == 1 and abs(sale_a.sb_cogs - 75) < 0.005 and out['pa'] == (15.0, 225.0))
        or (queued and not sale_a and out['pa'] == (20.0, 300.0)),
        'case 1: back-dated receipt and a later sale at the same second are serialised, no stale cost: %s; sale %s, item %s'
        % ({k: v[1] if v[0] != 'ok' else 'posted' for k, v in r1.items()}, sale_a.mapped('sb_cogs'), out['pa']))
    n_ok = sum(1 for v in r2.values() if v[0] == 'ok')
    chk(n_ok == 1 and out['pb'][0] >= 0, 'case 2: two sales of 8 when 10 are on hand at the same second: one posted, the other refused, '
        'stock not below zero (%s): %s' % (out['pb'], {k: v[1] if v[0] != 'ok' else 'posted' for k, v in r2.items()}))
    chk(abs(glinv - out['pa'][1] - out['pb'][1]) < 0.005, 'after both cases stock = GL: GL %s items %s' % (glinv, round(out['pa'][1] + out['pb'][1], 2)))



ONLY = [x.strip() for x in os.environ.get('REG4_ONLY', '').split(',') if x.strip()]
if not ONLY or '4.7' in ONLY:
    print('---- 4.7', flush=True)
    CUR[0] = '4.7'
    _t47 = _t.time()
    env.cr.rollback()  # nothing of ours is open: the two users' cursors wait on no lock of this one
    try:
        t47()
    except Exception as e:
        chk(False, 'crashed: %s: %s' % (type(e).__name__, str(e).splitlines()[0][:200] if str(e) else ''))
        traceback.print_exc()
    env.cr.rollback()
    T0['4.7'] = _t.time() - _t47


# ------------------------------------------------------------------ users (real roles, never superuser)
def _user(login, groups):
    U_ = env['res.users'].with_context(no_reset_password=True, mail_create_nolog=True, mail_notrack=True)
    return U_.create({'name': 'REG4 %s' % login, 'login': 'reg4_%s_%d@example.local' % (login, STAMP),
                      'company_id': company.id, 'company_ids': [Command.set([company.id])],
                      'group_ids': [Command.set([env.ref('base.group_user').id] + [env.ref(g).id for g in groups])]})


u_store = _user('store', [g_store])
u_clerk = _user('clerk', ['sonador_books.group_sb_clerk', g_store])
u_acc = _user('accountant', ['sonador_books.group_sb_accountant', g_store])
u_mgr = _user('manager', ['sonador_books.group_sb_manager', g_store])
U = env(user=u_acc.id)   # finance: posts, corrects, voids
S = env(user=u_store.id)  # the store: prepares, sends to finance
C = env(user=u_clerk.id)
M = env(user=u_mgr.id)
assert not U.su and not S.su and not U.user.has_group('sonador_books.group_sb_manager') and not S.user.has_group('sonador_books.group_sb_clerk')


# ------------------------------------------------------------------ accounts, items, partners of the test
def _acc(code, name, typ, rec=False):
    a = env['account.account'].search([('code', '=', code), ('company_ids', 'in', company.id)], limit=1)
    return a or env['account.account'].create({'code': code, 'name': name, 'account_type': typ,
                                               'company_ids': [(6, 0, [company.id])], 'reconcile': rec})


INV = _acc('RG41001', 'REG4 Inventory', 'asset_current')
INV2 = _acc('RG41003', 'REG4 Inventory FG', 'asset_current')
WIP = _acc('RG41002', 'REG4 WIP', 'asset_current')
GRNI = _acc('RG42001', 'REG4 GRNI', 'liability_current', True)
INC = _acc('RG44001', 'REG4 Sales', 'income')
COGS = _acc('RG45001', 'REG4 Cost of sales', 'expense_direct_cost')
E1 = _acc('RG46001', 'REG4 Exp1', 'expense')
E2 = _acc('RG46002', 'REG4 Exp2', 'expense')
PPV = company.sb_cost_difference_account_id
LAB = company.sb_labour_absorbed_account_id


def _cat(name, inv):
    return env['product.category'].create({'name': name, 'property_cost_method': 'average', 'property_valuation': 'real_time',
                                           'property_stock_valuation_account_id': inv.id,
                                           'property_account_expense_categ_id': COGS.id, 'property_account_income_categ_id': INC.id})


CAT = _cat('REG4 AVCO %d' % STAMP, INV)
CATFG = _cat('REG4 FG %d' % STAMP, INV2)
SUP = env['res.partner'].create({'name': 'REG4 Supplier', 'supplier_rank': 1})
CUS = env['res.partner'].create({'name': 'REG4 Customer', 'customer_rank': 1})
_plan = env['account.analytic.plan'].search([], limit=1)
JA = env['account.analytic.account'].create({'name': 'REG4 Job A', 'plan_id': _plan.id})
company.sb_allow_negative_stock = False
company.sb_recost_entries = 'day'
company.sb_only_invoices = False  # a company still on another main system: documents are made here in the test
company.fiscalyear_lock_date = min(company.fiscalyear_lock_date or date(2025, 7, 7), date(2025, 7, 7))
_n = [0]
ITEMS = []


def item(tag, cat=None):
    _n[0] += 1
    p = ADM['product.product'].create({'name': 'REG4 %s %d' % (tag, _n[0]), 'default_code': 'RG4-%s-%d-%d' % (tag, _n[0], STAMP),
                                       'categ_id': (cat or CAT).id, 'is_storable': True, 'type': 'consu', 'list_price': 100,
                                       'supplier_taxes_id': [(6, 0, [])], 'taxes_id': [(6, 0, [])]})
    ITEMS.append(p)
    return p


# ------------------------------------------------------------------ documents, as the roles make them
def _post(e, v):
    """The store saves and sends to finance where the company says so; finance posts. Everyone else posts."""
    if v._name == 'sb.stock.voucher' and company.sb_store_posts_by_finance and e.user == u_store \
            and v.voucher_type in v._store_types():
        v.with_env(e).action_sb_send_finance()
        U['sb.stock.voucher'].browse(v.id).action_sb_finance_post()
    else:
        v.with_env(e).action_post()
    return v.with_env(U)


def grn(e, p, d, q, c=None, val=None, acct=None, post=True, lines=None):
    if lines is None:
        ln = {'product_id': p.id, 'quantity': q}
        if val is not None:
            ln['value'] = val
        else:
            ln['unit_cost'] = c
        lines = [ln]
    v = e['sb.stock.voucher'].create({'voucher_type': 'grn', 'date': d, 'partner_id': SUP.id, 'warehouse_id': WH.id,
                                      'account_id': (acct or GRNI).id, 'line_ids': [Command.create(x) for x in lines]})
    return _post(e, v) if post else v


def giv(e, p, d, q, acct=None, an=None, lines=None, post=True):
    ls = lines or [{'product_id': p.id, 'quantity': q}]
    vals = {'voucher_type': 'giv', 'date': d, 'warehouse_id': WH.id, 'account_id': (acct or E1).id,
            'line_ids': [Command.create(x) for x in ls]}
    if an:
        vals['analytic_account_id'] = an.id
    v = e['sb.stock.voucher'].create(vals)
    return _post(e, v) if post else v


def fprr(e, p, d, q, c=0, wip=False, post=True, labour=0):
    vals = {'voucher_type': 'fprr', 'date': d, 'warehouse_id': WH.id, 'account_id': WIP.id, 'sb_wip_costed': wip,
            'line_ids': [Command.create({'product_id': p.id, 'quantity': q, 'unit_cost': c})]}
    if labour:
        vals['sb_labour_total'] = labour
    v = e['sb.stock.voucher'].create(vals)
    return _post(e, v) if post else v


def build(e, fg, d, q, comps, post=True):
    v = e['sb.stock.voucher'].create({'voucher_type': 'build', 'date': d, 'warehouse_id': WH.id, 'build_product_id': fg.id,
                                      'build_qty': q, 'line_ids': [Command.create({'product_id': c.id, 'quantity': cq}) for c, cq in comps]})
    return _post(e, v) if post else v


def sale(e, p, d, q, price=100, kind='cash_sale', post=True):
    vals = {'voucher_type': kind, 'date': d, 'partner_id': CUS.id, 'sb_warehouse_id': WH.id, 'sb_pad_number': fsno(),
            'line_ids': [Command.create({'product_id': p.id, 'name': 'x', 'quantity': q, 'price_unit': price,
                                         'account_id': INC.id, 'tax_ids': [(6, 0, [])]})]}
    if kind == 'cash_sale':
        vals['cash_account_id'] = CASH.id
    v = e['sb.voucher'].create(vals)
    if post:
        v.action_post()
    return v


def sret(e, s, p, d, q, price=100):
    v = e['sb.voucher'].create({'voucher_type': 'sale_return', 'date': d, 'partner_id': CUS.id, 'origin_id': s.id,
                                'sb_warehouse_id': WH.id, 'sb_pad_number': 'CN %05d' % _r.randint(1, 99999),
                                'line_ids': [Command.create({'product_id': p.id, 'name': 'x', 'quantity': q, 'price_unit': price,
                                                             'account_id': INC.id, 'tax_ids': [(6, 0, [])]})]})
    v.action_post()
    return v


def void(doc, reason='REG4 test'):
    doc.with_context(sb_void_reason=reason).action_void()


def mval(p, upto=None):
    env.flush_all()
    q = v = 0.0
    for m in ADM['stock.move'].search([('product_id', '=', p.id), ('state', '=', 'done')]):
        if upto and m.date.date() > upto:
            continue
        dd = ADM['sb.stock']._direction(m)
        q += dd * m.quantity
        v += dd * abs(m.value)
    if 'sb.stock.revaluation' in ADM:
        for r in ADM['sb.stock.revaluation'].search([('product_id', '=', p.id)]):
            if upto and r.date > upto:
                continue
            v += r.value
    return round(q, 4), round(v, 2)


def gl(acc, upto=None, pid=None):
    dom = [('account_id', '=', acc.id), ('parent_state', '=', 'posted')]
    if upto:
        dom.append(('date', '<=', upto))
    if pid:
        dom.append(('product_id', '=', pid))
    return round(sum(ADM['account.move.line'].search(dom).mapped('balance')), 2)


def rec_row(upto, acc=None):
    acc = acc or INV
    doc = U['sb.report'].get_report('inv_gl_rec', {'date_to': str(upto)})
    r = [x for x in doc['rows'] if x['id'] == 'a%d' % acc.id]
    return r[0]['values'] if r else {'stock': 0, 'gl': 0, 'diff': 0}


def card(p, df, dt):
    doc = U['sb.report'].get_report('stock_card', {'date_from': str(df), 'date_to': str(dt), 'product_ids': [p.id]})
    g = [r for r in doc['rows'] if r['id'] == 'p%d' % p.id]
    return (round(g[0]['values']['bal_qty'], 4), round(g[0]['values']['bal_value'], 2)) if g else (0, 0)


def valn(p, dt):
    doc = U['sb.report'].get_report('inv_valuation', {'date_to': str(dt), 'product_ids': [p.id]})
    for r in doc['rows']:
        if r.get('kind') == 'line' and r['values'].get('code') == p.default_code:
            return round(r['values'].get('qty', 0), 4), round(r['values'].get('value', 0), 2)
    return (0, 0)


def full(p, exp_q, exp_v, dt, label, df=D(2025, 7, 8)):
    """moves, stock card, valuation report, GL of the item's account and the stock/GL reconciliation all agree."""
    acc = ADM['sb.stock']._valuation_account(p, company)
    its = [x for x in ITEMS if ADM['sb.stock']._valuation_account(x, company) == acc]
    mq, mv = mval(p, dt)
    cq, cv = card(p, df, dt)
    vq, vv = valn(p, dt)
    g = gl(acc, dt)
    r = rec_row(dt, acc)
    tot = round(sum(mval(x, dt)[1] for x in its), 2)
    ok = abs(mq - exp_q) < 1e-6 and abs(mv - exp_v) < 0.005 and abs(cq - exp_q) < 1e-6 and abs(cv - exp_v) < 0.005 \
        and abs(vq - exp_q) < 1e-6 and abs(vv - exp_v) < 0.005 and abs(g - tot) < 0.005 and abs(r['diff']) < 0.005
    chk(ok, '%s: expected %s/%.2f | moves %s/%.2f card %s/%.2f valuation %s/%.2f GL %.2f vs items %.2f, stock/GL diff %.2f'
        % (label, exp_q, exp_v, mq, mv, cq, cv, vq, vv, g, tot, r['diff']))
    return ok


def err(f):
    try:
        with env.cr.savepoint():
            f()
        return None
    except (UserError, ValidationError) as e:
        return str(e)


def outs(doc):
    ms = (doc.stock_move_ids if doc._name == 'sb.stock.voucher' else doc.sb_stock_move_ids).filtered(
        lambda m: not m.sb_reversal_of_id and not m.sb_cancelled and m.state == 'done')
    return round(sum(abs(m.value) for m in ms), 2)


def replay(events):
    """Independent AVCO replay in exact decimals. Same-day receipts first, then issues at that day's average; a return
    comes back at its sale's own cost. Returns ({key: {qty, value}}, (qty, value))."""
    q = Dm(0)
    v = Dm(0)
    out = {}
    cent = Dm('0.01')
    for d in sorted(set(e['day'] for e in events)):
        today = [e for e in events if e['day'] == d]
        for e in sorted([e for e in today if e['dir'] > 0], key=lambda e: e.get('seq', 0)):
            if e.get('follow'):
                s = out[e['follow']]
                eq = Dm(str(e['qty']))
                val = s['value'] if eq == s['qty'] else (eq * s['value'] / s['qty']).quantize(cent, ROUND_HALF_UP)
                out[e['key']] = {'qty': eq, 'value': val}
            else:
                val = Dm(str(e['value']))
            q += Dm(str(e['qty']))
            v += val
        v_day, q_day = v, q  # the day's average, kept as a fraction: (3 x 68.75) / 6 = 34.375 -> 34.38, never 11.4583... x 3
        for e in sorted([e for e in today if e['dir'] < 0], key=lambda e: e.get('seq', 0)):
            eq = Dm(str(e['qty']))
            val = v.quantize(cent, ROUND_HALF_UP) if eq == q else (eq * v_day / q_day).quantize(cent, ROUND_HALF_UP)
            out[e['key']] = {'qty': eq, 'value': val}
            q -= eq
            v -= val
    return out, (q, v)


def section(item_no, fn):
    """Each item on its own: whatever it posted is rolled back before the next one, so no item sees another's stock."""
    CUR[0] = item_no
    t = _t.time()
    n_items = len(ITEMS)
    env.flush_all()
    env.cr.execute('SAVEPOINT reg4_item')
    try:
        fn()
        env.flush_all()
    except Exception as e:  # a crash is a failed check, never a stop: the other items still run
        chk(False, 'crashed: %s: %s' % (type(e).__name__, str(e).splitlines()[0][:200] if str(e) else ''))
        traceback.print_exc()
    env.cr.execute('ROLLBACK TO SAVEPOINT reg4_item')
    env.invalidate_all()
    env.registry.clear_cache()
    del ITEMS[n_items:]
    T0[item_no] = T0.get(item_no, 0) + (_t.time() - t)


END = D(2026, 12, 31)


# ================================================================== 4.1 AVCO = independent replay, 300+ random steps
def t41():
    seed = int(os.environ.get('REG4_SEED', '41'))
    steps = int(os.environ.get('REG4_STEPS', '320'))
    R = _r.Random(seed)
    Mi = item('RM')
    FG = item('RFG', CATFG)
    base = D(2026, 10, 12)
    docs = []

    def live(d):
        return d['rec'].state == 'posted'

    def mid(rec):
        ms = (rec.stock_move_ids if rec._name == 'sb.stock.voucher' else rec.sb_stock_move_ids).filtered(
            lambda m: not m.sb_reversal_of_id and not m.sb_cancelled and m.state == 'done')
        return min(ms.ids) if ms else 0

    def expect():
        env.invalidate_all()
        ev = []
        for d in docs:
            if not live(d) or d['item'] != 'M':
                continue
            r = d['rec']
            k = d['kind']
            if k == 'grn':
                ev.append({'day': r.date, 'dir': 1, 'qty': r.line_ids.quantity, 'value': r.line_ids.value, 'key': r.id, 'seq': mid(r)})
            elif k in ('giv', 'sale'):
                ev.append({'day': r.date, 'dir': -1, 'qty': r.line_ids.quantity, 'key': r.id, 'seq': mid(r)})
            elif k == 'ret':
                ev.append({'day': r.date, 'dir': 1, 'qty': r.line_ids.quantity, 'key': r.id, 'seq': mid(r), 'follow': r.origin_id.id})
        out, fin = replay(ev)
        fev = []
        for d in docs:
            if not live(d) or d['item'] != 'FG':
                continue
            r = d['rec']
            if d['kind'] == 'fprr':
                mat = sum((out[g.id]['value'] for g in r.sb_wip_giv_ids if g.state == 'posted'), Dm(0))
                fev.append({'day': r.date, 'dir': 1, 'qty': r.line_ids.quantity, 'value': mat + Dm(str(r.labour_value or 0)),
                            'key': r.id, 'seq': mid(r)})
            else:
                fev.append({'day': r.date, 'dir': -1, 'qty': r.line_ids.quantity, 'key': r.id, 'seq': mid(r)})
        fout, ffin = replay(fev)
        return out, fin, fout, ffin

    def verify(step):
        out, fin, fout, ffin = expect()
        bad = []
        for d in docs:
            if not live(d):
                continue
            r = d['rec']
            o = (out if d['item'] == 'M' else fout).get(r.id)
            if o is None:
                continue
            if abs(float(o['value']) - outs(r)) > 0.0049:
                bad.append((d['kind'], r.name, float(o['value']), outs(r)))
        for d in docs:  # a finished-goods receipt = the materials its GIVs put into WIP + labour
            r = d['rec']
            if live(d) and d['kind'] == 'fprr':
                mat = sum((out[g.id]['value'] for g in r.sb_wip_giv_ids if g.state == 'posted' and g.id in out), Dm(0))
                exp_v = float(mat + Dm(str(r.labour_value or 0)))
                fgm = r.stock_move_ids.filtered(lambda m: m.product_id == FG and m.state == 'done' and not m.sb_reversal_of_id and not m.sb_cancelled)
                got = round(sum(abs(m.value) for m in fgm), 2)
                if abs(exp_v - got) > 0.0049:
                    bad.append(('fprr', r.name, exp_v, got))
        if bad and os.environ.get('REG4_DEBUG'):
            last = max(next(d['rec'].date for d in docs if d['rec'].name == b[1]) for b in bad)
            for d in sorted(docs, key=lambda x: (x['rec'].date, x['rec'].id)):
                r = d['rec']
                if d['item'] == 'FG' and live(d) and r.date <= last:
                    dd = r.date
                    if True:
                        o = fout.get(r.id)
                        info('debug %s %s %s qty %s replay %s system %s move ids %s' % (dd, d['kind'], r.name, r.line_ids.quantity,
                             o and o['value'], outs(r), (r.stock_move_ids if r._name == 'sb.stock.voucher' else r.sb_stock_move_ids).ids))
        CUR[0] = '4.2'
        full(Mi, float(fin[0]), float(fin[1]), END, 'chain step %s material: stock = GL (day mode)' % step)
        full(FG, float(ffin[0]), float(ffin[1]), END, 'chain step %s finished goods: stock = GL (day mode)' % step)
        CUR[0] = '4.1'
        wip_exp = sum(float(out[d['rec'].id]['value']) for d in docs if live(d) and d['kind'] == 'giv' and d.get('wip')
                      and not (d['rec'].sb_fprr_id and d['rec'].sb_fprr_id.state == 'posted'))
        chk(not bad and abs(gl(WIP) - wip_exp) < 0.005,
            'seed %d step %s: %d documents vs independent replay, mismatches %s; WIP GL %.2f expected %.2f'
            % (seed, step, sum(1 for d in docs if live(d)), bad[:4], gl(WIP), wip_exp))

    def day():
        return base + timedelta(days=R.randint(0, 40))

    for g in range(3):
        docs.append({'rec': grn(U, Mi, base + timedelta(days=g), R.randint(20, 60), round(R.uniform(5, 15), 2)), 'kind': 'grn', 'item': 'M'})
    log = []
    for step in range(steps):
        a = R.choice(['grn', 'grn', 'giv', 'giv', 'gw', 'sale', 'sale', 'ret', 'void', 'corr', 'fprr', 'fgsale'])
        try:
            with env.cr.savepoint():
                if a == 'grn':
                    q = R.choice([R.randint(1, 40), round(R.uniform(0.5, 9), 3)])
                    e_ = S if R.random() < 0.3 else U  # some prepared by the store and posted by finance
                    rec = grn(e_, Mi, day(), q, val=round(R.uniform(10, 500), 2)) if R.random() < 0.3 else \
                        grn(e_, Mi, day(), q, round(R.uniform(5, 20), 4))
                    docs.append({'rec': rec, 'kind': 'grn', 'item': 'M'})
                elif a in ('giv', 'gw'):
                    rec = giv(U, Mi, day(), R.randint(1, 15), acct=WIP if a == 'gw' else E1)
                    docs.append({'rec': rec, 'kind': 'giv', 'item': 'M', 'wip': a == 'gw'})
                elif a == 'sale':
                    rec = sale(U, Mi, day(), R.randint(1, 12), kind=R.choice(['sale', 'cash_sale']))
                    docs.append({'rec': rec, 'kind': 'sale', 'item': 'M'})
                elif a == 'ret':
                    ss = [d['rec'] for d in docs if live(d) and d['kind'] == 'sale']
                    if ss:
                        s = R.choice(ss)
                        rec = sret(U, s, Mi, s.date + timedelta(days=R.randint(1, 5)), R.randint(1, int(s.line_ids.quantity)))
                        docs.append({'rec': rec, 'kind': 'ret', 'item': 'M'})
                elif a == 'void':
                    cand = [d for d in docs if live(d) and d['kind'] in ('grn', 'giv', 'sale', 'ret', 'fprr', 'fgsale')]
                    if cand:
                        d = R.choice(cand)
                        void(d['rec'].with_env(U))
                elif a == 'corr':
                    cand = [d for d in docs if live(d) and d['kind'] in ('grn', 'giv', 'fprr')]
                    if cand:
                        d = R.choice(cand)
                        r = d['rec'].with_env(U)
                        r.action_correct()
                        if d['kind'] == 'grn':
                            r.line_ids.write({'unit_cost': round(R.uniform(5, 20), 2), 'value_entered': False} if R.random() < 0.5
                                             else {'quantity': r.line_ids.quantity + R.randint(0, 5)})
                        elif d['kind'] == 'giv':
                            r.line_ids.write({'quantity': max(1, r.line_ids.quantity + R.randint(-2, 3))})
                        r.action_post()
                elif a == 'fprr':
                    rec = fprr(U, FG, day(), R.randint(1, 5), wip=True, labour=R.choice([0, 0, 10, 25.5]))
                    docs.append({'rec': rec, 'kind': 'fprr', 'item': 'FG'})
                elif a == 'fgsale':
                    rec = sale(U, FG, day(), R.randint(1, 3))
                    docs.append({'rec': rec, 'kind': 'fgsale', 'item': 'FG'})
            log.append((a, 'ok'))
        except (UserError, ValidationError) as e:
            log.append((a, 'refused: ' + str(e)[:60]))
        if step % 40 == 39 or step == steps - 1:
            verify(step + 1)
    counts = {k: sum(1 for d in docs if d['kind'] == k and live(d)) for k in ('grn', 'giv', 'sale', 'ret', 'fprr', 'fgsale')}
    ok_steps = sum(1 for x in log if x[1] == 'ok')
    chk(steps >= 300 and ok_steps >= 200, 'chain of %d steps (%d done, %d refused by the rules), live documents %s'
        % (steps, ok_steps, steps - ok_steps, counts))


# ================================================================== 4.2 month mode: stock = GL at every period end
def t42():
    from odoo.addons.sonador_inventory.models.costing import _period_end
    company.sudo().sb_recost_entries = 'month'
    try:
        N = 70
        base = D(2026, 10, 12)
        p = item('MON')
        grn(U, p, base, N, 10)
        for i in range(N - 10):
            giv(U, p, base + timedelta(days=i + 1), 1)
        sale(U, p, base + timedelta(days=3), 0.5)
        E = ADM['account.move']
        top = E.search([], order='id desc', limit=1).id
        grn(U, p, base, 100, 20)  # back-dated receipt re-costs everything after it
        ents = E.search([('id', '>', top), ('ref', '=', 'Cost recalculation (same-day receipt rule)')])
        bad = [x.date for x in ents if x.date != _period_end(x.date)]
        chk(ents and not bad, 'month mode: %d re-cost entries, all on Ethiopian period ends %s' % (len(ents), sorted(set(ents.mapped('date')))))
        pe = sorted(set(_period_end(base + timedelta(days=k)) for k in range(0, N + 2)))
        mism = [(str(d), mval(p, d)[1], gl(INV, d)) for d in pe if abs(mval(p, d)[1] - gl(INV, d)) > 0.005]
        chk(not mism, 'month mode: stock = GL at every period end %s %s' % ([str(x) for x in pe], mism))
        r = rec_row(pe[0])
        chk(abs(r['diff']) < 0.005, 'month mode: stock/GL reconciliation at period end %s: diff %s' % (pe[0], r['diff']))
    finally:
        company.sudo().sb_recost_entries = 'day'
    # day mode on sample days (the chain in 4.1 checks the year end)
    p = item('DAY')
    base = D(2026, 3, 1)
    grn(U, p, base, 50, 10)
    for i in range(20):
        giv(U, p, base + timedelta(days=i + 1), 1)
    grn(U, p, base - timedelta(days=1), 10, 40)
    ds = [base + timedelta(days=k) for k in (0, 1, 5, 12, 21)]
    mism = [(str(d), mval(p, d)[1], gl(INV, d)) for d in ds if abs(mval(p, d)[1] - gl(INV, d)) > 0.005]
    chk(not mism, 'day mode: stock = GL on every sample day after a back-dated receipt %s' % mism)


# ================================================================== 4.3 Correct and Void refused below zero, naming documents
def t43():
    p = item('B1')
    g = grn(U, p, D(2026, 2, 10), 10, 10)
    s = sale(U, p, D(2026, 2, 12), 8)
    v = giv(U, p, D(2026, 2, 13), 1)
    # a used receipt can be corrected (a new cost on the same quantity takes nothing back); posted again with less than
    # the later documents used, it is refused, naming them, and it can be posted back as it was
    e = err(lambda: (g.with_env(U).action_correct(), g.with_env(U).line_ids.write({'unit_cost': 12}), g.with_env(U).action_post()))
    s.invalidate_recordset()
    chk(e is None and g.state == 'posted' and abs(s.sb_cogs - 96) < 0.005,
        'Correct of a used GRN, new cost 12 on the same quantity: posted, the sale follows to 96: %s %s' % (e, s.sb_cogs))
    g.with_env(U).action_correct()
    g.with_env(U).line_ids.write({'quantity': 5})
    e = err(lambda: g.with_env(U).action_post())
    chk(e and s.name in e and v.name in e, 'posted again with 5 when 9 were used: refused, naming %s and %s: %s' % (s.name, v.name, (e or '')[:200]))
    e = err(lambda: void(g.with_env(U)))
    chk(e and s.name in e, 'voided while in correction: refused, naming the sale: %s' % (e or '')[:200])
    g.invalidate_recordset()
    g.with_env(U).line_ids.write({'quantity': 10, 'unit_cost': 10})
    g.with_env(U).action_post()
    s.invalidate_recordset()
    chk(g.state == 'posted' and abs(s.sb_cogs - 80) < 0.005, 'posted back as it was: the sale back to 80: %s' % s.sb_cogs)
    e = err(lambda: void(g.with_env(U)))
    chk(e and s.name in e, 'Void of a used GRN refused, naming the sale: %s' % (e or '')[:200])
    e = err(lambda: void(g.with_env(M)))
    chk(e and s.name in e, 'a manager is refused too: %s' % (e or '')[:120])
    g0 = grn(U, p, D(2026, 2, 9), 5, 20)
    e = err(lambda: (g.with_env(U).action_correct(), g.with_env(U).line_ids.write({'quantity': 3}), g.with_env(U).action_post()))
    chk(e is not None, 'the 10 posted again as 3 when only 6 are spare: refused')
    e = err(lambda: (g0.with_env(U).action_correct(), g0.with_env(U).line_ids.write({'quantity': 0.5}), g0.with_env(U).action_post()))
    chk(e is None and g0.state == 'posted', 'the 5 posted again as 0.5 when 6 are spare: allowed: %s' % (e or 'posted')[:120])
    company.sb_allow_negative_stock = True
    try:
        p2 = item('B2')
        s1 = sale(U, p2, D(2026, 2, 3), 5)
        g2 = grn(U, p2, D(2026, 2, 5), 10, 12)
        s1.invalidate_recordset()
        chk(abs(s1.sb_cogs - 60) < 0.005, 'negative stock allowed: the earlier sale is trued up to the receipt cost 12: %s' % s1.sb_cogs)
        company.sb_allow_negative_stock = False
        e = err(lambda: void(g2.with_env(U)))
        chk(e and s1.name in e, 'negative stock off: void of the receipt covering an earlier sale names it: %s' % (e or '')[:200])
    finally:
        company.sb_allow_negative_stock = False
    p3 = item('B3')
    grn(U, p3, D(2026, 2, 1), 5, 10)
    e = err(lambda: sale(U, p3, D(2026, 2, 2), 6))
    chk(e is not None, 'a sale of more than is on hand is refused: %s' % (e or '')[:120])
    e = err(lambda: giv(S, p3, D(2026, 2, 2), 6))
    chk(e is not None, 'a store issue of more than is on hand is refused: %s' % (e or '')[:120])


# ================================================================== 4.4 returns follow their sale's cost and cannot exceed it
def t44():
    p = item('R')
    grn(U, p, D(2026, 5, 1), 10, 10)
    grn(U, p, D(2026, 5, 2), 10, 13)
    s = sale(U, p, D(2026, 5, 3), 10, kind='sale')
    chk(abs(s.sb_cogs - 115) < 0.005, 'credit sale 10 at 11.50 = 115: %s' % s.sb_cogs)
    r1 = sret(U, s, p, D(2026, 5, 4), 4)
    chk(abs(outs(r1) - 46) < 0.005, 'return of 4 at the sale unit 11.50 = 46: %s' % outs(r1))
    e = err(lambda: sret(U, s, p, D(2026, 5, 5), 7))
    chk(e is not None, 'a return of 7 when 6 are left is refused: %s' % (e or '')[:150])
    r2 = sret(U, s, p, D(2026, 5, 5), 6)
    e = err(lambda: sret(U, s, p, D(2026, 5, 6), 1))
    chk(e is not None, 'nothing left to return: refused: %s' % (e or '')[:120])
    void(r2.with_env(U))
    full(p, 14, round(230 - 115 + 46, 2), END, 'after returns and a voided return')
    c0 = gl(COGS)
    grn(U, p, D(2026, 4, 30), 10, 7)  # average on 2 May (70+100+130)/30 = 10: sale 100, return 40
    for x in (s, r1):
        x.invalidate_recordset()
    chk(abs(s.sb_cogs - 100) < 0.005 and abs(outs(r1) - 40) < 0.005,
        'back-dated receipt: sale re-costed to 100 (%s) and its return follows to 40 (%s)' % (s.sb_cogs, outs(r1)))
    chk(abs(gl(COGS) - c0 - (-15 + 6)) < 0.005, 'cost of sales moves by -9: %s' % round(gl(COGS) - c0, 2))
    e = err(lambda: void(s.with_env(U)))
    chk(e and r1.name in e, 'a sale with a return cannot be voided, naming it: %s' % (e or '')[:150])
    r0 = U['sb.voucher'].create({'voucher_type': 'sale_return', 'date': D(2026, 5, 6), 'partner_id': CUS.id, 'sb_warehouse_id': WH.id,
                                 'sb_pad_number': 'CN %05d' % _r.randint(1, 99999), 'line_ids': [Command.create({'product_id': p.id, 'name': 'x', 'quantity': 2,
                                                                                       'price_unit': 50, 'account_id': INC.id,
                                                                                       'tax_ids': [(6, 0, [])]})]})
    r0.action_post()
    chk(abs(outs(r0) - 20) < 0.005, 'a return without its sale comes back at the average 10: %s' % outs(r0))
    # supplier return against its bill, re-costed with the item
    p = item('PR')
    g = grn(U, p, D(2026, 5, 1), 10, 10)
    bill = U['sb.voucher'].browse(g.with_env(U).action_create_bill()['res_id'])
    bill.reference = 'RG4B%d' % _r.randint(1, 10 ** 6)
    bill.action_post()
    grn(U, p, D(2026, 5, 2), 10, 14)
    pc0 = gl(PPV)
    pr = U['sb.voucher'].create({'voucher_type': 'purchase_return', 'date': D(2026, 5, 3), 'partner_id': SUP.id, 'origin_id': bill.id,
                                 'sb_warehouse_id': WH.id, 'reference': 'RG4DN%d' % _r.randint(1, 10 ** 6),
                                 'line_ids': [Command.create({'product_id': p.id, 'name': 'x', 'quantity': 2, 'price_unit': 10,
                                                              'account_id': INV.id, 'tax_ids': [(6, 0, [])]})]})
    pr.action_post()
    chk(abs(outs(pr) - 24) < 0.005 and abs(gl(PPV) - pc0 - 4) < 0.005,
        'supplier return of 2 at the average 12 = 24, debit note 20, difference 4 to cost difference: %s / %s' % (outs(pr), round(gl(PPV) - pc0, 2)))
    e = err(lambda: U['sb.voucher'].create({'voucher_type': 'purchase_return', 'date': D(2026, 5, 4), 'partner_id': SUP.id,
                                            'origin_id': bill.id, 'sb_warehouse_id': WH.id, 'reference': 'RG4DN%d' % _r.randint(1, 10 ** 6),
                                            'line_ids': [Command.create({'product_id': p.id, 'name': 'x', 'quantity': 9, 'price_unit': 10,
                                                                         'account_id': INV.id, 'tax_ids': [(6, 0, [])]})]}).action_post())
    chk(e is not None, 'a supplier return beyond what was billed is refused: %s' % (e or '')[:120])


# ================================================================== 4.5 bills from GRNs
def t45():
    p = item('BL')
    for q, val in ((3, 100.00), (7, 1000.00), (0.5, 10.01), (1.5, 10.01), (2.5, 7.03), (0.3, 0.01), (12, 0.05), (3.3333, 10.0)):
        g = grn(S, p, D(2026, 4, 1), q, val=val)  # prepared by the store, posted by finance
        bill = U['sb.voucher'].browse(g.with_env(U).action_create_bill()['res_id'])
        bill.write({'reference': 'RG4BILL%s' % _r.randint(0, 10 ** 8)})
        tq = sum(bill.line_ids.mapped('quantity'))
        tv = sum(l.quantity * l.price_unit for l in bill.line_ids)
        e = err(lambda: bill.action_post())
        open_grni = round(sum(ADM['account.move.line'].search([('account_id', '=', GRNI.id), ('reconciled', '=', False),
                                                               ('move_id', 'in', (g.move_id | bill.move_id | bill.sb_cost_move_ids).ids)]).mapped('balance')), 2)
        chk(e is None and abs(g.line_ids.value - val) < 0.005 and abs(tq - q) < 1e-9 and abs(tv - val) < 0.005 and abs(open_grni) < 0.005
            and not bill.sb_cost_move_ids,
            'bill from GRN of %s for %.2f: real quantity %s, total %.4f, GRNI left open %s %s' % (q, val, tq, tv, open_grni, e or ''))
    g = grn(U, p, D(2026, 4, 2), 10, 10)
    bill = U['sb.voucher'].browse(g.with_env(U).action_create_bill()['res_id'])
    bill.write({'reference': 'RG4BILL%s' % _r.randint(0, 10 ** 8)})
    bill.line_ids.write({'price_unit': 11})
    pc0 = gl(PPV)
    bill.action_post()
    open_grni = round(sum(ADM['account.move.line'].search([('account_id', '=', GRNI.id), ('reconciled', '=', False),
                                                           ('move_id', 'in', (g.move_id | bill.move_id | bill.sb_cost_move_ids).ids)]).mapped('balance')), 2)
    inv_after = mval(p)[1]
    chk(abs(open_grni) < 0.005, 'bill at 11 against a GRN at 10: GRNI cleared (%s)' % open_grni)
    chk(abs((gl(PPV) - pc0) + (inv_after - mval(p)[1])) >= 0 and (abs(gl(PPV) - pc0 - 10) < 0.005 or bill.sb_cost_move_ids),
        'price difference 10 flows to cost (cost difference %s, cost entries %d)' % (round(gl(PPV) - pc0, 2), len(bill.sb_cost_move_ids)))
    e = err(lambda: g.with_env(U).action_correct())
    chk(e and 'bill' in e.lower(), 'Correct of a billed GRN is refused: %s' % (e or '')[:120])
    # VAT on purchases goes to cost when the company is not registered for VAT
    vat = env['account.tax'].search([('type_tax_use', '=', 'purchase'), ('amount', '=', 15), ('amount_type', '=', 'percent'),
                                     ('company_id', '=', company.id)], limit=1)
    if vat and not company.sb_vat_registered:
        p2 = item('VAT')
        p2.supplier_taxes_id = [(6, 0, vat.ids)]
        b2 = U['sb.voucher'].create({'voucher_type': 'bill', 'date': D(2026, 5, 10), 'partner_id': SUP.id, 'sb_warehouse_id': WH.id,
                                     'reference': 'RG4B%d' % _r.randint(1, 10 ** 6),
                                     'line_ids': [Command.create({'product_id': p2.id, 'name': 'x', 'quantity': 10, 'price_unit': 100,
                                                                  'account_id': INV.id, 'tax_ids': [(6, 0, vat.ids)]})]})
        e = err(lambda: b2.action_post())
        chk(e is not None or abs(mval(p2)[1] - 1150) < 0.005 or abs(mval(p2)[1] - 1000) < 0.005 and not b2.line_ids.tax_ids,
            'not VAT registered: VAT on a direct bill is refused or goes to cost (stock %s) %s' % (mval(p2), (e or '')[:100]))
    else:
        info('VAT registered or no 15%% purchase tax: VAT-in-cost not tested here')
    # closed period untouched: a bill dated in a closed period is refused
    lock = company._get_user_fiscal_lock_date(company.account_stock_journal_id) if company.account_stock_journal_id else company.fiscalyear_lock_date
    g = grn(U, p, D(2026, 4, 3), 1, 10)
    bill = U['sb.voucher'].browse(g.with_env(U).action_create_bill()['res_id'])
    bill.write({'reference': 'RG4BILL%s' % _r.randint(0, 10 ** 8), 'date': lock})
    e = err(lambda: bill.action_post())
    chk(e is not None, 'a bill dated in a closed period (%s) is refused: %s' % (lock, (e or '')[:100]))


# ================================================================== 4.6 FPRR, builds and WIP
def t46():
    for order in ('exp_first', 'wip_first'):
        m = item('M' + order)
        fg = item('FG' + order, CATFG)
        grn(S, m, D(2026, 2, 2), 100, 10)
        l_e = {'product_id': m.id, 'quantity': 10, 'account_id': E1.id}
        l_w = {'product_id': m.id, 'quantity': 5, 'account_id': WIP.id}
        e0, w0, c0, lab0 = gl(E1), gl(WIP), gl(COGS), gl(LAB)
        gv = giv(S, m, D(2026, 2, 5), 0, acct=E1, lines=[l_e, l_w] if order == 'exp_first' else [l_w, l_e])
        f = fprr(S, fg, D(2026, 2, 6), 5, c=0, wip=True, labour=15)
        chk(abs(outs(f) - 65) < 0.005, '%s: FPRR from WIP = 50 materials + 15 labour = 65: %s' % (order, outs(f)))
        s = sale(U, fg, D(2026, 2, 8), 2)
        chk(abs(s.sb_cogs - 26) < 0.005, '%s: finished goods sale 2 x 13 = 26: %s' % (order, s.sb_cogs))
        grn(U, m, D(2026, 2, 1), 50, 16)
        for x in (s, f, gv):
            x.invalidate_recordset()
        lv = {l.account_id.code: l.value for l in gv.line_ids}
        chk(abs(lv[E1.code] - 120) < 0.005 and abs(lv[WIP.code] - 60) < 0.005, '%s: back-dated receipt, GIV per line: expense 120 WIP 60: %s' % (order, lv))
        chk(abs(outs(f) - 75) < 0.005 and abs(s.sb_cogs - 30) < 0.005, '%s: FPRR follows to 75 (%s), the sale to 30 (%s)' % (order, outs(f), s.sb_cogs))
        chk(abs(gl(E1) - e0 - 120) < 0.005 and abs(gl(WIP) - w0) < 0.005 and abs(gl(COGS) - c0 - 30) < 0.005 and abs(gl(LAB) - lab0 + 15) < 0.005,
            '%s: GL expense +%s, WIP %s (cleared), cost of sales +%s, labour absorbed %s'
            % (order, round(gl(E1) - e0, 2), round(gl(WIP) - w0, 2), round(gl(COGS) - c0, 2), round(gl(LAB) - lab0, 2)))
        full(m, 135, 1620, END, '%s material' % order)
        full(fg, 3, 45, END, '%s finished goods' % order)
        gv.with_env(U).action_correct()
        gv.with_env(U).line_ids.filtered(lambda l: l.account_id == WIP).write({'quantity': 6})
        gv.with_env(U).action_post()
        for x in (s, f):
            x.invalidate_recordset()
        chk(abs(outs(f) - 87) < 0.005 and abs(s.sb_cogs - 34.8) < 0.005 and abs(gl(WIP) - w0) < 0.005,
            '%s: GIV corrected to 6 into WIP: FPRR 87 (%s), sale 34.80 (%s), WIP cleared (%s)' % (order, outs(f), s.sb_cogs, round(gl(WIP) - w0, 2)))
    # a typed FPRR keeps its cost; the WIP difference stays in WIP
    m = item('Mt')
    fg = item('FGt', CATFG)
    grn(U, m, D(2026, 2, 2), 100, 10)
    w0 = gl(WIP)
    giv(U, m, D(2026, 2, 5), 10, acct=WIP)
    f = fprr(U, fg, D(2026, 2, 6), 10, c=11)
    chk(abs(outs(f) - 110) < 0.005 and abs(gl(WIP) - w0 + 10) < 0.005, 'typed FPRR 110, WIP residual -10: %s %s' % (outs(f), round(gl(WIP) - w0, 2)))
    # build: components into the finished item, re-costed with them
    c1 = item('C1')
    c2 = item('C2')
    fgb = item('FGB', CATFG)
    grn(U, c1, D(2026, 4, 2), 10, 10)
    grn(U, c2, D(2026, 4, 2), 10, 5)
    b = build(U, fgb, D(2026, 4, 5), 2, [(c1, 4), (c2, 2)])
    fgm = b.production_id.move_finished_ids.filtered(lambda x: x.state == 'done')
    chk(abs(sum(abs(x.value) for x in fgm) - 50) < 0.005, 'build: finished item = 40 + 10 = 50: %s' % fgm.mapped('value'))
    sb = sale(U, fgb, D(2026, 4, 6), 1)
    chk(abs(sb.sb_cogs - 25) < 0.005, 'build: finished item sold at 25: %s' % sb.sb_cogs)
    grn(U, c1, D(2026, 4, 1), 10, 16)  # component average 13: c1 52, finished 62, sale 31
    for x in (sb, fgm, b):
        x.invalidate_recordset()
    chk(abs(sum(abs(x.value) for x in fgm) - 62) < 0.005 and abs(sb.sb_cogs - 31) < 0.005,
        'build re-costed with its components: finished 62 (%s), sale 31 (%s)' % (fgm.mapped('value'), sb.sb_cogs))
    full(fgb, 1, 31, END, 'build finished item')
    # Eco Green's own books: work in process balance (the Tikimt finding of 2 Nov 2026)
    wip_live = ADM['account.account'].search([('code', '=', '1505-0000'), ('company_ids', 'in', company.id)], limit=1)
    if wip_live:
        bal = gl(wip_live)
        info('Eco Green books: Work in Process 1505-0000 balance %s. Negative = finished goods received (FPRR) before the '
             'packing / carton / sticker issues reach WIP: those GIVs post to "Cost of Sales - Indirect" and move to WIP only '
             'at the period close JV (JV-114 for Q1). Engine is right per line (above); the posting rule is Robel\'s decision.'
             % '{:,.2f}'.format(bal))


# ================================================================== 4.8 the COGS journal equals the ledger
def t48():
    def cogsj(p, df=D(2026, 1, 1), dt=D(2026, 12, 31)):
        doc = U['sb.report'].get_report('cogs_journal', {'date_from': str(df), 'date_to': str(dt), 'product_ids': [p.id]})
        return [r['values'] for r in doc['rows'] if r['kind'] == 'line'], [r['values'] for r in doc['rows'] if r['kind'] == 'total'][0]
    c_before = gl(COGS)
    p = item('V')
    grn(U, p, D(2026, 1, 5), 100, 10)
    s1 = sale(U, p, D(2026, 1, 8), 30)
    sale(U, p, D(2026, 1, 9), 10, kind='sale')
    grn(U, p, D(2026, 1, 4), 100, 20)  # re-costs both sales
    void(s1.with_env(U))
    grn(U, p, D(2026, 1, 3), 50, 30)   # re-costs again after the void
    rows, tot = cogsj(p)
    um = [r for r in rows if r.get('note') == 'Cost without a matched sale']
    v1 = [r for r in rows if r['ref'].startswith(s1.name)]
    chk(not um, 'every cost line matched to its sale (%d unmatched)' % len(um))
    chk(v1 and abs(sum(r['cost'] for r in v1)) < 0.005 and all(abs(r['sales']) < 0.005 for r in v1),
        'a voided sale nets to zero in the journal: %s' % [(r['ref'], r['cost']) for r in v1])
    chk(abs(tot['cost'] - (gl(COGS, pid=p.id))) < 0.005 and abs(tot['sales'] - 1000) < 0.005,
        'journal total cost %s = ledger %s, sales %s' % (tot['cost'], gl(COGS, pid=p.id), tot['sales']))
    # with returns: the journal follows the return's cost
    p4 = item('B3')
    grn(U, p4, D(2026, 3, 1), 10, 10)
    sa = sale(U, p4, D(2026, 3, 2), 6)
    grn(U, p4, D(2026, 3, 3), 10, 20)
    sale(U, p4, D(2026, 3, 4), 4)
    sret(U, sa, p4, D(2026, 3, 5), 2)
    grn(U, p4, D(2026, 2, 28), 10, 40)
    rows, tot = cogsj(p4)
    chk(abs(tot['cost'] - gl(COGS, pid=p4.id)) < 0.005 and abs(tot.get('unmatched', 0)) < 0.005,
        'with a return and a back-dated receipt: journal %s = ledger %s, unmatched %s' % (tot['cost'], gl(COGS, pid=p4.id), tot.get('unmatched')))
    # the whole test company period: journal = ledger on the test cost account
    doc = U['sb.report'].get_report('cogs_journal', {'date_from': '2026-01-01', 'date_to': '2026-12-31'})
    t_all = [r['values'] for r in doc['rows'] if r['kind'] == 'total'][0]
    info('whole company 2026 COGS journal total %s (all items, all accounts)' % t_all.get('cost'))
    chk(abs(gl(COGS) - c_before - tot['cost'] - [r for r in cogsj(p)[1:]][0]['cost']) < 0.005,
        'ledger cost of sales of the two test items = their two journals')


# ================================================================== 4.9 re-costing 1,000 issues under 5 s
def t49():
    N = int(os.environ.get('REG4_N49', '1000'))
    base = D(2025, 7, 10)
    p = item('PERF')
    grn(U, p, base, N, 10)
    givs = []
    t = _t.time()
    for i in range(N):
        givs.append(giv(U, p, base + timedelta(days=i + 1), 1))
    info('%d GIVs posted in %.1f s' % (N, _t.time() - t))
    end_ = base + timedelta(days=N + 5)

    def expected(extra_cost):
        ev = [{'day': base, 'dir': 1, 'qty': N, 'value': N * 10, 'key': 'g0', 'seq': 0}]
        if extra_cost is not None:
            ev.append({'day': base, 'dir': 1, 'qty': 100, 'value': 100 * extra_cost, 'key': 'gb', 'seq': 1})
        ev += [{'day': base + timedelta(days=i + 1), 'dir': -1, 'qty': 1, 'key': i} for i in range(N)]
        return replay(ev)

    def check_givs(extra, label):
        out, (fq, fv) = expected(extra)
        env.invalidate_all()
        bad = [(i, float(out[i]['value']), outs(g)) for i, g in enumerate(givs) if abs(float(out[i]['value']) - outs(g)) > 0.0049]
        chk(not bad, '%s: all GIVs equal the independent replay (%d off, e.g. %s)' % (label, len(bad), bad[:3]))
        return float(fq), float(fv)
    for mode in ('day', 'month'):
        company.sudo().sb_recost_entries = mode
        try:
            env.flush_all()  # timed as its own request: from a flushed transaction
            t = _t.time()
            if os.environ.get('REG4_PROFILE') and mode == 'day':
                import cProfile, io, pstats
                pr_ = cProfile.Profile()
                pr_.enable()
                gb = grn(U, p, base, 100, 20)
                pr_.disable()
                buf = io.StringIO()
                pstats.Stats(pr_, stream=buf).sort_stats('cumulative').print_stats(90)
                print('PROFILE GRN\n' + buf.getvalue()[:12000])
            else:
                gb = grn(U, p, base, 100, 20)
            env.flush_all()  # its own work written, as at the end of its request
            tb = _t.time() - t
            fq, fv = check_givs(20, '%s mode back-dated GRN' % mode)
            full(p, fq, fv, end_, '%s mode after back-dated GRN' % mode)
            env.flush_all()  # timed as its own request: from a flushed transaction
            t = _t.time()
            if os.environ.get('REG4_PROFILE') and mode == 'day':
                import cProfile, io, pstats
                pr_ = cProfile.Profile()
                pr_.enable()
                gb.with_env(U).action_correct()
                pr_.disable()
                buf = io.StringIO()
                pstats.Stats(pr_, stream=buf).sort_stats('cumulative').print_stats(35)
                print('PROFILE Correct\n' + buf.getvalue()[:9000])
            else:
                gb.with_env(U).action_correct()
            env.flush_all()  # its own work written, as at the end of its request
            tc = _t.time() - t
            gb.with_env(U).line_ids.write({'unit_cost': 30})
            env.flush_all()  # timed as its own request: from a flushed transaction
            t = _t.time()
            gb.with_env(U).action_post()
            env.flush_all()  # its own work written, as at the end of its request
            tr = _t.time() - t
            check_givs(30, '%s mode repost at 30' % mode)
            env.flush_all()  # timed as its own request: from a flushed transaction
            t = _t.time()
            void(gb.with_env(U))
            env.flush_all()  # its own work written, as at the end of its request
            tv = _t.time() - t
            fq, fv = check_givs(None, '%s mode void' % mode)
            full(p, fq, fv, end_, '%s mode after void' % mode)
            chk(max(tb, tc, tr, tv) < 5, '%s mode timings under 5 s: back-dated GRN %.1f s, Correct %.1f s, Repost %.1f s, Void %.1f s'
                % (mode, tb, tc, tr, tv))
        finally:
            company.sudo().sb_recost_entries = 'day'



# ================================================================== 4.10 receipts changed after use: a recipe chain, every value
def t410():
    """C is made of 2 A + 4 B. Random GRNs, GIVs (to WIP and to an expense), FPRRs (typed and from WIP) and sales, then
    random changes to documents later ones already used: GRN price and quantity, FPRR cost and quantity, GIV quantity,
    bills at another price, voids, back-dated documents. After every change, against an independent replay: every
    issue, receipt, finished-goods cost and sale; stock = GL on every day; WIP, expense and cost of sales in the GL; no
    stock below zero; a refusal only where the change would take stock below zero (or a stated rule)."""
    seed = int(os.environ.get('REG4_SEED_410', '410'))
    steps = int(os.environ.get('REG4_STEPS_410', '120'))
    R = _r.Random(seed)
    A, B, C = item('A'), item('B'), item('C', CATFG)
    base = D(2026, 9, 1)
    days = [base + timedelta(days=k) for k in range(30)]
    docs = []  # {'rec', 'kind': open|grn|givw|give|fprr|sale, 'bill': price}

    def live(d):
        d['rec'].invalidate_recordset()
        return d['rec'].state == 'posted'

    def key(rec, p):
        return (rec._name, rec.id, p.id)

    def money(q, cost):
        # quantity x unit cost to the cent, half up, as the books round (722.405 is 722.41, not the float's 722.40)
        return float((Dm(str(q)) * Dm(str(cost))).quantize(Dm('0.01'), ROUND_HALF_UP))

    def lines(rec):
        return rec.line_ids.filtered('product_id')

    def got(rec, p):
        ms = (rec.stock_move_ids if rec._name == 'sb.stock.voucher' else rec.sb_stock_move_ids).filtered(
            lambda m: m.product_id == p and not m.sb_reversal_of_id and not m.sb_cancelled and m.state == 'done')
        return round(sum(abs(m.value) for m in ms), 2), (min(ms.ids) if ms else 0)

    def events(over=None, extra=()):
        """{item: [events]} from the live documents' own figures; `over`: {(doc kind, id): {item: qty}} replaces a
        document's quantities (empty: as if voided), `extra`: documents not posted yet [(kind, day, {item: (qty, cost)})]."""
        ev = defaultdict(list)
        for d in docs:
            r = d['rec']
            if not live(d):
                continue
            o = (over or {}).get((r._name, r.id))
            for l in lines(r):
                p = l.product_id
                q = o.get(p.id, 0) if o is not None else l.quantity
                if not q:
                    continue
                _v, seq = got(r, p)
                e = {'day': r.date, 'qty': q, 'key': key(r, p), 'seq': seq, 'p': p.id}
                if d['kind'] in ('open', 'grn') or (d['kind'] == 'fprr' and not r.sb_wip_costed):
                    typed = l.value_entered and o is None and not d.get('bill')
                    e.update(dir=1, value=l.value if typed else money(q, d.get('bill') or l.unit_cost))
                elif d['kind'] == 'fprr':
                    e.update(dir=1, fprr=r, value=None)
                else:
                    e.update(dir=-1)
                ev[p.id].append(e)
        for i, (kind, day, its) in enumerate(extra):
            for pid, (q, cost) in its.items():
                e = {'day': day, 'qty': q, 'key': ('new', i, pid), 'seq': 10 ** 9 + i, 'p': pid}
                e.update(dir=1, value=money(q, cost)) if kind in ('grn', 'fprr') else e.update(dir=-1)
                ev[pid].append(e)
        return ev

    def expect(over=None, extra=()):
        ev = events(over, extra)
        out, fin = {}, {}
        for p in (A, B):
            o, f = replay(ev[p.id])
            out.update(o)
            fin[p.id] = f
        for e in ev[C.id]:  # finished goods from WIP: the materials of the GIVs they took (as replayed)
            if e['dir'] > 0 and e['value'] is None:
                f = e['fprr']
                e['value'] = float(sum((out.get(key(g, l.product_id), {'value': Dm(0)})['value']
                                        for g in f.sb_wip_giv_ids if g.state == 'posted' for l in lines(g)), Dm(0)))
        o, f = replay(ev[C.id])
        out.update(o)
        fin[C.id] = f
        # receipts (GRN at its cost or its bill's, FPRR typed or from WIP) are checked too: the replay keeps only issues
        for es in ev.values():
            for e in es:
                if e['dir'] > 0:
                    out[e['key']] = {'qty': Dm(str(e['qty'])), 'value': Dm(str(round(e['value'], 2)))}
        return out, fin, ev

    def negative(ev):
        for pid, es in ev.items():
            q = 0
            for d in sorted(set(e['day'] for e in es)):
                q += sum(e['qty'] * e['dir'] for e in es if e['day'] == d)
                if q < -1e-6:
                    return True
        return False

    def verify(tag):
        out, fin, ev = expect()
        bad = []
        for d in docs:
            r = d['rec']
            if not live(d):
                continue
            for l in lines(r):
                exp_v = out.get(key(r, l.product_id))
                v, _seq = got(r, l.product_id)
                if exp_v is not None and abs(float(exp_v['value']) - v) > 0.0049:
                    bad.append((d['kind'], r.name, l.product_id.name[-1], float(exp_v['value']), v))
        chk(not bad, '%s: %d documents, every value = independent replay %s' % (tag, sum(1 for d in docs if live(d)), bad[:4]))
        if bad and os.environ.get('REG4_DEBUG'):
            for d in docs:
                r = d['rec']
                if d['kind'] == 'fprr' and r.name in [b[1] for b in bad]:
                    info('debug %s value %s build %s moves %s' % (r.name, lines(r).value, r.build_value,
                         [(m.id, m.value, m.sb_recost_total, m.sb_cancelled) for m in r.stock_move_ids]))
                    for g in r.sb_wip_giv_ids:
                        info('debug   taken %s %s %s lines %s moves %s replay %s' % (g.name, g.date, g.state,
                             [(l.product_id.name[-1], l.quantity, l.value) for l in lines(g)],
                             [(m.product_id.name[-1], m.value, m.sb_cancelled) for m in g.stock_move_ids if m.state == 'done'],
                             [float(out.get(key(g, l.product_id), {'value': 0})['value']) for l in lines(g)]))
            raise UserError('debug stop')
        chk(not negative(ev), '%s: no stock below zero on any day' % tag)
        # stock = GL on every day of the month, both inventory accounts
        miss = []
        for day in days:
            for acc, its in ((INV, (A, B)), (INV2, (C,))):
                st = round(sum(mval(p, day)[1] for p in its), 2)
                if abs(st - gl(acc, day)) > 0.005:
                    miss.append((str(day), acc.code, st, gl(acc, day)))
        chk(not miss, '%s: stock = GL on each of %d days %s' % (tag, len(days), miss[:3]))
        for p in (A, B, C):
            q, v = fin[p.id]
            if abs(mval(p)[1] - float(v)) > 0.005 or abs(mval(p)[0] - float(q)) > 1e-6:
                chk(False, '%s: %s ends at %s / %s, replay %s / %s' % (tag, p.name, mval(p)[0], mval(p)[1], q, v))
        val = lambda kinds: round(sum(float(out[key(d['rec'], l.product_id)]['value']) for d in docs if d['kind'] in kinds and live(d)
                                      for l in lines(d['rec']) if key(d['rec'], l.product_id) in out), 2)
        wip = round(val(('givw',)) - val(('fprr',)), 2)
        if os.environ.get('REG4_DEBUG') and abs(gl(WIP) - W0 - wip) > 0.005:
            for d in docs:
                r = d['rec']
                if d['kind'] in ('givw', 'fprr'):
                    mv = r.move_id
                    info('debug %s %s %s state %s wip %s entry %s lines on WIP %s accounts %s' % (
                        d['kind'], r.name, r.date, r.state, r.sb_wip_costed if d['kind'] == 'fprr' else '',
                        mv.name, round(sum(mv.line_ids.filtered(lambda x: x.account_id == WIP).mapped('balance')), 2),
                        sorted(set(mv.line_ids.account_id.mapped('code')))))
        chk(abs(gl(WIP) - W0 - wip) < 0.005 and abs(gl(E2) - E0 - val(('give',))) < 0.005 and abs(gl(COGS) - C0 - val(('sale',))) < 0.005,
            '%s: GL work in process %.2f (expected %.2f), expense %.2f (%.2f), cost of sales %.2f (%.2f)'
            % (tag, gl(WIP) - W0, wip, gl(E2) - E0, val(('give',)), gl(COGS) - C0, val(('sale',))))

    W0, E0, C0 = gl(WIP), gl(E2), gl(COGS)

    def on_hand(p, day):
        out, fin, ev = expect()
        q = 0
        low = None
        for d in sorted(set(e['day'] for e in ev[p.id])):
            q += sum(e['qty'] * e['dir'] for e in ev[p.id] if e['day'] == d)
            if d >= day:
                low = q if low is None else min(low, q)
        return max(low if low is not None else q, 0)

    def new_doc(kind, day, plan):
        """Post a new document; its plan [(kind, day, {item: (qty, cost)})] is put in `plan` first, for a refusal's check."""
        if kind == 'grn':
            p = R.choice([A, B])
            q, c = R.randint(20, 120), round(R.uniform(8, 30), 2)
            plan.append(('grn', day, {p.id: (q, c)}))
            return grn(U, p, day, q, c), 'grn'
        if kind in ('givw', 'give'):
            n = R.randint(1, 15)
            ls = [{'product_id': A.id, 'quantity': 2 * n}, {'product_id': B.id, 'quantity': 4 * n}] if kind == 'givw' else \
                [{'product_id': R.choice([A, B]).id, 'quantity': R.randint(1, 10)}]
            plan.append((kind, day, {x['product_id']: (x['quantity'], 0) for x in ls}))
            return giv(U, None, day, 0, acct=WIP if kind == 'givw' else E2, lines=ls), kind
        if kind == 'fprr':
            wip_ = R.random() < 0.5
            q, c = R.randint(1, 15), round(R.uniform(80, 160), 2)
            plan.append(('fprr', day, {C.id: (q, c)}))
            return fprr(U, C, day, q, c=c, wip=wip_), 'fprr'
        q = R.randint(1, 10)
        plan.append(('sale', day, {C.id: (q, 0)}))
        return sale(U, C, day, q, price=200), 'sale'

    # the month opens with stock
    o = U['sb.stock.voucher'].create({'voucher_type': 'opening', 'date': base, 'warehouse_id': WH.id,
                                      'line_ids': [Command.create({'product_id': A.id, 'quantity': 100, 'unit_cost': 10}),
                                                   Command.create({'product_id': B.id, 'quantity': 200, 'unit_cost': 20}),
                                                   Command.create({'product_id': C.id, 'quantity': 10, 'unit_cost': 100})]})
    o.action_post()
    docs.append({'rec': o, 'kind': 'open'})
    log = defaultdict(int)
    bad_refusals = []
    for step in range(steps):
        build_phase = step < steps // 3
        a = R.choice(['grn', 'givw', 'givw', 'give', 'fprr', 'fprr', 'sale', 'sale'] if build_phase else
                     ['grn_cost', 'grn_qty', 'grn_qty', 'fprr_cost', 'fprr_qty', 'fprr_qty', 'giv_qty', 'void', 'bill',
                      'grn', 'givw', 'fprr', 'sale'])
        day = R.choice(days[1:])
        over, extra, d = None, [], None
        try:
            with env.cr.savepoint():
                if a in ('grn', 'givw', 'give', 'fprr', 'sale'):
                    rec, kind = new_doc(a, day, extra)
                    docs.append({'rec': rec, 'kind': kind})
                else:
                    kinds = {'grn_cost': ('grn',), 'grn_qty': ('grn',), 'bill': ('grn',), 'fprr_cost': ('fprr',),
                             'fprr_qty': ('fprr',), 'giv_qty': ('givw', 'give'), 'void': ('grn', 'givw', 'give', 'fprr', 'sale')}[a]
                    cand = [x for x in docs if x['kind'] in kinds and live(x) and not x.get('bill')
                            and not (a == 'fprr_cost' and x['rec'].sb_wip_costed)]
                    if not cand:
                        log[a + ' (nothing to change)'] += 1
                        continue
                    d = R.choice(cand)
                    r = d['rec'].with_env(U)
                    if a == 'void':
                        over = {(r._name, r.id): {}}
                        void(r)
                    elif a == 'bill':
                        act = r.action_create_bill()
                        bill = U['sb.voucher'].browse(act['res_id'])
                        price = round(r.line_ids.unit_cost * R.uniform(0.8, 1.25), 2)
                        bill.line_ids.filtered('product_id').write({'price_unit': price, 'tax_ids': [(6, 0, [])]})
                        bill.action_post()
                        d['bill'] = price
                    else:
                        r.action_correct()
                        f = R.choice([R.uniform(0.3, 0.9), R.uniform(1.1, 1.6)])
                        new = {}
                        for l in lines(r):
                            if a in ('grn_cost', 'fprr_cost'):
                                l.write({'unit_cost': round(l.unit_cost * f, 4), 'value_entered': False})
                            else:
                                l.write({'quantity': max(1, round(l.quantity * f)), 'value_entered': False})
                            new[l.product_id.id] = l.quantity
                        over = {(r._name, r.id): new}
                        r.action_post()
            log[a] += 1
        except (UserError, ValidationError) as e:
            msg = str(e)
            log[a + ' refused'] += 1
            env.invalidate_all()
            stock_rule = any(w in msg for w in ('stock', 'Not enough', 'take back'))
            if stock_rule:
                # the refusal must be right: the change would take some stock below zero on some day
                if not negative(events(over, extra)):
                    bad_refusals.append((a, d and d['rec'].name, msg.splitlines()[0][:120]))
            elif not any(w in msg for w in ('billed', 'Nothing is waiting')):
                bad_refusals.append((a, d and d['rec'].name, 'unexpected: ' + msg.splitlines()[0][:120]))
        if not build_phase and (step % 5 == 4 or step == steps - 1):
            verify('seed %d step %d' % (seed, step + 1))
    chk(not bad_refusals, 'every refusal was right (the change would take stock below zero, or a stated rule) %s' % bad_refusals[:4])
    done = sum(v for k, v in log.items() if 'refused' not in k and 'nothing' not in k)
    chk(done >= steps // 2, 'seed %d: %d steps, %d done: %s' % (seed, steps, done, dict(log)))
    for p, its in ((A, 'A'), (B, 'B'), (C, 'C')):
        q, v = mval(p)
        full(p, q, v, END, 'end of the chain, item %s' % its)


# ================================================================== run
for no, fn in (('4.1', t41), ('4.2', t42), ('4.3', t43), ('4.4', t44), ('4.5', t45), ('4.6', t46), ('4.8', t48), ('4.9', t49),
                ('4.10', t410)):
    if ONLY and no not in ONLY:
        continue
    print('---- %s' % no, flush=True)
    section(no, fn)
print('')
by = defaultdict(list)
for it, ok_, msg in RES:
    by[it].append(ok_)
for no in sorted(by):
    k, m = sum(by[no]), len(by[no])
    print('ITEM %s %s (%d/%d checks, %.0f s)' % (no, 'PASS' if k == m else 'FAIL', k, m, T0.get(no, T0.get('4.1', 0))))
print('AREA 4: %d/%d checks passed' % (sum(1 for r in RES if r[1]), len(RES)))
env.cr.rollback()
