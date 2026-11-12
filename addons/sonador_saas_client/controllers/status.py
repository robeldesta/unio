from datetime import date
from urllib.parse import urlencode

from odoo import fields, http
from odoo.http import request


class SaasStatus(http.Controller):

    @http.route('/saas/status', type='jsonrpc', auth='user')
    def status(self):
        P = request.env['ir.config_parameter'].sudo()
        if not P.get_param('saas.tenant'):
            return {'cloud': False}
        end = P.get_param('saas.trial_end')
        left = (fields.Date.to_date(end) - date.today()).days if end else None
        users = request.env['res.users'].sudo().search_count([('share', '=', False), ('active', '=', True), ('id', '!=', 1)]) or 1
        base = P.get_param('saas.subscribe_url', 'https://unio.et/subscribe.html')
        url = '%s?%s' % (base, urlencode({'c': P.get_param('saas.tenant'), 'u': users, 'p': P.get_param('saas.billing') or 'monthly'}))
        state = P.get_param('saas.plan_state', 'trial')
        until = P.get_param('saas.paid_until')
        renew_in = (fields.Date.to_date(until) - date.today()).days if until and state in ('active', 'overdue') else None
        return {'cloud': True, 'locked': P.get_param('saas.locked') == 'True', 'trial': state == 'trial', 'overdue': state == 'overdue',
                'renew_in': renew_in if renew_in is not None and renew_in <= 7 else None, 'days_left': left, 'subscribe_url': url}
