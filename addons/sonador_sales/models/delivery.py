"""Delivery orders (dispatch records) with batches: each batch is one truck load.

The invoice already took the goods out of available stock (into 'Awaiting dispatch') and booked the cost of sales;
a batch moves them on to the customer, with vehicle, driver, delivery method and the invoiced / loaded / gate exit /
unloading / return times.

Names, states and wording as the delivery orders Eco Green used before (Robel: "keep in line with Odoo 18"):
a delivery order is Draft, Ready, In Transit, Delivered, Done or Cancelled; its batches are "Batch 1", "Batch 2" ...
(shown as "DO/05753 — Batch 1" where the delivery order is not on screen, as Odoo 18 printed it), each Draft, Done or Cancelled.
A company can switch on strict delivery batches (Eco Green): every posted invoice / cash sale opens its own draft
batch (the invoice and its time on the batch), a batch is validated only with the vehicle and every time in order,
only a validated batch prints, and the delivery order is closed by hand with Mark as Done.
"""
from collections import defaultdict

from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare

from odoo.addons.sonador_books.models.ethiopian_calendar import format_ethiopian

METHODS = [('company', 'Company Delivery'), ('pickup', 'Own Customer Pickup'), ('rented', 'Rented Vehicle Delivery')]
DO_STATES = [('draft', 'Draft'), ('ready', 'Ready'), ('in_transit', 'In Transit'), ('delivered', 'Delivered'), ('done', 'Done'),
             ('cancel', 'Cancelled')]
BATCH_STATES = [('draft', 'Draft'), ('done', 'Done'), ('cancel', 'Cancelled')]
VALIDATION = [('incomplete', 'Incomplete'), ('complete', 'Complete'), ('validated', 'Validated')]
# the times of a load, in the order they happen (each must be after the one before)
TIMES = ('invoiced_time', 'loaded_time', 'gate_time', 'unloading_time', 'return_time')


def _next_name(env, model, prefix, day, company):
    """The company's delivery order series (pad) if it has one (DO/05753); else DO/26-27/0001, or DO/0001 without the year."""
    Series = env['sb.doc.series'].sudo()  # whoever dispatches, not only the accounting users
    series = Series._for('delivery', company=company) if model == 'sb.delivery.order' else Series
    if series:
        return series._take(day, what=env._('Delivery order'))
    return Series._sb_plain_next(model, prefix, day, company)


def _check_order(rec, times, err):
    """Each time that is set must be after the one before it (the same minute is refused too)."""
    filled = [(f, rec[f]) for f in times if rec[f]]
    for (f1, t1), (f2, t2) in zip(filled, filled[1:]):
        if t2 <= t1:
            raise err(rec.env._('%(f2)s must be after %(f1)s (%(rec)s).', f2=rec._fields[f2].string, f1=rec._fields[f1].string,
                                rec=rec.display_name))


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_delivery_strict = fields.Boolean(
        'Strict delivery batches',
        help='Each posted invoice or cash sale opens its own draft delivery batch (the invoice and the invoiced time on the batch). '
             'A batch is validated only with the delivery vehicle (company delivery) and the Invoiced, Loaded, Gate Exit and '
             'Unloading times, each after the one before (own customer pickup: Loaded and Gate Exit). Only a validated batch '
             'prints (administrators: any time). The delivery order is closed with Mark as Done.\n'
             'Off: a batch needs a vehicle or driver, and the delivery order is done by itself once everything invoiced is delivered.')


