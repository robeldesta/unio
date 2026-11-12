"""Factory floor records: machines / lines, shifts, loss reasons, production orders with their materials, shift records
(output, waste, downtime) and quality checks. Finishing an order posts a Build voucher (Finished Product Receipt) with the
actual materials, labour and overhead."""
from odoo import api, fields, models
from odoo.exceptions import ValidationError

ORDER_STATES = [('draft', 'Draft'), ('planned', 'Planned'), ('running', 'Running'), ('paused', 'Paused'), ('done', 'Done'),
                ('cancel', 'Cancelled')]


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    sb_unit_weight = fields.Float('Weight per unit (kg)', digits=(16, 4),
                                  help='Net weight of one unit, for yield: kg of material in against kg of product out.')


class SbMfgLine(models.Model):
    _name = 'sb.mfg.line'
    _description = 'Machine / line'
    _order = 'sequence, name'

    name = fields.Char(required=True)
    code = fields.Char()
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True)
    rated_speed = fields.Float('Rated output per hour', help='What the machine makes in an hour at full speed (in the unit of the products it makes).')
    speed_unit = fields.Char('Unit', default='units')
    labour_rate = fields.Float('Labour cost per hour', help='Crew cost per running hour (wages and benefits of the people on the line).')
    overhead_rate = fields.Float('Overhead per hour', help='Power, depreciation, maintenance… absorbed per running hour.')
    crew = fields.Integer('People per shift', default=1)
    color = fields.Integer()
    active = fields.Boolean(default=True)


class SbMfgShift(models.Model):
    _name = 'sb.mfg.shift'
    _description = 'Shift'
    _order = 'sequence, id'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    hour_from = fields.Float(required=True)
    hour_to = fields.Float(required=True)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True)
    active = fields.Boolean(default=True)



class SbMfgReason(models.Model):
    _name = 'sb.mfg.reason'
    _description = 'Downtime / waste reason'
    _order = 'kind, sequence, name'

    name = fields.Char(required=True)
    kind = fields.Selection([('downtime', 'Downtime'), ('waste', 'Waste')], required=True)
    sequence = fields.Integer(default=10)
    planned = fields.Boolean('Planned stop', help='Planned stops (breaks, planned maintenance) do not count against availability.')
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True)
    active = fields.Boolean(default=True)


class SbMfgOrder(models.Model):
    _name = 'sb.mfg.order'
    _description = 'Production order'
    _order = 'date_planned desc, id desc'

    name = fields.Char(required=True, copy=False, default='/')
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True, index=True)
    product_id = fields.Many2one('product.product', 'Product', required=True, index=True)
    bom_id = fields.Many2one('mrp.bom', 'Recipe')
    uom_name = fields.Char(related='product_id.uom_id.name')
    qty_planned = fields.Float('Quantity to make', required=True, digits='Product Unit')
    line_id = fields.Many2one('sb.mfg.line', 'Machine / line', index=True)
    date_planned = fields.Date('Planned day', index=True)
    shift_id = fields.Many2one('sb.mfg.shift', 'Shift')
    state = fields.Selection(ORDER_STATES, default='planned', required=True, index=True)
    priority = fields.Boolean('Urgent')
    partner_id = fields.Many2one('res.partner', 'For customer')
    sale_order_id = fields.Many2one('sale.order', 'Sales order')
    lot_name = fields.Char('Batch / lot', help='Printed on the cartons; links the output to the material lots used.')
    started_at = fields.Datetime()
    finished_at = fields.Datetime()
    material_ids = fields.One2many('sb.mfg.material', 'order_id')
    entry_ids = fields.One2many('sb.mfg.entry', 'order_id')
    check_ids = fields.One2many('sb.mfg.check', 'order_id')
    qty_good = fields.Float('Good output', compute='_compute_totals', store=True, digits='Product Unit')
    qty_waste = fields.Float('Rejects', compute='_compute_totals', store=True, digits='Product Unit')
    run_minutes = fields.Integer(compute='_compute_totals', store=True)
    down_minutes = fields.Integer(compute='_compute_totals', store=True)
    voucher_id = fields.Many2one('sb.stock.voucher', 'Finished Product Receipt', readonly=True, copy=False)
    note = fields.Text()

    @api.depends('entry_ids.good_qty', 'entry_ids.loss_ids.qty', 'entry_ids.minutes_run', 'entry_ids.loss_ids.minutes')
    def _compute_totals(self):
        for o in self:
            o.qty_good = sum(o.entry_ids.mapped('good_qty'))
            losses = o.entry_ids.loss_ids
            o.qty_waste = sum(losses.filtered(lambda l: l.kind == 'waste' and not l.in_kg).mapped('qty'))
            o.run_minutes = sum(o.entry_ids.mapped('minutes_run'))
            o.down_minutes = sum(losses.filtered(lambda l: l.kind == 'downtime').mapped('minutes'))

    @api.model_create_multi
    def create(self, vals_list):
        for v in vals_list:
            if v.get('name', '/') == '/':
                v['name'] = self.env['ir.sequence'].next_by_code('sb.mfg.order') or 'MO/NEW'
        return super().create(vals_list)


