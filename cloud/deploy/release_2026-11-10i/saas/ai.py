"""Unio AI for customer systems on their own server: the same gateway over HTTPS, with the system's token."""
import json

from odoo import http
from odoo.exceptions import UserError
from odoo.http import request


class UnioAiGateway(http.Controller):

    def _client(self):
        auth = request.httprequest.headers.get('Authorization', '')
        token = auth[7:] if auth.startswith('Bearer ') else ''
        return request.env['saas.ai.client'].sudo().by_token(token)

    def _reply(self, body, status=200):
        return request.make_response(json.dumps(body), headers=[('Content-Type', 'application/json')], status=status)

    @http.route('/saas/ai/v1/<string:what>', type='http', auth='none', methods=['POST'], csrf=False, save_session=False)
    def gateway(self, what, **kw):
        client = self._client()
        if not client:
            return self._reply({'error': 'unknown system'}, 401)
        try:
            data = json.loads(request.httprequest.get_data() or b'{}')
        except ValueError:
            return self._reply({'error': 'bad request'}, 400)
        user = (data.get('user') or '')[:120] or False
        ai = request.env['saas.ai'].sudo()
        try:
            if what == 'status':
                return self._reply(ai.status(client.database, user))
            if what == 'call':
                feature = data.get('feature') if data.get('feature') in ('assistant', 'studio', 'help') else 'assistant'
                rid = (data.get('request_id') or '')[:64]
                Reply = request.env['saas.ai.reply'].sudo()
                if rid:
                    # asked again after the answer was lost on the way back: the same answer, not charged again
                    kept = Reply.search([('database', '=', client.database), ('request_id', '=', rid)], limit=1)
                    if kept:
                        return self._reply(json.loads(kept.response))
                res = ai.call(client.database, feature, data.get('payload') or {}, user)
                if rid:
                    Reply.create({'database': client.database, 'request_id': rid, 'response': json.dumps(res)})
                return self._reply(res)
        except UserError as e:
            return self._reply({'error': str(e)}, 429)
        return self._reply({'error': 'not found'}, 404)