class SbDeliveryOrder(models.Model):
    _name = 'sb.delivery.order'
    _description = 'Delivery Order'
    _inherit = ['mail.thread']
    _order = 'date desc, id desc'
    _check_company_auto = True

    name = fields.Char('Reference', default='/', copy=False, readonly=True, index=True)
    date = fields.Date(required=True, default=fields.Date.context_today)
    date_ec = fields.Char('E.C. Date', compute='_compute_date_ec')
    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company)
    partner_id = fields.Many2one('res.partner', 'Customer', required=True, check_company=True)
    sale_order_id = fields.Many2one('sale.order', 'Sale Order', check_company=True, readonly=True)
    warehouse_id = fields.Many2one('stock.warehouse', required=True, check_company=True)
    voucher_ids = fields.One2many('sb.voucher', 'sb_delivery_id', string='Invoices')
    dispatch_ref = fields.Char('Dispatch Voucher')
    notes = fields.Text('Notes', help='Site, contact, unloading instructions; printed on every delivery note.')
    line_ids = fields.One2many('sb.delivery.order.line', 'delivery_id', string='Delivery Lines')
    batch_ids = fields.One2many('sb.delivery.batch', 'delivery_id', string='Batches')
    state = fields.Selection(DO_STATES, string='Status', default='draft', required=True, readonly=True, copy=False, tracking=True,
                             index=True)
    sb_to_deliver = fields.Boolean('To deliver', compute='_compute_to_deliver', store=True, index=True,
                                   help='Invoiced quantities not yet delivered (and not cancelled).')
    uses_batches = fields.Boolean('Uses Batches', default=True, copy=False,
                                  help='Stock leaves per batch. Off only on one-shot delivery orders brought over from the earlier system.')
    batch_count = fields.Integer('Batches', compute='_compute_batch_count')
    validation_status = fields.Selection(VALIDATION, 'Validation Status', compute='_compute_validation_status', store=True)
    # one-shot (not batched) delivery orders keep their logistics here; batched ones on each batch
    invoiced_time = fields.Datetime('Invoiced Time', tracking=True)
    loaded_time = fields.Datetime('Loaded Time', tracking=True)
    gate_time = fields.Datetime('Gate Exit Time', tracking=True)
    unloading_time = fields.Datetime('Unloading Time', tracking=True)
    return_time = fields.Datetime('Return Time', tracking=True)
    sb_legacy_ref = fields.Char('Brought over from', copy=False, index='btree_not_null', readonly=True,
                                help='Delivery orders brought over from the earlier system: O18|<id>.')

    @api.depends('date')
    def _compute_date_ec(self):
        for d in self:
            d.date_ec = format_ethiopian(d.date)

    @api.depends('line_ids.qty_remaining', 'state')
    def _compute_to_deliver(self):
        for d in self:
            d.sb_to_deliver = d.state != 'cancel' and any(l.qty_remaining > 1e-6 for l in d.line_ids)

    @api.depends('batch_ids.state')
    def _compute_batch_count(self):
        for d in self:
            d.batch_count = len(d.batch_ids.filtered(lambda b: b.state != 'cancel'))

    @api.depends('state', 'uses_batches', 'batch_ids.state', 'invoiced_time', 'loaded_time', 'gate_time')
    def _compute_validation_status(self):
        for d in self:
            if d.uses_batches:
                if d.batch_ids.filtered(lambda b: b.state == 'done'):
                    d.validation_status = 'validated' if d.state == 'done' else 'complete'
                else:
                    d.validation_status = 'incomplete'
            elif d.invoiced_time and d.loaded_time and d.gate_time:
                d.validation_status = 'validated' if d.state in ('ready', 'in_transit', 'delivered', 'done') else 'complete'
            else:
                d.validation_status = 'incomplete'

    @api.constrains(*TIMES)
    def _check_time_sequence(self):
        for d in self:
            _check_order(d, TIMES, ValidationError)

    def _sb_sync_state(self):
        """Draft until a batch is validated; then Ready, and In Transit / Delivered / Done from the delivery order's own
        times (one-shot orders). Done otherwise by Mark as Done, or (not strict) once everything invoiced is delivered."""
        for d in self.filtered(lambda x: x.state not in ('done', 'cancel')):
            moved = bool(d.batch_ids.filtered(lambda b: b.state == 'done')) or bool(not d.uses_batches and d.loaded_time)
            if not moved:
                state = 'draft'
            elif d.return_time:
                state = 'done'
            elif d.unloading_time:
                state = 'delivered'
            elif d.gate_time:
                state = 'in_transit'
            elif d.loaded_time or d.invoiced_time:
                state = 'ready'
            else:
                state = 'draft'
            if moved and not d.company_id.sb_delivery_strict and not d.sb_to_deliver:
                state = 'done'
            if d.state != state:
                d.state = state

    def action_mark_done(self):
        """Mark as Done: once at least one batch is validated."""
        for d in self.filtered(lambda x: x.state not in ('done', 'cancel')):
            if not d.batch_ids.filtered(lambda b: b.state == 'done') and not (d.sb_legacy_ref and d.state != 'draft'):
                raise UserError(self.env._('Validate at least one delivery batch (to create stock moves) before marking the '
                                           'delivery order as done.'))
            d.state = 'done'
        return True

    def action_cancel(self):
        for d in self:
            if d.batch_ids.filtered(lambda b: b.state == 'done'):
                raise UserError(self.env._('%s has a validated batch: it cannot be cancelled (record a sales return).', d.name))
            if d.voucher_ids.filtered(lambda v: v.state == 'posted'):
                raise UserError(self.env._('%s belongs to a posted invoice: void the invoice instead.', d.name))
        self.batch_ids.filtered(lambda b: b.state == 'draft').write({'state': 'cancel'})
        self.write({'state': 'cancel'})
        return True

    def action_draft(self):
        """Reset to Draft (a done or cancelled delivery order); the state then follows its batches again."""
        self.filtered(lambda d: d.state in ('done', 'cancel')).write({'state': 'draft'})
        self._sb_sync_state()
        return True

    def action_refresh_invoiced_time(self):
        """Refresh Invoiced Time: when the first invoice / cash sale of the delivery order was made."""
        for d in self:
            v = d.voucher_ids.filtered(lambda x: x.state == 'posted').sorted('create_date')[:1]
            if v:
                d.invoiced_time = v.create_date
        self._sb_sync_state()
        return True

    @api.model
    def _sb_for_voucher(self, voucher):
        dom = [('company_id', '=', voucher.company_id.id), ('state', 'not in', ('done', 'cancel')),
               ('partner_id', '=', voucher.partner_id.commercial_partner_id.id)]
        dom.append(('sale_order_id', '=', voucher.sb_sale_order_id.id) if voucher.sb_sale_order_id else ('sale_order_id', '=', False))
        do = self.search(dom, limit=1) if voucher.sb_sale_order_id else self.browse()
        if not do:
            do = self.create({'partner_id': voucher.partner_id.commercial_partner_id.id, 'sale_order_id': voucher.sb_sale_order_id.id,
                              'warehouse_id': voucher.sb_warehouse_id.id, 'date': voucher.date, 'company_id': voucher.company_id.id})
        voucher.sb_delivery_id = do
        if not do.invoiced_time:
            do.invoiced_time = fields.Datetime.now()
        do._refresh_lines()
        if do.company_id.sb_delivery_strict:
            do._sb_batch_for_invoice(voucher)
        do._sb_sync_state()
        return do

    def _sb_batch_for_invoice(self, voucher):
        """Strict batches: the invoice's own draft batch, for what it sent to Awaiting dispatch and no draft batch carries yet."""
        self.ensure_one()
        Batch = self.env['sb.delivery.batch']
        if self.batch_ids.filtered(lambda b: b.invoice_id == voucher and b.state != 'cancel'):
            return Batch
        disp = self.warehouse_id.sb_dispatch_location_id
        qty = defaultdict(float)
        for m in voucher.sb_stock_move_ids.filtered(lambda m: m.state == 'done' and m.location_dest_id == disp):
            qty[m.product_id] += m.quantity
        on_batches = self._sb_on_draft_batches()
        lines = []
        for l in self.line_ids:
            q = min(qty.get(l.product_id, 0.0), l.qty_remaining - on_batches.get(l.product_id, 0.0))
            if q > 1e-6:
                lines.append(Command.create({'product_id': l.product_id.id, 'quantity': q}))
        if not lines:
            return Batch
        b = Batch.create({'delivery_id': self.id, 'date': voucher.date, 'invoice_id': voucher.id,
                          'invoiced_time': fields.Datetime.now(), 'line_ids': lines})
        self.message_post(body=self.env._('Draft delivery batch %(b)s created from invoice %(inv)s (%(q)s).',
                                          b=b.name, inv=voucher.name, q=b.total_qty))
        return b

    def _sb_on_draft_batches(self):
        on = defaultdict(float)
        for l in self.batch_ids.filtered(lambda b: b.state == 'draft').line_ids:
            on[l.product_id] += l.quantity
        return on

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for d in records.filtered(lambda r: r.name == '/'):
            d.name = _next_name(self.env, self._name, 'DO', d.date, d.company_id)
        return records

    def _quantities(self):
        """{product: [invoiced (into awaiting dispatch), delivered]} from the stock moves."""
        self.ensure_one()
        q = defaultdict(lambda: [0.0, 0.0])
        disp = self.warehouse_id.sb_dispatch_location_id
        for v in self.voucher_ids.filtered(lambda x: x.state in ('posted', 'void')):
            for m in v.sb_stock_move_ids.filtered(lambda m: m.state == 'done'):
                if m.location_dest_id == disp:
                    q[m.product_id][0] += m.quantity
                elif m.location_id == disp:  # void: goods back from awaiting dispatch
                    q[m.product_id][0] -= m.quantity
        for b in self.batch_ids.filtered(lambda b: b.state == 'done'):
            for l in b.line_ids:
                q[l.product_id][1] += l.quantity
        return q

    def _refresh_lines(self):
        for d in self:
            q = d._quantities()
            if d.sb_legacy_ref:
                # brought over: the invoiced quantities came with it (its invoices moved no stock here); delivered from the batches
                for l in d.line_ids:
                    l.qty_delivered = q[l.product_id][1] if l.product_id in q else 0.0
                continue
            existing = {l.product_id: l for l in d.line_ids}
            for p, (inv, dlv) in q.items():
                vals = {'qty_invoiced': inv, 'qty_delivered': dlv}
                if p in existing:
                    existing[p].write(vals)
                else:
                    d.line_ids = [Command.create(dict(vals, product_id=p.id))]

    def action_sb_view_sale_order(self):
        return self.env['sb.voucher']._sb_open(self.sale_order_id, self.sale_order_id.name)

    def action_sb_view_invoices(self):
        return self.env['sb.voucher']._sb_open(self.voucher_ids, self.env._('Invoices'))

    def action_new_batch(self):
        """Add Batch: a draft batch for what is invoiced and not yet on a batch."""
        self.ensure_one()
        if self.state == 'cancel':
            raise UserError(self.env._('Cannot add a batch on a cancelled delivery.'))
        on = self._sb_on_draft_batches()
        lines = [Command.create({'product_id': l.product_id.id, 'quantity': l.qty_remaining - on.get(l.product_id, 0.0)})
                 for l in self.line_ids if l.qty_remaining - on.get(l.product_id, 0.0) > 1e-6]
        if not lines:
            raise UserError(self.env._('Nothing left to deliver on %s. All invoiced quantity is already on batches.',
                                       self.sale_order_id.name or self.name))
        b = self.env['sb.delivery.batch'].create({'delivery_id': self.id, 'line_ids': lines})
        return {'type': 'ir.actions.act_window', 'res_model': 'sb.delivery.batch', 'res_id': b.id, 'view_mode': 'form',
                'name': self.env._('Delivery Batch')}

    def action_view_batches(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'sb.delivery.batch', 'view_mode': 'list,form',
                'domain': [('delivery_id', '=', self.id)], 'context': {'default_delivery_id': self.id},
                'name': self.env._('Batches — %s', self.name)}


