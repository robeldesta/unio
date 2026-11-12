"""Vehicles and everything logged against them: documents that expire (Bolo, insurance, permits), odometer readings,
fuel requests and fills, trips (gate pass, per diem), maintenance schedules and work orders. Accounting lines and store
issues can carry the vehicle, so costs from the books land on it too."""
import re

from odoo import api, fields, models

VEHICLE_KINDS = [('car', 'Car'), ('pickup', 'Pickup'), ('van', 'Van / minibus'), ('bus', 'Bus'), ('truck', 'Truck'),
                 ('machine', 'Machine / equipment'), ('motorbike', 'Motorbike'), ('other', 'Other')]
OWNERSHIP = [('owned', 'Owned'), ('leased', 'Leased'), ('hired', 'Hired / outsourced')]
VEHICLE_STATES = [('active', 'In service'), ('workshop', 'In the workshop'), ('idle', 'Parked / idle'), ('disposed', 'Sold / disposed')]
FUELS = [('diesel', 'Diesel'), ('benzine', 'Benzine'), ('electric', 'Electric'), ('hybrid', 'Hybrid')]
DOC_KINDS = [('bolo', 'Bolo (annual inspection)'), ('third_party', 'Third-party insurance'), ('comprehensive', 'Comprehensive insurance'),
             ('permit', 'Transport / route permit'), ('customs', 'Customs / duty-free'), ('other', 'Other')]
PAY_METHODS = [('telebirr', 'telebirr'), ('cash', 'Cash'), ('coupon', 'Fuel coupon / card'), ('credit', 'Station credit'), ('bank', 'Bank transfer')]
RISKS = [('ok', 'OK'), ('watch', 'Watch'), ('alert', 'Alert'), ('theft', 'Possible theft')]
SERVICE_KINDS = [('service', 'Service / oil change'), ('repair', 'Repair'), ('tyres', 'Tyres'), ('parts', 'Spare parts'),
                 ('body', 'Body work'), ('accident', 'Accident'), ('other', 'Other')]


def plate_key(plate):
    """Plates are written many ways (3-AA-B34542, AA 3 B 34542, 03-AA-B34542): compare on letters and digits only."""
    s = re.sub(r'[^0-9A-Z]', '', (plate or '').upper())
    return re.sub(r'(?<![0-9])0+(?=[0-9])', '', s)


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_fleet_perdiem_rate = fields.Monetary('Driver per diem per day', default=1100.0, currency_field='currency_id')
    sb_fleet_perdiem_account_id = fields.Many2one('account.account', 'Per diem expense account')
    sb_fleet_fuel_account_id = fields.Many2one('account.account', 'Fuel expense account')
    sb_fleet_diesel_price = fields.Float('Diesel price per litre', default=180.46)
    sb_fleet_benzine_price = fields.Float('Benzine price per litre', default=170.0)
    sb_fleet_kml_tolerance = fields.Float('Fuel economy tolerance %', default=20.0,
                                          help='How far from the expected km per litre a fill may be before it is questioned.')
    sb_fleet_doc_warn_days = fields.Integer('Warn before documents expire (days)', default=30)


class SbVehicle(models.Model):
    _name = 'sb.vehicle'
    _description = 'Vehicle'
    _inherit = ['mail.thread']
    _order = 'active desc, state, plate'

    name = fields.Char(compute='_compute_name', store=True)
    plate = fields.Char('Plate', required=True, tracking=True, help='As on the plate, e.g. 3-AA-B34542 or ET-03-A14668')
    plate_key = fields.Char(compute='_compute_name', store=True, index=True)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True, index=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    make = fields.Char(help='Toyota, Isuzu, Sino Howo …')
    model = fields.Char(help='Hilux, NPR, Dzire …')
    year = fields.Integer('Model year')
    kind = fields.Selection(VEHICLE_KINDS, default='pickup', required=True)
    ownership = fields.Selection(OWNERSHIP, default='owned', required=True)
    state = fields.Selection(VEHICLE_STATES, default='active', required=True, tracking=True)
    fuel = fields.Selection(FUELS, default='diesel', required=True)
    tank = fields.Float('Tank (litres)')
    target_kml = fields.Float('Expected km per litre', help='What this vehicle should do; fills far off it are questioned.')
    odometer = fields.Float('Odometer (km)', tracking=True)
    odometer_date = fields.Date()
    driver_id = fields.Many2one('hr.employee', 'Driver', tracking=True)
    user_ids = fields.Many2many('hr.employee', string='Other drivers')
    chassis = fields.Char('Chassis no.')
    engine = fields.Char('Engine no.')
    colour = fields.Char()
    seats = fields.Integer()
    capacity = fields.Char('Load capacity', help='e.g. 5 t, 30 quintals')
    acquired = fields.Date('Acquired on')
    value = fields.Monetary('Purchase value', currency_field='currency_id')
    charge_rate_km = fields.Float('Charge-out rate per km', help='What a job is charged per km on trips.')
    gps_imei = fields.Char('GPS tracker IMEI')
    fuel_sensor = fields.Boolean('GPS fuel sensor')
    note = fields.Text()
    active = fields.Boolean(default=True)
    doc_ids = fields.One2many('sb.vehicle.doc', 'vehicle_id')
    fuel_ids = fields.One2many('sb.fuel', 'vehicle_id')
    trip_ids = fields.One2many('sb.trip', 'vehicle_id')
    service_ids = fields.One2many('sb.vehicle.service', 'vehicle_id')
    schedule_ids = fields.One2many('sb.vehicle.schedule', 'vehicle_id')
    reading_ids = fields.One2many('sb.vehicle.reading', 'vehicle_id')

    @api.depends('plate', 'make', 'model')
    def _compute_name(self):
        for v in self:
            v.plate_key = plate_key(v.plate)
            v.name = ' '.join(x for x in ((v.make or ''), (v.model or ''), '·' if (v.make or v.model) else '', v.plate or '') if x).strip()

    def _set_odometer(self, value, date, source, res=False):
        """Record a reading; the vehicle keeps the highest one."""
        for v in self:
            if not value:
                continue
            self.env['sb.vehicle.reading'].sudo().create({'vehicle_id': v.id, 'value': value, 'date': date, 'source': source,
                                                          'res_ref': res or False})
            if value > (v.odometer or 0):
                v.sudo().write({'odometer': value, 'odometer_date': date})


