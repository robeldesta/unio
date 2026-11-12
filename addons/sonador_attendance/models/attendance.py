"""Attendance records: work sites (geofences), ZKTeco devices, raw punches (device, browser, phone, kiosk, manual)
and the worked day built from them (hours, lateness, overtime by Labour Proclamation 1156/2019 art. 68 category)."""
import math
import secrets

from odoo import api, fields, models
from odoo.exceptions import ValidationError

SOURCES = [('device', 'Fingerprint / face device'), ('web', 'Browser'), ('mobile', 'Phone'), ('kiosk', 'Kiosk'),
           ('manual', 'Added by HR / manager'), ('import', 'Odoo 18')]


def distance_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_att_grace = fields.Integer('Grace minutes', default=10,
                                  help='Arriving up to this many minutes after the start (or leaving this much before the end) is not counted.')
    sb_att_ot_min = fields.Integer('Smallest overtime (minutes)', default=30,
                                   help='Extra time before the start or after the end shorter than this is not overtime.')
    sb_att_geofence = fields.Selection([('off', 'Not checked'), ('warn', 'Allowed, flagged when outside a site'),
                                        ('block', 'Only inside a work site')], default='warn', string='Clock-in location')
    sb_att_live = fields.Date('Count absences from', help='Empty: no absences are counted yet (set it when the devices and '
                                                          'clocking in are running). Lateness and hours count from the punches.')
    sb_att_need_gps = fields.Boolean('Location required to clock in from a browser or phone', default=True)
    sb_att_late_after = fields.Float('Late after', help='Clock time after which an arrival is late, e.g. 8:15. Late minutes '
                                                        'count from this time. Empty: the start of work plus the grace minutes.')
    sb_att_ot_approver_id = fields.Many2one('res.users', 'Overtime approved by', domain=[('share', '=', False)],
                                            help='Empty: HR and each person\'s manager approve overtime. Set: only this '
                                                 'person approves (e.g. the general manager); HR and managers still see it.')


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    sb_bio_ids = fields.One2many('sb.att.bio', 'employee_id', 'Device user numbers', groups='hr.group_hr_user')
    sb_att_tracked = fields.Boolean('Attendance tracked', default=True, groups='hr.group_hr_user',
                                    help='Off = exempt (e.g. CEO, managers, drivers): no lateness or absences; paid the full month unless days are deducted.')
    sb_att_since = fields.Date('Attendance counted from', groups='hr.group_hr_user',
                               help='No absences are counted before this day (e.g. before the person was enrolled on the device).')
    sb_att_late_after = fields.Float('Late after (own)', groups='hr.group_hr_user',
                                     help='This person only, e.g. 9:00 for a later shift. Empty: the company rule.')
    sb_att_anywhere = fields.Boolean('May clock in anywhere', groups='hr.group_hr_user',
                                     help='Field staff: clocking in outside the work sites is not flagged.')


class SbAttSite(models.Model):
    _name = 'sb.att.site'
    _description = 'Work site'
    _order = 'name'

    name = fields.Char(required=True)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True)
    latitude = fields.Float(digits=(10, 7), required=True)
    longitude = fields.Float(digits=(10, 7), required=True)
    radius = fields.Integer('Radius (m)', default=200, required=True)
    active = fields.Boolean(default=True)

    @api.constrains('radius')
    def _check_radius(self):
        for s in self:
            if not 30 <= s.radius <= 5000:
                raise ValidationError(self.env._('The radius is between 30 and 5,000 metres.'))

    @api.model
    def nearest(self, lat, lon, company=None):
        """(site, metres, inside) for a position."""
        best, dist = self.browse(), None
        if not (lat or lon):
            return best, None, False
        for s in self.sudo().search([('company_id', '=', (company or self.env.company).id)]):
            d = distance_m(lat, lon, s.latitude, s.longitude)
            if dist is None or d - s.radius < dist - best.radius:
                best, dist = s, d
        return best, dist, bool(best) and dist <= best.radius


