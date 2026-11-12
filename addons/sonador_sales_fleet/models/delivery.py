"""Delivery Vehicle from the fleet on delivery orders and batches. The plate and driver are written into the batch's
text fields too, so gate passes, delivery notes and reports read the same as without a fleet."""
from odoo import api, fields, models


def _vehicle_text(env, vals):
    if vals.get('vehicle_id'):
        v = env['sb.vehicle'].browse(vals['vehicle_id'])
        vals.setdefault('vehicle', v.plate)
        if v.driver_id and 'driver' in env['sb.delivery.batch']._fields:
            vals.setdefault('driver', v.driver_id.name)
    return vals


class SbDeliveryOrder(models.Model):
    _inherit = 'sb.delivery.order'

    vehicle_id = fields.Many2one('sb.vehicle', 'Delivery Vehicle', tracking=True, check_company=True,
                                 help='The usual vehicle: new batches of this delivery order start with it (company delivery).')
    driver_name = fields.Char('Driver', related='vehicle_id.driver_id.name')


class SbDeliveryBatch(models.Model):
    _inherit = 'sb.delivery.batch'

    vehicle_id = fields.Many2one('sb.vehicle', 'Delivery Vehicle', tracking=True, check_company=True)

    @api.onchange('vehicle_id')
    def _onchange_vehicle_id(self):
        if self.vehicle_id:
            self.vehicle = self.vehicle_id.plate
            if self.vehicle_id.driver_id:
                self.driver = self.vehicle_id.driver_id.name

    @api.onchange('delivery_method')
    def _onchange_delivery_method(self):
        if self.delivery_method in ('pickup', 'rented'):
            self.vehicle_id = False

    @api.model_create_multi
    def create(self, vals_list):
        D = self.env['sb.delivery.order']
        for vals in vals_list:
            method = vals.get('delivery_method') or 'company'
            if method == 'company' and not vals.get('vehicle_id') and not vals.get('vehicle') and vals.get('delivery_id'):
                vals['vehicle_id'] = D.browse(vals['delivery_id']).vehicle_id.id or False
            if method in ('pickup', 'rented'):
                vals['vehicle_id'] = False
            _vehicle_text(self.env, vals)
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('vehicle_id'):
            v = self.env['sb.vehicle'].browse(vals['vehicle_id'])
            vals = dict(vals)
            vals.setdefault('vehicle', v.plate)
            if v.driver_id:
                vals.setdefault('driver', v.driver_id.name)
        return super().write(vals)
