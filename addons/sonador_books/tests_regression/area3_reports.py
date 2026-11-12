"""Acceptance checklist, area 3: Financial reports (items 3.1 - 3.9), as real users on a copy of the books.

Every check runs as a user with the real roles (clerk, accountant, manager, HR); the superuser only sets up users and
test documents. Prints one line per check, "PASS|FAIL <item> <what>", then one summary line per item. Everything is
rolled back at the end.
Run:  docker compose exec -T odoo sh -c 'odoo shell -d <copy> --no-http ...' < tests_regression/area3_reports.py"""
import io
import time
from collections import defaultdict
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.addons.sonador_books.models.ethiopian_calendar import fiscal_periods, fiscal_year_of, format_ethiopian

assert env.cr.dbname not in ('ecogreen_test', 'ecogreen_web'), 'never on a live database'

RESULTS = defaultdict(list)
T0 = {}
CUR_ITEM = [None]


def item(n):
    CUR_ITEM[0] = n
    T0[n] = time.time()


def check(cond, msg):
    n = CUR_ITEM[0]
    RESULTS[n].append(bool(cond))
    print('%s %s %s' % ('PASS' if cond else 'FAIL', n, msg))


def near(a, b, tol=0.015):
    return abs((a or 0.0) - (b or 0.0)) <= tol


def raises(fn, exc=(AccessError, UserError, ValidationError)):
    try:
        with env.cr.savepoint():
            fn()
    except exc:
        return True
    return False


S = fields.Date.to_string
company = env.company
PL_TYPES = "('income', 'income_other', 'expense', 'expense_depreciation', 'expense_direct_cost')"  # not carried forward
today = fields.Date.context_today(env['res.users'])
FY = fiscal_year_of(today)
PER = fiscal_periods(FY)
FY_FROM = PER[0][2]
TO = min(today, PER[-1][3])
PPER = fiscal_periods(FY - 1)
PFY_FROM, PFY_TO = PPER[0][2], PPER[-1][3]
cur_periods = [p for p in PER if p[2] <= TO]
LAST_FULL = [p for p in cur_periods if p[3] < today][-1]  # the last finished Ethiopian month

# ------------------------------------------------------------------ users with the real roles
U = env['res.users'].with_context(no_reset_password=True, mail_create_nolog=True)
users = {}
for role, groups in (('clerk', ['sonador_books.group_sb_clerk']), ('accountant', ['sonador_books.group_sb_accountant']),
                     ('manager', ['sonador_books.group_sb_manager']), ('hr', ['sonador_books.group_sb_hr'])):
    users[role] = U.create({'name': 'A3 %s' % role, 'login': 'a3_%s@regression.local' % role, 'company_id': company.id,
                            'company_ids': [Command.set([company.id])],
                            'group_ids': [Command.set([env.ref('base.group_user').id] + [env.ref(g).id for g in groups])]})
acc_u, mgr_u, clerk_u, hr_u = users['accountant'], users['manager'], users['clerk'], users['hr']
RA = env['sb.report'].with_user(acc_u)   # every report below is read by the accountant
RM = env['sb.report'].with_user(mgr_u)


def row(doc, rid):
    return next((r for r in doc['rows'] if r['id'] == rid), None)


def sql(q, args):
    env.flush_all()
    env.cr.execute(q, args)
    return env.cr.fetchall()


def acc_by_code(code):
    return env['account.account'].with_company(company).search([('code', '=', code), ('company_ids', 'in', company.id)], limit=1)


# ================================================================== 3.1 statements tie to each other and to SQL
item('3.1')
fy = {'date_from': S(FY_FROM), 'date_to': S(TO)}


