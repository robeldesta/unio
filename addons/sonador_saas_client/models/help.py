"""The "?" panel: the help articles and questions to Unio support, kept in the platform database (same server)."""
from contextlib import contextmanager

import odoo
from odoo import api, models
from odoo.exceptions import UserError


class SaasHelp(models.AbstractModel):
    _name = 'saas.help'
    _description = 'Help and support'

    @contextmanager
    def _platform(self):
        P = self.env['ir.config_parameter'].sudo()
        db = P.get_param('saas.platform_db')
        if not db:
            raise UserError(self.env._('Help is not available on this system.'))
        registry = odoo.modules.registry.Registry(db)
        with registry.cursor() as cr:
            yield api.Environment(cr, odoo.SUPERUSER_ID, {}), P.get_param('saas.tenant')

    @api.model
    def available(self):
        return bool(self.env['ir.config_parameter'].sudo().get_param('saas.platform_db'))

    @api.model
    def search(self, query):
        with self._platform() as (penv, sub):
            res = penv['saas.article'].search_help(query)
            tenant = penv['saas.tenant'].search([('subdomain', '=', sub)], limit=1)
            if (query or '').strip():
                penv['saas.help.search'].create({'tenant_id': tenant.id, 'query': query.strip()[:200], 'results': len(res)})
            return res

    @api.model
    def ai_on(self):
        with self._platform() as (penv, _sub):
            return 'saas.ai' in penv and penv['saas.ai'].enabled()

    @api.model
    def ai_answer(self, query):
        with self._platform() as (penv, sub):
            return penv['saas.article'].ai_answer(sub, query)

    @api.model
    def article(self, article_id):
        with self._platform() as (penv, _sub):
            a = penv['saas.article'].browse(article_id).exists()
            if not a or not a.published:
                return {}
            a.views += 1
            return {'id': a.id, 'title': a.title, 'body': str(a.body or ''), 'app': a.app or '', 'tour': a.tour if 'tour' in a._fields else False}

    @api.model
    def vote(self, article_id, helpful):
        with self._platform() as (penv, _sub):
            a = penv['saas.article'].browse(article_id).exists()
            if a:
                if helpful:
                    a.helpful += 1
                else:
                    a.not_helpful += 1
        return True

    @api.model
    def ask(self, subject, question, page):
        if not (question or '').strip():
            raise UserError(self.env._('Write your question.'))
        u = self.env.user
        with self._platform() as (penv, sub):
            penv['saas.ticket'].ask(sub, u.name, u.email or u.login, subject, question.strip(), page)
        return self.mine()

    @api.model
    def mine(self):
        u = self.env.user
        with self._platform() as (penv, sub):
            return penv['saas.ticket'].mine(sub, u.email or u.login)

    @api.model
    def mark_read(self, ticket_id):
        u = self.env.user
        with self._platform() as (penv, sub):
            penv['saas.ticket'].mark_read(sub, u.email or u.login, ticket_id)
        return True
