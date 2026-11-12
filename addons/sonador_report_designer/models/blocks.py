"""The report blocks: each a small table or a few figures for the report's period, with the period before beside it,
read from the records already in Unio (posted vouchers, store documents, van trips, requests, attendance, planning).
A block runs as the person writing the report: what their role cannot see in Unio, their report cannot show."""
import re
from collections import defaultdict
from datetime import datetime, time, timedelta

from odoo import fields, models
from odoo.exceptions import AccessError

from odoo.addons.sonador_books.models.ethiopian_calendar import format_ethiopian

SALES = ('sale', 'cash_sale')
LITRES = re.compile(r'(\d+(?:\.\d+)?)\s*(?:L\b|Lit(?:er|re)s?)', re.I)

# code: (title, department, function name)
BLOCKS = {}


def block(code, title, department, about=''):
    def wrap(fn):
        BLOCKS[code] = {'code': code, 'title': title, 'department': department, 'about': about, 'fn': fn.__name__}
        return fn
    return wrap


DEPARTMENTS = [('sales', 'Sales'), ('marketing', 'Marketing'), ('finance', 'Finance'), ('store', 'Store and production'),
               ('purchase', 'Purchase'), ('people', 'People'), ('every', 'Every report')]


def money(v):
    return round(v or 0.0, 2)


def pct(a, b):
    return round(100.0 * (a - b) / b, 1) if b else None


