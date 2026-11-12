"""Purchase app (procurement) and its share of the Inventory and Books screens."""
from odoo import api, fields, models

P = 'sonador_purchase.%s'
B = 'sonador_books.%s'
I = 'sonador_inventory.%s'


def tile(label, icon, action, create=False, hint=''):
    return {'label': label, 'icon': icon, 'action': action, 'create': create, 'hint': hint}


class SbAppHome(models.AbstractModel):
    _inherit = 'sb.app.home'

    @api.model
    def _home_purchase(self):
        company = self.env.company
        now = fields.Datetime.now()
        base = [('company_id', '=', company.id)]
        PO = self.env['purchase.order']
        money = lambda x: '{:,.2f}'.format(x)  # noqa: E731
        books = self.env.user.has_group('sonador_books.group_sb_clerk')
        open_pos = PO.search(base + [('state', '=', 'purchase'), ('sb_receipt_status', 'in', ('none', 'partial'))])
        advances = open_pos.filtered(lambda o: o.sb_payment_status in ('advance', 'paid', 'partial') and o.sb_amount_paid > 0)
        unbilled = self.env['sb.stock.voucher'].search(base + [('voucher_type', '=', 'grn'), ('state', '=', 'posted'),
                                                               ('billed', '=', False), ('sb_purchase_id', '!=', False)])
        queues = [
            self._queue('rfq', 'Requests for quotation', 'fa-file-text-o', 'purchase.order', base + [('state', 'in', ('draft', 'sent'))],
                        lambda o: o.name, lambda o: o.partner_id.name, lambda o: money(o.amount_total), order='date_order desc'),
            self._queue('approve', 'Waiting for approval', 'fa-gavel', 'purchase.order', base + [('state', '=', 'to approve')],
                        lambda o: o.name, lambda o: o.partner_id.name, lambda o: money(o.amount_total), tone='warn'),
            self._queue('late', 'Late deliveries', 'fa-clock-o', 'purchase.order',
                        [('id', 'in', open_pos.filtered(lambda o: o.date_planned and o.date_planned < now).ids)],
                        lambda o: o.name, lambda o: '%s · due %s' % (o.partner_id.name, fields.Date.to_string(o.date_planned.date())),
                        lambda o: dict(o._fields['sb_receipt_status'].selection).get(o.sb_receipt_status), tone='bad',
                        order='date_planned'),
            self._queue('to_receive', 'Still to receive', 'fa-truck', 'purchase.order', [('id', 'in', open_pos.ids)],
                        lambda o: o.name, lambda o: o.partner_id.name,
                        lambda o: dict(o._fields['sb_receipt_status'].selection).get(o.sb_receipt_status), order='date_planned'),
            self._queue('advances', 'Paid, goods not all in', 'fa-exclamation-circle', 'purchase.order', [('id', 'in', advances.ids)],
                        lambda o: o.name, lambda o: o.partner_id.name, lambda o: money(o.sb_amount_paid), tone='warn'),
            self._queue('to_bill', 'Received, not invoiced', 'fa-file-text', 'sb.stock.voucher', [('id', 'in', unbilled.ids)],
                        lambda g: g.name, lambda g: '%s · %s' % (g.partner_id.name, g.sb_purchase_id.name),
                        lambda g: money(g.amount_total), order='date'),
        ]
        flows = [
            [tile('Request for Quotation', 'fa-file-text-o', P % 'action_sb_rfq', True)],
            [tile('Purchase Order', 'fa-shopping-cart', P % 'action_sb_purchase_orders', False, 'confirm, approve')],
        ]
        if books:
            flows.append([tile('Advance Payment', 'fa-upload', B % 'action_sb_payment', True, 'CPV before delivery')])
        flows.append([tile('Receive (GRN)', 'fa-sign-in', I % 'action_sb_grn', True, 'in batches, against the order')])
        if books:
            flows.append([tile('Purchase Voucher', 'fa-file-text', B % 'action_sb_bill', True, 'clears the GRNs'),
                          tile('Pay', 'fa-money', B % 'action_sb_payment', True, 'CPV')])
        return {
            'title': 'Purchase', 'subtitle': 'Requests for quotation, purchase orders, deliveries, purchase vouchers and payments',
            'icon': 'fa-shopping-cart',
            'kpis': [{'label': 'Open orders', 'value': len(open_pos)},
                     {'label': 'Paid, goods not all in', 'value': money(sum(advances.mapped('sb_amount_paid')))}],
            'flows': flows,
            'more': [tile('Vendors', 'fa-truck', B % 'action_sb_vendors'),
                     tile('Vendor Prices', 'fa-tags', P % 'action_sb_vendor_prices'),
                     tile('GRNs', 'fa-list', P % 'action_sb_po_grns')],
            'reports': ['po_status', 'po_payments', 'grn_register', 'grni'],
            'queues': queues,
        }

    @api.model
    def _home_store(self):
        data = super()._home_store()
        now = fields.Datetime.now()
        pos = self.env['purchase.order'].search([('company_id', '=', self.env.company.id), ('state', '=', 'purchase'),
                                                 ('sb_receipt_status', 'in', ('none', 'partial'))], order='date_planned')
        data['flows'][0].insert(0, tile('Purchase Orders', 'fa-shopping-cart', P % 'action_sb_purchase_orders', False,
                                        'receive against the order'))
        data['queues'].insert(0, {
            'key': 'expected', 'title': 'Expected from suppliers', 'icon': 'fa-shopping-cart', 'count': len(pos),
            'tone': 'bad' if pos.filtered(lambda o: o.date_planned and o.date_planned < now) else None,
            'model': 'purchase.order', 'domain': [('id', 'in', pos.ids)], 'action_name': 'Expected from suppliers',
            'rows': [{'id': o.id, 'label': o.name,
                      'sub': '%s · due %s' % (o.partner_id.name, fields.Date.to_string(o.date_planned.date()) if o.date_planned else '—'),
                      'value': dict(o._fields['sb_receipt_status'].selection).get(o.sb_receipt_status)} for o in pos[:8]]})
        data['reports'] = data['reports'] + ['po_status']
        return data
