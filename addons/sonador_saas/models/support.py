"""Help inside every customer system: search the help articles, read them, ask us when they do not answer.

Customer systems run in the same server as the platform: their "?" panel reaches these models through the platform
database directly (sonador_saas_client, saas.help), so no password or token travels. Every search is logged, so the
questions the articles do not answer show up in HQ."""
import re

from markupsafe import Markup, escape

from odoo import api, fields, models

STOP = {'the', 'a', 'an', 'to', 'of', 'in', 'on', 'for', 'how', 'do', 'i', 'my', 'is', 'can', 'what', 'and', 'or', 'with', 'it', 'be', 'by', 'at'}


def _words(text):
    return [w for w in re.findall(r'[a-z0-9ሀ-፿]+', (text or '').lower()) if w not in STOP and len(w) > 1]


class SaasArticle(models.Model):
    _name = 'saas.article'
    _description = 'Help article'
    _order = 'sequence, id'

    title = fields.Char(required=True, translate=True)
    app = fields.Char(help='The app it is about (accounting, sales, inventory...).')
    keywords = fields.Char(help='Other words people use for it, comma-separated.')
    body = fields.Html(sanitize=True, translate=True)
    sequence = fields.Integer(default=10)
    published = fields.Boolean(default=True)
    views = fields.Integer(readonly=True)
    helpful = fields.Integer(readonly=True)
    not_helpful = fields.Integer(readonly=True)
    tour = fields.Char(help='The guided tour that shows it on screen (the key of a tour in the customer system).')

    TOURS = {'article_01': 'invoice', 'article_02': 'receipt', 'article_03': 'bill', 'article_04': 'reports', 'article_05': 'reports',
             'article_08': 'payroll', 'article_09': 'employee', 'article_11': 'quotation', 'article_12': 'grn', 'article_13': 'count',
             'article_14': 'print', 'article_15': 'apps', 'article_16': 'studio', 'article_21': 'website'}

    @api.model
    def _set_tours(self):
        for xmlid, tour in self.TOURS.items():
            a = self.env.ref('sonador_saas.' + xmlid, raise_if_not_found=False)
            if a and not a.tour:
                a.tour = tour

    @api.model
    def search_help(self, query, limit=8):
        words = _words(query)
        if not words:
            return []
        scored = []
        for a in self.sudo().search([('published', '=', True)]):
            title, keys = _words(a.title), _words(a.keywords)
            body = _words(re.sub(r'<[^>]+>', ' ', str(a.body or '')))
            score = 0
            for w in words:
                score += 6 * sum(1 for t in title if t.startswith(w)) + 4 * sum(1 for k in keys if k.startswith(w)) + min(sum(1 for b in body if b.startswith(w)), 3)
            if score:
                scored.append((score, a))
        scored.sort(key=lambda x: -x[0])
        return [{'id': a.id, 'title': a.title, 'app': a.app or '', 'snippet': self._snippet(a, words)} for _s, a in scored[:limit]]

    def _snippet(self, a, words):
        import html as htmllib
        text = htmllib.unescape(re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', str(a.body or ''))).strip())
        text = re.sub(r'\s+([,.:;)])', r'\1', text)
        low = text.lower()
        pos = min([low.find(w) for w in words if low.find(w) >= 0] or [0])
        start = max(pos - 60, 0)
        return ('…' if start else '') + text[start:start + 180] + ('…' if len(text) > start + 180 else '')


    @api.model
    def ai_answer(self, database, query):
        """An answer written from the help articles (cited), for the "?" panel."""
        found = self.search_help(query, limit=5)
        arts = self.sudo().browse([f['id'] for f in found])
        context = '\n\n'.join('[%d] %s\n%s' % (a.id, a.title, re.sub(r'<[^>]+>', ' ', str(a.body or ''))) for a in arts)
        data = self.env['saas.ai'].sudo().call(database, 'help', {
            'max_tokens': 700,
            'system': 'You answer questions about Unio, a business system (accounting, sales, stock, payroll...), for its users. '
                      'Use only the help articles given; if they do not cover it, say so and suggest asking support. '
                      'Be short and practical, with steps. Cite the articles you used like [3]. Answer in the language of the question.',
            'messages': [{'role': 'user', 'content': 'Help articles:\n%s\n\nQuestion: %s' % (context or '(none found)', query)}]})
        text = '\n'.join(b.get('text', '') for b in data.get('content') or [] if b.get('type') == 'text').strip()
        return {'answer': text, 'articles': [{'id': a.id, 'title': a.title} for a in arts]}


class SaasHelpSearch(models.Model):
    _name = 'saas.help.search'
    _description = 'Search in the help'
    _order = 'id desc'

    tenant_id = fields.Many2one('saas.tenant', ondelete='set null', index=True)
    query = fields.Char(index=True)
    results = fields.Integer()
    opened_id = fields.Many2one('saas.article', ondelete='set null')


class SaasTicket(models.Model):
    _name = 'saas.ticket'
    _description = 'Question from a customer'
    _inherit = ['mail.thread']
    _order = 'state_order, id desc'

    name = fields.Char('Subject', required=True, tracking=True)
    tenant_id = fields.Many2one('saas.tenant', ondelete='set null', index=True)
    user_name = fields.Char('Asked by')
    user_email = fields.Char()
    question = fields.Text()
    page = fields.Char(help='Where in the system the person was.')
    state = fields.Selection([('new', 'New'), ('answered', 'Answered'), ('closed', 'Closed')], default='new', tracking=True, index=True)
    state_order = fields.Integer(compute='_compute_state_order', store=True)
    answer = fields.Html(sanitize=True)
    answered_on = fields.Datetime(readonly=True)
    answered_by = fields.Many2one('res.users', readonly=True)
    first_answer_minutes = fields.Integer(readonly=True)
    read_by_customer = fields.Boolean(readonly=True)

    @api.depends('state')
    def _compute_state_order(self):
        for t in self:
            t.state_order = {'new': 0, 'answered': 1, 'closed': 2}.get(t.state, 3)

    @api.model
    def ask(self, subdomain, user_name, user_email, subject, question, page):
        tenant = self.env['saas.tenant'].sudo().search([('subdomain', '=', subdomain)], limit=1)
        t = self.sudo().create({'name': (subject or question or 'Question')[:120], 'tenant_id': tenant.id, 'user_name': user_name,
                                'user_email': user_email, 'question': question, 'page': (page or '')[:300]})
        t.message_post(body=Markup('<p><b>%s</b> (%s) asks:</p><p>%s</p>') % (user_name, tenant.name or subdomain, question))
        sales = self.env['ir.config_parameter'].sudo().get_param('saas.support_email') or self.env['ir.config_parameter'].sudo().get_param('saas.sales_email')
        if sales:
            self.env['mail.mail'].sudo().create({
                'subject': 'Question from %s: %s' % (tenant.name or subdomain, t.name), 'email_to': sales,
                'reply_to': user_email or False,
                'email_from': self.env['ir.config_parameter'].sudo().get_param('saas.mail_from') or 'Unio <noreply@unio.et>',
                'body_html': Markup('<p>%s (%s, %s) asks, on <i>%s</i>:</p><p>%s</p><p>Answer it in Unio HQ, Support.</p>') % (
                    user_name, user_email, tenant.name or subdomain, page or '', question)}).send(raise_exception=False)
        return t.id

    @api.model
    def mine(self, subdomain, user_email):
        tenant = self.env['saas.tenant'].sudo().search([('subdomain', '=', subdomain)], limit=1)
        out = []
        for t in self.sudo().search([('tenant_id', '=', tenant.id), ('user_email', '=', user_email)], limit=30):
            out.append({'id': t.id, 'subject': t.name, 'question': t.question, 'state': t.state, 'answer': str(t.answer or ''),
                        'date': fields.Datetime.to_string(t.create_date)[:16], 'unread': t.state == 'answered' and not t.read_by_customer})
        return out

    @api.model
    def mark_read(self, subdomain, user_email, ticket_id):
        t = self.sudo().browse(ticket_id)
        if t.tenant_id.subdomain == subdomain and t.user_email == user_email:
            t.read_by_customer = True
        return True

    def action_answer(self, answer_html):
        """From HQ: answer the question (shown in the customer's help panel and sent by email)."""
        for t in self:
            first = not t.answered_on
            t.write({'answer': answer_html, 'state': 'answered', 'answered_on': fields.Datetime.now(), 'answered_by': self.env.user.id,
                     'read_by_customer': False})
            if first:
                t.first_answer_minutes = int((t.answered_on - t.create_date).total_seconds() // 60)
            t.message_post(body=Markup('<p><b>Answer:</b></p>') + Markup(answer_html))
            if t.user_email:
                P = self.env['ir.config_parameter'].sudo()
                self.env['mail.mail'].sudo().create({
                    'subject': 'Re: %s' % t.name, 'email_to': t.user_email,
                    'email_from': P.get_param('saas.mail_from') or 'Unio <noreply@unio.et>',
                    'reply_to': P.get_param('saas.support_email') or 'support@unio.et',
                    'body_html': Markup('<div style="font-family:Arial,sans-serif;color:#1B2240"><p>Hello %s,</p>%s'
                                        '<p style="color:#6B7286;font-size:13px">Your question: %s</p><p>Unio support</p></div>') % (
                        t.user_name or '', Markup(answer_html), t.question or '')}).send(raise_exception=False)
        return True

    @api.model
    def hq_summary(self):
        S = self.sudo()
        now = fields.Datetime.now()
        from datetime import timedelta
        answered = S.search([('first_answer_minutes', '>', 0), ('create_date', '>', now - timedelta(days=30))])
        searches = self.env['saas.help.search'].sudo()
        missed = searches.search([('results', '=', 0)], limit=40)
        top = {}
        for s in searches.search([('create_date', '>', now - timedelta(days=30))]):
            q = (s.query or '').strip().lower()
            if q:
                top[q] = top.get(q, 0) + 1
        return {
            'open': S.search_count([('state', '=', 'new')]),
            'answered_30d': S.search_count([('state', 'in', ('answered', 'closed')), ('create_date', '>', now - timedelta(days=30))]),
            'avg_minutes': int(sum(answered.mapped('first_answer_minutes')) / len(answered)) if answered else None,
            'tickets': [{'id': t.id, 'subject': t.name, 'company': t.tenant_id.name or '', 'who': t.user_name or '', 'email': t.user_email or '',
                         'question': t.question or '', 'page': t.page or '', 'state': t.state, 'answer': str(t.answer or ''),
                         'date': fields.Datetime.to_string(t.create_date)[:16]} for t in S.search([], limit=60)],
            'missed': [{'query': s.query, 'company': s.tenant_id.name or '', 'date': fields.Datetime.to_string(s.create_date)[:16]} for s in missed],
            'top': sorted([{'query': q, 'n': n} for q, n in top.items()], key=lambda x: -x['n'])[:15],
            'articles': self.env['saas.article'].sudo().search_count([('published', '=', True)]),
        }

    @api.model
    def hq_answer(self, ticket_id, text):
        """HQ answers in plain text; paragraphs are kept."""
        html = Markup('').join(Markup('<p>%s</p>') % escape(p) for p in (text or '').split('\n\n') if p.strip())
        self.sudo().browse(ticket_id).action_answer(str(html))
        return self.hq_summary()


class SaasTourEvent(models.Model):
    """Guided tours taken in the customer systems: which are offered, started, finished, skipped (and where people stop)."""
    _name = 'saas.tour.event'
    _description = 'Guided tour taken'
    _order = 'id desc'

    tenant_id = fields.Many2one('saas.tenant', ondelete='cascade', index=True)
    tour = fields.Char(index=True)
    step = fields.Integer()
    event = fields.Selection([('offer', 'Offered'), ('decline', 'Not now'), ('start', 'Started'), ('done', 'Finished'), ('skip', 'Skipped')], index=True)
    login = fields.Char()

    @api.model
    def log(self, subdomain, tour, step, event, login):
        tenant = self.env['saas.tenant'].sudo().search([('subdomain', '=', subdomain)], limit=1)
        self.sudo().create({'tenant_id': tenant.id, 'tour': tour, 'step': step, 'event': event, 'login': (login or '')[:120]})
        return True

    @api.model
    def hq_summary(self):
        from datetime import timedelta
        rows = self.sudo().search([('create_date', '>', fields.Datetime.now() - timedelta(days=90))])
        per = {}
        for r in rows:
            d = per.setdefault(r.tour, {'tour': r.tour, 'offer': 0, 'decline': 0, 'start': 0, 'done': 0, 'skip': 0, 'stops': {}})
            d[r.event] += 1
            if r.event == 'skip':
                d['stops'][r.step + 1] = d['stops'].get(r.step + 1, 0) + 1
        out = []
        for d in per.values():
            stop = max(d['stops'].items(), key=lambda x: x[1])[0] if d['stops'] else None
            out.append(dict(d, stops=None, finish_pct=round(100 * d['done'] / d['start']) if d['start'] else None, stop_step=stop))
        return sorted(out, key=lambda x: -x['start'])
