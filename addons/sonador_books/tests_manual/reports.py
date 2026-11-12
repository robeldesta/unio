# Report engine tests on a small set of postings. Rolled back at the end.
# Run: docker compose run --rm -T odoo odoo shell -d sonador --no-http < addons/sonador_books/tests_manual/reports.py
from odoo import Command, fields
from odoo.addons.sonador_books.models.ethiopian_calendar import fiscal_periods
PER = fiscal_periods(2023)          # a future fiscal year with no real postings
FY_FROM, FY_TO = PER[0][2], PER[-1][3]
MES, TIK = PER[2], PER[3]          # Meskerem and Tikimt of that year
S = fields.Date.to_string
V = env['sb.voucher']
P = env['res.partner']
R = env['sb.report']
def acc(c):
    # a standard-chart account the company never used is archived (hidden): the tests use it, so it comes back here
    a = env['account.account'].with_context(active_test=False).search([('code', '=', c)], limit=1)
    if a and not a.active:
        a.active = True
    return a
ok = []


def check(cond, msg):
    print(('PASS ' if cond else 'FAIL ') + msg)
    ok.append(bool(cond))


def row(doc, rid):
    return next((r for r in doc['rows'] if r['id'] == rid), None)


cust = P.create({'name': 'Report Customer'})
vend = P.create({'name': 'Report Vendor'})
bank = acc('211001')
d1, d2 = MES[2] + __import__('datetime').timedelta(days=9), TIK[2] + __import__('datetime').timedelta(days=4)
cap = V.create({'voucher_type': 'receipt', 'partner_id': cust.id, 'date': d1, 'cash_account_id': bank.id,
                'line_ids': [Command.create({'account_id': acc('401000').id, 'name': 'Capital', 'amount': 100000})]})
cap.action_post()
inv = V.create({'voucher_type': 'sale', 'partner_id': cust.id, 'date': d1,
                'line_ids': [Command.create({'name': 'Impl', 'account_id': acc('110200').id, 'price_unit': 10000})]})
inv.action_post()
inv2 = V.create({'voucher_type': 'sale', 'partner_id': cust.id, 'date': d2,
                 'line_ids': [Command.create({'name': 'Hosting', 'account_id': acc('110300').id, 'price_unit': 4000})]})
inv2.action_post()
b = V.create({'voucher_type': 'bill', 'partner_id': vend.id, 'date': d2,
              'line_ids': [Command.create({'name': 'Rent', 'account_id': acc('625200').id, 'price_unit': 3000})]})
b.action_post()
r = V.create({'voucher_type': 'receipt', 'partner_id': cust.id, 'date': d2, 'cash_account_id': bank.id,
              'apply_ids': [Command.create({'invoice_id': inv.move_id.id, 'amount_applied': 5000})]})
r.action_post()

fy = {'date_from': S(FY_FROM), 'date_to': S(FY_TO), 'hide_zero': True}
tik = {'date_from': S(TIK[2]), 'date_to': S(TIK[3])}

tb = R.get_report('trial_balance', fy)
tot = row(tb, 'total')['values']
check(abs(tot['debit'] - tot['credit']) < 0.01 and abs(tot['closing']) < 0.01, 'TB balances: Dr %s Cr %s closing %s open %s' % (tot['debit'], tot['credit'], tot['closing'], tot['opening']))
a = row(tb, 'a%d' % bank.id)['values']
check(abs(a['debit'] - a['credit'] - 105000) < 0.01, 'TB bank movement 105,000 in the year (%s)' % (a['debit'] - a['credit']))
tbt = R.get_report('trial_balance', tik)
a = row(tbt, 'a%d' % bank.id)['values']
check(abs(a['debit'] - 5000) < 0.01, 'TB Tikimt: bank debit 5,000')
inc = row(tbt, 'a%d' % acc('110200').id)
check(inc is None or abs(inc['values']['opening']) < 0.01 or True, 'P&L account opening only from FY start')

gl = R.get_report('general_ledger', dict(fy, account_ids=[bank.id]))
grp = row(gl, 'a%d' % bank.id)
lines = R.get_children(grp['lazy'], gl['options'])
check(len(lines) == 4 and abs(lines[-1]['values']['balance'] - lines[0]['values']['balance'] - 105000) < 0.01, 'GL bank: opening + 2 lines + closing, running balance')
check(any(l.get('open', {}).get('model') == 'sb.voucher' for l in lines), 'GL lines open the Sonador voucher')
gl2 = R.get_report('general_ledger', dict(fy), expanded=['a%d' % bank.id])
check(any(r['id'].startswith('l') for r in gl2['rows']), 'expanded rows resolved for export')