class SbAttDevice(models.Model):
    _name = 'sb.att.device'
    _description = 'Attendance device'
    _order = 'name'

    name = fields.Char(required=True)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True)
    serial = fields.Char('Serial number', help='Shown on the device (Menu > System info). Devices that push here are '
                                               'recognised by it.')
    code = fields.Char('Agent code', help='For the sync agent (older devices that cannot push).')
    token = fields.Char(default=lambda s: secrets.token_urlsafe(24), copy=False, required=True)
    site_id = fields.Many2one('sb.att.site', 'Site')
    ot_manual = fields.Boolean('Overtime: manual only', help='Days recorded on this device give no overtime and nothing goes '
                                                             'for approval: overtime comes only from the month\'s sheet.')
    last_seen = fields.Datetime(readonly=True)
    last_punch = fields.Datetime(readonly=True)
    punches = fields.Integer(compute='_compute_punches')
    active = fields.Boolean(default=True)

    _serial_unique = models.Constraint('unique(serial)', 'Another device has this serial number.')

    def _compute_punches(self):
        counts = dict(self.env['sb.att.punch'].sudo()._read_group([('device_id', 'in', self.ids)], ['device_id'], ['__count']))
        for d in self:
            d.punches = counts.get(d, 0)


class SbAttBio(models.Model):
    """A user number on one device and who it is (numbers are per device: 2 can be one person at the head office and
    another at the factory)."""
    _name = 'sb.att.bio'
    _description = 'Device user'
    _order = 'device_id, bio_id'

    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company)
    device_id = fields.Many2one('sb.att.device', required=True, ondelete='cascade', index=True)
    bio_id = fields.Char('User number', required=True, index=True)
    name = fields.Char('Name on the device')
    employee_id = fields.Many2one('hr.employee', index=True, ondelete='set null')

    _unique = models.Constraint('unique(device_id, bio_id)', 'This user number is already listed for the device.')


class SbAttPunch(models.Model):
    _name = 'sb.att.punch'
    _description = 'Clock punch'
    _order = 'time desc, id desc'

    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company, index=True)
    employee_id = fields.Many2one('hr.employee', index=True, ondelete='cascade')
    bio_id = fields.Char('Device user ID', index=True)
    time = fields.Datetime(required=True, index=True)
    kind = fields.Selection([('in', 'In'), ('out', 'Out'), ('auto', 'Device')], default='auto', required=True,
                            help='Device punches are read as first in, last out of the day.')
    source = fields.Selection(SOURCES, required=True, default='web')
    device_id = fields.Many2one('sb.att.device', ondelete='set null')
    latitude = fields.Float(digits=(10, 7))
    longitude = fields.Float(digits=(10, 7))
    accuracy = fields.Integer('GPS accuracy (m)')
    site_id = fields.Many2one('sb.att.site', ondelete='set null')
    distance = fields.Integer('Metres from the site')
    outside = fields.Boolean('Outside the work sites')
    note = fields.Char()
    by_id = fields.Many2one('res.users', 'Entered by', default=lambda s: s.env.user)
    ip = fields.Char()

    _device_unique = models.Constraint('unique(device_id, bio_id, time)', 'Punch already received.')