class SbDeliveryOrderLine(models.Model):
    _name = 'sb.delivery.order.line'
    _description = 'Delivery Order Line'

    delivery_id = fields.Many2one('sb.delivery.order', required=True, ondelete='cascade', index=True)
    product_id = fields.Many2one('product.product', 'Product', required=True)
    uom_id = fields.Many2one(related='product_id.uom_id', string='Unit of Measure')
    qty_invoiced = fields.Float('Invoiced', digits='Product Unit')
    qty_delivered = fields.Float('Delivered', digits='Product Unit')
    qty_remaining = fields.Float('To deliver', compute='_compute_remaining', store=True, digits='Product Unit')

    @api.depends('qty_invoiced', 'qty_delivered')
    def _compute_remaining(self):
        for l in self:
            l.qty_remaining = l.qty_invoiced - l.qty_delivered


class SbDeliveryBatch(models.Model):
    _name = 'sb.delivery.batch'
    _description = 'Delivery Order Batch'
    _inherit = ['mail.thread']
    _order = 'date desc, id desc'
    _rec_names_search = ['reference', 'name']

    name = fields.Char('Batch', default='/', copy=False, readonly=True)
    reference = fields.Char('Print Reference', compute='_compute_reference', store=True, index=True,
                            help='The delivery order and the batch, as printed: DO/05753 — Batch 1.')
    delivery_id = fields.Many2one('sb.delivery.order', 'Delivery Order', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='delivery_id.company_id', store=True)
    partner_id = fields.Many2one(related='delivery_id.partner_id', store=True, string='Customer')
    date = fields.Date(required=True, default=fields.Date.context_today)
    date_ec = fields.Char('E.C. Date', compute='_compute_date_ec')
    state = fields.Selection(BATCH_STATES, string='Status', default='draft', required=True, tracking=True, copy=False)
    invoice_id = fields.Many2one('sb.voucher', 'Invoice', copy=False, index='btree_not_null', readonly=True,
                                 help='The invoice / cash sale that opened this batch.')
    delivery_method = fields.Selection(METHODS, 'Delivery Method', default='company', required=True, tracking=True,
                                       help='Company Delivery requires a company vehicle and logistics times. '
                                            'Rented Vehicle Delivery and Own Customer Pickup skip the company vehicle.')
    vehicle = fields.Char('Vehicle (plate)', tracking=True)
    driver = fields.Char('Driver')
    invoiced_time = fields.Datetime('Invoiced Time', tracking=True)
    loaded_time = fields.Datetime('Loaded Time', tracking=True)
    gate_time = fields.Datetime('Gate Exit Time', tracking=True)
    unloading_time = fields.Datetime('Unloading Time', tracking=True)
    return_time = fields.Datetime('Return Time', tracking=True)
    received_by = fields.Char('Received by (customer)')
    print_count = fields.Integer('Print Count', default=0, copy=False, readonly=True, help='Number of times this batch DO has been printed.')
    can_print_batch = fields.Boolean('Can Print Batch', compute='_compute_can_print_batch')
    batch_sequence_display = fields.Integer('Batch No', compute='_compute_batch_sequence_display')
    sb_strict = fields.Boolean(related='company_id.sb_delivery_strict')
    sb_legacy_ref = fields.Char('Brought over from', copy=False, index='btree_not_null', readonly=True)
    line_ids = fields.One2many('sb.delivery.batch.line', 'batch_id', string='Batch Lines')
    move_ids = fields.One2many('stock.move', 'sb_delivery_batch_id', string='Stock Moves')
    total_qty = fields.Float('Total Qty', compute='_compute_total', digits='Product Unit')

    @api.depends('date')
    def _compute_date_ec(self):
        for b in self:
            b.date_ec = format_ethiopian(b.date)

    @api.depends('line_ids.quantity')
    def _compute_total(self):
        for b in self:
            b.total_qty = sum(b.line_ids.mapped('quantity'))

    @api.depends('name', 'delivery_id.name')
    def _compute_reference(self):
        for b in self:
            b.reference = '%s — %s' % (b.delivery_id.name or '', b.name or '')  # as printed before: DO/05753 — Batch 1

    @api.depends('state')
    @api.depends_context('uid')
    def _compute_can_print_batch(self):
        admin = self.env.user.has_group('base.group_system')
        for b in self:
            b.can_print_batch = b.state != 'cancel' and (admin or not b.company_id.sb_delivery_strict or b.state == 'done')

    @api.depends('delivery_id.batch_ids.state')
    def _compute_batch_sequence_display(self):
        for b in self:
            siblings = b.delivery_id.batch_ids.filtered(lambda x: x.state != 'cancel').sorted('id')
            b.batch_sequence_display = (siblings.ids.index(b.id) + 1) if b.id in siblings.ids else (len(siblings) or 1)

    @api.depends('reference')
    def _compute_display_name(self):
        for b in self:
            b.display_name = b.reference or b.name

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for b in records.filtered(lambda r: not r.name or r.name == '/'):
            b.name = self.env._('Batch %s', len(b.delivery_id.batch_ids))
        return records

    def _check_times(self):
        """Before the goods leave. Strict: vehicle and the times of the delivery method; always: each time after the one before."""
        for b in self:
            if b.company_id.sb_delivery_strict:
                pickup = b.delivery_method == 'pickup'
                need = ('loaded_time', 'gate_time') if pickup else ('invoiced_time', 'loaded_time', 'gate_time', 'unloading_time')
                if b.delivery_method == 'company' and not b.vehicle:
                    raise UserError(self.env._('Delivery Vehicle is required on batch %s before validation.', b.display_name))
                for f in need:
                    if not b[f]:
                        raise UserError(self.env._('%(f)s is required on batch %(b)s before validation.', f=b._fields[f].string,
                                                   b=b.display_name))
                _check_order(b, ('loaded_time', 'gate_time') if pickup else TIMES, UserError)
                continue
            _check_order(b, TIMES, ValidationError)
            if b.delivery_method != 'pickup' and not (b.vehicle or b.driver):
                raise ValidationError(self.env._('%s: enter the vehicle or driver.', b.display_name))

    def action_refresh_invoiced_time(self):
        """Refresh Invoiced Time: from the batch's invoice, else the delivery order's first invoice."""
        for b in self.filtered(lambda x: x.state == 'draft'):
            v = b.invoice_id or b.delivery_id.voucher_ids.filtered(lambda x: x.state == 'posted').sorted('create_date')[:1]
            if v:
                b.invoiced_time = v.create_date
        return True

    def action_validate(self):
        """Validate Batch: goods leave the yard, awaiting dispatch -> customer (already costed on the invoice)."""
        if not (self.env.su or self.env.user.has_group('sonador_inventory.group_sb_store_user')):
            raise UserError(self.env._('Only the store validates a batch.'))
        Stock = self.env['sb.stock']
        for b in self.filtered(lambda x: x.state == 'draft'):
            d = b.delivery_id
            if d.state == 'cancel':
                raise UserError(self.env._('Cannot validate a batch on a cancelled delivery order.'))
            if not b.line_ids.filtered(lambda l: l.quantity > 0):
                raise UserError(self.env._('Batch %s has no lines to validate.', b.display_name))
            b._check_times()
            remaining = {l.product_id: l.qty_remaining for l in d.line_ids}
            loaded = defaultdict(float)
            for l in b.line_ids:
                loaded[l.product_id] += l.quantity
            over = ['%s: %s loaded, %s still to deliver' % (p.display_name, q, remaining.get(p, 0.0))
                    for p, q in loaded.items() if float_compare(q, remaining.get(p, 0.0), precision_rounding=p.uom_id.rounding) > 0]
            if over:
                raise UserError(self.env._('More than was invoiced:\n%s', '\n'.join(over)))
            disp = d.warehouse_id._sb_dispatch_location()
            customer = self.env.ref('stock.stock_location_customers')
            for p, q in loaded.items():
                Stock._move(p, q, disp, customer, b.date, '%s / %s' % (d.name, b.name), d.company_id, link={'sb_delivery_batch_id': b.id})
            so_lines = d.sale_order_id.order_line
            for l in b.line_ids:
                l.sale_line_id = so_lines.filtered(lambda s: s.product_id == l.product_id)[:1]
            b.state = 'done'
            d._refresh_lines()
            d._sb_sync_state()
            d.message_post(body=self.env._('Batch %(b)s validated — stock deducted for %(q)s.', b=b.name,
                                           q=', '.join('%s %s' % (q, p.display_name) for p, q in loaded.items())))
        return True

    def action_cancel(self):
        if not (self.env.su or self.env.user.has_group('sonador_inventory.group_sb_store_user')):
            raise UserError(self.env._('Only the store cancels a batch.'))
        if any(b.state == 'done' for b in self):
            raise UserError(self.env._('Cannot cancel a validated batch: record a sales return.'))
        self.write({'state': 'cancel'})
        return True

    def action_draft(self):
        if self.filtered('move_ids'):
            raise UserError(self.env._('Cannot reset a batch that already has stock moves.'))
        self.write({'state': 'draft'})
        return True

    def _sb_before_print(self):
        self._sb_check_print()
        self._sb_count_print()

    def _sb_check_print(self):
        """Strict: a batch prints once validated (administrators any time)."""
        admin = self.env.user.has_group('base.group_system')
        for b in self:
            if b.state == 'cancel':
                raise UserError(self.env._('Cannot print a cancelled batch.'))
            if not b.line_ids:
                raise UserError(self.env._('Batch %s has no lines to print.', b.display_name))
            if b.company_id.sb_delivery_strict and not admin and b.state != 'done':
                raise UserError(self.env._('Batch %s must be validated before printing.', b.display_name))

    def _sb_count_print(self):
        for b in self:
            b.sudo().print_count = (b.print_count or 0) + 1

    def action_print(self):
        self._sb_before_print()
        return self.env.ref('sonador_sales.action_report_delivery_note').report_action(self)


class SbDeliveryBatchLine(models.Model):
    _name = 'sb.delivery.batch.line'
    _description = 'Delivery Order Batch Line'

    batch_id = fields.Many2one('sb.delivery.batch', 'Batch', required=True, ondelete='cascade', index=True)
    product_id = fields.Many2one('product.product', 'Product', required=True)
    uom_id = fields.Many2one(related='product_id.uom_id', string='Unit of Measure')
    quantity = fields.Float('Quantity', digits='Product Unit', required=True)
    sale_line_id = fields.Many2one('sale.order.line', copy=False, index='btree_not_null')


class StockMove(models.Model):
    _inherit = 'stock.move'

    sb_delivery_batch_id = fields.Many2one('sb.delivery.batch', 'Delivery Batch', copy=False, index='btree_not_null')