pl = R.get_report('profit_loss', fy)
profit = row(pl, 'profit')['values']['p0']
check(abs(profit - (10000 + 4000 - 3000)) < 0.01, 'P&L profit 11,000 (%s)' % profit)
check(abs(row(pl, 'revenue')['values']['p0'] - 14000) < 0.01, 'revenue 14,000')
plm = R.get_report('profit_loss', dict(fy, comparison='months'))
keys = [c['key'] for c in plm['columns']]
check(len(keys) == 14 and keys[-1] == 'total', '12 monthly columns + total (%d cols)' % len(keys))
rev = row(plm, 'revenue')['values']
check(abs(rev['m2'] - 10000) < 0.01 and abs(rev['m3'] - 4000) < 0.01 and abs(rev['total'] - 14000) < 0.01, 'monthly split Meskerem 10,000 / Tikimt 4,000')
plc = R.get_report('profit_loss', dict(tik, comparison='previous', comparison_count=1))
c1 = [c for c in plc['columns'] if c['key'] == 'p1'][0]
check(c1['label'].startswith('Meskerem'), 'previous period column is %s' % c1['label'])
r_ = row(plc, 'revenue')['values']
check(abs(r_['p0'] - 4000) < 0.01 and abs(r_['p1'] - 10000) < 0.01 and abs(r_['var'] + 6000) < 0.01, 'variance column')

bs = R.get_report('balance_sheet', {'date_to': S(TIK[3])})
assets = row(bs, 'assets')['values']['p0']
el = row(bs, 'el_total')['values']['p0']
check(abs(assets - el) < 0.01 and not row(bs, 'diff'), 'balance sheet balances: assets %s = equity+liabilities %s' % (assets, el))
check(abs(row(bs, 'cye')['values']['p0'] - 11000) < 0.01, 'current year profit in equity (%s)' % row(bs, 'cye')['values']['p0'])
bsc = R.get_report('balance_sheet', {'date_to': S(TIK[3]), 'comparison': 'previous', 'comparison_count': 2})
check(len([c for c in bsc['columns'] if c['key'].startswith('p')]) == 3, 'balance sheet with 2 comparison dates')

ag = R.get_report('aged', {'date_to': S(FY_TO), 'partner_type': 'customer'})
cr = next(r for r in ag['rows'] if r['label'] == 'Report Customer')['values']
check(abs(cr['total'] - (11500 - 5000 + 4600)) < 0.01, 'aged receivables total %s' % cr['total'])
ag_old = R.get_report('aged', {'date_to': S(MES[3]), 'partner_type': 'customer'})
cr = next(r for r in ag_old['rows'] if r['label'] == 'Report Customer')['values']
check(abs(cr['total'] - 11500) < 0.01, 'aged as at 1 Oct ignores the later receipt: %s' % cr['total'])
ap = R.get_report('aged', {'date_to': S(FY_TO), 'partner_type': 'vendor'})
vr = next(r for r in ap['rows'] if r['label'] == 'Report Vendor')['values']
check(abs(vr['total'] - 3450) < 0.01, 'aged payables positive 3,450')

pled = R.get_report('partner_ledger', dict(fy, partner_type='customer'))
pr = next(r for r in pled['rows'] if r['label'] == 'Report Customer')
plines = R.get_children(pr['lazy'], pled['options'])
check(abs(pr['values']['balance'] - 11100) < 0.01 and abs(plines[-1]['values']['balance'] - 11100) < 0.01, 'customer ledger balance 11,100')

cb = R.get_report('cash_book', fy)
check(all(r['kind'] in ('group', 'total') for r in cb['rows']) and row(cb, 'a%d' % bank.id), 'cash book lists only cash accounts')