def tie_out(label):
    """The statements of the current year tie to each other and to SQL; returns the key figures."""
    out = {}
    tb = RA.get_report('trial_balance', fy)
    tot = row(tb, 'total')['values']
    check(near(tot['debit'], tot['credit']) and near(tot['closing'], 0), '%s: trial balance debits = credits (%s) and closes at 0'
          % (label, '{:,.2f}'.format(tot['debit'])))
    mov = {a: (d or 0.0, c or 0.0) for a, d, c in sql(
        "select l.account_id, sum(l.debit), sum(l.credit) from account_move_line l where l.company_id = %s "
        "and l.parent_state = 'posted' and l.date between %s and %s group by 1", (company.id, FY_FROM, TO))}
    bad = []
    for r in tb['rows']:
        if r['kind'] == 'line' and str(r['id']).startswith('a') and str(r['id'])[1:].isdigit():
            d, c = mov.get(int(r['id'][1:]), (0.0, 0.0))
            if not (near(r['values']['debit'], d) and near(r['values']['credit'], c)):
                bad.append(r['values'].get('code'))
    check(not bad, '%s: every trial balance line equals SQL debits and credits%s' % (label, (' - differs: %s' % bad[:8]) if bad else ''))
    bs_close = {a: b or 0.0 for a, b in sql(
        "select l.account_id, sum(l.balance) from account_move_line l join account_account a on a.id = l.account_id "
        "where l.company_id = %s and l.parent_state = 'posted' and l.date <= %s and a.account_type not in " + PL_TYPES + " and a.account_type != 'off_balance' group by 1",
        (company.id, TO))}
    bad = [r['values'].get('code') for r in tb['rows'] if r['kind'] == 'line' and str(r['id'])[1:].isdigit()
           and int(r['id'][1:]) in bs_close and not near(r['values']['closing'], bs_close[int(r['id'][1:])])]
    check(not bad, '%s: every balance-sheet account closes at its SQL balance%s' % (label, (' - differs: %s' % bad[:8]) if bad else ''))
    pl = RA.get_report('profit_loss', fy)
    profit = row(pl, 'profit')['values']['p0']
    sql_profit = -(sql("select coalesce(sum(l.balance), 0) from account_move_line l join account_account a on a.id = l.account_id "
                       "join account_move m on m.id = l.move_id where l.company_id = %s and l.parent_state = 'posted' "
                       "and l.date between %s and %s and a.account_type in " + PL_TYPES + " and not coalesce(m.sb_closing, false)",
                       (company.id, FY_FROM, TO))[0][0] or 0.0)
    check(near(profit, sql_profit), '%s: profit for the year %s = SQL %s' % (label, '{:,.2f}'.format(profit), '{:,.2f}'.format(sql_profit)))
    bs = RA.get_report('balance_sheet', {'date_to': S(TO)})
    assets, el = row(bs, 'assets')['values']['p0'], row(bs, 'el_total')['values']['p0']
    check(near(assets, el) and not row(bs, 'diff'), '%s: balance sheet balances: assets %s = equity and liabilities %s'
          % (label, '{:,.2f}'.format(assets), '{:,.2f}'.format(el)))
    cye = row(bs, 'cye')
    check(cye is not None and near(cye['values']['p0'], profit), '%s: current year profit on the balance sheet = profit and loss'
          % label)
    sql_net = sql("select coalesce(sum(l.balance), 0) from account_move_line l join account_account a on a.id = l.account_id "
                  "where l.company_id = %s and l.parent_state = 'posted' and l.date <= %s and a.account_type not in " + PL_TYPES,
                  (company.id, TO))[0][0] or 0.0
    sql_pl_all = sql("select coalesce(sum(l.balance), 0) from account_move_line l join account_account a on a.id = l.account_id "
                     "where l.company_id = %s and l.parent_state = 'posted' and l.date <= %s and a.account_type in " + PL_TYPES,
                     (company.id, TO))[0][0] or 0.0
    check(near(sql_net + sql_pl_all, 0, 0.05), '%s: SQL: every account up to the date nets to 0 (%s)' % (label, '{:,.2f}'.format(sql_net + sql_pl_all)))
    sql_prior = -(sql("select coalesce(sum(l.balance), 0) from account_move_line l join account_account a on a.id = l.account_id "
                      "where l.company_id = %s and l.parent_state = 'posted' and l.date < %s and a.account_type in " + PL_TYPES,
                      (company.id, FY_FROM))[0][0] or 0.0)
    pye = row(bs, 'pye')
    check(near(pye['values']['p0'] if pye else 0.0, sql_prior, 0.05), "%s: prior years' results not yet closed %s = SQL %s"
          % (label, '{:,.2f}'.format(pye['values']['p0'] if pye else 0.0), '{:,.2f}'.format(sql_prior)))
    cf = RA.get_report('cash_flow', fy)
    check(not row(cf, 'diff'), '%s: cash flow reconciles to cash on the balance sheet (no difference row)' % label)
    cash_sql = sql("select coalesce(sum(l.balance), 0) from account_move_line l join account_account a on a.id = l.account_id "
                   "where l.company_id = %s and l.parent_state = 'posted' and l.date <= %s and a.account_type = 'asset_cash'",
                   (company.id, TO))[0][0] or 0.0
    rc = row(cf, 'recon')
    check(rc is not None and near(rc['values']['amount'], cash_sql), '%s: cash flow closing cash %s = SQL cash and bank %s'
          % (label, '{:,.2f}'.format(rc['values']['amount'] if rc else 0), '{:,.2f}'.format(cash_sql)))
    eq = RA.get_report('equity_changes', fy)
    eq_profit = row(eq, 'profit')['values']['total']
    eq_close = row(eq, 'close')['values']['total']
    bs_eq = next((r['values']['p0'] for r in bs['rows'] if r['id'] in ('equity', 'sec_equity', 'eq_total')), None)
    check(near(eq_profit, profit), '%s: statement of changes in equity: profit = profit and loss' % label)
    check(bs_eq is not None and near(eq_close, bs_eq), '%s: equity at the end %s = equity on the balance sheet %s'
          % (label, '{:,.2f}'.format(eq_close), '{:,.2f}'.format(bs_eq or 0)))
    notes = RA.get_report('notes_pack', {'date_to': S(TO)})
    groups = {r['label'].strip().lower(): r['values'].get('p0') for r in bs['rows'] if r['kind'] == 'group'}
    nbad, nn = [], 0
    secs = [r for r in notes['rows'] if r['kind'] == 'section' and 'p0' in r['values']]
    for sec in secs:
        name = sec['label'].split('. ', 1)[-1].strip().lower()
        kids = [r for r in notes['rows'] if r['kind'] == 'line' and str(r['id']).startswith(sec['id'] + '_')]
        if name in groups:
            nn += 1
            if not near(sec['values']['p0'], groups[name]):
                nbad.append('%s %s vs %s' % (name[:20], sec['values']['p0'], groups[name]))
        if kids and not near(sum(k['values'].get('p0', 0.0) for k in kids), sec['values']['p0'], 0.05):
            nbad.append('%s: lines do not add up' % name[:20])
    check(nn and not nbad, '%s: notes: %d notes equal their balance-sheet line and add up%s' % (label, nn, (' - ' + '; '.join(nbad[:4])) if nbad else ''))
    out.update(profit=profit, assets=assets, prior=row(bs, 'pye')['values']['p0'] if row(bs, 'pye') else 0.0)
    return out


