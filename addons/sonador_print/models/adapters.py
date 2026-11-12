"""How each kind of document is described for printing (see engine.py for the shape)."""
from odoo import api, fields, models

COLS_ITEMS = [{'key': 'no', 'label': '#', 'align': 'left'}, {'key': 'desc', 'label': 'Description', 'align': 'left'},
              {'key': 'qty', 'label': 'Qty', 'align': 'right'}, {'key': 'unit', 'label': 'Unit', 'align': 'left'},
              {'key': 'price', 'label': 'Unit price', 'align': 'right'}, {'key': 'tax', 'label': 'Tax', 'align': 'left', 'opt': 'show_tax_column'},
              {'key': 'amount', 'label': 'Amount', 'align': 'right'}]


class SbPrintAdapters(models.AbstractModel):
    _inherit = 'sb.print'

    # ------------------------------------------------------------------ vouchers
    def _doc_sb_voucher(self, v, kind):
        cur = v.amount_currency_id if 'amount_currency_id' in v._fields else v.currency_id
        d = {'record': v, 'number': v.name if v.name not in (False, 'New', '/') else 'Draft', 'date': v.date, 'currency': cur,
             'status_mark': 'DRAFT' if v.state == 'draft' else 'VOID' if v.state == 'void' else '', 'notes': v.memo or '',
             'meta': [], 'fiscal': [], 'qr': False}
        if v.reference:
            d['meta'].append(('Reference', v.reference))
        if v.due_date and kind in ('invoice', 'bill'):
            d['meta'].append(('Due', fields.Date.to_string(v.due_date)))
        if 'sb_sale_order_id' in v._fields and v.sb_sale_order_id:
            d['meta'].append(('Order', v.sb_sale_order_id.name))
        if 'sb_efris_fdn' in v._fields and v.sb_efris_fdn:
            d['fiscal'] = [('EFRIS fiscal document no.', v.sb_efris_fdn), ('Verification code', v.sb_efris_code or '')]
            d['qr'] = v.sb_efris_qr_png if 'sb_efris_qr_png' in v._fields else False
        if kind in ('invoice', 'cash_sale', 'credit_note', 'bill'):
            d['party'] = self._party(v.partner_id, 'Supplier' if kind == 'bill' else 'Customer')
            if kind == 'cash_sale' and not v.partner_id:
                d['party']['name'] = 'Cash customer'
            d['columns'] = COLS_ITEMS
            d['lines'] = [{'no': i + 1, 'desc': l.name or l.product_id.display_name or l.account_id.name,
                           'code': (l.product_id.default_code or '') if l.product_id else '', 'qty': self._qty(l.quantity),
                           'unit': l.product_id.uom_id.name if l.product_id else '', 'price': self._money(l.price_unit, cur),
                           'tax': ', '.join(l.tax_ids.mapped('name')) or 'Exempt', 'amount': self._money(l.price_subtotal, cur)}
                          for i, l in enumerate(v.line_ids.filtered(lambda x: x.price_subtotal or x.product_id))]
            d['totals'] = [('Amount before tax', self._money(v.amount_untaxed, cur), False), ('VAT', self._money(v.amount_tax, cur), False),
                           ('Total %s' % cur.name, self._money(v.amount_total, cur), True)]
            if v.withholding and v.wht_amount:
                d['totals'].insert(2, ('Withholding', '-' + self._money(v.wht_amount, v.currency_id), False))
            d['amount'] = v.amount_total
            if kind == 'cash_sale':
                d['meta'] += [('Paid by', ', '.join('%s %s' % (r.account_id.name, r.reference or '') for r in v.cash_line_ids))]
        elif kind in ('receipt', 'payment'):
            d['party'] = self._party(v.partner_id, 'Received from' if kind == 'receipt' else 'Paid to')
            d['columns'] = [{'key': 'doc', 'label': 'For', 'align': 'left'}, {'key': 'date', 'label': 'Date', 'align': 'left'},
                            {'key': 'amount', 'label': 'Amount', 'align': 'right'}]
            applied = v.apply_ids.filtered(lambda x: x.pay or x.amount_applied)
            lines = [{'doc': '%s %s' % (a.document or '', ('(%s)' % a.invoice_ref) if a.invoice_ref else ''), 'date': fields.Date.to_string(a.invoice_date) if a.invoice_date else '',
                      'inv': '%s %s' % (a.item_currency_id.name, self._money(a.amount_applied, a.item_currency_id)) if a.item_currency_id != v.currency_id else '',
                      'amount': self._money(a.amount_applied_company, v.currency_id)} for a in applied]
            if any(a.item_currency_id != v.currency_id for a in applied):
                # settling foreign-currency invoices: show what was settled in their own currency too
                d['columns'].insert(2, {'key': 'inv', 'label': 'Invoice amount', 'align': 'right'})
            lines += [{'doc': l.name or l.account_id.display_name, 'date': '', 'amount': self._money(l.amount, v.currency_id)}
                      for l in v.line_ids if l.amount]
            d['lines'] = lines
            d['currency'] = v.currency_id
            d['totals'] = [('Total', self._money(v.amount_total, v.currency_id), False)]
            if v.withholding and v.wht_amount:
                d['totals'].append(('Withholding tax (receipt %s)' % (v.wht_certificate_no or ''), '-' + self._money(v.wht_amount, v.currency_id), False))
            d['totals'].append(('%s %s' % ('Received' if kind == 'receipt' else 'Paid', v.currency_id.name), self._money(v.amount_cash, v.currency_id), True))
            d['meta'] += [('By', ', '.join('%s%s' % (dict(r._fields['method'].selection).get(r.method), (' ' + r.reference) if r.reference else '')
                                            for r in v.cash_line_ids))]
            d['amount'] = v.amount_cash
        elif kind == 'transfer':
            d['party'] = {'label': 'From', 'name': v.cash_account_id.display_name if 'cash_account_id' in v._fields else '', 'tin': '', 'address': '', 'phone': ''}
            d['columns'] = [{'key': 'to', 'label': 'To', 'align': 'left'}, {'key': 'ref', 'label': 'Reference', 'align': 'left'},
                            {'key': 'amount', 'label': 'Amount', 'align': 'right'}]
            d['lines'] = [{'to': r.account_id.display_name, 'ref': r.reference or '', 'amount': '%s %s' % (r.row_currency_id.name, self._money(r.amount_currency, r.row_currency_id))}
                          for r in v.cash_line_ids]
            d['currency'] = v.currency_id
            d['totals'] = [('Total %s' % v.currency_id.name, self._money(v.amount_cash, v.currency_id), True)]
            d['amount'] = v.amount_cash
        else:  # journal
            d['party'] = self._party(v.partner_id, 'Partner') if v.partner_id else None
            d['columns'] = [{'key': 'acc', 'label': 'Account', 'align': 'left'}, {'key': 'desc', 'label': 'Description', 'align': 'left'},
                            {'key': 'partner', 'label': 'Partner', 'align': 'left'}, {'key': 'debit', 'label': 'Debit', 'align': 'right'},
                            {'key': 'credit', 'label': 'Credit', 'align': 'right'}]
            src = v.move_id.line_ids if v.move_id else v.line_ids
            d['lines'] = [{'acc': '%s %s' % (l.account_id.code, l.account_id.name), 'desc': l.name or '', 'partner': l.partner_id.name or '',
                           'debit': self._money(l.debit, v.currency_id) if l.debit else '', 'credit': self._money(l.credit, v.currency_id) if l.credit else ''}
                          for l in src]
            d['currency'] = v.currency_id
            d['totals'] = [('Total debit', self._money(sum(src.mapped('debit')), v.currency_id), False),
                           ('Total credit', self._money(sum(src.mapped('credit')), v.currency_id), False)]
            d['amount'] = None
        return d

    # ------------------------------------------------------------------ sales and purchase orders
    def _doc_order(self, o, kind, party_label):
        cur = o.currency_id
        d = {'record': o, 'number': o.name, 'date': o.date_order.date() if o.date_order else False, 'currency': cur,
             'status_mark': 'CANCELLED' if o.state == 'cancel' else '', 'notes': (o.note and str(o.note)) or '', 'meta': [], 'fiscal': [], 'qr': False,
             'party': self._party(o.partner_id, party_label), 'columns': COLS_ITEMS}
        lines = o.order_line.filtered(lambda l: not l.display_type)
        d['lines'] = [{'no': i + 1, 'desc': l.name or l.product_id.display_name, 'code': l.product_id.default_code or '',
                       'qty': self._qty(l.product_uom_qty if o._name == 'sale.order' else l.product_qty), 'unit': l.product_uom_id.name or '',
                       'price': self._money(l.price_unit, cur), 'tax': ', '.join(l.tax_ids.mapped('name')) or 'Exempt',
                       'amount': self._money(l.price_subtotal, cur)} for i, l in enumerate(lines)]
        d['totals'] = [('Amount before tax', self._money(o.amount_untaxed, cur), False), ('VAT', self._money(o.amount_tax, cur), False),
                       ('Total %s' % cur.name, self._money(o.amount_total, cur), True)]
        d['amount'] = o.amount_total
        if o.user_id:
            d['meta'].append(('Salesperson' if o._name == 'sale.order' else 'Buyer', o.user_id.name))
        return d

    def _doc_sale_order(self, o, kind):
        d = self._doc_order(o, kind, 'Customer')
        if o.client_order_ref:
            d['meta'].insert(0, ('Your reference', o.client_order_ref))
        if kind == 'quotation' and o.validity_date:
            d['meta'].append(('Valid until', fields.Date.to_string(o.validity_date)))
        return d

    def _doc_purchase_order(self, o, kind):
        d = self._doc_order(o, kind, 'Supplier')
        if o.partner_ref:
            d['meta'].insert(0, ('Supplier reference', o.partner_ref))
        if o.date_planned:
            d['meta'].append(('Deliver by', fields.Date.to_string(o.date_planned.date())))
        return d

    # ------------------------------------------------------------------ deliveries and stock
    def _doc_sb_delivery_batch(self, b, kind):
        d = {'record': b, 'number': b.reference or b.name, 'date': b.date, 'currency': b.company_id.currency_id, 'status_mark': 'NOT YET DELIVERED' if b.state == 'draft' else '',
             'notes': b.delivery_id.notes or '', 'fiscal': [], 'qr': False, 'party': self._party(b.partner_id, 'Deliver to'),
             'meta': [(k, v) for k, v in (('Order', b.delivery_id.sale_order_id.name or ''), ('Delivery order', b.delivery_id.name),
                                          ('Invoice', b.invoice_id.name or ''), ('Delivery Vehicle', b.vehicle or ''),
                                          ('Driver', b.driver or '')) if v],
             'columns': [{'key': 'no', 'label': '#', 'align': 'left'}, {'key': 'desc', 'label': 'Item', 'align': 'left'},
                         {'key': 'qty', 'label': 'Quantity', 'align': 'right'}, {'key': 'unit', 'label': 'Unit', 'align': 'left'}],
             'lines': [{'no': i + 1, 'desc': l.product_id.display_name, 'code': l.product_id.default_code or '', 'qty': self._qty(l.quantity),
                        'unit': l.uom_id.name or ''} for i, l in enumerate(b.line_ids)],
             'totals': [('Total quantity', self._qty(sum(b.line_ids.mapped('quantity'))), True)], 'amount': None}
        return d

    def _doc_sb_stock_voucher(self, s, kind):
        cur = s.company_id.currency_id
        valued = kind in ('grn', 'stock_count')
        cols = [{'key': 'no', 'label': '#', 'align': 'left'}, {'key': 'desc', 'label': 'Item', 'align': 'left'},
                {'key': 'qty', 'label': 'Quantity', 'align': 'right'}, {'key': 'unit', 'label': 'Unit', 'align': 'left'}]
        if valued:
            cols += [{'key': 'cost', 'label': 'Unit cost', 'align': 'right'}, {'key': 'value', 'label': 'Value', 'align': 'right'}]
        d = {'record': s, 'number': s.name if s.name != '/' else 'Draft', 'date': s.date, 'currency': cur,
             'status_mark': 'DRAFT' if s.state == 'draft' else 'VOID' if s.state == 'void' else '', 'notes': s.memo or '', 'fiscal': [], 'qr': False,
             'party': self._party(s.partner_id, 'Supplier') if s.partner_id else {'label': 'Issued to', 'name': s.department or s.received_by or '',
                                                                                  'tin': '', 'address': '', 'phone': ''},
             'meta': [(k, v) for k, v in (('Store', s.warehouse_id.name), ('To store', s.warehouse_dest_id.name if s.warehouse_dest_id else ''),
                                          ('Reference', s.reference or ''), ('Received / requested by', s.received_by or '')) if v],
             'columns': cols,
             'lines': [{'no': i + 1, 'desc': l.name or l.product_id.display_name, 'code': l.product_id.default_code or '',
                        'qty': self._qty(l.quantity if kind != 'stock_count' else (l.qty_counted - l.qty_on_hand)), 'unit': l.uom_id.name or '',
                        'cost': self._money(l.unit_cost, cur), 'value': self._money(l.value, cur)}
                       for i, l in enumerate(s.line_ids.filtered('product_id'))],
             'totals': [('Total value', self._money(sum(s.line_ids.mapped('value')), cur), True)] if valued else [], 'amount': None}
        return d
