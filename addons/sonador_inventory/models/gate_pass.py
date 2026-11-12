"""Gate pass: what leaves (or comes back through) the gate, checked by security.

It moves no stock and posts nothing: the GIV, transfer, delivery or cash sale behind it already did. A gate pass is
made from that document (items and quantities copied) or written by hand for things that are not stock: tools out
for repair (returnable), waste, personal belongings. Returnable passes stay open until the goods are back.
"""
from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.sonador_books.models.ethiopian_calendar import format_ethiopian


PURPOSES = [
    ('delivery', 'Delivery to customer'),
    ('issue', 'Issue (GIV)'),
    ('transfer', 'Transfer to another store'),
    ('returnable', 'Returnable (repair, loan, sample)'),
    ('non_returnable', 'Non-returnable'),
    ('return_in', 'Coming back in'),
    ('waste', 'Waste / scrap'),
    ('personal', 'Personal belongings'),
    ('other', 'Other'),
]


class SbGatePass(models.Model):
    _name = 'sb.gate.pass'
    _description = 'Gate Pass'
    _inherit = ['mail.thread']
    _order = 'date desc, id desc'
    _check_company_auto = True

    name = fields.Char('Number', default='/', copy=False, readonly=True, index=True)
    state = fields.Selection([('draft', 'Draft'), ('issued', 'Issued'), ('out', 'Passed the gate'),
                              ('returned', 'Returned'), ('cancel', 'Cancelled')],
                             default='draft', tracking=True, copy=False, index=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company)
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    date_ec = fields.Char('E.C. Date', compute='_compute_date_ec')
    direction = fields.Selection([('out', 'Out'), ('in', 'In')], default='out', required=True)
    purpose = fields.Selection(PURPOSES, required=True, default='delivery', tracking=True)
    warehouse_id = fields.Many2one('stock.warehouse', 'Store', check_company=True,
                                   default=lambda s: s.env['stock.warehouse'].search([('company_id', '=', s.env.company.id)], limit=1))
    partner_id = fields.Many2one('res.partner', 'Customer / to', check_company=True)
    destination = fields.Char('Destination', help='Site, branch, department or address the goods go to.')
    vehicle = fields.Char('Vehicle plate')
    driver = fields.Char('Driver')
    driver_phone = fields.Char('Driver phone')
    carried_by = fields.Char('Carried by', help='When nobody drives: the person who takes the goods out.')
    stock_voucher_id = fields.Many2one('sb.stock.voucher', 'Inventory document', check_company=True, copy=False,
                                       help='GIV, transfer or GRN return the gate pass is for.')
    source = fields.Char('Source documents', help='Delivery orders, invoices, cash sales ... behind this gate pass.')
    line_ids = fields.One2many('sb.gate.pass.line', 'pass_id', string='Items', copy=True)
    expected_return = fields.Date('Expected back by')
    returned_date = fields.Date('Came back on', copy=False, tracking=True)
    issued_by = fields.Many2one('res.users', 'Issued by', copy=False, readonly=True)
    out_time = fields.Datetime('Left the gate', copy=False, readonly=True)
    guard = fields.Char('Security guard', copy=False)
    note = fields.Text()

    @api.depends('date')
    def _compute_date_ec(self):
        for g in self:
            g.date_ec = format_ethiopian(g.date)

    @api.onchange('purpose')
    def _onchange_purpose(self):
        self.direction = 'in' if self.purpose == 'return_in' else 'out'

    def _next_name(self):
        self.ensure_one()
        # the company's gate pass series (pad) if it has one; else GP/26-27/0001, or GP/0001 without the year
        Series = self.env['sb.doc.series'].sudo()  # whoever issues gate passes, not only the accounting users
        series = Series._for('gate_pass', company=self.company_id)
        if series:
            return series._take(self.date, what=self.env._('Gate pass'))
        return Series._sb_plain_next('sb.gate.pass', 'GP', self.date, self.company_id)

    def action_issue(self):
        for g in self:
            if g.state != 'draft':
                continue
            if not g.line_ids:
                raise ValidationError(self.env._('List what passes the gate.'))
            if g.purpose == 'returnable' and not g.expected_return:
                raise ValidationError(self.env._('A returnable gate pass needs the date the goods are expected back.'))
            g.write({'state': 'issued', 'name': g.name if g.name != '/' else g._next_name(), 'issued_by': self.env.uid})
        return True

    def action_print(self):
        self.filtered(lambda g: g.state == 'draft').action_issue()
        return self.env.ref('sonador_inventory.action_report_gate_pass').report_action(self)

    sb_can_gate = fields.Boolean(compute='_compute_sb_can_gate', help='Gate security (or an administrator): lets goods out.')

    @api.depends_context('uid')
    def _compute_sb_can_gate(self):
        ok = self._sb_gate_user()
        for g in self:
            g.sb_can_gate = ok

    def _sb_gate_user(self):
        user = self.env.user
        gate = self.env.ref('sonador_me.group_gate', raise_if_not_found=False)
        return self.env.su or user.has_group('base.group_system') or \
            (gate in user.all_group_ids if gate else user.has_group('sonador_inventory.group_sb_store_user'))

    def action_out(self):
        """Security: the goods left (or came in through) the gate."""
        if not self._sb_gate_user():
            raise UserError(self.env._('Only gate security lets goods out.'))
        for g in self:
            if g.state != 'issued':
                raise UserError(self.env._('%s is not issued.', g.display_name))
            g.write({'state': 'out', 'out_time': fields.Datetime.now()})
        return True

    def action_returned(self):
        if not self._sb_gate_user():
            raise UserError(self.env._('Only gate security records goods coming back.'))
        for g in self:
            if g.purpose != 'returnable' or g.state != 'out':
                raise UserError(self.env._('Only a returnable gate pass that left the gate can come back.'))
            g.write({'state': 'returned', 'returned_date': fields.Date.context_today(self)})
        return True

    def action_cancel(self):
        if not (self.env.su or self.env.user.has_group('sonador_inventory.group_sb_store_user')):
            raise UserError(self.env._('Only the store cancels a gate pass.'))
        if self.filtered(lambda g: g.state in ('out', 'returned')):
            raise UserError(self.env._('The goods already passed the gate: the gate pass cannot be cancelled.'))
        self.write({'state': 'cancel'})
        return True

    def action_draft(self):
        self.filtered(lambda g: g.state == 'cancel').write({'state': 'draft'})
        return True

    def unlink(self):
        if self.filtered(lambda g: g.name != '/'):
            raise UserError(self.env._('Numbered gate passes cannot be deleted. Cancel them.'))
        return super().unlink()

    @api.model
    def _from_lines(self, vals, lines):
        """lines: [(product, quantity, uom, description)]"""
        vals['line_ids'] = [Command.create({'product_id': p.id if p else False, 'name': d or (p.display_name if p else ''),
                                            'quantity': q, 'uom_id': (u or p.uom_id).id if (u or p) else False})
                            for p, q, u, d in lines if q]
        return self.create(vals)

    def _open(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id, 'view_mode': 'form',
                'name': self.env._('Gate Pass'), 'target': 'current'}