base = tie_out('year open')
# prior year closed, then reopened, as the manager
lock_before = company.fiscalyear_lock_date
with env.cr.savepoint():
    W = env['sb.year.end.wizard'].with_user(mgr_u)
    wiz = W.create({'year': str(FY - 1)})
    # the year must have nothing in draft: the test's own setup clears what is pending on the copy (as in a real close)
    blocking = wiz.sudo()._sb_blocking(PFY_FROM, PFY_TO)
    if blocking:
        span = [('company_id', '=', company.id), ('date', '>=', PFY_FROM), ('date', '<=', PFY_TO)]
        env['sb.voucher'].sudo().search(span + [('state', '=', 'draft')]).unlink()
        env['account.move'].sudo().search(span + [('state', '=', 'draft')]).with_context(force_delete=True).unlink()
        if 'sb.stock.voucher' in env:
            for s in env['sb.stock.voucher'].sudo().search(span + [('state', '=', 'draft')]):
                try:
                    with env.cr.savepoint():
                        s.unlink()
                except Exception:
                    s.write({'state': 'void'}) if 'void' in dict(s._fields['state'].selection) else None
    closed_ok = False
    try:
        with env.cr.savepoint():
            wiz.action_close_year()
        closed_ok = True
    except Exception as e:  # noqa: BLE001
        print('   close refused:', str(e).splitlines()[0][:160])
    check(closed_ok, 'prior year %s closed by the manager' % (FY - 1))
    if closed_ok:
        after = tie_out('prior year closed')
        check(near(after['profit'], base['profit']) and near(after['assets'], base['assets']),
              'closing the prior year changes neither this year\'s profit nor total assets')
        prev = RA.get_report('profit_loss', {'date_from': S(PFY_FROM), 'date_to': S(PFY_TO)})
        check(not near(row(prev, 'profit')['values']['p0'], 0), 'the closed year\'s profit and loss still shows its result')
        wiz2 = W.create({'year': str(FY - 1), 'reopen_reason': 'regression test'})
        reopened = False
        try:
            with env.cr.savepoint():
                wiz2.action_reopen_year()
            reopened = True
        except Exception as e:  # noqa: BLE001
            print('   reopen refused:', str(e).splitlines()[0][:160])
        check(reopened, 'prior year reopened by the manager with a reason')
        if reopened:
            again = tie_out('prior year reopened')
            check(near(again['profit'], base['profit']) and near(again['assets'], base['assets']) and near(again['prior'], base['prior']),
                  'after reopening, the statements are back to where they were')

# ================================================================== 3.2 every figure drills to entries that sum to it
item('3.2')


def gl_of(drill, base_opts):
    o = dict(base_opts)
    o.update({k: v for k, v in drill.items() if k != 'report'})
    return RA.get_report(drill.get('report', 'general_ledger'), o), o


def drill_sum(doc_opts, drill, aid):
    gl, o = gl_of(drill, doc_opts)
    g = row(gl, 'a%d' % aid)
    if not g:
        return None, None
    lines = RA.get_children(g['lazy'], gl['options']) if g.get('lazy') else []
    entry = [l for l in lines if l.get('open')]
    s = sum((l['values'].get('debit') or 0.0) - (l['values'].get('credit') or 0.0) for l in entry)
    return g['values'], s


