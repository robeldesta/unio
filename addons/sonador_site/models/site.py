"""The company's website: one per company. Settings (look, contact, shop, delivery, payment, SEO) and pages made of
ready-made blocks. The storefront (Next.js) reads it all through the public API in controllers/api.py."""
import hashlib
import hmac
import json
import re
import time

from odoo import api, fields, models
from odoo.exceptions import UserError

THEMES = [('fresh', 'Fresh'), ('classic', 'Classic'), ('bold', 'Bold'), ('market', 'Market')]
FONTS = [('inter', 'Inter'), ('poppins', 'Poppins'), ('dm_serif', 'DM Serif + Inter'), ('nunito', 'Nunito'), ('noto_ethiopic', 'Noto Sans Ethiopic')]

# The blocks a page is made of, and the settings each one has (used by the page builder and the storefront)
BLOCKS = {
    'hero': {'label': 'Big banner', 'icon': 'fa-image', 'fields': [
        ('title', 'text', 'Title'), ('subtitle', 'textarea', 'Text'), ('button', 'text', 'Button text'),
        ('link', 'link', 'Button goes to'), ('image', 'image', 'Picture'), ('style', 'choice:split|full|plain', 'Layout')],
        'default': {'title': 'Quality you can count on', 'subtitle': 'Order online, pay on delivery. Fast delivery across the city.',
                    'button': 'Shop now', 'link': '/shop', 'style': 'split'}},
    'products': {'label': 'Products', 'icon': 'fa-shopping-bag', 'fields': [
        ('title', 'text', 'Title'), ('source', 'choice:featured|newest|category', 'Which products'),
        ('category', 'category', 'Category'), ('limit', 'number', 'How many')],
        'default': {'title': 'Our products', 'source': 'featured', 'limit': 8}},
    'categories': {'label': 'Categories', 'icon': 'fa-th-large', 'fields': [('title', 'text', 'Title')],
                   'default': {'title': 'Shop by category'}},
    'text': {'label': 'Text', 'icon': 'fa-align-left', 'fields': [('title', 'text', 'Title'), ('body', 'textarea', 'Text'),
                                                               ('align', 'choice:left|center', 'Align')],
             'default': {'title': 'About us', 'body': 'Tell your story: who you are, what you make, and why customers choose you.', 'align': 'left'}},
    'image_text': {'label': 'Picture and text', 'icon': 'fa-columns', 'fields': [
        ('title', 'text', 'Title'), ('body', 'textarea', 'Text'), ('image', 'image', 'Picture'),
        ('side', 'choice:left|right', 'Picture on the'), ('button', 'text', 'Button text'), ('link', 'link', 'Button goes to')],
        'default': {'title': 'Made with care', 'body': 'Every order is checked before it leaves us.', 'side': 'left'}},
    'features': {'label': 'Why us', 'icon': 'fa-check-square-o', 'fields': [('title', 'text', 'Title'), ('items', 'list:icon|title|text', 'Points')],
                 'default': {'title': 'Why customers choose us', 'items': [
                     {'icon': 'truck', 'title': 'Fast delivery', 'text': 'Same day in the city.'},
                     {'icon': 'shield', 'title': 'Quality', 'text': 'Checked before it leaves us.'},
                     {'icon': 'cash', 'title': 'Pay on delivery', 'text': 'Cash, telebirr or bank transfer.'}]}},
    'gallery': {'label': 'Gallery', 'icon': 'fa-picture-o', 'fields': [('title', 'text', 'Title'), ('images', 'images', 'Pictures')],
                'default': {'title': 'Gallery', 'images': []}},
    'testimonials': {'label': 'What customers say', 'icon': 'fa-quote-left', 'fields': [
        ('title', 'text', 'Title'), ('items', 'list:quote|name|role', 'Quotes')],
        'default': {'title': 'What our customers say', 'items': [{'quote': 'Always on time and the quality is excellent.', 'name': 'Hanna T.', 'role': 'Café owner'}]}},
    'cta': {'label': 'Call to action', 'icon': 'fa-bullhorn', 'fields': [('title', 'text', 'Title'), ('subtitle', 'text', 'Text'),
                                                                         ('button', 'text', 'Button text'), ('link', 'link', 'Button goes to')],
            'default': {'title': 'Ordering for a business?', 'subtitle': 'Ask for wholesale prices and monthly invoicing.', 'button': 'Contact us', 'link': '/contact'}},
    'faq': {'label': 'Questions', 'icon': 'fa-question-circle', 'fields': [('title', 'text', 'Title'), ('items', 'list:q|a', 'Questions')],
            'default': {'title': 'Questions', 'items': [{'q': 'How long does delivery take?', 'a': 'Same day in the city for orders before 2 pm.'},
                                                        {'q': 'How can I pay?', 'a': 'Cash or telebirr on delivery, or bank transfer.'}]}},
    'contact': {'label': 'Contact', 'icon': 'fa-map-marker', 'fields': [('title', 'text', 'Title'), ('text', 'textarea', 'Text'), ('form', 'bool', 'Message form')],
                'default': {'title': 'Get in touch', 'text': 'Call, write or visit us.', 'form': True}},
}


