"""CRM reports on the engine: the prospect list (open deals and leads)."""
from odoo import fields, models


class SbReportCrm(models.AbstractModel):
    _inherit = 'sb.report'

    def _report_prospects(self, opts):
        """Open deals: customer or prospect, stage, value, chance, expected close and owner."""
        rows, grand, weighted = [], 0.0, 0.0
        deals = self.env['sb.deal'].search([('company_id', '=', self.env.company.id), ('state', 'not in', ('won', 'lost'))], order='close_date, id')
        for d in deals:
            if not self._search_ok(opts, d.name, d.partner_id.name, d.owner_id.name):
                continue
            grand += d.amount
            weighted += d.amount * (d.probability or 0) / 100.0
            rows.append({'id': 'd%d' % d.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': d.name,
                         'values': {'name': d.name, 'who': d.partner_id.name or '', 'phone': d.partner_id.phone or '', 'stage': d.stage_id.name,
                                    'amount': d.amount, 'chance': d.probability, 'close': fields.Date.to_string(d.close_date) or '',
                                    'owner': d.owner_id.name or ''}, 'open': {'model': 'sb.deal', 'id': d.id}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total',
                     'values': {'name': 'Total (weighted %s)' % '{:,.0f}'.format(weighted), 'amount': grand}})
        cols = [{'key': 'name', 'label': 'Deal', 'type': 'text'}, {'key': 'who', 'label': 'Customer / prospect', 'type': 'text'},
                {'key': 'phone', 'label': 'Phone', 'type': 'text'}, {'key': 'stage', 'label': 'Stage', 'type': 'text'},
                {'key': 'amount', 'label': 'Value', 'type': 'monetary'}, {'key': 'chance', 'label': 'Chance %', 'type': 'number'},
                {'key': 'close', 'label': 'Expected close', 'type': 'date'}, {'key': 'owner', 'label': 'Owner', 'type': 'text'}]
        return self._doc('prospects', opts, cols, rows, 'Open deals', 'Prospect List')