class SbRepBlocks(models.AbstractModel):
    _name = 'sb.rep.blocks'
    _description = 'Report blocks'

    # ------------------------------------------------------------------ running a block
    def catalog(self):
        return [{'code': b['code'], 'title': b['title'], 'department': b['department'], 'about': b['about']}
                for b in BLOCKS.values() if self._available(b['code'])]

    def _available(self, code):
        if code == 'van_trips':
            return 'sb.van.trip' in self.env
        if code in ('team_usage',):
            return 'ecg.usage.score' in self.env
        if code.startswith('fleet_'):
            return 'sb.fleet' in self.env
        if code in ('plan_board', 'plan_differences'):
            return 'sb.plan.score' in self.env
        return True

    def run(self, code, date_from, date_to, employee, config=None, kind=None):
        b = BLOCKS.get(code)
        if not b or not self._available(code):
            return {'code': code, 'title': code, 'error': self.env._('This block is not available here.')}
        days = (date_to - date_from).days + 1
        ctx = {'from': date_from, 'to': date_to, 'pfrom': date_from - timedelta(days=days), 'pto': date_from - timedelta(days=1),
               'employee': employee, 'config': config or {}, 'company': self.env.company, 'kind': kind}
        try:
            with self.env.cr.savepoint():
                out = getattr(self, b['fn'])(ctx)
        except AccessError:
            out = {'error': self.env._('Your role cannot see these records.')}
        out.update(code=code, title=b['title'], department=b['department'])
        return out

    # ------------------------------------------------------------------ helpers
    def _vouchers(self, types, a, b, extra=None):
        dom = [('voucher_type', 'in', types), ('state', '=', 'posted'), ('date', '>=', a), ('date', '<=', b),
               ('company_id', '=', self.env.company.id)] + (extra or [])
        return self.env['sb.voucher'].search(dom)

    def _lines(self, vouchers):
        return vouchers.mapped('line_ids').filtered('product_id')

    def _litres(self, line):
        m = LITRES.search(line.product_id.name or '')
        return float(m.group(1)) * line.quantity if m else 0.0

    def _salesperson(self, v):
        if v.sb_van_trip_id and v.sb_van_trip_id.salesperson_id:
            return v.sb_van_trip_id.salesperson_id.name
        if v.sb_sale_order_id and v.sb_sale_order_id.user_id:
            return v.sb_sale_order_id.user_id.name
        return (v.partner_id.sb_owner_id.name if 'sb_owner_id' in v.partner_id._fields else '') or v.create_uid.name or ''

    def _region(self, partner):
        for f in ('sb_region', 'sb_zone'):
            if f in partner._fields and partner[f]:
                return partner[f]
        return partner.state_id.name or partner.city or '-'

    def _sales_kind(self, v):
        if v.sb_van_trip_id:
            return self.env._('Van sales')
        return self.env._('Cash sales') if v.voucher_type == 'cash_sale' else self.env._('Credit sales')

    def _team(self, ctx):
        e = ctx['employee']
        return self.env['hr.employee'].sudo().search([('id', 'child_of', e.id), ('id', '!=', e.id)]) or e

    # ------------------------------------------------------------------ sales
    @block('sales_summary', 'Sales this period', 'sales', 'Revenue (before VAT), litres and invoices, against the period before.')
    def _b_sales_summary(self, ctx):
        now = self._vouchers(SALES, ctx['from'], ctx['to'])
        prev = self._vouchers(SALES, ctx['pfrom'], ctx['pto'])
        back = self._vouchers(('sale_return',), ctx['from'], ctx['to'])
        rev, prev_rev = sum(now.mapped('amount_untaxed')) - sum(back.mapped('amount_untaxed')), sum(prev.mapped('amount_untaxed'))
        lit, prev_lit = sum(self._litres(l) for l in self._lines(now)), sum(self._litres(l) for l in self._lines(prev))
        return {'kpis': [{'label': self.env._('Revenue'), 'value': money(rev), 'prev': money(prev_rev), 'change': pct(rev, prev_rev), 'money': True},
                         {'label': self.env._('Litres'), 'value': round(lit), 'prev': round(prev_lit), 'change': pct(lit, prev_lit)},
                         {'label': self.env._('Invoices and cash sales'), 'value': len(now), 'prev': len(prev), 'change': pct(len(now), len(prev))},
                         {'label': self.env._('Returns'), 'value': money(sum(back.mapped('amount_untaxed'))), 'money': True}]}

    @block('sales_by_product', 'Sales by product', 'sales', 'Quantity, litres and revenue per product.')
    def _b_sales_by_product(self, ctx):
        acc = defaultdict(lambda: [0.0, 0.0, 0.0])
        for l in self._lines(self._vouchers(SALES, ctx['from'], ctx['to'])):
            a = acc[l.product_id.display_name]
            a[0] += l.quantity
            a[1] += self._litres(l)
            a[2] += l.price_subtotal
        rows = sorted(([k, round(v[0]), round(v[1]), money(v[2])] for k, v in acc.items()), key=lambda r: -r[3])
        return {'table': {'columns': [self.env._('Product'), self.env._('Quantity'), self.env._('Litres'), self.env._('Revenue')],
                          'money': [3], 'rows': rows, 'total': ['', sum(r[1] for r in rows), sum(r[2] for r in rows), money(sum(r[3] for r in rows))]}}

    @block('sales_by_type', 'Sales by type', 'sales', 'Cash sales, credit sales and van sales.')
    def _b_sales_by_type(self, ctx):
        acc = defaultdict(lambda: [0, 0.0, 0.0])
        for v in self._vouchers(SALES, ctx['from'], ctx['to']):
            a = acc[self._sales_kind(v)]
            a[0] += 1
            a[1] += sum(self._litres(l) for l in v.line_ids.filtered('product_id'))
            a[2] += v.amount_untaxed
        total = sum(a[2] for a in acc.values()) or 1.0
        rows = [[k, v[0], round(v[1]), money(v[2]), round(100 * v[2] / total)] for k, v in sorted(acc.items(), key=lambda x: -x[1][2])]
        return {'table': {'columns': [self.env._('Type'), self.env._('Documents'), self.env._('Litres'), self.env._('Revenue'), '%'],
                          'money': [3], 'rows': rows}}

    @block('sales_by_person', 'Who sold where', 'sales', 'Revenue and litres per salesperson and region.')
    def _b_sales_by_person(self, ctx):
        acc = defaultdict(lambda: [0.0, 0.0])
        for v in self._vouchers(SALES, ctx['from'], ctx['to']):
            a = acc[(self._salesperson(v), self._region(v.partner_id))]
            a[0] += sum(self._litres(l) for l in v.line_ids.filtered('product_id'))
            a[1] += v.amount_untaxed
        rows = sorted(([k[0], k[1], round(v[0]), money(v[1])] for k, v in acc.items()), key=lambda r: -r[3])
        return {'table': {'columns': [self.env._('Salesperson'), self.env._('Region'), self.env._('Litres'), self.env._('Revenue')],
                          'money': [3], 'rows': rows[:40]}}

    @block('top_customers', 'Top 10 customers', 'sales', 'The customers who bought most in the period.')
    def _b_top_customers(self, ctx):
        acc = defaultdict(float)
        for v in self._vouchers(SALES, ctx['from'], ctx['to']):
            acc[v.partner_id.display_name or '-'] += v.amount_untaxed
        rows = sorted(([k, money(v)] for k, v in acc.items()), key=lambda r: -r[1])[:10]
        return {'table': {'columns': [self.env._('Customer'), self.env._('Revenue')], 'money': [1], 'rows': rows}}

    @block('collections', 'Collections on credit sales', 'sales', 'Receipts (CRV) applied to credit invoices, per customer.')
    def _b_collections(self, ctx):
        acc = defaultdict(float)
        for v in self._vouchers(('receipt',), ctx['from'], ctx['to']):
            if v.amount_applied:
                acc[v.partner_id.display_name or '-'] += v.amount_applied
        billed = sum(self._vouchers(('sale',), ctx['from'], ctx['to']).mapped('amount_total'))
        rows = sorted(([k, money(v)] for k, v in acc.items()), key=lambda r: -r[1])
        got = sum(r[1] for r in rows)
        return {'kpis': [{'label': self.env._('Collected'), 'value': money(got), 'money': True},
                         {'label': self.env._('Credit sales billed'), 'value': money(billed), 'money': True}],
                'table': {'columns': [self.env._('Customer'), self.env._('Collected')], 'money': [1], 'rows': rows[:30]}}

    @block('receivables_ageing', 'Customers owing', 'sales', 'What customers owe at the end of the period, by age.')
    def _b_receivables(self, ctx):
        Line = self.env['account.move.line']
        lines = Line.search([('account_id.account_type', '=', 'asset_receivable'), ('parent_state', '=', 'posted'),
                             ('date', '<=', ctx['to']), ('company_id', '=', self.env.company.id), ('amount_residual', '!=', 0)])
        acc = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
        for l in lines:
            age = (ctx['to'] - (l.date_maturity or l.date)).days
            i = 0 if age <= 30 else 1 if age <= 60 else 2 if age <= 90 else 3
            acc[l.partner_id.display_name or '-'][i] += l.amount_residual
        rows = sorted(([k] + [money(x) for x in v] + [money(sum(v))] for k, v in acc.items()), key=lambda r: -r[5])
        rows = [r for r in rows if abs(r[5]) >= 0.01]
        return {'table': {'columns': [self.env._('Customer'), '0-30', '31-60', '61-90', '90+', self.env._('Total')],
                          'money': [1, 2, 3, 4, 5], 'rows': rows[:25],
                          'total': [self.env._('All customers')] + [money(sum(r[i] for r in rows)) for i in range(1, 6)]}}

    @block('van_trips', 'Van trips', 'sales', 'Trips in the period: loaded, sold, returned, and what each salesperson owes.')
    def _b_van_trips(self, ctx):
        trips = self.env['sb.van.trip'].search([('date', '>=', ctx['from']), ('date', '<=', ctx['to']),
                                                ('company_id', '=', self.env.company.id), ('state', '!=', 'draft')])
        rows = []
        for t in trips:
            rows.append([t.name, t.salesperson_id.name or '', t.van_id.display_name or '', money(getattr(t, 'loaded_value', 0)),
                         money(getattr(t, 'sold_value', 0)), money(getattr(t, 'returned_value', 0)), money(getattr(t, 'salesperson_owes', 0))])
        return {'table': {'columns': [self.env._('Trip'), self.env._('Salesperson'), self.env._('Van'), self.env._('Loaded'),
                                      self.env._('Sold'), self.env._('Returned'), self.env._('Owes')],
                          'money': [3, 4, 5, 6], 'rows': rows}}

    # ------------------------------------------------------------------ marketing
    @block('marketing_expenses', 'Marketing expenses', 'marketing',
           'Expenses of the marketing accounts by group; payroll accounts left out. The groups are set in the template.')
    def _b_marketing(self, ctx):
        groups = (ctx['config'] or {}).get('marketing_groups') or []
        Line = self.env['account.move.line']
        rows, total, prev_total = [], 0.0, 0.0
        for g in groups:
            accs = self.env['account.account'].search([('code', 'in', g.get('codes') or []), ('company_ids', 'in', self.env.company.id)])
            def bal(a, b):
                return sum(Line.search([('account_id', 'in', accs.ids), ('parent_state', '=', 'posted'), ('date', '>=', a),
                                        ('date', '<=', b)]).mapped('balance'))
            now, prev = bal(ctx['from'], ctx['to']), bal(ctx['pfrom'], ctx['pto'])
            total += now
            prev_total += prev
            rows.append([g.get('name'), money(now), money(prev), pct(now, prev)])
        note = ''
        if (ctx['config'] or {}).get('marketing_left_out'):
            note = self.env._('Left out: %s', ', '.join(ctx['config']['marketing_left_out']))
        return {'note': note, 'table': {'columns': [self.env._('Group'), self.env._('This period'), self.env._('Period before'), '%'],
                                        'money': [1, 2], 'rows': rows, 'total': [self.env._('Total'), money(total), money(prev_total), pct(total, prev_total)]}}

    # ------------------------------------------------------------------ finance
    @block('cash_bank', 'Cash and bank', 'finance', 'Each bank and cash account: opening, in, out, closing.')
    def _b_cash_bank(self, ctx):
        Line = self.env['account.move.line']
        accs = self.env['account.account'].search([('account_type', '=', 'asset_cash'), ('company_ids', 'in', self.env.company.id)])
        rows = []
        for a in accs:
            base = [('account_id', '=', a.id), ('parent_state', '=', 'posted')]
            opening = sum(Line.search(base + [('date', '<', ctx['from'])]).mapped('balance'))
            period = Line.search(base + [('date', '>=', ctx['from']), ('date', '<=', ctx['to'])])
            inn, out = sum(period.mapped('debit')), sum(period.mapped('credit'))
            if opening or inn or out:
                rows.append([a.display_name, money(opening), money(inn), money(out), money(opening + inn - out)])
        return {'table': {'columns': [self.env._('Account'), self.env._('Opening'), self.env._('In'), self.env._('Out'), self.env._('Closing')],
                          'money': [1, 2, 3, 4], 'rows': rows,
                          'total': [self.env._('All')] + [money(sum(r[i] for r in rows)) for i in range(1, 5)]}}

    @block('payments_by_purpose', 'Payments made', 'finance', 'Payment vouchers (CPV) by expense account.')
    def _b_payments(self, ctx):
        acc = defaultdict(float)
        for l in self._vouchers(('payment', 'cash_purchase'), ctx['from'], ctx['to']).mapped('line_ids'):
            acc[l.account_id.display_name or '-'] += l.amount or l.price_subtotal or 0.0
        rows = sorted(([k, money(v)] for k, v in acc.items() if v), key=lambda r: -r[1])
        return {'table': {'columns': [self.env._('Paid for'), self.env._('Amount')], 'money': [1], 'rows': rows[:30],
                          'total': [self.env._('Total'), money(sum(r[1] for r in rows))]}}

    @block('requests_waiting', 'Requests waiting', 'finance', 'Payment, petty cash and per diem requests not yet decided, with their age.')
    def _b_requests(self, ctx):
        if 'sb.payment.request' not in self.env:
            return {'error': self.env._('Requests are not installed.')}
        reqs = self.env['sb.payment.request'].search([('state', 'in', ('dept', 'finance', 'gm')), ('company_id', '=', self.env.company.id)])
        today = fields.Date.context_today(self)
        rows = [[r.name, r.requester_id.name or '', dict(r._fields['state'].selection).get(r.state), money(r.amount),
                 (today - (r.submitted_at.date() if r.submitted_at else r.create_date.date())).days] for r in reqs]
        rows.sort(key=lambda r: -r[4])
        return {'table': {'columns': [self.env._('Request'), self.env._('By'), self.env._('Waiting for'), self.env._('Amount'), self.env._('Days')],
                          'money': [3], 'rows': rows[:30]}}

    @block('vat_wht', 'VAT and withholding', 'finance', 'Output and input VAT, withholding deducted and suffered.')
    def _b_vat(self, ctx):
        Line = self.env['account.move.line']
        base = [('parent_state', '=', 'posted'), ('date', '>=', ctx['from']), ('date', '<=', ctx['to']), ('company_id', '=', self.env.company.id)]
        out = -sum(Line.search(base + [('tax_line_id.type_tax_use', '=', 'sale')]).mapped('balance'))
        inn = sum(Line.search(base + [('tax_line_id.type_tax_use', '=', 'purchase')]).mapped('balance'))
        wht = sum(self._vouchers(('payment', 'cash_purchase'), ctx['from'], ctx['to']).mapped('wht_amount'))
        wht_in = sum(self._vouchers(('receipt', 'cash_sale'), ctx['from'], ctx['to']).mapped('wht_amount'))
        return {'kpis': [{'label': self.env._('VAT on sales'), 'value': money(out), 'money': True},
                         {'label': self.env._('VAT on purchases'), 'value': money(inn), 'money': True},
                         {'label': self.env._('VAT payable'), 'value': money(out - inn), 'money': True},
                         {'label': self.env._('Withholding deducted'), 'value': money(wht), 'money': True},
                         {'label': self.env._('Withholding suffered'), 'value': money(wht_in), 'money': True}]}

    # ------------------------------------------------------------------ store and production
    def _stock_docs(self, types, ctx):
        return self.env['sb.stock.voucher'].search([('voucher_type', 'in', types), ('state', '=', 'posted'), ('date', '>=', ctx['from']),
                                                    ('date', '<=', ctx['to']), ('company_id', '=', self.env.company.id)])

    @block('stock_movements', 'Stock in and out', 'store', 'Received (GRN), issued (GIV) and produced (FPRR) by item class.')
    def _b_stock(self, ctx):
        acc = defaultdict(lambda: [0.0, 0.0, 0.0])
        for i, types in enumerate((('grn',), ('giv',), ('fprr', 'build'))):
            for l in self._stock_docs(types, ctx).mapped('line_ids').filtered('product_id'):
                acc[l.product_id.categ_id.name or '-'][i] += l.value or 0.0
        rows = sorted(([k, money(v[0]), money(v[1]), money(v[2])] for k, v in acc.items()), key=lambda r: r[0])
        return {'table': {'columns': [self.env._('Item class'), self.env._('Received'), self.env._('Issued'), self.env._('Produced')],
                          'money': [1, 2, 3], 'rows': rows}}

    @block('stock_low', 'Items to reorder', 'store', 'Stock items at or below their reorder point.')
    def _b_stock_low(self, ctx):
        items = self.env['product.template'].search([('is_storable', '=', True), ('sb_reorder_point', '>', 0)])
        rows = [[t.display_name, round(t.qty_available, 2), t.sb_reorder_point, t.sb_reorder_qty] for t in items if t.qty_available <= t.sb_reorder_point]
        return {'table': {'columns': [self.env._('Item'), self.env._('On hand'), self.env._('Reorder at'), self.env._('Order')], 'rows': rows}}

    @block('production', 'Production', 'store', 'Finished goods received from production, per product, and work in process.')
    def _b_production(self, ctx):
        acc = defaultdict(lambda: [0.0, 0.0])
        for l in self._stock_docs(('fprr', 'build'), ctx).mapped('line_ids').filtered('product_id'):
            acc[l.product_id.display_name][0] += l.quantity
            acc[l.product_id.display_name][1] += self._litres(l)
        rows = sorted(([k, round(v[0]), round(v[1])] for k, v in acc.items()), key=lambda r: -r[2])
        kpis = [{'label': self.env._('Litres produced'), 'value': round(sum(r[2] for r in rows))}]
        # work in process only for those who see the books
        if self.env['account.move.line'].has_access('read'):
            wip = self.env['account.account'].search([('name', 'ilike', 'work in pro'), ('company_ids', 'in', self.env.company.id)], limit=1)
            bal = sum(self.env['account.move.line'].search([('account_id', '=', wip.id), ('parent_state', '=', 'posted'),
                                                            ('date', '<=', ctx['to'])]).mapped('balance')) if wip else 0.0
            kpis.append({'label': self.env._('Work in process'), 'value': money(bal), 'money': True})
        return {'kpis': kpis,
                'table': {'columns': [self.env._('Product'), self.env._('Quantity'), self.env._('Litres')], 'rows': rows}}

    @block('deliveries', 'Deliveries', 'store', 'Delivery orders by state, and how long loading to gate takes.')
    def _b_deliveries(self, ctx):
        if 'sb.delivery.order' not in self.env:
            return {'error': self.env._('Deliveries are not installed.')}
        dos = self.env['sb.delivery.order'].search([('date', '>=', ctx['from']), ('date', '<=', ctx['to']), ('company_id', '=', self.env.company.id)])
        acc = defaultdict(int)
        for d in dos:
            acc[dict(d._fields['state'].selection).get(d.state)] += 1
        waits = [(d.gate_time - d.loaded_time).total_seconds() / 3600 for d in dos if d.gate_time and d.loaded_time]
        return {'kpis': [{'label': self.env._('Delivery orders'), 'value': len(dos)},
                         {'label': self.env._('Hours loading to gate'), 'value': round(sum(waits) / len(waits), 1) if waits else '-'}],
                'table': {'columns': [self.env._('State'), self.env._('Orders')], 'rows': [[k, v] for k, v in acc.items()]}}

    # ------------------------------------------------------------------ purchase
    @block('po_status', 'Purchase orders', 'purchase', 'Orders of the period by state and supplier.')
    def _b_po(self, ctx):
        pos = self.env['purchase.order'].search([('date_order', '>=', ctx['from']), ('date_order', '<=', fields.Date.to_string(ctx['to']) + ' 23:59:59'),
                                                 ('company_id', '=', self.env.company.id)])
        sel = dict(self.env['purchase.order']._fields['state']._description_selection(self.env))
        rows = [[p.name, p.partner_id.name or '', sel.get(p.state, p.state), money(p.amount_total)] for p in pos]
        return {'table': {'columns': [self.env._('Order'), self.env._('Supplier'), self.env._('State'), self.env._('Amount')],
                          'money': [3], 'rows': rows, 'total': ['', '', '', money(sum(r[3] for r in rows))]}}

    @block('purchase_requests', 'Purchase requests waiting', 'purchase', 'Purchase requests not yet approved or ordered.')
    def _b_pr(self, ctx):
        if 'sb.purchase.request' not in self.env:
            return {'error': self.env._('Requests are not installed.')}
        reqs = self.env['sb.purchase.request'].search([('state', 'in', ('dept', 'finance', 'gm', 'approved')), ('company_id', '=', self.env.company.id)])
        sel = dict(reqs._fields['state'].selection)
        rows = [[r.name, r.requester_id.name or '', sel.get(r.state)] for r in reqs]
        return {'table': {'columns': [self.env._('Request'), self.env._('By'), self.env._('State')], 'rows': rows[:30]}}

    # ------------------------------------------------------------------ people
    @block('team_attendance', 'Team attendance', 'people', 'Days at work, late, absent and overtime of the people reporting to you.')
    def _b_attendance(self, ctx):
        if 'sb.att.day' not in self.env:
            return {'error': self.env._('Attendance is not installed.')}
        days = self.env['sb.att.day'].sudo().search([('employee_id', 'in', self._team(ctx).ids), ('date', '>=', ctx['from']), ('date', '<=', ctx['to'])])
        acc = defaultdict(lambda: [0, 0, 0, 0.0])
        for d in days:
            a = acc[d.employee_id.name]
            a[0] += d.status in ('present', 'late', 'missing')
            a[1] += bool(d.late)
            a[2] += d.status == 'absent'
            a[3] += (d.ot_total or 0) / 60.0 if d.ot_state == 'approved' else 0
        rows = sorted([k, v[0], v[1], v[2], round(v[3], 1)] for k, v in acc.items())
        return {'table': {'columns': [self.env._('Person'), self.env._('At work'), self.env._('Late'), self.env._('Absent'), self.env._('Overtime h')], 'rows': rows}}

    @block('team_leave', 'Team leave', 'people', 'Leave taken and planned in the period by the people reporting to you.')
    def _b_leave(self, ctx):
        if 'sb.leave' not in self.env:
            return {'error': self.env._('Leave Requests is not installed.')}
        leaves = self.env['sb.leave'].sudo().search([('employee_id', 'in', self._team(ctx).ids), ('state', 'in', ('approved', 'submitted')),
                                                     ('date_from', '<=', ctx['to']), ('date_to', '>=', ctx['from'])])
        rows = [[l.employee_id.name, l.type_id.name or '', fields.Date.to_string(l.date_from), fields.Date.to_string(l.date_to), l.days,
                 dict(l._fields['state'].selection).get(l.state)] for l in leaves]
        return {'table': {'columns': [self.env._('Person'), self.env._('Type'), self.env._('From'), self.env._('To'), self.env._('Days'), self.env._('State')], 'rows': rows}}

    @block('team_usage', 'Team system usage', 'people', 'The usage score of the people reporting to you.')
    def _b_usage(self, ctx):
        S = self.env['ecg.usage.score'].sudo()
        rows = []
        for e in self._team(ctx):
            s = S.search([('employee_id', '=', e.id), ('date', '<=', ctx['to'])], order='date desc', limit=1)
            if s:
                rows.append([e.name, s.total if s.state == 'scored' else '-', dict(s._fields['state'].selection).get(s.state)])
        return {'table': {'columns': [self.env._('Person'), self.env._('Score'), ''], 'rows': rows}}

    # ------------------------------------------------------------------ every report
    # ------------------------------------------------------------------ fleet (logistics)
    def _fleet(self):
        return self.env['sb.fleet'].sudo().with_company(self.env.company)

    def _fleet_vehicles(self):
        return self.env['sb.vehicle'].sudo().search([('company_id', '=', self.env.company.id), ('state', '!=', 'disposed')])

    @block('fleet_status', 'Vehicles', 'store',
           'Vehicles available, in the workshop or parked; Bolo, insurance and permits expiring within 30 days; driving licences.')
    def _b_fleet_status(self, ctx):
        F = self._fleet()
        vs = self._fleet_vehicles()
        own = vs.filtered(lambda v: v.ownership == 'owned')
        ready = own.filtered(lambda v: v.state == 'active')
        today = fields.Date.context_today(self)
        rows = []
        for (vid, kind), d in F._current_docs(vs).items():
            st = F._doc_status(d, 30)
            if st in ('expired', 'soon'):
                rows.append([d.vehicle_id.name, dict(d._fields['kind'].selection)[kind],
                             format_ethiopian(d.date_end) if d.date_end else '-', 'expired' if st == 'expired' else '%d days left' % (d.date_end - today).days])
        for v in own.filtered(lambda v: v.state != 'active'):
            rows.append([v.name, dict(v._fields['state'].selection)[v.state], '-', 'not available'])
        return {'kpis': [{'label': self.env._('Own vehicles available'), 'value': '%d of %d' % (len(ready), len(own))},
                         {'label': self.env._('Available'), 'value': '%d%%' % round(100.0 * len(ready) / len(own)) if own else '-'},
                         {'label': self.env._('Documents due'), 'value': len([r for r in rows if r[3] != 'not available'])}],
                'table': {'columns': [self.env._('Vehicle'), self.env._('What'), self.env._('Ends'), self.env._('Status')],
                          'rows': sorted(rows, key=lambda r: r[3])}}

    @block('fleet_fuel', 'Fuel', 'store',
           'Litres and birr per vehicle, km per litre against the vehicle\'s normal, and fills the fuel checker flagged.')
    def _b_fleet_fuel(self, ctx):
        F = self._fleet()
        vs = self._fleet_vehicles()
        econ = {e['id']: e for e in F._economy(vs, ctx['from'], ctx['to'])}
        cost = F._costs(vs, ctx['from'], ctx['to'])
        rows = []
        for r in sorted(cost['rows'], key=lambda r: -r['fuel']):
            if not r['liters'] and not r['fuel']:
                continue
            e = econ.get(r['id']) or {}
            kml = e.get('kml')
            rows.append([r['name'], r['liters'], money(r['fuel']), kml if kml is not None else '-', e.get('target') or '-',
                         'below normal' if e.get('status') == 'bad' else ''])
        flagged = self.env['sb.fuel'].sudo().search([('vehicle_id', 'in', vs.ids), ('state', '=', 'filled'), ('risk', 'in', ('alert', 'theft')),
                                                     ('date', '>=', datetime.combine(ctx['from'], time.min)),
                                                     ('date', '<=', datetime.combine(ctx['to'], time.max))])
        for f in flagged:
            rows.append(['  ⚠ %s' % f.vehicle_id.name, f.liters, money(f.amount), '-', '-', '; '.join((x.get('msg') or '') if isinstance(x, dict) else str(x) for x in (f.flags or []))[:120]])
        return {'kpis': [{'label': self.env._('Litres'), 'value': round(cost['totals']['liters'], 1)},
                         {'label': self.env._('Fuel cost'), 'value': money(cost['totals']['fuel']), 'money': True},
                         {'label': self.env._('Fills flagged'), 'value': len(flagged)}],
                'table': {'columns': [self.env._('Vehicle'), self.env._('Litres'), self.env._('Birr'), self.env._('km per litre'),
                                      self.env._('Normal'), self.env._('Note')], 'money': [2], 'rows': rows}}

    @block('fleet_maintenance', 'Maintenance', 'store',
           'Work orders of the period per vehicle (what, garage, cost, days in the workshop), vehicles still in the workshop, '
           'services due by km or date.')
    def _b_fleet_maintenance(self, ctx):
        F = self._fleet()
        vs = self._fleet_vehicles()
        S = self.env['sb.vehicle.service'].sudo()
        today = fields.Date.context_today(self)
        kinds = dict(S._fields['kind'].selection)
        rows = []
        done = S.search([('vehicle_id', 'in', vs.ids), ('state', '!=', 'planned'), ('date', '>=', ctx['from']), ('date', '<=', ctx['to'])], order='date')
        for s in done:
            days = ((s.date_done or today) - s.date).days + 1 if s.date else '-'
            rows.append([s.vehicle_id.name, kinds.get(s.kind, s.kind), (s.description or '')[:80], s.partner_id.name or '-', money(s.amount),
                         days, 'in the workshop' if s.state == 'open' else 'done'])
        for s in S.search([('vehicle_id', 'in', vs.ids), ('state', '=', 'open'), ('date', '<', ctx['from'])]):
            rows.append([s.vehicle_id.name, kinds.get(s.kind, s.kind), (s.description or '')[:80], s.partner_id.name or '-', money(s.amount),
                         (today - s.date).days + 1 if s.date else '-', 'still in the workshop'])
        due = [F._sched_due(x) for x in self.env['sb.vehicle.schedule'].sudo().search([('vehicle_id', 'in', vs.ids)])]
        for d in due:
            if d['status'] in ('overdue', 'soon'):
                rows.append([d['vehicle'], d['name'], 'next at %s km / %s' % (d['next_km'] or '-', d['next_ec'] or '-'), '-', '-', '-', d['status']])
        return {'kpis': [{'label': self.env._('Work orders'), 'value': len(done)},
                         {'label': self.env._('Maintenance cost'), 'value': money(sum(done.mapped('amount'))), 'money': True},
                         {'label': self.env._('In the workshop now'), 'value': S.search_count([('vehicle_id', 'in', vs.ids), ('state', '=', 'open')])}],
                'table': {'columns': [self.env._('Vehicle'), self.env._('Kind'), self.env._('What'), self.env._('Garage'), self.env._('Cost'),
                                      self.env._('Days'), self.env._('Status')], 'money': [4], 'rows': rows}}

    @block('fleet_costs', 'Trips and cost per vehicle', 'store',
           'Per vehicle: km, fuel, maintenance, documents, per diem and other costs, and the cost per km; trips of the period.')
    def _b_fleet_costs(self, ctx):
        F = self._fleet()
        vs = self._fleet_vehicles()
        cost = F._costs(vs, ctx['from'], ctx['to'])
        T = self.env['sb.trip'].sudo()
        trips = T.search([('vehicle_id', 'in', vs.ids), ('state', '!=', 'cancel'), ('out_at', '>=', datetime.combine(ctx['from'], time.min)),
                          ('out_at', '<=', datetime.combine(ctx['to'], time.max))])
        n = defaultdict(int)
        for t in trips:
            n[t.vehicle_id.id] += 1
        rows = [[r['name'], n[r['id']], r['km'], money(r['fuel']), money(r['maintenance']), money(r['documents'] + r['perdiem'] + r['other']),
                 money(r['total']), r['per_km'] if r['per_km'] is not None else '-']
                for r in sorted(cost['rows'], key=lambda r: -r['total']) if r['total'] or n[r['id']]]
        t = cost['totals']
        return {'kpis': [{'label': self.env._('Trips'), 'value': len(trips)}, {'label': self.env._('km'), 'value': t['km']},
                         {'label': self.env._('Vehicle cost'), 'value': money(t['total']), 'money': True},
                         {'label': self.env._('Cost per km'), 'value': t['per_km'] if t['per_km'] is not None else '-'}],
                'table': {'columns': [self.env._('Vehicle'), self.env._('Trips'), self.env._('km'), self.env._('Fuel'), self.env._('Maintenance'),
                                      self.env._('Other'), self.env._('Total'), self.env._('Per km')], 'money': [3, 4, 5, 6], 'rows': rows}}

    @block('plan_checkins', 'My plan and its score', 'every',
           "The period's plan lines from Planning (Must, Should, Could): target, actual and the plan score.")
    def _b_plan(self, ctx):
        e = ctx['employee']
        tasks = self.env['sb.plan.task'].sudo().search([('employee_id', '=', e.id), ('date_from', '<=', ctx['to']),
                                                        ('date_to', '>=', ctx['from'])])
        if 'sb.plan.score' in self.env:
            wk = self.env['sb.plan.score'].week(e, ctx['from'], ctx['to'], tasks)
            names = {'must': 'Must', 'should': 'Should', 'could': 'Could'}
            fmt = lambda v: '' if v in (None, '') else ('{:,.0f}' if abs(v) >= 100 or float(v).is_integer() else '{:,.2f}').format(v)  # noqa: E731
            rows = [[names[l['priority']] + (' (came up)' if l['unplanned'] else ''), l['name'],
                     ('%s %s' % (fmt(l['target']), l['unit'])).strip() if l['target'] else '-',
                     fmt(l['actual']) if l['actual'] is not None else {'done': 'Done', 'blocked': 'Blocked', 'on_track': 'On track'}.get(l['status'], '-'),
                     '%s%%%s' % (l['pct'], ' · missed: %s' % l['reason'] if l['missed'] and l['reason'] else (' · missed' if l['missed'] else ''))]
                    for l in wk['lines']]
            musts = [l for l in wk['lines'] if l['priority'] == 'must' and not l['unplanned']]
            return {'kpis': [{'label': self.env._('Plan score'), 'value': '-' if wk['score'] is None else '%s / 100' % wk['score']},
                             {'label': self.env._('Must done'), 'value': '%d of %d' % (len([l for l in musts if l['pct'] >= 100]), len(musts))},
                             {'label': self.env._('Came up'), 'value': wk['unplanned']}],
                    'table': {'columns': [self.env._('Priority'), self.env._('Line'), self.env._('Target'), self.env._('Actual'),
                                          self.env._('Achieved')], 'rows': rows}}
        C = self.env['sb.plan.checkin'].sudo()
        rows = []
        for t in tasks:
            last = C.search([('task_id', '=', t.id), ('date', '<=', ctx['to'])], order='date desc', limit=1)
            rows.append([t.name, t.done_when or '', '%s%%' % (last.progress if last else t.progress or 0),
                         {'done': 'Done', 'blocked': 'Blocked', 'on_track': 'On track'}.get(last.status, '-') if last else '-'])
        done = len([r for r in rows if r[3] == 'Done'])
        return {'kpis': [{'label': self.env._('Tasks'), 'value': len(rows)}, {'label': self.env._('Done'), 'value': done}],
                'table': {'columns': [self.env._('Task'), self.env._('Done when'), self.env._('Progress'), self.env._('Status')], 'rows': rows}}

    @block('plan_board', 'Plan scores by department', 'every',
           "Every department in the same shape: its plan score and KPIs, then what is off plan (Must missed, carried twice, "
           "KPIs off, reports late).")
    def _b_plan_board(self, ctx):
        if 'sb.plan.score' not in self.env:
            return {'text': self.env._('Planning is not installed.')}
        b = self.env['sb.planning'].board(fields.Date.to_string(ctx['from']))
        rows = []
        for d in b['departments']:
            rows.append([d['name'], '-' if d['score'] is None else d['score'],
                         '; '.join('%s %s%%' % (k['name'], k['pct']) for k in d['kpis']) or '-',
                         ', '.join('%s %s' % (p['name'], '-' if p['score'] is None else p['score']) for p in d['people'])])
        return {'kpis': [{'label': self.env._('Company plan score'), 'value': '-' if b['company'] is None else '%s / 100' % b['company']},
                         {'label': self.env._('Off plan'), 'value': len(b['exceptions'])}],
                'table': {'columns': [self.env._('Department'), self.env._('Score'), self.env._('KPIs'), self.env._('People')], 'rows': rows}}

    @block('plan_differences', 'Off plan', 'every',
           'What needs attention, worst first: Must lines missed, lines carried twice, department KPIs off, reports late or missing.')
    def _b_plan_differences(self, ctx):
        if 'sb.plan.score' not in self.env:
            return {'text': self.env._('Planning is not installed.')}
        b = self.env['sb.planning'].board(fields.Date.to_string(ctx['from']))
        rows = [[x['label'], x['dept'], x['who'] or '', x['text']] for x in b['exceptions'][:40]]
        return {'kpis': [{'label': self.env._('Off plan'), 'value': len(b['exceptions'])}],
                'table': {'columns': [self.env._('What'), self.env._('Department'), self.env._('Who'), self.env._('Detail')], 'rows': rows}}

    @block('reports_received', 'Reports from the team', 'every',
           'The reports of the period from the people you read: who sent, who was accepted, who has not sent yet.')
    def _b_reports(self, ctx):
        me = ctx['employee']
        Service = self.env['sb.rep.service']
        R = self.env['sb.rep.report'].sudo()
        dom = [('company_id', '=', self.env.company.id), ('employee_id', '!=', me.id), ('date_from', '<=', ctx['to']),
               ('date_to', '>=', ctx['from'])]
        if ctx.get('kind'):  # a weekly report reads the weekly reports, a monthly one the monthly ...
            dom.append(('period', '=', ctx['kind']))
        reports = R.search(dom, order='date_from, id')
        readable = reports.filtered(lambda r: self._sb_can_read_report(Service, r))
        names = dict(R._fields['state'].selection)
        rows = [[r.employee_id.name, r.template_id.name, Service._label(r), names.get(r.state, r.state) + (' (late)' if r.late else ''),
                 fields.Datetime.to_string(r.sent_at)[:10] if r.sent_at else ''] for r in readable]
        sent = readable.filtered(lambda r: r.state in ('sent', 'accepted'))
        return {'kpis': [{'label': self.env._('Reports'), 'value': len(readable)}, {'label': self.env._('Sent or accepted'), 'value': len(sent)},
                         {'label': self.env._('Not sent yet'), 'value': len(readable) - len(sent)}],
                'table': {'columns': [self.env._('Who'), self.env._('Report'), self.env._('Period'), self.env._('State'), self.env._('Sent')],
                          'rows': rows}}

    def _sb_can_read_report(self, Service, r):
        try:
            return Service._can_read(r)
        except AccessError:
            return False

    @block('approvals_decided', 'Approvals', 'every', 'What you decided in the period, and what still waits for you.')
    def _b_approvals(self, ctx):
        if 'sb.approval.log' not in self.env:
            return {'error': self.env._('Approvals are not installed.')}
        user = ctx['employee'].user_id
        logs = self.env['sb.approval.log'].sudo().search([('user_id', '=', user.id), ('create_date', '>=', ctx['from']),
                                                          ('create_date', '<=', fields.Date.to_string(ctx['to']) + ' 23:59:59')])
        waiting = self.env['sb.me'].with_user(user).approvals() if user else []
        return {'kpis': [{'label': self.env._('Approved'), 'value': len(logs.filtered(lambda l: l.decision == 'approved'))},
                         {'label': self.env._('Sent back'), 'value': len(logs.filtered(lambda l: l.decision != 'approved'))},
                         {'label': self.env._('Waiting for me now'), 'value': len(waiting or [])}]}
