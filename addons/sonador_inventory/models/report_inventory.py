"""Inventory reports on the report engine (OWL screen, Excel, PDF)."""
from collections import defaultdict
from datetime import datetime, time

from odoo import api, fields, models

from odoo.addons.sonador_books.models.ethiopian_calendar import format_ethiopian
from odoo.addons.sonador_books.models.report_catalog import E

from .stock_voucher import STOCK_TYPES

EXTRA_CATALOG = [
    E('stock_movement', 'inventory', 'Inventory Movement Summary', 'Per item: opening, received, issued and sold, closing - quantity and value.', 1),
    E('stock_by_wh', 'inventory', 'Stock by Warehouse', 'Quantity on hand per item in every warehouse and store, at any date.', 1),
    E('count_sheet', 'inventory', 'Physical Count Sheet', 'Printable count sheet per warehouse, with bins and a column to write the count.', 1),
    E('inv_gl_rec', 'inventory', 'Inventory to GL Reconciliation', 'Stock value per inventory account against the general ledger balance.', 1),
    E('grni', 'inventory', 'Goods Received Not Invoiced', 'GRNs posted to goods received not invoiced and not yet billed; ties to the account.', 1),
]

BLURBS = {
    'inv_valuation': 'What is in stock and what it is worth, per item, at any date (FIFO or average cost).',
    'stock_card': 'Bin card: every receipt, issue, sale and count per item, with running quantity and value.',
    'stock_movement': 'Per item: what you started with, what came in, what went out, what is left - quantity and value.',
    'item_costing': 'How each item is costed. FIFO items list the receipts still in stock, oldest first.',
    'cogs_journal': 'Every cost of sales posting in the general ledger, matched to its sale. The margin counts matched sales only; cost with no sale behind it (issues, year-end entries, recalculations) is shown apart.',
    'adj_journal': 'Stock counts and adjustments: books, counted and the difference posted.',
    'grn_register': 'Goods received notes with their items and value.',
    'giv_register': 'Goods issued to departments, sites and jobs, with the expense charged.',
    'reorder': 'Stock items at or below their reorder point.',
    'price_list': 'Every item with its type, class, unit, price, cost and quantity on hand.',
    'stock_by_wh': 'Quantity on hand per item and warehouse.',
    'count_sheet': 'Print it, count, write the quantities, then enter them as a Stock Count.',
    'inv_gl_rec': 'The stock records and the general ledger must agree. Any difference shows here.',
    'grni': 'GRNs posted to the goods received not invoiced account and not billed yet. GRNs that credited the supplier directly are already invoiced and are not listed.',
}

KIND_LABELS = dict(STOCK_TYPES)


