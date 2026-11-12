"""Orders from the website land as sales orders (quotations until confirmed, unless the shop confirms them at once)."""
import secrets

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    site_order = fields.Boolean('From the website', readonly=True, index=True, copy=False)
    site_delivery = fields.Selection([('pickup', 'Pickup'), ('delivery', 'Delivery')], 'Web: pickup or delivery', readonly=True, copy=False)
    site_payment = fields.Selection([('cod', 'On delivery / pickup'), ('bank', 'Bank transfer'), ('telebirr', 'telebirr transfer'),
                                     ('chapa', 'Chapa')], 'Web: payment', readonly=True, copy=False)
    site_paid = fields.Boolean('Web: paid online', readonly=True, copy=False)
    site_pay_ref = fields.Char('Web: payment reference', readonly=True, copy=False)
    site_address = fields.Text('Web: delivery address', readonly=True, copy=False)
    site_token = fields.Char(readonly=True, copy=False, index=True)


class UnioSiteShop(models.AbstractModel):
    """What the storefront asks for, done as the superuser but only ever on published products."""
    _name = 'unio.site.shop'
    _description = 'Online shop'

    def _site(self):
        return self.env['unio.site'].sudo().current()

    def _lines(self, cart):
        """cart: [{'id': product.template id, 'qty': n}] -> [(product.product, qty)] for published products only."""
        site = self._site()
        out, problems = [], []
        T = self.env['product.template'].sudo()
        for item in cart or []:
            try:
                tmpl = T.browse(int(item.get('id'))).exists()
                qty = max(min(float(item.get('qty') or 0), 9999), 0)
            except (TypeError, ValueError):
                continue
            if not tmpl or not tmpl.site_published or not tmpl.sale_ok or qty <= 0:
                problems.append({'id': item.get('id'), 'problem': 'gone'})
                continue
            prod = tmpl.product_variant_id
            if tmpl.is_storable and not site.sell_out_of_stock and qty > tmpl.qty_available:
                problems.append({'id': tmpl.id, 'problem': 'stock', 'left': max(tmpl.qty_available, 0)})
                qty = max(tmpl.qty_available, 0)
                if not qty:
                    continue
            out.append((prod, qty))
        return out, problems

    def _delivery_product(self):
        p = self.env.ref('sonador_site.product_delivery', raise_if_not_found=False)
        return p and p.sudo()

    @api.model
    def quote(self, cart, how='delivery'):
        site = self._site()
        lines, problems = self._lines(cart)
        sub = 0.0
        tax = 0.0
        items = []
        partner = self.env.company.sudo().partner_id
        for prod, qty in lines:
            price = prod.lst_price
            taxes = prod.taxes_id.filtered(lambda t: t.company_id == self.env.company)
            res = taxes.compute_all(price, self.env.company.currency_id, qty, product=prod, partner=partner)
            sub += res['total_excluded']
            tax += res['total_included'] - res['total_excluded']
            items.append({'id': prod.product_tmpl_id.id, 'name': prod.name, 'qty': qty, 'price': round(res['total_included'] / qty, 2),
                          'total': res['total_included']})
        fee = 0.0
        if how == 'delivery' and site.delivery and lines:
            fee = 0.0 if (site.free_over and sub + tax >= site.free_over) else site.delivery_fee
        total = sub + tax + fee
        return {'items': items, 'problems': problems, 'subtotal': round(sub, 2), 'tax': round(tax, 2), 'delivery': round(fee, 2),
                'total': round(total, 2), 'currency': self.env.company.currency_id.name,
                'min_order': site.min_order, 'below_min': bool(site.min_order and sub + tax < site.min_order)}

    @api.model
    def place(self, data):
        site = self._site()
        if not site.published or not site.shop:
            raise UserError('The shop is closed.')
        name = (data.get('name') or '').strip()[:120]
        phone = (data.get('phone') or '').strip()[:40]
        email = (data.get('email') or '').strip()[:120]
        how = data.get('how') if data.get('how') in ('pickup', 'delivery') else 'pickup'
        pay = data.get('pay') if data.get('pay') in ('cod', 'bank', 'telebirr', 'chapa') else 'cod'
        if not name or not phone:
            raise UserError('Your name and phone number are needed.')
        if how == 'delivery' and not site.delivery or how == 'pickup' and not site.pickup:
            raise UserError('That way of getting the order is not offered.')
        if {'cod': not site.pay_cod, 'bank': not site.pay_bank, 'telebirr': not site.pay_telebirr, 'chapa': not site._chapa()}[pay]:
            raise UserError('That way of paying is not offered.')
        lines, problems = self._lines(data.get('cart'))
        if not lines:
            raise UserError('The cart is empty.')
        if any(p['problem'] == 'stock' for p in problems):
            return {'ok': False, 'problems': problems}
        q = self.quote(data.get('cart'), how)
        if q['below_min']:
            raise UserError('The smallest order is %s.' % site.min_order)
        Partner = self.env['res.partner'].sudo()
        digits = ''.join(c for c in phone if c.isdigit())[-9:]
        partner = Partner
        if email:
            partner = Partner.search([('email', '=ilike', email)], limit=1)
        if not partner and digits:
            partner = Partner.search([('phone', 'like', digits)], limit=1)
        if not partner:
            partner = Partner.create({'name': name, 'phone': phone, 'email': email or False, 'customer_rank': 1,
                                      'street': (data.get('address') or '')[:200] or False, 'city': (data.get('city') or '')[:80] or False})
        order_lines = [(0, 0, {'product_id': p.id, 'product_uom_qty': qty}) for p, qty in lines]
        if q['delivery']:
            dp = self._delivery_product()
            if dp:
                order_lines.append((0, 0, {'product_id': dp.id, 'product_uom_qty': 1, 'price_unit': q['delivery'], 'tax_ids': [(5, 0, 0)]}))
        address = '\n'.join(x for x in [name, phone, data.get('address'), data.get('city')] if x)
        note = (data.get('note') or '').strip()[:1000]
        order = self.env['sale.order'].sudo().create({
            'partner_id': partner.id, 'order_line': order_lines, 'site_order': True, 'site_delivery': how,
            'site_payment': pay, 'site_address': address if how == 'delivery' else False,
            'site_token': secrets.token_urlsafe(16), 'origin': 'Website',
            'note': Markup('<p>%s</p>') % note if note else False,
        })
        order.message_post(body=Markup('<p>Order from the website: %s, %s, %s.</p>') % (
            dict(order._fields['site_delivery'].selection)[how], dict(order._fields['site_payment'].selection)[pay], phone))
        if pay == 'chapa':
            pay_url = self._chapa_start(site, order, data.get('origin'))
            return {'ok': True, 'order': order.name, 'token': order.site_token, 'total': order.amount_total, 'pay_url': pay_url}
        self._finish(site, order)
        self._notify(site, order, email)
        return {'ok': True, 'order': order.name, 'token': order.site_token, 'total': order.amount_total}

    # ------------------------------------------------------------------ after ordering
    def _finish(self, site, order):
        """Confirm the order, post its invoice (or, paid online, its cash sale) and make its delivery order ready.
        Whatever blocks it in the books (an account missing...) leaves the order saved, with a note saying what to fix."""
        if not site.confirm_orders:
            return
        try:
            with self.env.cr.savepoint():
                if order.state in ('draft', 'sent'):
                    order.action_confirm()
                if not order.sb_voucher_ids.filtered(lambda v: v.state != 'void'):
                    paid = order.site_paid
                    act = order._sb_invoice('cash_sale' if paid else 'sale')
                    v = self.env['sb.voucher'].browse(act['res_id'])
                    if paid:
                        account = site.chapa_account_id or v._default_cash_account()
                        v.cash_line_ids = [(5, 0, 0), (0, 0, {'account_id': account.id, 'amount_currency': v.amount_cash,
                                                              'reference': (order.site_pay_ref or '')[:60]})]
                    if v.sb_fiscal_only:
                        # Ethiopia: the invoice number is the fiscal receipt / e-invoice reference, typed in when it is issued
                        order.message_post(body=Markup('<p>Invoice %s prepared. Issue the fiscal receipt (or the Ministry e-invoice), '
                                                       'type its number on the invoice and post it; the delivery order follows.</p>') % v.display_name)
                    else:
                        v.action_post()
        except Exception as e:
            order.message_post(body=Markup('<p><b>Not invoiced automatically:</b> %s</p><p>Invoice it from the order.</p>') % str(e)[:300])

    def _chapa_start(self, site, order, origin):
        c = site._chapa()
        if not c:
            raise UserError('Online payment is not available.')
        origin = (origin or '').rstrip('/') or self.env['unio.site']._public_url()
        ref = '%s-%s' % (order.name.replace('/', ''), secrets.token_hex(3))
        names = (order.partner_id.name or '').split(' ', 1)
        url, raw = c.initialize(ref, order.amount_total, order.partner_id.email, names[0], names[1] if len(names) > 1 else '',
                                order.partner_id.phone, '%s/unio/site/chapa/notify' % origin, '%s/order/%s' % (origin, order.site_token),
                                site.name, 'Order %s' % order.name)
        if not url:
            order.message_post(body=Markup('<p>Chapa refused the payment: %s</p>') % str(raw.get('message') or raw)[:300])
            raise UserError('The payment page could not be opened. Please try again or choose another way to pay.')
        order.site_pay_ref = ref
        return url

    def _chapa_check(self, order):
        """Ask Chapa whether the order is paid; when it is, finish the order. True when paid."""
        if order.site_paid:
            return True
        site = self._site()
        c = site._chapa()
        if not c or not order.site_pay_ref:
            return False
        try:
            data, _raw = c.verify(order.site_pay_ref)
        except Exception:
            return False
        try:
            amount = float(data.get('amount') or 0)
        except (TypeError, ValueError):
            amount = 0.0
        if data.get('status') == 'success' and amount + 0.01 >= order.amount_total:
            order.write({'site_paid': True})
            order.message_post(body=Markup('<p>Paid with Chapa: %s %s, reference %s.</p>') % (
                data.get('amount'), data.get('currency') or 'ETB', data.get('reference') or order.site_pay_ref))
            self._finish(site, order)
            self._notify(site, order, order.partner_id.email)
            return True
        return False

    @api.model
    def pay_again(self, token, origin):
        o = self.env['sale.order'].sudo().search([('site_token', '=', token), ('site_order', '=', True)], limit=1)
        if not o or o.site_paid or o.state == 'cancel' or o.site_payment != 'chapa':
            raise UserError('Nothing to pay.')
        return {'pay_url': self._chapa_start(self._site(), o, origin)}

    @api.model
    def chapa_notify(self, tx_ref):
        o = self.env['sale.order'].sudo().search([('site_pay_ref', '=', tx_ref)], limit=1) if tx_ref else None
        return bool(o) and self._chapa_check(o)

    @api.model
    def _cron_chapa(self):
        """Chapa orders of the last day that are not marked paid yet (the buyer closed the page before coming back)."""
        from datetime import timedelta
        since = fields.Datetime.now() - timedelta(days=1)
        for o in self.env['sale.order'].sudo().search([('site_payment', '=', 'chapa'), ('site_paid', '=', False),
                                                        ('state', 'in', ('draft', 'sent')), ('create_date', '>', since)], limit=50):
            self._chapa_check(o)

    def _notify(self, site, order, email):
        Mail = self.env['mail.mail'].sudo()
        frm = self.env.company.email_formatted or self.env['ir.config_parameter'].sudo().get_param('saas.mail_from') or False
        rows = Markup('').join(Markup('<tr><td>%s</td><td style="text-align:right">%s</td><td style="text-align:right">%s</td></tr>') % (
            l.product_id.name, '%g' % l.product_uom_qty, '{:,.2f}'.format(l.price_total)) for l in order.order_line)
        table = Markup('<table style="width:100%%;border-collapse:collapse;font-size:14px">%s<tr><td colspan="2"><b>Total</b></td>'
                       '<td style="text-align:right"><b>%s %s</b></td></tr></table>') % (rows, '{:,.2f}'.format(order.amount_total), order.currency_id.name)
        if site.notify_email:
            Mail.create({'email_from': frm, 'email_to': site.notify_email, 'subject': 'New web order %s from %s' % (order.name, order.partner_id.name),
                         'body_html': Markup('<p>%s (%s) ordered on the website, %s.</p>%s') % (
                             order.partner_id.name, order.partner_id.phone or '', dict(order._fields['site_delivery'].selection)[order.site_delivery], table)})
        if email:
            Mail.create({'email_from': frm, 'email_to': email, 'subject': 'Your order %s at %s' % (order.name, site.name),
                         'body_html': Markup('<div style="font-family:Arial,sans-serif"><p>Thank you, %s. We have your order <b>%s</b> '
                                             'and will call you to confirm.</p>%s<p>%s</p></div>') % (
                             order.partner_id.name, order.name, table, site.name)})

    @api.model
    def status(self, token):
        o = self.env['sale.order'].sudo().search([('site_token', '=', token), ('site_order', '=', True)], limit=1) if token else None
        if not o:
            return {}
        site = self._site()
        if o.site_payment == 'chapa' and not o.site_paid:
            self._chapa_check(o)
        step = 'received'
        if o.state == 'sale':
            step = 'confirmed'
        if o.state == 'sale' and o.sb_status == 'done' and o.sb_voucher_ids.filtered(lambda v: v.state == 'posted'):
            step = 'delivered'
        if o.state == 'cancel':
            step = 'cancelled'
        return {'name': o.name, 'date': fields.Datetime.to_string(o.date_order)[:16], 'step': step, 'how': o.site_delivery, 'pay': o.site_payment,
                'total': o.amount_total, 'currency': o.currency_id.name, 'customer': o.partner_id.name,
                'lines': [{'name': l.product_id.name, 'qty': l.product_uom_qty, 'total': l.price_total} for l in o.order_line],
                'bank': (site.bank_text or '') if o.site_payment == 'bank' else '',
                'telebirr': (site.telebirr_text or '') if o.site_payment == 'telebirr' else '',
                'paid': o.site_paid, 'awaiting_payment': o.site_payment == 'chapa' and not o.site_paid and o.state != 'cancel'}
