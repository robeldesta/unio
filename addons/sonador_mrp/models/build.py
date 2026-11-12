"""Build voucher (Peachtree "Build Assemblies"): consume components, produce the item, on the voucher date.

Runs as a standard Odoo manufacturing order, so FIFO / average costing, recipes and later work centres and
routings all apply. The finished goods are valued at what the components actually cost plus labour and
overhead absorbed per unit; the voucher posts one entry in the Inventory journal on the voucher date.
"""
from collections import defaultdict
from datetime import datetime

from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_is_zero


class SbStockVoucher(models.Model):
    _inherit = 'sb.stock.voucher'

    voucher_type = fields.Selection(selection_add=[('build', 'Production (Build)')], ondelete={'build': 'cascade'})
    build_product_id = fields.Many2one('product.product', 'Item produced', check_company=True,
                                       domain="[('is_storable', '=', True)]")
    build_bom_id = fields.Many2one('mrp.bom', 'Recipe (BOM)', check_company=True,
                                   domain="[('product_tmpl_id.product_variant_ids', 'in', build_product_id)]")
    build_qty = fields.Float('Quantity produced', digits='Product Unit', default=1.0)
    build_uom_id = fields.Many2one(related='build_product_id.uom_id', string='Unit ')
    production_id = fields.Many2one('mrp.production', 'Manufacturing order', copy=False, readonly=True)
    build_kind = fields.Selection([('fprr', 'Finished Product Receipt (FPRR)'), ('batch', 'Production Batch (into WIP)')],
                                  string='Document', compute='_compute_build_kind', store=True, readonly=False,
                                  help='FPRR: finished goods received from production. Batch: an intermediate made into work in process '
                                       '(e.g. liquid mixed in the tanks).')
    material_value = fields.Monetary('Materials', compute='_compute_build_values', store=True)
    labour_value = fields.Monetary('Labour absorbed', copy=False, readonly=True)
    overhead_value = fields.Monetary('Overhead absorbed', copy=False, readonly=True)
    build_value = fields.Monetary('Cost of production', copy=False, readonly=True)
    sb_labour_total = fields.Monetary('Labour (actual)', copy=False,
                                      help='When set (e.g. from shift hours of a production order), used instead of the recipe rate.')
    sb_overhead_total = fields.Monetary('Overhead (actual)', copy=False)
    build_unit_cost = fields.Float('Cost per unit', compute='_compute_build_values', store=True, digits=(16, 4))

    @api.depends('line_ids.value', 'build_value', 'build_qty', 'voucher_type')
    def _compute_build_values(self):
        for v in self:
            v.material_value = sum(v.line_ids.mapped('value')) if v.voucher_type == 'build' else 0.0
            v.build_unit_cost = v.build_value / v.build_qty if v.voucher_type == 'build' and v.build_qty else 0.0

    @api.depends('build_product_id', 'voucher_type')
    def _compute_build_kind(self):
        for v in self:
            if v.voucher_type != 'build' or not v.build_product_id:
                v.build_kind = False
                continue
            # an item that is itself a component of other recipes is an intermediate (WIP); otherwise it is a finished good
            used = self.env['mrp.bom.line'].search_count([('product_id', '=', v.build_product_id.id)])
            v.build_kind = 'batch' if used else 'fprr'

    def _name_prefix(self):
        if self.voucher_type == 'build':
            return 'FPRR' if self.build_kind == 'fprr' else 'PB'
        return super()._name_prefix()

    def _type_label(self):
        if self.voucher_type == 'build' and self.build_kind:
            return dict(self._fields['build_kind']._description_selection(self.env)).get(self.build_kind)
        return super()._type_label()

    @api.onchange('build_product_id', 'date')
    def _onchange_build_product(self):
        if self.voucher_type == 'build' and self.build_product_id:
            self.build_bom_id = self.env['mrp.bom']._sb_for(self.build_product_id, self.date or fields.Date.context_today(self))

    @api.onchange('build_bom_id', 'build_qty')
    def _onchange_build_bom(self):
        if self.voucher_type != 'build' or not self.build_bom_id:
            return
        self.line_ids = [Command.clear()] + [Command.create(vals) for vals in self._build_lines_vals()]

    def _build_lines_vals(self):
        bom, factor = self.build_bom_id, (self.build_qty or 0.0) / (self.build_bom_id.product_qty or 1.0)
        out = []
        for bl in bom.bom_line_ids:
            p = bl.product_id
            qty = bl.product_uom_id._compute_quantity(bl.product_qty * factor, p.uom_id)
            out.append({'product_id': p.id, 'name': p.display_name, 'qty_standard': qty, 'quantity': qty,
                        'unit_cost': p.standard_price, 'value': qty * p.standard_price})
        return out

    def action_load_recipe(self):
        """Fill the component lines from the recipe (standard quantities for the quantity produced)."""
        for v in self.filtered(lambda x: x.state == 'draft' and x.voucher_type == 'build'):
            if not v.build_bom_id and v.build_product_id:
                v.build_bom_id = self.env['mrp.bom']._sb_for(v.build_product_id, v.date)
            if not v.build_bom_id:
                raise UserError(self.env._('%s has no recipe (bill of materials).', v.build_product_id.display_name))
            v.line_ids = [Command.clear()] + [Command.create(vals) for vals in v._build_lines_vals()]
        return True

    def _check(self):
        if self.voucher_type != 'build':
            return super()._check()
        self.ensure_one()
        if not self.build_product_id or self.build_qty <= 0:
            raise ValidationError(self.env._('Choose the item produced and the quantity.'))
        if not self.build_bom_id:
            self.build_bom_id = self.env['mrp.bom']._sb_for(self.build_product_id, self.date)
        if not self.line_ids.filtered('product_id') and self.build_bom_id:
            self.action_load_recipe()
        if not self.line_ids.filtered(lambda l: l.product_id and l.quantity > 0):
            raise ValidationError(self.env._('%s has no recipe and no components entered: add what was used.',
                                             self.build_product_id.display_name))
        if any(l.quantity < 0 for l in self.line_ids):
            raise ValidationError(self.env._('Component quantities cannot be negative.'))
        if any(l.product_id and not l.product_id.is_storable for l in self.line_ids):
            raise ValidationError(self.env._('Components must be stock items.'))
        lock = self.company_id._get_user_fiscal_lock_date(self.company_id.account_stock_journal_id)
        if self.date <= lock:
            raise ValidationError(self.env._('The books are closed through %s.', lock))
        c = self.company_id
        bom = self.build_bom_id
        if (self.sb_labour_total or self.sb_overhead_total or (bom and (bom.sb_labour_cost or bom.sb_overhead_cost))) and \
                not (c.sb_labour_absorbed_account_id and c.sb_overhead_absorbed_account_id):
            raise ValidationError(self.env._('Set the labour and overhead absorbed accounts in settings.'))

    def _post_stock(self):
        if self.voucher_type != 'build':
            return super()._post_stock()
        Stock, company = self.env['sb.stock'], self.company_id
        stock = self.warehouse_id.lot_stock_id
        lines = self.line_ids.filtered(lambda l: l.product_id and l.quantity > 0)
        wanted = defaultdict(float)
        for l in lines:
            wanted[l.product_id] += l.quantity
        Stock._check_available(company, wanted, stock, self.name, self.date)
        bom = self.build_bom_id
        labour_u, overhead_u = (bom.sb_labour_cost, bom.sb_overhead_cost) if bom else (0.0, 0.0)
        if self.sb_labour_total or self.sb_overhead_total:
            labour_u = (self.sb_labour_total or 0.0) / self.build_qty
            overhead_u = (self.sb_overhead_total or 0.0) / self.build_qty
        product = self.build_product_id
        mo = self.env['mrp.production'].sudo().create({
            'product_id': product.id, 'bom_id': bom.id or False, 'product_qty': self.build_qty, 'product_uom_id': product.uom_id.id,
            'location_src_id': stock.id, 'location_dest_id': stock.id, 'picking_type_id': self.warehouse_id.manu_type_id.id,
            'date_start': datetime.combine(self.date, datetime.min.time()), 'origin': self.name, 'company_id': company.id,
            'extra_cost': labour_u + overhead_u,
        })
        if not bom:
            mo.move_raw_ids = [Command.clear()]
        mo.action_confirm()
        mo.qty_producing = self.build_qty
        mo._set_qty_producing()
        # what was really used: the voucher lines (they start from the recipe and can be changed)
        raw_by_product = defaultdict(lambda: self.env['stock.move'])
        for m in mo.move_raw_ids:
            raw_by_product[m.product_id] |= m
        for p, qty in wanted.items():
            moves = raw_by_product.pop(p, self.env['stock.move'])
            if not moves:
                # a component the recipe does not list (or a build without a recipe): consumed like the others
                moves = self.env['stock.move'].sudo().create({
                    'product_id': p.id, 'product_uom_qty': qty, 'product_uom': p.uom_id.id, 'location_id': stock.id,
                    'location_dest_id': mo.production_location_id.id, 'raw_material_production_id': mo.id, 'company_id': company.id,
                    'origin': self.name, 'warehouse_id': self.warehouse_id.id, 'picking_type_id': mo.picking_type_id.id})
                moves._action_confirm()
            moves[0].write({'quantity': qty, 'picked': True})
            (moves - moves[0]).write({'quantity': 0.0, 'picked': False})
        for moves in raw_by_product.values():
            moves.write({'quantity': 0.0, 'picked': False})
        mo.with_context(skip_consumption=True, skip_backorder=True, skip_redirection=True, skip_immediate=True).button_mark_done()
        if mo.state != 'done':
            raise UserError(self.env._('The production could not be completed (%s).', mo.state))
        when = datetime.combine(self.date, fields.Datetime.now().time())
        done = (mo.move_raw_ids | mo.move_finished_ids).filtered(lambda m: m.state == 'done')
        done.write({'date': when, 'sb_stock_voucher_id': self.id})
        done.move_line_ids.write({'date': when})
        mo.write({'date_finished': when})
        self.production_id = mo
        # the components at their average cost on the build date (same-day receipt rule, back-dated builds re-cost what
        # follows), the finished goods at what the components cost plus labour and overhead absorbed
        raws = mo.move_raw_ids.filtered(lambda m: m.state == 'done')
        if not raws and wanted:
            raise UserError(self.env._('The components of %s could not be consumed.', self.name))
        for p in raws.product_id:
            if Stock._uses_daily_average(p, company):
                for m in raws.filtered(lambda m: m.product_id == p):
                    Stock._sb_cost_out(m, self.date)
                Stock._recost(p, company, self.date, reason=self.name, fresh=raws)
        finished_moves = mo.move_finished_ids.filtered(lambda m: m.state == 'done' and m.product_id == product)
        if finished_moves:
            fin_total = company.currency_id.round(sum(abs(m.value) for m in raws) + (labour_u + overhead_u) * self.build_qty)
            finished_moves[0].value = fin_total
            (finished_moves - finished_moves[0]).write({'value': 0.0})
        # accounting: Dr finished goods / Cr components, labour absorbed, overhead absorbed
        entry, name = [], '%s - %s' % (self.name, product.display_name)
        for l in lines:
            moves = mo.move_raw_ids.filtered(lambda m: m.product_id == l.product_id and m.state == 'done')
            value = sum(abs(m.value) for m in moves)
            l.write({'value': value, 'unit_cost': value / l.quantity if l.quantity else 0.0})
        for m in mo.move_raw_ids.filtered(lambda m: m.state == 'done'):
            entry.append((Stock._valuation_account(m.product_id, company), -abs(m.value), name, None))
        finished = mo.move_finished_ids.filtered(lambda m: m.state == 'done' and m.product_id == product)
        fin_value = sum(abs(m.value) for m in finished)
        labour, overhead = labour_u * self.build_qty, overhead_u * self.build_qty
        entry.append((Stock._valuation_account(product, company), fin_value, name, None))
        if labour:
            entry.append((company.sb_labour_absorbed_account_id, -labour, '%s labour absorbed' % self.name, None))
        if overhead:
            entry.append((company.sb_overhead_absorbed_account_id, -overhead, '%s overhead absorbed' % self.name, None))
        self.write({'labour_value': labour, 'overhead_value': overhead, 'build_value': fin_value})
        self.move_id = Stock._post_entry(company, self.date, '%s %s' % (self.name, self.memo or self.reference or ''), entry,
                                         move=self._sb_entry_to_reuse())
        if Stock._uses_daily_average(product, company):  # finished goods received today: re-cost their issues dated today or later
            Stock._recost(product, company, self.date, reason=self.name)

    def _correctable_types(self):
        return super()._correctable_types() + ('build',)

    def _sb_brings_in(self):
        """A build brings in the item produced (its components go out, checked as they are consumed)."""
        if self.voucher_type != 'build':
            return super()._sb_brings_in()
        qty = defaultdict(float)
        if self.build_product_id:
            qty[(self.build_product_id, self.warehouse_id.lot_stock_id)] += self.build_qty
        return qty


class SbStockVoucherLine(models.Model):
    _inherit = 'sb.stock.voucher.line'

    qty_standard = fields.Float('Recipe qty', digits='Product Unit', help='What the recipe says for the quantity produced.')
    qty_variance = fields.Float('Usage variance', digits='Product Unit', compute='_compute_qty_variance', store=True)

    @api.depends('quantity', 'qty_standard', 'voucher_type')
    def _compute_qty_variance(self):
        for l in self:
            l.qty_variance = (l.quantity - l.qty_standard) if l.voucher_type == 'build' else 0.0