class SbGatePassLine(models.Model):
    _name = 'sb.gate.pass.line'
    _description = 'Gate Pass Item'
    _order = 'sequence, id'

    pass_id = fields.Many2one('sb.gate.pass', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one('product.product', 'Item')
    name = fields.Char('Description', required=True)
    quantity = fields.Float(digits='Product Unit', default=1.0)
    uom_id = fields.Many2one('uom.uom', 'Unit')
    remarks = fields.Char()

    @api.onchange('product_id')
    def _onchange_product(self):
        if self.product_id:
            self.name = self.product_id.display_name
            self.uom_id = self.product_id.uom_id


class SbStockVoucher(models.Model):
    _inherit = 'sb.stock.voucher'

    gate_pass_ids = fields.One2many('sb.gate.pass', 'stock_voucher_id', string='Gate passes')
    gate_pass_count = fields.Integer(compute='_compute_gate_pass_count')

    def _compute_gate_pass_count(self):
        for v in self:
            v.gate_pass_count = len(v.gate_pass_ids.filtered(lambda g: g.state != 'cancel'))

    def action_gate_pass(self):
        """Gate pass for what this document takes out (GIV, transfer)."""
        self.ensure_one()
        existing = self.gate_pass_ids.filtered(lambda g: g.state != 'cancel')[:1]
        if existing:
            return existing._open()
        if self.state != 'posted':
            raise UserError(self.env._('Post %s first.', self.display_name))
        purpose = {'giv': 'issue', 'transfer': 'transfer'}.get(self.voucher_type, 'other')
        dest = self.warehouse_dest_id.name if self.voucher_type == 'transfer' else (self.department or '')
        g = self.env['sb.gate.pass']._from_lines({
            'purpose': purpose, 'date': fields.Date.context_today(self), 'warehouse_id': self.warehouse_id.id,
            'stock_voucher_id': self.id, 'source': self.name, 'destination': dest, 'carried_by': self.received_by,
            'partner_id': self.partner_id.id,
        }, [(l.product_id, l.quantity, l.uom_id, l.name) for l in self.line_ids.filtered('product_id')])
        return g._open()

    def action_print_voucher(self):
        return self.env.ref('sonador_inventory.action_report_stock_voucher').report_action(self)
