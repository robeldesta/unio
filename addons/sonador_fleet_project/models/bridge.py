"""A trip for a job charges the job: km × the vehicle's charge-out rate (or, without one, its own cost per km over the
last twelve months) as equipment, and the trip's per diem as travel."""
from datetime import timedelta

from odoo import api, fields, models

from odoo.addons.sonador_project.models.service import _ec, _s


class SbTrip(models.Model):
    _inherit = 'sb.trip'

    job_id = fields.Many2one('sb.job', 'Job', index=True, ondelete='set null')


class SbProjects(models.AbstractModel):
    _inherit = 'sb.projects'

    def _vehicle_rate(self, v, cache):
        if v.id not in cache:
            rate = v.charge_rate_km
            if not rate:
                today = fields.Date.context_today(self)
                row = self.env['sb.fleet'].sudo()._costs(v, today - timedelta(days=365), today)['rows'][0]
                rate = row['per_km'] or 0.0
            cache[v.id] = rate
        return cache[v.id]

    def _actuals(self, jobs):
        out = super()._actuals(jobs)
        Code = self.env['sb.cost.code'].sudo()
        eqp = Code.search([('kind', '=', 'equipment')], limit=1)
        trv = Code.search([('code', '=', 'TRV')], limit=1) or Code.search([('kind', '=', 'other')], limit=1)
        cache = {}
        for t in self.env['sb.trip'].sudo().search([('job_id', 'in', jobs.ids), ('state', '=', 'back')]):
            o = out[t.job_id.id]
            d = t.back_at.date() if t.back_at else False
            rate = self._vehicle_rate(t.vehicle_id, cache)
            if t.km and rate:
                amt = round(t.km * rate, 2)
                o['cost'][eqp.id] += amt
                o['lines'].append({'date': _s(d), 'ec': _ec(d), 'ref': t.name, 'type': 'Vehicle trip', 'who': t.driver_id.name or '',
                                   'what': '%s · %s km × %s' % (t.vehicle_id.plate, round(t.km), round(rate, 2)), 'cost': amt, 'revenue': 0.0,
                                   'code': eqp.display_name or '', 'phase': '', 'model': False, 'res_id': False})
            if t.perdiem and t.perdiem_state in ('approved', 'paid'):
                o['cost'][trv.id] += t.perdiem
                o['lines'].append({'date': _s(d), 'ec': _ec(d), 'ref': t.name, 'type': 'Per diem', 'who': t.driver_id.name or '',
                                   'what': '%s days' % t.days, 'cost': round(t.perdiem, 2), 'revenue': 0.0,
                                   'code': trv.display_name or '', 'phase': '', 'model': False, 'res_id': False})
        return out