def check_drills(doc, label, value_key, sign_fn, limit=12):
    rows = [r for r in doc['rows'] if r.get('drill') and str(r['id'])[1:].isdigit() and str(r['id']).startswith('a')]
    rows.sort(key=lambda r: -abs(r['values'].get(value_key) or 0.0))
    bad, n = [], 0
    for r in rows[:limit]:
        keys = [(value_key, r['drill'])] + [(k, d) for k, d in (r.get('drills') or {}).items() if k != value_key]
        for key, drill in keys:
            gv, s = drill_sum(doc['options'], drill, int(r['id'][1:]))
            n += 1
            want = r['values'].get(key) or 0.0
            if gv is None:
                if not near(want, 0):
                    bad.append('%s %s: no ledger' % (r['label'][:20], key))
                continue
            opening = gv.get('opening') or 0.0
            got = sign_fn(s + (opening if drill.get('date_from', 'x') is None else 0.0))
            if not near(abs(got), abs(want), 0.05):
                bad.append('%s %s: %s vs %s' % (r['label'][:24], key, '{:,.2f}'.format(want), '{:,.2f}'.format(got)))
    check(n and not bad, '%s: %d figures drill to entries that sum to them%s' % (label, n, (' - ' + '; '.join(bad[:5])) if bad else ''))


tbd = RA.get_report('trial_balance', fy)
# a trial balance line drills to the year's ledger lines: debits - credits of the entries = the line's movement
rows = sorted([r for r in tbd['rows'] if r.get('drill') and str(r['id'])[1:].isdigit()], key=lambda r: -abs(r['values']['debit']))[:12]
bad = []
for r in rows:
    gv, s = drill_sum(tbd['options'], r['drill'], int(r['id'][1:]))
    if gv is None or not near(s, r['values']['debit'] - r['values']['credit'], 0.05):
        bad.append(r['values'].get('code'))
check(rows and not bad, 'trial balance: %d lines drill to entries equal to their movement%s' % (len(rows), (' - ' + str(bad)) if bad else ''))
plc = RA.get_report('profit_loss', dict(fy, comparison='last_year'))
check_drills(plc, 'profit and loss with last year', 'p0', lambda s: -s)
bsc = RA.get_report('balance_sheet', {'date_to': S(TO), 'comparison': 'previous', 'comparison_count': 1})
check_drills(bsc, 'balance sheet with a comparison date', 'p0', lambda s: s)

# ================================================================== 3.3 comparatives
item('3.3')
plly = RA.get_report('profit_loss', dict(fy, comparison='last_year'))
ly_from, ly_to = PFY_FROM, PFY_FROM + (TO - FY_FROM)
alone = RA.get_report('profit_loss', {'date_from': S(ly_from), 'date_to': S(ly_to)})
check(near(row(plly, 'profit')['values'].get('p1'), row(alone, 'profit')['values']['p0']),
      'profit and loss, last year column = last year on its own (%s)' % '{:,.2f}'.format(row(alone, 'profit')['values']['p0']))
check(near(row(plly, 'profit')['values'].get('var', 0), row(plly, 'profit')['values']['p0'] - row(plly, 'profit')['values']['p1'])
      or 'var' not in row(plly, 'profit')['values'], 'variance column = this year - last year')
if len(cur_periods) >= 3:
    p = cur_periods[-2]
    plp = RA.get_report('profit_loss', {'date_from': S(p[2]), 'date_to': S(p[3]), 'comparison': 'previous', 'comparison_count': 2})
    ok_ = True
    for i, q in ((1, cur_periods[-3]), (2, cur_periods[-4] if len(cur_periods) >= 4 else None)):
        if q is None:
            continue
        one = RA.get_report('profit_loss', {'date_from': S(q[2]), 'date_to': S(q[3])})
        ok_ &= near(row(plp, 'profit')['values'].get('p%d' % i), row(one, 'profit')['values']['p0'])
    check(ok_, 'profit and loss, previous periods: each column = that month on its own (%s)' % p[1])
fyfull = {'date_from': S(FY_FROM), 'date_to': S(PER[-1][3])}
plm = RA.get_report('profit_loss', dict(fyfull, comparison='months'))
full_profit = row(RA.get_report('profit_loss', fyfull), 'profit')['values']['p0']
mk = [c['key'] for c in plm['columns'] if c['key'].startswith('m')]
prow = row(plm, 'profit')['values']
check(len(mk) == 12 and near(sum(prow.get(k, 0) for k in mk), prow.get('total')) and near(prow.get('total'), full_profit),
      'profit and loss by month: 12 months, they add up to the year (%s)' % '{:,.2f}'.format(prow.get('total') or 0))