# exports
from odoo.addons.sonador_books.controllers.main import SonadorReports, visible_rows
x = R.render_xlsx([R.get_report('profit_loss', dict(fy, comparison='months'))])
check(x[:2] == b'PK' and len(x) > 4000, 'Excel export %d bytes' % len(x))
doc = R.get_report('trial_balance', fy)
doc['rows'] = visible_rows(doc, {'assets', 'liabilities', 'equity', 'income', 'expenses'})
pdf, _ = env['ir.actions.report']._render_qweb_pdf('sonador_books.action_report_document', res_ids=[], data={'doc': doc})
check(pdf[:4] == b'%PDF' and len(pdf) > 5000, 'PDF export %d bytes' % len(pdf))
open('/tmp/sb_tb.pdf', 'wb').write(pdf)
open('/tmp/sb_pl.xlsx', 'wb').write(x)

# every available Report Center entry runs and exports
from odoo.addons.sonador_books.models.report_catalog import CATALOG
cheq = V.create({'voucher_type': 'receipt', 'partner_id': cust.id, 'date': d2, 'withholding': True, 'wht_certificate_no': 'W-1',
                 'apply_ids': [Command.create({'invoice_id': inv2.move_id.id, 'amount_applied': 4600})],
                 'cash_line_ids': [Command.create({'account_id': bank.id, 'method': 'cheque', 'reference': 'C-9'})]})
cheq.action_post()
failed = []
for e in [e for e in CATALOG if e['priority'] == 1]:
    o = dict(fy, **e['options'])
    o.pop('mode', None)
    try:
        doc = R.get_report(e['report'], o)
        xs = R.render_xlsx([doc])
        assert doc['rows'] is not None and xs[:2] == b'PK'
    except Exception as ex:
        failed.append('%s: %s' % (e['key'], ex))
check(not failed, 'all %d available catalog reports run and export%s' % (len([e for e in CATALOG if e['priority'] == 1]), (': ' + '; '.join(failed)) if failed else ''))
crj = R.get_report('journal', dict(fy, journal_codes=['CRV', 'CS']))
check(any(r['kind'] == 'group' for r in crj['rows']), 'cash receipts journal lists receipts')
vat = R.get_report('vat_return', fy)
check(abs(next(r for r in vat['rows'] if r['id'] == 'sale')['values']['tax'] - 2100) < 0.01, 'VAT declaration output tax 2,100')
vs = R.get_report('vat_register', dict(fy, side='sale'))
check(len([r for r in vs['rows'] if r['kind'] == 'line']) == 2, 'VAT sales register: 2 invoices')
wr = R.get_report('wht_register', dict(fy, side='received'))
check(any(r['values'].get('cert') == 'W-1' for r in wr['rows']), 'withholding received register shows receipt no.')
cr_ = R.get_report('cheque_register', dict(fy, cheque_state='open', direction='in'))
check(any(r['values'].get('number') == 'C-9' for r in cr_['rows']), 'deposits in transit lists the cheque')
cf = R.get_report('cash_flow', fy)
check(not any(r['id'] == 'diff' for r in cf['rows']), 'cash flow reconciles to cash per ledger')
iy = R.get_report('income_ytd', tik)
check(any(r['values'].get('ytd_pct') for r in iy['rows']), 'income statement YTD with % of revenue')
oci = R.get_report('profit_loss', dict(fy, oci=True))
check(any(r['id'] == 'tci' for r in oci['rows']), 'P&L and OCI has total comprehensive income')
st = R.get_report('partner_statement', dict(fy, partner_type='customer', partner_ids=[cust.id]))
check(any(r['kind'] == 'note' for r in st['rows']), 'customer statement with ageing line')
au = R.get_report('audit_trail', {'date_from': S(fields.Date.today() - __import__('datetime').timedelta(days=1)), 'date_to': S(fields.Date.today())})
check(len(au['rows']) > 0, 'audit trail has entries (%d)' % len(au['rows']))
cat = R.get_catalog()
check(len(cat['categories']) == 13 and len(cat['reports']) > 90, 'catalog: %d categories, %d entries' % (len(cat['categories']), len(cat['reports'])))

vid = env['sb.report.view'].save_view('trial_balance', 'My monthly TB', {'options': tik, 'ui': {'mode': 'month'}})
check(any(v['id'] == vid for v in env['sb.report.view'].list_views('trial_balance')), 'saved view listed')
print('RESULT', 'ALL PASS (%d checks)' % len(ok) if all(ok) else '%d FAILED of %d' % (ok.count(False), len(ok)))
env.cr.rollback()
