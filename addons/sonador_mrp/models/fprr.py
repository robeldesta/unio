"""Finished Product Receipt (FPRR): finished goods received into stock from production at cost,
credited to work in process. The Peachtree way of recording production without recipes; also the form that
historical FPRRs are converted into.

The cost is either typed in (from the production reports, the default) or taken from work in process: the materials
issued to the work in process account (GIVs not yet taken by an FPRR, for the same job when one is chosen) plus the
labour and overhead entered on the FPRR. Costed from work in process, a GIV that is later re-costed, corrected or
voided passes the difference on to the finished goods, and from there to cost of sales.
"""
from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.sonador_books.models.move_guard import controlled


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_fprr_cost_source = fields.Selection([('typed', 'Typed in (from production reports)'),
                                            ('wip', 'From work in process (materials issued + labour and overhead)')],
                                           'FPRR cost', default='typed', required=True,
                                           help='Typed in: the FPRR carries the unit cost entered on it.\n'
                                                'From work in process: the FPRR takes the materials issued to work in process '
                                                '(GIVs not yet received as finished goods) plus the labour and overhead entered, '
                                                'and follows any later change in the cost of those GIVs.')


class SbStockVoucher(models.Model):
    _inherit = 'sb.stock.voucher'

    voucher_type = fields.Selection(selection_add=[('fprr', 'Finished Product Receipt (FPRR)')], ondelete={'fprr': 'cascade'})
    sb_wip_costed = fields.Boolean('Cost from work in process', copy=False,
                                   default=lambda s: s.env.company.sb_fprr_cost_source == 'wip',
                                   help='The FPRR takes its cost from the materials issued to work in process, not from the unit cost typed.')
    sb_fprr_id = fields.Many2one('sb.stock.voucher', 'Received as finished goods on', copy=False, readonly=True, index='btree_not_null',
                                 help='GIV to work in process: the FPRR that took its cost into finished goods.')
    sb_wip_giv_ids = fields.One2many('sb.stock.voucher', 'sb_fprr_id', string='Materials taken from work in process')

    sb_void_effect = fields.Char('Effect of a void', compute='_compute_sb_void_effect',
                                 help='GIV taken into finished goods by an FPRR costed from work in process: what voiding it does to them.')

    def _store_types(self):
        return super()._store_types() + ('fprr', 'build')

    @api.depends('state', 'sb_fprr_id', 'line_ids.value')
    def _compute_sb_void_effect(self):
        fmt = '{:,.2f}'.format
        for v in self:
            fprr = v.sb_fprr_id
            if v.voucher_type != 'giv' or v.state != 'posted' or not fprr or fprr.state != 'posted' or not fprr.sb_wip_costed:
                v.sb_void_effect = False
                continue
            taken = v._sb_wip_value(fprr.account_id)
            fg = fprr._sb_live_moves().filtered(lambda m: m.location_dest_id.usage == 'internal')
            now = sum(abs(m.value) for m in fg)
            qty = sum(fg.mapped('quantity'))
            after = now - taken
            v.sb_void_effect = self.env._(
                'Taken into the finished goods of %(fprr)s (%(items)s). Voiding or correcting this GIV takes %(taken)s out of '
                'their cost: %(now)s becomes %(after)s%(unit)s.',
                fprr=fprr.name, items=', '.join(fg.product_id.mapped('display_name')), taken=fmt(taken), now=fmt(now),
                after=fmt(after), unit=self.env._(' (unit cost %(a)s becomes %(b)s)', a='{:,.4f}'.format(now / qty), b='{:,.4f}'.format(after / qty))
                if qty else '') + (self.env._(' Nothing else is in their cost: they would be left at zero.') if abs(after) < 0.005 else '')
    sb_migrated = fields.Boolean('Brought over', copy=False, readonly=True,
                                 help='Converted from the previous system: its accounting is already in the general ledger.')

    def _name_prefix(self):
        return 'FPRR' if self.voucher_type == 'fprr' else super()._name_prefix()

    def _sb_series_doc_type(self):
        # finished product receipts (also a build received as an FPRR): numbered in the FPRR series when there is one
        if self.voucher_type == 'fprr' or (self.voucher_type == 'build' and self._name_prefix() == 'FPRR'):
            return 'fprr'
        return super()._sb_series_doc_type()

    @api.onchange('voucher_type', 'company_id')
    def _onchange_default_account(self):
        super()._onchange_default_account()
        if self.voucher_type == 'fprr':
            self.account_id = self.company_id.account_production_wip_account_id
            self.sb_wip_costed = self.company_id.sb_fprr_cost_source == 'wip'

    def _correctable_types(self):
        return super()._correctable_types() + ('fprr',)

    def _check(self):
        if self.voucher_type != 'fprr':
            return super()._check()
        lines = self.line_ids.filtered('product_id')
        if not lines or any(l.quantity <= 0 for l in lines):
            raise ValidationError(self.env._('Add the finished products received, with quantities.'))
        if not self.account_id:
            raise ValidationError(self.env._('Choose the work in process account the cost comes from.'))
        lock = self.company_id._get_user_fiscal_lock_date(self.company_id.account_stock_journal_id)
        if self.date <= lock:
            raise ValidationError(self.env._('The books are closed through %s.', lock))
        c = self.company_id
        if self.sb_wip_costed and (self.sb_labour_total or self.sb_overhead_total) and \
                not (c.sb_labour_absorbed_account_id and c.sb_overhead_absorbed_account_id):
            raise ValidationError(self.env._('Set the labour and overhead absorbed accounts in settings.'))

    # ------------------------------------------------------------------ cost from work in process
    def _sb_wip_value(self, account):
        """GIV: the value of its lines charged to `account` (what is waiting there to become finished goods)."""
        return sum(l.value for l in self.line_ids if l.product_id and (l.account_id or self.account_id) == account)

    def _sb_waiting_givs(self):
        """GIVs to this FPRR's work in process account not yet received as finished goods (same job, when one is chosen)."""
        dom = [('company_id', '=', self.company_id.id), ('voucher_type', '=', 'giv'), ('state', '=', 'posted'),
               ('sb_fprr_id', '=', False), ('sb_migrated', '=', False), ('date', '<=', self.date),
               '|', ('account_id', '=', self.account_id.id), ('line_ids.account_id', '=', self.account_id.id)]
        if self.analytic_account_id:
            dom.append(('analytic_account_id', '=', self.analytic_account_id.id))
        return self.search(dom).filtered(lambda g: not self.currency_id.is_zero(g._sb_wip_value(self.account_id)))

    def _sb_wip_costs(self):
        """({line: value}, GIVs taken) for an FPRR costed from work in process: the materials waiting plus labour and
        overhead, shared by the unit costs typed (as relative costs, when given) or else by quantity."""
        givs = self._sb_waiting_givs()
        total = sum(g._sb_wip_value(self.account_id) for g in givs) + (self.sb_labour_total or 0.0) + (self.sb_overhead_total or 0.0)
        if total <= 0:
            raise ValidationError(self.env._('Nothing is waiting in %s to be received as finished goods: post the GIVs of the '
                                             'materials first, or type the cost in.', self.account_id.display_name))
        lines = self.line_ids.filtered('product_id')
        weight = {l: l.quantity * l.unit_cost for l in lines}
        if sum(weight.values()) <= 0:
            weight = {l: l.quantity for l in lines}
        cur, left, out, all_w = self.currency_id, total, {}, sum(weight.values())
        for i, l in enumerate(lines):
            share = left if i == len(lines) - 1 else cur.round(total * weight[l] / all_w)
            out[l] = share
            left -= share
        return out, givs

    def _post_stock(self):
        if self.voucher_type != 'fprr':
            res = super()._post_stock()
            if self.voucher_type == 'giv' and self.sb_fprr_id.state == 'posted':
                # a corrected GIV posted again: its new cost goes on into the finished goods that took it
                self.sb_fprr_id._sb_take_wip_change(self._sb_wip_value(self.sb_fprr_id.account_id), self.name)
            return res
        Stock, company = self.env['sb.stock'], self.company_id
        production = self.env['stock.location'].search([('usage', '=', 'production'), ('company_id', 'in', (company.id, False))], limit=1)
        lines = self.line_ids.filtered('product_id')
        costs, givs = self._sb_wip_costs() if self.sb_wip_costed else ({}, self.browse())
        entry = []
        for l in lines:
            cost = costs[l] / l.quantity if self.sb_wip_costed else l._sb_unit_cost()
            m = Stock._move(l.product_id, l.quantity, production, self.warehouse_id.lot_stock_id, self.date, self.name, company,
                            unit_cost=cost, link={'sb_stock_voucher_id': self.id, 'sb_stock_line_id': l.id})
            if self.sb_wip_costed:
                l.write({'value': abs(m.value), 'unit_cost': cost})
            else:
                l.value = abs(m.value)
            name = '%s - %s' % (self.name, l.product_id.display_name)
            entry.append((Stock._valuation_account(l.product_id, company), l.value, name, None))
        if self.sb_wip_costed:
            labour, overhead = self.sb_labour_total or 0.0, self.sb_overhead_total or 0.0
            total = sum(lines.mapped('value'))
            entry.append((self.account_id, -(total - labour - overhead), self.env._('%s materials from work in process', self.name), None))
            if labour:
                entry.append((company.sb_labour_absorbed_account_id, -labour, '%s labour absorbed' % self.name, None))
            if overhead:
                entry.append((company.sb_overhead_absorbed_account_id, -overhead, '%s overhead absorbed' % self.name, None))
            givs.write({'sb_fprr_id': self.id})
            self.write({'labour_value': labour, 'overhead_value': overhead, 'build_value': total})
        else:
            entry += [(self.account_id, -l.value, '%s - %s' % (self.name, l.product_id.display_name), None) for l in lines]
        self.move_id = Stock._post_entry(company, self.date, '%s %s' % (self.name, self.memo or self.reference or ''), entry,
                                         move=self._sb_entry_to_reuse())

    def _sb_take_wip_change(self, delta, reason, per_day=None):
        """FPRR costed from work in process: a GIV it took changed by `delta`. The finished goods follow on the FPRR's
        date (Dr finished goods / Cr work in process), shared by value. With `per_day` the lines are added to it (the
        caller posts them) and [(item, date)] to re-cost is returned; without, they are posted and re-costed here."""
        self.ensure_one()
        Stock, company, cur = self.env['sb.stock'], self.company_id, self.currency_id
        if self.state != 'posted' or not self.sb_wip_costed or cur.is_zero(delta) or self.date < Stock._first_open_day(company):
            return []  # a closed FPRR keeps its cost: the difference stays in work in process
        fg = self._sb_live_moves().filtered(lambda m: Stock._direction(m) > 0)
        if not fg:
            return []
        own = per_day is None
        per_day = defaultdict(list) if own else per_day
        base = sum(abs(m.value) for m in fg) or 1.0
        name = self.env._('%(fprr)s cost from work in process (%(why)s)', fprr=self.name, why=reason)
        left = delta
        for i, m in enumerate(fg):
            part = left if i == len(fg) - 1 else cur.round(delta * abs(m.value) / base)
            left -= part
            m.write({'value': abs(m.value) + part, 'sb_recost_total': m.sb_recost_total + part})
            line = Stock._voucher_line(m)  # the FPRR line of this move (an item received on two lines: each its own)
            if line:
                line.write({'value': line.value + part, 'unit_cost': (line.value + part) / line.quantity if line.quantity else line.unit_cost})
            per_day[self.date] += [(Stock._valuation_account(m.product_id, company), part, name, None), (self.account_id, -part, name, None)]
        self.build_value += delta
        follow = [(p, self.date) for p in fg.product_id]
        if not own:
            return follow
        for day, lines in per_day.items():
            Stock._post_entry(company, day, name, lines)
        for p, day in follow:
            Stock._recost(p, company, day, reason=self.name)
        return []

    def _sb_undo_stock(self, day, origin):
        fprr = self.sb_fprr_id if self.voucher_type == 'giv' and self.sb_fprr_id.state == 'posted' else self.browse()
        taken = self._sb_wip_value(fprr.account_id) if fprr else 0.0
        warnings = super()._sb_undo_stock(day, origin)
        if fprr:  # the finished goods give back what this GIV put into them
            fprr._sb_take_wip_change(-taken, origin)
        if self.voucher_type == 'fprr':  # the materials wait in work in process again
            self.sb_wip_giv_ids.write({'sb_fprr_id': False})
        return warnings

    def action_void(self):
        if any(v.sb_migrated for v in self):
            raise UserError(self.env._('Converted history cannot be voided here: correct it with a new voucher.'))
        givs = self.filtered(lambda v: (v.state == 'posted' or v._sb_correcting()) and v.voucher_type == 'giv' and v.sb_fprr_id)
        res = super().action_void()
        controlled(givs.filtered(lambda v: v.state == 'void')).write({'sb_fprr_id': False})
        return res