labels = [c['label'] for c in plm['columns'] if c['key'].startswith('m')]
check(labels and labels[0].startswith('Hamle') and labels[-1].startswith('Sene'), 'months run Hamle to Sene: %s ... %s' % (labels[0] if labels else '', labels[-1] if labels else ''))
bsp = RA.get_report('balance_sheet', {'date_to': S(TO), 'comparison': 'previous', 'comparison_count': 2})
cols = [c for c in bsp['columns'] if c['key'].startswith('p') and c['key'] != 'p0']
ok_ = bool(cols)
from datetime import datetime  # noqa: E402
for c in cols:
    as_at = c.get('date_to') or S(datetime.strptime(c['label'].replace('As at', '').strip(), '%d %b %Y').date())
    one = RA.get_report('balance_sheet', {'date_to': as_at})
    ok_ &= near(row(bsp, 'assets')['values'].get(c['key']), row(one, 'assets')['values']['p0'])
check(ok_, 'balance sheet, %d comparison dates: each = the balance sheet at that date' % len(cols))
cfl = RA.get_report('cash_flow', dict(fy, comparison='last_year'))
check(len([c for c in cfl['columns'] if c['type'] == 'monetary']) >= 2 and not row(cfl, 'diff'), 'cash flow with last year runs and reconciles')
eqc = RA.get_report('equity_changes', dict(fy, comparison='last_year'))
check(row(eqc, 'close') is not None, 'statement of changes in equity with last year runs')
tbp = RA.get_report('trial_balance', {'date_from': S(LAST_FULL[2]), 'date_to': S(LAST_FULL[3])})
check(near(row(tbp, 'total')['values']['debit'], row(tbp, 'total')['values']['credit']), 'trial balance for one month (%s) balances' % LAST_FULL[1])

# ================================================================== 3.4 filters, printed on PDF and Excel
item('3.4')
from pdfminer.high_level import extract_text  # noqa: E402
import openpyxl  # noqa: E402


def pdf_text(doc):
    pdf = RA.render_pdf(dict(doc))
    return pdf, extract_text(io.BytesIO(pdf))


def xlsx_text(doc):
    data = RA.render_xlsx([dict(doc)])
    wb = openpyxl.load_workbook(io.BytesIO(data))
    return ' '.join(str(c.value) for ws in wb.worksheets for r in ws.iter_rows() for c in r if c.value is not None)


journal = env['account.move.line'].search([('company_id', '=', company.id), ('parent_state', '=', 'posted'),
                                           ('date', '>=', LAST_FULL[2]), ('date', '<=', LAST_FULL[3])], limit=1).journal_id
mon = {'date_from': S(LAST_FULL[2]), 'date_to': S(LAST_FULL[3])}
glj = RA.get_report('general_ledger', dict(mon, journal_ids=[journal.id]))
lines = []
for g in [r for r in glj['rows'] if r.get('lazy')][:8]:
    lines += [l for l in RA.get_children(g['lazy'], glj['options']) if l.get('open')]
check(lines and all((l['values'].get('jrnl') or journal.code) == journal.code for l in lines),
      'journal filter %s: %d ledger lines, all from that journal' % (journal.code, len(lines)))
check(any(journal.code in f for f in glj['filters']), 'journal filter is listed in the heading')
bank = acc_by_code('1104-0002') or env['account.account'].search([('account_type', '=', 'asset_cash'), ('company_ids', 'in', company.id)], limit=1)
tba = RA.get_report('trial_balance', dict(mon, account_ids=[bank.id]))
check([r for r in tba['rows'] if r['kind'] == 'line'] and all(r['id'] == 'a%d' % bank.id for r in tba['rows'] if r['kind'] == 'line'),
      'account filter: the trial balance shows only %s' % bank.code)
partner = env['account.move.line'].search([('company_id', '=', company.id), ('parent_state', '=', 'posted'),
                                           ('partner_id', '!=', False), ('account_id.account_type', '=', 'asset_receivable')],
                                          limit=1, order='date desc').partner_id
glp = RA.get_report('general_ledger', dict(fy, partner_ids=[partner.id]))
plines = []
for g in [r for r in glp['rows'] if r.get('lazy')][:6]:
    plines += [l for l in RA.get_children(g['lazy'], glp['options']) if l.get('open')]
check(plines and all(partner.name[:15] in (l['values'].get('partner') or '') for l in plines),
      'customer filter %s: %d ledger lines, all of that customer' % (partner.name, len(plines)))
