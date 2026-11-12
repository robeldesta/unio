"""Store documents posted by finance (a company setting, off by default).

On: a store clerk (Inventory app, without an Accounting role) prepares and saves goods received, goods issued, transfers
and production receipts, and sends them to finance instead of posting them. Finance (Clerk or higher) checks the unit
price and VAT, then posts (or sends the document back to the store with the reason): in Me's Approval Center, or on the
form. Documents other documents post by themselves (a van load, for instance) are posted as before."""
from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import UserError

PRICE_GAP = 0.20  # a unit price this much away from the order price / last cost is pointed out to the store
# context value of another document's own flow posting a store document (a van load): a Python object, so a client request
# cannot claim it (`posted_by_flow(records)`)
FLOW_POST = object()


def posted_by_flow(records):
    return records.with_context(sb_store_auto_post=FLOW_POST)


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_store_posts_by_finance = fields.Boolean(
        'Store documents are posted by finance',
        help='On: store clerks save goods received, goods issued, transfers and production receipts and send them to finance, '
             'who check the unit price and VAT, then post them (Me > Approvals). Off: store clerks post them themselves.')


class SbStockVoucher(models.Model):
    _inherit = 'sb.stock.voucher'

    sb_sent_finance_at = fields.Datetime('Sent to finance on', copy=False, readonly=True, tracking=True)
    sb_sent_finance_by = fields.Many2one('res.users', 'Sent to finance by', copy=False, readonly=True)
    sb_sent_back_reason = fields.Char('Sent back by finance', copy=False, readonly=True,
                                      help='Why finance sent the document back to the store.')
    sb_finance_posts = fields.Boolean(compute='_compute_sb_finance_posts',
                                      help='This user saves the document and sends it to finance, who post it.')

    @api.depends_context('uid', 'company')
    @api.depends('voucher_type', 'company_id')
    def _compute_sb_finance_posts(self):
        store_only = self._sb_store_only()
        for v in self:
            v.sb_finance_posts = store_only and bool((v.company_id or self.env.company).sb_store_posts_by_finance) \
                and v.voucher_type in v._store_types()

    def _sb_store_only(self, user=None):
        """A store clerk without an Accounting role (Clerk or higher)."""
        user = user or self.env.user
        return not user._is_superuser() and user.has_group('sonador_inventory.group_sb_store_user') \
            and not user.has_group('sonador_books.group_sb_clerk')

    def _sb_require(self):
        if not self.env.su and self.company_id.sb_store_posts_by_finance and self.voucher_type in self._store_types() \
                and self._sb_store_only() and self.env.context.get('sb_store_auto_post') is not FLOW_POST:
            raise UserError(self.env._('Store documents are posted by finance: save it and press Send to finance. They check '
                                       'the unit price and VAT, then post it.'))
        return super()._sb_require()

    # ------------------------------------------------------------------ the store sends
    def _sb_last_cost(self, product):
        """The unit cost of the item's last posted receipt in the company, else its cost on the item."""
        line = self.env['sb.stock.voucher.line'].sudo().search([
            ('product_id', '=', product.id), ('company_id', '=', self.company_id.id),
            ('voucher_id.state', '=', 'posted'), ('voucher_id.voucher_type', 'in', ('grn', 'opening')),
            ('voucher_id', '!=', self.id)], order='id desc', limit=1)
        return line.unit_cost if line else product.sudo().standard_price

    def _sb_price_warnings(self):
        """Lines whose unit price looks wrong: none at all, or more than 20% away from the purchase order line (else the
        last cost). Receipts only: issues and transfers are valued at cost when posted. A warning, never a refusal."""
        self.ensure_one()
        if self.voucher_type not in self._sb_receipt_types() or self.voucher_type == 'opening':
            return []
        if self.voucher_type == 'fprr' and getattr(self, 'sb_wip_costed', False):
            return []  # costed from the materials taken into work in process
        out = []
        fmt = lambda x: '{:,.2f}'.format(x)  # noqa: E731
        for l in self.line_ids.filtered('product_id'):
            item = l.product_id.display_name
            price = l._sb_unit_cost()
            if self.currency_id.is_zero(price):
                out.append(self.env._('%s: no unit price.', item))
                continue
            pol = l.sb_po_line_id if 'sb_po_line_id' in l._fields else False
            if pol:
                ref, what = pol._sb_price_company(self.date), self.env._('the order price')
            else:
                ref, what = self._sb_last_cost(l.product_id), self.env._('the last cost')
            if ref and abs(price - ref) > PRICE_GAP * abs(ref):
                out.append(self.env._('%(item)s: unit price %(p)s is %(pct)s%% %(dir)s %(what)s (%(ref)s).', item=item, p=fmt(price),
                                      pct=round(abs(price - ref) / abs(ref) * 100), what=what, ref=fmt(ref),
                                      dir=self.env._('above') if price > ref else self.env._('below')))
        return out

    def action_sb_send_finance(self):
        warnings = []
        for v in self:
            if v.state != 'draft':
                raise UserError(self.env._('%s is posted already.', v.display_name))
            if v.voucher_type not in v._store_types():
                raise UserError(self.env._('Only goods received, goods issued, transfers and production receipts go to finance.'))
            found = v._sb_price_warnings()
            v._check()
            v.write({'sb_sent_finance_at': fields.Datetime.now(), 'sb_sent_finance_by': self.env.uid, 'sb_sent_back_reason': False})
            body = self.env._('Sent to finance by %s: they check the unit price and VAT, then post it.', self.env.user.name)
            if found:
                body = Markup('%s<br/>%s<ul>%s</ul>') % (body, self.env._('Check the unit price:'),
                                                         Markup('').join(Markup('<li>%s</li>') % w for w in found))
            v.message_post(body=body)
            warnings += found
        message = self.env._('Saved and sent to finance: they check the unit price and VAT, then post it.')
        if warnings:
            message = '%s\n%s\n%s' % (message, self.env._('Check the unit price:'), '\n'.join('- ' + w for w in warnings))
        if self.env.context.get('sb_in_drawer'):
            nxt = {'type': 'ir.actions.act_window_close'}
        else:
            nxt = {'type': 'ir.actions.client', 'tag': 'sonador_books.back'}
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': self.env._('Sent to finance'), 'message': message, 'sticky': bool(warnings),
                           'type': 'warning' if warnings else 'success', 'next': nxt}}

    # ------------------------------------------------------------------ finance decides
    def _sb_finance_check(self):
        if not self.env.su and not self.env.user.has_group('sonador_books.group_sb_clerk'):
            raise UserError(self.env._('Only finance (Clerk or higher) posts or sends back what the store sent.'))

    def action_sb_finance_post(self):
        self._sb_finance_check()
        sent = self.filtered(lambda v: v.state == 'draft' and v.sb_sent_finance_at)
        self.action_post()
        for v in sent.filtered(lambda x: x.state == 'posted'):
            v.message_post(body=self.env._('Checked and posted by %s.', self.env.user.name))
        return True

    def action_sb_send_back(self, reason):
        self._sb_finance_check()
        reason = (reason or '').strip()
        if not reason:
            raise UserError(self.env._('Say why it goes back: the store will see your reason.'))
        for v in self:
            if v.state != 'draft':
                raise UserError(self.env._('%s is posted already.', v.display_name))
            v.write({'sb_sent_finance_at': False, 'sb_sent_back_reason': reason})
            v.message_post(body=self.env._('Sent back to the store by %(u)s: %(r)s', u=self.env.user.name, r=reason))
        return True
