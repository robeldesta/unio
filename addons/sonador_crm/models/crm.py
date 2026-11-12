"""CRM records: Ethiopian woredas, contacts and companies (res.partner), pipelines and deals, the activity timeline
(notes, calls, meetings, field visits, Telegram / SMS, tasks) and monthly targets."""
import re

from odoo import api, fields, models
from odoo.exceptions import ValidationError

LIFECYCLE = [('prospect', 'Prospect'), ('contacted', 'Contacted'), ('qualified', 'Qualified'), ('customer', 'Customer'),
             ('repeat', 'Repeat customer'), ('inactive', 'Inactive')]
CTYPES = [('smallholder', 'Smallholder farmer'), ('commercial_farm', 'Commercial farm'), ('union', 'Cooperative union'),
          ('cooperative', 'Primary cooperative'), ('agro_dealer', 'Agro-dealer'), ('distributor', 'Distributor / wholesaler'),
          ('government', 'Government (BoA / office)'), ('ngo', 'NGO / project'), ('aoss', 'AOSS'), ('enterprise', 'Company'), ('other', 'Other')]
CHANNELS = [('retailer', 'Retailer'), ('wholesaler', 'Wholesaler'), ('end_user', 'End user')]
SOURCES = [('field_visit', 'Field visit'), ('referral', 'Referral'), ('expo', 'Expo / trade fair'), ('telegram', 'Telegram'),
           ('phone_in', 'Phoned in'), ('walk_in', 'Walk-in'), ('social', 'Facebook / TikTok'), ('radio_tv', 'Radio / TV'),
           ('tender', 'Tender'), ('existing', 'Existing customer'), ('other', 'Other')]
LANGUAGES = [('am', 'Amharic'), ('om', 'Afaan Oromo'), ('ti', 'Tigrinya'), ('so', 'Somali'), ('sid', 'Sidaamu Afoo'),
             ('wal', 'Wolaytta'), ('aa', 'Afar'), ('en', 'English'), ('lg', 'Luganda'), ('sw', 'Swahili'), ('nyn', 'Runyankole'),
             ('luo', 'Luo'), ('other', 'Other')]
LANGUAGES_BY_COUNTRY = {'ET': ['am', 'om', 'ti', 'so', 'sid', 'wal', 'aa', 'en', 'other'], 'UG': ['en', 'lg', 'sw', 'nyn', 'luo', 'other']}
KINDS = [('note', 'Note'), ('call', 'Call'), ('meeting', 'Meeting'), ('visit', 'Field visit'), ('telegram', 'Telegram'),
         ('sms', 'SMS'), ('email', 'Email'), ('task', 'Task')]
OUTCOMES = [('connected', 'Talked'), ('no_answer', 'No answer'), ('busy', 'Busy / switched off'), ('wrong', 'Wrong number'),
            ('callback', 'Call back later'), ('interested', 'Interested'), ('ordered', 'Ordered'), ('not_interested', 'Not interested'),
            ('not_there', 'Not there')]


DIAL = {'ET': ('251', '79'), 'UG': ('256', '7'), 'KE': ('254', '71')}


def ethiopian_phone(raw, cc='ET'):
    """International form (+2519..., +2567...) of a mobile number written the local way; other numbers are left as typed.
    The country is the company's (Ethiopia unless said)."""
    if not raw:
        return raw
    code, mobile = DIAL.get(cc or 'ET', DIAL['ET'])
    digits = re.sub(r'\D', '', raw)
    if digits.startswith('00'):
        digits = digits[2:]
    if len(digits) == 10 and digits[0] == '0' and digits[1] in mobile:
        return '+' + code + digits[1:]
    if len(digits) == 9 and digits[0] in mobile:
        return '+' + code + digits
    if len(digits) == 12 and digits[:3] in [c for c, _m in DIAL.values()]:
        return '+' + digits
    return raw.strip()


class SbWoreda(models.Model):
    _name = 'sb.woreda'
    _description = 'Woreda'
    _order = 'region, zone, name'

    name = fields.Char(required=True, index=True)
    zone = fields.Char(index=True)
    region = fields.Char(index=True)
    latitude = fields.Float(digits=(10, 7))
    longitude = fields.Float(digits=(10, 7))
    code = fields.Char(index=True)
    country_code = fields.Char(default='ET', index=True, help='ET: woreda; UG: district')
    active = fields.Boolean(default=True)

    @api.depends('name', 'zone', 'region')
    def _compute_display_name(self):
        for w in self:
            w.display_name = ' / '.join(x for x in (w.name, w.zone, w.region) if x)


