"""The public API the storefront (Next.js) reads. Only published things come out; orders go in as sales orders.

Each company's storefront calls its own address (company.unio.et/unio/site/api/...), so the database is the
company's. Everything is read as the superuser and filtered to what is published."""
import json
import time
from collections import defaultdict, deque

from odoo import fields, http
from odoo.exceptions import UserError
from odoo.http import request

_RATE = defaultdict(deque)
_PLACEHOLDER = []


def _real_logo(data):
    """False for Odoo's "Your logo" picture, which a company has until it uploads its own."""
    if not data:
        return False
    import base64
    import hashlib
    if not _PLACEHOLDER:
        from odoo.tools.misc import file_open
        with file_open('base/static/img/res_company_logo.png', 'rb') as f:
            _PLACEHOLDER.append(hashlib.md5(base64.b64encode(f.read())).hexdigest())
    raw = data if isinstance(data, bytes) else data.encode()
    return hashlib.md5(raw).hexdigest() != _PLACEHOLDER[0]


def _limited(key, n, seconds):
    q = _RATE[key]
    now = time.time()
    while q and q[0] < now - seconds:
        q.popleft()
    if len(q) >= n:
        return True
    q.append(now)
    return False


def _json(data, status=200, cache=0):
    headers = [('Content-Type', 'application/json; charset=utf-8')]
    headers.append(('Cache-Control', 'public, max-age=%d' % cache if cache else 'no-store'))
    return request.make_response(json.dumps(data, default=str), headers=headers, status=status)


def _body():
    try:
        return json.loads(request.httprequest.get_data(as_text=True) or '{}')
    except ValueError:
        return {}


def _ip():
    return (request.httprequest.headers.get('X-Forwarded-For') or request.httprequest.remote_addr or '').split(',')[0].strip()