class SbMfgMaterial(models.Model):
    _name = 'sb.mfg.material'
    _description = 'Production order material'
    _order = 'order_id, sequence, id'

    order_id = fields.Many2one('sb.mfg.order', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one('product.product', required=True)
    uom_name = fields.Char(related='product_id.uom_id.name')
    qty_required = fields.Float('Recipe quantity', digits='Product Unit')
    qty_consumed = fields.Float('Used', digits='Product Unit')
    lots = fields.Char('Lots used', help='Lot or roll numbers of the material used, comma separated.')


class SbMfgEntry(models.Model):
    """One shift's record on an order: what was made, lost and stopped."""
    _name = 'sb.mfg.entry'
    _description = 'Shift record'
    _order = 'date desc, id desc'

    order_id = fields.Many2one('sb.mfg.order', required=True, ondelete='cascade', index=True)
    line_id = fields.Many2one(related='order_id.line_id', store=True)
    product_id = fields.Many2one(related='order_id.product_id', store=True)
    company_id = fields.Many2one(related='order_id.company_id', store=True)
    date = fields.Date(required=True, default=fields.Date.context_today, index=True)
    shift_id = fields.Many2one('sb.mfg.shift')
    employee_id = fields.Many2one('hr.employee', 'Operator / supervisor')
    minutes_run = fields.Integer('Minutes running')
    good_qty = fields.Float('Good output', digits='Product Unit')
    loss_ids = fields.One2many('sb.mfg.loss', 'entry_id')
    note = fields.Char()
    by_id = fields.Many2one('res.users', default=lambda s: s.env.user)


class SbMfgLoss(models.Model):
    _name = 'sb.mfg.loss'
    _description = 'Waste or downtime'

    entry_id = fields.Many2one('sb.mfg.entry', required=True, ondelete='cascade', index=True)
    order_id = fields.Many2one(related='entry_id.order_id', store=True, index=True)
    line_id = fields.Many2one(related='entry_id.line_id', store=True)
    date = fields.Date(related='entry_id.date', store=True)
    kind = fields.Selection([('downtime', 'Downtime'), ('waste', 'Waste')], required=True)
    reason_id = fields.Many2one('sb.mfg.reason', required=True)
    planned = fields.Boolean(related='reason_id.planned', store=True)
    minutes = fields.Integer()
    qty = fields.Float(digits='Product Unit')
    in_kg = fields.Boolean('Weighed in kg', help='Waste weighed (e.g. trim, broke) rather than counted in product units.')

    @api.constrains('kind', 'minutes', 'qty')
    def _check(self):
        for l in self:
            if l.kind == 'downtime' and l.minutes <= 0:
                raise ValidationError(self.env._('Give the minutes stopped.'))
            if l.kind == 'waste' and l.qty <= 0:
                raise ValidationError(self.env._('Give the quantity wasted.'))


class SbMfgCheckTemplate(models.Model):
    _name = 'sb.mfg.check.template'
    _description = 'Quality check'
    _order = 'sequence, id'

    name = fields.Char(required=True, help='e.g. GSM, ply, roll weight, sheets per roll, carton seal.')
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True)
    product_ids = fields.Many2many('product.product', string='Products', help='Empty: every product.')
    kind = fields.Selection([('number', 'Measured value'), ('pass', 'Pass / fail')], default='number', required=True)
    min_value = fields.Float('Min')
    max_value = fields.Float('Max')
    unit = fields.Char()
    blocking = fields.Boolean('Must pass to finish', default=True)
    active = fields.Boolean(default=True)


class SbMfgCheck(models.Model):
    _name = 'sb.mfg.check'
    _description = 'Quality check result'
    _order = 'order_id, sequence, id'

    order_id = fields.Many2one('sb.mfg.order', required=True, ondelete='cascade', index=True)
    template_id = fields.Many2one('sb.mfg.check.template')
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    kind = fields.Selection([('number', 'Measured value'), ('pass', 'Pass / fail')], default='number', required=True)
    min_value = fields.Float()
    max_value = fields.Float()
    unit = fields.Char()
    blocking = fields.Boolean(default=True)
    value = fields.Float()
    result = fields.Selection([('pending', 'Not done'), ('pass', 'Pass'), ('fail', 'Fail')], default='pending', required=True)
    by_id = fields.Many2one('res.users')
    at = fields.Datetime()
    note = fields.Char()
