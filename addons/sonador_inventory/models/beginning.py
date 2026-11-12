"""Inventory beginning balances (Maintain Inventory Items > Beginning Balances).

One Opening Stock document (OS) per beginning-balance period holds every item's quantity and cost. Changing an
item later reverses its opening receipt at the value it went in with and receives the new quantity and cost on the
same date, so issues and sales after it are re-costed (open periods) and the GL entry of the document is rebuilt.
"""
from collections import defaultdict

from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.account.models.mail_message import bypass_token
from odoo.addons.sonador_books.models.move_guard import controlled


class SbStockVoucher(models.Model):
    _inherit = 'sb.stock.voucher'

    sb_ob_period = fields.Date('Beginning balance period', index=True, copy=False)


class SbBeginning(models.AbstractModel):
    _inherit = 'sb.beginning'

    def _has_inventory(self):
        return True

    def _os(self, end):
        return self.env['sb.stock.voucher'].search([('voucher_type', '=', 'opening'), ('state', '=', 'posted'),
                                                    ('sb_ob_period', '=', end), ('company_id', '=', self.env.company.id)],
                                                   order='id')

    def _extra_periods(self):
        res = super()._extra_periods()
        os = self.env['sb.stock.voucher'].search([('voucher_type', '=', 'opening'), ('state', '=', 'posted'),
                                                  ('company_id', '=', self.env.company.id)])
        return res + [v.sb_ob_period or v.date for v in os]

    def _adopt(self):
        super()._adopt()
        for v in self.env['sb.stock.voucher'].search([('voucher_type', '=', 'opening'), ('sb_ob_period', '=', False),
                                                      ('company_id', '=', self.env.company.id)]):
            controlled(v).sb_ob_period = v.date

    def _inventory_accounts(self):
        company = self.env.company
        accs = set(super()._inventory_accounts())
        for categ in self.env['product.category'].search([]):
            acc = categ.with_company(company).property_stock_valuation_account_id
            if acc:
                accs.add(acc.id)
        if company.account_stock_valuation_id:
            accs.add(company.account_stock_valuation_id.id)
        return accs

    def _items_by_account(self, end):
        out = defaultdict(float, super()._items_by_account(end))
        Stock = self.env['sb.stock']
        for l in self._os(end).line_ids:
            out[Stock._valuation_account(l.product_id, self.env.company).id] += l.value
        return out

    # ------------------------------------------------------------------ screen
    @api.model
    def bb_items(self, key, search=''):
        period = self._period(key)
        end = fields.Date.to_date(period['end'])
        company = self.env.company
        current = defaultdict(lambda: [0.0, 0.0])
        vouchers = self._os(end)
        for l in vouchers.line_ids:
            current[l.product_id.id][0] += l.quantity
            current[l.product_id.id][1] += l.value
        dom = [('is_storable', '=', True), ('company_id', 'in', (company.id, False))]
        if search:
            dom += ['|', ('default_code', 'ilike', search), ('name', 'ilike', search)]
        products = self.env['product.product'].search(dom, order='default_code, name')
        products |= self.env['product.product'].browse(list(current))
        rows = []
        for p in products.sorted(lambda p: (p.default_code or '~', p.name)):
            qty, value = current.get(p.id, (0.0, 0.0))
            rows.append({'id': p.id, 'code': p.default_code or '', 'name': p.name, 'uom': p.uom_id.name,
                         'qty': qty, 'value': value, 'cost': value / qty if qty else p.with_company(company).standard_price,
                         'kind': getattr(p, 'sb_item_kind', False) or '', 'active': p.active})
        # the part of the inventory accounts' GL balance that is not in items (entered on the chart of accounts)
        inv = self._inventory_accounts()
        not_itemised = sum(l.debit - l.credit for l in self._account_vouchers(end).line_ids if l.account_id.id in inv)
        return {'period': period, 'rows': rows, 'total': sum(r['value'] for r in rows), 'not_itemised': not_itemised,
                'vouchers': [{'id': v.id, 'name': v.name} for v in vouchers],
                'warehouse': vouchers[:1].warehouse_id.name or self._warehouse().name}

    def _warehouse(self):
        return self.env['stock.warehouse'].search([('company_id', '=', self.env.company.id)], limit=1)

    @api.model
    def bb_items_save(self, key, rows):
        """rows: [{id: product id, qty, value}] for the items the user changed."""
        self._require()
        self = controlled(self)  # the opening stock screen rewrites its posted opening stock vouchers
        period = self._period(key)
        end = fields.Date.to_date(period['end'])
        company = self.env.company
        cur = company.currency_id
        equity = company.sb_opening_equity_account_id
        if not equity:
            raise UserError(self.env._('Set the opening balance equity account in Settings first.'))
        Product = self.env['product.product']
        vouchers = self._os(end)
        current = defaultdict(lambda: [0.0, 0.0])
        for l in vouchers.line_ids:
            current[l.product_id.id][0] += l.quantity
            current[l.product_id.id][1] += l.value
        changes = []
        for r in rows or []:
            p = Product.browse(int(r['id']))
            qty = float(r.get('qty') or 0.0)
            value = cur.round(float(r.get('value') or 0.0))
            if qty < 0:
                raise ValidationError(self.env._('%s: the beginning quantity cannot be negative.', p.display_name))
            if not qty and value:
                raise ValidationError(self.env._('%s: a value needs a quantity.', p.display_name))
            if value < 0:
                raise ValidationError(self.env._('%s: the beginning value cannot be negative.', p.display_name))
            q0, v0 = current.get(p.id, (0.0, 0.0))
            if abs(qty - q0) < 1e-6 and cur.is_zero(value - v0):
                continue
            changes.append((p, qty, value))
        if not changes:
            return self.bb_items(key)
        if not vouchers:
            lines = [Command.create({'product_id': p.id, 'quantity': q, 'unit_cost': v / q, 'name': p.display_name})
                     for p, q, v in changes if q > 0]
            if lines:
                v = self.env['sb.stock.voucher'].create({
                    'voucher_type': 'opening', 'date': end, 'sb_ob_period': end, 'warehouse_id': self._warehouse().id,
                    'account_id': equity.id, 'memo': period['title'], 'line_ids': lines})
                v.action_post()
                # quantity x unit cost can differ from the value typed by a cent: keep what was typed
                self._os_fix_values(v, {p.id: val for p, q, val in changes if q > 0})
            return self.bb_items(key)
        target = vouchers[-1]
        Stock = self.env['sb.stock']
        loc = Stock._locations(company)
        for p, qty, value in changes:
            for v in vouchers:
                for m in v.stock_move_ids.filtered(lambda m: m.product_id == p and m.state == 'done' and not m.sb_reversal_of_id
                                                   and m.location_dest_id.usage == 'internal' and m.location_id.usage != 'internal'):
                    if self.env['stock.move'].search_count([('sb_reversal_of_id', '=', m.id)]):
                        continue
                    r = Stock._move(p, m.quantity, m.location_dest_id, m.location_id, end,
                                    'Beginning balance change %s' % v.name, company, unit_cost=abs(m.value) / m.quantity if m.quantity else 0.0,
                                    link={'sb_stock_voucher_id': v.id}, reverses=m)
                    r.value = abs(m.value)
                v.line_ids.filtered(lambda l: l.product_id == p).unlink()
            if qty <= 0 and Stock._uses_daily_average(p, company):  # taken out: re-cost what came after
                Stock._recost(p, company, end, reason=target.name)
            if qty > 0:
                m = Stock._move(p, qty, loc['inventory'], target.warehouse_id.lot_stock_id, end, target.name, company,
                                unit_cost=value / qty, link={'sb_stock_voucher_id': target.id})
                if not cur.is_zero(abs(m.value) - value):
                    m.value = value
                    Stock._recost(p, company, end, reason=target.name)
                target.write({'line_ids': [Command.create({'product_id': p.id, 'quantity': qty, 'unit_cost': value / qty,
                                                           'value': value, 'name': p.display_name})]})
        for v in vouchers:
            self._os_entry(v)
        return self.bb_items(key)

    def _os_fix_values(self, voucher, wanted):
        cur = voucher.currency_id
        changed = False
        for l in voucher.line_ids:
            want = wanted.get(l.product_id.id)
            if want is None or cur.is_zero(want - l.value):
                continue
            m = voucher.stock_move_ids.filtered(lambda m: m.product_id == l.product_id and not m.sb_reversal_of_id)[:1]
            if m:
                m.value = want
                self.env['sb.stock']._recost(l.product_id, voucher.company_id, voucher.date, reason=voucher.name)
            l.value = want
            changed = True
        if changed:
            self._os_entry(voucher)

    def _os_entry(self, voucher):
        """Rebuild the GL entry of an Opening Stock document from its lines."""
        Stock = self.env['sb.stock']
        company = voucher.company_id
        entry = []
        for l in voucher.line_ids:
            name = '%s - %s' % (voucher.name, l.product_id.display_name)
            entry += [(Stock._valuation_account(l.product_id, company), l.value, name, None),
                      (voucher.account_id, -l.value, name, None)]
        old = voucher.move_id
        if old:
            # the opening stock screen is one of the controlled flows allowed to take a posted entry out
            old = controlled(old).with_context(bypass_audit=bypass_token)
            old.button_draft()
            voucher.move_id = False
            old.with_context(force_delete=True).unlink()
        voucher.move_id = Stock._post_entry(company, voucher.date, '%s %s' % (voucher.name, voucher.memo or ''), entry)


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    def action_sb_beginning(self):
        self.ensure_one()
        return {'type': 'ir.actions.client', 'tag': 'sonador_books.beginning_balances', 'name': self.env._('Beginning Balances'),
                'params': {'tab': 'inventory', 'product_id': self.product_variant_id.id}}