class SbVehicleReading(models.Model):
    _name = 'sb.vehicle.reading'
    _description = 'Odometer reading'
    _order = 'date desc, value desc, id desc'

    vehicle_id = fields.Many2one('sb.vehicle', required=True, ondelete='cascade', index=True)
    date = fields.Date(required=True, default=fields.Date.context_today)
    value = fields.Float('km', required=True)
    source = fields.Selection([('manual', 'Entered'), ('fuel', 'Fuel fill'), ('trip', 'Trip'), ('service', 'Workshop'), ('import', 'Brought over')],
                              default='manual')
    res_ref = fields.Char()


class SbVehicleDoc(models.Model):
    _name = 'sb.vehicle.doc'
    _description = 'Vehicle document'
    _order = 'date_end desc, id desc'

    vehicle_id = fields.Many2one('sb.vehicle', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='vehicle_id.company_id', store=True)
    kind = fields.Selection(DOC_KINDS, required=True, default='bolo')
    number = fields.Char('Policy / certificate no.')
    partner_id = fields.Many2one('res.partner', 'Insurer / issuer')
    date_start = fields.Date('From')
    date_end = fields.Date('Expires', index=True)
    amount = fields.Float('Cost')
    note = fields.Char()
    renewed = fields.Boolean(help='A newer document of the same kind replaced it.')
    voucher_id = fields.Many2one('sb.voucher', 'Paid by', ondelete='set null')