class UnioSite(models.Model):
    _name = 'unio.site'
    _description = 'Website'

    name = fields.Char('Site name', required=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company, ondelete='cascade')
    published = fields.Boolean('Online', default=False)
    tagline = fields.Char()
    theme = fields.Selection(THEMES, default='fresh', required=True)
    font = fields.Selection(FONTS, default='inter', required=True)
    color = fields.Char('Main colour', default='#1F6F5C')
    accent = fields.Char('Accent', default='#F2A93B')
    logo = fields.Image(max_width=600, max_height=300, help='Empty: the company logo.')
    announcement = fields.Char(help='A line across the top, e.g. "Free delivery over 2,000 birr".')
    # contact
    phone = fields.Char()
    whatsapp = fields.Char()
    telegram = fields.Char()
    email = fields.Char()
    address = fields.Text()
    hours = fields.Char('Opening hours')
    map_url = fields.Char('Map link')
    facebook = fields.Char()
    instagram = fields.Char()
    tiktok = fields.Char()
    # shop
    shop = fields.Boolean('Online shop', default=True)
    show_stock = fields.Boolean('Show stock left', default=True)
    sell_out_of_stock = fields.Boolean('Take orders when out of stock')
    min_order = fields.Float('Smallest order')
    pickup = fields.Boolean('Pickup', default=True)
    pickup_text = fields.Char(default='Pick up at our shop.')
    delivery = fields.Boolean('Delivery', default=True)
    delivery_fee = fields.Float(default=150.0)
    free_over = fields.Float('Free delivery over', default=0.0)
    delivery_text = fields.Char(default='Same day in the city for orders before 2 pm.')
    pay_cod = fields.Boolean('Pay on delivery or pickup', default=True)
    pay_bank = fields.Boolean('Bank transfer', default=True)
    bank_text = fields.Text('Bank details', help='Shown after ordering. Empty: your bank accounts.')
    pay_telebirr = fields.Boolean('telebirr')
    telebirr_text = fields.Char('telebirr till / phone')
    confirm_orders = fields.Boolean('Confirm, invoice and prepare delivery at once', default=True,
                                    help='On: a web order is confirmed, invoiced in the books and its delivery order made ready '
                                         '(paid by Chapa: once Chapa confirms the payment). Off: web orders wait as quotations.')
    pay_chapa = fields.Boolean('Chapa (cards, telebirr, CBE Birr, M-Pesa)')
    chapa_key = fields.Char('Chapa secret key', groups='base.group_system', copy=False)
    chapa_ok = fields.Boolean('Chapa key checked', readonly=True, copy=False)
    chapa_test = fields.Boolean('Chapa test key', readonly=True, copy=False)
    chapa_hint = fields.Char(readonly=True, copy=False)
    chapa_account_id = fields.Many2one('account.account', 'Chapa money goes to',
                                       help='The bank or cash account where Chapa payments are recorded (e.g. a "Chapa" account).')
    notify_email = fields.Char('Send new orders to')
    terms = fields.Text('Terms of sale')
    # search engines
    seo_title = fields.Char('Title in Google')
    seo_description = fields.Char('Description in Google')
    domain = fields.Char('Own domain', help='e.g. www.yourshop.et, pointed at Unio.')
    secret = fields.Char(groups='base.group_system', copy=False)
    views_total = fields.Integer(readonly=True)

    page_ids = fields.One2many('unio.site.page', 'site_id')

    # ------------------------------------------------------------------ helpers
    @api.model
    def current(self):
        site = self.sudo().search([('company_id', '=', self.env.company.id)], limit=1) or self.sudo().search([], limit=1)
        if not site:
            site = self.sudo()._create_default()
        return site

    def _create_default(self):
        c = self.env.company
        bank = '\n'.join('%s: %s' % (b.bank_id.name or 'Bank', b.acc_number) for b in c.partner_id.bank_ids)
        site = self.create({'name': c.name, 'phone': c.phone, 'email': c.email,
                            'address': ', '.join(p for p in [c.street, c.city, c.country_id.name] if p),
                            'notify_email': c.email, 'bank_text': bank or False,
                            'seo_title': c.name, 'tagline': 'Order online'})
        site.page_ids = [(0, 0, p) for p in [
            {'name': 'Home', 'slug': 'home', 'sequence': 1, 'menu': False, 'blocks': json.dumps([
                dict(BLOCKS['hero']['default'], type='hero', title=c.name, subtitle='Order online and we deliver.'),
                dict(BLOCKS['categories']['default'], type='categories'),
                dict(BLOCKS['products']['default'], type='products'),
                dict(BLOCKS['features']['default'], type='features'),
                dict(BLOCKS['testimonials']['default'], type='testimonials'),
                dict(BLOCKS['cta']['default'], type='cta')])},
            {'name': 'About us', 'slug': 'about', 'sequence': 10, 'blocks': json.dumps([
                dict(BLOCKS['text']['default'], type='text'), dict(BLOCKS['image_text']['default'], type='image_text'),
                dict(BLOCKS['faq']['default'], type='faq')])},
            {'name': 'Contact', 'slug': 'contact', 'sequence': 20, 'blocks': json.dumps([dict(BLOCKS['contact']['default'], type='contact')])},
        ]]
        for p in site.page_ids:
            p.blocks_draft = p.blocks
        return site

    def _secret(self):
        self.ensure_one()
        if not self.sudo().secret:
            import secrets
            self.sudo().secret = secrets.token_hex(24)
        return self.sudo().secret

    def preview_token(self, page_id):
        """A short-lived token so the storefront shows the unpublished draft of a page in the builder."""
        exp = int(time.time()) + 3600
        msg = '%s:%s' % (page_id, exp)
        return '%s:%s' % (msg, hmac.new(self._secret().encode(), msg.encode(), hashlib.sha256).hexdigest()[:24])

    def check_preview(self, token):
        try:
            page_id, exp, sig = token.split(':')
            msg = '%s:%s' % (page_id, exp)
            good = hmac.new(self._secret().encode(), msg.encode(), hashlib.sha256).hexdigest()[:24]
            return hmac.compare_digest(sig, good) and int(exp) > time.time() and int(page_id)
        except Exception:
            return False

    # ------------------------------------------------------------------ for the Website app
    @api.model
    def builder_data(self):
        site = self.current()
        Order = self.env['sale.order']
        orders = Order.search([('site_order', '=', True)], limit=8, order='id desc')
        data = site.read([f for f in self._fields if f not in ('page_ids', 'logo', 'secret', 'company_id', 'chapa_key', 'chapa_account_id')])[0]
        data['has_logo'] = bool(site.logo)
        data['chapa_account_id'] = site.chapa_account_id.id or False
        accounts = self.env['account.account'].search([('account_type', '=', 'asset_cash')])
        P = self.env['product.template']
        return {
            'site': data,
            'pages': [{'id': p.id, 'name': p.name, 'slug': p.slug, 'menu': p.menu, 'published': p.published,
                       'blocks': json.loads(p.blocks_draft or p.blocks or '[]'), 'changed': (p.blocks_draft or '') != (p.blocks or ''),
                       'seo_title': p.seo_title or '', 'seo_description': p.seo_description or ''} for p in site.page_ids.sorted('sequence')],
            'blocks': {k: {'label': v['label'], 'icon': v['icon'], 'fields': v['fields'], 'default': v['default']} for k, v in BLOCKS.items()},
            'themes': THEMES, 'fonts': FONTS,
            'categories': [{'id': c.id, 'name': c.name} for c in self.env['unio.site.category'].search([])],
            'counts': {'published': P.search_count([('site_published', '=', True)]), 'products': P.site_products('', 1)['total'],
                       'orders': Order.search_count([('site_order', '=', True)]),
                       'new_orders': Order.search_count([('site_order', '=', True), ('state', 'in', ('draft', 'sent'))])},
            'orders': [{'id': o.id, 'name': o.name, 'customer': o.partner_id.name, 'total': o.amount_total, 'state': o.state,
                        'date': fields.Datetime.to_string(o.date_order)[:16], 'how': o.site_delivery} for o in orders],
            'url': site._public_url(),
            'storefront': self.storefront_base(),
            'cash_accounts': [{'id': a.id, 'name': a.display_name} for a in accounts],
            'visits': self._visits(),
        }

    def _visits(self):
        from datetime import timedelta
        H = self.env['unio.site.hit'].sudo()
        today = fields.Date.context_today(self)
        rows = H.search([('date', '>', today - timedelta(days=30))])
        days = []
        for i in range(13, -1, -1):
            d = today - timedelta(days=i)
            days.append({'date': fields.Date.to_string(d), 'views': sum(r.views for r in rows if r.date == d)})
        top = {}
        for r in rows:
            top[r.path] = top.get(r.path, 0) + r.views
        return {'days': days, 'week': sum(x['views'] for x in days[-7:]), 'top': sorted(top.items(), key=lambda x: -x[1])[:8]}

    def _public_url(self):
        self.ensure_one()
        if self.domain:
            return 'https://%s' % self.domain
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url') or ''
        return base

    @api.model
    def save_settings(self, vals):
        site = self.current()
        allowed = {k: v for k, v in vals.items() if k in self._fields and k not in (
            'id', 'page_ids', 'secret', 'company_id', 'views_total', 'chapa_key', 'chapa_ok', 'chapa_test', 'chapa_hint')}
        if allowed.get('chapa_account_id'):
            allowed['chapa_account_id'] = int(allowed['chapa_account_id'])
        if allowed.get('pay_chapa') and not site.chapa_ok:
            raise UserError(self.env._('Add your Chapa secret key and check it first.'))
        if 'domain' in allowed and allowed['domain']:
            allowed['domain'] = re.sub(r'^https?://', '', allowed['domain'].strip().lower()).strip('/')
        site.write(allowed)
        return self.builder_data()

    @api.model
    def set_chapa_key(self, key):
        """Check the shop's own Chapa key with Chapa before keeping it. A wrong key is refused at once."""
        if not self.env.user.has_group('sales_team.group_sale_manager'):
            raise UserError(self.env._('Only sales managers set the payment keys.'))
        from .chapa import Chapa
        site = self.current()
        key = (key or '').strip()
        if not key:
            site.sudo().write({'chapa_key': False, 'chapa_ok': False, 'pay_chapa': False, 'chapa_hint': False})
            return self.builder_data()
        if not key.startswith('CHASECK'):
            raise UserError(self.env._('That is not a Chapa secret key: it starts with CHASECK_TEST- or CHASECK-. '
                                       'Find it in your Chapa dashboard, Settings, API.'))
        c = Chapa(key)
        if not c.check():
            raise UserError(self.env._('Chapa did not accept this key. Copy the secret key again from your Chapa dashboard.'))
        vals = {'chapa_key': key, 'chapa_ok': True, 'chapa_test': c.test, 'chapa_hint': '…' + key[-4:]}
        if not site.chapa_account_id:
            journal = self.env['account.journal'].search([('type', '=', 'bank'), ('company_id', '=', self.env.company.id)], limit=1)
            vals['chapa_account_id'] = journal.default_account_id.id or False
        site.sudo().write(vals)
        return self.builder_data()

    def _chapa(self):
        from .chapa import Chapa
        key = self.sudo().chapa_key
        return Chapa(key) if key and self.chapa_ok and self.pay_chapa else None

    @api.model
    def save_logo(self, data_b64):
        self.current().logo = data_b64 or False
        return True

    @api.model
    def save_page(self, page_id, vals, publish=False):
        site = self.current()
        Page = self.env['unio.site.page']
        page = Page.browse(page_id) if page_id else Page
        v = {}
        if 'name' in vals:
            v['name'] = vals['name']
        if 'slug' in vals:
            v['slug'] = Page._clean_slug(vals['slug'] or vals.get('name') or 'page')
        for k in ('menu', 'published', 'seo_title', 'seo_description', 'sequence'):
            if k in vals:
                v[k] = vals[k]
        if 'blocks' in vals:
            v['blocks_draft'] = json.dumps(vals['blocks'])
        if page:
            page.write(v)
        else:
            v.setdefault('blocks_draft', '[]')
            v['site_id'] = site.id
            v.setdefault('sequence', 50)
            page = Page.create(v)
        if publish:
            page.blocks = page.blocks_draft
            page.published = True
        return {'id': page.id, 'data': self.builder_data()}

    @api.model
    def delete_page(self, page_id):
        page = self.env['unio.site.page'].browse(page_id)
        if page.slug == 'home':
            raise UserError(self.env._('The home page cannot be deleted.'))
        page.unlink()
        return self.builder_data()

    @api.model
    def preview_url(self, page_id):
        site = self.current()
        page = self.env['unio.site.page'].browse(page_id)
        path = '/' if page.slug == 'home' else '/%s' % page.slug
        return '%s?preview=%s' % (path, site.preview_token(page.id))

    @api.model
    def upload_image(self, name, data_b64):
        """A picture for a page block: kept as a public attachment, shown at /web/image/<id>."""
        att = self.env['ir.attachment'].sudo().create({'name': name or 'picture', 'datas': data_b64, 'public': True,
                                                       'res_model': 'unio.site', 'res_id': self.current().id})
        return '/web/image/%d' % att.id

    @api.model
    def storefront_base(self):
        """Where the storefront answers. Same address as the system, unless set (e.g. while developing)."""
        return self.env['ir.config_parameter'].sudo().get_param('unio.site.storefront_url') or ''

    @api.model
    def set_online(self, online):
        self.current().published = bool(online)
        return self.builder_data()