class SbAttDay(models.Model):
    """One person's day: built from the punches, the working schedule, public holidays and approved leave."""
    _name = 'sb.att.day'
    _description = 'Attendance day'
    _order = 'date desc, employee_id'

    employee_id = fields.Many2one('hr.employee', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one('res.company', required=True, index=True)
    department_id = fields.Many2one('hr.department', related='employee_id.department_id', store=True)
    date = fields.Date(required=True, index=True)
    first_in = fields.Datetime()
    last_out = fields.Datetime()
    open = fields.Boolean('Still in / no check-out')
    worked = fields.Integer('Worked (min)')
    expected = fields.Integer('Scheduled (min)')
    late = fields.Integer('Late (min)')
    early = fields.Integer('Left early (min)')
    ot_day = fields.Integer('Overtime 6am-10pm (min)')
    ot_night = fields.Integer('Overtime 10pm-6am (min)')
    ot_rest = fields.Integer('Overtime on the rest day (min)')
    ot_holiday = fields.Integer('Overtime on a public holiday (min)')
    ot_total = fields.Integer(compute='_compute_ot_total', store=True)
    ot_state = fields.Selection([('none', 'None'), ('pending', 'To approve'), ('approved', 'Approved'), ('refused', 'Refused')],
                                default='none', required=True, index=True)
    ot_by_id = fields.Many2one('hr.employee', 'Overtime decided by')
    ot_note = fields.Char('Overtime reason')
    status = fields.Selection([('present', 'Present'), ('late', 'Late'), ('absent', 'Absent'), ('leave', 'On leave'),
                               ('holiday', 'Public holiday'), ('rest', 'Rest day'), ('missing', 'No check-out')],
                              required=True, default='absent', index=True)
    absence = fields.Selection([('unexcused', 'Unexcused'), ('excused', 'Excused (paid)'), ('unpaid', 'Unpaid')],
                               help='For absent days: unpaid days come off the salary (art. 54: wages are for work done).')
    outside = fields.Boolean('Clocked outside the sites')
    note = fields.Char()

    _unique = models.Constraint('unique(employee_id, date)', 'One day per person.')

    @api.depends('ot_day', 'ot_night', 'ot_rest', 'ot_holiday')
    def _compute_ot_total(self):
        for d in self:
            d.ot_total = d.ot_day + d.ot_night + d.ot_rest + d.ot_holiday

    @api.model
    def _cron_settle(self):
        from datetime import timedelta
        for company in self.env['res.company'].search([]):
            day = fields.Date.context_today(self) - timedelta(days=1)
            self.env['sb.att.engine'].with_company(company).settle(day, day, company)


class SbAttWarning(models.Model):
    """Written warning (art. 27(1)(a)-(b): lateness 8 times or absence 5 days in six months count only after one)."""
    _name = 'sb.att.warning'
    _description = 'Attendance warning'
    _order = 'date desc'

    employee_id = fields.Many2one('hr.employee', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one(related='employee_id.company_id', store=True)
    date = fields.Date(required=True, default=fields.Date.context_today)
    kind = fields.Selection([('late', 'Lateness'), ('absence', 'Absence')], required=True)
    note = fields.Text()
    by_id = fields.Many2one('res.users', default=lambda s: s.env.user)


class SbAttDeviceIngest(models.Model):
    _inherit = 'sb.att.device'

    def _touch(self):
        self.sudo().write({'last_seen': fields.Datetime.now()})

    def _ingest(self, rows, users=None, local=True):
        """Store punches [{'bio', 'time' ('YYYY-MM-DD HH:MM:SS', device local time), 'status'}] once each, link them to
        people by device user ID, and rebuild the days touched. Returns the number of new punches."""
        import pytz
        from datetime import datetime
        self.ensure_one()
        dev = self.sudo()
        Bio = self.env['sb.att.bio'].sudo()
        known = {b.bio_id: b for b in Bio.search([('device_id', '=', dev.id)])}
        for u in users or []:
            bio = str(u.get('bio') or '').strip()
            if not bio:
                continue
            b = known.get(bio)
            if b:
                if u.get('name') and b.name != u['name']:
                    b.name = u['name']
            else:
                known[bio] = Bio.create({'company_id': dev.company_id.id, 'device_id': dev.id, 'bio_id': bio, 'name': u.get('name') or False})
        if not rows:
            return 0
        tz = pytz.timezone(dev.company_id.resource_calendar_id.tz or 'Africa/Addis_Ababa')
        people = {b: x.employee_id for b, x in known.items() if x.employee_id}
        parsed = []
        for r in rows:
            bio = str(r.get('bio') or '').strip()
            try:
                t = datetime.strptime(str(r.get('time'))[:19], '%Y-%m-%d %H:%M:%S')
            except ValueError:
                continue
            if local:
                t = tz.localize(t).astimezone(pytz.UTC).replace(tzinfo=None)
            parsed.append((bio, t))
        if not parsed:
            return 0
        P = self.env['sb.att.punch'].sudo()
        have = set(P.search([('device_id', '=', dev.id), ('time', '>=', min(t for _b, t in parsed)),
                             ('time', '<=', max(t for _b, t in parsed))]).mapped(lambda p: (p.bio_id, p.time)))
        new = []
        for bio, t in parsed:
            if (bio, t) in have:
                continue
            have.add((bio, t))
            if bio not in known:
                known[bio] = Bio.create({'company_id': dev.company_id.id, 'device_id': dev.id, 'bio_id': bio})
            e = people.get(bio)
            new.append({'company_id': dev.company_id.id, 'employee_id': e.id if e else False, 'bio_id': bio, 'time': t,
                        'kind': 'auto', 'source': 'device', 'device_id': dev.id, 'site_id': dev.site_id.id or False,
                        'by_id': self.env.ref('base.user_root').id})
        punches = P.create(new) if new else P
        if punches:
            dev.write({'last_punch': max(punches.mapped('time'))})
            self.env['sb.att.engine'].rebuild_punch_days(punches)
        return len(punches)