class SbCrmTag(models.Model):
    _name = 'sb.crm.tag'
    _description = 'CRM tag'
    _order = 'name'

    name = fields.Char(required=True)
    color = fields.Integer()

    _unique = models.Constraint('unique(name)', 'This tag exists.')


class ResPartner(models.Model):
    _inherit = 'res.partner'

    sb_lifecycle = fields.Selection(LIFECYCLE, 'Lifecycle stage', index=True)
    sb_owner_id = fields.Many2one('res.users', 'Owner', index=True, help='The rep who looks after this contact.')
    sb_ctype = fields.Selection(CTYPES, 'Customer type', index=True)
    sb_channel = fields.Selection(CHANNELS, 'Trade channel')
    sb_source = fields.Selection(SOURCES, 'Source')
    sb_language = fields.Selection(LANGUAGES, 'Language')
    sb_woreda_id = fields.Many2one('sb.woreda', 'Woreda', index=True)
    sb_region = fields.Char('Region', compute='_compute_sb_area', store=True, readonly=False, index=True)
    sb_zone = fields.Char('Zone', compute='_compute_sb_area', store=True, readonly=False, index=True)
    sb_kebele = fields.Char('Kebele / town')
    sb_telegram = fields.Char('Telegram', help='Username (without @) or the phone the account uses.')
    sb_hectares = fields.Float('Farm size (ha)')
    sb_crops = fields.Char('Crops')
    sb_tag_ids = fields.Many2many('sb.crm.tag', string='Tags')
    sb_latitude = fields.Float(digits=(10, 7))
    sb_longitude = fields.Float(digits=(10, 7))
    sb_last_touch = fields.Datetime('Last activity', index=True)
    sb_deal_ids = fields.One2many('sb.deal', 'partner_id')

    @api.depends('sb_woreda_id')
    def _compute_sb_area(self):
        for p in self:
            if p.sb_woreda_id:
                p.sb_region, p.sb_zone = p.sb_woreda_id.region, p.sb_woreda_id.zone

    @api.model_create_multi
    def create(self, vals_list):
        for v in vals_list:
            for f in ('phone',):
                if v.get(f):
                    v[f] = ethiopian_phone(v[f], self.env.company.country_id.code)
        return super().create(vals_list)

    def write(self, vals):
        for f in ('phone',):
            if vals.get(f):
                vals = dict(vals, **{f: ethiopian_phone(vals[f], self.env.company.country_id.code)})
        return super().write(vals)


class SbCrmPipeline(models.Model):
    _name = 'sb.crm.pipeline'
    _description = 'Deal pipeline'
    _order = 'sequence, id'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    stage_ids = fields.One2many('sb.crm.stage', 'pipeline_id')
    active = fields.Boolean(default=True)


class SbCrmStage(models.Model):
    _name = 'sb.crm.stage'
    _description = 'Deal stage'
    _order = 'pipeline_id, sequence, id'

    pipeline_id = fields.Many2one('sb.crm.pipeline', required=True, ondelete='cascade')
    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    probability = fields.Float('Win probability %', default=10)
    kind = fields.Selection([('open', 'Open'), ('won', 'Won'), ('lost', 'Lost')], default='open', required=True)
    rot_days = fields.Integer('Stale after (days)', default=14, help='A deal sitting longer than this in the stage is flagged.')


class SbCrmLostReason(models.Model):
    _name = 'sb.crm.lost.reason'
    _description = 'Lost reason'
    _order = 'sequence, id'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)


