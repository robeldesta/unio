"""Report packs (Peachtree "Report Groups"): several reports for one period, in one PDF or one workbook,
printed on demand or sent by email / Telegram every month."""
import base64
import logging
from datetime import timedelta

from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .ethiopian_calendar import fiscal_periods, fiscal_year_of, format_ethiopian
from .report_catalog import CATALOG_BY_KEY

_logger = logging.getLogger(__name__)

PERIODS = [
    ('last_month', 'Last month'),
    ('this_month', 'This month'),
    ('last_quarter', 'Last quarter'),
    ('ytd', 'Year to date'),
    ('last_fy', 'Last fiscal year'),
    ('this_fy', 'This fiscal year'),
]


def _catalog_selection(self):
    return [(e['key'], e['name']) for e in sorted(CATALOG_BY_KEY.values(), key=lambda e: e['name'])
            if e['priority'] == 1 and e['category'] != 'groups']


class SbReportPack(models.Model):
    _name = 'sb.report.pack'
    _description = 'Report Pack'
    _inherit = ['mail.thread']
    _order = 'sequence, name'

    sequence = fields.Integer(default=10)
    name = fields.Char(required=True, tracking=True)
    description = fields.Char()
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True)
    period = fields.Selection(PERIODS, default='last_month', required=True,
                              help='Worked out on the day the pack is printed or sent.')
    line_ids = fields.One2many('sb.report.pack.line', 'pack_id', string='Reports', copy=True)
    fmt = fields.Selection([('pdf', 'PDF'), ('xlsx', 'Excel'), ('both', 'PDF and Excel')], default='pdf', string='Send as')
    partner_ids = fields.Many2many('res.partner', string='Email to')
    telegram_chat = fields.Char('Telegram chat ID', help='Group or user chat ID that the bot can post to.')
    schedule = fields.Selection([('none', 'Only when I press Send'), ('monthly', 'Every month')], default='none', required=True)
    send_day = fields.Integer('Day of the Ethiopian month', default=3,
                              help='Sent on this day of each Ethiopian month (1-30), usually just after the previous month is closed.')
    last_sent = fields.Datetime(readonly=True)
    active = fields.Boolean(default=True)

    def _range(self, today=None):
        today = today or fields.Date.context_today(self)
        fy = fiscal_year_of(today)
        periods = fiscal_periods(fy)
        idx = next(i for i, p in enumerate(periods) if p[2] <= today <= p[3])
        if self.period == 'this_month':
            return periods[idx][2], periods[idx][3]
        if self.period == 'last_month':
            if idx == 0:
                p = fiscal_periods(fy - 1)[-1]
                return p[2], p[3]
            return periods[idx - 1][2], periods[idx - 1][3]
        if self.period == 'last_quarter':
            q = idx // 3
            if q == 0:
                prev = fiscal_periods(fy - 1)
                return prev[9][2], prev[11][3]
            return periods[(q - 1) * 3][2], periods[(q - 1) * 3 + 2][3]
        if self.period == 'ytd':
            return periods[0][2], today
        if self.period == 'this_fy':
            return periods[0][2], periods[-1][3]
        prev = fiscal_periods(fy - 1)
        return prev[0][2], prev[-1][3]

    # ------------------------------------------------------------------ payroll in packs
    def _payroll_lines(self):
        keys = self.env['sb.report']._payroll_report_keys()
        return self.line_ids.filtered(lambda l: (CATALOG_BY_KEY.get(l.report_key) or {}).get('report') in keys
                                      or l.report_key in keys)

    def _payroll_readers_ok(self):
        """Who receives a payroll report must be allowed to read it: every email recipient a user with the HR role
        (or payroll manager), and no Telegram chat (anyone may be in it)."""
        self.ensure_one()
        R = self.env['sb.report']
        for partner in self.partner_ids:
            users = partner.sudo().with_context(active_test=False).user_ids.filtered(lambda u: u.active and not u.share)
            if not any(R.with_user(u)._report_allowed('payroll_register') for u in users):
                return False
        return not self.telegram_chat

    @api.constrains('line_ids', 'partner_ids', 'telegram_chat')
    def _check_payroll_access(self):
        user = self.env.user  # the person saving the pack (constraints may run with superuser rights)
        R = self.env['sb.report'].with_user(user)
        for pack in self.filtered(lambda p: p._payroll_lines()):
            names = ', '.join(pack._payroll_lines().mapped(lambda l: l.title or dict(_catalog_selection(l)).get(l.report_key, l.report_key)))
            if not user._is_superuser() and not R._report_allowed('payroll_register'):
                raise ValidationError(self.env._('%(pack)s: payroll reports (%(names)s) need the HR Officer role.',
                                                 pack=pack.name, names=names))
            if not pack._payroll_readers_ok():
                raise ValidationError(self.env._(
                    '%(pack)s: payroll reports (%(names)s) can be sent only to users with the HR Officer role, and not to '
                    'a Telegram chat. Remove them from this pack or change the recipients.', pack=pack.name, names=names))

    def _owner(self):
        """The user a pack is rendered as: whoever made it (a scheduled pack runs as that person, not as the system)."""
        self.ensure_one()
        owner = self.sudo().create_uid
        return owner if owner and owner.active and not owner._is_public() else self.env.ref('base.user_root')

    def _docs(self, today=None, sending=False):
        self.ensure_one()
        if not self.line_ids:
            raise UserError(self.env._('Add at least one report to %s.', self.name))
        start, end = self._range(today)
        R = self.env['sb.report']
        if self.env.su or sending:
            # a scheduled or sent pack reads with its owner's rights, never the system's
            R = R.with_user(self._owner()).with_company(self.company_id)
        payroll_ok = not sending or self._payroll_readers_ok()
        docs = []
        for line in self.line_ids:
            entry = CATALOG_BY_KEY.get(line.report_key)
            if not entry or entry['priority'] != 1 or not R._report_allowed(entry['report']):
                continue  # a pack leaves out what the user may not see (payroll without the HR role)
            if not payroll_ok and entry['report'] in R._payroll_report_keys():
                continue  # nor sends payroll to a reader without the HR role
            opts = dict(entry['options'], date_from=fields.Date.to_string(start), date_to=fields.Date.to_string(end),
                        _title=line.title or entry['name'])
            opts.pop('mode', None)
            doc = R.get_report(entry['report'], opts)
            # print what a reader expects: sections and groups open, ledgers' detail stays summarised
            from ..controllers.main import visible_rows
            exp = {r['id'] for r in doc['rows'] if r.get('expandable') and not r.get('lazy')}
            doc['rows'] = visible_rows(doc, exp)
            docs.append(doc)
        return docs, start, end

    def _cover(self, docs, start, end):
        company = self.company_id
        return {'company': company.name, 'title': self.name,
                'subtitle': '%s to %s  |  %s to %s' % (start.strftime('%d %B %Y'), end.strftime('%d %B %Y'),
                                                      format_ethiopian(start), format_ethiopian(end))}

    def render(self, fmt, today=None, sending=False):
        """(data, filename, mimetype)"""
        self.ensure_one()
        docs, start, end = self._docs(today, sending=sending)
        if not docs:
            raise UserError(self.env._('%s has no report its reader may see.', self.name))
        cover = self._cover(docs, start, end)
        stem = '%s %s' % (self.name, format_ethiopian(end))
        if fmt == 'xlsx':
            return (self.env['sb.report'].render_xlsx(docs, cover), stem + '.xlsx',
                    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        from odoo.tools.pdf import merge_pdf
        R = self.env['sb.report']
        cover_doc = {'key': 'pack_cover', 'title': self.name, 'subtitle': cover['subtitle'], 'company': cover['company'],
                     'currency': '', 'printed': self.env['sb.report']._printed_now(),
                     'printed_ec': format_ethiopian(fields.Date.context_today(self)), 'options': {}, 'signatures': False,
                     'columns': [{'key': 'no', 'label': '#', 'type': 'text'}, {'key': 'title', 'label': 'Contents', 'type': 'text'},
                                 {'key': 'sub', 'label': 'Period', 'type': 'text'}],
                     'rows': [{'id': 'c%d' % i, 'level': 0, 'kind': 'line', 'label': str(i + 1),
                               'values': {'no': str(i + 1), 'title': d['title'], 'sub': d['subtitle']}} for i, d in enumerate(docs)]}
        pdfs = [R.render_pdf(cover_doc)] + [R.render_pdf(d) for d in docs]
        return merge_pdf(pdfs), stem + '.pdf', 'application/pdf'

    # ------------------------------------------------------------------ actions
    def action_print_pdf(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_url', 'url': '/sonador_books/pack/%d/pdf?inline=1' % self.id, 'target': 'new'}

    def action_excel(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_url', 'url': '/sonador_books/pack/%d/xlsx' % self.id, 'target': 'self'}

    def action_send(self):
        for pack in self:
            pack._send()
        return True

    def _attachments(self, today=None):
        atts = []
        fmts = ['pdf', 'xlsx'] if self.fmt == 'both' else [self.fmt]
        for f in fmts:
            data, filename, mime = self.render(f, today, sending=True)
            atts.append((filename, data, mime))
        return atts

    def _send(self, today=None):
        self.ensure_one()
        if not self.partner_ids and not self.telegram_chat:
            raise UserError(self.env._('Add email recipients or a Telegram chat to %s.', self.name))
        files = self._attachments(today)
        start, end = self._range(today)
        body = self.env._('%(name)s for %(a)s to %(b)s is attached.', name=self.name, a=format_ethiopian(start), b=format_ethiopian(end))
        if self.partner_ids:
            attachments = self.env['ir.attachment'].create([{
                'name': fn, 'datas': base64.b64encode(data), 'mimetype': mime, 'res_model': self._name, 'res_id': self.id}
                for fn, data, mime in files])
            self.message_post(body=body, subject=self.name, partner_ids=self.partner_ids.ids,
                              attachment_ids=attachments.ids, message_type='comment', subtype_xmlid='mail.mt_comment')
        if self.telegram_chat:
            self._send_telegram(files, body)
        self.last_sent = fields.Datetime.now()

    def _send_telegram(self, files, caption):
        token = self.company_id.sb_telegram_token
        if not token:
            raise UserError(self.env._('Set the Telegram bot token in Accounting settings first.'))
        import requests
        for fn, data, mime in files:
            resp = requests.post('https://api.telegram.org/bot%s/sendDocument' % token,
                                 data={'chat_id': self.telegram_chat, 'caption': caption[:1000]},
                                 files={'document': (fn, data, mime)}, timeout=60)
            if resp.status_code != 200 or not resp.json().get('ok'):
                raise UserError(self.env._('Telegram refused the message: %s', resp.text[:300]))

    @api.model
    def _cron_send(self):
        today = fields.Date.context_today(self)
        from .ethiopian_calendar import to_ethiopian
        day = to_ethiopian(today)[2]
        for pack in self.search([('schedule', '=', 'monthly'), ('send_day', '=', day)]):
            if pack.last_sent and pack.last_sent.date() == today:
                continue
            try:
                pack._send(today)
                self.env.cr.commit()
            except Exception as e:  # one failing pack must not stop the others
                self.env.cr.rollback()
                _logger.warning('Report pack %s not sent: %s', pack.name, e)
                pack.message_post(body=self.env._('Could not send: %s', e))


class SbReportPackLine(models.Model):
    _name = 'sb.report.pack.line'
    _description = 'Report in a pack'
    _order = 'sequence, id'

    pack_id = fields.Many2one('sb.report.pack', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    report_key = fields.Selection(_catalog_selection, string='Report', required=True)
    title = fields.Char('Title in the pack', help='Leave empty to use the report name.')


class ResCompanyTelegram(models.Model):
    _inherit = 'res.company'

    sb_telegram_token = fields.Char('Telegram bot token', groups='base.group_system',
                                    help='Token of the bot that posts report packs (from @BotFather).')