class SbReportInventory(models.AbstractModel):
    _inherit = 'sb.report'

    def _extra_catalog(self):
        return super()._extra_catalog() + EXTRA_CATALOG

    def _present(self, doc):
        super()._present(doc)
        if not doc.get('blurb') and doc['key'] in BLURBS:
            doc['blurb'] = BLURBS[doc['key']]

    # ------------------------------------------------------------------ data
    def _end(self, day):
        return datetime.combine(day, time.max)

    def _inv_moves(self, dt, df=None, product_ids=None):
        company = self.env.company
        dom = [('state', '=', 'done'), ('company_id', '=', company.id), ('product_id.is_storable', '=', True),
               ('date', '<=', self._end(dt))]
        if df:
            dom.append(('date', '>=', datetime.combine(df, time.min)))
        if product_ids:
            dom.append(('product_id', 'in', list(product_ids)))
        return self.env['stock.move'].sudo().search(dom, order='date, id')

    @staticmethod
    def _direction(m):
        src, dst = m.location_id.usage == 'internal', m.location_dest_id.usage == 'internal'
        return 1 if dst and not src else (-1 if src and not dst else 0)

    def _move_kind(self, m):
        if m.sb_doc_type:
            return m.sb_doc_type
        if m.sb_reversal_of_id:
            return 'Correction' if self._is_correction(m) else 'Void'
        if m.sb_stock_voucher_id:
            return m.sb_stock_voucher_id._type_label()
        if m.sb_voucher_id:
            v = m.sb_voucher_id
            return dict(v._fields['voucher_type']._description_selection(self.env)).get(v.voucher_type, '')
        return {'customer': 'Delivery', 'supplier': 'Receipt', 'inventory': 'Adjustment', 'production': 'Production'}.get(
            (m.location_dest_id if self._direction(m) < 0 else m.location_id).usage, 'Transfer')

    def _is_correction(self, m):
        """A reversal made by Correct (the document was posted again after it, or waits in draft to be) rather than by Void."""
        doc = m.sb_stock_voucher_id
        if not doc:
            return False
        return doc.state == 'draft' or any(o.id > m.id and not o.sb_reversal_of_id for o in doc.stock_move_ids)

    def _move_doc(self, m):
        doc = m.sb_stock_voucher_id or m.sb_voucher_id
        who = ''
        if m.sb_stock_voucher_id:
            who = m.sb_stock_voucher_id.partner_id.name or m.sb_stock_voucher_id.department or ''
        elif m.sb_voucher_id:
            who = m.sb_voucher_id.partner_id.name or ''
        else:
            who = m.sb_party or ''
        return doc, who

    def _balances(self, dt, product_ids=None):
        """{product: [qty, value]} at the end of dt."""
        bal = defaultdict(lambda: [0.0, 0.0])
        for m in self._inv_moves(dt, product_ids=product_ids):
            d = self._direction(m)
            if d:
                bal[m.product_id][0] += d * m.quantity
                bal[m.product_id][1] += d * abs(m.value)
        for r in self._revaluations(dt, product_ids=product_ids):
            bal[r.product_id][1] += r.value
        return bal

    def _revaluations(self, dt, df=None, product_ids=None):
        dom = [('company_id', '=', self.env.company.id), ('date', '<=', dt)]
        if df:
            dom.append(('date', '>=', df))
        if product_ids:
            dom.append(('product_id', 'in', list(product_ids)))
        return self.env['sb.stock.revaluation'].sudo().search(dom)

    def _products(self, opts, products):
        out = [p for p in products if self._search_ok(opts, p.name, p.default_code, p.categ_id.name)]
        return sorted(out, key=lambda p: (p.categ_id.complete_name or '', p.default_code or '', p.name))

    def _item_cols(self):
        return [{'key': 'code', 'label': 'Item ID', 'type': 'text'}, {'key': 'name', 'label': 'Description', 'type': 'text'}]

    # ------------------------------------------------------------------ Inventory valuation
    def _report_inv_valuation(self, opts):
        dt = self._d(opts['date_to'])
        bal = self._balances(dt, opts.get('product_ids'))
        rows, total = [], [0.0, 0.0]
        by_class = defaultdict(list)
        for p in self._products(opts, [p for p, (q, v) in bal.items() if abs(q) > 1e-6 or abs(v) > 0.005]):
            by_class[p.categ_id].append(p)
        grand = sum(v for _q, v in bal.values()) or 1.0
        for categ in sorted(by_class, key=lambda c: c.complete_name or ''):
            cid = 'c%d' % categ.id
            cq = cv = 0.0
            children = []
            for p in by_class[categ]:
                q, v = bal[p]
                cq += q
                cv += v
                children.append({'id': 'p%d' % p.id, 'parent': cid, 'level': 1, 'kind': 'line', 'label': p.name,
                                 'warning': q < -1e-6,
                                 'values': {'code': p.default_code or '', 'name': p.name, 'uom': p.uom_id.name, 'qty': q,
                                            'cost': v / q if abs(q) > 1e-9 else 0.0, 'value': v, 'pct': 100.0 * v / grand},
                                 'drill': {'report': 'stock_card', 'product_ids': [p.id], 'date_to': opts['date_to']}})
            rows.append({'id': cid, 'parent': None, 'level': 0, 'kind': 'group', 'expandable': True,
                         'label': '%s  (%d %s)' % (categ.complete_name, len(children), 'item' if len(children) == 1 else 'items'),
                         'values': {'code': '', 'name': '', 'value': cv, 'pct': 100.0 * cv / grand}})
            rows += children
            total[0] += cq
            total[1] += cv
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total',
                     'values': {'name': 'Total inventory', 'value': total[1], 'pct': 100.0 if total[1] else 0.0}})
        gl = self._inv_gl_balance(dt)
        if abs(gl - total[1]) > 0.01:
            rows.append({'id': 'gl', 'parent': None, 'level': 0, 'kind': 'note', 'label': 'General ledger',
                         'values': {'name': 'Inventory accounts in the general ledger', 'value': gl}})
            rows.append({'id': 'diff', 'parent': None, 'level': 0, 'kind': 'note', 'label': 'Difference', 'warning': True,
                         'values': {'name': 'Difference: see Inventory to GL Reconciliation', 'value': total[1] - gl},
                         'drill': {'report': 'inv_gl_rec', 'date_to': opts['date_to']}})
        cols = self._item_cols() + [
            {'key': 'uom', 'label': 'Unit', 'type': 'text'}, {'key': 'qty', 'label': 'On hand', 'type': 'number'},
            {'key': 'cost', 'label': 'Unit cost', 'type': 'number'}, {'key': 'value', 'label': 'Value', 'type': 'monetary'},
            {'key': 'pct', 'label': '% of total', 'type': 'percent'}]
        return self._doc('inv_valuation', opts, cols, rows, 'As at %s  |  %s' % (dt.strftime('%d %B %Y'), format_ethiopian(dt)),
                         'Inventory Valuation')

    def _inv_accounts(self):
        company = self.env.company
        accounts = self.env['account.account']
        for categ in self.env['product.category'].with_company(company).search([]):
            accounts |= categ.property_stock_valuation_account_id
        # and every account on the balance sheet's Inventories line (work in process, goods in transit...): an inventory
        # balance no item class uses is still shown, against no stock records
        accounts |= self.env['account.account'].search([('company_ids', 'in', company.id), ('sb_ifrs_line_id.code', '=', 'SFP-CA-01')])
        return accounts | company.account_stock_valuation_id

    def _inv_gl_balance(self, dt, accounts=None):
        accounts = accounts or self._inv_accounts()
        res = self.env['account.move.line']._read_group(
            [('company_id', '=', self.env.company.id), ('parent_state', '=', 'posted'), ('account_id', 'in', accounts.ids),
             ('date', '<=', dt)], [], ['balance:sum'])
        return res[0][0] or 0.0

    # ------------------------------------------------------------------ Stock card / bin card
    def _report_stock_card(self, opts):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        pids = opts.get('product_ids') or None
        from datetime import timedelta
        opening = self._balances(df - timedelta(days=1), pids)
        moves = self._inv_moves(dt, df, pids)
        per = defaultdict(list)
        for m in moves:
            if self._direction(m):
                per[m.product_id].append(m)
        for r in self._revaluations(dt, df, pids):
            per[r.product_id].append(r)
        for p in per:
            per[p].sort(key=lambda x: (x.date if x._name == 'sb.stock.revaluation' else x.date.date(), x._name != 'sb.stock.revaluation', x.id))
        products = self._products(opts, set(per) | {p for p, (q, v) in opening.items() if abs(q) > 1e-6 or abs(v) > 0.005})
        rows = []
        for p in products:
            gid = 'p%d' % p.id
            q, v = opening[p] if p in opening else (0.0, 0.0)
            lines = []
            lines.append({'id': gid + 'o', 'parent': gid, 'level': 1, 'kind': 'detail', 'label': 'Opening balance',
                          'values': {'date': '', 'ref': 'Opening balance', 'bal_qty': q, 'bal_value': v}})
            tin = tout = vin = vout = 0.0
            for m in per.get(p, []):
                if m._name == 'sb.stock.revaluation':
                    v += m.value
                    vin += max(m.value, 0.0)
                    vout += max(-m.value, 0.0)
                    lines.append({'id': 'r%d' % m.id, 'parent': gid, 'level': 1, 'kind': 'detail', 'label': m.reference or 'Cost adjustment',
                                  'values': {'date': fields.Date.to_string(m.date), 'date_ec': format_ethiopian(m.date),
                                             'ref': m.reference or '', 'kind': 'Cost adjustment', 'who': m.description or '',
                                             'qty_in': 0.0, 'qty_out': 0.0, 'bal_qty': q, 'cost': 0.0,
                                             'value_in': max(m.value, 0.0), 'value_out': max(-m.value, 0.0), 'bal_value': v}})
                    continue
                d = self._direction(m)
                qty, val = m.quantity, abs(m.value)
                q += d * qty
                v += d * val
                doc, who = self._move_doc(m)
                day = m.date.date()
                if d > 0:
                    tin += qty
                    vin += val
                else:
                    tout += qty
                    vout += val
                lines.append({'id': 'm%d' % m.id, 'parent': gid, 'level': 1, 'kind': 'detail', 'label': doc.display_name if doc else (m.sb_ref or m.origin or ''),
                              'values': {'date': fields.Date.to_string(day), 'date_ec': format_ethiopian(day),
                                         'ref': doc.name if doc else (m.sb_ref or m.origin or m.reference or ''), 'kind': self._move_kind(m), 'who': who,
                                         'qty_in': qty if d > 0 else 0.0, 'qty_out': qty if d < 0 else 0.0, 'bal_qty': q,
                                         'cost': val / qty if qty else 0.0, 'value_in': val if d > 0 else 0.0,
                                         'value_out': val if d < 0 else 0.0, 'bal_value': v},
                              'open': {'model': doc._name, 'id': doc.id} if doc else None})
            rows.append({'id': gid, 'parent': None, 'level': 0, 'kind': 'group', 'expandable': True,
                         'label': '%s%s' % ('[%s] ' % p.default_code if p.default_code else '', p.name),
                         'values': {'date': '', 'ref': '%s%s' % ('[%s] ' % p.default_code if p.default_code else '', p.name),
                                    'qty_in': tin, 'qty_out': tout, 'bal_qty': q, 'value_in': vin, 'value_out': vout, 'bal_value': v}})
            rows += lines
        cols = [{'key': 'ref', 'label': 'Item / Reference', 'type': 'text'}, {'key': 'date', 'label': 'Date', 'type': 'date'},
                {'key': 'date_ec', 'label': 'E.C. date', 'type': 'text'}, {'key': 'kind', 'label': 'Type', 'type': 'text'},
                {'key': 'who', 'label': 'Customer / supplier / department', 'type': 'text'},
                {'key': 'qty_in', 'label': 'Qty in', 'type': 'number'}, {'key': 'qty_out', 'label': 'Qty out', 'type': 'number'},
                {'key': 'bal_qty', 'label': 'Balance qty', 'type': 'number'}, {'key': 'cost', 'label': 'Unit cost', 'type': 'number'},
                {'key': 'value_in', 'label': 'Value in', 'type': 'monetary'}, {'key': 'value_out', 'label': 'Value out', 'type': 'monetary'},
                {'key': 'bal_value', 'label': 'Balance value', 'type': 'monetary'}]
        return self._doc('stock_card', opts, cols, rows, '%s  |  %s' % (self._period_name(df, dt), self._label_range_ec(df, dt)),
                         'Stock Card (Bin Card)')

    # ------------------------------------------------------------------ Movement summary
    def _report_stock_movement(self, opts):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        from datetime import timedelta
        opening = self._balances(df - timedelta(days=1), opts.get('product_ids'))
        flows = defaultdict(lambda: defaultdict(float))
        for m in self._inv_moves(dt, df, opts.get('product_ids')):
            d = self._direction(m)
            if not d:
                continue
            kind = 'in' if d > 0 else 'out'
            f = flows[m.product_id]
            f[kind + '_qty'] += m.quantity
            f[kind + '_value'] += abs(m.value)
            if d < 0 and m.location_dest_id.usage == 'customer':
                f['sold_qty'] += m.quantity
        for r in self._revaluations(dt, df, opts.get('product_ids')):
            flows[r.product_id]['in_value' if r.value > 0 else 'out_value'] += abs(r.value)
        products = self._products(opts, set(flows) | {p for p, (q, v) in opening.items() if abs(q) > 1e-6 or abs(v) > 0.005})
        rows, grand = [], defaultdict(float)
        for p in products:
            oq, ov = opening[p] if p in opening else (0.0, 0.0)
            f = flows.get(p, {})
            vals = {'code': p.default_code or '', 'name': p.name, 'uom': p.uom_id.name, 'open_qty': oq, 'open_value': ov,
                    'in_qty': f.get('in_qty', 0.0), 'in_value': f.get('in_value', 0.0), 'out_qty': f.get('out_qty', 0.0),
                    'out_value': f.get('out_value', 0.0), 'sold_qty': f.get('sold_qty', 0.0)}
            vals['close_qty'] = oq + vals['in_qty'] - vals['out_qty']
            vals['close_value'] = ov + vals['in_value'] - vals['out_value']
            for k in ('open_value', 'in_value', 'out_value', 'close_value'):
                grand[k] += vals[k]
            rows.append({'id': 'p%d' % p.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': p.name, 'values': vals,
                         'warning': vals['close_qty'] < -1e-6,
                         'drill': {'report': 'stock_card', 'product_ids': [p.id], 'date_from': opts['date_from'], 'date_to': opts['date_to']}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total', 'values': dict(grand, name='Total')})
        cols = self._item_cols() + [
            {'key': 'uom', 'label': 'Unit', 'type': 'text'},
            {'key': 'open_qty', 'label': 'Opening qty', 'type': 'number'}, {'key': 'open_value', 'label': 'Opening value', 'type': 'monetary'},
            {'key': 'in_qty', 'label': 'Qty in', 'type': 'number'}, {'key': 'in_value', 'label': 'Value in', 'type': 'monetary'},
            {'key': 'out_qty', 'label': 'Qty out', 'type': 'number'}, {'key': 'sold_qty', 'label': 'of which sold', 'type': 'number'},
            {'key': 'out_value', 'label': 'Value out', 'type': 'monetary'},
            {'key': 'close_qty', 'label': 'Closing qty', 'type': 'number'}, {'key': 'close_value', 'label': 'Closing value', 'type': 'monetary'}]
        return self._doc('stock_movement', opts, cols, rows, '%s  |  %s' % (self._period_name(df, dt), self._label_range_ec(df, dt)),
                         'Inventory Movement Summary')

    # ------------------------------------------------------------------ Item costing
    def _report_item_costing(self, opts):
        dt = self._d(opts['date_to'])
        bal = self._balances(dt)
        Product = self.env['product.product'].with_company(self.env.company)
        products = self._products(opts, Product.search([('is_storable', '=', True)]))
        method_label = {'fifo': 'FIFO', 'average': 'Average (AVCO)', 'standard': 'Standard'}
        rows = []
        for p in products:
            q, v = bal[p] if p in bal else (0.0, 0.0)
            gid = 'p%d' % p.id
            rows.append({'id': gid, 'parent': None, 'level': 0, 'kind': 'group' if p.cost_method == 'fifo' else 'line',
                         'expandable': p.cost_method == 'fifo', 'label': p.name,
                         'values': {'code': p.default_code or '', 'name': p.name, 'method': method_label.get(p.cost_method, p.cost_method),
                                    'qty': q, 'cost': p.standard_price, 'value': v},
                         'drill': {'report': 'stock_card', 'product_ids': [p.id]}})
            if p.cost_method == 'fifo':
                ins = self.env['stock.move'].sudo().search([('product_id', '=', p.id), ('state', '=', 'done'), ('is_in', '=', True)],
                                                           order='date, id')
                for m in ins.filtered(lambda x: x.remaining_qty > 1e-9):
                    doc, who = self._move_doc(m)
                    rows.append({'id': 'l%d' % m.id, 'parent': gid, 'level': 1, 'kind': 'detail', 'label': doc.name if doc else m.origin,
                                 'values': {'name': '%s  %s  %s' % (fields.Date.to_string(m.date.date()), doc.name if doc else (m.origin or ''), who),
                                            'qty': m.remaining_qty, 'cost': m.value / m.quantity if m.quantity else 0.0,
                                            'value': m.remaining_value}})
        cols = self._item_cols() + [{'key': 'method', 'label': 'Costing', 'type': 'text'}, {'key': 'qty', 'label': 'On hand', 'type': 'number'},
                                    {'key': 'cost', 'label': 'Current unit cost', 'type': 'number'}, {'key': 'value', 'label': 'Value', 'type': 'monetary'}]
        return self._doc('item_costing', opts, cols, rows, 'FIFO layers and average costs', 'Item Costing')

    # ------------------------------------------------------------------ Cost of sales journal (with margin)
    def _report_cogs_journal(self, opts):
        """Every cost of sales posting in the period, read from the general ledger: sales and cash sales, credit notes,
        cost recalculations, issues charged to cost of sales and entries brought over from other systems (e.g. a COGS
        journal). Each cost line is matched to its sale: the same entry, the sale whose cost entry it is, the sale whose
        delivery a cost recalculation line re-costs, or the sale named in the reference of an entry brought over
        ("COGS|FS00001848", "FS00001851/COGS", "PT|COGS|FS00000198|7/6/26"). The margin is worked out on matched lines
        only; cost with no sale behind it (issues, year-end entries) is shown as such, never against sales it does not
        belong to."""
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        company = self.env.company
        lines = self.env['account.move.line'].search([
            ('company_id', '=', company.id), ('parent_state', '=', 'posted'), ('date', '>=', df), ('date', '<=', dt),
            ('account_id.account_type', '=', 'expense_direct_cost'), ('move_id.sb_closing', '=', False)], order='date, move_id, id')
        if opts.get('product_ids'):
            lines = lines.filtered(lambda l: l.product_id.id in opts['product_ids'])
        moves = lines.move_id
        V = self.env['sb.voucher']
        # a voided sale keeps its cost lines (the cost as posted, the void's reversal, the recalculations and their
        # reversal): they are matched to it and net to zero, with no sales against them
        dom = [('company_id', '=', company.id), ('voucher_type', 'in', ('cash_sale', 'sale', 'sale_return')), ('state', 'in', ('posted', 'void'))]
        originals = moves | moves.reversed_entry_id
        voucher_of = {v.move_id: v for v in V.search(dom + [('move_id', 'in', originals.ids)])}
        for v in V.search(dom + [('sb_cost_move_ids', 'in', originals.ids)]):  # cost entries posted apart from their sale
            for m in v.sb_cost_move_ids & originals:
                voucher_of.setdefault(m, v)
        for m in moves.filtered('reversed_entry_id'):  # the reversal of a voided sale's entry (or of its cost entry)
            if m not in voucher_of and m.reversed_entry_id in voucher_of and voucher_of[m.reversed_entry_id].state == 'void':
                voucher_of[m] = voucher_of[m.reversed_entry_id]
        # entries brought over from another system name their sale in the reference
        numbers_of = {m: self._cogs_doc_numbers(m.ref) for m in moves if m not in voucher_of and m.ref}
        wanted = {n for numbers in numbers_of.values() for n in numbers}
        sale_by_number = {v.name: v for v in V.search(dom + [('name', 'in', list(wanted))])} if wanted else {}
        for m, numbers in numbers_of.items():
            sale = next((sale_by_number[n] for n in numbers if n in sale_by_number), None)
            if sale:
                voucher_of[m] = sale
        # cost recalculation lines name the delivery they re-cost (one entry holds the recalculations of many sales)
        recosted = lines.filtered('sb_recost_move_id')
        sale_of_delivery = {v.id: v for v in V.search(dom + [('id', 'in', recosted.sb_recost_move_id.sb_voucher_id.ids)])}
        line_voucher = {l: sale_of_delivery.get(l.sb_recost_move_id.sb_voucher_id.id) if l.sb_recost_move_id else voucher_of.get(l.move_id)
                        for l in lines}
        sales_of = defaultdict(float)  # (sale entry, item) -> sales; (sale entry, None) -> all its sales
        for il in self.env['account.move.line'].search([('move_id', 'in', [v.move_id.id for v in line_voucher.values() if v and v.state == 'posted']),
                                                        ('account_id.account_type', 'in', ('income', 'income_other'))]):
            sales_of[(il.move_id, il.product_id)] -= il.balance
            sales_of[(il.move_id, None)] -= il.balance
        # a sale whose cost lines and sales lines all name their items is matched item by item; otherwise the whole
        # sale goes against its first cost line
        itemised_sales = {k[0] for k in sales_of if k[1]}
        unitemised = {v.move_id for l, v in line_voucher.items() if v and not l.product_id}
        by_item = {v.move_id for v in line_voucher.values() if v and v.move_id in itemised_sales} - unitemised
        given = set()
        rows, grand = [], defaultdict(float)
        for l in lines:
            v = line_voucher.get(l)
            void = bool(v) and v.state == 'void'
            ref = (self.env._('%s (void)', v.name) if void else v.name) if v else \
                (l.move_id.name if l.move_id.name and l.move_id.name != '/' else l.move_id.ref or '')
            who = (v.partner_id if v else (l.partner_id or l.move_id.partner_id)).name or ''
            item = l.product_id.display_name or l.name or l.move_id.ref or ''
            if not self._search_ok(opts, item, l.product_id.default_code, who, ref, l.move_id.ref):
                continue
            cost = l.balance
            vals = {'date': fields.Date.to_string(l.date), 'ref': ref, 'who': who, 'item': item,
                    'qty': (1 if cost >= 0 else -1) * abs(l.quantity) if l.product_id and not l.sb_recost_move_id else 0.0,
                    'cost': cost}
            if v:
                key = (v.move_id, l.product_id if v.move_id in by_item else None)
                sales = 0.0
                if key not in given:  # the sales of a sale (and item) go on its first cost line only
                    given.add(key)
                    sales = sales_of.get(key, 0.0)
                vals.update(sales=sales, margin=sales - cost, note=self.env._('Void sale: its cost nets to zero') if void else '',
                            pct=100.0 * (sales - cost) / sales if abs(sales) > 0.005 else 0.0)
                grand['sales'] += sales
                grand['matched'] += cost
            else:
                vals.update(sales=0.0, margin=0.0, pct=0.0, note=self.env._('Cost without a matched sale'))
                grand['unmatched'] += cost
            grand['cost'] += cost
            rows.append({'id': 'l%d' % l.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': ref, 'values': vals,
                         'open': {'model': 'sb.voucher', 'id': v.id} if v else {'model': 'account.move', 'id': l.move_id.id}})
        grand['margin'] = grand['sales'] - grand['matched']
        grand['pct'] = 100.0 * grand['margin'] / grand['sales'] if abs(grand['sales']) > 0.005 else 0.0
        note = self.env._('Margin on matched sales only') if abs(grand['unmatched']) > 0.005 else ''
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total',
                     'values': dict(grand, date='', ref='Total', note=note)})
        cols = [{'key': 'date', 'label': 'Date', 'type': 'date'}, {'key': 'ref', 'label': 'Voucher / entry', 'type': 'text'},
                {'key': 'who', 'label': 'Customer', 'type': 'text'}, {'key': 'item', 'label': 'Item', 'type': 'text'},
                {'key': 'qty', 'label': 'Qty', 'type': 'number'}, {'key': 'sales', 'label': 'Sales', 'type': 'monetary'},
                {'key': 'cost', 'label': 'Cost of sales', 'type': 'monetary'}, {'key': 'margin', 'label': 'Gross margin', 'type': 'monetary'},
                {'key': 'pct', 'label': 'Margin %', 'type': 'percent'}, {'key': 'note', 'label': 'Note', 'type': 'text'}]
        return self._doc('cogs_journal', opts, cols, rows, '%s  |  %s' % (self._period_name(df, dt), self._label_range_ec(df, dt)),
                         'Cost of Goods Sold Journal')

    @staticmethod
    def _cogs_doc_numbers(ref):
        """Sale numbers a cost entry brought over may name, most likely first: 'COGS|FS00001848' -> FS00001848,
        'O18|FS00001851/COGS' -> FS00001851, 'PT|COGS|VAN-074-FS00000198|7/6/26' -> VAN-074-FS00000198."""
        parts = [p.strip() for p in (ref or '').split('|')]
        parts = [p[:-5].strip() if p.upper().endswith('/COGS') else p for p in parts]
        return [p for p in parts[2:3] + parts[::-1] if p]

    # ------------------------------------------------------------------ Registers of inventory vouchers
    def _stock_register(self, opts, key, vtype, title, extra_cols):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        dom = [('company_id', '=', self.env.company.id), ('voucher_type', '=', vtype), ('date', '>=', df), ('date', '<=', dt),
               ('state', '!=', 'draft')]
        rows, grand = [], defaultdict(float)
        for v in self.env['sb.stock.voucher'].search(dom, order='date, name'):
            if not self._search_ok(opts, v.name, v.partner_id.name, v.department, v.reference, *v.line_ids.product_id.mapped('name')):
                continue
            void = v.state == 'void'
            gid = 'v%d' % v.id
            head = {'date': fields.Date.to_string(v.date), 'ref': v.name, 'who': v.partner_id.name or v.department or v.reason_id.name or '',
                    'wh': v.warehouse_id.name + (' > %s' % v.warehouse_dest_id.name if v.warehouse_dest_id else ''),
                    'account': v.account_id.display_name if v.account_id else '', 'value': 0.0 if void else v.amount_total,
                    'status': 'Void' if void else ('Billed' if v.billed else '')}
            if not void:
                grand['value'] += v.amount_total
            rows.append({'id': gid, 'parent': None, 'level': 0, 'kind': 'group', 'expandable': True, 'label': v.name, 'values': head,
                         'open': {'model': 'sb.stock.voucher', 'id': v.id}})
            for l in v.line_ids:
                rows.append({'id': 'l%d' % l.id, 'parent': gid, 'level': 1, 'kind': 'detail', 'label': l.product_id.display_name,
                             'values': {'ref': l.product_id.display_name, 'qty': l.quantity, 'counted': l.qty_counted,
                                        'on_hand': l.qty_on_hand, 'cost': l.unit_cost, 'value': l.value}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total', 'values': dict(grand, ref='Total')})
        cols = [{'key': 'ref', 'label': 'Voucher / Item', 'type': 'text'}, {'key': 'date', 'label': 'Date', 'type': 'date'}] + extra_cols + [
            {'key': 'qty', 'label': 'Qty' if vtype != 'adjustment' else 'Difference', 'type': 'number'},
            {'key': 'cost', 'label': 'Unit cost', 'type': 'number'}, {'key': 'value', 'label': 'Value', 'type': 'monetary'},
            {'key': 'status', 'label': 'Status', 'type': 'text'}]
        return self._doc(key, opts, cols, rows, '%s  |  %s' % (self._period_name(df, dt), self._label_range_ec(df, dt)), title)

    def _report_grn_register(self, opts):
        return self._stock_register(opts, 'grn_register', 'grn', 'GRN Register',
                                    [{'key': 'who', 'label': 'Supplier', 'type': 'text'}, {'key': 'wh', 'label': 'Warehouse', 'type': 'text'}])

    def _report_giv_register(self, opts):
        return self._stock_register(opts, 'giv_register', 'giv', 'GIV Register',
                                    [{'key': 'who', 'label': 'Issued to', 'type': 'text'}, {'key': 'wh', 'label': 'Warehouse', 'type': 'text'},
                                     {'key': 'account', 'label': 'Charged to', 'type': 'text'}])

    def _report_adj_journal(self, opts):
        return self._stock_register(opts, 'adj_journal', 'adjustment', 'Inventory Adjustment Journal',
                                    [{'key': 'who', 'label': 'Reason', 'type': 'text'}, {'key': 'account', 'label': 'Account', 'type': 'text'},
                                     {'key': 'wh', 'label': 'Warehouse', 'type': 'text'}, {'key': 'on_hand', 'label': 'Books', 'type': 'number'},
                                     {'key': 'counted', 'label': 'Counted', 'type': 'number'}])

    # ------------------------------------------------------------------ Lists
    def _report_price_list(self, opts):
        dt = self._d(opts['date_to'])
        bal = self._balances(dt)
        kinds = dict(self.env['product.template']._fields['sb_item_kind']._description_selection(self.env))
        Product = self.env['product.product'].with_company(self.env.company)
        rows = []
        for p in self._products(opts, Product.search([])):
            q, v = bal[p] if p in bal else (0.0, 0.0)
            rows.append({'id': 'p%d' % p.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': p.name,
                         'values': {'code': p.default_code or '', 'name': p.name, 'kind': kinds.get(p.sb_item_kind, ''),
                                    'class': p.categ_id.name or '', 'uom': p.uom_id.name, 'price': p.lst_price, 'cost': p.standard_price,
                                    'qty': q if p.is_storable else None, 'value': v if p.is_storable else None},
                         'open': {'model': 'product.template', 'id': p.product_tmpl_id.id}})
        cols = self._item_cols() + [{'key': 'kind', 'label': 'Type', 'type': 'text'}, {'key': 'class', 'label': 'Item class', 'type': 'text'},
                                    {'key': 'uom', 'label': 'Unit', 'type': 'text'}, {'key': 'price', 'label': 'Sales price', 'type': 'monetary'},
                                    {'key': 'cost', 'label': 'Unit cost', 'type': 'number'}, {'key': 'qty', 'label': 'On hand', 'type': 'number'},
                                    {'key': 'value', 'label': 'Value', 'type': 'monetary'}]
        return self._doc('price_list', opts, cols, rows, '%d items' % len(rows), 'Item List / Price List')

    def _report_reorder(self, opts):
        Product = self.env['product.product'].with_company(self.env.company)
        bal = self._balances(fields.Date.context_today(self))
        rows = []
        for p in self._products(opts, Product.search([('is_storable', '=', True), ('sb_reorder_point', '>', 0)])):
            q = bal[p][0] if p in bal else 0.0
            if q > p.sb_reorder_point + 1e-9:
                continue
            suggest = max(p.sb_reorder_qty, p.sb_reorder_point - q)
            rows.append({'id': 'p%d' % p.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': p.name, 'warning': q <= 0,
                         'values': {'code': p.default_code or '', 'name': p.name, 'uom': p.uom_id.name, 'qty': q,
                                    'point': p.sb_reorder_point, 'order': suggest, 'vendor': p.sb_vendor_id.name or '',
                                    'cost': p.standard_price, 'value': suggest * p.standard_price},
                         'open': {'model': 'product.template', 'id': p.product_tmpl_id.id}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total',
                     'values': {'name': 'Estimated cost to reorder', 'value': sum(r['values']['value'] for r in rows)}})
        cols = self._item_cols() + [{'key': 'uom', 'label': 'Unit', 'type': 'text'}, {'key': 'qty', 'label': 'On hand', 'type': 'number'},
                                    {'key': 'point', 'label': 'Reorder point', 'type': 'number'}, {'key': 'order', 'label': 'Order', 'type': 'number'},
                                    {'key': 'vendor', 'label': 'Preferred vendor', 'type': 'text'},
                                    {'key': 'cost', 'label': 'Last unit cost', 'type': 'number'}, {'key': 'value', 'label': 'Est. cost', 'type': 'monetary'}]
        return self._doc('reorder', opts, cols, rows, 'Items at or below their reorder point today', 'Reorder Worksheet')

    def _wh_quantities(self, dt):
        """{(product, warehouse): qty} at the end of dt."""
        qty = defaultdict(float)
        for m in self._inv_moves(dt):
            src, dst = m.location_id.warehouse_id, m.location_dest_id.warehouse_id
            if m.location_dest_id.usage == 'internal' and dst and dst != (src if m.location_id.usage == 'internal' else False):
                qty[(m.product_id, dst)] += m.quantity
            if m.location_id.usage == 'internal' and src and src != (dst if m.location_dest_id.usage == 'internal' else False):
                qty[(m.product_id, src)] -= m.quantity
        return qty

    def _report_stock_by_wh(self, opts):
        dt = self._d(opts['date_to'])
        whs = self.env['stock.warehouse'].search([('company_id', '=', self.env.company.id)])
        qty = self._wh_quantities(dt)
        products = self._products(opts, {p for (p, w), q in qty.items() if abs(q) > 1e-6})
        rows = []
        for p in products:
            vals = {'code': p.default_code or '', 'name': p.name, 'uom': p.uom_id.name}
            tot = 0.0
            for w in whs:
                vals['w%d' % w.id] = qty.get((p, w), 0.0)
                tot += vals['w%d' % w.id]
            vals['total'] = tot
            rows.append({'id': 'p%d' % p.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': p.name, 'values': vals,
                         'drill': {'report': 'stock_card', 'product_ids': [p.id], 'date_to': opts['date_to']}})
        cols = self._item_cols() + [{'key': 'uom', 'label': 'Unit', 'type': 'text'}] + [
            {'key': 'w%d' % w.id, 'label': w.name, 'type': 'number'} for w in whs] + [{'key': 'total', 'label': 'Total', 'type': 'number'}]
        return self._doc('stock_by_wh', opts, cols, rows, 'As at %s  |  %s' % (dt.strftime('%d %B %Y'), format_ethiopian(dt)),
                         'Stock by Warehouse')

    def _report_count_sheet(self, opts):
        dt = self._d(opts['date_to'])
        whs = self.env['stock.warehouse'].search([('company_id', '=', self.env.company.id)])
        qty = self._wh_quantities(dt)
        Product = self.env['product.product'].with_company(self.env.company)
        stock_items = self._products(opts, Product.search([('is_storable', '=', True)]))
        rows = []
        for w in whs:
            wid = 'w%d' % w.id
            items = [p for p in stock_items if abs(qty.get((p, w), 0.0)) > 1e-6] or ([] if len(whs) > 1 else stock_items)
            if not items:
                continue
            rows.append({'id': wid, 'parent': None, 'level': 0, 'kind': 'section', 'label': w.name, 'page_break': bool(rows),
                         'values': {'code': w.name}})
            for p in sorted(items, key=lambda x: (x.sb_bin or '', x.default_code or '', x.name)):
                rows.append({'id': '%s_p%d' % (wid, p.id), 'parent': wid, 'level': 1, 'kind': 'line', 'label': p.name,
                             'values': {'code': p.default_code or '', 'name': p.name, 'bin': p.sb_bin or '', 'uom': p.uom_id.name,
                                        'books': qty.get((p, w), 0.0) if opts.get('show_books', True) else None, 'counted': '______'}})
        cols = self._item_cols() + [{'key': 'bin', 'label': 'Bin / shelf', 'type': 'text'}, {'key': 'uom', 'label': 'Unit', 'type': 'text'},
                                    {'key': 'books', 'label': 'Books', 'type': 'number'}, {'key': 'counted', 'label': 'Counted', 'type': 'text'}]
        doc = self._doc('count_sheet', opts, cols, rows, 'Count date %s  |  %s' % (dt.strftime('%d %B %Y'), format_ethiopian(dt)),
                        'Physical Count Sheet')
        doc['signatures'] = True
        return doc

    # ------------------------------------------------------------------ Controls
    def _report_inv_gl_rec(self, opts):
        dt = self._d(opts['date_to'])
        company = self.env.company
        bal = self._balances(dt)
        stock = defaultdict(float)
        for p, (q, v) in bal.items():
            acc = self.env['sb.stock']._valuation_account(p, company)
            stock[acc] += v
        rows, grand = [], defaultdict(float)
        for acc in sorted(set(stock) | set(self._inv_accounts()), key=lambda a: a.code or ''):
            gl = self._inv_gl_balance(dt, acc)
            s = stock.get(acc, 0.0)
            if abs(gl) < 0.005 and abs(s) < 0.005:
                continue
            diff = s - gl
            for k, val in (('stock', s), ('gl', gl), ('diff', diff)):
                grand[k] += val
            rows.append({'id': 'a%d' % acc.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': acc.display_name,
                         'warning': abs(diff) > 0.01,
                         'values': {'code': acc.code, 'name': acc.name, 'stock': s, 'gl': gl, 'diff': diff,
                                    'status': 'Agrees' if abs(diff) <= 0.01 else 'Check'},
                         'drill': {'report': 'general_ledger', 'account_ids': [acc.id], 'date_from': None, 'date_to': opts['date_to']}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total',
                     'values': dict(grand, name='Total', status='Agrees' if abs(grand['diff']) <= 0.01 else 'Check')})
        cols = [{'key': 'code', 'label': 'Account', 'type': 'text'}, {'key': 'name', 'label': 'Description', 'type': 'text'},
                {'key': 'stock', 'label': 'Stock records', 'type': 'monetary'}, {'key': 'gl', 'label': 'General ledger', 'type': 'monetary'},
                {'key': 'diff', 'label': 'Difference', 'type': 'monetary'}, {'key': 'status', 'label': 'Status', 'type': 'text'}]
        return self._doc('inv_gl_rec', opts, cols, rows, 'As at %s  |  %s' % (dt.strftime('%d %B %Y'), format_ethiopian(dt)),
                         'Inventory to GL Reconciliation')

    def _report_grni(self, opts):
        """GRNs posted to the goods received not invoiced account and not billed by the date, at what they credited it.
        GRNs that credited a supplier or an advance directly are already invoiced in the books and are not listed. The
        total is checked against the balance of the account."""
        dt = self._d(opts['date_to'])
        company = self.env.company
        grni = company.sb_grni_account_id
        rows, grand = [], 0.0
        dom = [('company_id', '=', company.id), ('voucher_type', '=', 'grn'), ('state', '=', 'posted'), ('date', '<=', dt),
               ('move_id.line_ids.account_id', '=', grni.id)]
        for v in self.env['sb.stock.voucher'].search(dom, order='partner_id, date') if grni else []:
            if any(b.state == 'posted' and b.date <= dt for b in v.bill_ids | v.bill_many_ids):
                continue
            value = -sum(v.move_id.line_ids.filtered(lambda a: a.account_id == grni).mapped('balance'))
            if company.currency_id.is_zero(value) or not self._search_ok(opts, v.name, v.partner_id.name, v.reference):
                continue
            grand += value
            rows.append({'id': 'v%d' % v.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': v.name,
                         'values': {'date': fields.Date.to_string(v.date), 'ref': v.name, 'who': v.partner_id.name or '',
                                    'reference': v.reference or '', 'days': (dt - v.date).days, 'value': value},
                         'open': {'model': 'sb.stock.voucher', 'id': v.id}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total', 'values': {'ref': 'Total', 'value': grand}})
        if grni:
            gl = -self._inv_gl_balance(dt, grni)
            rows.append({'id': 'gl', 'parent': None, 'level': 0, 'kind': 'total', 'label': grni.display_name,
                         'values': {'ref': self.env._('Balance of %s', grni.display_name), 'value': gl}})
            if not company.currency_id.is_zero(gl - grand):
                rows.append({'id': 'diff', 'parent': None, 'level': 0, 'kind': 'note', 'warning': True,
                             'label': self.env._('%s on the account is not from GRNs waiting for a bill (bills entered without '
                                                 'their GRN, price differences, entries brought over)', '{:,.2f}'.format(gl - grand)),
                             'values': {'value': gl - grand}})
        cols = [{'key': 'date', 'label': 'Date', 'type': 'date'}, {'key': 'ref', 'label': 'GRN', 'type': 'text'},
                {'key': 'who', 'label': 'Supplier', 'type': 'text'}, {'key': 'reference', 'label': 'Delivery note', 'type': 'text'},
                {'key': 'days', 'label': 'Days waiting', 'type': 'number_int'}, {'key': 'value', 'label': 'Value', 'type': 'monetary'}]
        return self._doc('grni', opts, cols, rows, 'As at %s  |  %s' % (dt.strftime('%d %B %Y'), format_ethiopian(dt)),
                         'Goods Received Not Invoiced')

    # ------------------------------------------------------------------ presentation
    def _present_inv_valuation(self, doc):
        from odoo.addons.sonador_books.models.report_present import _card
        total = self._row(doc, 'total')
        items = [r for r in doc['rows'] if r['kind'] == 'line']
        doc['summary'] = [_card('Inventory value', total['values']['value'] if total else 0.0),
                          _card('Items in stock', len([r for r in items if r['values']['qty'] > 0]), 'int'),
                          _card('Negative stock', len([r for r in items if r['values']['qty'] < 0]), 'int',
                                'bad' if any(r['values']['qty'] < 0 for r in items) else 'good'),
                          _card('Agrees with GL', 'No' if self._row(doc, 'diff') else 'Yes', 'badge', 'bad' if self._row(doc, 'diff') else 'good')]
        groups = [(r['label'], r['values'].get('value', 0.0)) for r in doc['rows'] if r['kind'] == 'group' and r['values'].get('value', 0) > 0.005]
        if groups:
            doc['chart'] = {'type': 'doughnut', 'title': 'Value by item class', 'labels': [g[0] for g in groups],
                            'series': [{'label': 'Value', 'data': [g[1] for g in groups]}]}

    def _present_cogs_journal(self, doc):
        from odoo.addons.sonador_books.models.report_present import _card
        t = (self._row(doc, 'total') or {}).get('values', {})
        doc['summary'] = [_card('Cost of sales', t.get('cost', 0.0), hint='All cost of sales postings: agrees with the ledger'),
                          _card('Matched sales', t.get('sales', 0.0)), _card('Gross margin', t.get('margin', 0.0), hint='Matched sales only'),
                          _card('Margin %', t.get('pct', 0.0), 'percent'),
                          _card('Cost without a matched sale', t.get('unmatched', 0.0), tone='bad' if abs(t.get('unmatched', 0.0)) > 0.005 else None)]

    def _present_grni(self, doc):
        from odoo.addons.sonador_books.models.report_present import _card
        total, gl = self._row(doc, 'total'), self._row(doc, 'gl')
        t, g = (total or {}).get('values', {}).get('value', 0.0), (gl or {}).get('values', {}).get('value', 0.0)
        ok = abs(t - g) <= 0.01
        doc['summary'] = [_card('Not invoiced', t), _card('GRNI account', g),
                          _card('Agrees with GL', 'Yes' if ok else 'No', 'badge', 'good' if ok else 'bad')]

    def _present_inv_gl_rec(self, doc):
        from odoo.addons.sonador_books.models.report_present import _card
        t = (self._row(doc, 'total') or {}).get('values', {})
        ok = abs(t.get('diff', 0.0)) <= 0.01
        doc['summary'] = [_card('Stock records', t.get('stock', 0.0)), _card('General ledger', t.get('gl', 0.0)),
                          _card('Difference', t.get('diff', 0.0)), _card('Status', 'Agrees' if ok else 'Check', 'badge', 'good' if ok else 'bad')]