# a cost centre split 60 / 40: the filter takes 60% of the line
Plan = env['account.analytic.plan'].sudo()
plan = Plan.create({'name': 'A3 cost centres'})
cc1 = env['account.analytic.account'].sudo().create({'name': 'A3 Factory', 'plan_id': plan.id, 'company_id': company.id})
cc2 = env['account.analytic.account'].sudo().create({'name': 'A3 Office', 'plan_id': plan.id, 'company_id': company.id})
exp = env['account.account'].search([('account_type', '=', 'expense'), ('company_ids', 'in', company.id)], limit=1)
jday = LAST_FULL[3]
# cost centres are split on journal entry lines (setup: an entry with a 60 / 40 split)
jnl = env['account.journal'].search([('company_id', '=', company.id), ('type', '=', 'general')], limit=1)
mv = env['account.move'].sudo().create({'move_type': 'entry', 'journal_id': jnl.id, 'date': jday, 'ref': 'A3 split',
                                        'line_ids': [Command.create({'account_id': exp.id, 'name': 'A3 split', 'debit': 1000.0,
                                                                     'analytic_distribution': {str(cc1.id): 60, str(cc2.id): 40}}),
                                                     Command.create({'account_id': bank.id, 'name': 'A3 split', 'credit': 1000.0})]})
mv.sudo().action_post()
tbc = RA.get_report('trial_balance', dict(mon, analytic_ids=[cc1.id]))
r = row(tbc, 'a%d' % exp.id)
check(r and near(r['values']['debit'], 600), 'cost centre filter: a 1,000 line split 60/40 counts 600 for the 60%% centre (%s)'
      % (r['values']['debit'] if r else None))
gls = RA.get_report('general_ledger', dict(mon, search='A3 split'))
check(any(r.get('lazy') for r in gls['rows']) and any('Search' in f for f in gls['filters']), 'search filter finds the entry and is listed')
doc = RA.get_report('trial_balance', dict(mon, journal_ids=[journal.id], analytic_ids=[cc1.id]))
pdf, text = pdf_text(doc)
check(pdf[:4] == b'%PDF' and ('Journals: %s' % journal.code) in text and 'Analytic' in text, 'PDF prints the filters (journal, cost centre)')
xt = xlsx_text(doc)
check(('Journals: %s' % journal.code) in xt and 'Analytic' in xt, 'Excel prints the filters (journal, cost centre)')

# ================================================================== 3.5 draft basis
item('3.5')
sale_acc = acc_by_code('4000-0001') or env['account.account'].search([('account_type', '=', 'income'), ('company_ids', 'in', company.id)], limit=1)
cust = env['res.partner'].sudo().create({'name': 'A3 draft customer', 'customer_rank': 1})
draft = env['sb.voucher'].with_user(acc_u).create({'voucher_type': 'journal', 'date': LAST_FULL[3], 'memo': 'A3 draft',
                                                   'line_ids': [Command.create({'account_id': bank.id, 'name': 'A3 draft', 'debit': 7777.0}),
                                                                Command.create({'account_id': sale_acc.id, 'name': 'A3 draft', 'credit': 7777.0})]})
check(draft.state == 'draft', 'a draft voucher of 7,777 is saved, not posted')
posted_pl = RA.get_report('profit_loss', mon)
draft_pl = RA.get_report('profit_loss', dict(mon, posted_only=False))
check(near(row(draft_pl, 'revenue')['values']['p0'] - row(posted_pl, 'revenue')['values']['p0'], 7777.0),
      'draft basis includes the draft voucher (revenue + 7,777); the posted basis does not')
check('unposted' in draft_pl['title'].lower() and draft_pl['subtitle'].startswith('DRAFT'), 'draft basis is labelled: "%s"' % draft_pl['title'])
pdf, text = pdf_text(draft_pl)
check('DRAFT' in text.upper() and 'unposted' in text.lower(), 'the PDF says it is a draft')
check(not RA.get_report('profit_loss', mon)['title'].lower().count('unposted'), 'the posted basis is not labelled draft')