class SiteApi(http.Controller):

    # ------------------------------------------------------------------ helpers
    def _site(self):
        if 'unio.site' not in request.env:
            return None
        return request.env['unio.site'].sudo().current()

    def _money(self, tmpl):
        """Price as shown: with the taxes included."""
        prod = tmpl.product_variant_id
        c = request.env.company.sudo()
        taxes = tmpl.taxes_id.filtered(lambda t: t.company_id == c)
        res = taxes.compute_all(tmpl.list_price, c.currency_id, 1, product=prod, partner=c.partner_id)
        return round(res['total_included'], 2)

    def _stock(self, site, t):
        if not t.is_storable:
            return {'state': 'in', 'left': None}
        left = t.qty_available
        if left <= 0:
            return {'state': 'backorder' if site.sell_out_of_stock else 'out', 'left': 0}
        return {'state': 'low' if left <= 5 else 'in', 'left': left if site.show_stock else None}

    def _card(self, site, t):
        return {'id': t.id, 'slug': t.site_slug, 'name': t.name, 'price': self._money(t),
                'image': '/unio/site/img/p/%d/%s' % (t.id, int(t.write_date.timestamp())) if t.image_128 else None,
                'short': t.description_sale or '', 'stock': self._stock(site, t), 'uom': t.uom_id.name,
                'featured': t.site_featured, 'categories': [c.slug for c in t.site_category_ids]}

    def _published(self):
        return request.env['product.template'].sudo().search([('site_published', '=', True), ('sale_ok', '=', True)])

    # ------------------------------------------------------------------ site
    @http.route('/unio/site/api/site', type='http', auth='public', methods=['GET'], csrf=False, sitemap=False)
    def site(self, **kw):
        site = self._site()
        if not site:
            return _json({'online': False})
        c = request.env.company.sudo()
        pages = site.page_ids.filtered(lambda p: p.published).sorted('sequence')
        cats = request.env['unio.site.category'].sudo().search([])
        logo = '/unio/site/img/logo/%s' % int((site.write_date or c.write_date).timestamp()) if (site.logo or _real_logo(c.logo)) else None
        return _json({
            'online': site.published, 'name': site.name, 'tagline': site.tagline or '', 'theme': site.theme, 'font': site.font,
            'color': site.color, 'accent': site.accent, 'logo': logo, 'announcement': site.announcement or '',
            'contact': {k: site[k] or '' for k in ('phone', 'whatsapp', 'telegram', 'email', 'address', 'hours', 'map_url', 'facebook', 'instagram', 'tiktok')},
            'shop': site.shop, 'currency': c.currency_id.name, 'symbol': c.currency_id.symbol,
            'menu': [{'name': p.name, 'path': '/' if p.slug == 'home' else '/' + p.slug} for p in pages if p.menu],
            'categories': [{'name': x.name, 'slug': x.slug, 'image': '/unio/site/img/c/%d' % x.id if x.image else None,
                            'count': len(x.product_ids.filtered('site_published'))} for x in cats if x.product_ids.filtered('site_published')],
            'delivery': {'pickup': site.pickup, 'pickup_text': site.pickup_text or '', 'delivery': site.delivery, 'fee': site.delivery_fee,
                         'free_over': site.free_over, 'text': site.delivery_text or '', 'min_order': site.min_order},
            'payment': {'cod': site.pay_cod, 'bank': site.pay_bank, 'telebirr': site.pay_telebirr, 'chapa': bool(site._chapa())},
            'terms': site.terms or '', 'seo': {'title': site.seo_title or site.name, 'description': site.seo_description or site.tagline or ''},
            'company': {'name': c.name, 'vat': c.vat or ''},
        }, cache=30)

    @http.route('/unio/site/api/page', type='http', auth='public', methods=['GET'], csrf=False, sitemap=False)
    def page(self, slug='home', preview=None, **kw):
        site = self._site()
        if not site:
            return _json({}, 404)
        page = site.page_ids.filtered(lambda p: p.slug == slug)[:1]
        draft = preview and site.check_preview(preview) == page.id
        if not page or not (page.published or draft):
            return _json({}, 404)
        blocks = json.loads((page.blocks_draft if draft else page.blocks) or '[]')
        return _json({'name': page.name, 'slug': page.slug, 'blocks': self._fill(site, blocks), 'draft': bool(draft),
                      'seo': {'title': page.seo_title or '', 'description': page.seo_description or ''}}, cache=0 if draft else 20)

    def _fill(self, site, blocks):
        """Blocks that show products get them filled in here, so a page is one call."""
        T = request.env['product.template'].sudo()
        for b in blocks:
            if b.get('type') == 'products':
                dom = [('site_published', '=', True), ('sale_ok', '=', True)]
                order = 'site_sequence, name'
                if b.get('source') == 'featured':
                    dom.append(('site_featured', '=', True))
                elif b.get('source') == 'newest':
                    order = 'create_date desc'
                elif b.get('source') == 'category' and b.get('category'):
                    dom.append(('site_category_ids', 'in', [int(b['category'])]))
                limit = min(int(b.get('limit') or 8), 24)
                recs = T.search(dom, order=order, limit=limit)
                if not recs and b.get('source') == 'featured':
                    recs = T.search([('site_published', '=', True), ('sale_ok', '=', True)], order=order, limit=limit)
                b['products'] = [self._card(site, t) for t in recs]
        return blocks

    @http.route('/unio/site/api/products', type='http', auth='public', methods=['GET'], csrf=False, sitemap=False)
    def products(self, cat=None, q=None, sort='', page=1, limit=24, **kw):
        site = self._site()
        if not site:
            return _json({'products': []})
        dom = [('site_published', '=', True), ('sale_ok', '=', True)]
        if cat:
            dom.append(('site_category_ids.slug', '=', cat))
        if q:
            dom += ['|', '|', ('name', 'ilike', q), ('default_code', 'ilike', q), ('description_sale', 'ilike', q)]
        order = {'price': 'list_price asc', 'price_desc': 'list_price desc', 'new': 'create_date desc', 'name': 'name'}.get(sort, 'site_featured desc, site_sequence, name')
        T = request.env['product.template'].sudo()
        limit = min(int(limit or 24), 60)
        page = max(int(page or 1), 1)
        total = T.search_count(dom)
        recs = T.search(dom, order=order, limit=limit, offset=(page - 1) * limit)
        return _json({'total': total, 'page': page, 'pages': max((total + limit - 1) // limit, 1),
                      'products': [self._card(site, t) for t in recs]}, cache=20)

    @http.route('/unio/site/api/product', type='http', auth='public', methods=['GET'], csrf=False, sitemap=False)
    def product(self, slug=None, **kw):
        site = self._site()
        T = request.env['product.template'].sudo()
        t = T.search([('site_slug', '=', slug), ('site_published', '=', True)], limit=1) if slug and site else T
        if not t:
            return _json({}, 404)
        d = self._card(site, t)
        d['description'] = str(t.site_description or '') or ''
        d['images'] = ([d['image']] if d['image'] else []) + ['/unio/site/img/x/%d' % i.id for i in t.site_image_ids]
        d['code'] = t.default_code or ''
        related = T.search([('site_published', '=', True), ('id', '!=', t.id), ('site_category_ids', 'in', t.site_category_ids.ids)], limit=4) \
            if t.site_category_ids else T.search([('site_published', '=', True), ('id', '!=', t.id)], limit=4)
        d['related'] = [self._card(site, r) for r in related]
        d['category'] = {'name': t.site_category_ids[:1].name, 'slug': t.site_category_ids[:1].slug} if t.site_category_ids else None
        return _json(d, cache=20)

    @http.route('/unio/site/api/sitemap', type='http', auth='public', methods=['GET'], csrf=False, sitemap=False)
    def sitemap(self, **kw):
        site = self._site()
        if not site or not site.published:
            return _json({'pages': [], 'products': []})
        return _json({'pages': ['/' if p.slug == 'home' else '/' + p.slug for p in site.page_ids if p.published],
                      'products': [{'slug': t.site_slug, 'date': fields.Datetime.to_string(t.write_date)[:10]} for t in self._published()],
                      'categories': [c.slug for c in request.env['unio.site.category'].sudo().search([])]}, cache=300)

    # ------------------------------------------------------------------ cart and orders
    @http.route('/unio/site/api/quote', type='http', auth='public', methods=['POST'], csrf=False, sitemap=False)
    def quote(self, **kw):
        b = _body()
        return _json(request.env['unio.site.shop'].sudo().quote(b.get('cart') or [], b.get('how') or 'delivery'))

    @http.route('/unio/site/api/order', type='http', auth='public', methods=['POST'], csrf=False, sitemap=False)
    def order(self, **kw):
        b = _body()
        if b.get('website'):           # the hidden field only robots fill in
            return _json({'ok': False, 'error': 'Please try again.'}, 400)
        if _limited('order:%s:%s' % (request.db, b.get('ip') or _ip()), 8, 3600):
            return _json({'ok': False, 'error': 'Too many orders from here. Please call us.'}, 429)
        try:
            return _json(request.env['unio.site.shop'].sudo().place(b))
        except UserError as e:
            return _json({'ok': False, 'error': str(e.args[0] if e.args else e)}, 400)

    @http.route('/unio/site/api/pay', type='http', auth='public', methods=['POST'], csrf=False, sitemap=False)
    def pay(self, **kw):
        b = _body()
        if _limited('pay:%s:%s' % (request.db, b.get('ip') or _ip()), 20, 3600):
            return _json({'ok': False, 'error': 'Too many tries. Please call us.'}, 429)
        try:
            return _json(dict(request.env['unio.site.shop'].sudo().pay_again(b.get('token'), b.get('origin')), ok=True))
        except UserError as e:
            return _json({'ok': False, 'error': str(e.args[0] if e.args else e)}, 400)

    @http.route('/unio/site/chapa/notify', type='http', auth='public', methods=['GET', 'POST'], csrf=False, sitemap=False)
    def chapa_notify(self, **kw):
        """Chapa calls this when a payment is made; we only believe Chapa's verify service."""
        b = _body() if request.httprequest.method == 'POST' else {}
        ref = kw.get('trx_ref') or kw.get('tx_ref') or b.get('trx_ref') or b.get('tx_ref')
        ok = request.env['unio.site.shop'].sudo().chapa_notify(ref)
        return _json({'ok': bool(ok)})

    @http.route('/unio/site/api/order', type='http', auth='public', methods=['GET'], csrf=False, sitemap=False)
    def order_status(self, t=None, **kw):
        res = request.env['unio.site.shop'].sudo().status(t)
        return _json(res, 200 if res else 404)

    @http.route('/unio/site/api/message', type='http', auth='public', methods=['POST'], csrf=False, sitemap=False)
    def message(self, **kw):
        b = _body()
        if b.get('website') or not (b.get('name') and (b.get('phone') or b.get('email')) and b.get('message')):
            return _json({'ok': False, 'error': 'Your name, a phone or email, and the message are needed.'}, 400)
        if _limited('msg:%s:%s' % (request.db, b.get('ip') or _ip()), 5, 3600):
            return _json({'ok': False, 'error': 'Too many messages from here.'}, 429)
        m = request.env['unio.site.message'].sudo().create({k: (b.get(k) or '')[:2000] for k in ('name', 'phone', 'email', 'message')})
        site = self._site()
        if site and site.notify_email:
            from markupsafe import Markup
            request.env['mail.mail'].sudo().create({
                'email_to': site.notify_email, 'subject': 'Message from the website: %s' % m.name,
                'email_from': request.env.company.email_formatted or False, 'reply_to': m.email or False,
                'body_html': Markup('<p><b>%s</b> (%s %s) wrote:</p><p>%s</p>') % (m.name, m.phone or '', m.email or '', m.message)})
        return _json({'ok': True})

    @http.route('/unio/site/api/hit', type='http', auth='public', methods=['POST'], csrf=False, sitemap=False)
    def hit(self, **kw):
        b = _body()
        path = (b.get('path') or '/')[:200]
        if _limited('hit:%s:%s' % (request.db, b.get('ip') or _ip()), 120, 3600):
            return _json({'ok': True})
        H = request.env['unio.site.hit'].sudo()
        today = fields.Date.context_today(H)
        row = H.search([('date', '=', today), ('path', '=', path)], limit=1)
        if row:
            request.env.cr.execute('UPDATE unio_site_hit SET views = views + 1 WHERE id = %s', [row.id])
        else:
            H.create({'date': today, 'path': path, 'views': 1})
        return _json({'ok': True})

    # ------------------------------------------------------------------ pictures
    @http.route(['/unio/site/img/p/<int:pid>', '/unio/site/img/p/<int:pid>/<string:v>'], type='http', auth='public', sitemap=False)
    def img_product(self, pid, v=None, **kw):
        t = request.env['product.template'].sudo().browse(pid).exists()
        if not t or not t.site_published:
            return request.not_found()
        return self._img(t, 'image_1024')

    @http.route('/unio/site/img/x/<int:iid>', type='http', auth='public', sitemap=False)
    def img_extra(self, iid, **kw):
        i = request.env['unio.site.image'].sudo().browse(iid).exists()
        if not i or not i.product_tmpl_id.site_published:
            return request.not_found()
        return self._img(i, 'image')

    @http.route('/unio/site/img/c/<int:cid>', type='http', auth='public', sitemap=False)
    def img_category(self, cid, **kw):
        c = request.env['unio.site.category'].sudo().browse(cid).exists()
        return self._img(c, 'image') if c else request.not_found()

    @http.route(['/unio/site/img/logo', '/unio/site/img/logo/<string:v>'], type='http', auth='public', sitemap=False)
    def img_logo(self, v=None, **kw):
        site = self._site()
        if site and site.logo:
            return self._img(site, 'logo')
        return self._img(request.env.company.sudo(), 'logo')

    def _img(self, record, field):
        stream = request.env['ir.binary']._get_image_stream_from(record, field)
        res = stream.get_response()
        res.headers['Cache-Control'] = 'public, max-age=86400'
        return res