class SbFuel(models.Model):
    """One fuel event: a driver's request, the approval, and the fill with its receipt, checked for cheating."""
    _name = 'sb.fuel'
    _description = 'Fuel request / fill'
    _order = 'date desc, id desc'

    name = fields.Char(default='/', copy=False, index=True)
    vehicle_id = fields.Many2one('sb.vehicle', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one(related='vehicle_id.company_id', store=True)
    driver_id = fields.Many2one('hr.employee', 'Driver')
    state = fields.Selection([('request', 'Requested'), ('approved', 'Approved'), ('filled', 'Filled'), ('rejected', 'Rejected')],
                             default='request', required=True, index=True)
    requested_at = fields.Datetime(default=fields.Datetime.now)
    liters_req = fields.Float('Litres asked')
    level_now = fields.Float('Tank level before (litres)')
    reason = fields.Char()
    approved_by = fields.Many2one('hr.employee')
    reject_note = fields.Char()
    date = fields.Datetime('Filled at', index=True)
    liters = fields.Float('Litres')
    price = fields.Float('Price per litre')
    amount = fields.Float()
    odometer = fields.Float('Odometer at fill')
    full = fields.Boolean('Filled to full', default=True)
    station = fields.Char()
    pay = fields.Selection(PAY_METHODS, default='telebirr')
    receipt = fields.Char('Receipt / transaction no.', index=True)
    receipt_checked = fields.Boolean('Receipt checked online')
    receipt_amount = fields.Float()
    receipt_date = fields.Char()
    receipt_payee = fields.Char()
    receipt_plate = fields.Char()
    gps_liters = fields.Float('GPS saw (litres)', help='Litres the GPS fuel sensor saw going in.')
    km = fields.Float('km since last full fill')
    kml = fields.Float('km per litre', digits=(10, 2))
    flags = fields.Json(default=list)
    risk = fields.Selection(RISKS, default='ok', index=True)
    voucher_id = fields.Many2one('sb.voucher', 'Paid by', ondelete='set null')
    sb_import_ref = fields.Char(index=True)

    @api.model_create_multi
    def create(self, vals_list):
        for v in vals_list:
            if v.get('name', '/') == '/':
                v['name'] = self.env['ir.sequence'].next_by_code('sb.fuel') or '/'
        return super().create(vals_list)


class SbTrip(models.Model):
    _name = 'sb.trip'
    _description = 'Trip'
    _order = 'out_at desc, id desc'

    name = fields.Char(default='/', copy=False, index=True)
    vehicle_id = fields.Many2one('sb.vehicle', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one(related='vehicle_id.company_id', store=True)
    driver_id = fields.Many2one('hr.employee', 'Driver')
    purpose = fields.Char(required=True)
    origin = fields.Char('From')
    destination = fields.Char('To')
    cargo = fields.Char('Load / passengers', help='Delivery note, goods, people on board')
    state = fields.Selection([('planned', 'Planned'), ('out', 'On the road'), ('back', 'Back'), ('cancel', 'Cancelled')],
                             default='planned', required=True, index=True)
    out_at = fields.Datetime('Left at')
    back_at = fields.Datetime('Back at')
    odo_out = fields.Float('Odometer out')
    odo_in = fields.Float('Odometer in')
    km = fields.Float(compute='_compute_km', store=True)
    gate_by = fields.Char('Gate checked by')
    days = fields.Float('Per diem days')
    perdiem_rate = fields.Float('Per diem per day')
    perdiem = fields.Float('Per diem')
    perdiem_state = fields.Selection([('none', 'None'), ('requested', 'Requested'), ('approved', 'Approved'), ('paid', 'Paid')],
                                     default='none', required=True, index=True)
    perdiem_voucher_id = fields.Many2one('sb.voucher', 'Per diem payment', ondelete='set null')
    note = fields.Char()
    sb_import_ref = fields.Char(index=True)

    @api.depends('odo_out', 'odo_in')
    def _compute_km(self):
        for t in self:
            t.km = max((t.odo_in or 0) - (t.odo_out or 0), 0) if t.odo_in and t.odo_out else 0

    @api.model_create_multi
    def create(self, vals_list):
        for v in vals_list:
            if v.get('name', '/') == '/':
                v['name'] = self.env['ir.sequence'].next_by_code('sb.trip') or '/'
        return super().create(vals_list)


class SbVehicleSchedule(models.Model):
    """Recurring maintenance: every N km and / or every N months."""
    _name = 'sb.vehicle.schedule'
    _description = 'Maintenance schedule'
    _order = 'vehicle_id, id'

    vehicle_id = fields.Many2one('sb.vehicle', required=True, ondelete='cascade', index=True)
    name = fields.Char(required=True, help='Oil and filter change, tyre rotation, major service …')
    every_km = fields.Float('Every km')
    every_months = fields.Integer('Every months')
    last_km = fields.Float('Last done at km')
    last_date = fields.Date('Last done on')
    active = fields.Boolean(default=True)


class SbVehicleService(models.Model):
    _name = 'sb.vehicle.service'
    _description = 'Work order'
    _order = 'date desc, id desc'

    name = fields.Char(default='/', copy=False, index=True)
    vehicle_id = fields.Many2one('sb.vehicle', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one(related='vehicle_id.company_id', store=True)
    kind = fields.Selection(SERVICE_KINDS, default='service', required=True)
    schedule_id = fields.Many2one('sb.vehicle.schedule', 'For schedule', ondelete='set null')
    state = fields.Selection([('planned', 'Planned'), ('open', 'In the workshop'), ('done', 'Done')], default='open', required=True, index=True)
    date = fields.Date(default=fields.Date.context_today, index=True)
    date_done = fields.Date('Done on')
    odometer = fields.Float()
    partner_id = fields.Many2one('res.partner', 'Garage / supplier')
    description = fields.Text()
    amount = fields.Float('Cost')
    voucher_id = fields.Many2one('sb.voucher', 'Paid by', ondelete='set null')
    sb_import_ref = fields.Char(index=True)

    @api.model_create_multi
    def create(self, vals_list):
        for v in vals_list:
            if v.get('name', '/') == '/':
                v['name'] = self.env['ir.sequence'].next_by_code('sb.vehicle.service') or '/'
        return super().create(vals_list)


class SbVoucherLine(models.Model):
    _inherit = 'sb.voucher.line'

    vehicle_id = fields.Many2one('sb.vehicle', 'Vehicle', index=True)


class SbStockVoucherLine(models.Model):
    _inherit = 'sb.stock.voucher.line'

    vehicle_id = fields.Many2one('sb.vehicle', 'Vehicle', index=True)


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    sb_licence_no = fields.Char('Driving licence no.')
    sb_licence_grade = fields.Char('Licence grade', help='e.g. Automobile, Public transport 1, Dry cargo 2, Liquid cargo 3')
    sb_licence_expiry = fields.Date('Licence expires')
