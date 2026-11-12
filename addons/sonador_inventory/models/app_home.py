"""Home pages of the departmental apps (Inventory for the store, Sales for the commercial department).

Each app shows its task flow and the work waiting for the department. Modules add to an app by extending
`_home_<app>` (sales adds deliveries to the store page, manufacturing adds FPRRs, Eco Green adds van trips).
"""
from odoo import api, fields, models

I = 'sonador_inventory.%s'


def tile(label, icon, action, create=False, hint=''):
    return {'label': label, 'icon': icon, 'action': action, 'create': create, 'hint': hint}


class SbAppHome(models.AbstractModel):
    _name = 'sb.app.home'
    _description = 'Department app home'

    @api.model
    def get_home(self, app):
        data = getattr(self, '_home_%s' % app)()
        data['queues'] = [q for q in data['queues'] if q]
        return data

    @api.model
    def _queue(self, key, title, icon, model, domain, rows, label, sub=None, value=None, tone=None, limit=8, order=None,
               action_name=None):
        Model = self.env[model]
        if not Model.has_access('read'):
            return None
        count = Model.search_count(domain)
        recs = Model.search(domain, limit=limit, order=order) if count else Model
        return {'key': key, 'title': title, 'icon': icon, 'count': count, 'tone': tone if count else None,
                'model': model, 'domain': domain, 'action_name': action_name or title,
                'rows': [{'id': r.id, 'label': label(r), 'sub': sub(r) if sub else '', 'value': value(r) if value else ''}
                         for r in recs]}

    # ------------------------------------------------------------------ store
    @api.model
    def _home_store(self):
        company = self.env.company
        today = fields.Date.context_today(self)
        SV = 'sb.stock.voucher'
        base = [('company_id', '=', company.id)]
        queues = [
            self._queue('drafts', 'Documents not posted', 'fa-pencil-square-o', SV,
                        base + [('state', '=', 'draft'), ('voucher_type', 'in', self._store_types())],
                        lambda v: '%s %s' % (v._type_label(), v.name if v.name != '/' else ''),
                        lambda v: v.date_ec, tone='warn', order='date, id'),
            self._queue('sent_back', 'Sent back by finance', 'fa-reply', SV,
                        base + [('state', '=', 'draft'), ('sb_sent_finance_at', '=', False), ('sb_sent_back_reason', '!=', False)],
                        None, lambda v: '%s %s' % (v._type_label(), v.date_ec or ''),
                        lambda v: v.sb_sent_back_reason, tone='bad', order='date, id'),
            self._queue('gate_issued', 'Gate passes waiting at the gate', 'fa-ticket', 'sb.gate.pass',
                        base + [('state', '=', 'issued')], lambda g: g.name,
                        lambda g: ' · '.join(filter(None, [dict(g._fields['purpose'].selection).get(g.purpose), g.vehicle, g.driver])),
                        order='date, id'),
            self._queue('gate_returnable', 'Returnable items still out', 'fa-undo', 'sb.gate.pass',
                        base + [('state', '=', 'out'), ('purpose', '=', 'returnable')], lambda g: g.name,
                        lambda g: '%s · back by %s' % (g.partner_id.name or g.destination or '', g.expected_return or ''),
                        tone='bad' if self.env['sb.gate.pass'].search_count(
                            base + [('state', '=', 'out'), ('purpose', '=', 'returnable'), ('expected_return', '<', today)]) else 'warn',
                        order='expected_return'),
            self._reorder_queue(),
            self._queue('today', 'Posted today', 'fa-check', SV,
                        base + [('state', '=', 'posted'), ('date', '=', today), ('voucher_type', 'in', self._store_types())],
                        lambda v: v.name, lambda v: v._type_label(), order='id desc'),
        ]
        return {
            'title': 'Inventory', 'subtitle': 'Store: receive, issue, dispatch, gate passes', 'icon': 'fa-cubes',
            'flows': [
                [tile('Receive (GRN)', 'fa-sign-in', I % 'action_sb_grn', True, 'from suppliers')],
                [tile('Issue (GIV)', 'fa-sign-out', I % 'action_sb_giv', True, 'to departments, sites'),
                 tile('Transfer', 'fa-exchange', I % 'action_sb_stock_transfer', True, 'between stores')],
                [tile('Gate Pass', 'fa-ticket', I % 'action_sb_gate_pass', True, 'out through the gate')],
            ],
            'more': [tile('Items', 'fa-cube', 'sonador_books.action_sb_products'),
                     tile('Gate Passes', 'fa-list', I % 'action_sb_gate_pass')],
            'reports': ['stock_card', 'stock_by_wh', 'count_sheet', 'reorder', 'grn_register', 'giv_register'],
            'queues': queues,
        }

    @api.model
    def _store_types(self):
        return ['grn', 'giv', 'transfer']

    @api.model
    def _reorder_queue(self):
        Product = self.env['product.product']
        if 'sb_reorder_point' not in Product._fields:
            return None
        products = Product.search([('is_storable', '=', True), ('sb_reorder_point', '>', 0)])
        low = products.filtered(lambda p: p.qty_available <= p.sb_reorder_point)
        return {'key': 'reorder', 'title': 'At or below reorder point', 'icon': 'fa-exclamation-triangle',
                'count': len(low), 'tone': 'bad' if low else None, 'model': 'product.product',
                'domain': [('id', 'in', low.ids)], 'action_name': 'Items to reorder',
                'rows': [{'id': p.id, 'label': p.display_name, 'sub': 'reorder at %s' % p.sb_reorder_point,
                          'value': '%s %s' % ('{:,.2f}'.format(p.qty_available), p.uom_id.name)} for p in low[:8]]}