class SbStock(models.AbstractModel):
    _inherit = 'sb.stock'

    @api.model
    def _recost_pass_on(self, m, delta, company, name, per_day):
        follow = super()._recost_pass_on(m, delta, company, name, per_day)
        sv = m.sb_stock_voucher_id
        if sv and sv.voucher_type == 'giv' and sv.sb_fprr_id:
            # only the change of the line issued to work in process (a GIV can charge the same item to an expense too)
            line = self._voucher_line(m)
            if ((line.account_id if line else False) or sv.account_id) == sv.sb_fprr_id.account_id:
                follow += sv.sb_fprr_id._sb_take_wip_change(delta, name, per_day)
        return follow


class SbAppHome(models.AbstractModel):
    _inherit = 'sb.app.home'

    @api.model
    def _store_types(self):
        return super()._store_types() + ['fprr', 'build']

    @api.model
    def _home_store(self):
        data = super()._home_store()
        data['flows'][0].append({'label': 'Receive (FPRR)', 'icon': 'fa-industry', 'action': 'sonador_mrp.action_sb_fprr',
                                 'create': True, 'hint': 'finished products from production'})
        return data


class SbDocSeriesFprr(models.Model):
    _inherit = 'sb.doc.series'

    doc_type = fields.Selection(selection_add=[('fprr', 'Finished Product Receipt (FPRR)')], ondelete={'fprr': 'cascade'})

    @api.model
    def _sb_extra_name_tables(self):
        # FPRR/26-27/0001 (or FPRR/0001 without the year) unless the company adds an FPRR series (FPRR/01767)
        return dict(super()._sb_extra_name_tables(), fprr=('sb_stock_voucher', 'name'))