# ================================================================== 3.6 large exports
item('3.6')
yr = {'date_from': S(PFY_FROM), 'date_to': S(PFY_TO)}
t = time.time()
gl = RA.get_report('general_ledger', yr)
gl = RA.get_report('general_ledger', yr, expanded=[r['id'] for r in gl['rows'] if r.get('lazy')])
n_lines = len([r for r in gl['rows'] if r.get('open')])
pdf = RA.render_pdf(dict(gl))
secs = time.time() - t
check(pdf[:4] == b'%PDF' and secs < 30, 'full-year general ledger (%d lines) as PDF in %.1f s (limit 30 s), %d KB'
      % (n_lines, secs, len(pdf) // 1024))
t = time.time()
data = RA.render_xlsx([dict(gl)])
wb = openpyxl.load_workbook(io.BytesIO(data))
cells = sum(ws.max_row for ws in wb.worksheets)
check(data[:2] == b'PK' and cells >= n_lines and time.time() - t < 60, 'the same ledger as Excel opens cleanly (%d rows, %.1f s)'
      % (cells, time.time() - t))
tb_x = RA.render_xlsx([RA.get_report('trial_balance', yr)])
wb = openpyxl.load_workbook(io.BytesIO(tb_x))
check(any(isinstance(c.value, str) and c.value.startswith('=SUM') for ws in wb.worksheets for r in ws.iter_rows() for c in r),
      'the trial balance in Excel totals with live =SUM formulas')

# ================================================================== 3.7 Amharic prints
item('3.7')
am = env['res.partner'].sudo().create({'name': 'ኢኮ ግሪን ደንበኛ አዲስ አበባ', 'customer_rank': 1})
amj = env['sb.voucher'].with_user(acc_u).create({'voucher_type': 'journal', 'date': LAST_FULL[3], 'memo': 'የሽያጭ ማስተካከያ',
                                                 'line_ids': [Command.create({'account_id': bank.id, 'name': 'ክፍያ', 'debit': 100.0,
                                                                              'partner_id': am.id}),
                                                              Command.create({'account_id': sale_acc.id, 'name': 'ክፍያ', 'credit': 100.0,
                                                                              'partner_id': am.id})]})
amj.with_user(acc_u).action_post()
amd = RA.get_report('general_ledger', dict(mon, partner_ids=[am.id]))
amd = RA.get_report('general_ledger', dict(mon, partner_ids=[am.id]), expanded=[r['id'] for r in amd['rows'] if r.get('lazy')])
pdf, text = pdf_text(amd)
check('ኢኮ' in text or 'ደንበኛ' in text or 'ክፍያ' in text, 'Amharic text is in the PDF (HTML route)')
from PyPDF2 import PdfReader  # noqa: E402


def fonts(pdf):
    out = set()
    for p in PdfReader(io.BytesIO(pdf)).pages:
        res = p.get('/Resources') or {}
        for f in (res.get('/Font') or {}).values():
            out.add(str(f.get_object().get('/BaseFont')))
    return out


check(any('Ethiopic' in f for f in fonts(pdf)), 'the PDF embeds the Ethiopic font: %s' % sorted(fonts(pdf))[:4])
pdf2 = RA._render_pdf_direct(dict(amd))
t2 = extract_text(io.BytesIO(pdf2))
check(any('Ethiopic' in f for f in fonts(pdf2)) and ('ደንበኛ' in t2 or 'ክፍያ' in t2), 'large-report route (ReportLab) draws Amharic in Noto Sans Ethiopic')
xt = xlsx_text(amd)
check('ደንበኛ' in xt or 'ክፍያ' in xt, 'Excel keeps the Amharic text')

# ================================================================== 3.8 payroll only for HR
item('3.8')
check(raises(lambda: RA.get_report('payroll_register', fy), AccessError), 'accountant cannot open the payroll register')
check(raises(lambda: RM.get_report('payroll_register', fy), AccessError) or mgr_u.has_group('sonador_books.group_sb_hr'),
      'manager (without HR) cannot open the payroll register')
RH = env['sb.report'].with_user(hr_u)
ok_ = True
for k in ('payroll_register', 'paye_declaration', 'poessa_declaration', 'employee_list'):
    try:
        RH.get_report(k, fy)
    except Exception as e:  # noqa: BLE001
        ok_ = False
        print('   HR %s: %s' % (k, str(e)[:120]))
check(ok_, 'HR opens the payroll register, PAYE and pension declarations and the employee list')
check(raises(lambda: RH.get_report('trial_balance', fy), AccessError), 'HR alone cannot open accounting reports')
for name, u in (('accountant', acc_u), ('clerk', clerk_u), ('manager', mgr_u)):
    seen = None
    try:
        seen = env['sb.payroll.line'].with_user(u).search_count([]) + env['sb.employee'].with_user(u).search_count([])
    except AccessError:
        seen = 0
    check(seen == 0, '%s sees no payroll lines or employees (%s)' % (name, seen))
c = env['sb.report'].with_user(acc_u).get_center_data('payroll')
check(c.get('restricted') and not c.get('employees'), 'payroll navigation center is closed to the accountant')
check(raises(lambda: env['sb.report'].with_user(acc_u)._check_report_access('payroll_register'), AccessError),
      'export route refuses the payroll register to the accountant')
from odoo.addons.sonador_books.models.report_catalog import CATALOG_BY_KEY  # noqa: E402
pkey = next(k for k, v in CATALOG_BY_KEY.items() if v['report'] == 'payroll_register' and v['priority'] == 1)
tkey = next(k for k, v in CATALOG_BY_KEY.items() if v['report'] == 'trial_balance' and v['priority'] == 1)
Pk = env['sb.report.pack']
check(raises(lambda: Pk.with_user(acc_u).create({'name': 'A3 acc pack', 'line_ids': [Command.create({'report_key': pkey})]})),
      'accountant cannot put the payroll register in a report pack')
check(raises(lambda: Pk.with_user(hr_u).create({'name': 'A3 HR pack', 'partner_ids': [Command.set([acc_u.partner_id.id])],
                                                'line_ids': [Command.create({'report_key': pkey})]})),
      'HR cannot send a payroll pack to a non-HR recipient')
legacy = Pk.sudo().create({'name': 'A3 legacy', 'line_ids': [Command.create({'report_key': tkey})]})
env.cr.execute("insert into sb_report_pack_line(pack_id, report_key, sequence) values (%s, %s, 2)", (legacy.id, pkey))
env.cr.execute("update sb_report_pack set create_uid = %s where id = %s", (acc_u.id, legacy.id))
env.invalidate_all()
docs, _a, _b = legacy.sudo()._docs(sending=True)
check(pkey not in [d['key'] for d in docs] and 'payroll_register' not in [d.get('key') for d in docs],
      'an accountant\'s pack holding payroll is sent without it: %s' % [d['key'] for d in docs])

# ================================================================== 3.9 Ethiopian periods
item('3.9')
per = fiscal_periods(2019)
check(len(per) == 12 and per[0][1].startswith('Hamle') and per[-1][1].startswith('Sene'),
      'fiscal year 2019 E.C. has 12 periods, Hamle to Sene')
check(str(per[0][2]) == '2026-07-08' and str(per[-1][3]) == '2027-07-07', 'it runs 8 Jul 2026 to 7 Jul 2027')
neh = per[1]
check(neh[1].startswith('Nehase') and (neh[3] - neh[2]).days + 1 == 35 and str(neh[3]) == '2026-09-10',
      'Nehase 2018 carries the 5 days of Pagume: %s to %s (%d days)' % (neh[2], neh[3], (neh[3] - neh[2]).days + 1))
check(str(per[2][2]) == '2026-09-11', 'Meskerem 1, 2019 = 11 Sep 2026')
leap = fiscal_periods(2020)
check((leap[1][3] - leap[1][2]).days + 1 == 36 and str(leap[2][2]) == '2027-09-12',
      'in a leap year Pagume has 6 days: Nehase 2019 ends %s, Meskerem 2020 starts %s' % (leap[1][3], leap[2][2]))
covered = sum((p[3] - p[2]).days + 1 for p in per)
check(covered == 365 and all((per[i + 1][2] - per[i][3]).days == 1 for i in range(11)), 'periods are contiguous and cover the year (%d days)' % covered)
check(format_ethiopian(fields.Date.to_date('2026-11-02')).startswith('Tikimt 23, 2019'), 'Ethiopian dates: 2 Nov 2026 = %s'
      % format_ethiopian(fields.Date.to_date('2026-11-02')))
check(fiscal_year_of(fields.Date.to_date('2026-07-07')) == 2018 and fiscal_year_of(fields.Date.to_date('2026-07-08')) == 2019,
      'the fiscal year turns on Hamle 1 (8 Jul)')
pm = RA.get_report('profit_loss', {'date_from': '2026-07-08', 'date_to': '2027-07-07', 'comparison': 'months'})
mlabels = [c['label'] for c in pm['columns'] if c['key'].startswith('m')]
check(len(mlabels) == 12 and mlabels[1].startswith('Nehase') and mlabels[2].startswith('Meskerem'), '12-period income statement columns: %s'
      % ', '.join(l.split()[0] for l in mlabels))
nm = RA.get_report('profit_loss', {'date_from': S(neh[2]), 'date_to': S(neh[3])})
pag = env['account.move.line'].search_count([('company_id', '=', company.id), ('parent_state', '=', 'posted'),
                                             ('date', '>=', '2026-09-06'), ('date', '<=', '2026-09-10'),
                                             ('account_id.include_initial_balance', '=', False)])
nrev = row(pm, 'profit')['values'].get(next(c['key'] for c in pm['columns'] if c['label'].startswith('Nehase')))
check(near(nrev, row(nm, 'profit')['values']['p0']), 'Pagume entries (%d lines) fall in Nehase in the 12-period report' % pag)

# ================================================================== summary
print('-' * 60)
for n in sorted(RESULTS, key=lambda x: float(x)):
    r = RESULTS[n]
    secs = (T0[str(round(float(n) + 0.1, 1))] if str(round(float(n) + 0.1, 1)) in T0 else time.time()) - T0[n]
    print('ITEM %s %s (%d/%d checks) %.1f s' % (n, 'PASS' if all(r) else 'FAIL', sum(r), len(r), secs))
env.cr.rollback()
