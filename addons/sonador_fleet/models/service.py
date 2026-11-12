"""Calls of the Fleet screens: dashboard, vehicles, documents, fuel (requests, approvals, fills and the cheat checker),
trips (gate pass, per diem), maintenance (schedules and work orders), costs per vehicle and per km, settings, and the
driver's own page.

Money: fuel fills, work orders, documents and per diem are what the fleet records say. Costs posted in Accounting with
the vehicle on the line are added, except vouchers already linked to one of those records (no double counting)."""
import math
from collections import defaultdict
from datetime import datetime, time, timedelta

import pytz
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.sonador_books.models.ethiopian_calendar import format_ethiopian

from .fleet import DOC_KINDS, FUELS, OWNERSHIP, PAY_METHODS, SERVICE_KINDS, VEHICLE_KINDS, VEHICLE_STATES, plate_key
from .receipt import plate_digits

RISK_ORDER = {'ok': 0, 'watch': 1, 'alert': 2, 'theft': 3}
EXPENSE_TYPES = ('expense', 'expense_direct_cost', 'expense_depreciation', 'expense_other')


def _s(d):
    return fields.Date.to_string(d) if d else False


def _ec(d):
    return format_ethiopian(d) if d else ''


def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


class SbFleet(models.AbstractModel):
    _name = 'sb.fleet'
    _description = 'Fleet'

    # ------------------------------------------------------------------ access and helpers
    def _is_manager(self):
        return self.env.su or self.env.user.has_group('sonador_fleet.group_manager')

    def _is_officer(self):
        return self.env.su or self.env.user.has_group('sonador_fleet.group_user')

    def _is_viewer(self):
        """Looks at all of Fleet, costs included, and changes nothing (the general manager, an owner)."""
        return not self.env.su and self.env.user.has_group('sonador_fleet.group_viewer')

    def _require(self, level='driver', read=False):
        """read=True only on methods that change nothing: those are open to a viewer, every other method is not."""
        if read and self._is_viewer():
            return
        if level != 'driver' and self._is_viewer() and not self._is_officer():
            raise AccessError(self.env._('You can look at Fleet, not change it.'))
        if level == 'manager' and not self._is_manager():
            raise AccessError(self.env._('Only a fleet manager can do this.'))
        if level == 'officer' and not self._is_officer():
            raise AccessError(self.env._('Only the fleet office can do this.'))
        if not (self.env.su or self.env.user.has_group('sonador_fleet.group_driver')):
            if self._is_viewer():
                raise AccessError(self.env._('You can look at Fleet, not change it.'))
            raise AccessError(self.env._('Fleet is for drivers and the fleet office.'))

    def _me(self):
        return self.env['sb.people']._me()

    def _V(self):
        return self.env['sb.vehicle'].sudo().with_context(active_test=False)

    def _vehicles(self, all_=False):
        dom = [('company_id', '=', self.env.company.id)]
        if not all_:
            dom.append(('active', '=', True))
        if not (self._is_officer() or self._is_viewer()):
            me = self._me()
            dom += ['|', ('driver_id', '=', me.id or 0), ('user_ids', 'in', me.ids or [0])]
        return self._V().search(dom)

    def _vehicle(self, vid):
        v = self._V().browse(int(vid))
        if not v.exists() or v not in self._vehicles(all_=True):
            raise AccessError(self.env._('Not one of your vehicles.'))
        return v

    def _tz(self):
        return pytz.timezone(self.env.user.tz or 'Africa/Addis_Ababa')

    def _dt_in(self, s):
        """'2026-10-19T08:30' (local) -> naive UTC datetime"""
        if not s:
            return False
        d = datetime.fromisoformat(str(s).replace(' ', 'T')[:16])
        return self._tz().localize(d).astimezone(pytz.utc).replace(tzinfo=None)

    def _dt_out(self, d):
        if not d:
            return ''
        return pytz.utc.localize(d).astimezone(self._tz()).strftime('%Y-%m-%dT%H:%M')

    def _dt_label(self, d):
        if not d:
            return ''
        loc = pytz.utc.localize(d).astimezone(self._tz())
        return '%s %s' % (_ec(loc.date()), loc.strftime('%H:%M'))

    def _today(self):
        return fields.Date.context_today(self)

    def _price(self, company, fuel):
        return company.sb_fleet_diesel_price if fuel == 'diesel' else company.sb_fleet_benzine_price if fuel in ('benzine', 'hybrid') else 0.0

    # ------------------------------------------------------------------ context
    @api.model
    def ctx(self):
        self._require(read=True)
        c = self.env.company
        me = self._me()
        sel = lambda lst: [{'id': k, 'name': v} for k, v in lst]  # noqa: E731
        viewer = self._is_viewer()
        return {'is_manager': self._is_manager(), 'is_officer': self._is_officer() or viewer, 'sees_costs': self._is_manager() or viewer,
                'view_only': viewer and not self._is_officer(), 'me': me.id or False, 'me_name': me.name or '',
                'company': c.name, 'currency': c.currency_id.name, 'today': _s(self._today()), 'today_ec': _ec(self._today()),
                'now': self._dt_out(fields.Datetime.now()),
                'kinds': sel(VEHICLE_KINDS), 'ownership': sel(OWNERSHIP), 'states': sel(VEHICLE_STATES), 'fuels': sel(FUELS),
                'doc_kinds': sel(DOC_KINDS), 'pay_methods': sel(PAY_METHODS), 'service_kinds': sel(SERVICE_KINDS),
                'perdiem_rate': c.sb_fleet_perdiem_rate, 'prices': {'diesel': c.sb_fleet_diesel_price, 'benzine': c.sb_fleet_benzine_price,
                                                                     'hybrid': c.sb_fleet_benzine_price},
                'people': [{'id': e.id, 'name': e.name} for e in self.env['hr.employee'].sudo().search(
                    [('company_id', '=', c.id)], order='name')] if self._is_officer() or viewer else ([{'id': me.id, 'name': me.name}] if me else []),
                'jobs': [{'id': j.id, 'name': j.display_name} for j in self.env['sb.job'].sudo().search(
                    [('company_id', '=', c.id), ('state', 'in', ('active', 'hold'))])] if 'job_id' in self.env['sb.trip']._fields else [],
                'vehicles': [{'id': v.id, 'name': v.name, 'plate': v.plate, 'fuel': v.fuel, 'tank': v.tank, 'odometer': v.odometer,
                              'driver_id': v.driver_id.id or False} for v in self._vehicles()]}

    # ------------------------------------------------------------------ documents and schedules: what is due
    def _doc_status(self, d, warn):
        if not d.date_end:
            return 'none'
        left = (d.date_end - self._today()).days
        return 'expired' if left < 0 else 'soon' if left <= warn else 'ok'

    def _current_docs(self, vehicles):
        """The latest document of each kind per vehicle (older ones are history)."""
        out = {}
        for d in self.env['sb.vehicle.doc'].sudo().search([('vehicle_id', 'in', vehicles.ids)], order='date_end desc, id desc'):
            out.setdefault((d.vehicle_id.id, d.kind), d)
        return out

    def _sched_due(self, s):
        v = s.vehicle_id
        today = self._today()
        nk = s.last_km + s.every_km if s.every_km and s.last_km else False
        nd = s.last_date + relativedelta(months=s.every_months) if s.every_months and s.last_date else False
        status = 'ok'
        if (nk and v.odometer >= nk) or (nd and today >= nd):
            status = 'overdue'
        elif (nk and v.odometer >= nk - max(500, s.every_km * 0.1)) or (nd and (nd - today).days <= 14):
            status = 'soon'
        if not s.last_km and not s.last_date:
            status = 'unknown'
        return {'id': s.id, 'name': s.name, 'every_km': s.every_km, 'every_months': s.every_months, 'last_km': s.last_km,
                'last_date': _s(s.last_date), 'last_ec': _ec(s.last_date), 'next_km': nk or '', 'next_date': _s(nd), 'next_ec': _ec(nd),
                'km_left': round(nk - v.odometer) if nk else '', 'status': status, 'vehicle_id': v.id, 'vehicle': v.name}

    # ------------------------------------------------------------------ dashboard
    @api.model
    def dashboard(self):
        self._require(read=True)
        c = self.env.company
        vs = self._vehicles()
        today = self._today()
        warn = c.sb_fleet_doc_warn_days or 30
        docs = []
        for (vid, kind), d in self._current_docs(vs).items():
            st = self._doc_status(d, warn)
            if st in ('expired', 'soon'):
                docs.append({'id': d.id, 'vehicle_id': vid, 'vehicle': d.vehicle_id.name, 'kind': dict(DOC_KINDS)[kind], 'status': st,
                             'end': _s(d.date_end), 'end_ec': _ec(d.date_end), 'days': (d.date_end - today).days})
        drivers = (vs.mapped('driver_id') | vs.mapped('user_ids'))
        lic = [{'name': e.name, 'end_ec': _ec(e.sb_licence_expiry), 'days': (e.sb_licence_expiry - today).days,
                'status': 'expired' if e.sb_licence_expiry < today else 'soon'}
               for e in drivers if e.sb_licence_expiry and (e.sb_licence_expiry - today).days <= warn]
        sched = [self._sched_due(s) for s in self.env['sb.vehicle.schedule'].sudo().search([('vehicle_id', 'in', vs.ids)])]
        F = self.env['sb.fuel'].sudo()
        m0 = today.replace(day=1)
        month = F.search([('vehicle_id', 'in', vs.ids), ('state', '=', 'filled'), ('date', '>=', datetime.combine(m0, time.min))])
        flagged = F.search([('vehicle_id', 'in', vs.ids), ('state', '=', 'filled'), ('risk', 'in', ('alert', 'theft')),
                            ('date', '>=', datetime.combine(today - timedelta(days=60), time.min))], limit=30)
        T = self.env['sb.trip'].sudo()
        by_state = defaultdict(int)
        for v in vs:
            by_state[v.state] += 1
        econ = self._economy(vs, today - timedelta(days=90), today)
        cost = self._costs(vs, m0, today)
        return {'vehicles': len(vs), 'by_state': [{'id': k, 'name': dict(VEHICLE_STATES)[k], 'n': by_state[k]} for k, _n in VEHICLE_STATES],
                'docs': sorted(docs, key=lambda x: x['days']), 'licences': sorted(lic, key=lambda x: x['days']),
                'due': sorted([s for s in sched if s['status'] in ('overdue', 'soon')], key=lambda x: x['status'] != 'overdue'),
                'fuel_month': {'liters': round(sum(month.mapped('liters')), 1), 'amount': round(sum(month.mapped('amount')), 2), 'fills': len(month)},
                'flagged': [self._fuel_row(f) for f in flagged],
                'requests': F.search_count([('vehicle_id', 'in', vs.ids), ('state', '=', 'request')]),
                'on_road': [self._trip_row(t) for t in T.search([('vehicle_id', 'in', vs.ids), ('state', '=', 'out')])],
                'perdiem_waiting': T.search_count([('vehicle_id', 'in', vs.ids), ('perdiem_state', '=', 'requested')]),
                'workshop': self.env['sb.vehicle.service'].sudo().search_count([('vehicle_id', 'in', vs.ids), ('state', '=', 'open')]),
                'economy': econ, 'cost_month': cost['totals'], 'cost_rows': sorted(cost['rows'], key=lambda r: -r['total'])[:8]}

    def _economy(self, vehicles, d0, d1):
        """km per litre per vehicle over a period, from full-to-full fills."""
        out = []
        F = self.env['sb.fuel'].sudo()
        for v in vehicles.filtered(lambda x: x.fuel != 'electric'):
            fills = F.search([('vehicle_id', '=', v.id), ('state', '=', 'filled'), ('date', '>=', datetime.combine(d0, time.min)),
                              ('date', '<=', datetime.combine(d1, time.max))])
            good = fills.filtered(lambda f: f.kml > 0)
            km, lit = sum(good.mapped('km')), sum(good.mapped('liters'))
            kml = round(km / lit, 2) if lit else None
            out.append({'id': v.id, 'name': v.name, 'kml': kml, 'target': v.target_kml or None, 'liters': round(sum(fills.mapped('liters')), 1),
                        'status': 'none' if kml is None or not v.target_kml else
                        'bad' if kml < v.target_kml * (1 - (v.company_id.sb_fleet_kml_tolerance or 20) / 100.0) else 'ok'})
        return out

    # ------------------------------------------------------------------ vehicles
    def _vrow(self, v, docs=None, warn=30):
        st = {}
        if docs is not None:
            for k, _n in DOC_KINDS:
                d = docs.get((v.id, k))
                if d:
                    st[k] = {'status': self._doc_status(d, warn), 'end_ec': _ec(d.date_end)}
        return {'id': v.id, 'name': v.name, 'plate': v.plate, 'make': v.make or '', 'model': v.model or '', 'year': v.year or '',
                'kind': v.kind, 'ownership': v.ownership, 'state': v.state, 'fuel': v.fuel, 'tank': v.tank, 'target_kml': v.target_kml,
                'odometer': v.odometer, 'odometer_ec': _ec(v.odometer_date), 'driver_id': v.driver_id.id or '', 'driver': v.driver_id.name or '',
                'active': v.active, 'docs': st, 'gps': bool(v.gps_imei), 'sensor': v.fuel_sensor}

    @api.model
    def vehicles(self, f=None):
        self._require(read=True)
        f = f or {}
        vs = self._vehicles(all_=bool(f.get('archived')))
        if f.get('archived'):
            vs = vs.filtered(lambda v: not v.active)
        if f.get('state'):
            vs = vs.filtered(lambda v: v.state == f['state'])
        if f.get('search'):
            s = f['search'].lower()
            k = plate_key(f['search'])
            vs = vs.filtered(lambda v: s in (v.name or '').lower() or (k and k in (v.plate_key or '')) or s in (v.driver_id.name or '').lower())
        docs = self._current_docs(vs)
        warn = self.env.company.sb_fleet_doc_warn_days or 30
        return {'rows': [self._vrow(v, docs, warn) for v in vs]}

    @api.model
    def vehicle(self, vid):
        self._require(read=True)
        v = self._vehicle(vid)
        warn = v.company_id.sb_fleet_doc_warn_days or 30
        today = self._today()
        docs = self._current_docs(v)
        F = self.env['sb.fuel'].sudo()
        fills = F.search([('vehicle_id', '=', v.id)], limit=60)
        trips = self.env['sb.trip'].sudo().search([('vehicle_id', '=', v.id)], limit=60)
        services = self.env['sb.vehicle.service'].sudo().search([('vehicle_id', '=', v.id)], limit=60)
        y0 = today - timedelta(days=365)
        cost = self._costs(v, y0, today)
        econ = self._economy(v, today - timedelta(days=90), today)
        return dict(self._vrow(v, docs, warn), chassis=v.chassis or '', engine=v.engine or '', colour=v.colour or '', seats=v.seats,
                    capacity=v.capacity or '', acquired=_s(v.acquired), value=v.value, charge_rate_km=v.charge_rate_km, gps_imei=v.gps_imei or '',
                    fuel_sensor=v.fuel_sensor, note=v.note or '', user_ids=v.user_ids.ids, can_edit=self._is_officer(),
                    doc_list=[{'id': d.id, 'kind': d.kind, 'kind_name': dict(DOC_KINDS)[d.kind], 'number': d.number or '',
                               'partner_id': d.partner_id.id or '', 'partner': d.partner_id.name or '', 'start': _s(d.date_start),
                               'end': _s(d.date_end), 'start_ec': _ec(d.date_start), 'end_ec': _ec(d.date_end), 'amount': d.amount, 'note': d.note or '',
                               'status': self._doc_status(d, warn) if docs.get((v.id, d.kind)) == d else 'history'} for d in v.doc_ids],
                    fills=[self._fuel_row(f) for f in fills], trips=[self._trip_row(t) for t in trips],
                    services=[self._service_row(s) for s in services],
                    schedules=[self._sched_due(s) for s in v.schedule_ids],
                    readings=[{'date_ec': _ec(r.date), 'value': r.value, 'source': r.source} for r in v.reading_ids[:20]],
                    cost=cost['rows'][0] if cost['rows'] else {}, months=self._monthly(v, 12), economy=econ[0] if econ else {})

    @api.model
    def save_vehicle(self, vid, vals):
        self._require('officer')
        w = {}
        for k in ('plate', 'make', 'model', 'kind', 'ownership', 'state', 'fuel', 'chassis', 'engine', 'colour', 'capacity', 'gps_imei', 'note'):
            if k in vals:
                w[k] = (vals[k] or '').strip() if isinstance(vals[k], str) else vals[k]
        for k in ('tank', 'target_kml', 'value', 'charge_rate_km'):
            if k in vals:
                w[k] = _f(vals[k])
        for k in ('year', 'seats'):
            if k in vals:
                w[k] = int(_f(vals[k]))
        if 'acquired' in vals:
            w['acquired'] = vals['acquired'] or False
        if 'fuel_sensor' in vals:
            w['fuel_sensor'] = bool(vals['fuel_sensor'])
        if 'driver_id' in vals:
            w['driver_id'] = int(vals['driver_id']) if vals['driver_id'] else False
        if 'user_ids' in vals:
            w['user_ids'] = [(6, 0, [int(x) for x in vals['user_ids'] or []])]
        if 'active' in vals:
            w['active'] = bool(vals['active'])
        if not vid and not w.get('plate'):
            raise ValidationError(self.env._('Enter the plate.'))
        if w.get('plate'):
            k = plate_key(w['plate'])
            dup = self._V().search([('plate_key', '=', k), ('company_id', '=', self.env.company.id), ('id', '!=', int(vid or 0))], limit=1)
            if dup:
                raise ValidationError(self.env._('%s is already in the fleet (%s).', w['plate'], dup.name))
        if w.get('target_kml') and not (0.5 <= w['target_kml'] <= 40):
            raise ValidationError(self.env._('Expected km per litre looks wrong (trucks 2–5, pickups 7–12, small cars 12–20).'))
        if vid:
            v = self._vehicle(vid)
            v.write(w)
        else:
            v = self._V().create(dict(w, company_id=self.env.company.id))
        if vals.get('odometer') not in (None, '') and _f(vals['odometer']) != v.odometer:
            new = _f(vals['odometer'])
            if v.odometer and new < v.odometer and not self._is_manager():
                raise ValidationError(self.env._('The odometer cannot go down (now %s km). Ask the fleet manager.', round(v.odometer)))
            v._set_odometer(new, self._today(), 'manual')
            if new < v.odometer:
                v.write({'odometer': new, 'odometer_date': self._today()})
        return self.vehicle(v.id)

    # documents
    @api.model
    def save_doc(self, vid, vals):
        self._require('officer')
        v = self._vehicle(vid)
        if not vals.get('date_end'):
            raise ValidationError(self.env._('Enter when it expires.'))
        if vals.get('date_start') and vals['date_start'] > vals['date_end']:
            raise ValidationError(self.env._('It expires before it starts.'))
        w = {'kind': vals.get('kind') or 'other', 'number': vals.get('number') or False, 'partner_id': int(vals['partner_id']) if vals.get('partner_id') else False,
             'date_start': vals.get('date_start') or False, 'date_end': vals['date_end'], 'amount': _f(vals.get('amount')), 'note': vals.get('note') or False}
        D = self.env['sb.vehicle.doc'].sudo()
        if vals.get('id'):
            D.browse(int(vals['id'])).write(w)
        else:
            D.search([('vehicle_id', '=', v.id), ('kind', '=', w['kind'])]).write({'renewed': True})
            D.create(dict(w, vehicle_id=v.id))
        return self.vehicle(v.id)

    @api.model
    def remove_doc(self, doc_id):
        self._require('officer')
        d = self.env['sb.vehicle.doc'].sudo().browse(int(doc_id))
        vid = d.vehicle_id.id
        self._vehicle(vid)
        d.unlink()
        return self.vehicle(vid)

    @api.model
    def partners(self, search):
        self._require(read=True)
        return [{'id': p.id, 'name': p.name} for p in self.env['res.partner'].sudo().search(
            [('name', 'ilike', search), ('company_id', 'in', (False, self.env.company.id))], limit=10)]

    # ------------------------------------------------------------------ fuel
    def _fuel_row(self, f):
        return {'id': f.id, 'name': f.name, 'vehicle_id': f.vehicle_id.id, 'vehicle': f.vehicle_id.name, 'plate': f.vehicle_id.plate,
                'driver_id': f.driver_id.id or '', 'driver': f.driver_id.name or '', 'state': f.state,
                'requested': self._dt_label(f.requested_at), 'liters_req': f.liters_req, 'level_now': f.level_now, 'reason': f.reason or '',
                'date': self._dt_out(f.date), 'date_label': self._dt_label(f.date), 'liters': f.liters, 'price': f.price, 'amount': f.amount,
                'odometer': f.odometer, 'full': f.full, 'station': f.station or '', 'pay': f.pay or '', 'receipt': f.receipt or '',
                'receipt_checked': f.receipt_checked, 'receipt_amount': f.receipt_amount, 'receipt_plate': f.receipt_plate or '',
                'receipt_payee': f.receipt_payee or '', 'gps_liters': f.gps_liters, 'km': f.km, 'kml': f.kml, 'flags': f.flags or [],
                'risk': f.risk, 'approved_by': f.approved_by.name or '', 'reject_note': f.reject_note or ''}

    def _check_fuel(self, f):
        """The fuel cheat checker. Returns (flags, risk) and sets km / km per litre."""
        flags = []
        v = f.vehicle_id
        c = v.company_id
        tol = (c.sb_fleet_kml_tolerance or 20.0) / 100.0

        def flag(level, msg):
            flags.append({'level': level, 'msg': msg})

        F = self.env['sb.fuel'].sudo()
        if f.state == 'request':
            if v.tank and f.liters_req and f.liters_req + (f.level_now or 0) > v.tank * 1.05:
                flag('alert', self.env._('Asks for %(l)s L but only %(r)s L fits (tank %(t)s L, %(n)s L in it).', l=f.liters_req,
                                         r=round(max(v.tank - (f.level_now or 0), 0), 1), t=v.tank, n=f.level_now or 0))
            last = F.search([('vehicle_id', '=', v.id), ('state', '=', 'filled'), ('id', '!=', f.id)], order='date desc', limit=1)
            if last and last.date and f.requested_at and (f.requested_at - last.date).total_seconds() < 24 * 3600:
                flag('watch', self.env._('Filled only %s hours ago.', round((f.requested_at - last.date).total_seconds() / 3600, 1)))
            return flags, max([x['level'] for x in flags], key=RISK_ORDER.get, default='ok')
        if v.fuel == 'electric':
            flag('alert', self.env._('This is an electric vehicle.'))
        prev = F.search([('vehicle_id', '=', v.id), ('state', '=', 'filled'), ('id', '!=', f.id), ('date', '<=', f.date)], order='date desc, id desc')
        last_odo = prev.filtered(lambda x: x.odometer)[:1]
        if f.odometer and last_odo and f.odometer < last_odo.odometer:
            flag('alert', self.env._('Odometer %(o)s km is lower than at the last fill (%(l)s km).', o=round(f.odometer), l=round(last_odo.odometer)))
        elif f.odometer and last_odo and f.date and last_odo.date:
            days = max((f.date - last_odo.date).total_seconds() / 86400.0, 0.04)
            if (f.odometer - last_odo.odometer) / days > 1200:
                flag('alert', self.env._('%(k)s km in %(d)s days is not possible: check the odometer.', k=round(f.odometer - last_odo.odometer),
                                         d=round(days, 1)))
        if not f.odometer and not f.sb_import_ref:
            flag('watch', self.env._('No odometer reading: km per litre cannot be checked.'))
        if v.tank and f.liters > v.tank * 1.5:
            flag('theft', self.env._('%(l)s L is far more than the tank holds (%(t)s L).', l=f.liters, t=v.tank))
        elif v.tank and f.liters > v.tank * 1.05:
            flag('alert', self.env._('%(l)s L is more than the tank holds (%(t)s L): fuel into jerrycans, or the tank size is wrong.',
                                     l=f.liters, t=v.tank))
        elif v.tank and f.level_now and f.liters + f.level_now > v.tank * 1.1:
            flag('alert', self.env._('%(l)s L on top of %(n)s L already in it overflows the %(t)s L tank.', l=f.liters, n=f.level_now, t=v.tank))
        if prev and prev[0].date and f.date and (f.date - prev[0].date).total_seconds() < 12 * 3600 and f.liters > 5:
            flag('watch', self.env._('Second fill within %s hours.', round((f.date - prev[0].date).total_seconds() / 3600, 1)))
        # km per litre since the last full fill
        km = kml = 0.0
        if f.full and f.odometer:
            base = prev.filtered(lambda x: x.full and x.odometer and x.odometer < f.odometer)[:1]
            if base:
                between = F.search([('vehicle_id', '=', v.id), ('state', '=', 'filled'), ('date', '>', base.date), ('date', '<=', f.date)])
                lit = sum(between.mapped('liters'))
                km = f.odometer - base.odometer
                kml = km / lit if lit else 0
                if v.target_kml and kml:
                    dev = (kml - v.target_kml) / v.target_kml
                    if dev < -2 * tol:
                        flag('alert', self.env._('Only %(k)s km per litre (expected %(t)s): %(x)s L more than %(km)s km needs.', k=round(kml, 2),
                                                 t=v.target_kml, x=round(lit - km / v.target_kml), km=round(km)))
                    elif dev < -tol:
                        flag('watch', self.env._('%(k)s km per litre, below the expected %(t)s.', k=round(kml, 2), t=v.target_kml))
                    elif dev > 2 * tol:
                        flag('watch', self.env._('%(k)s km per litre is better than this vehicle can do (%(t)s): a fill may be missing '
                                                 'or the odometer wrong.', k=round(kml, 2), t=v.target_kml))
        # receipts
        if f.receipt:
            dup = F.search([('receipt', '=ilike', f.receipt.strip()), ('id', '!=', f.id), ('state', '!=', 'rejected')], limit=1)
            if dup:
                flag('theft', self.env._('Receipt %(r)s was already used on %(n)s (%(v)s).', r=f.receipt, n=dup.name, v=dup.vehicle_id.plate))
        elif f.pay == 'telebirr' and not f.sb_import_ref:
            flag('watch', self.env._('telebirr fill without the transaction number.'))
        if f.receipt_checked:
            if f.receipt_amount and abs(f.receipt_amount - f.amount) > max(1.0, f.amount * 0.01):
                flag('alert', self.env._('The receipt says %(r)s, the fill says %(a)s.', r='{:,.2f}'.format(f.receipt_amount),
                                         a='{:,.2f}'.format(f.amount)))
            if f.receipt_plate and plate_digits(f.receipt_plate) and plate_digits(f.receipt_plate) != plate_digits(v.plate):
                flag('theft', self.env._('The receipt is for plate %(p)s, not %(v)s.', p=f.receipt_plate, v=v.plate))
        reg = self._price(c, v.fuel)
        if reg and f.price and not f.sb_import_ref and abs(f.price - reg) / reg > 0.03:
            flag('watch', self.env._('%(p)s per litre; the set price is %(r)s.', p=f.price, r=reg))
        if f.liters_req and f.liters > f.liters_req * 1.1 + 2:
            flag('watch', self.env._('Filled %(l)s L, %(q)s L was approved.', l=f.liters, q=f.liters_req))
        # GPS fuel sensor
        if v.fuel_sensor and f.gps_liters:
            diff = abs(f.gps_liters - f.liters) / f.liters * 100 if f.liters else 0
            if diff >= 30:
                flag('theft', self.env._('GPS saw %(g)s L go in, the receipt says %(l)s L.', g=round(f.gps_liters, 1), l=f.liters))
            elif diff >= 15:
                flag('alert', self.env._('GPS saw %(g)s L go in, the receipt says %(l)s L.', g=round(f.gps_liters, 1), l=f.liters))
            elif diff >= 5:
                flag('watch', self.env._('GPS %(g)s L vs %(l)s L.', g=round(f.gps_liters, 1), l=f.liters))
        f.write({'km': round(km, 1), 'kml': round(kml, 2)})
        return flags, max([x['level'] for x in flags], key=RISK_ORDER.get, default='ok')

    def _recheck(self, f):
        flags, risk = self._check_fuel(f)
        f.write({'flags': flags, 'risk': risk})

    @api.model
    def fuel_list(self, f=None):
        self._require(read=True)
        f = f or {}
        vs = self._vehicles(all_=True)
        dom = [('vehicle_id', 'in', vs.ids)]
        if f.get('vehicle_id'):
            dom.append(('vehicle_id', '=', int(f['vehicle_id'])))
        if f.get('state'):
            dom.append(('state', '=', f['state']))
        if f.get('risk') == 'flagged':
            dom.append(('risk', 'in', ('alert', 'theft')))
        if f.get('date_from'):
            dom.append(('date', '>=', f['date_from']))
        if f.get('date_to'):
            dom.append(('date', '<=', f['date_to'] + ' 23:59:59'))
        rows = self.env['sb.fuel'].sudo().search(dom, order='state desc, date desc, requested_at desc', limit=int(f.get('limit') or 300))
        fills = rows.filtered(lambda x: x.state == 'filled')
        return {'rows': [self._fuel_row(x) for x in rows],
                'totals': {'liters': round(sum(fills.mapped('liters')), 1), 'amount': round(sum(fills.mapped('amount')), 2),
                           'flagged': len(fills.filtered(lambda x: x.risk in ('alert', 'theft')))}}

    @api.model
    def save_fuel(self, vals):
        """A request (state request) or a fill (state filled); a fill may complete an approved request."""
        self._require()
        F = self.env['sb.fuel'].sudo()
        rec = F.browse(int(vals['id'])) if vals.get('id') else F
        v = self._vehicle(vals.get('vehicle_id') or rec.vehicle_id.id)
        me = self._me()
        if rec and rec.state == 'filled' and not self._is_officer():
            raise AccessError(self.env._('A filled record is changed by the fleet office.'))
        kind = vals.get('mode') or ('fill' if rec and rec.state == 'approved' else 'request')
        if kind == 'request':
            lit = _f(vals.get('liters_req'))
            if lit <= 0:
                raise ValidationError(self.env._('How many litres?'))
            w = {'vehicle_id': v.id, 'driver_id': int(vals['driver_id']) if vals.get('driver_id') else (me.id or v.driver_id.id or False),
                 'liters_req': lit, 'level_now': _f(vals.get('level_now')), 'reason': vals.get('reason') or False, 'state': 'request',
                 'requested_at': fields.Datetime.now()}
            if vals.get('odometer'):
                w['odometer'] = _f(vals['odometer'])
            rec = rec.write(w) and rec if rec else F.create(w)
        else:
            if rec and rec.state == 'request' and not self._is_manager():
                raise UserError(self.env._('The request is not approved yet.'))
            lit, price = _f(vals.get('liters')), _f(vals.get('price'))
            amount = _f(vals.get('amount')) or round(lit * price, 2)
            if lit <= 0:
                raise ValidationError(self.env._('Enter the litres filled.'))
            if not price and amount:
                price = round(amount / lit, 2)
            if not vals.get('date'):
                raise ValidationError(self.env._('When was it filled?'))
            w = {'vehicle_id': v.id, 'driver_id': int(vals['driver_id']) if vals.get('driver_id') else (rec.driver_id.id or me.id or v.driver_id.id or False),
                 'date': self._dt_in(vals['date']), 'liters': lit, 'price': price, 'amount': amount, 'odometer': _f(vals.get('odometer')),
                 'full': bool(vals.get('full', True)), 'station': vals.get('station') or False, 'pay': vals.get('pay') or 'cash',
                 'receipt': (vals.get('receipt') or '').strip().upper() or False, 'gps_liters': _f(vals.get('gps_liters')),
                 'level_now': _f(vals.get('level_now')) or rec.level_now, 'state': 'filled'}
            if rec and rec.receipt != w['receipt']:
                w.update(receipt_checked=False, receipt_amount=0, receipt_plate=False, receipt_payee=False, receipt_date=False)
            if w['date'] > fields.Datetime.now() + timedelta(hours=1):
                raise ValidationError(self.env._('The fill time is in the future.'))
            rec = rec.write(w) and rec if rec else F.create(w)
            if rec.odometer:
                v._set_odometer(rec.odometer, rec.date.date(), 'fuel', rec.name)
        self._recheck(rec)
        # a fill changes the km per litre of the next full fill too
        nxt = F.search([('vehicle_id', '=', v.id), ('state', '=', 'filled'), ('date', '>', rec.date or fields.Datetime.now())], order='date', limit=1)
        if nxt:
            self._recheck(nxt)
        return self._fuel_row(rec)

    @api.model
    def decide_fuel(self, ids, approve, note=''):
        self._require('manager')
        me = self._me()
        recs = self.env['sb.fuel'].sudo().browse([int(i) for i in ids]).filtered(lambda r: r.state == 'request')
        if approve:
            recs.write({'state': 'approved', 'approved_by': me.id or False})
        else:
            if not note:
                raise ValidationError(self.env._('Say why it is refused.'))
            recs.write({'state': 'rejected', 'approved_by': me.id or False, 'reject_note': note})
        return self.fuel_list({'state': 'request'})

    @api.model
    def remove_fuel(self, fid):
        self._require('officer')
        r = self.env['sb.fuel'].sudo().browse(int(fid))
        self._vehicle(r.vehicle_id.id)
        if r.voucher_id and r.voucher_id.state == 'posted':
            raise UserError(self.env._('Paid in Accounting: void the payment first.'))
        r.unlink()
        return True

    @api.model
    def verify_receipt(self, fid):
        self._require()
        r = self.env['sb.fuel'].sudo().browse(int(fid))
        self._vehicle(r.vehicle_id.id)
        if not r.receipt:
            raise ValidationError(self.env._('Enter the telebirr transaction number first.'))
        info = self.env['sb.fleet.receipt'].fetch(r.receipt)
        r.write({'receipt_checked': True, 'receipt_amount': info['amount'], 'receipt_plate': info['plate'] or False,
                 'receipt_payee': info['payee'] or False, 'receipt_date': info['date'] or False,
                 'station': r.station or info['payee'] or False})
        self._recheck(r)
        return self._fuel_row(r)

    # ------------------------------------------------------------------ trips
    def _trip_row(self, t):
        return {'id': t.id, 'name': t.name, 'vehicle_id': t.vehicle_id.id, 'vehicle': t.vehicle_id.name, 'plate': t.vehicle_id.plate,
                'driver_id': t.driver_id.id or '', 'driver': t.driver_id.name or '', 'purpose': t.purpose, 'origin': t.origin or '',
                'destination': t.destination or '', 'cargo': t.cargo or '', 'state': t.state, 'out_at': self._dt_out(t.out_at),
                'back_at': self._dt_out(t.back_at), 'out_label': self._dt_label(t.out_at), 'back_label': self._dt_label(t.back_at),
                'odo_out': t.odo_out, 'odo_in': t.odo_in, 'km': t.km, 'gate_by': t.gate_by or '', 'days': t.days,
                'perdiem_rate': t.perdiem_rate, 'perdiem': t.perdiem, 'perdiem_state': t.perdiem_state, 'note': t.note or '',
                'voucher': t.perdiem_voucher_id and {'id': t.perdiem_voucher_id.id, 'name': t.perdiem_voucher_id.name,
                                                     'state': t.perdiem_voucher_id.state} or False,
                'hours': round((t.back_at - t.out_at).total_seconds() / 3600, 1) if t.back_at and t.out_at else None,
                'job_id': t.job_id.id if 'job_id' in t._fields and t.job_id else '', 'job': t.job_id.display_name if 'job_id' in t._fields and t.job_id else ''}

    @api.model
    def trips(self, f=None):
        self._require(read=True)
        f = f or {}
        vs = self._vehicles(all_=True)
        dom = [('vehicle_id', 'in', vs.ids)]
        if f.get('vehicle_id'):
            dom.append(('vehicle_id', '=', int(f['vehicle_id'])))
        if f.get('state'):
            dom.append(('state', '=', f['state']))
        if f.get('perdiem'):
            dom.append(('perdiem_state', '=', f['perdiem']))
        rows = self.env['sb.trip'].sudo().search(dom, limit=int(f.get('limit') or 300))
        so = {'out': 0, 'planned': 1, 'back': 2, 'cancel': 3}
        rows = rows.sorted(lambda t: (so[t.state], -(t.out_at.timestamp() if t.out_at else 0)))
        return {'rows': [self._trip_row(t) for t in rows]}

    @api.model
    def save_trip(self, vals):
        """Plan, leave (gate out), come back (gate in), or correct a trip."""
        self._require()
        T = self.env['sb.trip'].sudo()
        t = T.browse(int(vals['id'])) if vals.get('id') else T
        v = self._vehicle(vals.get('vehicle_id') or t.vehicle_id.id)
        me = self._me()
        action = vals.get('action') or 'save'
        w = {}
        for k in ('purpose', 'origin', 'destination', 'cargo', 'note', 'gate_by'):
            if k in vals:
                w[k] = vals[k] or False
        if 'driver_id' in vals:
            w['driver_id'] = int(vals['driver_id']) if vals['driver_id'] else False
        if 'job_id' in vals and 'job_id' in T._fields:
            w['job_id'] = int(vals['job_id']) if vals['job_id'] else False
        if not t and not w.get('purpose'):
            raise ValidationError(self.env._('What is the trip for?'))
        if action == 'out':
            odo = _f(vals.get('odo_out')) or v.odometer
            if v.odometer and odo < v.odometer - 1 and not self._is_manager():
                raise ValidationError(self.env._('Odometer out %(o)s is below the last known %(l)s km.', o=round(odo), l=round(v.odometer)))
            busy = T.search([('vehicle_id', '=', v.id), ('state', '=', 'out'), ('id', '!=', t.id or 0)], limit=1)
            if busy:
                raise ValidationError(self.env._('%(v)s is already out on %(t)s.', v=v.plate, t=busy.name))
            w.update(state='out', odo_out=odo, out_at=self._dt_in(vals.get('out_at')) or fields.Datetime.now())
        elif action == 'back':
            odo = _f(vals.get('odo_in'))
            start = t.odo_out if t else _f(vals.get('odo_out'))
            if not odo:
                raise ValidationError(self.env._('Enter the odometer on return.'))
            if odo < start:
                raise ValidationError(self.env._('Odometer in (%(i)s) is below odometer out (%(o)s).', i=round(odo), o=round(start)))
            back = self._dt_in(vals.get('back_at')) or fields.Datetime.now()
            out = t.out_at if t else self._dt_in(vals.get('out_at'))
            if out and back < out:
                raise ValidationError(self.env._('It came back before it left.'))
            if out and (odo - start) / max((back - out).total_seconds() / 3600.0, 0.25) > 110:
                raise ValidationError(self.env._('%(k)s km in %(h)s hours is faster than 110 km/h: check the odometer and times.', k=round(odo - start),
                                                 h=round((back - out).total_seconds() / 3600.0, 1)))
            w.update(state='back', odo_in=odo, back_at=back)
        elif action == 'cancel':
            w['state'] = 'cancel'
        else:
            for k in ('odo_out', 'odo_in'):
                if k in vals and self._is_officer():
                    w[k] = _f(vals[k])
            for k in ('out_at', 'back_at'):
                if k in vals and self._is_officer():
                    w[k] = self._dt_in(vals[k])
        if t:
            t.write(w)
        else:
            w.setdefault('driver_id', me.id or v.driver_id.id or False)
            t = T.create(dict(w, vehicle_id=v.id))
        if action == 'back':
            v._set_odometer(t.odo_in, t.back_at.date(), 'trip', t.name)
        elif action == 'out' and t.odo_out:
            v._set_odometer(t.odo_out, t.out_at.date(), 'trip', t.name)
        return self._trip_row(t)

    @api.model
    def request_perdiem(self, tid, days, note=''):
        self._require()
        t = self.env['sb.trip'].sudo().browse(int(tid))
        self._vehicle(t.vehicle_id.id)
        days = _f(days)
        if t.perdiem_state in ('approved', 'paid'):
            raise UserError(self.env._('Per diem is already approved.'))
        if days <= 0:
            raise ValidationError(self.env._('How many days?'))
        if t.out_at and t.back_at:
            span = (t.back_at - t.out_at).total_seconds() / 86400.0
            if days > math.ceil(span) + 0.01:
                raise ValidationError(self.env._('The trip lasted %s days.', round(span, 1)))
        rate = t.company_id.sb_fleet_perdiem_rate
        t.write({'days': days, 'perdiem_rate': rate, 'perdiem': round(days * rate, 2), 'perdiem_state': 'requested',
                 'note': note or t.note})
        return self._trip_row(t)

    @api.model
    def decide_perdiem(self, tid, approve, amount=None):
        """Approve: a draft payment voucher to the driver goes to Accounting."""
        self._require('manager')
        t = self.env['sb.trip'].sudo().browse(int(tid))
        c = t.company_id
        if t.perdiem_state != 'requested':
            raise UserError(self.env._('Nothing to decide.'))
        if not approve:
            t.write({'perdiem_state': 'none'})
            return self._trip_row(t)
        if amount not in (None, ''):
            t.perdiem = _f(amount)
        if not c.sb_fleet_perdiem_account_id:
            raise UserError(self.env._('Set the per diem expense account in Fleet settings.'))
        partner = t.driver_id.work_contact_id
        v = self.env['sb.voucher'].sudo().create({
            'voucher_type': 'payment', 'company_id': c.id, 'date': self._today(), 'payer_type': 'employee', 'partner_id': partner.id or False,
            'reference': t.name, 'memo': self.env._('Per diem %(d)s, %(n)s days, %(p)s', d=t.driver_id.name or '', n=t.days, p=t.destination or t.purpose),
            'line_ids': [(0, 0, {'account_id': c.sb_fleet_perdiem_account_id.id, 'amount': t.perdiem, 'vehicle_id': t.vehicle_id.id,
                                 'name': self.env._('Per diem %(t)s %(d)s', t=t.name, d=t.destination or '')})]})
        t.write({'perdiem_state': 'approved', 'perdiem_voucher_id': v.id})
        return self._trip_row(t)

    # ------------------------------------------------------------------ maintenance
    def _service_row(self, s):
        return {'id': s.id, 'name': s.name, 'vehicle_id': s.vehicle_id.id, 'vehicle': s.vehicle_id.name, 'kind': s.kind,
                'kind_name': dict(SERVICE_KINDS)[s.kind], 'state': s.state, 'date': _s(s.date), 'date_ec': _ec(s.date), 'date_done': _s(s.date_done),
                'odometer': s.odometer, 'partner_id': s.partner_id.id or '', 'partner': s.partner_id.name or '', 'description': s.description or '',
                'amount': s.amount, 'schedule_id': s.schedule_id.id or ''}

    @api.model
    def maintenance(self, f=None):
        self._require('officer', read=True)
        f = f or {}
        vs = self._vehicles()
        sched = [self._sched_due(s) for s in self.env['sb.vehicle.schedule'].sudo().search([('vehicle_id', 'in', vs.ids)])]
        order = {'overdue': 0, 'soon': 1, 'unknown': 2, 'ok': 3}
        dom = [('vehicle_id', 'in', self._vehicles(all_=True).ids)]
        if f.get('state'):
            dom.append(('state', '=', f['state']))
        orders = self.env['sb.vehicle.service'].sudo().search(dom, limit=300)
        return {'schedules': sorted(sched, key=lambda s: (order[s['status']], s['vehicle'])), 'orders': [self._service_row(s) for s in orders]}

    @api.model
    def save_service(self, vals):
        self._require('officer')
        S = self.env['sb.vehicle.service'].sudo()
        s = S.browse(int(vals['id'])) if vals.get('id') else S
        v = self._vehicle(vals.get('vehicle_id') or s.vehicle_id.id)
        w = {'kind': vals.get('kind') or 'service', 'state': vals.get('state') or 'open', 'date': vals.get('date') or self._today(),
             'odometer': _f(vals.get('odometer')), 'partner_id': int(vals['partner_id']) if vals.get('partner_id') else False,
             'description': vals.get('description') or False, 'amount': _f(vals.get('amount')),
             'schedule_id': int(vals['schedule_id']) if vals.get('schedule_id') else False}
        if w['state'] == 'done':
            w['date_done'] = vals.get('date_done') or s.date_done or self._today()
        if s:
            s.write(w)
        else:
            s = S.create(dict(w, vehicle_id=v.id))
        if s.odometer:
            v._set_odometer(s.odometer, s.date, 'service', s.name)
        if s.state == 'done' and s.schedule_id:
            s.schedule_id.write({'last_km': s.odometer or s.schedule_id.last_km, 'last_date': s.date_done})
        if s.state == 'open' and v.state == 'active':
            v.state = 'workshop'
        elif s.state == 'done' and v.state == 'workshop' and not S.search_count([('vehicle_id', '=', v.id), ('state', '=', 'open')]):
            v.state = 'active'
        return self._service_row(s)

    @api.model
    def save_schedule(self, vid, vals):
        self._require('officer')
        v = self._vehicle(vid)
        if not vals.get('name'):
            raise ValidationError(self.env._('Name the job (e.g. oil and filter change).'))
        if not _f(vals.get('every_km')) and not int(_f(vals.get('every_months'))):
            raise ValidationError(self.env._('Give every how many km or every how many months.'))
        w = {'name': vals['name'], 'every_km': _f(vals.get('every_km')), 'every_months': int(_f(vals.get('every_months'))),
             'last_km': _f(vals.get('last_km')), 'last_date': vals.get('last_date') or False}
        Sc = self.env['sb.vehicle.schedule'].sudo()
        if vals.get('id'):
            Sc.browse(int(vals['id'])).write(w)
        else:
            Sc.create(dict(w, vehicle_id=v.id))
        return self.vehicle(v.id)

    @api.model
    def remove_schedule(self, sid):
        self._require('officer')
        s = self.env['sb.vehicle.schedule'].sudo().browse(int(sid))
        vid = s.vehicle_id.id
        self._vehicle(vid)
        s.unlink()
        return self.vehicle(vid)

    # ------------------------------------------------------------------ costs
    def _costs(self, vehicles, d0, d1):
        """Cost per vehicle in a period: fuel, maintenance, documents, per diem, other costs from the books; km and cost per km."""
        d0dt, d1dt = datetime.combine(d0, time.min), datetime.combine(d1, time.max)
        rows = []
        linked = set()
        data = {}
        for v in vehicles:
            data[v.id] = defaultdict(float)
        for f in self.env['sb.fuel'].sudo().search([('vehicle_id', 'in', vehicles.ids), ('state', '=', 'filled'), ('date', '>=', d0dt), ('date', '<=', d1dt)]):
            data[f.vehicle_id.id]['fuel'] += f.amount
            data[f.vehicle_id.id]['liters'] += f.liters
            linked.add(f.voucher_id.id)
        for s in self.env['sb.vehicle.service'].sudo().search([('vehicle_id', 'in', vehicles.ids), ('state', '!=', 'planned'), ('date', '>=', d0), ('date', '<=', d1)]):
            data[s.vehicle_id.id]['maintenance'] += s.amount
            linked.add(s.voucher_id.id)
        for d in self.env['sb.vehicle.doc'].sudo().search([('vehicle_id', 'in', vehicles.ids), ('date_start', '>=', d0), ('date_start', '<=', d1)]):
            data[d.vehicle_id.id]['documents'] += d.amount
            linked.add(d.voucher_id.id)
        for t in self.env['sb.trip'].sudo().search([('vehicle_id', 'in', vehicles.ids), ('perdiem_state', 'in', ('approved', 'paid')),
                                                    ('out_at', '>=', d0dt), ('out_at', '<=', d1dt)]):
            data[t.vehicle_id.id]['perdiem'] += t.perdiem
            linked.add(t.perdiem_voucher_id.id)
        for l in self.env['sb.voucher.line'].sudo().search([('vehicle_id', 'in', vehicles.ids), ('voucher_id.state', '=', 'posted'),
                                                            ('voucher_id.date', '>=', d0), ('voucher_id.date', '<=', d1)]):
            if l.voucher_id.id in linked or l.account_id.account_type not in EXPENSE_TYPES:
                continue
            amt = l.price_subtotal if 'price_subtotal' in l._fields and l.price_subtotal else (getattr(l, 'amount', 0.0) or 0.0)
            if l.voucher_id.voucher_type in ('purchase_return',):
                amt = -amt
            data[l.vehicle_id.id]['other'] += amt
        for l in self.env['sb.stock.voucher.line'].sudo().search([('vehicle_id', 'in', vehicles.ids), ('voucher_id.state', '=', 'posted'),
                                                                  ('voucher_id.voucher_type', '=', 'giv'), ('voucher_id.date', '>=', d0),
                                                                  ('voucher_id.date', '<=', d1)]):
            data[l.vehicle_id.id]['maintenance'] += abs(getattr(l, 'value', 0.0) or 0.0)
        R = self.env['sb.vehicle.reading'].sudo()
        for v in vehicles:
            reads = R.search([('vehicle_id', '=', v.id), ('date', '>=', d0), ('date', '<=', d1)]).mapped('value')
            before = R.search([('vehicle_id', '=', v.id), ('date', '<', d0)], order='value desc', limit=1).value
            km = max(reads) - (before or min(reads)) if reads else 0
            d = data[v.id]
            total = d['fuel'] + d['maintenance'] + d['documents'] + d['perdiem'] + d['other']
            rows.append({'id': v.id, 'name': v.name, 'plate': v.plate, 'fuel': round(d['fuel'], 2), 'liters': round(d['liters'], 1),
                         'maintenance': round(d['maintenance'], 2), 'documents': round(d['documents'], 2), 'perdiem': round(d['perdiem'], 2),
                         'other': round(d['other'], 2), 'total': round(total, 2), 'km': round(max(km, 0)),
                         'per_km': round(total / km, 2) if km > 0 else None})
        tot = {k: round(sum(r[k] for r in rows), 2) for k in ('fuel', 'maintenance', 'documents', 'perdiem', 'other', 'total', 'km', 'liters')}
        tot['per_km'] = round(tot['total'] / tot['km'], 2) if tot['km'] else None
        return {'rows': rows, 'totals': tot}

    def _monthly(self, v, n):
        today = self._today()
        out = []
        for i in range(n - 1, -1, -1):
            m0 = (today.replace(day=1) - relativedelta(months=i))
            m1 = m0 + relativedelta(months=1) - timedelta(days=1)
            r = self._costs(v, m0, m1)['rows'][0]
            out.append({'label': _ec(m0).split(' ')[0], 'fuel': r['fuel'], 'maintenance': r['maintenance'],
                        'other': round(r['documents'] + r['perdiem'] + r['other'], 2), 'km': r['km']})
        return out

    @api.model
    def costs(self, d0, d1):
        self._require('manager', read=True)
        d0, d1 = fields.Date.to_date(d0), fields.Date.to_date(d1)
        if d1 < d0:
            raise ValidationError(self.env._('The period ends before it starts.'))
        vs = self._vehicles(all_=True)
        res = self._costs(vs, d0, d1)
        res['rows'] = sorted([r for r in res['rows'] if r['total'] or r['km']], key=lambda r: -r['total'])
        res['label'] = '%s – %s' % (_ec(d0), _ec(d1))
        return res

    # ------------------------------------------------------------------ driver's page
    @api.model
    def my(self):
        self._require(read=True)
        me = self._me()
        if not me:
            return {'no_person': True}
        vs = self._V().search([('active', '=', True), '|', ('driver_id', '=', me.id), ('user_ids', 'in', me.ids)])
        F = self.env['sb.fuel'].sudo()
        T = self.env['sb.trip'].sudo()
        return {'me': me.name, 'licence_ec': _ec(me.sb_licence_expiry), 'licence_days': (me.sb_licence_expiry - self._today()).days if me.sb_licence_expiry else None,
                'vehicles': [self._vrow(v) for v in vs],
                'fuel': [self._fuel_row(f) for f in F.search([('vehicle_id', 'in', vs.ids), '|', ('driver_id', '=', me.id), ('state', 'in', ('request', 'approved'))], limit=20)],
                'trips': [self._trip_row(t) for t in T.search([('vehicle_id', 'in', vs.ids), ('driver_id', '=', me.id)], limit=20)]}

    # ------------------------------------------------------------------ settings
    @api.model
    def settings(self):
        self._require('manager')
        c = self.env.company
        accs = self.env['account.account'].sudo().search([('company_ids', 'in', c.id), ('account_type', 'in', EXPENSE_TYPES)], order='code')
        return {'perdiem_rate': c.sb_fleet_perdiem_rate, 'perdiem_account': c.sb_fleet_perdiem_account_id.id or '',
                'fuel_account': c.sb_fleet_fuel_account_id.id or '', 'diesel': c.sb_fleet_diesel_price, 'benzine': c.sb_fleet_benzine_price,
                'tolerance': c.sb_fleet_kml_tolerance, 'warn_days': c.sb_fleet_doc_warn_days,
                'accounts': [{'id': a.id, 'name': '%s %s' % (a.code, a.name)} for a in accs],
                'drivers': [{'id': e.id, 'name': e.name, 'licence': e.sb_licence_no or '', 'grade': e.sb_licence_grade or '',
                             'expiry': _s(e.sb_licence_expiry)} for e in (self._vehicles().mapped('driver_id') | self._vehicles().mapped('user_ids')).sorted('name')]}

    @api.model
    def save_settings(self, vals):
        self._require('manager')
        if _f(vals.get('diesel')) and not 50 <= _f(vals['diesel']) <= 500:
            raise ValidationError(self.env._('The diesel price looks wrong.'))
        self.env.company.sudo().write({
            'sb_fleet_perdiem_rate': _f(vals.get('perdiem_rate')), 'sb_fleet_diesel_price': _f(vals.get('diesel')),
            'sb_fleet_benzine_price': _f(vals.get('benzine')), 'sb_fleet_kml_tolerance': _f(vals.get('tolerance')) or 20,
            'sb_fleet_doc_warn_days': int(_f(vals.get('warn_days'))) or 30,
            'sb_fleet_perdiem_account_id': int(vals['perdiem_account']) if vals.get('perdiem_account') else False,
            'sb_fleet_fuel_account_id': int(vals['fuel_account']) if vals.get('fuel_account') else False})
        return self.settings()

    @api.model
    def save_driver(self, vals):
        self._require('officer')
        e = self.env['hr.employee'].sudo().browse(int(vals['id']))
        e.write({'sb_licence_no': vals.get('licence') or False, 'sb_licence_grade': vals.get('grade') or False,
                 'sb_licence_expiry': vals.get('expiry') or False})
        return self.settings() if self._is_manager() else True
