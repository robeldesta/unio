"""Fixed-asset reports on the report engine: asset register, depreciation schedule, PPE movement (IAS 16.73)."""
from collections import defaultdict

from odoo import models

from odoo.addons.sonador_books.models.ethiopian_calendar import format_ethiopian


class SbReportAssets(models.AbstractModel):
    _inherit = 'sb.report'

    def _assets(self, dt):
        return self.env['sb.asset'].search([('company_id', '=', self.env.company.id), ('state', '!=', 'draft'), ('date_acquired', '<=', dt)],
                                           order='category_id, code')

    def _dep_to(self, a, dt):
        posted = a.line_ids.filtered(lambda l: l.voucher_id and l.voucher_id.state == 'posted' and l.date <= dt)
        return a.opening_depreciation + sum(posted.mapped('amount'))

    def _report_asset_register(self, opts):
        dt = self._d(opts['date_to'])
        rows, cat_tot, grand = [], defaultdict(lambda: defaultdict(float)), defaultdict(float)
        cats = []
        for a in self._assets(dt):
            if not self._search_ok(opts, a.code, a.name, a.serial, a.location):
                continue
            gone = a.state == 'disposed' and a.date_disposed and a.date_disposed <= dt
            if gone:
                continue
            dep = self._dep_to(a, dt)
            vals = {'code': a.code, 'name': a.name, 'acquired': str(a.date_acquired), 'method': dict(a._fields['method'].selection)[a.method],
                    'life': a.life_months or '', 'cost': a.cost, 'dep': dep, 'nbv': a.cost - dep, 'where': a.location or a.custodian_id.name or ''}
            key = 'c%d' % a.category_id.id
            if key not in cats:
                cats.append(key)
                rows.append({'id': key, 'parent': None, 'level': 0, 'kind': 'group', 'label': a.category_id.name, 'values': {'code': a.category_id.name}})
            rows.append({'id': 'a%d' % a.id, 'parent': key, 'level': 1, 'kind': 'line', 'label': a.name, 'values': vals,
                         'open': {'model': 'sb.asset', 'id': a.id}})
            for k in ('cost', 'dep', 'nbv'):
                cat_tot[key][k] += vals[k]
                grand[k] += vals[k]
        for r in rows:
            if r['kind'] == 'group':
                r['values'].update(cat_tot[r['id']])
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total', 'values': dict(grand, code='Total')})
        cols = [{'key': 'code', 'label': 'Asset', 'type': 'text'}, {'key': 'name', 'label': 'Description', 'type': 'text'},
                {'key': 'acquired', 'label': 'Acquired', 'type': 'date'}, {'key': 'method', 'label': 'Method', 'type': 'text'},
                {'key': 'life', 'label': 'Life (months)', 'type': 'text'}, {'key': 'where', 'label': 'Location / held by', 'type': 'text'},
                {'key': 'cost', 'label': 'Cost', 'type': 'monetary'}, {'key': 'dep', 'label': 'Accumulated depreciation', 'type': 'monetary'},
                {'key': 'nbv', 'label': 'Carrying amount', 'type': 'monetary'}]
        return self._doc('asset_register', opts, cols, rows, 'As at %s' % format_ethiopian(dt), 'Asset Register')

    def _report_depreciation(self, opts):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        lines = self.env['sb.asset.dep'].search([('company_id', '=', self.env.company.id), ('date', '>=', df), ('date', '<=', dt),
                                                 ('voucher_id.state', '=', 'posted')], order='asset_id, date')
        months = sorted(set(lines.mapped('date')))
        by = defaultdict(lambda: defaultdict(float))
        for l in lines:
            by[l.asset_id][l.date] += l.amount
        rows, grand = [], defaultdict(float)
        for a, per in sorted(by.items(), key=lambda x: x[0].code):
            vals = {'code': a.code, 'name': a.name, 'total': sum(per.values())}
            for m in months:
                vals['m%s' % m.strftime('%Y%m')] = per.get(m, 0.0)
                grand['m%s' % m.strftime('%Y%m')] += per.get(m, 0.0)
            grand['total'] += vals['total']
            rows.append({'id': 'a%d' % a.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': a.name, 'values': vals,
                         'open': {'model': 'sb.asset', 'id': a.id}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total', 'values': dict(grand, code='Total')})
        cols = [{'key': 'code', 'label': 'Asset', 'type': 'text'}, {'key': 'name', 'label': 'Description', 'type': 'text'}] + \
            [{'key': 'm%s' % m.strftime('%Y%m'), 'label': format_ethiopian(m).split(' ')[1] if ' ' in format_ethiopian(m) else str(m),
              'type': 'monetary'} for m in months] + [{'key': 'total', 'label': 'Total', 'type': 'monetary'}]
        return self._doc('depreciation', opts, cols, rows, '%s - %s' % (format_ethiopian(df), format_ethiopian(dt)), 'Depreciation Schedule')

    def _report_ppe_movement(self, opts):
        """IAS 16.73(e): reconciliation of the carrying amount at the beginning and end of the period, per class."""
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        A = self.env['sb.asset'].search([('company_id', '=', self.env.company.id), ('state', '!=', 'draft')])
        tot = defaultdict(lambda: defaultdict(float))
        for a in A:
            k = a.category_id.name
            before = a.date_acquired < df
            gone_before = a.state == 'disposed' and a.date_disposed and a.date_disposed < df
            gone_in = a.state == 'disposed' and a.date_disposed and df <= a.date_disposed <= dt
            if gone_before:
                continue
            dep_open = a.opening_depreciation + sum(a.line_ids.filtered(lambda l: l.voucher_id.state == 'posted' and l.date < df).mapped('amount')) if before else (
                a.opening_depreciation if a.date_acquired >= df else 0.0)
            charge = sum(a.line_ids.filtered(lambda l: l.voucher_id.state == 'posted' and df <= l.date <= dt).mapped('amount'))
            if before:
                tot[k]['cost_open'] += a.cost
                tot[k]['dep_open'] += dep_open
            elif a.date_acquired <= dt:
                tot[k]['additions'] += a.cost
                tot[k]['dep_open'] += 0.0
                charge += a.opening_depreciation if a.date_acquired >= df else 0.0
            else:
                continue
            tot[k]['charge'] += charge
            if gone_in:
                tot[k]['disposals'] += a.cost
                tot[k]['dep_disposals'] += (dep_open + charge)
        rows, grand = [], defaultdict(float)
        for k, v in sorted(tot.items()):
            vals = dict(v)
            vals['cost_close'] = v['cost_open'] + v['additions'] - v['disposals']
            vals['dep_close'] = v['dep_open'] + v['charge'] - v['dep_disposals']
            vals['nbv_open'] = v['cost_open'] - v['dep_open']
            vals['nbv_close'] = vals['cost_close'] - vals['dep_close']
            vals['cls'] = k
            for kk, x in vals.items():
                if kk != 'cls':
                    grand[kk] += x
            rows.append({'id': 'c_%s' % k, 'parent': None, 'level': 0, 'kind': 'line', 'label': k, 'values': vals})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total', 'values': dict(grand, cls='Total')})
        cols = [{'key': 'cls', 'label': 'Class', 'type': 'text'}, {'key': 'cost_open', 'label': 'Cost, opening', 'type': 'monetary'},
                {'key': 'additions', 'label': 'Additions', 'type': 'monetary'}, {'key': 'disposals', 'label': 'Disposals', 'type': 'monetary'},
                {'key': 'cost_close', 'label': 'Cost, closing', 'type': 'monetary'}, {'key': 'dep_open', 'label': 'Depreciation, opening', 'type': 'monetary'},
                {'key': 'charge', 'label': 'Charge for the period', 'type': 'monetary'}, {'key': 'dep_disposals', 'label': 'On disposals', 'type': 'monetary'},
                {'key': 'dep_close', 'label': 'Depreciation, closing', 'type': 'monetary'}, {'key': 'nbv_open', 'label': 'Carrying amount, opening', 'type': 'monetary'},
                {'key': 'nbv_close', 'label': 'Carrying amount, closing', 'type': 'monetary'}]
        return self._doc('ppe_movement', opts, cols, rows, '%s - %s' % (format_ethiopian(df), format_ethiopian(dt)),
                         'Property, Plant and Equipment Movement')

    def _report_asset_control(self, opts):
        """The fixed-asset accounts in the ledger (control accounts) against the register (subsidiary ledger)."""
        dt = self._d(opts['date_to'])
        if 'sb.assets' not in self.env:
            return self._doc('asset_control', opts, [], [], '', 'Fixed Assets Control')
        data = self.env['sb.assets'].control(str(dt))
        kinds = {'cost': 'Cost', 'dep': 'Accumulated depreciation', 'expensed': 'Expensed when bought'}
        rows = []
        for i, r in enumerate(data['rows']):
            rows.append({'id': 'r%d' % i, 'parent': None, 'level': 0, 'kind': 'line', 'label': r['account'],
                         'values': {'account': r['account'], 'kind': kinds[r['kind']], 'class': r['label'], 'ledger': r['ledger'],
                                    'register': r['register'], 'diff': r['diff']}})
        if data['unlinked'] or data['outside']:
            rows.append({'id': 'u', 'parent': None, 'level': 0, 'kind': 'group', 'label': 'Not on the register',
                         'values': {'account': 'Ledger lines no asset accounts for'}})
            for i, u in enumerate(data['unlinked']):
                rows.append({'id': 'u%d' % i, 'parent': 'u', 'level': 1, 'kind': 'line', 'label': u['voucher'],
                             'values': {'account': u['account'], 'kind': u['voucher'], 'class': '%s %s' % (u['date'], u['name']),
                                        'ledger': u['amount']}, 'open': {'model': 'sb.voucher', 'id': u['voucher_id']}})
            for i, u in enumerate(data['outside']):
                rows.append({'id': 'o%d' % i, 'parent': 'u', 'level': 1, 'kind': 'line', 'label': u['move'],
                             'values': {'account': u['account'], 'kind': u['move'], 'class': '%s %s' % (u['date'], u['name']), 'ledger': u['amount']}})
        cols = [{'key': 'account', 'label': 'Account', 'type': 'text'}, {'key': 'kind', 'label': 'Holds', 'type': 'text'},
                {'key': 'class', 'label': 'Class', 'type': 'text'}, {'key': 'ledger', 'label': 'Ledger', 'type': 'monetary'},
                {'key': 'register', 'label': 'Register', 'type': 'monetary'}, {'key': 'diff', 'label': 'Difference', 'type': 'monetary'}]
        return self._doc('asset_control', opts, cols, rows, 'As at %s' % format_ethiopian(dt), 'Fixed Assets Control')

    def _report_asset_book_tax(self, opts):
        dt = self._d(opts['date_to'])
        data = self.env['sb.assets'].book_vs_tax(str(dt))
        rows, cats = [], {}
        for r in data['rows']:
            key = 'c' + r['category']
            if key not in cats:
                cats[key] = {'book': 0.0, 'tax': 0.0, 'diff': 0.0, 'deferred': 0.0}
                rows.append({'id': key, 'parent': None, 'level': 0, 'kind': 'group', 'label': r['category'], 'values': {'code': r['category']}})
            rows.append({'id': 'a%d' % r['id'], 'parent': key, 'level': 1, 'kind': 'line', 'label': r['name'],
                         'values': {'code': r['code'], 'name': r['name'], 'basis': r['tax_basis'], 'book': r['book'], 'tax': r['tax'],
                                    'diff': r['diff'], 'deferred': r['deferred']}, 'open': {'model': 'sb.asset', 'id': r['id']}})
            for k in ('book', 'tax', 'diff', 'deferred'):
                cats[key][k] += r[k]
        for r in rows:
            if r['kind'] == 'group':
                r['values'].update(cats[r['id']])
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total', 'values': dict(data['totals'], code='Total')})
        rows.append({'id': 'booked', 'parent': None, 'level': 0, 'kind': 'line', 'label': 'In the ledger',
                     'values': {'code': 'Deferred tax in the ledger', 'deferred': data['booked']}})
        rows.append({'id': 'adjust', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'To post',
                     'values': {'code': 'Adjustment to post', 'deferred': data['adjust']}})
        cols = [{'key': 'code', 'label': 'Asset', 'type': 'text'}, {'key': 'name', 'label': 'Description', 'type': 'text'},
                {'key': 'basis', 'label': 'Tax rate', 'type': 'text'}, {'key': 'book', 'label': 'Carrying amount (books)', 'type': 'monetary'},
                {'key': 'tax', 'label': 'Tax written-down value', 'type': 'monetary'}, {'key': 'diff', 'label': 'Difference', 'type': 'monetary'},
                {'key': 'deferred', 'label': 'Deferred tax at %s%%' % data['rate'], 'type': 'monetary'}]
        return self._doc('asset_book_tax', opts, cols, rows, 'As at %s' % format_ethiopian(dt), 'Fixed Assets: Books vs Tax')