class SbDeal(models.Model):
    _name = 'sb.deal'
    _description = 'Deal'
    _order = 'id desc'

    name = fields.Char(required=True)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True, index=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    partner_id = fields.Many2one('res.partner', 'Customer', index=True, ondelete='restrict')
    contact_id = fields.Many2one('res.partner', 'Contact person')
    owner_id = fields.Many2one('res.users', 'Owner', default=lambda s: s.env.user, index=True)
    pipeline_id = fields.Many2one('sb.crm.pipeline', required=True, index=True)
    stage_id = fields.Many2one('sb.crm.stage', required=True, index=True, domain="[('pipeline_id', '=', pipeline_id)]")
    state = fields.Selection(related='stage_id.kind', store=True, index=True)
    amount = fields.Monetary()
    quantity = fields.Float(help='e.g. litres or units the deal is about.')
    probability = fields.Float()
    close_date = fields.Date('Expected close')
    source = fields.Selection(SOURCES)
    lost_reason_id = fields.Many2one('sb.crm.lost.reason')
    lost_note = fields.Char()
    stage_date = fields.Datetime('In this stage since', default=fields.Datetime.now)
    won_date = fields.Date()
    note = fields.Text()
    tag_ids = fields.Many2many('sb.crm.tag', string='Tags')
    sale_order_ids = fields.One2many('sale.order', 'sb_deal_id', 'Quotations / orders')
    active = fields.Boolean(default=True)
    legacy_ref = fields.Char(index=True)

    @api.constrains('stage_id', 'pipeline_id')
    def _check_stage(self):
        for d in self:
            if d.stage_id.pipeline_id != d.pipeline_id:
                raise ValidationError(self.env._('The stage is not in this pipeline.'))

    def write(self, vals):
        if 'stage_id' in vals:
            stage = self.env['sb.crm.stage'].browse(vals['stage_id'])
            vals = dict(vals, stage_date=fields.Datetime.now())
            if stage.kind == 'won':
                vals.setdefault('won_date', fields.Date.context_today(self))
                vals.setdefault('probability', 100)
            elif stage.kind == 'lost':
                vals.setdefault('probability', 0)
            else:
                vals.setdefault('won_date', False)
                vals.setdefault('probability', stage.probability)
        return super().write(vals)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    sb_deal_id = fields.Many2one('sb.deal', 'Deal', index=True, copy=False)

    def action_confirm(self):
        res = super().action_confirm()
        for so in self.filtered('sb_deal_id'):
            deal = so.sb_deal_id
            if deal.state == 'open':
                won = deal.pipeline_id.stage_ids.filtered(lambda s: s.kind == 'won')[:1]
                if won:
                    deal.write({'stage_id': won.id, 'amount': max(deal.amount, so.amount_total)})
                    self.env['sb.crm.activity'].sudo().create({
                        'kind': 'note', 'deal_id': deal.id, 'partner_id': deal.partner_id.id, 'done': True,
                        'subject': self.env._('Won: order %(n)s confirmed (%(a)s)', n=so.name, a='{:,.2f}'.format(so.amount_total))})
        return res


class SbCrmActivity(models.Model):
    """One item on the timeline: something done (note, call, meeting, visit, message) or to do (task)."""
    _name = 'sb.crm.activity'
    _description = 'CRM activity'
    _order = 'date desc, id desc'

    kind = fields.Selection(KINDS, required=True, default='note', index=True)
    subject = fields.Char()
    body = fields.Text()
    partner_id = fields.Many2one('res.partner', index=True, ondelete='cascade')
    deal_id = fields.Many2one('sb.deal', index=True, ondelete='cascade')
    user_id = fields.Many2one('res.users', 'By / assigned to', default=lambda s: s.env.user, index=True)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, index=True)
    date = fields.Datetime(default=fields.Datetime.now, index=True, help='When it happened, or when the task is due.')
    done = fields.Boolean(default=True, index=True)
    done_date = fields.Datetime()
    outcome = fields.Selection(OUTCOMES)
    minutes = fields.Integer('Duration (min)')
    latitude = fields.Float(digits=(10, 7))
    longitude = fields.Float(digits=(10, 7))
    legacy_ref = fields.Char(index=True)

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        recs._touch()
        return recs

    def write(self, vals):
        res = super().write(vals)
        if 'done' in vals:
            self._touch()
        return res

    def _touch(self):
        for a in self.filtered('done'):
            when = a.done_date or a.date
            for p in (a.partner_id | a.deal_id.partner_id).commercial_partner_id | a.partner_id:
                if not p.sb_last_touch or p.sb_last_touch < when:
                    p.sudo().sb_last_touch = when


class SbCrmTarget(models.Model):
    _name = 'sb.crm.target'
    _description = 'Sales target'
    _order = 'month desc, user_id'

    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True)
    user_id = fields.Many2one('res.users', 'Rep', required=True)
    month = fields.Date('Ethiopian month (first day)', required=True, index=True)
    amount = fields.Monetary('Sales target (incl. VAT)')
    currency_id = fields.Many2one(related='company_id.currency_id')

    _unique = models.Constraint('unique(company_id, user_id, month)', 'One target per rep and month.')