class UnioSitePage(models.Model):
    _name = 'unio.site.page'
    _description = 'Web page'
    _order = 'sequence, id'

    site_id = fields.Many2one('unio.site', required=True, ondelete='cascade')
    name = fields.Char(required=True)
    slug = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    menu = fields.Boolean('In the menu', default=True)
    published = fields.Boolean(default=True)
    blocks = fields.Text(default='[]', help='Published content (JSON list of blocks).')
    blocks_draft = fields.Text(default='[]', help='What is being edited; Publish copies it to blocks.')
    seo_title = fields.Char()
    seo_description = fields.Char()

    _slug_unique = models.Constraint('unique(site_id, slug)', 'Another page already has this address.')

    @api.model
    def _clean_slug(self, s):
        s = re.sub(r'[^a-z0-9ሀ-፿]+', '-', (s or '').lower()).strip('-')
        reserved = {'shop', 'product', 'cart', 'checkout', 'order', 'odoo', 'web', 'unio', 'api', 'search', 'category'}
        return (s + '-page') if s in reserved else (s or 'page')


class UnioSiteCategory(models.Model):
    _name = 'unio.site.category'
    _description = 'Shop category'
    _order = 'sequence, name'

    name = fields.Char(required=True, translate=True)
    slug = fields.Char()
    sequence = fields.Integer(default=10)
    image = fields.Image(max_width=800, max_height=800)
    product_ids = fields.Many2many('product.template', string='Products')

    @api.model_create_multi
    def create(self, vals_list):
        for v in vals_list:
            v.setdefault('slug', self.env['unio.site.page']._clean_slug(v.get('name')))
        return super().create(vals_list)


class UnioSiteMessage(models.Model):
    _name = 'unio.site.message'
    _description = 'Message from the website'
    _inherit = ['mail.thread']
    _order = 'id desc'

    name = fields.Char('From', required=True)
    phone = fields.Char()
    email = fields.Char()
    message = fields.Text()
    done = fields.Boolean()


class UnioSiteHit(models.Model):
    _name = 'unio.site.hit'
    _description = 'Website visits per day'
    _order = 'date desc'

    date = fields.Date(required=True, index=True)
    path = fields.Char(index=True)
    views = fields.Integer()
